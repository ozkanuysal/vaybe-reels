"""Vay Be! - GitHub Actions'ta çalışır. Release "kuyruk" ekindeki ilk videoyu Instagram Reels olarak paylaşır.

Kuyruk: GitHub Release (tag: kuyruk) ekleri, ada göre sıralı çiftler:
    001-vombat.mp4 + 001-vombat.txt (txt = açıklama, UTF-8)
Paylaşılan çift release'ten silinir.

Ortam değişkenleri:
    IG_TOKEN     Instagram/Meta erişim anahtarı (GitHub Secret)
    GH_TOKEN     GitHub tokenı (release eklerini okuyup silmek için; workflow verir)
    GH_REPO      sahip/depo (workflow verir)
    YAYIN_SAATI  "SS:DD" (Türkiye saati) -> o saate kadar bekleyip yayınlar. Boş = hazır olunca hemen.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IG_USER = "17841443855232852"
API = "https://graph.facebook.com/v21.0"
TAG = "kuyruk"
TOKEN = os.environ["IG_TOKEN"].strip()
YAYIN_SAATI = os.environ.get("YAYIN_SAATI", "").strip()
TR = datetime.timezone(datetime.timedelta(hours=3))  # Türkiye: sabit UTC+3
IS = pathlib.Path("is")


def log(m):
    print(datetime.datetime.now(TR).strftime("%H:%M:%S"), m, flush=True)


def istek(method, url, params=None, data=None, headers=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    h = {"Authorization": f"Bearer {TOKEN}"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def json_istek(method, path, params=None):
    kod, govde = istek(method, f"{API}/{path}", params=params, data=b"" if method == "POST" else None)
    if kod != 200:
        raise RuntimeError(f"{method} {path} -> {kod}: {govde}")
    return json.loads(govde)


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main():
    hesap = json_istek("GET", IG_USER, {"fields": "username,media_count"})
    log(f"Hesap: @{hesap['username']} (gönderi: {hesap['media_count']})")

    ekler = [a["name"] for a in json.loads(gh("release", "view", TAG, "--json", "assets"))["assets"]]
    videolar = sorted(n for n in ekler if n.lower().endswith(".mp4"))
    if not videolar:
        log("Kuyrukta video yok, çıkılıyor.")
        return
    video = videolar[0]
    txt = video[:-4] + ".txt"
    log(f"Sıradaki: {video} (kuyrukta {len(videolar)} video)")

    IS.mkdir(exist_ok=True)
    gh("release", "download", TAG, "-p", video, "-D", str(IS), "--clobber")
    aciklama = ""
    if txt in ekler:
        gh("release", "download", TAG, "-p", txt, "-D", str(IS), "--clobber")
        aciklama = (IS / txt).read_text(encoding="utf-8-sig").strip()
    else:
        log(f"UYARI: {txt} yok, açıklamasız paylaşılıyor.")

    # Instagram formatına çevir (1080x1920, H.264, AAC 48k)
    cikti = IS / "instagram.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(IS / video),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "18", "-maxrate", "20M", "-bufsize", "40M",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(cikti),
    ], check=True)
    boyut = cikti.stat().st_size
    log(f"Çevrildi: {boyut / 1e6:.1f} MB")

    # Yükleme bazen "ProcessingFailedError" verip konteyneri sonsuza kadar IN_PROGRESS bırakıyor,
    # bazen aynı yanıta rağmen 1-2 dk'da FINISHED oluyor (2026-11-19/20 gözlemi). Bu yüzden:
    # her denemede 6 dk bekle, olmazsa yeni konteyner aç ve baştan yükle; en fazla 4 deneme.
    veri = cikti.read_bytes()
    kid = None
    for deneme in range(1, 5):
        kont = json_istek("POST", f"{IG_USER}/media", {
            "media_type": "REELS", "upload_type": "resumable", "caption": aciklama, "share_to_feed": "true",
        })
        log(f"Deneme {deneme}: konteyner {kont['id']}")
        kod, govde = istek("POST", kont["uri"], data=veri, headers={
            "Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(boyut),
            "Content-Type": "application/octet-stream",
        })
        log(f"Deneme {deneme}: yükleme yanıtı ({kod}): {govde}")
        for _ in range(36):  # 6 dk
            time.sleep(10)
            d = json_istek("GET", kont["id"], {"fields": "status_code,status"})
            if d.get("status_code") in ("FINISHED", "ERROR", "EXPIRED"):
                break
        log(f"Deneme {deneme}: durum {d.get('status_code')} - {d.get('status')}")
        if d.get("status_code") == "FINISHED":
            kid = kont["id"]
            break
    if not kid:
        raise RuntimeError("4 denemede de video işlenmedi.")
    log("Video işlendi, yayına hazır.")

    if YAYIN_SAATI:
        ss, dd = map(int, YAYIN_SAATI.split(":"))
        simdi = datetime.datetime.now(TR)
        hedef = simdi.replace(hour=ss, minute=dd, second=0, microsecond=0)
        if hedef > simdi:
            log(f"{YAYIN_SAATI} bekleniyor ({int((hedef - simdi).total_seconds())} sn)")
            time.sleep((hedef - simdi).total_seconds())

    pub = json_istek("POST", f"{IG_USER}/media_publish", {"creation_id": kid})
    log(f"YAYINLANDI: medya id {pub['id']}")

    gh("release", "delete-asset", TAG, video, "-y")
    if txt in ekler:
        gh("release", "delete-asset", TAG, txt, "-y")
    log("Kuyruktan silindi.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"HATA: {e}")
        sys.exit(1)

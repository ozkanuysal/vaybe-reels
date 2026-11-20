"""Vay Be! - GitHub Actions'ta çalışır. Release "kuyruk" ekindeki ilk videoyu Instagram Reels olarak paylaşır.

Kuyruk: GitHub Release (tag: kuyruk) ekleri, ada göre sıralı çiftler:
    001-vombat.mp4 + 001-vombat.txt (txt = açıklama, UTF-8)

Akış:
  1. Sıradaki videoyu indir, Instagram formatına çevir (1080x1920 H.264/AAC).
  2. Çevrilmiş videoyu herkese açık "yayin" release'ine koy.
  3. Yöntem A (ana): Instagram'a video_url ver, videoyu kendisi indirsin.
     Yöntem B (yedek): resumable upload ile baytları kendimiz gönder (yeniden denemeli).
  4. Video hazır olunca (istenirse YAYIN_SAATI'ni bekleyip) yayınla.
  5. Başarılıysa kuyruktan sil. Başarısızsa video kuyrukta kalır, sonraki çalışmada yeniden denenir.

Ortam değişkenleri:
    IG_TOKEN     Meta erişim anahtarı (GitHub Secret)
    GH_TOKEN     GitHub tokenı (workflow verir)
    GH_REPO      sahip/depo (workflow verir) - depo HERKESE AÇIK olmalı (video_url için)
    YAYIN_SAATI  "SS:DD" (Türkiye saati). Boş = hazır olunca hemen.
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
KUYRUK = "kuyruk"
YAYIN = "yayin"
REPO = os.environ["GH_REPO"]
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


def gh(*args, kontrol=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if kontrol and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} -> {r.stderr.strip()}")
    return r


def bekle(kid, dakika):
    """Konteyner FINISHED/ERROR/EXPIRED olana kadar ya da süre dolana kadar bekler; son durumu döndürür."""
    d = {}
    for _ in range(dakika * 6):
        time.sleep(10)
        d = json_istek("GET", kid, {"fields": "status_code,status"})
        if d.get("status_code") in ("FINISHED", "ERROR", "EXPIRED"):
            break
    return d


def konteyner(aciklama, **ek):
    p = {"media_type": "REELS", "caption": aciklama, "share_to_feed": "true"}
    p.update(ek)
    return json_istek("POST", f"{IG_USER}/media", p)


def yontem_a_video_url(aciklama, genel_url):
    """Instagram videoyu adresten kendisi indirir."""
    for deneme in range(1, 3):
        k = konteyner(aciklama, video_url=genel_url)
        log(f"A{deneme}: video_url konteyneri {k['id']}")
        d = bekle(k["id"], 10)
        log(f"A{deneme}: durum {d.get('status_code')} - {d.get('status')}")
        if d.get("status_code") == "FINISHED":
            return k["id"]
    return None


def yontem_b_resumable(aciklama, dosya):
    """Baytları kendimiz göndeririz (bu hesapta güvenilmez çıktı; yedek)."""
    veri = dosya.read_bytes()
    for deneme in range(1, 3):
        k = konteyner(aciklama, upload_type="resumable")
        log(f"B{deneme}: resumable konteyneri {k['id']}")
        kod, govde = istek("POST", k["uri"], data=veri, headers={
            "Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(len(veri)),
            "Content-Type": "application/octet-stream",
        })
        log(f"B{deneme}: yükleme yanıtı ({kod}): {govde}")
        d = bekle(k["id"], 6)
        log(f"B{deneme}: durum {d.get('status_code')} - {d.get('status')}")
        if d.get("status_code") == "FINISHED":
            return k["id"]
    return None


def main():
    hesap = json_istek("GET", IG_USER, {"fields": "username,media_count"})
    log(f"Hesap: @{hesap['username']} (gönderi: {hesap['media_count']})")

    # Zamanlı çalışmada günde tek paylaşım: GitHub zamanlayıcısı gecikebildiği/atlayabildiği için
    # akşam birden fazla tetikleyici var; bugün zaten paylaşım yapıldıysa sonrakiler bir şey yapmaz.
    if os.environ.get("ZAMANLI") == "1":
        son = json_istek("GET", f"{IG_USER}/media", {"fields": "timestamp", "limit": "1"}).get("data", [])
        if son:
            ts = datetime.datetime.strptime(son[0]["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(TR)
            if ts.date() == datetime.datetime.now(TR).date():
                log(f"Bugün zaten paylaşım yapılmış ({ts:%H:%M}), bu tetikleme atlanıyor.")
                return

    ekler =[a["name"] for a in json.loads(gh("release", "view", KUYRUK, "--json", "assets").stdout)["assets"]]
    videolar = sorted(n for n in ekler if n.lower().endswith(".mp4"))
    if not videolar:
        log("Kuyrukta video yok, çıkılıyor.")
        return
    video = videolar[0]
    txt = video[:-4] + ".txt"
    log(f"Sıradaki: {video} (kuyrukta {len(videolar)} video)")

    IS.mkdir(exist_ok=True)
    gh("release", "download", KUYRUK, "-p", video, "-D", str(IS), "--clobber")
    aciklama = ""
    if txt in ekler:
        gh("release", "download", KUYRUK, "-p", txt, "-D", str(IS), "--clobber")
        aciklama = (IS / txt).read_text(encoding="utf-8-sig").strip()
    else:
        log(f"UYARI: {txt} yok, açıklamasız paylaşılıyor.")

    # 1) Süre kontrolü (Reels: 3 sn - 15 dk) ve Instagram formatına çevir
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(IS / video)], capture_output=True, text=True)
    try:
        sure = float(r.stdout.strip())
    except ValueError:
        raise RuntimeError(f"{video} okunamadı (bozuk ya da video değil): {r.stderr.strip()}")
    log(f"Kaynak süre: {sure:.1f} sn")
    if sure > 900:
        raise RuntimeError(f"{video} {sure:.0f} sn; Reels en fazla 15 dk olabilir.")
    giris = ["-i", str(IS / video)]
    sinir = []
    if sure < 3:
        log("UYARI: Reels en az 3 sn olmalı; video döngüyle 4 sn'ye uzatılıyor.")
        giris = ["-stream_loop", "-1", "-i", str(IS / video)]
        sinir = ["-t", "4"]
    ad = f"ig-{int(time.time())}.mp4"
    cikti = IS / ad
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", *giris, *sinir,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "18", "-maxrate", "20M", "-bufsize", "40M",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(cikti),
    ], check=True)
    log(f"Çevrildi: {cikti.stat().st_size / 1e6:.1f} MB")

    # 2) Herkese açık "yayin" release'ine koy
    if gh("release", "view", YAYIN, kontrol=False).returncode != 0:
        gh("release", "create", YAYIN, "--title", "Yayın (geçici)",
           "--notes", "Instagram'ın indirmesi için geçici dosyalar. Otomatik silinir.")
    gh("release", "upload", YAYIN, str(cikti), "--clobber")
    genel_url = f"https://github.com/{REPO}/releases/download/{YAYIN}/{ad}"
    log(f"Geçici adres: {genel_url}")

    try:
        # 3) Yükle: önce video_url, olmazsa resumable
        kid = yontem_a_video_url(aciklama, genel_url)
        if not kid:
            log("Yöntem A olmadı, yedek yöntem B deneniyor.")
            kid = yontem_b_resumable(aciklama, cikti)
        if not kid:
            raise RuntimeError("Video hiçbir yöntemle işlenmedi. Video kuyrukta kaldı, sonraki çalışmada yeniden denenecek.")
        log("Video işlendi, yayına hazır.")

        # 4) Yayın saatini bekle
        if YAYIN_SAATI:
            ss, dd = map(int, YAYIN_SAATI.split(":"))
            simdi = datetime.datetime.now(TR)
            hedef = simdi.replace(hour=ss, minute=dd, second=0, microsecond=0)
            if hedef > simdi:
                log(f"{YAYIN_SAATI} bekleniyor ({int((hedef - simdi).total_seconds())} sn)")
                time.sleep((hedef - simdi).total_seconds())

        # 5) Yayınla (geçici ağ hatasına karşı 3 deneme)
        for deneme in range(1, 4):
            try:
                pub = json_istek("POST", f"{IG_USER}/media_publish", {"creation_id": kid})
                break
            except RuntimeError as e:
                log(f"Yayınlama denemesi {deneme} başarısız: {e}")
                if deneme == 3:
                    raise
                time.sleep(30)
        log(f"YAYINLANDI: medya id {pub['id']}")

        # 6) Kuyruktan sil
        gh("release", "delete-asset", KUYRUK, video, "-y")
        if txt in ekler:
            gh("release", "delete-asset", KUYRUK, txt, "-y")
        log("Kuyruktan silindi.")
    finally:
        gh("release", "delete-asset", YAYIN, ad, "-y", kontrol=False)
        log("Geçici dosya silindi.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"HATA: {e}")
        sys.exit(1)

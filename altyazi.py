"""Vay Be! - altyazı hattı (GitHub Actions). Release "ham" üzerinde çalışır.

  python3 altyazi.py cikar   -> "ham"daki her ham videonun (x.mp4) konuşmasını Whisper ile çıkarır, x.srt yükler
  python3 altyazi.py gom     -> x.mp4 + x.srt olanlara altyazıyı Reels stilinde gömer, x-altyazili.mp4 yükler
  python3 altyazi.py kuyruga -> x-altyazili.mp4 + x.txt (Reels açıklaması) olanları "kuyruk"a NNN-x adıyla taşır

Akış: ham videoyu "ham"a koy -> cikar -> x.srt'yi kontrol et/düzelt (gerekirse yeniden yükle) -> gom
      -> x-altyazili.mp4'ü izle -> x.txt açıklamasını "ham"a koy -> kuyruga
"""
import json
import os
import pathlib
import re
import subprocess
import sys

HAM = "ham"
KUYRUK = "kuyruk"
IS = pathlib.Path("is")
IS.mkdir(exist_ok=True)

# Altyazı stili: Reels'te yaygın - kalın beyaz yazı, siyah kenar, ekranın ortasının biraz altında,
# aynı anda en fazla 3 kelime, kısa "pop" animasyonu. 1080x1920 tuval üzerinde.
ASS_BASLIK = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Vay,Montserrat ExtraBold,88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,90,90,640,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
EN_FAZLA_KELIME = 3
EN_FAZLA_KARAKTER = 20
DURAKLAMA = 0.35  # bu kadar sessizlikte yeni parça


def log(m):
    print(m, flush=True)


def gh(*args, kontrol=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if kontrol and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} -> {r.stderr.strip()}")
    return r


def ekler(tag):
    return [a["name"] for a in json.loads(gh("release", "view", tag, "--json", "assets").stdout)["assets"]]


def indir(tag, ad):
    gh("release", "download", tag, "-p", ad, "-D", str(IS), "--clobber")
    return IS / ad


def ham_videolar(ad_listesi):
    return sorted(n for n in ad_listesi if n.lower().endswith(".mp4") and not n.endswith("-altyazili.mp4"))


# ---------- SRT yardımcıları ----------
def srt_zaman(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def ass_zaman(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def srt_oku(yol):
    metin = yol.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    parcalar = []
    for blok in re.split(r"\n\s*\n", metin.strip()):
        satirlar = blok.strip().split("\n")
        zs = next((i for i, s in enumerate(satirlar) if "-->" in s), None)
        if zs is None:
            continue
        a, b = [x.strip() for x in satirlar[zs].split("-->")]

        def sn(z):
            h, m, s = z.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)

        yazi = " ".join(s.strip() for s in satirlar[zs + 1:] if s.strip())
        if yazi:
            parcalar.append((sn(a), sn(b), yazi))
    return parcalar


# ---------- 1) cikar ----------
def cikar():
    from faster_whisper import WhisperModel

    adlar = ekler(HAM)
    isler = [v for v in ham_videolar(adlar) if v[:-4] + ".srt" not in adlar]
    if not isler:
        log("Altyazısı çıkarılacak video yok.")
        return
    model_adi = os.environ.get("WHISPER_MODEL", "large-v3")
    log(f"Whisper modeli yükleniyor: {model_adi}")
    model = WhisperModel(model_adi, device="cpu", compute_type="int8")

    for video in isler:
        log(f"\n=== {video}")
        yol = indir(HAM, video)
        wav = IS / (video[:-4] + ".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(yol), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(wav)], check=True)
        # Sesi kendimiz okuyup diziyi veriyoruz: faster-whisper'ın PyAV ile dosya açması bazı sürümlerde bozuk
        # ("open() got an unexpected keyword argument 'metadata_errors'", 2026-11-20).
        import wave
        import numpy as np
        with wave.open(str(wav)) as f:
            ses = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segmentler, _ = model.transcribe(ses, language="tr", word_timestamps=True, vad_filter=True, beam_size=5)
        # Whisper Türkçe ekleri ayrı kelime veriyor ("Rize 'ye"); kesme işaretiyle başlayanları öncekine yapıştır.
        from collections import namedtuple
        Kelime = namedtuple("Kelime", "word start end")
        # Whisper'ın sessizlik/müzik üstüne uydurduğu bilinen kapanış cümlelerini at.
        uydurma = ("izlediğiniz için teşekkür", "izlediğiniz için teşekkürler", "abone olmayı unutmayın",
                   "altyazı m.k", "bir sonraki videoda görüşmek")
        segmentler = [s for s in segmentler
                      if not any(u in s.text.lower().replace("i̇", "i") for u in uydurma)
                      or log(f"Uydurma cümle atıldı: {s.text.strip()}")]
        kelimeler = []
        for w in (w for s in segmentler for w in (s.words or []) if w.word.strip()):
            yazi = w.word.strip()
            if kelimeler and yazi[:1] in "'’":
                o = kelimeler[-1]
                kelimeler[-1] = Kelime(o.word.strip() + yazi.replace("’", "'"), o.start, w.end)
            else:
                kelimeler.append(Kelime(yazi, w.start, w.end))

        parcalar, simdiki = [], []
        for w in kelimeler:
            yazi = w.word.strip()
            if simdiki:
                onceki = simdiki[-1]
                uzun = len(" ".join(x.word.strip() for x in simdiki) + " " + yazi) > EN_FAZLA_KARAKTER
                if (len(simdiki) >= EN_FAZLA_KELIME or uzun or w.start - onceki.end > DURAKLAMA
                        or onceki.word.strip()[-1:] in ".,!?;:"):
                    parcalar.append(simdiki)
                    simdiki = []
            simdiki.append(w)
        if simdiki:
            parcalar.append(simdiki)

        satirlar = []
        for i, p in enumerate(parcalar):
            bas = p[0].start
            son = max(p[-1].end, bas + 0.3)
            if i + 1 < len(parcalar):
                son = min(son, parcalar[i + 1][0].start)
            yazi = " ".join(x.word.strip() for x in p)
            satirlar.append(f"{i + 1}\n{srt_zaman(bas)} --> {srt_zaman(son)}\n{yazi}\n")
        srt = IS / (video[:-4] + ".srt")
        srt.write_text("\n".join(satirlar), encoding="utf-8")
        gh("release", "upload", HAM, str(srt), "--clobber")
        log(f"{srt.name} yüklendi ({len(parcalar)} parça). Metin:")
        log(" ".join(x.word.strip() for x in kelimeler))


# ---------- 2) gom ----------
def gom():
    adlar = ekler(HAM)
    isler = [v for v in ham_videolar(adlar)
             if v[:-4] + ".srt" in adlar and v[:-4] + "-altyazili.mp4" not in adlar]
    if not isler:
        log("Altyazı gömülecek video yok (önce 'cikar' çalıştırılmalı).")
        return
    for video in isler:
        log(f"\n=== {video}")
        yol = indir(HAM, video)
        parcalar = srt_oku(indir(HAM, video[:-4] + ".srt"))
        olaylar = []
        for a, b, yazi in parcalar:
            yazi = yazi.replace("{", "(").replace("}", ")").replace("\\", "/")
            pop = r"{\fscx85\fscy85\t(0,90,\fscx100\fscy100)}"
            olaylar.append(f"Dialogue: 0,{ass_zaman(a)},{ass_zaman(b)},Vay,,0,0,0,,{pop}{yazi}")
        ass = IS / (video[:-4] + ".ass")
        ass.write_text(ASS_BASLIK + "\n".join(olaylar) + "\n", encoding="utf-8")
        cikti = IS / (video[:-4] + "-altyazili.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(yol),
            "-vf", f"scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p,ass={ass}",
            "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "18", "-maxrate", "20M", "-bufsize", "40M",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(cikti),
        ], check=True)
        gh("release", "upload", HAM, str(cikti), "--clobber")
        log(f"{cikti.name} yüklendi ({cikti.stat().st_size / 1e6:.1f} MB, {len(olaylar)} altyazı parçası).")


# ---------- goster ----------
def goster():
    """"ham"daki bütün .srt dosyalarını loga yazar (kontrol/düzeltme için)."""
    for srt in sorted(n for n in ekler(HAM) if n.endswith(".srt")):
        log(f"\n######## {srt}")
        log(indir(HAM, srt).read_text(encoding="utf-8-sig"))


# ---------- 3) kuyruga ----------
def kuyruga():
    adlar = ekler(HAM)
    hazirlar = sorted(n for n in adlar if n.endswith("-altyazili.mp4") and n[:-len("-altyazili.mp4")] + ".txt" in adlar)
    if not hazirlar:
        log("Kuyruğa eklenecek hazır video yok (x-altyazili.mp4 ve x.txt birlikte olmalı).")
        return
    kuyruk = ekler(KUYRUK)
    no = max([int(m.group(1)) for n in kuyruk if (m := re.match(r"^(\d{3})-", n))] + [0])
    for hazir in hazirlar:
        kok = hazir[:-len("-altyazili.mp4")]
        no += 1
        yeni = f"{no:03d}-{kok}"
        v = indir(HAM, hazir).rename(IS / f"{yeni}.mp4")
        t = indir(HAM, kok + ".txt").rename(IS / f"{yeni}.txt")
        gh("release", "upload", KUYRUK, str(v), str(t))
        for eski in (hazir, kok + ".txt", kok + ".mp4"):
            if eski in adlar:
                gh("release", "delete-asset", HAM, eski, "-y", kontrol=False)
        log(f"Kuyruğa eklendi: {yeni}.mp4 + {yeni}.txt")


if __name__ == "__main__":
    islem = sys.argv[1] if len(sys.argv) > 1 else ""
    if gh("release", "view", HAM, kontrol=False).returncode != 0:
        gh("release", "create", HAM, "--title", "Ham videolar (altyazı hattı)",
           "--notes", "Altyazı eklenecek videolar. Bkz. altyazi.py")
    {"cikar": cikar, "goster": goster, "gom": gom, "kuyruga": kuyruga}[islem]()

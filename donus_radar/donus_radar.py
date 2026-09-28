#!/usr/bin/env python3
"""
donus_radar.py -- Dönüş Radarı: zirve / dip yapıp dönen coinleri bulur.

Girdi : /var/www/veri/turev_radar.json  (coin evreni: yalnız Binance vadelide listeli coinler)
        /var/www/veri/ticker_ws.json    (evren yedeği, turev_radar yoksa)
        Binance SPOT mumları (data-api.binance.vision) -> yoksa Bybit vadeli mumları
Çıktı : /var/www/veri/donus_radar.json  (panel ve rsi_grafik.html bunu okur)
Önbellek: <bu klasör>/donus.db (sqlite: mumlar + canlı sinyal defteri)

Ağ: Binance FAPI'ye HİÇ gitmez (fapi ban gate'i gerekmez). Spot ve Bybit
public uçları, hız sınırlı; 418/429 gelirse Retry-After kadar o kaynak susar.
Her çalıştırmada yalnız yeni kapanan mumlar çekilir (1s her saat, 4s dört
saatte bir, 1g günde bir).

Çalıştırma: systemd timer ile saat başı (:02).  Elle:  python3 donus_radar.py
Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.
"""
import argparse
import datetime as dt
import json
import math
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donus_cekirdek as dc  # noqa: E402

# ---------------------------------------------------------------- ayarlar
KLASOR = os.path.dirname(os.path.abspath(__file__))
VERI_DIR = os.environ.get("DONUS_VERI_DIR", "/var/www/veri")
DB_YOL = os.environ.get("DONUS_DB", os.path.join(KLASOR, "donus.db"))
TUREV = os.environ.get("DONUS_TUREV", os.path.join(VERI_DIR, "turev_radar.json"))
TICKER = os.environ.get("DONUS_TICKER", os.path.join(VERI_DIR, "ticker_ws.json"))
CIKTI = os.environ.get("DONUS_CIKTI", os.path.join(VERI_DIR, "donus_radar.json"))
KARNE = os.environ.get("DONUS_KARNE", os.path.join(VERI_DIR, "donus_karne.json"))
SPOT_URL = os.environ.get("DONUS_SPOT_URL", "https://data-api.binance.vision/api/v3/klines")
BYBIT_URL = os.environ.get("DONUS_BYBIT_URL", "https://api.bybit.com/v5/market/kline")
HIZ = float(os.environ.get("DONUS_HIZ", "5"))          # saniyede en fazla istek (tüm iş parçacıkları)
ISCI = int(os.environ.get("DONUS_ISCI", "4"))
MIN_HACIM = float(os.environ.get("DONUS_MIN_HACIM", "250000"))   # 24s hacmi bunun altındaki coinler taranmaz
TFLER = ["1h", "4h", "1d"]
MUM_LIMIT = 1000              # canlı analizde kullanılan son mum sayısı
# coin/TF başına saklanan mum (karne birden çok piyasa dönemini görsün: ~6 ay / ~16 ay / ~4 yıl)
SAKLA = {"1h": 4500, "4h": 3000, "1d": 1500}
IZLE_MIN = 60                 # İZLE (teyitsiz) adayı: işaret skoru bu değerin altındaysa listelenmez
KARNE_MIN_N = 300             # karnenin mesafe tablosu bu kadar örnekten azsa varsayılan tablo kullanılır
SONUC_GOSTER_SAAT = 48        # sonuçlanan sinyaller panelde kaç saat görünür
BEKLE_DOSYA = os.path.join(os.path.dirname(os.path.abspath(DB_YOL)), ".kaynak_bekle.json")
STABIL = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDE", "PYUSD", "USD1", "EUR", "AEUR", "EURI",
          "XAUT", "PAXG", "USDS", "RLUSD", "BFUSD"}
UYARI = "Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def atomik_json(yol, veri):
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, yol)


def oku_json(yol):
    if yol.startswith("http"):
        with urllib.request.urlopen(urllib.request.Request(yol, headers={"User-Agent": "donus-radar"}), timeout=60) as r:
            return json.loads(r.read())
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- ağ
class Kisitlayici:
    """Tüm iş parçacıkları için ortak hız sınırı + kaynak bazında soğuma."""

    def __init__(self, hiz):
        self.aralik = 1.0 / hiz
        self.kilit = threading.Lock()
        self.sonraki = 0.0
        self.bekle = {}
        try:
            with open(BEKLE_DOSYA) as f:
                self.bekle = json.load(f)
        except Exception:
            self.bekle = {}

    def sira(self):
        with self.kilit:
            simdi = time.monotonic()
            t = max(simdi, self.sonraki)
            self.sonraki = t + self.aralik
        if t > simdi:
            time.sleep(t - simdi)

    def susuk(self, kaynak):
        return self.bekle.get(kaynak, 0) > time.time()

    def sustur(self, kaynak, sn):
        with self.kilit:
            self.bekle[kaynak] = max(self.bekle.get(kaynak, 0), time.time() + sn)
            try:
                atomik_json(BEKLE_DOSYA, self.bekle)
            except Exception:
                pass
        log("!! %s %d sn susturuldu" % (kaynak, sn))


KIS = Kisitlayici(HIZ)


class KaynakYok(Exception):
    pass


def http_json(url, kaynak):
    kalan = KIS.bekle.get(kaynak, 0) - time.time()
    if 0 < kalan <= 90:
        time.sleep(kalan)              # kısa soğuma: bekle, işi düşürme
    elif kalan > 90:
        raise RuntimeError(kaynak + " soğumada")
    KIS.sira()
    req = urllib.request.Request(url, headers={"User-Agent": "donus-radar/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (418, 429):
            ra = int(e.headers.get("Retry-After") or (300 if e.code == 418 else 60))
            KIS.sustur(kaynak, ra + 10)
            raise RuntimeError("%s HTTP %d" % (kaynak, e.code))
        govde = e.read()[:300].decode("utf-8", "replace")
        if e.code == 400 and "Invalid symbol" in govde:
            raise KaynakYok(govde)
        raise RuntimeError("%s HTTP %d %s" % (kaynak, e.code, govde))


def mum_spot(sym, tf, limit, bitis=None):
    q = {"symbol": sym, "interval": tf, "limit": limit}
    if bitis:
        q["endTime"] = bitis
    u = SPOT_URL + "?" + urllib.parse.urlencode(q)
    a = http_json(u, "spot")
    simdi = time.time() * 1000
    return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7]), int(k[8]), float(k[10]))
            for k in a if int(k[6]) < simdi]


BYBIT_IV = {"1h": "60", "4h": "240", "1d": "D"}


def mum_bybit(sym, tf, limit, bitis=None):
    q = {"category": "linear", "symbol": sym, "interval": BYBIT_IV[tf], "limit": limit}
    if bitis:
        q["end"] = bitis
    u = BYBIT_URL + "?" + urllib.parse.urlencode(q)
    d = http_json(u, "bybit")
    if d.get("retCode") == 10006:
        KIS.sustur("bybit", 30)
        raise RuntimeError("bybit hız sınırı")
    if d.get("retCode") != 0:
        raise KaynakYok(str(d.get("retMsg")))
    lst = (d.get("result") or {}).get("list") or []
    if not lst:
        if bitis:
            return []                  # geçmişin başına gelindi
        raise KaynakYok("bybit boş")
    simdi = time.time() * 1000
    dur = dc.TF_MS[tf]
    out = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[6]), None, None)
           for k in reversed(lst) if int(k[0]) + dur <= simdi]
    return out


# ---------------------------------------------------------------- veritabanı
def db_ac():
    db = sqlite3.connect(DB_YOL, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS mum(s TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, qv REAL, n INTEGER, tb REAL,
                                   PRIMARY KEY(s, tf, t)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS kaynak(s TEXT PRIMARY KEY, src TEXT, src_sym TEXT, carpan REAL, kontrol INTEGER);
    CREATE TABLE IF NOT EXISTS sinyal(id TEXT PRIMARY KEY, s TEXT, tf TEXT, yon TEXT, zirve_ts INTEGER, tetik_ts INTEGER,
        zirve REAL, giris REAL, stop REAL, hedef REAL, skor REAL, b TEXT, ilk INTEGER,
        sonuc TEXT, r REAL, r_net REAL, tuttu INTEGER, son_ts INTEGER, guncel INTEGER);
    """)
    return db


def son_t(db, s, tf):
    r = db.execute("SELECT max(t) FROM mum WHERE s=? AND tf=?", (s, tf)).fetchone()
    return r[0] if r and r[0] else None


def seri_oku(db, s, tf, carpan=1.0, limit=MUM_LIMIT):
    rows = db.execute("SELECT t,o,h,l,c,qv,n,tb FROM (SELECT * FROM mum WHERE s=? AND tf=? ORDER BY t DESC LIMIT ?) ORDER BY t",
                      (s, tf, limit)).fetchall()
    if len(rows) < 80:
        return None
    k = 1.0 / (carpan or 1.0)
    t = [r[0] for r in rows]
    return dc.Seri(t, [r[1] * k for r in rows], [r[2] * k for r in rows], [r[3] * k for r in rows], [r[4] * k for r in rows],
                   [r[5] for r in rows], [r[6] for r in rows], [r[7] for r in rows], tf=tf)


# ---------------------------------------------------------------- evren
def evren_oku():
    """Binance vadelide listeli coinler (turev_radar.json). Yoksa ticker_ws.json."""
    try:
        d = oku_json(TUREV)
        out = []
        for c in d.get("coin", []):
            b = str(c.get("b", ""))
            if not b or not b.isascii() or c.get("sup") or b in STABIL:
                continue
            v = c.get("v24")
            if v is not None and v < MIN_HACIM:
                continue
            out.append({"s": b + "USDT", "b": b, "v24": v})
        if out:
            return out, "turev_radar"
    except Exception as e:
        log("turev_radar okunamadı:", e)
    d = oku_json(TICKER)
    out = []
    for x in d.get("ticker24", []):
        s = x.get("symbol", "")
        if not s.endswith("USDT") or not s.isascii():
            continue
        b = s[:-4]
        if b in STABIL or float(x.get("quoteVolume") or 0) < MIN_HACIM:
            continue
        out.append({"s": s, "b": b, "v24": float(x.get("quoteVolume") or 0)})
    return out, "ticker_ws"


def turev_haritasi():
    try:
        d = oku_json(TUREV)
        return {c["b"]: c for c in d.get("coin", []) if c.get("b")}
    except Exception:
        return {}


# ---------------------------------------------------------------- mum güncelleme
def kaynak_coz(db, s):
    """Coin için mum kaynağı: Binance spot (aynı sembol) -> Bybit vadeli (aynı sembol, sonra 1000'li adlar)."""
    r = db.execute("SELECT src, src_sym, carpan, kontrol FROM kaynak WHERE s=?", (s,)).fetchone()
    if r and (r[0] == "spot" or time.time() - (r[3] or 0) < 7 * 86400):
        return r[0], r[1], r[2]
    return None


def kaynak_bul(s):
    """Ağdan kaynak dene (iş parçacığında çalışır; db yazmaz)."""
    try:
        mum_spot(s, "1d", 2)
        return ("spot", s, 1.0)
    except KaynakYok:
        pass
    b = s[:-4]
    for ad, k in ((s, 1.0), ("1000" + s, 1000.0), ("10000" + s, 10000.0), ("1000000" + s, 1e6), (b + "1000USDT", 1000.0)):
        try:
            mum_bybit(ad, "1d", 2)
            return ("bybit", ad, k)
        except KaynakYok:
            continue
    return ("yok", None, None)


def guncelle_is(s, src, src_sym, tf, son):
    """Bir coin/TF için eksik kapanmış mumları getirir (iş parçacığı)."""
    dur = dc.TF_MS[tf]
    simdi = int(time.time() * 1000)
    son_kapali = (simdi // dur) * dur - dur
    if son and son >= son_kapali:
        return s, tf, []
    limit = MUM_LIMIT if not son else int(min(MUM_LIMIT, (son_kapali - son) // dur + 3))
    f = mum_spot if src == "spot" else mum_bybit
    return s, tf, f(src_sym, tf, limit)


def mumlari_guncelle(db, evren, tfler):
    # 1) kaynağı bilinmeyenleri çöz
    coz = [c["s"] for c in evren if kaynak_coz(db, c["s"]) is None]
    if coz:
        log("kaynak çözülüyor:", len(coz), "coin")
        with ThreadPoolExecutor(ISCI) as ex:
            for s, sonuc in zip(coz, ex.map(lambda x: _guvenli(kaynak_bul, x), coz)):
                if sonuc is None:
                    continue
                eski = db.execute("SELECT src_sym FROM kaynak WHERE s=?", (s,)).fetchone()
                if eski and eski[0] != sonuc[1]:
                    db.execute("DELETE FROM mum WHERE s=?", (s,))     # kaynak değişti: karışık seri tutma
                db.execute("INSERT OR REPLACE INTO kaynak VALUES(?,?,?,?,?)", (s, sonuc[0], sonuc[1], sonuc[2], int(time.time())))
        db.commit()
    # 2) mumlar
    isler = []
    for c in evren:
        k = kaynak_coz(db, c["s"])
        if not k or k[0] == "yok":
            continue
        for tf in tfler:
            isler.append((c["s"], k[0], k[1], tf, son_t(db, c["s"], tf)))
    hata = 0
    yeni = 0
    with ThreadPoolExecutor(ISCI) as ex:
        for sonuc in ex.map(lambda a: _guvenli(guncelle_is, *a), isler):
            if sonuc is None:
                hata += 1
                continue
            s, tf, rows = sonuc
            if rows:
                db.executemany("INSERT OR REPLACE INTO mum VALUES(?,?,?,?,?,?,?,?,?,?)",
                               [(s, tf) + r for r in rows])
                yeni += len(rows)
    db.commit()
    log("mum güncelleme: %d iş, %d yeni mum, %d hata" % (len(isler), yeni, hata))
    return hata


def _guvenli(f, *a):
    try:
        return f(*a)
    except KaynakYok:
        return None
    except Exception as e:
        log("hata", a[:1], a[3] if len(a) > 3 else "", str(e)[:120])
        return None


def derin_is(s, src, src_sym, tf, en_eski, adet):
    """Önbellekteki en eski mumdan geriye doğru `adet` mum daha getirir (karne için)."""
    f = mum_spot if src == "spot" else mum_bybit
    out = []
    bitis = en_eski - 1
    while len(out) < adet:
        rows = f(src_sym, tf, MUM_LIMIT, bitis)
        rows = [r for r in rows if r[0] < bitis]
        if not rows:
            break
        out = rows + out
        bitis = rows[0][0] - 1
        if len(rows) < MUM_LIMIT // 2:
            break                      # coinin geçmişi bitti
    return s, tf, out


def derin_doldur(db, evren):
    """Tek seferlik / haftalık: karne için geçmişi SAKLA hedefine kadar geriye doldurur."""
    isler = []
    for c in evren:
        k = kaynak_coz(db, c["s"])
        if not k or k[0] == "yok":
            continue
        for tf in TFLER:
            n, eski = db.execute("SELECT count(*), min(t) FROM mum WHERE s=? AND tf=?", (c["s"], tf)).fetchone()
            if eski and n < SAKLA[tf] - 50:
                isler.append((c["s"], k[0], k[1], tf, eski, SAKLA[tf] - n))
    log("derin doldurma:", len(isler), "iş")
    yeni = 0
    with ThreadPoolExecutor(ISCI) as ex:
        for sonuc in ex.map(lambda a: _guvenli(derin_is, *a), isler):
            if sonuc and sonuc[2]:
                db.executemany("INSERT OR IGNORE INTO mum VALUES(?,?,?,?,?,?,?,?,?,?)", [(sonuc[0], sonuc[1]) + r for r in sonuc[2]])
                db.commit()            # kısa işlemler: karne aynı anda okuyabilsin
                yeni += len(sonuc[2])
    log("derin doldurma: %d mum eklendi" % yeni)


def budama(db):
    for tf in TFLER:
        sin = int(time.time() * 1000) - SAKLA[tf] * dc.TF_MS[tf]
        db.execute("DELETE FROM mum WHERE tf=? AND t<?", (tf, sin))
    db.commit()


# ---------------------------------------------------------------- analiz
TF_AD = {"1h": "1s", "4h": "4s", "1d": "1g"}


def turev_rozet(c, yon):
    """Türev verisinden ek bilgi (karneye girmez, sınanmadı)."""
    if not c:
        return []
    out = []
    fz, o24, d24, lp = c.get("fz"), c.get("o24s"), c.get("d24s"), c.get("long_pct")
    if yon == "ZIRVE":
        if isinstance(fz, (int, float)) and fz >= 2:
            out.append("Fonlama aşırı pozitif (z %.1f)" % fz)
        if isinstance(o24, (int, float)) and isinstance(d24, (int, float)) and o24 >= 8 and abs(d24) < 3:
            out.append("OI 24s %+.0f%%, fiyat duruyor" % o24)
        if isinstance(lp, (int, float)) and lp >= 70:
            out.append("Hesapların %%%d'i long" % lp)
    else:
        if isinstance(fz, (int, float)) and fz <= -2:
            out.append("Fonlama aşırı negatif (z %.1f)" % fz)
        l4, s4 = c.get("lq4s_l") or 0, c.get("lq4s_s") or 0
        oi = c.get("oi") or 0
        if oi and l4 >= 0.01 * oi and l4 > 3 * s4:
            out.append("Long tasfiye şelalesi (4s %s$)" % kisa_usd(l4))
        if isinstance(lp, (int, float)) and lp <= 40:
            out.append("Hesapların %%%d'i short" % (100 - lp))
    return out


def kisa_usd(v):
    for e, k in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= e:
            return "%.1f%s" % (v / e, k)
    return "%.0f" % v


def yuvarla_b(b):
    return {k: (None if v is None else round(v, 2)) for k, v in b.items()}


def etiketler(b):
    return [dc.BILESEN_AD[k] for k, v in sorted(b.items(), key=lambda kv: -(kv[1] or 0) * dc.AGIRLIK.get(kv[0], 0))
            if k in dc.AGIRLIK and v is not None and v >= 0.5]


def tutma_tablosu(karne):
    """Gece karnesinin mesafe tablosu (yeterli örnek varsa), yoksa çekirdekteki varsayılan."""
    out, kaynak = {}, {}
    for tf in TFLER:
        for yon in ("ZIRVE", "DIP"):
            z = ((karne or {}).get(tf) or {}).get(yon) or {}
            satir = [[m["a"], m["b"], m["n"], m.get("tutma"), m.get("ort_r_net")] for m in z.get("mesafe", []) if m.get("n")]
            yeterli = satir and sum(x[2] for x in satir) >= KARNE_MIN_N
            out.setdefault(tf, {})[yon] = satir if yeterli else dc.VARSAYILAN_TUTMA[tf][yon]
            kaynak.setdefault(tf, {})[yon] = "karne" if yeterli else "varsayilan"
    return out, kaynak


def tutma_alanlari(tablo, tf, yon, mesafe):
    t, n, r, kova = dc.tutma_bul(tablo, tf, yon, mesafe)
    return {"mesafe_atr": round(mesafe, 2), "tutma": t, "tutma_n": n, "bek_r": None if r is None else round(r, 3), "kova": kova}


def fyuv(x):
    if x is None:
        return None
    if x == 0:
        return 0.0
    return round(x, max(2, 6 - int(math.floor(math.log10(abs(x))))))


def analiz(db, evren, turev, tablo):
    sinyaller, sonuclanan = [], []
    simdi = int(time.time() * 1000)
    defter = []
    for c in evren:
        s = c["s"]
        k = kaynak_coz(db, s)
        if not k or k[0] == "yok":
            continue
        seriler = {}
        for tf in TFLER:
            sr = seri_oku(db, s, tf, k[2] if k[0] == "bybit" else 1.0)
            if sr:
                seriler[tf] = sr
        if not seriler:
            continue
        ustler = {tf: dc.UstDurum(v) for tf, v in seriler.items()}
        if "1d" in seriler:
            w = dc.haftalik(seriler["1d"])
            if w and len(w) > 60:
                ustler["1w"] = dc.UstDurum(w)
        tv = turev.get(c["b"])
        for tf, sr in seriler.items():
            son_c = sr.c[-1]
            rsi_son = sr.rsi[-1]
            for yon in ("ZIRVE", "DIP"):
                olaylar, izle = dc.olaylari_bul(sr, yon, ustler)
                ufuk = dc.UFUK[tf]
                for o in olaylar:
                    # ufuk + 3 mum: sonucu ufkun son mumunda belli olan sinyal de deftere işlenebilsin
                    if o["tetik_ts"] < simdi - (ufuk + 3) * dc.TF_MS[tf]:
                        continue
                    ortak = {
                        "s": s, "tf": tf, "yon": yon, "skor": o["skor"], "fiyat": fyuv(son_c),
                        "zirve": fyuv(o["zirve"]), "zirve_ts": o["zirve_ts"], "tetik": fyuv(o["tetik"]), "tetik_ts": o["tetik_ts"],
                        "giris": fyuv(o["giris"]), "stop": fyuv(o["stop"]), "hedef": fyuv(o["hedef"]),
                        "risk_pct": o["risk_pct"], "b": yuvarla_b(o["b"]), "etiket": etiketler(o["b"]), "ust": o["ust_metin"],
                        "kaynak": k[0],
                    }
                    risk = abs(o["stop"] - o["giris"])
                    r_simdi = ((o["giris"] - son_c) if yon == "ZIRVE" else (son_c - o["giris"])) / risk if risk else 0
                    ortak.update(tutma_alanlari(tablo, tf, yon, o["mesafe_atr"]))
                    if o["sonuc"] == "acik":
                        x = dict(ortak, asama="TETIK", yas=len(sr) - 1 - o["p"], tetikten=len(sr) - 1 - o["i"],
                                 r_simdi=round(r_simdi, 2), zirveden_pct=round((son_c / o["zirve"] - 1) * 100, 2),
                                 rsi=None if rsi_son is None else round(rsi_son, 1), turev=turev_rozet(tv, yon))
                        sinyaller.append(x)
                    elif o["son_ts"] and o["son_ts"] >= simdi - SONUC_GOSTER_SAAT * 3600_000:
                        kisa = {k2: ortak[k2] for k2 in ("s", "tf", "yon", "skor", "zirve", "tetik_ts", "giris", "stop", "hedef", "tutma")}
                        sonuclanan.append(dict(kisa, sonuc=o["sonuc"], r=o["r"], r_net=o["r_net"], son_ts=o["son_ts"]))
                    defter.append((s, tf, yon, o))
                if izle and izle["skor"] >= IZLE_MIN:
                    sinyaller.append({
                        "s": s, "tf": tf, "yon": yon, "asama": "IZLE", "skor": izle["skor"], "fiyat": fyuv(son_c),
                        "zirve": fyuv(izle["zirve"]), "zirve_ts": izle["zirve_ts"], "tetik": fyuv(izle["tetik"]), "tetik_ts": None,
                        "stop": fyuv(izle["stop"]), "yas": izle["yas"], "zirveden_pct": round((son_c / izle["zirve"] - 1) * 100, 2),
                        "tetige_pct": round((izle["tetik"] / son_c - 1) * 100, 2),
                        "rsi": None if rsi_son is None else round(rsi_son, 1), "b": yuvarla_b(izle["b"]),
                        "etiket": etiketler(izle["b"]), "ust": izle["ust_metin"], "turev": turev_rozet(tv, yon), "kaynak": k[0],
                        "mesafe_atr": izle["mesafe_atr"],
                    })
    return sinyaller, sonuclanan, defter


def defter_yaz(db, defter, ilk_calisma):
    """Canlı defter: sistem çalışmaya başladıktan SONRA tetiklenen sinyaller.
    Geçmişe dönük sinyaller buraya girmez (onlar gece karnesinde)."""
    simdi = int(time.time() * 1000)
    for s, tf, yon, o in defter:
        sid = "%s|%s|%s|%d" % (s, tf, yon, o["zirve_ts"])
        var = db.execute("SELECT sonuc FROM sinyal WHERE id=?", (sid,)).fetchone()
        if var is None:
            # yalnız tetiği bu çalışmadan kısa süre önce olanlar (ilk çalışmada geçmiş yazılmaz)
            if ilk_calisma or o["tetik_ts"] < simdi - 2 * dc.TF_MS[tf] - 3600_000:
                continue
            db.execute("INSERT INTO sinyal VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sid, s, tf, yon, o["zirve_ts"], o["tetik_ts"], o["zirve"], o["giris"], o["stop"], o["hedef"],
                        o["skor"], json.dumps(yuvarla_b(o["b"])), simdi, o["sonuc"], o["r"], o["r_net"],
                        None if o["tuttu"] is None else int(o["tuttu"]), o["son_ts"], simdi))
        elif var[0] == "acik" or o["tuttu"] is not None:
            db.execute("UPDATE sinyal SET sonuc=?, r=?, r_net=?, tuttu=?, son_ts=?, guncel=? WHERE id=?",
                       (o["sonuc"], o["r"], o["r_net"], None if o["tuttu"] is None else int(o["tuttu"]), o["son_ts"], simdi, sid))
    db.commit()


def canli_karne(db):
    out = {}
    for tf, yon, n, hd, st, su, rn, tt, ttn in db.execute("""
        SELECT tf, yon, count(*), sum(sonuc='hedef'), sum(sonuc='stop'), sum(sonuc='sure'), avg(r_net),
               sum(tuttu=1), sum(tuttu IS NOT NULL)
        FROM sinyal WHERE sonuc!='acik' GROUP BY tf, yon"""):
        out.setdefault(tf, {})[yon] = {
            "n": n, "isabet": round(100 * hd / n, 1) if n else None, "stop": round(100 * st / n, 1) if n else None,
            "sure": round(100 * su / n, 1) if n else None, "ort_r_net": None if rn is None else round(rn, 3),
            "tutma": round(100 * tt / ttn, 1) if ttn else None,
        }
    acik = db.execute("SELECT count(*) FROM sinyal WHERE sonuc='acik'").fetchone()[0]
    ilk = db.execute("SELECT min(ilk) FROM sinyal").fetchone()[0]
    return {"tablo": out, "acik": acik, "baslangic": ilk}


# ---------------------------------------------------------------- ana akış
def main():
    ap = argparse.ArgumentParser(description="Dönüş Radarı")
    ap.add_argument("--limit", type=int, default=0, help="yalnız ilk N coin (test)")
    ap.add_argument("--sadece-analiz", action="store_true", help="ağa çıkma, önbellekten hesapla")
    ap.add_argument("--semboller", default="", help="virgüllü sembol listesi (test)")
    ap.add_argument("--derin", action="store_true", help="karne için geçmişi geriye doldur (ilk kurulumda bir kez)")
    a = ap.parse_args()
    t0 = time.time()
    import fcntl
    kilit = open(os.path.join(os.path.dirname(os.path.abspath(DB_YOL)), ".donus_radar.lock"), "w")
    try:
        fcntl.flock(kilit, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("başka bir donus_radar çalışıyor; bu tur atlandı")
        return
    db = db_ac()
    ilk_calisma = db.execute("SELECT count(*) FROM mum").fetchone()[0] == 0
    evren, evren_kaynak = evren_oku()
    evren.sort(key=lambda c: -(c.get("v24") or 0))
    if a.semboller:
        iste = {x.strip().upper() for x in a.semboller.split(",") if x.strip()}
        evren = [c for c in evren if c["s"] in iste] or [{"s": x, "b": x[:-4], "v24": None} for x in iste]
    if a.limit:
        evren = evren[:a.limit]
    log("evren:", len(evren), "coin (%s)" % evren_kaynak)
    hata = 0
    if not a.sadece_analiz:
        hata = mumlari_guncelle(db, evren, TFLER)
        if a.derin:
            derin_doldur(db, evren)
        budama(db)
    karne = None
    try:
        karne = oku_json(KARNE).get("ozet")
    except Exception:
        pass
    tablo, tablo_kaynak = tutma_tablosu(karne)
    turev = turev_haritasi()
    sinyaller, sonuclanan, defter = analiz(db, evren, turev, tablo)
    defter_yaz(db, defter, ilk_calisma)
    # tetikliler: en yeni tetik önce; adaylar: işaret skoru
    sinyaller.sort(key=lambda x: (0, -x["tetik_ts"], -x["skor"]) if x["asama"] == "TETIK" else (1, 0, -x["skor"]))
    sonuclanan.sort(key=lambda x: -x["son_ts"])
    kaynaklar = dict(db.execute("SELECT src, count(*) FROM kaynak GROUP BY src").fetchall())
    cikti = {
        "surum": 1,
        "guncel": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ts": int(time.time()),
        "sure_sn": round(time.time() - t0, 1),
        "evren": {"n": len(evren), "kaynak": evren_kaynak, "min_hacim": MIN_HACIM, "mum_kaynak": kaynaklar, "hata": hata},
        "param": dict(dc.PARAM, UFUK=dc.UFUK, IZLE_MIN=IZLE_MIN),
        "tutma_tablosu": tablo, "tutma_kaynak": tablo_kaynak,
        "agirlik": dc.AGIRLIK, "bilesen_ad": dc.BILESEN_AD, "kilitli_esikler": True,
        "sinyaller": sinyaller,
        "sonuclanan": sonuclanan[:150],
        "canli": canli_karne(db),
        "karne": karne,
        "uyari": UYARI,
    }
    atomik_json(CIKTI, cikti)
    nt = sum(1 for x in sinyaller if x["asama"] == "TETIK")
    log("[OK] %s -- %d tetik, %d izle, %d sonuçlanan, %.0f sn" % (CIKTI, nt, len(sinyaller) - nt, len(sonuclanan), time.time() - t0))


if __name__ == "__main__":
    main()

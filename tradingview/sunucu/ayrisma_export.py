#!/usr/bin/env python3
"""
ayrisma_export.py — AYRIŞMA · Piyasa Uyumu tarayıcısı
(TradingView "AYRIŞMA + MSB-OB" göstergesinin piyasa uyumu bölümünün sunucu karşılığı)

Görevi : OTHERS evrenindeki (ilk 10 + hisse/emtia perp'leri hariç) Bybit USDT perp coinleri
         için 1s, 4s ve 1g'de "coinin fiyatı piyasanın durumuyla uyumlu mu?" sorusunu cevaplar
         → /var/www/veri/ayrisma.json  (ayrisma_panel.html bunu okur)
Timer  : ayrisma_export.timer — her saat :02:30'da tek geçiş (oneshot)
Girdi  : /var/www/veri/ticker_ws.json (ws_ticker üretir, SALT-OKUR) → coin evreni, 24s veri
         /var/www/veri/dom.json       (dom_export üretir, SALT-OKUR) → dominans payları, TOTAL, OTHERS
Ağ     : Bybit public kline (api.bybit.com/v5/market/kline) + saatte 1 istek CoinGecko /global
         (yalnız USDC payı için; alınamazsa USDC "diğer ilk-10" içinde kalır). Binance fapi'ye
         GİTMEZ → .fapi_ban kapısı gerekmez. Token-bucket throttle + 429/403 geri çekilme.
Durum  : /root/projelerim/ayrisma_dom_gecmis.json — saatlik dominans anlık görüntüleri
Bağımlılık: yalnız Python standart kütüphanesi. Mevcut botlara/dosyalara dokunmaz.
Yatırım tavsiyesi değildir.

MANTIK (göstergeyle aynı)
  1) Piyasa     : BTC, ETH ve majörler (BTC/ETH hariç ilk 10: XRP, BNB, SOL, DOGE, TRX, ADA; eşit ağırlıklı
                  sepet) — üçü 1/3 ağırlıkla. Birikimi / oynaklığı → ≥ +0,5σ ↑ · ≤ −0,5σ ↓ · arası ↔
  2) Para akışı : dominanslar aynı toplamın PAYLARIDIR → değişimleri yüzde PUAN olarak toplanır
                  (izleme listesindeki "Değ"). Karar OTHERS.D'den: 24 saatlik puan değişimi / bu
                  değişimin olağan büyüklüğü (σ, son 1 hafta) → ≥ +1σ altlara giriyor · ≤ −1σ çıkıyor.
                  Derece: <0,5σ yok · 0,5–1 hafif · 1–2 belirgin · ≥2 güçlü. Dolar karşılığı = puan × TOTAL.
                  Nereye / nereden: stabil (USDT.D + USDC.D), ETH.D, BTC.D, diğer ilk-10 (= kalan pay).
                  OTHERS'ta alt sepetinin açıklamadığı tek saatlik sıçrama (ilk-10 giriş/çıkışı,
                  veri düzeltmesi) o saat için 0 sayılır.
  3) Altlar için durum = piyasa × akış  (↑&çıkış → "piyasa yükseliyor ama altlar eziliyor" vb.)
  4) Coin       : getirisinden piyasa (BTC+ETH+majörler) VE alt sepeti (taranan coinlerin medyan getirisi) çok
                  değişkenli regresyonla birlikte çıkarılır; kalan birikim / oynaklık = z (σ)
                  → ≥ +1 önde · ≤ −1 geride · arası uyumlu
  Durum = (3) × (4) → 9 durum.
  Karne : her TF'de, bütün coinlerde coin durumu (önde / uyumlu / geride) BAŞLADIKTAN sonraki
          lenA bardaki getiri ve alt sepetine göre fazla getiri — eşiklerin gerçek veride ne
          işe yaradığını gösterir (n < MIN_KARNE ise "yetersiz").
"""
import json
import math
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# ── Yollar / ortam ayarları (ortam değişkeniyle ezilebilir; test için) ────────
VERI      = os.environ.get("AYR_VERI", "/var/www/veri")
TICKER    = os.environ.get("AYR_TICKER", os.path.join(VERI, "ticker_ws.json"))
DOM       = os.environ.get("AYR_DOM", os.path.join(VERI, "dom.json"))
CIKTI     = os.environ.get("AYR_CIKTI", os.path.join(VERI, "ayrisma.json"))
GECMIS    = os.environ.get("AYR_GECMIS", "/root/projelerim/ayrisma_dom_gecmis.json")
KLINE_URL = os.environ.get("AYR_KLINE_URL", "https://api.bybit.com/v5/market/kline")
MIN_HACIM = float(os.environ.get("AYR_MIN_HACIM", "3000000"))   # 24s işlem hacmi (USDT) alt sınırı
MAX_COIN  = int(os.environ.get("AYR_MAX_COIN", "0"))            # 0 = sınırsız (test için küçültülür)
ISTEK_HIZI = float(os.environ.get("AYR_ISTEK_HIZI", "8"))       # saniyede en fazla istek
PARALEL   = int(os.environ.get("AYR_PARALEL", "4"))

# ── Piyasa ve evren ──────────────────────────────────────────────────────────
# Piyasa yönü üç ayaktan: BTC, ETH ve MAJÖRLER (= BTC ve ETH hariç ilk 10'daki stabil olmayan coinler).
# Her ayak 1/3 ağırlık; majör sepeti kendi içinde eşit ağırlıklı. İlk 10 değişirse MAJORLER'i güncelleyin.
MAJORLER = ["XRPUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "TRXUSDT", "ADAUSDT"]
PIYASA_AGIRLIK = {"BTC": 1 / 3, "ETH": 1 / 3, "MAJ": 1 / 3}
# OTHERS = ilk 10 dışı. İlk 10'daki stabil olmayan coinler + stabil/altın perp'leri evrenden çıkarılır.
# Sıralama değişirse yalnız bu listeyi güncelleyin.
HARIC = {"BTC", "ETH", "XRP", "BNB", "SOL", "DOGE", "TRX", "ADA",
         "USDC", "USDE", "FDUSD", "DAI", "TUSD", "PYUSD", "USD1"}
# Bybit'teki geleneksel piyasa perp'leri (emtia, ETF, hisse) kripto değildir → OTHERS evrenine girmez.
# Yeni hisse/emtia perp'i görülürse AYR_HARIC_EK="ABC,XYZ" ile eklenebilir; "…STOCK" ile bitenler otomatik çıkar.
TRADFI = {"XAU", "XAG", "XPT", "XPD", "XAUT", "PAXG", "CL", "BZ", "NG",
          "SPY", "QQQ", "TQQQ", "SOXL", "SOXS", "KORU", "EWY",
          "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NFLX", "AMD", "INTC", "MU",
          "MSTR", "COIN", "HOOD", "CRCL", "PLTR", "NBIS", "SNDK", "SAMSUNG", "SKHYNIX", "SKHY"}
HARIC |= TRADFI | {s.strip().upper() for s in os.environ.get("AYR_HARIC_EK", "").split(",") if s.strip()}

# Zaman dilimleri — pencereler göstergenin "otomatik" tablosuyla aynı
TF = {
    "1h": {"iv": "60",  "sn": 3600,  "lenB": 168, "lenA": 24, "limit": 700, "ad": "1 saat"},
    "4h": {"iv": "240", "sn": 14400, "lenB": 120, "lenA": 30, "limit": 700, "ad": "4 saat"},
    # Günlük: Bybit'in en fazla 1000 mumu (~2,7 yıl). Yeni listelenen coinler için en az ~5 ay geçmiş yeter
    # (β 100 gün + ayrışma hafızası 3×14 gün); karne için daha uzun geçmişi olanlar kullanılır.
    "1d": {"iv": "D",   "sn": 86400, "lenB": 100, "lenA": 14, "limit": 1000, "ad": "1 gün", "min_bar": 150},
}
UYUM_BANT   = 1.0     # |z| < 1 → uyumlu (gri bant)
Z_ESIK      = 2.0     # |z| ≥ 2 → belirgin ayrışma
PIYASA_ESIK = 0.5     # piyasa yönü eşiği (σ)
SPARK_N     = 48      # tabloda mini grafik için son kaç z değeri
MIN_KARNE   = 30      # karnede bir durumun sonucu bu kadar örnekten azsa "yetersiz"

# Para akışı (göstergedeki gibi): dominans paylarının 24 saatlik PUAN değişimi
AKIS_PENCERE_SA = 24
AKIS_ESIK       = 1.0          # OTHERS.D |z| ≥ 1σ → giriş / çıkış
AKIS_SIGMA_N    = 168          # σ: son 1 haftanın saatlik 24s-değişimleri (göstergede 1s lenB = 168)
MIN_AKIS_ORNEK  = 24           # σ için en az bu kadar 24s-değişim (~48 saatlik geçmiş) — öncesi "ısınıyor"
DOM_SIC_K       = 6.0          # OTHERS sıçrama filtresi: sağlam σ katı …
DOM_SIC_MIN     = 0.02         # … ve en az %2 (log) açıklanamayan sıçrama
DOM_ANAHTAR     = ["OTHERS.D", "USDT.D", "BTC.D", "ETH.D"]
USDC_URL        = os.environ.get("AYR_USDC_URL", "https://api.coingecko.com/api/v3/global")   # boş = kapalı

UYARI = "Yatırım tavsiyesi değildir. Hesaplar kapanmış mumlarla yapılır; saatte bir güncellenir."


# ═══════════════════════════════════════════════════════════════════════════
# Yardımcılar
# ═══════════════════════════════════════════════════════════════════════════
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def atomic_write_json(path, data):
    """tmp dosyaya yaz → os.replace(). Hata olursa hedef dosyaya dokunulmaz."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def safe_read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def yuvarla(x, n=3):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(x, n)


class TokenBucket:
    """Saniyede `rate` istek, `burst` anlık pay. İş parçacığı güvenli."""
    def __init__(self, rate, burst=4):
        self.rate, self.burst, self.tokens = rate, burst, burst
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens < 1:
                time.sleep((1 - self.tokens) / self.rate)
                self.tokens = 0
                self.last = time.monotonic()
            else:
                self.tokens -= 1


KOVA = TokenBucket(ISTEK_HIZI, burst=4)


def http_json(url, deneme=4):
    """429 / 403 / 5xx ve ağ hatasında geri çekilerek tekrar dener."""
    bekle = 2.0
    for i in range(deneme):
        KOVA.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ayrisma_export/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 418, 429) or e.code >= 500:
                ra = e.headers.get("Retry-After") if e.headers else None
                s = float(ra) if ra else bekle
                log(f"[THROTTLE] HTTP {e.code} → {s:.0f}s bekleniyor ({i + 1}/{deneme})")
                time.sleep(s)
                bekle *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"[AĞ] {e} → {bekle:.0f}s sonra tekrar ({i + 1}/{deneme})")
            time.sleep(bekle)
            bekle *= 2
    raise RuntimeError(f"{deneme} denemede alınamadı: {url}")


def kline(sym, tf):
    """Bybit linear kline → kapanmış mumlar, eskiden yeniye [(t_ms, kapanış)]."""
    c = TF[tf]
    q = urllib.parse.urlencode({"category": "linear", "symbol": sym, "interval": c["iv"], "limit": c["limit"]})
    d = http_json(KLINE_URL + "?" + q)
    if not isinstance(d, dict) or d.get("retCode") not in (0, None):
        rc = d.get("retCode") if isinstance(d, dict) else "?"
        if rc == 10006:          # Bybit hız sınırı
            time.sleep(5)
        raise RuntimeError(f"{sym} {tf}: retCode={rc}")
    satir = (d.get("result") or {}).get("list") or []
    simdi_ms = int(time.time() * 1000)
    out = []
    for s in satir:
        t = int(s[0])
        if t + c["sn"] * 1000 > simdi_ms:        # henüz kapanmamış mum → alma
            continue
        out.append((t, float(s[4])))
    out.sort()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# İstatistik (Pine'daki ta.sma / ta.stdev ile aynı: kitle (population) varyansı)
# ═══════════════════════════════════════════════════════════════════════════
def roll_mean(x, n):
    out = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def roll_std(x, n):
    m = roll_mean(x, n)
    m2 = roll_mean([v * v for v in x], n)
    return [None if a is None else math.sqrt(max(b - a * a, 0.0)) for a, b in zip(m, m2)]


def getiriler(kapanis):
    """Pine lr(): önceki yoksa / geçersizse 0."""
    out = [0.0] * len(kapanis)
    for i in range(1, len(kapanis)):
        a, b = kapanis[i - 1], kapanis[i]
        out[i] = math.log(b / a) if (a is not None and b is not None and a > 0 and b > 0) else 0.0
    return out


def ayrisma(r, rm, lenB, lenA):
    """Varlık getirisi r'nin majör sepeti rm'ye göre ayrışması.
    Döner: (z serisi, β serisi, r(korelasyon) son değer)."""
    n = len(r)
    mM = roll_mean(rm, lenB)
    mR = roll_mean(r, lenB)
    mMR = roll_mean([a * b for a, b in zip(rm, r)], lenB)
    mMM = roll_mean([a * a for a in rm], lenB)
    mRR = roll_mean([a * a for a in r], lenB)
    beta = [1.0] * n
    for i in range(n):
        if mM[i] is not None:
            vr = mMM[i] - mM[i] * mM[i]
            if vr > 0:
                beta[i] = min(4.0, max(-1.0, (mMR[i] - mM[i] * mR[i]) / vr))
    resid = [r[i] - beta[i] * rm[i] for i in range(n)]
    sd = roll_std(resid, lenB)
    phi = 1.0 - 1.0 / lenA
    k = lenA / math.sqrt(2.0 * lenA - 1.0)
    z = [0.0] * n
    s = 0.0
    for i in range(n):
        s = resid[i] if i == 0 else s * phi + resid[i]
        z[i] = s / (sd[i] * k) if (sd[i] is not None and sd[i] > 0) else 0.0
    korr = None
    if n and mM[-1] is not None:
        vx = mMM[-1] - mM[-1] ** 2
        vy = mRR[-1] - mR[-1] ** 2
        if vx > 0 and vy > 0:
            korr = (mMR[-1] - mM[-1] * mR[-1]) / math.sqrt(vx * vy)
    return z, beta, korr


def model2(r, x1, x2, lenB, lenA):
    """Coin getirisi r'den majörler (x1) ve alt sepeti (x2) çok değişkenli EKK ile birlikte
    çıkarılır. Döner: z, fit, β_toplam (majörlere), β_alt, korelasyon(r, fit) son değer."""
    n = len(r)
    m1, m2, mr = roll_mean(x1, lenB), roll_mean(x2, lenB), roll_mean(r, lenB)
    m11 = roll_mean([a * a for a in x1], lenB)
    m22 = roll_mean([a * a for a in x2], lenB)
    m12 = roll_mean([a * b for a, b in zip(x1, x2)], lenB)
    m1r = roll_mean([a * b for a, b in zip(x1, r)], lenB)
    m2r = roll_mean([a * b for a, b in zip(x2, r)], lenB)
    fit, btop, balt = [0.0] * n, [1.0] * n, [None] * n
    for i in range(n):
        if m1[i] is None:
            fit[i] = x1[i]
            continue
        c11 = m11[i] - m1[i] ** 2
        c22 = m22[i] - m2[i] ** 2
        c12 = m12[i] - m1[i] * m2[i]
        c1r = m1r[i] - m1[i] * mr[i]
        c2r = m2r[i] - m2[i] * mr[i]
        det = c11 * c22 - c12 * c12
        if c11 > 0 and c22 > 0 and det > 1e-14 * c11 * c22:
            b1 = min(5.0, max(-3.0, (c22 * c1r - c12 * c2r) / det))
            b2 = min(5.0, max(-2.0, (c11 * c2r - c12 * c1r) / det))
            fit[i] = b1 * x1[i] + b2 * x2[i]
            btop[i], balt[i] = b1 + b2 * (c12 / c11), b2
        elif c11 > 0:
            b = min(4.0, max(-1.0, c1r / c11))
            fit[i], btop[i] = b * x1[i], b
        else:
            fit[i] = x1[i]
    resid = [r[i] - fit[i] for i in range(n)]
    sd = roll_std(resid, lenB)
    phi = 1.0 - 1.0 / lenA
    k = lenA / math.sqrt(2.0 * lenA - 1.0)
    z, s = [0.0] * n, 0.0
    for i in range(n):
        s = resid[i] if i == 0 else s * phi + resid[i]
        z[i] = s / (sd[i] * k) if (sd[i] is not None and sd[i] > 0) else 0.0
    korr = None
    mf, mff, mfr, mrr = roll_mean(fit, lenB)[-1], roll_mean([v * v for v in fit], lenB)[-1], \
        roll_mean([a * b for a, b in zip(fit, r)], lenB)[-1], roll_mean([v * v for v in r], lenB)[-1]
    if mf is not None:
        vx, vy = mff - mf * mf, mrr - mr[-1] ** 2
        if vx > 0 and vy > 0:
            korr = (mfr - mf * mr[-1]) / math.sqrt(vx * vy)
    return z, fit, btop, balt, korr


def piyasa_z(rm, lenB, lenA):
    """Majör sepetinin kendi birikimi / oynaklığı (piyasa yönü)."""
    sd = roll_std(rm, lenB)
    phi = 1.0 - 1.0 / lenA
    k = lenA / math.sqrt(2.0 * lenA - 1.0)
    out, s = [0.0] * len(rm), 0.0
    for i, v in enumerate(rm):
        s = s * phi + v
        out[i] = s / (sd[i] * k) if (sd[i] is not None and sd[i] > 0) else 0.0
    return out


def uclu(v, esik):
    return 1 if v >= esik else -1 if v <= -esik else 0


# ── Metinler (göstergeyle aynı) ──────────────────────────────────────────────
def ortam(m, f):
    """Altlar için piyasa durumu: (a, metin)."""
    if m == 1:
        return (1, "alt yükselişi") if f == 1 else (1, "normal yükseliş") if f == 0 else (-1, "piyasa yükseliyor ama altlar eziliyor")
    if m == -1:
        return (0, "piyasa düşüyor ama para altlara giriyor") if f == 1 else (-1, "düşüş") if f == 0 else (-1, "risk-off — altlar sert düşebilir")
    return (1, "alt rotasyonu") if f == 1 else (0, "yatay, belirgin akış yok") if f == 0 else (-1, "altlardan çıkış")


def durum_metni(a, c):
    if a == 1 and c == 1:
        return "EN GÜÇLÜ — ortam olumlu ve coin önde"
    if a == 1 and c == 0:
        return "Uyumlu — ortam olumlu, coin birlikte gidiyor"
    if a == 1 and c == -1:
        return "UYUMSUZ — ortam olumlu ama coin geride"
    if a == -1 and c == 1:
        return "Akıntıya karşı güçlü — ortam olumsuz ama coin önde"
    if a == -1 and c == 0:
        return "Uyumlu — ortam olumsuz, coin birlikte gidiyor"
    if a == -1 and c == -1:
        return "EN ZAYIF — ortam olumsuz ve coin de geride"
    if c == 1:
        return "Coin kendi gücüyle önde"
    if c == -1:
        return "Coin kendi zayıflığıyla geride"
    return "Belirgin bir durum yok"


def ton(a, c):
    """+2 en güçlü · +1 lehte · 0 nötr · −1 aleyhte · −2 en zayıf · 9 uyumsuz"""
    if a == 1 and c == -1:
        return 9
    if a == 1 and c == 1:
        return 2
    if a == -1 and c == -1:
        return -2
    if c == 1 or (a == 1 and c == 0):
        return 1
    if c == -1 or (a == -1 and c == 0):
        return -1
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Evren
# ═══════════════════════════════════════════════════════════════════════════
def evren():
    d = safe_read_json(TICKER, {}) or {}
    t24 = d.get("ticker24") or []
    fon = {p.get("symbol"): p.get("lastFundingRate") for p in (d.get("premiumIndex") or [])}
    secilen = []
    for t in t24:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in HARIC or base.endswith("STOCK"):
            continue
        try:
            qv = float(t.get("quoteVolume") or 0)
        except ValueError:
            continue
        if qv < MIN_HACIM:
            continue
        secilen.append({
            "s": s,
            "p": float(t.get("lastPrice") or 0),
            "ch24": float(t.get("priceChangePercent") or 0),
            "qv": qv,
            "fr": float(fon.get(s)) if fon.get(s) not in (None, "") else None,
        })
    secilen.sort(key=lambda x: -x["qv"])
    if MAX_COIN > 0:
        secilen = secilen[:MAX_COIN]
    return secilen, d.get("ts")


# ═══════════════════════════════════════════════════════════════════════════
# Dominans rejimi (dom.json'dan saatlik anlık görüntü biriktirerek)
# ═══════════════════════════════════════════════════════════════════════════
def _iso_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def usdc_payi():
    """CoinGecko /global → USDC'nin toplam piyasadaki payı (%). Alınamazsa None (akış yine çalışır)."""
    if not USDC_URL:
        return None
    for deneme in range(2):
        try:
            req = urllib.request.Request(USDC_URL, headers={"User-Agent": "ayrisma_export/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
            v = ((d.get("data") or {}).get("market_cap_percentage") or {}).get("usdc")
            return float(v) if v is not None else None
        except Exception as e:
            log(f"[UYARI] USDC payı alınamadı ({deneme + 1}/2): {e}")
            time.sleep(3)
    return None


def _tr(x, n=2, isaret=True):
    return (f"{x:+.{n}f}" if isaret else f"{x:.{n}f}").replace(".", ",")


def _derece(z):
    if z is None:
        return None
    a = abs(z)
    return "güçlü" if a >= 2 else "belirgin" if a >= 1 else "hafif" if a >= 0.5 else "yok denecek kadar az"


def para_akisi(grup1h, alt24):
    """Para akışı (dominans payları, yüzde puan).
    grup1h: [(bar_bitiş_sn, alt sepeti getirisi)] 1s, eskiden yeniye — OTHERS sıçrama filtresi için.
    alt24 : alt sepetinin son 24 saatlik log getirisi (çelişki notu için)."""
    import bisect
    dom = safe_read_json(DOM, {}) or {}
    items = {i.get("key"): i for i in (dom.get("items") or []) if isinstance(i, dict)}
    def deger(k):
        v = (items.get(k) or {}).get("value")
        return float(v) if isinstance(v, (int, float)) else None

    gecmis = safe_read_json(GECMIS, {"ornekler": []}) or {"ornekler": []}
    ornek = [o for o in (gecmis.get("ornekler") or []) if isinstance(o, dict) and "t" in o]
    dom_ts = _iso_ts(dom.get("updated_at") or dom.get("generated_at"))
    simdi = time.time()
    taze = dom_ts is not None and simdi - dom_ts < 2 * 3600
    usdc = None
    if taze and all(deger(k) is not None for k in DOM_ANAHTAR):
        usdc = usdc_payi()
        if not ornek or dom_ts - ornek[-1]["t"] > 40 * 60:           # saatte ~1 örnek (dom.json'un kendi zamanı)
            o = {"t": int(dom_ts), **{k: deger(k) for k in DOM_ANAHTAR}, "OTHERS": deger("OTHERS"), "TOTAL": deger("TOTAL")}
            if usdc is not None:
                o["USDC.D"] = usdc
            ornek.append(o)
            ornek = ornek[-600:]                                       # ~25 gün
            try:
                atomic_write_json(GECMIS, {"aciklama": "ayrisma_export: dom.json saatlik anlık görüntüleri (pay %, $)", "ornekler": ornek})
            except OSError as e:
                log(f"[UYARI] dominans geçmişi yazılamadı: {e}")
    ornek.sort(key=lambda o: o["t"])
    n = len(ornek)
    T = [o["t"] for o in ornek]

    # ── OTHERS sıçrama filtresi: OTHERS ($) saatlik değişiminin alt sepetinin açıklamadığı kısmı
    g_bitis = [b for b, _ in grup1h]
    g_kum = [0.0]
    for _, v in grup1h:
        g_kum.append(g_kum[-1] + v)
    def sepet(t0, t1):
        lo, hi = bisect.bisect_right(g_bitis, t0), bisect.bisect_right(g_bitis, t1)
        return g_kum[hi] - g_kum[lo] if hi > lo else None
    ciftler = {}                                   # i → (OTHERS log değişimi, sepet getirisi)
    for i in range(1, n):
        a, b = ornek[i - 1].get("OTHERS"), ornek[i].get("OTHERS")
        g = sepet(T[i - 1], T[i])
        if a and b and a > 0 and b > 0 and g is not None and T[i] - T[i - 1] <= 3 * 3600:
            ciftler[i] = (math.log(b / a), g)
    sic = set()
    if len(ciftler) >= 24:
        xs = [g for _, g in ciftler.values()]
        ys = [o for o, _ in ciftler.values()]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        beta = min(2.0, max(0.3, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx)) if vx > 0 else 1.0
        art = {i: o - beta * g for i, (o, g) in ciftler.items()}
        mad = sorted(abs(v) for v in art.values())[len(art) // 2]
        esik = max(DOM_SIC_K * 1.4826 * mad, DOM_SIC_MIN)
        sic = {i for i, v in art.items() if abs(v) > esik}

    def adim(k, i):
        """i. örnekteki puan adımı (OTHERS.D için sıçrama saatinde 0)."""
        a, b = ornek[i - 1].get(k), ornek[i].get(k)
        if a is None or b is None:
            return None
        return 0.0 if (k == "OTHERS.D" and i in sic) else b - a

    def deg24(k, i):
        """i. örnekte son ~24 saatlik puan değişimi (adımların toplamı) — 23–25,5 saat aralığı şart."""
        hedef = T[i] - AKIS_PENCERE_SA * 3600
        j = bisect.bisect_left(T, hedef - 90 * 60, 0, i)
        en_iyi = None
        while j < i and T[j] <= hedef + 60 * 60:
            if en_iyi is None or abs(T[j] - hedef) < abs(T[en_iyi] - hedef):
                en_iyi = j
            j += 1
        if en_iyi is None:
            return None
        top = 0.0
        for m in range(en_iyi + 1, i + 1):
            a = adim(k, m)
            if a is None:
                return None
            top += a
        return top

    sonuc = {"durum": "ısınıyor", "f": 0, "akis": "", "pencere_sa": AKIS_PENCERE_SA, "esik": AKIS_ESIK,
             "ornek": n, "sigma_n": 0, "gerekli": MIN_AKIS_ORNEK, "dom_taze": taze, "dom_regime": dom.get("regime"),
             "total": deger("TOTAL"), "usdc": False, "sicrama_24s": 0, "kaynak": "", "celiski": False,
             "alt24": yuvarla((math.exp(alt24) - 1) * 100.0, 2) if alt24 is not None else None}
    if not taze or n == 0:
        sonuc.update(durum="veri yok", akis="dominans verisi güncel değil (dom.json)")
        return sonuc

    son = n - 1
    guncel = abs(T[son] - dom_ts) < 60                  # son örnek şu anki dom.json mı?
    pp = {k: (deg24(k, son) if guncel else None) for k in DOM_ANAHTAR + ["USDC.D"]}
    if pp["OTHERS.D"] is not None:
        sonuc["kaynak"] = "geçmiş (24 saat, sıçrama filtreli)"
        sonuc["sicrama_24s"] = sum(1 for i in sic if T[i] > T[son] - AKIS_PENCERE_SA * 3600)
    else:                                               # geçmiş 24 saati doldurmadı → dom.json'daki 24s değişim (puan)
        pp = {k: (items.get(k) or {}).get("chg24") for k in DOM_ANAHTAR}
        pp["USDC.D"] = None
        sonuc["kaynak"] = "dom.json chg24 (geçmiş birikiyor, sıçrama filtresiz)"
    if pp["OTHERS.D"] is None:
        sonuc.update(durum="veri yok", akis="OTHERS.D değişimi yok")
        return sonuc

    # ── σ: son 1 haftanın saatlik 24s-değişimleri (göstergede ta.stdev(ppOth, lenB))
    seri = [d for d in (deg24("OTHERS.D", i) for i in range(max(1, n - AKIS_SIGMA_N), n)) if d is not None]
    sonuc["sigma_n"] = len(seri)
    z = None
    if len(seri) >= MIN_AKIS_ORNEK and sonuc["kaynak"].startswith("geçmiş"):
        ort = sum(seri) / len(seri)
        sd = math.sqrt(sum((x - ort) ** 2 for x in seri) / len(seri))
        if sd > 0:
            z = pp["OTHERS.D"] / sd
            sonuc["sigma"] = yuvarla(sd, 3)

    toplam = deger("TOTAL")
    usd = lambda x: None if (x is None or not toplam) else x / 100.0 * toplam
    stb = pp["USDT.D"] + (pp["USDC.D"] or 0.0) if pp["USDT.D"] is not None else None
    sonuc["usdc"] = pp["USDC.D"] is not None
    parcalar = [pp["OTHERS.D"], stb, pp["ETH.D"], pp["BTC.D"]]
    dig = None if any(x is None for x in parcalar) else -sum(parcalar)
    kal = [("stabil", "Nakit (USDT" + ("+USDC" if sonuc["usdc"] else "") + ")", stb,
            (deger("USDT.D") or 0) + (usdc or 0), "nakde", "nakitten"),
           ("ETH.D", "ETH.D", pp["ETH.D"], deger("ETH.D"), "ETH'ye", "ETH'den"),
           ("BTC.D", "BTC.D", pp["BTC.D"], deger("BTC.D"), "BTC'ye", "BTC'den"),
           ("diger", "Diğer ilk-10" + ("" if sonuc["usdc"] else " (USDC dahil)"), dig, None,
            "diğer ilk-10'a", "diğer ilk-10'dan")]
    oth = pp["OTHERS.D"]
    sonuc["oth"] = {"deger": deger("OTHERS.D"), "pp": yuvarla(oth, 3), "z": yuvarla(z, 2), "derece": _derece(z),
                    "usd": yuvarla(usd(oth), 0)}
    sonuc["kalemler"] = [{"k": k, "ad": ad, "pp": yuvarla(x, 3), "usd": yuvarla(usd(x), 0), "deger": yuvarla(v, 2)}
                         for k, ad, x, v, _, _ in kal]
    # Nereye (OTHERS.D düşüyorsa) / nereden (artıyorsa): ters işaretli kalemler, büyükten küçüğe, en fazla 3
    cikis = oth < 0
    ters = [(x, g if cikis else c) for _, _, x, _, g, c in kal if x is not None and (x > 0.005 if cikis else x < -0.005)]
    ters.sort(key=lambda t: -abs(t[0]))
    sonuc["nereye"] = " · ".join(f"{ad} {_tr(x)}" for x, ad in ters[:3])
    sonuc["yon"] = "nereye" if cikis else "nereden"

    if z is None:
        sonuc.update(durum="ısınıyor", f=0, akis="akış ısınıyor (ölçek birikiyor, ~48 saat)")
    else:
        f = uclu(z, AKIS_ESIK)
        sonuc["f"] = f
        sonuc["durum"] = "giriş" if f == 1 else "çıkış" if f == -1 else "nötr"
        sonuc["akis"] = ("para altlara giriyor" if f == 1 else "para altlardan çıkıyor" if f == -1 else "belirgin akış yok") + f" ({_derece(z)})"
        sonuc["celiski"] = f == -1 and (alt24 or 0) > 0
    return sonuc


# ═══════════════════════════════════════════════════════════════════════════
# Bir zaman dilimini hesapla (akış sonradan eklenir)
# ═══════════════════════════════════════════════════════════════════════════
def zaman_dilimi(tf, coins):
    c = TF[tf]
    lenB, lenA = c["lenB"], c["lenA"]
    min_bar = c.get("min_bar", lenB + 5 * lenA)
    isinma = lenB + 3 * lenA

    # Piyasa: BTC, ETH (zorunlu) + majör sepeti (eksik olan majör o barda ortalamaya girmez)
    btc, eth = dict(kline("BTCUSDT", tf)), dict(kline("ETHUSDT", tf))
    ortak = sorted(set(btc) & set(eth))
    if len(ortak) < min_bar:
        raise RuntimeError(f"{tf}: BTC/ETH verisi yetersiz ({len(ortak)} bar)")
    rB = getiriler([btc[t] for t in ortak])
    rE = getiriler([eth[t] for t in ortak])
    mj_ret = []
    for s in MAJORLER:
        try:
            d = dict(kline(s, tf))
            kap = [d.get(t) for t in ortak]
            gr = getiriler(kap)
            mj_ret.append([None if (i == 0 or kap[i] is None or kap[i - 1] is None) else gr[i] for i in range(len(ortak))])
        except Exception as e:
            log(f"[UYARI] {s} {tf} alınamadı: {e}")
    rMj = []
    for i in range(len(ortak)):
        vals = [m[i] for m in mj_ret if m[i] is not None]
        rMj.append(sum(vals) / len(vals) if vals else 0.0)
    W = PIYASA_AGIRLIK
    rm = [W["BTC"] * a + W["ETH"] * b + W["MAJ"] * c for a, b, c in zip(rB, rE, rMj)]
    mkZ = piyasa_z(rm, lenB, lenA)
    ayak = {k: piyasa_z(v, lenB, lenA)[-1] for k, v in (("btc", rB), ("eth", rE), ("maj", rMj))}
    idx = {t: i for i, t in enumerate(ortak)}

    def al(ci):
        try:
            return ci["s"], kline(ci["s"], tf)
        except Exception as e:           # tek coin hatası taramayı durdurmaz
            return ci["s"], e

    with ThreadPoolExecutor(max_workers=PARALEL) as ex:
        sonuclar = dict(ex.map(al, coins))

    seriler, hatali, kisa = {}, 0, 0
    for ci in coins:
        v = sonuclar.get(ci["s"])
        if isinstance(v, Exception) or v is None:
            hatali += 1
            continue
        d = {t: p for t, p in v if t in idx}
        if not d:
            kisa += 1
            continue
        bas = idx[min(d)]
        if len(ortak) - bas < min_bar:
            kisa += 1
            continue
        seriler[ci["s"]] = (bas, getiriler([d.get(t) for t in ortak[bas:]]))

    # Alt sepeti: her barda taranan coinlerin MEDYAN getirisi (eşit ağırlıklı OTHERS temsili)
    grup = [0.0] * len(ortak)
    for i in range(len(ortak)):
        vals = sorted(r[i - bas] for bas, r in seriler.values() if i > bas)
        if vals:
            h = len(vals) // 2
            grup[i] = vals[h] if len(vals) % 2 else (vals[h - 1] + vals[h]) / 2
    zGrp, _, _ = ayrisma(grup, rm, lenB, lenA)

    m_son = uclu(mkZ[-1], PIYASA_ESIK)
    m_sure = 0
    for j in range(1, len(mkZ) + 1):
        if uclu(mkZ[-j], PIYASA_ESIK) != m_son:
            break
        m_sure += 1

    satirlar = []
    karne = {1: [], 0: [], -1: [], "hepsi": []}   # coin durumu → [(coin getirisi %, sepete göre fazla getiri %)]
    for ci in coins:
        if ci["s"] not in seriler:
            continue
        bas, r = seriler[ci["s"]]
        x1, x2 = rm[bas:], grup[bas:]
        z, fit, btop, balt, korr = model2(r, x1, x2, lenB, lenA)
        cz = [uclu(v, UYUM_BANT) for v in z]
        cc = cz[-1]
        sure = 0
        for j in range(1, min(len(z), 300) + 1):
            if cz[-j] != cc:
                break
            sure += 1
        # Karne: durum BAŞLADIĞINDA sonraki lenA barda ne oldu? (yalnız değerlendirme; sinyal geleceğe bakmaz)
        # "hepsi" = her bar (taban): durumların tabandan farkı asıl bilgidir.
        pr, pg = [0.0], [0.0]
        for a_, b_ in zip(r, x2):
            pr.append(pr[-1] + a_)
            pg.append(pg[-1] + b_)
        for i in range(max(isinma, 1), len(r) - lenA):
            rc_ = pr[i + 1 + lenA] - pr[i + 1]
            rg_ = pg[i + 1 + lenA] - pg[i + 1]
            kayit = ((math.exp(rc_) - 1) * 100.0, (math.exp(rc_) - math.exp(rg_)) * 100.0)
            karne["hepsi"].append(kayit)
            if cz[i] != cz[i - 1]:
                karne[cz[i]].append(kayit)
        satirlar.append({
            **ci,
            "z": yuvarla(z[-1], 2), "z1": yuvarla(z[-2], 2),
            "dz6": yuvarla(z[-1] - z[-7], 2) if len(z) > 7 else None,
            "c": cc, "sure": sure,
            "beta": yuvarla(btop[-1], 2), "beta_alt": yuvarla(balt[-1], 2), "r": yuvarla(korr, 2),
            "bek": yuvarla((math.exp(sum(fit[-lenA:])) - 1) * 100.0, 1),
            "ger": yuvarla((math.exp(sum(r[-lenA:])) - 1) * 100.0, 1),
            "spark": [yuvarla(x, 2) for x in z[-SPARK_N:]],
        })

    def ozet(liste):
        n = len(liste)
        if n < MIN_KARNE:
            return {"n": n, "yetersiz": True}
        fz = sorted(b for _, b in liste)
        return {"n": n, "ort": yuvarla(sum(a for a, _ in liste) / n, 2),
                "fazla": yuvarla(sum(b for _, b in liste) / n, 2),
                "fazla_med": yuvarla(fz[n // 2] if n % 2 else (fz[n // 2 - 1] + fz[n // 2]) / 2, 2),
                "fazla_poz": yuvarla(100.0 * sum(1 for _, b in liste if b > 0) / n, 0),
                "poz": yuvarla(100.0 * sum(1 for a, _ in liste if a > 0) / n, 0)}

    satirlar.sort(key=lambda x: -(x["z"] or 0))
    return {
        "ad": c["ad"], "lenB": lenB, "lenA": lenA,
        "son_mum": datetime.fromtimestamp(ortak[-1] / 1000, tz=timezone.utc).isoformat(),
        "gun": round(len(ortak) * c["sn"] / 86400),
        "piyasa": {"z": yuvarla(mkZ[-1], 2), "m": m_son, "sure": m_sure,
                   "pct": yuvarla((math.exp(sum(rm[-lenA:])) - 1.0) * 100.0, 2),
                   "yon": "↑" if m_son > 0 else "↓" if m_son < 0 else "↔",
                   "ayak": {k: {"z": yuvarla(v, 2), "m": uclu(v, PIYASA_ESIK),
                                "pct": yuvarla((math.exp(sum(r_[-lenA:])) - 1.0) * 100.0, 2)}
                            for (k, v), r_ in zip(ayak.items(), (rB, rE, rMj))},
                   "majorler": [s.replace("USDT", "") for s in MAJORLER], "majorler_n": len(mj_ret)},
        "grup": {"z": yuvarla(zGrp[-1], 2), "pct": yuvarla((math.exp(sum(grup[-lenA:])) - 1.0) * 100.0, 2), "n": len(seriler)},
        "grup_getiri": [[ortak[i] // 1000, round(grup[i], 6)] for i in range(len(ortak))],
        "piyasa_getiri": [[ortak[i] // 1000, round(rm[i], 6)] for i in range(len(ortak))],
        "karne": {"H": lenA, "onde": ozet(karne[1]), "uyumlu": ozet(karne[0]), "geride": ozet(karne[-1]),
                  "hepsi": ozet(karne["hepsi"])},
        "_grup1h": [((ortak[i] + c["sn"] * 1000) // 1000, grup[i]) for i in range(len(ortak))] if tf == "1h" else None,
        "_alt24": sum(grup[-24:]) if tf == "1h" else None,
        "atlanan": {"hata": hatali, "kisa_gecmis": kisa},
        "coins": satirlar,
    }


def main():
    t0 = time.time()
    coins, ticker_ts = evren()
    if not coins:
        log("[HATA] ticker_ws.json okunamadı ya da boş → eski ayrisma.json korunuyor")
        return
    log(f"Evren: {len(coins)} coin (24s hacim ≥ {MIN_HACIM:,.0f} USDT, ilk 10 ve hisse/emtia hariç)")

    sonuc_tf = {}
    for tf in TF:
        try:
            sonuc_tf[tf] = zaman_dilimi(tf, coins)
            a = sonuc_tf[tf]["atlanan"]
            log(f"{tf}: {len(sonuc_tf[tf]['coins'])} coin (hata {a['hata']}, kısa geçmiş {a['kisa_gecmis']}) "
                f"· majörler {sonuc_tf[tf]['piyasa']['yon']} · karne {sonuc_tf[tf]['karne']}")
        except Exception as e:
            log(f"[HATA] {tf} hesaplanamadı: {e}")

    if not sonuc_tf:
        log("[HATA] hiçbir zaman dilimi hesaplanamadı → eski ayrisma.json korunuyor")
        return

    h1 = sonuc_tf.get("1h", {})
    rejim = para_akisi(h1.get("_grup1h") or [], h1.get("_alt24"))
    f = rejim["f"]
    for v in sonuc_tf.values():
        v.pop("_grup1h", None)
        v.pop("_alt24", None)
        a, metin = ortam(v["piyasa"]["m"], f)
        v["ortam"] = {"a": a, "metin": metin}
        for row in v["coins"]:
            row["ton"] = ton(a, row["c"])
            row["durum"] = durum_metni(a, row["c"])

    simdi = datetime.now(timezone.utc)
    atomic_write_json(CIKTI, {
        "generated_at": simdi.isoformat(),
        "updated_human": (simdi + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M") + " (TR)",
        "sure_sn": round(time.time() - t0, 1),
        "kaynak": {"mum": "Bybit USDT perp (kapanmış mumlar)", "evren": "ticker_ws.json",
                   "dominans": "dom.json (saatlik geçmiş: ayrisma_dom_gecmis.json) + CoinGecko /global (USDC payı)", "ticker_ts": ticker_ts},
        "ayarlar": {"piyasa_agirlik": PIYASA_AGIRLIK, "majorler": MAJORLER, "min_hacim": MIN_HACIM, "haric": sorted(HARIC),
                    "uyum_bant": UYUM_BANT, "z_esik": Z_ESIK, "piyasa_esik": PIYASA_ESIK,
                    "akis_pencere_sa": AKIS_PENCERE_SA, "akis_esik": AKIS_ESIK, "min_karne": MIN_KARNE},
        "rejim": rejim,
        "tf": sonuc_tf,
        "uyari": UYARI,
    })
    log(f"[OK] {CIKTI} yazıldı · {time.time() - t0:.1f} sn · akış: {rejim['akis']}"
        + (f" · OTHERS.D {rejim['oth']['pp']:+.2f} puan" if rejim.get("oth") else ""))


if __name__ == "__main__":
    main()

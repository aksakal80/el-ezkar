#!/usr/bin/env python3
"""
ayrisma_export.py — AYRIŞMA · Piyasa Uyumu tarayıcısı
(TradingView "AYRIŞMA + MSB-OB v2.2" göstergesinin sunucu karşılığı)

Görevi : OTHERS evrenindeki (ilk 10 hariç) Bybit USDT perp coinleri için 1s ve 4s'te
         "coinin fiyatı, majör piyasanın (BTC+ETH) durumuyla uyumlu mu?" sorusunu
         hesaplar → /var/www/veri/ayrisma.json  (ayrisma_panel.html bunu okur)
Timer  : ayrisma_export.timer — her saat :02:30'da tek geçiş (oneshot)
Girdi  : /var/www/veri/ticker_ws.json (ws_ticker üretir, SALT-OKUR) → coin evreni, 24s veri
         /var/www/veri/dom.json       (dom_export üretir, SALT-OKUR) → dominanslar
Ağ     : yalnız Bybit public kline (api.bybit.com/v5/market/kline). Binance fapi'ye
         GİTMEZ → .fapi_ban kapısı gerekmez. Token-bucket throttle + 429/403 geri çekilme.
Durum  : /root/projelerim/ayrisma_dom_gecmis.json — saatlik dominans anlık görüntüleri
         (dom.json yalnız anlık değer verdiği için σ hesabına geçmiş burada biriktirilir)
Bağımlılık: yalnız Python standart kütüphanesi (pip kurulumu gerekmez).
Mevcut botlara/dosyalara dokunmaz. Yatırım tavsiyesi değildir.

HESAP (göstergeyle aynı formüller)
  r        = ln(kapanış / önceki kapanış)
  majör    = 0,70 × BTC + 0,30 × ETH getirisi
  β        = kov(coin, majör) / var(majör)   (lenB bar, −1…4 arası)
  artık    = coin − β × majör
  birikim  = önceki × φ + artık,  φ = 1 − 1/lenA   (sönümlü, yankısız)
  z (σ)    = birikim / (σ_artık × lenA / √(2·lenA − 1))
  piyasa   = majör getirisinin aynı yöntemle birikimi / oynaklığı → ≥ +0,5 ↑ · ≤ −0,5 ↓ · arası ↔
  alt grubu= taranan coinlerin bar bar MEDYAN getirisi (eşit ağırlıklı; ilk-10 sıçraması yok)
             → majörlere göre aynı z hesabı
  durum    = piyasa (↑ ↔ ↓) × coin (z ≥ +1 güçlü · ≤ −1 zayıf · arası uyumlu) → 9 durum
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
MAJOR = {"BTCUSDT": 0.70, "ETHUSDT": 0.30}          # majör sepeti = piyasa yönü
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
    "1h": {"iv": "60",  "sn": 3600,  "lenB": 168, "lenA": 24, "limit": 400, "ad": "1 saat"},
    "4h": {"iv": "240", "sn": 14400, "lenB": 120, "lenA": 30, "limit": 400, "ad": "4 saat"},
}
UYUM_BANT   = 1.0     # |z| < 1 → uyumlu (gri bant)
Z_ESIK      = 2.0     # |z| ≥ 2 → belirgin ayrışma
PIYASA_ESIK = 0.5     # piyasa yönü eşiği (σ)
SPARK_N     = 48      # tabloda mini grafik için son kaç z değeri

# Dominans rejimi (göstergedeki gibi: her dominansın 12 saatlik değişimi kendi oynaklığına bölünür)
DOM_PENCERE_SA = 12
DOM_OY_ESIK    = 1.0
DOM_SIC_K      = 6.0          # OTHERS.D'de ilk-10 giriş/çıkış sıçraması filtresi (sağlam σ katı)
MIN_DOM_ORNEK  = 24           # bu kadar 12s-değişim örneği birikmeden rejim oy vermez ("ısınıyor")
DOM_OY = [                    # (anahtar, yön, ağırlık, açıklama)
    ("USDT.D",   -1, 1, "Stabil payı ▲ = risk-off"),
    ("BTC.D",    -1, 1, "BTC.D ▲ = altlar için olumsuz"),
    ("OTHERS.D", +1, 2, "OTHERS.D ▲ = küçük altlar için olumlu (×2)"),
]
DOM_BILGI = ["ETH.D"]         # yalnız bilgi, oy vermez

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


# ── 9 durum (göstergeyle aynı metinler) ──────────────────────────────────────
def durum_metni(m, c):
    if m == 1 and c == 1:
        return "Coin piyasayı önden çekiyor — göreli güçlü"
    if m == 1 and c == 0:
        return "Uyumlu yükseliş — coin piyasayı takip ediyor"
    if m == 1 and c == -1:
        return "UYUMSUZ — piyasa yükselirken coin geride"
    if m == -1 and c == 1:
        return "Coin düşüşe direniyor — piyasa dönünce ilk toparlananlardan olabilir"
    if m == -1 and c == 0:
        return "Uyumlu düşüş — coin piyasayla birlikte düşüyor"
    if m == -1 and c == -1:
        return "Coin piyasadan da zayıf — en olumsuz durum"
    if c == 1:
        return "Piyasa yatay — coin kendi gücüyle yükseliyor"
    if c == -1:
        return "Piyasa yatay — coin kendi zayıflığıyla geriliyor"
    return "Piyasa yatay, coin uyumlu — belirgin bir durum yok"


def ton(m, c):
    """+2 güçlü lehte · +1 lehte · 0 nötr · −1 aleyhte · −2 güçlü aleyhte · 9 uyumsuz"""
    if m == 1 and c == -1:
        return 9
    if m == 1 and c == 1:
        return 2
    if m == -1 and c == -1:
        return -2
    if c == 1 or (m == 1 and c == 0):
        return 1
    if c == -1 or (m == -1 and c == 0):
        return -1
    return 0


def grup_eki(c, g):
    if c == 1 and g == 1:
        return "bütün altlar güçlü"
    if c == 1:
        return "yalnız bu coin güçlü"
    if c == -1 and g == -1:
        return "bütün altlar zayıf"
    if c == -1:
        return "yalnız bu coin zayıf"
    if g == 1:
        return "altlar önde, coin henüz katılmadı"
    if g == -1:
        return "altlar geride, coin dayanıyor"
    return ""


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


def dominans_rejimi(alt_12s_getiri):
    dom = safe_read_json(DOM, {}) or {}
    items = {i.get("key"): i for i in (dom.get("items") or []) if isinstance(i, dict)}
    anahtarlar = [k for k, _, _, _ in DOM_OY] + DOM_BILGI
    gecmis = safe_read_json(GECMIS, {"ornekler": []}) or {"ornekler": []}
    ornek = gecmis.get("ornekler") or []

    dom_ts = _iso_ts(dom.get("updated_at") or dom.get("generated_at"))
    simdi = time.time()
    taze = dom_ts is not None and simdi - dom_ts < 2 * 3600
    if taze and all(items.get(k, {}).get("value") is not None for k in anahtarlar):
        if not ornek or simdi - ornek[-1]["t"] > 40 * 60:           # saatte ~1 örnek
            ornek.append({"t": int(simdi), **{k: float(items[k]["value"]) for k in anahtarlar}})
            ornek = ornek[-500:]                                       # ~3 hafta
            try:
                atomic_write_json(GECMIS, {"aciklama": "ayrisma_export: dom.json saatlik anlık görüntüleri", "ornekler": ornek})
            except OSError as e:
                log(f"[UYARI] dominans geçmişi yazılamadı: {e}")

    sonuc = {"durum": "ısınıyor", "puan": 0, "n": sum(w for _, _, w, _ in DOM_OY), "ornek": len(ornek),
             "gerekli": MIN_DOM_ORNEK + DOM_PENCERE_SA, "oylar": [], "bilgi": [],
             "dom_regime": dom.get("regime"), "dom_taze": taze}

    def seri_z(k, sicrama_filtresi):
        """12 saatlik log değişimi (adım adım toplanır) / kendi oynaklığı."""
        v = [(o["t"], o.get(k)) for o in ornek if o.get(k)]
        if len(v) < 3:
            return None, None, None
        adim = [(v[i][0], math.log(v[i][1] / v[i - 1][1])) for i in range(1, len(v))]
        if sicrama_filtresi and len(adim) >= 24:
            son = [abs(a) for _, a in adim[-168:]]
            mad = sorted(son)[len(son) // 2]
            if mad > 0:
                adim = [(t, 0.0 if abs(a) > DOM_SIC_K * 1.4826 * mad else a) for t, a in adim]
        degisim = []
        for i in range(len(adim)):
            t = adim[i][0]
            degisim.append(sum(a for tt, a in adim[: i + 1] if tt > t - DOM_PENCERE_SA * 3600))
        # yalnız pencerenin tamamı dolduktan sonraki değişimler σ'ya girer
        ilk_t = v[0][0] + DOM_PENCERE_SA * 3600
        tam = [d for (t, _), d in zip(adim, degisim) if t >= ilk_t]
        if len(tam) < MIN_DOM_ORNEK:
            return None, degisim[-1] * 100.0 if degisim else None, v[-1][1]
        son = tam[-168:]
        ort = sum(son) / len(son)
        sd = math.sqrt(sum((x - ort) ** 2 for x in son) / len(son))
        return (tam[-1] / sd if sd > 0 else 0.0), tam[-1] * 100.0, v[-1][1]

    puan, isinan = 0, False
    for k, yon, w, acik in DOM_OY:
        z, deg, deger = seri_z(k, k == "OTHERS.D")
        if z is None:
            isinan = True
        oy = 0 if z is None else (w * uclu(z * yon, DOM_OY_ESIK))
        puan += oy
        sonuc["oylar"].append({"k": k, "aciklama": acik, "deger": yuvarla(deger, 3),
                               "degisim12": yuvarla(deg, 2), "z": yuvarla(z, 2), "oy": oy, "agirlik": w,
                               "chg24": items.get(k, {}).get("chg24")})
    for k in DOM_BILGI:
        z, deg, deger = seri_z(k, False)
        sonuc["bilgi"].append({"k": k, "deger": yuvarla(deger, 3), "degisim12": yuvarla(deg, 2),
                               "z": yuvarla(z, 2), "chg24": items.get(k, {}).get("chg24")})
    sonuc["puan"] = puan
    if isinan:
        sonuc["durum"] = "ısınıyor"
    else:
        gerek = max(1, math.ceil(sonuc["n"] / 2))
        if puan >= gerek:
            sonuc["durum"] = "risk-on"
        elif puan <= -gerek:
            sonuc["durum"] = "çelişki" if (alt_12s_getiri or 0) > 0 else "risk-off"
        else:
            sonuc["durum"] = "nötr"
    return sonuc


# ═══════════════════════════════════════════════════════════════════════════
# Bir zaman dilimini hesapla
# ═══════════════════════════════════════════════════════════════════════════
def zaman_dilimi(tf, coins):
    c = TF[tf]
    lenB, lenA = c["lenB"], c["lenA"]
    min_bar = lenB + 5 * lenA

    # Majörler — bunlar olmadan hiçbir şey hesaplanamaz
    maj = {s: kline(s, tf) for s in MAJOR}
    ortak = sorted(set.intersection(*[set(t for t, _ in v) for v in maj.values()]))
    if len(ortak) < min_bar:
        raise RuntimeError(f"{tf}: majör verisi yetersiz ({len(ortak)} bar)")
    kap = {s: dict(v) for s, v in maj.items()}
    rm = [0.0] * len(ortak)
    for s, w in MAJOR.items():
        g = getiriler([kap[s][t] for t in ortak])
        rm = [a + w * b for a, b in zip(rm, g)]
    mkZ = piyasa_z(rm, lenB, lenA)
    idx = {t: i for i, t in enumerate(ortak)}

    # Coin mumları (paralel, ortak throttle)
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
        kapanis = [d.get(t) for t in ortak[bas:]]
        seriler[ci["s"]] = (bas, getiriler(kapanis))

    # Alt grubu: her barda taranan coinlerin MEDYAN getirisi
    grup = [0.0] * len(ortak)
    for i in range(len(ortak)):
        vals = [r[i - bas] for bas, r in seriler.values() if i > bas]
        if vals:
            vals.sort()
            m = len(vals) // 2
            grup[i] = vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2
    zGrp, _, _ = ayrisma(grup, rm, lenB, lenA)

    m_son = uclu(mkZ[-1], PIYASA_ESIK)
    m_sure = 0
    for j in range(1, min(len(mkZ), 500) + 1):
        if uclu(mkZ[-j], PIYASA_ESIK) != m_son:
            break
        m_sure += 1
    g_son = uclu(zGrp[-1], UYUM_BANT)
    satirlar = []
    for ci in coins:
        if ci["s"] not in seriler:
            continue
        bas, r = seriler[ci["s"]]
        z, beta, korr = ayrisma(r, rm[bas:], lenB, lenA)
        cc = uclu(z[-1], UYUM_BANT)
        # coin kaç bardır aynı ayrışma bölgesinde (güçlü / uyumlu / zayıf)?
        sure = 0
        for j in range(1, min(len(z), 200) + 1):
            if uclu(z[-j], UYUM_BANT) != cc:
                break
            sure += 1
        satirlar.append({
            **ci,
            "z": yuvarla(z[-1], 2),
            "z1": yuvarla(z[-2], 2),
            "dz6": yuvarla(z[-1] - z[-7], 2) if len(z) > 7 else None,
            "c": cc,
            "ton": ton(m_son, cc),
            "durum": durum_metni(m_son, cc),
            "ek": grup_eki(cc, g_son),
            "sure": sure,
            "beta": yuvarla(beta[-1], 2),
            "r": yuvarla(korr, 2),
            "spark": [yuvarla(x, 2) for x in z[-SPARK_N:]],
        })

    satirlar.sort(key=lambda x: -(x["z"] or 0))
    pct = (math.exp(sum(rm[-lenA:])) - 1.0) * 100.0
    grup_pct = (math.exp(sum(grup[-lenA:])) - 1.0) * 100.0
    return {
        "ad": c["ad"], "lenB": lenB, "lenA": lenA,
        "son_mum": datetime.fromtimestamp(ortak[-1] / 1000, tz=timezone.utc).isoformat(),
        "piyasa": {"z": yuvarla(mkZ[-1], 2), "m": m_son, "pct": yuvarla(pct, 2),
                   "yon": "↑" if m_son > 0 else "↓" if m_son < 0 else "↔", "sure": m_sure},
        "grup": {"z": yuvarla(zGrp[-1], 2), "g": g_son, "pct": yuvarla(grup_pct, 2), "n": len(seriler)},
        "grup_seri": [[ortak[i] // 1000, yuvarla(zGrp[i], 2)] for i in range(max(0, len(ortak) - 300), len(ortak))],
        "grup_12s": sum(grup[-12:]) if tf == "1h" else None,
        "atlanan": {"hata": hatali, "kisa_gecmis": kisa},
        "coins": satirlar,
    }


def main():
    t0 = time.time()
    coins, ticker_ts = evren()
    if not coins:
        log("[HATA] ticker_ws.json okunamadı ya da boş → eski ayrisma.json korunuyor")
        return
    log(f"Evren: {len(coins)} coin (24s hacim ≥ {MIN_HACIM:,.0f} USDT, ilk 10 hariç)")

    sonuc_tf = {}
    for tf in TF:
        try:
            sonuc_tf[tf] = zaman_dilimi(tf, coins)
            a = sonuc_tf[tf]["atlanan"]
            log(f"{tf}: {len(sonuc_tf[tf]['coins'])} coin hesaplandı "
                f"(hata {a['hata']}, kısa geçmiş {a['kisa_gecmis']}) · piyasa {sonuc_tf[tf]['piyasa']}")
        except Exception as e:
            log(f"[HATA] {tf} hesaplanamadı: {e}")

    if not sonuc_tf:
        log("[HATA] hiçbir zaman dilimi hesaplanamadı → eski ayrisma.json korunuyor")
        return

    alt12 = sonuc_tf.get("1h", {}).get("grup_12s")
    rejim = dominans_rejimi(alt12)
    for v in sonuc_tf.values():
        v.pop("grup_12s", None)

    simdi = datetime.now(timezone.utc)
    atomic_write_json(CIKTI, {
        "generated_at": simdi.isoformat(),
        "updated_human": (simdi + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M") + " (TR)",
        "sure_sn": round(time.time() - t0, 1),
        "kaynak": {"mum": "Bybit USDT perp (kapanmış mumlar)", "evren": "ticker_ws.json",
                   "dominans": "dom.json (saatlik geçmiş: ayrisma_dom_gecmis.json)", "ticker_ts": ticker_ts},
        "ayarlar": {"majorler": MAJOR, "min_hacim": MIN_HACIM, "haric": sorted(HARIC),
                    "uyum_bant": UYUM_BANT, "z_esik": Z_ESIK, "piyasa_esik": PIYASA_ESIK,
                    "dom_pencere_sa": DOM_PENCERE_SA, "dom_oy_esik": DOM_OY_ESIK},
        "rejim": rejim,
        "tf": sonuc_tf,
        "uyari": UYARI,
    })
    log(f"[OK] {CIKTI} yazıldı · {time.time() - t0:.1f} sn · rejim: {rejim['durum']}")


if __name__ == "__main__":
    main()

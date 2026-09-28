#!/usr/bin/env python3
"""
donus_cekirdek.py -- Dönüş Radarı hesap çekirdeği (saf stdlib, ağ yok)

Zirve ve dip AYNI kodla bulunur: dip aranırken fiyat serisi aynalanır
(h' = -l, l' = -h, c' = -c, RSI' = 100 - RSI, taker alış oranı' = 1 - oran),
sonra "zirve" mantığı çalışır. Böylece iki yön birebir simetriktir.

Backtest (donus_karne.py) ve canlı tarama (donus_radar.py) aynı
`olaylari_bul()` fonksiyonunu kullanır; canlıda görülen sinyal ile
karnedeki sinyal aynı kuraldan doğar.

Aşamalar
  IZLE  : son K mum içinde, önceki W mumun en yükseği olan bir tepe var,
          tetik seviyesi henüz kırılmadı (dönüş teyitsiz).
  TETIK : kapanış, tepeye çıkan son itkinin dibinin (tetik) altına indi.
  Sonuç : 'hedef' (1,5R düşüş stoptan önce geldi) / 'stop' (tepe aşıldı)
          / 'sure' (ufuk doldu) / 'acik' (henüz sonuçlanmadı).
"""
import bisect
import math

# ---------------------------------------------------------------- parametreler
# Eşikler n yeterli olana kadar KİLİTLİ; değişiklik karne ile gerekçelendirilir.
PARAM = {
    "K": 6,             # tepe en fazla kaç mum önce olabilir (tetik de bu pencerede gelmeli)
    "W": 20,            # tepe, önceki W mumun en yükseği olmalı (koşu penceresi)
    "SW": 40,           # önceki salınım tepesi arama penceresi (uyumsuzluk / süpürme)
    "PIVOT": 3,         # salınım tepesi gücü (solunda ve sağında 3 mum)
    "ISINMA": 60,       # indikatörler oturana kadar atlanan mum sayısı
    "YUZDELIK_MIN": 80, # kendi geçmişine göre yüzdelik için en az örnek
    "HEDEF_R": 1.5,     # hedef = giriş - 1,5 x risk
    "STOP_ATR": 0.1,    # stop = tepe + 0,1 ATR
    "MALIYET": 0.0012,  # gidiş-dönüş komisyon + kayma (fiyatın oranı)
}
UFUK = {"1h": 24, "4h": 18, "1d": 10}          # sonuç ölçüm ufku (mum)

# TUTMA tablosu: tetik anında fiyat tepeden kaç ATR uzaklaşmışsa, tepe ufuk
# boyunca aşılmadan kaldı mı (%)? Testte dönüşün tutmasını belirleyen tek
# tutarlı etken bu mesafeydi. Satır: [alt, üst, n, tutma %, ort. net R].
# Varsayılan değerler 170 coinlik örneklemden; gece karnesi kendi tablosunu
# üretir ve donus_radar.py onu kullanır (örnek sayısı yeterliyse).
MESAFE_KOVA = [0, 1.5, 2, 2.5, 3, 4, 99]
VARSAYILAN_TUTMA = {
    "1d": {"DIP": [[0, 1.5, 467, 57.2, 0.128], [1.5, 2, 584, 69.2, 0.008], [2, 2.5, 416, 75.7, 0.077], [2.5, 3, 214, 81.3, 0.131], [3, 4, 89, 87.6, 0.046], [4, 99, 39, 89.7, -0.13]],
           "ZIRVE": [[0, 1.5, 128, 46.1, -0.005], [1.5, 2, 431, 65.2, 0.075], [2, 2.5, 555, 77.3, 0.06], [2.5, 3, 369, 84.3, 0.018], [3, 4, 218, 86.7, -0.031], [4, 99, 40, 92.5, -0.05]]},
    "1h": {"DIP": [[0, 1.5, 1867, 34.1, -0.088], [1.5, 2, 3435, 45.3, -0.041], [2, 2.5, 3016, 52.7, -0.072], [2.5, 3, 1767, 62.2, -0.082], [3, 4, 1245, 75.2, -0.016], [4, 99, 284, 88.4, 0.05]],
           "ZIRVE": [[0, 1.5, 925, 35.4, -0.112], [1.5, 2, 2730, 46.5, -0.06], [2, 2.5, 2978, 57.5, -0.03], [2.5, 3, 2101, 65.7, -0.036], [3, 4, 1533, 74.6, -0.065], [4, 99, 348, 82.5, -0.098]]},
    "4h": {"DIP": [[0, 1.5, 1505, 42.3, 0.028], [1.5, 2, 2155, 54.1, 0.01], [2, 2.5, 1666, 60.3, -0.026], [2.5, 3, 857, 76.2, 0.025], [3, 4, 560, 85.0, -0.004], [4, 99, 114, 88.6, -0.02]],
           "ZIRVE": [[0, 1.5, 526, 43.3, 0.01], [1.5, 2, 1553, 54.8, 0.084], [2, 2.5, 1784, 61.7, 0.022], [2.5, 3, 1135, 67.5, -0.097], [3, 4, 752, 77.0, -0.09], [4, 99, 174, 90.2, -0.03]]},
}


def tutma_bul(tablo, tf, yon, mesafe):
    """(tutma %, n, ort. net R, [alt, üst]) -- mesafe: tepeden ATR cinsinden uzaklık."""
    for a, b, n, t, r in (tablo or {}).get(tf, {}).get(yon, []):
        if a <= mesafe < b:
            return t, n, r, [a, b]
    return None, 0, None, None
TF_MS = {"1h": 3600_000, "4h": 4 * 3600_000, "1d": 86400_000, "1w": 7 * 86400_000}
UST_TF = {"1h": [("4h", 0.6), ("1d", 0.4)], "4h": [("1d", 0.7), ("1w", 0.3)], "1d": [("1w", 1.0)]}

# "İşaret skoru" bileşenleri (toplam ~100). Verisi olmayan bileşen (ör. Bybit
# kaynaklı coinde işlem sayısı) hesaba katılmaz, kalanlar yeniden ölçeklenir.
# DİKKAT: Geriye dönük testte (170 coin, 1s ~6 ay / 4s ~16 ay / 1g ~4 yıl,
# zaman ikiye bölünerek) bu skor dönüşün tutmasını ya da kârı öngörmedi.
# Skor yalnız "yorulma işaretleri ne kadar yoğun" bilgisidir; İZLE adaylarını
# süzmek için kullanılır. Olasılık için TUTMA tablosuna bakılır (aşağıda).
AGIRLIK = {
    "kosu": 10,          # A: son koşu, coinin kendi geçmişine göre ne kadar uzun (yüzdelik)
    "uzaklik": 10,       # A: tepe EMA20'den kaç ATR uzakta (yüzdelik)
    "rsi": 12,           # A: tepe anındaki RSI
    "bant": 4,           # A: Bollinger üst bandının dışına taşma
    "uyumsuzluk": 12,    # B: fiyat daha yüksek tepe, RSI daha alçak tepe
    "hacim_klimaks": 6,  # B: hacim patlaması (blow-off)
    "islem_yogun": 4,    # B: işlem sayısı patlaması
    "islem_kucuk": 4,    # B: ortalama işlem büyüklüğü küçülüyor (küçük yatırımcı)
    "taker_emilim": 4,   # B: market alım baskısı var, fiyat ilerlemiyor
    "taker_donus": 5,    # D: tepeden sonra market satışları baskın
    "fitil": 8,          # B: tepede uzun ret fitili
    "supurme": 6,        # C: önceki tepenin üstüne fitil atıp altında kapanış
    "ust_tf": 10,        # C: üst zaman dilimi de aşırı / düşüşte (düzeltme değil)
}
BILESEN_AD = {
    "kosu": "Uzun koşu", "uzaklik": "Ortalamadan uzak", "rsi": "RSI uçta", "bant": "Bant dışı",
    "uyumsuzluk": "RSI uyumsuzluğu", "hacim_klimaks": "Hacim patlaması", "hacim_kuruma": "Hacim kuruyor",
    "islem_yogun": "İşlem sayısı patladı", "islem_kucuk": "Küçük işlemler", "taker_emilim": "Emilim",
    "taker_donus": "Taker döndü", "fitil": "Ret fitili", "supurme": "Likidite süpürme", "ust_tf": "Üst TF uyumlu",
}


def kis(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


# ---------------------------------------------------------------- indikatörler
def rsi_wilder(c, n=14):
    out = [None] * len(c)
    if len(c) < n + 1:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    g /= n
    l /= n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + (d if d > 0 else 0)) / n
        l = (l * (n - 1) + (-d if d < 0 else 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def atr_wilder(h, l, c, n=14):
    out = [None] * len(c)
    if len(c) < n + 1:
        return out
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def ema(c, n):
    out = [None] * len(c)
    if len(c) < n:
        return out
    k = 2 / (n + 1)
    e = sum(c[:n]) / n
    out[n - 1] = e
    for i in range(n, len(c)):
        e = c[i] * k + e * (1 - k)
        out[i] = e
    return out


def bollinger(c, n=20, m=2.0):
    ust = [None] * len(c)
    alt = [None] * len(c)
    s = s2 = 0.0
    for i, v in enumerate(c):
        s += v; s2 += v * v
        if i >= n:
            w = c[i - n]; s -= w; s2 -= w * w
        if i >= n - 1:
            o = s / n
            sd = math.sqrt(max(s2 / n - o * o, 0.0))
            ust[i] = o + m * sd
            alt[i] = o - m * sd
    return ust, alt


def z_log(x, n=50):
    """log(x)'in, kendisinden ÖNCEKİ n değere göre z-skoru (o mumun sıçraması). Kayan toplamla O(N)."""
    out = [None] * len(x)
    lg = [math.log(v) if v and v > 0 else None for v in x]
    s = s2 = 0.0
    k = 0
    for i in range(len(x)):
        if i >= n and k >= n // 2 and lg[i] is not None:
            o = s / k
            var = max(s2 / k - o * o, 0.0)
            sd = math.sqrt(var)
            out[i] = (lg[i] - o) / sd if sd > 1e-9 else 0.0
        v = lg[i]
        if v is not None:
            s += v; s2 += v * v; k += 1
        if i - n >= 0:
            w = lg[i - n]
            if w is not None:
                s -= w; s2 -= w * w; k -= 1
    return out


def ort_onceki(x, n=50):
    """x'in kendisinden önceki n değerin ortalaması (None'lar atlanır). O(N)."""
    out = [None] * len(x)
    s = 0.0
    k = 0
    for i in range(len(x)):
        if i >= n and k >= n // 2:
            out[i] = s / k
        v = x[i]
        if v is not None:
            s += v; k += 1
        if i - n >= 0 and x[i - n] is not None:
            s -= x[i - n]; k -= 1
    return out


# ---------------------------------------------------------------- seri
class Seri:
    """Tek coin / tek zaman dilimi. Diziler kapanmış mumlardır (eski -> yeni)."""

    def __init__(self, t, o, h, l, c, qv, n=None, tb=None, tf="1h"):
        self.tf = tf
        self.t, self.o, self.h, self.l, self.c, self.qv = t, o, h, l, c, qv
        self.n = n if n and any(v is not None for v in n) else None
        self.tb = tb if tb and any(v is not None for v in tb) else None
        self.hesapla()

    def hesapla(self):
        c = self.c
        self.rsi = rsi_wilder(c)
        self.atr = atr_wilder(self.h, self.l, c)
        self.e20 = ema(c, 20)
        self.e50 = ema(c, 50)
        self.bb_ust, self.bb_alt = bollinger(c)
        self.vz = z_log(self.qv)
        if self.n:
            self.nz = z_log(self.n)
            self.boy = [q / k if q and k else None for q, k in zip(self.qv, self.n)]
            self.boy_ort = ort_onceki(self.boy)
        else:
            self.nz = self.boy = self.boy_ort = None
        self.tbr = [tb / q if (tb is not None and q) else None for tb, q in zip(self.tb, self.qv)] if self.tb else None

    def __len__(self):
        return len(self.c)

    def ayna(self):
        """Dip araması için aynalanmış görünüm (aynı nesne tipi, yeniden hesap yok)."""
        a = Seri.__new__(Seri)
        a.tf = self.tf
        a.t, a.qv, a.n, a.tb = self.t, self.qv, self.n, self.tb
        a.o = [-x for x in self.o]
        a.h = [-x for x in self.l]
        a.l = [-x for x in self.h]
        a.c = [-x for x in self.c]
        a.rsi = [None if x is None else 100 - x for x in self.rsi]
        a.atr = self.atr
        a.e20 = [None if x is None else -x for x in self.e20]
        a.e50 = [None if x is None else -x for x in self.e50]
        a.bb_ust = [None if x is None else -x for x in self.bb_alt]
        a.bb_alt = [None if x is None else -x for x in self.bb_ust]
        a.vz, a.nz, a.boy, a.boy_ort = self.vz, self.nz, self.boy, self.boy_ort
        a.tbr = [None if x is None else 1 - x for x in self.tbr] if self.tbr else None
        return a


# ---------------------------------------------------------------- üst zaman dilimi
def haftalik(g):
    """Günlük seriden haftalık (Pazartesi 00:00 UTC başlangıçlı) seri; yalnız tamamlanmış haftalar."""
    gruplar = []
    for i in range(len(g)):
        gun = g.t[i] // 86400_000              # 1970-01-01 Perşembe
        hafta = (gun + 3) // 7                 # Pazartesi başlar
        if gruplar and gruplar[-1][0] == hafta:
            gruplar[-1][2] = i
        else:
            gruplar.append([hafta, i, i])
    gruplar = [x for x in gruplar if ((g.t[x[2]] // 86400_000 + 3) % 7) == 6]   # son günü Pazar
    if not gruplar:
        return None
    t = [g.t[a] for _, a, b in gruplar]
    o = [g.o[a] for _, a, b in gruplar]
    h = [max(g.h[a:b + 1]) for _, a, b in gruplar]
    l = [min(g.l[a:b + 1]) for _, a, b in gruplar]
    c = [g.c[b] for _, a, b in gruplar]
    qv = [sum(g.qv[a:b + 1]) for _, a, b in gruplar]
    return Seri(t, o, h, l, c, qv, tf="1w")


class UstDurum:
    """Üst TF'nin, verilen andan ÖNCE kapanmış son mumdaki durumu."""

    def __init__(self, s):
        self.s = s
        dur = TF_MS[s.tf]
        self.kapanis = [x + dur for x in s.t]

    def durum(self, an_ms, yon):
        i = bisect.bisect_right(self.kapanis, an_ms) - 1
        s = self.s
        if i < 55 or s.rsi[i] is None or s.atr[i] is None or s.e50[i] is None or s.atr[i] <= 0:
            return None
        r, c, e20, e50, a = s.rsi[i], s.c[i], s.e20[i], s.e50[i], s.atr[i]
        if yon == "DIP":
            r, c, e20, e50 = 100 - r, -c, -e20, -e50
        return {"rsi": r, "uzak": (c - e20) / a, "c": c, "e20": e20, "e50": e50}


def ust_skor(d):
    """1 = üst TF de aşırı (dönüş anlamlı); 0,7 = üst TF ters trendde (tepki bitiyor);
    0 = üst TF'de sağlıklı trend sürüyor (büyük ihtimalle yalnız düzeltme)."""
    if d is None:
        return None
    if d["rsi"] >= 70 or d["uzak"] >= 2.0:
        return 1.0
    if d["c"] < d["e50"] and d["e20"] < d["e50"]:
        return 0.7
    if d["c"] > d["e20"] > d["e50"] and d["rsi"] < 65:
        return 0.0
    return 0.4


def ust_metin(d, tf, yon):
    if d is None:
        return tf + ": veri yok"
    r = d["rsi"] if yon == "ZIRVE" else 100 - d["rsi"]
    s = ust_skor(d)
    if yon == "ZIRVE":
        ne = {1.0: "aşırı alımda", 0.7: "düşüş trendinde", 0.0: "yükseliş sürüyor", 0.4: "nötr"}[s]
    else:
        ne = {1.0: "aşırı satımda", 0.7: "yükseliş trendinde", 0.0: "düşüş sürüyor", 0.4: "nötr"}[s]
    return "%s %s (RSI %.0f)" % (tf, ne, r)


# ---------------------------------------------------------------- dedektör
def _pivot_once(h, p, sw, pv):
    """p'den önce, onaylanmış (sağında pv mum olan) en yakın salınım tepesi."""
    for q in range(p - pv - 1, max(pv, p - sw) - 1, -1):
        hq = h[q]
        if all(hq >= h[k] for k in range(q - pv, q + pv + 1)):
            return q
    return None


def bilesenler(s, p, i, ust, yon, yuzde):
    """Tepe p, karar anı i için bileşen skorları (0..1). None = veri yok."""
    P = PARAM
    h, l, c, o = s.h, s.l, s.c, s.o
    a = s.atr[p] or s.atr[i]
    b = {}
    # A: aşırılık
    b["kosu"] = kis((yuzde[0] - 0.5) / 0.45) if yuzde[0] is not None else None
    b["uzaklik"] = kis((yuzde[1] - 0.5) / 0.45) if yuzde[1] is not None else None
    rp = max(x for x in (s.rsi[p - 1], s.rsi[p]) if x is not None)
    b["rsi"] = kis((rp - 60) / 20)
    bu, ba = s.bb_ust[p], s.bb_alt[p]
    b["bant"] = kis(((h[p] - ba) / (bu - ba) - 0.9) / 0.3) if bu is not None and bu > ba else None
    # B: yorulma
    q = _pivot_once(h, p, P["SW"], P["PIVOT"])
    if q is not None and h[p] > h[q]:
        rq = max(x for x in (s.rsi[q - 1], s.rsi[q], s.rsi[q + 1]) if x is not None)
        b["uyumsuzluk"] = 1.0 if rp < rq - 3 else 0.5 if rp < rq else 0.0
        oran = (s.qv[p] + s.qv[p - 1]) / ((s.qv[q] + s.qv[q - 1]) or 1e-12)
        b["hacim_kuruma"] = kis((0.9 - oran) / 0.5)
    else:
        b["uyumsuzluk"] = 0.0
        b["hacim_kuruma"] = 0.0
    vz = [x for x in s.vz[p - 2:p + 1] if x is not None]
    b["hacim_klimaks"] = kis((max(vz) - 1.5) / 1.5) if vz else None
    if s.n:
        nz = [x for x in s.nz[p - 2:p + 1] if x is not None]
        b["islem_yogun"] = kis((max(nz) - 1.0) / 2.0) if nz else None
        by = [x for x in s.boy[p - 2:p + 1] if x is not None]
        bo = s.boy_ort[p - 2]
        b["islem_kucuk"] = kis((1.0 - (sum(by) / len(by)) / bo) / 0.4) if by and bo else None
    else:
        b["islem_yogun"] = b["islem_kucuk"] = None
    if s.tbr:
        q3 = sum(s.qv[p - 2:p + 1]) or 1e-12
        tb3 = sum((s.tbr[k] or 0.5) * s.qv[k] for k in range(p - 2, p + 1)) / q3
        ilerleme = (c[p] - o[p - 2]) / a if a else 0
        b["taker_emilim"] = kis((tb3 - 0.5) / 0.08) * kis(1 - ilerleme / 2.0)
        if i > p:
            qs = sum(s.qv[p + 1:i + 1]) or 1e-12
            ts = sum((s.tbr[k] or 0.5) * s.qv[k] for k in range(p + 1, i + 1)) / qs
            b["taker_donus"] = kis((0.5 - ts) / 0.06)
        else:
            b["taker_donus"] = 0.0
    else:
        b["taker_emilim"] = b["taker_donus"] = None
    uw = 0.0
    for j in range(p - 1, min(p + 1, i) + 1):
        rng = h[j] - l[j]
        if a and rng >= 0.5 * a:
            uw = max(uw, (h[j] - max(o[j], c[j])) / rng)
    b["fitil"] = kis((uw - 0.3) / 0.4)
    # C: bağlam
    lo, hi = max(0, p - P["SW"]), p - 3
    if hi > lo:
        onceki = max(h[lo:hi])
        if h[p] > onceki and (c[p] < onceki or (i > p and c[p + 1] < onceki)):
            b["supurme"] = 1.0
        elif a and 0 <= onceki - h[p] <= 0.5 * a:
            b["supurme"] = 0.5          # önceki tepeyi test edip geçemedi (ikili tepe)
        else:
            b["supurme"] = 0.0
    else:
        b["supurme"] = None
    b["ust_tf"] = ust
    return b


def skor_hesap(b, agirlik=None):
    w = agirlik or AGIRLIK
    top = pay = 0.0
    for k, v in b.items():
        if v is None or k not in w:
            continue
        top += w[k]
        pay += w[k] * v
    return round(100 * pay / top, 1) if top else 0.0


def _ust_birlesik(ustler, tf, an_ms, yon):
    """Üst TF skorlarının ağırlıklı ortalaması + açıklama metni."""
    toplam = pay = 0.0
    metin = []
    for utf, w in UST_TF.get(tf, []):
        u = ustler.get(utf)
        d = u.durum(an_ms, yon) if u else None
        sk = ust_skor(d)
        metin.append(ust_metin(d, {"4h": "4s", "1d": "1g", "1w": "1hf"}[utf], yon))
        if sk is not None:
            toplam += w
            pay += w * sk
    return (pay / toplam if toplam else None), metin


def olaylari_bul(s, yon, ustler=None, agirlik=None, canli=True):
    """Serideki tüm TETİK olaylarını (sonuçlarıyla) ve istenirse son mumdaki
    İZLE adayını döndürür. yon: 'ZIRVE' | 'DIP'. ustler: {'4h': UstDurum, ...}"""
    P = PARAM
    K, W = P["K"], P["W"]
    ustler = ustler or {}
    x = s if yon == "ZIRVE" else s.ayna()
    h, l, c = x.h, x.l, x.c
    n = len(c)
    dur = TF_MS[s.tf]
    ufuk = UFUK.get(s.tf, 18)
    kosu_l, uzak_l = [], []           # kendi geçmiş dağılımı (yalnız geçmiş -> sızıntı yok)
    kullanildi = set()
    olaylar = []
    izle = None
    bas = max(P["ISINMA"], W + 3)

    def yuzdelik(liste, v):
        if len(liste) < P["YUZDELIK_MIN"]:
            return None
        return bisect.bisect_left(liste, v) / len(liste)

    for i in range(n):
        a = x.atr[i]
        if a and a > 0 and i >= W and x.e20[i] is not None:
            bisect.insort(kosu_l, (h[i] - min(l[i - W:i + 1])) / a)
            bisect.insort(uzak_l, (h[i] - x.e20[i]) / a)
        if i < bas:
            continue
        pen = h[i - K + 1:i + 1]
        p = i - K + 1 + pen.index(max(pen))
        if p in kullanildi or p < W + 2:
            continue
        if h[p] < max(h[p - W:p]):
            continue                   # koşunun tepesi değil
        tetik = min(l[p - 2], l[p - 1])
        ap = x.atr[p]
        if not ap or ap <= 0:
            continue
        kirildi = c[i] < tetik
        if not kirildi:
            if canli and i == n - 1:
                izle = (p, tetik)
            continue
        kullanildi.add(p)
        yz = (yuzdelik(kosu_l, (h[p] - min(l[p - W:p + 1])) / ap),
              yuzdelik(uzak_l, (h[p] - x.e20[p]) / ap))
        ust, ust_m = _ust_birlesik(ustler, s.tf, s.t[i] + dur, yon)
        b = bilesenler(x, p, i, ust, yon, yz)
        giris = c[i]
        stop = h[p] + P["STOP_ATR"] * x.atr[i]
        risk = stop - giris
        if risk <= 0:
            continue
        hedef = giris - P["HEDEF_R"] * risk
        if yon == "ZIRVE" and hedef <= 0:
            continue
        sonuc, r, j_son = "acik", None, None
        mfe = 0.0
        for j in range(i + 1, min(i + ufuk, n - 1) + 1):
            mfe = max(mfe, (giris - l[j]) / risk)
            if h[j] >= stop:                      # aynı mumda ikisi de -> kötümser: stop
                sonuc, r, j_son = "stop", -1.0, j
                break
            if l[j] <= hedef:
                sonuc, r, j_son = "hedef", P["HEDEF_R"], j
                break
        else:
            if i + ufuk <= n - 1:
                sonuc, r, j_son = "sure", (giris - c[i + ufuk]) / risk, i + ufuk
        # zirve tuttu mu: ufuk boyunca tepe aşılmadı (ufuk dolmadıysa ve aşılmadıysa henüz bilinmiyor)
        sonra = h[i + 1:min(i + ufuk, n - 1) + 1]
        if any(v > h[p] for v in sonra):
            tuttu = False
        elif i + ufuk <= n - 1:
            tuttu = True
        else:
            tuttu = None
        maliyet = P["MALIYET"] * abs(giris) / risk
        olaylar.append({
            "yon": yon, "p": p, "i": i,
            "zirve_ts": s.t[p], "tetik_ts": s.t[i],
            "zirve": abs(h[p]), "tetik": abs(tetik), "giris": abs(giris), "stop": abs(stop), "hedef": abs(hedef),
            "risk_pct": round(risk / abs(giris) * 100, 3), "risk_atr": round(risk / x.atr[i], 2),
            "mesafe_atr": round((h[p] - giris) / x.atr[i], 2),
            "skor": skor_hesap(b, agirlik), "b": b, "ust_metin": ust_m,
            "sonuc": sonuc, "r": None if r is None else round(r, 3),
            "r_net": None if r is None else round(r - maliyet, 3),
            "tuttu": tuttu,
            "mfe": round(mfe, 2), "sure_mum": None if j_son is None else j_son - i,
            "son_ts": None if j_son is None else s.t[j_son],
        })
    if izle and canli:
        p, tetik = izle
        i = n - 1
        ap = x.atr[p]
        yz = (yuzdelik(kosu_l, (h[p] - min(l[p - W:p + 1])) / ap),
              yuzdelik(uzak_l, (h[p] - x.e20[p]) / ap))
        ust, ust_m = _ust_birlesik(ustler, s.tf, s.t[i] + dur, yon)
        b = bilesenler(x, p, i, ust, yon, yz)
        izle = {"yon": yon, "p": p, "i": i, "zirve_ts": s.t[p], "zirve": abs(h[p]), "tetik": abs(tetik),
                "mesafe_atr": round((h[p] - c[i]) / x.atr[i], 2),
                "stop": abs(h[p] + P["STOP_ATR"] * x.atr[i]),
                "skor": skor_hesap(b, agirlik), "b": b, "ust_metin": ust_m, "yas": i - p}
    return olaylar, izle

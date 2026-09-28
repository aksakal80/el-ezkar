#!/usr/bin/env python3
"""
donus_karne.py -- Dönüş Radarı geriye dönük testi (karne).

Ağ çağrısı YOK: donus_radar.py'nin mum önbelleğini (donus.db) okur, aynı
dedektörü (donus_cekirdek.olaylari_bul) geçmiş mumların her birinde
çalıştırır ve sonuçlanmış her TETİK sinyalini sayar.

Başarı tanımı (kötümser):
  hedef : tetikten sonra ufuk içinde fiyat 1,5R ters yöne gitti, tepe/dip aşılmadan
  stop  : önce tepe (+0,1 ATR) aşıldı -- aynı mumda ikisi olursa STOP sayılır
  sure  : ufuk doldu, ikisi de olmadı (R = ufuk sonu kapanışına göre)
  r_net : komisyon + kayma (%0,12 gidiş-dönüş) düşülmüş R
  tutma : ufuk boyunca tepe (dip) hiç aşılmadı mı

Ana tablo "mesafe": tetik anında fiyat tepeden kaç ATR uzaklaşmışsa, tepe
ufuk boyunca tuttu mu? donus_radar.py sinyallerin "tutma %" değerini bu
tablodan alır. Her satırda zamanın eski ve yeni yarısı ayrı verilir; iki yarı
birbirine yakın değilse o satıra güvenme.
"skor_kovalari": işaret skoruna göre aynı ölçüler (skorun işe yarayıp
yaramadığını görmek için). 1,5R hedefte başa baş isabet ~%40'tır.

Çıktı: /var/www/veri/donus_karne.json   (panel + donus_radar.json özet olarak okur)
Çalıştırma: günlük timer (05:17).  Elle:  python3 donus_karne.py [--oneri]
Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donus_cekirdek as dc  # noqa: E402
import donus_radar as dr     # noqa: E402

KOVALAR = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 101)]


def istatistik(ol):
    n = len(ol)
    if not n:
        return {"n": 0}
    hd = sum(1 for o in ol if o["sonuc"] == "hedef")
    st = sum(1 for o in ol if o["sonuc"] == "stop")
    tt = [o["tuttu"] for o in ol if o["tuttu"] is not None]
    rn = [o["r_net"] for o in ol]
    return {
        "n": n,
        "isabet": round(100 * hd / n, 1),
        "stop": round(100 * st / n, 1),
        "sure": round(100 * (n - hd - st) / n, 1),
        "tutma": round(100 * sum(tt) / len(tt), 1) if tt else None,
        "ort_r_net": round(sum(rn) / n, 3),
        "top_r_net": round(sum(rn), 1),
        "ort_risk_pct": round(sum(o["risk_pct"] for o in ol) / n, 2),
    }


def kova_tablosu(ol):
    out = []
    for a, b in KOVALAR:
        st = istatistik([o for o in ol if a <= o["skor"] < b])
        st.update({"a": a, "b": b})
        out.append(st)
    return out


def mesafe_tablosu(ol):
    """Tepeden uzaklık (ATR) kovalarına göre tutma; eski/yeni yarı ayrı (kararlılık kontrolü)."""
    yari = len(ol) // 2
    eski_ids = {id(o) for o in ol[:yari]}
    out = []
    kv = dc.MESAFE_KOVA
    for a, b in zip(kv[:-1], kv[1:]):
        ks = [o for o in ol if a <= o["mesafe_atr"] < b]
        st = istatistik(ks)
        ea = istatistik([o for o in ks if id(o) in eski_ids])
        ya = istatistik([o for o in ks if id(o) not in eski_ids])
        st.update({"a": a, "b": b, "tutma_eski": ea.get("tutma"), "tutma_yeni": ya.get("tutma")})
        out.append(st)
    return out


def bilesen_tablosu(ol):
    out = {}
    for k in dc.AGIRLIK:
        var = [o for o in ol if o["b"].get(k) is not None and o["b"][k] >= 0.5]
        yok = [o for o in ol if o["b"].get(k) is not None and o["b"][k] < 0.5]
        sv, sy = istatistik(var), istatistik(yok)
        out[k] = {"var_n": sv["n"], "var_isabet": sv.get("isabet"), "var_r": sv.get("ort_r_net"), "var_tutma": sv.get("tutma"),
                  "yok_n": sy["n"], "yok_isabet": sy.get("isabet"), "yok_r": sy.get("ort_r_net"), "yok_tutma": sy.get("tutma")}
    return out


def lojistik(ol, adim=400, oran=0.5, l2=0.01):
    """Bileşenlerden 'hedef' olasılığı: basit lojistik regresyon (yalnız ÖNERİ, otomatik uygulanmaz)."""
    ks = list(dc.AGIRLIK)
    X, Y = [], []
    for o in ol:
        X.append([1.0] + [(o["b"].get(k) if o["b"].get(k) is not None else 0.0) for k in ks])
        Y.append(1.0 if o["sonuc"] == "hedef" else 0.0)
    if len(X) < 200:
        return None
    w = [0.0] * len(X[0])
    n = len(X)
    for _ in range(adim):
        g = [0.0] * len(w)
        for x, y in zip(X, Y):
            z = sum(a * b for a, b in zip(w, x))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            for j in range(len(w)):
                g[j] += (p - y) * x[j]
        for j in range(len(w)):
            w[j] -= oran * (g[j] / n + (l2 * w[j] if j else 0))
    return {k: round(v, 3) for k, v in zip(["sabit"] + ks, w)}


def main():
    ap = argparse.ArgumentParser(description="Dönüş Radarı karnesi (geriye dönük test)")
    ap.add_argument("--oneri", action="store_true", help="bileşen ağırlık önerisi (lojistik) de hesapla")
    ap.add_argument("--csv", default="", help="tüm olayları CSV'ye yaz (inceleme için)")
    a = ap.parse_args()
    t0 = time.time()
    db = dr.db_ac()
    semboller = db.execute("SELECT s, src, carpan FROM kaynak WHERE src!='yok'").fetchall()
    tum = {tf: {"ZIRVE": [], "DIP": []} for tf in dr.TFLER}
    for s, src, carpan in semboller:
        seriler = {}
        for tf in dr.TFLER:
            sr = dr.seri_oku(db, s, tf, carpan if src == "bybit" else 1.0, limit=dr.SAKLA[tf])
            if sr:
                seriler[tf] = sr
        ustler = {tf: dc.UstDurum(v) for tf, v in seriler.items()}
        if "1d" in seriler:
            w = dc.haftalik(seriler["1d"])
            if w and len(w) > 60:
                ustler["1w"] = dc.UstDurum(w)
        for tf, sr in seriler.items():
            for yon in ("ZIRVE", "DIP"):
                olaylar, _ = dc.olaylari_bul(sr, yon, ustler, canli=False)
                for o in olaylar:
                    if o["sonuc"] != "acik":
                        o["s"] = s
                        o["kaynak"] = src
                        tum[tf][yon].append(o)
    ozet, bilesen, oneri, donem = {}, {}, {}, {}
    n_top = 0
    for tf in dr.TFLER:
        ozet[tf], bilesen[tf] = {}, {}
        for yon in ("ZIRVE", "DIP"):
            ol = tum[tf][yon]
            n_top += len(ol)
            ol.sort(key=lambda o: o["tetik_ts"])
            yari = len(ol) // 2
            iyi = lambda l: [o for o in l if o["skor"] >= 60]  # noqa: E731
            ozet[tf][yon] = {
                "hepsi": istatistik(ol),
                "mesafe": mesafe_tablosu(ol),
                "skor_kovalari": kova_tablosu(ol),
                "yarilar_60": [istatistik(iyi(ol[:yari])), istatistik(iyi(ol[yari:]))],
            }
            bilesen[tf][yon] = bilesen_tablosu(ol)
            if ol:
                donem.setdefault(tf, [min(o["tetik_ts"] for o in ol), max(o["tetik_ts"] for o in ol)])
        if a.oneri:
            oneri[tf] = lojistik(tum[tf]["ZIRVE"] + tum[tf]["DIP"])
    cikti = {
        "guncel": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sure_sn": round(time.time() - t0, 1),
        "n_coin": len(semboller), "n_olay": n_top, "donem": donem,
        "tanim": "hedef = 1,5R ters hareket, tepe aşılmadan; stop = tepe aşıldı; ufuk 1s:24, 4s:18, 1g:10 mum; r_net maliyet düşülmüş.",
        "not": "Aynı anda birçok coinde çıkan sinyaller birbirinden bağımsız değildir (piyasa geneli hareket); "
               "n büyük görünse de etkin örnek daha küçüktür. Kovalar arası farka ve iki yarının tutarlılığına bakın.",
        "ozet": ozet, "bilesen": bilesen, "oneri_agirlik": oneri or None,
        "agirlik": dc.AGIRLIK, "param": dict(dc.PARAM, UFUK=dc.UFUK),
    }
    dr.atomik_json(dr.KARNE, cikti)
    if a.csv:
        import csv
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            ks = list(dc.AGIRLIK)
            w = csv.writer(f)
            w.writerow(["s", "tf", "yon", "tetik_ts", "skor", "sonuc", "r_net", "tuttu", "risk_pct", "kaynak"] + ks)
            for tf in tum:
                for yon in tum[tf]:
                    for o in tum[tf][yon]:
                        w.writerow([o["s"], tf, yon, o["tetik_ts"], o["skor"], o["sonuc"], o["r_net"], o["tuttu"], o["risk_pct"], o["kaynak"]]
                                   + [o["b"].get(k) for k in ks])
    # okunur özet
    for tf in dr.TFLER:
        for yon in ("ZIRVE", "DIP"):
            z = ozet[tf][yon]
            h = z["hepsi"]
            if not h["n"]:
                continue
            print("\n== %s %s  (tüm tetikler: n=%d isabet %%%.1f tutma %s ort %+.3fR)" %
                  (tf, yon, h["n"], h["isabet"], h["tutma"], h["ort_r_net"]))
            for k in z["mesafe"]:
                if k["n"]:
                    print("   mesafe %3.1f-%-4.1f ATR n=%5d tutma %%%5s (eski %s / yeni %s) isabet %%%5.1f ort %+.3fR" %
                          (k["a"], k["b"], k["n"], k["tutma"], k["tutma_eski"], k["tutma_yeni"], k["isabet"], k["ort_r_net"]))
            for k in z["skor_kovalari"]:
                if k["n"]:
                    print("   skor %3d-%-3d n=%5d isabet %%%5.1f stop %%%5.1f tutma %%%5s ort %+.3fR" %
                          (k["a"], k["b"], k["n"], k["isabet"], k["stop"], k["tutma"], k["ort_r_net"]))
            y = z["yarilar_60"]
            print("   skor>=60 eski yarı: n=%s isabet %s  |  yeni yarı: n=%s isabet %s" %
                  (y[0].get("n"), y[0].get("isabet"), y[1].get("n"), y[1].get("isabet")))
    if oneri:
        print("\nlojistik öneri:", json.dumps(oneri, ensure_ascii=False))
    print("\n[OK] %s -- %d olay, %.0f sn" % (dr.KARNE, n_top, time.time() - t0))


if __name__ == "__main__":
    main()

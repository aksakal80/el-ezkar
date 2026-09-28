#!/usr/bin/env python3
"""
rsi_grafik_yama.py -- rsi_grafik.html'e Dönüş Radarı'nı ekler (katman + liste + senaryo + bağlantı).

Dosyanın tamamını değiştirmez; bilinen çapa satırlarının yanına işaretli
bloklar ekler. Tekrar çalıştırılabilir: eski Dönüş blokları önce sökülür,
sonra güncel hâli eklenir. Çapalardan biri bulunamazsa HİÇBİR ŞEY yazmaz.

Kullanım:
  python3 rsi_grafik_yama.py /var/www/veri/rsi_grafik.html            # yedek alır, uygular
  python3 rsi_grafik_yama.py /var/www/veri/rsi_grafik.html --sok      # yalnız Dönüş bloklarını söker
  python3 rsi_grafik_yama.py girdi.html --cikti deneme.html           # başka dosyaya yaz (deneme)
Yedek: /root/backups/rsi_grafik.html.bak_YYYYMMDD_HHMMSS (klasör yoksa dosyanın yanına)
"""
import argparse
import os
import re
import shutil
import sys
import time

JS_BAS, JS_SON = "/* DONUS>>> */", "/* <<<DONUS */"
HT_BAS, HT_SON = "<!-- DONUS>>> -->", "<!-- <<<DONUS -->"

# ---------------------------------------------------------------- eklenecek bloklar
NAV = '<a href="donus_panel.html">🔄 Dönüş Radarı</a>'

ANAHTAR = ('🔄 <b>Dönüş Radarı</b>: ▼ ZİRVE / ▲ DİP oku tepe/dip mumunda (yanında skor), 🎯 dönüşün teyit edildiği mum · '
           '"Dönüş tetik" teyit seviyesi, "Dönüş iptal" aşılırsa sinyal geçersiz, "Dönüş hedef" ölçülü hedef (1,5R).<br>')

YARDIMCI = r"""
/* Dönüş Radarı (donus_radar.json): grafiğin zaman dilimindeki sinyal öncelikli, sonra tetikli, sonra skor */
function donusSec(v) {
  var l = (v && v.liste || []).slice().sort(function (a, b) {
    return ((b.tf === TF) - (a.tf === TF)) || ((a.asama === 'TETIK' ? 0 : 1) - (b.asama === 'TETIK' ? 0 : 1)) || (b.skor - a.skor);
  });
  return l[0] || null;
}
function donusTutma(x) {       /* geçmişte aynı uzaklıktaki sinyallerde tepenin tutma oranı (yalnız tetikliler) */
  if (x.asama !== 'TETIK' || x.tutma === null || x.tutma === undefined) return '';
  return 'tutma %' + nf(x.tutma, 0) + ' (' + nf(x.tutma_n, 0) + ' örnek, ort. ' + sgn(x.bek_r, 2) + 'R)';
}
function donusAd(x) { return (x.yon === 'ZIRVE' ? '🔴 Zirve' : '🟢 Dip') + ' ' + (TF_TR[x.tf] || x.tf) + ' · ' + (x.asama === 'TETIK' ? '🎯 dönüş teyitli' : '👀 aday, teyitsiz'); }
"""

KATMAN = r"""  {
    id: 'donus', kutu: 'okDonus', grup: 'g_formasyon', varsayilan: true,
    baslik: 'Dönüş Radarı: tepe/dip yapıp dönmeye başlayan coinler (aşırılık + yorulma + teyit; 1s/4s/1g, saat başı)',
    ad: function () { return 'Dönüş Radarı'; },
    anahtar: function () { return SYM; },
    yukle: function (sym) {
      return dosyaGetir('donus_radar.json').then(function (d) {
        var l = (d.sinyaller || []).filter(function (x) { return x.s === sym; });
        var r = (d.sonuclanan || []).filter(function (x) { return x.s === sym; });
        return (l.length || r.length) ? { liste: l, sonuc: r, karne: d.karne, guncel: d.guncel, esikGun: 0.25 } : null;
      });
    },
    seviyeler: function (v, P) {
      var x = donusSec(v);
      if (!x || bayat(v)) return [];
      return [{ p: x.zirve, kaynak: 'Dönüş Radarı ' + (x.yon === 'ZIRVE' ? 'zirvesi' : 'dibi') + ' (' + (TF_TR[x.tf] || x.tf) + ')' }];
    },
    ciz: function (v, R, LC) {
      var x = donusSec(v);
      if (!x) return [];
      var z = x.yon === 'ZIRVE', sar = css('--uyari'), L = [];
      L.push({ price: x.stop, color: z ? R.dip : R.tav, lineWidth: 1, lineStyle: LC.LineStyle.SparseDotted, axisLabelVisible: true, title: 'Dönüş iptal' });
      if (x.asama === 'TETIK') L.push({ price: x.hedef, color: sar, lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: true, title: 'Dönüş hedef' });
      else L.push({ price: x.tetik, color: sar, lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: 'Dönüş tetik' });
      return L;
    },
    seriler: function (v) {                // tepe/dip mumuna ok, tetik mumuna 🎯 (yalnız aynı zaman diliminde)
      var x = donusSec(v);
      if (!x || x.tf !== TF || !ROWS.length) return [];
      var z = x.yon === 'ZIRVE', ilk = ROWS[0].t, son = ROWS[ROWS.length - 1].t, veri = [], is = [];
      var ayni = x.tetik_ts && x.tetik_ts === x.zirve_ts;
      if (x.zirve_ts >= ilk && x.zirve_ts <= son) {
        veri.push({ time: t(x.zirve_ts), value: x.zirve });
        is.push({ time: t(x.zirve_ts), position: z ? 'aboveBar' : 'belowBar', shape: z ? 'arrowDown' : 'arrowUp', color: z ? css('--dip') : css('--tav'),
                  text: (z ? 'ZİRVE ' : 'DİP ') + Math.round(x.skor) + (ayni ? ' 🎯' : ''), size: 1.2 });
      }
      if (x.tetik_ts && !ayni && x.tetik_ts >= ilk && x.tetik_ts <= son) {
        veri.push({ time: t(x.tetik_ts), value: x.tetik });
        is.push({ time: t(x.tetik_ts), position: z ? 'aboveBar' : 'belowBar', shape: 'circle', color: css('--uyari'), text: '🎯', size: 0.8 });
      }
      return veri.length ? [{ id: 'dnIsaret', veri: veri, renk: 'rgba(0,0,0,0)', stil: 0, kalin: 1, isaretler: is }] : [];
    },
    kanit: function (v, P, art, eks) {
      if (bayat(v)) return;
      v.liste.forEach(function (x) {
        /* ağırlık ölçülü: geriye dönük testte ortalama sonuç ~0R. Tetikli > aday; grafiğin zaman dilimi tam, diğerleri yarım */
        var w = (x.asama === 'TETIK' ? 0.5 : 0.25) * (x.tf === TF ? 1 : 0.5);
        var t2 = donusTutma(x);
        var m = 'Dönüş Radarı (' + (TF_TR[x.tf] || x.tf) + '): ' + (x.yon === 'ZIRVE' ? 'tepe ' : 'dip ') + fiyatFmt(x.zirve) +
          (x.asama === 'TETIK' ? ', dönüş teyitli (tetik ' + fiyatFmt(x.tetik) + ' kırıldı)' : ' adayı, dönüş henüz teyitsiz (tetik ' + fiyatFmt(x.tetik) + ')') +
          (t2 ? '; geçmişte ' + t2 : '') + '.';
        if (x.yon === 'ZIRVE') eks(m, w); else art(m, w);
      });
    },
    ozet: function (v, P) {
      var h = v.liste.map(function (x) {
        var tt = x.asama === 'TETIK', t2 = donusTutma(x);
        return '<b>' + donusAd(x) + '</b>' + (t2 ? ' · <b>' + t2 + '</b>' : '') + ' · ' + (x.yon === 'ZIRVE' ? 'zirve ' : 'dip ') + fiyatFmt(x.zirve) +
          ' (' + esc(zamanTR(x.zirve_ts)) + ', ' + x.yas + ' mum önce) · tetik ' + fiyatFmt(x.tetik) +
          ' · iptal ' + fiyatFmt(x.stop) + ' (' + sgn((x.stop / P - 1) * 100, 1) + '%)' +
          (tt ? ' · hedef ' + fiyatFmt(x.hedef) + ' (' + sgn((x.hedef / P - 1) * 100, 1) + '%) · şimdi ' + sgn(x.r_simdi, 2) + 'R' : '') +
          '<br><small>' + esc((x.etiket || []).join(' · ')) + (x.ust && x.ust.length ? ' · üst TF: ' + esc(x.ust.join(', ')) : '') +
          (x.turev && x.turev.length ? ' · türev: ' + esc(x.turev.join(', ')) : '') + ' · işaret skoru ' + nf(x.skor, 0) + '</small>';
      });
      (v.sonuc || []).forEach(function (x) {
        h.push('<small>Son sonuçlanan: ' + (x.yon === 'ZIRVE' ? 'zirve' : 'dip') + ' ' + (TF_TR[x.tf] || x.tf) + ' → ' +
          ({ hedef: '✅ hedef', stop: '❌ iptal', sure: '⏱ süre doldu' }[x.sonuc] || esc(x.sonuc)) + ' (' + sgn(x.r_net, 2) + 'R, ' + esc(zamanTR(x.son_ts)) + ')</small>');
      });
      return (h.length ? h.join('<br>') : 'Bu coinde şu an dönüş sinyali yok.') + (bayat(v) ? bayatNot(v) : '');
    },
    karne: function (v) {
      var x = donusSec(v) || (v.sonuc || [])[0], k = x && v.karne && v.karne[x.tf] && v.karne[x.tf][x.yon];
      if (!k || !k.hepsi || !k.hepsi.n) return 'Karne henüz yok (gece hesaplanır).';
      return 'Karne (' + (TF_TR[x.tf] || x.tf) + ' ' + (x.yon === 'ZIRVE' ? 'zirve' : 'dip') + ', geriye dönük, ' + nf(k.hepsi.n, 0) + ' tetik): tepe ufuk boyunca %' + nf(k.hepsi.tutma, 0) +
        ' tuttu, 1,5R hedefe %' + nf(k.hepsi.isabet, 0) + ' ulaştı, ortalama net sonuç ' + sgn(k.hepsi.ort_r_net, 2) + 'R. ' +
        'RSI, hacim, işlem sayısı gibi işaretler sonucu öngörmedi; tutmayı belirleyen esas etken fiyatın tepeden uzaklığı.';
    }
  },
"""

SENARYO = r"""  donus: function (v, P) {
    var x = donusSec(v), L = [];
    if (!x) return L;
    var z = x.yon === 'ZIRVE';
    if (x.asama === 'TETIK') {
      L.push((z ? '🔴' : '🟢') + ' <b>Dönüş teyitli</b>: ' + (z ? 'tepe ' : 'dip ') + fiyatFmt(x.zirve) + ', fiyat tetik seviyesini (' + fiyatFmt(x.tetik) + ') ' +
             (z ? 'aşağı' : 'yukarı') + ' kırdı. Ölçülü hedef ' + fiyatFmt(x.hedef) + ' (' + sgn((x.hedef / P - 1) * 100, 1) + '%).');
      L.push('Geçersizlik: fiyat ' + fiyatFmt(x.stop) + ' ' + (z ? 'üstüne çıkarsa zirve' : 'altına inerse dip') + ' tutmamış olur; ' + (z ? 'yükseliş' : 'düşüş') + ' sürüyor demektir.');
    } else {
      L.push('👀 <b>Dönüş adayı, teyitsiz</b>: ' + (z ? 'tepe ' : 'dip ') + fiyatFmt(x.zirve) + ' civarında yorulma işaretleri var (' + esc((x.etiket || []).slice(0, 3).join(', ')) +
             '). Teyit için kapanışın ' + fiyatFmt(x.tetik) + (z ? ' altında' : ' üstünde') + ' gelmesi gerekir.');
      L.push('Teyit gelmeden ' + fiyatFmt(x.zirve) + (z ? ' aşılırsa yükseliş' : ' kırılırsa düşüş') + ' sürüyor demektir; aday düşer.');
    }
    if (x.ust && x.ust.length) L.push('Üst zaman dilimi: ' + esc(x.ust.join(' · ')) + '. "' + (z ? 'Yükseliş' : 'Düşüş') + ' sürüyor" yazıyorsa bu yalnız bir düzeltme olabilir.');
    if (x.asama === 'TETIK' && x.tutma !== null && x.tutma !== undefined)
      L.push('Geçmişte aynı zaman diliminde, tepeden aynı uzaklıkta tetiklenen ' + nf(x.tutma_n, 0) + ' sinyalde ' + (z ? 'zirve' : 'dip') + ' %' + nf(x.tutma, 0) +
             ' oranında tuttu; ortalama net sonuç ' + sgn(x.bek_r, 2) + 'R (sıfıra yakınsa tek başına işlem gerekçesi değildir).');
    return L;
  },
"""

LISTE = r"""  { id: 'donus', hazir: ['donus', 'srk', 'duvar'], isaret: true, sayfa: 'donus_panel.html', ad: '🔄 Dönüş Radarı', katman: 'donus', basliklar: ['TF', 'Tut%'],
    sekmeler: [['ZIRVE', '🔴 Zirve'], ['DIP', '🟢 Dip']],
    baslik: function (sk) { return 'Dönüş Radarı · ' + (sk === 'ZIRVE' ? 'tepe yapıp düşüşe dönenler' : 'dip yapıp yükselişe dönenler') + ' · 🎯 teyitli (en yeni önce), 👀 aday · Tut% = geçmişte aynı uzaklıkta tutma oranı (tıklayınca kendi zaman diliminde açılır)'; },
    yukle: function () { return dosyaGetir('donus_radar.json'); },
    satirlar: function (d, sk) {
      return (d.sinyaller || []).filter(function (x) { return x.yon === sk; })
        .sort(function (a, b) { return ((a.asama === 'TETIK' ? 0 : 1) - (b.asama === 'TETIK' ? 0 : 1)) || ((b.tetik_ts || 0) - (a.tetik_ts || 0)) || (b.skor - a.skor); })
        .map(function (x, i) {
          var tt = x.asama === 'TETIK' && x.tutma !== null && x.tutma !== undefined;
          var r = lsat(x.s, i + 1, TF_TR[x.tf] || x.tf, tt ? nf(x.tutma, 0) : '—', x.asama === 'TETIK' ? '🎯' : '👀', sk === 'ZIRVE' ? 'var(--dip)' : 'var(--tav)', x.tf);
          r.ipucu = (x.asama === 'TETIK' ? 'Dönüş teyitli · ' + donusTutma(x) : 'Aday, teyitsiz') + ' · ' + (x.etiket || []).slice(0, 3).join(', ');
          return r;
        });
    } },
"""

# (çapa, blok, konum, tür)   konum: 'once' = çapadan önce, 'sonra' = çapadan sonra
EKLER = [
    ('<a href="piyasa_pano.html">🧭 Piyasa Panosu</a>', NAV, "sonra", "html"),
    ("H-T1/H-T2 hedefleri, H stop).<br>", ANAHTAR, "sonra", "html"),
    ("var KATMAN_TANIM = [", YARDIMCI, "once", "js"),
    ("  {\n    id: 'zirve', kutu: 'okZirve', grup: 'g_ozet'", KATMAN, "once", "js"),
    ("  zirve: function (v) {\n    var L = [];", SENARYO, "once", "js"),
    ("  { id: 'zirve', hazir: ['zirve', 'srk', 'form', 'harm']", LISTE, "once", "js"),
]
# RSI taraması listesi seçiliyken de Dönüş katmanı açık gelsin
DEGISIM = [("{ id: 'rsi', hazir: ['duvar', 'srk'],", "{ id: 'rsi', hazir: ['duvar', 'srk', 'donus'],")]


def sok(metin):
    """Önceki Dönüş bloklarını ve değişimlerini geri alır."""
    for bas, son in ((JS_BAS, JS_SON), (HT_BAS, HT_SON)):
        metin = re.sub(r"\n?" + re.escape(bas) + r".*?" + re.escape(son) + r"\n?", "\n", metin, flags=re.S)
    for eski, yeni in DEGISIM:
        metin = metin.replace(yeni, eski)
    return metin


def uygula(metin):
    metin = sok(metin)
    # sökümde çapa etrafında kalan fazladan boş satırları geri almak için önce çapaları doğrula
    for capa, _, _, _ in EKLER:
        n = metin.count(capa)
        if n != 1:
            raise SystemExit("HATA: çapa %d kez bulundu (1 olmalı): %r\nrsi_grafik.html değişmiş olabilir; yama uygulanmadı." % (n, capa[:70]))
    for capa, blok, konum, tur in EKLER:
        if tur == "html":
            sarili = HT_BAS + blok + HT_SON
            yeni = capa + sarili if konum == "sonra" else sarili + capa
        else:
            sarili = JS_BAS + "\n" + blok.strip("\n") + "\n" + JS_SON + "\n"
            yeni = sarili + capa if konum == "once" else capa + "\n" + sarili
        metin = metin.replace(capa, yeni, 1)
    for eski, yeni in DEGISIM:
        if metin.count(eski) == 1:
            metin = metin.replace(eski, yeni, 1)
    return metin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dosya")
    ap.add_argument("--sok", action="store_true", help="yalnız Dönüş bloklarını kaldır")
    ap.add_argument("--cikti", default="", help="sonucu başka dosyaya yaz (yedek alınmaz)")
    a = ap.parse_args()
    with open(a.dosya, encoding="utf-8") as f:
        eski = f.read()
    yeni = sok(eski) if a.sok else uygula(eski)
    hedef = a.cikti or a.dosya
    if not a.cikti:
        yd = "/root/backups" if os.path.isdir("/root/backups") else os.path.dirname(os.path.abspath(a.dosya))
        yedek = os.path.join(yd, os.path.basename(a.dosya) + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(a.dosya, yedek)
        print("yedek:", yedek)
    tmp = hedef + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(yeni)
    if os.path.exists(hedef):
        shutil.copymode(hedef, tmp)
    os.replace(tmp, hedef)
    print("[OK]", hedef, "söküldü" if a.sok else "Dönüş Radarı eklendi", "(%+d bayt)" % (len(yeni) - len(eski)))


if __name__ == "__main__":
    sys.exit(main())

# 🔄 Dönüş Radarı

Binance vadelide listeli coinlerde (≈490 coin, 24 saatlik hacmi 250 bin $ üstü) 1 saatlik, 4 saatlik ve günlük mumlarda
**tepe / dip yapıp dönmeye başlayanları** bulur. Panel: `donus_panel.html`. Ayrıca `rsi_grafik.html`'e katman ve liste olarak eklenir.

Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir.

## Önce test sonucu

Geriye dönük test 170 coin üzerinde yapıldı: 1s için ~6 ay, 4s için ~16 ay, 1g için ~4 yıl, toplam 38.724 sonuçlanmış tetik.
Zaman ikiye bölündü, eski ve yeni yarı ayrı ölçüldü.

1. **RSI, hacim, işlem sayısı, RSI uyumsuzluğu, fitil, likidite süpürme, üst zaman dilimi**: bunların hiçbiri, tek tek ya da
   birlikte (örnek dışı lojistik model, AUC 0,50–0,56), **dönüşün kârlı olacağını öngörmedi**.
2. Tetikli sinyallerin ortalama sonucu, komisyon ve kayma düşülünce **yaklaşık 0R**:
   1s −0,05R, 4s ~0R, 1g +0,03 / +0,09R. Tek başına kârlı bir strateji değil.
3. Tepenin (dibin) **tutup tutmayacağını** belirleyen tek tutarlı etken, tetik anında **fiyatın tepeden kaç ATR uzaklaştığı**.
   Örneğin 1s zirvede: 0–1,5 ATR'de tepe %35, 3–4 ATR'de %75 tutmuş. Bu oranlar iki zaman yarısında da birbirine yakın.
   Ama uzaklık arttıkça kalan yol da kısalıyor. Kesinlik ile erken yakalama arasında takas var.
4. Hacim patlaması ve işlem sayısı patlaması olan tepelerde tutma oranı daha yüksek (%71 / %69, yokken %56). Bunun bir kısmı
   uzaklıkla ilişkiden geliyor; kâr sonucu yine ~0R.
5. Sonuçlar piyasa dönemine çok bağlı. Örneğin Ağustos–Eylül 2026 yükselişinde zirve sinyalleri kötü, dip sinyalleri iyi çalıştı;
   Haziran–Temmuz'da tersi oldu.

Bu yüzden paneldeki ana sayı **Tutma %**. Bu değer, geçmişte aynı zaman diliminde, aynı yönde ve aynı uzaklıktaki sinyallerin
gerçek tutma oranıdır. **İşaret skoru** yalnız yorulma işaretlerinin yoğunluğunu gösterir; bilgi amaçlıdır, İZLE adaylarını süzmek için kullanılır.

## Nasıl çalışır

| Aşama | Kural |
|---|---|
| **Tepe** | Son 6 mum içinde, önceki 20 mumun en yükseği olan mum |
| **👀 İZLE** | Tepe var, tetik kırılmadı, işaret skoru ≥ 60 (teyitsiz; fiyat yükselmeye devam edebilir) |
| **🎯 TETİK** | Kapanış, tepeye çıkan son itkinin dibinin (tepeden önceki 2 mumun en düşüğü) altına indi; tepeden en fazla 5 mum sonra |
| **İptal (stop)** | Tepe + 0,1 ATR. Aşılırsa zirve tutmamış demektir |
| **Hedef** | Giriş − 1,5 × risk |
| **Ufuk** | 1s: 24 mum · 4s: 18 mum · 1g: 10 mum |

Dip için aynı kod fiyatı aynalayarak çalışır, iki yön birebir simetriktir.

İşaret bileşenleri (her biri 0–1):
- Koşu uzunluğu ve ortalamadan uzaklık (coinin kendi geçmişine göre yüzdelik)
- RSI, Bollinger dışı
- RSI uyumsuzluğu
- Hacim patlaması (log hacim z-skoru)
- İşlem sayısı patlaması ve küçük işlemler (ortalama işlem büyüklüğü düşüyor)
- Emilim (market alım baskısı var, fiyat ilerlemiyor) ve taker dönüşü
- Ret fitili, likidite süpürme
- Üst zaman dilimi: 1s için 4s+1g, 4s için 1g+haftalık, 1g için haftalık

Türev rozetleri (fonlama z-skoru, açık pozisyon, long %, tasfiye) `turev_radar.json`'dan gelir. Geçmiş verisi olmadığı için
sınanmadılar, skora girmezler. Canlı defter bunları zamanla ölçebilir.

## Veri ve ağ

- **Evren**: `/var/www/veri/turev_radar.json` (yalnız Binance vadelide listeli coinler). Dosya yoksa `ticker_ws.json` kullanılır.
- **Mumlar**: önce Binance **spot** (`data-api.binance.vision`), spotta yoksa **Bybit** vadeli. **Binance FAPI'ye hiç gidilmez**,
  fapi ban kapısı gerekmez.
  - Spot mumları işlem sayısını ve taker alış hacmini içerir.
  - Bybit kaynaklı coinlerde bu iki veri yoktur; ilgili işaretler "veri yok" sayılır.
- **Hız sınırı**: saniyede 5 istek, 4 iş parçacığı. 418 ya da 429 gelirse o kaynak `Retry-After` kadar susar
  (`.kaynak_bekle.json`). Kısa soğumada beklenir.
- **Önbellek**: `/root/sinyal/donus_radar/donus.db` (sqlite, WAL). Her saat yalnız yeni kapanan mumlar çekilir.
  - Saklanan mum: 1s 4.500, 4s 3.000, 1g 1.500. Yaklaşık 4–5 milyon satır, birkaç yüz MB.
- **Kilit**: aynı anda tek `donus_radar.py` çalışır (`.donus_radar.lock`).

## Dosyalar

| Dosya | Görev |
|---|---|
| `donus_cekirdek.py` | İndikatörler, dedektör, işaret skoru, tutma tablosu (saf stdlib, ağ yok) |
| `donus_radar.py` | Saatlik iş: evren → mum → analiz → `/var/www/veri/donus_radar.json`, canlı defter |
| `donus_karne.py` | Gece karnesi (ağ yok): `/var/www/veri/donus_karne.json`. Tutma tablosu buradan güncellenir |
| `donus_panel.html` | Statik panel, `/var/www/veri/` altında |
| `rsi_grafik_yama.py` | `rsi_grafik.html`'e katman, liste, senaryo ve bağlantı ekler / söker |
| `systemd/` | `donus_radar` (saat başı :02), `donus_karne` (05:17), `donus_derin` (Pazar 04:32, yeni coinlerin geçmişi) |
| `kur.sh` / `geri_al.sh` | Kurulum / kaldırma |

Mevcut hiçbir bota, `.py`'ye ya da `.db`'ye dokunulmaz. Yalnız `rsi_grafik.html`'e işaretli bloklar eklenir; önce
`/root/backups/rsi_grafik.html.bak_TARİH` yedeği alınır.

## Kurulum (sunucuda, root)

```bash
# bu klasörü sunucuya kopyala (ör. /root/donus_radar_kurulum), sonra:
cd /root/donus_radar_kurulum
bash kur.sh          # ilk veri çekimi + geçmiş doldurma 20-40 dk sürer; screen/tmux içinde çalıştırman iyi olur
```

`kur.sh` sırasıyla şunları yapar:
1. Kodu `/root/sinyal/donus_radar/`'a kopyalar (`/root/sinyal/venv` kullanılır, ek paket gerekmez).
2. Paneli `/var/www/veri/`'ye kopyalar.
3. systemd birimlerini kurar.
4. İlk veri çekimini yapar, karneyi hesaplar.
5. `rsi_grafik.html`'i yamalar.
6. Zamanlayıcıları açar.

Kontrol:
```bash
systemctl list-timers --no-pager | grep donus
journalctl -u donus_radar.service -n 30 --no-pager
python3 -c "import json;d=json.load(open('/var/www/veri/donus_radar.json'));print(d['guncel'],len(d['sinyaller']),d['evren'])"
```

Adresler:
- https://veri.ayaydin.tr/donus_panel.html
- https://veri.ayaydin.tr/rsi_grafik.html?liste=donus

### rsi_grafik.html güncellenirse
Yama çapaları bulunamazsa hiçbir şey yazılmaz ve hata verir. Yeniden uygulamak için:
`/root/sinyal/venv/bin/python3 /root/sinyal/donus_radar/rsi_grafik_yama.py /var/www/veri/rsi_grafik.html`.
Betik tekrar çalıştırılabilir: eski blokları söküp günceli ekler.

## Kaldırma
```bash
bash geri_al.sh      # zamanlayıcılar durur, rsi_grafik'ten bloklar sökülür, panel dosyaları silinir (önbellek kalır)
```

## Eşik disiplini
- Parametreler (`PARAM`, `UFUK`, `AGIRLIK`) kodda sabittir.
- Tutma tablosunu gece karnesi yeniden üretir; en az 300 örnek yoksa çekirdekteki varsayılan tablo kullanılır.
- **Canlı defter**, sistem çalışmaya başladıktan sonra tetiklenen sinyalleri ileriye dönük kaydeder (geçmiş sızmaz).
  En az 200 sonuçlanmış canlı sinyal birikmeden eşik değiştirilmemeli.
- Panelin "Karne" sekmesinde geriye dönük test ile canlı sonuç yan yana görünür.

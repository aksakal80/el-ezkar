# Ayrışma · Piyasa Uyumu paneli — kurulum

TradingView'daki **AYRIŞMA + MSB-OB** göstergesinin piyasa uyumu kısmının sunucu
karşılığıdır. Aynı formüllerle çalışır. OTHERS evrenindeki bütün coinleri (ilk 10 ve
hisse/emtia perp'leri hariç) 1 saat, 4 saat ve 1 günde tarar. Her coin için şu soruya cevap
verir: **"Coinin fiyatı, piyasanın (BTC + ETH + majörler) ve paranın altlara akıp akmadığının
(OTHERS.D payı) durumuyla uyumlu mu?"**

Panel adresi (kurulumdan sonra): `https://veri.ayaydin.tr/ayrisma_panel.html`

## Dosyalar ve gidecekleri yer

| Dosya | Sunucudaki yeri | Görevi |
|---|---|---|
| `ayrisma_export.py` | `/root/projelerim/` | Saatte bir hesaplar → `/var/www/veri/ayrisma.json` |
| `ayrisma_panel.html` | `/var/www/veri/` | Paneli gösterir; yalnız `ayrisma.json` + mevcut `/ex/bybit/kline` vekilini okur |
| `ayrisma_export.service` | `/etc/systemd/system/` | Tek geçiş (oneshot) servis, venv: `/root/venv` |
| `ayrisma_export.timer` | `/etc/systemd/system/` | Her saat :02:30'da çalıştırır |

**Yeni oluşan dosyalar:**
- `/var/www/veri/ayrisma.json`: panelin okuduğu çıktı.
- `/root/projelerim/ayrisma_dom_gecmis.json`: saatlik dominans geçmişi (paylar, TOTAL, OTHERS).

**Dokunulmayan mevcut dosyalar:** Hiçbiri değiştirilmez. Aşağıdakiler yalnızca okunur:
- `/var/www/veri/ticker_ws.json` (ws_ticker üretir): coin listesi, 24 saatlik veri, fonlama.
- `/var/www/veri/dom.json` (dom_export üretir): USDT.D, BTC.D, OTHERS.D, ETH.D, TOTAL, OTHERS.

**Ağ:**
- Bybit'in herkese açık kline adresi (`api.bybit.com/v5/market/kline`, saniyede en fazla 8 istek).
- Çalışma başına 1 istek CoinGecko `/api/v3/global`. Yalnızca USDC payı için kullanılır, çünkü
  `dom.json`'da USDC.D yok. Alınamazsa akış yine çalışır; USDC o zaman "diğer ilk-10" içinde kalır.
- Binance fapi'ye hiç istek atılmaz, bu yüzden `.fapi_ban` kapısı gerekmez.

**Bağımlılık:** Yalnızca Python standart kütüphanesi kullanılır; `pip install` gerekmez.

## Kurulum adımları

Bilgisayarınızdan (PowerShell) dosyaları gönderin. Port, anahtar ve IP bilgileriniz
sunucu rehberinizde yazılı; aşağıda `<PORT>`, `<ANAHTAR>`, `<SUNUCU>` olarak geçiyor.

```powershell
scp -P <PORT> -i <ANAHTAR> ayrisma_export.py root@<SUNUCU>:/root/projelerim/
scp -P <PORT> -i <ANAHTAR> ayrisma_panel.html root@<SUNUCU>:/var/www/veri/
scp -P <PORT> -i <ANAHTAR> ayrisma_export.service ayrisma_export.timer root@<SUNUCU>:/etc/systemd/system/
```

Sonra sunucuda:

```bash
# 1) Önce elle bir kez çalıştırın (2–5 dk sürer, ilerlemeyi ekrana yazar)
/root/venv/bin/python3 /root/projelerim/ayrisma_export.py

# 2) Çıktı oluştu mu?
ls -la /var/www/veri/ayrisma.json

# 3) Zamanlayıcıyı açın
systemctl daemon-reload
systemctl enable --now ayrisma_export.timer
systemctl list-timers --no-pager | grep ayrisma
```

Tarayıcıda `https://veri.ayaydin.tr/ayrisma_panel.html` adresini açın. İsterseniz Panel
Merkezi'ne ve Piyasa Panosu'na kendiniz bir bağlantı ekleyin (mevcut sayfalara ben dokunmadım).

## Para akışı nasıl hesaplanır, ilk günler

Dominanslar aynı toplamın **paylarıdır**. Bu yüzden değişimleri yüzde **puan** olarak toplanır;
izleme listenizdeki "Değ" sütunu da budur.

- **Karar OTHERS.D'den verilir.** OTHERS.D'nin 24 saatlik puan değişimi, bu değişimin son 1
  haftadaki olağan büyüklüğüne (σ) bölünür: ≥ +1σ ise para altlara giriyor, ≤ −1σ ise çıkıyor.
- **Derece:** <0,5σ yok denecek kadar az · 0,5–1σ hafif · 1–2σ belirgin · ≥2σ güçlü.
- **Dolar karşılığı:** puan × TOTAL.
- **Nereye / nereden:** Paranın nereye ya da nereden aktığını dört kalem gösterir:
  - stabil payı (USDT.D + USDC.D),
  - ETH.D,
  - BTC.D,
  - diğer ilk-10 (majörler; kalan pay).

  Bu kalemler ayrıca oy vermez, bu yüzden aynı fiyat hareketi iki kez sayılmaz.
- **Sıçrama filtresi:** OTHERS'ın ($) bir saatlik değişiminin alt sepetince açıklanamayan kısmı
  çok büyükse (ilk-10 giriş/çıkışı ya da veri düzeltmesi), o saatteki OTHERS.D değişimi 0 sayılır.
  Panelde ✂ ile gösterilir.

`dom.json` yalnızca anlık değer verir. σ için gereken geçmiş, her çalışmada bir örnek eklenerek
birikir. İlk **~48 saat** kartta "akış ısınıyor" yazar. Bu sürede:
- akış "nötr" sayılır,
- puanlar `dom.json`'daki 24 saatlik değişimden gösterilir (sıçrama filtresiz).

Coin tablosu, piyasa yönü ve alt sepeti ilk çalışmadan itibaren tam çalışır.

## Kontrol ve log

```bash
systemctl status ayrisma_export.service --no-pager
journalctl -u ayrisma_export.service -n 50 --no-pager
```

Her çalışma şöyle bir satırla biter:
`[OK] /var/www/veri/ayrisma.json yazıldı · 180.4 sn · akış: … · OTHERS.D +0.12 puan`

Hata olursa eski `ayrisma.json` bozulmadan kalır ve panel son iyi veriyi göstermeye devam eder.

## Ayarlar (isteğe bağlı)

`ayrisma_export.service` içine `Environment=` satırı ekleyip `systemctl daemon-reload`
komutunu çalıştırın.

| Değişken | Varsayılan | Anlamı |
|---|---|---|
| `AYR_MIN_HACIM` | `3000000` | Taranacak coinin 24 saatlik en az hacmi (USDT) |
| `AYR_HARIC_EK` | boş | Evrenden ayrıca çıkarılacaklar, örn. `ABC,XYZ` (yeni hisse/emtia perp'i görürseniz) |
| `AYR_ISTEK_HIZI` | `8` | Bybit'e saniyede en fazla istek |
| `AYR_PARALEL` | `4` | Aynı anda kaç istek |
| `AYR_USDC_URL` | CoinGecko `/global` | USDC payının alındığı adres; boş bırakılırsa USDC "diğer ilk-10" içinde kalır |

Formül sabitleri dosyanın başındadır (`TF`, `UYUM_BANT`, `PIYASA_ESIK`, `AKIS_*`, `MAJORLER`,
`HARIC`). Değerleri göstergenin varsayılan ayarlarıyla aynıdır:
- **Piyasa:** BTC, ETH ve majörler, her biri ⅓ ağırlıkla. Majörler, BTC ve ETH hariç ilk 10'daki
  stabil olmayan coinlerdir (XRP, BNB, SOL, DOGE, TRX, ADA) ve kendi içinde eşit ağırlıklıdır.
- **Pencereler:** 1 saatte β 168 / ayrışma 24 bar, 4 saatte β 120 / ayrışma 30 bar, 1 günde
  β 100 / ayrışma 14 bar. Günlükte bir coinin taranması için en az ~150 günlük (≈5 ay) geçmiş
  gerekir; daha yeni coinler "kısa geçmiş" sayılır.
- **Eşikler:** uyum bandı ±1σ, piyasa yön eşiği ±0,5σ, akış eşiği ±1σ (OTHERS.D, 24 saat).

İlk 10 sıralaması değişirse `MAJORLER` ve `HARIC` listelerini güncelleyin.

## Geri alma

```bash
systemctl disable --now ayrisma_export.timer
rm /etc/systemd/system/ayrisma_export.service /etc/systemd/system/ayrisma_export.timer
systemctl daemon-reload
rm /root/projelerim/ayrisma_export.py /root/projelerim/ayrisma_dom_gecmis.json
rm /var/www/veri/ayrisma_panel.html /var/www/veri/ayrisma.json
```

## Bilinen sınırlar

- **Altcoin grubu:** Taranan coinlerin bar bar medyan getirisidir, CRYPTOCAP:OTHERS
  değildir. Eşit ağırlıklıdır ve ilk-10 giriş/çıkış sıçraması içermez. Sizin küçük
  coinleriniz için daha temsilidir, ama TradingView'daki OTHERS çizgisiyle birebir aynı değildir.
- **Birleşik AL/SAT ve MSB:** Panelde yok. MSB zaten `msb_ob_bot` tarafından hesaplanıyor.
- **OTHERS.D kaynağı:** Değer `dom.json`'dan (CoinGecko) gelir. TradingView'daki
  CRYPTOCAP:OTHERS.D ile ilk-10 tanımı ve güncellenme zamanı farklı olabilir, bu yüzden
  küçük farklar normaldir.
- **Test:** Hesaplar göstergeyle aynı formüllerdir ve panel ile sunucu aynı sonucu verir
  (ör. HBAR 1s +5,39σ her ikisinde). Karne sinyallerin geçmişteki sonucunu gösterir, ancak
  geleceği garanti etmez.
- **Uyarı:** Yatırım tavsiyesi değildir.

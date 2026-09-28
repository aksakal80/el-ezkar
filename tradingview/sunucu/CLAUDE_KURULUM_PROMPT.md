Sunucuya yeni bir modül kuracağız: **"Ayrışma · Piyasa Uyumu" paneli**. Bu, TradingView'daki
AYRIŞMA + MSB-OB göstergesinin sunucu karşılığı. OTHERS coinlerini 1 saat ve 4 saatte tarar:
"coinin fiyatı majör piyasanın (BTC+ETH) durumuyla uyumlu mu?". Sonucu
`/var/www/veri/ayrisma.json` dosyasına yazar; `ayrisma_panel.html` bu dosyayı gösterir.

Dosyaları **`/root/ayrisma_kurulum/`** klasörüne yükledim:
- `ayrisma_export.py`: saatte bir çalışan hesaplayıcı (yalnız Python standart kütüphanesi)
- `ayrisma_panel.html`: panel
- `ayrisma_export.service`, `ayrisma_export.timer`: systemd birimleri
- `KURULUM.md`: ayrıntılı kılavuz. **Önce bunu oku.**

`kripto-bot-iskeleti` skill'in varsa onu da oku ve kurallarına uy.

## Kesin kurallar

- **Mevcut hiçbir dosyaya dokunma:** bot, servis, panel, JSON, nginx ayarı, crontab dahil.
  Kurulum yalnızca şu YENİ dosyaları oluşturur:
  - `/root/projelerim/ayrisma_export.py`
  - `/var/www/veri/ayrisma_panel.html`
  - `/etc/systemd/system/ayrisma_export.service`
  - `/etc/systemd/system/ayrisma_export.timer`
  - Çalışınca ayrıca: `/var/www/veri/ayrisma.json` ve `/root/projelerim/ayrisma_dom_gecmis.json`
- **Salt-okunur girdiler:** `/var/www/veri/ticker_ws.json` (ws_ticker) ve `/var/www/veri/dom.json` (dom_export).
  Bunları üreten botlara dokunma.
- **Ağ:** Yalnızca `api.bybit.com/v5/market/kline` (herkese açık) kullanılır. Binance fapi'ye istek yoktur.
- **Kodu değiştirme:** Dosyaların içeriğini değiştirme ya da "iyileştirme". Bir sorun görürsen dur ve bana bildir.
- **Çakışma:** Hedeflerde aynı adlı bir dosya zaten varsa üzerine YAZMA, bana sor.

## Adımlar

**1) Ön kontrol (hiçbir şey kopyalamadan önce).** Her birinin sonucunu bana göster.
- `ls -la /root/ayrisma_kurulum/`: 5 dosya var mı?
- Yukarıdaki hedef yolların hiçbiri şu an mevcut olmamalı.
- `/root/venv/bin/python3 --version` → 3.10 veya üstü olmalı.
- `/root/venv/bin/python3 -m py_compile /root/ayrisma_kurulum/ayrisma_export.py` → hatasız olmalı.
- `ticker_ws.json` güncel mi? Yapısı şöyle olmalı: `ticker24` listesi (symbol, lastPrice,
  priceChangePercent, quoteVolume) ve `premiumIndex` listesi (symbol, lastFundingRate).
- `dom.json` güncel mi? Yapısı şöyle olmalı: `updated_at` ve `items` listesi (key: BTC.D,
  ETH.D, USDT.D, OTHERS.D; value, chg24). Yapı farklıysa DUR ve bana bildir.
- Bybit erişimi: `curl -s "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=2"`
  → `"retCode":0` dönmeli.
- Panelin mum vekili çalışıyor mu:
  `curl -s "https://veri.ayaydin.tr/ex/bybit/kline?category=linear&symbol=BTCUSDT&interval=60&limit=2"`
  → `"retCode":0` dönmeli.

**2) Kopyala** (izinler 644):
```bash
install -m 644 /root/ayrisma_kurulum/ayrisma_export.py      /root/projelerim/ayrisma_export.py
install -m 644 /root/ayrisma_kurulum/ayrisma_panel.html     /var/www/veri/ayrisma_panel.html
install -m 644 /root/ayrisma_kurulum/ayrisma_export.service /etc/systemd/system/ayrisma_export.service
install -m 644 /root/ayrisma_kurulum/ayrisma_export.timer   /etc/systemd/system/ayrisma_export.timer
```

**3) Elle bir kez çalıştır ve doğrula:**
```bash
cd /root/projelerim && /root/venv/bin/python3 ayrisma_export.py
```
- 1–3 dakika sürer ve `[OK] /var/www/veri/ayrisma.json yazıldı` satırıyla bitmeli.
  Çıktının tamamını bana göster.
- JSON'u kontrol et: `tf` altında `1h` ve `4h` olmalı; her birinde kaç coin var, `atlanan`
  sayıları kaç? Rejim durumunun "ısınıyor" olması ilk ~36 saat boyunca NORMALDİR.
- `curl -s -o /dev/null -w "%{http_code}\n" https://veri.ayaydin.tr/ayrisma.json` → 200 dönmeli.
- `curl -s -o /dev/null -w "%{http_code}\n" https://veri.ayaydin.tr/ayrisma_panel.html` → 200 dönmeli.

**4) Zamanlayıcıyı aç:**
```bash
systemctl daemon-reload
systemctl enable --now ayrisma_export.timer
systemctl start ayrisma_export.service      # servis üzerinden bir kez dene
journalctl -u ayrisma_export.service -n 30 --no-pager
systemctl list-timers --no-pager | grep ayrisma
```

**5) Evren kontrolü.** `ayrisma.json` içindeki 1h coin listesini (sembolleri) bana göster.
- Hisse, ETF ya da emtia olduğundan şüphelendiğin sembol varsa bana sor. Kod altın, NVDA,
  TSLA, QQQ ve benzerlerini zaten çıkarıyor.
- Onaylarsam `.py` dosyasını DEĞİŞTİRME; şu yolu izle:
  - `ayrisma_export.service` dosyasına `Environment=AYR_HARIC_EK=SEMBOL1,SEMBOL2` satırını
    ekle (bu yeni dosya, dokunulabilir).
  - `systemctl daemon-reload` ve `systemctl start ayrisma_export.service` çalıştır.

**6) Panel Merkezi'ne bağlantı EKLEME.** Önce bana sor. Onaylarsam önce yedek al
(`/root/backups/`), sonra Panel Merkezi sayfasına tek satırlık bir
`<a href="ayrisma_panel.html">🧭 Ayrışma · Piyasa Uyumu</a>` bağlantısı ekle.

**7) Rapor.** Şunları özetle:
- Hangi dosya nereye kondu.
- İlk çalışmanın süresi.
- 1h ve 4h'de hesaplanan coin sayısı; hata ve kısa geçmiş sayıları.
- Timer'ın bir sonraki çalışma zamanı.
- Karşılaşılan uyarılar.
- Panel adresi: https://veri.ayaydin.tr/ayrisma_panel.html

Ayrıca `/root/BOTLAR.md` ve `/root/SISTEM/KRIPTO_BOTLAR.md` dosyalarına yeni modülün
(servis, timer, girdi/çıktı dosyaları) eklenmesini öner, ama ben onaylamadan yazma.

**Geri alma** gerekirse `KURULUM.md` dosyasındaki "Geri alma" bölümünü uygula.

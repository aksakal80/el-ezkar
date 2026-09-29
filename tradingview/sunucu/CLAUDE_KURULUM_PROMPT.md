Sunucuya **"Ayrışma · Piyasa Uyumu" paneli** modülünü kuracağız ya da güncelleyeceğiz. Bu,
TradingView'daki AYRIŞMA + MSB-OB göstergesinin sunucu karşılığı. OTHERS coinlerini 1 saat,
4 saat ve 1 günde tarar ve şu soruyu cevaplar: "coinin fiyatı piyasanın (BTC + ETH + majörler) ve para
akışının (OTHERS.D payı) durumuyla uyumlu mu?". Sonucu `/var/www/veri/ayrisma.json` dosyasına
yazar; `ayrisma_panel.html` bu dosyayı gösterir.

Dosyaları **`/root/ayrisma_kurulum/`** klasörüne yükledim:
- `ayrisma_export.py`: saatte bir çalışan hesaplayıcı (yalnız Python standart kütüphanesi)
- `ayrisma_panel.html`: panel
- `ayrisma_export.service`, `ayrisma_export.timer`: systemd birimleri
- `KURULUM.md`: ayrıntılı kılavuz. **Önce bunu oku.**

`kripto-bot-iskeleti` skill'in varsa onu da oku ve kurallarına uy.

## Kesin kurallar

- **Mevcut hiçbir dosyaya dokunma:** bot, servis, panel, JSON, nginx ayarı, crontab dahil.
  Bu modülün dosyaları yalnızca şunlardır:
  - `/root/projelerim/ayrisma_export.py`
  - `/var/www/veri/ayrisma_panel.html`
  - `/etc/systemd/system/ayrisma_export.service`
  - `/etc/systemd/system/ayrisma_export.timer`
  - Çalışınca ayrıca: `/var/www/veri/ayrisma.json` ve `/root/projelerim/ayrisma_dom_gecmis.json`
- **Salt-okunur girdiler:** `/var/www/veri/ticker_ws.json` (ws_ticker) ve `/var/www/veri/dom.json` (dom_export).
  Bunları üreten botlara dokunma.
- **Ağ:**
  - Bybit: yalnızca `api.bybit.com/v5/market/kline` (herkese açık).
  - CoinGecko: çalışma başına 1 istek `api.coingecko.com/api/v3/global` (yalnız USDC payı için).
  - Binance fapi'ye istek yoktur.
- **Kodu değiştirme:** Dosyaların içeriğini değiştirme ya da "iyileştirme". Bir sorun görürsen dur ve bana bildir.
- **Çakışma:** Yukarıdaki listede OLMAYAN bir dosyanın üzerine asla yazma.

## Adımlar

**1) Ön kontrol (hiçbir şey kopyalamadan önce).** Her birinin sonucunu bana göster.
- `ls -la /root/ayrisma_kurulum/`: 5 dosya var mı?
- **Yeni kurulum mu, güncelleme mi?** Hedef yollara bak:
  - Hiçbiri yoksa → **yeni kurulum**.
  - Hepsi varsa ve `/root/projelerim/ayrisma_export.py` dosyasının başında "ayrisma_export.py —
    AYRIŞMA · Piyasa Uyumu tarayıcısı" yazıyorsa → **güncelleme**. Bu dosyalar bu modülün
    önceki sürümüdür.
  - Başka bir durum varsa (bazıları var, bazıları yok ya da içerik farklı) → DUR ve bana sor.
- `/root/venv/bin/python3 --version` → 3.10 veya üstü olmalı.
- `/root/venv/bin/python3 -m py_compile /root/ayrisma_kurulum/ayrisma_export.py` → hatasız olmalı.
- `ticker_ws.json` güncel mi? Yapısı şöyle olmalı:
  - `ticker24` listesi (symbol, lastPrice, priceChangePercent, quoteVolume),
  - `premiumIndex` listesi (symbol, lastFundingRate).
- `dom.json` güncel mi? Yapısı şöyle olmalı:
  - `updated_at` alanı,
  - `items` listesi (key ve value). Anahtarlar: BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL, OTHERS.
    Dominanslarda `chg24` de olmalı.

  Yapı farklıysa DUR ve bana bildir.
- Bybit erişimi: `curl -s "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=2"`
  → `"retCode":0` dönmeli.
- CoinGecko erişimi: `curl -s https://api.coingecko.com/api/v3/global | head -c 300`
  → `market_cap_percentage` görünmeli. Görünmezse kuruluma devam et ve bunu raporda belirt;
  USDC o zaman "diğer ilk-10" içinde kalır.
- Panelin mum vekili çalışıyor mu:
  `curl -s "https://veri.ayaydin.tr/ex/bybit/kline?category=linear&symbol=BTCUSDT&interval=60&limit=2"`
  → `"retCode":0` dönmeli.

**2a) Yeni kurulumsa kopyala** (izinler 644):
```bash
install -m 644 /root/ayrisma_kurulum/ayrisma_export.py      /root/projelerim/ayrisma_export.py
install -m 644 /root/ayrisma_kurulum/ayrisma_panel.html     /var/www/veri/ayrisma_panel.html
install -m 644 /root/ayrisma_kurulum/ayrisma_export.service /etc/systemd/system/ayrisma_export.service
install -m 644 /root/ayrisma_kurulum/ayrisma_export.timer   /etc/systemd/system/ayrisma_export.timer
```

**2b) Güncellemeyse önce yedek al, sonra kopyala:**
```bash
systemctl stop ayrisma_export.timer
Y=/root/backups/ayrisma_$(date +%Y%m%d_%H%M) && mkdir -p $Y
cp -a /root/projelerim/ayrisma_export.py /var/www/veri/ayrisma_panel.html \
      /etc/systemd/system/ayrisma_export.service /etc/systemd/system/ayrisma_export.timer $Y/
cp -a /root/projelerim/ayrisma_dom_gecmis.json $Y/ 2>/dev/null || true
```
- Mevcut `ayrisma_export.service` dosyasında benim eklediğim bir `Environment=` satırı var mı
  (ör. `AYR_HARIC_EK`)? Varsa bana göster ve o satırı yeni service dosyasına da ekle.
- Sonra 2a'daki dört `install` komutunu çalıştır.
- `ayrisma_dom_gecmis.json` dosyasını SİLME. Eski örnekler yeni sürümle uyumludur ve ısınmayı
  kısaltır.

**3) Elle bir kez çalıştır ve doğrula:**
```bash
systemctl daemon-reload
cd /root/projelerim && /root/venv/bin/python3 ayrisma_export.py
```
- 2–5 dakika sürer ve `[OK] /var/www/veri/ayrisma.json yazıldı` satırıyla bitmeli.
  Çıktının tamamını bana göster.
- JSON'u kontrol et:
  - `tf` altında `1h`, `4h` ve `1d` olmalı.
  - Her birinde kaç coin var, `atlanan` sayıları kaç?
  - `rejim` altındaki şu alanları bana göster: `durum`, `akis`, `oth`, `kalemler`, `nereye`,
    `kaynak`, `usdc`.
- Para akışının "ısınıyor" olması ilk ~48 saat boyunca NORMALDİR.
- `curl -s -o /dev/null -w "%{http_code}\n" https://veri.ayaydin.tr/ayrisma.json` → 200 dönmeli.
- `curl -s -o /dev/null -w "%{http_code}\n" https://veri.ayaydin.tr/ayrisma_panel.html` → 200 dönmeli.

**4) Zamanlayıcıyı aç:**
```bash
systemctl enable --now ayrisma_export.timer
systemctl start ayrisma_export.service      # servis üzerinden bir kez dene
journalctl -u ayrisma_export.service -n 30 --no-pager
systemctl list-timers --no-pager | grep ayrisma
```

**5) Evren kontrolü.** `ayrisma.json` içindeki 1h coin listesini (sembolleri) bana göster.
- Hisse, ETF ya da emtia olduğundan şüphelendiğin sembol varsa bana sor. Kod altın, NVDA,
  TSLA, QQQ ve benzerlerini zaten çıkarıyor.
- Onaylarsam `.py` dosyasını DEĞİŞTİRME; şu yolu izle:
  - `ayrisma_export.service` dosyasına `Environment=AYR_HARIC_EK=SEMBOL1,SEMBOL2` satırını ekle.
    Bu dosya bu modüle ait olduğu için dokunulabilir.
  - `systemctl daemon-reload` ve `systemctl start ayrisma_export.service` çalıştır.

**6) Panel Merkezi'ne bağlantı EKLEME.** Önce bana sor. Bağlantı zaten eklenmişse bu adımı atla.
Onaylarsam:
- önce `/root/backups/` altına yedek al,
- sonra Panel Merkezi sayfasına tek satırlık bir
  `<a href="ayrisma_panel.html">🧭 Ayrışma · Piyasa Uyumu</a>` bağlantısı ekle.

**7) Rapor.** Şunları özetle:
- Yeni kurulum mu, güncelleme mi yapıldı; güncellemeyse yedeğin yeri.
- Hangi dosya nereye kondu.
- İlk çalışmanın süresi.
- 1h, 4h ve 1d'de hesaplanan coin sayısı; hata ve kısa geçmiş sayıları. (1d'de ~5 aydan
  yeni coinler "kısa geçmiş" sayılır, bu normaldir.)
- Para akışı satırı: OTHERS.D puanı, durum, nereye/nereden.
- CoinGecko'dan USDC payı alınabildi mi?
- Timer'ın bir sonraki çalışma zamanı.
- Karşılaşılan uyarılar.
- Panel adresi: https://veri.ayaydin.tr/ayrisma_panel.html

Ayrıca `/root/BOTLAR.md` ve `/root/SISTEM/KRIPTO_BOTLAR.md` dosyalarına yeni modülün (servis,
timer, girdi/çıktı dosyaları) eklenmesini ya da güncellenmesini öner, ama ben onaylamadan yazma.

**Geri alma** gerekirse:
- Güncellemede: yedek klasöründeki dosyaları geri kopyala, sonra `systemctl daemon-reload`.
- Yeni kurulumda: `KURULUM.md` dosyasındaki "Geri alma" bölümünü uygula.

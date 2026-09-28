# Ayrışma · Piyasa Uyumu paneli — kurulum

TradingView'daki **AYRIŞMA + MSB-OB** göstergesinin piyasa uyumu kısmının sunucu
karşılığıdır. Aynı formüllerle çalışır. OTHERS evrenindeki bütün coinleri (ilk 10 ve
hisse/emtia perp'leri hariç) 1 saat ve 4 saatte tarar. Her coin için şu soruya cevap
verir: **"Coinin fiyatı, majör piyasanın (BTC + ETH) durumuyla uyumlu mu?"**

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
- `/root/projelerim/ayrisma_dom_gecmis.json`: saatlik dominans geçmişi.

**Dokunulmayan mevcut dosyalar:** Hiçbiri değiştirilmez. Aşağıdakiler yalnızca okunur:
- `/var/www/veri/ticker_ws.json` (ws_ticker üretir): coin listesi, 24 saatlik veri, fonlama.
- `/var/www/veri/dom.json` (dom_export üretir): USDT.D, BTC.D, OTHERS.D, ETH.D.

**Ağ:** Yalnızca Bybit'in herkese açık kline adresi kullanılır (`api.bybit.com/v5/market/kline`,
saniyede en fazla 8 istek). Binance fapi'ye hiç istek atılmadığı için `.fapi_ban` kapısı gerekmez.

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
# 1) Önce elle bir kez çalıştırın (1–3 dk sürer, ilerlemeyi ekrana yazar)
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

## İlk günler: dominans rejiminin ısınması

`dom.json` yalnızca anlık değer veriyor. Bu yüzden rejim hesabı (12 saatlik değişimin
kendi oynaklığına oranı) için geçmiş, çalışma başına bir örnekle biriktirilir. Yaklaşık
**36 saat** boyunca rejim kartında "Isınıyor" yazar; bu sürede oylar sayılmaz ve mevcut
dominans panelinizin yorumu gösterilir. Coin tablosu, piyasa yönü ve altcoin grubu ilk
çalışmadan itibaren tam çalışır.

## Kontrol ve log

```bash
systemctl status ayrisma_export.service --no-pager
journalctl -u ayrisma_export.service -n 50 --no-pager
```

Her çalışma şöyle bir satırla biter:
`[OK] /var/www/veri/ayrisma.json yazıldı · 75.2 sn · rejim: …`

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

Formül sabitleri dosyanın başında (`TF`, `UYUM_BANT`, `PIYASA_ESIK`, `DOM_OY`, `HARIC`)
ve göstergenin varsayılan ayarlarıyla aynıdır:
- Majör sepeti: BTC 0,70 + ETH 0,30.
- Pencereler: 1 saatte β 168 / ayrışma 24 bar, 4 saatte β 120 / ayrışma 30 bar.
- Eşikler: uyum bandı ±1σ, piyasa yön eşiği ±0,5σ, OTHERS.D 2 oy.

İlk 10 sıralaması değişirse `HARIC` listesini güncelleyin.

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
- **Test:** Hesaplar göstergeyle aynı formüllerdir ve panel ile sunucu aynı sonucu verir
  (ör. HBAR 1s +5,38σ her ikisinde). Ancak sinyallerin başarısı gerçek veride test edilmedi.
- **Uyarı:** Yatırım tavsiyesi değildir.

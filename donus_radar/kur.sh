#!/usr/bin/env bash
# Dönüş Radarı kurulumu -- sunucuda root olarak, bu klasörün içinden:  bash kur.sh
# Mevcut botlara dokunmaz. rsi_grafik.html'i değiştirmeden önce /root/backups'a yedekler.
set -euo pipefail
HEDEF=/root/sinyal/donus_radar
VERI=/var/www/veri
PY=/root/sinyal/venv/bin/python3
KAYNAK="$(cd "$(dirname "$0")" && pwd)"
TARIH=$(date +%Y%m%d_%H%M%S)

[ -x "$PY" ] || { echo "HATA: $PY bulunamadı (sinyal venv'i)"; exit 1; }
[ -f "$VERI/turev_radar.json" ] || echo "UYARI: $VERI/turev_radar.json yok; evren ticker_ws.json'dan alınacak"
mkdir -p "$HEDEF" /root/backups

echo "1) Kod -> $HEDEF"
cp "$KAYNAK"/donus_cekirdek.py "$KAYNAK"/donus_radar.py "$KAYNAK"/donus_karne.py "$KAYNAK"/rsi_grafik_yama.py "$HEDEF"/

echo "2) Panel -> $VERI/donus_panel.html"
[ -f "$VERI/donus_panel.html" ] && cp "$VERI/donus_panel.html" "/root/backups/donus_panel.html.bak_$TARIH"
cp "$KAYNAK/donus_panel.html" "$VERI/donus_panel.html"
chmod 644 "$VERI/donus_panel.html"

echo "3) systemd birimleri"
cp "$KAYNAK"/systemd/donus_*.service "$KAYNAK"/systemd/donus_*.timer /etc/systemd/system/
systemctl daemon-reload

echo "4) İlk veri çekimi + geçmiş doldurma (tek sefer, 20-40 dk sürebilir; Binance FAPI kullanılmaz)"
"$PY" "$HEDEF/donus_radar.py" --derin
echo "5) İlk karne (geriye dönük test)"
"$PY" "$HEDEF/donus_karne.py"
"$PY" "$HEDEF/donus_radar.py" --sadece-analiz

echo "6) rsi_grafik.html'e Dönüş Radarı ekleniyor (yedek /root/backups)"
"$PY" "$HEDEF/rsi_grafik_yama.py" "$VERI/rsi_grafik.html"

echo "7) Zamanlayıcılar"
systemctl enable --now donus_radar.timer donus_karne.timer donus_derin.timer
systemctl list-timers --no-pager | grep donus || true
echo
echo "Bitti. Panel: https://veri.ayaydin.tr/donus_panel.html"
echo "Grafik:  https://veri.ayaydin.tr/rsi_grafik.html?liste=donus"

#!/usr/bin/env bash
# Dönüş Radarı'nı kaldırır: zamanlayıcılar durur, rsi_grafik.html'den Dönüş blokları sökülür, panel dosyaları silinir.
# Önbellek (/root/sinyal/donus_radar/donus.db) silinmez; istersen elle sil.
set -uo pipefail
HEDEF=/root/sinyal/donus_radar
VERI=/var/www/veri
PY=/root/sinyal/venv/bin/python3
systemctl disable --now donus_radar.timer donus_karne.timer donus_derin.timer 2>/dev/null
rm -f /etc/systemd/system/donus_radar.{service,timer} /etc/systemd/system/donus_karne.{service,timer} /etc/systemd/system/donus_derin.{service,timer}
systemctl daemon-reload
[ -f "$HEDEF/rsi_grafik_yama.py" ] && "$PY" "$HEDEF/rsi_grafik_yama.py" "$VERI/rsi_grafik.html" --sok
rm -f "$VERI/donus_panel.html" "$VERI/donus_radar.json" "$VERI/donus_karne.json"
echo "Kaldırıldı. rsi_grafik.html yedekleri: /root/backups/rsi_grafik.html.bak_*"

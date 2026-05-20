#!/bin/bash
# ============================================================================
# ORÓMA – Deploy Script (systemd + nginx + cron + logrotate)
# Pfad: /opt/ai/oroma/deploy_all.sh
# ============================================================================
set -e

BASE="/opt/ai/oroma"
SYSTEMD_DIR="/etc/systemd/system"
NGINX_DIR="/etc/nginx/sites-enabled"
LOGROTATE_DIR="/etc/logrotate.d"

echo "==> Installiere systemd-Dienst"
sudo cp "$BASE/systemd/oroma.service" "$SYSTEMD_DIR/oroma.service"
sudo systemctl daemon-reload
sudo systemctl enable oroma.service
sudo systemctl restart oroma.service

echo "==> Logrotate konfigurieren"
sudo tee "$LOGROTATE_DIR/oroma" >/dev/null <<EOF
$BASE/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF

echo "==> Deployment abgeschlossen."
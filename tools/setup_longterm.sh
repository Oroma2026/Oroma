#!/usr/bin/env bash
# =============================================================================
# Datei: /opt/ai/oroma/tools/setup_longterm.sh
# Zweck: Aktiviert ORÓMA Langzeitspeicher (DreamWorker + Archiv)
# =============================================================================
set -e

echo "🔍 Prüfe ORÓMA-Langzeitumgebung..."
cd /opt/ai/oroma

# Systemd reloaden
echo "↻ Lade systemd neu..."
sudo systemctl daemon-reload

# Sicherstellen, dass Logs existieren
mkdir -p /opt/ai/oroma/logs

# Relevante Timer aktivieren
echo "🧠 Aktiviere DreamWorker- und Archiv-Timer..."
sudo systemctl enable --now oroma-dream.timer || true
sudo systemctl enable --now oroma-archive.timer || true

# Hauptservice sicherstellen
sudo systemctl enable --now oroma.service || true

echo "⏳ Starte DreamWorker einmalig zum Test..."
sudo systemctl start oroma-dream.service
sleep 5

echo "📜 Letzte Log-Zeilen:"
sudo journalctl -u oroma-dream.service -n 10 --no-pager

echo "🧩 Tabellenprüfung:"
sqlite3 /opt/ai/oroma/data/oroma.db "SELECT name, count(*) FROM sqlite_master, snapchains LIMIT 1;" 2>/dev/null || echo "ℹ️ Noch keine SnapChains importiert."

echo "✅ Fertig. DreamWorker-Timer ist aktiv und startet alle 30 Minuten."
echo "Status prüfen mit: systemctl status oroma-dream.timer"
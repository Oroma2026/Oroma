<!-- /opt/ai/oroma/v2.30/systemd/README-systemd.md -->
# ORÓMA v2.30 – systemd

## Installation
```bash
# Service- und Timer-Dateien nach /etc/systemd/system kopieren
sudo cp /opt/ai/oroma/v2.30/systemd/*.service /etc/systemd/system/
sudo cp /opt/ai/oroma/v2.30/systemd/*.timer   /etc/systemd/system/

# systemd neu einlesen
sudo systemctl daemon-reload

# Hauptdienst aktivieren und sofort starten
sudo systemctl enable --now oroma.service

# Zusätzliche Timer aktivieren (optional, je nach Bedarf)
sudo systemctl enable --now oroma-health.timer
sudo systemctl enable --now oroma-replay.timer
sudo systemctl enable --now oroma-archive.timer
sudo systemctl enable --now oroma-exportgate.timer


#journalctl -u oroma.service -f
#sudo systemctl start oroma.service
#sudo systemctl stop oroma.service
#sudo systemctl restart oroma.service


Übersicht für ORÓMA v3.5 systemd-Units:

⸻

✅ Pflicht-Services (immer gebraucht)

Diese müssen laufen, sonst startet das Projekt nicht korrekt:
	•	oroma.service → Hauptprozess (Flask-UI + Core).
	•	oroma-replay.service + oroma-replay.timer → für stündliches Replay der SnapChains.
	•	oroma-export.service + oroma-export.timer → täglicher ExportGate-Lauf (Qualität + 30-Tage-Regel).

⸻

ℹ️ Optionale, aber empfohlen
	•	oroma-health.service + oroma-health.timer
→ Healthchecks alle 5 Minuten, schreibt JSON/Logs. Praktisch fürs Monitoring, aber nicht zwingend.
	•	oroma-archive.service + oroma-archive.timer
→ monatliches Backup/Archivieren. Sollte aktiv sein, wenn du automatische Backups willst.
	•	oroma-selftest.service + oroma-selftest.timer
→ täglicher Selftest (DB-Integrität, Wrapper-Erreichbarkeit, UI-Endpoints). Sehr nützlich, aber auch hier optional.

⸻

💤 Komplett optional

(werden nur gebraucht, wenn du diese Features nutzen willst)
	•	oroma-nginx.service (falls du NGINX als Proxy über systemd steuern willst; sonst ignorieren).
	•	oroma-picar.service (nur relevant, wenn das PiCar-Modul mit Safety aktiv ist).
	•	oroma-video.service (falls du Kamera-Streams separat haben willst)
#!/usr/bin/env bash
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/cleanup_metrics.sh
# Projekt: ORÓMA
# Version: v3.7+ (DB-Bereinigung Health-/System-Metriken)
# Stand:   2025-10-21
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Dieses Skript entfernt nicht lernrelevante Health-/Diagnose-Daten
#   aus der SQLite-Datenbank (metrics-Tabelle).
#
#   Typische Einträge:
#       • coverage, novelty, confidence, time_to_goal_norm
#       • health:*, sys:*
#
#   Die Reinigung hält die Datenbank schlank und verbessert Performance.
#
# Ablauf
# ──────
#   1. Öffnet /opt/ai/oroma/data/oroma.db
#   2. Löscht Health-/Systemmetriken
#   3. Führt VACUUM zur Speicherfreigabe aus
#   4. Protokolliert Ergebnis in /opt/ai/oroma/logs/cleanup_metrics.log
#
# Automatisierung
# ───────────────
#   Füge via Cron oder Systemd-Timer ein:
#       0 3 * * * /opt/ai/oroma/tools/cleanup_metrics.sh
# =============================================================================

DB_PATH="/opt/ai/oroma/data/oroma.db"
LOG_DIR="/opt/ai/oroma/logs"
LOG_FILE="${LOG_DIR}/cleanup_metrics.log"
DATE="$(date '+%Y-%m-%d %H:%M:%S')"

mkdir -p "$LOG_DIR"

echo "[$DATE] --- ORÓMA Metrics Cleanup gestartet ---" >> "$LOG_FILE"

if [ ! -f "$DB_PATH" ]; then
    echo "[$DATE] ❌ Datenbank nicht gefunden: $DB_PATH" >> "$LOG_FILE"
    exit 1
fi

sqlite3 "$DB_PATH" <<'SQL' >>"$LOG_FILE" 2>&1
DELETE FROM metrics
 WHERE key IN ('coverage','novelty','confidence','time_to_goal_norm')
    OR key LIKE 'health:%'
    OR key LIKE 'sys:%';
VACUUM;
SQL

if [ $? -eq 0 ]; then
    echo "[$DATE] ✅ Bereinigung erfolgreich abgeschlossen." >> "$LOG_FILE"
else
    echo "[$DATE] ⚠️ Fehler beim Bereinigen der metrics-Tabelle." >> "$LOG_FILE"
fi

echo "[$DATE] --- ORÓMA Metrics Cleanup beendet ---" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
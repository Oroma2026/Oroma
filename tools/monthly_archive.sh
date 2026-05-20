#!/usr/bin/env bash
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/monthly_archive.sh
# Projekt: ORÓMA v3.5
# Zweck:   Monatliche Archivierung (DB, Modelle, Exporte, Logs)
# Stand:   2025-09-21
# =============================================================================
set -euo pipefail

# --- Basis & ENV laden ------------------------------------
BASE="/opt/ai/oroma"
EXPORT_DIR="$BASE/exports"
ARCHIVE_DIR="$BASE/archives"
DB_DIR="$BASE/data"
LOG_DIR="$BASE/logs"

TS="$(date +%Y-%m-%d_%H-%M-%S)"
ARCHIVE_NAME="oroma_archive_$TS.tar.gz"
ARCHIVE_PATH="$ARCHIVE_DIR/$ARCHIVE_NAME"

mkdir -p "$EXPORT_DIR" "$ARCHIVE_DIR" "$DB_DIR" "$LOG_DIR"

echo "[oroma-archive] Starte Archivierung: $TS"

# --- DBs sichern ------------------------------------------
echo "[oroma-archive] Sichere Datenbanken..."
cp "$DB_DIR/oroma.db"     "$DB_DIR/oroma_${TS}.bak"     || true
cp "$DB_DIR/knowledge.db" "$DB_DIR/knowledge_${TS}.bak" || true

# --- Archiv erstellen -------------------------------------
echo "[oroma-archive] Erstelle Archiv: $ARCHIVE_PATH"
tar -czf "$ARCHIVE_PATH" \
    -C "$EXPORT_DIR" . \
    -C "$DB_DIR" oroma.db knowledge.db \
    -C "$LOG_DIR" .

# --- Abschlussmeldung -------------------------------------
echo "[oroma-archive] Archivierung abgeschlossen."
echo "[oroma-archive] Archiv: $ARCHIVE_PATH"
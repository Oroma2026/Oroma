#!/usr/bin/env bash
# =============================================================================
# ORÓMA v3.5 – Init Script für Datenbanken
# Pfad:    /opt/ai/oroma/tools/init_db.sh
# Zweck:
#   - Löscht bestehende Datenbanken (oroma.db, knowledge.db)
#   - Initialisiert sie mit tools/init_db.sql
# =============================================================================

set -e

BASE_DIR="/opt/ai/oroma"
DATA_DIR="$BASE_DIR/data"
SCHEMA_FILE="$BASE_DIR/tools/init_db.sql"

OROMA_DB="$DATA_DIR/oroma.db"
KNOWLEDGE_DB="$DATA_DIR/knowledge.db"

# -----------------------------------------------------------------------------#
echo "[init_db] Starte Initialisierung von ORÓMA v3.5"

# Datenverzeichnis sicherstellen
mkdir -p "$DATA_DIR"

# Alte Datenbanken verschieben (Backup mit Zeitstempel)
TS=$(date +"%Y%m%d_%H%M%S")
for DB in "$OROMA_DB" "$KNOWLEDGE_DB"; do
  if [ -f "$DB" ]; then
    echo "[init_db] Bestehende DB gefunden: $DB → Backup"
    mv "$DB" "${DB}.bak_${TS}"
  fi
done

# Schema anwenden
echo "[init_db] Wende Schema an → $OROMA_DB"
sqlite3 "$OROMA_DB" < "$SCHEMA_FILE"

echo "[init_db] Wende Schema an → $KNOWLEDGE_DB"
sqlite3 "$KNOWLEDGE_DB" < "$SCHEMA_FILE"

echo "[init_db] Fertig. Beide Datenbanken sind frisch erstellt."
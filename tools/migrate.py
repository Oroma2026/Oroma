#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/migrate.py
# Projekt: ORÓMA
# Version: v3.5
# Stand:   2025-09-21
#
# Zweck:
#   Führt Migrationen (SQL-Skripte in /tools/) gegen die SQLite-Datenbanken aus:
#     - /opt/ai/oroma/data/oroma.db
#     - /opt/ai/oroma/data/knowledge.db
#
# Features:
#   - Erkennt doppelte Migrationen über migrations-Tabelle
#   - Lädt alle *.sql Dateien aus tools/, sortiert nach Name
#   - Protokolliert in migrations (id, name, applied_at)
#   - Sicheres Logging auf stdout
# =============================================================================

import os
import sqlite3
import glob
import sys
from datetime import datetime

BASE_DIR = "/opt/ai/oroma"
DATA_DIR = os.path.join(BASE_DIR, "data")
TOOLS_DIR = os.path.join(BASE_DIR, "tools")

OROMA_DB = os.path.join(DATA_DIR, "oroma.db")
KNOWLEDGE_DB = os.path.join(DATA_DIR, "knowledge.db")


def apply_migration(db_path: str, sql_file: str):
    """ wendet eine Migration auf eine SQLite-DB an """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # migrations-Tabelle sicherstellen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    mig_name = os.path.basename(sql_file)

    # Prüfen ob schon angewendet
    cur.execute("SELECT 1 FROM migrations WHERE name = ?", (mig_name,))
    if cur.fetchone():
        print(f"[skip] {mig_name} bereits angewendet auf {os.path.basename(db_path)}")
        conn.close()
        return

    # SQL aus Datei laden
    with open(sql_file, "r", encoding="utf-8") as f:
        sql = f.read()

    print(f"[run]  {mig_name} → {os.path.basename(db_path)}")
    try:
        cur.executescript(sql)
        cur.execute("INSERT INTO migrations (name, applied_at) VALUES (?, ?)",
                    (mig_name, datetime.utcnow().isoformat()))
        conn.commit()
        print(f"[ok]   {mig_name} angewendet auf {os.path.basename(db_path)}")
    except Exception as e:
        print(f"[err]  Fehler bei {mig_name} auf {os.path.basename(db_path)}: {e}")
    finally:
        conn.close()


def run_migrations():
    sql_files = sorted(glob.glob(os.path.join(TOOLS_DIR, "migrate_*.sql")))
    if not sql_files:
        print("[info] Keine Migrationen gefunden (tools/migrate_*.sql)")
        return

    for db_path in (OROMA_DB, KNOWLEDGE_DB):
        if not os.path.exists(db_path):
            print(f"[warn] DB fehlt: {db_path} – bitte init_db.sh ausführen")
            continue
        for sql_file in sql_files:
            apply_migration(db_path, sql_file)


if __name__ == "__main__":
    run_migrations()
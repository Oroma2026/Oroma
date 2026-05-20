#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/db_cleanup_drop_empathy_log.py
# Projekt: ORÓMA – DB Cleanup Tool (headless)
# Version: v1.0
# Stand:   2025-12-26
# Autor:   ORÓMA · KI-JWG-X1 (Jörg) + GPT-5.2 Thinking
#
# Zweck
# ─────
#   Entfernt (optional) die Altlast-Tabelle `empathy_log` aus der produktiven
#   ORÓMA SQLite-DB.
#
# Hintergrund
# ──────────
#   In der aktuellen ORÓMA-Linie wird Empathie/Stimmung produktiv über
#   `empathy_snaps(ts, mood, score)` (core/sql_manager.py) geloggt.
#   Die historische Tabelle `empathy_log` (delta_val/delta_arousal) ist im
#   aktuellen Snapshot leer und wird von den produktiven Pipelines nicht mehr
#   benötigt.
#
# Sicherheit / Non-Destructive
# ────────────────────────────
#   • Standardmäßig wird NUR gelöscht, wenn `empathy_log` existiert UND leer ist.
#   • Vor jedem Drop wird ein Backup der DB angelegt:
#       <db>.bak_empathylog_YYYYMMDD_HHMMSS
#   • Wenn `empathy_log` Datensätze enthält, bricht das Tool ab – außer `--force`.
#
# Wichtige Hinweise für den Live-Betrieb
# ──────────────────────────────────────
#   • Empfohlen: ORÓMA-Dienste kurz stoppen, damit keine Schreibkonflikte auftreten.
#   • Die DB kann WAL nutzen (je nach ENV). Backup per Datei-Kopie ist in der Regel
#     ausreichend, aber für maximale Sicherheit: Services stoppen.
#
# Optionen / CLI
# ──────────────
#   --db PATH        DB-Pfad (Default: core.sql_manager.get_db_path())
#   --force          Drop auch wenn Tabelle NICHT leer ist
#   --vacuum         Nach Drop VACUUM ausführen (kann dauern; optional)
#   --dry-run        Zeigt nur, was passieren würde (kein Backup, kein Drop)
#
# Beispiele
# ─────────
#   PYTHONPATH=/opt/ai/oroma python3 tools/db_cleanup_drop_empathy_log.py --dry-run
#   PYTHONPATH=/opt/ai/oroma python3 tools/db_cleanup_drop_empathy_log.py
#   PYTHONPATH=/opt/ai/oroma python3 tools/db_cleanup_drop_empathy_log.py --force --vacuum
# =============================================================================

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import time
from typing import Optional
import logging
from core.log_guard import log_suppressed

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


def _default_db_path() -> str:
    if sql_manager is not None and hasattr(sql_manager, "get_db_path"):
        try:
            return str(sql_manager.get_db_path())  # type: ignore[attr-defined]
        except Exception as e:
            log_suppressed('tools/db_cleanup_drop_empathy_log.py:69', exc=e, level=logging.WARNING)
            pass
    return "/opt/ai/oroma/data/oroma.db"


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone()
    return bool(row)


def _table_count(conn: sqlite3.Connection, name: str) -> Optional[int]:
    try:
        cur = conn.execute(f"SELECT COUNT(*) AS c FROM {name}")
        r = cur.fetchone()
        if not r:
            return 0
        # row kann tuple oder dict sein
        if isinstance(r, dict):
            return int(r.get("c", 0))
        return int(r[0])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA DB Cleanup: drop empathy_log (legacy)")
    ap.add_argument("--db", default=_default_db_path(), help="Pfad zur oroma.db (Default: sql_manager.get_db_path())")
    ap.add_argument("--force", action="store_true", help="Drop auch wenn empathy_log nicht leer ist")
    ap.add_argument("--vacuum", action="store_true", help="Nach DROP: VACUUM ausführen (kann dauern)")
    ap.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts ändern")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"[cleanup] ❌ DB nicht gefunden: {db_path}")
        return 2

    # Verbindung – bewusst ohne sql_manager, damit dieses Tool auch allein läuft.
    conn = sqlite3.connect(db_path)
    try:
        # RowFactory optional (nur für Debug/Count)
        conn.row_factory = sqlite3.Row

        if not _table_exists(conn, "empathy_log"):
            print("[cleanup] ✅ empathy_log existiert nicht – nichts zu tun.")
            return 0

        n = _table_count(conn, "empathy_log")
        if n is None:
            print("[cleanup] ❌ Konnte COUNT(*) für empathy_log nicht ermitteln – Abbruch (Safety).")
            return 3

        print(f"[cleanup] empathy_log gefunden. rows={n}")

        if (n > 0) and (not args.force):
            print("[cleanup] ⚠️ empathy_log ist NICHT leer. Abbruch (ohne --force).")
            return 4

        backup_path = f"{db_path}.bak_empathylog_{_ts()}"
        if args.dry_run:
            print("[cleanup] DRY-RUN:")
            print(f"  - würde Backup erstellen: {backup_path}")
            print("  - würde DROP TABLE empathy_log ausführen")
            if args.vacuum:
                print("  - würde VACUUM ausführen")
            return 0

        # Backup (Datei-Kopie) – für maximale Sicherheit: Services vorher stoppen.
        shutil.copy2(db_path, backup_path)
        print(f"[cleanup] Backup erstellt: {backup_path}")

        # DROP
        conn.execute("DROP TABLE IF EXISTS empathy_log")
        conn.commit()
        print("[cleanup] DROP TABLE empathy_log ✅")

        if args.vacuum:
            print("[cleanup] VACUUM läuft ...")
            conn.execute("VACUUM")
            conn.commit()
            print("[cleanup] VACUUM ✅")

        return 0
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed('tools/db_cleanup_drop_empathy_log.py:163', exc=e, level=logging.WARNING)
            pass


if __name__ == "__main__":
    raise SystemExit(main())

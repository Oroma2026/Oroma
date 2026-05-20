#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/export_gate.py
# Projekt:   ORÓMA (Offline-First · Headless · Registry Export Policy)
# Modul:     ExportGate – Policy-gesteuerter Export aus registry.db (items) → TAR-Bundle (meta.json) + best-effort Markierung + KPI-Metrics
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# ExportGate implementiert eine minimal-invasive Exportkontrolle für die Model-Registry.
# Es entscheidet anhand einer Policy, welche Registry-Einträge exportiert werden dürfen,
# erzeugt ein portables TAR-Bundle und markiert exportierte Items (best effort),
# ohne Daten zu löschen oder zu deaktivieren.
#
# WICHTIG: WAS DIESE DATEI IM AKTUELLEN CODESTAND TATSÄCHLICH NUTZT
# ────────────────────────────────────────────────────────────────
# Dieses Modul arbeitet NICHT auf oroma.db/snapchains, sondern ausschließlich auf:
#   /opt/ai/oroma/data/registry.db   (Tabelle: items)
#
# Pfade sind im Code fest verdrahtet:
#   BASE       = "/opt/ai/oroma"
#   DB         = "{BASE}/data/registry.db"
#   EXPORT_DIR = "{BASE}/exports"
#
# Hinweis:
# - EXPORT_DIR wird beim Import/Start automatisch erstellt (exist_ok=True).
#
# EXPORT-INHALT (ABSICHTLICH MINIMAL)
# ───────────────────────────────────
# Das erzeugte Bundle enthält aktuell NUR eine meta.json (als "meta.json" im TAR):
#   - meta.json enthält eine Liste der exportfähigen items (vollständige Row-Dicts)
#   - Danach wird die temporäre meta_YYYYMMDD_HHMMSS.json neben dem TAR wieder gelöscht
#
# Das ist bewusst „safe & klein“:
# - Policy/Markierung sind produktionsreif nutzbar
# - „Reales File Packing“ (Model-Dateien, Verzeichnisse) kann später ergänzt werden,
#   ohne das Gate/Markierungsverhalten zu verändern.
#
# EXPORT-POLICY (HARTE GATES)
# ──────────────────────────
# Pro Item (Row aus items) wird check_exportable(item) angewandt:
#   - created_at muss älter sein als now - delay_days
#   - quality muss >= quality_threshold sein
#
# Erwartete Keys im Item-Dict (defensiv; fehlen → 0):
#   - created_at : Unix Timestamp (Sekunden)
#   - quality    : float
#
# Policy Parameter (ENV, tatsächlich im Code):
#   OROMA_EXPORT_DELAY_DAYS        (Default: 30)
#   OROMA_EXPORT_QUALITY_THRESHOLD (Default: 0.7)
#
# DB-READ (eligible items)
# ───────────────────────
# _eligible_items():
#   - sqlite3.connect(registry.db)
#   - conn.row_factory = sqlite3.Row
#   - SELECT * FROM items
#   - für jedes Row:
#       - KPI "kpi:export_considered" wird gezählt
#       - check_exportable() entscheidet Aufnahme in Exportliste
#
# MARKIERUNG (BEST EFFORT, NON-DESTRUCTIVE)
# ─────────────────────────────────────────
# Nach dem Bundle-Build werden Items best effort markiert:
#   - UPDATE items SET exported=1 WHERE id=?
#   - UPDATE items SET exported_at=? WHERE id=?
#
# Wichtig:
# - Wenn die Spalten exported/exported_at fehlen, wird das suppressed geloggt
#   (log_guard.log_suppressed), aber der Exportlauf bricht nicht ab.
# - Es gibt KEIN Delete, KEIN Deactivate, KEIN Drop in diesem Modul.
#
# KPI / METRICS (BEST EFFORT → oroma.db)
# ──────────────────────────────────────
# ExportGate versucht optional core.sql_manager zu importieren:
#   - wenn verfügbar: sql_manager.insert_metric("kpi:export_considered", 1.0)
#                    sql_manager.insert_metric("kpi:export_marked", 1.0)
#   - wenn nicht verfügbar oder DB locked: suppressed log, weiter
#
# Diese KPIs gehen in die Haupt-DB (oroma.db) über sql_manager, sind aber niemals kritisch.
#
# TAR-NAMING / META-STRUKTUR (GENAUER CODESTAND)
# ──────────────────────────────────────────────
# Dateiname:
#   oroma_export_YYYYMMDD_HHMMSS.tar    (UTC Timestamp)
#
# meta.json Inhalt:
#   {
#     "created": "<ts>",
#     "delay_days": <int>,
#     "quality_threshold": <float>,
#     "count": <int>,
#     "items": [ {<row dict>}, ... ]
#   }
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# check_exportable(item: dict) -> bool
#   - reiner Policy-Check (created_at/quality)
#
# mark_exported(item_id: int) -> None
#   - best-effort UPDATE in registry.db (exported/exported_at)
#
# create_export() -> str | None
#   - sammelt exportfähige Items
#   - schreibt meta_*.json, baut TAR (arcname="meta.json"), löscht meta_*.json
#   - markiert Items (best effort)
#   - gibt TAR-Pfad zurück oder None, wenn nichts exportierbar ist
#
# SELFTEST / CLI
# ──────────────
# Direktaufruf:
#   python3 /opt/ai/oroma/core/export_gate.py
# Ausgabe:
#   Export: <pfad-zur-tar> oder <none>
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Non-destructive: niemals löschen/deaktivieren.
# - Markierung bleibt best effort (fehlende Spalten dürfen Export nicht verhindern).
# - Bundle bleibt minimal (aktuell nur meta.json) – Erweiterungen später nur additiv.
# - KPI-Schreiben darf nie kritisch sein (sql_manager optional, locked tolerant).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import tarfile
import json
import time
import sqlite3
from core import db_writer_client as _dbw
from datetime import datetime
import logging
from core.log_guard import log_suppressed
LOG = logging.getLogger("oroma.export_gate")

# --------------------------------------------------------------------------- #
# Basis-Pfade (Registry bleibt separat von oroma.db)
# --------------------------------------------------------------------------- #
BASE = "/opt/ai/oroma"
DB = os.path.join(BASE, "data", "registry.db")
EXPORT_DIR = os.path.join(BASE, "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# --------------------------------------------------------------------------- #
# Policy-Parameter
# --------------------------------------------------------------------------- #
EXPORT_DELAY_DAYS = int(os.environ.get("OROMA_EXPORT_DELAY_DAYS", "30"))
EXPORT_QUALITY_THRESHOLD = float(os.environ.get("OROMA_EXPORT_QUALITY_THRESHOLD", "0.7"))

# --------------------------------------------------------------------------- #
# KPI-Helper (best-effort, nur wenn sql_manager vorhanden ist)
# --------------------------------------------------------------------------- #
try:
    from core import sql_manager  # type: ignore
    _HAS_SQL = True
except Exception:
    sql_manager = None  # type: ignore
    _HAS_SQL = False

def _kpi(name: str, v: float = 1.0) -> None:
    if not _HAS_SQL:
        return
    try:
        sql_manager.insert_metric(name, float(v))  # type: ignore[attr-defined]
    except Exception as e:
        log_suppressed(LOG, key="export_gate.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

# --------------------------------------------------------------------------- #
# Interne Helfer
# --------------------------------------------------------------------------- #
def _row_to_dict(row: sqlite3.Row) -> dict:
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        # Fallback (ältere SQLite ohne Row.keys())
        return dict(row)

def check_exportable(item: dict) -> bool:
    """
    Prüft, ob ein Registry-Item exportiert werden darf (Policy-Check).
    Erwartete Keys (optional, defensiv):
      - created_at: Unix-Timestamp (Sekunden)
      - quality:    float in [0..1]
    """
    created_at = float(item.get("created_at", 0) or 0)
    quality = float(item.get("quality", 0.0) or 0.0)
    cutoff = time.time() - EXPORT_DELAY_DAYS * 86400
    return (created_at < cutoff) and (quality >= EXPORT_QUALITY_THRESHOLD)



def _dbw_enabled() -> bool:
    try:
        return bool(int(os.getenv("OROMA_DBW_ENABLE", "0")))
    except Exception:
        return False

def mark_exported(item_id: int) -> None:
    """Markiert ein Item als exportiert, wenn Spalten existieren.

    Stufe C (DBWriter Multi-DB): bevorzugt DBWriter (db='registry').
    """
    if not os.path.exists(DB):
        return

    now = int(time.time())

    if _dbw_enabled() and '_dbw' in globals() and _dbw is not None:
        try:
            _dbw.exec_write(
                "UPDATE items SET exported=1 WHERE id=?",
                [int(item_id)],
                tag="export_gate.mark_exported.exported",
                priority="low",
                timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_REGISTRY", "5000")),
                db="registry",
            )
        except Exception as e:
            log_suppressed(LOG, key="export_gate.dbw.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        try:
            _dbw.exec_write(
                "UPDATE items SET exported_at=? WHERE id=?",
                [now, int(item_id)],
                tag="export_gate.mark_exported.exported_at",
                priority="low",
                timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_REGISTRY", "5000")),
                db="registry",
            )
        except Exception as e:
            log_suppressed(LOG, key="export_gate.dbw.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return

    # legacy/local fallback
    try:
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        try:
            cur.execute("UPDATE items SET exported=1 WHERE id=?", (int(item_id),))
        except Exception as e:
            log_suppressed(LOG, key="export_gate.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        try:
            cur.execute("UPDATE items SET exported_at=? WHERE id=?", (now, int(item_id)))
        except Exception as e:
            log_suppressed(LOG, key="export_gate.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        conn.commit()
    except Exception as e:
        log_suppressed(LOG, key="export_gate.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    finally:
        try:
            conn.close()  # type: ignore
        except Exception as e:
            log_suppressed(LOG, key="export_gate.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


def _eligible_items() -> list[dict]:
    """
    Holt exportierbare Einträge aus registry.db.
    Schreibt KPI 'kpi:export_considered' pro geprüftem Kandidaten.
    """
    items: list[dict] = []
    if not os.path.exists(DB):
        return items

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM items")
        for row in cur.fetchall():
            d = _row_to_dict(row)

            # KPI: jedes gelesene/prüfbare Item zählt als 'considered'
            _kpi("kpi:export_considered", 1.0)

            if check_exportable(d):
                items.append(d)
    finally:
        conn.close()
    return items

def create_export() -> str | None:
    """
    Erzeugt ein TAR-Bundle + JSON-Metadaten und liefert den Dateipfad zurück.
    Für jedes tatsächlich exportierte Item wird 'kpi:export_marked' gezählt
    und (best-effort) in der Registry markiert.
    """
    items = _eligible_items()
    if not items:
        return None

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"oroma_export_{ts}.tar"
    fpath = os.path.join(EXPORT_DIR, fname)

    # Meta-Datei vorbereiten
    meta = {
        "created": ts,
        "delay_days": EXPORT_DELAY_DAYS,
        "quality_threshold": EXPORT_QUALITY_THRESHOLD,
        "count": len(items),
        "items": items,
    }
    metafile = os.path.join(EXPORT_DIR, f"meta_{ts}.json")
    with open(metafile, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # TAR bauen
    with tarfile.open(fpath, "w") as tar:
        tar.add(metafile, arcname="meta.json")
        # Hier könnten reale Dateien ergänzt werden, z. B.:
        # tar.add("/opt/ai/oroma/models", arcname="models")

    # KPI + Markierung pro exportiertem Item
    for it in items:
        _kpi("kpi:export_marked", 1.0)
        # best-effort Datenbankmarkierung
        try:
            iid = int(it.get("id", 0))
            if iid > 0:
                mark_exported(iid)
        except Exception as e:
            log_suppressed(LOG, key="export_gate.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # Aufräumen: meta.json neben TAR löschen
    try:
        os.remove(metafile)
    except Exception as e:
        log_suppressed(LOG, key="export_gate.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    return fpath

# --------------------------------------------------------------------------- #
# Optionaler Selftest
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    path = create_export()
    print("Export:", path or "<none>")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/import_gate.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     ImportGate – Bundle-Import (ZIP/TAR) → SnapChains + Models (oroma.db) mit Dedupe (source_id/source_hash)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# ImportGate ist die bewusst schlanke Import-Schicht für ORÓMA, um „portable Bundles“
# (ZIP/TAR) in die produktive Hauptdatenbank (oroma.db) zu integrieren.
#
# Es importiert aktuell zwei Artefakt-Typen:
#   1) SnapChains  → Tabelle `snapchains` (Blob/JSON)
#   2) Model-Meta  → Tabelle `models`     (Registry-ähnliche Model-Metadaten)
#
# Wichtig:
# - Dieses Modul ist **non-destructive**: Es löscht nichts, sondern fügt hinzu
#   oder dedupliziert (identische Artefakte werden wiederverwendet).
# - Dieses Modul ist **headless**: keine UI/Qt/Wayland/X11 Abhängigkeiten.
#
# WAS GENAU ALS „BUNDLE“ VERSTANDEN WIRD
# ──────────────────────────────────────
# ImportGate erwartet ein Container-Archiv (ZIP oder TAR), das typischerweise eine
# `meta.json` und/oder Payload-Dateien enthält (z. B. chain.json, model meta).
# Die konkrete Payload-Erkennung ist absichtlich tolerant:
#   - JSON-Dateien werden als mögliche SnapChain- oder Model-Metadaten interpretiert
#   - Bytes-Payloads werden als SnapChain-Blob übernommen (sofern sinnvoll)
#
# DB-ZIEL (WICHTIG: NICHT registry.db)
# ────────────────────────────────────
# ImportGate schreibt in:
#   {OROMA_BASE_DIR}/data/oroma.db
#
# Es nutzt im Produktionsbetrieb bevorzugt den globalen DBWriter (Stufe C) für Writes,
# damit Import parallel zu Dream/Service laufen kann, ohne `database is locked`.
# Reads (Dedupe/Listing) bleiben lokal/WAL-friendly über sql_manager.get_conn().
# ImportGate ruft selbst **kein** ensure_schema() auf.
#
# DEDUPLIKATION (PRODUKTIONSKRITISCH)
# ───────────────────────────────────
# 1) SnapChains (Tabelle `snapchains`)
#   - Dedupe-Key ist `source_id`
#   - `source_id` wird als SHA-Hash über den Blob gebildet (_hash_bytes)
#   - Ablauf:
#       SELECT id FROM snapchains WHERE source_id=? LIMIT 1
#       → Treffer: vorhandene ID zurückgeben (kein Insert)
#       → sonst: INSERT snapchains(..., source_id=<hash>)
#
#   Erwartete Spalten (müssen im Schema existieren):
#     ts, quality, blob, exported, status, origin, source_id
#
# 2) Models (Tabelle `models`)
#   - Dedupe-Key ist `source_hash` (aus meta JSON)
#   - Ablauf:
#       SELECT id FROM models WHERE source_hash=? LIMIT 1
#       → Treffer: vorhandene ID zurückgeben
#       → sonst: INSERT models(..., source_hash=<hash>)
#
#   Erwartete Spalten (aus aktuellem Insert ersichtlich):
#     task, family, version, input_size, preproc_json, postproc_json,
#     labels_txt, hef_path, source_hash, calib_hash, created_at, status
#
# ORIGIN-KONVENTION
# ────────────────
# Imported SnapChains werden mit einem `origin` gespeichert, standardmäßig:
#   origin="import"
# Tools/UI können später via origin LIKE 'import%' die importierten Chains listen.
#
# LOGGING / NACHVOLLZIEHBARKEIT
# ────────────────────────────
# ImportGate loggt bewusst minimal nach stdout/stderr (via _log),
# damit systemd/orchestrator logs eine Spur haben.
# Keine „Massenspam“-Logs; der Import soll in Production leise bleiben.
#
# DATEI-/PFAD-KONVENTIONEN
# ───────────────────────
# BASE:
#   OROMA_BASE_DIR (Default: /opt/ai/oroma)
#
# Upload/Temp-Verzeichnis:
#   OROMA_UPLOAD_DIR (Default: {BASE}/uploads)
#   - wird bei Bedarf erstellt
#
# Testmodus:
#   OROMA_IMPORT_TEST=1
#   - aktiviert ggf. mehr Debug/Logs (je nach Codepfad), Import bleibt dennoch safe.
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# import_bundle(bundle_path: str, origin: str = "import") -> Dict[str, Any]
#   - entpackt Bundle, erkennt Kandidaten, schreibt SnapChains/Models (dedupe)
#   - Rückgabe enthält Summaries (z. B. counts/ids/errors)
#
# list_imports(limit: int = 50) -> List[Dict[str, Any]]
#   - liefert die letzten importierten SnapChains (origin LIKE 'import%')
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Kein Delete/Drop/Mass-Update: Import ist additiv.
# - Dedupe muss stabil bleiben (source_id = Hash(blob), models über source_hash).
# - DB-Schema wird vorausgesetzt (sql_manager.ensure_schema ist upstream).
# - Import muss headless laufen (keine externen Parser/GUI-Abhängigkeiten).
#
# =============================================================================
# END HEADER
# =============================================================================

import os
import tarfile
import zipfile
import hashlib
import json
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, List

# Optional: DBWriter (Stufe C / Single Writer). ImportGate ist meist ein
# Wartungs-/Offline-Pfad, kann aber im produktiven System parallel laufen.
# Deshalb: Writes bevorzugt via DBWriter, Reads lokal (WAL-friendly).
try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

BASE = Path(os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma"))
DB = BASE / "data" / "oroma.db"
UPLOAD_DIR = Path(os.environ.get("OROMA_UPLOAD_DIR", BASE / "uploads"))
IMPORT_LOG = BASE / "logs" / "import.log"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_LOG.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------
def _log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(IMPORT_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _insert_snapchain(blob: bytes, origin: str = "import") -> int:
    """Fügt SnapChain in DB ein (mit Deduplikation)."""
    # Dedupe-Read: über sql_manager.get_conn() (read-only, WAL-friendly).
    from core import sql_manager
    conn = sql_manager.get_conn()
    cur = conn.cursor()

    # Hash für Deduplikation
    h = _hash_bytes(blob)
    cur.execute("SELECT id FROM snapchains WHERE source_id=? LIMIT 1", (h,))
    row = cur.fetchone()
    if row:
        conn.close()
        return int(row["id"])

    ts = int(time.time())
    try:
        conn.close()
    except Exception:
        pass

    # Write: bevorzugt DBWriter
    if db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        sql = (
            "INSERT INTO snapchains (ts, quality, blob, exported, status, origin, source_id) "
            "VALUES (?,?,?,?,?,?,?)"
        )
        sid = db_writer_client.exec_lastrowid(
            sql,
            [ts, 0.0, blob, 0, "active", origin, h],
            tag="import_gate.snapchains.insert",
            priority="normal",
            timeout_ms=60000,
            db="oroma",
        )
        return int(sid)

    # Fallback: Writer über sql_manager (serialisiert Writes, keine direkte sqlite3.connect Nutzung).
    with sql_manager.writer_lock("import_gate.snapchains.insert", timeout_sec=60):
        with sql_manager.get_conn() as conn2:
            cur2 = conn2.execute(
                "INSERT INTO snapchains (ts, quality, blob, exported, status, origin, source_id) VALUES (?,?,?,?,?,?,?)",
                (ts, 0.0, sqlite3.Binary(blob), 0, "active", origin, h),
            )
            return int(cur2.lastrowid)


def _insert_model(meta: Dict[str, Any]) -> int:
    """Fügt Modell in Registry ein (mit Deduplikation via source_hash)."""
    from core import sql_manager
    conn = sql_manager.get_conn()
    cur = conn.cursor()

    h = meta.get("source_hash")
    if h:
        cur.execute("SELECT id FROM models WHERE source_hash=? LIMIT 1", (h,))
        row = cur.fetchone()
        if row:
            conn.close()
            return int(row["id"])

    sql = (
        "INSERT INTO models (task, family, version, input_size, preproc_json, postproc_json, "
        "labels_txt, hef_path, source_hash, calib_hash, created_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    params = [
        meta.get("task"),
        meta.get("family"),
        meta.get("version", "v3.5"),
        meta.get("input_size"),
        meta.get("preproc_json"),
        meta.get("postproc_json"),
        meta.get("labels_txt"),
        meta.get("hef_path"),
        meta.get("source_hash"),
        meta.get("calib_hash"),
        int(time.time()),
        "active",
    ]
    try:
        conn.close()
    except Exception:
        pass

    if db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        mid = db_writer_client.exec_lastrowid(
            sql,
            params,
            tag="import_gate.models.insert",
            priority="normal",
            timeout_ms=60000,
            db="oroma",
        )
        return int(mid)

    with sql_manager.writer_lock("import_gate.models.insert", timeout_sec=60):
        with sql_manager.get_conn() as conn2:
            cur2 = conn2.execute(sql, tuple(params))
            return int(cur2.lastrowid)


# ---------------------------------------------------------------------------
# Hauptfunktionen
# ---------------------------------------------------------------------------
def import_bundle(path: str) -> Dict[str, Any]:
    """Importiert ein Archiv (ZIP/TAR) und integriert Inhalte in DB."""
    results: Dict[str, List[int]] = {"snapchains": [], "models": []}
    p = Path(path)

    if not p.exists():
        return {"ok": False, "error": f"Datei nicht gefunden: {p}"}

    try:
        if tarfile.is_tarfile(p):
            with tarfile.open(p, "r:*") as tar:
                for member in tar.getmembers():
                    if member.name.endswith(".blob"):  # SnapChain
                        blob = tar.extractfile(member).read()
                        sid = _insert_snapchain(blob, origin="import/tar")
                        results["snapchains"].append(sid)
                    elif member.name.endswith("models.json"):  # Modelle
                        meta = json.load(tar.extractfile(member))
                        mid = _insert_model(meta)
                        results["models"].append(mid)

        elif zipfile.is_zipfile(p):
            with zipfile.ZipFile(p, "r") as z:
                for name in z.namelist():
                    if name.endswith(".blob"):
                        blob = z.read(name)
                        sid = _insert_snapchain(blob, origin="import/zip")
                        results["snapchains"].append(sid)
                    elif name.endswith("models.json"):
                        meta = json.loads(z.read(name).decode("utf-8"))
                        mid = _insert_model(meta)
                        results["models"].append(mid)
        else:
            return {"ok": False, "error": "Unbekanntes Archivformat"}

        _log(f"Import abgeschlossen: {p.name} → {results}")
        return {"ok": True, "results": results}

    except Exception as e:
        _log(f"Fehler beim Import {p.name}: {e}")
        return {"ok": False, "error": str(e)}


def list_imports(limit: int = 50) -> List[Dict[str, Any]]:
    """Liste letzte SnapChains/Modelle aus DB, die von Import stammen."""
    from core import sql_manager
    conn = sql_manager.get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, ts, origin FROM snapchains WHERE origin LIKE 'import%' "
        "ORDER BY ts DESC LIMIT ?", (limit,)
    )
    snaps = [dict(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT id, task, family, version, created_at "
        "FROM models WHERE status='active' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    models = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {"snapchains": snaps, "models": models}


# ---------------------------------------------------------------------------
# CLI-Test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_file = os.environ.get("OROMA_IMPORT_TEST", "")
    if test_file:
        print(import_bundle(test_file))
    else:
        print("Bitte Archiv angeben (OROMA_IMPORT_TEST=/pfad/zum/file)")
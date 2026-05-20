#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/exports/model_import.py
# Projekt:   ORÓMA – Offline-First Edge-KI (Headless)
# Modul:     Legacy Package Import (ZIP/NDJSON) – „model_import“
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul importiert ORÓMA-Pakete im **ZIP-Format** in die lokale SQLite-DB.
#
# Wichtig (Einordnung in v3.7.3):
# - Dieses File ist ein **Legacy-Importer** (Format/Manifest: v3.5) und ist *nicht*
#   identisch zu neueren Import-/Merge-Pipelines (z. B. ImportGate).
# - Es ist gedacht für:
#     • Offline-Transfer aus externen Quellen
#     • kontrollierten Batch-Import von SnapChains + Regeln (rules)
#     • „non-destructive“ Einspielen ohne Überschreiben lokaler Daten
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless, keine UI-Abhängigkeiten.
# - Non-destructive:
#     • überschreibt keine lokalen Rows
#     • importierte Datensätze werden als „neu“ eingefügt
#     • exported bleibt 0, disabled bleibt 0 (Standard)
# - Best-effort Schema-Kompatibilität:
#     • ensure_schema() wird aufgerufen
#     • fehlende Spalten werden per ALTER TABLE nachgezogen (_ensure_columns)
#
# PACKAGE-FORMAT (ZIP) – VERTRAG DIESER DATEI
# ───────────────────────────────────────────
# Pflichtdateien im ZIP:
#   - manifest.json
#   - snapchains.ndjson
#
# Optional:
#   - rules.ndjson
#   - meta.json
#
# NDJSON bedeutet: „eine JSON-Zeile pro Datensatz“.
#
# manifest.json (typisch) enthält u. a.:
#   - created_at (unix)
#   - package_id (string; wird u. a. als source_id genutzt)
#   - namespaces / defaults (kann je nach Generator variieren)
#
# meta.json (optional) kann u. a. enthalten:
#   - gaps: [<id>, <id>, ...]        → markiert einzelne SnapChains als gap
#   - namespaces: {"<id>": "..."}     → Mapping von externer ID zu Namespace
#
# GAP-SEMANIK
# ───────────
# Wenn meta.json["gaps"] eine SnapChain-ID enthält, wird beim Import:
#   metadata["gap"] = True
# gesetzt (zusätzlich zu evtl. bestehender metadata im Datensatz).
#
# NAMESPACE-SEMANTIK (PRIORITÄT)
# ─────────────────────────────
# Für jede SnapChain wird ein Namespace bestimmt:
#   1) CLI-Parameter --namespace (wenn gesetzt) → gilt global
#   2) meta.json["namespaces"].get(str(row["id"])) (falls vorhanden)
#   3) manifest["package_id"] (Fallback)
#
# Dadurch bleiben SnapChains logisch gruppiert, ohne lokale Namensräume zu kollidieren.
#
# DB / ABHÄNGIGKEITEN
# ───────────────────
# Dieses Modul nutzt:
#   from core.sql_manager import get_conn, ensure_schema
#
# Zusätzlich führt es Schema-Anpassungen durch (_ensure_columns):
#   - snapchains: ergänzt ggf. namespace, source_id, exported, disabled, created_at, metadata
#   - rules:      ergänzt ggf. namespace, source_id, exported, disabled, created_at
#
# (Das ist bewusst, damit Import auch in „slim“ oder älteren DB-Ständen funktioniert.)
#
# DATENFELDER (SNAPCHAINS NDJSON)
# ───────────────────────────────
# Erwartete Felder pro NDJSON-Row (best effort):
#   - id (externe ID; wird NICHT als lokale DB-ID verwendet)
#   - quality (float)
#   - created_at (int unix) [optional]
#   - source_id (string)    [optional; sonst manifest.package_id]
#   - namespace (string)    [optional; sonst aus Mapping/CLI/Fallback]
#   - metadata (dict)       [optional]
#   - data oder blob        [bytes oder str/obj; wird per _coerce_to_bytes normalisiert]
#
# _coerce_to_bytes:
# - akzeptiert bytes
# - akzeptiert str (wird UTF-8 bytes)
# - akzeptiert andere JSON-Objekte (werden JSON-dumps → UTF-8 bytes)
# - None ist invalid (führt zu ValueError)
#
# IMPORT-TRANSAKTION / DRY-RUN
# ────────────────────────────
# - Import läuft in einer expliziten Transaktion (BEGIN).
# - dry_run=True:
#     • führt Insert-Zähler hoch
#     • rollt am Ende IMMER zurück (rollback)
# - dry_run=False:
#     • commit am Ende
#
# SNAPCHAINS INSERT (LOKALES SCHEMA, PRODUKTIONSWICHTIG)
# ─────────────────────────────────────────────────────
# Es werden neue Rows eingefügt (kein Update bestehender Einträge):
#   INSERT INTO snapchains (blob, quality, created_at, namespace, source_id, exported, disabled, metadata)
#   VALUES (?, ?, ?, ?, ?, 0, 0, ?)
#
# Regeln (rules.ndjson) werden analog eingefügt:
#   INSERT INTO rules (data, created_at, namespace, source_id, exported, disabled)
#   VALUES (?, ?, ?, ?, 0, 0)
#
# QUALITY-FILTER
# ──────────────
# min_quality (CLI/Argument):
# - SnapChains mit quality < min_quality werden übersprungen
# - Zähler skipped_snapchains_quality wird erhöht
#
# ÖFFENTLICHE API (FUNKTIONEN)
# ────────────────────────────
# validate_package(zip_path) -> Dict
#   - Prüft Pflichtdateien, liest manifest.json (wenn vorhanden), liefert Report:
#       {"ok": bool, "errors": [...], "counts": {...}, "manifest": {...}}
#
# import_package(zip_path, *, namespace=None, min_quality=0.0, dry_run=False) -> Dict
#   - Importiert SnapChains (+ optional rules), liefert Report:
#       {
#         "ok": True,
#         "inserted_snapchains": <int>,
#         "skipped_snapchains_quality": <int>,
#         "inserted_rules": <int>,
#         "namespace": <effective namespace>,
#         "dry_run": <bool>,
#         ...
#       }
#
# CLI (PRODUKTIONSNAH)
# ────────────────────
# Validierung (nur prüfen):
#   python3 /opt/ai/oroma/exports/model_import.py --validate /pfad/paket.zip
#
# Import:
#   python3 /opt/ai/oroma/exports/model_import.py --import /pfad/paket.zip
#
# Import mit Namespace + Quality Filter:
#   python3 /opt/ai/oroma/exports/model_import.py --import /pfad/paket.zip --namespace "import:bundle01" --min-quality 0.6
#
# Dry-Run:
#   python3 /opt/ai/oroma/exports/model_import.py --import /pfad/paket.zip --dry-run
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - Non-destructive: niemals lokale Rows überschreiben.
# - _ensure_columns muss tolerant bleiben (ALTER TABLE nur wenn Spalte fehlt).
# - min_quality muss vor Insert greifen (damit DB sauber bleibt).
# - dry_run muss immer rollbacken.
# - Namespace-Priorität (CLI > meta.json mapping > manifest fallback) beibehalten.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os, sys, io, json, time, hashlib, zipfile, argparse, logging
from typing import Dict, Any, Optional, List

BASE = "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    from core.sql_manager import get_conn, ensure_schema
except Exception as e:
    print("[model_import] FEHLER: core.sql_manager nicht importierbar:", e, file=sys.stderr)
    raise

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.model_import")
if not LOG.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Konstanten
# -----------------------------------------------------------------------------
REQUIRED_FILES = ("manifest.json", "snapchains.ndjson")
OPTIONAL_FILES = ("rules.ndjson", "meta.json")
DEFAULT_MIN_QUALITY = 0.0
MANIFEST_VERSION = "v3.5"

# -----------------------------------------------------------------------------
# Helfer
# -----------------------------------------------------------------------------
def _read_zip_json(zf: zipfile.ZipFile, name: str) -> Dict[str, Any]:
    with zf.open(name, "r") as f:
        return json.loads(f.read().decode("utf-8"))

def _iter_zip_ndjson(zf: zipfile.ZipFile, name: str):
    with zf.open(name, "r") as f:
        for raw in f:
            line = raw.decode("utf-8", "ignore").strip()
            if line:
                yield json.loads(line)

def _ensure_columns(conn) -> None:
    cur = conn.cursor()
    # Snapchains
    cur.execute("PRAGMA table_info(snapchains)")
    cols = {r[1] for r in cur.fetchall()}
    for cname, ctype in [
        ("namespace", "TEXT"),
        ("source_id", "TEXT"),
        ("exported", "INTEGER DEFAULT 0"),
        ("disabled", "INTEGER DEFAULT 0"),
        ("quality", "REAL DEFAULT 0.0"),
        ("created_at", "INTEGER"),
        ("metadata", "TEXT DEFAULT '{}'"),
    ]:
        if cname not in cols:
            LOG.info("ALTER TABLE snapchains ADD COLUMN %s %s", cname, ctype)
            cur.execute(f"ALTER TABLE snapchains ADD COLUMN {cname} {ctype}")

    # Rules
    cur.execute("PRAGMA table_info(rules)")
    cols_r = {r[1] for r in cur.fetchall()}
    for cname, ctype in [
        ("namespace", "TEXT"),
        ("source_id", "TEXT"),
        ("exported", "INTEGER DEFAULT 0"),
        ("disabled", "INTEGER DEFAULT 0"),
        ("created_at", "INTEGER"),
    ]:
        if cname not in cols_r:
            LOG.info("ALTER TABLE rules ADD COLUMN %s %s", cname, ctype)
            cur.execute(f"ALTER TABLE rules ADD COLUMN {cname} {ctype}")

    conn.commit()

def _coerce_to_bytes(field: Any) -> Optional[bytes]:
    if field is None:
        return None
    if isinstance(field, (bytes, bytearray)):
        return bytes(field)
    if isinstance(field, list):
        try:
            return bytes(int(x) & 0xFF for x in field)  # v3.5 Fix
        except Exception:
            return json.dumps(field, ensure_ascii=False).encode("utf-8")
    if isinstance(field, dict):
        if "b64" in field:
            import base64
            try: return base64.b64decode(field["b64"])
            except Exception: return None
        if "hex" in field:
            try: return bytes.fromhex(field["hex"])
            except Exception: return None
        return json.dumps(field, ensure_ascii=False).encode("utf-8")
    if isinstance(field, str):
        s = field.strip()
        try:
            return bytes.fromhex(s) if len(s) % 2 == 0 else s.encode("utf-8")
        except Exception:
            return s.encode("utf-8")
    try:
        return json.dumps(field, ensure_ascii=False).encode("utf-8")
    except Exception:
        return None

# -----------------------------------------------------------------------------
# Validierung & Preview
# -----------------------------------------------------------------------------
def validate_package(zip_path: str) -> Dict[str, Any]:
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Paket nicht gefunden: {zip_path}")
    report = {"zip_path": zip_path, "ok": False, "errors": [], "manifest": None, "counts": {}}
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            for req in REQUIRED_FILES:
                if req not in names:
                    report["errors"].append(f"Pflichtdatei fehlt: {req}")
            if "manifest.json" in names:
                manifest = _read_zip_json(zf, "manifest.json")
                report["manifest"] = manifest
                if manifest.get("format_version") not in (MANIFEST_VERSION, "v3.5", "v2"):
                    report["errors"].append(f"Unerwartete Manifest-Version: {manifest.get('format_version')}")
            if "snapchains.ndjson" in names:
                report["counts"]["snapchains"] = sum(1 for _ in zf.open("snapchains.ndjson"))
            if "rules.ndjson" in names:
                report["counts"]["rules"] = sum(1 for _ in zf.open("rules.ndjson"))
            report["ok"] = (len(report["errors"]) == 0)
    except Exception as e:
        report["errors"].append(f"Validierungsfehler: {type(e).__name__}: {e}")
    return report

def preview_package(zip_path: str) -> Dict[str, Any]:
    rep = validate_package(zip_path)
    return {
        "zip_path": rep["zip_path"],
        "valid": rep["ok"],
        "errors": rep["errors"],
        "counts": rep.get("counts"),
        "manifest": rep.get("manifest"),
    }

# -----------------------------------------------------------------------------
# Import
# -----------------------------------------------------------------------------
def import_package(zip_path: str, namespace: Optional[str] = None,
                   min_quality: float = DEFAULT_MIN_QUALITY,
                   dry_run: bool = False) -> Dict[str, Any]:
    rep = validate_package(zip_path)
    if not rep["ok"]:
        raise RuntimeError(f"Paket nicht valide: {rep['errors']}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        manifest = rep.get("manifest") or {}
        meta_extra = {}
        if "meta.json" in zf.namelist():
            try: meta_extra = _read_zip_json(zf, "meta.json")
            except Exception as e: LOG.warning("meta.json konnte nicht gelesen werden: %s", e)

        gaps = set(meta_extra.get("gaps", []))
        namespaces_map = meta_extra.get("namespaces", {})

        conn = get_conn()
        ensure_schema(); _ensure_columns(conn)
        cur = conn.cursor()

        inserted_sc, inserted_rules, skipped_sc_quality = 0, 0, 0
        try:
            cur.execute("BEGIN")

            if "snapchains.ndjson" in zf.namelist():
                for row in _iter_zip_ndjson(zf, "snapchains.ndjson"):
                    q = float(row.get("quality", 0.0))
                    if q < float(min_quality):
                        skipped_sc_quality += 1; continue
                    created_at = int(row.get("created_at") or manifest.get("created_at") or int(time.time()))
                    source_id = row.get("source_id") or manifest.get("package_id")
                    ns = namespace or namespaces_map.get(str(row.get("id"))) or manifest.get("package_id")
                    metadata = row.get("metadata", {}) or {}
                    if row.get("id") in gaps: metadata["gap"] = True
                    data_blob = _coerce_to_bytes(row.get("data") or row.get("blob"))
                    if data_blob is None: raise ValueError("Ungültiges SnapChain-Datenfeld")
                    if not dry_run:
                        cur.execute("""
                            INSERT INTO snapchains (blob, quality, created_at, namespace, source_id, exported, disabled, metadata)
                            VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                        """, (data_blob, q, created_at, ns, source_id, json.dumps(metadata, ensure_ascii=False)))
                    inserted_sc += 1

            if "rules.ndjson" in zf.namelist():
                for row in _iter_zip_ndjson(zf, "rules.ndjson"):
                    created_at = int(row.get("created_at") or manifest.get("created_at") or int(time.time()))
                    source_id = row.get("source_id") or manifest.get("package_id")
                    if not dry_run:
                        cur.execute("""
                            INSERT INTO rules (data, created_at, namespace, source_id, exported, disabled)
                            VALUES (?, ?, ?, ?, 0, 0)
                        """, (json.dumps(row, ensure_ascii=False), created_at, namespace, source_id))
                    inserted_rules += 1

            conn.rollback() if dry_run else conn.commit()

            return {
                "ok": True,
                "zip_path": zip_path,
                "namespace": namespace,
                "inserted_snapchains": inserted_sc,
                "skipped_snapchains_quality": skipped_sc_quality,
                "inserted_rules": inserted_rules,
                "dry_run": dry_run,
            }
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Import fehlgeschlagen: {type(e).__name__}: {e}") from e

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ORÓMA v3.5 – model_import",
        description="Importiert exportierte ORÓMA-Pakete (ZIP) Gap-/Namespace-ready.")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_prev = sub.add_parser("preview", help="Paket validieren & Vorschau anzeigen")
    p_prev.add_argument("zip", help="Pfad zum Export-Paket (ZIP)")
    p_imp = sub.add_parser("import", help="Paket importieren (non-destructive)")
    p_imp.add_argument("zip", help="Pfad zum Export-Paket (ZIP)")
    p_imp.add_argument("--namespace", default=None, help="Optionaler Namespace")
    p_imp.add_argument("--min-quality", type=float, default=DEFAULT_MIN_QUALITY)
    p_imp.add_argument("--dry-run", action="store_true")
    return p

def main(argv: Optional[List[str]] = None) -> int:
    args = _build_argparser().parse_args(argv or sys.argv[1:])
    try:
        conn = get_conn(); ensure_schema(); _ensure_columns(conn)
    except Exception as e:
        LOG.error("DB/Schemaprüfung fehlgeschlagen: %s: %s", type(e).__name__, e)
        return 2

    if args.cmd == "preview":
        print(json.dumps(preview_package(args.zip), ensure_ascii=False, indent=2)); return 0
    if args.cmd == "import":
        try:
            res = import_package(args.zip, namespace=args.namespace,
                                 min_quality=float(args.min_quality),
                                 dry_run=bool(args.dry_run))
            print(json.dumps(res, ensure_ascii=False, indent=2)); return 0
        except Exception as e:
            LOG.error("Import fehlgeschlagen: %s: %s", type(e).__name__, e); return 1
    return 2

if __name__ == "__main__":
    sys.exit(main())
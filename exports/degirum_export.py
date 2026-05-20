#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/exports/degirum_export.py
# Projekt:   ORÓMA – Offline-First Edge-KI (Headless)
# Modul:     DeGirum Export – SnapChains + Rules als DeGirum-kompatibles ZIP-Dataset (manifest.json + JSONL)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul exportiert ORÓMA-Lerndaten (SnapChains + Rules) in ein **portables ZIP**
# in einem Format, das als „DeGirum-kompatibles Dataset“ verstanden werden kann.
#
# Zielgruppe / Use-Cases:
# - Offline-Transfer (Airgap) von SnapChains/Regeln in ein neutrales Paket
# - Bereitstellung eines „Datasets“ für externe Verarbeitung/Analyse (z. B. Toolchains)
# - Kooperation mit DeGirum-Ökosystem (SDK optional), ohne ORÓMA an DeGirum zu binden
#
# WICHTIG (ARCHITEKTUR-VERTRAG)
# ─────────────────────────────
# - **Nicht-destruktiv**: Inhalte bleiben in SQLite erhalten.
# - Side-Effect ist ausschließlich (optional) das Setzen von `exported=1`
#   in den exportierten Tabellen (snapchains, rules).
# - Export ist headless und benötigt keine GUI/Display.
#
# FORMAT: ZIP-INHALT (STABIL)
# ───────────────────────────
# Das erzeugte ZIP enthält:
#
#   manifest.json
#   dataset.jsonl
#   rules.jsonl              (optional; nur wenn rules gefunden wurden)
#   README_EXPORT.txt
#
# dataset.jsonl:
# - eine JSON-Zeile pro SnapChain-Kandidat
# - Felder (exakt aus _row_to_jsonl_snapchain):
#     {
#       "id": <int>,
#       "created_at": <int|null>,          # aus snapchains.ts
#       "quality": <float|null>,
#       "metadata": {"origin": <str|null>},
#       "data_b64": "<base64(blob)>"
#     }
#
# rules.jsonl:
# - eine JSON-Zeile pro Regel-Kandidat
# - Felder (exakt aus _row_to_jsonl_rule):
#     {
#       "id": <int>,
#       "created_at": <int|null>,
#       "weight": <float|null>,
#       "rule": <str>,                      # aus rules.content
#       "metadata": {}
#     }
#
# manifest.json:
# - enthält Metadaten und Policy-Parameter dieses Exports (exakt aus Code):
#     {
#       "format": "oroma-degirum-export",
#       "version": "v3.5",                  # Paketformat-Version (Legacy stabil)
#       "created_at": <unix_ts>,
#       "policy": {
#         "min_delay_days": <EXPORT_DELAY_DAYS>,
#         "requested_days": <days>,
#         "min_quality": <min_quality>,
#         "max_items": <max_items>
#       },
#       "counts": {"snapchains": <n>, "rules": <n>},
#       "vector_db": {"threshold": <VECDB_THRESHOLD>},
#       "degirum": {
#         "sdk_present": <bool>,
#         "client": <dict|null>,
#         "model_hint": <str|null>,
#         "models_listed": <list>
#       },
#       "notes": [...]
#     }
#
# DB / DATENQUELLE (EXAKT IM CODE)
# ───────────────────────────────
# - DB-Basisverzeichnis wird über core.sql_manager.get_base_dir() ermittelt.
# - Default DB Pfad:
#     DB_PATH_DEFAULT = <BASE_DIR>/data/oroma.db
# - sqlite3 Connection Konfiguration:
#     PRAGMA journal_mode=WAL
#     PRAGMA foreign_keys=ON
#
# Kandidaten-Query SnapChains (exakt):
#   SELECT id, ts as created_at, quality, origin as metadata, blob as data
#   FROM snapchains
#   WHERE (ts IS NOT NULL AND ts <= ?)
#     AND (quality IS NULL OR quality >= ?)
#     AND (exported IS NULL OR exported = 0)
#   ORDER BY ts ASC
#   LIMIT ?
#
# Kandidaten-Query Rules (exakt):
#   SELECT id, created_at, weight, content as rule, NULL as metadata
#   FROM rules
#   WHERE (created_at IS NOT NULL AND created_at <= ?)
#     AND (exported IS NULL OR exported = 0)
#   ORDER BY created_at ASC
#   LIMIT ?
#
# MIN-AGE POLICY (WICHTIG)
# ───────────────────────
# days-Parameter ist NICHT „hart“ – der tatsächliche Mindest-Delay ist:
#   min_age = max(EXPORT_DELAY_DAYS, days)
#
# Das verhindert zu frühe Exporte, selbst wenn days klein gesetzt wird.
#
# SIDE EFFECT: exported=1 markieren (OPTIONAL)
# ──────────────────────────────────────────
# export_to_degirum_zip(..., also_mark_exported=True) markiert anschließend:
#   UPDATE <table> SET exported=1 WHERE id IN (...)
#
# Unterstützte Tabellen in diesem Modul:
# - snapchains (Standard)
# - rules     (wenn rule IDs vorhanden)
#
# DEGRIUM SDK (OPTIONAL)
# ─────────────────────
# - DeGirum ist kein Pflicht-Dependency.
# - Wenn `import degirum` fehlschlägt:
#     _HAS_DEGIRUM = False
#     manifest["degirum"]["sdk_present"] = False
#
# degirum_info(client_uri):
# - versucht optional ein Client-Objekt via _dg.get_client(uri) zu erzeugen
# - versucht (best effort) `client.list_models()` aufzurufen
# - Fehler werden als "error" im info dict dokumentiert, Export läuft trotzdem weiter.
#
# ENV / KONFIGURATION (EXAKT)
# ───────────────────────────
# OROMA_EXPORT_DELAY_DAYS (default: "30")
#   - Mindestalter in Tagen; effektiver Delay ist max(ENV, days)
#
# OROMA_VECTORDB_THRESHOLD (default: "100000")
#   - wird nur in manifest.json dokumentiert (Telemetry/Kompatibilität)
#
# OROMA_LOG_LEVEL (default: "INFO")
#   - Logger: "oroma.degirum_export"
#
# EXPORT OUTPUT VERZEICHNIS
# ─────────────────────────
# Default:
#   EXPORT_DIR_DEFAULT = <BASE_DIR>/exports/out
# out_dir Parameter überschreibt dieses Ziel.
#
# Dateiname:
#   oroma_dg_export_<ts>_<sha12>.zip
# sha12 basiert auf:
#   f"{ts}:{n_snap}:{n_rules}:{min_quality}:{days}:{degirum_model_hint or ''}"
#
# ÖFFENTLICHE API (FUNKTIONEN)
# ────────────────────────────
# list_candidates(days=60, min_quality=0.6, max_items=1000) -> {"snapchains":[Row], "rules":[Row]}
#   - führt die DB-Queries aus und liefert sqlite3.Rows
#
# mark_exported(ids: List[int], table="snapchains") -> int
#   - setzt exported=1 für alle IDs in der Tabelle; gibt rowcount zurück
#
# degirum_info(client_uri: Optional[str]) -> Dict[str,Any]
#   - liefert Info über SDK/Client/Model-Liste, best effort
#
# export_to_degirum_zip(days=60, min_quality=0.6, max_items=2000,
#                       degirum_uri=None, degirum_model_hint=None,
#                       out_dir=None, also_mark_exported=True) -> str
#   - erstellt ZIP, schreibt manifest + jsonl, markiert optional exported
#   - Rückgabe: Pfad zur ZIP-Datei (string)
#
# CLI (PRODUKTIONSNAH)
# ────────────────────
# Export (Default):
#   python3 /opt/ai/oroma/exports/degirum_export.py --days 60 --min-quality 0.6 --max-items 2000
#
# Export ohne Markieren:
#   python3 /opt/ai/oroma/exports/degirum_export.py --no-mark-exported
#
# DeGirum Client Info + Model Hint:
#   python3 /opt/ai/oroma/exports/degirum_export.py --degirum-uri local:// --model-hint "yolov8n"
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - ZIP-Struktur (manifest.json, dataset.jsonl, rules.jsonl, README_EXPORT.txt) bleibt stabil.
# - dataset.jsonl nutzt base64(blob) in "data_b64" (kein Re-Serialize des BLOB-Inhalts).
# - Export bleibt non-destructive; keine Deletes, kein Disable, kein Schema-Migration-Flickwerk.
# - Auch ohne DeGirum-SDK muss Export funktionieren (sdk_present=false).
# - `min_age = max(EXPORT_DELAY_DAYS, days)` muss erhalten bleiben.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os, time, json, base64, sqlite3, zipfile, tempfile, hashlib, logging
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import numpy as _np
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

try:
    import degirum as _dg
    _HAS_DEGIRUM = True
except Exception:
    _HAS_DEGIRUM = False

from core.sql_manager import get_base_dir

BASE_DIR = get_base_dir()
DB_PATH_DEFAULT = os.path.join(BASE_DIR, "data", "oroma.db")
EXPORT_DIR_DEFAULT = os.path.join(BASE_DIR, "exports", "out")

EXPORT_DELAY_DAYS = int(os.environ.get("OROMA_EXPORT_DELAY_DAYS", "30"))
VECDB_THRESHOLD = int(os.environ.get("OROMA_VECTORDB_THRESHOLD", "100000"))

LOG = logging.getLogger("oroma.degirum_export")
if not LOG.handlers:
    LOG.setLevel(os.environ.get("OROMA_LOG_LEVEL", "INFO").upper())
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOG.addHandler(_h)

# --- DB Helfer ----------------------------------------------------------------

def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH_DEFAULT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# --- Kandidaten-Logik ---------------------------------------------------------

def _now_ts() -> int:
    return int(time.time())

def _days_to_secs(d: int) -> int:
    return d * 86400

def list_candidates(days: int = 60,
                    min_quality: float = 0.6,
                    max_items: int = 1000) -> Dict[str, List[sqlite3.Row]]:
    conn = _db_connect()
    cur = conn.cursor()
    min_age = max(EXPORT_DELAY_DAYS, days)
    min_ts = _now_ts() - _days_to_secs(min_age)

    out = {"snapchains": [], "rules": []}

    try:
        cur.execute("""
            SELECT id, ts as created_at, quality, origin as metadata, blob as data
            FROM snapchains
            WHERE (ts IS NOT NULL AND ts <= ?)
              AND (quality IS NULL OR quality >= ?)
              AND (exported IS NULL OR exported = 0)
            ORDER BY ts ASC
            LIMIT ?
        """, (min_ts, float(min_quality), int(max_items)))
        out["snapchains"] = cur.fetchall()
    except Exception as e:
        LOG.error("DB-Query snapchains fehlgeschlagen: %s", e)

    try:
        cur.execute("""
            SELECT id, created_at, weight, content as rule, NULL as metadata
            FROM rules
            WHERE (created_at IS NOT NULL AND created_at <= ?)
              AND (exported IS NULL OR exported = 0)
            ORDER BY created_at ASC
            LIMIT ?
        """, (min_ts, int(max_items)))
        out["rules"] = cur.fetchall()
    except Exception:
        pass

    conn.close()
    return out

def mark_exported(ids: List[int], table: str = "snapchains") -> int:
    if not ids:
        return 0
    conn = _db_connect()
    cur = conn.cursor()
    q = f"UPDATE {table} SET exported=1 WHERE id IN ({','.join('?'*len(ids))})"
    cur.execute(q, ids)
    conn.commit()
    n = cur.rowcount
    conn.close()
    LOG.info("Markiert %d Einträge in %s als exportiert.", n, table)
    return n

# --- DeGirum Info -------------------------------------------------------------

def degirum_info(client_uri: Optional[str] = None) -> Dict[str, Any]:
    info = {"has_degirum": _HAS_DEGIRUM, "client": None, "models": []}
    if not _HAS_DEGIRUM:
        return info
    try:
        uri = client_uri or "local://"
        client = _dg.get_client(uri)
        info["client"] = {"uri": uri, "type": str(type(client))}
        try:
            models = getattr(client, "list_models", lambda: [])()
            info["models"] = models
        except Exception:
            pass
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    return info

# --- JSONL Konvertierungen ----------------------------------------------------

def _row_to_jsonl_snapchain(row: sqlite3.Row) -> Dict[str, Any]:
    data_b64 = ""
    try:
        if row["data"] is not None:
            buf = bytes(row["data"])
            data_b64 = base64.b64encode(buf).decode("ascii")
    except Exception:
        data_b64 = ""

    return {
        "id": int(row["id"]),
        "created_at": int(row["created_at"]) if row["created_at"] else None,
        "quality": float(row["quality"]) if row["quality"] is not None else None,
        "metadata": {"origin": row["metadata"]},
        "data_b64": data_b64
    }

def _row_to_jsonl_rule(row: sqlite3.Row) -> Dict[str, Any]:
    rule_val = row["rule"]
    try:
        if isinstance(rule_val, str):
            try:
                rule_val = json.loads(rule_val)
            except Exception:
                pass
    except Exception:
        pass
    return {
        "id": int(row["id"]),
        "created_at": int(row["created_at"]) if row["created_at"] else None,
        "weight": float(row["weight"]) if row["weight"] is not None else None,
        "rule": rule_val,
        "metadata": {},
    }

# --- Export ZIP Paket ---------------------------------------------------------

def _manifest_json(meta: Dict[str, Any]) -> str:
    return json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True)

def _safe_jsonl_write(path: Path, items: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False))
            f.write("\n")

def export_to_degirum_zip(days: int = 60,
                          min_quality: float = 0.6,
                          max_items: int = 2000,
                          degirum_uri: Optional[str] = None,
                          degirum_model_hint: Optional[str] = None,
                          out_dir: Optional[str] = None,
                          also_mark_exported: bool = True) -> str:
    out_root = Path(out_dir or EXPORT_DIR_DEFAULT)
    out_root.mkdir(parents=True, exist_ok=True)

    cand = list_candidates(days=days, min_quality=min_quality, max_items=max_items)
    sc_rows = cand.get("snapchains", []) or []
    rl_rows = cand.get("rules", []) or []

    if not sc_rows and not rl_rows:
        raise RuntimeError("Keine Kandidaten gefunden.")

    dg_info = degirum_info(degirum_uri) if _HAS_DEGIRUM else {"has_degirum": False}

    ts = int(time.time())
    tmpdir = Path(tempfile.mkdtemp(prefix=f"oroma_dgexp_{ts}_"))
    try:
        dataset_path = tmpdir / "dataset.jsonl"
        dataset_items = [_row_to_jsonl_snapchain(r) for r in sc_rows]
        _safe_jsonl_write(dataset_path, dataset_items)

        rules_items: List[Dict[str, Any]] = []
        rules_path = tmpdir / "rules.jsonl"
        if rl_rows:
            rules_items = [_row_to_jsonl_rule(r) for r in rl_rows]
            _safe_jsonl_write(rules_path, rules_items)

        readme_path = tmpdir / "README_EXPORT.txt"
        readme_path.write_text(
            "ORÓMA Export-Paket (DeGirum-kompatibel)\n"
            "========================================\n", encoding="utf-8"
        )

        manifest = {
            "format": "oroma-degirum-export",
            "version": "v3.5",
            "created_at": ts,
            "policy": {
                "min_delay_days": EXPORT_DELAY_DAYS,
                "requested_days": days,
                "min_quality": min_quality,
                "max_items": max_items
            },
            "counts": {
                "snapchains": len(sc_rows),
                "rules": len(rl_rows)
            },
            "vector_db": {"threshold": VECDB_THRESHOLD},
            "degirum": {
                "sdk_present": dg_info.get("has_degirum", False),
                "client": dg_info.get("client"),
                "model_hint": degirum_model_hint,
                "models_listed": dg_info.get("models", [])
            },
            "notes": [
                "Dieses Paket ist universell; es kann neben DeGirum-ModelZoo-Modellen verwendet werden.",
                "ORÓMA behält Post-Processing/Resonanzkontrolle.",
            ]
        }
        manifest_path = tmpdir / "manifest.json"
        manifest_path.write_text(_manifest_json(manifest), encoding="utf-8")

        hash_src = f"{ts}:{len(sc_rows)}:{len(rl_rows)}:{min_quality}:{days}:{degirum_model_hint or ''}"
        sha = hashlib.sha256(hash_src.encode("utf-8")).hexdigest()[:12]
        out_zip = out_root / f"oroma_dg_export_{ts}_{sha}.zip"

        with zipfile.ZipFile(str(out_zip), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.write(str(manifest_path), "manifest.json")
            zf.write(str(dataset_path), "dataset.jsonl")
            if rl_rows:
                zf.write(str(rules_path), "rules.jsonl")
            zf.write(str(readme_path), "README_EXPORT.txt")

        LOG.info("Export erstellt: %s", out_zip)

        if also_mark_exported:
            sc_ids = [int(r["id"]) for r in sc_rows]
            rl_ids = [int(r["id"]) for r in rl_rows]
            if sc_ids:
                mark_exported(sc_ids, table="snapchains")
            if rl_ids:
                mark_exported(rl_ids, table="rules")

        return str(out_zip)

    finally:
        try:
            for p in tmpdir.glob("*"):
                try: p.unlink()
                except Exception: pass
            tmpdir.rmdir()
        except Exception:
            pass

# --- CLI ----------------------------------------------------------------------

def _parse_cli(argv: List[str]) -> Dict[str, Any]:
    import argparse
    ap = argparse.ArgumentParser(prog="degirum_export",
        description="ORÓMA v3.5 – DeGirum-kompatibler Exporter (ZIP)")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--min-quality", type=float, default=0.6)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--out", type=str, default=EXPORT_DIR_DEFAULT)
    ap.add_argument("--degirum-uri", type=str, default=None)
    ap.add_argument("--degirum-model", type=str, default=None)
    ap.add_argument("--no-mark", action="store_true")
    return vars(ap.parse_args(argv))

def main(argv: Optional[List[str]] = None) -> int:
    import sys
    args = _parse_cli(argv or sys.argv[1:])
    try:
        zip_path = export_to_degirum_zip(
            days=int(args["days"]),
            min_quality=float(args["min_quality"]),
            max_items=int(args["limit"]),
            degirum_uri=args["degirum_uri"],
            degirum_model_hint=args["degirum_model"],
            out_dir=args["out"],
            also_mark_exported=(not args["no_mark"])
        )
        print(zip_path)
        return 0
    except Exception as e:
        LOG.error("Export fehlgeschlagen: %s", e)
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
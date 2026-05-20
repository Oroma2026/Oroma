#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/exports/model_export.py
# Projekt:   ORÓMA – Offline-First Edge-KI (Headless)
# Modul:     Legacy SnapChain Export (tar.gz) – „model_export“
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul exportiert „eligible“ SnapChains aus der lokalen SQLite-DB (oroma.db)
# in ein portables, versioniertes **.tar.gz** Bundle.
#
# Wichtig (Einordnung in v3.7.3):
# - Dieses File ist ein **Legacy-Exporter** (Bundle-Format: „oroma-export-v3.5“),
#   wird im Projekt als optional/kompatibel geführt (z. B. via exports/__init__.py),
#   und ist primär für:
#     • Offline-Transfer (Airgap)
#     • Debug/Archiv-Snapshots
#     • Migrationen zwischen Instanzen
#   gedacht.
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine GUI/Qt/Wayland/X11 Abhängigkeiten.
# - Policy-first: Export ist **nicht destruktiv**:
#     • kein Delete
#     • kein „Disable“ als Side-Effect
#     • Standard: exported=1 markieren, optional status='archived'
# - DB-Schema wird vor Nutzung über core.sql_manager.ensure_schema() abgesichert.
# - Export ist robust gegenüber Schema-Varianten (gap_flag optional).
#
# KERNIDEE „ELIGIBLE SNAPCHAINS“
# ──────────────────────────────
# Kandidaten werden über eine SQL-Abfrage selektiert:
#   - exported = 0
#   - ts <= now - (days_delay * 86400)
#   - quality >= min_quality
#   - optional: Gap-Filter (wenn Spalte gap_flag existiert)
#
# Gap-Filter-Semantik (optional, nur falls Spalte existiert):
#   --only-gaps      → gap_flag = 1
#   --exclude-gaps   → COALESCE(gap_flag,0) = 0
# Wenn gap_flag in der Tabelle fehlt → Filter wird ignoriert (only_gaps=None).
#
# DB / ABHÄNGIGKEITEN
# ───────────────────
# Dieses Modul arbeitet direkt mit SQLite über core.sql_manager:
#   - from core.sql_manager import get_conn, ensure_schema
#
# Erwartetes SnapChains-Schema (für Exportfunktion):
#   snapchains(
#     id INTEGER,
#     ts INTEGER,
#     quality REAL,
#     blob BLOB,
#     exported INTEGER,
#     status TEXT,
#     origin TEXT,
#     gap_flag INTEGER [optional]
#   )
#
# WICHTIG: Es werden bewusst nur die benötigten Spalten gelesen/geschrieben,
#          um mit „slim“ / älteren DB-Ständen kompatibel zu bleiben.
#
# BUNDLE-FORMAT (TAR.GZ) – STABILER VERTRAG DIESER DATEI
# ─────────────────────────────────────────────────────
# Output ist eine .tar.gz Datei mit folgender Struktur:
#
#   meta.json
#   blobs/<snapchain_id>.blob
#   blobs/<snapchain_id>.blob
#   ...
#
# meta.json enthält ein Manifest:
#   {
#     "format_version": "oroma-export-v3.5",
#     "created_at": <unix_ts>,
#     "count": <n>,
#     "ids": [<id1>, <id2>, ...],
#     "policy": {
#        "delay_days": <int>,
#        "min_quality": <float>,
#        "archived_on_export": <bool>,
#        "only_gaps": <true|false|null>
#     },
#     "items": [
#        {
#          "id": <int>,
#          "sha256": "<sha256(blob)>",
#          "meta": {"ts":..., "quality":..., "status":..., "origin":...}
#        }, ...
#     ]
#   }
#
# „blobs/<id>.blob“ ist der DB-BLOB **1:1** (bytes) – keine Re-Serialisierung.
# Dadurch bleibt das Bundle extrem schnell und unabhängig vom konkreten JSON-Schema
# innerhalb des blobs.
#
# SIDE EFFECTS / GOVERNANCE
# ─────────────────────────
# Nach erfolgreichem Schreiben der tar.gz:
#   1) UPDATE snapchains SET exported=1 WHERE id=?
#   2) optional (CLI --archive):
#        UPDATE snapchains SET status='archived' WHERE id=?
#
# „dry-run“:
# - erzeugt KEINE Datei
# - setzt KEINE Flags
# - liefert aber bereits „path“ + ids + policy als Simulationsergebnis zurück
#
# KONFIGURATION / ENV
# ───────────────────
# OROMA_EXPORT_DELAY_DAYS
#   - Default: 30
#   - Bedeutung: Mindestalter in Tagen, bevor exportiert werden darf
#
# OROMA_BASE
#   - Default: /opt/ai/oroma
#   - Bedeutung: Basispfad, aus dem dieses Modul sein Export-Ziel ableitet
#
# Export-Zielordner in diesem Modul:
#   EXPORT_DIR = "<OROMA_BASE>/exports_out"
#
# (Hinweis: Das ist bewusst **nicht** identisch zu manchen neueren Export-Pipelines,
#  die /opt/ai/oroma/exports oder ExportGate nutzen. Dieses Modul bleibt eigenständig.)
#
# ÖFFENTLICHE API (FUNKTIONEN)
# ────────────────────────────
# list_candidates(days_delay=DEFAULT_DELAY_DAYS, min_quality=0.6, limit=1000, only_gaps=None) -> List[Dict]
#   - Gibt Kandidaten zurück (id, ts, quality, status, origin)
#
# create_export(days_delay=DEFAULT_DELAY_DAYS, min_quality=0.6, limit=1000, *,
#              only_gaps=None, out_dir=None, name=None, archive_after=False, dry_run=False) -> Dict
#   - Baut Bundle + markiert exported=1 + optional archived
#   - Rückgabe enthält ok/path/count/ids/policy (oder ok=False + error)
#
# CLI (PRODUKTIONSNAH)
# ────────────────────
# Scan (nur Kandidaten anzeigen):
#   python3 /opt/ai/oroma/exports/model_export.py --scan --days 30 --min-quality 0.6 --limit 1000
#
# Export:
#   python3 /opt/ai/oroma/exports/model_export.py --export --days 30 --min-quality 0.6 --limit 1000
#
# Nur Gaps:
#   python3 /opt/ai/oroma/exports/model_export.py --export --only-gaps
#
# Gaps ausschließen:
#   python3 /opt/ai/oroma/exports/model_export.py --export --exclude-gaps
#
# Optional archivieren:
#   python3 /opt/ai/oroma/exports/model_export.py --export --archive
#
# Dry-Run:
#   python3 /opt/ai/oroma/exports/model_export.py --export --dry-run
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - meta.json muss im tar Root bleiben (Importer/Tools erwarten das).
# - blobs/<id>.blob Pfadkonvention beibehalten.
# - Export bleibt non-destructive (kein Delete/kein Auto-Disable).
# - Gap-Filter darf nur greifen, wenn gap_flag existiert (sonst kompatibel bleiben).
# - DB-Updates (exported/status) nur nach erfolgreichem Bundle-Write.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import io
import os
import tarfile
import time
import json
import hashlib
from typing import Any, Dict, List, Optional, Tuple

try:
    from core.sql_manager import get_conn, ensure_schema
except Exception as e:
    raise RuntimeError("core.sql_manager.get_conn/ensure_schema erforderlich") from e


# ----------------------------------------------------------------------------- 
# Konfiguration / Defaults
# -----------------------------------------------------------------------------
DEFAULT_DELAY_DAYS = int(os.environ.get("OROMA_EXPORT_DELAY_DAYS", "30"))
BASE_DIR = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
EXPORT_DIR = os.path.join(BASE_DIR, "exports_out")
os.makedirs(EXPORT_DIR, exist_ok=True)


# ----------------------------------------------------------------------------- 
# DB-Hilfen
# -----------------------------------------------------------------------------
def _supports_gap_flag(conn) -> bool:
    try:
        cur = conn.execute("PRAGMA table_info(snapchains)")
        cols = {r[1] for r in cur.fetchall()}
        return "gap_flag" in cols
    except Exception:
        return False


def _now_ts() -> int:
    return int(time.time())


def _eligible_query(days_delay: int,
                    min_quality: float,
                    limit: int,
                    only_gaps: Optional[bool]) -> Tuple[str, Tuple[Any, ...]]:
    cutoff = _now_ts() - int(days_delay) * 86400
    where = ["exported=0", "ts <= ?", "quality >= ?"]
    params: List[Any] = [int(cutoff), float(min_quality)]

    if only_gaps is True:
        where.append("gap_flag=1")
    elif only_gaps is False:
        where.append("COALESCE(gap_flag,0)=0")

    sql = (
        "SELECT id, ts, quality, status, origin "
        "FROM snapchains "
        "WHERE " + " AND ".join(where) +
        " ORDER BY ts ASC LIMIT ?"
    )
    params.append(int(limit))
    return sql, tuple(params)


def list_candidates(days_delay: int = DEFAULT_DELAY_DAYS,
                    min_quality: float = 0.6,
                    limit: int = 1000,
                    only_gaps: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Listet export-eligible SnapChains (keine Export-Flag, Delay-Fenster erfüllt, Quality-Grenze)."""
    ensure_schema()
    conn = get_conn()
    has_gap = _supports_gap_flag(conn)
    og = only_gaps if has_gap else None

    sql, params = _eligible_query(days_delay, min_quality, limit, og)
    cur = conn.execute(sql, params)
    rows = cur.fetchall() or []
    return [
        {
            "id": int(r["id"]),
            "ts": int(r["ts"]),
            "quality": float(r["quality"]),
            "status": r["status"],
            "origin": r["origin"],
        }
        for r in rows
    ]


def _fetch_blobs(conn, ids: List[int]) -> List[Tuple[int, bytes]]:
    if not ids:
        return []
    out: List[Tuple[int, bytes]] = []
    B = 800
    for i in range(0, len(ids), B):
        chunk = ids[i:i+B]
        qmarks = ",".join("?" for _ in chunk)
        cur = conn.execute(
            f"SELECT id, blob FROM snapchains WHERE id IN ({qmarks})",
            tuple(int(x) for x in chunk)
        )
        for row in cur.fetchall() or []:
            out.append((int(row["id"]), bytes(row["blob"])))
    return out


def _mark_exported(conn, ids: List[int]) -> int:
    if not ids:
        return 0
    with conn:
        for sid in ids:
            conn.execute("UPDATE snapchains SET exported=1 WHERE id=?", (int(sid),))
    return len(ids)


def _set_status(conn, ids: List[int], status: str = "archived") -> int:
    if not ids:
        return 0
    with conn:
        for sid in ids:
            conn.execute("UPDATE snapchains SET status=? WHERE id=?", (status, int(sid)))
    return len(ids)


# ----------------------------------------------------------------------------- 
# Bundle-Erstellung
# -----------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _build_manifest(ts: int,
                    ids: List[int],
                    policy: Dict[str, Any],
                    per_item_meta: Optional[Dict[int, Dict[str, Any]]] = None,
                    per_item_hash: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    return {
        "format_version": "oroma-export-v3.5",
        "created_at": int(ts),
        "count": len(ids),
        "ids": ids,
        "policy": policy,
        "items": [
            {
                "id": int(i),
                "sha256": per_item_hash.get(i) if per_item_hash else None,
                "meta": per_item_meta.get(i) if per_item_meta else None,
            }
            for i in ids
        ],
    }


def create_export(days_delay: int = DEFAULT_DELAY_DAYS,
                  min_quality: float = 0.6,
                  limit: int = 1000,
                  *,
                  only_gaps: Optional[bool] = None,
                  out_dir: Optional[str] = None,
                  name: Optional[str] = None,
                  archive_after: bool = False,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Baut ein .tar.gz mit eligible SnapChains (setzt Flags, optional status='archived')."""
    ensure_schema()
    conn = get_conn()

    candidates = list_candidates(days_delay, min_quality, limit, only_gaps)
    if not candidates:
        return {"ok": False, "error": "no eligible candidates", "count": 0}

    ids = [c["id"] for c in candidates]
    ts = _now_ts()
    out_base = out_dir or EXPORT_DIR
    os.makedirs(out_base, exist_ok=True)
    fname = f"{name}.tar.gz" if name else f"oroma_export_{ts}.tar.gz"
    out_path = os.path.join(out_base, fname)

    blobs = _fetch_blobs(conn, ids)
    if not blobs:
        return {"ok": False, "error": "no blobs fetched", "count": 0}

    per_hash: Dict[int, str] = {}
    per_meta: Dict[int, Dict[str, Any]] = {}
    rowmap = {c["id"]: c for c in candidates}
    for sid, b in blobs:
        per_hash[sid] = _sha256(b)
        row = rowmap.get(sid, {})
        per_meta[sid] = {
            "ts": row.get("ts"),
            "quality": row.get("quality"),
            "status": row.get("status"),
            "origin": row.get("origin"),
        }

    policy = {
        "delay_days": int(days_delay),
        "min_quality": float(min_quality),
        "archived_on_export": bool(archive_after),
        "only_gaps": bool(only_gaps) if only_gaps is not None else None,
    }
    manifest = _build_manifest(ts, ids, policy, per_item_meta=per_meta, per_item_hash=per_hash)

    if dry_run:
        return {"ok": True, "dry_run": True, "path": out_path, "count": len(ids), "ids": ids, "policy": policy}

    with tarfile.open(out_path, "w:gz") as tar:
        meta_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        meta_info = tarfile.TarInfo(name="meta.json")
        meta_info.size = len(meta_bytes)
        meta_info.mtime = ts
        tar.addfile(meta_info, io.BytesIO(meta_bytes))
        for sid, blob in blobs:
            binfo = tarfile.TarInfo(name=f"blobs/{sid}.blob")
            binfo.size = len(blob)
            binfo.mtime = ts
            tar.addfile(binfo, io.BytesIO(blob))

    _mark_exported(conn, ids)
    if archive_after:
        _set_status(conn, ids, "archived")

    return {"ok": True, "path": out_path, "count": len(ids), "ids": ids, "policy": policy}


# ----------------------------------------------------------------------------- 
# CLI
# -----------------------------------------------------------------------------
def _parse_args():
    import argparse
    p = argparse.ArgumentParser(
        prog="ORÓMA v3.5 – model_export",
        description="Exportiert eligible SnapChains in ein .tar.gz-Bundle (Policy-Fix, Gap-Filter, optional archive).",
    )
    p.add_argument("--scan", action="store_true", help="Nur Kandidaten anzeigen (keine Dateien schreiben)")
    p.add_argument("--export", action="store_true", help="Export-Bundle erstellen (.tar.gz)")
    p.add_argument("--days", type=int, default=DEFAULT_DELAY_DAYS, help="Delay-Fenster in Tagen (default aus ENV)")
    p.add_argument("--min-quality", type=float, default=0.6, help="Qualitätsschwelle")
    p.add_argument("--limit", type=int, default=1000, help="max. Kandidaten")
    p.add_argument("--only-gaps", action="store_true", help="Nur gap_flag=1 exportieren (falls Spalte existiert)")
    p.add_argument("--exclude-gaps", action="store_true", help="gap_flag=1 ausschließen (falls Spalte existiert)")
    p.add_argument("--out", default=None, help="Zielverzeichnis (default: exports_out)")
    p.add_argument("--name", default=None, help="Dateiname ohne .tar.gz")
    p.add_argument("--archive", action="store_true", help="Nach Export status='archived' setzen")
    p.add_argument("--dry-run", action="store_true", help="Ablauf simulieren (kein Schreiben/Update)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    ensure_schema()

    only_gaps: Optional[bool] = None
    if args.only_gaps and args.exclude_gaps:
        print(json.dumps({"ok": False, "error": "choose either --only-gaps OR --exclude-gaps"}, ensure_ascii=False))
        return 2
    if args.only_gaps:
        only_gaps = True
    if args.exclude_gaps:
        only_gaps = False

    if args.scan:
        rows = list_candidates(args.days, args.min_quality, args.limit, only_gaps)
        print(json.dumps({"ok": True, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
        return 0

    if args.export:
        res = create_export(args.days, args.min_quality, args.limit,
                            only_gaps=only_gaps, out_dir=args.out,
                            name=args.name, archive_after=args.archive,
                            dry_run=args.dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    print(json.dumps({"ok": False, "error": "use --scan or --export"}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
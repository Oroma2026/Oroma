#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/oroma_snapchain_format_audit.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     SnapChain Format Audit – read-only Matrix für Blob-Formate,
#            Policy-Trainierbarkeit und Dream-Verarbeitbarkeit
# Version:   v0.1.0-readonly
# Stand:     2026-07-05
# Autor:     ORÓMA Project
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool prüft ausschließlich lesend, welche Formate in `snapchains.blob`
# vorkommen und ob sie über `core.snapchain_adapters` normalisiert werden können.
# Es beantwortet pro Origin/Namespace:
#   - Gibt es Rows?
#   - Welches Format wird erkannt?
#   - Ist der Trace policy-trainierbar?
#   - Ist der Trace dream-processable?
#   - Warum wird etwas übersprungen?
#
# NICHT-ZIELE / SAFETY
# ───────────────────
# - Keine DB-Writes.
# - Kein DBWriter.
# - Kein DreamWorker-Umbau.
# - Keine policy_rules-Änderung.
# - Kein PTZ / keine Motorik.
# - Für große Live-DBs standardmäßig LIMIT-basiert, keine Volltabellen-Scans.
#
# BEISPIELE
# ─────────
#   PYTHONPATH=. python3 tools/oroma_snapchain_format_audit.py --limit 30
#   PYTHONPATH=. python3 tools/oroma_snapchain_format_audit.py --origin game:snake3d --limit 20 --json
#   PYTHONPATH=. python3 tools/oroma_snapchain_format_audit.py --games --per-origin-limit 10
#
# =============================================================================
# END HEADER
# =============================================================================
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

from core.snapchain_adapters import normalize_snapchain_blob, summarize_trace  # noqa: E402


DEFAULT_GAME_ORIGINS = [
    "game:snake",
    "game:snake3d",
    "game:pong",
    "game:connect4",
    "game:chess",
    "game:chess2",
    "game:chesspro",
    "game:tictactoe",
    "game:memory",
    "game:sudoku",
    "game:flappy",
    "game:ctf",
    "game:hideseek",
    "game:tetris",
    "game:memorymaze_hybrid",
]


def _row_get(row: Any, key: str, idx: int = 0) -> Any:
    try:
        if hasattr(row, "keys"):
            return row[key]
    except Exception:
        pass
    try:
        return row[idx]
    except Exception:
        return None


def _get_conn(db_path: str | None = None):
    if db_path:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    from core import sql_manager  # type: ignore
    return sql_manager.get_conn()


def _fetch_recent(conn, *, limit: int) -> List[Any]:
    return list(conn.execute(
        """
        SELECT id, ts, origin, namespace, status, notes, version, source_id, blob
          FROM snapchains
      ORDER BY id DESC
         LIMIT ?
        """,
        (int(max(0, limit)),),
    ).fetchall() or [])


def _fetch_origin(conn, *, origin: str, limit: int) -> List[Any]:
    return list(conn.execute(
        """
        SELECT id, ts, origin, namespace, status, notes, version, source_id, blob
          FROM snapchains
         WHERE origin = ?
      ORDER BY id DESC
         LIMIT ?
        """,
        (str(origin), int(max(0, limit))),
    ).fetchall() or [])


def _summarize_rows(rows: Iterable[Any]) -> Dict[str, Any]:
    rows = list(rows or [])
    by_origin: Dict[str, Dict[str, Any]] = {}
    global_formats: Counter[str] = Counter()
    global_skips: Counter[str] = Counter()

    for row in rows:
        rid = _row_get(row, "id", 0)
        origin = str(_row_get(row, "origin", 2) or "")
        namespace = str(_row_get(row, "namespace", 3) or "")
        source_id = _row_get(row, "source_id", 7)
        blob = _row_get(row, "blob", 8)
        trace = normalize_snapchain_blob(blob, origin=origin, namespace=namespace, source_id=source_id)
        td = summarize_trace(trace)
        key = origin or namespace or "(empty)"
        if key not in by_origin:
            by_origin[key] = {
                "origin": origin,
                "namespace_examples": Counter(),
                "rows": 0,
                "max_id": None,
                "formats": Counter(),
                "policy_trainable": 0,
                "dream_processable": 0,
                "adapter_ok": 0,
                "skip_reasons": Counter(),
                "feature_dims": Counter(),
                "sample": None,
            }
        g = by_origin[key]
        g["rows"] += 1
        try:
            g["max_id"] = max(int(g["max_id"] or 0), int(rid or 0))
        except Exception:
            g["max_id"] = rid
        if namespace:
            g["namespace_examples"][namespace] += 1
        fmt = str(td.get("source_format") or "unknown")
        g["formats"][fmt] += 1
        global_formats[fmt] += 1
        if td.get("ok"):
            g["adapter_ok"] += 1
        if td.get("policy_trainable"):
            g["policy_trainable"] += 1
        if td.get("dream_processable"):
            g["dream_processable"] += 1
        sr = str(td.get("skip_reason") or "")
        if sr:
            g["skip_reasons"][sr] += 1
            global_skips[sr] += 1
        try:
            g["feature_dims"][int(td.get("feature_dim") or 0)] += 1
        except Exception:
            pass
        if g["sample"] is None:
            g["sample"] = {
                "id": rid,
                "notes": _row_get(row, "notes", 5),
                "version": _row_get(row, "version", 6),
                **td,
            }

    # Convert counters to plain dicts for JSON stability.
    out_origins: List[Dict[str, Any]] = []
    for _, g in sorted(by_origin.items(), key=lambda kv: int(kv[1].get("max_id") or 0), reverse=True):
        out_origins.append({
            "origin": g["origin"],
            "rows": g["rows"],
            "max_id": g["max_id"],
            "namespaces": dict(g["namespace_examples"].most_common()),
            "formats": dict(g["formats"].most_common()),
            "adapter_ok": g["adapter_ok"],
            "policy_trainable": g["policy_trainable"],
            "dream_processable": g["dream_processable"],
            "skip_reasons": dict(g["skip_reasons"].most_common()),
            "feature_dims": dict(g["feature_dims"].most_common()),
            "sample": g["sample"],
        })
    return {
        "rows": len(rows),
        "formats": dict(global_formats.most_common()),
        "skip_reasons": dict(global_skips.most_common()),
        "origins": out_origins,
    }


def _print_human(result: Dict[str, Any]) -> None:
    print("ORÓMA SnapChain Format Audit (read-only)")
    print(f"rows={result.get('rows', 0)} formats={result.get('formats', {})}")
    skips = result.get("skip_reasons") or {}
    if skips:
        print(f"skip_reasons={skips}")
    print("")
    print("Origin | Rows | Format | Adapter | Policy | Dream | MaxID | Skip")
    print("---|---:|---|---:|---:|---:|---:|---")
    for g in result.get("origins", []):
        fmt = ",".join(f"{k}:{v}" for k, v in (g.get("formats") or {}).items()) or "-"
        skip = ",".join(f"{k}:{v}" for k, v in (g.get("skip_reasons") or {}).items()) or "-"
        print(
            f"{g.get('origin') or '-'} | {g.get('rows')} | {fmt} | "
            f"{g.get('adapter_ok')} | {g.get('policy_trainable')} | {g.get('dream_processable')} | "
            f"{g.get('max_id')} | {skip}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only SnapChain Blob-Format Audit für ORÓMA.")
    ap.add_argument("--db", default="", help="Optionaler Pfad zu oroma.db; sonst core.sql_manager.get_conn().")
    ap.add_argument("--limit", type=int, default=100, help="Limit für global recent scan (Default: 100).")
    ap.add_argument("--origin", action="append", default=[], help="Origin gezielt prüfen; mehrfach nutzbar.")
    ap.add_argument("--games", action="store_true", help="Bekannte game:* Origins gezielt prüfen (indexed origin queries).")
    ap.add_argument("--per-origin-limit", type=int, default=20, help="Limit pro --origin/--games Origin.")
    ap.add_argument("--json", action="store_true", help="JSON statt Markdown/Human-Ausgabe.")
    args = ap.parse_args()

    origins = list(args.origin or [])
    if args.games:
        for o in DEFAULT_GAME_ORIGINS:
            if o not in origins:
                origins.append(o)

    try:
        conn = _get_conn(args.db or None)
    except Exception as exc:
        print(f"ERROR: DB open failed: {exc}", file=sys.stderr)
        return 2

    try:
        rows: List[Any] = []
        if origins:
            for origin in origins:
                try:
                    rows.extend(_fetch_origin(conn, origin=origin, limit=int(args.per_origin_limit)))
                except Exception as exc:
                    # Origin errors should be visible but not abort the whole matrix.
                    print(f"WARN: origin {origin!r} failed: {exc}", file=sys.stderr)
        else:
            rows = _fetch_recent(conn, limit=int(args.limit))
        result = _summarize_rows(rows)
        result["mode"] = "origins" if origins else "recent"
        result["requested_origins"] = origins
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_human(result)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

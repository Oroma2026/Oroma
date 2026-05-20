#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pfad:    tools/meta_snap_indexer_runner.py
Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
Version: v3.7.x (line-compatible)
Stand:   2026-04-29
Autor:   Jörg Werner (public), ORÓMA · KI-JWG-X1 (intern)

ZWECK
-----
DBWriter-sichere Reaktivierung des historischen AutoMeta-Kompressionspfads:

- scannt neue `meta_snaps` (id > cursor) mit `label LIKE 'compressed_%'`
- materialisiert sie konservativ als `object_nodes` (kind='object', label=meta.label)
- legt eine minimale Relation von einem Anchor-Node zu diesem compressed Node
- schreibt Cursor/Status atomar nach `/opt/ai/oroma/data/state/meta_snap_indexer_state.json`
- schreibt Stage-B Stats nach `stats.db` (stats_points), DBWriter-only

SICHERHEIT / REGELN
-------------------
- DBWriter-first: OROMA_DBW_ENABLE=1 erforderlich.
- Keine destruktiven Operationen.
- Dedupe: wenn object_nodes.label bereits existiert, wird übersprungen.
- Small-batch via `--max-n`.

Ausführung:
  cd /opt/ai/oroma
  PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 python3 tools/meta_snap_indexer_runner.py --once --max-n 250
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, Optional, Tuple

# Robust import when invoked as `python3 tools/...`
if __package__ is None and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "core")):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import db_writer_client  # type: ignore
from core.log_guard import log_suppressed  # type: ignore


STATE_PATH = "/opt/ai/oroma/data/state/meta_snap_indexer_state.json"


def _now_ts() -> int:
    return int(time.time())


def _db_path_oroma() -> str:
    base = os.environ.get("OROMA_DATA_DIR", "/opt/ai/oroma/data")
    return os.path.join(base, "oroma.db")


def _dbw_required() -> None:
    if os.environ.get("OROMA_DBW_ENABLE", "") not in ("1", "true", "True", "YES", "yes"):
        raise RuntimeError("DBWriter required (set OROMA_DBW_ENABLE=1)")
    if not db_writer_client.ping(timeout_ms=500):
        raise RuntimeError("DBWriter required but not available (db_writer_client.ping failed)")


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _lastrowid(res: Any) -> int:
    if res is None:
        return 0
    if isinstance(res, int):
        return int(res)
    if isinstance(res, dict):
        if res.get("lastrowid") is not None:
            try:
                return int(res["lastrowid"])
            except Exception:
                return 0
        r = res.get("result")
        if isinstance(r, dict) and r.get("lastrowid") is not None:
            try:
                return int(r["lastrowid"])
            except Exception:
                return 0
    return 0


def _stats_write(ts: int, series: str, value: float, src_uid: str) -> None:
    db_writer_client.exec(
        "INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [int(ts), str(series), float(value), "meta_snap_indexer", 0, None, str(src_uid)],
        tag="compress.meta.indexer.stats",
        priority="normal",
        timeout_ms=2000,
        expect="rowcount",
        db="stats",
    )


def _ensure_anchor(ts: int) -> int:
    label = "origin:meta_snaps"
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # NOTE: missing-only newest-first fetch (no cursor dependence).
    try:
        row = con.execute(
            "SELECT id FROM object_nodes WHERE kind='origin' AND label=? ORDER BY id DESC LIMIT 1",
            (label,),
        ).fetchone()
        if row:
            return int(row["id"])
    finally:
        try:
            con.close()
        except Exception:
            pass

    meta = {"source": "meta_snap_indexer", "kind": "origin"}
    res = db_writer_client.exec(
        "INSERT INTO object_nodes(kind,label,meta_json,created_ts) VALUES (?,?,?,?)",
        ["origin", label, json.dumps(meta, ensure_ascii=False), int(ts)],
        tag="compress.meta.indexer.anchor",
        priority="normal",
        timeout_ms=4000,
        expect="lastrowid",
        db="oroma",
    )
    return _lastrowid(res)


def _object_node_exists(label: str) -> bool:
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # NOTE: missing-only newest-first fetch (no cursor dependence).
    try:
        row = con.execute("SELECT 1 FROM object_nodes WHERE label=? LIMIT 1", (label,)).fetchone()
        return bool(row)
    finally:
        try:
            con.close()
        except Exception:
            pass



def _fetch_meta_snaps_missing_newest(max_n: int, min_score: float = 0.0):
    """Fetch newest missing compressed_% MetaSnaps (not yet materialized into object_nodes.label)."""
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if min_score and min_score > 0:
            rows = con.execute(
                """
                SELECT m.id, m.label, m.score, m.sources
                FROM meta_snaps m
                WHERE m.label LIKE 'compressed_%'
                  AND m.score IS NOT NULL
                  AND m.score >= ?
                  AND NOT EXISTS (SELECT 1 FROM object_nodes o WHERE o.label = m.label)
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (float(min_score), int(max_n)),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT m.id, m.label, m.score, m.sources
                FROM meta_snaps m
                WHERE m.label LIKE 'compressed_%'
                  AND NOT EXISTS (SELECT 1 FROM object_nodes o WHERE o.label = m.label)
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (int(max_n),),
            ).fetchall()
        out = []
        max_seen = 0
        for r in rows:
            mid = int(r["id"])
            max_seen = max(max_seen, mid)
            out.append((mid, r["label"], r["score"], r["sources"]))
        return max_seen, out
    finally:
        try:
            con.close()
        except Exception:
            pass

def _fetch_meta_snaps_since(last_id: int, max_n: int) -> Tuple[int, list]:
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # NOTE: missing-only newest-first fetch (no cursor dependence).
    try:
        rows = con.execute(
            "SELECT m.id, m.label, m.score, m.sources "
            "FROM meta_snaps m "
            "WHERE m.label LIKE 'compressed_%' "
            "  AND NOT EXISTS (SELECT 1 FROM object_nodes o WHERE o.label = m.label) "
            "ORDER BY m.id DESC LIMIT ?",
            (int(max_n),),
        ).fetchall()
        out = []
        max_seen = int(last_id)
        for r in rows:
            mid = int(r["id"])
            max_seen = max(max_seen, mid)
            out.append((mid, r["label"], r["score"], r["sources"]))
        return max_seen, out
    finally:
        try:
            con.close()
        except Exception:
            pass


def _insert_object_node_from_meta(anchor_id: int, meta_id: int, label: str, score: Any, sources: Any, ts: int) -> Optional[int]:
    if _object_node_exists(label):
        return None
    meta = {
        "source": "meta_snap_indexer",
        "meta_snap_id": int(meta_id),
        "score": score,
        "sources": sources,
    }
    res = db_writer_client.exec(
        "INSERT INTO object_nodes(kind,label,meta_json,created_ts) VALUES (?,?,?,?)",
        ["object", str(label), json.dumps(meta, ensure_ascii=False), int(ts)],
        tag="compress.meta.indexer.node",
        priority="normal",
        timeout_ms=4000,
        expect="lastrowid",
        db="oroma",
    )
    node_id = _lastrowid(res)
    if node_id <= 0:
        return None

    db_writer_client.exec(
        "INSERT INTO object_relations(a_id,relation,b_id,confidence,source_scene_id,ts,notes) VALUES (?,?,?,?,?,?,?)",
        [int(anchor_id), "meta_compressed", int(node_id), 1.0, None, int(ts), f"meta_snap_id={meta_id}"],
        tag="compress.meta.indexer.rel",
        priority="normal",
        timeout_ms=4000,
        expect="lastrowid",
        db="oroma",
    )
    return node_id


def run_once(max_n: int, min_score: float = 0.0, budget_per_day: int = 0) -> Dict[str, Any]:
    _dbw_required()
    t0 = time.time()
    ts = _now_ts()

    st = _load_state()
    day = time.strftime('%Y-%m-%d', time.localtime(ts))
    created_today = int(st.get('created_today') or 0)
    state_day = str(st.get('day') or '')
    if state_day != day:
        created_today = 0

    last_id = int(st.get("last_meta_id") or 0)

    max_seen, metas = _fetch_meta_snaps_missing_newest(max_n=max_n, min_score=min_score)
    anchor_id = _ensure_anchor(ts)

    created = 0
    skipped_existing = 0
    for meta_id, label, score, sources in metas:
        if budget_per_day and budget_per_day > 0 and created_today >= budget_per_day:
            break

        try:
            nid = _insert_object_node_from_meta(anchor_id, int(meta_id), str(label), score, sources, ts)
            if nid is None:
                skipped_existing += 1
            else:
                created += 1
                created_today += 1
        except Exception:
            continue

    try:
        _stats_write(ts, "compress.meta.indexed.created_24h", float(created), f"{ts}:created")
        _stats_write(ts, "compress.meta.indexed.skipped_existing_24h", float(skipped_existing), f"{ts}:skipped")
        _stats_write(ts, "compress.meta.indexed.max_seen_id", float(max_seen), f"{ts}:max_seen")
    except Exception as e:
        log_suppressed("meta_snap_indexer.stats", key="stats write failed", msg="stats_points write failed", exc=e)

    _atomic_write_json(
        STATE_PATH,
        {
            "last_meta_id": int(max_seen),
            "last_run_ts": int(ts),
            "last_created": int(created),
            "last_skipped_existing": int(skipped_existing),
            "day": day,
            "created_today": int(created_today),
            "budget_per_day": int(budget_per_day),
        },
    )

    return {
        "ok": True,
        "max_n": int(max_n),
        "last_id_before": int(last_id),
        "last_id_after": int(max_seen),
        "meta_rows": int(len(metas)),
        "created_nodes": int(created),
        "skipped_existing": int(skipped_existing),
        "dt_sec": float(round(time.time() - t0, 3)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA MetaSnap Indexer Runner (DBWriter-safe)")
    ap.add_argument("--once", action="store_true", default=True)
    ap.add_argument("--max-n", type=int, default=int(os.environ.get("OROMA_META_SNAP_INDEXER_MAX_N", "250")))
    ap.add_argument("--min-score", type=float, default=float(os.environ.get("OROMA_META_SNAP_INDEXER_MIN_SCORE", "0")))
    ap.add_argument("--budget-per-day", type=int, default=int(os.environ.get("OROMA_META_SNAP_INDEXER_BUDGET_PER_DAY", "0")))
    args = ap.parse_args()
    try:
        res = run_once(max_n=int(args.max_n), min_score=float(args.min_score), budget_per_day=int(args.budget_per_day))
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except Exception as e:
        log_suppressed("meta_snap_indexer.error", key="meta_snap_indexer failed", msg="meta_snap_indexer failed", exc=e)
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

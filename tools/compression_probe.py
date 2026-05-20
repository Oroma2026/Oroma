#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORÓMA – Offline-Realtime-Organic-Memory-AI
File:        /opt/ai/oroma/tools/compression_probe.py
Version:     v3.7.3 (project line)
Stand:       2026-04-29
Author:      Jörg Werner (ORÓMA) · Internal: ORÓMA · KI-JWG-X1
License:     Project License (see repository/Zenodo); Probe code intended for MIT in software snapshot.

Purpose
-------
Stage-A (measure-only) probe for "Kompression / Cognitive Depth".
This tool answers the practical question:
  "Why is compressed_share still 0.00%?"

It measures two independent axes:
  (1) SnapChain redundancy: do we have repeatable patterns at all?
  (2) Materialization: are 'compressed_*' concepts created in MetaSnaps / ObjectGraph?

Key idea
--------
Learning UI's `compressed_share` is derived from ObjectGraph activity:
  object_nodes.created_ts in window AND label LIKE 'compressed_%'

So Stage A must explicitly count:
  - meta_snaps(label LIKE 'compressed_%')   [DreamForgetting emits these]
  - object_nodes(label LIKE 'compressed_%') [required for UI compressed_share]

Safety / Patch-Gate constraints
-------------------------------
- Minimal-invasive: READS from oroma.db only.
- Writes only to stats.db.stats_points via DBWriter (DBWriter-only; no direct sqlite fallback).
- Bounded runtime and bounded scans; fail-open behaviour for UI/Orchestrator stability.

CLI
---
  python3 tools/compression_probe.py --window-sec 86400 --limit-chains 5000 --max-runtime-s 20
Optional:
  --fp-dims 32 --fp-decimals 2 --min-repeat 3 --top-origins 8

Environment
-----------
- Requires DBWriter socket running (oroma-db-writer.service).
- Uses db_writer_client.exec(..., db="stats") with required stats_points columns.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import math
import hashlib
import sqlite3
from typing import Dict, List, Tuple, Any

# Ensure imports work when executed from anywhere
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import db_writer_client  # DBWriter-only
from core.log_guard import log_suppressed


def _now_ts() -> int:
    return int(time.time())


def _safe_origin_key(origin: str) -> str:
    # Make origin safe for metric series keys.
    # Keep it stable; compress characters.
    s = (origin or "unknown").strip()
    s = s.replace(":", "_").replace("/", "_").replace(".", "_").replace("-", "_")
    s = "".join(ch for ch in s if (ch.isalnum() or ch == "_"))
    return s[:48] if len(s) > 48 else s


def _connect_ro(db_path: str, timeout_sec: float = 0.25) -> sqlite3.Connection:
    # Read-only connection; do not change WAL mode here.
    # Use URI mode to force readonly.
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only = 1")
    except Exception:
        pass
    try:
        con.execute("PRAGMA busy_timeout = 200")
    except Exception:
        pass
    return con


def _fingerprint(vec: List[float], fp_dims: int, fp_decimals: int) -> str:
    # Stable coarse fingerprint:
    # - Take first fp_dims floats
    # - Round to fp_decimals
    # - Hash to fixed-length key
    if not vec:
        return ""
    d = min(int(fp_dims), len(vec))
    # Avoid heavy formatting; use scaled ints.
    scale = 10 ** int(fp_decimals)
    ints = []
    for i in range(d):
        try:
            x = float(vec[i])
            ints.append(int(round(x * scale)))
        except Exception:
            ints.append(0)
    b = ",".join(map(str, ints)).encode("utf-8", "replace")
    return hashlib.sha1(b).hexdigest()  # stable, short enough


def _dbw_exec(sql: str, params: List[Any], tag: str) -> None:
    # DBWriter-only write path
    db_writer_client.exec(
        sql,
        params,
        tag=tag,
        priority="normal",
        timeout_ms=2500,
        expect="rowcount",
        db="stats",
    )


def _write_stat_many(ts: int, src_uid: str, series_to_value: Dict[str, float]) -> None:
    # stats_points schema requires: ts, series, value, src_table, src_id, meta, src_uid
    for series, value in series_to_value.items():
        _dbw_exec(
            "INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [int(ts), str(series), float(value), "compression_probe", 0, None, str(src_uid)],
            tag="compress.a.probe",
        )


def run_once(
    window_sec: int,
    limit_chains: int,
    max_runtime_s: int,
    fp_dims: int,
    fp_decimals: int,
    min_repeat: int,
    top_origins: int,
) -> Dict[str, Any]:
    t0 = time.monotonic()
    now = _now_ts()
    since = now - int(window_sec)

    db_oroma = os.path.join(BASE_DIR, "data", "oroma.db")
    # NOTE: In live system this is /opt/ai/oroma/data/oroma.db (BASE_DIR already /opt/ai/oroma)
    con = _connect_ro(db_oroma, timeout_sec=0.25)

    scanned = 0
    missing_vec = 0

    fp_counts: Dict[str, int] = {}
    fp_counts_by_origin: Dict[str, Dict[str, int]] = {}

    # Materialization counters (what UI actually needs)
    metasnap_comp = 0
    objnode_comp = 0

    try:
        # 1) Materialization: compressed_* in meta_snaps (DreamForgetting produces these)
        try:
            r = con.execute(
                "SELECT COUNT(*) AS n FROM meta_snaps WHERE created_ts>=? AND label LIKE 'compressed_%'",
                (int(since),),
            ).fetchone()
            metasnap_comp = int((r["n"] if hasattr(r, "keys") else r[0]) or 0)
        except Exception:
            metasnap_comp = 0

        # 2) Materialization: compressed_* in object_nodes (this drives compressed_share)
        try:
            r = con.execute(
                "SELECT COUNT(*) AS n FROM object_nodes WHERE created_ts>=? AND label LIKE 'compressed_%'",
                (int(since),),
            ).fetchone()
            objnode_comp = int((r["n"] if hasattr(r, "keys") else r[0]) or 0)
        except Exception:
            objnode_comp = 0

        # 3) Redundancy: recent snapchains
        rows = con.execute(
            "SELECT id, ts, origin, blob FROM snapchains "
            "WHERE ts>=? ORDER BY id DESC LIMIT ?",
            (int(since), int(limit_chains)),
        ).fetchall()

        for r in rows:
            if (time.monotonic() - t0) > float(max_runtime_s):
                break
            scanned += 1

            origin = str(r["origin"] if hasattr(r, "keys") else r[2] or "")
            blob = r["blob"] if hasattr(r, "keys") else r[3]

            # blob can be TEXT or BLOB depending on table; handle both
            try:
                if isinstance(blob, (bytes, bytearray)):
                    o = json.loads(blob.decode("utf-8", "replace"))
                else:
                    o = json.loads(str(blob))
            except Exception:
                missing_vec += 1
                continue

            v = o.get("v") or []
            if not isinstance(v, list) or not v:
                missing_vec += 1
                continue

            fp = _fingerprint(v, fp_dims=fp_dims, fp_decimals=fp_decimals)
            if not fp:
                missing_vec += 1
                continue

            fp_counts[fp] = fp_counts.get(fp, 0) + 1
            d = fp_counts_by_origin.get(origin)
            if d is None:
                d = {}
                fp_counts_by_origin[origin] = d
            d[fp] = d.get(fp, 0) + 1

    finally:
        try:
            con.close()
        except Exception:
            pass

    # Aggregate
    unique_fp = len(fp_counts)
    repeat_ge_2 = sum(1 for c in fp_counts.values() if c >= 2)
    repeat_ge_3 = sum(1 for c in fp_counts.values() if c >= 3)
    repeat_ge_5 = sum(1 for c in fp_counts.values() if c >= 5)

    # Per-origin candidates: compute repeat_ge_min_repeat keys, but only for top origins by volume
    origin_counts = sorted(
        ((origin, sum(d.values())) for origin, d in fp_counts_by_origin.items()),
        key=lambda x: -x[1],
    )
    top = origin_counts[: max(0, int(top_origins))]
    per_origin_series: Dict[str, float] = {}
    for origin, _c in top:
        d = fp_counts_by_origin.get(origin) or {}
        rep = sum(1 for c in d.values() if c >= int(min_repeat))
        per_origin_series[f"compress.a.origin.{_safe_origin_key(origin)}.repeat_ge_{int(min_repeat)}_24h"] = float(rep)

    # Write stats_points via DBWriter
    ts = _now_ts()
    src_uid = f"{ts}.{os.getpid()}"
    series = {
        "compress.a.events_24h": float(scanned),
        "compress.a.unique_fp_24h": float(unique_fp),
        "compress.a.repeat_ge_2_24h": float(repeat_ge_2),
        "compress.a.repeat_ge_3_24h": float(repeat_ge_3),
        "compress.a.repeat_ge_5_24h": float(repeat_ge_5),
        "compress.a.fp.dims_24h": float(fp_dims),
        "compress.a.fp.decimals_24h": float(fp_decimals),
        "compress.a.gate.missing_vec_24h": float(missing_vec),
        "compress.a.metasnap.compressed_24h": float(metasnap_comp),
        "compress.a.objnode.compressed_24h": float(objnode_comp),
        "compress.a.runtime_ms": float((time.monotonic() - t0) * 1000.0),
    }
    series.update(per_origin_series)

    try:
        _write_stat_many(ts=ts, src_uid=src_uid, series_to_value=series)
    except Exception as e:
        # Must be visible but must not crash system; log guarded
        log_suppressed(logger="compression_probe.dbw", key="compression_probe.dbw", msg="stats_points write failed", exc=e)
        return {"ok": False, "error": "dbw_write_failed", "scanned": scanned}

    return {
        "ok": True,
        "window_sec": int(window_sec),
        "since_ts": int(since),
        "scanned": int(scanned),
        "unique_fp": int(unique_fp),
        "repeat_ge_2": int(repeat_ge_2),
        "repeat_ge_3": int(repeat_ge_3),
        "repeat_ge_5": int(repeat_ge_5),
        "missing_vec": int(missing_vec),
        "metasnap_compressed": int(metasnap_comp),
        "objnode_compressed": int(objnode_comp),
        "fp_dims": int(fp_dims),
        "fp_decimals": int(fp_decimals),
        "dt_sec": round(float(time.monotonic() - t0), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-sec", type=int, default=86400)
    ap.add_argument("--limit-chains", type=int, default=5000)
    ap.add_argument("--max-runtime-s", type=int, default=20)
    ap.add_argument("--fp-dims", type=int, default=32)
    ap.add_argument("--fp-decimals", type=int, default=2)
    ap.add_argument("--min-repeat", type=int, default=3)
    ap.add_argument("--top-origins", type=int, default=8)
    args = ap.parse_args()

    try:
        res = run_once(
            window_sec=int(args.window_sec),
            limit_chains=int(args.limit_chains),
            max_runtime_s=int(args.max_runtime_s),
            fp_dims=int(args.fp_dims),
            fp_decimals=int(args.fp_decimals),
            min_repeat=int(args.min_repeat),
            top_origins=int(args.top_origins),
        )
        print(json.dumps({"compression_stage": "A", **res}, ensure_ascii=False))
        return 0 if res.get("ok") else 2
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        log_suppressed(logger="compression_probe.error", key="compression_probe.error", msg="probe failed", exc=e)
        print(json.dumps({"compression_stage": "A", "ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
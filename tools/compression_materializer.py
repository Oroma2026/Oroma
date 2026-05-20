#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pfad:    tools/compression_materializer.py
Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
Version: v3.7.x (line-compatible)
Stand:   2026-04-29
Autor:   Jörg Werner (public), ORÓMA · KI-JWG-X1 (intern)

ZWECK
-----
Stage B (minimal-invasive) für "Kompression / Cognitive Depth":
- Nimmt erkannte Redundanz (Wiederholung) aus SnapChains und materialisiert sie als
  "compressed_*" Knoten im ObjectGraph (object_nodes) + minimale Relationen (object_relations),
  sodass die Learning-UI `compressed_share` wieder ein realer, messbarer Wert ist.

WICHTIG / SICHERHEIT
--------------------
- DBWriter-first (OROMA_DBW_ENABLE=1 erforderlich); keine direkten Writes ohne DBWriter.
- Keine destruktiven DB-Operationen.
- Sehr konservativ: Top-K (Default 10) nur für origins, die explizit erlaubt sind.
- Ziel ist NICHT "aggressiv komprimieren", sondern eine kontrollierte Materialisierung, damit
  ORÓMA wieder Depth/Reuse aufbauen kann (und nicht nur sammelt).

ARCHITEKTUR-IDEE
----------------
Historisch kamen `compressed_*` Nodes aus der Vision/SceneGraph AutoMeta-Pipeline.
Aktuell sind neue `compressed_*` Nodes seit einiger Zeit ausgeblieben, obwohl Redundanz existiert.
Dieses Tool erzeugt deshalb einen minimalen "Compression Marker" direkt aus SnapChain-Repeats.

KONZEPT
-------
1) Read-only Scan (sqlite RO) der SnapChains innerhalb eines Fensters:
   - Filter auf origins (Default: audio/token), optional weitere.
   - Fingerprint auf Vektor v (dims/decimals) → zählt Wiederholungen.
2) Stage-B Materialisierung (DBWriter):
   - Anchor Node pro Origin (kind='origin', label='origin:<origin_sanitized>').
   - Für jedes Top-Fingerprint (count>=min_repeat):
       object_nodes(kind='object', label='compressed_<ts><i>')
       object_relations(anchor -> compressed, relation='compressed_of', notes enthält count

ENV / CLI
---------
Pflicht:
- OROMA_DBW_ENABLE=1 (DBWriter aktiv)
Optional (ENV):
- OROMA_COMP_MAT_ORIGINS="audio/token,vision/token"
- OROMA_COMP_MAT_WINDOW_SEC=86400
- OROMA_COMP_MAT_LIMIT_CHAINS=5000
- OROMA_COMP_MAT_TOPK=10
- OROMA_COMP_MAT_MIN_REPEAT=5
- OROMA_COMP_FP_DIMS=32
- OROMA_COMP_FP_DECIMALS=2

Ausführung:
  cd /opt/ai/oroma
  PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 \
    python3 tools/compression_materializer.py --once

Exit Codes:
- 0: OK
- 2: DBWriter nicht verfügbar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Tuple

# Robust import when invoked as `python3 tools/...`
if __package__ is None and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "core")):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import db_writer_client  # type: ignore
from core.log_guard import log_suppressed  # type: ignore


def _lastrowid(res: Any) -> int:
    """Normalize DBWriter client exec() return value.

    Depending on ORÓMA line, db_writer_client.exec(...) may return:
      - dict-like: {"lastrowid": ...}
      - int: lastrowid directly
      - None / other: treated as 0
    """
    try:
        if isinstance(res, int):
            return int(res)
        if isinstance(res, dict):
            return _lastrowid(res)
        # Some implementations return {"result": {"lastrowid": ...}}
        if hasattr(res, "get"):
            return _lastrowid(res)
    except Exception:
        pass
    return 0


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return int(default)
    try:
        return int(v)
    except Exception:
        return int(default)


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None else str(v)


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


def _fingerprint(v: List[float], dims: int, decimals: int) -> str:
    if not v:
        return ""
    vv = v[:dims]
    q = [round(float(x), decimals) for x in vv]
    fmt = "{:." + str(decimals) + "f}"
    return ",".join(fmt.format(x) for x in q)


def _sanitize_origin(origin: str) -> str:
    s = origin.strip().lower()
    s = s.replace("/", "_").replace(":", "_")
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _scan_repeats(window_sec: int, limit_chains: int, origins: List[str], dims: int, decimals: int) -> Dict[str, int]:
    now = _now_ts()
    since = now - int(window_sec)
    db = _db_path_oroma()

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        placeholders = ",".join(["?"] * len(origins))
        sql = f"""
            SELECT id, ts, origin, blob
            FROM snapchains
            WHERE ts >= ?
              AND origin IN ({placeholders})
            ORDER BY id DESC
            LIMIT ?
        """
        rows = con.execute(sql, [since, *origins, int(limit_chains)]).fetchall()

        counts: Dict[str, int] = {}
        for r in rows:
            try:
                blob = r["blob"]
                if blob is None:
                    continue
                if isinstance(blob, (bytes, bytearray)):
                    obj = json.loads(blob.decode("utf-8", "replace"))
                else:
                    obj = json.loads(str(blob))
                v = obj.get("v") or []
                if not isinstance(v, list) or not v:
                    continue
                fp = _fingerprint(v, dims=dims, decimals=decimals)
                if not fp:
                    continue
                counts[fp] = counts.get(fp, 0) + 1
            except Exception:
                continue
        return counts
    finally:
        try:
            con.close()
        except Exception:
            pass


def _ensure_anchor(origin: str, ts: int) -> int:
    label = f"origin:{_sanitize_origin(origin)}"

    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
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

    meta = {
        "source": "compression_materializer",
        "origin": origin,
    }
    res = db_writer_client.exec(
        "INSERT INTO object_nodes(kind,label,meta_json,created_ts) VALUES (?,?,?,?)",
        ["origin", label, json.dumps(meta, ensure_ascii=False), int(ts)],
        tag="compression.b.anchor.insert",
        priority="normal",
        timeout_ms=4000,
        expect="lastrowid",
        db="oroma",
    )
    return _lastrowid(res)


def _insert_compressed(anchor_id: int, fp: str, count: int, ts: int, seq: int) -> Tuple[int, int]:
    label = f"compressed_{ts}{seq:02d}"
    meta = {
        "source": "compression_materializer",
        "fp": fp[:256],
        "repeat": int(count),
        "anchor_id": int(anchor_id),
    }
    r1 = db_writer_client.exec(
        "INSERT INTO object_nodes(kind,label,meta_json,created_ts) VALUES (?,?,?,?)",
        ["object", label, json.dumps(meta, ensure_ascii=False), int(ts)],
        tag="compression.b.node.insert",
        priority="normal",
        timeout_ms=4000,
        expect="lastrowid",
        db="oroma",
    )
    node_id = _lastrowid(r1)

    notes = f"repeat={count}"
    r2 = db_writer_client.exec(
        "INSERT INTO object_relations(a_id,relation,b_id,confidence,source_scene_id,ts,notes) VALUES (?,?,?,?,?,?,?)",
        [int(anchor_id), "compressed_of", int(node_id), 1.0, None, int(ts), notes],
        tag="compression.b.rel.insert",
        priority="normal",
        timeout_ms=4000,
        expect="lastrowid",
        db="oroma",
    )
    rel_id = _lastrowid(r2)
    return node_id, rel_id


def run_once(window_sec: int, limit_chains: int, origins: List[str], topk: int, min_repeat: int, dims: int, decimals: int) -> Dict[str, Any]:
    _dbw_required()

    t0 = time.time()
    ts = _now_ts()

    counts = _scan_repeats(window_sec, limit_chains, origins, dims=dims, decimals=decimals)

    cand = [(fp, c) for fp, c in counts.items() if int(c) >= int(min_repeat)]
    cand.sort(key=lambda x: (-x[1], x[0]))
    cand = cand[: int(topk)]

    created = 0
    rel_created = 0
    anchors: Dict[str, int] = {}

    for i, (fp, c) in enumerate(cand, start=1):
        origin = origins[0] if len(origins) == 1 else "mixed"
        if origin not in anchors:
            anchors[origin] = _ensure_anchor(origin, ts)
        node_id, rel_id = _insert_compressed(anchors[origin], fp, int(c), ts, i)
        if node_id:
            created += 1
        if rel_id:
            rel_created += 1

    dt = time.time() - t0
    return {
        "compression_stage": "B",
        "ok": True,
        "window_sec": int(window_sec),
        "limit_chains": int(limit_chains),
        "origins": origins,
        "topk": int(topk),
        "min_repeat": int(min_repeat),
        "candidates": len(cand),
        "created_nodes": int(created),
        "created_relations": int(rel_created),
        "fp_dims": int(dims),
        "fp_decimals": int(decimals),
        "dt_sec": float(round(dt, 3)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Compression Materializer (Stage B, minimal-invasive)")
    ap.add_argument("--window-sec", type=int, default=_env_int("OROMA_COMP_MAT_WINDOW_SEC", 86400))
    ap.add_argument("--limit-chains", type=int, default=_env_int("OROMA_COMP_MAT_LIMIT_CHAINS", 5000))
    ap.add_argument("--topk", type=int, default=_env_int("OROMA_COMP_MAT_TOPK", 10))
    ap.add_argument("--min-repeat", type=int, default=_env_int("OROMA_COMP_MAT_MIN_REPEAT", 5))
    ap.add_argument("--fp-dims", type=int, default=_env_int("OROMA_COMP_FP_DIMS", 32))
    ap.add_argument("--fp-decimals", type=int, default=_env_int("OROMA_COMP_FP_DECIMALS", 2))
    ap.add_argument("--once", action="store_true", default=True)
    args = ap.parse_args()

    origins_raw = _env_str("OROMA_COMP_MAT_ORIGINS", "audio/token")
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()] or ["audio/token"]

    try:
        res = run_once(
            window_sec=int(args.window_sec),
            limit_chains=int(args.limit_chains),
            origins=origins,
            topk=int(args.topk),
            min_repeat=int(args.min_repeat),
            dims=int(args.fp_dims),
            decimals=int(args.fp_decimals),
        )
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except Exception as e:
        log_suppressed("compression_materializer.error", key="compression_materializer failed", msg="compression materializer failed", exc=e)
        print(json.dumps({"compression_stage": "B", "ok": False, "error": str(e)}, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
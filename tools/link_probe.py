#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/link_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Binding Stage A – Link Probe (Measure-only, DBWriter-only)
# Version:   v3.7.3
# Stand:     2026-04-27
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Binding Stage A (Measure-only):
# - liest SnapChains mit `origin LIKE 'link/%'` in einem Zeitfenster (default 24h)
# - extrahiert Kanten-Kandidaten und schreibt ausschließlich Metriken (stats.db)
#
# WICHTIG: Keine Writes an object_nodes/object_relations in Stage A.
# DBWriter-only (Single-Writer), lokale DBs nur read-only für SELECTs.
#
# AUTOMATISCHE 48h-ZUSAMMENFASSUNG
# ───────────────────────────────
# alle 48h (seit letzter Summary) wird eine kompakte JSON Summary in STDOUT geloggt
# und `binding.a.summary_last_ts` in stats_points / stats_meta gesetzt.
#
# =============================================================================

from __future__ import annotations

# --- sys.path bootstrap (tool may be executed from tools/ directory) ---
import os as _os
import sys as _sys
_BASE = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _BASE not in _sys.path:
    _sys.path.insert(0, _BASE)

import argparse
import hashlib
import logging
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from core.log_guard import log_suppressed

try:
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore

LOG = logging.getLogger("oroma.link_probe")

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None else str(v)


def _dbw_required() -> None:
    """Require DBWriter availability.

    ORÓMA Policy:
    - Writes must go through DBWriter (Single-Writer).

    Practical note:
    - In interactive shells `OROMA_DBW_ENABLE` is sometimes not exported.
      If the socket exists and a ping succeeds, we treat DBWriter as available.
    """
    if not dbw:
        raise RuntimeError("DBWriter client module unavailable (core/db_writer_client.py import failed)")

    # Fast-path: enabled() + ping()
    try:
        if getattr(dbw, "enabled", lambda: False)() and getattr(dbw, "ping", lambda timeout_ms=500: False)(timeout_ms=800):
            return
    except Exception:
        pass

    # Fallback: socket exists + direct ping via internal client (env may be missing)
    sock = os.getenv("OROMA_DBW_SOCKET", "/opt/ai/oroma/data/state/db_writer.sock")
    if os.path.exists(sock):
        try:
            _client = getattr(dbw, "_client", None)
            if _client is not None:
                resp = _client().request(op="ping", timeout_ms=800, expect="none", tag="link_probe.ping_fallback")
                if bool(resp.get("ok")):
                    return
        except Exception:
            pass

    raise RuntimeError("DBWriter required but not available/enabled (set OROMA_DBW_ENABLE=1 and ensure db_writer.sock is alive)")

def _dbw_timeout_ms() -> int:
    return _env_int("OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM", 60000)


def _dbw_exec(sql: str, params: Tuple[Any, ...], tag: str) -> int:
    _dbw_required()
    return int(
        dbw.exec_write(
            sql,
            params=params,
            tag=tag,
            priority="normal",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
    )


def _oroma_ro_select(sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    """Read-only select from oroma.db (no DBWriter needed for reads)."""
    oroma_db = _env_str("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
    uri = f"file:{oroma_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        return [dict(r) for r in rows]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _stats_ro_select(sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    """Read-only select from stats.db."""
    stats_db = _env_str("OROMA_STATS_DB_PATH", "/opt/ai/oroma/data/stats.db")
    uri = f"file:{stats_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        return [dict(r) for r in rows]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _sha1_hex(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def _canon_json(obj: Any) -> str:
    """Stable canonical JSON for mixed payloads (best-effort)."""
    volatile = {"ts", "timestamp", "time", "now", "rand", "nonce", "uuid", "id", "snap_id"}

    def scrub(x: Any) -> Any:
        if isinstance(x, dict):
            out: Dict[str, Any] = {}
            for k in sorted(x.keys(), key=lambda v: str(v)):
                ks = str(k)
                if ks in volatile:
                    continue
                out[ks] = scrub(x[k])
            return out
        if isinstance(x, list):
            return [scrub(v) for v in x]
        if isinstance(x, float):
            return round(x, 6)
        if isinstance(x, (int, str, bool)) or x is None:
            return x
        return repr(x)

    try:
        return json.dumps(scrub(obj), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except Exception:
        return json.dumps({"repr": repr(obj)}, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _norm_label(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _node_key_from_any(payload: Any, ns: str) -> Tuple[str, str]:
    """Return (kind, node_key) where kind is one of obj|label|raw."""
    if isinstance(payload, dict):
        for k in ("object_id", "obj_id", "node_id", "id"):
            v = payload.get(k)
            if isinstance(v, int) and v > 0:
                return ("obj", f"node:obj:{v}")
            if isinstance(v, str) and v.isdigit():
                return ("obj", f"node:obj:{v}")
        for k in ("label", "name", "token", "text"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return ("label", f"node:label:{ns}:{_norm_label(v)}")
    cj = _canon_json(payload)
    return ("raw", f"node:raw:{_sha1_hex(cj.encode('utf-8'))}")


def _edge_fp(origin: str, rel_type: str, src_key: str, dst_key: str) -> str:
    return _sha1_hex(f"{origin}|{rel_type}|{src_key}|{dst_key}".encode("utf-8"))


def _extract_edges_from_chain_blob(blob: str) -> List[Dict[str, Any]]:
    try:
        d = json.loads(blob)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []

    def push(src: Any, dst: Any, typ: str, score: Optional[float]) -> None:
        out.append({"src": src, "dst": dst, "type": typ or "link", "score": score})

    links = None
    if isinstance(d, dict):
        links = d.get("links") or d.get("edges") or d.get("bindings")
    if isinstance(links, list):
        for it in links:
            if isinstance(it, dict):
                sc = it.get("score")
                push(it.get("src") or it.get("source"), it.get("dst") or it.get("target"), str(it.get("type") or it.get("rel") or "link"), float(sc) if isinstance(sc, (int, float)) else None)
        return out

    def scan(x: Any) -> None:
        if isinstance(x, dict):
            if ("src" in x and "dst" in x) or ("source" in x and "target" in x):
                sc = x.get("score")
                push(x.get("src", x.get("source")), x.get("dst", x.get("target")), str(x.get("type") or x.get("rel") or x.get("relation") or "link"), float(sc) if isinstance(sc, (int, float)) else None)
            for v in x.values():
                scan(v)
        elif isinstance(x, list):
            for v in x:
                scan(v)

    scan(d)
    return out
def _metric_insert(series: str, value: float, ts: int, meta: Optional[Dict[str, Any]] = None) -> None:
    """Insert a Stage-A timeseries point into stats.db via DBWriter.

    stats.db in ORÓMA uses `stats_points(ts, series, value, src_table, src_id, meta, src_uid)`.
    We store Stage-A signals as series `binding.a.*`.
    """
    m = json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    _dbw_exec(
        "INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid) VALUES(?,?,?,?,?,?,?)",
        (int(ts), str(series), float(value), "binding", 0, m, "link_probe"),
        tag="binding.stageA.stats_points",
    )


def _meta_get_float(key: str) -> Optional[float]:
    rows = _stats_ro_select("SELECT v FROM stats_meta WHERE k=? LIMIT 1", (str(key),))
    if not rows:
        return None
    try:
        return float(rows[0]["v"])
    except Exception:
        return None


def _meta_set_float(key: str, value: float) -> None:
    _dbw_exec(
        "INSERT INTO stats_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (str(key), str(float(value))),
        tag="binding.stageA.stats_meta",
    )
    try:
        return float(rows[0]["value"])
    except Exception:
        return None


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    if q <= 0:
        return float(vs[0])
    if q >= 1:
        return float(vs[-1])
    idx = int(round((len(vs) - 1) * q))
    idx = max(0, min(len(vs) - 1, idx))
    return float(vs[idx])


def run_once(window_sec: int, limit_chains: int, max_runtime_s: int) -> Dict[str, Any]:
    _dbw_required()
    t0 = time.time()
    now = int(time.time())
    since = now - int(max(60, window_sec))

    rows = _oroma_ro_select(
        """
        SELECT id, ts, origin, blob
        FROM snapchains
        WHERE ts >= ?
          AND origin LIKE 'link/%'
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(since), int(limit_chains)),
    )

    link_sc = len(rows)
    edge_count = 0
    edge_fp_counts: Dict[str, int] = {}
    scores: List[float] = []
    by_origin: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    nodekind = {"obj": 0, "label": 0, "raw": 0}
    missing_src = 0
    missing_dst = 0

    for r in rows:
        if time.time() - t0 > float(max_runtime_s):
            break
        origin = str(r.get("origin") or "")
        by_origin[origin] = by_origin.get(origin, 0) + 1
        blob = r.get("blob")
        if not isinstance(blob, str) or not blob:
            continue
        edges = _extract_edges_from_chain_blob(blob)
        for e in edges:
            if time.time() - t0 > float(max_runtime_s):
                break
            src = e.get("src")
            dst = e.get("dst")
            typ = str(e.get("type") or "link")
            by_type[typ] = by_type.get(typ, 0) + 1

            if src is None:
                missing_src += 1
                continue
            if dst is None:
                missing_dst += 1
                continue

            sk_kind, sk = _node_key_from_any(src, ns=origin)
            dk_kind, dk = _node_key_from_any(dst, ns=origin)
            nodekind[sk_kind] = nodekind.get(sk_kind, 0) + 1
            nodekind[dk_kind] = nodekind.get(dk_kind, 0) + 1

            fp = _edge_fp(origin=origin, rel_type=typ, src_key=sk, dst_key=dk)
            edge_fp_counts[fp] = edge_fp_counts.get(fp, 0) + 1
            edge_count += 1

            sc = e.get("score")
            if isinstance(sc, (int, float)):
                scores.append(float(sc))

    reps = list(edge_fp_counts.values())
    rep_ge_2 = sum(1 for n in reps if n >= 2)
    rep_ge_3 = sum(1 for n in reps if n >= 3)
    rep_ge_5 = sum(1 for n in reps if n >= 5)

    ts = now
    # core metrics
    _metric_insert("binding.a.link_sc.count_24h", float(link_sc), ts)
    _metric_insert("binding.a.edge_candidates.count_24h", float(edge_count), ts)
    _metric_insert("binding.a.edge_fp.unique_24h", float(len(edge_fp_counts)), ts)
    _metric_insert("binding.a.edge_fp.repeat_ge_2_24h", float(rep_ge_2), ts)
    _metric_insert("binding.a.edge_fp.repeat_ge_3_24h", float(rep_ge_3), ts)
    _metric_insert("binding.a.edge_fp.repeat_ge_5_24h", float(rep_ge_5), ts)

    _metric_insert("binding.a.edge_score.p50_24h", float(_quantile(scores, 0.50)), ts)
    _metric_insert("binding.a.edge_score.p90_24h", float(_quantile(scores, 0.90)), ts)
    _metric_insert("binding.a.edge_score.p99_24h", float(_quantile(scores, 0.99)), ts)

    _metric_insert("binding.a.nodekey.kind.obj.count_24h", float(nodekind.get("obj", 0)), ts)
    _metric_insert("binding.a.nodekey.kind.label.count_24h", float(nodekind.get("label", 0)), ts)
    _metric_insert("binding.a.nodekey.kind.raw.count_24h", float(nodekind.get("raw", 0)), ts)
    _metric_insert("binding.a.mapping.missing_src_24h", float(missing_src), ts)
    _metric_insert("binding.a.mapping.missing_dst_24h", float(missing_dst), ts)

    # by origin/type (top 20)
    top_origin = sorted(by_origin.items(), key=lambda kv: kv[1], reverse=True)[:20]
    top_type = sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)[:20]
    for k, v in top_origin:
        safe = re.sub(r"[^a-zA-Z0-9_\-:/\.]+", "_", k)[:80]
        _metric_insert(f"binding.a.by_origin.{safe}.count_24h", float(v), ts)
    for k, v in top_type:
        safe = re.sub(r"[^a-zA-Z0-9_\-:/\.]+", "_", k)[:80]
        _metric_insert(f"binding.a.by_rel_type.{safe}.count_24h", float(v), ts)

    # 48h summary log
    last_summary = _meta_get_float("binding.a.summary_last_ts") or 0.0
    do_summary = (ts - int(last_summary)) >= 48 * 3600
    summary = None
    if do_summary:
        _meta_set_float("binding.a.summary_last_ts", float(ts))
        summary = {
            "top_origins": top_origin[:10],
            "top_types": top_type[:10],
            "repeat_ge_3": rep_ge_3,
            "unique_edges": len(edge_fp_counts),
            "nodekey_kind": nodekind,
            "missing_src": missing_src,
            "missing_dst": missing_dst,
            "score_p90": _quantile(scores, 0.90),
        }

    dur = time.time() - t0
    return {
        "ok": True,
        "window_sec": window_sec,
        "since_ts": since,
        "chains_scanned": link_sc,
        "edges": edge_count,
        "unique_edges": len(edge_fp_counts),
        "repeat_ge_3": rep_ge_3,
        "score_p90": _quantile(scores, 0.90),
        "duration_s": round(dur, 3),
        "summary": summary,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Binding Stage A: measure-only link probe (DBWriter-only, stats.db)")
    ap.add_argument("--window-sec", type=int, default=_env_int("OROMA_BINDING_PROBE_WINDOW_SEC", 86400))
    ap.add_argument("--limit-chains", type=int, default=_env_int("OROMA_BINDING_PROBE_LIMIT_CHAINS", 5000))
    ap.add_argument("--max-runtime-s", type=int, default=_env_int("OROMA_BINDING_PROBE_MAX_RUNTIME_S", 120))
    args = ap.parse_args()

    try:
        res = run_once(args.window_sec, args.limit_chains, args.max_runtime_s)
        print(json.dumps({"binding_stage": "A", **res}, ensure_ascii=False))
        return 0
    except Exception as e:
        log_suppressed(LOG, key="link_probe.error", msg=f"[link_probe] ERROR: {e}", exc=e)
        print(json.dumps({"binding_stage": "A", "ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
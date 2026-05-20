#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pfad:    tools/synapses_probe.py
Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
Version: v3.7.x / v3.8 line-compatible
Stand:   2026-05-04
Autor:   Jörg Werner (public), ORÓMA · KI-JWG-X1 (intern)

ZWECK
-----
Lightweight KPI-Probe für den Synapsen-Graph (Vernetzung), ohne UI-Render:

- liest aus `object_relations` alle Kanten mit relation='synaptic' in zwei Fenstern:
    * 24h: Frische / neue Synapsen-Aktivität
    * 7d: stabile Vernetzungsbasis für Bridge Stage A und Learning UI
- baut daraus je Fenster einen ungerichteten Graph-Snapshot
- berechnet robuste Kennzahlen:
    * nodes (Knoten)
    * edges (Kanten)
    * components (Anzahl verbundener Komponenten)
    * giant_share (Anteil der größten Komponente)
    * avg_deg (Ø-Grad)
- schreibt diese Kennzahlen als `stats_points` Serien `synapses.*` nach `stats.db`
  (DBWriter-only, keine direkten SQLite-Writes):
    * synapses.nodes_24h / edges_24h / components_24h / giant_share_24h / avg_deg_24h
    * synapses.nodes_7d  / edges_7d  / components_7d  / giant_share_7d  / avg_deg_7d

WARUM
-----
Die Synapsen-UI zeigt Vernetzung visuell, aber Learning ist KPI-getrieben.
Diese Probe erzeugt eine Brücke: Learning kann synapses(24h) anzeigen + Deep-Link /synapses.

SICHERHEIT / PERFORMANCE
------------------------
- DBWriter-first: OROMA_DBW_ENABLE=1 erforderlich.
- Read-only SQL auf oroma.db (mode=ro).
- limit_edges begrenzt Scan (Default 50k) → stabil auch bei großen DBs.
- keine destruktiven DB-Operationen.

ENV
---
- OROMA_SYNAPSES_WINDOW_SEC      (Default 86400; Primary/Legacy-Fenster)
- OROMA_SYNAPSES_WINDOW_SEC_7D   (Default 604800; Stable-Window für 7d KPIs)
- OROMA_SYNAPSES_LIMIT_EDGES     (Default 50000)

RUN
---
cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 python3 tools/synapses_probe.py --once
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Tuple

# Robust import when invoked as `python3 tools/...`
if __package__ is None and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "core")):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import db_writer_client  # type: ignore
from core.log_guard import log_suppressed  # type: ignore


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


def _write_stat(ts: int, series: str, value: float, src_uid: str) -> None:
    # stats_points requires src_table/src_id/src_uid (schema enforced)
    db_writer_client.exec(
        "INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [int(ts), str(series), float(value), "synapses_probe", 0, None, str(src_uid)],
        tag="synapses.probe",
        priority="normal",
        timeout_ms=2000,
        expect="rowcount",
        db="stats",
    )


def _fetch_edges(window_sec: int, limit_edges: int) -> List[Tuple[int, int]]:
    db = _db_path_oroma()
    since = _now_ts() - int(window_sec)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT a_id, b_id
            FROM object_relations
            WHERE relation='synaptic'
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (int(since), int(limit_edges)),
        ).fetchall()

        edges: List[Tuple[int, int]] = []
        for r in rows:
            try:
                a = int(r["a_id"])
                b = int(r["b_id"])
                if a <= 0 or b <= 0 or a == b:
                    continue
                edges.append((a, b))
            except Exception:
                continue
        return edges
    finally:
        try:
            con.close()
        except Exception:
            pass


def _graph_metrics(edges: List[Tuple[int, int]]) -> Dict[str, Any]:
    if not edges:
        return {"nodes": 0, "edges": 0, "components": 0, "giant_share": 0.0, "avg_deg": 0.0}

    adj: Dict[int, set] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)

    nodes = list(adj.keys())
    n = len(nodes)

    # undirected edge count
    e2 = sum(len(adj[k]) for k in adj)
    e = e2 // 2

    seen = set()
    comp_sizes: List[int] = []
    for u in nodes:
        if u in seen:
            continue
        q = deque([u])
        seen.add(u)
        sz = 0
        while q:
            x = q.popleft()
            sz += 1
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    q.append(y)
        comp_sizes.append(sz)

    comps = len(comp_sizes)
    giant = max(comp_sizes) if comp_sizes else 0
    giant_share = (float(giant) / float(n)) if n > 0 else 0.0
    avg_deg = (2.0 * float(e) / float(n)) if n > 0 else 0.0
    return {"nodes": n, "edges": e, "components": comps, "giant_share": giant_share, "avg_deg": avg_deg}


def _write_metric_set(ts: int, suffix: str, metrics: Dict[str, Any], uid_prefix: str) -> None:
    """Write one complete synapses KPI metric set for a named window suffix.

    Der Suffix ist bewusst explizit (z. B. ``24h`` oder ``7d``), damit die
    Learning-UI nicht mehr 24h-Frische und 7d-Bestandsvernetzung verwechselt.
    Jede Serie bekommt eine eigene ``src_uid``; dadurch bleiben Inserts mit dem
    produktiven ``UNIQUE(src_table, src_uid, series)``-Index idempotent genug,
    ohne ältere Werte zu überschreiben.
    """
    safe_suffix = str(suffix).strip().replace(".", "_") or "window"
    _write_stat(ts, f"synapses.nodes_{safe_suffix}", float(metrics["nodes"]), uid_prefix + f":{safe_suffix}:n")
    _write_stat(ts, f"synapses.edges_{safe_suffix}", float(metrics["edges"]), uid_prefix + f":{safe_suffix}:e")
    _write_stat(ts, f"synapses.components_{safe_suffix}", float(metrics["components"]), uid_prefix + f":{safe_suffix}:c")
    _write_stat(ts, f"synapses.giant_share_{safe_suffix}", float(metrics["giant_share"]), uid_prefix + f":{safe_suffix}:g")
    _write_stat(ts, f"synapses.avg_deg_{safe_suffix}", float(metrics["avg_deg"]), uid_prefix + f":{safe_suffix}:d")


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA synapses KPI probe (stats_points)")
    ap.add_argument("--once", action="store_true", default=True)
    ap.add_argument("--window-sec", type=int, default=int(os.environ.get("OROMA_SYNAPSES_WINDOW_SEC", "86400")))
    ap.add_argument("--window-sec-7d", type=int, default=int(os.environ.get("OROMA_SYNAPSES_WINDOW_SEC_7D", "604800")))
    ap.add_argument("--limit-edges", type=int, default=int(os.environ.get("OROMA_SYNAPSES_LIMIT_EDGES", "50000")))
    args = ap.parse_args()

    try:
        _dbw_required()
        ts = _now_ts()
        uid = str(ts)

        # 24h = Frische-Indikator. Wenn hier 0 steht, heißt das nur: keine neuen
        # Synapsen im letzten Tag – nicht, dass der gesamte Synapsen-Graph leer ist.
        edges_24h = _fetch_edges(86400, int(args.limit_edges))
        m24 = _graph_metrics(edges_24h)
        _write_metric_set(ts, "24h", m24, uid)

        # 7d = stabile Arbeitsbasis für Bridge Stage A und Learning UI. Auf dem
        # Live-System liegen bekannte Synapsen oft außerhalb des 24h-Fensters,
        # aber innerhalb von 7 Tagen; deshalb wird dieses Fenster immer zusätzlich
        # geschrieben. Das bleibt read-only + DBWriter-only.
        window_7d = max(86400, int(args.window_sec_7d))
        edges_7d = _fetch_edges(window_7d, int(args.limit_edges))
        m7 = _graph_metrics(edges_7d)
        _write_metric_set(ts, "7d", m7, uid)

        print(json.dumps({
            "ok": True,
            "window_sec": int(args.window_sec),
            "window_sec_24h": 86400,
            "window_sec_7d": int(window_7d),
            "limit_edges": int(args.limit_edges),
            "edges_scanned_24h": len(edges_24h),
            "edges_scanned_7d": len(edges_7d),
            "metrics_24h": m24,
            "metrics_7d": m7,
        }, ensure_ascii=False))
        return 0
    except Exception as e:
        log_suppressed("synapses_probe.error", key="synapses_probe failed", msg="synapses probe failed", exc=e)
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

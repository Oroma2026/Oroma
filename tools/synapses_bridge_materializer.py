#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pfad:    tools/synapses_bridge_materializer.py
Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
Version: v3.7.x / v3.8 line-compatible
Stand:   2026-05-06 (B3)
Autor:   Jörg Werner (public), ORÓMA · KI-JWG-X1 (intern)

ZWECK
-----
Synapses Bridge Materializer – Stage B Mini / kontrollierte Einzel-Brücke mit Tageslimit-Guard.

Dieses Tool ist der bewusst getrennte Schreibpfad zur zuvor eingeführten
`tools/synapses_bridge_probe.py` Stage A-4. Die Probe bleibt measure-only. Dieses
Tool materialisiert nur dann eine Brücke, wenn explizit ein Lauf gestartet wird
und ein hinreichend starker, mittelstarker Kontext-Kandidat vorhanden ist.

Die Materialisierung ist absichtlich sehr konservativ:

- schreibt NICHT relation='synaptic'
- schreibt ausschließlich relation='synaptic_bridge'
- nutzt ausschließlich Medium-Anker (`context_episode`, `context_time_bucket`,
  `context_neighbor_time_bucket`, `context_episode_sequence_bucket`,
  `context_snapchain_nearby_bucket`, `context_origin_time_bucket`)
- ignoriert generische Anker (`scope:*`, `event_type:*`, `scope_event_type:*`)
- ignoriert singleton-ref-Anker (`ref:snapchains:*`) als alleinige Brückenquelle
- default maximal 1 Bridge pro Lauf
- B2-Schutz: bereits materialisierte Komponentenpaare werden erkannt und nicht erneut vorgeschlagen
- B2-Metriken: available_new_medium_candidates / existing_component_pair_candidates
- B3-Tageslimit: rollierendes 24h-Limit verhindert zu aggressive Bridge-Materialisierung
- DBWriter-kompatibler Schreibpfad über core.sql_manager.insert_object_relation
- sichtbare State- und Stats-Ausgabe

WARUM
-----
Die Live-Diagnostik zeigte:

- `relation='synaptic'` bildet dichte, aber fragmentierte Event↔Event-Inseln.
- `relation='synaptic_context'` ergänzt Kontextanker.
- Generische Anker erzeugen viele Kandidaten, sind aber zu grob.
- Medium-Anker erzeugten erstmals einen Kandidaten mit Score ~0.78.

Stage B Mini schreibt daher zunächst nur eine getrennte, auditierbare
`synaptic_bridge`-Kante. Danach können `synapses_probe.py`/Bridge-Probe erweitert
oder per SQL getrennt ausgewertet werden, ohne den ursprünglichen Synapsenbackbone
zu verfälschen.

DB-/LOCK-DISZIPLIN
------------------
- Reads: SQLite read-only URI auf `oroma.db`; alle Connections werden geschlossen.
- Writes: ausschließlich über `core.sql_manager.insert_object_relation`, das bei
  aktivem OROMA_DBW_ENABLE den DBWriter nutzt.
- Stats: ausschließlich via DBWriter nach `stats.db -> stats_points`.
- Kein lokaler Write-Fallback, wenn DBWriter nicht verfügbar ist.

ENV
---
- OROMA_SYNAPSES_BRIDGE_WINDOW_SEC       Default: 604800
- OROMA_SYNAPSES_BRIDGE_LIMIT_EDGES      Default: 50000
- OROMA_SYNAPSES_BRIDGE_TOPK             Default: 25
- OROMA_SYNAPSES_BRIDGE_MAX_NODES        Default: 8000
- OROMA_SYNAPSES_BRIDGE_MAT_MAX_EDGES    Default: 1
- OROMA_SYNAPSES_BRIDGE_MAT_MIN_SCORE    Default: 0.70
- OROMA_SYNAPSES_BRIDGE_MAT_CONFIDENCE   Default: 0.14
- OROMA_SYNAPSES_BRIDGE_MAT_MAX_PER_DAY  Default: 3
- OROMA_SYNAPSES_BRIDGE_MAT_DAY_WINDOW   Default: 86400
- OROMA_DATA_DIR                         Default: /opt/ai/oroma/data

RUN
---
cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 \
  python3 tools/synapses_bridge_materializer.py --once --window-sec 604800 --max-bridges 1 --verbose

SICHERHEIT
----------
Ohne `--materialize` läuft das Tool standardmäßig als Dry-Run. Für produktives
Schreiben muss `--materialize` explizit gesetzt werden.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

if __package__ is None and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "core")):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import db_writer_client  # type: ignore
from core.sql_manager import insert_object_relation  # type: ignore
from tools import synapses_bridge_probe as bridge_probe  # type: ignore

STATE_PATH = os.path.join(
    os.environ.get("OROMA_DATA_DIR", "/opt/ai/oroma/data"),
    "state",
    "synapses_bridge_materializer_state.json",
)


def _now_ts() -> int:
    return int(time.time())


def _db_path_oroma() -> str:
    return os.path.join(os.environ.get("OROMA_DATA_DIR", "/opt/ai/oroma/data"), "oroma.db")


def _dbw_required() -> None:
    if os.environ.get("OROMA_DBW_ENABLE", "") not in ("1", "true", "True", "YES", "yes"):
        raise RuntimeError("DBWriter required (set OROMA_DBW_ENABLE=1)")
    if not db_writer_client.ping(timeout_ms=500):
        raise RuntimeError("DBWriter required but not available (db_writer_client.ping failed)")


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _write_stat(ts: int, series: str, value: float, src_uid: str, meta: Optional[Dict[str, Any]] = None) -> None:
    db_writer_client.exec(
        "INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [int(ts), str(series), float(value), "synapses_bridge_materializer", 0, json.dumps(meta, ensure_ascii=False) if meta else None, str(src_uid)],
        tag="synapses.bridge.materializer",
        priority="normal",
        timeout_ms=2500,
        expect="rowcount",
        db="stats",
    )


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isfinite(f):
            return f
    except Exception:
        pass
    return float(default)


def _read_existing_bridge_count() -> int:
    con = sqlite3.connect(f"file:{_db_path_oroma()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT COUNT(*) FROM object_relations WHERE relation='synaptic_bridge'").fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        con.close()


def _read_existing_bridge_count_since(ts_min: int) -> int:
    """Count already materialized bridge edges in a rolling guard window.

    The guard intentionally uses a simple rolling timestamp window instead of
    local calendar-day boundaries. This makes behavior deterministic for
    orchestrator runs, manual SSH runs, and devices with changed timezone
    settings. The function is read-only and always closes the SQLite
    connection to avoid long-lived locks on the large ORÓMA database.
    """
    con = sqlite3.connect(f"file:{_db_path_oroma()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM object_relations WHERE relation='synaptic_bridge' AND ts >= ?",
            (int(ts_min),),
        ).fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        con.close()


def _remaining_daily_bridge_budget(now_ts: int, max_per_day: int, day_window_sec: int) -> Tuple[int, int, bool]:
    """Return (existing_in_window, remaining_budget, blocked).

    max_per_day < 0 disables the day guard explicitly. Normal production use
    should keep the default of 3. Returning the explicit blocked flag keeps the
    state JSON and stats unambiguous for the UI/log analysis.
    """
    if int(max_per_day) < 0:
        return 0, max(0, 10**9), False
    window = max(1, int(day_window_sec))
    existing = _read_existing_bridge_count_since(int(now_ts) - window)
    remaining = max(0, int(max_per_day) - int(existing))
    return int(existing), int(remaining), bool(remaining <= 0)


def _fetch_existing_relation(a_id: int, b_id: int, relation: str = "synaptic_bridge") -> Optional[int]:
    lo, hi = sorted((int(a_id), int(b_id)))
    con = sqlite3.connect(f"file:{_db_path_oroma()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT id FROM object_relations WHERE a_id=? AND relation=? AND b_id=? LIMIT 1",
            (int(lo), str(relation), int(hi)),
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        con.close()


def _fetch_existing_bridge_between_components(
    node_to_comp: Dict[int, int],
    comp_a: int,
    comp_b: int,
) -> Optional[Dict[str, Any]]:
    """Return an existing bridge between two base-graph components, if any.

    Stage B2 must not repeatedly materialize the same component-to-component
    bridge with slightly different representative event nodes. The exact
    a_id/b_id uniqueness check is still kept, but this component-level guard is
    the stronger protection for iterative runs.
    """
    ca, cb = sorted((int(comp_a), int(comp_b)))
    con = sqlite3.connect(f"file:{_db_path_oroma()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, a_id, b_id, confidence, ts, notes
              FROM object_relations
             WHERE relation='synaptic_bridge'
             ORDER BY ts DESC, id DESC
            """
        ).fetchall()
        for r in rows:
            a_id = int(r["a_id"] or 0)
            b_id = int(r["b_id"] or 0)
            ra = node_to_comp.get(a_id)
            rb = node_to_comp.get(b_id)
            if ra is None or rb is None:
                continue
            x, y = sorted((int(ra), int(rb)))
            if x == ca and y == cb:
                return {
                    "relation_id": int(r["id"]),
                    "a_id": a_id,
                    "b_id": b_id,
                    "confidence": float(r["confidence"] or 0.0),
                    "ts": int(r["ts"] or 0),
                    "notes": str(r["notes"] or "")[:240],
                }
        return None
    finally:
        con.close()


def _fetch_nodes_for_anchor(anchor_label: str, node_to_comp: Dict[int, int], comp_a: int, comp_b: int) -> Tuple[Optional[int], Optional[int]]:
    """Find representative event nodes in both candidate components that share the same context anchor."""
    con = sqlite3.connect(f"file:{_db_path_oroma()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT r.a_id, r.b_id, a.kind AS a_kind, a.label AS a_label, b.kind AS b_kind, b.label AS b_label
              FROM object_relations r
              JOIN object_nodes a ON a.id = r.a_id
              JOIN object_nodes b ON b.id = r.b_id
             WHERE r.relation='synaptic_context'
               AND ((b.kind='context' AND b.label=?) OR (a.kind='context' AND a.label=?))
             ORDER BY r.ts DESC, r.id DESC
            """,
            (str(anchor_label), str(anchor_label)),
        ).fetchall()
        found: Dict[int, int] = {}
        for r in rows:
            a_id = int(r["a_id"])
            b_id = int(r["b_id"])
            a_kind = str(r["a_kind"] or "")
            b_kind = str(r["b_kind"] or "")
            event_id = b_id if a_kind == "context" else a_id if b_kind == "context" else 0
            if event_id <= 0:
                continue
            comp = int(node_to_comp.get(event_id, -1))
            if comp in (int(comp_a), int(comp_b)) and comp not in found:
                found[comp] = int(event_id)
            if int(comp_a) in found and int(comp_b) in found:
                return int(found[int(comp_a)]), int(found[int(comp_b)])
        return found.get(int(comp_a)), found.get(int(comp_b))
    finally:
        con.close()


def _build_graph(window_sec: int, limit_edges: int, max_nodes: int) -> Tuple[Dict[int, List[int]], Dict[int, int], List[Dict[str, Any]]]:
    edge_rows = bridge_probe._fetch_edge_rows(window_sec=int(window_sec), limit_edges=int(limit_edges))  # type: ignore[attr-defined]
    edges = [(int(r["a_id"]), int(r["b_id"])) for r in edge_rows]
    node_ids = sorted({int(x) for e in edges for x in e})
    if len(node_ids) > int(max_nodes):
        allowed = set(node_ids[:int(max_nodes)])
        edge_rows = [r for r in edge_rows if int(r["a_id"]) in allowed and int(r["b_id"]) in allowed]
        edges = [(int(r["a_id"]), int(r["b_id"])) for r in edge_rows]
    nodes_by_comp, _adj = bridge_probe._components(edges)  # type: ignore[attr-defined]
    node_to_comp: Dict[int, int] = {}
    for cid, ids in nodes_by_comp.items():
        for nid in ids:
            node_to_comp[int(nid)] = int(cid)
    return nodes_by_comp, node_to_comp, edge_rows


_ALLOWED_MEDIUM_SOURCES = (
    "context_episode",
    "context_time_bucket",
    "context_neighbor_time_bucket",
    "context_episode_sequence_bucket",
    "context_snapchain_nearby_bucket",
    "context_origin_time_bucket",
)


def _candidate_source_allowed(cand: Dict[str, Any]) -> bool:
    sources = {str(x) for x in cand.get("sources", [])}
    if any(s in _ALLOWED_MEDIUM_SOURCES for s in sources):
        return True
    source = str(cand.get("source") or "")
    return source in _ALLOWED_MEDIUM_SOURCES


def _candidate_anchor(cand: Dict[str, Any]) -> Optional[str]:
    evidence = cand.get("evidence") or {}
    # Prefer more local medium anchors over broader time neighborhood anchors.
    for src in (
        "context_episode",
        "context_snapchain_nearby_bucket",
        "context_episode_sequence_bucket",
        "context_time_bucket",
        "context_origin_time_bucket",
        "context_neighbor_time_bucket",
    ):
        ev = evidence.get(src) if isinstance(evidence, dict) else None
        if isinstance(ev, dict):
            shared = ev.get("shared") or []
            if shared:
                return str(shared[0])
    shared = cand.get("shared") or []
    if shared:
        return str(shared[0])
    return None


def _compute_candidates(window_sec: int, limit_edges: int, topk: int, max_nodes: int) -> Tuple[List[Dict[str, Any]], Dict[int, List[int]], Dict[int, int]]:
    nodes_by_comp, node_to_comp, edge_rows = _build_graph(window_sec, limit_edges, max_nodes)
    node_ids = sorted(node_to_comp)
    node_meta = bridge_probe._fetch_nodes(node_ids)  # type: ignore[attr-defined]
    profiles = bridge_probe._component_profiles(nodes_by_comp, node_meta, edge_rows)  # type: ignore[attr-defined]
    context_info = bridge_probe._context_evidence(nodes_by_comp, window_sec=int(window_sec), limit_edges=int(limit_edges), topk=max(int(topk), 25))  # type: ignore[attr-defined]
    medium = list(context_info.get("context_medium_candidates") or [])
    # Keep only strong medium candidates, never generic candidates.
    candidates = [c for c in medium if _candidate_source_allowed(c)]
    candidates.sort(key=lambda d: (-_safe_float(d.get("score"), 0.0), int(d.get("component_a") or 0), int(d.get("component_b") or 0)))
    return candidates[:max(0, int(topk))], nodes_by_comp, node_to_comp


def run_once(
    *,
    window_sec: int,
    limit_edges: int,
    topk: int,
    max_nodes: int,
    max_bridges: int,
    min_score: float,
    confidence: float,
    materialize: bool,
    max_bridges_per_day: int,
    day_window_sec: int,
) -> Dict[str, Any]:
    _dbw_required()
    ts = _now_ts()
    existing_before = _read_existing_bridge_count()
    daily_existing, daily_remaining, daily_blocked = _remaining_daily_bridge_budget(
        int(ts),
        int(max_bridges_per_day),
        int(day_window_sec),
    )
    effective_max_bridges = min(max(0, int(max_bridges)), max(0, int(daily_remaining)))
    candidates, nodes_by_comp, node_to_comp = _compute_candidates(window_sec, limit_edges, topk, max_nodes)

    selected: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    written: List[Dict[str, Any]] = []
    eligible_count = 0
    available_new_count = 0
    existing_exact_count = 0
    existing_component_pair_count = 0
    below_min_score_count = 0
    daily_limit_skipped_count = 0

    for cand in candidates:
        score = _safe_float(cand.get("score"), 0.0)
        if score < float(min_score):
            below_min_score_count += 1
            skipped.append({"reason": "below_min_score", "score": score, "candidate": cand})
            continue
        a_comp = int(cand.get("component_a") or -1)
        b_comp = int(cand.get("component_b") or -1)
        if a_comp < 0 or b_comp < 0 or a_comp == b_comp:
            skipped.append({"reason": "invalid_components", "candidate": cand})
            continue
        anchor = _candidate_anchor(cand)
        if not anchor:
            skipped.append({"reason": "missing_medium_anchor", "candidate": cand})
            continue
        eligible_count += 1

        existing_component_bridge = _fetch_existing_bridge_between_components(node_to_comp, a_comp, b_comp)
        if existing_component_bridge is not None:
            existing_component_pair_count += 1
            skipped.append({
                "reason": "bridge_between_components_exists",
                "component_a": a_comp,
                "component_b": b_comp,
                "anchor": str(anchor),
                "score": round(float(score), 6),
                "existing_bridge": existing_component_bridge,
            })
            continue

        node_a, node_b = _fetch_nodes_for_anchor(anchor, node_to_comp, a_comp, b_comp)
        if not node_a or not node_b:
            skipped.append({"reason": "no_representative_nodes_for_anchor", "anchor": anchor, "candidate": cand})
            continue
        lo, hi = sorted((int(node_a), int(node_b)))
        existing_id = _fetch_existing_relation(lo, hi, relation="synaptic_bridge")
        item = {
            "component_a": a_comp,
            "component_b": b_comp,
            "node_a": int(lo),
            "node_b": int(hi),
            "anchor": str(anchor),
            "score": round(float(score), 6),
            "sources": cand.get("sources", [cand.get("source")]),
            "evidence": cand.get("evidence", {}),
            "existing_relation_id": existing_id,
        }
        if existing_id is not None:
            existing_exact_count += 1
            skipped.append({"reason": "bridge_relation_exists", **item})
            continue
        available_new_count += 1
        if daily_blocked or effective_max_bridges <= 0:
            daily_limit_skipped_count += 1
            skipped.append({
                "reason": "daily_bridge_limit_reached",
                "daily_existing": int(daily_existing),
                "daily_limit": int(max_bridges_per_day),
                "day_window_sec": int(day_window_sec),
                **item,
            })
            continue
        if len(selected) >= int(effective_max_bridges):
            skipped.append({"reason": "max_bridges_reached", "effective_max_bridges": int(effective_max_bridges), **item})
            continue
        selected.append(item)

    if materialize:
        for item in selected:
            notes = {
                "source": "synapses_bridge_materializer",
                "stage": "B_mini",
                "relation": "synaptic_bridge",
                "score": item["score"],
                "anchor": item["anchor"],
                "sources": item.get("sources", []),
                "component_a": item["component_a"],
                "component_b": item["component_b"],
                "node_a": item["node_a"],
                "node_b": item["node_b"],
                "window_sec": int(window_sec),
                "policy": {
                    "max_bridges": int(max_bridges),
                    "min_score": float(min_score),
                    "confidence": float(confidence),
                    "generic_anchors_excluded": True,
                    "ref_singleton_only_excluded": True,
                    "max_bridges_per_day": int(max_bridges_per_day),
                    "day_window_sec": int(day_window_sec),
                    "daily_existing_before": int(daily_existing),
                    "daily_remaining_before": int(daily_remaining),
                },
                "evidence": item.get("evidence", {}),
            }
            rid = insert_object_relation(
                int(item["node_a"]),
                int(item["node_b"]),
                relation="synaptic_bridge",
                confidence=float(confidence),
                source_scene_id=None,
                ts=int(ts),
                notes=notes,
            )
            written.append({**item, "relation_id": int(rid)})

    existing_after = _read_existing_bridge_count()
    meta = {
        "stage": "B_mini",
        "window_sec": int(window_sec),
        "limit_edges": int(limit_edges),
        "topk": int(topk),
        "max_bridges": int(max_bridges),
        "effective_max_bridges": int(effective_max_bridges),
        "min_score": float(min_score),
        "materialize": bool(materialize),
        "max_bridges_per_day": int(max_bridges_per_day),
        "day_window_sec": int(day_window_sec),
        "daily_existing": int(daily_existing),
        "daily_remaining": int(daily_remaining),
    }
    uid = str(ts)
    _write_stat(ts, "synapses.bridge.materializer.candidates", float(len(candidates)), uid + ":candidates", meta)
    _write_stat(ts, "synapses.bridge.materializer.eligible", float(eligible_count), uid + ":eligible", meta)
    _write_stat(ts, "synapses.bridge.materializer.available_new", float(available_new_count), uid + ":available_new", meta)
    _write_stat(ts, "synapses.bridge.materializer.existing_component_pair_candidates", float(existing_component_pair_count), uid + ":existing_component_pair_candidates", meta)
    _write_stat(ts, "synapses.bridge.materializer.existing_exact_candidates", float(existing_exact_count), uid + ":existing_exact_candidates", meta)
    _write_stat(ts, "synapses.bridge.materializer.below_min_score", float(below_min_score_count), uid + ":below_min_score", meta)
    _write_stat(ts, "synapses.bridge.materializer.daily_existing", float(daily_existing), uid + ":daily_existing", meta)
    _write_stat(ts, "synapses.bridge.materializer.daily_remaining", float(daily_remaining), uid + ":daily_remaining", meta)
    _write_stat(ts, "synapses.bridge.materializer.daily_limit_skipped", float(daily_limit_skipped_count), uid + ":daily_limit_skipped", meta)
    _write_stat(ts, "synapses.bridge.materializer.guard_blocked", 1.0 if daily_blocked else 0.0, uid + ":guard_blocked", meta)
    _write_stat(ts, "synapses.bridge.materializer.selected", float(len(selected)), uid + ":selected", meta)
    _write_stat(ts, "synapses.bridge.materializer.written", float(len(written)), uid + ":written", meta)
    _write_stat(ts, "synapses.bridge.materializer.existing_total", float(existing_after), uid + ":existing_total", meta)

    state = {
        "ok": True,
        "stage": "B_mini",
        "last_run_ts": int(ts),
        "materialize": bool(materialize),
        "window_sec": int(window_sec),
        "limit_edges": int(limit_edges),
        "topk": int(topk),
        "max_bridges": int(max_bridges),
        "effective_max_bridges": int(effective_max_bridges),
        "max_bridges_per_day": int(max_bridges_per_day),
        "day_window_sec": int(day_window_sec),
        "daily_existing_bridge_count": int(daily_existing),
        "daily_remaining_bridge_budget": int(daily_remaining),
        "daily_bridge_guard_blocked": bool(daily_blocked),
        "min_score": float(min_score),
        "confidence": float(confidence),
        "candidate_count": int(len(candidates)),
        "eligible_medium_candidate_count": int(eligible_count),
        "available_new_medium_candidate_count": int(available_new_count),
        "existing_component_pair_candidate_count": int(existing_component_pair_count),
        "existing_exact_candidate_count": int(existing_exact_count),
        "below_min_score_candidate_count": int(below_min_score_count),
        "daily_limit_skipped_candidate_count": int(daily_limit_skipped_count),
        "selected_count": int(len(selected)),
        "written_count": int(len(written)),
        "existing_bridge_count_before": int(existing_before),
        "existing_bridge_count_after": int(existing_after),
        "selected": selected,
        "written": written,
        "skipped": skipped[:20],
        "diagnosis": {
            "generic_anchors_excluded": True,
            "only_medium_context_candidates": True,
            "component_pair_dedup_enabled": True,
            "available_new_medium_candidates": int(available_new_count),
            "daily_guard_enabled": bool(int(max_bridges_per_day) >= 0),
            "daily_guard_blocked": bool(daily_blocked),
            "safe_to_measure_next": bool(not materialize or not daily_blocked),
        },
    }
    _atomic_write_json(STATE_PATH, state)
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA synapses bridge materializer Stage B Mini")
    ap.add_argument("--once", action="store_true", default=True)
    ap.add_argument("--materialize", action="store_true", help="actually write relation='synaptic_bridge'; without this flag it is dry-run only")
    ap.add_argument("--window-sec", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_WINDOW_SEC", "604800")))
    ap.add_argument("--limit-edges", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_LIMIT_EDGES", "50000")))
    ap.add_argument("--topk", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_TOPK", "25")))
    ap.add_argument("--max-nodes", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAX_NODES", "8000")))
    ap.add_argument("--max-bridges", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAT_MAX_EDGES", "1")))
    ap.add_argument("--min-score", type=float, default=float(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAT_MIN_SCORE", "0.70")))
    ap.add_argument("--confidence", type=float, default=float(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAT_CONFIDENCE", "0.14")))
    ap.add_argument("--max-bridges-per-day", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAT_MAX_PER_DAY", "3")))
    ap.add_argument("--day-window-sec", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAT_DAY_WINDOW", "86400")))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        result = run_once(
            window_sec=int(args.window_sec),
            limit_edges=int(args.limit_edges),
            topk=int(args.topk),
            max_nodes=int(args.max_nodes),
            max_bridges=int(args.max_bridges),
            min_score=float(args.min_score),
            confidence=float(args.confidence),
            materialize=bool(args.materialize),
            max_bridges_per_day=int(args.max_bridges_per_day),
            day_window_sec=int(args.day_window_sec),
        )
        if args.verbose:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            keys = ["ok", "stage", "materialize", "candidate_count", "available_new_medium_candidate_count", "daily_existing_bridge_count", "daily_remaining_bridge_budget", "daily_bridge_guard_blocked", "selected_count", "written_count", "existing_bridge_count_after"]
            print(json.dumps({k: result.get(k) for k in keys}, ensure_ascii=False))
        return 0
    except Exception as e:
        err_state = {"ok": False, "stage": "B_mini", "last_run_ts": _now_ts(), "error": str(e)}
        try:
            _atomic_write_json(STATE_PATH, err_state)
        except Exception:
            pass
        print(json.dumps(err_state, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

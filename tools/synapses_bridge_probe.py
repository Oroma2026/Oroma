#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pfad:    tools/synapses_bridge_probe.py
Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
Version: v3.7.x / v3.8 line-compatible + idempotent-stats-v1.1
Stand:   2026-06-16
Autor:   Jörg Werner (public), ORÓMA · KI-JWG-X1 (intern)

ZWECK
-----
Synapses Bridge Probe – Stage A-6 / Context-aware measure-only plus materialized-bridge effect measurement.

Dieses Tool analysiert den bestehenden Synapsen-Graphen aus
`object_relations(relation='synaptic')` und berechnet Diagnose- und
Kandidatenmetriken für spätere, kontrollierte Brücken zwischen fragmentierten
Komponenten. Es materialisiert bewusst NICHTS in `object_relations`, legt KEINE
neuen Tabellen an und verändert keine `object_nodes`. Die Ausgabe erfolgt
ausschließlich über:

1. `stats.db -> stats_points` Serien `synapses.bridge.*`
2. ein atomisch geschriebenes State-JSON:
   `/opt/ai/oroma/data/state/synapses_bridge_probe_state.json`

WARUM
-----
Die Live-Messung zeigte eine stabile 7d-Bestandsvernetzung, aber starke
Fragmentierung:

- viele Komponenten/Inseln
- kleine Giant-Component
- `candidate_pairs=0` in der ursprünglichen Stage-A-Heuristik

Stage A-5 bleibt deshalb weiterhin read-only/measure-only, erklärt aber
zusätzlich, WARUM keine Brücken gefunden werden bzw. welche alternative Evidenz
vorhanden ist:

- Komponenten-Größenverteilung
- Label-/Kind-/Meta-Token-Overlap zwischen Komponenten
- Source-Scene-Nähe über `source_scene_id`
- Notes-/Kontext-Token-Overlap über `object_relations.notes`
- separate, vorsichtige Auswertung von `relation='synaptic_context'` Ankern

DESIGN / HEURISTIK
------------------
Das Tool nutzt konservative, nachvollziehbare Signale aus vorhandenen Tabellen:

- Synapsen-Komponenten aus `object_relations(relation='synaptic')`
- Labels, Kinds und `meta_json` aus `object_nodes`
- Relation-Kontext aus `source_scene_id` und `notes`
- getrennte Kandidatenlisten für Label, Scene und Notes
- kombinierte Top-K Kandidaten nur im State-JSON, nicht als DB-Materialisierung

Die Heuristik ist absichtlich vorsichtig. Stage B kann später – nach manueller
Plausibilitätsprüfung – wenige Kandidaten mit zusätzlichem Gate schreiben. Dieses
Tool schreibt keine Bridge-Edges.

DB-/LOCK-DISZIPLIN
------------------
- Reads: SQLite read-only URI auf `oroma.db`, alle Connections werden in
  `finally` geschlossen.
- Writes: ausschließlich DBWriter nach `stats.db`.
- Stats-Writes sind idempotent via UPSERT, damit Wiederholungsläufe mit
  gleichem `src_uid` keine UNIQUE-Tracebacks erzeugen.
- `OROMA_DBW_ENABLE=1` ist Pflicht.
- Keine direkten SQLite-Writes, keine lokalen Fallbacks.

ENV
---
- OROMA_SYNAPSES_BRIDGE_WINDOW_SEC   Default: 604800 (7d; Bridge braucht Bestand, nicht nur 24h-Frische)
- OROMA_SYNAPSES_BRIDGE_LIMIT_EDGES  Default: 50000
- OROMA_SYNAPSES_BRIDGE_TOPK         Default: 25
- OROMA_SYNAPSES_BRIDGE_MAX_NODES    Default: 8000
- OROMA_DATA_DIR                     Default: /opt/ai/oroma/data

RUN
---
cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 \
  python3 tools/synapses_bridge_probe.py --once --window-sec 604800 --topk 25
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

if __package__ is None and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "core")):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import db_writer_client  # type: ignore
from core.log_guard import log_suppressed  # type: ignore

STATE_PATH = os.path.join(
    os.environ.get("OROMA_DATA_DIR", "/opt/ai/oroma/data"),
    "state",
    "synapses_bridge_probe_state.json",
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9äöüÄÖÜß]{3,}")
_STOP_TOKENS = {
    "object", "objects", "origin", "scene", "auto", "vision", "token", "tokens", "audio",
    "meta", "snap", "snaps", "chain", "chains", "compressed", "unknown", "none", "null",
    "true", "false", "debug", "source", "score", "value", "values", "label", "kind",
    "created", "system", "oroma", "synaptic", "relation", "node", "nodes",
}


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


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _write_stat(ts: int, series: str, value: float, src_uid: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """Write one synapses.bridge.* stat point idempotently via DBWriter.

    The stats schema enforces uniqueness on `(src_table, src_uid, series)`. The
    orchestrator can repeat a measure-only probe after timeouts/restarts, so a
    duplicate source UID should refresh the same logical point instead of
    poisoning DBWriter `last_error` with a UNIQUE failure.
    """
    db_writer_client.exec(
        """
        INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(src_table, src_uid, series) DO UPDATE SET
          ts=excluded.ts,
          value=excluded.value,
          src_id=excluded.src_id,
          meta=excluded.meta
        """,
        [int(ts), str(series), float(value), "synapses_bridge_probe", 0, json.dumps(meta, ensure_ascii=False) if meta else None, str(src_uid)],
        tag="synapses.bridge.probe.upsert",
        priority="normal",
        timeout_ms=2000,
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


def _median_int(values: Sequence[int]) -> int:
    arr = sorted(int(v) for v in values)
    if not arr:
        return 0
    n = len(arr)
    mid = n // 2
    if n % 2:
        return int(arr[mid])
    return int(round((arr[mid - 1] + arr[mid]) / 2.0))


def _tokens(text: str) -> Set[str]:
    raw = str(text or "").replace("_", " ").replace(":", " ").replace("/", " ").replace("-", " ").lower()
    toks: Set[str] = set()
    for m in _TOKEN_RE.finditer(raw):
        t = m.group(0).strip().lower()
        if not t or t in _STOP_TOKENS or t.isdigit():
            continue
        if len(t) > 36:
            continue
        toks.add(t)
    return toks


def _json_tokens(meta_json: str) -> Set[str]:
    if not meta_json:
        return set()
    toks: Set[str] = set()
    try:
        data = json.loads(meta_json)
    except Exception:
        return _tokens(meta_json[:500])

    def walk(x: Any, depth: int = 0) -> None:
        if depth > 2:
            return
        if isinstance(x, dict):
            for k, v in list(x.items())[:25]:
                toks.update(_tokens(str(k)))
                walk(v, depth + 1)
        elif isinstance(x, (list, tuple)):
            for v in list(x)[:25]:
                walk(v, depth + 1)
        elif isinstance(x, (str, int, float, bool)):
            toks.update(_tokens(str(x)))

    walk(data)
    return toks


def _fetch_edge_rows(window_sec: int, limit_edges: int) -> List[Dict[str, Any]]:
    db = _db_path_oroma()
    since = _now_ts() - int(window_sec)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT a_id, b_id, source_scene_id, notes, ts
            FROM object_relations
            WHERE relation='synaptic'
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (int(since), int(limit_edges)),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                a = int(r["a_id"])
                b = int(r["b_id"])
                if a <= 0 or b <= 0 or a == b:
                    continue
                scene_raw = r["source_scene_id"]
                scene_id = int(scene_raw) if scene_raw is not None else 0
                out.append({
                    "a_id": a,
                    "b_id": b,
                    "source_scene_id": scene_id,
                    "notes": str(r["notes"] or ""),
                    "ts": int(r["ts"] or 0),
                })
            except Exception:
                continue
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass


def _fetch_context_rows(window_sec: int, limit_edges: int) -> List[Dict[str, Any]]:
    """Read synaptic_context edges as context evidence for Stage A-3.

    The Bridge probe does not write or upgrade these edges.  Generic anchors
    (scope:* and event_type:*) are measured but deliberately excluded from strong
    bridge scoring.  Specific anchors (currently ref:*) may become measure-only
    bridge candidates when shared by multiple components.
    """
    db = _db_path_oroma()
    since = _now_ts() - int(window_sec)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT a_id, b_id, source_scene_id, notes, ts
            FROM object_relations
            WHERE relation='synaptic_context'
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (int(since), int(limit_edges)),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                a = int(r["a_id"])
                b = int(r["b_id"])
                if a <= 0 or b <= 0 or a == b:
                    continue
                scene_raw = r["source_scene_id"]
                out.append({
                    "a_id": a,
                    "b_id": b,
                    "source_scene_id": int(scene_raw) if scene_raw is not None else 0,
                    "notes": str(r["notes"] or ""),
                    "ts": int(r["ts"] or 0),
                })
            except Exception:
                continue
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass




def _fetch_bridge_rows(window_sec: int, limit_edges: int) -> List[Dict[str, Any]]:
    """Read already materialized synaptic_bridge edges for A-5 effect measurement.

    These rows are NEVER created or modified by this probe.  They are only
    included in a secondary graph projection so the UI/Stats can distinguish:

    - base graph: relation='synaptic'
    - context evidence: relation='synaptic_context'
    - measured materialized effect: relation='synaptic' + relation='synaptic_bridge'

    This keeps Stage A/A5 read-only while making the effect of Stage B Mini
    measurable without changing the original backbone metrics.
    """
    db = _db_path_oroma()
    since = _now_ts() - int(window_sec)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT a_id, b_id, confidence, notes, ts
            FROM object_relations
            WHERE relation='synaptic_bridge'
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (int(since), int(limit_edges)),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                a = int(r["a_id"])
                b = int(r["b_id"])
                if a <= 0 or b <= 0 or a == b:
                    continue
                out.append({
                    "a_id": a,
                    "b_id": b,
                    "confidence": float(r["confidence"] or 0.0),
                    "notes": str(r["notes"] or ""),
                    "ts": int(r["ts"] or 0),
                })
            except Exception:
                continue
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass


def _with_bridge_metrics(nodes_by_comp: Dict[int, List[int]], bridge_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute graph metrics when materialized synaptic_bridge edges are included.

    The base component map is derived only from `synaptic` edges.  Bridge rows
    are then applied as unions between existing components.  Bridge endpoints
    outside the current base window are counted as ignored so the metric stays
    conservative and explainable.
    """
    comp_sizes = {int(cid): len(nodes) for cid, nodes in nodes_by_comp.items()}
    if not comp_sizes:
        return {
            "with_bridge_components": 0,
            "with_bridge_giant_share": 0.0,
            "with_bridge_edges": 0,
            "with_bridge_bridge_edges": 0,
            "with_bridge_bridge_edges_used": 0,
            "with_bridge_bridge_edges_ignored": int(len(bridge_rows)),
            "with_bridge_bridgeable_components": 0,
            "with_bridge_delta_components": 0,
            "with_bridge_delta_giant_share": 0.0,
        }

    node_to_comp: Dict[int, int] = {}
    for cid, ids in nodes_by_comp.items():
        for nid in ids:
            node_to_comp[int(nid)] = int(cid)

    parent = {cid: cid for cid in comp_sizes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        parent[rb] = ra
        return True

    used = 0
    ignored = 0
    bridgeable: Set[int] = set()
    for row in bridge_rows:
        a_node = int(row.get("a_id") or 0)
        b_node = int(row.get("b_id") or 0)
        ca = node_to_comp.get(a_node)
        cb = node_to_comp.get(b_node)
        if ca is None or cb is None or ca == cb:
            ignored += 1
            continue
        bridgeable.add(int(ca))
        bridgeable.add(int(cb))
        if union(int(ca), int(cb)):
            used += 1
        else:
            ignored += 1

    projected: Dict[int, int] = defaultdict(int)
    for cid, sz in comp_sizes.items():
        projected[find(cid)] += int(sz)
    total_nodes = sum(comp_sizes.values())
    base_components = len(comp_sizes)
    base_giant = max(comp_sizes.values()) if comp_sizes else 0
    base_giant_share = float(base_giant) / float(total_nodes) if total_nodes > 0 else 0.0
    with_components = len(projected)
    with_giant = max(projected.values()) if projected else 0
    with_giant_share = float(with_giant) / float(total_nodes) if total_nodes > 0 else 0.0

    return {
        "with_bridge_components": int(with_components),
        "with_bridge_giant_share": float(with_giant_share),
        "with_bridge_edges": int(total_nodes),
        "with_bridge_bridge_edges": int(len(bridge_rows)),
        "with_bridge_bridge_edges_used": int(used),
        "with_bridge_bridge_edges_ignored": int(ignored),
        "with_bridge_bridgeable_components": int(len(bridgeable)),
        "with_bridge_delta_components": int(with_components - base_components),
        "with_bridge_delta_giant_share": float(with_giant_share - base_giant_share),
    }

def _fetch_nodes(node_ids: Sequence[int]) -> Dict[int, Dict[str, str]]:
    ids = sorted({int(x) for x in node_ids if int(x) > 0})
    if not ids:
        return {}
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        out: Dict[int, Dict[str, str]] = {}
        chunk = 750
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            q = ",".join("?" for _ in part)
            rows = con.execute(
                f"SELECT id, kind, label, meta_json, created_ts FROM object_nodes WHERE id IN ({q})",
                [int(x) for x in part],
            ).fetchall()
            for r in rows:
                out[int(r["id"])] = {
                    "kind": str(r["kind"] or ""),
                    "label": str(r["label"] or ""),
                    "meta_json": str(r["meta_json"] or ""),
                    "created_ts": str(r["created_ts"] or ""),
                }
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass


def _components(edges: Sequence[Tuple[int, int]]) -> Tuple[Dict[int, List[int]], Dict[int, Set[int]]]:
    adj: Dict[int, Set[int]] = defaultdict(set)
    for a, b in edges:
        adj[int(a)].add(int(b))
        adj[int(b)].add(int(a))

    seen: Set[int] = set()
    nodes_by_comp: Dict[int, List[int]] = {}
    comp_id = 0
    for start in list(adj.keys()):
        if start in seen:
            continue
        q = deque([start])
        seen.add(start)
        arr: List[int] = []
        while q:
            x = q.popleft()
            arr.append(x)
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    q.append(y)
        nodes_by_comp[comp_id] = sorted(arr)
        comp_id += 1
    return nodes_by_comp, adj


def _component_profiles(
    nodes_by_comp: Dict[int, List[int]],
    node_meta: Dict[int, Dict[str, str]],
    edge_rows: Sequence[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    node_to_comp: Dict[int, int] = {}
    for cid, ids in nodes_by_comp.items():
        for nid in ids:
            node_to_comp[int(nid)] = int(cid)

    scene_counts_by_comp: Dict[int, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    notes_counts_by_comp: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    edge_count_by_comp: Dict[int, int] = defaultdict(int)
    latest_ts_by_comp: Dict[int, int] = defaultdict(int)

    for row in edge_rows:
        a = int(row.get("a_id") or 0)
        b = int(row.get("b_id") or 0)
        cid = node_to_comp.get(a, node_to_comp.get(b, -1))
        if cid < 0:
            continue
        edge_count_by_comp[cid] += 1
        latest_ts_by_comp[cid] = max(int(latest_ts_by_comp.get(cid, 0)), int(row.get("ts") or 0))
        scene_id = int(row.get("source_scene_id") or 0)
        if scene_id > 0:
            scene_counts_by_comp[cid][scene_id] += 1
        for tok in _tokens(str(row.get("notes") or "")):
            notes_counts_by_comp[cid][tok] += 1

    profiles: Dict[int, Dict[str, Any]] = {}
    for cid, ids in nodes_by_comp.items():
        label_token_counts: Dict[str, int] = defaultdict(int)
        meta_token_counts: Dict[str, int] = defaultdict(int)
        kinds: Dict[str, int] = defaultdict(int)
        examples: List[Dict[str, Any]] = []
        for nid in ids:
            meta = node_meta.get(int(nid), {})
            kind = str(meta.get("kind") or "")
            label = str(meta.get("label") or "")
            meta_json = str(meta.get("meta_json") or "")
            if kind:
                kinds[kind] += 1
                for tok in _tokens(kind):
                    label_token_counts[tok] += 1
            for tok in _tokens(label):
                label_token_counts[tok] += 1
            for tok in _json_tokens(meta_json):
                meta_token_counts[tok] += 1
            if len(examples) < 5:
                examples.append({"id": int(nid), "kind": kind, "label": label[:160]})

        top_label_tokens = sorted(label_token_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
        top_meta_tokens = sorted(meta_token_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        top_note_tokens = sorted(notes_counts_by_comp.get(cid, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        top_scenes = sorted(scene_counts_by_comp.get(cid, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:20]

        profiles[int(cid)] = {
            "component_id": int(cid),
            "size": int(len(ids)),
            "edge_count": int(edge_count_by_comp.get(cid, 0)),
            "latest_ts": int(latest_ts_by_comp.get(cid, 0)),
            "label_tokens": {k for k, _ in top_label_tokens},
            "meta_tokens": {k for k, _ in top_meta_tokens},
            "notes_tokens": {k for k, _ in top_note_tokens},
            "scene_ids": {int(k) for k, _ in top_scenes if int(k) > 0},
            "top_label_tokens": top_label_tokens[:10],
            "top_meta_tokens": top_meta_tokens[:8],
            "top_notes_tokens": top_note_tokens[:8],
            "top_scene_ids": top_scenes[:8],
            "kinds": dict(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:8]),
            "examples": examples,
        }
    return profiles


def _pair_candidates_from_sets(
    profiles: Dict[int, Dict[str, Any]],
    key: str,
    source: str,
    topk: int,
    max_fanout: int,
) -> List[Dict[str, Any]]:
    value_to_components: Dict[Any, Set[int]] = defaultdict(set)
    for cid, prof in profiles.items():
        for value in prof.get(key, set()):
            value_to_components[value].add(int(cid))

    pair_hits: Dict[Tuple[int, int], Set[Any]] = defaultdict(set)
    for value, cids in value_to_components.items():
        if len(cids) < 2 or len(cids) > int(max_fanout):
            continue
        ordered = sorted(cids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pair_hits[(int(a), int(b))].add(value)

    candidates: List[Dict[str, Any]] = []
    for (a, b), shared in pair_hits.items():
        pa = profiles.get(a, {})
        pb = profiles.get(b, {})
        set_a = set(pa.get(key, set()))
        set_b = set(pb.get(key, set()))
        if not set_a or not set_b:
            continue
        union = set_a | set_b
        jacc = float(len(shared)) / float(len(union)) if union else 0.0
        size_a = int(pa.get("size") or 0)
        size_b = int(pb.get("size") or 0)
        balance = float(min(size_a, size_b)) / float(max(size_a, size_b)) if max(size_a, size_b) > 0 else 0.0
        source_weight = 1.0
        if source == "scene":
            source_weight = 1.12
        elif source == "notes":
            source_weight = 0.92
        score = min(1.0, source_weight * ((0.82 * jacc) + (0.18 * math.sqrt(max(0.0, balance)))))
        if score <= 0:
            continue
        shared_values: List[Any] = sorted(shared, key=lambda x: str(x))[:12]
        candidates.append({
            "source": source,
            "component_a": int(a),
            "component_b": int(b),
            "size_a": int(size_a),
            "size_b": int(size_b),
            "score": round(float(score), 6),
            "shared": shared_values,
            "examples_a": pa.get("examples", [])[:3],
            "examples_b": pb.get("examples", [])[:3],
        })

    candidates.sort(key=lambda d: (-float(d.get("score") or 0.0), -(int(d.get("size_a") or 0) + int(d.get("size_b") or 0)), int(d.get("component_a") or 0), int(d.get("component_b") or 0)))
    return candidates[:max(0, int(topk))]


def _combine_candidates(
    label_candidates: Sequence[Dict[str, Any]],
    scene_candidates: Sequence[Dict[str, Any]],
    notes_candidates: Sequence[Dict[str, Any]],
    context_ref_candidates: Sequence[Dict[str, Any]],
    context_medium_candidates: Sequence[Dict[str, Any]],
    topk: int,
) -> List[Dict[str, Any]]:
    by_pair: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for cand in (
        list(label_candidates)
        + list(scene_candidates)
        + list(notes_candidates)
        + list(context_ref_candidates)
        + list(context_medium_candidates)
    ):
        a = int(cand.get("component_a") or -1)
        b = int(cand.get("component_b") or -1)
        if a < 0 or b < 0 or a == b:
            continue
        key = (min(a, b), max(a, b))
        entry = by_pair.setdefault(key, {
            "component_a": key[0],
            "component_b": key[1],
            "size_a": int(cand.get("size_a") or 0),
            "size_b": int(cand.get("size_b") or 0),
            "score": 0.0,
            "sources": [],
            "evidence": {},
            "examples_a": cand.get("examples_a", [])[:3],
            "examples_b": cand.get("examples_b", [])[:3],
        })
        src = str(cand.get("source") or "unknown")
        src_score = _safe_float(cand.get("score"), 0.0)
        if src not in entry["sources"]:
            entry["sources"].append(src)
        entry["evidence"][src] = {
            "score": round(src_score, 6),
            "shared": cand.get("shared", [])[:12],
        }
        entry["score"] = min(1.0, _safe_float(entry.get("score"), 0.0) + src_score)

    out = list(by_pair.values())
    for entry in out:
        # Multi-source evidence is more valuable than a single weak overlap, but
        # still capped to keep Stage A-2 conservative.
        src_bonus = min(0.12, 0.04 * max(0, len(entry.get("sources", [])) - 1))
        entry["score"] = round(min(1.0, _safe_float(entry.get("score"), 0.0) + src_bonus), 6)
        entry["sources"] = sorted(entry.get("sources", []))
    out.sort(key=lambda d: (-float(d.get("score") or 0.0), -len(d.get("sources", [])), -(int(d.get("size_a") or 0) + int(d.get("size_b") or 0)), int(d.get("component_a") or 0), int(d.get("component_b") or 0)))
    return out[:max(0, int(topk))]


def _projected_metrics(nodes_by_comp: Dict[int, List[int]], candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    comp_sizes = {int(cid): len(nodes) for cid, nodes in nodes_by_comp.items()}
    if not comp_sizes:
        return {"projected_components": 0, "projected_giant_share": 0.0, "bridgeable_components": 0}

    parent = {cid: cid for cid in comp_sizes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    bridgeable: Set[int] = set()
    for c in candidates:
        a = int(c.get("component_a") or -1)
        b = int(c.get("component_b") or -1)
        if a in parent and b in parent and a != b:
            bridgeable.add(a)
            bridgeable.add(b)
            union(a, b)

    projected: Dict[int, int] = defaultdict(int)
    for cid, sz in comp_sizes.items():
        projected[find(cid)] += int(sz)
    total_nodes = sum(comp_sizes.values())
    giant = max(projected.values()) if projected else 0
    return {
        "projected_components": int(len(projected)),
        "projected_giant_share": float(giant) / float(total_nodes) if total_nodes > 0 else 0.0,
        "bridgeable_components": int(len(bridgeable)),
    }


def _component_diagnostics(nodes_by_comp: Dict[int, List[int]], profiles: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    sizes = sorted((len(v) for v in nodes_by_comp.values()), reverse=True)
    sample_components: List[Dict[str, Any]] = []
    for cid, size in sorted(((cid, len(ids)) for cid, ids in nodes_by_comp.items()), key=lambda kv: (-kv[1], kv[0]))[:8]:
        prof = profiles.get(int(cid), {})
        sample_components.append({
            "component_id": int(cid),
            "size": int(size),
            "edge_count": int(prof.get("edge_count") or 0),
            "kinds": prof.get("kinds", {}),
            "top_label_tokens": prof.get("top_label_tokens", [])[:6],
            "top_scene_ids": prof.get("top_scene_ids", [])[:5],
            "top_notes_tokens": prof.get("top_notes_tokens", [])[:5],
            "examples": prof.get("examples", [])[:3],
        })
    return {
        "component_largest_size": int(sizes[0]) if sizes else 0,
        "component_median_size": int(_median_int(sizes)),
        "component_smallest_size": int(sizes[-1]) if sizes else 0,
        "component_sizes_top": [int(x) for x in sizes[:20]],
        "component_singletons": int(sum(1 for x in sizes if int(x) == 1)),
        "component_small_lt_5": int(sum(1 for x in sizes if int(x) < 5)),
        "sample_components": sample_components,
    }


def _anchor_type(label: str) -> str:
    s = str(label or "").strip().lower()
    if s.startswith("ref:"):
        return "ref"
    if s.startswith("episode:"):
        return "episode"
    if s.startswith("time_bucket:"):
        return "time_bucket"
    if s.startswith("neighbor_time_bucket:"):
        return "neighbor_time_bucket"
    if s.startswith("episode_sequence_bucket:"):
        return "episode_sequence_bucket"
    if s.startswith("snapchain_nearby_bucket:"):
        return "snapchain_nearby_bucket"
    if s.startswith("origin_time_bucket:"):
        return "origin_time_bucket"
    if s.startswith("scope_event_type:"):
        return "scope_event_type"
    if s.startswith("scope:"):
        return "scope"
    if s.startswith("event_type:"):
        return "event_type"
    return "other"


def _context_evidence(
    nodes_by_comp: Dict[int, List[int]],
    window_sec: int,
    limit_edges: int,
    topk: int,
) -> Dict[str, Any]:
    node_to_comp: Dict[int, int] = {}
    for cid, ids in nodes_by_comp.items():
        for nid in ids:
            node_to_comp[int(nid)] = int(cid)

    context_rows = _fetch_context_rows(window_sec=int(window_sec), limit_edges=int(limit_edges))
    context_node_ids: List[int] = []
    for row in context_rows:
        context_node_ids.append(int(row.get("a_id") or 0))
        context_node_ids.append(int(row.get("b_id") or 0))
    context_meta = _fetch_nodes(context_node_ids)

    anchor_counts: Dict[str, int] = defaultdict(int)
    anchor_type_counts: Dict[str, int] = defaultdict(int)
    anchor_to_components: Dict[str, Set[int]] = defaultdict(set)
    generic_anchor_to_components: Dict[str, Set[int]] = defaultdict(set)
    events_with_context: Set[int] = set()
    specific_edges = 0
    generic_edges = 0
    ref_edges = 0
    episode_edges = 0
    time_bucket_edges = 0
    neighbor_time_bucket_edges = 0
    episode_sequence_bucket_edges = 0
    snapchain_nearby_bucket_edges = 0
    origin_time_bucket_edges = 0
    scope_event_type_edges = 0
    scope_edges = 0
    event_type_edges = 0
    other_edges = 0
    medium_anchor_to_components: Dict[str, Set[int]] = defaultdict(set)

    for row in context_rows:
        a = int(row.get("a_id") or 0)
        b = int(row.get("b_id") or 0)
        comp = node_to_comp.get(a, node_to_comp.get(b, -1))
        ma = context_meta.get(a, {})
        mb = context_meta.get(b, {})
        ka = str(ma.get("kind") or "").lower()
        kb = str(mb.get("kind") or "").lower()
        la = str(ma.get("label") or "")
        lb = str(mb.get("label") or "")
        anchor_label = lb if kb == "context" else (la if ka == "context" else lb)
        anchor_label = str(anchor_label or "unknown")[:180]
        typ = _anchor_type(anchor_label)
        anchor_counts[anchor_label] += 1
        anchor_type_counts[typ] += 1
        if a in node_to_comp:
            events_with_context.add(a)
        if b in node_to_comp:
            events_with_context.add(b)
        if typ == "ref":
            ref_edges += 1
            specific_edges += 1
            if comp >= 0:
                anchor_to_components[anchor_label].add(int(comp))
        elif typ in ("episode", "time_bucket", "neighbor_time_bucket", "episode_sequence_bucket", "snapchain_nearby_bucket", "origin_time_bucket"):
            specific_edges += 1
            if typ == "episode":
                episode_edges += 1
            elif typ == "time_bucket":
                time_bucket_edges += 1
            elif typ == "neighbor_time_bucket":
                neighbor_time_bucket_edges += 1
            elif typ == "episode_sequence_bucket":
                episode_sequence_bucket_edges += 1
            elif typ == "snapchain_nearby_bucket":
                snapchain_nearby_bucket_edges += 1
            elif typ == "origin_time_bucket":
                origin_time_bucket_edges += 1
            if comp >= 0:
                medium_anchor_to_components[anchor_label].add(int(comp))
        elif typ == "scope_event_type":
            scope_event_type_edges += 1
            generic_edges += 1
            if comp >= 0:
                generic_anchor_to_components[anchor_label].add(int(comp))
        elif typ in ("scope", "event_type"):
            generic_edges += 1
            if typ == "scope":
                scope_edges += 1
            else:
                event_type_edges += 1
            if comp >= 0:
                generic_anchor_to_components[anchor_label].add(int(comp))
        else:
            other_edges += 1
            # Other anchors are measured but not trusted as strong evidence yet.

    candidates: List[Dict[str, Any]] = []
    for anchor, cids in anchor_to_components.items():
        if len(cids) < 2 or len(cids) > 80:
            continue
        ordered = sorted(cids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                sa = len(nodes_by_comp.get(a, []))
                sb = len(nodes_by_comp.get(b, []))
                balance = float(min(sa, sb)) / float(max(sa, sb)) if max(sa, sb) > 0 else 0.0
                score = min(1.0, 0.70 + (0.30 * math.sqrt(max(0.0, balance))))
                candidates.append({
                    "source": "context_ref",
                    "component_a": int(a),
                    "component_b": int(b),
                    "size_a": int(sa),
                    "size_b": int(sb),
                    "score": round(float(score), 6),
                    "shared": [anchor],
                    "examples_a": [],
                    "examples_b": [],
                })
    candidates.sort(key=lambda d: (-float(d.get("score") or 0.0), int(d.get("component_a") or 0), int(d.get("component_b") or 0)))

    medium_candidates: List[Dict[str, Any]] = []
    for anchor, cids in medium_anchor_to_components.items():
        typ = _anchor_type(anchor)
        if len(cids) < 2 or len(cids) > 120:
            continue
        ordered = sorted(cids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                sa = len(nodes_by_comp.get(a, []))
                sb = len(nodes_by_comp.get(b, []))
                balance = float(min(sa, sb)) / float(max(sa, sb)) if max(sa, sb) > 0 else 0.0
                base_by_type = {
                    "episode": 0.62,
                    "time_bucket": 0.56,
                    "neighbor_time_bucket": 0.52,
                    "episode_sequence_bucket": 0.58,
                    "snapchain_nearby_bucket": 0.60,
                    "origin_time_bucket": 0.54,
                }
                cap_by_type = {
                    "episode": 0.88,
                    "time_bucket": 0.86,
                    "neighbor_time_bucket": 0.80,
                    "episode_sequence_bucket": 0.84,
                    "snapchain_nearby_bucket": 0.86,
                    "origin_time_bucket": 0.82,
                }
                base = float(base_by_type.get(typ, 0.52))
                cap = float(cap_by_type.get(typ, 0.80))
                score = min(cap, base + (0.22 * math.sqrt(max(0.0, balance))))
                medium_candidates.append({
                    "source": f"context_{typ}",
                    "component_a": int(a),
                    "component_b": int(b),
                    "size_a": int(sa),
                    "size_b": int(sb),
                    "score": round(float(score), 6),
                    "shared": [anchor],
                    "examples_a": [],
                    "examples_b": [],
                })
    medium_candidates.sort(key=lambda d: (-float(d.get("score") or 0.0), int(d.get("component_a") or 0), int(d.get("component_b") or 0)))

    generic_pair_count = 0
    for _anchor, cids in generic_anchor_to_components.items():
        n = len(cids)
        if 2 <= n <= 10000:
            generic_pair_count += int((n * (n - 1)) / 2)

    return {
        "context_edges_scanned": int(len(context_rows)),
        "context_events_with_anchor": int(len(events_with_context)),
        "context_distinct_anchor_nodes": int(len(anchor_counts)),
        "context_ref_edges": int(ref_edges),
        "context_episode_edges": int(episode_edges),
        "context_time_bucket_edges": int(time_bucket_edges),
        "context_neighbor_time_bucket_edges": int(neighbor_time_bucket_edges),
        "context_episode_sequence_bucket_edges": int(episode_sequence_bucket_edges),
        "context_snapchain_nearby_bucket_edges": int(snapchain_nearby_bucket_edges),
        "context_origin_time_bucket_edges": int(origin_time_bucket_edges),
        "context_scope_event_type_edges": int(scope_event_type_edges),
        "context_scope_edges": int(scope_edges),
        "context_event_type_edges": int(event_type_edges),
        "context_specific_edges": int(specific_edges),
        "context_generic_edges": int(generic_edges),
        "context_other_edges": int(other_edges),
        "context_ref_candidate_pairs": int(len(candidates)),
        "context_medium_candidate_pairs": int(len(medium_candidates)),
        "context_generic_candidate_pairs": int(generic_pair_count),
        "context_ref_top_score": _top_score(candidates),
        "context_medium_top_score": _top_score(medium_candidates),
        "top_context_anchor_types": [[k, int(v)] for k, v in sorted(anchor_type_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1, int(topk))]],
        "top_context_anchor_labels": [[k, int(v)] for k, v in sorted(anchor_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1, int(topk))]],
        "context_ref_candidates": candidates[:max(0, int(topk))],
        "context_medium_candidates": medium_candidates[:max(0, int(topk))],
    }


def _build_diagnosis(
    edge_count: int,
    node_count: int,
    comp_count: int,
    label_count: int,
    scene_count: int,
    notes_count: int,
    combined_count: int,
) -> Dict[str, Any]:
    reason = "ok"
    if edge_count <= 0:
        reason = "no_synaptic_edges_in_selected_window"
    elif node_count <= 0:
        reason = "no_nodes_after_filtering"
    elif comp_count <= 1:
        reason = "graph_already_single_component_or_empty"
    elif combined_count <= 0:
        reason = "components_have_no_shared_label_scene_or_notes_context"
    return {
        "direct_overlap_candidates": 0,
        "label_candidates": int(label_count),
        "scene_candidates": int(scene_count),
        "notes_candidates": int(notes_count),
        "combined_candidates": int(combined_count),
        "reason_if_zero": reason,
    }


def _top_score(candidates: Sequence[Dict[str, Any]]) -> float:
    return float(candidates[0].get("score") or 0.0) if candidates else 0.0


def _avg_score(candidates: Sequence[Dict[str, Any]]) -> float:
    return sum(float(c.get("score") or 0.0) for c in candidates) / float(len(candidates)) if candidates else 0.0


def run_once(window_sec: int, limit_edges: int, topk: int, max_nodes: int) -> Dict[str, Any]:
    _dbw_required()
    ts = _now_ts()
    t0 = time.time()

    edge_rows = _fetch_edge_rows(window_sec=int(window_sec), limit_edges=int(limit_edges))
    edges = [(int(r["a_id"]), int(r["b_id"])) for r in edge_rows]
    node_ids = sorted({int(x) for e in edges for x in e})
    truncated_nodes = False
    if len(node_ids) > int(max_nodes):
        node_ids = node_ids[:int(max_nodes)]
        allowed = set(node_ids)
        edge_rows = [r for r in edge_rows if int(r["a_id"]) in allowed and int(r["b_id"]) in allowed]
        edges = [(int(r["a_id"]), int(r["b_id"])) for r in edge_rows]
        truncated_nodes = True

    nodes_by_comp, _adj = _components(edges)
    node_meta = _fetch_nodes(node_ids)
    profiles = _component_profiles(nodes_by_comp, node_meta, edge_rows)

    label_candidates = _pair_candidates_from_sets(profiles, key="label_tokens", source="label", topk=max(int(topk) * 2, 50), max_fanout=60)
    scene_candidates = _pair_candidates_from_sets(profiles, key="scene_ids", source="scene", topk=max(int(topk) * 2, 50), max_fanout=80)
    notes_candidates = _pair_candidates_from_sets(profiles, key="notes_tokens", source="notes", topk=max(int(topk) * 2, 50), max_fanout=60)
    context_info = _context_evidence(nodes_by_comp, window_sec=int(window_sec), limit_edges=int(limit_edges), topk=int(topk))
    context_ref_candidates = list(context_info.get("context_ref_candidates") or [])
    context_medium_candidates = list(context_info.get("context_medium_candidates") or [])
    candidates = _combine_candidates(label_candidates, scene_candidates, notes_candidates, context_ref_candidates, context_medium_candidates, topk=int(topk))

    projected = _projected_metrics(nodes_by_comp, candidates)
    bridge_rows = _fetch_bridge_rows(window_sec=int(window_sec), limit_edges=int(limit_edges))
    with_bridge = _with_bridge_metrics(nodes_by_comp, bridge_rows)
    comp_diag = _component_diagnostics(nodes_by_comp, profiles)

    comp_count = len(nodes_by_comp)
    node_count = len(node_ids)
    current_giant = max((len(v) for v in nodes_by_comp.values()), default=0)
    current_giant_share = float(current_giant) / float(node_count) if node_count > 0 else 0.0
    top_score = _top_score(candidates)
    avg_score = _avg_score(candidates)

    meta = {"window_sec": int(window_sec), "limit_edges": int(limit_edges), "topk": int(topk), "stage": "A6_context_neighbor_anchor_with_bridge_measure_only"}
    uid = str(ts)

    # Serien-Suffix passend zum tatsächlich analysierten Fenster.
    series_suffix = "7d" if int(window_sec) >= 604800 else "24h"
    _write_stat(ts, f"synapses.bridge.candidate_pairs_{series_suffix}", float(len(candidates)), uid + f":{series_suffix}:pairs", meta)
    _write_stat(ts, f"synapses.bridge.top_score_{series_suffix}", float(top_score), uid + f":{series_suffix}:top", meta)
    _write_stat(ts, f"synapses.bridge.avg_score_{series_suffix}", float(avg_score), uid + f":{series_suffix}:avg", meta)
    _write_stat(ts, f"synapses.bridge.bridgeable_components_{series_suffix}", float(projected["bridgeable_components"]), uid + f":{series_suffix}:bridgeable", meta)
    _write_stat(ts, f"synapses.bridge.projected_components_{series_suffix}", float(projected["projected_components"]), uid + f":{series_suffix}:proj_components", meta)
    _write_stat(ts, f"synapses.bridge.projected_giant_share_{series_suffix}", float(projected["projected_giant_share"]), uid + f":{series_suffix}:proj_giant", meta)

    _write_stat(ts, f"synapses.bridge.label_candidate_pairs_{series_suffix}", float(len(label_candidates)), uid + f":{series_suffix}:label_pairs", meta)
    _write_stat(ts, f"synapses.bridge.label_top_score_{series_suffix}", float(_top_score(label_candidates)), uid + f":{series_suffix}:label_top", meta)
    _write_stat(ts, f"synapses.bridge.scene_candidate_pairs_{series_suffix}", float(len(scene_candidates)), uid + f":{series_suffix}:scene_pairs", meta)
    _write_stat(ts, f"synapses.bridge.scene_top_score_{series_suffix}", float(_top_score(scene_candidates)), uid + f":{series_suffix}:scene_top", meta)
    _write_stat(ts, f"synapses.bridge.notes_candidate_pairs_{series_suffix}", float(len(notes_candidates)), uid + f":{series_suffix}:notes_pairs", meta)
    _write_stat(ts, f"synapses.bridge.notes_top_score_{series_suffix}", float(_top_score(notes_candidates)), uid + f":{series_suffix}:notes_top", meta)
    _write_stat(ts, f"synapses.bridge.component_largest_size_{series_suffix}", float(comp_diag["component_largest_size"]), uid + f":{series_suffix}:component_largest", meta)
    _write_stat(ts, f"synapses.bridge.component_median_size_{series_suffix}", float(comp_diag["component_median_size"]), uid + f":{series_suffix}:component_median", meta)
    _write_stat(ts, f"synapses.bridge.component_singletons_{series_suffix}", float(comp_diag["component_singletons"]), uid + f":{series_suffix}:component_singletons", meta)
    _write_stat(ts, f"synapses.bridge.component_small_lt_5_{series_suffix}", float(comp_diag["component_small_lt_5"]), uid + f":{series_suffix}:component_small_lt_5", meta)
    _write_stat(ts, f"synapses.context.edges_{series_suffix}", float(context_info.get("context_edges_scanned") or 0), uid + f":{series_suffix}:context_edges", meta)
    _write_stat(ts, f"synapses.context.events_with_anchor_{series_suffix}", float(context_info.get("context_events_with_anchor") or 0), uid + f":{series_suffix}:context_events", meta)
    _write_stat(ts, f"synapses.context.distinct_anchor_nodes_{series_suffix}", float(context_info.get("context_distinct_anchor_nodes") or 0), uid + f":{series_suffix}:context_distinct", meta)
    _write_stat(ts, f"synapses.context.ref_anchor_edges_{series_suffix}", float(context_info.get("context_ref_edges") or 0), uid + f":{series_suffix}:context_ref_edges", meta)
    _write_stat(ts, f"synapses.context.episode_anchor_edges_{series_suffix}", float(context_info.get("context_episode_edges") or 0), uid + f":{series_suffix}:context_episode_edges", meta)
    _write_stat(ts, f"synapses.context.time_bucket_anchor_edges_{series_suffix}", float(context_info.get("context_time_bucket_edges") or 0), uid + f":{series_suffix}:context_time_bucket_edges", meta)
    _write_stat(ts, f"synapses.context.neighbor_time_bucket_anchor_edges_{series_suffix}", float(context_info.get("context_neighbor_time_bucket_edges") or 0), uid + f":{series_suffix}:context_neighbor_time_bucket_edges", meta)
    _write_stat(ts, f"synapses.context.episode_sequence_bucket_anchor_edges_{series_suffix}", float(context_info.get("context_episode_sequence_bucket_edges") or 0), uid + f":{series_suffix}:context_episode_sequence_bucket_edges", meta)
    _write_stat(ts, f"synapses.context.snapchain_nearby_bucket_anchor_edges_{series_suffix}", float(context_info.get("context_snapchain_nearby_bucket_edges") or 0), uid + f":{series_suffix}:context_snapchain_nearby_bucket_edges", meta)
    _write_stat(ts, f"synapses.context.origin_time_bucket_anchor_edges_{series_suffix}", float(context_info.get("context_origin_time_bucket_edges") or 0), uid + f":{series_suffix}:context_origin_time_bucket_edges", meta)
    _write_stat(ts, f"synapses.context.scope_event_type_anchor_edges_{series_suffix}", float(context_info.get("context_scope_event_type_edges") or 0), uid + f":{series_suffix}:context_scope_event_type_edges", meta)
    _write_stat(ts, f"synapses.context.generic_anchor_edges_{series_suffix}", float(context_info.get("context_generic_edges") or 0), uid + f":{series_suffix}:context_generic_edges", meta)
    _write_stat(ts, f"synapses.bridge.context_ref_candidate_pairs_{series_suffix}", float(context_info.get("context_ref_candidate_pairs") or 0), uid + f":{series_suffix}:context_ref_pairs", meta)
    _write_stat(ts, f"synapses.bridge.context_medium_candidate_pairs_{series_suffix}", float(context_info.get("context_medium_candidate_pairs") or 0), uid + f":{series_suffix}:context_medium_pairs", meta)
    _write_stat(ts, f"synapses.bridge.context_generic_candidate_pairs_{series_suffix}", float(context_info.get("context_generic_candidate_pairs") or 0), uid + f":{series_suffix}:context_generic_pairs", meta)
    _write_stat(ts, f"synapses.bridge.context_ref_top_score_{series_suffix}", float(context_info.get("context_ref_top_score") or 0.0), uid + f":{series_suffix}:context_ref_top", meta)
    _write_stat(ts, f"synapses.bridge.context_medium_top_score_{series_suffix}", float(context_info.get("context_medium_top_score") or 0.0), uid + f":{series_suffix}:context_medium_top", meta)

    _write_stat(ts, f"synapses.with_bridge.components_{series_suffix}", float(with_bridge.get("with_bridge_components") or 0), uid + f":{series_suffix}:with_bridge_components", meta)
    _write_stat(ts, f"synapses.with_bridge.giant_share_{series_suffix}", float(with_bridge.get("with_bridge_giant_share") or 0.0), uid + f":{series_suffix}:with_bridge_giant", meta)
    _write_stat(ts, f"synapses.with_bridge.edges_{series_suffix}", float((len(edges) + int(with_bridge.get("with_bridge_bridge_edges") or 0))), uid + f":{series_suffix}:with_bridge_edges", meta)
    _write_stat(ts, f"synapses.with_bridge.bridge_edges_{series_suffix}", float(with_bridge.get("with_bridge_bridge_edges") or 0), uid + f":{series_suffix}:with_bridge_bridge_edges", meta)
    _write_stat(ts, f"synapses.with_bridge.bridge_edges_used_{series_suffix}", float(with_bridge.get("with_bridge_bridge_edges_used") or 0), uid + f":{series_suffix}:with_bridge_bridge_edges_used", meta)
    _write_stat(ts, f"synapses.with_bridge.delta_components_{series_suffix}", float(with_bridge.get("with_bridge_delta_components") or 0), uid + f":{series_suffix}:with_bridge_delta_components", meta)
    _write_stat(ts, f"synapses.with_bridge.delta_giant_share_{series_suffix}", float(with_bridge.get("with_bridge_delta_giant_share") or 0.0), uid + f":{series_suffix}:with_bridge_delta_giant", meta)

    diagnosis = _build_diagnosis(
        edge_count=len(edges),
        node_count=node_count,
        comp_count=comp_count,
        label_count=len(label_candidates),
        scene_count=len(scene_candidates),
        notes_count=len(notes_candidates),
        combined_count=len(candidates),
    )
    diagnosis.update({
        "context_edges_scanned": int(context_info.get("context_edges_scanned") or 0),
        "context_ref_candidates": int(context_info.get("context_ref_candidate_pairs") or 0),
        "context_medium_candidates": int(context_info.get("context_medium_candidate_pairs") or 0),
        "context_generic_candidates_measured_only": int(context_info.get("context_generic_candidate_pairs") or 0),
        "context_generic_excluded_from_strong_bridge": True,
        "reason_if_context_zero": "no_synaptic_context_edges_in_selected_window" if int(context_info.get("context_edges_scanned") or 0) <= 0 else "context_edges_present",
        "with_bridge_measured": True,
        "with_bridge_edges_used": int(with_bridge.get("with_bridge_bridge_edges_used") or 0),
        "with_bridge_delta_components": int(with_bridge.get("with_bridge_delta_components") or 0),
    })

    state = {
        "ok": True,
        "stage": "A6_context_neighbor_anchor_with_bridge_measure_only",
        "last_run_ts": int(ts),
        "window_sec": int(window_sec),
        "limit_edges": int(limit_edges),
        "edges_scanned": int(len(edges)),
        "nodes": int(node_count),
        "components": int(comp_count),
        "current_giant_share": round(float(current_giant_share), 6),
        "candidate_pairs": int(len(candidates)),
        "label_candidate_pairs": int(len(label_candidates)),
        "scene_candidate_pairs": int(len(scene_candidates)),
        "notes_candidate_pairs": int(len(notes_candidates)),
        "context_edges_scanned": int(context_info.get("context_edges_scanned") or 0),
        "context_events_with_anchor": int(context_info.get("context_events_with_anchor") or 0),
        "context_distinct_anchor_nodes": int(context_info.get("context_distinct_anchor_nodes") or 0),
        "context_ref_edges": int(context_info.get("context_ref_edges") or 0),
        "context_episode_edges": int(context_info.get("context_episode_edges") or 0),
        "context_time_bucket_edges": int(context_info.get("context_time_bucket_edges") or 0),
        "context_neighbor_time_bucket_edges": int(context_info.get("context_neighbor_time_bucket_edges") or 0),
        "context_episode_sequence_bucket_edges": int(context_info.get("context_episode_sequence_bucket_edges") or 0),
        "context_snapchain_nearby_bucket_edges": int(context_info.get("context_snapchain_nearby_bucket_edges") or 0),
        "context_origin_time_bucket_edges": int(context_info.get("context_origin_time_bucket_edges") or 0),
        "context_scope_event_type_edges": int(context_info.get("context_scope_event_type_edges") or 0),
        "context_scope_edges": int(context_info.get("context_scope_edges") or 0),
        "context_event_type_edges": int(context_info.get("context_event_type_edges") or 0),
        "context_specific_edges": int(context_info.get("context_specific_edges") or 0),
        "context_generic_edges": int(context_info.get("context_generic_edges") or 0),
        "context_other_edges": int(context_info.get("context_other_edges") or 0),
        "context_ref_candidate_pairs": int(context_info.get("context_ref_candidate_pairs") or 0),
        "context_medium_candidate_pairs": int(context_info.get("context_medium_candidate_pairs") or 0),
        "context_generic_candidate_pairs": int(context_info.get("context_generic_candidate_pairs") or 0),
        "context_ref_top_score": round(float(context_info.get("context_ref_top_score") or 0.0), 6),
        "context_medium_top_score": round(float(context_info.get("context_medium_top_score") or 0.0), 6),
        "top_score": round(float(top_score), 6),
        "avg_score": round(float(avg_score), 6),
        "label_top_score": round(float(_top_score(label_candidates)), 6),
        "scene_top_score": round(float(_top_score(scene_candidates)), 6),
        "notes_top_score": round(float(_top_score(notes_candidates)), 6),
        "bridgeable_components": int(projected["bridgeable_components"]),
        "projected_components": int(projected["projected_components"]),
        "projected_giant_share": round(float(projected["projected_giant_share"]), 6),
        "with_bridge_components": int(with_bridge.get("with_bridge_components") or 0),
        "with_bridge_giant_share": round(float(with_bridge.get("with_bridge_giant_share") or 0.0), 6),
        "with_bridge_edges": int(len(edges) + int(with_bridge.get("with_bridge_bridge_edges") or 0)),
        "with_bridge_bridge_edges": int(with_bridge.get("with_bridge_bridge_edges") or 0),
        "with_bridge_bridge_edges_used": int(with_bridge.get("with_bridge_bridge_edges_used") or 0),
        "with_bridge_bridge_edges_ignored": int(with_bridge.get("with_bridge_bridge_edges_ignored") or 0),
        "with_bridge_bridgeable_components": int(with_bridge.get("with_bridge_bridgeable_components") or 0),
        "with_bridge_delta_components": int(with_bridge.get("with_bridge_delta_components") or 0),
        "with_bridge_delta_giant_share": round(float(with_bridge.get("with_bridge_delta_giant_share") or 0.0), 6),
        "truncated_nodes": bool(truncated_nodes),
        "component_largest_size": int(comp_diag["component_largest_size"]),
        "component_median_size": int(comp_diag["component_median_size"]),
        "component_smallest_size": int(comp_diag["component_smallest_size"]),
        "component_sizes_top": comp_diag["component_sizes_top"],
        "component_singletons": int(comp_diag["component_singletons"]),
        "component_small_lt_5": int(comp_diag["component_small_lt_5"]),
        "sample_components": comp_diag["sample_components"],
        "diagnosis": diagnosis,
        "candidates": candidates,
        "label_candidates": label_candidates[:min(10, int(topk))],
        "scene_candidates": scene_candidates[:min(10, int(topk))],
        "notes_candidates": notes_candidates[:min(10, int(topk))],
        "context_ref_candidates": context_ref_candidates[:min(10, int(topk))],
        "context_medium_candidates": context_medium_candidates[:min(10, int(topk))],
        "top_context_anchor_types": context_info.get("top_context_anchor_types", []),
        "top_context_anchor_labels": context_info.get("top_context_anchor_labels", []),
    }
    _atomic_write_json(STATE_PATH, state)
    state["dt_sec"] = float(round(time.time() - t0, 3))
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA synapses bridge probe Stage A-5 (context medium-anchor measure-only)")
    ap.add_argument("--once", action="store_true", default=True)
    ap.add_argument("--window-sec", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_WINDOW_SEC", "604800")))
    ap.add_argument("--limit-edges", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_LIMIT_EDGES", "50000")))
    ap.add_argument("--topk", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_TOPK", "25")))
    ap.add_argument("--max-nodes", type=int, default=int(os.environ.get("OROMA_SYNAPSES_BRIDGE_MAX_NODES", "8000")))
    ap.add_argument("--verbose", action="store_true", default=False, help="Accepted for operator convenience; JSON state already contains detailed diagnostics.")
    args = ap.parse_args()

    try:
        res = run_once(
            window_sec=int(args.window_sec),
            limit_edges=int(args.limit_edges),
            topk=int(args.topk),
            max_nodes=int(args.max_nodes),
        )
        summary_keys = {
            "ok", "stage", "last_run_ts", "window_sec", "limit_edges", "edges_scanned", "nodes",
            "components", "current_giant_share", "candidate_pairs", "label_candidate_pairs",
            "scene_candidate_pairs", "notes_candidate_pairs", "top_score", "avg_score",
            "bridgeable_components", "projected_components", "projected_giant_share",
            "with_bridge_components", "with_bridge_giant_share", "with_bridge_bridge_edges",
            "with_bridge_delta_components", "with_bridge_delta_giant_share",
            "component_largest_size", "component_median_size", "component_singletons",
            "component_small_lt_5", "context_edges_scanned", "context_events_with_anchor",
            "context_ref_edges", "context_generic_edges", "context_ref_candidate_pairs",
            "context_generic_candidate_pairs", "truncated_nodes", "dt_sec",
        }
        print(json.dumps({k: v for k, v in res.items() if k in summary_keys}, ensure_ascii=False))
        return 0
    except Exception as e:
        log_suppressed("synapses_bridge_probe.error", key="synapses_bridge_probe failed", msg="synapses bridge probe failed", exc=e)
        err_state = {"ok": False, "stage": "A3_context_aware_measure_only", "last_run_ts": _now_ts(), "error": str(e)}
        try:
            _atomic_write_json(STATE_PATH, err_state)
        except Exception:
            pass
        print(json.dumps(err_state, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

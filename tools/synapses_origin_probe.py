#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pfad:    tools/synapses_origin_probe.py
Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
Version: v3.7.x / v3.8 line-compatible
Stand:   2026-05-06
Autor:   Jörg Werner (public), ORÓMA · KI-JWG-X1 (intern)

ZWECK
-----
Synapses Origin Probe – reine Herkunfts-/Qualitätsmessung für bestehende
`object_relations(relation='synaptic')` und ergänzende
`object_relations(relation='synaptic_context')` Kanten.

Dieses Tool beantwortet die operative Frage, warum der Synapsen-Graph zwar
Kanten enthält, Bridge Stage A/A-2 aber keine plausiblen Cross-Komponenten-
Brücken findet. Es materialisiert bewusst NICHTS in `object_relations`, legt
KEINE neuen Tabellen an und verändert KEINE `object_nodes`.

Die Ausgabe erfolgt ausschließlich über:

1. `stats.db -> stats_points` Serien `synapses.origin.*`
2. ein atomisch geschriebenes State-JSON:
   `/opt/ai/oroma/data/state/synapses_origin_probe_state.json`

HINTERGRUND
-----------
Die Live-Messung zeigte:

- 7d-Bestand: viele `synaptic` Kanten vorhanden
- 24h: keine neuen `synaptic` Kanten
- Bridge Stage A-2: 101 Komponenten, typischerweise event:event Inseln
- keine Label-/Scene-/Notes-Brücken zwischen Komponenten

Diese Probe misst deshalb gezielt, aus welchen Node-Typen, Label-Präfixen,
Notes-Mustern und Source-Scene-Kontexten die Synapsen bestehen:

- Anteil event↔event
- Anteil Kanten mit mindestens einem non-event Node
- Anteil Kanten mit source_scene_id
- distinct kind-pairs
- distinct label-prefix-pairs
- Notes-Pattern und interessante Notes-Tokens
- letzte bekannte Synapse und deren Alter
- fehlende object_node-Bezüge
- Context-Anchor-Abdeckung aus `synaptic_context` inkl. A3-Nachbarschaftsankern

DESIGN / SAFETY
---------------
- Measure-only: keine Writes nach oroma.db.
- Keine neuen Tabellen.
- DBWriter-only für stats.db Writes; `OROMA_DBW_ENABLE=1` ist Pflicht.
- SQLite Reads auf oroma.db erfolgen read-only über URI `mode=ro`.
- Alle SQLite-Verbindungen werden mit `try/finally` geschlossen.
- State-JSON wird atomisch geschrieben.
- Headless optimiert: kein Qt/Wayland/X11, keine UI-Abhängigkeit.

ENV
---
- OROMA_SYNAPSES_ORIGIN_WINDOW_SEC   Default: 604800 (7 Tage)
- OROMA_SYNAPSES_ORIGIN_LIMIT_EDGES  Default: 50000
- OROMA_SYNAPSES_ORIGIN_TOPK         Default: 25
- OROMA_DATA_DIR                     Default: /opt/ai/oroma/data

RUN
---
cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 \
  python3 tools/synapses_origin_probe.py --once --window-sec 604800 --topk 25 --verbose
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
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

if __package__ is None and os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "core")):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import db_writer_client  # type: ignore

STATE_PATH = os.path.join(
    os.environ.get("OROMA_DATA_DIR", "/opt/ai/oroma/data"),
    "state",
    "synapses_origin_probe_state.json",
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9äöüÄÖÜß]{3,}")
_GENERIC_NOTE_TOKENS = {
    "hebb", "cooc", "count", "first", "half", "synaptic", "relation",
    "node", "nodes", "event", "events", "true", "false", "none", "null",
    "source", "score", "auto", "oroma", "debug", "window",
}


def _now_ts() -> int:
    return int(time.time())


def _data_dir() -> str:
    return os.environ.get("OROMA_DATA_DIR", "/opt/ai/oroma/data")


def _db_path_oroma() -> str:
    return os.path.join(_data_dir(), "oroma.db")


def _dbw_required() -> None:
    if os.environ.get("OROMA_DBW_ENABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
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
    db_writer_client.exec(
        """
        INSERT OR REPLACE INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """.strip(),
        [
            int(ts),
            str(series),
            float(value),
            "synapses_origin_probe",
            0,
            json.dumps(meta, ensure_ascii=False) if meta else None,
            str(src_uid),
        ],
        tag="synapses.origin.probe",
        priority="normal",
        timeout_ms=3000,
        expect="rowcount",
        db="stats",
    )


def _suffix(window_sec: int) -> str:
    if int(window_sec) == 86400:
        return "24h"
    if int(window_sec) == 604800:
        return "7d"
    if int(window_sec) == 2592000:
        return "30d"
    return f"{int(window_sec)}s"


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isfinite(f):
            return f
    except Exception:
        pass
    return float(default)


def _label_prefix(label: str) -> str:
    s = str(label or "").strip().lower()
    if not s:
        return "unknown"
    for sep in (":", "/", "_", "-", "."):
        if sep in s:
            part = s.split(sep, 1)[0].strip()
            return part or "unknown"
    # event123 etc. should not become high-cardinality prefix.
    m = re.match(r"^([a-zA-ZäöüÄÖÜß]+)", s)
    return (m.group(1).lower() if m else s[:24]) or "unknown"


def _tokens(text: str) -> List[str]:
    raw = str(text or "").replace("_", " ").replace(":", " ").replace("/", " ").replace("-", " ").lower()
    out: List[str] = []
    for m in _TOKEN_RE.finditer(raw):
        t = m.group(0).strip().lower()
        if not t or t.isdigit() or len(t) > 40:
            continue
        out.append(t)
    return out


def _interesting_tokens(text: str) -> List[str]:
    return [t for t in _tokens(text) if t not in _GENERIC_NOTE_TOKENS]


def _norm_pair(a: str, b: str) -> str:
    x = str(a or "unknown").strip().lower() or "unknown"
    y = str(b or "unknown").strip().lower() or "unknown"
    if x <= y:
        return f"{x}↔{y}"
    return f"{y}↔{x}"


def _top(counter: Counter, n: int) -> List[List[Any]]:
    return [[k, int(v)] for k, v in counter.most_common(max(1, int(n)))]


def _fetch_edges(window_sec: int, limit_edges: int) -> List[Dict[str, Any]]:
    db = _db_path_oroma()
    since = _now_ts() - int(window_sec)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, a_id, b_id, confidence, source_scene_id, ts, notes
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
                if a <= 0 or b <= 0:
                    continue
                scene_raw = r["source_scene_id"]
                out.append({
                    "id": int(r["id"]),
                    "a_id": a,
                    "b_id": b,
                    "confidence": _safe_float(r["confidence"], 1.0),
                    "source_scene_id": int(scene_raw) if scene_raw is not None else 0,
                    "ts": int(r["ts"] or 0),
                    "notes": str(r["notes"] or ""),
                })
            except Exception:
                continue
        return out
    finally:
        con.close()


def _fetch_context_edges(window_sec: int, limit_edges: int) -> List[Dict[str, Any]]:
    """Read synaptic_context edges in the selected time window.

    These edges are generated by the NMR Context Anchor stage and deliberately
    kept separate from the event-event `synaptic` backbone.  The probe treats
    them as context evidence only; it does not materialize or upgrade anything.
    """
    db = _db_path_oroma()
    since = _now_ts() - int(window_sec)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, a_id, b_id, confidence, source_scene_id, ts, notes
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
                if a <= 0 or b <= 0:
                    continue
                scene_raw = r["source_scene_id"]
                out.append({
                    "id": int(r["id"]),
                    "a_id": a,
                    "b_id": b,
                    "confidence": _safe_float(r["confidence"], 1.0),
                    "source_scene_id": int(scene_raw) if scene_raw is not None else 0,
                    "ts": int(r["ts"] or 0),
                    "notes": str(r["notes"] or ""),
                })
            except Exception:
                continue
        return out
    finally:
        con.close()


def _fetch_nodes(ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    id_list = sorted({int(x) for x in ids if int(x) > 0})
    if not id_list:
        return {}
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: Dict[int, Dict[str, Any]] = {}
    try:
        for i in range(0, len(id_list), 800):
            chunk = id_list[i:i + 800]
            q = ",".join("?" for _ in chunk)
            for r in con.execute(
                f"SELECT id, kind, label, meta_json, created_ts FROM object_nodes WHERE id IN ({q})",
                chunk,
            ):
                nid = int(r["id"])
                label = str(r["label"] or "")
                kind = str(r["kind"] or "unknown")
                out[nid] = {
                    "id": nid,
                    "kind": kind,
                    "label": label,
                    "prefix": _label_prefix(label),
                    "created_ts": int(r["created_ts"] or 0),
                    "meta_json": str(r["meta_json"] or ""),
                }
        return out
    finally:
        con.close()


def _fetch_last_synaptic_ts() -> int:
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT MAX(ts) FROM object_relations WHERE relation='synaptic'").fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        con.close()


def _fetch_last_context_ts() -> int:
    db = _db_path_oroma()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT MAX(ts) FROM object_relations WHERE relation='synaptic_context'").fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        con.close()


def _local_time(ts: int) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


def analyze(window_sec: int, limit_edges: int, topk: int) -> Dict[str, Any]:
    ts_now = _now_ts()
    edges = _fetch_edges(window_sec=window_sec, limit_edges=limit_edges)
    context_edges = _fetch_context_edges(window_sec=window_sec, limit_edges=limit_edges)
    node_ids = []
    for e in edges:
        node_ids.append(int(e["a_id"]))
        node_ids.append(int(e["b_id"]))
    for e in context_edges:
        node_ids.append(int(e["a_id"]))
        node_ids.append(int(e["b_id"]))
    nodes = _fetch_nodes(node_ids)

    kind_pairs: Counter = Counter()
    label_prefix_pairs: Counter = Counter()
    kind_counts: Counter = Counter()
    label_prefix_counts: Counter = Counter()
    notes_patterns: Counter = Counter()
    notes_tokens: Counter = Counter()
    source_scenes: Counter = Counter()
    context_kind_pairs: Counter = Counter()
    context_label_prefix_pairs: Counter = Counter()
    context_anchor_labels: Counter = Counter()
    context_anchor_types: Counter = Counter()
    context_events: set[int] = set()
    context_sample_edges: List[Dict[str, Any]] = []

    event_event = 0
    non_event = 0
    with_source_scene = 0
    missing_endpoints = 0
    confidence_sum = 0.0
    newest_in_window = 0
    oldest_in_window = 0
    sample_edges: List[Dict[str, Any]] = []

    for e in edges:
        a = nodes.get(int(e["a_id"]))
        b = nodes.get(int(e["b_id"]))
        if a is None:
            missing_endpoints += 1
            a = {"id": int(e["a_id"]), "kind": "missing", "label": "missing", "prefix": "missing"}
        if b is None:
            missing_endpoints += 1
            b = {"id": int(e["b_id"]), "kind": "missing", "label": "missing", "prefix": "missing"}

        ka = str(a.get("kind") or "unknown").lower()
        kb = str(b.get("kind") or "unknown").lower()
        pa = str(a.get("prefix") or "unknown").lower()
        pb = str(b.get("prefix") or "unknown").lower()

        kind_pairs[_norm_pair(ka, kb)] += 1
        label_prefix_pairs[_norm_pair(pa, pb)] += 1
        kind_counts[ka] += 1
        kind_counts[kb] += 1
        label_prefix_counts[pa] += 1
        label_prefix_counts[pb] += 1

        if ka == "event" and kb == "event":
            event_event += 1
        else:
            non_event += 1

        scene_id = int(e.get("source_scene_id") or 0)
        if scene_id > 0:
            with_source_scene += 1
            source_scenes[str(scene_id)] += 1

        note = str(e.get("notes") or "").strip()
        if note:
            # Keep a compact normalized pattern for state diagnostics.
            toks = _tokens(note)
            pattern = ":".join(toks[:8]) if toks else note[:80]
            notes_patterns[pattern] += 1
            for t in _interesting_tokens(note):
                notes_tokens[t] += 1

        confidence_sum += _safe_float(e.get("confidence"), 1.0)
        ets = int(e.get("ts") or 0)
        newest_in_window = max(newest_in_window, ets)
        oldest_in_window = ets if not oldest_in_window else min(oldest_in_window, ets)

        if len(sample_edges) < int(topk):
            sample_edges.append({
                "relation_id": int(e.get("id") or 0),
                "a_id": int(a.get("id") or 0),
                "a_kind": ka,
                "a_label": str(a.get("label") or "")[:120],
                "b_id": int(b.get("id") or 0),
                "b_kind": kb,
                "b_label": str(b.get("label") or "")[:120],
                "source_scene_id": scene_id,
                "ts": ets,
                "local_time": _local_time(ets),
                "notes": note[:160],
            })

    context_ref_edges = 0
    context_episode_edges = 0
    context_time_bucket_edges = 0
    context_neighbor_time_bucket_edges = 0
    context_episode_sequence_bucket_edges = 0
    context_snapchain_nearby_bucket_edges = 0
    context_origin_time_bucket_edges = 0
    context_scope_event_type_edges = 0
    context_scope_edges = 0
    context_event_type_edges = 0
    context_generic_edges = 0
    context_other_edges = 0

    for e in context_edges:
        a = nodes.get(int(e["a_id"]))
        b = nodes.get(int(e["b_id"]))
        if a is None:
            a = {"id": int(e["a_id"]), "kind": "missing", "label": "missing", "prefix": "missing"}
        if b is None:
            b = {"id": int(e["b_id"]), "kind": "missing", "label": "missing", "prefix": "missing"}

        ka = str(a.get("kind") or "unknown").lower()
        kb = str(b.get("kind") or "unknown").lower()
        la = str(a.get("label") or "")
        lb = str(b.get("label") or "")
        pa = str(a.get("prefix") or _label_prefix(la) or "unknown").lower()
        pb = str(b.get("prefix") or _label_prefix(lb) or "unknown").lower()
        context_kind_pairs[_norm_pair(ka, kb)] += 1
        context_label_prefix_pairs[_norm_pair(pa, pb)] += 1

        # In the current NMR context design the event is usually the A-side and
        # the context anchor is the B-side.  Keep this robust in case future
        # writers reverse the direction.
        anchor_label = lb if kb == "context" else (la if ka == "context" else lb)
        event_id = int(a.get("id") or 0) if ka == "event" else (int(b.get("id") or 0) if kb == "event" else 0)
        if event_id > 0:
            context_events.add(event_id)
        anchor = str(anchor_label or "unknown")[:160]
        context_anchor_labels[anchor] += 1
        if anchor.startswith("ref:"):
            context_ref_edges += 1
            context_anchor_types["ref"] += 1
        elif anchor.startswith("episode:"):
            context_episode_edges += 1
            context_anchor_types["episode"] += 1
        elif anchor.startswith("time_bucket:"):
            context_time_bucket_edges += 1
            context_anchor_types["time_bucket"] += 1
        elif anchor.startswith("neighbor_time_bucket:"):
            context_neighbor_time_bucket_edges += 1
            context_anchor_types["neighbor_time_bucket"] += 1
        elif anchor.startswith("episode_sequence_bucket:"):
            context_episode_sequence_bucket_edges += 1
            context_anchor_types["episode_sequence_bucket"] += 1
        elif anchor.startswith("snapchain_nearby_bucket:"):
            context_snapchain_nearby_bucket_edges += 1
            context_anchor_types["snapchain_nearby_bucket"] += 1
        elif anchor.startswith("origin_time_bucket:"):
            context_origin_time_bucket_edges += 1
            context_anchor_types["origin_time_bucket"] += 1
        elif anchor.startswith("scope_event_type:"):
            context_scope_event_type_edges += 1
            context_generic_edges += 1
            context_anchor_types["scope_event_type"] += 1
        elif anchor.startswith("scope:"):
            context_scope_edges += 1
            context_generic_edges += 1
            context_anchor_types["scope"] += 1
        elif anchor.startswith("event_type:"):
            context_event_type_edges += 1
            context_generic_edges += 1
            context_anchor_types["event_type"] += 1
        else:
            context_other_edges += 1
            context_anchor_types["other"] += 1

        if len(context_sample_edges) < int(topk):
            ets = int(e.get("ts") or 0)
            context_sample_edges.append({
                "relation_id": int(e.get("id") or 0),
                "a_id": int(a.get("id") or 0),
                "a_kind": ka,
                "a_label": la[:120],
                "b_id": int(b.get("id") or 0),
                "b_kind": kb,
                "b_label": lb[:120],
                "anchor_label": anchor,
                "anchor_type": (
                    "ref" if anchor.startswith("ref:") else
                    "episode" if anchor.startswith("episode:") else
                    "time_bucket" if anchor.startswith("time_bucket:") else
                    "scope_event_type" if anchor.startswith("scope_event_type:") else
                    "scope" if anchor.startswith("scope:") else
                    "event_type" if anchor.startswith("event_type:") else
                    "other"
                ),
                "confidence": _safe_float(e.get("confidence"), 0.0),
                "ts": ets,
                "local_time": _local_time(ets),
                "notes": str(e.get("notes") or "")[:160],
            })

    n_edges = len(edges)
    last_total_ts = _fetch_last_synaptic_ts()
    last_age_hours = (float(ts_now - last_total_ts) / 3600.0) if last_total_ts else -1.0
    event_event_share = (event_event / n_edges) if n_edges else 0.0
    non_event_share = (non_event / n_edges) if n_edges else 0.0
    with_scene_share = (with_source_scene / n_edges) if n_edges else 0.0
    missing_share = (missing_endpoints / max(1, 2 * n_edges)) if n_edges else 0.0
    avg_conf = (confidence_sum / n_edges) if n_edges else 0.0
    n_context_edges = len(context_edges)
    context_ref_share = (context_ref_edges / n_context_edges) if n_context_edges else 0.0
    context_generic_share = (context_generic_edges / n_context_edges) if n_context_edges else 0.0
    context_events_share = (len(context_events) / max(1, len(set([int(e["a_id"]) for e in edges] + [int(e["b_id"]) for e in edges])))) if edges else 0.0

    if n_edges <= 0:
        reason = "no_synaptic_edges_in_selected_window"
    elif event_event_share >= 0.98 and with_scene_share <= 0.01 and len(kind_pairs) <= 1 and len(label_prefix_pairs) <= 1:
        reason = "synapses_are_almost_exclusively_event_event_without_source_scene_context"
    elif non_event_share <= 0.01:
        reason = "synapses_have_almost_no_non_event_context"
    elif with_scene_share <= 0.01:
        reason = "synapses_have_almost_no_source_scene_context"
    else:
        reason = "synapses_have_mixed_context_sources"

    state: Dict[str, Any] = {
        "ok": True,
        "stage": "origin_measure_only",
        "last_run_ts": ts_now,
        "window_sec": int(window_sec),
        "limit_edges": int(limit_edges),
        "edges_scanned": int(n_edges),
        "unique_nodes_seen": int(len(set(node_ids))),
        "object_nodes_loaded": int(len(nodes)),
        "missing_node_endpoints": int(missing_endpoints),
        "missing_node_share": round(float(missing_share), 6),
        "event_event_edges": int(event_event),
        "event_event_share": round(float(event_event_share), 6),
        "non_event_edges": int(non_event),
        "non_event_edge_share": round(float(non_event_share), 6),
        "with_source_scene_edges": int(with_source_scene),
        "with_source_scene_share": round(float(with_scene_share), 6),
        "source_scene_distinct": int(len(source_scenes)),
        "distinct_kind_pairs": int(len(kind_pairs)),
        "distinct_label_prefix_pairs": int(len(label_prefix_pairs)),
        "notes_pattern_count": int(len(notes_patterns)),
        "interesting_notes_token_count": int(len(notes_tokens)),
        "avg_confidence": round(float(avg_conf), 6),
        "oldest_edge_ts_in_window": int(oldest_in_window),
        "oldest_edge_time_in_window": _local_time(oldest_in_window),
        "newest_edge_ts_in_window": int(newest_in_window),
        "newest_edge_time_in_window": _local_time(newest_in_window),
        "last_synaptic_ts_total": int(last_total_ts),
        "last_synaptic_time_total": _local_time(last_total_ts),
        "last_synaptic_age_hours": round(float(last_age_hours), 3) if last_age_hours >= 0 else -1.0,
        "context_edges_scanned": int(n_context_edges),
        "context_events_with_anchor": int(len(context_events)),
        "context_events_share_of_synaptic_nodes": round(float(context_events_share), 6),
        "context_distinct_anchor_nodes": int(len(context_anchor_labels)),
        "context_ref_edges": int(context_ref_edges),
        "context_episode_edges": int(context_episode_edges),
        "context_time_bucket_edges": int(context_time_bucket_edges),
        "context_neighbor_time_bucket_edges": int(context_neighbor_time_bucket_edges),
        "context_episode_sequence_bucket_edges": int(context_episode_sequence_bucket_edges),
        "context_snapchain_nearby_bucket_edges": int(context_snapchain_nearby_bucket_edges),
        "context_origin_time_bucket_edges": int(context_origin_time_bucket_edges),
        "context_scope_event_type_edges": int(context_scope_event_type_edges),
        "context_scope_edges": int(context_scope_edges),
        "context_event_type_edges": int(context_event_type_edges),
        "context_generic_edges": int(context_generic_edges),
        "context_other_edges": int(context_other_edges),
        "context_ref_share": round(float(context_ref_share), 6),
        "context_generic_share": round(float(context_generic_share), 6),
        "last_synaptic_context_ts_total": int(_fetch_last_context_ts()),
        "last_synaptic_context_time_total": _local_time(_fetch_last_context_ts()),
        "top_context_anchor_types": _top(context_anchor_types, topk),
        "top_context_anchor_labels": _top(context_anchor_labels, topk),
        "top_context_kind_pairs": _top(context_kind_pairs, topk),
        "top_context_label_prefix_pairs": _top(context_label_prefix_pairs, topk),
        "sample_context_edges": context_sample_edges,
        "top_kind_pairs": _top(kind_pairs, topk),
        "top_label_prefix_pairs": _top(label_prefix_pairs, topk),
        "top_kinds": _top(kind_counts, topk),
        "top_label_prefixes": _top(label_prefix_counts, topk),
        "top_notes_patterns": _top(notes_patterns, topk),
        "top_interesting_notes_tokens": _top(notes_tokens, topk),
        "top_source_scenes": _top(source_scenes, topk),
        "sample_edges": sample_edges,
        "diagnosis": {
            "reason": reason,
            "event_event_dominant": bool(event_event_share >= 0.98 and n_edges > 0),
            "has_non_event_context": bool(non_event_share > 0.01),
            "has_source_scene_context": bool(with_scene_share > 0.01),
            "has_interesting_notes_context": bool(len(notes_tokens) > 0),
            "has_synaptic_context_edges": bool(n_context_edges > 0),
            "has_specific_ref_context": bool(context_ref_edges > 0),
            "has_medium_context": bool((context_episode_edges + context_time_bucket_edges + context_neighbor_time_bucket_edges + context_episode_sequence_bucket_edges + context_snapchain_nearby_bucket_edges + context_origin_time_bucket_edges) > 0),
            "candidate_for_bridge_stage_b": False,
            "next_recommended_step": "extend_bridge_probe_to_context_evidence" if n_context_edges > 0 else ("inspect_or_extend_nmr_synaptic_plasticity_context" if n_edges > 0 else "wait_for_or_trigger_dream_nmr_synapses"),
        },
    }
    return state


def write_stats(state: Dict[str, Any]) -> None:
    ts = int(state.get("last_run_ts") or _now_ts())
    suffix = _suffix(int(state.get("window_sec") or 0))
    meta = {
        "window_sec": int(state.get("window_sec") or 0),
        "limit_edges": int(state.get("limit_edges") or 0),
        "stage": str(state.get("stage") or "origin_measure_only"),
    }
    values = {
        f"synapses.origin.edges_{suffix}": float(state.get("edges_scanned") or 0),
        f"synapses.origin.event_event_share_{suffix}": float(state.get("event_event_share") or 0.0),
        f"synapses.origin.non_event_edge_share_{suffix}": float(state.get("non_event_edge_share") or 0.0),
        f"synapses.origin.with_source_scene_share_{suffix}": float(state.get("with_source_scene_share") or 0.0),
        f"synapses.origin.distinct_kind_pairs_{suffix}": float(state.get("distinct_kind_pairs") or 0),
        f"synapses.origin.distinct_label_prefix_pairs_{suffix}": float(state.get("distinct_label_prefix_pairs") or 0),
        f"synapses.origin.notes_pattern_count_{suffix}": float(state.get("notes_pattern_count") or 0),
        f"synapses.origin.interesting_notes_token_count_{suffix}": float(state.get("interesting_notes_token_count") or 0),
        f"synapses.origin.source_scene_distinct_{suffix}": float(state.get("source_scene_distinct") or 0),
        f"synapses.origin.missing_node_share_{suffix}": float(state.get("missing_node_share") or 0.0),
        "synapses.origin.last_synaptic_age_hours": float(state.get("last_synaptic_age_hours") or 0.0),
        f"synapses.context.edges_{suffix}": float(state.get("context_edges_scanned") or 0),
        f"synapses.context.events_with_anchor_{suffix}": float(state.get("context_events_with_anchor") or 0),
        f"synapses.context.distinct_anchor_nodes_{suffix}": float(state.get("context_distinct_anchor_nodes") or 0),
        f"synapses.context.ref_anchor_edges_{suffix}": float(state.get("context_ref_edges") or 0),
        f"synapses.context.episode_anchor_edges_{suffix}": float(state.get("context_episode_edges") or 0),
        f"synapses.context.time_bucket_anchor_edges_{suffix}": float(state.get("context_time_bucket_edges") or 0),
        f"synapses.context.neighbor_time_bucket_anchor_edges_{suffix}": float(state.get("context_neighbor_time_bucket_edges") or 0),
        f"synapses.context.episode_sequence_bucket_anchor_edges_{suffix}": float(state.get("context_episode_sequence_bucket_edges") or 0),
        f"synapses.context.snapchain_nearby_bucket_anchor_edges_{suffix}": float(state.get("context_snapchain_nearby_bucket_edges") or 0),
        f"synapses.context.origin_time_bucket_anchor_edges_{suffix}": float(state.get("context_origin_time_bucket_edges") or 0),
        f"synapses.context.scope_event_type_anchor_edges_{suffix}": float(state.get("context_scope_event_type_edges") or 0),
        f"synapses.context.scope_anchor_edges_{suffix}": float(state.get("context_scope_edges") or 0),
        f"synapses.context.event_type_anchor_edges_{suffix}": float(state.get("context_event_type_edges") or 0),
        f"synapses.context.generic_anchor_edges_{suffix}": float(state.get("context_generic_edges") or 0),
        f"synapses.context.ref_anchor_share_{suffix}": float(state.get("context_ref_share") or 0.0),
        f"synapses.context.generic_anchor_share_{suffix}": float(state.get("context_generic_share") or 0.0),
    }
    for series, value in values.items():
        _write_stat(ts, series, value, src_uid=f"synapses_origin_probe:{ts}:{series}", meta=meta)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Synapses Origin Probe (measure-only)")
    ap.add_argument("--once", action="store_true", help="Run once and exit (kept for orchestrator consistency).")
    ap.add_argument("--window-sec", type=int, default=int(os.environ.get("OROMA_SYNAPSES_ORIGIN_WINDOW_SEC", "604800")))
    ap.add_argument("--limit-edges", type=int, default=int(os.environ.get("OROMA_SYNAPSES_ORIGIN_LIMIT_EDGES", "50000")))
    ap.add_argument("--topk", type=int, default=int(os.environ.get("OROMA_SYNAPSES_ORIGIN_TOPK", "25")))
    ap.add_argument("--verbose", action="store_true", help="Accepted for ops symmetry; JSON output is always printed.")
    args = ap.parse_args(argv)

    _dbw_required()
    state = analyze(window_sec=max(60, int(args.window_sec)), limit_edges=max(1, int(args.limit_edges)), topk=max(1, int(args.topk)))
    write_stats(state)
    _atomic_write_json(STATE_PATH, state)
    print(json.dumps(state, ensure_ascii=False, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/oroma_vertical_learning_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Vertical Learning Probe – read-only Single-Namespace-Dry-Run
# Version:   v0.1.0-readonly-dry-run
# Stand:     2026-07-05
# Autor:     ORÓMA Project
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool prüft für genau einen Namespace, ob ORÓMA den vertikalen
# Lernfluss technisch vorbereiten kann:
#
#   Game-SnapChains
#      -> core.snapchain_adapters.NormalizedSnapTrace
#      -> Dream-processable Kandidaten
#      -> hypothetischer Policy-Bezug
#
# Das Tool ist eine PROBE, kein Training. Es macht keinen Dream-Lauf, ruft den
# DreamWorker nicht produktiv auf und schreibt nichts in SQLite/DBWriter.
#
# WARUM DIESES TOOL EXISTIERT
# ──────────────────────────
# Nach dem Snake3D-Transfer-Beweis war klar: lokale Game-Policy-Loops und
# Policy-Reuse funktionieren. Nicht bewiesen ist der systemweite Rückfluss:
#
#   Spiel-Erfahrung -> SnapChain/Trace -> Dream/Replay -> Policy-Verbesserung
#
# Bevor DreamWorker an moderne Game-Traces angeschlossen wird, muss sichtbar
# werden, ob ein einzelner Namespace überhaupt:
#   - SnapChains besitzt,
#   - über den Adapter ladbar ist,
#   - echte Dream-Features/Centroids enthält,
#   - Policy-relevante state_hash/action-Links zu vorhandenen policy_rules hat.
#
# SAFETY / PRODUKTIONSINVARIANTEN
# ───────────────────────────────
# - Read-only: ausschließlich SELECTs.
# - Kein DBWriter, keine SQLite-Writes, keine Policy-Änderung.
# - Kein DreamWorker-Umbau und kein produktiver Dream-Lauf.
# - Kein PTZ / keine Motorik.
# - Für große Live-DBs kleine LIMITs und indexierte Abfragen über origin.
# - Keine globalen Volltabellen-Scans.
# - Keine stillen Fehler: skip_reasons und Warnungen werden sichtbar.
#
# BEISPIELE
# ─────────
#   PYTHONPATH=. python3 tools/oroma_vertical_learning_probe.py --namespace game:snake --limit 50 --dry-run
#   PYTHONPATH=. python3 tools/oroma_vertical_learning_probe.py --namespace game:snake3d --limit 20 --dry-run --json
#   PYTHONPATH=. python3 tools/oroma_vertical_learning_probe.py --namespace game:snake --limit 20 --show-samples
#
# =============================================================================
# END HEADER
# =============================================================================
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import sys
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
for _path in (_PROJECT_ROOT, "/opt/ai/oroma"):
    if _path and _path not in sys.path:
        sys.path.append(_path)

from core.snapchain_adapters import (  # noqa: E402
    feature_centroid_from_trace,
    normalize_snapchain_blob,
    summarize_trace,
)


# -----------------------------------------------------------------------------
# DB helpers
# -----------------------------------------------------------------------------

def _row_get(row: Any, key: str, idx: int = 0, default: Any = None) -> Any:
    try:
        if hasattr(row, "keys"):
            return row[key]
    except Exception:
        pass
    try:
        return row[idx]
    except Exception:
        return default


def _get_conn(db_path: str | None = None):
    if db_path:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    from core import sql_manager  # type: ignore
    return sql_manager.get_conn()


def _has_table(conn: Any, table: str) -> bool:
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _columns(conn: Any, table: str) -> Set[str]:
    try:
        return {str(_row_get(r, "name", 1) or "") for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


# -----------------------------------------------------------------------------
# Blob / trace helpers
# -----------------------------------------------------------------------------

def _decode_blob(blob: Any) -> Tuple[Optional[Any], str]:
    if blob is None:
        return None, "blob_null"
    if isinstance(blob, (dict, list)):
        return blob, ""
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    candidates: List[str] = []
    if isinstance(blob, bytes):
        try:
            candidates.append(blob.decode("utf-8"))
        except Exception:
            pass
        try:
            candidates.append(zlib.decompress(blob).decode("utf-8"))
        except Exception:
            pass
    else:
        candidates.append(str(blob))
    last_error = "json_decode_failed"
    for text in candidates:
        try:
            return json.loads(text), ""
        except Exception as exc:
            last_error = f"json_decode_failed:{type(exc).__name__}"
    return None, last_error


def _take_state_hash(step: Dict[str, Any]) -> str:
    for key in ("state_hash", "h", "sh"):
        value = step.get(key)
        if value not in (None, ""):
            return str(value)
    state = step.get("state")
    if isinstance(state, dict):
        for key in ("state_hash", "h", "sh"):
            value = state.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _take_action(step: Dict[str, Any]) -> str:
    for key in ("action", "a", "action_canon", "ac", "action_name"):
        value = step.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _step_has_outcome(step: Dict[str, Any]) -> bool:
    for key in ("outcome", "result", "reward"):
        value = step.get(key)
        if value not in (None, ""):
            return True
    return False


def _root_has_outcome(root: Any) -> bool:
    if not isinstance(root, dict):
        return False
    for key in ("outcome", "result", "reward"):
        value = root.get(key)
        if value not in (None, ""):
            return True
    return False


def _schema_prefix(state_hash: Any) -> str:
    s = str(state_hash or "")
    if not s:
        return ""
    # ORÓMA game state_hashes commonly start with e.g. snake3d:pro_v1:...
    parts = s.split(":")
    if len(parts) >= 2 and parts[0] and (parts[1].startswith("pro_") or parts[1].startswith("v") or parts[1] in {"pro", "reuse"}):
        return f"{parts[0]}:{parts[1]}"
    if "|" in s:
        return s.split("|", 1)[0]
    if ":" in s:
        return s.split(":", 1)[0]
    return s[:64]


def _centroid_of_centroids(vectors: Sequence[Sequence[float]]) -> List[float]:
    valid = [list(map(float, v)) for v in vectors if isinstance(v, (list, tuple)) and len(v) > 0]
    if not valid:
        return []
    dim = min(len(v) for v in valid)
    if dim <= 0:
        return []
    acc = [0.0] * dim
    n = 0
    for vec in valid:
        for i in range(dim):
            x = float(vec[i])
            if math.isfinite(x):
                acc[i] += x
        n += 1
    return [x / n for x in acc] if n > 0 else []


# -----------------------------------------------------------------------------
# Fetch helpers – intentionally LIMIT based and namespace/origin filtered.
# -----------------------------------------------------------------------------

def _fetch_policy_snapshot(conn: Any, namespace: str) -> Dict[str, Any]:
    if not _has_table(conn, "policy_rules"):
        return {"ok": False, "reason": "missing_table:policy_rules"}
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS rules,
                   COALESCE(SUM(n),0) AS samples,
                   COALESCE(SUM(pos),0) AS pos,
                   COALESCE(SUM(neg),0) AS neg,
                   COALESCE(SUM(draw),0) AS draw,
                   AVG(q) AS q_avg,
                   MIN(q) AS q_min,
                   MAX(q) AS q_max,
                   MAX(id) AS max_id,
                   MAX(last_ts) AS max_last_ts
              FROM policy_rules
             WHERE namespace = ?
            """,
            (namespace,),
        ).fetchone()
        out = {
            "ok": True,
            "namespace": namespace,
            "rules": int(_row_get(row, "rules", 0, 0) or 0),
            "samples": int(_row_get(row, "samples", 1, 0) or 0),
            "pos": int(_row_get(row, "pos", 2, 0) or 0),
            "neg": int(_row_get(row, "neg", 3, 0) or 0),
            "draw": int(_row_get(row, "draw", 4, 0) or 0),
            "q_avg": _round_or_none(_row_get(row, "q_avg", 5)),
            "q_min": _round_or_none(_row_get(row, "q_min", 6)),
            "q_max": _round_or_none(_row_get(row, "q_max", 7)),
            "max_id": _row_get(row, "max_id", 8),
            "max_last_ts": _row_get(row, "max_last_ts", 9),
        }
        return out
    except Exception as exc:
        return {"ok": False, "reason": f"policy_snapshot_failed:{type(exc).__name__}:{exc}"}


def _fetch_policy_prefixes(conn: Any, namespace: str, *, limit: int) -> Dict[str, int]:
    if not _has_table(conn, "policy_rules"):
        return {}
    rows = []
    try:
        rows = conn.execute(
            """
            SELECT state_hash
              FROM policy_rules
             WHERE namespace = ?
          ORDER BY id DESC
             LIMIT ?
            """,
            (namespace, int(max(0, limit))),
        ).fetchall() or []
    except Exception:
        return {}
    c = Counter(_schema_prefix(_row_get(r, "state_hash", 0)) for r in rows)
    c.pop("", None)
    return dict(c.most_common(10))


def _fetch_snapchain_rows(
    conn: Any,
    *,
    namespace: str,
    limit: int,
    since_id: Optional[int],
    include_namespace_scan: bool = False,
) -> List[Any]:
    if not _has_table(conn, "snapchains"):
        return []
    params: List[Any] = [namespace]
    where = "origin = ?"
    if since_id is not None:
        where += " AND id > ?"
        params.append(int(since_id))
    params.append(int(max(0, limit)))
    rows = list(conn.execute(
        f"""
        SELECT id, ts, origin, namespace, status, notes, version, source_id, blob
          FROM snapchains
         WHERE {where}
      ORDER BY id DESC
         LIMIT ?
        """,
        tuple(params),
    ).fetchall() or [])

    if include_namespace_scan and len(rows) < int(limit):
        seen = {str(_row_get(r, "id", 0)) for r in rows}
        params2: List[Any] = [namespace]
        where2 = "namespace = ?"
        if since_id is not None:
            where2 += " AND id > ?"
            params2.append(int(since_id))
        params2.append(int(max(0, limit - len(rows))))
        more = list(conn.execute(
            f"""
            SELECT id, ts, origin, namespace, status, notes, version, source_id, blob
              FROM snapchains
             WHERE {where2}
          ORDER BY id DESC
             LIMIT ?
            """,
            tuple(params2),
        ).fetchall() or [])
        for row in more:
            rid = str(_row_get(row, "id", 0))
            if rid not in seen:
                rows.append(row)
                seen.add(rid)
    return rows


def _fetch_policy_overlap(
    conn: Any,
    *,
    namespace: str,
    state_actions: Set[Tuple[str, str]],
    batch_size: int = 200,
) -> Dict[str, Any]:
    if not _has_table(conn, "policy_rules"):
        return {"ok": False, "reason": "missing_table:policy_rules"}
    if not state_actions:
        return {
            "ok": True,
            "trace_state_actions": 0,
            "existing_state_actions": 0,
            "new_state_actions": 0,
            "existing_state_hashes": 0,
            "new_state_hashes": 0,
            "action_links": 0,
            "matched_rule_samples": 0,
            "matched_rule_q_avg": None,
        }

    states = sorted({sa[0] for sa in state_actions if sa[0]})
    existing_actions: Set[Tuple[str, str]] = set()
    existing_states: Set[str] = set()
    sample_sum = 0
    q_values: List[float] = []
    for i in range(0, len(states), max(1, int(batch_size))):
        chunk = states[i:i + max(1, int(batch_size))]
        placeholders = ",".join("?" for _ in chunk)
        try:
            rows = conn.execute(
                f"""
                SELECT state_hash, action, n, q
                  FROM policy_rules
                 WHERE namespace = ?
                   AND state_hash IN ({placeholders})
                """,
                tuple([namespace] + chunk),
            ).fetchall() or []
        except Exception as exc:
            return {"ok": False, "reason": f"policy_overlap_failed:{type(exc).__name__}:{exc}"}
        for row in rows:
            sh = str(_row_get(row, "state_hash", 0) or "")
            ac = str(_row_get(row, "action", 1) or "")
            if sh:
                existing_states.add(sh)
            if sh and ac:
                pair = (sh, ac)
                if pair in state_actions:
                    existing_actions.add(pair)
                    try:
                        sample_sum += int(_row_get(row, "n", 2) or 0)
                    except Exception:
                        pass
                    try:
                        q = float(_row_get(row, "q", 3) or 0.0)
                        if math.isfinite(q):
                            q_values.append(q)
                    except Exception:
                        pass
    trace_states = {sa[0] for sa in state_actions if sa[0]}
    return {
        "ok": True,
        "trace_state_actions": len(state_actions),
        "existing_state_actions": len(existing_actions),
        "new_state_actions": max(0, len(state_actions) - len(existing_actions)),
        "existing_state_hashes": len(existing_states.intersection(trace_states)),
        "new_state_hashes": max(0, len(trace_states) - len(existing_states.intersection(trace_states))),
        "action_links": len(existing_actions),
        "matched_rule_samples": sample_sum,
        "matched_rule_q_avg": _round_or_none(sum(q_values) / len(q_values) if q_values else None),
    }


def _round_or_none(value: Any, digits: int = 4) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        if not math.isfinite(f):
            return None
        return round(f, digits)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Probe core
# -----------------------------------------------------------------------------

def run_probe(
    conn: Any,
    *,
    namespace: str,
    limit: int,
    since_id: Optional[int],
    min_events: int,
    state_sample_limit: int,
    include_namespace_scan: bool,
    show_samples: bool,
) -> Dict[str, Any]:
    policy = _fetch_policy_snapshot(conn, namespace)
    if policy.get("ok"):
        policy["state_schema_prefixes_recent"] = _fetch_policy_prefixes(conn, namespace, limit=state_sample_limit)

    rows = _fetch_snapchain_rows(
        conn,
        namespace=namespace,
        limit=limit,
        since_id=since_id,
        include_namespace_scan=include_namespace_scan,
    )

    formats: Counter[str] = Counter()
    skips: Counter[str] = Counter()
    state_schemas: Counter[str] = Counter()
    action_schemas: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    adapter_ok = policy_trainable = dream_processable = 0
    input_events = input_features = 0
    candidate_count = candidate_events = candidate_features = 0
    centroids: List[List[float]] = []
    source_ids: List[Any] = []
    latest_ids: List[Any] = []

    total_steps = 0
    steps_state_action = 0
    steps_direct_outcome = 0
    steps_root_outcome = 0
    steps_state_action_outcome = 0
    state_actions: Set[Tuple[str, str]] = set()
    unique_states: Set[str] = set()
    samples: List[Dict[str, Any]] = []

    for row in rows:
        rid = _row_get(row, "id", 0)
        latest_ids.append(rid)
        origin = str(_row_get(row, "origin", 2) or "")
        ns = str(_row_get(row, "namespace", 3) or namespace or "")
        blob = _row_get(row, "blob", 8)
        root, root_err = _decode_blob(blob)
        trace = normalize_snapchain_blob(blob, origin=origin, namespace=ns, source_id=_row_get(row, "source_id", 7) or rid)
        td = summarize_trace(trace)
        fmt = str(td.get("source_format") or "unknown")
        formats[fmt] += 1
        if td.get("ok"):
            adapter_ok += 1
        if td.get("policy_trainable"):
            policy_trainable += 1
        if td.get("dream_processable"):
            dream_processable += 1
        sr = str(td.get("skip_reason") or "")
        if sr:
            skips[sr] += 1
        if trace.state_schema:
            state_schemas[trace.state_schema] += 1
        if trace.action_schema:
            action_schemas[trace.action_schema] += 1
        if trace.mode:
            modes[trace.mode] += 1
        version = str(_row_get(row, "version", 6) or "")
        if version:
            versions[version] += 1
        input_events += int(trace.event_count or 0)
        input_features += int(trace.feature_count or 0)
        root_outcome = _root_has_outcome(root)
        if root_outcome:
            steps_root_outcome += len(trace.steps or [])
        for step in trace.steps or []:
            if not isinstance(step, dict):
                continue
            total_steps += 1
            sh = _take_state_hash(step)
            ac = _take_action(step)
            direct_out = _step_has_outcome(step)
            if direct_out:
                steps_direct_outcome += 1
            if sh:
                unique_states.add(sh)
            if sh and ac:
                steps_state_action += 1
                state_actions.add((sh, ac))
            if sh and ac and (direct_out or root_outcome):
                steps_state_action_outcome += 1
        processable = bool(trace.dream_processable and trace.event_count >= max(0, int(min_events)))
        if processable:
            candidate_count += 1
            candidate_events += int(trace.event_count or 0)
            candidate_features += int(trace.feature_count or 0)
            centroid = feature_centroid_from_trace(trace)
            if centroid:
                centroids.append(centroid)
            if trace.source_id not in (None, ""):
                source_ids.append(trace.source_id)
        if show_samples and len(samples) < 5:
            samples.append({
                "id": rid,
                "origin": origin,
                "namespace": ns,
                "notes": _row_get(row, "notes", 5),
                "version": version,
                **td,
            })

    centroid = _centroid_of_centroids(centroids)
    overlap = _fetch_policy_overlap(conn, namespace=namespace, state_actions=state_actions)
    would_process = bool(candidate_count > 0 and candidate_events > 0 and len(centroid) > 0)
    if would_process:
        reason = "dream_processable_traces_with_centroid"
    elif not rows:
        reason = "no_snapchains_for_namespace_origin"
    elif candidate_count <= 0:
        reason = "no_dream_processable_candidates"
    elif not centroid:
        reason = "no_feature_centroid"
    else:
        reason = "unknown"

    result: Dict[str, Any] = {
        "tool": "oroma_vertical_learning_probe",
        "read_only": True,
        "dry_run": True,
        "namespace": namespace,
        "limit": int(limit),
        "since_id": since_id,
        "include_namespace_scan": bool(include_namespace_scan),
        "policy_before": policy,
        "trace_input": {
            "snapchains": len(rows),
            "latest_ids": latest_ids[:10],
            "formats": dict(formats.most_common()),
            "adapter_ok": adapter_ok,
            "policy_trainable": policy_trainable,
            "dream_processable": dream_processable,
            "skipped": sum(skips.values()),
            "skip_reasons": dict(skips.most_common()),
            "input_events": input_events,
            "input_features": input_features,
            "state_schema_distribution": dict(state_schemas.most_common()),
            "action_schema_distribution": dict(action_schemas.most_common()),
            "mode_distribution": dict(modes.most_common()),
            "version_distribution": dict(versions.most_common(8)),
        },
        "would_dream_process": {
            "yes": would_process,
            "reason": reason,
            "input_traces": candidate_count,
            "input_events": candidate_events,
            "input_features": candidate_features,
            "centroid_dim": len(centroid),
            "centroid_preview": [round(float(x), 6) for x in centroid[:12]],
            "source_ids": source_ids[:20],
        },
        "policy_overlap": {
            "total_steps": total_steps,
            "steps_with_state_action": steps_state_action,
            "steps_with_direct_outcome": steps_direct_outcome,
            "steps_with_root_outcome_available": steps_root_outcome,
            "steps_with_state_action_and_outcome": steps_state_action_outcome,
            "unique_trace_state_hashes": len(unique_states),
            **overlap,
        },
        "samples": samples if show_samples else [],
    }
    return result


# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------

def _print_human(result: Dict[str, Any]) -> None:
    print("ORÓMA Vertical Learning Probe (read-only)")
    print(f"namespace={result.get('namespace')}")
    print("dry_run=true")
    print("writes=0")
    print("")

    p = result.get("policy_before") or {}
    print("Policy before:")
    if p.get("ok"):
        print(f"  rules={p.get('rules')} samples={p.get('samples')} pos={p.get('pos')} neg={p.get('neg')} draw={p.get('draw')}")
        print(f"  q_avg/min/max={p.get('q_avg')}/{p.get('q_min')}/{p.get('q_max')} max_id={p.get('max_id')} max_last_ts={p.get('max_last_ts')}")
        print(f"  state_schema_prefixes_recent={p.get('state_schema_prefixes_recent') or {}}")
    else:
        print(f"  ok=false reason={p.get('reason')}")
    print("")

    t = result.get("trace_input") or {}
    print("Trace input:")
    print(f"  snapchains={t.get('snapchains')} latest_ids={t.get('latest_ids')}")
    print(f"  formats={t.get('formats')}")
    print(f"  adapter_ok={t.get('adapter_ok')} policy_trainable={t.get('policy_trainable')} dream_processable={t.get('dream_processable')} skipped={t.get('skipped')}")
    print(f"  input_events={t.get('input_events')} input_features={t.get('input_features')}")
    print(f"  state_schema={t.get('state_schema_distribution')}")
    print(f"  action_schema={t.get('action_schema_distribution')}")
    print(f"  mode={t.get('mode_distribution')}")
    if t.get("skip_reasons"):
        print(f"  skip_reasons={t.get('skip_reasons')}")
    print("")

    w = result.get("would_dream_process") or {}
    print("Would Dream process:")
    print(f"  yes={w.get('yes')} reason={w.get('reason')}")
    print(f"  input_traces={w.get('input_traces')} input_events={w.get('input_events')} input_features={w.get('input_features')} centroid_dim={w.get('centroid_dim')}")
    if w.get("centroid_preview"):
        print(f"  centroid_preview={w.get('centroid_preview')}")
    print("")

    o = result.get("policy_overlap") or {}
    print("Policy overlap:")
    print(f"  total_steps={o.get('total_steps')} state_action_steps={o.get('steps_with_state_action')} state_action_outcome_steps={o.get('steps_with_state_action_and_outcome')}")
    print(f"  root_outcome_steps={o.get('steps_with_root_outcome_available')} direct_outcome_steps={o.get('steps_with_direct_outcome')}")
    print(f"  unique_trace_state_hashes={o.get('unique_trace_state_hashes')}")
    print(f"  existing_state_hashes={o.get('existing_state_hashes')} new_state_hashes={o.get('new_state_hashes')}")
    print(f"  existing_state_actions={o.get('existing_state_actions')} new_state_actions={o.get('new_state_actions')} action_links={o.get('action_links')}")
    print(f"  matched_rule_samples={o.get('matched_rule_samples')} matched_rule_q_avg={o.get('matched_rule_q_avg')}")

    samples = result.get("samples") or []
    if samples:
        print("")
        print("Samples:")
        for s in samples:
            print(json.dumps(s, ensure_ascii=False, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Vertical Learning Probe für einen ORÓMA-Namespace.")
    ap.add_argument("--db", default="", help="Optionaler Pfad zu oroma.db; sonst core.sql_manager.get_conn().")
    ap.add_argument("--namespace", required=True, help="Namespace, z.B. game:snake oder game:snake3d.")
    ap.add_argument("--limit", type=int, default=50, help="Maximale SnapChains via origin=<namespace> (Default: 50).")
    ap.add_argument("--since-id", type=int, default=None, help="Nur SnapChains mit id > since-id.")
    ap.add_argument("--min-events", type=int, default=1, help="Mindest-Events pro Dream-Kandidat.")
    ap.add_argument("--state-sample-limit", type=int, default=2000, help="Limit für Policy-State-Prefix-Sampling.")
    ap.add_argument("--include-namespace-scan", action="store_true", help="Zusätzlich namespace=<namespace> scannen; Default aus DB-Last-Gründen aus.")
    ap.add_argument("--show-samples", action="store_true", help="Bis zu 5 Beispiel-Traces ausgeben.")
    ap.add_argument("--json", action="store_true", help="JSON-Ausgabe statt Human-Report.")
    ap.add_argument("--dry-run", action="store_true", help="Expliziter Dry-Run-Schalter; Tool ist immer read-only.")
    args = ap.parse_args()

    namespace = str(args.namespace or "").strip()
    if not namespace:
        print("ERROR: --namespace darf nicht leer sein", file=sys.stderr)
        return 2
    if int(args.limit) < 0:
        print("ERROR: --limit muss >= 0 sein", file=sys.stderr)
        return 2

    try:
        conn = _get_conn(args.db or None)
    except Exception as exc:
        print(f"ERROR: DB open failed: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_probe(
            conn,
            namespace=namespace,
            limit=int(args.limit),
            since_id=args.since_id,
            min_events=int(args.min_events),
            state_sample_limit=int(args.state_sample_limit),
            include_namespace_scan=bool(args.include_namespace_scan),
            show_samples=bool(args.show_samples),
        )
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

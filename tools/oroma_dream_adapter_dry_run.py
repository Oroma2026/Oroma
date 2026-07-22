#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/oroma_dream_adapter_dry_run.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Dream Adapter Dry-Run – read-only Single-Namespace-Konsolidierungsprobe
# Version:   v0.1.0-readonly-adapter-dry-run
# Stand:     2026-07-05
# Autor:     ORÓMA Project
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool ist der kontrollierte Zwischenschritt zwischen dem bereits
# vorhandenen SnapChain-Adapter und einem späteren produktiven DreamWorker-
# Anschluss. Es prüft für genau einen Namespace und genau ein State-Schema,
# ob moderne Game-Traces über `core.snapchain_adapters` dream-processable sind
# und ob ihre state_hash/action-Paare einen belastbaren Bezug zu vorhandenen
# `policy_rules` besitzen.
#
# WICHTIG: PROBE, NICHT TRAINING
# ──────────────────────────────
# Das Tool führt keinen produktiven DreamWorker-Lauf aus. Es schreibt nichts.
# Es verschiebt keine Checkpoints, erzeugt keine MetaChains, keine SnapChains,
# keine policy_rules und führt kein Forgetting aus. Es berechnet nur read-only,
# was ein adapterbasierter Dream-Pfad für diesen Namespace verarbeiten KÖNNTE.
#
# WARUM STATE-SCHEMA-FILTER?
# ─────────────────────────
# Ältere Game-Historie kann alte State-Schemata und alte, negative Policies
# enthalten. Der erste vertikale Proof darf dadurch nicht verfälscht werden.
# Deshalb filtert dieses Tool policy_rules per:
#
#   namespace = <namespace> AND state_hash LIKE '<state_schema_prefix>%'
#
# Für SnapChains wird NICHT angenommen, dass die Tabelle eine state_schema-Spalte
# besitzt. Der Filter erfolgt bewusst nach Adapter-Normalisierung aus dem Blob:
#
#   normalize_snapchain_blob(...).state_schema == <state_schema_prefix>
#
# Damit bleibt das Tool robust gegenüber historischer Tabellenstruktur.
#
# SAFE-TO-CONNECT-KRITERIEN
# ────────────────────────
# `safe_to_connect_next` ist absichtlich hart und nicht subjektiv:
#   - mindestens ein dream_processable Kandidat,
#   - outcome_source ist nicht `missing`,
#   - matched_rule_q_avg > 0,
#   - keine Decode-Errors in den ausgewählten Schema-Traces.
#
# PRODUKTIONSINVARIANTEN
# ─────────────────────
# - Read-only: nur SELECTs, kein DBWriter, keine SQLite-Writes.
# - Headless: keine GUI/Qt/Wayland/X11-Abhängigkeiten.
# - Für große Live-DBs kleine LIMITs und origin=<namespace> Abfragen.
# - Keine globalen Volltabellen-Scans.
# - Keine PTZ-/Motorik-Änderungen.
# - Sichtbare Skip-Reasons statt stiller Fehler.
#
# BEISPIELE
# ─────────
#   PYTHONPATH=. python3 tools/oroma_dream_adapter_dry_run.py \
#       --namespace game:snake --state-schema-prefix snake:pro_v2 --limit 20 --dry-run
#
#   PYTHONPATH=. python3 tools/oroma_dream_adapter_dry_run.py \
#       --namespace game:snake3d --state-schema-prefix snake3d:pro_v1 --limit 20 --dry-run --json
#
# =============================================================================
# END HEADER
# =============================================================================
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sqlite3
import sys
import zlib
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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
    meta = root.get("meta")
    if isinstance(meta, dict):
        for key in ("outcome", "result", "reward"):
            value = meta.get(key)
            if value not in (None, ""):
                return True
    return False


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
        if len(vec) < dim:
            continue
        for i in range(dim):
            x = float(vec[i])
            if math.isfinite(x):
                acc[i] += x
        n += 1
    return [x / n for x in acc] if n > 0 else []


def _outcome_source(direct_steps: int, root_steps: int, missing_steps: int) -> str:
    if direct_steps > 0 and root_steps > 0:
        return "mixed"
    if direct_steps > 0:
        return "direct"
    if root_steps > 0:
        return "root"
    if missing_steps > 0:
        return "missing"
    return "missing"


# -----------------------------------------------------------------------------
# Fetch helpers – intentionally LIMIT based and namespace/origin filtered.
# -----------------------------------------------------------------------------

def _fetch_policy_snapshot(conn: Any, namespace: str, state_schema_prefix: str) -> Dict[str, Any]:
    if not _has_table(conn, "policy_rules"):
        return {"ok": False, "reason": "missing_table:policy_rules"}
    like = f"{state_schema_prefix}%"
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
               AND state_hash LIKE ?
            """,
            (namespace, like),
        ).fetchone()
        return {
            "ok": True,
            "namespace": namespace,
            "state_schema_prefix": state_schema_prefix,
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
    except Exception as exc:
        return {"ok": False, "reason": f"policy_snapshot_failed:{type(exc).__name__}:{exc}"}


def _fetch_snapchain_rows(
    conn: Any,
    *,
    namespace: str,
    limit: int,
    since_id: Optional[int],
    include_namespace_scan: bool,
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
    state_schema_prefix: str,
    state_actions: Set[Tuple[str, str]],
    batch_size: int = 200,
) -> Dict[str, Any]:
    if not _has_table(conn, "policy_rules"):
        return {"ok": False, "reason": "missing_table:policy_rules"}
    state_actions = {(s, a) for s, a in state_actions if s.startswith(state_schema_prefix)}
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
                   AND state_hash LIKE ?
                   AND state_hash IN ({placeholders})
                """,
                tuple([namespace, f"{state_schema_prefix}%"] + chunk),
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
    existing_state_count = len(existing_states.intersection(trace_states))
    return {
        "ok": True,
        "trace_state_actions": len(state_actions),
        "existing_state_actions": len(existing_actions),
        "new_state_actions": max(0, len(state_actions) - len(existing_actions)),
        "existing_state_hashes": existing_state_count,
        "new_state_hashes": max(0, len(trace_states) - existing_state_count),
        "action_links": len(existing_actions),
        "matched_rule_samples": sample_sum,
        "matched_rule_q_avg": _round_or_none(sum(q_values) / len(q_values) if q_values else None),
    }


# -----------------------------------------------------------------------------
# Dry-run core
# -----------------------------------------------------------------------------

def run_dry_run(
    conn: Any,
    *,
    namespace: str,
    state_schema_prefix: str,
    limit: int,
    since_id: Optional[int],
    min_events: int,
    include_namespace_scan: bool,
    show_samples: bool,
) -> Dict[str, Any]:
    policy = _fetch_policy_snapshot(conn, namespace, state_schema_prefix)
    rows = _fetch_snapchain_rows(
        conn,
        namespace=namespace,
        limit=limit,
        since_id=since_id,
        include_namespace_scan=include_namespace_scan,
    )

    formats_all: Counter[str] = Counter()
    skips_all: Counter[str] = Counter()
    selected_formats: Counter[str] = Counter()
    selected_skips: Counter[str] = Counter()
    state_schemas: Counter[str] = Counter()
    action_schemas: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    versions: Counter[str] = Counter()

    latest_ids: List[Any] = []
    selected_ids: List[Any] = []
    adapter_ok = dream_processable = policy_trainable = 0
    selected_rows = 0
    schema_mismatch = 0
    decode_errors_selected = 0
    decode_errors_all = 0

    input_events = input_features = 0
    candidate_count = candidate_events = candidate_features = 0
    centroids: List[List[float]] = []
    source_ids: List[Any] = []

    total_steps = 0
    steps_state_action = 0
    steps_direct_outcome = 0
    steps_root_outcome = 0
    steps_missing_outcome = 0
    steps_state_action_outcome = 0
    positive_outcome_roots = 0
    negative_outcome_roots = 0
    zero_outcome_roots = 0
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
        summary = summarize_trace(trace)
        fmt = str(summary.get("source_format") or "unknown")
        formats_all[fmt] += 1
        if fmt == "decode_error" or str(summary.get("skip_reason") or "").startswith("json_decode_failed"):
            decode_errors_all += 1
        skip_reason = str(summary.get("skip_reason") or root_err or "")
        if skip_reason:
            skips_all[skip_reason] += 1

        # IMPORTANT: State-schema filtering happens after adapter normalization from the blob.
        if str(trace.state_schema or "") != state_schema_prefix:
            schema_mismatch += 1
            continue

        selected_rows += 1
        selected_ids.append(rid)
        selected_formats[fmt] += 1
        if skip_reason:
            selected_skips[skip_reason] += 1
        if fmt == "decode_error" or skip_reason.startswith("json_decode_failed"):
            decode_errors_selected += 1
        if summary.get("ok"):
            adapter_ok += 1
        if summary.get("policy_trainable"):
            policy_trainable += 1
        if summary.get("dream_processable"):
            dream_processable += 1
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
        root_has_outcome = _root_has_outcome(root)
        root_numeric = None
        if isinstance(root, dict):
            for key in ("outcome", "result", "reward"):
                try:
                    if root.get(key) not in (None, ""):
                        root_numeric = float(root.get(key))
                        break
                except Exception:
                    pass
            if root_numeric is None and isinstance(root.get("meta"), dict):
                for key in ("outcome", "result", "reward"):
                    try:
                        if root["meta"].get(key) not in (None, ""):
                            root_numeric = float(root["meta"].get(key))
                            break
                    except Exception:
                        pass
        if root_numeric is not None:
            if root_numeric > 1e-9:
                positive_outcome_roots += 1
            elif root_numeric < -1e-9:
                negative_outcome_roots += 1
            else:
                zero_outcome_roots += 1

        for step in trace.steps or []:
            if not isinstance(step, dict):
                continue
            total_steps += 1
            sh = _take_state_hash(step)
            ac = _take_action(step)
            if sh and not sh.startswith(state_schema_prefix):
                # Keep schema guard strict for policy-overlap evidence.
                continue
            direct_out = _step_has_outcome(step)
            if direct_out:
                steps_direct_outcome += 1
            elif root_has_outcome:
                steps_root_outcome += 1
            else:
                steps_missing_outcome += 1
            if sh:
                unique_states.add(sh)
            if sh and ac:
                steps_state_action += 1
                state_actions.add((sh, ac))
            if sh and ac and (direct_out or root_has_outcome):
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
                **summary,
            })

    centroid = _centroid_of_centroids(centroids)
    overlap = _fetch_policy_overlap(
        conn,
        namespace=namespace,
        state_schema_prefix=state_schema_prefix,
        state_actions=state_actions,
    )
    outcome_src = _outcome_source(steps_direct_outcome, steps_root_outcome, steps_missing_outcome)
    would_consolidate = bool(candidate_count > 0 and candidate_events > 0 and len(centroid) > 0 and outcome_src != "missing")
    if would_consolidate:
        reason = "dream_processable_schema_traces_with_centroid_and_outcome"
    elif selected_rows <= 0:
        reason = "no_snapchains_matching_state_schema_after_adapter_filter"
    elif candidate_count <= 0:
        reason = "no_dream_processable_candidates"
    elif not centroid:
        reason = "no_feature_centroid"
    elif outcome_src == "missing":
        reason = "missing_outcome_semantics"
    else:
        reason = "unknown"

    q_avg = overlap.get("matched_rule_q_avg") if isinstance(overlap, dict) else None
    safe_to_connect_next = bool(
        dream_processable > 0
        and outcome_src != "missing"
        and q_avg is not None
        and float(q_avg) > 0.0
        and decode_errors_selected == 0
    )

    result: Dict[str, Any] = {
        "tool": "oroma_dream_adapter_dry_run",
        "read_only": True,
        "dry_run": True,
        "writes": 0,
        "namespace": namespace,
        "state_schema_prefix": state_schema_prefix,
        "limit": int(limit),
        "since_id": since_id,
        "include_namespace_scan": bool(include_namespace_scan),
        "filter_path": "snapchains filtered after adapter normalization: trace.state_schema == state_schema_prefix; policy_rules filtered by state_hash LIKE prefix%",
        "policy_before": policy,
        "trace_input": {
            "snapchains_scanned": len(rows),
            "schema_matching_snapchains": selected_rows,
            "schema_mismatch": schema_mismatch,
            "latest_ids": latest_ids[:10],
            "selected_ids": selected_ids[:10],
            "formats_all": dict(formats_all.most_common()),
            "formats_selected": dict(selected_formats.most_common()),
            "adapter_ok": adapter_ok,
            "policy_trainable": policy_trainable,
            "dream_processable": dream_processable,
            "skipped_selected": sum(selected_skips.values()),
            "skip_reasons_selected": dict(selected_skips.most_common()),
            "skip_reasons_all": dict(skips_all.most_common(8)),
            "decode_errors_all": decode_errors_all,
            "decode_errors_selected": decode_errors_selected,
            "input_events": input_events,
            "input_features": input_features,
            "state_schema_distribution": dict(state_schemas.most_common()),
            "action_schema_distribution": dict(action_schemas.most_common()),
            "mode_distribution": dict(modes.most_common()),
            "version_distribution": dict(versions.most_common(8)),
        },
        "outcome_semantics": {
            "direct_outcome_steps": steps_direct_outcome,
            "root_outcome_steps": steps_root_outcome,
            "missing_outcome_steps": steps_missing_outcome,
            "state_action_outcome_steps": steps_state_action_outcome,
            "outcome_source": outcome_src,
            "positive_outcome_roots": positive_outcome_roots,
            "negative_outcome_roots": negative_outcome_roots,
            "zero_outcome_roots": zero_outcome_roots,
        },
        "hypothetical_consolidation": {
            "would_consolidate": would_consolidate,
            "reason": reason,
            "candidate_count": candidate_count,
            "input_events": candidate_events,
            "input_features": candidate_features,
            "centroid_dim": len(centroid),
            "centroid_preview": [round(float(x), 6) for x in centroid[:12]],
            "state_schema_distribution": dict(state_schemas.most_common()),
            "action_schema_distribution": dict(action_schemas.most_common()),
            "mode_distribution": dict(modes.most_common()),
            "source_ids": source_ids[:20],
            "safe_to_connect_next": safe_to_connect_next,
            "safe_to_connect_criteria": {
                "dream_processable_gt_0": dream_processable > 0,
                "outcome_source_not_missing": outcome_src != "missing",
                "matched_rule_q_avg_gt_0": bool(q_avg is not None and float(q_avg) > 0.0),
                "no_decode_errors_in_selected_traces": decode_errors_selected == 0,
            },
        },
        "policy_overlap": {
            "total_steps": total_steps,
            "steps_with_state_action": steps_state_action,
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
    print("ORÓMA Dream Adapter Dry-Run (read-only)")
    print(f"namespace={result.get('namespace')}")
    print(f"state_schema_prefix={result.get('state_schema_prefix')}")
    print("dry_run=true")
    print("writes=0")
    print(f"filter_path={result.get('filter_path')}")
    print("")

    p = result.get("policy_before") or {}
    print("Policy before:")
    if p.get("ok"):
        print(f"  rules={p.get('rules')} samples={p.get('samples')} pos={p.get('pos')} neg={p.get('neg')} draw={p.get('draw')}")
        print(f"  q_avg/min/max={p.get('q_avg')}/{p.get('q_min')}/{p.get('q_max')} max_id={p.get('max_id')} max_last_ts={p.get('max_last_ts')}")
    else:
        print(f"  ok=false reason={p.get('reason')}")
    print("")

    t = result.get("trace_input") or {}
    print("Trace input:")
    print(f"  snapchains_scanned={t.get('snapchains_scanned')} schema_matching={t.get('schema_matching_snapchains')} schema_mismatch={t.get('schema_mismatch')}")
    print(f"  latest_ids={t.get('latest_ids')}")
    print(f"  selected_ids={t.get('selected_ids')}")
    print(f"  formats_selected={t.get('formats_selected')} formats_all={t.get('formats_all')}")
    print(f"  adapter_ok={t.get('adapter_ok')} policy_trainable={t.get('policy_trainable')} dream_processable={t.get('dream_processable')} skipped_selected={t.get('skipped_selected')}")
    print(f"  decode_errors_selected={t.get('decode_errors_selected')} decode_errors_all={t.get('decode_errors_all')}")
    print(f"  input_events={t.get('input_events')} input_features={t.get('input_features')}")
    print(f"  state_schema={t.get('state_schema_distribution')}")
    print(f"  action_schema={t.get('action_schema_distribution')}")
    print(f"  mode={t.get('mode_distribution')}")
    if t.get("skip_reasons_selected"):
        print(f"  skip_reasons_selected={t.get('skip_reasons_selected')}")
    print("")

    o = result.get("outcome_semantics") or {}
    print("Outcome semantics:")
    print(f"  direct_outcome_steps={o.get('direct_outcome_steps')}")
    print(f"  root_outcome_steps={o.get('root_outcome_steps')}")
    print(f"  missing_outcome_steps={o.get('missing_outcome_steps')}")
    print(f"  state_action_outcome_steps={o.get('state_action_outcome_steps')}")
    print(f"  outcome_source={o.get('outcome_source')}")
    print("")

    po = result.get("policy_overlap") or {}
    print("Policy overlap:")
    print(f"  total_steps={po.get('total_steps')} state_action_steps={po.get('steps_with_state_action')} state_action_outcome_steps={po.get('steps_with_state_action_and_outcome')}")
    print(f"  unique_trace_state_hashes={po.get('unique_trace_state_hashes')}")
    print(f"  existing_state_hashes={po.get('existing_state_hashes')} new_state_hashes={po.get('new_state_hashes')}")
    print(f"  existing_state_actions={po.get('existing_state_actions')} new_state_actions={po.get('new_state_actions')} action_links={po.get('action_links')}")
    print(f"  matched_rule_samples={po.get('matched_rule_samples')} matched_rule_q_avg={po.get('matched_rule_q_avg')}")
    print("")

    h = result.get("hypothetical_consolidation") or {}
    print("Would Dream consolidate:")
    print(f"  yes={h.get('would_consolidate')} reason={h.get('reason')}")
    print(f"  candidate_count={h.get('candidate_count')} input_events={h.get('input_events')} input_features={h.get('input_features')} centroid_dim={h.get('centroid_dim')}")
    if h.get("centroid_preview"):
        print(f"  centroid_preview={h.get('centroid_preview')}")
    print(f"  safe_to_connect_next={h.get('safe_to_connect_next')}")
    print(f"  safe_to_connect_criteria={h.get('safe_to_connect_criteria')}")

    samples = result.get("samples") or []
    if samples:
        print("")
        print("Samples:")
        for s in samples:
            print(json.dumps(s, ensure_ascii=False, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Dream Adapter Dry-Run für einen ORÓMA-Game-Namespace.")
    ap.add_argument("--db", default="", help="Optionaler Pfad zu oroma.db; sonst core.sql_manager.get_conn().")
    ap.add_argument("--namespace", required=True, help="Namespace, z.B. game:snake oder game:snake3d.")
    ap.add_argument("--state-schema-prefix", required=True, help="Schema-Filter, z.B. snake:pro_v2 oder snake3d:pro_v1.")
    ap.add_argument("--limit", type=int, default=50, help="Maximale SnapChains via origin=<namespace> (Default: 50).")
    ap.add_argument("--since-id", type=int, default=None, help="Nur SnapChains mit id > since-id.")
    ap.add_argument("--min-events", type=int, default=1, help="Mindest-Events pro Dream-Kandidat.")
    ap.add_argument("--include-namespace-scan", action="store_true", help="Zusätzlich namespace=<namespace> scannen; Default aus DB-Last-Gründen aus.")
    ap.add_argument("--show-samples", action="store_true", help="Bis zu 5 ausgewählte Beispiel-Traces ausgeben.")
    ap.add_argument("--json", action="store_true", help="JSON-Ausgabe statt Human-Report.")
    ap.add_argument("--dry-run", action="store_true", help="Expliziter Dry-Run-Schalter; Tool ist immer read-only.")
    args = ap.parse_args()

    namespace = str(args.namespace or "").strip()
    schema = str(args.state_schema_prefix or "").strip()
    if not namespace:
        print("ERROR: --namespace darf nicht leer sein", file=sys.stderr)
        return 2
    if not schema:
        print("ERROR: --state-schema-prefix darf nicht leer sein", file=sys.stderr)
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
        result = run_dry_run(
            conn,
            namespace=namespace,
            state_schema_prefix=schema,
            limit=int(args.limit),
            since_id=args.since_id,
            min_events=int(args.min_events),
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

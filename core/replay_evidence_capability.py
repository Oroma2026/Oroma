#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/replay_evidence_capability.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Replay Evidence Capability Registry · Read-Only Source Preflight
# Version:   v0.3.4-canonical-direct-outcome
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses neutrale Core-Modul beantwortet ausschliesslich, ob fuer eine exakt
# identifizierte Replay-Anforderung eine direkt nutzbare, lineagefaehige Quelle
# vorhanden ist. Es erzeugt keine Evidence, interpretiert keine Episode zu einem
# Step um und besitzt keinerlei Schreib- oder Ausfuehrungsbefugnis.
#
# PIPELINE-ROLLE
# --------------
#   Validation -> Capability (Ob ist die Quelle vorhanden?)
#   Replay Probe -> Capability + erneutes Quellenlesen (Wie wird Evidence gebaut?)
#   Boundary -> entscheidet spaeter ueber dauerhafte Policy-Mutation
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - SQLite ausschliesslich ueber eine bereits read-only geoeffnete Connection.
# - Keine DB-, State-, Queue-, Promotion- oder Policy-Writes.
# - Keine Replay-, Dream-, Runner- oder GUI-Starts.
# - Exakte Identitaet: namespace + state_schema + state_hash + action.
# - Root-/Episode-Outcomes werden niemals als Direct-Step-Evidence umgedeutet.
# - Widerspruechliche direkte Outcomes blockieren fail-closed.
# - Nicht registrierte Domain-/Schema-Paare blockieren fail-closed.
# - Die oeffentliche API bleibt backendneutral; ein spaeterer Suchindex darf den
#   Blob-Scan ersetzen, ohne Validation oder Probe umzubauen.
# =============================================================================

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from core.direct_outcome_normalization import normalize_direct_outcome as _normalize_direct_outcome
from core.snake_reconstructable_trace import canonical_sha256, verify_step
from core.snake_targeted_evidence_runner import (
    CONTINUATION_PROTOCOL,
    SUPPORTED_CONTINUATION_PROTOCOLS,
    EVIDENCE_CLASS,
    EVIDENCE_SCHEMA,
    WRITER_ID as TARGETED_WRITER_ID,
    experiment_source_id,
)

VERSION = "v0.3.4-canonical-direct-outcome"
DEFAULT_SNAKE_SCAN_LIMIT = 500
CapabilityChecker = Callable[..., Dict[str, Any]]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if text and all(ch in "+-0123456789" for ch in text):
            return int(text)
        return int(float(value))
    except Exception:
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_str(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _decode_blob(blob: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if blob is None:
        return None, "snapchain_blob_null"
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if isinstance(blob, dict):
        return blob, None
    candidates: List[str] = []
    if isinstance(blob, bytes):
        try: candidates.append(blob.decode("utf-8"))
        except Exception: pass
        try: candidates.append(zlib.decompress(blob).decode("utf-8"))
        except Exception: pass
    else:
        candidates.append(str(blob))
    for text in candidates:
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value, None
        except Exception:
            pass
    return None, "snapchain_blob_decode_failed"


def _step_state_hash(step: Mapping[str, Any]) -> str:
    for key in ("state_hash", "h", "sh"):
        if step.get(key) not in (None, ""):
            return str(step.get(key))
    nested = step.get("state")
    if isinstance(nested, Mapping):
        for key in ("state_hash", "h", "sh"):
            if nested.get(key) not in (None, ""):
                return str(nested.get(key))
    return ""


def _step_action(step: Mapping[str, Any]) -> str:
    for key in ("action", "a", "action_canon", "ac", "action_name"):
        if step.get(key) not in (None, ""):
            return str(step.get(key))
    return ""



def _base_result(namespace: str, state_schema: str, state_hash: str, action: str) -> Dict[str, Any]:
    return {
        "capability_version": VERSION,
        "supported": True,
        "available": False,
        "blocked_reason": None,
        "namespace": namespace,
        "state_schema": state_schema,
        "state_hash": state_hash,
        "action": action,
        "source_kind": None,
        "historical_state_exact": False,
        "simulation_required": False,
    }




def _targeted_candidate_key(root: Mapping[str, Any]) -> Tuple[str, str]:
    lineage = root.get("source_lineage") if isinstance(root.get("source_lineage"), Mapping) else {}
    intervention = root.get("intervention") if isinstance(root.get("intervention"), Mapping) else {}
    return (
        _safe_str(lineage.get("source_state_hash"), 500),
        str(intervention.get("target_action")) if intervention.get("target_action") is not None else "",
    )


def _validate_targeted_candidate(root: Mapping[str, Any], row_meta: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one relevant Targeted Evidence v1 experiment fail-closed.

    The capability never derives temporal credit itself. It only verifies the
    direct credit that C2 already materialized onto intervention step zero.
    """
    blocked: List[str] = []
    lineage = root.get("source_lineage") if isinstance(root.get("source_lineage"), Mapping) else {}
    intervention = root.get("intervention") if isinstance(root.get("intervention"), Mapping) else {}
    continuation = root.get("continuation") if isinstance(root.get("continuation"), Mapping) else {}
    experiment_seed = root.get("experiment_seed") if isinstance(root.get("experiment_seed"), Mapping) else {}
    result = root.get("result") if isinstance(root.get("result"), Mapping) else {}
    intent = root.get("learning_intent_lineage") if isinstance(root.get("learning_intent_lineage"), Mapping) else {}
    steps = root.get("steps") if isinstance(root.get("steps"), list) else []

    expected = {
        "origin": "targeted_evidence_runner:v1",
        "namespace": "game:snake",
        "state_schema": "snake:pro_v2",
        "trace_schema": "snake_trace:reconstructable_v1",
        "evidence_schema": EVIDENCE_SCHEMA,
        "evidence_class": EVIDENCE_CLASS,
        "writer_id": TARGETED_WRITER_ID,
    }
    for key, value in expected.items():
        if _safe_str(root.get(key), 300) != value:
            blocked.append(f"targeted_{key}_mismatch")

    experiment_id = _safe_str(root.get("experiment_id"), 300)
    if not experiment_id.startswith("snake_targeted_experiment:"):
        blocked.append("targeted_experiment_id_invalid")
    expected_source_id = experiment_source_id(experiment_id) if experiment_id else 0
    root_source_id = _as_int(root.get("source_id"), 0)
    row_source_id = _as_int(row_meta.get("snapchain_source_id"), 0)
    if not expected_source_id or root_source_id != expected_source_id or row_source_id != expected_source_id:
        blocked.append("targeted_experiment_source_id_mismatch")

    required_lineage = (
        "source_snapchain_id", "source_step_index", "source_state_hash",
        "source_before_state_digest", "source_episode_id", "source_runner_config_digest",
    )
    for key in required_lineage:
        if lineage.get(key) in (None, ""):
            blocked.append(f"targeted_lineage_{key}_missing")
    target_action = str(intervention.get("target_action")) if intervention.get("target_action") is not None else ""
    if target_action not in {"0", "1", "2"}:
        blocked.append("targeted_action_invalid")
    if intent:
        required_intent = (
            "promotion_id", "promotion_signature", "request_signature",
            "promotion_bucket", "promotion_status_at_acquisition", "namespace",
            "state_schema", "state_hash", "target", "primary_action",
        )
        for key in required_intent:
            if intent.get(key) in (None, ""):
                blocked.append(f"targeted_learning_intent_{key}_missing")
        if _safe_str(intent.get("promotion_bucket"), 160) != "promotion_candidate_replay":
            blocked.append("targeted_learning_intent_bucket_mismatch")
        if _safe_str(intent.get("promotion_status_at_acquisition"), 160) != "promotion_review":
            blocked.append("targeted_learning_intent_status_mismatch")
        if _safe_str(intent.get("namespace"), 160) != "game:snake":
            blocked.append("targeted_learning_intent_namespace_mismatch")
        if _safe_str(intent.get("state_schema"), 160) != "snake:pro_v2":
            blocked.append("targeted_learning_intent_schema_mismatch")
        if _safe_str(intent.get("target"), 80) != "replay":
            blocked.append("targeted_learning_intent_target_mismatch")
        if _safe_str(intent.get("state_hash"), 500) != _safe_str(lineage.get("source_state_hash"), 500):
            blocked.append("targeted_learning_intent_state_mismatch")
        if _safe_str(intent.get("primary_action"), 160) != target_action:
            blocked.append("targeted_learning_intent_action_mismatch")
    continuation_protocol = _safe_str(continuation.get("protocol"), 200)
    continuation_protocol_version = _safe_str(continuation.get("protocol_version"), 80)
    expected_protocol_version = SUPPORTED_CONTINUATION_PROTOCOLS.get(continuation_protocol)
    if expected_protocol_version is None:
        blocked.append("targeted_continuation_protocol_not_registered")
    elif continuation_protocol_version != str(expected_protocol_version):
        blocked.append("targeted_continuation_protocol_version_mismatch")
    if not _safe_str(continuation.get("config_digest"), 300).startswith("sha256:"):
        blocked.append("targeted_continuation_digest_missing")
    if not _safe_str(experiment_seed.get("digest"), 300).startswith("sha256:"):
        blocked.append("targeted_experiment_seed_digest_missing")
    if result.get("all_steps_verified") is not True:
        blocked.append("targeted_steps_not_preverified")
    if result.get("ready_for_replay_capability") is not True:
        blocked.append("targeted_result_not_replay_ready")
    if not steps:
        blocked.append("targeted_steps_missing")

    recorded_digest = _safe_str(root.get("evidence_digest"), 300)
    digest_payload = dict(root)
    digest_payload.pop("evidence_digest", None)
    computed_digest = canonical_sha256(digest_payload)
    if not recorded_digest or recorded_digest != computed_digest:
        blocked.append("targeted_evidence_digest_mismatch")

    verification_reasons: Dict[str, int] = {}
    for step in steps:
        checked = verify_step(step) if isinstance(step, Mapping) else None
        reason = checked.reason if checked is not None else "targeted_step_not_mapping"
        verification_reasons[reason] = verification_reasons.get(reason, 0) + 1
        if checked is None or not checked.ok:
            blocked.append("targeted_step_verification_failed")
            break

    target_step = steps[0] if steps and isinstance(steps[0], Mapping) else {}
    if _step_state_hash(target_step) != _safe_str(lineage.get("source_state_hash"), 500):
        blocked.append("targeted_intervention_state_mismatch")
    if _step_action(target_step) != target_action:
        blocked.append("targeted_intervention_action_mismatch")

    target_direct = result.get("target_step_direct_outcome")
    try:
        target_direct_num = float(target_direct)
    except Exception:
        target_direct_num = None
    outcome = "pos" if target_direct_num is not None and target_direct_num > 0 else "neg" if target_direct_num is not None and target_direct_num < 0 else "draw" if target_direct_num == 0 else None
    outcome_field = "result.target_step_direct_outcome"
    outcome_raw = target_direct
    try:
        step_outcome_num = float(target_step.get("outcome"))
        step_reward_num = float(target_step.get("reward"))
        step_result_num = float(target_step.get("result"))
    except Exception:
        step_outcome_num = step_reward_num = step_result_num = None
    if outcome not in {"pos", "neg"} or any(v != target_direct_num for v in (step_outcome_num, step_reward_num, step_result_num)):
        blocked.append("targeted_credit_outcome_mismatch")
    if _safe_str(target_step.get("credit_source"), 200) != "targeted_simulation_direct_step_window":
        blocked.append("targeted_credit_source_mismatch")
    if _safe_str(target_step.get("credit_model"), 200) != "snake_targeted_event_window_v1":
        blocked.append("targeted_credit_model_mismatch")
    if _as_int(target_step.get("credit_window_index"), -1) != 0:
        blocked.append("targeted_credit_intervention_index_mismatch")
    if _as_int(target_step.get("credit_window_size"), 0) < 1:
        blocked.append("targeted_credit_window_invalid")

    return {
        "valid": not blocked,
        "blocked_reasons": sorted(set(blocked)),
        "outcome": outcome,
        "outcome_field": outcome_field,
        "outcome_raw": outcome_raw,
        "experiment_id": experiment_id,
        "writer_id": _safe_str(root.get("writer_id"), 300),
        "evidence_schema": _safe_str(root.get("evidence_schema"), 200),
        "evidence_class": _safe_str(root.get("evidence_class"), 200),
        "source_snapchain_id": _as_int(lineage.get("source_snapchain_id"), 0),
        "source_step_index": _as_int(lineage.get("source_step_index"), -1),
        "source_before_state_digest": _safe_str(lineage.get("source_before_state_digest"), 300),
        "continuation_protocol": continuation_protocol,
        "continuation_protocol_version": continuation_protocol_version,
        "continuation_config_digest": _safe_str(continuation.get("config_digest"), 300),
        "experiment_seed_digest": _safe_str(experiment_seed.get("digest"), 300),
        "evidence_digest": recorded_digest,
        "verification_reason_counts": verification_reasons,
        "steps_total": len(steps),
        "event": _safe_str(result.get("event"), 160),
        "result_status": _safe_str(result.get("status"), 160),
        "learning_intent_lineage": json.loads(json.dumps(dict(intent), ensure_ascii=False)) if intent else {},
    }


def validate_targeted_evidence_snapchain(
    root: Mapping[str, Any],
    *,
    snapchain_id: int,
    snapchain_source_id: Any,
    snapchain_ts: int = 0,
    snapchain_origin: str = "",
    snapchain_namespace: str = "",
    snapchain_version: str = "",
    snapchain_quality: float = 0.0,
    snapchain_weight: float = 1.0,
) -> Dict[str, Any]:
    """Public, side-effect-free Targeted Evidence v1 verifier.

    Replay Probe uses this function only after freshly loading the selected
    SnapChain from SQLite in read-only mode. The function deliberately accepts
    explicit row metadata so the persisted row and immutable blob are checked
    as one TOCTOU verification unit.
    """
    row_meta = {
        "snapchain_id": int(snapchain_id),
        "snapchain_source_id": snapchain_source_id,
        "snapchain_ts": int(snapchain_ts),
        "snapchain_origin": str(snapchain_origin or ""),
        "snapchain_namespace": str(snapchain_namespace or ""),
        "snapchain_version": str(snapchain_version or ""),
        "snapchain_quality": float(snapchain_quality),
        "snapchain_weight": float(snapchain_weight),
    }
    return _validate_targeted_candidate(root, row_meta)


def build_replay_evidence_capability_context(
    con: sqlite3.Connection,
    *,
    schemas: Optional[List[str]] = None,
    scan_limit: int = DEFAULT_SNAKE_SCAN_LIMIT,
) -> Dict[str, Any]:
    """Build one immutable in-memory lookup for a validation/probe run.

    The context is read-only and process-local. It avoids decoding the same
    SnapChain blobs once per candidate while preserving the exact same
    state/action and direct-outcome semantics. No persistent index is created.
    """
    started = time.perf_counter()
    requested = set(str(x or "").strip() for x in (schemas or ["snake:pro_v2"]) if str(x or "").strip())
    context: Dict[str, Any] = {
        "context_version": VERSION,
        "schemas": sorted(requested),
        "snake_index": {},
        "snake_targeted_index": {},
        "historical_snapchains_scanned": 0,
        "targeted_snapchains_scanned": 0,
        "targeted_candidates_indexed": 0,
        "targeted_irrelevant_invalid_total": 0,
        "snapchains_scanned": 0,
        "steps_scanned_total": 0,
        "decode_errors": 0,
        "build_dt_ms": 0.0,
    }
    if "snake:pro_v2" not in requested or not _table_exists(con, "snapchains"):
        context["build_dt_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return context
    rows = con.execute(
        """SELECT id,ts,quality,blob,status,origin,namespace,source_id,version,weight
           FROM snapchains
           WHERE status='active' AND (origin IN ('game:snake','snake') OR namespace IN ('game:snake','snake'))
           ORDER BY id DESC LIMIT ?""",
        (max(1, int(scan_limit)),),
    ).fetchall()
    index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    targeted_index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    context["snapchains_scanned"] = len(rows)
    for row in rows:
        root, err = _decode_blob(row["blob"])
        if root is None:
            context["decode_errors"] += 1
            continue
        meta = root.get("meta") if isinstance(root.get("meta"), dict) else {}
        metadata = root.get("metadata") if isinstance(root.get("metadata"), dict) else {}
        origin = _safe_str(row["origin"] or root.get("origin"), 160)
        row_meta = {
            "snapchain_id": _as_int(row["id"]),
            "snapchain_ts": _as_int(row["ts"]),
            "snapchain_origin": origin,
            "snapchain_namespace": _safe_str(row["namespace"], 160),
            "snapchain_source_id": row["source_id"],
            "snapchain_version": _safe_str(row["version"], 160),
            "snapchain_quality": _as_float(row["quality"], 0.0),
            "snapchain_weight": _as_float(row["weight"], 1.0),
        }
        if origin == "targeted_evidence_runner:v1" or _safe_str(root.get("evidence_schema"), 160) == EVIDENCE_SCHEMA:
            context["targeted_snapchains_scanned"] += 1
            key = _targeted_candidate_key(root)
            if key[0] and key[1]:
                targeted_index.setdefault(key, []).append({"root": root, **row_meta})
                context["targeted_candidates_indexed"] += 1
            else:
                context["targeted_irrelevant_invalid_total"] += 1
            continue
        context["historical_snapchains_scanned"] += 1
        root_schema = _safe_str(root.get("state_schema") or meta.get("state_schema") or metadata.get("state_schema"), 120) or "snake:pro_v2"
        action_schema = _safe_str(root.get("action_schema") or meta.get("action_schema") or metadata.get("action_schema"), 120)
        steps = root.get("steps") if isinstance(root.get("steps"), list) else []
        context["steps_scanned_total"] += len(steps)
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            sh = _step_state_hash(step)
            act = _step_action(step)
            if not sh or not act:
                continue
            outcome, field, raw = _normalize_direct_outcome(step)
            index.setdefault((sh, act), []).append({
                "usable": outcome is not None,
                "blocked_reason": None if outcome is not None else (field or "matching_step_without_direct_outcome"),
                "outcome": outcome,
                "outcome_field": field,
                "outcome_raw": raw,
                "snapchain_id": _as_int(row["id"]),
                "snapchain_ts": _as_int(row["ts"]),
                "snapchain_origin": _safe_str(row["origin"], 160),
                "snapchain_namespace": _safe_str(row["namespace"], 160),
                "snapchain_source_id": row["source_id"],
                "snapchain_version": _safe_str(row["version"], 160),
                "snapchain_quality": _as_float(row["quality"], 0.0),
                "snapchain_weight": _as_float(row["weight"], 1.0),
                "step_index": step_index,
                "step_ts": _as_int(step.get("ts") or step.get("t"), 0),
                "step_mode": _safe_str(step.get("mode"), 120),
                "state_schema": root_schema,
                "action_schema": action_schema,
            })
    context["snake_index"] = index
    context["snake_targeted_index"] = targeted_index
    context["build_dt_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    return context


def _check_snake_direct_step_capability(
    con: sqlite3.Connection, state_schema: str, state_hash: str, action: str, scan_limit: int,
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    result = _base_result("game:snake", state_schema, state_hash, action)
    result.update({
        "snapchains_scanned": 0,
        "steps_scanned_total": 0,
        "decode_errors": 0,
        "matching_steps_total": 0,
        "matching_steps_with_direct_outcome": 0,
        "matching_direct_outcomes_total": 0,
        "matching_snapchains_total": 0,
        "matching_outcome_classes": [],
        "historical_matching_steps_total": 0,
        "historical_usable_total": 0,
        "targeted_candidates_total": 0,
        "targeted_valid_total": 0,
        "targeted_invalid_total": 0,
        "selected_source_kind": None,
        "capability_dt_ms": 0.0,
    })
    def _finish() -> Dict[str, Any]:
        result["capability_dt_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return result

    result["source_kind"] = "stored_snapchain_direct_step"
    if not state_hash.startswith("snake:pro_v2:"):
        result["blocked_reason"] = "unsupported_state_schema"
        return _finish()
    if not action:
        result["blocked_reason"] = "missing_replay_action"
        return _finish()
    if not _table_exists(con, "snapchains"):
        result["blocked_reason"] = "snake_snapchain_source_missing"
        return _finish()
    shared = context if isinstance(context, Mapping) else None
    if shared is not None:
        matches = list((shared.get("snake_index") or {}).get((state_hash, action), []))
        targeted_raw = list((shared.get("snake_targeted_index") or {}).get((state_hash, action), []))
        decode_errors = 0
        steps_scanned_total = 0
        rows_count = 0
        result["shared_scan_context"] = True
    else:
        local_context = build_replay_evidence_capability_context(
            con, schemas=["snake:pro_v2"], scan_limit=scan_limit
        )
        matches = list((local_context.get("snake_index") or {}).get((state_hash, action), []))
        targeted_raw = list((local_context.get("snake_targeted_index") or {}).get((state_hash, action), []))
        decode_errors = _as_int(local_context.get("decode_errors"), 0)
        steps_scanned_total = _as_int(local_context.get("steps_scanned_total"), 0)
        rows_count = _as_int(local_context.get("snapchains_scanned"), 0)
        result["shared_scan_context"] = False
    if shared is not None and _as_int(shared.get("snapchains_scanned"), 0) <= 0:
        result["blocked_reason"] = "snake_snapchain_source_missing"
        return _finish()
    if shared is None and rows_count <= 0:
        result["blocked_reason"] = "snake_snapchain_source_missing"
        return _finish()
    historical_usable = [m for m in matches if m["usable"]]
    targeted_valid: List[Dict[str, Any]] = []
    targeted_invalid: List[Dict[str, Any]] = []
    for candidate in targeted_raw:
        validated = _validate_targeted_candidate(candidate.get("root") or {}, candidate)
        merged = {**candidate, **validated}
        (targeted_valid if validated.get("valid") else targeted_invalid).append(merged)

    matching_snapchains = {int(m["snapchain_id"]) for m in matches}
    matching_snapchains.update(int(m["snapchain_id"]) for m in targeted_raw)
    historical_outcomes = sorted({str(m["outcome"]) for m in historical_usable if m.get("outcome")})
    targeted_outcomes = sorted({str(m["outcome"]) for m in targeted_valid if m.get("outcome")})
    result.update({
        "snapchains_scanned": rows_count,
        "steps_scanned_total": steps_scanned_total,
        "decode_errors": decode_errors,
        "matching_steps_total": len(matches) + len(targeted_raw),
        "matching_steps_with_direct_outcome": len(historical_usable) + len(targeted_valid),
        "matching_direct_outcomes_total": len(historical_usable) + len(targeted_valid),
        "matching_snapchains_total": len(matching_snapchains),
        "matching_outcome_classes": sorted(set(historical_outcomes + targeted_outcomes)),
        "historical_matching_steps_total": len(matches),
        "historical_usable_total": len(historical_usable),
        "targeted_candidates_total": len(targeted_raw),
        "targeted_valid_total": len(targeted_valid),
        "targeted_invalid_total": len(targeted_invalid),
        "targeted_invalid_reasons": sorted({r for m in targeted_invalid for r in m.get("blocked_reasons", [])}),
    })
    if targeted_invalid:
        result["blocked_reason"] = "targeted_simulation_corrupt_or_invalid"
        result["technical_blocked_reason"] = "relevant_targeted_evidence_failed_validation"
        return _finish()
    if not matches and not targeted_raw:
        result["blocked_reason"] = "replay_state_action_source_missing"
        return _finish()
    if not historical_usable and not targeted_valid:
        result["blocked_reason"] = "replay_direct_evidence_unavailable"
        result["technical_blocked_reason"] = "snake_matching_step_without_supported_direct_outcome"
        return _finish()
    if len(historical_outcomes) > 1:
        result.update({"blocked_reason": "replay_direct_evidence_conflict", "conflicting_outcomes": historical_outcomes})
        return _finish()
    if len(targeted_outcomes) > 1:
        result.update({"blocked_reason": "targeted_simulation_evidence_conflict", "conflicting_outcomes": targeted_outcomes})
        return _finish()
    if historical_outcomes and targeted_outcomes and historical_outcomes[0] != targeted_outcomes[0]:
        result.update({
            "blocked_reason": "replay_evidence_source_class_conflict",
            "historical_outcome": historical_outcomes[0],
            "targeted_outcome": targeted_outcomes[0],
        })
        return _finish()

    if targeted_valid:
        selected = sorted(targeted_valid, key=lambda m: (m["snapchain_ts"], m["snapchain_id"]), reverse=True)[0]
        selected_source_kind = "targeted_simulation_snapchain"
        outcome = targeted_outcomes[0]
        selected_step_index = 0
    else:
        selected = sorted(historical_usable, key=lambda m: (m["snapchain_ts"], m["snapchain_id"], -m["step_index"]), reverse=True)[0]
        selected_source_kind = "stored_snapchain_direct_step"
        outcome = historical_outcomes[0]
        selected_step_index = selected["step_index"]

    result.update({
        "available": True,
        "blocked_reason": None,
        "historical_state_exact": True,
        "source_kind": selected_source_kind,
        "selected_source_kind": selected_source_kind,
        "outcome": outcome,
        "outcome_field": selected.get("outcome_field"),
        "outcome_raw": selected.get("outcome_raw"),
        "snapchain_id": selected["snapchain_id"],
        "snapchain_ts": selected["snapchain_ts"],
        "snapchain_origin": selected["snapchain_origin"],
        "snapchain_namespace": selected["snapchain_namespace"],
        "snapchain_source_id": selected["snapchain_source_id"],
        "snapchain_version": selected["snapchain_version"],
        "snapchain_quality": selected["snapchain_quality"],
        "snapchain_weight": selected["snapchain_weight"],
        "step_index": selected_step_index,
        "step_ts": selected.get("step_ts", 0),
        "step_mode": selected.get("step_mode", ""),
        "action_schema": selected.get("action_schema", ""),
        "supporting_historical_total": len(historical_usable),
        "supporting_targeted_total": len(targeted_valid),
    })
    if selected_source_kind == "targeted_simulation_snapchain":
        result.update({
            "evidence_schema": selected.get("evidence_schema"),
            "evidence_class": selected.get("evidence_class"),
            "experiment_id": selected.get("experiment_id"),
            "writer_id": selected.get("writer_id"),
            "source_snapchain_id": selected.get("source_snapchain_id"),
            "source_step_index": selected.get("source_step_index"),
            "source_before_state_digest": selected.get("source_before_state_digest"),
            "continuation_protocol": selected.get("continuation_protocol"),
            "continuation_protocol_version": selected.get("continuation_protocol_version"),
            "continuation_config_digest": selected.get("continuation_config_digest"),
            "experiment_seed_digest": selected.get("experiment_seed_digest"),
            "evidence_digest": selected.get("evidence_digest"),
            "targeted_result_status": selected.get("result_status"),
            "targeted_event": selected.get("event"),
            "targeted_steps_total": selected.get("steps_total"),
            "targeted_verification_reason_counts": selected.get("verification_reason_counts"),
            "learning_intent_lineage": selected.get("learning_intent_lineage") or {},
            "simulation_required": False,
        })
    return _finish()


_CAPABILITY_CHECKERS: Dict[Tuple[str, str], CapabilityChecker] = {
    ("game:snake", "snake:pro_v2"): _check_snake_direct_step_capability,
    ("snake", "snake:pro_v2"): _check_snake_direct_step_capability,
}


def state_schema_from_hash(state_hash: str) -> str:
    text = str(state_hash or "").strip()
    parts = text.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else ""


def check_replay_evidence_capability(
    con: sqlite3.Connection, *, namespace: str, state_schema: str = "", state_hash: str,
    action: str, scan_limit: int = DEFAULT_SNAKE_SCAN_LIMIT,
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ns = str(namespace or "").strip()
    sh = str(state_hash or "").strip()
    act = str(action or "").strip()
    schema = str(state_schema or state_schema_from_hash(sh)).strip()
    checker = _CAPABILITY_CHECKERS.get((ns, schema))
    if checker is None:
        result = _base_result(ns, schema, sh, act)
        result.update({"supported": False, "blocked_reason": "replay_evidence_capability_not_implemented"})
        return result
    return checker(con, schema, sh, act, max(1, int(scan_limit)), context=context)


__all__ = [
    "VERSION",
    "DEFAULT_SNAKE_SCAN_LIMIT",
    "build_replay_evidence_capability_context",
    "check_replay_evidence_capability",
    "state_schema_from_hash",
    "validate_targeted_evidence_snapchain",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_targeted_evidence_runner.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Targeted Evidence Runner – Read-Only Simulation Core
# Version: v0.5.0-continuation-protocol-registry
# Stand:   2026-07-13
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul führt einen streng begrenzten, deterministischen Snake-
# Interventionsversuch auf Basis eines bereits verifizierten konkreten
# ``snake_trace:reconstructable_v1``-Vorzustands aus. Es beantwortet eine
# präzise experimentelle Frage:
#
#   "Welches direkt beobachtbare Ereignis entsteht, wenn in diesem konkreten
#    Zustand genau diese relative Aktion ausgeführt und anschließend das
#    versionierte schwache Continuation Protocol verwendet wird?"
#
# EPISTEMISCHE GRENZE
# ───────────────────
# Das Ergebnis ist ``targeted_simulation_observation``. Ein einzelner Zweig ist
# echte experimentelle Evidence, aber kein kontrafaktischer oder universeller
# Kausalbeweis. Die Aussage bleibt vollständig an Vorzustand, Zielaktion,
# Continuation Protocol, Horizont und Experiment-Seed gebunden.
#
# SICHERHEITSVERTRAG
# ──────────────────
#   • Keine Datenbankzugriffe in diesem Core-Modul.
#   • Keine Policy-, Gap-, Queue-, Promotion- oder Outcome-Mutation.
#   • Keine Live-Policy und kein Explore-Zufall.
#   • Nur relative Aktionen 0=fwd, 1=left, 2=right.
#   • Harte Schritt- und Laufzeitgrenzen.
#   • Fail-closed bei Schema-, Digest-, State-Hash- oder Physikabweichungen.
#   • Die Original-SnapChain wird niemals verändert.
#
# CONTINUATION PROTOCOL
# ─────────────────────
# ``snake_continuation:safe_deterministic_v1`` prüft nach der erzwungenen
# Zielaktion immer in fester Reihenfolge forward, left, right. Die erste nicht
# unmittelbar tödliche Aktion wird gewählt. Existiert keine sichere Aktion,
# wird forward gewählt und das physikalische Todesereignis sauber beobachtet.
# Es gibt kein A*, keine Food-Optimierung und keine Policy-Abfrage.
# =============================================================================

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.snake_food_directed_continuation import (
    PROTOCOL_ID as FOOD_DIRECTED_PROTOCOL,
    PROTOCOL_VERSION as FOOD_DIRECTED_PROTOCOL_VERSION,
    select_food_directed_action,
)
from core.snake_reconstructable_trace import (
    CREDIT_SCHEMA,
    STATE_SCHEMA,
    TRACE_SCHEMA,
    canonical_sha256,
    compact_after_state,
    next_head,
    normalize_body,
    relative_to_absolute,
    state_digest,
    verify_step,
)

VERSION = "v0.5.0-continuation-protocol-registry"
WRITER_ID = "writer:core.snake_targeted_evidence_runner:v1"
PERSISTED_SCHEMA_VERSION = "snake_targeted_evidence_runner:persisted_v1"
EVIDENCE_SCHEMA = "snake_targeted_evidence:v1"
EVIDENCE_CLASS = "targeted_simulation_observation"
CONTINUATION_PROTOCOL = "snake_continuation:safe_deterministic_v1"
CONTINUATION_PROTOCOL_VERSION = "v1"
SUPPORTED_CONTINUATION_PROTOCOLS = {
    CONTINUATION_PROTOCOL: CONTINUATION_PROTOCOL_VERSION,
    FOOD_DIRECTED_PROTOCOL: FOOD_DIRECTED_PROTOCOL_VERSION,
}
REL_ACTIONS: Tuple[int, int, int] = (0, 1, 2)


def experiment_source_id(experiment_id: str) -> int:
    """Return a deterministic positive 63-bit SQLite-compatible source id.

    The full canonical experiment id remains authoritative inside the immutable
    evidence blob. This integer is only the indexed lookup key used for fast
    idempotency checks against the existing ``snapchains.source_id`` column.
    """
    digest = hashlib.sha256(str(experiment_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def build_persisted_evidence_blob(evidence_preview: Mapping[str, Any]) -> Dict[str, Any]:
    """Create the immutable C2 evidence blob without mutating the preview."""
    blob = json.loads(json.dumps(dict(evidence_preview), ensure_ascii=False))
    experiment_id = str(blob.get("experiment_id") or "").strip()
    if not experiment_id:
        raise ValueError("experiment_id_missing")
    blob["schema_version"] = PERSISTED_SCHEMA_VERSION
    blob["writer_id"] = WRITER_ID
    blob["source_id"] = int(experiment_source_id(experiment_id))
    meta = dict(blob.get("meta") or {})
    meta.update({
        "db_written": True,
        "dbwriter_only": True,
        "max_writes_per_run": 1,
        "policy_write_allowed_here": False,
        "queue_write_allowed_here": False,
        "promotion_write_allowed_here": False,
    })
    blob["meta"] = meta
    blob.pop("evidence_digest", None)
    blob["evidence_digest"] = canonical_sha256(blob)
    return blob


@dataclass(frozen=True)
class TargetedSimulationResult:
    ok: bool
    status: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": bool(self.ok), "status": str(self.status), **dict(self.payload)}


def _sign(value: int) -> int:
    return 1 if int(value) > 0 else -1 if int(value) < 0 else 0


def _bucket(value: int, cuts: Sequence[int]) -> str:
    v = int(value)
    for cut in cuts:
        if v <= int(cut):
            return str(int(cut))
    return "gt" + str(int(cuts[-1]))


def _collision_kind(
    body: Sequence[Sequence[int]],
    candidate_head: Sequence[int],
    grid_w: int,
    grid_h: int,
    *,
    ate: bool,
) -> Optional[str]:
    x, y = int(candidate_head[0]), int(candidate_head[1])
    if x < 0 or y < 0 or x >= int(grid_w) or y >= int(grid_h):
        return "wall"
    normalized = normalize_body(body)
    blocked = normalized if ate else normalized[:-1]
    return "self" if [x, y] in blocked else None


def _flood_space(
    start: Sequence[int],
    blocked: Sequence[Sequence[int]],
    grid_w: int,
    grid_h: int,
    *,
    limit: int = 128,
) -> int:
    start_cell = (int(start[0]), int(start[1]))
    if not (0 <= start_cell[0] < int(grid_w) and 0 <= start_cell[1] < int(grid_h)):
        return 0
    blocked_set = {(int(c[0]), int(c[1])) for c in blocked}
    if start_cell in blocked_set:
        return 0
    queue: List[Tuple[int, int]] = [start_cell]
    seen = {start_cell}
    cursor = 0
    while cursor < len(queue) and len(seen) < int(limit):
        x, y = queue[cursor]
        cursor += 1
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            cell = (x + dx, y + dy)
            if not (0 <= cell[0] < int(grid_w) and 0 <= cell[1] < int(grid_h)):
                continue
            if cell in blocked_set or cell in seen:
                continue
            seen.add(cell)
            queue.append(cell)
    return int(len(seen))


def build_pro_v2_state_hash(
    snake_body: Sequence[Sequence[int]],
    direction: int,
    food: Sequence[int],
    grid_w: int,
    grid_h: int,
    steps_since_food: int,
) -> Tuple[str, Dict[str, Any]]:
    """Recreate the production ``snake:pro_v2`` abstraction deterministically."""
    body = normalize_body(snake_body)
    head = body[0]
    food_cell = [int(food[0]), int(food[1])]
    dangers: Dict[str, int] = {}
    spaces: Dict[str, int] = {}
    for rel, name in ((0, "f"), (1, "l"), (2, "r")):
        absolute = relative_to_absolute(direction, rel)
        candidate = next_head(head, absolute)
        ate = candidate == food_cell
        collision = _collision_kind(body, candidate, grid_w, grid_h, ate=ate)
        dangers[name] = 1 if collision else 0
        if collision:
            spaces[name] = 0
        else:
            blocked = body if ate else body[:-1]
            spaces[name] = _flood_space(candidate, blocked, grid_w, grid_h, limit=128)

    dx = int(food_cell[0]) - int(head[0])
    dy = int(food_cell[1]) - int(head[1])
    d = int(direction) % 4
    if d == 0:
        fwd, right = -dy, dx
    elif d == 1:
        fwd, right = dx, dy
    elif d == 2:
        fwd, right = dy, -dx
    else:
        fwd, right = -dx, -dy

    dist = abs(dx) + abs(dy)
    feat = {
        "df": dangers.get("f", 1),
        "dl": dangers.get("l", 1),
        "dr": dangers.get("r", 1),
        "food_fwd": _sign(fwd),
        "food_right": _sign(right),
        "dist_bucket": _bucket(dist, (0, 1, 2, 4, 7, 11, 16, 24)),
        "len_bucket": _bucket(len(body), (3, 5, 8, 12, 18, 25)),
        "space_bucket": _bucket(max(spaces.values()) if spaces else 0, (0, 4, 8, 16, 32, 64, 96)),
        "hunger_bucket": _bucket(steps_since_food, (0, 4, 8, 16, 32, 64, 128, 256)),
    }
    state_hash = (
        "snake:pro_v2:"
        f"d={feat['df']}{feat['dl']}{feat['dr']}"
        f":ff={feat['food_fwd']}:fr={feat['food_right']}"
        f":dist={feat['dist_bucket']}:len={feat['len_bucket']}"
        f":space={feat['space_bucket']}:hun={feat['hunger_bucket']}"
    )
    return state_hash, feat


def derive_experiment_seed(
    source_snapchain_id: int,
    source_step_index: int,
    target_action: int,
    attempt_index: int,
    *,
    continuation_protocol: str = CONTINUATION_PROTOCOL,
) -> Dict[str, Any]:
    payload = {
        "source_snapchain_id": int(source_snapchain_id),
        "source_step_index": int(source_step_index),
        "target_action": int(target_action),
        "continuation_protocol": str(continuation_protocol),
        "continuation_protocol_version": SUPPORTED_CONTINUATION_PROTOCOLS[str(continuation_protocol)],
        "attempt_index": int(attempt_index),
    }
    digest = canonical_sha256(payload)
    seed_int = int(digest.split(":", 1)[1][:16], 16)
    return {"payload": payload, "digest": digest, "seed_int": seed_int}


def _deterministic_food(
    snake_body: Sequence[Sequence[int]],
    grid_w: int,
    grid_h: int,
    experiment_seed_digest: str,
    spawn_index: int,
) -> List[int]:
    occupied = {(int(c[0]), int(c[1])) for c in normalize_body(snake_body)}
    free_cells = [
        [x, y]
        for y in range(int(grid_h))
        for x in range(int(grid_w))
        if (x, y) not in occupied
    ]
    if not free_cells:
        return [0, 0]
    selector_payload = {
        "experiment_seed_digest": str(experiment_seed_digest),
        "spawn_index": int(spawn_index),
        "occupied_digest": canonical_sha256({"occupied": sorted([list(c) for c in occupied])}),
        "free_cells": len(free_cells),
    }
    selector = hashlib.sha256(
        json.dumps(selector_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return list(free_cells[int(selector[:16], 16) % len(free_cells)])


def _choose_continuation_action(
    body: Sequence[Sequence[int]],
    direction: int,
    food: Sequence[int],
    grid_w: int,
    grid_h: int,
    *,
    continuation_protocol: str = CONTINUATION_PROTOCOL,
) -> Dict[str, Any]:
    evaluated = []
    for action in REL_ACTIONS:
        absolute = relative_to_absolute(direction, action)
        candidate = next_head(body[0], absolute)
        ate = candidate == [int(food[0]), int(food[1])]
        collision = _collision_kind(body, candidate, grid_w, grid_h, ate=ate)
        evaluated.append({
            "action": int(action),
            "absolute_direction": int(absolute),
            "next_head": list(candidate),
            "collision": collision,
            "safe": collision is None,
        })

    if continuation_protocol == CONTINUATION_PROTOCOL:
        for item in evaluated:
            if item["safe"]:
                return {
                    "action": int(item["action"]),
                    "evaluated": evaluated,
                    "fallback_to_forward": False,
                    "protocol": CONTINUATION_PROTOCOL,
                    "protocol_version": CONTINUATION_PROTOCOL_VERSION,
                }
        return {
            "action": 0,
            "evaluated": evaluated,
            "fallback_to_forward": True,
            "protocol": CONTINUATION_PROTOCOL,
            "protocol_version": CONTINUATION_PROTOCOL_VERSION,
        }

    if continuation_protocol == FOOD_DIRECTED_PROTOCOL:
        return select_food_directed_action(evaluated, food)

    raise ValueError("continuation_protocol_not_supported")


def _apply_direct_credit(
    steps: List[Dict[str, Any]],
    window: int,
    value: float,
    reason: str,
    event_type: str,
    *,
    terminal: bool,
) -> int:
    selected = steps[-max(1, int(window)):]
    for index, step in enumerate(selected):
        step.update({
            "outcome": float(value),
            "reward": float(value),
            "result": float(value),
            "credit_source": "targeted_simulation_direct_step_window",
            "credit_model": "snake_targeted_event_window_v1",
            "direct_credit": float(value),
            "direct_credit_sum": float(value),
            "direct_credit_count": 1,
            "credit_reason": str(reason),
            "credit_reasons": [str(reason)],
            "event_type": str(event_type),
            "event_types": [str(event_type)],
            "credit_window_index": int(index),
            "credit_window_size": int(len(selected)),
        })
        if terminal and index == len(selected) - 1:
            step["terminal"] = True
            step["terminal_event_type"] = str(event_type)
    return len(selected)



def _manhattan(a: Sequence[int], b: Sequence[int]) -> int:
    """Return deterministic Manhattan distance between two grid cells."""
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def _wall_clearance(cell: Sequence[int], grid_w: int, grid_h: int) -> int:
    """Return minimum in-board distance from ``cell`` to any wall boundary.

    A head on an edge cell has clearance 0. An out-of-board collision candidate
    also reports 0, which keeps the metric conservative and easy to audit.
    """
    x, y = int(cell[0]), int(cell[1])
    if x < 0 or y < 0 or x >= int(grid_w) or y >= int(grid_h):
        return 0
    return max(0, min(x, y, int(grid_w) - 1 - x, int(grid_h) - 1 - y))


def _body_clearance(head: Sequence[int], body: Sequence[Sequence[int]]) -> Optional[int]:
    """Return minimum Manhattan clearance from head to another body segment."""
    normalized = normalize_body(body)
    if len(normalized) <= 1:
        return None
    return min(_manhattan(head, segment) for segment in normalized[1:])


def _build_exhaustion_diagnostics(
    *,
    initial_body: Sequence[Sequence[int]],
    initial_direction: int,
    initial_food: Sequence[int],
    initial_hunger: int,
    grid_w: int,
    grid_h: int,
    steps: Sequence[Mapping[str, Any]],
    status: str,
    event: Optional[str],
    max_steps: int,
) -> Dict[str, Any]:
    """Derive read-only trajectory diagnostics from verified trace records.

    The function does not assign credit and does not influence simulation
    control flow. Every metric is derived from already recorded states.
    """
    initial_head = normalize_body(initial_body)[0]
    food_distances: List[int] = [_manhattan(initial_head, initial_food)]
    head_positions: List[Tuple[int, int]] = [(int(initial_head[0]), int(initial_head[1]))]
    movement_signatures: List[Tuple[int, int, int, int, int]] = [
        (int(initial_head[0]), int(initial_head[1]), int(initial_direction) % 4, int(initial_food[0]), int(initial_food[1]))
    ]
    wall_clearances: List[int] = [_wall_clearance(initial_head, grid_w, grid_h)]
    initial_body_clearance = _body_clearance(initial_head, initial_body)
    body_clearances: List[int] = [] if initial_body_clearance is None else [initial_body_clearance]
    hunger_values: List[int] = [int(initial_hunger)]

    for step in steps:
        context = step.get("trace_context") if isinstance(step.get("trace_context"), Mapping) else {}
        transition = context.get("transition") if isinstance(context.get("transition"), Mapping) else {}
        checked = verify_step(step)
        reconstructed = checked.reconstructed_after if checked.ok else {}
        after_body = normalize_body(reconstructed.get("snake_body") or []) if reconstructed.get("snake_body") else []
        after_food = [int(x) for x in (reconstructed.get("food") or [])]
        after_direction = int(reconstructed.get("direction", 0)) % 4
        after_hunger = int(reconstructed.get("steps_since_food", hunger_values[-1]))
        if after_body and len(after_food) == 2:
            head = after_body[0]
            head_positions.append((int(head[0]), int(head[1])))
            movement_signatures.append((int(head[0]), int(head[1]), after_direction, int(after_food[0]), int(after_food[1])))
            food_distances.append(_manhattan(head, after_food))
            wall_clearances.append(_wall_clearance(head, grid_w, grid_h))
            clearance = _body_clearance(head, after_body)
            if clearance is not None:
                body_clearances.append(clearance)
            hunger_values.append(after_hunger)
        elif transition.get("collision"):
            candidate = transition.get("next_head") or initial_head
            wall_clearances.append(_wall_clearance(candidate, grid_w, grid_h))
            if str(transition.get("collision")) == "self":
                body_clearances.append(0)

    seen: Dict[Tuple[int, int, int, int, int], int] = {}
    periods: List[int] = []
    for index, signature in enumerate(movement_signatures):
        if signature in seen:
            periods.append(index - seen[signature])
        else:
            seen[signature] = index

    unique_heads = len(set(head_positions))
    revisited = max(0, len(head_positions) - unique_heads)
    initial_distance = int(food_distances[0])
    final_distance = int(food_distances[-1])
    minimum_distance = int(min(food_distances))
    return {
        "diagnostics_schema": "snake_exhaustion_diagnostics:v1",
        "initial_food_distance": initial_distance,
        "final_food_distance": final_distance,
        "minimum_food_distance": minimum_distance,
        "food_distance_delta": initial_distance - final_distance,
        "best_food_distance_gain": initial_distance - minimum_distance,
        "unique_head_positions": unique_heads,
        "revisited_head_positions": revisited,
        "loop_detected": bool(periods),
        "loop_period_guess": min(periods) if periods else None,
        "minimum_wall_clearance": min(wall_clearances) if wall_clearances else None,
        "minimum_body_clearance": min(body_clearances) if body_clearances else None,
        "initial_steps_since_food": int(hunger_values[0]),
        "final_steps_since_food": int(hunger_values[-1]),
        "termination_reason": str(event or status),
        "horizon_reached": bool(len(steps) >= int(max_steps) and status == "no_direct_outcome"),
        "death_reached": bool(status in {"death_wall_credit", "death_self_credit"}),
        "food_reached": bool(status in {"food_credit", "target_credit"}),
        "trajectory_steps": int(len(steps)),
    }

def simulate_targeted_observation(
    *,
    source_snapchain_id: int,
    source_step_index: int,
    source_chain: Mapping[str, Any],
    source_step: Mapping[str, Any],
    target_action: int,
    attempt_index: int = 0,
    max_steps: int = 12,
    max_runtime_sec: float = 2.0,
    target_len: Optional[int] = None,
    credit_steps: int = 12,
    death_credit_steps: int = 4,
    learning_intent_lineage: Optional[Mapping[str, Any]] = None,
    acquisition_schedule: Optional[Mapping[str, Any]] = None,
    continuation_protocol: str = CONTINUATION_PROTOCOL,
) -> TargetedSimulationResult:
    started = time.monotonic()
    source_namespace = str(source_chain.get("namespace") or source_chain.get("origin") or "")
    if source_namespace not in {"game:snake", "snake"}:
        return TargetedSimulationResult(False, "source_namespace_not_supported", {"errors": ["source_namespace_not_supported"]})
    if str(source_chain.get("trace_schema") or "") != TRACE_SCHEMA:
        return TargetedSimulationResult(False, "trace_schema_not_supported", {"errors": ["trace_schema_not_supported"]})
    if str(source_chain.get("state_schema") or "") != STATE_SCHEMA:
        return TargetedSimulationResult(False, "state_schema_not_supported", {"errors": ["state_schema_not_supported"]})
    if int(target_action) not in REL_ACTIONS:
        return TargetedSimulationResult(False, "action_not_supported", {"errors": ["action_not_supported"]})
    if int(max_steps) <= 0:
        return TargetedSimulationResult(False, "step_budget_invalid", {"errors": ["step_budget_invalid"]})
    if str(continuation_protocol) not in SUPPORTED_CONTINUATION_PROTOCOLS:
        return TargetedSimulationResult(False, "continuation_protocol_not_supported", {
            "errors": ["continuation_protocol_not_supported"],
            "continuation_protocol": str(continuation_protocol),
        })

    verification = verify_step(source_step)
    if not verification.ok:
        return TargetedSimulationResult(False, "source_trace_invalid", {
            "errors": [verification.reason],
            "source_verification": verification.to_dict(),
        })

    context = source_step.get("trace_context")
    before = context.get("before") if isinstance(context, Mapping) else None
    if not isinstance(before, Mapping):
        return TargetedSimulationResult(False, "source_before_missing", {"errors": ["source_before_missing"]})

    body = normalize_body(before.get("snake_body") or [])
    direction = int(before.get("direction", 0)) % 4
    food = [int(x) for x in (before.get("food") or [])]
    initial_body = [list(c) for c in body]
    initial_direction = int(direction)
    initial_food = list(food)
    grid_w = int(before.get("grid_w", 0))
    grid_h = int(before.get("grid_h", 0))
    hunger = int(before.get("steps_since_food", 0))
    before_digest = state_digest(body, direction, food, grid_w, grid_h, hunger)
    if before_digest != str(before.get("state_digest") or ""):
        return TargetedSimulationResult(False, "state_digest_mismatch", {"errors": ["state_digest_mismatch"]})

    computed_source_hash, _ = build_pro_v2_state_hash(body, direction, food, grid_w, grid_h, hunger)
    recorded_source_hash = str(source_step.get("state_hash") or source_step.get("sh") or "")
    if not recorded_source_hash or computed_source_hash != recorded_source_hash:
        return TargetedSimulationResult(False, "source_state_hash_mismatch", {
            "errors": ["source_state_hash_mismatch"],
            "recorded_state_hash": recorded_source_hash,
            "computed_state_hash": computed_source_hash,
        })

    resolved_continuation_protocol = str(continuation_protocol)
    resolved_continuation_version = SUPPORTED_CONTINUATION_PROTOCOLS[resolved_continuation_protocol]
    seed = derive_experiment_seed(
        source_snapchain_id, source_step_index, target_action, attempt_index,
        continuation_protocol=resolved_continuation_protocol,
    )
    continuation_config = {
        "protocol": resolved_continuation_protocol,
        "protocol_version": resolved_continuation_version,
        "action_priority": [0, 1, 2],
        "selection_rule": (
            "first_safe_forward_left_right"
            if resolved_continuation_protocol == CONTINUATION_PROTOCOL
            else "minimum_safe_manhattan_then_forward_left_right"
        ),
        "policy_access": False,
        "explore_randomness": False,
        "food_spawn": "canonical_hash_free_cell_v1",
        "stop_on_first_direct_event": True,
    }
    continuation_config_digest = canonical_sha256(continuation_config)
    resolved_target_len = int(target_len or (source_chain.get("runner_config") or {}).get("target_len") or 20)
    simulation_steps: List[Dict[str, Any]] = []
    event: Optional[str] = None
    status = "no_direct_outcome"
    spawn_index = 0

    for simulation_index in range(int(max_steps)):
        if time.monotonic() - started > float(max_runtime_sec):
            status = "runtime_budget_exceeded"
            break
        state_hash, features = build_pro_v2_state_hash(body, direction, food, grid_w, grid_h, hunger)
        if simulation_index == 0:
            action = int(target_action)
            action_source = "target_action"
            continuation_decision = None
        else:
            continuation_decision = _choose_continuation_action(
                body, direction, food, grid_w, grid_h,
                continuation_protocol=resolved_continuation_protocol,
            )
            action = int(continuation_decision["action"])
            action_source = resolved_continuation_protocol

        absolute = relative_to_absolute(direction, action)
        candidate = next_head(body[0], absolute)
        ate = candidate == food
        collision = _collision_kind(body, candidate, grid_w, grid_h, ate=ate)
        step_record: Dict[str, Any] = {
            "t": int(simulation_index),
            "state_hash": state_hash,
            "sh": state_hash,
            "a": int(action),
            "action": int(action),
            "features": features,
            "trace_schema": TRACE_SCHEMA,
            "trace_context": {
                "before": {
                    "grid_w": int(grid_w),
                    "grid_h": int(grid_h),
                    "snake_body": [list(c) for c in body],
                    "direction": int(direction),
                    "food": list(food),
                    "steps_since_food": int(hunger),
                    "state_digest": state_digest(body, direction, food, grid_w, grid_h, hunger),
                },
                "transition": {
                    "relative_action": int(action),
                    "absolute_direction": int(absolute),
                    "next_head": list(candidate),
                    "ate": bool(ate),
                    "collision": collision,
                    "applied": collision is None,
                    "action_source": action_source,
                    "continuation_decision": continuation_decision,
                },
            },
        }

        if collision is not None:
            event = "death_wall" if collision == "wall" else "death_self"
            step_record["trace_context"]["after"] = compact_after_state(
                body, direction, food, grid_w, grid_h, hunger,
                terminal=True, event=event,
            )
            simulation_steps.append(step_record)
            status = "death_wall_credit" if collision == "wall" else "death_self_credit"
            _apply_direct_credit(
                simulation_steps,
                death_credit_steps,
                -1.0,
                event,
                event,
                terminal=True,
            )
            break

        direction = absolute
        body = [list(candidate)] + [list(c) for c in body]
        if ate:
            hunger = 0
            spawn_index += 1
            food = _deterministic_food(body, grid_w, grid_h, seed["digest"], spawn_index)
            event = "target_len" if len(body) >= resolved_target_len else "food_eaten"
        else:
            body.pop()
            hunger += 1
            event = None

        terminal = bool(event == "target_len")
        step_record["trace_context"]["after"] = compact_after_state(
            body, direction, food, grid_w, grid_h, hunger,
            terminal=terminal, event=event,
        )
        simulation_steps.append(step_record)

        if event == "food_eaten":
            status = "food_credit"
            _apply_direct_credit(
                simulation_steps,
                credit_steps,
                +1.0,
                "food_eaten",
                "food_eaten",
                terminal=False,
            )
            break
        if event == "target_len":
            status = "target_credit"
            _apply_direct_credit(
                simulation_steps,
                credit_steps,
                +1.0,
                "target_len",
                "target_len",
                terminal=True,
            )
            break

    all_verified = True
    verification_reasons: List[str] = []
    for step_record in simulation_steps:
        checked = verify_step(step_record)
        verification_reasons.append(checked.reason)
        if not checked.ok:
            all_verified = False

    diagnostics = _build_exhaustion_diagnostics(
        initial_body=initial_body,
        initial_direction=initial_direction,
        initial_food=initial_food,
        initial_hunger=int(before.get("steps_since_food", 0)),
        grid_w=grid_w,
        grid_h=grid_h,
        steps=simulation_steps,
        status=status,
        event=event,
        max_steps=max_steps,
    )
    target_step = simulation_steps[0] if simulation_steps else {}
    target_direct_outcome = target_step.get("outcome")
    target_ready = target_direct_outcome in (-1.0, 1.0, -1, 1)
    intent_lineage = json.loads(json.dumps(dict(learning_intent_lineage or {}), ensure_ascii=False))
    schedule_lineage = json.loads(json.dumps(dict(acquisition_schedule or {}), ensure_ascii=False))
    experiment_id_payload = {
        "source_snapchain_id": int(source_snapchain_id),
        "source_step_index": int(source_step_index),
        "source_before_state_digest": before_digest,
        "target_action": int(target_action),
        "continuation_protocol": resolved_continuation_protocol,
        "continuation_protocol_version": resolved_continuation_version,
        "continuation_config_digest": continuation_config_digest,
        "experiment_seed_digest": seed["digest"],
        "attempt_index": int(attempt_index),
        "learning_intent_lineage": intent_lineage,
        "acquisition_schedule": schedule_lineage,
    }
    experiment_id = "snake_targeted_experiment:" + canonical_sha256(experiment_id_payload).split(":", 1)[1][:24]
    evidence_preview = {
        "schema_version": "snake_targeted_evidence_runner:dry_run_v1",
        "state_schema": STATE_SCHEMA,
        "trace_schema": TRACE_SCHEMA,
        "credit_schema": CREDIT_SCHEMA,
        "evidence_schema": EVIDENCE_SCHEMA,
        "evidence_class": EVIDENCE_CLASS,
        "origin": "targeted_evidence_runner:v1",
        "namespace": "game:snake",
        "experiment_id": experiment_id,
        "learning_intent_lineage": intent_lineage,
        "acquisition_schedule": schedule_lineage,
        "source_lineage": {
            "source_snapchain_id": int(source_snapchain_id),
            "source_step_index": int(source_step_index),
            "source_state_hash": recorded_source_hash,
            "source_action": int(source_step.get("a", -1)),
            "source_before_state_digest": before_digest,
            "source_episode_id": source_chain.get("episode_id"),
            "source_runner_config_digest": source_chain.get("runner_config_digest"),
        },
        "intervention": {
            "target_action": int(target_action),
            "attempt_index": int(attempt_index),
            "max_steps": int(max_steps),
            "max_runtime_sec": float(max_runtime_sec),
        },
        "continuation": {
            "protocol": resolved_continuation_protocol,
            "protocol_version": resolved_continuation_version,
            "config": continuation_config,
            "config_digest": continuation_config_digest,
        },
        "experiment_seed": seed,
        "result": {
            "status": status,
            "event": event,
            "steps_executed": len(simulation_steps),
            "target_step_direct_outcome": target_direct_outcome,
            "ready_for_replay_capability": bool(target_ready),
            "all_steps_verified": bool(all_verified),
            "exhaustion_diagnostics": diagnostics,
        },
        "steps": simulation_steps,
        "meta": {
            "policy_used": False,
            "explore_randomness_used": False,
            "db_written": False,
            "policy_write_allowed_here": False,
        },
    }
    evidence_preview["evidence_digest"] = canonical_sha256(evidence_preview)

    success_statuses = {
        "food_credit", "target_credit", "death_wall_credit", "death_self_credit", "no_direct_outcome"
    }
    ok = bool(status in success_statuses and all_verified)
    return TargetedSimulationResult(ok, status, {
        "version": VERSION,
        "evidence_preview": evidence_preview,
        "summary": {
            "steps_executed": len(simulation_steps),
            "target_step_direct_outcome": target_direct_outcome,
            "ready_for_replay_capability": bool(target_ready),
            "all_steps_verified": bool(all_verified),
            "verification_reason_counts": {
                reason: verification_reasons.count(reason) for reason in sorted(set(verification_reasons))
            },
            "exhaustion_diagnostics": diagnostics,
            "dt_ms": round((time.monotonic() - started) * 1000.0, 3),
        },
        "errors": [] if ok else ([status] if status not in success_statuses else ["generated_step_verification_failed"]),
        "safety": {
            "db_reads": False,
            "db_writes": False,
            "policy_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "runner_starts": False,
        },
    })

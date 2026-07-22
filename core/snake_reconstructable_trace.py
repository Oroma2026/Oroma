#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_reconstructable_trace.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Reconstructable Trace – Canonical State and Verification Core
# Version: v1.0.0-snake-trace-reconstructable
# Stand:   2026-07-13
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul definiert die einzige kanonische Wahrheit für das Trace-Schema
# ``snake_trace:reconstructable_v1``. Es trennt die konkrete physikalische
# Spielwelt von der abstrahierten Policy-Sicht ``snake:pro_v2`` und von der
# zeitlichen Credit-Zuordnung ``snake_credit:direct_step_window_v1``.
#
# Das Modul wird gemeinsam verwendet von:
#   • tools/snake_daily_runner.py zur verlustfreien Trace-Erzeugung,
#   • tools/snake_reconstructable_trace_audit.py zur read-only Verifikation,
#   • dem späteren Targeted-Evidence-Runner zur exakten Zustandsinjektion.
#
# ARCHITEKTURVERTRAG
# ─────────────────
#   • Keine Datenbankzugriffe.
#   • Keine Policy- oder Evidence-Mutation.
#   • Keine Zufallsentscheidungen.
#   • Keine Interpretation abstrakter State-Hashes als konkreter Zustand.
#   • Prozess- und plattformstabile SHA-256-Digests über kanonisches JSON.
#   • Fail-closed bei unbekannten Schemata, unvollständigen Zuständen oder
#     Abweichungen zwischen aufgezeichnetem und rekonstruiertem Nachzustand.
#
# REKONSTRUKTIONSMODELL
# ─────────────────────
# Jeder Step speichert den vollständigen konkreten Zustand unmittelbar vor der
# Aktion. Der Nachzustand wird kompakt gespeichert und über seinen Digest
# verifiziert. Bei einem Futterereignis ist die neu erzeugte Futterposition
# Teil des aufgezeichneten Nachzustands. Sie wird bei der Verifikation als
# beobachtetes Umweltergebnis gebunden und nicht aus einem potenziell instabilen
# RNG-Aufrufpfad neu geraten.
#
# Ein Episode-Seed bleibt als zusätzlicher Reproduktionsanker erhalten. Er ist
# jedoch ausdrücklich kein Ersatz für den vollständigen konkreten Vorzustand.
# =============================================================================

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TRACE_SCHEMA = "snake_trace:reconstructable_v1"
STATE_SCHEMA = "snake:pro_v2"
CREDIT_SCHEMA = "snake_credit:direct_step_window_v1"

# Absolute direction encoding: 0=up, 1=right, 2=down, 3=left.
DIRS: Tuple[Tuple[int, int], ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))


@dataclass(frozen=True)
class StepVerification:
    """Deterministic result of verifying one reconstructable Snake step."""

    ok: bool
    reason: str
    before_digest_ok: bool
    after_digest_ok: bool
    transition_ok: bool
    reconstructed_after: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "reason": str(self.reason),
            "before_digest_ok": bool(self.before_digest_ok),
            "after_digest_ok": bool(self.after_digest_ok),
            "transition_ok": bool(self.transition_ok),
            "reconstructed_after": dict(self.reconstructed_after),
        }


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 digest over canonical compact JSON."""
    raw = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def normalize_body(body: Sequence[Sequence[int]]) -> List[List[int]]:
    normalized: List[List[int]] = []
    for cell in body:
        if not isinstance(cell, (list, tuple)) or len(cell) != 2:
            raise ValueError("snake_body_cell_invalid")
        normalized.append([int(cell[0]), int(cell[1])])
    if not normalized:
        raise ValueError("snake_body_empty")
    return normalized


def state_payload(
    snake_body: Sequence[Sequence[int]],
    direction: int,
    food: Sequence[int],
    grid_w: int,
    grid_h: int,
    steps_since_food: int,
) -> Dict[str, Any]:
    if not isinstance(food, (list, tuple)) or len(food) != 2:
        raise ValueError("food_invalid")
    w = int(grid_w)
    h = int(grid_h)
    if w <= 1 or h <= 1:
        raise ValueError("grid_invalid")
    return {
        "grid_w": w,
        "grid_h": h,
        "snake_body": normalize_body(snake_body),
        "direction": int(direction) % 4,
        "food": [int(food[0]), int(food[1])],
        "steps_since_food": max(0, int(steps_since_food)),
    }


def state_digest(
    snake_body: Sequence[Sequence[int]],
    direction: int,
    food: Sequence[int],
    grid_w: int,
    grid_h: int,
    steps_since_food: int,
) -> str:
    return canonical_sha256({
        "trace_schema": TRACE_SCHEMA,
        **state_payload(
            snake_body,
            direction,
            food,
            grid_w,
            grid_h,
            steps_since_food,
        ),
    })


def compact_after_state(
    snake_body: Sequence[Sequence[int]],
    direction: int,
    food: Sequence[int],
    grid_w: int,
    grid_h: int,
    steps_since_food: int,
    *,
    terminal: bool,
    event: Optional[str],
) -> Dict[str, Any]:
    body = normalize_body(snake_body)
    return {
        "head": list(body[0]),
        "direction": int(direction) % 4,
        "food": [int(food[0]), int(food[1])],
        "length": int(len(body)),
        "steps_since_food": max(0, int(steps_since_food)),
        "terminal": bool(terminal),
        "event": str(event) if event else None,
        "state_digest": state_digest(
            body,
            direction,
            food,
            grid_w,
            grid_h,
            steps_since_food,
        ),
    }


def relative_to_absolute(direction: int, relative_action: int) -> int:
    d = int(direction) % 4
    action = int(relative_action)
    if action == 0:
        return d
    if action == 1:
        return (d - 1) % 4
    if action == 2:
        return (d + 1) % 4
    raise ValueError("relative_action_not_supported")


def next_head(head: Sequence[int], absolute_direction: int) -> List[int]:
    if not isinstance(head, (list, tuple)) or len(head) != 2:
        raise ValueError("head_invalid")
    dx, dy = DIRS[int(absolute_direction) % 4]
    return [int(head[0]) + int(dx), int(head[1]) + int(dy)]


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
    if [x, y] in blocked:
        return "self"
    return None


def reconstruct_after_from_step(step: Mapping[str, Any]) -> Dict[str, Any]:
    """Reconstruct one transition from the recorded concrete pre-state.

    For food consumption, the new food position is read from the recorded
    compact after-state. This binds the observed environment transition without
    pretending that a seed alone reproduces all historical RNG consumption.
    """
    if str(step.get("trace_schema") or "") != TRACE_SCHEMA:
        raise ValueError("trace_schema_not_supported")
    ctx = step.get("trace_context")
    if not isinstance(ctx, Mapping):
        raise ValueError("trace_context_missing")
    before = ctx.get("before")
    transition = ctx.get("transition")
    after = ctx.get("after")
    if not isinstance(before, Mapping):
        raise ValueError("trace_before_missing")
    if not isinstance(transition, Mapping):
        raise ValueError("trace_transition_missing")
    if not isinstance(after, Mapping):
        raise ValueError("trace_after_missing")

    body = normalize_body(before.get("snake_body") or [])
    direction = int(before.get("direction", 0)) % 4
    food = before.get("food")
    grid_w = int(before.get("grid_w", 0))
    grid_h = int(before.get("grid_h", 0))
    hunger = int(before.get("steps_since_food", 0))
    if not isinstance(food, (list, tuple)) or len(food) != 2:
        raise ValueError("trace_before_food_invalid")

    action = int(transition.get("relative_action", step.get("a", -1)))
    absolute = relative_to_absolute(direction, action)
    candidate = next_head(body[0], absolute)
    ate = candidate == [int(food[0]), int(food[1])]
    collision = _collision_kind(body, candidate, grid_w, grid_h, ate=ate)

    if collision:
        reconstructed_body = body
        reconstructed_direction = direction
        reconstructed_food = [int(food[0]), int(food[1])]
        reconstructed_hunger = hunger
    else:
        reconstructed_body = [candidate] + body
        reconstructed_direction = absolute
        if ate:
            after_food = after.get("food")
            if not isinstance(after_food, (list, tuple)) or len(after_food) != 2:
                raise ValueError("trace_after_food_invalid")
            reconstructed_food = [int(after_food[0]), int(after_food[1])]
            reconstructed_hunger = 0
        else:
            reconstructed_body.pop()
            reconstructed_food = [int(food[0]), int(food[1])]
            reconstructed_hunger = hunger + 1

    return {
        "snake_body": reconstructed_body,
        "direction": reconstructed_direction,
        "food": reconstructed_food,
        "grid_w": grid_w,
        "grid_h": grid_h,
        "steps_since_food": reconstructed_hunger,
        "absolute_direction": absolute,
        "next_head": candidate,
        "ate": bool(ate),
        "collision": collision,
        "applied": collision is None,
        "state_digest": state_digest(
            reconstructed_body,
            reconstructed_direction,
            reconstructed_food,
            grid_w,
            grid_h,
            reconstructed_hunger,
        ),
    }


def verify_step(step: Mapping[str, Any]) -> StepVerification:
    """Verify one recorded step and fail closed on every inconsistency."""
    try:
        if str(step.get("trace_schema") or "") != TRACE_SCHEMA:
            return StepVerification(False, "trace_schema_not_supported", False, False, False, {})
        ctx = step.get("trace_context")
        if not isinstance(ctx, Mapping):
            return StepVerification(False, "trace_context_missing", False, False, False, {})
        before = ctx.get("before")
        transition = ctx.get("transition")
        after = ctx.get("after")
        if not isinstance(before, Mapping) or not isinstance(transition, Mapping) or not isinstance(after, Mapping):
            return StepVerification(False, "trace_component_missing", False, False, False, {})

        before_expected = state_digest(
            before.get("snake_body") or [],
            int(before.get("direction", 0)),
            before.get("food") or [],
            int(before.get("grid_w", 0)),
            int(before.get("grid_h", 0)),
            int(before.get("steps_since_food", 0)),
        )
        before_ok = str(before.get("state_digest") or "") == before_expected
        reconstructed = reconstruct_after_from_step(step)

        transition_ok = (
            int(transition.get("absolute_direction", -1)) == int(reconstructed["absolute_direction"])
            and list(transition.get("next_head") or []) == list(reconstructed["next_head"])
            and bool(transition.get("ate", False)) == bool(reconstructed["ate"])
            and (transition.get("collision") or None) == reconstructed["collision"]
            and bool(transition.get("applied", False)) == bool(reconstructed["applied"])
        )
        after_ok = (
            str(after.get("state_digest") or "") == str(reconstructed["state_digest"])
            and list(after.get("head") or []) == list(reconstructed["snake_body"][0])
            and int(after.get("direction", -1)) == int(reconstructed["direction"])
            and list(after.get("food") or []) == list(reconstructed["food"])
            and int(after.get("length", -1)) == len(reconstructed["snake_body"])
            and int(after.get("steps_since_food", -1)) == int(reconstructed["steps_since_food"])
        )
        if not before_ok:
            reason = "recorded_before_state_digest_mismatch"
        elif not transition_ok:
            reason = "recorded_transition_mismatch"
        elif not after_ok:
            reason = "reconstructed_after_state_digest_mismatch"
        else:
            reason = "verified"
        return StepVerification(
            ok=bool(before_ok and transition_ok and after_ok),
            reason=reason,
            before_digest_ok=before_ok,
            after_digest_ok=after_ok,
            transition_ok=transition_ok,
            reconstructed_after=reconstructed,
        )
    except Exception as exc:
        return StepVerification(
            False,
            f"trace_verification_error:{type(exc).__name__}:{exc}",
            False,
            False,
            False,
            {},
        )

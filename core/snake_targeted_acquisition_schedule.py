#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_targeted_acquisition_schedule.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Targeted Acquisition Schedule – Bounded Multi-Horizon Runner
# Version: v0.1.0-bounded-horizon-schedule
# Stand:   2026-07-14
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul legt eine kleine, vollständig deterministische Versuchskaskade
# über den bereits verifizierten Snake Targeted Evidence Runner. Es löst das
# Liveness-Problem eines einzelnen zu kurzen Horizonts, ohne unbeschränkte
# Simulationen, freie Retry-Schleifen oder eine Veränderung der Credit-Semantik
# einzuführen.
#
# FESTER V1-PLAN
# ──────────────
#   Versuch 0: 12 Schritte
#   Versuch 1: 24 Schritte
#   Versuch 2: 48 Schritte
#
# Jeder Versuch besitzt einen eigenen ``attempt_index``. Dieser Index fließt
# bereits im bestehenden Runner in Experiment-Seed, Experiment-ID und Evidence-
# Digest ein. Die einzelnen Beobachtungen bleiben dadurch reproduzierbar und
# auditierbar. Sobald ein Versuch direkte Evidence erzeugt, endet die Kaskade.
# Bleiben alle Versuche ohne Direct Outcome, entsteht der legitime Terminal-
# zustand ``exhausted_no_direct_outcome``. Es wird kein positives oder negatives
# Ergebnis erfunden.
#
# ARCHITEKTURGRENZEN
# ─────────────────
#   • Kein Datenbankzugriff in diesem Core-Modul.
#   • Keine Persistenz, Queue-, Promotion- oder Policy-Mutation.
#   • Keine Änderung am Continuation Protocol oder Credit Assignment.
#   • Keine dynamische Horizon-Vergrößerung außerhalb des versionierten Plans.
#   • Fail-closed bei ungültigem Plan oder fehlgeschlagenem Einzelversuch.
# =============================================================================

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.snake_reconstructable_trace import canonical_sha256
from core.snake_targeted_evidence_runner import simulate_targeted_observation

VERSION = "v0.1.0-bounded-horizon-schedule"
SCHEDULE_ID = "snake_targeted_acquisition:bounded_horizon_v1"
DEFAULT_HORIZONS: Tuple[int, int, int] = (12, 24, 48)
MAX_ATTEMPTS = 3
MAX_HORIZON = 48


def normalize_horizons(values: Optional[Iterable[int]] = None) -> Tuple[int, ...]:
    """Validate and canonicalize a bounded, strictly increasing horizon plan."""
    raw = tuple(int(v) for v in (values if values is not None else DEFAULT_HORIZONS))
    if not raw:
        raise ValueError("acquisition_schedule_empty")
    if len(raw) > MAX_ATTEMPTS:
        raise ValueError("acquisition_schedule_attempt_budget_exceeded")
    if any(v <= 0 for v in raw):
        raise ValueError("acquisition_schedule_horizon_invalid")
    if any(raw[i] >= raw[i + 1] for i in range(len(raw) - 1)):
        raise ValueError("acquisition_schedule_not_strictly_increasing")
    if max(raw) > MAX_HORIZON:
        raise ValueError("acquisition_schedule_max_horizon_exceeded")
    return raw


def build_schedule_descriptor(
    horizons: Optional[Sequence[int]] = None,
    max_runtime_sec_per_attempt: float = 2.0,
) -> Tuple[Dict[str, Any], str]:
    """Return the canonical schedule payload and digest without running simulation."""
    plan = normalize_horizons(horizons)
    payload = {
        "schedule_id": SCHEDULE_ID,
        "schedule_version": VERSION,
        "horizons": list(plan),
        "max_attempts": len(plan),
        "max_horizon": max(plan),
        "stop_on_first_direct_outcome": True,
        "max_runtime_sec_per_attempt": float(max_runtime_sec_per_attempt),
    }
    return payload, canonical_sha256(payload)


def run_bounded_acquisition_schedule(
    *,
    source_snapchain_id: int,
    source_step_index: int,
    source_chain: Mapping[str, Any],
    source_step: Mapping[str, Any],
    target_action: int,
    learning_intent_lineage: Mapping[str, Any],
    horizons: Optional[Sequence[int]] = None,
    max_runtime_sec_per_attempt: float = 2.0,
    target_len: Optional[int] = None,
    credit_steps: int = 12,
    death_credit_steps: int = 4,
) -> Dict[str, Any]:
    """Run the fixed bounded schedule and stop at the first direct outcome."""
    started = time.monotonic()
    plan = normalize_horizons(horizons)
    schedule_payload, schedule_digest = build_schedule_descriptor(
        plan, max_runtime_sec_per_attempt=max_runtime_sec_per_attempt
    )
    attempts: List[Dict[str, Any]] = []
    selected_attempt: Optional[Dict[str, Any]] = None

    for attempt_index, horizon in enumerate(plan):
        result = simulate_targeted_observation(
            source_snapchain_id=int(source_snapchain_id),
            source_step_index=int(source_step_index),
            source_chain=source_chain,
            source_step=source_step,
            target_action=int(target_action),
            attempt_index=int(attempt_index),
            max_steps=int(horizon),
            max_runtime_sec=float(max_runtime_sec_per_attempt),
            target_len=target_len,
            credit_steps=int(credit_steps),
            death_credit_steps=int(death_credit_steps),
            learning_intent_lineage=learning_intent_lineage,
            acquisition_schedule={**schedule_payload, "schedule_digest": schedule_digest},
        ).to_dict()
        preview = result.get("evidence_preview") if isinstance(result.get("evidence_preview"), dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        attempt = {
            "attempt_index": int(attempt_index),
            "max_steps": int(horizon),
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or ""),
            "steps_executed": int(summary.get("steps_executed") or 0),
            "target_step_direct_outcome": summary.get("target_step_direct_outcome"),
            "ready_for_replay_capability": bool(summary.get("ready_for_replay_capability")),
            "all_steps_verified": bool(summary.get("all_steps_verified")),
            "exhaustion_diagnostics": dict(summary.get("exhaustion_diagnostics") or {}),
            "experiment_id": str(preview.get("experiment_id") or ""),
            "evidence_digest": str(preview.get("evidence_digest") or ""),
            "errors": list(result.get("errors") or []),
        }
        attempts.append(attempt)

        if not result.get("ok"):
            return {
                "ok": False,
                "status": "acquisition_attempt_failed",
                "version": VERSION,
                "schedule": schedule_payload,
                "schedule_digest": schedule_digest,
                "attempts": attempts,
                "selected_attempt": None,
                "selected_evidence_preview": None,
                "errors": [f"attempt_{attempt_index}:{attempt['status']}"] + attempt["errors"],
                "summary": {
                    "attempts_executed": len(attempts),
                    "attempts_budget": len(plan),
                    "selected_attempt_index": None,
                    "direct_outcome_acquired": False,
                    "exhausted": False,
                    "dt_ms": round((time.monotonic() - started) * 1000.0, 3),
                },
            }

        if attempt["ready_for_replay_capability"]:
            selected_attempt = attempt
            selected_preview = json.loads(json.dumps(preview, ensure_ascii=False))
            selected_preview["acquisition_schedule"].update({
                "selected_attempt_index": int(attempt_index),
                "selected_horizon": int(horizon),
                "attempts_executed": len(attempts),
            })
            selected_preview.pop("evidence_digest", None)
            selected_preview["evidence_digest"] = canonical_sha256(selected_preview)
            return {
                "ok": True,
                "status": "evidence_acquired",
                "version": VERSION,
                "schedule": schedule_payload,
                "schedule_digest": schedule_digest,
                "attempts": attempts,
                "selected_attempt": selected_attempt,
                "selected_evidence_preview": selected_preview,
                "errors": [],
                "summary": {
                    "attempts_executed": len(attempts),
                    "attempts_budget": len(plan),
                    "selected_attempt_index": int(attempt_index),
                    "selected_horizon": int(horizon),
                    "selected_experiment_id": selected_preview.get("experiment_id"),
                    "direct_outcome_acquired": True,
                    "exhausted": False,
                    "dt_ms": round((time.monotonic() - started) * 1000.0, 3),
                },
            }

    return {
        "ok": True,
        "status": "exhausted_no_direct_outcome",
        "version": VERSION,
        "schedule": schedule_payload,
        "schedule_digest": schedule_digest,
        "attempts": attempts,
        "selected_attempt": None,
        "selected_evidence_preview": None,
        "errors": [],
        "summary": {
            "attempts_executed": len(attempts),
            "attempts_budget": len(plan),
            "selected_attempt_index": None,
            "direct_outcome_acquired": False,
            "exhausted": True,
            "terminal_reason": "bounded_horizon_schedule_completed_without_direct_outcome",
            "dt_ms": round((time.monotonic() - started) * 1000.0, 3),
        },
    }


__all__ = [
    "VERSION",
    "SCHEDULE_ID",
    "DEFAULT_HORIZONS",
    "normalize_horizons",
    "build_schedule_descriptor",
    "run_bounded_acquisition_schedule",
]

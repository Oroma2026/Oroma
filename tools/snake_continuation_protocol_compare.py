#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_continuation_protocol_compare.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Continuation Protocol v1/v2 Read-Only Comparison
# Version: v0.1.0-v1-v2-protocol-comparison
# Stand:   2026-07-16
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Werkzeug führt für identische Promotions, identische reconstructable
# Quellen und identische Horizonte einen kontrollierten A/B-Vergleich zwischen
# ``safe_deterministic_v1`` und ``safe_food_directed_v2`` aus. Es verändert
# keine Daten und bewertet nur harte Direct Outcomes sowie Diagnosemetriken.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.snake_targeted_acquisition_schedule import run_bounded_acquisition_schedule
from core.snake_targeted_acquisition_schedule_v2 import run_food_directed_acquisition_schedule
from core.snake_targeted_source_locator import locate_source
from tools.snake_targeted_evidence_runner import _load_promotion, _load_source, _verify_promotion_source_binding

VERSION = "v0.1.0-v1-v2-protocol-comparison"


def _parse_ids(text: str) -> List[int]:
    ids = [int(part.strip()) for part in str(text or "").split(",") if part.strip()]
    if not ids:
        raise ValueError("promotion_ids_missing")
    return ids


def _attempt_view(attempt: Dict[str, Any]) -> Dict[str, Any]:
    diag = dict(attempt.get("exhaustion_diagnostics") or {})
    return {
        "attempt_index": int(attempt.get("attempt_index") or 0),
        "max_steps": int(attempt.get("max_steps") or 0),
        "status": str(attempt.get("status") or ""),
        "steps_executed": int(attempt.get("steps_executed") or 0),
        "ready_for_replay_capability": bool(attempt.get("ready_for_replay_capability")),
        "minimum_food_distance": diag.get("minimum_food_distance"),
        "best_food_distance_gain": diag.get("best_food_distance_gain"),
        "food_reached": bool(diag.get("food_reached")),
        "death_reached": bool(diag.get("death_reached")),
        "loop_detected": bool(diag.get("loop_detected")),
        "minimum_wall_clearance": diag.get("minimum_wall_clearance"),
        "minimum_body_clearance": diag.get("minimum_body_clearance"),
    }


def _compare_one(db_path: str, promotion_id: int, scan_limit: int) -> Dict[str, Any]:
    promotion = _load_promotion(db_path, int(promotion_id))
    locator = locate_source(db_path, state_hash=str(promotion["state_hash"]), scan_limit=int(scan_limit))
    if not locator.get("found"):
        return {"promotion_id": int(promotion_id), "ok": True, "status": "source_state_missing", "source": None}
    source = dict(locator["source"])
    loaded = _load_source(db_path, int(source["source_snapchain_id"]))
    steps = loaded["chain"].get("steps")
    if not isinstance(steps, list):
        raise ValueError("source_steps_missing")
    step = steps[int(source["source_step_index"])]
    if not isinstance(step, dict):
        raise ValueError("source_step_not_object")
    target_action = int(promotion["primary_action"])
    _verify_promotion_source_binding(promotion, step, target_action)

    common = {
        "source_snapchain_id": int(source["source_snapchain_id"]),
        "source_step_index": int(source["source_step_index"]),
        "source_chain": loaded["chain"],
        "source_step": step,
        "target_action": target_action,
        "learning_intent_lineage": promotion,
        "target_len": (loaded["chain"].get("runner_config") or {}).get("target_len"),
    }
    v1 = run_bounded_acquisition_schedule(**common)
    v2 = run_food_directed_acquisition_schedule(**common)
    return {
        "promotion_id": int(promotion_id),
        "ok": bool(v1.get("ok") and v2.get("ok")),
        "status": "compared",
        "promotion": promotion,
        "source": source,
        "v1": {
            "status": v1.get("status"),
            "schedule_digest": v1.get("schedule_digest"),
            "attempts": [_attempt_view(a) for a in (v1.get("attempts") or [])],
        },
        "v2": {
            "status": v2.get("status"),
            "schedule_digest": v2.get("schedule_digest"),
            "attempts": [_attempt_view(a) for a in (v2.get("attempts") or [])],
            "selected_evidence_preview": v2.get("selected_evidence_preview"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"))
    parser.add_argument("--promotion-ids", required=True)
    parser.add_argument("--source-scan-limit", type=int, default=5000)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "results": [],
        "errors": [],
        "safety": {
            "db_reads": True,
            "db_writes": False,
            "lifecycle_writes": False,
            "evidence_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "policy_writes": False,
            "runner_starts": False,
            "db_open_mode": "read_only_uri_mode_ro",
        },
    }
    try:
        for promotion_id in _parse_ids(args.promotion_ids):
            result["results"].append(_compare_one(args.db, promotion_id, args.source_scan_limit))
        result["ok"] = all(bool(item.get("ok")) for item in result["results"])
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"{type(exc).__name__}:{exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

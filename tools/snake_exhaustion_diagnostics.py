#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_exhaustion_diagnostics.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Exhaustion Diagnostics – Read-Only Promotion Trajectory Audit
# Version: v0.1.0-exhaustion-diagnostics
# Stand:   2026-07-15
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Werkzeug vermisst bereits bekannte promotion-bound Snake-Zustände
# erneut mit dem unveränderten bounded_horizon_v1-Plan (12/24/48), um die
# Ursache von ``exhausted_no_direct_outcome`` physikalisch sichtbar zu machen.
# Es erzeugt ausschließlich Diagnosewerte wie Food-Distanz, Positionsvielfalt,
# Schleifenhinweise und minimale Wall-/Body-Clearance.
#
# SICHERHEITSVERTRAG
# ──────────────────
#   • Datenbank ausschließlich über SQLite URI ``mode=ro`` lesen.
#   • Keine Lifecycle-, Event-, Evidence-, Outcome-, Promotion- oder Policy-
#     Persistenz.
#   • Keine Änderung der Schedule-, Continuation- oder Credit-Semantik.
#   • Keine automatische Reacquisition-Generation.
#   • Exakte Source- und Before-Digest-Verifikation vor jeder Simulation.
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

from core.snake_targeted_acquisition_schedule import DEFAULT_HORIZONS, run_bounded_acquisition_schedule
from core.snake_targeted_source_locator import locate_source
from tools.snake_targeted_evidence_runner import _load_promotion, _load_source, _verify_promotion_source_binding

VERSION = "v0.1.0-exhaustion-diagnostics"


def _parse_ids(text: str) -> List[int]:
    values = []
    for part in str(text or "").split(","):
        part = part.strip()
        if part:
            values.append(int(part))
    if not values:
        raise ValueError("promotion_ids_missing")
    return values


def _diagnose_one(db_path: str, promotion_id: int, scan_limit: int) -> Dict[str, Any]:
    promotion = _load_promotion(db_path, int(promotion_id))
    locator = locate_source(db_path, state_hash=str(promotion["state_hash"]), scan_limit=int(scan_limit))
    if not locator.get("found"):
        return {
            "promotion_id": int(promotion_id),
            "ok": True,
            "status": "source_state_missing",
            "promotion": promotion,
            "source_locator_summary": locator.get("summary"),
            "attempts": [],
        }
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
    before = ((step.get("trace_context") or {}).get("before") or {})
    if str(before.get("state_digest") or "") != str(source.get("source_before_state_digest") or ""):
        raise ValueError("source_before_state_digest_changed")
    schedule = run_bounded_acquisition_schedule(
        source_snapchain_id=int(source["source_snapchain_id"]),
        source_step_index=int(source["source_step_index"]),
        source_chain=loaded["chain"],
        source_step=step,
        target_action=target_action,
        learning_intent_lineage=promotion,
        horizons=DEFAULT_HORIZONS,
        target_len=(loaded["chain"].get("runner_config") or {}).get("target_len"),
    )
    return {
        "promotion_id": int(promotion_id),
        "ok": bool(schedule.get("ok")),
        "status": str(schedule.get("status") or ""),
        "state_hash": str(promotion.get("state_hash") or ""),
        "primary_action": str(promotion.get("primary_action") or ""),
        "source": source,
        "attempts": [
            {
                "attempt_index": int(a.get("attempt_index") or 0),
                "max_steps": int(a.get("max_steps") or 0),
                "status": str(a.get("status") or ""),
                "steps_executed": int(a.get("steps_executed") or 0),
                "diagnostics": dict(a.get("exhaustion_diagnostics") or {}),
            }
            for a in (schedule.get("attempts") or [])
        ],
        "summary": schedule.get("summary"),
        "errors": list(schedule.get("errors") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"))
    parser.add_argument("--promotion-ids", required=True, help="Comma-separated promotion ids")
    parser.add_argument("--source-scan-limit", type=int, default=5000)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "promotion_ids": [],
        "results": [],
        "errors": [],
        "safety": {
            "db_reads": True,
            "db_writes": False,
            "policy_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "lifecycle_writes": False,
            "runner_starts": False,
            "db_open_mode": "read_only_uri_mode_ro",
        },
    }
    try:
        ids = _parse_ids(args.promotion_ids)
        result["promotion_ids"] = ids
        for promotion_id in ids:
            result["results"].append(_diagnose_one(args.db, promotion_id, args.source_scan_limit))
        result["ok"] = all(bool(item.get("ok")) for item in result["results"])
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"{type(exc).__name__}:{exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

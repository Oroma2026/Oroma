#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_targeted_acquisition_worker.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Targeted Acquisition Worker – Promotion→Source→Guard→Schedule
# Version: v0.1.0-lifecycle-aware-worker
# Stand:   2026-07-15
# =============================================================================
#
# ZWECK
# -----
# Verbindet genau einen bestehenden Promotion-Auftrag deterministisch mit einer
# reconstructable Source, dem persistenten Lifecycle-Guard und der begrenzten
# 12→24→48-Akquisition. Der Worker startet niemals mehr als eine Promotion pro
# Aufruf und schreibt weder Policy, Outcome Queue noch Promotion. Lifecycle-
# Persistenz ist explizit gegated und erfolgt ausschließlich über DBWriter.
# =============================================================================
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.snake_targeted_acquisition_schedule import (
    DEFAULT_HORIZONS,
    VERSION as SCHEDULE_VERSION,
    build_schedule_descriptor,
    run_bounded_acquisition_schedule,
)
from core.snake_targeted_source_locator import locate_source
from core.targeted_acquisition_lifecycle import (
    build_acquisition_id,
    build_source_identity,
    lifecycle_guard,
    compatible_terminal_guard,
    equivalent_protocol_terminal_guard,
    persist_completed_run,
)
from tools.snake_targeted_evidence_runner import (
    _load_promotion,
    _load_source,
    _verify_promotion_source_binding,
)

VERSION = "v0.1.0-lifecycle-aware-worker"
LIFECYCLE_CONFIRM_REQUIRED = "PERSIST_TARGETED_ACQUISITION_LIFECYCLE_V1"
MAX_PROMOTIONS_PER_RUN = 1


def _promotion_ids(db_path: str, explicit_id: int | None, limit: int) -> List[int]:
    if explicit_id is not None:
        return [int(explicit_id)]
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT id
              FROM gap_policy_promotion_queue
             WHERE namespace='game:snake'
               AND target='replay'
               AND promotion_bucket='promotion_candidate_replay'
               AND status='promotion_review'
             ORDER BY id DESC
             LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        con.close()


def _source_hwm(db_path: str) -> int:
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT COALESCE(MAX(id),0) FROM snapchains").fetchone()[0] or 0)
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"))
    p.add_argument("--promotion-id", type=int)
    p.add_argument("--promotion-scan-limit", type=int, default=2500)
    p.add_argument("--source-scan-limit", type=int, default=5000)
    p.add_argument("--horizons", default=",".join(str(v) for v in DEFAULT_HORIZONS))
    p.add_argument("--max-runtime-sec-per-attempt", type=float, default=2.0)
    p.add_argument("--credit-steps", type=int, default=12)
    p.add_argument("--death-credit-steps", type=int, default=4)
    p.add_argument("--write-lifecycle", action="store_true")
    p.add_argument("--lifecycle-confirm", default="")
    p.add_argument("--reacquisition-generation", type=int, default=0)
    p.add_argument("--dbwriter-timeout-ms", type=int, default=60000)
    p.add_argument("--pretty", action="store_true")
    a = p.parse_args()

    horizons = tuple(int(v.strip()) for v in str(a.horizons).split(",") if v.strip())
    schedule_payload, schedule_digest = build_schedule_descriptor(
        horizons, max_runtime_sec_per_attempt=a.max_runtime_sec_per_attempt
    )
    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "status": "no_eligible_promotion",
        "max_promotions_per_run": MAX_PROMOTIONS_PER_RUN,
        "promotions_considered": 0,
        "simulation_started": False,
        "attempts_executed": 0,
        "errors": [],
    }
    try:
        ids = _promotion_ids(a.db, a.promotion_id, a.promotion_scan_limit)
        result["promotion_ids_loaded"] = len(ids)
        selected = None
        terminal_seen = []
        missing_sources = []
        for promotion_id in ids:
            result["promotions_considered"] += 1
            promotion = _load_promotion(a.db, promotion_id)
            locator = locate_source(
                a.db,
                state_hash=str(promotion["state_hash"]),
                scan_limit=a.source_scan_limit,
            )
            if not locator.get("found"):
                missing_sources.append({"promotion_id": promotion_id, "reason": locator.get("reason")})
                if a.promotion_id is not None:
                    result.update({"status": "source_state_missing", "promotion": promotion, "source_locator": locator})
                    break
                continue
            source = dict(locator["source"])
            source_identity = build_source_identity(
                source["source_snapchain_id"],
                source["source_step_index"],
                source["source_before_state_digest"],
            )
            acquisition_id = build_acquisition_id(
                promotion_id=promotion_id,
                promotion_signature=str(promotion["promotion_signature"]),
                acquisition_protocol="bounded_horizon",
                protocol_version=SCHEDULE_VERSION,
                schedule_digest=schedule_digest,
                source_identity=source_identity,
                reacquisition_generation=a.reacquisition_generation,
            )
            guard = lifecycle_guard(a.db, acquisition_id)
            if not guard.get("already_terminal"):
                equivalent_guard = equivalent_protocol_terminal_guard(
                    a.db,
                    promotion_id=promotion_id,
                    promotion_signature=str(promotion["promotion_signature"]),
                    acquisition_protocol="bounded_horizon",
                    protocol_version=SCHEDULE_VERSION,
                    schedule_digest=schedule_digest,
                    reacquisition_generation=a.reacquisition_generation,
                )
                if equivalent_guard.get("matched"):
                    equivalent_record = equivalent_guard.get("record") or {}
                    guard = {
                        "allowed": False,
                        "reason": "terminal_equivalent_protocol_found",
                        "already_terminal": True,
                        "status": equivalent_record.get("status"),
                        "record": equivalent_record,
                        "computed_acquisition_id": acquisition_id,
                        "equivalent_protocol_terminal": True,
                        "source_identity_changed": str(equivalent_record.get("source_identity") or "") != source_identity,
                    }
            if not guard.get("already_terminal"):
                legacy_guard = compatible_terminal_guard(
                    a.db,
                    promotion_id=promotion_id,
                    source_snapchain_id=int(source["source_snapchain_id"]),
                    source_step_index=int(source["source_step_index"]),
                    acquisition_protocol="bounded_horizon",
                    protocol_version=SCHEDULE_VERSION,
                    schedule_digest=schedule_digest,
                    reacquisition_generation=a.reacquisition_generation,
                )
                if legacy_guard.get("matched"):
                    legacy_record = legacy_guard.get("record") or {}
                    guard = {
                        "allowed": False,
                        "reason": "already_terminal_legacy_identity",
                        "already_terminal": True,
                        "status": legacy_record.get("status"),
                        "record": legacy_record,
                        "computed_acquisition_id": acquisition_id,
                        "legacy_identity_compatibility": True,
                    }
            candidate = {
                "promotion": promotion,
                "source": source,
                "source_locator": locator,
                "source_identity": source_identity,
                "acquisition_id": acquisition_id,
                "lifecycle_guard": guard,
            }
            if guard.get("already_terminal"):
                terminal_seen.append({
                    "promotion_id": promotion_id,
                    "acquisition_id": acquisition_id,
                    "status": guard.get("status"),
                })
                if a.promotion_id is not None:
                    result.update({
                        "status": "already_terminal",
                        "promotion": promotion,
                        "source": source,
                        "source_locator": locator,
                        "acquisition_id": acquisition_id,
                        "lifecycle_guard": guard,
                    })
                    break
                continue
            selected = candidate
            break

        result["terminal_candidates_skipped"] = terminal_seen
        result["source_missing_candidates"] = missing_sources
        if selected is not None:
            promotion = selected["promotion"]
            source = selected["source"]
            loaded = _load_source(a.db, source["source_snapchain_id"])
            steps = loaded["chain"].get("steps")
            if not isinstance(steps, list):
                raise ValueError("source_steps_missing")
            step = steps[int(source["source_step_index"])]
            if not isinstance(step, dict):
                raise ValueError("source_step_not_object")
            target_action = int(promotion["primary_action"])
            _verify_promotion_source_binding(promotion, step, target_action)
            trace_context = step.get("trace_context") if isinstance(step.get("trace_context"), dict) else {}
            before = trace_context.get("before") if isinstance(trace_context.get("before"), dict) else {}
            live_digest = str(before.get("state_digest") or "")
            if live_digest != str(source["source_before_state_digest"]):
                raise ValueError("source_before_state_digest_changed")
            result["simulation_started"] = True
            schedule = run_bounded_acquisition_schedule(
                source_snapchain_id=int(source["source_snapchain_id"]),
                source_step_index=int(source["source_step_index"]),
                source_chain=loaded["chain"],
                source_step=step,
                target_action=target_action,
                learning_intent_lineage=promotion,
                horizons=horizons,
                max_runtime_sec_per_attempt=a.max_runtime_sec_per_attempt,
                target_len=(loaded["chain"].get("runner_config") or {}).get("target_len"),
                credit_steps=a.credit_steps,
                death_credit_steps=a.death_credit_steps,
            )
            result.update({
                "status": str(schedule.get("status") or ""),
                "promotion": promotion,
                "source": source,
                "source_locator": selected["source_locator"],
                "acquisition_id": selected["acquisition_id"],
                "source_identity": selected["source_identity"],
                "lifecycle_guard": selected["lifecycle_guard"],
                "schedule": schedule,
                "attempts_executed": int((schedule.get("summary") or {}).get("attempts_executed") or 0),
            })
            lifecycle_write = {"requested": bool(a.write_lifecycle), "write_attempted": False, "reason": "write_lifecycle_not_requested"}
            if a.write_lifecycle:
                if a.lifecycle_confirm != LIFECYCLE_CONFIRM_REQUIRED:
                    lifecycle_write = {"requested": True, "ok": False, "write_attempted": False, "reason": "lifecycle_confirm_phrase_mismatch"}
                    result["ok"] = False
                    result["errors"].append("lifecycle_confirm_phrase_mismatch")
                elif schedule.get("status") not in {"evidence_acquired", "exhausted_no_direct_outcome"}:
                    lifecycle_write = {"requested": True, "ok": False, "write_attempted": False, "reason": "schedule_not_terminal"}
                else:
                    summary = schedule.get("summary") if isinstance(schedule.get("summary"), dict) else {}
                    record = {
                        "acquisition_id": selected["acquisition_id"],
                        "promotion_id": int(promotion["promotion_id"]),
                        "promotion_signature": str(promotion["promotion_signature"]),
                        "request_signature": str(promotion["request_signature"]),
                        "namespace": str(promotion["namespace"]),
                        "state_schema": str(promotion["state_schema"]),
                        "state_hash": str(promotion["state_hash"]),
                        "primary_action": str(target_action),
                        "source_snapchain_id": int(source["source_snapchain_id"]),
                        "source_step_index": int(source["source_step_index"]),
                        "source_before_state_digest": str(source["source_before_state_digest"]),
                        "source_identity": selected["source_identity"],
                        "acquisition_protocol": "bounded_horizon",
                        "protocol_version": SCHEDULE_VERSION,
                        "schedule_id": str(schedule_payload["schedule_id"]),
                        "schedule_digest": schedule_digest,
                        "attempts_budget": int(summary.get("attempts_budget") or 0),
                        "attempts_executed": int(summary.get("attempts_executed") or 0),
                        "selected_attempt_index": summary.get("selected_attempt_index"),
                        "direct_outcome_acquired": bool(summary.get("direct_outcome_acquired")),
                        "terminal_reason": str(summary.get("terminal_reason") or schedule.get("status") or ""),
                        "source_high_water_mark": f"snapchains:max_id:{_source_hwm(a.db)}",
                        "reacquisition_generation": int(a.reacquisition_generation),
                        "meta": {"attempts": schedule.get("attempts") or [], "source": source},
                    }
                    lifecycle_write = persist_completed_run(
                        record,
                        final_status=str(schedule.get("status")),
                        payload={"summary": summary, "attempts": schedule.get("attempts") or []},
                        timeout_ms=a.dbwriter_timeout_ms,
                    )
                    lifecycle_write["requested"] = True
            result["lifecycle_persistence"] = lifecycle_write

        lifecycle_written = bool((result.get("lifecycle_persistence") or {}).get("ok") and (result.get("lifecycle_persistence") or {}).get("write_attempted"))
        result["safety"] = {
            "db_reads": True,
            "db_writes": lifecycle_written,
            "policy_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "runner_starts": False,
            "db_open_mode": "dbwriter_write_plus_read_only_lookup" if lifecycle_written else "read_only_uri_mode_ro",
            "local_sqlite_write_fallback": False,
            "schema_changes": lifecycle_written,
        }
    except Exception as exc:
        result.update({
            "ok": False,
            "status": "worker_failed",
            "errors": result.get("errors", []) + [f"{type(exc).__name__}:{exc}"],
            "safety": {
                "db_reads": True,
                "db_writes": False,
                "policy_writes": False,
                "queue_writes": False,
                "promotion_writes": False,
                "runner_starts": False,
                "db_open_mode": "read_only_uri_mode_ro",
                "local_sqlite_write_fallback": False,
                "schema_changes": False,
            },
        })
    print(json.dumps(result, ensure_ascii=False, indent=2 if a.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

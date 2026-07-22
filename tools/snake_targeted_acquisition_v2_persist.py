#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_targeted_acquisition_v2_persist.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   G3.1 – Promotion-bound Food-Directed Evidence Persistence
# Version: v0.1.1-g31-dbwriter-gate-diagnostics
# Stand:   2026-07-16
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses eng begrenzte Werkzeug führt für genau eine bestehende Snake-Promotion
# den bereits read-only validierten Schedule ``safe_food_directed_v2`` aus. Nur
# wenn der Schedule ``evidence_acquired`` und eine direkt replay-fähige Evidence
# liefert, darf ein explizit bestätigter DBWriter-Write stattfinden.
#
# ATOMARER SCHREIBVERTRAG
# ----------------------
# Lifecycle-Zeile, zwei Append-only Lifecycle-Events und genau eine immutable
# Targeted-Evidence-SnapChain werden in einer DBWriter-Transaktion geschrieben.
# Der Evidence-Insert ist idempotent über ``snapchains.source_id``. Vor und nach
# der Transaktion wird die vollständige ``experiment_id`` read-only verifiziert,
# sodass eine numerische Source-ID-Kollision fail-closed behandelt wird.
#
# SICHERHEIT
# ----------
#   • Dry Run ist Standard.
#   • Schreiben erfordert --write-g3 und exakte Confirm-Phrase.
#   • Kein direkter SQLite-Write und kein lokaler Fallback.
#   • Keine Outcome-Queue-, Promotion- oder Policy-Mutation.
#   • Maximal eine Evidence-SnapChain pro Aufruf.
# =============================================================================
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.snake_targeted_acquisition_schedule_v2 import (
    DEFAULT_HORIZONS,
    VERSION as SCHEDULE_VERSION,
    build_schedule_descriptor,
    run_food_directed_acquisition_schedule,
)
from core.snake_targeted_evidence_runner import (
    build_persisted_evidence_blob,
    experiment_source_id,
)
from core.snake_targeted_source_locator import locate_source
from core.targeted_acquisition_lifecycle import (
    build_acquisition_id,
    build_source_identity,
    equivalent_protocol_terminal_guard,
    lifecycle_guard,
    persist_completed_run_with_evidence,
)
from tools.snake_targeted_evidence_runner import (
    _decode_blob,
    _load_promotion,
    _load_source,
    _verify_promotion_source_binding,
)

VERSION = "v0.1.1-g31-dbwriter-gate-diagnostics"
CONFIRM_REQUIRED = "PERSIST_TARGETED_ACQUISITION_EVIDENCE_V2"
ACQUISITION_PROTOCOL = "bounded_horizon_food_directed"
MAX_WRITES_PER_RUN = 1


def _ro_connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _source_hwm(db_path: str) -> int:
    con = _ro_connect(db_path)
    try:
        return int(con.execute("SELECT COALESCE(MAX(id),0) FROM snapchains").fetchone()[0] or 0)
    finally:
        con.close()


def _find_evidence(db_path: str, experiment_id: str) -> Dict[str, Any]:
    source_id = int(experiment_source_id(experiment_id))
    con = _ro_connect(db_path)
    try:
        rows = con.execute(
            """SELECT id,source_id,origin,namespace,version,blob
                 FROM snapchains
                WHERE source_id=? AND origin='targeted_evidence_runner:v1' AND namespace='game:snake'
                ORDER BY id ASC""",
            (source_id,),
        ).fetchall()
    finally:
        con.close()
    collisions = []
    for row in rows:
        blob = _decode_blob(row["blob"])
        stored_id = str((blob or {}).get("experiment_id") or "")
        if stored_id == experiment_id:
            return {
                "exists": True,
                "collision": False,
                "snapchain_id": int(row["id"]),
                "source_id": source_id,
            }
        collisions.append({"snapchain_id": int(row["id"]), "experiment_id": stored_id})
    return {
        "exists": False,
        "collision": bool(collisions),
        "snapchain_id": None,
        "source_id": source_id,
        "collision_rows": collisions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"))
    parser.add_argument("--promotion-id", type=int, required=True)
    parser.add_argument("--source-scan-limit", type=int, default=5000)
    parser.add_argument("--horizons", default=",".join(str(v) for v in DEFAULT_HORIZONS))
    parser.add_argument("--max-runtime-sec-per-attempt", type=float, default=2.0)
    parser.add_argument("--credit-steps", type=int, default=12)
    parser.add_argument("--death-credit-steps", type=int, default=4)
    parser.add_argument("--reacquisition-generation", type=int, default=0)
    parser.add_argument("--write-g3", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--dbwriter-timeout-ms", type=int, default=60000)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "mode": "read_only_g3_v2_dry_run",
        "status": "not_started",
        "errors": [],
    }
    write_attempted = False
    try:
        horizons = tuple(int(v.strip()) for v in str(args.horizons).split(",") if v.strip())
        schedule_descriptor, schedule_digest = build_schedule_descriptor(
            horizons,
            max_runtime_sec_per_attempt=args.max_runtime_sec_per_attempt,
        )
        promotion = _load_promotion(args.db, args.promotion_id)
        locator = locate_source(
            args.db,
            state_hash=str(promotion["state_hash"]),
            scan_limit=args.source_scan_limit,
        )
        if not locator.get("found"):
            raise ValueError(str(locator.get("reason") or "source_state_missing"))
        source = dict(locator["source"])
        source_identity = build_source_identity(
            int(source["source_snapchain_id"]),
            int(source["source_step_index"]),
            str(source["source_before_state_digest"]),
        )
        acquisition_id = build_acquisition_id(
            promotion_id=int(promotion["promotion_id"]),
            promotion_signature=str(promotion["promotion_signature"]),
            acquisition_protocol=ACQUISITION_PROTOCOL,
            protocol_version=SCHEDULE_VERSION,
            schedule_digest=schedule_digest,
            source_identity=source_identity,
            reacquisition_generation=args.reacquisition_generation,
        )
        exact_guard = lifecycle_guard(args.db, acquisition_id)
        equivalent_guard = equivalent_protocol_terminal_guard(
            args.db,
            promotion_id=int(promotion["promotion_id"]),
            promotion_signature=str(promotion["promotion_signature"]),
            acquisition_protocol=ACQUISITION_PROTOCOL,
            protocol_version=SCHEDULE_VERSION,
            schedule_digest=schedule_digest,
            reacquisition_generation=args.reacquisition_generation,
        )
        result.update({
            "promotion": promotion,
            "source": source,
            "source_locator": locator,
            "source_identity": source_identity,
            "acquisition_id": acquisition_id,
            "schedule_descriptor": schedule_descriptor,
            "schedule_digest": schedule_digest,
            "lifecycle_guard": exact_guard,
            "equivalent_protocol_guard": equivalent_guard,
        })

        loaded = _load_source(args.db, int(source["source_snapchain_id"]))
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
        if str(before.get("state_digest") or "") != str(source["source_before_state_digest"]):
            raise ValueError("source_before_state_digest_changed")

        schedule = run_food_directed_acquisition_schedule(
            source_snapchain_id=int(source["source_snapchain_id"]),
            source_step_index=int(source["source_step_index"]),
            source_chain=loaded["chain"],
            source_step=step,
            target_action=target_action,
            learning_intent_lineage=promotion,
            horizons=horizons,
            max_runtime_sec_per_attempt=args.max_runtime_sec_per_attempt,
            target_len=(loaded["chain"].get("runner_config") or {}).get("target_len"),
            credit_steps=args.credit_steps,
            death_credit_steps=args.death_credit_steps,
        )
        result["schedule"] = schedule
        result["status"] = str(schedule.get("status") or "")
        result["simulation_started"] = True

        selected_preview = schedule.get("selected_evidence_preview")
        if schedule.get("status") != "evidence_acquired" or not isinstance(selected_preview, Mapping):
            result["mode"] = "g3_v2_no_replay_capable_evidence"
            result["persistence"] = {
                "requested": bool(args.write_g3),
                "write_attempted": False,
                "reason": "schedule_did_not_acquire_direct_evidence",
            }
        else:
            persisted_blob = build_persisted_evidence_blob(selected_preview)
            experiment_id = str(persisted_blob["experiment_id"])
            before_write = _find_evidence(args.db, experiment_id)
            result["idempotency_before"] = before_write
            if before_write.get("collision"):
                raise RuntimeError("experiment_source_id_collision")
            if before_write.get("exists"):
                result["mode"] = "g3_v2_evidence_existing"
                result["persistence"] = {
                    "requested": bool(args.write_g3),
                    "write_attempted": False,
                    "inserted": False,
                    "existing": True,
                    "snapchain_id": before_write.get("snapchain_id"),
                }
            elif not args.write_g3:
                result["mode"] = "read_only_g3_v2_evidence_preview"
                result["persistence"] = {
                    "requested": False,
                    "write_attempted": False,
                    "inserted": False,
                    "existing": False,
                    "reason": "write_g3_not_requested",
                }
            elif str(args.confirm) != CONFIRM_REQUIRED:
                result["ok"] = False
                result["status"] = "write_gate_blocked"
                result["errors"].append("confirm_phrase_mismatch")
                result["persistence"] = {
                    "requested": True,
                    "write_attempted": False,
                    "confirm_required": CONFIRM_REQUIRED,
                    "confirm_ok": False,
                    "reason": "confirm_phrase_mismatch",
                }
            else:
                summary = schedule.get("summary") if isinstance(schedule.get("summary"), dict) else {}
                record = {
                    "acquisition_id": acquisition_id,
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
                    "source_identity": source_identity,
                    "acquisition_protocol": ACQUISITION_PROTOCOL,
                    "protocol_version": SCHEDULE_VERSION,
                    "schedule_id": str(schedule_descriptor["schedule_id"]),
                    "schedule_digest": schedule_digest,
                    "attempts_budget": int(summary.get("attempts_budget") or 0),
                    "attempts_executed": int(summary.get("attempts_executed") or 0),
                    "selected_attempt_index": summary.get("selected_attempt_index"),
                    "direct_outcome_acquired": True,
                    "terminal_reason": "direct_outcome_acquired",
                    "source_high_water_mark": f"snapchains:max_id:{_source_hwm(args.db)}",
                    "reacquisition_generation": int(args.reacquisition_generation),
                    "meta": {
                        "attempts": schedule.get("attempts") or [],
                        "selected_attempt": schedule.get("selected_attempt"),
                        "source": source,
                        "experiment_id": experiment_id,
                        "evidence_digest": persisted_blob.get("evidence_digest"),
                    },
                }
                write_attempted = True
                persistence = persist_completed_run_with_evidence(
                    record,
                    final_status="evidence_acquired",
                    payload={
                        "summary": summary,
                        "attempts": schedule.get("attempts") or [],
                        "experiment_id": experiment_id,
                        "evidence_digest": persisted_blob.get("evidence_digest"),
                    },
                    evidence_blob=persisted_blob,
                    timeout_ms=args.dbwriter_timeout_ms,
                )
                result["persistence"] = {**persistence, "requested": True, "confirm_ok": True}
                if not persistence.get("ok"):
                    # A rejected pre-write gate is not an atomic verification
                    # failure.  Preserve the canonical DBWriter/lifecycle reason
                    # so operations can distinguish "nothing was written" from
                    # "a completed transaction could not be read back".
                    persistence_reason = str(persistence.get("reason") or "unknown")
                    raise RuntimeError(
                        f"atomic_lifecycle_evidence_persistence_failed:{persistence_reason}"
                    )
                after_write = _find_evidence(args.db, experiment_id)
                result["idempotency_after"] = after_write
                if not after_write.get("exists") or after_write.get("collision"):
                    raise RuntimeError("atomic_lifecycle_evidence_verification_failed")
                result["mode"] = "g3_v2_lifecycle_and_evidence_persisted_or_existing"
                result["persisted_evidence"] = {
                    "experiment_id": experiment_id,
                    "evidence_digest": persisted_blob.get("evidence_digest"),
                    "source_id": persisted_blob.get("source_id"),
                    "snapchain_id": after_write.get("snapchain_id"),
                }

        successful_write = bool(write_attempted and (result.get("persistence") or {}).get("ok"))
        result["safety"] = {
            "db_reads": True,
            "db_writes": successful_write,
            "lifecycle_writes": successful_write,
            "evidence_writes": successful_write,
            "queue_writes": False,
            "promotion_writes": False,
            "policy_writes": False,
            "runner_starts": False,
            "dbwriter_only": True,
            "local_sqlite_write_fallback": False,
            "max_evidence_writes_per_run": MAX_WRITES_PER_RUN,
            "db_open_mode": "dbwriter_atomic_write_plus_read_only_verification" if successful_write else "read_only_uri_mode_ro",
        }
    except Exception as exc:
        result.update({
            "ok": False,
            "status": "g3_v2_persistence_failed",
            "errors": result.get("errors", []) + [f"{type(exc).__name__}:{exc}"],
            "safety": {
                "db_reads": True,
                "db_writes": False,
                "lifecycle_writes": False,
                "evidence_writes": False,
                "queue_writes": False,
                "promotion_writes": False,
                "policy_writes": False,
                "runner_starts": False,
                "dbwriter_only": True,
                "local_sqlite_write_fallback": False,
                "max_evidence_writes_per_run": MAX_WRITES_PER_RUN,
                "db_open_mode": "read_only_uri_mode_ro",
            },
        })

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

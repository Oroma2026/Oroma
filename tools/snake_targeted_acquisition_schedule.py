#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_targeted_acquisition_schedule.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Targeted Acquisition Schedule – Dry Run + Gated Persistence
# Version: v0.1.0-bounded-horizon-schedule
# Stand:   2026-07-14
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses CLI führt für genau einen vorhandenen promotion-bound Lernauftrag die
# feste V1-Horizontkaskade 12 → 24 → 48 aus. Es lädt Promotion und Source Trace
# ausschließlich read-only und nutzt dieselben Bindungsprüfungen wie der
# bestehende Targeted Evidence Runner. Dry Run bleibt der Standard. Nur bei
# einem erworbenen Direct Outcome, --write-db, korrekter Confirm Phrase und
# verfügbarem DBWriter darf genau eine immutable Evidence-SnapChain entstehen.
#
# AUSGABE
# ───────
#   • ``evidence_acquired``: erster Versuch mit direkter Evidence wurde gefunden.
#   • ``exhausted_no_direct_outcome``: alle drei verifizierten Versuche blieben
#     ohne Direct Outcome; dies ist ein legitimer terminaler Befund.
#   • ``acquisition_attempt_failed`` oder Validierungsfehler: fail-closed.
#
# PERSISTENZ
# ──────────
# Ohne ``--write-db`` wird nie persistiert. Im Schreibmodus verwendet das Tool
# dieselben bestehenden C2-Helfer für Idempotenz, DBWriter-Gate, Recheck und
# Insert. Es gibt keinen lokalen SQLite-Schreibfallback und maximal einen Write
# pro Aufruf. Bei exhausted_no_direct_outcome bleibt Persistenz blockiert.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.targeted_acquisition_lifecycle import build_acquisition_id, build_source_identity, persist_completed_run
from core.snake_targeted_acquisition_schedule import (
    DEFAULT_HORIZONS,
    VERSION,
    run_bounded_acquisition_schedule,
)
from tools.snake_targeted_evidence_runner import (
    CONFIRM_REQUIRED,
    MAX_WRITES_PER_RUN,
    WRITER_ID,
    _dbwriter_ready,
    _find_existing,
    _load_promotion,
    _load_source,
    _persist,
    _verify_promotion_source_binding,
    build_persisted_evidence_blob,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"))
    parser.add_argument("--promotion-id", type=int, required=True)
    parser.add_argument("--source-snapchain-id", type=int, required=True)
    parser.add_argument("--source-step-index", type=int, required=True)
    parser.add_argument("--target-action", type=int, choices=(0, 1, 2), required=True)
    parser.add_argument("--horizons", default=",".join(str(v) for v in DEFAULT_HORIZONS))
    parser.add_argument("--max-runtime-sec-per-attempt", type=float, default=2.0)
    parser.add_argument("--credit-steps", type=int, default=12)
    parser.add_argument("--death-credit-steps", type=int, default=4)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--write-lifecycle", action="store_true")
    parser.add_argument("--lifecycle-confirm", default="")
    parser.add_argument("--reacquisition-generation", type=int, default=0)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--dbwriter-timeout-ms", type=int, default=60000)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    try:
        horizons = tuple(int(v.strip()) for v in str(args.horizons).split(",") if v.strip())
        lineage = _load_promotion(args.db, args.promotion_id)
        loaded = _load_source(args.db, args.source_snapchain_id)
        chain = loaded["chain"]
        steps = chain.get("steps")
        if not isinstance(steps, list):
            raise ValueError("source_steps_missing")
        if args.source_step_index < 0 or args.source_step_index >= len(steps):
            raise ValueError("source_step_index_out_of_range")
        source_step = steps[args.source_step_index]
        if not isinstance(source_step, dict):
            raise ValueError("source_step_not_object")
        _verify_promotion_source_binding(lineage, source_step, args.target_action)

        result = run_bounded_acquisition_schedule(
            source_snapchain_id=args.source_snapchain_id,
            source_step_index=args.source_step_index,
            source_chain=chain,
            source_step=source_step,
            target_action=args.target_action,
            learning_intent_lineage=lineage,
            horizons=horizons,
            max_runtime_sec_per_attempt=args.max_runtime_sec_per_attempt,
            target_len=(chain.get("runner_config") or {}).get("target_len"),
            credit_steps=args.credit_steps,
            death_credit_steps=args.death_credit_steps,
        )
        result["promotion_lineage"] = lineage
        result["source_row"] = loaded["row"]
        before = source_step.get("before") if isinstance(source_step.get("before"), dict) else {}
        source_before_digest = str(before.get("state_digest") or lineage.get("source_before_state_digest") or "")
        source_identity = build_source_identity(args.source_snapchain_id, args.source_step_index, source_before_digest)
        schedule_digest = str(result.get("schedule_digest") or "")
        acquisition_id = build_acquisition_id(
            promotion_id=args.promotion_id,
            promotion_signature=str(lineage.get("promotion_signature") or ""),
            acquisition_protocol="bounded_horizon",
            protocol_version=VERSION,
            schedule_digest=schedule_digest,
            source_identity=source_identity,
            reacquisition_generation=args.reacquisition_generation,
        )
        result["acquisition_lifecycle_identity"] = {
            "acquisition_id": acquisition_id,
            "source_identity": source_identity,
            "reacquisition_generation": args.reacquisition_generation,
        }
        lifecycle_write = {"requested": bool(args.write_lifecycle), "write_attempted": False, "reason": "write_lifecycle_not_requested"}
        if args.write_lifecycle:
            if str(args.lifecycle_confirm) != "PERSIST_TARGETED_ACQUISITION_LIFECYCLE_V1":
                lifecycle_write = {"requested": True, "write_attempted": False, "ok": False, "reason": "lifecycle_confirm_phrase_mismatch"}
                result["ok"] = False
                result.setdefault("errors", []).append("lifecycle_confirm_phrase_mismatch")
            elif result.get("status") not in {"evidence_acquired", "exhausted_no_direct_outcome"}:
                lifecycle_write = {"requested": True, "write_attempted": False, "ok": False, "reason": "schedule_not_terminal"}
            else:
                import sqlite3
                rc=sqlite3.connect(f"file:{loaded['db_path']}?mode=ro",uri=True)
                hwm=rc.execute("SELECT COALESCE(MAX(id),0) FROM snapchains").fetchone()[0]
                rc.close()
                summary=result.get("summary") if isinstance(result.get("summary"),dict) else {}
                record={
                    "acquisition_id":acquisition_id,"promotion_id":args.promotion_id,
                    "promotion_signature":str(lineage.get("promotion_signature") or ""),
                    "request_signature":str(lineage.get("request_signature") or ""),
                    "namespace":str(lineage.get("namespace") or "game:snake"),
                    "state_schema":str(lineage.get("state_schema") or "snake:pro_v2"),
                    "state_hash":str(lineage.get("state_hash") or ""),"primary_action":str(args.target_action),
                    "source_snapchain_id":args.source_snapchain_id,"source_step_index":args.source_step_index,
                    "source_before_state_digest":source_before_digest,"source_identity":source_identity,
                    "acquisition_protocol":"bounded_horizon","protocol_version":VERSION,
                    "schedule_id":str((result.get("schedule") or {}).get("schedule_id") or ""),"schedule_digest":schedule_digest,
                    "attempts_budget":int(summary.get("attempts_budget") or 0),"attempts_executed":int(summary.get("attempts_executed") or 0),
                    "selected_attempt_index":summary.get("selected_attempt_index"),"direct_outcome_acquired":bool(summary.get("direct_outcome_acquired")),
                    "terminal_reason":str(summary.get("terminal_reason") or result.get("status") or ""),
                    "source_high_water_mark":f"snapchains:max_id:{int(hwm)}","reacquisition_generation":args.reacquisition_generation,
                    "meta":{"attempts":result.get("attempts") or [],"source_row":loaded.get("row") or {}},
                }
                lifecycle_write=persist_completed_run(record,final_status=str(result.get("status")),payload={"summary":summary,"attempts":result.get("attempts") or []},timeout_ms=args.dbwriter_timeout_ms)
                lifecycle_write["requested"]=True
        result["lifecycle_persistence"] = lifecycle_write
        preview = result.get("selected_evidence_preview") if isinstance(result.get("selected_evidence_preview"), dict) else None
        inserted = False
        existing = False
        write_attempted = False
        inserted_id = None
        write_block_reason = "write_db_not_requested"
        if args.write_db:
            if result.get("status") != "evidence_acquired" or preview is None:
                write_block_reason = "no_acquired_evidence"
            elif str(args.confirm) != CONFIRM_REQUIRED:
                write_block_reason = "confirm_phrase_mismatch"
                result["ok"] = False
                result.setdefault("errors", []).append(write_block_reason)
            else:
                existing_check = _find_existing(loaded["db_path"], str(preview.get("experiment_id") or ""))
                if existing_check.get("collision"):
                    write_block_reason = "experiment_source_id_collision"
                    result["ok"] = False
                    result.setdefault("errors", []).append(write_block_reason)
                elif existing_check.get("exists"):
                    existing = True
                    inserted_id = int(existing_check["snapchain_id"])
                    write_block_reason = "existing"
                else:
                    dbw = _dbwriter_ready(args.dbwriter_timeout_ms)
                    if not dbw.get("ready"):
                        write_block_reason = str(dbw.get("reason") or "dbwriter_not_ready")
                        result["ok"] = False
                        result.setdefault("errors", []).append(write_block_reason)
                    else:
                        persisted_blob = build_persisted_evidence_blob(preview)
                        recheck = _find_existing(loaded["db_path"], str(preview.get("experiment_id") or ""))
                        if recheck.get("collision"):
                            raise RuntimeError("experiment_source_id_collision")
                        if recheck.get("exists"):
                            existing = True
                            inserted_id = int(recheck["snapchain_id"])
                            write_block_reason = "existing_after_recheck"
                        else:
                            write_attempted = True
                            inserted_id = _persist(persisted_blob, args.dbwriter_timeout_ms)
                            if inserted_id <= 0:
                                raise RuntimeError("dbwriter_insert_returned_invalid_id")
                            inserted = True
                            write_block_reason = "write_completed"
                        result["persisted_evidence"] = persisted_blob
        result["mode"] = (
            "bounded_targeted_evidence_persisted" if inserted else
            "bounded_targeted_evidence_existing" if existing else
            "read_only_bounded_targeted_acquisition"
        )
        result["persistence"] = {
            "requested": bool(args.write_db),
            "confirm_required": CONFIRM_REQUIRED,
            "confirm_ok": str(args.confirm) == CONFIRM_REQUIRED,
            "max_writes_per_run": MAX_WRITES_PER_RUN,
            "write_attempted": write_attempted,
            "inserted": inserted,
            "existing": existing,
            "snapchain_id": inserted_id,
            "write_block_reason": write_block_reason,
            "writer_id": WRITER_ID,
        }
        lifecycle_db_written = bool((result.get("lifecycle_persistence") or {}).get("write_attempted") and (result.get("lifecycle_persistence") or {}).get("ok"))
        result["safety"] = {
            "db_reads": True,
            "db_writes": bool(inserted or lifecycle_db_written),
            "policy_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "runner_starts": False,
            "db_open_mode": "dbwriter_write_plus_read_only_lookup" if (inserted or lifecycle_db_written) else "read_only_uri_mode_ro",
            "local_sqlite_write_fallback": False,
            "schema_changes": False,
        }
    except Exception as exc:
        result = {
            "ok": False,
            "status": "source_load_or_schedule_validation_failed",
            "version": VERSION,
            "mode": "read_only_bounded_targeted_acquisition_failed",
            "errors": [f"{type(exc).__name__}:{exc}"],
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
        }

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

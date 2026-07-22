#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_targeted_evidence_runner.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Targeted Evidence Runner – Dry Run + Gated C2 Persistence
# Version: v0.3.1-bounded-schedule-lineage
# Stand:   2026-07-13
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Das CLI lädt genau eine Promotion und eine explizit benannte reconstructable Snake-SnapChain
# ausschließlich über SQLite ``mode=ro``, führt den deterministischen C1-
# Versuch aus und gibt standardmäßig nur die Evidence-Vorschau aus.
#
# C2-PERSISTENZ
# ─────────────
# Genau eine neue immutable Evidence-SnapChain darf ausschließlich dann über
# den globalen DBWriter geschrieben werden, wenn alle Gates gleichzeitig
# erfüllt sind:
#   • ``--write-db`` wurde explizit gesetzt,
#   • die Confirm Phrase ist exakt korrekt,
#   • DBWriter ist aktiviert und erreichbar,
#   • die Simulation war vollständig erfolgreich und verifiziert,
#   • das harte Schreibbudget von einem Datensatz wird eingehalten.
#
# IDEMPOTENZ
# ──────────
# Die vollständige kanonische ``experiment_id`` bleibt die Autorität im Blob.
# Ein daraus deterministisch abgeleiteter positiver 63-Bit-Wert wird im bereits
# vorhandenen numerischen ``snapchains.source_id`` gespeichert. Ein Lookup-
# Treffer wird durch Dekodieren und Vergleich der vollständigen experiment_id
# bestätigt; numerische Hash-Kollisionen werden dadurch fail-closed erkannt.
#
# SICHERHEIT
# ──────────
#   • Dry Run ist unverändert der Standard.
#   • Kein lokaler SQLite-Schreibfallback.
#   • Keine Policy-, Gap-, Queue-, Promotion- oder Outcome-Mutation.
#   • Maximal eine SnapChain pro Prozessaufruf.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)

from core import db_writer_client
from core.snake_targeted_evidence_runner import (
    EVIDENCE_CLASS,
    EVIDENCE_SCHEMA,
    VERSION,
    WRITER_ID,
    build_persisted_evidence_blob,
    experiment_source_id,
    simulate_targeted_observation,
)

CONFIRM_REQUIRED = "PERSIST_TARGETED_EVIDENCE_SNAKE_V1"
ORIGIN = "targeted_evidence_runner:v1"
NAMESPACE = "game:snake"
MAX_WRITES_PER_RUN = 1

PROMOTION_TABLE = "gap_policy_promotion_queue"
PROMOTION_BUCKET = "promotion_candidate_replay"
PROMOTION_STATUS = "promotion_review"
PROMOTION_TARGET = "replay"
STATE_SCHEMA = "snake:pro_v2"



def _decode_blob(blob: Any) -> Optional[Dict[str, Any]]:
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    candidates: List[bytes] = []
    if isinstance(blob, bytes):
        candidates.append(blob)
        try:
            candidates.append(zlib.decompress(blob))
        except Exception:
            pass
    elif isinstance(blob, str):
        candidates.append(blob.encode("utf-8"))
    for raw in candidates:
        try:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return None


def _ro_connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_source(db_path: str, snapchain_id: int) -> Dict[str, Any]:
    path = Path(db_path).resolve()
    conn = _ro_connect(str(path))
    try:
        row = conn.execute(
            """
            SELECT id, ts, status, origin, namespace, version, blob
              FROM snapchains
             WHERE id=?
             LIMIT 1
            """,
            (int(snapchain_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("source_snapchain_missing")
    chain = _decode_blob(row["blob"])
    if not isinstance(chain, dict):
        raise ValueError("source_snapchain_blob_decode_error")
    return {
        "row": {
            "id": int(row["id"]), "ts": int(row["ts"] or 0),
            "status": row["status"], "origin": row["origin"],
            "namespace": row["namespace"], "version": row["version"],
        },
        "chain": chain,
        "db_path": str(path),
    }


def _state_schema_from_hash(state_hash: str) -> str:
    parts = str(state_hash or "").strip().split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else ""


def _load_promotion(db_path: str, promotion_id: int) -> Dict[str, Any]:
    conn = _ro_connect(db_path)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PROMOTION_TABLE,),
        ).fetchone()
        if table is None:
            raise ValueError("promotion_table_missing")
        row = conn.execute(
            f"""
            SELECT id, promotion_signature, request_signature, evidence_queue_id,
                   plan_id, focus_id, target, promotion_bucket, namespace,
                   state_hash, primary_action, kind, reason, recommended_next,
                   score, status, policy_write_allowed, source_validation_bucket,
                   source_validation_ts, created_ts, updated_ts, meta_json
              FROM {PROMOTION_TABLE}
             WHERE id=?
             LIMIT 1
            """,
            (int(promotion_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("promotion_missing")
    state_hash = str(row["state_hash"] or "").strip()
    action = str(row["primary_action"] or "").strip()
    lineage = {
        "promotion_id": int(row["id"]),
        "promotion_signature": str(row["promotion_signature"] or "").strip(),
        "request_signature": str(row["request_signature"] or "").strip(),
        "evidence_queue_id": int(row["evidence_queue_id"] or 0),
        "plan_id": str(row["plan_id"] or "").strip(),
        "focus_id": str(row["focus_id"] or "").strip(),
        "promotion_bucket": str(row["promotion_bucket"] or "").strip(),
        "promotion_status_at_acquisition": str(row["status"] or "").strip(),
        "namespace": str(row["namespace"] or "").strip(),
        "state_schema": _state_schema_from_hash(state_hash),
        "state_hash": state_hash,
        "target": str(row["target"] or "").strip(),
        "primary_action": action,
        "source_validation_bucket": str(row["source_validation_bucket"] or "").strip(),
        "source_validation_ts": int(row["source_validation_ts"] or 0),
        "promotion_updated_ts": int(row["updated_ts"] or 0),
    }
    checks = [
        (lineage["promotion_status_at_acquisition"] == PROMOTION_STATUS, "promotion_not_current"),
        (lineage["promotion_bucket"] == PROMOTION_BUCKET, "promotion_not_replay_candidate"),
        (lineage["target"] == PROMOTION_TARGET, "promotion_target_not_replay"),
        (lineage["namespace"] == NAMESPACE, "promotion_namespace_not_supported"),
        (lineage["state_schema"] == STATE_SCHEMA, "promotion_state_schema_not_supported"),
        (bool(lineage["promotion_signature"]), "promotion_signature_missing"),
        (bool(lineage["request_signature"]), "promotion_request_signature_missing"),
        (bool(lineage["state_hash"]), "promotion_state_hash_missing"),
        (lineage["primary_action"] in {"0", "1", "2"}, "promotion_primary_action_invalid"),
    ]
    for ok, reason in checks:
        if not ok:
            raise ValueError(reason)
    return lineage


def _verify_promotion_source_binding(
    lineage: Dict[str, Any], source_step: Dict[str, Any], target_action: int
) -> None:
    source_state_hash = str(source_step.get("state_hash") or source_step.get("sh") or "").strip()
    source_action = str(source_step.get("action") if source_step.get("action") is not None else source_step.get("a") or "").strip()
    if source_state_hash != str(lineage["state_hash"]):
        raise ValueError("promotion_source_state_mismatch")
    if str(int(target_action)) != str(lineage["primary_action"]):
        raise ValueError("promotion_target_action_mismatch")
    lineage["source_step_recorded_action"] = source_action


def _find_existing(db_path: str, experiment_id: str) -> Dict[str, Any]:
    lookup_id = experiment_source_id(experiment_id)
    conn = _ro_connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, source_id, origin, namespace, version, blob
              FROM snapchains
             WHERE source_id=? AND origin=? AND namespace=?
             ORDER BY id ASC
            """,
            (int(lookup_id), ORIGIN, NAMESPACE),
        ).fetchall()
    finally:
        conn.close()
    collisions = []
    for row in rows:
        blob = _decode_blob(row["blob"])
        stored_experiment_id = str((blob or {}).get("experiment_id") or "")
        if stored_experiment_id == experiment_id:
            return {
                "exists": True,
                "snapchain_id": int(row["id"]),
                "source_id": int(lookup_id),
                "collision": False,
            }
        collisions.append({"snapchain_id": int(row["id"]), "experiment_id": stored_experiment_id})
    if collisions:
        return {
            "exists": False,
            "snapchain_id": None,
            "source_id": int(lookup_id),
            "collision": True,
            "collision_rows": collisions,
        }
    return {"exists": False, "snapchain_id": None, "source_id": int(lookup_id), "collision": False}


def _dbwriter_ready(timeout_ms: int) -> Dict[str, Any]:
    if not db_writer_client.enabled():
        return {"ready": False, "reason": "dbwriter_disabled"}
    if not db_writer_client.ping(timeout_ms=min(int(timeout_ms), 2000)):
        return {"ready": False, "reason": "dbwriter_unreachable"}
    return {"ready": True, "reason": "dbwriter_ready"}


def _persist(blob: Dict[str, Any], timeout_ms: int) -> int:
    raw = json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    now = int(time.time())
    sql = """
        INSERT INTO snapchains
          (ts, quality, blob, exported, status, origin,
           gap_flag, notes, namespace, source_id, version, weight)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        now, 1.0, raw, 0, "active", ORIGIN, 0,
        "immutable targeted simulation observation; no policy write",
        NAMESPACE, int(blob["source_id"]), EVIDENCE_SCHEMA, 1.0,
    )
    return int(db_writer_client.exec_lastrowid(
        sql, params=params, tag="snake.targeted_evidence.persist.v1",
        priority="high", timeout_ms=int(timeout_ms), db="oroma",
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"))
    parser.add_argument("--promotion-id", type=int, required=True)
    parser.add_argument("--source-snapchain-id", type=int, required=True)
    parser.add_argument("--source-step-index", type=int, required=True)
    parser.add_argument("--target-action", type=int, choices=(0, 1, 2), required=True)
    parser.add_argument("--attempt-index", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-runtime-sec", type=float, default=2.0)
    parser.add_argument("--credit-steps", type=int, default=12)
    parser.add_argument("--death-credit-steps", type=int, default=4)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--dbwriter-timeout-ms", type=int, default=60000)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    write_attempted = False
    inserted = False
    existing = False
    inserted_id = None
    try:
        promotion_lineage = _load_promotion(args.db, args.promotion_id)
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
        _verify_promotion_source_binding(promotion_lineage, source_step, args.target_action)

        result = simulate_targeted_observation(
            source_snapchain_id=args.source_snapchain_id,
            source_step_index=args.source_step_index,
            source_chain=chain,
            source_step=source_step,
            target_action=args.target_action,
            attempt_index=args.attempt_index,
            max_steps=args.max_steps,
            max_runtime_sec=args.max_runtime_sec,
            target_len=(chain.get("runner_config") or {}).get("target_len"),
            credit_steps=args.credit_steps,
            death_credit_steps=args.death_credit_steps,
            learning_intent_lineage=promotion_lineage,
        ).to_dict()
        result["source_row"] = loaded["row"]
        result["promotion_lineage"] = promotion_lineage
        result["db_path"] = loaded["db_path"]
        result.setdefault("version", VERSION)

        preview = result.get("evidence_preview") if isinstance(result.get("evidence_preview"), dict) else None
        if not result.get("ok") or preview is None:
            result["mode"] = "targeted_evidence_simulation_failed"
        else:
            experiment_id = str(preview.get("experiment_id") or "")
            existing_check = _find_existing(loaded["db_path"], experiment_id)
            result["idempotency"] = existing_check
            if existing_check.get("collision"):
                result["ok"] = False
                result["status"] = "experiment_source_id_collision"
                result.setdefault("errors", []).append("experiment_source_id_collision")
            elif existing_check.get("exists"):
                existing = True
                result["mode"] = "targeted_evidence_existing"
                result["persistence"] = {
                    "requested": bool(args.write_db), "write_attempted": False,
                    "inserted": False, "existing": True,
                    "snapchain_id": existing_check.get("snapchain_id"),
                    "writer_id": WRITER_ID,
                }
            elif not args.write_db:
                result["mode"] = "read_only_targeted_evidence_dry_run"
                result["persistence"] = {
                    "requested": False, "write_attempted": False,
                    "inserted": False, "existing": False,
                    "write_block_reason": "write_db_not_requested",
                    "writer_id": WRITER_ID,
                }
            else:
                confirm_ok = str(args.confirm) == CONFIRM_REQUIRED
                dbw = _dbwriter_ready(args.dbwriter_timeout_ms)
                if not confirm_ok:
                    result["ok"] = False
                    result["status"] = "write_gate_blocked"
                    result.setdefault("errors", []).append("confirm_phrase_mismatch")
                    block_reason = "confirm_phrase_mismatch"
                elif not dbw["ready"]:
                    result["ok"] = False
                    result["status"] = "write_gate_blocked"
                    result.setdefault("errors", []).append(str(dbw["reason"]))
                    block_reason = str(dbw["reason"])
                else:
                    if not isinstance(preview.get("learning_intent_lineage"), dict):
                        raise RuntimeError("learning_intent_lineage_missing")
                    persisted_blob = build_persisted_evidence_blob(preview)
                    # Recheck immediately before the sole allowed write (TOCTOU guard).
                    recheck = _find_existing(loaded["db_path"], experiment_id)
                    if recheck.get("collision"):
                        raise RuntimeError("experiment_source_id_collision")
                    if recheck.get("exists"):
                        existing = True
                        inserted_id = int(recheck["snapchain_id"])
                        block_reason = "existing_after_recheck"
                    else:
                        write_attempted = True
                        inserted_id = _persist(persisted_blob, args.dbwriter_timeout_ms)
                        if inserted_id <= 0:
                            raise RuntimeError("dbwriter_insert_returned_invalid_id")
                        inserted = True
                        block_reason = "write_completed"
                    result["mode"] = "targeted_evidence_persisted" if inserted else "targeted_evidence_existing"
                    result["persisted_evidence"] = persisted_blob
                result["persistence"] = {
                    "requested": True,
                    "confirm_required": CONFIRM_REQUIRED,
                    "confirm_ok": confirm_ok,
                    "dbwriter_ready": bool(dbw["ready"]),
                    "dbwriter_reason": dbw["reason"],
                    "max_writes_per_run": MAX_WRITES_PER_RUN,
                    "write_attempted": write_attempted,
                    "inserted": inserted,
                    "existing": existing,
                    "snapchain_id": inserted_id,
                    "write_block_reason": block_reason,
                    "writer_id": WRITER_ID,
                }

        result["safety"] = {
            **dict(result.get("safety") or {}),
            "db_reads": True,
            "db_open_mode": "dbwriter_write_plus_read_only_lookup" if write_attempted else "read_only_uri_mode_ro",
            "db_writes": bool(inserted),
            "dbwriter_only": True,
            "local_sqlite_write_fallback": False,
            "max_writes_per_run": MAX_WRITES_PER_RUN,
            "policy_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "schema_changes": False,
        }
    except Exception as exc:
        result = {
            "ok": False,
            "status": "source_load_validation_or_persistence_failed",
            "version": VERSION,
            "mode": "targeted_evidence_failed",
            "errors": [f"{type(exc).__name__}:{exc}"],
            "persistence": {
                "requested": bool(args.write_db), "write_attempted": write_attempted,
                "inserted": inserted, "existing": existing,
                "max_writes_per_run": MAX_WRITES_PER_RUN,
                "writer_id": WRITER_ID,
            },
            "safety": {
                "db_reads": True,
                "db_open_mode": "read_only_uri_mode_ro",
                "db_writes": False,
                "dbwriter_only": True,
                "local_sqlite_write_fallback": False,
                "policy_writes": False,
                "queue_writes": False,
                "promotion_writes": False,
                "schema_changes": False,
            },
        }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

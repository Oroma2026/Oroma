#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_targeted_source_locator.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Targeted Source Locator – Read-Only Reconstructable State Match
# Version: v0.1.0-deterministic-source-locator
# Stand:   2026-07-15
# =============================================================================
"""Read-only, deterministic locator for promotion-bound Snake source states.

The locator intentionally matches the physical pre-state only. The historical
recorded action is lineage metadata, not a selection constraint, because the
promotion action is the intervention that the targeted runner must force.
"""
from __future__ import annotations

import json
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.snake_reconstructable_trace import verify_step

VERSION = "v0.1.0-deterministic-source-locator"
TRACE_SCHEMA = "snake_trace:reconstructable_v1"
DEFAULT_SCAN_LIMIT = 5000


def _decode_blob(blob: Any) -> Optional[Dict[str, Any]]:
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    raw_candidates: List[bytes] = []
    if isinstance(blob, bytes):
        raw_candidates.append(blob)
        try:
            raw_candidates.append(zlib.decompress(blob))
        except Exception:
            pass
    elif isinstance(blob, str):
        raw_candidates.append(blob.encode("utf-8"))
    for raw in raw_candidates:
        try:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return None


def _state_hash(step: Mapping[str, Any]) -> str:
    return str(step.get("state_hash") or step.get("sh") or "").strip()


def _recorded_action(step: Mapping[str, Any]) -> str:
    raw = step.get("action") if step.get("action") is not None else step.get("a")
    return "" if raw is None else str(raw).strip()


def _before_digest(step: Mapping[str, Any]) -> str:
    ctx = step.get("trace_context") if isinstance(step.get("trace_context"), Mapping) else {}
    before = ctx.get("before") if isinstance(ctx.get("before"), Mapping) else {}
    return str(before.get("state_digest") or "").strip()


def locate_source_for_state(
    con: sqlite3.Connection,
    *,
    state_hash: str,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> Dict[str, Any]:
    """Locate the deterministic best reconstructable source for one state.

    Selection order is stable and explicit:
      1. newest snapchain id,
      2. lowest step index inside that snapchain.
    """
    started = time.monotonic()
    wanted = str(state_hash or "").strip()
    if not wanted:
        return {"ok": False, "found": False, "reason": "state_hash_missing", "version": VERSION}
    rows = con.execute(
        """
        SELECT id,ts,status,origin,namespace,version,blob
          FROM snapchains
         WHERE status='active'
           AND (origin IN ('game:snake','snake') OR namespace IN ('game:snake','snake'))
         ORDER BY id DESC
         LIMIT ?
        """,
        (max(1, int(scan_limit)),),
    ).fetchall()
    decode_errors = 0
    reconstructable = 0
    steps_scanned = 0
    invalid_matching_steps = 0
    matches: List[Dict[str, Any]] = []
    for row in rows:
        root = _decode_blob(row["blob"])
        if root is None:
            decode_errors += 1
            continue
        if str(root.get("trace_schema") or "") != TRACE_SCHEMA:
            continue
        reconstructable += 1
        steps = root.get("steps") if isinstance(root.get("steps"), list) else []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            steps_scanned += 1
            if _state_hash(step) != wanted:
                continue
            verification = verify_step(step)
            if not bool(verification.ok):
                invalid_matching_steps += 1
                continue
            digest = _before_digest(step)
            if not digest:
                invalid_matching_steps += 1
                continue
            matches.append({
                "source_snapchain_id": int(row["id"]),
                "source_step_index": int(index),
                "source_before_state_digest": digest,
                "source_step_recorded_action": _recorded_action(step),
                "source_snapchain_ts": int(row["ts"] or 0),
                "source_origin": str(row["origin"] or ""),
                "source_namespace": str(row["namespace"] or ""),
                "source_version": str(row["version"] or ""),
                "state_hash": wanted,
                "verification_reason": str(verification.reason or "verified"),
            })
    selected = matches[0] if matches else None
    return {
        "ok": True,
        "found": selected is not None,
        "reason": "source_found" if selected is not None else "source_state_missing",
        "version": VERSION,
        "selection_policy": "newest_snapchain_then_lowest_step_index",
        "source": selected,
        "summary": {
            "snapchains_loaded": len(rows),
            "reconstructable_snapchains": reconstructable,
            "steps_scanned": steps_scanned,
            "matching_sources": len(matches),
            "invalid_matching_steps": invalid_matching_steps,
            "decode_errors": decode_errors,
            "scan_limit": max(1, int(scan_limit)),
            "dt_ms": round((time.monotonic() - started) * 1000.0, 3),
        },
        "safety": {
            "db_reads": True,
            "db_writes": False,
            "policy_writes": False,
            "queue_writes": False,
            "promotion_writes": False,
            "db_open_mode": "existing_read_only_connection",
        },
    }


def locate_source(db_path: str, *, state_hash: str, scan_limit: int = DEFAULT_SCAN_LIMIT) -> Dict[str, Any]:
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        result = locate_source_for_state(con, state_hash=state_hash, scan_limit=scan_limit)
        result["db_path"] = str(path)
        return result
    finally:
        con.close()


__all__ = ["VERSION", "TRACE_SCHEMA", "DEFAULT_SCAN_LIMIT", "locate_source", "locate_source_for_state"]

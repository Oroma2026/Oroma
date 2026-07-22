#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_reconstructable_trace_audit.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Reconstructable Trace – Read-Only Live Audit
# Version: v1.0.1-snake-trace-live-audit-import-bootstrap
# Stand:   2026-07-13
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Werkzeug prüft aktuelle Snake-SnapChains ausschließlich lesend gegen
# ``snake_trace:reconstructable_v1``. Jeder Step wird aus seinem vollständigen
# Vorzustand und seiner Aktion rekonstruiert. Anschließend werden Transition,
# kompakter Nachzustand und SHA-256-Digest verglichen.
#
# SICHERHEIT
# ──────────
#   • SQLite wird ausschließlich mit URI ``mode=ro`` geöffnet.
#   • Keine Schemaänderung, keine DB-, Evidence-, Queue- oder Policy-Writes.
#   • Keine Runner-, Replay-, Dream- oder Orchestrator-Starts.
#   • Historische Traces ohne reconstructable_v1 werden gezählt, aber nicht
#     umgedeutet. Unbekannte Trace-Schemata bleiben fail-closed.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

# Script-/Modul-Kompatibilitaet ------------------------------------------------
#
# Dieses Audit wird produktiv als Datei aus ``tools`` gestartet. Python nimmt
# dabei nur das Script-Verzeichnis in ``sys.path`` auf; ohne expliziten Bootstrap
# waere ``core.snake_reconstructable_trace`` beim direkten Aufruf nicht erreichbar.
# Das Projekt-Root wird deshalb vor dem ersten ORÓMA-Import deterministisch aus
# ``__file__`` abgeleitet und defensiv ergaenzt. Der Audit bleibt weiterhin
# vollstaendig read-only und unabhaengig vom aktuellen Arbeitsverzeichnis.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)

from core.snake_reconstructable_trace import TRACE_SCHEMA, verify_step

VERSION = "v1.0.1-snake-trace-live-audit-import-bootstrap"


def _decode_blob(blob: Any) -> Optional[Dict[str, Any]]:
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    attempts = []
    if isinstance(blob, bytes):
        attempts.extend((blob,))
        try:
            attempts.append(zlib.decompress(blob))
        except Exception:
            pass
    elif isinstance(blob, str):
        attempts.append(blob.encode("utf-8"))
    for raw in attempts:
        try:
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:
            continue
    return None


def audit(db_path: str, limit: int) -> Dict[str, Any]:
    started = time.time()
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, ts, origin, namespace, version, blob
              FROM snapchains
             WHERE status='active'
               AND (origin IN ('game:snake','snake')
                    OR namespace IN ('game:snake','snake'))
             ORDER BY id DESC
             LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    finally:
        conn.close()

    schema_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    rows_verified = 0
    rows_failed = 0
    steps_verified = 0
    steps_failed = 0
    decode_errors = 0
    examples = []

    for row in rows:
        chain = _decode_blob(row["blob"])
        if not isinstance(chain, dict):
            decode_errors += 1
            reason_counts["blob_decode_error"] += 1
            continue
        trace_schema = str(chain.get("trace_schema") or "historical_no_trace_schema")
        schema_counts[trace_schema] += 1
        if trace_schema != TRACE_SCHEMA:
            reason_counts["trace_schema_not_supported"] += 1
            continue
        steps = chain.get("steps")
        if not isinstance(steps, list):
            rows_failed += 1
            reason_counts["steps_missing"] += 1
            continue
        row_ok = True
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                steps_failed += 1
                row_ok = False
                reason_counts["step_not_object"] += 1
                continue
            result = verify_step(step)
            reason_counts[result.reason] += 1
            if result.ok:
                steps_verified += 1
            else:
                steps_failed += 1
                row_ok = False
                if len(examples) < 20:
                    examples.append({
                        "snapchain_id": int(row["id"]),
                        "step_index": int(index),
                        "reason": result.reason,
                        "state_hash": step.get("state_hash") or step.get("sh"),
                        "action": step.get("a"),
                    })
        if row_ok:
            rows_verified += 1
        else:
            rows_failed += 1

    ok = bool(rows_failed == 0 and steps_failed == 0 and decode_errors == 0)
    return {
        "ok": ok,
        "version": VERSION,
        "generated_at_ts": int(time.time()),
        "db_path": str(path),
        "config": {"limit": int(limit), "trace_schema": TRACE_SCHEMA},
        "summary": {
            "rows_loaded": len(rows),
            "rows_verified": rows_verified,
            "rows_failed": rows_failed,
            "steps_verified": steps_verified,
            "steps_failed": steps_failed,
            "decode_errors": decode_errors,
            "schema_counts": dict(schema_counts),
            "reason_counts": dict(reason_counts),
            "dt_ms": round((time.time() - started) * 1000.0, 3),
        },
        "failures": examples,
        "safety": {
            "db_open_mode": "read_only_uri_mode_ro",
            "db_writes": False,
            "schema_changes": False,
            "policy_writes": False,
            "jobs_started": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=os.environ.get("OROMA_DB_PATH", "data/oroma.db"),
        help="Path to ORÓMA SQLite database (opened read-only).",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = audit(args.db, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

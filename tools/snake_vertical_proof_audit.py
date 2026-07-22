#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_vertical_proof_audit.py
# Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:   Snake Vertical-Proof Writer-Isolation Audit
# Version: v0.1.0-causal-isolation-audit
# Stand:   2026-07-13
#
# ZWECK
# ─────
# Read-only Auditwerkzeug für den kausal sterilen Snake-Referenznachweis. Das
# Tool zeigt maschinenlesbar, welche bekannten Snake-Policy-Writer im aktuellen
# Ausführungsmodus erlaubt oder blockiert sind, ob der Evidence-Pfad aktiv
# bleiben darf und ob der historische systemd-Timer auf dem Live-System aktiv
# oder enabled ist.
#
# Das Tool startet keine Jobs, verändert keine Units, schreibt keine Datenbank
# und autorisiert keinen Boundary-Write. Die simulierte Boundary-Entscheidung
# wird ausschließlich als Entscheidungsobjekt ausgegeben.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List

_BASE = Path(__file__).resolve().parents[1]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import execution_mode

VERSION = "v0.1.0-causal-isolation-audit"

KNOWN_WRITERS = [
    ("writer:tools.snake_daily_runner:legacy", "tools/snake_daily_runner.py", "daily runner learn_many"),
    ("writer:core.train_snake_policy:legacy", "core/train_snake_policy.py", "orchestrator/systemd trainer"),
    ("writer:core.snake_trainer:legacy", "core/snake_trainer.py", "manual legacy trainer"),
    ("writer:mini_programs.snake:legacy", "mini_programs/snake.py", "manual/UI game learning"),
    ("writer:core.universal_policy:legacy", "core/universal_policy.py", "shared final legacy mutation point"),
]


def _systemctl_state(unit: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"unit": unit, "available": False, "active": "unknown", "enabled": "unknown"}
    try:
        for key, cmd in (
            ("active", ["systemctl", "is-active", unit]),
            ("enabled", ["systemctl", "is-enabled", unit]),
        ):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            value = (proc.stdout or proc.stderr or "unknown").strip().splitlines()
            out[key] = value[0] if value else "unknown"
            out["available"] = True
    except Exception as exc:
        out["error"] = repr(exc)
    return out


def build_report(namespace: str) -> Dict[str, Any]:
    writers: List[Dict[str, Any]] = []
    for writer_id, path, activation in KNOWN_WRITERS:
        decision = execution_mode.legacy_policy_training_allowed(writer_id=writer_id, namespace=namespace)
        writers.append({
            "writer_id": writer_id,
            "path": path,
            "activation": activation,
            "decision": decision.to_dict(),
            "source_exists": Path(path).exists(),
        })

    boundary_writer = (execution_mode.boundary_writer_allowlist() or ["writer:core.gap_policy_mini_write:v0.3"])[0]
    boundary_without_auth = execution_mode.policy_mutation_decision(
        writer_id=boundary_writer,
        namespace=namespace,
        mutation_type="UPDATE_RULE_STATISTICS",
        boundary_authorized=False,
    )
    boundary_with_auth = execution_mode.policy_mutation_decision(
        writer_id=boundary_writer,
        namespace=namespace,
        mutation_type="UPDATE_RULE_STATISTICS",
        boundary_authorized=True,
    )
    evidence = execution_mode.evidence_collection_allowed(namespace=namespace)

    blocked = sum(1 for row in writers if not bool(row["decision"].get("allowed")))
    return {
        "version": VERSION,
        "ok": True,
        "namespace": namespace,
        "execution": execution_mode.execution_mode_status(),
        "writers": writers,
        "summary": {
            "known_writers": len(writers),
            "legacy_writers_blocked": blocked,
            "legacy_writers_allowed": len(writers) - blocked,
            "evidence_collection_allowed": evidence.allowed,
            "boundary_without_authorization_allowed": boundary_without_auth.allowed,
            "boundary_with_authorization_allowed": boundary_with_auth.allowed,
            "causally_isolated": bool(blocked == len(writers) and evidence.allowed and not boundary_without_auth.allowed),
        },
        "evidence_decision": evidence.to_dict(),
        "boundary_without_authorization": boundary_without_auth.to_dict(),
        "boundary_with_authorization": boundary_with_auth.to_dict(),
        "systemd": {
            "train_snake_timer": _systemctl_state("oroma-train-snake.timer"),
            "train_snake_service": _systemctl_state("oroma-train-snake.service"),
        },
        "safety": {
            "db_reads": False,
            "db_writes": False,
            "policy_writes": False,
            "jobs_started": False,
            "systemd_changes": False,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Snake vertical-proof writer isolation audit")
    ap.add_argument("--namespace", default="game:snake")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    report = build_report(str(args.namespace or "game:snake"))
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=bool(args.pretty)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

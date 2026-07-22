#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_replay_evidence_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Targeted Replay Evidence Probe CLI · Headless · State-only
# Version:   v0.2.3-shared-capability-scan
# Stand:     2026-07-12
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# CLI fuer core.gap_replay_evidence_probe. Das Tool prueft wenige
# Gap-Promotion-Kandidaten ueber explizite, lokale Headless-Replay-Adapter.
# Snake `snake:pro_v2` wird ausschliesslich aus exakt passenden gespeicherten
# SnapChain-Steps mit direktem Step-Outcome abgeleitet; keine Rekonstruktion.
# Es startet keinen globalen ReplayManager, keinen Runner und keinen Dream-Job;
# es schreibt nicht in DB/policy_rules, sondern nur best-effort State-JSON.
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_replay_evidence_probe


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Targeted Replay Evidence Probe (state-only, no policy write)")
    ap.add_argument("--once", action="store_true", help="Run one bounded replay-evidence probe pass")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ap.add_argument("--limit", type=int, default=None, help="Max promotion candidates to inspect")
    ap.add_argument("--topk", type=int, default=None, help="Max candidates returned in output")
    ap.add_argument("--horizon-steps", type=int, default=None, help="Short replay horizon per candidate")
    ap.add_argument("--namespaces", default=None, help="Comma-separated namespace allowlist")
    ap.add_argument("--state-schemas", default=None, help="Comma-separated state-schema allowlist")
    ap.add_argument("--targets", default=None, help="Comma-separated target allowlist")
    ap.add_argument("--db", default=None, help="Override DB path")
    ap.add_argument("--state", default=None, help="Override state JSON path")
    args = ap.parse_args()

    out = gap_replay_evidence_probe.run_once(
        db_path=Path(args.db).resolve() if args.db else None,
        state_path=Path(args.state).resolve() if args.state else None,
        limit=args.limit,
        topk=args.topk,
        horizon_steps=args.horizon_steps,
        namespaces=gap_replay_evidence_probe.parse_csv(args.namespaces, []) if args.namespaces is not None else None,
        state_schemas=gap_replay_evidence_probe.parse_csv(args.state_schemas, []) if args.state_schemas is not None else None,
        targets=gap_replay_evidence_probe.parse_csv(args.targets, []) if args.targets is not None else None,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

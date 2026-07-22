#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_evidence_outcome.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Outcome Collector CLI · Read-Only · Headless
# Version:   v0.1.0-read-only-outcome-collector
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# CLI für core.gap_evidence_outcome. Das Tool sucht vorhandene direkte Evidence
# für Gap-Promotion-Kandidaten. Es startet keine Runner-, Replay- oder Dream-
# Jobs, schreibt nicht in die DB und verändert keine policy_rules.
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_evidence_outcome


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Evidence Outcome Collector (read-only, no starts, no policy write)")
    ap.add_argument("--once", action="store_true", help="Run one bounded collector pass")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ap.add_argument("--limit", type=int, default=None, help="Max promotion rows to inspect")
    ap.add_argument("--topk", type=int, default=None, help="Max rows per bucket in output")
    ap.add_argument("--lookback-sec", type=int, default=None, help="Reward/episode lookback window")
    ap.add_argument("--reward-eps", type=float, default=None, help="Reward epsilon for pos/neg/draw classification")
    ap.add_argument("--db", default=None, help="Override DB path")
    ap.add_argument("--state", default=None, help="Override state JSON path")
    args = ap.parse_args()

    out = gap_evidence_outcome.run_once(
        db_path=Path(args.db).resolve() if args.db else None,
        state_path=Path(args.state).resolve() if args.state else None,
        limit=args.limit,
        topk=args.topk,
        lookback_sec=args.lookback_sec,
        reward_eps=args.reward_eps,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

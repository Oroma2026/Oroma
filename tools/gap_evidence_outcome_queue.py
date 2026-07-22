#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_evidence_outcome_queue.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Outcome Queue Gate CLI · DBWriter-only · No Policy Write
# Version:   v0.2.0-targeted-toctou-outcome-queue
# Stand:     2026-07-13
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# CLI fuer core.gap_evidence_outcome_queue. Das Tool uebernimmt fertige Outcomes
# aus data/state/gap_replay_evidence_probe.json in eine eigene DBWriter-only
# Outcome-Queue. Es schreibt nicht in policy_rules, startet nichts und hat keinen
# lokalen SQLite-Schreibfallback.
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_evidence_outcome_queue


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Evidence Outcome Queue Gate (DBWriter-only, no policy write)")
    ap.add_argument("--once", action="store_true", help="Run one outcome queue pass")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ap.add_argument("--limit", type=int, default=None, help="Max ready outcomes to enqueue")
    ap.add_argument("--topk", type=int, default=None, help="Max items returned in JSON")
    ap.add_argument("--min-confidence", type=float, default=None, help="Minimum replay-probe confidence")
    ap.add_argument("--db", default=None, help="Override DB path")
    ap.add_argument("--source", default=None, help="Override source JSON path")
    ap.add_argument("--state", default=None, help="Override state JSON path")
    args = ap.parse_args()

    out = gap_evidence_outcome_queue.run_once(
        db_path=Path(args.db).resolve() if args.db else None,
        source_path=Path(args.source).resolve() if args.source else None,
        state_path=Path(args.state).resolve() if args.state else None,
        limit=args.limit,
        topk=args.topk,
        min_confidence=args.min_confidence,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_targeted_evidence_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Targeted Evidence Probe CLI · Read-Only · Headless
# Version:   v0.1.0-targeted-evidence-probe
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# CLI für core.gap_targeted_evidence_probe. Das Tool prueft wenige
# Gap-Promotion-Kandidaten gezielt auf historisch rekonstruierbare Evidence und
# Format-Mismatches. Es startet keine Jobs, schreibt nicht in die DB und
# veraendert keine policy_rules.
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_targeted_evidence_probe


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Targeted Evidence Probe (read-only, no starts, no policy write)")
    ap.add_argument("--once", action="store_true", help="Run one bounded probe pass")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ap.add_argument("--limit", type=int, default=None, help="Max promotion candidates to inspect")
    ap.add_argument("--topk", type=int, default=None, help="Max matches per source in output")
    ap.add_argument("--scan-limit", type=int, default=None, help="Bounded newest-row scan size per source table")
    ap.add_argument("--db", default=None, help="Override DB path")
    ap.add_argument("--state", default=None, help="Override state JSON path")
    args = ap.parse_args()

    out = gap_targeted_evidence_probe.run_once(
        db_path=Path(args.db).resolve() if args.db else None,
        state_path=Path(args.state).resolve() if args.state else None,
        limit=args.limit,
        topk=args.topk,
        scan_limit=args.scan_limit,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

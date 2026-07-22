#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_policy_mini_write.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Policy Mini-Write Gate CLI · DBWriter-only · Ledger
# Version:   v0.2.0-promotion-lifecycle
# Stand:     2026-07-12
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# CLI für core.gap_policy_mini_write. Dieses Tool ist headless und iPhone-/SSH-
# freundlich ausführbar. Es startet keine Runner-, Replay- oder Dream-Jobs.
# Bei Default-ENV bleibt es fail-closed und erzeugt nur State-Status. Ein echter
# policy_rules-Mini-Write ist nur mit explizitem Confirm-Token UND echter
# Evidence-Outcome-Quelle möglich. Core v0.3 prüft Outcome und Promotion
# unabhängig auf Freshness und schließt beide Lifecycle-Zeilen atomar ab.
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_policy_mini_write


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Policy Mini-Write Gate (DBWriter-only, fail-closed)")
    ap.add_argument("--once", action="store_true", help="Run one bounded gate pass")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ap.add_argument("--limit", type=int, default=None, help="Max promotion rows to inspect")
    ap.add_argument("--topk", type=int, default=None, help="Max candidates to include in output")
    ap.add_argument("--max-writes", type=int, default=None, help="Max policy writes per run")
    ap.add_argument("--db", default=None, help="Override DB path")
    ap.add_argument("--state", default=None, help="Override state JSON path")
    args = ap.parse_args()

    out = gap_policy_mini_write.run_once(
        db_path=Path(args.db).resolve() if args.db else None,
        state_path=Path(args.state).resolve() if args.state else None,
        limit=args.limit,
        topk=args.topk,
        max_writes=args.max_writes,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

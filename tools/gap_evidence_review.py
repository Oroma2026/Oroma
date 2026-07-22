#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_evidence_review.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Queue Review CLI · Read-Only Dry-Run
# Version:   v0.1.0-read-only-review
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool liest die Tabelle gap_focus_evidence_queue read-only und erzeugt
# data/state/gap_evidence_review.json. Es bewertet Queue-Kandidaten fuer spaetere
# Replay-/Dream-/Explore-/Runner-Gates, schreibt aber weder DB noch Policy und
# startet keine Jobs.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - SQLite nur read-only via URI mode=ro.
# - Keine DB-Writes, keine Schemaaenderungen, keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Status-State atomar: data/state/gap_evidence_review.json.
# - Bei Root-Manual-Lauf wird die State-Datei best-effort auf oroma:oroma 664
#   gesetzt, damit der Orchestrator spaeter nicht durch Root-Ownership blockiert.
#
# CLI-BEISPIELE (iPhone-/SSH-sichere Einzeiler)
# ---------------------------------------------
#   cd /opt/ai/oroma; set -a; . ./.env.systemd; set +a; python3 tools/gap_evidence_review.py --once --pretty | tail -n 120
#   cd /opt/ai/oroma; python3 tools/gap_evidence_review.py --once --targets replay,dream --limit 100 --pretty
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_evidence_review  # noqa: E402


def _parse_targets(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return gap_evidence_review.parse_csv(raw, gap_evidence_review.DEFAULT_TARGETS)


def _parse_statuses(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return gap_evidence_review.parse_csv(raw, gap_evidence_review.DEFAULT_STATUSES)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Evidence Queue Review (read-only dry-run, no policy writes)")
    ap.add_argument("--once", action="store_true", help="Einmaligen Lauf ausfuehren")
    ap.add_argument("--pretty", action="store_true", help="JSON formatiert ausgeben")
    ap.add_argument("--db", default=None, help="Pfad zur ORÓMA SQLite DB")
    ap.add_argument("--state", default=None, help="Pfad fuer gap_evidence_review.json")
    ap.add_argument("--targets", default=None, help="CSV targets, z.B. explore,replay,dream,runner_priority")
    ap.add_argument("--statuses", default=None, help="CSV statuses, Default queued")
    ap.add_argument("--limit", type=int, default=None, help="Max Queue-Zeilen lesen")
    ap.add_argument("--topk", type=int, default=None, help="Max Items pro Review-Bucket im JSON")
    ap.add_argument("--min-score", type=float, default=None, help="Mindestscore fuer Review-Kandidaten")
    ap.add_argument("--covered-min-n", type=int, default=None, help="Ab diesem current-policy total_n kann ein Gap als covered erscheinen")
    ap.add_argument("--uncertainty-eps", type=float, default=None, help="q_gap <= eps gilt als weiterhin unsicher")
    ap.add_argument("--no-require-policy-rule", action="store_true", help="Auch ohne aktuelle policy_rules-Evidence sichtbar durchlassen")
    ap.add_argument("--no-write-state", action="store_true", help="State-JSON nicht schreiben")
    ns = ap.parse_args(argv)

    if not ns.once:
        ns.once = True

    doc = gap_evidence_review.build_evidence_review(
        db_path=Path(ns.db).expanduser().resolve() if ns.db else None,
        state_path=Path(ns.state).expanduser().resolve() if ns.state else None,
        targets=_parse_targets(ns.targets),
        statuses=_parse_statuses(ns.statuses),
        limit=ns.limit,
        topk=ns.topk,
        min_score=ns.min_score,
        covered_min_n=ns.covered_min_n,
        uncertainty_eps=ns.uncertainty_eps,
        require_policy_rule=False if ns.no_require_policy_rule else None,
    )
    if not ns.no_write_state:
        gap_evidence_review.write_state(doc, state_path=Path(ns.state).expanduser().resolve() if ns.state else None)
        summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
        summary = dict(summary)
        summary["state_written"] = True
        doc["summary"] = summary

    print(json.dumps(doc, ensure_ascii=False, indent=2 if ns.pretty else None, sort_keys=True))
    return 0 if bool(doc.get("ok", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

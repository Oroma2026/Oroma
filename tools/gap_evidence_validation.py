#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_evidence_validation.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Execution/Validation CLI · Read-Only Dry-Run
# Version:   v0.2.2-shared-capability-scan
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses CLI-Tool liest data/state/gap_evidence_review.json und prueft die
# dortigen Review-Kandidaten read-only gegen gap_focus_evidence_queue und
# policy_rules. Es erzeugt data/state/gap_evidence_validation.json als
# technische Kandidaten-/Blockiersicht fuer spaetere Evidence-Execution- und
# Promotion-Gates.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - SQLite nur read-only via URI mode=ro.
# - Keine DB-Writes, keine Schemaaenderungen, keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Status-State atomar: data/state/gap_evidence_validation.json.
# - Bei Root-Manual-Lauf wird die State-Datei best-effort auf oroma:oroma 664
#   gesetzt, damit der Orchestrator spaeter nicht durch Root-Ownership blockiert.
#
# CLI-BEISPIELE (iPhone-/SSH-sichere Einzeiler)
# ---------------------------------------------
#   cd /opt/ai/oroma; set -a; . ./.env.systemd; set +a; python3 tools/gap_evidence_validation.py --once --pretty | tail -n 120
#   cd /opt/ai/oroma; python3 tools/gap_evidence_validation.py --once --buckets ready_for_replay_review,ready_for_dream_review --topk 5 --pretty
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

from core import gap_evidence_validation  # noqa: E402


def _parse_buckets(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return gap_evidence_validation.parse_csv(raw, gap_evidence_validation.DEFAULT_BUCKETS)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Evidence Execution/Validation (read-only dry-run, no starts, no policy writes)")
    ap.add_argument("--once", action="store_true", help="Einmaligen Lauf ausfuehren")
    ap.add_argument("--pretty", action="store_true", help="JSON formatiert ausgeben")
    ap.add_argument("--db", default=None, help="Pfad zur ORÓMA SQLite DB")
    ap.add_argument("--source", default=None, help="Pfad zu gap_evidence_review.json")
    ap.add_argument("--state", default=None, help="Pfad fuer gap_evidence_validation.json")
    ap.add_argument("--buckets", default=None, help="CSV Review-Buckets")
    ap.add_argument("--limit", type=int, default=None, help="Max Review-Kandidaten pruefen")
    ap.add_argument("--topk", type=int, default=None, help="Max Items pro Validation-Bucket im JSON")
    ap.add_argument("--max-age-sec", type=int, default=None, help="Maximales Alter der Review-Quelle")
    ap.add_argument("--allow-stale", action="store_true", help="Auch stale Review-Quelle akzeptieren")
    ap.add_argument("--min-score", type=float, default=None, help="Mindestscore fuer Validation-Kandidaten")
    ap.add_argument("--min-rule-count", type=int, default=None, help="Mindestanzahl Policy-Aktionen/Evidence-Regeln")
    ap.add_argument("--min-total-n", type=int, default=None, help="Mindestanzahl Policy-Samples")
    ap.add_argument("--replay-capability-schemas", default=None, help="CSV der Schemas mit verpflichtendem Replay-Capability-Preflight")
    ap.add_argument("--replay-capability-scan-limit", type=int, default=None, help="Maximale SnapChains je Capability-Pruefung")
    ap.add_argument("--no-write-state", action="store_true", help="State-JSON nicht schreiben")
    ns = ap.parse_args(argv)

    if not ns.once:
        ns.once = True

    doc = gap_evidence_validation.build_evidence_validation(
        db_path=Path(ns.db).expanduser().resolve() if ns.db else None,
        source_path=Path(ns.source).expanduser().resolve() if ns.source else None,
        state_path=Path(ns.state).expanduser().resolve() if ns.state else None,
        buckets=_parse_buckets(ns.buckets),
        limit=ns.limit,
        topk=ns.topk,
        max_age_sec=ns.max_age_sec,
        allow_stale=True if ns.allow_stale else None,
        min_score=ns.min_score,
        min_rule_count=ns.min_rule_count,
        min_total_n=ns.min_total_n,
        replay_capability_schemas=gap_evidence_validation.parse_csv(ns.replay_capability_schemas, gap_evidence_validation.DEFAULT_REPLAY_CAPABILITY_SCHEMAS) if ns.replay_capability_schemas is not None else None,
        replay_capability_scan_limit=ns.replay_capability_scan_limit,
    )
    if not ns.no_write_state:
        gap_evidence_validation.write_state(doc, state_path=Path(ns.state).expanduser().resolve() if ns.state else None)
        summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
        summary = dict(summary)
        summary["state_written"] = True
        doc["summary"] = summary

    print(json.dumps(doc, ensure_ascii=False, indent=2 if ns.pretty else None, sort_keys=True))
    return 0 if bool(doc.get("ok", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

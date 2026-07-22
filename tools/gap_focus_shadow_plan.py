#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_focus_shadow_plan.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap-Focus Shadow Plan · Read-Only Kandidatenplan
# Version:   v0.1.0-read-only-shadow-plan
# Stand:     2026-07-09
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool liest data/state/gap_focus_consumer.json und erzeugt daraus:
#
#   data/state/gap_focus_shadow_plan.json
#
# Der Shadow-Plan macht sichtbar, welche Gap-Fokus-Kandidaten spaeter fuer
# Replay-Review, Dream-Konsolidierung, Explore-Episodenplanung oder Runner-
# Prioritaet infrage kommen. Es wird nichts ausgefuehrt.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt/Wayland/X11-/GUI-Abhaengigkeiten.
# - Kein DB-Zugriff, kein DBWriter, keine DB-Writes.
# - Kein policy_rules-/rules-Write.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Nur State-JSON-Write per tmp+fsync+replace.
# - Stale-Gate blockiert alte Consumer-Dateien sichtbar.
#
# CLI-BEISPIELE (iPhone-/SSH-sichere Einzeiler)
# ---------------------------------------------
#   cd /opt/ai/oroma; python3 tools/gap_focus_shadow_plan.py --once --pretty
#   cd /opt/ai/oroma; python3 tools/gap_focus_shadow_plan.py --once --targets replay,dream --topk 5 --pretty
#   cd /opt/ai/oroma; python3 tools/gap_focus_shadow_plan.py --once --no-write-state --pretty
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_GAP_FOCUS_CONSUMER_STATE_PATH=/opt/ai/oroma/data/state/gap_focus_consumer.json
#   OROMA_GAP_FOCUS_SHADOW_PLAN_SOURCE_PATH=/opt/ai/oroma/data/state/gap_focus_consumer.json
#   OROMA_GAP_FOCUS_SHADOW_PLAN_STATE_PATH=/opt/ai/oroma/data/state/gap_focus_shadow_plan.json
#   OROMA_GAP_FOCUS_SHADOW_PLAN_TARGETS=explore,replay,dream,runner_priority
#   OROMA_GAP_FOCUS_SHADOW_PLAN_TOPK=10
#   OROMA_GAP_FOCUS_SHADOW_PLAN_MAX_AGE_SEC=7200
#   OROMA_GAP_FOCUS_SHADOW_PLAN_ALLOW_STALE=0
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List


def _bootstrap_import_path() -> None:
    here = Path(__file__).resolve()
    base = here.parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))


_bootstrap_import_path()

from core.gap_shadow_plan import (  # noqa: E402
    DEFAULT_TARGETS,
    VERSION,
    build_shadow_plan,
    parse_csv,
    write_shadow_plan,
)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ORÓMA Gap-Focus Shadow Plan Read-Only")
    ap.add_argument("--once", action="store_true", help="Run once and exit. Present for orchestrator symmetry.")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    ap.add_argument("--no-write-state", action="store_true", help="Do not write data/state/gap_focus_shadow_plan.json.")
    ap.add_argument("--source", default="", help="Override gap_focus_consumer.json source path.")
    ap.add_argument("--state", default="", help="Override gap_focus_shadow_plan.json output path.")
    ap.add_argument("--targets", default="", help="Comma-separated targets, e.g. replay,dream,explore,runner_priority.")
    ap.add_argument("--topk", type=int, default=0, help="Max shadow-plan candidates per target.")
    ap.add_argument("--max-age-sec", type=int, default=-1, help="Max allowed consumer age before stale blocking.")
    ap.add_argument("--allow-stale", action="store_true", help="Allow stale consumer file to produce shadow candidates.")
    return ap.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))

    source = Path(args.source).expanduser().resolve() if args.source else None
    state = Path(args.state).expanduser().resolve() if args.state else None
    targets = parse_csv(args.targets, DEFAULT_TARGETS) if args.targets else None

    doc = build_shadow_plan(
        source_path=source,
        state_path=state,
        targets=targets,
        topk=args.topk if args.topk > 0 else None,
        max_age_sec=args.max_age_sec if args.max_age_sec >= 0 else None,
        allow_stale=True if args.allow_stale else None,
    )
    doc["cli"] = {"version": VERSION, "write_state_requested": not args.no_write_state}

    if not args.no_write_state:
        try:
            out_path = write_shadow_plan(doc, state)
            doc["state_path"] = str(out_path)
            if isinstance(doc.get("summary"), dict):
                doc["summary"]["state_written"] = True
        except Exception as exc:
            doc.setdefault("errors", []).append("state_write_error:%s" % exc)
            if isinstance(doc.get("summary"), dict):
                doc["summary"]["state_written"] = False
            doc["ok"] = False

    text = json.dumps(doc, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True)
    print(text)
    return 0 if doc.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

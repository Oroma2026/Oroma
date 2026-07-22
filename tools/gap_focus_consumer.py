#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_focus_consumer.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap-Focus Consumer · Read-Only View für Explore/Replay/Dream/Runner
# Version:   v0.2.0-reference-replay-strategy
# Stand:     2026-07-12
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool liest die von tools/gap_learning_bridge.py erzeugte Datei
# data/state/gap_learning_focus.json und erzeugt daraus eine nur lesende
# Verbrauchersicht:
#
#   data/state/gap_focus_consumer.json
#
# Diese Sicht enthält Buckets für:
#   - explore
#   - replay
#   - dream
#   - runner_priority
#
# Es werden keine Runner gestartet, keine Replay- oder Dream-Läufe ausgelöst,
# keine Datenbank geöffnet und keine Policy-Regeln geschrieben. Das Tool ist
# ausschließlich eine sichere, maschinenlesbare Routing-/Diagnoseschicht für den
# nächsten Architekturschritt.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt/Wayland/X11-/GUI-Abhängigkeiten.
# - Kein DB-Zugriff und kein DBWriter, weil keine DB geschrieben wird.
# - Kein policy_rules-/rules-Write.
# - Kein Subprozess-Start außer diesem Tool selbst durch den Orchestrator.
# - Nur State-JSON-Write per tmp+fsync+replace.
# - Stale-Gate verhindert, dass alte Fokusdaten still als aktuelle Lernziele
#   erscheinen.
#
# CLI-BEISPIELE (iPhone-/SSH-sichere Einzeiler)
# ---------------------------------------------
#   cd /opt/ai/oroma; python3 tools/gap_focus_consumer.py --once --pretty
#   cd /opt/ai/oroma; python3 tools/gap_focus_consumer.py --once --topk 5 --targets explore,replay --pretty
#   cd /opt/ai/oroma; python3 tools/gap_focus_consumer.py --once --no-write-state --pretty
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_GAP_LEARNING_STATE_PATH=/opt/ai/oroma/data/state/gap_learning_focus.json
#   OROMA_GAP_FOCUS_CONSUMER_STATE_PATH=/opt/ai/oroma/data/state/gap_focus_consumer.json
#   OROMA_GAP_FOCUS_CONSUMER_TARGETS=explore,replay,dream,runner_priority
#   OROMA_GAP_FOCUS_CONSUMER_TOPK=10
#   OROMA_GAP_FOCUS_CONSUMER_MAX_AGE_SEC=7200
#   OROMA_GAP_FOCUS_CONSUMER_ALLOW_STALE=0
#   OROMA_GAP_FOCUS_CONSUMER_NAMESPACE_ALLOWLIST=game:*
#   OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_SCHEMAS=snake:pro_v2
#   OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_REQUIRE_POLICY=1
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, List


def _bootstrap_import_path() -> None:
    here = Path(__file__).resolve()
    base = here.parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))


_bootstrap_import_path()

from core.gap_focus import (  # noqa: E402
    DEFAULT_TARGETS,
    VERSION,
    build_consumer_view,
    parse_csv,
    write_consumer_view,
)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ORÓMA Gap-Focus Consumer Read-Only View")
    ap.add_argument("--once", action="store_true", help="Run once and exit. Present for orchestrator symmetry.")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    ap.add_argument("--no-write-state", action="store_true", help="Do not write data/state/gap_focus_consumer.json.")
    ap.add_argument("--source", default="", help="Override gap_learning_focus.json source path.")
    ap.add_argument("--state", default="", help="Override gap_focus_consumer.json output path.")
    ap.add_argument("--targets", default="", help="Comma-separated consumers, e.g. explore,replay,dream,runner_priority.")
    ap.add_argument("--topk", type=int, default=0, help="Max candidates per consumer bucket.")
    ap.add_argument("--max-age-sec", type=int, default=-1, help="Max allowed source age before stale blocking.")
    ap.add_argument("--allow-stale", action="store_true", help="Allow stale source file to be routed as read-only candidates.")
    ap.add_argument("--namespace-allowlist", default="", help="Comma-separated namespace patterns, default game:*.")
    return ap.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))

    source = Path(args.source).expanduser().resolve() if args.source else None
    state = Path(args.state).expanduser().resolve() if args.state else None
    targets = parse_csv(args.targets, DEFAULT_TARGETS) if args.targets else None
    ns_patterns = parse_csv(args.namespace_allowlist, ("game:*",)) if args.namespace_allowlist else None

    doc = build_consumer_view(
        source_path=source,
        state_path=state,
        targets=targets,
        topk=args.topk if args.topk > 0 else None,
        max_age_sec=args.max_age_sec if args.max_age_sec >= 0 else None,
        allow_stale=True if args.allow_stale else None,
        namespace_allowlist=ns_patterns,
    )
    doc["cli"] = {"version": VERSION, "write_state_requested": not args.no_write_state}

    if not args.no_write_state:
        try:
            out_path = write_consumer_view(doc, state)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_evidence_queue_writer.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Queue Writer · DBWriter-only Queue-Write
# Version:   v0.1.0-dbwriter-evidence-queue
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses CLI-Tool liest data/state/gap_focus_shadow_plan.json und schreibt die
# enthaltenen Kandidaten als deduplizierte Review-/Evidence-Requests in die
# Tabelle gap_focus_evidence_queue. Das ist der erste echte Write nach der
# read-only Gap-Kette, aber weiterhin KEIN Policy-Write.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt/Wayland/X11-/GUI-Abhaengigkeiten.
# - DB-Writes ausschliesslich ueber DBWriter (core.db_writer_client).
# - Kein lokaler SQLite-Write-Fallback.
# - Keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Eigene Queue-Tabelle + Indizes nur bei aktivem Write-Gate.
# - Deduplizierung per request_signature.
# - Status-State: data/state/gap_evidence_queue_writer.json.
#
# CLI-BEISPIELE (iPhone-/SSH-sichere Einzeiler)
# ---------------------------------------------
#   cd /opt/ai/oroma; set -a; . ./.env.systemd; set +a; python3 tools/gap_evidence_queue_writer.py --once --pretty
#   cd /opt/ai/oroma; python3 tools/gap_evidence_queue_writer.py --once --no-write-db --pretty
#   cd /opt/ai/oroma; python3 tools/gap_evidence_queue_writer.py --once --targets replay,dream --topk 5 --pretty
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import gap_evidence_queue  # noqa: E402


def _parse_targets(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return gap_evidence_queue.parse_csv(raw, gap_evidence_queue.DEFAULT_TARGETS)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Evidence Queue Writer (DBWriter-only, no policy writes)")
    ap.add_argument("--once", action="store_true", help="Einmaligen Lauf ausfuehren")
    ap.add_argument("--pretty", action="store_true", help="JSON formatiert ausgeben")
    ap.add_argument("--source", default=None, help="Pfad zu gap_focus_shadow_plan.json")
    ap.add_argument("--state", default=None, help="Pfad fuer gap_evidence_queue_writer.json")
    ap.add_argument("--targets", default=None, help="CSV targets, z.B. explore,replay,dream,runner_priority")
    ap.add_argument("--topk", type=int, default=None, help="Max Kandidaten pro Target")
    ap.add_argument("--max-age-sec", type=int, default=None, help="Maximales Alter der Shadow-Plan-Quelle")
    ap.add_argument("--allow-stale", action="store_true", help="Alte Quelle sichtbar erlauben")
    ap.add_argument("--no-write-state", action="store_true", help="State-JSON nicht schreiben")
    ap.add_argument("--write-db", action="store_true", help="DBWriter-Queue-Write fuer diesen Lauf erzwingen")
    ap.add_argument("--no-write-db", action="store_true", help="DB-Write fuer diesen Lauf explizit deaktivieren")
    ap.add_argument("--confirm", default=None, help="Review-Confirm-Token fuer Queue-Write")
    ap.add_argument("--dbw-timeout-ms", type=int, default=None, help="DBWriter Timeout fuer Transaktion")
    ns = ap.parse_args(argv)

    if not ns.once:
        ns.once = True

    write_enable = None
    if ns.write_db:
        write_enable = True
    if ns.no_write_db:
        write_enable = False

    source_path = Path(ns.source).expanduser().resolve() if ns.source else None
    state_path = Path(ns.state).expanduser().resolve() if ns.state else None

    doc = gap_evidence_queue.build_queue_write_plan(
        source_path=source_path,
        state_path=state_path,
        targets=_parse_targets(ns.targets),
        topk=ns.topk,
        max_age_sec=ns.max_age_sec,
        allow_stale=True if ns.allow_stale else None,
        write_enable=write_enable,
        confirm_token=ns.confirm,
        dbw_timeout_ms=ns.dbw_timeout_ms,
    )
    if not ns.no_write_state:
        gap_evidence_queue.write_state(doc, state_path=state_path)
        # write_state setzt state_written nur in der geschriebenen Datei; fuer stdout
        # spiegeln wir den Status hier ebenfalls.
        summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
        summary = dict(summary)
        summary["state_written"] = True
        doc["summary"] = summary

    print(json.dumps(doc, ensure_ascii=False, indent=2 if ns.pretty else None, sort_keys=True))
    return 0 if bool(doc.get("ok", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

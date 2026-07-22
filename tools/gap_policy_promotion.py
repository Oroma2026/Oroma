#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_policy_promotion.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Policy Promotion Queue CLI · DBWriter-only · No Policy Write
# Version:   v0.2.2-safe-revalidation-refresh
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses CLI-Tool liest data/state/gap_evidence_validation.json und schreibt
# validierte Kandidaten dedupliziert in gap_policy_promotion_queue. Es ist der
# letzte Review-/Approval-Puffer vor einem spaeteren, separat gegateten
# Policy-Mini-Write.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - DB-Write nur via DBWriter-Client.
# - Kein lokaler SQLite-Schreibfallback.
# - Keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - State-Datei best-effort oroma:oroma 664 fuer Root-Manual-Laeufe.
#
# CLI-BEISPIELE (iPhone-/SSH-sichere Einzeiler)
# ---------------------------------------------
#   cd /opt/ai/oroma; set -a; . ./.env.systemd; set +a; python3 tools/gap_policy_promotion.py --once --pretty | tail -n 120
#   cd /opt/ai/oroma; python3 tools/gap_policy_promotion.py --once --no-write-state --pretty
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

from core import gap_policy_promotion  # noqa: E402


def _parse_csv_optional(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return gap_policy_promotion.parse_csv(raw)


def _parse_buckets(raw: Optional[str]) -> Optional[Sequence[str]]:
    if raw is None:
        return None
    return gap_policy_promotion.parse_csv(raw, gap_policy_promotion.DEFAULT_BUCKETS)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap Policy Promotion Queue (DBWriter-only, no policy write)")
    ap.add_argument("--once", action="store_true", help="Einmaligen Lauf ausfuehren")
    ap.add_argument("--pretty", action="store_true", help="JSON formatiert ausgeben")
    ap.add_argument("--db", default=None, help="Pfad zur ORÓMA SQLite DB")
    ap.add_argument("--source", default=None, help="Pfad zu gap_evidence_validation.json")
    ap.add_argument("--state", default=None, help="Pfad fuer gap_policy_promotion_queue_writer.json")
    ap.add_argument("--buckets", default=None, help="CSV Validation-Buckets")
    ap.add_argument("--namespaces", default=None, help="CSV Namespace-Allowlist; Glob-Muster erlaubt")
    ap.add_argument("--state-schemas", default=None, help="CSV State-Schema-Allowlist, z. B. snake:pro_v2")
    ap.add_argument("--targets", default=None, help="CSV Target-Allowlist, z. B. replay")
    ap.add_argument("--limit", type=int, default=None, help="Max Validation-Kandidaten pruefen")
    ap.add_argument("--topk", type=int, default=None, help="Max Kandidaten fuer Ausgabe und DBWriter-Transaktion")
    ap.add_argument("--max-age-sec", type=int, default=None, help="Maximales Alter der Validation-Quelle")
    ap.add_argument("--allow-stale", action="store_true", help="Auch stale Validation-Quelle akzeptieren")
    ap.add_argument("--min-score", type=float, default=None, help="Mindestscore fuer Promotion-Kandidaten")
    ap.add_argument("--write-enable", action="store_true", default=None, help="Promotion-Queue-Write explizit aktivieren")
    ap.add_argument("--confirm", default=None, help="Confirm-Token fuer Promotion-Queue-Write")
    ap.add_argument("--confirm-required", default=None, help="Erwartetes Confirm-Token")
    ap.add_argument("--dbw-timeout-ms", type=int, default=None, help="DBWriter Transaction Timeout")
    ap.add_argument("--dbw-ping-timeout-ms", type=int, default=None, help="DBWriter Ping Timeout")
    ap.add_argument("--no-write-state", action="store_true", help="State-JSON nicht schreiben")
    ns = ap.parse_args(argv)

    if not ns.once:
        ns.once = True

    doc = gap_policy_promotion.build_promotion_queue_write(
        db_path=Path(ns.db).expanduser().resolve() if ns.db else None,
        source_path=Path(ns.source).expanduser().resolve() if ns.source else None,
        state_path=Path(ns.state).expanduser().resolve() if ns.state else None,
        buckets=_parse_buckets(ns.buckets),
        namespace_allowlist=_parse_csv_optional(ns.namespaces),
        state_schema_allowlist=_parse_csv_optional(ns.state_schemas),
        target_allowlist=_parse_csv_optional(ns.targets),
        limit=ns.limit,
        topk=ns.topk,
        max_age_sec=ns.max_age_sec,
        allow_stale=True if ns.allow_stale else None,
        min_score=ns.min_score,
        write_enable=True if ns.write_enable else None,
        confirm_token=ns.confirm,
        confirm_required=ns.confirm_required,
        dbw_timeout_ms=ns.dbw_timeout_ms,
        dbw_ping_timeout_ms=ns.dbw_ping_timeout_ms,
    )
    if not ns.no_write_state:
        gap_policy_promotion.write_state(doc, state_path=Path(ns.state).expanduser().resolve() if ns.state else None)
        summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
        summary = dict(summary)
        summary["state_written"] = True
        doc["summary"] = summary

    print(json.dumps(doc, ensure_ascii=False, indent=2 if ns.pretty else None, sort_keys=True))
    return 0 if bool(doc.get("ok", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

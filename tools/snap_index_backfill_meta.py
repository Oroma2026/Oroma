#!/usr/bin/env python3
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snap_index_backfill_meta.py
# Projekt: ORÓMA
# Version: v3.7.x (Patch: snap_index MetaSnaps-only Backfill)
# Stand:   2025-12-28
# Autor:   Jörg + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
# Backfill für `snap_index` ausschließlich aus `meta_snaps` ("MetaSnaps-only").
#
# Hintergrund:
# - `snap_index` ist ein dünner Cache/Index (Fingerprint, Norm, kleine Payloads)
#   für schnellen Lookup / Dedupe / Explainability (RAG-Brücke).
# - Historische Installationen können ein älteres `snap_index`-Schema besitzen
#   (ohne ref_table/ref_id/ref_key). Dieses Tool ruft deshalb zuerst
#   `sql_manager.ensure_schema()` auf (idempotent) und schreibt dann per
#   `core.snap_indexer.index_meta_snap()`.
#
# Nutzung
# ------
#   sudo -u oroma PYTHONPATH=/opt/ai/oroma \
#     python3 /opt/ai/oroma/tools/snap_index_backfill_meta.py --batch 1000 --limit 0
#
# Parameter
# ---------
#   --batch N   : Anzahl MetaSnaps je Fetch
#   --limit N   : Maximal zu verarbeitende MetaSnaps (0 = unbegrenzt)
#   --since-id  : Nur MetaSnaps mit id > since-id
#
# Output
# ------
# JSON Summary (ok, processed, inserted, skipped, latest_meta_id)
# =============================================================================

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from typing import Any, Dict

from core import snap_indexer
from core import sql_manager


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill snap_index aus meta_snaps (MetaSnaps-only)")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--limit", type=int, default=0, help="0 = unbegrenzt")
    ap.add_argument("--since-id", type=int, default=0, help="Nur meta_snaps.id > since-id")
    args = ap.parse_args()

    # Wichtig: ensure_schema() erwartet optional einen DB-Pfad (oder None),
    # aber **keine** sqlite3.Connection.
    sql_manager.ensure_schema()

    processed = 0
    inserted = 0
    skipped = 0
    latest_meta_id = 0

    with sql_manager.get_conn() as conn:
        conn.row_factory = sqlite3.Row

        # Sicherheitsnetz: falls meta_snaps.sources in älteren DBs fehlt, liefern wir NULL.
        # In dem Fall schreibt index_meta_snap trotzdem eine sinnvolle Payload.
        # (Wir versuchen es zuerst mit sources, fallen bei Fehler zurück.)
        try:
            cur = conn.execute(
                "SELECT id, label, score, sources FROM meta_snaps WHERE id > ? ORDER BY id ASC",
                (int(args.since_id),),
            )
            have_sources = True
        except sqlite3.OperationalError:
            cur = conn.execute(
                "SELECT id, label, score, NULL as sources FROM meta_snaps WHERE id > ? ORDER BY id ASC",
                (int(args.since_id),),
            )
            have_sources = False

        while True:
            if args.limit and processed >= args.limit:
                break

            rows = cur.fetchmany(int(args.batch))
            if not rows:
                break

            for r in rows:
                if args.limit and processed >= args.limit:
                    break

                processed += 1
                meta_id = int(r["id"]) if isinstance(r, sqlite3.Row) else int(r[0])
                label = (r["label"] if isinstance(r, sqlite3.Row) else r[1]) or f"meta_{meta_id}"
                score = float((r["score"] if isinstance(r, sqlite3.Row) else r[2]) or 0.0)
                sources_raw = (r["sources"] if isinstance(r, sqlite3.Row) else r[3])

                sources = None
                if sources_raw:
                    try:
                        sources = json.loads(sources_raw)
                    except Exception:
                        sources = None

                latest_meta_id = max(latest_meta_id, meta_id)

                # index_meta_snap ist idempotent via fingerprint UNIQUE + ON CONFLICT.
                # Wir zählen "inserted" trotzdem grob als "attempt".
                try:
                    snap_indexer.index_meta_snap(
                        conn,
                        meta_id=meta_id,
                        label=str(label),
                        score=score,
                        sources=sources,
                        ts=time.time(),
                        source="backfill:meta_snaps",
                        privacy_tier="local",
                    )
                    inserted += 1
                except Exception:
                    skipped += 1

        conn.commit()

    out: Dict[str, Any] = {
        "ok": True,
        "processed": processed,
        "inserted_attempts": inserted,
        "skipped": skipped,
        "latest_meta_id": latest_meta_id,
        "note": "sources_column_present" if have_sources else "sources_column_missing_fallback_used",
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

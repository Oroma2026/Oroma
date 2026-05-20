#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/objectgraph_dedupe.py
# Projekt: ORÓMA – KI-JWG-X1
# Modul:   ObjectGraph – Duplikate in object_relations bereinigen
# Version: v0.1
# Stand:   2025-12-10
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Dieses Tool bereinigt doppelte Kanten in der Tabelle `object_relations`.
#
#   Zielmodell:
#     - Pro Tripel (a_id, relation, b_id) soll genau EINE Kante existieren.
#     - Duplikate entstehen, wenn der ObjectExtractor mehrfach über dieselben
#       SceneGraphs / ObjectGraphs läuft und jedes Mal dieselbe Relation erneut
#       einträgt.
#
#   Vorgehen:
#     1) Alle Einträge aus `object_relations` laden:
#          id, a_id, relation, b_id
#     2) Nach (a_id, relation, b_id, id) sortieren
#     3) Für jedes Tripel:
#          - den ersten Eintrag behalten
#          - alle weiteren IDs als Duplikate markieren
#     4) Duplikate optional löschen (Default: wirklich löschen)
#
#   Sicherheit:
#     - Es wird NICHT an `object_nodes` oder `scenegraphs` geändert.
#     - Nur zusätzliche, redundante Kanten verschwinden.
#
# NUTZUNG
# ───────
#   # Trockenlauf (nur zählen, nichts löschen):
#   PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_dedupe.py --dry-run
#
#   # Produktiv (Duplikate löschen):
#   PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_dedupe.py
#
#   # Optional: erhöhtes Log-Level
#   OROMA_LOG_LEVEL=DEBUG PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_dedupe.py
# =============================================================================

from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Tuple

from core import sql_manager

LOG = logging.getLogger("ObjectGraphDedupe")


def setup_logging() -> None:
    level_name = sql_manager.os.environ.get("OROMA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [ObjectGraphDedupe] %(message)s",
    )


def _load_relations(conn) -> List[Tuple[int, int, str, int]]:
    """
    Lädt alle Kanten aus object_relations, sortiert nach (a_id, relation, b_id, id).
    """
    cur = conn.execute(
        """
        SELECT id, a_id, relation, b_id
          FROM object_relations
         ORDER BY a_id, relation, b_id, id
        """
    )
    rows = [(int(r["id"]), int(r["a_id"]), str(r["relation"]), int(r["b_id"])) for r in cur]
    LOG.info("Geladene object_relations: %d", len(rows))
    return rows


def _find_duplicates(rows: List[Tuple[int, int, str, int]]) -> Tuple[List[int], Dict[str, int]]:
    """
    Bestimmt Duplikate pro (a_id, relation, b_id).

    Rückgabe:
      - Liste der IDs, die gelöscht werden sollen.
      - kleine Statistik pro relation (wie viele Duplikate entfernt würden).
    """
    to_delete: List[int] = []
    per_relation: Dict[str, int] = {}

    last_key = None  # type: ignore
    for row in rows:
        _id, a_id, relation, b_id = row
        key = (a_id, relation, b_id)
        if key != last_key:
            # erster Eintrag für dieses Tripel → behalten
            last_key = key
            continue

        # Duplikat
        to_delete.append(_id)
        per_relation[relation] = per_relation.get(relation, 0) + 1

    return to_delete, per_relation


def _delete_ids(conn, ids: List[int]) -> int:
    """
    Löscht die angegebenen IDs in Batches.
    """
    if not ids:
        return 0

    total_deleted = 0
    BATCH = 500

    for i in range(0, len(ids), BATCH):
        chunk = ids[i : i + BATCH]
        placeholders = ",".join("?" for _ in chunk)
        LOG.debug("Lösche Batch %d..%d (%d IDs)", i, i + len(chunk) - 1, len(chunk))
        conn.execute(
            f"DELETE FROM object_relations WHERE id IN ({placeholders})",
            chunk,
        )
        total_deleted += len(chunk)

    return total_deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bereinigt doppelte Kanten in object_relations (pro (a_id, relation, b_id) bleibt 1)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur zählen, keine Duplikate löschen.",
    )
    args = parser.parse_args()

    setup_logging()

    with sql_manager.get_conn() as conn:
        rows = _load_relations(conn)
        dup_ids, per_rel = _find_duplicates(rows)

        LOG.info("Duplikate gesamt: %d", len(dup_ids))
        if not dup_ids:
            LOG.info("Keine Duplikate gefunden – nichts zu tun.")
            return 0

        # kleine Statistik pro Relationstyp
        for rel, cnt in sorted(per_rel.items(), key=lambda kv: (-kv[1], kv[0])):
            LOG.info("  Relation '%s': %d Duplikate", rel, cnt)

        if args.dry_run:
            LOG.info("Dry-Run aktiv – es werden KEINE Duplikate gelöscht.")
            return 0

        deleted = _delete_ids(conn, dup_ids)
        LOG.info("Duplikate gelöscht: %d", deleted)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
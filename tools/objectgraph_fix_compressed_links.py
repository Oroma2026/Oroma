#!/usr/bin/env python3
# =============================================================================
# Pfad:    tools/objectgraph_fix_compressed_links.py
# Projekt: ORÓMA – ObjectGraph 1.0 – Reparatur-Tool für compressed_*-Links
# Version: v0.1
# Stand:   2025-12-10
# Autor:   Jörg Werner + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# -----
#   - Ergänzt fehlende Relationen für komprimierte SnapChains im ObjectGraph:
#       * meta_to_chain   :  compressed_N  ->  Chain N
#       * chain_to_origin :  Chain N       ->  origin-Knoten (z.B. vision/token)
#
#   - Hintergrund:
#       * Der ursprüngliche Objekt-Builder hat für einen ersten Batch an
#         komprimierten Chains (z.B. compressed_449xx) sauber meta_to_chain /
#         chain_to_origin erzeugt.
#       * Der neue Object-Extractor importiert zusätzliche Knoten/Edges aus
#         object:auto:vision-SceneGraphs (inkl. compressed_50xxx), legt aber
#         aktuell KEINE expliziten meta_to_chain-/chain_to_origin-Edges an.
#       * Der ObjectGraph-Audit erwartet für JEDEN compressed_*-Meta-Knoten
#         und jede passende komprimierte Chain genau eine solche Relation.
#
#   - Dieses Tool:
#       * findet alle Meta-Knoten mit label LIKE 'compressed_%',
#       * leitet daraus den erwarteten Chain-Label "Chain N" ab,
#       * prüft, ob der SnapChain-Knoten existiert,
#       * prüft, ob meta_to_chain / chain_to_origin schon existieren,
#       * legt fehlende Relationen an (oder zeigt sie in --dry-run nur an).
#
# Nutzung
# -------
#   # Trockenlauf (nur anzeigen, nichts schreiben)
#   PYTHONPATH=/opt/ai/oroma \\
#     python3 tools/objectgraph_fix_compressed_links.py --dry-run
#
#   # Produktiv (fehlende Links werden ergänzt)
#   PYTHONPATH=/opt/ai/oroma \\
#     python3 tools/objectgraph_fix_compressed_links.py
#
# Hinweise
# --------
#   - Es werden nur Paare ergänzt, bei denen sowohl:
#       * ein Meta-Knoten "compressed_NNNNN"
#       * und ein SnapChain-Knoten "Chain NNNNN"
#     existieren.
#   - Für bereits korrekt verlinkte Paare passiert nichts (Idempotenz).
#   - Audit (tools/objectgraph_audit.py) kann nach einem Lauf erneut genutzt
#     werden, um die Health zu prüfen.
# =============================================================================

import argparse
import json
import logging
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

from core import sql_manager
from core.log_guard import log_suppressed

LOG = logging.getLogger("ObjectGraphFixCompressed")


def _get_origin_node_id(conn) -> Optional[int]:
    """
    Versucht, den Origin-Knoten (z.B. vision/token) in object_nodes zu finden.

    Heuristik:
      - kind = 'origin'
      - bevorzugt label='vision/token', sonst erster origin-Knoten.
    """
    conn.row_factory = sql_manager._row_factory  # type: ignore[attr-defined]

    row = conn.execute(
        """
        SELECT id, label
          FROM object_nodes
         WHERE kind = 'origin'
         ORDER BY id ASC
         LIMIT 10
        """
    ).fetchone()

    if not row:
        LOG.warning("[FixCompressed] Kein origin-Knoten in object_nodes gefunden.")
        return None

    origin_id = row["id"]
    LOG.info(
        "[FixCompressed] Verwende origin-Knoten id=%s label=%r",
        origin_id,
        row["label"],
    )
    return origin_id


def _load_compressed_meta(conn) -> List[Tuple[int, str, Optional[int]]]:
    """
    Lädt alle Meta-Knoten mit label LIKE 'compressed_%'.

    Rückgabe: Liste von (meta_id, meta_label, chain_number | None)
    """
    conn.row_factory = sql_manager._row_factory  # type: ignore[attr-defined]

    rows = conn.execute(
        """
        SELECT id, label
          FROM object_nodes
         WHERE kind = 'meta'
           AND label LIKE 'compressed_%'
         ORDER BY id ASC
        """
    ).fetchall()

    result: List[Tuple[int, str, Optional[int]]] = []
    for r in rows:
        label = r["label"]
        m = re.match(r"compressed_(\d+)$", label)
        if not m:
            LOG.debug("[FixCompressed] Ignoriere Meta id=%s mit label=%r (kein N-Suffix)", r["id"], label)
            result.append((r["id"], label, None))
            continue
        n = int(m.group(1))
        result.append((r["id"], label, n))

    LOG.info("[FixCompressed] Gefundene compressed-Meta-Knoten: %d", len(result))
    return result


def _find_chain_node_id(conn, chain_num: int) -> Optional[int]:
    """
    Sucht den SnapChain-Knoten "Chain N" in object_nodes.

    Rückgabe: id oder None.
    """
    conn.row_factory = sql_manager._row_factory  # type: ignore[attr-defined]

    label = f"Chain {chain_num}"
    row = conn.execute(
        """
        SELECT id
          FROM object_nodes
         WHERE label = ?
           AND kind = 'snapchain'
         ORDER BY id ASC
         LIMIT 1
        """,
        (label,),
    ).fetchone()

    if not row:
        return None
    return row["id"]


def _relation_exists(conn, a_id: int, relation: str, b_id: int) -> bool:
    """
    Prüft, ob eine Relation (a_id, relation, b_id) bereits existiert.
    """
    conn.row_factory = None  # einfache Tupel reichen hier
    row = conn.execute(
        """
        SELECT 1
          FROM object_relations
         WHERE a_id = ?
           AND relation = ?
           AND b_id = ?
         LIMIT 1
        """,
        (a_id, relation, b_id),
    ).fetchone()
    return row is not None


def _insert_relation(
    conn,
    a_id: int,
    relation: str,
    b_id: int,
    source_scene_id: Optional[int],
    ts: int,
    notes: Dict,
) -> None:
    """Fügt eine Relation in object_relations ein.

    Dieses Tool nutzt bewusst einen direkten INSERT, um unabhängig von
    sql_manager-Helpern zu bleiben.

    PRODUKTIONSHINWEIS (wichtig für Headless/Orchestrator):
      - In ORÓMA laufen oft mehrere Writer parallel (AgentLoop, Dream, Timers,
        Orchestrator-Jobs). Dadurch können SQLite-Writes kollidieren.
      - Wir nutzen deshalb sql_manager.writer_lock() und loggen Fehlerpfade
        sichtbar (rate-limited), statt "silent" zu scheitern.
    """
    try:
        with sql_manager.writer_lock(
            kind="tool.objectgraph_fix_compressed.object_relations",
            timeout_sec=sql_manager._env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30),
        ):
            conn.execute(
                """
                INSERT INTO object_relations (a_id, relation, b_id, confidence,
                                              source_scene_id, ts, notes)
                VALUES (?, ?, ?, 1.0, ?, ?, ?)
                """,
                (a_id, relation, b_id, source_scene_id, ts, json.dumps(notes)),
            )
    except Exception as e:
        log_suppressed(
            LOG,
            key="tool.objectgraph_fix_compressed.insert_relation",
            msg=f"[FixCompressed] INSERT object_relations failed (a_id={a_id} rel={relation!r} b_id={b_id})",
            exc=e,
            level=logging.WARNING,
            interval_s=120,
        )
        raise


def run(dry_run: bool = False) -> None:
    """
    Hauptlogik:
      - findet compressed_*-Meta
      - matcht zu Chain N
      - ergänzt fehlende meta_to_chain / chain_to_origin
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )

    sql_manager.ensure_schema()

    now_ts = int(time.time())
    fixed_meta = 0
    fixed_chain_origin = 0
    skipped_no_chain = 0
    skipped_no_origin = 0

    with sql_manager.get_conn() as conn:
        origin_id = _get_origin_node_id(conn)
        compressed_meta = _load_compressed_meta(conn)

        for meta_id, meta_label, num in compressed_meta:
            if num is None:
                # Kein numerischer Suffix -> wir können keine Chain N ableiten.
                continue

            chain_id = _find_chain_node_id(conn, num)
            if chain_id is None:
                LOG.debug(
                    "[FixCompressed] Keine Chain für %r (N=%d) gefunden – Meta id=%s bleibt ohne Pair.",
                    meta_label,
                    num,
                    meta_id,
                )
                skipped_no_chain += 1
                continue

            # 1) meta_to_chain ergänzen, falls fehlt
            if not _relation_exists(conn, meta_id, "meta_to_chain", chain_id):
                LOG.info(
                    "[FixCompressed] Ergänze meta_to_chain: meta=%s (%s) -> chain=%s (Chain %d)",
                    meta_id,
                    meta_label,
                    chain_id,
                    num,
                )
                if not dry_run:
                    _insert_relation(
                        conn,
                        a_id=meta_id,
                        relation="meta_to_chain",
                        b_id=chain_id,
                        source_scene_id=None,
                        ts=now_ts,
                        notes={"reason": "auto_fix_compressed", "from": meta_label},
                    )
                fixed_meta += 1

            # 2) chain_to_origin ergänzen, falls fehlt
            if origin_id is None:
                skipped_no_origin += 1
                continue

            if not _relation_exists(conn, chain_id, "chain_to_origin", origin_id):
                LOG.info(
                    "[FixCompressed] Ergänze chain_to_origin: chain=%s (Chain %d) -> origin=%s",
                    chain_id,
                    num,
                    origin_id,
                )
                if not dry_run:
                    _insert_relation(
                        conn,
                        a_id=chain_id,
                        relation="chain_to_origin",
                        b_id=origin_id,
                        source_scene_id=None,
                        ts=now_ts,
                        notes={"reason": "auto_fix_compressed", "from_chain": f"Chain {num}"},
                    )
                fixed_chain_origin += 1

        if not dry_run:
            conn.commit()

    LOG.info(
        "[FixCompressed] Fertig. meta_to_chain ergänzt: %d, chain_to_origin ergänzt: %d, "
        "ohne Chain: %d, ohne Origin: %d",
        fixed_meta,
        fixed_chain_origin,
        skipped_no_chain,
        skipped_no_origin,
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repariert fehlende meta_to_chain/chain_to_origin-Links für compressed_*-Nodes im ObjectGraph."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur anzeigen, welche Relationen ergänzt würden – nichts schreiben.",
    )
    args = parser.parse_args(argv)

    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
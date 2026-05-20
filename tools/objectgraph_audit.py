#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    tools/objectgraph_audit.py
# Projekt: ORÓMA – KI-JWG-X1
# Modul:   ObjectGraph-Audit (Konsistenz-Checks für object_nodes/object_relations)
# Version: v1.1
# Stand:   2025-12-10
# Autor:   Jörg Werner + GPT-5.1 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Führe Konsistenzprüfungen für den ObjectGraph durch, insbesondere:
#
#     1) Meta → Chain:
#        - Für jeden ObjectNode mit kind='meta' und label LIKE 'compressed_%'
#          muss es GENAU EINE Relation
#
#              meta_to_chain:  meta.id (a_id) → snapchain.id (b_id)
#
#          geben, und der Zielknoten muss kind='snapchain' haben.
#        - Optionaler Zusatztcheck:
#              label(meta) == f"compressed_<NUM>"  und
#              label(snapchain) == f"Chain <NUM>"
#
#     2) Compressed-Chains:
#        - Für jeden ObjectNode mit kind='snapchain' und
#          meta.raw.meta.status == 'compressed'
#          muss gelten:
#
#          a) Genau EINE meta_to_chain-Relation:
#                meta.id (a_id) → snapchain.id (b_id)
#
#          b) Genau EINE chain_to_origin-Relation:
#                snapchain.id (a_id) → origin.id (b_id)
#
#             wobei origin.kind = 'origin' und origin.label = 'vision/token'
#
#   Damit prüfen wir, ob dein Dream-ObjectGraph sauber die Dreiecke
#   (MetaSnap ↔ Chain ↔ Origin) bildet.
#
# NUTZUNG
# ───────
#   Basis:
#     PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_audit.py
#
#   Nur JSON-Output (für weitere Verarbeitung):
#     PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_audit.py --json-only
#
#   Verbose-Log:
#     PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_audit.py --verbose
#
#   Optional:
#     --max-errors N  → wie viele Fehler-Beispiele maximal in den JSON-Details
#                       landen sollen (Default: 20)
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Tuple

from core import sql_manager

LOG = logging.getLogger("ObjectGraphAudit")


# -----------------------------------------------------------------------------
# Hilfsfunktionen: Logging
# -----------------------------------------------------------------------------
def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [ObjectGraphAudit] %(message)s",
    )


# -----------------------------------------------------------------------------
# Laden der Daten aus der DB
# -----------------------------------------------------------------------------
def _load_object_nodes(conn) -> Dict[int, Dict[str, Any]]:
    """
    Lädt alle object_nodes in ein Dict:
      id -> {id, kind, label, meta_json}

    WICHTIG:
      - Die Spalte mit den Metadaten kann in deiner DB unterschiedlich heißen,
        z.B. 'meta', 'meta_json', 'meta_blob'.
      - Deshalb laden wir *alle* Spalten und suchen nach einer Meta-Spalte.
    """
    nodes: Dict[int, Dict[str, Any]] = {}

    cur = conn.execute("SELECT * FROM object_nodes")
    for row in cur:
        # sqlite3.Row unterstützt keys()
        cols = set(row.keys())

        node_id = int(row["id"])
        kind = row["kind"]
        label = row["label"]

        raw_meta = None
        # bevorzugte Namen
        for cname in ("meta", "meta_json", "meta_blob"):
            if cname in cols:
                raw_meta = row[cname]
                break

        meta_json = None
        if raw_meta is not None:
            try:
                if isinstance(raw_meta, bytes):
                    raw_meta = raw_meta.decode("utf-8", errors="ignore")
                meta_json = json.loads(raw_meta)
            except Exception:
                meta_json = None

        nodes[node_id] = {
            "id": node_id,
            "kind": kind,
            "label": label,
            "meta_json": meta_json,
        }

    LOG.info("ObjectNodes geladen: total=%d", len(nodes))
    return nodes


def _load_object_relations(conn) -> List[Dict[str, Any]]:
    """
    Lädt alle object_relations in eine Liste.
    """
    relations: List[Dict[str, Any]] = []
    cur = conn.execute(
        "SELECT id, a_id, relation, b_id, confidence, source_scene_id, ts, notes "
        "FROM object_relations"
    )
    for row in cur:
        rid = int(row["id"])
        a_id = int(row["a_id"])
        b_id = int(row["b_id"])
        rel = row["relation"]
        conf = float(row["confidence"]) if row["confidence"] is not None else None
        source_scene_id = row["source_scene_id"]
        ts = row["ts"]
        raw_notes = row["notes"]
        notes_json = None
        if raw_notes:
            try:
                if isinstance(raw_notes, bytes):
                    raw_notes = raw_notes.decode("utf-8", errors="ignore")
                notes_json = json.loads(raw_notes)
            except Exception:
                notes_json = None
        relations.append(
            {
                "id": rid,
                "a_id": a_id,
                "b_id": b_id,
                "relation": rel,
                "confidence": conf,
                "source_scene_id": source_scene_id,
                "ts": ts,
                "notes_json": notes_json,
            }
        )
    LOG.info("ObjectRelations geladen: total=%d", len(relations))
    return relations


def _build_relation_index(
    relations: List[Dict[str, Any]]
) -> Tuple[Dict[int, List[Dict[str, Any]]], Dict[int, List[Dict[str, Any]]]]:
    """
    Erstellt zwei Indizes:
      - by_a: a_id -> [relation...]
      - by_b: b_id -> [relation...]
    """
    by_a: Dict[int, List[Dict[str, Any]]] = {}
    by_b: Dict[int, List[Dict[str, Any]]] = {}
    for r in relations:
        a = r["a_id"]
        b = r["b_id"]
        by_a.setdefault(a, []).append(r)
        by_b.setdefault(b, []).append(r)
    return by_a, by_b


# -----------------------------------------------------------------------------
# Erkennungsheuristiken
# -----------------------------------------------------------------------------
def _is_compressed_meta(node: Dict[str, Any]) -> bool:
    """
    Erkenne Meta-Knoten, die ein komprimiertes Objekt beschreiben:
      - kind == 'meta'
      - label beginnt mit 'compressed_'
    """
    return node.get("kind") == "meta" and str(node.get("label", "")).startswith(
        "compressed_"
    )


def _is_compressed_snapchain(node: Dict[str, Any]) -> bool:
    """
    Erkenne SnapChain-Knoten, die komprimierte Vision-Chains repräsentieren:

    Erwartete Struktur in meta_json:
      {
        "raw": {
          "meta": {
            "status": "compressed",
            ...
          },
          ...
        },
        ...
      }
    """
    if node.get("kind") != "snapchain":
        return False
    meta_json = node.get("meta_json") or {}
    raw = meta_json.get("raw") or {}
    inner_meta = raw.get("meta") or {}
    status = inner_meta.get("status")
    return status == "compressed"


def _extract_chain_id_from_labels(
    meta_label: str, chain_label: str
) -> Tuple[str, str]:
    """
    Extrahiere die numerische ID aus:
      - meta_label:  'compressed_44919'
      - chain_label: 'Chain 44919'

    Gibt (meta_id_str, chain_id_str) zurück (ohne harte Validierung).
    """
    meta_id = meta_label.split("compressed_", 1)[-1]
    chain_id = chain_label.split("Chain", 1)[-1].strip()
    return meta_id, chain_id


# -----------------------------------------------------------------------------
# Kern-Audit
# -----------------------------------------------------------------------------
def run_audit(max_errors: int = 20) -> Dict[str, Any]:
    """
    Führt alle definierten Checks aus und gibt einen JSON-kompatiblen Report
    als Dict zurück.
    """
    with sql_manager.get_conn() as conn:
        nodes = _load_object_nodes(conn)
        relations = _load_object_relations(conn)

    by_a, by_b = _build_relation_index(relations)

    # --- 1) Meta → Chain (compressed_*)
    meta_nodes = [n for n in nodes.values() if _is_compressed_meta(n)]
    compressed_snaps = [n for n in nodes.values() if _is_compressed_snapchain(n)]

    LOG.info(
        "Meta-Knoten (compressed_*) = %d, komprimierte SnapChains = %d",
        len(meta_nodes),
        len(compressed_snaps),
    )

    meta_ok = 0
    meta_errors: List[Dict[str, Any]] = []

    for mn in meta_nodes:
        mid = mn["id"]
        label = mn["label"]

        rels = [
            r
            for r in by_a.get(mid, [])
            if r["relation"] == "meta_to_chain"
        ]

        if len(rels) != 1:
            meta_errors.append(
                {
                    "meta_id": mid,
                    "meta_label": label,
                    "error": f"expected exactly 1 meta_to_chain, found {len(rels)}",
                }
            )
            continue

        r = rels[0]
        snap = nodes.get(r["b_id"])
        if not snap or snap.get("kind") != "snapchain":
            meta_errors.append(
                {
                    "meta_id": mid,
                    "meta_label": label,
                    "relation_id": r["id"],
                    "b_id": r["b_id"],
                    "error": "meta_to_chain target is not a snapchain",
                }
            )
            continue

        # Optional: Label-Konsistenz compressed_X vs Chain X
        try:
            mid_str, cid_str = _extract_chain_id_from_labels(
                str(label), str(snap.get("label", ""))
            )
            if mid_str != cid_str:
                meta_errors.append(
                    {
                        "meta_id": mid,
                        "meta_label": label,
                        "chain_id": snap["id"],
                        "chain_label": snap.get("label"),
                        "error": "label mismatch compressed_<ID> vs Chain <ID>",
                    }
                )
                continue
        except Exception as exc:
            meta_errors.append(
                {
                    "meta_id": mid,
                    "meta_label": label,
                    "error": f"failed to parse label ids: {exc!r}",
                }
            )
            continue

        meta_ok += 1

    # --- 2) Compressed SnapChains: meta_to_chain + chain_to_origin
    snaps_ok = 0
    snaps_errors: List[Dict[str, Any]] = []

    for sn in compressed_snaps:
        sid = sn["id"]
        slabel = sn["label"]

        # (a) genau eine meta_to_chain (b_id = sid)
        mts = [
            r
            for r in by_b.get(sid, [])
            if r["relation"] == "meta_to_chain"
        ]

        # (b) genau eine chain_to_origin (a_id = sid)
        cto = [
            r
            for r in by_a.get(sid, [])
            if r["relation"] == "chain_to_origin"
        ]

        error_msgs: List[str] = []

        if len(mts) != 1:
            error_msgs.append(f"expected 1 meta_to_chain, found {len(mts)}")

        if len(cto) != 1:
            error_msgs.append(f"expected 1 chain_to_origin, found {len(cto)}")

        if len(cto) == 1:
            target = nodes.get(cto[0]["b_id"])
            if not target:
                error_msgs.append("chain_to_origin target node not found")
            else:
                if target.get("kind") != "origin":
                    error_msgs.append(
                        f"chain_to_origin target kind mismatch: {target.get('kind')}"
                    )
                if str(target.get("label")) != "vision/token":
                    error_msgs.append(
                        f"chain_to_origin target label mismatch: {target.get('label')}"
                    )

        if error_msgs:
            snaps_errors.append(
                {
                    "snap_id": sid,
                    "snap_label": slabel,
                    "errors": error_msgs,
                }
            )
            continue

        snaps_ok += 1

    # -----------------------------------------------------------------------------
    # Zusammenfassen
    # -----------------------------------------------------------------------------
    report: Dict[str, Any] = {
        "summary": {
            "nodes_total": len(nodes),
            "relations_total": len(relations),
            "compressed_meta_nodes": len(meta_nodes),
            "compressed_snapchains": len(compressed_snaps),
            "meta_to_chain": {
                "ok": meta_ok,
                "failed": len(meta_nodes) - meta_ok,
            },
            "compressed_snapchain_links": {
                "ok": snaps_ok,
                "failed": len(compressed_snaps) - snaps_ok,
            },
        },
        "details": {
            "meta_errors": meta_errors[:max_errors],
            "compressed_snapchain_errors": snaps_errors[:max_errors],
        },
    }

    LOG.info(
        "Audit fertig: meta_ok=%d/%d, compressed_snap_ok=%d/%d",
        meta_ok,
        len(meta_nodes),
        snaps_ok,
        len(compressed_snaps),
    )
    return report


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ORÓMA – ObjectGraph-Audit (Meta↔Chain↔Origin Konsistenzcheck)"
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Nur den JSON-Report auf stdout ausgeben (kein zusätzliches Logging).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose-Logging (DEBUG) aktivieren.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=20,
        help="Maximale Anzahl von Fehler-Beispielen in den JSON-Details (Default: 20).",
    )

    args = parser.parse_args(argv)

    _setup_logging(verbose=args.verbose)

    report = run_audit(max_errors=args.max_errors)

    # JSON-Output immer auf stdout
    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
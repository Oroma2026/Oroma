#!/usr/bin/env python3
# =============================================================================
# Pfad:    tools/objectgraph_top_objects.py
# Projekt: ORÓMA – KI-JWG-X1
# Version: v0.2
# Stand:   2025-12-10
# Autor:   Jörg Werner + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# -----
#   Analysiert die Tabellen `object_nodes` und `object_relations` und ermittelt
#   die "wichtigsten" Objekte nach ihrem Grad (Degree) im ObjectGraph:
#
#     - Degree = Anzahl der Relationen, in denen ein Objekt als a_id oder b_id
#       vorkommt (nur für kind='object').
#     - Zusätzlich wird die Anzahl unterschiedlicher Relationstypen pro Objekt
#       gezählt.
#
#   v0.2 – Ergänzung:
#     - Optionale Ausblendung von "Global-Hubs" (z. B. vision/token,
#       scenegraph:vision_token:*), um eher lokale/konkrete Objekte zu sehen.
#
# Nutzung
# -------
#   Basisaufruf (Top 20 Objekte, min. Degree = 3):
#
#     PYTHONPATH=/opt/ai/oroma \
#       python3 tools/objectgraph_top_objects.py
#
#   Parameter:
#
#     --limit N
#         Anzahl der Top-Objekte (Default: 20).
#
#     --min-degree N
#         Minimale Anzahl von Relationen (Degree), damit ein Objekt in die
#         Topliste aufgenommen wird (Default: 3).
#
#     --hide-global-hubs
#         Blendt einige bekannte "global hubs" nach Label aus, z. B.
#         vision/token oder scenegraph:vision_token:*.
#
#     --json-only
#         Ausgabe als JSON, ohne Texttabelle (z. B. für weitere Verarbeitung).
#
# Beispiel:
#
#     PYTHONPATH=/opt/ai/oroma \
#       python3 tools/objectgraph_top_objects.py --limit 30 --min-degree 5
#
#     PYTHONPATH=/opt/ai/oroma \
#       python3 tools/objectgraph_top_objects.py --hide-global-hubs
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List

from core import sql_manager


LOG = logging.getLogger("ObjectGraphTopObjects")


# =============================================================================
# Hilfsfunktionen: Laden von Nodes & Relationen
# =============================================================================


def _load_object_nodes(conn) -> Dict[int, Dict[str, Any]]:
    """
    Lädt alle object_nodes in ein Dict:
        id -> {"id": int, "kind": str, "label": str, "meta": dict}
    """
    nodes: Dict[int, Dict[str, Any]] = {}
    cur = conn.execute(
        """
        SELECT id, kind, label, meta_json
          FROM object_nodes
        """
    )
    for row in cur:
        meta: Dict[str, Any]
        try:
            meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
        except Exception:
            meta = {}
        nodes[row["id"]] = {
            "id": row["id"],
            "kind": row["kind"],
            "label": row["label"],
            "meta": meta,
        }
    return nodes


def _load_object_relations(conn) -> List[Dict[str, Any]]:
    """
    Lädt alle object_relations in eine Liste von Dicts.
    """
    rels: List[Dict[str, Any]] = []
    cur = conn.execute(
        """
        SELECT id, a_id, relation, b_id, confidence, source_scene_id, ts, notes
          FROM object_relations
        """
    )
    for row in cur:
        rels.append(
            {
                "id": row["id"],
                "a_id": row["a_id"],
                "relation": row["relation"],
                "b_id": row["b_id"],
                "confidence": row["confidence"],
                "source_scene_id": row["source_scene_id"],
                "ts": row["ts"],
                "notes": row["notes"],
            }
        )
    return rels


# =============================================================================
# Kernlogik: Degree & Rel-Typen pro Objekt
# =============================================================================


def _compute_object_degrees(
    nodes: Dict[int, Dict[str, Any]],
    relations: List[Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """
    Berechnet für alle Nodes mit kind='object' den Degree und die Anzahl
    unterschiedlicher Relationstypen, in denen sie vorkommen.

    Rückgabe:
      object_id -> {
          "id": int,
          "label": str,
          "kind": "object",
          "degree": int,
          "relation_types": set[str],
      }
    """
    object_ids = {
        node_id
        for node_id, n in nodes.items()
        if n.get("kind") == "object"
    }

    stats: Dict[int, Dict[str, Any]] = {
        oid: {
            "id": oid,
            "label": nodes[oid].get("label") or "",
            "kind": "object",
            "degree": 0,
            "relation_types": set(),  # type: ignore[assignment]
        }
        for oid in object_ids
    }

    for rel in relations:
        rel_type = rel.get("relation") or ""
        a_id = rel.get("a_id")
        b_id = rel.get("b_id")

        if a_id in stats:
            stats[a_id]["degree"] += 1
            if rel_type:
                stats[a_id]["relation_types"].add(rel_type)  # type: ignore[arg-type]

        if b_id in stats:
            stats[b_id]["degree"] += 1
            if rel_type:
                stats[b_id]["relation_types"].add(rel_type)  # type: ignore[arg-type]

    return stats


def _is_global_hub_label(label: str) -> bool:
    """
    Heuristik: erkennt typische "Global-Hubs", die wir optional ausblenden
    wollen, z. B.:
      - 'vision/token'
      - 'scenegraph:vision_token:hoch'
      - 'scenegraph:vision_token:niedrig'
    """
    if not label:
        return False

    if label == "vision/token":
        return True

    if label.startswith("scenegraph:vision_token:"):
        return True

    return False


def _select_top_objects(
    stats: Dict[int, Dict[str, Any]],
    min_degree: int,
    limit: int,
    hide_global_hubs: bool = False,
) -> List[Dict[str, Any]]:
    """
    Filtert nach min_degree und sortiert nach Degree (absteigend) und ID
    (aufsteigend) als Tiebreak.

    Optional:
      - hide_global_hubs=True blendet bestimmte Label-Muster aus.
    """
    candidates: List[Dict[str, Any]] = []

    for s in stats.values():
        degree = int(s.get("degree", 0))
        if degree < min_degree:
            continue

        if hide_global_hubs:
            label = s.get("label") or ""
            if _is_global_hub_label(label):
                continue

        candidates.append(s)

    candidates.sort(
        key=lambda s: (-int(s.get("degree", 0)), int(s.get("id", 0)))
    )

    if limit > 0:
        candidates = candidates[:limit]

    # relation_types in eine sortierte Liste wandeln
    for s in candidates:
        rel_types = sorted(list(s.get("relation_types") or []))
        s["relation_types"] = rel_types
        s["relation_type_count"] = len(rel_types)

    return candidates


# =============================================================================
# CLI / Ausgaben
# =============================================================================


def run_top_objects(
    limit: int = 20,
    min_degree: int = 3,
    hide_global_hubs: bool = False,
) -> Dict[str, Any]:
    """
    Führt die komplette Analyse aus und liefert ein Dict für JSON/Weiterverarbeitung.
    """
    with sql_manager.get_conn() as conn:
        nodes = _load_object_nodes(conn)
        relations = _load_object_relations(conn)

    nodes_total = len(nodes)
    object_nodes_total = sum(1 for n in nodes.values() if n.get("kind") == "object")

    LOG.info(
        "Geladene ObjectNodes: total=%d (davon kind='object': %d)",
        nodes_total,
        object_nodes_total,
    )
    LOG.info("Geladene ObjectRelations: total=%d", len(relations))

    if not nodes or not relations or object_nodes_total == 0:
        raise RuntimeError(
            "ObjectGraph scheint leer zu sein (keine Nodes/Relations oder keine kind='object')."
        )

    stats = _compute_object_degrees(nodes, relations)
    top_objs = _select_top_objects(
        stats,
        min_degree=min_degree,
        limit=limit,
        hide_global_hubs=hide_global_hubs,
    )

    result: Dict[str, Any] = {
        "summary": {
            "nodes_total": nodes_total,
            "object_nodes_total": object_nodes_total,
            "relations_total": len(relations),
            "min_degree": min_degree,
            "limit": limit,
            "top_objects_count": len(top_objs),
            "hide_global_hubs": hide_global_hubs,
        },
        "objects": top_objs,
    }
    return result


def _print_text_report(report: Dict[str, Any]) -> None:
    """
    Gibt einen einfachen Textbericht auf STDOUT aus.
    """
    summary = report["summary"]
    objects = report["objects"]

    print("ObjectGraph – Top-Objekte nach Degree")
    print("=====================================")
    print(
        f"ObjectNodes (gesamt): {summary['nodes_total']} "
        f"(davon kind='object': {summary['object_nodes_total']})"
    )
    print(f"ObjectRelations:       {summary['relations_total']}")
    print(
        f"Filter: min_degree={summary['min_degree']} "
        f"hide_global_hubs={summary['hide_global_hubs']} "
        f"→ Top {summary['top_objects_count']} von Limit={summary['limit']}"
    )
    print()

    if not objects:
        print("Keine Objekte erfüllen den Filter.")
        return

    header = f"{'ID':>6}  {'Degree':>6}  {'RelTypes':>8}  Label"
    print(header)
    print("-" * len(header))

    for obj in objects:
        oid = obj["id"]
        degree = obj["degree"]
        rel_type_count = obj.get("relation_type_count", 0)
        label = obj.get("label") or ""

        if len(label) > 60:
            label = label[:57] + "..."

        print(f"{oid:6d}  {degree:6d}  {rel_type_count:8d}  {label}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analysiert object_nodes/object_relations und listet die "
            "Top-Objekte nach Degree auf."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Anzahl der Top-Objekte (Default: 20).",
    )
    parser.add_argument(
        "--min-degree",
        type=int,
        default=3,
        help="Mindest-Degree (Anzahl Relationen), damit ein Objekt in die "
        "Topliste aufgenommen wird (Default: 3).",
    )
    parser.add_argument(
        "--hide-global-hubs",
        action="store_true",
        help="Blendet bekannte Global-Hubs (vision/token, "
        "scenegraph:vision_token:*) aus.",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Nur JSON-Ausgabe (kein Textbericht).",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [ObjectGraphTopObjects] %(message)s",
    )

    try:
        report = run_top_objects(
            limit=args.limit,
            min_degree=args.min_degree,
            hide_global_hubs=args.hide_global_hubs,
        )
    except Exception as exc:
        LOG.error("Fehler beim Top-Objekt-Run: %s", exc)
        return 1

    if args.json_only:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        _print_text_report(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
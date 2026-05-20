#!/usr/bin/env python3
# =============================================================================
# Pfad:    core/object_extractor.py
# Projekt: ORÓMA – KI-JWG-X1
# Version: v0.2
# Stand:   2025-12-10
# Autor:   Jörg Werner + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# -----
#  - Liest bestehende SceneGraphs aus der Tabelle `scenegraphs`.
#  - Erzeugt daraus Einträge in:
#        - object_nodes
#        - object_relations
#  - Damit entsteht eine explizite Objekt-/Szenen-Schicht (2.5D / 3D SnapSpace),
#    die von UI (/objects), Tools und Reasonern genutzt werden kann.
#
# Kontext
# -------
#  - SceneGraphs liegen als JSON in der Tabelle `scenegraphs` vor
#      (siehe core/scenegraph_store.py).
#  - Dieser Extractor ist bewusst konservativ:
#      * keine Schema-Änderungen,
#      * keine harten Annahmen über spezielle Namespaces,
#      * robustes Error-Handling.
#
# ObjectGraph 1.0
# ---------------
#  - object_nodes:
#        id         INTEGER PRIMARY KEY AUTOINCREMENT
#        kind       TEXT NOT NULL
#        label      TEXT NOT NULL
#        meta_json  TEXT
#        created_ts INTEGER NOT NULL
#  - object_relations:
#        id             INTEGER PRIMARY KEY AUTOINCREMENT
#        a_id           INTEGER NOT NULL
#        relation       TEXT NOT NULL
#        b_id           INTEGER NOT NULL
#        confidence     REAL NOT NULL DEFAULT 1.0
#        source_scene_id INTEGER
#        ts             INTEGER NOT NULL
#        notes          TEXT
#
# Semantische Heuristik (Option 2 – Stand 10.12.2025)
# ---------------------------------------------------
#  - Ziel:
#      * kind="object" soll mittel-/langfristig wirklich "Objekte/Konzepte"
#        repräsentieren (z.B. aggregierte Vision-Objekte, stabile Cluster).
#      * Technische Knoten (meta/origin/scene/snapchain/...) bleiben als solche
#        klassifiziert.
#  - Umsetzung:
#      * Basierend auf:
#          - SceneGraph-Namespace (object:auto:... vs. scene:auto_meta:...)
#          - ursprünglichem Node-Kind (base_kind)
#          - leichten Pattern-Heuristiken (z.B. "Chain 4711" → snapchain)
#      * Wir schreiben meta_json so, dass sowohl base_kind als auch final_kind
#        nachvollziehbar bleiben.
#
# Nutzung (CLI)
# -------------
#  - Dry-Run (nur zählen, nichts in die DB schreiben):
#
#      PYTHONPATH=/opt/ai/oroma \\
#        python3 -m core.object_extractor \\
#        --dry-run --verbose
#
#  - Nur bestimmte Namespaces:
#
#      PYTHONPATH=/opt/ai/oroma \\
#        python3 -m core.object_extractor \\
#        --namespace scene:auto_meta:vision_token \\
#        --namespace object:auto:vision \\
#        --max-graphs 100
#
#  - Produktiver Lauf (füllt object_nodes/object_relations):
#
#      PYTHONPATH=/opt/ai/oroma \\
#        python3 -m core.object_extractor \\
#        --max-graphs 200
#
#   Hinweis:
#   --------
#   - Der Extractor ist idempotent im Sinne von: ensure_object_node() legt für
#     (kind, label) nur einmal einen Node an und gibt sonst die bestehende ID
#     zurück.
#   - object_relations werden dagegen immer neu eingefügt; Dedupe passiert über
#     tools/objectgraph_dedupe.py.
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

from . import sql_manager

LOG = logging.getLogger(__name__)


DEFAULT_NAMESPACES: Tuple[str, ...] = (
    "scene:auto_meta:vision_token",
    "object:auto:vision",
)


# --------------------------------------------------------------------------- #
# Semantische Klassifikation von Objekt-Knoten (Option 2 – sanfte Heuristik)
# --------------------------------------------------------------------------- #
#
# Ziel:
#   - kind="object" soll mittel-/langfristig wirklich "Objekte/Konzepte" meinen.
#   - Technische Knoten (meta/origin/scene/snapchain/...) bleiben als solche
#     klassifiziert und können im Reasoner leichter ausgeblendet werden.
#   - Die Heuristik ist bewusst konservativ und basiert auf:
#       * dem SceneGraph-Namespace (object:auto:... vs. scene:auto_meta:...)
#       * dem ursprünglichen Node-Kind
#       * leichten Pattern-Heuristiken für SnapChains.
#
# WICHTIG:
#   - Es werden keine bestehenden Tabellen geändert.
#   - Die Klassifikation wirkt nur auf neu extrahierte ObjectGraph-Knoten.
#   - Bestehende object_nodes bleiben unverändert, bis der Extractor erneut läuft.

OBJECT_CANDIDATE_KINDS: Tuple[str, ...] = (
    "object",
    "entity",
    "item",
    "cluster",
    "token_cluster",
    "region",
    "bbox",
    "slot",
)

TECHNICAL_KINDS: Tuple[str, ...] = (
    "meta",
    "origin",
    "scene",
    "snapchain",
    "edge",
    "relation",
    "episode",
    "game",
    "agent",
    "context",
    "metric",
    "policy",
    "rule",
)


def classify_node_kind(namespace: str, node: Dict[str, Any]) -> Tuple[str, str]:
    """
    Bestimmt das finale kind für object_nodes unter Berücksichtigung
    einer sanften Semantik-Heuristik.

    Rückgabewert:
      (final_kind, base_kind)

    - base_kind: ursprüngliches Node-Kind aus dem SceneGraph
    - final_kind: das in object_nodes zu speichernde kind
    """
    base_kind = _node_kind(node)
    bk = (base_kind or "").lower()
    ns = (namespace or "").lower()

    # 1) Technische Knoten bleiben technisch
    if bk in TECHNICAL_KINDS:
        return bk, base_kind

    # 2) Heuristik: SnapChains erkennen (auch wenn kind="node"/"generic"):
    node_id = str(node.get("id") or "")
    label = str(node.get("label") or "")
    if (
        (node_id.startswith("chain:") or label.startswith("Chain "))
        and bk not in OBJECT_CANDIDATE_KINDS
    ):
        return "snapchain", base_kind

    # 3) Explizite Objekt-Kandidaten → "object"
    if bk in OBJECT_CANDIDATE_KINDS:
        return "object", base_kind

    # 4) Namespace-basiert:
    #    In object:auto:* sind die Knoten Ergebnis des ObjectGraph-Builders.
    #    Alles, was nicht explizit technisch ist, wird dort als "object" behandelt.
    if ns.startswith("object:auto:"):
        if bk in ("", "node", "generic", "graph_node", "token"):
            return "object", base_kind

    # 5) Fallback: ursprüngliches Kind beibehalten
    return base_kind, base_kind


# --------------------------------------------------------------------------- #
# Hilfsfunktionen für SceneGraph-Lesen
# --------------------------------------------------------------------------- #


def _iter_scenegraphs(
    conn,
    namespaces: Sequence[str],
    max_graphs: Optional[int] = None,
) -> Iterable[Dict[str, Any]]:
    """
    Liefert Rows aus der Tabelle `scenegraphs` inklusive graph_json,
    gefiltert nach Namespace-Liste.

    Robustheit:
      - Unterstützt exakte Namespaces **und** Prefix/Wildcard-Matches:
          * "object:auto:"     → LIKE "object:auto:%"
          * "object:auto:*"    → LIKE "object:auto:%"
          * "object:auto:%"    → LIKE "object:auto:%"

    Hintergrund:
      - In einigen ORÓMA-Versionen/Setups können Namespaces Suffixe tragen
        (z.B. "object:auto:vision:v2", "object:auto:vision_token").
      - Ohne LIKE-Unterstützung bleibt der Extractor dann stillschweigend ohne Treffer.
    """
    if not namespaces:
        namespaces = DEFAULT_NAMESPACES

    exact: List[str] = []
    like: List[str] = []

    for ns in namespaces:
        s = (ns or "").strip()
        if not s:
            continue
        if s.endswith("*"):
            like.append(s[:-1] + "%")
        elif "%" in s:
            like.append(s)
        elif s.endswith(":"):
            like.append(s + "%")
        else:
            exact.append(s)

    clauses: List[str] = []
    params: List[Any] = []

    if exact:
        placeholders = ",".join("?" for _ in exact)
        clauses.append(f"namespace IN ({placeholders})")
        params.extend(exact)

    if like:
        clauses.append("(" + " OR ".join("namespace LIKE ?" for _ in like) + ")")
        params.extend(like)

    if not clauses:
        exact = list(DEFAULT_NAMESPACES)
        placeholders = ",".join("?" for _ in exact)
        clauses = [f"namespace IN ({placeholders})"]
        params = list(exact)

    where = " OR ".join(clauses)
    sql = f"""
        SELECT id, ts, namespace, source, quality, graph_json
          FROM scenegraphs
         WHERE {where}
         ORDER BY id DESC
    """

    if max_graphs is not None:
        sql += " LIMIT ?"
        params.append(int(max_graphs))

    # WICHTIG (ORÓMA-spezifisch):
    # ----------------------------
    # sql_manager.get_conn(...) liefert eine _ClosingConnection, die beim Verlassen
    # eines `with conn:`-Blocks die Verbindung *wirklich schließt* (siehe
    # core/sql_manager.py → class _ClosingConnection.__exit__).
    #
    # _iter_scenegraphs() ist ein Generator. Wenn wir hier `with conn:` verwenden,
    # wird die Connection bereits beim ersten Yield/bei Generator-Teilverbrauch
    # geschlossen → nachgelagerte DB-Operationen enden in:
    #   "Cannot operate on a closed database."
    #
    # Daher: KEIN `with conn:` in Generatoren. Die Lebensdauer der Connection wird
    # ausschließlich vom aufrufenden Kontext gemanagt (run_extractor() öffnet und
    # schließt die Read-Connection explizit).
    cur = None
    try:
        conn.row_factory = sql_manager._row_factory  # type: ignore[attr-defined]
        cur = conn.execute(sql, params)
        for row in cur:
            yield row
    finally:
        try:
            if cur is not None:
                cur.close()
        except Exception:
            # Cursor-Close ist best effort; Fehler hier dürfen den Lauf nicht kippen.
            pass


def _load_graph(graph_json: Any) -> Dict[str, Any]:
    """
    Wandelt graph_json aus der DB in ein Dict um.
    """
    if graph_json is None:
        return {}
    if isinstance(graph_json, (bytes, bytearray)):
        try:
            graph_json = graph_json.decode("utf-8")
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.object_extractor.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
    if isinstance(graph_json, str):
        try:
            return json.loads(graph_json)
        except Exception:
            LOG.exception("object_extractor: konnte graph_json nicht parsen")
            return {}
    if isinstance(graph_json, dict):
        return graph_json
    return {}


# --------------------------------------------------------------------------- #
# Extraction-Logik: SceneGraph → ObjectGraph
# --------------------------------------------------------------------------- #


def _node_key(node: Dict[str, Any]) -> str:
    """
    Extrahiert eine Node-ID aus einem Knoten-Dict.

    Bevorzugt:
      - "id", "node_id", "name"
      - sonst ID.
    """
    for key in ("id", "node_id", "name"):
        v = node.get(key)
        if isinstance(v, str) and v:
            return v
    # Fallback: Darstellung des Dicts
    return repr(node)[:64]


def _node_kind(node: Dict[str, Any]) -> str:
    """
    Bestimmt die Art des Knotens. Fällt robust auf 'node' zurück.
    """
    for key in ("kind", "type", "node_type"):
        v = node.get(key)
        if isinstance(v, str) and v:
            return v
    return "node"


def _node_label(node: Dict[str, Any]) -> str:
    """
    Label für den Objektknoten.

    Bevorzugt:
      - "label", "name", "title"
      - sonst Key (z.B. "chain:4711").
    """
    for key in ("label", "name", "title"):
        v = node.get(key)
        if isinstance(v, str) and v:
            return v
    return _node_key(node)


def _edge_endpoints(edge: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrahiert (source_id, target_id) aus einer Kante. Versucht mehrere Feldnamen.
    """
    src = edge.get("source") or edge.get("src") or edge.get("from")
    dst = edge.get("target") or edge.get("dst") or edge.get("to")

    if not isinstance(src, str):
        src = None
    if not isinstance(dst, str):
        dst = None

    return src, dst


def _edge_relation(edge: Dict[str, Any]) -> str:
    """
    Bestimmt den Relationstyp.

    Bevorzugt:
      - "relation", "label", "type"
      - Fallback: "rel".
    """
    for key in ("relation", "label", "type", "rel"):
        v = edge.get(key)
        if isinstance(v, str) and v:
            return v
    return "relation"


def _edge_confidence(edge: Dict[str, Any]) -> float:
    """
    Bestimmt eine Confidence für die Relation.

    Bevorzugt:
      - "confidence", "score", "weight"
      - Fallback: 1.0
    """
    for key in ("confidence", "score", "weight"):
        v = edge.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return 1.0


def extract_objects_from_scenegraph_row(
    row: Dict[str, Any],
    db_path: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> Tuple[int, int]:
    """
    Verarbeitet genau einen SceneGraph-Row:

      - erzeugt object_nodes für alle Knoten
      - erzeugt object_relations für alle Kanten

    Rückgabewert: (anzahl_nodes, anzahl_relations), die *neu* angelegt wurden.
    """
    graph = _load_graph(row["graph_json"])
    nodes: List[Dict[str, Any]] = graph.get("nodes") or []
    edges: List[Dict[str, Any]] = graph.get("edges") or []

    node_id_map: Dict[str, int] = {}
    new_nodes = 0
    new_edges = 0

    # --- Nodes → object_nodes ------------------------------------------------
    for node in nodes:
        key = _node_key(node)
        kind, base_kind = classify_node_kind(row["namespace"], node)
        label = _node_label(node)

        meta = {
            "scenegraph_id": row["id"],
            "namespace": row["namespace"],
            "source": row["source"],
            "quality": row["quality"],
            "base_kind": base_kind,
            "final_kind": kind,
            "raw": node,
        }

        if dry_run:
            # keine DB-Schreibzugriffe
            obj_id = -1
        else:
            obj_id = sql_manager.ensure_object_node(kind=kind, label=label, meta=meta, db_path=db_path)

        node_id_map[key] = obj_id
        new_nodes += 1

    # --- Edges → object_relations -------------------------------------------
    for edge in edges:
        src_key, dst_key = _edge_endpoints(edge)
        if not src_key or not dst_key:
            continue

        if src_key not in node_id_map or dst_key not in node_id_map:
            continue  # sollte nicht passieren, aber wir sind vorsichtig

        if dry_run:
            new_edges += 1
            continue

        relation = _edge_relation(edge)
        confidence = _edge_confidence(edge)

        # Notes = restliches Edge-Dict ohne Endpunkte
        notes = {
            k: v
            for k, v in edge.items()
            if k not in ("source", "src", "from", "target", "dst", "to")
        } or None

        sql_manager.insert_object_relation(
            a_id=node_id_map[src_key],
            relation=relation,
            b_id=node_id_map[dst_key],
            confidence=confidence,
            source_scene_id=row["id"],
            ts=row["ts"],
            notes=notes,
        )
        new_edges += 1

    if verbose:
        LOG.info(
            "object_extractor: scenegraph id=%s -> %d nodes, %d relations (namespace=%s)",
            row["id"],
            new_nodes,
            new_edges,
            row["namespace"],
        )

    return new_nodes, new_edges


def run_extractor(
    namespaces: Sequence[str],
    max_graphs: Optional[int],
    dry_run: bool,
    verbose: bool,
    db_path: Optional[str] = None,
) -> None:
    """
    Hauptfunktion für CLI-Nutzung.

    Wichtiger Architekturpunkt:
      - Wir trennen Lesen (SceneGraphs) und Schreiben (ObjectGraph):
        1) Alle relevanten SceneGraphs werden in einem Rutsch aus der DB gelesen.
        2) Die Verbindnung für das Lesen wird geschlossen.
        3) Erst dann werden über sql_manager.ensure_object_node() /
           sql_manager.insert_object_relation() neue Verbindungen zum Schreiben
           aufgebaut.

      Hintergrund:
        - SQLite erlaubt immer nur einen Schreibenden zur Zeit.
        - Gleichzeitige Lese- und Schreibverbindungen aus demselben Prozess
          können bei langen Lese-Transaktionen zu "database is locked" führen.
        - Durch die Trennung minimieren wir das Risiko von Locks deutlich, ohne
          das Schema zu ändern.
    """
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    # Schema sicherstellen (inkl. object_nodes/object_relations)
    sql_manager.ensure_schema(db_path=db_path)

    # 1) SceneGraphs lesen (eine Verbindung, dann schließen)
    scene_rows: List[Dict[str, Any]] = []
    with sql_manager.get_conn(db_path) as conn:
        for row in _iter_scenegraphs(conn, namespaces, max_graphs=max_graphs):
            # Wir kopieren das Row-Objekt in ein "normales" Dict, damit
            # wir es nach Schließen der Verbindung weiter nutzen können.
            scene_rows.append(
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "namespace": row["namespace"],
                    "source": row["source"],
                    "quality": row["quality"],
                    "graph_json": row["graph_json"],
                }
            )


    LOG.info(
        "object_extractor: query namespaces=%s max_graphs=%s",
        ",".join([str(x) for x in namespaces]) if namespaces else "(default)",
        str(max_graphs),
    )
    LOG.info("object_extractor: matched_scenegraphs=%d", len(scene_rows))

    if not scene_rows:
        # Hilfsdiagnose: welche Namespaces existieren überhaupt?
        try:
            with sql_manager.get_conn(db_path) as c2:
                top = c2.execute(
                    "SELECT namespace, COUNT(*) AS c FROM scenegraphs GROUP BY namespace ORDER BY c DESC LIMIT 12"
                ).fetchall()
            LOG.warning("object_extractor: keine Treffer. Top scenegraph namespaces (max 12): %s", top)
        except Exception:
            LOG.warning("object_extractor: keine Treffer und Namespace-Diagnose fehlgeschlagen.")
        return

    # 2) Schreiben (ObjectGraph) – hier werden neue Verbindungen geöffnet
    total_nodes = 0
    total_edges = 0

    for row in scene_rows:
        n_nodes, n_edges = extract_objects_from_scenegraph_row(
            row=row,
            db_path=db_path,
            dry_run=dry_run,
            verbose=verbose,
        )
        total_nodes += n_nodes
        total_edges += n_edges

    LOG.info(
        "object_extractor: fertig – total_nodes=%d, total_relations=%d (dry_run=%s)",
        total_nodes,
        total_edges,
        dry_run,
    )
# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Extrahiert Objektknoten und Relationen aus SceneGraphs "
            "in die Tabellen object_nodes und object_relations."
        )
    )
    p.add_argument(
        "--namespace",
        dest="namespaces",
        action="append",
        help=(
            "SceneGraph-Namespace (z.B. scene:auto_meta:vision_token, object:auto:vision). "
            "Kann mehrfach angegeben werden. Default: beide."
        ),
    )

    p.add_argument(
        "--namespace-prefix",
        dest="namespace_prefixes",
        action="append",
        help=(
            "Namespace-Prefix (LIKE), z.B. 'object:auto:' oder 'object:auto:*'. "
            "Kann mehrfach angegeben werden. Wird intern als LIKE 'prefix%%' behandelt."
        ),
    )

    p.add_argument(
        "--max-graphs",
        type=int,
        default=None,
        help="Maximale Anzahl SceneGraphs, die verarbeitet werden (neueste zuerst).",
    )
    p.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Optionaler Pfad zur oroma.db (Standard: aus sql_manager).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur zählen, nichts schreiben.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Ausführliches Logging.",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    args = _parse_args(argv)

    # Namespaces zusammenbauen (exakt + optional Prefixe).
    namespaces: List[str] = []
    if args.namespaces:
        namespaces.extend([s.strip() for s in (args.namespaces or []) if (s or "").strip()])

    if getattr(args, "namespace_prefixes", None):
        namespaces.extend([s.strip() for s in (args.namespace_prefixes or []) if (s or "").strip()])

    if not namespaces:
        namespaces = list(DEFAULT_NAMESPACES)

    run_extractor(
        namespaces=namespaces,
        max_graphs=args.max_graphs,
        dry_run=bool(args.dry_run),
        verbose=bool(args.verbose),
        db_path=args.db_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

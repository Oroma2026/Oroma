#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    tools/objectgraph_selfcheck.py
# Projekt: ORÓMA – KI-JWG-X1
# Modul:   ObjectGraph Selfcheck (Health & Stats)
# Version: v3.7.3-r3
# Stand:   2025-12-12
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# -----
#  Dieses Tool bewertet den Zustand der ObjectGraph-Ebene:
#
#    - object_nodes          (Objekt-/Meta-/Chain-Knoten)
#    - object_relations      (Relationen zwischen den Knoten)
#    - scenegraphs           (ObjectGraph-aggregierte SceneGraphs,
#                             namespace 'object:auto:*')
#
#  Es liefert:
#
#    1) Basis-Statistiken (Anzahlen, Verteilungen, Integrität)
#    2) Erweiterte Metriken (v1.5):
#       - semantic:   Verteilung kinds/relations, exotische Relationen
#       - quality:    Degree-Statistik, Dangling Nodes, Top-Knoten
#       - consistency:FK-Integrität + meta_json-Checks + SceneGraph-Status
#       - time:       Zeitspanne und Aktivität der ObjectNodes
#       - health:     Aggregierte Bewertung (ok/warning/error + Exit-Code)
#
#  Ausgabe:
#
#    - Logging auf STDOUT (INFO/ERROR)
#    - Optional: JSON-Report (--json), der z.B. in CI oder Monitoring
#      ausgewertet werden kann.
#
# Aufruf-Beispiele
# ----------------
#
#    # Nur Logging, Standard-DB
#    PYTHONPATH=/opt/ai/oroma \
#      python3 tools/objectgraph_selfcheck.py
#
#    # Spezifische DB-Datei, JSON-Ausgabe
#    PYTHONPATH=/opt/ai/oroma \
#      python3 tools/objectgraph_selfcheck.py --db data/oroma.db --json
#
#    # Namespace einschränken (falls später mehrere ObjectGraph-Namespaces)
#    python3 tools/objectgraph_selfcheck.py \
#      --db /opt/ai/oroma/data/oroma.db \
#      --namespace-prefix object:auto:
#
# Konventionen
# ------------
#
#  - Dieses Tool greift *read-only* auf die SQLite-DB zu.
#  - Es ist tolerant gegenüber fehlenden Tabellen (liefert dann 0-Werte).
#  - Es arbeitet bewusst mit begrenzten Mengen (LIMIT), um auch bei großen
#    DBs schnell zu bleiben. In deinem Backup-Szenario sind die Tabellen durch
#    den "DB-Slimmer" bereits auf die letzten N Zeilen begrenzt.
#
# Environment-Variablen (optional)
# --------------------------------
#
#  - OROMA_DB_PATH       Pfad zur oroma.db (Default: data/oroma.db)
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import statistics
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# interne "Version" des Selfcheck-Reports (nicht Projektversion)
SELF_VERSION = "1.5"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging(verbose: bool = False) -> None:
    """Initialisiert das Logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [ObjectGraphSelfcheck] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# SQLite-Helfer
# ---------------------------------------------------------------------------


def _open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None


def _get_table_row_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    cur = conn.execute(f"SELECT COUNT(*) AS c FROM {table}")
    row = cur.fetchone()
    return int(row["c"]) if row and row["c"] is not None else 0


def _get_min_max(
    conn: sqlite3.Connection, table: str, column: str
) -> Tuple[Optional[float], Optional[float]]:
    if not _table_exists(conn, table):
        return None, None
    cur = conn.execute(
        f"SELECT MIN({column}) AS min_v, MAX({column}) AS max_v FROM {table} "
        f"WHERE {column} IS NOT NULL"
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return row["min_v"], row["max_v"]


# ---------------------------------------------------------------------------
# Basis-Statistiken
# ---------------------------------------------------------------------------


def summarize_object_nodes(conn: sqlite3.Connection, limit_kinds: int = 20) -> Dict[str, Any]:
    """Liest Basis-Statistiken aus object_nodes."""
    total = _get_table_row_count(conn, "object_nodes")

    kinds: List[Dict[str, Any]] = []
    if total > 0:
        cur = conn.execute(
            """
            SELECT kind, COUNT(*) AS c
              FROM object_nodes
             GROUP BY kind
             ORDER BY c DESC, kind ASC
             LIMIT ?
            """,
            (limit_kinds,),
        )
        for r in cur.fetchall():
            kinds.append({"kind": r["kind"], "count": int(r["c"])})

    _, recent_ts = _get_min_max(conn, "object_nodes", "created_ts")

    logging.info("ObjectNodes: total=%s", total)
    if kinds:
        logging.info(
            "  Top-Kinds: %s",
            ", ".join(f"{k['kind']}={k['count']}" for k in kinds),
        )
    if recent_ts is not None:
        logging.info("  recent_ts: %s", recent_ts)

    return {
        "total": total,
        "kinds": kinds,
        "recent_ts": recent_ts,
    }


def summarize_object_relations(
    conn: sqlite3.Connection, limit_relations: int = 20
) -> Dict[str, Any]:
    """Liest Basis-Statistiken aus object_relations."""
    total = _get_table_row_count(conn, "object_relations")

    relations: List[Dict[str, Any]] = []
    if total > 0:
        cur = conn.execute(
            """
            SELECT relation, COUNT(*) AS c
              FROM object_relations
             GROUP BY relation
             ORDER BY c DESC, relation ASC
             LIMIT ?
            """,
            (limit_relations,),
        )
        for r in cur.fetchall():
            relations.append({"relation": r["relation"], "count": int(r["c"])})

    min_conf, max_conf = _get_min_max(conn, "object_relations", "confidence")

    avg_conf: Optional[float] = None
    if total > 0:
        cur = conn.execute(
            """
            SELECT confidence
              FROM object_relations
             WHERE confidence IS NOT NULL
            """
        )
        vals = [float(r["confidence"]) for r in cur.fetchall()]
        if vals:
            try:
                avg_conf = float(statistics.fmean(vals))
            except statistics.StatisticsError:
                avg_conf = None

    missing_a = 0
    missing_b = 0
    if total > 0 and _table_exists(conn, "object_nodes"):
        # FK-Check a_id
        cur = conn.execute(
            """
            SELECT COUNT(*) AS c_missing
              FROM object_relations r
             WHERE r.a_id IS NOT NULL
               AND NOT EXISTS (
                     SELECT 1
                       FROM object_nodes n
                      WHERE n.id = r.a_id
               )
            """
        )
        row = cur.fetchone()
        missing_a = int(row["c_missing"]) if row and row["c_missing"] is not None else 0

        # FK-Check b_id
        cur = conn.execute(
            """
            SELECT COUNT(*) AS c_missing
              FROM object_relations r
             WHERE r.b_id IS NOT NULL
               AND NOT EXISTS (
                     SELECT 1
                       FROM object_nodes n
                      WHERE n.id = r.b_id
               )
            """
        )
        row = cur.fetchone()
        missing_b = int(row["c_missing"]) if row and row["c_missing"] is not None else 0

    logging.info("ObjectRelations: total=%s", total)
    if relations:
        logging.info(
            "  Relations: %s",
            ", ".join(f"{r['relation']}={r['count']}" for r in relations),
        )
    if min_conf is not None or max_conf is not None:
        logging.info(
            "  Confidence: min=%s max=%s avg=%s",
            min_conf,
            max_conf,
            f"{avg_conf:.3f}" if avg_conf is not None else None,
        )
    logging.info("  Missing references: a_id=%s, b_id=%s", missing_a, missing_b)

    return {
        "total": total,
        "relations": relations,
        "confidence": {
            "min": min_conf,
            "max": max_conf,
            "avg": avg_conf,
        },
        "integrity": {
            "missing_a": missing_a,
            "missing_b": missing_b,
        },
    }


def summarize_scenegraphs(
    conn: sqlite3.Connection, namespace_prefix: str
) -> Dict[str, Any]:
    """Liest Basis-Statistiken aus scenegraphs für ObjectGraph-Namespaces."""
    if not _table_exists(conn, "scenegraphs"):
        logging.info("SceneGraphs: Tabelle 'scenegraphs' existiert nicht.")
        return {
            "total": 0,
            "namespace_prefix": namespace_prefix,
            "stats": {
                "objects": {"min": None, "max": None, "avg": None},
                "object_edges": {"min": None, "max": None, "avg": None},
                "graphs_used": {"min": None, "max": None, "avg": None},
            },
        }

    cur = conn.execute(
        """
        SELECT COUNT(*) AS c
          FROM scenegraphs
         WHERE namespace LIKE ?
        """,
        (f"{namespace_prefix}%",),
    )
    row = cur.fetchone()
    total = int(row["c"]) if row and row["c"] is not None else 0

    stats = {
        "objects": {"min": None, "max": None, "avg": None},
        "object_edges": {"min": None, "max": None, "avg": None},
        "graphs_used": {"min": None, "max": None, "avg": None},
    }

    if total > 0:
        cur = conn.execute(
            """
            SELECT
                MIN(json_extract(graph_json, '$.stats.objects'))        AS min_objects,
                MAX(json_extract(graph_json, '$.stats.objects'))        AS max_objects,
                AVG(json_extract(graph_json, '$.stats.objects'))        AS avg_objects,
                MIN(json_extract(graph_json, '$.stats.object_edges'))   AS min_edges,
                MAX(json_extract(graph_json, '$.stats.object_edges'))   AS max_edges,
                AVG(json_extract(graph_json, '$.stats.object_edges'))   AS avg_edges,
                MIN(json_extract(graph_json, '$.stats.graphs_used'))    AS min_graphs_used,
                MAX(json_extract(graph_json, '$.stats.graphs_used'))    AS max_graphs_used,
                AVG(json_extract(graph_json, '$.stats.graphs_used'))    AS avg_graphs_used
              FROM scenegraphs
             WHERE namespace LIKE ?
            """,
            (f"{namespace_prefix}%",),
        )
        row = cur.fetchone()
        if row:
            stats["objects"] = {
                "min": row["min_objects"],
                "max": row["max_objects"],
                "avg": row["avg_objects"],
            }
            stats["object_edges"] = {
                "min": row["min_edges"],
                "max": row["max_edges"],
                "avg": row["avg_edges"],
            }
            stats["graphs_used"] = {
                "min": row["min_graphs_used"],
                "max": row["max_graphs_used"],
                "avg": row["avg_graphs_used"],
            }

    logging.info(
        "SceneGraphs (object-namespace): total=%s (prefix=%s)",
        total,
        namespace_prefix,
    )
    if total > 0:
        logging.info(
            "  Objects: min=%s max=%s avg=%.2f",
            stats["objects"]["min"],
            stats["objects"]["max"],
            stats["objects"]["avg"] if stats["objects"]["avg"] is not None else 0.0,
        )
        logging.info(
            "  ObjectEdges: min=%s max=%s avg=%.2f",
            stats["object_edges"]["min"],
            stats["object_edges"]["max"],
            stats["object_edges"]["avg"]
            if stats["object_edges"]["avg"] is not None
            else 0.0,
        )
        logging.info(
            "  GraphsUsed: min=%s max=%s avg=%.2f",
            stats["graphs_used"]["min"],
            stats["graphs_used"]["max"],
            stats["graphs_used"]["avg"]
            if stats["graphs_used"]["avg"] is not None
            else 0.0,
        )

    return {
        "total": total,
        "namespace_prefix": namespace_prefix,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Erweiterte Metriken v1.5
# ---------------------------------------------------------------------------

KNOWN_RELATIONS = {
    "describes",
    "origin",
    "meta_to_chain",
    "chain_to_origin",
    "cooccurs",
    "part_of",
    "follows",
    "near",
    "same_object",
    "same_scene",
    "parent_of",
    "child_of",
    "belongs_to",
    "touches",
    "occludes",
    "inside",
    "intersects",
    "overlaps",
}


def compute_extended_metrics(
    conn: sqlite3.Connection,
    namespace_prefix: str,
    nodes_summary: Dict[str, Any],
    rels_summary: Dict[str, Any],
    scene_summary: Dict[str, Any],
    meta_sample_limit: int = 200,
) -> Dict[str, Any]:
    """Berechnet semantic/quality/consistency/time/health auf Basis der DB."""
    total_nodes = int(nodes_summary.get("total", 0) or 0)
    total_rel = int(rels_summary.get("total", 0) or 0)
    total_scene = int(scene_summary.get("total", 0) or 0)

    # -------------------------
    # semantic
    # -------------------------
    kinds_histogram: Dict[str, int] = {}
    for entry in nodes_summary.get("kinds", []) or []:
        kind = entry.get("kind")
        count = int(entry.get("count", 0) or 0)
        if kind is not None:
            kinds_histogram[str(kind)] = count

    relations_histogram: Dict[str, int] = {}
    if total_rel > 0 and _table_exists(conn, "object_relations"):
        cur = conn.execute(
            """
            SELECT relation, COUNT(*) AS c
              FROM object_relations
             GROUP BY relation
             ORDER BY c DESC, relation ASC
            """
        )
        for r in cur.fetchall():
            rel_name = r["relation"]
            relations_histogram[str(rel_name)] = int(r["c"])

    exotic_relations = sorted(
        [name for name in relations_histogram.keys() if name not in KNOWN_RELATIONS]
    )

    semantic = {
        "kinds_histogram": kinds_histogram,
        "relations_histogram": relations_histogram,
        "exotic_relations": exotic_relations,
    }

    # -------------------------
    # quality
    # -------------------------
    relations_per_node = {"min": None, "avg": None, "max": None}
    dangling_nodes = {"count": 0, "ratio": 0.0}
    top_connected_nodes: List[Dict[str, Any]] = []

    degrees: Dict[int, int] = {}
    if total_rel > 0 and _table_exists(conn, "object_relations"):
        cur = conn.execute("SELECT a_id, b_id FROM object_relations")
        for r in cur.fetchall():
            a_id = r["a_id"]
            b_id = r["b_id"]
            if a_id is not None:
                degrees[a_id] = degrees.get(a_id, 0) + 1
            if b_id is not None:
                degrees[b_id] = degrees.get(b_id, 0) + 1

    min_deg: Optional[int] = None
    max_deg: Optional[int] = None
    sum_deg: int = 0
    dangling_count: int = 0
    node_count_for_deg: int = 0

    if total_nodes > 0 and _table_exists(conn, "object_nodes"):
        cur = conn.execute("SELECT id, kind, label FROM object_nodes")
        for r in cur.fetchall():
            node_id = int(r["id"])
            kind = r["kind"]
            label = r["label"]
            deg = int(degrees.get(node_id, 0))
            sum_deg += deg
            node_count_for_deg += 1

            if min_deg is None or deg < min_deg:
                min_deg = deg
            if max_deg is None or deg > max_deg:
                max_deg = deg
            if deg == 0:
                dangling_count += 1

            top_connected_nodes.append(
                {
                    "id": node_id,
                    "kind": kind,
                    "label": label,
                    "degree": deg,
                }
            )

    avg_deg: Optional[float] = None
    dangling_ratio: float = 0.0
    if node_count_for_deg > 0:
        avg_deg = float(sum_deg) / float(node_count_for_deg)
        dangling_ratio = float(dangling_count) / float(node_count_for_deg)

    # sortiert nach Degree absteigend, nur Degree > 0
    top_connected_nodes = [
        n for n in sorted(top_connected_nodes, key=lambda x: x["degree"], reverse=True)
        if n["degree"] > 0
    ][:10]

    relations_per_node = {
        "min": min_deg,
        "avg": avg_deg,
        "max": max_deg,
    }
    dangling_nodes = {
        "count": dangling_count,
        "ratio": dangling_ratio,
    }

    quality = {
        "relations_per_node": relations_per_node,
        "dangling_nodes": dangling_nodes,
        "top_connected_nodes": top_connected_nodes,
    }

    # -------------------------
    # consistency
    # -------------------------
    integrity = rels_summary.get("integrity") or {}
    missing_a = int(integrity.get("missing_a", 0) or 0)
    missing_b = int(integrity.get("missing_b", 0) or 0)

    meta_sample_size = 0
    meta_parse_errors = 0
    missing_keys_counter: Counter = Counter()

    if total_nodes > 0 and _table_exists(conn, "object_nodes"):
        # Wir begnügen uns mit einer Stichprobe von max. meta_sample_limit Zeilen
        cur = conn.execute(
            """
            SELECT id, meta_json
              FROM object_nodes
             WHERE meta_json IS NOT NULL
             ORDER BY id DESC
             LIMIT ?
            """,
            (meta_sample_limit,),
        )
        for r in cur.fetchall():
            meta_sample_size += 1
            raw = r["meta_json"]
            if raw is None:
                continue
            try:
                meta = json.loads(raw)
            except Exception:
                meta_parse_errors += 1
                continue
            if not isinstance(meta, dict):
                continue
            stats = meta.get("stats")
            if not isinstance(stats, dict):
                missing_keys_counter["stats"] += 1
                continue
            for key in ("seen_count", "first_ts", "last_ts"):
                if key not in stats:
                    missing_keys_counter[f"stats.{key}"] += 1

    consistency = {
        "integrity": {
            "missing_a": missing_a,
            "missing_b": missing_b,
        },
        "meta_json": {
            "sample_size": meta_sample_size,
            "parse_errors": meta_parse_errors,
            "missing_keys": dict(missing_keys_counter),
        },
        "scenegraphs": {
            "total": total_scene,
            "namespace_prefix": scene_summary.get("namespace_prefix", namespace_prefix),
        },
    }

    # -------------------------
    # time
    # -------------------------
    first_ts: Optional[float] = None
    last_ts: Optional[float] = nodes_summary.get("recent_ts")
    if total_nodes > 0 and _table_exists(conn, "object_nodes"):
        cur = conn.execute(
            """
            SELECT created_ts
              FROM object_nodes
             WHERE created_ts IS NOT NULL
             ORDER BY created_ts ASC
             LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row["created_ts"] is not None:
            first_ts = float(row["created_ts"])

    span_seconds: Optional[float] = None
    span_days: Optional[float] = None
    if first_ts is not None and last_ts is not None:
        try:
            span_seconds = max(0.0, float(last_ts) - float(first_ts))
            span_days = span_seconds / 86400.0
        except Exception:
            span_seconds = None
            span_days = None

    recent_activity_count: Optional[int] = None
    if last_ts is not None and total_nodes > 0 and _table_exists(conn, "object_nodes"):
        threshold = float(last_ts) - 86400.0
        try:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS c
                  FROM object_nodes
                 WHERE created_ts IS NOT NULL
                   AND created_ts >= ?
                """,
                (threshold,),
            )
            row = cur.fetchone()
            if row and row["c"] is not None:
                recent_activity_count = int(row["c"])
        except Exception:
            recent_activity_count = None

    time_section = {
        "span": {
            "first_ts": first_ts,
            "last_ts": last_ts,
            "span_seconds": span_seconds,
            "span_days": span_days,
        },
        "recent_activity": {
            "nodes_last_24h": recent_activity_count,
        },
    }

    # -------------------------
    # health (Aggregat + Exit-Code)
    # -------------------------
    warnings: List[str] = []
    errors: List[str] = []

    if total_nodes == 0:
        warnings.append("ObjectGraph enthält keine object_nodes-Zeilen.")
    if total_rel == 0:
        warnings.append("ObjectGraph enthält keine object_relations-Zeilen.")

    if missing_a > 0 or missing_b > 0:
        errors.append(
            f"FK-Integrität verletzt: missing_a={missing_a}, missing_b={missing_b}."
        )

    d_ratio = dangling_nodes["ratio"]
    if d_ratio is not None:
        try:
            dr = float(d_ratio)
        except Exception:
            dr = 0.0
        if dr > 0.20:
            errors.append(
                f"Dangling-Nodes-Anteil {dr:.3f} > 0.20 (kritisch, viele isolierte Knoten)."
            )
        elif dr > 0.05:
            warnings.append(
                f"Dangling-Nodes-Anteil {dr:.3f} > 0.05 (auffällig viele isolierte Knoten)."
            )

    parse_errors = meta_parse_errors
    if parse_errors > 10:
        errors.append(
            f"meta_json-Parse-Fehler: {parse_errors} > 10 (viele defekte Meta-Einträge)."
        )
    elif parse_errors > 0:
        warnings.append(
            f"meta_json-Parse-Fehler: {parse_errors} (einige Meta-Einträge nicht lesbar)."
        )

    if total_scene == 0:
        warnings.append(
            f"Keine SceneGraphs für Namespace '{namespace_prefix}' gefunden."
        )

    if errors:
        overall_status = "error"
        exit_code = 2
    elif warnings:
        overall_status = "warning"
        exit_code = 1
    else:
        overall_status = "ok"
        exit_code = 0

    health = {
        "overall_status": overall_status,
        "warnings": warnings,
        "errors": errors,
        "exit_code": exit_code,
    }

    logging.info(
        "Health: status=%s, warnings=%d, errors=%d",
        overall_status,
        len(warnings),
        len(errors),
    )

    return {
        "semantic": semantic,
        "quality": quality,
        "consistency": consistency,
        "time": time_section,
        "health": health,
    }


# ---------------------------------------------------------------------------
# CLI / Main
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ObjectGraph Selfcheck (Health, Stats, Semantic Metriken)"
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/oroma.db",
        help="Pfad zur SQLite-DB (Default: data/oroma.db)",
    )
    parser.add_argument(
        "--namespace-prefix",
        type=str,
        default="object:auto:",
        help="Namespace-Präfix für ObjectGraph-SceneGraphs (Default: object:auto:)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit für Top-Kinds/Relations (nur für Logging-Übersicht, Default: 20)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Report als JSON auf STDOUT ausgeben",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose-Logging aktivieren",
    )
    return parser.parse_args(argv)


def run_selfcheck(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Führt den Selfcheck aus und liefert (Report, Exit-Code)."""
    setup_logging(verbose=args.verbose)

    logging.info(
        "Starte ObjectGraph-Selfcheck (namespace_prefix=%s)", args.namespace_prefix
    )

    conn = _open_db(args.db)
    try:
        nodes_stats = summarize_object_nodes(conn, limit_kinds=args.limit)
        rel_stats = summarize_object_relations(conn, limit_relations=args.limit)
        scene_stats = summarize_scenegraphs(conn, namespace_prefix=args.namespace_prefix)

        extended = compute_extended_metrics(
            conn,
            namespace_prefix=args.namespace_prefix,
            nodes_summary=nodes_stats,
            rels_summary=rel_stats,
            scene_summary=scene_stats,
        )

        report: Dict[str, Any] = {
            "version": SELF_VERSION,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "namespace_prefix": args.namespace_prefix,
            "object_nodes": nodes_stats,
            "object_relations": rel_stats,
            "scenegraphs": scene_stats,
        }
        report.update(extended)

        exit_code = int(extended["health"]["exit_code"])
        return report, exit_code
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_selfcheck(args)

    if args.json:
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        print()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
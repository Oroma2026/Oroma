#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/scenegraph_selfcheck.py
# Projekt: ORÓMA
# Modul:   SceneGraph Self-Check & Statistik (MetaSnaps + SceneGraphs)
# Version: v3.8-r2
# Stand:   2025-12-02
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Dieses Werkzeug prüft und fasst zusammen, was der SceneGraph-Builder
#   (core/scenegraph_builder.py) bislang erzeugt hat:
#
#   1) MetaSnaps-Statistik:
#        - wie viele meta_snaps mit Label-Prefix "scenegraph:<origin_clean>:"
#        - Aufteilung nach Label (z. B. hoch / niedrig)
#        - min/avg/max(score) pro Label
#
#   2) SceneGraph-Statistik:
#        - wie viele SceneGraphs es für einen Namespace gibt
#        - Details zu den letzten N Graphen (id, ts, source, quality)
#        - optional: Node- und Edge-Anzahl aus graph_json (falls JSON-Struktur
#          mit "nodes" / "edges" vorhanden ist)
#
#   Fokus-Szenario:
#     origin       = "vision/token"
#     origin_clean = "vision_token"
#     Labels       = "scenegraph:vision_token:hoch" / "…:niedrig"
#     Namespace    = "scene:auto_meta:vision_token"
#
#   Damit kannst du in einem Schritt prüfen:
#     - Hat der SceneGraph-Builder MetaSnaps erzeugt?
#     - Wie verteilen sich "hoch" vs. "niedrig"?
#     - Gibt es fertige Graphen im Namespace, und wie groß sind sie?
#
# INTEGRATION
# ───────────
#   • Nutzt core.sql_manager.get_conn() für DB-Zugriff.
#   • Greift ausschließlich lesend auf die Tabellen zu:
#       - meta_snaps(label, score, sources, …)
#       - scenegraphs(id, ts, namespace, source, quality, graph_json, …)
#
#   • Keine Schreiboperationen, kein Einfluss auf laufende Dienste.
#
# ROBUSTHEIT
# ──────────
#   • Wenn meta_snaps oder scenegraphs in der aktuellen DB noch nicht existieren,
#     wird NICHT abgebrochen, sondern:
#       - total = 0
#       - by_label / latest = []
#     zurückgegeben, plus eine INFO-Logzeile.
#
# UMWELTVARIABLEN
# ───────────────
#   OROMA_SCENEGRAPH_ORIGIN
#       Default-Origin (Standard: "vision/token")
#
#   OROMA_SCENEGRAPH_NAMESPACE
#       Standard-SceneGraph-Namespace (Standard, wenn unset):
#         "scene:auto_meta:<origin_clean>"
#       wobei origin_clean = origin.replace("/", "_").replace(":", "_")
#
#   OROMA_SCENEGRAPH_SELFCHK_LIMIT
#       Default-Limit für die Anzahl der letzten SceneGraphs (Standard: 5)
#
# NUTZUNG – BEISPIELE
# ───────────────────
#   1) Standard-Self-Check (vision/token, auto-Namespace, 5 letzte Graphen):
#
#       cd /opt/ai/oroma
#       export PYTHONPATH=/opt/ai/oroma
#       python3 tools/scenegraph_selfcheck.py
#
#   2) Anderer Origin (z. B. vision/scene) + Namespace explizit:
#
#       python3 tools/scenegraph_selfcheck.py \
#           --origin vision/scene \
#           --namespace scene:auto_meta:vision_scene \
#           --limit 10 \
#           --verbose
#
#   3) JSON-Ausgabe (z. B. für weitere Auswertung mit jq):
#
#       python3 tools/scenegraph_selfcheck.py --json-only | jq .
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

# Sicherstellen, dass /opt/ai/oroma im Pfad ist
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

from core import sql_manager

logger = logging.getLogger("oroma.scenegraph_selfcheck")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("[scenegraph_selfcheck] %(levelname)s: %(message)s")
    )
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _origin_to_clean(origin: str) -> str:
    """
    Normalisiert einen Origin-String so, wie es der SceneGraph-Builder tut:
      "vision/token" → "vision_token"
      "game:snake"   → "game_snake"
    """
    if not origin:
        origin = "unknown"
    return origin.replace("/", "_").replace(":", "_")


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v.strip())
    except Exception:
        return default


def _table_exists(conn, table_name: str) -> bool:
    """
    Prüft, ob eine Tabelle in der aktuellen SQLite-DB existiert.

    Rückgabe:
      True  → Tabelle vorhanden
      False → Tabelle existiert nicht oder es gab einen Fehler
    """
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception as exc:  # pragma: no cover
        logger.warning("Fehler bei _table_exists(%s): %s", table_name, exc)
        return False


# =============================================================================
# MetaSnaps-Auswertung
# =============================================================================

def summarize_meta_snaps(
    conn,
    origin: str,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Liefert eine Zusammenfassung aller MetaSnaps zu einem Origin.

    Es werden nur MetaSnaps berücksichtigt, deren label mit:
      "scenegraph:<origin_clean>:"
    beginnt, wobei origin_clean aus origin abgeleitet wird.

    Rückgabe-Struktur:
      {
        "prefix": "scenegraph:vision_token:",
        "total": 123,
        "by_label": [
          {
            "label": "scenegraph:vision_token:hoch",
            "count": 10,
            "min_score": 0.051,
            "max_score": 0.122,
            "avg_score": 0.073
          },
          ...
        ]
      }

    Wenn die Tabelle meta_snaps nicht existiert, wird:
      total = 0, by_label = [] zurückgegeben.
    """
    origin_clean = _origin_to_clean(origin)
    prefix = f"scenegraph:{origin_clean}:"

    if not _table_exists(conn, "meta_snaps"):
        if verbose:
            logger.info(
                "Tabelle meta_snaps existiert nicht – keine MetaSnaps-Auswertung möglich."
            )
        return {
            "prefix": prefix,
            "total": 0,
            "by_label": [],
        }

    cur = conn.cursor()

    # Gesamtanzahl der MetaSnaps für diesen Prefix
    row_total = cur.execute(
        "SELECT COUNT(*) AS cnt FROM meta_snaps WHERE label LIKE ?",
        (prefix + "%",),
    ).fetchone()
    total = int(row_total["cnt"]) if row_total and row_total["cnt"] is not None else 0

    if total == 0:
        if verbose:
            logger.info(
                "Keine MetaSnaps für Prefix %s gefunden (origin=%s).", prefix, origin
            )
        return {
            "prefix": prefix,
            "total": 0,
            "by_label": [],
        }

    if verbose:
        logger.info(
            "MetaSnaps-Präfix %s gefunden, Gesamtanzahl: %d", prefix, total
        )

    # Aufteilung nach Label (z. B. hoch/niedrig)
    rows = cur.execute(
        """
        SELECT
            label,
            COUNT(*) AS cnt,
            MIN(score) AS min_score,
            MAX(score) AS max_score,
            AVG(score) AS avg_score
        FROM meta_snaps
        WHERE label LIKE ?
        GROUP BY label
        ORDER BY label
        """,
        (prefix + "%",),
    ).fetchall()

    by_label: List[Dict[str, Any]] = []
    for r in rows or []:
        try:
            label = str(r["label"])
            cnt = int(r["cnt"] or 0)
            min_score = float(r["min_score"]) if r["min_score"] is not None else None
            max_score = float(r["max_score"]) if r["max_score"] is not None else None
            avg_score = float(r["avg_score"]) if r["avg_score"] is not None else None
        except Exception as exc:  # pragma: no cover
            logger.warning("Fehler beim Lesen einer meta_snaps-Zeile: %s", exc)
            continue

        by_label.append(
            {
                "label": label,
                "count": cnt,
                "min_score": min_score,
                "max_score": max_score,
                "avg_score": avg_score,
            }
        )

        if verbose:
            logger.info(
                "MetaSnaps %s: count=%d, min=%.5f, max=%.5f, avg=%.5f",
                label,
                cnt,
                min_score if min_score is not None else 0.0,
                max_score if max_score is not None else 0.0,
                avg_score if avg_score is not None else 0.0,
            )

    return {
        "prefix": prefix,
        "total": total,
        "by_label": by_label,
    }


# =============================================================================
# SceneGraph-Auswertung
# =============================================================================

def _parse_graph_stats(graph_json_text: Optional[str]) -> Dict[str, Optional[int]]:
    """
    Versucht, aus graph_json die Anzahl der Nodes/Edges zu bestimmen.

    Erwartet JSON-Strukturen der Form:
      { "nodes": [...], "edges": [...] }

    Wenn das nicht passt, werden node_count / edge_count = None gesetzt.
    """
    if not graph_json_text:
        return {"node_count": None, "edge_count": None}

    try:
        data = json.loads(graph_json_text)
    except Exception:
        return {"node_count": None, "edge_count": None}

    if isinstance(data, dict):
        nodes = data.get("nodes")
        edges = data.get("edges")
        node_count = len(nodes) if isinstance(nodes, list) else None
        edge_count = len(edges) if isinstance(edges, list) else None
        return {"node_count": node_count, "edge_count": edge_count}

    return {"node_count": None, "edge_count": None}


def summarize_scenegraphs(
    conn,
    namespace: str,
    limit: int = 5,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Fasst SceneGraphs für einen Namespace zusammen.

    Es werden:
      - die Gesamtanzahl aller SceneGraphs mit diesem Namespace
      - die letzten `limit` SceneGraphs (id DESC / ts DESC)
        inkl. Node-/Edge-Anzahl (falls aus graph_json bestimmbar)
    zurückgegeben.

    Rückgabe-Struktur:
      {
        "namespace": "...",
        "total": 12,
        "latest": [
          {
            "id": 7,
            "ts": 1764601200,
            "ts_iso": "2025-12-01T23:00:00Z",
            "source": "builder:vision_tokens",
            "quality": 0.73,
            "node_count": 120,
            "edge_count": 260
          },
          ...
        ]
      }

    Wenn die Tabelle scenegraphs nicht existiert, wird:
      total = 0, latest = [] zurückgegeben.
    """
    if not _table_exists(conn, "scenegraphs"):
        if verbose:
            logger.info(
                "Tabelle scenegraphs existiert nicht – keine SceneGraph-Auswertung möglich."
            )
        return {
            "namespace": namespace,
            "total": 0,
            "latest": [],
        }

    cur = conn.cursor()

    # Gesamtanzahl
    row_total = cur.execute(
        "SELECT COUNT(*) AS cnt FROM scenegraphs WHERE namespace = ?",
        (namespace,),
    ).fetchone()
    total = int(row_total["cnt"]) if row_total and row_total["cnt"] is not None else 0

    if total == 0:
        if verbose:
            logger.info(
                "Keine SceneGraphs für Namespace %s gefunden.", namespace
            )
        return {
            "namespace": namespace,
            "total": 0,
            "latest": [],
        }

    if verbose:
        logger.info(
            "SceneGraphs für Namespace %s gefunden, Gesamtanzahl: %d",
            namespace,
            total,
        )

    rows = cur.execute(
        """
        SELECT id, ts, namespace, source, quality, graph_json
        FROM scenegraphs
        WHERE namespace = ?
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """,
        (namespace, int(limit)),
    ).fetchall()

    latest: List[Dict[str, Any]] = []
    for r in rows or []:
        try:
            gid = int(r["id"])
            ts_val = int(r["ts"])
            source = str(r["source"]) if r["source"] is not None else ""
            quality = float(r["quality"]) if r["quality"] is not None else None
            graph_json_text = r["graph_json"]
        except Exception as exc:  # pragma: no cover
            logger.warning("Fehler beim Lesen einer scenegraphs-Zeile: %s", exc)
            continue

        graph_stats = _parse_graph_stats(graph_json_text)
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_val))

        entry = {
            "id": gid,
            "ts": ts_val,
            "ts_iso": ts_iso,
            "source": source,
            "quality": quality,
            "node_count": graph_stats["node_count"],
            "edge_count": graph_stats["edge_count"],
        }
        latest.append(entry)

        if verbose:
            logger.info(
                "SceneGraph id=%d, ts=%s, source=%s, quality=%s, nodes=%s, edges=%s",
                gid,
                ts_iso,
                source,
                f"{quality:.5f}" if quality is not None else "None",
                str(graph_stats["node_count"]),
                str(graph_stats["edge_count"]),
            )

    return {
        "namespace": namespace,
        "total": total,
        "latest": latest,
    }


# =============================================================================
# Hauptfunktion / CLI
# =============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="ORÓMA SceneGraph Self-Check (MetaSnaps + SceneGraphs)"
    )

    default_origin = _env_str("OROMA_SCENEGRAPH_ORIGIN", "vision/token")
    default_origin_clean = _origin_to_clean(default_origin)
    default_namespace = _env_str(
        "OROMA_SCENEGRAPH_NAMESPACE",
        f"scene:auto_meta:{default_origin_clean}",
    )

    ap.add_argument(
        "--origin",
        type=str,
        default=default_origin,
        help=f"SnapChain-Origin für MetaSnaps (Default: {default_origin!r})",
    )
    ap.add_argument(
        "--namespace",
        type=str,
        default=default_namespace,
        help=f"SceneGraph-Namespace (Default: {default_namespace!r})",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=_env_int("OROMA_SCENEGRAPH_SELFCHK_LIMIT", 5),
        help="Anzahl der letzten SceneGraphs, die detailliert angezeigt werden (Default: 5)",
    )
    ap.add_argument(
        "--json-only",
        action="store_true",
        help="Nur JSON-Ergebnis auf stdout ausgeben (keine zusätzlichen Log-Zeilen)",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Ausführliche Log-Ausgaben aktivieren",
    )

    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.json_only:
        # Logs auf WARN drosseln, damit stdout weitgehend nur JSON enthält
        logger.setLevel(logging.WARNING)

    origin = args.origin
    namespace = args.namespace
    limit = max(1, int(args.limit))

    conn = sql_manager.get_conn()
    try:
        meta_summary = summarize_meta_snaps(conn, origin=origin, verbose=args.verbose)
        scene_summary = summarize_scenegraphs(
            conn,
            namespace=namespace,
            limit=limit,
            verbose=args.verbose,
        )
    finally:
        conn.close()

    result = {
        "ok": True,
        "origin": origin,
        "origin_clean": _origin_to_clean(origin),
        "namespace": namespace,
        "meta_snaps": meta_summary,
        "scenegraphs": scene_summary,
        "ts": int(time.time()),
    }

    # Menschliche Kurz-Zusammenfassung (wenn nicht json-only)
    if not args.json_only:
        logger.info("===== SceneGraph Self-Check =====")
        logger.info(
            "Origin: %s (origin_clean=%s)",
            origin,
            result["origin_clean"],
        )
        logger.info(
            "MetaSnaps: prefix=%s total=%d",
            meta_summary.get("prefix"),
            meta_summary.get("total", 0),
        )
        for lbl in meta_summary.get("by_label", []):
            logger.info(
                "  Label=%s count=%d avg_score=%.5f",
                lbl["label"],
                lbl["count"],
                lbl["avg_score"] if lbl["avg_score"] is not None else 0.0,
            )
        logger.info(
            "SceneGraphs: namespace=%s total=%d",
            namespace,
            scene_summary.get("total", 0),
        )
        for g in scene_summary.get("latest", []):
            logger.info(
                "  Graph id=%d ts=%s source=%s quality=%s nodes=%s edges=%s",
                g["id"],
                g["ts_iso"],
                g["source"],
                f"{g['quality']:.5f}" if g["quality"] is not None else "None",
                str(g["node_count"]),
                str(g["edge_count"]),
            )

    # JSON-Ergebnis auf stdout
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
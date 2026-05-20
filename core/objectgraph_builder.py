#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/objectgraph_builder.py
# Projekt: ORÓMA
# Modul:   ObjectGraph Builder (SceneGraphs → ObjectGraphs, 2.5D → 3D/N-Space)
# Version: v3.8-r1
# Stand:   2025-12-08
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Dieser Builder ergänzt den bestehenden SceneGraph-Pfad um eine zweite Ebene:
#
#       SceneGraphs (Moment-Sichten, „2.5D“)  →  ObjectGraphs (persistente Objekte)
#
#   • SceneGraphs werden heute bereits automatisch aus Vision-Tokens (SnapChains)
#     in der Dream-Phase erzeugt und in der Tabelle `scenegraphs` gespeichert
#     (namespace z. B. "scene:auto_meta:vision_token").
#   • Der ObjectGraph-Builder liest eine Menge dieser SceneGraphs, aggregiert
#     wiederkehrende Knoten zu „Objekten“ und leitet einfache Relationen ab
#     (co-occur, zeitliche Nachbarschaft, rel. Topologie).
#
#   Ziel:
#   -----
#   • Ein „3D/Objekt“-Layer aufzubauen, ohne das Schema zu verändern:
#       - ObjectGraphs werden wie SceneGraphs in der Tabelle `scenegraphs`
#         gespeichert, aber mit eigenem Namespace:
#             namespace = "object:auto:vision" (Default, konfigurierbar)
#       - Knoten haben kind="object" und referenzieren die ursprünglichen
#         SceneGraph-Knoten nur noch logisch (via ID/Meta).
#   • Bestehende Module (scenegraph_store, DreamWorker, UI) bleiben kompatibel:
#       - ObjectGraphs sind normale SceneGraphs, nur mit anderem Namespace.
#
#   Einbindung:
#   -----------
#   • DreamWorker kann diesen Builder optional nach dem SceneGraph-Build
#     aufrufen, z. B.:
#
#       from core import objectgraph_builder as og
#       og.auto_objectgraph_from_scenegraphs(
#           source_namespace_prefix="scene:auto_meta:",
#           target_namespace="object:auto:vision",
#           max_graphs=32,
#       )
#
#   • Die UI (scenegraph_ui.py) kann ObjectGraphs einfach mit anzeigen, indem
#     sie nach dem Namespace "object:auto:%" filtert – die Struktur von
#     `graph_json` bleibt identisch zu SceneGraphs.
#
#   WICHTIG (v1-Implementierung):
#   -----------------------------
#   • Diese erste Version liefert einen robusten, erklärbaren Aggregationspfad,
#     aber noch kein vollständiges physikalisches 3D-Modell.
#   • Der Fokus liegt auf:
#       - Persistenten Objekt-Knoten (konstante IDs über viele SceneGraphs),
#       - verdichteten Relationen (z. B. wie oft zwei Objekte gemeinsam
#         auftreten),
#       - sauberer Integration in bestehende Strukturen.
#
#   Die Implementierung ist bewusst konservativ:
#   • Kein Schema-Umbau, keine harten 3D-Abhängigkeiten,
#   • Lauf auf wenigen, konfigurierbaren SceneGraphs (max_graphs),
#   • defensives Error-Handling (Fehler in einzelnen Graphen bremsen den
#     Gesamt-Builder nicht aus).
#
# ENVIRONMENT-VARIABLEN
# ---------------------
#   OROMA_OBJECTGRAPH_SRC_NS
#       Prefix des SceneGraph-Namespaces, aus denen Objekte aggregiert werden.
#       Default: "scene:auto_meta:"
#
#   OROMA_OBJECTGRAPH_TARGET_NS
#       Namespace, unter dem der ObjectGraph gespeichert wird.
#       Default: "object:auto:vision"
#
#   OROMA_OBJECTGRAPH_MAX_GRAPHS
#       Wie viele der neuesten SceneGraphs mit passendem Namespace gelesen
#       werden sollen.
#       Default: 32
#
#   OROMA_OBJECTGRAPH_MIN_QUALITY
#       Optionaler Quality-Filter für SceneGraphs. Ist kein Quality-Wert
#       gesetzt, wird der Graph trotzdem berücksichtigt.
#       Default: 0.0
#
# NUTZUNG (Beispiele)
# -------------------
#   # 1) Nur im RAM bauen (z. B. in einem Experiment):
#   from core import objectgraph_builder as og
#
#   g = og.build_objectgraph_from_scenegraphs(
#       source_namespace_prefix="scene:auto_meta:",
#       max_graphs=32,
#       min_quality=0.0,
#   )
#   print(len(g.nodes), len(g.edges))
#
#   # 2) Auto-Build + Persistenz (z. B. aus DreamWorker)
#   res = og.auto_objectgraph_from_scenegraphs(
#       source_namespace_prefix="scene:auto_meta:",
#       target_namespace="object:auto:vision",
#       max_graphs=32,
#       min_quality=0.0,
#       persist=True,
#   )
#   print(res["ok"], res["meta"].get("saved_id"), res["meta"].get("stats"))
#
#   # 3) CLI-Selftest:
#   #    python3 -m core.objectgraph_builder --max-graphs 16 --verbose
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Sicherstellen, dass /opt/ai/oroma im Pfad ist (CLI-freundlich)
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

from core import sql_manager
from core import scenegraph_store as sg


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger("oroma.objectgraph")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[objectgraph] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# =============================================================================
# Interne Hilfsstrukturen
# =============================================================================


@dataclass
class _AggNode:
    """Aggregierte Objekt-Sicht eines SceneGraph-Knotens.

    Dieses interne Aggregat bündelt Informationen aus vielen SceneGraphs:

    Felder
    ------
    obj_id      : stabile Objekt-ID (kanonisch; z. B. "meta:17", "chain:715")
    labels      : Menge gesehener Labels
    kinds       : Menge gesehener Node-Typen (meta, snapchain, origin, object, ...)
    count       : Anzahl beobachteter Vorkommen
    first_ts    : frühester Beobachtungszeitpunkt (Unix-Sekunden, aus SceneGraph-TS)
    last_ts     : letzter Beobachtungszeitpunkt
    score_sum   : Summe aller Scores (falls gesetzt)
    score_count : Anzahl der Score-Werte
    meta_samples: kleine Liste von Meta-Beispielen (für Explainability)
    """

    obj_id: str
    labels: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)
    count: int = 0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    score_sum: float = 0.0
    score_count: int = 0
    meta_samples: List[Dict[str, Any]] = field(default_factory=list)

    def add_observation(self, node: sg.SceneNode, graph_ts: Optional[int]) -> None:
        """Integriert eine Beobachtung aus einem SceneGraph-Node.

        Parameter
        ---------
        node : sg.SceneNode
            Knoten aus einem bestehenden SceneGraph.
        graph_ts : int|None
            Zeitstempel des zugrunde liegenden SceneGraphs (Unix-Sekunden).
        """
        lbl = node.label.strip() if node.label else ""
        if lbl and lbl not in self.labels:
            self.labels.append(lbl)

        k = (node.kind or "generic").strip()
        if k and k not in self.kinds:
            self.kinds.append(k)

        self.count += 1

        if graph_ts is not None:
            if self.first_ts is None or graph_ts < self.first_ts:
                self.first_ts = graph_ts
            if self.last_ts is None or graph_ts > self.last_ts:
                self.last_ts = graph_ts

        if node.score is not None:
            try:
                s = float(node.score)
            except Exception:
                s = None
            if s is not None:
                self.score_sum += s
                self.score_count += 1

        # Nur wenige Meta-Beispiele speichern, um die JSON-Größe zu begrenzen.
        if node.meta and len(self.meta_samples) < 4:
            # defensive Kopie, keine riesigen Strukturen duplizieren
            try:
                sample = dict(node.meta)
            except Exception:
                sample = {"_repr": repr(node.meta)}
            self.meta_samples.append(sample)

    def to_scene_node(self) -> sg.SceneNode:
        """Erzeugt einen SceneNode (kind="object") aus dem Aggregat.

        Die ursprünglichen Label/Kinds werden in meta abgelegt, damit die
        ObjectGraph-Ebene weiterhin nachvollziehbar bleibt.
        """
        if self.labels:
            label = self.labels[0]
        else:
            label = self.obj_id

        if self.score_count > 0:
            score = self.score_sum / max(self.score_count, 1)
        else:
            score = None

        meta: Dict[str, Any] = {
            "kinds": sorted(self.kinds),
            "label_variants": self.labels,
            "obs_count": self.count,
        }
        if self.first_ts is not None:
            meta["first_ts"] = self.first_ts
        if self.last_ts is not None:
            meta["last_ts"] = self.last_ts
        if self.meta_samples:
            meta["meta_samples"] = self.meta_samples

        # Hinweis: ts wird hier auf last_ts gesetzt, damit der Node zeitlich
        # ungefähr „aktuell“ einsortiert werden kann. first_ts ist in meta.
        ts = self.last_ts

        return sg.SceneNode(
            id=self.obj_id,
            label=label,
            kind="object",
            score=score,
            ts=ts,
            meta=meta,
        )


@dataclass
class _AggEdge:
    """Aggregierte Kante zwischen zwei Objekt-Knoten.

    Felder
    ------
    source      : Objekt-ID (Quelle)
    target      : Objekt-ID (Ziel)
    kind        : Kanten-Typ (z. B. "co_occur", "derived", "chain_to_origin")
    label       : kurzer Label-Text
    count       : Häufigkeit (wie oft beobachtet)
    weight_sum  : Summe der Edge-Gewichte (falls gesetzt, sonst 1.0 pro Beobachtung)
    """

    source: str
    target: str
    kind: str
    label: str
    count: int = 0
    weight_sum: float = 0.0

    def add_observation(self, weight: Optional[float]) -> None:
        w = 1.0
        if weight is not None:
            try:
                w = float(weight)
            except Exception:
                w = 1.0
        self.count += 1
        self.weight_sum += w

    def to_scene_edge(self) -> sg.SceneEdge:
        """Erzeugt eine SceneEdge aus dem Aggregat."""
        if self.count > 0:
            avg_weight: Optional[float] = self.weight_sum / self.count
        else:
            avg_weight = None

        meta: Dict[str, Any] = {"count": self.count}
        if avg_weight is not None:
            meta["avg_weight"] = avg_weight

        return sg.SceneEdge(
            source=self.source,
            target=self.target,
            label=self.label,
            kind=self.kind,
            weight=avg_weight,
            meta=meta,
        )


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _env_int(name: str, default: int) -> int:
    """Liest eine Ganzzahl aus einer Umgebungsvariable."""
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v.strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    """Liest einen Float aus einer Umgebungsvariable."""
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return float(v.strip())
    except Exception:
        return default


def _canonical_object_id(node: sg.SceneNode) -> Optional[str]:
    """Bestimmt eine kanonische Objekt-ID für einen SceneGraph-Knoten.

    Strategie (v1):
    --------------
    1. Wenn node.meta["object_id"] gesetzt ist (z. B. durch einen späteren
       Detector/Tracker), wird daraus eine Objekt-ID erzeugt:
           "obj:{object_id}"
    2. Andernfalls wird node.id verwendet (z. B. "meta:17", "chain:715").
       Das sorgt dafür, dass dieselbe SceneGraph-ID über viele Graphen hinweg
       als ein Objekt betrachtet wird.
    3. Leere IDs werden ignoriert (None → Node fliegt raus).

    Diese Logik ist bewusst konservativ. Sie kann in späteren Versionen z. B.
    um Clustering über Feature-Ähnlichkeit erweitert werden, ohne das Format
    zu brechen.
    """
    meta = node.meta or {}
    object_id = meta.get("object_id")
    if isinstance(object_id, str) and object_id.strip():
        return f"obj:{object_id.strip()}"

    nid = (node.id or "").strip()
    if not nid:
        return None
    return nid


def _load_scenegraphs(
    *,
    source_namespace_prefix: str,
    max_graphs: int,
    min_quality: Optional[float] = None,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """Lädt die letzten SceneGraphs mit passendem Namespace-Prefix.

    Rückgabe ist eine Liste von Dicts mit:
        {
          "id": int,
          "ts": int|None,
          "namespace": str|None,
          "source": str|None,
          "quality": float|None,
          "graph": dict  # SceneGraph-Struktur
        }
    """
    from sqlite3 import OperationalError

    ns_like = f"{source_namespace_prefix}%"
    out: List[Dict[str, Any]] = []

    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            if min_quality is None:
                cur.execute(
                    """SELECT id, ts, namespace, source, quality, graph_json
                           FROM scenegraphs
                          WHERE namespace LIKE ?
                       ORDER BY ts DESC, id DESC
                          LIMIT ?""",
                    (ns_like, max_graphs),
                )
            else:
                cur.execute(
                    """SELECT id, ts, namespace, source, quality, graph_json
                           FROM scenegraphs
                          WHERE namespace LIKE ?
                            AND (quality IS NULL OR quality >= ?)
                       ORDER BY ts DESC, id DESC
                          LIMIT ?""",
                    (ns_like, float(min_quality), max_graphs),
                )
            rows = cur.fetchall() or []
    except OperationalError as exc:
        logger.warning("[objectgraph] DB-Fehler beim Laden von SceneGraphs: %s", exc)
        return out
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "[objectgraph] Unerwarteter Fehler beim Laden von SceneGraphs: %s", exc
        )
        return out

    for r in rows:
        try:
            gid = int(r.get("id"))
            ts = r.get("ts")
            if ts is not None:
                try:
                    ts = int(ts)
                except Exception:
                    ts = None
            ns = r.get("namespace") or None
            src = r.get("source") or None
            q = r.get("quality")
            if q is not None:
                try:
                    q = float(q)
                except Exception:
                    q = None

            raw = r.get("graph_json") or "{}"
            try:
                g_dict = json.loads(raw)
            except Exception as exc:
                logger.warning(
                    "[objectgraph] Ungültiges graph_json in SceneGraph id=%s: %s",
                    gid,
                    exc,
                )
                continue

            out.append(
                {
                    "id": gid,
                    "ts": ts,
                    "namespace": ns,
                    "source": src,
                    "quality": q,
                    "graph": g_dict,
                }
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "[objectgraph] Fehler beim Verarbeiten eines SceneGraph-Datensatzes: %s",
                exc,
            )

    if verbose:
        logger.info(
            "Geladene SceneGraphs für prefix=%s: %d",
            source_namespace_prefix,
            len(out),
        )

    return out


# =============================================================================
# Haupt-API: Builder
# =============================================================================


def build_objectgraph_from_scenegraphs(
    *,
    source_namespace_prefix: str = "scene:auto_meta:",
    max_graphs: int = 32,
    min_quality: Optional[float] = 0.0,
    verbose: bool = False,
) -> sg.SceneGraph:
    """Erzeugt einen ObjectGraph aus aktuellen SceneGraphs.

    Pipeline (v1):
    --------------
    1. SceneGraphs laden (Namespace-Prefix, Quality-Filter, max_graphs).
    2. Für jeden SceneGraph:
       • sg.SceneGraph.from_dict() → Nodes/Edges.
       • Für jeden Node → kanonische Objekt-ID bestimmen → _AggNode aggregieren.
       • Für jede Edge → auf Aggregat-Ebene (_AggEdge) abbilden.
    3. Aus den Aggregaten werden Object-Nodes (kind="object") und -Edges
       (kind z. B. "derived"/"co_occur") erzeugt.
    4. SceneGraph.meta wird durch eine `stats`-Struktur ergänzt.

    WICHTIG:
    --------
    • Es wird nichts in die DB geschrieben – dafür ist
      auto_objectgraph_from_scenegraphs() zuständig.
    • Diese Funktion ist deterministisch für eine gegebene SceneGraph-Menge.
    """
    if max_graphs <= 0:
        raise ValueError("max_graphs muss > 0 sein")

    sg_rows = _load_scenegraphs(
        source_namespace_prefix=source_namespace_prefix,
        max_graphs=max_graphs,
        min_quality=min_quality,
        verbose=verbose,
    )

    if not sg_rows:
        logger.info(
            "[objectgraph] Keine passenden SceneGraphs gefunden (prefix=%s)",
            source_namespace_prefix,
        )
        return sg.SceneGraph(
            nodes=[],
            edges=[],
            meta={
                "error": "no_scenegraphs",
                "source_namespace_prefix": source_namespace_prefix,
                "stats": {
                    "graphs_used": 0,
                    "nodes_seen": 0,
                    "edges_seen": 0,
                },
            },
        )

    agg_nodes: Dict[str, _AggNode] = {}
    agg_edges: Dict[Tuple[str, str, str, str], _AggEdge] = {}

    graphs_used = 0
    nodes_seen = 0
    edges_seen = 0

    for row in sg_rows:
        graph_ts = row.get("ts")
        g_dict = row.get("graph") or {}
        try:
            g = sg.SceneGraph.from_dict(g_dict)
        except Exception as exc:
            logger.warning(
                "[objectgraph] Ungültiger SceneGraph (id=%s): %s", row.get("id"), exc
            )
            continue

        graphs_used += 1
        nodes_seen += len(g.nodes)
        edges_seen += len(g.edges)

        # 1) Knoten aggregieren
        for node in g.nodes:
            obj_id = _canonical_object_id(node)
            if not obj_id:
                continue

            agn = agg_nodes.get(obj_id)
            if agn is None:
                agn = _AggNode(obj_id=obj_id)
                agg_nodes[obj_id] = agn
            agn.add_observation(node, graph_ts)

        # 2) Kanten aggregieren (nach projizierten Objekt-IDs)
        for edge in g.edges:
            # Knoten können weggefallen sein, daher erneut projizieren.
            dummy_source_node = sg.SceneNode(
                id=edge.source,
                label="",
                kind="generic",
            )
            dummy_target_node = sg.SceneNode(
                id=edge.target,
                label="",
                kind="generic",
            )
            src_obj = _canonical_object_id(dummy_source_node)
            tgt_obj = _canonical_object_id(dummy_target_node)
            if not src_obj or not tgt_obj:
                continue
            if src_obj == tgt_obj:
                # Self-Loops auf objektiver Ebene sind selten nützlich.
                continue

            kind = edge.kind or "derived"
            label = edge.label or kind
            key = (src_obj, tgt_obj, kind, label)

            age = agg_edges.get(key)
            if age is None:
                age = _AggEdge(
                    source=src_obj,
                    target=tgt_obj,
                    kind=kind,
                    label=label,
                )
                agg_edges[key] = age
            age.add_observation(edge.weight)

    # 3) SceneGraph-Objekt bauen
    nodes: List[sg.SceneNode] = []
    edges: List[sg.SceneEdge] = []

    for agn in agg_nodes.values():
        nodes.append(agn.to_scene_node())

    for age in agg_edges.values():
        edges.append(age.to_scene_edge())

    # Meta-Infos für Diagnose/Explainability
    stats = {
        "graphs_used": graphs_used,
        "nodes_seen": nodes_seen,
        "edges_seen": edges_seen,
        "objects": len(nodes),
        "object_edges": len(edges),
        "source_namespace_prefix": source_namespace_prefix,
    }

    meta = {
        "kind": "objectgraph:auto",
        "stats": stats,
    }

    return sg.SceneGraph(nodes=nodes, edges=edges, meta=meta)


def auto_objectgraph_from_scenegraphs(
    *,
    source_namespace_prefix: str = "scene:auto_meta:",
    target_namespace: str = "object:auto:vision",
    max_graphs: int = 32,
    min_quality: Optional[float] = 0.0,
    persist: bool = True,
    quality: Optional[float] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience-Funktion: ObjectGraph bauen und optional speichern.

    Verhalten:
    ----------
    • ruft build_objectgraph_from_scenegraphs(...) auf
    • optional: speichert den Graphen über scenegraph_store.save_scenegraph()
    • liefert ein Dict mit:
        - ok   : bool
        - graph: dict (SceneGraph.to_dict())
        - meta : Dict (inkl. saved_id, stats, target_namespace)
    """
    g = build_objectgraph_from_scenegraphs(
        source_namespace_prefix=source_namespace_prefix,
        max_graphs=max_graphs,
        min_quality=min_quality,
    )
    g_dict = g.to_dict()

    out_meta: Dict[str, Any] = dict(g.meta or {})
    out_meta.setdefault("source_namespace_prefix", source_namespace_prefix)
    out_meta["target_namespace"] = target_namespace

    saved_id: Optional[int] = None
    if persist:
        try:
            saved_id = sg.save_scenegraph(
                namespace=target_namespace,
                source=f"auto:object:{source_namespace_prefix}",
                graph=g_dict,
                quality=quality,
                notes=notes,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("[objectgraph] Fehler beim Speichern des ObjectGraph: %s", exc)
            saved_id = None
        out_meta["saved_id"] = saved_id

    return {
        "ok": True,
        "graph": g_dict,
        "meta": out_meta,
    }


# =============================================================================
# CLI / Selftest
# =============================================================================


def _cli() -> None:
    """Einfacher CLI-Wrapper für Experimente und Debugging."""
    ap = argparse.ArgumentParser(
        prog="objectgraph_builder",
        description=(
            "Baut einen ObjectGraph aus bestehenden SceneGraphs und "
            "kann das Ergebnis optional in der DB speichern."
        ),
    )

    default_src_ns = os.environ.get("OROMA_OBJECTGRAPH_SRC_NS", "scene:auto_meta:")
    default_tgt_ns = os.environ.get("OROMA_OBJECTGRAPH_TARGET_NS", "object:auto:vision")
    default_max_graphs = _env_int("OROMA_OBJECTGRAPH_MAX_GRAPHS", 32)
    default_min_quality = _env_float("OROMA_OBJECTGRAPH_MIN_QUALITY", 0.0)

    ap.add_argument(
        "--src-ns-prefix",
        default=default_src_ns,
        help=f"Namespace-Prefix für SceneGraphs (Default: {default_src_ns})",
    )
    ap.add_argument(
        "--target-namespace",
        default=default_tgt_ns,
        help=f"Namespace für den ObjectGraph (Default: {default_tgt_ns})",
    )
    ap.add_argument(
        "--max-graphs",
        type=int,
        default=default_max_graphs,
        help=f"Maximale Anzahl SceneGraphs (Default: {default_max_graphs})",
    )
    ap.add_argument(
        "--min-quality",
        type=float,
        default=default_min_quality,
        help=f"Quality-Filter für SceneGraphs (Default: {default_min_quality})",
    )
    ap.add_argument(
        "--no-persist",
        action="store_true",
        help="Nicht in die DB schreiben, nur Graph auf STDOUT ausgeben.",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Mehr Logging ausgeben.",
    )

    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    persist = not args.no_persist

    res = auto_objectgraph_from_scenegraphs(
        source_namespace_prefix=args.src_ns_prefix,
        target_namespace=args.target_namespace,
        max_graphs=args.max_graphs,
        min_quality=args.min_quality,
        persist=persist,
    )

    if persist:
        sid = res.get("meta", {}).get("saved_id")
        print(
            json.dumps(
                {
                    "ok": res.get("ok"),
                    "saved_id": sid,
                    "meta": res.get("meta"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        # Vollständigen Graphen mit ausgeben – kann groß sein, ist aber
        # für Debug/Analyse oft nützlich.
        print(json.dumps(res, ensure_ascii=False, indent=2))


def _selftest() -> None:
    """Kleiner Selftest für schnellen Sanity-Check.

    - versucht, ein paar SceneGraphs zu laden
    - baut daraus einen ObjectGraph
    - gibt die wichtigsten Stats aus
    """
    print("[objectgraph] Selftest startet …")
    g = build_objectgraph_from_scenegraphs(
        source_namespace_prefix=os.environ.get(
            "OROMA_OBJECTGRAPH_SRC_NS", "scene:auto_meta:"
        ),
        max_graphs=_env_int("OROMA_OBJECTGRAPH_MAX_GRAPHS", 8),
        min_quality=_env_float("OROMA_OBJECTGRAPH_MIN_QUALITY", 0.0),
        verbose=True,
    )
    stats = (g.meta or {}).get("stats", {})
    print(
        f"  Objects: {stats.get('objects')}  Edges: {stats.get('object_edges')}"
    )
    print(
        f"  Graphs used: {stats.get('graphs_used')}  Nodes seen: {stats.get('nodes_seen')}"
    )
    print("[objectgraph] Selftest OK ✅")


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) > 1:
        _cli()
    else:
        _selftest()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/scene_graph.py
# Projekt: ORÓMA – Welt- & Selbstmodell (SceneGraph/EgoGraph)
# Version: v3.8-r1 (kompatibel zu v3.5/v3.7)
# Stand:   2025-11-24
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
#   Dieses Modul führt eine leichte Graph-Schicht ein, die auf den bestehenden
#   Bausteinen Snap / SnapChain / MetaSnaps aufsetzt:
#
#     • SceneNode  – Knoten (Objekte, Agent, Regionen, Events)
#     • SceneEdge  – Kanten (Beziehungen: near, in_front_of, collides_with, …)
#     • SceneGraph – aktueller Welt- & Selbstgraph (SceneGraph/EgoGraph)
#
#   Ziel:
#     - explizites, aber schlankes Weltmodell (Objekte + Relationen),
#     - EgoNode für ORÓMA selbst (SelfRec + Aktion + Stimmung),
#     - Persistenz als MetaSnap (meta_snaps.sources → JSON-Graph),
#     - einfache Integration in AgentLoop / Dream / Replay.
#
#   WICHTIG:
#     - Es werden KEINE neuen SQL-Tabellen angelegt.
#     - Persistenz nutzt die bestehende Tabelle meta_snaps:
#           label  → kurzer Name/Typ (z. B. "scene_graph:snake")
#           score  → optionaler Score (z. B. Reward-Summe)
#           sources→ JSON-String mit vollständigem Graph-Payload
#
# FEATURES
# ────────
#   • Datenklassen:
#       - SceneNode(kind, label, tags, attrs, embedding, from_snaps)
#       - SceneEdge(src, dst, rel, weight, ts, from_snaps)
#       - SceneGraph(nodes, edges, ts, context)
#
#   • (De-)Serialisierung:
#       - SceneNode/SceneEdge/SceneGraph → dict
#       - SceneGraph ↔ MetaSnap-Payload (kind="scene_graph")
#
#   • Persistenz:
#       - store_scene_graph() → legt Eintrag in meta_snaps an
#       - load_scene_graph()  → rekonstruiert Graph aus meta_snaps.sources
#
#   • Builder-Hilfen:
#       - SceneGraph.from_snaps()  → heuristischer Graph aus Snaps+Context
#         (nutzt Metadaten, ist bewusst generisch und fehlertolerant)
#
# INTEGRATION
# ───────────
#   • AgentLoop / Hooks:
#       - Ein Hook kann pro Schritt/Replay einen SceneGraph erzeugen und
#         optional als MetaSnap speichern, z. B.:
#
#             from core import scene_graph
#
#             def scene_graph_hook(step_ctx: dict) -> None:
#                 snaps         = step_ctx.get("snaps", [])
#                 selfrec_state = step_ctx.get("selfrec_state")  # dict|None
#                 intent_state  = step_ctx.get("intent_state")   # dict|None
#                 action        = step_ctx.get("action")         # str|None
#                 reward        = step_ctx.get("reward")         # float|None
#
#                 g = scene_graph.SceneGraph.from_snaps(
#                         snaps,
#                         episode_id=step_ctx.get("episode_id"),
#                         intent_state=intent_state,
#                         selfrec_state=selfrec_state,
#                         action=action,
#                         reward=reward,
#                     )
#                 scene_graph.store_scene_graph(g, label="scene_graph:snake")
#
#   • MetaSnaps:
#       - meta_snaps.sources enthält für SceneGraphs einen JSON-String der Form:
#
#           {
#             "kind": "scene_graph",
#             "ts": 1732440000,
#             "nodes": [...],
#             "edges": [...],
#             "context": {...}
#           }
#
# UMGEBUNGSVARIABLEN
# ──────────────────
#   • OROMA_LOG_LEVEL  – steuert das Log-Level (DEBUG/INFO/WARNING/…)
#
# NUTZUNG (Kurz)
# ──────────────
#   from core.scene_graph import SceneGraph, store_scene_graph, load_scene_graph
#
#   g = SceneGraph.from_snaps(snaps, episode_id=episode_id,
#                             intent_state=intent, selfrec_state=selfrec,
#                             action=action, reward=reward)
#   scene_id = store_scene_graph(g, label="scene_graph:demo")
#   g2 = load_scene_graph(scene_id)
#
# =============================================================================

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("oroma.scene_graph")
if not logger.handlers:
    _h = logging.StreamHandler()
    _f = logging.Formatter("[scene_graph] %(levelname)s: %(message)s")
    _h.setFormatter(_f)
    logger.addHandler(_h)

logger.setLevel(
    getattr(logging, os.environ.get("OROMA_LOG_LEVEL", "WARNING").upper(), logging.WARNING)
)

# -----------------------------------------------------------------------------
# Optionale Abhängigkeiten – Snap & SQL-Backend
# -----------------------------------------------------------------------------
try:
    from core.snap import Snap  # type: ignore
except Exception:  # pragma: no cover
    # Fallback-Stub, falls das Modul in Isolation getestet wird.
    @dataclass
    class Snap:  # type: ignore[no-redef]
        features: Sequence[float] = field(default_factory=list)
        metadata: Dict[str, Any] = field(default_factory=dict)


try:
    from core import sql_manager  # type: ignore
    from core.sql_manager import get_conn, ensure_schema  # type: ignore
except Exception as e:  # pragma: no cover
    sql_manager = None  # type: ignore[assignment]
    get_conn = None     # type: ignore[assignment]
    ensure_schema = None  # type: ignore[assignment]
    logger.warning("sql_manager nicht verfügbar – Persistenz von SceneGraphs deaktiviert: %s", e)


# =============================================================================
# Datenklassen – Nodes & Edges
# =============================================================================

@dataclass
class SceneNode:
    """
    Repräsentiert einen Knoten im SceneGraph.

    Felder
    ------
    node_id    : stabile ID (str), z. B. "ego", "obj:snake:head", "tile:3,4"
    kind       : z. B. "agent" | "object" | "region" | "event"
    label      : kompakte, menschenlesbare Bezeichnung
    tags       : Liste von semantischen Tags (["player", "enemy", "goal", ...])
    attrs      : freie Attribute (Position, Health, Score, Farbe, ...)
    embedding  : optional numerische Verdichtung (z. B. aus Fusion)
    from_snaps : Liste von Snap-/Chain-/Index-IDs, die zu diesem Node beitrugen
    """

    node_id: str
    kind: str = "object"
    label: str = ""
    tags: List[str] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)
    embedding: List[float] = field(default_factory=list)
    from_snaps: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "tags": list(self.tags or []),
            "attrs": dict(self.attrs or {}),
            "embedding": [float(x) for x in (self.embedding or [])],
            "from_snaps": list(self.from_snaps or []),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneNode":
        return cls(
            node_id=str(data.get("id", "")),
            kind=str(data.get("kind") or "object"),
            label=str(data.get("label") or ""),
            tags=list(data.get("tags") or []),
            attrs=dict(data.get("attrs") or {}),
            embedding=[float(x) for x in (data.get("embedding") or [])],
            from_snaps=[int(x) for x in (data.get("from_snaps") or [])],
        )


@dataclass
class SceneEdge:
    """
    Repräsentiert eine gerichtete Kante im SceneGraph.

    Beispiele:
        SceneEdge(src="ego", dst="obj:apple", rel="near", weight=0.9, ts=...)
    """

    src: str
    dst: str
    rel: str
    weight: float = 1.0
    ts: int = field(default_factory=lambda: int(time.time()))
    from_snaps: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "rel": self.rel,
            "weight": float(self.weight),
            "ts": int(self.ts),
            "from_snaps": list(self.from_snaps or []),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneEdge":
        return cls(
            src=str(data.get("src") or ""),
            dst=str(data.get("dst") or ""),
            rel=str(data.get("rel") or ""),
            weight=float(data.get("weight", 1.0)),
            ts=int(data.get("ts", int(time.time()))),
            from_snaps=[int(x) for x in (data.get("from_snaps") or [])],
        )


# =============================================================================
# SceneGraph – Welt- & Selbstgraph
# =============================================================================

@dataclass
class SceneGraph:
    """
    Welt- & Selbstgraph für eine Episode / einen Zeitpunkt.

    Eigenschaften
    -------------
    nodes   : Dict[node_id, SceneNode]
    edges   : List[SceneEdge]
    ts      : Zeitstempel (Unix, Sek.)
    context : beliebiger Zusatz-Context (episode_id, intent_state, selfrec_state,
              reward_window, game, namespace, ...)
    """

    nodes: Dict[str, SceneNode] = field(default_factory=dict)
    edges: List[SceneEdge] = field(default_factory=list)
    ts: int = field(default_factory=lambda: int(time.time()))
    context: Dict[str, Any] = field(default_factory=dict)

    # ---------------- Basis-API ------------------------------------------------

    def add_node(self, node: SceneNode, overwrite: bool = False) -> None:
        """
        Fügt einen Knoten hinzu oder mergen ihn (from_snaps/attrs/tags).
        """
        if node.node_id in self.nodes and not overwrite:
            existing = self.nodes[node.node_id]
            # Tags/Attrs/Referenzen mergen (idempotent)
            existing.tags = list(sorted({*existing.tags, *node.tags}))
            existing.attrs.update(node.attrs or {})
            # from_snaps mergen, Dedupe
            if node.from_snaps:
                merged = {int(x) for x in (existing.from_snaps or [])}
                merged.update(int(x) for x in node.from_snaps)
                existing.from_snaps = sorted(merged)
            # Embedding: wenn neu und leer, übernehme; sonst lasse existing
            if not existing.embedding and node.embedding:
                existing.embedding = list(node.embedding)
        else:
            self.nodes[node.node_id] = node

    def get_or_create_node(
        self,
        node_id: str,
        *,
        kind: str = "object",
        label: str = "",
        tags: Optional[Iterable[str]] = None,
    ) -> SceneNode:
        if node_id in self.nodes:
            return self.nodes[node_id]
        n = SceneNode(
            node_id=node_id,
            kind=kind,
            label=label or node_id,
            tags=list(tags or []),
        )
        self.nodes[node_id] = n
        return n

    def add_edge(self, edge: SceneEdge) -> None:
        """
        Fügt eine Kante hinzu. Dedupe ist bewusst einfach gehalten: gleiche
        (src, dst, rel) werden NICHT automatisch zusammengeführt, sondern
        können mehrfach vorkommen (z. B. aus unterschiedlichen Snaps).
        """
        self.edges.append(edge)

    # ---------------- Serialisierung ------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "scene_graph",
            "ts": int(self.ts),
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "context": dict(self.context or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneGraph":
        nodes_seq = data.get("nodes") or []
        edges_seq = data.get("edges") or []
        nodes: Dict[str, SceneNode] = {}
        for n_raw in nodes_seq:
            try:
                n = SceneNode.from_dict(n_raw)
                if n.node_id:
                    nodes[n.node_id] = n
            except Exception as ex:
                logger.debug("SceneNode.from_dict Fehler: %s", ex)
        edges: List[SceneEdge] = []
        for e_raw in edges_seq:
            try:
                e = SceneEdge.from_dict(e_raw)
                if e.src and e.dst and e.rel:
                    edges.append(e)
            except Exception as ex:
                logger.debug("SceneEdge.from_dict Fehler: %s", ex)
        ts = int(data.get("ts", int(time.time())))
        context = dict(data.get("context") or {})
        return cls(nodes=nodes, edges=edges, ts=ts, context=context)

    # Abkürzungen für MetaSnap-Payload
    def to_meta_payload(self) -> Dict[str, Any]:
        """
        Gibt den Payload zurück, der in meta_snaps.sources gespeichert wird.
        """
        return self.to_dict()

    @classmethod
    def from_meta_payload(cls, payload: Dict[str, Any]) -> "SceneGraph":
        """
        Erzeugt einen SceneGraph aus einem MetaSnap-Payload (JSON).
        """
        return cls.from_dict(payload or {})

    # ---------------- Heuristischer Builder -----------------------------------

    @classmethod
    def from_snaps(
        cls,
        snaps: Sequence[Snap],
        *,
        episode_id: Optional[int] = None,
        intent_state: Optional[Dict[str, Any]] = None,
        selfrec_state: Optional[Dict[str, Any]] = None,
        action: Optional[str] = None,
        reward: Optional[float] = None,
        game: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> "SceneGraph":
        """
        Baut einen SceneGraph heuristisch aus einer Menge Snaps + Kontext.

        Design:
          - bewusst generisch und defensiv,
          - nutzt Metadaten-Felder, falls vorhanden:
              snap.metadata.get("scene_nodes")
              snap.metadata.get("scene_edges")
              snap.metadata.get("ego") / ("self" / "agent")
              snap.metadata.get("game") / ("namespace")
          - wenn keine strukturierte Info vorhanden ist, wird ein sehr einfacher
            Graph erzeugt:
              EgoNode + "frame:*"-Nodes, verbunden mit "perceives".
        """
        ts_now = int(time.time())
        g = cls(ts=ts_now, context={})

        # Context auf oberer Ebene
        ctx: Dict[str, Any] = {}
        if episode_id is not None:
            ctx["episode_id"] = int(episode_id)
        if intent_state is not None:
            ctx["intent_state"] = intent_state
        if selfrec_state is not None:
            ctx["selfrec_state"] = selfrec_state
        if action is not None:
            ctx["action"] = action
        if reward is not None:
            ctx["reward"] = float(reward)
        if game is not None:
            ctx["game"] = game
        if namespace is not None:
            ctx["namespace"] = namespace

        # Heuristik: falls Snaps Metadaten game/namespace tragen, übernehmen
        if snaps:
            try:
                meta0 = getattr(snaps[0], "metadata", {}) or {}
                if "game" in meta0 and "game" not in ctx:
                    ctx["game"] = meta0.get("game")
                if "namespace" in meta0 and "namespace" not in ctx:
                    ctx["namespace"] = meta0.get("namespace")
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.scene_graph.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )

        g.context = ctx

        # EgoNode aufbauen
        ego = SceneNode(
            node_id="ego",
            kind="agent",
            label="ORÓMA",
            tags=["agent", "self"],
            attrs={},
            from_snaps=[],
        )

        # SelfRec-Infos eintragen (falls vorhanden)
        if selfrec_state:
            # z. B. {"mood":"neutral","confidence":0.6,"valence":0.1,"arousal":0.3}
            ego.attrs["selfrec"] = dict(selfrec_state)
        # Intent-Layer (Roter Faden)
        if intent_state:
            ego.attrs["intent"] = dict(intent_state)
        # Letzte Aktion + Reward
        if action is not None:
            ego.attrs["last_action"] = action
        if reward is not None:
            ego.attrs["last_reward"] = float(reward)

        g.add_node(ego)

        # Pro Snap versuchen wir, strukturierte Szene-Infos zu extrahieren.
        # Falls keine vorhanden sind, legen wir generische "frame"-Nodes an.
        snap_idx = 0
        for snap in snaps:
            snap_idx += 1
            meta = getattr(snap, "metadata", {}) or {}
            snap_src_ids: List[int] = []
            # SnapChain-/Index-ID wenn vorhanden übernehmen (optional)
            for key in ("snap_id", "_snap_index_id", "snap_index_id", "chain_id"):
                val = meta.get(key)
                if isinstance(val, int):
                    snap_src_ids.append(val)

            # 1) strukturierte Szene-Nodes (falls vorhanden)
            nodes_spec = meta.get("scene_nodes")
            edges_spec = meta.get("scene_edges")

            if isinstance(nodes_spec, Sequence) and nodes_spec:
                for spec in nodes_spec:
                    if not isinstance(spec, dict):
                        continue
                    nid = str(spec.get("id") or spec.get("node_id") or "")
                    if not nid:
                        # Fallback: generische ID pro Snap
                        nid = f"node:{snap_idx}:{len(g.nodes)+1}"
                    kind = str(spec.get("kind") or spec.get("type") or "object")
                    label = str(spec.get("label") or nid)
                    tags = list(spec.get("tags") or [])
                    attrs = dict(spec.get("attrs") or {})
                    # Heuristik: Position übernehmen, falls flach in spec steht
                    for k in ("x", "y", "row", "col"):
                        if k in spec and k not in attrs:
                            attrs[k] = spec[k]
                    # Embedding optional
                    emb = []
                    if "embedding" in spec:
                        try:
                            emb = [float(x) for x in (spec.get("embedding") or [])]
                        except Exception:
                            emb = []
                    node = SceneNode(
                        node_id=nid,
                        kind=kind,
                        label=label,
                        tags=tags,
                        attrs=attrs,
                        embedding=emb,
                        from_snaps=list(snap_src_ids),
                    )
                    g.add_node(node)

            # 2) strukturierte Szene-Kanten (falls vorhanden)
            if isinstance(edges_spec, Sequence) and edges_spec:
                for es in edges_spec:
                    if not isinstance(es, dict):
                        continue
                    src = str(es.get("src") or es.get("from") or "")
                    dst = str(es.get("dst") or es.get("to") or "")
                    rel = str(es.get("rel") or es.get("relation") or "")
                    if not src or not dst or not rel:
                        continue
                    wt = float(es.get("weight", 1.0))
                    ets = int(es.get("ts", ts_now))
                    edge = SceneEdge(
                        src=src,
                        dst=dst,
                        rel=rel,
                        weight=wt,
                        ts=ets,
                        from_snaps=list(snap_src_ids),
                    )
                    g.add_edge(edge)

            # 3) Fallback: generischer Frame-Node + perceive-Kante
            if not nodes_spec and not edges_spec:
                frame_id = f"frame:{snap_idx}"
                frame_label = meta.get("label") or meta.get("kind") or frame_id
                frame_tags: List[str] = []
                if "origin" in meta:
                    frame_tags.append(str(meta.get("origin")))
                if "game" in meta:
                    frame_tags.append(f"game:{meta.get('game')}")
                frame_attrs: Dict[str, Any] = {}
                for k in ("origin", "status", "namespace", "quality"):
                    if k in meta:
                        frame_attrs[k] = meta[k]
                fnode = SceneNode(
                    node_id=frame_id,
                    kind="frame",
                    label=str(frame_label),
                    tags=frame_tags,
                    attrs=frame_attrs,
                    from_snaps=list(snap_src_ids),
                )
                g.add_node(fnode)
                g.add_edge(
                    SceneEdge(
                        src="ego",
                        dst=frame_id,
                        rel="perceives",
                        weight=1.0,
                        ts=ts_now,
                        from_snaps=list(snap_src_ids),
                    )
                )

        return g


# =============================================================================
# Persistenz über meta_snaps
# =============================================================================

def _ensure_meta_schema() -> None:
    """
    Stellt sicher, dass die Tabelle meta_snaps existiert.
    Nutzt sql_manager.ensure_schema(), falls verfügbar.
    """
    if ensure_schema is None:
        return
    try:
        ensure_schema()
    except Exception as ex:  # pragma: no cover
        logger.debug("ensure_schema() für meta_snaps fehlgeschlagen: %s", ex)


def store_scene_graph(
    graph: SceneGraph,
    label: Optional[str] = None,
    score: Optional[float] = None,
) -> Optional[int]:
    """
    Speichert einen SceneGraph als MetaSnap in der Tabelle 'meta_snaps'.

    Parameter
    ---------
    graph : SceneGraph
        Zu speichernder Graph.
    label : str|None
        Kurzer Label-String für meta_snaps.label (z. B. "scene_graph:snake").
        Default: "scene_graph".
    score : float|None
        Optionaler Score für meta_snaps.score, z. B. Gesamt-Reward der Episode.
        Wenn None, wird versucht, graph.context["reward"] zu verwenden.

    Rückgabewert
    ------------
    int|None : ID des meta_snaps-Eintrags oder None bei Fehler / fehlendem sql_manager.
    """
    if sql_manager is None or get_conn is None:
        logger.warning("store_scene_graph: sql_manager nicht verfügbar, kein Persist.")
        return None

    _ensure_meta_schema()

    lbl = label or "scene_graph"
    sc: float = 0.0
    if score is not None:
        sc = float(score)
    else:
        try:
            if isinstance(graph.context, dict) and "reward" in graph.context:
                sc = float(graph.context.get("reward") or 0.0)
        except Exception:
            sc = 0.0

    payload = graph.to_meta_payload()
    try:
        sources_txt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception as ex:
        logger.error("store_scene_graph: JSON-Dump fehlgeschlagen: %s", ex)
        return None

    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO meta_snaps (label, score, sources) VALUES (?, ?, ?)",
                (lbl, sc, sources_txt),
            )
            conn.commit()
            mid = int(cur.lastrowid)
            logger.info("SceneGraph als MetaSnap gespeichert: id=%s label=%s", mid, lbl)
            return mid
    except Exception as ex:
        logger.error("store_scene_graph: Insert in meta_snaps fehlgeschlagen: %s", ex)
        return None


def load_scene_graph(meta_id: int) -> Optional[SceneGraph]:
    """
    Lädt einen SceneGraph aus meta_snaps anhand der ID.

    Erwartet, dass meta_snaps.sources ein JSON mit "kind":"scene_graph" enthält.
    Bei Fehlern (kein Eintrag, kaputtes JSON) wird None zurückgegeben.
    """
    if sql_manager is None or get_conn is None:
        logger.warning("load_scene_graph: sql_manager nicht verfügbar.")
        return None

    _ensure_meta_schema()

    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT label, score, sources FROM meta_snaps WHERE id=?",
                (int(meta_id),),
            ).fetchone()
    except Exception as ex:
        logger.error("load_scene_graph: SELECT fehlgeschlagen: %s", ex)
        return None

    if not row:
        logger.info("load_scene_graph: kein meta_snaps-Eintrag für id=%s", meta_id)
        return None

    sources_txt = row.get("sources") if isinstance(row, dict) else row[2]
    if not sources_txt:
        logger.warning("load_scene_graph: leeres sources-Feld für id=%s", meta_id)
        return None

    try:
        payload = json.loads(sources_txt)
    except Exception as ex:
        logger.error("load_scene_graph: JSON-Parse fehlgeschlagen: %s", ex)
        return None

    kind = payload.get("kind")
    if kind != "scene_graph":
        logger.warning(
            "load_scene_graph: kind='%s' (erwartet 'scene_graph') – id=%s",
            kind,
            meta_id,
        )
    try:
        g = SceneGraph.from_meta_payload(payload)
        return g
    except Exception as ex:
        logger.error("load_scene_graph: SceneGraph.from_meta_payload Fehler: %s", ex)
        return None


# =============================================================================
# Selftest
# =============================================================================

def _selftest(verbose: bool = True) -> None:
    """
    Sehr einfacher Selftest:
      - baut einen künstlichen SceneGraph,
      - serialisiert ↔ deserialisiert,
      - speichert optional in meta_snaps (falls sql_manager verfügbar).
    """
    if verbose:
        print("[SceneGraph] Selftest startet ...")

    # Dummy-Snaps (nur Metadaten relevant)
    s1 = Snap(features=[], metadata={"origin": "test", "label": "frame-1"})
    s2 = Snap(features=[], metadata={"origin": "test", "label": "frame-2"})

    g = SceneGraph.from_snaps(
        [s1, s2],
        episode_id=42,
        intent_state={"goal": "explore"},
        selfrec_state={"mood": "neutral", "confidence": 0.7},
        action="step_forward",
        reward=0.5,
        game="testgame",
        namespace="test",
    )

    if verbose:
        print("  Nodes:", list(g.nodes.keys()))
        print("  Edges:", [(e.src, e.rel, e.dst) for e in g.edges])
        print("  Context:", g.context)

    payload = g.to_meta_payload()
    g2 = SceneGraph.from_meta_payload(payload)

    assert set(g2.nodes.keys()) == set(g.nodes.keys()), "Node-Menge unterscheidet sich"
    if verbose:
        print("  Roundtrip OK (dict ↔ SceneGraph)")

    mid = store_scene_graph(g, label="scene_graph:selftest")
    if verbose:
        print("  meta_snaps.id:", mid)

    if mid is not None:
        g3 = load_scene_graph(mid)
        if verbose:
            if g3:
                print("  load_scene_graph OK, Nodes:", list(g3.nodes.keys()))
            else:
                print("  load_scene_graph → None (kein sql_manager?)")

    if verbose:
        print("[SceneGraph] Selftest OK ✅")


if __name__ == "__main__":  # pragma: no cover
    _selftest(verbose=True)
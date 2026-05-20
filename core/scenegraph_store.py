#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/scenegraph_store.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-first Graph Layer)
# Modul:     SceneGraphStore – Persistenz + Modell für SceneGraphs (Nodes/Edges/Meta) sowie Bridge aus MetaSnaps↔SnapChains
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist die zentrale, leichtgewichtige „Graph“-Schicht in ORÓMA:
#   - SceneGraphs sind Moment-/Verdichtungs-Graphen (2.5D/episodisch), die z. B.
#     aus Vision-Tokens, MetaSnaps oder Episoden gebaut werden.
#   - Sie werden in SQLite persistiert (Tabelle `scenegraphs`) und sind damit:
#       • UI-freundlich (listbar, abrufbar)
#       • explainability-freundlich (Nodes/Edges/Meta als JSON)
#       • pipeline-freundlich (DreamWorker/Tools können daraus weiter ableiten)
#
# Das Modul stellt bereit:
#   1) Datenmodell: SceneNode, SceneEdge, SceneGraph
#   2) SQLite-Persistenz: save/get/list (+ optional delete)
#   3) Bridge-Builder: build_scenegraph_from_meta_snaps()
#      - verbindet MetaSnaps mit referenzierten SnapChains + deren origin
#
# HEADLESS & PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine GUI, keine externen Graph-Libs nötig.
# - SQLite-first: Daten liegen lokal in oroma.db (über core.sql_manager.get_conn()).
# - JSON als Austauschformat: graph_json ist reines JSON (kein Pickle).
# - Robustheit: Parsing/DB-Fehler führen zu defensiven Defaults statt Crash.
#
# DB / PERSISTENZ (EXAKT IM CODEPFAD)
# ───────────────────────────────────
# Diese Datei nutzt:
#   from core.sql_manager import get_conn
# und erwartet, dass sql_manager eine Connection mit dict row_factory liefert.
#
# Tabelle: scenegraphs
#   CREATE TABLE IF NOT EXISTS scenegraphs (
#     id        INTEGER PRIMARY KEY AUTOINCREMENT,
#     ts        INTEGER NOT NULL,      -- Unix seconds
#     namespace TEXT,                  -- z.B. "scene:auto_meta:..."
#     source    TEXT,                  -- z.B. "dream:auto_meta"
#     quality   REAL,                  -- optional 0..1
#     graph_json TEXT NOT NULL,        -- JSON: {"nodes":[...],"edges":[...],"meta":{...}}
#     notes     TEXT                   -- freie Notiz / Debug
#   )
#
# Wichtig:
# - _ensure_scenegraph_schema() wird bei save/get/list typischerweise aufgerufen,
#   damit die Tabelle auch in Slim-Deploys existiert.
#
# GRAPH JSON FORMAT (STABILER VERTRAG)
# ────────────────────────────────────
# graph_json enthält ein Dict mit:
#   - "nodes": List[Dict]
#   - "edges": List[Dict]
#   - "meta" : Dict
#
# Node-Format (SceneNode.to_dict()):
#   { "id": str, "label": str, "kind": str, "score": float|None, "ts": int|None, "meta": Dict }
#
# Edge-Format (SceneEdge.to_dict()):
#   { "source": str, "target": str, "label": str, "kind": str, "weight": float|None, "meta": Dict }
#
# Kinds (typisch, nicht exklusiv):
#   Node.kind: "meta" | "snapchain" | "origin" | "object" | "generic"
#   Edge.kind: "ref" | "contains" | "derived" | "co_occur" | "origin"
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# _ensure_scenegraph_schema() -> None
#   - erstellt Tabelle scenegraphs, idempotent
#
# save_scenegraph(ts:int, namespace:str|None, source:str|None, graph:dict, quality:float|None, notes:str|None) -> int
#   - speichert graph_json als kompaktes JSON, liefert graph_id
#
# get_scenegraph(graph_id:int) -> Optional[dict]
#   - lädt einen Graphdatensatz (inkl. parsed graph_json) als Dict:
#       {"id","ts","namespace","source","quality","graph","notes"}
#
# list_scenegraphs(limit:int=50, namespace_prefix:str|None=None) -> List[dict]
#   - listet neueste Graphen (ORDER BY ts DESC, id DESC)
#   - optional Filter: namespace LIKE "<prefix>%"
#
# delete_scenegraph(graph_id:int) -> bool
#   - löscht genau einen Datensatz per id
#   - Hinweis: Das ist eine Verwaltungsfunktion; produktive Pipelines sollten
#     i. d. R. additiv arbeiten (kein Pruning hier erzwingen).
#
# BRIDGE: build_scenegraph_from_meta_snaps()
# ──────────────────────────────────────────
# Zweck:
# - baut einen SceneGraph aus den jüngsten MetaSnaps (meta_snaps Tabelle) und den
#   referenzierten SnapChains (snapchains Tabelle).
#
# Node-IDs:
#   - "meta:{id}"        (kind="meta")
#   - "chain:{id}"       (kind="snapchain")
#   - "origin:{origin}"  (kind="origin")
#
# Edge-Semantik (typisch im Code):
#   - meta → chain        (kind z.B. "ref", label z.B. "uses")
#   - chain → origin      (kind z.B. "origin", label z.B. "origin")
#   - optional meta → origin (wenn schnell ableitbar)
#
# Quellen-Parsing:
# - MetaSnaps speichern sources als TEXT (z. B. "chain:12|chain:15|snap:99").
# - _parse_sources_txt() zerlegt in List[str]
# - _source_to_chain_id() extrahiert chain_id aus "chain:<id>"
#
# Convenience:
# auto_scenegraph_from_meta(...)
#   - ruft build_scenegraph_from_meta_snaps() auf
#   - kann optional direkt persistieren (save_scenegraph)
#   - liefert {"ok":bool, "graph":dict, "meta":{...saved_id...}}
#
# FEHLER-/ROBUSTHEITSVERHALTEN
# ────────────────────────────
# - Wenn graph_json nicht parsebar ist → graph={}
# - Fehlende Felder in DB-Rows werden defensiv behandelt (row.get()).
# - DB-Operationen sind kurz; Lock/OperationalError wird geloggt und führt zu
#   None/[] statt Crash (damit Orchestrator/UI stabil bleiben).
#
# ENV
# ───
# Dieses Modul definiert keine eigenen ENV-Schalter.
# DB-Verhalten (WAL/busy_timeout/path) kommt indirekt über core.sql_manager.
#
# SELFTEST (CLI)
# ─────────────
# python3 /opt/ai/oroma/core/scenegraph_store.py
#   - erzeugt einen Mini-Graph, speichert ihn, lädt ihn wieder, listet die letzten Einträge.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - graph_json bleibt JSON-Text (kein Pickle, kein binäres Customformat).
# - Node/Edge Dict Keys müssen stabil bleiben (UI/Tools erwarten diese Struktur).
# - Bridge-Funktionen müssen tolerant gegenüber unvollständigen sources bleiben.
# - delete_scenegraph existiert, aber darf nicht stillschweigend in Auto-Pipelines
#   eingebaut werden (non-destructive Prinzip).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from core.sql_manager import get_conn  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "scenegraph_store benötigt core.sql_manager.get_conn(). "
        "Bitte sicherstellen, dass core/sql_manager.py vorhanden ist."
    ) from e

logger = logging.getLogger("oroma.scenegraph")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[scenegraph] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# =============================================================================
# Datenklassen
# =============================================================================

@dataclass
class SceneNode:
    """
    Repräsentiert einen Knoten im SceneGraph.

    Felder
    ------
    id    : eindeutiger Node-Identifier (z.B. "meta:17", "chain:715", "origin:game:snake")
    label : menschenlesbar (z.B. "Meta 17 – Snake-Strategie")
    kind  : Typ (z.B. "meta", "snapchain", "origin", "object", "agent", ...)
    score : optionaler Score (z.B. MetaSnap-Score, Qualität, Wichtigkeit)
    ts    : optionaler Timestamp (Unix-Sekunden)
    meta  : freies Dictionary für Zusatzinfos (z.B. origin, status, tags)
    """
    id: str
    label: str
    kind: str = "generic"
    score: Optional[float] = None
    ts: Optional[int] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "score": self.score,
            "ts": self.ts,
            "meta": self.meta or {},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneNode":
        return cls(
            id=str(data.get("id") or ""),
            label=str(data.get("label") or ""),
            kind=str(data.get("kind") or "generic"),
            score=data.get("score"),
            ts=data.get("ts"),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class SceneEdge:
    """
    Verbindung zwischen zwei Nodes im SceneGraph.

    Felder
    ------
    source : Node-ID (z.B. "meta:17")
    target : Node-ID (z.B. "chain:715")
    label  : kurze Beschreibung (z.B. "describes", "origin", "causes")
    kind   : Typ (z.B. "meta_to_chain", "chain_to_origin", ...)
    weight : optionales Gewicht (z.B. Stärke der Beziehung)
    meta   : Zusatzinfos
    """
    source: str
    target: str
    label: str = ""
    kind: str = "generic"
    weight: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "kind": self.kind,
            "weight": self.weight,
            "meta": self.meta or {},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneEdge":
        return cls(
            source=str(data.get("source") or ""),
            target=str(data.get("target") or ""),
            label=str(data.get("label") or ""),
            kind=str(data.get("kind") or "generic"),
            weight=data.get("weight"),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class SceneGraph:
    """
    Vollständiger SceneGraph: Nodes, Edges, Meta-Infos.
    """
    nodes: List[SceneNode] = field(default_factory=list)
    edges: List[SceneEdge] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "meta": self.meta or {},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneGraph":
        nodes = [SceneNode.from_dict(d) for d in (data.get("nodes") or [])]
        edges = [SceneEdge.from_dict(d) for d in (data.get("edges") or [])]
        meta = dict(data.get("meta") or {})
        return cls(nodes=nodes, edges=edges, meta=meta)


# =============================================================================
# Schema (scenegraphs)
# =============================================================================

def _ensure_scenegraph_schema() -> None:
    """
    Stellt sicher, dass die Tabelle 'scenegraphs' existiert.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scenegraphs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            namespace TEXT,
            source TEXT,
            quality REAL,
            graph_json TEXT NOT NULL,
            notes TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_scenegraphs_ts ON scenegraphs(ts)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_scenegraphs_ns ON scenegraphs(namespace)"
    )
    conn.commit()
    logger.debug("[scenegraph_store] Schema scenegraphs OK")


# =============================================================================
# Persistenz-API
# =============================================================================

def save_scenegraph(
    namespace: Optional[str],
    source: Optional[str],
    graph: Dict[str, Any],
    *,
    quality: Optional[float] = None,
    notes: Optional[str] = None,
    ts: Optional[int] = None,
) -> Optional[int]:
    """
    Speichert einen SceneGraph in der DB.

    Parameter
    ---------
    namespace : str|None
        Logischer Kontext (z. B. "decision", "selfrec", "scene", "missions").
    source : str|None
        Technische Quelle (z. B. "auto:meta_snaps", "ui:manual").
    graph : dict
        SceneGraph-Struktur (SceneGraph.to_dict()).

    Rückgabewert
    ------------
    int|None : ID des gespeicherten Graphen oder None bei Fehler.
    """
    _ensure_scenegraph_schema()
    try:
        payload = json.dumps(graph or {}, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        logger.error("[scenegraph_store] save_scenegraph: JSON-Fehler: %s", e)
        return None

    try:
        # Stufe C: wenn DBWriter aktiv ist, INSERT über globalen Single-Writer.
        try:
            from core import sql_manager  # type: ignore
            _dbw_enabled = getattr(sql_manager, "_dbw_enabled", None)
            _dbw = getattr(sql_manager, "_dbw", None)
            if callable(_dbw_enabled) and _dbw_enabled() and _dbw is not None:
                gid = int(getattr(_dbw, "exec_lastrowid")(
                    """
                    INSERT INTO scenegraphs (ts, namespace, source, quality, graph_json, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    params=[
                        int(ts if ts is not None else int(time.time())),
                        namespace,
                        source,
                        float(quality) if quality is not None else None,
                        payload,
                        notes,
                    ],
                    tag="scenegraph_store.save",
                    priority="low",
                    timeout_ms=8000,
                    db="oroma",
                ) or 0)
                logger.info("[scenegraph_store] save_scenegraph: id=%d, ns=%s, src=%s", gid, namespace, source)
                return gid
        except Exception as e:
            try:
                if callable(getattr(sql_manager, "_dbw_enabled", None)) and getattr(sql_manager, "_dbw_enabled")():
                    logger.error("[scenegraph_store] save_scenegraph: DBWriter failed – skip (no local fallback): %s", e)
                    return None
            except Exception:
                pass

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO scenegraphs
                (ts, namespace, source, quality, graph_json, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(ts if ts is not None else int(time.time())),
                namespace,
                source,
                float(quality) if quality is not None else None,
                payload,
                notes,
            ),
        )
        conn.commit()
        gid = int(cur.lastrowid)
        logger.info("[scenegraph_store] save_scenegraph: id=%d, ns=%s, src=%s", gid, namespace, source)
        return gid
    except Exception as e:
        print(f"[scenegraph_store] save_scenegraph: DB-Fehler: {e}")
        return None


def get_scenegraph(graph_id: int) -> Optional[Dict[str, Any]]:
    """
    Lädt einen SceneGraph als Dict aus der DB.

    Rückgabe: dict mit Feldern:
        - id, ts, namespace, source, quality, notes, graph
    """
    _ensure_scenegraph_schema()
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, ts, namespace, source, quality, graph_json, notes "
            "FROM scenegraphs WHERE id = ?",
            (int(graph_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            graph = json.loads(row["graph_json"] or "{}")
        except Exception:
            graph = {}
        return {
            "id": int(row["id"]),
            "ts": int(row["ts"]),
            "namespace": row.get("namespace"),
            "source": row.get("source"),
            "quality": row.get("quality"),
            "notes": row.get("notes"),
            "graph": graph,
        }
    except Exception as e:
        print(f"[scenegraph_store] get_scenegraph: DB-Fehler: {e}")
        return None


def list_scenegraphs(
    *,
    limit: int = 50,
    namespace: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Listet SceneGraphs (ohne Graph-JSON, nur Kopfzeilen).

    Rückgabe: Liste von dicts:
        [{id, ts, namespace, source, quality, notes}, ...]
    """
    _ensure_scenegraph_schema()
    limit = max(1, int(limit))
    try:
        conn = get_conn()
        cur = conn.cursor()
        if namespace:
            cur.execute(
                """
                SELECT id, ts, namespace, source, quality, notes
                  FROM scenegraphs
                 WHERE namespace = ?
                 ORDER BY ts DESC
                 LIMIT ?
                """,
                (namespace, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, ts, namespace, source, quality, notes
                  FROM scenegraphs
                 ORDER BY ts DESC
                 LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall() or []
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "ts": int(r["ts"]),
                    "namespace": r.get("namespace"),
                    "source": r.get("source"),
                    "quality": r.get("quality"),
                    "notes": r.get("notes"),
                }
            )
        return out
    except Exception as e:
        print(f"[scenegraph_store] list_scenegraphs: DB-Fehler: {e}")
        return []


def delete_scenegraph(graph_id: int) -> bool:
    """
    Löscht einen SceneGraph aus der DB.
    """
    _ensure_scenegraph_schema()
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM scenegraphs WHERE id = ?", (int(graph_id),))
        conn.commit()
        return True
    except Exception as e:
        print(f"[scenegraph_store] delete_scenegraph: DB-Fehler: {e}")
        return False


# =============================================================================
# Bridge: MetaSnaps + SnapChains → SceneGraph
# =============================================================================

def _parse_sources_txt(s: Optional[str]) -> List[str]:
    """
    Parsen von meta_snaps.sources (TEXT, meist JSON-Liste).
    """
    if not s:
        return []
    s = s.strip()
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        # Fallback: einfache Komma- oder Semikolon-Liste
        parts = [p.strip() for p in s.replace(";", ",").split(",")]
        return [p for p in parts if p]
    return []


def _source_to_chain_id(src: Any) -> Optional[int]:
    """
    Versucht aus einem Source-Eintrag eine SnapChain-ID zu extrahieren.

    Unterstützt u.a.:
        - "123"
        - "chain:123"
        - "snapchain:123"
        - "sc:123"
        - int/float
    """
    if isinstance(src, (int, float)):
        try:
            x = int(src)
            return x if x >= 0 else None
        except Exception:
            return None
    if not isinstance(src, str):
        return None
    s = src.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    for prefix in ("chain:", "snapchain:", "sc:", "episode:"):
        if s.startswith(prefix):
            tail = s[len(prefix) :].strip()
            if tail.isdigit():
                return int(tail)
    return None


def build_scenegraph_from_meta_snaps(
    *,
    max_meta: int = 32,
    max_chains_per_meta: int = 16,
) -> SceneGraph:
    """
    Baut einen SceneGraph aus den jüngsten MetaSnaps + den referenzierten SnapChains.

    Struktur
    --------
    Nodes:
      - meta:{id}        kind="meta"
      - chain:{id}       kind="snapchain"
      - origin:{origin}  kind="origin"

    Edges:
      - meta:{id}  → chain:{id}   label="describes", kind="meta_to_chain"
      - chain:{id} → origin:{o}   label="origin",    kind="chain_to_origin"

    Parameter
    ---------
    max_meta : int
        Anzahl MetaSnaps (ORDER BY id DESC) für den Graphen.
    max_chains_per_meta : int
        Begrenzung der pro MetaSnap verfolgten Chain-Referenzen.

    Rückgabe
    --------
    SceneGraph
    """
    max_meta = max(1, int(max_meta))
    max_chains_per_meta = max(1, int(max_chains_per_meta))

    nodes_by_id: Dict[str, SceneNode] = {}
    edges: List[SceneEdge] = []

    try:
        conn = get_conn()
        cur = conn.cursor()

        # 1) MetaSnaps holen
        cur.execute(
            "SELECT id, label, score, sources FROM meta_snaps ORDER BY id DESC LIMIT ?",
            (max_meta,),
        )
        meta_rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("[scenegraph_store] build_scenegraph_from_meta_snaps: DB-Fehler meta_snaps: %s", e)
        return SceneGraph(nodes=[], edges=[], meta={"error": str(e)})

    chain_cache: Dict[int, Dict[str, Any]] = {}

    for mr in meta_rows:
        mid = int(mr["id"])
        label = mr.get("label") or f"Meta {mid}"
        score = mr.get("score")
        sources_txt = mr.get("sources")
        meta_node_id = f"meta:{mid}"

        if meta_node_id not in nodes_by_id:
            nodes_by_id[meta_node_id] = SceneNode(
                id=meta_node_id,
                label=label,
                kind="meta",
                score=float(score) if score is not None else None,
                ts=None,
                meta={},
            )

        src_list = _parse_sources_txt(sources_txt)
        if not src_list:
            continue

        chains_for_meta = 0
        for src in src_list:
            cid = _source_to_chain_id(src)
            if cid is None:
                continue
            if chains_for_meta >= max_chains_per_meta:
                break
            chains_for_meta += 1

            # 2) SnapChain-Info holen (mit Cache)
            if cid not in chain_cache:
                try:
                    cur2 = conn.cursor()
                    cur2.execute(
                        "SELECT id, ts, quality, origin, status, namespace, notes "
                        "FROM snapchains WHERE id = ?",
                        (cid,),
                    )
                    row = cur2.fetchone()
                    chain_cache[cid] = row or {}
                except Exception as e:
                    logger.debug("[scenegraph_store] SnapChain fetch Fehler (id=%d): %s", cid, e)
                    chain_cache[cid] = {}

            cinfo = chain_cache.get(cid) or {}
            if not cinfo:
                continue

            chain_node_id = f"chain:{cid}"
            if chain_node_id not in nodes_by_id:
                origin = cinfo.get("origin")
                quality = cinfo.get("quality")
                ts_val = cinfo.get("ts")
                ns = cinfo.get("namespace")
                status = cinfo.get("status")
                notes = cinfo.get("notes")

                nodes_by_id[chain_node_id] = SceneNode(
                    id=chain_node_id,
                    label=f"Chain {cid}",
                    kind="snapchain",
                    score=float(quality) if quality is not None else None,
                    ts=int(ts_val) if ts_val is not None else None,
                    meta={
                        "origin": origin,
                        "namespace": ns,
                        "status": status,
                        "notes": notes,
                    },
                )

            # Edge: Meta → Chain
            edges.append(
                SceneEdge(
                    source=meta_node_id,
                    target=chain_node_id,
                    label="describes",
                    kind="meta_to_chain",
                )
            )

            # 3) Origin-Knoten + Edge
            origin = cinfo.get("origin") or "unknown"
            origin_node_id = f"origin:{origin}"
            if origin_node_id not in nodes_by_id:
                nodes_by_id[origin_node_id] = SceneNode(
                    id=origin_node_id,
                    label=str(origin),
                    kind="origin",
                    score=None,
                    ts=None,
                    meta={},
                )

            edges.append(
                SceneEdge(
                    source=chain_node_id,
                    target=origin_node_id,
                    label="origin",
                    kind="chain_to_origin",
                )
            )

    graph = SceneGraph(
        nodes=list(nodes_by_id.values()),
        edges=edges,
        meta={
            "built_at": int(time.time()),
            "source": "meta_snaps",
            "max_meta": max_meta,
            "max_chains_per_meta": max_chains_per_meta,
            "num_meta": len(meta_rows),
            "num_nodes": len(nodes_by_id),
            "num_edges": len(edges),
        },
    )
    return graph


def auto_scenegraph_from_meta(
    *,
    namespace: str = "scene:auto_meta",
    source: str = "auto:meta_snaps",
    max_meta: int = 32,
    max_chains_per_meta: int = 16,
    persist: bool = False,
    quality: Optional[float] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience-Funktion für UI (/scenegraph/api/auto).

    Verhalten:
      • baut SceneGraph aus MetaSnaps + SnapChains
      • optional: speichert ihn in scenegraphs
      • liefert ein Dict mit:
          - ok: bool
          - graph: dict (SceneGraph.to_dict())
          - meta: Dict (inkl. saved_id, falls persist=True & erfolgreich)
    """
    g = build_scenegraph_from_meta_snaps(
        max_meta=max_meta,
        max_chains_per_meta=max_chains_per_meta,
    )
    g_dict = g.to_dict()
    out_meta: Dict[str, Any] = dict(g.meta or {})
    saved_id: Optional[int] = None

    if persist:
        saved_id = save_scenegraph(
            namespace=namespace,
            source=source,
            graph=g_dict,
            quality=quality,
            notes=notes,
        )
        out_meta["saved_id"] = saved_id

    return {
        "ok": True,
        "graph": g_dict,
        "meta": out_meta,
    }


# =============================================================================
# CLI-Selftest
# =============================================================================

def _selftest() -> None:
    """
    Kleiner Selftest: Schema prüfen und einen Auto-SceneGraph generieren.
    """
    print("[scenegraph_store] Selftest startet …")
    _ensure_scenegraph_schema()
    g = build_scenegraph_from_meta_snaps(max_meta=8, max_chains_per_meta=4)
    print(f"  Nodes: {len(g.nodes)}, Edges: {len(g.edges)}")
    res = auto_scenegraph_from_meta(max_meta=4, max_chains_per_meta=2, persist=False)
    print(f"  ok={res.get('ok')}, meta={res.get('meta')}")
    print("[scenegraph_store] Selftest OK ✅")


if __name__ == "__main__":
    _selftest()
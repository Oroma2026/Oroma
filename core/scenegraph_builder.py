#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/objectgraph_builder.py
# Projekt:   ORÓMA (Offline-First · Headless · Graph Abstraction)
# Modul:     ObjectGraphBuilder – Aggregation: SceneGraphs → ObjectGraph (Objekt-Persistenz-Layer als SceneGraph-Variante) + optionales Speichern
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ergänzt ORÓMA um eine zweite, „persistente“ Abstraktionsebene:
#
#   SceneGraphs (Moment-/Episode-Sichten)  →  ObjectGraph (objektzentrierte Aggregation)
#
# Wichtig: In diesem Code-Stand ist der „ObjectGraph“ technisch weiterhin ein
# SceneGraph-Objekt (aus core.scenegraph_store), aber mit:
#   - Node.kind="object"
#   - aggregierten Kanten (co-occur/derived) über mehrere SceneGraphs hinweg
#
# Ziel:
# - Aus vielen kurzlebigen SceneGraphs werden stabile Objekt-Knoten + Häufigkeiten
#   / Gewichte abgeleitet, ohne dass ein schweres Tracking-System erforderlich ist.
#
# ABHÄNGIGKEITEN / INTEGRATION
# ────────────────────────────
# - core.sql_manager:
#     • wird genutzt, um SceneGraphs aus Tabelle `scenegraphs` zu laden
# - core.scenegraph_store (importiert als sg):
#     • stellt SceneNode/SceneEdge/SceneGraph bereit
#     • stellt save_scenegraph() bereit (Persistenz des erzeugten ObjectGraphs)
#
# Dieses Modul ist headless und nutzt keine externen Graph-Libs.
#
# QUELLDATEN (INPUT)
# ──────────────────
# Geladen werden die letzten N SceneGraphs aus SQLite:
#   SELECT id, ts, namespace, source, quality, graph_json
#   FROM scenegraphs
#   WHERE namespace LIKE "<source_namespace_prefix>%"
#   [optional quality Filter]
#   ORDER BY ts DESC, id DESC
#   LIMIT <max_graphs>
#
# Parameter:
#   source_namespace_prefix: default "scene:auto_meta:"
#   max_graphs: default 32
#   min_quality: default 0.0 (Graphen ohne quality werden trotzdem akzeptiert)
#
# KANONISCHE OBJEKT-ID (KERNHEURISTIK)
# ───────────────────────────────────
# _canonical_object_id(node: sg.SceneNode) erzeugt pro SceneNode eine Objekt-ID.
# Strategie (v1) im aktuellen Code:
#   1) Wenn node.meta["object_id"] existiert:
#        → "obj:{object_id}"
#   2) sonst: Ableitung über kind/label/id als fallback (deterministisch)
#
# Dadurch können SceneGraph-Nodes, die in unterschiedlichen Graphen auftreten,
# auf ein gemeinsames Objekt projiziert werden.
#
# AGGREGATIONSMODELL
# ──────────────────
# Aggregation erfolgt über zwei interne Dataclasses:
#   - _AggNode: sammelt Count/Score/Labels/Meta je Objekt-ID
#   - _AggEdge: sammelt Count/Weight je (src_obj, tgt_obj, kind/label)
#
# Ergebnisbildung:
# - Für jedes aggregierte Objekt entsteht ein Node:
#     kind="object"
#     score ~ aggregiert (z. B. Mean/Max; im Code als float geführt)
#     meta enthält Stats wie:
#       {"n": <count>, "labels": [...], "kinds":[...], "sources":[graph_ids...]}
#
# - Für jede aggregierte Beziehung entsteht eine Edge:
#     kind: "derived" / "co_occur" (gemäß Projektion)
#     weight: aggregierte Häufigkeit / Gewicht
#
# OUTPUT (SceneGraph-Objekt als ObjectGraph)
# ─────────────────────────────────────────
# build_objectgraph_from_scenegraphs(...) -> sg.SceneGraph
# - erzeugt ein sg.SceneGraph Objekt
# - meta enthält Stats, u. a.:
#     • input_graphs: Anzahl geladener SceneGraphs
#     • objects: Anzahl Object-Nodes
#     • edges: Anzahl Object-Edges
#     • source_namespace_prefix, min_quality, max_graphs
#
# PERSISTENZ (OPTIONAL, CONVENIENCE)
# ──────────────────────────────────
# auto_objectgraph_from_scenegraphs(...)
# - ruft build_objectgraph_from_scenegraphs()
# - kann optional speichern:
#     scenegraph_store.save_scenegraph(
#       ts=now,
#       namespace=<target_namespace>,
#       source="object:auto",
#       graph=g.to_dict(),
#       quality=<quality>,
#       notes=<notes>
#     )
# - Rückgabe (Dict):
#     {
#       "ok": True,
#       "graph": <SceneGraph.to_dict()>,
#       "meta": {
#          "saved_id": <int|None>,
#          "target_namespace": "...",
#          "stats": {...}
#       }
#     }
#
# ENV / DEFAULTS (DIESE DATEI)
# ───────────────────────────
# Für CLI/Defaults werden ENV-Variablen gelesen:
#   OROMA_OBJECTGRAPH_SRC_NS         (Default: "scene:auto_meta:")
#   OROMA_OBJECTGRAPH_TARGET_NS      (Default: "object:auto:vision")
#   OROMA_OBJECTGRAPH_MAX_GRAPHS     (Default: 32)
#   OROMA_OBJECTGRAPH_MIN_QUALITY    (Default: 0.0)
#
# Diese ENV-Schalter sind bewusst „tuning knobs“ für Orchestrator/Dream-Jobs.
#
# CLI (PRODUKTIONSNAH, HEADLESS)
# ─────────────────────────────
# python3 /opt/ai/oroma/core/objectgraph_builder.py --help
#
# Typische Nutzung:
#   python3 /opt/ai/oroma/core/objectgraph_builder.py \
#     --src-ns-prefix "scene:auto_meta:" \
#     --target-namespace "object:auto:vision" \
#     --max-graphs 32 \
#     --min-quality 0.0
#
# Flags:
#   --no-persist  (nur bauen, nicht speichern)
#   --verbose     (DEBUG Logging)
#
# ROBUSTHEIT
# ──────────
# - DB OperationalError beim Laden → leere Liste, Builder liefert leeren Graph mit meta stats.
# - graph_json parse errors → betreffender Graph wird übersprungen (Warnung).
# - fehlende Nodes für Edges → Edge wird über Dummy-Nodes projiziert (fallback), sofern möglich.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Der ObjectGraph bleibt ein SceneGraph-Format (kompatibel zu UI/Store).
# - Projektion über _canonical_object_id muss deterministisch bleiben (sonst „flackert“ Aggregation).
# - Persistenz ist optional (persist=False darf keinen Seiteneffekt haben).
# - Keine destruktiven DB-Aktionen in diesem Modul (nur lesen + optional save via scenegraph_store).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

# Sicherstellen, dass /opt/ai/oroma im Pfad ist (analog zu snake_trainer)
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

from core import sql_manager
from core import scenegraph_store
try:
    from core import db_writer_client as db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

logger = logging.getLogger("oroma.scenegraph_builder")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("[scenegraph_builder] %(levelname)s: %(message)s")
    )
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


@dataclass
class ChainInfo:
    """
    Kleine Hilfestruktur zur Repräsentation einer SnapChain für das Grouping.
    """

    id: int
    ts: int
    quality: float
    origin: Optional[str]
    status: Optional[str]


def _fetch_chains_for_origin(
    *,
    origin: str,
    max_chains: int,
    min_quality: Optional[float] = None,
    since_ts: Optional[int] = None,
    verbose: bool = False,
) -> List[ChainInfo]:
    """
    Holt die jüngsten SnapChains für ein gegebenes origin.

    Es wird nach id DESC sortiert und dann auf max_chains begrenzt; anschließend
    wird die Liste in aufsteigender Reihenfolge (älteste zuerst) zurückgegeben.
    """
    conn = sql_manager.get_conn()
    conn.row_factory = sql_manager.sqlite3.Row  # type: ignore[attr-defined]
    cur = conn.cursor()

    params: List[Any] = [origin]
    where = ["origin = ?"]

    # Status-Filter: nur aktive Chains
    where.append("status = 'active'")

    if since_ts is not None and since_ts > 0:
        where.append("ts >= ?")
        params.append(int(since_ts))

    if min_quality is not None:
        where.append("quality >= ?")
        params.append(float(min_quality))

    where_clause = " AND ".join(where)
    sql = (
        "SELECT id, ts, quality, origin, status "
        "FROM snapchains "
        f"WHERE {where_clause} "
        "ORDER BY id DESC "
        "LIMIT ?"
    )
    params.append(int(max_chains))

    if verbose:
        logger.info(
            "Hole SnapChains: origin=%s max_chains=%s since_ts=%s min_quality=%s",
            origin,
            max_chains,
            since_ts,
            min_quality,
        )

    rows = cur.execute(sql, params).fetchall() or []
    conn.close()

    chains: List[ChainInfo] = []
    for r in rows:
        try:
            cid = int(r["id"])
            ts = int(r["ts"])
            q = float(r["quality"])
            o = r["origin"]
            st = r["status"]
            chains.append(ChainInfo(id=cid, ts=ts, quality=q, origin=o, status=st))
        except Exception as exc:  # pragma: no cover
            logger.warning("Fehler beim Lesen einer SnapChain-Zeile: %s", exc)

    # Älteste zuerst (für zeitliche Gruppenbildung)
    chains.sort(key=lambda c: c.id)
    if verbose:
        logger.info("Gefundene Chains für origin=%s: %d", origin, len(chains))
    return chains


def _group_chains(
    chains: Sequence[ChainInfo],
    group_size: int,
) -> List[List[ChainInfo]]:
    """
    Gruppiert eine Sequenz von ChainInfo-Objekten in Blöcke der Größe group_size.
    """
    if group_size <= 0:
        raise ValueError("group_size muss > 0 sein")
    groups: List[List[ChainInfo]] = []
    current: List[ChainInfo] = []
    for ch in chains:
        current.append(ch)
        if len(current) >= group_size:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _label_for_group(
    *,
    origin: str,
    avg_quality: float,
) -> str:
    """
    Erzeugt ein Label für einen MetaSnap, basierend auf origin + Qualitätslevel.

    Für vision/token hat sich aus der realen Qualitätsverteilung bewährt:
      - QUALITY_HOCH_THRESHOLD = 0.05

    Alles mit avg_quality >= 0.05 wird als „hoch“ markiert,
    alles darunter als „niedrig“. So werden wirklich nur auffällige
    Gruppen als „hoch“ gelabelt.
    """
    origin_clean = (origin or "unknown").replace("/", "_").replace(":", "_")

    # Empfehlung für vision/token:
    #   typische Qualität ~0.00–0.22, median ~0.01, p90 ~0.037
    #   → 0.05 trennt „auffällig gut“ vom normalen Rauschen.
    QUALITY_HOCH_THRESHOLD = 0.035

    if avg_quality >= QUALITY_HOCH_THRESHOLD:
        level = "hoch"
    else:
        level = "niedrig"

    return f"scenegraph:{origin_clean}:{level}"

def build_meta_snaps_from_tokens(
    *,
    origin: str = "vision/token",
    max_chains: int = 256,
    group_size: int = 32,
    min_quality: Optional[float] = None,
    since_ts: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Erzeugt MetaSnaps aus SnapChains (Vision-Tokens o.ä.).

    Die erzeugten MetaSnaps sind kompatibel zu scenegraph_store.build_scenegraph_from_meta_snaps(),
    da 'sources' eine JSON-Liste von Chain-Referenzen enthält:

        ["chain:123", "chain:124", "episode:130", ...]

    Parameter
    ---------
    origin : str
        SnapChain-Origin (Default: "vision/token").
    max_chains : int
        Max. Anzahl SnapChains, die betrachtet werden.
    group_size : int
        Gruppengröße (Chains pro MetaSnap).
    min_quality : float|None
        Optionaler Qualitätsfilter (mindestens dieser Wert).
    since_ts : int|None
        Optionaler Mindest-Timestamp (Unixzeit).
    dry_run : bool
        Wenn True, werden keine MetaSnaps geschrieben (nur Simulation).
    verbose : bool
        Wenn True, zusätzliche Log-Ausgaben.

    Rückgabewert
    ------------
    dict mit u.a.:
        {
          "ok": True/False,
          "origin": str,
          "num_chains": int,
          "num_groups": int,
          "num_meta_created": int,
          "meta_ids": [..],
        }
    """
    chains = _fetch_chains_for_origin(
        origin=origin,
        max_chains=max_chains,
        min_quality=min_quality,
        since_ts=since_ts,
        verbose=verbose,
    )
    if not chains:
        msg = "Keine SnapChains für Origin gefunden."
        logger.warning(msg)
        return {
            "ok": False,
            "reason": msg,
            "origin": origin,
            "num_chains": 0,
            "num_groups": 0,
            "num_meta_created": 0,
            "meta_ids": [],
        }

    groups = _group_chains(chains, group_size=group_size)
    if verbose:
        logger.info(
            "Gruppierung: %d Chains → %d Gruppen (group_size=%d)",
            len(chains),
            len(groups),
            group_size,
        )

    meta_ids: List[int] = []
    now = int(time.time())

    if dry_run:
        # Nur Statistik berechnen und zurückgeben
        for idx, grp in enumerate(groups, start=1):
            qualities = [g.quality for g in grp]
            avg_q = sum(qualities) / max(1, len(qualities))
            logger.info(
                "[dry-run] Gruppe %d: chains=%d, id-span=[%d..%d], avg_quality=%.3f",
                idx,
                len(grp),
                grp[0].id,
                grp[-1].id,
                avg_q,
            )
        return {
            "ok": True,
            "origin": origin,
            "num_chains": len(chains),
            "num_groups": len(groups),
            "num_meta_created": 0,
            "meta_ids": [],
            "dry_run": True,
        }

    # Jetzt echte Inserts in meta_snaps
    use_dbw = bool(
        db_writer_client is not None
        and os.environ.get("OROMA_DBW_ENABLE", "0").strip().lower() not in ("0", "false", "no", "off")
    )
    conn = None
    try:
        if use_dbw:
            timeout_ms = int(getattr(sql_manager, "_dbw_timeout_ms", lambda kind='dream': 60000)("dream"))
            for idx, grp in enumerate(groups, start=1):
                qualities = [g.quality for g in grp]
                avg_q = sum(qualities) / max(1, len(qualities))
                label = _label_for_group(origin=origin, avg_quality=avg_q)
                score = float(avg_q)
                sources_list = [f"chain:{g.id}" for g in grp]
                sources_txt = json.dumps(sources_list, ensure_ascii=False, separators=(",", ":"))

                if verbose:
                    logger.info(
                        "MetaSnap #%d: label=%s chains=%d avg_q=%.3f ids=[%d..%d]",
                        idx, label, len(grp), avg_q, grp[0].id, grp[-1].id
                    )

                mid = int(db_writer_client.exec_lastrowid(
                    "INSERT INTO meta_snaps (label, score, sources) VALUES (?,?,?)",
                    [label, score, sources_txt],
                    tag="scenegraph_builder.meta_snap",
                    priority="low",
                    timeout_ms=timeout_ms,
                    db="oroma",
                ) or 0)
                meta_ids.append(mid)
        else:
            conn = sql_manager.get_conn()
            cur = conn.cursor()
            for idx, grp in enumerate(groups, start=1):
                qualities = [g.quality for g in grp]
                avg_q = sum(qualities) / max(1, len(qualities))
                label = _label_for_group(origin=origin, avg_quality=avg_q)
                score = float(avg_q)

                sources_list = [f"chain:{g.id}" for g in grp]
                sources_txt = json.dumps(
                    sources_list, ensure_ascii=False, separators=(",", ":")
                )

                if verbose:
                    logger.info(
                        "MetaSnap #%d: label=%s chains=%d avg_q=%.3f ids=[%d..%d]",
                        idx,
                        label,
                        len(grp),
                        avg_q,
                        grp[0].id,
                        grp[-1].id,
                    )

                cur.execute(
                    "INSERT INTO meta_snaps (label, score, sources) VALUES (?,?,?)",
                    (label, score, sources_txt),
                )
                mid = int(cur.lastrowid)
                meta_ids.append(mid)

            conn.commit()
    except Exception as exc:
        logger.error("Fehler beim Schreiben in meta_snaps: %s", exc)
        if conn is not None:
            try:
                conn.rollback()
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.scenegraph_builder.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        return {
            "ok": False,
            "reason": str(exc),
            "origin": origin,
            "num_chains": len(chains),
            "num_groups": len(groups),
            "num_meta_created": 0,
            "meta_ids": [],
        }
    finally:
        if conn is not None:
            conn.close()

    return {
        "ok": True,
        "origin": origin,
        "num_chains": len(chains),
        "num_groups": len(groups),
        "num_meta_created": len(meta_ids),
        "meta_ids": meta_ids,
        "ts": now,
    }


def bootstrap_scenegraph_from_tokens(
    *,
    origin: str = "vision/token",
    max_chains: int = 256,
    group_size: int = 32,
    min_quality: Optional[float] = None,
    since_ts: Optional[int] = None,
    max_meta: int = 32,
    max_chains_per_meta: int = 16,
    namespace: Optional[str] = None,
    persist: bool = True,
    notes: Optional[str] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Komfort-Funktion: baut MetaSnaps aus Tokens und erzeugt danach einen SceneGraph.

    Parameter (Auszug)
    ------------------
    origin : str
        SnapChain-Origin (Standard: "vision/token").
    max_chains, group_size, min_quality, since_ts
        Werden an build_meta_snaps_from_tokens() durchgereicht.
    max_meta : int
        max_meta für scenegraph_store.auto_scenegraph_from_meta().
    max_chains_per_meta : int
        max_chains_per_meta für auto_scenegraph_from_meta().
    namespace : str|None
        Namespace für scenegraphs.namespace; Default:
           "scene:auto_meta:<origin_clean>"
    persist : bool
        Ob der SceneGraph in scenegraphs persistiert werden soll.
    notes : str|None
        Optionaler Hinweis-Text für scenegraphs.notes.
    verbose : bool
        Zusätzliche Logs.

    Rückgabewert
    ------------
    dict mit u.a.:
        {
          "ok": True/False,
          "meta_snaps_result": {...},
          "scenegraph_result": {...} oder None,
        }
    """
    if verbose:
        logger.info("bootstrap_scenegraph_from_tokens: starte MetaSnap-Build …")

    meta_res = build_meta_snaps_from_tokens(
        origin=origin,
        max_chains=max_chains,
        group_size=group_size,
        min_quality=min_quality,
        since_ts=since_ts,
        dry_run=False,
        verbose=verbose,
    )

    if not meta_res.get("ok"):
        logger.warning(
            "MetaSnap-Build fehlgeschlagen oder leer: %s", meta_res.get("reason")
        )
        return {
            "ok": False,
            "meta_snaps_result": meta_res,
            "scenegraph_result": None,
        }

    origin_clean = (origin or "unknown").replace("/", "_").replace(":", "_")
    if namespace is None:
        namespace = f"scene:auto_meta:{origin_clean}"

    if notes is None:
        notes = (
            f"Auto-SceneGraph aus origin={origin}, "
            f"max_chains={max_chains}, group_size={group_size}"
        )

    if verbose:
        logger.info(
            "Rufe scenegraph_store.auto_scenegraph_from_meta() auf "
            "(namespace=%s, max_meta=%d, max_chains_per_meta=%d, persist=%s)",
            namespace,
            max_meta,
            max_chains_per_meta,
            persist,
        )

    sg_res = scenegraph_store.auto_scenegraph_from_meta(
        namespace=namespace,
        source="builder:vision_tokens",
        max_meta=max_meta,
        max_chains_per_meta=max_chains_per_meta,
        persist=persist,
        quality=None,
        notes=notes,
    )

    return {
        "ok": bool(sg_res.get("ok")),
        "meta_snaps_result": meta_res,
        "scenegraph_result": sg_res,
    }


def _env_int(name: str, default: int) -> int:
    """
    Liest eine Ganzzahl aus einer Umgebungsvariable, mit Fallback auf default.
    """
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v.strip())
    except Exception:
        return default


def main() -> None:
    """
    CLI-Einstiegspunkt für:
      - MetaSnaps aus Tokens erzeugen
      - optional: SceneGraph generieren/persistieren
    """
    ap = argparse.ArgumentParser(
        description="ORÓMA SceneGraph Builder (Vision-Tokens → MetaSnaps → SceneGraphs)"
    )

    default_origin = os.environ.get("OROMA_SCENEGRAPH_ORIGIN", "vision/token")
    default_max_chains = _env_int("OROMA_SCENEGRAPH_MAX_CHAINS", 256)
    default_group_size = _env_int("OROMA_SCENEGRAPH_GROUP_SIZE", 32)

    ap.add_argument(
        "--origin",
        default=default_origin,
        help=f"SnapChain-Origin (Default: {default_origin})",
    )
    ap.add_argument(
        "--max-chains",
        type=int,
        default=default_max_chains,
        help=f"Max. Anzahl Chains (Default: {default_max_chains})",
    )
    ap.add_argument(
        "--group-size",
        type=int,
        default=default_group_size,
        help=f"Gruppengröße pro MetaSnap (Default: {default_group_size})",
    )
    ap.add_argument(
        "--min-quality",
        type=float,
        default=None,
        help="Optionaler Mindest-Qualitätswert (z.B. 0.3)",
    )
    ap.add_argument(
        "--since-ts",
        type=int,
        default=None,
        help="Optionaler Mindest-Timestamp (Unixzeit) für Chains",
    )
    ap.add_argument(
        "--build-graph",
        action="store_true",
        help="Nach MetaSnap-Build automatisch SceneGraph erzeugen",
    )
    ap.add_argument(
        "--max-meta",
        type=int,
        default=32,
        help="Max. MetaSnaps für SceneGraph (Default: 32)",
    )
    ap.add_argument(
        "--max-chains-per-meta",
        type=int,
        default=16,
        help="Max. Chains pro MetaSnap für SceneGraph (Default: 16)",
    )
    ap.add_argument(
        "--namespace",
        type=str,
        default=None,
        help="Namespace für SceneGraphs (Default: scene:auto_meta:<origin_clean>)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur anzeigen, nichts in die DB schreiben",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Ausführliche Log-Ausgaben aktivieren",
    )

    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.dry_run and args.build_graph:
        logger.warning(
            "Kombination --dry-run und --build-graph ist widersprüchlich. "
            "SceneGraph wird im Dry-Run NICHT erzeugt."
        )
        args.build_graph = False

    if args.build_graph:
        res = bootstrap_scenegraph_from_tokens(
            origin=args.origin,
            max_chains=args.max_chains,
            group_size=args.group_size,
            min_quality=args.min_quality,
            since_ts=args.since_ts,
            max_meta=args.max_meta,
            max_chains_per_meta=args.max_chains_per_meta,
            namespace=args.namespace,
            persist=True,
            notes=None,
            verbose=args.verbose,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        res = build_meta_snaps_from_tokens(
            origin=args.origin,
            max_chains=args.max_chains,
            group_size=args.group_size,
            min_quality=args.min_quality,
            since_ts=args.since_ts,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
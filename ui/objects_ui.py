#!/usr/bin/env python3
# ==============================================================================
# Datei:    ui/objects_ui.py
# Projekt:  ORÓMA v3.7.3 – ObjectGraph UI
# Stand:    2025-12-13
# Autor:    ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# Version:  v0.8
#
# Zweck:
#   Flask-Blueprint für die ObjectGraph-Übersicht (/objects)
#   - Zeigt eine Stichprobe aus object_nodes / object_relations
#   - Bietet Filter (kind, focus_id)
#   - Integriert Health-Status aus objectgraph_selfcheck
#
#   Ab v0.6:
#     - kind-Filter erfolgt auf DB-Ebene (WHERE kind = ?)
#
#   Ab v0.7:
#     - Top-Labels werden aus dem aktuellen View (nodes) berechnet
#     - nodes_by_id wird um alle in der Relationen-Stichprobe referenzierten
#       Knoten erweitert (id IN (...)), damit weniger "(unbekannt)" angezeigt
#       wird und die Degree-Statistik realer wird.
#
#   Ab v0.8:
#     - Bei gesetzter focus_id wird ein kleines "Ego-Net" für den Fokus-Knoten
#       berechnet:
#         • degree / Anzahl Relationstypen für focus_id
#         • Liste der direkt benachbarten Knoten im aktuellen Sample
#     - Diese Infos werden an das Template übergeben (focus_degree_info)
# ==============================================================================

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from flask import Blueprint, render_template, request  # jsonify aktuell nicht genutzt

from core import sql_manager

bp = Blueprint("objects", __name__, template_folder="templates")

log = logging.getLogger("oroma.ui.objects")

# Kurzer Cache für den Health-Status (um nicht bei jedem Request den
# Selfcheck neu zu starten).
_OBJ_HEALTH_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_OBJ_HEALTH_TTL_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# Hilfsfunktionen – Pfade & Health
# ---------------------------------------------------------------------------

def _resolve_db_path() -> str:
    """Ermittle den Pfad zur oroma.db.

    Bevorzugt wird die Umgebungsvariable OROMA_DB_PATH. Falls diese nicht
    gesetzt ist, wird aus OROMA_BASE (oder dem Standardpfad /opt/ai/oroma)
    der Default-Pfad `data/oroma.db` abgeleitet.
    """
    db_path = os.environ.get("OROMA_DB_PATH")
    if db_path:
        return db_path

    base = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
    return os.path.join(base, "data", "oroma.db")


def _resolve_selfcheck_script() -> str:
    """Pfad zum ObjectGraph-Selfcheck-Skript ermitteln."""
    base = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
    return os.path.join(base, "tools", "objectgraph_selfcheck.py")


def _normalize_health(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normiere Health-Informationen auf ein einheitliches Schema."""
    health = (payload or {}).get("health") or {}
    overall_status = health.get("overall_status")
    warnings = list(health.get("warnings") or [])
    errors = list(health.get("errors") or [])

    if not overall_status:
        # Alte Selfcheck-Version: grobe Einschätzung über Integritätsdaten
        rel = (payload or {}).get("object_relations") or {}
        integrity = rel.get("integrity") or {}
        missing_a = integrity.get("missing_a") or 0
        missing_b = integrity.get("missing_b") or 0

        if missing_a or missing_b:
            overall_status = "warning"
            warnings.append(
                "FK-Integrität unvollständig (Selfcheck < 1.5, "
                "abgeleitete Bewertung)."
            )
        else:
            overall_status = "ok"

    return {
        "overall_status": overall_status,
        "warnings": warnings,
        "errors": errors,
    }


def _get_objectgraph_health() -> Dict[str, Any]:
    """Führe den ObjectGraph-Selfcheck aus und cache das Ergebnis kurz."""
    now = time.time()
    cached_ts = _OBJ_HEALTH_CACHE.get("ts", 0.0)
    cached_data = _OBJ_HEALTH_CACHE.get("data")

    if cached_data is not None and (now - cached_ts) < _OBJ_HEALTH_TTL_SECONDS:
        return cached_data  # type: ignore[return-value]

    db_path = _resolve_db_path()
    script_path = _resolve_selfcheck_script()

    cmd = [
        sys.executable,
        script_path,
        "--db-path",
        db_path,
        "--namespace-prefix",
        "object:auto:",
        "--json-only",
    ]

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30.0,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("ObjectGraphSelfcheck: Aufruf fehlgeschlagen: %s", exc)
        data: Dict[str, Any] = {
            "health": {
                "overall_status": "unknown",
                "warnings": ["Selfcheck-Aufruf fehlgeschlagen"],
                "errors": [],
            }
        }
        _OBJ_HEALTH_CACHE["ts"] = now
        _OBJ_HEALTH_CACHE["data"] = data
        return data

    if proc.returncode != 0:
        log.warning(
            "ObjectGraphSelfcheck: Rueckgabecode %s, stderr=%s",
            proc.returncode,
            (proc.stderr or "").strip()[:500],
        )
        data = {
            "health": {
                "overall_status": "warning",
                "warnings": [f"Selfcheck-Exit-Code {proc.returncode}"],
                "errors": [],
            }
        }
        _OBJ_HEALTH_CACHE["ts"] = now
        _OBJ_HEALTH_CACHE["data"] = data
        return data

    stdout = proc.stdout or "{}"
    try:
        payload: Dict[str, Any] = json.loads(stdout)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "ObjectGraphSelfcheck: JSON-Parsing-Fehler: %s, raw=%r",
            exc,
            stdout[:500],
        )
        data = {
            "health": {
                "overall_status": "unknown",
                "warnings": ["Selfcheck-JSON konnte nicht geparst werden"],
                "errors": [],
            }
        }
        _OBJ_HEALTH_CACHE["ts"] = now
        _OBJ_HEALTH_CACHE["data"] = data
        return data

    payload["health"] = _normalize_health(payload)

    _OBJ_HEALTH_CACHE["ts"] = now
    _OBJ_HEALTH_CACHE["data"] = payload
    return payload


# ---------------------------------------------------------------------------
# Datenstrukturen – Fokus-Ego-Netz
# ---------------------------------------------------------------------------

@dataclass
class FocusDegreeInfo:
    """Kleine Zusammenfassung für einen Fokus-Knoten im aktuellen Sample.

    Wird berechnet, wenn eine focus_id gesetzt ist und im Mapping nodes_by_id
    vorkommt. Dient vor allem der Visualisierung im UI.
    """

    node_id: int
    degree: int
    relation_types: int
    neighbors: List[Dict[str, Any]]


# ==============================================================================
# Routes
# ==============================================================================


@bp.route("/objects")
@bp.route("/objects/")
def objects_index():
    """ObjectGraph-Übersicht."""
    filter_kind: Optional[str] = request.args.get("kind") or None
    focus_id_raw: Optional[str] = request.args.get("focus_id")

    focus_id: Optional[int] = None
    if focus_id_raw:
        try:
            focus_id = int(focus_id_raw)
        except ValueError:
            focus_id = None

    # Stichprobengröße für UI-Ansicht und Statistiken
    SAMPLE_LIMIT = 500

    with sql_manager.get_conn() as conn:
        conn.row_factory = sqlite3.Row

        # Ungefilterte Nodes-Stichprobe (für initiales Mapping)
        cur_nodes_all = conn.execute(
            """
            SELECT id, kind, label, meta_json
              FROM object_nodes
             ORDER BY id DESC
             LIMIT ?
            """,
            (SAMPLE_LIMIT,),
        )
        nodes_sample_all = [dict(r) for r in cur_nodes_all.fetchall()]

        # Gefilterte Nodes-View + Count (abhängig von filter_kind)
        if filter_kind:
            cur_nodes = conn.execute(
                """
                SELECT id, kind, label, meta_json
                  FROM object_nodes
                 WHERE kind = ?
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (filter_kind, SAMPLE_LIMIT),
            )
            nodes_view = [dict(r) for r in cur_nodes.fetchall()]
            total_nodes_row = conn.execute(
                "SELECT COUNT(*) AS c FROM object_nodes WHERE kind = ?",
                (filter_kind,),
            ).fetchone()
        else:
            nodes_view = list(nodes_sample_all)
            total_nodes_row = conn.execute(
                "SELECT COUNT(*) AS c FROM object_nodes"
            ).fetchone()

        total_nodes = int(total_nodes_row["c"] if total_nodes_row else 0)

        # Relations-Stichprobe + Count (global)
        cur_rel = conn.execute(
            """
            SELECT id, a_id, b_id, relation, confidence
              FROM object_relations
             ORDER BY id DESC
             LIMIT ?
            """,
            (SAMPLE_LIMIT,),
        )
        relations = [dict(r) for r in cur_rel.fetchall()]

        total_relations_row = conn.execute(
            "SELECT COUNT(*) AS c FROM object_relations"
        ).fetchone()
        total_relations = int(total_relations_row["c"] if total_relations_row else 0)

        # ------------------------------------------------------------------
        # Mapping id -> Node: zunächst aus nodes_sample_all
        # ------------------------------------------------------------------
        nodes_by_id: Dict[int, Dict[str, Any]] = {
            int(n["id"]): n for n in nodes_sample_all if "id" in n
        }

        # IDs sammeln, die in Relationen vorkommen und noch fehlen
        missing_ids: Set[int] = set()
        for r in relations:
            for key in ("a_id", "b_id"):
                node_id = r.get(key)
                if isinstance(node_id, int) and node_id not in nodes_by_id:
                    missing_ids.add(node_id)

        # Fehlen weitere Nodes? Dann gezielt nachladen (id IN (...)).
        # Wegen SQLite-Parametergrenze chunked wir im Worst-Case.
        if missing_ids:
            missing_ids_list = sorted(missing_ids)
            CHUNK_SIZE = 800  # etwas Reserve unterhalb 999-Param-Boundary

            for i in range(0, len(missing_ids_list), CHUNK_SIZE):
                chunk = missing_ids_list[i : i + CHUNK_SIZE]
                placeholders = ",".join("?" for _ in chunk)
                sql = (
                    "SELECT id, kind, label, meta_json "
                    "FROM object_nodes WHERE id IN (" + placeholders + ")"
                )
                cur = conn.execute(sql, chunk)
                for row in cur.fetchall():
                    d = dict(row)
                    nodes_by_id[int(d["id"])] = d

    # View-Liste für die Tabellenansicht (bereits gefiltert)
    nodes = nodes_view

    # Verteilung nach kind im aktuellen View
    kinds_counter: Counter = Counter(n.get("kind") for n in nodes)
    kinds_summary = sorted(
        ((k or "?", c) for k, c in kinds_counter.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )

    # Top-Labels basierend auf dem aktuellen View (nodes)
    label_counter: Counter = Counter(
        (n.get("label") or "").strip()
        for n in nodes
        if n.get("kind") == "object"
    )
    if "" in label_counter:
        del label_counter[""]

    top_object_labels = sorted(
        label_counter.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:20]

    # Verteilung der Relationen (Stichprobe)
    rel_counter: Counter = Counter(r.get("relation") for r in relations)
    relations_summary = sorted(
        ((k or "?", c) for k, c in rel_counter.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )

    # Degree-Statistik auf Basis der Relationen-Stichprobe
    degree_counter: Counter = Counter()
    reltypes_per_node: Dict[int, set] = {}

    for r in relations:
        rel_name = r.get("relation")
        a_id = r.get("a_id")
        b_id = r.get("b_id")

        for node_id in (a_id, b_id):
            if not isinstance(node_id, int):
                continue
            degree_counter[node_id] += 1
            if node_id not in reltypes_per_node:
                reltypes_per_node[node_id] = set()
            if rel_name:
                reltypes_per_node[node_id].add(rel_name)

    # Top-Objekte nach Degree (global, aus Stichprobe)
    top_objects_degree: List[Dict[str, Any]] = []
    for node_id, deg in degree_counter.items():
        node = nodes_by_id.get(node_id)
        if not node:
            continue
        if node.get("kind") != "object":
            continue
        top_objects_degree.append(
            {
                "id": node_id,
                "kind": node.get("kind"),
                "degree": int(deg),
                "rel_types": len(reltypes_per_node.get(node_id, set())),
                "label": (node.get("label") or "").strip(),
            }
        )

    top_objects_degree.sort(
        key=lambda item: (-item["degree"], item["label"])
    )

    min_degree_for_top = 2
    top_objects_degree = [
        item for item in top_objects_degree
        if item["degree"] >= min_degree_for_top
    ][:20]

    # -------------------------------------------------------------------------
    # Fokus-Ego-Netz (falls focus_id gesetzt und bekannt)
    # -------------------------------------------------------------------------
    focus_degree_info: Optional[FocusDegreeInfo] = None
    focus_relations: List[Dict[str, Any]] = []

    if focus_id is not None and focus_id in nodes_by_id:
        # Degree-Infos aus der globalen Degree-Statistik holen
        deg = int(degree_counter.get(focus_id, 0))
        rel_types = len(reltypes_per_node.get(focus_id, set()))

        # Alle Relationen aus der Stichprobe, in denen focus_id vorkommt
        for r in relations:
            if r.get("a_id") == focus_id or r.get("b_id") == focus_id:
                focus_relations.append(r)

        # Direkte Nachbarn sammeln
        neighbor_ids: Set[int] = set()
        for r in focus_relations:
            a_id = r.get("a_id")
            b_id = r.get("b_id")
            if a_id == focus_id and isinstance(b_id, int):
                neighbor_ids.add(b_id)
            elif b_id == focus_id and isinstance(a_id, int):
                neighbor_ids.add(a_id)

        neighbors: List[Dict[str, Any]] = []
        for nid in sorted(neighbor_ids):
            node = nodes_by_id.get(nid)
            if not node:
                continue
            neighbors.append(
                {
                    "id": nid,
                    "kind": node.get("kind"),
                    "label": (node.get("label") or "").strip(),
                }
            )

        focus_degree_info = FocusDegreeInfo(
            node_id=focus_id,
            degree=deg,
            relation_types=rel_types,
            neighbors=neighbors,
        )

    # Health-Status
    health_payload = _get_objectgraph_health()
    health = (health_payload or {}).get("health") or {}

    return render_template(
        "objects.html",
        # Listen
        nodes=nodes,
        nodes_by_id=nodes_by_id,
        relations=relations,
        # Filter/Fokus
        focus_id=focus_id,
        filter_kind=filter_kind or "",
        focus_degree_info=focus_degree_info,
        focus_relations=focus_relations,
        # Statistiken
        total_nodes=total_nodes,
        kinds_summary=kinds_summary,
        total_relations=total_relations,
        relations_summary=relations_summary,
        top_object_labels=top_object_labels,
        top_objects_degree=top_objects_degree,
        min_degree_for_top=min_degree_for_top,
        # Health
        health_status=health.get("overall_status"),
        health_warnings_count=len(health.get("warnings") or []),
        health_errors_count=len(health.get("errors") or []),
    )
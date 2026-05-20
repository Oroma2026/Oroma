#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/scenegraph_ui.py
# Projekt: ORÓMA – Headless UI
# Modul:   SceneGraph UI (Viewer für MetaSnap→SnapChain-Beziehungen)
# Version: v3.8-r2
# Stand:   2025-11-28
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Web-UI & API für SceneGraphs:
#
#     • HTML-Seite /scenegraph
#         - kompakte Übersicht über Nodes/Edges
#         - Auto-Graph aus MetaSnaps + SnapChains (2.5D-„Beziehungsraum“)
#
#     • API:
#         - GET /scenegraph/api/auto
#             → baut SceneGraph aus MetaSnaps (optional persist)
#         - GET /scenegraph/api/list
#             → listet gespeicherte SceneGraphs
#         - GET /scenegraph/api/get/<id>
#             → lädt einen gespeicherten SceneGraph + Graph-JSON
#
# Sicherheit
# ──────────
#   - Wenn ENV OROMA_UI_TOKEN gesetzt ist, müssen API-Requests den
#     Header X-OROMA-TOKEN mitsenden.
#   - Die HTML-Seite injiziert den Token in window.OROMA_UI_TOKEN
#     (via base.html).
# =============================================================================

from __future__ import annotations

import os
from typing import Any, Optional

from flask import Blueprint, jsonify, render_template, request

from core import scenegraph_store  # type: ignore

# WICHTIG: Name = bp, damit run_oroma.py → scenegraph_ui.bp findet
bp = Blueprint("scenegraph", __name__, template_folder="templates")


# -----------------------------------------------------------------------------
# Helper: Token-Check
# -----------------------------------------------------------------------------

def _check_token() -> Optional[Any]:
    """
    Einfache Token-Prüfung für API-Endpunkte.
    Wenn OROMA_UI_TOKEN nicht gesetzt ist → alles frei.
    """
    expected = os.environ.get("OROMA_UI_TOKEN") or ""
    if not expected:
        return None
    got = request.headers.get("X-OROMA-TOKEN") or ""
    if got == expected:
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


# -----------------------------------------------------------------------------
# HTML-Seite
# -----------------------------------------------------------------------------

@bp.route("/scenegraph")
def scenegraph_page():
    ui_token = os.environ.get("OROMA_UI_TOKEN") or ""
    # base.html erwartet ui_token im Template-Kontext
    return render_template("scenegraph.html", ui_token=ui_token)


# -----------------------------------------------------------------------------
# API: Auto-Graph aus MetaSnaps
# -----------------------------------------------------------------------------

@bp.route("/scenegraph/api/auto")
def scenegraph_auto_api():
    bad = _check_token()
    if bad is not None:
        return bad

    def _to_int(arg: str, default: int) -> int:
        try:
            return max(1, int(arg))
        except Exception:
            return default

    max_meta = _to_int(request.args.get("max_meta", "32"), 32)
    max_chains = _to_int(request.args.get("max_chains", "16"), 16)

    persist_flag = (request.args.get("persist", "0").lower() in ("1", "true", "yes"))
    namespace = request.args.get("namespace", "scene:auto_meta")
    notes = request.args.get("notes") or "Auto-SceneGraph aus MetaSnaps"

    res = scenegraph_store.auto_scenegraph_from_meta(
        namespace=namespace,
        source="ui:scenegraph_auto",
        max_meta=max_meta,
        max_chains_per_meta=max_chains,
        persist=persist_flag,
        quality=None,
        notes=notes,
    )
    return jsonify(res)


# -----------------------------------------------------------------------------
# API: Liste gespeicherter SceneGraphs
# -----------------------------------------------------------------------------

@bp.route("/scenegraph/api/list")
def scenegraph_list_api():
    bad = _check_token()
    if bad is not None:
        return bad

    def _to_int(arg: str, default: int) -> int:
        try:
            return max(1, int(arg))
        except Exception:
            return default

    limit = _to_int(request.args.get("limit", "50"), 50)
    namespace = request.args.get("namespace") or None

    items = scenegraph_store.list_scenegraphs(limit=limit, namespace=namespace)
    return jsonify({"ok": True, "items": items})


# -----------------------------------------------------------------------------
# API: Einzelner SceneGraph
# -----------------------------------------------------------------------------

@bp.route("/scenegraph/api/get/<int:graph_id>")
def scenegraph_get_api(graph_id: int):
    bad = _check_token()
    if bad is not None:
        return bad

    data = scenegraph_store.get_scenegraph(graph_id)
    if not data:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "graph": data})
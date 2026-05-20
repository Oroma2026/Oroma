#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/curriculum_ui.py
# Projekt: ORÓMA
# Version: v3.6
# Stand:   2025-09-27
#
# Zweck:
#   - Curriculum-Dashboard (Flask Blueprint)
#   - Zeigt aktuelle Stage & Progress
#   - API-Endpunkte für State + Advance
# =============================================================================

from flask import Blueprint, render_template, jsonify, request
from core import curriculum

bp = Blueprint("curriculum_ui", __name__, url_prefix="/curriculum")

# ----------------------------- UI-Seite -------------------------------------

@bp.route("/")
def page():
    return render_template("curriculum.html")

# ----------------------------- API ------------------------------------------

@bp.route("/api/state", methods=["GET"])
def api_state():
    curriculum.ensure_schema()
    st = curriculum.get_state()
    return jsonify({"ok": True, "state": st, "stage_name": curriculum.current_stage_name()})

@bp.route("/api/advance", methods=["POST"])
def api_advance():
    data = request.get_json(force=True)
    metrics = data.get("metrics", {})
    ok = curriculum.advance_if_ready(metrics)
    return jsonify({"ok": ok, "stage_name": curriculum.current_stage_name()})
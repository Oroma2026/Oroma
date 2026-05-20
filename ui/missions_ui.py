#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/missions_ui.py
# Projekt: ORÓMA
# Version: v3.6
# Stand:   2025-09-27
#
# Zweck:
#   - Missions-Dashboard (Flask Blueprint)
#   - Zeigt aktive/abgeschlossene Missionen
#   - API-Endpunkte für Erstellung, Update, Abschluss
#
# Nutzung:
#   - Route /missions
#   - API:
#       POST   /missions/api/new       {"name":..., "criteria": {...}, "goal": "..."}
#       GET    /missions/api/list      → Liste aller Missionen
#       POST   /missions/api/update    {"id":..., "progress": {...}}
#       POST   /missions/api/complete  {"id":...}
# =============================================================================

from flask import Blueprint, render_template, request, jsonify
import time
from core import missions

bp = Blueprint("missions_ui", __name__, url_prefix="/missions")

# ----------------------------- UI-Seite -------------------------------------

@bp.route("/")
def page():
    return render_template("missions.html")

# ----------------------------- API ------------------------------------------

@bp.route("/api/new", methods=["POST"])
def api_new():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    crit = data.get("criteria", {})
    goal = data.get("goal", "")
    if not name:
        return jsonify({"ok": False, "error": "Name fehlt"}), 400
    missions.ensure_schema()
    mid = missions.new_mission(name, crit, goal)
    return jsonify({"ok": True, "id": mid})

@bp.route("/api/list", methods=["GET"])
def api_list():
    missions.ensure_schema()
    items = missions.list_missions(active_only=False)
    return jsonify({"ok": True, "items": items})

@bp.route("/api/update", methods=["POST"])
def api_update():
    data = request.get_json(force=True)
    mid = int(data.get("id", 0))
    prog = data.get("progress", {})
    missions.update_progress(mid, prog)
    return jsonify({"ok": True})

@bp.route("/api/complete", methods=["POST"])
def api_complete():
    data = request.get_json(force=True)
    mid = int(data.get("id", 0))
    ok = missions.check_and_complete(mid)
    return jsonify({"ok": ok})
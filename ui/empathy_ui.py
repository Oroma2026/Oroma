#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/empathy_ui.py
# Projekt: ORÓMA
# Version: v3.5patch2 (final+sql_helpers)
# Stand:   2025-09-25
#
# Zweck:
#   - Flask-Blueprint für Empathie-System
#   - Deaktivierbar per ENV OROMA_ENABLE_EMPATHY
#   - API-Endpunkte:
#       GET  /empathy/api/state    → aktueller Zustand (falls Modul aktiv)
#       POST /empathy/api/event    → Ereignis anwenden (falls Modul aktiv)
#       GET  /empathy/api/history  → Verlauf (DB, immer verfügbar falls Tabelle existiert)
#   - UI-Route:
#       GET  /empathy              → Rendert empathy.html
# =============================================================================

from __future__ import annotations
import sys, os
from flask import Blueprint, jsonify, request, render_template

# ENV-Flag prüfen
_ENABLE_EMPATHY = os.environ.get("OROMA_ENABLE_EMPATHY", "false").lower() in ("1", "true", "yes")

try:
    from core import empathy
    _HAS_EMPATHY = True
except Exception:
    empathy = None
    _HAS_EMPATHY = False

try:
    from core import sql_manager
    _HAS_SQL = True
except Exception:
    sql_manager = None
    _HAS_SQL = False

bp = Blueprint("empathy_ui", __name__, url_prefix="/empathy")

# ----------------------------- UI Route --------------------------------------

@bp.route("/")
def page():
    """Render Empathy Dashboard (oder Disabled-Hinweis)."""
    return render_template("empathy.html", enabled=_ENABLE_EMPATHY and _HAS_EMPATHY)

# ----------------------------- API: State ------------------------------------

@bp.get("/api/state")
def api_state():
    if not (_ENABLE_EMPATHY and _HAS_EMPATHY):
        return jsonify({"ok": False, "error": "empathy disabled"})
    return jsonify({"ok": True, "state": empathy.STATE.as_dict()})

# ----------------------------- API: Event ------------------------------------

@bp.post("/api/event")
def api_event():
    if not (_ENABLE_EMPATHY and _HAS_EMPATHY):
        return jsonify({"ok": False, "error": "empathy disabled"})
    data = request.get_json(force=True, silent=True) or {}
    etype = data.get("type")
    if not etype:
        return jsonify({"ok": False, "error": "no type"})
    try:
        res = empathy.apply_event(str(etype), data)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ----------------------------- API: History ----------------------------------

@bp.get("/api/history")
def api_history():
    if not _HAS_SQL:
        return jsonify({"ok": False, "error": "sql_manager missing", "history": []})

    limit = int(request.args.get("limit", 20))
    try:
        rows = sql_manager.fetch_last_empathy(limit)
        hist = [
            {"ts": r["ts"], "mood": r["mood"], "score": r["score"]}
            for r in rows
        ]
        return jsonify({"ok": True, "history": hist})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "history": []})

# ----------------------------- Selftest --------------------------------------

if __name__ == "__main__":
    if not (_ENABLE_EMPATHY and _HAS_EMPATHY):
        print("[empathy_ui] Empathy deaktiviert oder Modul fehlt.")
    else:
        print("[empathy_ui] Starte Selftest ...")
        print("State:", empathy.STATE.as_dict())
        print("Event-Test (reward):", empathy.apply_event("reward", {}))
        print("Event-Test (gap):", empathy.apply_event("gap", {}))

    if _HAS_SQL:
        print("Letzte EmpathySnaps aus DB:", sql_manager.fetch_last_empathy(5))
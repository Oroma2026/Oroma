#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/selfrec_ui.py
# Projekt: ORÓMA v3.7
# Version: v1.0
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Kleine UI/API zum manuellen Starten der Selbsterkennungstests.
#
# Routen
# ──────
#   GET  /selfrec/             → einfache Seite (Template optional)
#   POST /selfrec/api/run      → {method: auto|led|picar} → Ergebnis-JSON
#   GET  /selfrec/api/quick    → quick run (auto)
# =============================================================================

from __future__ import annotations
from flask import Blueprint, jsonify, render_template, request
from core import self_recognition

bp = Blueprint("selfrec", __name__, url_prefix="/selfrec")

@bp.route("/")
def page():
    # Optional: baue später ein Template. Vorläufige Info:
    return render_template("selfrec.html") if False else jsonify(ok=True, msg="Use /selfrec/api/run {method:auto|led|picar}")

@bp.route("/api/quick")
def api_quick():
    res = self_recognition.run_auto(prefer="auto")
    return jsonify({"ok": True, "result": res})

@bp.route("/api/run", methods=["POST"])
def api_run():
    try:
        js = request.get_json(force=True) or {}
    except Exception:
        js = {}
    method = (js.get("method") or "auto").strip().lower()
    res = self_recognition.run_auto(prefer=method)
    return jsonify({"ok": True, "result": res})
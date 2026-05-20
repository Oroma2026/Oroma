#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/setcalc_ui.py
# Projekt: ORÓMA v3.7 (final) – SetCalc: Mengenlehre + Reward-Bridge
# Stand:   2025-09-29
#
# Zweck
# ─────
#  - Flask-Blueprint für Mengenoperationen + UI-Seite
#  - JSON-APIs:
#      POST /setcalc/api/union         {A, B}
#      POST /setcalc/api/intersection  {A, B}
#      POST /setcalc/api/difference    {A, B}
#      POST /setcalc/api/complement    {A, U}
#      POST /setcalc/api/powerset      {A}
#      POST /setcalc/api/cartesian     {A, B}
#      POST /setcalc/api/venn_counts   {A, B}
#
# Patch „Mengenleere“
# ───────────────────
#  - Leere Ergebnisse werden als "∅" zurückgegeben (UI/Anzeige)
#  - Logging bleibt kompatibel (DB speichert [])
#
# Reward-Bridge (v3.7)
# ────────────────────
#  - Erfolgreiche Operationen erzeugen ein Reward-Signal (source="setcalc", +0.05)
#  - Rewards sind sofort im Learning-Dashboard sichtbar
#  - Voll kompatibel zu v3.6 Patch-2c; keine funktionalen Änderungen notwendig
# =============================================================================

from __future__ import annotations
from flask import Blueprint, jsonify, render_template, request
from core import setcalc, reward

bp = Blueprint("setcalc", __name__, url_prefix="/setcalc")

# -------- UI-Seite -----------------------------------------------------------

@bp.route("/")
def page():
    return render_template("setcalc.html")

# -------- Helpers ------------------------------------------------------------

def _ok(payload):
    return jsonify({"ok": True, **payload})

def _err(e):
    return jsonify({"ok": False, "error": str(e)}), 400

def _reward(op: str, meta: dict):
    """Schreibt ein Reward-Signal für erfolgreiche Operationen"""
    try:
        reward.log("setcalc", value=0.05, info={"op": op, **meta})
    except Exception as e:
        print(f"[setcalc_ui] reward-log Fehler: {e}")

# -------- APIs ---------------------------------------------------------------

@bp.post("/api/union")
def api_union():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.op_union(js.get("A"), js.get("B"))
        setcalc.log("union", js.get("A"), js.get("B"), res)
        _reward("union", {"A": js.get("A"), "B": js.get("B")})
        return _ok({"result": res})
    except Exception as e:
        return _err(e)

@bp.post("/api/intersection")
def api_intersection():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.op_intersection(js.get("A"), js.get("B"))
        setcalc.log("intersection", js.get("A"), js.get("B"), res)
        _reward("intersection", {"A": js.get("A"), "B": js.get("B")})
        return _ok({"result": res})
    except Exception as e:
        return _err(e)

@bp.post("/api/difference")
def api_difference():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.op_difference(js.get("A"), js.get("B"))
        setcalc.log("difference", js.get("A"), js.get("B"), res)
        _reward("difference", {"A": js.get("A"), "B": js.get("B")})
        return _ok({"result": res})
    except Exception as e:
        return _err(e)

@bp.post("/api/complement")
def api_complement():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.op_complement(js.get("A"), js.get("U"))
        setcalc.log("complement", js.get("A"), js.get("U"), res)
        _reward("complement", {"A": js.get("A"), "U": js.get("U")})
        return _ok({"result": res})
    except Exception as e:
        return _err(e)

@bp.post("/api/powerset")
def api_powerset():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.op_powerset(js.get("A"))
        cnt = len(res) if isinstance(res, list) else 0
        setcalc.log("powerset", js.get("A"), None, {"count": cnt})
        _reward("powerset", {"A": js.get("A"), "count": cnt})
        return _ok({"result": res, "count": cnt})
    except Exception as e:
        return _err(e)

@bp.post("/api/cartesian")
def api_cartesian():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.op_cartesian(js.get("A"), js.get("B"))
        cnt = len(res) if isinstance(res, list) else 0
        setcalc.log("cartesian", js.get("A"), js.get("B"), {"count": cnt})
        _reward("cartesian", {"A": js.get("A"), "B": js.get("B"), "count": cnt})
        return _ok({"pairs": res, "count": cnt})
    except Exception as e:
        return _err(e)

@bp.post("/api/venn_counts")
def api_venn_counts():
    js = request.get_json(force=True) or {}
    try:
        res = setcalc.venn_counts(js.get("A"), js.get("B"))
        # optional kein Reward – nur Statistik
        return _ok(res)
    except Exception as e:
        return _err(e)
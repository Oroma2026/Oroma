#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/scicalc_ui.py
# Projekt: ORÓMA v3.7 (final) – SciCalc + Charts + Reward-Bridge
# Stand:   2025-09-29
#
# Zweck
# ─────
#  Flask-Blueprint für den wissenschaftlichen Taschenrechner mit
#  Diagramm-Integration und persistenter Ergebnis-Logging.
#  Ergänzt den Curriculum-Calculator um:
#    • Funktionsauswertung (evaluate)
#    • Nullstellen-Suche (roots)
#    • Taylor-Entwicklung (taylor)
#    • Grenzwerte (limit)
#    • Funktionsplots (plot_points → LineChart)
#    • Balkendiagramme (make_bar → BarChart)
#    • Tortendiagramme (make_pie → PieChart)
#
# Reward-Bridge (v3.7)
# ────────────────────
#  • Erfolgreiche Operationen erzeugen ein Reward-Signal (source="scicalc", +0.05)
#  • Rewards erscheinen im Dashboard (/learning/api/data, /learning/api/history)
#  • Voll kompatibel zu v3.6 Patch-2c; keine weiteren Abhängigkeiten
#
# Hinweise
# ────────
#  • Logging in scicalc_results via core.sql_manager.insert_scicalc_result()
#  • Reward-Logging via core.reward.log()
#  • Keine Entfernung bestehender Funktionalität; nur Header konsolidiert
# =============================================================================

import time
from flask import Blueprint, render_template, request, jsonify
from core import scicalc, sql_manager, reward
import logging
from core.log_guard import log_suppressed

bp = Blueprint("scicalc", __name__, url_prefix="/scicalc")

# ----------------------------------------------------------------------
# UI-Seite
# ----------------------------------------------------------------------
@bp.route("/")
def page():
    return render_template("scicalc.html")

# ----------------------------------------------------------------------
# Helper: Logging + Rewards
# ----------------------------------------------------------------------
def _log(expr: str, method: str, input: dict, result: str = "", error: str = ""):
    ts = int(time.time())
    try:
        sql_manager.insert_scicalc_result(
            ts, expr, method, str(input), str(result), str(error)
        )
    except Exception as e:
        log_suppressed('ui/scicalc_ui.py:56', exc=e, level=logging.WARNING)
        pass

def _reward(method: str, meta: dict):
    try:
        reward.log("scicalc", value=0.05, info={"method": method, **meta})
    except Exception as e:
        print(f"[scicalc_ui] reward-log Fehler: {e}")

# ----------------------------------------------------------------------
# APIs
# ----------------------------------------------------------------------
@bp.route("/api/eval", methods=["POST"])
def api_eval():
    js = request.get_json(force=True)
    expr = js.get("expr", "x")
    xval = float(js.get("x", 0))
    try:
        y = scicalc.evaluate(expr, xval)
        _log(expr, "eval", {"x": xval}, y)
        _reward("eval", {"expr": expr, "x": xval})
        return jsonify(ok=True, y=y)
    except Exception as e:
        _log(expr, "eval", {"x": xval}, error=str(e))
        return jsonify(ok=False, error=str(e))

@bp.route("/api/roots", methods=["POST"])
def api_roots():
    js = request.get_json(force=True)
    expr = js.get("expr", "x")
    xmin = float(js.get("xmin", -10))
    xmax = float(js.get("xmax", 10))
    try:
        roots = scicalc.find_roots(expr, xmin, xmax)
        _log(expr, "roots", {"xmin": xmin, "xmax": xmax}, roots)
        _reward("roots", {"expr": expr, "xmin": xmin, "xmax": xmax})
        return jsonify(ok=True, roots=roots)
    except Exception as e:
        _log(expr, "roots", {"xmin": xmin, "xmax": xmax}, error=str(e))
        return jsonify(ok=False, error=str(e))

@bp.route("/api/taylor", methods=["POST"])
def api_taylor():
    js = request.get_json(force=True)
    expr = js.get("expr", "x")
    x0 = float(js.get("x0", 0))
    n = int(js.get("n", 5))
    try:
        series = scicalc.taylor_expand(expr, x0, n)
        _log(expr, "taylor", {"x0": x0, "n": n}, series)
        _reward("taylor", {"expr": expr, "x0": x0, "n": n})
        return jsonify(ok=True, series=series)
    except Exception as e:
        _log(expr, "taylor", {"x0": x0, "n": n}, error=str(e))
        return jsonify(ok=False, error=str(e))

@bp.route("/api/plot", methods=["POST"])
def api_plot():
    js = request.get_json(force=True)
    expr = js.get("expr", "x")
    xmin = float(js.get("xmin", -10))
    xmax = float(js.get("xmax", 10))
    try:
        data = scicalc.plot_points(expr, xmin, xmax)
        _log(expr, "plot", {"xmin": xmin, "xmax": xmax}, f"{len(data['x'])} points")
        _reward("plot", {"expr": expr, "xmin": xmin, "xmax": xmax})
        return jsonify(ok=True, **data)
    except Exception as e:
        _log(expr, "plot", {"xmin": xmin, "xmax": xmax}, error=str(e))
        return jsonify(ok=False, error=str(e))

@bp.route("/api/limit", methods=["POST"])
def api_limit():
    js = request.get_json(force=True)
    expr = js.get("expr", "x")
    x0 = float(js.get("x0", 0))
    try:
        val = scicalc.limit(expr, "x", x0)
        _log(expr, "limit", {"x0": x0}, val)
        _reward("limit", {"expr": expr, "x0": x0})
        return jsonify(ok=True, limit=val)
    except Exception as e:
        _log(expr, "limit", {"x0": x0}, error=str(e))
        return jsonify(ok=False, error=str(e))

@bp.route("/api/bar", methods=["POST"])
def api_bar():
    js = request.get_json(force=True)
    data = js.get("data", {"A": 1, "B": 2})
    try:
        out = scicalc.make_bar(data)
        _log("bar-data", "bar", data, out)
        _reward("bar", {"data": data})
        return jsonify(ok=True, **out)
    except Exception as e:
        _log("bar-data", "bar", data, error=str(e))
        return jsonify(ok=False, error=str(e))

@bp.route("/api/pie", methods=["POST"])
def api_pie():
    js = request.get_json(force=True)
    data = js.get("data", {"A": 30, "B": 70})
    try:
        out = scicalc.make_pie(data)
        _log("pie-data", "pie", data, out)
        _reward("pie", {"data": data})
        return jsonify(ok=True, **out)
    except Exception as e:
        _log("pie-data", "pie", data, error=str(e))
        return jsonify(ok=False, error=str(e))
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/calculator_ui.py
# Projekt: ORÓMA – UI – Calculator (Math/Logic Tasks)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   UI-Blueprint für den ORÓMA-Curriculum-Taschenrechner:
#     - Übersicht aller Aufgaben & Ergebnisse
#     - API-Endpunkte (Tasks/Results)
#     - Interaktiv: neue Aufgabe / Lösung prüfen
#
# Erweiterungen (v3.7.3)
# ──────────────────────
#   • truth_json wird mit ausgeliefert (UI kann Typ erkennen: fill/seq/puzzle/cmp)
#   • solve:
#       - akzeptiert scalar, "2, -2" (Listen), und compare-Symbole "< = >"
#       - nutzt truth_json, um ggf. "solution" (List/Scalar) zu verwenden
#
# =============================================================================

from __future__ import annotations

import json
from flask import Blueprint, render_template, jsonify, request
from core import sql_manager
import logging
from core.log_guard import log_suppressed

bp = Blueprint("calculator_ui", __name__, url_prefix="/calculator")


def _fetch_tasks(limit: int = 50):
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, ts as created_at, level, expr, truth, truth_json "
        "FROM calculator_tasks ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in cur.fetchall() or []]


def _fetch_results(limit: int = 100):
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT r.id, r.task_id, r.ts as created_at, r.got, r.got_json, r.correct, r.reward, t.expr "
        "FROM calculator_results r "
        "LEFT JOIN calculator_tasks t ON r.task_id = t.id "
        "ORDER BY r.id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in cur.fetchall() or []]


@bp.route("/")
def page():
    return render_template("calculator.html")


@bp.get("/api/tasks")
def api_tasks():
    limit = int(request.args.get("limit", 50))
    return jsonify({"ok": True, "tasks": _fetch_tasks(limit)})


@bp.get("/api/results")
def api_results():
    limit = int(request.args.get("limit", 100))
    return jsonify({"ok": True, "results": _fetch_results(limit)})


@bp.post("/api/new_task")
def api_new_task():
    from mini_programs.calculator import Calculator
    data = request.get_json(force=True) or {}
    level = int(data.get("level", 1))
    tid = Calculator.new_task(level)

    conn = sql_manager.get_conn()
    cur = conn.execute(
        "SELECT id, ts as created_at, level, expr, truth, truth_json "
        "FROM calculator_tasks WHERE id=?",
        (int(tid),),
    )
    task = cur.fetchone()
    return jsonify({"ok": True, "task": dict(task) if task else None})


@bp.post("/api/solve")
def api_solve():
    from mini_programs.calculator import Calculator

    data = request.get_json(force=True) or {}
    task_id = int(data.get("task_id"))

    got_raw = data.get("got")
    got = got_raw

    # Hole Wahrheit + truth_json aus DB
    conn = sql_manager.get_conn()
    row = conn.execute("SELECT truth, truth_json FROM calculator_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Task not found"}), 404

    truth = row["truth"]
    truth_json_raw = row["truth_json"]

    truth_json = None
    if truth_json_raw:
        try:
            truth_json = json.loads(truth_json_raw)
            # falls solution vorhanden → ist die „volle“ Wahrheit
            if isinstance(truth_json, list):
                truth = truth_json
            elif isinstance(truth_json, dict):
                if "solution" in truth_json:
                    truth = truth_json.get("solution")
                elif "solutions" in truth_json:
                    truth = truth_json.get("solutions")
        except Exception:
            truth_json = None

    # got parsing: Zahlen, "2, -2", oder compare "< = >"
    try:
        if isinstance(got_raw, str):
            s = got_raw.strip()
            # compare
            if s in ("<", "=", ">"):
                got = {"<": -1.0, "=": 0.0, ">": 1.0}[s]
            # list
            elif "," in s:
                got = [float(x.strip().replace(",", ".")) for x in s.split(",") if x.strip()]
            else:
                got = float(s.replace(",", "."))
        elif isinstance(got_raw, (int, float)):
            got = float(got_raw)
        elif isinstance(got_raw, list):
            got = [float(x) for x in got_raw]
    except Exception:
        got = got_raw  # fallback

    rid = Calculator.solve_task(task_id, got, truth)

    # UI-friendly Anzeige der Wahrheit (z.B. compare als Symbol)
    display_truth = truth
    try:
        if isinstance(truth_json, dict) and truth_json.get("type") == "compare":
            # truth ist -1/0/1
            if float(truth) < 0:
                display_truth = "<"
            elif float(truth) > 0:
                display_truth = ">"
            else:
                display_truth = "="
        elif isinstance(truth, list):
            display_truth = ", ".join(str(x) for x in truth)
    except Exception as e:
        log_suppressed('ui/calculator_ui.py:162', exc=e, level=logging.WARNING)
        pass

    # "correct" für Response robust ableiten
    correct = False
    try:
        if isinstance(truth, list) and isinstance(got, list):
            correct = len(truth) == len(got) and all(abs(float(a) - float(b)) < 1e-6 for a, b in zip(truth, got))
        else:
            correct = abs(float(got) - float(truth)) < 1e-6
    except Exception:
        correct = (got == truth)

    return jsonify({
        "ok": True,
        "result_id": rid,
        "correct": bool(correct),
        "truth": truth,
        "display_truth": display_truth,
        "truth_json": truth_json,
    })
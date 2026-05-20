#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/ptz_coverage_ui.py
# Projekt:   ORÓMA (PTZ Coverage · Staubsauger-Sweep · UI)
# Version:   v3.7.6
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
#   UI-Blueprint für das PTZ Coverage-Spiel.
#   Dieses UI ist bewusst "standardisiert" wie die anderen Game-UIs:
#     - Seite /ptz_coverage/ (Cards/Rows)
#     - API: /ptz_coverage/api/state + /ptz_coverage/api/run
#     - Default Explore bleibt aktiv
#
# DAUERBETRIEB / PERSISTENZ
# ─────────────────────────
#   Coverage-Zustand liegt in stats.db (Tabelle ptz_coverage_cells), damit
#   tägliche Sweeps über Wochen persistent sind. Das UI zeigt kompakte
#   Summaries (Anzahl Zellen, letzte Zellen, oldest-last-seen).
#
# DB-SICHERHEIT (User-Regel)
# ─────────────────────────
#   Jede DB-Connection wird zuverlässig geschlossen.
#   -> core.sql_manager.get_conn(...) ist ein Context-Manager, der close() sicherstellt.
#
# HEADLESS
# ────────
#   Keine GUI-Bibliotheken; nur Flask + stdlib + core.sql_manager.
#
# =============================================================================

from __future__ import annotations

import os
import json
import subprocess
from typing import Any, Dict, List

from flask import Blueprint, jsonify, render_template, request


bp = Blueprint("ptz_coverage_bp", __name__, url_prefix="/ptz_coverage", template_folder="templates")


def _base_dir() -> str:
    return os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma"


def _stats_db_path() -> str:
    return os.path.join(_base_dir(), "data", "stats.db")


def _ensure_stats_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ptz_coverage_cells (
          namespace TEXT NOT NULL,
          cell_id TEXT NOT NULL,
          last_seen_ts INTEGER NOT NULL,
          seen_count INTEGER NOT NULL,
          best_motion REAL NOT NULL,
          best_strength REAL NOT NULL,
          best_sharp REAL NOT NULL,
          best_ts INTEGER NOT NULL,
          PRIMARY KEY(namespace, cell_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ptz_coverage_cells_last_seen ON ptz_coverage_cells(namespace, last_seen_ts DESC)")


def _query_stats(namespace: str) -> Dict[str, Any]:
    from core import sql_manager

    ns = str(namespace)
    with sql_manager.get_conn(_stats_db_path()) as conn:
        _ensure_stats_schema(conn)
        conn.commit()
        row = conn.execute(
            "SELECT COUNT(*) AS n_cells, COALESCE(SUM(seen_count),0) AS n_visits FROM ptz_coverage_cells WHERE namespace=?",
            (ns,),
        ).fetchone() or {}

        last_rows = conn.execute(
            "SELECT cell_id, last_seen_ts, seen_count, best_motion, best_strength, best_sharp "
            "FROM ptz_coverage_cells WHERE namespace=? ORDER BY last_seen_ts DESC LIMIT 12",
            (ns,),
        ).fetchall() or []

        oldest_rows = conn.execute(
            "SELECT cell_id, last_seen_ts, seen_count FROM ptz_coverage_cells WHERE namespace=? ORDER BY last_seen_ts ASC LIMIT 12",
            (ns,),
        ).fetchall() or []

    return {
        "namespace": ns,
        "n_cells": int(row.get("n_cells") or 0),
        "n_visits": int(row.get("n_visits") or 0),
        "last": list(last_rows),
        "oldest": list(oldest_rows),
    }


@bp.route("/", methods=["GET"])
def page() -> str:
    return render_template("ptz_coverage.html")


def _ptz_status_probe() -> Dict[str, Any]:
    """Probe PTZ status endpoint to detect if absolute coordinates are available."""
    import urllib.request

    base_url = (os.environ.get("OROMA_PTZ_COV_BASE_URL") or "http://127.0.0.1:8080").rstrip("/")
    timeout_sec = float(os.environ.get("OROMA_PTZ_COV_HTTP_TIMEOUT_SEC") or "2.0")
    url = f"{base_url}/video/api/ptz/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as r:
            raw = r.read().decode("utf-8", errors="replace")
        st = json.loads(raw) if raw else {}
    except Exception:
        return {"ok": False, "supported": False, "has_coords": False, "pan": None, "tilt": None, "zoom": None}

    pan = st.get("pan")
    tilt = st.get("tilt")
    zoom = st.get("zoom")
    try:
        pan_i = int(pan) if pan is not None else 0
        tilt_i = int(tilt) if tilt is not None else 0
        zoom_i = int(zoom) if zoom is not None else 0
        has_coords = (pan is not None or tilt is not None or zoom is not None) and (pan_i != 0 or tilt_i != 0 or zoom_i != 0)
    except Exception:
        has_coords = False

    return {"ok": True, "supported": True, "has_coords": bool(has_coords), "pan": pan, "tilt": tilt, "zoom": zoom}

@bp.route("/api/state", methods=["GET"])
def api_state():
    ns = os.environ.get("OROMA_PTZ_COV_NAMESPACE", "ptz:coverage")
    stats = _query_stats(ns)
    ptz = _ptz_status_probe()
    virtual_mode = bool(ptz.get('ok') and (not ptz.get('has_coords')))
    return jsonify({
        "ok": True,
        "namespace": ns,
        "defaults": {
            "policy_games": int(os.environ.get("OROMA_ORCH_PTZ_COVERAGE_POLICY_GAMES", "20")),
            "explore_games": int(os.environ.get("OROMA_ORCH_PTZ_COVERAGE_EXPLORE_GAMES", "20")),
        },
        "stats": stats,
        "ptz_status": ptz,
        "virtual_mode": virtual_mode,
    })


@bp.route("/api/run", methods=["POST"])
def api_run():
    """Run a small batch synchronously (kept small; intended for manual testing)."""
    payload = request.get_json(silent=True) or {}
    ns = str(payload.get("namespace") or os.environ.get("OROMA_PTZ_COV_NAMESPACE", "ptz:coverage"))
    policy_games = int(payload.get("policy_games") or 0)
    explore_games = int(payload.get("explore_games") or 10)
    seed = int(payload.get("seed") or 1)
    timeout_s = int(payload.get("timeout_s") or 180)

    base = _base_dir()
    runner = os.path.join(base, "tools", "ptz_coverage_daily_runner.py")
    cmd = ["python3", runner, "--policy-games", str(max(0, policy_games)), "--explore-games", str(max(0, explore_games)), "--seed", str(seed), "--namespace", ns]

    env = os.environ.copy()
    env["PYTHONPATH"] = base
    env["OROMA_BASE"] = base

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        j = json.loads(out) if out else {"ok": False, "err": "no_output"}
        if p.returncode != 0 and j.get("ok") is True:
            j["ok"] = False
            j["err"] = f"runner_exit_{p.returncode}"
        if err:
            j["stderr"] = err[-2000:]
        return jsonify(j)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "err": "timeout"}), 504
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 500


# games_ui Kompatibilität
ptz_coverage_bp = bp

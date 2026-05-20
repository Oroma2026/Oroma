#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/coverage_ui.py
# Projekt:   ORÓMA (Offline-First · Headless · Learning Telemetry)
# Modul:     Coverage UI – kleines Dashboard (State + History) für coverage_log via core.sql_manager
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Coverage ist in ORÓMA ein leichtgewichtiges Telemetrie-Signal, das ausdrückt,
# wie viel „abgedeckt“ / „aktiv“ ein Bereich des Wissenszustands gerade ist
# (z. B. Anteil aktiver Einheiten vs. Gesamtmenge).
#
# Diese Datei liefert dafür ein minimales UI + API:
#   - UI-Seite: /coverage/  (template: coverage.html)
#   - API:
#       GET /coverage/api/state    → letzter Coverage-Wert (oder Default)
#       GET /coverage/api/history  → Verlauf (letzte N Einträge)
#
# HEADLESS / ROBUSTHEIT
# ─────────────────────
# - Headless: keine GUI-Libs.
# - Best-effort Import:
#     try: from core import sql_manager
#     except: sql_manager=None, _HAS_SQL=False
#   → UI bleibt startbar, selbst wenn DB-Schicht fehlt (liefert dann Defaults).
#
# BLUEPRINT
# ─────────
# bp = Blueprint("coverage_ui", __name__, url_prefix="/coverage")
#
# ENV-SCHALTER
# ────────────
# OROMA_ENABLE_COVERAGE (default "true")
# - Werte, die als True gelten: "1", "true", "yes" (case-insensitive)
# - Wenn False:
#     • API liefert {"ok":False,"error":"coverage disabled"} (history:[])
#     • UI rendert coverage.html mit enabled=False (Template kann Banner anzeigen)
#
# DATENQUELLE (EXAKT)
# ───────────────────
# Dieses Modul delegiert Reads an:
#   sql_manager.fetch_last_coverage(limit)
#
# In core.sql_manager ist die Abfrage:
#   SELECT ts, coverage, active, total
#   FROM coverage_log
#   ORDER BY id DESC
#   LIMIT ?
#
# coverage_log Schema (aus sql_manager.ensure_schema()):
#   CREATE TABLE IF NOT EXISTS coverage_log (
#     id       INTEGER PRIMARY KEY AUTOINCREMENT,
#     ts       INTEGER NOT NULL,
#     coverage REAL NOT NULL,
#     active   INTEGER NOT NULL,
#     total    INTEGER NOT NULL
#   );
#
# HELFERFUNKTIONEN (SEMANTIK)
# ──────────────────────────
# _fetch_state():
#   - Wenn sql_manager nicht verfügbar → Default:
#       {"coverage":0.0,"active":0,"total":0,"ts":now}
#   - Sonst: fetch_last_coverage(1)
#       • keine Rows → Default wie oben
#       • sonst → rows[0] (Row-Dict aus sql_manager row_factory)
#
# _fetch_history(limit=50):
#   - Wenn sql_manager nicht verfügbar → []
#   - Sonst: sql_manager.fetch_last_coverage(limit)
#
# ROUTES / RESPONSES (EXAKT IM CODE)
# ──────────────────────────────────
# UI:
#   GET /coverage/
#     - render_template("coverage.html", enabled=_ENABLE_COVERAGE)
#
# API:
#   GET /coverage/api/state
#     - wenn disabled → {"ok":False,"error":"coverage disabled"}
#     - sonst        → {"ok":True,"state": <dict>}
#
#   GET /coverage/api/history?limit=50
#     - wenn disabled → {"ok":False,"error":"coverage disabled","history":[]}
#     - sonst         → {"ok":True,"history": <list[dict]>}
#     - limit wird aus Query gelesen, default 50 (als String → int wird in sql_manager gecastet)
#
# AUTH / SECURITY
# ───────────────
# Keine Auth in diesem Modul. Zugriffsschutz erfolgt zentral (Flask-Setup / Proxy).
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - url_prefix="/coverage" stabil halten (UI-Links/Bookmarks).
# - ENV-Schalter OROMA_ENABLE_COVERAGE muss erhalten bleiben (Feature-Toggle).
# - Best-effort Verhalten (_HAS_SQL) muss bleiben (Boot-Stabilität).
# - API-Key-Namen ("state","history","coverage","active","total","ts") stabil halten,
#   da Templates/JS diese Struktur erwarten.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os
import time
from flask import Blueprint, render_template, jsonify, request

try:
    from core import sql_manager
    _HAS_SQL = True
except Exception:
    sql_manager = None
    _HAS_SQL = False

bp = Blueprint("coverage_ui", __name__, url_prefix="/coverage")

# ENV-Flag (default: true)
_ENABLE_COVERAGE = os.environ.get("OROMA_ENABLE_COVERAGE", "true").lower() in ("1", "true", "yes")

# ----------------------------- Helpers ---------------------------------------

def _fetch_state(mode: str = "window"):
    """Letzter Coverage-Eintrag aus DB oder Default-Wert.

    mode:
      - "window" (default): coverage_log_30d (Fenster-Variante, Default 30 Tage)
      - "total":  coverage_log (Legacy, gesamte Historie)

    Backwards compatible:
      - Falls coverage_log_30d in einer alten DB fehlt, wird auf coverage_log zurückgefallen.
    """
    if not _HAS_SQL:
        return {"coverage": 0.0, "active": 0, "total": 0, "ts": int(time.time())}

    mode = (mode or "window").strip().lower()
    if mode == "total":
        rows = sql_manager.fetch_last_coverage(1)
        return rows[0] if rows else {"coverage": 0.0, "active": 0, "total": 0, "ts": int(time.time())}

    # default: window
    try:
        rows = sql_manager.fetch_last_coverage_30d(1)
        if rows:
            return rows[0]
    except Exception:
        pass

    # Fallback: total
    rows = sql_manager.fetch_last_coverage(1)
    return rows[0] if rows else {"coverage": 0.0, "active": 0, "total": 0, "ts": int(time.time())}


def _fetch_history(limit: int = 50, mode: str = "window"):
    """Letzte N Coverage-Einträge aus DB."""
    if not _HAS_SQL:
        return []

    mode = (mode or "window").strip().lower()
    if mode == "total":
        return sql_manager.fetch_last_coverage(limit)

    try:
        rows = sql_manager.fetch_last_coverage_30d(limit)
        if rows is not None:
            return rows
    except Exception:
        pass

    # Fallback: total
    return sql_manager.fetch_last_coverage(limit)

# ----------------------------- UI-Route --------------------------------------

@bp.route("/")
def page():
    return render_template("coverage.html", enabled=_ENABLE_COVERAGE)

# ----------------------------- API Routes ------------------------------------

@bp.get("/api/state")
def api_state():
    if not _ENABLE_COVERAGE:
        return jsonify({"ok": False, "error": "coverage disabled"})
    mode = request.args.get("mode", "window")
    return jsonify({"ok": True, "state": _fetch_state(mode)})

@bp.get("/api/history")
def api_history():
    if not _ENABLE_COVERAGE:
        return jsonify({"ok": False, "error": "coverage disabled", "history": []})
    limit = request.args.get("limit", 50)
    mode = request.args.get("mode", "window")
    return jsonify({"ok": True, "history": _fetch_history(limit, mode)})
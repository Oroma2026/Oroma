#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/meta_ui.py
# Projekt:   ORÓMA (Offline-First · Headless · MetaSnaps)
# Modul:     Meta UI – Minimal-Blueprint zum Listen von meta_snaps + Dummy-Graph (UI-Exploration)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Diese Datei stellt eine sehr kleine MetaSnaps-UI bereit:
#   - HTML-Seite /meta/ (template: meta.html)
#   - JSON-API /meta/api/list: liefert:
#       • items: Liste der letzten 50 MetaSnaps
#       • graph: Dummy-Graph (Nodes aus items, keine Edges)
#
# Wichtig:
# - In diesem Repo-Stand ist dieses Modul **Legacy/Minimal** und nutzt
#   **direkt sqlite3** statt core.sql_manager.
# - Es erzeugt sein eigenes Schema (`meta_snaps`) per CREATE TABLE IF NOT EXISTS.
# - Der Graph ist bewusst ein „Dummy-Graph“ (nur Knoten), damit die UI bereits
#   etwas darstellen kann, auch ohne Core-Graph-Builder.
#
# BLUEPRINT
# ─────────
# bp = Blueprint("meta_ui", __name__, url_prefix="/meta")
#
# UI:
#   GET /meta/ → templates/meta.html
#
# API:
#   GET /meta/api/list → JSON: items + dummy graph
#
# DATENBANKPFAD (WICHTIG / LEGACY-DETAIL)
# ───────────────────────────────────────
# DB_PATH = ENV["OROMA_DB"] oder Default:
#   "/opt/ai/oroma/database/oroma.db"
#
# ⚠️ Hinweis für v3.7.x ORÓMA:
# - Der übliche Pfad ist in vielen Setups: /opt/ai/oroma/data/oroma.db
# - Dieses Modul nutzt jedoch den *alten* Default-Pfad „/database/oroma.db“.
#   Wenn ENV OROMA_DB nicht gesetzt ist, kann dieses UI sonst eine **separate**
#   Datenbankdatei anlegen und „leer“ wirken.
#
# Empfehlung (ohne Code-Änderung, nur Betrieb):
# - Setze im Service/Env:
#     OROMA_DB=/opt/ai/oroma/data/oroma.db
#
# SCHEMA (WIRD HIER SELBST ANGELEGT)
# ──────────────────────────────────
# In api_list() wird (idempotent) folgendes Schema erzeugt:
#   CREATE TABLE IF NOT EXISTS meta_snaps (
#     id      INTEGER PRIMARY KEY AUTOINCREMENT,
#     label   TEXT,
#     score   REAL,
#     sources TEXT          -- JSON-Liste (String), z. B. ["chain:12","origin:vision/token"]
#   );
#
# Danach:
#   SELECT id, label, score, sources FROM meta_snaps ORDER BY id DESC LIMIT 50;
#
# OUTPUT-FORMAT (API)
# ───────────────────
# items:
#   [
#     {
#       "id": <int>,
#       "label": <str>   (Fallback: "MetaSnap <id>"),
#       "score": <float|None>,
#       "sources": <list[str]>   (aus JSON geparst; bei Fehler → [])
#     }, ...
#   ]
#
# graph (Dummy):
#   {
#     "nodes": [{"id": <id>, "label": <label>, "type": "meta"}, ...],
#     "edges": []
#   }
#
# FEHLERROBUSTHEIT
# ────────────────
# - sources Parsing ist best-effort:
#     json.loads(...) Fehler → sources=[]
# - DB-Zugriff ist „simple sqlite3“ (keine row_factory dicts).
#
# AUTH / SECURITY
# ───────────────
# Keine Auth in diesem Modul. Zugriffsschutz muss global erfolgen (Flask-Setup / Proxy).
#
# BEKANNTES TECH-DEBT (EXPLIZIT DOKUMENTIERT)
# ───────────────────────────────────────────
# 1) Direkt sqlite3 statt core.sql_manager:
#    - Umstellung wäre sinnvoll, um:
#        • denselben DB-Pfad/PRAGMA (WAL/busy_timeout) zu nutzen
#        • Schema zentral zu halten
#
# 2) Dummy-Graph:
#    - Edges fehlen absichtlich. Spätere Erweiterung:
#        • Graph aus core.scenegraph_store / core.meta_snap / rules ableiten
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - url_prefix="/meta" stabil halten (UI-Links).
# - sources bleibt JSON-Liste in TEXT (kompatibel, portable).
# - Bei Refactor: API-Response Keys ("items","graph","nodes","edges") stabil halten.
#
# =============================================================================
# END HEADER
# =============================================================================

from flask import Blueprint, render_template, jsonify
import sqlite3
import os
import json

bp = Blueprint("meta_ui", __name__, url_prefix="/meta")

DB_PATH = os.environ.get("OROMA_DB", "/opt/ai/oroma/database/oroma.db")

def _get_conn():
    return sqlite3.connect(DB_PATH)

# ----------------------------- UI-Seite -------------------------------------

@bp.route("/")
def page():
    return render_template("meta.html")

# ----------------------------- API ------------------------------------------

@bp.route("/api/list", methods=["GET"])
def api_list():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta_snaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            score REAL,
            sources TEXT
        )
    """)
    conn.commit()
    cur.execute("SELECT id, label, score, sources FROM meta_snaps ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    conn.close()

    items = []
    for r in rows:
        srcs = []
        try:
            srcs = json.loads(r[3]) if r[3] else []
        except Exception:
            srcs = []
        items.append({
            "id": r[0],
            "label": r[1] or f"MetaSnap {r[0]}",
            "score": r[2],
            "sources": srcs
        })

    # Dummy-Graph (später aus Core generieren)
    graph = {
        "nodes": [{"id": m["id"], "label": m["label"], "type": "meta"} for m in items],
        "edges": []
    }

    return jsonify({"ok": True, "items": items, "graph": graph})
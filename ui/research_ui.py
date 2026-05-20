#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/research_ui.py
# Projekt:   ORÓMA (Offline-First · Headless · Research Loop)
# Modul:     Research UI – Hypothesen-Dashboard (Anlegen/List/Update) als Flask-Blueprint
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Diese Datei stellt das „Research / Hypothesen“-Dashboard für ORÓMA bereit.
# Der Fokus ist bewusst pragmatisch: Hypothesen werden als strukturierte Einträge
# in SQLite geführt (oroma.db) und können über die UI/JSON-API gepflegt werden.
#
# Typischer ORÓMA-Workflow:
#   1) Beobachtung / Gap / Anomalie (z. B. über diagnostics/gaps/learning curves)
#   2) Hypothese anlegen (Text)
#   3) Hypothese iterativ prüfen (manuell oder via Experiment-/Tuning-Tools)
#   4) Status/Score/Confidence aktualisieren, last_tested setzen
#
# DESIGN-PRINZIPIEN
# ────────────────
# - Headless: keine Qt/Wayland/X11/GUI-Libs.
# - Thin UI Layer: keine DB-Logik „überall“, sondern:
#     • Schema/Insert/Update delegiert an core.sql_manager
#     • Listing nutzt bewusst ein kurzes SELECT (stabil & schnell)
# - Non-destructive: keine Löschfunktion (Hypothesen bleiben nachvollziehbar).
# - JSON-Antworten sind klein, UI-freundlich und stabil (ok + payload).
#
# BLUEPRINT
# ─────────
# bp = Blueprint("research_ui", __name__, url_prefix="/research")
#
# Template:
# - GET /research/ rendert: templates/research.html
#
# AUTH / SECURITY (WICHTIG)
# ────────────────────────
# Diese Datei implementiert KEINE Authentifizierung.
# Zugriffsschutz (Token-Guard / Proxy-Auth) ist zentral (z. B. ui/flask_ui.py
# bzw. Reverse-Proxy). Bitte NICHT hier „ad-hoc“ Auth einbauen, ohne die globale
# Policy zu prüfen.
#
# DATENBANK / SCHEMA (EXAKT: core.sql_manager)
# ────────────────────────────────────────────
# Dieses UI nutzt core.sql_manager als „Source of Truth“ für Schema & Writes:
#   - sql.ensure_schema()
#   - sql.insert_hypothesis(...)
#   - sql.update_hypothesis(...)
#   - sql.get_conn() (für das Listing SELECT)
#
# Tabelle: hypotheses  (wird in sql_manager.ensure_schema() angelegt)
#   CREATE TABLE IF NOT EXISTS hypotheses (
#     id          INTEGER PRIMARY KEY AUTOINCREMENT,
#     text        TEXT NOT NULL,
#     status      TEXT NOT NULL DEFAULT 'open',      -- open|confirmed|rejected
#     score       REAL NOT NULL DEFAULT 0.0,         -- frei interpretierbar (z. B. Nutzen)
#     confidence  REAL NOT NULL DEFAULT 0.0,         -- frei interpretierbar (z. B. Evidenz)
#     plan        TEXT,                               -- reserviert (Experiment-Plan)
#     last_tested INTEGER,                            -- Unix seconds (Retest/Update Zeitpunkt)
#     meta        TEXT,                               -- optional JSON/Text
#     created     INTEGER NOT NULL                    -- Unix seconds
#   );
#
# Hinweis:
# - In dieser UI werden meta/plan aktuell nicht aktiv gepflegt, aber bewusst im Schema
#   behalten (Research-Loop Erweiterbarkeit).
#
# ROUTES / API (EXAKT IM CODE)
# ───────────────────────────
# 1) UI
#   GET  /research/
#     - liefert HTML (research.html)
#
# 2) API – neue Hypothese
#   POST /research/api/new
#     Request JSON:
#       {"text": "<string>"}
#     Verhalten:
#       - text wird .strip() validiert (leer → 400)
#       - created = now (Unix seconds)
#       - Insert via sql.insert_hypothesis(ts=created, text=text)
#     Response JSON:
#       200: {"ok": true, "id": <int>, "text": "<string>"}
#       400: {"ok": false, "error": "Text fehlt"}
#       500: {"ok": false, "error": "Insert fehlgeschlagen"}
#
# 3) API – Liste (letzte 50)
#   GET /research/api/list
#     Verhalten:
#       - SELECT id,text,created,status,score,confidence,last_tested
#         FROM hypotheses ORDER BY id DESC LIMIT 50
#       - Formatiert created/last_tested in lokale Zeitstrings (YYYY-MM-DD HH:MM:SS)
#     Response JSON:
#       {"ok": true, "items": [
#          {"id":..,"text":..,"created":"..","status":"open|confirmed|rejected",
#           "score":..,"confidence":..,"last_tested":".."},
#          ...
#       ]}
#
# 4) API – Update (Status/Score/Confidence/Retest)
#   POST /research/api/update/<int:hypo_id>
#     Request JSON (jede Kombination möglich):
#       {"status": "confirmed"|"rejected"|"open"}          # optional
#       {"action": "retest"}                               # optional
#       {"score": <float>}                                 # optional
#       {"confidence": <float>}                            # optional
#
#     Validierung:
#       - status muss in {None,"confirmed","rejected","open"} liegen, sonst 400
#       - action muss None oder "retest" sein, sonst 400
#
#     last_tested:
#       - wird auf now gesetzt, wenn:
#           • status gesetzt wurde ODER
#           • action == "retest"
#       - sonst bleibt last_tested unverändert (None wird nicht geschrieben)
#
#     DB-Update:
#       - sql.update_hypothesis(hid, status=?, score=?, confidence=?, last_tested=?)
#
#     Response JSON:
#       200: {"ok": true, "id": <id>, "status": <status|None>, "action": <action|None>}
#       400: {"ok": false, "error": "..."}
#       500: {"ok": false, "error": "Update fehlgeschlagen"}
#
# BETRIEB / PERFORMANCE
# ─────────────────────
# - Writes sind klein (1 row).
# - List ist auf 50 Items begrenzt (UI-freundlich).
# - ensure_schema() wird bei API-Calls aufgerufen (idempotent; safe für Deploys).
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - url_prefix="/research" muss stabil bleiben (UI-Links).
# - status-Werte ("open","confirmed","rejected") sind Vertrag (UI + evtl. Tools).
# - last_tested Semantik (nur bei status/action) beibehalten (Audit-Logik).
# - Keine Delete-Route hinzufügen ohne Governance/Policy (non-destructive).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
from flask import Blueprint, render_template, request, jsonify
import time

from core import sql_manager as sql

bp = Blueprint("research_ui", __name__, url_prefix="/research")

# ----------------------------- UI-Seite -------------------------------------

@bp.route("/")
def page():
    return render_template("research.html")

# ----------------------------- API: Neue Hypothese ---------------------------

@bp.route("/api/new", methods=["POST"])
def api_new():
    sql.ensure_schema()
    data = request.get_json(force=True)
    txt = (data.get("text") or "").strip()
    if not txt:
        return jsonify({"ok": False, "error": "Text fehlt"}), 400

    ts = int(time.time())
    hid = sql.insert_hypothesis(ts=ts, text=txt)  # Defaults: status='open', score=0.0, confidence=0.0
    if not hid:
        return jsonify({"ok": False, "error": "Insert fehlgeschlagen"}), 500
    return jsonify({"ok": True, "id": hid, "text": txt})

# ----------------------------- API: Liste -----------------------------------

@bp.route("/api/list", methods=["GET"])
def api_list():
    sql.ensure_schema()
    limit = 50
    with sql.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, text, created, status, score, confidence, last_tested "
            "FROM hypotheses ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = cur.fetchall()

    items = []
    for r in rows:
        created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created"])) if r.get("created") else "-"
        last_tested_str = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["last_tested"]))
            if r.get("last_tested") else "-"
        )
        items.append({
            "id": r["id"],
            "text": r["text"],
            "created": created_str,
            "status": r.get("status", "open"),
            "score": r.get("score", 0.0),
            "confidence": r.get("confidence", 0.0),
            "last_tested": last_tested_str
        })
    return jsonify({"ok": True, "items": items})

# ----------------------------- API: Update ----------------------------------

@bp.route("/api/update/<int:hypo_id>", methods=["POST"])
def api_update(hypo_id: int):
    sql.ensure_schema()
    data = request.get_json(force=True) or {}

    status = data.get("status")
    action = data.get("action")
    score = data.get("score")
    confidence = data.get("confidence")

    # Validierung Status/Action
    if status not in (None, "confirmed", "rejected", "open"):
        return jsonify({"ok": False, "error": "Ungültiger Status"}), 400
    if action and action != "retest":
        return jsonify({"ok": False, "error": "Ungültige Action"}), 400

    last_tested = int(time.time()) if (status or action == "retest") else None
    ok = sql.update_hypothesis(
        hypo_id,
        status=status,
        score=score,
        confidence=confidence,
        last_tested=last_tested
    )
    if not ok:
        return jsonify({"ok": False, "error": "Update fehlgeschlagen"}), 500
    return jsonify({"ok": True, "id": hypo_id, "status": status, "action": action})
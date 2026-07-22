#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/replay_ui.py
# Projekt:   ORÓMA (Flask UI · Headless · Replay Control)
# Modul:     Replay UI – Blueprint + JSON-API für core.replay_manager (Start/Pause/Resume/Stop/Status) + minimale Fehlerhärtung
# Version:   v3.7.3+replay-consolidation-v1
# Stand:     2026-06-25
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist die UI-Schicht für ORÓMAs Replay-System. Es stellt:
#   - eine HTML-Seite bereit (templates/replay.html)
#   - eine kleine JSON-API zur Steuerung eines laufenden Replays
#
# WICHTIG: Dieses Modul implementiert bewusst **keine** Replay-Logik.
# Alle Aktionen delegieren an:
#   core.replay_manager
#
# Damit bleibt der Core (Replay) sauber getrennt von Flask/UI.
#
# BLUEPRINT / ROUTING
# ───────────────────
# Blueprint:
#   bp = Blueprint("replay", __name__, url_prefix="/replay")
#
# UI:
#   GET  /replay/                 → replay.html
#
# API:
#   GET  /replay/api/status       → Replay-Status als JSON
#   POST /replay/api/start        → Startet Replay (JSON Body: chain_id, speed)
#   POST /replay/api/pause        → Pausiert Replay
#   POST /replay/api/resume       → Setzt Replay fort
#   POST /replay/api/stop         → Stoppt Replay
#
# API-VERTRAG (start)
# ───────────────────
# Request JSON:
#   {
#     "chain_id": <int>,   # Pflicht, >0
#     "speed": <float>     # optional, Default 1.0
#   }
#
# Verhalten:
# - chain_id <= 0 → HTTP 400 {ok:false, error:"Ungültige chain_id"}
# - sonst: replay_manager.start(chain_id=..., speed=...) und HTTP 200 {ok:true,...}
#
# STATUS-ENDPOINT
# ───────────────
# GET /replay/api/status liefert:
#   { ok:true, status: <dict> }
# wobei status aus replay_manager.status() stammt (oder {}).
#
# IMPORT-/BUILD-FEHLER: replay_manager fehlt
# ─────────────────────────────────────────
# Wenn core.replay_manager nicht importierbar ist:
# - replay_manager wird auf None gesetzt
# - alle API-Endpunkte liefern:
#     HTTP 500 {ok:false, error:"replay_manager Modul fehlt"}
#
# Typische Ursachen:
# - ImportError im Core (z. B. fehlende Datei/Dependency)
# - fehlerhafter Deploy (ZIP unvollständig)
# - selten: zirkulärer Import
#
# SECURITY / TOKEN
# ────────────────
# Dieses Modul implementiert keine Authentifizierung.
# Token/Rate-Limits werden zentral in ui/flask_ui.py (bzw. run_oroma.py Setup) umgesetzt.
# Konsequenz:
# - Alle /api/* Endpoints gelten als „protected by outer layer“.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Keine Replay-Logik in der UI: Delegation an core.replay_manager muss bleiben.
# - URL-Pfade müssen stabil bleiben (Frontend/Buttons/JS verlassen sich darauf).
# - Fehlerverhalten „replay_manager Modul fehlt“ muss stabil bleiben (Debugbarkeit).
# - Headless: keine lokalen GUI-Dialoge/Blocking Calls.
#
# =============================================================================
# END HEADER
# =============================================================================

import os
from flask import Blueprint, jsonify, render_template, request

try:
    from core import replay_manager
except ImportError:
    replay_manager = None

bp = Blueprint("replay", __name__, url_prefix="/replay")

# ------------------------- UI-Seite -------------------------

@bp.route("/")
def index():
    """Replay-Seite (Web-UI)"""
    return render_template("replay.html")

# ------------------------- API: Status ----------------------

@bp.route("/api/status")
def api_status():
    """Aktueller Replay-Status"""
    if not replay_manager:
        return jsonify({"ok": False, "error": "replay_manager Modul fehlt"}), 500
    try:
        st = replay_manager.status() or {}
        pretty = bool(request.args.get("pretty"))
        return jsonify({"ok": True, "status": st if not pretty else st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------- API: Start -----------------------

@bp.route("/api/start", methods=["POST"])
def api_start():
    """Starte ein Replay für SnapChain"""
    if not replay_manager:
        return jsonify({"ok": False, "error": "replay_manager Modul fehlt"}), 500
    try:
        data = request.get_json(force=True) or {}
        chain_id = int(data.get("chain_id", 0))
        speed = float(data.get("speed", 1.0))
        if chain_id <= 0:
            return jsonify({"ok": False, "error": "Ungültige chain_id"}), 400
        replay_manager.start(chain_id=chain_id, speed=speed)
        return jsonify({"ok": True, "msg": f"Replay gestartet für Chain {chain_id} @ Speed {speed}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------- API: Pause -----------------------

@bp.route("/api/pause", methods=["POST"])
def api_pause():
    if not replay_manager:
        return jsonify({"ok": False, "error": "replay_manager Modul fehlt"}), 500
    try:
        replay_manager.pause()
        return jsonify({"ok": True, "msg": "Replay pausiert"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------- API: Resume ----------------------

@bp.route("/api/resume", methods=["POST"])
def api_resume():
    if not replay_manager:
        return jsonify({"ok": False, "error": "replay_manager Modul fehlt"}), 500
    try:
        replay_manager.resume()
        return jsonify({"ok": True, "msg": "Replay fortgesetzt"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------- API: Stop ------------------------

@bp.route("/api/stop", methods=["POST"])
def api_stop():
    if not replay_manager:
        return jsonify({"ok": False, "error": "replay_manager Modul fehlt"}), 500
    try:
        replay_manager.stop()
        return jsonify({"ok": True, "msg": "Replay gestoppt"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

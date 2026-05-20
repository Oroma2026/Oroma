#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/vs_ui.py
# Projekt: ORÓMA
# Modul:   UI-Blueprint – „Oroma vs Oroma – Pong Arena“
# Version: v3.1
# Stand:   2025-09-30
#
# Zweck
# ─────
#   Flask-Blueprint, der die headless Pong-Umgebung (core.pong_arena.PongArena)
#   als kleine Web-Oberfläche bereitstellt:
#     • GET  /vs/         → HTML-Seite (Canvas + Buttons)
#     • GET  /vs/state    → JSON-Zustand der Arena (für Polling)
#     • POST /vs/cmd      → Steuerbefehle: start | stop | reset
#
# Öffentliche Exporte
# ───────────────────
#   • bp     : Flask Blueprint (normaler Name)
#   • vs_bp  : Alias für Abwärtskompatibilität (z. B. import in games_ui.py)
#
# Abhängigkeiten
# ──────────────
#   • Flask (Blueprint, request, current_app, render_template, jsonify)
#   • core.pong_arena.PongArena
#
# Hinweise zur Integration
# ────────────────────────
#   • In App-Factory registrieren: app.register_blueprint(vs_bp)
#   • Arena wird pro Flask-Prozess einmal erzeugt und im app.config gehalten:
#       - Schlüssel: "_pong_arena"
#       - Threadsicherheit über Lock in app.config["_pong_arena_lock"]
#   • Bei Gunicorn mit mehreren Workern existiert pro Worker eine Arena-Instanz.
#
# Fehlerbehandlung
# ───────────────
#   • Ungültige Kommandos → HTTP 400 mit {"ok": False, "error": "..."}
#   • Unerwartete Exceptions → HTTP 500 (JSON mit Fehlertext), Log via app.logger
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

import threading
from flask import Blueprint, jsonify, render_template, request, current_app
from core.pong_arena import PongArena

bp = Blueprint("vs", __name__, url_prefix="/vs")
# Alias für bestehenden Code, der `vs_bp` importiert:
vs_bp = bp


def _get_arena() -> PongArena:
    """
    Liefert die (prozessweite) Arena-Instanz. Erzeugt sie einmalig und
    speichert sie im app.config. Zugriff ist über ein Lock geschützt.
    """
    app = current_app
    lock_key = "_pong_arena_lock"
    arena_key = "_pong_arena"

    # Lock einmalig anlegen
    if lock_key not in app.config:
        app.config[lock_key] = threading.Lock()

    with app.config[lock_key]:
        if arena_key not in app.config:
            app.logger.info("[vs_ui] Erzeuge neue PongArena-Instanz")
            app.config[arena_key] = PongArena()
        return app.config[arena_key]


@bp.route("/")
def page():
    # vs.html muss in templates/ liegen (z. B. /opt/ai/oroma/ui/templates/vs.html)
    return render_template("vs.html")


@bp.get("/state")
def api_state():
    try:
        st = _get_arena().state()
        return jsonify(st), 200
    except Exception as e:
        current_app.logger.exception("Fehler in /vs/state: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/cmd")
def api_cmd():
    try:
        data = request.get_json(force=True, silent=False) or {}
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ungültiges JSON: {e}"}), 400

    cmd = (data.get("cmd") or "").strip().lower()
    arena = _get_arena()

    try:
        if cmd == "start":
            arena.start()
        elif cmd == "stop":
            arena.stop()
        elif cmd == "reset":
            arena.reset()
        else:
            return jsonify({"ok": False, "error": f"Unbekanntes Kommando: {cmd}"}), 400

        return jsonify({"ok": True, "state": arena.state()}), 200

    except Exception as e:
        current_app.logger.exception("Fehler in /vs/cmd (%s): %s", cmd, e)
        return jsonify({"ok": False, "error": str(e)}), 500
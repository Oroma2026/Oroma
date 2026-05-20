#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
/opt/ai/oroma/ui/pong_panel_ui.py – ORÓMA v3.7 (Produktiv, Headless)
===============================================================================
Stand:   2025-10-04
Autor:   ORÓMA · KI-JWG-X1
Projekt: ORÓMA – Dashboard-Komponenten

Zweck
-----
Stellt das kompakte Pong-Live-Panel unter /pong_panel bereit.
Dient zur Einbettung in Dashboard-Seiten wie /control oder /learning.

Beschreibung
------------
• Rendert das Template ui/templates/pong_panel.html
• Zeigt Live-ASCII-Darstellung des Spiels Pong (API-basiert)
• Kein Canvas, kein pygame, 100 % Headless-kompatibel
• Datenquelle: /games/pong/state (bereitgestellt durch ui/pong_ui.py)

Sicherheits- & Integrationshinweise
-----------------------------------
• Falls Umgebungsvariable OROMA_UI_TOKEN gesetzt ist, wird Token-Header geprüft.
• Kann eigenständig oder eingebettet per <iframe src="/pong_panel"> verwendet werden.
• Registrierung erfolgt über:
      from ui.pong_panel_ui import pong_panel_bp
      app.register_blueprint(pong_panel_bp)

Dateien
-------
• Template:  /opt/ai/oroma/ui/templates/pong_panel.html
• Blueprint: /opt/ai/oroma/ui/pong_panel_ui.py

===============================================================================
"""

import os
from flask import Blueprint, render_template, request, make_response

# -----------------------------------------------------------------------------
# Blueprint-Definition
# -----------------------------------------------------------------------------
pong_panel_bp = Blueprint(
    "pong_panel",
    __name__,
    url_prefix="/pong_panel",
    template_folder="templates"
)

# -----------------------------------------------------------------------------
# Optionaler Token-Check
# -----------------------------------------------------------------------------
def _check_auth() -> bool:
    """Prüft, ob UI-Token vorhanden und korrekt ist (optional)."""
    tok_env = os.environ.get("OROMA_UI_TOKEN", "").strip()
    if not tok_env:
        return True
    tok_req = (
        request.headers.get("X-OROMA-TOKEN")
        or request.args.get("token")
        or request.cookies.get("OROMA_UI_TOKEN")
        or ""
    ).strip()
    return tok_req == tok_env

@pong_panel_bp.before_request
def _auth_middleware():
    if not _check_auth():
        return make_response("Unauthorized", 401)

# -----------------------------------------------------------------------------
# Route: /pong_panel/
# -----------------------------------------------------------------------------
@pong_panel_bp.route("/", methods=["GET"])
def page():
    """Rendert das Pong-Panel-Template."""
    return render_template("pong_panel.html")
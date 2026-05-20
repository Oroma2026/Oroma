#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/admin.py
# Projekt: ORÓMA
# Modul:   Admin-Endpoints (Shutdown)
# Version: v1.0
# Stand:   2025-10-03
# =============================================================================
from __future__ import annotations

import os
import json
import threading
from flask import Blueprint, request, abort, jsonify

from core.graceful import graceful_shutdown

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

def _check_token() -> None:
    expect = os.environ.get("OROMA_UI_TOKEN", "").strip()
    if not expect:
        return  # kein Token gesetzt → offen (nur intern nutzen!)
    got = request.headers.get("X-OROMA-TOKEN", "").strip()
    if got != expect:
        abort(401)

@admin_bp.route("/shutdown", methods=["POST"])
def shutdown():
    """Beendet den Prozess nach sauberem Stop der Ressourcen."""
    _check_token()

    # Non-blocking: erst HTTP 200 zurückgeben, dann runterfahren
    def _do():
        try:
            graceful_shutdown(reason="admin_endpoint", exit_process=True, exit_code=0)
        except Exception:
            # Fallback, falls irgendwas hängen bleibt
            os._exit(0)

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "action": "shutdown"}), 200
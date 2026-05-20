#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/chat_ui.py
# Projekt: ORÓMA – Headless UI (Flask)
# Modul:   Chat – UI & API (LLM-Dialog)
# Version: v3.8.2 (GET offen; Token-Gate für POST; kein require_ui_token-Import)
# Stand:   2025-11-02
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Routen
# ──────
#   GET  /chat/               → chat.html (offen)
#   POST /chat/api/load_model → Modell laden (Token falls konfiguriert)
#   POST /chat/api/send       → Nachricht senden (Token falls konfiguriert)
# =============================================================================

from __future__ import annotations

import logging
import os
from typing import Optional, Any, Dict

from flask import Blueprint, render_template, jsonify, request, make_response

try:
    from wrappers import text_wrapper  # type: ignore
except Exception as e:
    text_wrapper = None
    logging.getLogger("oroma.chat_ui").error("text_wrapper fehlt: %s", e)

bp = Blueprint("chat", __name__, url_prefix="/chat")
_log = logging.getLogger("oroma.chat_ui")

_runner = None

# ------------------------------- Token-Gate ----------------------------------

def _cfg_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token() -> Optional[str]:
    h = request.headers.get("X-OROMA-TOKEN")
    if h:
        return h.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = request.args.get("token")
    if q:
        return q.strip()
    c = request.cookies.get("OROMA_UI_TOKEN")
    if c:
        return c.strip()
    return None

def _token_valid() -> bool:
    cfg = _cfg_token()
    if not cfg:
        return True
    return _extract_token() == cfg

@bp.before_request
def _guard_mutations():
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not _token_valid():
        return make_response(("Unauthorized", 401))


# --------------------------------- Helpers -----------------------------------

def _ensure_runner():
    """Initialisiert einen TextRunner als Singleton."""
    global _runner
    if _runner is None and text_wrapper:
        _runner = text_wrapper.TextRunner()
        _log.info("TextRunner initialisiert.")
    return _runner


# --------------------------------- UI ----------------------------------------

@bp.get("/")
def page():
    """Chat-Seite rendern (Model-Liste laden, falls möglich)."""
    models = []
    if text_wrapper:
        try:
            models = text_wrapper.TextRunner().list_models()
        except Exception as e:
            _log.error("Modelle konnten nicht geladen werden: %s", e, exc_info=True)
    return render_template("chat.html", models=models)


# --------------------------------- API ---------------------------------------

@bp.post("/api/load_model")
def api_load_model():
    if not text_wrapper:
        return jsonify({"ok": False, "error": "text_wrapper nicht verfügbar"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        model_path = str(data.get("model_path", "")).strip()
        if not model_path:
            return jsonify({"ok": False, "error": "Kein Modell angegeben"}), 400
        runner = _ensure_runner()
        backend = runner.load_model(model_path)
        return jsonify({"ok": True, "model": model_path, "backend": backend})
    except Exception as e:
        _log.error("Fehler in api_load_model: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api/send")
def api_send():
    runner = _ensure_runner()
    if not runner:
        return jsonify({"ok": False, "error": "Kein Modell geladen"}), 400
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        prompt = str(data.get("prompt", "")).strip()
        if not prompt:
            return jsonify({"ok": False, "error": "Leere Eingabe"}), 400
        resp = runner.chat(prompt)
        return jsonify({"ok": True, "response": resp})
    except Exception as e:
        _log.error("Fehler in api_send: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/models_ui.py
# Projekt:   ORÓMA (Flask UI · Headless)
# Modul:     Models UI – Modelle-Dashboard + API (Status, LLM/Vision/Audio Auswahl) via core.model_registry
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses UI-Modul stellt die Modell-Übersicht bereit und erlaubt das „Umschalten“
# der ausgewählten Laufzeit-Modelle (LLM/Vision/Audio) über eine klare JSON-API.
#
# WICHTIG: WAS „LOAD“ IN DIESEM BUILD BEDEUTET
# ───────────────────────────────────────────
# Die API-Calls load_llm/load_vision/load_audio delegieren an core.model_registry.
# In deinem aktuellen Stand sind diese Loader in model_registry ein „Mock/Minimal-State“:
#   - sie laden keine echten Gewichte, sondern setzen nur einen Auswahl-State
#   - status() gibt den State zurück
#
# Vorteil:
#   - UI bleibt stabil und kann später ohne API-Änderung an echte Runtimes
#     (z. B. core.llm_runtime, vision backends, whisper.cpp) angebunden werden.
#
# BLUEPRINT
# ─────────
#   Blueprint: "models"
#   url_prefix: /models
#
# ROUTES
# ──────
# UI:
#   GET  /models/                 → templates/models.html (zeigt verfügbare Modelle)
#
# API (GET offen, Mutationen optional token-geschützt):
#   GET  /models/api/status       → {ok:true, status:{llm,vision,audio}}
#   POST /models/api/llm/load     → {name} → model_registry.load_llm(name)
#   POST /models/api/vision/load  → {name, backend?} → model_registry.load_vision(name, backend)
#   POST /models/api/audio/load   → {name, kind?} → model_registry.load_audio(name, kind)
#
# SECURITY / TOKEN-GATE (WICHTIG)
# ──────────────────────────────
# Dieses Modul implementiert ein eigenes Token-Gate:
#   - GET ist immer offen (UI kann laden)
#   - POST/PUT/PATCH/DELETE verlangen Token, WENN OROMA_UI_TOKEN gesetzt ist
#
# Token-Extraktion (Reihenfolge):
#   1) Header: X-OROMA-TOKEN
#   2) Authorization: Bearer <token>
#   3) Query: ?token=<token>
#   4) Cookie: OROMA_UI_TOKEN
#
# Wenn OROMA_UI_TOKEN leer ist → Mutationen sind frei (token-free Modus).
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
#   OROMA_UI_TOKEN=""|"<secret>"
#
# DEPENDENCIES / FALLBACKS
# ───────────────────────
# - core.model_registry ist optional importiert:
#     Wenn Import fehlschlägt → UI/API liefert {ok:false, error:"Registry fehlt"}.
# - Dieses Modul enthält absichtlich keine heavy ML-Imports.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, render_template, jsonify, request, make_response

# Optional: Model-Registry
try:
    from core import model_registry  # type: ignore
except Exception:
    model_registry = None  # type: ignore

bp = Blueprint("models", __name__, url_prefix="/models")


# ------------------------------- Token-Gate ----------------------------------

def _cfg_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token() -> Optional[str]:
    # 1) eigener Header
    h = request.headers.get("X-OROMA-TOKEN")
    if h:
        return h.strip()
    # 2) Bearer
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 3) Query
    q = request.args.get("token")
    if q:
        return q.strip()
    # 4) Cookie
    c = request.cookies.get("OROMA_UI_TOKEN")
    if c:
        return c.strip()
    return None

def _token_valid() -> bool:
    cfg = _cfg_token()
    if not cfg:
        return True  # Token nicht konfiguriert → frei
    return _extract_token() == cfg

@bp.before_request
def _guard_mutations():
    """
    GET ist offen; schreibende Methoden verlangen Token (falls konfiguriert).
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not _token_valid():
        return make_response(("Unauthorized", 401))


# --------------------------------- UI ----------------------------------------

@bp.get("/")
def page() -> Any:
    if not model_registry:
        return "<h3>❌ Modelle-Registry nicht verfügbar</h3>", 500
    try:
        cfg = model_registry.available_models()
    except Exception as e:
        return f"<h3>❌ Modelle-Registry Fehler: {e}</h3>", 500
    return render_template("models.html", cfg=cfg or {})


# --------------------------------- API ---------------------------------------

@bp.get("/api/status")
def api_status():
    if not model_registry:
        return jsonify({"ok": False, "error": "Registry fehlt"}), 500
    try:
        st = model_registry.status()
        return jsonify({"ok": True, "status": st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api/llm/load")
def api_llm_load():
    if not model_registry:
        return jsonify({"ok": False, "error": "Registry fehlt"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        name = str(data.get("name", "")).strip()
        if not name:
            return jsonify({"ok": False, "error": "name fehlt"}), 400
        res = model_registry.load_llm(name)
        # Erwartet ein Dict mit ok/err – sonst normieren:
        if isinstance(res, dict) and "ok" in res:
            return jsonify(res)
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api/vision/load")
def api_vision_load():
    if not model_registry:
        return jsonify({"ok": False, "error": "Registry fehlt"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        name = str(data.get("name", "")).strip()
        backend = str(data.get("backend", "onnx")).strip() or "onnx"
        if not name:
            return jsonify({"ok": False, "error": "name fehlt"}), 400
        res = model_registry.load_vision(name, backend=backend)
        if isinstance(res, dict) and "ok" in res:
            return jsonify(res)
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api/audio/load")
def api_audio_load():
    if not model_registry:
        return jsonify({"ok": False, "error": "Registry fehlt"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        name = str(data.get("name", "")).strip()
        kind = str(data.get("kind", "whisper")).strip() or "whisper"
        if not name:
            return jsonify({"ok": False, "error": "name fehlt"}), 400
        res = model_registry.load_audio(name, kind=kind)
        if isinstance(res, dict) and "ok" in res:
            return jsonify(res)
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
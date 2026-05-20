#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/gaps_ui.py
# Projekt: ORÓMA – Headless (Flask-UI)
# Version: v3.7-prod2 (HTML immer frei; API: PUBLIC-Flag oder Token)
# Stand:   2025-11-01
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
# UI & API für Knowledge-Gaps.
#
#   HTML (immer frei):
#     GET  /gaps/                    → ui/templates/gaps.html
#
#   API (zwei Modi):
#     • PUBLIC-Flag:  OROMA_GAPS_PUBLIC=1  → /gaps/api/* ohne Token
#     • Token-Modus:  OROMA_UI_TOKEN gesetzt → /gaps/api/* verlangen Token
#       (X-OROMA-TOKEN / Bearer / ?token= / Cookie OROMA_UI_TOKEN)
#
# Rückgabe
# ────────
#   OK:  {"ok": true, ...}
#   ERR: {"ok": false, "error": "..."} + HTTP 4xx/5xx
#   Cache-Control: no-store auf API-Routen
# =============================================================================

from __future__ import annotations
import os
import logging
from typing import Optional
from flask import Blueprint, render_template, jsonify, request

# Core optional laden – robust
try:
    from core import gaps  # erwartet get_summary() / list_gaps()
except Exception:  # pragma: no cover
    gaps = None  # type: ignore

bp = Blueprint("gaps", __name__, url_prefix="/gaps")
log = logging.getLogger("oroma.ui.gaps")
log.setLevel(logging.INFO)

# ----------------------------- Config -----------------------------

def _public_api_enabled() -> bool:
    """Wenn OROMA_GAPS_PUBLIC=1 → API ohne Token zugänglich."""
    return (os.environ.get("OROMA_GAPS_PUBLIC", "").strip() in ("1", "true", "yes", "on"))

def _cfg_token() -> str:
    """Konfigurierter UI-Token (leer → kein Token gesetzt)."""
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token_from_request() -> Optional[str]:
    """Token aus Request (Header/Query/Cookie) extrahieren."""
    # 1) X-OROMA-TOKEN
    t = request.headers.get("X-OROMA-TOKEN")
    if t:
        return t.strip()
    # 2) Authorization: Bearer <token>
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 3) ?token=
    q = request.args.get("token")
    if q:
        return q.strip()
    # 4) Cookie
    c = request.cookies.get("OROMA_UI_TOKEN")
    if c:
        return c.strip()
    return None

def _token_valid_for_api() -> bool:
    """API-Gate: PUBLIC-Flag oder korrekter Token."""
    if _public_api_enabled():
        return True
    cfg = _cfg_token()
    if not cfg:
        # Kein Token konfiguriert → API offen
        return True
    incoming = _extract_token_from_request()
    ok = (incoming == cfg)
    if not ok:
        log.warning("Gaps-API Tokenprüfung fehlgeschlagen (remote=%s path=%s)",
                    request.remote_addr, request.path)
    return ok

def _json_error(message: str, status: int = 401):
    resp = jsonify({"ok": False, "error": message})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp

# ----------------------------- Guards -----------------------------

@bp.before_request
def _guard_api_only():
    """
    HTML (/gaps, /gaps/) bleibt stets frei.
    Nur /gaps/api/* wird – je nach Config – geschützt.
    """
    p = request.path or ""
    if p.startswith("/gaps/api"):
        if not _token_valid_for_api():
            return _json_error("Unauthorized", 401)
    # HTML oder autorisierte API → durchlassen
    return None

# ----------------------------- Routes -----------------------------

@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
def page():
    """
    Rendert immer (keine DB-Zugriffe hier).
    Falls Template fehlen sollte, liefern wir eine Minimal-Seite statt 500.
    """
    try:
        # optional Token ins Template injizieren (kann leer sein)
        return render_template("gaps.html", ui_token=_cfg_token(),
                               public=_public_api_enabled())
    except Exception as e:
        # Harte Fallbacks verhindern 500
        log.error("gaps.html Rendering-Fehler: %s", e)
        return (
            "<h1>Gaps</h1><p>Template fehlt/fehlerhaft.</p>"
            "<p>API: <code>/gaps/api/summary</code>, <code>/gaps/api/list</code></p>",
            200,
            {"Content-Type": "text/html; charset=utf-8"}
        )

@bp.route("/api/summary", methods=["GET"])
def api_summary():
    if not gaps:
        return _json_error("gaps core fehlt", 500)
    try:
        summary = gaps.get_summary()
        resp = jsonify({"ok": True, "summary": summary})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        log.exception("Fehler in /gaps/api/summary: %s", e)
        return _json_error(str(e), 500)

@bp.route("/api/list", methods=["GET"])
def api_list():
    if not gaps:
        return _json_error("gaps core fehlt", 500)
    # Parameter
    try:
        limit = int(request.args.get("limit", "100"))
        if limit < 1:
            raise ValueError("limit muss >= 1 sein")
        limit = min(limit, 1000)
    except ValueError as ve:
        return _json_error(f"bad request: {ve}", 400)
    # Daten
    try:
        items = gaps.list_gaps(limit=limit)
        resp = jsonify({"ok": True, "items": items})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        log.exception("Fehler in /gaps/api/list: %s", e)
        return _json_error(str(e), 500)
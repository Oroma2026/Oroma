#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/flask_ui.py
# Projekt:   ORÓMA (Flask UI · Headless)
# Modul:     Flask App Core – zentrales `app` Objekt + schlanker Token-Guard (nur /api/*) + Auth-Utility Endpoints + Cookie-Komfort
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# WICHTIG – ROLLE DIESER DATEI
# ───────────────────────────
# Diese Datei stellt das zentrale Flask-App-Objekt `app` bereit und implementiert
# ausschließlich den minimalen UI-Rahmen:
#   - App-Instanz mit stabilen Template-/Static-Pfaden
#   - einen leichten Token-Guard für API-Routen
#   - kleine Auth-Hilfs-Endpunkte (ping/status/logout)
#   - eine harmlose Index-Route ("/")
#
# Fachlogik (Learning/Replay/Control/Models/Games/…) ist in separaten Blueprints
# und wird in run_oroma.py registriert (safe_register).
#
# DESIGNENTSCHEIDUNG: TOKEN-GUARD NUR FÜR /api/*
# ───────────────────────────────────────────────
# In v3.7.3 ist das UI bewusst so aufgebaut:
#   - HTML-Seiten ("/", "/replay", "/learning", …) und /static bleiben offen
#   - NUR /api/* ist geschützt, wenn OROMA_UI_TOKEN gesetzt ist
#
# Motivation:
# - UX/Debug: die UI-Seiten müssen überhaupt erreichbar sein, um Token zu testen
# - In lokalen Netzen kann OROMA_UI_TOKEN leer bleiben (token-free Modus)
# - Maschinenzugriffe (JS-Fetch, curl, Tools) laufen über /api/* und sind damit kontrollierbar
#
# TOKEN-QUELLEN (EXAKTE REIHENFOLGE IM CODE)
# ─────────────────────────────────────────
# _extract_token_from_request() akzeptiert (erste gefundene Quelle gewinnt):
#   1) Header: X-OROMA-TOKEN: <token>
#   2) Header: Authorization: Bearer <token>
#   3) Query : ?token=<token>
#   4) Cookie: OROMA_UI_TOKEN=<token>
#   5) JSON  : {"token":"<token>"}  (v. a. /api/auth/ping)
#
# TOKEN-REQUIRED / TOKEN-VALID
# ────────────────────────────
# - Wenn ENV OROMA_UI_TOKEN leer ist:
#     → token_required = False → alles gilt als gültig (offener Modus)
# - Wenn ENV OROMA_UI_TOKEN gesetzt ist:
#     → token_required = True → tok muss exakt == OROMA_UI_TOKEN sein
#
# REQUEST-GUARD (before_request)
# ─────────────────────────────
# @app.before_request:
#   - /static/* und /favicon.ico immer durchlassen
#   - wenn path.startswith("/api/"):
#       • /api/auth/* wird NICHT vom Guard geblockt (prüft intern selbst)
#       • alle anderen /api/* → bei ungültigem Token abort(401)
#
# EINHEITLICHE API-401 ANTWORT
# ────────────────────────────
# @app.errorhandler(401):
#   - für /api/* liefert JSON:
#       {"ok": False, "error": "unauthorized"}
#   - für HTML wird Standard-Handler genutzt
#
# COOKIE-KOMFORT (after_request)
# ─────────────────────────────
# @app.after_request setzt bei gültigem Token (und nur wenn token_required=True) ein Cookie:
#   - Name:    OROMA_UI_TOKEN
#   - TTL:     7 Tage
#   - httponly=False (bewusst: Frontend/JS darf lesen)
#   - samesite="Lax"
#   - secure wird best effort gesetzt, wenn:
#       • ENV FLASK_SECURE_COOKIE=true
#       • oder Header X-Forwarded-Proto=https (Reverse-Proxy)
#
# ROUTES (DIESER DATEI)
# ─────────────────────
# - GET  /api/auth/status   → {"token_required":bool, "token_valid":bool}
# - GET/POST /api/auth/ping → validiert Token (aus Quellen) und erlaubt Cookie-Set
# - POST /api/auth/logout   → löscht Cookie
# - GET  /                 → templates/index.html wenn vorhanden, sonst minimale "UI läuft" Antwort
#
# TEMPLATE/STATIC PFADE (STABIL, WICHTIG FÜR DEPLOYS)
# ───────────────────────────────────────────────────
# app wird mit absoluten Pfaden initialisiert:
#   template_folder = /opt/ai/oroma/ui/templates
#   static_folder   = /opt/ai/oroma/ui/static
# So funktionieren Assets unabhängig davon, ob ORÓMA aus /opt/ai/oroma oder via PYTHONPATH
# gestartet wird.
#
# ENV
# ───
# - OROMA_UI_TOKEN=<token>              (leer → token-free Modus)
# - FLASK_SECURE_COOKIE=true|false      (erzwingt secure Cookie)
# - (Reverse-Proxy) X-Forwarded-Proto=https wird automatisch erkannt
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Token-Guard muss NUR /api/* schützen (HTML offen lassen).
# - Token-Quellen-Reihenfolge muss stabil bleiben (Kompatibilität zu Tools/UI).
# - after_request Cookie-Set ist Absicht (UX; verhindert Header-Pflege im Frontend).
# - secure Cookie Entscheidung bleibt best effort (Proxy-Setups).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
from typing import Optional

import logging
from core.log_guard import log_suppressed

from flask import (
    Flask,
    request,
    abort,
    make_response,
    render_template,
    jsonify,
)

HERE = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(HERE, "templates"),
    static_folder=os.path.join(HERE, "static"),
)

# -----------------------------------------------------------------------------
# Token-/Cookie-Parameter
# -----------------------------------------------------------------------------
_UI_TOKEN: str = os.environ.get("OROMA_UI_TOKEN", "").strip()
_COOKIE_NAME: str = "OROMA_UI_TOKEN"  # konsistent mit Frontend

def _env_true(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if not v:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")

def _wants_secure_cookie() -> bool:
    """
    secure=True, wenn:
      - ENV FLASK_SECURE_COOKIE=true ODER
      - Request offensichtlich HTTPS (X-Forwarded-Proto=https)
    """
    if _env_true("FLASK_SECURE_COOKIE", False):
        return True
    # Best effort Proxy-Erkennung
    try:
        xf_proto = (request.headers.get("X-Forwarded-Proto") or "").lower()
        if xf_proto == "https":
            return True
    except Exception as e:
        log_suppressed('ui/flask_ui.py:127', exc=e, level=logging.WARNING)
        pass
    return False

# -----------------------------------------------------------------------------
# Token-Extraktion & Validierung
# -----------------------------------------------------------------------------
def _extract_token_from_request() -> Optional[str]:
    """
    Akzeptierte Quellen (Reihenfolge):
      1) Header: X-OROMA-TOKEN
      2) Header: Authorization: Bearer <token>
      3) Query:  ?token=<token>
      4) Cookie: OROMA_UI_TOKEN=<token>
      5) JSON:   { "token": "<token>" }  (z. B. für /api/auth/ping)
    """
    # 1) Direkter Header
    tok = (request.headers.get("X-OROMA-TOKEN") or "").strip()
    if tok:
        return tok

    # 2) Bearer
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    # 3) Query
    tok = (request.args.get("token") or "").strip()
    if tok:
        return tok

    # 4) Cookie
    tok = (request.cookies.get(_COOKIE_NAME) or "").strip()
    if tok:
        return tok

    # 5) JSON-Body (ohne Fehler werfen)
    try:
        data = request.get_json(silent=True) or {}
        tok = str(data.get("token", "")).strip()
        if tok:
            return tok
    except Exception as e:
        log_suppressed('ui/flask_ui.py:170', exc=e, level=logging.WARNING)
        pass

    return None

def _token_required() -> bool:
    # Kein ENV-Token gesetzt → keine Auth erfordern
    return bool(_UI_TOKEN)

def _token_valid(tok: Optional[str]) -> bool:
    # Wenn kein Token konfiguriert ist, gilt alles als gültig (offener Modus)
    if not _token_required():
        return True
    return bool(tok) and (tok == _UI_TOKEN)

# -----------------------------------------------------------------------------
# Request Guards
# -----------------------------------------------------------------------------
@app.before_request
def _auth_guard():
    path = request.path or "/"

    # Statische Dateien & Favicon immer durchlassen
    if path.startswith("/static/") or path == "/favicon.ico":
        return

    # Nur /api/* schützen – HTML frei ladbar (Token kann im UI gesetzt werden)
    if path.startswith("/api/"):
        # Für Auth-Hilfsrouten (ping/status/logout) prüfen wir intern selbst
        if path.startswith("/api/auth/"):
            return
        if not _token_valid(_extract_token_from_request()):
            # Für API konsistent 401 liefern (JSON wird unten standardisiert)
            abort(401)

@app.after_request
def _maybe_set_cookie(resp):
    """
    Komfort: Bei gültigem Token im Request (Header/Query/JSON) setzen wir ein
    Cookie OROMA_UI_TOKEN (7 Tage). So muss das Frontend den Header nicht
    dauerhaft manuell mitsenden.
    """
    tok = _extract_token_from_request()
    if _token_valid(tok) and _token_required():
        try:
            resp.set_cookie(
                _COOKIE_NAME,
                tok,  # sichere Speicherung; httpOnly False, damit Frontend lesen kann
                max_age=7 * 24 * 3600,
                httponly=False,            # JS darf lesen (bewusst)
                samesite="Lax",
                secure=_wants_secure_cookie(),
            )
        except Exception as e:
            log_suppressed('ui/flask_ui.py:224', exc=e, level=logging.WARNING)
            pass
    return resp

# Einheitliche JSON-Antwort für API-Auth-Fehler
@app.errorhandler(401)
def _handle_unauthorized(err):
    if (request.path or "").startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    # HTML-Seiten lassen wir vom Standard-Handler behandeln
    return err

# -----------------------------------------------------------------------------
# Auth-Utilities
# -----------------------------------------------------------------------------
@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    """
    Liefert (immer erreichbar):
      - requires_token: ob /api/* Schutz aktiv ist
      - ok: Token im Request ist gültig (oder nicht nötig)
    """
    tok = _extract_token_from_request()
    req = _token_required()
    return jsonify({
        "ok": _token_valid(tok),
        "requires_token": req,
        "has_cookie": bool(request.cookies.get(_COOKIE_NAME)),
    }), 200

@app.route("/api/auth/ping", methods=["GET", "POST"])
def auth_ping():
    """
    Validiert das Token und setzt (bei Erfolg) das Auth-Cookie.
    - Token kann per Header/Query/JSON geliefert werden.
    - Antwort ist immer JSON.
    """
    tok = _extract_token_from_request()
    if not _token_valid(tok):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401

    # Cookie setzen über after_request; hier nur Erfolg melden
    return jsonify({"ok": True}), 200

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """
    Löscht das Auth-Cookie. Token im Request ist nicht erforderlich.
    """
    resp = make_response(jsonify({"ok": True}), 200)
    try:
        resp.delete_cookie(
            _COOKIE_NAME,
            samesite="Lax",
            secure=_wants_secure_cookie(),
        )
    except Exception as e:
        log_suppressed('ui/flask_ui.py:281', exc=e, level=logging.WARNING)
        pass
    return resp

# -----------------------------------------------------------------------------
# Optionale Index-Seite (verhindert url_for('index')-Fehler)
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception:
        # Minimaler Fallback, falls kein Template existiert
        html = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>ORÓMA</title>"
            "<h1>ORÓMA UI läuft</h1>"
            "<p>Blueprints werden in <code>run_oroma.py</code> registriert.</p>"
        )
        return make_response(html, 200)

# -----------------------------------------------------------------------------
# Dev-/Solo-Start (im Betrieb startet run_oroma.py die UI)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("FLASK_RUN_PORT", "8080"))
    except Exception:
        port = 8080
    app.run(host=host, port=port, debug=False, use_reloader=False)
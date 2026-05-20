#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/why_ui.py
# Projekt:   ORÓMA (Headless · Explainability UI · Flask)
# Modul:     Why-UI („Explainability“) – UI + JSON-API für „Warum diese Entscheidung?“ + Hypothesen-Bridge
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul stellt die Explainability-/Why-Oberfläche bereit.
# Es verbindet drei Dinge:
#   1) UI-Seite: Darstellung letzter Entscheidungen / Erklärungen
#   2) Explainability API: „why_last“ oder „why_decision(context_centroid=...)“
#   3) Research-Bridge: Hypothesen anlegen/listen/updaten (über core.explain Bindings)
#
# Wichtig: Dieses UI ist bewusst „thin“:
# - keine direkte DB-Logik
# - keine schweren Abhängigkeiten
# - Core-Funktionen sind optional (Try-Import), UI bleibt bootfähig.
#
# BLUEPRINT
# ─────────
# bp = Blueprint("why", __name__, url_prefix="/why")
#
# UI:
#   GET /why/ → templates/why.html
#
# API:
#   GET  /why/api/recent              → letzte Entscheidungen (Liste/Tabelle)
#   POST /why/api/why                 → Erklärung: {centroid} → why_decision(), sonst why_last()
#   GET  /why/api/hypotheses          → Liste Hypothesen
#   POST /why/api/hypotheses          → neue Hypothese: {"hypothesis": "..."}
#   POST /why/api/hypotheses/<id>     → update_result: {"score":..,"confidence":..,"status":..}
#
# CORE-BINDINGS (OPTIONAL, EXAKT IM CODE)
# ───────────────────────────────────────
# Dieses Modul versucht zu importieren:
#   from core.explain import (
#       why_decision,
#       get_recent_decisions,
#       why_last,
#       hypotheses_add,
#       hypotheses_list,
#       hypotheses_update_result,
#   )
#
# Wenn dieser Import fehlschlägt:
# - alle Bindings werden auf None gesetzt
# - API-Endpunkte liefern dann {"ok":False,"error":"... not available"} statt Crash
#
# TOKEN-GUARD (LOKALER GUARD NUR FÜR MUTATIONS)
# ────────────────────────────────────────────
# Dieses Modul implementiert bewusst einen lokalen Guard:
# - GET ist offen (lesende Explainability ist oft „read-only dashboard“)
# - Mutations (POST/PUT/PATCH/DELETE) erfordern ein Token, sofern OROMA_UI_TOKEN gesetzt ist
#
# Ablauf:
#   @bp.before_request -> _guard_mutations()
#   - wenn request.method in ("POST","PUT","PATCH","DELETE"):
#       • wenn OROMA_UI_TOKEN leer → erlaubt (token-free mode)
#       • sonst: Token extrahieren und validieren
#
# Token-Quellen (Reihenfolge, exakt im Code):
#   1) Header: X-OROMA-TOKEN
#   2) Authorization: Bearer <token>
#   3) Query: ?token=<token>
#   4) Cookie: OROMA_UI_TOKEN
#
# Validierung:
# - token == configured token (OROMA_UI_TOKEN)
# - bei Invalid:
#     • JSON error + 401 (oder make_response)
#
# Hinweis:
# - Dieser Guard ist bewusst auf das Why-Modul begrenzt.
# - Globale Token-Policy kann zusätzlich in ui/flask_ui.py existieren; dieser Guard
#   bleibt trotzdem sinnvoll, weil er Why-spezifische POST-Endpoints schützt.
#
# API-SEMANTIK (DETAILS)
# ─────────────────────
# GET /api/recent:
# - delegiert an get_recent_decisions(limit=?)
# - liefert {"ok":True,"items":[...]} oder Fehler
# - Items sind „thin“ gehalten (z. B. ts, action, centroid, reward, trace_id …),
#   genaue Struktur kommt aus core.explain.get_recent_decisions().
#
# POST /api/why:
# - Request JSON optional:
#     {"centroid": <any JSON-serialisierbar>}
# - Wenn centroid vorhanden und why_decision verfügbar:
#     res = why_decision(context_centroid=centroid)
# - Sonst, wenn why_last verfügbar:
#     res = why_last()
# - Ergebnis wird normiert:
#     wenn res ein dict mit "ok" ist → direkt zurück
#     sonst → {"ok":True,"result":res}
#
# /api/hypotheses:
# - GET:
#     hypotheses_list() → Liste
# - POST:
#     erwartet {"hypothesis":"..."} (nicht leer)
#     hypotheses_add(text) → id
#
# /api/hypotheses/<id> (POST):
# - delegiert an hypotheses_update_result(hid, payload)
# - payload typischerweise:
#     {"score":0.72,"confidence":0.81,"status":"running|accepted|rejected|open"}
#
# FEHLERSTRATEGIE
# ───────────────
# - JSON parsing: request.get_json(force=True, silent=True) or {}
# - Alle Exceptions werden in {"ok":False,"error":str(e)} gewandelt (HTTP 500),
#   damit UI nicht mit Tracebacks „hängen bleibt“.
#
# AUTH / SECURITY (WICHTIG)
# ────────────────────────
# - Dieses Modul schützt Mutations nur, wenn OROMA_UI_TOKEN gesetzt ist.
# - In token-free mode (OROMA_UI_TOKEN leer) sind POSTs erlaubt (bewusst für lokale Netze).
# - Für produktive Exponierung empfiehlt sich zusätzlich Reverse-Proxy Auth.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - url_prefix="/why" muss stabil bleiben (UI/Links/Bookmarks).
# - Token-Extraktion in der definierten Reihenfolge beibehalten (Kompatibilität zu Clients).
# - GET bleibt read-only offen; Mutations werden über before_request gegated.
# - Core-Bindings bleiben optional (UI darf ORÓMA Boot nicht verhindern).
# - Response Keys ("ok","error","items","result") stabil halten (Templates/JS).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, render_template, request, jsonify, make_response

# Core-Bindings (optional; UI bleibt benutzbar)
try:
    from core.explain import (  # type: ignore
        why_decision,
        get_recent_decisions,
        why_last,
        hypotheses_add,
        hypotheses_list,
        hypotheses_update_result,
    )
except Exception:
    why_decision = None
    get_recent_decisions = None
    why_last = None
    hypotheses_add = None
    hypotheses_list = None
    hypotheses_update_result = None

bp = Blueprint("why", __name__, url_prefix="/why")

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
        return True
    return _extract_token() == cfg

@bp.before_request
def _guard_mutations():
    # GET ist offen; alle schreibenden Methoden brauchen Token (falls konfiguriert)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not _token_valid():
        return make_response(("Unauthorized", 401))


# --------------------------------- UI ----------------------------------------

@bp.get("/")
def page() -> Any:
    return render_template("why.html")


# --------------------------------- API ---------------------------------------

@bp.get("/api/recent")
def api_recent():
    if not get_recent_decisions:
        return jsonify({"ok": False, "error": "no recent decisions"}), 500
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    try:
        rows = get_recent_decisions(limit=limit)
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api")
def api_why():
    """Ad-hoc-Erklärung: {centroid} → why_decision(), sonst why_last()."""
    if not why_decision and not why_last:
        return jsonify({"ok": False, "error": "Explain core not available"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        centroid = data.get("centroid")
        if centroid and why_decision:
            res = why_decision(context_centroid=centroid)
        elif why_last:
            res = why_last()
        else:
            res = {"ok": False, "msg": "Keine Daten verfügbar"}
        # Ergebnis normieren
        if isinstance(res, dict) and "ok" in res:
            return jsonify(res)
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/hypotheses", methods=["GET", "POST"])
def api_hypotheses():
    if request.method == "GET":
        if not hypotheses_list:
            return jsonify({"ok": False, "error": "hypotheses_list not available"}), 500
        try:
            limit = int(request.args.get("limit", 50))
        except Exception:
            limit = 50
        try:
            return jsonify({"ok": True, "rows": hypotheses_list(limit=limit)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # POST (neu)
    if not hypotheses_add:
        return jsonify({"ok": False, "error": "hypotheses_add not available"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        htext = str(data.get("hypothesis", "")).strip()
        if not htext:
            return jsonify({"ok": False, "error": "Keine Hypothese übergeben"}), 400
        hid = hypotheses_add(htext)
        return jsonify({"ok": True, "id": hid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api/hypotheses/<int:hid>")
def api_hypotheses_update(hid: int):
    if not hypotheses_update_result:
        return jsonify({"ok": False, "error": "hypotheses_update_result not available"}), 500
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        result = str(data.get("result", "")).strip()
        if not result:
            return jsonify({"ok": False, "error": "Kein Ergebnis übergeben"}), 400
        ok = hypotheses_update_result(hid, result)
        return jsonify({"ok": bool(ok)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
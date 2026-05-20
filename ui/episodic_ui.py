#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/episodic_ui.py
# Projekt: ORÓMA – Headless UI
# Version: v3.7.3-r1 (HTML frei, API tokenpflichtig; Vision-Episoden & cam_token-API)
# Stand:   2025-12-07
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
# Episoden-UI & API für das episodische Gedächtnis.
#
#   • HTML /episodic
#       – frei zugänglich für einfache Bedienung
#       – zeigt:
#           · klassischen Episoden-Browser (Core-episodic)
#           · Vision-Sessions (Kamera-Autolog via episodes/episode_events)
#
#   • API  /episodic/api/*
#       – tokenpflichtig, wenn OROMA_UI_TOKEN gesetzt
#       – Endpunkte:
#
#       Klassische Episoden (Core-episodic)
#       ──────────────────────────────────
#         POST /episodic/api/create
#             → Episode anlegen (manuell, via core.episodic.save_episode)
#         GET  /episodic/api/get/<id>
#             → Episode laden (core.episodic.load_episode)
#         POST /episodic/api/similar
#             → Ähnlichkeitssuche über Centroid (core.episodic.find_similar)
#         GET  /episodic/api/list[?limit=N]
#             → Liste Episoden (+ Events-Anzahl via get_episode(with_events=True))
#
#       Vision-Episoden (direkt aus episodes/episode_events)
#       ─────────────────────────────────────────────────────
#         GET  /episodic/api/vision/list[?limit=N]
#             → Liste der letzten Vision-Sessions (kind='vision', source='vision/token')
#                mit:
#                  · ts_start, ts_end
#                  · cam_events (Anzahl episode_events mit event_type='cam_token')
#
#         GET  /episodic/api/vision/events/<id>[?limit=N]
#             → letzte N cam_token-Events zu einer Vision-Episode:
#                  · ts, ref_table, ref_id
#                  · origin, q, motion, edges, color (aus meta_json)
#
# Sicherheit
# ──────────
#   • HTML bleibt frei.
#   • API prüft Token, wenn OROMA_UI_TOKEN ≠ "".
#     Tokenquellen: X-OROMA-TOKEN | Authorization: Bearer <t> | ?token= | Cookie OROMA_UI_TOKEN
#
# Integration
# ───────────
#   • Core-Abhängigkeit (klassisch): core/episodic.py mit:
#       save_episode(title, chain_ids, centroid, meta)
#       load_episode(eid)
#       find_similar(centroid, topk=10)
#       list_episodes(limit=50)
#       get_episode(eid, with_events=True)
#
#   • Vision-Episoden:
#       – direkter Zugriff auf core/sql_manager.get_conn()
#       – nutzt Tabellen episodes / episode_events
#       – erwartet:
#           episodes(id, ts_start, ts_end, kind, label, source, meta_json)
#           episode_events(episode_id, ts, event_type, ref_table, ref_id, meta_json)
#
#   • Blueprint-Name: "episodic_ui"
#   • WICHTIG: Exportiert sowohl `episodic_bp` als auch `bp` (Alias) für Kompatibilität.
#
# Cache
# ─────
#   • API-Antworten setzen "Cache-Control: no-store" für frische UI-Daten.
# =============================================================================

from __future__ import annotations

import os
import time
import json
import logging
from typing import Optional, List

from flask import Blueprint, render_template, request, jsonify

# Core (fehlertolerant importieren)
try:
    from core import episodic  # erwartet save_episode, load_episode, find_similar, list_episodes, get_episode
except Exception:  # pragma: no cover
    episodic = None  # type: ignore

try:
    from core import sql_manager  # für Vision-Episoden (episodes/episode_events)
except Exception:  # pragma: no cover
    sql_manager = None  # type: ignore

# Blueprint (interner Name bleibt "episodic_ui")
bp = Blueprint("episodic_ui", __name__, url_prefix="/episodic")

log = logging.getLogger("oroma.ui.episodic")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[episodic] %(levelname)s: %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)

# ------------------------------ Token-Helpers --------------------------------

def _cfg_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token() -> Optional[str]:
    # 1) X-OROMA-TOKEN
    h = request.headers.get("X-OROMA-TOKEN")
    if h:
        return h.strip()
    # 2) Authorization: Bearer <t>
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

def _token_valid() -> bool:
    cfg = _cfg_token()
    if not cfg:
        return True
    incoming = _extract_token()
    ok = (incoming == cfg)
    if not ok:
        log.warning("Tokenprüfung fehlgeschlagen (route=%s, remote=%s)", request.path, request.remote_addr)
    return ok

def _json(data, status: int = 200):
    resp = jsonify(data)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp

def _json_error(msg: str, status: int = 400):
    return _json({"ok": False, "error": msg}, status)

# Nur API schützen, HTML frei lassen
@bp.before_request
def _mw_api_only():
    if request.path.startswith("/episodic/api"):
        if not _token_valid():
            return _json_error("Unauthorized", 401)
    return None

# ------------------------------ HTML ------------------------------------------

@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
def page():
    return render_template("episodic.html")

# ------------------------------ API: klassisch -------------------------------

@bp.route("/api/create", methods=["POST"])
def api_create():
    if not episodic:
        return _json_error("episodic core not available", 500)
    try:
        data = request.get_json(silent=True) or {}
        title = (str(data.get("title") or "")).strip() or f"Episode @{int(time.time())}"
        chain_ids_raw = data.get("chain_ids") or []
        centroid_raw = data.get("centroid") or []

        # Validierung
        try:
            chain_ids: List[int] = [int(x) for x in chain_ids_raw]
        except Exception:
            return _json_error("chain_ids müssen int sein", 400)
        try:
            centroid: List[float] = [float(x) for x in centroid_raw]
        except Exception:
            return _json_error("centroid müssen float sein", 400)

        eid = episodic.save_episode(title, chain_ids, centroid, meta={"ui": "episodic_ui"})
        return _json({"ok": True, "id": int(eid)})
    except Exception as e:  # pragma: no cover
        log.exception("api_create failed: %s", e)
        return _json_error(str(e), 500)

@bp.route("/api/get/<int:eid>", methods=["GET"])
def api_get(eid: int):
    if not episodic:
        return _json_error("episodic core not available", 500)
    try:
        ep = episodic.load_episode(int(eid))
        if not ep:
            return _json_error("not found", 404)
        return _json({"ok": True, "episode": ep})
    except Exception as e:  # pragma: no cover
        log.exception("api_get failed: %s", e)
        return _json_error(str(e), 500)

@bp.route("/api/similar", methods=["POST"])
def api_similar():
    if not episodic:
        return _json_error("episodic core not available", 500)
    try:
        data = request.get_json(silent=True) or {}
        q = [float(x) for x in (data.get("centroid") or [])]
        sim = episodic.find_similar(q, topk=10) or []
        top = [{"id": int(i), "score": float(s)} for i, s in sim]
        return _json({"ok": True, "top": top})
    except Exception as e:  # pragma: no cover
        log.exception("api_similar failed: %s", e)
        return _json_error(str(e), 500)

@bp.route("/api/list", methods=["GET"])
def api_list():
    if not episodic:
        return _json_error("episodic core not available", 500)
    try:
        # Limit validieren
        try:
            limit = int(request.args.get("limit", "50"))
            if limit < 1:
                raise ValueError("limit muss >= 1 sein")
            limit = min(limit, 1000)
        except ValueError as ve:
            return _json_error(f"bad request: {ve}", 400)

        eps = episodic.list_episodes(limit=limit) or []
        enriched = []
        for ep in eps:
            try:
                full = episodic.get_episode(int(ep["id"]), with_events=True)
                n_events = len(full.get("events", [])) if full else None
            except Exception:
                n_events = None
            # Meta ergänzen ohne bestehendes zu zerstören
            meta = dict(ep.get("meta") or {})
            meta["events"] = n_events
            ep = dict(ep)
            ep["meta"] = meta
            enriched.append(ep)
        return _json({"ok": True, "episodes": enriched})
    except Exception as e:  # pragma: no cover
        log.exception("api_list failed: %s", e)
        return _json_error(str(e), 500)

# -------------------------- API: Vision-Episoden ------------------------------

def _db_conn():
    """Hilfsfunktion: liefert eine DB-Connection oder None."""
    if not sql_manager:  # pragma: no cover
        return None
    return sql_manager.get_conn()

@bp.route("/api/vision/list", methods=["GET"])
def api_vision_list():
    """
    Liste der letzten Vision-Sessions (kind='vision', source='vision/token').

    Antwort:
      {
        "ok": true,
        "episodes": [
          {
            "id": 10,
            "ts_start": 1765115445,
            "ts_end": null,
            "kind": "vision",
            "label": "Vision-Session",
            "source": "vision/token",
            "created_at": 1765115445,
            "cam_events": 93,
            "meta": {...}
          },
          ...
        ]
      }
    """
    conn = _db_conn()
    if not conn:
        return _json_error("sql_manager not available", 500)

    try:
        try:
            limit = int(request.args.get("limit", "50"))
            if limit < 1:
                raise ValueError("limit muss >= 1 sein")
            limit = min(limit, 1000)
        except ValueError as ve:
            return _json_error(f"bad request: {ve}", 400)

        sql = """
            SELECT
                e.id,
                e.ts_start,
                e.ts_end,
                e.kind,
                e.label,
                e.source,
                e.meta_json,
                COUNT(ev.rowid) AS cam_events
            FROM episodes e
            LEFT JOIN episode_events ev
                   ON ev.episode_id = e.id
                  AND ev.event_type = 'cam_token'
            WHERE e.kind = 'vision'
            GROUP BY e.id, e.ts_start, e.ts_end, e.kind, e.label, e.source, e.meta_json
            ORDER BY e.id DESC
            LIMIT ?
        """
        rows = conn.execute(sql, (limit,)).fetchall()
        episodes = []
        for r in rows:
            meta = {}
            mj = r["meta_json"]
            if mj:
                try:
                    meta = json.loads(mj)
                except Exception:
                    meta = {"raw_meta_json": mj}
            episodes.append({
                "id": int(r["id"]),
                "ts_start": r["ts_start"],
                "ts_end": r["ts_end"],
                "kind": r["kind"],
                "label": r["label"],
                "source": r["source"],
                "created_at": r["ts_start"],
                "cam_events": int(r["cam_events"] or 0),
                "meta": meta,
            })
        return _json({"ok": True, "episodes": episodes})
    except Exception as e:  # pragma: no cover
        log.exception("api_vision_list failed: %s", e)
        return _json_error(str(e), 500)

@bp.route("/api/vision/events/<int:eid>", methods=["GET"])
def api_vision_events(eid: int):
    """
    Letzte N cam_token-Events einer Vision-Episode.

    Antwort:
      {
        "ok": true,
        "events": [
          {
            "ts": 1765115445,
            "ref_table": "snapchains",
            "ref_id": 38927,
            "origin": "vision/token",
            "q": 0.0403,
            "motion": 0.0,
            "edges": 0.10,
            "color": 0.31,
            "raw_meta": {...}
          },
          ...
        ]
      }
    """
    conn = _db_conn()
    if not conn:
        return _json_error("sql_manager not available", 500)

    try:
        try:
            limit = int(request.args.get("limit", "50"))
            if limit < 1:
                raise ValueError("limit muss >= 1 sein")
            limit = min(limit, 500)
        except ValueError as ve:
            return _json_error(f"bad request: {ve}", 400)

        sql = """
            SELECT
                ts,
                event_type,
                ref_table,
                ref_id,
                meta_json
            FROM episode_events
            WHERE episode_id = ?
              AND event_type = 'cam_token'
            ORDER BY ts ASC
            LIMIT ?
        """
        rows = conn.execute(sql, (eid, limit)).fetchall()
        events = []
        for r in rows:
            dat = {}
            mj = r["meta_json"]
            if mj:
                try:
                    dat = json.loads(mj)
                except Exception:
                    dat = {"raw_meta_json": mj}
            events.append({
                "ts": r["ts"],
                "ref_table": r["ref_table"],
                "ref_id": r["ref_id"],
                "origin": dat.get("origin"),
                "q": dat.get("q"),
                "motion": dat.get("motion"),
                "edges": dat.get("edges"),
                "color": dat.get("color"),
                "raw_meta": dat,
            })
        return _json({"ok": True, "events": events})
    except Exception as e:  # pragma: no cover
        log.exception("api_vision_events failed: %s", e)
        return _json_error(str(e), 500)

# -----------------------------------------------------------------------------
# Kompatibilität: Alias für run_oroma.py
# -----------------------------------------------------------------------------
episodic_bp = bp  # <- Alias für Kompatibilität (run_oroma erwartet episodic_bp)
__all__ = ["episodic_bp", "bp"]
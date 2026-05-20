#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/api.py
# Projekt:   ORÓMA (Offline-First · Headless · Flask Research API)
# Modul:     Research API Blueprint – schlanke /api/* Endpunkte für Rewards, Curiosity, Episodic Memory, Explainability, Spatial Index, Diagnostics, Hypothesen, MetaSnaps
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert einen Flask Blueprint (`api`) mit Forschungs-/Diagnose-Endpunkten.
# Es ist bewusst eine „thin controller“-Schicht:
#   - Validiert Request minimal (JSON/Args)
#   - delegiert Business-Logik an core-Module
#   - gibt JSON-Antworten zurück (stabil, klein, UI-/Tool-freundlich)
#
# Wichtig:
# - Diese Endpunkte sind als ORÓMA-internes API gedacht (UI/Tools/Experimente).
# - Stabilitätsgarantie ist „best effort“ – es ist kein öffentliches, versioniertes REST-API.
# - Auth/Token-Guard passiert NICHT hier, sondern im zentralen Flask-Setup (ui/flask_ui.py),
#   das für /api/* optional OROMA_UI_TOKEN erzwingt.
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Keine GUI/Qt Abhängigkeiten.
# - Keine direkten DB-Operationen in dieser Datei (das machen die core-Module).
# - Deterministische JSON-Strukturen (damit UI/JS stabil bleibt).
# - Fehler sollen als {"ok": False, "error": "..."} zurückkommen (oder Flask-Default),
#   ohne den Gesamtprozess zu crashen.
#
# ABHÄNGIGKEITEN (CORE-MODULE, EXAKT WIE IM CODE)
# ───────────────────────────────────────────────
# from core import (
#   reward, curiosity, episodic, explain, spatial_index,
#   diagnostics, auto_tuner, hypotheses, meta_snap
# )
#
# Daraus ergibt sich:
# - reward:         RewardLogger + RewardAggregator (Logging/Stats)
# - curiosity:      CuriosityLogger (Logging)
# - episodic:       create_episode / list_episodes (episodische DB-Struktur)
# - explain:        causal_trace(decision_id) oder Trace-Getter (Explainability 2.0)
# - spatial_index:  add_point(...) (Spatial Index / simple Map Store)
# - diagnostics:    gaps_summary() o. ä. (Knowledge-Gaps / Systemdiagnose)
# - auto_tuner:     suggest(...) (Tuning Vorschlag)
# - hypotheses:     add(...) / list_all() (Hypothesenverwaltung)
# - meta_snap:      create(...) / list_recent(...) (MetaSnap Erzeugung/Listing)
#
# BLUEPRINT
# ─────────
# api = Blueprint("api", __name__)
# Dieses Blueprint-Objekt wird in run_oroma.py registriert (safe_register),
# damit ORÓMA auch bei optionalen Modulen bootfähig bleibt.
#
# ROUTES (AKTUELLER STAND DIESER DATEI)
# ─────────────────────────────────────
# 1) Rewards
#   POST /api/reward/log
#     Request JSON (typisch):
#       {
#         "reward": <float>,         # Pflicht (default 0.0 wenn fehlend)
#         "source": <str>,           # z.B. "curriculum" | "game:tictactoe" | "vision/token"
#         "meta": { ... }            # optional
#       }
#     Response:
#       {"ok": True, "id": <reward_row_id>}
#
#   GET /api/reward/stats/<source>
#     Response:
#       {
#         "ok": True,
#         "ema100": <float>,
#         "mean50": <float>,
#         "sum1000": <float>
#       }
#
# 2) Curiosity
#   POST /api/curiosity/log
#     Request JSON (typisch):
#       {"curiosity": <float>, "source": <str>, "meta": {...}}
#     Response:
#       {"ok": True, "id": <curiosity_row_id>}
#
# 3) Episodic Memory
#   POST /api/episodic/create
#     Request JSON:
#       {"title": <str>, "meta": {...}}
#     Response:
#       {"ok": True, "episode_id": <int>}
#
#   GET /api/episodic/list?limit=50
#     Response:
#       {"ok": True, "episodes": [ ... ]}
#
# 4) Explainability 2.0
#   GET /api/explain/trace/<int:decision_id>
#     Response:
#       {"ok": True, "trace": {...}}   # Struktur kommt direkt aus core.explain
#
# 5) Spatial Index
#   POST /api/spatial/add_point
#     Request JSON (typisch):
#       {"x": <float>, "y": <float>, "label": <str>, "meta": {...}}
#     Response:
#       {"ok": True, "id": <int>}
#
# 6) Diagnostics / Knowledge Gaps
#   GET /api/gaps/summary
#     Response:
#       {"ok": True, "summary": {...}} # kommt aus core.diagnostics
#
# 7) AutoTuner
#   POST /api/auto_tuner/suggest
#     Request JSON:
#       {"param": <str>, "current": <float>, "basis": <str optional>, "note": <str optional>}
#     Response:
#       {"ok": True, "suggested": <float>}
#
# 8) Hypothesen (Research)
#   POST /api/research/new
#     Request JSON:
#       {"text": <str>, "meta": {... optional}}
#     Response:
#       {"ok": True, "hypothesis_id": <int>}
#
#   GET /api/research/list
#     Response:
#       {"ok": True, "hypotheses": [ ... ]}
#
# 9) MetaSnaps
#   POST /api/meta/create
#     Request JSON:
#       {"label": <str>, "sources": [..], "score": <float optional>, "tags": [..], "extra": {...}}
#     Response:
#       {"ok": True, "meta_id": <int>}   # Rückgabe hängt von core.meta_snap ab
#
#   GET /api/meta/list?limit=50
#     Response:
#       {"ok": True, "items": [ ... ]}
#
# FEHLERSTRATEGIE
# ───────────────
# - Requests ohne JSON werden als {} behandelt (request.json or {}).
# - Parameter werden defensiv gelesen (get(..., default)).
# - Exceptions sollen nach Möglichkeit abgefangen werden (oder in core passieren);
#   falls nicht, greift Flask-Fehlerbehandlung (ui/flask_ui.py kann JSON-Handler für /api/* haben).
#
# SECURITY / AUTH (WICHTIG)
# ─────────────────────────
# Dieses Modul implementiert KEINE Auth.
# Token-/Cookie-Handling und /api/*-Guard sind zentral in ui/flask_ui.py umgesetzt:
#   - OROMA_UI_TOKEN leer → token-free Modus
#   - OROMA_UI_TOKEN gesetzt → /api/* verlangt gültigen Token
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Blueprint-Name und Route-Pfade müssen stabil bleiben (UI/Tools/Automationen).
# - Datei bleibt „thin“: keine SQL-Strings hier, sondern Delegation an core.*.
# - JSON-Antworten bleiben klein und konsistent (ok + payload).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
from flask import Blueprint, request, jsonify

# Core-Module
from core import (
    reward, curiosity, episodic, explain, spatial_index,
    diagnostics, auto_tuner, hypotheses, meta_snap
)

api = Blueprint("api", __name__)

# ------------------------- Reward --------------------------------------------

@api.route("/api/reward/log", methods=["POST"])
def api_reward_log():
    data = request.json or {}
    rid = reward.RewardLogger().log(
        source=data.get("source", "generic"),
        step=int(data.get("step", 0)),
        reward=float(data.get("reward", 0.0)),
        raw=data.get("raw"),
        episode_id=data.get("episode_id"),
        tag=data.get("tag"),
    )
    return jsonify({"ok": True, "id": rid})

@api.route("/api/reward/stats/<source>", methods=["GET"])
def api_reward_stats(source):
    agg = reward.RewardAggregator()
    return jsonify({
        "ok": True,
        "ema100": agg.ema(source, span=100),
        "mean50": agg.window_mean(source, 50),
        "sum1000": agg.window_sum(source, 1000),
    })

# ------------------------- Curiosity -----------------------------------------

@api.route("/api/curiosity/log", methods=["POST"])
def api_curiosity_log():
    data = request.json or {}
    sig = curiosity.curiosity_score(
        pred=data.get("pred"), obs=data.get("obs"),
        last_logits=data.get("last_logits"), new_logits=data.get("new_logits"),
        prior_probs=data.get("prior_probs"), post_probs=data.get("post_probs"),
        seen_count=data.get("seen_count"),
    )
    rid = curiosity.CuriosityLogger().log(data.get("source", "generic"), sig, tag=data.get("tag"))
    return jsonify({"ok": True, "id": rid, "signal": sig.signal, "components": sig.components})

# ------------------------- Episodic ------------------------------------------

@api.route("/api/episodic/create", methods=["POST"])
def api_epi_create():
    data = request.json or {}
    eid = episodic.create_episode(data.get("title", "untitled"), data.get("meta") or {})
    return jsonify({"ok": True, "episode_id": eid})

@api.route("/api/episodic/list", methods=["GET"])
def api_epi_list():
    eps = episodic.list_episodes(limit=int(request.args.get("limit", 50)))
    return jsonify({"ok": True, "episodes": eps})

# ------------------------- Explainability 2.0 --------------------------------

@api.route("/api/explain/trace/<int:decision_id>", methods=["GET"])
def api_explain_trace(decision_id: int):
    trace = explain.get_causal_trace(decision_id)
    return jsonify({"ok": True, "trace": trace})

# ------------------------- Spatial Index -------------------------------------

@api.route("/api/spatial/add_point", methods=["POST"])
def api_spatial_add_point():
    d = request.json or {}
    pid = spatial_index.add_point(float(d.get("x", 0.0)), float(d.get("y", 0.0)), d.get("z"), d.get("label"))
    return jsonify({"ok": True, "point_id": pid})

# ------------------------- Diagnostics & AutoTuner ---------------------------

@api.route("/api/gaps/summary", methods=["GET"])
def api_gaps_summary():
    return jsonify(diagnostics.quick_summary())

@api.route("/api/auto_tuner/suggest", methods=["POST"])
def api_auto_tuner_suggest():
    params = request.json or {}
    sugg = auto_tuner.suggest(params)
    return jsonify({"ok": True, "suggestions": sugg})

# ------------------------- Hypothesen ----------------------------------------

@api.route("/api/research/new", methods=["POST"])
def api_research_new():
    data = request.json or {}
    hid = hypotheses.add(data.get("text", ""), meta=data.get("meta"))
    return jsonify({"ok": True, "hypothesis_id": hid})

@api.route("/api/research/list", methods=["GET"])
def api_research_list():
    return jsonify({"ok": True, "hypotheses": hypotheses.list_all()})

# ------------------------- MetaSnaps -----------------------------------------

@api.route("/api/meta/create", methods=["POST"])
def api_meta_create():
    data = request.json or {}
    mids = meta_snap.create_from_sources(data.get("sources") or [], label=data.get("label"))
    return jsonify({"ok": True, "meta_snap_ids": mids})

@api.route("/api/meta/list", methods=["GET"])
def api_meta_list():
    return jsonify({"ok": True, "meta_snaps": meta_snap.list_all()})
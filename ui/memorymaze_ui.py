#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/memorymaze_ui.py
# Projekt: ORÓMA – Headless UI (Flask) / Games
# Modul:   MemoryMaze Hybrid UI + JSON-API (TicTacToe-Standard)
# Version: v1.0
# Stand:   2026-02-22
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#   Standardisierte Web-UI + JSON-API für das Hybrid-Spiel "MemoryMaze Hybrid"
#   (mini_programs/memorymaze_hybrid.py). Route bleibt bewusst:
#
#       /memorymaze/
#
#   Damit bleibt die existierende URL stabil. Das frühere Modul
#   mini_programs/memory_maze2033.py ist ein anderes Konzept (zwei Modi
#   "Memory" und "Maze"); dieses UI steuert das neue Hybrid-Environment.
#
# Spezifikation (Kurzfassung)
# ---------------------------
#   - Zwei Spieler P1/P2 (ghosted gegeneinander), optional Hard-P3 (Jäger) als
#     separate Auswahl "hard_p3".
#   - 5 Paare Blocker A–E (anfangs unpassierbar, verschwinden bei Match).
#   - Claim-Lock Anti-Steal: Claim entsteht durch aktive Reveal-Aktion.
#     Pro Spieler max 1 Claim, Timeout 60 Steps.
#   - Items (a/b/c/d) + Random Fallgruben.
#   - Sieger: Elimination (5 Strikes bei P1/P2). P3 kann durch Fallgruben
#     sterben (50 Strikes) und wird dann deaktiviert.
#
# UI/UX-Standard (wie TicTacToe)
# ------------------------------
#   - Start/Stop Autoplay: Client tickt /api/step, Server hält nur State.
#   - Apply/Reset Parameter (seed, map_kind, mode normal|hard_p3, eps)
#   - Statuspanel: step, pairs_left, strikes, speed multipliers, winner.
#   - Debug-Grid (ASCII) für schnelle Sichtbarkeit.
#
# Produktiv-Constraints
# ---------------------
#   - Headless-safe: keine pygame/GUI.
#   - DB-Safety: UI schreibt keine DB; DB Writes laufen nur in Daily-Runnern.
#   - Keine stillen Fehler: API liefert ok:false + err.
#
# API
# ---
#   GET  /memorymaze/
#   GET  /memorymaze/api/state
#   POST /memorymaze/api/reset
#   POST /memorymaze/api/apply
#   POST /memorymaze/api/step
#   POST /memorymaze/api/toggle_autoplay
#
# =============================================================================

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, render_template, request


try:
    from mini_programs.memorymaze_hybrid import HybridGame
except Exception as e:
    raise RuntimeError(f"memorymaze_hybrid import fehlgeschlagen: {e}")


memorymaze_bp = Blueprint(
    "memorymaze_ui",
    __name__,
    url_prefix="/memorymaze",
    template_folder="templates",
    static_folder="static",
)

# Compatibility alias (einige Loader erwarten "bp")
bp = memorymaze_bp


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return default


@dataclass
class _RT:
    game: HybridGame
    autoplay: bool = False
    mode: str = "normal"          # normal | hard_p3
    map_kind: str = "sym"         # sym | asym
    eps: float = 0.08
    seed: Optional[int] = None
    last_err: Optional[str] = None
    last_tick_ms: int = 0


def _rt() -> _RT:
    app = memorymaze_bp
    # store on blueprint module global (works per-process)
    global _RUNTIME
    if _RUNTIME is None:
        g = HybridGame(map_kind=os.environ.get("OROMA_MMZ_MAP", "sym"))
        g.reset(seed=None, mode="normal")
        _RUNTIME = _RT(game=g)
    return _RUNTIME


_RUNTIME: Optional[_RT] = None


@memorymaze_bp.get("/")
def page() -> Any:
    return render_template("memorymaze.html")


@memorymaze_bp.get("/api/state")
def api_state() -> Any:
    rt = _rt()
    try:
        st = rt.game.state()
        st.update({
            "autoplay": bool(rt.autoplay),
            "mode_sel": rt.mode,
            "map_kind": rt.map_kind,
            "eps": rt.eps,
            "seed": rt.seed,
            "last_tick_ms": rt.last_tick_ms,
            "err": rt.last_err,
        })
        return jsonify(st)
    except Exception as e:
        rt.last_err = str(e)
        return jsonify({"ok": False, "err": str(e)})


@memorymaze_bp.post("/api/reset")
def api_reset() -> Any:
    rt = _rt()
    try:
        data = request.get_json(silent=True) or {}
        rt.mode = str(data.get("mode", rt.mode))
        rt.map_kind = str(data.get("map_kind", rt.map_kind))
        rt.eps = _safe_float(data.get("eps", rt.eps), rt.eps)
        seed = data.get("seed", rt.seed)
        rt.seed = int(seed) if seed not in (None, "", "null") else None
        rt.game = HybridGame(map_kind=rt.map_kind)
        rt.game.reset(seed=rt.seed, mode=rt.mode)
        rt.autoplay = False
        rt.last_err = None
        rt.last_tick_ms = 0
        st = rt.game.state()
        st["autoplay"] = False
        return jsonify(st)
    except Exception as e:
        rt.last_err = str(e)
        return jsonify({"ok": False, "err": str(e)})


@memorymaze_bp.post("/api/apply")
def api_apply() -> Any:
    rt = _rt()
    try:
        data = request.get_json(silent=True) or {}
        if "mode" in data:
            rt.mode = str(data.get("mode"))
        if "map_kind" in data:
            rt.map_kind = str(data.get("map_kind"))
        if "eps" in data:
            rt.eps = _safe_float(data.get("eps"), rt.eps)
        if "seed" in data:
            seed = data.get("seed")
            rt.seed = int(seed) if seed not in (None, "", "null") else None
        return jsonify({"ok": True, "mode_sel": rt.mode, "map_kind": rt.map_kind, "eps": rt.eps, "seed": rt.seed})
    except Exception as e:
        rt.last_err = str(e)
        return jsonify({"ok": False, "err": str(e)})


@memorymaze_bp.post("/api/toggle_autoplay")
def api_toggle_autoplay() -> Any:
    rt = _rt()
    try:
        data = request.get_json(silent=True) or {}
        val = data.get("autoplay")
        if val is None:
            rt.autoplay = not rt.autoplay
        else:
            rt.autoplay = bool(val)
        return jsonify({"ok": True, "autoplay": bool(rt.autoplay)})
    except Exception as e:
        rt.last_err = str(e)
        return jsonify({"ok": False, "err": str(e)})


@memorymaze_bp.post("/api/step")
def api_step() -> Any:
    rt = _rt()
    try:
        # Each step advances AI for P1/P2 (and P3 if enabled)
        t0 = _now_ms()
        a1 = rt.game.ai_action("p1", eps=rt.eps)
        a2 = rt.game.ai_action("p2", eps=rt.eps)
        acts: Dict[str, str] = {"p1": a1, "p2": a2}
        if rt.mode == "hard_p3":
            acts["p3"] = rt.game.ai_action("p3", eps=0.0)
        st = rt.game.step(acts)
        rt.last_tick_ms = _now_ms() - t0
        st.update({
            "autoplay": bool(rt.autoplay),
            "mode_sel": rt.mode,
            "map_kind": rt.map_kind,
            "eps": rt.eps,
            "seed": rt.seed,
            "last_tick_ms": rt.last_tick_ms,
            "ai": {"p1": a1, "p2": a2, "p3": acts.get("p3")},
        })
        return jsonify(st)
    except Exception as e:
        rt.last_err = str(e)
        return jsonify({"ok": False, "err": str(e)})

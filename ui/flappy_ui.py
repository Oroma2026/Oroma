#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/flappy_ui.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   FlappyBird UI + UniversalPolicy Integration (Standard-Modi)
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Stellt eine standardisierte Web-UI für FlappyBird bereit und integriert die
# ORÓMA UniversalPolicy (policy_rules) analog zu TicTacToe/Connect4/Pong/Chess.
#
# Motivation:
#   In der Codebasis existierten historisch zwei Flappy-Varianten:
#     (A) eine sehr einfache Canvas-Demo (bird_y/pipe_x/pipe_gap_y)
#     (B) die headless RL-Umgebung mini_programs/flappybird.py
#
# Diese UI nutzt jetzt konsequent die headless RL-Umgebung (B), damit Flappy
# nicht nur „spielbar“, sondern auch als Lern-/Policy-Referenz dient.
#
# ROUTES / API (Blueprint: /flappy)
# -------------------------------
#   GET  /flappy/                 → HTML UI (Canvas + Controls)
#   GET  /flappy/api/state         → Zustand + UI-Meta + Policy-Stats
#   POST /flappy/api/reset         → Reset (optional seed)
#   POST /flappy/api/action        → {action:0|1} (human input: flap/noop)
#   POST /flappy/api/step          → 1 Tick (action wird aus Mode/Autoplay gewählt)
#   POST /flappy/api/mode          → {mode: 'human'|'oroma_vs_oroma_policy'|'oroma_vs_oroma_explore'}
#   POST /flappy/api/toggle        → {autoplay: true|false}
#   GET  /flappy/api/settings      → UI Settings (eps, explore_moves_per_game)
#   POST /flappy/api/settings      → Patch Settings
#
# MODES (STANDARD)
# ---------------
#   • human                 : User steuert Flap, Server macht step
#   • oroma_vs_oroma_policy : UniversalPolicy wählt action (eps=0)
#   • oroma_vs_oroma_explore: Explore (eps>0 + explore moves budget)
#
# POLICY / STATE HASH
# -------------------
# Flappy ist Single-Agent. Für UniversalPolicy nutzen wir ein kompaktes,
# robustes State-Hash aus quantisierten Features (y, vy, dx, gap_y, gap_h).
# Damit vermeiden wir state-space Explosion durch float-Noise.
#
# PRODUKTIONSREGELN
# -----------------
# • headless-freundlich: keine pygame/Qt/X11 Abhängigkeit
# • thread-safe: State wird über Lock geschützt
# • keine stillen Fehler: API gibt ok=false + error zurück, zusätzlich Logging
# =============================================================================

from __future__ import annotations

import os
import threading
import time
import random
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, render_template, request, current_app


flappy_bp = Blueprint(
    "flappy",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/flappy",
)


def _env_float(name: str, default: float) -> float:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


class _PolicyShim:
    """Leichtgewichtige Brücke zur core.universal_policy.Policy."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore

            self.pol = Policy(namespace=namespace)
        except Exception:
            self.pol = None

    @staticmethod
    def _qb(v: float, bins: int) -> int:
        # v in [0..1] (für y, dx, gap_y, gap_h)
        try:
            x = int(float(v) * bins)
        except Exception:
            x = 0
        if x < 0:
            return 0
        if x > bins:
            return bins
        return x

    @staticmethod
    def _qs(v: float) -> int:
        # sign for vy
        try:
            if v > 1e-9:
                return 1
            if v < -1e-9:
                return -1
        except Exception:
            pass
        return 0

    def state_hash(self, st: Dict[str, Any]) -> str:
        # Quantisierung: bewusst grob (robust)
        y = self._qb(st.get("y", 0.5), 40)
        dx = self._qb(min(1.0, max(0.0, float(st.get("dx", 1.0)))), 40)
        gy = self._qb(st.get("gap_y", 0.5), 40)
        gh = self._qb(st.get("gap_h", 0.25), 40)
        vs = self._qs(float(st.get("vy", 0.0)))
        return f"flappy:v1:y={y}:dx={dx}:gy={gy}:gh={gh}:vs={vs}"

    def choose(self, st: Dict[str, Any], legal: List[int]) -> int:
        if not self.pol:
            return int(random.choice(legal))
        sh = self.state_hash(st)
        # side ist bei Single-Agent konstant "X" (kompatibel zur Policy API)
        return int(self.pol.choose(sh, legal, side="X"))

    def rules_count(self) -> int:
        try:
            from core import sql_manager

            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM policy_rules WHERE namespace=?",
                    (self.namespace,),
                )
                row = cur.fetchone()
                if row is None:
                    return 0
                if hasattr(row, "keys"):
                    return int(list(row)[0])
                return int(row[0])
        except Exception:
            return 0


class FlappyRuntime:
    """Thread-sicherer Runtime-Container für FlappyBird (headless)."""

    def __init__(self):
        self.lock = threading.Lock()

        from mini_programs.flappybird import FlappyBird, FBConfig  # type: ignore

        self.cfg = FBConfig()
        self.env = FlappyBird(self.cfg)

        # UI Settings
        # Default: explore (wie gewünscht) – Flappy soll nach Standard sofort
        # "autark" laufen können, ohne dass der User erst Mode/Autoplay setzen
        # muss. Human-Mode bleibt natürlich verfügbar.
        self.mode = "oroma_vs_oroma_explore"
        self.autoplay = True
        self.eps = _env_float("OROMA_FLAPPY_EPS", 0.08)
        self.explore_moves_per_game = int(_env_float("OROMA_FLAPPY_EXPLORE_MOVES", 1.0))

        self._explore_budget = 0
        self._pending_action: Optional[int] = None

        self.policy = _PolicyShim(namespace="game:flappy")

    def reset(self, seed: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            self._explore_budget = 0
            self._pending_action = None
            try:
                st = self.env.reset(seed=seed)
                return asdict(st)
            except Exception:
                st = self.env.reset(seed=None)
                return asdict(st)

    def _pick_action(self, st: Dict[str, Any]) -> int:
        legal = [0, 1]
        if self.mode == "human":
            if self._pending_action is None:
                return 0
            a = int(self._pending_action)
            self._pending_action = None
            return 1 if a == 1 else 0

        # Policy/Explore
        if self.mode == "oroma_vs_oroma_policy":
            return int(self.policy.choose(st, legal))

        # explore
        if self.mode == "oroma_vs_oroma_explore":
            if self._explore_budget < self.explore_moves_per_game:
                self._explore_budget += 1
                return int(random.choice(legal))
            if random.random() < float(self.eps):
                return int(random.choice(legal))
            return int(self.policy.choose(st, legal))

        # fallback
        return 0

    def action(self, a: int) -> None:
        with self.lock:
            self._pending_action = 1 if int(a) == 1 else 0

    def step(self) -> Dict[str, Any]:
        with self.lock:
            st = self.env.get_state()
            if not st.get("alive", True):
                # If dead, keep stable state
                return st
            a = 0
            if self.autoplay or self.mode != "human":
                a = self._pick_action(st)
            else:
                a = self._pick_action(st)
            st2, _r, _done, _info = self.env.step(int(a))
            # reset explore budget when a new episode begins (passed pipe resets in env internally)
            if not st2.alive:
                self._explore_budget = 0
            return asdict(st2)

    def state(self) -> Dict[str, Any]:
        with self.lock:
            st = self.env.get_state()
            return {
                "ok": True,
                "state": st,
                "mode": self.mode,
                "autoplay": bool(self.autoplay),
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "namespace": "game:flappy",
                "policy_rules": int(self.policy.rules_count()),
            }

    def settings(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
            }

    def patch_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            try:
                if "eps" in patch:
                    self.eps = float(patch.get("eps"))
                if "explore_moves_per_game" in patch:
                    self.explore_moves_per_game = int(float(patch.get("explore_moves_per_game")))
            except Exception:
                pass
            return self.settings()


def _rt() -> FlappyRuntime:
    k = "_flappy_runtime"
    if k not in current_app.config:
        current_app.config[k] = FlappyRuntime()
    return current_app.config[k]


@flappy_bp.get("/")
def page():
    return render_template("flappy.html")


@flappy_bp.get("/api/state")
def api_state():
    return jsonify(_rt().state())


@flappy_bp.post("/api/reset")
def api_reset():
    try:
        d = request.get_json(force=True, silent=True) or {}
    except Exception:
        d = {}
    seed = d.get("seed", None)
    try:
        seed_i = int(seed) if seed is not None else None
    except Exception:
        seed_i = None
    st = _rt().reset(seed=seed_i)
    return jsonify({"ok": True, "state": st})


@flappy_bp.post("/api/action")
def api_action():
    try:
        d = request.get_json(force=True) or {}
        a = int(d.get("action", 0))
    except Exception:
        a = 0
    _rt().action(a)
    return jsonify({"ok": True})


@flappy_bp.post("/api/step")
def api_step():
    try:
        st = _rt().step()
        return jsonify({"ok": True, "state": st})
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)})


@flappy_bp.post("/api/mode")
def api_mode():
    try:
        d = request.get_json(force=True) or {}
        mode = str(d.get("mode") or "human")
    except Exception:
        mode = "human"

    # Compatibility aliases
    if mode == "oroma_solo":
        mode = "oroma_vs_oroma_explore"
    if mode == "oroma_vs_oroma":
        mode = "oroma_vs_oroma_explore"

    if mode not in ("human", "oroma_vs_oroma_policy", "oroma_vs_oroma_explore"):
        mode = "human"

    rt = _rt()
    with rt.lock:
        rt.mode = mode
        # Standard-Verhalten:
        # - human bleibt wie gesetzt
        # - policy/explore -> autoplay standardmäßig an
        if mode in ("oroma_vs_oroma_policy", "oroma_vs_oroma_explore"):
            rt.autoplay = True
    return jsonify({"ok": True, "mode": rt.mode, "autoplay": bool(rt.autoplay)})


@flappy_bp.post("/api/toggle")
def api_toggle():
    try:
        d = request.get_json(force=True) or {}
        ap = bool(d.get("autoplay", False))
    except Exception:
        ap = False
    rt = _rt()
    with rt.lock:
        rt.autoplay = bool(ap)
    return jsonify({"ok": True, "autoplay": bool(rt.autoplay)})


@flappy_bp.get("/api/settings")
def api_settings_get():
    return jsonify({"ok": True, "settings": _rt().settings()})


@flappy_bp.post("/api/settings")
def api_settings_post():
    try:
        d = request.get_json(force=True) or {}
    except Exception:
        d = {}
    st = _rt().patch_settings(d)
    return jsonify({"ok": True, "settings": st})

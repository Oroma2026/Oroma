#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/ctf_ui.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Capture The Flag UI (CTF) – Standard-Modi + UniversalPolicy + Autoplay
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# Zweck / Überblick
# ---------------
# Dieses Modul stellt eine **headless** Flask-UI für das Mini-Program
# `mini_programs/capture_the_flag.py` bereit.
#
# Ziele (UI-Standard wie TicTacToe/Connect4/Pong/HideSeek/Flappy/Chess)
# - Einheitliche Modi:
#     * human (A wird per WASD/Pfeile gesteuert, B = Policy)
#     * oroma_vs_oroma_policy   (eps=0.0, learn=false)
#     * oroma_vs_oroma_explore  (eps>0, learn=true)
# - Start/Stop: Start aktiviert Tick-Loop (keine /step Calls bei Stop).
# - Auto-Reset: Wenn Episode done ist, wird automatisch reset() ausgeführt.
# - UniversalPolicy:
#     * Namespace: "game:ctf"
#     * Side-aware: Agent A = "X", Agent B = "O" (nur Namenskonvention für Policy)
# - Headless: Kein pygame/Qt/Wayland/X11.
#
# API
# ---
# GET  /ctf/                 -> UI
# GET  /ctf/api/state         -> JSON-State (inkl. ASCII-Render)
# POST /ctf/api/reset         -> reset()
# POST /ctf/api/toggle        -> autoplay toggle (Start/Stop)
# POST /ctf/api/mode          -> set mode
# POST /ctf/api/settings      -> set eps/explore_moves_per_game/auto_reset
# POST /ctf/api/move          -> Human move (action int 0..4)
#
# ENV
# ---
# OROMA_BASE                    (Default: /opt/ai/oroma)
# OROMA_CTF_TICK_MS              (Default: 120)
# OROMA_CTF_MAX_STEPS_UI         (Default: 400)  # UI Episode-Limit (nicht Daily)
# OROMA_CTF_DEFAULT_MODE         (Default: oroma_vs_oroma_explore)
# OROMA_CTF_DEFAULT_EPS          (Default: 0.08)
# OROMA_CTF_DEFAULT_EXPLORE_MOVES (Default: 1)
#
# Hinweise / Design
# -----------------
# - Die UI zeichnet ASCII (pre), weil es extrem robust auf Mobile ist
#   und keinerlei Canvas/Scaling-Probleme hat.
# - Policy-State-Hash basiert auf **diskretisierten** Observations (13 floats)
#   aus dem Environment, um die Policy-Rules nicht zu sprengen.
# - Kein "silent failure": Ausnahmen werden geloggt und in state["err"] sichtbar.
# =============================================================================

from __future__ import annotations

import os
import time
import json
import math
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple

from flask import Blueprint, jsonify, render_template, request, current_app

from core import sql_manager

# Mini-Program (headless env)
from mini_programs.capture_the_flag import CTFEnv, CTFConfig


ctf_bp = Blueprint(
    "ctf_bp",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/ctf",
)

# Compatibility alias (games_ui expects attr "bp")
bp = ctf_bp


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)).strip())
        return v
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, str(default)).strip())
        return v
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _quantize01(x: float, bins: int = 16) -> int:
    """Quantize x in [0..1] into 0..bins-1."""
    x = _clamp(float(x), 0.0, 1.0)
    return int(round(x * (bins - 1)))


def _state_hash_from_obs(obs: List[float], side: str) -> str:
    """
    Deterministic, compact state hash:
    - obs length is expected 13 (see mini_program docs)
    - quantize each value into small bins to limit state explosion
    """
    if not obs:
        return f"ctf|{side}|empty"
    # pos-like values are [0..1], scores/steps already normalized
    q = [_quantize01(v, bins=16) for v in obs]
    return f"ctf|{side}|" + ",".join(str(n) for n in q)


class UniversalPolicyShim:
    """
    Wrap core.universal_policy.Policy to a stable interface.

    Important: In some ORÓMA builds learn_many may return None (fire-and-forget).
    This shim always returns an int count (0 if unknown).
    """

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._have_up = False
        self._pol = None
        try:
            from core.universal_policy import Policy  # lazy import
            self._pol = Policy(namespace=namespace)
            self._have_up = True
        except Exception as e:
            current_app.logger.exception("[ctf_ui] UniversalPolicy init failed: %s", e)
            self._pol = None
            self._have_up = False

    @property
    def have_up(self) -> bool:
        return bool(self._have_up and self._pol is not None)

    def choose(self, obs: List[float], legal: List[int], side: str) -> int:
        if not legal:
            return 0
        if not self.have_up:
            return legal[0]
        sh = _state_hash_from_obs(obs, side)
        try:
            a = self._pol.choose(sh, legal, side=side)
            return int(a) if a in legal else int(legal[0])
        except Exception as e:
            current_app.logger.exception("[ctf_ui] choose failed: %s", e)
            return int(legal[0])

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not items or not self.have_up:
            return 0
        try:
            res = self._pol.learn_many(items)
            if res is None:
                return 0
            try:
                return int(res)
            except Exception:
                return 0
        except Exception as e:
            current_app.logger.exception("[ctf_ui] learn_many failed: %s", e)
            return 0

    def policy_rules_count(self) -> int:
        try:
            return int(sql_manager.count_policy_rules(self.namespace))
        except Exception:
            return 0


@dataclass
class CTFSettings:
    mode: str = os.environ.get("OROMA_CTF_DEFAULT_MODE", "oroma_vs_oroma_explore").strip() or "oroma_vs_oroma_explore"
    autoplay: bool = True
    auto_reset: bool = True
    eps: float = _env_float("OROMA_CTF_DEFAULT_EPS", 0.08)
    explore_moves_per_game: int = _env_int("OROMA_CTF_DEFAULT_EXPLORE_MOVES", 1)
    tick_ms: int = _env_int("OROMA_CTF_TICK_MS", 120)
    max_steps_ui: int = _env_int("OROMA_CTF_MAX_STEPS_UI", 400)


class CTFRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.settings = CTFSettings()
        self.env = CTFEnv(CTFConfig(max_steps=self.settings.max_steps_ui))
        self.state = self.env.reset(seed=int(time.time()) & 0xffffffff)
        self.done = False
        self.last_err: Optional[str] = None

        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        self.shim = UniversalPolicyShim(namespace="game:ctf")

        # For explore learning: collect a small batch of transitions
        self._learn_buf: List[Dict[str, Any]] = []

        # human pending move (for Agent A)
        self._human_action: Optional[int] = None

    def reset(self):
        with self.lock:
            self.env = CTFEnv(CTFConfig(max_steps=self.settings.max_steps_ui))
            self.state = self.env.reset(seed=int(time.time()) & 0xffffffff)
            self.done = False
            self.last_err = None
            self._learn_buf.clear()
            self._human_action = None

    def set_mode(self, mode: str):
        mode = (mode or "").strip()
        if mode in ("oroma_vs_oroma", "oroma_solo"):
            # legacy alias -> explore
            mode = "oroma_vs_oroma_explore"
        if mode not in ("human", "oroma_vs_oroma_policy", "oroma_vs_oroma_explore"):
            mode = "oroma_vs_oroma_explore"
        with self.lock:
            self.settings.mode = mode
            # mode semantics
            if mode == "oroma_vs_oroma_policy":
                self.settings.eps = 0.0
            elif mode == "oroma_vs_oroma_explore":
                self.settings.eps = float(self.settings.eps or 0.08)

    def set_settings(self, eps: Optional[float] = None, explore_moves_per_game: Optional[int] = None,
                     auto_reset: Optional[bool] = None, autoplay: Optional[bool] = None):
        with self.lock:
            if eps is not None:
                self.settings.eps = float(_clamp(float(eps), 0.0, 1.0))
            if explore_moves_per_game is not None:
                try:
                    self.settings.explore_moves_per_game = max(0, int(explore_moves_per_game))
                except Exception:
                    pass
            if auto_reset is not None:
                self.settings.auto_reset = bool(auto_reset)
            if autoplay is not None:
                self.settings.autoplay = bool(autoplay)

    def submit_human_action(self, a: int):
        try:
            a = int(a)
        except Exception:
            return
        if a not in (0, 1, 2, 3, 4):
            return
        with self.lock:
            self._human_action = a

    def _choose_action_for(self, agent: str) -> int:
        # agent: "A" or "B"
        legal = [0, 1, 2, 3, 4]
        side = "X" if agent == "A" else "O"
        # NOTE: CTFEnv exposes `features(agent)` (not `observe`).
        # This returns the 13-dim normalized feature vector documented in mini_program.
        obs = self.env.features(agent)
        # explore move injection: per game, small random action
        if self.settings.mode == "oroma_vs_oroma_explore" and self.settings.explore_moves_per_game > 0:
            # Inject epsilon at decision-level
            if (time.time_ns() & 0xffff) / 0xffff < self.settings.eps:
                return int(legal[int(time.time_ns()) % len(legal)])
        return int(self.shim.choose(obs, legal, side))

    def step_once(self):
        with self.lock:
            try:
                if self.done:
                    if self.settings.auto_reset:
                        self.reset()
                    else:
                        return

                mode = self.settings.mode

                if mode == "human":
                    # Human controls A; B is policy
                    aA = self._human_action if self._human_action is not None else 0
                    self._human_action = None
                    aB = self._choose_action_for("B")
                else:
                    # policy/explore: both by policy shim
                    aA = self._choose_action_for("A")
                    aB = self._choose_action_for("B")

                prev_obs_A = self.env.features("A")
                prev_obs_B = self.env.features("B")
                prev_sh_A = _state_hash_from_obs(prev_obs_A, "X")
                prev_sh_B = _state_hash_from_obs(prev_obs_B, "O")

                st, rewards, done, info = self.env.step({"A": int(aA), "B": int(aB)})
                self.state = st
                self.done = bool(done)

                # Learn buffer only in explore mode
                if mode == "oroma_vs_oroma_explore":
                    # reward sign for the action used (simple)
                    rA = float(rewards.get("A", 0.0))
                    rB = float(rewards.get("B", 0.0))
                    # reduce to -1/0/+1
                    def sgn(x: float) -> int:
                        if x > 1e-9: return 1
                        if x < -1e-9: return -1
                        return 0
                    self._learn_buf.append({"state_hash": prev_sh_A, "action": int(aA), "reward": sgn(rA), "side": "X"})
                    self._learn_buf.append({"state_hash": prev_sh_B, "action": int(aB), "reward": sgn(rB), "side": "O"})

                    # Flush small batches to policy
                    if len(self._learn_buf) >= 64:
                        self.shim.learn_many(self._learn_buf)
                        self._learn_buf.clear()

                self.last_err = None
            except Exception as e:
                self.last_err = f"{type(e).__name__}: {e}"
                current_app.logger.exception("[ctf_ui] step_once failed: %s", e)

    def _loop(self):
        while not self._stop_evt.is_set():
            try:
                if self.settings.autoplay:
                    self.step_once()
                time.sleep(max(0.01, self.settings.tick_ms / 1000.0))
            except Exception:
                current_app.logger.exception("[ctf_ui] loop error")
                time.sleep(0.25)

    def ensure_thread(self):
        with self.lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._loop, name="CTF-Loop", daemon=True)
            self._thread.start()

    def toggle_autoplay(self) -> bool:
        with self.lock:
            self.settings.autoplay = not self.settings.autoplay
            return self.settings.autoplay

    def get_view_state(self) -> Dict[str, Any]:
        with self.lock:
            ascii_map = self.env.render_ascii()
            return {
                "ok": True,
                "mode": self.settings.mode,
                "autoplay": bool(self.settings.autoplay),
                "auto_reset": bool(self.settings.auto_reset),
                "eps": float(self.settings.eps),
                "explore_moves_per_game": int(self.settings.explore_moves_per_game),
                "tick_ms": int(self.settings.tick_ms),
                "namespace": "game:ctf",
                "policy_rules": int(self.shim.policy_rules_count()) if self.shim.have_up else 0,
                "have_up": bool(self.shim.have_up),
                "done": bool(self.done),
                "err": self.last_err,
                "ascii": ascii_map,
                "scores": {
                    "A": int(getattr(self.state, "score_A", 0)),
                    "B": int(getattr(self.state, "score_B", 0)),
                    "steps": int(getattr(self.state, "steps", 0)),
                },
            }


_runtime: Optional[CTFRuntime] = None
_runtime_lock = threading.Lock()


def _rt() -> CTFRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = CTFRuntime()
            _runtime.ensure_thread()
        return _runtime


@ctf_bp.route("/", methods=["GET"])
def ctf_page():
    rt = _rt()
    rt.ensure_thread()
    return render_template("ctf.html")


@ctf_bp.route("/api/state", methods=["GET"])
def ctf_state():
    rt = _rt()
    rt.ensure_thread()
    return jsonify(rt.get_view_state())


@ctf_bp.route("/api/reset", methods=["POST"])
def ctf_reset():
    rt = _rt()
    rt.ensure_thread()
    rt.reset()
    return jsonify({"ok": True})


@ctf_bp.route("/api/toggle", methods=["POST"])
def ctf_toggle():
    rt = _rt()
    rt.ensure_thread()
    v = rt.toggle_autoplay()
    return jsonify({"ok": True, "autoplay": bool(v)})


@ctf_bp.route("/api/mode", methods=["POST"])
def ctf_mode():
    rt = _rt()
    rt.ensure_thread()
    data = request.get_json(force=True, silent=True) or {}
    rt.set_mode(str(data.get("mode", "")).strip())
    return jsonify({"ok": True, "mode": rt.settings.mode})


@ctf_bp.route("/api/settings", methods=["POST"])
def ctf_settings():
    rt = _rt()
    rt.ensure_thread()
    data = request.get_json(force=True, silent=True) or {}
    eps = data.get("eps", None)
    em = data.get("explore_moves_per_game", None)
    ar = data.get("auto_reset", None)
    ap = data.get("autoplay", None)
    rt.set_settings(eps=eps, explore_moves_per_game=em, auto_reset=ar, autoplay=ap)
    return jsonify({"ok": True, **rt.get_view_state()})


@ctf_bp.route("/api/move", methods=["POST"])
def ctf_move():
    rt = _rt()
    rt.ensure_thread()
    data = request.get_json(force=True, silent=True) or {}
    a = data.get("action", None)
    if a is None:
        return jsonify({"ok": False, "err": "missing action"}), 400
    rt.submit_human_action(a)
    return jsonify({"ok": True})

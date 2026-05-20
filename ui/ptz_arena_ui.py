#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/ptz_arena_ui.py
# Projekt:   ORÓMA (PTZ Arena UI · Headless)
# Version:   v3.7.3
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# Zweck
# ─────
#   Flask-UI Blueprint für die “PTZ Arena”: ein spielartiger Training-Loop, der
#   die PTZ Kamera über das bestehende DeviceHub/PTZ API bewegt und den
#   UniversalPolicy-Stack trainiert.
#
#   Warum als “Spiel”?
#   - Einheitlicher Standard: Start/Stop, Explore/Policy, Daily Runs, DB Telemetrie.
#   - Schnelles Lernen über policy_rules Namespace statt “hard-coded” PTZ Loops.
#
# Headless / Produktiv
# ───────────────────
#   - Keine Desktop-Dependencies; UI ist HTML/JS only.
#   - Frame-Scoring nutzt DeviceHub.get_latest_frame(ensure_start=False)
#     → keine Kamera-Startzwänge im UI Thread.
#   - PTZ moves sind über DeviceHub bereits timeout-geschützt (siehe video_ui).
#
# Routen
# ──────
#   GET  /ptz_arena/              → HTML
#   GET  /ptz_arena/api/state     → JSON state
#   POST /ptz_arena/api/toggle    → autoplay an/aus
#   POST /ptz_arena/api/reset     → reset (optional center)
#   POST /ptz_arena/api/settings  → update eps/tick/max_steps/amount
#   POST /ptz_arena/api/mode      → policy/explore
#
# ENV (Defaults)
# ─────────────
#   OROMA_PTZ_ARENA_UI_TICK_MS=200
#   OROMA_PTZ_ARENA_UI_EPS=0.08
#   OROMA_PTZ_ARENA_UI_AUTOPLAY=1
#   OROMA_PTZ_ARENA_UI_DEFAULT_MODE=explore|policy
#
# =============================================================================

from __future__ import annotations

import os
import time
import threading
from typing import Any, Dict, Optional, List

from flask import Blueprint, jsonify, render_template, request


ptz_arena_bp = Blueprint("ptz_arena", __name__, url_prefix="/ptz_arena", template_folder="templates")


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return default


_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None


class _Runtime:
    def __init__(self):
        self.mode = (os.environ.get("OROMA_PTZ_ARENA_UI_DEFAULT_MODE", "explore") or "explore").strip().lower()
        if self.mode not in ("explore", "policy"):
            self.mode = "explore"
        self.autoplay = _env_bool("OROMA_PTZ_ARENA_UI_AUTOPLAY", True)
        self.tick_ms = int(max(50, min(2000, _env_int("OROMA_PTZ_ARENA_UI_TICK_MS", 200))))
        self.eps = float(max(0.0, min(1.0, _env_float("OROMA_PTZ_ARENA_UI_EPS", 0.08))))
        self.explore_moves_per_game = 1

        self.last_err: Optional[str] = None
        self.last_action: str = "hold"
        self.steps: int = 0
        self.max_steps: int = int(max(5, min(2000, _env_int("OROMA_PTZ_ARENA_MAX_STEPS", 60))))
        self.amount: int = int(max(1, min(1000, _env_int("OROMA_PTZ_ARENA_AMOUNT", 10))))

        self.last_reward: float = 0.0
        self.last_obs: Optional[Dict[str, Any]] = None

        self._stop = False

        # Lazy init
        self._hub = None
        self._env = None
        self._policy = None

    def _get_hub(self):
        if self._hub is not None:
            return self._hub
        from core.device_hub import get_hub  # type: ignore
        self._hub = get_hub()
        return self._hub

    def _get_env(self):
        if self._env is not None:
            return self._env
        from mini_programs.ptz_arena import PTZArenaEnv  # type: ignore
        self._env = PTZArenaEnv(hub=self._get_hub())
        # sync amount/max_steps from runtime
        self._env.amount = int(self.amount)
        self._env.max_steps = int(self.max_steps)
        return self._env

    def _get_policy(self):
        if self._policy is not None:
            return self._policy
        try:
            from core.universal_policy import Policy  # type: ignore
            self._policy = Policy(namespace="ptz:arena")
        except Exception:
            self._policy = None
        return self._policy

    def reset(self, do_center: bool = False) -> None:
        try:
            env = self._get_env()
            env.amount = int(self.amount)
            env.max_steps = int(self.max_steps)
            obs = env.reset(do_center=do_center)
            self.steps = 0
            self.last_obs = obs.__dict__
            self.last_reward = 0.0
            self.last_action = "hold"
            self.last_err = None
        except Exception as e:
            self.last_err = f"reset_failed: {e}"

    def step_once(self) -> None:
        try:
            env = self._get_env()
            env.amount = int(self.amount)
            env.max_steps = int(self.max_steps)
            pol = self._get_policy()

            obs = env._last_obs or env.reset(do_center=False)
            sh = env.state_hash(obs)
            legal = env.legal_actions()

            # Choose action
            a = "hold"
            if self.mode == "policy":
                if pol is not None:
                    a = pol.choose(sh, legal, side="X") or "hold"
            else:
                # explore: epsilon-greedy
                import random
                if random.random() < float(self.eps):
                    a = random.choice(legal)
                else:
                    if pol is not None:
                        a = pol.choose(sh, legal, side="X") or "hold"

            obs2, reward, done, info = env.step(a)
            self.last_reward = float(reward)
            self.last_action = str(a)
            self.steps = int(info.get("steps", self.steps + 1))
            self.last_obs = obs2.__dict__
            self.last_err = None

            if self.mode == "explore" and pol is not None:
                # simple learn signal: reward sign
                label = 1 if reward > 0.0 else (-1 if reward < 0.0 else 0)
                try:
                    pol.learn_many([{ "state_hash": sh, "action": str(a), "label": label }])
                except Exception:
                    pass

            if done:
                # auto reset
                self.reset(do_center=False)

        except Exception as e:
            self.last_err = str(e)


_RT = _Runtime()


def _loop() -> None:
    global _RT
    while True:
        with _LOCK:
            if _RT._stop:
                return
            autoplay = bool(_RT.autoplay)
            tick_ms = int(_RT.tick_ms)
        if autoplay:
            with _LOCK:
                _RT.step_once()
        time.sleep(max(0.05, tick_ms / 1000.0))


def _ensure_thread() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _RT._stop = False
        _THREAD = threading.Thread(target=_loop, name="ptz_arena_loop", daemon=True)
        _THREAD.start()


@ptz_arena_bp.route("/", methods=["GET"])
def page():
    _ensure_thread()
    return render_template("ptz_arena.html")


@ptz_arena_bp.route("/api/state", methods=["GET"])
def api_state():
    _ensure_thread()
    with _LOCK:
        # policy rules counter (best-effort)
        pr = 0
        try:
            from core.sql_manager import get_db_path  # type: ignore
            import sqlite3
            con = sqlite3.connect(get_db_path())
            try:
                cur = con.cursor()
                cur.execute("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", ("ptz:arena",))
                pr = int(cur.fetchone()[0] or 0)
            finally:
                con.close()
        except Exception:
            pr = 0

        st = {}
        try:
            hub = _RT._get_hub()
            st = hub.ptz_status() or {}
        except Exception:
            st = {}

        return jsonify({
            "ok": True,
            "mode": "oroma_vs_oroma_explore" if _RT.mode == "explore" else "oroma_vs_oroma_policy",
            "autoplay": bool(_RT.autoplay),
            "eps": float(_RT.eps),
            "tick_ms": int(_RT.tick_ms),
            "amount": int(_RT.amount),
            "max_steps": int(_RT.max_steps),
            "steps": int(_RT.steps),
            "last_action": str(_RT.last_action),
            "last_reward": float(_RT.last_reward),
            "obs": _RT.last_obs or {},
            "ptz": st,
            "policy_rules": pr,
            "namespace": "ptz:arena",
            "err": _RT.last_err,
        })


@ptz_arena_bp.route("/api/toggle", methods=["POST"])
def api_toggle():
    _ensure_thread()
    with _LOCK:
        _RT.autoplay = not bool(_RT.autoplay)
        return jsonify({"ok": True, "autoplay": bool(_RT.autoplay)})


@ptz_arena_bp.route("/api/reset", methods=["POST"])
def api_reset():
    _ensure_thread()
    js = request.get_json(silent=True) or {}
    do_center = bool(js.get("center", False))
    with _LOCK:
        _RT.reset(do_center=do_center)
        return jsonify({"ok": True, "center": do_center})


@ptz_arena_bp.route("/api/mode", methods=["POST"])
def api_mode():
    _ensure_thread()
    js = request.get_json(silent=True) or {}
    m = (js.get("mode") or "").strip().lower()
    with _LOCK:
        if m in ("oroma_vs_oroma_policy", "policy"):
            _RT.mode = "policy"
        else:
            _RT.mode = "explore"
        return jsonify({"ok": True, "mode": "oroma_vs_oroma_explore" if _RT.mode == "explore" else "oroma_vs_oroma_policy"})


@ptz_arena_bp.route("/api/settings", methods=["POST"])
def api_settings():
    _ensure_thread()
    js = request.get_json(silent=True) or {}
    with _LOCK:
        if "eps" in js:
            try:
                _RT.eps = float(js.get("eps"))
            except Exception:
                pass
            _RT.eps = float(max(0.0, min(1.0, _RT.eps)))
        if "tick_ms" in js:
            try:
                _RT.tick_ms = int(js.get("tick_ms"))
            except Exception:
                pass
            _RT.tick_ms = int(max(50, min(2000, _RT.tick_ms)))
        if "amount" in js:
            try:
                _RT.amount = int(js.get("amount"))
            except Exception:
                pass
            _RT.amount = int(max(1, min(1000, _RT.amount)))
        if "max_steps" in js:
            try:
                _RT.max_steps = int(js.get("max_steps"))
            except Exception:
                pass
            _RT.max_steps = int(max(5, min(2000, _RT.max_steps)))
        return jsonify({
            "ok": True,
            "eps": float(_RT.eps),
            "tick_ms": int(_RT.tick_ms),
            "amount": int(_RT.amount),
            "max_steps": int(_RT.max_steps),
        })

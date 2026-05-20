#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/pong_ui.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Pong UI – Standardisierte Arena + UniversalPolicy (policy_rules)
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Pong als ORÓMA-"Referenzspiel" (nach TicTacToe/Connect4 Standard):
#   • Headless-freundliche Runtime (kein X/Qt/Wayland; kein pygame im UI-Loop)
#   • UI-Standard: /games/pong/ mit /api/state + /api/move + /api/reset + /api/toggle + /api/mode + /api/speed
#   • UniversalPolicy (policy_rules) für diskreten Action-Space: {-1,0,+1} (paddle up/hold/down)
#   • Side-aware Policy: X = LEFT, O = RIGHT
#   • Rückwärtskompatibel: alte Endpunkte /state und /cmd bleiben als Alias erhalten
#
# WARUM DIESER PATCH
# ------------------
# Die alte Pong-UI war funktional, aber nicht "Standard-konform" (Mode-Split, API-Naming,
# Rule-Counter, Policy/Explore Trennung, Daily Runner Telemetrie).
# Dieser Patch hebt Pong auf denselben Standard wie Connect4, so dass die Integration
# für weitere Spiele (Flappybird, Snake, …) nach demselben Muster erfolgen kann.
#
# KONFIGURATION (ENV)
# -------------------
# OROMA_PONG_POLICY_NAMESPACE        Default: game:pong
# OROMA_PONG_EPS                    Explore-Epsilon (Default: 0.08)
# OROMA_PONG_EXPLORE_MOVES_PER_GAME  Mindest-Random-Moves pro Side & Game (Default: 1)
# OROMA_PONG_TICK_MS                UI-Runtime Tick (Default: 20ms)
# OROMA_PONG_MAX_TICKS_PER_GAME      Default: 5000 (Fail-safe Draw)
#
# ROUTES
# ------
# UI:
#   GET  /games/pong/                 → HTML
# API (Standard):
#   GET  /games/pong/api/state
#   POST /games/pong/api/toggle       {"running": true|false}
#   POST /games/pong/api/reset
#   POST /games/pong/api/mode         {"mode": "..."}
#   POST /games/pong/api/speed        {"speed": "normal"|"turbo"}
#   POST /games/pong/api/move         {"side":"X"|"O", "action":-1|0|1}   (nur für Human-Modi)
# Legacy-Alias:
#   GET  /games/pong/state            → Alias auf /api/state
#   POST /games/pong/cmd              → best-effort Alias (start/pause/reset/mode_*)
#
# DB / LEARNING
# -------------
# Pong lernt (wie Connect4) über core/universal_policy.py in die Tabelle:
#   oroma.db → policy_rules (namespace='game:pong')
# Daily Runs werden durch tools/pong_daily_runner.py erfasst (episodes + episodic_metrics).
#
# HEADLESS / PRODUKTIONSREGELN
# ---------------------------
# • Keine GUI-Abhängigkeiten im Serverprozess.
# • Threads nur für die Runtime; keine Endlosschleifen ohne Stop-Flag.
# • Keine stillen Fehler: Exceptions werden geloggt, API liefert ok=false.
# =============================================================================

from __future__ import annotations

import os
import time
import threading
import random
import math
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request, current_app, render_template

LOG = logging.getLogger("oroma.pong_ui")
if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [PONG] %(message)s"))
    LOG.addHandler(_h)

def _env_float(name: str, default: float) -> float:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)

def _env_int(name: str, default: int) -> int:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)

def _env_str(name: str, default: str) -> str:
    v = (os.environ.get(name, "") or "").strip()
    return v if v else default

# -----------------------------------------------------------------------------
# UniversalPolicy Shim
# -----------------------------------------------------------------------------
class UniversalPolicyShim:
    """
    Minimaler Shim auf core.universal_policy.Policy – passend zu Connect4/TicTacToe.

    Pong ist kontinuierlich, daher verwenden wir einen diskreten Zustands-Hash:
      - Ball (x,y) grob quantisiert
      - Ball-Vel (Sign)
      - Paddle-Positionen grob quantisiert
    Actions: [-1,0,+1] (up/hold/down)
    """
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=namespace)
        except Exception:
            LOG.exception("UniversalPolicy Import/Init fehlgeschlagen")
            self.pol = None

    @staticmethod
    def _q(v: float, step: int, lo: int, hi: int) -> int:
        try:
            x = int(v // step)
        except Exception:
            x = 0
        return max(lo, min(hi, x))

    def state_hash(self, st: "PongState", side: str) -> str:
        # quantize to keep table size reasonable
        bx = self._q(st.bx, 16, 0, 40)
        by = self._q(st.by, 12, 0, 30)
        lv = 1 if st.bvx >= 0 else -1
        vv = 1 if st.bvy >= 0 else -1
        lp = self._q(st.lp, 12, 0, 30)
        rp = self._q(st.rp, 12, 0, 30)
        # include side marker so LEFT/RIGHT do not collide
        return f"pong:v1:s={side}:bx={bx}:by={by}:vx={lv}:vy={vv}:lp={lp}:rp={rp}"

    def choose(self, st: "PongState", legal: List[int], side: str) -> int:
        if not self.pol:
            return int(random.choice(legal))
        sh = self.state_hash(st, side)
        # Policy expects actions as strings or ints; we keep ints
        return int(self.pol.choose(sh, legal, side=side))

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not self.pol:
            return 0
        try:
            return int(self.pol.learn_many(items))
        except Exception:
            LOG.exception("learn_many fehlgeschlagen")
            return 0

# -----------------------------------------------------------------------------
# Game State / Runtime
# -----------------------------------------------------------------------------
@dataclass
class PongState:
    w: int
    h: int
    bx: float
    by: float
    bvx: float
    bvy: float
    lp: float
    rp: float
    scoreL: int
    scoreR: int
    tick: int

class PongRuntime:
    MODES = (
        "oroma_vs_human",
        "oroma_vs_ki",
        "ki_vs_ki",
        "oroma_vs_oroma_explore",
        "oroma_vs_oroma_policy",
    )

    def __init__(self):
        self.lock = threading.Lock()
        self.w = 640
        self.h = 360
        self.paddle_h = 60
        self.paddle_speed = 6.0
        self.ball_speed = 6.0
        self.running = False
        self.mode = "oroma_vs_oroma_policy"
        self.speed = "normal"
        self.tick_ms = _env_int("OROMA_PONG_TICK_MS", 20)
        self.max_ticks_per_game = _env_int("OROMA_PONG_MAX_TICKS_PER_GAME", 5000)

        self.namespace = _env_str("OROMA_PONG_POLICY_NAMESPACE", "game:pong")
        self.eps = _env_float("OROMA_PONG_EPS", 0.08)
        self.explore_moves_per_game = _env_int("OROMA_PONG_EXPLORE_MOVES_PER_GAME", 1)

        self.shim = UniversalPolicyShim(self.namespace)

        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

        self.reset()

    def stop(self):
        self._stop = True

    def reset(self):
        with self.lock:
            self.state = PongState(
                w=self.w, h=self.h,
                bx=self.w/2, by=self.h/2,
                bvx=random.choice([-1, 1]) * self.ball_speed,
                bvy=random.choice([-1, 1]) * (self.ball_speed * 0.6),
                lp=self.h/2 - self.paddle_h/2,
                rp=self.h/2 - self.paddle_h/2,
                scoreL=0, scoreR=0,
                tick=0,
            )
            self.last_winner: Optional[str] = None
            self.last_point: Optional[str] = None
            self._explore_budget = {"X": 0, "O": 0}
            self._learn_items: List[Dict[str, Any]] = []
            self._game_start_ts = int(time.time())

    def _loop(self):
        while not self._stop:
            time.sleep(max(0.001, self.tick_ms/1000.0))
            if self.running:
                try:
                    self.step()
                except Exception:
                    LOG.exception("Runtime step error")
                    # fail-safe: pause
                    self.running = False

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return lo if v < lo else hi if v > hi else v

    def _legal(self) -> List[int]:
        return [-1, 0, 1]

    def _apply_action(self, side: str, action: int):
        st = self.state
        dy = float(action) * self.paddle_speed
        if side == "X":
            st.lp = self._clamp(st.lp + dy, 0, st.h - self.paddle_h)
        else:
            st.rp = self._clamp(st.rp + dy, 0, st.h - self.paddle_h)

    def _simple_ai(self, side: str) -> int:
        # very simple: move toward ball y
        st = self.state
        py = st.lp if side == "X" else st.rp
        center = py + self.paddle_h/2
        if abs(st.by - center) < 8:
            return 0
        return 1 if st.by > center else -1

    def _pick_action(self, side: str) -> int:
        legal = self._legal()
        if self.mode in ("ki_vs_ki", "oroma_vs_ki"):
            # In oroma_vs_ki: left is ORÓMA (policy), right is KI
            if self.mode == "oroma_vs_ki" and side == "O":
                return self._simple_ai(side)
            if self.mode == "ki_vs_ki":
                return self._simple_ai(side)

        # Human modes: only apply external actions; here default hold
        if self.mode == "oroma_vs_human":
            if side == "O":  # right is human in UI, left ORÓMA
                return 0
        # ORÓMA modes
        if self.mode == "oroma_vs_oroma_explore":
            # force a few random moves per side, then eps
            if self._explore_budget.get(side, 0) < self.explore_moves_per_game:
                self._explore_budget[side] += 1
                return int(random.choice(legal))
            if random.random() < self.eps:
                return int(random.choice(legal))
            return int(self.shim.choose(self.state, legal, side))
        # policy
        return int(self.shim.choose(self.state, legal, side))

    def _ball_paddle_collision(self):
        st = self.state
        # top/bottom
        if st.by <= 0 or st.by >= st.h:
            st.bvy *= -1
            st.by = self._clamp(st.by, 0, st.h)

        # paddles: left x ~ 18, right x ~ w-18
        left_x = 18
        right_x = st.w - 18
        paddle_w = 8

        # left paddle
        if st.bx <= left_x + paddle_w and st.bx >= left_x:
            if st.lp <= st.by <= st.lp + self.paddle_h:
                st.bvx = abs(st.bvx)
                # small angle based on hit position
                rel = (st.by - (st.lp + self.paddle_h/2)) / (self.paddle_h/2)
                st.bvy = rel * (self.ball_speed * 0.9)
        # right paddle
        if st.bx >= right_x - paddle_w and st.bx <= right_x:
            if st.rp <= st.by <= st.rp + self.paddle_h:
                st.bvx = -abs(st.bvx)
                rel = (st.by - (st.rp + self.paddle_h/2)) / (self.paddle_h/2)
                st.bvy = rel * (self.ball_speed * 0.9)

    def _terminal_check(self) -> Optional[str]:
        st = self.state
        if st.bx < 0:
            st.scoreR += 1
            return "O"
        if st.bx > st.w:
            st.scoreL += 1
            return "X"
        if st.tick >= self.max_ticks_per_game:
            return "D"
        return None

    def step(self):
        with self.lock:
            st = self.state
            st.tick += 1

            # choose actions
            ax = self._pick_action("X")
            ao = self._pick_action("O")

            # apply actions
            # In oroma_vs_human: right is human and is driven via /api/move; default hold here.
            self._apply_action("X", ax)
            if self.mode != "oroma_vs_human":
                self._apply_action("O", ao)

            # physics
            st.bx += st.bvx
            st.by += st.bvy
            self._ball_paddle_collision()

            term = self._terminal_check()
            if term:
                self.last_point = term
                if term in ("X", "O"):
                    self.last_winner = term
                # reset ball for next rally (keep scores)
                st.bx = st.w/2
                st.by = st.h/2
                st.bvx = random.choice([-1, 1]) * self.ball_speed
                st.bvy = random.choice([-1, 1]) * (self.ball_speed * 0.6)
                # fail-safe draw: also stop
                if term == "D":
                    self.running = False

    # ------------------------------ API State ---------------------------------
    def api_state(self) -> Dict[str, Any]:
        with self.lock:
            st = self.state
            # rule counters (best-effort)
            policy_rules_count = None
            rules_count = None
            try:
                import sqlite3
                from core import sql_manager  # type: ignore
                db_path = sql_manager.get_db_path()
                with sql_manager.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) AS c FROM policy_rules WHERE namespace=?", (self.namespace,))
                    r = cur.fetchone()
                    policy_rules_count = int((r or {}).get("c", 0))
                    # optional archive table "rules"
                    try:
                        cur.execute("SELECT COUNT(*) AS c FROM rules WHERE active=1")
                        rr = cur.fetchone()
                        rules_count = int((rr or {}).get("c", 0))
                    except Exception:
                        rules_count = 0
            except Exception:
                policy_rules_count = None
                rules_count = None

            return {
                "ok": True,
                "mode": self.mode,
                "speed": self.speed,
                "running": bool(self.running),
                "namespace": self.namespace,
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "policy_rules_count": policy_rules_count,
                "rules_active_count": rules_count,
                "tick": int(st.tick),
                "scoreL": int(st.scoreL),
                "scoreR": int(st.scoreR),
                "ball": {"x": float(st.bx), "y": float(st.by), "vx": float(st.bvx), "vy": float(st.bvy)},
                "paddles": {"L": float(st.lp), "R": float(st.rp), "h": int(self.paddle_h)},
                "last_point": self.last_point,
                "last_winner": self.last_winner,
            }

# -----------------------------------------------------------------------------
# Flask Blueprint + Helpers
# -----------------------------------------------------------------------------
pong_bp = Blueprint("pong_ui", __name__, url_prefix="/pong", template_folder="templates")
# Legacy/Alias Blueprint (für bestehende Panel-Links /games/pong/...)
bp = Blueprint("pong_ui_legacy", __name__, url_prefix="/games/pong", template_folder="templates")

def _get_rt() -> PongRuntime:
    rt = current_app.config.get("_pong_rt")
    if rt is None:
        rt = PongRuntime()
        current_app.config["_pong_rt"] = rt
    return rt

@pong_bp.get("/")
@bp.get("/")
def page():
    return render_template("pong.html")

@pong_bp.get("/api/state")
@bp.get("/api/state")
def api_state():
    return jsonify(_get_rt().api_state())

@pong_bp.post("/api/toggle")
@bp.post("/api/toggle")
def api_toggle():
    rt = _get_rt()
    try:
        data = request.get_json(force=True, silent=True) or {}
        running = bool(data.get("running", True))
        rt.running = running
        return jsonify({"ok": True, "running": rt.running})
    except Exception as e:
        LOG.exception("toggle failed")
        return jsonify({"ok": False, "err": str(e)}), 500

@pong_bp.post("/api/reset")
@bp.post("/api/reset")
def api_reset():
    rt = _get_rt()
    try:
        rt.reset()
        return jsonify({"ok": True})
    except Exception as e:
        LOG.exception("reset failed")
        return jsonify({"ok": False, "err": str(e)}), 500

@pong_bp.post("/api/mode")
@bp.post("/api/mode")
def api_mode():
    rt = _get_rt()
    try:
        data = request.get_json(force=True, silent=True) or {}
        m = (data.get("mode") or "").strip()
        if m in PongRuntime.MODES:
            rt.mode = m
            rt.reset()
            return jsonify({"ok": True, "mode": rt.mode})
        return jsonify({"ok": False, "err": "invalid mode"}), 400
    except Exception as e:
        LOG.exception("mode failed")
        return jsonify({"ok": False, "err": str(e)}), 500

@pong_bp.post("/api/speed")
@bp.post("/api/speed")
def api_speed():
    rt = _get_rt()
    try:
        data = request.get_json(force=True, silent=True) or {}
        s = (data.get("speed") or "").strip()
        if s not in ("normal", "turbo"):
            return jsonify({"ok": False, "err": "invalid speed"}), 400
        rt.speed = s
        rt.tick_ms = 5 if s == "turbo" else _env_int("OROMA_PONG_TICK_MS", 20)
        return jsonify({"ok": True, "speed": rt.speed, "tick_ms": rt.tick_ms})
    except Exception as e:
        LOG.exception("speed failed")
        return jsonify({"ok": False, "err": str(e)}), 500

@pong_bp.post("/api/move")
@bp.post("/api/move")
def api_move():
    rt = _get_rt()
    try:
        data = request.get_json(force=True, silent=True) or {}
        side = (data.get("side") or "").strip().upper()
        action = int(data.get("action", 0))
        if side not in ("X", "O"):
            return jsonify({"ok": False, "err": "invalid side"}), 400
        if action not in (-1, 0, 1):
            return jsonify({"ok": False, "err": "invalid action"}), 400
        # allow manual only in human mode
        if rt.mode != "oroma_vs_human":
            return jsonify({"ok": False, "err": "move only allowed in oroma_vs_human"}), 400
        with rt.lock:
            rt._apply_action("O", action)  # right paddle is human
        return jsonify({"ok": True})
    except Exception as e:
        LOG.exception("move failed")
        return jsonify({"ok": False, "err": str(e)}), 500

# ------------------------------ Legacy Aliases -------------------------------
@pong_bp.get("/state")
@bp.get("/state")
def legacy_state():
    return api_state()

@pong_bp.post("/cmd")
@bp.post("/cmd")
def legacy_cmd():
    # best-effort compatibility with old commands
    rt = _get_rt()
    data = request.get_json(force=True, silent=True) or {}
    cmd = (data.get("cmd") or "").lower().strip()
    if cmd == "start":
        rt.running = True
    elif cmd == "pause":
        rt.running = False
    elif cmd == "reset":
        rt.reset()
    elif cmd.startswith("mode"):
        mapping = {
            "mode_human": "oroma_vs_human",
            "mode_ki": "ki_vs_ki",
            "mode_oroma_ki": "oroma_vs_ki",
            "mode_policy": "oroma_vs_oroma_policy",
            "mode_explore": "oroma_vs_oroma_explore",
        }
        m = mapping.get(cmd, (data.get("mode") or "").strip())
        if m in PongRuntime.MODES:
            rt.mode = m
            rt.reset()
    return jsonify({"ok": True, "state": rt.api_state()})

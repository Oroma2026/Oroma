#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/ptz_target_ui.py
# Projekt:   ORÓMA (PTZ Targeting · UI + Policy)
# Version:   v3.7.3
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# Zweck
# ─────
#   Flask-UI für das "PTZ Targeting" Spiel:
#     - Ziel: bewegtes Target (Motion-Centroid) in Bildmitte bringen und halten
#     - Actions: left/right/up/down/zoom_in/zoom_out/hold
#
#   Standard-UI wie bei den anderen Games:
#     - Start/Stop (autoplay)
#     - Default Explore Mode (eps>0)
#     - UniversalPolicy Namespace: "ptz:target"
#     - /ptz_target/api/state + /toggle + /reset + /settings
#
#   Headless-/Produktionshinweise
#   ─────────────────────────────
#   - Keine X11/Qt Abhängigkeiten.
#   - Nutzt die laufende Hub-Konfiguration (wie Video-UI):
#       core.device_hub.get_hub(); hub.get_latest_frame(ensure_start=False)
#   - PTZ Commands laufen über hub.ptz_command()
#
# =============================================================================

from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, render_template, request


bp = Blueprint("ptz_target_bp", __name__, url_prefix="/ptz_target")
# Kompatibilität mit games_ui attr_fallbacks
ptz_target_bp = bp


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


def _now_ts() -> int:
    return int(time.time())


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _to_gray_small(frame: Any, w: int = 160, h: int = 90) -> Optional[Any]:
    """Convert BGR/RGB frame to small grayscale uint8."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None
    try:
        if frame is None:
            return None
        arr = frame
        if not hasattr(arr, "shape"):
            return None
        if arr.ndim == 2:
            g = arr
        else:
            # assume BGR from OpenCV pipelines
            g = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        g2 = cv2.resize(g, (int(w), int(h)), interpolation=cv2.INTER_AREA)
        if g2.dtype != np.uint8:
            g2 = g2.astype(np.uint8, copy=False)
        return g2
    except Exception:
        return None


def _motion_centroid(g1: Any, g2: Any, thr: int = 25) -> Tuple[float, float, float]:
    """Return (dx,dy,strength) in normalized [-1..1] coords and strength [0..1]."""
    try:
        import numpy as np  # type: ignore
    except Exception:
        return 0.0, 0.0, 0.0
    try:
        d = np.abs(g2.astype(np.int16) - g1.astype(np.int16)).astype(np.uint8)
        m = (d >= int(thr)).astype(np.uint8)
        area = float(m.sum())
        h, w = m.shape[:2]
        if area <= 1.0:
            return 0.0, 0.0, 0.0
        ys, xs = np.nonzero(m)
        cx = float(xs.mean())
        cy = float(ys.mean())
        dx = (cx - (w / 2.0)) / (w / 2.0)
        dy = (cy - (h / 2.0)) / (h / 2.0)
        dx = float(_clamp(dx, -1.0, 1.0))
        dy = float(_clamp(dy, -1.0, 1.0))
        # strength: ratio of active pixels (clamped)
        strength = area / float(w * h)
        strength = float(_clamp(strength * 5.0, 0.0, 1.0))
        return dx, dy, strength
    except Exception:
        return 0.0, 0.0, 0.0


def _sharpness_var(g: Any) -> float:
    try:
        import cv2  # type: ignore
    except Exception:
        return 0.0
    try:
        lap = cv2.Laplacian(g, cv2.CV_64F)
        v = float(lap.var())
        if not math.isfinite(v):
            return 0.0
        return max(0.0, v)
    except Exception:
        return 0.0


def _att_score(motion_norm: float, sharp_var: float) -> float:
    # small score proxy like video_ui; used only for display
    try:
        div = 6.5
        sharp_n = float(math.log1p(max(0.0, float(sharp_var))) / div)
        sharp_n = float(_clamp(sharp_n, 0.0, 1.0))
    except Exception:
        sharp_n = 0.0
    return float(0.8 * float(motion_norm) + 0.2 * float(sharp_n))


class PolicyShim:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.have_up = False
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=namespace)
            self.have_up = True
        except Exception:
            self.pol = None
            self.have_up = False

    def choose(self, state_hash: str, legal: List[str]) -> str:
        if not legal:
            return "hold"
        if self.pol is None:
            return random.choice(legal)
        a = self.pol.choose(state_hash, legal, side="X")
        if not a:
            return random.choice(legal)
        return str(a)


@dataclass
class TargetState:
    ok: bool
    dx: float
    dy: float
    dist: float
    strength: float
    motion: float
    sharp: float
    score: float
    pan: int
    tilt: int
    zoom: int


class PTZTargetRuntime:
    def __init__(self) -> None:
        self.namespace = "ptz:target"
        self.mode = "oroma_vs_oroma_explore"  # default explore
        self.eps = _env_float("OROMA_PTZ_TARGET_EPS", 0.08)
        self.explore_moves_per_game = _env_int("OROMA_PTZ_TARGET_EXPLORE_MOVES_PER_GAME", 1)

        self.dt_ms = _env_int("OROMA_PTZ_TARGET_DT_MS", 250)
        self.amount = _env_int("OROMA_PTZ_TARGET_AMOUNT", 10)
        self.max_steps = _env_int("OROMA_PTZ_TARGET_MAX_STEPS", 40)
        self.auto_reset = True

        self.autoplay = False
        self.last_action = "hold"
        self.last_reward = 0.0

        self._prev_dist: Optional[float] = None
        self._steps = 0
        self._lock = 0
        self._done = False

        self._g_prev = None

        self.shim = PolicyShim(self.namespace)

    def reset(self) -> None:
        self._prev_dist = None
        self._steps = 0
        self._lock = 0
        self._done = False
        self.last_action = "hold"
        self.last_reward = 0.0
        self._g_prev = None

    def _get_hub(self):
        from core.device_hub import get_hub  # type: ignore
        return get_hub()

    def _read_target_state(self) -> TargetState:
        hub = self._get_hub()
        st = hub.ptz_status() or {}
        # values
        def _v(path: List[str], default: int = 0) -> int:
            cur = st
            for p in path:
                if not isinstance(cur, dict):
                    return default
                cur = cur.get(p)
            try:
                return int(cur)
            except Exception:
                return default

        pan = _v(["controls", "pan_absolute", "value"], 0)
        tilt = _v(["controls", "tilt_absolute", "value"], 0)
        zoom = _v(["controls", "zoom_absolute", "value"], 100)

        # One-frame update (fast): use previous grayscale stored in runtime.
        # This avoids blocking /api/state with sleep() and keeps the UI snappy.
        f2, _ts2 = hub.get_latest_frame(ensure_start=False)
        if f2 is None:
            return TargetState(False, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, pan, tilt, zoom)

        g2 = _to_gray_small(f2)
        if g2 is None:
            return TargetState(False, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, pan, tilt, zoom)

        g1 = self._g_prev
        self._g_prev = g2
        if g1 is None:
            # Need at least two frames to compute motion centroid.
            sharp = float(_sharpness_var(g2))
            return TargetState(False, 0.0, 0.0, 1.0, 0.0, 0.0, sharp, 0.0, pan, tilt, zoom)

        # motion (norm) + centroid
        try:
            import numpy as np  # type: ignore
            motion = float(np.mean(np.abs(g2.astype(np.int16) - g1.astype(np.int16)))) / 255.0
        except Exception:
            motion = 0.0
        dx, dy, strength = _motion_centroid(g1, g2)
        dist = float(math.sqrt(dx * dx + dy * dy))
        sharp = float(_sharpness_var(g2))
        score = float(_att_score(float(motion), float(sharp)))

        return TargetState(True, dx, dy, dist, strength, float(motion), float(sharp), score, pan, tilt, zoom)

    def _state_hash(self, s: TargetState) -> str:
        # discretize
        def b(x: float, bins: int, lo: float, hi: float) -> int:
            x = float(_clamp(x, lo, hi))
            if bins <= 1:
                return 0
            r = (x - lo) / (hi - lo)
            return int(_clamp(math.floor(r * bins), 0, bins - 1))

        dx_b = b(s.dx, 11, -1.0, 1.0)
        dy_b = b(s.dy, 11, -1.0, 1.0)
        st_b = b(s.strength, 6, 0.0, 1.0)
        z_b = b(float(s.zoom), 8, 100.0, 150.0)
        # pan/tilt bins in relative range
        pan_b = b(float(s.pan), 13, -540000.0, 540000.0)
        tilt_b = b(float(s.tilt), 9, -324000.0, 324000.0)
        return f"dx{dx_b}|dy{dy_b}|st{st_b}|z{z_b}|p{pan_b}|t{tilt_b}"

    def tick(self) -> Dict[str, Any]:
        err = None
        try:
            if self._done and self.auto_reset:
                self.reset()

            s = self._read_target_state()
            legal = ["left", "right", "up", "down", "zoom_in", "zoom_out", "hold"]

            # choose action
            a = "hold"
            if self.autoplay:
                sh = self._state_hash(s)
                if self.mode == "oroma_vs_oroma_policy":
                    a = self.shim.choose(sh, legal)
                else:
                    # explore mode
                    a = self.shim.choose(sh, legal)
                    if random.random() < float(self.eps):
                        a = random.choice(legal)

            # execute action
            if a != "hold":
                # NOTE:
                #   DeviceHub.ptz_command(action, amount) nimmt in v3.7.x **keinen**
                #   device= Parameter. Die aktive PTZ-Quelle wird im Hub konfiguriert
                #   (siehe /video/ UI). Deshalb hier strikt ohne keyword args.
                hub = self._get_hub()
                hub.ptz_command(str(a), int(self.amount))

            # reward from dist improvement
            dist_now = float(s.dist)
            if self._prev_dist is None:
                d_improve = 0.0
            else:
                d_improve = float(self._prev_dist) - dist_now
            self._prev_dist = dist_now
            r = float(d_improve) * float(_clamp(s.strength, 0.0, 1.0))
            if a != "hold":
                r -= 0.01

            # done condition in UI: lock-on
            if (s.strength >= 0.20) and (dist_now <= 0.18):
                self._lock += 1
            else:
                self._lock = 0
            self._steps += 1
            if self._lock >= 6 or self._steps >= int(self.max_steps):
                self._done = True

            self.last_action = str(a)
            self.last_reward = float(r)

            # policy_rules count
            pr_count = 0
            try:
                from core.sql_manager import get_db_path  # type: ignore
                import sqlite3
                dbp = get_db_path("oroma.db")
                con = sqlite3.connect(dbp, timeout=3)
                try:
                    pr_count = int(con.execute("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", (self.namespace,)).fetchone()[0])
                finally:
                    con.close()
            except Exception:
                pr_count = 0

            return {
                "ok": True,
                "err": None,
                "namespace": self.namespace,
                "mode": self.mode,
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "autoplay": bool(self.autoplay),
                "auto_reset": bool(self.auto_reset),
                "amount": int(self.amount),
                "dt_ms": int(self.dt_ms),
                "max_steps": int(self.max_steps),
                "done": bool(self._done),
                "last_action": str(self.last_action),
                "last_reward": float(self.last_reward),
                "lock": int(self._lock),
                "steps": int(self._steps),
                "have_up": bool(self.shim.have_up),
                "policy_rules": int(pr_count),
                "obs": {
                    "ok": bool(s.ok),
                    "dx": float(s.dx),
                    "dy": float(s.dy),
                    "dist": float(s.dist),
                    "strength": float(s.strength),
                    "motion": float(s.motion),
                    "sharp": float(s.sharp),
                    "score": float(s.score),
                    "pan": int(s.pan),
                    "tilt": int(s.tilt),
                    "zoom": int(s.zoom),
                },
            }
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            return {
                "ok": False,
                "err": err,
                "namespace": self.namespace,
                "mode": self.mode,
                "eps": float(self.eps),
                "autoplay": bool(self.autoplay),
            }

    def peek(self) -> Dict[str, Any]:
        """Read-only state for UI polling (no moves, no counters)."""
        try:
            s = self._read_target_state()
            pr_count = 0
            try:
                from core.sql_manager import get_db_path  # type: ignore
                import sqlite3
                dbp = get_db_path("oroma.db")
                con = sqlite3.connect(dbp, timeout=3)
                try:
                    pr_count = int(con.execute("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", (self.namespace,)).fetchone()[0])
                finally:
                    con.close()
            except Exception:
                pr_count = 0
            return {
                "ok": True,
                "err": None,
                "namespace": self.namespace,
                "mode": self.mode,
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "autoplay": bool(self.autoplay),
                "auto_reset": bool(self.auto_reset),
                "amount": int(self.amount),
                "dt_ms": int(self.dt_ms),
                "max_steps": int(self.max_steps),
                "done": bool(self._done),
                "last_action": str(self.last_action),
                "last_reward": float(self.last_reward),
                "lock": int(self._lock),
                "steps": int(self._steps),
                "have_up": bool(self.shim.have_up),
                "policy_rules": int(pr_count),
                "obs": {
                    "ok": bool(s.ok),
                    "dx": float(s.dx),
                    "dy": float(s.dy),
                    "dist": float(s.dist),
                    "strength": float(s.strength),
                    "motion": float(s.motion),
                    "sharp": float(s.sharp),
                    "score": float(s.score),
                    "pan": int(s.pan),
                    "tilt": int(s.tilt),
                    "zoom": int(s.zoom),
                },
            }
        except Exception as e:
            return {"ok": False, "err": f"{type(e).__name__}: {e}"}


RUNTIME = PTZTargetRuntime()


@bp.get("/")
def page() -> Response:
    return Response(render_template("ptz_target.html"), mimetype="text/html")


@bp.get("/api/state")
def api_state() -> Response:
    if RUNTIME.autoplay:
        return jsonify(RUNTIME.tick())
    return jsonify(RUNTIME.peek())


@bp.post("/api/toggle")
def api_toggle() -> Response:
    RUNTIME.autoplay = not bool(RUNTIME.autoplay)
    return jsonify({"ok": True, "autoplay": bool(RUNTIME.autoplay)})


@bp.post("/api/reset")
def api_reset() -> Response:
    RUNTIME.reset()
    return jsonify({"ok": True})


@bp.post("/api/mode")
def api_mode() -> Response:
    data = request.get_json(silent=True) or {}
    m = str(data.get("mode", "") or "").strip()
    if m in ("oroma_vs_oroma_policy", "oroma_vs_oroma_explore", "human"):
        RUNTIME.mode = m
    # default explore
    if RUNTIME.mode == "human":
        RUNTIME.autoplay = False
    return jsonify({"ok": True, "mode": RUNTIME.mode})


@bp.post("/api/move")
def api_move() -> Response:
    data = request.get_json(silent=True) or {}
    a = str(data.get("action", "") or "hold").strip()
    if a not in ("left", "right", "up", "down", "zoom_in", "zoom_out", "hold"):
        a = "hold"
    try:
        if a != "hold":
            hub = RUNTIME._get_hub()
            hub.ptz_command(a, int(RUNTIME.amount))
        RUNTIME.last_action = a
    except Exception:
        pass
    return jsonify({"ok": True, "action": a})


@bp.post("/api/settings")
def api_settings() -> Response:
    data = request.get_json(silent=True) or {}
    if "eps" in data:
        try:
            RUNTIME.eps = float(data.get("eps"))
        except Exception:
            pass
    if "amount" in data:
        try:
            RUNTIME.amount = int(data.get("amount"))
        except Exception:
            pass
    if "dt_ms" in data:
        try:
            RUNTIME.dt_ms = int(data.get("dt_ms"))
        except Exception:
            pass
    if "max_steps" in data:
        try:
            RUNTIME.max_steps = int(data.get("max_steps"))
        except Exception:
            pass
    if "auto_reset" in data:
        RUNTIME.auto_reset = bool(data.get("auto_reset"))
    return jsonify({
        "ok": True,
        "eps": float(RUNTIME.eps),
        "amount": int(RUNTIME.amount),
        "dt_ms": int(RUNTIME.dt_ms),
        "max_steps": int(RUNTIME.max_steps),
        "auto_reset": bool(RUNTIME.auto_reset),
    })

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/hideseek_ui.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Hide & Seek UI (Grid-World) – Standard-Modi + UniversalPolicy
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses UI bringt das Mini-Programm mini_programs/hide_seek.py in den
# "neuen" Games-Standard von ORÓMA:
#   • /hideseek/ – Canvas UI (Grid)
#   • Standard-Modi (wie TicTacToe/Connect4/Pong/Chess/Flappy):
#       - human
#       - oroma_vs_oroma_policy
#       - oroma_vs_oroma_explore
#   • UniversalPolicy Integration (namespace: game:hideseek)
#
# GAME-MODELL
# -----------
# Grid 15×10 (siehe mini_programs/hide_seek.py):
#   - WALLS
#   - SEEKER (Agent)
#   - HIDER (Targets)
#
# ACTION SPACE (Seeker)
# ---------------------
#   0=RIGHT, 1=LEFT, 2=DOWN, 3=UP
# Legal: nicht in WALL.
#
# Hiders bewegen sich weiterhin "autonom" wie im Mini-Programm:
#   - bei LOS fliehen sie, sonst random-walk/stehen.
#
# LEARNING SIGNAL (UniversalPolicy)
# --------------------------------
# Robust und DB-schonend:
#   outcome = +1, wenn im Schritt ein Hider gefunden wurde
#           = -1, sonst
#
# Default-Verhalten (wie gewünscht)
# --------------------------------
#   - UI startet im Explore-Mode und autoplay=true.
#
# ROUTES / API (Blueprint: /hideseek)
# ----------------------------------
#   GET  /hideseek/                 → HTML UI
#   GET  /hideseek/api/state         → Zustand + UI-Meta + Policy-Stats
#   POST /hideseek/api/reset         → Reset (optional seed)
#   POST /hideseek/api/action        → {action:0..3} (human)
#   POST /hideseek/api/step          → 1 Tick (action aus Mode/Autoplay)
#   POST /hideseek/api/mode          → {mode: human|oroma_vs_oroma_policy|oroma_vs_oroma_explore}
#   POST /hideseek/api/toggle        → {autoplay:true|false}
#   GET  /hideseek/api/settings      → eps, explore_moves_per_game, max_steps
#   POST /hideseek/api/settings      → Patch Settings
#
# PRODUKTIONSREGELN
# -----------------
# • Headless-only (kein pygame)
# • Keine stillen Fehler: /api/step liefert ok=false + error
# • Thread-safe Runtime (Lock)
# =============================================================================

from __future__ import annotations

import os
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, current_app, jsonify, render_template, request


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


hideseek_bp = Blueprint(
    "hideseek",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/hideseek",
)


Vec = Tuple[int, int]


class HideSeekEnv:
    """Minimaler Wrapper um mini_programs.hide_seek für action-basiertes Stepping."""

    def __init__(self, seed: Optional[int] = None):
        from mini_programs import hide_seek  # type: ignore

        self._hs = hide_seek
        self.reset(seed=seed)

    @property
    def W(self) -> int:
        return int(self._hs.W)

    @property
    def H(self) -> int:
        return int(self._hs.H)

    def reset(self, seed: Optional[int] = None) -> Dict[str, Any]:
        s = int(seed) if seed is not None else int(time.time()) & 0xFFFFFFFF
        self.env = self._hs.gen_world(seed=s)
        self.max_steps = int(_env_int("OROMA_HIDESEEK_MAX_STEPS", 400))
        self.done = False
        return self.get_state()

    def _legal_actions(self) -> List[int]:
        g = self.env["grid"]
        sx, sy = self.env["seeker"]
        out: List[int] = []
        # 0=R,1=L,2=D,3=U
        cand = [(0, (sx + 1, sy)), (1, (sx - 1, sy)), (2, (sx, sy + 1)), (3, (sx, sy - 1))]
        for a, (x, y) in cand:
            if 0 <= x < self.W and 0 <= y < self.H:
                if g[y][x] != self._hs.WALL:
                    out.append(int(a))
        return out or [0, 1, 2, 3]

    def step(self, action: int) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        if self.done:
            return self.get_state(), 0.0, True, {"reason": "done"}

        g = self.env["grid"]
        sx, sy = self.env["seeker"]

        legal = self._legal_actions()
        a = int(action)
        if a not in legal:
            a = int(random.choice(legal))

        # Map action -> target cell
        dx, dy = (1, 0) if a == 0 else (-1, 0) if a == 1 else (0, 1) if a == 2 else (0, -1)
        nx, ny = sx + dx, sy + dy
        if not (0 <= nx < self.W and 0 <= ny < self.H):
            nx, ny = sx, sy

        found_now = 0

        # move seeker (capture possible)
        g[sy][sx] = self._hs.EMPTY
        if g[ny][nx] == self._hs.HIDER:
            found_now = 1
            self.env["found"] += 1
            self.env["hiders"] = [h for h in self.env["hiders"] if h != (nx, ny)]
        g[ny][nx] = self._hs.SEEKER
        self.env["seeker"] = (nx, ny)

        # move hiders (reuse original logic: LOS flee else random)
        hs_old = self.env["hiders"][:]
        new_hiders: List[Vec] = []
        for (hx, hy) in hs_old:
            # clear
            if g[hy][hx] == self._hs.HIDER:
                g[hy][hx] = self._hs.EMPTY

            cand = self._hs.neighbors((hx, hy))
            cand = [c for c in cand if g[c[1]][c[0]] == self._hs.EMPTY]

            if self._hs.los(self.env["seeker"], (hx, hy), g) and cand:
                cand.sort(key=lambda c: -(abs(nx - c[0]) + abs(ny - c[1])))
                tx, ty = cand[0]
            elif cand and random.random() < 0.5:
                tx, ty = random.choice(cand)
            else:
                tx, ty = hx, hy

            g[ty][tx] = self._hs.HIDER
            new_hiders.append((tx, ty))

        self.env["hiders"] = new_hiders
        self.env["steps"] += 1

        # reward + done
        reward = 1.0 if found_now else -1.0
        if int(self.env["found"]) >= int(self._hs.N_HIDERS):
            self.done = True
            return self.get_state(), reward, True, {"reason": "all_found"}
        if int(self.env["steps"]) >= int(self.max_steps):
            self.done = True
            return self.get_state(), reward, True, {"reason": "max_steps"}

        return self.get_state(), reward, False, {"found_now": found_now}

    def get_state(self) -> Dict[str, Any]:
        g = self.env["grid"]
        return {
            "w": self.W,
            "h": self.H,
            "grid": g,
            "seeker": {"x": int(self.env["seeker"][0]), "y": int(self.env["seeker"][1])},
            "hiders": [{"x": int(x), "y": int(y)} for (x, y) in self.env["hiders"]],
            "found": int(self.env.get("found", 0)),
            "steps": int(self.env.get("steps", 0)),
            "done": bool(self.done),
            "max_steps": int(self.max_steps),
        }

    def legal_actions(self) -> List[int]:
        return self._legal_actions()


class HideSeekRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.env = HideSeekEnv(seed=None)

        # Default: explore + autoplay
        self.mode = "oroma_vs_oroma_explore"
        self.autoplay = True

        self.eps = _env_float("OROMA_HIDESEEK_EPS", 0.08)
        self.explore_moves_per_game = int(_env_float("OROMA_HIDESEEK_EXPLORE_MOVES", 1.0))
        self._explore_budget = 0

        self._pending_action = 0

        # UniversalPolicy
        self.policy = None
        try:
            from core.universal_policy import Policy  # type: ignore

            self.policy = Policy(namespace="game:hideseek")
        except Exception:
            self.policy = None

    @staticmethod
    def _grid_hash(grid: List[List[int]], seeker: Vec, hiders: List[Vec]) -> str:
        flat: List[int] = []
        for row in grid:
            for v in row:
                flat.append(int(v))
        sx, sy = seeker
        hs = sorted([h[0] + 100 * h[1] for h in hiders])
        out = []
        if flat:
            last = flat[0]
            cnt = 1
            for x in flat[1:]:
                if x == last and cnt < 99:
                    cnt += 1
                else:
                    out.append(f"{last}{cnt:02d}")
                    last = x
                    cnt = 1
            out.append(f"{last}{cnt:02d}")
        return "hs:v1:" + "".join(out) + f":s={sx},{sy}:h=" + ",".join(map(str, hs[:12]))

    def _state_hash(self, st: Dict[str, Any]) -> str:
        grid = st.get("grid") or []
        sx = int((st.get("seeker") or {}).get("x", 0))
        sy = int((st.get("seeker") or {}).get("y", 0))
        hiders = [(int(h.get("x", 0)), int(h.get("y", 0))) for h in (st.get("hiders") or [])]
        return self._grid_hash(grid, (sx, sy), hiders)

    def _pick_action(self, st: Dict[str, Any]) -> int:
        legal = self.env.legal_actions() or [0, 1, 2, 3]
        if self.mode == "oroma_vs_oroma_explore":
            if self._explore_budget < int(self.explore_moves_per_game):
                self._explore_budget += 1
                return int(random.choice(legal))
            if random.random() < float(self.eps):
                return int(random.choice(legal))

        if not self.policy:
            return int(random.choice(legal))

        sh = self._state_hash(st)
        try:
            return int(self.policy.choose(sh, legal, side="X"))
        except Exception:
            return int(random.choice(legal))

    def reset(self, seed: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            st = self.env.reset(seed=seed)
            self._explore_budget = 0
            self._pending_action = 0
            return st

    def action(self, a: int) -> None:
        with self.lock:
            self._pending_action = int(a)

    def step(self) -> Dict[str, Any]:
        with self.lock:
            st = self.env.get_state()
            if st.get("done"):
                return st

            a = int(self._pending_action)
            if self.autoplay or self.mode != "human":
                a = self._pick_action(st)

            st2, reward, done, _info = self.env.step(a)

            if self.policy and self.mode == "oroma_vs_oroma_explore":
                try:
                    now = int(time.time())
                    self.policy.learn_many([
                        {
                            "state_hash": self._state_hash(st),
                            "action_canon": int(a),
                            "side": "X",
                            "outcome": float(reward),
                            "ts": now,
                        }
                    ])
                except Exception:
                    pass

            if done:
                self._explore_budget = 0

            return st2

    def state(self) -> Dict[str, Any]:
        with self.lock:
            st = self.env.get_state()
            rules = 0
            try:
                if self.policy:
                    rules = int(self.policy.rules_count())
            except Exception:
                rules = 0
            return {
                "ok": True,
                "state": st,
                "mode": self.mode,
                "autoplay": bool(self.autoplay),
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "namespace": "game:hideseek",
                "policy_rules": rules,
            }

    def settings(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "max_steps": int(self.env.max_steps),
            }

    def patch_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            try:
                if "eps" in patch:
                    self.eps = float(patch.get("eps"))
                if "explore_moves_per_game" in patch:
                    self.explore_moves_per_game = int(float(patch.get("explore_moves_per_game")))
                if "max_steps" in patch:
                    self.env.max_steps = int(float(patch.get("max_steps")))
            except Exception:
                pass
            return self.settings()


def _rt() -> HideSeekRuntime:
    k = "_hideseek_runtime"
    if k not in current_app.config:
        current_app.config[k] = HideSeekRuntime()
    return current_app.config[k]


@hideseek_bp.get("/")
def page():
    return render_template("hideseek.html")


@hideseek_bp.get("/api/state")
def api_state():
    return jsonify(_rt().state())


@hideseek_bp.post("/api/reset")
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


@hideseek_bp.post("/api/action")
def api_action():
    try:
        d = request.get_json(force=True) or {}
        a = int(d.get("action", 0))
    except Exception:
        a = 0
    _rt().action(a)
    return jsonify({"ok": True})


@hideseek_bp.post("/api/step")
def api_step():
    try:
        st = _rt().step()
        return jsonify({"ok": True, "state": st})
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)})


@hideseek_bp.post("/api/mode")
def api_mode():
    try:
        d = request.get_json(force=True) or {}
        mode = str(d.get("mode") or "human")
    except Exception:
        mode = "human"

    if mode == "oroma_solo":
        mode = "oroma_vs_oroma_explore"
    if mode == "oroma_vs_oroma":
        mode = "oroma_vs_oroma_explore"

    if mode not in ("human", "oroma_vs_oroma_policy", "oroma_vs_oroma_explore"):
        mode = "human"

    rt = _rt()
    with rt.lock:
        rt.mode = mode
        if mode in ("oroma_vs_oroma_policy", "oroma_vs_oroma_explore"):
            rt.autoplay = True
    return jsonify({"ok": True, "mode": rt.mode, "autoplay": bool(rt.autoplay)})


@hideseek_bp.post("/api/toggle")
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


@hideseek_bp.get("/api/settings")
def api_settings_get():
    return jsonify({"ok": True, "settings": _rt().settings()})


@hideseek_bp.post("/api/settings")
def api_settings_post():
    try:
        d = request.get_json(force=True) or {}
    except Exception:
        d = {}
    st = _rt().patch_settings(d)
    return jsonify({"ok": True, "settings": st})

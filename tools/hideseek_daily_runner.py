#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/hideseek_daily_runner.py
# Projekt: ORÓMA – Games / Professional State Templates
# Modul:   Hide&Seek Daily Runner – pro_v2 Tactical Policy/Explore Runner
# Version: v4.1-pro_v2-status
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt Hide & Seek automatisiert headless aus und schreibt weiterhin die
#   üblichen ORÓMA-Episoden nach episodes/episodic_metrics. Zusätzlich wird der
#   alte rohgridnahe und per-step-negative Lernpfad auf einen isolierten,
#   professionellen Lernpfad gehoben:
#
#       namespace:     game:hideseek
#       state_schema:  hideseek:pro_v2
#       action_schema: dir4_abs
#
# Professionelles Hide&Seek-Design
# --------------------------------
#   Hide&Seek ist kein vollständig lösbares Kleinstspiel wie TicTacToe. Hider
#   bewegen sich stochastisch, Wände und Startpositionen wechseln je Episode.
#   Deshalb wird kein Solver-Gap-Fill gebaut. Stattdessen nutzt der Runner eine
#   taktische, kleine State-Abstraktion aus Sicht des Suchers:
#
#     • Spielphase und verbleibende Hider
#     • Legal-/Wall-Mask und lokale Mobilität
#     • sichtbare Hider per Line-of-Sight
#     • BFS-Richtung und Distanz zum nächsten Hider
#     • Quadranten-/Dichte-Signale für Hider und Wände
#
#   Der Fallback spielt nicht zufällig, sondern sucht per BFS den nächsten oder
#   sichtbaren Hider und bewegt sich entlang des kürzesten sicheren Pfades.
#
# Lernsignale
# -----------
#   Der alte Daily Runner lernte jeden Nicht-Fund-Schritt als -1. Das erzeugt in
#   einem Suchspiel eine riesige Negativwand, weil lange Suche normal ist. pro_v2
#   lernt deshalb ausschließlich aus klaren Ereignissen:
#
#     • Hider gefangen                         → positives Kreditfenster
#     • sinnvoller Pfadschritt zum Ziel        → positives Signal
#     • klar verpasster besserer Pfadschritt   → negatives Signal
#     • Timeout ohne alle Hider                → negatives Endfenster
#     • alle Hider gefunden                    → positives Endfenster
#
#   Neutrale Suchschritte werden nicht als Draw/Negativmüll geschrieben.
#
# DB-/Write-Disziplin
# -------------------
#   Policy-Regeln werden ausschließlich über core.db_writer_client.executemany()
#   geschrieben. Es gibt keinen lokalen SQLite-Direktwrite-Fallback für
#   policy_rules. Wenn DBWriter nicht erreichbar ist, bleibt das sichtbar:
#
#       policy_learn_ok=false, learned_items=0
#
#   Episoden/Metriken nutzen weiterhin den vorhandenen sql_manager-Episodenpfad,
#   wie die übrigen Daily Runner im Projekt.
#
# Explore-Disziplin
# -----------------
#   Hide&Seek wird nicht vollständig gelöst. Deshalb wird Exploration nie hart
#   beendet. Wenn eine konfigurierbare Mindestabdeckung erreicht ist, reduziert
#   der Runner Exploration auf Sparmodus, setzt aber no_more_explore=0.
#
# ENV
# ---
#   OROMA_HIDESEEK_POLICY_NAMESPACE=game:hideseek
#   OROMA_HIDESEEK_MAX_STEPS=400
#   OROMA_HIDESEEK_EPS=0.08
#   OROMA_HIDESEEK_EXPLORE_MOVES=1
#   OROMA_HS_POLICY_ACCEPT_Q_MIN=0.15
#   OROMA_HS_POLICY_ACCEPT_MIN_N=2
#   OROMA_HS_POLICY_DBW_CHUNK=500
#   OROMA_HS_CAPTURE_CREDIT_STEPS=12
#   OROMA_HS_TERMINAL_SUCCESS_CREDIT_STEPS=16
#   OROMA_HS_TIMEOUT_CREDIT_STEPS=16
#   OROMA_HS_PATH_LEARNING=1
#   OROMA_HS_EXPLORE_REDUCE_RULES=20000
#   OROMA_HS_EXPLORE_REDUCED_EPS=0.02
#   OROMA_HS_EXPLORE_REDUCED_MOVES=0
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/hideseek_daily_runner.py --policy-games 100 --explore-games 100
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

STATE_SCHEMA = "hideseek:pro_v2"
ACTION_SCHEMA = "dir4_abs"
DEFAULT_NAMESPACE = "game:hideseek"
WALL = 1
ACTION_DELTAS: Dict[int, Tuple[int, int]] = {
    0: (1, 0),   # right
    1: (-1, 0),  # left
    2: (0, 1),   # down
    3: (0, -1),  # up
}
ACTION_NAMES = {0: "R", 1: "L", 2: "D", 3: "U"}


# -----------------------------------------------------------------------------
# Env / small helpers
# -----------------------------------------------------------------------------

def _now_ts() -> int:
    return int(time.time())


def _env_bool(name: str, default: str = "0") -> bool:
    v = os.environ.get(name, default)
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _bucket_int(value: int, limits: Sequence[int]) -> int:
    v = int(value)
    for idx, lim in enumerate(limits):
        if v <= int(lim):
            return idx
    return len(limits)


def _clamp01_count(value: int, hi: int) -> int:
    return max(0, min(int(hi), int(value)))


# -----------------------------------------------------------------------------
# Hide&Seek state helpers
# -----------------------------------------------------------------------------

def _grid(st: Dict[str, Any]) -> List[List[int]]:
    """Return the state grid without copying. Helpers only read from it."""
    return st.get("grid") or []


def _size(st: Dict[str, Any]) -> Tuple[int, int]:
    g = _grid(st)
    h = len(g)
    w = len(g[0]) if h else int(st.get("w") or 0)
    return int(w or st.get("w") or 0), int(h or st.get("h") or 0)


def _seeker(st: Dict[str, Any]) -> Tuple[int, int]:
    s = st.get("seeker") or {}
    return int(s.get("x", 0)), int(s.get("y", 0))


def _hiders(st: Dict[str, Any]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for h in (st.get("hiders") or []):
        try:
            out.append((int(h.get("x", 0)), int(h.get("y", 0))))
        except Exception:
            continue
    return out


def _in_bounds(st: Dict[str, Any], x: int, y: int) -> bool:
    w, h = _size(st)
    return 0 <= int(x) < int(w) and 0 <= int(y) < int(h)


def _cell(st: Dict[str, Any], x: int, y: int) -> int:
    g = _grid(st)
    if not g or not (0 <= int(y) < len(g)) or not (0 <= int(x) < len(g[int(y)])):
        return WALL
    return int(g[int(y)][int(x)])


def _passable(st: Dict[str, Any], x: int, y: int) -> bool:
    return _in_bounds(st, x, y) and _cell(st, x, y) != WALL


def _legal_actions_from_state(st: Dict[str, Any]) -> List[int]:
    sx, sy = _seeker(st)
    legal: List[int] = []
    for a, (dx, dy) in ACTION_DELTAS.items():
        nx, ny = sx + dx, sy + dy
        if _passable(st, nx, ny):
            legal.append(int(a))
    return legal or [0, 1, 2, 3]


def _legal_mask(legal: Iterable[int]) -> str:
    s = {int(a) for a in legal}
    return "".join("1" if a in s else "0" for a in (0, 1, 2, 3))


def _wall_mask(st: Dict[str, Any]) -> str:
    sx, sy = _seeker(st)
    bits: List[str] = []
    for a in (0, 1, 2, 3):
        dx, dy = ACTION_DELTAS[a]
        nx, ny = sx + dx, sy + dy
        bits.append("1" if (not _passable(st, nx, ny)) else "0")
    return "".join(bits)


def _dir_bucket(dx: int, dy: int) -> str:
    if int(dx) == 0 and int(dy) == 0:
        return "same"
    ax, ay = abs(int(dx)), abs(int(dy))
    if ax >= ay * 2:
        return "E" if dx > 0 else "W"
    if ay >= ax * 2:
        return "S" if dy > 0 else "N"
    if dx > 0 and dy > 0:
        return "SE"
    if dx > 0 and dy < 0:
        return "NE"
    if dx < 0 and dy > 0:
        return "SW"
    if dx < 0 and dy < 0:
        return "NW"
    return "mix"


def _dist_bucket(dist: Optional[int]) -> str:
    if dist is None or int(dist) < 0:
        return "none"
    d = int(dist)
    if d == 0:
        return "0"
    if d == 1:
        return "1"
    if d <= 3:
        return "close"
    if d <= 7:
        return "mid"
    if d <= 14:
        return "far"
    return "vfar"


def _phase_bucket(st: Dict[str, Any]) -> str:
    found = int(st.get("found", 0) or 0)
    remaining = len(_hiders(st))
    steps = int(st.get("steps", 0) or 0)
    max_steps = max(1, int(st.get("max_steps", 400) or 400))
    if remaining <= 0:
        return "done"
    if found <= 0 and steps < max_steps * 0.25:
        return "early"
    if remaining <= 1:
        return "endgame"
    if steps < max_steps * 0.60:
        return "mid"
    return "late"


def _los_visible(st: Dict[str, Any], a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    """Line-of-sight with wall blocking.

    The original mini-program uses a very small sign-step LOS helper. For the
    pro runner we keep the same grid semantics but avoid non-terminating diagonal
    attempts by accepting only row/column/perfect-diagonal lines.
    """
    x0, y0 = int(a[0]), int(a[1])
    x1, y1 = int(b[0]), int(b[1])
    dx = x1 - x0
    dy = y1 - y0
    if dx == 0 and dy == 0:
        return True
    if not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
        return False
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    x, y = x0, y0
    while (x, y) != (x1, y1):
        x += sx
        y += sy
        if not _in_bounds(st, x, y):
            return False
        if (x, y) != (x1, y1) and _cell(st, x, y) == WALL:
            return False
    return True


def _visible_hiders(st: Dict[str, Any]) -> List[Tuple[int, int]]:
    s = _seeker(st)
    return [h for h in _hiders(st) if _los_visible(st, s, h)]


def _bfs_first_action_to_targets(st: Dict[str, Any], targets: Sequence[Tuple[int, int]]) -> Tuple[Optional[int], Optional[int], Optional[Tuple[int, int]]]:
    target_set = {(int(x), int(y)) for x, y in targets}
    if not target_set:
        return None, None, None
    start = _seeker(st)
    if start in target_set:
        return None, 0, start
    q: deque[Tuple[int, int, int, int]] = deque()
    seen: Set[Tuple[int, int]] = {start}
    for a in (0, 1, 2, 3):
        dx, dy = ACTION_DELTAS[a]
        nx, ny = start[0] + dx, start[1] + dy
        if _passable(st, nx, ny):
            q.append((nx, ny, int(a), 1))
            seen.add((nx, ny))
    while q:
        x, y, first_a, dist = q.popleft()
        if (x, y) in target_set:
            return int(first_a), int(dist), (int(x), int(y))
        for a in (0, 1, 2, 3):
            dx, dy = ACTION_DELTAS[a]
            nx, ny = x + dx, y + dy
            p = (nx, ny)
            if p in seen:
                continue
            if not _passable(st, nx, ny):
                continue
            seen.add(p)
            q.append((nx, ny, int(first_a), int(dist) + 1))
    return None, None, None


def _target_info(st: Dict[str, Any]) -> Dict[str, Any]:
    hiders = _hiders(st)
    visible = _visible_hiders(st)
    vis_a, vis_d, vis_t = _bfs_first_action_to_targets(st, visible)
    near_a, near_d, near_t = _bfs_first_action_to_targets(st, hiders)
    sx, sy = _seeker(st)
    target = vis_t if vis_t is not None else near_t
    dist = vis_d if vis_t is not None else near_d
    action = vis_a if vis_a is not None else near_a
    dx = int(target[0] - sx) if target is not None else 0
    dy = int(target[1] - sy) if target is not None else 0
    return {
        "visible_count": len(visible),
        "visible_action": vis_a,
        "visible_dist": vis_d,
        "nearest_action": near_a,
        "nearest_dist": near_d,
        "target_action": action,
        "target_dist": dist,
        "target_dir": _dir_bucket(dx, dy) if target is not None else "none",
        "has_target": target is not None,
    }


def _wall_density_bucket(st: Dict[str, Any], radius: int = 2) -> int:
    sx, sy = _seeker(st)
    walls = 0
    cells = 0
    for y in range(sy - int(radius), sy + int(radius) + 1):
        for x in range(sx - int(radius), sx + int(radius) + 1):
            if not _in_bounds(st, x, y):
                continue
            cells += 1
            if _cell(st, x, y) == WALL:
                walls += 1
    if cells <= 0:
        return 0
    pct = walls / float(cells)
    if pct <= 0.05:
        return 0
    if pct <= 0.15:
        return 1
    if pct <= 0.30:
        return 2
    return 3


def _hider_quadrants(st: Dict[str, Any]) -> str:
    sx, sy = _seeker(st)
    q = [0, 0, 0, 0]  # NE,NW,SE,SW-ish with y down
    for hx, hy in _hiders(st):
        if hx >= sx and hy < sy:
            q[0] += 1
        elif hx < sx and hy < sy:
            q[1] += 1
        elif hx >= sx and hy >= sy:
            q[2] += 1
        else:
            q[3] += 1
    return "".join(str(_clamp01_count(v, 2)) for v in q)


def state_hash(st: Dict[str, Any], legal: Optional[Sequence[int]] = None) -> str:
    legal_list = [int(a) for a in (legal if legal is not None else _legal_actions_from_state(st))]
    info = _target_info(st)
    remaining = len(_hiders(st))
    found = int(st.get("found", 0) or 0)
    steps = int(st.get("steps", 0) or 0)
    max_steps = max(1, int(st.get("max_steps", 400) or 400))
    step_ratio_bucket = _bucket_int(int(100 * steps / max_steps), [10, 25, 50, 75])
    visible_bucket = _clamp01_count(int(info.get("visible_count") or 0), 3)
    parts = [
        STATE_SCHEMA,
        f"ph={_phase_bucket(st)}",
        f"rem={_clamp01_count(remaining, 4)}",
        f"found={_clamp01_count(found, 4)}",
        f"sr={step_ratio_bucket}",
        f"leg={_legal_mask(legal_list)}",
        f"wall={_wall_mask(st)}",
        f"mob={_clamp01_count(len(legal_list), 4)}",
        f"vis={visible_bucket}",
        f"vdir={info.get('target_dir', 'none') if visible_bucket else 'none'}",
        f"vd={_dist_bucket(info.get('visible_dist'))}",
        f"nd={_dist_bucket(info.get('nearest_dist'))}",
        f"ta={info.get('target_action') if info.get('target_action') is not None else 'none'}",
        f"quad={_hider_quadrants(st)}",
        f"wd={_wall_density_bucket(st)}",
    ]
    return ":".join(str(p) for p in parts)


def _static_distance_after_action(st: Dict[str, Any], action: int) -> Optional[int]:
    """Distance from the post-action seeker cell to the current hider positions.

    Hider movement is intentionally ignored here. The metric answers whether the
    seeker action itself moved along a sensible path before stochastic hiders react.
    """
    sx, sy = _seeker(st)
    dx, dy = ACTION_DELTAS.get(int(action), (0, 0))
    nx, ny = sx + dx, sy + dy
    if not _passable(st, nx, ny):
        return None
    st2 = dict(st)
    st2["seeker"] = {"x": int(nx), "y": int(ny)}
    _a, dist, _target = _bfs_first_action_to_targets(st2, _hiders(st))
    return dist




class LocalHideSeekEnv:
    """Headless Hide&Seek environment without Flask/package-registry dependency.

    The interactive UI imports Flask and ``mini_programs`` package import performs
    registry work. Daily runners must remain headless and predictable, therefore
    this local wrapper implements the small grid rules directly from
    mini_programs/hide_seek.py semantics. Hider movement uses the runner RNG, so
    --seed is reproducible.
    """

    W = 15
    H = 10
    N_HIDERS = 4
    WALL_RATE = 0.10
    EMPTY = 0
    WALL = 1
    SEEKER = 2
    HIDER = 3

    def __init__(self, seed: int, max_steps: int, rng: random.Random):
        self.rng = rng
        self.max_steps = int(max_steps)
        self.done = False
        self.env = self._gen_world(seed=int(seed))

    @staticmethod
    def _in_bounds(x: int, y: int) -> bool:
        return 0 <= int(x) < LocalHideSeekEnv.W and 0 <= int(y) < LocalHideSeekEnv.H

    @staticmethod
    def _neighbors(p: Tuple[int, int]) -> List[Tuple[int, int]]:
        x, y = int(p[0]), int(p[1])
        out = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        return [(a, b) for a, b in out if LocalHideSeekEnv._in_bounds(a, b)]

    @staticmethod
    def _los(a: Tuple[int, int], b: Tuple[int, int], grid: Sequence[Sequence[int]]) -> bool:
        x0, y0 = int(a[0]), int(a[1])
        x1, y1 = int(b[0]), int(b[1])
        dx = 1 if x1 > x0 else -1 if x1 < x0 else 0
        dy = 1 if y1 > y0 else -1 if y1 < y0 else 0
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x += dx
            y += dy
            if not LocalHideSeekEnv._in_bounds(x, y):
                return False
            if int(grid[int(y)][int(x)]) == LocalHideSeekEnv.WALL:
                return False
        return True

    def _gen_world(self, seed: int) -> Dict[str, Any]:
        local = random.Random(int(seed))
        grid = [[self.EMPTY for _ in range(self.W)] for _ in range(self.H)]
        for y in range(self.H):
            for x in range(self.W):
                if local.random() < self.WALL_RATE:
                    grid[y][x] = self.WALL
        while True:
            sx, sy = local.randrange(self.W), local.randrange(self.H)
            if grid[sy][sx] == self.EMPTY:
                grid[sy][sx] = self.SEEKER
                seeker = (sx, sy)
                break
        hiders: List[Tuple[int, int]] = []
        while len(hiders) < self.N_HIDERS:
            x, y = local.randrange(self.W), local.randrange(self.H)
            if grid[y][x] == self.EMPTY:
                grid[y][x] = self.HIDER
                hiders.append((x, y))
        return {"grid": grid, "seeker": seeker, "hiders": hiders, "steps": 0, "found": 0}

    def legal_actions(self) -> List[int]:
        g = self.env["grid"]
        sx, sy = self.env["seeker"]
        out: List[int] = []
        for a, (dx, dy) in ACTION_DELTAS.items():
            nx, ny = int(sx) + int(dx), int(sy) + int(dy)
            if 0 <= nx < self.W and 0 <= ny < self.H and int(g[ny][nx]) != self.WALL:
                out.append(int(a))
        return out or [0, 1, 2, 3]

    def step(self, action: int) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        if self.done:
            return self.get_state(), 0.0, True, {"reason": "done"}

        g = self.env["grid"]
        sx, sy = self.env["seeker"]
        legal = self.legal_actions()
        a = int(action)
        if a not in legal:
            a = int(self.rng.choice(legal))
        dx, dy = ACTION_DELTAS.get(a, (0, 0))
        nx, ny = int(sx) + int(dx), int(sy) + int(dy)
        if not (0 <= nx < self.W and 0 <= ny < self.H):
            nx, ny = int(sx), int(sy)

        found_now = 0
        g[int(sy)][int(sx)] = self.EMPTY
        if int(g[int(ny)][int(nx)]) == self.HIDER:
            found_now = 1
            self.env["found"] += 1
            self.env["hiders"] = [h for h in self.env["hiders"] if h != (int(nx), int(ny))]
        g[int(ny)][int(nx)] = self.SEEKER
        self.env["seeker"] = (int(nx), int(ny))

        hs_old = list(self.env["hiders"])
        new_hiders: List[Tuple[int, int]] = []
        for hx, hy in hs_old:
            if int(g[int(hy)][int(hx)]) == self.HIDER:
                g[int(hy)][int(hx)] = self.EMPTY
            cand = self._neighbors((int(hx), int(hy)))
            cand = [c for c in cand if int(g[int(c[1])][int(c[0])]) == self.EMPTY]
            if self._los(self.env["seeker"], (int(hx), int(hy)), g) and cand:
                cand.sort(key=lambda c: -(abs(int(nx) - int(c[0])) + abs(int(ny) - int(c[1]))))
                tx, ty = cand[0]
            elif cand and self.rng.random() < 0.5:
                tx, ty = self.rng.choice(cand)
            else:
                tx, ty = int(hx), int(hy)
            g[int(ty)][int(tx)] = self.HIDER
            new_hiders.append((int(tx), int(ty)))

        self.env["hiders"] = new_hiders
        self.env["steps"] += 1
        reward = 1.0 if found_now else 0.0
        if int(self.env.get("found", 0)) >= self.N_HIDERS:
            self.done = True
            return self.get_state(), reward, True, {"reason": "all_found", "found_now": int(found_now)}
        if int(self.env.get("steps", 0)) >= int(self.max_steps):
            self.done = True
            return self.get_state(), reward, True, {"reason": "max_steps", "found_now": int(found_now)}
        return self.get_state(), reward, False, {"found_now": int(found_now)}

    def get_state(self) -> Dict[str, Any]:
        return {
            "w": self.W,
            "h": self.H,
            "grid": self.env["grid"],
            "seeker": {"x": int(self.env["seeker"][0]), "y": int(self.env["seeker"][1])},
            "hiders": [{"x": int(x), "y": int(y)} for (x, y) in self.env["hiders"]],
            "found": int(self.env.get("found", 0)),
            "steps": int(self.env.get("steps", 0)),
            "done": bool(self.done),
            "max_steps": int(self.max_steps),
        }


# -----------------------------------------------------------------------------
# Policy read / fallback / safety
# -----------------------------------------------------------------------------

@dataclass
class PolicyStats:
    seen: int = 0
    accepted: int = 0
    fallback: int = 0
    rejected_n: int = 0
    rejected_q: int = 0
    rejected_unsafe: int = 0


@dataclass
class TacticalStats:
    visible_hider_steps: int = 0
    target_known_steps: int = 0
    path_moves_played: int = 0
    captures: int = 0
    timeout_games: int = 0
    all_found_games: int = 0


def _fallback_action(st: Dict[str, Any], legal: Sequence[int], rng: random.Random) -> int:
    legal_set = {int(a) for a in legal}
    info = _target_info(st)
    target_a = info.get("target_action")
    if target_a is not None and int(target_a) in legal_set:
        return int(target_a)

    # No known path: prefer moves that maximize future mobility and avoid walls.
    best_score = -10**9
    best: List[int] = []
    for a in legal_set:
        sx, sy = _seeker(st)
        dx, dy = ACTION_DELTAS.get(int(a), (0, 0))
        nx, ny = sx + dx, sy + dy
        if not _passable(st, nx, ny):
            continue
        # Estimate local mobility after the move.
        mob = 0
        for _aa, (ddx, ddy) in ACTION_DELTAS.items():
            if _passable(st, nx + ddx, ny + ddy):
                mob += 1
        center_bonus = -abs(nx - (_size(st)[0] // 2)) - abs(ny - (_size(st)[1] // 2))
        score = int(mob * 10 + center_bonus)
        if score > best_score:
            best_score = score
            best = [int(a)]
        elif score == best_score:
            best.append(int(a))
    if not best:
        return int(rng.choice(list(legal_set or {0, 1, 2, 3})))
    return int(rng.choice(sorted(best)))


def _policy_action_unsafe(st: Dict[str, Any], action: int, fallback: int) -> bool:
    if int(action) == int(fallback):
        return False
    visible_count = len(_visible_hiders(st))
    d_pol = _static_distance_after_action(st, int(action))
    d_fb = _static_distance_after_action(st, int(fallback))
    if d_pol is None or d_fb is None:
        return True
    # If a hider is visible, do not accept a policy move that clearly walks away
    # while the fallback has a path-reducing move.
    if visible_count > 0 and int(d_pol) > int(d_fb):
        return True
    # Without visibility we allow more exploration, but still reject strong path
    # regression when a better path move is known.
    if int(d_pol) >= int(d_fb) + 3:
        return True
    return False


def _db_choose_policy(namespace: str, st: Dict[str, Any], legal: Sequence[int], rng: random.Random, stats: PolicyStats) -> int:
    legal_set = {int(a) for a in legal}
    fallback = _fallback_action(st, legal, rng)
    sh = state_hash(st, legal)
    q_min = _env_float("OROMA_HS_POLICY_ACCEPT_Q_MIN", 0.15)
    n_min = _env_int("OROMA_HS_POLICY_ACCEPT_MIN_N", 2)
    rows: List[Any] = []
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                rows = list(conn.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                    (str(namespace), str(sh)),
                ).fetchall() or [])
    except Exception as e:
        sys.stderr.write(f"[hideseek_daily_runner] policy read failed: {e!r}\n")
        rows = []

    candidates: List[Tuple[int, float, int]] = []
    for row in rows:
        try:
            a_raw = row["action"] if hasattr(row, "keys") else row[0]
            q_raw = row["q"] if hasattr(row, "keys") else row[1]
            n_raw = row["n"] if hasattr(row, "keys") else row[2]
            a = int(a_raw)
            if a not in legal_set:
                continue
            candidates.append((int(a), float(q_raw), int(n_raw)))
        except Exception:
            continue

    if not candidates:
        stats.fallback += 1
        return int(fallback)

    stats.seen += 1
    candidates.sort(key=lambda t: (float(t[1]), int(t[2])), reverse=True)
    eligible: List[Tuple[int, float, int]] = []
    for a, q, n in candidates:
        if int(n) < int(n_min):
            continue
        if float(q) < float(q_min):
            continue
        eligible.append((int(a), float(q), int(n)))

    if not eligible:
        _a0, q0, n0 = candidates[0]
        if int(n0) < int(n_min):
            stats.rejected_n += 1
        elif float(q0) < float(q_min):
            stats.rejected_q += 1
        stats.fallback += 1
        return int(fallback)

    # Tie among near-equal eligible actions to avoid mechanical identical routes.
    top_q = eligible[0][1]
    tie_eps = _env_float("OROMA_HS_POLICY_Q_TIE_EPS", 0.000001)
    top = [t for t in eligible if abs(float(t[1]) - float(top_q)) <= float(tie_eps)]
    ordered = list(top if top else eligible[:1])
    rng.shuffle(ordered)
    for a, _q, _n in ordered + [t for t in eligible if t not in ordered]:
        if _policy_action_unsafe(st, int(a), int(fallback)):
            stats.rejected_unsafe += 1
            continue
        stats.accepted += 1
        return int(a)

    stats.fallback += 1
    return int(fallback)


# -----------------------------------------------------------------------------
# Event learning and DBWriter batch path
# -----------------------------------------------------------------------------

@dataclass
class TraceStep:
    state_hash: str
    action_canon: int
    target_action: Optional[int]
    target_dist_before: Optional[int]
    target_dist_after: Optional[int]
    fallback_dist_after: Optional[int]
    found_delta: int
    ts: int


def _dbw_try_enable() -> bool:
    if db_writer_client is None:
        return False
    raw = os.environ.get("OROMA_DBW_ENABLE")
    if raw is not None and str(raw).strip().lower() in ("0", "false", "no", "off"):
        return False
    if raw is None:
        try:
            sock_path = db_writer_client._sock_path() if hasattr(db_writer_client, "_sock_path") else "/opt/ai/oroma/data/state/db_writer.sock"
            if os.path.exists(str(sock_path)):
                os.environ["OROMA_DBW_ENABLE"] = "1"
        except Exception:
            pass
    try:
        return bool(db_writer_client.ping(timeout_ms=800))
    except Exception:
        return False


def _learn_policy_rules_dbw(namespace: str, items: Sequence[Dict[str, Any]]) -> Tuple[bool, int, float]:
    """Aggregate policy items and write via DBWriter only.

    Returns (ok, learned_item_count, duration_ms). No SQLite direct-write fallback
    is allowed here because policy_rules belongs to the managed ORÓMA DBWriter
    path.
    """
    t0 = time.time()
    if not items:
        return False, 0, 0.0
    if not _dbw_try_enable():
        return False, 0, round((time.time() - t0) * 1000.0, 3)

    now = int(time.time())
    agg: Dict[Tuple[str, str], Dict[str, int]] = {}
    learned_count = 0
    for it in items:
        sh = str(it.get("state_hash", "")).strip()
        if not sh:
            continue
        action = str(it.get("action_canon", it.get("action", ""))).strip()
        if action == "":
            continue
        try:
            out = float(it.get("outcome", 0.0))
        except Exception:
            out = 0.0
        if abs(out) <= 1e-9:
            continue
        key = (sh, action)
        row = agg.setdefault(key, {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": now})
        row["n"] += 1
        learned_count += 1
        if out > 0.0:
            row["pos"] += 1
        else:
            row["neg"] += 1
        try:
            ts = int(it.get("ts") or now)
            if ts > row["last_ts"]:
                row["last_ts"] = ts
        except Exception:
            pass

    if not agg:
        return False, 0, round((time.time() - t0) * 1000.0, 3)

    sql = """INSERT INTO policy_rules
             (namespace, state_hash, action, n, pos, neg, draw, q, last_ts)
             VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(namespace, state_hash, action) DO UPDATE SET
                 n = policy_rules.n + excluded.n,
                 pos = policy_rules.pos + excluded.pos,
                 neg = policy_rules.neg + excluded.neg,
                 draw = policy_rules.draw + excluded.draw,
                 q = CASE
                       WHEN (policy_rules.n + excluded.n) > 0
                       THEN CAST((policy_rules.pos + excluded.pos) - (policy_rules.neg + excluded.neg) AS REAL)
                            / CAST(policy_rules.n + excluded.n AS REAL)
                       ELSE 0.0
                     END,
                 last_ts = CASE
                             WHEN excluded.last_ts > policy_rules.last_ts THEN excluded.last_ts
                             ELSE policy_rules.last_ts
                           END
          """
    params: List[List[Any]] = []
    for (sh, action), row in agg.items():
        n = int(row["n"])
        pos = int(row["pos"])
        neg = int(row["neg"])
        draw = int(row["draw"])
        q = float(pos - neg) / float(max(1, n))
        params.append([str(namespace), sh, action, n, pos, neg, draw, q, int(row["last_ts"])])

    timeout_ms = int(getattr(sql_manager, "_dbw_timeout_ms", lambda kind="dream": 60000)("dream")) if sql_manager else 60000
    chunk = max(1, _env_int("OROMA_HS_POLICY_DBW_CHUNK", 500))
    try:
        for i in range(0, len(params), chunk):
            db_writer_client.executemany(
                sql,
                params[i:i + chunk],
                tag="hideseek.pro_v2.policy_rules.upsert",
                priority="low",
                timeout_ms=timeout_ms,
                db="oroma",
            )
        return True, int(learned_count), round((time.time() - t0) * 1000.0, 3)
    except Exception as e:
        sys.stderr.write(f"[hideseek_daily_runner] DBWriter policy upsert failed: {e!r}\n")
        return False, 0, round((time.time() - t0) * 1000.0, 3)


def _learn_items_from_trace(trace: Sequence[TraceStep], all_found: bool, timed_out: bool, ts: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    items: List[Dict[str, Any]] = []
    meta = {
        "learn_items": 0,
        "capture_credit_items": 0,
        "path_credit_items": 0,
        "missed_path_credit_items": 0,
        "timeout_credit_items": 0,
        "terminal_credit_items": 0,
    }

    def add(step: TraceStep, outcome: float, kind: str) -> None:
        if abs(float(outcome)) <= 1e-9:
            return
        items.append({
            "state_hash": str(step.state_hash),
            "action_canon": int(step.action_canon),
            "outcome": float(outcome),
            "ts": int(ts),
            "side": "seeker",
            "state_schema": STATE_SCHEMA,
            "action_schema": ACTION_SCHEMA,
            "credit_kind": str(kind),
        })
        meta["learn_items"] += 1
        if kind == "capture":
            meta["capture_credit_items"] += 1
        elif kind == "path":
            meta["path_credit_items"] += 1
        elif kind == "missed_path":
            meta["missed_path_credit_items"] += 1
        elif kind == "timeout":
            meta["timeout_credit_items"] += 1
        elif kind == "terminal_success":
            meta["terminal_credit_items"] += 1

    capture_credit_steps = max(1, _env_int("OROMA_HS_CAPTURE_CREDIT_STEPS", 12))
    terminal_success_steps = max(1, _env_int("OROMA_HS_TERMINAL_SUCCESS_CREDIT_STEPS", 16))
    timeout_steps = max(1, _env_int("OROMA_HS_TIMEOUT_CREDIT_STEPS", 16))
    path_learning = _env_bool("OROMA_HS_PATH_LEARNING", "1")

    # Local path credit: only clear path-following / clear missed-path moves.
    if path_learning:
        for step in trace:
            if step.found_delta > 0:
                continue
            if step.target_action is None or step.target_dist_after is None or step.fallback_dist_after is None:
                continue
            if int(step.action_canon) == int(step.target_action):
                add(step, 0.35, "path")
            elif int(step.target_dist_after) >= int(step.fallback_dist_after) + 1:
                add(step, -0.35, "missed_path")

    # Capture credit: the capture move and a short path window before it.
    for idx, step in enumerate(trace):
        if int(step.found_delta) <= 0:
            continue
        start = max(0, idx - int(capture_credit_steps) + 1)
        for prev in trace[start:idx + 1]:
            add(prev, 1.0, "capture")

    # Terminal credits: success receives a short positive tail; timeout receives a
    # short negative tail only, not all search steps.
    if all_found and trace:
        for step in trace[-terminal_success_steps:]:
            add(step, 1.0, "terminal_success")
    elif timed_out and trace:
        for step in trace[-timeout_steps:]:
            add(step, -1.0, "timeout")

    return items, meta


# -----------------------------------------------------------------------------
# Coverage / episode persistence
# -----------------------------------------------------------------------------

def _db_pro_coverage(namespace: str) -> Dict[str, int]:
    out = {"pro_states_known": 0, "pro_rules_known": 0, "pro_samples_known": 0}
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(DISTINCT state_hash) AS states,
                              COUNT(*) AS rules,
                              COALESCE(SUM(n),0) AS samples
                       FROM policy_rules
                       WHERE namespace=? AND state_hash LIKE ?""",
                    (str(namespace), f"{STATE_SCHEMA}%"),
                ).fetchone()
                if row is not None:
                    try:
                        out["pro_states_known"] = int(row["states"] or 0)
                        out["pro_rules_known"] = int(row["rules"] or 0)
                        out["pro_samples_known"] = int(row["samples"] or 0)
                    except Exception:
                        out["pro_states_known"] = int(row[0] or 0)
                        out["pro_rules_known"] = int(row[1] or 0)
                        out["pro_samples_known"] = int(row[2] or 0)
    except Exception as e:
        sys.stderr.write(f"[hideseek_daily_runner] coverage read failed: {e!r}\n")
    return out


def _db_write_episode(kind: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None or not hasattr(sql_manager, "insert_episode"):
        return None
    ts0 = int(meta.get("ts_start") or time.time())
    ts1 = int(meta.get("ts_end") or time.time())
    try:
        eid = sql_manager.insert_episode(
            ts_start=ts0,
            ts_end=ts1,
            kind=str(kind),
            source=str(meta.get("source") or "orchestrator"),
            label=str(meta.get("label") or kind),
            meta=meta,
        )
        return int(eid) if eid is not None else None
    except Exception as e:
        sys.stderr.write(f"[hideseek_daily_runner] DB insert_episode failed: {e!r}\n")
        return None


def _db_write_metrics(eid: int, metrics: Dict[str, Any]) -> bool:
    """Persist numeric episode metrics without misclassifying DBWriter success.

    In ORÓMA's DBWriter mode, core.sql_manager.insert_episodic_metric() may
    legitimately return None after a successful queued/write-through insert:
    the underlying DBWriter call is fire-and-confirm rather than a local
    sqlite cursor returning lastrowid. Older HideSeek pro_v2 status handling
    interpreted that None return as failure and therefore produced top-level
    ok=false/db_written=false even though the episode and policy_rules writes
    had succeeded.

    This helper treats the absence of an exception as a successful metric write
    request. Real failures remain visible because exceptions are still logged and
    make the function return False. No learning, state hashing, credit logic, or
    DBWriter policy-rule path is touched here.
    """
    if sql_manager is None or not hasattr(sql_manager, "insert_episodic_metric"):
        return False
    ts = int(time.time())
    ok = True
    for k, v in metrics.items():
        try:
            if isinstance(v, bool):
                fv = 1.0 if v else 0.0
            else:
                fv = float(v) if v is not None else 0.0
            sql_manager.insert_episodic_metric(
                episode_id=int(eid),
                ts=int(ts),
                key=str(k),
                value=float(fv),
            )
        except Exception as e:
            sys.stderr.write(f"[hideseek_daily_runner] DB metric failed ({k}): {e!r}\n")
            ok = False
    return ok


@dataclass
class BatchResult:
    ts_start: int
    ts_end: int
    duration_ms: int
    games: int
    wins_x: int
    wins_o: int
    draws: int
    avg_steps: float
    avg_found: float
    avg_game_ms: float
    mode: str
    namespace: str
    state_schema: str
    action_schema: str
    policy_enabled: float
    eps: float
    explore_moves_per_game: int
    explore_reduced: float
    no_more_explore: float
    learn: bool
    learn_items: int
    learned_items: int
    policy_learn_ok: bool
    learn_duration_ms: float
    sim_duration_ms: float
    policy_dbw_chunk: int
    policy_seen: int
    policy_accepted: int
    policy_fallback: int
    policy_rejected_n: int
    policy_rejected_q: int
    policy_rejected_unsafe: int
    visible_hider_steps: int
    target_known_steps: int
    path_moves_played: int
    captures: int
    timeout_games: int
    all_found_games: int
    capture_credit_items: int
    path_credit_items: int
    missed_path_credit_items: int
    timeout_credit_items: int
    terminal_credit_items: int
    pro_states_known_before: int
    pro_rules_known_before: int
    pro_samples_known_before: int
    pro_states_known: int
    pro_rules_known: int
    pro_samples_known: int
    max_steps: int
    source: str
    label: str
    runner: str
    shim: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def run_batch(
    rng: random.Random,
    namespace: str,
    mode: str,
    games: int,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    max_steps: int,
    source: str,
    coverage_before: Dict[str, int],
) -> BatchResult:
    ts_start = _now_ts()
    t0 = time.perf_counter()
    pol_stats = PolicyStats()
    tac_stats = TacticalStats()

    wins = 0
    draws = 0
    steps_sum = 0
    found_sum = 0
    all_items: List[Dict[str, Any]] = []
    credit_meta_total = {
        "learn_items": 0,
        "capture_credit_items": 0,
        "path_credit_items": 0,
        "missed_path_credit_items": 0,
        "timeout_credit_items": 0,
        "terminal_credit_items": 0,
    }

    for _gi in range(max(0, int(games))):
        game_seed = int(rng.getrandbits(32))
        env = LocalHideSeekEnv(seed=game_seed, max_steps=int(max_steps), rng=rng)
        explore_used = 0
        trace: List[TraceStep] = []

        while True:
            st = env.get_state()
            if st.get("done"):
                break
            legal = env.legal_actions() or _legal_actions_from_state(st)
            info = _target_info(st)
            if int(info.get("visible_count") or 0) > 0:
                tac_stats.visible_hider_steps += 1
            if info.get("has_target"):
                tac_stats.target_known_steps += 1

            do_rand = False
            if mode == "explore":
                if explore_used < max(0, int(explore_moves_per_game)):
                    do_rand = True
                elif rng.random() < max(0.0, float(eps)):
                    do_rand = True

            if do_rand:
                action = int(rng.choice([int(a) for a in legal]))
                explore_used += 1
            else:
                action = _db_choose_policy(namespace, st, legal, rng, pol_stats)

            fallback = _fallback_action(st, legal, rng)
            if int(action) == int(info.get("target_action")) if info.get("target_action") is not None else False:
                tac_stats.path_moves_played += 1

            sh = state_hash(st, legal)
            target_dist_before = info.get("target_dist")
            target_dist_after = _static_distance_after_action(st, int(action))
            fallback_dist_after = _static_distance_after_action(st, int(fallback))
            found_before = int(st.get("found", 0) or 0)
            st2, _reward, done, _step_info = env.step(int(action))
            found_after = int(st2.get("found", 0) or 0)
            found_delta = max(0, found_after - found_before)
            if found_delta > 0:
                tac_stats.captures += int(found_delta)
            trace.append(TraceStep(
                state_hash=str(sh),
                action_canon=int(action),
                target_action=int(info["target_action"]) if info.get("target_action") is not None else None,
                target_dist_before=int(target_dist_before) if target_dist_before is not None else None,
                target_dist_after=int(target_dist_after) if target_dist_after is not None else None,
                fallback_dist_after=int(fallback_dist_after) if fallback_dist_after is not None else None,
                found_delta=int(found_delta),
                ts=int(ts_start),
            ))
            if done:
                break

        final = env.get_state()
        all_found = bool(final.get("done") and int(final.get("found", 0)) >= 4)
        timed_out = bool(final.get("done") and int(final.get("found", 0)) < 4 and int(final.get("steps", 0)) >= int(max_steps))
        if all_found:
            wins += 1
            tac_stats.all_found_games += 1
        else:
            draws += 1
            if timed_out:
                tac_stats.timeout_games += 1
        steps_sum += int(final.get("steps", 0) or 0)
        found_sum += int(final.get("found", 0) or 0)

        if learn and trace:
            items, meta = _learn_items_from_trace(trace, bool(all_found), bool(timed_out), int(ts_start))
            all_items.extend(items)
            for k in credit_meta_total:
                credit_meta_total[k] += int(meta.get(k, 0))

    sim_end = time.perf_counter()
    learned_items = 0
    learn_ok = False
    learn_ms = 0.0
    if learn and all_items:
        learn_ok, learned_items, learn_ms = _learn_policy_rules_dbw(namespace, all_items)
    ts_end = _now_ts()
    t1 = time.perf_counter()
    coverage_after = _db_pro_coverage(namespace)

    dur_ms = int(round((t1 - t0) * 1000.0))
    sim_ms = round((sim_end - t0) * 1000.0, 3)
    avg_steps = (float(steps_sum) / float(games)) if games > 0 else 0.0
    avg_found = (float(found_sum) / float(games)) if games > 0 else 0.0
    avg_game_ms = (float(dur_ms) / float(games)) if games > 0 else 0.0
    label = f"hideseek:{mode} ({games} games)"

    return BatchResult(
        ts_start=int(ts_start),
        ts_end=int(ts_end),
        duration_ms=int(dur_ms),
        games=int(games),
        wins_x=int(wins),
        wins_o=0,
        draws=int(draws),
        avg_steps=float(round(avg_steps, 4)),
        avg_found=float(round(avg_found, 4)),
        avg_game_ms=float(round(avg_game_ms, 4)),
        mode=str(mode),
        namespace=str(namespace),
        state_schema=STATE_SCHEMA,
        action_schema=ACTION_SCHEMA,
        policy_enabled=1.0,
        eps=float(eps),
        explore_moves_per_game=int(explore_moves_per_game),
        explore_reduced=1.0 if (mode == "explore" and (float(eps) < _env_float("OROMA_HIDESEEK_EPS", 0.08) or int(explore_moves_per_game) < _env_int("OROMA_HIDESEEK_EXPLORE_MOVES", 1))) else 0.0,
        no_more_explore=0.0,
        learn=bool(learn),
        learn_items=int(len(all_items)),
        learned_items=int(learned_items),
        policy_learn_ok=bool(learn_ok),
        learn_duration_ms=float(learn_ms),
        sim_duration_ms=float(sim_ms),
        policy_dbw_chunk=max(1, _env_int("OROMA_HS_POLICY_DBW_CHUNK", 500)),
        policy_seen=int(pol_stats.seen),
        policy_accepted=int(pol_stats.accepted),
        policy_fallback=int(pol_stats.fallback),
        policy_rejected_n=int(pol_stats.rejected_n),
        policy_rejected_q=int(pol_stats.rejected_q),
        policy_rejected_unsafe=int(pol_stats.rejected_unsafe),
        visible_hider_steps=int(tac_stats.visible_hider_steps),
        target_known_steps=int(tac_stats.target_known_steps),
        path_moves_played=int(tac_stats.path_moves_played),
        captures=int(tac_stats.captures),
        timeout_games=int(tac_stats.timeout_games),
        all_found_games=int(tac_stats.all_found_games),
        capture_credit_items=int(credit_meta_total["capture_credit_items"]),
        path_credit_items=int(credit_meta_total["path_credit_items"]),
        missed_path_credit_items=int(credit_meta_total["missed_path_credit_items"]),
        timeout_credit_items=int(credit_meta_total["timeout_credit_items"]),
        terminal_credit_items=int(credit_meta_total["terminal_credit_items"]),
        pro_states_known_before=int(coverage_before.get("pro_states_known", 0)),
        pro_rules_known_before=int(coverage_before.get("pro_rules_known", 0)),
        pro_samples_known_before=int(coverage_before.get("pro_samples_known", 0)),
        pro_states_known=int(coverage_after.get("pro_states_known", 0)),
        pro_rules_known=int(coverage_after.get("pro_rules_known", 0)),
        pro_samples_known=int(coverage_after.get("pro_samples_known", 0)),
        max_steps=int(max_steps),
        source=str(source),
        label=str(label),
        runner="tools/hideseek_daily_runner.py",
        shim="tools/hideseek_daily_runner.pro_v2_tactical_bfs",
    )


def _metrics_from_result(res: BatchResult) -> Dict[str, float]:
    skip = {"mode", "namespace", "state_schema", "action_schema", "source", "label", "runner", "shim"}
    out: Dict[str, float] = {}
    for k, v in res.to_dict().items():
        if k in skip:
            continue
        if isinstance(v, bool):
            out[k] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def _persist_result(res: BatchResult) -> bool:
    meta = res.to_dict()
    kind = f"{res.namespace}:policy_batch" if res.mode == "policy" else f"{res.namespace}:explore_batch"
    eid = _db_write_episode(kind=kind, meta=meta)
    db_ok = False
    if eid is not None:
        db_ok = _db_write_metrics(int(eid), _metrics_from_result(res))
        meta["episode_id"] = int(eid)
    return bool(db_ok)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Hide&Seek daily runner (pro_v2 tactical BFS policy + explore)")
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_ORCH_HIDESEEK_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_ORCH_HIDESEEK_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=int(time.time()) & 0xFFFFFFFF)
    ap.add_argument("--namespace", type=str, default=os.environ.get("OROMA_HIDESEEK_POLICY_NAMESPACE", DEFAULT_NAMESPACE) or DEFAULT_NAMESPACE)
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_HIDESEEK_MAX_STEPS", 400))
    args = ap.parse_args()

    namespace = str(args.namespace or DEFAULT_NAMESPACE)
    rng = random.Random(int(args.seed))
    eps = float(_env_float("OROMA_HIDESEEK_EPS", 0.08))
    explore_moves = int(_env_int("OROMA_HIDESEEK_EXPLORE_MOVES", 1))

    coverage0 = _db_pro_coverage(namespace)
    reduce_rules = _env_int("OROMA_HS_EXPLORE_REDUCE_RULES", 20000)
    if int(coverage0.get("pro_rules_known", 0)) >= int(reduce_rules):
        eps = float(_env_float("OROMA_HS_EXPLORE_REDUCED_EPS", 0.02))
        explore_moves = int(_env_int("OROMA_HS_EXPLORE_REDUCED_MOVES", 0))

    policy_res = run_batch(
        rng=rng,
        namespace=namespace,
        mode="policy",
        games=max(0, int(args.policy_games)),
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        max_steps=int(args.max_steps),
        source="orchestrator",
        coverage_before=coverage0,
    )
    policy_db_ok = _persist_result(policy_res)

    coverage1 = _db_pro_coverage(namespace)
    explore_res = run_batch(
        rng=rng,
        namespace=namespace,
        mode="explore",
        games=max(0, int(args.explore_games)),
        eps=float(eps),
        explore_moves_per_game=int(explore_moves),
        learn=True,
        max_steps=int(args.max_steps),
        source="orchestrator",
        coverage_before=coverage1,
    )
    explore_db_ok = _persist_result(explore_res)

    ok = bool(policy_db_ok) and bool(explore_db_ok)
    print(json.dumps({
        "ok": bool(ok),
        "have_up": True,
        "db_written": bool(ok),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "seed": int(args.seed),
        "policy": policy_res.to_dict(),
        "explore": explore_res.to_dict(),
    }, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

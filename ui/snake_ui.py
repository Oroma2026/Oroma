#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/snake_ui.py
# Projekt: ORÓMA – Headless UI (Flask)
# Modul:   Snake Arena (DB-Ingest + Universal-Policy + ε-Explore + Zähler)
# Version: v3.7.7 (Policy-Integration live; D4-hash via Adapter; Legal-Actions)
# Stand:   2025-11-10
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# NEU in v3.7.7
# ─────────────
#  • ECHTE Policy-Nutzung (core.universal_policy.Policy) für ORÓMA-Züge:
#      – Modus „oroma_vs_oroma_policy“: rein policybasiert (ohne ε)
#      – Modus „oroma_vs_oroma_explore“: Policy + ε-Exploration
#  • Legal-Actions werden aus kollisionsfreien Richtungen abgeleitet und
#    an die Policy übergeben (Mapping 0:R,1:L,2:D,3:U).
#  • State→Hash per UniversalAdapter-Spezifikation (Head/Food koordiniert).
#  • Policy-Badge reflektiert, ob die Universal-Policy importierbar ist.
#
# ENV / Tuning (unverändert, auszugsweise)
# ────────────────────────────────────────
#  OROMA_SNAKE_MODE_DEFAULT=oroma_vs_oroma_explore|oroma_vs_oroma_policy|...
#  OROMA_SNAKE_TICK_MS=150
#  OROMA_SNAKE_EPS=0.07
#  OROMA_SNAKE_EPS_DECAY=1.0
#  OROMA_SNAKE_EPS_MIN=0.00
#  OROMA_SNAKE_LOSS_PRESSURE=2.0
#  OROMA_SNAKE_HEAT_DECAY=0.999
#  OROMA_SNAKE_EXPORT_DIR=/opt/ai/oroma/data/snapchains
#
#  – plus Policy-ENV (siehe core/universal_policy.py):
#    OROMA_UP_AUTO_EXPORT, OROMA_UP_MIN_N, OROMA_UP_MIN_ABS_Q, ...
# =============================================================================

from __future__ import annotations

import os, sys, pwd, json, time, random, threading, logging
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app, render_template
import logging
from core.log_guard import log_suppressed

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.snake")
if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [Snake] %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); LOG.addHandler(sh)
    try:
        Path("/opt/ai/oroma/logs").mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler("/opt/ai/oroma/logs/snake_ui.log", encoding="utf-8")
        fh.setFormatter(fmt); LOG.addHandler(fh)
    except Exception as e:
        print(f"[snake_ui] ⚠️ FileHandler-Fehler: {e}")

# -----------------------------------------------------------------------------
# Core-Imports
# -----------------------------------------------------------------------------
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

try:
    from core.snap import Snap
    from core.snapchain import SnapChain, save_chain
    LOG.info("✅ Core importiert (Snap=%s, SnapChain=%s, save_chain=%s)",
             bool(Snap), bool(SnapChain), "callable" if callable(save_chain) else False)
except Exception as e:
    Snap = None       # type: ignore
    SnapChain = None  # type: ignore
    save_chain = None # type: ignore
    LOG.exception("⚠️ Core-Importfehler: %s", e)

try:
    from core import sql_manager
    _HAS_SQL = True
    try:
        sql_manager.ensure_schema()
    except Exception as e:
        log_suppressed('ui/snake_ui.py:82', exc=e, level=logging.WARNING)
        pass
except Exception:
    sql_manager = None  # type: ignore
    _HAS_SQL = False

# ---- Universal Policy (optional, aber bevorzugt) ----------------------------
try:
    from core.universal_policy import Policy as _UPolicy
    _UPOLICY = _UPolicy(namespace="game:snake")
    _HAS_UPOL = True
    LOG.info("🧠 Universal-Policy geladen (namespace=game:snake)")
except Exception as e:
    _UPOLICY = None   # type: ignore
    _HAS_UPOL = False
    LOG.info("ℹ️ Universal-Policy nicht verfügbar: %s", e)

# -----------------------------------------------------------------------------
# Export / Policy / Autoplay – Modulweite Flags & Zähler
# -----------------------------------------------------------------------------
EXPORT_DIR = os.environ.get("OROMA_SNAKE_EXPORT_DIR",
             os.environ.get("OROMA_SNAPCHAINS", "/opt/ai/oroma/data/snapchains"))
Path(EXPORT_DIR).mkdir(parents=True, exist_ok=True)
LOG.info("📂 Exportverzeichnis: %s (beschreibbar=%s)", EXPORT_DIR, os.access(EXPORT_DIR, os.W_OK))

try:
    uid, gid = os.getuid(), os.getgid()
    LOG.info("👤 Laufzeit: user=%s (uid=%d,gid=%d) cwd=%s",
             pwd.getpwuid(uid).pw_name, uid, gid, os.getcwd())
except Exception:
    LOG.debug("Hinweis: pwd/getuid ggf. nicht verfügbar.")

# Modulzustand
# Policy ist nur „enablebar“, wenn Modul vorhanden ist
_POLICY_ENABLED: bool = bool(_HAS_UPOL)   # Badge/Toggle spiegelt tatsächliche Verfügbarkeit
_AUTOPLAY_ENABLED: bool = False
_EXPORTS_SESSION: int = 0

# -----------------------------------------------------------------------------
# Blueprint
# -----------------------------------------------------------------------------
snake_bp = Blueprint("snake", __name__, url_prefix="/games/snake")

# =============================================================================
# Utils
# =============================================================================
def _norm_mode(m: str) -> str:
    m = (m or "").lower()
    alias = {
        "oroma_vs_oroma": "oroma_vs_oroma_explore",
        "oroma_solo": "oroma_vs_oroma_explore",
        "human_vs_oroma": "oroma_vs_human",
        "oroma_vs_ki": "ki_vs_oroma",
    }
    return alias.get(m, m)

def _dir_to_idx(d: Tuple[int, int]) -> int:
    mapping = {(1,0):0, (-1,0):1, (0,1):2, (0,-1):3}
    return mapping.get(d, 0)

def _idx_to_dir(i: int) -> Tuple[int, int]:
    m = {0:(1,0), 1:(-1,0), 2:(0,1), 3:(0,-1)}
    return m.get(int(i) % 4, (1,0))

def _db_count_snapchains_ns() -> int:
    if not _HAS_SQL: return 0
    try:
        return int(sql_manager.count_snapchains(origin="game:snake"))
    except Exception:
        try:
            with sql_manager.get_conn() as c:  # type: ignore
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM snapchains WHERE origin = 'game:snake' OR origin LIKE 'game:snake%'"
                ).fetchone()
                return int(row["n"] if row and "n" in row else 0)
        except Exception:
            return 0

def _db_count_snapchains_total() -> int:
    if not _HAS_SQL: return 0
    try:
        return int(sql_manager.count_snapchains())
    except Exception:
        try:
            with sql_manager.get_conn() as c:  # type: ignore
                row = c.execute("SELECT COUNT(*) AS n FROM snapchains").fetchone()
                return int(row["n"] if row and "n" in row else 0)
        except Exception:
            return 0

def _policy_counts() -> Dict[str, int]:
    prc = 0; arc = 0
    if not _HAS_SQL: return {"policy_rules_count": prc, "archiv_rules_count": arc}
    try:
        with sql_manager.get_conn() as c:  # type: ignore
            prc = int(c.execute(
                "SELECT COUNT(*) AS n FROM policy_rules WHERE namespace=?",
                ("game:snake",)
            ).fetchone()["n"])
    except Exception as e:
        log_suppressed('ui/snake_ui.py:182', exc=e, level=logging.WARNING)
        pass
    try:
        with sql_manager.get_conn() as c:  # type: ignore
            row = c.execute(
                "SELECT COUNT(*) AS n FROM rules WHERE active=1 AND content LIKE 'policy::game:snake%'"
            ).fetchone()
            if row: arc = int(row["n"])
    except Exception as e:
        log_suppressed('ui/snake_ui.py:191', exc=e, level=logging.WARNING)
        pass
    return {"policy_rules_count": prc, "archiv_rules_count": arc}

def _grid_state_hash(*,
                    w: int,
                    h: int,
                    self_body: List[Tuple[int, int]],
                    other_body: List[Tuple[int, int]],
                    food: Tuple[int, int],
                    side_label: str) -> str:
    """Compute a full-board state hash for Snake.

    Purpose
    -------
    The user requires that the Snake policy can "see" the complete playfield,
    including its own body. Earlier versions used a compact vector
    (length/head/food/tick) which discards spatial constraints.

    Implementation
    --------------
    We encode the complete grid into a compact byte-array and hash it.
    The policy then operates on a stable string key (state_hash) without
    needing to understand the grid itself.

    Cell encoding
    ------------
      0 empty
      1 self body
      2 other body
      3 food
      4 self head (overrides body)
      5 other head (overrides body)

    The returned value includes grid size + side label to avoid collisions
    across configurations.
    """
    try:
        import hashlib
        ww = int(w); hh = int(h)
        grid = bytearray(ww * hh)
        for (x, y) in other_body:
            grid[(int(y) % hh) * ww + (int(x) % ww)] = 2
        for (x, y) in self_body:
            grid[(int(y) % hh) * ww + (int(x) % ww)] = 1
        if food is not None:
            fx, fy = int(food[0]) % ww, int(food[1]) % hh
            grid[fy * ww + fx] = 3
        if self_body:
            sx, sy = int(self_body[0][0]) % ww, int(self_body[0][1]) % hh
            grid[sy * ww + sx] = 4
        if other_body:
            ox, oy = int(other_body[0][0]) % ww, int(other_body[0][1]) % hh
            grid[oy * ww + ox] = 5
        return f"snake:g:{ww}x{hh}:{side_label}:" + hashlib.sha1(bytes(grid)).hexdigest()
    except Exception:
        return f"snake:g:{w}x{h}:{side_label}:fallback"

def _legend_for_mode(mode: str) -> Dict[str, str]:
    oroma_color = "#09f"
    op_color    = "#0f0"
    food_color  = "#f33"
    if mode == "ki_vs_oroma": op_name = "KI"
    elif mode == "oroma_vs_human": op_name = "Human"
    else: op_name = "ORÓMA"
    return {
        "oroma_color": oroma_color,
        "op_color": op_color,
        "food_color": food_color,
        "oroma_name": "ORÓMA",
        "op_name": op_name,
    }

# =============================================================================
# Snake Engine
# =============================================================================
class SnakeGame:
    MODES = ("ki_vs_oroma", "oroma_vs_human", "oroma_vs_oroma_explore", "oroma_vs_oroma_policy")

    def __init__(self, w: int = 24, h: int = 18):
        self.w, self.h = w, h

        # ENV
        self.tick_ms    = max(10, int(os.environ.get("OROMA_SNAKE_TICK_MS", "150") or "150"))
        self.eps        = float(os.environ.get("OROMA_SNAKE_EPS", "0.07") or "0.07")
        self.eps_decay  = float(os.environ.get("OROMA_SNAKE_EPS_DECAY", "1.0") or "1.0")
        self.eps_min    = float(os.environ.get("OROMA_SNAKE_EPS_MIN", "0.00") or "0.00")
        self.loss_w     = float(os.environ.get("OROMA_SNAKE_LOSS_PRESSURE", "2.0") or "2.0")
        self.heat_decay = float(os.environ.get("OROMA_SNAKE_HEAT_DECAY", "0.999") or "0.999")
        mdef_raw        = os.environ.get("OROMA_SNAKE_MODE_DEFAULT", "oroma_vs_oroma_explore") or "oroma_vs_oroma_explore"
        self.mode       = _norm_mode(mdef_raw) if _norm_mode(mdef_raw) in self.MODES else "oroma_vs_oroma_explore"

        # Laufstatus / Sync
        self.lock = threading.Lock()
        self.running = False
        self._stop = False

        # Stats
        self.games_played = 0
        self.tick_counter = 0
        self.highscore_time = 0
        self.last_cause: Optional[str] = None

        # Policy/Autoplay Snapshot (UI)
        self.policy_enabled = bool(_POLICY_ENABLED and _HAS_UPOL)
        self.autoplay_enabled = _AUTOPLAY_ENABLED

        # Gegner-Dir (Human)
        self.dir_op = (0, 0)

        # Heatmap (Loss-Lernen)
        self.heat = [[0.0 for _ in range(self.w)] for _ in range(self.h)]

        # SnapChain + letzte Aktion
        self.snapchain: Optional[SnapChain] = None
        self._last_action_idx: int = 0
        self._ensure_snapchain("init")

        # Start
        self.reset()
        threading.Thread(target=self._loop, daemon=True, name="Snake-Loop").start()

    # ---------------- Robustheit / SnapChain ----------------
    def _ensure_snapchain(self, context: str = "") -> bool:
        if self.snapchain is not None:
            return True
        if 'SnapChain' in globals() and callable(SnapChain):
            try:
                self.snapchain = SnapChain(patterns=[], metadata={"game": "snake"})
                LOG.debug("🧠 SnapChain initialisiert (%s)", context or "n/a")
                return True
            except Exception:
                LOG.exception("❌ SnapChain-Init fehlgeschlagen (%s)", context or "n/a")
                self.snapchain = None
        else:
            LOG.warning("⚠️ SnapChain-Klasse nicht verfügbar (%s)", context or "n/a")
        return False

    # ---------------- Setup / Reset ----------------
    def reset(self):
        with self.lock:
            self.tick_counter = 0
            self.last_cause = None
            # ORÓMA
            self.snake_oroma: List[Tuple[int, int]] = [(self.w // 2, self.h // 2)]
            self.dir_oroma: Tuple[int, int] = random.choice([(1,0),(-1,0),(0,1),(0,-1)])
            self._last_action_idx = _dir_to_idx(self.dir_oroma)
            # Gegner
            if self.mode == "oroma_vs_human":
                self.snake_op = [(3, 3), (2, 3), (1, 3)]
                self.dir_op = (0, 0)
            else:
                self.snake_op = [(3, 3), (2, 3), (1, 3)]
                self.dir_op = (1, 0)
            # Futter
            self.food = self._rand_food()
            self.games_played += 1
            self.running = False
        LOG.info("🎮 Neues Snake-Game (games=%d, mode=%s, ε=%.3f, policy=%s, autoplay=%s)",
                 self.games_played, self.mode, self.eps, "on" if self.policy_enabled else "off",
                 "on" if self.autoplay_enabled else "off")

    def _rand_food(self) -> Tuple[int, int]:
        while True:
            f = (random.randint(0, self.w - 1), random.randint(0, self.h - 1))
            if f not in self.snake_oroma and f not in self.snake_op:
                return f

    # ---------------- Hauptloop ----------------
    def _loop(self):
        global _EXPORTS_SESSION
        while not self._stop:
            time.sleep(self.tick_ms / 1000.0)
            if not self.running:
                continue
            alive, final_reward, cause = self.step()
            if not alive:
                self.last_cause = cause
                LOG.info("🏁 Game Over (cause=%s, reward=%.2f) – Board 2s sichtbar", cause, final_reward)
                # Heatmap verstärken an Tod-Zelle(n)
                try:
                    hx, hy = self.snake_oroma[0]
                    self.heat[hy][hx] += 3.0
                    for (x, y) in self.snake_oroma[1:1+3]:
                        self.heat[y][x] += 1.0
                except Exception as e:
                    log_suppressed('ui/snake_ui.py:323', exc=e, level=logging.WARNING)
                    pass
                time.sleep(2.0)
                if final_reward != 0.0:
                    self._log_snap(final_reward, tag="terminal")
                ok = self._export_snapchain()  # Datei + DB
                if ok:
                    _EXPORTS_SESSION += 1
                # ε-Decay pro Spiel
                self.eps = max(self.eps_min, self.eps * self.eps_decay)
                self.reset()
                if self.autoplay_enabled:
                    self.running = True

    # ---------------- Richtungsheuristiken + Policy ----------------
    @staticmethod
    def _dist_manh(a: Tuple[int, int], b: Tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _safe_dirs(self, head: Tuple[int, int], avoid: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        dirs = [(1,0),(-1,0),(0,1),(0,-1)]
        out: List[Tuple[int, int]] = []
        for d in dirs:
            nx, ny = (head[0] + d[0]) % self.w, (head[1] + d[1]) % self.h
            if (nx, ny) not in avoid:
                out.append(d)
        return out or dirs

    def _score_move(self, head: Tuple[int, int], d: Tuple[int, int], other: List[Tuple[int, int]]) -> float:
        nx, ny = (head[0] + d[0]) % self.w, (head[1] + d[1]) % self.h
        food_term = self._dist_manh((nx, ny), self.food) * 1.0
        heat_term = self.heat[ny][nx] * self.loss_w
        near_other = min(self._dist_manh((nx, ny), p) for p in other) if other else 9
        other_term = 0.0 if near_other >= 2 else (2 - near_other) * 1.5
        return heat_term + other_term + 0.1 * food_term

    def _state_vec(self) -> List[float]:
        """Vektor wie beim Snap-Logging (kompakt, normalisiert)."""
        hx, hy = self.snake_oroma[0]
        fx, fy = self.food
        return [
            float(len(self.snake_oroma)),
            float(len(self.snake_op)),
            hx / self.w, hy / self.h,
            fx / self.w, fy / self.h,
            float(self.tick_counter),
        ]

    def _state_spec(self) -> Dict[str, Any]:
        """UniversalAdapter-Spezifikation für Hash/Kanonisierung."""
        return {
            "space": "world2d",
            "symmetry": "square_D4",
            "action": {"kind": "dir2"},
            "indices": {"head": [2, 3], "food": [4, 5]},
            "hash_spec": {"bins": 12, "dims": 8}
        }

    def _dir_oroma(self, body: List[Tuple[int, int]], other: List[Tuple[int, int]], policy_only: bool) -> Tuple[int, int]:
        """Policy→Action (falls verfügbar) mit ε-Explore; sonst Heuristik."""
        head = body[0]
        avoid = set(body[1:] + other)
        cand = self._safe_dirs(head, list(avoid))
        # Exploration im Explore-Modus
        if not policy_only and random.random() < self.eps:
            return random.choice(cand)

        # Policy nutzen, wenn aktiviert + verfügbar
        if self.policy_enabled and _HAS_UPOL and _UPOLICY is not None:
            try:
                legal = [ _dir_to_idx(d) for d in cand ]
                # FULL-BOARD-VISION (User-Anforderung):
                # Statt eines kompakten Vektors wird der komplette Grid-Zustand
                # inkl. eigenem Körper, Gegner und Food gehasht.
                side_label = "X" if body is self.snake_oroma else "O"
                state_hash = _grid_state_hash(
                    w=self.w,
                    h=self.h,
                    self_body=body,
                    other_body=other,
                    food=self.food,
                    side_label=side_label,
                )
                if state_hash:
                    a = _UPOLICY.choose(state_hash, legal)
                    if a is not None:
                        return _idx_to_dir(int(a))
            except Exception as e:
                LOG.debug("Policy choose fehlgeschlagen: %s", e)

        # Fallback: Heuristik (heatmap + food)
        best = min(cand, key=lambda d: self._score_move(head, d, other))
        return best

    def _dir_ki(self, body: List[Tuple[int, int]]) -> Tuple[int, int]:
        head = body[0]
        dirs = self._safe_dirs(head, body[1:])
        sx = 1 if self.food[0] > head[0] else -1 if self.food[0] < head[0] else 0
        sy = 1 if self.food[1] > head[1] else -1 if self.food[1] < head[1] else 0
        pref = (sx, 0) if sx != 0 else (0, sy)
        return pref if pref in dirs else random.choice(dirs)

    # ---------------- Schritt / Kollisionen + Snap-Logging ----------------
    def _advance(self, body: List[Tuple[int, int]], d: Tuple[int, int]) -> Tuple[Tuple[int,int], bool, bool]:
        nh = ((body[0][0] + d[0]) % self.w, (body[0][1] + d[1]) % self.h)
        dead_self = nh in body
        body.insert(0, nh)
        ate = nh == self.food
        if not ate:
            body.pop()
        return nh, ate, dead_self

    def step(self) -> Tuple[bool, float, str]:
        with self.lock:
            self.tick_counter += 1
            # Heatmap kühlt ab
            try:
                for y in range(self.h):
                    row = self.heat[y]
                    for x in range(self.w):
                        hv = row[x] * self.heat_decay
                        row[x] = hv if hv > 1e-6 else 0.0
            except Exception as e:
                log_suppressed('ui/snake_ui.py:437', exc=e, level=logging.WARNING)
                pass

            policy_only = (self.mode == "oroma_vs_oroma_policy")
            # Richtungen
            if self.mode == "ki_vs_oroma":
                d_o = self._dir_oroma(self.snake_oroma, self.snake_op, policy_only)
                d_p = self._dir_ki(self.snake_op)
            elif self.mode in ("oroma_vs_oroma_explore", "oroma_vs_oroma_policy"):
                d_o = self._dir_oroma(self.snake_oroma, self.snake_op, policy_only)
                d_p = self._dir_oroma(self.snake_op, self.snake_oroma, policy_only)
            elif self.mode == "oroma_vs_human":
                d_o = self._dir_oroma(self.snake_oroma, self.snake_op, policy_only=False)
                d_p = self.dir_op
            else:
                d_o = self._dir_oroma(self.snake_oroma, self.snake_op, policy_only=False)
                d_p = self._dir_ki(self.snake_op)

            self._last_action_idx = _dir_to_idx(d_o)

            nh_o, ate_o, dead_self_o = self._advance(self.snake_oroma, d_o)
            nh_p, ate_p, dead_self_p = self._advance(self.snake_op, d_p) if d_p != (0,0) else (self.snake_op[0], False, False)

            dead_o = dead_self_o or (nh_o in self.snake_op[1:]) or (nh_o == nh_p and d_p != (0,0))
            dead_p = dead_self_p or (nh_p in self.snake_oroma[1:]) or (nh_p == nh_o and d_p != (0,0))

            reward = 0.0
            if dead_o and dead_p:
                reward -= 1.0
                self._append_snap(reward)
                return False, reward, "head_on"
            if dead_o:
                reward -= 2.0
                self._append_snap(reward)
                return False, reward, "oroma_dead"
            if dead_p:
                reward += 0.5
                self._append_snap(reward)
                return False, reward, "op_dead"

            if ate_o or ate_p:
                if ate_o: reward += 1.0
                if ate_p: reward += 0.4
                self.food = self._rand_food()

            if self.tick_counter > self.highscore_time:
                self.highscore_time = self.tick_counter
                reward += 0.2

            self._append_snap(reward)
            return True, reward, "tick"

    def _append_snap(self, reward_value: float):
        if not (Snap and self._ensure_snapchain("step")):
            return
        try:
            vec = self._state_vec()
            meta = {"reward": float(reward_value), "mode": self.mode, "action": int(self._last_action_idx)}
            self.snapchain.append(Snap(vec, metadata=meta))  # type: ignore[union-attr]
        except Exception:
            LOG.exception("❌ Snap-Erstellung fehlgeschlagen")

    def _log_snap(self, reward_value: float, tag: str = "terminal"):
        if not (Snap and self._ensure_snapchain("_log_snap")):
            return
        try:
            s = Snap([float(reward_value)], metadata={"reward": float(reward_value), "mode": self.mode, "tag": tag, "action": int(self._last_action_idx)})
            self.snapchain.append(s)  # type: ignore[union-attr]
        except Exception:
            LOG.exception("❌ Terminal-Snap fehlgeschlagen")

    def _export_snapchain(self) -> bool:
        LOG.info("🧠 [BEGIN] Export+DB")
        if not (self.snapchain and save_chain):
            LOG.warning("⚠️ Export übersprungen (snapchain/save_chain fehlt)")
            LOG.info("🧠 [END] Export+DB (abgebrochen)")
            return False
        try:
            length = len(self.snapchain.patterns)  # type: ignore[union-attr]
            if length == 0:
                LOG.info("ℹ️ Leere Chain – kein Export")
                LOG.info("🧠 [END] Export+DB (leer)")
                return False

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = save_chain(f"snake_{ts}", self.snapchain)  # type: ignore[arg-type]
            LOG.info("💾 SnapChain exportiert → %s", path)

            # DB-Ingestion
            try:
                if _HAS_SQL:
                    with open(path, "rb") as f:
                        blob_b = f.read()
                    inserted = False
                    if hasattr(sql_manager, "insert_snapchain"):
                        rid = sql_manager.insert_snapchain({
                            "blob": blob_b,
                            "origin": "game:snake",
                            "namespace": "game:snake",
                            "source_id": chain_id,
                            "notes": f"snake_ui chain_id={chain_id} file={os.path.basename(path)}",
                            "quality": float(min(1.0, max(0.0, len(self.snapchain.patterns) / 50.0))),
                            "status": "active",
                            "version": "v3.7.7",
                            "ts": int(time.time()),
                            "weight": 1.0
                        })
                        inserted = bool(rid)
                    else:
                        with sql_manager.get_conn() as conn:  # type: ignore
                            conn.execute(
                                "INSERT INTO snapchains (ts, quality, blob, exported, status, origin, weight) VALUES (?,?,?,?,?,?,?)",
                                (int(time.time()), 0.0, blob_b, 0, "active", "game:snake", 1.0)
                            )
                            conn.commit()
                            inserted = True
                    LOG.info("📥 DB-Ingestion %s (origin=game:snake, len=%d)", "ok" if inserted else "fehlgeschlagen", length)
                else:
                    LOG.warning("⚠️ sql_manager nicht verfügbar – DB-Ingestion übersprungen")
            except Exception as e:
                LOG.error("❌ DB-Insert für Snake-SnapChain fehlgeschlagen: %s", e)

            # Reset
            self.snapchain = None
            self._ensure_snapchain("post-export reset")
        except Exception:
            LOG.exception("❌ Exportfehler")
            return False
        LOG.info("🧠 [END] Export+DB")
        return True

    # ---------------- API State / Cmd ----------------
    def _policy_state(self) -> Dict[str, Any]:
        d = _policy_counts()
        d.update({
            "enabled": bool(self.policy_enabled and _HAS_UPOL),
            "eps": float(self.eps),
            "namespace": "game:snake"
        })
        return d

    def state(self) -> Dict[str, Any]:
        with self.lock:
            seconds = int(self.tick_counter * (self.tick_ms / 1000.0))
            return {
                "width": self.w, "height": self.h,
                "snake_ai": [{"x": x, "y": y} for x, y in self.snake_oroma],
                "snake": [{"x": x, "y": y} for x, y in self.snake_op],
                "food": {"x": self.food[0], "y": self.food[1]},
                "running": self.running,
                "games_played": self.games_played,
                "mode": self.mode,
                "ticks": self.tick_counter,
                "seconds": seconds,
                "highscore_time": self.highscore_time,
                "cause": self.last_cause,
                "eps": round(self.eps, 4),
                "tick_ms": self.tick_ms,
                "policy": self._policy_state(),
                "autoplay": bool(self.autoplay_enabled),
                "legend": _legend_for_mode(self.mode),
                "oroma_len": len(self.snake_oroma),
                "op_len": len(self.snake_op),
                "exports_session": int(_EXPORTS_SESSION),
                "snaps_in_ram": int(len(self.snapchain.patterns) if self.snapchain else 0),
                "snapchains_in_db": _db_count_snapchains_ns(),
                "snaps_total_db": _db_count_snapchains_total(),
            }

    def cmd(self, data: Dict[str, Any]):
        c = (data.get("cmd") or "").lower()
        if c == "start":
            self.running = True
        elif c == "pause":
            self.running = False
        elif c == "reset":
            self.reset()
        elif c == "dir":
            self.dir_op = (int(data.get("dx", 0)), int(data.get("dy", 0)))
        elif c == "mode":
            m = _norm_mode(data.get("mode") or "")
            if m in self.MODES:
                self.mode = m
                self.reset()

# =============================================================================
# Flask-Routen
# =============================================================================
def _game() -> SnakeGame:
    if "_snake_game" not in current_app.config:
        current_app.config["_snake_game"] = SnakeGame()
    return current_app.config["_snake_game"]

@snake_bp.get("/state")
def api_state():
    return jsonify(_game().state())

@snake_bp.post("/cmd")
def api_cmd():
    g = _game()
    try:
        g.cmd(request.get_json(force=True) or {})
    except Exception as e:
        LOG.warning("cmd error: %s", e)
    return jsonify({"ok": True, "state": g.state()})

# ------ Policy UI (ON/OFF + ε) ------
@snake_bp.get("/policy")
def api_policy_get():
    g = _game()
    return jsonify(g._policy_state())

@snake_bp.post("/policy")
def api_policy_set():
    global _POLICY_ENABLED
    g = _game()
    data = {}
    try:
        data = request.get_json(force=True) or {}
    except Exception as e:
        log_suppressed('ui/snake_ui.py:654', exc=e, level=logging.WARNING)
        pass
    if "enabled" in data:
        # nur toggelbar, wenn UP verfügbar
        want = bool(data.get("enabled"))
        if _HAS_UPOL:
            _POLICY_ENABLED = want
            g.policy_enabled = want
        else:
            _POLICY_ENABLED = False
            g.policy_enabled = False
    if "eps" in data:
        try:
            g.eps = float(data.get("eps"))
        except Exception as e:
            log_suppressed('ui/snake_ui.py:669', exc=e, level=logging.WARNING)
            pass
    st = g._policy_state()
    st["ok"] = True
    return jsonify(st)

# ------ Autoplay ------
@snake_bp.get("/auto")
def api_auto_get():
    g = _game()
    return jsonify({"enabled": bool(g.autoplay_enabled)})

@snake_bp.post("/auto")
def api_auto_set():
    g = _game()
    body = {}
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        log_suppressed('ui/snake_ui.py:688', exc=e, level=logging.WARNING)
        pass
    enabled = bool(body.get("enabled", True))
    g.autoplay_enabled = enabled
    return jsonify({"ok": True, "enabled": bool(g.autoplay_enabled)})

@snake_bp.get("/")
def page():
    return render_template("snake.html")
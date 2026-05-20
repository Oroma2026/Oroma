#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/tetris_ui.py
# Projekt: ORÓMA – Headless UI (Flask)
# Modul:   Tetris – Standard-UI Blueprint (client-side autoplay, kein Server-Thread)
# Version: v1.1
# Stand:   2026-02-23
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# Wichtiger Architektur-Hinweis (Headless / Stabilität)
# -----------------------------------------------------
#   Frühere Versionen hatten einen serverseitigen Tick-Thread, der automatisch
#   `eng.step()` in einer Endlosschleife ausführt. Das ist bei ORÓMA im Dauerbetrieb
#   und im Orchestrator-Modus ungünstig, weil:
#     • Threads im Service schwerer zu überwachen sind,
#     • UI-Besuche ungewollt Game-Progress auslösen können,
#     • die Standard-Pattern der anderen Spiele "client-side autoplay" nutzt.
#
#   Diese Version folgt dem ORÓMA-Standard:
#     • Kein Background-Thread im Service.
#     • Autoplay ist rein client-side getaktet.
#     • Der Server bietet dafür eine deterministische /api/step Route.
#
# Routen
# ------
#   GET    /tetris/              → tetris.html
#   GET    /tetris/api/ping      → {ok:true}
#   GET    /tetris/api/state     → kompletter Spielzustand (JSON)
#   POST   /tetris/api/step      → führt 1 Gravity-Tick aus (eng.step())
#   POST   /tetris/api/cmd       → {"cmd": "..."} (left|right|rotate|down|hard|reset)
#
# Sicherheit
# ----------
#   Wenn OROMA_UI_TOKEN gesetzt ist:
#     – mutierende POSTs nur mit Header X-OROMA-TOKEN: <token>
# =============================================================================

from __future__ import annotations

import os
import time
import random
from copy import deepcopy
from typing import Optional, Dict, Any, List, Tuple
from flask import Blueprint, render_template, jsonify, request

from core.tetris_engine import TetrisEngine, WIDTH, HEIGHT, Piece, TETROMINOS
import logging
from core.log_guard import log_suppressed

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

tetris_bp = Blueprint("tetris", __name__, url_prefix="/tetris", template_folder="templates")
_engine: Optional[TetrisEngine] = None

# -----------------------------------------------------------------------------
# Lightweight runtime stats (UI-facing)
# -----------------------------------------------------------------------------
# NOTE: Tetris is single-player. We still expose a stats block comparable to
# TicTacToe's "Statistik & Speicher" so /tetris/ matches the ORÓMA standard UI
# pattern (users expect a compact, persistent overview of progress and DB state).
_RT: Dict[str, Any] = {
    # NOTE: In TicTacToe the UI shows a "Spiele" counter that increments when a
    # new game starts (not only when a game ends). For Tetris we follow the same
    # expectation: "games_started" counts how many new runs were started via
    # engine init or explicit reset.
    "games_started": 0,
    "gameovers": 0,
    "best_score": 0,
    "last_gameover_ts": 0,
    # internal cache
    "_db_cache": None,
    "_db_cache_ts": 0.0,
}
_LAST_RUNNING: Optional[bool] = None

# -----------------------------------------------------------------------------
# Runtime config (UI-controlled)
# -----------------------------------------------------------------------------
_CFG: Dict[str, Any] = {
    "mode": os.environ.get("OROMA_TETRIS_MODE_DEFAULT", "explore").strip().lower() or "explore",
    "eps": float(os.environ.get("OROMA_TETRIS_EPS", "0.08") or "0.08"),
}

def _env_true(name: str, default: bool=False)->bool:
    v = os.environ.get(name, "")
    if v == "": return default
    return v.strip().lower() not in ("0","false","no","off")

def _cfg_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN","").strip()

def _extract_token() -> Optional[str]:
    h = request.headers.get("X-OROMA-TOKEN")
    if h: return h.strip()
    auth = request.headers.get("Authorization","")
    if auth.lower().startswith("bearer "): return auth[7:].strip()
    try:
        js = request.get_json(silent=True) or {}
        t = str(js.get("token","")).strip()
        if t: return t
    except Exception as e:
        log_suppressed('ui/tetris_ui.py:57', exc=e, level=logging.WARNING)
        pass
    return request.args.get("token") or request.cookies.get("OROMA_UI_TOKEN")

def _token_valid() -> bool:
    cfg = _cfg_token()
    if not cfg: return True
    return _extract_token() == cfg

def _ensure_engine() -> TetrisEngine:
    global _engine
    if _engine is None:
        _engine = TetrisEngine()
        # First engine instantiation starts a new run.
        _RT["games_started"] = int(_RT.get("games_started", 0) or 0) + 1
    return _engine


def _policy_rules_count() -> int:
    """Best effort: show policy_rules count for game:tetris if table exists."""
    if sql_manager is None:
        return 0
    try:
        db_path = sql_manager.get_db_path(None)
        with sql_manager.get_conn(db_path) as conn:
            cur = conn.cursor()
            # policy_rules is used by UniversalPolicy. If not present, this will fail and we return 0.
            cur.execute("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", ("game:tetris",))
            row = cur.fetchone()
            return int((row[0] if row else 0) or 0)
    except Exception:
        return 0


def _db_stats_cached(ttl_s: float = 2.0) -> Dict[str, int]:
    """Small cached DB stats (counts). Avoid UI stalls if DB is busy/locked."""
    now = time.time()
    cache = _RT.get("_db_cache")
    cache_ts = float(_RT.get("_db_cache_ts", 0.0) or 0.0)
    if isinstance(cache, dict) and cache_ts and (now - cache_ts) < float(ttl_s):
        return {
            "snapchains_total": int(cache.get("snapchains_total", 0) or 0),
            "policy_rules_count": int(cache.get("policy_rules_count", 0) or 0),
            "archiv_rules_count": int(cache.get("archiv_rules_count", 0) or 0),
        }

    # NOTE: TicTacToe shows the global DB-SnapChains count (all origins). Users
    # expect that same number here (not only game:tetris filtered).
    snapchains_total = 0
    prc = 0
    arc = 0
    if sql_manager is not None:
        try:
            with sql_manager.get_conn() as c:
                try:
                    c.execute("PRAGMA busy_timeout=250")
                except Exception:
                    pass

                row = c.execute("SELECT COUNT(*) AS n FROM snapchains").fetchone()
                snapchains_total = int(row["n"] if isinstance(row, dict) else (row[0] if row else 0))

                row = c.execute(
                    "SELECT COUNT(*) AS n FROM policy_rules WHERE namespace=?",
                    ("game:tetris",),
                ).fetchone()
                prc = int(row["n"] if isinstance(row, dict) else (row[0] if row else 0))

                row = c.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM rules
                    WHERE active=1
                      AND (content LIKE ? OR content LIKE ?)
                    """,
                    ('%"namespace":"game:tetris"%', '%"namespace": "game:tetris"%'),
                ).fetchone()
                arc = int(row["n"] if isinstance(row, dict) else (row[0] if row else 0))

        except Exception:
            # If DB is locked, return old cache if present.
            if isinstance(cache, dict):
                return {
                    "snapchains_total": int(cache.get("snapchains_total", 0) or 0),
                    "policy_rules_count": int(cache.get("policy_rules_count", 0) or 0),
                    "archiv_rules_count": int(cache.get("archiv_rules_count", 0) or 0),
                }

    new_cache = {
        "snapchains_total": int(snapchains_total),
        "policy_rules_count": int(prc),
        "archiv_rules_count": int(arc),
    }
    _RT["_db_cache"] = new_cache
    _RT["_db_cache_ts"] = now
    return new_cache


def _rt_on_state(st: Dict[str, Any]) -> None:
    """Update runtime stats.

    We track:
      • best_score continuously (not only at game-over),
      • gameovers on running->false edge.
    """
    global _LAST_RUNNING
    try:
        running = bool(st.get("running"))
    except Exception:
        running = False

    if _LAST_RUNNING is None:
        _LAST_RUNNING = running
        return

    # Update best score continuously (user expects to see it even before game-over).
    try:
        sc_now = int(st.get("score") or 0)
        if sc_now > int(_RT.get("best_score", 0) or 0):
            _RT["best_score"] = sc_now
    except Exception:
        pass

    if _LAST_RUNNING and (not running):
        _RT["gameovers"] = int(_RT.get("gameovers", 0) or 0) + 1
        _RT["last_gameover_ts"] = int(time.time())

    _LAST_RUNNING = running


# -----------------------------------------------------------------------------
# Simple AI (same heuristic family as daily runner)
# -----------------------------------------------------------------------------
def _board_height(board: List[List[int]]) -> int:
    for y in range(HEIGHT):
        if any(board[y][x] != -1 for x in range(WIDTH)):
            return HEIGHT - y
    return 0


def _count_holes(board: List[List[int]]) -> int:
    holes = 0
    for x in range(WIDTH):
        seen_block = False
        for y in range(HEIGHT):
            if board[y][x] != -1:
                seen_block = True
            elif seen_block:
                holes += 1
    return holes


def _place_piece_sim(board: List[List[int]], kind: str, rot: int, x: int) -> Optional[List[List[int]]]:
    p = Piece(kind=kind, rot=rot % 4, x=x, y=-2)

    def can_place(pp: Piece) -> bool:
        for (cx, cy) in pp.cells():
            if cx < 0 or cx >= WIDTH or cy >= HEIGHT:
                return False
            if cy >= 0 and board[cy][cx] != -1:
                return False
        return True

    if not can_place(p):
        return None

    while True:
        np = Piece(kind=p.kind, rot=p.rot, x=p.x, y=p.y + 1)
        if can_place(np):
            p = np
            continue
        break

    nb = deepcopy(board)
    for (cx, cy) in p.cells():
        if 0 <= cy < HEIGHT:
            nb[cy][cx] = 0

    full = [yy for yy in range(HEIGHT) if all(nb[yy][xx] != -1 for xx in range(WIDTH))]
    for yy in reversed(full):
        del nb[yy]
    for _ in full:
        nb.insert(0, [-1] * WIDTH)

    return nb


def _policy_best_placement(eng: TetrisEngine) -> Optional[Tuple[int, int]]:
    st = eng.get_state()
    cur = st.get("cur")
    if not cur or not cur.get("kind"):
        return None
    kind = str(cur["kind"])
    board = eng.board

    best: Optional[Tuple[float, int, int]] = None  # (score, rot, x)
    for rot in range(4):
        cells = TETROMINOS[kind][rot]
        min_cx = min(cx for cx, _ in cells)
        max_cx = max(cx for cx, _ in cells)
        for x in range(-min_cx, WIDTH - max_cx):
            nb = _place_piece_sim(board, kind, rot, x)
            if nb is None:
                continue
            holes = _count_holes(nb)
            height = _board_height(nb)
            score = holes * 10.0 + height
            if best is None or score < best[0]:
                best = (score, rot, x)

    if best is None:
        return None
    return (best[1], best[2])


def _apply_placement(eng: TetrisEngine, rot: int, x: int) -> int:
    cmds = 0
    for _ in range(rot % 4):
        if eng.rotate():
            cmds += 1
    st = eng.get_state()
    cur = st.get("cur")
    if cur and cur.get("x") is not None:
        cx = int(cur.get("x") or 0)
        while cx < x:
            if eng.right():
                cmds += 1
                cx += 1
            else:
                break
        while cx > x:
            if eng.left():
                cmds += 1
                cx -= 1
            else:
                break
    eng.hard_drop()
    cmds += 1
    return cmds



def _plan_cmds_for_placement(eng: TetrisEngine, rot: int, x: int, animate_down_max: int = 8) -> List[str]:
    """Build a deterministic command plan (rotate/left/right/down/hard).

    This enables UI-side animation: instead of jumping directly to the final
    placement, the browser can replay the plan step-by-step via /api/cmd.
    """
    st = eng.get_state()
    cur = st.get("cur") or {}
    cx = int(cur.get("x") or 0) if cur.get("x") is not None else 0

    cmds: List[str] = []
    for _ in range(rot % 4):
        cmds.append("rotate")

    dx = int(x) - cx
    if dx > 0:
        cmds.extend(["right"] * dx)
    elif dx < 0:
        cmds.extend(["left"] * (-dx))

    # Add a few soft drops so movement becomes visible (optional).
    try:
        gy = eng.ghost_y()
        cy = cur.get("y")
        if gy is not None and cy is not None:
            drop = max(0, int(gy) - int(cy))
            d = min(int(animate_down_max), int(drop))
            if d > 0:
                cmds.extend(["down"] * d)
    except Exception:
        pass

    cmds.append("hard")
    return cmds

@tetris_bp.before_app_request
def _guard_posts():
    if request.path.startswith("/tetris/api/") and request.method in ("POST","PUT","DELETE","PATCH"):
        if not _token_valid():
            return jsonify({"ok": False, "error": "unauthorized"}), 401

@tetris_bp.get("/")
def page():
    _ensure_engine()
    return render_template("tetris.html")

@tetris_bp.get("/api/ping")
def api_ping():
    return jsonify({"ok": True})

@tetris_bp.get("/api/state")
def api_state():
    eng = _ensure_engine()
    st = eng.get_state()
    # update runtime stats (game-over edge detection)
    _rt_on_state(st)

    dbs = _db_stats_cached(ttl_s=2.0)
    st["cfg"] = {"mode": _CFG.get("mode", "explore"), "eps": float(_CFG.get("eps", 0.08))}
    st["policy"] = {
        "enabled": True,
        "namespace": "game:tetris",
        "policy_rules_count": int(dbs.get("policy_rules_count", 0) or 0),
        "archiv_rules_count": int(dbs.get("archiv_rules_count", 0) or 0),
    }
    # TicTacToe-style UI fields
    st["stats"] = {
        "games": int(_RT.get("games_started", 0) or 0),
        "gameovers": int(_RT.get("gameovers", 0) or 0),
        "best_score": int(_RT.get("best_score", 0) or 0),
    }
    st["snaps_in_ram"] = 0
    st["snapchains_in_db"] = int(dbs.get("snapchains_total", 0) or 0)
    st["snaps_total_db"] = 0
    return jsonify(st)


@tetris_bp.post("/api/step")
def api_step():
    """Ein deterministischer Tick (Gravity). Autoplay bleibt client-side."""
    eng = _ensure_engine()
    eng.step()
    st = eng.get_state()
    _rt_on_state(st)
    st["ok"] = True
    return jsonify(st)


@tetris_bp.post("/api/apply")
def api_apply():
    """Apply lightweight UI config (mode + eps)."""
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    mode = str(data.get("mode", _CFG.get("mode", "explore"))).strip().lower() or "explore"
    if mode not in ("explore", "policy"):
        mode = "explore"
    try:
        eps = float(data.get("eps", _CFG.get("eps", 0.08)))
    except Exception:
        eps = float(_CFG.get("eps", 0.08))
    eps = max(0.0, min(1.0, eps))
    _CFG["mode"] = mode
    _CFG["eps"] = eps
    st = _ensure_engine().get_state()
    st["ok"] = True
    st["cfg"] = {"mode": mode, "eps": eps}
    return jsonify(st)


@tetris_bp.post("/api/ai_step")
def api_ai_step():
    """One AI decision: choose placement (policy or explore with eps) and hard_drop."""
    eng = _ensure_engine()
    mode = str(_CFG.get("mode", "explore"))
    eps = float(_CFG.get("eps", 0.08))
    rng = random.Random(int(time.time() * 1000) & 0xFFFFFFFF)

    placement = _policy_best_placement(eng)
    if placement is None:
        st = eng.get_state()
        st["ok"] = False
        st["error"] = "no placement"
        return jsonify(st)

    rot, x = placement
    if mode == "explore" and rng.random() < eps:
        rot = rng.randrange(0, 4)
        x = rng.randrange(0, WIDTH)

    _apply_placement(eng, rot, x)
    st = eng.get_state()
    _rt_on_state(st)
    st["ok"] = True
    st["cfg"] = {"mode": mode, "eps": eps}
    dbs = _db_stats_cached(ttl_s=2.0)
    st["policy"] = {
        "enabled": True,
        "namespace": "game:tetris",
        "policy_rules_count": int(dbs.get("policy_rules_count", 0) or 0),
        "archiv_rules_count": int(dbs.get("archiv_rules_count", 0) or 0),
    }
    st["stats"] = {
        "games": int(_RT.get("games_started", 0) or 0),
        "gameovers": int(_RT.get("gameovers", 0) or 0),
        "best_score": int(_RT.get("best_score", 0) or 0),
    }
    st["snaps_in_ram"] = 0
    st["snapchains_in_db"] = int(dbs.get("snapchains_total", 0) or 0)
    st["snaps_total_db"] = 0
    return jsonify(st)

@tetris_bp.post("/api/ai_plan")
def api_ai_plan():
    """Return a deterministic AI command plan for animation in the browser.

    The UI can replay the returned commands one-by-one via /api/cmd to make
    rotations/moves visible (instead of jumping directly to the final landing).
    """
    eng = _ensure_engine()
    mode = str(_CFG.get("mode", "explore"))
    eps = float(_CFG.get("eps", 0.08))
    rng = random.Random(int(time.time() * 1000) & 0xFFFFFFFF)

    placement = _policy_best_placement(eng)
    if placement is None:
        st = eng.get_state()
        return jsonify({"ok": False, "error": "no placement", "cfg": {"mode": mode, "eps": eps}, "state": st})

    rot, x = placement
    if mode == "explore" and rng.random() < eps:
        rot = rng.randrange(0, 4)
        x = rng.randrange(0, WIDTH)

    cmds = _plan_cmds_for_placement(eng, int(rot), int(x), animate_down_max=8)
    st = eng.get_state()
    st["cfg"] = {"mode": mode, "eps": eps}
    return jsonify({"ok": True, "cfg": {"mode": mode, "eps": eps}, "cmds": cmds, "placement": {"rot": int(rot), "x": int(x)}, "state": st})

@tetris_bp.post("/api/cmd")
def api_cmd():
    eng = _ensure_engine()
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    cmd = str(data.get("cmd","")).strip().lower()
    if not cmd:
        return jsonify({"ok": False, "error": "no cmd"}), 400

    handled = True
    if cmd in ("left","l"):      eng.left()
    elif cmd in ("right","r"):   eng.right()
    elif cmd in ("rotate","u"):  eng.rotate()
    elif cmd in ("down","d"):    eng.soft_drop()
    elif cmd in ("hard","space"): eng.hard_drop()
    elif cmd == "reset":
        eng.reset()
        # Start a new run (TicTacToe-style "Spiele" counter semantics).
        _RT["games_started"] = int(_RT.get("games_started", 0) or 0) + 1
    else:
        handled = False

    st = eng.get_state()
    _rt_on_state(st)
    st["ok"] = handled
    if not handled:
        st["error"] = f"unknown cmd: {cmd}"
        return jsonify(st), 400
    return jsonify(st)
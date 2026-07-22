#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/tictactoe_daily_runner.py
# Projekt: ORÓMA – Games / Professional State Templates
# Modul:   TicTacToe Daily Runner – pro_v2 Solver/Policy/Explore-Gap Runner
# Version: v2.0-pro_v2
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt TicTacToe automatisiert headless aus und schreibt weiterhin die üblichen
#   ORÓMA-Episoden nach episodes/episodic_metrics. Zusätzlich erzeugt dieser Runner
#   einen professionellen, endlichen Lernpfad für TicTacToe:
#
#       namespace:    game:tictactoe
#       state_schema: tictactoe:pro_v2
#       action_schema: canon_d4_9
#
#   TicTacToe ist ein vollständig lösbares endliches Spiel. Zufällige Exploration
#   ist hier nach vollständiger Zustandsabdeckung nicht nur unnötig, sondern kann
#   über Monate bloß redundante Draw-/Loss-Telemetrie erzeugen. Deshalb behandelt
#   dieser Runner "explore" als Gap-Filling über einen exakten Minimax-Solver:
#
#     • Wenn tictactoe:pro_v2 noch unvollständig ist, wird der komplette erreichbare
#       kanonische Zustandsraum mit optimalen/fehlerhaften Aktionen gelehrt.
#     • Wenn der Zustandsraum vollständig bekannt ist, setzt der Runner
#       no_more_explore=1 und schreibt keine neuen Explore-Lernitems mehr.
#
# Design für künftiges core/state_template.py
# -------------------------------------------
#   Die pro_v2-Abstraktion ist bewusst als spätere State-Template-Vorlage gebaut:
#
#     • Symmetrie:      D4-Kanonisierung des 3x3-Boards.
#     • Perspektive:    side-aware Boardwerte, eigene Steine=+1, Gegner=-1.
#     • Zustandsraum:   exakt, aber klein; deshalb ist ein kanonisches Board hier
#                       zulässig und professionell. Anders als bei Snake/Tetris
#                       entsteht keine Hash-Explosion.
#     • Aktion:         kanonischer Zellindex 0..8.
#     • Lernen:         solver-basiert und ereignis-/wertbasiert; kein neutraler
#                       Draw-Müll. Optimale Remis-Aktionen werden als "safe draw"
#                       positiv markiert, Blunder negativ.
#     • Explore-Stop:   explizite Coverage-Metriken zeigen, wann Exploration nicht
#                       mehr sinnvoll ist.
#
# DB-/Write-Disziplin
# -------------------
#   Policy-Regeln werden ausschließlich über core.db_writer_client.executemany()
#   geschrieben. Es gibt keinen lokalen SQLite-Direktwrite-Fallback für policy_rules.
#   Wenn DBWriter nicht erreichbar ist, bleibt das sichtbar:
#
#       policy_learn_ok=false, learned_items=0
#
#   Episoden/Metriken nutzen weiterhin den vorhandenen sql_manager-Episodenpfad,
#   wie die übrigen Daily Runner im Projekt.
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/tictactoe_daily_runner.py --policy-games 100 --explore-games 100
#
# ENV
# ---
#   OROMA_TTT_POLICY_ACCEPT_Q_MIN=0.15
#   OROMA_TTT_POLICY_ACCEPT_MIN_N=1
#   OROMA_TTT_POLICY_DBW_CHUNK=500
#   OROMA_TTT_SOLVER_RETEACH=0              # 1 = Solver-Teach auch bei Coverage komplett
#   OROMA_TTT_POLICY_START_MODE=x           # x|o|random|alternate
#   OROMA_TTT_DIVERSITY_MODE=optimal_seeded # fixed|optimal_seeded
#   OROMA_TTT_POLICY_Q_TIE_EPS=0.000001     # q tie window for safe optimal diversity
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore


STATE_SCHEMA = "tictactoe:pro_v2"
ACTION_SCHEMA = "canon_d4_9"
DEFAULT_NAMESPACE = "game:tictactoe"

_WINS: Tuple[Tuple[int, int, int], ...] = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)
_POS_PREF: Tuple[int, ...] = (4, 0, 2, 6, 8, 1, 3, 5, 7)


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


def _other(side: str) -> str:
    return "O" if side == "X" else "X"


def _check_winner(board: Sequence[str]) -> Optional[str]:
    for a, b, c in _WINS:
        if board[a] and board[a] == board[b] == board[c]:
            return str(board[a])
    return "draw" if all(board) else None


def _legal(board: Sequence[str]) -> List[int]:
    return [i for i, v in enumerate(board) if not v]


def _side_to_move(board: Sequence[str]) -> str:
    # Standard TicTacToe: X beginnt. Bei gültigen Zuständen gilt X==O oder X==O+1.
    x = sum(1 for v in board if v == "X")
    o = sum(1 for v in board if v == "O")
    return "X" if x <= o else "O"


# -----------------------------------------------------------------------------
# D4-Kanonisierung
# -----------------------------------------------------------------------------

def _idx(r: int, c: int) -> int:
    return r * 3 + c


def _build_d4_maps() -> Tuple[Tuple[int, ...], ...]:
    """Return maps old_index -> canonical/transformed_index for all D4 transforms."""
    maps: List[Tuple[int, ...]] = []
    funcs = (
        lambda r, c: (r, c),          # identity
        lambda r, c: (c, 2 - r),      # rot90
        lambda r, c: (2 - r, 2 - c),  # rot180
        lambda r, c: (2 - c, r),      # rot270
        lambda r, c: (r, 2 - c),      # mirror vertical
        lambda r, c: (2 - r, c),      # mirror horizontal
        lambda r, c: (c, r),          # main diagonal
        lambda r, c: (2 - c, 2 - r),  # anti diagonal
    )
    seen = set()
    for f in funcs:
        m = [0] * 9
        for r in range(3):
            for c in range(3):
                nr, nc = f(r, c)
                m[_idx(r, c)] = _idx(nr, nc)
        t = tuple(m)
        if t not in seen:
            seen.add(t)
            maps.append(t)
    return tuple(maps)


_D4_OLD_TO_NEW: Tuple[Tuple[int, ...], ...] = _build_d4_maps()


def _perspective_vec(board: Sequence[str], side: str) -> Tuple[int, ...]:
    opp = _other(side)
    out: List[int] = []
    for v in board:
        if v == side:
            out.append(1)
        elif v == opp:
            out.append(-1)
        else:
            out.append(0)
    return tuple(out)


def _canon_state(board: Sequence[str], side: str) -> Tuple[str, Tuple[int, ...], Tuple[int, ...], Tuple[int, ...]]:
    """Return (state_hash, old_to_canon, canon_to_old, canon_vec)."""
    base = _perspective_vec(board, side)
    best_vec: Optional[Tuple[int, ...]] = None
    best_map: Optional[Tuple[int, ...]] = None
    # Lexicographic max keeps own stones stable/preferred and is deterministic.
    for old_to_new in _D4_OLD_TO_NEW:
        transformed = [0] * 9
        for old_i, new_i in enumerate(old_to_new):
            transformed[new_i] = base[old_i]
        tv = tuple(transformed)
        if best_vec is None or tv > best_vec:
            best_vec = tv
            best_map = old_to_new
    assert best_vec is not None and best_map is not None
    inv = [0] * 9
    for old_i, new_i in enumerate(best_map):
        inv[new_i] = old_i
    enc = "".join("2" if x > 0 else "0" if x < 0 else "1" for x in best_vec)
    # The phase field is intentionally explicit for the future state_template registry.
    empties = best_vec.count(0)
    phase = "open" if empties >= 6 else "mid" if empties >= 3 else "end"
    sh = f"{STATE_SCHEMA}|p={phase}|b={enc}"
    return sh, tuple(best_map), tuple(inv), best_vec


# -----------------------------------------------------------------------------
# Exact Minimax Solver
# -----------------------------------------------------------------------------

def _terminal_value_for(board: Tuple[str, ...], perspective: str) -> Optional[int]:
    w = _check_winner(board)
    if w is None:
        return None
    if w == "draw":
        return 0
    return 1 if w == perspective else -1


@lru_cache(maxsize=None)
def _solve_value(board_t: Tuple[str, ...], side: str) -> int:
    tv = _terminal_value_for(board_t, side)
    if tv is not None:
        return int(tv)
    vals: List[int] = []
    for mv in _legal(board_t):
        nb = list(board_t)
        nb[mv] = side
        # Opponent's value from opponent perspective, negated for current side.
        vals.append(-_solve_value(tuple(nb), _other(side)))
    return max(vals) if vals else 0


def _move_values(board: Sequence[str], side: str) -> Dict[int, int]:
    vals: Dict[int, int] = {}
    for mv in _legal(board):
        nb = list(board)
        nb[mv] = side
        vals[int(mv)] = -_solve_value(tuple(nb), _other(side))
    return vals


def _solver_best_moves(board: Sequence[str], side: str) -> List[int]:
    """Return all exact-minimax-best legal moves in deterministic preference order.

    This helper is the core of the TicTacToe solved-game diversity policy: the
    runner may vary only among moves with identical minimax value. It never uses
    diversity to pick a known blunder. The stable preference order is still kept
    for fixed-mode operation and for deterministic fallbacks.
    """
    vals = _move_values(board, side)
    if not vals:
        return []
    best = max(vals.values())
    best_set = {int(mv) for mv, val in vals.items() if int(val) == int(best)}
    ordered = [int(mv) for mv in _POS_PREF if int(mv) in best_set]
    ordered.extend(sorted(int(mv) for mv in best_set if int(mv) not in ordered))
    return ordered


def _solver_move(board: Sequence[str], side: str) -> Optional[int]:
    best_moves = _solver_best_moves(board, side)
    return int(best_moves[0]) if best_moves else None


def _choose_solver_best(board: Sequence[str], side: str, rng: random.Random) -> Optional[int]:
    best_moves = _solver_best_moves(board, side)
    if not best_moves:
        return None
    mode = str(os.environ.get("OROMA_TTT_DIVERSITY_MODE", "optimal_seeded") or "optimal_seeded").strip().lower()
    if mode in ("0", "off", "false", "fixed", "deterministic") or len(best_moves) <= 1:
        return int(best_moves[0])
    return int(rng.choice(best_moves))


def _enumerate_reachable_states() -> List[Tuple[Tuple[str, ...], str]]:
    """Enumerate legal reachable non-terminal states from the standard empty X-start."""
    seen_boards: set[Tuple[Tuple[str, ...], str]] = set()
    out: List[Tuple[Tuple[str, ...], str]] = []

    def rec(board_t: Tuple[str, ...], side: str) -> None:
        if _check_winner(board_t) is not None:
            return
        key = (board_t, side)
        if key in seen_boards:
            return
        seen_boards.add(key)
        out.append(key)
        for mv in _legal(board_t):
            nb = list(board_t)
            nb[mv] = side
            rec(tuple(nb), _other(side))

    rec(tuple([""] * 9), "X")
    return out


def _build_solver_items() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build finite pro_v2 teaching items for all canonical solver states."""
    now = int(time.time())
    states = _enumerate_reachable_states()
    items: List[Dict[str, Any]] = []
    uniq_states: set[str] = set()
    uniq_rules: set[Tuple[str, int]] = set()
    best_items = blunder_items = safe_draw_items = forced_loss_best_items = 0
    state_value: Dict[str, int] = {}

    # Deduplicate exact canonical state/action/outcome sign to keep finite teach batches compact.
    emitted: set[Tuple[str, int, int]] = set()
    for board_t, side in states:
        sh, old_to_canon, _canon_to_old, _vec = _canon_state(board_t, side)
        uniq_states.add(sh)
        vals = _move_values(board_t, side)
        if not vals:
            continue
        best = max(vals.values())
        state_value.setdefault(sh, int(best))
        for mv, val in vals.items():
            a_canon = int(old_to_canon[int(mv)])
            is_best = int(val) == int(best)
            # We teach action ranking, not terminal score magnitude:
            #   - best action in a state is positive, including optimal draw / forced-loss defense.
            #   - suboptimal action is negative.
            outcome = 1 if is_best else -1
            ekey = (sh, a_canon, outcome)
            if ekey in emitted:
                continue
            emitted.add(ekey)
            uniq_rules.add((sh, a_canon))
            if is_best:
                best_items += 1
                if best == 0:
                    safe_draw_items += 1
                elif best < 0:
                    forced_loss_best_items += 1
            else:
                blunder_items += 1
            items.append({
                "state_hash": sh,
                "action_canon": int(a_canon),
                "outcome": float(outcome),
                "ts": now,
                "side": side,
                "solver_value": int(best),
                "state_schema": STATE_SCHEMA,
                "action_schema": ACTION_SCHEMA,
            })
    meta = {
        "solver_states_total": int(len(uniq_states)),
        "solver_rules_total": int(len(uniq_rules)),
        "solver_items_total": int(len(items)),
        "solver_best_items": int(best_items),
        "solver_blunder_items": int(blunder_items),
        "solver_safe_draw_items": int(safe_draw_items),
        "solver_forced_loss_best_items": int(forced_loss_best_items),
        "solver_value_win_states": int(sum(1 for v in state_value.values() if int(v) > 0)),
        "solver_value_draw_states": int(sum(1 for v in state_value.values() if int(v) == 0)),
        "solver_value_loss_states": int(sum(1 for v in state_value.values() if int(v) < 0)),
    }
    return items, meta


# -----------------------------------------------------------------------------
# DBWriter policy_rules batch
# -----------------------------------------------------------------------------

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
    """Aggregate items and write via DBWriter only. Returns (ok, learned_item_count, ms)."""
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
        key = (sh, action)
        row = agg.setdefault(key, {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": now})
        row["n"] += 1
        learned_count += 1
        if out > 1e-9:
            row["pos"] += 1
        elif out < -1e-9:
            row["neg"] += 1
        else:
            row["draw"] += 1
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
    chunk = max(1, _env_int("OROMA_TTT_POLICY_DBW_CHUNK", 500))
    try:
        for i in range(0, len(params), chunk):
            db_writer_client.executemany(
                sql,
                params[i:i + chunk],
                tag="tictactoe.pro_v2.policy_rules.upsert",
                priority="low",
                timeout_ms=timeout_ms,
                db="oroma",
            )
        return True, int(learned_count), round((time.time() - t0) * 1000.0, 3)
    except Exception as e:
        sys.stderr.write(f"[tictactoe_daily_runner] DBWriter policy upsert failed: {e!r}\n")
        return False, 0, round((time.time() - t0) * 1000.0, 3)


def _db_count_solver_coverage(namespace: str) -> Dict[str, int]:
    solver_items, sm = _build_solver_items()
    total_states = int(sm.get("solver_states_total", 0))
    total_rules = int(sm.get("solver_rules_total", 0))
    known_states = known_rules = 0
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                r = conn.execute(
                    "SELECT COUNT(DISTINCT state_hash) FROM policy_rules WHERE namespace=? AND state_hash LIKE ?",
                    (namespace, f"{STATE_SCHEMA}%"),
                ).fetchone()
                known_states = int(r[0] if r is not None and not hasattr(r, "keys") else (r["COUNT(DISTINCT state_hash)"] if r else 0))
                r = conn.execute(
                    "SELECT COUNT(*) FROM policy_rules WHERE namespace=? AND state_hash LIKE ?",
                    (namespace, f"{STATE_SCHEMA}%"),
                ).fetchone()
                known_rules = int(r[0] if r is not None and not hasattr(r, "keys") else (r["COUNT(*)"] if r else 0))
    except Exception:
        known_states = known_rules = 0
    return {
        "solver_states_total": total_states,
        "solver_rules_total": total_rules,
        "solver_states_known": int(known_states),
        "solver_rules_known": int(known_rules),
        "solver_states_missing": max(0, total_states - int(known_states)),
        "solver_rules_missing": max(0, total_rules - int(known_rules)),
    }


# -----------------------------------------------------------------------------
# Policy choice and gameplay benchmark
# -----------------------------------------------------------------------------

class _PolicyStats:
    def __init__(self) -> None:
        self.seen = 0
        self.accepted = 0
        self.fallback = 0
        self.rejected_n = 0
        self.rejected_q = 0


def _db_choose_policy(namespace: str, board: Sequence[str], side: str, legal: Sequence[int], stats: _PolicyStats, rng: random.Random) -> Optional[int]:
    sh, old_to_canon, canon_to_old, _vec = _canon_state(board, side)
    legal_canon = {int(old_to_canon[i]) for i in legal}
    if not legal_canon:
        return None
    q_min = _env_float("OROMA_TTT_POLICY_ACCEPT_Q_MIN", 0.15)
    n_min = _env_int("OROMA_TTT_POLICY_ACCEPT_MIN_N", 1)
    rows: List[Any] = []
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                rows = list(conn.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                    (namespace, sh),
                ).fetchall() or [])
    except Exception:
        rows = []
    candidates: List[Tuple[int, float, int]] = []
    for row in rows:
        try:
            a_raw = row["action"] if hasattr(row, "keys") else row[0]
            a = int(a_raw)
            if a not in legal_canon:
                continue
            q = float(row["q"] if hasattr(row, "keys") else row[1])
            n = int(row["n"] if hasattr(row, "keys") else row[2])
            candidates.append((a, q, n))
        except Exception:
            continue
    if not candidates:
        stats.fallback += 1
        return None
    stats.seen += 1
    candidates.sort(key=lambda t: (t[1], t[2]), reverse=True)
    eligible: List[Tuple[int, float, int]] = []
    for a, q, n in candidates:
        if n < n_min:
            continue
        if q < q_min:
            continue
        orig = int(canon_to_old[int(a)])
        if orig in legal:
            eligible.append((int(a), float(q), int(n)))
    if not eligible:
        # Keep rejection telemetry focused on the best available rule.
        a0, q0, n0 = candidates[0]
        if int(n0) < n_min:
            stats.rejected_n += 1
        elif float(q0) < q_min:
            stats.rejected_q += 1
        stats.fallback += 1
        return None

    # Only vary among equally good accepted policy rules. For solved games this
    # avoids ten visually identical optimal games while preserving perfect play.
    q_tie_eps = max(0.0, _env_float("OROMA_TTT_POLICY_Q_TIE_EPS", 0.000001))
    best_q = max(float(q) for _a, q, _n in eligible)
    tied = [row for row in eligible if float(row[1]) >= best_q - q_tie_eps]
    mode = str(os.environ.get("OROMA_TTT_DIVERSITY_MODE", "optimal_seeded") or "optimal_seeded").strip().lower()
    if mode in ("0", "off", "false", "fixed", "deterministic") or len(tied) <= 1:
        a, q, n = tied[0]
    else:
        a, q, n = rng.choice(tied)
    orig = int(canon_to_old[int(a)])
    if orig not in legal:
        stats.fallback += 1
        return None
    stats.accepted += 1
    return orig


def _start_side_for_game(game_idx: int, seed: Optional[int]) -> str:
    mode = str(os.environ.get("OROMA_TTT_POLICY_START_MODE", "x") or "x").strip().lower()
    if mode in ("o", "zero"):
        return "O"
    if mode in ("random", "rand"):
        return random.choice(["X", "O"])
    if mode in ("alternate", "alternating", "alt"):
        return "X" if (int(game_idx) % 2 == 0) else "O"
    return "X"


def _play_policy_game(namespace: str, game_idx: int, stats: _PolicyStats, rng: random.Random) -> Dict[str, Any]:
    t0 = time.time()
    board: List[str] = [""] * 9
    turn = _start_side_for_game(game_idx, None)
    moves = 0
    move_line: List[int] = []
    while _check_winner(board) is None:
        legal = _legal(board)
        if not legal:
            break
        mv = _db_choose_policy(namespace, board, turn, legal, stats, rng)
        if mv is None:
            mv = _choose_solver_best(board, turn, rng)
        if mv is None or mv not in legal:
            mv = rng.choice(list(legal))
        board[int(mv)] = turn
        move_line.append(int(mv))
        moves += 1
        if _check_winner(board) is not None:
            break
        turn = _other(turn)
    return {
        "winner": _check_winner(board) or "draw",
        "moves": int(moves),
        "line": tuple(int(x) for x in move_line),
        "opening": tuple(int(x) for x in move_line[:2]),
        "final_board": tuple(str(x or ".") for x in board),
        "duration_ms": round((time.time() - t0) * 1000.0, 3),
    }


def run_policy_batch(*, games: int, namespace: str, seed: Optional[int], source: str) -> Dict[str, Any]:
    rng = random.Random(int(seed) if seed is not None else (int(time.time()) & 0xFFFFFFFF))
    t0 = time.time()
    stats = _PolicyStats()
    wins_x = wins_o = draws = 0
    moves_sum = 0
    game_ms_sum = 0.0
    lines_seen: set[Tuple[int, ...]] = set()
    openings_seen: set[Tuple[int, ...]] = set()
    final_boards_seen: set[Tuple[str, ...]] = set()
    for gi in range(max(0, int(games))):
        g = _play_policy_game(namespace, gi, stats, rng)
        w = g["winner"]
        if w == "X":
            wins_x += 1
        elif w == "O":
            wins_o += 1
        else:
            draws += 1
        moves_sum += int(g["moves"])
        game_ms_sum += float(g["duration_ms"])
        try:
            lines_seen.add(tuple(int(x) for x in g.get("line", ())))
            openings_seen.add(tuple(int(x) for x in g.get("opening", ())))
            final_boards_seen.add(tuple(str(x) for x in g.get("final_board", ())))
        except Exception:
            pass
    dt_ms = round((time.time() - t0) * 1000.0, 3)
    n_games = max(1, int(games))
    return {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": float(dt_ms),
        "games": int(games),
        "wins_x": int(wins_x),
        "wins_o": int(wins_o),
        "draws": int(draws),
        "avg_moves": round(float(moves_sum) / n_games, 4),
        "avg_game_ms": round(float(game_ms_sum) / n_games, 3),
        "unique_lines": int(len(lines_seen)),
        "unique_openings": int(len(openings_seen)),
        "unique_final_boards": int(len(final_boards_seen)),
        "diversity_mode": str(os.environ.get("OROMA_TTT_DIVERSITY_MODE", "optimal_seeded") or "optimal_seeded"),
        "mode": "policy",
        "namespace": str(namespace),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "policy_enabled": 1.0,
        "policy_seen": int(stats.seen),
        "policy_accepted": int(stats.accepted),
        "policy_fallback": int(stats.fallback),
        "policy_rejected_n": int(stats.rejected_n),
        "policy_rejected_q": int(stats.rejected_q),
        "policy_accept_q_min": float(_env_float("OROMA_TTT_POLICY_ACCEPT_Q_MIN", 0.15)),
        "policy_accept_min_n": int(_env_int("OROMA_TTT_POLICY_ACCEPT_MIN_N", 1)),
        "learn": False,
        "learn_items": 0,
        "learned_items": 0,
        "policy_learn_ok": False,
        "source": str(source),
        "label": f"tictactoe:policy ({games} games)",
        "runner": "tools/tictactoe_daily_runner.py",
        "shim": "tools/tictactoe_daily_runner.pro_v2_solver",
    }


def run_explore_batch(*, requested_games: int, namespace: str, seed: Optional[int], source: str, allow_policy_write: bool = True) -> Dict[str, Any]:
    if seed is not None:
        random.seed(int(seed))
    t0 = time.time()
    coverage_before = _db_count_solver_coverage(namespace)
    solver_items, solver_meta = _build_solver_items()
    reteach = _env_bool("OROMA_TTT_SOLVER_RETEACH", "0")
    complete_before = int(coverage_before.get("solver_states_missing", 0)) <= 0
    no_more_explore = bool(complete_before and not reteach)

    learn_ok = False
    learned_items = 0
    learn_ms = 0.0
    effective_games = 0
    if not no_more_explore and bool(allow_policy_write):
        learn_ok, learned_items, learn_ms = _learn_policy_rules_dbw(namespace, solver_items)

    coverage_after = _db_count_solver_coverage(namespace) if learn_ok else coverage_before
    complete_after = int(coverage_after.get("solver_states_missing", 0)) <= 0
    no_more_after = bool(complete_after)
    dt_ms = round((time.time() - t0) * 1000.0, 3)

    out: Dict[str, Any] = {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": float(dt_ms),
        "games": int(effective_games),
        "requested_games": int(requested_games),
        "effective_games": int(effective_games),
        "wins_x": 0,
        "wins_o": 0,
        "draws": 0,
        "avg_moves": 0.0,
        "avg_game_ms": 0.0,
        "mode": "explore",
        "namespace": str(namespace),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "policy_enabled": 1.0,
        "eps": 0.0,
        "explore_moves_per_game": 0,
        "explore_complete": 1.0 if complete_after else 0.0,
        "no_more_explore": 1.0 if no_more_after else 0.0,
        "explore_disabled_reason": "solved_space_complete" if no_more_explore else "solver_gap_fill",
        "learn": bool((not no_more_explore) and allow_policy_write),
        "learn_items": int(len(solver_items) if ((not no_more_explore) and allow_policy_write) else 0),
        "learned_items": int(learned_items),
        "policy_learn_ok": bool(learn_ok),
        "learn_duration_ms": float(learn_ms),
        "sim_duration_ms": 0.0,
        "policy_dbw_chunk": int(_env_int("OROMA_TTT_POLICY_DBW_CHUNK", 500)),
        "source": str(source),
        "label": f"tictactoe:explore ({requested_games} requested; solver)",
        "runner": "tools/tictactoe_daily_runner.py",
        "shim": "tools/tictactoe_daily_runner.pro_v2_solver",
    }
    out.update(solver_meta)
    out.update(coverage_after)
    out["solver_states_known_before"] = int(coverage_before.get("solver_states_known", 0))
    out["solver_rules_known_before"] = int(coverage_before.get("solver_rules_known", 0))
    return out


# -----------------------------------------------------------------------------
# Episode writer
# -----------------------------------------------------------------------------

def _db_write_episode(kind: str, meta: Dict[str, Any]) -> Optional[int]:
    if not (sql_manager and hasattr(sql_manager, "insert_episode")):
        return None
    ts0 = int(meta.get("ts_start") or time.time())
    ts1 = int(meta.get("ts_end") or time.time())
    try:
        eid = sql_manager.insert_episode(
            ts_start=ts0,
            ts_end=ts1,
            kind=str(kind),
            source=str(meta.get("source") or "tictactoe_daily_runner"),
            label=str(meta.get("label") or kind),
            meta=meta,
        )
    except Exception as e:
        sys.stderr.write(f"[tictactoe_daily_runner] DB insert_episode failed ({kind}): {e!r}\n")
        eid = None
    if eid is None:
        sys.stderr.write(f"[tictactoe_daily_runner] DB episode not written ({kind})\n")
        return None
    if hasattr(sql_manager, "insert_episodic_metric"):
        ts = int(meta.get("ts_end") or time.time())
        metric_keys = (
            "games", "requested_games", "effective_games", "wins_x", "wins_o", "draws",
            "avg_moves", "duration_ms", "avg_game_ms", "unique_lines", "unique_openings", "unique_final_boards",
            "policy_enabled", "eps",
            "explore_moves_per_game", "learn_items", "learned_items", "policy_learn_ok",
            "learn_duration_ms", "sim_duration_ms", "policy_dbw_chunk",
            "policy_seen", "policy_accepted", "policy_fallback", "policy_rejected_n", "policy_rejected_q",
            "policy_accept_q_min", "policy_accept_min_n", "solver_states_total", "solver_rules_total",
            "solver_items_total", "solver_best_items", "solver_blunder_items", "solver_safe_draw_items",
            "solver_forced_loss_best_items", "solver_states_known", "solver_rules_known",
            "solver_states_missing", "solver_rules_missing", "solver_states_known_before", "solver_rules_known_before",
            "solver_value_win_states", "solver_value_draw_states", "solver_value_loss_states",
            "explore_complete", "no_more_explore",
        )
        for k in metric_keys:
            if k in meta and meta[k] is not None:
                try:
                    v = meta[k]
                    if isinstance(v, bool):
                        v = 1.0 if v else 0.0
                    sql_manager.insert_episodic_metric(int(eid), ts, str(k), float(v))
                except Exception as e:
                    sys.stderr.write(f"[tictactoe_daily_runner] DB insert_episodic_metric failed ({kind}:{k}): {e!r}\n")
    return int(eid)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA TicTacToe pro_v2 daily runner (solver coverage + policy benchmark)")
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--eps", type=float, default=None, help="kept for CLI compatibility; pro_v2 uses solver gap-fill")
    ap.add_argument("--explore-moves-per-game", type=int, default=None, help="kept for CLI compatibility; pro_v2 uses solver gap-fill")
    ap.add_argument("--learn-policy-batch", action="store_true", help="kept for compatibility; policy batch remains evaluation-only")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    source = os.environ.get("OROMA_RUN_SOURCE", "orchestrator")
    seed = args.seed if args.seed is not None else (int(time.time()) & 0xFFFFFFFF)

    meta_policy = run_policy_batch(
        games=max(0, int(args.policy_games)),
        namespace=str(args.namespace),
        seed=int(seed),
        source=source,
    )
    meta_explore = run_explore_batch(
        requested_games=max(0, int(args.explore_games)),
        namespace=str(args.namespace),
        seed=int(seed) + 1,
        source=source,
        allow_policy_write=(not bool(args.no_db)),
    )

    db_written = False
    if not args.no_db:
        eid1 = _db_write_episode("game:tictactoe:policy_batch", meta_policy)
        eid2 = _db_write_episode("game:tictactoe:explore_batch", meta_explore)
        db_written = bool(eid1 and eid2)
        if not db_written:
            sys.stderr.write("[tictactoe_daily_runner] ERROR: DB write incomplete (episodes missing).\n")
            sys.stdout.write(json.dumps({
                "ok": False,
                "db_written": False,
                "state_schema": STATE_SCHEMA,
                "policy": meta_policy,
                "explore": meta_explore,
            }, ensure_ascii=False) + "\n")
            return 2

    sys.stdout.write(json.dumps({
        "ok": True,
        "db_written": bool(db_written),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "policy": meta_policy,
        "explore": meta_explore,
    }, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

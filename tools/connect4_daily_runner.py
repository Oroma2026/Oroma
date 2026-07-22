#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/connect4_daily_runner.py
# Projekt: ORÓMA – Games / Professional State Templates
# Modul:   Connect4 Daily Runner – pro_v2 Tactical Policy/Explore Runner
# Version: v2.0-pro_v2
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt Connect4 automatisiert headless aus und schreibt weiterhin die üblichen
#   ORÓMA-Episoden nach episodes/episodic_metrics. Zusätzlich wird Connect4 von
#   einem alten, rohbrettnahen UniversalPolicy-Pfad auf einen isolierten,
#   professionellen Lernpfad gehoben:
#
#       namespace:    game:connect4
#       state_schema: connect4:pro_v2
#       action_schema: mirror_canon_col_7
#
#   Connect4 ist theoretisch gelöst, aber der vollständige Zustandsraum ist für
#   Raspberry-Pi-Edge-Betrieb viel zu groß. Anders als TicTacToe wird daher kein
#   vollständiger Solver-Gap-Fill durchgeführt. Dieser Runner verwendet stattdessen
#   eine taktische, side-relative State-Abstraktion und lernt ausschließlich aus
#   klaren Ereignissen:
#
#     • Gewinnzug gespielt                     → positives Signal
#     • gegnerischen Sofortgewinn geblockt     → positives Signal
#     • gegnerischen Sofortgewinn verpasst     → negatives Signal
#     • terminaler Sieg / terminale Niederlage → rückwirkendes Kreditfenster
#     • neutrale Draw-/Durchschnittszüge       → kein Draw-Müll in policy_rules
#
# Professionelles Connect4-Design
# -------------------------------
#   Die pro_v2-Abstraktion enthält keine vollständige 6x7-Rohposition, sondern
#   taktische Merkmale aus Sicht des aktuellen Spielers:
#
#     • Spielphase early/mid/late
#     • eigene/gegnerische Sofortgewinn-Spalten
#     • eigene/gegnerische 3er- und 2er-Bedrohungsfenster
#     • Spaltenhöhen als grobe Buckets
#     • Center-Control und Füllstand
#     • horizontale Spiegel-Kanonisierung; Aktion wird passend gespiegelt
#
#   Dadurch bleibt der Lernraum klein genug für Offline-Betrieb, aber reich genug,
#   um professionelle taktische Entscheidungen zu lernen.
#
# Policy-/Safety-Pfad
# -------------------
#   Policy-Regeln werden aus policy_rules gelesen und über Q-/N-Gates akzeptiert.
#   Zusätzlich schützt ein Safety-Guard vor direkten taktischen Fehlgriffen: Wenn
#   ein Policy-Zug dem Gegner einen sofortigen Gewinn erlaubt und eine sichere
#   Alternative existiert, wird der Policy-Zug verworfen und der Fallback übernimmt.
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
# Explore-Disziplin
# -----------------
#   Connect4 wird nicht vollständig gelöst. Deshalb wird Exploration nie hart wie
#   bei TicTacToe beendet. Wenn eine konfigurierbare Mindestabdeckung erreicht ist,
#   reduziert der Runner Exploration auf Sparmodus, setzt aber no_more_explore=0.
#
# ENV
# ---
#   OROMA_C4_POLICY_NAMESPACE=game:connect4
#   OROMA_C4_EPS=0.08
#   OROMA_C4_EXPLORE_MOVES_PER_GAME=1
#   OROMA_C4_POLICY_ACCEPT_Q_MIN=0.20
#   OROMA_C4_POLICY_ACCEPT_MIN_N=2
#   OROMA_C4_POLICY_DBW_CHUNK=500
#   OROMA_C4_WIN_CREDIT_STEPS=12
#   OROMA_C4_LOSS_CREDIT_STEPS=12
#   OROMA_C4_EXPLORE_REDUCE_RULES=25000
#   OROMA_C4_EXPLORE_REDUCED_EPS=0.02
#   OROMA_C4_EXPLORE_REDUCED_MOVES_PER_GAME=0
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/connect4_daily_runner.py --policy-games 100 --explore-games 100
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore


ROWS, COLS = 6, 7
CONNECT_N = 4
STATE_SCHEMA = "connect4:pro_v2"
ACTION_SCHEMA = "mirror_canon_col_7"
DEFAULT_NAMESPACE = "game:connect4"
CENTER_ORDER: Tuple[int, ...] = (3, 2, 4, 1, 5, 0, 6)


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


def _bucket_signed(value: int, neg: int, pos: int) -> str:
    v = int(value)
    if v <= -abs(int(neg)):
        return "neg2"
    if v < 0:
        return "neg1"
    if v == 0:
        return "eq"
    if v >= abs(int(pos)):
        return "pos2"
    return "pos1"


# -----------------------------------------------------------------------------
# Connect4 board mechanics
# -----------------------------------------------------------------------------

def new_board() -> List[List[int]]:
    return [[0] * COLS for _ in range(ROWS)]


def clone_board(board: Sequence[Sequence[int]]) -> List[List[int]]:
    return [[int(v) for v in row] for row in board]


def legal_actions(board: Sequence[Sequence[int]]) -> List[int]:
    return [c for c in range(COLS) if int(board[0][c]) == 0]


def _center_priority(cols: Iterable[int]) -> List[int]:
    order = {c: i for i, c in enumerate(CENTER_ORDER)}
    return sorted([int(c) for c in cols], key=lambda c: order.get(int(c), 99))


def apply_action(board: List[List[int]], col: int, side: int) -> bool:
    c = int(col)
    if not (0 <= c < COLS):
        return False
    if int(board[0][c]) != 0:
        return False
    for r in range(ROWS - 1, -1, -1):
        if int(board[r][c]) == 0:
            board[r][c] = int(side)
            return True
    return False


def _drop_row(board: Sequence[Sequence[int]], col: int) -> Optional[int]:
    c = int(col)
    if not (0 <= c < COLS):
        return None
    if int(board[0][c]) != 0:
        return None
    for r in range(ROWS - 1, -1, -1):
        if int(board[r][c]) == 0:
            return int(r)
    return None


def check_winner(board: Sequence[Sequence[int]]) -> int:
    dirs = ((1, 0), (0, 1), (1, 1), (1, -1))
    for r in range(ROWS):
        for c in range(COLS):
            start = int(board[r][c])
            if start == 0:
                continue
            for dr, dc in dirs:
                ok = True
                for k in range(1, CONNECT_N):
                    rr = r + dr * k
                    cc = c + dc * k
                    if not (0 <= rr < ROWS and 0 <= cc < COLS):
                        ok = False
                        break
                    if int(board[rr][cc]) != start:
                        ok = False
                        break
                if ok:
                    return int(start)
    return 0


def is_draw(board: Sequence[Sequence[int]]) -> bool:
    return all(int(board[0][c]) != 0 for c in range(COLS))


def _piece_count(board: Sequence[Sequence[int]]) -> int:
    return sum(1 for r in range(ROWS) for c in range(COLS) if int(board[r][c]) != 0)


def _column_heights(board: Sequence[Sequence[int]], mirror: bool = False) -> List[int]:
    heights: List[int] = []
    for out_c in range(COLS):
        c = (COLS - 1 - out_c) if mirror else out_c
        filled = sum(1 for r in range(ROWS) if int(board[r][c]) != 0)
        heights.append(int(filled))
    return heights


def _mirror_col(col: int) -> int:
    return COLS - 1 - int(col)


# -----------------------------------------------------------------------------
# Tactical analysis / state abstraction
# -----------------------------------------------------------------------------

def _winning_moves(board: Sequence[Sequence[int]], side: int) -> Set[int]:
    wins: Set[int] = set()
    for c in legal_actions(board):
        b = clone_board(board)
        if apply_action(b, int(c), int(side)) and check_winner(b) == int(side):
            wins.add(int(c))
    return wins


def _iter_windows() -> Iterable[Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int], Tuple[int, int]]]:
    dirs = ((0, 1), (1, 0), (1, 1), (1, -1))
    for r in range(ROWS):
        for c in range(COLS):
            for dr, dc in dirs:
                cells: List[Tuple[int, int]] = []
                for k in range(CONNECT_N):
                    rr = r + dr * k
                    cc = c + dc * k
                    if not (0 <= rr < ROWS and 0 <= cc < COLS):
                        cells = []
                        break
                    cells.append((rr, cc))
                if len(cells) == CONNECT_N:
                    yield tuple(cells)  # type: ignore[misc]


def _line_potential_counts(board: Sequence[Sequence[int]], side: int) -> Tuple[int, int]:
    """Return (open3_count, open2_count) for side without opponent stones."""
    open3 = 0
    open2 = 0
    for cells in _iter_windows():
        vals = [int(board[r][c]) for r, c in cells]
        own = vals.count(int(side))
        empty = vals.count(0)
        opp = vals.count(-int(side))
        if opp != 0:
            continue
        if own == 3 and empty == 1:
            open3 += 1
        elif own == 2 and empty == 2:
            open2 += 1
    return int(open3), int(open2)


def _mask_for_cols(cols: Set[int], mirror: bool) -> str:
    bits: List[str] = []
    for out_c in range(COLS):
        c = _mirror_col(out_c) if mirror else out_c
        bits.append("1" if int(c) in cols else "0")
    return "".join(bits)


def _height_profile(board: Sequence[Sequence[int]], mirror: bool) -> str:
    # 0=leer, 1=1-2, 2=3-4, 3=5-6. Sehr klein, aber taktisch nützlich.
    chars: List[str] = []
    for h in _column_heights(board, mirror=mirror):
        if h <= 0:
            chars.append("0")
        elif h <= 2:
            chars.append("1")
        elif h <= 4:
            chars.append("2")
        else:
            chars.append("3")
    return "".join(chars)


def _phase_for_moves(moves: int) -> str:
    m = int(moves)
    if m < 12:
        return "early"
    if m < 28:
        return "mid"
    return "late"


def _abstract_parts(board: Sequence[Sequence[int]], side: int, mirror: bool) -> str:
    moves = _piece_count(board)
    own_wins = _winning_moves(board, int(side))
    opp_wins = _winning_moves(board, -int(side))
    own_open3, own_open2 = _line_potential_counts(board, int(side))
    opp_open3, opp_open2 = _line_potential_counts(board, -int(side))
    heights = _column_heights(board, mirror=mirror)
    center_col = 3
    center_balance = sum(1 if int(board[r][center_col]) == int(side) else -1 if int(board[r][center_col]) == -int(side) else 0 for r in range(ROWS))
    left_fill = sum(heights[0:2])
    mid_fill = sum(heights[2:5])
    right_fill = sum(heights[5:7])
    # left/right are intentionally mirror-dependent; canonicalization chooses the
    # lexicographically smaller tactical view and mirrors the action with it.
    parts = [
        STATE_SCHEMA,
        f"ph:{_phase_for_moves(moves)}",
        f"ow:{_bucket_int(len(own_wins), (0, 1))}",
        f"pw:{_bucket_int(len(opp_wins), (0, 1))}",
        f"owm:{_mask_for_cols(own_wins, mirror)}",
        f"pwm:{_mask_for_cols(opp_wins, mirror)}",
        f"o3:{_bucket_int(own_open3, (0, 1, 2))}",
        f"p3:{_bucket_int(opp_open3, (0, 1, 2))}",
        f"o2:{_bucket_int(own_open2, (0, 2, 5))}",
        f"p2:{_bucket_int(opp_open2, (0, 2, 5))}",
        f"h:{_height_profile(board, mirror)}",
        f"cf:{_bucket_int(sum(heights), (10, 24, 36))}",
        f"cc:{_bucket_signed(center_balance, 2, 2)}",
        f"lm:{_bucket_signed(left_fill - mid_fill, 4, 4)}",
        f"rm:{_bucket_signed(right_fill - mid_fill, 4, 4)}",
    ]
    return "|".join(parts)


def state_hash(board: Sequence[Sequence[int]], side: int) -> Tuple[str, bool]:
    normal = _abstract_parts(board, int(side), mirror=False)
    mirrored = _abstract_parts(board, int(side), mirror=True)
    if mirrored < normal:
        return mirrored, True
    return normal, False


def _canon_action(action: int, mirrored: bool) -> int:
    return _mirror_col(int(action)) if bool(mirrored) else int(action)


def _uncanon_action(action: int, mirrored: bool) -> int:
    return _mirror_col(int(action)) if bool(mirrored) else int(action)


def _move_creates_own_fork(board: Sequence[Sequence[int]], col: int, side: int) -> int:
    b = clone_board(board)
    if not apply_action(b, int(col), int(side)):
        return 0
    if check_winner(b) == int(side):
        return 99
    return len(_winning_moves(b, int(side)))


def _move_allows_opp_win(board: Sequence[Sequence[int]], col: int, side: int) -> bool:
    b = clone_board(board)
    if not apply_action(b, int(col), int(side)):
        return True
    if check_winner(b) == int(side):
        return False
    return len(_winning_moves(b, -int(side))) > 0


def _safe_alternatives(board: Sequence[Sequence[int]], side: int, legal: Sequence[int]) -> List[int]:
    out: List[int] = []
    for c in legal:
        if not _move_allows_opp_win(board, int(c), int(side)):
            out.append(int(c))
    return _center_priority(out)


# -----------------------------------------------------------------------------
# Professional fallback and policy gate
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
class ThreatStats:
    own_win_available: int = 0
    opp_win_available: int = 0
    win_moves_played: int = 0
    blocks_played: int = 0
    missed_blocks: int = 0


def _fallback_action(board: Sequence[Sequence[int]], side: int, legal: Sequence[int], rng: random.Random) -> int:
    legal_centered = _center_priority(legal)
    own_wins = _winning_moves(board, int(side))
    if own_wins:
        return _center_priority(own_wins)[0]
    opp_wins = _winning_moves(board, -int(side))
    blockers = [c for c in legal_centered if c in opp_wins]
    if blockers:
        return blockers[0]

    best_score = -10**9
    best_cols: List[int] = []
    safe_cols = set(_safe_alternatives(board, int(side), legal_centered))
    for c in legal_centered:
        b = clone_board(board)
        if not apply_action(b, int(c), int(side)):
            continue
        own_fork = len(_winning_moves(b, int(side)))
        opp_reply_wins = len(_winning_moves(b, -int(side))) if check_winner(b) == 0 else 0
        own3, own2 = _line_potential_counts(b, int(side))
        opp3, _opp2 = _line_potential_counts(b, -int(side))
        drop = _drop_row(board, int(c))
        center_bonus = 6 - abs(3 - int(c)) * 2
        height_bonus = 0 if drop is None else max(0, ROWS - int(drop))
        score = 0
        score += center_bonus * 6
        score += own_fork * 70
        score += own3 * 7
        score += own2 * 2
        score -= opp_reply_wins * 120
        score -= opp3 * 5
        score += height_bonus
        if int(c) in safe_cols:
            score += 20
        if score > best_score:
            best_score = int(score)
            best_cols = [int(c)]
        elif score == best_score:
            best_cols.append(int(c))
    if not best_cols:
        return int(rng.choice(legal_centered))
    # Center-priority is deterministic; randomize only exact-tie tactical score to
    # avoid artificial left/right drift in long selfplay.
    best_cols = _center_priority(best_cols)
    if len(best_cols) == 1:
        return best_cols[0]
    return int(rng.choice(best_cols))


def _db_choose_policy(namespace: str, board: Sequence[Sequence[int]], side: int, legal: Sequence[int], stats: PolicyStats) -> Optional[int]:
    sh, mirrored = state_hash(board, int(side))
    legal_canon = {_canon_action(int(c), mirrored) for c in legal}
    if not legal_canon:
        stats.fallback += 1
        return None

    q_min = _env_float("OROMA_C4_POLICY_ACCEPT_Q_MIN", 0.20)
    n_min = _env_int("OROMA_C4_POLICY_ACCEPT_MIN_N", 2)
    rows: List[Any] = []
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                rows = list(conn.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                    (str(namespace), str(sh)),
                ).fetchall() or [])
    except Exception as e:
        sys.stderr.write(f"[connect4_daily_runner] policy read failed: {e!r}\n")
        rows = []

    candidates: List[Tuple[int, float, int]] = []
    for row in rows:
        try:
            a_raw = row["action"] if hasattr(row, "keys") else row[0]
            q_raw = row["q"] if hasattr(row, "keys") else row[1]
            n_raw = row["n"] if hasattr(row, "keys") else row[2]
            a = int(a_raw)
            if a not in legal_canon:
                continue
            candidates.append((int(a), float(q_raw), int(n_raw)))
        except Exception:
            continue

    if not candidates:
        stats.fallback += 1
        return None

    stats.seen += 1
    candidates.sort(key=lambda t: (float(t[1]), int(t[2])), reverse=True)
    eligible: List[Tuple[int, float, int]] = []
    for a, q, n in candidates:
        if int(n) < n_min:
            continue
        if float(q) < q_min:
            continue
        orig = _uncanon_action(int(a), mirrored)
        if orig in legal:
            eligible.append((int(a), float(q), int(n)))

    if not eligible:
        _a0, q0, n0 = candidates[0]
        if int(n0) < n_min:
            stats.rejected_n += 1
        elif float(q0) < q_min:
            stats.rejected_q += 1
        stats.fallback += 1
        return None

    for a, _q, _n in eligible:
        orig = _uncanon_action(int(a), mirrored)
        if _move_allows_opp_win(board, int(orig), int(side)):
            if _safe_alternatives(board, int(side), legal):
                stats.rejected_unsafe += 1
                continue
        stats.accepted += 1
        return int(orig)

    stats.fallback += 1
    return None


# -----------------------------------------------------------------------------
# Event learning and DBWriter batch path
# -----------------------------------------------------------------------------

@dataclass
class TraceStep:
    state_hash: str
    action_canon: int
    side: int
    chosen_col: int
    own_win_available: bool
    opp_win_available: bool
    win_move: bool
    block_move: bool
    missed_block: bool
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

    Returns (ok, learned_item_count, duration_ms). No SQLite direct-write fallback is
    allowed here because policy_rules belongs to the managed ORÓMA DBWriter path.
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
        # Draw/neutral items are intentionally not generated by this runner. If a
        # caller accidentally passes zero, keep it out to avoid resurrecting a draw
        # wall in connect4:pro_v2.
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
    chunk = max(1, _env_int("OROMA_C4_POLICY_DBW_CHUNK", 500))
    try:
        for i in range(0, len(params), chunk):
            db_writer_client.executemany(
                sql,
                params[i:i + chunk],
                tag="connect4.pro_v2.policy_rules.upsert",
                priority="low",
                timeout_ms=timeout_ms,
                db="oroma",
            )
        return True, int(learned_count), round((time.time() - t0) * 1000.0, 3)
    except Exception as e:
        sys.stderr.write(f"[connect4_daily_runner] DBWriter policy upsert failed: {e!r}\n")
        return False, 0, round((time.time() - t0) * 1000.0, 3)


def _learn_items_from_trace(trace: Sequence[TraceStep], winner: int, ts: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    items: List[Dict[str, Any]] = []
    meta = {
        "learn_items": 0,
        "win_move_credit_items": 0,
        "block_credit_items": 0,
        "missed_block_credit_items": 0,
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
            "side": "X" if int(step.side) == 1 else "O",
            "state_schema": STATE_SCHEMA,
            "action_schema": ACTION_SCHEMA,
            "credit_kind": str(kind),
        })
        meta["learn_items"] += 1
        if kind == "win_move":
            meta["win_move_credit_items"] += 1
        elif kind == "block":
            meta["block_credit_items"] += 1
        elif kind == "missed_block":
            meta["missed_block_credit_items"] += 1
        elif kind == "terminal":
            meta["terminal_credit_items"] += 1

    for st in trace:
        if st.win_move:
            add(st, 1.0, "win_move")
        if st.block_move:
            add(st, 0.70, "block")
        if st.missed_block:
            add(st, -1.0, "missed_block")

    if int(winner) != 0:
        win_n = max(0, _env_int("OROMA_C4_WIN_CREDIT_STEPS", 12))
        loss_n = max(0, _env_int("OROMA_C4_LOSS_CREDIT_STEPS", 12))
        winner_steps = [st for st in trace if int(st.side) == int(winner)]
        loser_steps = [st for st in trace if int(st.side) == -int(winner)]
        for st in winner_steps[-win_n:]:
            add(st, 1.0, "terminal")
        for st in loser_steps[-loss_n:]:
            add(st, -1.0, "terminal")

    return items, meta


# -----------------------------------------------------------------------------
# Coverage / snapchain / episode DB utilities
# -----------------------------------------------------------------------------

def _db_pro_coverage(namespace: str) -> Dict[str, int]:
    known_states = known_rules = known_n = 0
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                r = conn.execute(
                    "SELECT COUNT(DISTINCT state_hash), COUNT(*), COALESCE(SUM(n),0) "
                    "FROM policy_rules WHERE namespace=? AND state_hash LIKE ?",
                    (str(namespace), f"{STATE_SCHEMA}%"),
                ).fetchone()
                if r is not None:
                    known_states = int(r[0] if not hasattr(r, "keys") else r["COUNT(DISTINCT state_hash)"])
                    known_rules = int(r[1] if not hasattr(r, "keys") else r["COUNT(*)"])
                    known_n = int(r[2] if not hasattr(r, "keys") else r["COALESCE(SUM(n),0)"])
    except Exception:
        known_states = known_rules = known_n = 0
    return {
        "pro_states_known": int(known_states),
        "pro_rules_known": int(known_rules),
        "pro_samples_known": int(known_n),
    }


def _build_trace_chain(namespace: str, mode: str, trace: Sequence[TraceStep], winner: int, moves: int) -> Optional[Dict[str, Any]]:
    if not trace:
        return None
    steps: List[Dict[str, Any]] = []
    first = trace[0]
    steps.append({
        "t": 0,
        "state_hash": str(first.state_hash),
        "sh": str(first.state_hash),
        "side": "X" if int(first.side) == 1 else "O",
        "mode": str(mode),
        "ply": 0,
        "state_schema": STATE_SCHEMA,
    })
    for idx in range(1, len(trace)):
        cur = trace[idx]
        prev = trace[idx - 1]
        steps.append({
            "t": int(idx),
            "state_hash": str(cur.state_hash),
            "sh": str(cur.state_hash),
            "a": int(prev.action_canon),
            "side": "X" if int(cur.side) == 1 else "O",
            "mode": str(mode),
            "ply": int(idx),
            "state_schema": STATE_SCHEMA,
        })
    last = trace[-1]
    terminal = "x_win" if int(winner) == 1 else "o_win" if int(winner) == -1 else "draw"
    steps.append({
        "t": int(len(trace)),
        "state_hash": f"{STATE_SCHEMA}|terminal:{terminal}:moves={int(moves)}",
        "sh": f"{STATE_SCHEMA}|terminal:{terminal}:moves={int(moves)}",
        "a": int(last.action_canon),
        "side": "X" if int(last.side) == 1 else "O",
        "mode": str(mode),
        "ply": int(moves),
        "terminal": str(terminal),
        "state_schema": STATE_SCHEMA,
    })
    quality = 1.0 if int(winner) == 1 else (-1.0 if int(winner) == -1 else 0.0)
    return {
        "schema_version": "connect4:pro_v2",
        "kind": "connect4_pro_v2_policy_trace",
        "origin": str(namespace or DEFAULT_NAMESPACE),
        "namespace": str(namespace or DEFAULT_NAMESPACE),
        "mode": str(mode),
        "result": float(quality),
        "moves_total": int(moves),
        "steps_total": int(max(0, len(steps) - 1)),
        "steps": steps,
        "meta": {
            "runner": "tools/connect4_daily_runner.py",
            "source": "connect4_daily_runner",
            "winner": int(winner),
            "state_schema": STATE_SCHEMA,
            "action_schema": ACTION_SCHEMA,
            "reward_mode": "event_credit_no_draw_wall",
        },
    }


def _write_snapchains(namespace: str, mode: str, chains: Sequence[Dict[str, Any]], ts_now: int) -> int:
    if not (sql_manager and hasattr(sql_manager, "insert_snapchain")):
        return 0
    written = 0
    for idx, chain in enumerate(chains or [], start=1):
        try:
            steps = chain.get("steps") or []
            if not isinstance(steps, list) or len(steps) < 2:
                continue
            blob = json.dumps(chain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            sid = sql_manager.insert_snapchain({
                "ts": int(ts_now),
                "quality": float(chain.get("result", 0.0) or 0.0),
                "blob": blob,
                "exported": 0,
                "status": "active",
                "origin": str(namespace or DEFAULT_NAMESPACE),
                "gap_flag": 0,
                "notes": f"connect4_pro_v2_daily:{mode}:steps={max(0, len(steps)-1)}",
                "namespace": str(namespace or DEFAULT_NAMESPACE),
                "source_id": None,
                "version": "connect4_daily_runner:pro_v2",
                "weight": 1.0,
            })
            if sid:
                written += 1
        except Exception as e:
            sys.stderr.write(f"[connect4_daily_runner] snapchain write failed #{idx}: {e!r}\n")
    return int(written)


def _write_episode(kind: str, label: str, payload: Dict[str, Any], metrics: Dict[str, float]) -> bool:
    if not (sql_manager and hasattr(sql_manager, "insert_episode")):
        return False
    ts_start = int(payload.get("ts_start") or _now_ts())
    ts_end = int(payload.get("ts_end") or ts_start)
    try:
        eid = sql_manager.insert_episode(
            ts_start=ts_start,
            ts_end=ts_end,
            kind=str(kind),
            source=str(payload.get("source") or "connect4_daily_runner"),
            label=str(label or payload.get("label") or kind),
            meta=dict(payload or {}),
        )
    except Exception as e:
        sys.stderr.write(f"[connect4_daily_runner] DB insert_episode failed ({kind}): {e!r}\n")
        eid = None
    if eid is None:
        sys.stderr.write(f"[connect4_daily_runner] DB episode not written ({kind})\n")
        return False
    if hasattr(sql_manager, "insert_episodic_metric"):
        for k, v in (metrics or {}).items():
            try:
                sql_manager.insert_episodic_metric(int(eid), int(ts_end), str(k), float(v))
            except Exception as e:
                sys.stderr.write(f"[connect4_daily_runner] DB insert_episodic_metric failed ({kind}:{k}): {e!r}\n")
    return True


# -----------------------------------------------------------------------------
# Batch runner
# -----------------------------------------------------------------------------

@dataclass
class BatchResult:
    ts_start: int
    ts_end: int
    duration_ms: int
    games: int
    wins_x: int
    wins_o: int
    draws: int
    avg_moves: float
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
    own_win_available: int
    opp_win_available: int
    win_moves_played: int
    blocks_played: int
    missed_blocks: int
    win_move_credit_items: int
    block_credit_items: int
    missed_block_credit_items: int
    terminal_credit_items: int
    pro_states_known_before: int
    pro_rules_known_before: int
    pro_samples_known_before: int
    pro_states_known: int
    pro_rules_known: int
    pro_samples_known: int
    source: str
    label: str
    runner: str
    shim: str
    chains_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def run_batch(
    rng: random.Random,
    namespace: str,
    games: int,
    mode: str,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    source: str,
    coverage_before: Dict[str, int],
) -> BatchResult:
    ts_start = _now_ts()
    t0 = time.perf_counter()
    pol_stats = PolicyStats()
    thr_stats = ThreatStats()

    wins_x = wins_o = draws = total_moves = 0
    all_items: List[Dict[str, Any]] = []
    credit_meta_total = {
        "learn_items": 0,
        "win_move_credit_items": 0,
        "block_credit_items": 0,
        "missed_block_credit_items": 0,
        "terminal_credit_items": 0,
    }
    chains: List[Dict[str, Any]] = []

    for _gi in range(max(0, int(games))):
        board = new_board()
        side = 1
        moves = 0
        explore_used = 0
        trace: List[TraceStep] = []

        while True:
            legal = _center_priority(legal_actions(board))
            if not legal:
                draws += 1
                break

            own_wins = _winning_moves(board, int(side))
            opp_wins = _winning_moves(board, -int(side))
            if own_wins:
                thr_stats.own_win_available += 1
            if opp_wins:
                thr_stats.opp_win_available += 1

            do_rand = False
            if mode == "explore":
                if explore_used < max(0, int(explore_moves_per_game)):
                    do_rand = True
                elif rng.random() < max(0.0, float(eps)):
                    do_rand = True

            if do_rand:
                a = int(rng.choice(legal))
                explore_used += 1
            else:
                a = _db_choose_policy(namespace, board, int(side), legal, pol_stats)
                if a is None:
                    a = _fallback_action(board, int(side), legal, rng)

            sh, mirrored = state_hash(board, int(side))
            a_canon = _canon_action(int(a), mirrored)
            win_move = int(a) in own_wins
            block_move = (not win_move) and (int(a) in opp_wins)
            missed_block = bool(opp_wins and int(a) not in opp_wins and not win_move)
            if win_move:
                thr_stats.win_moves_played += 1
            if block_move:
                thr_stats.blocks_played += 1
            if missed_block:
                thr_stats.missed_blocks += 1
            trace.append(TraceStep(
                state_hash=str(sh),
                action_canon=int(a_canon),
                side=int(side),
                chosen_col=int(a),
                own_win_available=bool(own_wins),
                opp_win_available=bool(opp_wins),
                win_move=bool(win_move),
                block_move=bool(block_move),
                missed_block=bool(missed_block),
                ts=int(ts_start),
            ))

            ok = apply_action(board, int(a), int(side))
            if not ok:
                # Should not happen because legal was checked. Treat as opponent win
                # signal through terminal handling by assigning the other side.
                w = -int(side)
                if w == 1:
                    wins_x += 1
                else:
                    wins_o += 1
                break

            moves += 1
            w = check_winner(board)
            if w != 0:
                if w == 1:
                    wins_x += 1
                else:
                    wins_o += 1
                break
            if is_draw(board):
                draws += 1
                break
            side *= -1

        total_moves += int(moves)
        winner = check_winner(board)
        if trace:
            chain = _build_trace_chain(namespace, mode, trace, int(winner), int(moves))
            if chain is not None:
                chains.append(chain)
        if learn and trace:
            items, meta = _learn_items_from_trace(trace, int(winner), int(ts_start))
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
    written_chains = _write_snapchains(namespace=namespace, mode=mode, chains=chains, ts_now=ts_end)
    t1 = time.perf_counter()

    coverage_after = _db_pro_coverage(namespace)
    dur_ms = int(round((t1 - t0) * 1000.0))
    sim_ms = round((sim_end - t0) * 1000.0, 3)
    avg_moves = (float(total_moves) / float(games)) if games > 0 else 0.0
    avg_game_ms = (float(dur_ms) / float(games)) if games > 0 else 0.0
    label = f"connect4:{mode} ({games} games)"

    return BatchResult(
        ts_start=int(ts_start),
        ts_end=int(ts_end),
        duration_ms=int(dur_ms),
        games=int(games),
        wins_x=int(wins_x),
        wins_o=int(wins_o),
        draws=int(draws),
        avg_moves=float(round(avg_moves, 4)),
        avg_game_ms=float(round(avg_game_ms, 4)),
        mode=str(mode),
        namespace=str(namespace),
        state_schema=STATE_SCHEMA,
        action_schema=ACTION_SCHEMA,
        policy_enabled=1.0,
        eps=float(eps),
        explore_moves_per_game=int(explore_moves_per_game),
        explore_reduced=1.0 if (mode == "explore" and (float(eps) < _env_float("OROMA_C4_EPS", 0.08) or int(explore_moves_per_game) < _env_int("OROMA_C4_EXPLORE_MOVES_PER_GAME", 1))) else 0.0,
        no_more_explore=0.0,
        learn=bool(learn),
        learn_items=int(len(all_items)),
        learned_items=int(learned_items),
        policy_learn_ok=bool(learn_ok),
        learn_duration_ms=float(learn_ms),
        sim_duration_ms=float(sim_ms),
        policy_dbw_chunk=max(1, _env_int("OROMA_C4_POLICY_DBW_CHUNK", 500)),
        policy_seen=int(pol_stats.seen),
        policy_accepted=int(pol_stats.accepted),
        policy_fallback=int(pol_stats.fallback),
        policy_rejected_n=int(pol_stats.rejected_n),
        policy_rejected_q=int(pol_stats.rejected_q),
        policy_rejected_unsafe=int(pol_stats.rejected_unsafe),
        own_win_available=int(thr_stats.own_win_available),
        opp_win_available=int(thr_stats.opp_win_available),
        win_moves_played=int(thr_stats.win_moves_played),
        blocks_played=int(thr_stats.blocks_played),
        missed_blocks=int(thr_stats.missed_blocks),
        win_move_credit_items=int(credit_meta_total["win_move_credit_items"]),
        block_credit_items=int(credit_meta_total["block_credit_items"]),
        missed_block_credit_items=int(credit_meta_total["missed_block_credit_items"]),
        terminal_credit_items=int(credit_meta_total["terminal_credit_items"]),
        pro_states_known_before=int(coverage_before.get("pro_states_known", 0)),
        pro_rules_known_before=int(coverage_before.get("pro_rules_known", 0)),
        pro_samples_known_before=int(coverage_before.get("pro_samples_known", 0)),
        pro_states_known=int(coverage_after.get("pro_states_known", 0)),
        pro_rules_known=int(coverage_after.get("pro_rules_known", 0)),
        pro_samples_known=int(coverage_after.get("pro_samples_known", 0)),
        source=str(source),
        label=str(label),
        runner="tools/connect4_daily_runner.py",
        shim="tools/connect4_daily_runner.pro_v2_tactical",
        chains_count=int(written_chains),
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


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Connect4 daily runner (pro_v2 tactical policy + explore)")
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--namespace", type=str, default=os.environ.get("OROMA_C4_POLICY_NAMESPACE", DEFAULT_NAMESPACE))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--once", action="store_true", help="Compatibility flag (no-op; runner always runs once)")
    args = ap.parse_args()

    seed = int(args.seed or 0)
    rng = random.Random(seed if seed != 0 else int(time.time()))
    namespace = (args.namespace or DEFAULT_NAMESPACE).strip()

    eps_base = _env_float("OROMA_C4_EPS", 0.08)
    explore_moves_base = _env_int("OROMA_C4_EXPLORE_MOVES_PER_GAME", 1)
    reduce_rules = max(0, _env_int("OROMA_C4_EXPLORE_REDUCE_RULES", 25000))
    coverage0 = _db_pro_coverage(namespace)
    explore_reduced = int(coverage0.get("pro_rules_known", 0)) >= reduce_rules if reduce_rules > 0 else False
    eps_explore = _env_float("OROMA_C4_EXPLORE_REDUCED_EPS", 0.02) if explore_reduced else eps_base
    explore_moves = _env_int("OROMA_C4_EXPLORE_REDUCED_MOVES_PER_GAME", 0) if explore_reduced else explore_moves_base

    policy_res = run_batch(
        rng=rng,
        namespace=namespace,
        games=max(0, int(args.policy_games)),
        mode="policy",
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        source="orchestrator",
        coverage_before=coverage0,
    )

    # Refresh coverage after policy episode writes/snapchains are irrelevant for
    # policy_rules, but this keeps telemetry explicit and stable.
    coverage1 = _db_pro_coverage(namespace)
    explore_res = run_batch(
        rng=rng,
        namespace=namespace,
        games=max(0, int(args.explore_games)),
        mode="explore",
        eps=float(eps_explore),
        explore_moves_per_game=max(0, int(explore_moves)),
        learn=True,
        source="orchestrator",
        coverage_before=coverage1,
    )

    ok1 = _write_episode(
        kind="game:connect4:policy_batch",
        label=policy_res.label,
        payload=policy_res.to_dict(),
        metrics=_metrics_from_result(policy_res),
    )
    ok2 = _write_episode(
        kind="game:connect4:explore_batch",
        label=explore_res.label,
        payload=explore_res.to_dict(),
        metrics=_metrics_from_result(explore_res),
    )

    out = {
        "ok": bool(ok1 and ok2),
        "db_written": bool(ok1 and ok2),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "seed": int(seed if seed != 0 else 0),
        "policy": policy_res.to_dict(),
        "explore": explore_res.to_dict(),
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0 if (ok1 and ok2) else 2


if __name__ == "__main__":
    raise SystemExit(main())

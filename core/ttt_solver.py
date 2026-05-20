#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:        /opt/ai/oroma/core/ttt_solver.py
# Projekt:     ORÓMA
# Komponente:  Games / TicTacToe
# Modul:       Deterministischer TicTacToe-Solver (Minimax mit Memoization)
# Version:     v1.0
# Stand:       2025-12-29
# Autor:       ORÓMA · KI-JWG-X1
# Lizenz:      MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul liefert für TicTacToe (3x3) eine "perfekte" Aktion (Minimax).
#
# Hintergrund (warum das nötig ist)
# ---------------------------------
# ORÓMA kann TicTacToe über UniversalPolicy/PolicyEngine lernen. In Self-Play
# ohne perfekte Defensive entstehen jedoch häufig Startspieler-Siege. Das führt
# zu q≈+1.0 in policy_rules und damit zu einer "überoptimistischen" Policy.
#
# In der Praxis sieht man dann (wie in tools/ttt_eval.py):
#   Draws: 0%  und Startspieler gewinnt ~100%.
#
# Dieser Solver kann als "Teacher" / Safety-Net genutzt werden:
#   • für Evaluation (ttt_eval)
#   • für stabile defensive Entscheidungen (kein "dummer" Fehler)
#   • optional als Lern-Lehrer (wenn später ein Teacher-Mode ergänzt wird)
#
# WICHTIG
# -------
# • Headless: Nur stdlib.
# • Deterministisch.
# • Arbeitet im KANONRAUM (Index 0..8) – passend zu state_hash "v1|...".
# • "state_hash" kodiert das Board aus Sicht des aktuellen Spielers.
#   Der Solver nimmt daher IMMER an: "Spieler +1 ist am Zug".
#
# API
# ---
#   best_action_from_state_hash("v1|0,0,0,0,0,0,0,0,0") -> ("4", 0)
#   best_action_from_vec([..9 floats..]) -> ("idx", value)
#
# value:
#   +1 = Sieg erzwingbar
#    0 = Remis erzwingbar
#   -1 = Niederlage unvermeidbar
# =============================================================================

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


WINS = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)

# Stabiler Tie-Break: center, corners, edges
PREF_ORDER = (4, 0, 2, 6, 8, 1, 3, 5, 7)


def _as_int_board_from_vec(vec9: List[float]) -> List[int]:
    out: List[int] = []
    for x in (vec9 or [])[:9]:
        try:
            xf = float(x)
        except Exception:
            xf = 0.0
        out.append(1 if xf > 0.5 else -1 if xf < -0.5 else 0)
    while len(out) < 9:
        out.append(0)
    return out


def _parse_state_hash(state_hash: str) -> Optional[List[int]]:
    s = str(state_hash or "").strip()
    if not s:
        return None
    if s.startswith("v1|"):
        s = s[3:]
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 9:
        return None
    out: List[int] = []
    for p in parts:
        try:
            v = int(p)
        except Exception:
            return None
        if v not in (-1, 0, 1):
            return None
        out.append(v)
    return out


def _winner(board: Tuple[int, ...]) -> Optional[int]:
    # returns +1 if root player (X) wins, -1 if opponent wins, None otherwise
    for a, b, c in WINS:
        s = board[a] + board[b] + board[c]
        if s == 3:
            return 1
        if s == -3:
            return -1
    return None


def _is_full(board: Tuple[int, ...]) -> bool:
    return all(v != 0 for v in board)


_CACHE: Dict[Tuple[Tuple[int, ...], int], Tuple[int, int]] = {}


def _minimax(board: Tuple[int, ...], player: int) -> Tuple[int, int]:
    """Minimax aus Sicht des Root-Spielers (+1).

    Args:
      board: Tuple[int] length 9 in {-1,0,1}
      player: +1 (am Zug) oder -1 (am Zug)

    Returns:
      (best_action_index, value)
    """
    key = (board, player)
    if key in _CACHE:
        return _CACHE[key]

    w = _winner(board)
    if w is not None:
        # terminal win/loss for root perspective
        _CACHE[key] = (-1, int(w))
        return _CACHE[key]
    if _is_full(board):
        _CACHE[key] = (-1, 0)
        return _CACHE[key]

    legal = [i for i, v in enumerate(board) if v == 0]
    if not legal:
        _CACHE[key] = (-1, 0)
        return _CACHE[key]

    # deterministic order
    legal_sorted = [i for i in PREF_ORDER if i in legal]
    for i in legal:
        if i not in legal_sorted:
            legal_sorted.append(i)

    best_idx = legal_sorted[0]
    if player == 1:
        best_val = -2
        for idx in legal_sorted:
            nb = list(board)
            nb[idx] = 1
            _, v = _minimax(tuple(nb), -1)
            if v > best_val:
                best_val = v
                best_idx = idx
            if best_val == 1:
                break
    else:
        best_val = 2
        for idx in legal_sorted:
            nb = list(board)
            nb[idx] = -1
            _, v = _minimax(tuple(nb), 1)
            if v < best_val:
                best_val = v
                best_idx = idx
            if best_val == -1:
                break

    _CACHE[key] = (int(best_idx), int(best_val))
    return _CACHE[key]


def best_action_from_state_hash(state_hash: str) -> Optional[Tuple[str, int]]:
    b = _parse_state_hash(state_hash)
    if b is None:
        return None
    board = tuple(int(x) for x in b)
    # Konvention: state_hash ist aus Sicht des aktuellen Spielers.
    # Daher ist IMMER player=+1 am Zug.
    idx, val = _minimax(board, 1)
    if idx < 0:
        return None
    return (str(idx), int(val))


def best_action_from_vec(vec9: List[float]) -> Optional[Tuple[str, int]]:
    b = _as_int_board_from_vec(vec9)
    board = tuple(int(x) for x in b)
    idx, val = _minimax(board, 1)
    if idx < 0:
        return None
    return (str(idx), int(val))

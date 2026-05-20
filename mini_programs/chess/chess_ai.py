#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/chess/chess_ai.py
# Projekt: ORÓMA
# Modul:   Schach-KI (Minimax + Alpha-Beta, leichte Heuristik, ε-Exploration)
# Version: v3.8-r3 (robustes Status-Mapping, Root-TopN + ε, UCI-Fallback)
# Stand:   2025-10-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# HIGHLIGHTS
# ──────────
#  • Kompakte Engine ohne Drittlibs, Pi-tauglich
#  • Bewertung: Material + Mobilität + Königssicherheit + leichter Jitter
#  • Stochastik NUR am Root (Top-N + ε-Greedy), Innenknoten deterministisch
#  • Robuste Status-Auswertung: 'checkmate_black'/'_white', 'stalemate', 'threefold', 'fifty_moves'
#  • API: find_best_move(pos, depth) → Move | None ;  ChessAI.choose(pos) → UCI | None
# =============================================================================

from __future__ import annotations
import os
import math
import random
from typing import Optional, Tuple, List

from .chess_rules import ChessPosition, WHITE, BLACK, PIECE_VALUES, Move
import logging
from core.log_guard import log_suppressed

# RNG-Seed (optional deterministisch)
if "OROMA_CHESS_SEED" in os.environ:
    try:
        random.seed(int(os.environ["OROMA_CHESS_SEED"]))
    except Exception as e:
        log_suppressed('mini_programs/chess/chess_ai.py:33', exc=e, level=logging.WARNING)
        pass

# Tuning per ENV
DEF_DEPTH = int(os.environ.get("OROMA_CHESS_DEPTH", "2") or "2")
EPS       = float(os.environ.get("OROMA_CHESS_EPS", "0.08") or "0.08")     # 8% Exploration
TOP_N     = max(1, int(os.environ.get("OROMA_CHESS_TOPN", "3") or "3"))
NOISE_CP  = max(0, int(os.environ.get("OROMA_CHESS_NOISE", "6") or "6"))   # ±6 cp


# --- Bewertung ---------------------------------------------------------------
def _material(pos: ChessPosition) -> int:
    s = 0
    for (_rc, p) in pos.board:
        if p == ".":
            continue
        v = PIECE_VALUES.get(p, 0)
        s += v if p.isupper() else -v
    return s


def _mobility(pos: ChessPosition) -> int:
    try:
        n = len(pos.generate_legal_moves())
    except Exception:
        n = 0
    return n if pos.stm == WHITE else -n


def _king_safety(pos: ChessPosition) -> int:
    try:
        in_chk = pos.in_check(pos.stm)
    except Exception:
        in_chk = False
    if not in_chk:
        return 0
    return -20 if pos.stm == WHITE else 20


def _jitter() -> int:
    return 0 if NOISE_CP <= 0 else random.randint(-NOISE_CP, NOISE_CP)


def evaluate(pos: ChessPosition) -> int:
    return _material(pos) + _mobility(pos) + _king_safety(pos) + _jitter()


# --- Minimax + Alpha-Beta (Innen) -------------------------------------------
def _terminal_score(status: str) -> Optional[int]:
    if status in ("stalemate", "fifty_moves", "threefold"):
        return 0
    if status.startswith("checkmate"):
        # checkmate_black = Schwarz mattgesetzt → Weiß gewinnt
        if "black" in status:
            return 10_000
        if "white" in status:
            return -10_000
        # Fallback auf konservativ
        return 0
    if status in ("white_won",):
        return 10_000
    if status in ("black_won",):
        return -10_000
    return None


def _minimax(pos: ChessPosition, depth: int, alpha: int, beta: int) -> Tuple[int, Optional[Move]]:
    status = pos.status()
    ts = _terminal_score(status)
    if ts is not None or depth == 0:
        return (ts if ts is not None else evaluate(pos), None)

    legal: List[Move] = pos.generate_legal_moves()
    if not legal:
        return (evaluate(pos), None)

    best_move: Optional[Move] = None

    if pos.stm == WHITE:
        value = -math.inf
        for m in legal:
            pos.apply(m)
            val, _ = _minimax(pos, depth - 1, alpha, beta)
            pos.undo()
            if val > value or (val == value and best_move is None and random.random() < 0.5):
                value = val
                best_move = m
            alpha = max(alpha, int(value))
            if alpha >= beta:
                break
        return int(value), best_move
    else:
        value = math.inf
        for m in legal:
            pos.apply(m)
            val, _ = _minimax(pos, depth - 1, alpha, beta)
            pos.undo()
            if val < value or (val == value and best_move is None and random.random() < 0.5):
                value = val
                best_move = m
            beta = min(beta, int(value))
            if alpha >= beta:
                break
        return int(value), best_move


# --- Root-Suche mit Exploration ---------------------------------------------
def _to_uci(pos: ChessPosition, move: Move) -> Optional[str]:
    try:
        u = pos.move_to_uci(move)
        if isinstance(u, str) and u:
            return u
    except Exception as e:
        log_suppressed('mini_programs/chess/chess_ai.py:146', exc=e, level=logging.WARNING)
        pass
    try:
        u = getattr(move, "uci", None)
        if isinstance(u, str) and u:
            return u
    except Exception as e:
        log_suppressed('mini_programs/chess/chess_ai.py:153', exc=e, level=logging.WARNING)
        pass
    try:
        s = str(move)
        return s if s else None
    except Exception:
        return None


def _search_root(pos: ChessPosition, depth: int) -> Tuple[Optional[Move], Optional[str]]:
    legal = pos.generate_legal_moves()
    if not legal:
        return None, None

    scored: List[Tuple[int, Move, Optional[str]]] = []
    for m in legal:
        pos.apply(m)
        val, _ = _minimax(pos, depth - 1, -10**9, 10**9)
        pos.undo()
        uci = _to_uci(pos, m)
        scored.append((val, m, uci))

    if pos.stm == WHITE:
        scored.sort(key=lambda t: t[0], reverse=True)
    else:
        scored.sort(key=lambda t: t[0])

    n = min(len(scored), max(1, TOP_N))
    pick_idx = 0
    if random.random() < max(0.0, min(1.0, EPS)) and n > 1:
        pick_idx = random.randrange(0, n)

    _, mv, uci = scored[pick_idx]
    return mv, uci


# --- Öffentliche API ---------------------------------------------------------
def find_best_move(pos: ChessPosition, depth: int = None) -> Optional[Move]:
    if depth is None:
        depth = DEF_DEPTH
    mv, _ = _search_root(pos, int(depth))
    return mv


class ChessAI:
    def __init__(self, depth: int = None):
        self.depth = int(depth) if depth is not None else DEF_DEPTH

    def choose_move(self, pos: ChessPosition) -> Optional[Move]:
        return find_best_move(pos, depth=self.depth)

    def choose(self, pos: ChessPosition) -> Optional[str]:
        mv, uci = _search_root(pos, self.depth)
        if uci:
            return uci
        if mv is not None:
            return _to_uci(pos, mv)
        return None
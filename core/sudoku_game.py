#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/sudoku_game.py
# Projekt: ORÓMA – Sudoku Game Core (headless)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Reines Python-Sudoku-Backend (Generator + Solver + Validator) für ORÓMA.
#   Gedacht als „Anti-Langeweile“-Spiel und als optionaler Curriculum-Baustein.
#
# Designziele
# ───────────
#   • Headless/Server-freundlich (keine GUI-Abhängigkeiten).
#   • Reproduzierbar über seed.
#   • Optional: eindeutige Lösung (Uniqueness-Check via Solution-Count).
#   • Uniqueness-Check stoppt nach 2 Lösungen (CPU-schonend).
#
# Werte
# ─────
#   • 0 = leer
#   • 1..9 = gesetzt
#
# =============================================================================

from __future__ import annotations

import random
from typing import List, Optional, Tuple, Dict, Any

Grid = List[List[int]]


def _rng(seed: Optional[int]) -> random.Random:
    return random.Random(int(seed) if seed is not None else random.randrange(1 << 30))


def _copy_grid(g: Grid) -> Grid:
    return [row[:] for row in g]


def _find_empty(g: Grid) -> Optional[Tuple[int, int]]:
    for r in range(9):
        for c in range(9):
            if g[r][c] == 0:
                return r, c
    return None


def _valid_move(g: Grid, r: int, c: int, v: int) -> bool:
    if v < 1 or v > 9:
        return False

    # row/col
    for i in range(9):
        if g[r][i] == v and i != c:
            return False
        if g[i][c] == v and i != r:
            return False

    # box
    br = (r // 3) * 3
    bc = (c // 3) * 3
    for rr in range(br, br + 3):
        for cc in range(bc, bc + 3):
            if g[rr][cc] == v and (rr != r or cc != c):
                return False
    return True


def validate_grid(g: Grid) -> Tuple[bool, str]:
    """Validiert Format und aktuelle Konsistenz (keine Duplikate)."""
    if not isinstance(g, list) or len(g) != 9:
        return False, "grid must be a 9x9 list"
    for row in g:
        if not isinstance(row, list) or len(row) != 9:
            return False, "grid must be a 9x9 list"
        for v in row:
            if not isinstance(v, int) or v < 0 or v > 9:
                return False, "grid values must be ints 0..9"

    for r in range(9):
        for c in range(9):
            v = g[r][c]
            if v == 0:
                continue
            if not _valid_move(g, r, c, v):
                return False, f"conflict at r{r+1} c{c+1}"
    return True, ""


def _solve_backtrack(g: Grid, r: random.Random) -> bool:
    pos = _find_empty(g)
    if not pos:
        return True
    rr, cc = pos
    nums = list(range(1, 10))
    r.shuffle(nums)
    for v in nums:
        if _valid_move(g, rr, cc, v):
            g[rr][cc] = v
            if _solve_backtrack(g, r):
                return True
            g[rr][cc] = 0
    return False


def generate_solution(seed: Optional[int] = None) -> Grid:
    """Erzeugt ein vollständiges Sudoku (gelöstes Board)."""
    r = _rng(seed)
    g: Grid = [[0 for _ in range(9)] for _ in range(9)]
    _solve_backtrack(g, r)
    return g


def _count_solutions(g: Grid, limit: int = 2) -> int:
    """Zählt Lösungen bis limit (für Uniqueness-Check)."""
    pos = _find_empty(g)
    if not pos:
        return 1
    rr, cc = pos
    cnt = 0
    for v in range(1, 10):
        if _valid_move(g, rr, cc, v):
            g[rr][cc] = v
            cnt += _count_solutions(g, limit=limit)
            g[rr][cc] = 0
            if cnt >= limit:
                break
    return cnt


def _difficulty_to_clues(difficulty: str) -> int:
    d = (difficulty or "medium").strip().lower()
    if d == "easy":
        return 40
    if d == "hard":
        return 26
    return 32  # medium


def generate_puzzle(*,
                    seed: Optional[int] = None,
                    difficulty: str = "medium",
                    ensure_unique: bool = True) -> Dict[str, Any]:
    """Generiert Puzzle + Lösung."""
    r = _rng(seed)
    seed_i = int(seed) if seed is not None else r.randrange(1 << 30)

    sol = generate_solution(seed_i)
    puzzle = _copy_grid(sol)

    clues = _difficulty_to_clues(difficulty)
    to_remove = 81 - max(17, min(81, int(clues)))

    cells = [(rr, cc) for rr in range(9) for cc in range(9)]
    r.shuffle(cells)

    removed = 0
    for rr, cc in cells:
        if removed >= to_remove:
            break
        if puzzle[rr][cc] == 0:
            continue
        keep = puzzle[rr][cc]
        puzzle[rr][cc] = 0
        if ensure_unique:
            tmp = _copy_grid(puzzle)
            nsol = _count_solutions(tmp, limit=2)
            if nsol != 1:
                puzzle[rr][cc] = keep
                continue
        removed += 1

    return {
        "seed": seed_i,
        "difficulty": (difficulty or "medium").strip().lower(),
        "puzzle": puzzle,
        "solution": sol,
        "clues": 81 - removed,
        "unique": bool(ensure_unique),
    }


def is_consistent(candidate: Grid) -> bool:
    ok, _ = validate_grid(candidate)
    return ok


def is_solved(puzzle: Grid, candidate: Grid) -> bool:
    """True wenn candidate vollständig & korrekt (und puzzle-feste Felder unverändert)."""
    ok, _ = validate_grid(candidate)
    if not ok:
        return False

    for r in range(9):
        for c in range(9):
            if puzzle[r][c] != 0 and candidate[r][c] != puzzle[r][c]:
                return False
            if candidate[r][c] == 0:
                return False
    return True
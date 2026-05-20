#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/chess/board.py
# Projekt: ORÓMA
# Modul:   Generisches Board-Framework (8x8, spielagnostisch)
# Version: v3.8 (stabil, unverändert API)
# Stand:   2025-10-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
# ZWECK
# ─────
#  Leichtgewichtiges, wiederverwendbares Gitter-Board (Default 8x8) zur
#  Darstellung zustandsbasierter Brettspiele. Keine Regellogik.
# =============================================================================

from __future__ import annotations
from typing import List, Tuple, Optional, Iterable, Any

Coord = Tuple[int, int]  # (row, col) 0..7


class Board:
    __slots__ = ("rows", "cols", "_grid")

    def __init__(self, rows: int = 8, cols: int = 8, fill: Any = "."):
        self.rows = rows
        self.cols = cols
        self._grid: List[List[Any]] = [[fill for _ in range(cols)] for _ in range(rows)]

    def reset(self, mapping: dict[Coord, Any], fill: Any = ".") -> None:
        for r in range(self.rows):
            for c in range(self.cols):
                self._grid[r][c] = fill
        for (r, c), v in mapping.items():
            self._grid[r][c] = v

    def inside(self, rc: Coord) -> bool:
        r, c = rc
        return 0 <= r < self.rows and 0 <= c < self.cols

    def piece_at(self, rc: Coord) -> Any:
        r, c = rc
        return self._grid[r][c]

    def set_piece(self, rc: Coord, piece: Any) -> None:
        r, c = rc
        self._grid[r][c] = piece

    def move(self, frm: Coord, to: Coord) -> Any:
        """Bewegt das Piece (ohne Regelprüfung). Rückgabe: geschlagene Figur (oder Fill)."""
        fr, fc = frm
        tr, tc = to
        cap = self._grid[tr][tc]
        self._grid[tr][tc] = self._grid[fr][fc]
        self._grid[fr][fc] = "."
        return cap

    def clone(self) -> "Board":
        b = Board(self.rows, self.cols)
        b._grid = [row[:] for row in self._grid]
        return b

    def render(self) -> str:
        """ASCII für CLI (8x8, Top=8, Bottom=1, Links=a)."""
        lines: List[str] = []
        for r in range(self.rows):
            rank = self.rows - r
            row = self._grid[r]
            lines.append(f"{rank} " + " ".join(str(x) for x in row))
        files = "  " + " ".join([chr(ord("a") + i) for i in range(self.cols)])
        return "\n".join(lines + [files])

    def __iter__(self) -> Iterable[Tuple[Coord, Any]]:
        for r in range(self.rows):
            for c in range(self.cols):
                yield (r, c), self._grid[r][c]
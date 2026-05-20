#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/tetris_engine.py
# Projekt: ORÓMA – Core Games
# Modul:   TetrisEngine – reine Spiel-Logik (thread-safe, ohne UI)
# Version: v1.0 (stable)
# Stand:   2025-12-15
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# ─────
#   Kapselt die komplette Tetris-Logik unabhängig von Rendering/Flask:
#     • 10×20 Board, 7 Tetrominoes, Rotation, Kollision, Lock
#     • Zeilen-Clear, Score, Level, Pace
#     • Thread-safe (Lock), State-Snapshot als JSON-geeignetes Dict
#
# Integration
# ───────────
#   Wird von ui/tetris_ui.py genutzt. Keine externen Abhängigkeiten.
#
# Konfiguration
# ─────────────
#   WIDTH=10, HEIGHT=20 (Tetris-Standard)
#   Scoring: 1/2/3/4 Linien → 40/100/300/1200 * (Level+1)
#   Level steigt alle 10 gelöschten Linien
# =============================================================================

from __future__ import annotations
import random, threading, time
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

WIDTH, HEIGHT = 10, 20

# Tetrominos (Rotations-Sets), jede Rotation als Liste von (x,y) Zellen
# Koordinaten relativ zum Piece-Ursprung.
TETROMINOS: Dict[str, List[List[Tuple[int,int]]]] = {
    "I": [
        [(0,1),(1,1),(2,1),(3,1)],
        [(2,0),(2,1),(2,2),(2,3)],
        [(0,2),(1,2),(2,2),(3,2)],
        [(1,0),(1,1),(1,2),(1,3)],
    ],
    "O": [
        [(1,0),(2,0),(1,1),(2,1)],
        [(1,0),(2,0),(1,1),(2,1)],
        [(1,0),(2,0),(1,1),(2,1)],
        [(1,0),(2,0),(1,1),(2,1)],
    ],
    "T": [
        [(1,0),(0,1),(1,1),(2,1)],
        [(1,0),(1,1),(2,1),(1,2)],
        [(0,1),(1,1),(2,1),(1,2)],
        [(1,0),(0,1),(1,1),(1,2)],
    ],
    "S": [
        [(1,0),(2,0),(0,1),(1,1)],
        [(1,0),(1,1),(2,1),(2,2)],
        [(1,1),(2,1),(0,2),(1,2)],
        [(0,0),(0,1),(1,1),(1,2)],
    ],
    "Z": [
        [(0,0),(1,0),(1,1),(2,1)],
        [(2,0),(1,1),(2,1),(1,2)],
        [(0,1),(1,1),(1,2),(2,2)],
        [(1,0),(0,1),(1,1),(0,2)],
    ],
    "J": [
        [(0,0),(0,1),(1,1),(2,1)],
        [(1,0),(2,0),(1,1),(1,2)],
        [(0,1),(1,1),(2,1),(2,2)],
        [(1,0),(1,1),(0,2),(1,2)],
    ],
    "L": [
        [(2,0),(0,1),(1,1),(2,1)],
        [(1,0),(1,1),(1,2),(2,2)],
        [(0,1),(1,1),(2,1),(0,2)],
        [(0,0),(1,0),(1,1),(1,2)],
    ],
}
ORDER = ["I","O","T","S","Z","J","L"]

@dataclass
class Piece:
    kind: str
    rot: int
    x: int
    y: int

    def cells(self) -> List[Tuple[int,int]]:
        return [(self.x+cx, self.y+cy) for (cx,cy) in TETROMINOS[self.kind][self.rot]]

class TetrisEngine:
    def __init__(self, seed: Optional[int]=None) -> None:
        self._rng = random.Random(seed)
        self._lock = threading.RLock()
        self.reset()

    # ---------- core state ----------
    def reset(self) -> None:
        with self._lock:
            self.board = [[-1 for _ in range(WIDTH)] for _ in range(HEIGHT)]
            self.score = 0
            self.level = 0
            self.lines_total = 0
            self.running = True
            self._bag: List[str] = []
            self.cur: Optional[Piece] = None
            self.next_kind: str = self._draw_from_bag()
            self.spawn_new()

    def _draw_from_bag(self) -> str:
        if not self._bag:
            self._bag = ORDER[:]
            self._rng.shuffle(self._bag)
        return self._bag.pop()

    def spawn_new(self) -> None:
        with self._lock:
            k = self.next_kind
            self.next_kind = self._draw_from_bag()
            # Spawn-Position (klassisch "oben mittig")
            # ------------------------------------------------------------
            # Hintergrund:
            #   Ein fixes x=3 wirkt in der Praxis (insb. O/T/S/Z) nicht immer
            #   "zentriert" und sieht so aus, als würde das Piece direkt über
            #   einem bestehenden Stapel erscheinen.
            #
            #   Wir zentrieren daher die Bounding-Box der Rotation 0 über der
            #   Mitte des Boards. y bleibt negativ (typisches Tetris-Spawn),
            #   damit ein voller Top-Stack korrekt GameOver auslösen kann.
            cells0 = TETROMINOS[k][0]
            min_x = min(cx for (cx, _) in cells0)
            max_x = max(cx for (cx, _) in cells0)
            w = (max_x - min_x + 1)
            x0 = ((WIDTH - w) // 2) - min_x
            p = Piece(kind=k, rot=0, x=int(x0), y=-2)
            if not self._can_place(p):
                # Game over
                self.running = False
                self.cur = None
                return
            self.cur = p

    # ---------- movement ----------
    def _can_place(self, p: Piece) -> bool:
        for (x,y) in p.cells():
            if x < 0 or x >= WIDTH or y >= HEIGHT:
                return False
            if y >= 0 and self.board[y][x] != -1:
                return False
        return True

    def _move(self, dx:int, dy:int, drot:int=0) -> bool:
        if not self.cur: return False
        np = Piece(self.cur.kind, (self.cur.rot + drot) % 4, self.cur.x + dx, self.cur.y + dy)
        if self._can_place(np):
            self.cur = np
            return True
        return False

    def left(self) -> bool:
        with self._lock:
            return self._move(-1, 0)

    def right(self) -> bool:
        with self._lock:
            return self._move(+1, 0)

    def rotate(self) -> bool:
        with self._lock:
            # Simple rotation (ohne SRS wall kicks – reicht für Mini-Game)
            if self._move(0, 0, +1): return True
            # kleine Kicks probieren
            return self._move(-1, 0, +1) or self._move(+1, 0, +1)

    def soft_drop(self) -> bool:
        with self._lock:
            return self._move(0, +1)

    def hard_drop(self) -> int:
        with self._lock:
            if not self.cur: return 0
            d = 0
            while self._move(0, +1):
                d += 1
            self._lock_piece()
            return d

    # ---------- locking & line clear ----------
    def _lock_piece(self) -> None:
        if not self.cur: return
        # Game-over detection:
        # If any cell of the currently locking piece is above the visible board (y < 0),
        # classic Tetris rules consider this an immediate game over.
        # Without this, pieces can "lock" partially above the top and the game never ends.
        any_above = any((y < 0) for (x, y) in self.cur.cells())
        for (x,y) in self.cur.cells():
            if 0 <= y < HEIGHT and 0 <= x < WIDTH:
                self.board[y][x] = ORDER.index(self.cur.kind)  # 0..6
        self.cur = None
        self._clear_lines()
        if any_above:
            self.running = False
            return
        self.spawn_new()
    def _clear_lines(self) -> None:
        full_rows = [y for y in range(HEIGHT) if all(self.board[y][x] != -1 for x in range(WIDTH))]
        n = len(full_rows)
        if n == 0:
            return
        # remove and add empty rows on top
        for y in reversed(full_rows):
            del self.board[y]
        for _ in range(n):
            self.board.insert(0, [-1]*WIDTH)
        # scoring
        self.lines_total += n
        self.score += [0, 40, 100, 300, 1200][n] * (self.level + 1)
        self.level = self.lines_total // 10

    # ---------- tick ----------
    def tick_ms(self) -> int:
        # Level-Speed (klassisch ~ 48..8 frames → hier ms)
        table = [800, 720, 630, 550, 470, 380, 300, 220, 130, 100, 85, 75, 65, 55, 45]
        i = min(self.level, len(table)-1)
        return table[i]

    def step(self) -> None:
        with self._lock:
            if not self.running or not self.cur:
                return
            if not self._move(0, +1):
                self._lock_piece()

    # ---------- pause ----------
    def toggle_pause(self) -> bool:
        with self._lock:
            self.running = not self.running
            return self.running

    # ---------- ghost ----------
    def ghost_y(self) -> Optional[int]:
        with self._lock:
            if not self.cur: return None
            gy = self.cur.y
            while True:
                test = Piece(self.cur.kind, self.cur.rot, self.cur.x, gy+1)
                if not self._can_place(test):
                    return gy
                gy += 1

    # ---------- state ----------
    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            cur_cells = self.cur.cells() if self.cur else []
            ghost = self.ghost_y()
            ghost_cells: List[Tuple[int,int]] = []
            if ghost is not None and self.cur:
                ghost_cells = [(self.cur.x+cx, ghost+cy) for (cx,cy) in TETROMINOS[self.cur.kind][self.cur.rot]]
            return {
                "ok": True,
                "board": self.board,           # HEIGHT × WIDTH ints (−1 = leer, 0..6 Farbe)
                "cur": {
                    "kind": self.cur.kind if self.cur else None,
                    "rot": self.cur.rot if self.cur else None,
                    "x": self.cur.x if self.cur else None,
                    "y": self.cur.y if self.cur else None,
                    "cells": cur_cells,
                },
                "ghost": ghost_cells,
                "next": self.next_kind,
                "score": self.score,
                "level": self.level,
                "lines": self.lines_total,
                "running": self.running,
                "tick_ms": self.tick_ms(),
            }

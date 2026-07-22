#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/snake3d.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   Snake3D – Headless 3D-Snake-Umgebung für Schablonen-Transfer
# Version: v0.1.0-pro-template-test
# Stand:   2026-06-28
# Autor:   ORÓMA · Jörg Werner + GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Diese Datei implementiert eine kleine, vollständig headless laufende 3D-Snake-
# Umgebung. Sie ist bewusst unabhängig von pygame, Qt, Wayland, X11, Kamera,
# Audio, UI und Datenbank. Der Zweck ist nicht, ein neues UI-Spiel einzuführen,
# sondern den Transfer der in core.state_template.py dokumentierten Schablone
# `snake:pro_v2` auf eine neue Domäne praktisch zu testen.
#
# DESIGN
# ------
# Snake3D nutzt einen Würfel mit Koordinaten (x, y, z). Die Schlange besitzt eine
# horizontale Blickrichtung auf der x/y-Ebene. Der relative Aktionsraum folgt der
# Snake-Schablone, erweitert um die Z-Achse:
#
#   0 = forward  – vorwärts in aktueller horizontaler Blickrichtung
#   1 = left     – horizontal links drehen und gehen
#   2 = right    – horizontal rechts drehen und gehen
#   3 = up       – eine Ebene nach oben gehen, Blickrichtung bleibt erhalten
#   4 = down     – eine Ebene nach unten gehen, Blickrichtung bleibt erhalten
#
# Damit bleibt die wichtigste Snake-Invariante erhalten: Es gibt keine direkte
# Rückwärtsaktion in den eigenen Hals. Die 3D-Welt selbst besitzt natürlich sechs
# Nachbarschaftsrichtungen; der produktive relative Action-Raum enthält fünf
# sichere, schablonenkompatible Alternativen.
#
# HEADLESS-INVARIANTEN
# -------------------
# - Keine Grafik-/Audio-/Hardware-Abhängigkeit.
# - Kein Import aus core.*, damit mini_programs/snake3d.py isoliert testbar ist.
# - Keine Datei- oder DB-Schreibzugriffe.
# - Deterministisch reproduzierbar über einen übergebenen random.Random.
# =============================================================================

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import random
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

Point3 = Tuple[int, int, int]

# Horizontale Blickrichtungen: 0=north, 1=east, 2=south, 3=west.
HORIZONTAL_DIRS: Tuple[Point3, ...] = (
    (0, -1, 0),
    (1, 0, 0),
    (0, 1, 0),
    (-1, 0, 0),
)

# Volle 3D-Nachbarschaft für Flood-Fill/Freiraumabschätzung.
NEIGHBOR_DIRS_3D: Tuple[Point3, ...] = (
    (0, -1, 0),
    (1, 0, 0),
    (0, 1, 0),
    (-1, 0, 0),
    (0, 0, 1),
    (0, 0, -1),
)

# Relativer professioneller Action-Raum.
ACTION_NAMES: Dict[int, str] = {
    0: "forward",
    1: "left",
    2: "right",
    3: "up",
    4: "down",
}
REL_ACTIONS_3D: Tuple[int, ...] = tuple(sorted(ACTION_NAMES.keys()))


@dataclass(frozen=True)
class StepResult:
    """Kleines Ergebnisobjekt für einen Environment-Step."""

    alive: bool
    ate_food: bool
    collision: str
    score_food: int
    length: int
    head: Point3
    food: Optional[Point3]


def _add(a: Point3, b: Point3) -> Point3:
    return int(a[0]) + int(b[0]), int(a[1]) + int(b[1]), int(a[2]) + int(b[2])


def manhattan3(a: Point3, b: Point3) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1])) + abs(int(a[2]) - int(b[2]))


def sign(value: int) -> int:
    return 1 if int(value) > 0 else -1 if int(value) < 0 else 0


def rel_action_to_delta(heading: int, action: int) -> Tuple[int, Point3]:
    """Mappe eine relative Aktion auf neue horizontale Blickrichtung + Delta."""
    h = int(heading) % 4
    a = int(action)
    if a == 0:      # forward
        return h, HORIZONTAL_DIRS[h]
    if a == 1:      # left
        nh = (h - 1) % 4
        return nh, HORIZONTAL_DIRS[nh]
    if a == 2:      # right
        nh = (h + 1) % 4
        return nh, HORIZONTAL_DIRS[nh]
    if a == 3:      # up
        return h, (0, 0, 1)
    if a == 4:      # down
        return h, (0, 0, -1)
    return h, HORIZONTAL_DIRS[h]


class Snake3DEnv:
    """Headless 3D-Snake-Environment.

    Die Klasse enthält nur Spielmechanik. State-Abstraktion, Lernloop, Template-
    Report und DBWriter-Anbindung liegen bewusst im Runner, nicht im Mini-Spiel.
    """

    def __init__(self, size: int = 6, rng: Optional[random.Random] = None):
        self.size = max(4, int(size))
        self.rng = rng if rng is not None else random.Random()
        self.snake: List[Point3] = []
        self.heading = 1
        self.food: Optional[Point3] = None
        self.score_food = 0
        self.steps = 0
        self.steps_since_food = 0
        self.alive = True
        self.collision = ""
        self.reset()

    def reset(self) -> "Snake3DEnv":
        s = self.size
        cx = s // 2
        cy = s // 2
        cz = s // 2
        # Länge 3, Blickrichtung east; funktioniert ab size>=4 stabil.
        self.snake = [(cx, cy, cz), (cx - 1, cy, cz), (cx - 2, cy, cz)]
        self.heading = 1
        self.score_food = 0
        self.steps = 0
        self.steps_since_food = 0
        self.alive = True
        self.collision = ""
        self.food = self.spawn_food()
        return self

    def in_bounds(self, p: Point3) -> bool:
        x, y, z = int(p[0]), int(p[1]), int(p[2])
        return 0 <= x < self.size and 0 <= y < self.size and 0 <= z < self.size

    def spawn_food(self) -> Optional[Point3]:
        occupied = set(self.snake)
        total = self.size ** 3
        if len(occupied) >= total:
            return None
        for _ in range(max(100, total * 4)):
            p = (self.rng.randrange(self.size), self.rng.randrange(self.size), self.rng.randrange(self.size))
            if p not in occupied:
                return p
        for z in range(self.size):
            for y in range(self.size):
                for x in range(self.size):
                    p = (x, y, z)
                    if p not in occupied:
                        return p
        return None

    def next_head(self, action: int) -> Tuple[int, Point3]:
        nh, delta = rel_action_to_delta(self.heading, int(action))
        return nh, _add(self.snake[0], delta)

    def will_collide(self, p: Point3, *, ate_food: bool = False) -> str:
        if not self.in_bounds(p):
            return "wall"
        # Wenn nicht gegessen wird, wandert der Tail weg. In den alten Tail zu
        # laufen ist daher erlaubt. Beim Essen bleibt der Tail stehen.
        body = self.snake if bool(ate_food) else self.snake[:-1]
        if p in set(body):
            return "self"
        return ""

    def step(self, action: int) -> StepResult:
        if not self.alive:
            return StepResult(False, False, self.collision or "dead", self.score_food, len(self.snake), self.snake[0], self.food)
        self.steps += 1
        new_heading, new_head = self.next_head(int(action))
        ate = self.food is not None and new_head == self.food
        collision = self.will_collide(new_head, ate_food=ate)
        if collision:
            self.alive = False
            self.collision = collision
            return StepResult(False, False, collision, self.score_food, len(self.snake), self.snake[0], self.food)

        self.heading = int(new_heading) % 4
        self.snake.insert(0, new_head)
        if ate:
            self.score_food += 1
            self.steps_since_food = 0
            self.food = self.spawn_food()
        else:
            self.snake.pop()
            self.steps_since_food += 1
        return StepResult(True, bool(ate), "", self.score_food, len(self.snake), self.snake[0], self.food)

    def flood_space(self, start: Point3, blocked: Iterable[Point3], limit: int = 512) -> int:
        if not self.in_bounds(start):
            return 0
        blocked_set = set(blocked)
        if start in blocked_set:
            return 0
        q: Deque[Point3] = deque([start])
        seen = {start}
        while q and len(seen) < int(limit):
            x, y, z = q.popleft()
            for dx, dy, dz in NEIGHBOR_DIRS_3D:
                np = (x + dx, y + dy, z + dz)
                if np in seen or np in blocked_set or not self.in_bounds(np):
                    continue
                seen.add(np)
                q.append(np)
        return int(len(seen))

    def occupied_after_tail_move(self, *, ate_food: bool) -> List[Point3]:
        return list(self.snake) if bool(ate_food) else list(self.snake[:-1])

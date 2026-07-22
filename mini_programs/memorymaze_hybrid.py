#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/memorymaze_hybrid.py
# Projekt: ORÓMA
# Version: v1.5
# Stand:   2026-06-27
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#   "MemoryMaze Hybrid" – ein strategisches 2-Spieler-Maze (PacMan-Feeling)
#   mit Memory-Mechanik als Weg-Blocker (5 Paare), Items, Fallgruben und
#   optionaler Hard-Variante mit einem dritten Jäger (P3).
#
#   Dieses Modul ist bewusst "Headless-first" und integriert sich in ORÓMA
#   über die Mini-Program Registry (auto-discovery via mini_programs/__init__.py).
#
#   Wichtig: Dieses Spiel ist NICHT identisch mit mini_programs/memory_maze2033.py.
#   memory_maze2033 enthält zwei getrennte Modi (Memory + Maze). Dieses Modul
#   implementiert das hier festgelegte Hybrid-Game als EIN Environment.
#
# Spezifikation (v1.4 FINAL)
# --------------------------
#   1) Maze
#     • Außenring-Gang umlaufend, ≥5 Eingänge ins Innere.
#     • Nur Wege/Wände, keine Türmechanik.
#     • Erreichbarkeit: beide Spieler erreichen alle A–E und Items.
#
#   2) Spieler
#     • P1/P2 ghosted (blockieren sich nicht).
#     • 5 Paare (A–E) als Objekt-Blocker: initial unbegehbar, verschwinden bei Match.
#     • Pair-Speed-Bonus: Finder dauerhaft +10% Speed je Paar.
#     • Fallgruben: +1 Strike, Teleport 1 Tile zurück, 5 Strikes => tot.
#
#   3) Sicht (FOV)
#     • 120° Sichtkegel (±60° um Blickrichtung), LOS bis zur Wand.
#     • Claim entsteht nur durch aktive Aktion (hier: "reveal"), nicht durch LOS.
#
#   4) Claim-Lock (Anti-Steal)
#     • Claim-State pro Objekt: UNCLAIMED / CLAIMED_BY_P1 / CLAIMED_BY_P2.
#     • Pro Spieler max 1 aktiver Claim.
#     • Match: beide Blocker verschwinden + Speed-Bonus.
#     • Mismatch: beide Claims lösen sich.
#     • Timeout: CLAIM_TIMEOUT_STEPS = 240.
#
#   5) Items (Spawn-Limits pro Spiel)
#     • A Trail-Blocker (temporär): max 2 spawns, 5 Blocker, 60s, Nutzer -5% temp.
#     • B Permanent-Blocker: max 2 spawns, global max 1 Nutzung, Pickup-Window 30s,
#       Anti-Softlock (BFS check).
#     • C Speed+Ghost: max 1 spawn, +50% speed 10s, ghost nur gegen Blocker/Objekt.
#     • D Trap-Item: max 2 spawns, platziert 1 Fallgrube 30s.
#
#   6) Hazards
#     • Zufalls-Fallgruben temporär, telegraphed (optional), nicht direkt neben Spielern.
#
#   7) Hard-P3 (separat wählbar)
#     • mode: normal | hard_p3.
#     • P3 nutzt ganzes Maze.
#     • Kontakt: P3 +10% speed (stack), Spieler -10% speed (stack).
#     • Caps (Default): P3 max 2.0×, Spieler min 0.5×, Kontakt-Cooldown 0.8s.
#     • P3 stirbt durch Fallgruben nach 50 Strikes (10× HP).
#
# API / Integration
# -----------------
#   Dieses Modul stellt eine Spielklasse HybridGame bereit, die in der UI und
#   in Daily Runnern genutzt wird.
#
#   Wichtige Methoden:
#     - reset(seed=None, mode='normal')
#     - step(actions: dict) -> dict   (simuliert einen Tick; Aktionen für P1/P2/P3)
#     - state() -> dict               (voller Spielzustand, UI-sicher)
#
#   Hinweis: Für ORÓMA-Policy werden States als JSON stabilisiert und in
#   policy_rules unter namespace "game:memorymaze_hybrid" genutzt.
#
# Erweiterung v1.6
# ----------------
#   • Das Spielziel ist produktiv messbar: gelöste Memory-Paare setzen jetzt
#     winner/winner_reason, wenn alle Paare entfernt wurden.
#   • Reveal/Claim/Match/Mismatch/Pit/P3-Kontakt werden als Telemetrie gezählt.
#   • Die Headless-KI spielt memory-fähig: nach dem ersten Claim sucht sie
#     gezielt das zweite Objekt desselben Symbols und navigiert zu Reveal-Posen
#     neben Blockern, statt am ersten Objekt hängen zu bleiben.
#
#   • Policy-Reuse-Fix: Die Headless-KI wiederholt einen bereits geclaimten
#     ersten Blocker nicht mehr mit REVEAL, sondern sucht konsequent das
#     zweite passende Symbol. Das verhindert deterministische Claim-Timeout-
#     Schleifen in Policy-only-Läufen.
#
# DB / Locks
# ----------
#   Dieses Modul schreibt selbst NICHT in die DB. Schreiben erfolgt ausschließlich
#   über UI/Runner via core.sql_manager.* (mit korrekt geschlossenen Verbindungen).
#
# =============================================================================

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any


Coord = Tuple[int, int]


@dataclass
class _Obj:
    sym: str
    pos: Coord
    active: bool = True
    claimed_by: int = 0          # 0 none, 1 P1, 2 P2
    claimed_step: int = -1


@dataclass
class _Pit:
    pos: Coord
    ttl_steps: int
    telegraph_steps: int = 0

    @property
    def active(self) -> bool:
        return self.telegraph_steps <= 0


@dataclass
class _ItemSpawn:
    kind: str   # a/b/c/d
    pos: Coord
    active: bool = True


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _sign(x: int) -> int:
    return -1 if x < 0 else (1 if x > 0 else 0)


def _bfs_reachable(grid: List[str], start: Coord, blocked: set[Coord]) -> set[Coord]:
    h = len(grid)
    w = len(grid[0]) if h else 0
    q: List[Coord] = [start]
    seen: set[Coord] = {start}
    while q:
        r, c = q.pop(0)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if rr < 0 or cc < 0 or rr >= h or cc >= w:
                continue
            if (rr, cc) in seen:
                continue
            if grid[rr][cc] == '#':
                continue
            if (rr, cc) in blocked:
                continue
            seen.add((rr, cc))
            q.append((rr, cc))
    return seen


class HybridGame:
    """Headless Hybrid-Game Environment (v1.4).

    Das Environment ist absichtlich deterministisch im Sinne "Seed → gleiche Map
    + gleiche initiale Objektplatzierung" (soweit Random genutzt wird).
    """

    CLAIM_TIMEOUT_STEPS = 240

    # Hard caps
    P3_SPEED_CAP = 2.0
    PLAYER_SPEED_FLOOR = 0.5
    CONTACT_COOLDOWN_STEPS = 3  # ~0.8s bei dt_ms≈250

    def __init__(self, map_kind: str = "sym") -> None:
        self.map_kind = map_kind
        self._step_i = 0
        self._rng = random.Random(1)
        self._grid = self._load_map(map_kind)
        self.h = len(self._grid)
        self.w = len(self._grid[0]) if self.h else 0

        # spawns
        self.p1: Coord = (1, 1)
        self.p2: Coord = (self.h - 2, self.w - 2)
        self.p3: Coord = (1, self.w - 2)

        # facing: unit vector
        self.face: Dict[str, Tuple[int, int]] = {"p1": (0, 1), "p2": (0, -1), "p3": (1, 0)}

        # speed multipliers
        self.speed: Dict[str, float] = {"p1": 1.0, "p2": 1.0, "p3": 1.0}
        self.strikes: Dict[str, int] = {"p1": 0, "p2": 0, "p3": 0}

        self.hard_p3: bool = False
        self._p3_contact_cd = 0

        # objects & items
        self.objects: List[_Obj] = []
        self.item_spawns: List[_ItemSpawn] = []
        self.pits: List[_Pit] = []

        self.inventory: Dict[str, Dict[str, int]] = {"p1": {}, "p2": {}}
        self._perm_block_used: bool = False
        self._perm_pickup_deadline: Dict[str, int] = {"p1": -1, "p2": -1}
        self._speedghost_until: Dict[str, int] = {"p1": -1, "p2": -1}
        self._trail_until: Dict[str, int] = {"p1": -1, "p2": -1}
        self._trail_blocks: List[Tuple[Coord, int]] = []  # pos, expire_step
        self._perm_blocks: set[Coord] = set()
        self._trap_blocks: List[Tuple[Coord, int]] = []  # pit placed by item, expire_step

        # claim per player
        self._claim_first: Dict[str, Optional[int]] = {"p1": None, "p2": None}

        # scoring/telemetry for headless learning and UI diagnostics
        self.pairs_cleared: Dict[str, int] = {"p1": 0, "p2": 0}
        self.winner_reason: str = ""
        self.telemetry: Dict[str, int] = {
            "reveal_attempts": 0,
            "claims": 0,
            "second_reveals": 0,
            "matches": 0,
            "mismatches": 0,
            "claim_timeouts": 0,
            "pit_hits": 0,
            "p3_contacts": 0,
        }

        # game end
        self.winner: Optional[str] = None

    # ---------------------------------------------------------------------
    # Map
    # ---------------------------------------------------------------------
    def _load_map(self, kind: str) -> List[str]:
        # Minimal embedded map. For production, swap to generator or map pack.
        # This map is deliberately ring-based and connected.
        if kind == "asym":
            return _MAP_ASYM
        return _MAP_SYM

    # ---------------------------------------------------------------------
    # Reset/Init
    # ---------------------------------------------------------------------
    def reset(self, seed: Optional[int] = None, mode: str = "normal") -> None:
        self._step_i = 0
        self._rng = random.Random(int(seed) if seed is not None else int(time.time()))
        self.hard_p3 = (mode == "hard_p3")
        self.winner = None

        # locate spawns from markers if present
        # NOTE (robustness): The embedded map pack is maintained as lists of strings.
        # In practice, it's easy for one line to be shorter (trailing whitespace stripped,
        # editor wrap, etc.). Direct string indexing would then crash with IndexError.
        #
        # We normalize the map here: compute max width and right-pad all lines with
        # walls '#'. This is conservative (missing cells become walls) and ensures
        # deterministic behavior across editors and backup pipelines.
        raw = list(self._load_map(self.map_kind))
        self.h = len(raw)
        self.w = max((len(r) for r in raw), default=0)
        if self.h <= 0 or self.w <= 0:
            raise ValueError("memorymaze_hybrid: map is empty")
        self._grid = [r.ljust(self.w, '#') for r in raw]

        def find(ch: str, fallback: Coord) -> Coord:
            for r in range(self.h):
                for c in range(self.w):
                    if self._grid[r][c] == ch:
                        return (r, c)
            return fallback

        self.p1 = find('1', (1, 1))
        self.p2 = find('2', (self.h - 2, self.w - 2))
        self.p3 = find('3', (1, self.w - 2))
        self.face = {"p1": (0, 1), "p2": (0, -1), "p3": (1, 0)}
        self.speed = {"p1": 1.0, "p2": 1.0, "p3": 1.0}
        self.strikes = {"p1": 0, "p2": 0, "p3": 0}
        self._p3_contact_cd = 0

        self.objects = self._place_pairs(5)
        self.item_spawns = self._place_items()
        self.pits = []
        self.inventory = {"p1": {}, "p2": {}}
        self._perm_block_used = False
        self._perm_pickup_deadline = {"p1": -1, "p2": -1}
        self._speedghost_until = {"p1": -1, "p2": -1}
        self._trail_until = {"p1": -1, "p2": -1}
        self._trail_blocks = []
        self._perm_blocks = set()
        self._trap_blocks = []
        self._claim_first = {"p1": None, "p2": None}
        self.pairs_cleared = {"p1": 0, "p2": 0}
        self.winner_reason = ""
        self.telemetry = {
            "reveal_attempts": 0,
            "claims": 0,
            "second_reveals": 0,
            "matches": 0,
            "mismatches": 0,
            "claim_timeouts": 0,
            "pit_hits": 0,
            "p3_contacts": 0,
        }

    def _walkable(self, pos: Coord) -> bool:
        r, c = pos
        if r < 0 or c < 0 or r >= self.h or c >= self.w:
            return False
        if self._grid[r][c] == '#':
            return False
        if pos in self._perm_blocks:
            return False
        for p, exp in self._trail_blocks:
            if p == pos and self._step_i < exp:
                return False
        # object blockers active
        for o in self.objects:
            if o.active and o.pos == pos:
                return False
        return True

    def _ghost_blocker_allowed(self, who: str, pos: Coord) -> bool:
        # Speed+Ghost allows passing object blockers, trail blockers, perm blockers
        if self._speedghost_until.get(who, -1) >= self._step_i:
            if pos[0] < 0 or pos[1] < 0 or pos[0] >= self.h or pos[1] >= self.w:
                return False
            if self._grid[pos[0]][pos[1]] == '#':
                return False
            return True
        return self._walkable(pos)

    def _active_object_index_at(self, pos: Coord) -> Optional[int]:
        for i, o in enumerate(self.objects):
            if o.active and o.pos == pos:
                return i
        return None

    def _object_ahead_index(self, who: str) -> Optional[int]:
        fr, fc = self.face.get(who, (0, 1))
        pr, pc = self.p1 if who == "p1" else self.p2 if who == "p2" else self.p3
        return self._active_object_index_at((pr + fr, pc + fc))

    def _adjacent_reveal_poses(self, idx: int) -> List[Tuple[Coord, str]]:
        if idx < 0 or idx >= len(self.objects) or not self.objects[idx].active:
            return []
        r, c = self.objects[idx].pos
        poses: List[Tuple[Coord, str]] = []
        # action is the blocked direction used to turn toward the object once
        # the player stands at the adjacent pose. Next tick REVEAL can fire.
        for pos, face_action in (
            ((r - 1, c), "D"),
            ((r + 1, c), "U"),
            ((r, c - 1), "R"),
            ((r, c + 1), "L"),
        ):
            if self._walkable(pos):
                poses.append((pos, face_action))
        return poses

    def _move_action_between(self, src: Coord, dst: Coord) -> str:
        dr = dst[0] - src[0]
        dc = dst[1] - src[1]
        if dr < 0:
            return "U"
        if dr > 0:
            return "D"
        if dc < 0:
            return "L"
        if dc > 0:
            return "R"
        return ""

    def _bfs_next_action(self, who: str, goals: List[Tuple[Coord, str]]) -> str:
        if not goals:
            return ""
        start = self.p1 if who == "p1" else self.p2 if who == "p2" else self.p3
        goal_map = {pos: face_action for pos, face_action in goals}
        if start in goal_map:
            return goal_map[start]

        q: List[Coord] = [start]
        prev: Dict[Coord, Optional[Coord]] = {start: None}
        found: Optional[Coord] = None
        while q:
            cur = q.pop(0)
            if cur in goal_map:
                found = cur
                break
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cur[0] + dr, cur[1] + dc)
                if nxt in prev:
                    continue
                if who == "p3":
                    ok = self._walkable(nxt) or nxt in (self.p1, self.p2)
                else:
                    ok = self._ghost_blocker_allowed(who, nxt)
                if not ok:
                    continue
                prev[nxt] = cur
                q.append(nxt)

        if found is None:
            return ""
        cur = found
        parent = prev.get(cur)
        while parent is not None and parent != start:
            cur = parent
            parent = prev.get(cur)
        return self._move_action_between(start, cur)

    def _choose_memory_target_index(self, who: str) -> Optional[int]:
        pid = 1 if who == "p1" else 2
        idx_first = self._claim_first.get(who)
        if idx_first is not None and 0 <= idx_first < len(self.objects):
            first = self.objects[idx_first]
            if first.active and first.claimed_by == pid:
                candidates = [
                    (i, o) for i, o in enumerate(self.objects)
                    if i != idx_first and o.active and o.sym == first.sym and o.claimed_by in (0, pid)
                ]
                if candidates:
                    me = self.p1 if who == "p1" else self.p2
                    return min(candidates, key=lambda io: abs(io[1].pos[0] - me[0]) + abs(io[1].pos[1] - me[1]))[0]

        me = self.p1 if who == "p1" else self.p2
        candidates = [
            (i, o) for i, o in enumerate(self.objects)
            if o.active and o.claimed_by in (0, pid)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda io: abs(io[1].pos[0] - me[0]) + abs(io[1].pos[1] - me[1]))[0]

    def legal_actions(self, who: str) -> List[str]:
        if self.winner is not None:
            return []
        if who == "p3":
            me = self.p3
            legal: List[str] = []
            for act, d in (("U", (-1, 0)), ("D", (1, 0)), ("L", (0, -1)), ("R", (0, 1))):
                nxt = (me[0] + d[0], me[1] + d[1])
                if self._walkable(nxt) or nxt in (self.p1, self.p2):
                    legal.append(act)
            return legal or ["U", "D", "L", "R"]

        me = self.p1 if who == "p1" else self.p2
        legal = []
        for act, d in (("U", (-1, 0)), ("D", (1, 0)), ("L", (0, -1)), ("R", (0, 1))):
            nxt = (me[0] + d[0], me[1] + d[1])
            # Moving into an active object is legal as a turn-in-place action:
            # _move sets face before rejecting the blocked move, so REVEAL can
            # happen on the next tick. This is essential for object-blocker play.
            if self._ghost_blocker_allowed(who, nxt) or self._active_object_index_at(nxt) is not None:
                legal.append(act)
        if self._object_ahead_index(who) is not None:
            legal.append("REVEAL")
        return legal or ["U", "D", "L", "R"]

    def _place_pairs(self, pairs: int) -> List[_Obj]:
        syms = [chr(ord('A') + i) for i in range(pairs)]
        # candidate corridor tiles (excluding ring corners to reduce triviality)
        candidates: List[Coord] = []
        for r in range(1, self.h - 1):
            for c in range(1, self.w - 1):
                if self._grid[r][c] != '.':
                    continue
                if (r, c) in (self.p1, self.p2, self.p3):
                    continue
                candidates.append((r, c))
        self._rng.shuffle(candidates)
        objs: List[_Obj] = []
        idx = 0
        for s in syms:
            for _ in range(2):
                if idx >= len(candidates):
                    break
                objs.append(_Obj(sym=s, pos=candidates[idx]))
                idx += 1
        return objs

    def _place_items(self) -> List[_ItemSpawn]:
        # fixed spawns from map characters (a/b/c/d) if present; else none.
        sp: List[_ItemSpawn] = []
        for r in range(self.h):
            for c in range(self.w):
                ch = self._grid[r][c]
                if ch in ('a', 'b', 'c', 'd'):
                    sp.append(_ItemSpawn(kind=ch, pos=(r, c), active=True))
        return sp

    # ---------------------------------------------------------------------
    # Mechanics
    # ---------------------------------------------------------------------
    def _maybe_spawn_random_pit(self) -> None:
        # Small probability per step, capped
        if len([p for p in self.pits if p.ttl_steps > 0]) >= 4:
            return
        if self._rng.random() > 0.001:
            return
        # choose a random walkable tile away from players
        tries = 0
        while tries < 60:
            tries += 1
            r = self._rng.randrange(1, self.h - 1)
            c = self._rng.randrange(1, self.w - 1)
            pos = (r, c)
            if self._grid[r][c] != '.':
                continue
            if pos in (self.p1, self.p2, self.p3):
                continue
            if abs(r - self.p1[0]) + abs(c - self.p1[1]) <= 2:
                continue
            if abs(r - self.p2[0]) + abs(c - self.p2[1]) <= 2:
                continue
            self.pits.append(_Pit(pos=pos, ttl_steps=self._rng.randint(50, 120), telegraph_steps=2))
            return

    def _tick_pits(self) -> None:
        for p in self.pits:
            if p.telegraph_steps > 0:
                p.telegraph_steps -= 1
            p.ttl_steps -= 1
        self.pits = [p for p in self.pits if p.ttl_steps > 0]

    def _pit_at(self, pos: Coord) -> Optional[_Pit]:
        for p in self.pits:
            if p.pos == pos and p.active:
                return p
        for ppos, exp in self._trap_blocks:
            if ppos == pos and self._step_i < exp:
                return _Pit(pos=pos, ttl_steps=exp - self._step_i, telegraph_steps=0)
        return None

    def _apply_pit(self, who: str, prev: Coord) -> None:
        # +1 strike, teleport back
        self.telemetry["pit_hits"] = int(self.telemetry.get("pit_hits", 0)) + 1
        self.strikes[who] = int(self.strikes.get(who, 0)) + 1
        if who in ("p1", "p2") and self.strikes[who] >= 5:
            self.winner = "p2" if who == "p1" else "p1"
            self.winner_reason = f"{who}_strikes"
        if who == "p3" and self.strikes[who] >= 50:
            # P3 removed (stays at spawn and disabled)
            self.hard_p3 = False
        # teleport
        if who == "p1":
            self.p1 = prev
        elif who == "p2":
            self.p2 = prev
        else:
            self.p3 = prev

    def _reveal(self, who: str) -> None:
        # reveal in front cell
        self.telemetry["reveal_attempts"] = int(self.telemetry.get("reveal_attempts", 0)) + 1
        fr, fc = self.face[who]
        if who == "p1":
            pr, pc = self.p1
            pid = 1
        else:
            pr, pc = self.p2
            pid = 2
        tgt = (pr + fr, pc + fc)
        for i, o in enumerate(self.objects):
            if not o.active or o.pos != tgt:
                continue
            if o.claimed_by not in (0, pid):
                return
            idx_first = self._claim_first.get(who)
            if idx_first is not None and idx_first != i:
                self._resolve_claim_if_second(who, i)
                return
            if o.claimed_by == 0:
                o.claimed_by = pid
                o.claimed_step = self._step_i
                self._claim_first[who] = i
                self.telemetry["claims"] = int(self.telemetry.get("claims", 0)) + 1
                return
            return

    def _resolve_claim_if_second(self, who: str, idx_second: int) -> None:
        # second reveal called when player has a first claim and reveals another object
        idx_first = self._claim_first[who]
        if idx_first is None or idx_first == idx_second:
            return
        o1 = self.objects[idx_first]
        o2 = self.objects[idx_second]
        if not (o1.active and o2.active):
            self._claim_first[who] = None
            return
        pid = 1 if who == "p1" else 2
        # must be claimed by pid
        if o1.claimed_by != pid:
            self._claim_first[who] = None
            return
        # attempt claim second if free
        if o2.claimed_by not in (0, pid):
            return
        o2.claimed_by = pid
        o2.claimed_step = self._step_i
        self.telemetry["second_reveals"] = int(self.telemetry.get("second_reveals", 0)) + 1
        # match?
        if o1.sym == o2.sym:
            o1.active = False
            o2.active = False
            o1.claimed_by = 0
            o2.claimed_by = 0
            self._claim_first[who] = None
            self.telemetry["matches"] = int(self.telemetry.get("matches", 0)) + 1
            self.pairs_cleared[who] = int(self.pairs_cleared.get(who, 0)) + 1
            # speed bonus
            self.speed[who] = float(self.speed.get(who, 1.0)) * 1.10
            if not any(o.active for o in self.objects):
                self.winner = who
                self.winner_reason = "pairs_cleared"
        else:
            # mismatch reset claims
            self.telemetry["mismatches"] = int(self.telemetry.get("mismatches", 0)) + 1
            o1.claimed_by = 0
            o2.claimed_by = 0
            self._claim_first[who] = None

    def _timeout_claims(self) -> None:
        for who in ("p1", "p2"):
            idx = self._claim_first[who]
            if idx is None:
                continue
            o = self.objects[idx]
            if o.claimed_step >= 0 and (self._step_i - o.claimed_step) > self.CLAIM_TIMEOUT_STEPS:
                self.telemetry["claim_timeouts"] = int(self.telemetry.get("claim_timeouts", 0)) + 1
                o.claimed_by = 0
                o.claimed_step = -1
                self._claim_first[who] = None

    def _apply_items_on_tile(self, who: str, pos: Coord) -> None:
        # pick up any active spawn on pos
        for sp in self.item_spawns:
            if not sp.active or sp.pos != pos:
                continue
            sp.active = False
            inv = self.inventory[who]
            inv[sp.kind] = int(inv.get(sp.kind, 0)) + 1
            # auto-activate simple:
            if sp.kind == 'c':
                self._speedghost_until[who] = self._step_i + 40  # ~10s @4 steps/s
            if sp.kind == 'a':
                self._trail_until[who] = self._step_i + 240  # ~60s
            if sp.kind == 'b':
                self._perm_pickup_deadline[who] = self._step_i + 120  # ~30s
            if sp.kind == 'd':
                # place a trap behind at next move via immediate placement
                pr, pc = (self.p1 if who == "p1" else self.p2)
                dr, dc = self.face[who]
                back = (pr - dr, pc - dc)
                if self._grid[back[0]][back[1]] == '.':
                    self._trap_blocks.append((back, self._step_i + 120))

    def _place_trail(self, who: str, prev: Coord) -> None:
        if self._trail_until.get(who, -1) < self._step_i:
            return
        # drop block on prev position
        exp = self._step_i + 240
        self._trail_blocks.append((prev, exp))
        self._trail_blocks = self._trail_blocks[-10:]

    def _try_place_perm(self, who: str, prev: Coord) -> None:
        if self._perm_block_used:
            return
        if self._perm_pickup_deadline.get(who, -1) < self._step_i:
            return
        # place behind current position
        pr, pc = (self.p1 if who == "p1" else self.p2)
        dr, dc = self.face[who]
        back = (pr - dr, pc - dc)
        if self._grid[back[0]][back[1]] != '.':
            return
        if back in (self.p1, self.p2):
            return
        # anti-softlock (basic): ensure at least one active object reachable for both
        blocked = set(self._perm_blocks)
        blocked.add(back)
        act_obj = [o.pos for o in self.objects if o.active]
        if not act_obj:
            return
        r1 = _bfs_reachable(self._grid, self.p1, blocked)
        r2 = _bfs_reachable(self._grid, self.p2, blocked)
        if not any(p in r1 for p in act_obj):
            return
        if not any(p in r2 for p in act_obj):
            return
        self._perm_blocks.add(back)
        self._perm_block_used = True

    def _move(self, who: str, dr: int, dc: int) -> None:
        if self.winner is not None:
            return
        if who == "p1":
            cur = self.p1
        elif who == "p2":
            cur = self.p2
        else:
            cur = self.p3
        nxt = (cur[0] + dr, cur[1] + dc)
        self.face[who] = (_sign(dr), _sign(dc)) if (dr, dc) != (0, 0) else self.face[who]
        prev = cur
        if who == "p3":
            if self._walkable(nxt):
                self.p3 = nxt
        else:
            if self._ghost_blocker_allowed(who, nxt):
                if who == "p1":
                    self.p1 = nxt
                else:
                    self.p2 = nxt
                # items pickup
                self._apply_items_on_tile(who, nxt)
                # trail drop
                self._place_trail(who, prev)
                # perm place attempt
                self._try_place_perm(who, prev)
        # pit check
        pit = self._pit_at(nxt)
        if pit is not None:
            self._apply_pit(who, prev)

    def _p3_contact(self) -> None:
        if not self.hard_p3:
            return
        if self._p3_contact_cd > 0:
            self._p3_contact_cd -= 1
            return
        if self.p3 == self.p1:
            # P3 hits P1
            self.telemetry["p3_contacts"] = int(self.telemetry.get("p3_contacts", 0)) + 1
            self.speed["p3"] = _clamp(self.speed["p3"] * 1.10, 1.0, self.P3_SPEED_CAP)
            self.speed["p1"] = _clamp(self.speed["p1"] * 0.90, self.PLAYER_SPEED_FLOOR, 10.0)
            self._p3_contact_cd = self.CONTACT_COOLDOWN_STEPS
        elif self.p3 == self.p2:
            self.telemetry["p3_contacts"] = int(self.telemetry.get("p3_contacts", 0)) + 1
            self.speed["p3"] = _clamp(self.speed["p3"] * 1.10, 1.0, self.P3_SPEED_CAP)
            self.speed["p2"] = _clamp(self.speed["p2"] * 0.90, self.PLAYER_SPEED_FLOOR, 10.0)
            self._p3_contact_cd = self.CONTACT_COOLDOWN_STEPS

    # ---------------------------------------------------------------------
    # Step
    # ---------------------------------------------------------------------
    def step(self, actions: Dict[str, str]) -> Dict[str, Any]:
        """Simulate one tick.

        actions: {'p1': 'U/D/L/R/REVEAL', 'p2': ..., 'p3': ...}
        """
        self._step_i += 1
        self._timeout_claims()
        self._tick_pits()
        self._maybe_spawn_random_pit()

        # apply moves
        for who in ("p1", "p2"):
            a = str(actions.get(who, ""))
            if a == "U":
                self._move(who, -1, 0)
            elif a == "D":
                self._move(who, 1, 0)
            elif a == "L":
                self._move(who, 0, -1)
            elif a == "R":
                self._move(who, 0, 1)
            elif a == "REVEAL":
                # try reveal second if already have first by checking forward object index
                idx_first = self._claim_first[who]
                fr, fc = self.face[who]
                pr, pc = (self.p1 if who == "p1" else self.p2)
                tgt = (pr + fr, pc + fc)
                for i, o in enumerate(self.objects):
                    if o.active and o.pos == tgt:
                        if idx_first is not None and idx_first != i:
                            self._resolve_claim_if_second(who, i)
                        else:
                            self._reveal(who)
                        break

        # p3
        if self.hard_p3:
            a3 = str(actions.get("p3", ""))
            if a3 == "U":
                self._move("p3", -1, 0)
            elif a3 == "D":
                self._move("p3", 1, 0)
            elif a3 == "L":
                self._move("p3", 0, -1)
            elif a3 == "R":
                self._move("p3", 0, 1)
        self._p3_contact()

        return self.state()

    # ---------------------------------------------------------------------
    # AI helpers for runners/UI autoplay
    # ---------------------------------------------------------------------
    def ai_action(self, who: str, eps: float = 0.1) -> str:
        if self.winner is not None:
            return ""
        legal = self.legal_actions(who)
        if who == "p3":
            # Hard-P3 uses BFS chase. The old greedy axis chase often bounced at
            # walls and generated weak pressure; BFS makes the hard variant real.
            d1 = abs(self.p3[0] - self.p1[0]) + abs(self.p3[1] - self.p1[1])
            d2 = abs(self.p3[0] - self.p2[0]) + abs(self.p3[1] - self.p2[1])
            target = self.p1 if d1 <= d2 else self.p2
            act = self._bfs_next_action("p3", [(target, "")])
            if act in legal:
                return act
            return self._rng.choice(legal or ["U", "D", "L", "R"])

        if self._rng.random() < eps:
            return self._rng.choice(legal)

        idx_ahead = self._object_ahead_index(who)
        idx_first = self._claim_first.get(who)
        if idx_ahead is not None and "REVEAL" in legal:
            # Do not repeatedly reveal the already-claimed first card. The old
            # deterministic policy/fallback loop could stand next to its first
            # claim and fire REVEAL until the claim timed out. Once a first
            # claim exists, REVEAL is useful only if the object ahead is a
            # different active blocker, so the second reveal can resolve a
            # match/mismatch.
            if idx_first is None or idx_ahead != idx_first:
                return "REVEAL"

        idx_target = self._choose_memory_target_index(who)
        if idx_target is not None:
            act = self._bfs_next_action(who, self._adjacent_reveal_poses(idx_target))
            if act:
                return act

        return self._rng.choice(legal)

    # ---------------------------------------------------------------------
    # State
    # ---------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        # render grid with objects and players (debug)
        grid = [list(row) for row in self._grid]
        for o in self.objects:
            if o.active:
                r, c = o.pos
                grid[r][c] = o.sym
        for sp in self.item_spawns:
            if sp.active:
                r, c = sp.pos
                grid[r][c] = sp.kind
        for p in self.pits:
            if p.active:
                r, c = p.pos
                grid[r][c] = "^"  # pit
            else:
                r, c = p.pos
                grid[r][c] = "!"  # telegraph
        for ppos, exp in self._trap_blocks:
            if self._step_i < exp:
                r, c = ppos
                grid[r][c] = "^"
        # players
        r, c = self.p1
        grid[r][c] = "1"
        r, c = self.p2
        grid[r][c] = "2"
        if self.hard_p3:
            r, c = self.p3
            grid[r][c] = "3"
        lines = ["".join(row) for row in grid]

        pairs_left = len({o.sym for o in self.objects if o.active})

        return {
            "ok": True,
            "step": int(self._step_i),
            "mode": "hard_p3" if self.hard_p3 else "normal",
            "winner": self.winner,
            "winner_reason": str(self.winner_reason or ""),
            "pairs_left": int(pairs_left),
            "pairs_cleared": {k: int(v) for k, v in self.pairs_cleared.items()},
            "telemetry": {k: int(v) for k, v in self.telemetry.items()},
            "speed": {k: float(v) for k, v in self.speed.items()},
            "strikes": {k: int(v) for k, v in self.strikes.items()},
            "p1": {"r": self.p1[0], "c": self.p1[1], "face": list(self.face["p1"])},
            "p2": {"r": self.p2[0], "c": self.p2[1], "face": list(self.face["p2"])},
            "p3": {"r": self.p3[0], "c": self.p3[1], "face": list(self.face["p3"]), "active": bool(self.hard_p3)},
            "grid": lines,
        }


# -----------------------------------------------------------------------------
# Embedded 81×45 maps (sym/asym) – markers:
#   '1' spawn P1, '2' spawn P2, '3' spawn P3, a/b/c/d item spawns.
#   These are walkable like '.' for the engine.
# -----------------------------------------------------------------------------

_MAP_SYM = [
"#################################################################################",
"#1.................................P...........................................#",
"#.#######.#################.###############.#################.#######.#########.#",
"#.#.....#.#...............#.#.............#.#...............#.#.....#.#.......#.#",
"#.#.###.#.#.#############.#.#.###########.#.#.#############.#.#.###.#.#.#####.#.#",
"#.#.#...#.#.....#.......#.#.#.....#.....#.#.#.#.......#...#.#.#...#.#.....#...#.#",
"#.#.#.#####.###.#.#####.#.#.#####.#.###.#.#.#.#.#####.#.###.#.#####.###.#.###.#.#",
"#.#.#.....#.#...#.....#.#...#.....#.#...#.#.#.#.....#.#...#...#.....#...#...#.#.#",
"#.#.#####.#.#.#######.#.#####.#####.#.###.#.#.#######.###.#####.#####.#####.#.#.#",
"#.#.....#.#.#.#.....#.#.....#.....#.#...#.#.#.#.....#...#.....#.....#.#.....#.#.#",
"#.#####.#.#.#.#.###.#.#####.#####.#.###.#.#.#.#.###.###.#####.#####.#.#.#####.#.#",
"#.....#.#.#.#.#...#.#.....#.....#.#.#...#...#.#...#...#.....#.....#.#.#.#.....#.#",
"#####.#.#.#.#####.#.#####.#####.#.#.#.#######.###.###.#####.#####.#.#.#.#.#####.#",
"#...#.#.#.#.....#.#.....#.....#.#.#.#.#.....#...#...#.#.....#.....#.#.#.#.....#.#",
"#.#.#.#.#.#####.#.#####.#####.#.#.#.#.#.###.###.###.#.#.#####.#####.#.#.#####.#.#",
"#.#...#.#.....#.#...#.....#...#.#.#.#.#...#...#.#...#.#...#.....#...#.#.....#...#",
"#.#####.#####.#.###.#.###.#.###.#.#.#.###.###.#.#.###.###.#.###.#.###.#####.###.#",
"#.....#.....#.#.#...#.#...#...#.#.#.#...#...#.#.#...#...#.#.#..a#...#.....#.....#",
"#.###.#####.#.#.#.###.#.#####.#.#.#.###.###.#.#.###.###.#.#.#####.###.#####.###.#",
"#.#...#...#.#.#.#...#.#.....#.#...#...#...#.#.#...#...#.#.#.....#...#.....#...#.#",
"#.#.###.#.#.#.#.###.#.#####.#.#######.###.#.#.###.###.#.#.#####.###.#####.#.###.#",
"#.#.....#.#.#.#.....#.....#.#.......#...#.#.#...#...#.#.#.....#.....#.....#.....#",
"#.#######.#.#.###########.#.#######.###.#.#.###.###.#.#.#####.###########.#######",
"#.........#.#.............#.....b...#...#...#...#...#...#...c.....#.......#.....#",
"#.#########.#################.#######.#########.#########.#######.#.#####.#.###.#",
"#.#.......#.#...............#.#.....#.#.......#.#.......#.#.....#.#.#...#.#.#...#",
"#.#.#####.#.#.#############.#.#.###.#.#.#####.#.#.#####.#.#.###.#.#.#.#.#.#.#.###",
"#.#...#...#.#.....#.......#.#.#.#...#.#.#...#.#.#.#...#.#.#...#.#.#.#.#.#.#...#.#",
"#.###.#.###.#####.#.#####.#.#.#.#.###.#.#.#.#.#.#.#.#.#.#.###.#.#.#.#.#.#.###.#.#",
"#.....#.....#.....#.....#.#.#.#.#.....#.#.#.#...#.#.#.#...#...#.#.#...#.#.....#.#",
"#.###########.###########.#.#.#.#######.#.#.#####.#.#.#####.###.#.#####.#######.#",
"#.#...........#.........#.#.#.#.......#.#.#.....#.#.#.....#...#.#.....#.......#.#",
"#.#.###########.#######.#.#.#.#######.#.#.#####.#.#.#####.###.#.#####.#####.#.#.#",
"#.#.#.....d.....#.....#.#.#.#.....#...#.#.....#.#.#.....#...#.#.....#.....#.#.#.#",
"#.#.#.###########.###.#.#.#.#####.#.###.#####.#.#.#####.###.#.#####.###.#.#.#.#.#",
"#.#.#.....#.....#...#.#.#...#.....#...#.....#.#.#.....#.....#.....#...#.#.#.#.#.#",
"#.#.#####.#.###.###.#.#.#####.#######.#####.#.#.#####.###########.###.#.#.#.#.#.#",
"#.#.....#.#.#...#...#.#.....#.......#.....#.#.#.....#.#...........#...#.#.#.#.#.#",
"#.#####.#.#.#.###.###.#####.#######.#####.#.#.#####.#.#.###########.###.#.#.#.#.#",
"#.....#...#.#...#...#.....#.....#...#.....#.#.....#.#.#.....#.......#...#...#...#",
"#.###.#####.###.###.#####.#####.#.###.#####.#####.#.#.#####.#.#####.###.#####.###",
"#.................................................................2...........#",
"#################################################################################",
]

_MAP_ASYM = [
"#################################################################################",
"#1.................P............................................................#",
"#.#######.#################.###############.#################.#######.#########.#",
"#.#.....#.#...............#.#.............#.#...............#.#.....#.#.......#.#",
"#.#.###.#.#.#############.#.#.###########.#.#.#############.#.#.###.#.#.#####.#.#",
"#.#.#...#.#.....#.......#.#.#.....#.....#.#.#.#.......#...#.#.#...#.#.....#...#.#",
"#.#.#.#####.###.#.#####.#.#.#####.#.###.#.#.#.#.#####.#.###.#.#####.###.#.###.#.#",
"#.#.#.....#.#...#.....#.#...#.....#.#...#.#.#.#.....#.#...#...#.....#...#...#.#.#",
"#.#.#####.#.#.#######.#.#####.#####.#.###.#.#.#######.###.#####.#####.#####.#.#.#",
"#.#.....#.#.#.#.....#.#.....#.....#.#...#.#.#.#.....#...#.....#.....#.#.....#.#.#",
"#.#####.#.#.#.#.###.#.#####.#####.#.###.#.#.#.#.###.###.#####.#####.#.#.#####.#.#",
"#.....#.#.#.#.#...#.#.....#.....#.#.#...#...#.#...#...#.....#.....#.#.#.#.....#.#",
"#####.#.#.#.#####.#.#####.#####.#.#.#.#######.###.###.#####.#####.#.#.#.#.#####.#",
"#...#.#.#.#.....#.#.....#.....#.#.#.#.#.....#...#...#.#.....#.....#.#.#.#.....#.#",
"#.#.#.#.#.#####.#.#####.#####.#.#.#.#.#.###.###.###.#.#.#####.#####.#.#.#####.#.#",
"#.#...#.#.....#.#...#.....#...#.#.#.#.#...#...#.#...#.#...#.....#...#.#.....#...#",
"#.#####.#####.#.###.#.###.#.###.#.#.#.###.###.#.#.###.###.#.###.#.###.#####.###.#",
"#.....#.....#.#.#...#.#...#...#.#.#.#...#...#.#.#...#...#.#.#..a#...#.....#.....#",
"#.###.#####.#.#.#.###.#.#####.#.#.#.###.###.#.#.###.###.#.#.#####.###.#####.###.#",
"#.#...#...#.#.#.#...#.#.....#.#...#...#...#.#.#...#...#.#.#.....#...#.....#...#.#",
"#.#.###.#.#.#.#.###.#.#####.#.#######.###.#.#.###.###.#.#.#####.###.#####.#.###.#",
"#.#.....#.#.#.#.....#.....#.#.......#...#.#.#...#...#.#.#.....#.....#.....#.....#",
"#.#######.#.#.###########.#.#######.###.#.#.###.###.#.#.#####.###########.#######",
"#.........#.#.............#.....b...#...#...#...#...#...#...c.....#.......#.....#",
"#.#########.#################.#######.#########.#########.#######.#.#####.#.###.#",
"#.#.......#.#...............#.#.....#.#.......#.#.......#.#.....#.#.#...#.#.#...#",
"#.#.#####.#.#.#############.#.#.###.#.#.#####.#.#.#####.#.#.###.#.#.#.#.#.#.#.###",
"#.#...#...#.#.....#.......#.#.#.#...#.#.#...#.#.#.#...#.#.#...#.#.#.#.#.#.#...#.#",
"#.###.#.###.#####.#.#####.#.#.#.#.###.#.#.#.#.#.#.#.#.#.#.###.#.#.#.#.#.#.###.#.#",
"#.....#.....#.....#.....#.#.#.#.#.....#.#.#.#...#.#.#.#...#...#.#.#...#.#.....#.#",
"#.###########.###########.#.#.#.#######.#.#.#####.#.#.#####.###.#.#####.#######.#",
"#.#...........#.........#.#.#.#.......#.#.#.....#.#.#.....#...#.#.....#.......#.#",
"#.#.###########.#######.#.#.#.#######.#.#.#####.#.#.#####.###.#.#####.#####.#.#.#",
"#.#.#.....d.....#.....#.#.#.#.....#...#.#.....#.#.#.....#...#.#.....#.....#.#.#.#",
"#.#.#.###########.###.#.#.#.#####.#.###.#####.#.#.#####.###.#.#####.###.#.#.#.#.#",
"#.#.#.....#.....#...#.#.#...#.....#...#.....#.#.#.....#.....#.....#...#.#.#.#.#.#",
"#.#.#####.#.###.###.#.#.#####.#######.#####.#.#.#####.###########.###.#.#.#.#.#.#",
"#.#.....#.#.#...#...#.#.....#.......#.....#.#.#.....#.#...........#...#.#.#.#.#.#",
"#.#####.#.#.#.###.###.#####.#######.#####.#.#.#####.#.#.###########.###.#.#.#.#.#",
"#.....#...#.#...#...#.....#.....#...#.....#.#.....#.#.#.....#.......#...#...#...#",
"#.###.#####.###.###.#####.#####.#.###.#####.#####.#.#.#####.#.#####.###.#####.###",
"#.............................................................2...........3....#",
"#################################################################################",
]

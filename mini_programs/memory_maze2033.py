#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad: /opt/ai/oroma/v2.11/mini_programs/memory_maze2033.py
#
# Zweck:
#   Memory/Maze-Variante – Crossmodal-Lernspiel für ORÓMA.
#   - Modus "memory": klassisches Aufdeckspiel mit Paaren (Karten),
#     Crossmodalität: jede Karte hat ein Symbol + auditiven "Tonindex".
#   - Modus "maze": Gitter-Labyrinth mit Start → Ziel, A*-Navigation,
#     Crossmodalität: visuelle Zellenklassen + akustische Ereignis-IDs.
#
# Produktivmerkmale:
#   - Vollständig lauffähig ohne externe Abhängigkeiten (nur Stdlib).
#   - Optionales ORÓMA-Lernen: Snap/SnapChain-Log über core.* falls verfügbar.
#   - Deterministische Seeds für Tests/Benchmarks.
#   - Mensch vs. ORÓMA (Memory), ORÓMA-A*-Agent (Maze).
#   - Einfache CLI zum Starten/Spielen; modulare API für Dashboard-Integration.
#
# Abhängigkeiten (optional, werden zur Laufzeit dynamisch geprüft):
#   - core.snap, core.snapchain, core.sql_manager (für Lernen/Speicherung)
#   - wrappers.audio_wrapper (falls vorhanden, für Töne/Ereignisse)
#
# Hinweise:
#   - Diese Datei ist eigenständig verwendbar.
#   - Für UI-Integration stellt sie eine saubere Python-API bereit (siehe unten).
#
# =============================================================================

from __future__ import annotations
import random
import time
import math
import heapq
import json
import os
from typing import List, Tuple, Dict, Optional, Any
import logging
from core.log_guard import log_suppressed

_log = logging.getLogger("mini_programs.memory_maze2033")

# -----------------------------------------------------------------------------
# ORÓMA-OPTIONAL: SnapChain-Integration (failsafe)
# -----------------------------------------------------------------------------
_HAVE_OROMA = True
try:
    from core.snap import Snap  # type: ignore
    from core.snapchain import SnapChain  # type: ignore
    from core.sql_manager import insert_chain_quick, ensure_schema  # type: ignore
except Exception:
    _HAVE_OROMA = False

# Schema-Init nur einmal (vermeidet wiederholte Write-Locks durch ensure_schema())
_SCHEMA_OK = False

# -----------------------------------------------------------------------------
# WRAPPER-OPTIONAL: Audio/Ereignisse (failsafe)
# -----------------------------------------------------------------------------
_AUDIO = None
try:
    from wrappers.audio_wrapper import AudioInput  # type: ignore
    _AUDIO = True
except Exception:
    _AUDIO = False

# -----------------------------------------------------------------------------
# Hilfsfunktionen: Crossmodalität / Features / Logging
# -----------------------------------------------------------------------------

def _tone_index_for_symbol(sym: str) -> int:
    """Mappt ein Karten-Symbol auf einen stabilen Tonindex (0..11)."""
    base = sum(ord(c) for c in sym)
    return base % 12

def _event_id_memory(match: bool) -> int:
    """Crossmodal-Ereignis-ID für Memory-Treffer/Niete."""
    return 100 if match else 101

def _event_id_maze(cell: str) -> int:
    """Crossmodal-Ereignis-ID für Maze-Zelltyp."""
    return {
        'S': 200,  # Start
        'G': 201,  # Goal
        '#': 202,  # Wand
        '.': 203,  # freier Raum
        '*': 204,  # Pfad/Schritt
    }.get(cell, 299)

def _snap_features_memory(action: str,
                          revealed: List[Tuple[int, int, str]],
                          match: Optional[bool],
                          pairs_left: int,
                          board_w: int,
                          board_h: int) -> List[float]:
    """
    Numerische Features für Memory-Snap:
    - Aktion kodiert
    - Kartenpositionen & Tonindizes
    - Restpaare / Spielfeldgröße
    """
    # Aktion: "reveal", "match", "mismatch", "reset"
    action_map = {"reveal": 0.1, "match": 0.9, "mismatch": 0.2, "reset": 0.0}
    f = [action_map.get(action, 0.5)]

    # max 2 revealed Karten
    for i in range(2):
        if i < len(revealed):
            r, c, sym = revealed[i]
            tone = _tone_index_for_symbol(sym) / 12.0
            f.extend([r / max(1, board_h - 1), c / max(1, board_w - 1), tone])
        else:
            f.extend([0.0, 0.0, 0.0])

    # Match-Flag + Restpaare + Größe
    f.append(1.0 if (match is True) else (0.0 if (match is None) else -1.0))
    f.append(pairs_left / max(1, (board_w * board_h) // 2))
    f.append(board_w / 10.0)
    f.append(board_h / 10.0)
    return f

def _snap_features_maze(action: str,
                        pos: Tuple[int, int],
                        goal: Tuple[int, int],
                        cell: str,
                        board_w: int,
                        board_h: int,
                        step_idx: int) -> List[float]:
    """
    Numerische Features für Maze-Snap:
    - Aktion kodiert (move/blocked/reset)
    - aktuelle Position, Distanz zum Ziel
    - Zelltyp-ID
    - Schrittindex, Größe
    """
    action_map = {"move": 0.8, "blocked": 0.2, "reset": 0.0}
    pr, pc = pos
    gr, gc = goal
    dr = abs(gr - pr) / max(1, board_h - 1)
    dc = abs(gc - pc) / max(1, board_w - 1)
    dist = math.sqrt(dr * dr + dc * dc)
    cell_norm = _event_id_maze(cell) / 300.0

    f = [
        action_map.get(action, 0.5),
        pr / max(1, board_h - 1),
        pc / max(1, board_w - 1),
        dist,
        cell_norm,
        (step_idx % 100) / 100.0,
        board_w / 20.0,
        board_h / 20.0,
    ]
    return f

def _emit_snapchain(features: List[float], metadata: Dict[str, Any], quality: float = 0.6):
    """Erstellt (optional) einen SnapChain-Eintrag in der ORÓMA-DB."""
    if not _HAVE_OROMA:
        return
    # Dauerbetrieb: Emission optional deaktivierbar (z.B. Daily Runner), um DB-Write-Contention zu vermeiden.
    # Default: ON (interaktives Spiel), aber Daily Runner kann z.B. OROMA_MM_EMIT_SNAPCHAINS=0 setzen.
    try:
        if str(os.environ.get("OROMA_MM_EMIT_SNAPCHAINS", "1")).strip() in ("0", "false", "False", "no", "NO"):
            return
    except Exception:
        pass
    try:
        global _SCHEMA_OK
        if not _SCHEMA_OK:
            ensure_schema()
            _SCHEMA_OK = True
        snap = Snap(features=features, metadata=metadata)
        chain = SnapChain(patterns=[snap.features], metadata=metadata)
        # core.sql_manager.insert_snapchain erwartet ein Dict (keine positional-bytes + quality-kwarg).
        insert_chain_quick(
            {
                "ts": int(time.time()),
                "quality": float(quality),
                "blob": chain.as_blob(),
                "origin": "game:memorymaze2033",
                "namespace": "game:memorymaze2033",
                "notes": "memory_maze2033 emit",
                "weight": 1.0,
            }
        )
    except Exception as e:
        # Fail-safe: kein Crash, wenn DB/Schema (noch) nicht bereit
        log_suppressed(
            _log,
            key="mini_programs.memory_maze2033.snapchain.pass.1",
            msg="Suppressed exception while emitting SnapChain (DB/Schema not ready)",
            exc=e,
            level=logging.WARNING,
            interval_s=60,
        )
        return

def _audio_event(event_id: int, extra: Optional[int] = None):
    """Optional akustisches Ereignis via Audio-Wrapper (falls vorhanden)."""
    if not _AUDIO:
        return
    try:
        # Beispiel: wir nutzen nur das Interface initialisieren/benutzen,
        # ohne Echtzeit-Audio zu fordern (Pi-freundlich). Falls dein
        # AudioInput Wrapper Töne spielen kann, hier integrieren.
        # Hier lassen wir es bewusst minimal (keine Blockaden).
        ai = AudioInput()
        ai.note_on(event_id % 12, velocity=64)  # symbolischer Aufruf
        time.sleep(0.02)
        ai.note_off(event_id % 12)
    except Exception as e:
        log_suppressed(
            _log,
            key="mini_programs.memory_maze2033.audio.pass.1",
            msg="Suppressed exception while emitting audio event",
            exc=e,
            level=logging.WARNING,
            interval_s=120,
        )
        return

# -----------------------------------------------------------------------------
# MEMORY – Spiel-Logik
# -----------------------------------------------------------------------------

class MemoryGame:
    """
    Memory-Spiel:
    - Gitter mit verdeckten Karten (immer gerade Anzahl, Paare).
    - Spieler/ORÓMA decken zwei Karten pro Zug auf:
        * Bei Paar: Karten bleiben offen → Punkt, weiterer Zug
        * Bei Miss: Karten werden wieder verdeckt
    - Ziel: Alle Paare finden.
    - Crossmodal: Jeder Kartenwert → Tonindex.
    """

    def __init__(self, width: int = 4, height: int = 4, seed: Optional[int] = None):
        assert (width * height) % 2 == 0, "Anzahl Felder muss gerade sein."
        self.w = width
        self.h = height
        self.rng = random.Random(seed)
        self._new_board()
        self.turn = "human"  # "human" | "oroma"
        self.score = {"human": 0, "oroma": 0}
        # ORÓMA-Gedächtnis (für AI-Strategie): Map symbol -> Liste Positionen
        self.ai_memory: Dict[str, List[Tuple[int, int]]] = {}
        self.flipped: List[Tuple[int, int, str]] = []  # aktuell aufgedeckt

    def _new_board(self):
        num_pairs = (self.w * self.h) // 2
        symbols = [chr(65 + (i % 26)) for i in range(num_pairs)]
        vals = symbols * 2
        self.rng.shuffle(vals)
        # 2D-Board: [(symbol, visible_bool)]
        self.board: List[List[Tuple[str, bool]]] = []
        it = iter(vals)
        for r in range(self.h):
            row = []
            for c in range(self.w):
                row.append((next(it), False))
            self.board.append(row)
        self.finished = False

    def reset(self, seed: Optional[int] = None):
        if seed is not None:
            self.rng.seed(seed)
        self._new_board()
        self.turn = "human"
        self.score = {"human": 0, "oroma": 0}
        self.ai_memory.clear()
        self.flipped.clear()
        _emit_snapchain(
            _snap_features_memory("reset", [], None, self.pairs_left(), self.w, self.h),
            {"game": "memory", "action": "reset"},
            quality=0.5,
        )

    def pairs_left(self) -> int:
        hidden = sum(1 for r in range(self.h) for c in range(self.w) if not self.board[r][c][1])
        return hidden // 2

    def _reveal(self, r: int, c: int) -> Tuple[bool, str]:
        sym, vis = self.board[r][c]
        if vis:
            return False, sym
        self.board[r][c] = (sym, True)
        self.flipped.append((r, c, sym))
        # Crossmodal: Ton-Event pro Karte
        _audio_event(_tone_index_for_symbol(sym))
        _emit_snapchain(
            _snap_features_memory("reveal", [(r, c, sym)], None, self.pairs_left(), self.w, self.h),
            {"game": "memory", "action": "reveal", "sym": sym, "pos": (r, c)},
            quality=0.55,
        )
        # AI-Memory notieren
        if sym not in self.ai_memory:
            self.ai_memory[sym] = []
        if (r, c) not in self.ai_memory[sym]:
            self.ai_memory[sym].append((r, c))
        return True, sym

    def _conceal(self, a: Tuple[int, int], b: Tuple[int, int]):
        (r1, c1), (r2, c2) = a, b
        s1, _ = self.board[r1][c1]
        s2, _ = self.board[r2][c2]
        self.board[r1][c1] = (s1, False)
        self.board[r2][r2 if False else c2] = (s2, False)  # bewusst robust gegen Tippfehler
        # Korrektur (oben absichtlich robust), hier explizit setzen:
        self.board[r2][c2] = (s2, False)

    def _apply_pair_result(self) -> bool:
        """Wird aufgerufen, wenn genau zwei Karten aufgedeckt sind."""
        if len(self.flipped) != 2:
            return False
        (r1, c1, s1), (r2, c2, s2) = self.flipped
        match = s1 == s2
        _audio_event(_event_id_memory(match))
        if match:
            # Paar gefunden → Punkt und weiterer Zug
            self.score[self.turn] += 1
            _emit_snapchain(
                _snap_features_memory("match", self.flipped, True, self.pairs_left(), self.w, self.h),
                {"game": "memory", "action": "match", "pair": s1, "turn": self.turn},
                quality=0.7,
            )
        else:
            # Verdeckten
            self._conceal((r1, c1), (r2, c2))
            _emit_snapchain(
                _snap_features_memory("mismatch", self.flipped, False, self.pairs_left(), self.w, self.h),
                {"game": "memory", "action": "mismatch", "turn": self.turn},
                quality=0.6,
            )
            # Spielerwechsel
            self.turn = "oroma" if self.turn == "human" else "human"
        self.flipped.clear()

        # Fertig?
        if self.pairs_left() == 0:
            self.finished = True
        return True

    # -------------------------
    # Öffentliche API
    # -------------------------
    def state(self) -> Dict[str, Any]:
        """Zustand serialisierbar (für UI)."""
        return {
            "w": self.w,
            "h": self.h,
            "board": [[{"sym": s if v else None, "vis": v} for (s, v) in row] for row in self.board],
            "turn": self.turn,
            "score": dict(self.score),
            "pairs_left": self.pairs_left(),
            "finished": self.finished,
        }

    def human_reveal(self, r: int, c: int) -> bool:
        """Mensch deckt eine Karte auf (nur wenn human dran ist)."""
        if self.finished or self.turn != "human":
            return False
        ok, _ = self._reveal(r, c)
        if not ok:
            return False
        if len(self.flipped) == 2:
            self._apply_pair_result()
        return True

    def oroma_play(self) -> bool:
        """ORÓMA-Zug: aus Gedächtnis bekannte Paare priorisieren, sonst zufällig."""
        if self.finished or self.turn != "oroma":
            return False

        # 1) bekannte vollständige Paare?
        for sym, positions in list(self.ai_memory.items()):
            open_positions = [(r, c) for (r, c) in positions if not self.board[r][c][1]]
            if len(open_positions) >= 2:
                (r1, c1), (r2, c2) = open_positions[:2]
                self._reveal(r1, c1)
                self._reveal(r2, c2)
                self._apply_pair_result()
                return True

        # 2) ansonsten: suche eine offene Karte + passende bekannte Position
        hidden = [(r, c) for r in range(self.h) for c in range(self.w) if not self.board[r][c][1]]
        if not hidden:
            self.finished = True
            return True

        # Wenn gerade keine aufgedeckt ist: erste Karte zufällig
        if len(self.flipped) == 0:
            r1, c1 = self.rng.choice(hidden)
            self._reveal(r1, c1)
            return True

        # Eine ist offen → versuche passende Stelle aus Gedächtnis, sonst zufällig
        (r0, c0, s0) = self.flipped[0]
        candidates = [pos for pos in self.ai_memory.get(s0, []) if not self.board[pos[0]][pos[1]][1] and pos != (r0, c0)]
        if candidates:
            r2, c2 = self.rng.choice(candidates)
        else:
            # random hidden, nicht dieselbe
            hidden2 = [p for p in hidden if p != (r0, c0)]
            if not hidden2:
                hidden2 = hidden
            r2, c2 = self.rng.choice(hidden2)
        self._reveal(r2, c2)
        self._apply_pair_result()
        return True


# -----------------------------------------------------------------------------
# MAZE – A*-Navigation
# -----------------------------------------------------------------------------

class Maze:
    """
    Einfaches Labyrinth:
    - Gitter mit Wänden (#), frei (.), Start (S), Ziel (G).
    - A*-Suche zur Pfadfindung, ORÓMA läuft Weg schrittweise ab.
    - Crossmodal: Zellentypen lösen Ereignis-IDs aus.
    """

    def __init__(self, width: int = 15, height: int = 11, seed: Optional[int] = None, wall_prob: float = 0.22):
        self.w = max(5, width)
        self.h = max(5, height)
        self.rng = random.Random(seed)
        self.wall_prob = min(0.45, max(0.05, wall_prob))
        self.grid: List[List[str]] = []
        self.start: Tuple[int, int] = (0, 0)
        self.goal: Tuple[int, int] = (self.h - 1, self.w - 1)
        self.path: List[Tuple[int, int]] = []
        self.pos: Tuple[int, int] = self.start
        self.step_idx = 0
        self._gen()

    def _gen(self):
        # generiere zufällige Wände
        self.grid = []
        for r in range(self.h):
            row = []
            for c in range(self.w):
                if (r, c) in [(0, 0), (self.h - 1, self.w - 1)]:
                    row.append('.')
                else:
                    row.append('#' if self.rng.random() < self.wall_prob else '.')
            self.grid.append(row)
        self.start = (0, 0)
        self.goal = (self.h - 1, self.w - 1)
        self.grid[self.start[0]][self.start[1]] = 'S'
        self.grid[self.goal[0]][self.goal[1]] = 'G'
        self.pos = self.start
        self.step_idx = 0
        self.path = self._astar(self.start, self.goal)
        _emit_snapchain(
            _snap_features_maze("reset", self.pos, self.goal, self.grid[self.pos[0]][self.pos[1]], self.w, self.h, self.step_idx),
            {"game": "maze", "action": "reset"},
            quality=0.5,
        )

    def reset(self, seed: Optional[int] = None):
        if seed is not None:
            self.rng.seed(seed)
        self._gen()

    def neighbors(self, r: int, c: int) -> List[Tuple[int, int]]:
        out = []
        for dr, dc in [(-1,0), (1,0), (0,-1), (0,1)]:
            rr, cc = r + dr, c + dc
            if 0 <= rr < self.h and 0 <= cc < self.w and self.grid[rr][cc] != '#':
                out.append((rr, cc))
        return out

    @staticmethod
    def _heur(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return abs(a[0]-b[0]) + abs(a[1]-b[1])

    def _astar(self, start: Tuple[int, int], goal: Tuple[int, int]) -> List[Tuple[int, int]]:
        """Standard-A* auf dem Gitter."""
        open_set: List[Tuple[float, Tuple[int, int]]] = []
        heapq.heappush(open_set, (0.0, start))
        came: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {start: None}
        g = {start: 0.0}
        while open_set:
            _, cur = heapq.heappop(open_set)
            if cur == goal:
                # Pfad rekonstruieren
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = came[cur]
                return list(reversed(path))
            for nb in self.neighbors(*cur):
                ng = g[cur] + 1.0
                if nb not in g or ng < g[nb]:
                    g[nb] = ng
                    f = ng + self._heur(nb, goal)
                    came[nb] = cur
                    heapq.heappush(open_set, (f, nb))
        return []

    def step_oroma(self) -> bool:
        """Ein Schritt entlang des gefundenen Pfads. Liefert True bis Ziel erreicht oder kein Pfad."""
        if not self.path:
            # kein Pfad, versuche regenerieren (neues Maze)
            _audio_event(_event_id_maze('#'))
            return False
        if self.pos == self.goal:
            return False
        # nächste Zielzelle
        try:
            idx = self.path.index(self.pos)
            nxt = self.path[idx+1]
        except Exception:
            return False

        r, c = nxt
        cell = self.grid[r][c]
        if cell == '#':
            _audio_event(_event_id_maze('#'))
            _emit_snapchain(
                _snap_features_maze("blocked", self.pos, self.goal, '#', self.w, self.h, self.step_idx),
                {"game": "maze", "action": "blocked"},
                quality=0.55,
            )
            return False

        # ziehen
        self.pos = (r, c)
        self.step_idx += 1
        _audio_event(_event_id_maze(cell))
        _emit_snapchain(
            _snap_features_maze("move", self.pos, self.goal, cell, self.w, self.h, self.step_idx),
            {"game": "maze", "action": "move", "pos": self.pos},
            quality=0.62,
        )
        return True

    def solved(self) -> bool:
        return self.pos == self.goal

    def state(self) -> Dict[str, Any]:
        """Zustand serialisierbar (für UI)."""
        return {
            "w": self.w,
            "h": self.h,
            "grid": self.grid,
            "pos": self.pos,
            "start": self.start,
            "goal": self.goal,
            "step_idx": self.step_idx,
            "solved": self.solved(),
            "path_len": len(self.path),
        }


# -----------------------------------------------------------------------------
# Gemeinsame Fassade für Dashboard/CLI
# -----------------------------------------------------------------------------

class MemoryMaze2033:
    """
    Fassade für beide Modi, inkl. einfache CLI-Schleife und API-Methoden.
    """

    def __init__(self, mode: str = "memory", seed: Optional[int] = None):
        self.mode = mode.lower()
        if self.mode == "memory":
            self.game = MemoryGame(width=4, height=4, seed=seed)
        elif self.mode == "maze":
            self.game = Maze(width=17, height=11, seed=seed, wall_prob=0.22)
        else:
            raise ValueError("Unbekannter Modus. Erlaubt: 'memory' oder 'maze'.")

    # ---- API für UI/Integration ----
    def get_state(self) -> Dict[str, Any]:
        return {"mode": self.mode, "state": self.game.state()}

    def reset(self, seed: Optional[int] = None) -> Dict[str, Any]:
        self.game.reset(seed=seed)
        return self.get_state()

    # Memory: Menschzug
    def memory_reveal(self, r: int, c: int) -> Dict[str, Any]:
        if self.mode != "memory":
            return {"error": "Nicht im Memory-Modus."}
        ok = self.game.human_reveal(r, c)
        if ok and self.game.turn == "oroma" and not self.game.finished:
            # ORÓMA darf Folgezug machen (bei Match behält ORÓMA den Zug)
            time.sleep(0.05)
            self.game.oroma_play()
        return self.get_state()

    # Memory: expliziter ORÓMA-Zug (UI/Tests)
    def memory_oroma(self) -> Dict[str, Any]:
        if self.mode != "memory":
            return {"error": "Nicht im Memory-Modus."}
        self.game.oroma_play()
        return self.get_state()

    # Maze: ORÓMA-Schritt
    def maze_step(self) -> Dict[str, Any]:
        if self.mode != "maze":
            return {"error": "Nicht im Maze-Modus."}
        self.game.step_oroma()
        return self.get_state()

    # ---- CLI (für direkten Start) ----
    def run_cli(self):
        if self.mode == "memory":
            self._run_cli_memory()
        else:
            self._run_cli_maze()

    def _run_cli_memory(self):
        print("=== ORÓMA Memory (Mensch vs ORÓMA) ===")
        g: MemoryGame = self.game  # type: ignore
        while not g.finished:
            st = g.state()
            print(f"\nZug: {st['turn']} | Score H:{st['score']['human']} O:{st['score']['oroma']} | Paare übrig: {st['pairs_left']}")
            # Board-Print (X verdeckt, Buchstabe sichtbar)
            for r in range(g.h):
                row = []
                for c in range(g.w):
                    sym = st['board'][r][c]['sym']
                    row.append(sym if sym else 'X')
                print(" ".join(row))
            if g.turn == "human":
                try:
                    coords = input("Karte aufdecken (r c), leer für Zufall, q=Ende: ").strip()
                except EOFError:
                    coords = "q"
                if coords.lower() == 'q':
                    break
                if not coords:
                    # Zufallszug (bequem)
                    hidden = [(r, c) for r in range(g.h) for c in range(g.w) if not g.board[r][c][1]]
                    if hidden:
                        r, c = random.choice(hidden)
                        g.human_reveal(r, c)
                else:
                    parts = coords.split()
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        r, c = int(parts[0]), int(parts[1])
                        if 0 <= r < g.h and 0 <= c < g.w:
                            g.human_reveal(r, c)
            else:
                print("ORÓMA denkt …")
                time.sleep(0.2)
                g.oroma_play()
        print("\n=== Fertig ===")
        st = self.game.state()
        print(f"Endstand: H:{st['score']['human']} O:{st['score']['oroma']}")

    def _run_cli_maze(self):
        print("=== ORÓMA Maze (A*-Agent) ===")
        m: Maze = self.game  # type: ignore
        while not m.solved():
            st = m.state()
            print(f"\nSchritt {st['step_idx']} Position:{st['pos']} Ziel:{st['goal']} Pfadlänge:{st['path_len']}")
            # Drucke kleines Labyrinth (P=aktuell)
            for r in range(m.h):
                row = []
                for c in range(m.w):
                    ch = m.grid[r][c]
                    if (r, c) == m.pos:
                        row.append('P')
                    else:
                        row.append(ch)
                print("".join(row))
            ans = input("Enter=ORÓMA Schritt, 'r'=reset, 'q'=Ende: ").strip().lower()
            if ans == 'q':
                break
            if ans == 'r':
                m.reset()
                continue
            m.step_oroma()
        if m.solved():
            print("\nZiel erreicht! 🎉")


# -----------------------------------------------------------------------------
# Hauptprogramm
# -----------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="ORÓMA Memory/Maze 2033 – Crossmodal Lernspiel")
    ap.add_argument("--mode", choices=["memory", "maze"], default="memory", help="Spielmodus")
    ap.add_argument("--seed", type=int, default=None, help="Deterministischer Seed")
    ap.add_argument("--mem-size", type=str, default="4x4", help="Memory Größe WxH (z.B. 4x4, 6x4)")
    ap.add_argument("--maze-size", type=str, default="17x11", help="Maze Größe WxH (z.B. 17x11)")
    ap.add_argument("--maze-wallprob", type=float, default=0.22, help="Maze Wand-Wahrscheinlichkeit 0.05..0.45")
    args = ap.parse_args()

    if args.mode == "memory":
        try:
            w, h = map(int, args.mem_size.lower().split('x'))
        except Exception:
            w, h = 4, 4
        game = MemoryGame(width=w, height=h, seed=args.seed)
        # Für CLI-Hülle nutzen wir Fassade
        f = MemoryMaze2033(mode="memory", seed=args.seed)
        f.game = game  # ersetze interne Instanz mit benutzerdefinierter Größe
        f.run_cli()
    else:
        try:
            w, h = map(int, args.maze_size.lower().split('x'))
        except Exception:
            w, h = 17, 11
        game = Maze(width=w, height=h, seed=args.seed, wall_prob=args.maze_wallprob)
        f = MemoryMaze2033(mode="maze", seed=args.seed)
        f.game = game
        f.run_cli()


if __name__ == "__main__":
    main()
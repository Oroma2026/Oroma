# /opt/ai/oroma/v2.11/mini_programs/flappybird.py
# -*- coding: utf-8 -*-
"""
FlappyBird (headless) – Mini-Programm für ORÓMA v2.11
-----------------------------------------------------
- Keine externen Dependencies (kein pygame)
- Geeignet für RL / SnapChain-Lernen
- Einfache 1D-Physik + Pipes (Rechtecke mit Lücke)
- Aktionen: 0 = nichts, 1 = flap (kurzer Impuls nach oben)

State (continuous):
    y                 # vertikale Position des Vogels (0..1, 0 = Boden, 1 = Decke)
    vy                # vertikale Geschwindigkeit (negativ = aufwärts)
    dx                # Distanz zur nächsten Pipe (0..1, 0 = Pipe beim Vogel)
    gap_y             # vertikale Mitte der Lücke (0..1)
    gap_h             # Höhe der Lücke (0..1)
    score_norm        # normalisierte Punktzahl (Score / 100)

Reward:
    +0.1 pro Zeitschritt am Leben
    +1.0 bei jeder passierten Pipe
    -1.0 bei Kollision (Episode Ende)

Episodenende:
    Kollision mit Boden/Decke oder Pipe -> done=True

Integration in ORÓMA:
    - get_state() -> dict (JSON) für UI
    - features() -> List[float] für Snap/SnapChain (core.snap.Snap)
    - render_ascii(width=30,height=12) -> einfache Text-Visualisierung

Lizenz: MIT
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import random
import time
from typing import Dict, List, Optional, Tuple

# ----------------------------
# Konfiguration / Defaults
# ----------------------------

@dataclass
class FBConfig:
    gravity: float = 1.5        # Beschleunigung nach unten (pro Sekunde^2, in normierten Einheiten)
    flap_impulse: float = -0.6  # sofortige Änderung an vy beim Flap (negativ = nach oben)
    pipe_speed: float = 0.45    # Geschwindigkeit der Pipes (pro Sekunde, normierte Einheiten)
    pipe_gap: float = 0.25      # Höhe der Lücke (in [0..1])
    pipe_min_gap: float = 0.20  # minimale Lücke bei Schwierigkeitsanstieg
    pipe_spawn_dx: float = 1.0  # Startabstand der nächsten Pipe (normiert)
    pipe_interval: float = 0.85 # horizontale Distanz zwischen Pipes (je kleiner, desto mehr Pipes)
    dt: float = 0.05            # Zeitschritt in Sekunden (~20 FPS)
    max_steps: int = 10_000     # Sicherheitslimit
    difficulty_rate: float = 0.002  # Lückenreduktion pro passierter Pipe (bis pipe_min_gap)
    bird_radius: float = 0.04   # Kollisionsradius des Vogels (normiert)
    pipe_width: float = 0.12    # horizontale Breite einer Pipe (normiert)
    seed: Optional[int] = None  # RNG-Seed für deterministische Läufe


@dataclass
class FBState:
    y: float
    vy: float
    dx: float
    gap_y: float
    gap_h: float
    score: int
    alive: bool
    steps: int


class FlappyBird:
    """
    Headless Flappy-Bird-Umgebung.

    Aktionen:
        0 = nichts
        1 = flap

    Schnittstellen:
        reset(seed: Optional[int]) -> FBState
        step(action: int) -> (FBState, reward: float, done: bool, info: dict)
        get_state() -> dict
        features() -> List[float]   # für Snap/SnapChain
        render_ascii(width=30,height=12) -> str
    """

    def __init__(self, cfg: Optional[FBConfig] = None):
        self.cfg = cfg or FBConfig()
        self.rng = random.Random(self.cfg.seed or int(time.time() * 1e6) & 0xffffffff)
        self.reset(seed=self.cfg.seed)

    # ----------------------------
    # Core
    # ----------------------------

    def reset(self, seed: Optional[int] = None) -> FBState:
        if seed is not None:
            self.rng.seed(seed)

        # Startzustand
        self.y = 0.5
        self.vy = 0.0

        # Pipe-Setup
        self.dx = self.cfg.pipe_spawn_dx
        self.gap_y = self._rand_gap_y()
        self.gap_h = self.cfg.pipe_gap

        self.score = 0
        self.alive = True
        self.steps = 0

        return self._mk_state()

    def step(self, action: int) -> Tuple[FBState, float, bool, Dict]:
        """
        action: 0=nichts, 1=flap
        """
        if not self.alive:
            # Episode ist bereits vorbei
            return self._mk_state(), 0.0, True, {"reason": "dead"}

        self.steps += 1
        if self.steps > self.cfg.max_steps:
            self.alive = False
            return self._mk_state(), 0.0, True, {"reason": "max_steps"}

        dt = self.cfg.dt

        # Eingabe
        if action == 1:
            self.vy += self.cfg.flap_impulse

        # Physik
        self.vy += self.cfg.gravity * dt
        self.y += self.vy * dt

        # Pipe-Bewegung
        self.dx -= self.cfg.pipe_speed * dt

        reward = 0.1  # Schritt überlebt → kleine positive Belohnung

        # Pipe passiert?
        passed = False
        if self.dx <= 0.0:
            # Vogel hat die Pipe „hinter sich gelassen“ → Score++
            self.score += 1
            reward += 1.0
            passed = True
            # Neue Pipe spawnen
            self.dx += self.cfg.pipe_interval + self.cfg.pipe_width
            self.gap_y = self._rand_gap_y()
            # Schwierigkeit leicht erhöhen: Lücke verkleinern
            self.gap_h = max(self.cfg.pipe_min_gap, self.gap_h - self.cfg.difficulty_rate)

        # Kollisionen: Welt
        if self.y < 0.0 or self.y > 1.0:
            self.alive = False
            reward -= 1.0
            return self._mk_state(), reward, True, {"reason": "world_collision", "passed": passed}

        # Kollisionen: Pipe
        if self._collides_with_pipe():
            self.alive = False
            reward -= 1.0
            return self._mk_state(), reward, True, {"reason": "pipe_collision", "passed": passed}

        # Weiterleben
        return self._mk_state(), reward, False, {"passed": passed}

    # ----------------------------
    # Helpers / Features / Rendering
    # ----------------------------

    def _mk_state(self) -> FBState:
        return FBState(
            y=self._clip(self.y, 0.0, 1.0),
            vy=self.vy,
            dx=self._clip(self.dx, 0.0, 1.2),
            gap_y=self._clip(self.gap_y, 0.0, 1.0),
            gap_h=self._clip(self.gap_h, 0.05, 0.6),
            score=self.score,
            alive=bool(self.alive),
            steps=int(self.steps),
        )

    def get_state(self) -> Dict:
        s = self._mk_state()
        d = asdict(s)
        d["score_norm"] = min(1.0, s.score / 100.0)
        return d

    def features(self) -> List[float]:
        """
        Liefert SnapFeatures (normiert, bounded) fürs ORÓMA-Speichern:
            [ y, vy_tanh, dx, gap_y, gap_h, score_norm ]
        """
        s = self._mk_state()
        vy_tanh = math.tanh(s.vy)  # begrenzen
        score_norm = min(1.0, s.score / 100.0)
        return [s.y, vy_tanh, s.dx, s.gap_y, s.gap_h, score_norm]

    def render_ascii(self, width: int = 30, height: int = 12) -> str:
        """
        Einfache Textdarstellung (für Logs / Debug).
        y=0 ist unten; wir zeichnen top->down (invertieren für Anzeige).
        """
        # Canvas
        grid = [[" " for _ in range(width)] for _ in range(height)]

        # Vogel
        y_px = height - 1 - int(self._clip(self.y, 0.0, 1.0) * (height - 1))
        x_bird = width // 3
        if 0 <= y_px < height:
            grid[y_px][x_bird] = "◉"

        # Pipe
        # Pipe-Rechteck bewegt sich von rechts nach links, dx=Distanz bis zum Vogel-x
        pipe_x_center = x_bird + int(self.dx * width)
        w = max(1, int(self.cfg.pipe_width * width))
        left = pipe_x_center - w // 2
        right = pipe_x_center + w // 2

        gap_center = height - 1 - int(self._clip(self.gap_y, 0.0, 1.0) * (height - 1))
        gap_half = max(1, int(self._clip(self.gap_h, 0.05, 0.9) * height / 2))

        for x in range(left, right + 1):
            if 0 <= x < width:
                for y in range(height):
                    if not (gap_center - gap_half <= y <= gap_center + gap_half):
                        grid[y][x] = "█"

        # Rahmen + Score
        border_top = "┌" + "─" * width + "┐"
        border_bot = "└" + "─" * width + "┘"
        lines = ["".join(row) for row in grid]
        box = [border_top] + [f"│{ln}│" for ln in lines] + [border_bot]
        box.append(f"score={self.score}  y={self.y:.2f}  vy={self.vy:.2f}  dx={self.dx:.2f}")
        return "\n".join(box)

    # ----------------------------
    # Interne Utilities
    # ----------------------------

    def _rand_gap_y(self) -> float:
        # halte Lücke zur Mitte hin leicht bevorzugt
        return min(0.9, max(0.1, 0.5 + self.rng.uniform(-0.3, 0.3)))

    @staticmethod
    def _clip(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _collides_with_pipe(self) -> bool:
        """
        Kreis vs. AABB (Pipe ohne Lücke), 1D-vereinfachte Prüfung:
        Wir approximieren: Kollision wenn Vogel-x im Pipe-x-Fenster
        und y außerhalb der Lücke (±bird_radius).
        """
        # Liegt Pipe in Nähe des Vogel-x?
        # Vogel-x ist fix bei ~1/3 der Breite → Kollision nur, wenn Pipe dort ist
        # dx=0 → Pipe-Mitte beim Vogel; für Kollision tolerieren wir die halbe Breite
        half_w = self.cfg.pipe_width / 2.0
        in_x = abs(self.dx) <= half_w

        if not in_x:
            return False

        gap_top = self.gap_y + (self.gap_h / 2.0)
        gap_bot = self.gap_y - (self.gap_h / 2.0)

        # Vogel außerhalb der Lücke (mit Radius)
        if self.y + self.cfg.bird_radius < gap_bot:
            return True
        if self.y - self.cfg.bird_radius > gap_top:
            return True
        return False


# -----------------------------------------
# Komfort: kleine CLI zum lokalen Testen
# -----------------------------------------

def _demo():
    env = FlappyBird()
    s = env.reset()
    print(env.render_ascii())
    total = 0.0
    for t in range(200):
        # simple Heuristik: wenn unterhalb der Lückenmitte → flap
        action = 1 if env.y < (env.gap_y - 0.05) else 0
        s, r, done, info = env.step(action)
        total += r
        if t % 3 == 0:
            print(env.render_ascii())
        if done:
            print("DONE:", info, "score:", s.score, "return:", round(total, 3))
            break

if __name__ == "__main__":
    _demo()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/mini_programs/ptz_target.py
# Projekt:   ORÓMA (PTZ Targeting · RL-Env)
# Version:   v3.7.3
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# Zweck
# ─────
#   "PTZ Targeting" ist ein RL-Spiel/Env, das der PTZ-Policy ein starkes,
#   zielorientiertes Signal liefert: "bringe ein bewegtes Ziel in die Bildmitte
#   und halte es dort".
#
#   Motivation
#   ──────────
#   - PTZ-Arena (sharp/motion) ist ein guter Start, aber noch relativ "blind".
#   - Targeting liefert ein konkretes Distanz-Ziel (dx/dy) -> schneller Policy-Boost.
#
#   Design-Entscheidungen
#   ────────────────────
#   - Keine zusätzlichen ML-Modelle nötig: Target = Motion-Centroid aus
#     Frame-Differenz (2 Frames).
#   - Headless/robust: Frames kommen über die laufende ORÓMA HTTP-API
#     (identisch zur Video-UI): /video/snapshot.jpg.
#   - Kleine Action-Space: left/right/up/down/zoom_in/zoom_out/hold.
#
# API
# ───
#   Diese Datei stellt nur das Env bereit. UI/Runner kapseln HTTP/Policy/DB.
#   Das Env ist bewusst "dumm" (kein eigener Thread, kein Persist).
#
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TargetObs:
    ok: bool
    dx: float
    dy: float
    dist: float
    strength: float
    motion: float
    sharp: float
    pan: int
    tilt: int
    zoom: int


@dataclass
class TargetStep:
    obs: TargetObs
    reward: float
    done: bool
    info: Dict[str, Any]


class PTZTargetEnv:
    """Ein kleines Targeting-Env.

    Der eigentliche Frame-Zugriff und die PTZ-Kommandos laufen außerhalb
    dieses Envs (UI/Runner). Dieses Env bewertet nur:
      - Target-Offset (dx/dy/dist)
      - Reward aus Distanz-Verbesserung
      - Done-Kriterien (Lock-on oder max_steps)
    """

    def __init__(
        self,
        max_steps: int = 40,
        move_cost: float = 0.01,
        lock_dist: float = 0.18,
        lock_min_strength: float = 0.20,
        lock_hold_steps: int = 6,
    ) -> None:
        self.max_steps = int(max(1, max_steps))
        self.move_cost = float(move_cost)
        self.lock_dist = float(lock_dist)
        self.lock_min_strength = float(lock_min_strength)
        self.lock_hold_steps = int(max(1, lock_hold_steps))

        self.steps = 0
        self._lock_count = 0
        self._prev_dist: Optional[float] = None

    def reset(self) -> None:
        self.steps = 0
        self._lock_count = 0
        self._prev_dist = None

    def step(self, obs: TargetObs, action: str) -> TargetStep:
        """Nimmt die bereits berechnete Beobachtung entgegen und bewertet sie."""
        self.steps += 1

        # Distanz-Delta (positiv = besser)
        dist_now = float(obs.dist)
        if self._prev_dist is None:
            d_improve = 0.0
        else:
            d_improve = float(self._prev_dist) - dist_now
        self._prev_dist = dist_now

        # Reward: Distanzverbesserung * strength
        reward = float(d_improve) * float(max(0.0, min(1.0, obs.strength)))

        # Bewegungs-Kosten
        if str(action) != "hold":
            reward -= float(self.move_cost)

        # Lock-on: Ziel nahe Mitte und ausreichend stark
        if (obs.strength >= self.lock_min_strength) and (dist_now <= self.lock_dist):
            self._lock_count += 1
        else:
            self._lock_count = 0

        done = False
        reason = ""
        if self._lock_count >= self.lock_hold_steps:
            done = True
            reason = "lock"
        elif self.steps >= self.max_steps:
            done = True
            reason = "max_steps"

        info: Dict[str, Any] = {
            "steps": int(self.steps),
            "lock_count": int(self._lock_count),
            "reason": str(reason),
        }
        return TargetStep(obs=obs, reward=float(reward), done=bool(done), info=info)

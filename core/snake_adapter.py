#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_adapter.py
# Projekt: ORÓMA
# Modul:   SnakeAdapter (Policy-Bridge für PolicyEngine)
# Version: v3.7.4
# Stand:   2025-11-07
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
# Adapter-Schicht zwischen Snake-Spielzustand und PolicyEngine – analog zu
# TTTAdapter (TicTacToe). Der Adapter erzeugt:
#   • kompakte Feature-Vektoren (floats)
#   • einen stabile(n) Schlüssel/Key für diskrete Policies
#   • Legal-Action-Liste (0..3) und Mapping ↔ Richtungsvektor
#
# Design
# ──────
#  - Namespace: "game:snake"
#  - Action IDs: 0=Right, 1=Left, 2=Down, 3=Up
#  - Features (Standard):
#       [len_self, len_op, head_xN, head_yN, food_xN, food_yN,
#        rel_dxN, rel_dyN, danger_R, danger_L, danger_D, danger_U]
#    … wobei *_xN/_yN in [0..1], danger_* in {0.0,1.0}.
#  - Key (diskret): "S{ls}-O{lo}-RD{rd}{ru}-DN{ld}{lu}-REL{sx}{sy}"
#    ls/lo: Längen-Bucket (min(len, 10)), rd/ru/ld/lu: Danger-Bits,
#    sx/sy: Vorzeichen von rel_dx/rel_dy in {-1,0,1}.
#
# Nutzung
# ───────
#   from core.snake_adapter import SnakeAdapter
#   ad = SnakeAdapter()
#   obs = ad.make_obs(w=h=..., head=(hx,hy), body=..., op_body=..., food=(fx,fy))
#   legal = ad.legal_actions(obs)
#   action_idx = ad.pick_with_policy(engine, obs)  # robust, None bei Fehler
#   dvec = ad.action_to_dir(action_idx)            # (dx,dy)
#
# Hinweise
# ────────
#  - pick_with_policy() versucht mehrere generische Engine-Methoden:
#      choose_action_from_features / choose_action_from_key /
#      choose_action / choose_action_from_state
#    … und fällt bei Fehlern auf None zurück.
#  - Damit bleibt Snake lauffähig, selbst wenn PolicyEngine keine Snake-API hat.
# =============================================================================

from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional

import logging
from core import log_guard
logger = logging.getLogger(__name__)
class SnakeAdapter:
    namespace = "game:snake"
    # 0=Right, 1=Left, 2=Down, 3=Up
    ACTION_TO_DIR = {0: (1, 0), 1: (-1, 0), 2: (0, 1), 3: (0, -1)}
    DIR_TO_ACTION = {(1, 0): 0, (-1, 0): 1, (0, 1): 2, (0, -1): 3}

    def action_to_dir(self, a: int) -> Tuple[int, int]:
        return self.ACTION_TO_DIR.get(int(a), (0, 0))

    def dir_to_action(self, dxy: Tuple[int, int]) -> int:
        return self.DIR_TO_ACTION.get(tuple(dxy), 0)

    # ---------- Observations ----------
    def _wrap_rel(self, a: int, b: int, size: int) -> int:
        """Kürzeste Differenz auf toroidal (wrap-around)."""
        d = (b - a) % size
        if d > size // 2:
            d -= size
        return d

    def make_obs(
        self,
        *,
        w: int,
        h: int,
        head: Tuple[int, int],
        body: List[Tuple[int, int]],
        op_body: List[Tuple[int, int]],
        food: Tuple[int, int]
    ) -> Dict[str, Any]:
        hx, hy = head
        fx, fy = food
        # normierte Koordinaten
        head_xN, head_yN = hx / float(w), hy / float(h)
        food_xN, food_yN = fx / float(w), fy / float(h)
        # wrap-relative Richtung zum Futter
        rel_dx = self._wrap_rel(hx, fx, w)
        rel_dy = self._wrap_rel(hy, fy, h)
        rel_dxN = rel_dx / float(max(1, w // 2))
        rel_dyN = rel_dy / float(max(1, h // 2))

        # Danger-Bits pro Action (wenn nächster Kopf in Körper landet)
        occ = set(body[1:] + op_body)  # eigener Körper ohne Kopf + Gegner
        nxR, nyR = (hx + 1) % w, hy
        nxL, nyL = (hx - 1) % w, hy
        nxD, nyD = hx, (hy + 1) % h
        nxU, nyU = hx, (hy - 1) % h
        danger_R = 1.0 if (nxR, nyR) in occ else 0.0
        danger_L = 1.0 if (nxL, nyL) in occ else 0.0
        danger_D = 1.0 if (nxD, nyD) in occ else 0.0
        danger_U = 1.0 if (nxU, nyU) in occ else 0.0

        feat = [
            float(len(body)),
            float(len(op_body)),
            head_xN, head_yN,
            food_xN, food_yN,
            rel_dxN, rel_dyN,
            danger_R, danger_L, danger_D, danger_U,
        ]
        key = self._make_key(len(body), len(op_body), rel_dx, rel_dy, danger_R, danger_L, danger_D, danger_U)
        return {
            "features": feat,
            "key": key,
            "w": w, "h": h,
            "head": (hx, hy),
            "body": list(body),
            "op_body": list(op_body),
            "food": (fx, fy),
        }

    def _make_key(
        self, len_self: int, len_op: int, rel_dx: int, rel_dy: int,
        dR: float, dL: float, dD: float, dU: float
    ) -> str:
        ls = min(int(len_self), 10)
        lo = min(int(len_op), 10)
        sx = 1 if rel_dx > 0 else -1 if rel_dx < 0 else 0
        sy = 1 if rel_dy > 0 else -1 if rel_dy < 0 else 0
        rd = int(1 if dR else 0); rl = int(1 if dL else 0)
        dd = int(1 if dD else 0); du = int(1 if dU else 0)
        # Key stabil & kurz
        return f"S{ls}-O{lo}-RD{rd}{rl}-DN{dd}{du}-REL{sx}{sy}"

    def legal_actions(self, obs: Dict[str, Any]) -> List[int]:
        f = obs.get("features", [])
        # danger bits liegen an den letzten 4 Positionen
        if len(f) >= 12:
            dR, dL, dD, dU = (f[8], f[9], f[10], f[11])
            acts = []
            if not dR: acts.append(0)
            if not dL: acts.append(1)
            if not dD: acts.append(2)
            if not dU: acts.append(3)
            return acts or [0, 1, 2, 3]
        return [0, 1, 2, 3]

    # ---------- Policy-Bridge ----------
    def pick_with_policy(self, engine: Any, obs: Dict[str, Any]) -> Optional[int]:
        """
        Versucht mehrere mögliche PolicyEngine-APIs; bei Fehler → None.
        """
        if engine is None:
            return None
        feats = obs.get("features")
        key = obs.get("key")
        legal = self.legal_actions(obs)
        # Reihenfolge: Features → Key → Generic
        try:
            if hasattr(engine, "choose_action_from_features"):
                a = engine.choose_action_from_features(self.namespace, feats, legal)
                return int(a) if a in legal else None
        except Exception as e:
            log_guard.log_suppressed(logger, key="snake_adapter.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        try:
            if hasattr(engine, "choose_action_from_key"):
                a = engine.choose_action_from_key(self.namespace, key, legal)
                return int(a) if a in legal else None
        except Exception as e:
            log_guard.log_suppressed(logger, key="snake_adapter.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        try:
            if hasattr(engine, "choose_action"):
                a = engine.choose_action(self.namespace, {"key": key, "features": feats}, legal)
                return int(a) if a in legal else None
        except Exception as e:
            log_guard.log_suppressed(logger, key="snake_adapter.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        try:
            if hasattr(engine, "choose_action_from_state"):
                a = engine.choose_action_from_state(self.namespace, obs, legal)
                return int(a) if a in legal else None
        except Exception as e:
            log_guard.log_suppressed(logger, key="snake_adapter.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        return None
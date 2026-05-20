#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/universal_policy/adapter_universal.py
# Projekt: ORÓMA
# Modul:   UniversalAdapter – domänenagnostischer Policy-Adapter (2D/3D, Zeit)
# Version: v3.9-rc2
# Stand:   2025-11-11
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#  Universeller Adapter für die PolicyEngine (State→Action). Funktioniert für
#  einfache Raster-/Vektor-Spiele (Snake, Pong, Flappy, CTF, Hide&Seek, Memory,
#  TicTacToe, ConnectFour) sowie 3D-/Ego-Bewegungen – sofern der Chain-JSON
#  grundlegende Felder liefert.
#
#  NEU in v3.9-rc2 (für ORÓMA 3.8.9 Snake-Export):
#   • Unterstützt patterns[*].patterns → [[...], ...] (vorher: snaps/events.features)
#   • Auto-Inferenz von spec/indices, falls nicht vorhanden:
#       – Snake-Heuristik: head=[2,3], food=[4,5], action.kind="dir2"
#       – Generischer Fallback: wähle 2 Dimensionen mit größter Range als "head"
#
# PolicyEngine erwartet:
#    • namespace: str
#    • extract_vectors(chain: dict) -> List[List[float]]
#    • final_outcome(obj) -> int (+1 / 0 / -1)
#    • action_from_delta(prev: List[float], nxt: List[float], action_kind="index") -> Optional[str]
#    • canonicalize(vec: List[float], spec: dict | None = None) -> (state_hash, perm, inv_perm)
#    • map_action_through_perm(action: str, perm_or_invperm) -> str
#    • fallback_action(state_vec: List[float], kind: str = "index") -> Optional[str]
#
# RAM/SD-Schonung
# ───────────────
#  • Reiner Adapter – keine I/O. PolicyEngine erledigt RAM-first & DB.
#
# =============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

import math
import os
import logging
from core.log_guard import log_suppressed

Number = float
Vec = List[Number]


class UniversalAdapter:
    """
    Domänenagnostischer Adapter. Nutzt, wenn vorhanden, Chain['spec'] zur
    genaueren Interpretation (Koordinaten/Relativrichtung/Aktionsraum).
    """

    namespace: str = "game:any"

    def __init__(self) -> None:
        self._last_spec: Dict[str, Any] = {}
        # Default-Hash-Parameter (können via spec.hash_spec überschrieben werden)
        self._default_bins = int(os.environ.get("OROMA_UNI_BINS", "16"))
        self._default_dims = int(os.environ.get("OROMA_UNI_DIMS", "12"))
        self._default_tick = int(os.environ.get("OROMA_UNI_TICK_STRIDE", "10"))

    # ------------------------------------------------------------------ #
    # Core Hooks
    # ------------------------------------------------------------------ #

    def extract_vectors(self, chain: Dict[str, Any]) -> List[Vec]:
        """
        Vektor-Sequenz extrahieren (neue Reihenfolge: schnellste Pfade zuerst):
          1) chain["vectors"]           → [[...], ...]
          2) chain["steps"][i]["f"]     → [[...], ...]
          3) chain["patterns"][*].patterns           → [[...], ...]   (ORÓMA 3.8.9)
          4) chain["patterns"][*].snaps/events[*].features
        """
        self._last_spec = (chain.get("spec") or {}) if isinstance(chain, dict) else {}

        # 1) direkte Vektorliste
        vecs = chain.get("vectors")
        if isinstance(vecs, list) and vecs and all(isinstance(v, list) for v in vecs):
            out = [self._ensure_floats(v) for v in vecs]
            self._infer_spec_from_chain(chain, out)
            return out

        # 2) steps[].f
        steps = chain.get("steps")
        if isinstance(steps, list) and steps:
            out: List[Vec] = []
            for st in steps:
                if isinstance(st, dict) and isinstance(st.get("f"), list):
                    out.append(self._ensure_floats(st["f"]))
            if out:
                self._infer_spec_from_chain(chain, out)
                return out

        # 3) patterns[*].patterns   (ORÓMA 3.8.9 Snake-Export)
        pats = chain.get("patterns")
        if isinstance(pats, list) and pats:
            out: List[Vec] = []
            for p in pats:
                if not isinstance(p, dict):
                    continue
                # 3a) neue Form: direkt als Liste von Vektoren
                pv = p.get("patterns")
                if isinstance(pv, list):
                    for v in pv:
                        if isinstance(v, list) and v:
                            out.append(self._ensure_floats(v))
                # 3b) rückwärtskompatible Form: snaps/events[].features
                seq = p.get("snaps") or p.get("events")
                if isinstance(seq, list):
                    for s in seq:
                        feats = (s or {}).get("features")
                        if isinstance(feats, list) and feats:
                            out.append(self._ensure_floats(feats))
            if out:
                self._infer_spec_from_chain(chain, out)
                return out

        return []

    def final_outcome(self, obj: Any) -> int:
        """
        Ergebnis +1/0/-1:
          • Chain.result     (int/float) → sign
          • steps[-1].r      (Reward)    → sign
          • steps[-1].outcome           → {win/lose/draw} Mapping
          • Fallback: 0
        """
        try:
            if isinstance(obj, dict):
                if obj.get("result") is not None:
                    return self._sign(obj["result"])
                steps = obj.get("steps") or []
                if steps and isinstance(steps[-1], dict):
                    if "r" in steps[-1]:
                        return self._sign(steps[-1]["r"])
                    oc = steps[-1].get("outcome")
                    if isinstance(oc, str):
                        oc = oc.lower()
                        if oc in ("win", "won", "positive", "pos"): return +1
                        if oc in ("lose", "lost", "negative", "neg"): return -1
                        return 0
            # Vektor-Fallback: letzte Komponente plausibler Reward?
            if isinstance(obj, list) and obj and isinstance(obj[-1], (int, float)):
                r = float(obj[-1])
                if abs(r) > 1e-9:
                    return +1 if r > 0 else -1
        except Exception as e:
            log_suppressed('mini_programs/universal_policy/adapter_universal.py:151', exc=e, level=logging.WARNING)
            pass
        return 0

    def action_from_delta(self,
                          prev: Vec,
                          nxt: Vec,
                          action_kind: str = "index") -> Optional[str]:
        """
        Ableitung der Aktion zwischen prev → nxt.
        Unterstützte kinds:
          • "dir2"  : 0=Right,1=Left,2=Down,3=Up  (D4-kompatibel)
          • "dir3"  : 0=+X,1=-X,2=+Y,3=-Y,4=+Z,5=-Z
          • "index" : Index der stärksten Änderung (argmax |Δ|)
        Wenn spec.indices.* vorhanden sind, werden diese bevorzugt verwendet.
        """
        spec = self._last_spec or {}
        kind = str((spec.get("action") or {}).get("kind") or action_kind or "index").lower()

        # Spezifische Koordinaten nutzen, falls vorhanden
        idx = (spec.get("indices") or {})
        head = idx.get("head")
        rel  = idx.get("rel")

        if kind.startswith("dir2"):
            # Bevorzugt: relative Richtung
            if isinstance(rel, list) and len(rel) >= 2:
                dx = float(self._safe_get(nxt, rel[0]) - self._safe_get(prev, rel[0]))
                dy = float(self._safe_get(nxt, rel[1]) - self._safe_get(prev, rel[1]))
                return self._dir2_from_dxdy(dx, dy)
            # Üblich: Head-Verschiebung
            if isinstance(head, list) and len(head) >= 2:
                hx, hy = head[0], head[1]
                dx = float(self._safe_get(nxt, hx) - self._safe_get(prev, hx))
                dy = float(self._safe_get(nxt, hy) - self._safe_get(prev, hy))
                return self._dir2_from_dxdy(dx, dy)
            # Generischer Fallback: nehme Dimensionen 2/3, wenn vorhanden (Snake-Heuristik)
            if len(prev) >= 4 and len(nxt) >= 4:
                dx = float(self._safe_get(nxt, 2) - self._safe_get(prev, 2))
                dy = float(self._safe_get(nxt, 3) - self._safe_get(prev, 3))
                return self._dir2_from_dxdy(dx, dy)
            # Minimal-Fallback: erste 2 Dimensionen
            dx = float(self._safe_get(nxt, 0) - self._safe_get(prev, 0))
            dy = float(self._safe_get(nxt, 1) - self._safe_get(prev, 1))
            return self._dir2_from_dxdy(dx, dy)

        if kind.startswith("dir3"):
            if isinstance(rel, list) and len(rel) >= 3:
                dx = float(self._safe_get(nxt, rel[0]) - self._safe_get(prev, rel[0]))
                dy = float(self._safe_get(nxt, rel[1]) - self._safe_get(prev, rel[1]))
                dz = float(self._safe_get(nxt, rel[2]) - self._safe_get(prev, rel[2]))
                return self._dir3_from_deltas(dx, dy, dz)
            if isinstance(head, list) and len(head) >= 3:
                hx, hy, hz = head[0], head[1], head[2]
                dx = float(self._safe_get(nxt, hx) - self._safe_get(prev, hx))
                dy = float(self._safe_get(nxt, hy) - self._safe_get(prev, hy))
                dz = float(self._safe_get(nxt, hz) - self._safe_get(prev, hz))
                return self._dir3_from_deltas(dx, dy, dz)
            # Fallback: 0/1/2
            dx = float(self._safe_get(nxt, 0) - self._safe_get(prev, 0))
            dy = float(self._safe_get(nxt, 1) - self._safe_get(prev, 1))
            dz = float(self._safe_get(nxt, 2) - self._safe_get(prev, 2))
            return self._dir3_from_deltas(dx, dy, dz)

        # index: stärkste Änderung
        try:
            if len(prev) == len(nxt) and len(prev) > 0:
                deltas = [abs(float(nxt[i]) - float(prev[i])) for i in range(len(prev))]
                j = int(max(range(len(deltas)), key=lambda k: deltas[k]))
                return str(j)
        except Exception as e:
            log_suppressed('mini_programs/universal_policy/adapter_universal.py:222', exc=e, level=logging.WARNING)
            pass
        return "0"

    def canonicalize(self, vec: Vec, spec: Optional[Dict[str, Any]] = None) -> Tuple[str, Any, Any]:
        """
        Liefert (state_hash, perm, inv_perm).
        - Quantisiert die ersten D Dimensionen in B Bins (hash_spec).
        - Für world2d + square_D4 + bekannte „indices.head/food“:
          wählt die lexikographisch kleinste von 8 D4-Transformationen.
          perm beschreibt, wie Aktionen (dir2) transformiert werden müssten.
        """
        sp = spec or self._last_spec or {}
        hs  = (sp.get("hash_spec") or {})
        B   = int(hs.get("bins") or self._default_bins)
        D   = int(hs.get("dims") or min(len(vec), self._default_dims))
        B   = max(2, min(256, B))
        D   = max(1, min(len(vec), D))

        # Quantisierung 0..1 → 0..B-1
        q = [self._bin01(self._clamp01(float(vec[i])), B) for i in range(D)]
        base_hash = "uni|B%d|D%d|" % (B, D)

        # Prüfe D4-Kanonisierung (nur 2D)
        if str(sp.get("space") or "world2d").lower().startswith("world2d") \
           and str(sp.get("symmetry") or "").lower() in ("square_d4", "d4", "grid2d"):
            idx = (sp.get("indices") or {})
            head = idx.get("head")
            food = idx.get("food")
            if self._is_xy_pair(head) or self._is_xy_pair(food):
                # Erzeuge 8 Varianten aus (q) – mit Wirkung auf (head, food) Positionen.
                cands: List[Tuple[str, dict]] = []
                for op in ("I","R90","R180","R270","MX","MY","MD","MA"):
                    q_op = list(q)
                    perm = {"group":"D4","op":op}  # symbolisch; für map_action_through_perm
                    if self._is_xy_pair(head):
                        hx, hy = head[0], head[1]
                        q_op[hx], q_op[hy] = self._d4_apply(q[hx], q[hy], op, B)
                    if self._is_xy_pair(food):
                        fx, fy = food[0], food[1]
                        q_op[fx], q_op[fy] = self._d4_apply(q[fx], q[fy], op, B)
                    cands.append(("H" + "-".join(map(str, q_op)), perm))
                cands.sort(key=lambda t: t[0])  # lexikographisch kleinste
                best_hash, best_perm = cands[0]
                return (base_hash + best_hash, best_perm, self._d4_inv(best_perm))
        # Standard: keine Gruppenkanonisierung
        return (base_hash + "H" + "-".join(map(str, q)), None, None)

    def map_action_through_perm(self, action: str, perm_or_invperm: Any) -> str:
        """
        Transformiert eine Aktion durch eine D4-Operation (nur dir2 sinnvoll).
        action: "0..3" → 0=R,1=L,2=D,3=U
        """
        if not isinstance(perm_or_invperm, dict):
            return action
        if perm_or_invperm.get("group") != "D4":
            return action
        op = perm_or_invperm.get("op", "I")
        a = int(self._safe_int(action, 0)) % 4
        if op == "I":    return str(a)
        if op == "R90":  return str({0:2, 2:1, 1:3, 3:0}[a])
        if op == "R180": return str({0:1, 1:0, 2:3, 3:2}[a])
        if op == "R270": return str({0:3, 3:1, 1:2, 2:0}[a])
        if op == "MX":   return str({0:0, 1:1, 2:3, 3:2}[a])
        if op == "MY":   return str({0:1, 1:0, 2:2, 3:3}[a])
        if op == "MD":   return str({0:2, 2:0, 1:3, 3:1}[a])
        if op == "MA":   return str({0:3, 3:0, 1:2, 2:1}[a])
        return action

    def fallback_action(self, state_vec: Vec, kind: str = "index") -> Optional[str]:
        """
        Einfache Fallbacks ohne Policy:
          • dir2: bewege „in Richtung Ziel“, wenn rel oder head/food vorhanden.
          • dir3: dito mit Z-Komponente.
          • index: 0
        """
        spec = self._last_spec or {}
        k = str(kind or (spec.get("action") or {}).get("kind") or "index").lower()
        idx = (spec.get("indices") or {})

        if k.startswith("dir2"):
            rel = idx.get("rel")
            if self._is_xy_pair(rel):
                dx = float(self._safe_get(state_vec, rel[0]))
                dy = float(self._safe_get(state_vec, rel[1]))
                return self._dir2_from_dxdy(dx, dy)
            head = idx.get("head"); food = idx.get("food")
            if self._is_xy_pair(head) and self._is_xy_pair(food):
                hx, hy = self._safe_get(state_vec, head[0]), self._safe_get(state_vec, head[1])
                fx, fy = self._safe_get(state_vec, food[0]), self._safe_get(state_vec, food[1])
                return self._dir2_from_dxdy(fx - hx, fy - hy)
            # Snake-Heuristik: 2/3 als Kopf, 4/5 als Ziel – falls plausibel
            if len(state_vec) >= 6:
                hx, hy = self._safe_get(state_vec, 2), self._safe_get(state_vec, 3)
                fx, fy = self._safe_get(state_vec, 4), self._safe_get(state_vec, 5)
                return self._dir2_from_dxdy(fx - hx, fy - hy)
            return "0"

        if k.startswith("dir3"):
            rel = idx.get("rel")
            if isinstance(rel, list) and len(rel) >= 3:
                dx = float(self._safe_get(state_vec, rel[0]))
                dy = float(self._safe_get(state_vec, rel[1]))
                dz = float(self._safe_get(state_vec, rel[2]))
                return self._dir3_from_deltas(dx, dy, dz)
            head = idx.get("head"); food = idx.get("food")
            if isinstance(head, list) and isinstance(food, list) and len(head) >= 3 and len(food) >= 3:
                hx, hy, hz = [self._safe_get(state_vec, i) for i in (head[0], head[1], head[2])]
                fx, fy, fz = [self._safe_get(state_vec, i) for i in (food[0], food[1], food[2])]
                return self._dir3_from_deltas(fx - hx, fy - hy, fz - hz)
            return "0"

        return "0"

    # ------------------------------------------------------------------ #
    # Spec-Inferenz (neu)
    # ------------------------------------------------------------------ #

    def _infer_spec_from_chain(self, chain: Dict[str, Any], vecs: List[Vec]) -> None:
        """
        Ergänzt self._last_spec, falls keine spec/indices vorhanden sind.
        – Snake 3.8.9: head=[2,3], food=[4,5], dir2, D4
        – Generischer Fallback: 2 dims mit größter Range → head
        """
        if not vecs:
            return
        sp = self._last_spec or {}
        if (sp.get("indices") or {}) and (sp.get("action") or {}):
            return  # schon konfiguriert

        first = vecs[0]
        # Snake-Heuristik
        if isinstance(chain.get("schema_version"), str) \
           and chain["schema_version"].startswith("3.8") \
           and len(first) >= 6:
            self._last_spec = {
                "space": "world2d",
                "symmetry": "square_D4",
                "action": {"kind": "dir2"},
                "indices": {"head": [2, 3], "food": [4, 5]},
                "hash_spec": {"bins": self._default_bins, "dims": min(len(first), self._default_dims)}
            }
            return

        # Generischer Fallback (2D): nimm 2 Dimensionen mit größter Range
        dims = min(len(first), 12)
        ranges: List[Tuple[float, int]] = []
        for i in range(dims):
            mn = min(float(v[i]) for v in vecs)
            mx = max(float(v[i]) for v in vecs)
            ranges.append((mx - mn, i))
        ranges.sort(reverse=True, key=lambda t: t[0])
        if ranges and ranges[0][0] > 1e-3:
            head_xy = [ranges[0][1], ranges[1][1] if len(ranges) > 1 else (ranges[0][1] + 1) % dims]
            self._last_spec = {
                "space": "world2d",
                "symmetry": None,
                "action": {"kind": "dir2"},
                "indices": {"head": head_xy},
                "hash_spec": {"bins": self._default_bins, "dims": min(len(first), self._default_dims)}
            }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ensure_floats(v: List[Any]) -> Vec:
        out: Vec = []
        for x in v:
            try:
                out.append(float(x))
            except Exception:
                out.append(0.0)
        return out

    @staticmethod
    def _sign(x: Any) -> int:
        try:
            v = float(x)
            if v > 1e-9:
                return +1
            elif v < -1e-9:
                return -1
            return 0
        except Exception:
            return 0

    @staticmethod
    def _safe_get(v: List[Any], i: int, default: float = 0.0) -> float:
        try:
            return float(v[i])
        except Exception:
            return float(default)

    @staticmethod
    def _safe_int(x: Any, d: int = 0) -> int:
        try:
            return int(x)
        except Exception:
            return d

    @staticmethod
    def _clamp01(x: float) -> float:
        return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

    @staticmethod
    def _bin01(x01: float, bins: int) -> int:
        # map [0,1] → {0..bins-1}
        if x01 <= 0.0:
            return 0
        if x01 >= 1.0:
            return bins - 1
        return min(bins - 1, int(x01 * bins))

    @staticmethod
    def _is_xy_pair(lst: Any) -> bool:
        return isinstance(lst, list) and len(lst) >= 2 and all(isinstance(i, int) for i in lst[:2])

    def _dir2_from_dxdy(self, dx: float, dy: float) -> str:
        # 0=R,1=L,2=D,3=U
        if abs(dx) >= abs(dy):
            return "0" if dx >= 0 else "1"
        return "2" if dy >= 0 else "3"

    def _dir3_from_deltas(self, dx: float, dy: float, dz: float) -> str:
        # 0=+X,1=-X,2=+Y,3=-Y,4=+Z,5=-Z
        m = max((abs(dx), 0), (abs(dy), 2), (abs(dz), 4), key=lambda t: t[0])[1]
        if m == 0:
            return "0" if dx >= 0 else "1"
        if m == 2:
            return "2" if dy >= 0 else "3"
        return "4" if dz >= 0 else "5"

    def _d4_apply(self, x: int, y: int, op: str, bins: int) -> Tuple[int, int]:
        """Wendet eine D4-Operation auf diskrete Koordinaten an (x,y ∈ {0..bins-1})."""
        def to_unit(a: int) -> float:
            return (2.0 * (a + 0.5) / bins) - 1.0
        def to_bin(u: float) -> int:
            u = max(-1.0, min(1.0, u))
            a = (u + 1.0) * 0.5 * bins - 0.5
            ai = int(round(a))
            return max(0, min(bins - 1, ai))

        ux, uy = to_unit(x), to_unit(y)
        if   op == "I":    vx, vy = ux, uy
        elif op == "R90":  vx, vy =  uy, -ux
        elif op == "R180": vx, vy = -ux, -uy
        elif op == "R270": vx, vy = -uy,  ux
        elif op == "MX":   vx, vy =  ux, -uy
        elif op == "MY":   vx, vy = -ux,  uy
        elif op == "MD":   vx, vy =  uy,  ux
        elif op == "MA":   vx, vy = -uy, -ux
        else:              vx, vy = ux,  uy
        return (to_bin(vx), to_bin(vy))

    @staticmethod
    def _d4_inv(perm: dict) -> dict:
        """Inverse D4-Operation (für map_action_through_perm)."""
        if not isinstance(perm, dict) or perm.get("group") != "D4":
            return {}
        op = perm.get("op", "I")
        inv = {
            "I": "I", "R90": "R270", "R180": "R180", "R270": "R90",
            "MX": "MX", "MY": "MY", "MD": "MD", "MA": "MA"
        }.get(op, "I")
        return {"group": "D4", "op": inv}


# Optionaler Selftest
if __name__ == "__main__":
    ua = UniversalAdapter()
    # Mini-Chain im 2D-Raster mit Head/Rel, dir2
    chain = {
        "spec": {
            "space": "world2d",
            "symmetry": "square_D4",
            "action": {"kind": "dir2"},
            "indices": {"head": [2, 3], "rel": [6, 7], "food": [4, 5]},
            "hash_spec": {"bins": 12, "dims": 8}
        },
        "steps": [
            {"f": [0.2, 0.0, 0.10, 0.10, 0.90, 0.80, +0.80, +0.70]},
            {"f": [0.2, 0.0, 0.18, 0.10, 0.90, 0.80, +0.72, +0.70]},
            {"f": [0.2, 0.0, 0.26, 0.10, 0.90, 0.80, +0.64, +0.70]},
        ],
        "result": +1
    }
    vecs = ua.extract_vectors(chain)
    act1 = ua.action_from_delta(vecs[0], vecs[1], "dir2")
    h, p, ip = ua.canonicalize(vecs[0], chain["spec"])
    print("vectors=", len(vecs), "act1=", act1, "hash=", h, "perm=", p)
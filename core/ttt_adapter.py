#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/ttt_adapter.py
# Projekt: ORÓMA
# Modul:   TTTAdapter – Spezialisierter Adapter für TicTacToe (3x3)
# Version: v3.7.3-gapfix1
# Stand:   2025-12-29
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# ─────
#  Robuster, headless Adapter für TicTacToe, der von mehreren Kernkomponenten
#  genutzt werden kann (PolicyEngine/UniversalPolicy/DecisionEngine/Tools):
#
#   • extract_vectors(chain) aus steps[].board (['X','O','_']*9) oder steps[].f (9D)
#   • final_outcome(+1/-1/0) aus chain.result oder steps[-1].outcome
#   • action_from_delta(prev9,next9): Index der neu gesetzten Markierung
#   • canonicalize(vec9): D4-Kanonisierung inkl. Permutations-Mapping
#       - Format des state_hash ist **kompatibel zur UI/UniversalPolicy**:
#         "v1|<9 ints getrennt durch Komma>" (z.B. "v1|1,0,0,0,-1,0,...")
#   • map_action_through_perm(action, perm): Mapping für Feldindex 0..8
#   • legal_actions(vec9): legale Aktionen (leere Felder)
#   • state_tags(vec9) / state_dict(vec9): Feature-Extraktion für Prädikat-Regeln
#
# WICHTIG
# ───────
#  Dieser Adapter war früher ein sehr kleines Fallback. Für DecisionEngine/ttt_eval
#  fehlten aber legal_actions() und die Hash-Form war nicht kompatibel mit dem
#  "v1|..."-Format aus ui/tictactoe_ui.py.
#
#  Mit diesem Patch:
#   • läuft tools/ttt_eval.py wieder stabil,
#   • DecisionEngine kann exportierte Policy-Regeln (rules.type='policy') matchen,
#   • Prädikat-Regeln (rules.type='predicate') können über state_tags/state_dict
#     erstmals sinnvoll greifen (optional).
#
# Konvention
# ──────────
#  9D-Vektor: -1=Gegner, 0=leer, +1=Ich (aus Sicht der Engine).
#  In vielen Call-Sites wird Perspektive über Symbol-Swap erreicht (X↔O).
#
# =============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

Vec = List[float]


# D4-Transformationen auf 3x3 Indizes (wie ui/tictactoe_ui.py)
_D4: List[List[int]] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8],          # I
    [2, 5, 8, 1, 4, 7, 0, 3, 6],          # rot90
    [8, 7, 6, 5, 4, 3, 2, 1, 0],          # rot180
    [6, 3, 0, 7, 4, 1, 8, 5, 2],          # rot270
    [2, 1, 0, 5, 4, 3, 8, 7, 6],          # flipX
    [6, 7, 8, 3, 4, 5, 0, 1, 2],          # flipY
    [0, 3, 6, 1, 4, 7, 2, 5, 8],          # flipDiag
    [8, 5, 2, 7, 4, 1, 6, 3, 0],          # flipAntiDiag
]


def _inv_perm(M: List[int]) -> List[int]:
    inv = [0] * 9
    for i, j in enumerate(M):
        inv[j] = i
    return inv


class TTTAdapter:
    """Adapter für TicTacToe – kompatibel zu ORÓMA UI / UniversalPolicy."""

    namespace: str = "game:tictactoe"

    # ---------------------------------------------------------------------
    # SnapChain → Vektoren
    # ---------------------------------------------------------------------
    def extract_vectors(self, chain: Dict[str, Any]) -> List[Vec]:
        steps = chain.get("steps") or []
        out: List[Vec] = []
        for st in steps:
            if not isinstance(st, dict):
                continue
            if isinstance(st.get("f"), list) and len(st["f"]) >= 9:
                out.append([float(x) for x in st["f"][:9]])
            elif isinstance(st.get("board"), list) and len(st["board"]) == 9:
                out.append(self.vectorize_board(st["board"]))
        return out

    def final_outcome(self, obj: Any) -> int:
        if isinstance(obj, dict) and obj.get("result") is not None:
            r = float(obj["result"])
            return +1 if r > 0 else -1 if r < 0 else 0
        if isinstance(obj, dict):
            steps = obj.get("steps") or []
            if steps and isinstance(steps[-1], dict):
                oc = (steps[-1].get("outcome") or "").lower()
                if oc in ("x", "x_win", "win", "pos", "positive"):
                    return +1
                if oc in ("o", "o_win", "lose", "neg", "negative"):
                    return -1
        return 0

    def action_from_delta(self, prev9: Vec, next9: Vec, **_) -> Optional[str]:
        # Finde Feld, das sich von ~0 auf !=0 geändert hat
        p = self._asXO(prev9)
        n = self._asXO(next9)
        for i in range(9):
            if p[i] == 0 and n[i] != 0:
                return str(i)
        return None

    # ---------------------------------------------------------------------
    # Kanonisierung + Mapping
    # ---------------------------------------------------------------------
    def canonicalize(self, v9: Vec, _spec: Optional[Dict[str, Any]] = None) -> Tuple[str, Any, Any]:
        """
        D4-Kanonisierung.

        Rückgabe:
          (state_hash, M, M_inv)
        wobei:
          • state_hash = "v1|..." kompatibel zur UI/UniversalPolicy
          • M     = orig→canon (List[int] Länge 9)
          • M_inv = canon→orig (List[int] Länge 9)
        """
        base = self._asXO(v9)
        best_vec: Optional[List[int]] = None
        best_M: Optional[List[int]] = None

        for M in _D4:
            bt = [0] * 9
            for i in range(9):
                bt[M[i]] = base[i]
            if best_vec is None or tuple(bt) < tuple(best_vec):
                best_vec = bt
                best_M = M

        M = list(best_M or _D4[0])
        vec_can = list(best_vec or base)
        sh = "v1|" + ",".join(str(x) for x in vec_can)
        return (sh, M, _inv_perm(M))

    def map_action_through_perm(self, action: str, perm_or_invperm: Any) -> str:
        """Mappt eine Aktion über eine Permutation.

        Unterstützt:
          • List/Tuple (wie ui/tictactoe_ui.py): idx' = perm[idx]
          • legacy Dict-Format (ältere Varianten)
        """
        try:
            idx = int(str(action))
        except Exception:
            return str(action)

        # Neu: Listen/Arrays (empfohlen)
        if isinstance(perm_or_invperm, (list, tuple)) and len(perm_or_invperm) >= 9:
            try:
                return str(int(perm_or_invperm[idx]))
            except Exception:
                return str(action)

        # Alt: Dict-Format (best effort)
        if isinstance(perm_or_invperm, dict) and perm_or_invperm.get("group") == "D4":
            op = perm_or_invperm.get("op", "I")
            # Für alte Form ist ein Mapping nicht mehr garantiert; fallback identisch.
            _ = op
            return str(action)

        return str(action)

    # ---------------------------------------------------------------------
    # Legal / Fallback
    # ---------------------------------------------------------------------
    def legal_actions(self, state_vec: Vec) -> List[int]:
        b = self._asXO(state_vec)
        return [i for i in range(9) if b[i] == 0]

    def fallback_action(self, state_vec: Vec, *_args) -> Optional[str]:
        # Mitte, sonst Ecke, sonst Kante
        b = self._asXO(state_vec)
        order = [4, 0, 2, 6, 8, 1, 3, 5, 7]
        for i in order:
            if b[i] == 0:
                return str(i)
        return None

    # ---------------------------------------------------------------------
    # Prädikat-Regeln: Tags + State-Dict
    # ---------------------------------------------------------------------
    def state_dict(self, state_vec: Vec) -> Dict[str, Any]:
        """Kleine, stabile Summary für Prädikat-Regeln."""
        b = self._asXO(state_vec)
        moves = sum(1 for x in b if x != 0)
        empties = 9 - moves
        phase = "early" if moves <= 2 else "mid" if moves <= 6 else "late"
        return {
            "moves": int(moves),
            "empties": int(empties),
            "phase": phase,
            "center": int(b[4]),
        }

    def state_tags(self, state_vec: Vec) -> List[str]:
        """Erzeugt einfache, robuste Tags (ohne Heuristik-Explosion)."""
        b = self._asXO(state_vec)
        tags: List[str] = []

        if b[4] == 0:
            tags.append("center_free")
        else:
            tags.append("center_taken")

        corners = [0, 2, 6, 8]
        edges = [1, 3, 5, 7]
        if any(b[i] == 0 for i in corners):
            tags.append("corner_free")
        else:
            tags.append("no_corner_free")
        if any(b[i] == 0 for i in edges):
            tags.append("edge_free")
        else:
            tags.append("no_edge_free")

        # Gewinn-/Block-Indikator (nicht aktionsspezifisch, aber als "Signal" nutzbar)
        if self._has_immediate_win(b, me=1):
            tags.append("can_win_now")
        if self._has_immediate_win(b, me=-1):
            tags.append("must_block")

        return tags

    # ---------------------------------------------------------------------
    # Komfort
    # ---------------------------------------------------------------------
    def vectorize_board(self, board: List[str]) -> Vec:
        m = {"X": 1.0, "x": 1.0, "O": -1.0, "o": -1.0, "_": 0.0, " ": 0.0, "": 0.0, ".": 0.0}
        return [float(m.get(str(c), 0.0)) for c in (board or [])[:9]]

    # ---------------------------------------------------------------------
    # Intern
    # ---------------------------------------------------------------------
    @staticmethod
    def _asXO(v9: Vec) -> List[int]:
        out: List[int] = []
        for x in (v9 or [])[:9]:
            try:
                xf = float(x)
            except Exception:
                xf = 0.0
            out.append(1 if xf > 0.5 else -1 if xf < -0.5 else 0)
        # falls zu kurz
        while len(out) < 9:
            out.append(0)
        return out

    @staticmethod
    def _has_immediate_win(b: List[int], me: int) -> bool:
        wins = [
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        ]
        for a, c, d in wins:
            line = (b[a], b[c], b[d])
            if line.count(me) == 2 and line.count(0) == 1:
                return True
        return False


if __name__ == "__main__":
    t = TTTAdapter()
    v0 = t.vectorize_board(["", "", "", "", "", "", "", "", ""])
    v1 = t.vectorize_board(["X", "", "", "", "O", "", "", "", ""])
    sh, M, Minv = t.canonicalize(v1)
    print("hash=", sh)
    print("legal=", t.legal_actions(v1))
    print("tags=", t.state_tags(v1))
    print("dict=", t.state_dict(v1))
    print("map canon->orig (4):", t.map_action_through_perm("4", Minv))

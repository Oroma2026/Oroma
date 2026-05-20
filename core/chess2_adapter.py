#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess2_adapter.py
# Projekt: ORÓMA – Chess2 Adapter für PolicyEngine / Prehash-Ingestion
# Version: v3.8-r2
# Stand:   2026-03-12
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Adapter für das neue Mobility-native Chess2-Subsystem.
#
# Wichtige Designentscheidung:
#   Chess2 rekonstruiert Aktionen NICHT primär über Deltas des Feldvektors,
#   weil Mobility-Vektoren bei einem einzelnen Zug großflächige Änderungen
#   erzeugen. Stattdessen sollen Chains bevorzugt explizite Aktionen (`steps[*].a`)
#   mitführen. Die PolicyEngine unterstützt diesen Prehash-Pfad bereits.
#
# Der Adapter stellt daher vor allem bereit:
#   • namespace = game:chess2
#   • vector66_from_fen() / canonicalize() über den Chess2-Repräsentationskern
#   • extract_vectors() für Chains, die FEN oder Vektoren enthalten
#   • conservative fallback_action()
#
# `action_from_delta()` existiert nur als Notbremse und liefert bewusst `None`,
# damit kein falsches Pseudo-Wissen aus Mobility-Deltas abgeleitet wird.
# =============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import json

from core.chess2_repr import mobility_state_hash, vector66_from_fen


class Chess2Adapter:
    namespace: str = "game:chess2"

    def extract_vectors(self, chain_or_dict: Any) -> List[List[float]]:
        d: Dict[str, Any] = chain_or_dict if isinstance(chain_or_dict, dict) else {}
        out: List[List[float]] = []

        # 1) Vektorpfad aus patterns[].patterns[0] / centroid
        pats = d.get("patterns") or []
        for p in pats:
            vec = None
            try:
                arrs = p.get("patterns") or []
                if isinstance(arrs, list) and arrs and isinstance(arrs[0], list):
                    vec = [float(x) for x in arrs[0]]
                elif isinstance(p.get("centroid"), list):
                    vec = [float(x) for x in p.get("centroid")]
            except Exception:
                vec = None
            if vec is not None:
                if len(vec) < 66:
                    vec = (vec + [0.0] * (66 - len(vec)))[:66]
                else:
                    vec = vec[:66]
                out.append(vec)

        if out:
            return out

        # 2) Prehash/FEN-Schritte aus Chess2-Chains
        steps = d.get("steps") or []
        for st in steps:
            if not isinstance(st, dict):
                continue
            fen = (st.get("fen") or "").strip()
            if not fen:
                continue
            try:
                out.append(vector66_from_fen(fen, outcome=float(d.get("result") or 0.0 if st.get("terminal") else 0.0)))
            except Exception:
                continue
        return out

    def final_outcome(self, final_vec: List[float]) -> int:
        try:
            o = float(final_vec[65])
            return 1 if o > 0 else (-1 if o < 0 else 0)
        except Exception:
            return 0

    def action_from_delta(self, prev: List[float], nxt: List[float]) -> Optional[str]:
        # Mobility-Deltas sind bewusst NICHT als Aktionsquelle gedacht.
        return None

    def canonicalize(self, vec: List[float]) -> Tuple[str, List[int], List[int]]:
        if len(vec) < 65:
            vec = (list(vec) + [0.0] * (65 - len(vec)))[:65]
        turn = 'w' if float(vec[64]) >= 0.0 else 'b'
        # mobility_state_hash arbeitet fen-basiert; für reinen Vektorpfad nehmen wir
        # eine Vektor-Signatur im gleichen Namespace-Schema.
        body = ",".join(str(int(round(float(v) / 0.125))) for v in vec[:64])
        state_hash = f"chess2:m1:{turn}:vec:{abs(hash(body)) % (10**16):016d}"
        perm = list(range(64))
        return state_hash, perm, perm

    def map_action_through_perm(self, action: str, perm_or_invperm: List[int]) -> str:
        return action

    def fallback_action(self, state_vec: List[float]) -> Optional[str]:
        return "e2e4" if len(state_vec) >= 65 and float(state_vec[64]) >= 0.0 else "e7e5"

    def hash_fen(self, fen: str) -> str:
        return mobility_state_hash(fen)

    def vectorize_fen(self, fen: str) -> List[float]:
        return vector66_from_fen(fen)

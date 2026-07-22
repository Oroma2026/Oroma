#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/position_encoder.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro Position Encoder
# Version: v0.1.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Erzeugt aus einer Schachstellung eine stabile, ORÓMA-taugliche Repräsentation
#   für Policy, SnapChain, NMR und spätere Dream-Replays. Der Encoder ist bewusst
#   zustandsorientiert: Nicht der einzelne Zug ist der Kern, sondern die Stellung
#   mit Material, Mobilität, Struktur, Königssicherheit und professionellen
#   Regelhits.
#
# HASH-REGEL
# ──────────
#   `state_hash` basiert auf FEN ohne Halbzug-/Vollzugzähler plus Seitenrolle und
#   groben Feature-Buckets. Dadurch bleiben transpositionsnahe Stellungen stabil,
#   aber taktisch relevante Unterschiede wie Rochaderecht/En-passant erhalten.
# =============================================================================

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Tuple

from core.chess_pro.rules import WHITE, BLACK, ChessPosition

from .evaluator import ProfessionalEvaluator, EvaluationResult


@dataclass(frozen=True)
class PositionEncoding:
    fen: str
    stable_fen: str
    side_to_move: str
    focus_side: str
    state_hash: str
    vector: Tuple[float, ...]
    tokens: Tuple[str, ...]
    evaluation: EvaluationResult


class ChessProEncoder:
    """Positionsencoder für ChessPro."""

    def __init__(self, evaluator: ProfessionalEvaluator | None = None) -> None:
        self.evaluator = evaluator or ProfessionalEvaluator()

    @staticmethod
    def stable_fen(fen: str) -> str:
        parts = (fen or "").strip().split()
        if len(parts) >= 4:
            return " ".join(parts[:4])
        return (fen or "").strip()

    @staticmethod
    def _bucket(value: float, step: float, lo: int = -9, hi: int = 9) -> int:
        if step <= 0:
            return 0
        b = int(round(float(value) / float(step)))
        return max(int(lo), min(int(hi), b))

    def encode(self, pos: ChessPosition, focus_side: str | None = None) -> PositionEncoding:
        focus = focus_side if focus_side in {WHITE, BLACK} else pos.stm
        ev = self.evaluator.evaluate(pos)
        fen = pos.as_fen()
        stable = self.stable_fen(fen)
        stm = pos.stm
        sign = 1.0 if focus == WHITE else -1.0
        vector: Tuple[float, ...] = (
            sign * float(ev.material_cp) / 1000.0,
            sign * float(ev.mobility_cp) / 100.0,
            sign * float(ev.white_rule_cp - ev.black_rule_cp) / 300.0,
            float(ev.metrics.get("white_legal_moves", 0.0)) / 64.0,
            float(ev.metrics.get("black_legal_moves", 0.0)) / 64.0,
            float(ev.metrics.get("white_passed_pawns", 0.0) - ev.metrics.get("black_passed_pawns", 0.0)) / 8.0 * sign,
            float(ev.metrics.get("white_king_attackers", 0.0) - ev.metrics.get("black_king_attackers", 0.0)) / 8.0 * sign,
            float(ev.metrics.get("phase_opening", 0.0)),
            float(ev.metrics.get("phase_middlegame", 0.0)),
            float(ev.metrics.get("phase_endgame", 0.0)),
        )
        buckets: Dict[str, int | str] = {
            "fen": stable,
            "stm": "w" if stm == WHITE else "b",
            "focus": "w" if focus == WHITE else "b",
            "mat": self._bucket(sign * ev.material_cp, 200),
            "mob": self._bucket(sign * ev.mobility_cp, 30),
            "rules": self._bucket(sign * (ev.white_rule_cp - ev.black_rule_cp), 50),
            "phase": ev.phase,
            "wp": self._bucket(ev.metrics.get("white_passed_pawns", 0.0), 1, 0, 8),
            "bp": self._bucket(ev.metrics.get("black_passed_pawns", 0.0), 1, 0, 8),
        }
        raw = json.dumps(buckets, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        state_hash = "chess_pro:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
        tokens = self.tokens_from_eval(ev, focus, stm)
        return PositionEncoding(
            fen=fen,
            stable_fen=stable,
            side_to_move=stm,
            focus_side=focus,
            state_hash=state_hash,
            vector=vector,
            tokens=tokens,
            evaluation=ev,
        )

    def tokens_from_eval(self, ev: EvaluationResult, focus: str, stm: str) -> Tuple[str, ...]:
        sign = 1 if focus == WHITE else -1
        score_focus = sign * ev.score_cp
        tokens: List[str] = [f"phase:{ev.phase}", f"stm:{'white' if stm == WHITE else 'black'}", f"focus:{focus}"]
        if score_focus > 250:
            tokens.append("advantage:clear")
        elif score_focus > 80:
            tokens.append("advantage:slight")
        elif score_focus < -250:
            tokens.append("danger:clear")
        elif score_focus < -80:
            tokens.append("danger:slight")
        else:
            tokens.append("balance:near_equal")
        # Wichtigste Regelhits als symbolische SnapTokens.
        own_hits = ev.white_hits if focus == WHITE else ev.black_hits
        enemy_hits = ev.black_hits if focus == WHITE else ev.white_hits
        for hit in sorted(own_hits, key=lambda h: abs(h.score_cp), reverse=True)[:5]:
            polarity = "plus" if hit.score_cp >= 0 else "minus"
            tokens.append(f"rule:{polarity}:{hit.name}")
        for hit in sorted(enemy_hits, key=lambda h: abs(h.score_cp), reverse=True)[:3]:
            polarity = "enemy_plus" if hit.score_cp >= 0 else "enemy_minus"
            tokens.append(f"rule:{polarity}:{hit.name}")
        return tuple(tokens)

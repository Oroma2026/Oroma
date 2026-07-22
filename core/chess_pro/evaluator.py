#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/evaluator.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro ProfessionalEvaluator
# Version: v0.1.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Bewertet eine legale Schachstellung in Centipawns aus Weiß-Sicht. Die
#   Bewertung kombiniert klassische Engine-Bausteine mit professionellen
#   Schachprinzipien:
#
#     Material + Mobilität + Profi-Regeln + Terminalstatus
#
#   Diese Datei ist bewusst klein genug für Raspberry Pi, aber strukturiert
#   genug, damit spätere Dream-/NMR-/Policy-Pfade die Einzelbeiträge auswerten
#   und kalibrieren können.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from core.chess_pro.rules import WHITE, BLACK, PIECE_VALUES, ChessPosition

from .professional_rules import ProfessionalRuleBook, RuleHit, color_of


@dataclass(frozen=True)
class EvaluationResult:
    """Ergebnis einer Stellungsbewertung.

    `score_cp` ist Weiß-Sicht: positiv = Vorteil Weiß, negativ = Vorteil Schwarz.
    `metrics` enthält bewusst numerische Teilbeiträge für Episoden/Stats/NMR.
    """

    score_cp: int
    material_cp: int
    mobility_cp: int
    white_rule_cp: int
    black_rule_cp: int
    phase: str
    white_hits: Tuple[RuleHit, ...]
    black_hits: Tuple[RuleHit, ...]
    metrics: Dict[str, float]


class ProfessionalEvaluator:
    """Centipawn-Bewertung für ChessPro."""

    def __init__(self, rulebook: ProfessionalRuleBook | None = None) -> None:
        self.rulebook = rulebook or ProfessionalRuleBook()

    def material_score(self, pos: ChessPosition) -> int:
        total = 0
        for _rc, p in pos.board:
            if not p or p == ".":
                continue
            v = int(PIECE_VALUES.get(str(p), 0))
            total += v if color_of(str(p)) == WHITE else -v
        return int(total)

    def legal_count_for(self, pos: ChessPosition, color: str) -> int:
        old = pos.stm
        try:
            pos.stm = color
            return len(pos.generate_legal_moves())
        except Exception:
            return 0
        finally:
            try:
                pos.stm = old
            except Exception:
                pass

    def terminal_score(self, pos: ChessPosition) -> int | None:
        try:
            status = pos.status()
        except Exception:
            return None
        if status == "ongoing":
            return None
        if status == "checkmate_black":
            return 100_000
        if status == "checkmate_white":
            return -100_000
        if status in {"stalemate", "fifty_moves", "threefold"}:
            return 0
        return None

    def evaluate(self, pos: ChessPosition) -> EvaluationResult:
        terminal = self.terminal_score(pos)
        phase = self.rulebook.game_phase(pos)
        material = self.material_score(pos)
        white_rule, white_hits, white_metrics = self.rulebook.evaluate_side(pos, WHITE)
        black_rule, black_hits, black_metrics = self.rulebook.evaluate_side(pos, BLACK)
        white_mob = self.legal_count_for(pos, WHITE)
        black_mob = self.legal_count_for(pos, BLACK)
        mobility = 3 * (white_mob - black_mob)
        raw = int(material + mobility + white_rule - black_rule)
        score = int(terminal if terminal is not None else raw)
        metrics: Dict[str, float] = {
            "score_cp": float(score),
            "material_cp": float(material),
            "mobility_cp": float(mobility),
            "white_legal_moves": float(white_mob),
            "black_legal_moves": float(black_mob),
            "white_rule_cp": float(white_rule),
            "black_rule_cp": float(black_rule),
            "terminal": 1.0 if terminal is not None else 0.0,
            "phase_opening": 1.0 if phase == "opening" else 0.0,
            "phase_middlegame": 1.0 if phase == "middlegame" else 0.0,
            "phase_endgame": 1.0 if phase == "endgame" else 0.0,
        }
        for k, v in white_metrics.items():
            metrics[f"white_{k}"] = float(v)
        for k, v in black_metrics.items():
            metrics[f"black_{k}"] = float(v)
        return EvaluationResult(
            score_cp=score,
            material_cp=material,
            mobility_cp=mobility,
            white_rule_cp=white_rule,
            black_rule_cp=black_rule,
            phase=phase,
            white_hits=tuple(white_hits),
            black_hits=tuple(black_hits),
            metrics=metrics,
        )

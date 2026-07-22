#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/__init__.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro Paketinitialisierung
# Version: v0.2.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   `core.chess_pro` ist die neue professionelle Schachlinie für ORÓMA. Sie ist
#   bewusst als eigener Kern neben `game:chess` und `game:chess2` angelegt, damit
#   der bisherige Legacy-/Übergangsstand nicht weiter vermischt wird.
#
# DESIGN-GRENZEN
# ──────────────
#   • Headless-only: keine UI-, pygame-, OpenCV-, Qt-, Wayland- oder X11-Pfade.
#   • Keine Drittbibliothek als Runtime-Pflicht: die vorhandene ORÓMA-Regelengine
#     `mini_programs.chess.chess_rules` liefert legale Züge/FEN/Apply/Undo.
#   • Professionelle Schachregeln werden als gewichtete Merkmale genutzt, nicht
#     als harte Befehle. Die Suche entscheidet weiterhin stellungsbezogen.
#   • ORÓMA-Lernen erfolgt zustandsorientiert: Position → Features → Move →
#     Folgeposition → späteres Ergebnis. Nicht nur "letzter Zug → Reward".
#   • Ab v0.2.0: Long-Search-Profil mit kontrolliertem Lernloop aus terminalem
#     Ergebnis und stellungsbezogener Zugverbesserung.
#
# ÖFFENTLICHE API
# ───────────────
#   ProfessionalRuleBook   – heuristische Profi-Regeln und Regel-Telemetrie
#   ProfessionalEvaluator  – Material/Mobilität/Regeln/Taktik in Centipawns
#   ChessProSearch         – Alpha-Beta + Move Ordering + Quiescence-Light
#   ChessProEncoder        – stabile Snap-/Policy-/NMR-nahe Positionsfeatures
#   ChessProTrace          – Episoden-/SnapChain-Serialisierung
# =============================================================================

from __future__ import annotations

from .rules import WHITE, BLACK, Move, ChessPosition
from .professional_rules import ProfessionalRuleBook, RuleHit
from .evaluator import ProfessionalEvaluator, EvaluationResult
from .search import ChessProSearch, SearchResult
from .position_encoder import ChessProEncoder, PositionEncoding
from .episode_trace import ChessProTrace, ChessProDecision

__all__ = [
    "WHITE",
    "BLACK",
    "Move",
    "ChessPosition",
    "ProfessionalRuleBook",
    "RuleHit",
    "ProfessionalEvaluator",
    "EvaluationResult",
    "ChessProSearch",
    "SearchResult",
    "ChessProEncoder",
    "PositionEncoding",
    "ChessProTrace",
    "ChessProDecision",
]

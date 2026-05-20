#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/chess/__init__.py
# Projekt: ORÓMA
# Paket:   mini_programs.chess
# Version: v3.8-r3 (stabile API, UI-optional, Import-Reihenfolge gesichert)
# Stand:   2025-10-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
#  Bündelt die Schach-Module (Board, Regeln, Spiel, KI, UI) und stellt eine
#  stabile öffentliche API bereit. Verhindert Import-/Namenskonflikte durch
#  explizite Reihenfolge und Kompatibilitäts-Aliasse.
#
# ÖFFENTLICHE API
# ───────────────
#  from mini_programs.chess import (
#      Board, Coord,
#      Move, parse_uci, algebraic,
#      rc_to_sq, sq_to_rc,
#      ChessPosition,
#      find_best_move, ChessAI,
#      ChessGame,
#      chess_ui,  # optional, nur wenn Flask vorhanden
#  )
#
# LEGACY-ALIASSE
# ──────────────
#  ChessBoard  -> Board
#  square_name -> rc_to_sq
#  parse_square-> sq_to_rc
#
# HINWEIS
# ───────
#  `chess_ui` wird best-effort importiert. Fehlt Flask, bleibt chess_ui=None.
# =============================================================================

from __future__ import annotations

# 1) Board zuerst
from .board import Board, Coord

# 2) Regeln/Position
from .chess_rules import (
    Move, parse_uci, algebraic,
    rc_to_sq, sq_to_rc,
    ChessPosition,
)

# 3) KI (muss vor Game kommen, da Game find_best_move/ChessAI nutzt)
from .chess_ai import find_best_move, ChessAI

# 4) Game-Wrapper
from .chess_game import ChessGame

# 5) Optionales UI
try:
    from . import chess_ui  # type: ignore
except Exception:
    chess_ui = None  # Flask nicht vorhanden → UI bleibt None

# Legacy-Aliasse
ChessBoard = Board
square_name = rc_to_sq
parse_square = sq_to_rc

__all__ = [
    "Board", "Coord",
    "Move", "parse_uci", "algebraic", "rc_to_sq", "sq_to_rc",
    "ChessPosition",
    "find_best_move", "ChessAI",
    "ChessGame",
    "chess_ui",
    # Legacy
    "ChessBoard", "square_name", "parse_square",
]

__version__ = "v3.8-r3"


def _sanity():
    missing = [n for n in ("Board", "ChessPosition", "find_best_move", "ChessGame") if n not in globals()]
    if missing:
        raise ImportError(f"mini_programs.chess: fehlende Symbole: {missing}")
_sanity()
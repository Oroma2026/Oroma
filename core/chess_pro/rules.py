#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/rules.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro Rules Loader
# Version: v0.1.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Lädt die vorhandene stabile ORÓMA-Schachregelengine aus
#   `mini_programs/chess/chess_rules.py`, ohne das Paket `mini_programs` zu
#   importieren. Das ist wichtig, weil `mini_programs/__init__.py` beim Import
#   alle Spiele discovern kann und dadurch DB-/UI-/Policy-Seiteneffekte auslöst.
#
#   ChessPro braucht aber nur die reine legale Schachregelengine:
#
#     Board + ChessPosition + Move + FEN/UCI + Apply/Undo
#
# PRODUKTIONSINVARIANTEN
# ──────────────────────
#   • keine DB-Zugriffe
#   • keine Game-Discovery
#   • keine UI-Imports
#   • Headless und deterministic importierbar
# =============================================================================

from __future__ import annotations

import importlib.util
import os
import sys
import types
from types import ModuleType
from typing import Any

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CHESS_DIR = os.path.join(_BASE_DIR, "mini_programs", "chess")
_PKG_NAME = "_oroma_chess_rules_runtime"


def _load_module(name: str, path: str) -> ModuleType:
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ensure_runtime_package() -> ModuleType:
    pkg = sys.modules.get(_PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [_CHESS_DIR]  # type: ignore[attr-defined]
        sys.modules[_PKG_NAME] = pkg
    _load_module(f"{_PKG_NAME}.board", os.path.join(_CHESS_DIR, "board.py"))
    return _load_module(f"{_PKG_NAME}.chess_rules", os.path.join(_CHESS_DIR, "chess_rules.py"))


_rules = _ensure_runtime_package()

WHITE = getattr(_rules, "WHITE")
BLACK = getattr(_rules, "BLACK")
FILES = getattr(_rules, "FILES")
PIECE_VALUES = getattr(_rules, "PIECE_VALUES")
Castle = getattr(_rules, "Castle")
RepetitionTable = getattr(_rules, "RepetitionTable")
Move = getattr(_rules, "Move")
ChessPosition = getattr(_rules, "ChessPosition")
parse_uci = getattr(_rules, "parse_uci")
algebraic = getattr(_rules, "algebraic")
rc_to_sq = getattr(_rules, "rc_to_sq")
sq_to_rc = getattr(_rules, "sq_to_rc")

__all__ = [
    "WHITE", "BLACK", "FILES", "PIECE_VALUES",
    "Castle", "RepetitionTable", "Move", "ChessPosition",
    "parse_uci", "algebraic", "rc_to_sq", "sq_to_rc",
]

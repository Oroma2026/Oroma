#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess2_canon_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess2 Canon Daily Runner – kanonischer Side-Normalization-Pfad
# Version: v3.8-r2
# Stand:   2026-03-13
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================

from __future__ import annotations

import sys
import pathlib

if __package__ in {None, ""}:
    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT_STR)

from tools.chess2_daily_runner import main

if __name__ == '__main__':
    argv = [
        '--namespace', 'game:chess2_canon',
        '--canonical', '1',
        '--enable-flip-pass', '0',
        *sys.argv[1:],
    ]
    raise SystemExit(main(argv))

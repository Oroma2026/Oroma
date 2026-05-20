#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/curriculum_math.py
# Projekt: ORÓMA v3.7.x – Math Curriculum v2 (mehr Vielfalt, headless)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Strukturiertes Curriculum für Mathematik-/Logikaufgaben.
#
# Motivation (v2)
# ───────────────
#   Vorher war das Curriculum „monoton“, weil es sehr wenige feste Ausdrücke
#   gab, die sich ständig wiederholt haben.
#
#   v2 erweitert die Menge deutlich, OHNE DB-Schema-Bruch:
#     • Fill-in-the-Blank (3 + ? = 10)
#     • Zahlenfolgen (3, 6, 9, ?)
#     • Multi-Step ((3+4)*2=?)
#     • Vergleich (<,=,>) als -1/0/1 mit truth_json Meta
#
# Datenformat
# ───────────
#   Task: {"expr": str, "truth": Any, "truth_json": dict(optional)}
#
# =============================================================================

from __future__ import annotations

from typing import List, Dict, Any
import math
import random

from core.math_puzzles import generate_batch

_BASE: Dict[int, List[Dict[str, Any]]] = {
    1: [
        {"expr": "2+3", "truth": 5},
        {"expr": "7-4", "truth": 3},
        {"expr": "9+1", "truth": 10},
        {"expr": "8-5", "truth": 3},
    ],
    2: [
        {"expr": "2*3", "truth": 6},
        {"expr": "8/2", "truth": 4},
        {"expr": "5*5", "truth": 25},
        {"expr": "9/3", "truth": 3},
    ],
    3: [
        {"expr": "1/2", "truth": 0.5},
        {"expr": "2/4", "truth": 0.5},
        {"expr": "3/6", "truth": 0.5},
        {"expr": "4/8", "truth": 0.5},
    ],
    4: [
        {"expr": "pi", "truth": math.pi},
        {"expr": "phi", "truth": (1 + math.sqrt(5)) / 2},
        {"expr": "e", "truth": math.e},
    ],
    5: [
        {"expr": "2x+3=7", "truth": 2},
        {"expr": "3x-4=5", "truth": 3},
        {"expr": "x+7=10", "truth": 3},
    ],
    6: [
        {"expr": "x^2-4=0", "truth": [2, -2]},
        {"expr": "x^2+2x+1=0", "truth": [-1]},
        {"expr": "x^2-5x+6=0", "truth": [2, 3]},
    ],
}

def _finite_fraction_tasks() -> List[Dict[str, Any]]:
    """Brüche/Operationen mit endlichen Dezimaldarstellungen (robust für UI)."""
    out: List[Dict[str, Any]] = []
    denoms = [2, 4, 5, 8, 10, 20]
    nums = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    for d in denoms:
        for n in nums:
            if n < d:
                out.append({
                    "expr": f"{n}/{d}",
                    "truth": n / d,
                    "truth_json": {"type": "fraction", "skill": "fractions", "n": n, "d": d}
                })
    for d in [4, 5, 8, 10]:
        for a in [1, 2, 3]:
            for b in [1, 2, 3]:
                if a + b <= d:
                    out.append({
                        "expr": f"{a}/{d}+{b}/{d}",
                        "truth": (a + b) / d,
                        "truth_json": {"type": "fraction_add", "skill": "fractions", "d": d, "a": a, "b": b}
                    })
    return out

_CURRICULUM: Dict[int, List[Dict[str, Any]]] = {}

def _build_level(level: int) -> List[Dict[str, Any]]:
    if level in _CURRICULUM:
        return _CURRICULUM[level]

    base = list(_BASE.get(level, []))

    # deterministische Seeds pro Level → reproduzierbar, aber viel mehr Variety
    if level == 1:
        base += generate_batch(seed=11001, n=36, difficulty=2, kind="fill")
    elif level == 2:
        base += generate_batch(seed=22001, n=36, difficulty=4, kind="fill")
        base += generate_batch(seed=22099, n=12, difficulty=4, kind="multi")
        base += generate_batch(seed=22150, n=20, difficulty=4, kind="cmp")
    elif level == 3:
        base += _finite_fraction_tasks()
    elif level == 4:
        pass
    elif level == 5:
        base += generate_batch(seed=55001, n=24, difficulty=5, kind="fill")
    elif level == 6:
        pass
    elif level == 7:
        base += generate_batch(seed=77001, n=60, difficulty=4, kind="seq")
    else:
        base = []

    _CURRICULUM[level] = base
    return base

def levels() -> List[int]:
    return [1, 2, 3, 4, 5, 6, 7]

def get_all_tasks(level: int) -> List[Dict[str, Any]]:
    return _build_level(int(level))

def get_task(level: int, index: int) -> Dict[str, Any]:
    tasks = _build_level(int(level))
    if 0 <= index < len(tasks):
        return tasks[index]
    return {}

def get_random_task(level: int) -> Dict[str, Any]:
    tasks = _build_level(int(level))
    if not tasks:
        return {}
    return random.choice(tasks)
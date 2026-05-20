#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/math_puzzles.py
# Projekt: ORÓMA – Math/Puzzle Generator (headless, Curriculum v2)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Headless-freundliche Generatoren für zusätzliche Mathe-/Logik-Aufgaben,
#   um Langeweile/Monotonie im Calculator/Curriculum zu vermeiden – ohne
#   DB-Schema-Bruch.
#
#   Abgedeckte Aufgabentypen:
#     • Fill-in-the-Blank  ("fill: 3 + ? = 10")
#     • Zahlenfolgen       ("seq: 3, 6, 9, ?")
#     • Multi-Step         ("puzzle: (3 + 4) * 2 = ?")
#     • Vergleich          ("cmp: (3+4) ? (2*5)" → Antwort als <,=,> / -1,0,1)
#
# Datenformat (einheitlich)
# ─────────────────────────
#   Jeder Generator liefert ein Dict:
#     {
#       "expr": "fill: 3 + ? = 10",
#       "truth": 7,                         # DB-REAL-kompatibel oder Bewertung über JSON
#       "truth_json": { ... Meta ... }      # optional, wird in calculator_tasks.truth_json gespeichert
#     }
#
# Sicherheit/Performance
# ──────────────────────
#   • Kein eval() auf fremden Strings.
#   • Kein File-IO.
#   • Determinismus optional via seed.
#
# =============================================================================

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

Task = Dict[str, Any]


@dataclass(frozen=True)
class PuzzleConfig:
    """Parameter-Hülle für reproduzierbare Generatoren."""
    seed: int
    difficulty: int = 1  # 1..10 (grob)


def _rng(seed: Optional[int]) -> random.Random:
    return random.Random(int(seed) if seed is not None else random.randrange(1 << 30))


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


# -----------------------------------------------------------------------------
# Fill-in-the-Blank
# -----------------------------------------------------------------------------

def generate_fill_task(*,
                       seed: Optional[int] = None,
                       difficulty: int = 1,
                       ops: Optional[List[str]] = None) -> Task:
    """
    Erzeugt eine Lückenaufgabe wie:
      • fill: 3 + ? = 10
      • fill: ? - 4 = 9
      • fill: 2 * ? = 14

    Ziel: integer Lösung (handyfreundlich).
    """
    r = _rng(seed)
    diff = _clamp(difficulty, 1, 10)

    # diff 1 → 1..10, diff 10 → bis ~200
    max_n = 10 + (diff - 1) * 20
    min_n = 1

    if ops is None:
        ops = ["+", "-"] if diff <= 3 else ["+", "-", "*", "/"]

    op = r.choice(ops)

    if op == "+":
        a = r.randint(min_n, max_n)
        x = r.randint(min_n, max_n)
        c = a + x
        if r.random() < 0.5:
            expr = f"fill: {a} + ? = {c}"
        else:
            expr = f"fill: ? + {a} = {c}"
        sol = x

    elif op == "-":
        # Variante 1: a - ? = c
        if r.random() < 0.5:
            a = r.randint(min_n, max_n)
            x = r.randint(min_n, min(max_n, a))
            c = a - x
            expr = f"fill: {a} - ? = {c}"
            sol = x
        else:
            # Variante 2: ? - b = c → ? = b + c
            b = r.randint(min_n, max_n)
            c = r.randint(min_n, max_n)
            expr = f"fill: ? - {b} = {c}"
            sol = b + c

    elif op == "*":
        a = r.randint(min_n, max(2, max_n // 2))
        x = r.randint(min_n, max(2, max_n // 2))
        c = a * x
        if r.random() < 0.5:
            expr = f"fill: {a} * ? = {c}"
        else:
            expr = f"fill: ? * {a} = {c}"
        sol = x

    else:  # "/"
        # a / ? = c  → a = c*x
        x = r.randint(1, max(2, 3 + diff))
        c = r.randint(1, max(10, 10 + diff * 5))
        a = c * x
        if r.random() < 0.5:
            expr = f"fill: {a} / ? = {c}"
            sol = x
        else:
            # ? / b = c → ? = b*c
            b = r.randint(1, max(2, 3 + diff))
            c2 = r.randint(1, max(10, 10 + diff * 5))
            expr = f"fill: ? / {b} = {c2}"
            sol = b * c2

    return {
        "expr": expr,
        "truth": sol,
        "truth_json": {
            "type": "fill",
            "skill": "basic_arith",
            "op": op,
            "solution": sol,
            "difficulty": diff,
        },
    }


# -----------------------------------------------------------------------------
# Sequences
# -----------------------------------------------------------------------------

def _seq_arith(r: random.Random, diff: int) -> Tuple[List[int], int, str]:
    step = r.randint(1, 3 + diff)
    start = r.randint(0, 10 + diff * 3)
    seq = [start + i * step for i in range(4)]
    return seq[:3], seq[3], f"arith+{step}"


def _seq_geom(r: random.Random, diff: int) -> Tuple[List[int], int, str]:
    ratio = r.choice([2, 3]) if diff <= 6 else r.choice([2, 3, 4])
    start = r.randint(1, 5 + diff)
    seq = [start * (ratio ** i) for i in range(4)]
    return seq[:3], seq[3], f"geom*{ratio}"


def _seq_fibo(r: random.Random, diff: int) -> Tuple[List[int], int, str]:
    a = r.randint(0, 3 + diff // 3)
    b = r.randint(1, 4 + diff // 3)
    seq = [a, b]
    for _ in range(2):
        seq.append(seq[-1] + seq[-2])
    return seq[:3], seq[3], "fibo"


def _seq_squares(r: random.Random, diff: int) -> Tuple[List[int], int, str]:
    start = r.randint(1, 3 + diff // 2)
    ns = [start, start + 1, start + 2, start + 3]
    seq = [n * n for n in ns]
    return seq[:3], seq[3], "squares"


def generate_sequence_task(*,
                           seed: Optional[int] = None,
                           difficulty: int = 1,
                           kinds: Optional[List[str]] = None) -> Task:
    """
    Erzeugt eine Zahlenfolge-Aufgabe wie:
      • seq: 3, 6, 9, ?
      • seq: 2, 4, 8, ?
      • seq: 1, 1, 2, ?

    Lösung ist i.d.R. integer.
    """
    r = _rng(seed)
    diff = _clamp(difficulty, 1, 10)

    if kinds is None:
        kinds = ["arith", "geom", "fibo", "squares"]

    kind = r.choice(kinds)
    if kind == "geom":
        seq3, sol, rule = _seq_geom(r, diff)
    elif kind == "fibo":
        seq3, sol, rule = _seq_fibo(r, diff)
    elif kind == "squares":
        seq3, sol, rule = _seq_squares(r, diff)
    else:
        seq3, sol, rule = _seq_arith(r, diff)

    seq_txt = ", ".join(str(x) for x in seq3) + ", ?"
    expr = f"seq: {seq_txt}"
    return {
        "expr": expr,
        "truth": sol,
        "truth_json": {
            "type": "sequence",
            "skill": "sequences",
            "sequence": list(seq3),
            "rule": rule,
            "solution": sol,
            "difficulty": diff,
            "kind": kind,
        },
    }


# -----------------------------------------------------------------------------
# Multi-step puzzles (konservativ)
# -----------------------------------------------------------------------------

def generate_multi_step_task(*,
                             seed: Optional[int] = None,
                             difficulty: int = 1) -> Task:
    """
    Erzeugt ein kleines mehrstufiges Rechenrätsel, z.B.:
      • puzzle: (3 + 4) * 2 = ?

    Absichtlich konservativ: nur Klammern + (+,*, optional).
    """
    r = _rng(seed)
    diff = _clamp(difficulty, 1, 10)

    a = r.randint(1, 10 + diff)
    b = r.randint(1, 10 + diff)
    c = r.randint(1, 5 + diff // 2)

    if r.random() < 0.5:
        expr_math = f"({a} + {b}) * {c}"
        sol = (a + b) * c
        kind = "add_then_mul"
    else:
        expr_math = f"({a} * {b}) + {c}"
        sol = (a * b) + c
        kind = "mul_then_add"

    expr = f"puzzle: {expr_math} = ?"
    return {
        "expr": expr,
        "truth": sol,
        "truth_json": {
            "type": "multi_step",
            "skill": "puzzles",
            "kind": kind,
            "expr": expr_math,
            "solution": sol,
            "difficulty": diff,
        },
    }


# -----------------------------------------------------------------------------
# Compare ( <, =, > )  → truth: -1 / 0 / 1
# -----------------------------------------------------------------------------

def _safe_op(a: int, op: str, b: int) -> float:
    if op == "+":
        return float(a + b)
    if op == "-":
        return float(a - b)
    if op == "*":
        return float(a * b)
    # Division: b != 0
    return float(a) / float(b if b != 0 else 1)


def generate_compare_task(*,
                          seed: Optional[int] = None,
                          difficulty: int = 1) -> Task:
    """
    Erzeugt eine Vergleichsaufgabe:
      cmp: (3 + 4) ? (2 * 5)

    Erwartete Eingabe im UI: < oder = oder > (UI mappt auf -1/0/1).
    truth (DB): -1 (<), 0 (=), 1 (>)
    """
    r = _rng(seed)
    diff = _clamp(difficulty, 1, 10)

    ops = ["+", "-", "*"] if diff <= 3 else ["+", "-", "*", "/"]

    def mk_expr() -> Tuple[str, float]:
        a = r.randint(1, 5 + diff * 2)
        b = r.randint(1, 5 + diff * 2)
        op = r.choice(ops)
        if op == "/":
            # b nicht 0 und lieber „sauber“
            b = r.randint(1, 3 + diff)
        val = _safe_op(a, op, b)
        txt = f"{a}{op}{b}"
        return txt, val

    left_txt, left_val = mk_expr()
    right_txt, right_val = mk_expr()

    # Minimale Chance auf Gleichheit (sonst selten):
    if r.random() < 0.15:
        right_txt = left_txt
        right_val = left_val

    if abs(left_val - right_val) < 1e-9:
        rel_sym = "="
        truth = 0
    elif left_val < right_val:
        rel_sym = "<"
        truth = -1
    else:
        rel_sym = ">"
        truth = 1

    expr = f"cmp: ({left_txt}) ? ({right_txt})"
    return {
        "expr": expr,
        "truth": truth,
        "truth_json": {
            "type": "compare",
            "skill": "comparisons",
            "left": left_txt,
            "right": right_txt,
            "left_value": left_val,
            "right_value": right_val,
            "solution_symbol": rel_sym,
            "solution": truth,
            "mapping": {"<": -1, "=": 0, ">": 1},
            "difficulty": diff,
        },
    }


# -----------------------------------------------------------------------------
# Batch helpers for curriculum
# -----------------------------------------------------------------------------

def generate_batch(*,
                   seed: int,
                   n: int,
                   difficulty: int,
                   kind: str) -> List[Task]:
    """Generiert n Tasks einer Art deterministisch (seed + i)."""
    out: List[Task] = []
    for i in range(int(n)):
        s = int(seed) + i
        if kind == "fill":
            out.append(generate_fill_task(seed=s, difficulty=difficulty))
        elif kind == "seq":
            out.append(generate_sequence_task(seed=s, difficulty=difficulty))
        elif kind == "multi":
            out.append(generate_multi_step_task(seed=s, difficulty=difficulty))
        elif kind in ("cmp", "compare"):
            out.append(generate_compare_task(seed=s, difficulty=difficulty))
        else:
            out.append(generate_fill_task(seed=s, difficulty=difficulty))
    return out
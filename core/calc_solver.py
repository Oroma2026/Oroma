#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/calc_solver.py
# Projekt: ORÓMA – Mini-Solver für Math-Tasks (Curriculum / Calculator)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Ein bewusst kleiner, headless-freundlicher „Mini-Solver“, der ORÓMA beim
#   Curriculum NICHT mehr "teacher forced" (got=truth) laufen lässt, sondern
#   echte Versuche generiert.
#
#   Unterstützte Expr-Formate (aus core/math_puzzles.py & core/curriculum_math.py):
#     • Basic:      "2+3", "8/2", "1/4+2/4", "pi", "e", "phi"
#     • Fill:       "fill: 3 + ? = 10", "fill: ? - 4 = 17", "fill: 2 * ? = 14"
#     • Sequence:   "seq: 1, 4, 9, ?"
#     • Puzzle:     "puzzle: (3 + 4) * 2 = ?"
#     • Compare:    "cmp: (3 + 4) ? (2 * 5)"  → truth -1/0/1
#     • Quadratic:  "x^2-4=0", "x^2+2x+1=0", "x^2-5x+6=0" → roots als Liste
#
# DESIGN-PRINZIPIEN
# ─────────────────
#   • Kein eval() auf fremden Strings (AST-Safe-Eval).
#   • Kein IO, kein Netzwerk, keine DB – rein funktional.
#   • Robust: Fehler → None (Caller entscheidet Fallback).
#
# HINWEIS ZUR ROOT-REIHENFOLGE
# ────────────────────────────
#   Calculator.solve_task vergleicht Listen elementweise (L2). Für quadratische
#   Gleichungen ist die Reihenfolge mathematisch egal, in der DB aber relevant.
#   Diese Heuristik matcht das bestehende Curriculum:
#     - Wenn ein Root positiv und der andere negativ: positiver Root zuerst.
#     - Sonst: aufsteigend sortiert.
#
# =============================================================================

from __future__ import annotations

import ast
import math
import re
from typing import Any, Dict, List, Optional, Tuple



# ----------------------------- Normalization ---------------------------------
#
# Motivation:
#   In real-world UIs / mobile copy-paste can introduce Unicode operator variants
#   (e.g. '−' instead of '-', '×' instead of '*', '÷' instead of '/'),
#   as well as invisible whitespace (NBSP / zero-width spaces).
#   These characters break the regex parsers below and can silently force the
#   curriculum into fallback-random guesses (=> 0% accuracy on e.g. fill-tasks).
#
# Policy:
#   - Keep the solver strict and safe (no eval on unknown operators).
#   - Normalize a small, explicit set of Unicode operator glyphs to ASCII.
#   - Remove common invisible spacing characters.
#
_UNICODE_TRANSLATION = str.maketrans({
    "−": "-",  # U+2212 minus
    "–": "-",  # en-dash
    "—": "-",  # em-dash
    "＋": "+",  # fullwidth plus
    "×": "*",  # multiplication sign
    "∙": "*",  # bullet operator
    "·": "*",  # middle dot
    "÷": "/",  # division sign
    "／": "/",  # fullwidth slash
    "＝": "=",  # fullwidth equals
})


def _normalize_expr(s: str) -> str:
    """Normalize a math task expression to ASCII-friendly form."""
    if not s:
        return ""
    # NBSP + zero-width + BOM
    s = s.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    s = s.translate(_UNICODE_TRANSLATION)
    return s.strip()
# ----------------------------- Safe Eval -------------------------------------

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


def _safe_eval(expr: str) -> float:
    """
    Evaluieren eines reinen Rechen-Ausdrucks mit + - * / und Klammern.
    Unterstützt auch Dezimalzahlen.
    """
    expr = _normalize_expr(expr)

    # Konstanten (nur whole token)
    if expr == "pi":
        return math.pi
    if expr == "e":
        return math.e
    if expr == "phi":
        return (1.0 + math.sqrt(5.0)) / 2.0

    node = ast.parse(expr, mode="eval")

    def _eval(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _eval(n.body)

        if isinstance(n, ast.Constant):  # py3.8+
            if isinstance(n.value, (int, float)):
                return float(n.value)
            raise ValueError("bad-constant")

        if isinstance(n, ast.Num):  # pragma: no cover (older)
            return float(n.n)

        if isinstance(n, ast.UnaryOp) and isinstance(n.op, _ALLOWED_UNARY):
            v = _eval(n.operand)
            return +v if isinstance(n.op, ast.UAdd) else -v

        if isinstance(n, ast.BinOp) and isinstance(n.op, _ALLOWED_BINOPS):
            a = _eval(n.left)
            b = _eval(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            # Div
            if abs(b) < 1e-12:
                # defensiv: niemals crashen, aber klar falsch
                return float("inf")
            return a / b

        raise ValueError("unsupported-ast")

    return float(_eval(node))


# ----------------------------- Parsers ---------------------------------------

_RE_FILL = re.compile(
    r"^fill:\s*(?P<a>\?|[-]?\d+(?:\.\d+)?)\s*(?P<op>[+\-*/])\s*(?P<b>\?|[-]?\d+(?:\.\d+)?)\s*=\s*(?P<c>[-]?\d+(?:\.\d+)?)\s*$"
)

_RE_PUZZLE = re.compile(r"^puzzle:\s*(?P<body>.+?)=\s*\?\s*$")
_RE_CMP = re.compile(r"^cmp:\s*\((?P<l>.+?)\)\s*\?\s*\((?P<r>.+?)\)\s*$")

_RE_QUAD = re.compile(
    r"^x\^2(?:(?P<b>[+\-]\d+)x)?(?:(?P<c>[+\-]\d+))?=0$"  # bx optional, c optional
)


def _to_int_if_close(x: float, eps: float = 1e-9) -> Any:
    if not math.isfinite(x):
        return x
    rx = round(x)
    if abs(x - rx) <= eps:
        return int(rx)
    return float(x)


def _order_roots(a: Any, b: Any) -> List[Any]:
    af = float(a)
    bf = float(b)
    if (af >= 0 and bf < 0) or (bf >= 0 and af < 0):
        return [a, b] if af >= 0 else [b, a]
    return [a, b] if af <= bf else [b, a]


def solve(expr: str,
          *,
          truth: Any = None,
          truth_json: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Versucht die Aufgabe zu lösen.
    Rückgabe:
      (got, info)
        got  -> Lösung (Skalar oder Liste) oder None
        info -> Meta (solver, kind, confidence, error)
    """
    info: Dict[str, Any] = {"solver": "mini", "expr": expr, "kind": "unknown"}

    s = _normalize_expr(expr or "")
    if not s:
        info["error"] = "empty"
        return None, info

    # 1) Fill
    m = _RE_FILL.match(s)
    if m:
        info["kind"] = "fill"
        a = m.group("a")
        b = m.group("b")
        op = m.group("op")
        c = float(m.group("c"))

        def num(x: str) -> float:
            return float(x)

        try:
            if a == "?" and b != "?":
                bb = num(b)
                if op == "+":
                    x = c - bb
                elif op == "-":
                    x = c + bb
                elif op == "*":
                    x = c / bb if abs(bb) > 1e-12 else float("inf")
                else:  # /
                    x = c * bb
                return _to_int_if_close(float(x)), info

            if b == "?" and a != "?":
                aa = num(a)
                if op == "+":
                    x = c - aa
                elif op == "-":
                    x = aa - c
                elif op == "*":
                    x = c / aa if abs(aa) > 1e-12 else float("inf")
                else:  # /
                    x = aa / c if abs(c) > 1e-12 else float("inf")
                return _to_int_if_close(float(x)), info

        except Exception as e:
            info["error"] = f"fill:{type(e).__name__}"
            return None, info

        info["error"] = "fill:bad-shape"
        return None, info

    # 2) Sequence
    if s.startswith("seq:"):
        info["kind"] = "sequence"
        try:
            body = s[len("seq:"):].strip()
            parts = [p.strip() for p in body.split(",")]
            parts = [p for p in parts if p]
            if len(parts) < 4 or parts[-1] != "?":
                info["error"] = "seq:bad-format"
                return None, info

            seq3 = [float(parts[0]), float(parts[1]), float(parts[2])]
            a, b, c = seq3[0], seq3[1], seq3[2]

            # Quadratzahlen
            def _is_square(x: float) -> Optional[int]:
                if x < 0:
                    return None
                r = int(round(math.sqrt(x)))
                return r if abs(r * r - x) < 1e-9 else None

            sa, sb, sc = _is_square(a), _is_square(b), _is_square(c)
            if sa is not None and sb is not None and sc is not None:
                if (sb == sa + 1) and (sc == sb + 1):
                    nxt = (sc + 1) ** 2
                    return int(nxt), info

            # arithmetisch
            d1 = b - a
            d2 = c - b
            if abs(d1 - d2) < 1e-9:
                return _to_int_if_close(c + d2), info

            # geometrisch
            if abs(a) > 1e-12 and abs(b) > 1e-12:
                r1 = b / a
                r2 = c / b
                if abs(r1 - r2) < 1e-9:
                    return _to_int_if_close(c * r2), info

            # Fibo
            if abs((a + b) - c) < 1e-9:
                return _to_int_if_close(b + c), info

            # konstante 2. Differenz
            dd = d2 - d1
            nxt = c + (d2 + dd)
            return _to_int_if_close(nxt), info

        except Exception as e:
            info["error"] = f"seq:{type(e).__name__}"
            return None, info

    # 3) Puzzle
    m = _RE_PUZZLE.match(s)
    if m:
        info["kind"] = "puzzle"
        try:
            body = m.group("body").strip()
            return _to_int_if_close(_safe_eval(body)), info
        except Exception as e:
            info["error"] = f"puzzle:{type(e).__name__}"
            return None, info

    # 4) Compare
    m = _RE_CMP.match(s)
    if m:
        info["kind"] = "cmp"
        try:
            lv = _safe_eval(m.group("l").strip())
            rv = _safe_eval(m.group("r").strip())
            if abs(lv - rv) < 1e-9:
                return 0, info
            return (-1 if lv < rv else 1), info
        except Exception as e:
            info["error"] = f"cmp:{type(e).__name__}"
            return None, info

    # 5) Quadratic equation
    m = _RE_QUAD.match(s.replace(" ", ""))
    if m:
        info["kind"] = "quadratic"
        try:
            b_txt = m.group("b")
            c_txt = m.group("c")
            b = int(b_txt) if b_txt else 0
            c = int(c_txt) if c_txt else 0

            D = b * b - 4 * c
            if D < 0:
                return [], info

            sqrtD = int(round(math.sqrt(D)))
            if sqrtD * sqrtD != D:
                sqrtDf = math.sqrt(float(D))
                r1 = (-b + sqrtDf) / 2.0
                r2 = (-b - sqrtDf) / 2.0
            else:
                r1 = (-b + sqrtD) / 2.0
                r2 = (-b - sqrtD) / 2.0

            r1 = _to_int_if_close(float(r1))
            r2 = _to_int_if_close(float(r2))

            if r1 == r2:
                return [r1], info

            ordered = _order_roots(r1, r2)
            return ordered, info

        except Exception as e:
            info["error"] = f"quad:{type(e).__name__}"
            return None, info

    # 6) Plain arithmetic / fractions / constants
    info["kind"] = "arith"
    try:
        if re.match(r"^[0-9\s\+\-\*/\(\)\.]+$", s) or s in ("pi", "e", "phi"):
            return _to_int_if_close(_safe_eval(s)), info
    except Exception as e:
        info["error"] = f"arith:{type(e).__name__}"
        return None, info

    info["error"] = "unsupported"
    return None, info
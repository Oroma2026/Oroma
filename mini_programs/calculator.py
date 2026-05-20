#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/calculator.py
# Projekt: ORÓMA – Curriculum Taschenrechner / Math-Tasks
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Erzeugt und bewertet Calculator-Tasks (random oder curriculum) und speichert
#   Aufgaben + Ergebnisse in SQLite über core.sql_manager.
#
# Wichtige Erweiterungen (v3.7.3)
# ───────────────────────────────
#   1) truth_json / got_json akzeptieren jetzt auch dict (nicht nur list/tuple),
#      damit Curriculum v2 Meta-Daten (type/skill/solution/...) sauber speichern kann.
#   2) new_task_custom(...) für Repetition-Queue (expr/truth des Repeat-Items
#      wird als eigener Task in die DB geschrieben).
#   3) Optional mehr Variety für „Random“ durch Nutzung des erweiterten Curriculums:
#        ENV OROMA_CALC_RANDOM_VARIETY (Default: true)
#   4) NEU: Calculator → SnapChain Bridge (Transfer-Wissen):
#        Nach jedem Result wird eine SnapChain origin="calc/result" geschrieben,
#        damit das SnapSystem später crossmodal verknüpfen kann.
#
# ENV
# ───
#   OROMA_CALC_MODE               (random|curriculum)  Default: random
#   OROMA_CALC_RANDOM_VARIETY     (true|false)         Default: true
#
# Bridge-ENV (optional)
# ─────────────────────
#   OROMA_CALC_SNAPCHAINS         (true|false)         Default: true
#   OROMA_CALC_SNAP_EVERY         (int>=1)             Default: 1
#   OROMA_CALC_SNAP_VDIM          (int>=16)            Default: 84
#   OROMA_CALC_METASNAP_AGG       (true|false)         Default: true
#
# =============================================================================

from __future__ import annotations

import json
import os
import random
import time
from math import sqrt
from typing import Any, Optional

from core import curriculum_math
from core import sql_manager
from core import calc_to_snapchain
import logging
from core.log_guard import log_suppressed


_DEF_MODE = os.environ.get("OROMA_CALC_MODE", "random").strip().lower()
_RAND_VARIETY = os.environ.get("OROMA_CALC_RANDOM_VARIETY", "true").strip().lower() not in ("0", "false", "no", "off")


def _is_seq(x: Any) -> bool:
    return isinstance(x, (list, tuple))


def _to_float_safe(x: Any, default: float = 0.0) -> float:
    try:
        if isinstance(x, bool):
            return default
        return float(x)
    except Exception:
        return default


def _l2_distance(a: Any, b: Any) -> float:
    """
    Bewertungsdistanz:
      • Sequenz vs Sequenz → L2
      • Skalar vs Skalar   → abs
      • Fallback bei Nicht-Float (z.B. Strings) → exakte Gleichheit oder inf
    """
    try:
        if _is_seq(a) and _is_seq(b):
            if len(a) != len(b):
                return float("inf")
            acc = 0.0
            for x, y in zip(a, b):
                ax, by = _to_float_safe(x, float("inf")), _to_float_safe(y, float("inf"))
                if ax == float("inf") or by == float("inf"):
                    return float("inf")
                acc += (ax - by) ** 2
            return sqrt(acc)

        ax, by = _to_float_safe(a, float("inf")), _to_float_safe(b, float("inf"))
        if ax == float("inf") or by == float("inf"):
            return 0.0 if a == b else float("inf")
        return abs(ax - by)
    except Exception:
        return float("inf")


def _scalarize_for_db(x: Any) -> float:
    """
    DB-REAL:
      • Sequenz → erstes Element oder 0.0
      • Skalar  → float(x) oder 0.0
    """
    if _is_seq(x):
        return _to_float_safe(x[0] if x else 0.0, 0.0)
    return _to_float_safe(x, 0.0)


def _json_or_none(x: Any) -> Optional[str]:
    """
    JSON-String oder None.

    Historie:
      • Früher nur List/Tuple (Vektoren).
      • Curriculum v2 nutzt zusätzlich dict (Meta: type/skill/solution/...)
    """
    if x is None:
        return None
    try:
        if _is_seq(x):
            return json.dumps(list(x), ensure_ascii=False, separators=(",", ":"))
        if isinstance(x, dict):
            return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None
    return None


def _safe_int(x: Any, default: int = 0) -> int:
    """
    Robuste Int-Normalisierung für Curriculum-/Calculator-Pfade.

    Hintergrund:
      - curriculum_hook / curriculum können bei Altzuständen oder defekten
        Repeat-Queue-Einträgen None übergeben.
      - Direkte int(None)-Konvertierungen erzeugen dann Hook-Spam und brechen
        den Calculator-Pfad unnötig hart ab.

    Verhalten:
      - None / leere Strings / ungültige Werte -> default
      - bool wird nicht als 0/1 interpretiert, sondern ebenfalls default
    """
    try:
        if x is None:
            return int(default)
        if isinstance(x, bool):
            return int(default)
        if isinstance(x, str):
            s = x.strip()
            if not s:
                return int(default)
            return int(float(s))
        return int(x)
    except Exception:
        return int(default)


class Calculator:
    """
    Curriculum-Taschenrechner für ORÓMA.
    """

    LEVELS = {
        1: ["+", "-"],
        2: ["*", "/"],
        3: ["pi", "phi", "e"],
    }

    @staticmethod
    def new_task_random(level: int = 1) -> Optional[int]:
        """
        Erzeugt eine Random-Aufgabe.

        v3.7.3:
          Wenn OROMA_CALC_RANDOM_VARIETY=true → nutzt erweitertes Curriculum als
          „Random-Pool“, damit fill/seq/puzzle/cmp auch interaktiv auftauchen.
        """
        ts = int(time.time())
        lvl = _safe_int(level, 1)

        # 1) Variety: aus Curriculum ziehen (falls Level existiert)
        if _RAND_VARIETY and lvl in curriculum_math.levels():
            task = curriculum_math.get_random_task(lvl)
            if task:
                expr = str(task.get("expr", ""))
                truth = task.get("truth", 0.0)
                truth_db = _scalarize_for_db(truth)
                truth_json = _json_or_none(task.get("truth_json")) or _json_or_none(truth)
                try:
                    return sql_manager.insert_calculator_task(ts, lvl, expr, float(truth_db), truth_json=truth_json)
                except TypeError:
                    return sql_manager.insert_calculator_task(ts, lvl, expr, float(truth_db))

        # 2) Fallback: alte Random-Logik
        if lvl in (1, 2):
            a, b = random.randint(1, 10), random.randint(1, 10)
            op = random.choice(Calculator.LEVELS[lvl])
            if op == "/" and b == 0:
                b = 1
            expr = f"{a}{op}{b}"
            truth = (a / b) if op == "/" else eval(expr)  # kompatibel zur alten Logik
        elif lvl == 3:
            const = random.choice(Calculator.LEVELS[3])
            if const == "phi":
                truth = (1 + sqrt(5)) / 2
            elif const == "e":
                truth = 2.718281828459045
            else:
                truth = 3.141592653589793
            expr = const
        else:
            expr, truth = "pi", 3.141592653589793

        truth_db = _scalarize_for_db(truth)
        truth_json = _json_or_none(truth)

        try:
            return sql_manager.insert_calculator_task(ts, lvl, str(expr), float(truth_db), truth_json=truth_json)
        except TypeError:
            return sql_manager.insert_calculator_task(ts, lvl, str(expr), float(truth_db))

    @staticmethod
    def new_task_curriculum(level: int, index: int) -> Optional[int]:
        ts = int(time.time())
        lvl = _safe_int(level, 1)
        idx = _safe_int(index, 0)
        task = curriculum_math.get_task(lvl, idx)
        if not task:
            return -1

        expr = str(task.get("expr", ""))
        truth = task.get("truth")

        truth_db = _scalarize_for_db(truth)
        truth_json = _json_or_none(task.get("truth_json")) or _json_or_none(truth)

        try:
            return sql_manager.insert_calculator_task(ts, lvl, expr, float(truth_db), truth_json=truth_json)
        except TypeError:
            return sql_manager.insert_calculator_task(ts, lvl, expr, float(truth_db))

    @staticmethod
    def new_task_custom(*,
                        level: int,
                        expr: str,
                        truth: Any,
                        truth_json: Optional[Any] = None) -> Optional[int]:
        """
        Erzeugt einen beliebigen Calculator-Task.
        Wichtig für Repetition-Items: wir wollen genau diesen expr/truth loggen.
        """
        ts = int(time.time())
        lvl = _safe_int(level, 1)
        truth_db = _scalarize_for_db(truth)
        tj = _json_or_none(truth_json) or _json_or_none(truth)
        try:
            return sql_manager.insert_calculator_task(ts, lvl, str(expr), float(truth_db), truth_json=tj)
        except TypeError:
            return sql_manager.insert_calculator_task(ts, lvl, str(expr), float(truth_db))

    @staticmethod
    def new_task(level: int = 1, *, mode: Optional[str] = None, index: int = 0) -> Optional[int]:
        m = (mode or _DEF_MODE or "random").strip().lower()
        lvl = _safe_int(level, 1)
        idx = _safe_int(index, 0)
        if m == "curriculum":
            return Calculator.new_task_curriculum(lvl, idx)
        return Calculator.new_task_random(lvl)

    @staticmethod
    def solve_task(task_id: int, got: Any, truth: Any) -> Optional[int]:
        """
        Bewertet Lösung und schreibt calculator_results.
        correct: dist < eps
        reward: +0.1 (correct) / 0.0 (wrong)

        NEU v3.7.3:
          Schreibt danach eine SnapChain origin="calc/result" (Transfer-Bridge),
          damit das SnapSystem später crossmodal verknüpfen kann.
        """
        eps = 1e-6
        dist = _l2_distance(got, truth)
        correct = 1 if dist < eps else 0
        reward = 0.1 if correct else 0.0

        got_db = _scalarize_for_db(got)
        got_json = _json_or_none(got)

        ts = int(time.time())
        tid = _safe_int(task_id, 0)
        rid: Optional[int] = None

        # WICHTIG: Reihenfolge (task_id, ts, got, ...)
        try:
            rid = sql_manager.insert_calculator_result(
                tid,
                ts,
                float(got_db),
                correct=int(correct),
                reward=float(reward),
                got_json=got_json,
            )
        except TypeError:
            rid = sql_manager.insert_calculator_result(
                tid,
                ts,
                float(got_db),
                correct=int(correct),
                reward=float(reward),
            )

        # Fail-safe: SnapChain-Bridge darf niemals den Calculator blockieren
        try:
            if rid is not None:
                calc_to_snapchain.record_from_db(task_id=tid, result_id=_safe_int(rid, 0))
        except Exception as e:
            log_suppressed('mini_programs/calculator.py:283', exc=e, level=logging.WARNING)
            pass

        return rid
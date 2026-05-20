#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/curriculum_hook.py
# Projekt: ORÓMA v3.7 (Curriculum Hook – Adaptive + Repetition + Rewards)
# Version: v3.7.3 (Repetition-DB-Fix + Mini-Solver)
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   AgentLoop-Hook, der das Curriculum automatisiert ausführt:
#     • Alle 15 Ticks eine Aufgabe (Throttle)
#     • Vorrang für Spaced-Repetition-Queue (falls fällig)
#     • Reguläre Curriculum-Aufgaben danach
#     • Reward-Logging abhängig von correct (nicht blind +0.1)
#     • Empathie-gewichtete Wiederholung (sanft)
#
# Wichtiger Fix (v3.7.3)
# ──────────────────────
#   Früher wurde bei Repetition fälschlich ein *normaler* Curriculum-Task in die
#   DB geschrieben (new_task_curriculum(level,index)), statt das Repetition-Item
#   selbst.
#   Jetzt: new_task_custom(...) → expr/truth/truth_json des Repeat-Items landen
#   korrekt als eigener Task in calculator_tasks.
#
# Erweiterung (Mini-Solver)
# ─────────────────────────
#   ORÓMA soll nicht „betrügen“ (teacher forcing), sondern selbst versuchen:
#     • statt got=truth wird core.calc_solver.solve(expr, ...) genutzt
#     • epsilon-greedy Exploration möglich (OROMA_CURRICULUM_SOLVER_EPSILON)
#     • Fallback steuerbar (truth|random|skip)
#
# ENV
# ───
#   OROMA_CURRICULUM_THROTTLE_TICKS        Default: 15
#   OROMA_CURRICULUM_SOLVER_EPSILON        Default: 0.15   (Chance auf random guess)
#   OROMA_CURRICULUM_SOLVER_EPSILON_REPEAT Default: 0.05   (kleiner bei Repetition)
#   OROMA_CURRICULUM_SOLVER_FALLBACK       Default: random (truth|random|skip)
#
# =============================================================================

from __future__ import annotations

import os
import random
import time
from typing import Any, Optional, Tuple, Dict

from core import curriculum_math, curriculum, reward
from mini_programs.calculator import Calculator

import logging
from core import log_guard
logger = logging.getLogger(__name__)
# WICHTIG: core/__init__.py nutzt lazy loading; direkte Submodule-Imports sind robust:
import core.calc_solver as calc_solver

# Curiosity-Integration (v3.7.3+): nutzt Prediction-Error (got vs truth) als Motivationssignal
import core.curiosity as curiosity

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # graceful


_THROTTLE = int(os.getenv("OROMA_CURRICULUM_THROTTLE_TICKS", "15") or "15")
_EPS = float(os.getenv("OROMA_CURRICULUM_SOLVER_EPSILON", "0.15") or "0.15")
_EPS_REPEAT = float(os.getenv("OROMA_CURRICULUM_SOLVER_EPSILON_REPEAT", "0.05") or "0.05")
_FALLBACK = (os.getenv("OROMA_CURRICULUM_SOLVER_FALLBACK", "random") or "random").strip().lower()

# Curiosity → Exploration (light integration, minimal invasiv)
_CURIOSITY_ENABLE = int(os.getenv("OROMA_CURRICULUM_CURIOSITY", "1") or "1")
_CURIOSITY_EPS_SCALE = float(os.getenv("OROMA_CURRICULUM_CURIOSITY_EPS_SCALE", "0.25") or "0.25")
_CURIOSITY_EPS_MAX = float(os.getenv("OROMA_CURRICULUM_CURIOSITY_EPS_MAX", "0.60") or "0.60")
_CURIOSITY_TAG = (os.getenv("OROMA_CURRICULUM_CURIOSITY_TAG", "curriculum") or "curriculum").strip()

_CURIOSITY_LOGGER = None
_CURIOSITY_LAST = 0.0


def _safe_int(value: Any, default: int) -> int:
    """Robuster int-Coercer für Hook-Runtime-Daten.

    Verhindert, dass nullable/inkonsistente Payloads den gesamten Hook-Tick
    abbrechen. Stattdessen wird auf einen sichtbaren, kontrollierten Default
    zurückgefallen.
    """
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _task_level_default() -> int:
    return _safe_int(_CURRENT_LEVEL, 1)




def _safe_task_dict(task: Any) -> Dict[str, Any]:
    """Normalisiert Curriculum-/Repeat-Tasks defensiv auf ein Dict.

    In Live-Daten tauchen gelegentlich unvollständige oder nicht-diktartige
    Queue-Elemente auf. Der Hook darf daran nicht pro Tick hochgehen.
    """
    return task if isinstance(task, dict) else {}


def _safe_task_ident(task: Dict[str, Any], fallback_expr: str = "") -> str:
    try:
        expr = str(task.get("expr") or fallback_expr or "").strip()
        lvl = _safe_int(task.get("level"), _task_level_default())
        idx = _safe_int(task.get("index"), _CURRENT_INDEX)
        return f"expr={expr!r} level={lvl} index={idx}"
    except Exception:
        return f"expr={fallback_expr!r}"

def _normalize_task(expr: Any, truth: Any, truth_json: Optional[Dict[str, Any]]) -> Tuple[str, Any, Optional[Dict[str, Any]]]:
    """Normalisiert Task-Felder defensiv für Curriculum/Repeat-Pfade.

    Hintergrund: In der Runtime können einzelne Queue-/DB-Payloads unvollständig
    sein. Bisher führte bereits ein einzelnes None in Folgepfaden zu einem
    top-level Hook-Fehler und damit zu Log-Spam pro Tick.
    """
    expr_s = str(expr or "").strip()
    tj = truth_json if isinstance(truth_json, dict) else truth_json
    if truth is None and isinstance(tj, dict):
        for key in ("truth", "answer", "result", "value", "solution"):
            if tj.get(key) is not None:
                truth = tj.get(key)
                break
    return expr_s, truth, tj

def _as_vec(x: Any) -> Optional[list]:
    """Coerce scalar/list-like values to a float vector for curiosity_score()."""
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return [float(x)]
        if isinstance(x, str):
            s = x.strip().replace(",", ".")
            return [float(s)]
        if isinstance(x, (list, tuple)):
            vv = []
            for v in x:
                if v is None:
                    return None
                vv.append(float(v))
            return vv
    except Exception:
        return None
    return None

def _curiosity_epsilon(base_eps: float) -> float:
    """Map last curiosity into a safe epsilon range."""
    try:
        if not _CURIOSITY_ENABLE:
            return float(base_eps)
        eps = float(base_eps) + float(_CURIOSITY_LAST) * float(_CURIOSITY_EPS_SCALE)
        eps = max(0.0, min(float(_CURIOSITY_EPS_MAX), eps))
        return eps
    except Exception:
        return float(base_eps)

def _maybe_log_curiosity_from_task(*, expr: str, got: Any, truth: Any, correct: bool, repeat: bool, level: int) -> None:
    """Best-effort: log curiosity signal using got vs truth prediction error."""
    global _CURIOSITY_LOGGER, _CURIOSITY_LAST
    if not _CURIOSITY_ENABLE:
        return
    try:
        pv = _as_vec(got)
        ov = _as_vec(truth)
        if pv is None or ov is None:
            return
        sig = curiosity.curiosity_score(pred=pv, obs=ov, pe_range=(0.0, 5.0))
        if _CURIOSITY_LOGGER is None:
            _CURIOSITY_LOGGER = curiosity.CuriosityLogger()
        tag = f"{_CURIOSITY_TAG}:{'repeat' if repeat else 'task'}:{'ok' if correct else 'fail'}:L{int(level)}"
        _CURIOSITY_LOGGER.log("curriculum", sig, tag=tag)
        _CURIOSITY_LAST = float(sig.signal)
    except Exception as e:
        log_guard.log_suppressed(logger, key="curriculum_hook.curiosity.pass.1", msg="Suppressed exception (curiosity)", exc=e, level=logging.WARNING)



def _last_empathy_score(default: float = 0.5) -> float:
    if not sql_manager:
        return default
    try:
        rows = sql_manager.fetch_last_empathy(1)
        if rows:
            r = rows[0]
            if isinstance(r, dict) or hasattr(r, "keys"):
                return float(r.get("score", default))
            return float(r[2])
    except Exception as e:
        log_guard.log_suppressed(logger, key="curriculum_hook.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return default


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
    """Kopie der Bewertungslogik aus mini_programs/calculator.py (kompatibel)."""
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
            return (acc ** 0.5)

        ax, by = _to_float_safe(a, float("inf")), _to_float_safe(b, float("inf"))
        if ax == float("inf") or by == float("inf"):
            return 0.0 if a == b else float("inf")
        return abs(ax - by)
    except Exception:
        return float("inf")


def _random_guess_like(truth: Any) -> Any:
    """Erzeugt einen „plausiblen“ Zufalls-Guess mit ähnlicher Struktur wie truth."""
    if truth in (-1, 0, 1):
        return random.choice([-1, 0, 1])

    if _is_seq(truth):
        n = len(truth)
        out = []
        for _ in range(n):
            out.append(random.randint(-9, 9))
        return out

    if isinstance(truth, (int, float)):
        base = int(round(float(truth)))
        return base + random.choice([-3, -2, -1, 1, 2, 3])

    return random.randint(-9, 9)


def _attempt(expr: str, truth: Any, truth_json: Optional[Dict[str, Any]], *, epsilon: float) -> Tuple[Any, Dict[str, Any]]:
    """Epsilon-greedy: meistens solver, manchmal random guess."""
    if epsilon > 0.0 and random.random() < epsilon:
        return _random_guess_like(truth), {"solver": "epsilon_random", "kind": "guess", "expr": expr}

    got, info = calc_solver.solve(expr, truth=truth, truth_json=truth_json)
    if got is not None:
        return got, info

    if _FALLBACK == "skip":
        return None, {"solver": "fallback_skip", "kind": "skip", "expr": expr, "error": "no-solve"}
    if _FALLBACK == "truth":
        return truth, {"solver": "fallback_truth", "kind": "truth", "expr": expr, "error": "no-solve"}

    return _random_guess_like(truth), {"solver": "fallback_random", "kind": "guess", "expr": expr, "error": "no-solve"}


_CURRENT_LEVEL = 1
_CURRENT_INDEX = 0


def curriculum_hook(dt: float, tick: int) -> None:
    global _CURRENT_LEVEL, _CURRENT_INDEX

    try:
        if _THROTTLE > 0 and (tick % _THROTTLE) != 0:
            return

        # 1) Spaced-Repetition zuerst
        rep = curriculum.pop_repeat()
    except Exception as e:
        log_guard.log_suppressed(
            logger,
            key="curriculum_hook.prefetch",
            msg=f"Curriculum-Hook Präphase übersprungen: {e}",
            level=logging.WARNING,
            interval_s=600,
        )
        return
    if rep:
        rep_d = _safe_task_dict(rep)
        expr, truth, truth_json = _normalize_task(
            rep_d.get("expr", "repeat"),
            rep_d.get("truth"),
            rep_d.get("truth_json"),
        )
        if not expr:
            log_guard.log_suppressed(logger, key="curriculum_hook.repeat.invalid_expr", msg="Repeat-Task übersprungen: expr leer/ungültig", level=logging.WARNING)
            return
        if truth is None:
            log_guard.log_suppressed(
                logger,
                key="curriculum_hook.repeat.invalid_truth",
                msg=f"Repeat-Task übersprungen: truth fehlt (expr={expr!r})",
                level=logging.WARNING,
            )
            return

        try:
            tid = Calculator.new_task_custom(
                level=_task_level_default(),
                expr=expr,
                truth=truth,
                truth_json=truth_json,
            )
        except Exception as e:
            log_guard.log_suppressed(
                logger,
                key="curriculum_hook.repeat.new_task",
                msg=f"Repeat-Task konnte nicht angelegt werden ({_safe_task_ident(rep_d, expr)}): {e}",
                level=logging.WARNING,
                interval_s=600,
            )
            return
        if tid != -1:
            got, info = _attempt(expr, truth, truth_json, epsilon=_curiosity_epsilon(_EPS_REPEAT))
            if got is not None:
                Calculator.solve_task(tid, got, truth)
                dist = _l2_distance(got, truth)
                correct = dist < 1e-6
                try:
                    _maybe_log_curiosity_from_task(expr=expr, got=got, truth=truth, correct=bool(correct), repeat=True, level=_task_level_default())
                except Exception:
                    pass

                try:
                    reward.log(
                        "curriculum",
                        value=(+0.1 if correct else 0.0),
                        info={"repeat": True, "expr": expr, "correct": bool(correct), "solver": info},
                    )
                except Exception as e:
                    log_guard.log_suppressed(logger, key="curriculum_hook.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
                print(f"[Curriculum] Repeat: {'✅' if correct else '❌'} {expr} got={got}")
        return

    # 2) Normale Aufgabe aus aktuellem Level
    try:
        tasks = curriculum_math.get_all_tasks(_task_level_default())
    except Exception as e:
        log_guard.log_suppressed(
            logger,
            key="curriculum_hook.get_all_tasks",
            msg=f"Curriculum-Level konnte nicht geladen werden: {e}",
            level=logging.WARNING,
            interval_s=600,
        )
        return
    if not tasks:
        return

    safe_index = max(0, min(_safe_int(_CURRENT_INDEX, 0), len(tasks) - 1))
    task = _safe_task_dict(tasks[safe_index])
    expr, truth, truth_json = _normalize_task(
        task.get("expr", ""),
        task.get("truth"),
        task.get("truth_json"),
    )
    if not expr:
        log_guard.log_suppressed(logger, key="curriculum_hook.task.invalid_expr", msg="Curriculum-Task übersprungen: expr leer/ungültig", level=logging.WARNING)
        return
    if truth is None:
        log_guard.log_suppressed(
            logger,
            key="curriculum_hook.task.invalid_truth",
            msg=f"Curriculum-Task übersprungen: truth fehlt (level={_task_level_default()}, index={_CURRENT_INDEX})",
            level=logging.WARNING,
        )
        return

    try:
        tid = Calculator.new_task_curriculum(_task_level_default(), safe_index)
    except Exception as e:
        log_guard.log_suppressed(
            logger,
            key="curriculum_hook.task.new_task",
            msg=f"Curriculum-Task konnte nicht angelegt werden ({_safe_task_ident(task, expr)}): {e}",
            level=logging.WARNING,
            interval_s=600,
        )
        return
    if tid == -1:
        return

    got, info = _attempt(expr, truth, truth_json, epsilon=_curiosity_epsilon(_EPS))
    if got is None:
        try:
            reward.log("curriculum", value=0.0, info={"expr": expr, "level": _CURRENT_LEVEL, "skipped": True, "solver": info})
        except Exception as e:
            log_guard.log_suppressed(logger, key="curriculum_hook.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        print(f"[Curriculum] Skip: {expr}")
    else:
        Calculator.solve_task(tid, got, truth)
        dist = _l2_distance(got, truth)
        correct = dist < 1e-6

        try:
            _maybe_log_curiosity_from_task(expr=expr, got=got, truth=truth, correct=bool(correct), repeat=False, level=_task_level_default())
        except Exception:
            pass


        try:
            reward.log(
                "curriculum",
                value=(+0.1 if correct else 0.0),
                info={"expr": expr, "level": _CURRENT_LEVEL, "correct": bool(correct), "solver": info},
            )
        except Exception as e:
            log_guard.log_suppressed(logger, key="curriculum_hook.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        print(f"[Curriculum] {'✅' if correct else '❌'} L{_CURRENT_LEVEL} {safe_index+1}/{len(tasks)}: {expr} got={got}")

    # 3) Empathie-gewichtete Repetition
    try:
        emp = _last_empathy_score(0.5)
        if _CURRENT_LEVEL >= 3:
            base_delay = 90
            if emp < 0.4:
                delay = 60
                try:
                    curriculum.queue_repeat(task, delay=delay, weight=1.15)  # type: ignore
                except TypeError:
                    curriculum.queue_repeat(task, delay=delay)
            else:
                curriculum.queue_repeat(task, delay=base_delay)
    except Exception as e:
        log_guard.log_suppressed(logger, key="curriculum_hook.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    # 4) Index erhöhen / nächstes Level
    _CURRENT_INDEX += 1
    if _CURRENT_INDEX >= len(tasks):
        _CURRENT_LEVEL += 1
        _CURRENT_INDEX = 0
        if _CURRENT_LEVEL > max(curriculum_math.levels()):
            print("[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1")
            _CURRENT_LEVEL = 1
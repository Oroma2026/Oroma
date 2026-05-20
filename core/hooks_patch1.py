#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/hooks_patch1.py
# Projekt: ORÓMA
# Version: v3.5patch1-r2 (loggesteuert, headless-safe)
# Stand:   2025-10-21
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Enthält Hook-Funktionen für ORÓMA Patch Level 1:
#     • SelfAssessment – bewertet Lernqualität und speichert MetaSnaps
#     • TransferEngine – erkennt einfache Sequenzen und speichert TransferSnaps
#     • Calculator     – stellt Lernaufgaben und validiert Ergebnisse
#
# Erweiterung (r2)
# ────────────────
#   • Logging kann per ENV gesteuert werden:
#       OROMA_HOOKS_LOG=0      → keinerlei print()-Ausgaben
#       OROMA_HOOKS_LOG=1      → normale Konsolen-Ausgaben (Default)
#       OROMA_HOOKS_LOG_FILE=/pfad/datei.log  → Logdatei statt stdout
#
# Steuerung
# ─────────
#   - Aktivierung über agent_loop.register_hook(...)
#   - Jeder Hook bekommt (dt, tick) übergeben
#   - Läuft robust neben dem Hauptloop
# =============================================================================

import os
import random
import time
from core.self_assessment import SelfAssessment
from core.transfer_engine import TransferEngine
from mini_programs.calculator import Calculator
from core.log_guard import log_suppressed
import logging


# --------------------------------------------------------------------------- #
# Logging-Helfer (steuerbar per ENV)
# --------------------------------------------------------------------------- #

_OROMA_HOOKS_LOG = os.getenv("OROMA_HOOKS_LOG", "1").lower()
_LOG_FILE = os.getenv("OROMA_HOOKS_LOG_FILE")

def _log(msg: str) -> None:
    """Zentrale Logfunktion – deaktivierbar oder auf Datei umleitbar."""
    if _OROMA_HOOKS_LOG in ("0", "false", "off", "none"):
        return
    ts = time.strftime("[%H:%M:%S]")
    line = f"{ts} {msg}"
    try:
        if _LOG_FILE:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        else:
            print(line, flush=True)
    except Exception as e:
        # Fällt still zurück, falls kein Schreibrecht besteht.
        log_suppressed(
            logging.getLogger(__name__),
            key="core.hooks_patch1.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
def self_assessment_hook(dt: float, tick: int) -> None:
    """
    Führt alle 50 Ticks eine Selbstbewertung durch.
    Bewertet aktuelle Lernstatistiken und speichert MetaSnaps.
    """
    if tick % 50 == 0:
        stats = {
            "reward_avg": random.random(),
            "error_rate": random.random(),
            "duration": dt * 50,
        }
        sid = SelfAssessment.evaluate(stats)
        _log(f"[Patch1] SelfAssessment-Snap gespeichert: id={sid}")


# --------------------------------------------------------------------------- #
# TransferEngine-Hook
# --------------------------------------------------------------------------- #

def transfer_engine_hook(dt: float, tick: int) -> None:
    """
    Führt alle 30 Ticks eine einfache Mustererkennung aus.
    Beispiel: Sequenz ['A','B','C'] wird als 'linear' gespeichert.
    """
    if tick % 30 == 0:
        seq = ["A", "B", "C"]
        tid = TransferEngine.save_pattern(seq, "linear")
        _log(f"[Patch1] TransferSnap gespeichert: id={tid}")


# --------------------------------------------------------------------------- #
# Calculator-Hook
# --------------------------------------------------------------------------- #

def calculator_hook(dt: float, tick: int) -> None:
    """
    Stellt Lernaufgaben und speichert Ergebnisse.

    ⚠️ WICHTIG (Fix 2026-01-09)
    ─────────────────────────
    Dieser Hook war historisch nur als Demo gedacht. In älteren Ständen wurde
    hier fälschlich immer `task_id=1` mit `got=999` gespeichert.
    Das erzeugt massenhaft falsche `calculator_results` (z.B. 999.0) und
    verfälscht dadurch die Learning-Statistiken (insb. Level-1 "fill:" Tasks).

    Daher ist dieser Hook **standardmäßig deaktiviert** und muss explizit per
    ENV aktiviert werden.

    Aktivierung:
      - OROMA_PATCH1_CALC_HOOK=1

    Verhalten bei Aktivierung:
      - Alle 20 Ticks: neue Aufgabe erstellen
      - Direkt danach: Task aus DB lesen und mit dem Mini-Solver lösen
      - Ergebnis korrekt in DB schreiben (kein 999-Sentinel)

    Hinweis:
      Für produktives Lernen wird primär `core/curriculum_hook.py` verwendet.
      Dieser Patch1-Calculator-Hook ist nur noch ein optionales Debug-Tool.
    """

    # Default: aus – um Learning nicht zu verschmutzen.
    if os.getenv("OROMA_PATCH1_CALC_HOOK", "0").strip().lower() in ("0", "false", "no", "off"):
        return

    # Lazy Imports (headless-safe; niemals hard-fail)
    try:
        from core import sql_manager
        import json as _json
        import core.calc_solver as _calc_solver
    except Exception as e:
        log_suppressed(logging.getLogger(__name__), key="core.hooks_patch1.pass.calc_import", exc=e)
        return

    if tick % 20 != 0:
        return

    # 1) Task erzeugen
    tid = Calculator.new_task(level=1)
    _log(f"[Patch1] Calculator-Task erstellt: id={tid}")
    if not tid or int(tid) < 0:
        return

    # 2) Task aus DB lesen (expr/truth/truth_json)
    try:
        conn = sql_manager.get_conn()
        row = conn.execute(
            "SELECT expr, truth, truth_json FROM calculator_tasks WHERE id=?",
            (int(tid),),
        ).fetchone()
        if not row:
            return
        expr = str(row[0])
        truth = row[1]
        truth_json = None
        tj = row[2]
        if tj:
            try:
                truth_json = _json.loads(tj)
            except Exception:
                truth_json = None
    except Exception as e:
        log_suppressed(logging.getLogger(__name__), key="core.hooks_patch1.pass.calc_fetch", exc=e)
        return

    # 3) Lösen
    try:
        got, info = _calc_solver.solve(expr, truth=truth, truth_json=truth_json)
    except Exception as e:
        log_suppressed(logging.getLogger(__name__), key="core.hooks_patch1.pass.calc_solve", exc=e)
        return

    if got is None:
        _log(f"[Patch1] Calculator-Solve skipped (no-solve): expr={expr!r}")
        return

    # 4) Ergebnis speichern
    try:
        rid = Calculator.solve_task(task_id=int(tid), got=got, truth=truth)
        _log(f"[Patch1] Calculator-Result gespeichert: task_id={tid} result_id={rid} got={got} solver={info.get('solver') if isinstance(info, dict) else '?'}")
    except Exception as e:
        log_suppressed(logging.getLogger(__name__), key="core.hooks_patch1.pass.calc_store", exc=e)
        return
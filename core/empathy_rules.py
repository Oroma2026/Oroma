#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/empathy_rules.py
# Projekt:   ORÓMA (Offline-First · Headless · Empathie/Emotion-Layer)
# Modul:     Empathy Rules – sehr einfache Regelbasis + Event-Dispatcher (Brücke zu core.empathy)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul stellt eine **minimalistische, deterministische Regelbasis** bereit,
# um externe Ereignisse (Rewards, Gaps, Calculator, Games) in kleine „Stimmungs-Impulse“
# (Mood-Delta) zu übersetzen – ohne selbst Persistenz/DB-Logik zu besitzen.
#
# Die eigentliche Zustandsverwaltung, Persistenz, Historie und Ableitungen liegen in:
#   → core.empathy
#
# Dieses Modul ist damit eine „Policy-Adapter-Schicht“:
# - leicht importierbar (headless, stdlib)
# - leicht verständlich (wenige feste Regeln)
# - stabil (keine Side-Effects außerhalb core.empathy)
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine GUI/Qt/Wayland/X11.
# - Keine DB-Writes hier: Persistenz (falls aktiv) erfolgt ausschließlich in core.empathy.
# - Regeln sind bewusst „low risk“: nur kleine Deltas, keine komplexe Logik.
# - Deterministisch: identischer Input → identischer Output (keine Randomness).
#
# ABHÄNGIGKEITEN (EXAKT)
# ──────────────────────
# - core.empathy (als Backend):
#     • empathy.reward_event(reward: float, tag: str="reward") -> None
#     • empathy.manual_event(delta: float, tag: str="...") -> None
#
# Dieses Modul importiert:
#   from core import empathy
#
# ÖFFENTLICHE API (EXAKT IM CODE)
# ───────────────────────────────
# 1) on_reward(reward: float, tag: str="reward") -> None
#    - leitet Belohnungswerte direkt an core.empathy.reward_event weiter
#    - keine eigene Delta-Berechnung (Reward ist bereits „Signal“)
#
# 2) on_gap_detected(confidence: float) -> None
#    - übersetzt Gap-Confidence in ein kleines negatives Delta und ruft manual_event:
#        confidence > 0.7  → delta = -0.05
#        sonst             → delta = -0.15
#      Tag: "gap"
#
# 3) on_calculator_result(correct: bool) -> None
#    - kleiner Mood-Impuls aus Calculator-Ergebnis:
#        correct True  → delta = +0.05
#        correct False → delta = -0.10
#      Tag: "calculator"
#
# 4) on_game_outcome(won: bool) -> None
#    - kleiner Mood-Impuls aus Game-Outcome:
#        won True  → delta = +0.10
#        won False → delta = -0.05
#      Tag: "game"
#
# 5) apply_event(event: Dict[str,Any]) -> None
#    - generischer Dispatcher (Bridge-Funktion) für Hooks/Orchestrator:
#        erwartet event = {"type": <str>, "data": <any>}
#      Mapping:
#        type=="reward"     → on_reward(float(data or 0.0))
#        type=="gap"        → on_gap_detected(float(data or 0.0))
#        type=="calculator" → on_calculator_result(bool(data))
#        type=="game"       → on_game_outcome(bool(data))
#        sonst              → empathy.manual_event(0.0, tag="unknown")
#
# WICHTIGE SEMANTIK / TAGS (VERTRAG)
# ──────────────────────────────────
# Tags, die dieses Modul an core.empathy weitergibt:
#   - "reward"
#   - "gap"
#   - "calculator"
#   - "game"
#   - "unknown" (Fallback im Dispatcher)
#
# Diese Tags sind wichtig für:
# - spätere Analyse (History/Plots)
# - UI-Filter
# - Debugging (Warum Mood geändert wurde)
#
# INTEGRATION (ORÓMA-KONTEXT)
# ──────────────────────────
# Typische Caller:
# - Hooks (z. B. curriculum/gaps/calculator/game loops)
# - UI-Aktionen (wenn Events manuell ausgelöst werden)
# - Tools/Orchestrator (periodische Events)
#
# Dieses Modul ist bewusst klein, damit es überall importiert werden kann, ohne
# zusätzliche Subsysteme (Vision/Audio/LLM) mit zu ziehen.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - apply_event() akzeptiert immer {"type","data"} und crasht nicht wegen fehlender Keys.
# - Deltas bleiben klein und signiert wie dokumentiert (keine stillen Änderungen).
# - Keine DB-Logik hier hinzufügen (gehört in core.empathy oder in einen dedizierten Logger).
# - Tags bleiben stabil (UI/Stats/Tools verlassen sich darauf).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import time
from typing import Dict, Any

from core import empathy

# ----------------------------- Basis-Regeln ----------------------------------

def on_reward(reward: float, tag: str = "reward") -> None:
    """
    Regel: Bei Belohnungen wird der Empathie-Status angepasst.
    """
    empathy.reward_event(reward, tag=tag)


def on_gap_detected(confidence: float) -> None:
    """
    Regel: Wenn ein Gap erkannt wird → Stimmung leicht senken.
    Niedriges Confidence = größerer negativer Effekt.
    """
    delta = -0.05 if confidence > 0.7 else -0.15
    empathy.manual_event(delta, tag="gap")


def on_calculator_result(correct: bool) -> None:
    """
    Regel: Taschenrechner-Ergebnisse wirken auf Stimmung.
    """
    delta = 0.05 if correct else -0.1
    empathy.manual_event(delta, tag="calculator")


def on_game_outcome(won: bool) -> None:
    """
    Regel: Spiele (TicTacToe, Snake, etc.) beeinflussen Stimmung.
    """
    delta = 0.1 if won else -0.05
    empathy.manual_event(delta, tag="game")


# ----------------------------- Dispatcher ------------------------------------

def apply_event(event: Dict[str, Any]) -> None:
    """
    Generischer Event-Dispatcher.
    Erwartet Dict mit: {"type": str, "data": ...}
    """
    etype = event.get("type")
    data = event.get("data")

    if etype == "reward":
        on_reward(float(data or 0.0))
    elif etype == "gap":
        on_gap_detected(float(data or 0.0))
    elif etype == "calculator":
        on_calculator_result(bool(data))
    elif etype == "game":
        on_game_outcome(bool(data))
    else:
        empathy.manual_event(0.0, tag="unknown")
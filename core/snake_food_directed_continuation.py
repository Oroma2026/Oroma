#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_food_directed_continuation.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Snake Food-Directed Continuation Protocol v2
# Version: v0.1.0-safe-food-directed-v2
# Stand:   2026-07-16
# Autor:   Jörg + GPT-5.6 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul implementiert ausschließlich die deterministische Auswahl der
# Fortsetzungsaktion nach dem unverändert erzwungenen Promotion-Schritt eines
# Targeted-Evidence-Experiments. Es verändert weder Snake-Physik noch Credit-
# Assignment, Evidence-Gates, Lifecycle, Promotion, Queue oder Policy.
#
# PROTOKOLLVERTRAG
# ─────────────────
#   1. Prüfe relative Aktionen 0=forward, 1=left, 2=right gegen die bestehende
#      Produktions-Kollisionslogik des aufrufenden Runners.
#   2. Verwerfe unmittelbar tödliche Aktionen.
#   3. Wähle unter den sicheren Aktionen die kleinste Manhattan-Distanz des
#      hypothetischen nächsten Kopfes zum aktuellen Futter.
#   4. Bei gleicher Distanz gilt unverändert der deterministische Tie-Breaker
#      forward -> left -> right, abgebildet durch die Action-ID 0 < 1 < 2.
#   5. Existiert keine sichere Aktion, wird forward gewählt, damit das reale
#      physikalische Todesereignis beobachtet und nicht verborgen wird.
#
# ARCHITEKTURGRENZEN
# ─────────────────
#   • Keine Datenbank- oder Dateizugriffe.
#   • Keine Zufallsentscheidung, Live-Policy oder Exploration.
#   • Kein A*, keine Pfadplanung und kein Reward Shaping.
#   • Keine eigene Kollisionsphysik: Der Aufrufer liefert bereits ausgewertete
#     Kandidaten aus der kanonischen Runner-Physik.
# =============================================================================

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Sequence

VERSION = "v0.1.0-safe-food-directed-v2"
PROTOCOL_ID = "snake_continuation:safe_food_directed_v2"
PROTOCOL_VERSION = "v2"
ACTION_PRIORITY = (0, 1, 2)


def _manhattan(a: Sequence[int], b: Sequence[int]) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def select_food_directed_action(
    evaluated_candidates: Iterable[Mapping[str, Any]],
    food: Sequence[int],
) -> Dict[str, Any]:
    """Select the safest food-directed relative action deterministically.

    ``evaluated_candidates`` must originate from the canonical targeted Snake
    runner and contain ``action``, ``next_head``, ``collision`` and ``safe``.
    The function deliberately does not recompute collision semantics.
    """
    evaluated = [dict(item) for item in evaluated_candidates]
    safe = []
    for item in evaluated:
        action = int(item.get("action", -1))
        next_cell = item.get("next_head")
        if action not in ACTION_PRIORITY or not isinstance(next_cell, (list, tuple)) or len(next_cell) != 2:
            continue
        if bool(item.get("safe")) and item.get("collision") is None:
            candidate = dict(item)
            candidate["food_distance"] = _manhattan(next_cell, food)
            safe.append(candidate)

    if not safe:
        return {
            "action": 0,
            "evaluated": evaluated,
            "safe_candidates": [],
            "selection_key": None,
            "fallback_to_forward": True,
            "protocol": PROTOCOL_ID,
            "protocol_version": PROTOCOL_VERSION,
        }

    selected = min(safe, key=lambda item: (int(item["food_distance"]), int(item["action"])))
    return {
        "action": int(selected["action"]),
        "evaluated": evaluated,
        "safe_candidates": safe,
        "selection_key": [int(selected["food_distance"]), int(selected["action"])],
        "fallback_to_forward": False,
        "protocol": PROTOCOL_ID,
        "protocol_version": PROTOCOL_VERSION,
    }


__all__ = [
    "VERSION",
    "PROTOCOL_ID",
    "PROTOCOL_VERSION",
    "ACTION_PRIORITY",
    "select_food_directed_action",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/direct_outcome_normalization.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Kanonische Direct-Step-Outcome-Normalisierung
# Version:   v0.1.0-float-contract-compatibility
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul stellt genau eine kanonische, nebenwirkungsfreie Normalisierung
# fuer direkt auf einem Step gespeicherte Outcomes bereit. Es schliesst den
# Producer-/Consumer-Vertragsbruch, bei dem Targeted-Evidence-Producer bewusst
# numerische Float-Werte wie 1.0, -1.0 und 0.0 schreiben, waehrend historische
# Consumer zuvor nur die Stringformen "1", "-1" und "0" akzeptierten.
#
# VERTRAG
# -------
# Eingabe ist ein einzelner Step als Mapping. Ausgewertet werden in fester,
# fachlich relevanter Prioritaet:
#
#   1. outcome
#   2. result
#   3. reward
#
# Ein vorhandenes, aber ungueltiges hoeher priorisiertes Feld blockiert
# fail-closed. Es wird nicht still durch ein niedriger priorisiertes Feld
# ersetzt. Dadurch kann ein korrumpiertes oder semantisch unbekanntes outcome
# nicht durch einen zufaellig vorhandenen reward-Wert maskiert werden.
#
# AKZEPTIERTE REPRÄSENTATIONEN
# ---------------------------
# - Semantische Tokens: pos/positive/win/won/success/true,
#   neg/negative/loss/lose/lost/failure/fail/false,
#   draw/neutral/tie.
# - Endliche numerische Werte und numerische Strings. Das Vorzeichen bestimmt
#   pos, neg oder draw. Damit sind insbesondere +/-1.0 sowie "-1.0" gueltig.
# - Nicht endliche Zahlen (NaN, +Inf, -Inf) werden fail-closed verworfen.
#
# RUECKGABE
# ---------
# Tuple(normalized, source_field, raw_value)
#
# - Erfolg: normalized ist pos/neg/draw und source_field ist outcome/result/
#   reward.
# - Ungueltiges outcome/result: normalized=None und source_field ist
#   unsupported_direct_<feld>.
# - Ungueltiger reward: normalized=None und source_field ist
#   invalid_direct_reward.
# - Kein direktes Feld: (None, None, None). Diese Form bleibt absichtlich
#   kompatibel zu den bisherigen Consumern und deren Blockgrund-Mapping.
#
# SICHERHEITS- UND ARCHITEKTURINVARIANTEN
# --------------------------------------
# - Reine Funktion: keine DB-, Datei-, State-, Queue- oder Policy-Writes.
# - Keine Root-/Episode-Outcomes; nur das uebergebene Step-Mapping wird gelesen.
# - Keine implizite zeitliche Kreditzuweisung.
# - Unbekannte Tokens und nicht endliche Zahlen blockieren fail-closed.
# - Headless und ohne optionale GUI-/Hardware-Abhaengigkeiten.
# =============================================================================

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Tuple

NormalizedDirectOutcome = Tuple[Optional[str], Optional[str], Any]

_POSITIVE_TOKENS = frozenset({
    "pos", "positive", "win", "won", "success", "true",
})
_NEGATIVE_TOKENS = frozenset({
    "neg", "negative", "loss", "lose", "lost", "failure", "fail", "false",
})
_DRAW_TOKENS = frozenset({
    "draw", "neutral", "tie",
})


def _normalize_numeric(raw: Any) -> Optional[str]:
    """Normalize one finite numeric value by sign; return None if invalid."""
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    if value > 0.0:
        return "pos"
    if value < 0.0:
        return "neg"
    return "draw"


def normalize_direct_outcome(step: Mapping[str, Any]) -> NormalizedDirectOutcome:
    """Return a canonical outcome stored directly on the supplied step.

    The function deliberately does not inspect any trajectory/root object and
    therefore cannot reinterpret an episode outcome as direct state/action
    evidence. Field priority and fail-closed behavior match the historical
    ORÓMA consumers while extending their accepted numeric representation to
    finite floats and numeric strings.
    """
    for key in ("outcome", "result"):
        raw = step.get(key)
        if raw in (None, ""):
            continue

        text = str(raw).strip().lower()
        if text in _POSITIVE_TOKENS:
            return "pos", key, raw
        if text in _NEGATIVE_TOKENS:
            return "neg", key, raw
        if text in _DRAW_TOKENS:
            return "draw", key, raw

        normalized = _normalize_numeric(raw)
        if normalized is not None:
            return normalized, key, raw
        return None, f"unsupported_direct_{key}", raw

    raw = step.get("reward")
    if raw not in (None, ""):
        normalized = _normalize_numeric(raw)
        if normalized is None:
            return None, "invalid_direct_reward", raw
        return normalized, "reward", raw

    return None, None, None

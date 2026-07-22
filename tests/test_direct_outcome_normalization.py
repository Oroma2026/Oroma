#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tests/test_direct_outcome_normalization.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Regressionstest · Kanonischer Direct-Step-Outcome-Vertrag
# Stand:     2026-07-18
# =============================================================================
#
# ZWECK
# -----
# Dieser Test sichert den Producer-/Consumer-Vertrag fuer direkte Step-Evidenz.
# Targeted-Evidence-Producer schreiben endliche numerische Floats; Capability
# Registry und Replay Evidence Probe muessen dieselben Werte kanonisch lesen.
#
# GEPRÜFTE INVARIANTEN
# --------------------
# - Ints, Floats und numerische Strings werden nach Vorzeichen normalisiert.
# - Historische semantische Tokens bleiben abwaertskompatibel.
# - outcome hat Prioritaet vor result und reward.
# - Ein ungueltiges outcome wird nicht durch reward maskiert (fail-closed).
# - NaN und +/-Inf sind keine gueltige Evidenz.
# - Beide produktiven Consumer verwenden exakt dieselbe zentrale Funktion.
# - Keine DB-, Datei-, State-, Queue- oder Policy-Writes.
# =============================================================================

from __future__ import annotations

import pytest

from core.direct_outcome_normalization import normalize_direct_outcome
from core import gap_replay_evidence_probe as probe
from core import replay_evidence_capability as capability


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, "pos"), (1.0, "pos"), ("1", "pos"), ("1.0", "pos"),
        ("+1", "pos"), ("+1.0", "pos"), (0.5, "pos"), ("0.5", "pos"),
        (-1, "neg"), (-1.0, "neg"), ("-1", "neg"), ("-1.0", "neg"),
        (-0.5, "neg"), ("-0.5", "neg"),
        (0, "draw"), (0.0, "draw"), ("0", "draw"), ("0.0", "draw"),
        (-0.0, "draw"),
    ],
)
def test_outcome_accepts_finite_numeric_representations(raw, expected):
    assert normalize_direct_outcome({"outcome": raw}) == (expected, "outcome", raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pos", "pos"), ("Positive", "pos"), ("WIN", "pos"),
        ("won", "pos"), ("success", "pos"), (True, "pos"),
        ("neg", "neg"), ("Negative", "neg"), ("LOSS", "neg"),
        ("lose", "neg"), ("lost", "neg"), ("failure", "neg"),
        ("fail", "neg"), (False, "neg"),
        ("draw", "draw"), ("Neutral", "draw"), ("TIE", "draw"),
    ],
)
def test_outcome_preserves_semantic_tokens(raw, expected):
    assert normalize_direct_outcome({"outcome": raw}) == (expected, "outcome", raw)


def test_result_is_used_when_outcome_is_absent():
    assert normalize_direct_outcome({"result": -1.0, "reward": 1.0}) == ("neg", "result", -1.0)


def test_reward_is_used_when_outcome_and_result_are_absent():
    assert normalize_direct_outcome({"reward": 0.5}) == ("pos", "reward", 0.5)


def test_invalid_outcome_blocks_fail_closed_without_reward_fallback():
    step = {"outcome": "corrupted_state", "reward": 1.0}
    assert normalize_direct_outcome(step) == (
        None,
        "unsupported_direct_outcome",
        "corrupted_state",
    )


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf"])
def test_non_finite_outcome_is_rejected(raw):
    normalized, field, returned_raw = normalize_direct_outcome({"outcome": raw})
    assert normalized is None
    assert field == "unsupported_direct_outcome"
    if isinstance(raw, float) and raw != raw:
        assert isinstance(returned_raw, float) and returned_raw != returned_raw
    else:
        assert returned_raw == raw


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), "not-a-number"])
def test_invalid_reward_is_rejected(raw):
    normalized, field, returned_raw = normalize_direct_outcome({"reward": raw})
    assert normalized is None
    assert field == "invalid_direct_reward"
    if isinstance(raw, float) and raw != raw:
        assert isinstance(returned_raw, float) and returned_raw != returned_raw
    else:
        assert returned_raw == raw


def test_missing_direct_outcome_remains_compatible():
    assert normalize_direct_outcome({"state_hash": "s", "action": "1"}) == (None, None, None)


def test_both_consumers_share_the_exact_canonical_function():
    assert capability._normalize_direct_outcome is normalize_direct_outcome
    assert probe._normalize_direct_outcome is normalize_direct_outcome

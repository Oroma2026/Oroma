#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/social_resonance_tick.py
# Projekt: ORÓMA
# Version: v3.7 (Social Resonance Tick – Empathie-Δ → Reward-Brücke)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Periodischer, schneller Check, ob sich der Empathie-Score in einem Fenster
#   (Default 10 Minuten) signifikant verbessert hat. Falls ja, wird ein
#   kleines Reward-Event geloggt: source="empathy".
#
# Aufruf
# ──────
#   /usr/bin/python3 /opt/ai/oroma/tools/social_resonance_tick.py
#
# ENV (optional)
# ──────────────
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_EMPATHY_WINDOW_SEC=600
#   OROMA_EMPATHY_MIN_DELTA=0.2
#   OROMA_EMPATHY_REWARD=0.02
# =============================================================================

from __future__ import annotations
import os
import sys
import time

BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core import reward  # noqa: E402

def _get_env_float(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "")
        return float(v) if v not in ("", None) else default
    except Exception:
        return default

def _get_env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name, "")
        return int(v) if v not in ("", None) else default
    except Exception:
        return default

def main() -> int:
    window = _get_env_int("OROMA_EMPATHY_WINDOW_SEC", 600)
    min_delta = _get_env_float("OROMA_EMPATHY_MIN_DELTA", 0.2)
    rwd = _get_env_float("OROMA_EMPATHY_REWARD", 0.02)

    rid = reward.log_empathy_positive_shift(window_sec=window, min_delta=min_delta, reward_value=rwd)
    ts = int(time.time())
    if rid is not None and rid > 0:
        print(f"[social_resonance_tick] {ts}: empathy reward logged (row_id={rid}, window={window}s, min_delta={min_delta}, val={rwd})")
    else:
        print(f"[social_resonance_tick] {ts}: no positive shift (window={window}s, min_delta={min_delta})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/self_rec_hook.py
# Projekt: ORÓMA v3.7
# Version: v1.0
# Stand:   2025-09-29
#
# Zweck
# ─────
#   AgentLoop-Hook: führt in großen Abständen (z. B. alle 5 Minuten)
#   eine kurze Selbsterkennungs-Session durch (LED oder PiCar),
#   loggt Reward bei Erfolg und kann optional eine Selbstmeldung sprechen.
#
# ENV
# ───
#   OROMA_SELFREC_PERIOD=300     (Sek., Mindestabstand)
#   OROMA_SELFREC_METHOD=auto|led|picar
#   OROMA_SELFREC_SPEAK=true|false
# =============================================================================

from __future__ import annotations
import os, time
from core import self_recognition
from core.log_guard import log_suppressed
import logging

# TTS (optional)
def _say(text: str) -> None:
    try:
        from ui import tts
        tts.say(text)
    except Exception:
        try:
            from ui import audio_ui  # type: ignore
            if hasattr(audio_ui, "say"):
                audio_ui.say(text)   # type: ignore
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.self_rec_hook.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

_LAST = 0.0
_PERIOD = int(os.environ.get("OROMA_SELFREC_PERIOD", "300"))
_METHOD = os.environ.get("OROMA_SELFREC_METHOD", "auto").strip().lower()
_SPEAK  = os.environ.get("OROMA_SELFREC_SPEAK", "true").lower() not in ("0","false","no","off")

def self_rec_hook(dt: float, tick: int) -> None:
    global _LAST
    # alle 30 Ticks (~ dt * 30) prüfen → periodische Gate mit _PERIOD
    if tick % 30 != 0:
        return
    now = time.time()
    if now - _LAST < _PERIOD:
        return

    res = self_recognition.run_auto(prefer=_METHOD)
    _LAST = now

    if _SPEAK:
        if res.get("ok"):
            _say(f"Ich sehe mich selbst. Score {res.get('score',0.0):.2f}.")
        else:
            _say("Selbsterkennung war unklar.")
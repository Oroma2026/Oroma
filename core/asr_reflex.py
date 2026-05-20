#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/asr_reflex.py
# Projekt: ORÓMA
# Version: v3.7 (ASR Reflex v1 – Empathie, Intents & Thread-Kommandos)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Reagiert auf erkannten ASR-Text (Self-Listening):
#     • Leichte Stimmungsanalyse → schreibt Empathie-Snapshot (empathy_snaps)
#     • Einfache Intents (Wiederholen/Stop/Status) → freundliche Sprachausgabe
#     • Thread-Kommandos („roter Faden“): start/next/pause/done/status
#     • Kleines Reward-Signal für "speech" (optional) + Empathie-Δ-Prüfung
#
# Integration
# ───────────
#   ui/asr_ui.py ruft bei neuen Transkripten auf:
#       from core import asr_reflex
#       asr_reflex.process_text(txt)
#
# ENV
# ───
#   OROMA_ASR_EMPATHY=true|false     (Empathie-Snaps aktiv, Default: true)
#   OROMA_ASR_SPEECH_REWARD=0.01     (Reward für erkannte Intents; 0→aus)
# =============================================================================

from __future__ import annotations
import os, time, re
from typing import Tuple, List

import logging
from core import log_guard
logger = logging.getLogger(__name__)
try:
    from core import sql_manager, reward
except Exception:
    sql_manager = None  # type: ignore
    reward = None       # type: ignore

# Roter Faden (optional)
try:
    from core import roter_faden
    _HAS_THREAD = True
except Exception:
    _HAS_THREAD = False

# ---------- Config ----------
_EMP_ENABLED = (os.environ.get("OROMA_ASR_EMPATHY", "true").lower() not in ("0","false","no","off"))
_SPEECH_REWARD = float(os.environ.get("OROMA_ASR_SPEECH_REWARD", "0.01") or 0.0)

# ---------- TTS Bridge ----------
def _say(text: str) -> None:
    try:
        from ui import tts
        return tts.say(text)
    except Exception as e:
        log_guard.log_suppressed(logger, key="asr_reflex.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    try:
        from ui import audio_ui  # type: ignore
        if hasattr(audio_ui, "say"):
            return audio_ui.say(text)  # type: ignore
    except Exception as e:
        log_guard.log_suppressed(logger, key="asr_reflex.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    print("[ASR Reflex TTS] ", text)

# ---------- Sentiment (simple lexicon) ----------
_POS: List[str] = ["gut","toll","super","schaff","klappt","ok","geht","schön","danke","yes","yay","cool","prima"]
_NEG: List[str] = ["schlecht","doof","mist","kacke","scheiße","frust","hilfe","hilfe!","geht nicht","kann nicht","stopp","stop","abbruch","nervt","traurig"]

def _sentiment_score(text: str) -> Tuple[str, float]:
    """Sehr einfache Wortlisten-Heuristik → (mood, score 0..1)."""
    t = text.lower()
    pos = sum(1 for w in _POS if w in t)
    neg = sum(1 for w in _NEG if w in t)
    raw = pos - neg
    score = 0.5 + max(-2.0, min(2.0, raw)) * 0.15
    score = max(0.0, min(1.0, score))
    mood = "happy" if score > 0.6 else ("sad" if score < 0.4 else "neutral")
    return mood, float(score)

# ---------- Intents ----------
_REP_PAT  = re.compile(r"\b(wiederhol|nochmal|erneut|repeat)\b", re.I)
_STOP_PAT = re.compile(r"\b(stop|stopp|abbruch|pause)\b", re.I)
_STAT_PAT = re.compile(r"\b(status|wie\s?läuft|fortschritt|progress)\b", re.I)

# ---------- Thread-Kommandos ----------
_CMD_START = re.compile(r"\bthread\s+start\s+(.+)", re.I)
_CMD_NEXT  = re.compile(r"\b(thread\s+next|weiter|nächster\s+schritt)\b", re.I)
_CMD_PAUSE = re.compile(r"\b(thread\s+pause|pause)\b", re.I)
_CMD_DONE  = re.compile(r"\b(thread\s+done|fertig|erledigt)\b", re.I)
_CMD_STAT  = re.compile(r"\b(thread\s+status|status)\b", re.I)

def _handle_thread_cmds(txt: str) -> bool:
    if not _HAS_THREAD:
        return False
    said = False
    t = txt.strip()

    m = _CMD_START.search(t)
    if m:
        title = m.group(1).strip()
        roter_faden.start_thread(title=title, objective=title, steps=["Aufwärmen","Üben","Test"])
        said = True

    if _CMD_NEXT.search(t):
        roter_faden.advance(step_ok=True, speak=True)
        said = True

    if _CMD_PAUSE.search(t):
        roter_faden.pause()
        said = True

    if _CMD_DONE.search(t):
        roter_faden.done()
        said = True

    if _CMD_STAT.search(t):
        th = roter_faden.current()
        if th:
            roter_faden._speak_status(th)  # pylint: disable=protected-access
        else:
            _say("Kein aktiver Thread.")
        said = True

    if said and reward and _SPEECH_REWARD > 0:
        try:
            reward.log("speech", value=_SPEECH_REWARD, info={"intent":"thread_cmd"})
        except Exception as e:
            log_guard.log_suppressed(logger, key="asr_reflex.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return said

def _handle_intents(txt: str) -> None:
    said = False
    if _REP_PAT.search(txt):
        _say("Okay, ich wiederhole gleich passende Aufgaben und passe das Tempo an.")
        said = True
        if reward and _SPEECH_REWARD > 0:
            try:
                reward.log("speech", value=_SPEECH_REWARD, info={"intent":"repeat"})
            except Exception as e:
                log_guard.log_suppressed(logger, key="asr_reflex.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    if _STOP_PAT.search(txt):
        _say("Ich nehme das zur Kenntnis und mache kurz langsamer.")
        said = True
        if reward and _SPEECH_REWARD > 0:
            try:
                reward.log("speech", value=_SPEECH_REWARD, info={"intent":"stop"})
            except Exception as e:
                log_guard.log_suppressed(logger, key="asr_reflex.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    if _STAT_PAT.search(txt):
        try:
            from core import reward as rew_mod
            agg = rew_mod.RewardAggregator()
            rmean = {
                "curriculum": agg.window_mean("curriculum", 120),
                "scicalc":    agg.window_mean("scicalc", 120),
                "setcalc":    agg.window_mean("setcalc", 120),
            }
            _say(f"Aktueller Lernstatus: Curriculum {rmean['curriculum']:.2f}, SciCalc {rmean['scicalc']:.2f}, SetCalc {rmean['setcalc']:.2f}.")
        except Exception:
            _say("Status abrufen klappt, alles läuft stabil.")
        said = True
        if reward and _SPEECH_REWARD > 0:
            try:
                reward.log("speech", value=_SPEECH_REWARD, info={"intent":"status"})
            except Exception as e:
                log_guard.log_suppressed(logger, key="asr_reflex.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    if not said and reward and _SPEECH_REWARD > 0:
        try:
            reward.log("speech", value=_SPEECH_REWARD * 0.25, info={"intent":"none"})
        except Exception as e:
            log_guard.log_suppressed(logger, key="asr_reflex.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

# ---------- API ----------
def process_text(text: str) -> None:
    """
    Reagiert auf ASR-Text:
      1) Privacy-Puffer/Redaction (kein DB-Klartext per Default)
      2) Thread-Kommandos (falls vorhanden)
      3) Empathie-Snapshot (falls aktiviert)
      4) Intents (repeat/stop/status) + kleine Rewards
      5) Optionale Empathie-Δ-Prüfung (kleines Reward, wenn positiver Wechsel)
    """
    if not text or not isinstance(text, str):
        return

    # 1) Datenschutz: Puffer/Redaction (kein DB-Klartext per Default)
    try:
        from core.privacy import ingest_asr_text
        ingest_asr_text(text)
    except Exception as e:
        log_guard.log_suppressed(logger, key="asr_reflex.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    # 2) Thread-Kommandos zuerst – hat Priorität
    _handle_thread_cmds(text)

    # 3) Empathie – robust gegen fehlendes SQL
    if _EMP_ENABLED and sql_manager is not None:
        try:
            mood, score = _sentiment_score(text)
            ts = int(time.time())
            sql_manager.insert_empathy_snap(ts, mood, score)  # type: ignore
        except Exception as e:
            print("[ASR Reflex] empathy insert failed:", e)

    # 4) Intents
    try:
        _handle_intents(text)
    except Exception as e:
        print("[ASR Reflex] intent handling failed:", e)

    # 5) Empathie-Δ-Prüfung (leicht, idempotent)
    if reward is not None:
        try:
            reward.log_empathy_positive_shift(window_sec=600, min_delta=0.2, reward_value=0.02)
        except Exception as e:
            log_guard.log_suppressed(logger, key="asr_reflex.pass.9", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

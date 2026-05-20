#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/mangel_speak_hook.py
# Projekt: ORÓMA v3.7
# Version: v3.7 (MangelSpeak + Empathie-Policy + Thread-Kontext)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Automatisches „Sprechen“ bei erkannten Mängeln (Knowledge Gaps, Confidence ↓),
#   gekoppelt an Empathie-Signale (negativer Mood/Trend) und angereichert mit
#   Thread-Kontext („roter Faden“).
#
# Features
# ────────
#   • Prüft periodisch (alle 60 Ticks) auf Mängel via diagnostics.quick_summary()
#   • Empathie-Kopplung:
#       - Trigger auch, wenn letzter empathy_snaps.score < THRESH (Default 0.40)
#       - ODER wenn innerhalb WINDOW_SEC (Default 600s) score signifikant ↓ (DROP 0.15)
#   • Dynamische, empathische Formulierungen + Thread-Hinweis
#   • TTS-Ausgabe (ui/tts oder ui/audio_ui Fallback)
#   • Optional: Reward-Log für Sprechakt (source='speech') – mit Thread-Attach
#   • Optional: sofortige Wiederholung; Delay abhängig von Stimmung
# =============================================================================

from __future__ import annotations

import os
import random
import time
import logging
from typing import Tuple, Optional

from core import diagnostics, curriculum, reward
try:
    from core import sql_manager
except Exception:
    sql_manager = None  # graceful fallback


# Curiosity optional (Motivationssignal):
# - Wenn aktiv, wird aus diagnostics.quick_summary() ein CuriositySignal abgeleitet und in curiosity_log + metrics geschrieben.
# - Design: minimal-invasiv, rate-limited (nur in den ohnehin periodischen Checks), keine Hard-Dependency.
try:
    from core import curiosity  # type: ignore
    _HAS_CURIOSITY = True
except Exception:
    curiosity = None  # type: ignore
    _HAS_CURIOSITY = False

_CURIOSITY_FROM_DIAGNOSTICS = os.environ.get("OROMA_CURIOSITY_FROM_DIAGNOSTICS", "1").strip() not in ("0", "false", "False", "no", "NO")
_CURIOSITY_SOURCE = os.environ.get("OROMA_CURIOSITY_SOURCE", "mangel").strip() or "mangel"
_CURIOSITY_TAG = os.environ.get("OROMA_CURIOSITY_TAG", "mangel_speak").strip() or None

_cur_logger = None
_last_cur_log = 0.0

def _clamp01(x: float) -> float:
    try:
        return float(max(0.0, min(1.0, float(x))))
    except Exception:
        return 0.0

def _maybe_log_curiosity_from_gaps(gaps: dict, *, emp_last: float = 1.0, emp_drop: float = 0.0) -> None:
    """Ableitung eines Curiosity-Signals aus diagnostischen Mangel-Metriken.

    Hintergrund:
      - Curiosity existiert als Modul+DB, war aber im Kern bisher kaum „gefüttert“.
      - MangelSpeak hat genau die passenden Komponenten (confidence/coverage/novelty/time_to_goal_norm).
      - Wir loggen hier ein robustes, normalisiertes Signal, das Exploration/Task-Choice später gewichten kann.

    Safe-Design:
      - Nur aktiv, wenn OROMA_CURIOSITY_FROM_DIAGNOSTICS != 0 und core.curiosity importierbar ist.
      - Best effort: Fehler werden unterdrückt (log_suppressed), kein Crash im AgentLoop.
    """
    global _cur_logger, _last_cur_log
    if not _CURIOSITY_FROM_DIAGNOSTICS or not _HAS_CURIOSITY:
        return
    now = time.time()
    # zusätzlicher Rate-Limit-Schutz (falls Hook-Intervalle reduziert werden)
    if now - _last_cur_log < 30.0:
        return

    conf = _clamp01(gaps.get("confidence", 1.0))
    cov = _clamp01(gaps.get("coverage", 1.0))
    nov = _clamp01(gaps.get("novelty", 0.0))
    tgoal = _clamp01(gaps.get("time_to_goal_norm", 0.0))

    # Intuition:
    #   - niedrige Confidence & Coverage → starke Neugier / Bedarf nach Exploration
    #   - hohe Novelty → Neugier, aber etwas geringer gewichtet (damit nicht „Neuheits-Junkie“)
    #   - hohe time_to_goal_norm → Exploration/Wechsel kann sinnvoll sein
    #   - Empathie: wenn Stimmung droppt (emp_drop>0), Neugier leicht dämpfen (mehr Stabilität/Vertrauen)
    base = (1.0 - conf) * 0.35 + (1.0 - cov) * 0.35 + nov * 0.15 + tgoal * 0.15
    damp = _clamp01(1.0 - _clamp01(emp_drop) * 0.25)
    sig_val = _clamp01(base * damp)

    try:
        if _cur_logger is None:
            _cur_logger = curiosity.CuriosityLogger()
        sig = curiosity.CuriositySignal(sig_val, {
            "confidence": conf,
            "coverage": cov,
            "novelty": nov,
            "time_to_goal_norm": tgoal,
            "emp_last": _clamp01(emp_last),
            "emp_drop": _clamp01(emp_drop),
            "damp": damp,
        }).clamp()
        _cur_logger.log(_CURIOSITY_SOURCE, sig, tag=_CURIOSITY_TAG)
        _last_cur_log = now
    except Exception as e:
        # Keine stillen Fehler – aber rate-limited
        log_suppressed(
            logging.getLogger(__name__),
            key="core.mangel_speak.curiosity.1",
            exc=e,
            msg="Suppressed exception (curiosity log from diagnostics)",
            level=logging.WARNING,
            interval_s=600,
        )

# Roter Faden optional
try:
    from core import roter_faden
    _HAS_THREAD = True
except Exception:
    _HAS_THREAD = False

# ---- TTS-Bridge --------------------------------------------------------------
from core.log_guard import log_suppressed

# Speak throttling / rate limiting
# - prevents spam in logs/audio when the system is in a 'mangel' state.
# - value in seconds; default 15 minutes.
_interval_env = (
    os.environ.get("OROMA_MANGEL_SPEAK_INTERVAL_SEC")
    or os.environ.get("OROMA_MANGEL_INTERVAL")
    or "900"
)
_INTERVAL = float(_interval_env)

# Optional reward injection when we spoke (keeps the system 'motivated' to report missing sensors).
# Env: OROMA_SPEECH_REWARD or legacy OROMA_MANGEL_SPEAK_REWARD
_SPEECH_REWARD = float(
    os.environ.get("OROMA_SPEECH_REWARD", os.environ.get("OROMA_MANGEL_SPEAK_REWARD", "0.05"))
)

_last_speak: float = 0.0


_LOG = logging.getLogger("oroma.mangel_speak")

def _say(text: str) -> None:
    """Speak text via wrappers.tts_wrapper (non-fatal).

    Rationale: In ORÓMA v3.7.x the TTS implementation lives in wrappers/
    and may be absent depending on runtime environment.
    We therefore try to call it, but never let this hook crash the loop."""
    try:
        from wrappers import tts_wrapper
        # Non-blocking by default; blocking speech can stall AgentLoop.
        # NOTE: API drift across ORÓMA versions:
        #   - some wrappers use: speak(text, blocking: bool = True)
        #   - some older code used: speak(text, block: bool = True)
        # We support both (and a plain speak(text) fallback).
        try:
            tts_wrapper.speak(text, blocking=False)
        except TypeError:
            try:
                tts_wrapper.speak(text, block=False)
            except TypeError:
                tts_wrapper.speak(text)
    except Exception as e:
        # Log at most once per minute to avoid log spam.
        log_suppressed(
            _LOG,
            key="mangel_speak_hook.tts",
            msg="TTS not available (wrappers.tts_wrapper.speak failed)",
            exc=e,
            level="WARNING",
            interval_s=60.0,
        )
_EMP_ENABLE = (os.environ.get("OROMA_MANGEL_EMPATHY_ENABLE", "true").lower() not in ("0", "false", "no", "off"))
_EMP_THRESH = float(os.environ.get("OROMA_MANGEL_EMPATHY_THRESH", "0.40"))
_EMP_DROP   = float(os.environ.get("OROMA_MANGEL_EMPATHY_DROP",   "0.15"))
_EMP_WINSEC = int(os.environ.get("OROMA_MANGEL_EMPATHY_WINDOW_SEC", "600"))

# ---- Empathie-Auswertung -----------------------------------------------------
def _fetch_last_empathy(limit: int = 3):
    if not sql_manager:
        return []
    try:
        return sql_manager.fetch_last_empathy(limit)
    except Exception:
        return []

def _as_tuple(row) -> Tuple[int, str, float]:
    """Normalisiert eine empathy_snaps-Zeile zu (ts, mood, score).

    Unterstützt:
      - dict (row_factory)
      - sqlite3.Row (Mapping/Sequence)
      - tuple/list
    """
    if row is None:
        return (0, "neutral", 0.5)
    # dict
    if isinstance(row, dict):
        return int(row.get("ts", 0)), str(row.get("mood", "neutral")), float(row.get("score", 0.5))
    # sqlite3.Row oder ähnliches Mapping/Sequence
    try:
        ts = row["ts"]
        mood = row["mood"]
        score = row["score"]
        return int(ts), str(mood), float(score)
    except Exception:
        try:
            return int(row[0]), str(row[1]), float(row[2])
        except Exception:
            return (0, "neutral", 0.5)

def _empathy_trigger() -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Liefert (trigger, last_score, delta_neg).
    trigger = True, wenn letzter Score < THRESH oder negativer Δ >= DROP innerhalb WINSEC.
    """
    if not _EMP_ENABLE:
        return (False, None, None)

    rows = _fetch_last_empathy(3)
    if not rows:
        return (False, None, None)

    ts_last, mood_last, score_last = _as_tuple(rows[0])
    if score_last < _EMP_THRESH:
        return (True, score_last, None)

    base_score = None
    now = time.time()
    for r in reversed(rows):
        ts, _, sc = _as_tuple(r)
        if now - ts <= _EMP_WINSEC:
            base_score = sc
            break
    if base_score is not None:
        delta = base_score - score_last  # positiv = Abnahme
        if delta >= _EMP_DROP:
            return (True, score_last, delta)
    return (False, score_last, None)

# ---- Sprachbausteine ---------------------------------------------------------
def _build_message(gaps: dict, score_last: Optional[float], delta_neg: Optional[float]) -> str:
    empathic_intro = ""
    if score_last is not None:
        if score_last < 0.35:
            empathic_intro = "Ich höre Frust. "
        elif score_last < 0.5:
            empathic_intro = "Klingt herausfordernd. "
    if delta_neg is not None:
        empathic_intro += "Es wirkt anstrengender als zuvor. "

    parts = [empathic_intro.strip()]
    conf = gaps.get("confidence")
    cov  = gaps.get("coverage")
    nov  = gaps.get("novelty")
    t2g  = gaps.get("time_to_goal_norm")

    if conf is not None:
        parts.append(f"Meine Confidence ist niedrig: {conf:.2f}.")
    if cov is not None:
        parts.append(f"Meine Coverage ist {cov:.2f}.")
    if nov is not None:
        parts.append(f"Neuheit: {nov:.2f}.")
    if t2g is not None:
        parts.append(f"Zeit bis Ziel (normiert): {t2g:.2f}.")

    # Thread-Hinweis
    if _HAS_THREAD:
        try:
            th = roter_faden.current()  # type: ignore
            if th:
                parts.append(f"Aktueller Faden: {th.get('title','?')} (Schritt {int(th.get('idx',0))+1}).")
        except Exception as e:
            log_suppressed(_LOG, key="mangel_speak_hook.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    if not any(p for p in parts if p):
        parts.append("Ich erkenne einen Mangel.")

    parts.append(random.choice([
        "Ich starte Wiederholungen.",
        "Ich wiederhole anspruchsvolle Aufgaben.",
        "Ich passe mein Üben an."
    ]))
    return " ".join([p for p in parts if p])

# ---- Hook --------------------------------------------------------------------
def mangel_speak_hook(dt: float, tick: int) -> None:
    """AgentLoop-Hook: prüft regelmäßig auf Mängel und spricht bei Bedarf (mit Empathie-Policy)."""
    global _last_speak
    if tick % 60 != 0:
        return
    now = time.time()
    if now - _last_speak < _INTERVAL:
        return

    try:
        summary = diagnostics.quick_summary(window_sec=600)  # letzte 10 Minuten
    except Exception as e:
        print("[MangelSpeak] diagnostics.quick_summary Fehler:", e)
        return

    gaps = dict(summary.get("summary", {}))

    # Empathie + Drop-Info einmalig berechnen (wird für Trigger und Curiosity genutzt)
    emp_trigger, score_last, delta_neg = _empathy_trigger()

    # Curiosity-Integration (best effort): loggt ein normalisiertes Motivationssignal,
    # auch wenn am Ende nicht gesprochen wird. Der Hook wird ohnehin periodisch ausgeführt.
    _maybe_log_curiosity_from_gaps(gaps, emp_last=score_last, emp_drop=delta_neg)

    trigger = False
    if gaps.get("confidence", 1.0) < 0.6:
        trigger = True
    if gaps.get("coverage", 1.0) < 0.7:
        trigger = True
    if gaps.get("novelty", 0.0) > 0.8:
        trigger = True
    if gaps.get("time_to_goal_norm", 0.0) > 0.8:
        trigger = True

    trigger = trigger or emp_trigger

    if not trigger:
        return

    msg = _build_message(gaps, score_last, delta_neg)
    _say(msg)
    print("[MangelSpeak] Gesagt:", msg)
    _last_speak = now

    # Reward – mit Thread-Attach via reward.log()
    if _SPEECH_REWARD > 0.0:
        try:
            reward.log("speech", value=float(_SPEECH_REWARD), info={"text": msg}, tag="self-report")
        except Exception as e:
            log_suppressed(_LOG, key="mangel_speak_hook.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    # Wiederholung (Delay abhängig von Stimmung)
    try:
        rep = curriculum.pop_repeat()
        if rep:
            try:
                delay = 30 if (score_last is not None and score_last < 0.5) else 60
                curriculum.queue_repeat(rep, delay=delay)
            except TypeError:
                curriculum.queue_repeat(rep)
            print("[MangelSpeak] Wiederholungsaufgabe re-queued:", rep)
    except Exception as e:
        log_suppressed(_LOG, key="mangel_speak_hook.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

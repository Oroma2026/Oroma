#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/agent_loop.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     AgentLoop – Haupt-Tick-Schleife + Hook-Pipeline + Event-Bus
# Version:   v3.7.3+nmr-lite-observability-v1
# Stand:     2026-05-25
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / ROLLE IM SYSTEM
# ──────────────────────
# AgentLoop ist der **zeitgetaktete Runtime-Kern** von ORÓMA:
#   - erzeugt einen regelmäßigen Tick (dt) und hält das System „lebendig“
#   - ruft registrierte Hooks deterministisch nacheinander auf
#   - bietet einen Event-Bus für Producer/Listener (z. B. Replay-Events)
#   - schreibt optional Telemetrie/Heartbeat in die DB (für Diagnose & UI)
#
# WARUM EINE EIGENE LOOP?
# ──────────────────────
# ORÓMA besteht aus vielen optionalen Subsystemen (Vision, Audio, Curriculum,
# Empathy, Coverage, Crossmodal-Linker, Replay, …). AgentLoop sorgt dafür, dass:
#   - Ausfälle einzelner Module NICHT den Prozess stoppen (best effort)
#   - man im Status sofort erkennt „wo hängt es“ (Breadcrumbs: in_hook/last_hook_ms)
#   - Hooks zentral aktiviert/deaktiviert werden können (ENV-Schalter)
#
# HOOK-PIPELINE (DT/TICK)
# ──────────────────────
# Hooks haben typischerweise Signatur:
#   hook(dt: float, tick: int) -> None
#
# Eigenschaften:
#   - Reihenfolge ist stabil (Registrierung + interne Defaults)
#   - jeder Hook ist einzeln try/except-geschützt (kein Kaskaden-Crash)
#   - Status enthält „in_hook“ + Timing, um Hänger zu debuggen
#
# OPTIONAL REGISTRIERTE HOOKS (best effort)
# ────────────────────────────────────────
# Diese Module werden nur aktiviert, wenn importierbar UND per ENV erlaubt:
#   - hooks_patch1: SelfAssessment / Transfer / Calculator
#   - curriculum_hook: Curriculum-Ticks
#   - calc_vision_linker: Crossmodal Calculator↔Vision
#   - hooks_patch2: Empathy / Coverage
#   - mangel_speak_hook: „MangelSpeak“ (Meta-Signal)
#   - self_rec_hook: Self-Listening Score
#   - hooks_av_snaptoken: Kamera SnapToken Sampling (vision/token)
#   - vision_scene_infer_hook: Vision Szene Inferenz (Tags/Labels)
#   - hooks_audio_snaptoken: Audio SnapToken Sampling (audio/token)
#   - nmr_lite: Prediction-Error/EMA/Hint-Tick für Audio↔Replay-Binding
#
# PATCH-NOTIZ 2026-05-25 — NMR-Lite Live-Fix
# ───────────────────────────────────────────
# Der NMR-Lite-Core stellt tick(now_ts: Optional[float] = None) bereit.
# Der AgentLoop darf deshalb NICHT dt/tick als zwei Positionsargumente übergeben.
# Dieser Hotfix ruft den Core mit einem expliziten now_ts-Zeitstempel auf und
# nutzt Signaturprüfung statt TypeError-Fallback, damit echte Runtime-Fehler im
# NMR-Core nicht versehentlich als Signaturproblem maskiert werden.
#
# PATCH-NOTIZ 2026-05-25 — NMR-Lite Observability / Persist-Breadcrumbs
# ───────────────────────────────────────────────────────────────────────────
# Live-Validierung zeigte: NMR-Lite war importierbar, ENV-aktiv und als Hook
# registriert; außerdem funktionierten tick()+maybe_persist() in einem direkten
# Prozess-Test. Im echten oroma.service erschienen dennoch keine neuen nmr:*
# Metrics. Damit man nicht weiter blind zwischen Hook-Runner, ENV-Gate und
# Persist-Fenster unterscheiden muss, führt dieser Patch einen sichtbaren
# Diagnosezustand unter status()["nmr_lite"] ein. Dort stehen u. a. Aufrufzähler,
# erfolgreiche Ticks, Persist-True/False-Zähler, letzter Persist-Zeitpunkt, letzte
# Output-Werte und der letzte konkrete Fehler. Zusätzlich werden erster Tick und
# erste Persistenz sichtbar geloggt. Das ändert keine NMR-Fachlogik, sondern macht
# nur den Live-Pfad prüfbar.
#
# EVENT-BUS (inject_event)
# ───────────────────────
# Zusätzlich zum Tick hat AgentLoop einen kleinen Event-Bus:
#   - Producer: z. B. ReplayManager ruft inject_event(ev_dict) auf
#   - Listener: register_event_listener(fn) erhält Events as-is
#
# Typische Events:
#   - replay_start / replay_step / replay_end (für Logging/Archivierung)
#   - UI/Tools können Debug-Events einspeisen
#
# OPTION: EVENT-TRACE (leichtgewichtige DB-Spur)
# ─────────────────────────────────────────────
# Ein Default-Listener kann Events:
#   - mit roter_faden.attach(...) anreichern (Thread-Kontext)
#   - als SnapChain-Trace persistieren (origin: event/replay o.ä.)
# Sinn: später nachvollziehen, wann/warum Replays gelaufen sind – auch ohne Logs.
#
# TELEMETRIE / HEARTBEAT
# ──────────────────────
# AgentLoop kann regelmäßig eine Metric schreiben (agent_heartbeat).
# Wichtig: DB-Locks dürfen den Tick nicht einfrieren → optional async write.
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
# Kern:
#   OROMA_AGENT_ENABLED=1|0
#   OROMA_AGENT_DT=0.25
#   OROMA_AGENT_LOGLEVEL=INFO|DEBUG|...
#
# Heartbeat:
#   OROMA_AGENT_HEARTBEAT=1|0
#   OROMA_AGENT_HEARTBEAT_ASYNC=1|0
#
# Optionale Subsysteme:
#   OROMA_AUDIO_SNAPS=1|0
#   OROMA_AV_SNAPS=1|0
#   OROMA_VISION_INFER=1|0
#   OROMA_ENABLE_EMPATHY=true|false
#   OROMA_ENABLE_COVERAGE=true|false
#
# Event Trace:
#   OROMA_EVENT_TRACE=1|0
#   OROMA_EVENT_TRACE_ORIGIN=event/replay
#   OROMA_EVENT_TRACE_WEIGHT=0.1
#
# Replay Logger (Fallback):
#   OROMA_REPLAY_LOGGER=1|0
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# start() / stop()
# status() -> dict  (running, dt, tick, last_heartbeat, in_hook, last_hook_ms, …)
# register_hook(fn) / unregister_hook(fn)
# register_event_listener(fn) / unregister_event_listener(fn)
# inject_event(ev: Any)
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import threading
import time
import logging
import inspect
from core.log_guard import log_suppressed
import json
from typing import Optional, Dict, Any, Callable, List

from core import log_guard
logger = logging.getLogger(__name__)
# ----------------------------- Logging ---------------------------------------

LOG = logging.getLogger("oroma.agent_loop")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOG.addHandler(_h)

_level = os.getenv("OROMA_AGENT_LOGLEVEL", "INFO").upper()
LOG.setLevel(getattr(logging, _level, logging.INFO))

# ----------------------------- Optionale Module ------------------------------

# SQL / Metrics
try:
    from core import sql_manager
    _HAS_SQL = True
except Exception:
    sql_manager = None  # type: ignore
    _HAS_SQL = False

# Patch 1 (SelfAssessment / TransferEngine / Calculator)
try:
    from core import hooks_patch1
    _HAS_PATCH1 = True
except Exception:
    _HAS_PATCH1 = False

# Curriculum
try:
    from core import curriculum_hook
    _HAS_CURRICULUM = True
except Exception:
    _HAS_CURRICULUM = False

# Crossmodal Linker (Calculator ↔ Vision)
try:
    from core import calc_vision_linker
    _HAS_CROSSMODAL_LINKER = True
except Exception:
    _HAS_CROSSMODAL_LINKER = False

# Patch 2 (Empathy / Coverage)
try:
    from core import hooks_patch2
    _HAS_PATCH2 = True
except Exception:
    _HAS_PATCH2 = False

# Kern-Hooks (MangelSpeak / Self-Listening)
try:
    from core import mangel_speak_hook
    _HAS_MANGELSPEAK = True
except Exception:
    _HAS_MANGELSPEAK = False

try:
    from core import self_rec_hook
    _HAS_SELFREC = True
except Exception:
    _HAS_SELFREC = False

# Roter Faden & Reward (leichte v3.7 Hooks)
try:
    from core import roter_faden
    _HAS_THREAD = True
except Exception:
    _HAS_THREAD = False

try:
    from core import reward as _reward_mod
    _HAS_REWARD = True
except Exception:
    _reward_mod = None  # type: ignore
    _HAS_REWARD = False

# Kamera: Sampling + Inference (optional)
try:
    from core.hooks_av_snaptoken import av_snaptoken_hook
    _HAS_AV_SNAPS = True
except Exception:
    _HAS_AV_SNAPS = False

try:
    from core.vision_scene_infer_hook import vision_scene_infer_hook
    _HAS_VISION_INFER = True
except Exception:
    _HAS_VISION_INFER = False

# Audio: SnapToken (optional; Factory liefert je nach Version Callable ODER Objekt)
try:
    from core.hooks_audio_snaptoken import make_audio_snaptoken_hook
    _HAS_AUDIO_SNAPS = True
    # Lazy init: make_audio_snaptoken_hook() kann je nach Stand
    #   • ein Callable(dt,tick) zurückgeben (aktueller Standard)
    #   • oder ein Objekt mit .tick(...) / .tick() (Legacy)
    _AUDIO_SNAPTOKEN = None
    _AUDIO_SNAPTOKEN_LAST_WARN_TICK = -10**9  # rate-limit warnings
except Exception:
    _HAS_AUDIO_SNAPS = False
    _AUDIO_SNAPTOKEN = None
    _AUDIO_SNAPTOKEN_LAST_WARN_TICK = -10**9

# NMR-Lite (optional; erster echter AgentLoop-Tick-Anschluss)
try:
    from core.nmr_lite import (
        tick as _nmr_lite_tick,
        maybe_persist as _nmr_lite_maybe_persist,
        debug_state as _nmr_lite_debug_state,
    )
    _HAS_NMR_LITE = True
except Exception:
    _nmr_lite_tick = None  # type: ignore
    _nmr_lite_maybe_persist = None  # type: ignore
    _nmr_lite_debug_state = None  # type: ignore
    _HAS_NMR_LITE = False

# ----------------------------- Interner Zustand ------------------------------

_state_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_ev = threading.Event()

_status: Dict[str, Any] = {
    "running": False,
    "tick": 0,
    "dt": 0.25,
    "last_heartbeat": 0,

    # Debug/Breadcrumbs (damit man Hänger sofort sieht)
    "in_hook": None,
    "in_hook_since": 0,
    "last_hook": None,
    "last_hook_ms": 0.0,

    # NMR-Lite Live-Observability:
    # Dieser Block wird ausschließlich vom AgentLoop-Hook gepflegt und ist
    # bewusst im /control/api/status sichtbar. So lässt sich unterscheiden:
    #   - Hook registriert, aber nie aufgerufen
    #   - Hook aufgerufen, aber ENV-gated übersprungen
    #   - tick() erfolgreich, aber maybe_persist() noch im Fenster/False
    #   - Persistenz erfolgreich oder mit konkretem Fehler fehlgeschlagen
    "nmr_lite": {
        "available": bool(_HAS_NMR_LITE),
        "calls_total": 0,
        "skipped_disabled": 0,
        "ticks_ok": 0,
        "ticks_failed": 0,
        "persist_true": 0,
        "persist_false": 0,
        "last_tick_ts": 0.0,
        "last_persist_attempt_ts": 0.0,
        "last_persist_ok_ts": 0.0,
        "last_error": None,
        "last_output": None,
        "debug_state": None,
    },
}

_hooks: List[Callable[[float, int], None]] = []

# === Event-Bus ===============================================================

_event_listeners: List[Callable[[Any], None]] = []

def register_event_listener(func: Callable[[Any], None]) -> None:
    """Registriert einen Listener für inject_event()."""
    if func not in _event_listeners:
        _event_listeners.append(func)
        LOG.info("Event-Listener registriert: %s", getattr(func, "__name__", str(func)))

def unregister_event_listener(func: Callable[[Any], None]) -> None:
    """Entfernt einen zuvor registrierten Event-Listener (falls vorhanden)."""
    if func in _event_listeners:
        _event_listeners.remove(func)
        LOG.info("Event-Listener entfernt: %s", getattr(func, "__name__", str(func)))

def inject_event(ev: Any) -> None:
    """
    Extern aufrufbar (z. B. vom Replay-Manager oder UI):
    Übergibt ein Event (Dict/Snap/SnapPattern) an alle registrierten Listener.
    Fehler eines Listeners blockieren den Rest nicht.
    """
    for fn in list(_event_listeners):
        try:
            fn(ev)
        except Exception as e:
            LOG.warning("Event-Listener %s Fehler: %s", getattr(fn, "__name__", "?"), e)
    # leichte Telemetrie
    if _HAS_SQL:
        try:
            sql_manager.insert_metric("agent_event_injected", 1.0)  # type: ignore[union-attr]
        except Exception as e:
            log_suppressed(LOG, key="agent_loop.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

# --- Replay-Logger (Fallback) -------------------------------------------------
_replay_log_map: Dict[str, int] = {}  # chain_id -> log_id

def _replay_logger_enabled() -> bool:
    # Default AUS, um Doppel-Logging zu vermeiden. Aktiv: OROMA_REPLAY_LOGGER=1|true|yes
    return os.getenv("OROMA_REPLAY_LOGGER", "0").strip().lower() in ("1", "true", "yes")

# ----------------------------- Helpers ---------------------------------------

def _heartbeat() -> None:
    """Optionaler Herzschlag in metrics – per ENV abschaltbar."""
    if not _HAS_SQL:
        return
    if os.getenv("OROMA_AGENT_HEARTBEAT", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        sql_manager.insert_metric("agent_heartbeat", 1.0)  # type: ignore[union-attr]
    except Exception as e:
        log_suppressed(LOG, key="agent_loop.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

# ----------------------------- Async Heartbeat (optional) --------------------

_HB_THREAD: Optional[threading.Thread] = None
_HB_LOCK = threading.Lock()

def _heartbeat_maybe_async() -> None:
    """
    Verhindert, dass DB-Locks den AgentLoop einfrieren.

    Hintergrund:
      _heartbeat() schreibt in SQLite (metrics). Bei WAL/Busy-Timeout oder
      konkurrierenden Writes kann ein Insert blockieren. Da das reine Telemetrie
      ist, darf das den Haupt-Loop NICHT stoppen.

    Default: ASYNC an (OROMA_AGENT_HEARTBEAT_ASYNC=1).
    Abschalten: OROMA_AGENT_HEARTBEAT_ASYNC=0|false|no
    """
    if os.getenv("OROMA_AGENT_HEARTBEAT_ASYNC", "1").strip().lower() in ("0", "false", "no"):
        _heartbeat()
        return

    global _HB_THREAD
    with _HB_LOCK:
        # Wenn ein Heartbeat-Write noch läuft, überspringen wir den nächsten,
        # um keine Thread-Flut zu erzeugen.
        if _HB_THREAD is not None and _HB_THREAD.is_alive():
            return

        def _run() -> None:
            try:
                _heartbeat()
            except Exception as e:
                log_suppressed(LOG, key="agent_loop.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        _HB_THREAD = threading.Thread(target=_run, daemon=True)
        _HB_THREAD.start()

def register_hook(func: Callable[[float, int], None]) -> None:
    """Registriert eine Hook-Funktion. Signatur: (dt: float, tick: int) -> None."""
    if func not in _hooks:
        _hooks.append(func)
        LOG.info("Hook registriert: %s", getattr(func, "__name__", str(func)))

def unregister_hook(func: Callable[[float, int], None]) -> None:
    """Entfernt eine zuvor registrierte Hook-Funktion (falls vorhanden)."""
    if func in _hooks:
        _hooks.remove(func)
        LOG.info("Hook entfernt: %s", getattr(func, "__name__", str(func)))

def get_registered_hooks() -> List[str]:
    """Nur zur Diagnose."""
    return [getattr(h, "__name__", str(h)) for h in _hooks]

# ----------------------------- Leichte v3.7 Hooks ----------------------------

def _nudge_thread_hook(dt: float, tick: int) -> None:
    """Alle ~5 Minuten den „roten Faden“ sanft anstupsen, falls länger Leerlauf."""
    if not _HAS_THREAD:
        return
    try:
        period_ticks = max(1, int(300.0 / max(0.001, dt)))  # ≈ 300s
        if tick % period_ticks == 0:
            roter_faden.nudge_if_idle(900)  # 15 min Idle
    except Exception as e:
        log_suppressed(LOG, key="agent_loop.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

def _social_resonance_hook(dt: float, tick: int) -> None:
    """Alle ~5 Minuten Empathie-Δ (positiv) prüfen und minimalen Reward loggen."""
    if not _HAS_REWARD:
        return
    try:
        period_ticks = max(1, int(300.0 / max(0.001, dt)))
        if tick % period_ticks == 0:
            _reward_mod.log_empathy_positive_shift(  # type: ignore[union-attr]
                window_sec=600, min_delta=0.2, reward_value=0.02
            )
    except Exception as e:
        log_suppressed(LOG, key="agent_loop.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

def _nmr_lite_status_update(**updates: Any) -> None:
    """Aktualisiert den sichtbaren NMR-Lite-Diagnoseblock thread-sicher.

    Der Status ist absichtlich rein diagnostisch: keine Fachlogik, keine DB-
    Writes, kein Einfluss auf Tick/Persistenz. Dadurch bleibt der AgentLoop auch
    dann stabil, wenn einzelne Diagnosewerte nicht serialisierbar oder leer sind.
    """
    with _state_lock:
        cur = dict(_status.get("nmr_lite") or {})
        cur.update(updates)
        _status["nmr_lite"] = cur


def _nmr_lite_status_inc(key: str, n: int = 1) -> int:
    """Erhöht einen NMR-Lite-Diagnosezähler und liefert den neuen Wert zurück."""
    with _state_lock:
        cur = dict(_status.get("nmr_lite") or {})
        val = int(cur.get(key, 0) or 0) + int(n)
        cur[key] = val
        _status["nmr_lite"] = cur
        return val


def _nmr_lite_hook(dt: float, tick: int) -> None:
    """
    Optionaler NMR-Lite-Hook (AgentLoop).

    Rolle:
      - ruft core.nmr_lite.tick(now_ts=...) im regulären AgentLoop-Takt auf
      - hält NMR-Lite bewusst im bestehenden Hook-Modell (best effort, ENV-gated)
      - ruft die gedrosselte Persistenz maybe_persist(now_ts=...) auf
      - schreibt sichtbare Diagnosewerte nach status()["nmr_lite"]
      - darf den Loop bei Fehlern NICHT destabilisieren

    Aktivierung:
      - Modul muss importierbar sein (core.nmr_lite)
      - OROMA_NMR_ENABLE=1|true|yes
      - optional zusätzlich OROMA_NMR_AGENTLOOP=1|true|yes (Default: on)

    Wichtige Design-Regel:
      NMR-Lite läuft hier nur als leichter Signal-/Priorisierungs-Pfad.
      Persistenz bleibt im NMR-Modul selbst ENV-/Window-gated. Dieser Hook
      macht nur sichtbar, ob tick() und maybe_persist() im echten Service-
      Prozess tatsächlich erreicht werden.
    """
    _nmr_lite_status_inc("calls_total")
    _nmr_lite_status_update(available=bool(_HAS_NMR_LITE))

    if not _HAS_NMR_LITE or _nmr_lite_tick is None:
        _nmr_lite_status_inc("skipped_disabled")
        _nmr_lite_status_update(last_error="NMR-Lite module unavailable or tick callable missing")
        return
    if os.getenv("OROMA_NMR_ENABLE", "0").strip().lower() not in ("1", "true", "yes"):
        _nmr_lite_status_inc("skipped_disabled")
        _nmr_lite_status_update(last_error="OROMA_NMR_ENABLE is not active")
        return
    if os.getenv("OROMA_NMR_AGENTLOOP", "1").strip().lower() in ("0", "false", "no"):
        _nmr_lite_status_inc("skipped_disabled")
        _nmr_lite_status_update(last_error="OROMA_NMR_AGENTLOOP disables hook execution")
        return

    try:
        # core.nmr_lite.tick() erwartet im aktuellen NMR-Lite-Core genau
        # ein optionales Zeitargument (now_ts). dt/tick bleiben AgentLoop-
        # lokale Taktinformationen und werden hier bewusst NICHT als zwei
        # Positionsargumente weitergereicht.
        accepts_now_ts = True
        try:
            sig = inspect.signature(_nmr_lite_tick)
            accepts_now_ts = "now_ts" in sig.parameters
        except Exception:
            # Wenn Signaturprüfung scheitert, bevorzugen wir den aktuellen
            # produktiven Pfad mit now_ts; echte Fehler werden unten sichtbar.
            accepts_now_ts = True

        now_ts = time.time()
        if accepts_now_ts:
            out = _nmr_lite_tick(now_ts=now_ts)
        else:
            out = _nmr_lite_tick()

        tick_ok_count = _nmr_lite_status_inc("ticks_ok")
        _nmr_lite_status_update(
            last_tick_ts=float(now_ts),
            last_error=None,
            last_output=out if isinstance(out, dict) else None,
        )
        if tick_ok_count == 1:
            LOG.info("NMR-Lite erster AgentLoop-Tick OK (tick=%s).", tick)

        # NMR-Lite erzeugt den Live-Output im Speicher mit tick(), schreibt
        # die Beobachtungsmetriken aber bewusst nur gedrosselt über
        # maybe_persist(). Ohne diesen Aufruf ist der Hook zwar registriert
        # und tickt technisch, aber nmr:pe / nmr:pe_ema / binding_hint /
        # crossmodal_hint erscheinen nie in der metrics-Tabelle. Die
        # Persistenz bleibt vollständig ENV-gated und window-limitiert im
        # Modul core.nmr_lite selbst (OROMA_NMR_PERSIST,
        # OROMA_NMR_PERSIST_WINDOW_SEC, OROMA_NMR_METRIC_PREFIX).
        persist_ok = False
        if _nmr_lite_maybe_persist is not None:
            _nmr_lite_status_update(last_persist_attempt_ts=float(now_ts))
            persist_ok = bool(_nmr_lite_maybe_persist(now_ts=now_ts))
            if persist_ok:
                persist_true_count = _nmr_lite_status_inc("persist_true")
                _nmr_lite_status_update(last_persist_ok_ts=float(now_ts))
                if persist_true_count == 1:
                    LOG.info("NMR-Lite erste AgentLoop-Persistenz OK (tick=%s).", tick)
            else:
                _nmr_lite_status_inc("persist_false")
        else:
            _nmr_lite_status_inc("persist_false")
            _nmr_lite_status_update(last_error="maybe_persist callable missing")

        # Debug-State ist klein genug und extrem hilfreich, um live zu sehen,
        # ob _NMR_PERSIST aktiv ist, welches Persist-Fenster gilt und ob
        # Vision/Audio im NMR-Core als degraded markiert sind. Fehler dabei
        # dürfen niemals den AgentLoop blockieren.
        try:
            if _nmr_lite_debug_state is not None:
                _nmr_lite_status_update(debug_state=_nmr_lite_debug_state())
        except Exception as e:
            _nmr_lite_status_update(last_error=f"debug_state failed: {type(e).__name__}: {e}")

    except Exception as e:
        _nmr_lite_status_inc("ticks_failed")
        _nmr_lite_status_update(last_error=f"{type(e).__name__}: {e}")
        log_suppressed(LOG, key="agent_loop.nmr.tick", msg="NMR-Lite tick failed", exc=e, level=logging.WARNING, interval_s=300)

def audio_snaptoken_hook(dt: float, tick: int) -> None:
    """
    Audio SnapToken Hook (AgentLoop).

    Kompatibilitäts-Shim für zwei Welten:

      A) make_audio_snaptoken_hook() liefert ein Callable:
         hook(dt: float, tick: int) -> None

      B) Legacy: make_audio_snaptoken_hook() liefert ein Objekt mit .tick(...)
         - tick(dt,tick) oder tick().

    Warum das wichtig ist:
      In mehreren ZIP-Ständen existieren beide Varianten. Ohne Shim kommt es zu
      Warnungen wie:
        "function" object has no attribute "tick"
      und dadurch zu massivem Log-Spam / Tick-Verlangsamung (IO-lastig).

    Aktivierung:
      OROMA_AUDIO_SNAPS=1|true|yes  (sonst registriert start() den Hook nicht)
    """
    global _AUDIO_SNAPTOKEN, _AUDIO_SNAPTOKEN_LAST_WARN_TICK
    if not _HAS_AUDIO_SNAPS:
        return

    # Lazy init
    if _AUDIO_SNAPTOKEN is None:
        try:
            _AUDIO_SNAPTOKEN = make_audio_snaptoken_hook()
        except Exception as e:
            # Rate-limit, damit Boot-Fehler nicht alles zuspammen
            if tick - _AUDIO_SNAPTOKEN_LAST_WARN_TICK >= 200:
                LOG.warning("Audio-SnapToken-Hook konnte nicht erzeugt werden: %s", e)
                _AUDIO_SNAPTOKEN_LAST_WARN_TICK = tick
            return

    hook = _AUDIO_SNAPTOKEN

    try:
        # Stil B (Legacy): Objekt mit .tick
        if hasattr(hook, "tick"):
            try:
                hook.tick(dt, tick)  # type: ignore[attr-defined]
            except TypeError:
                # ältere Implementierungen ohne Parameter
                hook.tick()  # type: ignore[attr-defined]
            return

        # Stil A (aktueller Standard): Callable(dt, tick)
        if callable(hook):
            try:
                hook(dt, tick)  # type: ignore[misc]
            except TypeError:
                # Extrem defensiv: falls jemand hook() ohne args erwartet
                hook()  # type: ignore[misc]
            return

        # Unbekannter Typ (sollte nicht passieren)
        if tick - _AUDIO_SNAPTOKEN_LAST_WARN_TICK >= 200:
            LOG.warning("Audio-SnapToken-Hook: unbekannter Hook-Typ: %r", type(hook))
            _AUDIO_SNAPTOKEN_LAST_WARN_TICK = tick

    except Exception as e:
        # Rate-limit: verhindert, dass Audio-Probleme den AgentLoop „töten“
        if tick - _AUDIO_SNAPTOKEN_LAST_WARN_TICK >= 50:
            LOG.warning("Audio-SnapToken-Hook tick fehlgeschlagen: %r", e)
            _AUDIO_SNAPTOKEN_LAST_WARN_TICK = tick
        return

# ----------------------------- Event-Trace (NEU) -----------------------------

def _persist_event_trace(enriched: Dict[str, Any]) -> None:
    """
    Persistiert einen leichten Event-Trace als SnapChain-Blob (JSON).
    Gesteuert über OROMA_EVENT_TRACE (Default: an).
    """
    if not _HAS_SQL:
        return
    if os.getenv("OROMA_EVENT_TRACE", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        origin = os.getenv("OROMA_EVENT_TRACE_ORIGIN", "event/replay")
        weight = float(os.getenv("OROMA_EVENT_TRACE_WEIGHT", "0.1"))
        blob = json.dumps(enriched, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        sql_manager.insert_metric("replay_event", 1.0)  # type: ignore
        sql_manager.insert_snapchain({                      # type: ignore
            "ts": int(time.time()),
            "quality": 0.0,
            "blob": blob,
            "exported": 0,
            "status": "active",
            "origin": origin,
            "namespace": os.getenv("OROMA_REPLAY_NS", "replay"),
            "notes": "replay_event",
            "version": "v3.8",
            "weight": weight,
        })
    except Exception as e:
        log_guard.log_suppressed(logger, key="agent_loop.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

def _default_event_listener(ev: Any) -> None:
    """
    Standard-Listener (sanft):
      • hängt Event an den „roten Faden“ (Kontext),
      • persistiert optional einen leichten Event-Trace.
    """
    try:
        enriched = {"kind": "replay_event", "payload": ev}
        if _HAS_THREAD and hasattr(roter_faden, "attach"):
            enriched = roter_faden.attach(enriched)  # Kontext anreichern
        _persist_event_trace(enriched)
    except Exception as e:
        log_guard.log_suppressed(logger, key="agent_loop.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

# ----------------------------- Replay-Logger-Listener ------------------------

def _replay_logger_listener(ev: Any) -> None:
    """
    Fallback/Duplikatschutz:
      • akzeptiert Events mit ev['kind'] ODER ev['type'] ∈ {replay_start, replay_step, replay_end}
      • legt Start-Row nur an, wenn KEINE log_id im Event steckt
      • schreibt Steps-Updates; auf 'end' Status+Info
    Aktivierung über OROMA_REPLAY_LOGGER=1 (Default: 0).
    """
    if not (_HAS_SQL and _replay_logger_enabled()):
        return
    try:
        if not isinstance(ev, dict):
            return

        etype = ev.get("kind") or ev.get("type")
        cid = ev.get("chain_id")
        if not etype or cid is None:
            return
        cid = str(cid)

        # START
        if etype == "replay_start":
            # Wenn der Replay-Manager schon eine log_id mitsendet → nur merken
            if "log_id" in ev and ev["log_id"]:
                try:
                    _replay_log_map[cid] = int(ev["log_id"])
                except Exception as e:
                    log_suppressed(LOG, key="agent_loop.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                return
            # Sonst: Startreihe anlegen
            lid = sql_manager.insert_replay_log(
                chain_id=cid,
                ts_run=int(ev.get("ts") or time.time()),
                steps=int(ev.get("total") or 0),
                speed=float(ev.get("speed") or 0.0),
                status="run"
            )
            if lid:
                _replay_log_map[cid] = int(lid)
            return

        # STEP
        if etype == "replay_step":
            lid = _replay_log_map.get(cid)
            step = ev.get("step")
            if step is None:
                step = ev.get("steps")
            if lid and step is not None:
                try:
                    sql_manager.update_replay_log(int(lid), steps=int(step))
                except Exception as e:
                    log_suppressed(LOG, key="agent_loop.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            return

        # END
        if etype == "replay_end":
            lid = _replay_log_map.pop(cid, None)
            step = ev.get("steps", ev.get("step"))
            status = str(ev.get("status") or "done")
            info = ev.get("info")
            if lid:
                # info kann dict/list/str sein
                if isinstance(info, (dict, list)):
                    info_txt = json.dumps(info, separators=(",", ":"))
                else:
                    info_txt = str(info) if info is not None else None
                try:
                    sql_manager.update_replay_log(
                        int(lid),
                        steps=(int(step) if step is not None else None),
                        status=status,
                        info=info_txt
                    )
                except Exception as e:
                    log_suppressed(LOG, key="agent_loop.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            return

    except Exception as e:
        log_suppressed(LOG, key="agent_loop.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

# ----------------------------- Hauptschleife ---------------------------------

def _loop(dt: float) -> None:
    """Innere Takt-Schleife – robust gegen Hook-Fehler."""
    global _status
    LOG.info("AgentLoop gestartet (dt=%.2fs)", dt)
    _stop_ev.clear()
    with _state_lock:
        _status.update({"running": True, "dt": float(dt)})

    try:
        while not _stop_ev.is_set():
            t0 = time.time()

            # Tick/Heartbeat früh setzen, damit Status auch bei blockierenden Hooks sichtbar bleibt
            with _state_lock:
                cur = int(_status.get("tick", 0))
                cur = cur + 1
                _status["tick"] = cur
                _status["last_heartbeat"] = int(time.time())

            # --- Breadcrumb: Wenn es hier hängt, ist (fast sicher) DB/metrics der Blocker
            # Hinweis: _heartbeat() schreibt in SQLite → kann bei Busy-Timeout blockieren.
            with _state_lock:
                _status["in_hook"] = "__heartbeat__"
                _status["in_hook_since"] = int(time.time())

            # Optionaler DB-Heartbeat (Telemetrie). Default: async, damit DB-Locks
            # den Loop nicht einfrieren lassen.
            _heartbeat_maybe_async()

            # Breadcrumb: ab hier laufen die eigentlichen Hooks
            with _state_lock:
                _status["in_hook"] = "__hooks__"
                _status["in_hook_since"] = int(time.time())

            # Hooks „best effort“ nacheinander ausführen
            for h in list(_hooks):
                hname = getattr(h, "__name__", str(h))
                with _state_lock:
                    _status["in_hook"] = hname
                    _status["in_hook_since"] = int(time.time())

                t_hook0 = time.time()
                try:
                    h(dt, cur)
                except Exception as e:
                    LOG.warning("Hook %s fehlgeschlagen: %s", hname, e)
                finally:
                    ms = (time.time() - t_hook0) * 1000.0
                    with _state_lock:
                        _status["last_hook"] = hname
                        _status["last_hook_ms"] = float(ms)
                        _status["in_hook"] = None
                        _status["in_hook_since"] = 0

            # Wenn keine Hooks registriert sind, wollen wir trotzdem wieder "idle" zeigen.
            with _state_lock:
                if _status.get("in_hook") == "__hooks__":
                    _status["in_hook"] = None
                    _status["in_hook_since"] = 0

            # Takthalten
            t_spent = time.time() - t0
            time.sleep(max(0.0, dt - t_spent))
    except Exception as e:
        LOG.error("AgentLoop abgebrochen: %s: %s", type(e).__name__, e)
    finally:
        with _state_lock:
            _status["running"] = False
        LOG.info("AgentLoop gestoppt")

# ----------------------------- Öffentliche API -------------------------------

def start(dt: float = 0.25) -> bool:
    """
    Startet den Loop, wenn nicht bereits laufend. Registriert Standard-Hooks
    defensiv (nur wenn Modul vorhanden) und optionale Hooks je nach ENV.
    """
    global _thread

    # bereits laufend?
    with _state_lock:
        if _status.get("running"):
            return True
    if _thread and _thread.is_alive():
        return True

    # --- Patch 1 (falls verfügbar)
    if _HAS_PATCH1:
        try:
            if hasattr(hooks_patch1, "self_assessment_hook"):
                register_hook(hooks_patch1.self_assessment_hook)   # type: ignore[attr-defined]
            if hasattr(hooks_patch1, "transfer_engine_hook"):
                register_hook(hooks_patch1.transfer_engine_hook)   # type: ignore[attr-defined]
            if hasattr(hooks_patch1, "calculator_hook"):
                register_hook(hooks_patch1.calculator_hook)        # type: ignore[attr-defined]
            LOG.info("Patch1-Hooks automatisch registriert.")
        except Exception as e:
            LOG.warning("Patch1-Hooks konnten nicht registriert werden: %s", e)

    # --- Kern-Hooks (MangelSpeak / SelfRec)
    try:
        if _HAS_MANGELSPEAK and hasattr(mangel_speak_hook, "mangel_speak_hook"):
            register_hook(mangel_speak_hook.mangel_speak_hook)     # type: ignore[attr-defined]
        if _HAS_SELFREC and hasattr(self_rec_hook, "self_rec_hook"):
            register_hook(self_rec_hook.self_rec_hook)             # type: ignore[attr-defined]
        LOG.info("Core-Hooks registriert (MangelSpeak=%s, SelfRec=%s).",
                 _HAS_MANGELSPEAK, _HAS_SELFREC)
    except Exception as e:
        LOG.warning("Core-Hooks konnten nicht registriert werden: %s", e)

    # --- Curriculum
    if _HAS_CURRICULUM and hasattr(curriculum_hook, "curriculum_hook"):
        try:
            register_hook(curriculum_hook.curriculum_hook)         # type: ignore[attr-defined]
            LOG.info("Curriculum-Hook automatisch registriert.")
        except Exception as e:
            LOG.warning("Curriculum-Hook konnte nicht registriert werden: %s", e)

    # --- Crossmodal-Linker (Calculator ↔ Vision) – ENV-gesteuert
    try:
        if _HAS_CROSSMODAL_LINKER and os.getenv("OROMA_CROSSMODAL_LINKS", "1").strip().lower() in ("1", "true", "yes"):
            register_hook(calc_vision_linker.calc_vision_link_hook)  # type: ignore[attr-defined]
            LOG.info("Crossmodal-Linker registriert (OROMA_CROSSMODAL_LINKS=on).")
    except Exception as e:
        LOG.warning("Crossmodal-Linker konnte nicht registriert werden: %s", e)

    # --- Patch 2 (Empathy / Coverage) – ENV-gesteuert
    if _HAS_PATCH2:
        try:
            enable_empathy  = os.getenv("OROMA_ENABLE_EMPATHY",  "1").strip().lower() in ("1", "true", "yes")
            enable_coverage = os.getenv("OROMA_ENABLE_COVERAGE", "1").strip().lower() in ("1", "true", "yes")
            if enable_empathy and hasattr(hooks_patch2, "empathy_hook"):
                register_hook(hooks_patch2.empathy_hook)           # type: ignore[attr-defined]
            if enable_coverage and hasattr(hooks_patch2, "coverage_hook"):
                register_hook(hooks_patch2.coverage_hook)          # type: ignore[attr-defined]
            LOG.info("Patch2-Hooks registriert (Empathy=%s, Coverage=%s).", enable_empathy, enable_coverage)
        except Exception as e:
            LOG.warning("Patch2-Hooks konnten nicht registriert werden: %s", e)

    # --- Leichte v3.7 Hooks (immer versuchen)
    register_hook(_nudge_thread_hook)
    register_hook(_social_resonance_hook)

    # --- NMR-Lite (optional; erster AgentLoop-Tick-Anschluss)
    try:
        if _HAS_NMR_LITE and os.getenv("OROMA_NMR_ENABLE", "0").strip().lower() in ("1", "true", "yes"):
            if os.getenv("OROMA_NMR_AGENTLOOP", "1").strip().lower() not in ("0", "false", "no"):
                register_hook(_nmr_lite_hook)
                LOG.info("NMR-Lite-Hook registriert (OROMA_NMR_ENABLE=on).")
    except Exception as e:
        LOG.warning("NMR-Lite-Hook konnte nicht registriert werden: %s", e)

    # --- Default-Event-Listener aktivieren (für Replays etc.)
    register_event_listener(_default_event_listener)

    # --- Replay-Logger-Listener (immer registrieren; Listener selbst gated per ENV)
    register_event_listener(_replay_logger_listener)

    # --- Audio: SnapToken (nur bei gesetztem Flag + vorhandenem Modul)
    try:
        if _HAS_AUDIO_SNAPS and os.getenv("OROMA_AUDIO_SNAPS", "0").strip().lower() in ("1", "true", "yes"):
            register_hook(audio_snaptoken_hook)
            LOG.info("Audio-SnapToken-Hook registriert (OROMA_AUDIO_SNAPS=on).")
    except Exception as e:
        LOG.warning("Audio-SnapToken-Hook konnte nicht registriert werden: %s", e)

    # --- Kamera: SnapToken-Sampling (nur bei gesetztem Flag + vorhandenem Modul)
    try:
        if _HAS_AV_SNAPS and os.getenv("OROMA_AV_SNAPS", "0").strip().lower() in ("1", "true", "yes"):
            register_hook(av_snaptoken_hook)                       # type: ignore[arg-type]
            LOG.info("AV-SnapToken-Hook registriert (OROMA_AV_SNAPS=on).")
    except Exception as e:
        LOG.warning("AV-SnapToken-Hook konnte nicht registriert werden: %s", e)

    # --- Kamera: Vision-Inference (nur bei gesetztem Flag + vorhandenem Modul)
    try:
        if _HAS_VISION_INFER and os.getenv("OROMA_VISION_INFER", "0").strip().lower() in ("1", "true", "yes"):
            register_hook(vision_scene_infer_hook)                 # type: ignore[arg-type]
            LOG.info("Vision-Inference-Hook registriert (OROMA_VISION_INFER=on).")
    except Exception as e:
        LOG.warning("Vision-Inference-Hook konnte nicht registriert werden: %s", e)

    # Thread starten
    dt_eff = float(os.getenv("OROMA_AGENT_DT", str(dt)))
    _thread = threading.Thread(target=_loop, args=(dt_eff,), daemon=True)
    _thread.start()
    return True

def stop() -> bool:
    """Stoppt den Loop (soft); wartet kurz auf Thread-Ende."""
    global _thread
    _stop_ev.set()
    th = _thread
    if th and th.is_alive():
        try:
            th.join(timeout=5.0)
        except Exception as e:
            log_suppressed(LOG, key="agent_loop.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    return True

def status() -> Dict[str, Any]:
    """Liefert eine Momentaufnahme des Loop-Status (thread-sicher kopiert)."""
    with _state_lock:
        return dict(_status)

# ----------------------------- Selftest --------------------------------------

if __name__ == "__main__":
    LOG.info("Starte AgentLoop Selftest …")
    ok = start(0.10)
    if not ok:
        LOG.error("Start fehlgeschlagen.")
        raise SystemExit(1)
    time.sleep(3.0)
    stop()
    LOG.info("Status: %s", status())
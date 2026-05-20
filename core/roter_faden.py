#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/roter_faden.py
# Projekt:   ORÓMA
# Modul:     „Roter Faden“ – Thread/Intent Kontext + Gap-Integration (leichtgewichtig)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul implementiert eine **dünne, persistente Thread-Schicht**,
# damit ORÓMA über mehrere Schritte hinweg einen „roten Faden“ behält.
# Es ist bewusst klein gehalten (kein Planner), aber runtime-relevant:
#   - Starten/Verfolgen eines Threads (Titel, Ziel, Schritte)
#   - Fortschritt & Status (run/pause/done)
#   - Kontext-Anreicherung für Logs/Rewards („attach“)
#   - Idle-Nudges (sanfter Anstoß) + optionales Gap-Logging (thread_idle)
#   - Zentrales Gap-Logging („note_gap“) mit Thread-Context in meta
#
# PERSISTENZ / SPEICHERORT
# ───────────────────────
# Der Zustand wird im Curriculum-State abgelegt:
#   sql_manager.fetch_curriculum_state() / update_curriculum_state()
# Feld: curriculum_state.window (JSON)
# Beispiel:
#   {
#     "thread_seq": 7,
#     "current_thread": {
#       "id": 7,
#       "title": "...",
#       "objective": "...",
#       "steps": ["...","..."],
#       "idx": 0,
#       "status": "run",
#       "started": 1767...,
#       "last": 1767...,
#       "nudges": 0,
#       "last_nudge_ts": 0,
#       "last_gap_ts": {"thread_idle": 1767...}
#     }
#   }
#
# GAP-INTEGRATION (optional, fail-safe)
# ────────────────────────────────────
# Wenn core.gaps verfügbar ist, werden Gaps zentral mitprotokolliert.
# Falls nicht, bleibt roter_faden funktionsfähig (kein Hard-Require).
# Ziel: „Was hat das System behindert?“ → später analysierbar.
#
# ENV-STEUERUNG
# ─────────────
#   OROMA_THREAD_AUTO_GAPS=1|0
#     - aktiviert Auto-Gaps bei Idle-Nudges und step_failed (Default: 1)
#   OROMA_THREAD_GAP_MIN_GAP_SEC=300
#     - Throttle pro Gap-Kategorie (Default: 300s)
#   OROMA_THREAD_NUDGE_MIN_GAP_SEC=600
#     - Mindestabstand zwischen Nudges (Default: 600s)
#
# ÖFFENTLICHE API (KERN)
# ─────────────────────
#   start_thread(title, objective="", steps=None) -> int
#   current() -> dict|None
#   advance(step_ok=True, speak=True) -> dict|None
#   pause() -> bool
#   done() -> bool
#
# Kontext-Anreicherung:
#   attach(info: dict|None) -> dict
#     - hängt { "thread": {id,title,idx,status,progress_ratio,step_label?} } an.
#
# Nudging:
#   nudge_if_idle(max_idle_sec=900) -> bool
#     - prüft „idle“ anhand current_thread.last, setzt optional Gap thread_idle.
#
# Gaps:
#   note_gap(kind, desc, confidence=0.0, meta=None) -> bool
#     - mischt Thread-Context automatisch in meta (id/title/idx/status).
#
# WARUM „ROTER FADEN“ NICHT IM DECISION_ENGINE IST
# ────────────────────────────────────────────────
# DecisionEngine entscheidet pro Schritt/Aktion. Roter Faden liefert Kontext
# über viele Schritte hinweg und ist ein „Meta-Layer“ für Stabilität,
# Diagnose (Gaps) und langfristige Zielkohärenz.
#
# =============================================================================

from __future__ import annotations
import json, time, os
from typing import Any, Dict, List, Optional
from core import sql_manager

import logging
from core import log_guard
logger = logging.getLogger(__name__)
# gaps (optional, fail-safe)
try:
    from core import gaps as _gaps
    _GAPS_OK = True
except Exception:
    _gaps = None  # type: ignore
    _GAPS_OK = False

JSON = Dict[str, Any]

# --- ENV ---------------------------------------------------------------------
_AUTO_GAPS_ENABLED = (os.environ.get("OROMA_THREAD_AUTO_GAPS", "1").strip().lower()
                      in ("1", "true", "on", "yes"))
_GAP_MIN_GAP = int(os.environ.get("OROMA_THREAD_GAP_MIN_GAP_SEC", "300"))  # 5 min
_NUDGE_MIN_GAP = int(os.environ.get("OROMA_THREAD_NUDGE_MIN_GAP_SEC", "600"))  # 10 min

def _now() -> int:
    return int(time.time())

def _loads(s: Optional[str]) -> JSON:
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}

def _dumps(o: Any) -> str:
    try:
        return json.dumps(o if o is not None else {}, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"

def _fetch_state_window() -> JSON:
    st = sql_manager.fetch_curriculum_state()
    win_raw = None
    if isinstance(st, dict):
        win_raw = st.get("window")
    elif st:
        try:
            win_raw = st["window"] if "window" in st else None
        except Exception:
            win_raw = None
    win = _loads(win_raw) if isinstance(win_raw, (str, bytes)) else (win_raw or {})
    return win if isinstance(win, dict) else {}

def _store_state_window(win: JSON) -> None:
    sql_manager.update_curriculum_state(window_json=_dumps(win), last_update=_now())

# --------- öffentliche API ----------------------------------------------------

def start_thread(title: str, objective: str = "", steps: Optional[List[str]] = None) -> int:
    steps = [s.strip() for s in (steps or []) if str(s).strip()]
    win = _fetch_state_window()
    tid = int(win.get("thread_seq", 0)) + 1
    win["thread_seq"] = tid
    now = _now()
    win["current_thread"] = {
        "id": tid,
        "title": str(title).strip(),
        "objective": str(objective or title).strip(),
        "steps": steps,
        "idx": 0,
        "status": "run",
        "started": now,
        "last": now,
        "nudges": 0,
        "last_nudge_ts": 0,     # Anti-Spam Nudges
        # Neu: Gap-Throttle pro Kategorie
        "last_gap_ts": {},      # {"thread_idle": ts, "step_failed": ts, ...}
    }
    _store_state_window(win)
    _say(f"Starte Thread: {title}. Ziel: {objective or title}.")
    return tid

def current() -> Optional[JSON]:
    win = _fetch_state_window()
    th = win.get("current_thread")
    return th if isinstance(th, dict) else None

def pause() -> bool:
    win = _fetch_state_window()
    th = win.get("current_thread")
    if not th:
        return False
    th["status"] = "pause"
    th["last"] = _now()
    win["current_thread"] = th
    _store_state_window(win)
    _say(f"Thread '{th['title']}' pausiert.")
    return True

def done() -> bool:
    win = _fetch_state_window()
    th = win.get("current_thread")
    if not th:
        return False
    th["status"] = "done"
    th["last"] = _now()
    win["current_thread"] = th
    _store_state_window(win)
    _say(f"Thread '{th['title']}' abgeschlossen.")
    return True

def advance(step_ok: bool = True, speak: bool = True) -> Optional[JSON]:
    win = _fetch_state_window()
    th = win.get("current_thread")
    if not th:
        return None

    # Wenn der aktuelle Schritt fehlgeschlagen ist → Gap
    if not step_ok and _AUTO_GAPS_ENABLED:
        _note_gap_internal("step_failed", "Thread-Schritt fehlgeschlagen", 0.2, th)

    if step_ok:
        th["idx"] = min(len(th.get("steps", [])), int(th.get("idx", 0)) + 1)
    th["last"] = _now()
    if th["idx"] >= len(th.get("steps", [])) and th.get("steps"):
        th["status"] = "done"
    win["current_thread"] = th
    _store_state_window(win)
    if speak:
        speak_status(th)
    return th

def attach(info: Optional[JSON] = None) -> JSON:
    """
    Kontext an Logs/Rewards hängen:
      {
        ...,
        "thread": { id, title, idx, status, progress_ratio, step_label? }
      }
    """
    out: JSON = dict(info or {})
    th = current()
    if th:
        steps = th.get("steps", []) or []
        idx = int(th.get("idx", 0))
        total = len(steps)
        ratio = (idx / float(total)) if total > 0 else 0.0
        entry: JSON = {
            "id": th.get("id"),
            "title": th.get("title"),
            "idx": idx,
            "status": th.get("status"),
            "progress_ratio": round(max(0.0, min(1.0, ratio)), 3),
        }
        if 0 <= idx < total:
            entry["step_label"] = steps[idx]
        out["thread"] = entry
    return out

def nudge_if_idle(max_idle_sec: int = 900) -> bool:
    """
    Sanfter Anstoß, wenn der Thread zu lange stillsteht.
    Beachtet Mindestabstand zwischen Nudges (_NUDGE_MIN_GAP).
    Löst optional ein Gap ("thread_idle") aus – throttled per OROMA_THREAD_GAP_MIN_GAP_SEC.
    """
    th = current()
    if not th or th.get("status") != "run":
        return False
    now = _now()
    idle = now - int(th.get("last", now))
    if idle < int(max_idle_sec):
        return False

    # Anti-Spam: Mindestabstand zwischen Nudges
    last_nudge_ts = int(th.get("last_nudge_ts", 0))
    if last_nudge_ts and (now - last_nudge_ts) < max(60, _NUDGE_MIN_GAP):
        return False

    th["nudges"] = int(th.get("nudges", 0)) + 1
    th["last_nudge_ts"] = now
    _update(th)

    nxt = ""
    steps = th.get("steps", []) or []
    idx = int(th.get("idx", 0))
    if 0 <= idx < len(steps):
        nxt = f" Nächster Schritt: {steps[idx]}."
    _say(f"Thread '{th['title']}': kleiner Anstoß.{nxt}")

    # Auto-Gap: thread_idle (mit Throttle)
    if _AUTO_GAPS_ENABLED:
        _note_gap_internal("thread_idle", "Thread inaktiv (nudge)", 0.3, th)
    return True

# --------- Gap-API ------------------------------------------------------------

def note_gap(kind: str, desc: str, confidence: float = 0.0, meta: Optional[JSON] = None) -> bool:
    """
    Öffentliche, threadsichere Gap-API:
      • mischt aktiven Thread-Kontext in meta
      • respektiert Throttle je Gap-Kategorie (per ENV)
      • ist fail-safe, wenn core.gaps nicht verfügbar ist
    Rückgabe: True, wenn (versucht) geschrieben wurde; False, wenn unterdrückt/fehlte.
    """
    th = current()
    return _note_gap_internal(kind, desc, confidence, th, meta)

# --------- Status/Helpers -----------------------------------------------------

def speak_status(th: Optional[JSON] = None) -> None:
    """Status laut sprechen."""
    if th is None:
        th = current()
        if not th:
            _say("Kein aktiver Thread.")
            return
    pos = int(th.get("idx", 0))
    steps = th.get("steps", []) or []
    total = len(steps)
    status = th.get("status", "run")
    if total > 0 and pos <= total - 1:
        step = steps[pos] if pos < total else ""
        _say(f"Thread {th['title']} – Schritt {pos+1} von {total}: {step} (Status: {status}).")
    else:
        _say(f"Thread {th['title']} – Status: {status}.")

# Rückwärtskompatibler privater Alias:
def _speak_status(th: JSON) -> None:
    speak_status(th)

def _update(th: JSON) -> None:
    win = _fetch_state_window()
    win["current_thread"] = th
    _store_state_window(win)

def _say(text: str) -> None:
    try:
        from ui import tts
        tts.say(text)  # bevorzugt
        return
    except Exception as e:
        log_guard.log_suppressed(logger, key="roter_faden.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    try:
        from ui import audio_ui  # Fallback
        if hasattr(audio_ui, "say"):
            audio_ui.say(text)  # type: ignore
            return
    except Exception as e:
        log_guard.log_suppressed(logger, key="roter_faden.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    print("[RoterFaden/TTS]", text)

# --------- interne Gap-Utilities ---------------------------------------------

def _thread_ctx_meta(th: Optional[JSON]) -> JSON:
    """Thread-Kontext als Meta-Dict (id, title, idx, progress, status, step_label)."""
    out: JSON = {}
    if not th:
        return out
    steps = th.get("steps", []) or []
    idx = int(th.get("idx", 0))
    total = len(steps)
    ratio = (idx / float(total)) if total > 0 else 0.0
    out.update({
        "thread": {
            "id": th.get("id"),
            "title": th.get("title"),
            "idx": idx,
            "status": th.get("status"),
            "progress_ratio": round(max(0.0, min(1.0, ratio)), 3),
            "has_steps": bool(total > 0),
        }
    })
    if 0 <= idx < total:
        out["thread"]["step_label"] = steps[idx]
    return out

def _gap_allowed(th: Optional[JSON], kind: str, now: Optional[int] = None) -> bool:
    """Throttle je Gap-Kategorie – Zeitabstände in th['last_gap_ts'][kind]."""
    if not _AUTO_GAPS_ENABLED:
        return False
    if th is None:
        return True  # ohne Thread kein Throttle pro Thread
    now = now or _now()
    lg: JSON = th.get("last_gap_ts") or {}
    last = int(lg.get(kind, 0)) if isinstance(lg, dict) else 0
    if last and (now - last) < max(30, _GAP_MIN_GAP):
        return False
    # ok → Marke setzen
    try:
        if not isinstance(lg, dict):
            lg = {}
        lg[kind] = now
        th["last_gap_ts"] = lg
        _update(th)
    except Exception as e:
        log_guard.log_suppressed(logger, key="roter_faden.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return True

def _note_gap_internal(kind: str, desc: str, confidence: float, th: Optional[JSON], meta: Optional[JSON] = None) -> bool:
    """Interner Writer: mischt Thread-Kontext, prüft Throttle und ruft core.gaps."""
    try:
        if not _GAPS_OK:
            return False
        if not _gap_allowed(th, kind):
            return False
        m: JSON = {}
        # Thread-Kontext
        try:
            m.update(_thread_ctx_meta(th))
        except Exception as e:
            log_guard.log_suppressed(logger, key="roter_faden.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        # user-meta
        if isinstance(meta, dict):
            m.update(meta)
        _gaps.add_gap(str(kind or ""), str(desc or ""), float(confidence or 0.0), m)  # type: ignore
        return True
    except Exception:
        return False
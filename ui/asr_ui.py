#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/asr_ui.py
# Projekt: ORÓMA
# Modul:   ASR UI (Live-Transkription, DeviceHub-first)
# Version: v3.8-r4 (prod-fix: AudioWrapper API korrekt)
# Stand:   2026-01-02
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   UI + API für Live-ASR (Whisper) – robust im Headless-Betrieb und
#   konsequent über den zentralen DeviceHub geroutet.
#
#   Routen:
#     • GET  /asr              → HTML-Seite (ui/templates/asr.html)
#     • POST /asr/api/start    → startet den ASR-Worker
#     • POST /asr/api/stop     → stoppt den ASR-Worker
#     • GET  /asr/api/status   → aktueller Zustand + letzter Text
#
# WARUM DIESER PATCH?
# ───────────────────
#   In deinem Stand war /asr zwar als "v3.7" dokumentiert, verwendete aber
#   eine AudioWrapper-API, die in v3.8 anders ist:
#     • AudioWrapper(model_name=...) existiert nicht
#     • transcribe(...) bekam fälschlich ein Spektrum statt Audio/WAV
#
#   Ergebnis: /asr konnte instabil sein oder gar nicht arbeiten.
#
#   Dieses Modul nutzt nun eine produktive, einfache Pipeline:
#     1) AudioWrapper startet Mic (DeviceHub.start_mic über hub-first)
#     2) Worker nimmt alle CHUNK_SEC Sekunden WAV auf (record_wav)
#     3) Whisper transkribiert WAV (transcribe)
#     4) Optional: ASR-Reflex + Empathie-Logging
#
#   Damit gilt für Audio-Geräte durchgängig:
#     • Audio UI (/audio) → DeviceHub
#     • ASR (/asr)        → AudioWrapper → DeviceHub
#     • ASR2 (/asr2)      → OromaWrapper → wrappers.audio_wrapper.asr_stream → DeviceHub
#
# GERÄTEAUSWAHL (WICHTIG)
# ───────────────────────
#   Die Wahl des USB-Audio-Geräts erfolgt zentral über ENV (Substring-Match,
#   case-insensitive) – identisch für Audio/ASR/ASR2:
#     OROMA_AUDIO_INPUT_NAME=Jabra ...
#     OROMA_AUDIO_OUTPUT_NAME=Jabra ...
#
# ENV
# ───
#   OROMA_ASR_MODEL=tiny|base|small|medium|...       (Default: small)
#     → wird an AudioWrapper(whisper_model=...) übergeben.
#   OROMA_WHISPER_LANG=de|en|...                     (Default: de)
#   OROMA_ASR_CHUNK_SEC=0.8..8.0                     (Default: 2.0)
#   OROMA_ASR_LOOP_SLEEP_SEC=0.05..0.5               (Default: 0.10)
#
#   Teacher-Links (optional):
#     OROMA_ASR_AVLINK=0|1                           (Default: 0)  → origin 'link/av_label'
#     OROMA_ASR_ALINK=0|1                            (Default: 1)  → origin 'link/a_label' (Audio-only)
#
#   Reflex / Empathie (wie vorher):
#     OROMA_ASR_REFLEX_ENABLED=true|false            (Default: true)
#     OROMA_ASR_MIN_DELTA_MS=250
#     OROMA_ASR_EMPATHY_LOG_ENABLED=true|false       (Default: true)
#     OROMA_ASR_EMPATHY_MIN_GAP_SEC=15
#
# SICHERHEIT / STABILITÄT
# ───────────────────────
#   • Kein Qt/Wayland/X11.
#   • Keine Busy-Waits: Chunk-basiert, kurze Sleep-Drossel.
#   • Fehler werden in status.error abgelegt (UI zeigt an).
#   • Stop ist idempotent.
# =============================================================================

from __future__ import annotations

import os
import re
import time
import logging
import threading
from flask import Blueprint, render_template, jsonify
from core.log_guard import log_suppressed

try:
    from wrappers.audio_wrapper import AudioWrapper
except Exception as e:
    AudioWrapper = None  # type: ignore[assignment]
    print(f"[asr_ui] WARN: AudioWrapper fehlt: {e}")

# Optionale Module (Reflex / SQL)
_try_reflex = None
try:
    from core import asr_reflex  # bietet process_text(text: str)

    def _call_reflex(txt: str) -> None:
        try:
            asr_reflex.process_text(txt)
        except Exception as _e:
            print(f"[asr_ui] ASR-Reflex Fehler: {_e}")

    _try_reflex = _call_reflex
except Exception as e:
    print(f"[asr_ui] Hinweis: core.asr_reflex nicht gefunden ({e}) – Reflex deaktiviert.")

_sql = None
try:
    from core import sql_manager as _sql  # insert_empathy_snap
except Exception as e:
    print(f"[asr_ui] Hinweis: sql_manager nicht verfügbar ({e}) – Empathie-Logging deaktiviert.")

bp = Blueprint("asr", __name__, url_prefix="/asr")

# ---------------- ENV/Config ----------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except Exception:
        return float(default)


_ASR_MODEL = os.environ.get("OROMA_ASR_MODEL", "small").strip()
_WHISPER_LANG = os.environ.get("OROMA_WHISPER_LANG", "de").strip() or "de"
_CHUNK_SEC = max(0.8, min(8.0, _env_float("OROMA_ASR_CHUNK_SEC", 2.0)))
_LOOP_SLEEP = max(0.05, min(0.5, _env_float("OROMA_ASR_LOOP_SLEEP_SEC", 0.10)))
_AVLINK = str(os.environ.get("OROMA_ASR_AVLINK", "0")).strip().lower() in ("1","true","yes","y","on")
_ALINK = str(os.environ.get("OROMA_ASR_ALINK", "1")).strip().lower() in ("1","true","yes","y","on")

_REFLEX_ENABLED = (os.environ.get("OROMA_ASR_REFLEX_ENABLED", "true").lower() not in ("0", "false", "no", "off"))
_MIN_DELTA_MS = int(os.environ.get("OROMA_ASR_MIN_DELTA_MS", "250"))

_EMPATHY_ENABLED = (os.environ.get("OROMA_ASR_EMPATHY_LOG_ENABLED", "true").lower() not in ("0", "false", "no", "off"))
_EMPATHY_GAP = int(os.environ.get("OROMA_ASR_EMPATHY_MIN_GAP_SEC", "15"))

# ---------------- State ----------------
_lock = threading.Lock()
_state = {
    "running": False,
    "text": "",
    "model": _ASR_MODEL,
    "lang": _WHISPER_LANG,
    "chunk_sec": _CHUNK_SEC,
    "error": "",
}
_thread: threading.Thread | None = None
_aw: AudioWrapper | None = None  # type: ignore[valid-type]

logger = logging.getLogger("oroma.asr_ui")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())

# Entprellung/Reflex-Steuerung
_last_reflex_txt = ""
_last_reflex_ts = 0.0

# Empathie-Rate-Limit
_last_empathy_ts = 0.0

# ---------------- Empathie-Helfer -------------------------------------------

_POS_PAT = re.compile(r"\b(gut|toll|super|danke|ok(ay)?|schön|prima|love|happy|great|nice|passt|ja)\b", re.I)
_NEG_PAT = re.compile(r"\b(schlecht|hilfe|fehler|kaputt|frust|wütend|ärger|traurig|angst|mies|falsch|nein|problem)\b", re.I)


def _sentiment_score(text: str) -> float:
    if not text:
        return 0.5
    score = 0.5
    pos_hits = len(_POS_PAT.findall(text))
    neg_hits = len(_NEG_PAT.findall(text))
    score += 0.2 * pos_hits
    score -= 0.2 * neg_hits
    return max(0.0, min(1.0, score))


def _mood_from_score(score: float) -> str:
    if score >= 0.65:
        return "happy"
    if score <= 0.35:
        return "frustrated"
    return "neutral"


def _maybe_log_empathy(text: str) -> None:
    global _last_empathy_ts
    if not _EMPATHY_ENABLED or _sql is None or not text:
        return
    now = time.time()
    if now - _last_empathy_ts < _EMPATHY_GAP:
        return
    try:
        sc = _sentiment_score(text)
        mood = _mood_from_score(sc)
        ts = int(now)
        _sql.insert_empathy_snap(ts, mood, sc)  # type: ignore[attr-defined]
        _last_empathy_ts = now
    except Exception as e:
        logger.debug(f"[ASR Empathy] Logging übersprungen: {e}")


# ---------------- Reflex -----------------------------------------------------

def _maybe_reflex(txt: str) -> None:
    global _last_reflex_txt, _last_reflex_ts
    if not _REFLEX_ENABLED or _try_reflex is None:
        return
    if not txt:
        return

    now = time.time()

    # Dedupe/Entprellung: identischer Text zu schnell hintereinander → skip
    if txt == _last_reflex_txt and (now - _last_reflex_ts) * 1000.0 < _MIN_DELTA_MS:
        return

    _last_reflex_txt = txt
    _last_reflex_ts = now

    # Optional: Crossmodal Teacher-Link (ASR Live) – default OFF
    if _AVLINK:
        try:
            from core import av_label_linker  # lazy import
            av_label_linker.link_text_now(txt)
        except Exception as e:
            logger.debug(f"[ASR AVLink] übersprungen: {e}")

    # Optional: Unimodaler Teacher-Link (ASR Live → AudioTokens) – default ON
    # Entspricht dem menschlichen Fall: „Augen zu“ – Sprache bleibt als Label-Spur.
    if _ALINK:
        try:
            from core import audio_label_linker  # lazy import
            audio_label_linker.link_text_now(txt)
        except Exception as e:
            logger.debug(f"[ASR ALink] übersprungen: {e}")

    # Reflex (Empathie/Intents/Rewards) – bestehender Lernpfad
    _try_reflex(txt)
def _worker() -> None:
    global _aw
    if not AudioWrapper:
        with _lock:
            _state["running"] = False
            _state["error"] = "AudioWrapper nicht verfügbar"
        return

    try:
        _aw = AudioWrapper(
            samplerate=int(os.environ.get("OROMA_ASR_SR", os.environ.get("OROMA_AUDIO_SR", "16000"))),
            channels=int(os.environ.get("OROMA_AUDIO_CH", "1")),
            use_hub=True,
            enable_whisper=True,
            whisper_model=_ASR_MODEL,
            whisper_lang=_WHISPER_LANG,
            client="asr_ui",
        )
        _aw.start()
    except Exception as e:
        with _lock:
            _state["running"] = False
            _state["error"] = f"Startfehler: {e}"
        return

    last_text = ""
    try:
        while True:
            with _lock:
                if not _state["running"]:
                    break

            wav = b""
            try:
                wav = _aw.record_wav(_CHUNK_SEC) if _aw else b""
            except Exception as e:
                with _lock:
                    _state["error"] = f"record_wav Fehler: {e}"
                time.sleep(0.25)
                continue

            txt = ""
            try:
                txt = (_aw.transcribe(wav) if (_aw and wav) else "") or ""
            except Exception as e:
                with _lock:
                    _state["error"] = f"transcribe Fehler: {e}"
                time.sleep(0.25)
                continue

            txt = txt.strip()
            if txt and txt != last_text:
                last_text = txt
                with _lock:
                    _state["text"] = txt
                    _state["error"] = ""
                _maybe_reflex(txt)
                _maybe_log_empathy(txt)

            time.sleep(_LOOP_SLEEP)

    finally:
        try:
            if _aw:
                _aw.stop()
        except Exception as e:
            log_suppressed('ui/asr_ui.py:281', exc=e, level=logging.WARNING)
            pass
        _aw = None
        with _lock:
            _state["running"] = False


# ---------------- Routes -----------------------------------------------------

@bp.route("/")
def page():
    return render_template("asr.html")


@bp.route("/api/start", methods=["POST"])
def api_start():
    global _thread
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "error": "ASR läuft bereits", "status": dict(_state)})
        _state["running"] = True
        _state["error"] = ""
        _state["text"] = ""
        _state["model"] = _ASR_MODEL
        _state["lang"] = _WHISPER_LANG
        _state["chunk_sec"] = _CHUNK_SEC

    _thread = threading.Thread(target=_worker, name="oroma_asr_worker", daemon=True)
    _thread.start()
    return jsonify({"ok": True, "msg": f"ASR gestartet (model={_ASR_MODEL}, lang={_WHISPER_LANG}, chunk={_CHUNK_SEC:.1f}s)"})


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    global _thread
    with _lock:
        _state["running"] = False

    if _thread and _thread.is_alive():
        try:
            _thread.join(timeout=2.0)
        except Exception as e:
            log_suppressed('ui/asr_ui.py:323', exc=e, level=logging.WARNING)
            pass
    _thread = None
    return jsonify({"ok": True, "msg": "ASR gestoppt", "status": dict(_state)})


@bp.route("/api/status")
def api_status():
    with _lock:
        return jsonify({"ok": True, "status": dict(_state)})

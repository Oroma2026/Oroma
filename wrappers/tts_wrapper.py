#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/wrappers/tts_wrapper.py
# Projekt: ORÓMA
# Modul:   TTS-Wrapper (Fallback & DeviceHub-Integration)
# Version: v3.9 (prod, headless-safe playback)
# Stand:   2026-07-07
#
# Zweck
# ─────
#   Robuster Offline-TTS-Wrapper mit Fallback-Kette und optionaler Ausgabe
#   über den zentralen DeviceHub (für einheitliches Logging/Auditing).
#
# Features
# ────────
#   • Engines (automatische Auswahl oder per ENV):
#       1) pyttsx3      → offline, plattformübergreifend (Pi nutzt eSpeak NG)
#       2) espeak-ng    → direkter CLI-Aufruf
#       3) pico2wave    → CLI-Fallback (optional aplay für Direktwiedergabe)
#   • Blocking & Non-Blocking (Queue + Worker-Thread)
#   • Voice/Rate/Volume (normalisiert), Text-Chunks (Satz/Max-Länge)
#   • save_to_file(text, wav_path): WAV-Datei aus Chunks (ohne Resampling)
#   • Optionale Ausgabe über DeviceHub.play_wav(...) (Audit, zentrales Logging)
#
# ENV-Variablen
# ─────────────
#   OROMA_TTS_BACKEND=auto|pyttsx3|espeak|pico
#   OROMA_TTS_VOICE=...            (z. B. "de", "de-DE", "en-US", Name/ID bei pyttsx3)
#   OROMA_TTS_RATE=180             (Wörter/Minute; clamp 80..400)
#   OROMA_TTS_VOLUME=1.0           (0..1)
#   OROMA_TTS_MAX_CHARS=280        (Chunkgröße)
#   OROMA_TTS_USE_DEVICE_HUB=true|false  (Default: true)
#   OROMA_TTS_DIRECT_PLAYBACK_ENABLE=true|false
#       Default false. Wenn DeviceHub kein valides Output-Device findet, wird
#       NICHT mehr automatisch auf pyttsx3/espeak-Direktwiedergabe zurückgefallen.
#       Das verhindert PortAudio/ALSA-Spam in headless/systemd-Setups.
#   OROMA_TTS_HUB_OUTPUT_PRECHECK=true|false
#       Default true. Prüft DeviceHub-Output vor WAV-Synthese/Playback.
#   OROMA_TTS_NO_OUTPUT_CACHE_SEC=900
#       Cache-Zeit für erkannte Headless-/No-Output-Situation.
#   OROMA_TTS_SKIP_LOG_INTERVAL_SEC=900
#       Drosselung für Headless-Skip-Logs.
#   OROMA_LOG_LEVEL=INFO|DEBUG|...
#
# Kompatibilität
# ──────────────
#   • Kompatibel mit deinem aktuellen DeviceHub (play_wav(wav_bytes) ohne client-Arg).
#     Falls du future-mäßig einen client-Parameter hinzufügst, erkennt der Wrapper
#     das automatisch und nutzt ihn (Try/Except auf Typfehler).
#
# Lizenz: MIT (ORÓMA)
# =============================================================================

from __future__ import annotations

import os
import re
import io
import wave
import json
import shutil
import queue
import atexit
import logging
LOG = logging.getLogger("wrappers_tts_wrapper")
from core.log_guard import log_suppressed
import tempfile
import threading
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
log = logging.getLogger("oroma.tts")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_h)
log.setLevel(os.environ.get("OROMA_LOG_LEVEL", "INFO"))

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except Exception as e:
        log_suppressed(LOG, key="wrappers_tts_wrapper.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except Exception as e:
        log_suppressed(LOG, key="wrappers_tts_wrapper.ret.2", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _env_bool(key: str, default: bool) -> bool:
    v = str(os.environ.get(key, "")).strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")

# TTS-Verfügbarkeit im Dauerbetrieb cachen/rate-limitieren:
# - Wenn auf dem System gerade keine Engine verfügbar ist, soll nicht jeder
#   speak()-Versuch erneut pyttsx3/espeak/pico durchprobieren und mehrere
#   INFO/ERROR-Zeilen erzeugen.
# - Stattdessen merken wir uns den letzten Initialisierungsfehler für eine
#   begrenzte Retry-Zeit und loggen die Situation nur rate-limitiert.
_TTS_RETRY_SEC = max(60, _env_int("OROMA_TTS_RETRY_SEC", 900))
_TTS_MISSING_LOG_INTERVAL_SEC = max(60, _env_int("OROMA_TTS_MISSING_LOG_INTERVAL_SEC", 900))
_TTS_DISABLE_ON_MISSING = _env_bool("OROMA_TTS_DISABLE_ON_MISSING", True)
_TTS_INIT_FAILURE: Optional[str] = None
_TTS_INIT_FAILURE_TS: float = 0.0
_TTS_INIT_FAILURE_LOG_TS: float = 0.0

def _remember_tts_failure(msg: str) -> None:
    global _TTS_INIT_FAILURE, _TTS_INIT_FAILURE_TS
    _TTS_INIT_FAILURE = str(msg)
    _TTS_INIT_FAILURE_TS = float(__import__("time").time())

def _clear_tts_failure() -> None:
    global _TTS_INIT_FAILURE, _TTS_INIT_FAILURE_TS, _TTS_INIT_FAILURE_LOG_TS
    _TTS_INIT_FAILURE = None
    _TTS_INIT_FAILURE_TS = 0.0
    _TTS_INIT_FAILURE_LOG_TS = 0.0

def _log_tts_missing_ratelimited(msg: str) -> None:
    global _TTS_INIT_FAILURE_LOG_TS
    now = float(__import__("time").time())
    if (now - _TTS_INIT_FAILURE_LOG_TS) >= float(_TTS_MISSING_LOG_INTERVAL_SEC):
        _TTS_INIT_FAILURE_LOG_TS = now
        log.warning("TTS momentan deaktiviert/fehlend: %s (Retry in %ss)", msg, _TTS_RETRY_SEC)

def _tts_retry_allowed() -> bool:
    if not _TTS_DISABLE_ON_MISSING:
        return True
    if not _TTS_INIT_FAILURE:
        return True
    return (float(__import__("time").time()) - float(_TTS_INIT_FAILURE_TS)) >= float(_TTS_RETRY_SEC)


def _norm_rate(val: int) -> int:
    return int(max(80, min(400, val)))

def _norm_volume(val: float) -> float:
    return float(max(0.0, min(1.0, val)))

def _norm_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r"\s+", " ", text.strip())
    # ASCII + gängige deutsche Sonderzeichen und Satzzeichen
    return "".join(
        ch for ch in text
        if (31 < ord(ch) < 127) or ch in " äöüÄÖÜß€.,;:!?-()[]{}\"'/_\n"
    )

def _split_sentences(text: str) -> List[str]:
    text = _norm_text(text)
    parts = re.split(r"(?<=[\.\!\?;:])\s+(?=[A-ZÄÖÜ]|$)", text)
    return [p.strip() for p in parts if p.strip()]

def _chunk_text(text: str, max_chars: int = 280) -> List[str]:
    sents = _split_sentences(text)
    if not sents:
        return []
    chunks: List[str] = []
    buf = ""
    for s in sents:
        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_chars:
            buf += " " + s
        else:
            chunks.append(buf)
            buf = s
    if buf:
        chunks.append(buf)
    out: List[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            out.append(c)
        else:
            for i in range(0, len(c), max_chars):
                out.append(c[i:i+max_chars])
    return out

# -----------------------------------------------------------------------------
# DeviceHub (optional)
# -----------------------------------------------------------------------------
_USE_HUB_DEFAULT = True
_USE_HUB = _env_bool("OROMA_TTS_USE_DEVICE_HUB", _USE_HUB_DEFAULT)

# -----------------------------------------------------------------------------
# Headless-safe Playback Guard
# -----------------------------------------------------------------------------
# ORÓMA läuft produktiv häufig ohne X11/Wayland/Desktop und ohne dauerhaft
# vorhandenes Audio-Output-Device. In diesem Modus darf ein fehlgeschlagener
# DeviceHub-Playback-Versuch NICHT automatisch pyttsx3/espeak direkt starten,
# weil diese Engines intern wieder PortAudio/ALSA öffnen und dadurch hunderte
# Zeilen wie ``wave_open_sound > Pa_OpenStream : err=-9996`` in service.err.log
# erzeugen können.
#
# Design:
#   • TTS-Synthese in Dateien bleibt unverändert möglich (save_to_file).
#   • DeviceHub bleibt der bevorzugte Playback-Pfad, weil er zentral auditierbar
#     ist und Audio-Devices sauber auswählt.
#   • Wenn DeviceHub kein Output-Device hat, wird Sprache als kontrollierter
#     ``skipped``-Status zurückgegeben, nicht als Fehler-Spam.
#   • Direkte Engine-Wiedergabe ist weiterhin bewusst zuschaltbar über
#     OROMA_TTS_DIRECT_PLAYBACK_ENABLE=1, z. B. für Desktop-/Werkstattbetrieb.
_TTS_DIRECT_PLAYBACK_ENABLE = _env_bool("OROMA_TTS_DIRECT_PLAYBACK_ENABLE", False)
_TTS_HUB_OUTPUT_PRECHECK = _env_bool("OROMA_TTS_HUB_OUTPUT_PRECHECK", True)
_TTS_NO_OUTPUT_CACHE_SEC = max(30, _env_int("OROMA_TTS_NO_OUTPUT_CACHE_SEC", 900))
_TTS_SKIP_LOG_INTERVAL_SEC = max(30, _env_int("OROMA_TTS_SKIP_LOG_INTERVAL_SEC", 900))
_TTS_OUTPUT_BLOCKED_UNTIL_TS: float = 0.0
_TTS_OUTPUT_BLOCK_REASON: str = ""
_TTS_SKIP_LOG_TS: float = 0.0

def _remember_output_block(reason: str) -> None:
    global _TTS_OUTPUT_BLOCKED_UNTIL_TS, _TTS_OUTPUT_BLOCK_REASON
    _TTS_OUTPUT_BLOCK_REASON = str(reason or "no_output")
    _TTS_OUTPUT_BLOCKED_UNTIL_TS = time.time() + float(_TTS_NO_OUTPUT_CACHE_SEC)

def _clear_output_block() -> None:
    global _TTS_OUTPUT_BLOCKED_UNTIL_TS, _TTS_OUTPUT_BLOCK_REASON
    _TTS_OUTPUT_BLOCKED_UNTIL_TS = 0.0
    _TTS_OUTPUT_BLOCK_REASON = ""

def _output_block_active() -> Optional[str]:
    if time.time() < float(_TTS_OUTPUT_BLOCKED_UNTIL_TS or 0.0):
        return _TTS_OUTPUT_BLOCK_REASON or "no_output_cached"
    return None

def _log_tts_skip_ratelimited(reason: str) -> None:
    global _TTS_SKIP_LOG_TS
    now = time.time()
    if (now - float(_TTS_SKIP_LOG_TS or 0.0)) >= float(_TTS_SKIP_LOG_INTERVAL_SEC):
        _TTS_SKIP_LOG_TS = now
        log.info("TTS-Playback übersprungen: %s", reason)

def _skip_result(reason: str, backend: str = "", mode: str = "playback") -> Dict[str, Any]:
    _log_tts_skip_ratelimited(reason)
    return {
        "ok": True,
        "skipped": True,
        "reason": str(reason or "playback_skipped"),
        "backend": backend or "",
        "mode": mode,
        "headless_safe": True,
    }

def _hub_output_ready(hub) -> Tuple[bool, str]:
    """Prüft best-effort, ob DeviceHub wirklich ein Output-Device nutzen kann.

    Diese Vorprüfung verhindert, dass TTS erst WAV-Bytes synthetisiert und dann
    bei jedem Versuch in ``DeviceHub.play_wav``/PortAudio landet. Sie nutzt nur
    vorhandene DeviceHub-Felder/Methoden und bleibt strikt best-effort.
    """
    if hub is None:
        return False, "device_hub_unavailable"
    try:
        if not bool(getattr(hub, "audio_enable", True)):
            return False, "audio_disabled"
    except Exception:
        pass
    try:
        resolver = getattr(hub, "_resolve_output_device_index", None)
        if callable(resolver):
            idx = resolver()
            if idx is None:
                return False, "no_output_device"
            return True, "ok"
    except Exception as e:
        log_suppressed(LOG, key="wrappers_tts_wrapper.hub_output_precheck", msg="DeviceHub output precheck failed", exc=e, level=logging.DEBUG, interval_s=600)
        return False, "output_precheck_failed"
    return True, "ok"

def _get_hub():
    """Lazy-Import: gibt den DeviceHub zurück oder None."""
    try:
        from core.device_hub import get_hub  # type: ignore
        return get_hub()
    except Exception as e:
        log.debug("DeviceHub nicht verfügbar: %s", e)
        return None

def _hub_play_wav(hub, data: bytes, client: Optional[str] = None) -> bool:
    """
    Verträgt beide Signaturen:
      • play_wav(wav_bytes)
      • play_wav(wav_bytes, client="...")  (zukünftig)
    """
    try:
        # Versuch mit client-Argument
        return bool(hub.play_wav(data, client=client or "tts"))
    except TypeError:
        # Fallback auf alte Signatur ohne client
        return bool(hub.play_wav(data))
    except Exception as e:
        log.error("DeviceHub play_wav Fehler: %s", e)
        return False

# -----------------------------------------------------------------------------
# Engine-API
# -----------------------------------------------------------------------------
class TTSEngineBase:
    """Abstrakte Engine-API."""
    def list_voices(self) -> List[Dict[str, Any]]: return []
    def set_voice(self, voice_id_or_name: str) -> bool: return False
    def set_rate(self, words_per_min: int) -> None: pass
    def set_volume(self, volume_0_1: float) -> None: pass
    def speak_chunk(self, text: str, blocking: bool = True) -> None:
        raise NotImplementedError
    def synth_to_wav_bytes(self, text: str) -> Optional[bytes]:
        raise NotImplementedError
    def stop(self) -> None: pass

# -----------------------------------------------------------------------------
# Engine 1: pyttsx3
# -----------------------------------------------------------------------------
class Pyttsx3Engine(TTSEngineBase):
    def __init__(self):
        import pyttsx3
        self._engine = pyttsx3.init()
        self.set_rate(_norm_rate(_env_int("OROMA_TTS_RATE", 180)))
        self.set_volume(_norm_volume(_env_float("OROMA_TTS_VOLUME", 1.0)))
        v = os.environ.get("OROMA_TTS_VOICE", "").strip()
        if v:
            self.set_voice(v)
        log.info("Pyttsx3Engine aktiv.")

    def list_voices(self) -> List[Dict[str, Any]]:
        try:
            vs = self._engine.getProperty("voices") or []
            return [
                {"id": v.id, "name": getattr(v, "name", ""), "lang": getattr(v, "languages", "")}
                for v in vs
            ]
        except Exception as e:
            log_suppressed(LOG, key="wrappers_tts_wrapper.ret.3", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return []

    def set_voice(self, voice_id_or_name: str) -> bool:
        try:
            needle = voice_id_or_name.lower()
            for v in self._engine.getProperty("voices"):
                nm = str(getattr(v, "name", "")).lower()
                lg = str(getattr(v, "languages", "")).lower()
                if voice_id_or_name == v.id or needle in nm or needle in lg:
                    self._engine.setProperty("voice", v.id)
                    log.info("pyttsx3 Voice gesetzt: %s", v.id)
                    return True
        except Exception as e:
            log.debug("pyttsx3 set_voice Fehler: %s", e)
        return False

    def set_rate(self, words_per_min: int) -> None:
        try:
            self._engine.setProperty("rate", _norm_rate(words_per_min))
        except Exception as e:
            log_suppressed(LOG, key="wrappers_tts_wrapper.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    def set_volume(self, volume_0_1: float) -> None:
        try:
            self._engine.setProperty("volume", _norm_volume(volume_0_1))
        except Exception as e:
            log_suppressed(LOG, key="wrappers_tts_wrapper.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    def speak_chunk(self, text: str, blocking: bool = True) -> None:
        text = _norm_text(text)
        if not text:
            return
        if blocking:
            self._engine.say(text)
            self._engine.runAndWait()
        else:
            def _run():
                try:
                    self._engine.say(text)
                    self._engine.runAndWait()
                except Exception as e:
                    log.error("pyttsx3 speak non-blocking Fehler: %s", e)
            threading.Thread(target=_run, daemon=True).start()

    def synth_to_wav_bytes(self, text: str) -> Optional[bytes]:
        text = _norm_text(text)
        if not text:
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp = tf.name
            self._engine.saveToFile(text, tmp)
            self._engine.runAndWait()
            with open(tmp, "rb") as f:
                data = f.read()
            try: os.unlink(tmp)
            except Exception: pass
            return data
        except Exception as e:
            log.error("pyttsx3 synth_to_wav_bytes Fehler: %s", e)
            return None

    def stop(self) -> None:
        try: self._engine.stop()
        except Exception: pass

# -----------------------------------------------------------------------------
# Engine 2: espeak-ng (CLI)
# -----------------------------------------------------------------------------
class EspeakEngine(TTSEngineBase):
    def __init__(self):
        self._espeak = _which("espeak-ng") or _which("espeak")
        if not self._espeak:
            raise RuntimeError("espeak-ng/espeak nicht gefunden")
        self._voice  = os.environ.get("OROMA_TTS_VOICE", "de").strip() or "de"
        self._rate   = _norm_rate(_env_int("OROMA_TTS_RATE", 180))
        self._volume = _norm_volume(_env_float("OROMA_TTS_VOLUME", 1.0))
        log.info("EspeakEngine aktiv (%s, voice=%s, rate=%s, vol=%.2f)", self._espeak, self._voice, self._rate, self._volume)

    def _base_cmd(self) -> List[str]:
        return [self._espeak, "-s", str(self._rate), "-a", str(int(self._volume * 200)), "-v", self._voice]

    def list_voices(self) -> List[Dict[str, Any]]:
        return [
            {"id": "de", "name": "Deutsch"},
            {"id": "de+f3", "name": "Deutsch (f3)"},
            {"id": "en", "name": "English"},
            {"id": "en-us", "name": "English (US)"},
        ]

    def set_voice(self, voice_id_or_name: str) -> bool:
        self._voice = voice_id_or_name.strip() or self._voice
        return True

    def set_rate(self, words_per_min: int) -> None:
        self._rate = _norm_rate(words_per_min)

    def set_volume(self, volume_0_1: float) -> None:
        self._volume = _norm_volume(volume_0_1)

    def speak_chunk(self, text: str, blocking: bool = True) -> None:
        text = _norm_text(text)
        if not text:
            return
        cmd = self._base_cmd() + [text]
        if blocking:
            subprocess.run(cmd, check=True)
        else:
            subprocess.Popen(cmd)

    def synth_to_wav_bytes(self, text: str) -> Optional[bytes]:
        text = _norm_text(text)
        if not text:
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp = tf.name
            cmd = self._base_cmd() + ["-w", tmp, text]
            subprocess.run(cmd, check=True)
            with open(tmp, "rb") as f:
                data = f.read()
            try: os.unlink(tmp)
            except Exception: pass
            return data
        except Exception as e:
            log.error("espeak-ng synth_to_wav_bytes Fehler: %s", e)
            return None

# -----------------------------------------------------------------------------
# Engine 3: pico2wave (+ aplay)
# -----------------------------------------------------------------------------
class Pico2WaveEngine(TTSEngineBase):
    def __init__(self):
        self._pico = _which("pico2wave")
        self._aplay = _which("aplay")
        if not self._pico:
            raise RuntimeError("pico2wave nicht gefunden")
        self._voice = os.environ.get("OROMA_TTS_VOICE", "de-DE").strip() or "de-DE"
        log.info("Pico2WaveEngine aktiv (voice=%s)", self._voice)

    def list_voices(self) -> List[Dict[str, Any]]:
        return [
            {"id": "de-DE", "name": "Deutsch"},
            {"id": "en-US", "name": "English (US)"},
        ]

    def set_voice(self, voice_id_or_name: str) -> bool:
        self._voice = voice_id_or_name.strip() or self._voice
        return True

    def speak_chunk(self, text: str, blocking: bool = True) -> None:
        # Nur für direkte Wiedergabe außerhalb DeviceHub relevant
        text = _norm_text(text)
        if not text:
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp = tf.name
        try:
            subprocess.run([self._pico, "-l", self._voice, "-w", tmp, text], check=True)
            if self._aplay:
                if blocking:
                    subprocess.run([self._aplay, tmp], check=True)
                else:
                    subprocess.Popen([self._aplay, tmp])
            else:
                log.warning("aplay nicht gefunden – direkte Wiedergabe übersprungen.")
        finally:
            try: os.unlink(tmp)
            except Exception: pass

    def synth_to_wav_bytes(self, text: str) -> Optional[bytes]:
        text = _norm_text(text)
        if not text:
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp = tf.name
            subprocess.run([self._pico, "-l", self._voice, "-w", tmp, text], check=True)
            with open(tmp, "rb") as f:
                data = f.read()
            try: os.unlink(tmp)
            except Exception: pass
            return data
        except Exception as e:
            log.error("pico2wave synth_to_wav_bytes Fehler: %s", e)
            return None

# -----------------------------------------------------------------------------
# Öffentlicher Wrapper (Fallback/Orchestrierung)
# -----------------------------------------------------------------------------

@dataclass
class _Job:
    text: str
    use_hub: bool
    client: Optional[str]

class TTSWrapper:
    """Orchestriert die Engines (pyttsx3 → espeak-ng → pico2wave) und optional DeviceHub-Ausgabe."""
    def __init__(self, engine_preference: Optional[List[str]] = None):
        env_backend = os.environ.get("OROMA_TTS_BACKEND", "auto").strip().lower()
        self._use_hub_default = _USE_HUB
        self._backend_name: str = ""
        self._engine: TTSEngineBase

        if engine_preference is None:
            if env_backend in ("pyttsx3", "espeak", "espeak-ng", "pico", "pico2wave"):
                engine_preference = [env_backend]
            else:
                engine_preference = ["pyttsx3", "espeak", "pico"]

        last_err: Optional[str] = None
        for name in engine_preference:
            try:
                if name == "pyttsx3":
                    self._engine = Pyttsx3Engine(); self._backend_name = "pyttsx3"; break
                if name in ("espeak", "espeak-ng"):
                    self._engine = EspeakEngine(); self._backend_name = "espeak-ng"; break
                if name in ("pico", "pico2wave"):
                    self._engine = Pico2WaveEngine(); self._backend_name = "pico2wave"; break
            except Exception as e:
                last_err = f"{name}: {e}"
                log.debug("TTS-Engine %s nicht verfügbar: %s", name, e)
        else:
            err = f"Keine TTS-Engine verfügbar (letzter Fehler: {last_err})"
            _remember_tts_failure(err)
            raise RuntimeError(err)

        # Worker (Non-Blocking)
        self._q: "queue.Queue[_Job]" = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        _clear_tts_failure()
        log.info("TTSWrapper aktiv: %s (use_hub=%s)", self._backend_name, self._use_hub_default)

    # ---------------- interne Helfer ----------------

    def _worker_loop(self):
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._speak_impl(job.text, blocking=True, use_hub=job.use_hub, client=job.client)
            except Exception as e:
                log_suppressed(LOG, key="wrappers_tts_wrapper.worker", msg="TTS Worker-Fehler", exc=e, level=logging.ERROR, interval_s=300)
            finally:
                self._q.task_done()

    def _play_via_hub(self, text: str, client: Optional[str]) -> bool:
        hub = _get_hub()
        if not hub:
            return False
        if _TTS_HUB_OUTPUT_PRECHECK:
            ok, reason = _hub_output_ready(hub)
            if not ok:
                _remember_output_block(reason)
                return False
        maxc = _env_int("OROMA_TTS_MAX_CHARS", 280)
        chunks = _chunk_text(text, maxc)
        ok_all = True
        for c in chunks:
            data = self._engine.synth_to_wav_bytes(c)
            if not data:
                ok_all = False
                continue
            ok = _hub_play_wav(hub, data, client=client or "tts")
            ok_all = ok_all and bool(ok)
        if ok_all:
            _clear_output_block()
        else:
            _remember_output_block("device_hub_playback_failed")
        return ok_all

    def _speak_impl(self, text: str, blocking: bool, use_hub: bool, client: Optional[str]) -> Dict[str, Any]:
        text = _norm_text(text)
        if not text:
            return {"ok": False, "error": "empty_text", "backend": self.backend}

        blocked_reason = _output_block_active()
        if blocked_reason and not _TTS_DIRECT_PLAYBACK_ENABLE:
            return _skip_result(blocked_reason, backend=self.backend, mode="blocking" if blocking else "non-blocking")

        if use_hub:
            hub = _get_hub()
            if hub is not None:
                if self._play_via_hub(text, client=client):
                    return {"ok": True, "backend": self.backend, "mode": "blocking" if blocking else "non-blocking", "hub": True}
                reason = _TTS_OUTPUT_BLOCK_REASON or "device_hub_playback_failed"
                if not _TTS_DIRECT_PLAYBACK_ENABLE:
                    return _skip_result(reason, backend=self.backend, mode="blocking" if blocking else "non-blocking")
                log_suppressed(
                    LOG,
                    key="wrappers_tts_wrapper.direct_fallback",
                    msg="DeviceHub-Ausgabe fehlgeschlagen – direkte Engine-Wiedergabe als explizit aktivierter Fallback.",
                    level=logging.INFO,
                    interval_s=300,
                )
            elif not _TTS_DIRECT_PLAYBACK_ENABLE:
                return _skip_result("device_hub_unavailable", backend=self.backend, mode="blocking" if blocking else "non-blocking")

        if not _TTS_DIRECT_PLAYBACK_ENABLE:
            return _skip_result("direct_playback_disabled", backend=self.backend, mode="blocking" if blocking else "non-blocking")

        # Direkte Engine-Wiedergabe – bewusst nur wenn explizit freigegeben.
        maxc = _env_int("OROMA_TTS_MAX_CHARS", 280)
        for c in _chunk_text(text, maxc):
            self._engine.speak_chunk(c, blocking=blocking)
        return {"ok": True, "backend": self.backend, "mode": "blocking" if blocking else "non-blocking", "hub": False, "direct": True}

    # ---------------- Public API ----------------

    @property
    def backend(self) -> str:
        return self._backend_name

    def list_voices(self) -> List[Dict[str, Any]]:
        return self._engine.list_voices()

    def set_voice(self, voice_id_or_name: str) -> bool:
        return self._engine.set_voice(voice_id_or_name)

    def set_rate(self, words_per_min: int) -> None:
        self._engine.set_rate(words_per_min)

    def set_volume(self, volume_0_1: float) -> None:
        self._engine.set_volume(volume_0_1)

    def speak(self, text: str, blocking: bool = True, use_device_hub: Optional[bool] = None, client: Optional[str] = "tts") -> Dict[str, Any]:
        """
        Spricht Text. non-blocking → Queue + Worker.
        use_device_hub: None→ENV/Default, True/False überschreibt.
        client: optional (für zukünftiges Hub-Audit mit client-Attribut).
        """
        try:
            use_hub = self._use_hub_default if use_device_hub is None else bool(use_device_hub)
            if blocking:
                return self._speak_impl(text, blocking=True, use_hub=use_hub, client=client)
            else:
                blocked_reason = _output_block_active()
                if blocked_reason and not _TTS_DIRECT_PLAYBACK_ENABLE:
                    return _skip_result(blocked_reason, backend=self.backend, mode="non-blocking")
                self._q.put_nowait(_Job(text=text, use_hub=use_hub, client=client))
                return {"ok": True, "backend": self.backend, "mode": "non-blocking", "queued": True, "hub": bool(use_hub and _get_hub() is not None)}
        except queue.Full:
            return {"ok": False, "error": "queue_full"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_to_file(self, text: str, wav_path: str) -> Dict[str, Any]:
        """
        Speichert kompletten Text als WAV. Pro Chunk synthetisieren und
        hintereinander in eine Datei schreiben (einheitliche Params).
        """
        text = _norm_text(text)
        if not text:
            return {"ok": False, "error": "empty_text"}
        chunks = _chunk_text(text, _env_int("OROMA_TTS_MAX_CHARS", 280))
        if not chunks:
            return {"ok": False, "error": "no_chunks"}
        params: Optional[Tuple[int,int,int,int,int,int]] = None
        frames_all: List[bytes] = []
        for c in chunks:
            data = self._engine.synth_to_wav_bytes(c)
            if not data:
                continue
            with wave.open(io.BytesIO(data), "rb") as w:
                p = w.getparams()
                f = w.readframes(w.getnframes())
            if params is None:
                params = (p.nchannels, p.sampwidth, p.framerate, p.nframes, p.comptype, p.compname)
            else:
                if p.nchannels != params[0] or p.sampwidth != params[1] or p.framerate != params[2]:
                    log.warning("Chunk-Parameter weichen ab (nch/sampwidth/fr). Datei könnte inkonsistent sein.")
            frames_all.append(f)
        if not frames_all or params is None:
            return {"ok": False, "error": "synthesis_failed"}
        try:
            with wave.open(wav_path, "wb") as out:
                out.setnchannels(params[0]); out.setsampwidth(params[1]); out.setframerate(params[2])
                for fr in frames_all:
                    out.writeframes(fr)
            return {"ok": True, "path": wav_path, "sr": params[2]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stop(self) -> None:
        self._stop.set()
        try: self._engine.stop()
        except Exception: pass

# -----------------------------------------------------------------------------
# Singleton / Kompatibilitätsfunktionen
# -----------------------------------------------------------------------------
_singleton: Optional[TTSWrapper] = None
_singleton_lock = threading.Lock()

def _get_singleton() -> TTSWrapper:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            if not _tts_retry_allowed():
                msg = _TTS_INIT_FAILURE or "Keine TTS-Engine verfügbar"
                _log_tts_missing_ratelimited(msg)
                raise RuntimeError(msg)
            try:
                _singleton = TTSWrapper()
            except Exception as e:
                _remember_tts_failure(str(e))
                _log_tts_missing_ratelimited(str(e))
                raise
        return _singleton

def speak(text: str, blocking: bool = True, use_device_hub: Optional[bool] = None, client: Optional[str] = "tts") -> Dict[str, Any]:
    """Kompatible Modul-Funktion (Singleton)."""
    try:
        return _get_singleton().speak(text, blocking=blocking, use_device_hub=use_device_hub, client=client)
    except Exception as e:
        msg = str(e)
        _remember_tts_failure(msg)
        _log_tts_missing_ratelimited(msg)
        return {"ok": False, "error": msg}

def list_voices() -> List[Dict[str, Any]]:
    try:
        return _get_singleton().list_voices()
    except Exception as e:
        log_suppressed(LOG, key="wrappers_tts_wrapper.ret.6", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return []

def save_to_file(text: str, wav_path: str) -> Dict[str, Any]:
    try:
        return _get_singleton().save_to_file(text, wav_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

# -----------------------------------------------------------------------------
# Sauberer Shutdown
# -----------------------------------------------------------------------------
def _atexit():
    try:
        if _singleton:
            _singleton.stop()
    except Exception as e:
        log_suppressed(LOG, key="wrappers_tts_wrapper.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

atexit.register(_atexit)

# -----------------------------------------------------------------------------
# Selbsttest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    log.setLevel("DEBUG")
    print("=== ORÓMA TTS Selftest ===")
    print("Backend:", _get_singleton().backend)
    print("Voices (erste 3):", json.dumps(list_voices()[:3], ensure_ascii=False))
    txt = "Hallo ORÓMA! Dies ist ein kurzer Test. Wie geht es dir?"
    res = speak(txt, blocking=True, use_device_hub=_USE_HUB, client="tts_selftest")
    print("Speak blocking:", res)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        path = tf.name
    r2 = save_to_file("Dieser Text wird in eine WAV-Datei geschrieben.", path)
    print("save_to_file:", r2)
    print("WAV:", path)
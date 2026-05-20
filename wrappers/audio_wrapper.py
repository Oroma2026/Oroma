#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/wrappers/audio_wrapper.py
# Projekt: ORÓMA
# Modul:   Audio Wrapper (Hub-first, Features, ASR)
# Version: v3.8 (prod) + Audio-Student-Logging
# Stand:   2026-01-03
#
# Zweck
# ─────
#     • NEU: whisper.cpp Backend (ohne venv/Torch) via OROMA_ASR_BACKEND
#   Einheitlicher Audio-Wrapper für ORÓMA mit Priorität auf DeviceHub:
#     • Aufnahme (Mono-PCM float32) → Hub.read_audio(...) oder Fallback via
#       sounddevice-Callback + Ringbuffer
#     • Echtzeit-Features: RMS, ZCR, Pitch (librosa YIN oder ACF-Fallback),
#       Log-Power-Spektrum (64 Bins), kompakter Snap-Vektor
#     • Transkription (optional): Whisper (lazy load)
#     • Playback: Hub.play_wav()/play_pcm() (Audit) oder direkter Fallback
#
# Highlights
# ──────────
#   • "Hub-first": benutzt automatisch den zentralen DeviceHub für Capture &
#     Playback (inkl. Audit-Logging dort). Fällt robust auf lokale Streams zurück.
#   • Saubere Threading-Architektur: Worker erzeugt periodisch Feature-Snapshots
#     mit Fenster/Hop (überlappend), ohne Busy-Waits.
#   • ENV-gesteuert, defensive Fehlerbehandlung, Selftest.
#
# Öffentliche API
# ───────────────
#   class AudioWrapper:
#       start()/stop()
#       get_features(timeout=...) -> dict|None
#       get_audio_level() -> float (0..1)
#       read_audio(seconds) -> np.ndarray (float32, mono)
#       record_wav(seconds, sr=None) -> bytes
#       play_pcm(pcm, sr=None) -> bool
#       play_wav(wav_bytes) -> bool
#       transcribe(audio: np.ndarray|bytes) -> str|None
#
#   Helper:
#       asr_stream(language="de", model_name="small", duration=5) -> {ok, text|error}
#
# ENV
# ───
#   OROMA_AUDIO_WRAPPER_USE_HUB=true|false     (Default: true)
#   OROMA_AUDIO_SR=16000                       (ASR-geeignet)
#   OROMA_AUDIO_CH=1                           (mono)
#   OROMA_AUDIO_BLOCK_MS=20                    (Fallback-Callbackgröße)
#   OROMA_AUDIO_RING_SEC=10                    (Fallback-Ringlänge)
#   OROMA_AUDIO_INPUT_NAME=...                 (Fallback: Substring-Match)
#   OROMA_AUDIO_OUTPUT_NAME=...
#   OROMA_AUDIO_FEATURE_WIN=0.50               (Fensterlänge s)
#   OROMA_AUDIO_FEATURE_HOP=0.25               (Hop s)
#   OROMA_LOG_LEVEL=INFO|DEBUG|...
#   OROMA_WHISPER_ENABLE=true|false            (Default: true, falls Paket vorhanden)
#   OROMA_WHISPER_MODEL=tiny|base|small|...    (Default: small)
#   OROMA_WHISPER_LANG=de|en|...               (Default: de)
#
#   Audio-Student (Teacher/Student-Paare, nur Logging – optional):
#   OROMA_AUDIO_STUDENT_ENABLED=0|1            (Default: 1, siehe core/audio_student.py)
#
# Lizenz: MIT (ORÓMA)
# =============================================================================

from __future__ import annotations

import os
import io
import time
import wave
import math
import atexit
import queue
import logging
from core.log_guard import log_suppressed
import threading
from collections import deque
from typing import Optional, Dict, Any, List, Tuple, Union

import subprocess
import tempfile
import re

import numpy as np

# Optional: Hub
try:
    from core.device_hub import get_hub  # type: ignore
except Exception:
    get_hub = None  # type: ignore

# Optional: Fallback-Capture/Playback
try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover
    sd = None  # type: ignore

# Optional: Pitch/ASR
try:
    import librosa  # type: ignore
except Exception:  # pragma: no cover
    librosa = None  # type: ignore

try:
    import whisper  # type: ignore
except Exception:  # pragma: no cover
    whisper = None  # type: ignore

# Optional: Audio-Student (Teacher/Student-Logger)
try:
    from core import audio_student as _audio_student  # type: ignore
except Exception:
    _audio_student = None  # type: ignore


# =============================================================================
# Logging
# =============================================================================

LOG = logging.getLogger("oroma.audio")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [Audio] %(message)s"))
    LOG.addHandler(_h)
LOG.setLevel(os.environ.get("OROMA_LOG_LEVEL", "INFO"))


# =============================================================================
# ENV-Helper
# =============================================================================

def _env_bool(k: str, default: bool) -> bool:
    v = str(os.environ.get(k, "")).strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")

def _env_int(k: str, default: int) -> int:
    try:
        return int(os.environ.get(k, default))
    except Exception as e:
        log_suppressed(LOG, key="wrappers_audio_wrapper.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _env_float(k: str, default: float) -> float:
    try:
        return float(os.environ.get(k, default))
    except Exception as e:
        log_suppressed(LOG, key="wrappers_audio_wrapper.ret.2", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _env_str(k: str, default: str) -> str:
    v = os.environ.get(k)
    return v if v not in (None, "") else default


# =============================================================================
# DSP / Feature-Helfer
# =============================================================================

# =============================================================================
# Whisper.cpp (no-venv ASR-Backend)
# =============================================================================
#
# Steuerung per ENV:
#   OROMA_ASR_BACKEND=auto|whispercpp|whisper_py|disabled
#
#   OROMA_WHISPERCPP_ENABLE=true|false (Default: true)
#   OROMA_WHISPERCPP_BIN=/pfad/whisper-cli
#       Default: /opt/ai/oroma/third_party/whisper.cpp/build/bin/whisper-cli
#
#   OROMA_WHISPERCPP_MODEL=/pfad/model.bin
#       Default: (auto) /opt/ai/oroma/third_party/whisper.cpp/models/ggml-<model>.bin
#                fallback: ggml-base.bin
#
#   OROMA_WHISPERCPP_THREADS=4 (Default: CPU count)
#   OROMA_WHISPERCPP_TIMEOUT_SEC=120
#
# Hinweise:
#   • whisper.cpp erzeugt Misch-Output (stdout/stderr). Wir parsen bevorzugt
#     Segment-Zeilen mit Timestamps: "[00:00:.. --> 00:00:..] Text".
#   • Falls Timestamps deaktiviert sind, fällt der Parser auf die letzte
#     "nicht-Status" Zeile zurück.
# =============================================================================

_TS_LINE_RE = re.compile(r"^\[[0-9:.]+\s+-->\s+[0-9:.]+\]\s*(.*)$")

def _is_executable(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)
    except Exception as e:
        log_suppressed(LOG, key="wrappers_audio_wrapper.ret.3", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

def _parse_whispercpp_stdout(out: str) -> str:
    if not out:
        return ""
    parts: List[str] = []
    for line in out.splitlines():
        s = (line or "").strip()
        if not s:
            continue
        m = _TS_LINE_RE.match(s)
        if m:
            seg = (m.group(1) or "").strip()
            if seg:
                parts.append(seg)
    if parts:
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    # Fallback: letzte sinnvolle Zeile
    bad_prefix = (
        "whisper_", "main:", "system_info:", "ggml_", "warning:", "error:",
        "load time", "total time",
    )
    cand = ""
    for line in out.splitlines():
        s = (line or "").strip()
        if not s:
            continue
        low = s.lower()
        if any(low.startswith(p) for p in bad_prefix):
            continue
        cand = s
    return cand.strip()

def _whispercpp_transcribe_wav_bytes(
    wav_bytes: bytes,
    *,
    lang: str,
    cli_path: str,
    model_path: str,
    threads: int,
    timeout_sec: int,
) -> str:
    if not wav_bytes:
        return ""
    if not _is_executable(cli_path):
        raise RuntimeError(f"whisper.cpp CLI nicht ausführbar: {cli_path}")
    if not os.path.isfile(model_path):
        raise RuntimeError(f"whisper.cpp Model nicht gefunden: {model_path}")

    tmp = tempfile.NamedTemporaryFile(prefix="oroma_asr_", suffix=".wav", delete=False)
    tmp_path = tmp.name
    try:
        tmp.write(wav_bytes)
        tmp.flush()
        tmp.close()

        cmd = [cli_path, "-m", model_path, "-f", tmp_path]
        if lang:
            cmd += ["-l", lang]
        if threads and threads > 0:
            cmd += ["-t", str(int(threads))]

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=max(10, int(timeout_sec)), check=False)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        if p.returncode != 0:
            # Retry minimal ohne -t (Flag-Kompatibilität)
            cmd2 = [cli_path, "-m", model_path, "-f", tmp_path]
            if lang:
                cmd2 += ["-l", lang]
            p2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=max(10, int(timeout_sec)), check=False)
            out = (p2.stdout or "") + "\n" + (p2.stderr or "")
            if p2.returncode != 0:
                raise RuntimeError(f"whisper.cpp CLI Fehler (rc={p2.returncode}): {out.strip()[:800]}")

        return _parse_whispercpp_stdout(out)

    finally:
        try:
            os.unlink(tmp_path)
        except Exception as e:
            log_suppressed(LOG, key="wrappers_audio_wrapper.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


def _safe_rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float32), dtype=np.float32)))

def _zcr(x: np.ndarray) -> float:
    if x.size < 2:
        return 0.0
    s = np.sign(x)
    return float(np.mean((s[1:] != s[:-1]).astype(np.float32)))

def _pitch_librosa(x: np.ndarray, sr: int, fmin: float = 50.0, fmax: float = 500.0) -> float:
    if librosa is None or x.size == 0:
        return 0.0
    try:
        yin = librosa.yin(x.astype(np.float32), fmin=fmin, fmax=fmax, sr=sr)
        if yin is None or yin.size == 0:
            return 0.0
        # Nehme median zur Robustheit
        p = float(np.median(yin))
        return p if math.isfinite(p) else 0.0
    except Exception as e:
        log_suppressed(LOG, key="wrappers_audio_wrapper.ret.5", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return 0.0

def _pitch_acf(x: np.ndarray, sr: int, fmin: float = 50.0, fmax: float = 500.0) -> float:
    """Einfache ACF-Pitch-Schätzung als Fallback (ohne Drittlibs)."""
    n = x.size
    if n < 4 or sr <= 0:
        return 0.0
    # Normalisieren
    x = x.astype(np.float32)
    x -= float(np.mean(x))
    if np.allclose(x, 0.0):
        return 0.0
    # FFT-basierte Autokorrelation (schnell)
    nfft = 1
    while nfft < 2 * n:
        nfft <<= 1
    X = np.fft.rfft(x, n=nfft)
    acf = np.fft.irfft(np.abs(X) ** 2)[:n]
    acf /= acf[0] if acf[0] != 0 else 1.0
    # Suchbereich
    min_lag = int(sr / max(fmax, 1.0))
    max_lag = int(sr / max(fmin, 1.0))
    min_lag = max(min_lag, 1)
    max_lag = min(max_lag, n - 1) if n > 1 else min_lag
    if max_lag <= min_lag:
        return 0.0
    lag = int(np.argmax(acf[min_lag:max_lag]) + min_lag)
    if lag <= 0:
        return 0.0
    # Parabolische Interpolation um das Maximum
    if 1 <= lag < len(acf) - 1:
        y0, y1, y2 = acf[lag - 1], acf[lag], acf[lag + 1]
        denom = (y0 - 2 * y1 + y2)
        if denom != 0.0:
            delta = 0.5 * (y0 - y2) / denom
            lag = float(lag + delta)
    f0 = float(sr / lag) if lag > 0 else 0.0
    return f0 if (50.0 <= f0 <= 500.0 and math.isfinite(f0)) else 0.0

def _log_power_spectrum(x: np.ndarray, n_bins: int = 64) -> np.ndarray:
    """Einfache Log-Power-Features in linearen Bins (0..Nyquist)."""
    if x.size == 0:
        return np.zeros((n_bins,), dtype=np.float32)
    nfft = 1
    while nfft < x.size:
        nfft <<= 1
    X = np.fft.rfft(x, n=nfft)
    pwr = np.abs(X) ** 2
    # Log-Komprimierung
    pwr = np.log1p(pwr).astype(np.float32)
    # Binning
    bins = np.linspace(0, pwr.size - 1, n_bins + 1, dtype=np.int32)
    out = np.zeros((n_bins,), dtype=np.float32)
    for i in range(n_bins):
        a, b = bins[i], bins[i + 1]
        if b <= a:
            out[i] = 0.0
        else:
            out[i] = float(np.mean(pwr[a:b]))
    # Normierung (0..1)
    m = float(np.max(out)) if out.size else 1.0
    return out / (m + 1e-9)

def _features_from_signal(x: np.ndarray, sr: int) -> Dict[str, Any]:
    x = x.astype(np.float32).reshape(-1)
    rms = _safe_rms(x)
    zcr = _zcr(x)
    # Pitch
    p_lib = _pitch_librosa(x, sr) if librosa is not None else 0.0
    pitch = p_lib if p_lib > 0 else _pitch_acf(x, sr)
    spec = _log_power_spectrum(x, n_bins=64)
    snap = np.array([rms, zcr, pitch, float(np.mean(spec)), float(np.std(spec))], dtype=np.float32)
    return {
        "rms": float(rms),
        "zcr": float(zcr),
        "pitch": float(pitch),
        "spectrum": spec.tolist(),
        "snap_feature": snap.tolist(),
    }


# =============================================================================
# AudioWrapper
# =============================================================================

class AudioWrapper:
    """
    Hub-first Audio-Wrapper.
    - Nutzt primär den DeviceHub (Audit-fähig), ansonsten Fallback via sounddevice.
    - Erzeugt periodisch Feature-Snapshots (Fenster/Hop) im Worker-Thread.
    """

    def __init__(
        self,
        samplerate: int = _env_int("OROMA_AUDIO_SR", 16000),
        channels: int = _env_int("OROMA_AUDIO_CH", 1),
        use_hub: Optional[bool] = None,
        client: str = "audio_wrapper",
        feature_win: float = _env_float("OROMA_AUDIO_FEATURE_WIN", 0.50),
        feature_hop: float = _env_float("OROMA_AUDIO_FEATURE_HOP", 0.25),
        block_ms: int = _env_int("OROMA_AUDIO_BLOCK_MS", 20),
        ring_sec: int = _env_int("OROMA_AUDIO_RING_SEC", 10),
        in_name: Optional[str] = _env_str("OROMA_AUDIO_INPUT_NAME", ""),
        out_name: Optional[str] = _env_str("OROMA_AUDIO_OUTPUT_NAME", ""),
        enable_whisper: Optional[bool] = None,
        whisper_model: str = _env_str("OROMA_WHISPER_MODEL", "small"),
        whisper_lang: str = _env_str("OROMA_WHISPER_LANG", "de"),
    ):
        self.sr = int(samplerate)
        self.ch = int(channels)
        self.client = client
        self.win = max(0.1, float(feature_win))
        self.hop = max(0.05, float(feature_hop))
        self.block_ms = int(block_ms)
        self.ring_sec = int(ring_sec)
        self.want_in_name = (in_name or "").strip()
        self.want_out_name = (out_name or "").strip()

        # Hub-Integration
        env_hub = _env_bool("OROMA_AUDIO_WRAPPER_USE_HUB", True)
        self.use_hub = env_hub if use_hub is None else bool(use_hub)
        self.hub = None
        if self.use_hub and get_hub is not None:
            try:
                self.hub = get_hub()
            except Exception as e:
                LOG.warning("DeviceHub nicht verfügbar: %s (Fallback aktiv)", e)
                self.hub = None

        # Fallback State
        self._stream = None
        self._ring: deque[np.ndarray] = deque(maxlen=max(1, int(self.sr * self.ring_sec) // max(1, int(self.sr * self.block_ms / 1000))))
        self._ring_lock = threading.Lock()

        # Worker / Queues
        self._feat_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._thr: Optional[threading.Thread] = None

        # ASR
        if enable_whisper is None:
            enable_whisper = _env_bool("OROMA_WHISPER_ENABLE", True)
        self._whisper_enable = bool(enable_whisper and whisper is not None)
        self._whisper_model_name = whisper_model
        self._whisper_lang = whisper_lang
        self._whisper_model = None  # lazy load

        # -----------------------------------------------------------------
        # ASR Backend Auswahl / whisper.cpp (Torch-frei, global installbar)
        #
        # Hinweis:
        #   • Python-Whisper (Torch) kann weiterhin optional genutzt werden
        #   • whisper.cpp ist CLI-basiert und benötigt nur Binary + Model-Datei
        #
        # Auswahl über ENV:
        #   OROMA_ASR_BACKEND=auto|whispercpp|whisper_py|disabled
        #
        # whisper.cpp Pfade:
        #   OROMA_WHISPERCPP_BIN=/opt/ai/oroma/third_party/whisper.cpp/build/bin/whisper-cli
        #   OROMA_WHISPERCPP_MODEL=/opt/ai/oroma/third_party/whisper.cpp/models/ggml-base.bin
        # -----------------------------------------------------------------
        self._asr_backend = (_env_str("OROMA_ASR_BACKEND", "auto") or "auto").strip().lower()

        # whisper.cpp defaults (Headless, ohne venv)
        _cli_default = "/opt/ai/oroma/third_party/whisper.cpp/build/bin/whisper-cli"
        _cli_alt = "/usr/local/bin/whisper-cli"
        _model_default = "/opt/ai/oroma/third_party/whisper.cpp/models/ggml-base.bin"

        self._whispercpp_bin = (_env_str("OROMA_WHISPERCPP_BIN", _cli_default) or _cli_default).strip()
        if (not self._whispercpp_bin) and os.path.isfile(_cli_alt):
            self._whispercpp_bin = _cli_alt

        self._whispercpp_model = (_env_str("OROMA_WHISPERCPP_MODEL", _model_default) or _model_default).strip()

        # Tuning
        _cpu = int(os.cpu_count() or 4)
        self._whispercpp_threads = int(_env_int("OROMA_WHISPERCPP_THREADS", max(1, min(4, _cpu))))
        self._whispercpp_timeout_sec = int(_env_int("OROMA_WHISPERCPP_TIMEOUT_SEC", 25))

        # Availability (binary executable + model file readable)
        self._whispercpp_available = bool(_is_executable(self._whispercpp_bin) and os.path.isfile(self._whispercpp_model))
        if self._asr_backend in ("whispercpp", "cpp", "whisper_cpp") and not self._whispercpp_available:
            LOG.warning(
                "ASR whisper.cpp angefordert, aber Binary/Model fehlt oder nicht ausführbar: bin=%s model=%s",
                self._whispercpp_bin,
                self._whispercpp_model,
            )

        LOG.info(
            "AudioWrapper init: hub=%s, sr=%d, ch=%d, win=%.2fs, hop=%.2fs, whisper=%s(%s,%s)",
            "yes" if self.hub is not None else "no",
            self.sr, self.ch, self.win, self.hop,
            "on" if self._whisper_enable else "off",
            self._whisper_model_name, self._whisper_lang
        )

        LOG.info(
            "ASR init: backend=%s | whisper_py=%s | whispercpp=%s (bin=%s, model=%s)",
            self._asr_backend,
            "on" if self._whisper_enable else "off",
            "ok" if getattr(self, "_whispercpp_available", False) else "missing",
            getattr(self, "_whispercpp_bin", ""),
            getattr(self, "_whispercpp_model", ""),
        )


    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------
    def start(self) -> None:
        if self._thr and self._thr.is_alive():
            return
        # Hub-first Mic
        if self.hub is not None:
            try:
                self.hub.start_mic(client=self.client)
            except Exception as e:
                LOG.warning("Hub.start_mic fehlgeschlagen: %s (Fallback versuchen)", e)
                self.hub = None  # harte Umstellung auf Fallback
        # Fallback starten falls kein Hub
        if self.hub is None:
            self._start_fallback_stream()
        # Worker
        self._stop.clear()
        self._thr = threading.Thread(target=self._loop, name="oroma-audio-worker", daemon=True)
        self._thr.start()
        LOG.info("AudioWrapper gestartet (worker aktiv).")

    def stop(self) -> None:
        self._stop.set()
        if self._thr and self._thr.is_alive():
            try:
                self._thr.join(timeout=1.5)
            except Exception as e:
                log_suppressed(LOG, key="wrappers_audio_wrapper.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        self._thr = None
        # Fallback stream stoppen
        self._stop_fallback_stream()
        # Hub Mic stoppen (nur wenn wir ihn gestartet haben)
        if self.hub is not None:
            try:
                self.hub.stop_mic(client=self.client)
            except Exception as e:
                log_suppressed(LOG, key="wrappers_audio_wrapper.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        LOG.info("AudioWrapper gestoppt.")

    def __del__(self):
        try:
            self.stop()
        except Exception as e:
            log_suppressed(LOG, key="wrappers_audio_wrapper.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # ---------------------------------------------------------------------
    # Fallback-Stream (sounddevice)
    # ---------------------------------------------------------------------
    def _pick_device_index(self, want_name: str, want_input: bool) -> Optional[int]:
        if sd is None:
            return None
        try:
            devs = sd.query_devices()
            want = (want_name or "").lower().strip()
            best_idx = None
            for i, d in enumerate(devs):
                name = str(d.get("name", "")).lower()
                ok_ch = (d.get("max_input_channels", 0) > 0) if want_input else (d.get("max_output_channels", 0) > 0)
                if ok_ch and (not want or want in name):
                    best_idx = i
                    if want and want in name:
                        break
            return best_idx
        except Exception as e:
            log_suppressed(LOG, key="wrappers_audio_wrapper.ret.9", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def _sd_callback(self, indata, frames, time_info, status):  # sd.InputStream callback
        if status:
            LOG.debug("sd status: %s", status)
        if indata is None:
            return
        try:
            if indata.ndim == 2 and indata.shape[1] > 1:
                buf = np.mean(indata, axis=1, dtype=np.float32)
            else:
                buf = indata.reshape(-1).astype(np.float32)
        except Exception:
            try:
                buf = np.array(indata, dtype=np.float32).reshape(-1)
            except Exception:
                return
        with self._ring_lock:
            self._ring.append(buf)

    def _start_fallback_stream(self) -> None:
        if sd is None:
            LOG.warning("Kein DeviceHub und kein 'sounddevice' – Aufnahme nicht möglich.")
            return
        if self._stream is not None:
            return
        try:
            dev_idx = self._pick_device_index(self.want_in_name, want_input=True)
            blocksize = int(self.sr * self.block_ms / 1000)
            stream = sd.InputStream(
                samplerate=self.sr,
                channels=max(1, self.ch),
                dtype="float32",
                callback=self._sd_callback,
                blocksize=max(16, blocksize),
                device=dev_idx if dev_idx is not None else None,
            )
            stream.start()
            self._stream = stream
            LOG.info("Fallback-Mic gestartet (sr=%d ch=%d dev=%s)", self.sr, self.ch, str(dev_idx))
        except Exception as e:
            LOG.error("Fallback-Mic konnte nicht gestartet werden: %s", e)
            self._stream = None

    def _stop_fallback_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log_suppressed(LOG, key="wrappers_audio_wrapper.pass.10", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            self._stream = None

    def _fallback_concat(self) -> np.ndarray:
        with self._ring_lock:
            if not self._ring:
                return np.zeros((0,), dtype=np.float32)
            return np.concatenate(list(self._ring), dtype=np.float32) if len(self._ring) > 1 else self._ring[0].copy()

    # ---------------------------------------------------------------------
    # Worker
    # ---------------------------------------------------------------------
    def _loop(self) -> None:
        # simple overlapped pipeline
        next_t = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            # Audiofenster
            x = self.read_audio(self.win)
            if x.size > 0:
                feats = _features_from_signal(x, self.sr)
                feats.update({"sr": self.sr, "len": int(x.size), "ts": time.time()})
                self._put_q(self._feat_q, feats)
            # Schlaf bis zum nächsten Hop
            next_t = max(next_t + self.hop, time.time())
            dt = next_t - time.time()
            time.sleep(max(0.0, dt))

    @staticmethod
    def _put_q(q: "queue.Queue[Dict[str, Any]]", item: Dict[str, Any]) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                _ = q.get_nowait()
            except Exception as e:
                log_suppressed(LOG, key="wrappers_audio_wrapper.pass.11", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            try:
                q.put_nowait(item)
            except Exception as e:
                log_suppressed(LOG, key="wrappers_audio_wrapper.pass.12", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # ---------------------------------------------------------------------
    # Public: Features / Pegel
    # ---------------------------------------------------------------------
    def get_features(self, timeout: float = 0.5) -> Optional[Dict[str, Any]]:
        try:
            return self._feat_q.get(timeout=timeout)
        except Exception as e:
            log_suppressed(LOG, key="wrappers_audio_wrapper.ret.13", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def get_audio_level(self) -> float:
        """RMS-Level grob (Hub liefert aktuellen Level; Fallback schätzt)."""
        try:
            if self.hub is not None:
                return float(self.hub.get_audio_level())
        except Exception as e:
            log_suppressed(LOG, key="wrappers_audio_wrapper.pass.14", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        # Fallback: nutze letztes Fenster kurz
        x = self.read_audio(0.20)
        return _safe_rms(x)

    # ---------------------------------------------------------------------
    # Public: Audio I/O
    # ---------------------------------------------------------------------
    def read_audio(self, seconds: float) -> np.ndarray:
        """Liest bis zu 'seconds' Sekunden Mono-PCM float32 [-1,1]."""
        seconds = max(0.0, float(seconds))
        if seconds == 0.0:
            return np.zeros((0,), dtype=np.float32)
        # Hub-first
        if self.hub is not None:
            try:
                buf = self.hub.read_audio(seconds, client=self.client)
                return buf.astype(np.float32).reshape(-1)
            except Exception as e:
                LOG.warning("Hub.read_audio Fehler: %s (Fallback nutzen)", e)
                self.hub = None  # zukunft: Fallback
        # Fallback
        if self._stream is None:
            self._start_fallback_stream()
            time.sleep(max(0.0, self.block_ms / 1000.0))
        buf = self._fallback_concat()
        need = int(seconds * self.sr)
        if need <= 0 or buf.size == 0:
            return np.zeros((0,), dtype=np.float32)
        return buf[-need:] if buf.size >= need else buf.copy()

    def record_wav(self, seconds: float, sr: Optional[int] = None, gain_db: Optional[float] = None) -> bytes:
        sr = int(sr or self.sr)
        x = self.read_audio(seconds)
        if x.size == 0:
            return b""
        # Clip + PCM16
        y = np.clip(x, -1.0, 1.0)
        i16 = (y * 32767.0).astype(np.int16)
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(i16.tobytes())
        return bio.getvalue()

    def play_pcm(self, pcm: np.ndarray, sr: Optional[int] = None) -> bool:
        """Hub-first Playback; Fallback via sounddevice."""
        if pcm is None:
            return False
        sr = int(sr or self.sr)
        pcm = pcm.astype(np.float32).reshape(-1)
        # Hub-first
        if self.hub is not None:
            try:
                return bool(self.hub.play_pcm(pcm, sr=sr, client=self.client))
            except Exception as e:
                LOG.warning("Hub.play_pcm Fehler: %s (Fallback nutzen)", e)
                self.hub = None
        # Fallback
        if sd is None:
            LOG.warning("Kein Hub und kein sounddevice – Playback nicht möglich.")
            return False
        try:
            # einfache Ausgabe (blocking)
            dev_idx = self._pick_device_index(self.want_out_name, want_input=False)
            sd.play(pcm, samplerate=sr, device=dev_idx if dev_idx is not None else None, blocking=True)
            return True
        except Exception as e:
            LOG.error("Fallback-Playback-Fehler: %s", e)
            return False

    def play_wav(self, wav_bytes: bytes) -> bool:
        if wav_bytes is None or len(wav_bytes) == 0:
            return False
        if self.hub is not None:
            try:
                return bool(self.hub.play_wav(wav_bytes, client=self.client))
            except Exception as e:
                LOG.warning("Hub.play_wav Fehler: %s (Fallback nutzen)", e)
                self.hub = None
        # Fallback: dekodieren und via play_pcm
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                data = wf.readframes(wf.getnframes())
            if sw != 2:
                LOG.warning("WAV ist nicht PCM16 – Fallback-Annäherung.")
            if ch > 1:
                arr = np.frombuffer(data, dtype=np.int16).reshape(-1, ch)
                mono = arr.mean(axis=1).astype(np.int16)
            else:
                mono = np.frombuffer(data, dtype=np.int16)
            pcm = (mono.astype(np.float32) / 32767.0)
            return self.play_pcm(pcm, sr=sr)
        except Exception as e:
            LOG.error("play_wav Fehler: %s", e)
            return False

    # ---------------------------------------------------------------------
    # Public: ASR
    # ---------------------------------------------------------------------
    def _ensure_whisper(self):
        if not self._whisper_enable:
            return
        if self._whisper_model is None:
            try:
                self._whisper_model = whisper.load_model(self._whisper_model_name)  # type: ignore
                LOG.info("Whisper Modell '%s' geladen.", self._whisper_model_name)
            except Exception as e:
                LOG.error("Whisper konnte nicht geladen werden: %s", e)
                self._whisper_enable = False
                self._whisper_model = None

    def transcribe(self, audio: Union[np.ndarray, bytes]) -> Optional[str]:
        """
        ASR für ORÓMA – ohne venv möglich.

        Eingabe:
          • np.ndarray (float32, -1..1, mono) ODER
          • WAV-Bytes (PCM16 mono/mehrkanalig – wird zu mono gemittelt)

        Backends:
          • Python-Whisper (Torch) – wenn installiert
          • whisper.cpp CLI – wenn Binary + Model vorhanden

        Auswahl:
          OROMA_ASR_BACKEND=auto|whispercpp|whisper_py|disabled
        """
        backend = (getattr(self, "_asr_backend", "") or "auto").strip().lower()
        if backend in ("0", "false", "off", "disabled", "none"):
            return None

        want_py = backend in ("auto", "whisper_py", "py", "python")
        want_cpp = backend in ("auto", "whispercpp", "cpp", "whisper_cpp")

        py_ok = bool(getattr(self, "_whisper_enable", False))
        cpp_ok = bool(getattr(self, "_whispercpp_available", False))

        if backend in ("whisper_py", "py", "python") and not py_ok:
            return None
        if backend in ("whispercpp", "cpp", "whisper_cpp") and not cpp_ok:
            return None

        # Decode -> float32 mono + (stabil) auf 16k
        try:
            if isinstance(audio, (bytes, bytearray)):
                with wave.open(io.BytesIO(audio), "rb") as wf:
                    sr = int(wf.getframerate() or 16000)
                    ch = int(wf.getnchannels() or 1)
                    frames = wf.readframes(wf.getnframes())
                pcm16 = np.frombuffer(frames, dtype=np.int16)
                if ch > 1 and pcm16.size >= ch:
                    pcm16 = pcm16.reshape(-1, ch).mean(axis=1).astype(np.int16)
                x = (pcm16.astype(np.float32) / 32767.0)
                eff_sr = sr
            else:
                x = np.asarray(audio, dtype=np.float32).reshape(-1)
                eff_sr = int(getattr(self, "sr", 16000) or 16000)

            if eff_sr != 16000 and x.size > 0:
                if librosa is not None:
                    try:
                        x = librosa.resample(x, orig_sr=eff_sr, target_sr=16000).astype(np.float32)
                        eff_sr = 16000
                    except Exception as e:
                        log_suppressed(LOG, key="wrappers_audio_wrapper.pass.15", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                if eff_sr != 16000:
                    # Linear fallback resample
                    src_n = int(x.size)
                    dst_n = int(round(src_n * (16000.0 / float(eff_sr))))
                    if dst_n > 0 and src_n > 1:
                        xp = np.linspace(0.0, 1.0, num=src_n, dtype=np.float32)
                        xq = np.linspace(0.0, 1.0, num=dst_n, dtype=np.float32)
                        x = np.interp(xq, xp, x).astype(np.float32)
                        eff_sr = 16000

            # WAV PCM16 mono bytes für whisper.cpp
            bio = io.BytesIO()
            i16 = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
            with wave.open(bio, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(int(eff_sr))
                wf.writeframes(i16.tobytes())
            wav_bytes = bio.getvalue()

        except Exception as e:
            LOG.debug("transcribe(): decode/resample fehlgeschlagen: %s", e)
            return None

        txt: str = ""
        used_backend: str = "none"

        # 1) Python-Whisper bevorzugt (wenn erlaubt+verfügbar)
        if want_py and py_ok:
            try:
                self._ensure_whisper()
                if self._whisper_model is not None:
                    res = self._whisper_model.transcribe(
                        x,
                        language=(self._whisper_lang or None),
                        fp16=False,
                        verbose=False,
                    )
                    if isinstance(res, dict):
                        txt = str(res.get("text") or "").strip()
                        used_backend = "whisper_py"
            except Exception as e:
                LOG.debug("Python-Whisper transcribe Fehler: %s", e)
                txt = ""

        # 2) whisper.cpp fallback/forced
        if (not txt) and want_cpp and cpp_ok:
            try:
                txt = _whispercpp_transcribe_wav_bytes(
                    wav_bytes,
                    lang=(self._whisper_lang or ""),
                    cli_path=self._whispercpp_bin,
                    model_path=self._whispercpp_model,
                    threads=int(self._whispercpp_threads),
                    timeout_sec=int(self._whispercpp_timeout_sec),
                )
                txt = str(txt or "").strip()
                if txt:
                    used_backend = "whispercpp"
            except Exception as e:
                LOG.debug("whisper.cpp transcribe Fehler: %s", e)
                txt = ""

        if not txt:
            return None

        # Optional: Audio-Student Logging (Teacher)
        try:
            if _audio_student is not None and _env_bool("OROMA_AUDIO_STUDENT_ENABLED", True):
                feats = _features_from_signal(x, eff_sr)
                snap_vec = (feats.get("snap_feature") or []) if isinstance(feats, dict) else []
                source = f"{used_backend}:{self._whisper_model_name}"
                meta = {"lang": (self._whisper_lang or ""), "backend": used_backend}

                # Kompatibel: log_pair() (neu) oder insert_audio_pair() (alt)
                if hasattr(_audio_student, "log_pair"):
                    _audio_student.log_pair(
                        teacher_text=txt,
                        student_text=None,
                        feat_vector=snap_vec,
                        source=source,
                        meta=meta,
                    )
                elif hasattr(_audio_student, "insert_audio_pair"):
                    _audio_student.insert_audio_pair(
                        ts=int(time.time()),
                        source=source,
                        transcript_teacher=txt,
                        transcript_student=None,
                        distance=None,
                        feat={"snap_feature": snap_vec},
                        meta=meta,
                    )
        except Exception as e:
            LOG.debug("Audio-Student-Logging fehlgeschlagen: %s", e)

        return txt
# =============================================================================
# Kompakte ASR-Hilfsfunktion (für Skripte/CLI)
# =============================================================================

def asr_stream(language: str = "de", model_name: str = "small", duration: float = 5.0, gain_db: Optional[float] = None) -> Dict[str, Any]:
    """
    Nimmt 'duration' Sekunden Audio auf (Hub-first) und transkribiert mit Whisper.
    Rückgabe: {ok: bool, text: str|None, error?: str}

    Hinweis:
      • Das eigentliche Audio-Student-Logging passiert in AudioWrapper.transcribe().
    """
    try:
        aw = AudioWrapper(
            samplerate=_env_int("OROMA_AUDIO_SR", 16000),
            channels=1,
            use_hub=_env_bool("OROMA_AUDIO_WRAPPER_USE_HUB", True),
            enable_whisper=True,
            whisper_model=model_name,
            whisper_lang=language,
        )
        aw.start()
        time.sleep(0.2)  # kurzes Aufwärmen
        wav = aw.record_wav(duration, gain_db=gain_db)
        txt = aw.transcribe(wav) if wav else None
        aw.stop()
        if txt:
            return {"ok": True, "text": txt}
        return {"ok": False, "text": None, "error": "no_text"}
    except Exception as e:
        LOG.error("[ASR] Fehler: %s", e)
        return {"ok": False, "error": str(e)}


# =============================================================================
# Cleanup
# =============================================================================

def _atexit():
    # Nothing global to stop (Wrapper verwaltet sich selbst)
    pass

atexit.register(_atexit)


# =============================================================================
# Selbsttest
# =============================================================================

if __name__ == "__main__":
    logging.getLogger().setLevel("DEBUG")
    LOG.setLevel("DEBUG")
    print("=== ORÓMA Audio Wrapper Selftest ===")
    aw = AudioWrapper()
    aw.start()
    t_end = time.time() + 2.0
    got = 0
    while time.time() < t_end:
        f = aw.get_features(timeout=0.6)
        if f:
            got += 1
            print(f"[feat] rms={f['rms']:.3f} zcr={f['zcr']:.3f} pitch={f['pitch']:.1f} specμ={f['snap_feature'][3]:.3f}")
    print("Features erhalten:", got)
    wav = aw.record_wav(1.5)
    print("WAV-Bytes:", len(wav))
    # Playback (falls möglich)
    if len(wav) > 0:
        ok = aw.play_wav(wav)
        print("Playback ok:", ok)
    # Optional ASR-Kurztest (falls whisper installiert)
    if whisper is not None:
        res = asr_stream(duration=2.0)
        print("[ASR]", res)
    aw.stop()
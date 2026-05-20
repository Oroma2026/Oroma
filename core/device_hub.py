#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/device_hub.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
#            Offline-First · Headless · SQLite-First · Edge Runtime
# Modul:     DeviceHub – zentraler Geräte-/Sensor-Hub
#            (Camera + Light + Audio + Sessions + SensorChannels + Audit)
# Version:   v3.7.3
# Stand:     2026-04-18
# Patch:     PTZ-Reintegration nach OpenCV-MJPEG-Migration (Fail-Closed + Lazy-Init)
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
#
#
# ÜBERBLICK / ZWECK
# ─────────────────
# DeviceHub ist die „Single Source of Truth“ für alle echten IO-/Sensorzugriffe in ORÓMA.
# Er kapselt die physische Geräte-Nutzung so, dass:
#   - Kamera nicht mehrfach geöffnet wird (kritisch bei libcamera/Picamera2)
#   - Audio stabil bleibt (USB-Headsets, PortAudio, HostAPI-Auswahl, Re-Open Robustness)
#   - Multi-Client Zugriff nachvollziehbar ist (Sessions: wer nutzt gerade Kamera/Mic)
#   - UI (MJPEG/Snapshot, Audio-Test) ausschließlich über den Hub arbeitet (headless)
#   - Audit-Logs die Hardware-Nutzung erklärbar machen (rotierend, throttled)
#
# Der Hub ist bewusst „edge-safe“:
#   - Headless-only (kein Qt/Wayland/X11)
#   - Defensive optional Imports (cv2, picamera2, sounddevice): fehlende Libs → Dummy/No-Op statt Crash
#   - Kurze Locks: Lock schützt nur State, nicht lange IO
#
# SINGLETON-FASSADE (WICHTIG FÜR ORCHESTRATOR)
# ────────────────────────────────────────────
# DeviceHub wird als Singleton betrieben:
#   - DeviceHub.instance() ist threadsicher
#   - get_hub() liefert die Singleton-Instanz (preferred API)
# Motivation:
#   - Kamera/Mic dürfen pro Prozess nicht dupliziert werden
#   - UI + AgentLoop + Tools greifen auf denselben Zustand zu (latest frame, ringbuffer)
#
# KOMPONENTEN / SUBSYSTEME
# ────────────────────────
#
# (A) CAMERA (Capture + Latest Frame + Snapshot + MJPEG)
#   Intern existiert ein Backend-Abstraktionslayer:
#     - _BaseCam        (start/stop/read/running/id_string)
#     - _DummyCam       (synthetische Frames / fallback)
#     - _PiCamCam       (Picamera2/libcamera)
#     - _OpenCVCam      (cv2.VideoCapture)
#
#   Capture-Thread:
#     - start() startet optional einen Capture-Thread (_loop)
#     - _loop liest Frames, setzt _latest_frame + _latest_ts
#     - get_latest_frame(ensure_start=True) liefert (frame, ts)
#     - get_latest_jpeg() encodiert den latest frame via cv2.imencode(".jpg") (best effort)
#     - mjpeg_generator() streamt multipart/x-mixed-replace Frames (für Flask Video UI)
#
#   Kamera-Rotation (Software-Flip):
#     - Unterstützt nur 0 oder 180 Grad (90/270 wird bewusst NICHT unterstützt)
#     - akzeptiert mehrere historische ENV-Namen:
#         OROMA_VISION_ROTATE_DEG
#         OROMA_CAMERA_ROTATE_DEG
#         VISION_ROTATE_DEG
#         CAMERA_ROTATE_DEG
#         OROMA_VISION_ROTATE
#         OROMA_CAMERA_ROTATE
#
#   PRODUKTIONSFIX: Kamera Start-Fail Cooldown
#     - Bei Startfehlern (Device busy, libcamera fail, FD-Limit) würde ensure_start sonst
#       in kurzen Abständen erneut start() triggern.
#     - Daher: cooldown (OROMA_CAMERA_START_COOLDOWN_SEC, Default 15s) verhindert Logspam
#       und CPU/IO-Schleifen.
#
# (B) EXTERNAL FRAME INJECT (Provider/Push-Modus)
#   - submit_frame(frame, source="external") erlaubt externen Quellen (Replay, Remote-Cam,
#     PiCar, Testgenerator), Frames in den Hub zu pushen.
#   - Wenn externe Frames „frisch“ sind, liefert get_latest_frame() bevorzugt den
#     externen Frame (kein doppelter Kamera-Start im Hintergrund).
#   - Freshness Gate:
#       OROMA_EXTERNAL_FRAME_FRESH_SEC (Default: 8.0)
#
# (C) LIGHT (0..100) – aus Kamera-Luma (oder Dummy)
#   - get_light_level() liefert einen skalierten Wert 0..100
#   - Light-Quelle:
#       OROMA_LIGHT_SOURCE = "camera" | "dummy" | "off"
#   - Kamera-Licht-Abtastintervall:
#       OROMA_LIGHT_CAMERA_INTERVAL (Default: 300)  [Sekunden]
#   - Skalierung:
#       OROMA_LIGHT_MIN / OROMA_LIGHT_MAX definieren Clamps (Default 0/100)
#   - Audit-Modus für Light:
#       OROMA_LIGHT_AUDIT_MODE = "changes" | "all" | "off"
#
# (D) AUDIO (Mic Ringbuffer + Level + ReadWindow + WAV Record + Playback)
#   - Audio wird über PortAudio/sounddevice betrieben (optional import).
#   - start_mic()/stop_mic() sind session-aware und best-effort robust.
#   - Ringbuffer:
#       - float32 PCM, Kanalanzahl OROMA_AUDIO_CH
#       - Länge OROMA_AUDIO_RING_SEC
#       - Level-Berechnung (RMS) wird periodisch gedrosselt:
#           OROMA_AUDIO_LEVEL_INTERVAL (Default 0.15s)
#
#   Device-Auswahl:
#     - Input via Name-Substring:
#         OROMA_AUDIO_INPUT_NAME
#     - oder explizit via PortAudio-Index:
#         OROMA_AUDIO_INPUT_INDEX
#     - Output via Name-Substring:
#         OROMA_AUDIO_OUTPUT_NAME
#
#   HostAPI-Fix (reale Produktionsprobleme):
#     - Wenn PortAudio Geräte „verschluckt“ (z. B. nur JACK/Pulse sichtbar),
#       kann der Hub bevorzugt nach HostAPI filtern:
#         OROMA_AUDIO_HOSTAPI (Default: "ALSA")
#     - list_audio_devices() liefert strukturierte Infos:
#         {"input":[{...}], "output":[{...}]}
#       (UI nutzt das 1:1.)
#
#   Open-SampleRate Strategie (Robustness):
#     - start_mic() nutzt einen Modus-Schalter:
#         OROMA_AUDIO_OPEN_SR_MODE = "auto" | "fixed" | "probe"   (Default: auto)
#     - Overrides:
#         OROMA_AUDIO_OPEN_SR        (optional)
#         OROMA_AUDIO_OPEN_SR_PROBE  (optional; Probe-Liste/Strategie je nach Codepfad)
#     - Der Hub speichert Debug-Felder:
#         _mic_last_error, _mic_last_error_ts, _mic_open_sr
#
#   Audio-Read/Record:
#     - read_audio(seconds, client=...) liefert PCM aus dem Ringbuffer (concat)
#     - record_wav(seconds, sr=None, gain_db=..., client=...) gibt WAV-bytes zurück
#       Warmup (damit Ringbuffer nicht leer ist):
#         OROMA_AUDIO_RECORD_WARMUP_SEC (Default: 0.35s)
#       Gain:
#         gain_db Parameter ODER ENV OROMA_AUDIO_GAIN (Default 0.0 dB)
#
#   Playback (best effort, optional):
#     - play_pcm(pcm, sr=None, client=...) → bool
#     - play_wav(wav_bytes, client=...) → bool
#
# (E) SESSIONS (Multi-Client Nachvollziehbarkeit)
#   - open_session(client, kind) → session_id (uuid)
#   - close_session(session_id)
#   - status() liefert sessions als dict (copy) zurück
# Ziel:
#   - UI/AgentLoop/Tools können sauber erkennen, wer welche Ressource nutzt.
#
# (F) SENSOR CHANNELS (Plugin-System für zusätzliche Sensoren)
#   - register_sensor_channel(channel: BaseSensorChannel)
#   - list_sensor_channels() → {name:{...meta...}}
#   - start_sensors()/stop_sensors()
#   - get_sensor_health() → Zustandsübersicht (inkl. Errors, last_ts)
# Steuerung:
#   - OROMA_SENSORS_ENABLED (Default: 1)
#   - OROMA_SENSORS_SLEEP_BASE (Default: 0.05s)
#
# AUDIT-LOGGING (JSONL, rotierend, throttled)
# ──────────────────────────────────────────
# Der Hub kann Events als JSON Lines protokollieren (Start/Stop, Snapshot, Light, Audio):
#   - Enable:
#       OROMA_HUB_AUDIT_ENABLE (Default: true)
#   - Path:
#       OROMA_HUB_AUDIT_PATH   (Default: /opt/ai/oroma/log/devicehub_audit.log)
#   - Rotation:
#       OROMA_HUB_AUDIT_MAX_BYTES (Default: 1048576)
#       OROMA_HUB_AUDIT_BACKUPS   (Default: 5)
#   - Snapshot Throttle:
#       OROMA_HUB_AUDIT_SNAPSHOT_THROTTLE (Default: 3.0s)
#
# WICHTIGE ENV-VARIABLEN (AKTUELL IM CODE VERWENDET)
# ─────────────────────────────────────────────────
# Camera:
#   OROMA_VISION_BACKEND=picamera2|opencv|dummy   (Default: picamera2)
#   OROMA_VISION_DEVICE=0                         (Default: 0)
#   OROMA_VISION_W=640                            (Default: 640)
#   OROMA_VISION_H=360                            (Default: 360)
#   OROMA_VISION_FPS=30                           (Default: 30)
#   OROMA_CAMERA_START_COOLDOWN_SEC=15            (Default: 15.0)
# External:
#   OROMA_EXTERNAL_FRAME_FRESH_SEC=8.0            (Default: 8.0)
# Light:
#   OROMA_LIGHT_SOURCE=camera|dummy|off           (Default: camera)
#   OROMA_LIGHT_CAMERA_INTERVAL=300               (Default: 300)
#   OROMA_LIGHT_MIN=0                             (Default: 0)
#   OROMA_LIGHT_MAX=100                           (Default: 100)
#   OROMA_LIGHT_AUDIT_MODE=changes|all|off        (Default: changes)
# Audio:
#   OROMA_AUDIO_ENABLE=true|false                 (Default: true)
#   OROMA_AUDIO_INPUT_NAME=<substring>            (Default: "")
#   OROMA_AUDIO_INPUT_INDEX=<int>                 (Default: unset)
#   OROMA_AUDIO_OUTPUT_NAME=<substring>           (Default: "")
#   OROMA_AUDIO_HOSTAPI=ALSA|...                  (Default: ALSA)
#   OROMA_AUDIO_SR=16000                          (Default: 16000)
#   OROMA_AUDIO_CH=1                              (Default: 1)
#   OROMA_AUDIO_BLOCK_MS=20                       (Default: 20)
#   OROMA_AUDIO_RING_SEC=10                       (Default: 10)
#   OROMA_AUDIO_LEVEL_INTERVAL=0.15               (Default: 0.15)
#   OROMA_AUDIO_OPEN_SR_MODE=auto|fixed|probe     (Default: auto)
#   OROMA_AUDIO_OPEN_SR=<int>                     (Default: unset)
#   OROMA_AUDIO_OPEN_SR_PROBE=<string>            (Default: unset)
#   OROMA_AUDIO_RECORD_WARMUP_SEC=0.35            (Default: 0.35)
#   OROMA_AUDIO_GAIN=0.0                          (Default: 0.0)
# Sensors:
#   OROMA_SENSORS_ENABLED=1|0                     (Default: 1)
#   OROMA_SENSORS_SLEEP_BASE=0.05                 (Default: 0.05)
# Audit:
#   OROMA_HUB_AUDIT_ENABLE=true|false             (Default: true)
#   OROMA_HUB_AUDIT_PATH=/opt/ai/oroma/log/devicehub_audit.log
#   OROMA_HUB_AUDIT_MAX_BYTES=1048576
#   OROMA_HUB_AUDIT_BACKUPS=5
#   OROMA_HUB_AUDIT_SNAPSHOT_THROTTLE=3.0
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# Singleton:
#   hub = get_hub()
# Camera:
#   hub.start(), hub.stop()
#   frame, ts = hub.get_latest_frame(ensure_start=True)
#   jpg = hub.get_latest_jpeg(quality=85, client=None)
#   for chunk in hub.mjpeg_generator(boundary="frame", fps=..., quality=...): ...
#   hub.submit_frame(frame, source="external")
# Light:
#   level = hub.get_light_level()     # 0..100 oder None (bei off/kein frame)
# Audio:
#   hub.list_audio_devices() -> {"input":[...], "output":[...]}
#   hub.start_mic(client="...") -> bool
#   hub.stop_mic(client="...") -> None
#   hub.get_audio_level() -> float
#   pcm = hub.read_audio(seconds, client="...") -> np.ndarray
#   wav_bytes = hub.record_wav(seconds, sr=None, client="...", gain_db=None) -> bytes
#   hub.play_pcm(pcm, sr=None, client="...") -> bool
#   hub.play_wav(wav_bytes, client="...") -> bool
# Sessions:
#   sid = hub.open_session(client, kind)
#   hub.close_session(sid)
# Sensors:
#   hub.register_sensor_channel(channel)
#   hub.start_sensors(); hub.stop_sensors()
#   hub.get_sensor_health() -> dict
# Status:
#   st = hub.status() -> dict (inkl. audio debug + sessions + sensors)
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Muss headless bleiben (keine GUI-Abhängigkeiten).
# - Singleton muss erhalten bleiben (keine Doppel-Open der Devices).
# - External-Frame-Freshness Gate muss bleiben (sonst Doppel-Capture + Instabilität).
# - Kamera Start-Fail Cooldown muss bleiben (sonst CPU/Log-Schleifen).
# - Audio bleibt best effort (Device-Probleme dürfen AgentLoop/UI nicht killen).
# - Audit muss rotierend + throttled bleiben (sonst Log-Explosion).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import glob
import io
import json
import time
import uuid
import wave
import errno
import threading
import logging
from core.log_guard import log_suppressed
from logging.handlers import RotatingFileHandler
from collections import deque
from typing import Generator, List, Optional, Tuple, Dict, Any
from core.log_guard import log_suppressed
import logging
from pathlib import Path

from core.sensor_channel import BaseSensorChannel

# --- Optionales Audio-Backend (PortAudio via sounddevice) --------------------
try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover
    sd = None  # type: ignore

# --- Kamera-Dependencies -----------------------------------------------------
try:
    import cv2  # optional, nur für OpenCV-Backend / JPEG-Encode
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    from picamera2 import Picamera2  # type: ignore
except Exception:  # pragma: no cover
    Picamera2 = None  # type: ignore

import numpy as np  # type: ignore


# =============================================================================
# Logging (Console) + Audit (JSON Lines)
# =============================================================================

LOG = logging.getLogger("oroma.device_hub")
if not LOG.handlers and os.environ.get("OROMA_DEVICE_HUB_ATTACH_STDERR", "0").strip().lower() in ("1", "true", "yes", "on"):
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [DeviceHub] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.INFO)
LOG.propagate = True

_AUDIT_ENABLE = os.environ.get("OROMA_HUB_AUDIT_ENABLE", "true").strip().lower() in ("1", "true", "yes", "on")
_AUDIT_PATH = os.environ.get("OROMA_HUB_AUDIT_PATH", "/opt/ai/oroma/log/devicehub_audit.log")
_AUDIT_MAX = int(os.environ.get("OROMA_HUB_AUDIT_MAX_BYTES", "1048576"))
_AUDIT_BK = int(os.environ.get("OROMA_HUB_AUDIT_BACKUPS", "5"))
_AUDIT_SNAP_THR = float(os.environ.get("OROMA_HUB_AUDIT_SNAPSHOT_THROTTLE", "3.0"))  # Sek.


# Globaler Frame-Cache (prozessuebergreifend)
_FRAME_CACHE_STATE_DIR = Path(os.environ.get("OROMA_STATE_DIR", "/opt/ai/oroma/data/state"))
_FRAME_CACHE_JPG_PATH = _FRAME_CACHE_STATE_DIR / "latest_frame_cache.jpg"
_FRAME_CACHE_META_PATH = _FRAME_CACHE_STATE_DIR / "latest_frame_cache.json"
_FRAME_CACHE_MIN_INTERVAL_SEC = max(0.0, float(os.environ.get("OROMA_GLOBAL_FRAME_CACHE_MIN_INTERVAL_SEC", "0.5") or "0.5"))
_FRAME_CACHE_JPEG_QUALITY = int(os.environ.get("OROMA_GLOBAL_FRAME_CACHE_JPEG_QUALITY", "70") or "70")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        return
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def _setup_audit_logger() -> logging.Logger:
    lg = logging.getLogger("oroma.device_hub.audit")
    if lg.handlers:
        return lg
    if not _AUDIT_ENABLE:
        lg.propagate = False
        lg.disabled = True
        return lg
    try:
        _ensure_parent_dir(_AUDIT_PATH)
        rh = RotatingFileHandler(_AUDIT_PATH, maxBytes=_AUDIT_MAX, backupCount=_AUDIT_BK, encoding="utf-8")
        rh.setLevel(logging.INFO)
        rh.setFormatter(logging.Formatter("%(message)s"))  # reine JSON-Zeilen
        lg.addHandler(rh)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        LOG.debug("Audit-Logging aktiv: %s (max=%d, backups=%d)", _AUDIT_PATH, _AUDIT_MAX, _AUDIT_BK)
    except Exception as e:
        LOG.warning("Audit-Logger konnte nicht eingerichtet werden: %s", e)
        lg.disabled = True
    return lg


_AUDIT = _setup_audit_logger()

_audit_last: Dict[str, float] = {}
_audit_lock = threading.Lock()


def _audit(kind: str, action: str, **fields: Any) -> None:
    if not _AUDIT_ENABLE or _AUDIT.disabled:
        return
    try:
        evt = {"ts": time.time(), "kind": kind, "action": action}
        evt.update(fields)
        _AUDIT.info(json.dumps(evt, ensure_ascii=False))
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.device_hub.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )


def _audit_throttled(key: str, min_interval: float, kind: str, action: str, **fields: Any) -> None:
    if not _AUDIT_ENABLE or _AUDIT.disabled:
        return
    now = time.time()
    with _audit_lock:
        last = _audit_last.get(key, 0.0)
        if now - last < min_interval:
            return
        _audit_last[key] = now
    _audit(kind, action, **fields)


# =============================================================================
# Kamera-Backends
# =============================================================================
class _BaseCam:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read(self) -> Optional[np.ndarray]: ...
    def running(self) -> bool: return False
    def id_string(self) -> str: return "unknown"


class _DummyCam(_BaseCam):
    def __init__(self, w: int, h: int) -> None:
        self._w, self._h = w, h
        self._run = False

    def start(self) -> None:
        self._run = True
        LOG.warning("DummyCam aktiv – es werden Platzhalter-Frames geliefert.")
        _audit("camera", "start", backend="dummy", device=None, size=[self._w, self._h])

    def stop(self) -> None:
        self._run = False
        _audit("camera", "stop", backend="dummy")

    def read(self) -> Optional[np.ndarray]:
        if not self._run:
            return None
        img = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        t = int(time.time() % 255)
        img[:] = (t, t, t)
        return img

    def running(self) -> bool:
        return self._run

    def id_string(self) -> str:
        return f"dummy({self._w}x{self._h})"


class _PiCamera2Cam(_BaseCam):
    def __init__(self, w: int, h: int, fps: int) -> None:
        self._w, self._h, self._fps = w, h, fps
        self._run = False
        self._cam = None

    def _safe_close_cam(self, cam: Any) -> None:
        """
        -----------------------------------------------------------------------------
        PRODUKTIONSFIX – Ressourcen/FD-Leak Schutz (Picamera2)
        -----------------------------------------------------------------------------
        Problem:
          - Bei Exceptions während configure()/start() bleibt ein teilweise
            initialisiertes Picamera2-Objekt übrig.
          - stop() ohne close() kann je nach Stack/Version File-Deskriptoren
            offen lassen.
          - Im Betrieb kann das zu Errno 24 (Too many open files) führen und
            danach auch SQLite-Open fehlschlagen lassen ("unable to open database file").

        Lösung:
          - Best-effort Cleanup: stop() + close() jeweils in try/except
          - Cleanup darf NIE nach außen werfen (sonst verschlimmert es den Fehler)
        -----------------------------------------------------------------------------
        """
        if cam is None:
            return
        try:
            cam.stop()
        except Exception as e:
            log_suppressed(LOG, key="device_hub.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        try:
            # Picamera2 bietet close(); bei teilinitialisierten Instanzen kann
            # close() ebenfalls werfen – daher best-effort.
            cam.close()
        except Exception as e:
            log_suppressed(LOG, key="device_hub.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    def start(self) -> None:
        if Picamera2 is None:
            raise RuntimeError("PiCamera2-Modul nicht verfügbar")
        if self._run:
            return

        cam = None
        try:
            cam = Picamera2()
            cfg = cam.create_preview_configuration(main={"size": (self._w, self._h), "format": "BGR888"})
            cam.configure(cfg)
            cam.start()

            # Erst nach erfolgreichem Start übernehmen.
            self._cam = cam
            self._run = True

            LOG.info("PiCamera2 gestartet (%dx%d @ ~%dfps)", self._w, self._h, self._fps)
            _audit("camera", "start", backend="picamera2", device="picamera2", size=[self._w, self._h], fps=self._fps)

        except Exception:
            # Wichtig: Cleanup, sonst FD-Leak bei wiederholten Startversuchen.
            self._safe_close_cam(cam)
            self._cam = None
            self._run = False
            raise

    def stop(self) -> None:
        cam = self._cam
        self._cam = None
        self._run = False

        # Best-effort close
        self._safe_close_cam(cam)
        _audit("camera", "stop", backend="picamera2")

    def read(self) -> Optional[np.ndarray]:
        if not self._run or not self._cam:
            return None
        try:
            return self._cam.capture_array()  # type: ignore
        except Exception as e:
            LOG.error("PiCamera2 read()-Fehler: %s", e)
            _audit("camera", "error", backend="picamera2", error=str(e))
            return None

    def running(self) -> bool:
        return self._run

    def id_string(self) -> str:
        return f"picamera2({self._w}x{self._h}@{self._fps})"


class _OpenCVCam(_BaseCam):
    def __init__(self, dev: Any, w: int, h: int, fps: int) -> None:
        self._dev, self._w, self._h, self._fps = dev, w, h, fps
        self._fourcc = ''
        self._buffersize = 0
        self._cap = None
        self._run = False

    def start(self) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV nicht verfügbar")
        if self._run:
            return

        # ---------------------------------------------------------------------
        # PRODUKTIONSFIX – OpenCV Capture robust öffnen (CAP_V4L2 + Device-Pfad)
        # ---------------------------------------------------------------------
        # Hintergrund:
        #   In manchen OpenCV-Builds (z. B. mit obsensor/Depth-Kamera Support)
        #   kann cv2.VideoCapture(<int>) in einen falschen Backend-Pfad laufen
        #   („Camera index out of range“) oder zunächst GStreamer wählen.
        #   Für UVC/V4L2 Geräte ist ein explizites CAP_V4L2 (sofern verfügbar)
        #   und/oder ein Device-Pfad (/dev/videoX) die robusteste Variante.
        #
        # Lösung:
        #   - Wenn dev als int übergeben wurde, bevorzugen wir /dev/video<id>,
        #     sofern der Pfad existiert.
        #   - Wir erzwingen CAP_V4L2, wenn OpenCV das anbietet.
        #   - Fallback: ohne apiPreference.
        #
        # Hinweis:
        #   Dieses Verhalten ist bewusst best-effort und darf den Dienst nicht
        #   in Start-Loop/Crash-Spam treiben.
        # ---------------------------------------------------------------------
        dev = self._dev
        try:
            if isinstance(dev, int):
                dev_path = f"/dev/video{dev}"
                if os.path.exists(dev_path):
                    dev = dev_path
        except Exception:
            pass

        cap = None
        api = getattr(cv2, "CAP_V4L2", None)
        if api is not None:
            try:
                cap = cv2.VideoCapture(dev, api)
            except Exception:
                cap = None
        if cap is None:
            cap = cv2.VideoCapture(dev)
        if cap is not None and not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            cap = cv2.VideoCapture(dev)

        # ---------------------------------------------------------------------
        # PRODUKTIONSFIX – OpenCV FOURCC / MJPEG erzwingen (USB-PTZ-Kameras)
        # ---------------------------------------------------------------------
        # Hintergrund:
        #   Viele UVC-Kameras (inkl. PTZ-Webcams) liefern bei OpenCV/V4L2 ohne
        #   expliziten FOURCC oft YUYV/UYVY. Das ist auf dem Pi CPU-teuer und
        #   reduziert die erreichbare FPS/Resolution deutlich.
        #
        # Lösung:
        #   Wenn ein FOURCC gesetzt ist, versuchen wir MJPEG (z. B. 'MJPG')
        #   *best effort* zu aktivieren.
        #
        # ENV:
        #   OROMA_VISION_FOURCC=MJPG      (empfohlen für UVC/MJPEG)
        #   OROMA_OPENCV_FOURCC=MJPG      (Alias)
        #   OROMA_VISION_BUFFERSIZE=2     (optional; reduziert Latenz)
        # ---------------------------------------------------------------------
        _fourcc = (os.environ.get('OROMA_VISION_FOURCC') or os.environ.get('OROMA_OPENCV_FOURCC') or '').strip()
        if _fourcc:
            _fourcc = _fourcc[:4]
        self._fourcc = _fourcc
        try:
            _buf_raw = (os.environ.get('OROMA_VISION_BUFFERSIZE') or '').strip()
            self._buffersize = int(_buf_raw) if _buf_raw else 0
        except Exception:
            self._buffersize = 0

        # FOURCC möglichst früh setzen
        if self._fourcc and len(self._fourcc) == 4:
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._fourcc))
            except Exception:
                pass

        # Buffer-Limit (best effort; nicht alle Backends unterstützen das)
        if getattr(self, '_buffersize', 0) > 0:
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, int(self._buffersize))
            except Exception:
                pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._h)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        if not cap.isOpened():
            _audit("camera", "error", backend="opencv", device=self._dev, error="open_failed")
            raise RuntimeError(f"OpenCV-Kamera {self._dev} konnte nicht geöffnet werden")
        self._cap = cap
        self._run = True
        LOG.info("OpenCV-Kamera gestartet (dev=%s, %dx%d@~%dfps)", str(self._dev), self._w, self._h, self._fps)
        _audit("camera", "start", backend="opencv", device=self._dev, size=[self._w, self._h], fps=self._fps)

    def stop(self) -> None:
        if self._cap:
            try:
                self._cap.release()
            except Exception as e:
                log_suppressed(LOG, key="device_hub.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        self._cap = None
        self._run = False
        _audit("camera", "stop", backend="opencv", device=self._dev)

    def read(self) -> Optional[np.ndarray]:
        if not self._run or not self._cap:
            return None
        ok, frame = self._cap.read()
        if not ok:
            _audit_throttled("opencv_read_fail", 5.0, "camera", "read_fail", backend="opencv", device=self._dev)
            return None
        return frame

    def running(self) -> bool:
        return self._run

    def id_string(self) -> str:
        return f"opencv(dev={self._dev},{self._w}x{self._h}@{self._fps}{','+self._fourcc if getattr(self,'_fourcc','') else ''})"


# =============================================================================
# DeviceHub (Singleton) – Kamera + Light + Audio (+ Sessions)
# =============================================================================
class DeviceHub:
    """Zentrale thread-sichere Geräteverwaltung (Kamera, Light, Audio) mit Audit-Logging und Sessions."""

    _inst: Optional["DeviceHub"] = None
    _inst_lock = threading.Lock()

    # ----- Singleton Zugriff -----
    @classmethod
    def instance(cls) -> "DeviceHub":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = DeviceHub()
            return cls._inst

    # ----- Init -----
    def __init__(self) -> None:
        # Kamera/Light-Konfiguration
        self.backend = os.environ.get("OROMA_VISION_BACKEND", "picamera2").lower()
        _raw_dev = (os.environ.get("OROMA_VISION_DEVICE", "0") or "0").strip()
        # ------------------------------------------------------------------
        # AUTO-SWITCH: Wenn kein explizites OROMA_VISION_BACKEND gesetzt ist,
        # und wir sonst auf "picamera2" (IMX219) defaulten würden, bevorzugen
        # wir automatisch eine vorhandene USB-PTZ/UVC-Kamera als Vision-Quelle.
        #
        # Motivation (praktisch):
        #   - ptz_attention_loop läuft häufig als Subprozess des Orchestrators
        #     und erbt NICHT zwangsläufig das Environment aus oroma.service.
        #   - In diesem Fall würde der Hub sonst PiCamera2 starten (schlechte
        #     Qualität / falsche Kamera), obwohl die USB-PTZ-Kamera verfügbar ist.
        #
        # Regel:
        #   - Nur wenn OROMA_VISION_BACKEND NICHT gesetzt ist.
        #   - Wir nutzen zuerst OROMA_PTZ_DEVICE, andernfalls Auto-Detect.
        #   - Ein explizit gesetztes OROMA_VISION_DEVICE wird respektiert.
        # ------------------------------------------------------------------
        _vision_backend_env_set = ("OROMA_VISION_BACKEND" in os.environ)
        _vision_device_env_set = ("OROMA_VISION_DEVICE" in os.environ)

        if (not _vision_backend_env_set) and (self.backend == "picamera2"):
            _ptz_dev = os.environ.get("OROMA_PTZ_DEVICE", "").strip()
            if not _ptz_dev:
                try:
                    _ptz_dev = _auto_detect_ptz_device() or ""
                except Exception:
                    _ptz_dev = ""

            if _ptz_dev:
                self.backend = "opencv"
                if (not _vision_device_env_set):
                    # Falls _raw_dev leer/Default ist, auf PTZ-Gerät umbiegen.
                    if (not _raw_dev) or (_raw_dev == "0"):
                        _raw_dev = _ptz_dev

                LOG.info(
                    "[DeviceHub] Vision backend auto-switch: backend=%s dev=%s "
                    "(reason: PTZ device present, OROMA_VISION_BACKEND unset)",
                    self.backend,
                    _raw_dev or _ptz_dev,
                )

        # ------------------------------------------------------------------
        # PRODUKTIONSFIX – Reboot-sichere Device-Pfade unterstützen
        # ------------------------------------------------------------------
        # Hintergrund:
        #   /dev/videoX ist nach Reboots nicht stabil (USB/CSI Enumerations-
        #   Reihenfolge). Udev bietet stabile Symlinks unter /dev/v4l/by-id
        #   und /dev/v4l/by-path.
        #
        # Erwartung:
        #   OROMA_VISION_DEVICE kann sein:
        #     • Integer ("0", "8")
        #     • Device-Pfad ("/dev/video0")
        #     • Stabiler Symlink ("/dev/v4l/by-id/usb-...-video-index0")
        #
        # Umsetzung:
        #   - Jeder absolute /dev/* Pfad wird als String akzeptiert.
        #   - Sonst versuchen wir int() (wie bisher).
        # ------------------------------------------------------------------
        if _raw_dev.startswith("/dev/"):
            self.dev_id = _raw_dev
        else:
            try:
                self.dev_id = int(_raw_dev)
            except Exception:
                self.dev_id = 0
        self.w = int(os.environ.get("OROMA_VISION_W", "640"))
        self.h = int(os.environ.get("OROMA_VISION_H", "360"))
        self.fps = int(os.environ.get("OROMA_VISION_FPS", "30"))
        self._frame_cache_last_publish_ts = 0.0

        # Optional: Kamera-Rotation (Software-Flip)
        #   - 0   = keine
        #   - 180 = upside-down fix (Flip X+Y)
        # Kompatibel zu mehreren ENV-Namen (weil Configs historisch unterschiedlich waren).
        _rot_raw = (
            os.environ.get("OROMA_VISION_ROTATE_DEG")
            or os.environ.get("OROMA_CAMERA_ROTATE_DEG")
            or os.environ.get("VISION_ROTATE_DEG")
            or os.environ.get("CAMERA_ROTATE_DEG")
            or os.environ.get("OROMA_VISION_ROTATE")
            or os.environ.get("OROMA_CAMERA_ROTATE")
            or "0"
        )
        try:
            _rot = int(str(_rot_raw).strip())
        except Exception:
            _rot = 180 if str(_rot_raw).strip().lower() in ("1", "true", "yes", "on") else 0

        # erlaubte Werte: 0 oder 180 (90/270 würden W/H tauschen → unerwartete Effekte)
        if _rot in (1, -1):
            _rot = 180
        if _rot not in (0, 180):
            LOG.warning("[DeviceHub] OROMA_*_ROTATE_DEG=%r nicht unterstützt (nur 0/180) → ignoriere", _rot_raw)
            _rot = 0
        self.rotate_deg = _rot
        if self.rotate_deg:
            LOG.info("[DeviceHub] Kamera-Rotation aktiv: rotate_deg=%s (Software-Flip)", self.rotate_deg)

        self.light_source = os.environ.get("OROMA_LIGHT_SOURCE", "camera").lower()
        self.light_interval = int(os.environ.get("OROMA_LIGHT_CAMERA_INTERVAL", "300"))
        self.light_min = float(os.environ.get("OROMA_LIGHT_MIN", "0"))
        self.light_max = float(os.environ.get("OROMA_LIGHT_MAX", "100"))
        self.light_audit_mode = os.environ.get("OROMA_LIGHT_AUDIT_MODE", "changes").strip().lower()

        # Audio-Konfiguration
        self.audio_enable = os.environ.get("OROMA_AUDIO_ENABLE", "true").lower() in ("1", "true", "yes", "on")
        self.audio_in_name = os.environ.get("OROMA_AUDIO_INPUT_NAME", "").strip()
        # Optional: expliziter PortAudio-Device-Index (fixiert "default=-1" Probleme)
        _in_idx_raw = os.environ.get("OROMA_AUDIO_INPUT_INDEX", "").strip()
        self.audio_in_index: Optional[int] = None
        if _in_idx_raw != "":
            try:
                self.audio_in_index = int(_in_idx_raw)
            except Exception:
                self.audio_in_index = None

        self.audio_out_name = os.environ.get("OROMA_AUDIO_OUTPUT_NAME", "").strip()
        self.audio_sr = int(os.environ.get("OROMA_AUDIO_SR", "16000"))
        self.audio_ch = int(os.environ.get("OROMA_AUDIO_CH", "1"))
        self.audio_block_ms = int(os.environ.get("OROMA_AUDIO_BLOCK_MS", "20"))
        self.audio_ring_sec = int(os.environ.get("OROMA_AUDIO_RING_SEC", "10"))
        self.audio_lvl_iv = float(os.environ.get("OROMA_AUDIO_LEVEL_INTERVAL", "0.15"))

        # --- Audio Diagnostics / Robustness ---
        # Letzte Fehlertexte (UI Debug)
        self._mic_last_error: str = ""
        self._mic_last_error_ts: float = 0.0
        self._mic_open_sr: Optional[int] = None
        self._audio_devices_last_error: str = ""



        # ---------------------------------------------------------------------
        # PTZ (USB/UVC) – optionaler Controller (V4L2 Controls via v4l2-ctl)
        # ---------------------------------------------------------------------
        # Hintergrund:
        #   ORÓMA kann (optional) PTZ-Kameras (z. B. EMEET PIXY) steuern, sofern
        #   das Device V4L2 PTZ Controls exportiert (pan_absolute/tilt_absolute/
        #   zoom_absolute). Die Steuerung ist bewusst 'best effort' und darf den
        #   DeviceHub niemals destabilisieren.
        #
        # Design:
        #   - Lazy-Init: PTZ wird erst initialisiert, wenn Status/Move angefragt
        #     wird (kein zusätzlicher Start-Overhead).
        #   - Fail-Closed: ohne OROMA_PTZ_DEVICE oder ohne v4l2-ctl -> disabled.
        #   - Rate-Limit: wrappers/ptz_controller.py erzwingt Cooldown.
        #
        # ENV:
        #   OROMA_PTZ_DEVICE=/dev/video8
        #   OROMA_PTZ_COOLDOWN_MS=250
        # ---------------------------------------------------------------------
        self._ptz_device: str = os.environ.get("OROMA_PTZ_DEVICE", "").strip()
        self._ptz_lock = threading.Lock()
        self._ptz = None  # lazy: wrappers.ptz_controller.PTZController
        # Wenn der PTZController während USB-Reconnects/Stream-Open einen transienten
        # Zustand meldet (z. B. "v4l2-ctl list-ctrls empty output"), wollen wir nicht
        # bis zum nächsten Service-Restart stuck bleiben. Deshalb erlauben wir eine
        # sparsame Re-Init-Strategie.
        self._ptz_last_init_ts: float = 0.0
        # --- Kamera State ---
        self._cam: _BaseCam = self._build_cam()
        self._cap_thread: Optional[threading.Thread] = None
        self._cap_run = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_ts: float = 0.0

        # =====================================================================
        # CAMERA: STALL / READ_FAIL DIAGNOSTICS + SELF-HEAL (Watchdog State)
        # ---------------------------------------------------------------------
        # Hintergrund:
        #   In der Praxis kann OpenCV/V4L2 in Zustände geraten, in denen:
        #     • die Kamera als „running“ gilt (cap.isOpened() True)
        #     • der Capture-Thread weiterläuft
        #     • aber cap.read() wiederholt ok=False liefert (read_fail)
        #   Ergebnis:
        #     • _latest_frame bleibt None oder friert ein
        #     • UI zeigt „Kein Bild verfügbar (Hub liefert None)“
        #
        # Ziel:
        #   • Diagnose: Fail-Streak, letztes OK-Frame, letzter Restart, last_error
        #   • Self-Heal: best-effort Soft-Restart (stop/start) wenn Stall erkannt
        #
        # ENV:
        #   OROMA_CAMERA_STALL_RESTART_SEC        (Default: 12)
        #       Wenn seit letztem OK-Frame länger als diese Sekunden kein Frame
        #       kam, darf ein Restart versucht werden.
        #   OROMA_CAMERA_RESTART_MIN_INTERVAL_SEC (Default: 30)
        #       Mindestabstand zwischen Restart-Versuchen, um Spam zu vermeiden.
        #   OROMA_CAMERA_FAIL_STREAK_RESTART      (Default: 10)
        #       Minimale Anzahl aufeinanderfolgender read_fail bevor Restart
        #       *überhaupt* erwogen wird (verhindert Restarts bei kurzen Glitches).
        #
        # Regeln:
        #   • Nie restarten, wenn External-Mode aktiv ist (Frames kommen extern).
        #   • Restarts sind throttled und werden via Audit + Log sichtbar.
        # =====================================================================
        self._cam_last_ok_ts: float = 0.0
        self._cam_fail_streak: int = 0
        self._cam_restart_ts: float = 0.0
        self._cam_restart_count: int = 0
        self._cam_last_error: str = ""
        self._cam_last_error_ts: float = 0.0
        # =====================================================================
        # END CAMERA WATCHDOG STATE
        # =====================================================================


        # =====================================================================
        # CAPTURE THREAD WATCHDOG STATE
        # ---------------------------------------------------------------------
        # Zweck:
        #   Zusätzlich zur "read_fail" Stall-Detektion im Capture-Loop (siehe oben)
        #   brauchen wir einen Schutz, wenn der Capture-Thread selbst "stirbt"
        #   (Exception, OOM, Thread beendet) oder "hängt" (Loop-Heartbeat stagniert).
        #
        # Design:
        #   • Headless, keine externen Abhängigkeiten
        #   • Best-effort Soft-Recover (stop/start) mit throttling
        #   • Nie restarten, wenn External-Mode aktiv ist (Frames kommen extern)
        #
        # ENV:
        #   OROMA_DEVICEHUB_WATCHDOG=1|0                 (Default: 1)
        #   OROMA_DEVICEHUB_WATCHDOG_INTERVAL_SEC=5      (Default: 5)
        #   OROMA_DEVICEHUB_WATCHDOG_HUNG_SEC=20         (Default: 20)
        #   OROMA_DEVICEHUB_WATCHDOG_MIN_RESTART_SEC=45  (Default: 45)
        # =====================================================================
        self._cap_heartbeat_ts: float = 0.0
        self._cap_watchdog_run: bool = False
        self._cap_watchdog_thread: Optional[threading.Thread] = None
        self._cap_watchdog_restart_ts: float = 0.0
        self._cap_watchdog_restart_count: int = 0
        # =====================================================================
        # END CAPTURE THREAD WATCHDOG STATE
        # =====================================================================

        # ---------------------------------------------------------------------
        # PRODUKTIONSFIX – Kamera Start-Fail Cooldown
        # ---------------------------------------------------------------------
        # Hintergrund:
        #   Wenn die Kamera nicht starten kann (z. B. FD-Limit, libcamera-
        #   Problem, Device busy), wird start() ggf. in kurzen Intervallen
        #   erneut aufgerufen (über ensure_start=True). Das erzeugt:
        #     • Log-Spam / Audit-Spam
        #     • zusätzlichen Ressourcen-Druck
        #     • im Worst-Case: Eskalation bis Errno 24 (Too many open files)
        #
        # Lösung:
        #   Nach einem Start-Fehler warten wir eine kurze Cooldown-Zeit,
        #   bevor der nächste Startversuch erlaubt ist.
        #
        # ENV:
        #   OROMA_CAMERA_START_COOLDOWN_SEC (Default: 15)
        # ---------------------------------------------------------------------
        self._cam_start_fail_ts: float = 0.0
        self._cam_start_cooldown_sec: float = float(os.environ.get("OROMA_CAMERA_START_COOLDOWN_SEC", "15"))

        # =====================================================================
        # BLOCK: EXTERNAL_FRAME_STATE (SWAPPABLE)
        # Zweck:
        #   Externe Kamera-Provider (z. B. PiCar / Remote / Provider-Thread)
        #   können Frames in den DeviceHub pushen, ohne ein weiteres Backend
        #   oder einen zweiten Capture-Thread zu starten.
        #
        # Hinweise:
        #   - Diese Felder sind rein optional (Debug/Status/Audit).
        #   - Sie verändern die normale Kamera-Logik NICHT.
        # =====================================================================
        self._external_source: Optional[str] = None
        self._external_ts: float = 0.0
        self._external_frames: int = 0
        #
        # PRODUKTIONSFIX – External-Mode TTL / Internal-Start-Guard
        # ---------------------------------------------------------
        # Wenn Frames extern eingespeist werden (camera_hub/PiCar/Remote),
        # darf DeviceHub die interne Kamera (Picamera2) nicht parallel starten.
        # Das führt sonst zu libcamera-Konflikten (z.B. "Camera __init__")
        #
        # Wir betrachten External-Mode als 'aktiv', solange innerhalb eines
        # TTL-Fensters externe Frames eintreffen.
        #
        # ENV:
        #   OROMA_EXTERNAL_MODE_TTL_SEC (Default: 60)
        #   OROMA_ALLOW_INTERNAL_CAMERA_WITH_EXTERNAL=1  -> Guard deaktivieren
        #
        try:
            self._external_mode_ttl_sec: float = float(os.environ.get('OROMA_EXTERNAL_MODE_TTL_SEC', '60'))
        except Exception:
            self._external_mode_ttl_sec = 60.0
        self._allow_internal_with_external: bool = os.environ.get('OROMA_ALLOW_INTERNAL_CAMERA_WITH_EXTERNAL','0').strip().lower() in ('1','true','yes','on')
        # =====================================================================
        # END BLOCK: EXTERNAL_FRAME_STATE
        # =====================================================================

        # --- Light Cache ---
        self._light_lock = threading.Lock()
        self._light_val: Optional[float] = None
        self._light_ts: float = 0.0
        self._light_state: Optional[str] = None  # "DARK"|"BRIGHT"|None
        # Hysterese-Grenzen (in 0..100)
        self._dark_thr = 30.0
        self._bright_thr = 40.0

        # --- Audio State ---
        self._audio_lock = threading.Lock()
        self._mic_stream = None
        self._out_stream = None
        blocks_per_ring = max(
            1,
            int(self.audio_sr * self.audio_ring_sec) // max(1, int(self.audio_sr * self.audio_block_ms / 1000)),
        )
        self._ring = deque(maxlen=blocks_per_ring)
        self._ring_np_cache: Optional[np.ndarray] = None  # lazy concat cache
        self._lvl_ts = 0.0
        self._lvl_val = 0.0  # 0..1 RMS
        self._in_dev_idx: Optional[int] = None
        self._out_dev_idx: Optional[int] = None
        self._playback_blocked_until_ts: float = 0.0

        # --- Sessions ---
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._sess_lock = threading.Lock()

        # --- Sensor State (optional, generisch) --------------------------------
        # Sensoren (IR, Ultraschall, IMU, Temperatur, ...) werden als Channels
        # beim DeviceHub registriert. Der Hub kann diese Channels in einem
        # separaten Thread pollen und die Werte als SnapChains in die DB schreiben.
        self._sensor_channels: Dict[str, BaseSensorChannel] = {}
        self._sensor_lock = threading.Lock()
        self._sensor_thread: Optional[threading.Thread] = None
        self._sensor_run: bool = False

        # Steuerung über ENV (Standard: Sensoren sind erlaubt, aber nicht aktiv,
        # solange keine Channels registriert werden und start_sensors() nicht
        # explizit aufgerufen wird).
        self.sensors_enabled = os.environ.get("OROMA_SENSORS_ENABLED", "1").lower() not in ("0", "false", "no", "off")
        self.sensors_sleep_base = float(os.environ.get("OROMA_SENSORS_SLEEP_BASE", "0.05"))

        LOG.info(
            "DeviceHub init: backend=%s, dev=%s, %dx%d@%dfps, light=%s/%ss, audio=%s (sr=%d, ch=%d, block=%sms, ring=%ss)",
            self.backend,
            self.dev_id,
            self.w,
            self.h,
            self.fps,
            self.light_source,
            self.light_interval,
            "on" if (self.audio_enable and sd is not None) else "off",
            self.audio_sr,
            self.audio_ch,
            self.audio_block_ms,
            self.audio_ring_sec,
        )
        _audit(
            "hub",
            "init",
            vision_backend=self.backend,
            dev=self.dev_id,
            size=[self.w, self.h],
            fps=self.fps,
            light=self.light_source,
            audio_enabled=bool(self.audio_enable and sd is not None),
        )

    # -------------------------------------------------------------------------
    # Sensor-Integration (generische Channels)
    # -------------------------------------------------------------------------

    def register_sensor_channel(self, channel: BaseSensorChannel) -> None:
        """
        Registriert einen SensorChannel im DeviceHub.

        • überschreibt ggf. einen bestehenden Channel mit gleichem Namen
        • startet NICHT automatisch die Poll-Schleife
        """
        with self._sensor_lock:
            self._sensor_channels[channel.name] = channel
        LOG.info(
            "SensorChannel registriert: name=%s kind=%s origin=%s interval=%.3fs",
            channel.name,
            channel.kind,
            channel.origin,
            channel.interval_sec,
        )

    def list_sensor_channels(self) -> Dict[str, Dict[str, Any]]:
        """
        Liefert eine Übersicht der registrierten Sensoren.
        """
        with self._sensor_lock:
            out: Dict[str, Dict[str, Any]] = {}
            for name, ch in self._sensor_channels.items():
                out[name] = {
                    "kind": ch.kind,
                    "origin": ch.origin,
                    "namespace": ch.namespace,
                    "interval_sec": ch.interval_sec,
                }
            return out

    def _sensor_loop(self) -> None:
        """
        Interner Polling-Loop für alle registrierten SensorChannels.

        • nutzt BaseSensorChannel.due()/read_raw()/build_snapchain_data()
        • schreibt in snapchains über sql_manager.insert_snapchain()
        """
        from core import sql_manager  # Lazy-Import, um Zyklen zu vermeiden

        LOG.info(
            "DeviceHub Sensor-Loop gestartet (enabled=%s, channels=%d).",
            self.sensors_enabled,
            len(self.list_sensor_channels()),
        )
        try:
            while self._sensor_run:
                if not self.sensors_enabled:
                    time.sleep(self.sensors_sleep_base)
                    continue

                now = time.time()
                wrote = 0

                with self._sensor_lock:
                    channels = list(self._sensor_channels.values())

                for ch in channels:
                    if not ch.due(now):
                        continue

                    try:
                        # Rohdaten vom Sensor holen
                        raw = ch.read_raw()
                    except Exception as e:  # pragma: no cover – defensiv
                        LOG.warning("Sensor %s read_raw() Fehler: %s", ch.name, e)
                        ch.mark_polled(now)
                        continue

                    try:
                        # SnapChain-Daten + Qualität berechnen
                        data, quality = ch.build_snapchain_data(raw, ts=int(now))

                        # In DB schreiben
                        snap_id = sql_manager.insert_snapchain(data)
                        ch.mark_polled(now)
                        wrote += 1

                        # Best-Effort-Audit – hier KEIN Parameter-Name 'kind' benutzen,
                        # um nicht mit _audit(kind, action, **fields) zu kollidieren.
                        try:
                            _audit(
                                "sensor",
                                "sample",
                                sensor_name=ch.name,
                                origin=ch.origin,
                                sensor_kind=ch.kind,
                                quality=float(quality),
                                snap_id=snap_id,
                            )
                        except Exception as ae:  # pragma: no cover – defensiv
                            LOG.debug("Sensor %s Audit-Fehler: %s", ch.name, ae)

                    except Exception as e:  # pragma: no cover – defensiv
                        LOG.warning(
                            "Sensor %s Fehler beim Schreiben/Audit: %s",
                            ch.name,
                            e,
                        )

                if wrote == 0:
                    time.sleep(self.sensors_sleep_base)
        finally:
            LOG.info("DeviceHub Sensor-Loop gestoppt.")

    def start_sensors(self) -> None:
        """
        Startet die Sensor-Polling-Schleife in einem Hintergrundthread.

        • Kamera/Audio werden davon nicht beeinflusst.
        • Wenn keine Sensoren registriert sind, passiert praktisch nichts.
        """
        if self._sensor_thread and self._sensor_thread.is_alive():
            return
        self._sensor_run = True
        thr = threading.Thread(target=self._sensor_loop, daemon=True)
        self._sensor_thread = thr
        thr.start()
        LOG.info("DeviceHub Sensor-Poll-Thread gestartet.")

    def stop_sensors(self, join: bool = True) -> None:
        """
        Stoppt die Sensor-Polling-Schleife.
        """
        self._sensor_run = False
        t = self._sensor_thread
        if join and t is not None:
            t.join(timeout=2.0)
        LOG.info("DeviceHub Sensor-Poll-Thread beendet.")

    def get_sensor_health(self) -> Dict[str, Any]:
        """
        Liefert einen einfachen Health-Status der Sensorintegration.
        """
        return {
            "enabled": self.sensors_enabled,
            "channels": self.list_sensor_channels(),
            "running": bool(self._sensor_thread and self._sensor_thread.is_alive()),
        }

    # -------------------------------------------------------------------------
    # Sessions-API
    # -------------------------------------------------------------------------
    def open_session(self, client: str, kind: str) -> str:
        """Öffnet eine Session (z. B. client='video_ui', kind='camera|audio|light|generic')."""
        sid = str(uuid.uuid4())
        now = time.time()
        with self._sess_lock:
            self._sessions[sid] = {"client": client, "kind": kind, "start": now}
        _audit("session", "open", session_id=sid, client=client, kind=kind)
        return sid

    def close_session(self, session_id: str) -> None:
        now = time.time()
        with self._sess_lock:
            sess = self._sessions.pop(session_id, None)
        if sess:
            dur = now - sess.get("start", now)
            _audit(
                "session",
                "close",
                session_id=session_id,
                client=sess.get("client"),
                kind=sess.get("kind"),
                duration=dur,
            )

    # -------------------------------------------------------------------------
    # Kamera
    # -------------------------------------------------------------------------
    def _build_cam(self) -> _BaseCam:
        if self.backend == "picamera2" and Picamera2 is not None:
            return _PiCamera2Cam(self.w, self.h, self.fps)
        if self.backend == "opencv" and cv2 is not None:
            return _OpenCVCam(self.dev_id, self.w, self.h, self.fps)
        LOG.warning("Kein passendes Kamera-Backend → DummyCam.")
        return _DummyCam(self.w, self.h)

    def _loop(self) -> None:
        period = 1.0 / max(self.fps, 1)
        while self._cap_run:
            t0 = time.time()
            frame = None
            now = time.time()
            self._cap_heartbeat_ts = now


            try:
                frame = self._cam.read()
            except Exception as e:
                # Harte Exceptions dürfen nie den Capture-Thread töten.
                self._cam_last_error = f"read_exception: {e!r}"[:500]
                self._cam_last_error_ts = time.time()
                LOG.error("Kamera read()-Fehler: %s", e)
                _audit_throttled("camera_read_exc", 5.0, "camera", "read_error", backend=self.backend, error=str(e))
                frame = None

            if frame is not None:
                # OK-Frame
                self._cam_last_ok_ts = now
                self._cam_fail_streak = 0
                frame = self._apply_frame_rotation(frame)
                with self._frame_lock:
                    self._latest_frame = frame
                    self._latest_ts = now
                self._publish_global_frame_cache(frame, now, source='capture_loop')
            else:
                # read_fail / None
                self._cam_fail_streak = int(getattr(self, "_cam_fail_streak", 0) or 0) + 1
                if not self._cam_last_ok_ts:
                    # Wenn noch nie OK-Frame kam: trotzdem Startzeit markieren,
                    # damit Stall-Detektion sinnvoll arbeitet.
                    self._cam_last_ok_ts = now

                # Audit (throttled) – damit UI/Logs eindeutig zeigen „kein Frame“
                _audit_throttled("opencv_read_fail", 5.0, "camera", "read_fail", backend=self.backend, device=self.dev_id)

                # ---------------- Watchdog: Soft-Restart bei Stall ----------------
                if not self._external_active():
                    try:
                        stall_sec = float(os.environ.get("OROMA_CAMERA_STALL_RESTART_SEC", "12"))
                    except Exception:
                        stall_sec = 12.0
                    try:
                        min_iv = float(os.environ.get("OROMA_CAMERA_RESTART_MIN_INTERVAL_SEC", "30"))
                    except Exception:
                        min_iv = 30.0
                    try:
                        fail_n = int(os.environ.get("OROMA_CAMERA_FAIL_STREAK_RESTART", "10"))
                    except Exception:
                        fail_n = 10

                    age_ok = (now - float(self._cam_last_ok_ts or now))
                    can_try = (now - float(getattr(self, "_cam_restart_ts", 0.0) or 0.0)) >= max(5.0, min_iv)

                    if age_ok >= max(3.0, stall_sec) and self._cam_fail_streak >= max(3, fail_n) and can_try:
                        self._cam_restart_ts = now
                        self._cam_restart_count = int(getattr(self, "_cam_restart_count", 0) or 0) + 1
                        LOG.warning(
                            "[DeviceHub] Kamera-Stall erkannt (age_ok=%.1fs, fail_streak=%d) → Soft-Restart #%d",
                            age_ok, self._cam_fail_streak, self._cam_restart_count
                        )
                        _audit("camera", "stall_restart_try", backend=self.backend, device=self.dev_id, age_ok=age_ok, fail_streak=self._cam_fail_streak, n=self._cam_restart_count)
                        # Best-effort stop/start
                        try:
                            self._cam.stop()
                        except Exception as e:
                            self._cam_last_error = f"restart_stop_error: {e!r}"[:500]
                            self._cam_last_error_ts = time.time()
                            log_suppressed(LOG, key="device_hub.cam_restart.stop", msg="Suppressed exception (stop)", exc=e, level=logging.WARNING, interval_s=30)
                        try:
                            time.sleep(0.20)
                            self._cam.start()
                            # Reset counters after a successful start attempt
                            self._cam_fail_streak = 0
                            self._cam_last_ok_ts = time.time()
                            self._cam_last_error = ""
                            self._cam_last_error_ts = 0.0
                            _audit("camera", "stall_restart_ok", backend=self.backend, device=self.dev_id, n=self._cam_restart_count)
                        except Exception as e:
                            # Start-Fail Cooldown: prevent hot restart loop
                            self._cam_start_fail_ts = time.time()
                            self._cam_last_error = f"restart_start_error: {e!r}"[:500]
                            self._cam_last_error_ts = time.time()
                            LOG.error("[DeviceHub] Soft-Restart fehlgeschlagen: %s", e)
                            _audit("camera", "stall_restart_fail", backend=self.backend, device=self.dev_id, error=str(e), n=self._cam_restart_count)

            dt = time.time() - t0
            time.sleep(max(0.0, period - dt))


    def start(self) -> None:
        """Startet (falls nicht bereits gestartet) Kamera + Capture-Thread."""
        if self._cap_run:
            return

        # Cooldown nach Start-Fehlern (verhindert Start-Spam & FD-Druck)
        now = time.time()
        if self._cam_start_fail_ts > 0.0 and (now - self._cam_start_fail_ts) < self._cam_start_cooldown_sec:
            return


        # ---------------------------------------------------------------------
        # PRODUKTIONSFIX – External-Provider aktiv => interne Kamera NICHT starten
        # ---------------------------------------------------------------------
        # Wenn camera_hub/PiCar Frames liefert, ist ein paralleler Picamera2-Start
        # häufig fatal (libcamera init kollidiert): "Camera __init__ sequence".
        #
        # Wir blocken daher interne Starts solange External-Mode aktiv ist,
        # außer der Guard wurde explizit deaktiviert.
        # ---------------------------------------------------------------------
        if self._external_active():
            return
        # ---- EXTERNAL_ACTIVE_GUARD ----
        try:
            self._cam.start()
        except Exception as e:
            self._cam_start_fail_ts = time.time()
            LOG.error("Kamera konnte nicht gestartet werden: %s", e)
            _audit("camera", "start_fail", backend=self.backend, device=self.dev_id, error=str(e))
            return  # **nicht** Capture-Loop starten, wenn Start fehlschlug
        self._cap_run = True
        self._cap_thread = threading.Thread(target=self._loop, daemon=True)
        self._cap_thread.start()
        LOG.info("DeviceHub Capture-Thread läuft.")
        _audit("camera", "capture_loop_start", backend=self.backend, device=self._cam.id_string())
        # Capture-Watchdog (Thread-/Heartbeat-Guard)
        self._ensure_cap_watchdog()

    def stop(self) -> None:
        """Stoppt Capture-Thread und schließt Kamera."""
        if not self._cap_run:
            return
        self._cap_run = False
        if self._cap_thread and self._cap_thread.is_alive():
            try:
                self._cap_thread.join(timeout=1.5)
            except Exception as e:
                log_suppressed(LOG, key="device_hub.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        self._cap_thread = None
        try:
            self._cam.stop()
        except Exception as e:
            log_suppressed(LOG, key="device_hub.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        LOG.info("DeviceHub (Kamera) gestoppt.")
        _audit("camera", "capture_loop_stop", backend=self.backend)

    # =====================================================================
    # BLOCK: CAPTURE_THREAD_WATCHDOG (SWAPPABLE)
    # Zweck:
    #   Wenn der Capture-Thread stirbt oder der Loop-Heartbeat stagniert,
    #   soll DeviceHub best-effort einen Soft-Recover versuchen.
    #
    # Hinweis:
    #   Dieser Watchdog ergänzt den Stall-Restart im Capture-Loop (read_fail),
    #   indem er auch "Thread tot" und "Thread hängt" erkennt.
    # =====================================================================
    def _ensure_cap_watchdog(self) -> None:
        try:
            if getattr(self, "_cap_watchdog_run", False):
                return
            wd_on = os.environ.get("OROMA_DEVICEHUB_WATCHDOG", "1").strip().lower() not in ("0", "false", "no", "off")
            if not wd_on:
                return
            self._cap_watchdog_run = True
            self._cap_watchdog_thread = threading.Thread(target=self._cap_watchdog_loop, daemon=True)
            self._cap_watchdog_thread.start()
            _audit_throttled("cap_watchdog_start", 30.0, "camera", "cap_watchdog_start", backend=self.backend)
        except Exception as e:
            log_suppressed(LOG, key="device_hub.cap_watchdog.start", msg="Suppressed exception (cap watchdog start)", exc=e, level=logging.WARNING, interval_s=60)

    def _cap_watchdog_loop(self) -> None:
        while getattr(self, "_cap_watchdog_run", False):
            try:
                try:
                    interval = float(os.environ.get("OROMA_DEVICEHUB_WATCHDOG_INTERVAL_SEC", "5"))
                except Exception:
                    interval = 5.0
                time.sleep(max(1.0, min(30.0, interval)))

                # Nie restarten, wenn externes Feed aktiv ist.
                if self._external_active():
                    continue

                if not getattr(self, "_cap_run", False):
                    continue

                thr = getattr(self, "_cap_thread", None)
                alive = bool(thr is not None and getattr(thr, "is_alive", lambda: False)())
                now = time.time()
                hb = float(getattr(self, "_cap_heartbeat_ts", 0.0) or 0.0)
                hb_age = (now - hb) if hb else None

                try:
                    hung_sec = float(os.environ.get("OROMA_DEVICEHUB_WATCHDOG_HUNG_SEC", "20"))
                except Exception:
                    hung_sec = 20.0
                try:
                    min_iv = float(os.environ.get("OROMA_DEVICEHUB_WATCHDOG_MIN_RESTART_SEC", "45"))
                except Exception:
                    min_iv = 45.0

                need_restart = False
                reason = ""
                if not alive:
                    need_restart = True
                    reason = "thread_dead"
                elif hb_age is not None and hb_age >= max(5.0, hung_sec):
                    need_restart = True
                    reason = f"heartbeat_stale:{hb_age:.1f}s"

                if not need_restart:
                    continue

                can_try = (now - float(getattr(self, "_cap_watchdog_restart_ts", 0.0) or 0.0)) >= max(10.0, min_iv)
                if not can_try:
                    _audit_throttled("cap_watchdog_skip", 30.0, "camera", "cap_watchdog_skip", reason=reason)
                    continue

                self._cap_watchdog_restart_ts = now
                self._cap_watchdog_restart_count = int(getattr(self, "_cap_watchdog_restart_count", 0) or 0) + 1

                LOG.warning("[DeviceHub] Capture-Watchdog: %s → Soft-Recover #%d", reason, self._cap_watchdog_restart_count)
                _audit("camera", "cap_watchdog_restart_try", reason=reason, n=self._cap_watchdog_restart_count, backend=self.backend)

                # Best-effort Soft-Recover: stop/start
                try:
                    self.stop()
                except Exception as e:
                    log_suppressed(LOG, key="device_hub.cap_watchdog.stop", msg="Suppressed exception (cap watchdog stop)", exc=e, level=logging.WARNING, interval_s=30)
                try:
                    time.sleep(0.20)
                    self.start()
                    _audit("camera", "cap_watchdog_restart_ok", reason=reason, n=self._cap_watchdog_restart_count, backend=self.backend)
                except Exception as e:
                    self._cam_start_fail_ts = time.time()
                    self._cam_last_error = f"cap_watchdog_restart_error: {e!r}"[:500]
                    self._cam_last_error_ts = time.time()
                    LOG.error("[DeviceHub] Capture-Watchdog Soft-Recover fehlgeschlagen: %s", e)
                    _audit("camera", "cap_watchdog_restart_fail", reason=reason, error=str(e), n=self._cap_watchdog_restart_count, backend=self.backend)

            except Exception as e:
                log_suppressed(LOG, key="device_hub.cap_watchdog.loop", msg="Suppressed exception (cap watchdog loop)", exc=e, level=logging.WARNING, interval_s=30)

    # =====================================================================
    # END BLOCK: CAPTURE_THREAD_WATCHDOG
    # =====================================================================


    # =====================================================================
    # BLOCK: SUBMIT_EXTERNAL_FRAME (SWAPPABLE)
    # Zweck:
    #   Externe Komponenten dürfen ein Frame (BGR ndarray) an DeviceHub übergeben.
    #
    # Design:
    #   - Keine neuen Threads
    #   - Kein neues Backend
    #   - Thread-safe über _frame_lock
    #
    # Nutzung:
    #   hub.submit_frame(frame, source="picar")
    #
    # Hinweis:
    #   - Überschreibt _latest_frame und _latest_ts.
    #   - Capture-Loop darf parallel laufen; "Latest" ist dann das zuletzt
    #     geschriebene Frame (intern oder extern).
    # =====================================================================
    def submit_frame(self, frame: "np.ndarray", source: str = "external") -> None:
        if frame is None:
            return

        # Ensure consistent orientation across *all* providers.
        frame = self._apply_frame_rotation(frame)

        now = time.time()
        with self._frame_lock:
            self._latest_frame = frame
            self._latest_ts = now

            # Debug/Status-Infos (optional)
            self._external_source = source or "external"
            self._external_ts = now
            self._external_frames += 1
        self._publish_global_frame_cache(frame, now, source=source or 'external')

        # Audit throttled, damit Log nicht explodiert
        _audit_throttled(
            "external_frame",
            2.0,
            "camera",
            "external_frame",
            source=self._external_source,
            external_frames=self._external_frames,
            ts=self._external_ts,
        )
    # =====================================================================
    # END BLOCK: SUBMIT_EXTERNAL_FRAME
    # =====================================================================

    def _external_active(self) -> bool:
        """True, wenn innerhalb TTL externe Frames eingetroffen sind.

        Wird genutzt, um parallele interne Kamera-Starts zu verhindern.
        """
        if self._allow_internal_with_external:
            return False
        if self._external_ts <= 0.0:
            return False
        now = time.time()
        ttl = float(getattr(self, '_external_mode_ttl_sec', 60.0) or 60.0)
        return (now - self._external_ts) <= ttl

    def _apply_frame_rotation(self, frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Optional frame rotation (currently only 180°).

        Why here (DeviceHub)?
          - Central place: affects internal camera, OpenCV backend and external providers
            that push frames via submit_frame().
          - Keeps the DB, scenegraph and replay consistently oriented.

        Env:
          - OROMA_VISION_ROTATE_DEG=180  (preferred)
          - OROMA_CAMERA_ROTATE_DEG=180 (fallback)
        """
        if frame is None:
            return None
        if getattr(self, "rotate_deg", 0) != 180:
            return frame
        try:
            # 180° = flip vertical + horizontal. Keep contiguous for downstream JPEG encoders.
            return np.ascontiguousarray(frame[::-1, ::-1])
        except Exception as e:
            log_suppressed(LOG, key="device_hub.ret.6", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return frame

    def _publish_global_frame_cache(self, frame: Optional[np.ndarray], ts: Optional[float], source: str = "camera") -> None:
        """Schreibt ein leichtgewichtiges Latest-Frame-Cache-Artefakt fuer andere Prozesse.

        Ziel
        ----
        Kurzlebige One-Shot-Consumer (z. B. PTZ-Loop im Orchestrator) sollen ein
        aktuelles Bild lesen koennen, ohne selbst den DeviceHub/HW-Pfad zu
        initialisieren. Deshalb publiziert der Hub periodisch ein kleines JPEG
        plus Metadaten in den gemeinsamen State-Bereich.

        Verhalten
        ---------
        - best effort / nie fatal
        - rate-limited ueber `_FRAME_CACHE_MIN_INTERVAL_SEC`
        - atomare `os.replace()`-Writes fuer JPEG + JSON
        """
        if frame is None or cv2 is None:
            return
        try:
            now = time.time()
            if (now - float(getattr(self, "_frame_cache_last_publish_ts", 0.0) or 0.0)) < float(_FRAME_CACHE_MIN_INTERVAL_SEC):
                return
            _FRAME_CACHE_STATE_DIR.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(_FRAME_CACHE_JPEG_QUALITY)])
            if not ok:
                return
            jpg_tmp = _FRAME_CACHE_JPG_PATH.with_suffix('.jpg.tmp')
            meta_tmp = _FRAME_CACHE_META_PATH.with_suffix('.json.tmp')
            jpg_tmp.write_bytes(buf.tobytes())
            os.replace(str(jpg_tmp), str(_FRAME_CACHE_JPG_PATH))
            meta = {
                'ts': float(ts or now),
                'cache_ts': float(now),
                'source': str(source or 'camera'),
                'w': int(frame.shape[1]) if getattr(frame, 'ndim', 0) >= 2 else None,
                'h': int(frame.shape[0]) if getattr(frame, 'ndim', 0) >= 2 else None,
                'channels': int(frame.shape[2]) if getattr(frame, 'ndim', 0) >= 3 else 1,
            }
            meta_tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
            os.replace(str(meta_tmp), str(_FRAME_CACHE_META_PATH))
            self._frame_cache_last_publish_ts = now
        except Exception as e:
            log_suppressed(LOG, key='device_hub.frame_cache.publish', msg='Global frame cache publish failed', exc=e, interval_s=60)

    def get_latest_frame(self, ensure_start: bool = True) -> Tuple[Optional[np.ndarray], float]:
        """Gibt (Frame, Timestamp) zurück. Frame ist BGR ndarray oder None.

        -----------------------------------------------------------------------------
        PRODUKTIONSFIX – Externe Frame-Provider dürfen keine Kamera-Starts triggern
        -----------------------------------------------------------------------------
        Hintergrund:
          ORÓMA kann Frames über externe Provider in den DeviceHub pushen
          (z.B. camera_hub / PiCar / Remote-Provider) via submit_frame().

          Der bisherige Code startete die interne Kamera immer dann, wenn
          ensure_start=True und der Capture-Loop nicht läuft. Das ist in
          External-Provider-Betrieb falsch, weil:
            • Externe Frames sind bereits verfügbar (Latest-Frame wird aktualisiert)
            • Trotzdem werden Picamera2-Startversuche ausgelöst
            • Im Fehlerfall kann das zu FD-Druck bis Errno 24 führen
              ("Too many open files") und danach auch zu Folgefehlern
              (z.B. libcamera/SQLite/Open).

        Lösung:
          1) Wenn ein *frisches* externes Frame vorliegt, liefern wir es sofort zurück
             und starten KEINE interne Kamera.
          2) Nur wenn kein frisches externes Frame vorliegt, greift das bisherige
             ensure_start-Verhalten (interne Kamera starten).

        ENV:
          OROMA_EXTERNAL_FRAME_FRESH_SEC
            - Zeitfenster (Sekunden), in dem ein externes Frame als "frisch" gilt.
            - Default: 2.0

        Nicht-destruktiv:
          • Keine Änderung am Frame-Format
          • Keine Änderung am Capture-Loop (nur Start-Entscheidung angepasst)
        -----------------------------------------------------------------------------
        """
        now = time.time()
        try:
            fresh_sec = float(os.environ.get("OROMA_EXTERNAL_FRAME_FRESH_SEC", "8.0"))
        except Exception:
            fresh_sec = 8.0

        # 0) External-Mode aktiv: liefere Latest (auch wenn 'stale') und
        #    triggere KEINEN internen Kamera-Start.
        if self._external_active():
            with self._frame_lock:
                if self._latest_frame is not None:
                    return (self._latest_frame.copy(), self._latest_ts)
            # Falls extern aktiv aber noch kein Frame vorhanden: kein Start erzwingen
            return (None, self._latest_ts)

        # 1) Externes (gepushtes) Frame bevorzugen – keine Kamera doppelt öffnen
        with self._frame_lock:
            if (
                self._latest_frame is not None
                and self._external_ts > 0.0
                and (now - self._external_ts) <= fresh_sec
            ):
                return (self._latest_frame.copy(), self._latest_ts)

        # 2) Falls kein frisches externes Frame vorhanden: optional interne Kamera starten
        if ensure_start and not self._cap_run:
            self.start()
            time.sleep(0.05)  # kleines Aufwärmen
        with self._frame_lock:
            return (None if self._latest_frame is None else self._latest_frame.copy(), self._latest_ts)


    def get_latest_cached_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """Gibt das aktuell gecachte Frame strikt nicht-blockierend zurück.

        Zweck
        -----
        Dieser Getter ist fuer Fast-Path-Consumer wie den PTZ-Orchestrator-
        One-Shot gedacht. Er darf KEINE Kamera starten, KEINE Retries machen und
        keine sonstigen I/O-Nebenwirkungen ausloesen.

        Verhalten
        ---------
        - liefert sofort das letzte bekannte Frame (Kopie) + Timestamp
        - wenn noch kein Frame vorhanden ist: (None, last_ts)
        - startet niemals den Capture-Loop
        - ignoriert ensure-/Freshness-/External-Start-Logik bewusst
        """
        with self._frame_lock:
            return (None if self._latest_frame is None else self._latest_frame.copy(), self._latest_ts)

    def get_latest_jpeg(self, quality: int = 85, client: Optional[str] = None) -> Optional[bytes]:
        """Gibt aktuelles Frame als JPEG-Bytes zurück (oder None)."""
        frame, ts = self.get_latest_frame()
        if frame is None:
            _audit_throttled("jpeg_none", 3.0, "camera", "snapshot_none", backend=self.backend, client=client)
            return None
        if cv2 is None:
            try:
                import imageio  # type: ignore
                jb = imageio.v3.imencode(".jpg", frame, quality=quality).tobytes()
            except Exception as e:
                _audit_throttled(
                    "jpeg_enc_fail",
                    5.0,
                    "camera",
                    "snapshot_encode_fail",
                    backend=self.backend,
                    error=str(e),
                )
                return None
        else:
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            if not ok:
                _audit_throttled(
                    "jpeg_enc_fail_cv2",
                    5.0,
                    "camera",
                    "snapshot_encode_fail",
                    backend=self.backend,
                    client=client,
                )
                return None
            jb = buf.tobytes()
        _audit_throttled(
            "snapshot_ok",
            _AUDIT_SNAP_THR,
            "camera",
            "snapshot",
            backend=self.backend,
            bytes=len(jb),
            ts_frame=ts,
            client=client,
        )
        return jb

    def mjpeg_generator(
        self,
        boundary: bytes = b"frame",
        fps_cap: Optional[int] = None,
        client: Optional[str] = None,
    ) -> Generator[bytes, None, None]:
        """Generator für Flask-Response mit 'multipart/x-mixed-replace'."""
        min_period = 1.0 / float(fps_cap or self.fps or 10)
        sid = None
        try:
            if client:
                sid = self.open_session(client, kind="camera")
            while True:
                t0 = time.time()
                jpg = self.get_latest_jpeg(client=client)
                if jpg is not None:
                    _audit_throttled(
                        f"mjpeg_{client or 'anon'}",
                        _AUDIT_SNAP_THR,
                        "camera",
                        "mjpeg_push",
                        client=client,
                        bytes=len(jpg),
                    )
                    yield (
                        b"--" + boundary + b"\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                        + jpg + b"\r\n"
                    )
                dt = time.time() - t0
                time.sleep(max(0.0, min_period - dt))
        finally:
            if sid:
                self.close_session(sid)

    # -------------------------------------------------------------------------
    # Light (0..100)
    # -------------------------------------------------------------------------
    def _calc_luma(self, frame: np.ndarray) -> float:
        r = frame[:, :, 2].astype(np.float32)
        g = frame[:, :, 1].astype(np.float32)
        b = frame[:, :, 0].astype(np.float32)
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return float(y.mean())

    def _scale_0_100(self, luma_0_255: float) -> float:
        v = (luma_0_255 / 255.0) * 100.0
        v = max(self.light_min, min(self.light_max, v))
        return round(v, 2)

    def get_light_level(self) -> Optional[float]:
        """Gibt gemessene Helligkeit 0..100 zurück (oder None bei off)."""
        if self.light_source == "off":
            return None
        now = time.time()
        with self._light_lock:
            if self._light_val is not None and (now - self._light_ts) < max(1, self.light_interval):
                return self._light_val

        if self.light_source == "dummy":
            val = 80.0  # „hell“
            with self._light_lock:
                self._light_val, self._light_ts = val, now
            if self.light_audit_mode in ("all",):
                _audit("light", "sample", mode="dummy", value=val)
            return val

        frame, _ = self.get_latest_frame(ensure_start=True)
        if frame is None:
            LOG.debug("Light: kein Frame verfügbar.")
            _audit_throttled("light_none", 10.0, "light", "no_frame", mode=self.light_source)
            return None
        luma = self._calc_luma(frame)
        val = self._scale_0_100(luma)

        # Hysterese: Schwellen *direkt* in 0..100 vergleichen (Fix gegenüber v3.7)
        new_state = "DARK" if val <= self._dark_thr else ("BRIGHT" if val >= self._bright_thr else self._light_state)

        with self._light_lock:
            prev_state = self._light_state
            self._light_val, self._light_ts = val, now
            self._light_state = new_state

        if self.light_audit_mode == "all":
            _audit("light", "sample", value=val, state=self._light_state)
        elif self.light_audit_mode == "changes" and new_state != prev_state and new_state is not None:
            _audit("light", "change", value=val, state=self._light_state, prev=prev_state)
        return val

    # -------------------------------------------------------------------------
    # Audio – Geräte, Capture, Playback
    # -------------------------------------------------------------------------
    def _require_audio(self) -> bool:
        if not self.audio_enable:
            return False
        if sd is None:
            LOG.warning("Audio deaktiviert: 'sounddevice' nicht verfügbar.")
            return False
        return True


    def _force_audio_hostapi(self) -> None:
        """Versucht (best effort) eine stabile Host-API für PortAudio auszuwählen.

        Hintergrund (Headless/systemd):
        - PortAudio kann in manchen Umgebungen als Default eine Host-API wählen,
          die *keine* Devices liefert (z.B. Pulse/JACK ohne Daemon).
        - Dann ist sd.query_devices() leer und default.device=(-1,-1).

        Strategie:
        - Wenn OROMA_AUDIO_HOSTAPI gesetzt ist (Default: 'ALSA'), suche diese Host-API
          in sd.query_hostapis() und setze sd.default.hostapi darauf.
        - Manche sounddevice-Versionen expose'n ``default.hostapi`` jedoch nur read-only.
          In diesem Fall wird der Versuch sauber uebersprungen, statt bei jedem Lauf einen
          Warning-Block in ``service.err.log`` zu erzeugen.
        - Fehler werden NICHT hart geworfen, sondern nur gedrosselt gemeldet.
        """
        if sd is None:
            return
        prefer = (os.getenv("OROMA_AUDIO_HOSTAPI", "ALSA") or "").strip()
        if not prefer:
            return
        default_desc = getattr(type(sd.default), "hostapi", None)
        if isinstance(default_desc, property) and getattr(default_desc, "fset", None) is None:
            log_suppressed(
                LOG,
                key="device_hub.audio.hostapi.readonly",
                msg="[DeviceHub] default.hostapi ist read-only – HostAPI-Umschaltung wird uebersprungen.",
                level=logging.INFO,
                interval_s=3600,
            )
            return
        try:
            hostapis = sd.query_hostapis()
        except Exception as e:
            log_suppressed(
                LOG,
                key="device_hub.audio.hostapi.probe",
                msg="[DeviceHub] audio hostapi probe failed",
                exc=e,
                level=logging.WARNING,
                interval_s=600,
            )
            return
        try:
            prefer_l = prefer.lower()
            for i, ha in enumerate(hostapis):
                name = str((ha or {}).get("name", "") or "")
                if prefer_l in name.lower():
                    try:
                        # sounddevice nutzt hostapi-index (0..n-1)
                        sd.default.hostapi = i  # type: ignore[attr-defined]
                        LOG.info("[DeviceHub] audio hostapi selected: %s (idx=%s)", name, i)
                    except Exception as e:
                        log_suppressed(
                            LOG,
                            key="device_hub.audio.hostapi.set",
                            msg="[DeviceHub] set default.hostapi failed",
                            exc=e,
                            level=logging.WARNING,
                            interval_s=1800,
                        )
                    return
        except Exception as e:
            log_suppressed(
                LOG,
                key="device_hub.audio.hostapi.select",
                msg="[DeviceHub] audio hostapi selection error",
                exc=e,
                level=logging.WARNING,
                interval_s=600,
            )
            return
    def list_audio_devices(self) -> Dict[str, List[Dict[str, Any]]]:
        """Liefert {'input': [...], 'output': [...]} mit device info (falls verfügbar).

        Zusätzlich:
          - setzt self._audio_devices_last_error bei Query-Fehlern (UI-Debug).
        """
        out: Dict[str, List[Dict[str, Any]]] = {"input": [], "output": []}
        if sd is None:
            return out
        try:
            self._audio_devices_last_error = ""
            self._force_audio_hostapi()
            devs = sd.query_devices()
            if not devs:
                try:
                    hap = sd.query_hostapis()
                except Exception:
                    hap = None
                try:
                    dd = getattr(sd.default, 'device', None)
                except Exception:
                    dd = None
                self._audio_devices_last_error = (
                    "PortAudio liefert 0 Devices. hostapis=%s default.device=%s "
                    "(Tipp: OROMA_AUDIO_HOSTAPI=ALSA setzen; Debug: arecord -l / arecord -L)" % (hap, dd)
                )
                return out
            for i, d in enumerate(devs):
                info = {
                    "index": i,
                    "name": d.get("name"),
                    "max_input_channels": d.get("max_input_channels"),
                    "max_output_channels": d.get("max_output_channels"),
                }
                if int(d.get("max_input_channels", 0) or 0) > 0:
                    out["input"].append(info)
                if int(d.get("max_output_channels", 0) or 0) > 0:
                    out["output"].append(info)
        except Exception as e:
            self._audio_devices_last_error = str(e)
            LOG.warning("list_audio_devices() Fehler: %s", e)
        return out

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
            log_suppressed(LOG, key="device_hub.ret.7", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    @staticmethod
    def _device_name_by_index(index: Optional[int]) -> Optional[str]:
        if sd is None or index is None:
            return None
        try:
            return sd.query_devices()[index]["name"]
        except Exception as e:
            log_suppressed(LOG, key="device_hub.ret.8", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def _output_device_ok(self, idx: int) -> bool:
        """True, wenn der Device-Index mindestens einen Output-Kanal anbietet."""
        if sd is None:
            return False
        try:
            d = sd.query_devices(int(idx))
            mo = int((d or {}).get("max_output_channels", 0) or 0)
            return mo >= 1
        except Exception as e:
            log_suppressed(LOG, key="device_hub.ret.output_ok", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return False

    def _resolve_output_device_index(self) -> Optional[int]:
        """Wählt robust ein brauchbares Output-Device aus.

        Reihenfolge:
          1. bereits gesetzter und valider Index
          2. Name-Match über OROMA_AUDIO_OUTPUT_NAME
          3. PortAudio default.device[1]
          4. erstes Device mit max_output_channels > 0
        """
        if sd is None:
            return None
        try:
            self._force_audio_hostapi()
        except Exception:
            pass

        if self._out_dev_idx is not None and self._output_device_ok(int(self._out_dev_idx)):
            return int(self._out_dev_idx)

        idx = self._pick_device_index(self.audio_out_name, want_input=False)
        if idx is not None and self._output_device_ok(int(idx)):
            self._out_dev_idx = int(idx)
            return self._out_dev_idx

        try:
            dd = getattr(sd.default, "device", None)
            if isinstance(dd, (list, tuple)) and len(dd) >= 2:
                cand = dd[1]
                if isinstance(cand, int) and cand >= 0 and self._output_device_ok(int(cand)):
                    self._out_dev_idx = int(cand)
                    return self._out_dev_idx
        except Exception as e:
            log_suppressed(LOG, key="device_hub.pass.output_default", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        try:
            devs = sd.query_devices()
            for i, d in enumerate(devs):
                if int((d or {}).get("max_output_channels", 0) or 0) > 0:
                    self._out_dev_idx = int(i)
                    return self._out_dev_idx
        except Exception as e:
            log_suppressed(LOG, key="device_hub.pass.output_scan", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return None

    def _audio_callback(self, indata, frames, time_info, status):  # sd.InputStream callback
        """PortAudio Callback: nimmt Float32 entgegen und befüllt Ringbuffer.

        Wichtig:
          - target_sr = self.audio_sr (Default 16kHz)
          - falls der Stream mit device-default SR (z.B. 48kHz) geöffnet wurde,
            wird im Callback auf self.audio_sr downsampled (best-effort).
        """
        if status:
            LOG.debug("Audio status: %s", status)
        if indata is None:
            return

        # 1) Mono-Puffer (float32)
        try:
            if getattr(indata, "ndim", 0) == 2 and indata.shape[1] > 1:
                buf = np.mean(indata, axis=1, dtype=np.float32)
            else:
                buf = np.asarray(indata, dtype=np.float32).reshape(-1)
        except Exception:
            try:
                buf = np.array(indata, dtype=np.float32).reshape(-1)
            except Exception:
                return

        # 2) Optional: Resample (device SR -> target SR)
        try:
            in_sr = int(self._mic_open_sr or self.audio_sr)
            out_sr = int(self.audio_sr)
            if buf.size and in_sr > 0 and out_sr > 0 and in_sr != out_sr:
                if in_sr % out_sr == 0:
                    fac = in_sr // out_sr
                    n = (buf.size // fac) * fac
                    if n > 0:
                        buf = buf[:n].reshape(-1, fac).mean(axis=1).astype(np.float32, copy=False)
                    else:
                        buf = buf[:0]
                else:
                    in_n = int(buf.size)
                    out_n = int(round(in_n * (out_sr / float(in_sr))))
                    if out_n > 0:
                        xp = np.linspace(0.0, 1.0, num=in_n, endpoint=False, dtype=np.float32)
                        xq = np.linspace(0.0, 1.0, num=out_n, endpoint=False, dtype=np.float32)
                        buf = np.interp(xq, xp, buf).astype(np.float32, copy=False)
        except Exception as e:
            log_suppressed(LOG, key="device_hub.pass.9", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # 3) Ringbuffer + RMS-Level (throttled)
        with self._audio_lock:
            self._ring.append(buf)
            self._ring_np_cache = None
            t = time.time()
            if t - self._lvl_ts >= self.audio_lvl_iv:
                s = float(np.sqrt(np.mean(np.square(buf), dtype=np.float32))) if buf.size else 0.0
                self._lvl_val = max(0.0, min(1.0, s))
                self._lvl_ts = t

    def start_mic(self, client: Optional[str] = None) -> bool:
        """Startet den Mikrofonstream (lazy).

        Robustheitsziele (Headless/systemd):
          - PortAudio default device=-1 abfangen
          - Device-Selection: INDEX > NAME > default.device[0] > first input
          - Samplerate: zuerst target self.audio_sr; fallback device default SR
            und im Callback auf self.audio_sr downsamplen.
        """
        if not self._require_audio():
            return False
        if self._mic_stream is not None:
            return True

        # sicherstellen, dass PortAudio eine brauchbare Host-API nutzt (Headless)
        try:
            self._force_audio_hostapi()
        except Exception:
            pass

        try:
            # --------------------------------------------------------------
            # Audio-In Device Selection
            # --------------------------------------------------------------
            in_idx: Optional[int] = None

            def _input_device_ok(idx: int) -> bool:
                """True, wenn PortAudio/ALSA den Device-Index für Input mit >=audio_ch anbietet.

                Hintergrund: Nach Hotplug/Reboot kann ein vormals gültiger Index (z.B. 0)
                plötzlich nur noch Output liefern (max_input_channels=0). Dann wirft
                PortAudio beim Öffnen: 'Invalid number of channels' (-9998).
                """
                try:
                    d = sd.query_devices(int(idx))
                    mi = int((d or {}).get('max_input_channels', 0) or 0)
                    return mi >= max(1, int(getattr(self, 'audio_ch', 1) or 1))
                except Exception as e:
                    log_suppressed(LOG, key="device_hub.ret.10", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
                    return False

            if getattr(self, "audio_in_index", None) is not None:
                in_idx = self.audio_in_index
                if in_idx is not None and not _input_device_ok(int(in_idx)):
                    try:
                        d = sd.query_devices(int(in_idx))
                        mi = int((d or {}).get('max_input_channels', 0) or 0)
                        nm = str((d or {}).get('name', '') or '')
                    except Exception:
                        mi = -1
                        nm = ''
                    LOG.warning(
                        '[DeviceHub] Audio-In index %s unbrauchbar (max_input_channels=%s, name=%r) -> fallback auf NAME/default/first-input',
                        in_idx, mi, nm,
                    )
                    in_idx = None

            if in_idx is None:
                in_idx = self._pick_device_index(self.audio_in_name, want_input=True)
                if in_idx is not None and not _input_device_ok(int(in_idx)):
                    in_idx = None

            if in_idx is None:
                try:
                    self._force_audio_hostapi()
                    di = sd.default.device[0]  # type: ignore[attr-defined]
                    if isinstance(di, int) and di >= 0:
                        in_idx = int(di)
                except Exception as e:
                    log_suppressed(LOG, key="device_hub.pass.11", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

            if in_idx is not None and not _input_device_ok(int(in_idx)):
                in_idx = None

            if in_idx is None:
                try:
                    devs = sd.query_devices()

                    # -----------------------------------------------------------------
                    # PRODUKTIONSFIX (Headless): Sinnvollen Input-Device-Fallback wählen
                    # -----------------------------------------------------------------
                    # Hintergrund:
                    #   Wenn NAME/INDEX/PortAudio-default fehlschlagen, wurde früher
                    #   einfach "erstes Input-Device" genommen. Je nach System kann
                    #   das ein Digital-/SPDIF-Eingang sein → Aufnahme klingt wie Rauschen.
                    #
                    # Lösung:
                    #   Wir bewerten alle Input-Geräte und bevorzugen:
                    #     - USB / Jabra
                    #     - sysdefault/default (statt spdif/iec958/hdmi)
                    #   ohne das bestehende Verhalten (NAME/INDEX) zu ändern.
                    # -----------------------------------------------------------------
                    def _score_input_dev(d: dict) -> int | None:
                        try:
                            name = str(d.get("name", "") or "").lower().strip()
                            mi = int(d.get("max_input_channels", 0) or 0)
                            need_ch = max(1, int(getattr(self, "audio_ch", 1) or 1))
                            if mi < need_ch:
                                return None

                            score = 0
                            bad_tokens = ("spdif", "iec958", "hdmi", "digital")
                            if any(t in name for t in bad_tokens):
                                score -= 500

                            if "jabra" in name:
                                score += 400
                            if "usb" in name:
                                score += 220

                            # virtuelle/default ALSA devices sind oft "ok"
                            if "sysdefault" in name or name == "default":
                                score += 60

                            # leichte Präferenz für "mehr Kanäle" (aber nicht dominierend)
                            score += min(mi, 64)
                            return score
                        except Exception:
                            return None

                    best_idx = None
                    best_score = -10**9
                    for k, d in enumerate(devs):
                        sc = _score_input_dev(d if isinstance(d, dict) else {})
                        if sc is None:
                            continue
                        if sc > best_score:
                            best_score = sc
                            best_idx = int(k)

                    in_idx = best_idx
                except Exception:
                    in_idx = None

            self._in_dev_idx = in_idx

            if self._in_dev_idx is None:
                msg = (
                    "Audio-In: kein Input-Device gefunden (PortAudio default ungültig). "
                    "Bitte OROMA_AUDIO_INPUT_INDEX oder OROMA_AUDIO_INPUT_NAME setzen. "
                    "(Debug: arecord -l / arecord -L)"
                )
                LOG.error(msg)
                _audit("audio", "mic_start_fail", error=msg, client=client)
                self._mic_last_error = msg
                self._mic_last_error_ts = time.time()
                return False

            dev_name_actual = self._device_name_by_index(self._in_dev_idx)
            if self.audio_in_name:
                LOG.info(
                    "Audio-In: match name='%s' -> index=%s (%s)",
                    self.audio_in_name,
                    self._in_dev_idx,
                    dev_name_actual,
                )
            else:
                LOG.info(
                    "Audio-In: nutze index=%s (%s)",
                    self._in_dev_idx,
                    dev_name_actual,
                )

            # --------------------------------------------------------------
            # Samplerate-Handling
            # --------------------------------------------------------------

            # --------------------------------------------------------------
            # Samplerate-Handling (FIX: korrekt eingerückt / kein SyntaxError)
            # --------------------------------------------------------------
            # Hintergrund:
            #   In einem vorherigen Patch ist dieser Block versehentlich aus der
            #   Methode start_mic() heraus dedentet worden. Das führt zu einem
            #   SyntaxError ("expected 'except' or 'finally' block") und macht
            #   DeviceHub + Audio-Blueprint komplett unbenutzbar (→ /audio 404).
            #
            # Ziel:
            #   1) Immer syntaktisch sauber bleiben (alles innerhalb des try-Blocks).
            #   2) USB-Headsets robust öffnen: bevorzugt stabile Open-SRs testen
            #      (z.B. 48000/44100/32000) und im Callback auf self.audio_sr
            #      downsamplen.
            #
            # Optional per ENV:
            #   - OROMA_AUDIO_OPEN_SR_MODE=auto|device|target
            #   - OROMA_AUDIO_OPEN_SR=<int>                 (Override, zuerst probieren)
            #   - OROMA_AUDIO_OPEN_SR_PROBE="48000,44100,32000"
            # --------------------------------------------------------------

            def _try_open(open_sr: int) -> "sd.InputStream":
                bs = int(max(16, open_sr * self.audio_block_ms / 1000))
                return sd.InputStream(
                    samplerate=int(open_sr),
                    channels=int(self.audio_ch),
                    dtype="float32",
                    blocksize=bs,
                    callback=self._audio_callback,
                    device=self._in_dev_idx,
                )

            mode = (os.environ.get("OROMA_AUDIO_OPEN_SR_MODE", "auto") or "auto").strip().lower()
            override_sr = (os.environ.get("OROMA_AUDIO_OPEN_SR", "") or "").strip()
            override_sr_i: Optional[int] = None
            if override_sr:
                try:
                    override_sr_i = int(float(override_sr))
                except Exception:
                    override_sr_i = None

            target_sr = int(self.audio_sr)

            dev_sr: Optional[int] = None
            try:
                dinfo = sd.query_devices(self._in_dev_idx)
                dev_sr = int(float((dinfo or {}).get("default_samplerate", 0) or 0))
                if dev_sr <= 0:
                    dev_sr = None
            except Exception:
                dev_sr = None

            cands: list[int] = []
            if override_sr_i and override_sr_i > 0:
                cands.append(int(override_sr_i))

            # Probe-Liste: stabilere Open-SRs zuerst testen (wenn target typ. 16k)
            probe_raw = (os.environ.get("OROMA_AUDIO_OPEN_SR_PROBE", "") or "").strip()
            probe: list[int] = []
            if probe_raw:
                for part in probe_raw.split(","):
                    p = part.strip()
                    if not p:
                        continue
                    try:
                        v = int(float(p))
                    except Exception:
                        continue
                    if v > 0:
                        probe.append(v)
            else:
                if target_sr > 0 and target_sr <= 16000:
                    probe = [48000, 44100, 32000]

            for v in probe:
                if v > 0 and v not in cands:
                    cands.append(v)

            if mode == "target":
                if target_sr > 0:
                    cands.append(target_sr)
                if dev_sr and dev_sr != target_sr:
                    cands.append(dev_sr)
            elif mode == "device":
                if dev_sr:
                    cands.append(dev_sr)
                if target_sr > 0 and (not dev_sr or target_sr != dev_sr):
                    cands.append(target_sr)
            else:
                # auto: device-default zuerst (wenn abweichend), dann target
                if dev_sr and dev_sr != target_sr:
                    cands.append(dev_sr)
                if target_sr > 0:
                    cands.append(target_sr)

            # dedupe, preserve order
            seen = set()
            cands2: list[int] = []
            for s in cands:
                if s in seen:
                    continue
                seen.add(s)
                cands2.append(int(s))
            if not cands2:
                cands2 = [target_sr]

            last_err: Optional[Exception] = None
            stream = None
            self._mic_open_sr = target_sr
            for s in cands2:
                try:
                    self._mic_open_sr = int(s)
                    stream = _try_open(int(s))
                    break
                except Exception as e:
                    last_err = e
                    continue
            if stream is None:
                raise (last_err or RuntimeError("Mic open failed (unknown)"))


            self._mic_stream = stream
            self._mic_stream.start()

            LOG.info(
                "Mic gestartet (target_sr=%d open_sr=%s ch=%d dev=%s)",
                int(self.audio_sr),
                int(self._mic_open_sr or self.audio_sr),
                int(self.audio_ch),
                dev_name_actual or str(self._in_dev_idx),
            )
            _audit(
                "audio",
                "mic_start",
                sr=int(self.audio_sr),
                ch=int(self.audio_ch),
                dev_index=self._in_dev_idx,
                dev_name=dev_name_actual,
                mic_open_sr=int(self._mic_open_sr or self.audio_sr),
                client=client,
            )
            return True

        except Exception as e:
            self._mic_last_error = str(e)
            self._mic_last_error_ts = time.time()
            LOG.error("Mic konnte nicht gestartet werden: %s", e)
            _audit("audio", "mic_start_fail", error=str(e), client=client)
            self._mic_stream = None
            return False

    def stop_mic(self, client: Optional[str] = None) -> None:
        """Stoppt den Mikrofonstream."""
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception as e:
                log_suppressed(LOG, key="device_hub.pass.12", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            self._mic_stream = None
            LOG.info("Mic gestoppt.")
            _audit("audio", "mic_stop", client=client)

    def get_audio_level(self) -> float:
        """RMS-Level des letzten Blocks (0..1)."""
        return float(self._lvl_val)

    def _concat_ring(self) -> np.ndarray:
        with self._audio_lock:
            if self._ring_np_cache is not None:
                return self._ring_np_cache
            if not self._ring:
                self._ring_np_cache = np.zeros((0,), dtype=np.float32)
            else:
                self._ring_np_cache = (
                    np.concatenate(list(self._ring), dtype=np.float32) if len(self._ring) > 1 else self._ring[0].copy()
                )
            return self._ring_np_cache

    def read_audio(self, seconds: float, client: Optional[str] = None) -> np.ndarray:
        """
        Liefert bis zu 'seconds' Sekunden Mono-PCM (float32, [-1,1]) aus dem Ringbuffer.
        Wenn weniger vorhanden, wird nur das geliefert, was vorliegt.
        """
        if not self._require_audio():
            return np.zeros((0,), dtype=np.float32)
        if self._mic_stream is None:
            self.start_mic(client=client)
            time.sleep(max(0.0, self.audio_block_ms / 1000.0))
        buf = self._concat_ring()
        need = int(max(0, seconds) * self.audio_sr)
        if need <= 0 or buf.size == 0:
            return np.zeros((0,), dtype=np.float32)
        out = buf[-need:] if buf.size >= need else buf.copy()
        _audit_throttled("read_audio", 2.0, "audio", "read", seconds=seconds, samples=int(out.size))
        return out

    def record_wav(self, seconds: float, sr: Optional[int] = None, client: Optional[str] = None, gain_db: Optional[object] = None) -> bytes:
        """
        Nimmt bis zu 'seconds' Sekunden auf (aus Ringbuffer) und liefert WAV-Bytes (PCM16 mono).
        Hinweis: nutzt den aktuellen Ring – für exakte Aufnahme vorher kurze Wartezeit.
        """
        sr = int(sr or self.audio_sr)
        pcm = self.read_audio(seconds, client=client)

        # ---------------------------------------------------------------------
        # ORÓMA Audio – Warmup gegen „record_empty“ direkt nach mic_start
        # ---------------------------------------------------------------------
        # In der Praxis (USB Headsets / ALSA / scheduler jitter) kann der Ring-
        # Buffer in den ersten ~50–300ms nach start_mic() noch leer sein.
        # Dadurch liefert /audio/api/wav?sec=... kurzfristig 503 („no audio“),
        # obwohl das Mikro korrekt startet (siehe devicehub_audit + Logs).
        #
        # Wir warten daher *kurz* auf die ersten Samples, ohne den Request
        # unnötig zu blockieren. Sobald Samples da sind, wird wie gewohnt
        # „bis zu seconds“ aus dem Ring geliefert (notfalls weniger, falls
        # noch nicht genug Ring gefüllt ist).
        #
        # Steuerung:
        #   OROMA_AUDIO_RECORD_WARMUP_SEC  (Default: 0.35s, Range 0..2s)
        # ---------------------------------------------------------------------
        if pcm.size == 0:
            try:
                warm = float(os.environ.get("OROMA_AUDIO_RECORD_WARMUP_SEC", "0.35") or "0.35")
            except Exception:
                warm = 0.35
            warm = max(0.0, min(2.0, warm))

            if warm > 0.0:
                deadline = time.time() + warm
                while time.time() < deadline:
                    time.sleep(0.05)
                    pcm = self.read_audio(seconds, client=client)
                    if pcm.size > 0:
                        break

        if pcm.size == 0:
            _audit("audio", "record_empty", seconds=seconds)
            return b""
        # ---------------------------------------------------------------------
        # ORÓMA Audio – Optional Gain (dB) vor PCM16-Encoding
        # ---------------------------------------------------------------------
        # Hinweis: Gain wird als dB interpretiert (0.0 = unity).
        # ENV:
        #   OROMA_AUDIO_GAIN   (float, dB)  default=0.0   clamp [-24..+24]
        # Zusätzlich kann gain_db (z.B. aus UI-Query) übergeben werden.
        try:
            _gdb = float(gain_db) if gain_db is not None and str(gain_db).strip() != "" else float(os.environ.get("OROMA_AUDIO_GAIN", "0.0") or "0.0")
        except Exception:
            _gdb = 0.0
        _gdb = max(-24.0, min(24.0, _gdb))
        _g = float(10.0 ** (_gdb / 20.0))
        pcm_g = pcm * _g if _g != 1.0 else pcm
        x = np.clip(pcm_g, -1.0, 1.0)
        i16 = (x * 32767.0).astype(np.int16)
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(i16.tobytes())
        data = bio.getvalue()
        _audit("audio", "record_wav", seconds=seconds, bytes=len(data), sr=sr)
        return data

    def play_pcm(self, pcm: np.ndarray, sr: Optional[int] = None, client: Optional[str] = None) -> bool:
        """Spielt Mono-PCM float32 [-1,1] ab (falls Audio aktiv)."""
        if not self._require_audio():
            return False
        now = time.time()
        if now < float(getattr(self, "_playback_blocked_until_ts", 0.0) or 0.0):
            _audit("audio", "play_skip_blocked", until=float(self._playback_blocked_until_ts), client=client)
            return False
        try:
            sr = int(sr or self.audio_sr)
            if pcm.ndim != 1:
                pcm = pcm.reshape(-1)
            self._out_dev_idx = self._resolve_output_device_index()
            dev_name_actual = self._device_name_by_index(self._out_dev_idx)
            if self._out_dev_idx is None:
                self._playback_blocked_until_ts = time.time() + 60.0
                log_suppressed(
                    LOG,
                    key="device_hub.playback.no_output",
                    msg="Playback deaktiviert: kein valides Output-Device gefunden",
                    level=logging.WARNING,
                    interval_s=60,
                )
                _audit("audio", "play_skip_no_output", sr=sr, client=client)
                return False
            sd.play(
                pcm.astype(np.float32),
                samplerate=sr,
                device=int(self._out_dev_idx),
                blocking=True,
            )
            _audit(
                "audio",
                "play_pcm",
                samples=int(pcm.size),
                sr=sr,
                dev_index=self._out_dev_idx,
                dev_name=dev_name_actual,
                client=client,
            )
            return True
        except Exception as e:
            msg = str(e)
            self._out_dev_idx = None
            if ("device -1" in msg) or ("channelCount" in msg) or ("ValidateParameters" in msg) or ("Error querying device" in msg):
                self._playback_blocked_until_ts = time.time() + 60.0
            log_suppressed(LOG, key="device_hub.playback.fail", msg="Playback-Fehler", exc=e, level=logging.ERROR, interval_s=60)
            _audit("audio", "play_fail", error=msg)
            return False

    def play_wav(self, wav_bytes: bytes, client: Optional[str] = None) -> bool:
        """Spielt WAV-Bytes (PCM16 mono) ab."""
        if not self._require_audio():
            return False
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sampw = wf.getsampwidth()
                data = wf.readframes(wf.getnframes())
            if sampw != 2:
                LOG.warning("WAV nicht PCM16 – konvertiere grob.")
            if ch > 1:
                arr = np.frombuffer(data, dtype=np.int16).reshape(-1, ch)
                mono = arr.mean(axis=1).astype(np.int16)
            else:
                mono = np.frombuffer(data, dtype=np.int16)
            pcm = (mono.astype(np.float32) / 32767.0)
            ok = self.play_pcm(pcm, sr=sr, client=client)
            if ok:
                _audit(
                    "audio",
                    "play_wav_meta",
                    sr=sr,
                    ch=ch,
                    seconds=round(len(mono) / float(sr or 1), 3),
                    client=client,
                )
            return ok
        except Exception as e:
            LOG.error("play_wav Fehler: %s", e)
            _audit("audio", "play_wav_fail", error=str(e))
            return False

    # -------------------------------------------------------------------------
    # Status / Zusammenfassung
    # -------------------------------------------------------------------------
    # PTZ (optional) – V4L2 Controls via v4l2-ctl
    # -------------------------------------------------------------------------
    def _get_ptz(self):
        """Lazy-Init PTZ-Controller.

        Wichtig:
          - Fail-Closed: Ohne OROMA_PTZ_DEVICE wird KEIN PTZ initialisiert.
          - v4l2-ctl muss vorhanden sein (wrapper kümmert sich um supported=False).
          - Dieser Code darf niemals Exceptions nach außen werfen.
        """
        if not self._ptz_device:
            return None
        with self._ptz_lock:
            if self._ptz is not None:
                # Wenn PTZ beim ersten Init in einem transienten Zustand landet
                # (typisch: v4l2-ctl liefert leere Ausgabe während Stream offen ist),
                # wollen wir nach kurzer Zeit einen Re-Init versuchen, statt dauerhaft
                # "supported=False" zu kleben.
                try:
                    if not bool(getattr(self._ptz, "supported", lambda: False)()):
                        le = str(getattr(self._ptz, "last_error", "") or "").lower()
                        if "empty output" in le and (time.time() - float(getattr(self, "_ptz_last_init_ts", 0.0))) > 15.0:
                            self._ptz = None
                        else:
                            return self._ptz
                    else:
                        return self._ptz
                except Exception:
                    return self._ptz
            try:
                from wrappers.ptz_controller import PTZController  # type: ignore
                self._ptz_last_init_ts = time.time()
                self._ptz = PTZController(self._ptz_device)
                # -------------------------------------------------------------
                # PTZ AUDIT (DeviceHub)
                # -------------------------------------------------------------
                # Ziel: Keine stillen PTZ-Zustände.
                #   - Wir schreiben ein Audit-Event, sobald PTZ lazy-init
                #     versucht wurde.
                #   - Das landet in logs/devicehub_audit.log und ist damit auch
                #     ohne Flask/UI-Logs sichtbar.
                #
                # Hinweis:
                #   supported() kann False sein, obwohl das Objekt existiert
                #   (z.B. fehlende Controls). Das ist für Diagnose wichtig.
                # -------------------------------------------------------------
                try:
                    _audit("ptz", "init", device=self._ptz_device, supported=bool(getattr(self._ptz, "supported", lambda: False)()))
                except Exception:
                    pass
                # kein Audit-Spam: nur einmal loggen
                try:
                    LOG.info("[DeviceHub] PTZ init: device=%s supported=%s", self._ptz_device, bool(getattr(self._ptz, "supported", lambda: False)()))
                except Exception:
                    LOG.info("[DeviceHub] PTZ init: device=%s", self._ptz_device)
            except Exception as e:
                # Fail-Closed
                self._ptz = None
                LOG.warning("[DeviceHub] PTZ init fehlgeschlagen: %s", e)
                try:
                    _audit_throttled("ptz_init_fail", 30.0, "ptz", "init_fail", device=self._ptz_device, error=str(e))
                except Exception:
                    pass
            return self._ptz

    def _autodetect_ptz_device(self) -> str:
        """
        Auto-detect a PTZ-capable V4L2 device when OROMA_PTZ_DEVICE is not set.

        Safety / conservatism:
          - Only returns a device if exactly ONE PTZ-capable candidate is found.
          - PTZ-capable means v4l2-ctl reports at least pan + tilt controls, and ideally zoom.
          - Prefers /dev/v4l/by-id/*video-index0* over /dev/video*.
          - If ambiguous or none found, returns empty string (fail-closed).

        This is primarily a robustness feature for orchestrator/CLI runs where the full
        systemd Environment/EnvironmentFile might not be present.
        """
        try:
            # 1) Prefer stable by-id symlinks (best for reboot stability)
            cand = []
            cand += sorted(glob.glob("/dev/v4l/by-id/*video-index0*"))
            # 2) Fallback: raw /dev/video* (less stable; only used if unique)
            cand += sorted(glob.glob("/dev/video*"))

            # De-dup while preserving order
            seen = set()
            candidates = []
            for c in cand:
                if c not in seen:
                    seen.add(c)
                    candidates.append(c)

            ptz_ok = []
            for dev in candidates:
                # v4l2-ctl returns non-zero for non-V4L2 devices; ignore failures quietly here.
                try:
                    p = subprocess.run(
                        ["v4l2-ctl", "-d", dev, "-L"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=2.0,
                    )
                except Exception:
                    continue
                if p.returncode != 0:
                    continue
                out = (p.stdout or "").lower()

                # Heuristic: typical UVC PTZ control names
                has_pan = ("pan_absolute" in out) or ("pan_relative" in out)
                has_tilt = ("tilt_absolute" in out) or ("tilt_relative" in out)
                has_zoom = ("zoom_absolute" in out) or ("zoom_relative" in out) or ("zoom_continuous" in out)

                if has_pan and has_tilt:
                    ptz_ok.append((dev, has_zoom))

            if not ptz_ok:
                return ""

            # Prefer devices with zoom if available.
            ptz_ok_sorted = sorted(ptz_ok, key=lambda x: (not x[1], x[0]))
            # If more than one PTZ-capable device exists, stay fail-closed.
            # (This avoids picking the wrong camera in multi-webcam setups.)
            unique_devices = [d for d, _ in ptz_ok_sorted]
            if len(unique_devices) != 1:
                self._log.warning("[DeviceHub] PTZ auto-detect: ambiguous (%d candidates): %s", len(unique_devices), unique_devices)
                return ""

            return unique_devices[0]
        except Exception as e:
            self._log.warning("[DeviceHub] PTZ auto-detect failed: %s", e)
            return ""
    def ptz_status(self) -> Dict[str, Any]:
        """Kompakter PTZ-Status für UI/API.

        Rückgabe ist stabil (immer dict) und crash-sicher.
        """
        # ------------------------------------------------------------------
        # PTZ AUDIT (Status)
        # ------------------------------------------------------------------
        # ptz_status() wird von UI/Health häufig gepollt.
        # Wir auditieren deshalb nur throttled, um Logs nicht zu fluten.
        # ------------------------------------------------------------------
        try:
            if not self._ptz_device:
                _audit_throttled("ptz_status_disabled", 60.0, "ptz", "status", supported=False, reason="OROMA_PTZ_DEVICE not set")
        except Exception:
            pass
        if not self._ptz_device:
            return {
                "ok": True,
                "supported": False,
                "device": "",
                "reason": "OROMA_PTZ_DEVICE not set",
            }
        ptz = self._get_ptz()
        if ptz is None:
            return {
                "ok": False,
                "supported": False,
                "device": self._ptz_device,
                "error": "PTZ init failed",
            }
        try:
            st = ptz.status()  # type: ignore
        except Exception as e:
            return {
                "ok": False,
                "supported": False,
                "device": self._ptz_device,
                "error": str(e),
            }

        # ------------------------------------------------------------------
        # PTZ ATTENTION STATE MERGE (Backend → Hub → UI)
        # ------------------------------------------------------------------
        # Der PTZ-Attention-Loop schreibt seinen aktuellen Entscheidungszustand
        # best-effort in data/state/ptz_attention_state.json. Die Video-UI soll
        # Modus, letzte Aktion und Grund anzeigen koennen, ohne direkt den Loop
        # oder dessen Logs zu parsen. Deshalb liest der Hub diesen kleinen State
        # fail-closed ein und merged nur stabile Felder in den PTZ-Status.
        #
        # WICHTIG:
        # - Niemals PTZ status wegen eines State-Lesefehlers failen lassen.
        # - Nur additive Felder mergen; bestehende PTZ-Rohdaten bleiben unberuehrt.
        # - Keine harte Abhaengigkeit von core.ptz_attention_loop importieren,
        #   um Rekursionen / Seiteneffekte im Hub zu vermeiden.
        # ------------------------------------------------------------------
        try:
            state_path = os.environ.get(
                "OROMA_PTZ_ATTENTION_STATE_PATH",
                "/opt/ai/oroma/data/state/ptz_attention_state.json",
            )
            with open(state_path, "r", encoding="utf-8") as f:
                att = json.load(f)
            if isinstance(att, dict):
                mode = str(att.get("mode") or "").strip()
                action = str(att.get("action") or "").strip()
                reason = str(att.get("reason") or att.get("last_reason") or "").strip()
                ts = int(att.get("ts") or 0)
                moved = bool(att.get("moved"))
                if mode:
                    st["mode"] = mode
                    st["ptz_mode"] = mode
                if action:
                    st["last_action"] = action
                if reason:
                    st["last_reason"] = reason
                if ts > 0:
                    st["last_ts"] = ts
                    try:
                        st["last_age_sec"] = max(0, int(time.time()) - int(ts))
                    except Exception:
                        pass
                st["last_moved"] = bool(moved)
                st["last_decision"] = {
                    "mode": mode or None,
                    "action": action or None,
                    "reason": reason or None,
                    "ts": ts if ts > 0 else None,
                    "moved": bool(moved),
                }
                try:
                    src = str(att.get("frame_source") or "").strip()
                    if src:
                        st["attention_frame_source"] = src
                    age = att.get("frame_age_sec")
                    if age is not None:
                        st["attention_frame_age_sec"] = float(age)
                except Exception:
                    pass
        except Exception:
            pass
        return st

    def ptz_center(self) -> bool:
        """Pan/Tilt auf Default/Center setzen (best-effort)."""
        ptz = self._get_ptz()
        if ptz is None:
            return False
        try:
            ok = bool(ptz.center())  # type: ignore
            try:
                _audit("ptz", "center", device=self._ptz_device, ok=bool(ok))
            except Exception:
                pass
            return bool(ok)
        except Exception as e:
            LOG.warning("[DeviceHub] PTZ center failed: %s", e)
            try:
                _audit_throttled("ptz_center_fail", 10.0, "ptz", "center_fail", device=self._ptz_device, error=str(e))
            except Exception:
                pass
            return False


    def ptz_command(self, action: str, amount: int = 1) -> Dict[str, Any]:
        """Führt ein PTZ Kommando aus.

        Supported Actions (Phase-1 safe):
          - center
          - left/right/up/down   (nudge, amount=steps)
          - zoom_in/zoom_out     (zoom, amount=steps)

        Rückgabe ist ein dict (für UI/API), niemals Exception.

        PRODUKTIONSREGEL: Keine „stillen“ PTZ-Fehler
        -------------------------------------------
        Diese Methode schreibt sowohl:
          • strukturierte Audit-Events (devicehub_audit.log)
          • klassische Logs (service.err.log)
        damit man jederzeit unterscheiden kann:
          - UI hat nicht gesendet (kein cmd_in Audit)
          - UI gesendet, Hub hat gesehen (cmd_in), aber PTZ unsupported/fehlgeschlagen
          - PTZ ok (cmd_out ok=true) + Statuswerte (pan/tilt/zoom)
        """
        action = (action or "").strip().lower()
        try:
            amount_i = int(amount)
        except Exception:
            amount_i = 1

        # ------------------------------------------------------------------
        # PTZ AUDIT (Command In)
        # ------------------------------------------------------------------
        try:
            _audit("ptz", "cmd_in", device=self._ptz_device, action=action, amount=amount_i)
        except Exception:
            pass
        try:
            LOG.info("[DeviceHub] PTZ cmd_in: action=%s amount=%s device=%s", action, amount_i, (self._ptz_device or ""))
        except Exception:
            pass

        if action in ("center", "reset"):
            ok = self.ptz_center()
            st = self.ptz_status()
            err = ""
            if not ok:
                try:
                    err = str((st or {}).get("last_error") or (st or {}).get("error") or "")
                except Exception:
                    err = ""
                if not err:
                    err = "PTZ center failed"
            try:
                _audit("ptz", "cmd_out", device=self._ptz_device, action="center", amount=0, ok=bool(ok), error=(err or None))
            except Exception:
                pass
            out = {"ok": bool(ok), "action": "center", "amount": 0, "status": st}
            if not ok:
                out["error"] = err
            return out

        ptz = self._get_ptz()
        if ptz is None:
            try:
                _audit("ptz", "cmd_out", device=self._ptz_device, action=action, amount=amount_i, ok=False, error="PTZ not available")
            except Exception:
                pass
            return {"ok": False, "action": action, "amount": amount_i, "error": "PTZ not available", "status": self.ptz_status()}

        try:
            if action in ("left", "right", "up", "down"):
                ok = bool(ptz.nudge(action, steps=amount_i))  # type: ignore
                st = self.ptz_status()
                err = ""
                did_retry = False

                if not ok:
                    # Kein stiller Fehler: last_error aus Status propagieren
                    try:
                        err = str((st or {}).get("last_error") or (st or {}).get("error") or "")
                    except Exception:
                        err = ""
                    if not err:
                        err = "PTZ move failed"

                    # Fallback: Einige UVC-Kameras blocken v4l2-ctl Controls, solange OpenCV das Device hält.
                    # Wenn wir genau diesen Fehler sehen, pausieren wir kurz die Kamera und versuchen einmal erneut.
                    busy_hint = ("cannot open device" in err.lower()) or ("device or resource busy" in err.lower()) or ("busy" in err.lower())
                    if busy_hint and bool(getattr(self, '_cap_run', False)):
                        did_retry = True
                        try:
                            _audit("ptz", "cam_pause_retry_try", device=self._ptz_device, action=action, amount=amount_i, error=err)
                        except Exception:
                            pass
                        try:
                            LOG.warning('[DeviceHub] PTZ busy-hint → pausiere Kamera für Retry (action=%s amount=%s err=%s)', action, amount_i, err)
                        except Exception:
                            pass

                        # Best-effort: stop/start Kamera, ohne Exceptions nach außen
                        try:
                            self.stop()
                        except Exception as e:
                            try:
                                _audit("ptz", "cam_pause_retry_stop_fail", device=self._ptz_device, error=str(e))
                            except Exception:
                                pass
                        try:
                            import time as _t
                            _t.sleep(0.25)
                        except Exception:
                            pass

                        try:
                            ok2 = bool(ptz.nudge(action, steps=amount_i))  # type: ignore
                        except Exception as e:
                            ok2 = False
                            err = str(e)
                        ok = bool(ok2)
                        st = self.ptz_status()
                        if not ok:
                            try:
                                err = str((st or {}).get("last_error") or (st or {}).get("error") or err or "")
                            except Exception:
                                pass
                            if not err:
                                err = "PTZ move failed (after retry)"

                        try:
                            self.start()
                        except Exception as e:
                            # Kamera-Restart-Fehler muss sichtbar sein, PTZ Ergebnis liefern wir trotzdem zurück.
                            try:
                                _audit("ptz", "cam_pause_retry_start_fail", device=self._ptz_device, error=str(e))
                            except Exception:
                                pass

                        try:
                            _audit("ptz", "cam_pause_retry_done", device=self._ptz_device, action=action, ok=bool(ok), error=(err or None))
                        except Exception:
                            pass

                try:
                    _audit("ptz", "cmd_out", device=self._ptz_device, action=action, amount=amount_i, ok=bool(ok), error=(err or None), retry=bool(did_retry))
                except Exception:
                    pass

                out = {"ok": bool(ok), "action": action, "amount": amount_i, "status": st}
                if did_retry:
                    out["retry"] = True
                if not ok:
                    out["error"] = err
                return out

            if action in ("zoom_in", "zin"):
                ok = bool(ptz.zoom(abs(amount_i)))  # type: ignore
                st = self.ptz_status()
                err = ""
                did_retry = False

                if not ok:
                    try:
                        err = str((st or {}).get("last_error") or (st or {}).get("error") or "")
                    except Exception:
                        err = ""
                    if not err:
                        err = "PTZ zoom_in failed"

                    busy_hint = ("cannot open device" in err.lower()) or ("device or resource busy" in err.lower()) or ("busy" in err.lower())
                    if busy_hint and bool(getattr(self, '_cap_run', False)):
                        did_retry = True
                        try:
                            _audit("ptz", "cam_pause_retry_try", device=self._ptz_device, action="zoom_in", amount=abs(amount_i), error=err)
                        except Exception:
                            pass
                        try:
                            LOG.warning('[DeviceHub] PTZ busy-hint → pausiere Kamera für Retry (action=zoom_in amount=%s err=%s)', abs(amount_i), err)
                        except Exception:
                            pass
                        try:
                            self.stop()
                        except Exception as e:
                            try:
                                _audit("ptz", "cam_pause_retry_stop_fail", device=self._ptz_device, error=str(e))
                            except Exception:
                                pass
                        try:
                            import time as _t
                            _t.sleep(0.25)
                        except Exception:
                            pass
                        try:
                            ok2 = bool(ptz.zoom(abs(amount_i)))  # type: ignore
                        except Exception as e:
                            ok2 = False
                            err = str(e)
                        ok = bool(ok2)
                        st = self.ptz_status()
                        if not ok:
                            try:
                                err = str((st or {}).get("last_error") or (st or {}).get("error") or err or "")
                            except Exception:
                                pass
                            if not err:
                                err = "PTZ zoom_in failed (after retry)"
                        try:
                            self.start()
                        except Exception as e:
                            try:
                                _audit("ptz", "cam_pause_retry_start_fail", device=self._ptz_device, error=str(e))
                            except Exception:
                                pass
                        try:
                            _audit("ptz", "cam_pause_retry_done", device=self._ptz_device, action="zoom_in", ok=bool(ok), error=(err or None))
                        except Exception:
                            pass

                try:
                    _audit("ptz", "cmd_out", device=self._ptz_device, action="zoom_in", amount=abs(amount_i), ok=bool(ok), error=(err or None), retry=bool(did_retry))
                except Exception:
                    pass
                out = {"ok": bool(ok), "action": "zoom_in", "amount": abs(amount_i), "status": st}
                if did_retry:
                    out["retry"] = True
                if not ok:
                    out["error"] = err
                return out

            if action in ("zoom_out", "zout"):
                ok = bool(ptz.zoom(-abs(amount_i)))  # type: ignore
                st = self.ptz_status()
                err = ""
                did_retry = False

                if not ok:
                    try:
                        err = str((st or {}).get("last_error") or (st or {}).get("error") or "")
                    except Exception:
                        err = ""
                    if not err:
                        err = "PTZ zoom_out failed"

                    busy_hint = ("cannot open device" in err.lower()) or ("device or resource busy" in err.lower()) or ("busy" in err.lower())
                    if busy_hint and bool(getattr(self, '_cap_run', False)):
                        did_retry = True
                        try:
                            _audit("ptz", "cam_pause_retry_try", device=self._ptz_device, action="zoom_out", amount=abs(amount_i), error=err)
                        except Exception:
                            pass
                        try:
                            LOG.warning('[DeviceHub] PTZ busy-hint → pausiere Kamera für Retry (action=zoom_out amount=%s err=%s)', abs(amount_i), err)
                        except Exception:
                            pass
                        try:
                            self.stop()
                        except Exception as e:
                            try:
                                _audit("ptz", "cam_pause_retry_stop_fail", device=self._ptz_device, error=str(e))
                            except Exception:
                                pass
                        try:
                            import time as _t
                            _t.sleep(0.25)
                        except Exception:
                            pass
                        try:
                            ok2 = bool(ptz.zoom(-abs(amount_i)))  # type: ignore
                        except Exception as e:
                            ok2 = False
                            err = str(e)
                        ok = bool(ok2)
                        st = self.ptz_status()
                        if not ok:
                            try:
                                err = str((st or {}).get("last_error") or (st or {}).get("error") or err or "")
                            except Exception:
                                pass
                            if not err:
                                err = "PTZ zoom_out failed (after retry)"
                        try:
                            self.start()
                        except Exception as e:
                            try:
                                _audit("ptz", "cam_pause_retry_start_fail", device=self._ptz_device, error=str(e))
                            except Exception:
                                pass
                        try:
                            _audit("ptz", "cam_pause_retry_done", device=self._ptz_device, action="zoom_out", ok=bool(ok), error=(err or None))
                        except Exception:
                            pass

                try:
                    _audit("ptz", "cmd_out", device=self._ptz_device, action="zoom_out", amount=abs(amount_i), ok=bool(ok), error=(err or None), retry=bool(did_retry))
                except Exception:
                    pass
                out = {"ok": bool(ok), "action": "zoom_out", "amount": abs(amount_i), "status": st}
                if did_retry:
                    out["retry"] = True
                if not ok:
                    out["error"] = err
                return out

            try:
                _audit("ptz", "cmd_out", device=self._ptz_device, action=action, amount=amount_i, ok=False, error="unknown action")
            except Exception:
                pass
            return {"ok": False, "action": action, "amount": amount_i, "error": "unknown action", "status": self.ptz_status()}

        except Exception as e:
            # keine stille Exception
            try:
                _audit("ptz", "cmd_out", device=self._ptz_device, action=action, amount=amount_i, ok=False, error=str(e))
            except Exception:
                pass
            LOG.warning("[DeviceHub] PTZ command failed: action=%s amount=%s err=%s", action, amount_i, e)
            return {"ok": False, "action": action, "amount": amount_i, "error": str(e), "status": self.ptz_status()}

    def status(self) -> Dict[str, Any]:

        """Gibt eine kompakte Übersicht über den aktuellen Gerätezustand zurück."""
        cam_running = self._cam.running()
        last_frame_age = (time.time() - self._latest_ts) if self._latest_ts else None

        st: Dict[str, Any] = {
            "camera": {
                "backend": self.backend,
                "id": self._cam.id_string(),
                "running": cam_running,
                "last_frame_age": last_frame_age,
                "size": [self.w, self.h],
                "fps": self.fps,

                # =================================================================
                # BLOCK: EXTERNAL_FRAME_STATUS (SWAPPABLE)
                # Zweck:
                #   Sichtbarkeit im Status, ob Frames extern eingespeist werden.
                # =================================================================
                "external_source": self._external_source,
                "external_frames": self._external_frames,
                "external_last_ts": self._external_ts,
                "external_active": self._external_active(),
                "ok_by_frame": bool(last_frame_age is not None and last_frame_age <= float(os.environ.get('OROMA_VIDEO_FRAME_FRESH_SEC','10'))),
                # =================================================================
                # BLOCK: CAMERA_DIAG_STATUS (SWAPPABLE)
                # Zweck:
                #   Sichtbarkeit im UI/Health: warum kein Bild kommt (read_fail),
                #   ob Watchdog bereits restartet, und wann zuletzt ein OK-Frame war.
                # =================================================================
                "diag": {
                    "last_ok_ts": self._cam_last_ok_ts,
                    "fail_streak": int(self._cam_fail_streak),
                    "last_restart_ts": self._cam_restart_ts,
                    "restart_count": int(self._cam_restart_count),
                    "last_error": self._cam_last_error,
                    "last_error_ts": self._cam_last_error_ts,
                },
                # =================================================================
                # END BLOCK: CAMERA_DIAG_STATUS
                # =================================================================
                # =================================================================
                # END BLOCK: EXTERNAL_FRAME_STATUS
                # =================================================================
            },
            "light": {
                "source": self.light_source,
                "value": self._light_val,
                "state": self._light_state,
                "last_ts": self._light_ts,
            },
            "audio": {
                "enabled": bool(self.audio_enable and sd is not None),
                "in_name": self.audio_in_name,
                "out_name": self.audio_out_name,
                "sr": self.audio_sr,
                "ch": self.audio_ch,
                "mic_active": bool(self._mic_stream is not None),
                "level": self._lvl_val,

                # =================================================================
                # BLOCK: AUDIO_DEVICE_DEBUG (SWAPPABLE)
                # Zweck:
                #   Debug/UX: Sichtbar machen, welche Audio-Geräte *tatsächlich*
                #   durch DeviceHub ausgewählt wurden (Index + Name).
                #
                # Hintergrund:
                #   In Headless-Setups ist die wichtigste Frage oft:
                #     „Nimmt ORÓMA das richtige USB-Audio?“
                #   Diese Felder beantworten das sofort – ohne Log-Suche.
                #
                # Hinweis:
                #   - in_dev_* wird i. d. R. nach start_mic() gesetzt
                #   - out_dev_* wird nach erstem Playback gesetzt (oder wenn
                #     play_*() einen Default setzt)
                # =================================================================
                "in_dev_index": self._in_dev_idx,
                "out_dev_index": self._out_dev_idx,
                "in_dev_name": (
                    self._device_name_by_index(self._in_dev_idx)
                    if (sd is not None and self._in_dev_idx is not None)
                    else None
                ),
                "out_dev_name": (
                    self._device_name_by_index(self._out_dev_idx)
                    if (sd is not None and self._out_dev_idx is not None)
                    else None
                ),
                # =================================================================
                # END BLOCK: AUDIO_DEVICE_DEBUG
                # =================================================================
            },
            "sessions": self._sessions.copy(),
            "sensors": self.get_sensor_health(),  # neu: Sensor-Status
        }

        # PTZ (optional) – kompakter Status für UI/API
        st["ptz"] = self.ptz_status()
        return st


# -----------------------------------------------------------------------------
# Singleton-Facade
# -----------------------------------------------------------------------------
def get_hub() -> DeviceHub:
    """Bequemer Zugriff auf das Singleton."""
    return DeviceHub.instance()


# -----------------------------------------------------------------------------
# Selbsttest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Minimaler Selbsttest: Kamera anwerfen, Snapshot generieren, Light lesen, Audio-Geräte auflisten.
    hub = get_hub()
    LOG.info("Status (vor Start): %s", hub.status())
    # Kamera
    jpg = hub.get_latest_jpeg(client="selftest")
    if jpg:
        LOG.info("Snapshot bytes=%d", len(jpg))
    else:
        LOG.info("Kein Snapshot verfügbar (ggf. DummyCam).")
    # Light
    val = hub.get_light_level()
    LOG.info("Light-Level: %s", str(val))
    # Audio
    devs = hub.list_audio_devices()
    LOG.info("Audio-Geräte (kurz): input=%d, output=%d", len(devs.get("input", [])), len(devs.get("output", [])))
    LOG.info("Status (nach Tests): %s", hub.status())
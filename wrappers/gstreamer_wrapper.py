#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Pfad: /opt/ai/oroma/v2.11/wrappers/gstreamer_wrapper.py
# Abhängigkeiten:
#   - Python 3.9+
#   - GStreamer 1.18+ mit Python-GI (gir1.2-gst-1.0, python3-gi, python3-gi-cairo)
#   - Optional: OpenCV (python3-opencv) und NumPy (python3-numpy) für Bild-/Audio-Features
#   - (Raspberry Pi) gstreamer1.0-plugins-{base,good,bad,ugly}, gstreamer1.0-libav, v4l2-utils
#
# Zweck:
#   Produktiver GStreamer-Wrapper für ORÓMA v2.11:
#     - Video- und Audio-Streaming (RTSP/HTTP/File/USB/V4L2/ALSA)
#     - Appsink-Integration (Pull/Callback) -> NumPy-Arrays (Frames/Samples)
#     - Threaded MainLoop + Bus-Fehlerbehandlung, sauberes Start/Stop
#     - Dynamische Quellenumschaltung zur Laufzeit
#     - Feature-Hooks (Kanten/Histogramm für Video; RMS/FFT für Audio)
#     - Snapshot/Wave-Speicher, einfache Profiling-Zeiten
#
# Hinweis:
#   Dieser Wrapper ist „kernnah“ (keine Flask-Abhängigkeit). Er wird vom IO-Manager
#   bzw. Vision-/Audio-Pipelines in ORÓMA genutzt. Er kann auch standalone getestet werden:
#       python -m wrappers.gstreamer_wrapper --video /dev/video0 --width 640 --height 480 --fps 30
#       python -m wrappers.gstreamer_wrapper --audio hw:1,0 --rate 16000
#
# Sicherheit / Robustheit:
#   - Idempotentes start()/stop()
#   - Bus-Watch mit Fehler-Logging
#   - Graceful Shutdown bei EOS/Fehlern
#   - Zeitstempel & einfache Messwerte (Fps, Latenz grob)
#
# Lizenz: MIT (für ORÓMA-Projekt)

from __future__ import annotations
import os
import io
import sys
import time
import math
import queue
import struct
import logging
from core.log_guard import log_suppressed
import threading
from dataclasses import dataclass, field
from typing import Optional, Tuple, Callable, List

# --- Optional-Importe (für Features) ---
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

# --- GStreamer / GLib ---
try:
    import gi  # type: ignore
    gi.require_version("Gst", "1.0")
    gi.require_version("GObject", "2.0")
    from gi.repository import Gst, GObject, GLib  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError(
        "GStreamer/PyGObject nicht verfügbar. Installiere u.a.: "
        "sudo apt-get install -y python3-gi gir1.2-gst-1.0 gstreamer1.0-tools "
        "gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav"
    ) from e

# --- Logging ---
LOG = logging.getLogger("oroma.gstreamer")
if not LOG.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    LOG.addHandler(h)
    LOG.setLevel(logging.INFO)

# --- GStreamer initialisieren (einmal pro Prozess) ---
Gst.init(None)


# =============================================================================
# Hilfsfunktionen (Audio/Video Konvertierung)
# =============================================================================

def _caps_to_dict(caps: Gst.Caps) -> dict:
    """Konvertiert Caps in ein dict (nur erster Struktur-Eintrag)."""
    if not caps or caps.is_empty():
        return {}
    s = caps.get_structure(0)
    out = {"name": s.get_name()}
    for i in range(s.n_fields()):
        key = s.nth_field_name(i)
        out[key] = s.get_value(key)
    return out


def _gst_buffer_to_ndarray_video(sample: Gst.Sample) -> Optional["np.ndarray"]:
    """Extrahiert Videoframe (BGR) aus sample -> numpy.ndarray (H,W,3)."""
    if np is None:
        return None
    buf = sample.get_buffer()
    caps = sample.get_caps()
    info = _caps_to_dict(caps)
    try:
        width = int(info.get("width"))
        height = int(info.get("height"))
        fmt = info.get("format", "BGR")
    except Exception as e:
        log_suppressed(LOG, key="wrappers_gstreamer_wrapper.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

    # Wir unterstützen gängige RGB/BGR-Formate. Für NV12/I420 nutzen wir cv2.cvtColor.
    success, mapinfo = buf.map(Gst.MapFlags.READ)
    if not success:
        return None
    try:
        data = mapinfo.data
        if fmt in ("BGR", "RGB"):
            arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
            if fmt == "RGB":
                arr = arr[:, :, ::-1].copy()  # nach BGR
            return arr
        elif fmt in ("GRAY8",):
            arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width))
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR) if cv2 is not None else None
        elif fmt in ("I420", "YV12", "NV12", "NV21"):
            if cv2 is None:
                return None
            yuv = np.frombuffer(data, dtype=np.uint8)
            if fmt == "I420":
                yuv = yuv.reshape((int(height * 1.5), width))
                bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            elif fmt == "NV12":
                yuv = yuv.reshape((int(height * 1.5), width))
                bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12)
            elif fmt == "NV21":
                yuv = yuv.reshape((int(height * 1.5), width))
                bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV21)
            else:
                return None
            return bgr
        else:
            # Fallback: versuchen in BGRx umzuwandeln, wenn cv2 existiert
            return None
    finally:
        buf.unmap(mapinfo)


def _gst_buffer_to_ndarray_audio(sample: Gst.Sample) -> Tuple[Optional["np.ndarray"], int]:
    """Extrahiert Audiosamples (float32 mono) + Samplerate aus sample."""
    if np is None:
        return None, 0
    buf = sample.get_buffer()
    caps = sample.get_caps()
    info = _caps_to_dict(caps)
    rate = int(info.get("rate", 0))
    ch = int(info.get("channels", 1))
    fmt = info.get("format", "F32LE")

    success, mapinfo = buf.map(Gst.MapFlags.READ)
    if not success:
        return None, 0
    try:
        data = mapinfo.data
        if fmt in ("F32LE", "F32BE"):
            arr = np.frombuffer(data, dtype=np.float32)
        elif fmt in ("S16LE", "S16BE"):
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            return None, 0
        if ch > 1:
            arr = arr.reshape(-1, ch).mean(axis=1)  # auf mono
        return arr, rate
    finally:
        buf.unmap(mapinfo)


def _compute_video_features(frame_bgr: "np.ndarray") -> dict:
    """Einfache Video-Features: Kanten/HOG/Histo (Pi-tauglich)."""
    if np is None or cv2 is None or frame_bgr is None:
        return {}
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 150)
    hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten()
    hist = (hist / (hist.sum() + 1e-9)).astype(float)
    # Downscale HOG-ähnlich (sehr grob)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    feat = {
        "edges_mean": float(edges.mean()),
        "histogram_32": hist.tolist(),
        "downscale32": small.flatten().astype(float).tolist(),
        "w": int(frame_bgr.shape[1]),
        "h": int(frame_bgr.shape[0]),
    }
    return feat


def _compute_audio_features(samples: "np.ndarray", rate: int) -> dict:
    """Einfache Audio-Features: RMS, dominante Frequenz (FFT) (Pi-tauglich)."""
    if np is None or samples is None or rate <= 0:
        return {}
    rms = float(np.sqrt(np.mean(samples**2)))
    # FFT (nur grob, max-bin)
    N = min(len(samples), 4096)
    if N < 64:
        return {"rms": rms, "rate": rate}
    win = np.hanning(N)
    spec = np.fft.rfft(samples[:N] * win)
    mag = np.abs(spec)
    idx = int(np.argmax(mag[1:])) + 1
    dom_freq = float(idx * rate / N)
    return {"rms": rms, "rate": rate, "dom_freq": dom_freq}


# =============================================================================
# Video-Stream
# =============================================================================

@dataclass
class VideoConfig:
    source: str = "/dev/video0"   # RTSP/HTTP/File/V4L2
    width: int = 640
    height: int = 480
    fps: int = 30
    latency_ms: int = 100         # nur für RTSP relevant
    # Pipeline-Override (optional). Wenn gesetzt, überschreibt alle anderen Einstellungen.
    pipeline_launch: Optional[str] = None


class GStreamerVideo:
    """
    Video-Pipeline mit Appsink.
    Unterstützte Quellen:
      - V4L2 (/dev/videoX)
      - RTSP (rtsp://)
      - Dateien (file:/// oder Pfad)
      - HTTP/HTTPS Streams (sofern Plugins vorhanden)
    Ausgabe: BGR-Frames (NumPy) + Feature-Hook.
    """

    def __init__(
        self,
        config: VideoConfig,
        on_frame: Optional[Callable[[float, "np.ndarray", dict], None]] = None,
    ):
        """
        on_frame(ts, frame_bgr, features) wird bei jedem neuen Frame aufgerufen.
        """
        self.cfg = config
        self.on_frame = on_frame
        self._loop: Optional[GLib.MainLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._pipeline: Optional[Gst.Pipeline] = None
        self._appsink: Optional[Gst.Element] = None
        self._last_frame: Optional["np.ndarray"] = None
        self._last_ts: float = 0.0
        self._fps_counter = 0
        self._fps_last = time.time()
        self._running = threading.Event()

    # ---------------- Pipeline-Bau ----------------

    def _build_pipeline(self) -> Gst.Pipeline:
        if self.cfg.pipeline_launch:
            LOG.info("Video: nutze custom pipeline (gst-launch-Notation)")
            pipeline = Gst.parse_launch(self.cfg.pipeline_launch)
            return pipeline  # type: ignore

        src = self.cfg.source
        w, h, fps = self.cfg.width, self.cfg.height, self.cfg.fps

        if src.startswith("rtsp://"):
            # RTSP: rtspsrc -> depay -> decode -> convert -> scale -> caps -> appsink
            launch = (
                f"rtspsrc location={src} latency={self.cfg.latency_ms} ! "
                "rtpjitterbuffer ! rtph264depay ! h264parse ! avdec_h264 ! "
                "videoconvert ! videoscale ! "
                f"video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 ! "
                "appsink name=appsink emit-signals=true max-buffers=2 drop=true sync=false"
            )
        elif src.startswith("http://") or src.startswith("https://"):
            # HTTP/HTTPS Stream (z.B. MP4, MJPEG je nach Plugin)
            launch = (
                f"souphttpsrc location={src} ! decodebin ! "
                "videoconvert ! videoscale ! "
                f"video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 ! "
                "appsink name=appsink emit-signals=true max-buffers=2 drop=true sync=false"
            )
        elif src.startswith("file://") or os.path.isfile(src):
            # Datei
            if not src.startswith("file://"):
                src_uri = Gst.filename_to_uri(os.path.abspath(src))
            else:
                src_uri = src
            launch = (
                f"uridecodebin uri={src_uri} ! videoconvert ! videoscale ! "
                f"video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 ! "
                "appsink name=appsink emit-signals=true max-buffers=2 drop=true sync=false"
            )
        elif src.startswith("/dev/video"):
            # V4L2 Kamera
            launch = (
                f"v4l2src device={src} ! "
                f"video/x-raw,width={w},height={h},framerate={fps}/1 ! "
                "videoconvert ! videoscale ! "
                f"video/x-raw,format=BGR ! "
                "appsink name=appsink emit-signals=true max-buffers=2 drop=true sync=false"
            )
        else:
            # Versuch via decodebin (generisch)
            if os.path.exists(src):
                src_uri = Gst.filename_to_uri(os.path.abspath(src))
                launch = (
                    f"uridecodebin uri={src_uri} ! videoconvert ! videoscale ! "
                    f"video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 ! "
                    "appsink name=appsink emit-signals=true max-buffers=2 drop=true sync=false"
                )
            else:
                raise ValueError(f"Unbekannte Video-Quelle: {src}")

        LOG.info("Video-Pipeline:\n  gst-launch-1.0 %s", launch)
        pipeline: Gst.Pipeline = Gst.parse_launch(launch)  # type: ignore
        self._appsink = pipeline.get_by_name("appsink")
        if self._appsink is None:
            raise RuntimeError("appsink nicht gefunden")
        self._appsink.connect("new-sample", self._on_new_sample)  # type: ignore
        return pipeline

    # ---------------- Callbacks & Bus ----------------

    def _on_new_sample(self, sink):
        try:
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK
            frame = _gst_buffer_to_ndarray_video(sample)
            ts = time.time()
            if frame is not None:
                self._last_frame = frame
                self._last_ts = ts
                if self.on_frame:
                    feats = _compute_video_features(frame)
                    self.on_frame(ts, frame, feats)
                # fps grob
                self._fps_counter += 1
                if self._fps_counter >= 30:
                    now = time.time()
                    dt = now - self._fps_last
                    if dt > 0:
                        fps = self._fps_counter / dt
                        LOG.debug("Video FPS ~ %.2f", fps)
                    self._fps_counter = 0
                    self._fps_last = now
            return Gst.FlowReturn.OK
        except Exception as e:
            LOG.error("Video new-sample Fehler: %s", e)
            return Gst.FlowReturn.ERROR

    def _on_bus(self, bus: Gst.Bus, msg: Gst.Message):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            LOG.error("GStreamer ERROR: %s; debug=%s", err, debug)
            self.stop()
        elif t == Gst.MessageType.EOS:
            LOG.warning("GStreamer EOS (Video)")
            self.stop()
        elif t == Gst.MessageType.WARNING:
            w, debug = msg.parse_warning()
            LOG.warning("GStreamer WARNING: %s; debug=%s", w, debug)

    # ---------------- Steuerung ----------------

    def start(self):
        if self._running.is_set():
            return
        self._pipeline = self._build_pipeline()
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)

        self._loop = GLib.MainLoop()
        def _run():
            try:
                self._pipeline.set_state(Gst.State.PLAYING)
                self._loop.run()
            finally:
                self._pipeline.set_state(Gst.State.NULL)

        self._running.set()
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        LOG.info("Video gestartet: %s", self.cfg.source)

    def stop(self):
        if not self._running.is_set():
            return
        self._running.clear()
        try:
            if self._loop is not None:
                self._loop.quit()
        except Exception as e:
            log_suppressed(LOG, key="wrappers_gstreamer_wrapper.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._loop = None
        self._pipeline = None
        LOG.info("Video gestoppt")

    def is_running(self) -> bool:
        return self._running.is_set()

    def snapshot(self, path: str) -> bool:
        """Speichert letztes Frame als PNG/JPG (Dateiendung)."""
        if cv2 is None or self._last_frame is None:
            return False
        ok = cv2.imwrite(path, self._last_frame)
        LOG.info("Snapshot %s: %s", path, ok)
        return bool(ok)

    def get_latest(self) -> Tuple[Optional["np.ndarray"], float]:
        """Gibt (Frame, Timestamp) zurück (kopiert NICHT, numpy-View!)."""
        return self._last_frame, self._last_ts

    def reconfigure(self, **kwargs):
        """Laufzeit-Konfiguration anpassen (Source/Größe/FPS). Start/Stop wird automatisch gehandhabt."""
        was_running = self.is_running()
        if was_running:
            self.stop()
        for k, v in kwargs.items():
            if hasattr(self.cfg, k):
                setattr(self.cfg, k, v)
        if was_running:
            self.start()


# =============================================================================
# Audio-Stream
# =============================================================================

@dataclass
class AudioConfig:
    source: str = "default"        # ALSA device ("hw:1,0" etc.) oder "autoaudiosrc"
    rate: int = 16000
    channels: int = 1
    chunk_ms: int = 40             # ~640 samples bei 16 kHz
    pipeline_launch: Optional[str] = None


class GStreamerAudio:
    """
    Audio-Pipeline mit Appsink.
    Unterstützte Quellen:
      - ALSA (alsasrc)
      - autoaudiosrc (falls unklar)
    Ausgabe: float32 mono Samples + Feature-Hook.
    """

    def __init__(
        self,
        config: AudioConfig,
        on_samples: Optional[Callable[[float, "np.ndarray", dict], None]] = None,
    ):
        """
        on_samples(ts, samples_f32, features) bei jedem Chunk.
        """
        self.cfg = config
        self.on_samples = on_samples
        self._loop: Optional[GLib.MainLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._pipeline: Optional[Gst.Pipeline] = None
        self._appsink: Optional[Gst.Element] = None
        self._last_samples: Optional["np.ndarray"] = None
        self._last_rate: int = 0
        self._last_ts: float = 0.0
        self._running = threading.Event()

    def _build_pipeline(self) -> Gst.Pipeline:
        if self.cfg.pipeline_launch:
            LOG.info("Audio: nutze custom pipeline (gst-launch-Notation)")
            pipeline = Gst.parse_launch(self.cfg.pipeline_launch)
            return pipeline  # type: ignore

        rate = self.cfg.rate
        ch = self.cfg.channels
        # Samples als F32LE Mono ausgeben
        # Hinweis: Für „source=default“ benutzen wir autoaudiosrc, ansonsten alsasrc device=..
        if self.cfg.source == "default":
            src = "autoaudiosrc"
        else:
            src = f"alsasrc device={self.cfg.source}"

        # appsink mit gewünschter Blockgröße (chunk_ms)
        n_samples = max(1, int(rate * (self.cfg.chunk_ms / 1000.0)))
        launch = (
            f"{src} ! audioconvert ! audioresample ! "
            f"audio/x-raw,format=F32LE,rate={rate},channels={ch} ! "
            f"appsink name=appsink emit-signals=true max-buffers=8 drop=true sync=false "
            f"caps=audio/x-raw,format=F32LE,rate={rate},channels={ch}"
        )
        LOG.info("Audio-Pipeline:\n  gst-launch-1.0 %s", launch)
        pipeline: Gst.Pipeline = Gst.parse_launch(launch)  # type: ignore
        self._appsink = pipeline.get_by_name("appsink")
        if self._appsink is None:
            raise RuntimeError("appsink nicht gefunden (Audio)")
        self._appsink.connect("new-sample", self._on_new_sample)  # type: ignore
        # Hinweis: Chunkgröße lässt sich nicht 1:1 via appsink erzwingen; wir paketieren im Callback.
        self._accum = []  # Akkumulator für gewünschte Chunkgröße
        self._target_samples = n_samples
        return pipeline

    def _on_new_sample(self, sink):
        try:
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK
            arr, rate = _gst_buffer_to_ndarray_audio(sample)
            ts = time.time()
            if arr is not None and rate > 0 and np is not None:
                # Akkumulieren, bis wir ~chunk_ms erreicht haben
                self._accum.append(arr)
                total = np.concatenate(self._accum) if len(self._accum) > 1 else self._accum[0]
                if total.shape[0] >= self._target_samples:
                    chunk = total[: self._target_samples]
                    rest = total[self._target_samples :]
                    self._accum = [rest] if rest.size > 0 else []
                    self._last_samples = chunk
                    self._last_rate = rate
                    self._last_ts = ts
                    if self.on_samples:
                        feats = _compute_audio_features(chunk, rate)
                        self.on_samples(ts, chunk, feats)
            return Gst.FlowReturn.OK
        except Exception as e:
            LOG.error("Audio new-sample Fehler: %s", e)
            return Gst.FlowReturn.ERROR

    def _on_bus(self, bus: Gst.Bus, msg: Gst.Message):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            LOG.error("GStreamer ERROR (Audio): %s; debug=%s", err, debug)
            self.stop()
        elif t == Gst.MessageType.EOS:
            LOG.warning("GStreamer EOS (Audio)")
            self.stop()
        elif t == Gst.MessageType.WARNING:
            w, debug = msg.parse_warning()
            LOG.warning("GStreamer WARNING (Audio): %s; debug=%s", w, debug)

    def start(self):
        if self._running.is_set():
            return
        self._pipeline = self._build_pipeline()
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)

        self._loop = GLib.MainLoop()
        def _run():
            try:
                self._pipeline.set_state(Gst.State.PLAYING)
                self._loop.run()
            finally:
                self._pipeline.set_state(Gst.State.NULL)

        self._running.set()
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        LOG.info("Audio gestartet: %s", self.cfg.source)

    def stop(self):
        if not self._running.is_set():
            return
        self._running.clear()
        try:
            if self._loop is not None:
                self._loop.quit()
        except Exception as e:
            log_suppressed(LOG, key="wrappers_gstreamer_wrapper.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._loop = None
        self._pipeline = None
        LOG.info("Audio gestoppt")

    def is_running(self) -> bool:
        return self._running.is_set()

    def get_latest(self) -> Tuple[Optional["np.ndarray"], int, float]:
        """Gibt (Samples float32 mono, rate, ts) zurück."""
        return self._last_samples, self._last_rate, self._last_ts

    def reconfigure(self, **kwargs):
        """Laufzeit-Konfiguration anpassen (Source/Rate/Channels/Chunk)."""
        was_running = self.is_running()
        if was_running:
            self.stop()
        for k, v in kwargs.items():
            if hasattr(self.cfg, k):
                setattr(self.cfg, k, v)
        if was_running:
            self.start()


# =============================================================================
# Kombi-Wrapper (Video + Audio)
# =============================================================================

@dataclass
class AVConfig:
    video: VideoConfig = field(default_factory=VideoConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)


class GStreamerWrapper:
    """
    Komfort-Wrapper, der Video- und Audio-Pipeline gemeinsam verwaltet.
    - Gemeinsame Start/Stop-Methoden
    - Feature-Hooks durchleitbar
    """

    def __init__(
        self,
        avcfg: AVConfig,
        on_video_frame: Optional[Callable[[float, "np.ndarray", dict], None]] = None,
        on_audio_chunk: Optional[Callable[[float, "np.ndarray", dict], None]] = None,
    ):
        self.cfg = avcfg
        self.video = GStreamerVideo(self.cfg.video, on_frame=on_video_frame)
        self.audio = GStreamerAudio(self.cfg.audio, on_samples=on_audio_chunk)

    def start_all(self):
        self.video.start()
        self.audio.start()

    def stop_all(self):
        self.video.stop()
        self.audio.stop()

    def is_running(self) -> bool:
        return self.video.is_running() or self.audio.is_running()

    def latest_video(self) -> Tuple[Optional["np.ndarray"], float]:
        return self.video.get_latest()

    def latest_audio(self) -> Tuple[Optional["np.ndarray"], int, float]:
        return self.audio.get_latest()

    def reconfigure_video(self, **kwargs):
        self.video.reconfigure(**kwargs)

    def reconfigure_audio(self, **kwargs):
        self.audio.reconfigure(**kwargs)


# =============================================================================
# CLI-Test
# =============================================================================

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="ORÓMA v2.11 – GStreamer-Wrapper Test")
    parser.add_argument("--video", type=str, default="/dev/video0", help="Video-Quelle (RTSP/HTTP/File/V4L2)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--audio", type=str, default="default", help="Audio-Quelle (ALSA device oder 'default')")
    parser.add_argument("--rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--duration", type=float, default=10.0, help="Testdauer in Sekunden")
    parser.add_argument("--snapshot", type=str, default="", help="Pfad für Snapshot am Ende (optional)")
    args = parser.parse_args()

    def on_frame(ts, frame, feats):
        LOG.info("Video-Frame ts=%.3f, shape=%s, edges=%.2f",
                 ts, None if frame is None else frame.shape, feats.get("edges_mean", -1))

    def on_audio(ts, samples, feats):
        LOG.info("Audio-Chunk ts=%.3f, len=%s, rms=%.3f, f0=%.1fHz",
                 ts, None if samples is None else samples.shape[0],
                 feats.get("rms", -1.0), feats.get("dom_freq", 0.0))

    avcfg = AVConfig(
        video=VideoConfig(source=args.video, width=args.width, height=args.height, fps=args.fps),
        audio=AudioConfig(source=args.audio, rate=args.rate, channels=args.channels)
    )
    wrapper = GStreamerWrapper(avcfg, on_video_frame=on_frame, on_audio_chunk=on_audio)
    wrapper.start_all()
    LOG.info("Starte Test für %.1f s ...", args.duration)
    t0 = time.time()
    try:
        while time.time() - t0 < args.duration:
            time.sleep(0.25)
    finally:
        if args.snapshot:
            frame, _ = wrapper.latest_video()
            if frame is not None and cv2 is not None:
                ok = cv2.imwrite(args.snapshot, frame)
                LOG.info("Snapshot gespeichert: %s (ok=%s)", args.snapshot, ok)
        wrapper.stop_all()
        LOG.info("Beendet.")

if __name__ == "__main__":
    _cli()
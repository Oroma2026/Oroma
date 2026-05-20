#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/wrappers/vision_wrapper.py
# Projekt:   ORÓMA (Headless Vision · Edge Runtime)
# Modul:     VisionWrapper – Capture (OpenCV/GStreamer/Picamera2) + SnapFeatures + Overlay + optional Best-Effort Inference (Hailo/ONNX/DeGirum)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist der produktive, headless Vision-Baustein in ORÓMA:
#
#   1) VisionWrapper (Klasse):
#      - startet einen Capture-Thread (idempotent start/stop)
#      - hält einen Ringbuffer der letzten Frames (verlustarm, UI-friendly)
#      - berechnet pro Frame leichte SnapFeatures (Histogramme, Kanten, Helligkeit, Motion, FPS)
#      - kann ein Overlay-Frame rendern (Debug/Status im MJPEG/Video-UI)
#      - optional: Best-Effort Tags durch verfügbare Backends (Hailo/ONNX/DeGirum)
#
#   2) embed(frame) (Modul-Funktion):
#      - superleichte CPU-Feature-Extraktion aus einem einzelnen Frame
#      - gedacht für SnapTokens/Meta-Wrapper (ohne Capture-Thread)
#
#   3) detect(frame) (Modul-Funktion):
#      - heuristische „Erkennung“ auf Basis der embed()-Features
#      - liefert Tags wie 'dark', 'bright', 'low_motion', 'motion', 'detailed'
#
# HEADLESS-INVARIANTE
# ──────────────────
# - Keine GUI/Qt/Wayland/X11 Abhängigkeiten.
# - Capture läuft über OpenCV (V4L2/USB), optional GStreamer Pipeline,
#   optional Picamera2 (libcamera).
# - Ausgabe/Debug erfolgt über Web-UI/Overlay – nicht über Desktop.
#
# SNAPFEATURES (WAS GENAU BERECHNET WIRD)
# ──────────────────────────────────────
# Die Feature-Extraktion ist bewusst „Edge-leicht“ und deterministisch:
#   - RGB-Histogramm: 3×16 Bins (normalisiert)
#   - HSV-Histogramm: H 16 + S 16 (normalisiert)
#   - edges_ratio: Kantenanteil über Canny (0..1)
#   - brightness: mittlere Helligkeit (0..255), plus brightness_norm (0..1)
#   - motion_mag/motion_norm: Differenz zum vorherigen Gray-Frame (0..1)
#   - colorfulness: grobe Farbigkeit (0..1)
#   - fps: gemessen aus Capture-Timing (für Overlay/Debug)
#
# RESULT-FORMAT (embed())
# ──────────────────────
# embed(frame) liefert dict (immer ok=True, dummy=True/False):
#   {
#     "ok": True,
#     "dummy": False|True,
#     "vec": [...],            # kompakter Feature-Vektor (Histogramme + 4 Scalars)
#     "embedding": [...],      # Alias (für Wrapper-Kompatibilität)
#     "features": { ... },     # Detailwerte (edges_ratio, brightness_norm, motion_norm, w/h, ...)
#     "motion_area": <float>,  # Alias für motion_norm (Legacy)
#     "edge_mean": <float>,    # Alias für edges_ratio (Legacy)
#     "colorfulness": <float>, # Alias
#     "tags": [...],           # heuristische Tags
#   }
#
# WICHTIG: embed() nutzt eine modulweite _prev_gray_embed Cache-Variable, um Motion
# ohne externen Zustand zu berechnen (best effort).
#
# CAPTURE-BACKENDS (VisionWrapper Klasse)
# ───────────────────────────────────────
# - backend="opencv"   : cv2.VideoCapture(device oder Datei/URL)
# - backend="gstreamer": cv2.VideoCapture(pipeline, CAP_GSTREAMER)
# - backend="picamera2": Picamera2 (wenn installiert)
#
# Optional „Inference Ready“ (best effort):
# - backend="hailo"    : nutzt wrappers.hailo_wrapper.HailoWrapper, wenn vorhanden/available
# - ONNX runtime       : wenn onnxruntime verfügbar UND OROMA_VISION_ONNX_MODEL existiert
# - backend="degirum"  : nur Status-Tag „DG:ready“ (ohne konkretes Modell in diesem Wrapper)
#
# Best-Effort Inference liefert Tags:
#   - Hailo: Tag 'hailo' (Embedding/Backend verfügbar)
#   - ONNX : Top-Labels (Top-3) aus Softmax/Logits (wenn Labelsdatei vorhanden)
#   - DeGirum: 'DG:ready'
#
# OVERLAY (DEBUG & UI)
# ───────────────────
# VisionWrapper kann aus dem aktuellen Frame ein Overlay erzeugen:
#   - Kantenlayer + Statuszeilen:
#       backend=...
#       fps=... bright=... edges=... motion=...
#       tags=... (wenn inference_ready)
# Ziel:
#   - Headless Debug im Browser (Video-UI/MJPEG), ohne SSH-logspam.
#
# ROBUSTHEIT / PRODUKTIONSVERHALTEN
# ─────────────────────────────────
# - start/stop sind idempotent; stop() räumt Capture/Thread best effort auf.
# - Exceptions werden rate-limited über core.log_guard.log_suppressed geloggt.
# - Wenn Backend/Camera nicht verfügbar ist: Wrapper bleibt funktionsfähig,
#   liefert dummy/None an Aufrufer, statt hart zu crashen.
#
# WICHTIGE ENV-VARIABLEN (BUILD-FROM-ENV)
# ───────────────────────────────────────
# build_from_env() akzeptiert:
#   VISION_BACKEND oder OROMA_VISION_BACKEND = opencv|gstreamer|picamera2|hailo|degirum
#   OROMA_VISION_SOURCE     = <pfad/url/pipeline> (optional; wenn gesetzt: source statt device)
#   OROMA_VISION_DEVICE     = 0 (USB device index)
#   OROMA_VISION_W / _H     = 640 / 360
#   OROMA_VISION_FPS        = 30
#
# Optional ONNX:
#   OROMA_VISION_ONNX_MODEL  = /path/to/model.onnx
#   OROMA_VISION_ONNX_LABELS = /path/to/labels.txt
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# class VisionWrapper:
#   - start(), stop(), is_alive()
#   - get_frame(timeout=...)            -> Optional[np.ndarray]
#   - get_overlay_frame(timeout=...)    -> Optional[np.ndarray]
#   - get_features(timeout=...)         -> Optional[VideoSnapFeatures]
#
# Modul-Funktionen:
#   - build_from_env(name="vision") -> VisionWrapper
#   - embed(frame)  -> dict
#   - detect(frame) -> dict
#   - _selftest()   -> CLI-Selbsttest (wenn __main__)
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Headless bleibt Pflicht.
# - Feature-Extraktion muss leicht & deterministisch bleiben (Edge-Stabilität).
# - Optional-Backends sind best effort (nie Boot/Loop blockieren).
# - embed()/detect() müssen ohne laufenden Capture-Thread nutzbar bleiben
#   (SnapTokens/Meta-Wrapper verlassen sich darauf).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import time
import atexit
import queue
import threading
import traceback
import logging
from core.log_guard import log_suppressed
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

import cv2
import numpy as np

# ---------- Logging -----------------------------------------------------------

logger = logging.getLogger("oroma.vision")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[vision] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# ---------- Optionale Backends -----------------------------------------------

try:
    import onnxruntime as ort  # type: ignore
    _HAS_ORT = True
except Exception:
    _HAS_ORT = False

try:
    import degirum  # type: ignore
    _HAS_DEGIRUM = True
except Exception:
    _HAS_DEGIRUM = False

try:
    from picamera2 import Picamera2  # type: ignore
    _HAS_PICAM2 = True
except Exception:
    _HAS_PICAM2 = False

try:
    from wrappers.hailo_wrapper import HailoWrapper  # type: ignore
    _HAS_HAILO = True
except Exception:
    _HAS_HAILO = False


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# ---------------------------------------------------------------------------
# SnapFeature-Datensatz
# ---------------------------------------------------------------------------

@dataclass
class VideoSnapFeatures:
    hist_rgb_16: np.ndarray   # 48 Werte (3x16)
    hist_hs_16: np.ndarray    # 32 Werte (H=16, S=16)
    edges_ratio: float        # 0..1
    brightness: float         # 0..255 (Graumittel)
    motion_mag: float         # optische Fluss-/Diff-Magnitude
    fps: float
    width: int
    height: int
    ts: float                 # Unix-Timestamp
    tags: List[str]           # optionale Label-Tags

    def as_vector(self) -> List[float]:
        return (
            self.hist_rgb_16.astype(np.float32).tolist()
            + self.hist_hs_16.astype(np.float32).tolist()
            + [float(self.edges_ratio),
               float(self.brightness),
               float(self.motion_mag),
               float(self.fps)]
        )

    def as_metadata(self) -> Dict[str, Any]:
        return {
            "width": int(self.width),
            "height": int(self.height),
            "ts": float(self.ts),
            "tags": list(self.tags),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {"vector": self.as_vector(), "metadata": self.as_metadata()}


# ---------------------------------------------------------------------------
# VisionWrapper (für Video-UI etc.)
# ---------------------------------------------------------------------------

class VisionWrapper:
    """
    Thread-sicherer Capture-/Feature-Wrapper. Start/Stop idempotent.
    Nutzt intern dieselbe Feature-Logik wie embed(frame), aber mit
    eigenem Thread und Ringbuffer.
    """

    def __init__(
        self,
        source: Optional[str] = None,
        device_index: int = 0,
        use_gstreamer: bool = False,
        use_picam2: bool = False,
        width: int = 640,
        height: int = 360,
        fps: int = 30,
        ring_size: int = 4,
        name: str = "vision",
        onnx_model: Optional[str] = None,
        onnx_labels: Optional[str] = None,
    ):
        # Konfiguration
        self.name = name
        self.source = source
        self.device_index = device_index
        self.use_gstreamer = use_gstreamer
        self.use_picam2 = use_picam2
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)

        # Inferenz-Assets (optional)
        self.onnx_model = onnx_model
        self.onnx_labels = onnx_labels

        # Backend laut ENV (nur Info/Flags, Capture läuft je nach use_* Flags)
        self.backend = os.getenv("VISION_BACKEND", os.getenv("OROMA_VISION_BACKEND", "opencv")).lower()
        self._backend_info: str = self.backend or "opencv"

        # Laufzeit
        self._stop = threading.Event()
        self._thr: Optional[threading.Thread] = None
        self._frame_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=ring_size)
        self._feature_q: "queue.Queue[VideoSnapFeatures]" = queue.Queue(maxsize=ring_size)

        self._prev_gray: Optional[np.ndarray] = None
        self._last_ts = time.monotonic()
        self._fps_ema = float(self.fps)

        # Capture & Devices
        self._cap: Optional[cv2.VideoCapture] = None
        self._picam2: Optional["Picamera2"] = None

        # Inferenz-Objekte
        self._inference_ready = False
        self._hailo: Optional["HailoWrapper"] = None
        self._ort_sess: Optional["ort.InferenceSession"] = None  # type: ignore
        self._ort_input_name: Optional[str] = None
        self._labels: Optional[List[str]] = None

        # Locks
        self._lock = threading.RLock()

        self._init_inference()
        atexit.register(self.stop)

    # ------------------------ Init ------------------------

    def _init_inference(self) -> None:
        """
        Initialisiert optionale Inferenz-Backends einmalig.
        Hailo: Verbindung + embed-ready
        ONNX: Session + Labelcache
        DeGirum: nur Verfügbarkeitsanzeige (kein konkretes Modell hier)
        """
        # Hailo
        if self.backend == "hailo" and _HAS_HAILO:
            try:
                h = HailoWrapper()
                h.connect()
                if h.is_available():
                    self._hailo = h
                    self._inference_ready = True
                    self._backend_info = "hailo"
                else:
                    self._hailo = None
                    self._backend_info = "cpu"
            except Exception:
                self._hailo = None

        # ONNX
        if _HAS_ORT and self.onnx_model and os.path.isfile(self.onnx_model):
            try:
                self._ort_sess = ort.InferenceSession(
                    self.onnx_model,
                    providers=["CPUExecutionProvider"]
                )
                self._ort_input_name = self._ort_sess.get_inputs()[0].name
                # Labels laden (optional)
                if self.onnx_labels and os.path.isfile(self.onnx_labels):
                    with open(self.onnx_labels, "r", encoding="utf-8") as f:
                        self._labels = [ln.strip() for ln in f if ln.strip()]
                self._inference_ready = True
                if self._backend_info in ("opencv", "cpu"):
                    self._backend_info = "onnx"
            except Exception:
                self._ort_sess = None
                self._ort_input_name = None
                self._labels = None

        # DeGirum: hier nur Flag, da Modell/Session projektabhängig ist
        if self.backend == "degirum" and _HAS_DEGIRUM:
            self._inference_ready = True
            if self._backend_info in ("opencv", "cpu"):
                self._backend_info = "degirum"

    # ------------------------ Start/Stop ------------------

    def start(self) -> None:
        with self._lock:
            if self._thr and self._thr.is_alive():
                return
            self._stop.clear()
            self._open_capture()
            self._thr = threading.Thread(target=self._loop, name=f"{self.name}-loop", daemon=True)
            self._thr.start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            if self._thr:
                self._thr.join(timeout=1.5)
            self._thr = None
            # Capture freigeben
            try:
                if self._cap:
                    self._cap.release()
            except Exception as e:
                log_suppressed(logger, key="wrappers_vision_wrapper.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            self._cap = None
            # Picamera stoppen
            if self._picam2:
                try:
                    self._picam2.stop()
                except Exception as e:
                    log_suppressed(logger, key="wrappers_vision_wrapper.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                self._picam2 = None
            # Hailo trennen
            if self._hailo:
                try:
                    self._hailo.disconnect()
                except Exception as e:
                    log_suppressed(logger, key="wrappers_vision_wrapper.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                self._hailo = None

    def __del__(self):
        try:
            self.stop()
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    def is_alive(self) -> bool:
        return bool(self._thr and self._thr.is_alive())

    # ------------------------ Capture ---------------------

    def _open_capture(self) -> None:
        # Picamera2
        if self.use_picam2:
            if not _HAS_PICAM2:
                raise RuntimeError("Picamera2-Modul nicht installiert.")
            self._picam2 = Picamera2()
            cfg = self._picam2.create_preview_configuration(
                main={"size": (self.width, self.height)},
                controls={"FrameRate": self.fps},
            )
            self._picam2.configure(cfg)
            self._picam2.start()
            self._backend_info = "picamera2"
            # Reset Zustände
            self._prev_gray = None
            self._last_ts = time.monotonic()
            self._fps_ema = float(self.fps)
            return

        # OpenCV / GStreamer
        if self.use_gstreamer:
            if not self.source:
                raise RuntimeError("GStreamer benötigt OROMA_VISION_SOURCE.")
            self._cap = cv2.VideoCapture(self.source, cv2.CAP_GSTREAMER)
            self._backend_info = "gstreamer"
        else:
            if self.source is None:
                self._cap = cv2.VideoCapture(int(self.device_index))
                self._backend_info = "opencv-device"
            else:
                self._cap = cv2.VideoCapture(self.source)
                self._backend_info = "opencv-ffmpeg"

        if not self._cap or not self._cap.isOpened():
            raise RuntimeError(f"VideoCapture konnte nicht geöffnet werden ({self._backend_info})")

        # Capture-Properties (best effort)
        # ---------------------------------------------------------------------
        # PRODUKTIONSFIX – OpenCV FOURCC / MJPEG best-effort
        # ---------------------------------------------------------------------
        # Analog zu core/device_hub.py: wenn eine UVC Kamera MJPEG kann, ist das
        # auf dem Pi meist deutlich performanter als YUYV.
        #
        # ENV:
        #   OROMA_VISION_FOURCC=MJPG
        #   OROMA_OPENCV_FOURCC=MJPG
        #   OROMA_VISION_BUFFERSIZE=2
        # ---------------------------------------------------------------------
        _fourcc = (_env("OROMA_VISION_FOURCC", "") or _env("OROMA_OPENCV_FOURCC", "")).strip()
        if _fourcc:
            _fourcc = _fourcc[:4]
        if _fourcc and len(_fourcc) == 4:
            try:
                self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*_fourcc))
            except Exception:
                pass

        _buf_raw = _env("OROMA_VISION_BUFFERSIZE", "").strip()
        if _buf_raw:
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, int(_buf_raw))
            except Exception:
                pass

        for (prop, val) in (
            (cv2.CAP_PROP_FRAME_WIDTH, self.width),
            (cv2.CAP_PROP_FRAME_HEIGHT, self.height),
            (cv2.CAP_PROP_FPS, self.fps),
        ):
            try:
                self._cap.set(prop, val)
            except Exception as e:
                log_suppressed(logger, key="wrappers_vision_wrapper.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        self._prev_gray = None
        self._last_ts = time.monotonic()
        self._fps_ema = float(self.fps)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                # Frame holen
                if self._picam2 is not None:
                    frame = self._picam2.capture_array()
                    ok = frame is not None
                else:
                    ok, frame = self._cap.read() if self._cap else (False, None)

                if not ok or frame is None:
                    time.sleep(0.01)
                    continue

                # Resample (Safety)
                if frame.shape[1] != self.width or frame.shape[0] != self.height:
                    frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)

                # FPS berechnen
                ts = time.monotonic()
                dt = ts - self._last_ts
                self._last_ts = ts
                if dt > 1e-6:
                    fps_inst = 1.0 / dt
                    self._fps_ema = 0.9 * self._fps_ema + 0.1 * fps_inst

                # Features + Overlay
                feats = self._extract_features(frame, self._fps_ema, time.time())
                overlay = self._render_overlay(frame.copy(), feats)

                # Ringbuffer push (non-blocking)
                self._put_queue(self._frame_q, overlay)
                self._put_queue(self._feature_q, feats)

            except Exception:
                traceback.print_exc()
                time.sleep(0.02)

    @staticmethod
    def _put_queue(q: "queue.Queue", item) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                _ = q.get_nowait()
            except Exception as e:
                log_suppressed(logger, key="wrappers_vision_wrapper.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            try:
                q.put_nowait(item)
            except Exception as e:
                log_suppressed(logger, key="wrappers_vision_wrapper.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # ------------------------ Features --------------------

    def _extract_features(self, frame_bgr: np.ndarray, fps: float, ts: float) -> VideoSnapFeatures:
        """
        Standard-SnapFeatures + optionale Inferenz-Tags.
        """
        h, w = frame_bgr.shape[:2]

        # Gray & Kanten
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 64, 128)
        edges_ratio = float(np.count_nonzero(edges)) / float(max(1, w * h))

        # Helligkeit
        brightness = float(gray.mean())

        # RGB-16 Histogramme
        hist_rgb_16 = []
        for ch in range(3):
            hist = cv2.calcHist([frame_bgr], [ch], None, [16], [0, 256]).flatten()
            hist = hist / max(1.0, float(hist.sum()))
            hist_rgb_16.append(hist.astype(np.float32))
        hist_rgb_16 = np.concatenate(hist_rgb_16, axis=0)

        # HSV-16 (H & S)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        hist_h_16 = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s_16 = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        hist_h_16 = (hist_h_16 / max(1.0, float(hist_h_16.sum()))).astype(np.float32)
        hist_s_16 = (hist_s_16 / max(1.0, float(hist_s_16.sum()))).astype(np.float32)
        hist_hs_16 = np.concatenate([hist_h_16, hist_s_16], axis=0)

        # Bewegung (optischer Fluss, sparse)
        motion_mag = 0.0
        try:
            if self._prev_gray is None:
                self._prev_gray = gray
            else:
                p0 = cv2.goodFeaturesToTrack(
                    self._prev_gray, maxCorners=200, qualityLevel=0.01, minDistance=7, blockSize=7
                )
                if p0 is not None and len(p0) >= 8:
                    p1, st, _ = cv2.calcOpticalFlowPyrLK(
                        self._prev_gray, gray, p0, None,
                        winSize=(15, 15), maxLevel=2,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)
                    )
                    if p1 is not None and st is not None:
                        good_new = p1[st == 1]
                        good_old = p0[st == 1]
                        if len(good_new) > 0:
                            diff = good_new - good_old
                            mag = np.linalg.norm(diff, axis=1)
                            motion_mag = float(np.mean(mag))
                self._prev_gray = gray
        except Exception:
            motion_mag = 0.0

        # Tags (optional, best effort)
        tags: List[str] = []
        try:
            if self._inference_ready:
                tags = self._infer_tags_best_effort(frame_bgr)
        except Exception:
            tags = []

        return VideoSnapFeatures(
            hist_rgb_16=hist_rgb_16,
            hist_hs_16=hist_hs_16,
            edges_ratio=edges_ratio,
            brightness=brightness,
            motion_mag=motion_mag,
            fps=float(fps),
            width=int(w),
            height=int(h),
            ts=float(ts),
            tags=tags[:5],
        )

    def _infer_tags_best_effort(self, frame_bgr: np.ndarray) -> List[str]:
        """
        Liefert bis zu 5 Tags, falls ein Backend verfügbar ist.
        DeGirum: Platzhalter-Tag (bereit).
        Hailo: Embedding vorhanden → 'hailo'.
        ONNX: Top-3 aus Softmax/Logits.
        """
        tags: List[str] = []

        # DeGirum: ohne konkretes Modell hier nur Status-Tag
        if _HAS_DEGIRUM and self.backend == "degirum":
            tags.append("DG:ready")

        # Hailo: Embedding-Check
        if self._hailo is not None:
            try:
                arr = cv2.resize(frame_bgr, (224, 224)).astype(np.float32) / 255.0
                arr = np.expand_dims(arr, axis=0)
                emb = self._hailo.embed_batch(arr)
                if emb is not None:
                    tags.append("hailo")
            except Exception as e:
                log_suppressed(logger, key="wrappers_vision_wrapper.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # ONNX: einmalig initialisierte Session
        if self._ort_sess is not None and self._ort_input_name:
            try:
                img = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                img = cv2.resize(img, (224, 224), interpolation=cv2.INTER_AREA)
                x = img.astype(np.float32) / 255.0
                x = np.transpose(x, (2, 0, 1))[None, ...]  # NCHW
                out = self._ort_sess.run(None, {self._ort_input_name: x})[0]
                probs = out[0]
                topk = probs.argsort()[-3:][::-1]
                if self._labels:
                    for idx in topk:
                        if 0 <= int(idx) < len(self._labels):
                            tags.append(self._labels[int(idx)])
                else:
                    tags.extend([str(int(i)) for i in topk])
            except Exception as e:
                log_suppressed(logger, key="wrappers_vision_wrapper.pass.9", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # Begrenzen
        return tags[:5]

    # ------------------------ Overlay --------------------

    def _render_overlay(self, frame_bgr: np.ndarray, feats: VideoSnapFeatures) -> np.ndarray:
        # Kantenlayer
        try:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 64, 128)
            color_edges = np.zeros_like(frame_bgr)
            color_edges[edges > 0] = (0, 255, 255)
            frame_bgr = cv2.addWeighted(frame_bgr, 0.9, color_edges, 0.4, 0.0)
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.pass.10", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # Textzeilen
        lines = [
            f"backend={self._backend_info}",
            f"fps={feats.fps:.1f} bright={feats.brightness:.1f} "
            f"edges={feats.edges_ratio:.3f} motion={feats.motion_mag:.3f}",
        ]
        if feats.tags:
            lines.append("tags: " + ", ".join(feats.tags))

        y = 18
        for ln in lines:
            cv2.putText(frame_bgr, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (30, 220, 30), 1, cv2.LINE_AA)
            y += 18
        return frame_bgr

    # ------------------------ API ------------------------

    def get_overlay_frame(self, timeout: float = 0.12) -> Optional[np.ndarray]:
        try:
            return self._frame_q.get(timeout=timeout)
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.ret.11", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def get_latest_features(self, timeout: float = 0.06) -> Optional[VideoSnapFeatures]:
        try:
            return self._feature_q.get(timeout=timeout)
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.ret.12", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def make_snap(self) -> Optional[Dict[str, Any]]:
        feats = self.get_latest_features(timeout=0.0)
        return feats.as_dict() if feats else None

    def snapshot(self, with_overlay: bool = True) -> Optional[np.ndarray]:
        """
        Liefert einen einzelnen Frame (BGR).
        with_overlay=True → annotierter Frame aus dem Ringbuffer.
        with_overlay=False → Rohbild (direkt vom Capture).
        """
        try:
            if with_overlay:
                frm = self.get_overlay_frame(timeout=0.25)
                if frm is not None:
                    return frm
            # Rohbild direkt ziehen
            if self._picam2 is not None:
                return self._picam2.capture_array()
            if self._cap and self._cap.isOpened():
                ok, frm = self._cap.read()
                return frm if ok else None
            return None
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.ret.13", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def snapshot_jpeg(self, with_overlay: bool = True, quality: int = 85) -> Optional[bytes]:
        frm = self.snapshot(with_overlay=with_overlay)
        if frm is None:
            return None
        try:
            params = [int(cv2.IMWRITE_JPEG_QUALITY), int(max(1, min(100, quality)))]
            ok, buf = cv2.imencode(".jpg", frm, params)
            return bytes(buf) if ok else None
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.ret.14", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def snapshot_png(self, with_overlay: bool = True, compression: int = 3) -> Optional[bytes]:
        frm = self.snapshot(with_overlay=with_overlay)
        if frm is None:
            return None
        try:
            params = [int(cv2.IMWRITE_PNG_COMPRESSION), int(max(0, min(9, compression)))]
            ok, buf = cv2.imencode(".png", frm, params)
            return bytes(buf) if ok else None
        except Exception as e:
            log_suppressed(logger, key="wrappers_vision_wrapper.ret.15", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None


# ---------------------------------------------------------------------------
# Fabrik aus ENV (für Video-UI)
# ---------------------------------------------------------------------------

def build_from_env(name: str = "vision") -> VisionWrapper:
    """
    Baut eine VisionWrapper-Instanz auf Basis von ENV.
    Beachtet sowohl VISION_BACKEND als auch OROMA_VISION_BACKEND.
    """
    backend = _env("VISION_BACKEND", _env("OROMA_VISION_BACKEND", "opencv")).strip().lower()
    source = os.environ.get("OROMA_VISION_SOURCE", None)
    device = int(_env("OROMA_VISION_DEVICE", "0"))
    width = int(_env("OROMA_VISION_W", "640"))
    height = int(_env("OROMA_VISION_H", "360"))
    fps = int(_env("OROMA_VISION_FPS", "30"))
    onnx_model = os.environ.get("OROMA_VISION_ONNX_MODEL", "") or None
    onnx_labels = os.environ.get("OROMA_VISION_ONNX_LABELS", "") or None

    return VisionWrapper(
        source=source,
        device_index=device,
        use_gstreamer=(backend == "gstreamer"),
        use_picam2=(backend == "picamera2"),
        width=width,
        height=height,
        fps=fps,
        ring_size=4,
        name=name,
        onnx_model=onnx_model,
        onnx_labels=onnx_labels,
    )


# ---------------------------------------------------------------------------
# Leichtgewichtige embed()-Funktion für den Meta-Wrapper
# ---------------------------------------------------------------------------

_prev_gray_embed: Optional[np.ndarray] = None


def _colorfulness_0_1(frame_bgr: np.ndarray) -> float:
    """
    Einfache Farbigkeitsmetrik 0..1 nach Hasler/Suesstrunk (grob normalisiert).
    """
    try:
        b, g, r = cv2.split(frame_bgr.astype("float32"))
        rg = np.abs(r - g)
        yb = np.abs(0.5 * (r + g) - b)

        std_rg, std_yb = float(rg.std()), float(yb.std())
        mean_rg, mean_yb = float(rg.mean()), float(yb.mean())

        cf = np.sqrt(std_rg ** 2 + std_yb ** 2) + 0.3 * np.sqrt(mean_rg ** 2 + mean_yb ** 2)
        # grobe Normierung auf 0..1
        return float(max(0.0, min(cf / 100.0, 1.0)))
    except Exception as e:
        log_suppressed(logger, key="wrappers_vision_wrapper.ret.16", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return 0.0


def embed(frame: Optional[np.ndarray]) -> Dict[str, Any]:
    """
    Leichtgewichtige CPU-Embedding-Funktion.

    Erwartet:
        frame: BGR-ndarray (z. B. aus DeviceHub / camera_hub)

    Liefert bei Erfolg:
        {
          "ok": True,
          "dummy": False,
          "vec": [...],
          "embedding": [...],
          "features": {...},
          "motion_area": float(0..1),
          "edge_mean": float(0..1),
          "colorfulness": float(0..1),
        }

    Bei Fehler:
        dummy=True (Meta-Wrapper kann dann ggf. andere Backends nutzen).
    """
    global _prev_gray_embed

    if frame is None:
        logger.warning("embed(): kein Frame übergeben → Dummy-Fallback")
        return {
            "ok": True,
            "dummy": True,
            "embedding": [0.0],
            "reason": "no frame",
        }

    try:
        h, w = frame.shape[:2]

        # Gray & Kanten
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 64, 128)
        edges_ratio = float(np.count_nonzero(edges)) / float(max(1, w * h))  # 0..1

        # Helligkeit
        brightness = float(gray.mean())  # 0..255
        brightness_norm = brightness / 255.0

        # Bewegung (einfache Frame-Differenz, 0..1)
        motion_norm = 0.0
        try:
            if _prev_gray_embed is None:
                _prev_gray_embed = gray
            else:
                diff = cv2.absdiff(_prev_gray_embed, gray)
                motion_norm = float(diff.mean()) / 255.0
                motion_norm = max(0.0, min(1.0, motion_norm))
                _prev_gray_embed = gray
        except Exception:
            motion_norm = 0.0
            _prev_gray_embed = gray

        # RGB-16 Histogramme
        hist_rgb_16 = []
        for ch in range(3):
            hist = cv2.calcHist([frame], [ch], None, [16], [0, 256]).flatten()
            hist = hist / max(1.0, float(hist.sum()))
            hist_rgb_16.append(hist.astype(np.float32))
        hist_rgb_16 = np.concatenate(hist_rgb_16, axis=0)

        # HSV-16 (H & S)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist_h_16 = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s_16 = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        hist_h_16 = (hist_h_16 / max(1.0, float(hist_h_16.sum()))).astype(np.float32)
        hist_s_16 = (hist_s_16 / max(1.0, float(hist_s_16.sum()))).astype(np.float32)
        hist_hs_16 = np.concatenate([hist_h_16, hist_s_16], axis=0)

        # Farbigkeit 0..1
        color_norm = _colorfulness_0_1(frame)

        # Vektor zusammenbauen (ähnlich VideoSnapFeatures.as_vector, aber kompakter)
        vec = (
            hist_rgb_16.astype(np.float32).tolist()
            + hist_hs_16.astype(np.float32).tolist()
            + [float(edges_ratio), float(brightness_norm), float(motion_norm), float(color_norm)]
        )

        return {
            "ok": True,
            "dummy": False,
            "vec": vec,
            "embedding": vec,
            "features": {
                "edges_ratio": float(edges_ratio),
                "brightness": float(brightness),
                "brightness_norm": float(brightness_norm),
                "motion_norm": float(motion_norm),
                "colorfulness": float(color_norm),
                "w": int(w),
                "h": int(h),
            },
            # Kanonische Primitive fuer die aktuelle ORÓMA Hook-/Arbiter-/DB-Lernkette.
            # Diese drei Keys werden produktiv von hooks_av_snaptoken.py,
            # vision_arbiter.py und sql_manager.insert_cam_token(...) erwartet.
            "motion": float(motion_norm),
            "edges": float(edges_ratio),
            "color": float(color_norm),
            # Legacy-/Kompatibilitaets-Aliase fuer bestehende Wrapper/Diagnostik.
            "motion_area": float(motion_norm),
            "edge_mean": float(edges_ratio),
            "colorfulness": float(color_norm),
        }

    except Exception as e:
        logger.warning("embed() Fehler: %s", e)
        return {
            "ok": True,
            "dummy": True,
            "embedding": [0.0],
            "reason": "vision_error",
        }


# ---------------------------------------------------------------------------
# Einfaches detect() (Heuristik) – optional für Meta-Wrapper
# ---------------------------------------------------------------------------

def detect(frame: Optional[np.ndarray]) -> Dict[str, Any]:
    """
    Sehr einfache CPU-"Erkennung" auf Basis der embed()-Features.
    Liefert nur heuristische Tags wie 'dark', 'bright', 'low_motion' etc.
    """
    if frame is None:
        return {
            "ok": True,
            "dummy": True,
            "features": [],
            "reason": "no frame",
        }

    emb = embed(frame)
    if emb.get("dummy"):
        return {
            "ok": True,
            "dummy": True,
            "features": [],
            "reason": "embed dummy",
        }

    f = emb.get("features", {})
    tags: List[str] = []
    b = float(f.get("brightness", 0.0))
    m = float(f.get("motion_norm", 0.0))
    e = float(f.get("edges_ratio", 0.0))

    if b < 40:
        tags.append("dark")
    elif b > 180:
        tags.append("bright")

    if m < 0.02:
        tags.append("low_motion")
    else:
        tags.append("motion")

    if e > 0.15:
        tags.append("detailed")

    return {
        "ok": True,
        "dummy": False,
        "features": tags,
    }


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def _selftest() -> None:
    vw = build_from_env()
    vw.start()
    time.sleep(0.12)  # Backend setzt sich
    print(f"[vision] Start: backend={vw._backend_info} source={vw.source} device={vw.device_index}")
    t0 = time.time()
    n = 0
    try:
        while n < 10:
            frm = vw.get_overlay_frame(timeout=0.5)
            if frm is not None:
                n += 1
                emb = embed(frm)
                print(
                    f"[vision] snap {n} – dummy={emb.get('dummy')} "
                    f"len(vec)={len(emb.get('vec', []))} "
                    f"motion={emb.get('motion_area')} "
                    f"edges={emb.get('edge_mean')}"
                )
    finally:
        vw.stop()
        dt = time.time() - t0
        fps = (n / dt) if dt > 0 else 0.0
        print(f"[vision] Stop. {n} Frames in {dt:.2f}s, ~{fps:.1f} fps")


if __name__ == "__main__":
    _selftest()
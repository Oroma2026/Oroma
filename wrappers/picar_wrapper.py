#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/wrappers/picar_wrapper.py
# Projekt: ORÓMA
# Version: v3.7 (final produktiv)
# Stand:   2025-10-01
#
# Zweck
# ─────
# Low-Level Wrapper für PiCar (Motorsteuerung, Safety, Kamera-Anbindung):
#   • Motorsteuerung (PWM, vorwärts/rückwärts, links/rechts, stop)
#   • Deadman-Switch (Timeout ohne Befehle → STOP)
#   • Abstandssensor HC-SR04 (Hindernis-Stop bei < threshold cm)
#   • Optional: Kamera-Provider für CameraHub (Picamera2 oder OpenCV)
#
# Highlights
# ──────────
#   • GPIO import robust: läuft auch ohne RPi.GPIO (Debug-Modus auf x86)
#   • Threads für Deadman & Safety laufen sauber im Hintergrund
#   • CameraHub-Integration → UI/Video-Seite kann PiCar-Stream nutzen
#
# ENV Variablen
# ─────────────
#   OROMA_PICAR_CAMERA=1      → aktiviert CameraHub-Provider
#   OROMA_PICAM_W/H/FPS       → Kamera-Auflösung/FPS
#   OROMA_OPENCV_DEV=0        → Fallback Kamera-Device
#
# Lizenz: MIT (Projekt ORÓMA)
# =============================================================================

import os
import time
import logging
import threading

# GPIO (optional)
try:
    import RPi.GPIO as RPiGPIO
    _HAS_GPIO = True
except ImportError:
    RPiGPIO = None
    _HAS_GPIO = False

# CameraHub (optional)
try:
    from core import camera_hub
except Exception:
    camera_hub = None

# OpenCV/Picamera2 (optional)
try:
    import cv2
except Exception:
    cv2 = None

try:
    from picamera2 import Picamera2
except Exception:
    Picamera2 = None

logger = logging.getLogger("PiCarWrapper")
logger.setLevel(logging.INFO)


# =============================================================================
# Kamera-Provider (für CameraHub)
# =============================================================================

class _BaseProvider:
    def start(self): ...
    def stop(self): ...
    def get_frame(self): ...
    def _push(self, frame):
        if camera_hub:
            camera_hub.submit_frame(frame)

class _OpenCVProvider(_BaseProvider):
    def __init__(self, dev=0, width=None, height=None, fps=None):
        self.dev, self.width, self.height, self.fps = dev, width, height, fps
        self.cap, self._thr = None, None
        self._stop = threading.Event()

    def start(self):
        if cv2 is None:
            raise RuntimeError("OpenCV nicht verfügbar")
        self.cap = cv2.VideoCapture(self.dev)
        if self.width:  self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  int(self.width))
        if self.height: self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.height))
        if self.fps:    self.cap.set(cv2.CAP_PROP_FPS,          int(self.fps))
        self._stop.clear()
        self._thr = threading.Thread(target=self._loop, daemon=True)
        self._thr.start()
        logger.info("OpenCVProvider gestartet (dev=%s, %sx%s @%s)", self.dev, self.width, self.height, self.fps)

    def _loop(self):
        while not self._stop.is_set():
            ok, frame = self.cap.read() if self.cap else (False, None)
            if ok and frame is not None:
                self._push(frame)
            else:
                time.sleep(0.02)

    def get_frame(self):
        return camera_hub.get_frame() if camera_hub else None

    def stop(self):
        self._stop.set()
        if self._thr:
            try: self._thr.join(timeout=1.0)
            except Exception: pass
        if self.cap:
            try: self.cap.release()
            except Exception: pass
        logger.info("OpenCVProvider gestoppt")

class _Picamera2Provider(_BaseProvider):
    def __init__(self, width=640, height=480, fps=30):
        self.w, self.h, self.fps = width, height, fps
        self.picam, self._thr = None, None
        self._stop = threading.Event()

    def start(self):
        if Picamera2 is None:
            raise RuntimeError("Picamera2 nicht verfügbar")
        self.picam = Picamera2()
        cfg = self.picam.create_video_configuration(main={"size": (self.w, self.h)}, controls={"FrameRate": self.fps})
        self.picam.configure(cfg)
        self.picam.start()
        self._stop.clear()
        self._thr = threading.Thread(target=self._loop, daemon=True)
        self._thr.start()
        logger.info("Picamera2Provider gestartet (%sx%s @%s)", self.w, self.h, self.fps)

    def _loop(self):
        while not self._stop.is_set():
            try:
                frame = self.picam.capture_array("main")  # BGR
                if frame is not None:
                    self._push(frame)
            except Exception:
                time.sleep(0.01)

    def get_frame(self):
        return camera_hub.get_frame() if camera_hub else None

    def stop(self):
        self._stop.set()
        if self._thr:
            try: self._thr.join(timeout=1.0)
            except Exception: pass
        if self.picam:
            try: self.picam.stop(); self.picam.close()
            except Exception: pass
        logger.info("Picamera2Provider gestoppt")


def _bool_env(name: str, default=False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v == "": return default
    return v in ("1", "true", "yes", "on")

def _float_env(name: str, default: float) -> float:
    """Liest Float-ENV robust (headless, no deps)."""
    v = os.environ.get(name, "").strip()
    if v == "":
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


# =============================================================================
# PiCar Wrapper
# =============================================================================

class PiCar:
    """
    Wrapper-Klasse für die PiCar-Steuerung.
    """

    def __init__(self,
                 in1=17, in2=27, ena=22,
                 in3=23, in4=24, enb=25,
                 trig=5, echo=6,
                 deadman_timeout=2.0,
                 safe_distance=20.0):
        self.in1, self.in2, self.ena = in1, in2, ena
        self.in3, self.in4, self.enb = in3, in4, enb
        self.trig, self.echo = trig, echo
        self.deadman_timeout = deadman_timeout
        self.safe_distance = safe_distance

        self._last_command_time = time.time()
        self._running = False
        self._deadman_thread = None
        self._safety_thread = None
        self._cam_provider = None

        if _HAS_GPIO:
            GPIO = RPiGPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup([self.in1, self.in2, self.in3, self.in4], GPIO.OUT)
            GPIO.setup([self.ena, self.enb], GPIO.OUT)
            self.pwm_a = GPIO.PWM(self.ena, 100); self.pwm_b = GPIO.PWM(self.enb, 100)
            self.pwm_a.start(0); self.pwm_b.start(0)
            GPIO.setup(self.trig, GPIO.OUT); GPIO.setup(self.echo, GPIO.IN)
            GPIO.output(self.trig, False)
        else:
            self.pwm_a = self.pwm_b = None
            logger.warning("RPi.GPIO nicht verfügbar – PiCar im Debugmodus.")

        self._start_deadman()
        self._start_safety()
        self._maybe_start_camera_provider()

    # Kamera-Provider starten
    def _maybe_start_camera_provider(self):
        if not camera_hub or not _bool_env("OROMA_PICAR_CAMERA", False):
            return
        w, h, fps = int(os.getenv("OROMA_PICAM_W", "640")), int(os.getenv("OROMA_PICAM_H", "480")), int(os.getenv("OROMA_PICAM_FPS", "30"))
        provider = None
        if Picamera2:
            try: provider = _Picamera2Provider(width=w, height=h, fps=fps)
            except Exception as e: logger.warning("Picamera2Provider fehlgeschlagen: %s", e)
        if provider is None and cv2:
            dev = int(os.getenv("OROMA_OPENCV_DEV", "0"))
            provider = _OpenCVProvider(dev=dev, width=w, height=h, fps=fps)
        if provider:
            ok = camera_hub.set_provider(name="picar", provider=provider, replace=True)
            if ok: self._cam_provider = provider; logger.info("CameraHub-Provider PiCar aktiv")

    # Motorsteuerung
    def _apply_motor(self, left_forward, right_forward, speed=50):
        self._last_command_time = time.time()
        if not _HAS_GPIO:
            logger.info("[DEBUG] Motor cmd L=%s R=%s Speed=%s", left_forward, right_forward, speed); return
        GPIO = RPiGPIO
        GPIO.output(self.in1, GPIO.HIGH if left_forward else GPIO.LOW)
        GPIO.output(self.in2, GPIO.LOW if left_forward else GPIO.HIGH)
        GPIO.output(self.in3, GPIO.HIGH if right_forward else GPIO.LOW)
        GPIO.output(self.in4, GPIO.LOW if right_forward else GPIO.HIGH)
        self.pwm_a.ChangeDutyCycle(speed); self.pwm_b.ChangeDutyCycle(speed)

    def _stop_motors(self):
        if not _HAS_GPIO:
            logger.info("[DEBUG] STOP"); return
        GPIO = RPiGPIO
        GPIO.output([self.in1, self.in2, self.in3, self.in4], GPIO.LOW)
        self.pwm_a.ChangeDutyCycle(0); self.pwm_b.ChangeDutyCycle(0)

    # Safety
    def distance_cm(self) -> float:
        """Misst Distanz via HC-SR04 (Trig/Echo).

        WICHTIG (Stabilität/CPU):
          • Der klassische HC-SR04 Code nutzt Busy-Wait (while input()==0/1) und kann
            bei fehlendem/floatendem Echo-Pin mehrere CPU-Kerne verheizen.
          • Daher: Timeout + Micro-Sleep im Polling.

        Rückgabe:
          • Distanz in cm (float, gerundet)
          • Bei Timeout/Fehler: 999.0 ("weit weg")
        """
        if not _HAS_GPIO:
            return 999.0
        GPIO = RPiGPIO

        # Enable-Schalter: erlaubt PiCar ohne HC-SR04 (CPU-safe).
        if not _bool_env("OROMA_PICAR_DISTANCE", True):
            return 999.0

        timeout_sec = _float_env("OROMA_PICAR_GPIO_TIMEOUT", 0.03)
        poll_sleep  = _float_env("OROMA_PICAR_GPIO_SLEEP", 0.0005)
        if poll_sleep < 0.0:
            poll_sleep = 0.0

        # Trigger-Puls (10µs) – danach Echo messen.
        GPIO.output(self.trig, True); time.sleep(0.00001); GPIO.output(self.trig, False)

        t0 = time.time()
        start = stop = t0

        # Warte auf Rising Edge (echo goes HIGH)
        while GPIO.input(self.echo) == 0:
            start = time.time()
            if start - t0 > timeout_sec:
                return 999.0
            if poll_sleep:
                time.sleep(poll_sleep)

        # Warte auf Falling Edge (echo goes LOW)
        while GPIO.input(self.echo) == 1:
            stop = time.time()
            if stop - start > timeout_sec:
                return 999.0
            if poll_sleep:
                time.sleep(poll_sleep)

        return round(((stop - start) * 34300) / 2, 2)

    def _start_safety(self):
        if not _bool_env("OROMA_PICAR_SAFETY", True):
            return

        self._running = True
        def loop():
            while self._running:
                try:
                    d = self.distance_cm()
                    if d < self.safe_distance:
                        logger.warning("[Safety] Hindernis bei %scm → STOP", d)
                        self._stop_motors()
                except Exception: pass
                time.sleep(0.2)
        self._safety_thread = threading.Thread(target=loop, daemon=True); self._safety_thread.start()
        if not _bool_env("OROMA_PICAR_DEADMAN", True):
            return


    # Deadman
    def _start_deadman(self):
        self._running = True
        def loop():
            while self._running:
                if time.time() - self._last_command_time > self.deadman_timeout:
                    self._stop_motors()
                time.sleep(0.2)
        self._deadman_thread = threading.Thread(target=loop, daemon=True); self._deadman_thread.start()

    # Public API
    def forward(self, speed=50): self._apply_motor(True, True, speed)
    def backward(self, speed=50): self._apply_motor(False, False, speed)
    def left(self, speed=50): self._apply_motor(False, True, speed)
    def right(self, speed=50): self._apply_motor(True, False, speed)
    def stop(self): self._stop_motors()

    def cleanup(self):
        self._running = False; self._stop_motors()
        if camera_hub and self._cam_provider:
            try: camera_hub.clear_provider(self._cam_provider)
            except Exception: pass
        if _HAS_GPIO:
            try: self.pwm_a.stop(); self.pwm_b.stop(); RPiGPIO.cleanup()
            except Exception: pass

# Selftest
if __name__ == "__main__":
    car = PiCar()
    try:
        logger.info("Testlauf Forward 1s, Safety aktiv")
        car.forward(40); time.sleep(1); car.stop()
        logger.info("Distanz: %s cm", car.distance_cm())
    finally:
        car.cleanup()

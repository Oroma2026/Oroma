#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/circadian_controller.py
# Projekt:   ORÓMA (Circadian / Day↔Dream · Headless)
# Modul:     CircadianController – Licht-Automat + Delay + Hysterese + Fallback-Uhrzeit
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieser Controller steuert die Systemphase:
#   - DAY  (aktive Perzeption / AgentLoop / UI)
#   - DREAM (Offline-Lernen / DreamWorker / Konsolidierung)
#
# Er liest periodisch eine Helligkeit (0..100) und schaltet:
#   - bei Dunkelheit nach einer Verzögerung (Delay) in DREAM
#   - bei ausreichender Helligkeit (mit Hysterese) zurück nach DAY
#
# HEADLESS & ROBUSTHEIT
# ────────────────────
# - Keine GUI-Abhängigkeit.
# - Light-Sensor ist optional. Wenn keiner gesetzt ist, nutzt der Controller
#   einen **Clock-Fallback** (konfigurierbares Zeitfenster).
# - Logging ist „write-safe“: bevorzugt OROMA_LOG_DIR, sonst /var/tmp oder /tmp.
# - Ausnahmen werden suppressed geloggt, damit der Service nicht stirbt.
#
# INTEGRATIONSPUNKTE
# ──────────────────
# Der Controller kann beim Mode-Wechsel:
#   A) in eine Queue schreiben (event_queue.put("DAY"/"DREAM"))
#   B) einen Callback ausführen (on_mode_change(mode))
#
# run_oroma.py nutzt typischerweise:
#   - on_mode_change, um DreamWorker zu starten/stoppen
#   - zusätzlich wird häufig ein „Phase-JSON“ geschrieben (separater Mechanismus)
#
# SCHALTLOGIK (KONZEPT)
# ─────────────────────
# - night_threshold: unterhalb dieses Werts gilt es als „dunkel“
# - delay_minutes: muss ununterbrochen dunkel sein, bevor DREAM aktiviert wird
# - hysteresis: Rückkehr zu DAY erfordert lux >= threshold + hysteresis
# - poll_sec: Messintervall
#
# FALLBACK OHNE SENSOR („CLOCK MODE“)
# ──────────────────────────────────
# Wenn light_sensor=None:
#   - zwischen OROMA_DAY_START_H und OROMA_NIGHT_START_H wird lux=80 simuliert
#   - außerhalb dieses Fensters lux=10
# => damit läuft Day/Dream stabil auch ohne Kamera/Light-Sampling.
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
#   OROMA_NIGHTMODE_LIGHT_THRESHOLD=25    # 0..100
#   OROMA_NIGHTMODE_DELAY_MINUTES=30      # Minuten
#   OROMA_CIRCADIAN_POLL_SEC=30           # Sekunden
#   OROMA_CIRCADIAN_HYSTERESIS=5          # 0..100
#
# Clock-Fallback:
#   OROMA_DAY_START_H=8
#   OROMA_NIGHT_START_H=22
#
# ÖFFENTLICHE API
# ───────────────
# class CircadianController:
#   start() / stop()
#   get_status() -> dict  (phase, threshold, delay, hysteresis, last_lux, source, timestamps)
#   update_config(threshold=?, delay_min=?, poll_sec=?, hysteresis=?)
#   force_mode("DAY"|"DREAM", reason="")
#
# KOMPATIBILITÄT (WICHTIG)
# ───────────────────────
# Bestands-Code erwartet:
#   - _set_mode(mode: str) existiert (wird intern genutzt)
#   - get_status() liefert mindestens "phase" + Schwellwerte
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import time
import threading
import logging
from datetime import datetime, timedelta
from queue import Queue
from typing import Callable, Optional, Dict, Any
from core.log_guard import log_suppressed
import logging

# -----------------------------------------------------------------------------
# Robustes Logging (kein harter Fail bei fehlenden Rechten)
# -----------------------------------------------------------------------------
LOG_DIR = os.environ.get("OROMA_LOG_DIR", "/opt/ai/oroma/logs")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    for _fallback in ("/var/tmp/oroma_logs", "/tmp/oroma_logs"):
        try:
            os.makedirs(_fallback, exist_ok=True)
            LOG_DIR = _fallback
            break
        except Exception:
            continue

logger = logging.getLogger("oroma.circadian")
if not logger.handlers:
    logger.setLevel(getattr(logging, os.environ.get("OROMA_LOG_LEVEL", "INFO").upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [Circadian] %(message)s")
    try:
        fh = logging.FileHandler(os.path.join(LOG_DIR, "circadian.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

# -----------------------------------------------------------------------------
# Controller
# -----------------------------------------------------------------------------
class CircadianController:
    def __init__(
        self,
        light_sensor: Optional[Callable[[], float]] = None,
        event_queue: Optional[Queue] = None,
        on_mode_change: Optional[Callable[[str], None]] = None,
    ):
        """
        light_sensor: Callable -> float (0..100). Darf None sein (clock-fallback).
        event_queue:  Queue zum Senden von "DAY"/"DREAM" an das Hauptsystem.
        on_mode_change: Optionaler Callback für direkte Steuerung (z. B. DreamWorker).
        """
        self.light_sensor = light_sensor
        self.event_queue = event_queue
        self.on_mode_change = on_mode_change

        # Konfiguration (ENV mit Defaults)
        self.night_threshold: int = int(os.getenv("OROMA_NIGHTMODE_LIGHT_THRESHOLD", "25"))
        self.delay_minutes: int = int(os.getenv("OROMA_NIGHTMODE_DELAY_MINUTES", "30"))
        self.poll_sec: int = int(os.getenv("OROMA_CIRCADIAN_POLL_SEC", "30"))
        self.hysteresis: int = int(os.getenv("OROMA_CIRCADIAN_HYSTERESIS", "5"))

        # Clock-Fallback Fenster
        self.day_start_h: int = int(os.getenv("OROMA_DAY_START_H", "8"))
        self.night_start_h: int = int(os.getenv("OROMA_NIGHT_START_H", "22"))

        # Laufzeit
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._current_mode: str = "DAY"
        self._last_dark: Optional[datetime] = None
        self._last_bright: Optional[datetime] = None
        self._last_lux: Optional[float] = None
        self._lux_source: str = "sensor" if (light_sensor is not None) else "clock"

        logger.info(
            "CircadianController init – threshold=%d, delay=%dmin, poll=%ds, hysteresis=%d, source=%s",
            self.night_threshold, self.delay_minutes, self.poll_sec, self.hysteresis, self._lux_source
        )

    # -------------------------- Public Lifecycle -----------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("CircadianController bereits aktiv")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("CircadianController gestartet")

    def stop(self) -> None:
        self._stop_event.set()
        t = self._thread
        if t:
            try:
                t.join(timeout=max(2, self.poll_sec + 1))
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.circadian_controller.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        self._thread = None
        logger.info("CircadianController gestoppt")

    # -------------------------- Config / Control -----------------------------

    def update_config(
        self,
        threshold: Optional[int] = None,
        delay_min: Optional[int] = None,
        poll_sec: Optional[int] = None,
        hysteresis: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Laufzeit-Update der Parameter (alle optional)."""
        if threshold is not None:
            self.night_threshold = max(0, min(100, int(threshold)))
        if delay_min is not None:
            self.delay_minutes = max(0, int(delay_min))
        if poll_sec is not None:
            self.poll_sec = max(1, int(poll_sec))
        if hysteresis is not None:
            self.hysteresis = max(0, int(hysteresis))
        logger.info(
            "Config aktualisiert – threshold=%d, delay=%dmin, poll=%ds, hysteresis=%d",
            self.night_threshold, self.delay_minutes, self.poll_sec, self.hysteresis
        )
        return self.get_status()

    def force_mode(self, mode: str, reason: str = "") -> None:
        """Hartes Umschalten (z. B. für manuelle Overrides aus der UI)."""
        mode = mode.upper().strip()
        if mode not in ("DAY", "DREAM"):
            logger.error("force_mode: ungültiger Modus: %s", mode)
            return
        logger.info("force_mode → %s (%s)", mode, reason or "no reason")
        self._set_mode(mode)

    # -------------------------- Main Loop ------------------------------------

    def _run(self) -> None:
        # Startzustand nach aktueller Helligkeit bestimmen
        try:
            lux0 = self._read_light()
            if lux0 < self.night_threshold:
                self._last_dark = datetime.now()
            else:
                self._last_bright = datetime.now()
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.circadian_controller.pass.2",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

        while not self._stop_event.is_set():
            try:
                lux = self._read_light()
                now = datetime.now()

                # DREAM-Bedingung: (lux < threshold) für >= delay_minutes
                if lux < self.night_threshold:
                    if self._last_dark is None:
                        self._last_dark = now
                        logger.debug("Dunkelphase gestartet (lux=%.1f < %d)", lux, self.night_threshold)
                    # Timer für Dunkelheit läuft
                    if (now - self._last_dark) >= timedelta(minutes=self.delay_minutes):
                        if self._current_mode != "DREAM":
                            self._set_mode("DREAM")
                else:
                    # hell
                    self._last_dark = None
                    self._last_bright = now
                    # Rückkehr zu DAY mit Hysterese (flipflop vermeiden)
                    if self._current_mode != "DAY":
                        if lux >= (self.night_threshold + self.hysteresis):
                            self._set_mode("DAY")

            except Exception as e:
                logger.error("CircadianController Fehler: %s", e)

            time.sleep(self.poll_sec)

    # -------------------------- Helpers --------------------------------------

    def _read_light(self) -> float:
        """Liest aktuelle 'Lux' (0..100). Sensor bevorzugt, sonst Uhrzeit-Fallback."""
        if self.light_sensor is not None:
            try:
                v = float(self.light_sensor())
                if v != v:  # NaN
                    raise ValueError("sensor returned NaN")
                v = max(0.0, min(100.0, v))
                self._last_lux = v
                self._lux_source = "sensor"
                return v
            except Exception as e:
                logger.warning("Lichtsensor nicht verfügbar (%s) – nutze clock-fallback", e)

        # Fallback: Uhrzeitfenster → Helligkeit grob schätzen
        now = datetime.now()
        day = self.day_start_h % 24
        night = self.night_start_h % 24
        h = now.hour

        if day < night:
            # z. B. 8..22 → DAY
            is_day = day <= h < night
        else:
            # z. B. 22..8 → über Mitternacht → DAY wenn (h >= day) oder (h < night)
            is_day = (h >= day) or (h < night)

        v = 80.0 if is_day else 10.0
        self._last_lux = v
        self._lux_source = "clock"
        return v

    def _set_mode(self, mode: str) -> None:
        """Zentraler Umschaltpunkt (von run_oroma.py wird diese Methode gepatcht!)."""
        mode = mode.upper()
        if mode not in ("DAY", "DREAM"):
            logger.error("_set_mode: ungültiger Modus: %s", mode)
            return
        if self._current_mode == mode:
            return
        self._current_mode = mode
        logger.info("Modus gewechselt zu: %s", mode)
        if self.event_queue:
            try:
                self.event_queue.put(mode)
            except Exception as e:
                logger.error("event_queue.put Fehler: %s", e)
        if self.on_mode_change:
            try:
                self.on_mode_change(mode)
            except Exception as e:
                logger.error("on_mode_change Fehler: %s", e)

    # -------------------------- Status ---------------------------------------

    def get_status(self) -> Dict[str, Any]:
        return {
            "phase": self._current_mode,
            "last_dark": self._last_dark.isoformat() if self._last_dark else None,
            "last_bright": self._last_bright.isoformat() if self._last_bright else None,
            "threshold": self.night_threshold,
            "delay_min": self.delay_minutes,
            "poll_sec": self.poll_sec,
            "hysteresis": self.hysteresis,
            "lux": self._last_lux,
            "lux_source": self._lux_source,
            "day_start_h": self.day_start_h,
            "night_start_h": self.night_start_h,
        }


# -----------------------------------------------------------------------------
# Selftest (optional)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Simulierter Sensor: 20s dunkel, 20s hell im Wechsel
    from collections import deque

    seq = deque([10.0]*20 + [85.0]*20)

    def fake_sensor():
        v = seq[0]
        seq.rotate(-1)
        return v

    q = Queue()

    def cb(mode: str):
        print("[CALLBACK]", mode)

    cc = CircadianController(light_sensor=fake_sensor, event_queue=q, on_mode_change=cb)
    cc.update_config(threshold=25, delay_min=0, poll_sec=1, hysteresis=5)
    cc.start()
    try:
        for _ in range(60):
            while not q.empty():
                print("[EVENT]", q.get(), cc.get_status())
            time.sleep(1)
    except KeyboardInterrupt as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.circadian_controller.pass.3",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    finally:
        cc.stop()
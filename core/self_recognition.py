#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/self_recognition.py
# Projekt: ORÓMA v3.7
# Version: v1.0 (Generic Sensorimotor Self-Recognition)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Generische, hardware-agnostische Selbsterkennung via Sensor-Motor-Korrelation.
#   Idee: „Ich erzeuge ein bekanntes Muster (Emitter) → sehe es in meinen
#   Sensoren (Observer) wieder → hohe Korrelation ⇒ das war ich selbst.“
#
#   • Emitter (Erzeuger):   LED-Blink, PiCar-Lenk-Wiggle, (später: Audio-Beep, Servo-Nick, …)
#   • Observer (Beobachter): Kamera-Helligkeit/Bewegung, (später: Mikro → Ton-FFT)
#   • Metrik: normierte Kreuzkorrelation zwischen Steuer-Signal u. Sensor-Zeitreihe
#
# Ergebnis
# ────────
#   run_auto() liefert dict:
#     { ok, score, method, duration, samples, note }
#   und loggt:
#     • reward.log("self_rec", +0.2) bei ausreichendem score
#     • insert_metric("self_rec_score", score)
#
# Abhängigkeiten
# ──────────────
#   • Ohne OpenCV; nur numpy (optional). Kamera via core.device_hub (falls vorhanden)
#   • Keine Hard-Fails: Fehlt Hardware/Permission, wird sauber „skip“ zurückgegeben.
#
# Hinweise
# ────────
#   • LED-Emitter nutzt /sys/class/leds/led0 (Pi ACT-LED). Kein root? → skip.
#   • PiCar-Emitter nutzt wrappers.picar_wrapper, falls vorhanden.
#   • Observer liest Frames aus DeviceHub (get_hub().get_frame / read_frame).
# =============================================================================

from __future__ import annotations
import os, time, math, json
from typing import Optional, List, Tuple, Dict, Any

import logging
from core import log_guard
logger = logging.getLogger(__name__)
try:
    import numpy as _np  # optional, aber hilfreich
except Exception:
    _np = None  # Fallback ohne numpy

from core import reward, sql_manager

# ------------------------- Hilfsfunktionen -----------------------------------

def _now() -> float:
    return time.time()

def _mean(xs: List[float]) -> float:
    return sum(xs) / max(1, len(xs))

def _norm(zs: List[float]) -> List[float]:
    if not zs:
        return []
    m = _mean(zs)
    sd = math.sqrt(sum((z - m) ** 2 for z in zs) / max(1, len(zs)))
    if sd <= 1e-9:
        return [0.0 for _ in zs]
    return [(z - m) / sd for z in zs]

def _corr(a: List[float], b: List[float]) -> float:
    """Pearson-Korrelation zweier gleich langer Listen (0 bei Edge-Cases)."""
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    an, bn = _norm(a), _norm(b)
    return float(_mean([x * y for x, y in zip(an, bn)]))

# ------------------------- Observer (Sensor) ---------------------------------

class CameraObserver:
    """
    Liest Frames via DeviceHub (falls verfügbar) und liefert eine skalare Metrik:
      • brightness_mean: globale Helligkeit (0..255)
      • motion_energy  : mittlere absolute Frame-Differenz (pix/abs)
    """
    def __init__(self, mode: str = "motion_energy"):
        self.mode = mode
        self._hub = None
        self._last = None

        try:
            from core.device_hub import get_hub  # type: ignore
            self._hub = get_hub()
            try:
                self._hub.start()
            except Exception as e:
                log_guard.log_suppressed(logger, key="self_recognition.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        except Exception:
            self._hub = None

    def read_value(self) -> Optional[float]:
        if self._hub is None:
            return None
        frame = None
        # Mehrere Getter-Varianten erlauben:
        for getter in ("get_frame", "read_frame", "read", "last_frame"):
            if hasattr(self._hub, getter):
                try:
                    got = getattr(self._hub, getter)()
                    frame = got[1] if (isinstance(got, (tuple, list)) and len(got) >= 2) else got
                    break
                except Exception:
                    frame = None
        if frame is None:
            return None

        try:
            import numpy as np  # lokal importieren, falls verfügbar
            arr = np.asarray(frame)
            if arr.ndim == 3 and arr.shape[2] >= 3:
                # grobe Luminanz
                y = 0.2126 * arr[..., 2] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 0]
            elif arr.ndim == 2:
                y = arr.astype("float32")
            else:
                y = arr.astype("float32")
        except Exception:
            # Ohne numpy: sehr grobe Approx – keine Motion möglich
            return None

        if self.mode == "brightness_mean":
            return float(y.mean())
        else:
            # motion_energy: Differenz zu letztem Frame
            if self._last is None:
                self._last = y
                return 0.0
            try:
                diff = (y - self._last)
                self._last = y
                # mittlere absolute Differenz
                import numpy as np
                return float(np.mean(np.abs(diff)))
            except Exception:
                return None

# ------------------------- Emitter (Motor/Aktor) -----------------------------

class LedEmitter:
    """
    Nutzt die Board-ACT-LED über /sys/class/leds/led0.
    Erzeugt ein binäres Muster (on/off) bei 'trigger=none'.
    """
    LED_PATH = "/sys/class/leds/led0"

    def __init__(self, on_val: int = 1, off_val: int = 0):
        self.on_val = on_val
        self.off_val = off_val
        self._prev_trigger = None
        self._ok = os.path.isdir(self.LED_PATH) and os.access(self.LED_PATH, os.W_OK)

    def _write(self, rel: str, val: str) -> bool:
        try:
            with open(os.path.join(self.LED_PATH, rel), "w") as f:
                f.write(val)
            return True
        except Exception:
            return False

    def available(self) -> bool:
        return self._ok

    def _set_trigger(self, trig: str) -> None:
        self._write("trigger", trig)

    def _set_brightness(self, v: int) -> None:
        self._write("brightness", str(int(v)))

    def begin(self) -> None:
        if not self._ok:
            return
        # aktuellen Trigger merken
        try:
            with open(os.path.join(self.LED_PATH, "trigger"), "r") as f:
                self._prev_trigger = f.read().strip()
        except Exception:
            self._prev_trigger = None
        # eigenen Modus setzen
        self._set_trigger("none")
        # initial aus
        self._set_brightness(self.off_val)

    def emit_step(self, on: bool) -> None:
        if not self._ok:
            return
        self._set_brightness(self.on_val if on else self.off_val)

    def end(self) -> None:
        if not self._ok:
            return
        # LED wieder an Timer hängen? sicher ist sicher: „timer“ aus → standard „mmc“/default
        if self._prev_trigger:
            self._set_trigger(self._prev_trigger)
        else:
            self._set_trigger("timer")
            self._write("delay_on", "0")
            self._write("delay_off", "0")

class PiCarWiggleEmitter:
    """
    Kleiner Lenk-Wiggle (links/rechts) bei vorhandenem PiCar-Wrapper.
    Erzeugt ein Muster (±delta) mit kurzer Ruhephase dazwischen.
    """
    def __init__(self, delta: float = 0.2):
        self.delta = float(delta)
        self._car = None
        try:
            from wrappers.picar_wrapper import PiCar  # type: ignore
            self._car = PiCar()
        except Exception:
            self._car = None

    def available(self) -> bool:
        return self._car is not None

    def begin(self) -> None:
        pass

    def emit_step(self, on: bool) -> None:
        if not self._car:
            return
        try:
            steer = +self.delta if on else -self.delta
            self._car.steer_relative(steer)  # kleine Lenkabweichung
            time.sleep(0.05)
            self._car.steer_relative(-steer)  # zurück
        except Exception as e:
            log_guard.log_suppressed(logger, key="self_recognition.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    def end(self) -> None:
        try:
            if self._car:
                self._car.steer_absolute(0.0)
        except Exception as e:
            log_guard.log_suppressed(logger, key="self_recognition.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

# ------------------------- Self-Recognition Core -----------------------------

class SelfRecognizer:
    """
    Führt eine kurze Session durch:
      • toggelt den Emitter nach Muster (on/off Duty)
      • sampelt Observer-Metrik (Hz ~ 15-25)
      • berechnet Korrelation zwischen Muster und Messwerten
    """
    def __init__(self, emitter, observer: CameraObserver):
        self.emitter = emitter
        self.observer = observer

    def run(self, duration_s: float = 2.5, hz: float = 20.0, duty: float = 0.2) -> Dict[str, Any]:
        """
        duration_s: Gesamtdauer
        hz        : Abtastrate Observer
        duty      : Anteil „on“ je Zyklus (0..1), Muster: [on..off]
        """
        dt = 1.0 / max(1.0, float(hz))
        steps = max(5, int(duration_s / dt))

        sig_ctrl: List[float] = []
        sig_obs : List[float] = []

        self.emitter.begin()
        try:
            for i in range(steps):
                phase = (i % 10) / 10.0  # 10-Step Zyklus
                on = phase < duty        # z. B. 0.2 → 2 on, 8 off
                # Aktor schalten
                try:
                    self.emitter.emit_step(on)
                except Exception as e:
                    log_guard.log_suppressed(logger, key="self_recognition.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

                # Observer messen
                val = self.observer.read_value()
                if val is None:
                    # wenn kein Wert: neutral 0 anhängen (beeinträchtigt, aber robust)
                    val = 0.0

                sig_ctrl.append(1.0 if on else 0.0)
                sig_obs.append(float(val))

                time.sleep(dt)
        finally:
            try:
                self.emitter.end()
            except Exception as e:
                log_guard.log_suppressed(logger, key="self_recognition.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        score = _corr(sig_ctrl, sig_obs)
        return {
            "ok": bool(score >= 0.4),   # heuristische Schwelle
            "score": float(score),
            "duration": float(duration_s),
            "samples": len(sig_obs),
            "ctrl_preview": sig_ctrl[:20],
            "obs_preview": sig_obs[:20],
        }

# ------------------------- Public API ----------------------------------------

def run_auto(prefer: str = "auto") -> Dict[str, Any]:
    """
    Wählt automatisch Emitter+Observer:
      • LED + Kamera, falls möglich
      • sonst PiCar-Wiggle + Kamera
      • ansonsten -> skip
    """
    # Observer
    obs = CameraObserver(mode="motion_energy")
    # Emitter-Order je „prefer“
    emitters = []
    if prefer in ("auto", "led"):
        emitters.append(("led", LedEmitter()))
    if prefer in ("auto", "picar"):
        emitters.append(("picar", PiCarWiggleEmitter()))

    for name, em in emitters:
        if not hasattr(em, "available") or not em.available():
            continue
        rec = SelfRecognizer(emitter=em, observer=obs).run()
        rec["method"] = name
        # Logging
        try:
            sql_manager.insert_metric("self_rec_score", rec.get("score", 0.0))
        except Exception as e:
            log_guard.log_suppressed(logger, key="self_recognition.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        if rec.get("ok"):
            try:
                reward.log("self_rec", +0.2, info={"method": name, "score": rec.get("score")})
            except Exception as e:
                log_guard.log_suppressed(logger, key="self_recognition.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
            rec["note"] = "self recognized via sensorimotor correlation"
            return rec
    return {"ok": False, "score": 0.0, "method": "none", "duration": 0.0, "samples": 0, "note": "no emitter/observer available or low correlation"}
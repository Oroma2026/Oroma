#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/wrappers/sensor_ir_front.py
# Projekt: ORÓMA
# Modul:   Front-IR-Sensor (Abstand) als DeviceHub-SensorChannel
# Version: v3.8-r1
# Stand:   2025-12-07
# Autor:   ORÓMA · KI-JWG-X1 + Jörg
# =============================================================================
#
# Zweck
# ─────
#   Implementiert einen konkreten SensorChannel für einen Front-IR-/Abstandssensor
#   und registriert diesen beim DeviceHub.
#
#   Unterstützt zwei Modi:
#     • Hardware-Modus  (Platzhalter, kann später mit echter Bibliothek gefüllt werden)
#     • Simulationsmodus (Default, sicher auf jedem System lauffähig)
#
# =============================================================================

from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, Optional

from core.sensor_channel import BaseSensorChannel
from core.device_hub import DeviceHub

try:
    # Platzhalter – echte HW-Bibliotheken können später ergänzt werden
    import board  # type: ignore
    import busio  # type: ignore
except Exception:  # pragma: no cover
    board = None  # type: ignore
    busio = None  # type: ignore


class IRFrontSensor(BaseSensorChannel):
    """
    Konkreter SensorChannel für einen Front-IR-/Abstandssensor.
    """

    def __init__(
        self,
        name: str = "front_ir",
        origin: str = "sensor/ir/front",
        namespace: str = "sensor",
        interval_sec: float = 0.1,
        meta_base: Optional[Dict[str, Any]] = None,
    ) -> None:
        meta = dict(meta_base or {})
        meta.setdefault("created_by", "sensor_ir_front")
        meta.setdefault("role", "distance_front")

        super().__init__(
            name=name,
            kind="ir_distance",
            origin=origin,
            namespace=namespace,
            interval_sec=interval_sec,
            meta_base=meta,
            weight=1.0,
            notes="sensor_sample",
            version="v3.8",
        )

        sim_env = os.environ.get("OROMA_IR_FRONT_SIMULATION", "1")
        self._simulate = sim_env not in ("0", "false", "False", "no", "off")

        max_cm = float(os.environ.get("OROMA_IR_FRONT_MAX_CM", "100.0"))
        self._max_cm = max(1.0, max_cm)

        self._hw_available = False
        self._hw_info: Dict[str, Any] = {}
        if not self._simulate and board is not None and busio is not None:
            try:
                # Hier könnte echte HW initialisiert werden (z.B. I2C-Sensor)
                # self._sensor = IRSensor(...)
                self._hw_available = True
                self._hw_info = {"driver": "custom_ir_driver", "bus": "I2C"}
            except Exception:
                self._simulate = True
                self._hw_available = False

    # ------------------------------------------------------------------
    # BaseSensorChannel-Implementierung
    # ------------------------------------------------------------------

    def read_raw(self) -> Dict[str, Any]:
        if not self._simulate and self._hw_available:
            # Hier echte Hardwareabfrage einbauen
            distance_cm = 42.0
            signal_ok = True
            raw_data = {"hw": True, "info": self._hw_info}
        else:
            # Simulation: sanft pendelnde Distanzkurve
            t = time.time()
            amplitude = (self._max_cm - 10.0) / 2.0
            base = 10.0 + amplitude
            distance_cm = base + amplitude * math.sin(t / 3.0)
            signal_ok = True
            raw_data = {"hw": False, "simulated": True}

        return {
            "distance_cm": float(distance_cm),
            "signal_ok": bool(signal_ok),
            "raw": raw_data,
        }

    def build_snap_payload(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        dist_cm = float(raw.get("distance_cm", 0.0))
        signal_ok = bool(raw.get("signal_ok", False))

        d_norm = max(0.0, min(1.0, dist_cm / self._max_cm))
        ok_float = 1.0 if signal_ok else 0.0

        return {
            "kind": "ir_distance",
            "v": [d_norm, ok_float],
            "distance_cm": dist_cm,
            "signal_ok": signal_ok,
            "raw": raw.get("raw", {}),
        }


def register_front_ir(interval_sec: float = 0.1, meta_base: Optional[Dict[str, Any]] = None) -> None:
    """
    Convenience-Funktion:
      • erzeugt einen IRFrontSensor
      • registriert ihn beim DeviceHub
    """
    hub = DeviceHub.instance()
    ch = IRFrontSensor(interval_sec=interval_sec, meta_base=meta_base)
    hub.register_sensor_channel(ch)
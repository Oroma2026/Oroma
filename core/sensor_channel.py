#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/sensor_channel.py
# Projekt:   ORÓMA (Headless · Device Abstraction · Snap-Compatible)
# Modul:     SensorChannel – abstrakte Basis für periodische Sensorsignale (IR/Ultraschall/IMU/Temp …) inkl. SnapChain-Datenaufbereitung
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul definiert eine generische Abstraktion für Sensoren als „Kanäle“.
# Ein SensorChannel ist eine logische Quelle, die regelmäßig (polling) Rohdaten liefert
# und daraus ORÓMA-kompatible SnapChain-Inserts erzeugen kann.
#
# Wichtig: Diese Datei kennt bewusst NICHT den DeviceHub und keine konkrete Hardware.
# Hardware-Anbindung (GPIO/I2C/SPI/USB) gehört in Subklassen, typischerweise unter:
#   wrappers/...
# Der DeviceHub kann Channels lediglich registrieren und zyklisch abfragen.
#
# KERNKLASSE: BaseSensorChannel
# ────────────────────────────
# BaseSensorChannel ist eine abc.ABC und stellt bereit:
#   - Polling-Logik:
#       due(now=None) -> bool
#       mark_polled(now=None) -> None
#   - Abstrakte Sensor-I/O:
#       read_raw() -> Dict[str,Any]               (MUSS in Subklasse)
#       build_snap_payload(raw) -> Dict[str,Any]  (MUSS in Subklasse)
#   - Default-Qualität:
#       compute_quality(raw) -> float  (0..1; kann überschrieben werden)
#   - SnapChain-Insert-Builder:
#       build_snapchain_data(raw, ts=None) -> (data_dict, quality)
#
# INIT-PARAMETER (EXAKT IM CODE)
# ─────────────────────────────
# __init__(
#   name: str,             # Kanalname, z. B. "front_ir"
#   kind: str,             # Sensortyp, z. B. "ir", "imu", "temp"
#   origin: str,           # snapchains.origin, z. B. "sensor:front_ir"
#   namespace: str="sensor",
#   interval_sec: float=0.1,
#   meta_base: Optional[Dict[str,Any]]=None,   # Basis-Meta, wird in Payload gemerged
#   weight: float=1.0,     # optionales Gewicht (z. B. für Training/Ranking)
#   notes: str="",
#   version: str="v3.8"    # (historisch im Code), in Header v3.7.3 dokumentiert
# )
#
# Zeitsteuerung:
# - self._next_ts wird intern geführt; due() prüft now >= _next_ts.
# - mark_polled() setzt _next_ts = now + interval_sec.
#
# RAW → PAYLOAD → SNAPCHAIN INSERT (WICHTIGER VERTRAG)
# ───────────────────────────────────────────────────
# read_raw() liefert Rohwerte, z. B.:
#   {"distance_cm": 42.3, "signal_ok": True}
#   {"ax":0.1,"ay":-0.05,"az":1.0,"gx":...}
#
# build_snap_payload(raw) MUSS ein Dict liefern, das als JSON in snapchains.blob landet.
# Mindestfelder laut Docstring:
#   - "kind": str
#   - "v": List[float]   (normierter Feature-Vektor)
# Zusätzlich sind weitere Felder erlaubt (Rohwerte, Flags, Debug).
#
# compute_quality(raw) Default:
#   - wenn "signal_ok" vorhanden:
#       True  → 1.0
#       False → 0.0
#   - sonst:
#       1.0
# Subklassen dürfen compute_quality überschreiben (z. B. SNR/Range/Validity).
#
# build_snapchain_data(raw, ts=None) erstellt:
#   - payload = build_snap_payload(raw)
#   - payload wird JSON-serialisiert und als bytes abgelegt (utf-8)
#   - quality = compute_quality(raw)
#   - Rückgabe: (data_dict, quality)
#
# data_dict ist kompatibel zu core.sql_manager.insert_snapchain():
#   {
#     "ts":        <int unix sec>,
#     "quality":   <float>,
#     "blob":      <bytes>,            # JSON bytes
#     "exported":  0,
#     "status":    "active",
#     "origin":    self.origin,
#     "gap_flag":  0,
#     "notes":     self.notes or "sensor_sample",
#     "namespace": self.namespace,
#     "source_id": None,
#     "version":   self.version,
#     "weight":    self.weight,
#   }
#
# DESIGN: WARUM build_snapchain_data HIER UND NICHT IM sql_manager?
# ────────────────────────────────────────────────────────────────
# - Sensor-spezifische Payload-Generierung bleibt in der Sensor-Schicht.
# - sql_manager bleibt generisch (DB-Insert/Schema/PRAGMA).
# - Dadurch kann der DeviceHub Channels abfragen, ohne Sensor-Details zu kennen.
#
# FEHLERROBUSTHEIT / PRODUKTION
# ─────────────────────────────
# - build_snapchain_data importiert json lokal, um zyklische Imports zu vermeiden.
# - Exceptions in Subklassen (read_raw/build_snap_payload) sollen vom Caller (DeviceHub)
#   abgefangen werden; diese Basisklasse erzwingt bewusst keine Retry-Schleifen.
#
# ENV
# ───
# Dieses Modul definiert keine eigenen ENV-Schalter.
# Scheduling/Intervall ist ein Konstruktor-Parameter und kann aus DeviceHub/Config kommen.
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class BaseSensorChannel(abc.ABC):
#   - due(now=None) -> bool
#   - mark_polled(now=None) -> None
#   - read_raw() -> Dict[str,Any]                     (abstract)
#   - build_snap_payload(raw) -> Dict[str,Any]        (abstract)
#   - compute_quality(raw) -> float                   (default impl)
#   - build_snapchain_data(raw, ts=None) -> (dict, float)
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Diese Datei bleibt hardware-agnostisch (keine GPIO/I2C Imports).
# - build_snap_payload bleibt abstrakt (Subklassen definieren Feature-Vektor-Format).
# - build_snapchain_data muss weiterhin ein sql_manager-kompatibles Dict liefern
#   (damit DeviceHub/Orchestrator keinen Sonderpfad brauchen).
# - Polling-Mechanik (due/mark_polled) muss deterministisch bleiben.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import abc
import time
from typing import Any, Dict, Optional, Tuple


class BaseSensorChannel(abc.ABC):
    """
    Abstrakte Basisklasse für einen Sensor-Kanal.

    Ein Channel repräsentiert eine logische Sensorquelle, z.B.:
      • IR-Sensor vorne ("front_ir")
      • Ultraschall links ("ultra_left")
      • IMU / Gyro ("imu_main")
      • Temperatur ("temp_room")

    Die konkrete Hardware-Anbindung (GPIO, I2C, SPI usw.) wird in Subklassen
    implementiert (typischerweise unter wrappers/).
    """

    def __init__(
        self,
        name: str,
        kind: str,
        origin: str,
        namespace: str = "sensor",
        interval_sec: float = 0.1,
        meta_base: Optional[Dict[str, Any]] = None,
        weight: float = 1.0,
        notes: str = "",
        version: str = "v3.8",
    ) -> None:
        """
        Parameter
        ---------
        name : str
            Interner Name des Channels (z.B. "front_ir").
        kind : str
            Semantische Sensorart (z.B. "ir_distance", "ultrasonic", "imu").
        origin : str
            Origin-Feld in snapchains (z.B. "sensor/ir/front").
        namespace : str
            Namespace-Feld in snapchains (Standard: "sensor").
        interval_sec : float
            Ziel-Abtastintervall in Sekunden.
        meta_base : dict | None
            Basis-Metadaten, die in den Snap-Blob übernommen werden sollen.
        weight : float
            Gewichtungs-Feld für snapchains (Standard: 1.0).
        notes : str
            Notizfeld für snapchains (z.B. "sensor_sample").
        version : str
            Version-Tag, das in snapchains.version geschrieben werden kann.
        """
        self.name = str(name)
        self.kind = str(kind)
        self.origin = str(origin)
        self.namespace = str(namespace)
        self.interval_sec = float(interval_sec)
        self.meta_base: Dict[str, Any] = dict(meta_base or {})
        self.weight = float(weight)
        self.notes = str(notes or "")
        self.version = str(version)

        # interne Zeitverwaltung für Polling
        self._next_ts: float = 0.0

    # -------------------------------------------------------------------------
    # Polling-Hilfen
    # -------------------------------------------------------------------------

    def due(self, now: Optional[float] = None) -> bool:
        """
        True, wenn dieser Channel wieder abgefragt werden sollte.
        """
        t = float(now if now is not None else time.time())
        return t >= self._next_ts

    def mark_polled(self, now: Optional[float] = None) -> None:
        """
        Aktualisiert den nächsten Poll-Zeitpunkt nach einem erfolgreichen Read.
        """
        t = float(now if now is not None else time.time())
        self._next_ts = t + max(0.001, self.interval_sec)

    # -------------------------------------------------------------------------
    # Abstrakte Methoden für konkrete Implementierungen
    # -------------------------------------------------------------------------

    @abc.abstractmethod
    def read_raw(self) -> Dict[str, Any]:
        """
        Liest einen Rohwert vom Sensor und gibt ihn als Dict zurück.

        Beispiele:
          IR-Distanz:
            {"distance_cm": 42.3, "signal_ok": True}
          IMU:
            {"ax": 0.1, "ay": -0.05, "az": 1.0, "gx": ...}
        """
        raise NotImplementedError

    @abc.abstractmethod
    def build_snap_payload(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Baut das JSON-Blob für die snapchains.blob-Spalte.

        Muss mindestens enthalten:
          kind : str
          v    : list[float]  – normierter Feature-Vektor

        Weitere Felder (z.B. Rohwerte, Flags) können ergänzt werden.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Standard-Implementierungen (können überschrieben werden)
    # -------------------------------------------------------------------------

    def compute_quality(self, raw: Dict[str, Any]) -> float:
        """
        Leitet einen Qualitätswert aus den Rohdaten ab (0..1).

        Default:
          - wenn "signal_ok" vorhanden ist:
              True  → 1.0
              False → 0.0
          - sonst: 1.0

        Konkrete Channels können dies bei Bedarf überschreiben.
        """
        signal_ok = raw.get("signal_ok")
        if isinstance(signal_ok, bool):
            return 1.0 if signal_ok else 0.0
        return 1.0

    def build_snapchain_data(
        self,
        raw: Dict[str, Any],
        ts: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], float]:
        """
        Liefert ein Dict, das direkt an sql_manager.insert_snapchain()
        übergeben werden kann, sowie den berechneten Qualitätswert.

        Rückgabe:
          (data_dict, quality)

        data_dict enthält mindestens:
          ts, quality, blob, origin, namespace, notes, version, weight
        """
        import json  # lokal, um zyklische Imports zu vermeiden

        t = int(ts if ts is not None else time.time())
        q = float(self.compute_quality(raw))

        payload = self.build_snap_payload(raw)

        # Metadaten injizieren
        meta = dict(self.meta_base)
        meta.update(payload.get("meta", {}))
        payload["meta"] = meta

        blob_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        data: Dict[str, Any] = {
            "ts": t,
            "quality": q,
            "blob": blob_bytes,
            "exported": 0,
            "status": "active",
            "origin": self.origin,
            "gap_flag": 0,
            "notes": self.notes or "sensor_sample",
            "namespace": self.namespace,
            "source_id": None,
            "version": self.version,
            "weight": self.weight,
        }
        return data, q
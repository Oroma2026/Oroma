#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hailo_wrapper.py – ORÓMA v3.5 Patch 2.1
========================================
Wrapper für Hailo-NPU-Integration (26 TOPS) mit optionalem DeGirum-Backend
und CPU-Fallback. Dient als *einheitliche* Abstraktionsschicht für Vision-
/Embedding-Inferenz in ORÓMA (z. B. für Overlay-, Predictor- und LZG-Pfade).

ZIELE & SCOPE (Patch 2.1)
-------------------------
• NPU-first: Embeddings/Inference bevorzugt auf Hailo ausführen, CPU nur als Fallback.
• Einheitliche API: `infer()` (generische Inferenz) und `embed_batch()` (Embeddings).
• Monitoring: `get_status()` liefert NPU-Kennzahlen (Streams/FPS/Temp/Throughput),
  sodass die Health-UI CPU/RAM/NPU gemeinsam anzeigen kann.
• Konfigurierbare Backends via .env (ohne Codeänderung).

ÖFFENTLICHE API
---------------
class HailoWrapper:
    __init__(models_dir: str = "/opt/ai/oroma/models/hailo")
        -> Initialisiert, liest Backend aus .env (VISION_BACKEND).

    connect() -> None
        -> Verbindet NPU/DeGirum oder aktiviert Dummy-Modus.

    disconnect() -> None
        -> Schließt Device/Streams, setzt Modell zurück.

    list_models() -> list[str]
        -> Listet ModelZoo-Dateien (.hef/.onnx/.json) im models_dir.

    load_model(model_name: str) -> None
        -> Lädt ein Modell (Hailo/DeGirum) bzw. setzt Dummy.

    infer(input_data: np.ndarray) -> dict
        -> Führt eine Inferenz aus (Generika). Ergebnis wird „vereinheitlicht“
           zurückgegeben (dict mit Feldern wie 'features', 'class', 'conf', ...).

    embed_batch(batch: np.ndarray | list) -> dict
        -> Erzeugt Embeddings für einen Batch (Bilder/Features).
           Nutzt NPU, fällt bei Fehlern auf CPU zurück.
           Rückgabe z. B. {"features": [[...],[...], ...]}.

    get_status() -> dict
        -> NPU-Status: {"ok": bool, "device": "hailo"/"degirum"/"dummy",
                        "streams": int, "fps": float, "throughput": float|None,
                        "temp_c": float|None}
           (Bei Dummy/Fehler werden sinnvolle Defaults geliefert.)

    is_available() -> bool
        -> True, wenn ein echtes Backend geladen und nicht im Dummy-Modus.

UMGEBUNGSVARIABLEN (.env)
-------------------------
• VISION_BACKEND= hailo | degirum | cpu
    - "hailo":   Hailo Runtime SDK verwenden (empfohlen).
    - "degirum": DeGirum PySDK verwenden (falls vorhanden).
    - "cpu":     Immer Dummy/CPU-Fallback nutzen (z. B. für Dev/Tests).

• HAILO_MODELS_DIR= Pfad/zum/ModelZoo
    - Optional, überschreibt den Default-Pfad in __init__.

• EMPFEHLUNG: EMBEDDING_BACKEND in den LZG/Overlay-Komponenten ebenfalls auf "hailo"
  setzen, damit die gesamte Kette NPU-first arbeitet.

ABHÄNGIGKEITEN
--------------
• Python: numpy, logging, os, json
• Optional: hailo_platform (Hailo Runtime SDK), degirum (DeGirum PySDK)

THREAD-SAFETY & PERFORMANCE
---------------------------
• Einfache Thread-Synchronisation via Lock:
  Inferenz-Methoden (`infer`, `embed_batch`) verwenden ein internes Lock, um
  parallele Zugriffe auf dasselbe Device/Modell zu serialisieren. Das ist in
  Flask/WSGI-Umgebungen sinnvoll (mehrere Requests).
• Wenn hohe Parallelität benötigt wird, empfiehlt sich:
  - ein dedizierter Inferenz-Worker-Prozess (Queue) oder
  - mehrere Wrapper-Instanzen mit dedizierten Streams (SDK-abhängig).
• CPU-Fallback ist bewusst „leichtgewichtig“ (statistische Aggregation), um
  Tests/Dev nicht zu blockieren. In Produktion sollte Hailo aktiv sein.

KOMPATIBILITÄT & PLACEHOLDER
----------------------------
• Die konkreten SDK-Methodennamen können je nach Hailo/DeGirum-Version variieren.
  Daher ruft `get_status()` interne Metriken defensiv via `getattr(...)` auf.
• `self.device.load_model(...)` / `self.model.infer(...)` sind stellvertretend –
  bitte ggf. an das tatsächlich genutzte SDK binden (HEF-Load/Runner/Stream-API).

BEISPIEL (CLI)
--------------
    hw = HailoWrapper()
    hw.connect()
    print(hw.list_models())
    if hw.list_models():
        hw.load_model(hw.list_models()[0])
        x = np.random.rand(1, 224, 224, 3).astype(np.float32)
        print(hw.infer(x))
        print(hw.embed_batch(x))
        print(hw.get_status())
    hw.disconnect()

ÄNDERUNGEN ggü. v2.11
---------------------
• Deutlich erweiterter Header/Docstring.
• Neues Monitoring (get_status), neue Embedding-API (embed_batch).
• .env-gestützte Backend-Steuerung, robustere Fallbacks & Logging.
"""

from __future__ import annotations

import os
import json
import logging
import threading
from typing import List, Dict, Any
from core.log_guard import log_suppressed
import logging

import numpy as np

# Optionale Backends
try:
    import hailo_platform as hp  # Hailo Runtime SDK
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

try:
    import degirum as dg  # DeGirum PySDK (kann Hailo-Karten ansteuern)
    DEGIRUM_AVAILABLE = True
except ImportError:
    DEGIRUM_AVAILABLE = False

# Logging
logger = logging.getLogger("oroma.hailo")
logger.setLevel(logging.INFO)


class HailoWrapper:
    """
    Wrapper-Klasse für Hailo/DeGirum mit CPU/Dummy-Fallback.
    Siehe Modul-Docstring für Details zur öffentlichen API.
    """

    def __init__(self, models_dir: str = "/opt/ai/oroma/models/hailo"):
        self.models_dir = os.getenv("HAILO_MODELS_DIR", models_dir)
        self.backend = os.getenv("VISION_BACKEND", "hailo").lower()  # hailo|degirum|cpu
        self.device: Any = None
        self.model: Any = None
        self.model_name: str | None = None
        self._lock = threading.Lock()

        if not HAILO_AVAILABLE and not DEGIRUM_AVAILABLE:
            logger.warning(
                "⚠️ Weder hailo_platform noch degirum verfügbar – Fallback auf Dummy/CPU."
            )

    # ---------------------------------------------------------
    # Geräteverwaltung
    # ---------------------------------------------------------
    def connect(self) -> None:
        """
        Verbindet das gewünschte Backend. Bei „cpu“ wird Dummy aktiviert.
        """
        if self.backend == "hailo" and HAILO_AVAILABLE:
            # Hinweis: konkrete Initialisierung abhängig vom SDK.
            self.device = hp.HailoRT()  # Platzhalter
            logger.info("✅ HailoRT Device verbunden.")
        elif self.backend == "degirum" and DEGIRUM_AVAILABLE:
            # Beispielhafte DeGirum-Initialisierung:
            self.device = dg.connect(model_zoo_dir=self.models_dir)
            logger.info("✅ DeGirum SDK mit ModelZoo verbunden.")
        else:
            self.device = "DUMMY"
            logger.info("⚠️ Dummy-Device aktiv (VISION_BACKEND=%s).", self.backend)

    def disconnect(self) -> None:
        """
        Verbindung zur NPU schließen und Zustand zurücksetzen.
        """
        if self.device and self.device != "DUMMY":
            try:
                self.device.close()
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="wrappers.hailo_wrapper.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        self.device = None
        self.model = None
        self.model_name = None
        logger.info("🔌 Hailo/DeGirum-Gerät getrennt.")

    # ---------------------------------------------------------
    # Model Zoo
    # ---------------------------------------------------------
    def list_models(self) -> List[str]:
        """
        Listet die verfügbaren Modelle im ModelZoo-Verzeichnis.
        """
        if not os.path.exists(self.models_dir):
            return []
        return [
            f for f in os.listdir(self.models_dir)
            if f.endswith((".hef", ".onnx", ".json"))
        ]

    def load_model(self, model_name: str) -> None:
        """
        Lädt ein Modell (Hailo/DeGirum) oder aktiviert Dummy-Modell.
        """
        if not self.device:
            self.connect()

        model_path = os.path.join(self.models_dir, model_name)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"❌ Modell nicht gefunden: {model_path}")

        self.model_name = model_name
        if self.backend == "hailo" and HAILO_AVAILABLE and self.device != "DUMMY":
            # Platzhalter: je nach SDK muss hier ein Runner/Network-Objekt erzeugt werden.
            self.model = self.device.load_model(model_path)  # type: ignore[attr-defined]
            logger.info("✅ Modell geladen (Hailo SDK): %s", model_name)
        elif self.backend == "degirum" and DEGIRUM_AVAILABLE and self.device != "DUMMY":
            self.model = self.device.load_model(model_name)  # type: ignore[attr-defined]
            logger.info("✅ Modell geladen (DeGirum SDK): %s", model_name)
        else:
            self.model = "DUMMY"
            logger.warning("⚠️ Dummy-Modell geladen: %s", model_name)

    # ---------------------------------------------------------
    # Inferenz & Embeddings
    # ---------------------------------------------------------
    def infer(self, input_data: np.ndarray) -> Dict[str, Any]:
        """
        Führt eine (generische) Inferenz aus und gibt ein vereinheitlichtes
        Ergebnis-Dict zurück. Bei Dummy liefert es Testdaten.
        Thread-safe via internem Lock.
        """
        if not self.model:
            raise RuntimeError("❌ Kein Modell geladen (load_model() zuerst aufrufen).")

        with self._lock:
            if self.model == "DUMMY":
                return {
                    "class": "dummy",
                    "confidence": 0.0,
                    "features": input_data.tolist()[:5],
                }

            try:
                if self.backend == "hailo" and HAILO_AVAILABLE:
                    result = self.model.infer(input_data)  # type: ignore[attr-defined]
                elif self.backend == "degirum" and DEGIRUM_AVAILABLE:
                    result = self.model(input_data)       # type: ignore[misc]
                else:
                    result = {}
            except Exception as e:
                logger.error("❌ Inferenz fehlgeschlagen: %s", e)
                return {}

        return self._postprocess(result)

    def embed_batch(self, batch: np.ndarray | list) -> Dict[str, Any]:
        """
        Erzeugt Embeddings für einen Batch (Bilder/Features).
        Nutzt NPU, fällt bei Fehlern auf CPU zurück.
        Rückgabeformat: {"features": [[...], [...], ...]}.
        """
        if not isinstance(batch, np.ndarray):
            batch = np.array(batch, dtype=np.float32)

        # Versuche NPU/DeGirum
        if self.model and self.model != "DUMMY":
            try:
                out = self.infer(batch)
                # Erwartung: out enthält bereits "features" (modellabhängig).
                if isinstance(out, dict) and "features" in out:
                    return out
                # Fallback: Wenn das Modell etwas anderes liefert, in Features umwandeln (Best-Effort).
                if isinstance(out, dict) and "raw" in out:
                    return {"features": [np.array(out["raw"]).flatten().tolist()]}
            except Exception as e:
                logger.error("⚠️ Embedding via NPU fehlgeschlagen: %s", e)

        # CPU-Fallback: einfache Verdichtung (z. B. Kanal-/Raum-Mittelwert je Sample)
        try:
            feats = batch.mean(axis=tuple(range(1, batch.ndim))).tolist()
        except Exception:
            feats = np.atleast_2d(batch).tolist()
        return {"features": feats}

    # ---------------------------------------------------------
    # Status & Monitoring
    # ---------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """
        Liefert NPU-Statusinformationen (für Health-UI).
        Defensive Abfragen via getattr(), damit unterschiedliche SDK-Versionen
        keine harten Fehler verursachen.
        """
        if self.device == "DUMMY":
            return {
                "ok": False,
                "device": "dummy",
                "streams": 0,
                "fps": 0.0,
                "throughput": 0.0,
                "temp_c": None,
            }

        try:
            if self.backend == "hailo" and HAILO_AVAILABLE:
                # Beispielhafte, defensive Abfragen:
                streams = getattr(self.device, "active_streams", lambda: 1)()
                fps = getattr(self.device, "total_fps", lambda: 0.0)()
                thr = getattr(self.device, "throughput_macs", lambda: 0.0)()
                tmp = getattr(self.device, "temperature_c", lambda: None)()
                return {
                    "ok": True,
                    "device": "hailo",
                    "streams": streams,
                    "fps": fps,
                    "throughput": thr,
                    "temp_c": tmp,
                }
            elif self.backend == "degirum" and DEGIRUM_AVAILABLE:
                # DeGirum: Metriken je nach SDK/Runner; hier Minimal-Placeholder:
                return {
                    "ok": True,
                    "device": "degirum",
                    "streams": 1,
                    "fps": 0.0,
                    "throughput": None,
                    "temp_c": None,
                }
        except Exception as e:
            logger.error("⚠️ Statusabfrage fehlgeschlagen: %s", e)

        return {"ok": False, "error": "status_unavailable"}

    # ---------------------------------------------------------
    # Internes Postprocessing
    # ---------------------------------------------------------
    def _postprocess(self, result: Any) -> Dict[str, Any]:
        """
        Vereinheitlicht das Backend-Ergebnis in ein Dict.
        Erlaubt flexible Weiterverarbeitung in ORÓMA (SnapFeatures).
        """
        if isinstance(result, dict):
            return result
        if isinstance(result, np.ndarray):
            return {"features": result.tolist()}
        try:
            # Manchmal kommt ein JSON-String/Objekt zurück
            return json.loads(str(result))
        except Exception:
            return {"raw": str(result)}

    # ---------------------------------------------------------
    # Hilfsfunktionen
    # ---------------------------------------------------------
    def is_available(self) -> bool:
        """
        True, wenn ein echtes Backend (Hailo/DeGirum) aktiv ist.
        """
        return (HAILO_AVAILABLE or DEGIRUM_AVAILABLE) and self.device != "DUMMY"


# ---------------------------------------------------------
# Manueller Kurztest
# ---------------------------------------------------------
if __name__ == "__main__":
    hw = HailoWrapper()
    hw.connect()
    print("ModelZoo:", hw.list_models())
    try:
        models = hw.list_models()
        if models:
            hw.load_model(models[0])
            dummy = np.random.rand(1, 224, 224, 3).astype(np.float32)
            print("Inference:", hw.infer(dummy))
            print("Embed:", hw.embed_batch(dummy))
        print("Status:", hw.get_status())
    finally:
        hw.disconnect()
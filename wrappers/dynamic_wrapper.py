#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/v2.11/wrappers/dynamic_wrapper.py
# Projekt:   ORÓMA
# Modul:     Dynamischer Wrapper-Manager
# Version:   v2.11
# Stand:     2025-09-27
#
# Zweck / Rolle
# ─────────────
#  - Zentrale Komponente für dynamisches Laden, Umschalten und Entladen von
#    Wrapper-Modulen (z. B. Audio, Vision, TTS, Text, PiCar, Hailo, GStreamer).
#  - Abstraktionsschicht: Core bleibt stabil, während Backends flexibel
#    austauschbar sind.
#  - Erlaubt paralleles Betreiben mehrerer Wrapper.
#
# Features
# ────────
#  - Automatische Erkennung installierter Wrapper im Verzeichnis `wrappers/`.
#  - Laufzeit-Laden per `importlib` → keine statischen Importe nötig.
#  - Safe Switch: Vorheriger Wrapper wird gestoppt, bevor neuer geladen wird.
#  - Thread-sicher dank Lock (gleichzeitige Zugriffe möglich).
#  - Logging über Logger `DynamicWrapper` (Konsole).
#
# Öffentliche API
# ───────────────
#  - available_wrappers() → Liste aller Wrapper-Dateien.
#  - load(name)           → Lädt Wrapper, gibt Instanz zurück.
#  - unload(name)         → Entlädt Wrapper (inkl. `stop()` falls vorhanden).
#  - switch(old,new)      → Wechselt von Wrapper A zu B.
#  - get(name)            → Holt geladene Instanz.
#  - list_loaded()        → Gibt geladene Wrapper + Klassenname zurück.
#  - stop_all()           → Stoppt und entlädt alle Wrapper.
#
# Beispiel
# ────────
# mgr = DynamicWrapperManager()
# mgr.load("audio_wrapper")
# audio = mgr.get("audio_wrapper")
# audio.start()
# mgr.switch("audio_wrapper", "vision_wrapper")
#
# Logging
# ───────
#  - Ausgabeformat: [Zeitstempel] [Level] DynamicWrapper: Nachricht
#  - Level: INFO (Default)
#
# Abhängigkeiten
# ──────────────
#  - Python: os, sys, importlib, logging, threading
#  - Typing: Any, Dict, Optional
#
# Sicherheit / Stabilität
# ───────────────────────
#  - Fehler beim Laden/Entladen werden abgefangen und geloggt.
#  - Wrapper müssen zwingend eine Klasse `Wrapper` implementieren.
#  - Kein externer Netzwerkzugriff, läuft rein lokal.
#
# Kompatibilität
# ──────────────
#  - Getestet mit Python 3.11 auf Raspberry Pi OS (Bookworm).
#  - ORÓMA v2.11 Kernbestandteil (Wrapper-Management).
#
# Hinweise
# ────────
#  - Nur für interne Nutzung im ORÓMA-Core gedacht.
#  - Bei Migration auf spätere Versionen (≥ v3.0) ersetzt durch
#    `vision_wrapper`/`audio_wrapper` mit Backend-Routing.
# =============================================================================

import os
import sys
import importlib
import logging
import threading
from typing import Any, Dict, Optional

# Logging einrichten
logger = logging.getLogger("DynamicWrapper")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class DynamicWrapperManager:
    """Manager-Klasse für dynamisches Laden und Umschalten von Wrappern."""

    def __init__(self, base_path: str = "wrappers"):
        self.base_path = base_path
        self.loaded: Dict[str, Any] = {}
        self.lock = threading.Lock()
        logger.info("DynamicWrapperManager initialisiert (Basis: %s)", base_path)

    def available_wrappers(self) -> Dict[str, str]:
        """Liste aller Wrapper im wrappers/-Verzeichnis."""
        wrappers = {}
        wrapper_dir = os.path.join(os.path.dirname(__file__))
        for fname in os.listdir(wrapper_dir):
            if fname.endswith("_wrapper.py") and fname != "dynamic_wrapper.py":
                key = fname.replace(".py", "")
                wrappers[key] = os.path.join(wrapper_dir, fname)
        return wrappers

    def load(self, name: str) -> Optional[Any]:
        """Lädt einen Wrapper dynamisch und gibt Instanz zurück."""
        with self.lock:
            if name in self.loaded:
                logger.warning("Wrapper '%s' bereits geladen.", name)
                return self.loaded[name]

            try:
                module = importlib.import_module(f"{self.base_path}.{name}")
                if hasattr(module, "Wrapper"):
                    instance = module.Wrapper()
                    self.loaded[name] = instance
                    logger.info("Wrapper '%s' erfolgreich geladen.", name)
                    return instance
                else:
                    logger.error("Wrapper '%s' hat keine Klasse 'Wrapper'.", name)
            except Exception as e:
                logger.exception("Fehler beim Laden von Wrapper '%s': %s", name, e)
            return None

    def unload(self, name: str) -> bool:
        """Entlädt einen Wrapper."""
        with self.lock:
            if name not in self.loaded:
                logger.warning("Wrapper '%s' ist nicht geladen.", name)
                return False

            instance = self.loaded[name]
            try:
                if hasattr(instance, "stop"):
                    instance.stop()
                del self.loaded[name]
                logger.info("Wrapper '%s' entladen.", name)
                return True
            except Exception as e:
                logger.exception("Fehler beim Entladen von Wrapper '%s': %s", name, e)
                return False

    def switch(self, old_name: str, new_name: str) -> Optional[Any]:
        """Wechselt von einem Wrapper zu einem anderen."""
        logger.info("Wechsel von '%s' zu '%s' angefordert.", old_name, new_name)
        self.unload(old_name)
        return self.load(new_name)

    def get(self, name: str) -> Optional[Any]:
        """Holt Instanz eines geladenen Wrappers."""
        return self.loaded.get(name)

    def list_loaded(self) -> Dict[str, str]:
        """Liste aktuell geladener Wrapper."""
        return {k: type(v).__name__ for k, v in self.loaded.items()}

    def stop_all(self):
        """Stoppt und entlädt alle Wrapper."""
        with self.lock:
            for name in list(self.loaded.keys()):
                self.unload(name)
            logger.info("Alle Wrapper gestoppt und entladen.")


# --- Selbsttest ---
if __name__ == "__main__":
    mgr = DynamicWrapperManager()
    print("Gefundene Wrapper:", mgr.available_wrappers())

    # Test: Audio-Wrapper laden
    audio = mgr.load("audio_wrapper")
    if audio:
        print("Audio-Wrapper geladen:", audio)
        if hasattr(audio, "start"):
            audio.start()

    # Test: Umschalten auf Vision-Wrapper
    vision = mgr.switch("audio_wrapper", "vision_wrapper")
    if vision:
        print("Vision-Wrapper geladen:", vision)

    print("Geladene Wrapper:", mgr.list_loaded())
    mgr.stop_all()
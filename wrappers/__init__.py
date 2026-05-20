#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/wrappers/__init__.py
# Projekt: ORÓMA
# Version: v3.7 (final)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Zentrale Wrapper-Registry & Lazy-Loader für ORÓMA. Dieses Paket bündelt
#   alle Hardware-/Backend-Wrapper (Vision, Audio, TTS, …) und stellt eine
#   schlanke API bereit, um Module erst bei Bedarf zu importieren.
#
# Highlights v3.7
# ───────────────
#   • Klare Backend-Selektion für Vision (Hailo / DeGirum / OpenCV / GStreamer / Picamera2)
#   • Failover-Logik via ENV (zuerst explizite Wahl, sonst Präferenzkette)
#   • Zero-heavy-import: keine schweren Abhängigkeiten auf Modulebene
#   • Komfort-Getter (get_audio_wrapper(), get_tts_wrapper(), …)
#   • Convenience: build_vision_from_env() → nutzt wrappers.vision_wrapper.build_from_env
#
# ENV-Variablen (Auszug)
# ──────────────────────
#   OROMA_BACKEND_PREF=auto|hailo|degirum|cpu|vision
#       - Preference-Kette bei der Backendauswahl (nur, wenn keine explizite
#         Vision-Wahl gesetzt ist). „cpu“ entspricht dem generischen OpenCV-Backend.
#         Default: auto  → ['hailo','degirum','vision']
#
#   OROMA_FAILOVER=true|false
#       - true: Wenn ein Backend nicht verfügbar ist, wird in der Kette zum
#         nächsten gefallen. false: Keine Fallbacks (harte Auswahl).
#
#   OROMA_VISION_BACKEND= hailo|degirum|opencv|gstreamer|picamera2|cpu
#   VISION_BACKEND=        (gleiche Werte; Legacy/Kompatibilität)
#       - Explizite Vision-Backendwahl. Hat Vorrang vor OROMA_BACKEND_PREF.
#         „cpu“ ≙ „opencv“. Wenn gesetzt und nicht verfügbar:
#           • bei OROMA_FAILOVER=true → Fallback gemäß Präferenz
#           • bei OROMA_FAILOVER=false → harter Fehler
#
# Hinweise
# ────────
#   • WRAPPER_MODULES enthält nur Modulpfade als Strings; echte Imports
#     erfolgen erst in lazy_load()/get_*().
#   • is_available() prüft importlib.util.find_spec → keine Seiteneffekte.
#   • Dieses __init__ importiert bewusst nichts Schweres (cv2, sounddevice, …).
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

import os
from importlib import import_module, util
from typing import Dict, Any, Optional, List

# -----------------------------------------------------------------------------
# Registry: Wrapper-Namen → Modulpfad (keine echten Imports hier!)
# -----------------------------------------------------------------------------
WRAPPER_MODULES: Dict[str, str] = {
    # Meta
    "oroma":       "wrappers.oroma_wrapper",

    # Vision Backends
    "vision":      "wrappers.vision_wrapper",      # generisch (OpenCV/ONNX, Picamera2 via Flags)
    "hailo":       "wrappers.hailo_wrapper",       # Hailo NPU
    "degirum":     "wrappers.degirum_wrapper",     # DeGirum NPU

    # Audio / Text / TTS
    "audio":       "wrappers.audio_wrapper",
    "text":        "wrappers.text_wrapper",
    "tts":         "wrappers.tts_wrapper",

    # IO / Streams / Robotics / Sensors
    "gstreamer":   "wrappers.gstreamer_wrapper",
    "dynamic":     "wrappers.dynamic_wrapper",
    "brightness":  "wrappers.brightness_wrapper",
    "picar":       "wrappers.picar_wrapper",
}

# =============================================================================
# Verfügbarkeit & Aufzählung
# =============================================================================

def is_available(modname_or_path: str) -> bool:
    """True, wenn ein Modulpfad importierbar ist (ohne Import auszuführen)."""
    modpath = WRAPPER_MODULES.get(modname_or_path, modname_or_path)
    return util.find_spec(modpath) is not None


def list_available_wrappers() -> List[str]:
    """Liste aller Wrapper-Keys, deren Module im System gefunden werden können."""
    return [name for name, path in WRAPPER_MODULES.items() if util.find_spec(path) is not None]

# =============================================================================
# Lazy-Import & Initialisierung
# =============================================================================

def lazy_load(name_or_path: str):
    """
    Modul erst bei Bedarf importieren.
    name_or_path kann ein Wrapper-Key ('audio') oder ein Modulpfad
    ('wrappers.audio_wrapper') sein.
    """
    modpath = WRAPPER_MODULES.get(name_or_path, name_or_path)
    return import_module(modpath)


def initialize_all() -> Dict[str, str]:
    """
    Optionale Initialisierung aller verfügbaren Wrapper (falls Modul.initialize() existiert).
    Importiert bewusst nacheinander (kann „heavy“ sein) → manuell aufrufen.
    """
    result: Dict[str, str] = {}
    for key, modpath in WRAPPER_MODULES.items():
        if not is_available(modpath):
            result[key] = "missing"
            continue
        try:
            m = import_module(modpath)
            if hasattr(m, "initialize"):
                m.initialize()  # type: ignore[attr-defined]
            result[key] = "OK"
        except Exception as e:
            result[key] = f"error: {e}"
    return result

# =============================================================================
# Vision-Backend-Selektion
# =============================================================================

def _env_bool(key: str, default: bool = True) -> bool:
    v = os.environ.get(key, "")
    if v == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _explicit_vision_backend() -> Optional[str]:
    """
    Liefert die explizite ENV-Wahl, falls vorhanden:
      OROMA_VISION_BACKEND oder VISION_BACKEND
    Normalisiert 'cpu' → 'vision' (OpenCV).
    """
    raw = os.environ.get("OROMA_VISION_BACKEND") or os.environ.get("VISION_BACKEND")
    if not raw:
        return None
    val = raw.strip().lower()
    if val == "cpu":
        val = "vision"
    return val


def _env_pref_order() -> List[str]:
    """
    Leitet aus OROMA_BACKEND_PREF die Reihenfolge ab.
      auto (Default): ['hailo','degirum','vision']
      hailo          : ['hailo','degirum','vision']
      degirum        : ['degirum','hailo','vision']
      cpu|vision     : ['vision','hailo','degirum'] (mit Failover ggf. erweiterbar)
    """
    pref = (os.environ.get("OROMA_BACKEND_PREF", "auto") or "auto").strip().lower()
    if pref in ("", "auto"):
        return ["hailo", "degirum", "vision"]
    if pref in ("hailo", "degirum", "vision", "cpu"):
        if pref == "cpu":
            pref = "vision"
        chain = [pref]
        # Fallbacks anhängen, damit bei FAILOVER=True automatisch zurückgegriffen wird
        for name in ["hailo", "degirum", "vision"]:
            if name not in chain:
                chain.append(name)
        return chain
    # Unbekannter Wert → Default
    return ["hailo", "degirum", "vision"]


def pick_vision_backend(preference: Optional[List[str]] = None) -> Optional[str]:
    """
    Wählt ein Vision-Backend nach Regeln:
      1) Explizite ENV-Wahl (OROMA_VISION_BACKEND|VISION_BACKEND), falls vorhanden.
         • Ist Modul vorhanden → verwenden
         • Ist Modul NICHT vorhanden:
             - OROMA_FAILOVER=true  → zu Präferenzkette/Fallbacks wechseln
             - OROMA_FAILOVER=false → None (Signal für „hart fehlend“)
      2) Sonst Präferenzkette aus OROMA_BACKEND_PREF (oder übergebenem 'preference').
    Gibt 'hailo' | 'degirum' | 'vision' oder None zurück.
    """
    failover = _env_bool("OROMA_FAILOVER", True)

    # (1) Explizite Wahl respektieren
    explicit = _explicit_vision_backend()
    if explicit:
        use = "vision" if explicit == "cpu" else explicit
        if is_available(use):
            return use
        if not failover:
            return None  # harter Fehlerfall gewünscht

    # (2) Präferenzkette
    order = preference or _env_pref_order()
    for name in order:
        if is_available(name):
            return name
        if not failover:
            break
    return None


def get_vision_wrapper(preference: Optional[List[str]] = None):
    """
    Importiert und liefert das Vision-Wrapper-Modul entsprechend der Selektion.
    Raise RuntimeError, wenn kein geeignetes Backend gefunden wird.
    """
    name = pick_vision_backend(preference)
    if not name:
        explicit = _explicit_vision_backend()
        hint = f" (explicit={explicit})" if explicit else ""
        raise RuntimeError(f"Kein Vision-Backend verfügbar{hint}. "
                           f"Bitte Pakete installieren oder ENV anpassen.")
    return lazy_load(name)


def build_vision_from_env(name: str = "vision"):
    """
    Convenience: Erzeugt eine VisionWrapper-Instanz gemäß ENV-Parametern.
    Delegiert an wrappers.vision_wrapper.build_from_env() (falls vorhanden).
    """
    mod = lazy_load("vision")  # generisches Modul, enthält build_from_env()
    if hasattr(mod, "build_from_env"):
        return mod.build_from_env(name=name)  # type: ignore[attr-defined]
    # Fallback: direkte Klasse instanziieren (minimale Defaults)
    if hasattr(mod, "VisionWrapper"):
        return mod.VisionWrapper(name=name)  # type: ignore[attr-defined]
    raise RuntimeError("Vision-Modul ohne build_from_env/VisionWrapper – bitte prüfen.")

# =============================================================================
# Komfort-Getter für spezifische Wrapper
# =============================================================================

def get_audio_wrapper():
    if not is_available("audio"):
        raise RuntimeError("Audio-Wrapper nicht verfügbar (Abhängigkeiten fehlen?).")
    return lazy_load("audio")


def get_tts_wrapper():
    if not is_available("tts"):
        raise RuntimeError("TTS-Wrapper nicht verfügbar.")
    return lazy_load("tts")


def get_text_wrapper():
    if not is_available("text"):
        raise RuntimeError("Text-Wrapper nicht verfügbar.")
    return lazy_load("text")


def get_gstreamer_wrapper():
    if not is_available("gstreamer"):
        raise RuntimeError("GStreamer-Wrapper nicht verfügbar (Systempakete/Bindings fehlen?).")
    return lazy_load("gstreamer")


def get_dynamic_wrapper():
    if not is_available("dynamic"):
        raise RuntimeError("Dynamic-Wrapper nicht verfügbar.")
    return lazy_load("dynamic")


def get_brightness_wrapper():
    if not is_available("brightness"):
        raise RuntimeError("Brightness-Wrapper nicht verfügbar.")
    return lazy_load("brightness")


def get_picar_wrapper():
    if not is_available("picar"):
        raise RuntimeError("PiCar-Wrapper nicht verfügbar.")
    return lazy_load("picar")


__all__ = [
    # Registry & Checks
    "WRAPPER_MODULES",
    "is_available",
    "list_available_wrappers",
    "lazy_load",
    "initialize_all",
    # Vision selection
    "pick_vision_backend",
    "get_vision_wrapper",
    "build_vision_from_env",
    # Convenience getters
    "get_audio_wrapper",
    "get_tts_wrapper",
    "get_text_wrapper",
    "get_gstreamer_wrapper",
    "get_dynamic_wrapper",
    "get_brightness_wrapper",
    "get_picar_wrapper",
]

# -----------------------------------------------------------------------------
# Selbsttest (direkter Aufruf)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("🔎 ORÓMA Wrapper – Selbsttest")
    print("• Verfügbare Wrapper:", ", ".join(list_available_wrappers()) or "(keine gefunden)")
    try:
        chosen = pick_vision_backend()
        print("• Vision-Backend:", chosen or "(kein Backend)")
    except Exception as e:
        print("• Vision-Backend Fehler:", e)
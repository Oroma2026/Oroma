#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/camera_hub.py
# Projekt:   ORÓMA (Offline-First · Headless · Perzeption)
# Modul:     CameraHub – Kompatibilitäts-Bridge zu DeviceHub + Provider-Registry (Frame Injection) für ältere/heterogene Komponenten
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist eine **Kompatibilitäts-Schicht** für ältere ORÓMA-Teile, die
# historisch `core.camera_hub` importieren und dort `get_frame()` erwarten.
#
# In v3.7.3 gilt: **DeviceHub ist die „Single Source of Truth“** für Geräte.
# CameraHub macht deshalb primär zwei Dinge:
#
#   (1) Bridge zu core.device_hub.get_hub()
#       - liefert „latest frame“ aus dem zentralen Hub
#       - bietet zusätzliche Helpers (luma, status)
#
#   (2) Provider-Registry (Frame-Injection)
#       - erlaubt, externe Frame-Provider zu registrieren (z. B. virtuelle Kamera,
#         Stream-Quelle, Testgenerator, Replay-Feed)
#       - verhindert dabei Race-Conditions zwischen set_provider/clear_provider
#
# HEADLESS-INVARIANTE
# ──────────────────
# Dieses Modul darf niemals GUI/Qt/Wayland/X11 voraussetzen.
# Es delegiert an DeviceHub, der wiederum headless Capture/Libcamera/OpenCV nutzt.
#
# WARUM EIN PROVIDER-SYSTEM?
# ─────────────────────────
# ORÓMA nutzt unterschiedliche Perzeptions-Quellen:
#   - echte Kamera (DeviceHub)
#   - Replay/Simulation
#   - MJPEG/Network-Feed
#   - Tests (synthetische Frames)
#
# Damit alle alten Komponenten unverändert bleiben können, muss `get_frame()`
# trotzdem funktionieren – aber die Quelle darf dynamisch austauschbar sein.
#
# Provider-System in diesem Modul:
#   - set_provider(name, provider, replace=True)
#   - clear_provider(name)
#   - list_providers()
#   - submit_frame(frame, ts)  (für Provider/Inject-Workflows)
#
# Provider-Objekte sind bewusst generisch („Any“):
#   - wenn provider.start() existiert → wird best effort gestartet
#   - wenn provider.stop() existiert  → wird best effort gestoppt
#   - provider kann (je nach Implementierung) Frames pushen oder pullen
#
# PRODUKTIONSFIX: ATOMARE REGISTRY-UPDATES (RACE-FREE)
# ───────────────────────────────────────────────────
# In früheren Ständen konnten konkurrierende Updates passieren:
#   - set_provider() und clear_provider() haben Lookup/Update getrennt gemacht
#   - dadurch konnten falsche Provider entfernt werden („stop A, remove B“)
#
# v3.7.3 Verhalten:
#   - Registry-Updates passieren **unter EINEM Lock**
#   - Es existieren zwei Maps:
#       _PROVIDERS_BY_NAME[name] = provider
#       _PROVIDERS_BY_ID[id(provider)] = name
#   - Beim Ersetzen wird der alte Provider deterministisch identifiziert
#     und nur dieser best effort gestoppt.
#
# PROVIDER-AWARE get_frame (KEIN AUTO-START, WENN PROVIDER AKTIV)
# ──────────────────────────────────────────────────────────────
# Bridge-Funktion:
#   get_frame() / get_frame_with_ts()
#
# Delegation:
#   hub.get_latest_frame(ensure_start=<bool>)
#
# Dabei gilt:
#   - Wenn Provider aktiv ist → ensure_start=False
#     (DeviceHub soll nicht automatisch Capture starten, wenn ein Provider
#      bereits die Quelle liefert – wichtig für Replay/Tests und Debug)
#   - Wenn kein Provider aktiv ist → ensure_start=True (normales Live-Verhalten)
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# get_frame() -> Optional[object]
#   - liefert den aktuellen Frame (Format hängt vom Hub/Provider ab, typischerweise numpy ndarray BGR/RGB)
#
# get_frame_with_ts(ensure_start: bool=True) -> (frame, ts)
#   - liefert zusätzlich den Timestamp (float seconds)
#
# get_luma() -> Optional[float]
#   - liefert Helligkeit/Luma (wenn Hub das kann); ansonsten None
#
# submit_frame(frame, ts: float|None=None) -> bool
#   - erlaubt Provider/Tests, Frames an den Hub (oder aktiven Provider-Pfad) zu melden
#
# set_provider(name: str, provider: Any, replace: bool=True) -> bool
# clear_provider(name: str) -> bool
# list_providers() -> List[str]
#
# status() -> Optional[dict]
#   - liefert DeviceHub.status() + zusätzlich "camera_hub_providers"
#
# FEHLER- UND STABILITÄTSPRINZIP
# ──────────────────────────────
# - Alle Calls sind „best effort“: Exceptions werden geloggt und führen zu None/False,
#   aber das System darf nicht crashen.
# - Keine langen Wartezeiten/Locks: Provider-Lock schützt nur Registry, nicht IO.
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Provider-Registry muss atomar bleiben (Lock + ID-Map).
# - get_frame darf DeviceHub nicht auto-starten, wenn Provider aktiv ist.
# - Kein Entfernen/„Cleanup“ von DeviceHub hier: CameraHub ist Bridge, nicht Owner.
# - Headless bleibt Pflicht.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Any, Dict

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore

LOG = logging.getLogger("oroma.camera_hub")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [camera_hub] %(message)s"))
    LOG.addHandler(_h)
LOG.setLevel(logging.INFO)


# =============================================================================
# BLOCK: HUB_IMPORT (SWAPPABLE)
# =============================================================================
try:
    from core.device_hub import get_hub  # type: ignore
except Exception:  # pragma: no cover
    get_hub = None  # type: ignore
# =============================================================================
# END BLOCK: HUB_IMPORT
# =============================================================================


# =============================================================================
# BLOCK: HUB_SINGLETON_CACHE (SWAPPABLE)
# =============================================================================
_DEVICE_HUB = None
_DEVICE_HUB_LOGGED = False
_CAMERA_HUB_STATE_DIR = Path(os.environ.get("OROMA_STATE_DIR", "/opt/ai/oroma/data/state"))
_CAMERA_HUB_BRIDGE_LOG_PATH = _CAMERA_HUB_STATE_DIR / "camera_hub_bridge_log.json"
_CAMERA_HUB_BRIDGE_LOG_REPEAT_SEC = max(0, int(os.environ.get("OROMA_CAMERA_HUB_BRIDGE_LOG_REPEAT_SEC", "60") or "60"))
_CAMERA_HUB_GLOBAL_FRAME_META_PATH = _CAMERA_HUB_STATE_DIR / "latest_frame_cache.json"
_CAMERA_HUB_GLOBAL_FRAME_JPG_PATH = _CAMERA_HUB_STATE_DIR / "latest_frame_cache.jpg"
_CAMERA_HUB_GLOBAL_FRAME_MAX_AGE_SEC = max(0.0, float(os.environ.get("OROMA_GLOBAL_FRAME_CACHE_MAX_AGE_SEC", "5.0") or "5.0"))
# =============================================================================
# END BLOCK: HUB_SINGLETON_CACHE
# =============================================================================


def _should_log_bridge_activation() -> bool:
    """Rate-limit the DeviceHub bridge activation log across processes.

    Why
    ---
    `core.ptz_attention_loop --once` runs as many short-lived processes. A pure
    in-process `log once` flag is therefore not enough because every new process
    would log `DeviceHub-Bridge aktiv.` again.

    This helper stores the last activation-log timestamp in the shared state
    directory and only allows a new log after a configurable repeat interval.
    Failures must be non-fatal and default to permissive logging.
    """
    global _DEVICE_HUB_LOGGED
    if _DEVICE_HUB_LOGGED:
        return False
    try:
        now = time.time()
        repeat_sec = int(_CAMERA_HUB_BRIDGE_LOG_REPEAT_SEC)
        if repeat_sec <= 0:
            _DEVICE_HUB_LOGGED = True
            return True
        _CAMERA_HUB_STATE_DIR.mkdir(parents=True, exist_ok=True)
        last_ts = 0.0
        if _CAMERA_HUB_BRIDGE_LOG_PATH.exists():
            try:
                data = json.loads(_CAMERA_HUB_BRIDGE_LOG_PATH.read_text(encoding="utf-8") or "{}")
                last_ts = float(data.get("last_ts") or 0.0)
            except Exception:
                last_ts = 0.0
        if (now - last_ts) < float(repeat_sec):
            _DEVICE_HUB_LOGGED = True
            return False
        tmp = _CAMERA_HUB_BRIDGE_LOG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_ts": now}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CAMERA_HUB_BRIDGE_LOG_PATH)
        _DEVICE_HUB_LOGGED = True
        return True
    except Exception:
        _DEVICE_HUB_LOGGED = True
        return True


def _hub():
    global _DEVICE_HUB, _DEVICE_HUB_LOGGED, get_hub
    if _DEVICE_HUB is not None:
        return _DEVICE_HUB
    if get_hub is None:
        LOG.debug("get_hub() nicht verfügbar – kein DeviceHub.")
        return None
    try:
        _DEVICE_HUB = get_hub()
        if _should_log_bridge_activation():
            LOG.info("DeviceHub-Bridge aktiv.")
        return _DEVICE_HUB
    except Exception as e:
        LOG.warning("get_hub() fehlgeschlagen: %s", e)
        return None


# =============================================================================
# BLOCK: PROVIDER_REGISTRY (SWAPPABLE)
# =============================================================================
_PROVIDER_LOCK = threading.Lock()
_PROVIDERS_BY_NAME: Dict[str, Any] = {}
_PROVIDERS_BY_ID: Dict[int, str] = {}


def _providers_active() -> bool:
    """
    True, wenn mindestens ein externer Provider registriert ist.
    """
    with _PROVIDER_LOCK:
        return bool(_PROVIDERS_BY_NAME)


def _preferred_ensure_start(default: bool) -> bool:
    """
    Wenn Provider aktiv sind, darf NICHT automatisch die interne Hub-Kamera starten.
    """
    return default if not _providers_active() else False
# =============================================================================
# END BLOCK: PROVIDER_REGISTRY
# =============================================================================


# =============================================================================
# BLOCK: GET_FRAME_COMPAT (SWAPPABLE)
# =============================================================================
def get_frame() -> Optional["object"]:
    hub = _hub()
    if not hub:
        return None
    try:
        ensure = _preferred_ensure_start(True)
        frame, _ts = hub.get_latest_frame(ensure_start=ensure)
        return frame
    except Exception as e:
        LOG.warning("get_frame() Fehler: %s", e)
        return None


def get_frame_with_ts(ensure_start: bool = True) -> Tuple[Optional["object"], float]:
    hub = _hub()
    if not hub:
        return (None, 0.0)
    try:
        ensure = _preferred_ensure_start(bool(ensure_start))
        frame, ts = hub.get_latest_frame(ensure_start=ensure)
        return (frame, float(ts or 0.0))
    except Exception as e:
        LOG.warning("get_frame_with_ts() Fehler: %s", e)
        return (None, 0.0)
# =============================================================================
# END BLOCK: GET_FRAME_COMPAT
# =============================================================================


def get_global_cached_frame_with_ts() -> Tuple[Optional["object"], float]:
    """Liest den prozessuebergreifenden Latest-Frame-Cache aus dem State-Bereich.

    Dieser Pfad ist bewusst komplett passiv: kein Hub-Resolve, kein Kamera-Start,
    kein Warten. Er dient als globale Fallback-Quelle fuer kurzlebige Prozesse.
    """
    try:
        if cv2 is None or np is None:
            return (None, 0.0)
        if not _CAMERA_HUB_GLOBAL_FRAME_META_PATH.exists() or not _CAMERA_HUB_GLOBAL_FRAME_JPG_PATH.exists():
            return (None, 0.0)
        data = json.loads(_CAMERA_HUB_GLOBAL_FRAME_META_PATH.read_text(encoding='utf-8') or '{}')
        ts = float(data.get('ts') or 0.0)
        if ts <= 0.0:
            return (None, 0.0)
        max_age = float(_CAMERA_HUB_GLOBAL_FRAME_MAX_AGE_SEC)
        if max_age > 0.0 and (time.time() - ts) > max_age:
            return (None, ts)
        raw = _CAMERA_HUB_GLOBAL_FRAME_JPG_PATH.read_bytes()
        if not raw:
            return (None, ts)
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return (None, ts)
        return (frame, ts)
    except Exception as e:
        LOG.warning('get_global_cached_frame_with_ts() Fehler: %s', e)
        return (None, 0.0)


def get_cached_frame_with_ts() -> Tuple[Optional["object"], float]:
    """Liefert das letzte im Hub gecachte Frame strikt nicht-blockierend.

    WICHTIG
    -------
    Dieser Helfer darf keinerlei Kamera- oder Provider-Start triggern. Er ist
    ausschliesslich fuer Fast-Path-Consumer gedacht, die lieber sofort mit
    'no frame' / 'stale frame' zurueckkehren als auf neue Daten zu warten.
    """
    hub = _hub()
    if not hub:
        return (None, 0.0)
    try:
        getter = getattr(hub, "get_latest_cached_frame", None)
        if callable(getter):
            frame, ts = getter()
            return (frame, float(ts or 0.0))
        # Fallback fuer aeltere Hubs: niemals ensure_start erzwingen.
        frame, ts = hub.get_latest_frame(ensure_start=False)
        return (frame, float(ts or 0.0))
    except Exception as e:
        LOG.warning("get_cached_frame_with_ts() Fehler: %s", e)
        return (None, 0.0)


def get_cached_frame_with_ts_fast() -> Tuple[Optional["object"], float]:
    """Liefert ein gecachtes Frame strikt passiv und ohne Hub-Resolve.

    Reihenfolge
    ----------
    1) Wenn im aktuellen Prozess bereits ein DeviceHub existiert, dessen
       `get_latest_cached_frame()` nutzen.
    2) Sonst auf den prozessuebergreifenden globalen Frame-Cache zurueckfallen.

    WICHTIG
    -------
    Kein `_hub()` und kein `get_hub()` in diesem Pfad. Bei fehlenden Daten
    lieber sofort `(None, 0.0)` als einen teuren Hub-/Bridge-Aufbau ausloesen.
    """
    frame, ts, _src, _meta = get_cached_frame_with_ts_fast_diag()
    return (frame, ts)


def get_cached_frame_with_ts_fast_diag() -> Tuple[Optional["object"], float, str, Dict[str, Any]]:
    """Wie get_cached_frame_with_ts_fast(), aber mit Diagnosemetadaten.

    Rueckgabe
    ---------
    (frame, ts, source, meta)

    source:
      - "local_hub_cache"
      - "global_frame_cache"
      - "none"
    """
    global _DEVICE_HUB
    hub = _DEVICE_HUB
    meta: Dict[str, Any] = {
        'local_hub_available': bool(hub is not None),
        'local_frame_available': False,
        'global_meta_exists': bool(_CAMERA_HUB_GLOBAL_FRAME_META_PATH.exists()),
        'global_jpg_exists': bool(_CAMERA_HUB_GLOBAL_FRAME_JPG_PATH.exists()),
        'global_cache_age_sec': None,
        'fallback_used': False,
    }
    try:
        if hub is not None:
            getter = getattr(hub, 'get_latest_cached_frame', None)
            if callable(getter):
                frame, ts = getter()
                if frame is not None and float(ts or 0.0) > 0.0:
                    meta['local_frame_available'] = True
                    return (frame, float(ts or 0.0), 'local_hub_cache', meta)
        meta['fallback_used'] = True
        frame, ts = get_global_cached_frame_with_ts()
        if float(ts or 0.0) > 0.0:
            try:
                meta['global_cache_age_sec'] = round(max(0.0, time.time() - float(ts or 0.0)), 3)
            except Exception:
                meta['global_cache_age_sec'] = None
        if frame is not None and float(ts or 0.0) > 0.0:
            return (frame, float(ts or 0.0), 'global_frame_cache', meta)
        return (None, float(ts or 0.0), 'none', meta)
    except Exception as e:
        LOG.warning("get_cached_frame_with_ts_fast_diag() Fehler: %s", e)
        meta['error'] = str(e)
        return (None, 0.0, 'none', meta)



# =============================================================================
# BLOCK: GET_LUMA_COMPAT (SWAPPABLE)
# =============================================================================
def get_luma() -> Optional[float]:
    hub = _hub()
    if not hub:
        return None
    try:
        return hub.get_light_level()
    except Exception as e:
        LOG.warning("get_luma() Fehler: %s", e)
        return None
# =============================================================================
# END BLOCK: GET_LUMA_COMPAT
# =============================================================================


# =============================================================================
# BLOCK: SUBMIT_FRAME_COMPAT (SWAPPABLE)
# =============================================================================
def submit_frame(frame: "object", source: str = "external") -> bool:
    hub = _hub()
    if not hub:
        return False
    try:
        fn = getattr(hub, "submit_frame", None)
        if fn is None:
            return False
        fn(frame, source=source)
        return True
    except Exception as e:
        LOG.warning("submit_frame() Fehler: %s", e)
        return False
# =============================================================================
# END BLOCK: SUBMIT_FRAME_COMPAT
# =============================================================================


# =============================================================================
# BLOCK: PROVIDER_API (SWAPPABLE)
# =============================================================================
def set_provider(name: str, provider: Any, replace: bool = True) -> bool:
    """
    Registriert einen Frame-Provider unter `name`.

    -----------------------------------------------------------------------------
    PRODUKTIONSFIX – ATOMARES PROVIDER-REGISTRY (Race-free)
    -----------------------------------------------------------------------------
    Hintergrund:
      In früheren Builds wurden Lookup (unter Lock) und Remove/Update (später)
      getrennt durchgeführt. Das ermöglichte Race-Conditions wie:
        • clear_provider() stoppt Provider A, entfernt aber Provider B aus dem Dict
        • set_provider() überschreibt Registry nach einem konkurrierenden Update

    Lösung (dieser Block):
      1) Provider wird *vorher* best-effort gestartet (falls start() existiert).
         → Registry zeigt i. d. R. nur laufende Provider.
      2) Swap/Remove/ID-Index erfolgen *atomar* unter _PROVIDER_LOCK.
      3) Stop des alten Providers passiert *außerhalb* des Locks (keine Blockade).

    Hinweis:
      - Provider-Objekte werden über ihre Objekt-ID (id(provider)) indiziert.
      - Ein Provider soll typischerweise genau unter einem Namen registriert sein.
    -----------------------------------------------------------------------------
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("set_provider(): name ist leer")
    if provider is None:
        raise ValueError("set_provider(): provider ist None")

    # 1) Provider vorab starten (best-effort), damit Registry nicht auf "tote" Provider zeigt
    try:
        start_fn = getattr(provider, "start", None)
        if callable(start_fn):
            start_fn()
    except Exception as e:
        LOG.warning("set_provider(%s): provider.start() fehlgeschlagen: %s", name, e)
        return False

    old_to_stop: Any = None

    # 2) Atomarer Swap (Name-Registry + ID-Registry)
    with _PROVIDER_LOCK:
        # Falls provider bereits unter einem anderen Namen registriert ist → alten Alias entfernen
        prev_name = _PROVIDERS_BY_ID.get(id(provider))
        if prev_name and prev_name != name:
            _PROVIDERS_BY_NAME.pop(prev_name, None)

        old = _PROVIDERS_BY_NAME.get(name)
        if old is not None and old is not provider and not replace:
            return False

        if old is not None and old is not provider:
            old_to_stop = old
            # ID-Index nur entfernen, wenn er wirklich auf diesen Namen zeigt
            if _PROVIDERS_BY_ID.get(id(old)) == name:
                _PROVIDERS_BY_ID.pop(id(old), None)

        _PROVIDERS_BY_NAME[name] = provider
        _PROVIDERS_BY_ID[id(provider)] = name

    # 3) alten Provider außerhalb des Locks stoppen
    if old_to_stop is not None:
        _stop_provider(old_to_stop, name)

    return True

def clear_provider(name: str) -> bool:
    """
    Entfernt einen Provider aus der Registry.

    PRODUKTIONSFIX:
      - Entfernen + ID-Index-Update *atomar* unter _PROVIDER_LOCK
      - Stop außerhalb des Locks
    """
    name = (name or "").strip()
    if not name:
        return False

    old: Any = None
    with _PROVIDER_LOCK:
        old = _PROVIDERS_BY_NAME.pop(name, None)
        if old is not None:
            if _PROVIDERS_BY_ID.get(id(old)) == name:
                _PROVIDERS_BY_ID.pop(id(old), None)

    if old is None:
        return False

    _stop_provider(old, name)
    return True
def list_providers() -> Dict[str, str]:
    with _PROVIDER_LOCK:
        return {nm: type(p).__name__ for nm, p in _PROVIDERS_BY_NAME.items()}


def _stop_provider(provider: Any, name: str) -> None:
    try:
        stop_fn = getattr(provider, "stop", None)
        if callable(stop_fn):
            stop_fn()
        else:
            LOG.debug("_stop_provider(%s): provider hat keine stop()-Methode.", name)
    except Exception as e:
        LOG.debug("_stop_provider(%s): stop() Fehler: %s", name, e)
# =============================================================================
# END BLOCK: PROVIDER_API
# =============================================================================


def status() -> Optional[dict]:
    hub = _hub()
    if not hub:
        return None
    try:
        fn = getattr(hub, "status", None)
        if fn is None:
            return None
        st = dict(fn() or {})
        st["camera_hub_providers"] = list_providers()
        return st
    except Exception as e:
        LOG.warning("status() Fehler: %s", e)
        return None


if __name__ == "__main__":
    LOG.info("Providers: %s", list_providers())
    LOG.info("Status: %s", status())
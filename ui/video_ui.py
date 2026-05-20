#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/video_ui.py
# Projekt:   ORÓMA (Flask UI · Headless Video)
# Modul:     Video UI – MJPEG Stream + Snapshot + Health (DeviceHub first, VisionWrapper fallback)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses UI-Modul stellt einen headless MJPEG-Livestream bereit und dient als
# visuelle Diagnose-/Monitoring-Schicht für ORÓMA:
#   - Live-MJPEG Stream (Browser-kompatibel)
#   - Snapshot als JPEG
#   - Health-Endpunkt (für LoadBalancer/Watchdogs)
#   - Anti-Cache Header, damit Safari/iOS nicht „alte Frames“ cached
#
# BLUEPRINT
# ─────────
#   Blueprint Name: "video"
#   url_prefix: /video
#
# ROUTES (AKTUELLER VERTRAG)
# ─────────────────────────
# Streams:
#   GET /video/stream          -> MJPEG Stream (primär Push, sonst Poll-Fallback)
#   GET /video/mjpeg           -> Alias von /stream (Kompatibilität)
#   GET /video/mjpg            -> Alias von /stream (Kompatibilität)
#   GET /video/stream_poll     -> explizit Poll-Stream (erzwingt Poll-Strategie)
#
# Snapshots:
#   GET /video/snapshot.jpg    -> aktueller Frame als JPEG
#
# UI:
#   GET /video/                -> HTML Seite (templates/video.html)
#
# Health:
#   GET /video/healthz         -> {ok:true, source:"hub|vw|none", age_sec,...}
#
# QUELLSTRATEGIE (DEVICEHUB FIRST)
# ───────────────────────────────
# Das Modul versucht Frames in stabiler Reihenfolge zu liefern:
#   1) DeviceHub Push:
#      - nutzt hub.mjpeg_generator() (streamt direkt aus Hub, effizient)
#
#   2) DeviceHub Poll:
#      - nutzt hub.get_latest_jpeg() in einem Poll-Loop
#      - dient als Fallback, wenn Push nicht verfügbar ist
#
#   3) VisionWrapper Poll (Fallback2):
#      - erstellt ggf. VisionWrapper via wrappers.vision_wrapper.build_from_env(...)
#      - startet Wrapper nur bei Bedarf (lazy start)
#      - encodiert Frames zu JPEG und streamt sie
#
# Ziel:
# - ORÓMA soll einen Stream liefern, selbst wenn ein Backend temporär ausfällt.
# - Der Stream darf den Prozess nicht crashen (best effort + sauberes Logging).
#
# FRAME-FRISCHE / STALE-PROTECTION
# ────────────────────────────────
# Ein zentraler Schutz ist „Frame Freshness“:
#   - Frames, die älter als OROMA_VIDEO_FRAME_FRESH_SEC sind, gelten als stale.
#   - In diesem Fall wird zur nächsten Quelle/Fallback gewechselt oder ein
#     „No fresh frame“ geliefert (je nach Route).
#
# BOUNDARY / BROWSER-KOMPATIBILITÄT
# ─────────────────────────────────
# MJPEG benötigt eine Boundary-Zeichenkette im multipart/x-mixed-replace Stream.
# Dieses Modul nutzt:
#   - Default: "frame"
#   - konfigurierbar über ENV OROMA_MJPEG_BOUNDARY
#
# Zusätzlich setzt es Anti-Cache Header:
#   Cache-Control: no-store, no-cache, must-revalidate, max-age=0
#   Pragma: no-cache
# um Safari/iPhone „stuck frames“ zu vermeiden.
#
# FORCE-POLL MODUS (OPS)
# ──────────────────────
# Manchmal ist Push-Streaming problematisch (Proxy/Buffering).
# Dann kann Poll erzwungen werden:
#   OROMA_VIDEO_FORCE_POLL=1
# oder man nutzt direkt:
#   /video/stream_poll
#
# WICHTIGE ENV-VARIABLEN (AKTUELL GENUTZT)
# ───────────────────────────────────────
# Video-UI:
#   OROMA_VIDEO_FRAME_FRESH_SEC   (Default: 10)   # wie alt Frames max. sein dürfen
#   OROMA_VIDEO_REFRESH_MS        (Default: 250)  # Poll-Schlafzeit/Intervall (ms)
#   OROMA_VIDEO_FORCE_POLL        (Default: 0)    # erzwingt Poll statt Push
#   OROMA_MJPEG_BOUNDARY          (Default: "frame")
#
# Vision Backend Hint (für Logging + Wrapper-Fallback):
#   OROMA_VISION_BACKEND          (Fallback: VISION_BACKEND, Default: "opencv")
#   OROMA_VISION_FPS              (optional; nur als Hint/Log)
#
# Logging Path (für fallback logs/guarding):
#   OROMA_LOG_DIR                 (optional; sonst system default)
#
# THREADING / SINGLETONS
# ──────────────────────
# Dieses Modul hält Singletons, damit nicht pro Request neue Captures entstehen:
#   - _HUB: DeviceHub Instanz (lazy)
#   - _VW:  VisionWrapper Instanz (lazy, nur wenn nötig)
#   - _LOCK: verhindert Race Conditions beim Lazy-Init
#
# HEALTHZ DETAILS
# ───────────────
# /video/healthz liefert:
#   - ok: bool
#   - source: "hub" | "vw" | "none"
#   - age_sec: float (Alter des letzten Frames)
# Damit können externe Watchdogs/Reverse-Proxies den Zustand prüfen.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os, time, atexit, threading, sqlite3
import subprocess
import math
from typing import Optional, Generator, Dict, Any, List
from flask import Blueprint, Response, render_template, jsonify, current_app, request, url_for
import logging
from core.log_guard import log_suppressed
import json

try:
    from core.ptz_motor_state import (
        json_safe_obj as _ptz_core_json_safe_obj,
        read_ptz_motor_state as _ptz_core_read_state,
        read_ptz_motor_status as _ptz_core_read_status,
        run_systemctl_for_motor as _ptz_core_run_systemctl,
        safe_tail_lines as _ptz_core_safe_tail_lines,
    )
except Exception:  # pragma: no cover - UI must stay importable on partial deployments
    _ptz_core_json_safe_obj = None  # type: ignore
    _ptz_core_read_state = None  # type: ignore
    _ptz_core_read_status = None  # type: ignore
    _ptz_core_run_systemctl = None  # type: ignore
    _ptz_core_safe_tail_lines = None  # type: ignore

# Optional für VisionWrapper-Fallback und JPEG-Encode
try:
    import cv2  # type: ignore
except Exception:
    cv2 = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore

# Reward Logging (best-effort, darf UI nie crashen)
try:
    from core.reward import RewardLogger  # type: ignore
except Exception:
    RewardLogger = None  # type: ignore

video_bp = Blueprint("video", __name__, url_prefix="/video")

# --- Boundary & Stream-Settings (ENV) ----------------------------------------
BOUNDARY_STR = os.environ.get("OROMA_MJPEG_BOUNDARY", "frame")
BOUNDARY = BOUNDARY_STR.encode("ascii", "ignore")

_FORCE_POLL = os.environ.get("OROMA_VIDEO_FORCE_POLL", "0").strip().lower() in ("1","true","yes","on")
_FPS = int(os.environ.get("OROMA_VISION_FPS", "10"))

# ----------------------------------------------------------------------------
# PRODUKTIONSFIX – Status/Health im External-Frame Betrieb
# ----------------------------------------------------------------------------
# Hintergrund:
#   Wenn Frames über camera_hub/Provider extern in den DeviceHub gepusht werden,
#   läuft der interne Capture-Thread oft nicht (cam.running=False). Trotzdem ist
#   der Stream online, sobald ein frisches Frame existiert.
#
# Lösung:
#   "alive"/"ok" wird als TRUE bewertet, wenn entweder cam.running=True ODER
#   last_frame_age <= OROMA_VIDEO_FRAME_FRESH_SEC ist.
#
# ENV:
#   OROMA_VIDEO_FRAME_FRESH_SEC (Default: 10)
# ----------------------------------------------------------------------------

try:
    _FRESH_SEC = float(os.environ.get("OROMA_VIDEO_FRAME_FRESH_SEC", "10"))
except Exception:
    _FRESH_SEC = 10.0

# ----------------------------------------------------------------------------
# Edge-Overlay / Kanten-Debug (sichtbare Visualisierung im Video-Tab)
# ----------------------------------------------------------------------------
# Ziel:
#   ORÓMA berechnet bereits einfache Kanten-/Gradienten-Merkmale. Für die UI
#   möchten wir optional eine direkt sichtbare Kantenansicht anbieten, damit
#   im Browser nachvollziehbar ist, was die Kamera in Struktur/Konturen sieht.
#
# Design:
#   - default AUS auf der Route selbst; Aktivierung via Query-Parameter
#       ?edges=1
#   - arbeitet best effort auf einem bereits vorhandenen Frame
#   - nutzt OpenCV Canny + leichte Dilatation + grüne Overlay-Maske
#   - greift NICHT in den normalen Kamerapfad ein, wenn edges=0 bleibt
#
# ENV:
#   OROMA_VIDEO_EDGE_CANNY_LOW     Default 80
#   OROMA_VIDEO_EDGE_CANNY_HIGH    Default 160
#   OROMA_VIDEO_EDGE_DILATE        Default 1
#   OROMA_VIDEO_EDGE_ALPHA         Default 0.85
# ----------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except Exception:
        return int(default)


def _env_float_local(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip() or default)
    except Exception:
        return float(default)


_EDGE_CANNY_LOW = _env_int('OROMA_VIDEO_EDGE_CANNY_LOW', 80)
_EDGE_CANNY_HIGH = _env_int('OROMA_VIDEO_EDGE_CANNY_HIGH', 160)
_EDGE_DILATE = max(0, _env_int('OROMA_VIDEO_EDGE_DILATE', 1))
_EDGE_ALPHA = max(0.0, min(1.0, _env_float_local('OROMA_VIDEO_EDGE_ALPHA', 0.85)))

# --- Singletons ---------------------------------------------------------------
_HUB = None
_VW = None
_LOCK = threading.Lock()

# Reward logger singleton (lazy)
_RLOG = None


# -----------------------------------------------------------------------------
# USB/KERNEL ALERT INTEGRATION (UI)
# -----------------------------------------------------------------------------
# Motivation
# ----------
# In der Praxis sind "Video hängt / PTZ nicht steuerbar" sehr häufig kein ORÓMA-
# Fehler, sondern ein USB-Problem (Over-Current, Disconnect, UVC URB -19 etc.).
# Diese Kernel-Events sind mit `dmesg` sichtbar, aber im Alltag unbequem.
#
# Lösung
# ------
# ORÓMA kann (optional) per tools/usb_kernel_watch.py kritische Kernel-Zeilen
# herausfiltern und in:
#   - /opt/ai/oroma/logs/usb_kernel_watch.log
#   - /opt/ai/oroma/data/state/usb_kernel_watch.json
# spiegeln. Dieses UI-Modul liest best-effort diese State-Datei und zeigt oben
# im Video-Tab einen Banner (Warnung), wenn kürzlich ein Alarm aufgetreten ist.
#
# Wichtig:
# - UI darf niemals crashen, wenn die Datei fehlt/kaputt ist.
# - Keine "stillen" Fehler: Parsing-Probleme werden über log_suppressed
#   protokolliert (WARN), aber die UI bleibt verfügbar.
#
# ENV (optional)
# -------------
#   OROMA_STATE_DIR   Default: /opt/ai/oroma/data/state
#   OROMA_USB_ALERT_MAX_AGE_SEC Default: 43200 (12h)
# -----------------------------------------------------------------------------

_STATE_DIR = os.environ.get("OROMA_STATE_DIR", "/opt/ai/oroma/data/state")
_USB_STATE_PATH = os.path.join(_STATE_DIR, "usb_kernel_watch.json")

try:
    _USB_ALERT_MAX_AGE_SEC = int(os.environ.get("OROMA_USB_ALERT_MAX_AGE_SEC", "43200"))
except Exception:
    _USB_ALERT_MAX_AGE_SEC = 43200


def _read_usb_kernel_alert_state() -> Dict[str, Any]:
    """Best-effort: liest den letzten USB/KERNEL Alarm aus der Watch-State-Datei.

    Erwartetes Format (best effort, tolerant):
      {
        "last_alert_ts": 123,
        "last_alert_line": "...",
        "last_alert_kind": "over-current|disconnect|uvc|...",
        "boot_id": "...",
        ...
      }

    Rückgabe ist immer ein Dict mit stabilen Keys, UI-kompatibel.
    """
    now = int(time.time())
    out: Dict[str, Any] = {
        "ok": True,
        "path": _USB_STATE_PATH,
        "exists": False,
        "max_age_sec": _USB_ALERT_MAX_AGE_SEC,
        "last_scan_ts": 0,
        "last_scan_age": None,
        "last_scan_rc": None,
        "last_scan_err": None,
        "last_alert_ts": 0,
        "last_alert_age": None,
        "last_alert_kind": None,
        "last_alert_line": None,
        "show": False,
    }
    try:
        if not os.path.exists(_USB_STATE_PATH):
            return out
        out["exists"] = True
        with open(_USB_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Scan-Status (Watchdog Heartbeat)
        st = data.get("last_scan_ts")
        if isinstance(st, (int, float)):
            out["last_scan_ts"] = int(st)
            out["last_scan_age"] = float(max(0, now - int(st)))
        out["last_scan_rc"] = data.get("last_scan_rc")
        out["last_scan_err"] = data.get("last_scan_err")

        ts = data.get("last_alert_ts")
        if isinstance(ts, (int, float)):
            out["last_alert_ts"] = int(ts)
            out["last_alert_age"] = float(max(0, now - int(ts)))
        out["last_alert_kind"] = data.get("last_alert_kind")
        out["last_alert_line"] = data.get("last_alert_line")

        # Banner:
        #   A) wenn ein echter USB/KERNEL-Alarm existiert und nicht zu alt ist.
        #   B) ODER wenn der Watcher selbst gerade fehlschlägt (z.B. journalctl
        #      Permission-Problem) – damit das NICHT still passiert.
        show_alert = False
        if out["last_alert_ts"] and out["last_alert_age"] is not None:
            show_alert = bool(out["last_alert_age"] <= float(_USB_ALERT_MAX_AGE_SEC))

        show_scan_fail = False
        try:
            rc = out.get("last_scan_rc")
            age = out.get("last_scan_age")
            if rc is not None and int(rc) != 0 and age is not None:
                # nur wenn es "aktuell" ist (sonst nervt es dauerhaft)
                show_scan_fail = bool(float(age) <= 600.0)
        except Exception:
            show_scan_fail = False

        out["show"] = bool(show_alert or show_scan_fail)
        return out
    except Exception as e:
        # Nicht crashen – aber sichtbar machen.
        out["ok"] = False
        log_suppressed('ui/video_ui.py:usb_alert_state', exc=e, level=logging.WARNING)
        return out


def _get_reward_logger():
    """Lazy create RewardLogger.

    Wichtig: Best-effort. Bei fehlender DB oder Importfehlern wird None
    geliefert. Der Aufrufer muss damit umgehen.
    """
    global _RLOG
    if _RLOG is not None:
        return _RLOG
    if RewardLogger is None:
        _RLOG = None
        return None
    try:
        _RLOG = RewardLogger()  # type: ignore
    except Exception:
        _RLOG = None
    return _RLOG

# ----------------------------------------------------------------------------
# V4L2 DEVICE LISTING (UI helper)
# ----------------------------------------------------------------------------
# Zweck:
#   Nach Reboots sind /dev/videoX-Nummern nicht stabil. Für USB-Kameras existieren
#   stabile Symlinks unter /dev/v4l/by-id/ (und alternativ /dev/v4l/by-path/).
#   Diese UI-Hilfe stellt eine JSON-API bereit, damit die Video-Seite die
#   verfügbaren Geräte inkl. stabiler Pfade anzeigen kann.
#
# Route:
#   GET /video/api/devices
#
# Sicherheit/Performance:
#   - Best effort, timeouts, kein Crash
#   - v4l2-ctl Aufrufe sind kurz und werden nur für by-id Einträge getestet
#     (typisch wenige Geräte)
# ----------------------------------------------------------------------------

def _read_symlinks(dir_path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        if not os.path.isdir(dir_path):
            return out
        for name in sorted(os.listdir(dir_path)):
            p = os.path.join(dir_path, name)
            if not os.path.islink(p):
                continue
            try:
                target = os.readlink(p)
                real = os.path.realpath(p)
            except Exception:
                target, real = "", ""
            out.append({
                "name": name,
                "path": p,
                "target": target,
                "real": real,
            })
    except Exception:
        return out
    return out


def _run_cmd(cmd: List[str], timeout_sec: float = 2.0) -> Dict[str, Any]:
    """Best-effort subprocess runner (no raise)."""
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return {
            "ok": p.returncode == 0,
            "rc": p.returncode,
            "out": (p.stdout or "").strip(),
            "err": (p.stderr or "").strip(),
        }
    except Exception as e:
        return {"ok": False, "rc": -1, "out": "", "err": str(e)}


def _ptz_quickcheck(dev_path: str) -> Dict[str, Any]:
    """Prüft PTZ Controls über -C pan/tilt/zoom (robust, auch wenn list-ctrls leer ist)."""
    r = _run_cmd(["v4l2-ctl", "-d", dev_path, "-C", "pan_absolute", "-C", "tilt_absolute", "-C", "zoom_absolute"], timeout_sec=1.5)
    if not r.get("ok"):
        return {"supported": False, "detail": r.get("err") or r.get("out") or ""}
    # Beispielausgabe: "pan_absolute: 0\n tilt_absolute: 0\n zoom_absolute: 100"
    vals: Dict[str, Any] = {}
    for line in (r.get("out") or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            vals[k.strip()] = v.strip()
    return {"supported": True, "values": vals}


# -----------------------------------------------------------------------------
# V4L2 / Device Discovery (UI Diagnostics)
# -----------------------------------------------------------------------------
# Motivation:
#   /dev/videoX Nummern sind NICHT stabil über Reboots (Enumeration-Reihenfolge).
#   Für USB-Kameras liefert udev stabile Symlinks unter:
#     - /dev/v4l/by-id/   (Hersteller/Seriennummer)
#     - /dev/v4l/by-path/ (Topologie-Pfad)
#
# Ziel:
#   Die Video-UI soll die relevanten V4L2-Devices transparent anzeigen, damit
#   die Konfiguration für Kamera/PTZ reboot-fest erfolgen kann.
#
# Routes:
#   GET /video/api/devices -> JSON mit by-id/by-path + (best effort) "v4l2-ctl --list-devices"
# -----------------------------------------------------------------------------

def _safe_run(cmd: List[str], timeout_sec: float = 2.0) -> Dict[str, Any]:
    """subprocess runner mit harten Timeouts (headless, fail-closed)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        return {
            "ok": p.returncode == 0,
            "rc": int(p.returncode),
            "out": (p.stdout or ""),
            "err": (p.stderr or ""),
            "cmd": cmd,
        }
    except Exception as e:
        return {"ok": False, "rc": -1, "out": "", "err": str(e), "cmd": cmd}


def _list_v4l_symlinks(dir_path: str) -> List[Dict[str, str]]:
    """Listet Symlinks in /dev/v4l/by-id und /dev/v4l/by-path."""
    items: List[Dict[str, str]] = []
    try:
        if not os.path.isdir(dir_path):
            return items
        for name in sorted(os.listdir(dir_path)):
            p = os.path.join(dir_path, name)
            if not os.path.islink(p):
                continue
            try:
                target_rel = os.readlink(p)
                target_abs = os.path.realpath(p)
                items.append({
                    "name": name,
                    "link": p,
                    "target_rel": target_rel,
                    "target": target_abs,
                })
            except Exception:
                continue
    except Exception:
        return items
    return items


def _ptz_probe_get_ctrls(dev: str) -> Dict[str, Any]:
    """Best effort PTZ-Probe: prüft pan/tilt/zoom via v4l2-ctl -C ...

    Hintergrund:
      Manche Devices liefern bei parallelem OpenCV-Capture ein leeres
      `--list-ctrls`. -C <ctrl> ist hier oft robuster.
    """
    res = _safe_run(["v4l2-ctl", "-d", dev, "-C", "pan_absolute", "-C", "tilt_absolute", "-C", "zoom_absolute"], timeout_sec=1.2)
    if not res.get("ok"):
        return {"ok": False, "supported": False, "err": res.get("err", ""), "out": res.get("out", "")}
    out = (res.get("out") or "").strip()
    # Beispiel-Ausgabe:
    #   pan_absolute: 0
    #   tilt_absolute: 0
    #   zoom_absolute: 100
    have = ("pan_absolute" in out and "tilt_absolute" in out and "zoom_absolute" in out)
    return {"ok": True, "supported": bool(have), "out": out, "err": res.get("err", "")}

# -------------------- Quellen holen --------------------

def _get_hub():
    """DeviceHub (Singleton) holen – bevorzugte Quelle."""
    global _HUB
    if _HUB is not None:
        return _HUB
    try:
        from core.device_hub import get_hub  # type: ignore
        _HUB = get_hub()
        current_app.logger.info("[video_ui] DeviceHub aktiv.")
    except Exception as e:
        current_app.logger.info(f"[video_ui] DeviceHub nicht verfügbar: {e}")
        _HUB = None
    return _HUB

def _get_vw():
    """VisionWrapper als Fallback starten (ohne Kamera-Doppelöffnung, wenn Hub genutzt wird)."""
    global _VW
    with _LOCK:
        if _VW is not None:
            return _VW
        try:
            from wrappers import vision_wrapper  # type: ignore
            current_app.logger.info(
                f"[video_ui] Fallback VisionWrapper init "
                f"(Backend={os.environ.get('OROMA_VISION_BACKEND', os.environ.get('VISION_BACKEND','opencv'))})"
            )
            _VW = vision_wrapper.build_from_env("video-ui")
            _VW.start()
            current_app.logger.info(
                f"[video_ui] VisionWrapper gestartet – backend={getattr(_VW,'_backend_info',None)} "
                f"source={getattr(_VW,'source',None)} device={getattr(_VW,'device_index',None)}"
            )
        except Exception as e:
            current_app.logger.error(f"[video_ui] VisionWrapper Startfehler: {e}")
            _VW = None
        return _VW

# -------------------- MJPEG Generatoren --------------------

def _hub_mjpeg_push(hub, fps_cap: Optional[int] = None) -> Generator[bytes, None, None]:
    """Direkter Push-Stream vom DeviceHub (optimal)."""
    try:
        # WICHTIG: boundary als STRING weitergeben
        for chunk in hub.mjpeg_generator(boundary=BOUNDARY_STR, fps_cap=fps_cap, client="video_ui"):
            if isinstance(chunk, str):
                chunk = chunk.encode("latin1", "ignore")
            yield chunk
    except GeneratorExit:
        return
    except Exception as e:
        current_app.logger.warning(f"[video_ui] Hub-Push Abbruch: {e}")
        return

def _hub_mjpeg_poll(hub, fps: int = 10) -> Generator[bytes, None, None]:
    """Kompatibler Stream: pollt JPEGs aktiv und baut selbst MJPEG-Parts."""
    period = 1.0 / max(1, int(fps))
    while True:
        t0 = time.time()
        try:
            jpg = hub.get_latest_jpeg(client="video_ui")
            if jpg:
                yield (b"--" + BOUNDARY + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(jpg)).encode("ascii") + b"\r\n\r\n" +
                       jpg + b"\r\n")
        except GeneratorExit:
            return
        except Exception:
            time.sleep(0.05)
        dt = time.time() - t0
        time.sleep(max(0.001, period - dt))

def _vw_mjpeg_poll(vw, fps: int = 10) -> Generator[bytes, None, None]:
    """Polling-Stream über VisionWrapper (Fallback), JPEG via OpenCV."""
    if cv2 is None:
        yield from ()
        return
    period = 1.0 / max(1, int(fps))
    while True:
        t0 = time.time()
        try:
            frame = vw.get_overlay_frame(timeout=0.5)
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    jpg = buf.tobytes()
                    yield (b"--" + BOUNDARY + b"\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(jpg)).encode("ascii") + b"\r\n\r\n" +
                           jpg + b"\r\n")
        except GeneratorExit:
            return
        except Exception:
            time.sleep(0.05)
        dt = time.time() - t0
        time.sleep(max(0.001, period - dt))


def _bool_qs(name: str, default: bool = False) -> bool:
    try:
        raw = (request.args.get(name, "").strip().lower())
    except Exception:
        raw = ""
    if not raw:
        return bool(default)
    return raw in ("1", "true", "yes", "on")


def _frame_from_jpeg_bytes(jpeg: Optional[bytes]):
    if not jpeg or cv2 is None or np is None:
        return None
    try:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        if arr.size <= 0:
            return None
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        return None


def _draw_edge_overlay(frame, *, canny_low: int = None, canny_high: int = None, dilate_iter: int = None, alpha: float = None):
    """Build a highly visible edge-debug frame from a BGR frame.

    Ergebnis:
      - großes Banner "EDGE DEBUG ACTIVE" oben links
      - grüne Kantenmaske über dem Originalbild
      - zusätzliche Schwarz/Weiß-Edge-Maske oben rechts
      - untere Legende für sofort sichtbaren Debug-Zustand

    Wichtig:
      - best effort, nie nach außen werfen
      - wenn OpenCV/Numpy fehlen oder Frame ungeeignet ist -> Original zurück
    """
    if frame is None or cv2 is None or np is None:
        return frame
    try:
        if getattr(frame, 'ndim', 0) != 3:
            return frame
        out = frame.copy()
        low = int(_EDGE_CANNY_LOW if canny_low is None else canny_low)
        high = int(_EDGE_CANNY_HIGH if canny_high is None else canny_high)
        if high <= low:
            high = max(low + 1, low * 2)
        dil = int(_EDGE_DILATE if dilate_iter is None else dilate_iter)
        a = float(_EDGE_ALPHA if alpha is None else alpha)
        a = max(0.0, min(1.0, a))

        gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(blur, threshold1=low, threshold2=high)
        if dil > 0:
            kernel = np.ones((3, 3), dtype=np.uint8)
            edges = cv2.dilate(edges, kernel, iterations=dil)

        mask = edges > 0
        edge_count = int(np.count_nonzero(mask))
        total_px = int(edges.size) if getattr(edges, 'size', 0) else 1
        density = float(edge_count / max(1, total_px))

        overlay = out.copy()
        overlay[mask] = (0, 255, 0)
        out = cv2.addWeighted(overlay, a, out, max(0.0, 1.0 - a), 0.0)

        h = int(out.shape[0])
        w = int(out.shape[1])

        cv2.rectangle(out, (8, 8), (370, 74), (0, 0, 0), thickness=-1)
        cv2.rectangle(out, (8, 8), (370, 74), (0, 255, 0), thickness=2)
        cv2.putText(out, 'EDGE DEBUG ACTIVE', (18, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(out, f'canny={low}/{high}  density={density:.3f}', (18, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.54, (220, 255, 220), 1, cv2.LINE_AA)

        preview_w = min(220, max(120, w // 4))
        preview_h = min(160, max(90, h // 4))
        x1 = max(8, w - preview_w - 12)
        y1 = 10
        preview = cv2.resize(edges, (preview_w, preview_h), interpolation=cv2.INTER_NEAREST)
        preview_bgr = cv2.cvtColor(preview, cv2.COLOR_GRAY2BGR)
        cv2.rectangle(out, (x1 - 2, y1 - 24), (x1 + preview_w + 2, y1 + preview_h + 2), (0, 0, 0), thickness=-1)
        cv2.rectangle(out, (x1 - 2, y1 - 24), (x1 + preview_w + 2, y1 + preview_h + 2), (255, 255, 255), thickness=1)
        out[y1:y1 + preview_h, x1:x1 + preview_w] = preview_bgr
        cv2.putText(out, 'EDGE MASK', (x1 + 8, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)

        cv2.rectangle(out, (8, max(8, h - 34)), (260, h - 8), (0, 0, 0), thickness=-1)
        cv2.line(out, (18, h - 20), (98, h - 20), (0, 255, 0), 3, cv2.LINE_AA)
        cv2.putText(out, 'green overlay = detected edge', (106, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 255, 220), 1, cv2.LINE_AA)
        return out
    except Exception as e:
        try:
            current_app.logger.debug('[video_ui] edge overlay warn: %s', e)
        except Exception:
            pass
        return frame


def _encode_jpeg(frame, quality: int = 85) -> Optional[bytes]:
    if frame is None or cv2 is None:
        return None
    try:
        ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        return buf.tobytes() if ok else None
    except Exception:
        return None



def _diagnostic_frame(message: str, detail: str = '', *, width: int = 1280, height: int = 720):
    # Create a loud diagnostic frame so edge-debug failures are never silent.
    # Used when ?edges=1 is requested but no annotatable frame can be produced.
    if cv2 is None or np is None:
        return None
    try:
        w = max(640, int(width))
        h = max(360, int(height))
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:] = (18, 18, 28)
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (0, 0, 255), 6)
        cv2.rectangle(img, (18, 18), (min(w - 18, 760), 150), (0, 0, 0), -1)
        cv2.rectangle(img, (18, 18), (min(w - 18, 760), 150), (0, 0, 255), 2)
        cv2.putText(img, 'EDGE DEBUG REQUESTED', (36, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 0), 2, cv2.LINE_AA)
        cv2.putText(img, str(message)[:72], (36, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (255, 255, 255), 2, cv2.LINE_AA)
        if detail:
            cv2.putText(img, str(detail)[:96], (36, 136), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (200, 220, 255), 1, cv2.LINE_AA)
        cv2.putText(img, 'This frame appears only when ?edges=1 cannot produce an annotated frame.', (36, h - 42), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, 'Reason is shown here instead of silently returning the normal image.', (36, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (200, 220, 255), 1, cv2.LINE_AA)
        return img
    except Exception:
        return None

def _hub_latest_frame_or_jpeg(hub):
    """Best effort raw-frame fetch for debug overlays.

    Priorität:
      1) raw frame aus DeviceHub
      2) Decode des latest JPEG
    """
    if hub is None:
        return None
    try:
        frame, _ts = hub.get_latest_frame()
        if frame is not None:
            return frame
    except Exception:
        pass
    try:
        jpeg = hub.get_latest_jpeg(client='video_ui')
        return _frame_from_jpeg_bytes(jpeg)
    except Exception:
        return None


def _edge_mjpeg_poll(hub=None, vw=None, fps: int = 10, quality: int = 80) -> Generator[bytes, None, None]:
    """MJPEG generator with visible edge overlay.

    Wird nur auf expliziten Wunsch (?edges=1) genutzt, damit der normale
    Hub-Push-Pfad unverändert performant bleibt.

    Wichtiger Produktionsschutz:
      Der Edge-Pfad darf nie still das normale Bild weiterlaufen lassen. Wenn
      keine annotierbare Quelle verfügbar ist, wird ein diagnostisches JPEG mit
      rotem Banner erzeugt, damit die Ursache direkt im Browser sichtbar ist.
    """
    period = 1.0 / max(1, int(fps))
    while True:
        t0 = time.time()
        try:
            frame = None
            reason = ''
            if hub is not None:
                frame = _hub_latest_frame_or_jpeg(hub)
                if frame is None:
                    reason = 'hub latest frame/jpeg unavailable or undecodable'
            if frame is None and vw is not None and hasattr(vw, 'get_overlay_frame'):
                frame = vw.get_overlay_frame(timeout=0.5)
                if frame is None and not reason:
                    reason = 'vision wrapper overlay frame unavailable'
            if frame is not None:
                dbg = _draw_edge_overlay(frame)
            else:
                dbg = _diagnostic_frame('annotated edge frame unavailable', reason or 'no source frame available')
            jpg = _encode_jpeg(dbg, quality=quality) if dbg is not None else None
            if jpg:
                yield (b"--" + BOUNDARY + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(jpg)).encode("ascii") + b"\r\n\r\n" +
                       jpg + b"\r\n")
        except GeneratorExit:
            return
        except Exception as e:
            try:
                jpg = _encode_jpeg(_diagnostic_frame('edge stream exception', str(e)), quality=quality)
                if jpg:
                    yield (b"--" + BOUNDARY + b"\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(jpg)).encode("ascii") + b"\r\n\r\n" +
                           jpg + b"\r\n")
            except Exception:
                pass
            time.sleep(0.05)
        dt = time.time() - t0
        time.sleep(max(0.001, period - dt))

# -------------------- Routen --------------------

@video_bp.route("/stream")
def stream():
    """Primärer Stream: Hub-Push → (optional erzwungen) Poll → Fallback VisionWrapper-Poll.

    Zusatz:
      ?edges=1 erzwingt einen expliziten Debug-Poll-Pfad mit sichtbarer
      Kantenmaske. Damit bleibt der normale Push-Stream schnell, während die
      Diagnoseansicht gezielt annotiert werden kann.
    """
    hub = _get_hub()
    edges = _bool_qs('edges', default=False)

    if edges:
        vw = _get_vw() if hub is None else None
        if hub is None and not vw:
            return "Videoquelle nicht verfügbar (DeviceHub & VisionWrapper fehlgeschlagen)", 503
        gen = _edge_mjpeg_poll(hub=hub, vw=vw, fps=_FPS, quality=80)
    # Force-Poll global?
    elif _FORCE_POLL:
        if hub is not None:
            gen = _hub_mjpeg_poll(hub, fps=_FPS)
        else:
            vw = _get_vw()
            if not vw:
                return "Videoquelle nicht verfügbar (DeviceHub & VisionWrapper fehlgeschlagen)", 503
            gen = _vw_mjpeg_poll(vw, fps=_FPS)
    else:
        # Erst Push versuchen, ansonsten Poll
        if hub is not None and hasattr(hub, "mjpeg_generator"):
            gen = _hub_mjpeg_push(hub, fps_cap=None)
        elif hub is not None:
            gen = _hub_mjpeg_poll(hub, fps=_FPS)
        else:
            vw = _get_vw()
            if not vw:
                return "Videoquelle nicht verfügbar (DeviceHub & VisionWrapper fehlgeschlagen)", 503
            gen = _vw_mjpeg_poll(vw, fps=_FPS)

    resp = Response(gen, mimetype=f"multipart/x-mixed-replace;boundary={BOUNDARY_STR}")
    # Safari/Proxy-Kompatibilität: explizites Content-Type ohne Leerzeichen nach ;
    resp.headers["Content-Type"] = f"multipart/x-mixed-replace;boundary={BOUNDARY_STR}"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Connection"] = "keep-alive"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp



@video_bp.route("/mjpeg")
def mjpeg_alias():
    """Alias-Route für Kompatibilität.

    Hintergrund:
      Einige Tools/Checks erwarten /video/mjpeg. ORÓMA nutzt intern /video/stream
      als primären MJPEG-Endpoint.

    Verhalten:
      Identisch zu /video/stream.
    """
    return stream()


@video_bp.route("/mjpg")
def mjpg_alias():
    """Kurzalias für MJPEG (Kompat).

    Identisch zu /video/mjpeg und /video/stream.
    """
    return stream()


@video_bp.route("/stream_poll")
def stream_poll():
    """Erzwingt Polling-Stream (maximale Kompatibilität)."""
    hub = _get_hub()
    if hub is not None:
        gen = _hub_mjpeg_poll(hub, fps=_FPS)
    else:
        vw = _get_vw()
        if not vw:
            return "Videoquelle nicht verfügbar (DeviceHub & VisionWrapper fehlgeschlagen)", 503
        gen = _vw_mjpeg_poll(vw, fps=_FPS)

    resp = Response(gen, mimetype=f"multipart/x-mixed-replace;boundary={BOUNDARY_STR}")
    # Safari/Proxy-Kompatibilität: explizites Content-Type ohne Leerzeichen nach ;
    resp.headers["Content-Type"] = f"multipart/x-mixed-replace;boundary={BOUNDARY_STR}"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Connection"] = "keep-alive"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

@video_bp.route("/snapshot.jpg")
def snapshot():
    """Snapshot mit optionalem sichtbaren Kanten-Overlay.

    Query:
      ?edges=1   -> zeichnet grüne Kantenmaske + HUD in das JPEG
    """
    base = os.environ.get("OROMA_LOG_DIR", "/opt/ai/oroma/logs")
    try: os.makedirs(base, exist_ok=True)
    except Exception: pass

    edges = _bool_qs('edges', default=False)
    jpeg = None

    hub = _get_hub()
    if hub is not None:
        if edges:
            frame = _hub_latest_frame_or_jpeg(hub)
            if frame is not None:
                jpeg = _encode_jpeg(_draw_edge_overlay(frame), quality=85)
            if not jpeg:
                raw_jpeg = None
                try:
                    raw_jpeg = hub.get_latest_jpeg(client="video_ui")
                except Exception:
                    raw_jpeg = None
                if raw_jpeg:
                    dec = _frame_from_jpeg_bytes(raw_jpeg)
                    if dec is not None:
                        jpeg = _encode_jpeg(_draw_edge_overlay(dec), quality=85)
                if not jpeg:
                    jpeg = _encode_jpeg(_diagnostic_frame('edge snapshot unavailable', 'hub frame/jpeg missing or undecodable'), quality=85)
        else:
            jpeg = hub.get_latest_jpeg(client="video_ui")
        if not jpeg:
            return "Kein Bild verfügbar (Hub liefert None – Kamera warm?)", 503
    else:
        vw = _get_vw()
        if not vw:
            return "Videoquelle nicht verfügbar (DeviceHub & VisionWrapper fehlgeschlagen)", 503
        if edges and cv2 is not None and hasattr(vw, "snapshot"):
            frame = vw.snapshot(with_overlay=True)
            if frame is not None:
                jpeg = _encode_jpeg(_draw_edge_overlay(frame), quality=85)
        if edges and not jpeg and hasattr(vw, "snapshot_jpeg"):
            raw_jpeg = vw.snapshot_jpeg(with_overlay=True, quality=85)
            if raw_jpeg:
                frame = _frame_from_jpeg_bytes(raw_jpeg)
                if frame is not None:
                    jpeg = _encode_jpeg(_draw_edge_overlay(frame), quality=85)
        if not edges and not jpeg and hasattr(vw, "snapshot_jpeg"):
            jpeg = vw.snapshot_jpeg(with_overlay=True, quality=85)
        if not edges and not jpeg and cv2 is not None and hasattr(vw, "snapshot"):
            frame = vw.snapshot(with_overlay=True)
            if frame is not None:
                jpeg = _encode_jpeg(frame, quality=85)
        if edges and not jpeg:
            jpeg = _encode_jpeg(_diagnostic_frame('edge snapshot unavailable', 'vision wrapper could not provide annotatable frame'), quality=85)
        if not jpeg:
            return "Kein Bild verfügbar (VisionWrapper)", 503

    # Optional: letzten Snapshot ablegen (Fehlschläge ignorieren)
    try:
        out_path = os.path.join(base, "snapshot.jpg")
        with open(out_path, "wb") as f:
            f.write(jpeg)
        current_app.logger.info(f"[video_ui] Snapshot gespeichert: {out_path}")
    except Exception as e:
        current_app.logger.debug(f"[video_ui] Snapshot Save-Warnung: {e}")

    resp = Response(jpeg, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _parse_cam_token_blob(raw: Any) -> Optional[Dict[str, Any]]:
    """Parst einen snapchains.blob Eintrag fuer kind=cam_token best effort."""
    try:
        if raw is None:
            return None
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode('utf-8', errors='ignore')
        obj = json.loads(raw or '{}')
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _read_latest_vision_primitives(limit: int = 5) -> Dict[str, Any]:
    """Liest die letzten vision/token SnapChains und verdichtet motion/edges/color/q.

    Absichtlich leichtgewichtig:
    - nur LIMIT N
    - nur namespace='vision' und origin='vision/token'
    - kurzer sqlite timeout
    """
    out: Dict[str, Any] = {
        'ok': False,
        'source': 'snapchains:vision/token',
        'count': 0,
        'last_ts': 0,
        'age_sec': None,
        'last': {},
        'avg_5': {},
    }
    db_path = os.environ.get('OROMA_DB_PATH', '/opt/ai/oroma/data/oroma.db')
    conn = None
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=0.8)
        conn.execute('PRAGMA busy_timeout=800')
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ts, quality, blob
            FROM snapchains
            WHERE namespace='vision' AND origin='vision/token'
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        items = []
        for r in rows:
            obj = _parse_cam_token_blob(r['blob'])
            if not obj:
                continue
            item = {
                'ts': int(r['ts'] or 0),
                'q': None if r['quality'] is None else float(r['quality']),
                'motion': None if obj.get('motion') is None else float(obj.get('motion')),
                'edges': None if obj.get('edges') is None else float(obj.get('edges')),
                'color': None if obj.get('color') is None else float(obj.get('color')),
            }
            items.append(item)
        if not items:
            return out
        last = items[0]
        def _avg(key: str) -> Optional[float]:
            vals = [float(x[key]) for x in items if x.get(key) is not None]
            if not vals:
                return None
            return round(sum(vals) / float(len(vals)), 6)
        out.update({
            'ok': True,
            'count': len(items),
            'last_ts': int(last['ts'] or 0),
            'age_sec': max(0, int(time.time()) - int(last['ts'] or 0)) if last.get('ts') else None,
            'last': {
                'motion': last.get('motion'),
                'edges': last.get('edges'),
                'color': last.get('color'),
                'q': last.get('q'),
            },
            'avg_5': {
                'motion': _avg('motion'),
                'edges': _avg('edges'),
                'color': _avg('color'),
                'q': _avg('q'),
            },
        })
        return out
    except Exception as e:
        try:
            current_app.logger.warning('[video_ui] vision_primitives read failed: %s', e)
        except Exception:
            pass
        out['error'] = str(e)
        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


@video_bp.route('/api/vision_primitives')
def api_vision_primitives():
    limit = 5
    try:
        limit = max(1, min(10, int(request.args.get('limit', '5'))))
    except Exception:
        limit = 5
    return jsonify(_read_latest_vision_primitives(limit=limit))


def _edge_debug_state_payload() -> Dict[str, Any]:
    """Machine-readable edge-debug state for UI and future CLI diagnostics.

    This endpoint must stay lightweight and best effort. It should help answer:
    - is an annotatable frame available right now?
    - from which source?
    - can the current visible debug path theoretically work?
    """
    out: Dict[str, Any] = {
        'ok': True,
        'ts': int(time.time()),
        'edge_capable': bool(cv2 is not None and np is not None),
        'force_poll': bool(_FORCE_POLL),
        'stream_url': url_for('video.stream', edges=1),
        'snapshot_url': url_for('video.snapshot', edges=1),
        'raw_frame_available': False,
        'jpeg_available': False,
        'annotatable': False,
        'source': '',
        'reason': '',
    }
    if cv2 is None or np is None:
        out['reason'] = 'cv2 or numpy unavailable'
        return out

    hub = _get_hub()
    frame = None
    source = ''
    if hub is not None:
        try:
            fr, _ts = hub.get_latest_frame()
            if fr is not None:
                out['raw_frame_available'] = True
                frame = fr
                source = 'hub.latest_frame'
        except Exception:
            pass
        if frame is None:
            try:
                jpeg = hub.get_latest_jpeg(client='video_ui_diag')
                if jpeg:
                    out['jpeg_available'] = True
                    dec = _frame_from_jpeg_bytes(jpeg)
                    if dec is not None:
                        frame = dec
                        source = 'hub.latest_jpeg'
            except Exception:
                pass
    if frame is None:
        vw = _get_vw()
        if vw is not None and hasattr(vw, 'get_overlay_frame'):
            try:
                fr = vw.get_overlay_frame(timeout=0.25)
                if fr is not None:
                    frame = fr
                    source = 'vision_wrapper.overlay'
            except Exception:
                pass
    out['source'] = source
    if frame is None:
        out['reason'] = 'no annotatable frame available from hub/vw'
        return out
    try:
        dbg = _draw_edge_overlay(frame)
        jpg = _encode_jpeg(dbg, quality=80) if dbg is not None else None
        out['annotatable'] = bool(jpg)
        out['frame_shape'] = list(frame.shape) if hasattr(frame, 'shape') else None
        out['reason'] = '' if jpg else 'overlay encode failed'
    except Exception as e:
        out['annotatable'] = False
        out['reason'] = str(e)
    return out


@video_bp.route('/api/edge_debug_state')
def api_edge_debug_state():
    return jsonify(_edge_debug_state_payload())

@video_bp.route("/")
def index():
    refresh_ms = int(os.environ.get("OROMA_VIDEO_REFRESH_MS", "2000"))
    hub = _get_hub()
    status = "hub" if hub is not None else ("vision" if _VW else "none")
    ctx = {"refresh_ms": refresh_ms, "now": int(time.time()),
           "alive": False, "backend": None, "source": None, "device": None,
           "status": status,
           "edge_debug_default": False,
           "edge_debug_capable": bool(cv2 is not None and np is not None),
           "ptz": {"supported": False, "device": "", "reason": "n/a"},
           # Best-effort: letzter USB/KERNEL Alarm (aus usb_kernel_watch.json)
           "usb_alert": _read_usb_kernel_alert_state()}
    if hub is not None:
        try:
            st = hub.status(); cam = st.get("camera", {})
            ctx["backend"] = cam.get("backend")
            ctx["device"]  = cam.get("id")
            ctx["source"]  = cam.get("external_source") or "hub"
            ctx["ptz"]     = st.get("ptz") or ctx["ptz"]

            # External-Frame Betrieb: running kann False sein, aber Frames sind frisch.
            age = cam.get("last_frame_age")
            alive_by_age = (isinstance(age, (int, float)) and age <= _FRESH_SEC)
            ctx["alive"] = bool(cam.get("running")) or alive_by_age
        except Exception as e:
            log_suppressed('ui/video_ui.py:317', exc=e, level=logging.WARNING)
            pass
    elif _VW is not None:
        try:
            ctx["backend"] = getattr(_VW, "_backend_info", None)
            ctx["device"]  = getattr(_VW, "device_index", None)
            ctx["source"]  = getattr(_VW, "source", None)
            ctx["alive"]   = bool(_VW.is_alive())
        except Exception as e:
            log_suppressed('ui/video_ui.py:326', exc=e, level=logging.WARNING)
            pass
    return render_template("video.html", **ctx)


@video_bp.route("/api/usb_alert")
def api_usb_alert():
    """Small helper endpoint for UI polling.

    Always returns JSON (best-effort) and never raises.
    """
    return jsonify(_read_usb_kernel_alert_state())



# -----------------------------------------------------------------------------
# PTZ Motor Worker API (Video-Tab Integration, read-only)
# -----------------------------------------------------------------------------
# Zweck
# -----
# Der PTZ Motor Worker ist seit Phase 3a ein eigener, persistenter Servo-/Reflex-
# Prozess. Er darf nicht als zusätzliche UI-Seite versteckt werden, sondern wird
# bewusst im vorhandenen Video-Tab sichtbar gemacht, weil dort Kamera, PTZ und
# Live-Bild ohnehin zusammenlaufen.
#
# Sicherheits-/Produktionsregeln
# ------------------------------
# - Keine DB-Zugriffe und keine DB-Writes in diesen Endpunkten.
# - Status wird aus systemd + ptz_motor_state.json gelesen.
# - Die Video-UI ist fuer den Motor Worker bewusst READ-ONLY.
# - Flask/UI bekommt zu keinem Zeitpunkt Schreibrechte auf systemd.
# - Start/Stop/Restart erfolgen ausschliesslich manuell auf der Pi-Konsole
#   via `sudo systemctl start|stop|restart oroma-ptz-motor-worker.service`.
# - Die Unit bleibt "disabled by default"; die UI darf weder `enable` noch
#   `start`, `stop` oder `restart` ausfuehren.
# - Stale State wird sichtbar markiert, damit alte dx/dy/action-Werte nach
#   gestopptem Worker nicht wie aktive Motorik wirken.
# - Die Endpunkte blockieren nur kurz mit hartem Timeout.
#
# API
# ---
#   GET  /video/api/ptz/motor/status
#   POST /video/api/ptz/motor/control  -> bleibt absichtlich read-only/403
# -----------------------------------------------------------------------------

_PTZ_MOTOR_UNIT = os.environ.get("OROMA_PTZ_MOTOR_UNIT", "oroma-ptz-motor-worker.service")
_PTZ_MOTOR_STATE_PATH = os.environ.get(
    "OROMA_PTZ_MOTOR_STATE_PATH",
    os.path.join(_STATE_DIR, "ptz_motor_state.json"),
)
_PTZ_MOTOR_LOG_OUT = os.environ.get(
    "OROMA_PTZ_MOTOR_LOG_OUT",
    "/opt/ai/oroma/logs/ptz_motor_worker.out.log",
)
_PTZ_MOTOR_LOG_ERR = os.environ.get(
    "OROMA_PTZ_MOTOR_LOG_ERR",
    "/opt/ai/oroma/logs/ptz_motor_worker.err.log",
)


def _safe_tail_lines(path: str, limit: int = 20, max_bytes: int = 65536) -> Dict[str, Any]:
    """Read a small tail from a log file without blocking the UI.

    v1.3a: Delegiert bevorzugt an core.ptz_motor_state, damit UI,
    Diagnose und spaetere Attention/Snap-Module dieselbe read-only Leseschicht
    nutzen. Der lokale Code bleibt als Fallback fuer teilinstallierte Systeme.
    """
    if _ptz_core_safe_tail_lines is not None:
        return _ptz_core_safe_tail_lines(path=path, limit=limit, max_bytes=max_bytes)
    out: Dict[str, Any] = {"exists": False, "path": path, "lines": [], "error": ""}
    try:
        if not path or not os.path.exists(path):
            return out
        out["exists"] = True
        with open(path, "rb") as fh:
            try:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - int(max_bytes)), os.SEEK_SET)
            except Exception:
                pass
            data = fh.read(int(max_bytes))
        txt = data.decode("utf-8", "replace")
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        out["lines"] = lines[-max(1, int(limit)):]
    except Exception as e:
        out["error"] = str(e)
    return out


def _read_ptz_motor_state() -> Dict[str, Any]:
    """Read ptz_motor_state.json best-effort and annotate age/staleness.

    v1.3a: Zentrale Quelle ist core.ptz_motor_state.read_ptz_motor_state().
    Der lokale Code bleibt als Fail-soft-Fallback erhalten, damit die Video-UI
    auch bei unvollstaendigen Deployments nicht importseitig ausfaellt.
    """
    if _ptz_core_read_state is not None:
        return _ptz_core_read_state(path=_PTZ_MOTOR_STATE_PATH)
    path = _PTZ_MOTOR_STATE_PATH
    payload: Dict[str, Any] = {"exists": False, "path": path, "ok": False, "error": ""}
    try:
        if not path or not os.path.exists(path):
            return payload
        payload["exists"] = True
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            payload.update(data)
        else:
            payload["error"] = "state json is not an object"
        now = time.time()
        hb = payload.get("heartbeat_ts") or payload.get("ts") or 0
        try:
            age = max(0.0, now - float(hb)) if float(hb) > 0 else None
        except Exception:
            age = None
        payload["state_age_sec"] = None if age is None else round(float(age), 3)
        payload["state_stale"] = bool(age is not None and age > float(os.environ.get("OROMA_PTZ_MOTOR_UI_STALE_SEC", "5.0")))
        payload["ok"] = bool(payload.get("ok", False))
    except Exception as e:
        payload["error"] = str(e)
    return payload


def _json_safe_obj(value: Any, _seen: Optional[set] = None) -> Any:
    """Return a JSON-serialisable copy without circular references.

    v1.3a: Delegiert bevorzugt an core.ptz_motor_state.json_safe_obj(), damit
    alle PTZ-Motor-Statuspfade dieselbe JSON-Sicherheitslogik verwenden.

    Hintergrund / Produktionsfix:
    - Der PTZ-Motor-Status wird direkt an Flask `jsonify()` übergeben.
    - Python-Dicts/Listen können versehentlich zirkulär werden, z.B. wenn ein
      Ergebnisobjekt eine `attempts`-Liste referenziert, die wiederum genau
      dieses Ergebnisobjekt enthält. Flask/Python bricht dann mit
      `ValueError: Circular reference detected` ab.
    - Diese Funktion ist ein defensiver UI-Schutz: sie kopiert nur primitive
      JSON-Werte, kappt rekursive Referenzen sichtbar und wandelt unbekannte
      Objekte in Strings um. Sie schreibt nichts und verändert das Original nicht.
    """
    if _ptz_core_json_safe_obj is not None:
        return _ptz_core_json_safe_obj(value)

    if _seen is None:
        _seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    obj_id = id(value)
    if obj_id in _seen:
        return "<circular-reference>"

    if isinstance(value, dict):
        _seen.add(obj_id)
        out: Dict[str, Any] = {}
        for k, v in value.items():
            try:
                key = str(k)
            except Exception:
                key = "<unstringifiable-key>"
            out[key] = _json_safe_obj(v, _seen)
        _seen.discard(obj_id)
        return out

    if isinstance(value, (list, tuple, set)):
        _seen.add(obj_id)
        out_list = [_json_safe_obj(v, _seen) for v in list(value)]
        _seen.discard(obj_id)
        return out_list

    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _ptz_motor_attempt_record(cmd: List[str], rc: int, out: str = "", err: str = "", ok: bool = False) -> Dict[str, Any]:
    """Create one flat, JSON-safe systemctl attempt record."""
    return {
        "cmd": [str(x) for x in cmd],
        "rc": int(rc),
        "out": str(out or "").strip(),
        "err": str(err or "").strip(),
        "ok": bool(ok),
    }


def _run_systemctl_for_motor(args: List[str], timeout_sec: float = 4.0) -> Dict[str, Any]:
    """Run systemctl for the motor unit with a short timeout and visible errors.

    v1.3a: Delegiert bevorzugt an core.ptz_motor_state.run_systemctl_for_motor()
    und bleibt lokal nur als Fallback bestehen. Schreibende systemd-Aktionen
    duerfen weiterhin nicht aus Flask/UI erfolgen.

    The Flask service usually runs as user `oroma`. This helper is intentionally
    limited to read-only systemd status calls such as `show`, `is-active` and
    `is-enabled`. It does not try sudo and it is not used for runtime control.

    Wichtig:
    - Das Rückgabeobjekt ist bewusst flach kopiert und JSON-sicher.
    - Es enthält keine Selbstreferenz auf die `attempts`-Liste. Genau diese
      Selbstreferenz war die Ursache für HTTP 500 auf
      `/video/api/ptz/motor/status` mit `Circular reference detected`.
    """
    if _ptz_core_run_systemctl is not None:
        return _ptz_core_run_systemctl(args=args, timeout_sec=timeout_sec)

    safe_args = [str(a) for a in args if str(a).strip()]
    if not safe_args:
        return {"ok": False, "rc": -1, "out": "", "err": "missing systemctl args", "cmd": [], "attempts": []}

    # Read-only systemd access only. Do not use sudo from Flask/UI.
    # `systemctl show/is-active/is-enabled` are sufficient for status telemetry.
    # Runtime control remains a root/sudoer console action on the Pi.
    commands = [["/usr/bin/systemctl"] + safe_args]

    attempts: List[Dict[str, Any]] = []
    for cmd in commands:
        try:
            cp = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=float(timeout_sec),
                check=False,
            )
            item = _ptz_motor_attempt_record(
                cmd=cmd,
                rc=int(cp.returncode),
                out=cp.stdout or "",
                err=cp.stderr or "",
                ok=int(cp.returncode) == 0,
            )
            attempts.append(item)
            if item["ok"]:
                result = dict(item)
                result["attempts"] = [dict(a) for a in attempts]
                return result
        except subprocess.TimeoutExpired:
            attempts.append(_ptz_motor_attempt_record(cmd=cmd, rc=-2, err=f"timeout after {timeout_sec}s", ok=False))
        except Exception as e:
            attempts.append(_ptz_motor_attempt_record(cmd=cmd, rc=-3, err=str(e), ok=False))

    base = attempts[-1] if attempts else _ptz_motor_attempt_record(cmd=[], rc=-1, err="not executed", ok=False)
    result = dict(base)
    result["attempts"] = [dict(a) for a in attempts]
    return result


def _ptz_motor_status_payload(include_logs: bool = False) -> Dict[str, Any]:
    """Assemble UI status for the PTZ motor worker.

    v1.3a: Die bevorzugte Implementierung liegt in core.ptz_motor_state.
    Dadurch gibt es fuer UI, spaetere Attention/SnapChain-Module und Diagnose
    eine zentrale, read-only und JSON-sichere Quelle. Der lokale Fallback bleibt
    bewusst erhalten, damit die Video-UI auf teilinstallierten Systemen nicht
    hart ausfaellt.
    """
    if _ptz_core_read_status is not None:
        return _ptz_core_read_status(
            include_logs=include_logs,
            unit=_PTZ_MOTOR_UNIT,
            state_path=_PTZ_MOTOR_STATE_PATH,
            log_out=_PTZ_MOTOR_LOG_OUT,
            log_err=_PTZ_MOTOR_LOG_ERR,
        )

    active = _run_systemctl_for_motor(["is-active", _PTZ_MOTOR_UNIT], timeout_sec=2.0)
    enabled = _run_systemctl_for_motor(["is-enabled", _PTZ_MOTOR_UNIT], timeout_sec=2.0)
    show = _run_systemctl_for_motor([
        "show",
        _PTZ_MOTOR_UNIT,
        "--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus,Result,UnitFileState,FragmentPath",
        "--no-page",
    ], timeout_sec=2.5)

    props: Dict[str, str] = {}
    if show.get("ok") and show.get("out"):
        for line in str(show.get("out") or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[str(k)] = str(v)

    state = _read_ptz_motor_state()
    unit_exists = props.get("LoadState") not in ("", "not-found") if props else ("not-found" not in str(show.get("out") or show.get("err") or ""))

    payload: Dict[str, Any] = {
        "ok": True,
        "unit": _PTZ_MOTOR_UNIT,
        "unit_exists": bool(unit_exists),
        "active": active.get("ok") and str(active.get("out") or "").strip() == "active",
        "active_state": props.get("ActiveState") or (str(active.get("out") or "").strip() if active.get("out") else "unknown"),
        "sub_state": props.get("SubState") or "unknown",
        "enabled": enabled.get("ok") and str(enabled.get("out") or "").strip() == "enabled",
        "unit_file_state": props.get("UnitFileState") or (str(enabled.get("out") or "").strip() if enabled.get("out") else "unknown"),
        "main_pid": int(props.get("MainPID") or 0) if str(props.get("MainPID") or "0").isdigit() else 0,
        "fragment_path": props.get("FragmentPath") or "",
        "systemctl": {
            "active": active,
            "enabled": enabled,
            "show": show,
        },
        "state": state,
        "manual_only": True,
        "can_enable_from_ui": False,
        "logs": {
            "out": _PTZ_MOTOR_LOG_OUT,
            "err": _PTZ_MOTOR_LOG_ERR,
        },
    }
    payload["heartbeat_ok"] = bool(payload["active"] and state.get("exists") and not state.get("state_stale"))
    if include_logs:
        payload["log_tail"] = {
            "out": _safe_tail_lines(_PTZ_MOTOR_LOG_OUT, limit=20),
            "err": _safe_tail_lines(_PTZ_MOTOR_LOG_ERR, limit=20),
        }
    return payload


@video_bp.route("/api/ptz/motor/status")
def ptz_motor_status():
    """Return PTZ Motor Worker status for the Video tab.

    Dieser Endpoint darf bei kaputten systemctl-Ausgaben, ungewöhnlichen State-
    Dateien oder zukünftigen Payload-Erweiterungen nicht mehr mit HTTP 500
    abbrechen. Fehler werden als JSON sichtbar gemacht.
    """
    include_logs = (request.args.get("logs") or "").strip().lower() in ("1", "true", "yes", "on")
    try:
        payload = _ptz_motor_status_payload(include_logs=include_logs)
        return jsonify(_json_safe_obj(payload))
    except Exception as e:
        try:
            log_suppressed('ui/video_ui.py:ptz_motor_status', exc=e, level=logging.ERROR)
        except Exception:
            pass
        return jsonify({
            "ok": False,
            "error": str(e),
            "unit": _PTZ_MOTOR_UNIT,
            "manual_only": True,
            "can_enable_from_ui": False,
        }), 500


@video_bp.route("/api/ptz/motor/control", methods=["POST"])
def ptz_motor_control():
    """Read-only guard for legacy UI/control callers.

    Die Video-UI darf den PTZ Motor Worker nicht starten, stoppen, neu starten
    oder aktivieren. Diese Route bleibt nur erhalten, damit alte Browser-Tabs
    oder externe Tests eine klare, JSON-faehige Antwort bekommen statt 404/500.
    Runtime-Steuerung erfolgt ausschliesslich auf der Pi-Konsole:

        sudo systemctl start|stop|restart oroma-ptz-motor-worker.service

    Dadurch bleibt die Trennung sauber: UI = Status/Logs, root-Konsole = Motor-
    Kontrolle.
    """
    js = request.get_json(silent=True) or {}
    action = str(js.get("action") or "").strip().lower()
    status = _ptz_motor_status_payload(include_logs=True)
    return jsonify(_json_safe_obj({
        "ok": False,
        "error": "PTZ Motor Worker control is read-only from the Video UI; use sudo systemctl on the Pi console.",
        "action": action or None,
        "allowed_from_ui": [],
        "manual_commands": [
            f"sudo systemctl start {_PTZ_MOTOR_UNIT}",
            f"sudo systemctl stop {_PTZ_MOTOR_UNIT}",
            f"sudo systemctl restart {_PTZ_MOTOR_UNIT}",
        ],
        "status": status,
    })), 403



# -----------------------------------------------------------------------------
# PTZ Telemetrie / Coverage (UI + Autonomie)
# -----------------------------------------------------------------------------
# Motivation
# ----------
# Sobald PTZ Teil des Action-Space ist, brauchen wir Telemetrie:
#   - aktuelle Pan/Tilt/Zoom Werte (Zeitreihe)
#   - Action-Events (für Debug / Anti-Oszillation)
#   - Bewegungsabdeckung (Coverage) im UI: welche Bereiche wurden gesehen?
#
# Design
# ------
# - Wir loggen NUR wenn PTZ tatsächlich supported ist und ein Status verfügbar ist.
# - Writes sind Hot-Path → wir nutzen ausschliesslich den globalen DBWriter (Single-Writer).
# - Coverage wird rein aus metrics abgeleitet (ptz:pan, ptz:tilt), damit sie unabhängig
#   vom Video-Backend ist (opencv/picamera2/…).
#
# Keys (metrics)
# -------------
#   ptz:pan      -> int (V4L2 unit), current pan position
#   ptz:tilt     -> int (V4L2 unit), current tilt position
#   ptz:zoom     -> int (V4L2 unit), current zoom position
#   ptz:cmd:*    -> 1.0 (event counter), z.B. ptz:cmd:left
#
# Coverage Endpoint
# -----------------
#   GET /video/api/ptz/coverage?window_sec=86400&bins_x=9&bins_y=5
#   → liefert Grid (counts), Coverage%, Samples, und Blind-Spots (target pan/tilt)
# -----------------------------------------------------------------------------

def _write_metric_dbwriter_only(key: str, value: float, ts: Optional[int] = None) -> None:
    """Write one metric strictly via the global DBWriter.

    Wichtige Invariante:
    - kein lokaler SQLite-Fallback
    - kein Zugriff auf core.sql_manager.insert_metric()
    - bei deaktiviertem/fehlerhaftem DBWriter wird nur sichtbar geloggt
    
    DB:
    - target db: oroma
    - table: metrics
    """
    try:
        import core.db_writer_client as _dbw  # type: ignore
        if not bool(_dbw.enabled()):
            raise RuntimeError('db_writer disabled')
        now_ts = int(ts if ts is not None else time.time())
        _dbw.exec_lastrowid(
            sql='INSERT INTO metrics (key, ts, value) VALUES (?, ?, ?)',
            params=[str(key), int(now_ts), float(value)],
            tag='video_ui.metric',
            priority='low',
            timeout_ms=1500,
            db='oroma',
        )
    except Exception as e:
        try:
            log_suppressed('ui/video_ui.py:dbwriter_metric', exc=e, level=logging.WARNING)
        except Exception:
            pass


def _log_ptz_metrics_from_status(action: str, ptz_status: Dict[str, Any]) -> None:
    """Best effort: schreibt PTZ Metrics (pan/tilt/zoom + cmd event) in die DB.

    Wichtig:
    - niemals Exceptions nach außen werfen (UI/API darf nicht 500en).
    - verwendet ausschliesslich den globalen DBWriter (kein lokaler Fallback).
    """
    try:
        if not isinstance(ptz_status, dict):
            return
        if not ptz_status.get("supported"):
            return
        controls = (ptz_status.get("controls") or {}) if isinstance(ptz_status.get("controls"), dict) else {}

        def _ctrl_val(name: str) -> Optional[float]:
            c = controls.get(name)
            if isinstance(c, dict) and "value" in c:
                try:
                    return float(c.get("value"))
                except Exception:
                    return None
            return None

        pan = _ctrl_val("pan_absolute")
        tilt = _ctrl_val("tilt_absolute")
        zoom = _ctrl_val("zoom_absolute")

        now_ts = int(time.time())

        if pan is not None:
            _write_metric_dbwriter_only("ptz:pan", pan, ts=now_ts)
        if tilt is not None:
            _write_metric_dbwriter_only("ptz:tilt", tilt, ts=now_ts)
        if zoom is not None:
            _write_metric_dbwriter_only("ptz:zoom", zoom, ts=now_ts)

        # Action event counter (Debug/Analyse)
        a = (action or "").strip().lower()
        if a:
            _write_metric_dbwriter_only(f"ptz:cmd:{a}", 1.0, ts=now_ts)

    except Exception as e:
        # Nur debug – niemals UI stören
        try:
            current_app.logger.debug(f"[video_ui] PTZ metric log skipped: {e}")
        except Exception:
            pass


def _att_score(motion_norm: float, sharp_var: float) -> float:
    """Compute a small attention score from motion + sharpness.

    Dieses Score ist bewusst billig und robust. Er dient nur dazu, nach einem
    PTZ-Move einen "Attention Gain" (delta) zu schätzen.

    ENV:
      OROMA_PTZ_ATT_W_MOTION     (Default 1.0)
      OROMA_PTZ_ATT_W_SHARP      (Default 0.2)
      OROMA_PTZ_ATT_SHARP_DIV    (Default 10.0)  # log1p(var)/div
    """
    try:
        w_m = float(os.environ.get("OROMA_PTZ_ATT_W_MOTION", "1.0"))
    except Exception:
        w_m = 1.0
    try:
        w_s = float(os.environ.get("OROMA_PTZ_ATT_W_SHARP", "0.2"))
    except Exception:
        w_s = 0.2
    try:
        div = float(os.environ.get("OROMA_PTZ_ATT_SHARP_DIV", "10.0"))
        div = max(0.01, div)
    except Exception:
        div = 10.0

    # Motion already normalized 0..1. Sharpness is unbounded -> log compress.
    sharp_n = 0.0
    try:
        sharp_n = float(math.log1p(max(0.0, float(sharp_var))) / div)
    except Exception:
        sharp_n = 0.0
    # clamp sharp contribution to avoid spikes
    if sharp_n < 0.0:
        sharp_n = 0.0
    if sharp_n > 1.0:
        sharp_n = 1.0
    return float(w_m * float(motion_norm) + w_s * sharp_n)


def _att_pair_from_hub(hub, client: str) -> Optional[Dict[str, float]]:
    """Best-effort: compute cheap attention proxies from two recent frames.

    IMPORTANT (Production Safety):
    -----------------------------
    This helper MUST NOT:
      - force-start the camera (no ensure_start=True)
      - block for long or wait on device locks
      - depend on JPEG encode/decode paths (which can trigger camera starts)

    Therefore we ONLY use DeviceHub.get_latest_frame(ensure_start=False) and
    return None if no frames are available.
    """
    try:
        gap_ms = int(os.environ.get("OROMA_PTZ_ATTENTION_GAP_MS", "60"))
    except Exception:
        gap_ms = 60
    gap_ms = max(0, min(gap_ms, 250))

    try:
        f1, _ts1 = hub.get_latest_frame(ensure_start=False)
        if f1 is None:
            return None
        if gap_ms > 0:
            time.sleep(gap_ms / 1000.0)
        f2, _ts2 = hub.get_latest_frame(ensure_start=False)
        if f2 is None:
            return None

        g1 = _to_gray_small(f1)
        g2 = _to_gray_small(f2)
        if g1 is None or g2 is None:
            return None

        motion = _motion_score(g1, g2)
        sharp = _sharpness_score(g2)
        return {"motion": float(motion), "sharpness": float(sharp)}
    except Exception:
        return None


def _log_attention_gain(action: str, amount: int, pre: Optional[Dict[str, float]], post: Optional[Dict[str, float]], ptz_status: Dict[str, Any]) -> None:
    """Write attention gain metrics + reward log.

    - metrics: ptz:att_* (pre/post/gain)
    - rewards_log: source='ptz/attention_gain'

    Wichtig: Best-effort. Keine Exceptions nach außen.
    """
    # Diagnose: sichtbar machen, ob der Attention-Logger überhaupt erreicht wird.
    try:
        _write_metric_dbwriter_only("ptz:att_log_called", 1.0)
    except Exception:
        pass

    if pre is None or post is None:
        try:
            if pre is None:
                _write_metric_dbwriter_only("ptz:att_missing_pre", 1.0)
            if post is None:
                _write_metric_dbwriter_only("ptz:att_missing_post", 1.0)
        except Exception:
            pass
        return
    try:
        gain = float(post.get("score", 0.0) - pre.get("score", 0.0))
    except Exception:
        gain = 0.0

    now_ts = int(time.time())

    # metrics (best-effort)
    try:
        _write_metric_dbwriter_only("ptz:att_motion_pre", float(pre.get("motion", 0.0)), ts=now_ts)
        _write_metric_dbwriter_only("ptz:att_sharp_pre", float(pre.get("sharp", 0.0)), ts=now_ts)
        _write_metric_dbwriter_only("ptz:att_score_pre", float(pre.get("score", 0.0)), ts=now_ts)
        _write_metric_dbwriter_only("ptz:att_motion_post", float(post.get("motion", 0.0)), ts=now_ts)
        _write_metric_dbwriter_only("ptz:att_sharp_post", float(post.get("sharp", 0.0)), ts=now_ts)
        _write_metric_dbwriter_only("ptz:att_score_post", float(post.get("score", 0.0)), ts=now_ts)
        _write_metric_dbwriter_only("ptz:att_gain", float(gain), ts=now_ts)
        if action:
            _write_metric_dbwriter_only(f"ptz:att_action:{action}", 1.0, ts=now_ts)
    except Exception:
        pass

    # reward log (best-effort)
    try:
        rl = _get_reward_logger()
        if not rl:
            return
        raw = {
            "action": str(action),
            "amount": int(amount),
            "pre": pre,
            "post": post,
            "gain": gain,
            "ptz": ptz_status,
        }
        rl.log(source="ptz/attention_gain", step=0, reward=float(gain), raw=raw, ts=now_ts)
    except Exception:
        pass

# -----------------------------------------------------------------------------
# PTZ API (optional) – DeviceHub passthrough
# -----------------------------------------------------------------------------
# Diese Endpunkte sind bewusst minimal:
#   - Wenn PTZ nicht konfiguriert ist (OROMA_PTZ_DEVICE leer), liefert status
#     supported=False.
#   - Commands sind Phase-1 safe (center / nudges / zoom in/out).
#
# GET  /video/api/ptz/status
# POST /video/api/ptz/command  JSON: {"action":"left|right|up|down|zoom_in|zoom_out|center", "amount":1}
# -----------------------------------------------------------------------------


@video_bp.route("/api/ptz/status")
def ptz_status():
    """Return PTZ status and best-effort mirror current pose into metrics.

    Warum dieser Write-Pfad hier zusätzlich wichtig ist:
    - Die Video-UI lädt beim Öffnen zuerst den Status und erst danach Coverage.
    - Wenn gerade keine PTZ-Kommandos/Attention-Moves stattfinden, hätte die
      dokumentierte Coverage-Quelle (metrics: ptz:pan/ptz:tilt) sonst keinerlei
      frische Samples.
    - Durch dieses best-effort Mirroring bleibt die dokumentierte Architektur
      erhalten: /video/api/ptz/coverage liest weiterhin aus metrics, bekommt
      aber bereits beim regulären Status-Poll einen aktuellen Pose-Sample.

    Sicherheitsregeln:
    - Niemals den Status-Endpunkt wegen DB/Metric-Problemen failen lassen.
    - Keine stillen Request-Hänger: Metric-Logging bleibt klein und wird bei
      Fehlern nur sichtbar geloggt.
    """
    hub = _get_hub()
    if hub is None:
        return jsonify({"ok": False, "supported": False, "device": "", "error": "DeviceHub not available"})
    try:
        st = hub.ptz_status()
    except Exception as e:
        return jsonify({"ok": False, "supported": False, "device": "", "error": str(e)})

    try:
        _log_ptz_metrics_from_status(action="status", ptz_status=st)
    except Exception as e:
        try:
            current_app.logger.warning("[video_ui] PTZ status metric mirror skipped: %s", e)
        except Exception:
            pass

    return jsonify(st)


@video_bp.route("/api/ptz/command", methods=["GET", "POST"])
def ptz_command():
    """Execute a PTZ command (nudge/center/zoom) via DeviceHub.

    Produktionsziele (harte Invarianten):
    ------------------------------------
    1) UI-/curl-Requests dürfen NICHT „hängen“:
       - Keine potentiell lang blockierenden DB-Operationen im Request-Thread
       - Keine Kamera-Starts erzwingen (keine ensure_start=True Ketten)
       - Schutz gegen „rare hangs“ (z.B. Treiber/Stop/Start Edge Cases)

    2) Fehler dürfen NICHT still sein:
       - Wenn ein Timeout/Busy eintritt, wird es im Service-Log sichtbar
         und auch im JSON zurückgegeben.

    Hintergrund:
    ------------
    - Wenn Flask single-threaded läuft, blockiert *ein* hängender Request alle
      folgenden Requests (curl timeouts, Safari wirkt „tot“).
    - Zusätzlich können Metric-Inserts (SQLite busy_timeout) mehrere Sekunden
      warten und so UI-PTZ Requests künstlich verlängern.

    Daher:
    - PTZ Move wird mit einem kurzen Timeout ausgeführt (Thread-Join).
    - Telemetrie/Attention-Gain Logging läuft in einem separaten Hintergrund-Thread.
    """
    hub = _get_hub()
    if hub is None:
        return jsonify({"ok": False, "error": "DeviceHub not available"}), 503

    # Input: GET query params or POST JSON body
    action = ""
    amt = 1
    try:
        if request.method == "POST":
            js = request.get_json(silent=True) or {}
            action = (js.get("action") or "").strip().lower()
            amt = int(js.get("amount", 1))
        else:
            action = (request.args.get("action") or "").strip().lower()
            amt = int(request.args.get("amount", "1"))
    except Exception:
        action = (action or "").strip().lower()
        amt = 1

    if amt < 1:
        amt = 1
    if amt > 1000:
        amt = 1000

    # Logging: visible inbound command
    try:
        current_app.logger.info(
            "[video_ui] PTZ cmd: action=%s amount=%s remote=%s",
            action,
            amt,
            request.remote_addr,
        )
    except Exception:
        pass

    # Optional: cheap pre attention metrics (never forces camera start)
    att_enabled = os.environ.get("OROMA_PTZ_UI_LOG_ATTENTION", "0").strip().lower() in ("1", "true", "yes", "on")
    pre = None
    if att_enabled:
        try:
            pre = _att_pair_from_hub(hub, client="ptz_att_pre")
        except Exception:
            pre = None

    # ------------------------------------------------------------------
    # PTZ move with hard timeout (prevents request-thread hangs)
    # ------------------------------------------------------------------
    try:
        timeout_sec = float(os.environ.get("OROMA_PTZ_CMD_TIMEOUT_SEC", "2.0"))
    except Exception:
        timeout_sec = 2.0
    timeout_sec = max(0.2, min(timeout_sec, 10.0))

    res_box = {}
    err_box = {}

    def _do_move():
        try:
            res_box["res"] = hub.ptz_command(action, amount=amt)
        except Exception as e:
            err_box["err"] = e

    th = threading.Thread(target=_do_move, name="ptz_cmd", daemon=True)
    th.start()
    th.join(timeout=timeout_sec)

    if th.is_alive():
        # Request must return; the worker thread may still run, but UI stays alive.
        try:
            current_app.logger.warning(
                "[video_ui] PTZ cmd TIMEOUT after %.3fs: action=%s amount=%s",
                timeout_sec, action, amt
            )
        except Exception:
            pass
        try:
            st = hub.ptz_status()
        except Exception:
            st = {}
        return jsonify({
            "ok": False,
            "action": action,
            "amount": amt,
            "error": f"ptz_command_timeout_after_{timeout_sec:.3f}s",
            "status": st,
        })

    if "err" in err_box:
        e = err_box.get("err")
        try:
            current_app.logger.exception("[video_ui] PTZ cmd exception: %s", e)
        except Exception:
            pass
        try:
            st = hub.ptz_status()
        except Exception:
            st = {}
        return jsonify({"ok": False, "action": action, "amount": amt, "error": str(e), "status": st})

    res = res_box.get("res")
    if not isinstance(res, dict):
        try:
            st = hub.ptz_status()
        except Exception:
            st = {}
        res = {"ok": False, "action": action, "amount": amt, "error": "invalid ptz result", "status": st}

    # ------------------------------------------------------------------
    # Post-processing: metrics + attention gain (async to avoid curl timeouts)
    # ------------------------------------------------------------------
    postproc_enabled = os.environ.get("OROMA_PTZ_UI_POSTPROC", "1").strip().lower() not in ("0", "false", "no", "off")

    if postproc_enabled:
        def _postproc():
            try:
                st = res.get("status") or {}

                # 1) PTZ telemetry (pan/tilt/zoom) – may hit SQLite busy_timeout
                try:
                    _log_ptz_metrics_from_status(action=action, ptz_status=st)
                except Exception:
                    pass

                # 2) Optional attention gain (post) – allow settle, never start camera
                if att_enabled:
                    try:
                        settle_ms = int(os.environ.get("OROMA_PTZ_ATTENTION_SETTLE_MS", "250"))
                    except Exception:
                        settle_ms = 250
                    settle_ms = max(0, min(settle_ms, 2000))
                    if settle_ms > 0:
                        time.sleep(settle_ms / 1000.0)

                    post = None
                    try:
                        post = _att_pair_from_hub(hub, client="ptz_att_post")
                    except Exception:
                        post = None

                    try:
                        _log_attention_gain(action=action, amount=amt, pre=pre, post=post, ptz_status=st)
                    except Exception:
                        pass
            except Exception:
                pass

        threading.Thread(target=_postproc, name="ptz_postproc", daemon=True).start()

    return jsonify(res)

@video_bp.route("/api/ptz/coverage")
def ptz_coverage():
    """Compute PTZ movement coverage from metrics.

    This endpoint is designed for the /video UI to visualize "exploration":
    which pan/tilt regions were visited during a time window.

    MODES
    -----
    The coverage grid can represent two different concepts:

    1) mode=dwell (default)
       - counts *samples per bin* (dwell time / how long the camera stayed)
       - useful to see "stuck" regions (e.g. ceiling) as huge counts

    2) mode=moves
       - counts *entries into a bin* after collapsing consecutive duplicates
       - approximates "exploration" (bin transitions), independent of sample rate
       - "samples" in response becomes the number of deduped steps (not raw)

    Query:
      window_sec (default 86400)
      bins_x     (default 9)  - pan bins
      bins_y     (default 5)  - tilt bins
      mode       (default "dwell") -> "dwell" | "moves"

    Output:
      - coverage_pct
      - visited_bins / total_bins
      - samples (raw for dwell, steps for moves)
      - grid counts (bins_y rows, bins_x cols)
      - blind_spots (list of bin centers in device units)
    """
    hub = _get_hub()
    if hub is None:
        return jsonify({"ok": False, "error": "DeviceHub not available"}), 503

    try:
        window_sec = int(request.args.get("window_sec", "86400"))
    except Exception:
        window_sec = 86400
    try:
        bins_x = int(request.args.get("bins_x", "9"))
    except Exception:
        bins_x = 9
    try:
        bins_y = int(request.args.get("bins_y", "5"))
    except Exception:
        bins_y = 5

    scope = (request.args.get("scope", "observed") or "observed").strip().lower()
    if scope not in ("full", "observed"):
        return jsonify({"ok": False, "supported": True, "error": f"invalid scope: {scope}"}), 400

    mode = (request.args.get("mode", "dwell") or "dwell").strip().lower()
    if mode not in ("dwell", "moves"):
        return jsonify({"ok": False, "supported": True, "error": f"invalid mode: {mode}"}), 400

    # Clamp to sane bounds (UI safety)
    window_sec = max(60, min(window_sec, 30 * 86400))
    bins_x = max(3, min(bins_x, 31))
    bins_y = max(3, min(bins_y, 21))

    st = hub.ptz_status()
    if not (isinstance(st, dict) and st.get("supported")):
        return jsonify({
            "ok": True,
            "supported": False,
            "reason": st.get("reason") if isinstance(st, dict) else "n/a",
            "window_sec": window_sec,
            "bins_x": bins_x,
            "bins_y": bins_y,
            "mode": mode,
            "scope": scope,
            "samples": 0,
            "coverage_pct": 0.0,
            "grid": [],
        })

    controls = st.get("controls") or {}
    pan_c = controls.get("pan_absolute") or {}
    tilt_c = controls.get("tilt_absolute") or {}

    try:
        pan_min = int(pan_c.get("min"))
        pan_max = int(pan_c.get("max"))
        tilt_min = int(tilt_c.get("min"))
        tilt_max = int(tilt_c.get("max"))
    except Exception:
        return jsonify({"ok": False, "supported": True, "error": "PTZ controls missing min/max"}), 500

    if pan_max <= pan_min or tilt_max <= tilt_min:
        return jsonify({
            "ok": False,
            "supported": True,
            "error": "PTZ control ranges invalid",
            "pan": {"min": pan_min, "max": pan_max},
            "tilt": {"min": tilt_min, "max": tilt_max},
        }), 500

    now_ts = int(time.time())
    ts_min = now_ts - window_sec

    # Fetch samples (pan/tilt metrics) in the window
    # IMPORTANT:
    # - metrics.ts is in seconds (int). Multiple inserts per second are common.
    # - naive JOIN on ts may multiply rows (n_pan * n_tilt within same second).
    # - therefore: aggregate per ts first (AVG), then join.
    samples: List[Dict[str, Any]] = []
    try:
        from core.sql_manager import get_conn  # type: ignore
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                WITH p AS (
                  SELECT ts, AVG(value) AS pan
                  FROM metrics
                  WHERE key='ptz:pan' AND ts>=?
                  GROUP BY ts
                ),
                t AS (
                  SELECT ts, AVG(value) AS tilt
                  FROM metrics
                  WHERE key='ptz:tilt' AND ts>=?
                  GROUP BY ts
                )
                SELECT p.ts AS ts, p.pan AS pan, t.tilt AS tilt
                FROM p
                JOIN t ON t.ts = p.ts
                ORDER BY p.ts ASC
                LIMIT 50000
            """, (ts_min, ts_min))

            for row in cur.fetchall():
                try:
                    if isinstance(row, dict):
                        ts = row.get("ts")
                        pan = row.get("pan")
                        tilt = row.get("tilt")
                    else:
                        ts, pan, tilt = row  # type: ignore[misc]
                    samples.append({"ts": int(ts), "pan": float(pan), "tilt": float(tilt)})
                except Exception:
                    pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        return jsonify({"ok": False, "supported": True, "error": f"db query failed: {e}"}), 500

    grid = [[0 for _ in range(bins_x)] for __ in range(bins_y)]

    def _bin(v: float, vmin: int, vmax: int, bins: int) -> int:
        x = (v - vmin) / float(vmax - vmin)
        if x < 0.0:
            x = 0.0
        if x > 1.0:
            x = 1.0
        b = int(x * bins)
        if b >= bins:
            b = bins - 1
        return b

    raw_n = len(samples)

    device_pan_min = pan_min
    device_pan_max = pan_max
    device_tilt_min = tilt_min
    device_tilt_max = tilt_max

    if samples and scope == "observed":
        pan_vals = [float(s["pan"]) for s in samples]
        tilt_vals = [float(s["tilt"]) for s in samples]
        obs_pan_min = min(pan_vals)
        obs_pan_max = max(pan_vals)
        obs_tilt_min = min(tilt_vals)
        obs_tilt_max = max(tilt_vals)

        pan_span = max(1.0, float(device_pan_max - device_pan_min))
        tilt_span = max(1.0, float(device_tilt_max - device_tilt_min))
        pan_pad = max(3600.0, pan_span * 0.02)
        tilt_pad = max(3600.0, tilt_span * 0.02)

        pan_min = int(max(device_pan_min, math.floor(obs_pan_min - pan_pad)))
        pan_max = int(min(device_pan_max, math.ceil(obs_pan_max + pan_pad)))
        tilt_min = int(max(device_tilt_min, math.floor(obs_tilt_min - tilt_pad)))
        tilt_max = int(min(device_tilt_max, math.ceil(obs_tilt_max + tilt_pad)))

        if pan_max <= pan_min:
            mid = int(round((obs_pan_min + obs_pan_max) / 2.0))
            pan_min = max(device_pan_min, mid - 3600)
            pan_max = min(device_pan_max, mid + 3600)
        if tilt_max <= tilt_min:
            mid = int(round((obs_tilt_min + obs_tilt_max) / 2.0))
            tilt_min = max(device_tilt_min, mid - 3600)
            tilt_max = min(device_tilt_max, mid + 3600)

    if mode == "dwell":
        for s in samples:
            bx = _bin(s["pan"], pan_min, pan_max, bins_x)
            by = _bin(s["tilt"], tilt_min, tilt_max, bins_y)
            grid[by][bx] += 1
        steps_n = raw_n
        moves_n = max(0, steps_n - 1)
        visited_set = {(x, y) for y in range(bins_y) for x in range(bins_x) if grid[y][x] > 0}

    else:  # mode == "moves"
        # Collapse consecutive duplicates in *binned* space.
        seq: List[tuple[int,int]] = []
        prev = None
        for s in samples:
            bx = _bin(s["pan"], pan_min, pan_max, bins_x)
            by = _bin(s["tilt"], tilt_min, tilt_max, bins_y)
            cur = (bx, by)
            if cur != prev:
                seq.append(cur)
                prev = cur

        # Count entries into bins (including first entry).
        for (bx, by) in seq:
            grid[by][bx] += 1

        steps_n = len(seq)
        moves_n = max(0, steps_n - 1)
        visited_set = set(seq)

    visited = len(visited_set)
    total = bins_x * bins_y
    coverage_pct = (100.0 * visited / total) if total else 0.0

    blind = []
    for by in range(bins_y):
        for bx in range(bins_x):
            if (bx, by) not in visited_set:
                pan0 = pan_min + (bx + 0.5) * (pan_max - pan_min) / bins_x
                tilt0 = tilt_min + (by + 0.5) * (tilt_max - tilt_min) / bins_y
                blind.append({"bin_x": bx, "bin_y": by, "pan": int(round(pan0)), "tilt": int(round(tilt0))})

    return jsonify({
        "ok": True,
        "supported": True,
        "ts": now_ts,
        "window_sec": window_sec,
        "bins_x": bins_x,
        "bins_y": bins_y,
        "mode": mode,
        "scope": scope,
        "pan": {"min": pan_min, "max": pan_max},
        "tilt": {"min": tilt_min, "max": tilt_max},
        "device_pan": {"min": device_pan_min, "max": device_pan_max},
        "device_tilt": {"min": device_tilt_min, "max": device_tilt_max},
        # Backwards compatible fields
        "samples": steps_n if mode == "moves" else raw_n,
        "visited_bins": visited,
        "total_bins": total,
        "coverage_pct": round(coverage_pct, 2),
        "grid": grid,
        "blind_spots": blind[:200],
        # Extra diagnostics (UI may show them)
        "raw_samples": raw_n,
        "steps": steps_n,
        "moves": moves_n,
    })


@video_bp.route("/api/devices")

def devices():
    """List V4L2 devices and stable symlinks.

    Returns a small diagnostic payload for the /video UI. Intended for human
    ops/debugging. Best effort; never raises.
    """
    by_id = _read_symlinks("/dev/v4l/by-id")
    by_path = _read_symlinks("/dev/v4l/by-path")

    # v4l2-ctl --list-devices (raw text) – very useful, but optional
    lst = _run_cmd(["v4l2-ctl", "--list-devices"], timeout_sec=2.0)

    # Quick PTZ check for by-id entries only (usually small). We annotate the
    # resolved /dev/videoX node, not the symlink.
    for item in by_id:
        real = item.get("real") or ""
        if real.startswith("/dev/video"):
            item["ptz"] = _ptz_quickcheck(real)
        else:
            item["ptz"] = {"supported": False, "detail": "not a /dev/video node"}

    return jsonify({
        "ok": True,
        "ts": int(time.time()),
        "by_id": by_id,
        "by_path": by_path,
        "v4l2_list_devices": {
            "ok": bool(lst.get("ok")),
            "rc": lst.get("rc"),
            "out": lst.get("out"),
            "err": lst.get("err"),
        },
    })

@video_bp.route("/healthz")
def healthz():
    hub = _get_hub()
    if hub is not None:
        try:
            st = hub.status(); cam = st.get("camera", {})

            age = cam.get("last_frame_age")
            alive_by_age = (isinstance(age, (int, float)) and age <= _FRESH_SEC)
            ok = bool(cam.get("running")) or alive_by_age

            return jsonify({
                "backend": cam.get("backend"),
                "device": cam.get("id"),
                "running": bool(cam.get("running")),
                "last_frame_age": age,
                "external_source": cam.get("external_source"),
                "external_frames": cam.get("external_frames"),
                "external_last_ts": cam.get("external_last_ts"),
                "ok": ok,
                "source": "hub",
            })
        except Exception:
            return jsonify({"ok": False, "source": "hub"})
    vw = _get_vw()
    return jsonify({"ok": bool(vw and vw.is_alive()), "source": "vision",
                    "backend": getattr(vw, "_backend_info", None),
                    "device": getattr(vw, "device_index", None),
                    "source_uri": getattr(vw, "source", None)})


# ---- Cleanup (DeviceHub nicht stoppen!) ----
def _shutdown():
    global _VW
    if _VW:
        try:
            _VW.stop()
            current_app.logger.info("[video_ui] VisionWrapper (Fallback) gestoppt.")
        except Exception as e:
            log_suppressed('ui/video_ui.py:368', exc=e, level=logging.WARNING)
            pass
        _VW = None

atexit.register(_shutdown)

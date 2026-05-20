#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/ptz_attention_loop.py
# Projekt:   ORÓMA – Headless Lern-KI (Edge / Orchestrator)
# Modul:     PTZ Attention Loop (Phase-1) – „tierähnliche“ Orientierung / Scan / Fixation
# Version:   v3.7.3+ptz-follow-hotpatch7
# Stand:     2026-05-07
# Autor:     Jörg Werner (public) / ORÓMA Project (internal)
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul implementiert einen konservativen, headless „Aufmerksamkeits“-Loop
# für PTZ-Kameras (z.B. EMEET PIXY via V4L2 Controls pan/tilt/zoom).
#
# Ziel ist NICHT „hartes RL“ im ersten Schritt, sondern ein stabiles, messbares,
# sicherheitsorientiertes Verhalten, das „tierähnlich“ wirkt und gleichzeitig
# robuste Lern-/Telemetrie-Signale erzeugt:
#
#   • FIXATE  (ruhig bleiben / zuhören / analysieren)
#   • ORIENT  (seltene, kurze Sakkaden/Nudges bei deutlicher Aktivität/Neuheit)
#   • SCAN    (langsames, systematisches Abtasten → Coverage wächst)
#
# WICHTIG
# ───────
# - Das Modul ist „Fail-Closed“:
#     Wenn kein frischer Frame verfügbar ist oder PTZ nicht supported ist,
#     führt es KEINE Bewegungen aus.
# - Bewegungen sind Phase-1 safe:
#     es werden ausschließlich „nudges“ (left/right/up/down), „center“ und
#     „zoom_in/zoom_out“ genutzt.
# - Telemetrie wird konsequent in metrics geschrieben:
#     ptz:pan / ptz:tilt / ptz:zoom (+ ptz:cmd:* event counter)
#   Dadurch kann die UI (Video: PTZ Coverage) die Bewegungsabdeckung anzeigen
#   und „blinde Flecke“ sichtbar machen.
#
# INTEGRATION
# ───────────
# Orchestrator (empfohlen):
#   tools/oroma_orchestrator.py ruft dieses Modul seriell auf:
#     python -m core.ptz_attention_loop --once
#   Aktivierung über ENV:
#     OROMA_ORCH_ENABLE_PTZ_ATTENTION=1
#     OROMA_ORCH_INT_PTZ_ATTENTION=2
#
# Datenquellen:
#   - Frames: core.camera_hub.get_frame_with_ts()
#   - PTZ:    core.device_hub.get_hub().ptz_command(...)
#   - DB:     core.sql_manager.insert_metric() (lock-robust)
#
# STATE / PERSISTENZ
# ──────────────────
# Der Loop speichert minimalen Zustand (Prev-Frame downsample + Scan-Zähler)
# in einer JSON-Datei:
#   /opt/ai/oroma/data/state/ptz_attention_state.json
#
# Das ist bewusst klein und robust, damit Reboots/Crashes keine Schäden erzeugen.
#
# ENV (FEINTUNING – sichere Defaults)
# ──────────────────────────────────
# Allgemein:
#   OROMA_BASE                          (Default /opt/ai/oroma)
#   OROMA_PTZ_ATTENTION_STATE_PATH      (Default {BASE}/data/state/ptz_attention_state.json)
#   OROMA_PTZ_ATTENTION_DRY_RUN         (Default 0)   # 1 = keine Bewegungen ausführen (nur Telemetrie)
#   OROMA_PTZ_ATTENTION_ENSURE_CAM      (Default 0)   # Orchestrator nutzt bevorzugt Cache/Snapshot; 1 nur standalone
#
# Mode/Trigger:
#   OROMA_PTZ_ATTENTION_MOTION_LOW      (Default 0.008)  # darunter gilt als „ruhig“
#   OROMA_PTZ_ATTENTION_MOTION_HIGH     (Default 0.022)  # darüber gilt als „aktiv“
#   OROMA_PTZ_ATTENTION_BORED_SEC       (Default 90)     # nach so vielen ruhigen Sekunden → SCAN
#   OROMA_PTZ_ATTENTION_ORIENT_COOLDOWN (Default 4)      # Mindestabstand zwischen Orient-Nudges
#
# Scan:
#   OROMA_PTZ_ATTENTION_SCAN_BINS_X     (Default 7)
#   OROMA_PTZ_ATTENTION_SCAN_BINS_Y     (Default 4)
#   OROMA_PTZ_ATTENTION_SCAN_STEP       (Default 1)      # nudge steps pro Tick
#   OROMA_PTZ_SCAN_TILT_SOFT_MIN_FRAC   (Default 0.20)  # Scan meidet unteren Tilt-Anteil (Boden/Untertisch)
#   OROMA_PTZ_SCAN_TILT_SOFT_MAX_FRAC   (Default 0.12)  # Scan meidet oberen Tilt-Anteil (Decke/Lampe)
#   OROMA_PTZ_SCAN_TILT_SOFT_DARK_BOOST (Default 0.15)  # Extra-Boost auf MIN_FRAC wenn luma 'dark-ish'
#
# Fixation:
#   OROMA_PTZ_ATTENTION_FIXATE_SEC_MIN  (Default 2)
#   OROMA_PTZ_ATTENTION_FIXATE_SEC_MAX  (Default 4)
#
# Luma (Helligkeit) – Recovery (biologisch: nur wenn Bild dauerhaft extrem dunkel/hell ist):
#   OROMA_PTZ_LUMA_RECOVER_ENABLE        (Default 1)     # 0 = deaktivieren
#   OROMA_PTZ_LUMA_LOW                  (Default 0.12)  # unterhalb = zu dunkel
#   OROMA_PTZ_LUMA_HIGH                 (Default 0.85)  # oberhalb  = zu hell
#   OROMA_PTZ_LUMA_HYST                 (Default 0.03)  # Hysterese (Verhindert Flattern)
#   OROMA_PTZ_LUMA_HOLD_SEC             (Default 2)     # wie lange dauerhaft dunkel/hell bis Recovery
#   OROMA_PTZ_LUMA_EMA_ALPHA            (Default 0.02)  # EMA für Luma (Noise/AE glätten)
#   OROMA_PTZ_LUMA_RECOVER_STEPS        (Default 2)     # tick-basierte Nudges pro Trigger
#   OROMA_PTZ_LUMA_RECOVER_COOLDOWN_SEC (Default 8)     # Cooldown zwischen Recovery-Bursts
#
# Zoom (Phase-1: optional, default off):
#   OROMA_PTZ_ATTENTION_ZOOM_ENABLE     (Default 0)
#   OROMA_PTZ_ATTENTION_ZOOM_STEP       (Default 1)
#
# Fine Motion / Follow:
#   OROMA_PTZ_ATTENTION_MOTION_W        (Default 96)    # Downsample-Breite für Motion/Follow
#   OROMA_PTZ_ATTENTION_MOTION_H        (Default 54)    # Downsample-Höhe  für Motion/Follow
#   OROMA_PTZ_THREAT_FORCE_DIST         (Default 0.045) # erzwingt Follow auch nahe Bildmitte
#   OROMA_PTZ_THREAT_FORCE_ENERGY       (Default 0.006) # Mindestenergie für Force-Follow
#   OROMA_PTZ_THREAT_FORCE_AXIS_MIN     (Default 0.035) # minimale Achsenabweichung fuer Dominant-Achse-Follow
#   OROMA_PTZ_THREAT_DEADZONE_MIN       (Default 0.040) # kleinste dynamische Deadzone bei starker Bewegung
#   OROMA_PTZ_THREAT_DEADZONE_E_SCALE   (Default 1.80)  # wie stark Energie die Deadzone reduziert
#   OROMA_PTZ_THREAT_STICKY_DIR_SEC     (Default 2)     # letzte Follow-Richtung kurz weiterverwenden
#   OROMA_PTZ_THREAT_STICKY_ENERGY      (Default 0.010) # Mindestenergie fuer Sticky-Direction
#   OROMA_PTZ_THREAT_FIXATE_SEC         (Default 1)     # kurze Nachruhe nach Threat-Move
#   OROMA_PTZ_THREAT_BURST_ENABLE       (Default 1)     # Threat-Move als kleiner Mehrfach-Nudge
#   OROMA_PTZ_THREAT_BURST_STEPS        (Default 3)     # Basis-Nudge-Schritte pro Threat-Tick
#   OROMA_PTZ_THREAT_AMOUNT_DYNAMIC     (Default 1)     # Schrittgröße nach Abstand/Energie dynamisch erhöhen
#   OROMA_PTZ_THREAT_AMOUNT_MIN         (Default 5)     # minimale sichtbare Follow-Schrittgröße bei Threat
#   OROMA_PTZ_THREAT_AMOUNT_MAX         (Default 8)     # harte Obergrenze gegen Überschwingen
#   OROMA_PTZ_THREAT_AMOUNT_DIST_SCALE  (Default 0.22)  # normierter Abstand für Max-Schritt
#   OROMA_PTZ_THREAT_AMOUNT_E_SCALE     (Default 0.035) # normierte Energie für Max-Schritt
#   OROMA_PTZ_FOLLOW_INVERT_X           (Default 1)     # EMEET PIXY: Follow-Pan optisch invertieren
#   OROMA_PTZ_FOLLOW_INVERT_Y           (Default 0)     # Tilt-Follow nur bei Bedarf invertieren
#   OROMA_PTZ_SELF_MOTION_GUARD_SEC     (Default 1.20)  # Nach PTZ-Move eigene Kamerabewegung kurz ignorieren
#   OROMA_PTZ_THREAT_REVERSAL_GUARD_SEC (Default 10.0)  # Links/Rechts- bzw. Hoch/Runter-Pendelung bremsen
#   OROMA_PTZ_THREAT_REVERSAL_RATIO     (Default 1.65)  # Umkehr nur bei deutlich stärkerem neuen Signal erlauben
#   OROMA_PTZ_THREAT_VERTICAL_GAIN      (Default 1.35)  # Tilt-Achse gegen Pan-Dominanz leicht gewichten
#   OROMA_PTZ_CMD_VERIFY_POSE           (Default 1)     # PTZ-Erfolg mit Status/Telemetrie sichtbar prüfen
#
# AUSFÜHRUNG
# ──────────
# One-Shot (für Orchestrator):
#   python -m core.ptz_attention_loop --once
#
# Debug:
#   python -m core.ptz_attention_loop --once --verbose
#
# HINWEISE ZUR HARDWARE-SICHERHEIT
# ────────────────────────────────
# Soft-Limits & Cooldowns werden im PTZ-Controller (core/ptz_controller.py bzw.
# DeviceHub PTZ) bereits umgesetzt. Dieser Loop respektiert zusätzlich:
#   - eigene Cooldowns (orient)
#   - FIXATE windows (ruhig bleiben)
#
# =============================================================================

from __future__ import annotations

import os
import json
import time
import random
import argparse
import math
import urllib.request
import urllib.error
import glob
import logging
from typing import Dict, Any, Optional, Tuple, List

from core.device_hub import get_hub
from core.camera_hub import (
    get_frame_with_ts,
    get_cached_frame_with_ts,
    get_cached_frame_with_ts_fast,
    get_cached_frame_with_ts_fast_diag,
)
from wrappers.ptz_controller import PTZController
from core.sql_manager import insert_metric, get_conn


def _fetch_ui_snapshot(url: str, timeout_sec: float = 2.0):
    """Fetch a current JPEG snapshot from the UI and decode it as OpenCV BGR.

    Returns:
        (ok, frame, err) where:
            ok:   bool
            frame: np.ndarray | None
            err:  str (empty on success)

    Auth:
        If the Flask UI is token-protected, this function will automatically
        attach the token via HTTP header:
            X-OROMA-TOKEN: <token>

        The token is read from (first wins):
            - OROMA_PTZ_ATTENTION_UI_TOKEN
            - OROMA_UI_TOKEN
    """
    try:
        headers = {"User-Agent": "oroma-ptz-att/1"}
        tok = (os.environ.get("OROMA_PTZ_ATTENTION_UI_TOKEN") or os.environ.get("OROMA_UI_TOKEN") or "").strip()
        if tok:
            headers["X-OROMA-TOKEN"] = tok
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:
            data = r.read()
        if not data:
            return (False, None, "empty response")
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return (False, None, "decode failed")
        return (True, img, "")
    except Exception as e:
        return (False, None, str(e))


# -----------------------------------------------------------------------------
# PTZ backend selection (direct v4l2 preferred)
# -----------------------------------------------------------------------------
#
# Motivation:
# - In Orchestrator/OneShot runs this module often executes as a separate process.
#   Starting a full DeviceHub stack here would unnecessarily open camera/audio
#   devices (risking locks) although we can fetch frames via the running service's
#   snapshot endpoint.
# - The Video-UI PTZ buttons work v4l2-based on a /dev/videoX device, while
#   DeviceHub PTZ only becomes available when OROMA_PTZ_DEVICE is set.
# - To keep PTZ attention usable even when DeviceHub isn't configured for PTZ,
#   we try a direct v4l2 PTZ controller first and only fall back to the hub
#   backend if needed.
#
# Controls:
#   OROMA_PTZ_ATTENTION_USE_HUB=1        → force hub PTZ backend
#   OROMA_PTZ_DEVICE=/dev/videoX|/dev/v4l/by-id/... → preferred PTZ device
#   OROMA_PTZ_ATTENTION_NUDGE_PAN=3600   → raw pan nudge (default 3600)
#   OROMA_PTZ_ATTENTION_NUDGE_TILT=3600  → raw tilt nudge (default 3600)
#   OROMA_PTZ_ATTENTION_ZOOM_STEP=1      → zoom step (default 1)
#   OROMA_PTZ_ATTENTION_SNAPSHOT_FIRST=1 → prefer UI snapshot frames (default 1)


def _autodetect_ptz_device() -> str:
    """Best-effort autodetect of a PTZ-capable V4L2 device."""
    candidates: list[str] = []
    try:
        candidates.extend(sorted(glob.glob("/dev/v4l/by-id/*video-index*")))
    except Exception:
        pass
    for i in range(0, 12):
        candidates.append(f"/dev/video{i}")

    seen = set()
    for dev in candidates:
        if dev in seen:
            continue
        seen.add(dev)
        if not os.path.exists(dev):
            continue
        try:
            st = PTZController(dev).status()
            if st.get("supported"):
                return dev
        except Exception:
            continue
    return ""


class _DirectPTZBackend:
    """Direct PTZ control via v4l2-ctl (PTZController wrapper)."""

    def __init__(self, device: str):
        self.device = device
        self.ctrl = PTZController(device)
        try:
            self.nudge_pan = int(os.getenv("OROMA_PTZ_ATTENTION_NUDGE_PAN", "3600"))
        except Exception:
            self.nudge_pan = 3600
        try:
            self.nudge_tilt = int(os.getenv("OROMA_PTZ_ATTENTION_NUDGE_TILT", "3600"))
        except Exception:
            self.nudge_tilt = 3600
        try:
            self.zoom_step = int(os.getenv("OROMA_PTZ_ATTENTION_ZOOM_STEP", "1"))
        except Exception:
            self.zoom_step = 1

    def ptz_status(self) -> dict:
        st = self.ctrl.status() or {}
        pan = (st.get("pan") or {}).get("value")
        tilt = (st.get("tilt") or {}).get("value")
        zoom = (st.get("zoom") or {}).get("value")
        st["pose"] = {"pan": pan, "tilt": tilt, "zoom": zoom}
        st["backend"] = "direct-v4l2"
        return st

    def ptz_command(self, action: str, amount: int = 1) -> dict:
        a = (action or "").strip().lower()
        if a in ("left", "right", "up", "down"):
            # amount ist ein Multiplikator (wie beim Hub). Default 1.
            # Wichtig: PTZController.nudge() arbeitet mit direction+steps (nicht pan_delta/tilt_delta).
            try:
                amt = int(amount)
            except Exception:
                amt = 1
            if amt < 1:
                amt = 1
            return self.ctrl.nudge(direction=a, steps=amt)
        if a == "center":
            return self.ctrl.center()
        if a in ("zoom_in", "zin", "+"):
            st = self.ctrl.status() or {}
            z = (st.get("zoom") or {}).get("value")
            z = int(z) if z is not None else 0
            return self.ctrl.zoom_abs(z + (self.zoom_step * max(1, int(amount) if str(amount).isdigit() else 1)))
        if a in ("zoom_out", "zout", "-"):
            st = self.ctrl.status() or {}
            z = (st.get("zoom") or {}).get("value")
            z = int(z) if z is not None else 0
            return self.ctrl.zoom_abs(z - (self.zoom_step * max(1, int(amount) if str(amount).isdigit() else 1)))
        return {"ok": False, "action": a, "device": self.device, "err": f"unknown action: {a}"}


def _resolve_ptz_backend(logger: logging.Logger):
    """Resolve PTZ backend: prefer direct-v4l2, fallback to hub."""
    force_hub = os.getenv("OROMA_PTZ_ATTENTION_USE_HUB", "0").strip().lower() in ("1", "true", "yes", "on")
    if force_hub:
        logger.info("[ptz_attention_loop] PTZ backend forced to HUB via OROMA_PTZ_ATTENTION_USE_HUB=1")
        return "hub", None

    dev = os.getenv("OROMA_PTZ_DEVICE", "").strip()
    if not dev:
        dev = _autodetect_ptz_device()
        if dev:
            logger.info("[ptz_attention_loop] PTZ autodetect selected device: %s", dev)

    if dev:
        try:
            b = _DirectPTZBackend(dev)
            st = b.ptz_status()
            if st.get("supported"):
                return "direct-v4l2", b
            logger.warning("[ptz_attention_loop] Direct PTZ device not supported: %s (reason=%s)", dev, st.get("reason"))
        except Exception as e:
            logger.warning("[ptz_attention_loop] Direct PTZ backend init failed for %s: %s", dev, e)

    return "hub", None


# Reward logging (best-effort)
try:
    from core.reward import RewardLogger  # type: ignore
except Exception:
    RewardLogger = None  # type: ignore

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:
    cv2 = None
    np = None


def _get_reward_logger():
    """Lazy singleton RewardLogger (best-effort)."""
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


_RLOG = None


def _policy_state_hash(pan: int, tilt: int, zoom: int, pan_min: int, pan_max: int, tilt_min: int, tilt_max: int) -> str:
    """Compute a compact PTZ state hash for policy_rules.

    Design goals:
      - deterministic and cheap (no heavy hashing)
      - stable across reboots (purely numeric)
      - bins align with the *soft-limited* range whenever possible

    ENV:
      OROMA_PTZ_POLICY_BINS_X   (Default 13)
      OROMA_PTZ_POLICY_BINS_Y   (Default 7)
      OROMA_PTZ_POLICY_ZOOM_BINS(Default 6)

    Output format:
      "p{bx}/t{by}/z{zb}"  (e.g. "p6/t3/z0")
    """
    bins_x = _env_int("OROMA_PTZ_POLICY_BINS_X", 13)
    bins_y = _env_int("OROMA_PTZ_POLICY_BINS_Y", 7)
    zbins = _env_int("OROMA_PTZ_POLICY_ZOOM_BINS", 6)

    def clamp(v: int, lo: int, hi: int) -> int:
        try:
            if v < lo:
                return lo
            if v > hi:
                return hi
            return v
        except Exception:
            return v

    # Defensive: avoid div-by-zero
    pr = max(1, int(pan_max) - int(pan_min))
    tr = max(1, int(tilt_max) - int(tilt_min))

    pan_c = clamp(int(pan), int(pan_min), int(pan_max))
    tilt_c = clamp(int(tilt), int(tilt_min), int(tilt_max))
    # zoom: use coarse bins based on 100..150 default; if not, still works.
    try:
        zmin = int(os.environ.get("OROMA_PTZ_ZOOM_MIN", "100"))
        zmax = int(os.environ.get("OROMA_PTZ_ZOOM_MAX", "150"))
    except Exception:
        zmin, zmax = 100, 150
    zr = max(1, zmax - zmin)
    zoom_c = clamp(int(zoom), zmin, zmax)

    bx = int((float(pan_c - int(pan_min)) / float(pr)) * float(max(1, bins_x)))
    by = int((float(tilt_c - int(tilt_min)) / float(tr)) * float(max(1, bins_y)))
    bz = int((float(zoom_c - zmin) / float(zr)) * float(max(1, zbins)))

    if bx >= bins_x:
        bx = bins_x - 1
    if by >= bins_y:
        by = bins_y - 1
    if bz >= zbins:
        bz = zbins - 1
    if bx < 0:
        bx = 0
    if by < 0:
        by = 0
    if bz < 0:
        bz = 0

    return f"p{bx}/t{by}/z{bz}"


def _policy_choose_action(state_hash: str) -> Optional[str]:
    """Epsilon-greedy action selection from policy_rules.

    Namespace: 'ptz_att'

    ENV:
      OROMA_PTZ_ATT_POLICY_ENABLE   (Default 1)
      OROMA_PTZ_ATT_POLICY_EPS      (Default 0.20)  # explore probability
      OROMA_PTZ_ATT_POLICY_MIN_N    (Default 3)     # ignore actions with fewer samples

    Returns action string or None.
    """
    if not _env_bool("OROMA_PTZ_ATT_POLICY_ENABLE", True):
        return None
    eps_base = _env_float("OROMA_PTZ_ATT_POLICY_EPS", 0.20)
    eps = float(eps_base)
    # Optional: Curiosity → mehr Exploration (höheres eps)
    # (best-effort; falls Curiosity/DB nicht verfügbar ist, bleibt eps=eps_base)
    if _env_bool("OROMA_PTZ_ATT_CURIOSITY_ENABLE", True):
        cur = _curiosity_norm()
        if cur is not None:
            scale = _env_float("OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_SCALE", 0.25)
            eps_min = _env_float("OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_MIN", 0.05)
            eps_max = _env_float("OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_MAX", 0.85)
            try:
                eps = float(eps_base) + float(cur) * float(scale)
            except Exception:
                eps = float(eps_base)
            eps = max(float(eps_min), min(float(eps_max), float(eps)))
    min_n = _env_int("OROMA_PTZ_ATT_POLICY_MIN_N", 3)
    eps = max(0.0, min(float(eps), 1.0))

    try:
        if random.random() < eps:
            return None  # exploration (fallback to default random logic)
    except Exception:
        pass

    try:
        with get_conn() as conn:
            conn.row_factory = None
            rows = conn.execute(
                """
                SELECT action, n, q
                FROM policy_rules
                WHERE namespace=? AND state_hash=?
                """,
                ("ptz_att", str(state_hash)),
            ).fetchall()
    except Exception:
        return None

    best_a = None
    best_q = None
    best_n = 0
    for r in rows or []:
        try:
            a = str(r[0])
            n = int(r[1])
            q = float(r[2])
        except Exception:
            continue
        if n < min_n:
            continue
        if best_q is None or (q > best_q) or (q == best_q and n > best_n):
            best_a, best_q, best_n = a, q, n
    return best_a


# -----------------------------------------------------------------------------
# Curiosity → PTZ Exploration (light integration)
# -----------------------------------------------------------------------------
#
# Motivation:
#   ORÓMA erzeugt Curiosity-Signale (core/curiosity.py → Tabelle curiosity_log).
#   Für PTZ-Attention ist Curiosity besonders sinnvoll, weil "Exploration" hier
#   unmittelbar physisch sichtbar wird: bei hoher Curiosity wird stärker gescannt
#   (größere Schritte, kürzere Fixation, höheres Policy-ε), bei niedriger Curiosity
#   wird ruhiger fixiert (kleinere Schritte, längere Fixation).
#
# Design-Prinzipien (produktiv / minimal-invasiv):
#   - Best effort: Bei DB-/Schema-Fehlern niemals Crash; nur None zurückgeben.
#   - Cache: DB-Abfrage wird kurz gecached, um Overhead zu vermeiden.
#   - Keine Schema-Änderungen, keine neuen Tabellen.
#
# ENV (optional):
#   OROMA_PTZ_ATT_CURIOSITY_ENABLE             (Default 1)
#   OROMA_PTZ_ATT_CURIOSITY_WINDOW_SEC         (Default 300)
#   OROMA_PTZ_ATT_CURIOSITY_SIGNAL_MAX         (Default 1.0)  # Normalisierung
#   OROMA_PTZ_ATT_CURIOSITY_CACHE_SEC          (Default 2.0)
#
#   OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_SCALE   (Default 0.25)
#   OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_MIN     (Default 0.05)
#   OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_MAX     (Default 0.85)
#
#   OROMA_PTZ_ATT_CURIOSITY_SCAN_STEP_SCALE    (Default 1.00)
#   OROMA_PTZ_ATT_CURIOSITY_SCAN_STEP_MAX      (Default 4)
#   OROMA_PTZ_ATT_CURIOSITY_FIXATE_SHRINK      (Default 0.50)  # 0..1 (nur kürzer)
#

_CURI_CACHE: Dict[str, Any] = {"ts": 0.0, "val": None}


def _curiosity_norm() -> Optional[float]:
    """Return recent curiosity in [0..1] (best effort, cached)."""
    if not _env_bool("OROMA_PTZ_ATT_CURIOSITY_ENABLE", True):
        return None

    now = time.time()
    cache_sec = _env_float("OROMA_PTZ_ATT_CURIOSITY_CACHE_SEC", 2.0)
    try:
        ts = float(_CURI_CACHE.get("ts") or 0.0)
        if (now - ts) <= float(cache_sec):
            v = _CURI_CACHE.get("val")
            return None if v is None else float(v)
    except Exception:
        pass

    window_sec = _env_int("OROMA_PTZ_ATT_CURIOSITY_WINDOW_SEC", 300)
    sig_max = _env_float("OROMA_PTZ_ATT_CURIOSITY_SIGNAL_MAX", 1.0)
    if sig_max <= 0:
        sig_max = 1.0

    val: Optional[float] = None
    try:
        with get_conn() as conn:
            conn.row_factory = None
            since = int(now) - int(max(5, window_sec))
            row = conn.execute(
                "SELECT AVG(signal) FROM curiosity_log WHERE created_at>=? AND signal IS NOT NULL",
                (int(since),),
            ).fetchone()
            if row and row[0] is not None:
                try:
                    raw = float(row[0])
                    # Normalisieren + clamp
                    val = raw / float(sig_max)
                    if val < 0.0:
                        val = 0.0
                    if val > 1.0:
                        val = 1.0
                except Exception:
                    val = None
    except Exception:
        val = None

    try:
        _CURI_CACHE["ts"] = float(now)
        _CURI_CACHE["val"] = val
    except Exception:
        pass
    return val


# -----------------------------------------------------------------------------
# PTZ Reflex Layer (v3.8) – Audio-Spike & Motion-Centroid (DB-frei)
# -----------------------------------------------------------------------------
# Design: "Lebewesen"-tauglich
#   - Reflex darf NICHT auf DB/Policy/UI warten
#   - Time-Budget pro Tick: kleine, deterministische O(wh) Operationen
#   - Policy/Dream sind *optional* Optimierer (Bandit), niemals Blocker
#
# Diese Helfer sind bewusst klein und robust implementiert:
#   - Motion-Centroid: arbeitet auf 64x36 Grayscale-Bytes
#   - Audio-Spike: EMA Noise-Floor + Hysterese + Cooldown
#   - Policy: separate Namespaces (ptz_motion, ptz_probe) mit kleinem State-Space
# -----------------------------------------------------------------------------

def _policy_choose_action_ns(namespace: str, state_hash: str, env_prefix: str) -> Optional[str]:
    """Namespace-Variante von _policy_choose_action().

    env_prefix examples:
      - OROMA_PTZ_MOTION_POLICY
      - OROMA_PTZ_PROBE_POLICY

    Expected env vars:
      {env_prefix}_ENABLE (Default 1)
      {env_prefix}_EPS    (Default 0.20)
      {env_prefix}_MIN_N  (Default 3)
    """
    if not _env_bool(f"{env_prefix}_ENABLE", True):
        return None
    eps = _env_float(f"{env_prefix}_EPS", 0.20)
    min_n = _env_int(f"{env_prefix}_MIN_N", 3)
    eps = max(0.0, min(float(eps), 1.0))

    try:
        if random.random() < eps:
            return None
    except Exception:
        return None

    try:
        with get_conn() as conn:
            conn.row_factory = None
            rows = conn.execute(
                """
                SELECT action, n, q
                FROM policy_rules
                WHERE namespace=? AND state_hash=?
                """,
                (str(namespace), str(state_hash)),
            ).fetchall()
    except Exception:
        return None

    best_a = None
    best_q = None
    best_n = 0
    for r in rows or []:
        try:
            a = str(r[0])
            n = int(r[1])
            q = float(r[2])
        except Exception:
            continue
        if n < min_n:
            continue
        if best_q is None or (q > best_q) or (q == best_q and n > best_n):
            best_a, best_q, best_n = a, q, n
    return best_a


def _edge_flags(pan: int, tilt: int, pan_min: int, pan_max: int, tilt_min: int, tilt_max: int) -> int:
    """Return a compact 4-bit edge flag mask.

    Bits:
      1: pan near min
      2: pan near max
      4: tilt near min
      8: tilt near max
    """
    try:
        margin = int(_env_int("OROMA_PTZ_EDGE_MARGIN", 7200))  # ~=2 steps default
    except Exception:
        margin = 7200
    f = 0
    try:
        if int(pan) <= int(pan_min) + margin:
            f |= 1
        if int(pan) >= int(pan_max) - margin:
            f |= 2
        if int(tilt) <= int(tilt_min) + margin:
            f |= 4
        if int(tilt) >= int(tilt_max) - margin:
            f |= 8
    except Exception:
        pass
    return int(f)


def _motion_centroid(prev: Optional[bytes], cur: Optional[bytes], w: int = 64, h: int = 36) -> Dict[str, float]:
    """Compute motion energy + centroid (normalized) from two small grayscale byte buffers.

    Output:
      {
        'energy': mean_abs_diff/255 in [0..1],
        'dx':    centroid_x in [-1..1] (left..right),
        'dy':    centroid_y in [-1..1] (up..down),
        'dist':  sqrt(dx^2 + dy^2) in [0..~1.4]
      }

    Notes:
      - Pure Python (works even if numpy is missing).
      - w*h is small (default 64*36=2304), so O(n) is cheap on Pi.
    """
    if prev is None or cur is None:
        return {"energy": 0.0, "dx": 0.0, "dy": 0.0, "dist": 0.0}
    try:
        if len(prev) != len(cur) or len(cur) != int(w) * int(h):
            return {"energy": 0.0, "dx": 0.0, "dy": 0.0, "dist": 0.0}
        tot = 0.0
        wx = 0.0
        wy = 0.0
        n = len(cur)
        for i in range(n):
            d = int(cur[i]) - int(prev[i])
            if d < 0:
                d = -d
            if d == 0:
                continue
            tot += float(d)
            x = i % int(w)
            y = i // int(w)
            wx += float(d) * float(x)
            wy += float(d) * float(y)
        energy = float(tot / float(max(1.0, float(n) * 255.0)))
        if tot <= 0.0:
            return {"energy": energy, "dx": 0.0, "dy": 0.0, "dist": 0.0}
        cx = wx / tot
        cy = wy / tot
        # normalize centroid to [-1..1]
        dx = (float(cx) / float(max(1.0, float(w - 1))) - 0.5) * 2.0
        dy = (float(cy) / float(max(1.0, float(h - 1))) - 0.5) * 2.0
        dist = float(math.sqrt(dx * dx + dy * dy))
        return {"energy": energy, "dx": dx, "dy": dy, "dist": dist}
    except Exception:
        return {"energy": 0.0, "dx": 0.0, "dy": 0.0, "dist": 0.0}


def _bin3(v: float, dead: float = 0.15) -> int:
    """Quantize to 3 bins: 0=neg,1=center,2=pos."""
    try:
        if v <= -abs(float(dead)):
            return 0
        if v >= abs(float(dead)):
            return 2
        return 1
    except Exception:
        return 1


def _energy_bin(energy: float, thr: float) -> int:
    """Quantize energy into 2 bins (0/1)."""
    try:
        return 1 if float(energy) >= float(thr) else 0
    except Exception:
        return 0


def _state_hash_motion(dx_bin: int, dy_bin: int, e_bin: int, flags: int) -> str:
    return f"dx{int(dx_bin)}/dy{int(dy_bin)}/e{int(e_bin)}/f{int(flags)}"


def _state_hash_probe(phase: int, e_bin: int, flags: int) -> str:
    return f"ph{int(phase)}/e{int(e_bin)}/f{int(flags)}"


def _opposite_ptz_dir(a: str, b: str) -> bool:
    """Return True if two PTZ directions are direct opposites.

    Production note:
      This is intentionally tiny and dependency-free because it is used inside
      the PTZ reflex path. It prevents the classic self-motion oscillation where
      a pan move creates optical flow that immediately triggers the opposite pan
      move in the next orchestrator tick.
    """
    try:
        return (str(a), str(b)) in (("left", "right"), ("right", "left"), ("up", "down"), ("down", "up"))
    except Exception:
        return False


def _ptz_dir_axis(a: str) -> str:
    """Return 'x' for pan, 'y' for tilt, '' for non-directional actions."""
    try:
        if str(a) in ("left", "right"):
            return "x"
        if str(a) in ("up", "down"):
            return "y"
    except Exception:
        pass
    return ""


def _ptz_dir_strength(a: str, dx: float, dy: float, vertical_gain: float = 1.0) -> float:
    """Return axis strength used for reversal/hysteresis decisions."""
    try:
        if str(a) in ("left", "right"):
            return abs(float(dx))
        if str(a) in ("up", "down"):
            return abs(float(dy)) * max(0.01, float(vertical_gain))
    except Exception:
        pass
    return 0.0


def _dominant_motion_axis(dx: float, dy: float, vertical_gain: float = 1.0) -> str:
    """Choose dominant motion axis with a configurable tilt compensation."""
    try:
        x = abs(float(dx))
        y = abs(float(dy)) * max(0.01, float(vertical_gain))
        return "y" if y > x else "x"
    except Exception:
        return "x"


def _direction_from_axis(axis: str, dx: float, dy: float) -> str:
    """Map a dominant axis and centroid sign to a raw PTZ follow action.

    Important: this function returns the mathematically expected direction from
    image-space motion only. Some camera/control combinations expose pan signs
    opposite to the optical follow convention. The production follow path must
    therefore call _map_follow_action() before sending a PTZ command.
    """
    try:
        if str(axis) == "y":
            return "up" if float(dy) < 0.0 else "down"
        return "left" if float(dx) < 0.0 else "right"
    except Exception:
        return ""


def _map_follow_action(action: str, *, invert_x: bool = False, invert_y: bool = False) -> str:
    """Map raw image-space follow action to the physical PTZ command.

    Live-Befund 2026-05-07:
      Die EMEET-PIXY/V4L2-Kombination reagierte im Follow-Test horizontal
      gespiegelt: Bewegung links im Bild führte mit der bisherigen Raw-Aktion
      sichtbar in die Gegenrichtung. Darum wird nur der Threat-Follow-Pfad
      optisch gemappt; PTZController, UI-Buttons, Scan und absolute Positionen
      bleiben unverändert.
    """
    a = str(action or "").strip().lower()
    if bool(invert_x):
        if a == "left":
            return "right"
        if a == "right":
            return "left"
    if bool(invert_y):
        if a == "up":
            return "down"
        if a == "down":
            return "up"
    return a


def _calc_dynamic_threat_amount(
    *,
    base_steps: int,
    min_steps: int,
    max_steps: int,
    dx: float,
    dy: float,
    dist: float,
    energy: float,
    deadzone: float,
    vertical_gain: float,
    dist_scale: float,
    energy_scale: float,
) -> int:
    """Return a visible but bounded PTZ amount for threat-follow nudges.

    Hintergrund:
      Der Orchestrator ruft den PTZ-Loop typischerweise nur alle mehrere
      Sekunden als --once-Prozess auf. Ein fester 3-Step-Nudge war in den
      Live-Tests zwar erfolgreich (cmd_ok=1), optisch aber zu klein, um eine
      Person sichtbar nachzuführen. Diese Funktion erhöht die Schrittgröße
      anhand von Motion-Abstand und Motion-Energie, bleibt aber hart begrenzt,
      damit die Kamera nicht überschießt oder in Anschläge fährt.

    Design:
      - min_steps ist der produktive sichtbare Default für echte Threat-Moves.
      - max_steps begrenzt aggressive Bewegungen.
      - dist/energy werden normiert; der stärkere Score gewinnt.
      - vertical_gain fließt in die Achsstärke ein, damit Tilt-Signale nicht
        systematisch gegen Pan verlieren.
    """
    try:
        base_i = max(1, int(base_steps))
        lo = max(1, int(min_steps))
        hi = max(lo, int(max_steps))
        axis_strength = max(abs(float(dx)), abs(float(dy)) * max(0.01, float(vertical_gain)))
        d = max(float(dist), float(axis_strength))
        e = max(0.0, float(energy))
        dz = max(0.0, float(deadzone))
        ds = max(0.001, float(dist_scale))
        es = max(0.001, float(energy_scale))
        dist_score = max(0.0, min(1.0, (d - dz) / ds))
        energy_score = max(0.0, min(1.0, e / es))
        score = max(dist_score, energy_score)
        amt = int(round(float(lo) + (float(hi - lo) * score)))
        return max(base_i, max(lo, min(hi, amt)))
    except Exception:
        try:
            return max(1, int(base_steps))
        except Exception:
            return 1


def _audio_update(st: Dict[str, Any], audio_level: float, now_ts: int) -> Dict[str, float]:
    """Update EMA noise-floor and compute spike.

    Stored state keys:
      audio_noise: EMA baseline
      audio_last_ts: last update ts
    """
    alpha = _env_float("OROMA_PTZ_AUDIO_EMA_ALPHA", 0.02)
    alpha = max(0.001, min(float(alpha), 0.25))

    noise = float(st.get("audio_noise") or 0.0)
    if noise <= 0.0:
        noise = float(audio_level)

    # time-delta guarded EMA (avoid giant jumps after long pauses)
    last_ts = int(st.get("audio_last_ts") or now_ts)
    dt = max(0, int(now_ts) - int(last_ts))
    # if dt very large, fast-sync once
    if dt > 60:
        noise = float(audio_level)
    else:
        noise = (1.0 - alpha) * float(noise) + alpha * float(audio_level)

    st["audio_noise"] = float(noise)
    st["audio_last_ts"] = int(now_ts)

    spike = float(audio_level) - float(noise)
    if spike < 0.0:
        spike = 0.0

    return {"noise": float(noise), "spike": float(spike)}

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return default


def _state_path(base: str) -> str:
    return os.environ.get(
        "OROMA_PTZ_ATTENTION_STATE_PATH",
        os.path.join(base, "data", "state", "ptz_attention_state.json"),
    )


def _load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f)
            return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _save_state(path: str, st: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        pass


def _downsample_gray(frame: "object", w: int = 64, h: int = 36) -> Optional[bytes]:
    """Return small grayscale bytes for motion estimation (very cheap)."""
    if cv2 is None:
        return None
    try:
        # assume frame is numpy array BGR
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA)
        return small.tobytes()
    except Exception:
        return None


def _motion_norm(prev: Optional[bytes], cur: Optional[bytes]) -> float:
    if prev is None or cur is None or np is None:
        return 0.0
    try:
        a = np.frombuffer(prev, dtype=np.uint8).astype(np.int16)
        b = np.frombuffer(cur, dtype=np.uint8).astype(np.int16)
        if a.shape != b.shape:
            return 0.0
        mad = np.mean(np.abs(a - b))
        return float(mad) / 255.0
    except Exception:
        return 0.0


def _luma_norm(cur: Optional[bytes]) -> float:
    """Return normalized mean luminance from a small grayscale buffer.

    - cur is expected to be the output of _downsample_gray() (uint8 bytes).
    - Returns a value in [0..1].

    IMPORTANT (perf):
      Uses numpy if available (fast), otherwise falls back to pure Python.
      Buffer sizes are tiny (64*36 by default), so even pure Python is cheap.
    """
    if cur is None:
        return 0.0
    try:
        if np is not None:
            a = np.frombuffer(cur, dtype=np.uint8)
            if a.size <= 0:
                return 0.0
            return float(a.mean()) / 255.0
    except Exception:
        pass

    # pure python fallback
    try:
        if not cur:
            return 0.0
        sm = 0
        for b in cur:
            sm += int(b)
        return float(sm) / float(len(cur) * 255.0)
    except Exception:
        return 0.0


def _luma_update(st: Dict[str, Any], luma_level: float, now_ts: int) -> Dict[str, float]:
    """Update EMA for luminance and store it in state.

    Design goals:
      - DB-free (statefile only)
      - robust under restarts
      - stable even with AE/Gain oscillations

    ENV:
      OROMA_PTZ_LUMA_EMA_ALPHA (Default 0.02)

    State keys:
      - luma_ema
      - luma_last_ts
    """
    try:
        alpha = float(_env_float('OROMA_PTZ_LUMA_EMA_ALPHA', 0.02))
    except Exception:
        alpha = 0.02
    if alpha < 0.001:
        alpha = 0.001
    if alpha > 0.25:
        alpha = 0.25

    try:
        ema = float(st.get('luma_ema') or 0.0)
    except Exception:
        ema = 0.0

    # first sample: initialize more directly
    if ema <= 0.0:
        ema = float(luma_level)
    else:
        ema = (1.0 - alpha) * float(ema) + alpha * float(luma_level)

    st['luma_ema'] = float(ema)
    st['luma_last_ts'] = int(now_ts)
    return {'luma': float(luma_level), 'ema': float(ema)}


def _sharpness(frame: "object") -> float:
    if cv2 is None:
        return 0.0
    try:
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(g, cv2.CV_64F)
        return float(lap.var())
    except Exception:
        return 0.0


def _att_score(motion_norm: float, sharp_var: float) -> float:
    """Small attention score used for 'attention gain' reward.

    ENV:
      OROMA_PTZ_ATT_W_MOTION   (Default 1.0)
      OROMA_PTZ_ATT_W_SHARP    (Default 0.2)
      OROMA_PTZ_ATT_SHARP_DIV  (Default 10.0)
    """
    w_m = _env_float("OROMA_PTZ_ATT_W_MOTION", 1.0)
    w_s = _env_float("OROMA_PTZ_ATT_W_SHARP", 0.2)
    div = max(0.01, _env_float("OROMA_PTZ_ATT_SHARP_DIV", 10.0))

    try:
        sharp_n = float(math.log1p(max(0.0, float(sharp_var))) / div)
    except Exception:
        sharp_n = 0.0
    if sharp_n < 0.0:
        sharp_n = 0.0
    if sharp_n > 1.0:
        sharp_n = 1.0
    return float(w_m * float(motion_norm) + w_s * sharp_n)


def _att_pair(frame1: "object", frame2: "object") -> Dict[str, float]:
    """Compute motion/sharpness/score from two frames (same viewpoint window)."""
    s1 = _downsample_gray(frame1)
    s2 = _downsample_gray(frame2)
    motion = _motion_norm(s1, s2)
    sharp = _sharpness(frame2)
    score = _att_score(motion, sharp)
    return {"motion": float(motion), "sharp": float(sharp), "score": float(score)}


def _audio_active(base: str, window_sec: int = 5) -> bool:
    """Very cheap proxy: did we see audio/token snapchains recently?

    Fail-closed: if DB unavailable -> False.
    """
    try:
        db_path = os.path.join(base, "data", "oroma.db")
        conn = get_conn(db_path=db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) FROM snapchains
                WHERE origin='audio/token' AND ts > strftime('%s','now') - ?
                """,
                (int(window_sec),),
            )
            n = cur.fetchone()[0]
            return bool(int(n or 0) > 0)
        finally:
            conn.close()
    except Exception:
        return False


def _pick_scan_target(pan_min: int, pan_max: int, tilt_min: int, tilt_max: int, bins_x: int, bins_y: int, idx: int) -> Tuple[int, int]:
    """Serpentine grid ordering: idx -> (target_pan, target_tilt)."""
    total = max(1, bins_x * bins_y)
    i = idx % total
    y = i // bins_x
    x = i % bins_x
    # serpentine: every other row reversed
    if y % 2 == 1:
        x = (bins_x - 1) - x
    pan = pan_min + int(round((x + 0.5) * (pan_max - pan_min) / bins_x))
    tilt = tilt_min + int(round((y + 0.5) * (tilt_max - tilt_min) / bins_y))
    return (pan, tilt)


def _nudge_towards(cur: int, target: int, deadband: int = 0) -> Optional[str]:
    if abs(target - cur) <= deadband:
        return None
    return "right" if target > cur else "left"


def _nudge_towards_tilt(cur: int, target: int, deadband: int = 0) -> Optional[str]:
    """Return PTZ tilt nudge direction towards target.

    IMPORTANT (production / sign convention)
    ----------------------------------------
    In ORÓMA we treat V4L2 tilt_absolute with the *conventional* mapping that is
    also used by wrappers/ptz_controller.PTZController:

        - 'up'   increases tilt_absolute
        - 'down' decreases tilt_absolute

    Some UVC devices may implement inverted signs internally, but PTZController
    intentionally normalizes to this convention.

    The previous implementation in this module returned the *opposite* direction
    (target>cur → 'down'), which can drive the camera into the ceiling/floor and
    then keep it pinned at the limit during SCAN (because the corrective
    direction was inverted).

    This fix is minimal-invasive and only corrects the direction mapping.
    """
    if abs(target - cur) <= deadband:
        return None
    return "up" if target > cur else "down"


def _log_ptz_status_metrics(action: str, ptz: Dict[str, Any]) -> None:
    """Write ptz:pan/tilt/zoom (+ ptz:cmd:*). Ensures same ts for join."""
    try:
        if not ptz.get("supported"):
            return
        controls = ptz.get("controls") or {}
        now_ts = int(time.time())

        def _val(k: str) -> Optional[float]:
            c = controls.get(k)
            if isinstance(c, dict) and "value" in c:
                try:
                    return float(c.get("value"))
                except Exception:
                    return None
            return None

        pan = _val("pan_absolute")
        tilt = _val("tilt_absolute")
        zoom = _val("zoom_absolute")

        if pan is not None:
            insert_metric("ptz:pan", pan, ts=now_ts)
        if tilt is not None:
            insert_metric("ptz:tilt", tilt, ts=now_ts)
        if zoom is not None:
            insert_metric("ptz:zoom", zoom, ts=now_ts)
        if action:
            insert_metric(f"ptz:cmd:{action}", 1.0, ts=now_ts)
    except Exception:
        pass


def run_once(verbose: bool = False, once_mode: bool = False) -> Dict[str, Any]:
    logger = logging.getLogger("ptz_attention_loop")
    if verbose:
        try:
            logger.setLevel(logging.DEBUG)
        except Exception:
            pass

    base = os.getenv("OROMA_BASE", "/opt/ai/oroma")
    dry_run = _env_bool("OROMA_PTZ_ATTENTION_DRY_RUN", False)
    ensure_cam = _env_bool("OROMA_PTZ_ATTENTION_ENSURE_CAM", False)

    # Fast-Path fuer Orchestrator/--once: lieber frueh und sauber zurueckkehren
    # als durch Snapshot-Fetches, Sleeps und Post-Measurements in den 25s-Timeout
    # zu laufen.
    once_fast = bool(once_mode and _env_bool("OROMA_PTZ_ATTENTION_ONCE_FASTPATH", True))
    once_soft_deadline_sec = float(_env_float("OROMA_PTZ_ATTENTION_ONCE_SOFT_DEADLINE_SEC", 18.0 if once_fast else 0.0))
    once_skip_attention_gain = bool(once_fast and _env_bool("OROMA_PTZ_ATTENTION_ONCE_SKIP_ATTENTION_GAIN", True))
    once_snapshot_first = bool(once_fast and _env_bool("OROMA_PTZ_ATTENTION_ONCE_SNAPSHOT_FIRST", True))
    # ORCHESTRATOR NOTE: In --once runs (separate process) prefer UI snapshot first to avoid
    # attempting to open the camera device from this worker process.

    once_allow_ensure_cam = bool(once_fast and _env_bool("OROMA_PTZ_ATTENTION_ONCE_ALLOW_ENSURE_CAM", False))
    run_started_monotonic = time.monotonic()
    deadline_monotonic = (run_started_monotonic + max(1.0, once_soft_deadline_sec)) if once_fast and once_soft_deadline_sec > 0 else None

    def _deadline_hit() -> bool:
        return deadline_monotonic is not None and time.monotonic() >= float(deadline_monotonic)

    def _remaining_deadline() -> float:
        if deadline_monotonic is None:
            return 1e9
        return max(0.0, float(deadline_monotonic) - time.monotonic())

    motion_low = _env_float("OROMA_PTZ_ATTENTION_MOTION_LOW", 0.008)
    motion_high = _env_float("OROMA_PTZ_ATTENTION_MOTION_HIGH", 0.022)
    bored_sec = _env_int("OROMA_PTZ_ATTENTION_BORED_SEC", 90)
    orient_cd = _env_int("OROMA_PTZ_ATTENTION_ORIENT_COOLDOWN", 4)

    # Fine motion grid for close-range following (e.g. hand/body motion at ~1 m).
    motion_w = max(48, min(192, _env_int("OROMA_PTZ_ATTENTION_MOTION_W", 96)))
    motion_h = max(27, min(108, _env_int("OROMA_PTZ_ATTENTION_MOTION_H", 54)))

    # Reflex Layer: Motion (Threat) + Audio-Spike (Probe)
    threat_high = _env_float("OROMA_PTZ_THREAT_HIGH", float(motion_high))
    threat_hold_sec = _env_int("OROMA_PTZ_THREAT_HOLD_SEC", 4)
    threat_deadzone = _env_float("OROMA_PTZ_THREAT_DEADZONE", 0.09)  # centroid deadzone
    threat_deadzone_min = _env_float("OROMA_PTZ_THREAT_DEADZONE_MIN", 0.040)
    threat_deadzone_e_scale = _env_float("OROMA_PTZ_THREAT_DEADZONE_E_SCALE", 1.80)
    threat_force_dist = _env_float("OROMA_PTZ_THREAT_FORCE_DIST", 0.045)
    threat_force_energy = _env_float("OROMA_PTZ_THREAT_FORCE_ENERGY", 0.006)
    threat_force_axis_min = _env_float("OROMA_PTZ_THREAT_FORCE_AXIS_MIN", 0.035)
    threat_sticky_dir_sec = _env_int("OROMA_PTZ_THREAT_STICKY_DIR_SEC", 2)
    threat_sticky_energy = _env_float("OROMA_PTZ_THREAT_STICKY_ENERGY", 0.010)

    # Cat-like follow tuning: weniger "Eule/Fixate", mehr kleine, haeufige Nachfuehrung.
    # Default aktiv, damit erkannter Threat schneller in echte PTZ-Korrekturen uebersetzt wird.
    cat_follow_enable = _env_bool("OROMA_PTZ_CAT_FOLLOW_ENABLE", True)
    cat_deadzone_scale = _env_float("OROMA_PTZ_CAT_DEADZONE_SCALE", 0.65)
    cat_deadzone_min = _env_float("OROMA_PTZ_CAT_DEADZONE_MIN", 0.020)
    cat_force_energy = _env_float("OROMA_PTZ_CAT_FORCE_ENERGY", 0.003)
    cat_force_dist = _env_float("OROMA_PTZ_CAT_FORCE_DIST", 0.020)
    cat_force_axis_min = _env_float("OROMA_PTZ_CAT_FORCE_AXIS_MIN", 0.020)
    cat_scan_fix_min = _env_int("OROMA_PTZ_CAT_SCAN_FIXATE_SEC_MIN", 0)
    cat_scan_fix_max = _env_int("OROMA_PTZ_CAT_SCAN_FIXATE_SEC_MAX", 1)
    cat_threat_fixate_sec = _env_int("OROMA_PTZ_CAT_THREAT_FIXATE_SEC", 0)

    # Threat Burst:
    # Im Orchestrator wird dieses Modul als --once gestartet. Ein einzelner 1-Step-Nudge
    # alle mehrere Sekunden ist für Personenverfolgung zu träge. Deshalb darf Threat
    # einen kleinen Mehrfach-Nudge als *ein* Backend-Kommando senden. Das respektiert
    # PTZController-Cooldown besser als mehrere schnelle Einzelkommandos und bleibt
    # über OROMA_PTZ_THREAT_BURST_* vollständig abschalt-/begrenzbar.
    threat_burst_enable = _env_bool("OROMA_PTZ_THREAT_BURST_ENABLE", True)
    threat_burst_steps = max(1, min(8, _env_int("OROMA_PTZ_THREAT_BURST_STEPS", 3)))
    threat_amount_dynamic = _env_bool("OROMA_PTZ_THREAT_AMOUNT_DYNAMIC", True)
    threat_amount_min = max(1, min(12, _env_int("OROMA_PTZ_THREAT_AMOUNT_MIN", 5)))
    threat_amount_max = max(threat_amount_min, min(16, _env_int("OROMA_PTZ_THREAT_AMOUNT_MAX", 8)))
    threat_amount_dist_scale = max(0.01, min(1.00, _env_float("OROMA_PTZ_THREAT_AMOUNT_DIST_SCALE", 0.22)))
    threat_amount_e_scale = max(0.001, min(0.250, _env_float("OROMA_PTZ_THREAT_AMOUNT_E_SCALE", 0.035)))

    # Threat Stabilization / Anti-Pendel:
    # Nach einem erfolgreichen PTZ-Move erzeugt die Kamera selbst fuer kurze Zeit
    # starke Bilddifferenzen. Diese Self-Motion darf nicht sofort wieder als
    # Zielbewegung interpretiert werden, sonst entsteht Links/Rechts-Pendelung.
    # Zudem wird die Tilt-Achse leicht gewichtet, weil Pan in realen Innenraum-
    # Szenen durch Perspektive, Personenbreite und Kameraschwenk sonst fast immer
    # dominiert und up/down kaum erreichbar ist.
    self_motion_guard_sec = max(0.0, min(5.0, _env_float("OROMA_PTZ_SELF_MOTION_GUARD_SEC", 1.20)))
    reversal_guard_sec = max(0.0, min(30.0, _env_float("OROMA_PTZ_THREAT_REVERSAL_GUARD_SEC", 10.0)))
    reversal_ratio = max(1.0, min(5.0, _env_float("OROMA_PTZ_THREAT_REVERSAL_RATIO", 1.65)))
    vertical_gain = max(0.50, min(3.00, _env_float("OROMA_PTZ_THREAT_VERTICAL_GAIN", 1.35)))
    # Follow-only optical mapping. Default X inversion matches the 2026-05-07
    # EMEET PIXY live observation: raw image-space left/right had to be mirrored
    # for physical PTZ follow to move toward the observed person.
    follow_invert_x = _env_bool("OROMA_PTZ_FOLLOW_INVERT_X", True)
    follow_invert_y = _env_bool("OROMA_PTZ_FOLLOW_INVERT_Y", False)

    # Audio Spike → Probe (DB-frei)
    audio_enable = _env_bool("OROMA_PTZ_AUDIO_ENABLE", True)
    audio_abs_min = _env_float("OROMA_PTZ_AUDIO_ABS_MIN", 0.18)
    audio_spike_hi = _env_float("OROMA_PTZ_AUDIO_SPIKE_HI", 0.10)
    audio_probe_cd = _env_int("OROMA_PTZ_AUDIO_PROBE_COOLDOWN_SEC", 10)
    probe_steps = _env_int("OROMA_PTZ_PROBE_STEPS", 4)
    probe_settle_ms = _env_int("OROMA_PTZ_PROBE_SETTLE_MS", 250)

    # Speech-Guard (fixate while someone is talking) – DB-frei via Noise-Floor

    # Luma-Recovery (DB-frei, tick-basiert): wenn Bild dauerhaft extrem dunkel/hell ist
    # -> kleine Korrektur (meist Tilt Richtung Mitte), um 'blind pinned' zu vermeiden
    luma_recover_enable = _env_bool("OROMA_PTZ_LUMA_RECOVER_ENABLE", True)
    luma_low = _env_float("OROMA_PTZ_LUMA_LOW", 0.12)
    luma_high = _env_float("OROMA_PTZ_LUMA_HIGH", 0.85)
    luma_hyst = _env_float("OROMA_PTZ_LUMA_HYST", 0.03)
    luma_hold_sec = _env_int("OROMA_PTZ_LUMA_HOLD_SEC", 2)
    luma_steps = _env_int("OROMA_PTZ_LUMA_RECOVER_STEPS", 2)
    luma_cd_sec = _env_int("OROMA_PTZ_LUMA_RECOVER_COOLDOWN_SEC", 8)
    # Zusätzliche Daempfung gegen wiederholte luma_recover-Bursts:
    # - Re-Arm blockiert den naechsten Neu-Trigger fuer eine Weile
    # - Burst-Window begrenzt, wie viele Recover-Bursts in kurzer Zeit erlaubt sind
    #   bevor der Loop wieder in normale Scan/Fixate-Entscheidungen zurueckfaellt.
    luma_rearm_sec = _env_int("OROMA_PTZ_LUMA_RECOVER_REARM_SEC", 30)
    luma_window_sec = _env_int("OROMA_PTZ_LUMA_RECOVER_WINDOW_SEC", 180)
    luma_max_bursts_per_window = _env_int("OROMA_PTZ_LUMA_RECOVER_MAX_BURSTS_PER_WINDOW", 3)

    audio_guard = _env_bool("OROMA_PTZ_ATTENTION_AUDIO_GUARD", True)
    speech_abs_min = _env_float("OROMA_PTZ_SPEECH_ABS_MIN", 0.22)
    speech_margin = _env_float("OROMA_PTZ_SPEECH_MARGIN", 0.06)

    # ORIENT threshold (keep legacy behavior but avoid 'threat' stealing all)
    orient_thr = _env_float("OROMA_PTZ_ATTENTION_ORIENT_THR", max(float(motion_low) * 2.0, float(motion_high) * 0.60))

    fix_min = _env_int("OROMA_PTZ_ATTENTION_FIXATE_SEC_MIN", 2)
    fix_max = _env_int("OROMA_PTZ_ATTENTION_FIXATE_SEC_MAX", 4)

    scan_bins_x = _env_int("OROMA_PTZ_ATTENTION_SCAN_BINS_X", 7)
    scan_bins_y = _env_int("OROMA_PTZ_ATTENTION_SCAN_BINS_Y", 4)
    scan_step = _env_int("OROMA_PTZ_ATTENTION_SCAN_STEP", 1)

    # Curiosity → Scan/Fixation (light): bei hoher Curiosity stärker scannen,
    # bei niedriger Curiosity ruhiger fixieren.
    cur_norm = _curiosity_norm()
    if cur_norm is not None:
        try:
            step_scale = _env_float("OROMA_PTZ_ATT_CURIOSITY_SCAN_STEP_SCALE", 1.00)
            step_max = _env_int("OROMA_PTZ_ATT_CURIOSITY_SCAN_STEP_MAX", 4)
            # nur erhöhen (niemals kleiner als Basis)
            scan_step_eff = int(round(float(scan_step) * (1.0 + float(cur_norm) * float(step_scale))))
            scan_step = max(int(scan_step), min(int(step_max), int(scan_step_eff)))

            shrink = _env_float("OROMA_PTZ_ATT_CURIOSITY_FIXATE_SHRINK", 0.50)
            shrink = max(0.0, min(1.0, float(shrink)))
            # nur verkürzen (nie verlängern)
            fmin_eff = int(round(float(fix_min) * (1.0 - float(cur_norm) * float(shrink))))
            fmax_eff = int(round(float(fix_max) * (1.0 - float(cur_norm) * float(shrink))))
            fix_min = max(2, min(int(fix_min), int(fmin_eff)))
            fix_max = max(int(fix_min), min(int(fix_max), int(fmax_eff)))

            logger.info(
                "[Curiosity] PTZ adapt: cur=%.3f → scan_step=%d, fix=[%ds..%ds]",
                float(cur_norm), int(scan_step), int(fix_min), int(fix_max),
            )
        except Exception:
            pass

    # Scan Tilt Soft-Band (stabilisiert gegen 'unten schwarz' / 'oben Decke')
    scan_tilt_soft_min_frac = _env_float("OROMA_PTZ_SCAN_TILT_SOFT_MIN_FRAC", 0.20)
    scan_tilt_soft_max_frac = _env_float("OROMA_PTZ_SCAN_TILT_SOFT_MAX_FRAC", 0.12)
    scan_tilt_soft_dark_boost = _env_float("OROMA_PTZ_SCAN_TILT_SOFT_DARK_BOOST", 0.15)

    zoom_en = _env_bool("OROMA_PTZ_ATTENTION_ZOOM_ENABLE", False)
    zoom_step = _env_int("OROMA_PTZ_ATTENTION_ZOOM_STEP", 1)

    st_path = _state_path(base)
    st = _load_state(st_path)

    # PTZ backend (direct-v4l2 preferred; hub fallback)
    ptz_backend_name, direct_ptz = _resolve_ptz_backend(logger)

    hub = None
    if ptz_backend_name == "hub":
        hub = get_hub()

    # Audio-Hub: fuer Reflexe immer DB-frei (in-memory). Wir erzwingen hier
    # KEIN Kamera-Start. Falls der PTZ-Backend bereits 'hub' ist, reuse.
    hub_audio = hub
    if hub_audio is None:
        try:
            hub_audio = get_hub()
        except Exception:
            hub_audio = None

    def _ptz_status():
        if direct_ptz is not None:
            return direct_ptz.ptz_status()
        assert hub is not None
        return hub.ptz_status()

    def _ptz_command(action: str, amount: int = 1):
        if direct_ptz is not None:
            return direct_ptz.ptz_command(action, amount=amount)
        assert hub is not None
        return hub.ptz_command(action, amount=amount)

    # ------------------------------------------------------------------
    # PTZ command execution with explicit success telemetry
    # ------------------------------------------------------------------
    # Hintergrund / Hotpatch 2026-05-07:
    # Der alte Loop setzte moved=True unmittelbar nach _ptz_command(), ohne
    # den Rückgabewert auszuwerten. Dadurch konnten Logs "moved=1" melden,
    # obwohl v4l2-ctl/DeviceHub das Kommando verworfen, rate-limited oder
    # blockiert hatte. Diese Helfer kapseln jeden physischen PTZ-Impuls,
    # speichern Ergebnis/Fehler sichtbar in State+Metrics und liefern nur dann
    # ok=True zurück, wenn der Backend-Rückgabewert wirklich Erfolg signalisiert.
    ptz_cmd_events: List[Dict[str, Any]] = []
    ptz_verify_pose = _env_bool("OROMA_PTZ_CMD_VERIFY_POSE", True)

    def _pose_from_ptz_status(st_in: Any) -> Dict[str, Optional[int]]:
        pose: Dict[str, Optional[int]] = {"pan": None, "tilt": None, "zoom": None}
        try:
            if not isinstance(st_in, dict):
                return pose
            p0 = st_in.get("pose")
            if isinstance(p0, dict):
                for k in ("pan", "tilt", "zoom"):
                    try:
                        if p0.get(k) is not None:
                            pose[k] = int(p0.get(k))
                    except Exception:
                        pass
            controls0 = st_in.get("controls") or {}
            if isinstance(controls0, dict):
                mapping = {"pan": "pan_absolute", "tilt": "tilt_absolute", "zoom": "zoom_absolute"}
                for out_k, ctrl_k in mapping.items():
                    try:
                        c = controls0.get(ctrl_k) or {}
                        if isinstance(c, dict) and c.get("value") is not None:
                            pose[out_k] = int(c.get("value"))
                    except Exception:
                        pass
        except Exception:
            pass
        return pose

    def _ptz_result_ok(res: Any) -> bool:
        try:
            if isinstance(res, bool):
                return bool(res)
            if isinstance(res, dict):
                return bool(res.get("ok"))
            return bool(res)
        except Exception:
            return False

    def _ptz_result_error(res: Any, after_status: Any = None) -> str:
        try:
            if isinstance(res, dict):
                for k in ("error", "err", "reason", "last_error"):
                    v = res.get(k)
                    if v:
                        return str(v)[:500]
                st0 = res.get("status")
                if isinstance(st0, dict):
                    for k in ("last_error", "error", "reason"):
                        v = st0.get(k)
                        if v:
                            return str(v)[:500]
            if isinstance(after_status, dict):
                for k in ("last_error", "error", "reason"):
                    v = after_status.get(k)
                    if v:
                        return str(v)[:500]
        except Exception:
            pass
        return ""

    def _exec_ptz_command(action_in: str, amount: int = 1, reason: str = "") -> Tuple[bool, Dict[str, Any]]:
        a = str(action_in or "").strip().lower()
        try:
            amt = int(amount)
        except Exception:
            amt = 1
        if amt < 1:
            amt = 1

        event: Dict[str, Any] = {
            "ts": int(time.time()),
            "action": a,
            "amount": int(amt),
            "reason": str(reason or ""),
            "backend": str(ptz_backend_name or "unknown"),
            "dry_run": bool(dry_run),
            "ok": False,
            "error": "",
        }

        if not a:
            event["error"] = "empty action"
            ptz_cmd_events.append(event)
            return False, event

        before_status = None
        after_status = None
        if ptz_verify_pose and not dry_run:
            try:
                before_status = _ptz_status()
                event["pose_before"] = _pose_from_ptz_status(before_status)
            except Exception as e:
                event["pose_before_error"] = str(e)[:300]

        t0 = time.monotonic()
        res: Any = None
        try:
            if dry_run:
                res = {"ok": True, "dry_run": True}
            else:
                res = _ptz_command(a, amount=amt)
        except Exception as e:
            res = {"ok": False, "error": repr(e)}

        try:
            event["duration_ms"] = int(round((time.monotonic() - t0) * 1000.0))
        except Exception:
            event["duration_ms"] = 0

        if ptz_verify_pose and not dry_run:
            try:
                after_status = _ptz_status()
                event["pose_after"] = _pose_from_ptz_status(after_status)
            except Exception as e:
                event["pose_after_error"] = str(e)[:300]

        ok = bool(_ptz_result_ok(res))
        err = "" if ok else _ptz_result_error(res, after_status=after_status)
        event["ok"] = bool(ok)
        if err:
            event["error"] = str(err)[:500]

        # Record successful physical PTZ movement centrally for self-motion
        # suppression. This covers threat, scan, probe and recovery equally:
        # any successful pan/tilt move can create optical flow that must not be
        # mistaken for target movement in the next frame.
        if ok and (not dry_run) and a in ("left", "right", "up", "down"):
            try:
                st["last_ptz_move_ts"] = float(time.time())
                st["last_ptz_move_action"] = str(a)
                st["last_ptz_move_reason"] = str(reason or "")
                st["last_ptz_move_amount"] = int(amt)
            except Exception:
                pass

        try:
            if isinstance(res, dict):
                event["result"] = {k: res.get(k) for k in ("ok", "action", "amount", "error", "err", "reason", "retry") if k in res}
            elif isinstance(res, bool):
                event["result"] = {"ok": bool(res)}
            else:
                event["result"] = {"repr": repr(res)[:300]}
        except Exception:
            pass

        ptz_cmd_events.append(event)

        # Metrics: numeric-only, sichtbar im DB/UI-Kontext. Fehler werden nicht
        # still verschluckt; mindestens State+Logger enthalten Details.
        try:
            ts_cmd = int(event.get("ts") or time.time())
            insert_metric("ptz:cmd_attempt", 1.0, ts=ts_cmd)
            insert_metric("ptz:cmd_ok", 1.0 if ok else 0.0, ts=ts_cmd)
            insert_metric("ptz:cmd_fail", 0.0 if ok else 1.0, ts=ts_cmd)
            insert_metric("ptz:cmd_amount", float(amt), ts=ts_cmd)
            if reason:
                # Per-reason Erfolgszähler, z.B. ptz:cmd_ok:threat / ptz:cmd_fail:threat
                safe_reason = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(reason))[:40]
                if safe_reason:
                    insert_metric(f"ptz:cmd_ok:{safe_reason}", 1.0 if ok else 0.0, ts=ts_cmd)
                    insert_metric(f"ptz:cmd_fail:{safe_reason}", 0.0 if ok else 1.0, ts=ts_cmd)
        except Exception:
            pass

        try:
            if ok:
                logger.info(
                    "PTZ command ok: action=%s amount=%s reason=%s backend=%s duration_ms=%s",
                    a, int(amt), str(reason or ""), str(ptz_backend_name or "unknown"), event.get("duration_ms"),
                )
            else:
                logger.warning(
                    "PTZ command failed: action=%s amount=%s reason=%s backend=%s error=%s",
                    a, int(amt), str(reason or ""), str(ptz_backend_name or "unknown"), str(event.get("error") or "unknown"),
                )
        except Exception:
            pass

        return bool(ok), event

    ptz = _ptz_status()
    if not (isinstance(ptz, dict) and ptz.get("supported")):
        return {"ok": True, "moved": False, "mode": "disabled", "reason": (ptz.get("reason") if isinstance(ptz, dict) else "ptz not supported")}

    # Camera freshness
    #
    # IMPORTANT: In Orchestrator-Mode this module often runs as a separate
    # process. It must NOT assume it can open the camera device directly.
    # Therefore we try the in-process camera_hub frame first, then fall back
    # to the UI snapshot endpoint (served by the running ORÓMA service).
    now_ts = int(time.time())
    max_age = float(_env_float("OROMA_PTZ_ATTENTION_MAX_FRAME_AGE", 3.0))
    snap_url = os.getenv("OROMA_PTZ_ATTENTION_SNAPSHOT_URL", "http://127.0.0.1:8080/video/snapshot.jpg")
    snap_timeout = float(_env_float("OROMA_PTZ_ATTENTION_SNAPSHOT_TIMEOUT", 2.0))
    if once_fast:
        max_age = min(float(max_age), float(_env_float("OROMA_PTZ_ATTENTION_ONCE_MAX_FRAME_AGE", 1.5)))
        snap_timeout = min(float(snap_timeout), float(_env_float("OROMA_PTZ_ATTENTION_ONCE_SNAPSHOT_TIMEOUT", 0.5)))
        if not once_allow_ensure_cam:
            ensure_cam = False

    snapshot_first = os.getenv("OROMA_PTZ_ATTENTION_SNAPSHOT_FIRST", "1").strip().lower() in ("1", "true", "yes", "on")
    if once_fast:
        snapshot_first = bool(once_snapshot_first)

    frame_src = "hub"
    frame = None
    ts = None
    frame_diag: Dict[str, Any] = {"source": "unknown"}

    if _deadline_hit():
        st["last_run_ts"] = now_ts
        st["last_reason"] = "timeout_budget_before_frame"
        _save_state(st_path, st)
        return {"ok": True, "ts": int(now_ts), "moved": False, "mode": "idle", "reason": "timeout budget before frame"}

    if snapshot_first and not once_fast:
        ok_s, frame_s, err_s = _fetch_ui_snapshot(snap_url, timeout_sec=snap_timeout)
        if ok_s and frame_s is not None:
            frame = frame_s
            ts = time.time()
            frame_src = "ui_snapshot"

    if once_fast:
        frame, ts, frame_src, frame_diag = get_cached_frame_with_ts_fast_diag()
        st["last_frame_source"] = str(frame_src or "none")
        st["last_frame_diag"] = dict(frame_diag or {})
        if frame is None or not ts:
            st["last_run_ts"] = now_ts
            st["last_reason"] = "no_frame (cached)"
            st["last_motion_prev_available"] = False
            _save_state(st_path, st)
            return {"ok": True, "ts": int(now_ts), "moved": False, "mode": "idle", "reason": "no frame", "frame_source": str(frame_src or "none"), "frame_diag": dict(frame_diag or {}), "motion_prev_available": False}
        age = time.time() - float(ts)
        st["last_frame_age_sec"] = round(float(age), 3)
        if age > max_age:
            st["last_run_ts"] = now_ts
            st["last_reason"] = f"stale_frame age={age:.2f}s"
            st["last_motion_prev_available"] = False
            _save_state(st_path, st)
            return {"ok": True, "ts": int(now_ts), "moved": False, "mode": "idle", "reason": f"stale frame age={age:.2f}s", "frame_source": str(frame_src or "none"), "frame_age_sec": round(float(age), 3), "frame_diag": dict(frame_diag or {}), "motion_prev_available": False}
    else:
        if frame is None and ensure_cam and (not _deadline_hit()) and _remaining_deadline() > 0.75:
            frame, ts = get_frame_with_ts(ensure_start=ensure_cam)

        if (frame is None or not ts) and (not _deadline_hit()):
            ok_s, frame_s, err_s = _fetch_ui_snapshot(snap_url, timeout_sec=snap_timeout)
            if ok_s and frame_s is not None:
                frame = frame_s
                ts = time.time()
                frame_src = "ui_snapshot"
            else:
                st["last_run_ts"] = now_ts
                st["last_reason"] = f"no_frame ({err_s or 'hub'})"
                _save_state(st_path, st)
                return {"ok": True, "ts": int(now_ts), "moved": False, "mode": "idle", "reason": "no frame"}

        age = time.time() - float(ts)
        if age > max_age:
            if once_fast or _deadline_hit():
                st["last_run_ts"] = now_ts
                st["last_reason"] = f"stale_frame age={age:.2f}s"
                _save_state(st_path, st)
                return {"ok": True, "ts": int(now_ts), "moved": False, "mode": "idle", "reason": f"stale frame age={age:.2f}s"}
            ok_s, frame_s, err_s = _fetch_ui_snapshot(snap_url, timeout_sec=snap_timeout)
            if ok_s and frame_s is not None:
                frame = frame_s
                ts = time.time()
                frame_src = "ui_snapshot"
                age = 0.0
            else:
                st["last_run_ts"] = now_ts
                st["last_reason"] = f"stale_frame age={age:.2f}s ({err_s or 'hub'})"
                _save_state(st_path, st)
                return {"ok": True, "ts": int(now_ts), "moved": False, "mode": "idle", "reason": f"stale frame age={age:.2f}s"}

    # ---------------------------------------------------------------------
    # Attention Gain (pre)
    # ---------------------------------------------------------------------
    # Wir messen ein sehr leichtgewichtiges Attention-Score VOR einem Move,
    # basierend auf zwei schnellen Frames im selben Pose-Fenster.
    # Danach messen wir erneut NACH dem Move und loggen:
    #   - metrics: ptz:att_* (pre/post/gain)
    #   - rewards_log: source='ptz/attention_gain'
    #
    # Das ist Pi-freundlich und vermeidet schwere Simulationen.
    pre_pair = None
    frame2 = None
    if (cv2 is not None and np is not None) and (not once_skip_attention_gain) and (not _deadline_hit()):
        try:
            gap_ms = _env_int("OROMA_PTZ_ATT_SAMPLE_GAP_MS", 60)
            gap_ms = max(0, min(gap_ms, 500))
        except Exception:
            gap_ms = 60
        try:
            if gap_ms > 0 and _remaining_deadline() > ((gap_ms / 1000.0) + 0.2):
                time.sleep(gap_ms / 1000.0)

            if frame_src == "ui_snapshot":
                ok2, frame2_s, _err2 = _fetch_ui_snapshot(snap_url, timeout_sec=1.5)
                if ok2 and frame2_s is not None:
                    frame2 = frame2_s
            elif ensure_cam:
                frame2, _ts2 = get_frame_with_ts(ensure_start=ensure_cam)

            if frame2 is not None:
                pre_pair = _att_pair(frame, frame2)
        except Exception:
            pre_pair = None

    # Optional: stable motion centroid from the *same pose* (frame vs frame2)
    # Used for Threat policy/reward without camera-motion artefacts.
    pre_motion_feat = None
    if frame2 is not None:
        try:
            s1 = _downsample_gray(frame, w=motion_w, h=motion_h)
            s2 = _downsample_gray(frame2, w=motion_w, h=motion_h)
            cent = _motion_centroid(s1, s2, w=motion_w, h=motion_h)
            pre_motion_feat = {"centroid": cent, "dist": float(cent.get("dist", 0.0)), "energy": float(cent.get("energy", 0.0))}
        except Exception:
            pre_motion_feat = None

    # Feature extraction (cheap) – motion across ticks.
    #
    # IMPORTANT / HOTPATCH 2026-05-07:
    # The follow/threat path evaluates the motion centroid with motion_w/motion_h.
    # Therefore the stored current frame must be downsampled with exactly the same
    # dimensions. The previous implementation stored the default 64x36 buffer but
    # evaluated it as 96x54 when OROMA_PTZ_ATTENTION_MOTION_W/H were configured.
    # That made _motion_centroid() return dx=dy=energy=0 and Threat mode produced
    # action="" even though motion was detected.
    expected_motion_bytes = int(motion_w) * int(motion_h)
    cur_small = _downsample_gray(frame, w=motion_w, h=motion_h)
    prev_small = st.get("prev_small")
    if isinstance(prev_small, str):
        try:
            prev_small_b = bytes.fromhex(prev_small)
        except Exception:
            prev_small_b = None
    else:
        prev_small_b = None

    motion_shape_reset = False
    if prev_small_b is not None and len(prev_small_b) != expected_motion_bytes:
        motion_shape_reset = True
        old_len = len(prev_small_b)
        prev_small_b = None
        st["last_motion_shape_reset"] = True
        st["last_motion_shape_reset_old_len"] = int(old_len)
        st["last_motion_shape_reset_expected_len"] = int(expected_motion_bytes)
        st["last_motion_shape_reset_ts"] = int(now_ts)
        try:
            insert_metric("ptz:motion_shape_reset", 1.0, ts=int(now_ts))
        except Exception:
            pass
        try:
            logger.warning(
                "PTZ motion buffer shape reset: prev_len=%s expected_len=%s motion_w=%s motion_h=%s",
                old_len, expected_motion_bytes, motion_w, motion_h,
            )
        except Exception:
            pass
    else:
        st["last_motion_shape_reset"] = False

    motion_prev_available = bool(prev_small_b is not None)
    motion_cur_available = bool(cur_small is not None)
    mot = _motion_norm(prev_small_b, cur_small)
    shp = _sharpness(frame)

    # Luma (Helligkeit) – DB-freies Signal (aus Downsample)
    luma = _luma_norm(cur_small)
    luma_ema = 0.0
    if luma_recover_enable:
        try:
            upd = _luma_update(st, luma_level=float(luma), now_ts=int(now_ts))
            luma_ema = float(upd.get('ema', 0.0))
        except Exception:
            luma_ema = float(luma)
    else:
        luma_ema = float(luma)

    now_ts = int(time.time())
    last_active = int(st.get("last_active_ts") or 0)
    if mot >= motion_low:
        last_active = now_ts

    # Fixation window
    fix_until = int(st.get("fix_until_ts") or 0)

    # Decide mode
    # NOTE: Reflex Layer (Audio-Spike / Motion-Threat)
    # ---------------------------------------------
    # Reflexe muessen DB-frei und sehr robust sein. Wir benutzen daher:
    #   - hub_audio.get_audio_level()  (in-memory; kein SQL)
    #   - EMA Noise-Floor + Hysterese + Cooldown
    # Ergebnis:
    #   - Spike -> kurzer Probe-Mode (Suchen nach Bewegung)
    #   - Bewegung hoch -> Threat-Mode (Motion bekommt Priorität)
    audio_level = 0.0
    audio_noise = 0.0
    audio_spike = 0.0
    speech = False
    if audio_enable and hub_audio is not None:
        try:
            audio_level = float(hub_audio.get_audio_level() or 0.0)
        except Exception:
            audio_level = 0.0
        aud = _audio_update(st, audio_level=float(audio_level), now_ts=int(now_ts))
        audio_noise = float(aud.get("noise", 0.0))
        audio_spike = float(aud.get("spike", 0.0))

        # Speech-Guard (DB-frei): absolut + relativ ueber Noise-Floor
        try:
            if audio_guard and float(audio_level) >= float(speech_abs_min) and float(audio_level) >= float(audio_noise) + float(speech_margin):
                speech = True
        except Exception:
            speech = False

        # Spike -> Probe triggern (nur wenn nicht gerade Threat aktiv)
        try:
            last_probe_ts = int(st.get("audio_last_probe_ts") or 0)
        except Exception:
            last_probe_ts = 0
        try:
            if float(audio_level) >= float(audio_abs_min) and float(audio_spike) >= float(audio_spike_hi):
                if (int(now_ts) - int(last_probe_ts)) >= int(audio_probe_cd):
                    st["audio_last_probe_ts"] = int(now_ts)
                    st["probe_remaining"] = int(max(0, probe_steps))
                    st["probe_idx"] = int(st.get("probe_idx") or 0)
                    st["probe_trigger_ts"] = int(now_ts)
        except Exception:
            pass

    # Motion -> Threat hold (Hysterese: bleibt kurz aktiv)
    try:
        if float(mot) >= float(threat_high):
            st["threat_until_ts"] = int(now_ts) + int(max(1, threat_hold_sec))
    except Exception:
        pass
    try:
        threat_until = int(st.get("threat_until_ts") or 0)
    except Exception:
        threat_until = 0
    threat_active = (int(now_ts) < int(threat_until)) and (float(mot) >= float(motion_low))

    # Probe aktiv?
    try:
        probe_remaining = int(st.get("probe_remaining") or 0)
    except Exception:
        probe_remaining = 0
    probe_active = probe_remaining > 0

    # -----------------------------------------------------------------
    # Luma-Recovery trigger (DB-frei, tick-basiert)
    # -----------------------------------------------------------------
    # Problem: PTZ kann an Decke/Boden 'pinnen' (z.B. Tilt extreme) – Bild wird
    # sehr dunkel/hell. Ein Lebewesen wuerde dann *kurz* korrigieren, aber
    # nicht nervoes scannen. Daher: Hold + Hysterese + Cooldown + Burst-Steps.
    luma_recover_active = False
    luma_recover_reason = ''
    try:
        cd_until = int(st.get('luma_recover_cd_until_ts') or 0)
    except Exception:
        cd_until = 0
    try:
        rearm_until = int(st.get('luma_recover_rearm_until_ts') or 0)
    except Exception:
        rearm_until = 0
    try:
        burst_window_start = int(st.get('luma_recover_burst_window_start_ts') or 0)
    except Exception:
        burst_window_start = 0
    try:
        burst_count = int(st.get('luma_recover_burst_count') or 0)
    except Exception:
        burst_count = 0
    if int(luma_window_sec) <= 0:
        burst_window_start = 0
        burst_count = 0
    elif burst_window_start <= 0 or (int(now_ts) - int(burst_window_start)) >= int(max(1, luma_window_sec)):
        burst_window_start = int(now_ts)
        burst_count = 0
        st['luma_recover_burst_window_start_ts'] = int(burst_window_start)
        st['luma_recover_burst_count'] = int(burst_count)
    try:
        bad_since = int(st.get('luma_bad_since_ts') or 0)
    except Exception:
        bad_since = 0
    try:
        bright_since = int(st.get('luma_bright_since_ts') or 0)
    except Exception:
        bright_since = 0

    # Determine current bad/bright state with hysteresis
    try:
        low_thr = float(luma_low)
        high_thr = float(luma_high)
        hyst = max(0.0, float(luma_hyst))
    except Exception:
        low_thr = 0.12
        high_thr = 0.85
        hyst = 0.03

    # Update hold timers
    if float(luma_ema) <= float(low_thr):
        if bad_since <= 0:
            st['luma_bad_since_ts'] = int(now_ts)
            bad_since = int(now_ts)
        # reset bright timer
        st['luma_bright_since_ts'] = 0
        bright_since = 0
    elif float(luma_ema) >= float(low_thr) + float(hyst):
        st['luma_bad_since_ts'] = 0
        bad_since = 0

    if float(luma_ema) >= float(high_thr):
        if bright_since <= 0:
            st['luma_bright_since_ts'] = int(now_ts)
            bright_since = int(now_ts)
        # reset dark timer
        st['luma_bad_since_ts'] = 0
        bad_since = 0
    elif float(luma_ema) <= float(high_thr) - float(hyst):
        st['luma_bright_since_ts'] = 0
        bright_since = 0

    # Trigger burst (only if not threat/probe/speech)
    try:
        remaining = int(st.get('luma_recover_remaining') or 0)
    except Exception:
        remaining = 0

    luma_trigger_allowed = (
        remaining <= 0
        and luma_recover_enable
        and (int(now_ts) >= int(cd_until))
        and (int(now_ts) >= int(rearm_until))
        and (not threat_active)
        and (not probe_active)
        and (not speech)
        and (int(luma_max_bursts_per_window) <= 0 or int(burst_count) < int(luma_max_bursts_per_window))
    )

    if luma_trigger_allowed:
        try:
            if bad_since > 0 and (int(now_ts) - int(bad_since)) >= int(max(1, luma_hold_sec)):
                st['luma_recover_remaining'] = int(max(1, min(6, int(luma_steps))))
                st['luma_recover_reason'] = 'low'
                st['luma_recover_cd_until_ts'] = int(now_ts) + int(max(1, luma_cd_sec))
                st['luma_recover_rearm_until_ts'] = int(now_ts) + int(max(1, luma_rearm_sec))
                burst_count = int(burst_count) + 1
                st['luma_recover_burst_count'] = int(burst_count)
                st['luma_recover_burst_last_ts'] = int(now_ts)
        except Exception:
            pass
        try:
            remaining = int(st.get('luma_recover_remaining') or 0)
        except Exception:
            remaining = 0
        if remaining <= 0:
            try:
                if bright_since > 0 and (int(now_ts) - int(bright_since)) >= int(max(1, luma_hold_sec)):
                    st['luma_recover_remaining'] = int(max(1, min(6, int(luma_steps))))
                    st['luma_recover_reason'] = 'high'
                    st['luma_recover_cd_until_ts'] = int(now_ts) + int(max(1, luma_cd_sec))
                    st['luma_recover_rearm_until_ts'] = int(now_ts) + int(max(1, luma_rearm_sec))
                    burst_count = int(burst_count) + 1
                    st['luma_recover_burst_count'] = int(burst_count)
                    st['luma_recover_burst_last_ts'] = int(now_ts)
            except Exception:
                pass

    try:
        remaining = int(st.get('luma_recover_remaining') or 0)
    except Exception:
        remaining = 0
    if remaining > 0:
        luma_recover_active = True
        try:
            luma_recover_reason = str(st.get('luma_recover_reason') or '')
        except Exception:
            luma_recover_reason = ''

    # Light telemetry (best-effort)
    try:
        insert_metric("ptz:audio_level", float(audio_level))
        insert_metric("ptz:audio_noise", float(audio_noise))
        insert_metric("ptz:audio_spike", float(audio_spike))
        insert_metric("ptz:speech_guard", 1.0 if speech else 0.0)
        insert_metric("ptz:probe_active", 1.0 if probe_active else 0.0)
        insert_metric("ptz:threat_active", 1.0 if threat_active else 0.0)
        insert_metric("ptz:luma", float(luma))
        insert_metric("ptz:luma_ema", float(luma_ema))
        if cur_norm is not None:
            insert_metric("ptz:curiosity", float(cur_norm))
        insert_metric("ptz:luma_recover_active", 1.0 if luma_recover_active else 0.0)
        # Optional: expose reason as numeric metric for SQL/UI.
        #  0 = none/inactive
        #  1 = low (too dark)
        #  2 = high (too bright)
        # NOTE: SQLite metrics.value is numeric; we therefore map string→number.
        reason_num = 0.0
        if luma_recover_active:
            if (luma_recover_reason or "") == "low":
                reason_num = 1.0
            elif (luma_recover_reason or "") == "high":
                reason_num = 2.0
        insert_metric("ptz:luma_recover_reason", float(reason_num))
    except Exception:
        pass

    # -----------------------------------------------------------------
    # skip_reason (Bugfix / Produktion)
    # --------------------------------------------
    # Der Loop nutzt skip_reason (UI/Logging), deshalb muss es IMMER
    # definiert werden.
    skip_reason = ""

    # Mode-Priorität (biologisch):
    #   Threat > Probe > SpeechGuard > LumaRecover > Fixate > Scan > Orient
    if threat_active:
        mode = "threat"
        skip_reason = "threat"
    elif probe_active:
        mode = "probe"
        skip_reason = "probe"
    elif speech:
        mode = "fixate"
        skip_reason = "speech_guard"
    elif luma_recover_active:
        # UI/Logs sollen ein klares, stabiles Signal bekommen.
        # Statt "luma_recover:..." (zu detailreich / wechselnd) nutzen wir
        # zwei saubere Gründe: 'luma_dark' oder 'luma_bright'.
        mode = "luma_recover"
        if str(luma_recover_reason or '').strip().lower() == 'high':
            skip_reason = "luma_bright"
        else:
            skip_reason = "luma_dark"
    elif now_ts < fix_until:
        mode = "fixate"
    elif (now_ts - last_active) >= bored_sec:
        mode = "scan"
    elif float(mot) >= float(orient_thr):
        mode = "orient"
    else:
        mode = "fixate"


    # Curiosity → ORIENT trigger (light, safe)
    # ------------------------------------------------
    # Wenn Curiosity hoch ist, kann der Loop auch ohne Motion einen seltenen ORIENT
    # auslösen (ähnlich "explore saccade"). Dadurch wird Exploration physisch sichtbar,
    # ohne Threat/Probe/Speech/Luma zu stören.
    if _env_bool("OROMA_PTZ_ATT_CURIOSITY_ORIENT_ENABLE", True) and cur_norm is not None:
        try:
            cur_thr = _env_float("OROMA_PTZ_ATT_CURIOSITY_ORIENT_THR", 0.65)
            p = _env_float("OROMA_PTZ_ATT_CURIOSITY_ORIENT_P", 0.18)
            cd = _env_int("OROMA_PTZ_ATT_CURIOSITY_ORIENT_COOLDOWN_SEC", 12)
            last_orient_ts = int(st.get("last_orient_ts") or 0)
            if mode == "fixate" and float(cur_norm) >= float(cur_thr) and (int(now_ts) - int(last_orient_ts)) >= int(max(1, cd)):
                if random.random() < max(0.0, min(1.0, float(p) * float(cur_norm))):
                    mode = "orient"
                    skip_reason = "curiosity_orient"
        except Exception:
            pass

    # Read current positions + ranges
    controls = ptz.get("controls") or {}
    pan_c = controls.get("pan_absolute") or {}
    tilt_c = controls.get("tilt_absolute") or {}
    zoom_c = controls.get("zoom_absolute") or {}

    try:
        pan_cur = int(pan_c.get("value"))
        tilt_cur = int(tilt_c.get("value"))
        pan_min = int(pan_c.get("min"))
        pan_max = int(pan_c.get("max"))
        tilt_min = int(tilt_c.get("min"))
        tilt_max = int(tilt_c.get("max"))
    except Exception:
        pan_cur = 0
        tilt_cur = 0
        pan_min = -1
        pan_max = 1
        tilt_min = -1
        tilt_max = 1

    last_orient = int(st.get("last_orient_ts") or 0)
    last_dir = str(st.get("last_dir") or "")

    # Action outputs (used for API response / reward logging)
    moved = False
    action = ""
    motion_state_hash_used = ""
    probe_state_hash_used = ""


    if mode == "orient":
        if (now_ts - last_orient) < orient_cd:
            mode = "fixate"

    if mode == "threat":
        # THREAT: Bewegung priorisieren (Lebewesen-Reflex)
        #
        # Ziel: Richtung der Bewegung ins Zentrum ziehen (kleiner State-Space).
        # Policy (optional): namespace 'ptz_motion' kann Reflex-Action überschreiben.
        try:
            st["probe_remaining"] = 0  # Threat dominiert Probe
        except Exception:
            pass

        flags = _edge_flags(int(pan_cur), int(tilt_cur), int(pan_min), int(pan_max), int(tilt_min), int(tilt_max))

        # Motion centroid (prefer stable pre-frame pair, else tick-to-tick)
        mcent = None
        try:
            if isinstance(pre_motion_feat, dict) and isinstance(pre_motion_feat.get("centroid"), dict):
                mcent = pre_motion_feat.get("centroid")
        except Exception:
            mcent = None
        if not isinstance(mcent, dict):
            mcent = _motion_centroid(prev_small_b, cur_small, w=motion_w, h=motion_h)

        dx = float(mcent.get("dx", 0.0))
        dy = float(mcent.get("dy", 0.0))
        e = float(mcent.get("energy", 0.0))
        dist = float(mcent.get("dist", 0.0))

        dyn_dead = float(threat_deadzone) - (max(0.0, float(e)) * float(threat_deadzone_e_scale))
        dyn_dead = max(float(threat_deadzone_min), min(float(threat_deadzone), float(dyn_dead)))
        if cat_follow_enable:
            dyn_dead = max(float(cat_deadzone_min), float(dyn_dead) * max(0.10, float(cat_deadzone_scale)))

        dx_bin = _bin3(dx, dead=float(dyn_dead))
        dy_bin = _bin3(dy, dead=float(dyn_dead))
        e_thr = _env_float("OROMA_PTZ_MOTION_ENERGY_THR", float(motion_low))
        e_bin = _energy_bin(e, thr=float(e_thr))
        motion_state_hash_used = _state_hash_motion(dx_bin, dy_bin, e_bin, int(flags))

        # Stabilization telemetry. These fields are intentionally stored before
        # the action decision so UI/logs can show why vertical movement was or
        # was not selected.
        try:
            st["last_threat_dx"] = round(float(dx), 6)
            st["last_threat_dy"] = round(float(dy), 6)
            st["last_threat_energy_centroid"] = round(float(e), 6)
            st["last_threat_dist_centroid"] = round(float(dist), 6)
            st["last_threat_dyn_deadzone"] = round(float(dyn_dead), 6)
            st["last_threat_vertical_gain"] = round(float(vertical_gain), 3)
        except Exception:
            pass
        try:
            insert_metric("ptz:threat_dx", float(dx), ts=now_ts)
            insert_metric("ptz:threat_dy", float(dy), ts=now_ts)
            insert_metric("ptz:threat_dist", float(dist), ts=now_ts)
            insert_metric("ptz:threat_energy_centroid", float(e), ts=now_ts)
            insert_metric("ptz:threat_deadzone_used", float(dyn_dead), ts=now_ts)
        except Exception:
            pass

        raw_follow_action = ""
        mapped_follow_action = ""
        pol = _policy_choose_action_ns("ptz_motion", motion_state_hash_used, "OROMA_PTZ_MOTION_POLICY")
        if pol in ("left", "right", "up", "down", "center"):
            raw_follow_action = pol
            action = _map_follow_action(pol, invert_x=follow_invert_x, invert_y=follow_invert_y) if pol in ("left", "right", "up", "down") else pol
            mapped_follow_action = str(action or "")
        else:
            # Reflex: grobe Richtung wählen (dominante Achse statt Pan-Zwang).
            #
            # Historisch wurde Pan zuerst geprüft: dx_bin vor dy_bin. Dadurch
            # gewann links/rechts fast immer, weil Personenbewegung und PTZ-
            # Eigenbewegung im Innenraum horizontal stärkere Bilddifferenzen
            # erzeugen. Up/down war praktisch nur erreichbar, wenn X komplett
            # innerhalb der Deadzone lag. Für echtes Follow ist das zu starr.
            #
            # Jetzt wird die dominante Achse explizit bestimmt; dy bekommt eine
            # kleine, konfigurierbare Kompensation (vertical_gain). Damit bleibt
            # Pan weiterhin natürlich, aber Tilt kann bei erkennbarem vertikalen
            # Schwerpunkt überhaupt konkurrieren.
            dominant_axis = _dominant_motion_axis(dx, dy, vertical_gain=vertical_gain)
            dominant_mag = _ptz_dir_strength("right" if dominant_axis == "x" else "down", dx, dy, vertical_gain=vertical_gain)
            st["last_threat_axis"] = str(dominant_axis)
            st["last_threat_axis_strength"] = round(float(dominant_mag), 6)

            if dx_bin != 1 or dy_bin != 1:
                # Wenn beide Achsen außerhalb der Deadzone liegen, entscheidet
                # die dominante Achse. Wenn nur eine Achse ausbricht, wird genau
                # diese gewählt. Das ersetzt die alte harte dx-vor-dy-Reihenfolge.
                if dx_bin != 1 and dy_bin != 1:
                    action = _direction_from_axis(dominant_axis, dx, dy)
                elif dx_bin != 1:
                    action = "left" if dx_bin == 0 else "right"
                else:
                    action = "up" if dy_bin == 0 else "down"
            elif cat_follow_enable and float(e) >= float(cat_force_energy) and (
                float(dist) >= float(cat_force_dist) or float(dominant_mag) >= float(cat_force_axis_min)
            ):
                action = _direction_from_axis(dominant_axis, dx, dy)
                try:
                    insert_metric("ptz:cat_follow_force", 1.0, ts=now_ts)
                except Exception:
                    pass
            elif float(e) >= float(threat_force_energy) and float(dist) >= float(threat_force_dist):
                action = _direction_from_axis(dominant_axis, dx, dy)
                try:
                    insert_metric("ptz:threat_force_follow", 1.0, ts=now_ts)
                except Exception:
                    pass
            elif float(e) >= float(threat_sticky_energy) and float(dominant_mag) >= float(threat_force_axis_min):
                action = _direction_from_axis(dominant_axis, dx, dy)
                try:
                    insert_metric("ptz:threat_axis_follow", 1.0, ts=now_ts)
                except Exception:
                    pass
            elif (now_ts - int(last_orient)) <= int(max(0, threat_sticky_dir_sec)) and str(last_dir or "") in ("left", "right", "up", "down") and float(e) >= float(threat_sticky_energy):
                action = str(last_dir)
                try:
                    insert_metric("ptz:threat_sticky_follow", 1.0, ts=now_ts)
                except Exception:
                    pass
            else:
                action = ""

            raw_follow_action = str(action or "")
            if raw_follow_action in ("left", "right", "up", "down"):
                mapped_follow_action = _map_follow_action(raw_follow_action, invert_x=follow_invert_x, invert_y=follow_invert_y)
                if mapped_follow_action != raw_follow_action:
                    try:
                        insert_metric("ptz:follow_mapping_inverted", 1.0, ts=now_ts)
                    except Exception:
                        pass
                action = mapped_follow_action
            else:
                mapped_follow_action = raw_follow_action

        try:
            st["last_threat_raw_action"] = str(raw_follow_action or "")
            st["last_threat_mapped_action"] = str(mapped_follow_action or action or "")
            st["last_threat_follow_invert_x"] = bool(follow_invert_x)
            st["last_threat_follow_invert_y"] = bool(follow_invert_y)
        except Exception:
            pass

        # Self-motion guard: if the previous successful PTZ command is still
        # very recent, the current optical-flow centroid is likely caused by the
        # camera itself. Suppress immediate follow to avoid chasing our own pan.
        # This remains conservative in Orchestrator one-shot mode: if ticks are
        # several seconds apart, the guard naturally expires before the next run.
        try:
            last_move_ts_f = float(st.get("last_ptz_move_ts") or 0.0)
        except Exception:
            last_move_ts_f = 0.0
        try:
            since_move = float(time.time()) - float(last_move_ts_f)
        except Exception:
            since_move = 999999.0
        if action in ("left", "right", "up", "down") and float(self_motion_guard_sec) > 0.0 and last_move_ts_f > 0.0 and since_move < float(self_motion_guard_sec):
            try:
                insert_metric("ptz:self_motion_guard", 1.0, ts=now_ts)
            except Exception:
                pass
            st["last_threat_suppressed_action"] = str(action)
            st["last_threat_suppressed_reason"] = "self_motion_guard"
            st["last_threat_self_motion_age_sec"] = round(float(since_move), 3)
            action = ""
            skip_reason = "ptz_self_motion_guard"

        # Reversal guard: block quick left/right or up/down flips unless the new
        # opposite signal is clearly stronger. This addresses the observed ~62%
        # direction-change rate from the live logs.
        try:
            last_follow_dir = str(st.get("last_threat_follow_dir") or st.get("last_dir") or "")
            last_follow_ts = float(st.get("last_threat_follow_ts") or st.get("last_ptz_move_ts") or 0.0)
            last_follow_strength = float(st.get("last_threat_follow_strength") or 0.0)
        except Exception:
            last_follow_dir = ""
            last_follow_ts = 0.0
            last_follow_strength = 0.0
        new_strength = _ptz_dir_strength(action, dx, dy, vertical_gain=vertical_gain)
        try:
            since_follow = float(time.time()) - float(last_follow_ts)
        except Exception:
            since_follow = 999999.0
        if action in ("left", "right", "up", "down") and _opposite_ptz_dir(action, last_follow_dir) and float(reversal_guard_sec) > 0.0 and since_follow < float(reversal_guard_sec):
            allow_reverse = bool(float(new_strength) >= max(float(threat_force_axis_min), float(last_follow_strength) * float(reversal_ratio)))
            if not allow_reverse:
                try:
                    insert_metric("ptz:reversal_guard", 1.0, ts=now_ts)
                except Exception:
                    pass
                st["last_threat_suppressed_action"] = str(action)
                st["last_threat_suppressed_reason"] = "reversal_guard"
                st["last_threat_reversal_prev_dir"] = str(last_follow_dir)
                st["last_threat_reversal_new_strength"] = round(float(new_strength), 6)
                st["last_threat_reversal_prev_strength"] = round(float(last_follow_strength), 6)
                action = ""
                skip_reason = "ptz_reversal_guard"

        if action:
            if threat_burst_enable and action in ("left", "right", "up", "down"):
                if threat_amount_dynamic:
                    threat_amount = _calc_dynamic_threat_amount(
                        base_steps=int(threat_burst_steps),
                        min_steps=int(threat_amount_min),
                        max_steps=int(threat_amount_max),
                        dx=float(dx),
                        dy=float(dy),
                        dist=float(dist),
                        energy=float(e),
                        deadzone=float(dyn_dead),
                        vertical_gain=float(vertical_gain),
                        dist_scale=float(threat_amount_dist_scale),
                        energy_scale=float(threat_amount_e_scale),
                    )
                    try:
                        insert_metric("ptz:threat_amount_dynamic", float(threat_amount), ts=now_ts)
                    except Exception:
                        pass
                else:
                    threat_amount = int(threat_burst_steps)
            else:
                threat_amount = 1
            try:
                st["last_threat_amount"] = int(threat_amount)
                st["last_threat_amount_dynamic"] = bool(threat_amount_dynamic)
                st["last_threat_amount_min"] = int(threat_amount_min)
                st["last_threat_amount_max"] = int(threat_amount_max)
            except Exception:
                pass
            cmd_ok, cmd_event = _exec_ptz_command(action, amount=threat_amount, reason="threat")
            moved = bool(cmd_ok) and (not dry_run)
            st["last_ptz_command_ok"] = bool(cmd_ok)
            st["last_ptz_command_error"] = str(cmd_event.get("error") or "")
            st["last_threat_burst_amount"] = int(threat_amount)
            if cmd_ok:
                st["last_dir"] = action
                st["last_ptz_move_ts"] = float(time.time())
                st["last_threat_follow_dir"] = str(action)
                st["last_threat_follow_ts"] = float(time.time())
                st["last_threat_follow_axis"] = _ptz_dir_axis(action)
                st["last_threat_follow_strength"] = round(float(_ptz_dir_strength(action, dx, dy, vertical_gain=vertical_gain)), 6)
                # Katzenaehnlich: nach Threat-Move moeglichst schnell erneut bewerten statt lange fixieren.
                if cat_follow_enable:
                    st["fix_until_ts"] = now_ts + max(0, int(cat_threat_fixate_sec))
                else:
                    st["fix_until_ts"] = now_ts + max(1, int(_env_int("OROMA_PTZ_THREAT_FIXATE_SEC", 1)))
            else:
                try:
                    insert_metric("ptz:threat_move_failed", 1.0, ts=now_ts)
                except Exception:
                    pass
        else:
            moved = False

    elif mode == "probe":
        # PROBE: Audio-Spike -> kurz nach Bewegung suchen (tick-basiert)
        #
        # Policy (optional): namespace 'ptz_probe' lernt "wo" nach Spikes zu suchen.
        flags = _edge_flags(int(pan_cur), int(tilt_cur), int(pan_min), int(pan_max), int(tilt_min), int(tilt_max))

        try:
            probe_idx = int(st.get("probe_idx") or 0)
        except Exception:
            probe_idx = 0
        phase = int(probe_idx % 2)
        e_bin = _energy_bin(float(mot), thr=float(motion_low))
        probe_state_hash_used = _state_hash_probe(phase, e_bin, int(flags))

        pol = _policy_choose_action_ns("ptz_probe", probe_state_hash_used, "OROMA_PTZ_PROBE_POLICY")
        if pol in ("left", "right", "up", "down", "center"):
            action = pol
        else:
            pattern = ["left", "right", "up", "down"]
            action = pattern[int(probe_idx) % len(pattern)]

        if action:
            cmd_ok, cmd_event = _exec_ptz_command(action, amount=1, reason="probe")
            moved = bool(cmd_ok) and (not dry_run)
            st["last_ptz_command_ok"] = bool(cmd_ok)
            st["last_ptz_command_error"] = str(cmd_event.get("error") or "")
        else:
            moved = False
        if moved:
            st["last_dir"] = action
            try:
                st["probe_remaining"] = max(0, int(probe_remaining) - 1)
            except Exception:
                st["probe_remaining"] = 0
            st["probe_idx"] = int(probe_idx) + 1
            st["fix_until_ts"] = now_ts + 1

    elif mode == "luma_recover":
        # LUMA_RECOVER: Bild ist dauerhaft extrem dunkel/hell -> kurze Korrektur
        # (tick-basiert, kein Busy-Loop, kein DB).
        #
        # Ziel: aus 'pinned' Situationen (Boden/Decke/Lampe) herauskommen.
        # Prioritaet ist unterhalb von Threat/Probe/Speech, aber oberhalb
        # von Fixate/Scan/Orient, weil ein 'blindes' Tier nicht sinnvoll
        # fixieren kann.
        try:
            remaining = int(st.get('luma_recover_remaining') or 0)
        except Exception:
            remaining = 0

        # Produktion-Guard:
        # In seltenen Randfällen (z.B. nach Reboot/Statefile-Reset oder
        # inkonsistentem State) kann der Mode bereits auf 'luma_recover' stehen,
        # obwohl 'remaining' noch 0 ist. Dann wuerde der Loop in einem
        # "scheinbar aktiven" Modus bleiben, aber keinen Move ausfuehren.
        #
        # Hier erzwingen wir einen minimalen 1-Step-Burst, damit der Recovery-
        # Impuls sichtbar wird und die Kamera aus dem dunklen/hellen Pinning
        # herauskommt. Danach greift der normale Cooldown/Hold wieder.
        if remaining <= 0:
            remaining = 1
            st['luma_recover_remaining'] = 1
        try:
            reason_lr = str(st.get('luma_recover_reason') or 'low')
        except Exception:
            reason_lr = 'low'

        flags = _edge_flags(int(pan_cur), int(tilt_cur), int(pan_min), int(pan_max), int(tilt_min), int(tilt_max))

        # Default: tilt towards a *center band* (prevents the classic "stuck at ceiling" / "stuck at floor" case).
        # Reason semantics:
        #   'low'  -> too dark
        #   'high' -> too bright
        # Heuristic:
        #   - If too dark and we're already above the center band, go DOWN (escape dark ceiling corner).
        #   - If too dark and we're below/near center, go UP (escape dark floor).
        #   - If too bright and we're already below the center band, go UP (escape over-bright floor glare).
        #   - If too bright and we're above/near center, go DOWN (escape bright ceiling/lamp).
        action = ''
        tilt_rng = max(1, int(tilt_max) - int(tilt_min))
        tilt_mid = int(tilt_min) + (tilt_rng // 2)
        band_half = max(int(tilt_rng * 0.15), 3600)  # ~15% of range, at least one PTZ step
        above_center = int(tilt_cur) >= (tilt_mid + band_half)
        below_center = int(tilt_cur) <= (tilt_mid - band_half)

        if reason_lr == 'high':
            # Prefer DOWN, unless we're already below center (then UP) or edges force otherwise.
            prefer = 'up' if below_center else 'down'
        else:
            # Prefer UP, unless we're already above center (then DOWN) or edges force otherwise.
            prefer = 'down' if above_center else 'up'

        if prefer == 'down':
            if not (flags & 4):
                action = 'down'
        else:
            if not (flags & 8):
                action = 'up'

        # If tilt is already blocked by edge, diversify by pan alternating.
        if not action:
            try:
                lr_idx = int(st.get('luma_recover_idx') or 0)
            except Exception:
                lr_idx = 0
            action = 'left' if (lr_idx % 2 == 0) else 'right'
            st['luma_recover_idx'] = lr_idx + 1

        if action:
            cmd_ok, cmd_event = _exec_ptz_command(action, amount=1, reason="luma_recover")
            moved = bool(cmd_ok) and (not dry_run)
            st["last_ptz_command_ok"] = bool(cmd_ok)
            st["last_ptz_command_error"] = str(cmd_event.get("error") or "")
            if moved:
                st['last_dir'] = action
            st['fix_until_ts'] = now_ts + 2
            try:
                st['luma_recover_remaining'] = max(0, int(remaining) - 1)
            except Exception:
                st['luma_recover_remaining'] = 0
        else:
            moved = False
            st['luma_recover_remaining'] = 0

    elif mode == "scan":
        scan_idx = int(st.get("scan_idx") or 0)
        # Scan Tilt Soft-Band:
        # Wir vermeiden, dass der Scan sofort wieder in extreme Tilt-Bereiche laeuft,
        # die typischerweise 'schwarz' (Boden/Untertisch) oder 'ausgebrannt' (Decke/Lampe)
        # produzieren. Das ist wichtig, um Oszillation zwischen luma_recover und scan
        # zu verhindern.
        tilt_range = int(tilt_max) - int(tilt_min)
        eff_tilt_min = int(tilt_min)
        eff_tilt_max = int(tilt_max)
        try:
            if tilt_range > 0:
                min_frac = max(0.0, min(0.80, float(scan_tilt_soft_min_frac)))
                max_frac = max(0.0, min(0.80, float(scan_tilt_soft_max_frac)))
                # Wenn es 'dark-ish' ist, meide unten noch staerker.
                try:
                    if float(luma_ema) <= float(luma_low) + float(max(0.0, luma_hyst)):
                        min_frac = max(min_frac, min(0.80, float(min_frac) + float(scan_tilt_soft_dark_boost)))
                except Exception:
                    pass
                eff_tilt_min = int(int(tilt_min) + int(float(tilt_range) * float(min_frac)))
                eff_tilt_max = int(int(tilt_max) - int(float(tilt_range) * float(max_frac)))
                # Safety: eff range darf nicht kollabieren
                if eff_tilt_max <= eff_tilt_min + 3600:
                    eff_tilt_min = int(tilt_min)
                    eff_tilt_max = int(tilt_max)
        except Exception:
            eff_tilt_min = int(tilt_min)
            eff_tilt_max = int(tilt_max)

        tgt_pan, tgt_tilt = _pick_scan_target(pan_min, pan_max, eff_tilt_min, eff_tilt_max, scan_bins_x, scan_bins_y, scan_idx)

        # pick axis first (pan, then tilt) to avoid diagonal jitter
        a_pan = _nudge_towards(pan_cur, tgt_pan)
        a_tilt = _nudge_towards_tilt(tilt_cur, tgt_tilt)

        if a_pan and a_pan != last_dir:
            action = a_pan
        elif a_tilt and a_tilt != last_dir:
            action = a_tilt
        else:
            action = a_pan or a_tilt or ""

        if action:
            cmd_ok, cmd_event = _exec_ptz_command(action, amount=scan_step, reason="scan")
            moved = bool(cmd_ok) and (not dry_run)
            st["last_ptz_command_ok"] = bool(cmd_ok)
            st["last_ptz_command_error"] = str(cmd_event.get("error") or "")
            if moved:
                st["last_dir"] = action
        else:
            # reached target -> advance scan cell
            st["scan_idx"] = scan_idx + 1

        # Nach Scan nicht mehr "eulenartig" lange verharren.
        if cat_follow_enable:
            lo = int(min(cat_scan_fix_min, cat_scan_fix_max))
            hi = int(max(cat_scan_fix_min, cat_scan_fix_max))
            st["fix_until_ts"] = now_ts + (random.randint(lo, hi) if hi > lo else hi)
        else:
            # after scan move: short fixation window (eule)
            st["fix_until_ts"] = now_ts + random.randint(fix_min, fix_max)

    elif mode == "orient":
        # ORIENT: reaktive, seltene Sakkade.
        #
        # Phase-2 (Lernen): Wenn DreamWorker policy_rules aus attention_gain
        # aktualisiert hat, ziehen wir hier epsilon-greedy die beste Aktion
        # für den aktuellen Zustand. Falls keine Policy vorhanden (oder
        # Exploration), fällt der Loop auf die alte Random-Logik zurück.

        try:
            z_cur = int(zoom_c.get("value"))
        except Exception:
            z_cur = 0

        state_hash = _policy_state_hash(
            pan=pan_cur,
            tilt=tilt_cur,
            zoom=z_cur,
            pan_min=pan_min,
            pan_max=pan_max,
            tilt_min=tilt_min,
            tilt_max=tilt_max,
        )

        pol = _policy_choose_action(state_hash)
        if pol in ("left", "right", "up", "down", "zoom_in", "zoom_out", "center"):
            action = pol
        else:
            # Fallback: simple conservative saccade (diversify direction)
            cand = ["left", "right", "up", "down"]
            # Curiosity: bei hoher Curiosity dürfen Zoom-Probes auch in ORIENT auftauchen
            # (nur wenn Zoom-Range gültig ist). Das macht Exploration sichtbar, ohne
            # Scan zu übersteuern.
            if _env_bool("OROMA_PTZ_ATT_CURIOSITY_ZOOM_IN_ORIENT_ENABLE", True) and cur_norm is not None:
                try:
                    thr = _env_float("OROMA_PTZ_ATT_CURIOSITY_ZOOM_IN_ORIENT_THR", 0.70)
                    if float(cur_norm) >= float(thr):
                        try:
                            z_min = int(zoom_c.get("min"))
                            z_max = int(zoom_c.get("max"))
                        except Exception:
                            z_min = 0
                            z_max = 0
                        if z_max > z_min:
                            cand += ["zoom_in", "zoom_out"]
                except Exception:
                    pass
            if last_dir in cand:
                cand.remove(last_dir)
            action = random.choice(cand) if cand else "left"
        cmd_ok, cmd_event = _exec_ptz_command(action, amount=1, reason="orient")
        moved = bool(cmd_ok) and (not dry_run)
        st["last_ptz_command_ok"] = bool(cmd_ok)
        st["last_ptz_command_error"] = str(cmd_event.get("error") or "")
        if moved:
            st["last_orient_ts"] = now_ts
            st["last_dir"] = action
            st["fix_until_ts"] = now_ts + random.randint(fix_min, fix_max)

    else:
        # fixate
        moved = False
        action = ""

    if _deadline_hit():
        st["last_run_ts"] = now_ts
        st["last_reason"] = "timeout_budget"
        _save_state(st_path, st)
        out = {"ok": True, "ts": int(now_ts), "moved": bool(moved), "mode": str(mode), "action": (action or ""), "reason": "timeout budget", "frame_source": frame_src, "frame_age_sec": round(float(age), 2) if age is not None else None}
        try:
            out["sharpness"] = round(float(sharp), 2)
        except Exception:
            pass
        try:
            out["motion"] = round(float(mot), 4)
        except Exception:
            pass
        return out

    # Optional zoom micro-adjust (off by default)
    if moved and zoom_en:
        try:
            z_cur = int(zoom_c.get("value"))
            z_min = int(zoom_c.get("min"))
            z_max = int(zoom_c.get("max"))
            # keep near default: if too low and motion high -> zoom in, else if too high and motion low -> zoom out
            if mot >= motion_high and z_cur < z_max:
                cmd_ok, cmd_event = _exec_ptz_command("zoom_in", amount=zoom_step, reason="zoom")
                if cmd_ok:
                    action = action or "zoom_in"
            elif mot < motion_low and z_cur > z_min:
                cmd_ok, cmd_event = _exec_ptz_command("zoom_out", amount=zoom_step, reason="zoom")
                if cmd_ok:
                    action = action or "zoom_out"
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Attention Gain (post) + Reward log
    # ---------------------------------------------------------------------
    post_pair = None
    gain = None
    if moved and (not dry_run) and action and pre_pair is not None and cv2 is not None and np is not None and (not once_skip_attention_gain) and (not _deadline_hit()):
        try:
            settle_ms = _env_int("OROMA_PTZ_ATTENTION_SETTLE_MS", 300)
            settle_ms = max(0, min(settle_ms, 2000))
        except Exception:
            settle_ms = 300
        if settle_ms > 0 and _remaining_deadline() > ((settle_ms / 1000.0) + 0.2):
            time.sleep(settle_ms / 1000.0)

        try:
            gap_ms = _env_int("OROMA_PTZ_ATT_SAMPLE_GAP_MS", 60)
            gap_ms = max(0, min(gap_ms, 500))
        except Exception:
            gap_ms = 60

        try:
            if _deadline_hit():
                f1 = None
            else:
                f1, _t1 = get_frame_with_ts(ensure_start=ensure_cam)
            if f1 is not None:
                if gap_ms > 0 and _remaining_deadline() > ((gap_ms / 1000.0) + 0.2):
                    time.sleep(gap_ms / 1000.0)
                if _deadline_hit():
                    f2 = None
                else:
                    f2, _t2 = get_frame_with_ts(ensure_start=ensure_cam)
                if f2 is not None:
                    post_pair = _att_pair(f1, f2)
                    try:
                        s1p = _downsample_gray(f1)
                        s2p = _downsample_gray(f2)
                        cent_p = _motion_centroid(s1p, s2p, w=64, h=36)
                        post_motion_feat = {"centroid": cent_p, "dist": float(cent_p.get("dist", 0.0)), "energy": float(cent_p.get("energy", 0.0))}
                    except Exception:
                        post_motion_feat = None
        except Exception:
            post_pair = None

        if post_pair is not None:
            try:
                gain = float(post_pair.get("score", 0.0) - pre_pair.get("score", 0.0))
            except Exception:
                gain = 0.0

            now_ts_att = int(time.time())
            try:
                insert_metric("ptz:att_motion_pre", float(pre_pair.get("motion", 0.0)), ts=now_ts_att)
                insert_metric("ptz:att_sharp_pre", float(pre_pair.get("sharp", 0.0)), ts=now_ts_att)
                insert_metric("ptz:att_score_pre", float(pre_pair.get("score", 0.0)), ts=now_ts_att)
                insert_metric("ptz:att_motion_post", float(post_pair.get("motion", 0.0)), ts=now_ts_att)
                insert_metric("ptz:att_sharp_post", float(post_pair.get("sharp", 0.0)), ts=now_ts_att)
                insert_metric("ptz:att_score_post", float(post_pair.get("score", 0.0)), ts=now_ts_att)
                insert_metric("ptz:att_gain", float(gain), ts=now_ts_att)
                insert_metric(f"ptz:att_action:{action}", 1.0, ts=now_ts_att)
            except Exception:
                pass

            # RewardLogger (best-effort)
            try:
                rl = _get_reward_logger()
                if rl:
                    # Add PTZ pose snapshot for DreamWorker policy aggregation.
                    ptz_pose = None
                    try:
                        ptz_pose = _ptz_status()
                    except Exception:
                        ptz_pose = None

                    # Policy state hash (aligns with DreamWorker aggregation).
                    try:
                        z_cur2 = int(zoom_c.get("value"))
                    except Exception:
                        z_cur2 = 0
                    try:
                        st_hash = _policy_state_hash(
                            pan=pan_cur,
                            tilt=tilt_cur,
                            zoom=z_cur2,
                            pan_min=pan_min,
                            pan_max=pan_max,
                            tilt_min=tilt_min,
                            tilt_max=tilt_max,
                        )
                    except Exception:
                        st_hash = ""

                    rl.log(
                        source="ptz/attention_gain",
                        step=0,
                        reward=float(gain),
                        raw={
                            "mode": str(mode),
                            "state_hash": str(st_hash),
                            "action": str(action),
                            "pre": pre_pair,
                            "post": post_pair,
                            "gain": float(gain),
                            "motion_tick": float(mot),
                            "sharpness_tick": float(shp),
                            "ptz": ptz_pose,
                        },
                        ts=now_ts_att,
                    )
                    # Zusatz-Rewards (nur wenn wir stabile Post-Pairs haben)
                    try:
                        if str(mode) == "threat" and motion_state_hash_used and pre_motion_feat and post_motion_feat:
                            pre_d = float(pre_motion_feat.get("dist", 0.0))
                            post_d = float(post_motion_feat.get("dist", 0.0))
                            # Reward: Distanz zur Mitte reduzieren (positiv ist gut)
                            r = float(pre_d - post_d)
                            rl.log(
                                source="ptz/motion_focus",
                                step=0,
                                reward=float(r),
                                raw={
                                    "mode": "threat",
                                    "state_hash": str(motion_state_hash_used),
                                    "action": str(action),
                                    "pre_dist": float(pre_d),
                                    "post_dist": float(post_d),
                                    "pre_energy": float(pre_motion_feat.get("energy", 0.0)),
                                    "post_energy": float(post_motion_feat.get("energy", 0.0)),
                                    "pre": pre_pair,
                                    "post": post_pair,
                                    "ptz": ptz_pose,
                                },
                                ts=now_ts_att,
                            )
                        if str(mode) == "probe" and probe_state_hash_used:
                            detect_thr = _env_float("OROMA_PTZ_PROBE_MOTION_DETECT_THR", float(motion_high))
                            m_post = float(post_pair.get("motion", 0.0) or 0.0) if isinstance(post_pair, dict) else 0.0
                            detected = (m_post >= float(detect_thr))
                            # kleiner negativer Reward bei "nichts gefunden", positiver bei Fund
                            r = 1.0 if detected else -0.02
                            rl.log(
                                source="ptz/audio_probe",
                                step=0,
                                reward=float(r),
                                raw={
                                    "mode": "probe",
                                    "state_hash": str(probe_state_hash_used),
                                    "action": str(action),
                                    "detected": bool(detected),
                                    "motion_post": float(m_post),
                                    "detect_thr": float(detect_thr),
                                    "post": post_pair,
                                    "ptz": ptz_pose,
                                },
                                ts=now_ts_att,
                            )
                    except Exception:
                        pass
            except Exception:
                pass

    # Refresh PTZ status after action (for correct metrics)
    try:
        ptz2 = _ptz_status()
        if isinstance(ptz2, dict) and ptz2.get("supported"):
            _log_ptz_status_metrics(action=action, ptz=ptz2)
    except Exception:
        pass

    # Log basic mode/motion/sharpness metrics for analysis
    try:
        insert_metric("ptz:motion", float(mot))
        insert_metric("ptz:sharpness", float(shp))
        insert_metric("ptz:mode", float({"fixate": 0, "orient": 1, "scan": 2}.get(mode, -1)))
    except Exception:
        pass

    # Update state (store current small frame with explicit dimensions for future compatibility)
    if cur_small is not None:
        st["prev_small"] = cur_small.hex()
        st["prev_small_w"] = int(motion_w)
        st["prev_small_h"] = int(motion_h)
        st["prev_small_len"] = int(len(cur_small))
    st["last_active_ts"] = last_active
    st["last_frame_source"] = str(frame_src or "hub")
    st["last_frame_age_sec"] = round(float(age), 3)
    st["last_frame_diag"] = dict(frame_diag or {})
    st["last_motion_prev_available"] = bool(motion_prev_available)
    st["last_motion_cur_available"] = bool(motion_cur_available)
    st["last_motion_value"] = round(float(mot), 6)
    st["last_sharpness_value"] = round(float(shp), 3)
    st["last_mode"] = str(mode)
    st["last_action"] = str(action or "")
    st["last_skip_reason"] = str(skip_reason or "")
    st["last_threat_active"] = bool(threat_active)
    st["last_threat_energy"] = round(float(e), 6) if "e" in locals() else 0.0
    st["last_threat_dist"] = round(float(dist), 6) if "dist" in locals() else 0.0
    st["last_motion_gate_low"] = round(float(motion_low), 6)
    st["last_threat_gate_high"] = round(float(threat_high), 6)
    st["last_ptz_command_events"] = ptz_cmd_events[-6:]
    st["last_ptz_command_count"] = int(len(ptz_cmd_events))
    if ptz_cmd_events:
        try:
            st["last_ptz_command_ok"] = bool(ptz_cmd_events[-1].get("ok"))
            st["last_ptz_command_error"] = str(ptz_cmd_events[-1].get("error") or "")
            st["last_ptz_command_backend"] = str(ptz_cmd_events[-1].get("backend") or "")
            st["last_ptz_command_amount"] = int(ptz_cmd_events[-1].get("amount") or 0)
        except Exception:
            pass

    _save_state(st_path, st)

    # Normalized reason string for UI/metrics consumers.
    # - If a decision is skipped, 'skip_reason' describes why.
    # - Otherwise we provide the current mode (fixate/orient/scan) for lightweight introspection.
    reason = str(skip_reason or mode or "")

    out = {
        "ok": True,
        "ts": int(now_ts),
        "mode": mode,
        "moved": bool(moved) and (not dry_run),
        "dry_run": bool(dry_run),
        "motion": round(float(mot), 4),
        "sharpness": round(float(shp), 2),
        "action": action,
        "reason": reason,
        "frame_source": str(frame_src or (st.get("last_frame_source") or "hub")),
        "frame_age_sec": round(float(age), 3),
        "frame_diag": dict(frame_diag or {}),
        "motion_prev_available": bool(motion_prev_available),
        "motion_cur_available": bool(motion_cur_available),
        "motion_low": round(float(motion_low), 6),
        "threat_high": round(float(threat_high), 6),
        "threat_active": bool(threat_active),
        "state_path": st_path,
        "ptz_command_count": int(len(ptz_cmd_events)),
        "ptz_command_ok": bool(ptz_cmd_events[-1].get("ok")) if ptz_cmd_events else None,
        "ptz_command_backend": str(ptz_cmd_events[-1].get("backend") or "") if ptz_cmd_events else str(ptz_backend_name or ""),
        "ptz_command_amount": int(ptz_cmd_events[-1].get("amount") or 0) if ptz_cmd_events else 0,
        "ptz_command_error": str(ptz_cmd_events[-1].get("error") or "") if ptz_cmd_events else "",
        "ptz_backend": str(ptz_backend_name or ""),
    }
    try:
        out["threat_dist"] = round(float(dist), 4)
        out["threat_energy"] = round(float(e), 4)
    except Exception:
        pass
    if str(mode) == "threat":
        try:
            out["threat_dx"] = round(float(dx), 4)
            out["threat_dy"] = round(float(dy), 4)
            out["threat_dist"] = round(float(dist), 4)
            out["threat_energy"] = round(float(e), 4)
            out["threat_deadzone_used"] = round(float(dyn_dead), 4)
            out["threat_burst_steps"] = int(st.get("last_threat_burst_amount") or 0)
            out["threat_amount"] = int(st.get("last_threat_amount") or 0)
            out["threat_amount_dynamic"] = bool(st.get("last_threat_amount_dynamic"))
            out["threat_axis"] = str(st.get("last_threat_axis") or "")
            out["threat_axis_strength"] = round(float(st.get("last_threat_axis_strength") or 0.0), 4)
            out["threat_raw_action"] = str(st.get("last_threat_raw_action") or "")
            out["threat_mapped_action"] = str(st.get("last_threat_mapped_action") or "")
            out["threat_follow_invert_x"] = bool(st.get("last_threat_follow_invert_x"))
            out["threat_follow_invert_y"] = bool(st.get("last_threat_follow_invert_y"))
            out["threat_dx_bin"] = int(dx_bin)
            out["threat_dy_bin"] = int(dy_bin)
        except Exception:
            pass
    return out


def _should_emit_one_line_log(res: dict) -> bool:
    """
    Reduziert PTZ-Logspam fuer Orchestrator-One-Shot-Laeufe.

    Verhalten:
    - immer loggen bei verbose/Fehlern
    - loggen bei Modus/Aktion/Reason-Aenderung
    - identische Wiederholungen nur in groesserem Intervall erneut loggen

    Persistenz:
    - nutzt den bestehenden PTZ-State (state_path aus run_once), damit die
      Drosselung auch pro Subprozess-Lauf uebergreifend funktioniert.
    """
    try:
        if not bool(res.get("ok")):
            return True
        st_path = str(res.get("state_path") or "").strip()
        if not st_path:
            return True
        now_ts = int(res.get("ts") or time.time())
        mode = str(res.get("mode") or "")
        action = str(res.get("action") or "")
        reason = str(res.get("reason") or "")
        moved = bool(res.get("moved"))
        src = str(res.get("frame_source") or "")
        age = res.get("frame_age_sec")
        sig = {
            "mode": mode,
            "action": action,
            "reason": reason,
            "moved": moved,
            "frame_source": src,
            "frame_age_bucket": None if age is None else round(float(age), 1),
            "ptz_command_ok": res.get("ptz_command_ok"),
            "ptz_command_amount": int(res.get("ptz_command_amount") or 0),
            "ptz_command_error": str(res.get("ptz_command_error") or "")[:120],
            "threat_dx": res.get("threat_dx"),
            "threat_dy": res.get("threat_dy"),
            "threat_amount": res.get("threat_amount"),
        }
        repeat_sec = _env_int("OROMA_PTZ_ATTENTION_LOG_REPEAT_SEC", 60)
        prev = {}
        try:
            with open(st_path, "r", encoding="utf-8") as f:
                prev = json.load(f) or {}
        except Exception:
            prev = {}
        prev_sig = prev.get("last_log_signature") if isinstance(prev, dict) else None
        prev_ts = int((prev or {}).get("last_log_ts") or 0) if isinstance(prev, dict) else 0
        should = False
        if not isinstance(prev_sig, dict) or prev_sig != sig:
            should = True
        elif prev_ts <= 0 or (now_ts - prev_ts) >= max(5, int(repeat_sec)):
            should = True
        if should:
            try:
                if isinstance(prev, dict):
                    prev["last_log_signature"] = sig
                    prev["last_log_ts"] = now_ts
                    with open(st_path, "w", encoding="utf-8") as f:
                        json.dump(prev, f, ensure_ascii=False, indent=2, sort_keys=True)
            except Exception:
                pass
        return bool(should)
    except Exception:
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="One-shot run (recommended for orchestrator).")
    ap.add_argument("--verbose", action="store_true", help="Verbose JSON output.")
    args = ap.parse_args()

    res = run_once(verbose=bool(args.verbose), once_mode=bool(args.once))
    if args.verbose:
        print(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        # Orchestrator-friendly single line, aber gedrosselt bei identischen
        # Wiederholungen (z. B. luma_recover up/down Serien oder lange fixate-Phasen).
        try:
            if _should_emit_one_line_log(res):
                ts = int(res.get("ts") or time.time())
                moved = "1" if res.get("moved") else "0"
                mode = str(res.get("mode") or "?")
                action = str(res.get("action") or "-")
                reason = str(res.get("reason") or "-")
                src = str(res.get("frame_source") or "-")
                age = res.get("frame_age_sec")
                age_s = "-" if age is None else f"{float(age):.1f}s"
                cmd_ok = res.get("ptz_command_ok")
                cmd_s = "-" if cmd_ok is None else ("1" if cmd_ok else "0")
                cmd_amt = res.get("ptz_command_amount") or 0
                extra = ""
                if mode == "threat":
                    try:
                        extra = (
                            f" dx={float(res.get('threat_dx', 0.0)):.4f}"
                            f" dy={float(res.get('threat_dy', 0.0)):.4f}"
                            f" dist={float(res.get('threat_dist', 0.0)):.4f}"
                            f" energy={float(res.get('threat_energy', 0.0)):.4f}"
                            f" dead={float(res.get('threat_deadzone_used', 0.0)):.4f}"
                            f" axis={str(res.get('threat_axis') or '-') }"
                            f" strength={float(res.get('threat_axis_strength', 0.0)):.4f}"
                            f" raw={str(res.get('threat_raw_action') or '-')}"
                            f" mapped={str(res.get('threat_mapped_action') or '-')}"
                            f" invx={1 if res.get('threat_follow_invert_x') else 0}"
                            f" invy={1 if res.get('threat_follow_invert_y') else 0}"
                            f" amount_dyn={1 if res.get('threat_amount_dynamic') else 0}"
                        )
                    except Exception:
                        extra = ""
                print(f"[ptz_attention_loop] ts={ts} ok={int(bool(res.get('ok')))} moved={moved} mode={mode} action={action} reason={reason} frame={src} age={age_s} cmd_ok={cmd_s} cmd_amount={cmd_amt}{extra}")
        except Exception:
            # Never fail the job due to logging.
            pass
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_motor_worker.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     PTZ Motor Worker – schneller persistenter Servo-/Reflex-Loop
# Version:   v3.7.3+ptz-motor-worker-v1.5b
# Stand:     2026-05-16
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieser Worker trennt die schnelle PTZ-Motorik vom schweren ORÓMA-
# Orchestrator-One-Shot-Pfad. Der bisherige Aufruf
#
#     python -m core.ptz_attention_loop --once
#
# ist fuer Diagnose, Scan/Orient/Fixate und langsame Aufmerksamkeit geeignet,
# aber nicht fuer sichtbares Personen-/Bewegungs-Following: jeder Tick startet
# einen neuen Python-Prozess, initialisiert Module, prueft Backend/State und kann
# durch DB-/Frame-Wartezeiten gebremst werden. Motorik braucht dagegen einen
# dauerhaften, latenzarmen Loop.
#
# ARCHITEKTUR-ROLLE
# ─────────────────
#   - ptz_attention_loop.py     = langsame Aufmerksamkeit / Diagnose / Kognition
#   - ptz_motor_worker.py       = schneller Motorik-/Servo-Pfad (dieses Modul)
#   - ptz_attention_state.json  = kognitiver Attention-Zustand
#   - ptz_motor_state.json      = Motorik-Zustand/Heartbeat/Telemetrie
#
# WICHTIGE INVARIANTEN
# ────────────────────
#   - Keine Haupt-DB-Schreibzugriffe im Hot-Path.
#   - Kein ensure_schema() pro Tick.
#   - Kein PTZ-Backend-Autodetect pro Tick; Device/Controller werden einmal beim
#     Start initialisiert und danach wiederverwendet.
#   - Frame-Zugriff nur ueber den passiven Fast-Cache:
#       core.camera_hub.get_cached_frame_with_ts_fast_diag()
#     Dieser Pfad darf keinen Kamera-Start erzwingen.
#   - State-JSON wird rate-limited und atomar geschrieben.
#   - Headless-only: keine GUI-, Qt-, Wayland- oder X11-Abhaengigkeit.
#
# START / TEST
# ────────────
# Manuell fuer Phase 3a:
#
#   cd /opt/ai/oroma
#   sudo -u oroma env \
#     PYTHONPATH=/opt/ai/oroma \
#     OROMA_BASE=/opt/ai/oroma \
#     OROMA_PTZ_DEVICE=/dev/v4l/by-id/usb-EMEET_EMEET_PIXY_A250607001103370-video-index0 \
#     python3 tools/ptz_motor_worker.py --verbose
#
# Kurztest:
#
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     python3 tools/ptz_motor_worker.py --once --verbose
#
# PRODUKTIV-HINWEIS
# ─────────────────
# Solange dieser Worker laeuft, soll der alte Orchestrator-One-Shot fuer PTZ
# deaktiviert sein:
#
#   OROMA_ORCH_ENABLE_PTZ_ATTENTION=0
#
# Sonst koennten zwei unabhaengige Pfade gleichzeitig PTZ-Kommandos senden.
#
# ENVIRONMENT
# ───────────
# Basis:
#   OROMA_BASE                         Default /opt/ai/oroma
#   OROMA_PTZ_DEVICE                   Optional; bevorzugt /dev/v4l/by-id/...
#   OROMA_PTZ_MOTOR_ENABLE             Default 1
#   OROMA_PTZ_MOTOR_STATE_PATH         Default {BASE}/data/state/ptz_motor_state.json
#
# Loop/Frequenz:
#   OROMA_PTZ_MOTOR_HZ                 Default 3.0  (Start konservativ)
#   OROMA_PTZ_MOTOR_MAX_FRAME_AGE      Default 1.50 Sekunden
#   OROMA_PTZ_MOTOR_STATE_WRITE_SEC    Default 1.0
#   OROMA_PTZ_MOTOR_SUMMARY_SEC        Default 20.0
#   OROMA_PTZ_MOTOR_CENTER_ON_START    Default 1   (pan/tilt auf 0/0 beim Start)
#   OROMA_PTZ_MOTOR_HOME_PAN           Default 0
#   OROMA_PTZ_MOTOR_HOME_TILT          Default 0
#   OROMA_PTZ_MOTOR_HOME_ZOOM          Optional; leer = Zoom nicht verändern
#   OROMA_PTZ_MOTOR_HOME_SETTLE_SEC    Default 0.70
#
# Motion/Folgen:
#   OROMA_PTZ_MOTOR_MOTION_W           Default 96
#   OROMA_PTZ_MOTOR_MOTION_H           Default 54
#   OROMA_PTZ_MOTOR_DEADZONE           Default 0.030
#   OROMA_PTZ_MOTOR_ENERGY_MIN         Default 0.010
#   OROMA_PTZ_MOTOR_VERTICAL_GAIN      Default 1.35
#   OROMA_PTZ_FOLLOW_INVERT_X          Default 1   (EMEET/Live-Test 2026-05-07)
#   OROMA_PTZ_FOLLOW_INVERT_Y          Default 0
#
# Motorik-Ausgabe:
#   OROMA_PTZ_MOTOR_AMOUNT_MIN         Default 2
#   OROMA_PTZ_MOTOR_AMOUNT_MAX         Default 3
#   OROMA_PTZ_MOTOR_DIST_SCALE         Default 0.18
#   OROMA_PTZ_MOTOR_E_SCALE            Default 0.060
#   OROMA_PTZ_MOTOR_REVERSAL_GUARD_SEC Default 0.80
#   OROMA_PTZ_MOTOR_REVERSAL_RATIO     Default 1.55
#
# Servo-Damping / Calm-Follow v1.4d:
#   OROMA_PTZ_MOTOR_MOVE_COOLDOWN_TICKS Default 3
#       Tick-basierter Motor-Cooldown nach erfolgreichem PTZ-Kommando. Bei 3 Hz
#       entsprechen 3 Ticks ungefaehr einer Sekunde. Kein sleep/blocking im Loop.
#       Starke Signale und Eye-Pair-Salience duerfen den Cooldown ueberschreiben.
#   OROMA_PTZ_MOTOR_MICRO_GUARD_ENABLE  Default 1
#   OROMA_PTZ_MOTOR_MICRO_GUARD_DIST_FACTOR Default 1.50
#   OROMA_PTZ_MOTOR_MICRO_GUARD_CONF_MAX Default 0.120
#       Blockiert sehr kleine, schwache Nachfuehrbewegungen vor Axis-Lock, damit
#       der Servo-Pfad ruhiger wirkt und nicht bei jedem kleinen Motion-Centroid
#       sofort ein Motorbefehl entsteht.
#
# Motion-Stabilisierung v1.1:
#   OROMA_PTZ_MOTOR_UPPER_BIAS_ENABLE  Default 1
#   OROMA_PTZ_MOTOR_UPPER_GAIN         Default 1.35
#   OROMA_PTZ_MOTOR_LOWER_DAMPING      Default 0.70
#   OROMA_PTZ_MOTOR_HISTORY_N          Default 5
#   OROMA_PTZ_MOTOR_DOWN_CONFIRM_MIN   Default 3
#   OROMA_PTZ_MOTOR_STABILITY_MIN      Default 2
#   OROMA_PTZ_MOTOR_STRONG_SIGNAL_BYPASS Default 0.22
#
# Target-Stabilisierung v1.2a:
#   OROMA_PTZ_MOTOR_TARGET_ENABLE     Default 1
#   OROMA_PTZ_MOTOR_TARGET_DECAY      Default 0.85  (exponentiell pro Tick ohne Bestaetigung)
#   OROMA_PTZ_MOTOR_TARGET_ALPHA      Default 0.45  (sanfte dx/dy-Aktualisierung)
#   OROMA_PTZ_MOTOR_TARGET_CONF_MIN   Default 0.020 (Mindestvertrauen fuer Hold/Anzeige)
#   OROMA_PTZ_MOTOR_TARGET_OVERRIDE_RATIO Default 1.80 (starkes neues Signal ersetzt Ziel hart)
#   OROMA_PTZ_MOTOR_TARGET_HOLD_TICKS Default 6
#   OROMA_PTZ_MOTOR_TARGET_HOLD_COMMAND Default 0  (Hold stabilisiert, sendet default keine Kommandos)
#
# Eye-/Head-Hold-Bias v1.4c:
#   OROMA_PTZ_MOTOR_EYE_HOLD_BIAS_ENABLE Default 1
#   OROMA_PTZ_MOTOR_EYE_HOLD_TICKS       Default 8
#   OROMA_PTZ_MOTOR_EYE_HOLD_CONF_MIN    Default 0.060
#   OROMA_PTZ_MOTOR_EYE_HOLD_OVERRIDE_RATIO Default 1.60
#   OROMA_PTZ_MOTOR_EYE_HOLD_COMMAND     Default 1
#       Wenn zuletzt ein Eye-Pair-/Head-aehnlicher Candidate ein echtes Follow-
#       Kommando erzeugt hat, darf das geglaettete Ziel kurz aktiv gehalten
#       werden. Schwache neue Motion-/Edge-Signale duerfen dieses Ziel nicht
#       sofort wegziehen; deutlich staerkere Signale koennen es weiterhin
#       ueberschreiben. Das ist ein Attention-Hold, keine Personenerkennung.
#
# Candidate-/Axis-Stabilisierung v1.2b:
#   OROMA_PTZ_MOTOR_AXIS_LOCK_ENABLE  Default 1
#   OROMA_PTZ_MOTOR_AXIS_LOCK_TICKS   Default 4   (kurzes Achsen-Gedaechtnis nach echtem Follow)
#   OROMA_PTZ_MOTOR_AXIS_LOCK_OVERRIDE_RATIO Default 1.65
#       Frische Querachse darf den Lock nur brechen, wenn sie deutlich staerker ist.
#       Der Lock wird nur nach erfolgreichem PTZ-Kommando gesetzt; UI/State bleiben read-only.
#
# Eye-Pair-/Face-like-Salience v2a:
#   OROMA_PTZ_MOTOR_EYE_PAIR_ENABLE   Default 1
#   OROMA_PTZ_MOTOR_EYE_PAIR_REQUIRE_MOTION Default 1
#       Eye-Pair wirkt initial nur als zusaetzlicher Candidate/Confidence-Anker,
#       solange Motion vorhanden ist. Reines Eye-Following kann spaeter separat
#       aktiviert werden, ohne den Motion-Fallback zu entfernen.
#   OROMA_PTZ_MOTOR_EYE_PAIR_MIN_CONF Default 0.18
#   OROMA_PTZ_MOTOR_EYE_PAIR_SCORE_GAIN Default 1.20
#   OROMA_PTZ_MOTOR_EYE_PAIR_MAX_ANGLE_DEG Default 45
#   OROMA_PTZ_MOTOR_EYE_PAIR_MIN_SEP   Default 0.07 (relativ zur Bildbreite)
#   OROMA_PTZ_MOTOR_EYE_PAIR_MAX_SEP   Default 0.46 (relativ zur Bildbreite)
#   OROMA_PTZ_MOTOR_EYE_PAIR_MOTION_RADIUS Default 0.35
#       Lokales Motion-Gate im gemeinsamen normalisierten Raum (-1..+1).
#       Ein Augenpaar wirkt nur, wenn sein Mittelpunkt nahe am Motion-Centroid liegt.
#   OROMA_PTZ_MOTOR_EYE_PAIR_MIN_FRAMES_STABLE Default 2
#       Temporales Gate: Kandidat muss in aufeinanderfolgenden Ticks stabil sein.
#   OROMA_PTZ_MOTOR_EYE_PAIR_STABLE_RADIUS Default 0.12
#       Toleranz fuer temporale Stabilitaet, ebenfalls im normalisierten Raum.
#       Diese Stufe ist KEINE Personenerkennung und KEINE harte Face-Detection.
#       Sie sucht nur zwei plausible dunkle Blobs als weiches Salience-Signal.
#
# Expected-Face-Region / Head-Context v1.5b:
#   OROMA_PTZ_MOTOR_FACE_REGION_ENABLE Default 1
#   OROMA_PTZ_MOTOR_FACE_REGION_BONUS  Default 0.28
#   OROMA_PTZ_MOTOR_FACE_REGION_MIN_SCORE Default 0.18
#   OROMA_PTZ_MOTOR_FACE_REGION_MIN_STD Default 4.0
#   OROMA_PTZ_MOTOR_FACE_REGION_HORIZ_MAX Default 0.82
#   OROMA_PTZ_MOTOR_FACE_REGION_GRAD_MIN Default 0.80
#   OROMA_PTZ_MOTOR_EYE_FACE_RANK_THRESHOLD Default 0.85
#   OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST Default 1.55
#   OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST_MIN Default 0.40
#       Face-assisted Motion-Radius v1.5c: Wenn ein Eye-Pair bereits eine
#       plausible Face-Region mit ausreichend score_norm besitzt, darf sein
#       lokales Motion-Gate einen groesseren effektiven Radius nutzen. Das
#       rettet Kopf-/Augen-Kandidaten, deren Motion-Centroid kurz auf Brille,
#       Schulter oder Hand statt exakt auf dem Augenmittelpunkt liegt. Ohne
#       Face-Region bleibt der konservative Basisradius aktiv.
#       Kein Ellipse-Fit und keine Face Detection. Aus Augenabstand und
#       Augenmittelpunkt wird eine erwartete Kopf-/Gesichts-ROI abgeleitet.
#       v1.5b nutzt dafuer einen sehr kleinen NumPy-Score auf dem ohnehin
#       vorhandenen Downsample-Graybild: Mittelwert pro ROI-Zeile, Differenz
#       aufeinanderfolgender Zeilen und Varianz dieser vertikalen Staffelung.
#       Horizontale/Moebelkanten-Dominanz wird als Penalty behandelt. Der Score
#       ist ein Soft-Ranking-Signal fuer Eye/Face-Salience; Motion bleibt
#       Fallback. Keine schweren Modelle, keine Cascade, kein Per-Pixel-Hotpath.
#
# LOGGING
# ───────
#   --verbose zeigt einzelne Tick-Entscheidungen.
#   Periodische Summary-Zeilen sind bewusst kompakt und DB-frei.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    from core.camera_hub import get_cached_frame_with_ts_fast_diag  # type: ignore
except Exception:  # pragma: no cover
    get_cached_frame_with_ts_fast_diag = None  # type: ignore

try:
    from wrappers.ptz_controller import PTZController  # type: ignore
except Exception:  # pragma: no cover
    PTZController = None  # type: ignore

_STOP = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return int(default)


def _env_optional_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw_s = str(raw).strip()
    if not raw_s or raw_s.lower() in ("none", "null", "off", "false"):
        return None
    try:
        return int(raw_s)
    except Exception:
        return None


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _clamp_float(v: float, lo: float, hi: float) -> float:
    try:
        return max(float(lo), min(float(hi), float(v)))
    except Exception:
        return float(lo)


def _clamp_int(v: int, lo: int, hi: int) -> int:
    try:
        return max(int(lo), min(int(hi), int(v)))
    except Exception:
        return int(lo)


def _now() -> float:
    return time.time()


def _state_path(base: str) -> Path:
    return Path(os.environ.get("OROMA_PTZ_MOTOR_STATE_PATH", os.path.join(base, "data", "state", "ptz_motor_state.json")))


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception as e:
        print(f"[ptz_motor_worker] state_write_error={e}", file=sys.stderr, flush=True)


def _downsample_gray(frame: object, w: int, h: int) -> Optional[bytes]:
    if cv2 is None:
        return None
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (int(w), int(h)), interpolation=cv2.INTER_AREA)
        return small.tobytes()
    except Exception:
        return None


def _empty_centroid() -> Dict[str, float]:
    return {
        "energy": 0.0,
        "dx": 0.0,
        "dy": 0.0,
        "dist": 0.0,
        "dx_raw": 0.0,
        "dy_raw": 0.0,
        "dist_raw": 0.0,
        "energy_weighted": 0.0,
        "upper_bias": 0.0,
    }


def _row_bias_weights(h: int, upper_gain: float, lower_damping: float) -> Optional[object]:
    """Return per-row upper-body weights without imposing a fixed target height.

    The image coordinate system is normalized as y=-1 at the top, y=0 at the
    center and y=+1 at the bottom. Upper rows receive a gentle gain, lower rows
    are gently damped. This is intentionally a soft salience bias, not a hard
    crop, so children, seated people and lower face-like candidates stay visible.
    """
    if np is None:
        return None
    try:
        hh = max(1, int(h))
        y_norm = np.linspace(-1.0, 1.0, hh, dtype=np.float32)
        top_weight = 1.0 + ((-np.minimum(y_norm, 0.0)) * max(0.0, float(upper_gain) - 1.0))
        bottom_weight = 1.0 - (np.maximum(y_norm, 0.0) * max(0.0, 1.0 - float(lower_damping)))
        weights = top_weight * bottom_weight
        return np.clip(weights, 0.05, 5.0).astype(np.float32)
    except Exception:
        return None


def _centroid_from_weighted_diff(diff_obj: object, w: int, h: int, raw_total: float) -> Dict[str, float]:
    """Compute normalized centroid from a NumPy diff image."""
    if np is None:
        return _empty_centroid()
    try:
        diff = diff_obj
        total = float(np.sum(diff))
        expected = int(w) * int(h)
        energy_weighted = float(total / float(max(1, expected) * 255.0))
        if total <= 0.0:
            out = _empty_centroid()
            out["energy"] = float(raw_total / float(max(1, expected) * 255.0))
            out["energy_weighted"] = energy_weighted
            return out
        xs = np.arange(int(w), dtype=np.float32)
        ys = np.arange(int(h), dtype=np.float32)
        col_sum = np.sum(diff, axis=0)
        row_sum = np.sum(diff, axis=1)
        cx = float(np.sum(col_sum * xs) / total)
        cy = float(np.sum(row_sum * ys) / total)
        dx = ((cx / float(max(1, int(w) - 1))) * 2.0) - 1.0
        dy = ((cy / float(max(1, int(h) - 1))) * 2.0) - 1.0
        dist = math.sqrt((dx * dx) + (dy * dy))
        return {
            "energy": float(raw_total / float(max(1, expected) * 255.0)),
            "dx": float(dx),
            "dy": float(dy),
            "dist": float(dist),
            "dx_raw": float(dx),
            "dy_raw": float(dy),
            "dist_raw": float(dist),
            "energy_weighted": energy_weighted,
            "upper_bias": 0.0,
        }
    except Exception:
        return _empty_centroid()


def _motion_centroid(
    prev: Optional[bytes],
    cur: Optional[bytes],
    w: int,
    h: int,
    upper_bias_enabled: bool = False,
    upper_gain: float = 1.35,
    lower_damping: float = 0.70,
) -> Dict[str, float]:
    if prev is None or cur is None:
        return _empty_centroid()
    try:
        expected = int(w) * int(h)
        if len(prev) != len(cur) or len(cur) != expected:
            return _empty_centroid()

        if np is not None:
            arr_cur = np.frombuffer(cur, dtype=np.uint8).reshape((int(h), int(w)))
            arr_prev = np.frombuffer(prev, dtype=np.uint8).reshape((int(h), int(w)))
            diff_raw = np.abs(arr_cur.astype(np.int16) - arr_prev.astype(np.int16)).astype(np.float32)
            raw_total = float(np.sum(diff_raw))
            raw = _centroid_from_weighted_diff(diff_raw, int(w), int(h), raw_total)
            raw["dx_raw"] = float(raw.get("dx", 0.0))
            raw["dy_raw"] = float(raw.get("dy", 0.0))
            raw["dist_raw"] = float(raw.get("dist", 0.0))
            if bool(upper_bias_enabled):
                weights = _row_bias_weights(int(h), float(upper_gain), float(lower_damping))
                if weights is not None:
                    diff_biased = diff_raw * weights.reshape((int(h), 1))
                    biased = _centroid_from_weighted_diff(diff_biased, int(w), int(h), raw_total)
                    biased["dx_raw"] = float(raw.get("dx_raw", 0.0))
                    biased["dy_raw"] = float(raw.get("dy_raw", 0.0))
                    biased["dist_raw"] = float(raw.get("dist_raw", 0.0))
                    biased["energy"] = float(raw.get("energy", 0.0))
                    biased["upper_bias"] = float(biased.get("dy", 0.0)) - float(raw.get("dy_raw", 0.0))
                    return biased
            return raw

        # Fallback path: pure Python, kept for environments without NumPy.
        total = 0.0
        wx = 0.0
        wy = 0.0
        for i in range(expected):
            d = int(cur[i]) - int(prev[i])
            if d < 0:
                d = -d
            if d <= 0:
                continue
            x = i % int(w)
            y = i // int(w)
            weight = 1.0
            if bool(upper_bias_enabled) and int(h) > 1:
                y_norm = ((float(y) / float(max(1, int(h) - 1))) * 2.0) - 1.0
                if y_norm < 0.0:
                    weight = 1.0 + ((-y_norm) * max(0.0, float(upper_gain) - 1.0))
                else:
                    weight = 1.0 - (y_norm * max(0.0, 1.0 - float(lower_damping)))
                weight = max(0.05, min(5.0, weight))
            wd = float(d) * float(weight)
            total += wd
            wx += wd * float(x)
            wy += wd * float(y)
        energy = float(sum(abs(int(cur[i]) - int(prev[i])) for i in range(expected)) / float(max(1, expected) * 255.0))
        if total <= 0.0:
            out = _empty_centroid()
            out["energy"] = energy
            return out
        cx = wx / total
        cy = wy / total
        dx = ((cx / float(max(1, int(w) - 1))) * 2.0) - 1.0
        dy = ((cy / float(max(1, int(h) - 1))) * 2.0) - 1.0
        dist = math.sqrt((dx * dx) + (dy * dy))
        return {"energy": energy, "dx": float(dx), "dy": float(dy), "dist": float(dist), "dx_raw": float(dx), "dy_raw": float(dy), "dist_raw": float(dist), "energy_weighted": energy, "upper_bias": 0.0}
    except Exception:
        return _empty_centroid()


def _autodetect_ptz_device() -> str:
    candidates = []
    patterns = [
        "/dev/v4l/by-id/*video-index*",
        "/dev/v4l/by-id/*video*",
        "/dev/video*",
    ]
    for pat in patterns:
        try:
            candidates.extend(sorted(glob.glob(pat)))
        except Exception:
            pass
    seen = set()
    for dev in candidates:
        if dev in seen:
            continue
        seen.add(dev)
        try:
            ctrl = PTZController(dev) if PTZController is not None else None
            if ctrl is None:
                continue
            st = ctrl.status() or {}
            if bool(st.get("supported")):
                return str(dev)
        except Exception:
            continue
    return ""


class MotorController:
    """Small wrapper around PTZController with one-time device initialization."""

    def __init__(self, device: str) -> None:
        if PTZController is None:
            raise RuntimeError("wrappers.ptz_controller.PTZController unavailable")
        self.device = str(device)
        self.ctrl = PTZController(self.device)
        st = self.ctrl.status() or {}
        if not bool(st.get("supported")):
            raise RuntimeError(f"PTZ device not supported: {self.device} reason={st.get('reason')}")
        self.last_error = ""

    def home(self, pan: int = 0, tilt: int = 0, zoom: Optional[int] = None, settle_sec: float = 0.70) -> bool:
        """Move camera to a deterministic motor origin.

        PTZController.set_absolute(pan=..., tilt=...) can be affected by the
        controller cooldown because it sets multiple V4L2 controls in sequence.
        The motor worker therefore sets each axis separately with a short wait
        so pan and tilt both reliably reach the requested origin.
        """
        ok = True
        try:
            ok = bool(self.ctrl.set_absolute(pan=int(pan))) and ok
            time.sleep(max(0.05, float(settle_sec)))
            ok = bool(self.ctrl.set_absolute(tilt=int(tilt))) and ok
            if zoom is not None:
                time.sleep(max(0.05, float(settle_sec)))
                ok = bool(self.ctrl.set_absolute(zoom=int(zoom))) and ok
            if ok:
                self.last_error = ""
            else:
                self.last_error = str((self.ctrl.status() or {}).get("last_error") or "home returned false")
            return bool(ok)
        except Exception as e:
            self.last_error = str(e)
            return False

    def nudge(self, action: str, amount: int) -> bool:
        try:
            ok = bool(self.ctrl.nudge(str(action), int(max(1, amount))))
            if ok:
                self.last_error = ""
            else:
                self.last_error = str((self.ctrl.status() or {}).get("last_error") or "nudge returned false")
            return ok
        except Exception as e:
            self.last_error = str(e)
            return False

    def status(self) -> Dict[str, Any]:
        try:
            return self.ctrl.status() or {}
        except Exception as e:
            return {"ok": False, "supported": False, "reason": str(e), "device": self.device}


def _raw_action_from_motion(dx: float, dy: float, deadzone: float, vertical_gain: float) -> Tuple[str, str, float]:
    """Return raw action, dominant axis and normalized strength before mapping."""
    x_strength = abs(float(dx))
    y_strength = abs(float(dy)) * max(0.1, float(vertical_gain))
    if x_strength < float(deadzone) and y_strength < float(deadzone):
        return "", "-", max(x_strength, y_strength)
    if y_strength > x_strength:
        return ("up" if float(dy) < 0.0 else "down"), "y", y_strength
    return ("left" if float(dx) < 0.0 else "right"), "x", x_strength




def _face_region_context_score(
    small_gray: object,
    *,
    eye_cx: float,
    eye_cy: float,
    sep_px: float,
    min_std: float,
    horiz_max: float,
    grad_min: float,
) -> Dict[str, Any]:
    """Return a lightweight expected-face-region context score.

    This is deliberately not an ellipse fit and not face detection. The function
    derives a coarse expected head/face ROI from the eye-pair geometry and then
    measures whether that region looks like a plausible head/face context rather
    than a flat wall or a single dominant furniture edge. It operates on the
    already downsampled grayscale frame, so it is cheap and resolution-stable.
    """
    try:
        if np is None or small_gray is None or not hasattr(small_gray, "shape"):
            return {"enabled": True, "ok": False, "score": 0.0, "reason": "no_gray"}
        h, w = int(small_gray.shape[0]), int(small_gray.shape[1])
        sep = float(max(1.0, sep_px))

        # Simple anthropometric prior: eyes sit in the upper/middle face area.
        # Values are intentionally broad; this is a bonus, not a hard detector.
        face_w = max(6.0, 1.55 * sep)
        face_h = max(8.0, 2.80 * sep)
        face_cx = float(eye_cx)
        face_cy = float(eye_cy) + (0.40 * sep)

        x0 = int(max(0, round(face_cx - (face_w / 2.0))))
        x1 = int(min(w, round(face_cx + (face_w / 2.0))))
        y0 = int(max(0, round(face_cy - (face_h * 0.38))))
        y1 = int(min(h, round(face_cy + (face_h * 0.62))))
        roi_w = int(max(0, x1 - x0))
        roi_h = int(max(0, y1 - y0))
        if roi_w < 5 or roi_h < 7:
            return {
                "enabled": True, "ok": False, "score": 0.0, "reason": "roi_too_small",
                "roi": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            }

        roi = small_gray[y0:y1, x0:x1]
        if getattr(roi, "size", 0) <= 0:
            return {"enabled": True, "ok": False, "score": 0.0, "reason": "empty_roi"}

        roi_f = roi.astype(np.float32, copy=False)
        std = float(np.std(roi_f))
        mean = float(np.mean(roi_f))

        # Performance note for Raspberry Pi 5:
        # This score operates only on the already downsampled ROI. It computes
        # one mean vector per axis and two small 1-D diff/variance operations;
        # no cv2 model, no ellipse fit, no full-resolution scan.
        row_means = np.mean(roi_f, axis=1) if roi_h > 1 else np.asarray([mean], dtype=np.float32)
        row_grad = np.diff(row_means.astype(np.float32, copy=False)) if row_means.shape[0] > 1 else np.asarray([0.0], dtype=np.float32)
        grad_variance = float(np.var(row_grad)) if getattr(row_grad, "size", 0) else 0.0

        col_means = np.mean(roi_f, axis=0) if roi_w > 1 else np.asarray([mean], dtype=np.float32)
        col_grad = np.diff(col_means.astype(np.float32, copy=False)) if col_means.shape[0] > 1 else np.asarray([0.0], dtype=np.float32)
        horiz_variance = float(np.var(col_grad)) if getattr(col_grad, "size", 0) else 0.0

        face_region_score = float(grad_variance) / max(float(horiz_variance), 1.0)
        score_norm = _clamp_float(float(face_region_score) / max(1.0, float(grad_min) * 2.0), 0.0, 1.0)

        if std < float(min_std):
            reason = "grad_low"
            ok = False
        elif horiz_variance > max(1.0, grad_variance * (1.0 / max(0.05, 1.0 - float(horiz_max)))) and face_region_score < (float(grad_min) * 1.5):
            reason = "horiz_dominant"
            ok = False
        elif face_region_score < float(grad_min):
            reason = "grad_low"
            ok = False
        else:
            reason = "grad_ok"
            ok = True

        return {
            "enabled": True,
            "ok": bool(ok),
            "score": round(float(face_region_score), 6),
            "score_norm": round(float(score_norm), 6),
            "reason": str(reason),
            "std": round(float(std), 4),
            "mean": round(float(mean), 4),
            "grad_variance": round(float(grad_variance), 6),
            "horiz_variance": round(float(horiz_variance), 6),
            "grad_min": round(float(grad_min), 6),
            "horiz_max": round(float(horiz_max), 6),
            "roi": {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "w": roi_w, "h": roi_h},
        }
    except Exception as exc:
        return {"enabled": True, "ok": False, "score": 0.0, "reason": f"error:{type(exc).__name__}"}

def _candidate_from_motion(
    *,
    tick: int,
    frame_source: str,
    target_mode: str,
    dx_raw: float,
    dy_raw: float,
    obs_dx: float,
    obs_dy: float,
    obs_dist: float,
    energy: float,
    energy_weighted: float,
    deadzone: float,
    vertical_gain: float,
    invert_x: bool,
    invert_y: bool,
) -> Dict[str, Any]:
    """Build a JSON-safe motion candidate for the v1.2b attention pipeline.

    This is intentionally a compact internal representation. The current worker
    still uses the single strongest motion centroid as its candidate, but the
    explicit object creates the stable seam for the later v2 pipeline:

        Motion Field → Candidate Extraction → Candidate Scoring
        → Target Tracker → Servo Decision → PTZ Command

    No DB writes and no UI control side effects are introduced here.
    """
    raw_action, axis, strength = _raw_action_from_motion(obs_dx, obs_dy, deadzone, vertical_gain)
    mapped_action = _map_action(raw_action, invert_x, invert_y) if raw_action else ""
    x_strength = abs(float(obs_dx))
    y_strength = abs(float(obs_dy)) * max(0.1, float(vertical_gain))
    conf = _clamp_float(max(float(energy), float(energy_weighted), float(strength), float(obs_dist)), 0.0, 1.0)
    return {
        "id": f"motion:{int(tick)}",
        "kind": "motion_centroid",
        "source": "motion_diff_upper" if str(target_mode) == "motion_upper" else "motion_diff",
        "frame_source": str(frame_source or "none"),
        "target_mode": str(target_mode or "motion"),
        "dx_raw": round(float(dx_raw), 6),
        "dy_raw": round(float(dy_raw), 6),
        "dx": round(float(obs_dx), 6),
        "dy": round(float(obs_dy), 6),
        "dist": round(float(obs_dist), 6),
        "energy": round(float(energy), 6),
        "energy_weighted": round(float(energy_weighted), 6),
        "confidence": round(float(conf), 6),
        "x_strength": round(float(x_strength), 6),
        "y_strength": round(float(y_strength), 6),
        "axis": str(axis or "-"),
        "raw_action": str(raw_action or ""),
        "mapped_action": str(mapped_action or ""),
    }


def _candidate_from_eye_pair(
    *,
    frame: object,
    tick: int,
    frame_source: str,
    target_mode: str,
    motion_w: int,
    motion_h: int,
    deadzone: float,
    vertical_gain: float,
    invert_x: bool,
    invert_y: bool,
    min_conf: float,
    score_gain: float,
    max_angle_deg: float,
    min_sep: float,
    max_sep: float,
    face_region_enable: bool = True,
    face_region_bonus: float = 0.28,
    face_region_min_score: float = 0.18,
    face_region_min_std: float = 4.0,
    face_region_horiz_max: float = 0.82,
    face_region_grad_min: float = 0.80,
    stats: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Return a soft Eye-Pair / face-like salience candidate.

    This is deliberately *not* face recognition and not an identity feature. It
    searches for two plausible dark blobs in the upper/central image region and
    converts the pair midpoint into the same candidate schema as motion. The
    output is a confidence-scored candidate only; motion remains the fallback and
    the tracker/servo path decides whether the candidate is useful.
    """
    if cv2 is None or np is None or frame is None:
        return {}

    try:
        if stats is not None:
            stats.setdefault("raw", 0)
            stats.setdefault("geom_ok", 0)
            stats.setdefault("face_checked", 0)
            stats.setdefault("face_ok", 0)
            stats.setdefault("face_bonus", 0)
        w = int(max(48, min(192, int(motion_w))))
        h = int(max(27, min(108, int(motion_h))))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)

        # Eyes are expected in the upper/mid part but not forced into a tiny box.
        y0 = int(max(0, round(h * 0.05)))
        y1 = int(min(h, round(h * 0.74)))
        if y1 <= y0 + 4:
            return {}
        roi = small[y0:y1, :]

        # Adaptive dark threshold with an absolute cap avoids assuming one fixed
        # room brightness. Very dark hair/shadows may also create candidates;
        # therefore later geometric checks keep this as a weak salience signal.
        percentile = float(np.percentile(roi, 18.0))
        dark_cap = float(_clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_DARK_MAX", 96.0), 16.0, 180.0))
        threshold = int(max(8.0, min(dark_cap, percentile + 10.0)))
        mask = (roi <= threshold).astype(np.uint8) * 255
        mask = cv2.medianBlur(mask, 3)

        contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        min_area = max(1.0, float(w * h) * 0.00020)
        max_area = max(min_area + 1.0, float(w * h) * 0.01800)
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < min_area or area > max_area:
                continue
            x, y, bw, bh = cv2.boundingRect(contour)
            if bw <= 0 or bh <= 0:
                continue
            ratio = float(bw) / float(max(1, bh))
            if ratio < 0.35 or ratio > 3.20:
                continue
            cx = float(x) + (float(bw) / 2.0)
            cy = float(y0 + y) + (float(bh) / 2.0)
            patch = small[max(0, int(cy - bh)):min(h, int(cy + bh + 1)), max(0, int(cx - bw)):min(w, int(cx + bw + 1))]
            mean_dark = float(np.mean(patch)) if getattr(patch, "size", 0) else float(threshold)
            darkness = _clamp_float((float(threshold) + 35.0 - mean_dark) / 80.0, 0.0, 1.0)
            compact = _clamp_float(area / float(max(1, bw * bh)), 0.0, 1.0)
            blobs.append({
                "x": cx,
                "y": cy,
                "w": float(bw),
                "h": float(bh),
                "area": area,
                "darkness": darkness,
                "compact": compact,
            })

        if len(blobs) < 2:
            return {}

        best = None
        best_score = 0.0
        min_sep_px = float(w) * _clamp_float(float(min_sep), 0.01, 1.0)
        max_sep_px = float(w) * _clamp_float(float(max_sep), 0.02, 1.5)
        max_angle = abs(float(max_angle_deg))
        for i in range(len(blobs)):
            for j in range(i + 1, len(blobs)):
                a = blobs[i]
                b = blobs[j]
                left, right = (a, b) if float(a["x"]) <= float(b["x"]) else (b, a)
                dx_px = float(right["x"]) - float(left["x"])
                dy_px = float(right["y"]) - float(left["y"])
                if dx_px <= 0.0:
                    continue
                if stats is not None:
                    stats["raw"] = int(stats.get("raw", 0)) + 1
                sep = math.sqrt((dx_px * dx_px) + (dy_px * dy_px))
                if sep < min_sep_px or sep > max_sep_px:
                    continue
                angle = abs(math.degrees(math.atan2(dy_px, dx_px)))
                if angle > max_angle:
                    continue
                if stats is not None:
                    stats["geom_ok"] = int(stats.get("geom_ok", 0)) + 1
                size_a = max(float(left["w"]), float(left["h"]), 1.0)
                size_b = max(float(right["w"]), float(right["h"]), 1.0)
                size_sim = 1.0 - min(1.0, abs(size_a - size_b) / max(size_a, size_b, 1.0))
                y_mid = (float(left["y"]) + float(right["y"])) / 2.0
                upper_bonus = 1.0 - min(1.0, abs((y_mid / float(max(1, h))) - 0.34) / 0.42)
                sep_mid = (min_sep_px + max_sep_px) / 2.0
                sep_quality = 1.0 - min(1.0, abs(sep - sep_mid) / max(1.0, (max_sep_px - min_sep_px) / 2.0))
                angle_quality = 1.0 - min(1.0, angle / max(1.0, max_angle))
                dark_quality = (float(left["darkness"]) + float(right["darkness"])) / 2.0
                compact_quality = (float(left["compact"]) + float(right["compact"])) / 2.0
                base_score = (
                    0.34 * dark_quality
                    + 0.18 * size_sim
                    + 0.18 * angle_quality
                    + 0.14 * sep_quality
                    + 0.10 * upper_bonus
                    + 0.06 * compact_quality
                ) * max(0.10, float(score_gain))
                face_context: Dict[str, Any] = {"enabled": bool(face_region_enable), "ok": False, "score": 0.0, "reason": "disabled"}
                if bool(face_region_enable):
                    if stats is not None:
                        stats["face_checked"] = int(stats.get("face_checked", 0)) + 1
                    face_context = _face_region_context_score(
                        small,
                        eye_cx=(float(left["x"]) + float(right["x"])) / 2.0,
                        eye_cy=(float(left["y"]) + float(right["y"])) / 2.0,
                        sep_px=sep,
                        min_std=face_region_min_std,
                        horiz_max=face_region_horiz_max,
                        grad_min=face_region_grad_min,
                    )
                    face_score_norm = float(face_context.get("score_norm") or 0.0)
                    if bool(face_context.get("ok")) and face_score_norm >= float(face_region_min_score):
                        if stats is not None:
                            stats["face_ok"] = int(stats.get("face_ok", 0)) + 1
                            stats["face_bonus"] = int(stats.get("face_bonus", 0)) + 1
                        bonus = 1.0 + (float(face_region_bonus) * face_score_norm)
                    else:
                        bonus = 1.0
                else:
                    bonus = 1.0
                score = _clamp_float(float(base_score) * float(bonus), 0.0, 1.0)
                if score > best_score:
                    best_score = score
                    best = {
                        "left": left,
                        "right": right,
                        "sep_px": sep,
                        "angle_deg": angle,
                        "size_similarity": size_sim,
                        "sep_quality": sep_quality,
                        "upper_bonus": upper_bonus,
                        "dark_quality": dark_quality,
                        "compact_quality": compact_quality,
                        "base_score": base_score,
                        "face_context": face_context,
                    }

        if not best or best_score < float(min_conf):
            return {}

        lx = float(best["left"]["x"])
        ly = float(best["left"]["y"])
        rx = float(best["right"]["x"])
        ry = float(best["right"]["y"])
        cx = (lx + rx) / 2.0
        cy = (ly + ry) / 2.0
        cand_dx = ((cx / float(max(1, w - 1))) * 2.0) - 1.0
        cand_dy = ((cy / float(max(1, h - 1))) * 2.0) - 1.0
        cand_dist = math.sqrt((cand_dx * cand_dx) + (cand_dy * cand_dy))
        raw_action, axis, strength = _raw_action_from_motion(cand_dx, cand_dy, deadzone, vertical_gain)
        mapped_action = _map_action(raw_action, invert_x, invert_y) if raw_action else ""
        x_strength = abs(float(cand_dx))
        y_strength = abs(float(cand_dy)) * max(0.1, float(vertical_gain))

        return {
            "id": f"eye_pair:{int(tick)}",
            "kind": "eye_pair_salience",
            "source": "eye_pair_heuristic",
            "frame_source": str(frame_source or "none"),
            "target_mode": str(target_mode or "motion_upper"),
            "dx_raw": round(float(cand_dx), 6),
            "dy_raw": round(float(cand_dy), 6),
            "dx": round(float(cand_dx), 6),
            "dy": round(float(cand_dy), 6),
            "dist": round(float(cand_dist), 6),
            "energy": 0.0,
            "energy_weighted": 0.0,
            "confidence": round(float(best_score), 6),
            "score": round(float(best_score), 6),
            "x_strength": round(float(x_strength), 6),
            "y_strength": round(float(y_strength), 6),
            "axis": str(axis or "-"),
            "raw_action": str(raw_action or ""),
            "mapped_action": str(mapped_action or ""),
            "pair": {
                "left": {"x": round(lx / float(max(1, w - 1)), 4), "y": round(ly / float(max(1, h - 1)), 4)},
                "right": {"x": round(rx / float(max(1, w - 1)), 4), "y": round(ry / float(max(1, h - 1)), 4)},
                "sep_norm": round(float(best["sep_px"]) / float(max(1, w)), 6),
                "angle_deg": round(float(best["angle_deg"]), 3),
                "dark_quality": round(float(best["dark_quality"]), 6),
                "size_similarity": round(float(best["size_similarity"]), 6),
                "sep_quality": round(float(best["sep_quality"]), 6),
                "upper_bonus": round(float(best["upper_bonus"]), 6),
            },
            "face_region": dict(best.get("face_context") if isinstance(best.get("face_context"), dict) else {}),
            "base_score": round(float(best.get("base_score") or best_score), 6),
            "note": "soft eye-pair salience only; no identity and no hard face detection",
        }
    except Exception as exc:
        return {"kind": "eye_pair_salience", "source": "eye_pair_heuristic", "ok": False, "error": str(exc)}


def _distance_norm(ax: float, ay: float, bx: float, by: float) -> float:
    """Distance in the shared normalized PTZ attention plane (-1..+1)."""
    try:
        return math.sqrt(((float(ax) - float(bx)) ** 2.0) + ((float(ay) - float(by)) ** 2.0))
    except Exception:
        return 999.0


def _gate_eye_pair_candidate(
    candidate: Dict[str, Any],
    *,
    motion_dx: float,
    motion_dy: float,
    motion_relevant: bool,
    require_motion: bool,
    motion_radius: float,
    face_radius_boost: float,
    face_radius_boost_min: float,
    min_frames_stable: int,
    stable_radius: float,
    last_pos: Optional[Tuple[float, float]],
    stable_count: int,
) -> Tuple[Dict[str, Any], Optional[Tuple[float, float]], int, str]:
    """Apply local-motion and temporal gates to a soft Eye-Pair candidate.

    Coordinates are intentionally normalized (-1..+1) because both the motion
    centroid and eye-pair midpoint already live in that common space. This keeps
    the gate independent of camera resolution or future downsample dimensions.
    """
    if not candidate or candidate.get("error"):
        return dict(candidate or {}), None, 0, "missing"

    gated = dict(candidate)
    eye_dx = float(gated.get("dx") or 0.0)
    eye_dy = float(gated.get("dy") or 0.0)
    motion_dist = _distance_norm(eye_dx, eye_dy, float(motion_dx), float(motion_dy))

    # v1.5c: Face-assisted Motion-Radius.  The base radius remains conservative
    # for generic eye-like blobs.  Only candidates that already carry a positive
    # Face-Region/Head-Context score may widen the local-motion gate.  This keeps
    # false positives bounded while allowing real head/eye candidates to survive
    # when the motion centroid briefly sits on glasses, shoulder, hand or another
    # nearby facial edge instead of exactly at the eye midpoint.
    face_ctx = gated.get("face_region") if isinstance(gated.get("face_region"), dict) else {}
    face_ok = bool(face_ctx.get("ok"))
    face_score_norm = float(face_ctx.get("score_norm") or 0.0)
    boost_factor = max(1.0, float(face_radius_boost))
    boost_min = max(0.0, float(face_radius_boost_min))
    boost_active = bool(face_ok and face_score_norm >= boost_min and boost_factor > 1.0)
    effective_radius = float(motion_radius) * boost_factor if boost_active else float(motion_radius)

    gated["motion_distance"] = round(float(motion_dist), 6)
    gated["motion_radius"] = float(motion_radius)
    gated["motion_radius_effective"] = round(float(effective_radius), 6)
    gated["face_radius_boost_active"] = bool(boost_active)
    gated["face_radius_boost_factor"] = round(float(boost_factor), 6)
    gated["face_radius_boost_min"] = round(float(boost_min), 6)

    if bool(require_motion) and (not bool(motion_relevant) or motion_dist > float(effective_radius)):
        gated["gate_state"] = "rejected_motion"
        gated["gate_reason"] = "motion_absent" if not bool(motion_relevant) else "motion_too_far"
        return gated, None, 0, "motion"

    if last_pos is None:
        next_count = 1
    else:
        stable_dist = _distance_norm(eye_dx, eye_dy, float(last_pos[0]), float(last_pos[1]))
        gated["stable_distance"] = round(float(stable_dist), 6)
        if stable_dist <= float(stable_radius):
            next_count = int(stable_count) + 1
        else:
            next_count = 1
    gated["stable_count"] = int(next_count)
    gated["stable_required"] = int(min_frames_stable)
    gated["stable_radius"] = float(stable_radius)

    if int(min_frames_stable) > 1 and next_count < int(min_frames_stable):
        gated["gate_state"] = "rejected_temporal"
        gated["gate_reason"] = "not_stable_yet"
        return gated, (eye_dx, eye_dy), next_count, "temporal"

    gated["gate_state"] = "accepted"
    gated["gate_reason"] = "motion_temporal_ok"
    return gated, (eye_dx, eye_dy), next_count, "accepted"


def _select_attention_candidate(
    candidates: list,
    *,
    motion_relevant: bool,
    eye_pair_require_motion: bool,
    eye_pair_min_conf: float,
    face_region_bonus: float = 0.28,
    eye_face_rank_threshold: float = 0.85,
) -> Dict[str, Any]:
    """Select the best candidate without removing the motion fallback.

    Motion is always safe as baseline. Eye-Pair may win when it is plausible and
    either motion is already relevant or pure eye-following was explicitly
    enabled via ENV. This keeps v2a conservative and testable.
    """
    valid = [c for c in candidates if isinstance(c, dict) and c.get("source") and str(c.get("gate_state") or "accepted") == "accepted"]
    if not valid:
        return {}
    motion = next((c for c in valid if str(c.get("kind") or "") == "motion_centroid"), valid[0])
    eye = next((c for c in valid if str(c.get("kind") or "") == "eye_pair_salience" and float(c.get("confidence") or 0.0) >= float(eye_pair_min_conf)), None)
    if not eye:
        selected = dict(motion)
        selected["candidate_winner"] = "motion_centroid"
        return selected
    if bool(eye_pair_require_motion) and not bool(motion_relevant):
        selected = dict(motion)
        selected["candidate_winner"] = "motion_centroid"
        selected["eye_face_rank_reason"] = "motion_required"
        return selected
    motion_conf = float(motion.get("confidence") or 0.0)
    eye_conf = float(eye.get("confidence") or 0.0)
    face_ctx = eye.get("face_region") if isinstance(eye.get("face_region"), dict) else {}
    face_ok = bool(face_ctx.get("ok"))
    face_score_norm = float(face_ctx.get("score_norm") or 0.0)
    eye_face_rank_score = float(eye_conf) + (float(face_region_bonus) * face_score_norm if face_ok else 0.0)
    threshold_conf = float(motion_conf) * max(0.05, float(eye_face_rank_threshold))
    if eye_face_rank_score >= threshold_conf or eye_conf >= motion_conf or str(motion.get("raw_action") or "") == "":
        selected = dict(eye)
        selected["selected_over"] = str(motion.get("source") or "motion")
        selected["candidate_winner"] = "eye_face_salience" if face_ok else "eye_pair_salience"
        selected["motion_confidence"] = round(float(motion_conf), 6)
        selected["eye_face_rank_score"] = round(float(eye_face_rank_score), 6)
        selected["eye_face_rank_threshold"] = round(float(eye_face_rank_threshold), 6)
        return selected
    selected = dict(motion)
    selected["candidate_winner"] = "motion_centroid"
    selected["eye_face_rank_score"] = round(float(eye_face_rank_score), 6)
    selected["eye_face_rank_threshold"] = round(float(eye_face_rank_threshold), 6)
    return selected


def _axis_strength(dx: float, dy: float, axis: str, vertical_gain: float) -> float:
    """Return the servo strength for one axis using the same scaling as decisioning."""
    if str(axis) == "y":
        return abs(float(dy)) * max(0.1, float(vertical_gain))
    if str(axis) == "x":
        return abs(float(dx))
    return 0.0


def _raw_action_for_axis(dx: float, dy: float, axis: str, deadzone: float, vertical_gain: float) -> Tuple[str, str, float]:
    """Return action constrained to one axis if that axis is still meaningful."""
    axis_s = str(axis or "-")
    strength = _axis_strength(dx, dy, axis_s, vertical_gain)
    if strength < float(deadzone):
        return "", "-", strength
    if axis_s == "y":
        return ("up" if float(dy) < 0.0 else "down"), "y", strength
    if axis_s == "x":
        return ("left" if float(dx) < 0.0 else "right"), "x", strength
    return "", "-", strength


def _apply_axis_lock(
    *,
    dx: float,
    dy: float,
    raw_action: str,
    axis: str,
    strength: float,
    tick: int,
    locked_axis: str,
    locked_until_tick: int,
    deadzone: float,
    vertical_gain: float,
    override_ratio: float,
) -> Tuple[str, str, float, bool, str]:
    """Stabilise short-term axis selection after a real PTZ follow command.

    The lock is deliberately conservative: it can only prefer the last successful
    servo axis for a few ticks, and a clearly stronger fresh orthogonal signal
    breaks through immediately. This reduces x/y flicker without creating a
    long-lived tracker or hiding strong new movement.
    """
    if not raw_action or str(locked_axis or "-") not in ("x", "y"):
        return raw_action, axis, float(strength), False, "none"
    if int(tick) > int(locked_until_tick):
        return raw_action, axis, float(strength), False, "expired"
    if str(axis) == str(locked_axis):
        return raw_action, axis, float(strength), True, "same_axis"

    locked_action, locked_axis_out, locked_strength = _raw_action_for_axis(
        dx, dy, str(locked_axis), deadzone, vertical_gain
    )
    if not locked_action:
        return raw_action, axis, float(strength), False, "locked_axis_weak"

    # Break the lock when the fresh orthogonal axis is clearly stronger.
    if float(strength) > (float(locked_strength) * max(1.0, float(override_ratio))):
        return raw_action, axis, float(strength), False, "override"

    return locked_action, locked_axis_out, float(locked_strength), True, "locked"


def _map_action(raw: str, invert_x: bool, invert_y: bool) -> str:
    a = str(raw or "")
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


def _opposite(a: str, b: str) -> bool:
    return (a == "left" and b == "right") or (a == "right" and b == "left") or (a == "up" and b == "down") or (a == "down" and b == "up")


def _trim_history(history: list, max_n: int) -> None:
    try:
        keep = max(1, int(max_n))
        if len(history) > keep:
            del history[:-keep]
    except Exception:
        pass


def _history_count(history: list, key: str, value: str, relevant_only: bool = True) -> int:
    count = 0
    for item in history:
        try:
            if bool(relevant_only) and not bool(item.get("relevant")):
                continue
            if str(item.get(key) or "") == str(value):
                count += 1
        except Exception:
            continue
    return int(count)


def _history_down_count(history: list) -> int:
    count = 0
    for item in history:
        try:
            if not bool(item.get("relevant")):
                continue
            if str(item.get("mapped_action") or "") == "down" and float(item.get("dy") or 0.0) > 0.0:
                count += 1
        except Exception:
            continue
    return int(count)


def _dynamic_amount(dist: float, energy: float, min_amt: int, max_amt: int, dist_scale: float, e_scale: float) -> int:
    try:
        base = int(min_amt)
        dist_part = int(math.ceil(max(0.0, float(dist)) / max(0.001, float(dist_scale))))
        e_part = int(math.ceil(max(0.0, float(energy)) / max(0.001, float(e_scale))))
        return _clamp_int(base + max(dist_part, e_part) - 1, int(min_amt), int(max_amt))
    except Exception:
        return int(min_amt)


def _read_frame() -> Tuple[Optional[object], float, str, Dict[str, Any]]:
    if get_cached_frame_with_ts_fast_diag is None:
        return None, 0.0, "none", {"error": "get_cached_frame_with_ts_fast_diag unavailable"}
    try:
        frame, ts, src, meta = get_cached_frame_with_ts_fast_diag()
        return frame, float(ts or 0.0), str(src or "none"), meta if isinstance(meta, dict) else {}
    except Exception as e:
        return None, 0.0, "none", {"error": str(e)}


def run_worker(duration_sec: float = 0.0, once: bool = False, verbose: bool = False) -> int:
    base = os.getenv("OROMA_BASE", "/opt/ai/oroma")
    enabled = _env_bool("OROMA_PTZ_MOTOR_ENABLE", True)
    state_path = _state_path(base)
    hz = _clamp_float(_env_float("OROMA_PTZ_MOTOR_HZ", 3.0), 0.2, 20.0)
    interval = 1.0 / float(hz)
    max_frame_age = _clamp_float(_env_float("OROMA_PTZ_MOTOR_MAX_FRAME_AGE", 1.50), 0.05, 30.0)
    state_write_sec = _clamp_float(_env_float("OROMA_PTZ_MOTOR_STATE_WRITE_SEC", 1.0), 0.1, 30.0)
    summary_sec = _clamp_float(_env_float("OROMA_PTZ_MOTOR_SUMMARY_SEC", 20.0), 2.0, 300.0)
    center_on_start = _env_bool("OROMA_PTZ_MOTOR_CENTER_ON_START", True)
    home_pan = _env_int("OROMA_PTZ_MOTOR_HOME_PAN", 0)
    home_tilt = _env_int("OROMA_PTZ_MOTOR_HOME_TILT", 0)
    home_zoom = _env_optional_int("OROMA_PTZ_MOTOR_HOME_ZOOM")
    home_settle_sec = _clamp_float(_env_float("OROMA_PTZ_MOTOR_HOME_SETTLE_SEC", 0.70), 0.05, 5.0)

    motion_w = _clamp_int(_env_int("OROMA_PTZ_MOTOR_MOTION_W", _env_int("OROMA_PTZ_ATTENTION_MOTION_W", 96)), 48, 192)
    motion_h = _clamp_int(_env_int("OROMA_PTZ_MOTOR_MOTION_H", _env_int("OROMA_PTZ_ATTENTION_MOTION_H", 54)), 27, 108)
    deadzone = _clamp_float(_env_float("OROMA_PTZ_MOTOR_DEADZONE", 0.030), 0.001, 0.80)
    energy_min = _clamp_float(_env_float("OROMA_PTZ_MOTOR_ENERGY_MIN", 0.010), 0.0, 1.0)
    vertical_gain = _clamp_float(_env_float("OROMA_PTZ_MOTOR_VERTICAL_GAIN", 1.35), 0.1, 5.0)
    invert_x = _env_bool("OROMA_PTZ_FOLLOW_INVERT_X", True)
    invert_y = _env_bool("OROMA_PTZ_FOLLOW_INVERT_Y", False)
    amount_min = _clamp_int(_env_int("OROMA_PTZ_MOTOR_AMOUNT_MIN", 2), 1, 50)
    amount_max = _clamp_int(_env_int("OROMA_PTZ_MOTOR_AMOUNT_MAX", 3), amount_min, 80)
    dist_scale = _clamp_float(_env_float("OROMA_PTZ_MOTOR_DIST_SCALE", 0.18), 0.01, 10.0)
    e_scale = _clamp_float(_env_float("OROMA_PTZ_MOTOR_E_SCALE", 0.060), 0.001, 10.0)
    reversal_guard_sec = _clamp_float(_env_float("OROMA_PTZ_MOTOR_REVERSAL_GUARD_SEC", 0.80), 0.0, 10.0)
    reversal_ratio = _clamp_float(_env_float("OROMA_PTZ_MOTOR_REVERSAL_RATIO", 1.55), 1.0, 10.0)
    move_cooldown_ticks = _clamp_int(_env_int("OROMA_PTZ_MOTOR_MOVE_COOLDOWN_TICKS", 3), 0, 60)
    micro_guard_enable = _env_bool("OROMA_PTZ_MOTOR_MICRO_GUARD_ENABLE", True)
    micro_guard_dist_factor = _clamp_float(_env_float("OROMA_PTZ_MOTOR_MICRO_GUARD_DIST_FACTOR", 1.50), 1.0, 10.0)
    micro_guard_conf_max = _clamp_float(_env_float("OROMA_PTZ_MOTOR_MICRO_GUARD_CONF_MAX", 0.120), 0.0, 2.0)
    upper_bias_enable = _env_bool("OROMA_PTZ_MOTOR_UPPER_BIAS_ENABLE", True)
    upper_gain = _clamp_float(_env_float("OROMA_PTZ_MOTOR_UPPER_GAIN", 1.35), 1.0, 5.0)
    lower_damping = _clamp_float(_env_float("OROMA_PTZ_MOTOR_LOWER_DAMPING", 0.70), 0.05, 1.0)
    history_n = _clamp_int(_env_int("OROMA_PTZ_MOTOR_HISTORY_N", 5), 1, 25)
    down_confirm_min = _clamp_int(_env_int("OROMA_PTZ_MOTOR_DOWN_CONFIRM_MIN", 3), 1, history_n)
    stability_min = _clamp_int(_env_int("OROMA_PTZ_MOTOR_STABILITY_MIN", 2), 1, history_n)
    strong_signal_bypass = _clamp_float(_env_float("OROMA_PTZ_MOTOR_STRONG_SIGNAL_BYPASS", 0.22), 0.0, 2.0)
    target_enable = _env_bool("OROMA_PTZ_MOTOR_TARGET_ENABLE", True)
    target_decay = _clamp_float(_env_float("OROMA_PTZ_MOTOR_TARGET_DECAY", 0.85), 0.05, 0.999)
    target_alpha = _clamp_float(_env_float("OROMA_PTZ_MOTOR_TARGET_ALPHA", 0.45), 0.01, 1.0)
    target_conf_min = _clamp_float(_env_float("OROMA_PTZ_MOTOR_TARGET_CONF_MIN", 0.020), 0.0, 1.0)
    target_override_ratio = _clamp_float(_env_float("OROMA_PTZ_MOTOR_TARGET_OVERRIDE_RATIO", 1.80), 1.0, 10.0)
    target_hold_ticks = _clamp_int(_env_int("OROMA_PTZ_MOTOR_TARGET_HOLD_TICKS", 6), 0, 120)
    target_hold_command = _env_bool("OROMA_PTZ_MOTOR_TARGET_HOLD_COMMAND", False)
    eye_hold_bias_enable = _env_bool("OROMA_PTZ_MOTOR_EYE_HOLD_BIAS_ENABLE", True)
    eye_hold_ticks = _clamp_int(_env_int("OROMA_PTZ_MOTOR_EYE_HOLD_TICKS", 8), 0, 120)
    eye_hold_conf_min = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_HOLD_CONF_MIN", 0.060), 0.0, 1.0)
    eye_hold_override_ratio = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_HOLD_OVERRIDE_RATIO", 1.60), 1.0, 10.0)
    eye_hold_command = _env_bool("OROMA_PTZ_MOTOR_EYE_HOLD_COMMAND", True)
    axis_lock_enable = _env_bool("OROMA_PTZ_MOTOR_AXIS_LOCK_ENABLE", True)
    axis_lock_ticks = _clamp_int(_env_int("OROMA_PTZ_MOTOR_AXIS_LOCK_TICKS", 4), 0, 60)
    axis_lock_override_ratio = _clamp_float(_env_float("OROMA_PTZ_MOTOR_AXIS_LOCK_OVERRIDE_RATIO", 1.65), 1.0, 10.0)
    eye_pair_enable = _env_bool("OROMA_PTZ_MOTOR_EYE_PAIR_ENABLE", True)
    eye_pair_require_motion = _env_bool("OROMA_PTZ_MOTOR_EYE_PAIR_REQUIRE_MOTION", True)
    eye_pair_min_conf = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_MIN_CONF", 0.18), 0.0, 1.0)
    eye_pair_score_gain = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_SCORE_GAIN", 1.20), 0.1, 5.0)
    eye_pair_max_angle_deg = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_MAX_ANGLE_DEG", 45.0), 1.0, 85.0)
    eye_pair_min_sep = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_MIN_SEP", 0.07), 0.01, 1.0)
    eye_pair_max_sep = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_MAX_SEP", 0.46), eye_pair_min_sep, 1.5)
    eye_pair_motion_radius = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_MOTION_RADIUS", 0.35), 0.01, 2.5)
    eye_pair_min_frames_stable = _clamp_int(_env_int("OROMA_PTZ_MOTOR_EYE_PAIR_MIN_FRAMES_STABLE", 2), 1, 20)
    eye_pair_stable_radius = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_PAIR_STABLE_RADIUS", 0.12), 0.005, 2.0)
    face_region_enable = _env_bool("OROMA_PTZ_MOTOR_FACE_REGION_ENABLE", True)
    face_region_bonus = _clamp_float(_env_float("OROMA_PTZ_MOTOR_FACE_REGION_BONUS", 0.28), 0.0, 2.0)
    face_region_min_score = _clamp_float(_env_float("OROMA_PTZ_MOTOR_FACE_REGION_MIN_SCORE", 0.18), 0.0, 1.0)
    face_region_min_std = _clamp_float(_env_float("OROMA_PTZ_MOTOR_FACE_REGION_MIN_STD", 4.0), 0.0, 64.0)
    face_region_horiz_max = _clamp_float(_env_float("OROMA_PTZ_MOTOR_FACE_REGION_HORIZ_MAX", 0.82), 0.30, 0.99)
    face_region_grad_min = _clamp_float(_env_float("OROMA_PTZ_MOTOR_FACE_REGION_GRAD_MIN", 0.80), 0.05, 12.0)
    eye_face_rank_threshold = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_FACE_RANK_THRESHOLD", 0.85), 0.10, 2.50)
    eye_face_radius_boost = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST", 1.55), 1.0, 4.0)
    eye_face_radius_boost_min = _clamp_float(_env_float("OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST_MIN", 0.40), 0.0, 1.0)

    started = _now()
    counters: Dict[str, int] = {
        "ticks": 0,
        "frames": 0,
        "stale_frames": 0,
        "no_frames": 0,
        "moves": 0,
        "cmd_ok": 0,
        "cmd_fail": 0,
        "guarded_reversals": 0,
        "down_holds": 0,
        "stability_waits": 0,
        "idle": 0,
        "target_holds": 0,
        "target_updates": 0,
        "target_overrides": 0,
        "target_decays": 0,
        "eye_hold_bias": 0,
        "eye_hold_commands": 0,
        "eye_hold_overridden": 0,
        "move_cooldown_blocks": 0,
        "move_cooldown_bypass": 0,
        "micro_guard_blocks": 0,
        "axis_locks": 0,
        "axis_lock_overrides": 0,
        "eye_pair_raw": 0,
        "eye_pair_geom_ok": 0,
        "eye_pair_motion_gated": 0,
        "eye_pair_temporal_gated": 0,
        "eye_pair_candidates": 0,
        "eye_pair_selected": 0,
        "eye_pair_rejected_motion": 0,
        "eye_pair_rejected_temporal": 0,
        "eye_pair_rejected_geometry": 0,
        "face_region_checked": 0,
        "face_region_ok": 0,
        "face_region_bonus": 0,
    }
    last_state_write = 0.0
    last_summary = started
    last_action = ""
    last_strength = 0.0
    last_move_ts = 0.0
    prev_small: Optional[bytes] = None
    motion_history: list = []
    last_result: Dict[str, Any] = {}
    target_dx = 0.0
    target_dy = 0.0
    target_conf = 0.0
    target_age_ticks = 0
    target_last_qualified_reason = ""
    target_last_qualified_kind = ""
    target_last_qualified_source = ""
    target_last_update = "none"
    axis_lock_axis = "-"
    axis_lock_until_tick = 0
    axis_lock_reason = "none"
    axis_lock_active = False
    last_candidate: Dict[str, Any] = {}
    last_candidates: list = []
    last_eye_candidate: Dict[str, Any] = {}
    eye_last_pos: Optional[Tuple[float, float]] = None
    eye_stable_count = 0
    move_cooldown_remaining = 0
    move_cooldown_active = False
    move_cooldown_bypass = False
    micro_guard_active = False

    if not enabled:
        result = {"ok": True, "enabled": False, "reason": "OROMA_PTZ_MOTOR_ENABLE=0", "ts": int(_now())}
        _atomic_write_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 0

    device = os.getenv("OROMA_PTZ_DEVICE", "").strip()
    if not device:
        device = _autodetect_ptz_device()
    if not device:
        result = {"ok": False, "enabled": True, "reason": "no PTZ device found", "ts": int(_now())}
        _atomic_write_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 2

    try:
        motor = MotorController(device)
    except Exception as e:
        result = {"ok": False, "enabled": True, "device": device, "reason": f"PTZ init failed: {e}", "ts": int(_now())}
        _atomic_write_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 2

    home_ok: Optional[bool] = None
    if center_on_start:
        home_ok = bool(motor.home(pan=int(home_pan), tilt=int(home_tilt), zoom=home_zoom, settle_sec=float(home_settle_sec)))
        print(
            f"[ptz_motor_worker] home_on_start ok={1 if home_ok else 0} pan={int(home_pan)} tilt={int(home_tilt)} "
            f"zoom={home_zoom if home_zoom is not None else '-'} err={getattr(motor, 'last_error', '')}",
            flush=True,
        )
        # Drop any implicit optical history after an active motor reset.
        prev_small = None

    boot_state = motor.status()
    print(
        f"[ptz_motor_worker] start ok=1 device={device} hz={hz:.2f} interval={interval:.3f}s "
        f"motion={motion_w}x{motion_h} dead={deadzone:.4f} emin={energy_min:.4f} "
        f"invx={1 if invert_x else 0} invy={1 if invert_y else 0} amount={amount_min}-{amount_max} "
        f"cooldown_ticks={move_cooldown_ticks} micro_guard={1 if micro_guard_enable else 0} "
        f"micro_factor={micro_guard_dist_factor:.2f} micro_conf={micro_guard_conf_max:.3f} "
        f"upper_bias={1 if upper_bias_enable else 0} ugain={upper_gain:.2f} ldamp={lower_damping:.2f} "
        f"hist={history_n} down_confirm={down_confirm_min} stability={stability_min} bypass={strong_signal_bypass:.3f} "
        f"target={1 if target_enable else 0} decay={target_decay:.3f} alpha={target_alpha:.2f} "
        f"conf_min={target_conf_min:.3f} override={target_override_ratio:.2f} hold_ticks={target_hold_ticks} "
        f"eye_hold={1 if eye_hold_bias_enable else 0} eye_hold_ticks={eye_hold_ticks} "
        f"eye_hold_conf={eye_hold_conf_min:.3f} eye_hold_override={eye_hold_override_ratio:.2f} "
        f"eye_hold_cmd={1 if eye_hold_command else 0} "
        f"axis_lock={1 if axis_lock_enable else 0} axis_ticks={axis_lock_ticks} axis_override={axis_lock_override_ratio:.2f} "
        f"eye_pair={1 if eye_pair_enable else 0} eye_min={eye_pair_min_conf:.3f} eye_req_motion={1 if eye_pair_require_motion else 0} "
        f"eye_motion_radius={eye_pair_motion_radius:.3f} eye_stable_frames={eye_pair_min_frames_stable} "
        f"eye_stable_radius={eye_pair_stable_radius:.3f} face_region={1 if face_region_enable else 0} "
        f"face_bonus={face_region_bonus:.2f} face_min={face_region_min_score:.3f} "
        f"face_grad_min={face_region_grad_min:.3f} eye_face_rank={eye_face_rank_threshold:.2f} "
        f"eye_face_radius_boost={eye_face_radius_boost:.2f} eye_face_radius_min={eye_face_radius_boost_min:.2f}",
        flush=True,
    )

    while not _STOP:
        loop_t0 = time.monotonic()
        now = _now()
        counters["ticks"] += 1
        action = ""
        raw_action = ""
        mapped_action = ""
        axis = "-"
        cmd_ok: Optional[bool] = None
        amount = 0
        reason = "idle"
        frame_age: Optional[float] = None
        dx = dy = dist = energy = strength = 0.0
        dx_raw = dy_raw = dist_raw = energy_weighted = upper_bias_delta = 0.0
        target_mode = "motion_upper" if upper_bias_enable else "motion"
        down_confirm_count = 0
        stable_count = 0
        strong_bypass = False
        target_hold_active = False
        eye_hold_bias_active = False
        eye_hold_command_active = False
        target_update = "none"
        obs_dx = obs_dy = obs_dist = obs_strength = 0.0
        obs_action = ""
        obs_axis = "-"
        obs_mapped_action = ""
        obs_conf = 0.0
        candidate: Dict[str, Any] = {}
        candidates: list = []
        eye_candidate: Dict[str, Any] = {}
        candidate_source = ""
        axis_lock_active = False
        axis_lock_reason = "none"
        move_cooldown_active = bool(move_cooldown_remaining > 0)
        move_cooldown_bypass = False
        micro_guard_active = False
        frame_src = "none"
        frame_meta: Dict[str, Any] = {}

        frame, frame_ts, frame_src, frame_meta = _read_frame()
        if frame is None or frame_ts <= 0.0:
            counters["no_frames"] += 1
            reason = "no_frame"
            prev_small = None
        else:
            frame_age = max(0.0, now - float(frame_ts))
            if frame_age > max_frame_age:
                counters["stale_frames"] += 1
                reason = "stale_frame"
                prev_small = None
            else:
                counters["frames"] += 1
                cur_small = _downsample_gray(frame, motion_w, motion_h)
                cent = _motion_centroid(
                    prev_small,
                    cur_small,
                    motion_w,
                    motion_h,
                    upper_bias_enabled=bool(upper_bias_enable),
                    upper_gain=float(upper_gain),
                    lower_damping=float(lower_damping),
                )
                prev_small = cur_small
                dx = float(cent.get("dx") or 0.0)
                dy = float(cent.get("dy") or 0.0)
                dist = float(cent.get("dist") or 0.0)
                dx_raw = float(cent.get("dx_raw") or dx)
                dy_raw = float(cent.get("dy_raw") or dy)
                dist_raw = float(cent.get("dist_raw") or dist)
                energy = float(cent.get("energy") or 0.0)
                energy_weighted = float(cent.get("energy_weighted") or energy)
                upper_bias_delta = float(cent.get("upper_bias") or 0.0)
                obs_dx = float(dx)
                obs_dy = float(dy)
                obs_dist = float(dist)
                motion_candidate = _candidate_from_motion(
                    tick=int(counters["ticks"]),
                    frame_source=frame_src,
                    target_mode=target_mode,
                    dx_raw=dx_raw,
                    dy_raw=dy_raw,
                    obs_dx=obs_dx,
                    obs_dy=obs_dy,
                    obs_dist=obs_dist,
                    energy=energy,
                    energy_weighted=energy_weighted,
                    deadzone=deadzone,
                    vertical_gain=vertical_gain,
                    invert_x=invert_x,
                    invert_y=invert_y,
                )
                candidates = [dict(motion_candidate)]
                motion_relevant = bool(str(motion_candidate.get("raw_action") or "") and energy >= energy_min)

                if bool(eye_pair_enable):
                    eye_stats: Dict[str, int] = {"raw": 0, "geom_ok": 0}
                    eye_candidate = _candidate_from_eye_pair(
                        frame=frame,
                        tick=int(counters["ticks"]),
                        frame_source=frame_src,
                        target_mode=target_mode,
                        motion_w=motion_w,
                        motion_h=motion_h,
                        deadzone=deadzone,
                        vertical_gain=vertical_gain,
                        invert_x=invert_x,
                        invert_y=invert_y,
                        min_conf=eye_pair_min_conf,
                        score_gain=eye_pair_score_gain,
                        max_angle_deg=eye_pair_max_angle_deg,
                        min_sep=eye_pair_min_sep,
                        max_sep=eye_pair_max_sep,
                        face_region_enable=face_region_enable,
                        face_region_bonus=face_region_bonus,
                        face_region_min_score=face_region_min_score,
                        face_region_min_std=face_region_min_std,
                        face_region_horiz_max=face_region_horiz_max,
                        face_region_grad_min=face_region_grad_min,
                        stats=eye_stats,
                    )
                    counters["eye_pair_raw"] += int(eye_stats.get("raw", 0))
                    counters["eye_pair_geom_ok"] += int(eye_stats.get("geom_ok", 0))
                    counters["face_region_checked"] += int(eye_stats.get("face_checked", 0))
                    counters["face_region_ok"] += int(eye_stats.get("face_ok", 0))
                    counters["face_region_bonus"] += int(eye_stats.get("face_bonus", 0))
                    if eye_stats.get("geom_ok", 0) <= 0:
                        counters["eye_pair_rejected_geometry"] += 1
                    if eye_candidate and not eye_candidate.get("error"):
                        counters["eye_pair_candidates"] += 1
                        gated_eye, eye_last_pos, eye_stable_count, eye_gate = _gate_eye_pair_candidate(
                            eye_candidate,
                            motion_dx=float(motion_candidate.get("dx") or 0.0),
                            motion_dy=float(motion_candidate.get("dy") or 0.0),
                            motion_relevant=motion_relevant,
                            require_motion=eye_pair_require_motion,
                            motion_radius=eye_pair_motion_radius,
                            face_radius_boost=eye_face_radius_boost,
                            face_radius_boost_min=eye_face_radius_boost_min,
                            min_frames_stable=eye_pair_min_frames_stable,
                            stable_radius=eye_pair_stable_radius,
                            last_pos=eye_last_pos,
                            stable_count=eye_stable_count,
                        )
                        last_eye_candidate = dict(gated_eye)
                        candidates.append(dict(gated_eye))
                        if eye_gate == "accepted":
                            counters["eye_pair_motion_gated"] += 1
                            counters["eye_pair_temporal_gated"] += 1
                        elif eye_gate == "temporal":
                            counters["eye_pair_motion_gated"] += 1
                            counters["eye_pair_rejected_temporal"] += 1
                        elif eye_gate == "motion":
                            counters["eye_pair_rejected_motion"] += 1
                    else:
                        eye_last_pos = None
                        eye_stable_count = 0
                        if eye_candidate and eye_candidate.get("error"):
                            last_eye_candidate = dict(eye_candidate)

                candidate = _select_attention_candidate(
                    candidates,
                    motion_relevant=motion_relevant,
                    eye_pair_require_motion=eye_pair_require_motion,
                    eye_pair_min_conf=eye_pair_min_conf,
                    face_region_bonus=face_region_bonus,
                    eye_face_rank_threshold=eye_face_rank_threshold,
                ) or dict(motion_candidate)
                if str(candidate.get("kind") or "") == "eye_pair_salience":
                    counters["eye_pair_selected"] += 1
                last_candidate = dict(candidate)
                last_candidates = [dict(item) for item in candidates if isinstance(item, dict)]
                candidate_source = str(candidate.get("source") or "")
                obs_dx = float(candidate.get("dx") or obs_dx)
                obs_dy = float(candidate.get("dy") or obs_dy)
                obs_dist = float(candidate.get("dist") or math.sqrt((obs_dx * obs_dx) + (obs_dy * obs_dy)))
                obs_action = str(candidate.get("raw_action") or "")
                obs_axis = str(candidate.get("axis") or "-")
                obs_strength = float(candidate.get("y_strength") if obs_axis == "y" else candidate.get("x_strength") or 0.0)
                obs_mapped_action = str(candidate.get("mapped_action") or "")
                candidate_kind = str(candidate.get("kind") or "")
                candidate_source_kind = str(candidate.get("source") or "")
                relevant = bool(obs_action and (energy >= energy_min or (candidate_kind == "eye_pair_salience" and not bool(eye_pair_require_motion))))
                obs_conf = float(candidate.get("confidence") or 0.0)
                eye_hold_eligible = bool(
                    eye_hold_bias_enable
                    and target_last_qualified_kind == "eye_pair_salience"
                    and target_conf >= eye_hold_conf_min
                    and eye_hold_ticks > 0
                    and target_age_ticks <= eye_hold_ticks
                )
                eye_hold_should_prefer = bool(
                    eye_hold_eligible
                    and candidate_kind != "eye_pair_salience"
                    and obs_conf <= (target_conf * eye_hold_override_ratio)
                )

                if bool(target_enable):
                    if relevant and not eye_hold_should_prefer:
                        if target_conf <= target_conf_min:
                            target_dx = float(obs_dx)
                            target_dy = float(obs_dy)
                            target_conf = float(obs_conf)
                            target_age_ticks = 0
                            target_update = "init"
                        elif obs_conf > (target_conf * target_override_ratio):
                            target_dx = float(obs_dx)
                            target_dy = float(obs_dy)
                            target_conf = float(obs_conf)
                            target_age_ticks = 0
                            target_update = "override"
                            counters["target_overrides"] += 1
                        else:
                            a = float(target_alpha)
                            target_dx = (a * float(obs_dx)) + ((1.0 - a) * float(target_dx))
                            target_dy = (a * float(obs_dy)) + ((1.0 - a) * float(target_dy))
                            target_conf = max(float(obs_conf), (a * float(obs_conf)) + ((1.0 - a) * float(target_conf)))
                            target_age_ticks = 0
                            target_update = "blend"
                        counters["target_updates"] += 1
                        dx = float(target_dx)
                        dy = float(target_dy)
                        dist = math.sqrt((dx * dx) + (dy * dy))
                    else:
                        if eye_hold_should_prefer:
                            relevant = False
                            eye_hold_bias_active = True
                            target_update = "eye_hold_bias"
                        if target_conf > 0.0:
                            target_conf *= float(target_decay)
                            target_age_ticks += 1
                            target_update = "decay"
                            counters["target_decays"] += 1
                        generic_hold_active = bool(
                            target_last_qualified_reason == "follow"
                            and target_conf >= target_conf_min
                            and target_hold_ticks > 0
                            and target_age_ticks <= target_hold_ticks
                        )
                        if eye_hold_eligible:
                            eye_hold_bias_active = True
                        target_hold_active = bool(generic_hold_active or eye_hold_bias_active)
                        if target_hold_active:
                            counters["target_holds"] += 1
                            if eye_hold_bias_active:
                                counters["eye_hold_bias"] += 1
                            hold_should_command = bool(target_hold_command or (eye_hold_bias_active and eye_hold_command))
                            if hold_should_command:
                                dx = float(target_dx)
                                dy = float(target_dy)
                                dist = math.sqrt((dx * dx) + (dy * dy))
                                if eye_hold_bias_active and eye_hold_command:
                                    eye_hold_command_active = True
                                    counters["eye_hold_commands"] += 1
                            else:
                                dx = float(obs_dx)
                                dy = float(obs_dy)
                                dist = float(obs_dist)
                raw_action, axis, strength = _raw_action_from_motion(dx, dy, deadzone, vertical_gain)
                if raw_action and bool(micro_guard_enable):
                    micro_guard_limit = float(deadzone) * float(micro_guard_dist_factor)
                    if float(dist) < micro_guard_limit and float(target_conf) < float(micro_guard_conf_max):
                        micro_guard_active = True
                        counters["micro_guard_blocks"] += 1
                        reason = "micro_guard"
                        raw_action = ""
                        axis = "-"
                        strength = 0.0
                if raw_action and bool(move_cooldown_active):
                    move_cooldown_bypass = bool(
                        float(obs_conf) > float(strong_signal_bypass)
                        or str(candidate_kind or "") == "eye_pair_salience"
                    )
                    if move_cooldown_bypass:
                        counters["move_cooldown_bypass"] += 1
                    else:
                        counters["move_cooldown_blocks"] += 1
                        reason = "move_cooldown"
                        raw_action = ""
                        axis = "-"
                        strength = 0.0
                if bool(axis_lock_enable) and axis_lock_ticks > 0:
                    raw_action, axis, strength, axis_lock_active, axis_lock_reason = _apply_axis_lock(
                        dx=dx,
                        dy=dy,
                        raw_action=raw_action,
                        axis=axis,
                        strength=strength,
                        tick=int(counters["ticks"]),
                        locked_axis=axis_lock_axis,
                        locked_until_tick=axis_lock_until_tick,
                        deadzone=deadzone,
                        vertical_gain=vertical_gain,
                        override_ratio=axis_lock_override_ratio,
                    )
                    if axis_lock_active:
                        counters["axis_locks"] += 1
                    elif axis_lock_reason == "override":
                        counters["axis_lock_overrides"] += 1
                mapped_action = _map_action(raw_action, invert_x, invert_y) if raw_action else ""
                if not relevant and not (target_hold_active and (target_hold_command or eye_hold_command_active)):
                    raw_action = obs_action
                    mapped_action = obs_mapped_action
                    axis = obs_axis
                    strength = float(obs_strength)
                    axis_lock_active = False
                    axis_lock_reason = "not_relevant"

                motion_history.append({
                    "raw_action": raw_action or "",
                    "mapped_action": mapped_action or "",
                    "axis": axis,
                    "dx": float(dx),
                    "dy": float(dy),
                    "dx_raw": float(dx_raw),
                    "dy_raw": float(dy_raw),
                    "strength": float(strength),
                    "energy": float(energy),
                    "relevant": bool(relevant or eye_hold_command_active),
                })
                _trim_history(motion_history, history_n)
                down_confirm_count = _history_down_count(motion_history)
                stable_count = _history_count(motion_history, "mapped_action", mapped_action, relevant_only=True) if mapped_action else 0
                strong_bypass = bool(strength >= strong_signal_bypass or dist >= strong_signal_bypass)

                servo_energy_ok = bool(energy >= energy_min or eye_hold_command_active)
                if not raw_action or not servo_energy_ok:
                    if reason not in ("move_cooldown", "micro_guard"):
                        reason = "eye_hold" if eye_hold_command_active else ("target_hold" if target_hold_active else ("deadzone" if not raw_action else "energy_low"))
                    counters["idle"] += 1
                    if not bool(target_hold_command or eye_hold_command_active):
                        action = ""
                        amount = 0
                        cmd_ok = None
                else:
                    action = mapped_action
                    effective_energy = max(float(energy), float(target_conf) if eye_hold_command_active else float(energy))
                    amount = _dynamic_amount(dist, effective_energy, amount_min, amount_max, dist_scale, e_scale)
                    if action == "down":
                        if down_confirm_count < down_confirm_min and not strong_bypass:
                            counters["down_holds"] += 1
                            reason = "down_hold"
                            action = ""
                            amount = 0
                            cmd_ok = None
                    elif action:
                        if stable_count < stability_min and not strong_bypass:
                            counters["stability_waits"] += 1
                            reason = "stability_wait"
                            action = ""
                            amount = 0
                            cmd_ok = None
                    if action and last_action and _opposite(last_action, action) and (now - last_move_ts) < reversal_guard_sec:
                        if strength < (last_strength * reversal_ratio) and not strong_bypass:
                            counters["guarded_reversals"] += 1
                            reason = "reversal_guard"
                            action = ""
                            amount = 0
                            cmd_ok = None
                    if action:
                        cmd_ok = bool(motor.nudge(action, amount))
                        if cmd_ok:
                            counters["moves"] += 1
                            counters["cmd_ok"] += 1
                            last_action = action
                            last_strength = float(strength)
                            last_move_ts = now
                            if move_cooldown_ticks > 0:
                                move_cooldown_remaining = int(move_cooldown_ticks)
                            if eye_hold_command_active:
                                reason = "eye_hold"
                            else:
                                reason = "follow"
                                target_last_qualified_reason = "follow"
                                target_last_qualified_kind = str(candidate_kind or "")
                                target_last_qualified_source = str(candidate_source_kind or "")
                                target_age_ticks = 0
                            axis_lock_axis = axis if axis in ("x", "y") else axis_lock_axis
                            axis_lock_until_tick = int(counters["ticks"]) + int(axis_lock_ticks)
                        else:
                            counters["cmd_fail"] += 1
                            reason = "cmd_fail"

        last_result = {
            "ok": True,
            "enabled": True,
            "ts": int(now),
            "heartbeat_ts": float(now),
            "pid": os.getpid(),
            "device": device,
            "loop_hz_target": float(hz),
            "frame_source": frame_src,
            "frame_age_sec": None if frame_age is None else round(float(frame_age), 3),
            "frame_meta": frame_meta,
            "reason": reason,
            "action": action or "",
            "raw_action": raw_action or "",
            "mapped_action": mapped_action or "",
            "axis": axis,
            "target_mode": target_mode,
            "target_enabled": bool(target_enable),
            "target_dx": round(float(target_dx), 6),
            "target_dy": round(float(target_dy), 6),
            "target_conf": round(float(target_conf), 6),
            "target_age_ticks": int(target_age_ticks),
            "target_hold_active": bool(target_hold_active),
            "target_update": target_update,
            "target_last_qualified_reason": target_last_qualified_reason,
            "target_last_qualified_kind": target_last_qualified_kind,
            "target_last_qualified_source": target_last_qualified_source,
            "eye_hold_bias_enabled": bool(eye_hold_bias_enable),
            "eye_hold_bias_active": bool(eye_hold_bias_active),
            "eye_hold_command_active": bool(eye_hold_command_active),
            "eye_hold_ticks": int(eye_hold_ticks),
            "eye_hold_conf_min": float(eye_hold_conf_min),
            "eye_hold_override_ratio": float(eye_hold_override_ratio),
            "eye_hold_command": bool(eye_hold_command),
            "move_cooldown_ticks": int(move_cooldown_ticks),
            "move_cooldown_remaining": int(move_cooldown_remaining),
            "move_cooldown_active": bool(move_cooldown_active),
            "move_cooldown_bypass": bool(move_cooldown_bypass),
            "micro_guard_enabled": bool(micro_guard_enable),
            "micro_guard_active": bool(micro_guard_active),
            "micro_guard_dist_factor": float(micro_guard_dist_factor),
            "micro_guard_conf_max": float(micro_guard_conf_max),
            "target_decay": float(target_decay),
            "target_alpha": float(target_alpha),
            "target_conf_min": float(target_conf_min),
            "target_override_ratio": float(target_override_ratio),
            "target_hold_ticks": int(target_hold_ticks),
            "target_hold_command": bool(target_hold_command),
            "candidate": candidate if candidate else last_candidate,
            "candidates": last_candidates,
            "candidate_source": candidate_source or str((last_candidate or {}).get("source") or ""),
            "eye_pair_enabled": bool(eye_pair_enable),
            "eye_pair_require_motion": bool(eye_pair_require_motion),
            "eye_pair_min_conf": float(eye_pair_min_conf),
            "eye_pair_score_gain": float(eye_pair_score_gain),
            "eye_pair_max_angle_deg": float(eye_pair_max_angle_deg),
            "eye_pair_min_sep": float(eye_pair_min_sep),
            "eye_pair_max_sep": float(eye_pair_max_sep),
            "eye_pair_motion_radius": float(eye_pair_motion_radius),
            "eye_pair_min_frames_stable": int(eye_pair_min_frames_stable),
            "eye_pair_stable_radius": float(eye_pair_stable_radius),
            "face_region_enabled": bool(face_region_enable),
            "face_region_bonus": float(face_region_bonus),
            "face_region_min_score": float(face_region_min_score),
            "face_region_min_std": float(face_region_min_std),
            "face_region_horiz_max": float(face_region_horiz_max),
            "face_region_grad_min": float(face_region_grad_min),
            "eye_face_rank_threshold": float(eye_face_rank_threshold),
            "eye_face_radius_boost": float(eye_face_radius_boost),
            "eye_face_radius_boost_min": float(eye_face_radius_boost_min),
            "candidate_winner": str(candidate.get("candidate_winner") or candidate.get("kind") or ""),
            "eye_pair_stable_count": int(eye_stable_count),
            "eye_pair_last": last_eye_candidate,
            "axis_lock_enabled": bool(axis_lock_enable),
            "axis_lock_active": bool(axis_lock_active),
            "axis_lock_axis": axis_lock_axis,
            "axis_lock_until_tick": int(axis_lock_until_tick),
            "axis_lock_ticks": int(axis_lock_ticks),
            "axis_lock_reason": axis_lock_reason,
            "axis_lock_override_ratio": float(axis_lock_override_ratio),
            "dx": round(float(dx), 6),
            "dy": round(float(dy), 6),
            "dx_raw": round(float(dx_raw), 6),
            "dy_raw": round(float(dy_raw), 6),
            "dist": round(float(dist), 6),
            "dist_raw": round(float(dist_raw), 6),
            "energy": round(float(energy), 6),
            "energy_weighted": round(float(energy_weighted), 6),
            "strength": round(float(strength), 6),
            "upper_bias_enabled": bool(upper_bias_enable),
            "upper_bias_delta": round(float(upper_bias_delta), 6),
            "obs_dx": round(float(obs_dx), 6),
            "obs_dy": round(float(obs_dy), 6),
            "obs_dist": round(float(obs_dist), 6),
            "obs_conf": round(float(obs_conf), 6),
            "obs_action": obs_action or "",
            "obs_mapped_action": obs_mapped_action or "",
            "history_n": int(history_n),
            "down_confirm_count": int(down_confirm_count),
            "down_confirm_min": int(down_confirm_min),
            "stable_count": int(stable_count),
            "stability_min": int(stability_min),
            "strong_bypass": bool(strong_bypass),
            "deadzone": float(deadzone),
            "amount": int(amount),
            "cmd_ok": cmd_ok,
            "cmd_error": getattr(motor, "last_error", ""),
            "invert_x": bool(invert_x),
            "invert_y": bool(invert_y),
            "counters": counters.copy(),
            "ptz_status_start": boot_state,
            "home_on_start": bool(center_on_start),
            "home_ok": home_ok,
            "home_pan": int(home_pan),
            "home_tilt": int(home_tilt),
            "home_zoom": home_zoom,
        }

        if verbose:
            cmd_s = "-" if cmd_ok is None else ("1" if cmd_ok else "0")
            age_s = "-" if frame_age is None else f"{float(frame_age):.2f}s"
            print(
                f"[ptz_motor_worker] tick={counters['ticks']} reason={reason} target_mode={target_mode} src={frame_src} age={age_s} "
                f"dx_raw={dx_raw:.4f} dy_raw={dy_raw:.4f} obs_dx={obs_dx:.4f} obs_dy={obs_dy:.4f} | "
                f"target_dx={target_dx:.4f} target_dy={target_dy:.4f} target_conf={target_conf:.4f} "
                f"target_age={target_age_ticks} target_hold={1 if target_hold_active else 0} eye_hold={1 if eye_hold_bias_active else 0}/{1 if eye_hold_command_active else 0} update={target_update} "
                f"cand={candidate_source or '-'} eye={1 if str(candidate.get('kind') or '') == 'eye_pair_salience' else 0} "
                f"eye_raw={counters['eye_pair_raw']} eye_geom={counters['eye_pair_geom_ok']} "
                f"eye_motion={counters['eye_pair_motion_gated']} eye_temp={counters['eye_pair_temporal_gated']} eye_sel={counters['eye_pair_selected']} "
                f"axis_lock={1 if axis_lock_active else 0}:{axis_lock_reason}({axis_lock_axis}->{axis_lock_until_tick}) "
                f"cooldown={int(move_cooldown_remaining)}:{1 if move_cooldown_active else 0}/{1 if move_cooldown_bypass else 0} "
                f"micro={1 if micro_guard_active else 0} | "
                f"dx={dx:.4f} dy={dy:.4f} dist={dist:.4f} e={energy:.4f} ew={energy_weighted:.4f} axis={axis} "
                f"raw={raw_action or '-'} mapped={mapped_action or '-'} action={action or '-'} "
                f"amount={amount} cmd_ok={cmd_s} upper_bias={upper_bias_delta:.4f} "
                f"down_confirm={down_confirm_count}/{history_n} stable={stable_count}/{history_n} bypass={1 if strong_bypass else 0}",
                flush=True,
            )

        if (now - last_state_write) >= state_write_sec:
            _atomic_write_json(state_path, last_result)
            last_state_write = now

        if (now - last_summary) >= summary_sec:
            print(
                f"[ptz_motor_worker] summary ticks={counters['ticks']} frames={counters['frames']} "
                f"moves={counters['moves']} ok={counters['cmd_ok']} fail={counters['cmd_fail']} "
                f"no_frame={counters['no_frames']} stale={counters['stale_frames']} "
                f"down_hold={counters['down_holds']} stability_wait={counters['stability_waits']} "
                f"target_hold={counters['target_holds']} eye_hold={counters['eye_hold_bias']} eye_hold_cmd={counters['eye_hold_commands']} target_conf={target_conf:.3f} "
                f"cooldown={counters['move_cooldown_blocks']} cooldown_bypass={counters['move_cooldown_bypass']} micro={counters['micro_guard_blocks']} "
                f"axis_lock={counters['axis_locks']} axis_override={counters['axis_lock_overrides']} "
                f"eye_raw={counters['eye_pair_raw']} eye_geom={counters['eye_pair_geom_ok']} "
                f"eye_motion={counters['eye_pair_motion_gated']} eye_temp={counters['eye_pair_temporal_gated']} "
                f"eye_sel={counters['eye_pair_selected']} face_ok={counters['face_region_ok']} face_bonus={counters['face_region_bonus']} "
                f"eye_rej_m={counters['eye_pair_rejected_motion']} eye_rej_t={counters['eye_pair_rejected_temporal']} "
                f"last={reason}/{action or '-'} dx={dx:.3f} dy={dy:.3f} e={energy:.3f}",
                flush=True,
            )
            last_summary = now

        if cmd_ok is not True and move_cooldown_remaining > 0:
            move_cooldown_remaining = max(0, int(move_cooldown_remaining) - 1)

        if once:
            break
        if duration_sec > 0.0 and (_now() - started) >= float(duration_sec):
            break

        elapsed = time.monotonic() - loop_t0
        sleep_s = max(0.0, interval - elapsed)
        if sleep_s > 0.0:
            time.sleep(sleep_s)

    last_result["stopped_ts"] = int(_now())
    last_result["stopped"] = True
    _atomic_write_json(state_path, last_result)
    print(
        f"[ptz_motor_worker] stop ticks={counters['ticks']} frames={counters['frames']} moves={counters['moves']} "
        f"ok={counters['cmd_ok']} fail={counters['cmd_fail']}",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ORÓMA PTZ Motor Worker – persistent fast PTZ servo loop")
    parser.add_argument("--once", action="store_true", help="Run exactly one motor tick and exit.")
    parser.add_argument("--duration-sec", type=float, default=0.0, help="Run for N seconds, then exit. Default 0 = forever.")
    parser.add_argument("--verbose", action="store_true", help="Print per-tick telemetry.")
    args = parser.parse_args()
    try:
        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
    except Exception:
        pass
    return int(run_worker(duration_sec=float(args.duration_sec or 0.0), once=bool(args.once), verbose=bool(args.verbose)))


if __name__ == "__main__":
    raise SystemExit(main())
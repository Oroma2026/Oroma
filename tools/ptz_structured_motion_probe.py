#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_structured_motion_probe.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Structured Motion Probe – Regional Temporal Signature Evidence
# Version:   v3.7.3+p3a1-structured-motion-signature-v1.1
# Stand:     2026-06-16
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool ergänzt den bestehenden PTZ Positive-Position-/Eye-Face-Pfad um
# einen strikt getrennten, messenden Stage-A-Pfad für regionale zeitliche
# Bewegungssignaturen. Ausgangspunkt war die Live-Beobachtung, dass die Kamera
# kleine Menschen/Autos auf der Straße sehen kann, der bisherige Marker-Pfad
# aber bewusst eye/face-gated ist und Motion-only als Sicherheitsmaßnahme
# blockiert. P3a misst deshalb nicht „mehr Motion“, sondern unterscheidet pro
# Rasterzelle unterschiedliche Zeitsignaturen:
#
#   1) structured_blob_motion
#      Kleine/mittlere bewegte Blobs mit plausibler Schwerpunktwanderung.
#      Das ist der Kandidat für Straße/Mensch/Auto-artige Bewegung.
#
#   2) fixed_fast_change_region
#      Schnelle sichtbare Änderungen in einer festen Region. Gemeint ist KEINE
#      technische 50/60-Hz-Displayfrequenz, sondern Szenen-/UI-/Flächenwechsel
#      über einige Sekunden – z. B. TV-/Stream-artiges Verhalten.
#
#   3) fixed_low_change_display_region
#      Feste Region mit niedriger Änderungsrate, z. B. Alexa Show mit Uhrzeit/
#      Hintergrund. Wenn dort selten ein Stream läuft, darf dieselbe Zelle
#      zeitweise in fixed_fast_change_region wechseln; das ist korrektes
#      Verhalten, kein Messfehler.
#
#   4) dark_static_region
#      Dunkle statische Fläche, z. B. ausgeschalteter TV oder dunkles Display.
#
#   5) slow_drift_region
#      Langsame Helligkeitsänderung / Day-Night-Baseline. Diese Klasse dient vor
#      allem der Korrektur; sie ist kein Ziel für Motorik.
#
# ARCHITEKTUR-INVARIANTEN
# ───────────────────────
# - Measure-only Stage-A Evidence.
# - Keine PTZ-Motorbefehle.
# - Keine Policy-Aktivierung.
# - Keine Writes nach oroma.db.
# - Keine object_nodes/object_relations-Materialisierung.
# - Keine Änderung am bestehenden Eye/Face-/ptz.marker.*-Pfad.
# - Eigener Namespace: ptz.motion.*
# - Persistenter State nur in data/state/ptz_structured_motion_state.json.
# - Stats-Writes ausschließlich über DBWriter in stats.db.stats_points.
# - Stats-Writes sind idempotent per ON CONFLICT ... DO UPDATE.
# - Headless-only: kein Qt, kein Wayland, kein X11, keine GUI.
#
# ZEITSKALEN
# ──────────
# Das Tool trennt bewusst zwei Zeitskalen:
#
#   Kurzfristig pro Lauf:
#       12 Samples mit z. B. 0.35 s Abstand → ca. 4–6 s Messfenster.
#       Daraus entstehen fast_change/structured_blob-Metriken.
#
#   Langfristig im State:
#       pro Rasterzelle langsame EMA-Baselines für Helligkeit, Klassenpersistenz
#       und Wechselzähler. Daraus entstehen slow_drift/dark_static/Display-
#       Hinweise über viele Timer-Läufe.
#
# ENVIRONMENT
# ───────────
#   OROMA_BASE                                      Default: /opt/ai/oroma
#   OROMA_DBW_ENABLE                                Muss für --write-stats aktiv sein
#   OROMA_DBW_SOCKET                                Default: $OROMA_BASE/data/state/db_writer.sock
#   OROMA_PTZ_STRUCT_MOTION_STATE_PATH              Default: $OROMA_BASE/data/state/ptz_structured_motion_state.json
#   OROMA_PTZ_STRUCT_MOTION_GRID                    Default: 6
#   OROMA_PTZ_STRUCT_MOTION_SAMPLES                 Default: 12
#   OROMA_PTZ_STRUCT_MOTION_SAMPLE_INTERVAL_SEC     Default: 0.35
#   OROMA_PTZ_STRUCT_MOTION_MAX_FRAME_AGE_SEC       Default: 3.0
#   OROMA_PTZ_STRUCT_MOTION_W/H                     Default: 96 / 54
#   OROMA_PTZ_STRUCT_MOTION_DBW_TIMEOUT_MS          Default: 10000
#   OROMA_PTZ_STRUCT_MOTION_SYNTHETIC               Default: 0; nur Testmodus
#
# BEISPIELE
# ─────────
# Nur messen, JSON ausgeben:
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     python3 tools/ptz_structured_motion_probe.py --once --verbose
#
# Messen und Stage-A-Stats via DBWriter schreiben:
#   cd /opt/ai/oroma
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/ptz_structured_motion_probe.py --once --write-stats --verbose
#
# Synthetischer Selbsttest ohne Kamera:
#   OROMA_PTZ_STRUCT_MOTION_SYNTHETIC=1 \
#     python3 tools/ptz_structured_motion_probe.py --once --verbose
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

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
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        value = int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        value = int(default)
    if lo is not None:
        value = max(int(lo), value)
    if hi is not None:
        value = min(int(hi), value)
    return int(value)


def _env_float(name: str, default: float, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    try:
        value = float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    if lo is not None:
        value = max(float(lo), value)
    if hi is not None:
        value = min(float(hi), value)
    return float(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return float(out)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _state_path() -> Path:
    default = os.path.join(BASE, "data", "state", "ptz_structured_motion_state.json")
    return Path(os.environ.get("OROMA_PTZ_STRUCT_MOTION_STATE_PATH", default))


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def _key_hash(key: str) -> int:
    # Stable, short numeric hash for stats_points.value-compatible references.
    # The real key is always stored in stats_points.meta.
    h = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:8]
    return int(h, 16)


def _cell_key(grid: int, x: int, y: int) -> str:
    return f"g{int(grid)}:x{int(x)}:y{int(y)}"


def _norm01(v: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return max(0.0, min(1.0, float(v) / float(scale)))


def _frame_to_gray(frame: Any, w: int, h: int) -> Optional[Any]:
    if cv2 is None or np is None or frame is None:
        return None
    try:
        arr = frame
        if not hasattr(arr, "shape"):
            return None
        if len(arr.shape) == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        else:
            gray = arr
        gray = cv2.resize(gray, (int(w), int(h)), interpolation=cv2.INTER_AREA)
        return gray.astype("uint8")
    except Exception:
        return None


def _grid_means(gray: Any, grid: int) -> Dict[str, float]:
    if np is None:
        return {}
    h, w = gray.shape[:2]
    out: Dict[str, float] = {}
    for gy in range(int(grid)):
        y0 = int(round(gy * h / grid))
        y1 = int(round((gy + 1) * h / grid))
        for gx in range(int(grid)):
            x0 = int(round(gx * w / grid))
            x1 = int(round((gx + 1) * w / grid))
            cell = gray[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]
            out[_cell_key(grid, gx, gy)] = float(np.mean(cell)) if cell.size else 0.0
    return out


def _cell_from_xy(x: float, y: float, w: int, h: int, grid: int) -> Tuple[int, int, str]:
    gx = max(0, min(int(grid) - 1, int(float(x) / max(1.0, float(w)) * float(grid))))
    gy = max(0, min(int(grid) - 1, int(float(y) / max(1.0, float(h)) * float(grid))))
    return gx, gy, _cell_key(grid, gx, gy)


def _synthetic_frames(samples: int, w: int, h: int) -> List[Tuple[Any, float, str, Dict[str, Any]]]:
    if np is None or cv2 is None:
        return []
    frames: List[Tuple[Any, float, str, Dict[str, Any]]] = []
    now = time.time()
    for i in range(int(samples)):
        img = np.zeros((h, w, 3), dtype=np.uint8) + 35
        # Fixed low-change display in upper-right.
        cv2.rectangle(img, (int(w*0.70), int(h*0.08)), (int(w*0.95), int(h*0.30)), (45 + (i % 2), 45 + (i % 2), 45 + (i % 2)), -1)
        # Fast-change region in lower-right.
        val = 40 if i % 2 == 0 else 210
        cv2.rectangle(img, (int(w*0.70), int(h*0.55)), (int(w*0.95), int(h*0.85)), (val, val, val), -1)
        # Structured moving blob left-middle.
        cx = int(w * (0.15 + 0.035 * i))
        cy = int(h * 0.55)
        cv2.circle(img, (cx, cy), max(2, int(min(w, h) * 0.035)), (230, 230, 230), -1)
        frames.append((img, now + i * 0.35, "synthetic", {"synthetic": True}))
    return frames


def _read_frame() -> Tuple[Optional[Any], float, str, Dict[str, Any]]:
    if get_cached_frame_with_ts_fast_diag is None:
        return None, 0.0, "none", {"error": "get_cached_frame_with_ts_fast_diag unavailable"}
    try:
        frame, ts, src, meta = get_cached_frame_with_ts_fast_diag()
        return frame, float(ts or 0.0), str(src or "none"), meta if isinstance(meta, dict) else {}
    except Exception as exc:
        return None, 0.0, "none", {"error": str(exc)}


def collect_frames(samples: int, interval_s: float, max_age_s: float, w: int, h: int) -> List[Tuple[Any, float, str, Dict[str, Any]]]:
    if _env_bool("OROMA_PTZ_STRUCT_MOTION_SYNTHETIC", False):
        return _synthetic_frames(samples, w, h)
    out: List[Tuple[Any, float, str, Dict[str, Any]]] = []
    last_ts_seen = -1.0
    for i in range(max(1, int(samples))):
        frame, ts, src, meta = _read_frame()
        age = max(0.0, time.time() - float(ts or 0.0)) if float(ts or 0.0) > 0.0 else 999999.0
        if frame is not None and float(ts or 0.0) > 0.0 and age <= float(max_age_s):
            # Keep repeated cached frames too, but annotate count via meta; this avoids
            # falsely failing on slow camera cache while still showing low dynamics.
            out.append((frame, float(ts), src, dict(meta)))
            last_ts_seen = float(ts)
        if i < int(samples) - 1:
            time.sleep(max(0.0, float(interval_s)))
    return out


def analyze(frames: Sequence[Tuple[Any, float, str, Dict[str, Any]]], state: Mapping[str, Any]) -> Dict[str, Any]:
    grid = _env_int("OROMA_PTZ_STRUCT_MOTION_GRID", 6, lo=2, hi=16)
    w = _env_int("OROMA_PTZ_STRUCT_MOTION_W", 96, lo=48, hi=320)
    h = _env_int("OROMA_PTZ_STRUCT_MOTION_H", 54, lo=27, hi=240)
    dark_thr = _env_float("OROMA_PTZ_STRUCT_MOTION_DARK_THR", 28.0, lo=0.0, hi=255.0)
    low_change_thr = _env_float("OROMA_PTZ_STRUCT_MOTION_LOW_CHANGE_THR", 2.5, lo=0.1, hi=80.0)
    fast_change_thr = _env_float("OROMA_PTZ_STRUCT_MOTION_FAST_CHANGE_THR", 18.0, lo=1.0, hi=160.0)
    cut_thr = _env_float("OROMA_PTZ_STRUCT_MOTION_CUT_THR", 38.0, lo=1.0, hi=255.0)
    blob_thr = _env_float("OROMA_PTZ_STRUCT_MOTION_BLOB_DELTA_THR", 22.0, lo=1.0, hi=255.0)
    blob_min_area = _env_float("OROMA_PTZ_STRUCT_MOTION_BLOB_MIN_AREA", 4.0, lo=1.0, hi=5000.0)
    blob_max_area_ratio = _env_float("OROMA_PTZ_STRUCT_MOTION_BLOB_MAX_AREA_RATIO", 0.18, lo=0.001, hi=0.95)
    drift_min_norm = _env_float("OROMA_PTZ_STRUCT_MOTION_DRIFT_MIN_NORM", 0.035, lo=0.0, hi=1.0)
    ema_alpha = _env_float("OROMA_PTZ_STRUCT_MOTION_EMA_ALPHA", 0.08, lo=0.001, hi=1.0)

    now = int(time.time())
    grays: List[Any] = []
    ts_list: List[float] = []
    src_count: Dict[str, int] = {}
    for frame, ts, src, meta in frames:
        gray = _frame_to_gray(frame, w, h)
        if gray is not None:
            grays.append(gray)
            ts_list.append(float(ts))
            src_count[str(src)] = src_count.get(str(src), 0) + 1

    if not grays:
        now = int(time.time())
        empty_top = lambda cls: {"key": "", "key_hash": 0, "score": 0.0, "class": cls}
        summary = {
            "ok": True,
            "ts": now,
            "stage": "P3a_regional_temporal_motion_signature_measure_only",
            "base": BASE,
            "state_path": str(_state_path()),
            "grid": grid,
            "sample_requested": _env_int("OROMA_PTZ_STRUCT_MOTION_SAMPLES", 12, lo=2, hi=60),
            "sample_count": 0,
            "frame_source_counts": {},
            "frame_first_ts": 0.0,
            "frame_last_ts": 0.0,
            "cell_count": int(grid) * int(grid),
            "structured_candidate_count": 0,
            "fast_change_region_count": 0,
            "low_change_region_count": 0,
            "low_change_display_candidate_count": 0,
            "dark_static_region_count": 0,
            "slow_drift_region_count": 0,
            "cut_like_count": 0,
            "no_frame": 1,
            "top_structured": empty_top("structured_blob_motion"),
            "top_fast_change": empty_top("fixed_fast_change_region"),
            "top_low_change_display": empty_top("fixed_low_change_display_region"),
            "top_dark_static": empty_top("dark_static_region"),
            "top_slow_drift": empty_top("slow_drift_region"),
            "motion_namespace": "ptz.motion.*",
            "measure_only": True,
            "no_motor_control": True,
            "no_policy_activation": True,
            "no_materialization": True,
        }
        state_out = {
            "version": 1,
            "updated_ts": now,
            "stage": summary["stage"],
            "grid": grid,
            "sample_count_last": 0,
            "no_frame_last": 1,
            "class_counts_last": {
                "structured_blob_motion": 0,
                "fixed_fast_change_region": 0,
                "low_change_region": 0,
                "fixed_low_change_display_candidate": 0,
                "dark_static_region": 0,
                "slow_drift_region": 0,
            },
            "top_structured": summary["top_structured"],
            "top_fast_change": summary["top_fast_change"],
            "top_low_change_region": summary.get("top_low_change_region", {}),
            "top_low_change_display": summary["top_low_change_display"],
            "top_dark_static": summary["top_dark_static"],
            "top_slow_drift": summary["top_slow_drift"],
            "cells": state.get("cells", {}) if isinstance(state.get("cells"), Mapping) else {},
            "note": "No fresh cached frames available; previous cell baselines preserved. Measure-only, no PTZ motor control.",
        }
        summary["state_written"] = False
        _atomic_write_json(_state_path(), state_out)
        summary["state_written"] = True
        return summary

    cells: Dict[str, Dict[str, Any]] = {}
    for gy in range(grid):
        for gx in range(grid):
            key = _cell_key(grid, gx, gy)
            cells[key] = {
                "key": key, "x": gx, "y": gy, "grid": grid,
                "brightness_values": [], "delta_values": [],
                "blob_hits": 0, "blob_area_sum": 0.0, "blob_centroids": [],
            }

    if grays:
        means_per_frame = [_grid_means(g, grid) for g in grays]
        for key in cells:
            vals = [float(m.get(key, 0.0)) for m in means_per_frame]
            cells[key]["brightness_values"] = vals
            deltas = [abs(vals[i] - vals[i-1]) for i in range(1, len(vals))]
            cells[key]["delta_values"] = deltas

    if cv2 is not None and np is not None and len(grays) >= 2:
        max_area = float(w * h) * float(blob_max_area_ratio)
        for i in range(1, len(grays)):
            diff = cv2.absdiff(grays[i], grays[i-1])
            _, mask = cv2.threshold(diff, float(blob_thr), 255, cv2.THRESH_BINARY)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
            contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                area = float(cv2.contourArea(c))
                if area < float(blob_min_area) or area > float(max_area):
                    continue
                moments = cv2.moments(c)
                if abs(float(moments.get("m00") or 0.0)) < 1e-9:
                    continue
                cx = float(moments["m10"] / moments["m00"])
                cy = float(moments["m01"] / moments["m00"])
                _gx, _gy, key = _cell_from_xy(cx, cy, w, h, grid)
                cells[key]["blob_hits"] = int(cells[key].get("blob_hits") or 0) + 1
                cells[key]["blob_area_sum"] = float(cells[key].get("blob_area_sum") or 0.0) + area
                cells[key].setdefault("blob_centroids", []).append([round(cx / max(1, w), 4), round(cy / max(1, h), 4)])

    old_cells = state.get("cells") if isinstance(state.get("cells"), Mapping) else {}
    new_state_cells: Dict[str, Any] = {}
    class_counts = {
        "structured_blob_motion": 0,
        "fixed_fast_change_region": 0,
        "low_change_region": 0,
        "fixed_low_change_display_candidate": 0,
        "dark_static_region": 0,
        "slow_drift_region": 0,
    }
    top_by_class: Dict[str, Dict[str, Any]] = {}
    total_cut_like = 0

    for key, item in cells.items():
        vals = list(item.get("brightness_values") or [])
        deltas = list(item.get("delta_values") or [])
        mean_b = float(sum(vals) / len(vals)) if vals else 0.0
        mean_delta = float(sum(deltas) / len(deltas)) if deltas else 0.0
        max_delta = float(max(deltas)) if deltas else 0.0
        cut_like = int(sum(1 for d in deltas if float(d) >= cut_thr))
        total_cut_like += cut_like
        blob_hits = int(item.get("blob_hits") or 0)
        centroids = item.get("blob_centroids") if isinstance(item.get("blob_centroids"), list) else []
        centroid_drift = 0.0
        if len(centroids) >= 2:
            x0, y0 = centroids[0]
            x1, y1 = centroids[-1]
            centroid_drift = math.sqrt((float(x1)-float(x0))**2 + (float(y1)-float(y0))**2)
        old = old_cells.get(key) if isinstance(old_cells.get(key), Mapping) else {}
        old_ema = _safe_float(old.get("brightness_ema_slow"), mean_b)
        ema = (1.0 - ema_alpha) * old_ema + ema_alpha * mean_b
        slow_delta = abs(mean_b - old_ema)

        fast_score = max(_norm01(mean_delta - fast_change_thr, 45.0), _norm01(max_delta - cut_thr, 90.0))
        dark_score = (1.0 - _norm01(mean_b, max(1.0, dark_thr))) * (1.0 - _norm01(mean_delta, low_change_thr * 2.0))
        low_change_score = (1.0 - _norm01(mean_delta, low_change_thr * 3.0)) * (1.0 - _norm01(centroid_drift, max(0.001, drift_min_norm)))
        drift_score = _norm01(slow_delta, 45.0) * (1.0 - _norm01(mean_delta, fast_change_thr))
        structured_score = _norm01(blob_hits, max(2.0, len(grays) * 0.6)) * _norm01(centroid_drift, max(0.001, drift_min_norm * 3.0)) * (1.0 - min(0.85, fast_score * 0.75))

        # P3a.1: keep quiet cells neutral. A short 4–6 second low-change window
        # in a dark room must not be counted as "display" for every grid cell.
        # Display-candidate evidence is only promoted when the same cell remains
        # the dominant low-change region across several probe runs.
        scores = {
            "structured_blob_motion": structured_score,
            "fixed_fast_change_region": fast_score,
            "low_change_region": low_change_score,
            "dark_static_region": dark_score,
            "slow_drift_region": drift_score,
        }
        class_name = max(scores.items(), key=lambda kv: kv[1])[0]
        class_score = float(scores[class_name])
        if class_score < 0.18:
            class_name = "low_evidence"

        prev_class = str(old.get("class_last") or "")
        switch_count = _safe_int(old.get("class_switch_count"), 0)
        if prev_class and class_name != prev_class:
            switch_count += 1

        low_change_seen_count = _safe_int(old.get("low_change_seen_count"), 0)
        if class_name == "low_change_region" and class_score >= _env_float("OROMA_PTZ_STRUCT_MOTION_LOW_REGION_MIN_SCORE", 0.50, lo=0.05, hi=1.0):
            low_change_seen_count += 1
        else:
            low_change_seen_count = max(0, low_change_seen_count - 1)

        display_min_seen = _env_int("OROMA_PTZ_STRUCT_MOTION_DISPLAY_MIN_SEEN", 3, lo=1, hi=1000)
        display_candidate_score = low_change_score if low_change_seen_count >= display_min_seen else 0.0
        if display_candidate_score > 0.0:
            class_counts["fixed_low_change_display_candidate"] += 1
            curr_top_display = top_by_class.get("fixed_low_change_display_candidate")
            if curr_top_display is None or display_candidate_score > float(curr_top_display.get("score") or 0.0):
                top_by_class["fixed_low_change_display_candidate"] = {
                    "key": key,
                    "key_hash": _key_hash(key),
                    "score": round(display_candidate_score, 6),
                    "class": "fixed_low_change_display_candidate",
                    "mean_brightness": round(mean_b, 3),
                    "mean_delta": round(mean_delta, 3),
                    "max_delta": round(max_delta, 3),
                    "blob_hits": blob_hits,
                    "centroid_drift": round(centroid_drift, 6),
                    "class_switch_count": switch_count,
                    "low_change_seen_count": low_change_seen_count,
                }

        if class_name in class_counts:
            class_counts[class_name] += 1
            curr_top = top_by_class.get(class_name)
            if curr_top is None or class_score > float(curr_top.get("score") or 0.0):
                top_by_class[class_name] = {
                    "key": key,
                    "key_hash": _key_hash(key),
                    "score": round(class_score, 6),
                    "class": class_name,
                    "mean_brightness": round(mean_b, 3),
                    "mean_delta": round(mean_delta, 3),
                    "max_delta": round(max_delta, 3),
                    "blob_hits": blob_hits,
                    "centroid_drift": round(centroid_drift, 6),
                    "class_switch_count": switch_count,
                    "low_change_seen_count": low_change_seen_count,
                }

        new_state_cells[key] = {
            "key": key,
            "grid": grid,
            "brightness_ema_slow": round(ema, 6),
            "brightness_last": round(mean_b, 6),
            "mean_delta_last": round(mean_delta, 6),
            "max_delta_last": round(max_delta, 6),
            "blob_hits_last": blob_hits,
            "centroid_drift_last": round(centroid_drift, 6),
            "scores_last": {k: round(v, 6) for k, v in scores.items()},
            "class_last": class_name,
            "class_prev": prev_class,
            "class_switch_count": switch_count,
            "low_change_seen_count": low_change_seen_count,
            "display_candidate_score_last": round(display_candidate_score, 6),
            "last_ts": now,
        }

    def top(cls: str) -> Dict[str, Any]:
        return top_by_class.get(cls) or {"key": "", "key_hash": 0, "score": 0.0, "class": cls}

    summary: Dict[str, Any] = {
        "ok": True,
        "ts": now,
        "stage": "P3a_regional_temporal_motion_signature_measure_only",
        "base": BASE,
        "state_path": str(_state_path()),
        "grid": grid,
        "sample_requested": _env_int("OROMA_PTZ_STRUCT_MOTION_SAMPLES", 12, lo=2, hi=60),
        "sample_count": len(grays),
        "frame_source_counts": src_count,
        "frame_first_ts": min(ts_list) if ts_list else 0.0,
        "frame_last_ts": max(ts_list) if ts_list else 0.0,
        "cell_count": len(cells),
        "structured_candidate_count": class_counts["structured_blob_motion"],
        "fast_change_region_count": class_counts["fixed_fast_change_region"],
        "low_change_region_count": class_counts["low_change_region"],
        "low_change_display_candidate_count": class_counts["fixed_low_change_display_candidate"],
        "low_change_display_region_count": class_counts["fixed_low_change_display_candidate"],
        "dark_static_region_count": class_counts["dark_static_region"],
        "slow_drift_region_count": class_counts["slow_drift_region"],
        "cut_like_count": total_cut_like,
        "top_structured": top("structured_blob_motion"),
        "top_fast_change": top("fixed_fast_change_region"),
        "top_low_change_region": top("low_change_region"),
        "top_low_change_display": top("fixed_low_change_display_candidate"),
        "top_dark_static": top("dark_static_region"),
        "top_slow_drift": top("slow_drift_region"),
        "motion_namespace": "ptz.motion.*",
        "measure_only": True,
        "no_motor_control": True,
        "no_policy_activation": True,
        "no_materialization": True,
    }

    state_out = {
        "version": 1,
        "updated_ts": now,
        "stage": summary["stage"],
        "grid": grid,
        "sample_count_last": len(grays),
        "class_counts_last": class_counts,
        "top_structured": summary["top_structured"],
        "top_fast_change": summary["top_fast_change"],
        "top_low_change_display": summary["top_low_change_display"],
        "top_dark_static": summary["top_dark_static"],
        "top_slow_drift": summary["top_slow_drift"],
        "cells": new_state_cells,
        "note": "Measure-only regional temporal motion signature evidence; no PTZ motor control, no policy, no materialization.",
    }
    summary["state_written"] = False
    _atomic_write_json(_state_path(), state_out)
    summary["state_written"] = True
    return summary


def build_summary() -> Dict[str, Any]:
    samples = _env_int("OROMA_PTZ_STRUCT_MOTION_SAMPLES", 12, lo=2, hi=60)
    interval = _env_float("OROMA_PTZ_STRUCT_MOTION_SAMPLE_INTERVAL_SEC", 0.35, lo=0.0, hi=5.0)
    max_age = _env_float("OROMA_PTZ_STRUCT_MOTION_MAX_FRAME_AGE_SEC", 3.0, lo=0.05, hi=300.0)
    w = _env_int("OROMA_PTZ_STRUCT_MOTION_W", 96, lo=48, hi=320)
    h = _env_int("OROMA_PTZ_STRUCT_MOTION_H", 54, lo=27, hi=240)
    state = _read_json(_state_path())
    frames = collect_frames(samples=samples, interval_s=interval, max_age_s=max_age, w=w, h=h)
    summary = analyze(frames, state)
    return summary


def _dbw_timeout_ms() -> int:
    return _env_int("OROMA_PTZ_STRUCT_MOTION_DBW_TIMEOUT_MS", 10000, lo=500, hi=120000)


def _dbw_required() -> None:
    if dbw is None:
        raise RuntimeError("DBWriter client module unavailable")
    try:
        if dbw.enabled() and dbw.ping(timeout_ms=1000):
            return
    except Exception:
        pass
    sock = os.environ.get("OROMA_DBW_SOCKET", os.path.join(BASE, "data", "state", "db_writer.sock"))
    if os.path.exists(sock):
        try:
            client = getattr(dbw, "_client")()
            resp = client.request(op="ping", timeout_ms=1000, expect="none", tag="ptz.structured_motion_probe.ping")
            if bool(resp.get("ok")):
                return
        except Exception:
            pass
    raise RuntimeError("DBWriter required but not available/enabled")


def _metric_rows(summary: Mapping[str, Any]) -> Iterable[Tuple[str, float, Dict[str, Any]]]:
    common = {
        "grid": summary.get("grid"),
        "sample_count": summary.get("sample_count"),
        "stage": summary.get("stage"),
        "top_structured_key": (summary.get("top_structured") or {}).get("key") if isinstance(summary.get("top_structured"), Mapping) else "",
        "top_fast_change_key": (summary.get("top_fast_change") or {}).get("key") if isinstance(summary.get("top_fast_change"), Mapping) else "",
        "top_low_change_region_key": (summary.get("top_low_change_region") or {}).get("key") if isinstance(summary.get("top_low_change_region"), Mapping) else "",
        "top_low_change_display_key": (summary.get("top_low_change_display") or {}).get("key") if isinstance(summary.get("top_low_change_display"), Mapping) else "",
        "top_dark_static_key": (summary.get("top_dark_static") or {}).get("key") if isinstance(summary.get("top_dark_static"), Mapping) else "",
        "top_slow_drift_key": (summary.get("top_slow_drift") or {}).get("key") if isinstance(summary.get("top_slow_drift"), Mapping) else "",
    }

    def top_value(name: str, field: str, default: float = 0.0) -> float:
        item = summary.get(name)
        if isinstance(item, Mapping):
            return _safe_float(item.get(field), default)
        return float(default)

    def top_meta(cls: str, top_name: str) -> Dict[str, Any]:
        item = summary.get(top_name)
        out = dict(common)
        out["class"] = cls
        if isinstance(item, Mapping):
            out.update({
                "top_key": item.get("key"),
                "top_key_hash": item.get("key_hash"),
                "top_score": item.get("score"),
                "mean_brightness": item.get("mean_brightness"),
                "mean_delta": item.get("mean_delta"),
                "max_delta": item.get("max_delta"),
                "blob_hits": item.get("blob_hits"),
                "centroid_drift": item.get("centroid_drift"),
                "class_switch_count": item.get("class_switch_count"),
            })
        return out

    yield "ptz.motion.samples.frame_count", _safe_float(summary.get("sample_count")), dict(common)
    yield "ptz.motion.samples.no_frame", _safe_float(summary.get("no_frame", 0)), dict(common)
    yield "ptz.motion.samples.cell_count", _safe_float(summary.get("cell_count")), dict(common)
    yield "ptz.motion.structured.candidate_count", _safe_float(summary.get("structured_candidate_count")), top_meta("structured_blob_motion", "top_structured")
    yield "ptz.motion.structured.top_score", top_value("top_structured", "score"), top_meta("structured_blob_motion", "top_structured")
    yield "ptz.motion.structured.top_key_hash", top_value("top_structured", "key_hash"), top_meta("structured_blob_motion", "top_structured")
    yield "ptz.motion.fast_change.region_count", _safe_float(summary.get("fast_change_region_count")), top_meta("fixed_fast_change_region", "top_fast_change")
    yield "ptz.motion.fast_change.top_score", top_value("top_fast_change", "score"), top_meta("fixed_fast_change_region", "top_fast_change")
    yield "ptz.motion.fast_change.cut_like_count", _safe_float(summary.get("cut_like_count")), top_meta("fixed_fast_change_region", "top_fast_change")
    yield "ptz.motion.low_change.region_count", _safe_float(summary.get("low_change_region_count")), top_meta("low_change_region", "top_low_change_region")
    yield "ptz.motion.low_change.top_score", top_value("top_low_change_region", "score"), top_meta("low_change_region", "top_low_change_region")
    yield "ptz.motion.low_change_display.candidate_count", _safe_float(summary.get("low_change_display_candidate_count")), top_meta("fixed_low_change_display_candidate", "top_low_change_display")
    yield "ptz.motion.low_change_display.top_score", top_value("top_low_change_display", "score"), top_meta("fixed_low_change_display_candidate", "top_low_change_display")
    # Legacy-compatible series name now carries display_candidate_count, not all quiet cells.
    yield "ptz.motion.low_change_display.region_count", _safe_float(summary.get("low_change_display_candidate_count")), top_meta("fixed_low_change_display_candidate", "top_low_change_display")
    yield "ptz.motion.dark_static.region_count", _safe_float(summary.get("dark_static_region_count")), top_meta("dark_static_region", "top_dark_static")
    yield "ptz.motion.dark_static.top_score", top_value("top_dark_static", "score"), top_meta("dark_static_region", "top_dark_static")
    yield "ptz.motion.slow_drift.region_count", _safe_float(summary.get("slow_drift_region_count")), top_meta("slow_drift_region", "top_slow_drift")
    yield "ptz.motion.slow_drift.max_score", top_value("top_slow_drift", "score"), top_meta("slow_drift_region", "top_slow_drift")


def write_stats(summary: Mapping[str, Any]) -> int:
    _dbw_required()
    ts = _safe_int(summary.get("ts"), int(time.time()))
    written = 0
    for series, value, meta in _metric_rows(summary):
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        src_uid = f"ptz_structured_motion_probe:{ts}:{series}"
        dbw.exec_write(
            """
            INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(src_table, src_uid, series) DO UPDATE SET
              ts=excluded.ts,
              value=excluded.value,
              src_id=excluded.src_id,
              meta=excluded.meta
            """,
            params=[int(ts), str(series), float(value), "ptz_structured_motion_probe", 0, meta_json, src_uid],
            tag="ptz.structured_motion_probe.stats_points.upsert",
            priority="low",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
        written += 1
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ORÓMA PTZ Structured Motion Probe – Regional Temporal Signature Evidence / Measure-only")
    parser.add_argument("--once", action="store_true", help="Einmal ausführen und JSON-Summary ausgeben.")
    parser.add_argument("--write-stats", action="store_true", help="Metriken via DBWriter in stats.db.stats_points schreiben.")
    parser.add_argument("--verbose", action="store_true", help="Zusätzliche Statuszeile ausgeben.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = build_summary()
    if args.write_stats:
        summary["stats_written"] = write_stats(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.verbose:
        print(
            "[ptz_structured_motion_probe] "
            f"ok={summary.get('ok')} samples={summary.get('sample_count')} grid={summary.get('grid')} "
            f"structured={summary.get('structured_candidate_count')} fast={summary.get('fast_change_region_count')} "
            f"low_display={summary.get('low_change_display_region_count')} dark={summary.get('dark_static_region_count')} "
            f"top_structured={(summary.get('top_structured') or {}).get('key') if isinstance(summary.get('top_structured'), Mapping) else '-'} "
            f"stats_written={summary.get('stats_written', 0)}"
        )
    return 0 if bool(summary.get("ok")) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

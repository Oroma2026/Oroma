#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_zoom_context_probe.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Zoom Context Probe – P3z0 Wide/Detail Context Evidence
# Version:   v3.7.3+p3z01-wide-fov-context-gain-v1.1
# Stand:     2026-06-21
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool prüft die aus dem Live-System abgeleitete Hypothese, dass die
# EMEET PIXY bei `zoom_absolute=130` zu eng beobachtet und Außen-/Straßenkontext
# bei `zoom_absolute=100` bereits sichtbar wird. Es ist der vorgeschaltete,
# billigere Schritt vor einem späteren PTZ-ViewMap-/Pan/Tilt-Sweep.
#
# P3z0 vergleicht nicht pixelgenau. Unterschiedliche Zoomstufen verzerren
# Helligkeit, Kanten, Zellgrößen und Blob-Flächen. Deshalb nutzt dieses Tool
# bewusst nur aggregierte, klassenbasierte P3a.1-Signaturen:
#
#   - structured_blob_motion
#   - fixed_fast_change_region
#   - low_change_region
#   - fixed_low_change_display_candidate
#   - dark_static_region
#   - slow_drift_region
#
# ARCHITEKTUR-INVARIANTEN
# ───────────────────────
# - Measure-only Evidence mit kontrollierter Zoom-Rückstellung.
# - Keine Pan/Tilt-Bewegung.
# - Keine Policy-Aktivierung.
# - Keine object_nodes/object_relations-Materialisierung.
# - Keine Writes nach `oroma.db`.
# - Stats-Writes ausschließlich via DBWriter nach `stats.db.stats_points`.
# - Alle Zoom-Werte werden zur Laufzeit aus V4L2 gelesen; keine harten Live-
#   Grenzen aus Dokumentation/Headern.
# - Bei jedem Lauf wird der ursprüngliche Zoom in einem `finally`-Pfad wieder-
#   hergestellt, soweit V4L2 verfügbar ist.
# - Headless-only: kein Qt, Wayland oder X11.
#
# ENTSCHEIDUNGSLOGIK
# ──────────────────
# Ein einzelner Lauf darf keine endgültige Architekturentscheidung treffen, weil
# Straße/Autos/Menschen zeitlich zufällig sichtbar oder nicht sichtbar sein
# können. P3z0 schreibt deshalb Zeitreihen unter `ptz.zoom_context.*`; der
# Report aggregiert über 1h/6h/24h. Erst wiederholte Evidenz wie eine erhöhte
# wide_helpful_rate oder ein positiver average_delta sollte später eine
# Zoom-Policy begründen.
#
# ENVIRONMENT
# ───────────
#   OROMA_BASE                                      Default: /opt/ai/oroma
#   OROMA_DBW_ENABLE                                Muss für --write-stats aktiv sein
#   OROMA_PTZ_DEVICE                                Default: EMEET by-id index0, fallback /dev/video0
#   OROMA_PTZ_ZOOM_CONTEXT_STATE_PATH               Default: data/state/ptz_zoom_context_probe_state.json
#   OROMA_PTZ_ZOOM_CONTEXT_WIDE_ZOOM                Default: 100
#   OROMA_PTZ_ZOOM_CONTEXT_RESTORE                  Default: 1
#   OROMA_PTZ_ZOOM_CONTEXT_SETTLE_SEC               Default: 3.0
#   OROMA_PTZ_ZOOM_CONTEXT_SAMPLES                  Default: 24
#   OROMA_PTZ_ZOOM_CONTEXT_SAMPLE_INTERVAL_SEC      Default: 0.25
#   OROMA_PTZ_ZOOM_CONTEXT_DBW_TIMEOUT_MS           Default: 10000
#   OROMA_PTZ_ZOOM_CONTEXT_SYNTHETIC                Default: 0; Testmodus ohne V4L2/Kamera
#   OROMA_PTZ_ZOOM_CONTEXT_HELPFUL_DELTA            Default: 0.25
#   OROMA_PTZ_ZOOM_CONTEXT_FOV_GAIN_DELTA           Default: 0.15
#   OROMA_PTZ_ZOOM_CONTEXT_EDGE_GAIN_DELTA          Default: 0.10
#   OROMA_PTZ_ZOOM_CONTEXT_USABLE_GAIN_DELTA        Default: 2.0
#
# BEISPIELE
# ─────────
# Manuell messen, JSON ausgeben, Zoom danach wiederherstellen:
#   cd /opt/ai/oroma
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/ptz_zoom_context_probe.py --once --write-stats --verbose
#
# Nur Report lesen:
#   python3 tools/ptz_zoom_context_evidence_report.py --text --verbose
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore

try:
    from tools import ptz_structured_motion_probe as p3a  # type: ignore
except Exception:  # pragma: no cover
    p3a = None  # type: ignore


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
    default = os.path.join(BASE, "data", "state", "ptz_zoom_context_probe_state.json")
    return Path(os.environ.get("OROMA_PTZ_ZOOM_CONTEXT_STATE_PATH", default))


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def _default_device() -> str:
    by_id = "/dev/v4l/by-id/usb-EMEET_EMEET_PIXY_A250607001103370-video-index0"
    if os.path.exists(by_id):
        return by_id
    return "/dev/video0"


def _device() -> str:
    return os.environ.get("OROMA_PTZ_DEVICE") or os.environ.get("OROMA_PTZ_V4L2_DEVICE") or _default_device()


def _run_cmd(argv: Sequence[str], timeout_s: float = 3.0) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(list(argv), text=True, capture_output=True, timeout=float(timeout_s))
        return int(p.returncode), str(p.stdout or ""), str(p.stderr or "")
    except Exception as exc:
        return 999, "", str(exc)


def _read_ctrls(dev: str) -> Dict[str, Any]:
    rc, out, err = _run_cmd(["v4l2-ctl", "-d", str(dev), "-C", "pan_absolute", "-C", "tilt_absolute", "-C", "zoom_absolute"], timeout_s=3.0)
    vals: Dict[str, Any] = {"ok": rc == 0, "rc": rc, "err": err.strip(), "device": dev, "controls": {}}
    for line in out.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        if k in ("pan_absolute", "tilt_absolute", "zoom_absolute"):
            vals["controls"][k] = _safe_int(v.strip(), 0)
    return vals


def _set_zoom(dev: str, zoom: int) -> Dict[str, Any]:
    rc, out, err = _run_cmd(["v4l2-ctl", "-d", str(dev), "-c", f"zoom_absolute={int(zoom)}"], timeout_s=5.0)
    return {"ok": rc == 0, "rc": rc, "out": out.strip(), "err": err.strip(), "zoom": int(zoom), "device": dev}


def _compact_signature(summary: Mapping[str, Any]) -> Dict[str, Any]:
    def top(name: str) -> Mapping[str, Any]:
        v = summary.get(name)
        return v if isinstance(v, Mapping) else {}
    return {
        "ts": _safe_int(summary.get("ts"), int(time.time())),
        "sample_count": _safe_int(summary.get("sample_count"), 0),
        "cell_count": _safe_int(summary.get("cell_count"), 0),
        "structured_candidate_count": _safe_float(summary.get("structured_candidate_count"), 0.0),
        "structured_top_score": _safe_float(top("top_structured").get("score"), 0.0),
        "fast_change_region_count": _safe_float(summary.get("fast_change_region_count"), 0.0),
        "fast_change_top_score": _safe_float(top("top_fast_change").get("score"), 0.0),
        "low_change_region_count": _safe_float(summary.get("low_change_region_count"), 0.0),
        "low_change_top_score": _safe_float(top("top_low_change_region").get("score"), 0.0),
        "low_change_display_candidate_count": _safe_float(summary.get("low_change_display_candidate_count"), 0.0),
        "dark_static_region_count": _safe_float(summary.get("dark_static_region_count"), 0.0),
        "slow_drift_region_count": _safe_float(summary.get("slow_drift_region_count"), 0.0),
        "top_structured_key": str(top("top_structured").get("key") or ""),
        "top_fast_change_key": str(top("top_fast_change").get("key") or ""),
        "top_low_change_region_key": str(top("top_low_change_region").get("key") or ""),
        "top_display_candidate_key": str(top("top_low_change_display").get("key") or ""),
    }


def _p3a_state_path() -> Path:
    """Return the P3a state path without depending on a public P3a helper API."""
    default = os.path.join(BASE, "data", "state", "ptz_structured_motion_state.json")
    return Path(os.environ.get("OROMA_PTZ_STRUCT_MOTION_STATE_PATH", default))


def _parse_cell_xy(key: str) -> Tuple[int, int]:
    x = 0
    y = 0
    try:
        for part in str(key).split(":"):
            if part.startswith("x"):
                x = int(part[1:])
            elif part.startswith("y"):
                y = int(part[1:])
    except Exception:
        return 0, 0
    return x, y


def _mean(vals: Sequence[float]) -> float:
    return float(sum(vals) / len(vals)) if vals else 0.0


def _std(vals: Sequence[float]) -> float:
    if not vals:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((float(v) - m) ** 2 for v in vals) / len(vals))


def _entropy(labels: Sequence[str]) -> float:
    counts: Dict[str, int] = {}
    for label in labels:
        key = str(label or "unknown")
        counts[key] = counts.get(key, 0) + 1
    total = float(sum(counts.values()))
    if total <= 0:
        return 0.0
    ent = 0.0
    for count in counts.values():
        p = float(count) / total
        if p > 0:
            ent -= p * math.log(p, 2)
    return float(ent)


def _load_p3a_cell_context(label: str) -> Dict[str, Any]:
    """
    Build a zoom-aware, non-semantic context summary from the P3a cell state.

    P3z0.1 intentionally does not try to identify a street or reconstruct true
    camera geometry. It asks a safer edge question: did the wider zoom expose
    more non-uniform, usable edge/bottom-right context than the detail zoom?
    This is robust enough for Stage-A evidence and keeps the path headless and
    measure-only.
    """
    state = _read_json(_p3a_state_path())
    cells_raw = state.get("cells") if isinstance(state.get("cells"), Mapping) else {}
    cells: List[Mapping[str, Any]] = [v for v in cells_raw.values() if isinstance(v, Mapping)]
    if not cells:
        return {
            "label": label,
            "ok": False,
            "cell_count": 0,
            "grid": 0,
            "edge_context_score": 0.0,
            "wide_fov_context_score": 0.0,
            "usable_region_count": 0.0,
            "edge_usable_region_count": 0.0,
            "bottom_right_context_score": 0.0,
            "error": str(state.get("_read_error") or "no P3a cells available"),
        }

    grid = _safe_int(state.get("grid"), 0)
    if grid <= 0:
        grid = int(round(math.sqrt(max(1, len(cells)))))
    max_idx = max(0, grid - 1)

    brightness_all: List[float] = []
    delta_all: List[float] = []
    brightness_edge: List[float] = []
    delta_edge: List[float] = []
    brightness_bottom_right: List[float] = []
    delta_bottom_right: List[float] = []
    labels_all: List[str] = []
    labels_edge: List[str] = []
    usable = 0
    edge_usable = 0
    bottom_right_usable = 0
    dark = 0

    for item in cells:
        key = str(item.get("key") or "")
        x, y = _parse_cell_xy(key)
        cls = str(item.get("class_last") or "unknown")
        b = _safe_float(item.get("brightness_last"), 0.0)
        d = max(_safe_float(item.get("mean_delta_last"), 0.0), _safe_float(item.get("max_delta_last"), 0.0) * 0.35)
        labels_all.append(cls)
        brightness_all.append(b)
        delta_all.append(d)
        is_dark = cls == "dark_static_region" or b < _env_float("OROMA_PTZ_ZOOM_CONTEXT_DARK_BRIGHTNESS_MAX", 18.0, lo=0.0, hi=255.0)
        is_usable = not is_dark and cls not in ("unknown", "")
        if is_dark:
            dark += 1
        if is_usable:
            usable += 1
        is_edge = x == 0 or y == 0 or x == max_idx or y == max_idx
        is_bottom_right = x >= max(0, grid - 2) or y >= max(0, grid - 2)
        if is_edge:
            labels_edge.append(cls)
            brightness_edge.append(b)
            delta_edge.append(d)
            if is_usable:
                edge_usable += 1
        if is_bottom_right:
            brightness_bottom_right.append(b)
            delta_bottom_right.append(d)
            if is_usable:
                bottom_right_usable += 1

    def rng(vals: Sequence[float]) -> float:
        return float(max(vals) - min(vals)) if vals else 0.0

    brightness_range = rng(brightness_all)
    edge_range = rng(brightness_edge)
    bottom_right_range = rng(brightness_bottom_right)
    brightness_std = _std(brightness_all)
    edge_std = _std(brightness_edge)
    bottom_right_std = _std(brightness_bottom_right)
    class_diversity = len(set(labels_all))
    edge_diversity = len(set(labels_edge))
    entropy_all = _entropy(labels_all)
    entropy_edge = _entropy(labels_edge)

    edge_context_score = (
        min(3.0, edge_range / 24.0)
        + min(3.0, edge_std / 12.0)
        + min(2.0, _mean(delta_edge) / 12.0)
        + min(2.0, float(edge_usable) / max(1.0, float(len(brightness_edge))) * 2.0)
        + min(1.5, entropy_edge)
    )
    bottom_right_context_score = (
        min(3.0, bottom_right_range / 24.0)
        + min(3.0, bottom_right_std / 12.0)
        + min(2.0, _mean(delta_bottom_right) / 12.0)
        + min(2.0, float(bottom_right_usable) / max(1.0, float(len(brightness_bottom_right))) * 2.0)
    )
    wide_fov_context_score = (
        0.35 * min(5.0, brightness_range / 18.0)
        + 0.35 * min(5.0, brightness_std / 9.0)
        + 0.45 * edge_context_score
        + 0.25 * bottom_right_context_score
        + 0.25 * min(3.0, entropy_all)
        + 0.10 * float(class_diversity)
    )

    return {
        "label": label,
        "ok": True,
        "grid": grid,
        "cell_count": len(cells),
        "usable_region_count": float(usable),
        "edge_usable_region_count": float(edge_usable),
        "bottom_right_usable_region_count": float(bottom_right_usable),
        "dark_region_count": float(dark),
        "class_diversity_count": float(class_diversity),
        "edge_class_diversity_count": float(edge_diversity),
        "class_entropy": round(entropy_all, 6),
        "edge_class_entropy": round(entropy_edge, 6),
        "brightness_range": round(brightness_range, 6),
        "brightness_std": round(brightness_std, 6),
        "edge_brightness_range": round(edge_range, 6),
        "edge_brightness_std": round(edge_std, 6),
        "bottom_right_brightness_range": round(bottom_right_range, 6),
        "bottom_right_brightness_std": round(bottom_right_std, 6),
        "edge_mean_delta": round(_mean(delta_edge), 6),
        "bottom_right_mean_delta": round(_mean(delta_bottom_right), 6),
        "edge_context_score": round(edge_context_score, 6),
        "bottom_right_context_score": round(bottom_right_context_score, 6),
        "wide_fov_context_score": round(wide_fov_context_score, 6),
    }


def _merge_fov_context(sig: Dict[str, Any], fov: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(sig)
    out["fov_context"] = dict(fov)
    for key in (
        "wide_fov_context_score",
        "edge_context_score",
        "bottom_right_context_score",
        "usable_region_count",
        "edge_usable_region_count",
        "bottom_right_usable_region_count",
        "brightness_range",
        "brightness_std",
        "edge_brightness_range",
        "edge_brightness_std",
        "class_diversity_count",
        "edge_class_diversity_count",
    ):
        out[key] = _safe_float(fov.get(key), 0.0)
    out["context_score"] = _context_score(out)
    return out


def _context_score(sig: Mapping[str, Any]) -> float:
    cell_count = max(1.0, _safe_float(sig.get("cell_count"), 36.0))
    low_change = _safe_float(sig.get("low_change_region_count"), 0.0)
    active_cells = max(0.0, cell_count - low_change)
    score = 0.0
    score += 4.0 * _safe_float(sig.get("structured_top_score"), 0.0)
    score += 0.45 * _safe_float(sig.get("structured_candidate_count"), 0.0)
    score += 0.12 * active_cells
    score += 0.08 * _safe_float(sig.get("slow_drift_region_count"), 0.0)
    score += 0.04 * _safe_float(sig.get("fast_change_region_count"), 0.0)
    # P3z0.1: include non-semantic wide-FOV/context evidence. These terms do
    # not claim object identity; they only make additional usable edge context
    # visible to the report, especially when no car/person crosses the frame.
    score += 0.45 * _safe_float(sig.get("wide_fov_context_score"), 0.0)
    score += 0.30 * _safe_float(sig.get("edge_context_score"), 0.0)
    score += 0.20 * _safe_float(sig.get("bottom_right_context_score"), 0.0)
    return round(float(score), 6)


def _run_p3a_at_current_zoom(label: str) -> Dict[str, Any]:
    if p3a is None:
        raise RuntimeError("tools.ptz_structured_motion_probe is unavailable")
    old_samples = os.environ.get("OROMA_PTZ_STRUCT_MOTION_SAMPLES")
    old_interval = os.environ.get("OROMA_PTZ_STRUCT_MOTION_SAMPLE_INTERVAL_SEC")
    os.environ["OROMA_PTZ_STRUCT_MOTION_SAMPLES"] = str(_env_int("OROMA_PTZ_ZOOM_CONTEXT_SAMPLES", 24, lo=2, hi=80))
    os.environ["OROMA_PTZ_STRUCT_MOTION_SAMPLE_INTERVAL_SEC"] = str(_env_float("OROMA_PTZ_ZOOM_CONTEXT_SAMPLE_INTERVAL_SEC", 0.25, lo=0.0, hi=5.0))
    try:
        summary = p3a.build_summary()
        sig = _compact_signature(summary)
        sig["label"] = label
        sig["context_score"] = _context_score(sig)
        return sig
    finally:
        if old_samples is None:
            os.environ.pop("OROMA_PTZ_STRUCT_MOTION_SAMPLES", None)
        else:
            os.environ["OROMA_PTZ_STRUCT_MOTION_SAMPLES"] = old_samples
        if old_interval is None:
            os.environ.pop("OROMA_PTZ_STRUCT_MOTION_SAMPLE_INTERVAL_SEC", None)
        else:
            os.environ["OROMA_PTZ_STRUCT_MOTION_SAMPLE_INTERVAL_SEC"] = old_interval


def build_summary() -> Dict[str, Any]:
    now = int(time.time())
    synthetic = _env_bool("OROMA_PTZ_ZOOM_CONTEXT_SYNTHETIC", False)
    dev = _device()
    wide_zoom = _env_int("OROMA_PTZ_ZOOM_CONTEXT_WIDE_ZOOM", 100, lo=1, hi=10000)
    settle_s = _env_float("OROMA_PTZ_ZOOM_CONTEXT_SETTLE_SEC", 3.0, lo=0.0, hi=30.0)
    restore = _env_bool("OROMA_PTZ_ZOOM_CONTEXT_RESTORE", True)

    before = {"ok": True, "controls": {"zoom_absolute": 130, "pan_absolute": 0, "tilt_absolute": 0}, "synthetic": True} if synthetic else _read_ctrls(dev)
    original_zoom = _safe_int((before.get("controls") or {}).get("zoom_absolute"), wide_zoom)

    current_sig: Dict[str, Any] = {}
    wide_sig: Dict[str, Any] = {}
    set_wide: Dict[str, Any] = {"ok": True, "synthetic": synthetic, "zoom": wide_zoom}
    restore_result: Dict[str, Any] = {"ok": True, "skipped": not restore, "synthetic": synthetic}
    after_wide: Dict[str, Any] = {}
    after_restore: Dict[str, Any] = {}
    error = ""

    try:
        current_sig = _run_p3a_at_current_zoom("current_zoom")
        current_sig = _merge_fov_context(current_sig, _load_p3a_cell_context("current_zoom"))
        if not synthetic:
            set_wide = _set_zoom(dev, wide_zoom)
        time.sleep(settle_s)
        after_wide = {"ok": True, "controls": {"zoom_absolute": wide_zoom}, "synthetic": True} if synthetic else _read_ctrls(dev)
        wide_sig = _run_p3a_at_current_zoom("wide_zoom")
        wide_sig = _merge_fov_context(wide_sig, _load_p3a_cell_context("wide_zoom"))
    except Exception as exc:
        error = str(exc)
    finally:
        if restore and not synthetic:
            restore_result = _set_zoom(dev, original_zoom)
            time.sleep(min(settle_s, 5.0))
            after_restore = _read_ctrls(dev)
        elif restore and synthetic:
            after_restore = {"ok": True, "controls": {"zoom_absolute": original_zoom}, "synthetic": True}

    current_score = _safe_float(current_sig.get("context_score"), 0.0)
    wide_score = _safe_float(wide_sig.get("context_score"), 0.0)
    delta = round(wide_score - current_score, 6)
    structured_delta = round(_safe_float(wide_sig.get("structured_top_score"), 0.0) - _safe_float(current_sig.get("structured_top_score"), 0.0), 6)
    fov_delta = round(_safe_float(wide_sig.get("wide_fov_context_score"), 0.0) - _safe_float(current_sig.get("wide_fov_context_score"), 0.0), 6)
    edge_delta = round(_safe_float(wide_sig.get("edge_context_score"), 0.0) - _safe_float(current_sig.get("edge_context_score"), 0.0), 6)
    usable_delta = round(_safe_float(wide_sig.get("usable_region_count"), 0.0) - _safe_float(current_sig.get("usable_region_count"), 0.0), 6)
    bottom_right_delta = round(_safe_float(wide_sig.get("bottom_right_context_score"), 0.0) - _safe_float(current_sig.get("bottom_right_context_score"), 0.0), 6)
    wide_context_gain_sample = bool(
        fov_delta >= _env_float("OROMA_PTZ_ZOOM_CONTEXT_FOV_GAIN_DELTA", 0.15, lo=0.0, hi=999.0)
        or edge_delta >= _env_float("OROMA_PTZ_ZOOM_CONTEXT_EDGE_GAIN_DELTA", 0.10, lo=0.0, hi=999.0)
        or usable_delta >= _env_float("OROMA_PTZ_ZOOM_CONTEXT_USABLE_GAIN_DELTA", 2.0, lo=0.0, hi=999.0)
        or bottom_right_delta >= _env_float("OROMA_PTZ_ZOOM_CONTEXT_BOTTOM_RIGHT_GAIN_DELTA", 0.10, lo=0.0, hi=999.0)
    )
    wide_helpful_sample = bool(
        delta >= _env_float("OROMA_PTZ_ZOOM_CONTEXT_HELPFUL_DELTA", 0.25, lo=0.0, hi=999.0)
        or structured_delta >= 0.08
        or wide_context_gain_sample
    )

    state = _read_json(_state_path())
    history = state.get("history") if isinstance(state.get("history"), list) else []
    history.append({"ts": now, "wide_helpful_sample": wide_helpful_sample, "delta": delta, "current_zoom": original_zoom, "wide_zoom": wide_zoom})
    history = history[-_env_int("OROMA_PTZ_ZOOM_CONTEXT_HISTORY_MAX", 288, lo=1, hi=10000):]

    summary: Dict[str, Any] = {
        "ok": error == "",
        "ts": now,
        "stage": "P3z0_zoom_context_probe_measure_only",
        "base": BASE,
        "device": dev,
        "state_path": str(_state_path()),
        "wide_zoom": wide_zoom,
        "original_zoom": original_zoom,
        "restore_requested": restore,
        "before_controls": before,
        "set_wide": set_wide,
        "after_wide_controls": after_wide,
        "restore_result": restore_result,
        "after_restore_controls": after_restore,
        "current_signature": current_sig,
        "wide_signature": wide_sig,
        "current_context_score": current_score,
        "wide_context_score": wide_score,
        "context_score_delta": delta,
        "structured_top_score_delta": structured_delta,
        "wide_fov_context_delta": fov_delta,
        "edge_context_delta": edge_delta,
        "usable_region_delta": usable_delta,
        "bottom_right_context_delta": bottom_right_delta,
        "wide_context_gain_sample": wide_context_gain_sample,
        "wide_helpful_sample": wide_helpful_sample,
        "measure_only": True,
        "no_pan_tilt": True,
        "no_policy_activation": True,
        "no_materialization": True,
        "error": error,
    }
    state_out = {
        "version": 1,
        "updated_ts": now,
        "stage": summary["stage"],
        "last": summary,
        "history": history,
        "note": "P3z0/P3z0.1 compares current zoom with a wide zoom using P3a.1 class signatures plus non-semantic edge/FOV context gain; no pan/tilt, no policy, no materialization.",
    }
    _atomic_write_json(_state_path(), state_out)
    return summary


def _dbw_timeout_ms() -> int:
    return _env_int("OROMA_PTZ_ZOOM_CONTEXT_DBW_TIMEOUT_MS", 10000, lo=500, hi=120000)


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
            resp = client.request(op="ping", timeout_ms=1000, expect="none", tag="ptz.zoom_context_probe.ping")
            if bool(resp.get("ok")):
                return
        except Exception:
            pass
    raise RuntimeError("DBWriter required but not available/enabled")


def _metric_rows(summary: Mapping[str, Any]) -> Iterable[Tuple[str, float, Dict[str, Any]]]:
    current = summary.get("current_signature") if isinstance(summary.get("current_signature"), Mapping) else {}
    wide = summary.get("wide_signature") if isinstance(summary.get("wide_signature"), Mapping) else {}
    common = {
        "stage": summary.get("stage"),
        "device": summary.get("device"),
        "original_zoom": summary.get("original_zoom"),
        "wide_zoom": summary.get("wide_zoom"),
        "restore_requested": summary.get("restore_requested"),
        "restore_ok": bool((summary.get("restore_result") or {}).get("ok")) if isinstance(summary.get("restore_result"), Mapping) else False,
        "wide_helpful_sample": bool(summary.get("wide_helpful_sample")),
        "wide_context_gain_sample": bool(summary.get("wide_context_gain_sample")),
        "wide_fov_context_delta": summary.get("wide_fov_context_delta"),
        "edge_context_delta": summary.get("edge_context_delta"),
        "usable_region_delta": summary.get("usable_region_delta"),
        "bottom_right_context_delta": summary.get("bottom_right_context_delta"),
        "current_signature": current,
        "wide_signature": wide,
        "error": summary.get("error"),
    }
    yield "ptz.zoom_context.zoom.current", _safe_float(summary.get("original_zoom"), 0.0), dict(common)
    yield "ptz.zoom_context.zoom.wide", _safe_float(summary.get("wide_zoom"), 0.0), dict(common)
    yield "ptz.zoom_context.score.current", _safe_float(summary.get("current_context_score"), 0.0), dict(common)
    yield "ptz.zoom_context.score.wide", _safe_float(summary.get("wide_context_score"), 0.0), dict(common)
    yield "ptz.zoom_context.score.delta", _safe_float(summary.get("context_score_delta"), 0.0), dict(common)
    yield "ptz.zoom_context.structured.delta", _safe_float(summary.get("structured_top_score_delta"), 0.0), dict(common)
    yield "ptz.zoom_context.wide_fov.delta", _safe_float(summary.get("wide_fov_context_delta"), 0.0), dict(common)
    yield "ptz.zoom_context.edge_context.delta", _safe_float(summary.get("edge_context_delta"), 0.0), dict(common)
    yield "ptz.zoom_context.usable_region.delta", _safe_float(summary.get("usable_region_delta"), 0.0), dict(common)
    yield "ptz.zoom_context.bottom_right.delta", _safe_float(summary.get("bottom_right_context_delta"), 0.0), dict(common)
    yield "ptz.zoom_context.wide_context_gain_sample", 1.0 if bool(summary.get("wide_context_gain_sample")) else 0.0, dict(common)
    yield "ptz.zoom_context.wide_helpful_sample", 1.0 if bool(summary.get("wide_helpful_sample")) else 0.0, dict(common)
    yield "ptz.zoom_context.restore_ok", 1.0 if bool(common.get("restore_ok")) else 0.0, dict(common)


def write_stats(summary: Mapping[str, Any]) -> int:
    _dbw_required()
    ts = _safe_int(summary.get("ts"), int(time.time()))
    written = 0
    for series, value, meta in _metric_rows(summary):
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        src_uid = f"ptz_zoom_context_probe:{ts}:{series}"
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
            params=[int(ts), str(series), float(value), "ptz_zoom_context_probe", 0, meta_json, src_uid],
            tag="ptz.zoom_context_probe.stats_points.upsert",
            priority="low",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
        written += 1
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ORÓMA PTZ Zoom Context Probe – P3z0 measure-only")
    p.add_argument("--once", action="store_true", help="Einmal ausführen")
    p.add_argument("--write-stats", action="store_true", help="P3z0-Stats via DBWriter nach stats.db schreiben")
    p.add_argument("--json", action="store_true", help="JSON ausgeben")
    p.add_argument("--verbose", action="store_true", help="Kompakte Statuszeile ausgeben")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = build_summary()
    if args.write_stats:
        summary["stats_written"] = write_stats(summary)
    if args.json or not args.verbose:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.verbose:
        print(
            "[ptz_zoom_context_probe] "
            f"ok={summary.get('ok')} original_zoom={summary.get('original_zoom')} wide_zoom={summary.get('wide_zoom')} "
            f"score_current={summary.get('current_context_score')} score_wide={summary.get('wide_context_score')} "
            f"delta={summary.get('context_score_delta')} fov_delta={summary.get('wide_fov_context_delta')} "
            f"edge_delta={summary.get('edge_context_delta')} wide_gain={summary.get('wide_context_gain_sample')} "
            f"wide_helpful_sample={summary.get('wide_helpful_sample')} "
            f"restore_ok={(summary.get('restore_result') or {}).get('ok') if isinstance(summary.get('restore_result'), Mapping) else None} "
            f"stats_written={summary.get('stats_written', 0)} error={summary.get('error') or ''}"
        )
    return 0 if bool(summary.get("ok")) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

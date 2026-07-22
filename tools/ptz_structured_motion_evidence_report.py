#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_structured_motion_evidence_report.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Structured Motion Evidence Report – Read-only Trendanalyse
# Version:   v3.7.3+p3a1-structured-motion-report-v1.1
# Stand:     2026-06-16
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Read-only-Auswertung der P3a-Zeitreihen `ptz.motion.*` in
# `data/stats.db.stats_points`. Das Tool ergänzt
# `tools/ptz_structured_motion_probe.py` und beantwortet, ob regionale
# Zeitsignaturen stabil getrennt werden:
#
#   - structured_blob_motion: kleine wandernde Blobs (Straße/Menschen/Autos)
#   - fixed_fast_change_region: schnelle feste Flächenänderung (TV/Stream-artig)
#   - fixed_low_change_display_region: Alexa/Uhr/Hintergrund-artig
#   - dark_static_region: dunkle statische Fläche
#   - slow_drift_region: langsame Helligkeitsdrift / Baseline
#
# INVARIANTEN
# ───────────
# - Read-only: keine DB-Writes, kein DBWriter, keine State-Writes.
# - Keine PTZ-Motorbefehle.
# - Keine Policy-Aktivierung.
# - Keine Materialisierung.
# - Nutzt `stats_points.meta`, um historische Top-Keys nachvollziehbar zu halten.
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"


def _stats_db_path() -> Path:
    return Path(os.environ.get("OROMA_STATS_DB", os.path.join(BASE, "data", "stats.db")))


def _state_path() -> Path:
    return Path(os.environ.get("OROMA_PTZ_STRUCT_MOTION_STATE_PATH", os.path.join(BASE, "data", "state", "ptz_structured_motion_state.json")))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def _parse_meta(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_points(since_ts: int) -> List[Dict[str, Any]]:
    path = _stats_db_path()
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT ts, series, value, meta
            FROM stats_points
            WHERE series LIKE 'ptz.motion.%' AND ts >= ?
            ORDER BY ts ASC, series ASC
            """,
            (int(since_ts),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _series_stats(values: Sequence[float]) -> Dict[str, Any]:
    if not values:
        return {"samples": 0, "first": 0.0, "last": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0, "trend": 0.0}
    vals = [float(v) for v in values]
    return {
        "samples": len(vals),
        "first": round(vals[0], 6),
        "last": round(vals[-1], 6),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "avg": round(sum(vals) / len(vals), 6),
        "trend": round(vals[-1] - vals[0], 6),
    }


def _window_summary(points: Sequence[Mapping[str, Any]], window_min: int, now: int) -> Dict[str, Any]:
    since = int(now - int(window_min) * 60)
    rows = [dict(p) for p in points if int(p.get("ts") or 0) >= since]
    by_series: Dict[str, List[float]] = {}
    sample_ts = sorted({int(r.get("ts") or 0) for r in rows})
    top_key_counts: Dict[str, int] = {}
    class_last: Dict[str, str] = {}
    class_switch_max = 0
    latest_meta: Dict[str, Any] = {}
    for r in rows:
        series = str(r.get("series") or "")
        by_series.setdefault(series, []).append(_safe_float(r.get("value")))
        meta = _parse_meta(r.get("meta"))
        if meta:
            latest_meta[series] = meta
            cls = str(meta.get("class") or "")
            if cls:
                class_last[series] = cls
            key = str(meta.get("top_key") or "")
            if key:
                top_key_counts[key] = top_key_counts.get(key, 0) + 1
            try:
                class_switch_max = max(class_switch_max, int(meta.get("class_switch_count") or 0))
            except Exception:
                pass

    series_stats = {series: _series_stats(vals) for series, vals in sorted(by_series.items())}
    def last(series: str) -> float:
        return _safe_float(series_stats.get(series, {}).get("last"))
    top_key = ""
    top_key_stability = 0.0
    if top_key_counts:
        top_key, top_n = sorted(top_key_counts.items(), key=lambda kv: kv[1], reverse=True)[0]
        top_key_stability = round(float(top_n) / max(1.0, float(sum(top_key_counts.values()))), 6)

    return {
        "window_min": int(window_min),
        "since_ts": since,
        "first_ts": min(sample_ts) if sample_ts else 0,
        "last_ts": max(sample_ts) if sample_ts else 0,
        "sample_count": len(sample_ts),
        "point_count": len(rows),
        "series": series_stats,
        "summary": {
            "structured_candidate_last": last("ptz.motion.structured.candidate_count"),
            "structured_top_score_last": last("ptz.motion.structured.top_score"),
            "fast_change_region_last": last("ptz.motion.fast_change.region_count"),
            "fast_change_top_score_last": last("ptz.motion.fast_change.top_score"),
            "low_change_region_last": last("ptz.motion.low_change.region_count"),
            "low_change_top_score_last": last("ptz.motion.low_change.top_score"),
            "low_change_display_candidate_last": last("ptz.motion.low_change_display.candidate_count"),
            "low_change_display_region_last": last("ptz.motion.low_change_display.region_count"),
            "dark_static_region_last": last("ptz.motion.dark_static.region_count"),
            "slow_drift_region_last": last("ptz.motion.slow_drift.region_count"),
            "cut_like_last": last("ptz.motion.fast_change.cut_like_count"),
            "frame_count_last": last("ptz.motion.samples.frame_count"),
            "no_frame_last": last("ptz.motion.samples.no_frame"),
        },
        "top_key": top_key,
        "top_key_stability": top_key_stability,
        "class_switch_max": class_switch_max,
        "latest_meta": latest_meta,
    }


def build_report(windows_min: Sequence[int]) -> Dict[str, Any]:
    now = int(time.time())
    max_window = max([int(w) for w in windows_min] or [1440])
    points = _load_points(now - max_window * 60)
    return {
        "ok": True,
        "ts": now,
        "stats_db": str(_stats_db_path()),
        "state_path": str(_state_path()),
        "current_state": _read_json(_state_path()),
        "point_count_loaded": len(points),
        "windows_min": [int(w) for w in windows_min],
        "windows": [_window_summary(points, int(w), now) for w in windows_min],
    }


def print_text(report: Mapping[str, Any]) -> None:
    print("ORÓMA PTZ Structured Motion Evidence Report")
    print(f"ok: {report.get('ok')} ts: {report.get('ts')} stats_db: {report.get('stats_db')}")
    state = report.get("current_state") if isinstance(report.get("current_state"), Mapping) else {}
    print(
        "current: "
        f"grid={state.get('grid')} samples={state.get('sample_count_last')} "
        f"top_structured={(state.get('top_structured') or {}).get('key') if isinstance(state.get('top_structured'), Mapping) else ''} "
        f"top_fast={(state.get('top_fast_change') or {}).get('key') if isinstance(state.get('top_fast_change'), Mapping) else ''} "
        f"top_low={(state.get('top_low_change_region') or {}).get('key') if isinstance(state.get('top_low_change_region'), Mapping) else ''} "
        f"top_display_candidate={(state.get('top_low_change_display') or {}).get('key') if isinstance(state.get('top_low_change_display'), Mapping) else ''}"
    )
    for w in report.get("windows") or []:
        if not isinstance(w, Mapping):
            continue
        s = w.get("summary") if isinstance(w.get("summary"), Mapping) else {}
        print(
            f"\nwindow={w.get('window_min')}min samples={w.get('sample_count')} points={w.get('point_count')} "
            f"top_key={w.get('top_key') or '-'} stability={w.get('top_key_stability')} class_switch_max={w.get('class_switch_max')}"
        )
        print(
            "  "
            f"structured={s.get('structured_candidate_last')} structured_score={s.get('structured_top_score_last')} "
            f"fast_regions={s.get('fast_change_region_last')} fast_score={s.get('fast_change_top_score_last')} "
            f"low_change={s.get('low_change_region_last')} low_display_candidate={s.get('low_change_display_candidate_last')} "
            f"dark={s.get('dark_static_region_last')} "
            f"slow_drift={s.get('slow_drift_region_last')} cut_like={s.get('cut_like_last')} "
            f"frames={s.get('frame_count_last')} no_frame={s.get('no_frame_last')}"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ORÓMA PTZ Structured Motion Evidence Report – read-only")
    p.add_argument("--json", action="store_true", help="JSON ausgeben")
    p.add_argument("--text", action="store_true", help="Textbericht ausgeben")
    p.add_argument("--verbose", action="store_true", help="Kompakte Statuszeile ausgeben")
    p.add_argument("--window-min", action="append", type=int, default=[], help="Analysefenster in Minuten; mehrfach nutzbar")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    windows = args.window_min or [60, 360, 1440]
    report = build_report(windows)
    if args.json or not args.text:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.text:
        print_text(report)
    if args.verbose:
        first = report.get("windows", [{}])[0] if report.get("windows") else {}
        s = first.get("summary") if isinstance(first, Mapping) and isinstance(first.get("summary"), Mapping) else {}
        print(
            "[ptz_structured_motion_evidence_report] "
            f"ok={report.get('ok')} points={report.get('point_count_loaded')} "
            f"window={first.get('window_min') if isinstance(first, Mapping) else '-'}min "
            f"structured={s.get('structured_candidate_last')} fast={s.get('fast_change_region_last')} "
            f"low_change={s.get('low_change_region_last')} display_candidate={s.get('low_change_display_candidate_last')} "
            f"dark={s.get('dark_static_region_last')}"
        )
    return 0 if bool(report.get("ok")) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

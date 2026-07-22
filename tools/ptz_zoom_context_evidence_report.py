#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_zoom_context_evidence_report.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Zoom Context Evidence Report – P3z0 Read-only Auswertung
# Version:   v3.7.3+p3z1a-report-fix-best-decision-v1.0
# Stand:     2026-06-21
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Read-only Report für `ptz.zoom_context.*` Zeitreihen aus P3z0. Der Report
# entscheidet nicht hart auf Basis eines einzelnen Schnappschusses, sondern
# aggregiert über 1h/6h/24h, ob Wide-Zoom wiederholt mehr Kontext liefert. P3z0.1 berücksichtigt zusätzlich Wide-FOV-/Edge-/Bottom-Right-Kontextgewinn, damit ein ruhiges Nachtfenster nicht fälschlich als negatives Ergebnis gewertet wird.
#
# INVARIANTEN
# ───────────
# - Keine Kamera-/PTZ-Steuerung.
# - Keine DB-Writes.
# - SQLite-Verbindung wird immer geschlossen.
# - Headless-only.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def _stats_db_path() -> Path:
    return Path(os.environ.get("OROMA_STATS_DB", os.path.join(BASE, "data", "stats.db")))


def _state_path() -> Path:
    return Path(os.environ.get("OROMA_PTZ_ZOOM_CONTEXT_STATE_PATH", os.path.join(BASE, "data", "state", "ptz_zoom_context_probe_state.json")))


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
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
    try:
        data = json.loads(str(raw or "{}"))
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
            WHERE series LIKE 'ptz.zoom_context.%' AND ts >= ?
            ORDER BY ts ASC, series ASC
            """,
            (int(since_ts),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _series(values: Sequence[float]) -> Dict[str, Any]:
    if not values:
        return {"samples": 0, "first": 0.0, "last": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0, "trend": 0.0}
    vals = [float(v) for v in values]
    return {"samples": len(vals), "first": round(vals[0], 6), "last": round(vals[-1], 6), "min": round(min(vals), 6), "max": round(max(vals), 6), "avg": round(sum(vals)/len(vals), 6), "trend": round(vals[-1]-vals[0], 6)}


def _window(points: Sequence[Mapping[str, Any]], window_min: int, now: int) -> Dict[str, Any]:
    since = now - int(window_min) * 60
    rows = [dict(p) for p in points if int(p.get("ts") or 0) >= since]
    by_series: Dict[str, List[float]] = {}
    sample_ts = sorted({int(r.get("ts") or 0) for r in rows})
    latest_meta: Dict[str, Any] = {}
    for r in rows:
        series = str(r.get("series") or "")
        by_series.setdefault(series, []).append(_safe_float(r.get("value")))
        meta = _parse_meta(r.get("meta"))
        if meta:
            latest_meta[series] = meta
    stats = {k: _series(v) for k, v in sorted(by_series.items())}
    def last(name: str) -> float:
        return _safe_float(stats.get(name, {}).get("last"))
    def avg(name: str) -> float:
        return _safe_float(stats.get(name, {}).get("avg"))
    helpful_avg = avg("ptz.zoom_context.wide_helpful_sample")
    context_gain_avg = avg("ptz.zoom_context.wide_context_gain_sample")
    delta_avg = avg("ptz.zoom_context.score.delta")
    fov_delta_avg = avg("ptz.zoom_context.wide_fov.delta")
    edge_delta_avg = avg("ptz.zoom_context.edge_context.delta")
    usable_delta_avg = avg("ptz.zoom_context.usable_region.delta")
    bottom_right_delta_avg = avg("ptz.zoom_context.bottom_right.delta")
    decision = "insufficient"
    if len(sample_ts) >= 3:
        if helpful_avg >= 0.50 or context_gain_avg >= 0.50 or delta_avg >= 0.25 or fov_delta_avg >= 0.15 or edge_delta_avg >= 0.10:
            decision = "wide_zoom_likely_helpful"
        elif helpful_avg <= 0.10 and context_gain_avg <= 0.10 and delta_avg <= 0.0 and fov_delta_avg <= 0.0 and edge_delta_avg <= 0.0 and usable_delta_avg <= 0.0:
            decision = "wide_zoom_not_yet_helpful"
        else:
            decision = "mixed_observe_more"
    return {
        "window_min": int(window_min),
        "since_ts": since,
        "sample_count": len(sample_ts),
        "point_count": len(rows),
        "series": stats,
        "summary": {
            "current_zoom_last": last("ptz.zoom_context.zoom.current"),
            "wide_zoom_last": last("ptz.zoom_context.zoom.wide"),
            "score_current_last": last("ptz.zoom_context.score.current"),
            "score_wide_last": last("ptz.zoom_context.score.wide"),
            "score_delta_last": last("ptz.zoom_context.score.delta"),
            "score_delta_avg": round(delta_avg, 6),
            "structured_delta_last": last("ptz.zoom_context.structured.delta"),
            "wide_fov_delta_last": last("ptz.zoom_context.wide_fov.delta"),
            "wide_fov_delta_avg": round(fov_delta_avg, 6),
            "edge_context_delta_last": last("ptz.zoom_context.edge_context.delta"),
            "edge_context_delta_avg": round(edge_delta_avg, 6),
            "usable_region_delta_last": last("ptz.zoom_context.usable_region.delta"),
            "usable_region_delta_avg": round(usable_delta_avg, 6),
            "bottom_right_delta_last": last("ptz.zoom_context.bottom_right.delta"),
            "bottom_right_delta_avg": round(bottom_right_delta_avg, 6),
            "wide_context_gain_rate": round(context_gain_avg, 6),
            "wide_helpful_rate": round(helpful_avg, 6),
            "restore_ok_last": last("ptz.zoom_context.restore_ok"),
            "decision": decision,
        },
        "latest_meta": latest_meta,
    }


def build_report(windows_min: Sequence[int]) -> Dict[str, Any]:
    now = int(time.time())
    max_w = max([int(w) for w in windows_min] or [1440])
    points = _load_points(now - max_w * 60)
    return {
        "ok": True,
        "ts": now,
        "stats_db": str(_stats_db_path()),
        "state_path": str(_state_path()),
        "current_state": _read_json(_state_path()),
        "point_count_loaded": len(points),
        "windows_min": [int(w) for w in windows_min],
        "windows": [_window(points, int(w), now) for w in windows_min],
    }



_DECISION_PRIORITY = {
    "wide_zoom_likely_helpful": 3,
    "mixed_observe_more": 2,
    "insufficient": 1,
    "wide_zoom_not_yet_helpful": 0,
}


def _best_window(report: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the window with the strongest available evidence.

    The text report prints all windows, but the final verbose status line must
    not blindly use the first window.  The first window is the shortest one
    (usually 60 minutes) and is often still `insufficient` while the longer
    6h/24h windows already carry the stronger evidence.

    Ordering rules:
    1. highest explicit decision priority wins,
    2. if decisions tie, the window with more samples wins.

    This keeps P3z0.1 read-only and measure-only.  It only fixes how the
    already computed evidence is summarized.
    """
    windows = [w for w in (report.get("windows") or []) if isinstance(w, Mapping)]
    if not windows:
        return {}

    def _rank(w: Mapping[str, Any]) -> tuple:
        summary = w.get("summary") if isinstance(w.get("summary"), Mapping) else {}
        decision = str(summary.get("decision", "insufficient"))
        return (_DECISION_PRIORITY.get(decision, 1), int(w.get("sample_count") or 0))

    return max(windows, key=_rank)


def print_text(report: Mapping[str, Any]) -> None:
    print("ORÓMA PTZ Zoom Context Evidence Report")
    print(f"ok: {report.get('ok')} ts: {report.get('ts')} stats_db: {report.get('stats_db')}")
    state = report.get("current_state") if isinstance(report.get("current_state"), Mapping) else {}
    last = state.get("last") if isinstance(state.get("last"), Mapping) else {}
    print(
        "current: "
        f"original_zoom={last.get('original_zoom')} wide_zoom={last.get('wide_zoom')} "
        f"delta={last.get('context_score_delta')} wide_helpful_sample={last.get('wide_helpful_sample')} "
        f"restore_ok={(last.get('restore_result') or {}).get('ok') if isinstance(last.get('restore_result'), Mapping) else None}"
    )
    for w in report.get("windows") or []:
        if not isinstance(w, Mapping):
            continue
        s = w.get("summary") if isinstance(w.get("summary"), Mapping) else {}
        print(
            f"\nwindow={w.get('window_min')}min samples={w.get('sample_count')} points={w.get('point_count')} "
            f"decision={s.get('decision')}"
        )
        print(
            "  "
            f"zoom_current={s.get('current_zoom_last')} zoom_wide={s.get('wide_zoom_last')} "
            f"score_current={s.get('score_current_last')} score_wide={s.get('score_wide_last')} "
            f"delta_last={s.get('score_delta_last')} delta_avg={s.get('score_delta_avg')} "
            f"wide_helpful_rate={s.get('wide_helpful_rate')} restore_ok={s.get('restore_ok_last')}"
        )
        print(
            "  "
            f"fov_delta_last={s.get('wide_fov_delta_last')} fov_delta_avg={s.get('wide_fov_delta_avg')} "
            f"edge_delta_last={s.get('edge_context_delta_last')} edge_delta_avg={s.get('edge_context_delta_avg')} "
            f"usable_delta_last={s.get('usable_region_delta_last')} usable_delta_avg={s.get('usable_region_delta_avg')} "
            f"bottom_right_delta_last={s.get('bottom_right_delta_last')} bottom_right_delta_avg={s.get('bottom_right_delta_avg')} "
            f"wide_context_gain_rate={s.get('wide_context_gain_rate')}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA PTZ Zoom Context Evidence Report – read-only")
    ap.add_argument("--json", action="store_true", help="JSON ausgeben")
    ap.add_argument("--text", action="store_true", help="Textbericht ausgeben")
    ap.add_argument("--verbose", action="store_true", help="Kompakte Statuszeile ausgeben")
    ap.add_argument("--window-min", action="append", type=int, default=[], help="Analysefenster in Minuten; mehrfach nutzbar")
    args = ap.parse_args(argv)
    report = build_report(args.window_min or [60, 360, 1440])
    if args.json or not args.text:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.text:
        print_text(report)
    if args.verbose:
        best = _best_window(report)
        s = best.get("summary") if isinstance(best, Mapping) and isinstance(best.get("summary"), Mapping) else {}
        print(f"[ptz_zoom_context_evidence_report] ok={report.get('ok')} points={report.get('point_count_loaded')} decision={s.get('decision')} best_window={best.get('window_min')}min samples={best.get('sample_count')} delta_avg={s.get('score_delta_avg')} fov_delta_avg={s.get('wide_fov_delta_avg')} edge_delta_avg={s.get('edge_context_delta_avg')} context_gain_rate={s.get('wide_context_gain_rate')} helpful_rate={s.get('wide_helpful_rate')}")
    return 0 if bool(report.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

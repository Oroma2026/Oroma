#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_positive_position_evidence_report.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Positive Position Evidence Report – Stage-A Trendanalyse
# Version:   v3.7.3+ptz-positive-position-evidence-report-v1.0
# Stand:     2026-06-13
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool wertet die Stage-A-Evidence des PTZ Positive Position Marker
# aus. Es liest ausschließlich vorhandene Messpunkte aus:
#
#   data/stats.db -> stats_points (series LIKE 'ptz.marker.%')
#
# und optional den aktuellen Runtime-Zustand aus:
#
#   data/state/ptz_positive_position_markers.json
#   data/state/ptz_motor_state.json
#
# Es schreibt NICHTS. Es bewegt KEINEN Motor. Es aktiviert KEINE Policy und
# materialisiert KEINE object_nodes/object_relations. Damit bleibt es strikt
# Stage-A / measure-only im Sinne der Core-Dokumentation:
#
#   Evidence sammeln → Verlauf prüfen → später Dream/Binding entscheiden lassen
#
# WARUM DIESES TOOL EXISTIERT
# ──────────────────────────
# Der Timer `oroma-ptz-positive-position-probe.timer` schreibt alle 5 Minuten
# metrische Momentaufnahmen nach `stats_points`. Ein einzelner Messpunkt beweist
# aber noch kein Lernen. Für ORÓMA ist entscheidend, ob über Zeit erkennbar wird:
#
# - entstehen stabile positive Marker?
# - wächst repeat_ge_5?
# - bleibt motion_guard_blocked hoch, wenn Upper-Motion/Rauschen dominiert?
# - wird ceiling_active seltener oder bleibt es auffällig?
# - bleibt top_key stabil oder springt der Fokus unruhig?
#
# Dieses Report-Tool beantwortet genau diese Fragen für 1h/6h/24h-Fenster oder
# frei wählbare Fenster. Es ist bewusst konservativ: Trends sind Diagnose, nicht
# Steuerbefehl.
#
# ARCHITEKTUR-INVARIANTEN
# ───────────────────────
# - Read-only: keine Writes nach stats.db, oroma.db oder State-Dateien.
# - Keine DBWriter-Nutzung; DBWriter wird nur für Schreibpfade benötigt.
# - SQLite-Verbindungen werden immer mit `with`/Kontextmanager geschlossen.
# - Keine PTZ-Motorsteuerung.
# - Keine Policy-Bias-Aktivierung.
# - Keine Materialisierung in ObjectGraph/MetaSnaps/Relations.
# - JSON-Ausgabe ist maschinenlesbar für UI/Docs/weiteres Audit.
#
# ENVIRONMENT
# ───────────
#   OROMA_BASE                         Default: /opt/ai/oroma
#   OROMA_STATS_DB                     Default: $OROMA_BASE/data/stats.db
#   OROMA_PTZ_MOTOR_STATE_PATH         Default: $OROMA_BASE/data/state/ptz_motor_state.json
#   OROMA_PTZ_POSITIVE_MARKER_PATH     Default: $OROMA_BASE/data/state/ptz_positive_position_markers.json
#
# BEISPIELE
# ─────────
# JSON-Report mit Standardfenstern 1h, 6h, 24h:
#
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     python3 tools/ptz_positive_position_evidence_report.py --json --verbose
#
# Text-Report mit zusätzlichem 30-Minuten-Fenster:
#
#   cd /opt/ai/oroma
#   python3 tools/ptz_positive_position_evidence_report.py --window-min 30 --text
#
# iPhone-tauglicher Kurzlauf:
#
#   cd /opt/ai/oroma; python3 tools/ptz_positive_position_evidence_report.py --json
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"

SERIES_PREFIX = "ptz.marker."
DEFAULT_WINDOWS_MIN = (60, 360, 1440)

CORE_SERIES = (
    "ptz.marker.marker_count",
    "ptz.marker.positive_count",
    "ptz.marker.repeat_ge_3",
    "ptz.marker.repeat_ge_5",
    "ptz.marker.max_count",
    "ptz.marker.max_score_ema",
    "ptz.marker.eye_pair_positive",
    "ptz.marker.face_positive",
    "ptz.marker.motion_positive",
    "ptz.marker.motion_guard_blocked",
    "ptz.marker.ceiling_active",
    "ptz.marker.ceiling_marker_stale",
)


def _env_path(name: str, default: str) -> Path:
    raw = os.environ.get(name)
    return Path(str(raw).strip() if raw else default)


def _stats_db_path() -> Path:
    return _env_path("OROMA_STATS_DB", os.path.join(BASE, "data", "stats.db"))


def _marker_path() -> Path:
    return _env_path("OROMA_PTZ_POSITIVE_MARKER_PATH", os.path.join(BASE, "data", "state", "ptz_positive_position_markers.json"))


def _state_path() -> Path:
    return _env_path("OROMA_PTZ_MOTOR_STATE_PATH", os.path.join(BASE, "data", "state", "ptz_motor_state.json"))


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if out != out or out in (float("inf"), float("-inf")):
        return float(default)
    return float(out)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _json_meta(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _last(values: Sequence[Tuple[int, float]]) -> float:
    return float(values[-1][1]) if values else 0.0


def _first(values: Sequence[Tuple[int, float]]) -> float:
    return float(values[0][1]) if values else 0.0


def _trend(values: Sequence[Tuple[int, float]]) -> float:
    if len(values) < 2:
        return 0.0
    return float(_last(values) - _first(values))


def _rate(values: Sequence[Tuple[int, float]]) -> float:
    if not values:
        return 0.0
    return float(sum(1 for _, v in values if _safe_float(v) > 0.0) / len(values))


def _open_stats_readonly(path: Path) -> sqlite3.Connection:
    # URI read-only vermeidet versehentliche Datei-/Journal-Erstellung. Falls die
    # Plattform URI nicht zulässt oder die Datei fehlt, fällt der Fehler sichtbar
    # an den Aufrufer zurück.
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=2.0)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> List[str]:
    return [str(r["name"]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def _fetch_points(con: sqlite3.Connection, since_ts: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in con.execute(
        """
        SELECT ts, series, value, meta
        FROM stats_points
        WHERE series LIKE ? AND ts >= ?
        ORDER BY ts ASC, rowid ASC
        """,
        (SERIES_PREFIX + "%", int(since_ts)),
    ):
        rows.append({"ts": int(r["ts"]), "series": str(r["series"]), "value": _safe_float(r["value"]), "meta": r["meta"]})
    return rows


def _fetch_latest(con: sqlite3.Connection, limit: int = 48) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in con.execute(
        """
        SELECT ts, series, value, meta
        FROM stats_points
        WHERE series LIKE ?
        ORDER BY rowid DESC
        LIMIT ?
        """,
        (SERIES_PREFIX + "%", int(limit)),
    ):
        rows.append({"ts": int(r["ts"]), "series": str(r["series"]), "value": _safe_float(r["value"]), "meta": r["meta"]})
    rows.reverse()
    return rows


def _group_by_series(points: Iterable[Mapping[str, Any]]) -> Dict[str, List[Tuple[int, float]]]:
    grouped: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    for p in points:
        grouped[str(p.get("series") or "")].append((_safe_int(p.get("ts")), _safe_float(p.get("value"))))
    return dict(grouped)


def _top_keys(points: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    # Ein Probe-Lauf schreibt mehrere Serien mit identischem ts/meta. Für die
    # Top-Key-Stabilität darf ein Lauf daher nur einmal zählen, sonst wäre die
    # Stabilität künstlich um den Faktor der Serienanzahl erhöht.
    sample_ts_by_key: Dict[str, set[int]] = defaultdict(set)
    max_score: Dict[str, float] = defaultdict(float)
    last_ts: Dict[str, int] = defaultdict(int)
    for p in points:
        meta = _json_meta(p.get("meta"))
        key = str(meta.get("top_key") or "")
        if not key:
            continue
        ts = _safe_int(p.get("ts"))
        sample_ts_by_key[key].add(ts)
        max_score[key] = max(float(max_score.get(key, 0.0)), _safe_float(meta.get("top_score_ema")))
        last_ts[key] = max(int(last_ts.get(key, 0)), ts)
    out = [
        {"key": key, "samples": int(len(ts_set)), "max_score_ema": round(float(max_score.get(key, 0.0)), 6), "last_ts": int(last_ts.get(key, 0))}
        for key, ts_set in sample_ts_by_key.items()
    ]
    out.sort(key=lambda item: (int(item["samples"]), float(item["max_score_ema"]), int(item["last_ts"])), reverse=True)
    return out


def _window_report(points: List[Dict[str, Any]], now: int, window_min: int) -> Dict[str, Any]:
    since = int(now - int(window_min) * 60)
    pts = [p for p in points if _safe_int(p.get("ts")) >= since]
    grouped = _group_by_series(pts)
    series_reports: Dict[str, Dict[str, Any]] = {}
    for series in CORE_SERIES:
        values = grouped.get(series, [])
        numeric = [float(v) for _, v in values]
        series_reports[series] = {
            "samples": len(values),
            "first": round(_first(values), 6),
            "last": round(_last(values), 6),
            "min": round(min(numeric), 6) if numeric else 0.0,
            "max": round(max(numeric), 6) if numeric else 0.0,
            "avg": round(_mean(numeric), 6),
            "trend": round(_trend(values), 6),
            "positive_rate": round(_rate(values), 6),
        }

    sample_ts = sorted({int(p["ts"]) for p in pts})
    sample_count = len(sample_ts)
    top_keys = _top_keys(pts)
    latest_top_key = top_keys[0]["key"] if top_keys else ""
    latest_top_stability = round(float(top_keys[0]["samples"]) / sample_count, 6) if top_keys and sample_count else 0.0

    return {
        "window_min": int(window_min),
        "since_ts": since,
        "point_count": len(pts),
        "sample_count": sample_count,
        "first_ts": sample_ts[0] if sample_ts else 0,
        "last_ts": sample_ts[-1] if sample_ts else 0,
        "top_key": latest_top_key,
        "top_key_stability": latest_top_stability,
        "top_keys": top_keys[:10],
        "series": series_reports,
        "summary": {
            "marker_count_last": series_reports["ptz.marker.marker_count"]["last"],
            "positive_count_last": series_reports["ptz.marker.positive_count"]["last"],
            "repeat_ge_3_last": series_reports["ptz.marker.repeat_ge_3"]["last"],
            "repeat_ge_5_last": series_reports["ptz.marker.repeat_ge_5"]["last"],
            "motion_guard_rate": series_reports["ptz.marker.motion_guard_blocked"]["positive_rate"],
            "ceiling_active_rate": series_reports["ptz.marker.ceiling_active"]["positive_rate"],
            "ceiling_marker_stale_rate": series_reports["ptz.marker.ceiling_marker_stale"]["positive_rate"],
            "eye_pair_positive_last": series_reports["ptz.marker.eye_pair_positive"]["last"],
            "face_positive_last": series_reports["ptz.marker.face_positive"]["last"],
            "motion_positive_last": series_reports["ptz.marker.motion_positive"]["last"],
        },
    }


def _current_marker_state() -> Dict[str, Any]:
    marker_file = _read_json(_marker_path())
    state = _read_json(_state_path())
    markers_raw = marker_file.get("markers") if isinstance(marker_file.get("markers"), Mapping) else {}
    markers: Dict[str, Dict[str, Any]] = {str(k): dict(v) for k, v in markers_raw.items() if isinstance(v, Mapping)}
    positives = [(k, v) for k, v in markers.items() if bool(v.get("is_positive"))]
    positives.sort(key=lambda kv: (_safe_int(kv[1].get("count")), _safe_float(kv[1].get("score_ema"))), reverse=True)
    marker_meta = state.get("positive_position_marker_meta") if isinstance(state.get("positive_position_marker_meta"), Mapping) else {}
    ceiling = state.get("ceiling_recovery") if isinstance(state.get("ceiling_recovery"), Mapping) else {}
    return {
        "marker_path": str(_marker_path()),
        "state_path": str(_state_path()),
        "marker_version": marker_file.get("version"),
        "legacy_reset_reason": marker_file.get("legacy_reset_reason"),
        "marker_count": len(markers),
        "positive_count": len(positives),
        "positive_top": [v for _, v in positives[:5]],
        "last_update_reason": str(((marker_meta.get("last_update") if isinstance(marker_meta.get("last_update"), Mapping) else {}) or {}).get("reason") or ""),
        "ceiling": {
            "active": bool(ceiling.get("active")),
            "reason": str(ceiling.get("reason") or ""),
            "marker_stale": bool(ceiling.get("marker_stale")),
            "target_weak": bool(ceiling.get("target_weak")),
            "start_grace_ok": bool(ceiling.get("start_grace_ok")),
            "last_recovery_ts": _safe_int(ceiling.get("last_recovery_ts")),
        },
    }


def build_report(windows_min: Sequence[int], latest_limit: int = 48) -> Dict[str, Any]:
    now = int(time.time())
    stats_path = _stats_db_path()
    report: Dict[str, Any] = {
        "ok": False,
        "ts": now,
        "stats_db": str(stats_path),
        "windows_min": [int(w) for w in windows_min],
        "current_marker_state": _current_marker_state(),
    }
    if not stats_path.exists():
        report.update({"error": "stats.db not found", "ok": False})
        return report

    max_window = max([int(w) for w in windows_min] or [60])
    since = int(now - max_window * 60)
    try:
        with _open_stats_readonly(stats_path) as con:
            tables = _tables(con)
            report["tables"] = tables
            if "stats_points" not in tables:
                report.update({"error": "stats_points table not found", "ok": False})
                return report
            points = _fetch_points(con, since)
            latest = _fetch_latest(con, latest_limit)
    except Exception as exc:
        report.update({"error": str(exc), "ok": False})
        return report

    report["ok"] = True
    report["point_count_loaded"] = len(points)
    report["latest"] = latest
    report["windows"] = [_window_report(points, now, int(w)) for w in windows_min]
    return report


def _print_text(report: Mapping[str, Any]) -> None:
    print("ORÓMA PTZ Positive Position Evidence Report")
    print(f"ok: {report.get('ok')} ts: {report.get('ts')} stats_db: {report.get('stats_db')}")
    if not report.get("ok"):
        print(f"error: {report.get('error')}")
        return
    state = report.get("current_marker_state") if isinstance(report.get("current_marker_state"), Mapping) else {}
    print(
        "current: "
        f"marker_count={state.get('marker_count')} positive_count={state.get('positive_count')} "
        f"legacy_reset={state.get('legacy_reset_reason')} last_update={state.get('last_update_reason')}"
    )
    ceiling = state.get("ceiling") if isinstance(state.get("ceiling"), Mapping) else {}
    print(
        "ceiling: "
        f"active={ceiling.get('active')} stale={ceiling.get('marker_stale')} "
        f"target_weak={ceiling.get('target_weak')} reason={ceiling.get('reason')}"
    )
    for w in report.get("windows", []):
        if not isinstance(w, Mapping):
            continue
        summary = w.get("summary") if isinstance(w.get("summary"), Mapping) else {}
        print(f"\nwindow={w.get('window_min')}min samples={w.get('sample_count')} points={w.get('point_count')} top_key={w.get('top_key')} stability={w.get('top_key_stability')}")
        print(
            "  "
            f"positive_last={summary.get('positive_count_last')} repeat3={summary.get('repeat_ge_3_last')} "
            f"repeat5={summary.get('repeat_ge_5_last')} eye={summary.get('eye_pair_positive_last')} "
            f"motion_guard_rate={summary.get('motion_guard_rate')} ceiling_active_rate={summary.get('ceiling_active_rate')} "
            f"stale_rate={summary.get('ceiling_marker_stale_rate')}"
        )
        top_keys = w.get("top_keys") if isinstance(w.get("top_keys"), list) else []
        for item in top_keys[:3]:
            print(f"  top: {item}")


def _parse_windows(values: Optional[Sequence[str]]) -> List[int]:
    out = list(DEFAULT_WINDOWS_MIN)
    if values:
        for raw in values:
            try:
                value = int(str(raw).strip())
            except Exception:
                continue
            if value > 0 and value not in out:
                out.append(value)
    out.sort()
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ORÓMA PTZ Positive Position Evidence Report – read-only Stage-A trend analysis")
    parser.add_argument("--json", action="store_true", help="JSON-Report ausgeben (maschinenlesbar).")
    parser.add_argument("--text", action="store_true", help="Text-Report ausgeben (menschenlesbar).")
    parser.add_argument("--window-min", action="append", help="Zusätzliches Analysefenster in Minuten; mehrfach nutzbar.")
    parser.add_argument("--latest-limit", type=int, default=48, help="Anzahl letzter ptz.marker.* Punkte im JSON-Report. Default: 48")
    parser.add_argument("--verbose", action="store_true", help="Zusätzliche Kurzzeile ausgeben.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    windows = _parse_windows(args.window_min)
    report = build_report(windows, latest_limit=max(1, int(args.latest_limit)))

    emit_json = bool(args.json or not args.text)
    if args.text:
        _print_text(report)
    if emit_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.verbose:
        windows_report = report.get("windows") if isinstance(report.get("windows"), list) else []
        last_window = windows_report[0] if windows_report and isinstance(windows_report[0], Mapping) else {}
        summary = last_window.get("summary") if isinstance(last_window.get("summary"), Mapping) else {}
        print(
            "[ptz_positive_position_evidence_report] "
            f"ok={report.get('ok')} points={report.get('point_count_loaded', 0)} "
            f"window={last_window.get('window_min', '-')}min "
            f"positive_last={summary.get('positive_count_last', 0)} "
            f"repeat_ge_5={summary.get('repeat_ge_5_last', 0)} "
            f"ceiling_active_rate={summary.get('ceiling_active_rate', 0)}",
            flush=True,
        )
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

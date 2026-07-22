#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/ptz_zoom_observe_ui.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Zoom Observe UI – read-only Safety-/Audit-Kachel
# Version:   v3.7.12+ptz-zoom-observe-ui-v1.0
# Stand:     2026-06-28
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses UI-Modul macht den aktuellen PTZ-Zoom-Policy-Pfad sichtbar, ohne die
# Kamera zu bewegen. Es liest ausschließlich vorhandene Telemetrie aus
# `data/stats.db.stats_points` sowie die optionalen State-Dateien der Preview-
# und Apply-Tools. Es ist damit eine „spielartige“ Observe-/Safety-Kachel:
#
#   Zustand:    Motor-Reason, Frame-Qualität, Confidence-Werte, Zoom-Evidence
#   Aktion:     hold / recommend_wide / would_apply_but_gate_closed / applied
#   Gate:       Apply-ENV, CLI-Gate, Rate-Limit, Controller-Status
#   Lernwert:   Sichtbarkeit, ob Wide-Zoom nur in Suchzuständen empfohlen wird
#
# SICHERHEITSINVARIANTEN
# ──────────────────────
# - Keine PTZ-Kommandos, kein `v4l2-ctl`, kein PTZController-Import.
# - Keine DB-Writes, keine DBWriter-Calls, keine policy_rules-Änderungen.
# - Nur Read-only SQLite-Verbindungen zu `stats.db`.
# - Fehler werden in der UI/API sichtbar als `ok=false`/`errors`, nicht still
#   verschluckt.
# - Die Karte ersetzt keine Freigabe für echten Auto-Zoom. Sie ist Audit und
#   Diagnose für P3z1b/P3z1c.
#
# BETRIEBSKONTEXT
# ───────────────
# PTZ ist echte Hardware. Deshalb wird dieser Pfad bewusst getrennt von den
# aktiven PTZ-Games (Arena/Target/Coverage) geführt. Die Observe-Seite darf im
# Normalbetrieb dauerhaft sichtbar sein, weil sie keinerlei Bewegung auslöst.
#
# ROUTEN
# ──────
#   GET /ptz_zoom_observe/             HTML-Übersicht
#   GET /ptz_zoom_observe/api/status   JSON-Auditstatus
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from flask import Blueprint, jsonify, render_template

ptz_zoom_observe_bp = Blueprint(
    "ptz_zoom_observe",
    __name__,
    url_prefix="/ptz_zoom_observe",
    template_folder="templates",
)

# Kompatibilität mit register_games(...): einige UIs exportieren `bp`.
bp = ptz_zoom_observe_bp


# -----------------------------------------------------------------------------
# Kleine, robuste Helper – bewusst lokal, damit diese UI read-only und ohne
# Kopplung an PTZ-Controller/Policy-Module bleibt.
# -----------------------------------------------------------------------------

def _base_dir() -> Path:
    return Path(os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma")


def _stats_db_path() -> Path:
    return Path(os.environ.get("OROMA_STATS_DB_PATH") or (_base_dir() / "data" / "stats.db"))


def _preview_state_path() -> Path:
    return Path(os.environ.get("OROMA_PTZ_ZOOM_POLICY_STATE_PATH") or (_base_dir() / "data" / "state" / "ptz_zoom_policy_preview_state.json"))


def _apply_state_path() -> Path:
    return Path(os.environ.get("OROMA_PTZ_ZOOM_AUTO_STATE_PATH") or (_base_dir() / "data" / "state" / "ptz_zoom_policy_apply_state.json"))


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
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
    if not math.isfinite(out):
        return float(default)
    return float(out)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _fmt_ts(ts: Any) -> str:
    n = _safe_int(ts, 0)
    if n <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(n))
    except Exception:
        return ""


def _parse_meta(raw: Any) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _connect_stats_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    con = sqlite3.connect(uri, timeout=10.0, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has_stats_points(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stats_points' LIMIT 1"
    ).fetchone()
    return bool(row)


def _series_rows(con: sqlite3.Connection, series: str, limit: int = 500) -> List[sqlite3.Row]:
    return list(con.execute(
        """
        SELECT ts, value, meta
        FROM stats_points
        WHERE series=?
        ORDER BY rowid DESC
        LIMIT ?
        """,
        (str(series), int(limit)),
    ))


def _series_count(con: sqlite3.Connection, series: str) -> int:
    row = con.execute("SELECT COUNT(*) AS c FROM stats_points WHERE series=?", (str(series),)).fetchone()
    return _safe_int(row["c"] if row else 0, 0)


def _latest_by_series(con: sqlite3.Connection, series: str) -> Optional[Dict[str, Any]]:
    row = con.execute(
        """
        SELECT ts, value, meta
        FROM stats_points
        WHERE series=?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (str(series),),
    ).fetchone()
    if not row:
        return None
    meta = _parse_meta(row["meta"])
    return {
        "ts": _safe_int(row["ts"], 0),
        "time": _fmt_ts(row["ts"]),
        "value": _safe_float(row["value"], 0.0),
        "meta": meta,
    }


def _counter_dict(counter: Counter) -> Dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common()}


def _preview_summary(con: sqlite3.Connection, limit: int = 500) -> Dict[str, Any]:
    rows = _series_rows(con, "ptz.zoom_policy.preview.recommend_wide", limit=limit)
    by_reason: Counter = Counter()
    recommend_by_reason: Counter = Counter()
    final_decisions: Counter = Counter()
    latest_items: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows):
        value = _safe_float(row["value"], 0.0)
        meta = _parse_meta(row["meta"])
        motor = meta.get("motor") if isinstance(meta.get("motor"), Mapping) else {}
        reason = str(motor.get("reason") or "unknown")
        by_reason[reason] += 1
        if value > 0.5:
            recommend_by_reason[reason] += 1
        final_decisions[str(meta.get("final_decision") or "unknown")] += 1

        if idx < 40:
            latest_items.append({
                "ts": _safe_int(row["ts"], 0),
                "time": _fmt_ts(row["ts"]),
                "recommend": value,
                "final_decision": meta.get("final_decision"),
                "motor_reason": motor.get("reason"),
                "quality": motor.get("quality"),
                "target_conf": motor.get("target_conf"),
                "obs_conf": motor.get("obs_conf"),
                "candidate_conf": motor.get("candidate_conf"),
                "reason": meta.get("reason"),
            })

    latest = latest_items[0] if latest_items else {}
    return {
        "series": "ptz.zoom_policy.preview.recommend_wide",
        "samples": len(rows),
        "recommend_wide_count": int(sum(1 for r in rows if _safe_float(r["value"], 0.0) > 0.5)),
        "recommend_wide_rate": (float(sum(1 for r in rows if _safe_float(r["value"], 0.0) > 0.5)) / float(len(rows))) if rows else 0.0,
        "by_motor_reason": _counter_dict(by_reason),
        "recommend_by_reason": _counter_dict(recommend_by_reason),
        "final_decisions": _counter_dict(final_decisions),
        "latest": latest,
        "latest_items": latest_items,
    }


def _apply_summary(con: sqlite3.Connection, limit: int = 300) -> Dict[str, Any]:
    # Die Serie `apply.applied` existiert einmal pro Apply-Auswertung und eignet
    # sich zur zählenden Meta-Auswertung ohne Mehrfachzählung über alle Apply-
    # Submetriken.
    rows = _series_rows(con, "ptz.zoom_policy.apply.applied", limit=limit)
    final_decisions: Counter = Counter()
    by_reason: Counter = Counter()
    preview_recommend_count = 0
    apply_allowed_count = 0
    applied_count = 0
    enabled_count = 0
    latest_items: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows):
        meta = _parse_meta(row["meta"])
        motor = meta.get("motor") if isinstance(meta.get("motor"), Mapping) else {}
        final = str(meta.get("final_decision") or "unknown")
        reason = str(motor.get("reason") or "unknown")
        final_decisions[final] += 1
        by_reason[reason] += 1
        if bool(meta.get("preview_recommend")):
            preview_recommend_count += 1
        if bool(meta.get("apply_allowed")):
            apply_allowed_count += 1
        if bool(meta.get("applied")) or _safe_float(row["value"], 0.0) > 0.5:
            applied_count += 1
        if bool(meta.get("enabled")):
            enabled_count += 1
        if idx < 40:
            latest_items.append({
                "ts": _safe_int(row["ts"], 0),
                "time": _fmt_ts(row["ts"]),
                "value": _safe_float(row["value"], 0.0),
                "final_decision": meta.get("final_decision"),
                "enabled": bool(meta.get("enabled")),
                "preview_recommend": bool(meta.get("preview_recommend")),
                "apply_allowed": bool(meta.get("apply_allowed")),
                "applied": bool(meta.get("applied")) or _safe_float(row["value"], 0.0) > 0.5,
                "already_at_target": bool(meta.get("already_at_target")),
                "controller_ready": bool(meta.get("controller_ready")),
                "target_zoom": meta.get("target_zoom"),
                "current_zoom": meta.get("current_zoom"),
                "motor_reason": motor.get("reason"),
                "quality": motor.get("quality"),
                "target_conf": motor.get("target_conf"),
                "obs_conf": motor.get("obs_conf"),
                "candidate_conf": motor.get("candidate_conf"),
                "reason": meta.get("reason"),
            })

    latest = latest_items[0] if latest_items else {}
    return {
        "series": "ptz.zoom_policy.apply.applied",
        "samples": len(rows),
        "preview_recommend_count": int(preview_recommend_count),
        "apply_allowed_count": int(apply_allowed_count),
        "applied_count": int(applied_count),
        "enabled_count": int(enabled_count),
        "final_decisions": _counter_dict(final_decisions),
        "by_motor_reason": _counter_dict(by_reason),
        "latest": latest,
        "latest_items": latest_items,
        "series_counts": {
            "preview_recommend": _series_count(con, "ptz.zoom_policy.apply.preview_recommend"),
            "apply_allowed": _series_count(con, "ptz.zoom_policy.apply.apply_allowed"),
            "applied": _series_count(con, "ptz.zoom_policy.apply.applied"),
            "already_at_target": _series_count(con, "ptz.zoom_policy.apply.already_at_target"),
            "enabled": _series_count(con, "ptz.zoom_policy.apply.enabled"),
            "target_zoom": _series_count(con, "ptz.zoom_policy.apply.target_zoom"),
            "current_zoom": _series_count(con, "ptz.zoom_policy.apply.current_zoom"),
        },
    }


def build_status() -> Dict[str, Any]:
    """Read-only PTZ Zoom Observe status for HTML and JSON.

    The function opens `stats.db` in SQLite read-only mode. It deliberately does
    not import PTZ control modules and does not touch DBWriter. If `stats.db` is
    missing (e.g. fresh install), the returned document remains renderable.
    """
    now = int(time.time())
    stats_path = _stats_db_path()
    errors: List[str] = []
    preview: Dict[str, Any] = {"samples": 0, "recommend_wide_count": 0, "by_motor_reason": {}, "recommend_by_reason": {}, "latest_items": []}
    apply: Dict[str, Any] = {"samples": 0, "applied_count": 0, "apply_allowed_count": 0, "final_decisions": {}, "latest_items": []}

    if not stats_path.exists():
        errors.append(f"stats_db_missing:{stats_path}")
    else:
        try:
            with _connect_stats_ro(stats_path) as con:
                if not _has_stats_points(con):
                    errors.append("stats_points_missing")
                else:
                    preview = _preview_summary(con, limit=500)
                    apply = _apply_summary(con, limit=300)
        except Exception as exc:
            errors.append(f"stats_read_error:{exc}")

    preview_state_path = _preview_state_path()
    apply_state_path = _apply_state_path()
    preview_state = _read_json(preview_state_path)
    apply_state = _read_json(apply_state_path)

    # Safety verdict: compact, readable state for the card/header.
    apply_latest = apply.get("latest") if isinstance(apply.get("latest"), Mapping) else {}
    preview_latest = preview.get("latest") if isinstance(preview.get("latest"), Mapping) else {}
    applied_count = _safe_int(apply.get("applied_count"), 0)
    apply_allowed_count = _safe_int(apply.get("apply_allowed_count"), 0)
    enabled_now = bool(apply_latest.get("enabled")) if apply_latest else bool((apply_state.get("last") or {}).get("enabled") if isinstance(apply_state.get("last"), Mapping) else False)
    if applied_count > 0:
        safety = "applied_seen"
    elif apply_allowed_count > 0:
        safety = "apply_allowed_seen"
    elif enabled_now:
        safety = "enabled_no_apply"
    else:
        safety = "fail_closed"

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "now": now,
        "now_time": _fmt_ts(now),
        "stats_db": str(stats_path),
        "preview_state_path": str(preview_state_path),
        "apply_state_path": str(apply_state_path),
        "preview_state_exists": preview_state_path.exists(),
        "apply_state_exists": apply_state_path.exists(),
        "preview_state": preview_state,
        "apply_state": apply_state,
        "preview": preview,
        "apply": apply,
        "safety": {
            "state": safety,
            "read_only_ui": True,
            "no_ptz_commands": True,
            "no_db_writes": True,
            "auto_zoom_enabled_seen": bool(enabled_now),
            "applied_count": int(applied_count),
            "apply_allowed_count": int(apply_allowed_count),
            "latest_preview_reason": preview_latest.get("motor_reason") if preview_latest else None,
            "latest_apply_decision": apply_latest.get("final_decision") if apply_latest else None,
        },
    }


@ptz_zoom_observe_bp.route("/", methods=["GET"])
def page() -> str:
    return render_template("ptz_zoom_observe.html", status=build_status())


@ptz_zoom_observe_bp.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(build_status())

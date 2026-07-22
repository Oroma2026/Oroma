#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_positive_position_probe.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Positive Position Probe – Stage-A Evidence / Measure-only
# Version:   v3.7.3+ptz-positive-position-probe-v1.1-idempotent-stats
# Stand:     2026-06-16
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool macht die neuen PTZ Positive Position Marker messbar, ohne daraus
# bereits eine automatische Steuerung, Policy-Regel oder Binding-Materialisierung
# abzuleiten. Es ist bewusst eine Stage-A-Probe im Sinne der Core-Dokumentation:
#
#   messen  → sichtbar machen  → später Dream/Binding entscheiden lassen
#
# Der PTZ Motor Worker schreibt konservative Marker nach:
#
#   data/state/ptz_positive_position_markers.json
#
# Dieses Tool liest diese Datei sowie optional den aktuellen Worker-State:
#
#   data/state/ptz_motor_state.json
#
# und erzeugt daraus eine kompakte JSON-Zusammenfassung. Mit `--write-stats`
# schreibt es zusätzlich Metriken in `stats.db.stats_points` – ausschließlich
# über den DBWriter. Es gibt keinen lokalen SQLite-Write-Fallback.
#
# ARCHITEKTUR-INVARIANTEN
# ───────────────────────
# - Keine Motorsteuerung.
# - Keine Policy-Aktivierung.
# - Keine Writes in `oroma.db`.
# - Keine Materialisierung in object_nodes/object_relations.
# - Keine personenbezogene Identität; Marker bleiben reine Positions-/Salience-
#   Evidenz.
# - Lokale SQLite-Zugriffe sind nur read-only bzw. entfallen vollständig.
# - Stats-Writes nur über DBWriter-kompatiblen Single-Writer-Pfad.
# - Stats-Writes sind idempotent: ein versehentlich doppelter Lauf in derselben
#   Sekunde darf keinen Timer-Fehler und keinen DBWriter-Unique-Fehler erzeugen.
#
# METRIKEN BEI --write-stats
# ──────────────────────────
#   ptz.marker.marker_count
#   ptz.marker.positive_count
#   ptz.marker.repeat_ge_3
#   ptz.marker.repeat_ge_5
#   ptz.marker.max_count
#   ptz.marker.max_score_ema
#   ptz.marker.eye_pair_positive
#   ptz.marker.face_positive
#   ptz.marker.motion_positive
#   ptz.marker.motion_guard_blocked
#   ptz.marker.ceiling_active
#   ptz.marker.ceiling_marker_stale
#
# ENVIRONMENT
# ───────────
#   OROMA_BASE                                  Default: /opt/ai/oroma
#   OROMA_PTZ_MOTOR_STATE_PATH                  Default: $OROMA_BASE/data/state/ptz_motor_state.json
#   OROMA_PTZ_POSITIVE_MARKER_PATH              Default: $OROMA_BASE/data/state/ptz_positive_position_markers.json
#   OROMA_DBW_ENABLE                            Muss für --write-stats aktiv sein
#   OROMA_DBW_SOCKET                            Default: $OROMA_BASE/data/state/db_writer.sock
#   OROMA_PTZ_POS_PROBE_DBW_TIMEOUT_MS          Default: 10000
#
# BEISPIELE
# ─────────
# Nur lesen / JSON ausgeben:
#
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     python3 tools/ptz_positive_position_probe.py --once --verbose
#
# Stage-A-Metriken via DBWriter schreiben:
#
#   cd /opt/ai/oroma
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/ptz_positive_position_probe.py --once --write-stats --verbose
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore


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


def _env_path(name: str, default: str) -> Path:
    raw = os.environ.get(name)
    return Path(str(raw).strip() if raw else default)


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


def _markers(data: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = data.get("markers")
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, Mapping):
            out[str(key)] = dict(value)
    return out


def _positive_markers(markers: Mapping[str, Mapping[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    for key, marker in markers.items():
        if bool(marker.get("is_positive")):
            out.append((str(key), dict(marker)))
    out.sort(key=lambda kv: (_safe_int(kv[1].get("count")), _safe_float(kv[1].get("score_ema"))), reverse=True)
    return out


def _source_family(marker: Mapping[str, Any]) -> str:
    source = str(marker.get("last_source") or "").lower()
    kind = str(marker.get("last_kind") or "").lower()
    winner = str(marker.get("last_winner") or "").lower()
    joined = " ".join([source, kind, winner])
    if "eye" in joined:
        return "eye"
    if "face" in joined:
        return "face"
    if "motion" in joined:
        return "motion"
    return "other"


def build_summary() -> Dict[str, Any]:
    now = int(time.time())
    marker_file = _read_json(_marker_path())
    state = _read_json(_state_path())
    marker_meta = state.get("positive_position_marker_meta") if isinstance(state.get("positive_position_marker_meta"), Mapping) else {}
    ceiling = state.get("ceiling_recovery") if isinstance(state.get("ceiling_recovery"), Mapping) else {}

    markers = _markers(marker_file)
    positives = _positive_markers(markers)
    counts = [_safe_int(m.get("count")) for m in markers.values()]
    scores = [_safe_float(m.get("score_ema")) for m in markers.values()]

    family_counts = {"eye": 0, "face": 0, "motion": 0, "other": 0}
    for _, marker in positives:
        family_counts[_source_family(marker)] = int(family_counts.get(_source_family(marker), 0)) + 1

    last_update = marker_meta.get("last_update") if isinstance(marker_meta.get("last_update"), Mapping) else {}
    last_reason = str(last_update.get("reason") or "")
    motion_guard_blocked = 1 if last_reason in ("upper_motion_guard", "motion_only_guard", "motion_min_conf_guard") else 0

    top_key = positives[0][0] if positives else ""
    top_marker = positives[0][1] if positives else {}

    return {
        "ok": True,
        "ts": now,
        "marker_path": str(_marker_path()),
        "state_path": str(_state_path()),
        "marker_version": marker_file.get("version"),
        "legacy_reset_reason": marker_file.get("legacy_reset_reason"),
        "marker_count": len(markers),
        "positive_count": len(positives),
        "repeat_ge_3": sum(1 for c in counts if c >= 3),
        "repeat_ge_5": sum(1 for c in counts if c >= 5),
        "max_count": max(counts) if counts else 0,
        "max_score_ema": max(scores) if scores else 0.0,
        "top_key": top_key,
        "top_count": _safe_int(top_marker.get("count")),
        "top_score_ema": _safe_float(top_marker.get("score_ema")),
        "top_avg_dx": _safe_float(top_marker.get("avg_dx")),
        "top_avg_dy": _safe_float(top_marker.get("avg_dy")),
        "eye_pair_positive": family_counts["eye"],
        "face_positive": family_counts["face"],
        "motion_positive": family_counts["motion"],
        "other_positive": family_counts["other"],
        "last_update_reason": last_reason,
        "motion_guard_blocked": motion_guard_blocked,
        "ceiling_active": 1 if bool(ceiling.get("active")) else 0,
        "ceiling_marker_stale": 1 if bool(ceiling.get("marker_stale")) else 0,
        "ceiling_target_weak": 1 if bool(ceiling.get("target_weak")) else 0,
        "ceiling_start_grace_ok": 1 if bool(ceiling.get("start_grace_ok")) else 0,
        "ceiling_reason": str(ceiling.get("reason") or ""),
        "positive_top": [m for _, m in positives[:5]],
    }


def _dbw_timeout_ms() -> int:
    return _env_int("OROMA_PTZ_POS_PROBE_DBW_TIMEOUT_MS", 10000, lo=500, hi=120000)


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
            resp = client.request(op="ping", timeout_ms=1000, expect="none", tag="ptz.positive_position_probe.ping")
            if bool(resp.get("ok")):
                return
        except Exception:
            pass
    raise RuntimeError("DBWriter required but not available/enabled")


def _metric_rows(summary: Mapping[str, Any]) -> Iterable[Tuple[str, float]]:
    for key in (
        "marker_count",
        "positive_count",
        "repeat_ge_3",
        "repeat_ge_5",
        "max_count",
        "max_score_ema",
        "eye_pair_positive",
        "face_positive",
        "motion_positive",
        "motion_guard_blocked",
        "ceiling_active",
        "ceiling_marker_stale",
    ):
        yield f"ptz.marker.{key}", _safe_float(summary.get(key))


def write_stats(summary: Mapping[str, Any]) -> int:
    """Write Stage-A PTZ marker metrics via DBWriter, idempotently.

    `stats_points` has a unique index on `(src_table, src_uid, series)`. The
    systemd timer, manual tests, or a delayed DBWriter response can cause the
    same second-level snapshot to be submitted more than once. This must not be
    treated as a hard failure because the snapshot is measure-only Evidence and
    has deterministic values for a given `(ts, series)`.

    Therefore this function uses an explicit UPSERT instead of a plain INSERT.
    It preserves DBWriter-only write discipline and avoids noisy Tracebacks in
    `ptz_positive_position_probe.err.log` while keeping the newest value/meta if
    the same snapshot UID is written again.
    """
    _dbw_required()
    ts = _safe_int(summary.get("ts"), int(time.time()))
    meta = {
        "top_key": summary.get("top_key"),
        "top_count": summary.get("top_count"),
        "top_score_ema": summary.get("top_score_ema"),
        "last_update_reason": summary.get("last_update_reason"),
        "ceiling_reason": summary.get("ceiling_reason"),
        "legacy_reset_reason": summary.get("legacy_reset_reason"),
    }
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    written = 0
    for series, value in _metric_rows(summary):
        src_uid = f"ptz_positive_position_probe:{ts}:{series}"
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
            params=[int(ts), str(series), float(value), "ptz_positive_position_probe", 0, meta_json, src_uid],
            tag="ptz.positive_position_probe.stats_points.upsert",
            priority="low",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
        written += 1
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ORÓMA PTZ Positive Position Probe – Stage-A Evidence / Measure-only")
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
            "[ptz_positive_position_probe] "
            f"marker_count={summary.get('marker_count')} positive_count={summary.get('positive_count')} "
            f"repeat_ge_3={summary.get('repeat_ge_3')} top={summary.get('top_key') or '-'} "
            f"motion_guard_blocked={summary.get('motion_guard_blocked')} "
            f"stats_written={summary.get('stats_written', 0)}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

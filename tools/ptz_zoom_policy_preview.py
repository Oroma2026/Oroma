#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_zoom_policy_preview.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Zoom Policy Preview – P3z1b Wide-Observe Dry-Run
# Version:   v3.7.3+p3z1b-wide-observe-policy-preview-v1.0
# Stand:     2026-06-25
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool ist der P3z1b-Dry-Run zwischen Zoom-Evidence (P3z0/P3z0.1) und
# einer späteren echten Zoom-Policy. Es berechnet, ob ORÓMA aktuell den
# Wide-Observe-Zoom (Default: 100) empfehlen würde, steuert aber bewusst keine
# Kamera und ruft kein `v4l2-ctl -c` auf.
#
# WICHTIGE INVARIANTEN
# ────────────────────
# - Keine Pan/Tilt-/Zoom-Steuerung.
# - Keine Motor- oder Policy-Aktivierung.
# - Keine Writes in `oroma.db` und keine object_nodes/object_relations.
# - Stats-Writes ausschließlich via DBWriter nach `stats.db.stats_points`.
# - PTZ-Motor-State wird read-only über `data/state/ptz_motor_state.json` bzw.
#   `core.ptz_motor_state` gelesen.
# - `confidence=0.0` allein ist KEIN Trigger; eligible sind nur explizite
#   Gründe wie `deadzone`, `stale_frame`, `no_frame`, kombiniert mit Wide-
#   Evidence und ohne starkes Zielsignal.
# - Fehler werden sichtbar in JSON, stdout/stderr, State-Datei und optionalen
#   Stats-Metadaten abgelegt.
#
# ENTSCHEIDUNGSIDEE
# ─────────────────
# recommend_wide_observe_zoom=true nur wenn:
#   1. P3z0.1-Report über ein belastbares Fenster `wide_zoom_likely_helpful`
#      oder äquivalente Mindestwerte zeigt,
#   2. der PTZ-Motor-State frisch und lesbar ist,
#   3. der Motor aktuell keinen starken Zielvektor hat,
#   4. der Worker-Grund ein Such-/Orientierungsgrund ist (`deadzone`,
#      `stale_frame`, `no_frame`, optional `energy_low`), nicht bloß eine
#      niedrige Confidence-Zahl.
#
# ENVIRONMENT
# ───────────
#   OROMA_BASE / OROMA_BASE_DIR                         Default: /opt/ai/oroma
#   OROMA_PTZ_ZOOM_POLICY_STATE_PATH                    Default: data/state/ptz_zoom_policy_preview_state.json
#   OROMA_PTZ_ZOOM_POLICY_TARGET_ZOOM                   Default: 100
#   OROMA_PTZ_ZOOM_POLICY_WINDOWS_MIN                   Default: 60,360,1440
#   OROMA_PTZ_ZOOM_POLICY_MIN_HELPFUL_RATE              Default: 0.50
#   OROMA_PTZ_ZOOM_POLICY_MIN_CONTEXT_GAIN_RATE         Default: 0.35
#   OROMA_PTZ_ZOOM_POLICY_MIN_SAMPLE_COUNT              Default: 3
#   OROMA_PTZ_ZOOM_POLICY_TARGET_CONF_MAX               Default: 0.060
#   OROMA_PTZ_ZOOM_POLICY_OBS_CONF_MAX                  Default: 0.080
#   OROMA_PTZ_ZOOM_POLICY_CANDIDATE_CONF_MAX            Default: 0.080
#   OROMA_PTZ_ZOOM_POLICY_ELIGIBLE_REASONS              Default: deadzone,stale_frame,no_frame,energy_low
#   OROMA_PTZ_ZOOM_POLICY_DBW_TIMEOUT_MS                Default: 10000
#
# BEISPIELE
# ─────────
#   cd /opt/ai/oroma
#   python3 tools/ptz_zoom_policy_preview.py --once --verbose
#
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/ptz_zoom_policy_preview.py --once --write-stats --verbose
# =============================================================================

from __future__ import annotations

import argparse
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
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore

try:
    from core.ptz_motor_state import (  # type: ignore
        DEFAULT_STATE_PATH as _MOTOR_DEFAULT_STATE_PATH,
        build_ptz_motor_attention_snapshot,
        read_ptz_motor_state,
    )
except Exception:  # pragma: no cover
    _MOTOR_DEFAULT_STATE_PATH = os.path.join(BASE, "data", "state", "ptz_motor_state.json")
    build_ptz_motor_attention_snapshot = None  # type: ignore
    read_ptz_motor_state = None  # type: ignore

try:
    from tools.ptz_zoom_context_evidence_report import build_report, _best_window  # type: ignore
except Exception:  # pragma: no cover
    build_report = None  # type: ignore
    _best_window = None  # type: ignore


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
        return int(float(value))
    except Exception:
        return int(default)


def _split_csv(text: str) -> List[str]:
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def _state_path() -> Path:
    default = os.path.join(BASE, "data", "state", "ptz_zoom_policy_preview_state.json")
    return Path(os.environ.get("OROMA_PTZ_ZOOM_POLICY_STATE_PATH", default))


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


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        if isinstance(value, Mapping):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in list(value)]
        return str(value)


def _windows_from_env() -> List[int]:
    raw = os.environ.get("OROMA_PTZ_ZOOM_POLICY_WINDOWS_MIN", "60,360,1440")
    out: List[int] = []
    for part in _split_csv(raw):
        try:
            n = int(part)
            if n > 0:
                out.append(n)
        except Exception:
            continue
    return out or [60, 360, 1440]


def _load_zoom_evidence(windows_min: Sequence[int]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    if build_report is None or _best_window is None:
        raise RuntimeError("ptz_zoom_context_evidence_report helpers unavailable")
    report = build_report([int(w) for w in windows_min])
    best = _best_window(report)
    if not isinstance(best, Mapping):
        best = {}
    summary = best.get("summary") if isinstance(best.get("summary"), Mapping) else {}
    return dict(report), dict(best), dict(summary)


def _load_motor_state() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    state_path = os.environ.get("OROMA_PTZ_MOTOR_STATE_PATH", str(_MOTOR_DEFAULT_STATE_PATH))
    if read_ptz_motor_state is not None:
        state = read_ptz_motor_state(path=state_path)
    else:
        state = _read_json(Path(state_path))
        state.setdefault("exists", Path(state_path).exists())
        state.setdefault("state_stale", True)
        state.setdefault("error", "core.ptz_motor_state unavailable")
    if build_ptz_motor_attention_snapshot is not None:
        attention = build_ptz_motor_attention_snapshot(state)
    else:
        attention = {
            "ok": bool(state.get("exists") and not state.get("error")),
            "quality": "stale" if bool(state.get("state_stale")) else "fresh",
            "reason": str(state.get("reason") or ""),
            "action": str(state.get("action") or state.get("mapped_action") or ""),
            "candidate": state.get("candidate") if isinstance(state.get("candidate"), Mapping) else {},
            "target": {"confidence": _safe_float(state.get("target_conf"), 0.0)},
            "vector": {"dist": _safe_float(state.get("dist"), 0.0), "energy": _safe_float(state.get("energy"), 0.0)},
        }
    return _json_safe(state), _json_safe(attention)


def _evidence_ready(summary: Mapping[str, Any], window: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    decision = str(summary.get("decision") or "")
    sample_count = _safe_int(window.get("sample_count"), 0)
    min_samples = _env_int("OROMA_PTZ_ZOOM_POLICY_MIN_SAMPLE_COUNT", 3, lo=1, hi=100000)
    helpful_rate = _safe_float(summary.get("wide_helpful_rate"), 0.0)
    context_gain_rate = _safe_float(summary.get("wide_context_gain_rate"), 0.0)
    min_helpful = _env_float("OROMA_PTZ_ZOOM_POLICY_MIN_HELPFUL_RATE", 0.50, lo=0.0, hi=1.0)
    min_context = _env_float("OROMA_PTZ_ZOOM_POLICY_MIN_CONTEXT_GAIN_RATE", 0.35, lo=0.0, hi=1.0)
    if sample_count < min_samples:
        reasons.append(f"zoom_evidence_samples_low:{sample_count}<{min_samples}")
    if decision == "wide_zoom_likely_helpful":
        reasons.append("zoom_decision:wide_zoom_likely_helpful")
    if helpful_rate >= min_helpful:
        reasons.append(f"helpful_rate:{helpful_rate:.6f}>={min_helpful:.6f}")
    if context_gain_rate >= min_context:
        reasons.append(f"context_gain_rate:{context_gain_rate:.6f}>={min_context:.6f}")
    ready = bool(sample_count >= min_samples and (decision == "wide_zoom_likely_helpful" or helpful_rate >= min_helpful or context_gain_rate >= min_context))
    return ready, reasons


def _motor_eligible(state: Mapping[str, Any], attention: Mapping[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    reasons: List[str] = []
    details: Dict[str, Any] = {}
    eligible_reasons = set(_split_csv(os.environ.get("OROMA_PTZ_ZOOM_POLICY_ELIGIBLE_REASONS", "deadzone,stale_frame,no_frame,energy_low")))
    disallow_reasons = set(_split_csv(os.environ.get("OROMA_PTZ_ZOOM_POLICY_DISALLOW_REASONS", "eye_hold,target_hold,follow,move_cooldown,micro_guard,policy_bias_hold,ceiling_recovery_home")))

    quality = str(attention.get("quality") or "")
    state_stale = bool(attention.get("state_stale") or state.get("state_stale"))
    state_error = str(state.get("error") or "")
    reason = str(attention.get("reason") or state.get("reason") or "")
    action = str(attention.get("action") or attention.get("mapped_action") or state.get("action") or state.get("mapped_action") or "")
    raw_action = str(attention.get("raw_action") or state.get("raw_action") or "")
    target = attention.get("target") if isinstance(attention.get("target"), Mapping) else {}
    candidate = attention.get("candidate") if isinstance(attention.get("candidate"), Mapping) else {}
    vector = attention.get("vector") if isinstance(attention.get("vector"), Mapping) else {}

    target_conf = _safe_float(target.get("confidence"), _safe_float(state.get("target_conf"), 0.0))
    obs_conf = _safe_float(state.get("obs_conf"), 0.0)
    candidate_conf = _safe_float(candidate.get("confidence"), 0.0)
    dist = _safe_float(vector.get("dist"), _safe_float(state.get("dist"), 0.0))
    energy = _safe_float(vector.get("energy"), _safe_float(state.get("energy"), 0.0))

    target_conf_max = _env_float("OROMA_PTZ_ZOOM_POLICY_TARGET_CONF_MAX", 0.060, lo=0.0, hi=1.0)
    obs_conf_max = _env_float("OROMA_PTZ_ZOOM_POLICY_OBS_CONF_MAX", 0.080, lo=0.0, hi=1.0)
    candidate_conf_max = _env_float("OROMA_PTZ_ZOOM_POLICY_CANDIDATE_CONF_MAX", 0.080, lo=0.0, hi=1.0)

    details.update({
        "quality": quality,
        "state_stale": state_stale,
        "state_error": state_error,
        "reason": reason,
        "action": action,
        "raw_action": raw_action,
        "target_conf": round(float(target_conf), 6),
        "obs_conf": round(float(obs_conf), 6),
        "candidate_conf": round(float(candidate_conf), 6),
        "dist": round(float(dist), 6),
        "energy": round(float(energy), 6),
        "eligible_reasons": sorted(eligible_reasons),
        "disallow_reasons": sorted(disallow_reasons),
    })

    if state_error:
        reasons.append(f"state_error:{state_error}")
    if state_stale or quality in ("stale", "stopped", "missing", "error"):
        reasons.append(f"state_not_fresh:{quality or 'unknown'}")
    if reason in eligible_reasons:
        reasons.append(f"reason_eligible:{reason}")
    else:
        reasons.append(f"reason_not_eligible:{reason or 'empty'}")
    if reason in disallow_reasons:
        reasons.append(f"reason_disallowed:{reason}")
    if action or raw_action:
        reasons.append(f"action_active:{action or raw_action}")
    if target_conf > target_conf_max:
        reasons.append(f"target_conf_high:{target_conf:.6f}>{target_conf_max:.6f}")
    if obs_conf > obs_conf_max:
        reasons.append(f"obs_conf_high:{obs_conf:.6f}>{obs_conf_max:.6f}")
    if candidate_conf > candidate_conf_max:
        reasons.append(f"candidate_conf_high:{candidate_conf:.6f}>{candidate_conf_max:.6f}")

    eligible = bool(
        not state_error
        and not state_stale
        and quality not in ("stale", "stopped", "missing", "error")
        and reason in eligible_reasons
        and reason not in disallow_reasons
        and not action
        and not raw_action
        and target_conf <= target_conf_max
        and obs_conf <= obs_conf_max
        and candidate_conf <= candidate_conf_max
    )
    return eligible, reasons, details


def _append_history(state_doc: Dict[str, Any], item: Mapping[str, Any], max_n: int = 288) -> List[Dict[str, Any]]:
    history = state_doc.get("history") if isinstance(state_doc.get("history"), list) else []
    out = [h for h in history if isinstance(h, Mapping)]
    compact = {
        "ts": int(item.get("ts") or int(time.time())),
        "recommend_wide_observe_zoom": bool(item.get("recommend_wide_observe_zoom")),
        "target_zoom": item.get("target_zoom"),
        "final_decision": item.get("final_decision"),
        "reason": item.get("reason"),
        "zoom_best_window_min": item.get("zoom_best_window_min"),
        "zoom_decision": item.get("zoom_decision"),
        "motor_reason": item.get("motor_reason"),
        "motor_quality": item.get("motor_quality"),
    }
    out.append(compact)
    return [dict(h) for h in out[-int(max_n):]]


def evaluate_preview() -> Dict[str, Any]:
    now = int(time.time())
    errors: List[str] = []
    windows = _windows_from_env()
    target_zoom = _env_int("OROMA_PTZ_ZOOM_POLICY_TARGET_ZOOM", 100, lo=1, hi=10000)

    zoom_report: Dict[str, Any] = {}
    best_window: Dict[str, Any] = {}
    zoom_summary: Dict[str, Any] = {}
    try:
        zoom_report, best_window, zoom_summary = _load_zoom_evidence(windows)
    except Exception as exc:
        errors.append(f"zoom_evidence_error:{exc}")

    motor_state: Dict[str, Any] = {}
    attention: Dict[str, Any] = {}
    try:
        motor_state, attention = _load_motor_state()
    except Exception as exc:
        errors.append(f"motor_state_error:{exc}")

    evidence_ready, evidence_reasons = _evidence_ready(zoom_summary, best_window)
    motor_ok, motor_reasons, motor_details = _motor_eligible(motor_state, attention)
    recommend = bool(not errors and evidence_ready and motor_ok)
    final_decision = "recommend_wide_observe_zoom" if recommend else "hold_current_zoom"

    reason_parts: List[str] = []
    reason_parts.extend(evidence_reasons)
    reason_parts.extend(motor_reasons)
    reason_parts.extend(errors)
    reason_text = ";".join(reason_parts)[:2000]

    out: Dict[str, Any] = {
        "ok": bool(not errors),
        "ts": now,
        "stage": "P3z1b_wide_observe_zoom_policy_preview_dry_run",
        "version": "v3.7.3+p3z1b-wide-observe-policy-preview-v1.0",
        "recommend_wide_observe_zoom": recommend,
        "final_decision": final_decision,
        "target_zoom": int(target_zoom),
        "reason": reason_text,
        "errors": errors,
        "policy": {
            "mode": "preview_only_no_camera_control",
            "no_zoom_set": True,
            "no_pan_tilt": True,
            "no_policy_activation": True,
            "no_materialization": True,
        },
        "zoom_evidence": {
            "ready": bool(evidence_ready),
            "windows_min": windows,
            "best_window_min": _safe_int(best_window.get("window_min"), 0),
            "sample_count": _safe_int(best_window.get("sample_count"), 0),
            "point_count": _safe_int(best_window.get("point_count"), 0),
            "decision": str(zoom_summary.get("decision") or ""),
            "wide_helpful_rate": _safe_float(zoom_summary.get("wide_helpful_rate"), 0.0),
            "wide_context_gain_rate": _safe_float(zoom_summary.get("wide_context_gain_rate"), 0.0),
            "score_delta_avg": _safe_float(zoom_summary.get("score_delta_avg"), 0.0),
            "wide_fov_delta_avg": _safe_float(zoom_summary.get("wide_fov_delta_avg"), 0.0),
            "edge_context_delta_avg": _safe_float(zoom_summary.get("edge_context_delta_avg"), 0.0),
            "bottom_right_delta_avg": _safe_float(zoom_summary.get("bottom_right_delta_avg"), 0.0),
            "reasons": evidence_reasons,
        },
        "motor": {
            "eligible": bool(motor_ok),
            "state_path": os.environ.get("OROMA_PTZ_MOTOR_STATE_PATH", str(_MOTOR_DEFAULT_STATE_PATH)),
            "reason": motor_details.get("reason"),
            "quality": motor_details.get("quality"),
            "state_stale": motor_details.get("state_stale"),
            "action": motor_details.get("action"),
            "raw_action": motor_details.get("raw_action"),
            "target_conf": motor_details.get("target_conf"),
            "obs_conf": motor_details.get("obs_conf"),
            "candidate_conf": motor_details.get("candidate_conf"),
            "dist": motor_details.get("dist"),
            "energy": motor_details.get("energy"),
            "candidate_winner": str(attention.get("candidate_winner") or ""),
            "candidate_source": str(attention.get("candidate_source") or ""),
            "target_mode": str(attention.get("target_mode") or ""),
            "reasons": motor_reasons,
            "schema": {
                "source": "core.ptz_motor_state.read_ptz_motor_state + build_ptz_motor_attention_snapshot",
                "raw_fields_used": ["reason", "action", "raw_action", "target_conf", "obs_conf", "candidate.confidence", "state_stale"],
                "attention_fields_used": ["quality", "reason", "action", "raw_action", "target.confidence", "candidate.confidence"],
            },
        },
    }

    previous = _read_json(_state_path())
    history = _append_history(previous, {
        **out,
        "zoom_best_window_min": out["zoom_evidence"]["best_window_min"],
        "zoom_decision": out["zoom_evidence"]["decision"],
        "motor_reason": out["motor"]["reason"],
        "motor_quality": out["motor"]["quality"],
    })
    state_doc = {
        "version": 1,
        "updated_ts": now,
        "stage": out["stage"],
        "last": out,
        "history": history,
        "note": "P3z1b is preview/dry-run only: it recommends whether wide-observe zoom would be chosen, but it never controls camera zoom.",
    }
    _atomic_write_json(_state_path(), state_doc)
    return out


def _dbw_timeout_ms() -> int:
    return _env_int("OROMA_PTZ_ZOOM_POLICY_DBW_TIMEOUT_MS", 10000, lo=500, hi=120000)


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
            resp = client.request(op="ping", timeout_ms=1000, expect="none", tag="ptz.zoom_policy_preview.ping")
            if bool(resp.get("ok")):
                return
        except Exception:
            pass
    raise RuntimeError("DBWriter required but not available/enabled")


def _metric_rows(summary: Mapping[str, Any]) -> Iterable[Tuple[str, float, Dict[str, Any]]]:
    zoom = summary.get("zoom_evidence") if isinstance(summary.get("zoom_evidence"), Mapping) else {}
    motor = summary.get("motor") if isinstance(summary.get("motor"), Mapping) else {}
    common = {
        "stage": summary.get("stage"),
        "final_decision": summary.get("final_decision"),
        "recommend_wide_observe_zoom": bool(summary.get("recommend_wide_observe_zoom")),
        "reason": summary.get("reason"),
        "target_zoom": summary.get("target_zoom"),
        "zoom_evidence": zoom,
        "motor": motor,
        "errors": summary.get("errors") or [],
    }
    yield "ptz.zoom_policy.preview.recommend_wide", 1.0 if bool(summary.get("recommend_wide_observe_zoom")) else 0.0, dict(common)
    yield "ptz.zoom_policy.preview.evidence_ready", 1.0 if bool(zoom.get("ready")) else 0.0, dict(common)
    yield "ptz.zoom_policy.preview.motor_eligible", 1.0 if bool(motor.get("eligible")) else 0.0, dict(common)
    yield "ptz.zoom_policy.preview.target_zoom", _safe_float(summary.get("target_zoom"), 0.0), dict(common)
    yield "ptz.zoom_policy.preview.best_window_min", _safe_float(zoom.get("best_window_min"), 0.0), dict(common)
    yield "ptz.zoom_policy.preview.helpful_rate", _safe_float(zoom.get("wide_helpful_rate"), 0.0), dict(common)
    yield "ptz.zoom_policy.preview.context_gain_rate", _safe_float(zoom.get("wide_context_gain_rate"), 0.0), dict(common)
    yield "ptz.zoom_policy.preview.motor_target_conf", _safe_float(motor.get("target_conf"), 0.0), dict(common)
    yield "ptz.zoom_policy.preview.motor_obs_conf", _safe_float(motor.get("obs_conf"), 0.0), dict(common)


def write_stats(summary: Mapping[str, Any]) -> int:
    _dbw_required()
    ts = _safe_int(summary.get("ts"), int(time.time()))
    written = 0
    for series, value, meta in _metric_rows(summary):
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        src_uid = f"ptz_zoom_policy_preview:{ts}:{series}"
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
            params=[int(ts), str(series), float(value), "ptz_zoom_policy_preview", 0, meta_json, src_uid],
            tag="ptz.zoom_policy_preview.stats_points.upsert",
            priority="low",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
        written += 1
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ORÓMA PTZ Zoom Policy Preview – P3z1b dry-run")
    p.add_argument("--once", action="store_true", help="Einmal auswerten und beenden.")
    p.add_argument("--write-stats", action="store_true", help="Preview-Metriken via DBWriter in stats.db schreiben.")
    p.add_argument("--json", action="store_true", help="JSON ausgeben.")
    p.add_argument("--verbose", action="store_true", help="Kompakte Diagnosezeile ausgeben.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = evaluate_preview()
    if args.write_stats:
        try:
            summary["stats_written"] = write_stats(summary)
        except Exception as exc:
            summary["ok"] = False
            summary.setdefault("errors", []).append(f"stats_write_error:{exc}")
            print(f"[ptz_zoom_policy_preview] stats_write_error={exc}", file=sys.stderr, flush=True)
            # Update state with visible stats error as well.
            previous = _read_json(_state_path())
            previous["last"] = summary
            previous["updated_ts"] = _safe_int(summary.get("ts"), int(time.time()))
            _atomic_write_json(_state_path(), previous)
            return 2
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.verbose or not args.json:
        zoom = summary.get("zoom_evidence") if isinstance(summary.get("zoom_evidence"), Mapping) else {}
        motor = summary.get("motor") if isinstance(summary.get("motor"), Mapping) else {}
        print(
            "[ptz_zoom_policy_preview] "
            f"ok={summary.get('ok')} recommend={summary.get('recommend_wide_observe_zoom')} "
            f"decision={summary.get('final_decision')} target_zoom={summary.get('target_zoom')} "
            f"zoom_decision={zoom.get('decision')} best_window={zoom.get('best_window_min')}min "
            f"helpful_rate={zoom.get('wide_helpful_rate')} context_gain_rate={zoom.get('wide_context_gain_rate')} "
            f"motor_eligible={motor.get('eligible')} motor_reason={motor.get('reason')} "
            f"quality={motor.get('quality')} target_conf={motor.get('target_conf')} obs_conf={motor.get('obs_conf')} "
            f"reason={summary.get('reason')}",
            flush=True,
        )
    return 0 if bool(summary.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

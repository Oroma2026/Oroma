#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/ptz_motor_state.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
#            Offline-First · Headless · SQLite-First · Edge Runtime
# Modul:     PTZ Motor State Reader – read-only Status-/Attention-Snapshot
# Version:   v3.8.0-ptz-v1.5c
# Stand:     2026-05-16
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / SYSTEMROLLE
# ───────────────────
# Dieses Modul ist die zentrale, wiederverwendbare Leseschicht für den
# PTZ Motor Worker. Der Worker selbst ist der schnelle Servo-/Reflexpfad für die
# Kameraausrichtung. Dieses Modul greift NICHT in die Motorik ein, sondern liest
# ausschließlich den aktuellen Zustand und bereitet ihn für UI, Diagnose und
# spätere ORÓMA-Attention-/SnapChain-Verarbeitung auf.
#
# Warum ein eigenes Core-Modul?
#   - Die Video-UI soll keine eigene Fachlogik für PTZ-Attention ansammeln.
#   - Spätere Module (SnapChain, Attention, Learning, Explainability) sollen die
#     gleiche JSON-sichere Quelle nutzen können.
#   - Der Worker bleibt manuell kontrolliert und systemd-disabled-by-default.
#   - Flask/UI bekommt keine Schreibrechte auf systemd und startet/stoppt den
#     Worker nicht.
#
# SICHERHEITS- UND PRODUKTIONSINVARIANTEN
# ───────────────────────────────────────
# - Read-only: keine DB-Zugriffe, keine DB-Writes, keine systemd-Schreibbefehle.
# - Kein sudo: systemctl wird nur für Statusabfragen ohne sudo verwendet.
# - Keine GUI-Abhängigkeiten: headless-tauglich, kein Qt/Wayland/X11.
# - JSON-safe: zirkuläre Referenzen werden sichtbar gekappt, statt Flask/API zu
#   crashen. Das Modul verändert keine Originalobjekte in-place.
# - Stale-safe: alte ptz_motor_state.json-Werte werden markiert, damit dx/dy,
#   reason oder action nach gestopptem Worker nicht wie aktive Motorik wirken.
# - Fail-soft: defekte/fehlende Dateien oder systemctl-Fehler werden als
#   strukturierte Fehlerfelder zurückgegeben, nicht als ungefangene Exception.
#
# ARCHITEKTURKONTEXT
# ──────────────────
# Aktuelle PTZ-Pipeline (v1.5c):
#   Motion Field → Motion Candidate + Eye-Pair/Face-like Salience Candidate
#   → Local-Motion Gate → Temporal Gate → Candidate Scoring
#   → Target Smoothing/Hold → Servo-Damping/Micro-Guard → Axis Lock
#   → Servo Decision → PTZ Command
#
# Dieses Modul bildet daraus eine lesbare Attention-Sicht:
#   - candidate:      stärkster aktueller Bewegungs-/Zielkandidat
#   - candidates:     optionale Candidate-Liste, z.B. Motion + Eye-Pair
#   - eye_pair:       read-only Konfiguration/Status der weichen Augenpaar-Heuristik
#   - face_region:    Expected-Face-Region-/Head-Context-Bonus ohne Face Detection
#   - target:         geglättetes Ziel inkl. Confidence, Alter, Hold und Update-Art
#   - axis_lock:      kurzzeitige Achsenbindung zur Beruhigung der Motorik
#   - vector:         dx/dy/dist/energy des aktuell verwendeten Signals
#   - counters:       frames/moves/fails/stability_waits/target_hold/axis_lock
#   - quality:        fresh/stale/stopped/error für UI und spätere Snap-Auswertung
#
# UMGEBUNGSVARIABLEN
# ──────────────────
#   OROMA_BASE
#     Basisverzeichnis; Default: /opt/ai/oroma
#
#   OROMA_PTZ_MOTOR_UNIT
#     systemd Unitname; Default: oroma-ptz-motor-worker.service
#
#   OROMA_PTZ_MOTOR_STATE_PATH
#     State-JSON des Workers; Default:
#     /opt/ai/oroma/data/state/ptz_motor_state.json
#
#   OROMA_PTZ_MOTOR_LOG_OUT / OROMA_PTZ_MOTOR_LOG_ERR
#     Worker-Logs; Defaults unter /opt/ai/oroma/logs/
#
#   OROMA_PTZ_MOTOR_UI_STALE_SEC
#     Schwelle für stale State; Default: 5.0 Sekunden
#
# ÖFFENTLICHE FUNKTIONEN
# ──────────────────────
#   read_ptz_motor_state(...)
#     Liest ptz_motor_state.json und annotiert Alter/Staleness.
#
#   build_ptz_motor_attention_snapshot(state)
#     Erzeugt eine kompakte, JSON-sichere Attention-Sicht aus dem Worker-State.
#
#   read_ptz_motor_status(include_logs=False)
#     Kombiniert systemctl-Read-Only-Status, State, Attention-Snapshot und optional
#     Log-Tails. Diese Funktion ist die bevorzugte Quelle für die Video-UI.
#
# NICHT-ZIELE
# ───────────
# - Kein Start/Stop/Restart/Enable/Disable.
# - Keine Policy- oder Lernlogik.
# - Keine Interpretation als Personenerkennung oder Identifikation.
# - Kein direkter DB-/DBWriter-Zugriff.
#
# =============================================================================

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from typing import Any, Dict, List, Optional


DEFAULT_BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
DEFAULT_STATE_DIR = os.path.join(DEFAULT_BASE, "data", "state")
DEFAULT_LOG_DIR = os.path.join(DEFAULT_BASE, "logs")

DEFAULT_UNIT = os.environ.get("OROMA_PTZ_MOTOR_UNIT", "oroma-ptz-motor-worker.service")
DEFAULT_STATE_PATH = os.environ.get(
    "OROMA_PTZ_MOTOR_STATE_PATH",
    os.path.join(DEFAULT_STATE_DIR, "ptz_motor_state.json"),
)
DEFAULT_LOG_OUT = os.environ.get(
    "OROMA_PTZ_MOTOR_LOG_OUT",
    os.path.join(DEFAULT_LOG_DIR, "ptz_motor_worker.out.log"),
)
DEFAULT_LOG_ERR = os.environ.get(
    "OROMA_PTZ_MOTOR_LOG_ERR",
    os.path.join(DEFAULT_LOG_DIR, "ptz_motor_worker.err.log"),
)
DEFAULT_STALE_SEC = float(os.environ.get("OROMA_PTZ_MOTOR_UI_STALE_SEC", "5.0"))


def _as_float(value: Any, default: float = 0.0) -> float:
    """Return a finite float; invalid/non-finite values become default."""
    try:
        n = float(value)
        if math.isfinite(n):
            return n
    except Exception:
        pass
    return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    """Return an int from noisy JSON/systemd values; invalid values become default."""
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return int(value)
        text = str(value).strip()
        if not text:
            return int(default)
        return int(float(text))
    except Exception:
        return int(default)


def _as_bool(value: Any, default: bool = False) -> bool:
    """Return a robust boolean for JSON/ENV-style values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def json_safe_obj(value: Any, _seen: Optional[set] = None) -> Any:
    """Return a JSON-serialisable copy and visibly cut circular references.

    Flask's jsonify/json.dumps fails hard on circular references. The PTZ status
    path is diagnostic infrastructure and must not produce HTTP 500 just because
    a future state extension accidentally contains a recursive object. This
    helper keeps primitive JSON values, recursively copies containers and turns
    unknown objects into strings.
    """
    if _seen is None:
        _seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    obj_id = id(value)
    if obj_id in _seen:
        return "<circular-reference>"

    if isinstance(value, dict):
        _seen.add(obj_id)
        out: Dict[str, Any] = {}
        for key, item in value.items():
            try:
                out[str(key)] = json_safe_obj(item, _seen)
            except Exception as exc:
                out[str(key)] = f"<json-safe-error:{type(exc).__name__}>"
        _seen.discard(obj_id)
        return out

    if isinstance(value, (list, tuple, set)):
        _seen.add(obj_id)
        out_list = [json_safe_obj(item, _seen) for item in list(value)]
        _seen.discard(obj_id)
        return out_list

    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


def safe_tail_lines(path: str, limit: int = 20, max_bytes: int = 65536) -> Dict[str, Any]:
    """Read a small log tail without blocking or raising into callers."""
    out: Dict[str, Any] = {"exists": False, "path": str(path or ""), "lines": [], "error": ""}
    try:
        if not path or not os.path.exists(path):
            return out
        out["exists"] = True
        with open(path, "rb") as fh:
            try:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - int(max_bytes)), os.SEEK_SET)
            except Exception:
                pass
            data = fh.read(int(max_bytes))
        text = data.decode("utf-8", "replace")
        lines = [line for line in text.splitlines() if line.strip()]
        out["lines"] = lines[-max(1, int(limit)):]
    except Exception as exc:
        out["error"] = str(exc)
    return out


def read_ptz_motor_state(
    path: Optional[str] = None,
    stale_sec: Optional[float] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Read ptz_motor_state.json best-effort and annotate age/staleness.

    The returned object is JSON-safe and always contains at least:
      exists, path, ok, error, state_age_sec, state_stale
    """
    state_path = path or DEFAULT_STATE_PATH
    stale_threshold = DEFAULT_STALE_SEC if stale_sec is None else float(stale_sec)
    current_ts = time.time() if now is None else float(now)

    payload: Dict[str, Any] = {
        "exists": False,
        "path": state_path,
        "ok": False,
        "error": "",
        "state_age_sec": None,
        "state_stale": True,
    }

    try:
        if not state_path or not os.path.exists(state_path):
            payload["error"] = "state file missing"
            return payload

        payload["exists"] = True
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        if not isinstance(data, dict):
            payload["error"] = "state json is not an object"
            return payload

        payload.update(json_safe_obj(data))
        payload["path"] = state_path

        heartbeat = payload.get("heartbeat_ts") or payload.get("ts") or 0
        heartbeat_float = _as_float(heartbeat, 0.0)
        if heartbeat_float > 0:
            age = max(0.0, current_ts - heartbeat_float)
            payload["state_age_sec"] = round(float(age), 3)
            payload["state_stale"] = bool(age > stale_threshold)
        else:
            payload["state_age_sec"] = None
            payload["state_stale"] = True

        payload["ok"] = bool(payload.get("ok", False))
        if bool(payload.get("stopped", False)):
            payload["state_stale"] = True
    except Exception as exc:
        payload["error"] = str(exc)
        payload["state_stale"] = True

    return json_safe_obj(payload)


def build_ptz_motor_attention_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    """Create a compact read-only Attention snapshot from raw worker state.

    This is the future-facing interface for UI, SnapChain and learning modules.
    It intentionally contains no systemd control and no DB behavior.
    """
    st = state if isinstance(state, dict) else {}
    counters = st.get("counters") if isinstance(st.get("counters"), dict) else {}
    candidate = st.get("candidate") if isinstance(st.get("candidate"), dict) else {}
    candidates = st.get("candidates") if isinstance(st.get("candidates"), list) else []

    active_quality = "fresh"
    if not st.get("exists"):
        active_quality = "missing"
    elif st.get("error"):
        active_quality = "error"
    elif _as_bool(st.get("stopped"), False):
        active_quality = "stopped"
    elif _as_bool(st.get("state_stale"), False):
        active_quality = "stale"

    target = {
        "dx": _as_float(st.get("target_dx"), _as_float(st.get("dx"), 0.0)),
        "dy": _as_float(st.get("target_dy"), _as_float(st.get("dy"), 0.0)),
        "confidence": _as_float(st.get("target_conf"), 0.0),
        "age_ticks": _as_int(st.get("target_age_ticks"), 0),
        "hold_active": _as_bool(st.get("target_hold_active"), False),
        "update": str(st.get("target_update") or ""),
        "last_qualified_reason": str(st.get("target_last_qualified_reason") or ""),
        "last_qualified_kind": str(st.get("target_last_qualified_kind") or ""),
        "last_qualified_source": str(st.get("target_last_qualified_source") or ""),
        "eye_hold_bias_enabled": _as_bool(st.get("eye_hold_bias_enabled"), False),
        "eye_hold_bias_active": _as_bool(st.get("eye_hold_bias_active"), False),
        "eye_hold_command_active": _as_bool(st.get("eye_hold_command_active"), False),
        "eye_hold_ticks": _as_int(st.get("eye_hold_ticks"), 0),
        "eye_hold_conf_min": _as_float(st.get("eye_hold_conf_min"), 0.0),
        "eye_hold_override_ratio": _as_float(st.get("eye_hold_override_ratio"), 0.0),
        "eye_hold_command": _as_bool(st.get("eye_hold_command"), False),
    }

    eye_pair = {
        "enabled": _as_bool(st.get("eye_pair_enabled"), False),
        "require_motion": _as_bool(st.get("eye_pair_require_motion"), True),
        "min_conf": _as_float(st.get("eye_pair_min_conf"), 0.0),
        "score_gain": _as_float(st.get("eye_pair_score_gain"), 0.0),
        "max_angle_deg": _as_float(st.get("eye_pair_max_angle_deg"), 0.0),
        "min_sep": _as_float(st.get("eye_pair_min_sep"), 0.0),
        "max_sep": _as_float(st.get("eye_pair_max_sep"), 0.0),
        "motion_radius": _as_float(st.get("eye_pair_motion_radius"), 0.0),
        "face_radius_boost": _as_float(st.get("eye_face_radius_boost"), 1.0),
        "face_radius_boost_min": _as_float(st.get("eye_face_radius_boost_min"), 0.0),
        "min_frames_stable": _as_int(st.get("eye_pair_min_frames_stable"), 0),
        "stable_radius": _as_float(st.get("eye_pair_stable_radius"), 0.0),
        "stable_count": _as_int(st.get("eye_pair_stable_count"), 0),
        "last": json_safe_obj(st.get("eye_pair_last") if isinstance(st.get("eye_pair_last"), dict) else {}),
        "candidate_count": _as_int(counters.get("eye_pair_candidates"), 0),
        "selected_count": _as_int(counters.get("eye_pair_selected"), 0),
        "raw_count": _as_int(counters.get("eye_pair_raw"), 0),
        "geom_ok_count": _as_int(counters.get("eye_pair_geom_ok"), 0),
        "motion_gated_count": _as_int(counters.get("eye_pair_motion_gated"), 0),
        "temporal_gated_count": _as_int(counters.get("eye_pair_temporal_gated"), 0),
        "rejected_motion_count": _as_int(counters.get("eye_pair_rejected_motion"), 0),
        "rejected_temporal_count": _as_int(counters.get("eye_pair_rejected_temporal"), 0),
        "rejected_geometry_count": _as_int(counters.get("eye_pair_rejected_geometry"), 0),
    }

    face_region = {
        "enabled": _as_bool(st.get("face_region_enabled"), False),
        "bonus": _as_float(st.get("face_region_bonus"), 0.0),
        "min_score": _as_float(st.get("face_region_min_score"), 0.0),
        "min_std": _as_float(st.get("face_region_min_std"), 0.0),
        "horiz_max": _as_float(st.get("face_region_horiz_max"), 0.0),
        "grad_min": _as_float(st.get("face_region_grad_min"), 0.0),
        "eye_face_rank_threshold": _as_float(st.get("eye_face_rank_threshold"), 0.0),
        "checked_count": _as_int(counters.get("face_region_checked"), 0),
        "ok_count": _as_int(counters.get("face_region_ok"), 0),
        "bonus_count": _as_int(counters.get("face_region_bonus"), 0),
        "last": json_safe_obj((st.get("eye_pair_last") or {}).get("face_region") if isinstance(st.get("eye_pair_last"), dict) else {}),
    }

    servo = {
        "move_cooldown_ticks": _as_int(st.get("move_cooldown_ticks"), 0),
        "move_cooldown_remaining": _as_int(st.get("move_cooldown_remaining"), 0),
        "move_cooldown_active": _as_bool(st.get("move_cooldown_active"), False),
        "move_cooldown_bypass": _as_bool(st.get("move_cooldown_bypass"), False),
        "move_cooldown_blocks": _as_int(counters.get("move_cooldown_blocks"), 0),
        "move_cooldown_bypass_count": _as_int(counters.get("move_cooldown_bypass"), 0),
        "micro_guard_enabled": _as_bool(st.get("micro_guard_enabled"), False),
        "micro_guard_active": _as_bool(st.get("micro_guard_active"), False),
        "micro_guard_dist_factor": _as_float(st.get("micro_guard_dist_factor"), 0.0),
        "micro_guard_conf_max": _as_float(st.get("micro_guard_conf_max"), 0.0),
        "micro_guard_blocks": _as_int(counters.get("micro_guard_blocks"), 0),
    }

    axis_lock = {
        "enabled": _as_bool(st.get("axis_lock_enabled"), False),
        "active": _as_bool(st.get("axis_lock_active"), False),
        "axis": str(st.get("axis_lock_axis") or "-"),
        "until_tick": _as_int(st.get("axis_lock_until_tick"), 0),
        "ticks": _as_int(st.get("axis_lock_ticks"), 0),
        "reason": str(st.get("axis_lock_reason") or ""),
        "override_ratio": _as_float(st.get("axis_lock_override_ratio"), 0.0),
    }

    vector = {
        "dx": _as_float(st.get("dx"), 0.0),
        "dy": _as_float(st.get("dy"), 0.0),
        "dx_raw": _as_float(st.get("dx_raw"), 0.0),
        "dy_raw": _as_float(st.get("dy_raw"), 0.0),
        "dist": _as_float(st.get("dist"), 0.0),
        "dist_raw": _as_float(st.get("dist_raw"), 0.0),
        "energy": _as_float(st.get("energy"), 0.0),
        "energy_weighted": _as_float(st.get("energy_weighted"), 0.0),
    }

    snapshot: Dict[str, Any] = {
        "ok": bool(st.get("exists") and not st.get("error")),
        "quality": active_quality,
        "state_stale": _as_bool(st.get("state_stale"), True),
        "state_age_sec": st.get("state_age_sec"),
        "heartbeat_ts": _as_float(st.get("heartbeat_ts") or st.get("ts"), 0.0),
        "pid": _as_int(st.get("pid"), 0),
        "device": str(st.get("device") or ""),
        "frame_source": str(st.get("frame_source") or ""),
        "frame_age_sec": st.get("frame_age_sec"),
        "target_mode": str(st.get("target_mode") or ""),
        "reason": str(st.get("reason") or ""),
        "action": str(st.get("action") or st.get("mapped_action") or ""),
        "raw_action": str(st.get("raw_action") or ""),
        "mapped_action": str(st.get("mapped_action") or ""),
        "axis": str(st.get("axis") or "-"),
        "cmd_ok": st.get("cmd_ok"),
        "cmd_error": str(st.get("cmd_error") or ""),
        "vector": vector,
        "candidate": json_safe_obj(candidate),
        "candidates": json_safe_obj(candidates),
        "candidate_source": str(st.get("candidate_source") or candidate.get("source") or ""),
        "candidate_winner": str(st.get("candidate_winner") or candidate.get("candidate_winner") or candidate.get("kind") or ""),
        "eye_pair": eye_pair,
        "face_region": face_region,
        "target": target,
        "servo": servo,
        "axis_lock": axis_lock,
        "counters": json_safe_obj(counters),
    }
    return json_safe_obj(snapshot)


def ptz_motor_attempt_record(cmd: List[str], rc: int, out: str = "", err: str = "", ok: bool = False) -> Dict[str, Any]:
    """Create a flat, JSON-safe systemctl attempt record."""
    return {
        "cmd": [str(item) for item in cmd],
        "rc": int(rc),
        "out": str(out or "").strip(),
        "err": str(err or "").strip(),
        "ok": bool(ok),
    }


def run_systemctl_for_motor(args: List[str], timeout_sec: float = 4.0) -> Dict[str, Any]:
    """Run read-only systemctl status calls for the motor unit.

    Only use this for status commands such as show/is-active/is-enabled. Runtime
    control remains a manual root/sudoer action on the Pi console.
    """
    safe_args = [str(arg) for arg in args if str(arg).strip()]
    if not safe_args:
        return {"ok": False, "rc": -1, "out": "", "err": "missing systemctl args", "cmd": [], "attempts": []}

    cmd = ["/usr/bin/systemctl"] + safe_args
    attempts: List[Dict[str, Any]] = []
    try:
        cp = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=float(timeout_sec),
            check=False,
        )
        attempts.append(ptz_motor_attempt_record(cmd, int(cp.returncode), cp.stdout or "", cp.stderr or "", int(cp.returncode) == 0))
    except subprocess.TimeoutExpired:
        attempts.append(ptz_motor_attempt_record(cmd, -2, "", f"timeout after {timeout_sec}s", False))
    except Exception as exc:
        attempts.append(ptz_motor_attempt_record(cmd, -3, "", str(exc), False))

    base = dict(attempts[-1]) if attempts else ptz_motor_attempt_record([], -1, "", "not executed", False)
    base["attempts"] = [dict(item) for item in attempts]
    return json_safe_obj(base)


def parse_systemctl_show_output(text: str) -> Dict[str, str]:
    """Parse `systemctl show` key=value output into a string dictionary."""
    props: Dict[str, str] = {}
    for line in str(text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[str(key)] = str(value)
    return props


def read_ptz_motor_status(
    include_logs: bool = False,
    unit: Optional[str] = None,
    state_path: Optional[str] = None,
    log_out: Optional[str] = None,
    log_err: Optional[str] = None,
    stale_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a complete read-only status payload for the PTZ Motor Worker."""
    unit_name = unit or DEFAULT_UNIT
    out_path = log_out or DEFAULT_LOG_OUT
    err_path = log_err or DEFAULT_LOG_ERR

    active = run_systemctl_for_motor(["is-active", unit_name], timeout_sec=2.0)
    enabled = run_systemctl_for_motor(["is-enabled", unit_name], timeout_sec=2.0)
    show = run_systemctl_for_motor([
        "show",
        unit_name,
        "--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus,Result,UnitFileState,FragmentPath",
        "--no-page",
    ], timeout_sec=2.5)

    props = parse_systemctl_show_output(str(show.get("out") or "")) if show.get("ok") else {}
    state = read_ptz_motor_state(path=state_path, stale_sec=stale_sec)
    attention = build_ptz_motor_attention_snapshot(state)

    show_text = str(show.get("out") or show.get("err") or "")
    unit_exists = props.get("LoadState") not in ("", "not-found") if props else ("not-found" not in show_text)
    active_state = props.get("ActiveState") or (str(active.get("out") or "").strip() if active.get("out") else "unknown")
    enabled_state = props.get("UnitFileState") or (str(enabled.get("out") or "").strip() if enabled.get("out") else "unknown")

    payload: Dict[str, Any] = {
        "ok": True,
        "unit": unit_name,
        "unit_exists": bool(unit_exists),
        "active": bool(active.get("ok") and str(active.get("out") or "").strip() == "active"),
        "active_state": active_state,
        "sub_state": props.get("SubState") or "unknown",
        "enabled": bool(enabled.get("ok") and str(enabled.get("out") or "").strip() == "enabled"),
        "unit_file_state": enabled_state,
        "main_pid": _as_int(props.get("MainPID"), 0),
        "fragment_path": props.get("FragmentPath") or "",
        "systemctl": {
            "active": active,
            "enabled": enabled,
            "show": show,
        },
        "state": state,
        "attention": attention,
        "manual_only": True,
        "can_enable_from_ui": False,
        "logs": {
            "out": out_path,
            "err": err_path,
        },
    }
    payload["heartbeat_ok"] = bool(payload["active"] and state.get("exists") and not state.get("state_stale"))

    if include_logs:
        payload["log_tail"] = {
            "out": safe_tail_lines(out_path, limit=20),
            "err": safe_tail_lines(err_path, limit=20),
        }

    return json_safe_obj(payload)

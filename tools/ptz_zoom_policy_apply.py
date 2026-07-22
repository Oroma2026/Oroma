#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_zoom_policy_apply.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Zoom Policy Apply – P3z1c Wide-Observe Auto-Zoom Gate
# Version:   v3.7.3+p3z1c-wide-observe-auto-zoom-gated-v1.0
# Stand:     2026-06-26
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool ist die erste echte, aber streng gegatete Auto-Zoom-Stufe nach
# P3z1b. Es nutzt ausschließlich die bereits geprüfte Preview-Entscheidung aus
# `tools/ptz_zoom_policy_preview.py` und setzt den Wide-Observe-Zoom nur dann,
# wenn alle Schutzbedingungen erfüllt sind.
#
# WICHTIGE INVARIANTEN
# ────────────────────
# - Nur `zoom_absolute` darf gesetzt werden; kein Pan, kein Tilt, kein Focus.
# - Keine ObjectGraph-/SnapGraph-Materialisierung, keine Writes in `oroma.db`.
# - Stats ausschließlich via DBWriter nach `stats.db.stats_points`.
# - Default ist fail-closed: ohne `--apply` UND
#   `OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1` wird kein Zoom gesetzt.
# - Rate-Limit verhindert Zoom-Hot-Loops und USB-/Motorstress.
# - Bereits vorhandener Zielzoom wird als Erfolg ohne set-ctrl behandelt.
# - Alle Entscheidungen werden in State-Datei, stdout und optional Stats sichtbar.
#
# FREIGABELOGIK
# ─────────────
# Auto-Zoom darf nur ausgeführt werden, wenn:
#   1. P3z1b `recommend_wide_observe_zoom=true` ergibt,
#   2. der CLI-Gate `--apply` gesetzt ist,
#   3. `OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1` gesetzt ist,
#   4. das Rate-Limit eingehalten ist,
#   5. PTZ-Device und `zoom_absolute` verfügbar sind,
#   6. der aktuelle Zoom nicht bereits dem Zielzoom entspricht.
#
# ENVIRONMENT
# ───────────
#   OROMA_BASE / OROMA_BASE_DIR                         Default: /opt/ai/oroma
#   OROMA_PTZ_DEVICE / OROMA_PTZ_V4L2_DEVICE             PTZ-V4L2-Device
#   OROMA_PTZ_ZOOM_AUTO_STATE_PATH                       Default: data/state/ptz_zoom_policy_apply_state.json
#   OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE                     Default: 0
#   OROMA_PTZ_ZOOM_AUTO_MIN_INTERVAL_SEC                 Default: 1800
#   OROMA_PTZ_ZOOM_AUTO_MAX_PER_HOUR                     Default: 2
#   OROMA_PTZ_ZOOM_POLICY_TARGET_ZOOM                    Default: 100
#   OROMA_PTZ_ZOOM_POLICY_DBW_TIMEOUT_MS                 Default: 10000
#
# BEISPIELE
# ─────────
# Preview/No-op, garantiert ohne Kameraänderung:
#   python3 tools/ptz_zoom_policy_apply.py --once --write-stats --verbose
#
# Echter Apply-Test mit Doppel-Gate:
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1 \
#     python3 tools/ptz_zoom_policy_apply.py --once --apply --write-stats --verbose
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore

try:
    from tools.ptz_zoom_policy_preview import evaluate_preview  # type: ignore
except Exception as exc:  # pragma: no cover
    evaluate_preview = None  # type: ignore
    _PREVIEW_IMPORT_ERROR = str(exc)
else:
    _PREVIEW_IMPORT_ERROR = ""

try:
    from wrappers.ptz_controller import PTZController  # type: ignore
except Exception as exc:  # pragma: no cover
    PTZController = None  # type: ignore
    _PTZ_IMPORT_ERROR = str(exc)
else:
    _PTZ_IMPORT_ERROR = ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        value = int(float(str(os.environ.get(name, str(default))).strip()))
    except Exception:
        value = int(default)
    if lo is not None:
        value = max(int(lo), value)
    if hi is not None:
        value = min(int(hi), value)
    return int(value)


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


def _state_path() -> Path:
    default = os.path.join(BASE, "data", "state", "ptz_zoom_policy_apply_state.json")
    return Path(os.environ.get("OROMA_PTZ_ZOOM_AUTO_STATE_PATH", default))


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _default_device() -> str:
    by_id = "/dev/v4l/by-id/usb-EMEET_EMEET_PIXY_A250607001103370-video-index0"
    if os.path.exists(by_id):
        return by_id
    return "/dev/video0"


def _device() -> str:
    return os.environ.get("OROMA_PTZ_DEVICE") or os.environ.get("OROMA_PTZ_V4L2_DEVICE") or _default_device()


def _current_zoom(status: Mapping[str, Any]) -> Optional[int]:
    controls = status.get("controls") if isinstance(status.get("controls"), Mapping) else {}
    zoom = controls.get("zoom_absolute") if isinstance(controls.get("zoom_absolute"), Mapping) else {}
    if "value" not in zoom:
        return None
    return _safe_int(zoom.get("value"), 0)


def _rate_limit_ok(previous: Mapping[str, Any], now: int) -> Tuple[bool, list[str]]:
    reasons: list[str] = []
    min_interval = _env_int("OROMA_PTZ_ZOOM_AUTO_MIN_INTERVAL_SEC", 1800, lo=0, hi=86400)
    max_per_hour = _env_int("OROMA_PTZ_ZOOM_AUTO_MAX_PER_HOUR", 2, lo=0, hi=1000)
    last_apply_ts = _safe_int(previous.get("last_apply_ts"), 0)
    if last_apply_ts > 0 and (now - last_apply_ts) < min_interval:
        reasons.append(f"rate_limit_interval:{now-last_apply_ts}<{min_interval}")
    hist = previous.get("history") if isinstance(previous.get("history"), list) else []
    applied_hour = 0
    for item in hist:
        if not isinstance(item, Mapping):
            continue
        if bool(item.get("applied")) and _safe_int(item.get("ts"), 0) >= now - 3600:
            applied_hour += 1
    if max_per_hour > 0 and applied_hour >= max_per_hour:
        reasons.append(f"rate_limit_hour:{applied_hour}>={max_per_hour}")
    return (len(reasons) == 0), reasons


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
            resp = client.request(op="ping", timeout_ms=1000, expect="none", tag="ptz.zoom_policy_apply.ping")
            if bool(resp.get("ok")):
                return
        except Exception:
            pass
    raise RuntimeError("DBWriter required but not available/enabled")


def _append_history(previous: Mapping[str, Any], item: Mapping[str, Any], max_n: int = 288) -> list[Dict[str, Any]]:
    hist = previous.get("history") if isinstance(previous.get("history"), list) else []
    out = [dict(h) for h in hist if isinstance(h, Mapping)]
    out.append({
        "ts": _safe_int(item.get("ts"), int(time.time())),
        "enabled": bool(item.get("enabled")),
        "cli_apply": bool(item.get("cli_apply")),
        "preview_recommend": bool(item.get("preview_recommend")),
        "apply_allowed": bool(item.get("apply_allowed")),
        "applied": bool(item.get("applied")),
        "already_at_target": bool(item.get("already_at_target")),
        "current_zoom": item.get("current_zoom"),
        "target_zoom": item.get("target_zoom"),
        "final_decision": item.get("final_decision"),
        "motor_reason": item.get("motor_reason"),
        "reason": item.get("reason"),
    })
    return out[-int(max_n):]


def evaluate_and_maybe_apply(cli_apply: bool = False) -> Dict[str, Any]:
    now = int(time.time())
    previous = _read_json(_state_path())
    errors: list[str] = []
    reasons: list[str] = []

    enabled = _env_bool("OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE", False)
    target_zoom = _env_int("OROMA_PTZ_ZOOM_POLICY_TARGET_ZOOM", 100, lo=1, hi=10000)
    device = _device()

    if evaluate_preview is None:
        errors.append(f"preview_import_error:{_PREVIEW_IMPORT_ERROR}")
        preview: Dict[str, Any] = {}
    else:
        try:
            preview = evaluate_preview()
        except Exception as exc:
            preview = {}
            errors.append(f"preview_error:{exc}")

    preview_recommend = bool(preview.get("recommend_wide_observe_zoom"))
    if preview_recommend:
        reasons.append("preview_recommend_wide:true")
    else:
        reasons.append("preview_recommend_wide:false")

    if not cli_apply:
        reasons.append("cli_apply_gate_closed")
    if not enabled:
        reasons.append("env_apply_gate_closed")

    rate_ok, rate_reasons = _rate_limit_ok(previous, now)
    reasons.extend(rate_reasons)

    current_zoom: Optional[int] = None
    status: Dict[str, Any] = {}
    set_ok = False
    already_at_target = False
    controller_ready = False
    controller_error = ""

    if PTZController is None:
        errors.append(f"ptz_controller_import_error:{_PTZ_IMPORT_ERROR}")
    else:
        try:
            ctrl = PTZController(device)
            status = _json_safe(ctrl.status())
            current_zoom = _current_zoom(status)
            if current_zoom is None:
                reasons.append("current_zoom_unavailable")
            else:
                controller_ready = bool(status.get("supported", False))
                if not controller_ready:
                    reasons.append("ptz_not_supported")
                if int(current_zoom) == int(target_zoom):
                    already_at_target = True
                    reasons.append("already_at_target_zoom")
            controller_error = str(status.get("last_error") or "")
        except Exception as exc:
            errors.append(f"ptz_status_error:{exc}")

    apply_allowed = bool(not errors and preview_recommend and cli_apply and enabled and rate_ok and controller_ready)
    if already_at_target:
        # No set-ctrl needed. This is a safe terminal state, not an application.
        apply_allowed = False

    applied = False
    final_decision = "hold_current_zoom"
    if apply_allowed:
        try:
            ctrl = PTZController(device) if PTZController is not None else None
            if ctrl is None:
                raise RuntimeError("PTZController unavailable")
            set_ok = bool(ctrl.set_absolute(zoom=int(target_zoom)))
            status = _json_safe(ctrl.status())
            current_zoom_after = _current_zoom(status)
            applied = bool(set_ok and current_zoom_after == int(target_zoom))
            current_zoom = current_zoom_after if current_zoom_after is not None else current_zoom
            if applied:
                final_decision = "applied_wide_observe_zoom"
                reasons.append("zoom_set_ok")
            else:
                final_decision = "apply_failed"
                controller_error = str(status.get("last_error") or controller_error or "set_absolute_failed")
                errors.append(f"zoom_set_failed:{controller_error}")
        except Exception as exc:
            final_decision = "apply_failed"
            errors.append(f"zoom_set_exception:{exc}")
    elif already_at_target and preview_recommend:
        final_decision = "already_at_wide_observe_zoom"
    elif preview_recommend and (not cli_apply or not enabled):
        final_decision = "would_apply_but_gate_closed"
    elif preview_recommend and not rate_ok:
        final_decision = "would_apply_but_rate_limited"

    motor = preview.get("motor") if isinstance(preview.get("motor"), Mapping) else {}
    zoom_evidence = preview.get("zoom_evidence") if isinstance(preview.get("zoom_evidence"), Mapping) else {}
    reason_text = ";".join([str(r) for r in reasons + errors if str(r)])[:2500]

    out: Dict[str, Any] = {
        "ok": bool(len(errors) == 0 or (already_at_target and preview_recommend)),
        "ts": now,
        "stage": "P3z1c_wide_observe_zoom_auto_apply_gated",
        "version": "v3.7.3+p3z1c-wide-observe-auto-zoom-gated-v1.0",
        "enabled": bool(enabled),
        "cli_apply": bool(cli_apply),
        "preview_recommend": bool(preview_recommend),
        "apply_allowed": bool(apply_allowed),
        "applied": bool(applied),
        "already_at_target": bool(already_at_target),
        "controller_ready": bool(controller_ready),
        "final_decision": final_decision,
        "target_zoom": int(target_zoom),
        "current_zoom": current_zoom,
        "device": device,
        "reason": reason_text,
        "errors": errors,
        "rate_limit_ok": bool(rate_ok),
        "controller_error": controller_error,
        "preview": {
            "final_decision": preview.get("final_decision"),
            "reason": preview.get("reason"),
            "recommend_wide_observe_zoom": preview_recommend,
            "zoom_evidence": zoom_evidence,
            "motor": motor,
        },
        "policy": {
            "mode": "gated_auto_zoom_only",
            "no_pan_tilt": True,
            "no_focus": True,
            "no_materialization": True,
            "requires_cli_apply": True,
            "requires_env_enable": True,
            "env_enable_name": "OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE",
        },
    }

    hist = _append_history(previous, {
        **out,
        "motor_reason": motor.get("reason"),
    })
    state_doc = {
        "version": 1,
        "updated_ts": now,
        "stage": out["stage"],
        "last": out,
        "history": hist,
        "last_apply_ts": now if applied else _safe_int(previous.get("last_apply_ts"), 0),
        "note": "P3z1c is gated auto-zoom only. It never pans/tilts and only applies when --apply plus OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1 are both present.",
    }
    _atomic_write_json(_state_path(), state_doc)
    return out


def _metric_rows(summary: Mapping[str, Any]) -> Iterable[Tuple[str, float, Dict[str, Any]]]:
    preview = summary.get("preview") if isinstance(summary.get("preview"), Mapping) else {}
    motor = preview.get("motor") if isinstance(preview.get("motor"), Mapping) else {}
    zoom = preview.get("zoom_evidence") if isinstance(preview.get("zoom_evidence"), Mapping) else {}
    common = {
        "stage": summary.get("stage"),
        "final_decision": summary.get("final_decision"),
        "enabled": bool(summary.get("enabled")),
        "cli_apply": bool(summary.get("cli_apply")),
        "preview_recommend": bool(summary.get("preview_recommend")),
        "apply_allowed": bool(summary.get("apply_allowed")),
        "applied": bool(summary.get("applied")),
        "already_at_target": bool(summary.get("already_at_target")),
        "controller_ready": bool(summary.get("controller_ready")),
        "target_zoom": summary.get("target_zoom"),
        "current_zoom": summary.get("current_zoom"),
        "reason": summary.get("reason"),
        "errors": summary.get("errors") or [],
        "motor": motor,
        "zoom_evidence": zoom,
    }
    yield "ptz.zoom_policy.apply.preview_recommend", 1.0 if bool(summary.get("preview_recommend")) else 0.0, dict(common)
    yield "ptz.zoom_policy.apply.apply_allowed", 1.0 if bool(summary.get("apply_allowed")) else 0.0, dict(common)
    yield "ptz.zoom_policy.apply.applied", 1.0 if bool(summary.get("applied")) else 0.0, dict(common)
    yield "ptz.zoom_policy.apply.already_at_target", 1.0 if bool(summary.get("already_at_target")) else 0.0, dict(common)
    yield "ptz.zoom_policy.apply.enabled", 1.0 if bool(summary.get("enabled")) else 0.0, dict(common)
    yield "ptz.zoom_policy.apply.target_zoom", _safe_float(summary.get("target_zoom"), 0.0), dict(common)
    yield "ptz.zoom_policy.apply.current_zoom", _safe_float(summary.get("current_zoom"), 0.0), dict(common)


def write_stats(summary: Mapping[str, Any]) -> int:
    _dbw_required()
    ts = _safe_int(summary.get("ts"), int(time.time()))
    written = 0
    for series, value, meta in _metric_rows(summary):
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        src_uid = f"ptz_zoom_policy_apply:{ts}:{series}"
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
            params=[int(ts), str(series), float(value), "ptz_zoom_policy_apply", 0, meta_json, src_uid],
            tag="ptz.zoom_policy_apply.stats_points.upsert",
            priority="low",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
        written += 1
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ORÓMA PTZ Zoom Policy Apply – P3z1c gated auto-zoom")
    p.add_argument("--once", action="store_true", help="Einmal auswerten und beenden.")
    p.add_argument("--apply", action="store_true", help="CLI-Gate für echtes Setzen von zoom_absolute. Zusätzlich ist OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1 nötig.")
    p.add_argument("--write-stats", action="store_true", help="Apply-/No-op-Metriken via DBWriter in stats.db schreiben.")
    p.add_argument("--json", action="store_true", help="JSON ausgeben.")
    p.add_argument("--verbose", action="store_true", help="Kompakte Diagnosezeile ausgeben.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = evaluate_and_maybe_apply(cli_apply=bool(args.apply))
    if args.write_stats:
        try:
            summary["stats_written"] = write_stats(summary)
        except Exception as exc:
            summary["ok"] = False
            summary.setdefault("errors", []).append(f"stats_write_error:{exc}")
            print(f"[ptz_zoom_policy_apply] stats_write_error={exc}", file=sys.stderr, flush=True)
            prev = _read_json(_state_path())
            prev["last"] = summary
            prev["updated_ts"] = _safe_int(summary.get("ts"), int(time.time()))
            _atomic_write_json(_state_path(), prev)
            return 2
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.verbose or not args.json:
        preview = summary.get("preview") if isinstance(summary.get("preview"), Mapping) else {}
        motor = preview.get("motor") if isinstance(preview.get("motor"), Mapping) else {}
        print(
            "[ptz_zoom_policy_apply] "
            f"ok={bool(summary.get('ok'))} "
            f"enabled={bool(summary.get('enabled'))} "
            f"cli_apply={bool(summary.get('cli_apply'))} "
            f"preview_recommend={bool(summary.get('preview_recommend'))} "
            f"apply_allowed={bool(summary.get('apply_allowed'))} "
            f"applied={bool(summary.get('applied'))} "
            f"already_at_target={bool(summary.get('already_at_target'))} "
            f"controller_ready={bool(summary.get('controller_ready'))} "
            f"decision={summary.get('final_decision')} "
            f"current_zoom={summary.get('current_zoom')} "
            f"target_zoom={summary.get('target_zoom')} "
            f"motor_reason={motor.get('reason')} "
            f"reason={summary.get('reason','')}",
            flush=True,
        )
    return 0 if bool(summary.get("ok", False)) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

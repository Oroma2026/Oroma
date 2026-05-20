#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_motor_reward_collector.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     PTZ Motor Reward Collector – Local Utility Slow-Loop
# Version:   v3.7.3+structured-plasticity-ptz-motor-collector-v1.2
# Stand:     2026-05-17
# Autor:     ORÓMA / ChatGPT Patch-Gate
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieser Collector verbindet den schnellen, DB-freien PTZ Motor Worker mit der
# neuen domänenfreien Utility-Schicht von ORÓMA.
#
# Der PTZ Motor Worker schreibt im Hot-Loop ausschließlich Telemetrie nach
# `data/state/ptz_motor_state.json`. Das ist absichtlich so, damit Motorik und
# Servo-Verhalten nicht durch SQLite/DBWriter/Schema-Arbeit gebremst werden.
# Dieser Collector läuft dagegen als langsamer Slow-Loop: Er liest zwei zeitlich
# getrennte State-Snapshots, berechnet daraus lokale PTZ-Nützlichkeitssignale
# und emittiert diese über `core.utility.emit(UtilitySignal(...))`.
#
# ARCHITEKTUR-ROLLE
# ─────────────────
#   ptz_motor_worker.py
#       → schneller Reflex-/Servo-Pfad, keine DB-Schreibzugriffe im Hot-Loop
#
#   ptz_motor_reward_collector.py  (dieses Modul)
#       → langsame Auswertung vorher/nachher, lokale Utility-Signale
#
#   core.utility.py
#       → domänenfreie Normalisierung, Validierung und RewardLogger-Anbindung
#
#   core.reward.py / rewards_log
#       → technische Persistenz der gewichteten Utility-Signale
#
#   core.dream_worker.py  (späterer Patch)
#       → Verdichtung dieser Utility-Signale in policy_rules / Meta-Strukturen
#
# WICHTIGE INVARIANTEN
# ────────────────────
#   - Keine Änderung an ptz_motor_worker.py.
#   - Keine direkten SQLite-Schreibzugriffe.
#   - Keine DB-Schemaänderung.
#   - Keine PTZ-Kommandos, keine Motorsteuerung, keine Kamera-/Backend-Nutzung.
#   - Keine Pruning-, Dream- oder Policy-Entscheidung in dieser Datei.
#   - Utility wird ausschließlich über core.utility.emit() geschrieben.
#   - Skip-Gründe werden domänenfrei diagnostiziert, aber ändern keine Schwellen.
#   - Stale Worker-State erzeugt KEINE Penalty-Signale.
#   - Stillstand durch deadzone/energy_low erzeugt KEINE Bewegungs-Penalties.
#   - Kontext trennt Policy-Felder sauber von Diagnose-Rohfeldern:
#       policy_action   = nur bei cmd_ok=True für späteres Motor-Policy-Lernen
#       executed_action = tatsächlich ausgeführte Worker-Aktion
#       proposed_action = berechnete/naheliegende Kandidatenaktion
#       debug           = Rohfelder für Diagnose, nicht für DreamWorker-Policy nötig
#
# GELERNTE/EMITTIERTE SIGNALQUELLEN
# ─────────────────────────────────
#   ptz_motor/center_gain
#       Positives Signal, wenn nach einem erfolgreichen PTZ-Kommando die
#       normierte Distanz zum Zielzentrum ausreichend sinkt.
#
#   ptz_motor/wasted_motion_penalty
#       Negatives Signal, wenn nach einem erfolgreichen PTZ-Kommando die Distanz
#       ausreichend steigt. Nur bei cmd_ok=True; korrektes Stillhalten wird nicht
#       bestraft.
#
#   ptz_motor/target_conf_gain
#       Signal für Änderung des geglätteten Target-Vertrauens. Positive und
#       negative Werte sind erlaubt, weil ein Target stabiler oder schlechter
#       werden kann.
#
#   ptz_motor/target_stability
#       Positives Signal, wenn Eye-Pair-/Head-Salience über mehrere Ticks stabiler
#       wird. Das ist keine Personenerkennung, sondern ein lokales
#       Stabilitäts-/Salience-Signal.
#
#   ptz_motor/cmd_fail_penalty
#       Negatives Signal, wenn der kumulative cmd_fail-Zähler steigt.
#
#   ptz_motor/reversal_penalty
#       Negatives Signal, wenn der Guarded-Reversal-Zähler steigt. Unterstützt
#       später ruhigeres Blickverhalten ohne den Hot-Loop direkt zu verändern.
#
# ENVIRONMENT
# ───────────
# Basis:
#   OROMA_BASE
#       Projektbasis. Default: /opt/ai/oroma
#
#   OROMA_PTZ_MOTOR_STATE_PATH
#       Pfad zur State-Datei des PTZ Motor Workers.
#       Default: {OROMA_BASE}/data/state/ptz_motor_state.json
#
# Aktivierung/Loop:
#   OROMA_PTZ_MOTOR_COLLECTOR_ENABLE
#       Default: 1. Bei 0/false/off beendet sich der Collector ohne Emit.
#
#   OROMA_PTZ_MOTOR_COLLECTOR_INTERVAL_SEC
#       Default: 8.0 Sekunden. Slow-Loop-Intervall für vorher/nachher-Vergleich.
#       Bewusst langsamer als der Motor-Worker-Hot-Loop.
#
#   OROMA_PTZ_MOTOR_COLLECTOR_MAX_STATE_AGE
#       Default: 15.0 Sekunden. Ältere States gelten als inaktiv/stale und werden
#       ohne Penalty übersprungen.
#
# Schwellen:
#   OROMA_PTZ_MOTOR_COLLECTOR_MIN_DIST_CHANGE
#       Default: 0.02. Mindeständerung der normierten Distanz für center_gain
#       oder wasted_motion_penalty.
#
#   OROMA_PTZ_MOTOR_COLLECTOR_MIN_CONF_CHANGE
#       Default: 0.02. Mindeständerung von target_conf für target_conf_gain.
#
#   OROMA_PTZ_MOTOR_COLLECTOR_MIN_STABILITY_DELTA
#       Default: 1. Mindestanstieg von eye_pair_stable_count.
#
#   OROMA_PTZ_MOTOR_COLLECTOR_STABILITY_MIN_COUNT
#       Default: 2. Ab diesem stabilen Count wird target_stability emittiert.
#
# Confidence:
#   OROMA_PTZ_MOTOR_COLLECTOR_CONF_FLOOR
#       Default: 0.05. Minimale Confidence für valide, aber schwache States.
#
#   OROMA_PTZ_MOTOR_COLLECTOR_CONF_CEIL
#       Default: 1.00. Maximale Confidence.
#
# DIAGNOSE
# ────────
# Bei --verbose gibt der Collector pro Vergleich zusätzlich aggregierte
# Skip-Gründe aus. Diese Diagnose ist absichtlich rein beobachtend: Sie lockert
# keine Schwellen, erzeugt keine Rewards und verändert keine Motorlogik.
#
#   no_tick_change
#       State hat sich nicht weiterentwickelt oder ist älter/gleich alt.
#
#   state_stale
#       heartbeat_ts ist älter als OROMA_PTZ_MOTOR_COLLECTOR_MAX_STATE_AGE.
#
#   cmd_not_ok
#       Bewegungs-Rewards wurden übersprungen, weil cmd_ok nicht True war.
#
#   reason_suppressed
#       Bewegungs-Rewards wurden wegen deadzone/energy_low/idle/... unterdrückt.
#
#   dist_delta_below_min
#       Distanzänderung war kleiner als OROMA_PTZ_MOTOR_COLLECTOR_MIN_DIST_CHANGE.
#
#   conf_delta_below_min
#       target_conf-Änderung war kleiner als OROMA_PTZ_MOTOR_COLLECTOR_MIN_CONF_CHANGE.
#
#   stability_below_min
#       Eye-Pair-Stabilität erfüllte Mindestcount/-delta nicht.
#
#   fail_count_unchanged
#       cmd_fail-Zähler ist nicht gestiegen.
#
#   reversal_unchanged
#       guarded_reversals/reversals-Zähler ist nicht gestiegen.
#
# START / TEST
# ────────────
# Einmaliger Vergleich zweier State-Snapshots mit Intervall:
#
#   cd /opt/ai/oroma
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/ptz_motor_reward_collector.py --once --verbose
#
# Dauerlauf manuell:
#
#   cd /opt/ai/oroma
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/ptz_motor_reward_collector.py --verbose
#
# PRODUKTIV-HINWEIS
# ─────────────────
# Dieser Collector ist absichtlich ein separater Slow-Loop. Er darf nicht in den
# PTZ Motor Worker-Hot-Path verschoben werden. Das hält die Motorik reaktionsarm
# und bewahrt ORÓMAs Single-Writer-/DBWriter-Disziplin.
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.utility import UtilitySignal, emit, get_counters  # noqa: E402

_STOP = False

_SKIP_REASON_KEYS = (
    "no_tick_change",
    "state_stale",
    "cmd_not_ok",
    "reason_suppressed",
    "dist_delta_below_min",
    "conf_delta_below_min",
    "stability_below_min",
    "fail_count_unchanged",
    "reversal_unchanged",
)


# -----------------------------------------------------------------------------
# Kleine ENV-/Typ-Helfer
# -----------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "disabled", "")


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


def _state_path(base: str) -> Path:
    return Path(os.environ.get("OROMA_PTZ_MOTOR_STATE_PATH", os.path.join(base, "data", "state", "ptz_motor_state.json")))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        pass
    return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
        if not math.isfinite(v):
            return float(lo)
        return max(float(lo), min(float(hi), v))
    except Exception:
        return float(lo)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        print(f"[ptz_motor_reward_collector] state_not_dict path={path}", file=sys.stderr, flush=True)
        return None
    except FileNotFoundError:
        print(f"[ptz_motor_reward_collector] state_missing path={path}", file=sys.stderr, flush=True)
        return None
    except json.JSONDecodeError as exc:
        print(f"[ptz_motor_reward_collector] state_json_error path={path} err={exc}", file=sys.stderr, flush=True)
        return None
    except Exception as exc:
        print(f"[ptz_motor_reward_collector] state_read_error path={path} err={exc}", file=sys.stderr, flush=True)
        return None


def _counter(state: Mapping[str, Any], name: str, default: int = 0) -> int:
    counters = state.get("counters")
    if not isinstance(counters, Mapping):
        return int(default)
    return _safe_int(counters.get(name), default)


def _heartbeat(state: Mapping[str, Any]) -> float:
    return _safe_float(state.get("heartbeat_ts") or state.get("ts"), 0.0)


def _state_age_sec(state: Mapping[str, Any], now: Optional[float] = None) -> float:
    hb = _heartbeat(state)
    if hb <= 0.0:
        return float("inf")
    return float((time.time() if now is None else now) - hb)


def _is_fresh(state: Mapping[str, Any], max_age: float, now: Optional[float] = None) -> bool:
    age = _state_age_sec(state, now=now)
    return math.isfinite(age) and age <= float(max_age)


def _ticks(state: Mapping[str, Any]) -> int:
    return _counter(state, "ticks", _safe_int(state.get("tick"), 0))


def _same_or_older(prev: Mapping[str, Any], curr: Mapping[str, Any]) -> bool:
    prev_hb = _heartbeat(prev)
    curr_hb = _heartbeat(curr)
    prev_ticks = _ticks(prev)
    curr_ticks = _ticks(curr)
    if curr_hb > 0 and prev_hb > 0 and curr_hb <= prev_hb:
        return True
    if curr_ticks > 0 and prev_ticks > 0 and curr_ticks <= prev_ticks:
        return True
    return False


def _confidence_from_state(state: Mapping[str, Any], floor: float, ceil: float) -> float:
    candidates = [
        _safe_float(state.get("target_conf"), 0.0),
        _safe_float(state.get("obs_conf"), 0.0),
        _safe_float(state.get("energy"), 0.0),
        _safe_float(state.get("strength"), 0.0),
    ]
    value = max(candidates) if candidates else 0.0
    return _clamp(value, floor, ceil)


def _first_non_empty_str(*values: Any) -> str:
    """Gibt den ersten nichtleeren String aus einer Kandidatenliste zurück."""

    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _action_context(prev: Mapping[str, Any], curr: Mapping[str, Any]) -> Dict[str, Any]:
    """Baut den domänenspezifischen, aber DreamWorker-freundlichen Kontext.

    Die oberste Ebene enthält nur die Felder, die spätere Aggregatoren wirklich
    stabil lesen sollen. Roh- und Diagnosefelder liegen unter ``debug``. Dadurch
    kann ein DreamWorker später einfach ``context["policy_action"]`` prüfen, ohne
    alle Worker-internen Aktionsvarianten kennen zu müssen.
    """

    prev_counters = prev.get("counters") if isinstance(prev.get("counters"), Mapping) else {}
    curr_counters = curr.get("counters") if isinstance(curr.get("counters"), Mapping) else {}

    reason = str(curr.get("reason") or curr.get("last_reason") or "")
    executed_action = _first_non_empty_str(curr.get("action"), curr.get("last_action"))
    proposed_action = _first_non_empty_str(
        curr.get("mapped_action"),
        curr.get("raw_action"),
        curr.get("obs_mapped_action"),
        curr.get("obs_action"),
    )
    policy_action = executed_action if curr.get("cmd_ok") is True else ""

    return {
        # Kompatibilität: action bleibt das tatsächlich ausgeführte Worker-Feld.
        # Für neues Dream-/Policy-Lernen ist policy_action maßgeblich.
        "action": executed_action,
        "policy_action": policy_action,
        "executed_action": executed_action,
        "proposed_action": proposed_action,
        "reason": reason,
        "cmd_ok": curr.get("cmd_ok"),
        "before_dist": _safe_float(prev.get("dist"), 0.0),
        "after_dist": _safe_float(curr.get("dist"), 0.0),
        "before_target_conf": _safe_float(prev.get("target_conf"), 0.0),
        "after_target_conf": _safe_float(curr.get("target_conf"), 0.0),
        "candidate_winner": str(curr.get("candidate_winner") or ""),
        "candidate_source": str(curr.get("candidate_source") or ""),
        "target_mode": str(curr.get("target_mode") or ""),
        "target_update": str(curr.get("target_update") or ""),
        "target_hold_active": bool(curr.get("target_hold_active")),
        "eye_hold_bias_active": bool(curr.get("eye_hold_bias_active")),
        "axis_lock_active": bool(curr.get("axis_lock_active")),
        "axis_lock_axis": str(curr.get("axis_lock_axis") or ""),
        "prev_heartbeat_ts": _heartbeat(prev),
        "curr_heartbeat_ts": _heartbeat(curr),
        "prev_ticks": _ticks(prev),
        "curr_ticks": _ticks(curr),
        "prev_counters": dict(prev_counters),
        "curr_counters": dict(curr_counters),
        "debug": {
            "raw_action": str(curr.get("raw_action") or ""),
            "mapped_action": str(curr.get("mapped_action") or ""),
            "obs_action": str(curr.get("obs_action") or ""),
            "obs_mapped_action": str(curr.get("obs_mapped_action") or ""),
            "last_action": str(curr.get("last_action") or ""),
            "last_reason": str(curr.get("last_reason") or ""),
            "axis": str(curr.get("axis") or ""),
            "amount": _safe_int(curr.get("amount"), 0),
            "target_dx": _safe_float(curr.get("target_dx"), 0.0),
            "target_dy": _safe_float(curr.get("target_dy"), 0.0),
            "dx": _safe_float(curr.get("dx"), 0.0),
            "dy": _safe_float(curr.get("dy"), 0.0),
            "obs_dx": _safe_float(curr.get("obs_dx"), 0.0),
            "obs_dy": _safe_float(curr.get("obs_dy"), 0.0),
            "obs_dist": _safe_float(curr.get("obs_dist"), 0.0),
            "obs_conf": _safe_float(curr.get("obs_conf"), 0.0),
            "energy": _safe_float(curr.get("energy"), 0.0),
            "strength": _safe_float(curr.get("strength"), 0.0),
            "cmd_error": str(curr.get("cmd_error") or ""),
        },
    }


def _cmd_ok_true(state: Mapping[str, Any]) -> bool:
    return state.get("cmd_ok") is True


def _reason_allows_motion_reward(state: Mapping[str, Any]) -> bool:
    reason = str(state.get("reason") or "").strip().lower()
    if reason in ("deadzone", "energy_low", "idle", "cooldown", "move_cooldown", "micro_guard", "stability_wait", "down_hold"):
        return False
    return True


def _emit_signal(
    *,
    source: str,
    value: float,
    confidence: float,
    context: Dict[str, Any],
    verbose: bool,
) -> bool:
    signal = UtilitySignal(
        source=source,
        bahn="ptz",
        value=float(value),
        confidence=float(confidence),
        context=context,
        tag="ptz_motor.reward_collector",
    )
    ok = emit(signal)
    if verbose:
        print(
            f"[ptz_motor_reward_collector] emit source={source} value={float(value):+.4f} "
            f"confidence={float(confidence):.4f} ok={1 if ok else 0}",
            flush=True,
        )
    return ok


def _new_skip_reasons() -> Dict[str, int]:
    return {key: 0 for key in _SKIP_REASON_KEYS}


def _inc_skip_reason(skip_reasons: Dict[str, int], key: str, amount: int = 1) -> None:
    if key not in skip_reasons:
        skip_reasons[key] = 0
    skip_reasons[key] += int(amount)


def _merge_skip_reasons(total: Dict[str, int], current: Mapping[str, int]) -> None:
    for key in _SKIP_REASON_KEYS:
        total[key] = int(total.get(key, 0)) + int(current.get(key, 0))


def _format_skip_reasons(skip_reasons: Mapping[str, int]) -> str:
    parts = [f"{key}:{int(skip_reasons.get(key, 0))}" for key in _SKIP_REASON_KEYS if int(skip_reasons.get(key, 0)) > 0]
    if not parts:
        return "{}"
    return "{" + ", ".join(parts) + "}"


# -----------------------------------------------------------------------------
# Utility-Berechnung vorher/nachher
# -----------------------------------------------------------------------------
def evaluate_pair(
    prev: Mapping[str, Any],
    curr: Mapping[str, Any],
    *,
    max_state_age: float,
    min_dist_change: float,
    min_conf_change: float,
    min_stability_delta: int,
    stability_min_count: int,
    conf_floor: float,
    conf_ceil: float,
    verbose: bool = False,
) -> Tuple[int, int, Dict[str, int]]:
    """Bewertet zwei PTZ-State-Snapshots und emittiert Utility-Signale.

    Returns:
        (emitted_count, skipped_count, skip_reasons)
    """

    skip_reasons = _new_skip_reasons()

    now = time.time()
    if not _is_fresh(curr, max_state_age, now=now):
        _inc_skip_reason(skip_reasons, "state_stale")
        if verbose:
            print(
                f"[ptz_motor_reward_collector] skip stale_state age={_state_age_sec(curr, now=now):.3f}s "
                f"max={max_state_age:.3f}s skips={_format_skip_reasons(skip_reasons)}",
                flush=True,
            )
        return 0, 1, skip_reasons

    if _same_or_older(prev, curr):
        _inc_skip_reason(skip_reasons, "no_tick_change")
        if verbose:
            print(
                f"[ptz_motor_reward_collector] skip unchanged_or_older_state skips={_format_skip_reasons(skip_reasons)}",
                flush=True,
            )
        return 0, 1, skip_reasons

    emitted = 0
    skipped = 0
    base_context = _action_context(prev, curr)
    confidence = _confidence_from_state(curr, conf_floor, conf_ceil)

    prev_dist = _safe_float(prev.get("dist"), 0.0)
    curr_dist = _safe_float(curr.get("dist"), 0.0)
    dist_delta = prev_dist - curr_dist  # positiv = besser zentriert

    # Bewegungs-Rewards nur bei echtem erfolgreichen Kommando.
    if _cmd_ok_true(curr) and _reason_allows_motion_reward(curr):
        if dist_delta >= float(min_dist_change):
            context = dict(base_context)
            context.update({"dist_delta": dist_delta, "min_dist_change": float(min_dist_change)})
            value = _clamp(dist_delta / max(float(min_dist_change) * 5.0, 1e-9), 0.0, 1.0)
            if _emit_signal(source="ptz_motor/center_gain", value=value, confidence=confidence, context=context, verbose=verbose):
                emitted += 1
        elif dist_delta <= -float(min_dist_change):
            context = dict(base_context)
            context.update({"dist_delta": dist_delta, "min_dist_change": float(min_dist_change)})
            value = -_clamp(abs(dist_delta) / max(float(min_dist_change) * 5.0, 1e-9), 0.0, 1.0)
            if _emit_signal(source="ptz_motor/wasted_motion_penalty", value=value, confidence=confidence, context=context, verbose=verbose):
                emitted += 1
        else:
            skipped += 1
            _inc_skip_reason(skip_reasons, "dist_delta_below_min")
    else:
        skipped += 1
        if not _cmd_ok_true(curr):
            _inc_skip_reason(skip_reasons, "cmd_not_ok")
        else:
            _inc_skip_reason(skip_reasons, "reason_suppressed")

    # Target-Confidence-Gain darf positiv oder negativ sein, ist aber kein
    # Bewegungs-Penalty für korrektes Stillhalten.
    prev_conf = _safe_float(prev.get("target_conf"), 0.0)
    curr_conf = _safe_float(curr.get("target_conf"), 0.0)
    conf_delta = curr_conf - prev_conf
    if abs(conf_delta) >= float(min_conf_change):
        context = dict(base_context)
        context.update({"target_conf_delta": conf_delta, "min_conf_change": float(min_conf_change)})
        value = _clamp(conf_delta / max(float(min_conf_change) * 5.0, 1e-9), -1.0, 1.0)
        conf_for_signal = _clamp(max(prev_conf, curr_conf, confidence), conf_floor, conf_ceil)
        if _emit_signal(source="ptz_motor/target_conf_gain", value=value, confidence=conf_for_signal, context=context, verbose=verbose):
            emitted += 1
    else:
        skipped += 1
        _inc_skip_reason(skip_reasons, "conf_delta_below_min")

    # Eye-Pair-/Head-Salience-Stabilität: nur positives Signal bei Anstieg und
    # Mindeststabilität. Kein negatives Signal, damit kurzzeitiger Verlust nicht
    # sofort als Fehlverhalten interpretiert wird.
    prev_stable = _safe_int(prev.get("eye_pair_stable_count"), 0)
    curr_stable = _safe_int(curr.get("eye_pair_stable_count"), 0)
    stable_delta = curr_stable - prev_stable
    if curr_stable >= int(stability_min_count) and stable_delta >= int(min_stability_delta):
        context = dict(base_context)
        context.update(
            {
                "before_eye_pair_stable_count": prev_stable,
                "after_eye_pair_stable_count": curr_stable,
                "stable_delta": stable_delta,
                "stability_min_count": int(stability_min_count),
            }
        )
        value = _clamp(stable_delta / max(float(stability_min_count) * 2.0, 1.0), 0.0, 1.0)
        if _emit_signal(source="ptz_motor/target_stability", value=value, confidence=confidence, context=context, verbose=verbose):
            emitted += 1
    else:
        skipped += 1
        _inc_skip_reason(skip_reasons, "stability_below_min")

    # Counter-basierte Penalties. Diese sind unabhängig von cmd_ok, weil der
    # Counter-Anstieg selbst das Ereignis belegt. Stale/unchanged States wurden
    # oben bereits ausgefiltert, dadurch entstehen keine False-Negatives bei
    # inaktivem Worker.
    fail_delta = _counter(curr, "cmd_fail", 0) - _counter(prev, "cmd_fail", 0)
    if fail_delta > 0:
        context = dict(base_context)
        context.update({"cmd_fail_delta": fail_delta})
        value = -_clamp(float(fail_delta) / 3.0, 0.0, 1.0)
        if _emit_signal(source="ptz_motor/cmd_fail_penalty", value=value, confidence=1.0, context=context, verbose=verbose):
            emitted += 1
    else:
        skipped += 1
        _inc_skip_reason(skip_reasons, "fail_count_unchanged")

    reversal_curr = _counter(curr, "guarded_reversals", _counter(curr, "reversals", 0))
    reversal_prev = _counter(prev, "guarded_reversals", _counter(prev, "reversals", 0))
    reversal_delta = reversal_curr - reversal_prev
    if reversal_delta > 0:
        context = dict(base_context)
        context.update({"reversal_delta": reversal_delta, "counter_name": "guarded_reversals"})
        value = -_clamp(float(reversal_delta) / 3.0, 0.0, 1.0)
        if _emit_signal(source="ptz_motor/reversal_penalty", value=value, confidence=1.0, context=context, verbose=verbose):
            emitted += 1
    else:
        skipped += 1
        _inc_skip_reason(skip_reasons, "reversal_unchanged")

    return emitted, skipped, skip_reasons


# -----------------------------------------------------------------------------
# Laufmodi
# -----------------------------------------------------------------------------
def _install_signal_handlers() -> None:
    def _handle(signum: int, frame: object) -> None:  # noqa: ARG001
        global _STOP
        _STOP = True
        print(f"[ptz_motor_reward_collector] signal={signum} stop_requested=1", flush=True)

    try:
        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
    except Exception:
        pass


def run_once(args: argparse.Namespace) -> int:
    path = _state_path(BASE)
    first = _read_json(path)
    if first is None:
        return 2

    if not _is_fresh(first, args.max_state_age):
        if args.verbose:
            print(
                f"[ptz_motor_reward_collector] first_state_stale age={_state_age_sec(first):.3f}s max={args.max_state_age:.3f}s",
                flush=True,
            )
        return 0

    if args.verbose:
        print(
            f"[ptz_motor_reward_collector] once primed path={path} interval={args.interval_sec:.3f}s "
            f"tick={_ticks(first)} hb={_heartbeat(first):.3f}",
            flush=True,
        )
    time.sleep(float(args.interval_sec))

    second = _read_json(path)
    if second is None:
        return 2

    emitted, skipped, skip_reasons = evaluate_pair(
        first,
        second,
        max_state_age=args.max_state_age,
        min_dist_change=args.min_dist_change,
        min_conf_change=args.min_conf_change,
        min_stability_delta=args.min_stability_delta,
        stability_min_count=args.stability_min_count,
        conf_floor=args.conf_floor,
        conf_ceil=args.conf_ceil,
        verbose=args.verbose,
    )
    if args.verbose:
        print(
            f"[ptz_motor_reward_collector] once done emitted={emitted} skipped={skipped} "
            f"skips={_format_skip_reasons(skip_reasons)} counters={get_counters()}",
            flush=True,
        )
    return 0


def run_loop(args: argparse.Namespace) -> int:
    _install_signal_handlers()
    path = _state_path(BASE)
    prev: Optional[Dict[str, Any]] = None
    total_emitted = 0
    total_skipped = 0
    total_skip_reasons = _new_skip_reasons()

    print(
        f"[ptz_motor_reward_collector] start path={path} interval={args.interval_sec:.3f}s "
        f"max_state_age={args.max_state_age:.3f}s min_dist_change={args.min_dist_change:.4f}",
        flush=True,
    )

    while not _STOP:
        curr = _read_json(path)
        if curr is None:
            total_skipped += 1
            time.sleep(float(args.interval_sec))
            continue

        if prev is None:
            if _is_fresh(curr, args.max_state_age):
                prev = curr
                if args.verbose:
                    print(f"[ptz_motor_reward_collector] primed tick={_ticks(curr)} hb={_heartbeat(curr):.3f}", flush=True)
            else:
                if args.verbose:
                    print(f"[ptz_motor_reward_collector] wait_fresh_state age={_state_age_sec(curr):.3f}s", flush=True)
                total_skipped += 1
                _inc_skip_reason(total_skip_reasons, "state_stale")
            time.sleep(float(args.interval_sec))
            continue

        emitted, skipped, skip_reasons = evaluate_pair(
            prev,
            curr,
            max_state_age=args.max_state_age,
            min_dist_change=args.min_dist_change,
            min_conf_change=args.min_conf_change,
            min_stability_delta=args.min_stability_delta,
            stability_min_count=args.stability_min_count,
            conf_floor=args.conf_floor,
            conf_ceil=args.conf_ceil,
            verbose=args.verbose,
        )
        total_emitted += int(emitted)
        total_skipped += int(skipped)
        _merge_skip_reasons(total_skip_reasons, skip_reasons)
        if not _same_or_older(prev, curr):
            prev = curr

        if args.verbose:
            print(
                f"[ptz_motor_reward_collector] loop emitted={emitted} skipped={skipped} "
                f"skips={_format_skip_reasons(skip_reasons)} "
                f"total_emitted={total_emitted} total_skipped={total_skipped} "
                f"total_skips={_format_skip_reasons(total_skip_reasons)}",
                flush=True,
            )
        time.sleep(float(args.interval_sec))

    print(
        f"[ptz_motor_reward_collector] stop total_emitted={total_emitted} total_skipped={total_skipped} "
        f"total_skips={_format_skip_reasons(total_skip_reasons)} counters={get_counters()}",
        flush=True,
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ORÓMA PTZ Motor Reward Collector – local utility slow-loop")
    parser.add_argument("--once", action="store_true", help="Einmaligen vorher/nachher-Vergleich ausführen und beenden.")
    parser.add_argument("--verbose", action="store_true", help="Ausführliche Diagnoseausgaben aktivieren.")
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=_env_float("OROMA_PTZ_MOTOR_COLLECTOR_INTERVAL_SEC", 8.0, lo=1.0, hi=120.0),
        help="Slow-Loop-Intervall in Sekunden. Default via ENV oder 8.0.",
    )
    parser.add_argument(
        "--max-state-age",
        type=float,
        default=_env_float("OROMA_PTZ_MOTOR_COLLECTOR_MAX_STATE_AGE", 15.0, lo=1.0, hi=300.0),
        help="Maximales Alter des Worker-States in Sekunden. Default via ENV oder 15.0.",
    )
    parser.add_argument(
        "--min-dist-change",
        type=float,
        default=_env_float("OROMA_PTZ_MOTOR_COLLECTOR_MIN_DIST_CHANGE", 0.02, lo=0.001, hi=2.0),
        help="Mindeständerung der normierten Distanz. Default via ENV oder 0.02.",
    )
    parser.add_argument(
        "--min-conf-change",
        type=float,
        default=_env_float("OROMA_PTZ_MOTOR_COLLECTOR_MIN_CONF_CHANGE", 0.02, lo=0.001, hi=1.0),
        help="Mindeständerung von target_conf. Default via ENV oder 0.02.",
    )
    parser.add_argument(
        "--min-stability-delta",
        type=int,
        default=_env_int("OROMA_PTZ_MOTOR_COLLECTOR_MIN_STABILITY_DELTA", 1, lo=1, hi=100),
        help="Mindestanstieg von eye_pair_stable_count. Default via ENV oder 1.",
    )
    parser.add_argument(
        "--stability-min-count",
        type=int,
        default=_env_int("OROMA_PTZ_MOTOR_COLLECTOR_STABILITY_MIN_COUNT", 2, lo=1, hi=100),
        help="Mindestwert für eye_pair_stable_count. Default via ENV oder 2.",
    )
    parser.add_argument(
        "--conf-floor",
        type=float,
        default=_env_float("OROMA_PTZ_MOTOR_COLLECTOR_CONF_FLOOR", 0.05, lo=0.0, hi=1.0),
        help="Confidence-Untergrenze. Default via ENV oder 0.05.",
    )
    parser.add_argument(
        "--conf-ceil",
        type=float,
        default=_env_float("OROMA_PTZ_MOTOR_COLLECTOR_CONF_CEIL", 1.0, lo=0.0, hi=1.0),
        help="Confidence-Obergrenze. Default via ENV oder 1.0.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not _env_bool("OROMA_PTZ_MOTOR_COLLECTOR_ENABLE", True):
        print("[ptz_motor_reward_collector] disabled by OROMA_PTZ_MOTOR_COLLECTOR_ENABLE", flush=True)
        return 0

    args.interval_sec = _clamp(float(args.interval_sec), 1.0, 120.0)
    args.max_state_age = _clamp(float(args.max_state_age), 1.0, 300.0)
    args.min_dist_change = _clamp(float(args.min_dist_change), 0.001, 2.0)
    args.min_conf_change = _clamp(float(args.min_conf_change), 0.001, 1.0)
    args.conf_floor = _clamp(float(args.conf_floor), 0.0, 1.0)
    args.conf_ceil = _clamp(float(args.conf_ceil), args.conf_floor, 1.0)

    if args.once:
        return run_once(args)
    return run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())

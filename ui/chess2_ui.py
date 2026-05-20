#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/chess2_ui.py
# Projekt: ORÓMA – Headless Flask UI
# Modul:   Chess2 UI – mobility-native Parallel-UI zu /chess
# Version: v3.8-r3
# Stand:   2026-03-15
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================

from __future__ import annotations

import json
import os
import random
import threading
import time
from glob import glob
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, jsonify, render_template, request

from core import sql_manager
from core.chess2_repr import (
    board_to_rows,
    mobility_state_hash,
    canonical_mobility_state_hash,
    canonical_cooperation_state_hash,
    canonical_cooperation_king_state_hash,
    canonical_cooperation_king_territory_state_hash,
    mobility_vec64_from_fen,
    cooperation_vec64_from_fen,
    king_weighted_cooperation_vec64_from_fen,
    territory_vec64_from_fen,
    summarize_fen,
    parse_fen,
)
from mini_programs.chess.chess_game import ChessGame

try:
    from tools.chess2_daily_runner import PolicyShim as _RunnerPolicyShim  # type: ignore
except Exception:
    _RunnerPolicyShim = None  # type: ignore

chess2_bp = Blueprint("chess2_ui", __name__, url_prefix="/chess2", template_folder="templates")

_NAMESPACE_OPTIONS: List[Dict[str, str]] = [
    {"value": "game:chess2", "label": "Chess2 · Mobility"},
    {"value": "game:chess2_canon", "label": "Chess2 Canon · Mobility"},
    {"value": "game:chess2_canon_coop", "label": "Chess2 Canon + Coop"},
    {"value": "game:chess2_canon_coop_king", "label": "Chess2 Canon + Coop + King"},
    {"value": "game:chess2_canon_coop_king_territory", "label": "Chess2 Canon + Coop + King + Territory"},
    {"value": "game:chess3_canon_coop_king_territory_v1", "label": "Chess3 Stable · Canon + Coop + King + Territory"},
]
_OROMA_VARIANT_OPTIONS: List[Dict[str, str]] = [
    {"value": "stable", "label": "Stable"},
    {"value": "aggro", "label": "Aggro"},
]
_KI_PROFILE_OPTIONS: List[Dict[str, str]] = [
    {"value": "legacy_ki", "label": "Legacy KI Heuristik"},
    {"value": "chess2", "label": "Chess2 Canon+Coop+King+Territory"},
    {"value": "chess3_stable", "label": "Chess3 Stable"},
    {"value": "chess3_aggro", "label": "Chess3 Aggro"},
]
_AGGRO_LEVEL_OPTIONS: List[Dict[str, int | str]] = [
    {"value": 1, "label": "1 · 1.55"},
    {"value": 2, "label": "2 · 1.75"},
    {"value": 3, "label": "3 · 2.00"},
]
_VALID_NAMESPACES = {str(x["value"]): str(x["label"]) for x in _NAMESPACE_OPTIONS}
_VALID_OROMA_VARIANTS = {str(x["value"]) for x in _OROMA_VARIANT_OPTIONS}
_VALID_KI_PROFILES = {str(x["value"]) for x in _KI_PROFILE_OPTIONS}
_AGGRO_LEVEL_TO_VALUE: Dict[int, float] = {1: 1.55, 2: 1.75, 3: 2.0}

_LOCK = threading.RLock()
_RNG = random.Random()

_STATE_PERSIST_KEYS = ("mode", "speed", "oroma_side", "namespace", "oroma_variant", "ki_profile", "aggro_level")


def _state_config_path() -> Path:
    base = str(os.environ.get("OROMA_BASE") or "/opt/ai/oroma").strip() or "/opt/ai/oroma"
    return Path(base) / "data" / "state" / "chess2_ui_state.json"


def _save_ui_config() -> None:
    try:
        path = _state_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: _STATE.get(k) for k in _STATE_PERSIST_KEYS}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_ui_config() -> None:
    try:
        path = _state_config_path()
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            return
        if str(data.get("mode") or "") in {"oroma_vs_oroma_policy", "oroma_vs_oroma_explore", "ki_vs_ki", "oroma_vs_ki", "oroma_vs_human", "human_vs_ki"}:
            _STATE["mode"] = str(data.get("mode"))
        if str(data.get("speed") or "") in {"normal", "turbo"}:
            _STATE["speed"] = str(data.get("speed"))
        if str(data.get("oroma_side") or "") in {"white", "black"}:
            _STATE["oroma_side"] = str(data.get("oroma_side"))
        ns = str(data.get("namespace") or "")
        if ns in _VALID_NAMESPACES:
            _STATE["namespace"] = ns
        variant = str(data.get("oroma_variant") or "")
        if variant in _VALID_OROMA_VARIANTS:
            _STATE["oroma_variant"] = variant
        prof = str(data.get("ki_profile") or "").strip().lower()
        if prof in _VALID_KI_PROFILES:
            _STATE["ki_profile"] = prof
        try:
            lvl = int(data.get("aggro_level") or 2)
        except Exception:
            lvl = 2
        if lvl in _AGGRO_LEVEL_TO_VALUE:
            _STATE["aggro_level"] = lvl
    except Exception:
        pass

_STATE: Dict[str, Any] = {
    "game": ChessGame(),
    "running": False,
    "mode": "oroma_vs_oroma_explore",
    "speed": "normal",
    "oroma_side": "white",
    "last_step_ts": 0.0,
    "last_action": "",
    "last_source": "",
    "namespace": "game:chess2",
    "oroma_variant": "stable",
    "ki_profile": "legacy_ki",
    "aggro_level": 2,
    "session_started_ts": 0,
    "decision_trace": {"W": [], "B": []},
    "ui_game_written": False,
}


def _env_float(name: str, default: float) -> float:
    try:
        import os
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


class _PolicyShim:
    def __init__(self, namespace: str = "game:chess2", aggro: float = 1.0):
        self.namespace = str(namespace or "game:chess2")
        self.aggro = float(aggro or 1.0)
        self.pol = None
        self._runner_shim = None
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=self.namespace)
        except Exception:
            self.pol = None
        try:
            if _RunnerPolicyShim is not None:
                ns = self.namespace
                canon_mode = ns in {
                    "game:chess2_canon",
                    "game:chess2_canon_coop",
                    "game:chess2_canon_coop_king",
                    "game:chess2_canon_coop_king_territory",
                }
                cooperation_mode = ns in {
                    "game:chess2_canon_coop",
                    "game:chess2_canon_coop_king",
                    "game:chess2_canon_coop_king_territory",
                }
                king_mode = ns in {
                    "game:chess2_canon_coop_king",
                    "game:chess2_canon_coop_king_territory",
                }
                territory_mode = ns in {
                    "game:chess2_canon_coop_king_territory",
                }
                self._runner_shim = _RunnerPolicyShim(
                    namespace=ns,
                    flip_mode=False,
                    canon_mode=canon_mode,
                    cooperation_mode=cooperation_mode,
                    king_mode=king_mode,
                    territory_mode=territory_mode,
                    capture_bias=_env_float("OROMA_CHESS2_CAPTURE_BIAS", 0.12),
                    king_shuffle_penalty=_env_float("OROMA_CHESS2_KING_SHUFFLE_PENALTY", 0.10),
                    piece_variety_bias=_env_float("OROMA_CHESS2_PIECE_VARIETY_BIAS", 0.04),
                    hanging_piece_bias=_env_float("OROMA_CHESS2_HANGING_PIECE_BIAS", 0.18),
                    underdefended_piece_bias=_env_float("OROMA_CHESS2_UNDERDEFENDED_PIECE_BIAS", 0.08),
                    self_hanging_penalty=_env_float("OROMA_CHESS2_SELF_HANGING_PENALTY", 0.24),
                    retaliation_penalty=_env_float("OROMA_CHESS2_RETALIATION_PENALTY", 0.18),
                    defended_attack_bonus=_env_float("OROMA_CHESS2_DEFENDED_ATTACK_BONUS", 0.06),
                    discovery_exposure_penalty=_env_float("OROMA_CHESS2_DISCOVERY_EXPOSURE_PENALTY", 0.12),
                    castle_bias=_env_float("OROMA_CHESS2_CASTLE_BIAS", 0.22),
                    promotion_bias=_env_float("OROMA_CHESS2_PROMOTION_BIAS", 0.55),
                    en_passant_bias=_env_float("OROMA_CHESS2_EN_PASSANT_BIAS", 0.10),
                    check_bias=_env_float("OROMA_CHESS2_CHECK_BIAS", 0.06),
                    line_pressure_bias=_env_float("OROMA_CHESS3_LINE_PRESSURE_BIAS", 0.02),
                    line_pressure_middlegame_lift=_env_float("OROMA_CHESS3_LINE_PRESSURE_MIDDLEGAME_LIFT", 1.50),
                    defense_disruption_bias=_env_float("OROMA_CHESS3_DEFENSE_DISRUPTION_BIAS", 0.065),
                    lookahead_conversion_bias=_env_float("OROMA_CHESS3_LOOKAHEAD_CONVERSION_BIAS", 0.09),
                    penalty_damper_ratio=_env_float("OROMA_CHESS3_PENALTY_DAMPER_RATIO", 0.24),
                    opening_guideline_bias=_env_float("OROMA_CHESS3_OPENING_GUIDELINE_BIAS", 0.075),
                    anti_flat_bias=_env_float("OROMA_CHESS3_ANTI_FLAT_BIAS", 0.060),
                    asymmetry_keep_bias=_env_float("OROMA_CHESS3_ASYMMETRY_KEEP_BIAS", 0.050),
                    worst_piece_improve_bias=_env_float("OROMA_CHESS3_WORST_PIECE_IMPROVE_BIAS", 0.050),
                    coordination_bias=_env_float("OROMA_CHESS3_COORDINATION_BIAS", 0.040),
                    rook_file_activity_bias=_env_float("OROMA_CHESS3_ROOK_FILE_ACTIVITY_BIAS", 0.035),
                    attack_coordination_bias=_env_float("OROMA_CHESS3_ATTACK_COORDINATION_BIAS", 0.045),
                    king_line_open_bias=_env_float("OROMA_CHESS3_KING_LINE_OPEN_BIAS", 0.040),
                    attacker_trade_penalty=_env_float("OROMA_CHESS3_ATTACKER_TRADE_PENALTY", 0.035),
                    orbit_penalty_bias=_env_float("OROMA_CHESS3_ORBIT_PENALTY_BIAS", 0.040),
                    neutral_path_penalty_bias=_env_float("OROMA_CHESS3_NEUTRAL_PATH_PENALTY_BIAS", 0.024),
                    productive_asymmetry_bias=_env_float("OROMA_CHESS3_PRODUCTIVE_ASYMMETRY_BIAS", 0.050),
                    fixpoint_warning_bias=_env_float("OROMA_CHESS3_FIXPOINT_WARNING_BIAS", 0.022),
                    aggro=float(self.aggro),
                )
            else:
                self._runner_shim = None
        except Exception:
            self._runner_shim = None

    def choose(self, fen: str, legal: List[str], side: str, recent_own_actions: List[str] | None = None, recent_own_pieces: List[str] | None = None) -> str:
        if not legal:
            return ""
        if self._runner_shim is not None:
            try:
                action, _src, _sh, _fen_c, _learn = self._runner_shim.choose_meta(
                    fen, legal, side, recent_own_actions=recent_own_actions, recent_own_pieces=recent_own_pieces
                )
                if str(action or "") in legal:
                    return str(action)
            except Exception:
                pass
        if self.pol is None:
            return random.choice(legal)
        sh = mobility_state_hash(fen)
        chosen = self.pol.choose(sh, legal, side=side)
        return str(chosen) if chosen in legal else str(random.choice(legal))


_load_ui_config()
_SHIM_CACHE: Dict[tuple[str, float], _PolicyShim] = {}
_POLICY = _PolicyShim(namespace=str(_STATE.get("namespace") or "game:chess2"), aggro=1.0)


def _namespace_label(namespace: str) -> str:
    return str(_VALID_NAMESPACES.get(str(namespace or "game:chess2"), str(namespace or "game:chess2")))


def _variant_label(variant: str) -> str:
    return "Aggro" if str(variant or "stable") == "aggro" else "Stable"


def _ki_profile_label(profile: str) -> str:
    mapping = {str(x["value"]): str(x["label"]) for x in _KI_PROFILE_OPTIONS}
    return str(mapping.get(str(profile or "legacy_ki"), str(profile or "legacy_ki")))


def _aggro_value_for_level(level: int) -> float:
    try:
        lvl = int(level)
    except Exception:
        lvl = 2
    return float(_AGGRO_LEVEL_TO_VALUE.get(lvl, 1.75))


def _policy_key(namespace: str, aggro: float) -> tuple[str, float]:
    return (str(namespace or "game:chess2"), round(float(aggro or 1.0), 4))


def _get_policy_shim(namespace: str, aggro: float = 1.0) -> _PolicyShim:
    key = _policy_key(namespace, aggro)
    shim = _SHIM_CACHE.get(key)
    if shim is None:
        shim = _PolicyShim(namespace=str(namespace or "game:chess2"), aggro=float(aggro or 1.0))
        _SHIM_CACHE[key] = shim
    return shim


def _active_oroma_aggro() -> float:
    ns = str(_STATE.get("namespace") or "game:chess2")
    variant = str(_STATE.get("oroma_variant") or "stable")
    if ns != "game:chess3_canon_coop_king_territory_v1":
        return 1.0
    if variant != "aggro":
        return 1.0
    return _aggro_value_for_level(int(_STATE.get("aggro_level") or 2))


def _set_policy_namespace(namespace: str, aggro: float = 1.0) -> None:
    global _POLICY
    ns = str(namespace or "game:chess2")
    ag = float(aggro or 1.0)
    if getattr(_POLICY, "namespace", "") == ns and abs(float(getattr(_POLICY, "aggro", 1.0)) - ag) < 1e-9:
        return
    _POLICY = _get_policy_shim(ns, ag)


def _shim_from_ki_profile(profile: str) -> _PolicyShim | None:
    prof = str(profile or "legacy_ki").strip().lower()
    if prof == "legacy_ki":
        return None
    if prof == "chess2":
        return _get_policy_shim("game:chess2_canon_coop_king_territory", 1.0)
    if prof == "chess3_stable":
        return _get_policy_shim("game:chess3_canon_coop_king_territory_v1", 1.0)
    if prof == "chess3_aggro":
        return _get_policy_shim("game:chess3_canon_coop_king_territory_v1", _aggro_value_for_level(int(_STATE.get("aggro_level") or 2)))
    return None




def _effective_ki_profile() -> str:
    prof = str(_STATE.get("ki_profile") or "legacy_ki").strip().lower()
    return prof if prof in _VALID_KI_PROFILES else "legacy_ki"


def _effective_ki_profile_label() -> str:
    return _ki_profile_label(_effective_ki_profile())


def _effective_ki_aggro() -> float:
    return _aggro_value_for_level(int(_STATE.get("aggro_level") or 2)) if _effective_ki_profile() == "chess3_aggro" else 1.0


# Ensure the global UI policy is initialized only after all helper functions
# used to derive the active aggro/profile state are defined.
_set_policy_namespace(str(_STATE.get("namespace") or "game:chess2"), _active_oroma_aggro())

def _state_hash_for_namespace(namespace: str, fen: str) -> str:
    ns = str(namespace or "game:chess2")
    if ns == "game:chess2_canon":
        return canonical_mobility_state_hash(fen)
    if ns == "game:chess2_canon_coop":
        return canonical_cooperation_state_hash(fen)
    if ns == "game:chess2_canon_coop_king":
        return canonical_cooperation_king_state_hash(fen)
    if ns in {"game:chess2_canon_coop_king_territory", "game:chess3_canon_coop_king_territory_v1"}:
        return canonical_cooperation_king_territory_state_hash(fen)
    return mobility_state_hash(fen)


def _repr_metrics_for_namespace(namespace: str, fen: str) -> Dict[str, float | str]:
    ns = str(namespace or "game:chess2")
    if ns in {"game:chess2_canon_coop_king_territory", "game:chess3_canon_coop_king_territory_v1"}:
        cv = king_weighted_cooperation_vec64_from_fen(fen)
        tv = territory_vec64_from_fen(fen)
        return {"repr_mode": "canon+coop+king+territory", "repr_abs_sum": round(sum(abs(float(v)) for v in cv) + sum(abs(float(v)) for v in tv), 4)}
    if ns == "game:chess2_canon_coop_king":
        kv = king_weighted_cooperation_vec64_from_fen(fen)
        return {"repr_mode": "canon+coop+king", "repr_abs_sum": round(sum(abs(float(v)) for v in kv), 4)}
    if ns == "game:chess2_canon_coop":
        cv = cooperation_vec64_from_fen(fen)
        return {"repr_mode": "canon+coop", "repr_abs_sum": round(sum(abs(float(v)) for v in cv), 4)}
    if ns == "game:chess2_canon":
        mv = mobility_vec64_from_fen(fen)
        return {"repr_mode": "canon+mobility", "repr_abs_sum": round(sum(abs(float(v)) for v in mv), 4)}
    mv = mobility_vec64_from_fen(fen)
    return {"repr_mode": "mobility", "repr_abs_sum": round(sum(abs(float(v)) for v in mv), 4)}


def _reset_session_state() -> None:
    _STATE["session_started_ts"] = int(time.time())
    _STATE["decision_trace"] = {"W": [], "B": []}
    _STATE["ui_game_written"] = False


def _side_from_turn(turn: str) -> str:
    return "W" if str(turn) == "w" else "B"


def _winner_to_outcome(w: str | None) -> str:
    if w == "white":
        return "X"
    if w == "black":
        return "O"
    return "D"


def _side_outcome(outcome: str, side: str) -> int:
    if outcome == "D":
        return 0
    if outcome == "X":
        return 1 if side == "W" else -1
    if outcome == "O":
        return 1 if side == "B" else -1
    return 0


def _append_decision_trace(before_fen: str, after_fen: str, action: str, ply_before: int, source: str) -> None:
    parsed = parse_fen(before_fen)
    side = _side_from_turn(parsed.turn)
    piece_type = ""
    try:
        src = parse_square(str(action or "")[:2])
        piece_type = str(parsed.board.get(int(src), "") or "").upper()[:1] if src is not None else ""
    except Exception:
        piece_type = ""
    traces = _STATE.get("decision_trace") or {"W": [], "B": []}
    traces.setdefault(side, []).append({
        "state_hash": mobility_state_hash(before_fen),
        "fen": before_fen,
        "fen_after": after_fen,
        "action": str(action),
        "piece_type": str(piece_type),
        "ply": int(ply_before),
        "ply_after": int(ply_before + 1),
        "side": side,
        "source": str(source),
    })
    _STATE["decision_trace"] = traces


def _build_ui_learn_items(decision_trace: list[dict[str, Any]], outcome: str) -> list[dict[str, Any]]:
    now = int(time.time())
    items: list[dict[str, Any]] = []
    for entry in decision_trace or []:
        if str(entry.get("source") or "") != "oroma":
            continue
        side = str(entry.get("side") or "W")
        items.append({
            "state_hash": str(entry.get("state_hash") or ""),
            "action": str(entry.get("action") or ""),
            "outcome": float(_side_outcome(outcome, side)),
            "ts": now,
            "side": side,
        })
    return items


def _build_ui_chain(side: str, outcome: str, decision_trace: list[dict[str, Any]], mode: str) -> dict[str, Any] | None:
    own = [d for d in (decision_trace or []) if str(d.get("source") or "") == "oroma" and str(d.get("side") or "") == str(side)]
    if not own:
        return None
    rel = _side_outcome(outcome, side)
    steps: list[dict[str, Any]] = []
    first = own[0]
    steps.append({"t": 0, "state_hash": str(first["state_hash"]), "sh": str(first["state_hash"]), "fen": str(first["fen"]), "ply": int(first["ply"]), "side": str(side), "mode": str(mode)})
    for idx in range(1, len(own)):
        prev = own[idx - 1]
        cur = own[idx]
        steps.append({"t": int(idx), "state_hash": str(cur["state_hash"]), "sh": str(cur["state_hash"]), "fen": str(cur["fen"]), "a": str(prev["action"]), "ply": int(cur["ply"]), "side": str(side), "mode": str(mode)})
    last = own[-1]
    steps.append({"t": int(len(own)), "state_hash": f"chess2:terminal:{side}:{outcome}", "sh": f"chess2:terminal:{side}:{outcome}", "a": str(last["action"]), "fen": str(last["fen_after"]), "terminal": "win" if rel > 0 else ("loss" if rel < 0 else "draw"), "ply": int(last["ply_after"]), "side": str(side), "mode": str(mode)})
    return {
        "schema_version": "chess2-ui-1",
        "kind": "chess2_ui_trace",
        "origin": str(_STATE.get("namespace") or "game:chess2"),
        "namespace": str(_STATE.get("namespace") or "game:chess2"),
        "mode": str(mode),
        "side": str(side),
        "result": int(rel),
        "steps_total": int(max(0, len(steps) - 1)),
        "steps": steps,
        "meta": {
            "source": "ui/chess2_ui.py",
            "ui_mode": str(mode),
            "outcome": str(outcome),
            "decision_count": int(len(own)),
            "state_mode": "mobility",
        },
    }


def _persist_ui_game_if_done() -> None:
    if bool(_STATE.get("ui_game_written")):
        return
    g: ChessGame = _STATE["game"]
    winner = g.winner()
    if winner is None:
        return
    traces = _STATE.get("decision_trace") or {"W": [], "B": []}
    all_trace = list(traces.get("W") or []) + list(traces.get("B") or [])
    outcome = _winner_to_outcome(winner)
    learn_items = _build_ui_learn_items(all_trace, outcome)
    try:
        if learn_items and _POLICY.pol is not None:
            _POLICY.pol.learn_many(learn_items)
    except Exception:
        pass
    ts_end = int(time.time())
    ts_start = int(_STATE.get("session_started_ts") or ts_end)
    try:
        eid = sql_manager.insert_episode(
            ts_start=ts_start,
            ts_end=ts_end,
            kind="game:chess2:ui_game",
            source="ui",
            label=f"chess2:ui:{_STATE.get('mode','unknown')}:{winner}",
            meta={
                "namespace": str(_STATE.get("namespace") or "game:chess2"),
                "mode": str(_STATE.get("mode") or "unknown"),
                "oroma_side": str(_STATE.get("oroma_side") or "white"),
                "winner": str(winner),
                "learn_items_count": int(len(learn_items)),
            },
        )
        if eid:
            metrics = {
                "moves": int(sum(len(v or []) for v in traces.values())),
                "learn_items_count": int(len(learn_items)),
                "wins_white": 1.0 if winner == "white" else 0.0,
                "wins_black": 1.0 if winner == "black" else 0.0,
                "draws": 1.0 if winner == "draw" else 0.0,
            }
            for key, val in metrics.items():
                try:
                    sql_manager.insert_episodic_metric(int(eid), int(ts_end), str(key), float(val))
                except Exception:
                    pass
    except Exception:
        pass
    try:
        for side in ("W", "B"):
            chain = _build_ui_chain(side, outcome, all_trace, str(_STATE.get("mode") or "unknown"))
            if chain is None:
                continue
            blob = json.dumps(chain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            sql_manager.insert_snapchain({
                "ts": ts_end,
                "quality": float(chain.get("result", 0) or 0.0),
                "blob": blob,
                "exported": 0,
                "status": "active",
                "origin": str(_STATE.get("namespace") or "game:chess2"),
                "gap_flag": 0,
                "notes": f"chess2_ui:{_STATE.get('mode','unknown')}:side={side}:steps={max(0, len(chain.get('steps') or []) - 1)}",
                "namespace": str(_STATE.get("namespace") or "game:chess2"),
                "source_id": None,
                "version": "chess2_ui:v3.8-r5",
                "weight": 1.0,
            })
    except Exception:
        pass
    _STATE["ui_game_written"] = True


def _pick_ki_move(g: ChessGame) -> str:
    legal = g.legal_uci() or []
    if not legal:
        return ""
    shim = _shim_from_ki_profile(str(_STATE.get("ki_profile") or "legacy_ki"))
    if shim is not None:
        side = "W" if g.turn == "w" else "B"
        try:
            return shim.choose(g.fen(), legal, side=side, recent_own_actions=[], recent_own_pieces=[])
        except Exception:
            pass
    fen = g.fen()
    # Minimal robuste Heuristik: Bevorzuge Captures grob über Zielbelegung im FEN.
    from core.chess2_repr import parse_square
    p = parse_fen(fen)
    def score(uci: str) -> float:
        sq = parse_square(uci[2:4])
        target = p.board.get(sq) if sq is not None else None
        base = 10.0 if target is not None else 0.0
        if len(uci) >= 5:
            base += 3.0
        return base + _RNG.random()
    legal = sorted(legal, key=score, reverse=True)
    return legal[0]


def _mode_allows_human(g: ChessGame, mode: str, oroma_side: str) -> bool:
    turn = g.turn
    if mode == "oroma_vs_human":
        return (turn == "b" and oroma_side == "white") or (turn == "w" and oroma_side == "black")
    if mode == "human_vs_ki":
        return (turn == "b" and oroma_side == "white") or (turn == "w" and oroma_side == "black")
    return False


def _mode_actor(g: ChessGame, mode: str, oroma_side: str) -> str:
    if mode == "ki_vs_ki":
        return "ki"
    if mode == "oroma_vs_oroma_policy":
        return "policy"
    if mode == "oroma_vs_oroma_explore":
        return "policy_explore"
    if mode == "oroma_vs_ki":
        if (g.turn == "w" and oroma_side == "white") or (g.turn == "b" and oroma_side == "black"):
            return "policy"
        return "ki"
    if mode == "oroma_vs_human":
        if _mode_allows_human(g, mode, oroma_side):
            return "human"
        return "policy"
    if mode == "human_vs_ki":
        return "human" if _mode_allows_human(g, mode, oroma_side) else "ki"
    return "policy"


def _step_interval(speed: str) -> float:
    return 0.12 if speed == "turbo" else 0.75


def _recent_oroma_context(side: str) -> tuple[list[str], list[str]]:
    traces = _STATE.get("decision_trace") or {"W": [], "B": []}
    own = [d for d in (traces.get(str(side)) or []) if str(d.get("source") or "") == "oroma"]
    recent = own[-4:]
    return [str(d.get("action") or "") for d in recent if str(d.get("action") or "")], [str(d.get("piece_type") or "") for d in recent if str(d.get("piece_type") or "")]


def _choose_oroma_move(g: ChessGame, mode: str) -> str:
    legal = g.legal_uci() or []
    if not legal:
        return ""
    eps = _env_float("OROMA_CHESS2_UI_EPS", 0.08) if mode == "oroma_vs_oroma_explore" else 0.0
    if eps > 0.0 and _RNG.random() < eps:
        return str(_RNG.choice(legal))
    _set_policy_namespace(str(_STATE.get("namespace") or "game:chess2"), _active_oroma_aggro())
    side = "W" if g.turn == "w" else "B"
    recent_actions, recent_pieces = _recent_oroma_context(side)
    return _POLICY.choose(g.fen(), legal, side=side, recent_own_actions=recent_actions, recent_own_pieces=recent_pieces)




def _uci_last_move_payload(uci: str) -> Dict[str, Any]:
    u = str(uci or "").strip()
    if len(u) < 4:
        return {"last_move": {}, "last_from": None, "last_to": None}
    src = u[:2]
    dst = u[2:4]
    return {
        "last_move": {"uci": u, "from": src, "to": dst, "promotion": u[4:] if len(u) > 4 else ""},
        "last_from": src,
        "last_to": dst,
    }


def _side_king_square_name(fen: str, side_turn: str) -> str:
    try:
        p = parse_fen(fen)
        target = "K" if str(side_turn or "w") == "w" else "k"
        for sq, piece in p.board.items():
            if piece == target:
                file_idx = sq % 8
                rank_idx = sq // 8
                return f"abcdefgh"[file_idx] + str(rank_idx + 1)
    except Exception:
        return ""
    return ""


def _legal_moves_payload(g: ChessGame) -> Dict[str, Any]:
    legal = [str(u) for u in (g.legal_uci() or []) if u]
    origin_map: Dict[str, List[str]] = {}
    for u in legal:
        if len(u) < 4:
            continue
        src = u[:2]
        dst = u[2:4]
        origin_map.setdefault(src, []).append(dst)
    return {
        "legal_moves": legal,
        "legal_from": sorted(origin_map.keys()),
        "legal_to_by_from": {k: sorted(v) for k, v in origin_map.items()},
    }
def _winner_text(g: ChessGame) -> str:
    w = g.winner()
    if w == "white":
        return "White"
    if w == "black":
        return "Black"
    if w == "draw":
        return "Draw"
    return "–"


def _board_payload(g: ChessGame) -> Dict[str, Any]:
    fen = g.fen()
    summary = summarize_fen(fen)
    repr_info = _repr_metrics_for_namespace(str(_STATE.get("namespace") or "game:chess2"), fen)
    legal_info = _legal_moves_payload(g)
    payload = {
        "fen": fen,
        "turn": "white" if g.turn == "w" else "black",
        "turn_side": str(g.turn),
        "winner": _winner_text(g),
        "done": g.winner() is not None,
        "state_hash": _state_hash_for_namespace(str(_STATE.get("namespace") or "game:chess2"), fen),
        "mobility_abs_sum": float(repr_info.get("repr_abs_sum") or 0.0),
        "repr_mode": str(repr_info.get("repr_mode") or "mobility"),
        "namespace_label": _namespace_label(str(_STATE.get("namespace") or "game:chess2")),
        "material_bucket": summary.material_bucket,
        "phase": summary.phase,
        "castling": summary.castling,
        "in_check": bool(summary.in_check),
        "pieces": int(summary.pieces),
        "legal_count": int(len(legal_info.get("legal_moves") or [])),
        "legal_preview": (legal_info.get("legal_moves") or [])[:20],
        "rows": board_to_rows(parse_fen(fen).board),
        "check_square": _side_king_square_name(fen, g.turn) if bool(summary.in_check) else "",
    }
    payload.update(legal_info)
    payload.update(_uci_last_move_payload(str(_STATE.get("last_action") or "")))
    return payload


def _maybe_autostep() -> None:
    with _LOCK:
        if not _STATE.get("running"):
            return
        g: ChessGame = _STATE["game"]
        if g.winner() is not None:
            _STATE["running"] = False
            return
        now = time.time()
        if now - float(_STATE.get("last_step_ts") or 0.0) < _step_interval(str(_STATE.get("speed") or "normal")):
            return
        actor = _mode_actor(g, str(_STATE.get("mode")), str(_STATE.get("oroma_side")))
        if actor == "human":
            return
        uci = _pick_ki_move(g) if actor == "ki" else _choose_oroma_move(g, str(_STATE.get("mode")))
        src = "ki" if actor == "ki" else "oroma"
        fen_before = g.fen()
        ply_before = len(g.moves or [])
        if not uci or not g.play_uci(uci):
            _STATE["running"] = False
            return
        _append_decision_trace(fen_before, g.fen(), uci, ply_before, src)
        _STATE["last_action"] = uci
        _STATE["last_source"] = src
        _STATE["last_step_ts"] = now
        if g.winner() is not None:
            _STATE["running"] = False
            _persist_ui_game_if_done()


def _latest_batch(kind: str) -> Dict[str, Any]:
    try:
        with sql_manager.get_conn(None) as conn:
            row = conn.execute(
                """
                SELECT e.id, e.ts_end, e.label,
                       MAX(CASE WHEN m.key='games' THEN m.value END),
                       MAX(CASE WHEN m.key='avg_moves' THEN m.value END),
                       MAX(CASE WHEN m.key='learn_items_count' THEN m.value END),
                       MAX(CASE WHEN m.key='chains_count' THEN m.value END),
                       MAX(CASE WHEN m.key='bootstrap_items' THEN m.value END),
                       MAX(CASE WHEN m.key IN ('wins_white','wins_x') THEN m.value END),
                       MAX(CASE WHEN m.key IN ('wins_black','wins_o') THEN m.value END),
                       MAX(CASE WHEN m.key='draws' THEN m.value END)
                FROM episodes e
                LEFT JOIN episodic_metrics m ON m.episode_id=e.id
                WHERE e.kind=?
                GROUP BY e.id, e.ts_end, e.label
                ORDER BY e.ts_end DESC, e.id DESC
                LIMIT 1
                """,
                (kind,),
            ).fetchone()
            if not row:
                return {}
            return {
                "id": int(row[0]),
                "ts_end": int(row[1] or 0),
                "label": str(row[2] or ""),
                "games": int(row[3] or 0),
                "avg_moves": float(row[4] or 0.0),
                "learn_items_count": int(row[5] or 0),
                "chains_count": int(row[6] or 0),
                "bootstrap_items": int(row[7] or 0),
                "wins_white": int(row[8] or 0),
                "wins_black": int(row[9] or 0),
                "draws": int(row[10] or 0),
            }
    except Exception:
        return {}


def _policy_rule_stats(namespace: str) -> Dict[str, int]:
    try:
        with sql_manager.get_conn(None) as conn:
            row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT state_hash) FROM policy_rules WHERE namespace=?", (namespace,)).fetchone()
            return {"rules": int(row[0] or 0), "states": int(row[1] or 0)} if row else {"rules": 0, "states": 0}
    except Exception:
        return {"rules": 0, "states": 0}


def _logs_dir() -> Path:
    base = str(os.environ.get("OROMA_BASE") or "/opt/ai/oroma").strip() or "/opt/ai/oroma"
    return Path(base) / "logs"


def _tail_json_object(path: Path) -> Dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            s = str(line or "").strip()
            if not s.startswith("{") or not s.endswith("}"):
                continue
            try:
                data = json.loads(s)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _matchup_log_summary(prefix: str) -> Dict[str, Any]:
    try:
        logs = sorted(glob(str(_logs_dir() / f"{prefix}*.log")))
        if not logs:
            return {"ok": False, "label": prefix, "found": False}
        path = Path(logs[-1])
        payload = _tail_json_object(path)
        if not payload:
            return {"ok": False, "label": prefix, "found": True, "file": path.name}
        return {
            "ok": True,
            "found": True,
            "label": prefix,
            "file": path.name,
            "mtime": int(path.stat().st_mtime),
            "policy_draws_total": int(payload.get("policy_draws_total", 0) or 0),
            "engine_a_policy_wins_total": int(payload.get("engine_a_policy_wins_total", 0) or 0),
            "engine_b_policy_wins_total": int(payload.get("engine_b_policy_wins_total", 0) or 0),
            "policy_avg_moves": float(payload.get("policy_avg_moves", 0.0) or 0.0),
            "flip_policy_avg_moves": float(payload.get("flip_policy_avg_moves", 0.0) or 0.0),
            "rules_delta_engine_a": int(payload.get("rules_delta_engine_a", 0) or 0),
            "rules_delta_engine_b": int(payload.get("rules_delta_engine_b", 0) or 0),
            "chess3_attack_coordination_cases": int(payload.get("chess3_attack_coordination_cases", 0) or 0),
            "chess3_king_line_open_cases": int(payload.get("chess3_king_line_open_cases", 0) or 0),
            "chess3_productive_asymmetry_cases": int(payload.get("chess3_productive_asymmetry_cases", 0) or 0),
            "chess3_neutral_path_penalty_cases": int(payload.get("chess3_neutral_path_penalty_cases", 0) or 0),
            "chess3_fixpoint_warning_cases": int(payload.get("chess3_fixpoint_warning_cases", 0) or 0),
            "chess3_orbit_penalty_cases": int(payload.get("chess3_orbit_penalty_cases", 0) or 0),
        }
    except Exception:
        return {"ok": False, "label": prefix, "found": False}


def _batch_kind(namespace: str, suffix: str) -> str:
    ns = str(namespace or "game:chess2")
    return f"{ns}:{suffix}"


@chess2_bp.route("/", methods=["GET"])
def page() -> str:
    return render_template("chess2.html", namespace_options=_NAMESPACE_OPTIONS, default_namespace=str(_STATE.get("namespace") or "game:chess2"), oroma_variant_options=_OROMA_VARIANT_OPTIONS, ki_profile_options=_KI_PROFILE_OPTIONS, aggro_level_options=_AGGRO_LEVEL_OPTIONS, default_oroma_variant=str(_STATE.get("oroma_variant") or "stable"), default_ki_profile=str(_STATE.get("ki_profile") or "legacy_ki"), default_aggro_level=int(_STATE.get("aggro_level") or 2))


_reset_session_state()


@chess2_bp.route("/api/state", methods=["GET"])
def api_state():
    _maybe_autostep()
    with _LOCK:
        g: ChessGame = _STATE["game"]
        payload = _board_payload(g)
        payload.update({
            "ok": True,
            "running": bool(_STATE.get("running")),
            "mode": str(_STATE.get("mode")),
            "speed": str(_STATE.get("speed")),
            "oroma_side": str(_STATE.get("oroma_side")),
            "human_side": ("black" if str(_STATE.get("oroma_side")) == "white" else "white"),
            "last_action": str(_STATE.get("last_action") or ""),
            "last_source": str(_STATE.get("last_source") or ""),
            "namespace": str(_STATE.get("namespace") or "game:chess2"),
            "namespace_label": _namespace_label(str(_STATE.get("namespace") or "game:chess2")),
            "oroma_variant": str(_STATE.get("oroma_variant") or "stable"),
            "oroma_variant_label": _variant_label(str(_STATE.get("oroma_variant") or "stable")),
            "ki_profile": str(_STATE.get("ki_profile") or "legacy_ki"),
            "ki_profile_label": _ki_profile_label(str(_STATE.get("ki_profile") or "legacy_ki")),
            "effective_ki_profile": _effective_ki_profile(),
            "effective_ki_profile_label": _effective_ki_profile_label(),
            "aggro_level": int(_STATE.get("aggro_level") or 2),
            "aggro_value": float(_aggro_value_for_level(int(_STATE.get("aggro_level") or 2))),
            "effective_ki_aggro": float(_effective_ki_aggro()),
            "human_to_move": _mode_allows_human(g, str(_STATE.get("mode")), str(_STATE.get("oroma_side"))),
        })
        return jsonify(payload)


@chess2_bp.route("/api/reset", methods=["POST"])
def api_reset():
    with _LOCK:
        _STATE["game"] = ChessGame()
        _STATE["running"] = False
        _STATE["last_action"] = ""
        _STATE["last_source"] = ""
        _STATE["last_step_ts"] = 0.0
        _reset_session_state()
        return jsonify({"ok": True, **_board_payload(_STATE["game"])})


@chess2_bp.route("/api/toggle", methods=["POST"])
def api_toggle():
    with _LOCK:
        _STATE["running"] = not bool(_STATE.get("running"))
        return jsonify({"ok": True, "running": bool(_STATE.get("running"))})


@chess2_bp.route("/api/mode", methods=["POST"])
def api_mode():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "").strip()
    allowed = {"oroma_vs_oroma_policy", "oroma_vs_oroma_explore", "ki_vs_ki", "oroma_vs_ki", "oroma_vs_human", "human_vs_ki"}
    if mode not in allowed:
        return jsonify({"ok": False, "err": "invalid_mode"}), 400
    with _LOCK:
        _STATE["mode"] = mode
        _save_ui_config()
        return jsonify({"ok": True, "mode": mode})


@chess2_bp.route("/api/speed", methods=["POST"])
def api_speed():
    data = request.get_json(silent=True) or {}
    speed = str(data.get("speed") or "normal").strip().lower()
    if speed not in {"normal", "turbo"}:
        return jsonify({"ok": False, "err": "invalid_speed"}), 400
    with _LOCK:
        _STATE["speed"] = speed
        _save_ui_config()
        return jsonify({"ok": True, "speed": speed})


@chess2_bp.route("/api/oromaSide", methods=["POST"])
def api_oroma_side():
    data = request.get_json(silent=True) or {}
    side = str(data.get("side") or "white").strip().lower()
    if side not in {"white", "black"}:
        return jsonify({"ok": False, "err": "invalid_side"}), 400
    with _LOCK:
        _STATE["oroma_side"] = side
        _save_ui_config()
        return jsonify({"ok": True, "oroma_side": side, "human_side": "black" if side == "white" else "white"})


@chess2_bp.route("/api/namespace", methods=["POST"])
def api_namespace():
    data = request.get_json(silent=True) or {}
    namespace = str(data.get("namespace") or "game:chess2").strip()
    if namespace not in _VALID_NAMESPACES:
        return jsonify({"ok": False, "err": "invalid_namespace"}), 400
    with _LOCK:
        _STATE["namespace"] = namespace
        _save_ui_config()
        _STATE["game"] = ChessGame()
        _STATE["running"] = False
        _STATE["last_action"] = ""
        _STATE["last_source"] = ""
        _STATE["last_step_ts"] = 0.0
        _set_policy_namespace(namespace, _active_oroma_aggro())
        _reset_session_state()
        return jsonify({"ok": True, "namespace": namespace, "namespace_label": _namespace_label(namespace), **_board_payload(_STATE["game"])})


@chess2_bp.route("/api/oromaVariant", methods=["POST"])
def api_oroma_variant():
    data = request.get_json(silent=True) or {}
    variant = str(data.get("variant") or "stable").strip().lower()
    if variant not in _VALID_OROMA_VARIANTS:
        return jsonify({"ok": False, "err": "invalid_oroma_variant"}), 400
    with _LOCK:
        _STATE["oroma_variant"] = variant
        _save_ui_config()
        _set_policy_namespace(str(_STATE.get("namespace") or "game:chess2"), _active_oroma_aggro())
        return jsonify({"ok": True, "oroma_variant": variant, "oroma_variant_label": _variant_label(variant), "aggro_value": float(_active_oroma_aggro())})


@chess2_bp.route("/api/kiProfile", methods=["POST"])
def api_ki_profile():
    data = request.get_json(silent=True) or {}
    profile = str(data.get("profile") or "legacy_ki").strip().lower()
    if profile not in _VALID_KI_PROFILES:
        return jsonify({"ok": False, "err": "invalid_ki_profile"}), 400
    with _LOCK:
        _STATE["ki_profile"] = profile
        _save_ui_config()
        return jsonify({"ok": True, "ki_profile": profile, "ki_profile_label": _ki_profile_label(profile), "effective_ki_profile": _effective_ki_profile(), "effective_ki_profile_label": _effective_ki_profile_label(), "effective_ki_aggro": float(_effective_ki_aggro())})


@chess2_bp.route("/api/aggroLevel", methods=["POST"])
def api_aggro_level():
    data = request.get_json(silent=True) or {}
    try:
        level = int(data.get("level") or 2)
    except Exception:
        level = 2
    if level not in _AGGRO_LEVEL_TO_VALUE:
        return jsonify({"ok": False, "err": "invalid_aggro_level"}), 400
    with _LOCK:
        _STATE["aggro_level"] = int(level)
        _save_ui_config()
        _set_policy_namespace(str(_STATE.get("namespace") or "game:chess2"), _active_oroma_aggro())
        return jsonify({"ok": True, "aggro_level": int(level), "aggro_value": float(_aggro_value_for_level(level)), "effective_ki_aggro": float(_effective_ki_aggro())})


@chess2_bp.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(silent=True) or {}
    uci = str(data.get("uci") or "").strip()
    with _LOCK:
        g: ChessGame = _STATE["game"]
        if not _mode_allows_human(g, str(_STATE.get("mode")), str(_STATE.get("oroma_side"))):
            return jsonify({"ok": False, "err": "human_move_not_allowed"}), 409
        if uci not in (g.legal_uci() or []):
            return jsonify({"ok": False, "err": "illegal_move"}), 400
        fen_before = g.fen()
        ply_before = len(g.moves or [])
        if not g.play_uci(uci):
            return jsonify({"ok": False, "err": "play_failed"}), 400
        _append_decision_trace(fen_before, g.fen(), uci, ply_before, "human")
        _STATE["last_action"] = uci
        _STATE["last_source"] = "human"
        _STATE["last_step_ts"] = time.time()
        if g.winner() is not None:
            _persist_ui_game_if_done()
        return jsonify({"ok": True, **_board_payload(g)})


@chess2_bp.route("/api/matchup_status", methods=["GET"])
def api_matchup_status():
    summaries = {
        "a2c2": _matchup_log_summary("chess2_vs_chess3_A2C2_100x_5p5f_"),
        "e12": _matchup_log_summary("chess2_vs_chess3_E12_100x_5p5f_"),
    }
    latest_any = _matchup_log_summary("chess2_vs_chess3_")
    return jsonify({"ok": True, "latest": latest_any, "series": summaries})


@chess2_bp.route("/api/daily_status", methods=["GET"])
def api_daily_status():
    namespace = str(request.args.get("namespace") or _STATE.get("namespace") or "game:chess2")
    if namespace not in _VALID_NAMESPACES:
        namespace = "game:chess2"
    return jsonify({
        "ok": True,
        "namespace": namespace,
        "namespace_label": _namespace_label(namespace),
        "policy": _latest_batch(_batch_kind(namespace, "policy_batch")),
        "explore": _latest_batch(_batch_kind(namespace, "explore_batch")),
        "policy_rules": _policy_rule_stats(namespace),
    })

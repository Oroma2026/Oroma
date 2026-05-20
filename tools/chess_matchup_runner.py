#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess_matchup_runner.py
# Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:   Matchup Runner für interne Schachlinien mit Frozen-/Live-Learning-Modus
# Version: v3.8-r2-matchup-v2
# Stand:   2026-03-24
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
# Zweck
# -----
# Dieser Runner vergleicht zwei ORÓMA-Schachlinien direkt gegeneinander auf
# demselben Brettzustand. Der erste produktive Fokus liegt bewusst auf dem
# fairen Frozen-Vergleich zwischen Chess2 und Chess3:
#
# - keine Vermischung mit bestehendem Selbstspiel-Runner
# - keine destruktive Änderung an Chess2-/Chess3-Produktivpfaden
# - identische Startbedingungen, identische Seeds, identische Cap-Regeln
# - Flip-Pass für faire Farbverteilung
# - eigene Episode-Kinds für spätere SQL-/UI-Auswertung
#
# Warum ein eigener Matchup-Runner?
# -------------------------------
# Selbstspiel (Chess3 vs Chess3) zeigt gut, ob eine Heuristik intern feuert,
# aber schlecht, ob eine Linie die Vorgängerlinie real schlägt. Deshalb baut
# dieser Runner eine zusätzliche Matchup-Schicht, ohne die bestehenden Daily-
# Runner anzufassen. So bleiben:
#
# - Chess2-/Chess3-Selbstspiel-Lernspuren unverändert,
# - Matchup-Daten fachlich getrennt,
# - Vergleiche reproduzierbar,
# - Farbverzerrungen per Flip-Pass sichtbar.
#
# Produktiver Scope v1
# --------------------
# - Fokus: Chess2 vs Chess3
# - Frozen-Modus standardmäßig aktiv (keine Policy-Updates)
# - optionaler Live-Learning-Modus mit getrennten Lernpfaden pro Engine
# - Policy- und optionaler Explore-Vergleich
# - Flip-Pass standardmäßig aktiv
# - DBWriter-kompatible Episoden + SnapChains
#
# Wichtige Designentscheidung
# ---------------------------
# Die Engine-Logik pro Seite wird über dieselbe bewährte PolicyShim-Klasse aus
# `tools/chess2_daily_runner.py` gesteuert. Für Chess3 wird weiterhin die eigene
# Namespace verwendet, sodass die Chess3-spezifische Logik (selective lookahead,
# line pressure, defense disruption, conversion bonus, penalty damper) nur dort
# greift, wo sie fachlich hingehört.
#
# Frozen-Modus
# ------------
# Standardmäßig werden bei Matchups keine policy_rules verändert. Das misst die
# aktuelle Spielstärke der Linien, nicht ihre Online-Anpassung im Lauf der Serie.
# Episoden und SnapChains werden weiterhin geschrieben, damit die Matchups im
# ORÓMA-System sichtbar und auswertbar bleiben.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT_STR)

from mini_programs.chess.chess_game import ChessGame
from tools.chess2_daily_runner import (
    PolicyShim,
    _apply_opening_seed,
    _build_learn_items,
    _build_side_chain,
    _db_write_episode,
    _empty_lookahead_counts,
    _env_bool,
    _env_float,
    _env_int,
    _env_str,
    _namespace_rule_count,
    _special_move_counts_for_action,
    _side_from_turn,
    _winner_to_outcome,
    _write_snapchains,
    _piece_type,
    parse_fen,
    parse_square,
)

# Produktiv bewährte Defaults aus der Chess3-Linie; bewusst für beide Engines
# gleich gesetzt, damit der Vergleich nicht durch triviale Bias-Unterschiede
# verzerrt wird. Chess3 erhält darüber hinaus automatisch seine namespace-
# spezifischen Zusatzmechanismen.
_COMMON_HEURISTICS: Dict[str, float] = {
    "capture_bias": 0.12,
    "king_shuffle_penalty": 0.10,
    "piece_variety_bias": 0.04,
    "hanging_piece_bias": 0.18,
    "underdefended_piece_bias": 0.08,
    "self_hanging_penalty": 0.24,
    "retaliation_penalty": 0.18,
    "defended_attack_bonus": 0.06,
    "discovery_exposure_penalty": 0.12,
    "castle_bias": 0.22,
    "promotion_bias": 0.55,
    "en_passant_bias": 0.10,
    "check_bias": 0.06,
    "line_pressure_bias": 0.02,
    "line_pressure_middlegame_lift": 1.35,
    "defense_disruption_bias": 0.05,
    "lookahead_conversion_bias": 0.05,
    "penalty_damper_ratio": 0.15,
    "opening_guideline_bias": 0.075,
    "anti_flat_bias": 0.070,
    "asymmetry_keep_bias": 0.060,
    "worst_piece_improve_bias": 0.050,
    "coordination_bias": 0.040,
    "rook_file_activity_bias": 0.035,
    "attack_coordination_bias": 0.040,
    "king_line_open_bias": 0.032,
    "attacker_trade_penalty": 0.045,
    "orbit_penalty_bias": 0.060,
    "neutral_path_penalty_bias": 0.055,
    "productive_asymmetry_bias": 0.040,
    "fixpoint_warning_bias": 0.055,
}

_ENGINE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "chess2": {
        "namespace": "game:chess2_canon_coop_king_territory",
        "canonical": True,
        "cooperation": True,
        "king": True,
        "territory": True,
    },
    "chess3": {
        "namespace": "game:chess3_canon_coop_king_territory_v1",
        "canonical": True,
        "cooperation": True,
        "king": True,
        "territory": True,
    },
}

_OPENING_SEED_BOOK: List[List[str]] = [
    ["e2e4", "e7e5", "g1f3", "b8c6"],
    ["d2d4", "d7d5", "c2c4", "e7e6"],
    ["c2c4", "e7e5", "b1c3", "g8f6"],
    ["g1f3", "d7d5", "d2d4", "g8f6"],
    ["e2e4", "c7c5", "g1f3", "d7d6"],
    ["d2d4", "g8f6", "c2c4", "g7g6"],
    ["e2e4", "e7e6", "d2d4", "d7d5"],
    ["c2c4", "c7c5", "g2g3", "g7g6"],
]


def _select_opening_seed(index: int) -> List[str]:
    if not _OPENING_SEED_BOOK:
        return []
    return list(_OPENING_SEED_BOOK[int(index) % len(_OPENING_SEED_BOOK)])


def _safe_parse_src_piece(fen: str, uci: str) -> str:
    try:
        src_sq = parse_square(str(uci)[:2])
        return str(parse_fen(fen).board.get(int(src_sq) if src_sq is not None else -1, "") or "")
    except Exception:
        return ""


def _merge_counts(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for key, value in (src or {}).items():
        if isinstance(value, float):
            dst[str(key)] = float(dst.get(str(key), 0.0) or 0.0) + float(value or 0.0)
        else:
            dst[str(key)] = int(dst.get(str(key), 0) or 0) + int(value or 0)


def _build_shim(engine: str, *, aggro: float = 1.0) -> PolicyShim:
    name = str(engine or "").strip().lower()
    if name not in _ENGINE_CONFIGS:
        raise ValueError(f"unsupported engine '{engine}' (v1 unterstützt chess2/chess3)")
    cfg = dict(_ENGINE_CONFIGS[name])
    return PolicyShim(
        namespace=str(cfg["namespace"]),
        flip_mode=False,
        canon_mode=bool(cfg.get("canonical", False)),
        cooperation_mode=bool(cfg.get("cooperation", False)),
        king_mode=bool(cfg.get("king", False)),
        territory_mode=bool(cfg.get("territory", False)),
        **_COMMON_HEURISTICS,
        aggro=float(aggro),
    )




def _apply_live_learning_for_game(
    white_engine: str,
    black_engine: str,
    white_shim: PolicyShim,
    black_shim: PolicyShim,
    outcome: str,
    decision_trace: Dict[str, List[Dict[str, Any]]],
    *,
    win_weight: float = 1.0,
) -> Dict[str, int]:
    """Lernt unmittelbar aus einer abgeschlossenen Match-Partie.

    Die Lernpfade bleiben streng getrennt: Jede Seite lernt nur auf ihrem
    eigenen Namespace/PolicyShim aus ihrer eigenen Trace. Dadurch entsteht
    kein Vermischen der Linien, obwohl beide auf demselben Brett spielen.
    """
    counts: Dict[str, int] = {str(white_engine): 0, str(black_engine): 0}
    for side, engine_name, shim in (("W", str(white_engine), white_shim), ("B", str(black_engine), black_shim)):
        trace = list((decision_trace or {}).get(side) or [])
        if not trace:
            continue
        items = _build_learn_items(side, outcome, trace, win_weight=float(win_weight))
        if not items:
            continue
        learned = int(shim.learn_many(items) or 0)
        counts[str(engine_name)] = int(counts.get(str(engine_name), 0) or 0) + learned
    return counts

def _run_match_game(
    rng: random.Random,
    white_engine: str,
    black_engine: str,
    white_shim: PolicyShim,
    black_shim: PolicyShim,
    mode: str,
    eps_white: float,
    eps_black: float,
    explore_moves_white: int,
    explore_moves_black: int,
    max_plies: int,
    matchup_namespace: str,
    pass_name: str,
    opening_seed: Optional[List[str]],
    frozen: bool,
    win_weight: float,
) -> Dict[str, Any]:
    g = ChessGame()
    plies = 0
    explore_budget = {"W": 0, "B": 0}
    explore_budget_limit = {"W": int(max(0, explore_moves_white)), "B": int(max(0, explore_moves_black))}
    explore_eps = {"W": float(max(0.0, eps_white)), "B": float(max(0.0, eps_black))}
    decision_trace: Dict[str, List[Dict[str, Any]]] = {"W": [], "B": []}
    recent_actions_by_side: Dict[str, List[str]] = {"W": [], "B": []}
    recent_piece_types_by_side: Dict[str, List[str]] = {"W": [], "B": []}
    opening_seed_applied = 0
    if opening_seed:
        seed_plies, seed_recent_actions, seed_recent_piece_types, opening_seed_applied = _apply_opening_seed(g, list(opening_seed))
        plies += int(seed_plies)
        recent_actions_by_side = {"W": list(seed_recent_actions.get("W", [])), "B": list(seed_recent_actions.get("B", []))}
        recent_piece_types_by_side = {"W": list(seed_recent_piece_types.get("W", [])), "B": list(seed_recent_piece_types.get("B", []))}

    decision_source_counts: Dict[str, int] = {}
    special_move_counts: Dict[str, int] = {"castles": 0, "promotions": 0, "en_passant": 0, "checks": 0}
    lookahead_by_engine: Dict[str, Dict[str, Any]] = {
        str(white_engine): _empty_lookahead_counts(),
        str(black_engine): _empty_lookahead_counts(),
    }
    invalid_action_fallbacks = 0
    terminal_reason = "draw_unknown"

    while True:
        winner = g.winner()
        if winner is not None:
            outcome = _winner_to_outcome(winner)
            terminal_reason = "winner"
            break
        if plies >= max_plies:
            outcome = "D"
            terminal_reason = "max_plies"
            break
        legal = g.legal_uci() or []
        if not legal:
            outcome = "D"
            terminal_reason = "no_legal"
            break

        fen = g.fen()
        side = _side_from_turn(g.turn)
        engine_name = str(white_engine if side == "W" else black_engine)
        shim = white_shim if side == "W" else black_shim
        do_explore = False
        if mode == "explore":
            if explore_budget[side] < explore_budget_limit[side]:
                do_explore = True
                explore_budget[side] += 1
            elif rng.random() < explore_eps[side]:
                do_explore = True

        if do_explore:
            action = str(rng.choice(legal))
            source = f"{engine_name}:explore_random"
            learn_action = action
            fen_before_learn = fen
            state_hash = shim.state_hash(fen)
        else:
            shim._active_mode = "policy" if mode == "policy" else str(mode)
            action, source_core, state_hash, fen_before_learn, learn_action = shim.choose_meta(
                fen,
                legal,
                side=side,
                recent_own_actions=recent_actions_by_side.get(side, []),
                recent_own_pieces=recent_piece_types_by_side.get(side, []),
            )
            source = f"{engine_name}:{source_core}"
            _merge_counts(lookahead_by_engine[engine_name], getattr(shim, "last_lookahead_meta", {}) or {})

        action = str(action)
        if action not in legal:
            invalid_action_fallbacks += 1
            action = str(rng.choice(legal))
            source = f"{source}:invalid_fallback"

        for key, val in _special_move_counts_for_action(fen, action, side).items():
            special_move_counts[str(key)] = int(special_move_counts.get(str(key), 0) or 0) + int(val or 0)

        if not g.play_uci(action):
            outcome = "D"
            terminal_reason = "invalid_move_runtime"
            break

        recent_actions_by_side.setdefault(side, []).append(str(action))
        if len(recent_actions_by_side[side]) > 6:
            recent_actions_by_side[side] = recent_actions_by_side[side][-6:]
        moved_piece_t = _piece_type(_safe_parse_src_piece(fen, action))
        if moved_piece_t:
            recent_piece_types_by_side.setdefault(side, []).append(moved_piece_t)
            if len(recent_piece_types_by_side[side]) > 4:
                recent_piece_types_by_side[side] = recent_piece_types_by_side[side][-4:]
        decision_source_counts[source] = int(decision_source_counts.get(source, 0) or 0) + 1
        fen_after = g.fen()
        decision_trace[side].append({
            "state_hash": state_hash,
            "fen": fen_before_learn,
            "fen_after": fen_after,
            "action": learn_action or action,
            "played_action": action,
            "ply": int(plies),
            "ply_after": int(plies + 1),
            "side": side,
            "engine": engine_name,
            "mode": mode,
            "pass_name": str(pass_name),
            "decision_source": source,
        })
        plies += 1

    learn_counts: Dict[str, int] = {str(white_engine): 0, str(black_engine): 0}
    if not bool(frozen):
        learn_counts = _apply_live_learning_for_game(
            white_engine=str(white_engine),
            black_engine=str(black_engine),
            white_shim=white_shim,
            black_shim=black_shim,
            outcome=str(outcome),
            decision_trace=decision_trace,
            win_weight=float(win_weight),
        )

    chains: List[Dict[str, Any]] = []
    for side, engine_name in (("W", str(white_engine)), ("B", str(black_engine))):
        trace = decision_trace.get(side) or []
        if not trace:
            continue
        chain = _build_side_chain(matchup_namespace, mode, side, outcome, max_plies, trace, pass_name=pass_name)
        if isinstance(chain, dict):
            chain["engine"] = engine_name
            chains.append(chain)

    return {
        "outcome": outcome,
        "plies": int(plies),
        "chains": chains,
        "draw_by_cap": 1 if (str(outcome) == "D" and str(terminal_reason) == "max_plies") else 0,
        "terminal_reason": str(terminal_reason),
        "invalid_action_fallbacks": int(invalid_action_fallbacks),
        "opening_seed_applied": int(opening_seed_applied),
        "decision_source_counts": dict(decision_source_counts),
        "special_move_counts": dict(special_move_counts),
        "lookahead_by_engine": lookahead_by_engine,
        "learn_counts": dict(learn_counts),
    }


def _matchup_kind(engine_a: str, engine_b: str) -> str:
    return f"game:{str(engine_a)}_vs_{str(engine_b)}"


def _engine_metric_prefix(engine_name: str) -> str:
    return str(engine_name).strip().lower().replace(":", "_").replace("-", "_")


def _collect_engine_batch_metrics(prefix: str, counts: Dict[str, Any]) -> Dict[str, Any]:
    return {
        f"{prefix}_lookahead_2ply_used": int(counts.get("lookahead_2ply_used", 0) or 0),
        f"{prefix}_lookahead_3ply_used": int(counts.get("lookahead_3ply_used", 0) or 0),
        f"{prefix}_lookahead_correction_count": int(counts.get("lookahead_correction_count", 0) or 0),
        f"{prefix}_lookahead_agreement_count": int(counts.get("lookahead_agreement_count", 0) or 0),
        f"{prefix}_lookahead_bonus_sum": float(counts.get("lookahead_bonus_sum", 0.0) or 0.0),
        f"{prefix}_lookahead_penalty_sum": float(counts.get("lookahead_penalty_sum", 0.0) or 0.0),
        f"{prefix}_lookahead_errors": int(counts.get("lookahead_errors", 0) or 0),
        f"{prefix}_lookahead_fallbacks": int(counts.get("lookahead_fallbacks", 0) or 0),
        f"{prefix}_line_pressure_cases": int(counts.get("line_pressure_cases", 0) or 0),
        f"{prefix}_line_pressure_bonus_sum": float(counts.get("line_pressure_bonus_sum", 0.0) or 0.0),
        f"{prefix}_line_pressure_middlegame_cases": int(counts.get("line_pressure_middlegame_cases", 0) or 0),
        f"{prefix}_defense_disruption_cases": int(counts.get("defense_disruption_cases", 0) or 0),
        f"{prefix}_defense_disruption_bonus_sum": float(counts.get("defense_disruption_bonus_sum", 0.0) or 0.0),
        f"{prefix}_lookahead_conversion_bonus_sum": float(counts.get("lookahead_conversion_bonus_sum", 0.0) or 0.0),
        f"{prefix}_penalty_damper_cases": int(counts.get("penalty_damper_cases", 0) or 0),
        f"{prefix}_penalty_damper_sum": float(counts.get("penalty_damper_sum", 0.0) or 0.0),
        f"{prefix}_anti_flat_cases": int(counts.get("anti_flat_cases", 0) or 0),
        f"{prefix}_anti_flat_penalty_sum": float(counts.get("anti_flat_penalty_sum", 0.0) or 0.0),
        f"{prefix}_trade_without_gain_cases": int(counts.get("trade_without_gain_cases", 0) or 0),
        f"{prefix}_trade_without_gain_penalty_sum": float(counts.get("trade_without_gain_penalty_sum", 0.0) or 0.0),
        f"{prefix}_asymmetry_keep_cases": int(counts.get("asymmetry_keep_cases", 0) or 0),
        f"{prefix}_asymmetry_keep_bonus_sum": float(counts.get("asymmetry_keep_bonus_sum", 0.0) or 0.0),
        f"{prefix}_worst_piece_improve_cases": int(counts.get("worst_piece_improve_cases", 0) or 0),
        f"{prefix}_worst_piece_improve_bonus_sum": float(counts.get("worst_piece_improve_bonus_sum", 0.0) or 0.0),
        f"{prefix}_coordination_cases": int(counts.get("coordination_cases", 0) or 0),
        f"{prefix}_coordination_bonus_sum": float(counts.get("coordination_bonus_sum", 0.0) or 0.0),
        f"{prefix}_rook_file_activity_cases": int(counts.get("rook_file_activity_cases", 0) or 0),
        f"{prefix}_rook_file_activity_bonus_sum": float(counts.get("rook_file_activity_bonus_sum", 0.0) or 0.0),
        f"{prefix}_attack_coordination_cases": int(counts.get("attack_coordination_cases", 0) or 0),
        f"{prefix}_attack_coordination_bonus_sum": float(counts.get("attack_coordination_bonus_sum", 0.0) or 0.0),
        f"{prefix}_king_line_open_cases": int(counts.get("king_line_open_cases", 0) or 0),
        f"{prefix}_king_line_open_bonus_sum": float(counts.get("king_line_open_bonus_sum", 0.0) or 0.0),
        f"{prefix}_attacker_trade_penalty_cases": int(counts.get("attacker_trade_penalty_cases", 0) or 0),
        f"{prefix}_attacker_trade_penalty_sum": float(counts.get("attacker_trade_penalty_sum", 0.0) or 0.0),
        f"{prefix}_orbit_penalty_cases": int(counts.get("orbit_penalty_cases", 0) or 0),
        f"{prefix}_orbit_penalty_sum": float(counts.get("orbit_penalty_sum", 0.0) or 0.0),
        f"{prefix}_neutral_path_penalty_cases": int(counts.get("neutral_path_penalty_cases", 0) or 0),
        f"{prefix}_neutral_path_penalty_sum": float(counts.get("neutral_path_penalty_sum", 0.0) or 0.0),
        f"{prefix}_productive_asymmetry_cases": int(counts.get("productive_asymmetry_cases", 0) or 0),
        f"{prefix}_productive_asymmetry_bonus_sum": float(counts.get("productive_asymmetry_bonus_sum", 0.0) or 0.0),
        f"{prefix}_fixpoint_warning_cases": int(counts.get("fixpoint_warning_cases", 0) or 0),
        f"{prefix}_fixpoint_warning_penalty_sum": float(counts.get("fixpoint_warning_penalty_sum", 0.0) or 0.0),
    }


def _run_match_batch(
    rng: random.Random,
    engine_a: str,
    engine_b: str,
    games: int,
    mode: str,
    white_engine: str,
    black_engine: str,
    max_plies: int,
    eps_white: float,
    eps_black: float,
    explore_moves_white: int,
    explore_moves_black: int,
    pass_name: str,
    opening_seed_offset: int,
    frozen: bool,
    win_weight: float,
) -> Dict[str, Any]:
    t0 = time.time()
    wins_white = wins_black = draws = 0
    engine_a_wins = engine_b_wins = 0
    draws_by_cap = 0
    total_plies = 0
    max_plies_seen = 0
    opening_seed_games = 0
    opening_seed_variants: set[int] = set()
    invalid_action_fallbacks = 0
    special_move_counts = {"castles": 0, "promotions": 0, "en_passant": 0, "checks": 0}
    decision_source_counts: Dict[str, int] = {}
    terminal_reason_counts: Dict[str, int] = {}
    lookahead_by_engine: Dict[str, Dict[str, Any]] = {
        str(engine_a): _empty_lookahead_counts(),
        str(engine_b): _empty_lookahead_counts(),
    }
    learn_counts_by_engine: Dict[str, int] = {str(engine_a): 0, str(engine_b): 0}
    matchup_namespace = _matchup_kind(engine_a, engine_b)
    white_shim = _build_shim(white_engine)
    black_shim = _build_shim(black_engine)
    chains: List[Dict[str, Any]] = []

    for idx in range(max(0, int(games))):
        opening_seed = _select_opening_seed(int(opening_seed_offset) + int(idx))
        if opening_seed:
            opening_seed_games += 1
            opening_seed_variants.add((int(opening_seed_offset) + int(idx)) % max(1, len(_OPENING_SEED_BOOK)))
        result = _run_match_game(
            rng,
            white_engine=white_engine,
            black_engine=black_engine,
            white_shim=white_shim,
            black_shim=black_shim,
            mode=mode,
            eps_white=eps_white if mode == "explore" else 0.0,
            eps_black=eps_black if mode == "explore" else 0.0,
            explore_moves_white=explore_moves_white if mode == "explore" else 0,
            explore_moves_black=explore_moves_black if mode == "explore" else 0,
            max_plies=max_plies,
            matchup_namespace=matchup_namespace,
            pass_name=pass_name,
            opening_seed=opening_seed,
            frozen=bool(frozen),
            win_weight=float(win_weight),
        )
        plies = int(result.get("plies") or 0)
        total_plies += plies
        max_plies_seen = max(max_plies_seen, plies)
        draws_by_cap += int(result.get("draw_by_cap") or 0)
        invalid_action_fallbacks += int(result.get("invalid_action_fallbacks") or 0)
        term = str(result.get("terminal_reason") or "unknown")
        terminal_reason_counts[term] = int(terminal_reason_counts.get(term, 0) or 0) + 1
        for src, cnt in (result.get("decision_source_counts") or {}).items():
            decision_source_counts[str(src)] = int(decision_source_counts.get(str(src), 0) or 0) + int(cnt or 0)
        for key, cnt in (result.get("special_move_counts") or {}).items():
            special_move_counts[str(key)] = int(special_move_counts.get(str(key), 0) or 0) + int(cnt or 0)
        chains.extend([c for c in (result.get("chains") or []) if isinstance(c, dict)])
        for engine_name, counts in (result.get("lookahead_by_engine") or {}).items():
            _merge_counts(lookahead_by_engine.setdefault(str(engine_name), _empty_lookahead_counts()), counts or {})
        for engine_name, cnt in (result.get("learn_counts") or {}).items():
            learn_counts_by_engine[str(engine_name)] = int(learn_counts_by_engine.get(str(engine_name), 0) or 0) + int(cnt or 0)

        outcome = str(result.get("outcome") or "D")
        if outcome == "X":
            wins_white += 1
            if str(white_engine) == str(engine_a):
                engine_a_wins += 1
            else:
                engine_b_wins += 1
        elif outcome == "O":
            wins_black += 1
            if str(black_engine) == str(engine_a):
                engine_a_wins += 1
            else:
                engine_b_wins += 1
        else:
            draws += 1

    chains_written = _write_snapchains({"ts_end": int(time.time()), "namespace": matchup_namespace, "mode": f"matchup:{mode}", "chains": chains})
    dt = max(0.0, time.time() - t0)
    batch: Dict[str, Any] = {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": int(dt * 1000.0),
        "games": int(games),
        "wins_x": int(wins_white),
        "wins_o": int(wins_black),
        "wins_white": int(wins_white),
        "wins_black": int(wins_black),
        "draws": int(draws),
        "draws_by_cap": int(draws_by_cap),
        "avg_moves": float(total_plies / max(1, games)),
        "avg_game_ms": float((dt * 1000.0) / max(1, games)),
        "max_plies_seen": int(max_plies_seen),
        "mode": str(mode),
        "pass_name": str(pass_name),
        "namespace": matchup_namespace,
        "policy_enabled": 1.0,
        "eps": float(max(eps_white, eps_black) if mode == "explore" else 0.0),
        "eps_white": float(eps_white if mode == "explore" else 0.0),
        "eps_black": float(eps_black if mode == "explore" else 0.0),
        "explore_moves_white": int(explore_moves_white if mode == "explore" else 0),
        "explore_moves_black": int(explore_moves_black if mode == "explore" else 0),
        "flip_pass": 1 if str(pass_name) == "flip" else 0,
        "learn": bool(not frozen),
        "source": "orchestrator",
        "label": f"{engine_a}_vs_{engine_b}:{mode}:{pass_name}",
        "runner": "tools/chess_matchup_runner.py",
        "learn_items_count": int(sum(int(v or 0) for v in learn_counts_by_engine.values())),
        "learn_items_engine_a": int(learn_counts_by_engine.get(str(engine_a), 0) or 0),
        "learn_items_engine_b": int(learn_counts_by_engine.get(str(engine_b), 0) or 0),
        "chains_count": int(chains_written),
        "invalid_action_fallbacks": int(invalid_action_fallbacks),
        "terminal_winner": int(terminal_reason_counts.get("winner", 0) or 0),
        "terminal_max_plies": int(terminal_reason_counts.get("max_plies", 0) or 0),
        "terminal_no_legal": int(terminal_reason_counts.get("no_legal", 0) or 0),
        "terminal_invalid_move_runtime": int(terminal_reason_counts.get("invalid_move_runtime", 0) or 0),
        "castles": int(special_move_counts.get("castles", 0) or 0),
        "promotions": int(special_move_counts.get("promotions", 0) or 0),
        "en_passant": int(special_move_counts.get("en_passant", 0) or 0),
        "checks": int(special_move_counts.get("checks", 0) or 0),
        "opening_seed_games": int(opening_seed_games),
        "opening_seed_variants": int(len(opening_seed_variants)),
        "engine_a": str(engine_a),
        "engine_b": str(engine_b),
        "white_engine": str(white_engine),
        "black_engine": str(black_engine),
        "engine_a_wins": int(engine_a_wins),
        "engine_b_wins": int(engine_b_wins),
    }
    for src, cnt in decision_source_counts.items():
        batch[f"src_{src.replace(':', '_')}"] = int(cnt or 0)
    for engine_name in (str(engine_a), str(engine_b)):
        batch.update(_collect_engine_batch_metrics(_engine_metric_prefix(engine_name), lookahead_by_engine.get(engine_name, {})))
    return batch


def _sum_metric(batch: Optional[Dict[str, Any]], key: str) -> float:
    if not isinstance(batch, dict):
        return 0.0
    val = batch.get(key, 0)
    try:
        return float(val or 0)
    except Exception:
        return 0.0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Chess Matchup Runner (frozen/live, fair, with flip)")
    ap.add_argument("--engine-a", type=str, default=_env_str("OROMA_CHESS_MATCHUP_ENGINE_A", "chess2"))
    ap.add_argument("--engine-b", type=str, default=_env_str("OROMA_CHESS_MATCHUP_ENGINE_B", "chess3"))
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_CHESS_MATCHUP_POLICY_GAMES", 10))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_CHESS_MATCHUP_EXPLORE_GAMES", 0))
    ap.add_argument("--max-plies", type=int, default=_env_int("OROMA_CHESS_MATCHUP_MAX_PLIES", 180))
    ap.add_argument("--enable-flip-pass", type=int, default=_env_int("OROMA_CHESS_MATCHUP_ENABLE_FLIP_PASS", 1))
    ap.add_argument("--flip-policy-games", type=int, default=-1)
    ap.add_argument("--flip-explore-games", type=int, default=-1)
    ap.add_argument("--eps-white", type=float, default=_env_float("OROMA_CHESS_MATCHUP_EPS_WHITE", 0.08))
    ap.add_argument("--eps-black", type=float, default=_env_float("OROMA_CHESS_MATCHUP_EPS_BLACK", 0.08))
    ap.add_argument("--explore-moves-white", type=int, default=_env_int("OROMA_CHESS_MATCHUP_EXPLORE_MOVES_WHITE", 1))
    ap.add_argument("--explore-moves-black", type=int, default=_env_int("OROMA_CHESS_MATCHUP_EXPLORE_MOVES_BLACK", 1))
    ap.add_argument("--seed", type=int, default=_env_int("OROMA_CHESS_MATCHUP_SEED", 1337))
    ap.add_argument("--frozen", type=int, default=_env_int("OROMA_CHESS_MATCHUP_FROZEN", 1), help="1=frozen (kein Lernen), 0=live-adaptive Lernen pro Engine")
    ap.add_argument("--aggro-a", type=float, default=_env_float("OROMA_CHESS_MATCHUP_AGGRO_A", 1.0), help="Aggro-Faktor für Engine A (1.0=Basis)")
    ap.add_argument("--aggro-b", type=float, default=_env_float("OROMA_CHESS_MATCHUP_AGGRO_B", 1.0), help="Aggro-Faktor für Engine B (1.0=Basis)")
    ap.add_argument("--win-weight", type=float, default=_env_float("OROMA_CHESS_MATCHUP_WIN_WEIGHT", 1.0))
    args = ap.parse_args(list(argv) if argv is not None else None)

    rng = random.Random(int(args.seed))
    engine_a = str(args.engine_a or "").strip().lower()
    engine_b = str(args.engine_b or "").strip().lower()
    if engine_a == engine_b:
        print(json.dumps({"ok": False, "error": "engine_a_equals_engine_b"}, ensure_ascii=False))
        return 2
    # Produktiv defensiv: v1 support bewusst nur chess2/chess3.
    _build_shim(engine_a)
    _build_shim(engine_b)

    flip_enabled = bool(int(args.enable_flip_pass))
    flip_policy_games = int(args.flip_policy_games if int(args.flip_policy_games) >= 0 else args.policy_games)
    flip_explore_games = int(args.flip_explore_games if int(args.flip_explore_games) >= 0 else args.explore_games)
    matchup_kind = _matchup_kind(engine_a, engine_b)
    rules_before_a = _namespace_rule_count(_ENGINE_CONFIGS[engine_a]["namespace"])
    rules_before_b = _namespace_rule_count(_ENGINE_CONFIGS[engine_b]["namespace"])

    policy_res = _run_match_batch(
        rng, engine_a, engine_b, int(args.policy_games), "policy",
        white_engine=engine_a, black_engine=engine_b,
        max_plies=int(args.max_plies), eps_white=0.0, eps_black=0.0,
        explore_moves_white=0, explore_moves_black=0,
        pass_name="normal", opening_seed_offset=0,
        frozen=bool(int(args.frozen)), win_weight=float(args.win_weight),
    )
    explore_res = _run_match_batch(
        rng, engine_a, engine_b, int(args.explore_games), "explore",
        white_engine=engine_a, black_engine=engine_b,
        max_plies=int(args.max_plies), eps_white=float(args.eps_white), eps_black=float(args.eps_black),
        explore_moves_white=int(args.explore_moves_white), explore_moves_black=int(args.explore_moves_black),
        pass_name="normal", opening_seed_offset=int(args.policy_games),
        frozen=bool(int(args.frozen)), win_weight=float(args.win_weight),
    ) if int(args.explore_games) > 0 else None
    flip_policy_res = _run_match_batch(
        rng, engine_a, engine_b, int(flip_policy_games), "policy",
        white_engine=engine_b, black_engine=engine_a,
        max_plies=int(args.max_plies), eps_white=0.0, eps_black=0.0,
        explore_moves_white=0, explore_moves_black=0,
        pass_name="flip", opening_seed_offset=0,
        frozen=bool(int(args.frozen)), win_weight=float(args.win_weight),
    ) if flip_enabled and flip_policy_games > 0 else None
    flip_explore_res = _run_match_batch(
        rng, engine_a, engine_b, int(flip_explore_games), "explore",
        white_engine=engine_b, black_engine=engine_a,
        max_plies=int(args.max_plies), eps_white=float(args.eps_white), eps_black=float(args.eps_black),
        explore_moves_white=int(args.explore_moves_white), explore_moves_black=int(args.explore_moves_black),
        pass_name="flip", opening_seed_offset=int(flip_policy_games),
        frozen=bool(int(args.frozen)), win_weight=float(args.win_weight),
    ) if flip_enabled and flip_explore_games > 0 else None

    ok_policy = _db_write_episode(policy_res, kind=f"{matchup_kind}:policy_batch")
    ok_explore = True if explore_res is None else _db_write_episode(explore_res, kind=f"{matchup_kind}:explore_batch")
    ok_flip_policy = True if flip_policy_res is None else _db_write_episode(flip_policy_res, kind=f"{matchup_kind}:policy_batch_flip")
    ok_flip_explore = True if flip_explore_res is None else _db_write_episode(flip_explore_res, kind=f"{matchup_kind}:explore_batch_flip")

    rules_after_a = _namespace_rule_count(_ENGINE_CONFIGS[engine_a]["namespace"])
    rules_after_b = _namespace_rule_count(_ENGINE_CONFIGS[engine_b]["namespace"])

    out: Dict[str, Any] = {
        "ok": bool(ok_policy and ok_explore and ok_flip_policy and ok_flip_explore),
        "db_written": bool(ok_policy and ok_explore and ok_flip_policy and ok_flip_explore),
        "frozen_mode": bool(int(args.frozen)),
        "live_learning_enabled": bool(not int(args.frozen)),
        "matchup_kind": matchup_kind,
        "engine_a": engine_a,
        "engine_b": engine_b,
        "white_engine_normal": engine_a,
        "black_engine_normal": engine_b,
        "flip_pass_enabled": bool(flip_enabled),
        "white_engine_flip": engine_b if flip_enabled else "",
        "black_engine_flip": engine_a if flip_enabled else "",
        "policy_games": int(policy_res.get("games", 0) or 0),
        "explore_games": int((explore_res or {}).get("games", 0) or 0),
        "flip_policy_games": int((flip_policy_res or {}).get("games", 0) or 0),
        "flip_explore_games": int((flip_explore_res or {}).get("games", 0) or 0),
        "policy_white_wins": int(policy_res.get("wins_white", 0) or 0),
        "policy_black_wins": int(policy_res.get("wins_black", 0) or 0),
        "policy_draws": int(policy_res.get("draws", 0) or 0),
        "flip_policy_white_wins": int((flip_policy_res or {}).get("wins_white", 0) or 0),
        "flip_policy_black_wins": int((flip_policy_res or {}).get("wins_black", 0) or 0),
        "flip_policy_draws": int((flip_policy_res or {}).get("draws", 0) or 0),
        "engine_a_policy_wins_normal": int(policy_res.get("engine_a_wins", 0) or 0),
        "engine_b_policy_wins_normal": int(policy_res.get("engine_b_wins", 0) or 0),
        "engine_a_policy_wins_flip": int((flip_policy_res or {}).get("engine_a_wins", 0) or 0),
        "engine_b_policy_wins_flip": int((flip_policy_res or {}).get("engine_b_wins", 0) or 0),
        "engine_a_policy_wins_total": int((policy_res.get("engine_a_wins", 0) or 0) + ((flip_policy_res or {}).get("engine_a_wins", 0) or 0)),
        "engine_b_policy_wins_total": int((policy_res.get("engine_b_wins", 0) or 0) + ((flip_policy_res or {}).get("engine_b_wins", 0) or 0)),
        "policy_draws_total": int((policy_res.get("draws", 0) or 0) + ((flip_policy_res or {}).get("draws", 0) or 0)),
        "explore_white_wins": int((explore_res or {}).get("wins_white", 0) or 0),
        "explore_black_wins": int((explore_res or {}).get("wins_black", 0) or 0),
        "explore_draws": int((explore_res or {}).get("draws", 0) or 0),
        "flip_explore_white_wins": int((flip_explore_res or {}).get("wins_white", 0) or 0),
        "flip_explore_black_wins": int((flip_explore_res or {}).get("wins_black", 0) or 0),
        "flip_explore_draws": int((flip_explore_res or {}).get("draws", 0) or 0),
        "policy_avg_moves": float(policy_res.get("avg_moves", 0.0) or 0.0),
        "flip_policy_avg_moves": float((flip_policy_res or {}).get("avg_moves", 0.0) or 0.0),
        "explore_avg_moves": float((explore_res or {}).get("avg_moves", 0.0) or 0.0),
        "flip_explore_avg_moves": float((flip_explore_res or {}).get("avg_moves", 0.0) or 0.0),
        "rules_before_engine_a": int(rules_before_a),
        "rules_after_engine_a": int(rules_after_a),
        "rules_delta_engine_a": int(max(0, rules_after_a - rules_before_a)),
        "rules_before_engine_b": int(rules_before_b),
        "rules_after_engine_b": int(rules_after_b),
        "rules_delta_engine_b": int(max(0, rules_after_b - rules_before_b)),
        "opening_seed_games_normal_policy": int(policy_res.get("opening_seed_games", 0) or 0),
        "opening_seed_games_flip_policy": int((flip_policy_res or {}).get("opening_seed_games", 0) or 0),
        "snapchains_written": int((policy_res.get("chains_count", 0) or 0) + ((explore_res or {}).get("chains_count", 0) or 0) + ((flip_policy_res or {}).get("chains_count", 0) or 0) + ((flip_explore_res or {}).get("chains_count", 0) or 0)),
        "learn_items_engine_a_total": int((policy_res.get("learn_items_engine_a", 0) or 0) + ((explore_res or {}).get("learn_items_engine_a", 0) or 0) + ((flip_policy_res or {}).get("learn_items_engine_a", 0) or 0) + ((flip_explore_res or {}).get("learn_items_engine_a", 0) or 0)),
        "learn_items_engine_b_total": int((policy_res.get("learn_items_engine_b", 0) or 0) + ((explore_res or {}).get("learn_items_engine_b", 0) or 0) + ((flip_policy_res or {}).get("learn_items_engine_b", 0) or 0) + ((flip_explore_res or {}).get("learn_items_engine_b", 0) or 0)),
        "learn_items_total": int((policy_res.get("learn_items_count", 0) or 0) + ((explore_res or {}).get("learn_items_count", 0) or 0) + ((flip_policy_res or {}).get("learn_items_count", 0) or 0) + ((flip_explore_res or {}).get("learn_items_count", 0) or 0)),
    }
    for engine_name in (engine_a, engine_b):
        prefix = _engine_metric_prefix(engine_name)
        for batch in (policy_res, flip_policy_res):
            if isinstance(batch, dict):
                for k, v in batch.items():
                    if str(k).startswith(f"{prefix}_"):
                        out[str(k)] = out.get(str(k), 0) + v if isinstance(v, (int, float)) else v
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

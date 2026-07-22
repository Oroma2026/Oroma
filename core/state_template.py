#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/state_template.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   State Template Registry – Erfahrungswissen für professionelle Games
# Version: v0.2.0
# Stand:   2026-06-28
# Autor:   ORÓMA · Jörg Werner + GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul macht die in den professionellen ORÓMA-Game-Patches entstandenen
# State-Schablonen explizit. Es ist bewusst kein Generator, kein Auto-Patcher und
# keine Laufzeitabhängigkeit für bestehende Runner. Das Modul dient als kleine,
# lesbare Wissensbasis:
#
#   • Menschen sehen, welche Schablone für welche Domäne bewährt ist.
#   • Neue Domänen können gegen bekannte State-/Action-/Credit-Muster verglichen
#     werden, bevor Code geschrieben wird.
#   • Spätere Werkzeuge können dieses Register read-only importieren, ohne DB,
#     DeviceHub, UI, Kamera oder andere schwere ORÓMA-Subsysteme zu laden.
#
# DESIGN-GRENZEN
# --------------
# - Nur Python-Standardbibliothek; keine ORÓMA-internen Imports.
# - Keine Datenbankzugriffe, keine DBWriter-Nutzung, keine Dateischreibzugriffe.
# - Keine automatische Code-Erzeugung und kein Eingriff in bestehende Runner.
# - Menschliche Entscheidung bleibt verbindlich: find_best_match() liefert nur
#   Hinweise, keine Freigabe zum Patchen.
#
# TAXONOMIE
# ---------
# solved_game:
#   Endlicher, vollständig enumerierbarer Zustandsraum. Nach vollständiger
#   Abdeckung ist zufälliger Explore fachlich unnötig. Beispiel: TicTacToe.
#
# mechanic_solved:
#   Die Spielmechanik ist lernbar, aber konkrete Layouts/Situationen bleiben
#   variabel. Explore wird nach Verständnis reduziert, nicht vollständig beendet.
#   Beispiel: Memory.
#
# constraint_solved:
#   Constraint-Satisfaction ohne Gegner. Korrekte Techniken erzeugen nur positive
#   Lernsignale; neg=0/q=1.0 kann hier der richtige Zustand sein. Beispiel: Sudoku.
#
# tactical_learning:
#   Offene/taktische Domänen mit Gegner, Timing, Physik, Raum oder langfristiger
#   Strategie. Explore bleibt notwendig, muss aber über gute State-Abstraktion,
#   Event-Credit und Sicherheits-Gates kontrolliert werden.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class StateTemplate:
    """Beschreibt eine bewährte ORÓMA-State-Schablone.

    Die Klasse ist absichtlich klein und serialisierungsfreundlich. Alle Felder
    sind strings oder Tupel aus strings. Dadurch kann das Register später auch in
    UI, Docs, Tests oder CLI-Diagnosen benutzt werden, ohne Nebenwirkungen oder
    Import-Overhead zu erzeugen.
    """

    name: str
    category: str
    state_schema: str
    symmetry: str
    action_space: str
    explore_mode: str
    credit_type: str
    draw_wall_risk: str
    key_dimensions: Tuple[str, ...]
    action_schema: str = ""
    namespace: str = ""
    notes: str = ""
    family: str = ""
    aliases: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> Dict[str, object]:
        """Return a plain dict for JSON/debug output without importing json here."""
        return {
            "name": self.name,
            "category": self.category,
            "family": self.family,
            "state_schema": self.state_schema,
            "symmetry": self.symmetry,
            "action_space": self.action_space,
            "action_schema": self.action_schema,
            "explore_mode": self.explore_mode,
            "credit_type": self.credit_type,
            "draw_wall_risk": self.draw_wall_risk,
            "namespace": self.namespace,
            "key_dimensions": list(self.key_dimensions),
            "aliases": list(self.aliases),
            "notes": self.notes,
        }


# -----------------------------------------------------------------------------
# Normalisierung / Dimensionen
# -----------------------------------------------------------------------------


def _norm(value: object) -> str:
    """Normalize a human-entered dimension token for lightweight matching."""
    text = str(value or "").strip().lower()
    for ch in ("-", " ", ".", ":", "/"):
        text = text.replace(ch, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


# Manuell gepflegte Synonyme. Das ist keine Ontologie und keine Autoerkennung,
# sondern bewusst lesbares ORÓMA-Erfahrungswissen. Generic-Dimensionen wie
# "danger" sollen sinnvolle Template-Dimensionen treffen, ohne dass rohe Grids
# oder exakte Spielzustände verglichen werden.
_DIMENSION_ALIASES: Mapping[str, Tuple[str, ...]] = {
    # Navigation / Snake / HideSeek / CTF
    "danger": ("danger_front", "danger_left", "danger_right", "collision_risk", "wall_risk", "local_safety"),
    "danger_front": ("danger", "collision_risk", "front_blocked"),
    "danger_left": ("danger", "collision_risk", "left_blocked"),
    "danger_right": ("danger", "collision_risk", "right_blocked"),
    "danger_up": ("danger", "collision_risk", "z_axis", "vertical_danger"),
    "danger_down": ("danger", "collision_risk", "z_axis", "vertical_danger"),
    "food_direction": ("food_fwd", "food_right", "target_direction", "direction_to_food", "goal_direction"),
    "food_fwd": ("food_direction", "target_direction", "direction_to_food"),
    "food_right": ("food_direction", "target_direction", "direction_to_food"),
    "z_axis": ("vertical_axis", "danger_up", "danger_down", "food_z_direction", "z_direction"),
    "target_direction": ("food_direction", "goal_direction", "bfs_target_action", "target_dir"),
    "target_dist": ("distance_to_target", "nearest_dist", "visible_dist", "dist_bucket"),
    "target_dir": ("target_direction", "direction_to_target", "bfs_target_action"),
    "bfs_target_action": ("target_direction", "target_dir", "pathfinding"),

    # Grid/board tactics
    "phase": ("game_phase", "opening_mid_late", "progress_bucket"),
    "legal_mask": ("legal_actions", "action_mask", "legal_moves"),
    "mobility": ("legal_mask", "legal_moves", "action_count"),
    "threat": ("winning_moves", "opponent_winning_moves", "open3", "fork"),
    "winning_moves": ("immediate_win", "threat", "own_winning_moves"),
    "opponent_winning_moves": ("block", "threat", "opp_winning_moves"),
    "center_control": ("center_balance", "centrality", "middle_control"),
    "height_profile": ("column_heights", "skyline", "board_profile"),

    # Memory / card mechanics
    "known_pairs": ("pair_memory", "matched_pair_available", "pair_reuse"),
    "known_positions": ("memory_positions", "known_cards", "known_singles"),
    "unknown_count": ("unknown_cards", "hidden_positions", "unseen_count"),
    "pair_reuse": ("known_pairs", "memory_hits", "matched_pair_available"),

    # Sudoku / constraints
    "constraint": ("candidates", "dead_cells", "min_candidates", "avg_candidates"),
    "candidates": ("min_candidates", "avg_candidates", "candidate_count"),
    "naked_single": ("single_candidate", "technique", "constraint"),
    "hidden_single_row": ("hidden_single", "row_constraint", "technique"),
    "hidden_single_col": ("hidden_single", "column_constraint", "technique"),
    "hidden_single_box": ("hidden_single", "box_constraint", "technique"),
    "technique": ("naked_single", "hidden_single_row", "hidden_single_col", "hidden_single_box"),

    # Arcade/physics
    "velocity": ("vy", "vx", "speed", "motion"),
    "gap_direction": ("dy_to_gap", "gap_y", "target_window"),
    "clearance": ("risk_margin", "gap_margin", "safe_window"),
    "survival": ("survival_bucket", "steps_bucket", "timeout_risk"),

    # Chess/strategic
    "material": ("material_balance", "material_cp"),
    "king_safety": ("king_attackers", "king_pressure"),
    "rule_hits": ("professional_rules", "shape_signals", "strategic_features"),
}


def _expanded_terms(term: object) -> Tuple[str, ...]:
    """Return normalized term plus manually known aliases."""
    base = _norm(term)
    if not base:
        return tuple()
    out = {base}
    for alias in _DIMENSION_ALIASES.get(base, tuple()):
        a = _norm(alias)
        if a:
            out.add(a)
    return tuple(sorted(out))


def _template_terms(template: StateTemplate) -> Dict[str, Tuple[str, ...]]:
    """Map each explicit template dimension to its normalized alias set."""
    out: Dict[str, Tuple[str, ...]] = {}
    for dim in template.key_dimensions:
        out[_norm(dim)] = _expanded_terms(dim)
    for alias in template.aliases:
        # aliases are searchable but not listed as primary key_dimensions
        out.setdefault(_norm(alias), _expanded_terms(alias))
    return out


# -----------------------------------------------------------------------------
# Register
# -----------------------------------------------------------------------------


def _template(
    *,
    name: str,
    category: str,
    state_schema: str,
    family: str = "",
    symmetry: str,
    action_space: str,
    explore_mode: str,
    credit_type: str,
    draw_wall_risk: str,
    key_dimensions: Sequence[str],
    action_schema: str = "",
    namespace: str = "",
    notes: str = "",
    aliases: Sequence[str] = (),
) -> StateTemplate:
    return StateTemplate(
        name=str(name),
        category=str(category),
        state_schema=str(state_schema),
        family=str(family),
        symmetry=str(symmetry),
        action_space=str(action_space),
        action_schema=str(action_schema),
        explore_mode=str(explore_mode),
        credit_type=str(credit_type),
        draw_wall_risk=str(draw_wall_risk),
        namespace=str(namespace),
        key_dimensions=tuple(str(x) for x in key_dimensions),
        aliases=tuple(str(x) for x in aliases),
        notes=str(notes),
    )


registry: Dict[str, StateTemplate] = {
    "tictactoe:pro_v2": _template(
        name="TicTacToe pro_v2",
        category="solved_game",
        family="board_tactics",
        state_schema="tictactoe:pro_v2",
        namespace="game:tictactoe",
        symmetry="d4",
        action_space="absolute",
        action_schema="canon_d4_9",
        explore_mode="none",
        credit_type="solver",
        draw_wall_risk="low",
        key_dimensions=(
            "phase",
            "canonical_board",
            "side_to_move",
            "legal_mask",
            "terminal_state",
            "minimax_outcome",
            "optimal_action_set",
        ),
        aliases=("finite_board", "full_enumeration", "no_more_explore"),
        notes="Vollständig enumerierbarer Zustandsraum; nach Coverage no_more_explore=1.",
    ),
    "memory:pro_v2": _template(
        name="Memory pro_v2",
        category="mechanic_solved",
        family="memory_reuse",
        state_schema="memory:pro_v2",
        namespace="game:memory",
        symmetry="none",
        action_space="high_level",
        action_schema="high_level_memory_5",
        explore_mode="reduced",
        credit_type="event_based",
        draw_wall_risk="low",
        key_dimensions=(
            "phase",
            "pairs_left",
            "known_pairs",
            "known_singles",
            "known_positions",
            "unknown_count",
            "legal_mask",
            "score_delta_bucket",
            "turn_balance_bucket",
        ),
        aliases=("pair_reuse", "memory_hits", "mechanic_understood"),
        notes="Prinzip verstanden, Layout variabel; Explore wird reduziert, nicht beendet.",
    ),
    "sudoku:pro_v2": _template(
        name="Sudoku pro_v2",
        category="constraint_solved",
        family="constraint_solver",
        state_schema="sudoku:pro_v2",
        namespace="game:sudoku",
        symmetry="none",
        action_space="technique",
        action_schema="technique_5",
        explore_mode="reduced",
        credit_type="technique",
        draw_wall_risk="low",
        key_dimensions=(
            "difficulty",
            "empty_count",
            "givens",
            "naked_single",
            "hidden_single_row",
            "hidden_single_col",
            "hidden_single_box",
            "min_candidates",
            "avg_candidates",
            "dead_cells",
        ),
        aliases=("constraint", "candidates", "solution_guard", "mechanic_understood"),
        notes="Constraint-Satisfaction: nur logisch korrekte Techniken, neg=0/q=1.0 ist plausibel.",
    ),
    "snake:pro_v2": _template(
        name="Snake pro_v2",
        category="tactical_learning",
        family="navigation",
        state_schema="snake:pro_v2",
        namespace="game:snake",
        symmetry="perspective",
        action_space="relative",
        action_schema="relative_turn_3",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="medium",
        key_dimensions=(
            "danger_front",
            "danger_left",
            "danger_right",
            "food_fwd",
            "food_right",
            "dist_bucket",
            "len_bucket",
            "space_bucket",
            "hunger_bucket",
        ),
        aliases=("danger", "food_direction", "navigation", "local_safety", "flood_space"),
        notes="Relativer egozentrischer Navigations-State; gute Basis für 2D/3D-Snake-Erweiterungen.",
    ),
    "snake3d:pro_v1": _template(
        name="Snake3D pro_v1",
        category="tactical_learning",
        family="navigation",
        state_schema="snake3d:pro_v1",
        namespace="game:snake3d",
        symmetry="perspective",
        action_space="relative3d_5",
        action_schema="relative3d_5",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="medium",
        key_dimensions=(
            "danger_front",
            "danger_left",
            "danger_right",
            "danger_up",
            "danger_down",
            "food_fwd",
            "food_right",
            "food_z_direction",
            "dist_bucket",
            "len_bucket",
            "space_bucket",
            "hunger_bucket",
            "relative3d_action",
        ),
        aliases=(
            "danger",
            "food_direction",
            "food_up",
            "navigation",
            "local_safety",
            "flood_space",
            "z_axis",
            "spatial_navigation_z_extension",
        ),
        notes="Validierte 3D-Navigation; basiert auf snake:pro_v2 + spatial_navigation_z_extension. Keine generische z_extension-Schablone.",
    ),
    "flappy:pro_v3": _template(
        name="Flappy pro_v3",
        category="tactical_learning",
        family="arcade_physics",
        state_schema="flappy:pro_v3",
        namespace="game:flappy",
        symmetry="none",
        action_space="absolute",
        action_schema="binary_flap_hold",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="medium",
        key_dimensions=(
            "dy_to_gap",
            "vy",
            "dx_to_pipe",
            "clearance",
            "gap_height",
            "near_pipe",
            "danger_top",
            "danger_bottom",
            "score_bucket",
            "survival_bucket",
        ),
        aliases=("gap_direction", "velocity", "vertical_control", "physics"),
        notes="Kontinuierliche Physik in groben Buckets; Death-/Survival-Credit statt neutraler Step-Wand.",
    ),
    "ctf:pro_v2": _template(
        name="Capture the Flag pro_v2",
        category="tactical_learning",
        family="navigation",
        state_schema="ctf:pro_v2",
        namespace="game:ctf",
        symmetry="perspective",
        action_space="relative",
        action_schema="local_canonical_5",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="high",
        key_dimensions=(
            "phase",
            "target_dist",
            "target_direction",
            "opponent_dist",
            "opponent_direction",
            "score_diff_bucket",
            "local_lane_x",
            "local_lane_y",
        ),
        aliases=("raid", "return", "defend", "lane", "navigation"),
        notes="Side-symmetrischer Team-/Gegner-State; Event-Credit nötig, Step-Reward darf nicht dominieren.",
    ),
    "connect4:pro_v2": _template(
        name="Connect4 pro_v2",
        category="tactical_learning",
        family="board_tactics",
        state_schema="connect4:pro_v2",
        namespace="game:connect4",
        symmetry="horizontal_mirror",
        action_space="absolute",
        action_schema="mirror_canon_col_7",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="high",
        key_dimensions=(
            "phase",
            "own_winning_moves",
            "opponent_winning_moves",
            "own_win_mask",
            "opponent_win_mask",
            "own_open3",
            "opponent_open3",
            "own_open2",
            "opponent_open2",
            "height_profile",
            "center_control",
            "fill_balance_left_mid",
            "fill_balance_right_mid",
        ),
        aliases=("threat", "block", "fork", "column_game", "center_balance"),
        notes="Theoretisch gelöst, praktisch tactical_learning auf Pi; Mirror-Kanonisierung reduziert Varianz.",
    ),
    "hideseek:pro_v2": _template(
        name="HideSeek pro_v2",
        category="tactical_learning",
        family="navigation",
        state_schema="hideseek:pro_v2",
        namespace="game:hideseek",
        symmetry="none",
        action_space="absolute",
        action_schema="dir4_abs",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="high",
        key_dimensions=(
            "phase",
            "remaining_hiders",
            "found_count",
            "step_ratio",
            "legal_mask",
            "wall_mask",
            "mobility",
            "visible_hider",
            "target_dir",
            "visible_dist",
            "nearest_dist",
            "bfs_target_action",
            "hider_quadrants",
            "wall_density",
        ),
        aliases=("navigation", "pathfinding", "visibility", "maze", "search"),
        notes="Raumsuche mit BFS-Fallback; professionelle Policy darf Pfadlogik nicht durch Draw-Wand überlagern.",
    ),
    "pong:v1": _template(
        name="Pong v1 fixed-outcome",
        category="tactical_learning",
        family="arcade_physics",
        state_schema="pong:v1",
        namespace="game:pong",
        symmetry="perspective",
        action_space="absolute",
        action_schema="paddle_delta_3",
        explore_mode="full",
        credit_type="terminal_only",
        draw_wall_risk="medium",
        key_dimensions=(
            "side",
            "ball_x_bucket",
            "ball_y_bucket",
            "ball_vx_sign",
            "ball_vy_sign",
            "left_paddle_y_bucket",
            "right_paddle_y_bucket",
        ),
        aliases=("tracking", "paddle_control", "velocity", "rally"),
        notes="Kontinuierliche Ball-/Paddle-Domäne; Outcome-Fix schließt alten neutralen Lernloop.",
    ),
    "tetris:pro_v3": _template(
        name="Tetris pro_v3",
        category="tactical_learning",
        family="board_profile",
        state_schema="tetris:pro_v3",
        namespace="game:tetris",
        symmetry="none",
        action_space="high_level",
        action_schema="placement_candidate",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="medium",
        key_dimensions=(
            "current_piece",
            "next_piece_group",
            "aggregate_height",
            "max_height",
            "holes",
            "bumpiness",
            "wells",
            "top_danger",
            "skyline_profile",
        ),
        aliases=("height_profile", "board_profile", "placement", "line_clear"),
        notes="Kandidatenbasierte Platzierungsdomäne; Guard verhindert offensichtlich schlechte Lock-Ins.",
    ),
    "memorymaze_hybrid:reuse_v2": _template(
        name="MemoryMaze Hybrid reuse_v2",
        category="tactical_learning",
        family="navigation_memory_hybrid",
        state_schema="memorymaze_hybrid:reuse_v2",
        namespace="game:memorymaze_hybrid",
        symmetry="perspective",
        action_space="absolute",
        action_schema="maze_actions_claim_reveal_move",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="high",
        key_dimensions=(
            "mode",
            "player",
            "phase",
            "pairs_left_bucket",
            "has_target",
            "ahead_object_type",
            "target_dr",
            "target_dc",
            "target_dist",
            "my_strikes_bucket",
            "opponent_strikes_bucket",
        ),
        aliases=("memory", "maze", "claim", "pair_reuse", "navigation"),
        notes="Hybrid aus Memory und Maze; noch tactical_learning, weil Bewegung, Claims und Gegner/Strikes koppeln.",
    ),
    "chess_pro:v0.2": _template(
        name="ChessPro v0.2",
        category="tactical_learning",
        family="strategy_search",
        state_schema="chess_pro:v0.2",
        namespace="game:chess_pro",
        symmetry="perspective",
        action_space="absolute",
        action_schema="legal_chess_move",
        explore_mode="full",
        credit_type="event_based",
        draw_wall_risk="high",
        key_dimensions=(
            "stable_fen",
            "side_to_move",
            "focus_side",
            "material_balance",
            "mobility",
            "rule_hits",
            "phase",
            "passed_pawns",
            "king_safety",
            "search_depth",
            "terminal_score_cp",
        ),
        aliases=("strategy", "material", "king_safety", "professional_rules", "long_search"),
        notes="Positionsorientierte Langsuch-Domäne; Policy-Lernen darf Suche/Regeln nur ergänzen, nicht ersetzen.",
    ),
}


# -----------------------------------------------------------------------------
# Vergleich / Lückenanalyse
# -----------------------------------------------------------------------------


def _match_one_dimension(request_dim: str, template: StateTemplate) -> Tuple[bool, Tuple[str, ...]]:
    """Return whether request_dim is covered and by which template dimensions."""
    req_terms = set(_expanded_terms(request_dim))
    if not req_terms:
        return False, tuple()

    hits: List[str] = []
    for dim, dim_terms in _template_terms(template).items():
        if req_terms.intersection(dim_terms):
            hits.append(dim)
    return bool(hits), tuple(sorted(set(hits)))


def _score_template(
    domain: str,
    dimensions: Sequence[str],
    template: StateTemplate,
    category_hint: Optional[str] = None,
    family_hint: Optional[str] = None,
) -> Tuple[float, Dict[str, List[str]], List[str]]:
    """Compute a lightweight similarity score for one template.

    The score is intentionally transparent and conservative:
      - most weight comes from requested dimension coverage;
      - domain/category/family hints only break ties or narrow ranking;
      - missing dimensions are reported as gaps instead of being hidden.
    """
    dims = [str(d) for d in dimensions if _norm(d)]
    matched: Dict[str, List[str]] = {}
    gaps: List[str] = []

    for d in dims:
        ok, hits = _match_one_dimension(d, template)
        if ok:
            matched[str(d)] = list(hits)
        else:
            gaps.append(str(d))

    coverage = (len(matched) / float(len(dims))) if dims else 0.0
    score = coverage * 100.0

    domain_terms = set(_expanded_terms(domain))
    template_terms = set()
    for v in (template.name, template.category, template.family, template.state_schema, template.action_space, template.credit_type, template.symmetry):
        template_terms.update(_expanded_terms(v))
    for alias in template.aliases:
        template_terms.update(_expanded_terms(alias))
    for dim in template.key_dimensions:
        template_terms.update(_expanded_terms(dim))

    domain_overlap = len(domain_terms.intersection(template_terms))
    score += min(10.0, float(domain_overlap) * 2.0)

    if category_hint and _norm(category_hint) == _norm(template.category):
        score += 5.0
    if family_hint and _norm(family_hint) == _norm(template.family):
        score += 7.0

    # Prefer compact templates when coverage is identical. This prevents large
    # strategic templates from winning only because they contain many generic words.
    score -= min(3.0, len(template.key_dimensions) * 0.03)
    return round(score, 3), matched, gaps


def find_best_match(
    domain: str,
    dimensions: Sequence[str],
    category_hint: Optional[str] = None,
    family_hint: Optional[str] = None,
    top_n: int = 3,
) -> Dict[str, object]:
    """Find the closest existing StateTemplate for a new domain.

    Parameters
    ----------
    domain:
        Human-readable domain name such as "3D-Snake", "navigation" or
        "constraint puzzle". This is a weak hint only.
    dimensions:
        Candidate state dimensions for the new domain. They may be generic
        ("danger", "food_direction") or specific ("danger_up").
    category_hint:
        Optional taxonomy hint, e.g. "tactical_learning".
    family_hint:
        Optional family filter/hint, e.g. "navigation". If at least one
        template belongs to the requested family, ranking is restricted to this
        family and exact family matches receive a small score bonus.
    top_n:
        Number of ranked alternatives to include.

    Returns
    -------
    dict
        Plain, print-friendly result with best template, matched dimensions and
        gaps. The function never modifies registry, DB or files.

    Example
    -------
    >>> find_best_match('navigation', ['danger', 'food_direction', 'z_axis'])['best_template']
    'snake3d:pro_v1'
    >>> find_best_match('snake', ['danger', 'food_direction'])['best_template']
    'snake:pro_v2'
    """
    if isinstance(dimensions, str):
        dims = [dimensions]
    else:
        dims = [str(d) for d in dimensions]

    templates = list(registry.items())
    if family_hint:
        family_filtered = [(key, template) for key, template in templates if _norm(template.family) == _norm(family_hint)]
        if family_filtered:
            templates = family_filtered

    ranked: List[Tuple[float, str, StateTemplate, Dict[str, List[str]], List[str]]] = []
    for key, template in templates:
        score, matched, gaps = _score_template(
            str(domain),
            dims,
            template,
            category_hint=category_hint,
            family_hint=family_hint,
        )
        ranked.append((score, key, template, matched, gaps))

    ranked.sort(key=lambda x: (x[0], len(x[3]), -len(x[4])), reverse=True)
    best_score, best_key, best_template, best_matched, best_gaps = ranked[0]

    alternatives: List[Dict[str, object]] = []
    for score, key, template, matched, gaps in ranked[: max(1, int(top_n or 1))]:
        alternatives.append({
            "state_schema": key,
            "name": template.name,
            "category": template.category,
            "family": template.family,
            "score": score,
            "matched_count": len(matched),
            "gap_count": len(gaps),
        })

    recommendation = _recommendation_text(best_template, best_gaps)
    return {
        "domain": str(domain),
        "requested_dimensions": dims,
        "category_hint": str(category_hint) if category_hint else "",
        "family_hint": str(family_hint) if family_hint else "",
        "best_template": best_key,
        "best_name": best_template.name,
        "category": best_template.category,
        "family": best_template.family,
        "state_schema": best_template.state_schema,
        "symmetry": best_template.symmetry,
        "action_space": best_template.action_space,
        "explore_mode": best_template.explore_mode,
        "credit_type": best_template.credit_type,
        "draw_wall_risk": best_template.draw_wall_risk,
        "score": best_score,
        "matched_dimensions": best_matched,
        "gaps": best_gaps,
        "recommendation": recommendation,
        "alternatives": alternatives,
    }


def _recommendation_text(template: StateTemplate, gaps: Sequence[str]) -> str:
    """Small human-readable recommendation derived from a match result."""
    if not gaps:
        return f"{template.state_schema} passt direkt als Schablone; menschliche Prüfung bleibt erforderlich."
    if template.state_schema == "snake:pro_v2" and any(_norm(g) in {"z_axis", "vertical_axis", "danger_up", "danger_down", "food_z_direction"} for g in gaps):
        return "snake:pro_v2 als Basis verwenden; Z-Achse als neue Dimension ergänzen (danger_up/down, food_z_direction)."
    return f"{template.state_schema} als Basis prüfen; offene Dimensionen ergänzen: {', '.join(str(g) for g in gaps)}."


def analyze_gap(template_key: str, dimensions: Sequence[str]) -> Dict[str, object]:
    """Return coverage/gaps for a specific template without ranking others."""
    key = str(template_key)
    if key not in registry:
        raise KeyError(f"unknown state template: {template_key!r}")
    template = registry[key]
    score, matched, gaps = _score_template("", [str(d) for d in dimensions], template)
    return {
        "template": key,
        "name": template.name,
        "family": template.family,
        "score": score,
        "matched_dimensions": matched,
        "gaps": gaps,
        "template_dimensions": list(template.key_dimensions),
    }


def list_templates(category: Optional[str] = None, family: Optional[str] = None) -> List[Dict[str, object]]:
    """Return registered templates as plain dictionaries, optionally filtered."""
    out: List[Dict[str, object]] = []
    for template in registry.values():
        if category and _norm(category) != _norm(template.category):
            continue
        if family and _norm(family) != _norm(template.family):
            continue
        out.append(template.as_dict())
    return out


__all__ = [
    "StateTemplate",
    "registry",
    "find_best_match",
    "analyze_gap",
    "list_templates",
]

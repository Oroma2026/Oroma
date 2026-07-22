#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/sudoku_daily_runner.py
# Projekt: ORÓMA – Games / Professional State Templates
# Modul:   Sudoku Daily Runner – pro_v2 Mechanic-Solved Constraint Runner
# Version: v1.0-pro_v2
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt Sudoku headless aus und ergänzt die bisher reine UI-Funktion
#   (Generator/Check/Hint) um einen produktiven Lernloop:
#
#       namespace:     game:sudoku
#       state_schema:  sudoku:pro_v2
#       action_schema: technique_5
#
#   Sudoku wird hier bewusst NICHT wie ein endlos explorierendes Arcade-Spiel
#   behandelt. Es gehört – ähnlich wie Memory, aber anders als TicTacToe – zur
#   Kategorie "mechanic_solved": Die Regel-/Constraint-Mechanik ist lernbar,
#   konkrete Puzzles bleiben variabel. Sobald genügend pro_v2-Samples vorhanden
#   sind, reduziert der Runner Explore, stoppt ihn aber nicht vollständig:
#
#       mechanic_understood=1, explore_reduced=1, no_more_explore=0
#
# Professionelle Sudoku-Mechanik
# ------------------------------
#   Der Runner löst Puzzles mit menschlich erklärbaren Techniken:
#
#       0 = naked_single       Zelle hat nur einen Kandidaten
#       1 = hidden_single_row  Zahl kommt in einer Reihe nur einmal als Kandidat vor
#       2 = hidden_single_col  Zahl kommt in einer Spalte nur einmal als Kandidat vor
#       3 = hidden_single_box  Zahl kommt in einer 3x3-Box nur einmal als Kandidat vor
#       4 = solution_guard     abgesicherter Fallback über bekannte Lösung
#
#   Der solution_guard ist kein "freies Raten". Er wird sichtbar gezählt und nur
#   verwendet, wenn einfache Constraint-Techniken nicht weiterkommen. Dadurch
#   bleibt der Runner robust für alle generierten eindeutigen Puzzles, ohne in
#   Endlosschleifen oder Random-Explore zu fallen.
#
# Lernloop / Policy
# -----------------
#   Policy-Regeln werden als Technik-Entscheidungen gelernt, nicht als rohe
#   9x9-Grid-Zustände. Der State-Hash enthält Puzzle- und Constraint-Merkmale:
#   Phase, leere Zellen, Kandidaten-Dichte, vorhandene Singles/Hidden-Singles,
#   Min-Kandidaten-Bucket, Schwierigkeit und Givens-Bucket.
#
#   Lernen ist ereignisbasiert:
#       • logischer Fortschritt  -> positiv
#       • solution_guard         -> klein positiv/assistiert sichtbar
#       • final gelöst           -> kurzes terminales Kreditfenster positiv
#       • neutrale Zustände      -> kein Draw-Müll
#
# DB-/Write-Disziplin
# -------------------
#   policy_rules werden ausschließlich über core.db_writer_client.executemany()
#   geschrieben. Es gibt keinen lokalen SQLite-Direktwrite-Fallback für den
#   verwalteten Policy-Pfad. Wenn DBWriter nicht erreichbar ist, bleibt das
#   sichtbar: policy_learn_ok=false, learned_items=0.
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/sudoku_daily_runner.py \
#     --policy-games 10 --explore-games 10 --seed "$(date +%s)" --namespace game:sudoku
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:
    from core import sudoku_game
except Exception as e:  # pragma: no cover - import failure is visible at runtime
    sudoku_game = None  # type: ignore
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore


STATE_SCHEMA = "sudoku:pro_v2"
ACTION_SCHEMA = "technique_5"
DEFAULT_NAMESPACE = "game:sudoku"

ACTION_NAKED_SINGLE = 0
ACTION_HIDDEN_ROW = 1
ACTION_HIDDEN_COL = 2
ACTION_HIDDEN_BOX = 3
ACTION_SOLUTION_GUARD = 4
ACTION_NAMES = {
    ACTION_NAKED_SINGLE: "naked_single",
    ACTION_HIDDEN_ROW: "hidden_single_row",
    ACTION_HIDDEN_COL: "hidden_single_col",
    ACTION_HIDDEN_BOX: "hidden_single_box",
    ACTION_SOLUTION_GUARD: "solution_guard",
}

Grid = List[List[int]]


def _now_ts() -> int:
    return int(time.time())


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _bucket_int(value: int, limits: Sequence[int]) -> int:
    v = int(value)
    for idx, lim in enumerate(limits):
        if v <= int(lim):
            return idx
    return len(limits)


def _copy_grid(g: Grid) -> Grid:
    return [list(map(int, row)) for row in g]


def _box_id(r: int, c: int) -> int:
    return (int(r) // 3) * 3 + (int(c) // 3)


def _phase(empty_count: int) -> str:
    e = int(empty_count)
    if e > 54:
        return "early"
    if e > 28:
        return "mid"
    if e > 0:
        return "late"
    return "done"


def _valid_value(grid: Grid, r: int, c: int, v: int) -> bool:
    vi = int(v)
    if vi < 1 or vi > 9:
        return False
    for i in range(9):
        if i != int(c) and int(grid[int(r)][i]) == vi:
            return False
        if i != int(r) and int(grid[i][int(c)]) == vi:
            return False
    br = (int(r) // 3) * 3
    bc = (int(c) // 3) * 3
    for rr in range(br, br + 3):
        for cc in range(bc, bc + 3):
            if (rr != int(r) or cc != int(c)) and int(grid[rr][cc]) == vi:
                return False
    return True


def _candidates(grid: Grid, r: int, c: int) -> Set[int]:
    if int(grid[int(r)][int(c)]) != 0:
        return set()
    return {v for v in range(1, 10) if _valid_value(grid, int(r), int(c), v)}


@dataclass(frozen=True)
class Move:
    r: int
    c: int
    value: int
    action: int
    technique: str
    candidates_before: int


@dataclass
class BoardFeatures:
    empty_count: int
    filled_count: int
    givens: int
    naked_singles: int
    hidden_row: int
    hidden_col: int
    hidden_box: int
    min_candidates: int
    max_candidates: int
    avg_candidates: float
    dead_cells: int


def _all_candidate_map(grid: Grid) -> Dict[Tuple[int, int], Set[int]]:
    out: Dict[Tuple[int, int], Set[int]] = {}
    for r in range(9):
        for c in range(9):
            if int(grid[r][c]) == 0:
                out[(r, c)] = _candidates(grid, r, c)
    return out


def _find_naked_singles(grid: Grid) -> List[Move]:
    out: List[Move] = []
    for (r, c), cand in _all_candidate_map(grid).items():
        if len(cand) == 1:
            v = int(next(iter(cand)))
            out.append(Move(r, c, v, ACTION_NAKED_SINGLE, ACTION_NAMES[ACTION_NAKED_SINGLE], 1))
    return sorted(out, key=lambda m: (m.r, m.c, m.value))


def _hidden_single_units(grid: Grid, unit: str) -> List[Move]:
    cand_map = _all_candidate_map(grid)
    out: List[Move] = []
    units: List[List[Tuple[int, int]]] = []
    if unit == "row":
        units = [[(r, c) for c in range(9)] for r in range(9)]
        action = ACTION_HIDDEN_ROW
    elif unit == "col":
        units = [[(r, c) for r in range(9)] for c in range(9)]
        action = ACTION_HIDDEN_COL
    else:
        units = [[(r, c) for r in range(br, br + 3) for c in range(bc, bc + 3)] for br in (0, 3, 6) for bc in (0, 3, 6)]
        action = ACTION_HIDDEN_BOX

    for cells in units:
        for v in range(1, 10):
            hits = [(r, c) for (r, c) in cells if (r, c) in cand_map and v in cand_map[(r, c)]]
            if len(hits) == 1:
                r, c = hits[0]
                out.append(Move(r, c, int(v), action, ACTION_NAMES[action], len(cand_map.get((r, c), set()))))
    # de-duplicate: one cell/value may be hidden in multiple unit scans; keep first deterministic entry
    seen: Set[Tuple[int, int, int, int]] = set()
    uniq: List[Move] = []
    for m in sorted(out, key=lambda x: (x.r, x.c, x.value, x.action)):
        key = (m.r, m.c, m.value, m.action)
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    return uniq


def _solution_guard_moves(grid: Grid, solution: Grid) -> List[Move]:
    cand_map = _all_candidate_map(grid)
    cells = sorted(cand_map.items(), key=lambda kv: (len(kv[1]) if kv[1] else 99, kv[0][0], kv[0][1]))
    out: List[Move] = []
    for (r, c), cand in cells:
        if not cand:
            continue
        v = int(solution[r][c])
        if v in cand:
            out.append(Move(r, c, v, ACTION_SOLUTION_GUARD, ACTION_NAMES[ACTION_SOLUTION_GUARD], len(cand)))
    return out


def _moves_for_action(grid: Grid, solution: Grid, action: int) -> List[Move]:
    a = int(action)
    if a == ACTION_NAKED_SINGLE:
        return _find_naked_singles(grid)
    if a == ACTION_HIDDEN_ROW:
        return _hidden_single_units(grid, "row")
    if a == ACTION_HIDDEN_COL:
        return _hidden_single_units(grid, "col")
    if a == ACTION_HIDDEN_BOX:
        return _hidden_single_units(grid, "box")
    if a == ACTION_SOLUTION_GUARD:
        return _solution_guard_moves(grid, solution)
    return []


def _features(grid: Grid, givens: int) -> BoardFeatures:
    cand_map = _all_candidate_map(grid)
    empty_count = len(cand_map)
    sizes = [len(c) for c in cand_map.values()]
    return BoardFeatures(
        empty_count=int(empty_count),
        filled_count=int(81 - empty_count),
        givens=int(givens),
        naked_singles=len(_find_naked_singles(grid)),
        hidden_row=len(_hidden_single_units(grid, "row")),
        hidden_col=len(_hidden_single_units(grid, "col")),
        hidden_box=len(_hidden_single_units(grid, "box")),
        min_candidates=int(min(sizes) if sizes else 0),
        max_candidates=int(max(sizes) if sizes else 0),
        avg_candidates=float(sum(sizes) / float(max(1, len(sizes)))) if sizes else 0.0,
        dead_cells=sum(1 for s in sizes if int(s) == 0),
    )


def _state_hash(grid: Grid, givens: int, difficulty: str) -> str:
    f = _features(grid, int(givens))
    avg_bucket = _bucket_int(int(round(float(f.avg_candidates) * 10.0)), (15, 20, 25, 30, 40, 60, 90))
    parts = [
        STATE_SCHEMA,
        f"diff={(difficulty or 'medium').lower()}",
        f"ph={_phase(f.empty_count)}",
        f"e={_bucket_int(f.empty_count, (0, 8, 16, 28, 40, 52, 64))}",
        f"giv={_bucket_int(f.givens, (17, 24, 30, 36, 45, 60))}",
        f"ns={_bucket_int(f.naked_singles, (0, 1, 2, 4, 8, 16))}",
        f"hr={_bucket_int(f.hidden_row, (0, 1, 2, 4, 8, 16))}",
        f"hc={_bucket_int(f.hidden_col, (0, 1, 2, 4, 8, 16))}",
        f"hb={_bucket_int(f.hidden_box, (0, 1, 2, 4, 8, 16))}",
        f"minc={_bucket_int(f.min_candidates, (0, 1, 2, 3, 4, 6, 9))}",
        f"avgc={avg_bucket}",
        f"dead={_bucket_int(f.dead_cells, (0, 1, 2, 4, 8))}",
    ]
    return "|".join(parts)


def _applicable_actions(grid: Grid, solution: Grid) -> Set[int]:
    out: Set[int] = set()
    for a in (ACTION_NAKED_SINGLE, ACTION_HIDDEN_ROW, ACTION_HIDDEN_COL, ACTION_HIDDEN_BOX):
        if _moves_for_action(grid, solution, a):
            out.add(a)
    if _solution_guard_moves(grid, solution):
        out.add(ACTION_SOLUTION_GUARD)
    return out


def _fallback_action(grid: Grid, solution: Grid) -> int:
    for a in (ACTION_NAKED_SINGLE, ACTION_HIDDEN_ROW, ACTION_HIDDEN_COL, ACTION_HIDDEN_BOX):
        if _moves_for_action(grid, solution, a):
            return a
    return ACTION_SOLUTION_GUARD


@dataclass
class PolicyStats:
    seen: int = 0
    accepted: int = 0
    fallback: int = 0
    rejected_n: int = 0
    rejected_q: int = 0
    rejected_unsafe: int = 0


def _read_policy_candidates(namespace: str, state_hash: str) -> List[Tuple[int, float, int]]:
    out: List[Tuple[int, float, int]] = []
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                rows = conn.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=? ORDER BY q DESC, n DESC LIMIT 12",
                    (str(namespace), str(state_hash)),
                ).fetchall()
            for r in rows:
                try:
                    action = int(r["action"] if hasattr(r, "keys") else r[0])
                    q = float(r["q"] if hasattr(r, "keys") else r[1])
                    n = int(r["n"] if hasattr(r, "keys") else r[2])
                    out.append((action, q, n))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _policy_choose(grid: Grid, solution: Grid, namespace: str, state_hash: str, stats: PolicyStats, rng: random.Random) -> int:
    fallback = int(_fallback_action(grid, solution))
    candidates = _read_policy_candidates(namespace, state_hash)
    if not candidates:
        stats.fallback += 1
        return fallback
    stats.seen += 1
    q_min = _env_float("OROMA_SUDOKU_POLICY_ACCEPT_Q_MIN", 0.10)
    n_min = _env_int("OROMA_SUDOKU_POLICY_ACCEPT_MIN_N", 1)
    applicable = _applicable_actions(grid, solution)
    logic_available = any(a in applicable for a in (ACTION_NAKED_SINGLE, ACTION_HIDDEN_ROW, ACTION_HIDDEN_COL, ACTION_HIDDEN_BOX))
    candidates.sort(key=lambda t: (float(t[1]), int(t[2])), reverse=True)
    eligible: List[Tuple[int, float, int]] = []
    for a, q, n in candidates:
        if int(n) < int(n_min):
            continue
        if float(q) < float(q_min):
            continue
        if int(a) not in applicable:
            continue
        # Safety: do not use the solution guard while a real logical technique is available.
        if int(a) == ACTION_SOLUTION_GUARD and logic_available:
            continue
        eligible.append((int(a), float(q), int(n)))
    if not eligible:
        a0, q0, n0 = candidates[0]
        if int(n0) < int(n_min):
            stats.rejected_n += 1
        elif float(q0) < float(q_min):
            stats.rejected_q += 1
        else:
            stats.rejected_unsafe += 1
        stats.fallback += 1
        return fallback
    top_q = float(eligible[0][1])
    tie_eps = _env_float("OROMA_SUDOKU_POLICY_Q_TIE_EPS", 0.000001)
    top = [t for t in eligible if abs(float(t[1]) - top_q) <= tie_eps]
    stats.accepted += 1
    return int(rng.choice(top if top else eligible[:1])[0])


@dataclass
class TraceStep:
    state_hash: str
    action: int
    technique: str
    candidates_before: int
    logical: bool
    assisted: bool
    ts: int


def _dbw_try_enable() -> bool:
    if db_writer_client is None:
        return False
    raw = os.environ.get("OROMA_DBW_ENABLE")
    if raw is not None and str(raw).strip().lower() in ("0", "false", "no", "off"):
        return False
    if raw is None:
        try:
            sock_path = db_writer_client._sock_path() if hasattr(db_writer_client, "_sock_path") else "/opt/ai/oroma/data/state/db_writer.sock"
            if os.path.exists(str(sock_path)):
                os.environ["OROMA_DBW_ENABLE"] = "1"
        except Exception:
            pass
    try:
        return bool(db_writer_client.ping(timeout_ms=800))
    except Exception:
        return False


def _learn_policy_rules_dbw(namespace: str, items: Sequence[Dict[str, Any]]) -> Tuple[bool, int, float]:
    t0 = time.time()
    if not items:
        return False, 0, 0.0
    if not _dbw_try_enable():
        return False, 0, round((time.time() - t0) * 1000.0, 3)
    now = int(time.time())
    agg: Dict[Tuple[str, str], Dict[str, int]] = {}
    learned_count = 0
    for it in items:
        sh = str(it.get("state_hash", "")).strip()
        action = str(it.get("action_canon", it.get("action", ""))).strip()
        if not sh or action == "":
            continue
        try:
            out = float(it.get("outcome", 0.0))
        except Exception:
            out = 0.0
        if abs(out) <= 1e-9:
            continue
        row = agg.setdefault((sh, action), {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": now})
        row["n"] += 1
        learned_count += 1
        if out > 0.0:
            row["pos"] += 1
        else:
            row["neg"] += 1
        try:
            ts = int(it.get("ts") or now)
            if ts > row["last_ts"]:
                row["last_ts"] = ts
        except Exception:
            pass
    if not agg:
        return False, 0, round((time.time() - t0) * 1000.0, 3)
    sql = """INSERT INTO policy_rules
             (namespace, state_hash, action, n, pos, neg, draw, q, last_ts)
             VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(namespace, state_hash, action) DO UPDATE SET
                 n = policy_rules.n + excluded.n,
                 pos = policy_rules.pos + excluded.pos,
                 neg = policy_rules.neg + excluded.neg,
                 draw = policy_rules.draw + excluded.draw,
                 q = CASE
                       WHEN (policy_rules.n + excluded.n) > 0
                       THEN CAST((policy_rules.pos + excluded.pos) - (policy_rules.neg + excluded.neg) AS REAL)
                            / CAST(policy_rules.n + excluded.n AS REAL)
                       ELSE 0.0
                     END,
                 last_ts = CASE
                             WHEN excluded.last_ts > policy_rules.last_ts THEN excluded.last_ts
                             ELSE policy_rules.last_ts
                           END
          """
    params: List[List[Any]] = []
    for (sh, action), row in agg.items():
        n = int(row["n"])
        pos = int(row["pos"])
        neg = int(row["neg"])
        draw = int(row["draw"])
        q = float(pos - neg) / float(max(1, n))
        params.append([str(namespace), str(sh), str(action), n, pos, neg, draw, q, int(row["last_ts"])])
    timeout_ms = int(getattr(sql_manager, "_dbw_timeout_ms", lambda kind="dream": 60000)("dream")) if sql_manager else 60000
    chunk = max(1, _env_int("OROMA_SUDOKU_POLICY_DBW_CHUNK", 500))
    try:
        for i in range(0, len(params), chunk):
            db_writer_client.executemany(
                sql,
                params[i:i + chunk],
                tag="sudoku.pro_v2.policy_rules.upsert",
                priority="low",
                timeout_ms=timeout_ms,
                db="oroma",
            )
        return True, int(learned_count), round((time.time() - t0) * 1000.0, 3)
    except Exception as e:
        sys.stderr.write(f"[sudoku_daily_runner] DBWriter policy upsert failed: {e!r}\n")
        return False, 0, round((time.time() - t0) * 1000.0, 3)


def _learn_items_from_trace(trace: Sequence[TraceStep], solved: bool, ts: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    items: List[Dict[str, Any]] = []
    meta = {
        "learn_items": 0,
        "logic_credit_items": 0,
        "assist_credit_items": 0,
        "terminal_credit_items": 0,
    }
    def add(step: TraceStep, outcome: float, kind: str) -> None:
        if abs(float(outcome)) <= 1e-9:
            return
        items.append({"state_hash": step.state_hash, "action_canon": int(step.action), "outcome": float(outcome), "ts": int(step.ts or ts)})
        meta["learn_items"] += 1
        if kind == "logic":
            meta["logic_credit_items"] += 1
        elif kind == "assist":
            meta["assist_credit_items"] += 1
        elif kind == "terminal":
            meta["terminal_credit_items"] += 1
    for step in trace:
        if bool(step.logical):
            add(step, 1.0, "logic")
        elif bool(step.assisted):
            add(step, 0.25, "assist")
    if bool(solved) and trace:
        n = max(0, _env_int("OROMA_SUDOKU_TERMINAL_CREDIT_STEPS", 18))
        for step in list(trace)[-n:]:
            add(step, 0.50, "terminal")
    return items, meta


def _db_pro_coverage(namespace: str) -> Dict[str, int]:
    out = {"pro_states_known": 0, "pro_rules_known": 0, "pro_samples_known": 0}
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(DISTINCT state_hash) AS states,
                              COUNT(*) AS rules,
                              COALESCE(SUM(n),0) AS samples
                       FROM policy_rules
                       WHERE namespace=? AND state_hash LIKE ?""",
                    (str(namespace), f"{STATE_SCHEMA}%"),
                ).fetchone()
            if row is not None:
                try:
                    out["pro_states_known"] = int(row["states"] or 0)
                    out["pro_rules_known"] = int(row["rules"] or 0)
                    out["pro_samples_known"] = int(row["samples"] or 0)
                except Exception:
                    out["pro_states_known"] = int(row[0] or 0)
                    out["pro_rules_known"] = int(row[1] or 0)
                    out["pro_samples_known"] = int(row[2] or 0)
    except Exception as e:
        sys.stderr.write(f"[sudoku_daily_runner] coverage read failed: {e!r}\n")
    return out


def _db_write_episode(kind: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None or not hasattr(sql_manager, "insert_episode"):
        return None
    ts0 = int(meta.get("ts_start") or time.time())
    ts1 = int(meta.get("ts_end") or time.time())
    try:
        try:
            eid = sql_manager.insert_episode(ts_start=ts0, ts_end=ts1, kind=str(kind), source=str(meta.get("source") or "orchestrator"), label=str(meta.get("label") or kind), meta=meta)
        except TypeError:
            eid = sql_manager.insert_episode(kind=str(kind), ts_start=ts0, ts_end=ts1, label=str(meta.get("label") or kind), meta=meta)
        return int(eid) if eid is not None else None
    except Exception as e:
        sys.stderr.write(f"[sudoku_daily_runner] DB insert_episode failed: {e!r}\n")
        return None


def _db_write_metrics(eid: int, metrics: Dict[str, Any]) -> bool:
    if sql_manager is None or not hasattr(sql_manager, "insert_episodic_metric"):
        return False
    ts = int(time.time())
    ok = True
    for k, v in metrics.items():
        try:
            if isinstance(v, bool):
                fv = 1.0 if v else 0.0
            else:
                fv = float(v) if v is not None else 0.0
            sql_manager.insert_episodic_metric(episode_id=int(eid), ts=int(ts), key=str(k), value=float(fv))
        except Exception as e:
            sys.stderr.write(f"[sudoku_daily_runner] DB metric failed ({k}): {e!r}\n")
            ok = False
    return ok


def _numeric_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    skip = {"namespace", "mode", "state_schema", "action_schema", "source", "label", "runner", "shim", "difficulty"}
    out: Dict[str, Any] = {}
    for k, v in result.items():
        if k in skip:
            continue
        if isinstance(v, bool):
            out[k] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            out[k] = v
    return out


@dataclass
class BatchResult:
    ts_start: int
    ts_end: int
    duration_ms: int
    games: int
    requested_games: int
    effective_games: int
    wins_x: int
    wins_o: int
    draws: int
    avg_moves: float
    avg_empty_start: float
    avg_clues: float
    avg_logic_moves: float
    avg_assist_moves: float
    avg_naked_singles: float
    avg_hidden_row: float
    avg_hidden_col: float
    avg_hidden_box: float
    avg_solution_guard: float
    mode: str
    namespace: str
    state_schema: str
    action_schema: str
    difficulty: str
    policy_enabled: float
    eps: float
    explore_reduced: float
    no_more_explore: float
    mechanic_understood: float
    learn: bool
    learn_items: int
    learned_items: int
    policy_learn_ok: bool
    learn_duration_ms: float
    sim_duration_ms: float
    policy_dbw_chunk: int
    policy_seen: int
    policy_accepted: int
    policy_fallback: int
    policy_rejected_n: int
    policy_rejected_q: int
    policy_rejected_unsafe: int
    logic_credit_items: int
    assist_credit_items: int
    terminal_credit_items: int
    naked_single_moves: int
    hidden_row_moves: int
    hidden_col_moves: int
    hidden_box_moves: int
    solution_guard_moves: int
    stuck_games: int
    solved_games: int
    pro_states_known_before: int
    pro_rules_known_before: int
    pro_samples_known_before: int
    pro_states_known: int
    pro_rules_known: int
    pro_samples_known: int
    source: str
    label: str
    runner: str
    shim: str


def _play_one(seed: int, difficulty: str, namespace: str, mode: str, eps: float, stats: PolicyStats, rng: random.Random) -> Tuple[bool, int, int, int, Dict[int, int], List[TraceStep], int]:
    if sudoku_game is None:
        raise RuntimeError(f"core.sudoku_game import failed: {_IMPORT_ERROR!r}")
    rec = sudoku_game.generate_puzzle(seed=int(seed), difficulty=str(difficulty), ensure_unique=True)
    puzzle = _copy_grid(rec["puzzle"])
    solution = _copy_grid(rec["solution"])
    grid = _copy_grid(puzzle)
    givens = int(rec.get("clues", sum(1 for r in range(9) for c in range(9) if int(puzzle[r][c]) != 0)))
    trace: List[TraceStep] = []
    move_counts = {a: 0 for a in ACTION_NAMES}
    max_moves = _env_int("OROMA_SUDOKU_MAX_MOVES", 96)
    solved = False
    stuck = False
    for _idx in range(max_moves):
        if all(int(grid[r][c]) != 0 for r in range(9) for c in range(9)):
            solved = bool(sudoku_game.is_solved(puzzle, grid))
            break
        sh = _state_hash(grid, givens, difficulty)
        fallback = _fallback_action(grid, solution)
        if mode == "policy":
            action = _policy_choose(grid, solution, namespace, sh, stats, rng)
        else:
            if float(eps) > 0.0 and rng.random() < float(eps):
                applicable = sorted(_applicable_actions(grid, solution))
                logic = [a for a in applicable if a != ACTION_SOLUTION_GUARD]
                action = int(rng.choice(logic or applicable or [fallback]))
            else:
                action = _policy_choose(grid, solution, namespace, sh, stats, rng)
        moves = _moves_for_action(grid, solution, int(action))
        if not moves:
            action = fallback
            moves = _moves_for_action(grid, solution, int(action))
        if not moves:
            stuck = True
            break
        # deterministic but not always identical among equivalent moves
        top = sorted(moves, key=lambda m: (m.candidates_before, m.r, m.c, m.value))
        best_cand = top[0].candidates_before
        ties = [m for m in top if int(m.candidates_before) == int(best_cand)]
        mv = rng.choice(ties if ties else top[:1])
        if int(grid[mv.r][mv.c]) != 0:
            continue
        grid[mv.r][mv.c] = int(mv.value)
        move_counts[int(mv.action)] = int(move_counts.get(int(mv.action), 0)) + 1
        trace.append(TraceStep(
            state_hash=str(sh),
            action=int(mv.action),
            technique=str(mv.technique),
            candidates_before=int(mv.candidates_before),
            logical=(int(mv.action) != ACTION_SOLUTION_GUARD),
            assisted=(int(mv.action) == ACTION_SOLUTION_GUARD),
            ts=_now_ts(),
        ))
    else:
        stuck = True
    if not solved and all(int(grid[r][c]) != 0 for r in range(9) for c in range(9)):
        solved = bool(sudoku_game.is_solved(puzzle, grid))
    empty_start = 81 - int(givens)
    return bool(solved), int(len(trace)), int(empty_start), int(givens), move_counts, trace, int(1 if stuck and not solved else 0)


def _run_batch(*, namespace: str, mode: str, games: int, requested_games: int, eps: float, seed: int, difficulty: str, learn: bool, mechanic_understood: bool, explore_reduced: bool, coverage_before: Dict[str, int]) -> BatchResult:
    ts_start = _now_ts()
    t0 = time.time()
    rng = random.Random(int(seed) ^ (4321 if mode == "policy" else 98765))
    stats = PolicyStats()
    solved_games = 0
    stuck_games = 0
    moves_total = empty_total = clues_total = 0
    counts_total = {a: 0 for a in ACTION_NAMES}
    learn_items: List[Dict[str, Any]] = []
    learn_meta = {"learn_items": 0, "logic_credit_items": 0, "assist_credit_items": 0, "terminal_credit_items": 0}
    sim_t0 = time.time()
    for g in range(max(0, int(games))):
        solved, moves, empty_start, clues, counts, trace, stuck = _play_one(int(seed) + int(g), difficulty, namespace, mode, eps, stats, rng)
        solved_games += int(1 if solved else 0)
        stuck_games += int(stuck)
        moves_total += int(moves)
        empty_total += int(empty_start)
        clues_total += int(clues)
        for a, n in counts.items():
            counts_total[int(a)] = int(counts_total.get(int(a), 0)) + int(n)
        if learn:
            items, meta = _learn_items_from_trace(trace, bool(solved), _now_ts())
            learn_items.extend(items)
            for k, v in meta.items():
                learn_meta[k] = int(learn_meta.get(k, 0)) + int(v)
    sim_ms = round((time.time() - sim_t0) * 1000.0, 3)
    policy_ok = False
    learned = 0
    learn_ms = 0.0
    if learn:
        policy_ok, learned, learn_ms = _learn_policy_rules_dbw(namespace, learn_items)
    coverage_after = _db_pro_coverage(namespace)
    n_games = max(1, int(games))
    ts_end = _now_ts()
    return BatchResult(
        ts_start=ts_start,
        ts_end=ts_end,
        duration_ms=int(round((time.time() - t0) * 1000.0)),
        games=int(games),
        requested_games=int(requested_games),
        effective_games=int(games),
        wins_x=int(solved_games),
        wins_o=int(max(0, int(games) - int(solved_games))),
        draws=0,
        avg_moves=round(float(moves_total) / float(n_games), 3),
        avg_empty_start=round(float(empty_total) / float(n_games), 3),
        avg_clues=round(float(clues_total) / float(n_games), 3),
        avg_logic_moves=round(float(counts_total[ACTION_NAKED_SINGLE] + counts_total[ACTION_HIDDEN_ROW] + counts_total[ACTION_HIDDEN_COL] + counts_total[ACTION_HIDDEN_BOX]) / float(n_games), 3),
        avg_assist_moves=round(float(counts_total[ACTION_SOLUTION_GUARD]) / float(n_games), 3),
        avg_naked_singles=round(float(counts_total[ACTION_NAKED_SINGLE]) / float(n_games), 3),
        avg_hidden_row=round(float(counts_total[ACTION_HIDDEN_ROW]) / float(n_games), 3),
        avg_hidden_col=round(float(counts_total[ACTION_HIDDEN_COL]) / float(n_games), 3),
        avg_hidden_box=round(float(counts_total[ACTION_HIDDEN_BOX]) / float(n_games), 3),
        avg_solution_guard=round(float(counts_total[ACTION_SOLUTION_GUARD]) / float(n_games), 3),
        mode=str(mode),
        namespace=str(namespace),
        state_schema=STATE_SCHEMA,
        action_schema=ACTION_SCHEMA,
        difficulty=str(difficulty),
        policy_enabled=1.0,
        eps=float(eps),
        explore_reduced=1.0 if bool(explore_reduced) else 0.0,
        no_more_explore=0.0,
        mechanic_understood=1.0 if bool(mechanic_understood) else 0.0,
        learn=bool(learn),
        learn_items=int(learn_meta.get("learn_items", 0)),
        learned_items=int(learned),
        policy_learn_ok=bool(policy_ok),
        learn_duration_ms=float(learn_ms),
        sim_duration_ms=float(sim_ms),
        policy_dbw_chunk=max(1, _env_int("OROMA_SUDOKU_POLICY_DBW_CHUNK", 500)),
        policy_seen=int(stats.seen),
        policy_accepted=int(stats.accepted),
        policy_fallback=int(stats.fallback),
        policy_rejected_n=int(stats.rejected_n),
        policy_rejected_q=int(stats.rejected_q),
        policy_rejected_unsafe=int(stats.rejected_unsafe),
        logic_credit_items=int(learn_meta.get("logic_credit_items", 0)),
        assist_credit_items=int(learn_meta.get("assist_credit_items", 0)),
        terminal_credit_items=int(learn_meta.get("terminal_credit_items", 0)),
        naked_single_moves=int(counts_total[ACTION_NAKED_SINGLE]),
        hidden_row_moves=int(counts_total[ACTION_HIDDEN_ROW]),
        hidden_col_moves=int(counts_total[ACTION_HIDDEN_COL]),
        hidden_box_moves=int(counts_total[ACTION_HIDDEN_BOX]),
        solution_guard_moves=int(counts_total[ACTION_SOLUTION_GUARD]),
        stuck_games=int(stuck_games),
        solved_games=int(solved_games),
        pro_states_known_before=int(coverage_before.get("pro_states_known", 0)),
        pro_rules_known_before=int(coverage_before.get("pro_rules_known", 0)),
        pro_samples_known_before=int(coverage_before.get("pro_samples_known", 0)),
        pro_states_known=int(coverage_after.get("pro_states_known", 0)),
        pro_rules_known=int(coverage_after.get("pro_rules_known", 0)),
        pro_samples_known=int(coverage_after.get("pro_samples_known", 0)),
        source="orchestrator",
        label=f"sudoku:{mode} ({int(games)} games)",
        runner="tools/sudoku_daily_runner.py",
        shim="tools/sudoku_daily_runner.pro_v2_mechanic_solved",
    )


def run(policy_games: int, explore_games: int, seed: int, namespace: str, difficulty: str, eps: float) -> Dict[str, Any]:
    cov0 = _db_pro_coverage(namespace)
    mechanic_min = _env_int("OROMA_SUDOKU_MECHANIC_MIN_SAMPLES", 2500)
    mechanic_understood = int(cov0.get("pro_samples_known", 0)) >= int(mechanic_min)
    requested_explore = max(0, int(explore_games))
    effective_explore = requested_explore
    explore_reduced = False
    if mechanic_understood and requested_explore > 0:
        reduced_games = max(0, _env_int("OROMA_SUDOKU_EXPLORE_REDUCED_GAMES", 10))
        effective_explore = min(requested_explore, int(reduced_games))
        explore_reduced = effective_explore < requested_explore
        eps = _env_float("OROMA_SUDOKU_REDUCED_EPS", 0.0)
    policy = _run_batch(namespace=namespace, mode="policy", games=max(0, int(policy_games)), requested_games=max(0, int(policy_games)), eps=0.0, seed=int(seed), difficulty=difficulty, learn=False, mechanic_understood=mechanic_understood, explore_reduced=False, coverage_before=cov0)
    cov1 = _db_pro_coverage(namespace)
    explore = _run_batch(namespace=namespace, mode="explore", games=max(0, int(effective_explore)), requested_games=requested_explore, eps=float(eps), seed=int(seed) + 1000003, difficulty=difficulty, learn=True, mechanic_understood=mechanic_understood, explore_reduced=explore_reduced, coverage_before=cov1)
    out = {
        "ok": True,
        "have_db": bool(sql_manager is not None),
        "have_up": bool(db_writer_client is not None),
        "db_written": False,
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "seed": int(seed),
        "difficulty": str(difficulty),
        "mechanic_understood": 1.0 if mechanic_understood else 0.0,
        "explore_reduced": 1.0 if explore_reduced else 0.0,
        "no_more_explore": 0.0,
        "policy": asdict(policy),
        "explore": asdict(explore),
    }
    db_ok = True
    for br in (policy, explore):
        meta = asdict(br)
        kind = f"game:sudoku:{br.mode}_batch"
        eid = _db_write_episode(kind, meta)
        if eid is None:
            db_ok = False
            continue
        meta["episode_id"] = int(eid)
        okm = _db_write_metrics(int(eid), _numeric_metrics(meta))
        if not okm:
            db_ok = False
    out["db_written"] = bool(db_ok)
    out["ok"] = bool(db_ok and (not explore.learn or explore.policy_learn_ok or explore.learn_items == 0))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Sudoku daily runner – pro_v2 mechanic_solved")
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_SUDOKU_POLICY_GAMES", 10))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_SUDOKU_EXPLORE_GAMES", 10))
    ap.add_argument("--seed", type=int, default=int(time.time()) & 0xFFFFFFFF)
    ap.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    ap.add_argument("--difficulty", default=os.environ.get("OROMA_SUDOKU_DIFFICULTY", "medium"))
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_SUDOKU_EPS", 0.08))
    args = ap.parse_args(argv)
    try:
        res = run(
            policy_games=max(0, int(args.policy_games)),
            explore_games=max(0, int(args.explore_games)),
            seed=int(args.seed),
            namespace=str(args.namespace or DEFAULT_NAMESPACE),
            difficulty=str(args.difficulty or "medium").lower(),
            eps=float(args.eps),
        )
        print(json.dumps(res, ensure_ascii=False, separators=(",", ": ")))
        return 0 if bool(res.get("ok")) else 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": repr(e), "state_schema": STATE_SCHEMA, "action_schema": ACTION_SCHEMA}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

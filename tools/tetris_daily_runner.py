#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/tetris_daily_runner.py
# Projekt: ORÓMA – Games / Episodic Telemetry
# Modul:   Tetris Daily Runner (Policy + Explore) – DB Episode Writer
# Version: v1.0
# Stand:   2026-02-23
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt das Core-Spiel `core/tetris_engine.py` headless aus und schreibt
#   die Ergebnisse als Episoden + Metriken in die produktive ORÓMA DB
#   (`oroma.db`, Tabellen: episodes + episodic_metrics).
#
#   Standard-Pattern wie bei TicTacToe/Connect4/etc.:
#     • keine Flask/UI-Abhängigkeit
#     • zwei Batches pro Run: policy-only und explore
#     • DB-Connections werden sauber geschlossen (sql_manager intern)
#     • reproduzierbar via --seed
#
# Episode.kind
# ------------
#   • game:tetris:policy_batch
#   • game:tetris:explore_batch
#
# Policy/Explore
# -------------
#   Noch keine UniversalPolicyShim-Integration. Stattdessen schnelle Heuristik:
#     • policy: beste Platzierung (holes+height minimieren) + hard_drop
#     • explore: mit Wahrscheinlichkeit ε zufällige Platzierung
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     python3 tools/tetris_daily_runner.py --policy-games 100 --explore-games 100 --seed 1
#
# ENV
# ---
#   OROMA_TETRIS_EPS=0.08
#   OROMA_TETRIS_MAX_STEPS=5000
# =============================================================================

from __future__ import annotations

import argparse
import os
import random
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple


try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


from core.tetris_engine import TetrisEngine, WIDTH, HEIGHT, Piece, TETROMINOS

# Optional: UniversalPolicy learning (Policy-Table wie bei TicTacToe/Connect4)
try:
    from core.universal_policy import Policy as UniversalPolicy  # type: ignore
except Exception:
    UniversalPolicy = None  # type: ignore


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _now() -> int:
    return int(time.time())


def _board_height(board: List[List[int]]) -> int:
    for y in range(HEIGHT):
        if any(board[y][x] != -1 for x in range(WIDTH)):
            return HEIGHT - y
    return 0


def _count_holes(board: List[List[int]]) -> int:
    holes = 0
    for x in range(WIDTH):
        seen_block = False
        for y in range(HEIGHT):
            if board[y][x] != -1:
                seen_block = True
            elif seen_block:
                holes += 1
    return holes


def _place_piece_sim(board: List[List[int]], kind: str, rot: int, x: int) -> Optional[List[List[int]]]:
    p = Piece(kind=kind, rot=rot % 4, x=x, y=-2)

    def can_place(pp: Piece) -> bool:
        for (cx, cy) in pp.cells():
            if cx < 0 or cx >= WIDTH or cy >= HEIGHT:
                return False
            if cy >= 0 and board[cy][cx] != -1:
                return False
        return True

    if not can_place(p):
        return None

    while True:
        np = Piece(kind=p.kind, rot=p.rot, x=p.x, y=p.y + 1)
        if can_place(np):
            p = np
            continue
        break

    nb = deepcopy(board)
    for (cx, cy) in p.cells():
        if 0 <= cy < HEIGHT:
            nb[cy][cx] = 0

    full = [yy for yy in range(HEIGHT) if all(nb[yy][xx] != -1 for xx in range(WIDTH))]
    for yy in reversed(full):
        del nb[yy]
    for _ in full:
        nb.insert(0, [-1] * WIDTH)

    return nb


def _policy_best_placement(eng: TetrisEngine) -> Optional[Tuple[int, int]]:
    st = eng.get_state()
    cur = st.get("cur")
    if not cur or not cur.get("kind"):
        return None
    kind = str(cur["kind"])
    board = eng.board

    best: Optional[Tuple[float, int, int]] = None  # (score, rot, x)
    for rot in range(4):
        cells = TETROMINOS[kind][rot]
        min_cx = min(cx for cx, _ in cells)
        max_cx = max(cx for cx, _ in cells)
        for x in range(-min_cx, WIDTH - max_cx):
            nb = _place_piece_sim(board, kind, rot, x)
            if nb is None:
                continue
            holes = _count_holes(nb)
            height = _board_height(nb)
            score = holes * 10.0 + height
            if best is None or score < best[0]:
                best = (score, rot, x)

    if best is None:
        return None
    return (best[1], best[2])


def _apply_placement(eng: TetrisEngine, rot: int, x: int) -> int:
    cmds = 0
    for _ in range(rot % 4):
        if eng.rotate():
            cmds += 1

    st = eng.get_state()
    cur = st.get("cur")
    if cur and cur.get("x") is not None:
        cx = int(cur.get("x") or 0)
        while cx < x:
            if eng.right():
                cmds += 1
                cx += 1
            else:
                break
        while cx > x:
            if eng.left():
                cmds += 1
                cx -= 1
            else:
                break

    eng.hard_drop()
    cmds += 1
    return cmds


def _play_one(seed: int, *, eps: float, mode: str, max_steps: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    eng = TetrisEngine(seed=seed)
    steps = 0
    pieces = 0
    commands = 0
    t0 = time.time()

    # Für Learning: wir sammeln state/action/outcome Samples und schreiben sie
    # am Ende best-effort via UniversalPolicy.learn_many() in policy_rules.
    # (Nur wenn UniversalPolicy verfügbar ist.)
    learn_items: List[Dict[str, Any]] = []

    def _state_hash(e: TetrisEngine) -> str:
        """Kompakter Hash für Policy-Rules.

        Repräsentation:
          - Spaltenhöhen (10 ints)
          - aktuelles Piece Kind
          - nächstes Piece Kind
        """
        try:
            board = e.board
            heights = []
            for x in range(WIDTH):
                h = 0
                for y in range(HEIGHT):
                    if board[y][x] != -1:
                        h = HEIGHT - y
                        break
                heights.append(h)
            curk = e.cur.kind if e.cur else "-"
            nxt = getattr(e, "next_kind", "-") or "-"
            return "H:" + ",".join(str(v) for v in heights) + f"|C:{curk}|N:{nxt}"
        except Exception:
            return "H:" + (str(seed) or "0")

    while eng.running and steps < max_steps:
        steps += 1
        placement = _policy_best_placement(eng)
        if placement is None:
            break
        rot, x = placement
        if mode == "explore" and rng.random() < eps:
            rot = rng.randrange(0, 4)
            x = rng.randrange(0, WIDTH)

        # Learning sample: vor dem Zug
        sh = _state_hash(eng)
        score_before = int(getattr(eng, "score", 0))
        lines_before = int(getattr(eng, "lines_total", 0))

        commands += _apply_placement(eng, rot, x)
        pieces += 1

        # Outcome: Score/Lines Delta (leicht gewichtet)
        score_after = int(getattr(eng, "score", 0))
        lines_after = int(getattr(eng, "lines_total", 0))
        d_score = float(score_after - score_before)
        d_lines = float(lines_after - lines_before)
        outcome = (d_score / 40.0) + (d_lines * 0.25)

        learn_items.append({
            "state_hash": sh,
            "action": f"r{int(rot)}x{int(x)}",
            "outcome": float(outcome),
            "ts": _now(),
        })

    dt_ms = (time.time() - t0) * 1000.0

    learned = 0
    # Nur Explore lernt standardmäßig (wie bei anderen Games: policy-only ist "Evaluation")
    if mode == "explore" and UniversalPolicy is not None and learn_items:
        try:
            up = UniversalPolicy(namespace="game:tetris")
            up.learn_many(learn_items)
            learned = int(len(learn_items))
        except Exception:
            learned = 0

    return {
        "steps": steps,
        "pieces": pieces,
        "commands": commands,
        "score_end": int(getattr(eng, "score", 0)),
        "lines_end": int(getattr(eng, "lines_total", 0)),
        "level_end": int(getattr(eng, "level", 0)),
        "duration_ms": float(dt_ms),
        "learn": bool(mode == "explore"),
        "learned_items": int(learned),
    }


def _run_batch(*, seed: int, games: int, eps: float, mode: str, max_steps: int) -> Dict[str, Any]:
    t0 = time.time()
    total_steps = total_score = total_lines = total_level = total_pieces = total_cmds = 0
    total_learned = 0
    for i in range(int(games)):
        res = _play_one(seed + i, eps=eps, mode=mode, max_steps=max_steps)
        total_steps += int(res["steps"])
        total_score += int(res["score_end"])
        total_lines += int(res["lines_end"])
        total_level += int(res["level_end"])
        total_pieces += int(res["pieces"])
        total_cmds += int(res["commands"])
        try:
            total_learned += int(res.get("learned_items") or 0)
        except Exception:
            pass

    dt_ms = (time.time() - t0) * 1000.0
    g = max(1, int(games))
    return {
        "games": int(games),
        "steps": int(total_steps),
        "avg_score_end": float(total_score) / g,
        "avg_lines_end": float(total_lines) / g,
        "avg_level_end": float(total_level) / g,
        "avg_pieces": float(total_pieces) / g,
        "avg_commands": float(total_cmds) / g,
        "duration_ms": float(dt_ms),
        "eps": float(eps),
        "mode": mode,
        "max_steps": int(max_steps),
        "learn": bool(mode == "explore"),
        "learned_items": int(total_learned),
    }


def _db_write(kind: str, label: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None:
        return None
    ts_start = int(meta.get("ts_start") or _now())
    ts_end = int(meta.get("ts_end") or _now())
    if ts_end <= ts_start:
        ts_end = ts_start + 1
    meta["ts_start"] = ts_start
    meta["ts_end"] = ts_end

    # NOTE: insert_episode() can return None when DB is locked / write-flock timeout.
    # We must not crash the runner on int(None). Instead, retry briefly and then
    # return None so the caller can mark db_written=false.
    eid: Optional[int] = None
    last_err: Optional[Exception] = None
    for _attempt in range(3):
        try:
            _eid = sql_manager.insert_episode(kind=kind, ts_start=ts_start, ts_end=ts_end, label=label, meta=meta)
            if _eid is not None:
                eid = int(_eid)
                break
        except Exception as e:
            last_err = e
        time.sleep(0.5)

    if eid is None:
        # Keep the reason visible to the caller (serialized in out['err']).
        if last_err is not None:
            meta["db_write_error"] = str(last_err)
        return None

    def m(key: str, val: Any) -> None:
        try:
            sql_manager.insert_episodic_metric(episode_id=eid, key=key, value=float(val))
        except Exception:
            return

    for k in ("games", "steps", "avg_score_end", "avg_lines_end", "avg_level_end", "avg_pieces", "avg_commands", "duration_ms"):
        m(k, meta.get(k, 0))
    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_TETRIS_EPS", 0.08))
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_TETRIS_MAX_STEPS", 5000))
    args = ap.parse_args()

    if sql_manager is not None:
        try:
            sql_manager.ensure_schema()
        except Exception:
            pass

    base = {"namespace": "game:tetris", "policy_enabled": 0.0, "policy_rules": 0, "source": "orchestrator"}

    out: Dict[str, Any] = {"ok": True, "have_db": sql_manager is not None, "db_written": False}
    rc = 0
    try:
        p_t0 = time.time()
        policy_meta = _run_batch(seed=args.seed, games=args.policy_games, eps=0.0, mode="policy", max_steps=args.max_steps)
        policy_meta.update(base)
        policy_meta["ts_start"] = int(p_t0)
        policy_meta["ts_end"] = int(time.time())
        eid_p = _db_write("game:tetris:policy_batch", f"tetris:policy ({args.policy_games} games)", policy_meta)
        if eid_p is not None:
            policy_meta["episode_id"] = int(eid_p)

        e_t0 = time.time()
        explore_meta = _run_batch(seed=args.seed + 100000, games=args.explore_games, eps=args.eps, mode="explore", max_steps=args.max_steps)
        explore_meta.update(base)
        explore_meta["ts_start"] = int(e_t0)
        explore_meta["ts_end"] = int(time.time())
        explore_meta["learn"] = True
        explore_meta["learned_items"] = 0
        eid_e = _db_write("game:tetris:explore_batch", f"tetris:explore ({args.explore_games} games)", explore_meta)
        if eid_e is not None:
            explore_meta["episode_id"] = int(eid_e)

        out["db_written"] = bool((eid_p is not None) and (eid_e is not None))
        out["policy"] = policy_meta
        out["explore"] = explore_meta

        # If DB write failed, do not crash; mark ok=true but add a visible error.
        if not out["db_written"] and sql_manager is not None:
            perr = policy_meta.get("db_write_error")
            eerr = explore_meta.get("db_write_error")
            out["err"] = f"db_write_failed: policy={perr or 'n/a'} explore={eerr or 'n/a'}"

    except Exception as e:
        rc = 2
        out["ok"] = False
        out["err"] = str(e)

    print(out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

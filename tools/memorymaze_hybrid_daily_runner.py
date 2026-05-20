#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/memorymaze_hybrid_daily_runner.py
# Projekt: ORÓMA – Games / Episodic Telemetry
# Modul:   MemoryMaze Hybrid Daily Runner (Policy-only + Explore) – DB Writer
# Version: v1.0
# Stand:   2026-02-22
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#   Headless Daily-Runner für "MemoryMaze Hybrid" (mini_programs/memorymaze_hybrid.py).
#   Dieses Spiel ist das strategische Hybrid-Game (PacMan-Maze + Memory-Blocker
#   + Items + Fallgruben + optional Hard-P3).
#
#   Der Runner schreibt pro Ausführung typischerweise 2 Episoden:
#     1) game:memorymaze_hybrid:<mode>:policy_batch
#     2) game:memorymaze_hybrid:<mode>:explore_batch
#
#   Dabei ist <mode> entweder:
#     • normal
#     • hard_p3
#
#   Warum separate Kinds?
#     - Daily Runs sollen Normal und Hard-P3 gezielt abdecken (kein "jedes 3. Game"),
#       daher wird mode im kind gespeichert.
#
# DB / Stabilität
# ---------------
#   - Es werden KEINE neuen Tabellen angelegt.
#   - Jede DB-Connection wird sauber geschlossen (sql_manager API).
#   - Writes sind lock-robust (insert_episode/metrics sind in core/sql_manager
#     writer_lock+retry geschützt).
#
# CLI
# ---
#   cd /opt/ai/oroma
#   sudo -u oroma PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#       python3 tools/memorymaze_hybrid_daily_runner.py --mode normal --policy-games 100 --explore-games 100 --seed 1
#
#   sudo -u oroma PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#       python3 tools/memorymaze_hybrid_daily_runner.py --mode hard_p3 --policy-games 20 --explore-games 20 --seed 2
#
# ENV (optional)
# --------------
#   OROMA_MMZ_EPS=0.08
#   OROMA_MMZ_MAP=sym|asym
#   OROMA_MMZ_MAX_STEPS=900
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional


try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


try:
    from mini_programs.memorymaze_hybrid import HybridGame
except Exception as e:
    raise RuntimeError(f"memorymaze_hybrid import fehlgeschlagen: {e}")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default)).strip()))
    except Exception:
        return int(default)


def _now_s() -> int:
    return int(time.time())


def _run_batch(mode: str, policy: bool, games: int, seed: Optional[int], eps: float, map_kind: str, max_steps: int) -> Dict[str, Any]:
    g = HybridGame(map_kind=map_kind)
    g.reset(seed=seed, mode=mode)

    t0 = time.time()
    steps_total = 0
    wins_p1 = 0
    wins_p2 = 0
    draws = 0
    pairs_left_end_sum = 0
    strikes_p1_sum = 0
    strikes_p2_sum = 0
    p3_strikes_sum = 0

    for gi in range(int(games)):
        g.reset(seed=(None if seed is None else int(seed) + gi), mode=mode)
        # policy: eps=0
        eps_i = 0.0 if policy else eps
        for _ in range(int(max_steps)):
            if g.winner is not None:
                break
            a1 = g.ai_action("p1", eps=eps_i)
            a2 = g.ai_action("p2", eps=eps_i)
            acts = {"p1": a1, "p2": a2}
            if mode == "hard_p3":
                acts["p3"] = g.ai_action("p3", eps=0.0)
            g.step(acts)
            steps_total += 1
        st = g.state()
        w = st.get("winner")
        if w == "p1":
            wins_p1 += 1
        elif w == "p2":
            wins_p2 += 1
        else:
            draws += 1
        pairs_left_end_sum += int(st.get("pairs_left", 0))
        s = st.get("strikes", {}) or {}
        strikes_p1_sum += int(s.get("p1", 0))
        strikes_p2_sum += int(s.get("p2", 0))
        p3_strikes_sum += int(s.get("p3", 0))

    dur_ms = (time.time() - t0) * 1000.0
    avg_pairs_left_end = (pairs_left_end_sum / games) if games > 0 else 0.0

    return {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": float(dur_ms),
        "games": int(games),
        "steps": int(steps_total),
        "wins_p1": int(wins_p1),
        "wins_p2": int(wins_p2),
        "draws": int(draws),
        "avg_pairs_left_end": float(avg_pairs_left_end),
        "avg_strikes_p1": float(strikes_p1_sum / games) if games > 0 else 0.0,
        "avg_strikes_p2": float(strikes_p2_sum / games) if games > 0 else 0.0,
        "avg_strikes_p3": float(p3_strikes_sum / games) if games > 0 else 0.0,
        "mode": str(mode),
        "eps": float(eps_i),
        "map_kind": str(map_kind),
        "max_steps": int(max_steps),
        "source": "orchestrator",
        "policy": bool(policy),
    }


def _db_write(kind: str, label: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None:
        return None
    ts0 = int(meta.get("ts_start", _now_s()))
    ts1 = int(meta.get("ts_end", _now_s()))
    if ts1 <= ts0:
        ts1 = ts0 + 1
        meta["ts_end"] = ts1
    try:
        eid = sql_manager.insert_episode(
            kind=str(kind),
            ts_start=int(ts0),
            ts_end=int(ts1),
            source="orchestrator",
            label=str(label),
            meta=meta,
        )
    except Exception as e:
        print(f"[memorymaze_hybrid_runner] DB write failed insert_episode: {e}", file=sys.stderr)
        return None

    # metrics
    try:
        base_ts = int(meta.get("ts_end", ts1))
        for k in (
            "duration_ms",
            "games",
            "steps",
            "wins_p1",
            "wins_p2",
            "draws",
            "avg_pairs_left_end",
            "avg_strikes_p1",
            "avg_strikes_p2",
            "avg_strikes_p3",
        ):
            v = meta.get(k)
            if v is None:
                continue
            sql_manager.insert_episodic_metric(episode_id=int(eid), ts=int(base_ts), key=str(k), value=float(v))
    except Exception as e:
        print(f"[memorymaze_hybrid_runner] DB write failed metrics: {e}", file=sys.stderr)

    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("OROMA_MMZ_MODE", "normal"), choices=["normal", "hard_p3"])
    ap.add_argument("--map", dest="map_kind", default=os.environ.get("OROMA_MMZ_MAP", "sym"), choices=["sym", "asym"])
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_MMZ_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_MMZ_EXPLORE_GAMES", 100))
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_MMZ_EPS", 0.08))
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_MMZ_MAX_STEPS", 900))
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    policy_meta = _run_batch(args.mode, True, args.policy_games, args.seed, args.eps, args.map_kind, args.max_steps)
    explore_meta = _run_batch(args.mode, False, args.explore_games, args.seed, args.eps, args.map_kind, args.max_steps)

    ok = True
    eid_policy = None
    eid_explore = None
    if sql_manager is not None:
        eid_policy = _db_write(
            kind=f"game:memorymaze_hybrid:{args.mode}:policy_batch",
            label=f"memorymaze_hybrid:{args.mode}:policy ({args.policy_games} games)",
            meta=policy_meta,
        )
        eid_explore = _db_write(
            kind=f"game:memorymaze_hybrid:{args.mode}:explore_batch",
            label=f"memorymaze_hybrid:{args.mode}:explore ({args.explore_games} games)",
            meta=explore_meta,
        )
        ok = (eid_policy is not None) and (eid_explore is not None)

    out = {
        "ok": True,
        "have_db": bool(sql_manager is not None),
        "db_written": bool(ok) if sql_manager is not None else False,
        "policy": dict(policy_meta, episode_id=eid_policy),
        "explore": dict(explore_meta, episode_id=eid_explore),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

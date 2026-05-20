#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/hideseek_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Hide&Seek Daily Runner – Policy+Explore Batches → episodes + episodic_metrics
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# Wie Connect4/Pong/Chess/Flappy:
#   • policy_batch  : learn=false (eps=0)
#   • explore_batch : learn=true  (eps>0)
#
# DB:
#   episodes.kind:
#     - game:hideseek:policy_batch
#     - game:hideseek:explore_batch
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional

from core import sql_manager


def _env_float(name: str, default: float) -> float:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


class PolicyShim:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore

            self.pol = Policy(namespace=namespace)
        except Exception:
            self.pol = None

    @staticmethod
    def _rle(flat: List[int]) -> str:
        if not flat:
            return ""
        out = []
        last = int(flat[0])
        cnt = 1
        for x in flat[1:]:
            x = int(x)
            if x == last and cnt < 99:
                cnt += 1
            else:
                out.append(f"{last}{cnt:02d}")
                last = x
                cnt = 1
        out.append(f"{last}{cnt:02d}")
        return "".join(out)

    def state_hash(self, st: Dict[str, Any]) -> str:
        grid = st.get("grid") or []
        flat: List[int] = []
        for row in grid:
            for v in row:
                flat.append(int(v))
        s = st.get("seeker") or {}
        sx, sy = int(s.get("x", 0)), int(s.get("y", 0))
        hs = sorted([int(h.get("x", 0)) + 100 * int(h.get("y", 0)) for h in (st.get("hiders") or [])])
        return "hs:v1:" + self._rle(flat) + f":s={sx},{sy}:h=" + ",".join(map(str, hs[:12]))

    def choose(self, st: Dict[str, Any], legal: List[int]) -> int:
        if not self.pol:
            return int(random.choice(legal))
        try:
            return int(self.pol.choose(self.state_hash(st), legal, side="X"))
        except Exception:
            return int(random.choice(legal))

    def learn_many(self, items: List[Dict[str, Any]]) -> None:
        if not self.pol:
            return
        try:
            self.pol.learn_many(items)
        except Exception:
            return


def _db_write_episode(kind: str, meta: Dict[str, Any]) -> Optional[int]:
    if not hasattr(sql_manager, "insert_episode"):
        return None
    ts0 = int(meta.get("ts_start") or time.time())
    ts1 = int(meta.get("ts_end") or time.time())
    try:
        eid = sql_manager.insert_episode(
            ts_start=ts0,
            ts_end=ts1,
            kind=str(kind),
            source=str(meta.get("source") or "orchestrator"),
            label=str(meta.get("label") or kind),
            meta=meta,
        )
        return int(eid) if eid is not None else None
    except Exception as e:
        sys.stderr.write(f"[hideseek_daily_runner] DB insert_episode failed: {e!r}\n")
        return None


def _db_write_metrics(eid: int, metrics: Dict[str, Any]) -> bool:
    ts = int(time.time())
    ok = True
    for k, v in metrics.items():
        try:
            rid = sql_manager.insert_episodic_metric(
                episode_id=int(eid),
                ts=int(ts),
                key=str(k),
                value=float(v) if v is not None else 0.0,
            )
            if rid is None:
                ok = False
        except Exception as e:
            sys.stderr.write(f"[hideseek_daily_runner] DB metric failed ({k}): {e!r}\n")
            ok = False
    return ok


def run_batch(rng: random.Random,
              namespace: str,
              mode: str,
              games: int,
              eps: float,
              explore_moves_per_game: int,
              learn: bool,
              max_steps: int,
              source: str,
              label: str) -> Dict[str, Any]:
    from ui.hideseek_ui import HideSeekEnv  # type: ignore

    shim = PolicyShim(namespace=namespace)
    ts_start = int(time.time())
    t0 = time.time()

    wins = 0
    steps_sum = 0
    found_sum = 0

    for _ in range(int(games)):
        env = HideSeekEnv(seed=int(rng.getrandbits(32)))
        env.max_steps = int(max_steps)
        explore_budget = 0
        traj: List[Dict[str, Any]] = []

        while True:
            st = env.get_state()
            if st.get("done"):
                break
            legal = env.legal_actions() or [0, 1, 2, 3]

            if mode == "explore":
                if explore_budget < int(explore_moves_per_game):
                    explore_budget += 1
                    a = int(rng.choice(legal))
                elif rng.random() < float(eps):
                    a = int(rng.choice(legal))
                else:
                    a = int(shim.choose(st, legal))
            else:
                a = int(shim.choose(st, legal))

            st2, reward, done, _info = env.step(int(a))
            if learn:
                traj.append({"state_hash": shim.state_hash(st), "action_canon": int(a), "outcome": float(reward)})
            if done:
                break

        final = env.get_state()
        wins += 1 if (final.get("done") and int(final.get("found", 0)) >= 4) else 0
        steps_sum += int(final.get("steps", 0))
        found_sum += int(final.get("found", 0))

        if learn and traj:
            now = int(time.time())
            shim.learn_many([
                {"state_hash": tr["state_hash"], "action_canon": tr["action_canon"], "side": "X", "outcome": tr["outcome"], "ts": now}
                for tr in traj
            ])

    duration_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = int(time.time())

    avg_steps = steps_sum / float(int(games) or 1)
    avg_found = found_sum / float(int(games) or 1)
    avg_game_ms = duration_ms / float(int(games) or 1)
    draws = int(games) - int(wins)

    meta: Dict[str, Any] = {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": duration_ms,
        "games": int(games),
        "wins_x": int(wins),
        "wins_o": 0,
        "draws": int(draws),
        "avg_steps": float(avg_steps),
        "avg_found": float(avg_found),
        "avg_game_ms": float(avg_game_ms),
        "mode": "policy" if mode == "policy" else "explore",
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(0.0 if mode == "policy" else eps),
        "explore_moves_per_game": int(0 if mode == "policy" else explore_moves_per_game),
        "learn": bool(learn),
        "max_steps": int(max_steps),
        "source": str(source),
        "label": str(label),
        "runner": "tools/hideseek_daily_runner.py",
        "shim": "tools/hideseek_daily_runner.PolicyShim",
    }

    kind = f"{namespace}:policy_batch" if mode == "policy" else f"{namespace}:explore_batch"
    eid = _db_write_episode(kind=kind, meta=meta)

    metrics = {
        "duration_ms": duration_ms,
        "games": int(games),
        "wins_x": int(wins),
        "wins_o": 0,
        "draws": int(draws),
        "avg_steps": float(avg_steps),
        "avg_found": float(avg_found),
        "avg_game_ms": float(avg_game_ms),
        "policy_enabled": 1.0,
        "eps": float(0.0 if mode == "policy" else eps),
        "explore_moves_per_game": int(0 if mode == "policy" else explore_moves_per_game),
        "max_steps": int(max_steps),
    }

    db_ok = False
    if eid is not None:
        db_ok = _db_write_metrics(int(eid), metrics)
        meta["episode_id"] = int(eid)

    meta["db_written"] = bool(db_ok)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_ORCH_HIDESEEK_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_ORCH_HIDESEEK_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=int(time.time()) & 0xFFFFFFFF)
    ap.add_argument("--namespace", type=str, default="game:hideseek")
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_HIDESEEK_MAX_STEPS", 400))
    args = ap.parse_args()

    rng = random.Random(int(args.seed))
    eps = float(_env_float("OROMA_HIDESEEK_EPS", 0.08))
    explore_moves = int(_env_int("OROMA_HIDESEEK_EXPLORE_MOVES", 1))

    policy_res = run_batch(
        rng=rng,
        namespace=str(args.namespace),
        mode="policy",
        games=max(0, int(args.policy_games)),
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        max_steps=int(args.max_steps),
        source="orchestrator",
        label=f"hideseek:policy ({int(args.policy_games)} games)",
    )

    explore_res = run_batch(
        rng=rng,
        namespace=str(args.namespace),
        mode="explore",
        games=max(0, int(args.explore_games)),
        eps=float(eps),
        explore_moves_per_game=int(explore_moves),
        learn=True,
        max_steps=int(args.max_steps),
        source="orchestrator",
        label=f"hideseek:explore ({int(args.explore_games)} games)",
    )

    ok = bool(policy_res.get("db_written")) and bool(explore_res.get("db_written"))
    print(json.dumps({"ok": ok, "have_up": True, "db_written": ok, "policy": policy_res, "explore": explore_res}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/flappy_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   FlappyBird Daily Runner – 1×/Tag Policy+Explore Batches → episodes + episodic_metrics
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Führt FlappyBird automatisiert im Headless-Modus aus und schreibt aggregierte
# Ergebnisse in die DB (oroma.db), analog zu TicTacToe/Connect4/Pong/Chess.
#
#   • policy_batch  : N Games (learn=false) – Benchmark (eps=0)
#   • explore_batch : N Games (learn=true)  – epsilon-Explore + Learn (policy_rules)
#
# Ergebnis-Persistenz:
#   • episodes(kind='game:flappy:policy_batch' / 'game:flappy:explore_batch')
#   • episodic_metrics: games, avg_score, avg_steps, avg_return, duration_ms,
#                       avg_game_ms, eps, explore_moves_per_game, policy_enabled
#
# FLAPPY EPISODE-DEFINITION
# -------------------------
# Ein "Game" ist eine komplette Episode der headless RL-Umgebung
# mini_programs/flappybird.FlappyBird: Reset → step() bis done.
#
# ACTION SPACE
# ------------
# Diskret:
#   0 = nichts
#   1 = flap
#
# LEARN SIGNAL (UniversalPolicy)
# ------------------------------
# UniversalPolicy speichert für (state_hash, action) nur die Vorzeichen-Outcome.
# Für Flappy nutzen wir pro Episode ein sehr robustes Ziel:
#   outcome = +1 wenn score > 0 (mindestens eine Pipe passiert)
#           = -1 wenn score == 0
# (0 wird praktisch nicht verwendet)
#
# Das reicht, um "zu überleben bis zur ersten Pipe" zu lernen und vermeidet
# eine Outcome-Verzerrung durch +0.1 survival reward.
#
# PRODUKTIONSREGELN
# -----------------
# • headless-only, keine pygame/GUI Abhängigkeiten
# • DB-Verbindungen werden sauber geschlossen (Context-Manager)
# • Fehler sind sichtbar: stderr + ok=false (db_written=false)
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
    """Policy Adapter für Single-Agent Flappy."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore

            self.pol = Policy(namespace=namespace)
        except Exception:
            self.pol = None

    @staticmethod
    def _qb(v: float, bins: int) -> int:
        try:
            x = int(float(v) * bins)
        except Exception:
            x = 0
        if x < 0:
            return 0
        if x > bins:
            return bins
        return x

    @staticmethod
    def _qs(v: float) -> int:
        try:
            if v > 1e-9:
                return 1
            if v < -1e-9:
                return -1
        except Exception:
            pass
        return 0

    def state_hash(self, st: Dict[str, Any]) -> str:
        y = self._qb(st.get("y", 0.5), 40)
        dx = self._qb(min(1.0, max(0.0, float(st.get("dx", 1.0)))), 40)
        gy = self._qb(st.get("gap_y", 0.5), 40)
        gh = self._qb(st.get("gap_h", 0.25), 40)
        vs = self._qs(float(st.get("vy", 0.0)))
        return f"flappy:v1:y={y}:dx={dx}:gy={gy}:gh={gh}:vs={vs}"

    def choose(self, st: Dict[str, Any], legal: List[int]) -> int:
        if not self.pol:
            return int(random.choice(legal))
        sh = self.state_hash(st)
        return int(self.pol.choose(sh, legal, side="X"))

    def learn_many(self, items: List[Dict[str, Any]]) -> None:
        if not self.pol:
            return
        try:
            self.pol.learn_many(items)
        except Exception:
            # learning should never crash
            return


def run_one_episode(rng: random.Random,
                    shim: PolicyShim,
                    mode: str,
                    eps: float,
                    explore_moves_per_game: int,
                    learn: bool) -> Dict[str, Any]:
    from mini_programs.flappybird import FlappyBird, FBConfig  # type: ignore

    cfg = FBConfig(seed=None)
    env = FlappyBird(cfg)
    env.reset(seed=None)

    legal = [0, 1]
    explore_budget = 0
    traj: List[Dict[str, Any]] = []

    t0 = time.time()
    total_r = 0.0

    while True:
        st = env.get_state()
        if not st.get("alive", True):
            break

        a = 0
        if mode == "explore":
            if explore_budget < explore_moves_per_game:
                explore_budget += 1
                a = int(rng.choice(legal))
            elif rng.random() < eps:
                a = int(rng.choice(legal))
            else:
                a = int(shim.choose(st, legal))
        else:
            a = int(shim.choose(st, legal))

        if learn:
            traj.append({
                "state_hash": shim.state_hash(st),
                "action_canon": int(a),
            })

        _st2, r, done, _info = env.step(int(a))
        try:
            total_r += float(r)
        except Exception:
            pass
        if done:
            break

    dt_ms = int(round((time.time() - t0) * 1000.0))
    final = env.get_state()
    score = int(final.get("score", 0) or 0)
    steps = int(final.get("steps", 0) or 0)

    if learn and traj:
        # Outcome: pass at least one pipe
        out = 1.0 if score > 0 else -1.0
        now = int(time.time())
        items = []
        for tr in traj:
            items.append({
                "state_hash": tr["state_hash"],
                "action_canon": tr["action_canon"],
                "side": "X",
                "outcome": out,
                "ts": now,
            })
        shim.learn_many(items)

    return {
        "score": score,
        "steps": steps,
        "return": float(total_r),
        "duration_ms": int(dt_ms),
    }


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
            source=str(meta.get("source") or "flappy_daily_runner"),
            label=str(meta.get("label") or kind),
            meta=meta,
        )
        return int(eid) if eid is not None else None
    except Exception as e:
        sys.stderr.write(f"[flappy_daily_runner] DB insert_episode failed ({kind}): {e!r}\n")
        return None


def _db_write_metrics(eid: int, metrics: Dict[str, Any]) -> bool:
    """Schreibt episodic_metrics kompatibel zum realen Schema (episode_id, ts, key, value)."""
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
            sys.stderr.write(f"[flappy_daily_runner] DB insert_episodic_metric failed ({k}): {e!r}\n")
            ok = False
    return ok


def run_batch(rng: random.Random,
              namespace: str,
              mode: str,
              games: int,
              eps: float,
              explore_moves_per_game: int,
              learn: bool,
              source: str,
              label: str) -> Dict[str, Any]:
    shim = PolicyShim(namespace=namespace)

    ts_start = int(time.time())
    t0 = time.time()

    scores: List[int] = []
    steps: List[int] = []
    rets: List[float] = []

    for _ in range(int(games)):
        res = run_one_episode(
            rng=rng,
            shim=shim,
            mode=mode,
            eps=float(eps),
            explore_moves_per_game=int(explore_moves_per_game),
            learn=bool(learn),
        )
        scores.append(int(res["score"]))
        steps.append(int(res["steps"]))
        rets.append(float(res["return"]))

    duration_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = int(time.time())

    avg_score = sum(scores) / float(len(scores) or 1)
    avg_steps = sum(steps) / float(len(steps) or 1)
    avg_ret = sum(rets) / float(len(rets) or 1)
    avg_game_ms = duration_ms / float(int(games) or 1)

    meta = {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": duration_ms,
        "games": int(games),
        "avg_score": avg_score,
        "avg_steps": avg_steps,
        "avg_return": avg_ret,
        "avg_game_ms": avg_game_ms,
        "mode": "policy" if mode == "policy" else "explore",
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(0.0 if mode == "policy" else eps),
        "explore_moves_per_game": int(0 if mode == "policy" else explore_moves_per_game),
        "learn": bool(learn),
        "source": str(source),
        "label": str(label),
        "runner": "tools/flappy_daily_runner.py",
        "shim": "tools/flappy_daily_runner.PolicyShim",
    }

    kind = f"game:flappy:{'policy_batch' if mode=='policy' else 'explore_batch'}"
    eid = _db_write_episode(kind, meta)
    db_written = eid is not None

    if eid is not None:
        m = {
            "games": float(games),
            "avg_score": float(avg_score),
            "avg_steps": float(avg_steps),
            "avg_return": float(avg_ret),
            "duration_ms": float(duration_ms),
            "avg_game_ms": float(avg_game_ms),
            "eps": float(meta["eps"]),
            "explore_moves_per_game": float(meta["explore_moves_per_game"]),
            "policy_enabled": 1.0,
        }
        if not _db_write_metrics(int(eid), m):
            db_written = False

    return {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": duration_ms,
        "games": int(games),
        "avg_score": float(avg_score),
        "avg_steps": float(avg_steps),
        "avg_return": float(avg_ret),
        "avg_game_ms": float(avg_game_ms),
        "mode": "policy" if mode == "policy" else "explore",
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(meta["eps"]),
        "explore_moves_per_game": int(meta["explore_moves_per_game"]),
        "learn": bool(learn),
        "source": str(source),
        "label": str(label),
        "runner": "tools/flappy_daily_runner.py",
        "shim": "tools/flappy_daily_runner.PolicyShim",
        "episode_id": int(eid) if eid is not None else None,
    }, db_written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_FLAPPY_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_FLAPPY_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed if args.seed is not None else int(time.time() * 1000) & 0xffffffff)
    namespace = "game:flappy"
    eps = float(_env_float("OROMA_FLAPPY_EPS", 0.08))
    explore_moves_per_game = int(_env_int("OROMA_FLAPPY_EXPLORE_MOVES", 1))

    have_up = True
    try:
        from core.universal_policy import Policy  # noqa: F401
    except Exception:
        have_up = False

    policy_res, dbw1 = run_batch(
        rng=rng,
        namespace=namespace,
        mode="policy",
        games=int(args.policy_games),
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        source="orchestrator",
        label=f"flappy:policy ({int(args.policy_games)} games)",
    )

    explore_res, dbw2 = run_batch(
        rng=rng,
        namespace=namespace,
        mode="explore",
        games=int(args.explore_games),
        eps=float(eps),
        explore_moves_per_game=int(explore_moves_per_game),
        learn=True,
        source="orchestrator",
        label=f"flappy:explore ({int(args.explore_games)} games)",
    )

    ok = bool(dbw1 and dbw2)
    out = {
        "ok": bool(ok),
        "have_up": bool(have_up),
        "db_written": bool(ok),
        "policy": policy_res,
        "explore": explore_res,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/ctf_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Daily Runner – Capture The Flag (CTF) 100× Policy + 100× Explore
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# -----
# Führt das Mini-Program `mini_programs/capture_the_flag.py` im headless Modus aus
# und schreibt die Ergebnisse als **Episoden-Telemetrie** in `data/oroma.db`:
#
#   - game:ctf:policy_batch  (learn=false, eps=0)
#   - game:ctf:explore_batch (learn=true,  eps>0)
#
# Datenmodell (stabil, bereits im Projekt genutzt)
# - episodes (Kopf) + episodic_metrics (Key/Value)
#
# WICHTIG
# -------
# - DB-Schema ist in ORÓMA bewusst "slim" → wir nutzen core.sql_manager helpers.
# - Kein "silent failure": DB-Write-Fehler -> stderr + ok=false + db_written=false.
# - Headless: keine pygame/Qt/X11/Wayland Abhängigkeit.
#
# Usage
# -----
# sudo -u oroma PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#   python3 tools/ctf_daily_runner.py --policy-games 100 --explore-games 100 --seed 1
#
# Exit-Codes
# ----------
# 0 = ok (DB geschrieben)
# 2 = runner ok, aber DB write failed (telemetry sichtbar im stdout/stderr)
# 3 = fatal (unexpected exception)
# =============================================================================

from __future__ import annotations

import os
import sys
import time
import json
import random
import argparse
from typing import Any, Dict, List, Tuple, Optional

from core import sql_manager
from mini_programs.capture_the_flag import CTFEnv, CTFConfig


def _now() -> int:
    return int(time.time())


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _quantize01(x: float, bins: int = 16) -> int:
    x = _clamp(float(x), 0.0, 1.0)
    return int(round(x * (bins - 1)))


def _state_hash(obs: List[float], side: str) -> str:
    if not obs:
        return f"ctf|{side}|empty"
    q = [_quantize01(v, bins=16) for v in obs]
    return f"ctf|{side}|" + ",".join(str(n) for n in q)


class PolicyShim:
    """
    Wrap UniversalPolicy with stable semantics across ORÓMA builds.
    learn_many may return None -> treat as 0.
    """
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.have_up = False
        self.pol = None
        try:
            from core.universal_policy import Policy
            self.pol = Policy(namespace=namespace)
            self.have_up = True
        except Exception:
            self.have_up = False
            self.pol = None

    def choose(self, sh: str, legal: List[int], side: str) -> int:
        if not legal:
            return 0
        if not self.have_up:
            return int(legal[0])
        try:
            a = self.pol.choose(sh, legal, side=side)
            return int(a) if a in legal else int(legal[0])
        except Exception:
            return int(legal[0])

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not items or not self.have_up:
            return 0
        try:
            res = self.pol.learn_many(items)
            if res is None:
                return 0
            try:
                return int(res)
            except Exception:
                return 0
        except Exception:
            return 0


def run_batch(rng: random.Random,
              games: int,
              mode: str,
              namespace: str,
              eps: float,
              explore_moves_per_game: int,
              learn: bool,
              max_steps: int,
              source: str) -> Dict[str, Any]:
    shim = PolicyShim(namespace=namespace)
    wins_x = wins_o = draws = 0
    steps_sum = 0
    scoreA_sum = 0.0
    scoreB_sum = 0.0
    learn_items_all: List[Dict[str, Any]] = []

    legal = [0, 1, 2, 3, 4]

    t0 = time.time()

    for gi in range(int(games)):
        env = CTFEnv(CTFConfig(max_steps=max_steps, seed=rng.randrange(1, 2**31-1)))
        env.reset(seed=rng.randrange(1, 2**31-1))
        done = False

        explore_budget = int(explore_moves_per_game) if mode == "explore" else 0

        st_last = None
        while not done:
            # NOTE: CTFEnv exposes `features(agent)` (not `observe`).
            obsA = env.features("A")
            obsB = env.features("B")
            shA = _state_hash(obsA, "X")
            shB = _state_hash(obsB, "O")

            # choose actions
            if mode == "explore" and explore_budget > 0 and rng.random() < float(eps):
                aA = rng.choice(legal)
            else:
                aA = shim.choose(shA, legal, side="X")

            if mode == "explore" and explore_budget > 0 and rng.random() < float(eps):
                aB = rng.choice(legal)
            else:
                aB = shim.choose(shB, legal, side="O")

            st, rewards, done, info = env.step({"A": int(aA), "B": int(aB)})
            st_last = st

            if mode == "explore" and explore_budget > 0:
                # count down only when we actually injected exploration (approx)
                if rng.random() < float(eps):
                    explore_budget -= 1

            if learn and mode == "explore":
                def sgn(x: float) -> int:
                    if x > 1e-9: return 1
                    if x < -1e-9: return -1
                    return 0
                rA = float(rewards.get("A", 0.0))
                rB = float(rewards.get("B", 0.0))
                learn_items_all.append({"state_hash": shA, "action": int(aA), "reward": sgn(rA), "side": "X"})
                learn_items_all.append({"state_hash": shB, "action": int(aB), "reward": sgn(rB), "side": "O"})

        # terminal stats (use the last returned state; env has no stable `env.state` attribute)
        if st_last is None:
            st_last = env._mk_state()  # fallback (should not happen)

        sA = int(getattr(st_last, "A_score", 0))
        sB = int(getattr(st_last, "B_score", 0))
        scoreA_sum += sA
        scoreB_sum += sB
        steps = int(getattr(st_last, "steps", max_steps))
        steps_sum += steps

        if sA > sB:
            wins_x += 1
        elif sB > sA:
            wins_o += 1
        else:
            draws += 1

    # learn after batch
    learned = 0
    if learn and learn_items_all:
        learned = shim.learn_many(learn_items_all)

    t1 = time.time()
    duration_ms = int(round((t1 - t0) * 1000.0))
    avg_game_ms = float(duration_ms) / float(max(1, int(games)))

    avg_steps = float(steps_sum) / float(max(1, int(games)))
    avg_scoreA = float(scoreA_sum) / float(max(1, int(games)))
    avg_scoreB = float(scoreB_sum) / float(max(1, int(games)))

    return {
        "ts_start": int(t0),
        "ts_end": int(t1),
        "duration_ms": duration_ms,
        "games": int(games),
        "wins_x": int(wins_x),
        "wins_o": int(wins_o),
        "draws": int(draws),
        "avg_steps": avg_steps,
        "avg_score_A": avg_scoreA,
        "avg_score_B": avg_scoreB,
        "mode": mode,
        "namespace": namespace,
        "policy_enabled": 1.0 if shim.have_up else 0.0,
        "eps": float(eps) if mode == "explore" else 0.0,
        "explore_moves_per_game": int(explore_moves_per_game) if mode == "explore" else 0,
        "learn": bool(learn) if mode == "explore" else False,
        "learned_items": int(learned),
        "max_steps": int(max_steps),
        "source": source,
        "label": f"ctf:{mode} ({int(games)} games)",
        "runner": "tools/ctf_daily_runner.py",
        "shim": "tools/ctf_daily_runner.PolicyShim",
    }


def _db_write_episode(kind: str, res: Dict[str, Any]) -> Optional[int]:
    ts_start = int(res.get("ts_start", _now()))
    ts_end = int(res.get("ts_end", ts_start))
    meta = {k: res.get(k) for k in (
        "mode", "namespace", "policy_enabled", "eps", "explore_moves_per_game",
        "learn", "max_steps", "runner", "shim", "source"
    )}
    eid = sql_manager.insert_episode(
        ts_start=ts_start,
        kind=kind,
        source=str(res.get("source", "orchestrator")),
        label=str(res.get("label", kind)),
        meta=meta,
        ts_end=ts_end,
    )
    if not eid:
        return None

    metrics = {
        "games": float(res.get("games", 0)),
        "wins_x": float(res.get("wins_x", 0)),
        "wins_o": float(res.get("wins_o", 0)),
        "draws": float(res.get("draws", 0)),
        "avg_steps": float(res.get("avg_steps", 0.0)),
        "avg_score_A": float(res.get("avg_score_A", 0.0)),
        "avg_score_B": float(res.get("avg_score_B", 0.0)),
        "duration_ms": float(res.get("duration_ms", 0.0)),
        "avg_game_ms": float(res.get("avg_game_ms", 0.0)),
        "policy_enabled": float(res.get("policy_enabled", 0.0)),
        "eps": float(res.get("eps", 0.0)),
        "explore_moves_per_game": float(res.get("explore_moves_per_game", 0)),
        "max_steps": float(res.get("max_steps", 0)),
    }
    for k, v in metrics.items():
        sql_manager.insert_episodic_metric(int(eid), ts_end, str(k), float(v))
    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=int(os.environ.get("OROMA_CTF_POLICY_GAMES", "100")))
    ap.add_argument("--explore-games", type=int, default=int(os.environ.get("OROMA_CTF_EXPLORE_GAMES", "100")))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--namespace", type=str, default="game:ctf")
    ap.add_argument("--eps", type=float, default=float(os.environ.get("OROMA_CTF_EPS", "0.08")))
    ap.add_argument("--explore-moves", type=int, default=int(os.environ.get("OROMA_CTF_EXPLORE_MOVES", "1")))
    ap.add_argument("--max-steps", type=int, default=int(os.environ.get("OROMA_CTF_MAX_STEPS", "400")))
    ap.add_argument("--source", type=str, default="orchestrator")
    args = ap.parse_args()

    rng = random.Random(args.seed or int(time.time()) & 0xffffffff)

    policy_res = run_batch(
        rng=rng,
        games=args.policy_games,
        mode="policy",
        namespace=args.namespace,
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        max_steps=args.max_steps,
        source=args.source,
    )
    explore_res = run_batch(
        rng=rng,
        games=args.explore_games,
        mode="explore",
        namespace=args.namespace,
        eps=float(_clamp(args.eps, 0.0, 1.0)),
        explore_moves_per_game=max(0, int(args.explore_moves)),
        learn=True,
        max_steps=args.max_steps,
        source=args.source,
    )

    ok = True
    db_written = True
    try:
        eid1 = _db_write_episode("game:ctf:policy_batch", policy_res)
        eid2 = _db_write_episode("game:ctf:explore_batch", explore_res)
        if not eid1 or not eid2:
            db_written = False
    except Exception as e:
        db_written = False
        print(f"[ctf_daily_runner] DB write failed: {e!r}", file=sys.stderr)

    out = {
        "ok": bool(db_written),
        "have_up": True,  # legacy field (we always try UP; ok reflects DB)
        "db_written": bool(db_written),
        "policy": policy_res,
        "explore": explore_res,
    }
    print(json.dumps(out, ensure_ascii=False))

    if db_written:
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"[ctf_daily_runner] FATAL: {e!r}", file=sys.stderr)
        raise SystemExit(3)

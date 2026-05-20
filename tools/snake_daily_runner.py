#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Snake Daily Runner – Policy+Explore → episodes + episodic_metrics
# Version: v3.7.3
# Stand:   2026-02-20
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# HINWEIS (WICHTIG)
# -----------------
# Dieser Runner ist schema-sicher für die v3.7.x DB:
#   • sql_manager.insert_episode(..., meta=...)  → schreibt intern in episodes.meta_json
#
# WARUM DIESER PATCH
# ------------------
# In einem vorherigen Patch wurde versehentlich 'meta_json=' als Keyword verwendet.
# In deiner produktiven sql_manager.py lautet der Parameter jedoch 'meta'.
# Ergebnis: TypeError("insert_episode() got an unexpected keyword argument 'meta_json'").
# Dieser Runner behebt das sauber (minimal-invasiv) und bleibt kompatibel zu deinem Schema.
#
# LERNEN
# ------
# Snake ist ein Single-Agent Spiel. Wir mappen:
#   wins_x = "win" (z.B. Ziel-Länge erreicht)
#   wins_o = 0
#   draws  = "timeout" (max_steps erreicht)
#
# STATE HASH (FULL BOARD)
# -----------------------
# Policy sieht das komplette Spielfeld inkl. eigener Schlange:
#   • Grid als Bytearray (0=leer, 1=body, 2=head, 3=food)
#   • Hash = hex(grid_bytes) + dir + len
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

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

def _env_str(name: str, default: str) -> str:
    v = (os.environ.get(name, "") or "").strip()
    return v if v else default

class PolicyShim:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=namespace)
        except Exception:
            self.pol = None

    def state_hash(self, grid: bytearray, direction: int, length: int) -> str:
        return f"snake:v1:d={direction}:len={length}:g={grid.hex()}"

    def choose(self, state_hash: str, legal: List[int]) -> int:
        if not self.pol:
            return int(random.choice(legal))
        return int(self.pol.choose(state_hash, legal, side="X"))

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not self.pol:
            return 0
        return int(self.pol.learn_many(items))

def _idx(x: int, y: int, w: int) -> int:
    return y * w + x

def run_one_game(rng: random.Random,
                 shim: PolicyShim,
                 mode: str,
                 eps: float,
                 explore_moves_per_game: int,
                 max_steps: int,
                 target_len: int,
                 learn: bool,
                 namespace: str) -> Dict[str, Any]:
    w = _env_int("OROMA_SNAKE_W", 16)
    h = _env_int("OROMA_SNAKE_H", 16)
    legal = [0, 1, 2, 3]  # 0=up,1=right,2=down,3=left

    # init snake (len=3) center
    cx, cy = w // 2, h // 2
    snake: List[Tuple[int,int]] = [(cx, cy), (cx-1, cy), (cx-2, cy)]
    direction = 1  # right
    score_food = 0

    def place_food() -> Tuple[int,int]:
        for _ in range(2000):
            fx, fy = rng.randrange(w), rng.randrange(h)
            if (fx,fy) not in snake:
                return (fx,fy)
        return (0,0)

    food = place_food()
    explore_budget = 0
    last_sa: Tuple[str,int] | None = None
    learn_items: List[Dict[str,Any]] = []
    chain_steps: List[Dict[str, Any]] = []
    current_state_hash: Optional[str] = None

    for step in range(1, max_steps+1):
        # build full-board grid
        grid = bytearray([0] * (w*h))
        for (bx,by) in snake[1:]:
            grid[_idx(bx,by,w)] = 1
        hx,hy = snake[0]
        grid[_idx(hx,hy,w)] = 2
        fx,fy = food
        grid[_idx(fx,fy,w)] = 3

        sh = shim.state_hash(grid, direction, len(snake))
        if not chain_steps:
            current_state_hash = sh
            chain_steps.append({
                "t": int(step) - 1,
                "state_hash": sh,
                "sh": sh,
                "mode": mode,
                "dir": int(direction),
                "len": int(len(snake)),
            })
        else:
            current_state_hash = sh

        # pick action
        if mode == "explore" and (explore_budget < explore_moves_per_game):
            a = int(rng.choice(legal))
            explore_budget += 1
        elif mode == "explore" and (rng.random() < eps):
            a = int(rng.choice(legal))
        else:
            a = int(shim.choose(sh, legal))

        if learn:
            last_sa = (sh, a)

        # prevent instant reverse
        if (a + 2) % 4 == direction:
            a = direction

        direction = a
        dx, dy = [(0,-1),(1,0),(0,1),(-1,0)][direction]
        nx, ny = hx + dx, hy + dy

        # collision wall
        if nx < 0 or ny < 0 or nx >= w or ny >= h:
            outcome = "L"
            terminal_hash = f"snake:terminal:L:wall:step={step}"
            chain_steps.append({
                "t": int(step),
                "state_hash": terminal_hash,
                "sh": terminal_hash,
                "a": int(a),
                "event": "wall",
                "mode": mode,
            })
            break
        # collision self
        if (nx,ny) in snake:
            outcome = "L"
            terminal_hash = f"snake:terminal:L:self:step={step}"
            chain_steps.append({
                "t": int(step),
                "state_hash": terminal_hash,
                "sh": terminal_hash,
                "a": int(a),
                "event": "self",
                "mode": mode,
            })
            break

        snake.insert(0, (nx,ny))

        ate = (nx,ny) == food
        if ate:
            score_food += 1
            food = place_food()
        else:
            snake.pop()

        next_grid = bytearray([0] * (w*h))
        for (bx,by) in snake[1:]:
            next_grid[_idx(bx,by,w)] = 1
        nhx, nhy = snake[0]
        next_grid[_idx(nhx,nhy,w)] = 2
        nfx, nfy = food
        next_grid[_idx(nfx,nfy,w)] = 3
        next_sh = shim.state_hash(next_grid, direction, len(snake))
        chain_steps.append({
            "t": int(step),
            "state_hash": next_sh,
            "sh": next_sh,
            "a": int(a),
            "ate": bool(ate),
            "len": int(len(snake)),
            "score_food": int(score_food),
            "mode": mode,
        })

        # win condition
        if len(snake) >= target_len:
            outcome = "W"
            break
    else:
        outcome = "D"  # timeout
        step = max_steps

    if learn and last_sa:
        sh0, a0 = last_sa
        if outcome == "W":
            r = 1.0
            pos, neg, draw = 1, 0, 0
        elif outcome == "D":
            r = 0.0
            pos, neg, draw = 0, 0, 1
        else:
            r = -1.0
            pos, neg, draw = 0, 1, 0
        learn_items.append({
            "state_hash": sh0,
            "action": a0,
            "reward": r,
            "pos": pos,
            "neg": neg,
            "draw": draw,
            "ts": int(time.time()),
            "side": "X",
        })
        try:
            shim.learn_many(learn_items)
        except Exception:
            pass

    if outcome == "W":
        result = 1
    elif outcome == "L":
        result = -1
    else:
        result = 0

    chain = {
        "schema_version": "3.7.3",
        "kind": "snake_policy_trace",
        "origin": str(namespace or "game:snake"),
        "namespace": str(namespace or "game:snake"),
        "mode": str(mode),
        "result": int(result),
        "score_food": int(score_food),
        "steps_total": int(max(0, len(chain_steps) - 1)),
        "steps": chain_steps,
        "meta": {
            "runner": "tools/snake_daily_runner.py",
            "source": "snake_daily_runner",
            "mode": str(mode),
            "outcome": str(outcome),
            "max_steps": int(max_steps),
            "target_len": int(target_len),
            "grid_w": int(w),
            "grid_h": int(h),
        },
    }

    return {"outcome": outcome, "steps": int(step), "food": int(score_food), "chain": chain}

def run_batch(rng: random.Random,
              namespace: str,
              games: int,
              mode: str,
              eps: float,
              explore_moves_per_game: int,
              learn: bool,
              source: str) -> Dict[str, Any]:
    shim = PolicyShim(namespace)
    ts_start = int(time.time())
    t0 = time.time()

    wins = losses = draws = 0
    steps_sum = 0

    max_steps = _env_int("OROMA_SNAKE_MAX_STEPS", 800)
    target_len = _env_int("OROMA_SNAKE_TARGET_LEN", 20)

    chains: List[Dict[str, Any]] = []

    for _ in range(int(games)):
        r = run_one_game(rng, shim, mode, eps, explore_moves_per_game, max_steps, target_len, learn, namespace)
        steps_sum += r["steps"]
        ch = r.get("chain")
        if isinstance(ch, dict) and ch.get("steps"):
            chains.append(ch)
        if r["outcome"] == "W":
            wins += 1
        elif r["outcome"] == "D":
            draws += 1
        else:
            losses += 1

    dur_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = int(time.time())

    avg_moves = (steps_sum / float(games)) if games else 0.0
    avg_game_ms = (dur_ms / float(games)) if games else 0.0

    return {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": dur_ms,
        "games": float(games),
        "wins_x": float(wins),
        "wins_o": 0.0,
        "draws": float(draws),
        "avg_moves": float(avg_moves),
        "avg_game_ms": float(avg_game_ms),
        "mode": mode,
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(eps),
        "explore_moves_per_game": float(explore_moves_per_game),
        "learn": bool(learn),
        "source": source,
        "label": f"snake:{mode} ({games} games)",
        "runner": "tools/snake_daily_runner.py",
        "shim": "tools/snake_daily_runner.PolicyShim",
        "chains": chains,
        "chains_count": float(len(chains)),
    }

def _write_snapchains(payload: Dict[str, Any]) -> int:
    """
    Persistiert trainierbare Snake-SnapChains direkt in die DB, damit der
    nachgelagerte Policy-Trainer (`core/train_snake_policy.py`) echte
    DB-Datensätze mit origin=game:snake lesen kann.

    WICHTIG:
    - Der Nightly-Runner schrieb bisher nur `episodes` + `episodic_metrics`.
    - `policy_engine.train_from_db()` liest jedoch ausschließlich `snapchains`.
    - Deshalb blieb `trainierte Schritte: 0` über Monate systematisch auf 0.

    Jede Spiel-Session wird hier als JSON-Blob in `snapchains` gespeichert.
    Das Blob-Format ist bewusst prehash-kompatibel (`steps[*].state_hash`,
    `steps[*].a`), damit `PolicyEngine.ingest_chain()` die Schritte ohne
    zusätzlichen Adapter-/Vector-Zwang zuverlässig konsumieren kann.
    """
    inserted = 0
    ts_now = int(payload.get("ts_end", time.time()) or time.time())
    namespace = str(payload.get("namespace") or "game:snake")
    mode = str(payload.get("mode") or "snake")
    source = str(payload.get("source") or "orchestrator")
    chains = payload.get("chains") or []
    if not isinstance(chains, list):
        return 0

    for idx, chain in enumerate(chains, start=1):
        if not isinstance(chain, dict):
            continue
        steps = chain.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
            continue
        try:
            blob = json.dumps(chain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            chain_id = sql_manager.insert_snapchain({
                "ts": ts_now,
                "quality": float(chain.get("result", 0) or 0.0),
                "blob": blob,
                "exported": 0,
                "status": "active",
                "origin": namespace,
                "gap_flag": 0,
                "notes": f"snake_daily:{mode}:steps={max(0, len(steps)-1)}",
                "namespace": namespace,
                "source_id": None,
                "version": "snake_daily_runner:v3.7.3-dbchain",
                "weight": 1.0,
            })
            if chain_id:
                inserted += 1
        except Exception as e:
            print(f"[snake_daily_runner] snapchain write failed #{idx}: {e!r}", file=sys.stderr)
    return int(inserted)


def _write_episode(kind: str, payload: Dict[str, Any]) -> bool:
    try:
        eid = sql_manager.insert_episode(
            ts_start=int(payload.get("ts_start", time.time())),
            kind=kind,
            source=str(payload.get("source") or "orchestrator"),
            label=str(payload.get("label") or ""),
            meta=payload,
            ts_end=int(payload.get("ts_end", time.time())),
        )
        if not eid:
            raise RuntimeError("insert_episode returned None")
        ts = int(payload.get("ts_end", time.time()))
        for k in ("games","wins_x","wins_o","draws","avg_moves","duration_ms","avg_game_ms","eps","explore_moves_per_game","policy_enabled"):
            if k in payload:
                sql_manager.insert_episodic_metric(int(eid), ts, k, float(payload[k]))
        return True
    except Exception as e:
        print(f"[snake_daily_runner] DB write failed: {e!r}", file=sys.stderr)
        return False

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--namespace", type=str, default=_env_str("OROMA_SNAKE_POLICY_NAMESPACE","game:snake"))
    args = ap.parse_args()

    rng = random.Random(int(args.seed))
    eps = _env_float("OROMA_SNAKE_EPS", 0.07)
    explore_moves = _env_int("OROMA_SNAKE_EXPLORE_MOVES_PER_GAME", 1)

    policy_res = run_batch(rng, args.namespace, args.policy_games, mode="policy", eps=0.0, explore_moves_per_game=0, learn=False, source="orchestrator")
    explore_res = run_batch(rng, args.namespace, args.explore_games, mode="explore", eps=eps, explore_moves_per_game=explore_moves, learn=True, source="orchestrator")

    ok1 = _write_episode("game:snake:policy_batch", policy_res)
    ok2 = _write_episode("game:snake:explore_batch", explore_res)
    sc1 = _write_snapchains(policy_res)
    sc2 = _write_snapchains(explore_res)

    out = {
        "ok": bool(ok1 and ok2),
        "have_up": True,
        "db_written": bool(ok1 and ok2),
        "snapchains_written": int(sc1 + sc2),
        "policy_games": int(policy_res.get("games", 0) or 0),
        "explore_games": int(explore_res.get("games", 0) or 0),
        "policy_avg_moves": float(policy_res.get("avg_moves", 0.0) or 0.0),
        "explore_avg_moves": float(explore_res.get("avg_moves", 0.0) or 0.0),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if (ok1 and ok2) else 2

if __name__ == "__main__":
    raise SystemExit(main())

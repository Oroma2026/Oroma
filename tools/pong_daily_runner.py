#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/pong_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Pong Daily Runner – 1×/Tag Policy+Explore Batches → episodes + episodic_metrics
# Version: v3.7.4
# Stand:   2026-06-27
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Führt Pong automatisiert im Headless-Modus aus und schreibt die Ergebnisse in die DB,
# analog zu TicTacToe/Connect4 Daily Runner:
#
#   • policy_batch  : 100 Games (learn=false) – stabiler Benchmark
#   • explore_batch : 100 Games (learn=true)  – epsilon-Explore + Learn in policy_rules
#
# Ergebnis-Persistenz:
#   • oroma.db → episodes(kind='game:pong:policy_batch' / 'game:pong:explore_batch')
#   • oroma.db → episodic_metrics (wins_x/wins_o/draws, duration_ms, avg_game_ms, avg_ticks, eps, …)
#
# DEFINITION "GAME" (PONG)
# ------------------------
# Pong ist kontinuierlich; für Daily Telemetrie definieren wir ein Game als
# "Rally" bis ein Punkt fällt (Ball verlässt Feld links/rechts) oder bis
# MAX_TICKS erreicht wird (Fail-safe Draw).
#
# ACTION SPACE
# ------------
# Diskret: {-1,0,+1} (paddle up / hold / down)
# Side: X = LEFT, O = RIGHT
#
# STATE HASH (Policy)
# -------------------
# Quantisierte Merkmale (robust & kompakt):
#   bx,by (grob), sign(vx), sign(vy), lp,rp (grob) + side
#
# PRODUKTIONSREGELN
# -----------------
# • Keine UI/pygame Abhängigkeiten
# • Keine offenen DB-Conns: sql_manager.get_conn() via Context
# • Keine stillen Fehler: DB-Fehler werden stderr geloggt + ok=false
#
# Änderung v3.7.4 (2026-06-27)
# ------------------------------
# • UniversalPolicy-kompatibles Lernsignal: learn_items enthalten neben reward
#   jetzt auch outcome. Der Wert ist identisch zu reward (+1/-1/0), weil
#   core.universal_policy.Policy.learn_many outcome als primäres Ergebnisfeld
#   auswertet. Ohne outcome wurden Pong-Lernitems als 0.0/neutral behandelt.
# • Der deterministische Daily-Seed wird vom Orchestrator explizit gesetzt.
#   Dieser Runner akzeptiert --seed weiterhin für reproduzierbare Einzeltests.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
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

@dataclass
class PongState:
    w: int
    h: int
    paddle_h: int
    bx: float
    by: float
    bvx: float
    bvy: float
    lp: float
    rp: float
    tick: int

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
    def _q(v: float, step: int, lo: int, hi: int) -> int:
        try:
            x = int(v // step)
        except Exception:
            x = 0
        return max(lo, min(hi, x))

    def state_hash(self, st: PongState, side: str) -> str:
        bx = self._q(st.bx, 16, 0, 40)
        by = self._q(st.by, 12, 0, 30)
        lv = 1 if st.bvx >= 0 else -1
        vv = 1 if st.bvy >= 0 else -1
        lp = self._q(st.lp, 12, 0, 30)
        rp = self._q(st.rp, 12, 0, 30)
        return f"pong:v1:s={side}:bx={bx}:by={by}:vx={lv}:vy={vv}:lp={lp}:rp={rp}"

    def choose(self, st: PongState, legal: List[int], side: str) -> int:
        if not self.pol:
            return int(random.choice(legal))
        sh = self.state_hash(st, side)
        return int(self.pol.choose(sh, legal, side=side))

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not self.pol:
            return 0
        return int(self.pol.learn_many(items))

def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def _reset_state(rng: random.Random, w: int, h: int, paddle_h: int, ball_speed: float) -> PongState:
    return PongState(
        w=w, h=h, paddle_h=paddle_h,
        bx=w/2, by=h/2,
        bvx=(ball_speed if rng.random() < 0.5 else -ball_speed),
        bvy=((ball_speed * 0.6) if rng.random() < 0.5 else -(ball_speed * 0.6)),
        lp=h/2 - paddle_h/2,
        rp=h/2 - paddle_h/2,
        tick=0,
    )

def _ball_paddle_collision(st: PongState, paddle_h: int, ball_speed: float):
    # top/bottom
    if st.by <= 0 or st.by >= st.h:
        st.bvy *= -1
        st.by = _clamp(st.by, 0, st.h)

    left_x = 18
    right_x = st.w - 18
    paddle_w = 8

    # left paddle
    if st.bx <= left_x + paddle_w and st.bx >= left_x:
        if st.lp <= st.by <= st.lp + paddle_h:
            st.bvx = abs(st.bvx)
            rel = (st.by - (st.lp + paddle_h/2)) / (paddle_h/2)
            st.bvy = rel * (ball_speed * 0.9)

    # right paddle
    if st.bx >= right_x - paddle_w and st.bx <= right_x:
        if st.rp <= st.by <= st.rp + paddle_h:
            st.bvx = -abs(st.bvx)
            rel = (st.by - (st.rp + paddle_h/2)) / (paddle_h/2)
            st.bvy = rel * (ball_speed * 0.9)

def run_one_game(rng: random.Random,
                 shim: PolicyShim,
                 mode: str,
                 eps: float,
                 explore_moves_per_game: int,
                 max_ticks: int,
                 learn: bool,
                 namespace: str) -> Dict[str, Any]:
    """
    Führt ein Pong-Spiel aus und liefert neben Gewinner/Ticks auch zwei
    trainierbare Prehash-Traces (Side X / Side O), damit der Daily-Runner
    persistente DB-SnapChains für den nachgelagerten Policy- und Replay-Pfad
    erzeugen kann.

    WICHTIG:
    - Der frühere Runner schrieb nur episodes/episodic_metrics und lernte live
      über PolicyShim.learn_many(...).
    - Nachgelagerte Trainer arbeiten jedoch auf `snapchains`.
    - Deshalb blieb Pong strukturell im selben Altzustand wie Snake vor dem Fix.

    Die hier erzeugten Traces sind bewusst prehash-kompatibel:
    - steps[*].state_hash / sh
    - steps[*].a
    - state_hash enthält die Side bereits (`s=X` / `s=O`)
    """
    # mode: policy/explore
    w, h = 640, 360
    paddle_h = 60
    paddle_speed = 6.0
    ball_speed = 6.0
    legal = [-1, 0, 1]

    st = _reset_state(rng, w, h, paddle_h, ball_speed)
    explore_budget = {"X": 0, "O": 0}
    learn_items: List[Dict[str, Any]] = []

    # store last state/action for simple episodic update
    last_sa = {"X": None, "O": None}

    x_steps: List[Dict[str, Any]] = [{
        "t": 0,
        "state_hash": shim.state_hash(st, "X"),
        "sh": shim.state_hash(st, "X"),
        "mode": mode,
        "side": "X",
    }]
    o_steps: List[Dict[str, Any]] = [{
        "t": 0,
        "state_hash": shim.state_hash(st, "O"),
        "sh": shim.state_hash(st, "O"),
        "mode": mode,
        "side": "O",
    }]

    while True:
        st.tick += 1

        def pick(side: str) -> int:
            if mode == "explore":
                if explore_budget[side] < explore_moves_per_game:
                    explore_budget[side] += 1
                    return int(rng.choice(legal))
                if rng.random() < eps:
                    return int(rng.choice(legal))
            return int(shim.choose(st, legal, side))

        ax = pick("X")
        ao = pick("O")

        # keep for learn (state hash + action)
        if learn:
            last_sa["X"] = (shim.state_hash(st, "X"), ax)
            last_sa["O"] = (shim.state_hash(st, "O"), ao)

        st.lp = _clamp(st.lp + ax * paddle_speed, 0, h - paddle_h)
        st.rp = _clamp(st.rp + ao * paddle_speed, 0, h - paddle_h)

        st.bx += st.bvx
        st.by += st.bvy
        _ball_paddle_collision(st, paddle_h, ball_speed)

        x_steps.append({
            "t": st.tick,
            "state_hash": shim.state_hash(st, "X"),
            "sh": shim.state_hash(st, "X"),
            "a": int(ax),
            "mode": mode,
            "side": "X",
        })
        o_steps.append({
            "t": st.tick,
            "state_hash": shim.state_hash(st, "O"),
            "sh": shim.state_hash(st, "O"),
            "a": int(ao),
            "mode": mode,
            "side": "O",
        })

        # terminal: point or draw
        winner: Optional[str] = None
        event = "draw"
        if st.bx < 0:
            winner = "O"
            event = "goal_right"
        elif st.bx > w:
            winner = "X"
            event = "goal_left"
        elif st.tick >= max_ticks:
            winner = "D"
            event = "timeout"

        if winner is not None:
            if learn:
                # simplistic: assign +1 to winner, -1 to loser, 0 draw (both 0)
                def add_item(side: str, outcome: str):
                    sa = last_sa.get(side)
                    if not sa:
                        return
                    sh, a = sa
                    if outcome == "D":
                        r = 0.0
                        pos = neg = 0
                        draw = 1
                    elif outcome == side:
                        r = 1.0
                        pos, neg, draw = 1, 0, 0
                    else:
                        r = -1.0
                        pos, neg, draw = 0, 1, 0
                    learn_items.append({
                        "state_hash": sh,
                        "action": a,
                        "reward": r,
                        "outcome": r,
                        "pos": pos,
                        "neg": neg,
                        "draw": draw,
                        "ts": int(time.time()),
                        "side": side,
                    })
                add_item("X", winner)
                add_item("O", winner)
                try:
                    shim.learn_many(learn_items)
                except Exception:
                    # learning must not crash runner
                    pass

            x_outcome = 0 if winner == "D" else (1 if winner == "X" else -1)
            o_outcome = 0 if winner == "D" else (1 if winner == "O" else -1)

            # terminal markers: letzter Hash + Event + letzte Aktion
            x_steps.append({
                "t": st.tick + 1,
                "state_hash": f"pong:terminal:s=X:event={event}:tick={st.tick}",
                "sh": f"pong:terminal:s=X:event={event}:tick={st.tick}",
                "a": int(ax),
                "event": event,
                "mode": mode,
                "side": "X",
            })
            o_steps.append({
                "t": st.tick + 1,
                "state_hash": f"pong:terminal:s=O:event={event}:tick={st.tick}",
                "sh": f"pong:terminal:s=O:event={event}:tick={st.tick}",
                "a": int(ao),
                "event": event,
                "mode": mode,
                "side": "O",
            })

            x_chain = {
                "schema_version": "3.7.3",
                "kind": "pong_policy_trace",
                "origin": namespace,
                "namespace": namespace,
                "mode": mode,
                "side": "X",
                "result": int(x_outcome),
                "ticks_total": int(st.tick),
                "steps_total": int(max(0, len(x_steps) - 1)),
                "steps": x_steps,
                "meta": {
                    "runner": "tools/pong_daily_runner.py",
                    "source": "pong_daily_runner",
                    "mode": mode,
                    "winner": winner,
                    "side": "X",
                    "max_ticks": int(max_ticks),
                    "width": int(w),
                    "height": int(h),
                    "paddle_h": int(paddle_h),
                },
            }
            o_chain = {
                "schema_version": "3.7.3",
                "kind": "pong_policy_trace",
                "origin": namespace,
                "namespace": namespace,
                "mode": mode,
                "side": "O",
                "result": int(o_outcome),
                "ticks_total": int(st.tick),
                "steps_total": int(max(0, len(o_steps) - 1)),
                "steps": o_steps,
                "meta": {
                    "runner": "tools/pong_daily_runner.py",
                    "source": "pong_daily_runner",
                    "mode": mode,
                    "winner": winner,
                    "side": "O",
                    "max_ticks": int(max_ticks),
                    "width": int(w),
                    "height": int(h),
                    "paddle_h": int(paddle_h),
                },
            }
            return {"winner": winner, "ticks": st.tick, "chains": [x_chain, o_chain]}

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
    wins_x = wins_o = draws = 0
    ticks_sum = 0
    chains: List[Dict[str, Any]] = []

    max_ticks = _env_int("OROMA_PONG_MAX_TICKS_PER_GAME", 5000)

    for _ in range(int(games)):
        r = run_one_game(rng, shim, mode=mode, eps=eps, explore_moves_per_game=explore_moves_per_game,
                         max_ticks=max_ticks, learn=learn, namespace=namespace)
        w = r["winner"]
        ticks_sum += int(r["ticks"])
        rchains = r.get("chains")
        if isinstance(rchains, list):
            for ch in rchains:
                if isinstance(ch, dict) and isinstance(ch.get("steps"), list) and len(ch.get("steps") or []) >= 2:
                    chains.append(ch)
        if w == "X":
            wins_x += 1
        elif w == "O":
            wins_o += 1
        else:
            draws += 1

    dur_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = int(time.time())

    avg_ticks = (ticks_sum / float(games)) if games else 0.0
    avg_game_ms = (dur_ms / float(games)) if games else 0.0

    return {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": dur_ms,
        "games": float(games),
        "wins_x": float(wins_x),
        "wins_o": float(wins_o),
        "draws": float(draws),
        "avg_ticks": float(avg_ticks),
        "avg_game_ms": float(avg_game_ms),
        "mode": mode,
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(eps),
        "explore_moves_per_game": float(explore_moves_per_game),
        "learn": bool(learn),
        "source": source,
        "label": f"pong:{mode} ({games} games)",
        "runner": "tools/pong_daily_runner.py",
        "shim": "tools/pong_daily_runner.PolicyShim",
        "chains": chains,
        "chains_count": float(len(chains)),
    }

def _write_snapchains(payload: Dict[str, Any]) -> int:
    """
    Persistiert trainierbare Pong-SnapChains direkt in die DB.

    Hintergrund:
    - Pong schrieb bisher nur episodes/episodic_metrics und lernte live via
      PolicyShim.learn_many(...).
    - Nachgelagerte Trainer/Replays lesen jedoch `snapchains`.
    - Dadurch war Pong strukturell im selben Altzustand wie Snake vor dessen
      DB-Chain-Fix.

    Jede Side (X/O) wird pro Spiel als eigene prehash-kompatible Trace-Chain
    gespeichert, damit `core.policy_engine.PolicyEngine.ingest_chain()` die
    Daten ohne zusätzlichen Vector-Zwang verarbeiten kann.
    """
    inserted = 0
    ts_now = int(payload.get("ts_end", time.time()) or time.time())
    namespace = str(payload.get("namespace") or "game:pong")
    mode = str(payload.get("mode") or "pong")
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
                "notes": f"pong_daily:{mode}:side={chain.get('side','?')}:steps={max(0, len(steps)-1)}",
                "namespace": namespace,
                "source_id": None,
                "version": "pong_daily_runner:v3.7.3-dbchain",
                "weight": 1.0,
            })
            if chain_id:
                inserted += 1
        except Exception as e:
            print(f"[pong_daily_runner] snapchain write failed #{idx}: {e!r}", file=sys.stderr)
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
        # store key metrics
        for k in ("games","wins_x","wins_o","draws","avg_ticks","duration_ms","avg_game_ms","eps","explore_moves_per_game","policy_enabled"):
            if k in payload:
                sql_manager.insert_episodic_metric(int(eid), ts, k, float(payload[k]))
        return True
    except Exception as e:
        print(f"[pong_daily_runner] DB write failed: {e!r}", file=sys.stderr)
        return False

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--namespace", type=str, default=_env_str("OROMA_PONG_POLICY_NAMESPACE","game:pong"))
    args = ap.parse_args()

    rng = random.Random(int(args.seed))

    eps = _env_float("OROMA_PONG_EPS", 0.08)
    explore_moves = _env_int("OROMA_PONG_EXPLORE_MOVES_PER_GAME", 1)

    policy_res = run_batch(rng, args.namespace, args.policy_games, mode="policy", eps=0.0, explore_moves_per_game=0, learn=False, source="orchestrator")
    explore_res = run_batch(rng, args.namespace, args.explore_games, mode="explore", eps=eps, explore_moves_per_game=explore_moves, learn=True, source="orchestrator")

    ok1 = _write_episode("game:pong:policy_batch", policy_res)
    ok2 = _write_episode("game:pong:explore_batch", explore_res)
    sc1 = _write_snapchains(policy_res)
    sc2 = _write_snapchains(explore_res)

    out = {
        "ok": bool(ok1 and ok2),
        "have_up": True,
        "db_written": bool(ok1 and ok2),
        "snapchains_written": int(sc1 + sc2),
        "policy_games": int(policy_res.get("games", 0) or 0),
        "explore_games": int(explore_res.get("games", 0) or 0),
        "policy_avg_ticks": float(policy_res.get("avg_ticks", 0.0) or 0.0),
        "explore_avg_ticks": float(explore_res.get("avg_ticks", 0.0) or 0.0),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if (ok1 and ok2) else 2

if __name__ == "__main__":
    raise SystemExit(main())

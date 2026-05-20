#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/connect4_daily_runner.py
# Projekt: ORÓMA v3.7.x
# Modul:   Connect4 Daily Runner (Policy-Benchmark + Explore-Learning)
# Version: v3.7.3
# Stand:   2026-02-19
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Führt Connect4-Selfplay als täglichen Batch-Job aus (headless, ohne UI/Flask):
#   • 1x Policy-Benchmark   (default: 100 Games, eps=0, learn=false)
#   • 1x Explore-Learning   (default: 100 Games, eps>0, learn=true)
#
# Ergebnisse werden in oroma.db abgelegt:
#   • episodes.kind:
#       - game:connect4:policy_batch
#       - game:connect4:explore_batch
#   • episodic_metrics:
#       - games, wins_x, wins_o, draws, avg_moves
#       - duration_ms, avg_game_ms
#       - eps, explore_moves_per_game, policy_enabled
#       - chains_count (produktiv sichtbarer DB-SnapChain-Zähler)
#   • snapchains:
#       - origin / namespace = game:connect4
#       - kompakte Spieltraces für Replay / PolicyEngine / spätere Analyse
#
# DESIGN-ZIELE
# ------------
# • Headless: keine pygame, kein UI-Thread, keine X/Qt Abhängigkeiten
# • DB-safe: Verbindungen werden immer sauber geschlossen (context manager)
# • Robust: Fehler sind sichtbar (stderr + Exit-Code != 0)
# • Referenz: gleiche Telemetrie-Struktur wie TicTacToe Daily Runner
#
# KANONISIERUNG
# -------------
# Connect4 besitzt eine horizontale Spiegel-Symmetrie:
#   • Canon-Board = min(board_str, mirror(board_str)) (lexikographisch)
#   • Action-Mapping:
#       - mirror: col -> (6-col)
#
# ENV / PARAMETER
# ---------------
# OROMA_C4_POLICY_NAMESPACE         (Default: game:connect4)
# OROMA_C4_EPS                      (Default: 0.08)   Explore-Epsilon
# OROMA_C4_EXPLORE_MOVES_PER_GAME   (Default: 1)      Mindest-Random-Moves/Game
#
# CLI
# ---
# python3 tools/connect4_daily_runner.py --policy-games 100 --explore-games 100 --seed 1 --once
#
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


# Core imports (DB + UniversalPolicy)
try:
    from core import sql_manager
except Exception as e:
    sql_manager = None  # type: ignore

try:
    from core import universal_policy as upol
    _HAVE_UP = True
except Exception:
    upol = None  # type: ignore
    _HAVE_UP = False


ROWS, COLS = 6, 7
CONNECT_N = 4


def _now_ts() -> int:
    return int(time.time())


def _board_str(board: List[List[int]], mirror: bool) -> str:
    # board: 0 empty, 1 = X, -1 = O
    parts: List[str] = []
    for r in range(ROWS):
        row = board[r]
        for c in range(COLS):
            cc = (COLS - 1 - c) if mirror else c
            v = row[cc]
            parts.append("1" if v == 1 else "2" if v == -1 else "0")
    return "".join(parts)


def canonicalize(board: List[List[int]]) -> Tuple[str, bool]:
    a = _board_str(board, mirror=False)
    b = _board_str(board, mirror=True)
    if b < a:
        return b, True
    return a, False


def state_hash(board: List[List[int]], side: int) -> Tuple[str, bool]:
    canon, mirrored = canonicalize(board)
    # side: 1 (X) or -1 (O)
    s = "X" if side == 1 else "O"
    return f"c4:{canon}|side:{s}", mirrored


def legal_actions(board: List[List[int]]) -> List[int]:
    return [c for c in range(COLS) if board[0][c] == 0]


def apply_action(board: List[List[int]], col: int, side: int) -> bool:
    if not (0 <= col < COLS):
        return False
    if board[0][col] != 0:
        return False
    for r in range(ROWS - 1, -1, -1):
        if board[r][col] == 0:
            board[r][col] = side
            return True
    return False


def check_winner(board: List[List[int]]) -> int:
    # returns 1 (X), -1 (O), 0 none
    dirs = [(1, 0), (0, 1), (1, 1), (1, -1)]
    for r in range(ROWS):
        for c in range(COLS):
            start = board[r][c]
            if start == 0:
                continue
            for dr, dc in dirs:
                ok = True
                for k in range(1, CONNECT_N):
                    rr = r + dr * k
                    cc = c + dc * k
                    if not (0 <= rr < ROWS and 0 <= cc < COLS):
                        ok = False
                        break
                    if board[rr][cc] != start:
                        ok = False
                        break
                if ok:
                    return start
    return 0


def is_draw(board: List[List[int]]) -> bool:
    return all(board[0][c] != 0 for c in range(COLS))


@dataclass
class BatchResult:
    ts_start: int
    ts_end: int
    duration_ms: int
    games: int
    wins_x: int
    wins_o: int
    draws: int
    avg_moves: float
    avg_game_ms: float
    mode: str
    namespace: str
    policy_enabled: float
    eps: float
    explore_moves_per_game: int
    learn: bool
    source: str
    label: str
    runner: str
    shim: str
    chains_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_start": self.ts_start,
            "ts_end": self.ts_end,
            "duration_ms": self.duration_ms,
            "games": self.games,
            "wins_x": self.wins_x,
            "wins_o": self.wins_o,
            "draws": self.draws,
            "avg_moves": self.avg_moves,
            "avg_game_ms": self.avg_game_ms,
            "mode": self.mode,
            "namespace": self.namespace,
            "policy_enabled": self.policy_enabled,
            "eps": self.eps,
            "explore_moves_per_game": self.explore_moves_per_game,
            "learn": self.learn,
            "source": self.source,
            "label": self.label,
            "runner": self.runner,
            "shim": self.shim,
            "chains_count": self.chains_count,
        }


class PolicyShim:
    def __init__(self, namespace: str) -> None:
        self.namespace = (namespace or "game:connect4").strip()
        self.pol = upol.Policy(namespace=self.namespace) if _HAVE_UP else None

    def choose(self, board: List[List[int]], legal: List[int], side: int) -> Optional[int]:
        if not self.pol:
            return None
        sh, mirrored = state_hash(board, side)
        legal_canon = [(COLS - 1 - a) if mirrored else a for a in legal]
        a_c = self.pol.choose(sh, legal_canon, side=("X" if side == 1 else "O"))
        if a_c is None:
            return None
        a_c_i = int(a_c)
        return (COLS - 1 - a_c_i) if mirrored else a_c_i

    def learn_many(self, items: List[Dict[str, Any]]) -> None:
        if not self.pol:
            return
        self.pol.learn_many(items)




def _center_priority(cols: List[int]) -> List[int]:
    """Sortiert Spalten zentriert statt linksbündig.

    Hintergrund:
      Connect4 kippt bei deterministischen Gleichständen sehr leicht in einen
      ungewollten Linksdrall, wenn legal_actions() einfach [0..6] liefert und
      die erste gleichwertige Aktion gewählt wird. Für produktive ORÓMA-Läufe
      ist eine zentrierte Reihenfolge fachlich deutlich plausibler:
      3,2,4,1,5,0,6
    """
    order = {3: 0, 2: 1, 4: 2, 1: 3, 5: 4, 0: 5, 6: 6}
    return sorted([int(c) for c in cols], key=lambda c: order.get(int(c), 99))


def _build_trace_chain(namespace: str, mode: str, trace: List[Tuple[str, int, int]], winner: int, moves: int) -> Optional[Dict[str, Any]]:
    """Baut eine prehash-kompatible Connect4-SnapChain für die DB.

    Schritte:
      - steps[0] enthält nur den ersten Zustand
      - jeder Folgeschritt trägt die VORHERIGE Aktion in `a`
      - finaler terminal-Step kodiert Ergebnis + letzte Aktion
    """
    if not trace:
        return None
    steps: List[Dict[str, Any]] = []
    first_sh, _first_a, first_side = trace[0]
    steps.append({
        "t": 0,
        "state_hash": str(first_sh),
        "sh": str(first_sh),
        "side": "X" if int(first_side) == 1 else "O",
        "mode": str(mode),
        "ply": 0,
    })
    for idx in range(1, len(trace)):
        cur_sh, _cur_a, cur_side = trace[idx]
        _prev_sh, prev_a, _prev_side = trace[idx - 1]
        steps.append({
            "t": int(idx),
            "state_hash": str(cur_sh),
            "sh": str(cur_sh),
            "a": int(prev_a),
            "side": "X" if int(cur_side) == 1 else "O",
            "mode": str(mode),
            "ply": int(idx),
        })
    last_sh, last_a, last_side = trace[-1]
    if winner == 1:
        terminal = "x_win"
    elif winner == -1:
        terminal = "o_win"
    else:
        terminal = "draw"
    steps.append({
        "t": int(len(trace)),
        "state_hash": f"c4:terminal:{terminal}:moves={int(moves)}",
        "sh": f"c4:terminal:{terminal}:moves={int(moves)}",
        "a": int(last_a),
        "side": "X" if int(last_side) == 1 else "O",
        "mode": str(mode),
        "ply": int(moves),
        "terminal": str(terminal),
    })
    quality = 1.0 if winner == 1 else (-1.0 if winner == -1 else 0.0)
    return {
        "schema_version": "3.7.3",
        "kind": "connect4_policy_trace",
        "origin": str(namespace or "game:connect4"),
        "namespace": str(namespace or "game:connect4"),
        "mode": str(mode),
        "result": float(quality),
        "moves_total": int(moves),
        "steps_total": int(max(0, len(steps) - 1)),
        "steps": steps,
        "meta": {
            "runner": "tools/connect4_daily_runner.py",
            "source": "connect4_daily_runner",
            "winner": int(winner),
            "reward_mode": "terminal_trace",
        },
    }


def _write_snapchains(namespace: str, mode: str, chains: List[Dict[str, Any]], ts_now: int) -> int:
    if not (sql_manager and hasattr(sql_manager, "insert_snapchain")):
        return 0
    written = 0
    for idx, chain in enumerate(chains or [], start=1):
        try:
            steps = chain.get("steps") or []
            if not isinstance(steps, list) or len(steps) < 2:
                continue
            blob = json.dumps(chain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            sid = sql_manager.insert_snapchain({
                "ts": int(ts_now),
                "quality": float(chain.get("result", 0.0) or 0.0),
                "blob": blob,
                "exported": 0,
                "status": "active",
                "origin": str(namespace or "game:connect4"),
                "gap_flag": 0,
                "notes": f"connect4_daily:{mode}:steps={max(0, len(steps)-1)}",
                "namespace": str(namespace or "game:connect4"),
                "source_id": None,
                "version": "connect4_daily_runner:v3.7.3-dbchain",
                "weight": 1.0,
            })
            if sid:
                written += 1
        except Exception as e:
            sys.stderr.write(f"[connect4_daily_runner] snapchain write failed #{idx}: {e!r}\n")
    return int(written)

def run_batch(
    rng: random.Random,
    shim: PolicyShim,
    namespace: str,
    games: int,
    mode: str,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    source: str,
) -> BatchResult:
    ts_start = _now_ts()
    t0 = time.perf_counter()

    wins_x = 0
    wins_o = 0
    draws = 0
    total_moves = 0

    learn_items: List[Dict[str, Any]] = []
    chains: List[Dict[str, Any]] = []

    for gi in range(max(0, int(games))):
        board = [[0] * COLS for _ in range(ROWS)]
        side = 1  # X starts
        moves = 0
        explore_used = 0
        trace: List[Tuple[str, int, int]] = []  # (state_hash, action_canon, side)

        while True:
            legal = _center_priority(legal_actions(board))
            if not legal:
                draws += 1
                break

            # decide explore
            do_rand = False
            if mode == "explore":
                if explore_used < max(0, int(explore_moves_per_game)):
                    do_rand = True
                elif rng.random() < max(0.0, float(eps)):
                    do_rand = True

            if do_rand:
                a = rng.choice(legal)
                explore_used += 1
            else:
                a = shim.choose(board, legal, side)
                if a is None:
                    a = rng.choice(legal)

            # record state hash before move
            sh, mirrored = state_hash(board, side)
            a_canon = (COLS - 1 - int(a)) if mirrored else int(a)
            trace.append((sh, a_canon, side))

            ok = apply_action(board, int(a), side)
            if not ok:
                # should not happen; treat as draw and continue safely
                draws += 1
                break

            moves += 1
            w = check_winner(board)
            if w != 0:
                if w == 1:
                    wins_x += 1
                else:
                    wins_o += 1
                break
            if is_draw(board):
                draws += 1
                break

            side *= -1

        total_moves += moves

        if trace:
            w = check_winner(board)
            chain = _build_trace_chain(namespace=namespace, mode=mode, trace=trace, winner=int(w), moves=int(moves))
            if chain is not None:
                chains.append(chain)

        if learn and trace:
            # outcome from mover perspective
            w = check_winner(board)
            for sh, a_canon, s in trace:
                out = 0.0
                if w == 0:
                    out = 0.0
                else:
                    out = 1.0 if w == s else -1.0
                learn_items.append({
                    "state_hash": sh,
                    "action_canon": int(a_canon),
                    "outcome": float(out),
                    "ts": ts_start,
                })

    # write learn
    if learn and learn_items:
        shim.learn_many(learn_items)

    t1 = time.perf_counter()
    ts_end = _now_ts()
    written_chains = _write_snapchains(namespace=namespace, mode=mode, chains=chains, ts_now=ts_end)
    dur_ms = int(round((t1 - t0) * 1000.0))

    avg_moves = (float(total_moves) / float(games)) if games > 0 else 0.0
    avg_game_ms = (float(dur_ms) / float(games)) if games > 0 else 0.0

    label = f"connect4:{mode} ({games} games)"

    return BatchResult(
        ts_start=ts_start,
        ts_end=ts_end,
        duration_ms=dur_ms,
        games=int(games),
        wins_x=int(wins_x),
        wins_o=int(wins_o),
        draws=int(draws),
        avg_moves=float(round(avg_moves, 4)),
        avg_game_ms=float(round(avg_game_ms, 4)),
        mode=str(mode),
        namespace=str(namespace),
        policy_enabled=1.0 if _HAVE_UP else 0.0,
        eps=float(eps),
        explore_moves_per_game=int(explore_moves_per_game),
        learn=bool(learn),
        source=str(source),
        label=label,
        runner="tools/connect4_daily_runner.py",
        shim="tools/connect4_daily_runner.PolicyShim",
        chains_count=int(written_chains),
    )


def _write_episode(kind: str, label: str, payload: Dict[str, Any], metrics: Dict[str, float]) -> bool:
    """Schreibt eine Batch-Episode + Metrics im **aktuellen** ORÓMA-Schema.

    Wichtig:
      - episodes enthält **meta_json** (nicht payload_json).
      - episodic_metrics enthält zusätzlich eine ts-Spalte.

    Wir nutzen daher ausschließlich die offiziellen Helper aus core.sql_manager:
      - insert_episode(...)
      - insert_episodic_metric(...)
    """
    if not (sql_manager and hasattr(sql_manager, "insert_episode")):
        return False

    ts_start = int(payload.get("ts_start") or _now_ts())
    ts_end = int(payload.get("ts_end") or ts_start)

    try:
        eid = sql_manager.insert_episode(
            ts_start=ts_start,
            ts_end=ts_end,
            kind=str(kind),
            source=str(payload.get("source") or "connect4_daily_runner"),
            label=str(label or payload.get("label") or kind),
            meta=dict(payload or {}),
        )
    except Exception as e:
        sys.stderr.write(f"[connect4_daily_runner] DB insert_episode failed ({kind}): {e!r}\n")
        eid = None

    if eid is None:
        sys.stderr.write(f"[connect4_daily_runner] DB episode not written ({kind})\n")
        return False

    if hasattr(sql_manager, "insert_episodic_metric"):
        ts = ts_end
        for k, v in (metrics or {}).items():
            try:
                sql_manager.insert_episodic_metric(int(eid), int(ts), str(k), float(v))
            except Exception as e:
                sys.stderr.write(f"[connect4_daily_runner] DB insert_episodic_metric failed ({kind}:{k}): {e!r}\n")
    return True
def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Connect4 daily runner (policy + explore)")
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--namespace", type=str, default=os.environ.get("OROMA_C4_POLICY_NAMESPACE", "game:connect4"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--once", action="store_true", help="Compatibility flag (no-op; runner always runs once)")
    args = ap.parse_args()

    seed = int(args.seed or 0)
    rng = random.Random(seed if seed != 0 else int(time.time()))

    namespace = (args.namespace or "game:connect4").strip()
    eps = float(os.environ.get("OROMA_C4_EPS", "0.08"))
    explore_moves_per_game = int(os.environ.get("OROMA_C4_EXPLORE_MOVES_PER_GAME", "1"))

    shim = PolicyShim(namespace=namespace)

    policy_res = run_batch(
        rng=rng,
        shim=shim,
        namespace=namespace,
        games=max(0, int(args.policy_games)),
        mode="policy",
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        source="orchestrator",
    )

    explore_res = run_batch(
        rng=rng,
        shim=shim,
        namespace=namespace,
        games=max(0, int(args.explore_games)),
        mode="explore",
        eps=float(eps),
        explore_moves_per_game=max(0, int(explore_moves_per_game)),
        learn=True,
        source="orchestrator",
    )

    # DB write
    ok1 = _write_episode(
        kind="game:connect4:policy_batch",
        label=policy_res.label,
        payload=policy_res.to_dict(),
        metrics={
            "games": float(policy_res.games),
            "wins_x": float(policy_res.wins_x),
            "wins_o": float(policy_res.wins_o),
            "draws": float(policy_res.draws),
            "avg_moves": float(policy_res.avg_moves),
            "duration_ms": float(policy_res.duration_ms),
            "avg_game_ms": float(policy_res.avg_game_ms),
            "policy_enabled": float(policy_res.policy_enabled),
            "eps": float(policy_res.eps),
            "explore_moves_per_game": float(policy_res.explore_moves_per_game),
            "chains_count": float(policy_res.chains_count),
        },
    )

    ok2 = _write_episode(
        kind="game:connect4:explore_batch",
        label=explore_res.label,
        payload=explore_res.to_dict(),
        metrics={
            "games": float(explore_res.games),
            "wins_x": float(explore_res.wins_x),
            "wins_o": float(explore_res.wins_o),
            "draws": float(explore_res.draws),
            "avg_moves": float(explore_res.avg_moves),
            "duration_ms": float(explore_res.duration_ms),
            "avg_game_ms": float(explore_res.avg_game_ms),
            "policy_enabled": float(explore_res.policy_enabled),
            "eps": float(explore_res.eps),
            "explore_moves_per_game": float(explore_res.explore_moves_per_game),
            "chains_count": float(explore_res.chains_count),
        },
    )

    out = {
        "ok": bool(ok1 and ok2),
        "have_up": bool(_HAVE_UP),
        "db_written": bool(ok1 and ok2),
        "policy": policy_res.to_dict(),
        "explore": explore_res.to_dict(),
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")

    return 0 if (ok1 and ok2) else 2


if __name__ == "__main__":
    raise SystemExit(main())

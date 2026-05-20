#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/tictactoe_daily_runner.py
# Projekt: ORÓMA – Games / Episodic Telemetry
# Modul:   TicTacToe Daily Runner (Policy-only + Explore) – DB Episode Writer
# Version: v1.0
# Stand:   2026-02-19
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt TicTacToe automatisiert headless aus und schreibt die Ergebnisse
#   als Episoden + Metriken in die produktive ORÓMA-DB (oroma.db).
#
#   Ziel ist eine *Referenz-Integration* für weitere Spiele:
#     • klarer, reproduzierbarer Batch-Runner
#     • keine Flask/UI-Abhängigkeit im Ablauf
#     • stabile Policy-API via UniversalPolicyShim (falls verfügbar)
#     • robuste DB-Writes (Connections werden sauber geschlossen)
#
#   Der Runner erzeugt standardmäßig **2 Episoden** pro Ausführung:
#     1) policy-only  (Benchmark)  – N Games
#     2) explore      (Lernmodus)  – N Games (ε-gesteuerte Abweichungen)
#
# Datenmodell (DB)
# ----------------
#   Es wird bewusst *kein* neues DB-Schema benötigt.
#   Stattdessen nutzen wir die bereits vorhandenen Tabellen:
#     • episodes          (1 Zeile pro Batch)
#     • episodic_metrics  (Key/Value je Episode)
#
#   Episode.kind:
#     • "game:tictactoe:policy_batch"
#     • "game:tictactoe:explore_batch"
#
#   Episode.meta_json enthält u.a.:
#     • namespace, eps, explore_moves_per_game
#     • wins/draws
#     • avg_moves, duration_ms, rc
#
# Policy / Lernen
# ---------------
#   • Wenn ui.tictactoe_ui.UniversalPolicyShim verfügbar ist, wird diese
#     Policy genutzt. Das ist identisch zum UI-Policy-Shim (D4-Kanonisierung).
#   • In explore-Batch wird am Game-Ende Feedback in policy_rules geschrieben,
#     indem policy.learn_many([...]) aufgerufen wird.
#   • Im policy-only Batch wird standardmäßig **kein** Lernen durchgeführt,
#     damit der Benchmark nicht durch Training während des Benchmarks driftet.
#     (Kann via --learn-policy-batch aktiviert werden.)
#
# Robustheit
# ----------
#   • Keine Hintergrund-Threads.
#   • Jede DB-Connection wird garantiert geschlossen (context manager).
#   • Fehler werden als rc in meta_json + stderr sichtbar.
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma python3 tools/tictactoe_daily_runner.py --once
#
# ENV
# ---
#   OROMA_TTT_EPS=0.08
#   OROMA_TTT_EPS_MOVES_PER_GAME=1
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Best-effort Imports (Policy + DB)
# -----------------------------------------------------------------------------
try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


_HAVE_UP_SHIM = False
UniversalPolicyShim = None  # type: ignore
_state_hash = None  # type: ignore

try:
    # Reuse the UI-side Shim: stable D4-canon + DB UPSERT in policy_rules.
    # Importing this module does not start the Flask app.
    from ui.tictactoe_ui import UniversalPolicyShim as _UPS, _state_hash as _SH  # type: ignore
    UniversalPolicyShim = _UPS  # type: ignore
    _state_hash = _SH          # type: ignore
    _HAVE_UP_SHIM = True
except Exception:
    _HAVE_UP_SHIM = False


_WINS = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def _check_winner(board: List[str]) -> Optional[str]:
    for a, b, c in _WINS:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None


def _safe_random_move(board: List[str], sym_me: str, legal: List[int]) -> Optional[int]:
    """Safety heuristic (Win → avoid 1-move loss → random)."""
    if not legal:
        return None
    sym_you = "O" if sym_me == "X" else "X"

    for i in legal:
        tmp = board.copy(); tmp[i] = sym_me
        if _check_winner(tmp) == sym_me:
            return i

    safe: List[int] = []
    for i in legal:
        tmp = board.copy(); tmp[i] = sym_me
        bad = False
        for j in range(9):
            if not tmp[j]:
                tmp2 = tmp.copy(); tmp2[j] = sym_you
                if _check_winner(tmp2) == sym_you:
                    bad = True
                    break
        if not bad:
            safe.append(i)

    return random.choice(safe) if safe else random.choice(legal)


class _NullPolicy:
    """Fallback if UniversalPolicyShim is unavailable."""
    def __init__(self, namespace: str = "game:tictactoe"):
        self.namespace = namespace
        self.enabled = False
        self._impl = None

    def choose(self, board: List[str], side: str, legal_moves: List[int]) -> Optional[int]:
        return None

    def learn_many(self, items: List[Dict[str, Any]]) -> None:
        return None


def _policy_pick(policy: Any, board: List[str], side: str, legal: List[int]) -> Optional[int]:
    try:
        if getattr(policy, "enabled", False):
            return policy.choose(board, side, legal)
    except Exception:
        return None
    return None


def _play_one_game(
    policy: Any,
    *,
    mode: str,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
) -> Dict[str, Any]:
    t0 = time.time()
    board: List[str] = [""] * 9
    turn = random.choice(["X", "O"])
    winner: Optional[str] = None
    moves = 0

    explore_used = 0
    traj: List[Dict[str, Any]] = []

    while not winner:
        legal = [i for i, v in enumerate(board) if not v]
        if not legal:
            break

        use_explore = False
        if mode == "explore" and explore_used < explore_moves_per_game:
            if random.random() < float(eps):
                use_explore = True

        idx: Optional[int] = None
        if not use_explore:
            idx = _policy_pick(policy, board, turn, legal)

        if idx is None:
            if 4 in legal:
                idx = 4
            else:
                corners = [i for i in (0, 2, 6, 8) if i in legal]
                idx = _safe_random_move(board, turn, corners or legal)
            if use_explore:
                explore_used += 1

        if idx is None:
            idx = random.choice(legal)

        if learn and _HAVE_UP_SHIM and _state_hash is not None:
            try:
                sh, M, _M_inv = _state_hash(board, turn)  # type: ignore[misc]
                a_canon = int(M[int(idx)])
                traj.append({"state_hash": sh, "action_canon": a_canon, "side": turn})
            except Exception:
                pass

        board[int(idx)] = turn
        moves += 1
        winner = _check_winner(board)
        turn = "O" if turn == "X" else "X"

    if learn and traj:
        try:
            now = int(time.time())
            items = []
            for tr in traj:
                side = tr["side"]
                if winner == side:
                    out = +1.0
                elif winner in ("X", "O"):
                    out = -1.0
                else:
                    out = 0.0
                items.append({
                    "state_hash": tr["state_hash"],
                    "action_canon": tr["action_canon"],
                    "side": side,
                    "outcome": out,
                    "ts": now,
                })
            policy.learn_many(items)
        except Exception:
            pass

    dt_ms = int(round((time.time() - t0) * 1000.0))
    return {"winner": winner or "draw", "moves": int(moves), "duration_ms": int(dt_ms)}


def _db_write_episode(kind: str, meta: Dict[str, Any]) -> Optional[int]:
    if not (sql_manager and hasattr(sql_manager, "insert_episode")):
        return None

    ts0 = int(meta.get("ts_start") or time.time())
    ts1 = int(meta.get("ts_end") or time.time())

    try:
        eid = sql_manager.insert_episode(
            ts_start=ts0,
            ts_end=ts1,
            kind=str(kind),
            source=str(meta.get("source") or "tictactoe_daily_runner"),
            label=str(meta.get("label") or kind),
            meta=meta,
        )
    except Exception as e:
        sys.stderr.write(f"[tictactoe_daily_runner] DB insert_episode failed ({kind}): {e!r}\n")
        eid = None

    if eid is None:
        # No silent failures: surface this visibly (orchestrator logs + rc).
        sys.stderr.write(f"[tictactoe_daily_runner] DB episode not written ({kind})\n")
        return None

    if hasattr(sql_manager, "insert_episodic_metric"):
        ts = int(meta.get("ts_end") or time.time())
        for k in (
            "games", "wins_x", "wins_o", "draws", "avg_moves", "duration_ms", "avg_game_ms",
            "policy_enabled", "eps", "explore_moves_per_game",
        ):
            if k in meta and meta[k] is not None:
                try:
                    sql_manager.insert_episodic_metric(int(eid), ts, str(k), float(meta[k]))
                except Exception as e:
                    sys.stderr.write(f"[tictactoe_daily_runner] DB insert_episodic_metric failed ({kind}:{k}): {e!r}\n")
    return eid


def run_batch(
    *,
    mode: str,
    games: int,
    namespace: str,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    seed: Optional[int],
    source: str,
) -> Dict[str, Any]:
    if seed is not None:
        random.seed(int(seed))

    policy: Any = _NullPolicy(namespace=namespace)
    if _HAVE_UP_SHIM and UniversalPolicyShim is not None:
        try:
            policy = UniversalPolicyShim(namespace=namespace)  # type: ignore
        except Exception:
            policy = _NullPolicy(namespace=namespace)

    t0 = time.time()
    wins_x = wins_o = draws = 0
    moves_sum = 0
    game_ms_sum = 0

    for _ in range(int(games)):
        g = _play_one_game(policy, mode=mode, eps=eps, explore_moves_per_game=explore_moves_per_game, learn=learn)
        w = g.get("winner")
        if w == "X":
            wins_x += 1
        elif w == "O":
            wins_o += 1
        else:
            draws += 1
        moves_sum += int(g.get("moves") or 0)
        game_ms_sum += int(g.get("duration_ms") or 0)

    dt_ms = int(round((time.time() - t0) * 1000.0))
    avg_moves = float(moves_sum / max(1, int(games)))
    avg_game_ms = float(game_ms_sum / max(1, int(games)))

    return {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": int(dt_ms),
        "games": int(games),
        "wins_x": int(wins_x),
        "wins_o": int(wins_o),
        "draws": int(draws),
        "avg_moves": float(round(avg_moves, 4)),
        "avg_game_ms": float(round(avg_game_ms, 3)),
        "mode": str(mode),
        "namespace": str(namespace),
        "policy_enabled": 1.0 if getattr(policy, "enabled", False) else 0.0,
        "eps": float(eps),
        "explore_moves_per_game": int(explore_moves_per_game),
        "learn": bool(learn),
        "source": str(source),
        "label": f"tictactoe:{mode} ({games} games)",
        "runner": "tools/tictactoe_daily_runner.py",
        "shim": "ui.tictactoe_ui.UniversalPolicyShim" if _HAVE_UP_SHIM else "fallback",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA TicTacToe daily runner (policy + explore)")
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--namespace", default="game:tictactoe")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--eps", type=float, default=None)
    ap.add_argument("--explore-moves-per-game", type=int, default=None)
    ap.add_argument("--learn-policy-batch", action="store_true")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    eps = float(args.eps) if args.eps is not None else float(os.environ.get("OROMA_TTT_EPS", "0.08") or "0.08")
    eps = max(0.0, min(1.0, eps))
    epm = int(args.explore_moves_per_game) if args.explore_moves_per_game is not None else int(os.environ.get("OROMA_TTT_EPS_MOVES_PER_GAME", "1") or "1")
    if epm < 0:
        epm = 0

    source = os.environ.get("OROMA_RUN_SOURCE", "orchestrator")

    meta_policy = run_batch(
        mode="policy",
        games=int(args.policy_games),
        namespace=str(args.namespace),
        eps=0.0,
        explore_moves_per_game=0,
        learn=bool(args.learn_policy_batch),
        seed=args.seed,
        source=source,
    )
    meta_explore = run_batch(
        mode="explore",
        games=int(args.explore_games),
        namespace=str(args.namespace),
        eps=eps,
        explore_moves_per_game=epm,
        learn=True,
        seed=(None if args.seed is None else int(args.seed) + 1),
        source=source,
    )

    db_written = False
    if not args.no_db:
        eid1 = _db_write_episode("game:tictactoe:policy_batch", meta_policy)
        eid2 = _db_write_episode("game:tictactoe:explore_batch", meta_explore)
        db_written = bool(eid1 and eid2)
        if not db_written:
            sys.stderr.write("[tictactoe_daily_runner] ERROR: DB write incomplete (episodes missing).\n")
            # Exit non-zero so orchestrator state clearly shows failure.
            # (The orchestrator itself will continue running other jobs.)
            sys.stdout.write(json.dumps({
                "ok": False,
                "have_up_shim": bool(_HAVE_UP_SHIM),
                "db_written": False,
                "policy": meta_policy,
                "explore": meta_explore,
            }, ensure_ascii=False) + "\n")
            return 2

    sys.stdout.write(json.dumps({
        "ok": True,
        "have_up_shim": bool(_HAVE_UP_SHIM),
        "db_written": bool(db_written),
        "policy": meta_policy,
        "explore": meta_explore,
    }, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

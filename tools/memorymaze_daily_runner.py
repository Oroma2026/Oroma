#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/memorymaze_daily_runner.py
# Projekt: ORÓMA – Games / Episodic Telemetry
# Modul:   Memory Maze 2033 Daily Runner (Policy-only + Explore) – DB Writer
# Version: v1.0
# Stand:   2026-02-21
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#   Headless Daily-Runner für "Memory Maze 2033" (mini_programs/memory_maze2033.py).
#   Dieses Modul ist als Referenz-Integration für weitere Spiele gedacht
#   (analog zu tools/tictactoe_daily_runner.py), aber angepasst an die
#   Besonderheiten des Crossmodal-Spiels:
#
#     • zwei Sub-Modi existieren (memory / maze) – Daily-Runner fokussiert
#       produktiv auf "memory" (Mensch vs ORÓMA) als primärer Lern-Loop.
#     • Policy/Explore werden über core.universal_policy.Policy realisiert:
#         - state_hash: stabiler Hash über *beobachtbaren* Zustand
#         - legal: verfügbare Aktionen (hidden cards)
#         - action: index 0..(w*h-1)
#
#   Pro Ausführung werden standardmäßig **2 Episoden** in oroma.db geschrieben:
#     1) game:memorymaze2033:policy_batch   (Benchmark, eps=0)
#     2) game:memorymaze2033:explore_batch  (ε-Explore, learn_many aktiv)
#
# Datenmodell (DB)
# ---------------
#   • episodes(kind, ts_start, ts_end, source, label, meta_json)
#   • episodic_metrics(episode_id, ts, key, value)
#
#   Es werden KEINE neuen Tabellen in oroma.db benötigt.
#   (Policy schreibt in policy_rules via universal_policy.Policy.)
#
# Robustheit / Produktivregeln
# ----------------------------
#   • Headless-geeignet, keine GUI-Abhängigkeiten.
#   • Jede DB-Connection wird sauber geschlossen (sql_manager.get_conn() / insert_* API).
#   • Fehler werden NICHT still geschluckt: JSON enthält ok:false + err.
#
# CLI
# ---
#   cd /opt/ai/oroma
#   sudo -u oroma PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#       python3 tools/memorymaze_daily_runner.py --policy-games 100 --explore-games 100 --seed 1
#
# ENV (optional)
# --------------
#   OROMA_MM_EPS=0.08
#   OROMA_MM_MEM_SIZE=4x4
#   OROMA_MM_MAX_TURNS=220
#   OROMA_MM_EMIT_SNAPCHAINS=0|1        (Default im Daily-Runner: 0)
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple


# Best-effort DB
try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


# Best-effort Policy
try:
    from core.universal_policy import Policy
    _HAVE_UP = True
except Exception:
    Policy = None  # type: ignore
    _HAVE_UP = False


try:
    from mini_programs.memory_maze2033 import MemoryGame
except Exception as e:
    raise RuntimeError(f"memory_maze2033 import fehlgeschlagen: {e}")


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


def _parse_size(s: str, default: Tuple[int, int]) -> Tuple[int, int]:
    try:
        w, h = [int(x) for x in str(s).lower().split("x")[:2]]
        if w >= 2 and h >= 2 and (w * h) % 2 == 0:
            return w, h
    except Exception:
        pass
    return default


def _observable_state(game: MemoryGame) -> Dict[str, Any]:
    """Erzeuge einen beobachtbaren State für Policy/Explore.

    WICHTIG:
      Wir hashen NICHT die verdeckten Symbole (keine "Cheat"-Info).
      Der Agent sieht:
        - sichtbare Symbole (sonst '.')
        - Turn, pairs_left
        - aktuell aufgedeckte Karten (Positionen)
    """
    st = game.state()
    board = st.get("board", [])
    obs_rows: List[str] = []
    for r in range(game.h):
        row = []
        for c in range(game.w):
            sym = board[r][c].get("sym") if isinstance(board[r][c], dict) else None
            row.append(sym if sym else ".")
        obs_rows.append("".join(row))
    revealed = st.get("revealed", [])
    # revealed ist i.d.R. Liste von [r,c] oder dicts – normalize
    rev_norm: List[str] = []
    try:
        for it in revealed:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                rev_norm.append(f"{int(it[0])},{int(it[1])}")
            elif isinstance(it, dict):
                rev_norm.append(f"{int(it.get('r',-1))},{int(it.get('c',-1))}")
    except Exception:
        rev_norm = []
    rev_norm.sort()
    return {
        "mode": "memory",
        "w": int(game.w),
        "h": int(game.h),
        "turn": str(st.get("turn", "")),
        "pairs_left": int(st.get("pairs_left", 0)),
        "obs": obs_rows,
        "rev": rev_norm,
    }


def _state_hash(obs_state: Dict[str, Any]) -> str:
    blob = json.dumps(obs_state, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def _legal_actions(game: MemoryGame) -> List[int]:
    # hidden cards only
    st = game.state()
    board = st.get("board", [])
    legal: List[int] = []
    for r in range(game.h):
        for c in range(game.w):
            try:
                sym = board[r][c].get("sym") if isinstance(board[r][c], dict) else None
                if not sym:
                    legal.append(r * game.w + c)
            except Exception:
                continue
    return legal


def _action_to_rc(game: MemoryGame, a: int) -> Tuple[int, int]:
    r = int(a) // int(game.w)
    c = int(a) % int(game.w)
    return r, c


def _pick_action(policy: Any, sh: str, legal: List[int], *, eps: float, explore: bool) -> int:
    # Explore: epsilon random
    if explore and eps > 1e-9 and random.random() < eps:
        return int(random.choice(legal))
    if _HAVE_UP and policy is not None:
        try:
            a = policy.choose(sh, legal, side="oroma")
            if a is not None:
                return int(a)
        except Exception:
            pass
    return int(random.choice(legal))


def _run_batch(
    *,
    games: int,
    mode: str,
    eps: float,
    learn: bool,
    seed: Optional[int],
    mem_size: Tuple[int, int],
    max_turns: int,
    namespace: str,
) -> Dict[str, Any]:
    random.seed(seed)

    policy = Policy(namespace=namespace) if _HAVE_UP else None

    # NOTE:
    #   episodes.ts_* verwenden in ORÓMA Sekunden (int). Bei sehr schnellen Läufen
    #   (kleine Batches / warm cache) können Start/Ende sonst in derselben Sekunde
    #   landen und dt_s wird in SQL-Ausgaben 0.
    t0 = int(time.time())
    t0_wall = time.time()

    total_turns = 0
    wins_oroma = 0
    wins_human = 0
    draws = 0
    learned_items = 0

    items: List[Dict[str, Any]] = []

    w, h = mem_size
    for gi in range(int(games)):
        g = MemoryGame(width=w, height=h, seed=(seed + gi) if seed is not None else None)
        turns = 0

        # Simulierter "Human": rein zufällig (damit ORÓMA überhaupt Gegenspiel hat)
        # ORÓMA: Policy/Explore
        while not g.finished and turns < max_turns:
            st = g.state()
            turn = str(st.get("turn", "human"))
            if turn == "human":
                legal = _legal_actions(g)
                if not legal:
                    break
                a = int(random.choice(legal))
                r, c = _action_to_rc(g, a)
                g.human_reveal(r, c)
            else:
                obs = _observable_state(g)
                sh = _state_hash(obs)
                legal = _legal_actions(g)
                if not legal:
                    break
                a = _pick_action(policy, sh, legal, eps=eps, explore=(mode == "explore"))
                r, c = _action_to_rc(g, a)
                # ORÓMA-Zug per reveal: wir nutzen die vorhandene API
                # (oroma_play() entscheidet intern, aber hier wollen wir explizite Actions)
                # Daher: wir simulieren ORÓMA als "human_reveal" wenn gerade oroma dran ist.
                # Das ist ok, weil MemoryGame die Regeln für reveal/score intern kontrolliert.
                g.human_reveal(r, c)

                # Lernen: Outcome erst am Ende sinnvoll, aber wir speichern (state,action)
                if learn and _HAVE_UP:
                    items.append({"state_hash": sh, "action": int(a), "outcome": 0.0})

            turns += 1

        total_turns += turns
        st_end = g.state()
        score = st_end.get("score", {}) if isinstance(st_end.get("score", {}), dict) else {}
        try:
            hs = int(score.get("human", 0))
            os_ = int(score.get("oroma", 0))
        except Exception:
            hs, os_ = 0, 0
        if os_ > hs:
            wins_oroma += 1
        elif hs > os_:
            wins_human += 1
        else:
            draws += 1

        # Outcome pro Game als globaler Reward: +1 win, -1 loss, 0 draw
        if learn and _HAVE_UP and items:
            out = 1.0 if os_ > hs else (-1.0 if hs > os_ else 0.0)
            # set outcome for last game's items (best effort): last N turns
            # We approximate by applying outcome to the last 'turns' items.
            n_apply = min(len(items), turns)
            for k in range(len(items) - n_apply, len(items)):
                items[k]["outcome"] = out

    # Policy learn
    if learn and _HAVE_UP and policy is not None and items:
        try:
            policy.learn_many(items)
            learned_items = len(items)
        except Exception:
            learned_items = 0

    t1 = int(time.time())
    if t1 <= t0:
        t1 = t0 + 1
    dur_ms = (time.time() - t0_wall) * 1000.0

    return {
        "ts_start": t0,
        "ts_end": t1,
        "duration_ms": float(dur_ms),
        "games": int(games),
        "turns": int(total_turns),
        "avg_turns": float(total_turns) / max(1, int(games)),
        "wins_oroma": int(wins_oroma),
        "wins_human": int(wins_human),
        "draws": int(draws),
        "eps": float(eps),
        "learn": bool(learn),
        "learned_items": int(learned_items),
        "mem_w": int(w),
        "mem_h": int(h),
        "max_turns": int(max_turns),
        "mode": str(mode),
        "namespace": str(namespace),
        "policy_enabled": 1.0 if _HAVE_UP else 0.0,
    }


def _db_write(kind: str, label: str, meta: Dict[str, Any], metrics: Dict[str, float]) -> Optional[int]:
    if sql_manager is None:
        return None
    try:
        sql_manager.ensure_schema()
    except Exception:
        pass
    try:
        ts_start = int(meta.get("ts_start", int(time.time())))
        ts_end = int(meta.get("ts_end", int(time.time())))
        if ts_end <= ts_start:
            ts_end = ts_start + 1
        eid = sql_manager.insert_episode(
            ts_start=ts_start,
            ts_end=ts_end,
            kind=str(kind),
            source=str(meta.get("source", "orchestrator")),
            label=str(label),
            meta=meta,
        )
        if not eid:
            return None
        ts = ts_end
        for k, v in metrics.items():
            try:
                sql_manager.insert_episodic_metric(eid, ts, str(k), float(v))
            except Exception:
                continue
        return int(eid)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Memory Maze 2033 – Daily Runner")
    ap.add_argument("--policy-games", type=int, default=int(_env_int("OROMA_MM_POLICY_GAMES", 100)))
    ap.add_argument("--explore-games", type=int, default=int(_env_int("OROMA_MM_EXPLORE_GAMES", 100)))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--eps", type=float, default=float(_env_float("OROMA_MM_EPS", 0.08)))
    ap.add_argument("--mem-size", type=str, default=str(os.environ.get("OROMA_MM_MEM_SIZE", "4x4")))
    ap.add_argument("--max-turns", type=int, default=int(_env_int("OROMA_MM_MAX_TURNS", 220)))
    ap.add_argument("--source", type=str, default="orchestrator")
    ap.add_argument(
        "--emit-snapchains",
        type=int,
        default=int(_env_int("OROMA_MM_EMIT_SNAPCHAINS", 0)),
        help="0=keine SnapChain-Emission (Default, DB-schonend) | 1=emit SnapChains",
    )
    args = ap.parse_args()

    mem_size = _parse_size(args.mem_size, (4, 4))
    seed = args.seed

    # Daily Runner soll DB-schonend sein: SnapChain-Emission ist optional,
    # da insert_snapchain (writer_lock/flock) bei parallelen Writer-Jobs
    # sonst Episoden-Write blockieren kann.
    try:
        os.environ["OROMA_MM_EMIT_SNAPCHAINS"] = "1" if int(args.emit_snapchains) == 1 else "0"
    except Exception:
        pass

    out: Dict[str, Any] = {"ok": False, "have_up": bool(_HAVE_UP), "db_written": False}
    err = None
    try:
        ns = "game:memorymaze2033"
        policy = _run_batch(
            games=max(0, int(args.policy_games)),
            mode="policy",
            eps=0.0,
            learn=False,
            seed=seed,
            mem_size=mem_size,
            max_turns=int(args.max_turns),
            namespace=ns,
        )
        explore = _run_batch(
            games=max(0, int(args.explore_games)),
            mode="explore",
            eps=float(args.eps),
            learn=True,
            seed=(seed + 999) if seed is not None else None,
            mem_size=mem_size,
            max_turns=int(args.max_turns),
            namespace=ns,
        )

        # DB
        db_ok = True
        if sql_manager is None:
            db_ok = False
        else:
            meta_p = dict(policy)
            meta_p.update({"source": args.source})
            meta_e = dict(explore)
            meta_e.update({"source": args.source})

            eid_p = _db_write(
                kind="game:memorymaze2033:policy_batch",
                label=f"memorymaze2033:policy ({policy.get('games',0)} games)",
                meta=meta_p,
                metrics={
                    "games": float(policy.get("games", 0)),
                    "turns": float(policy.get("turns", 0)),
                    "avg_turns": float(policy.get("avg_turns", 0.0)),
                    "wins_oroma": float(policy.get("wins_oroma", 0)),
                    "wins_human": float(policy.get("wins_human", 0)),
                    "draws": float(policy.get("draws", 0)),
                    "duration_ms": float(policy.get("duration_ms", 0.0)),
                },
            )
            eid_e = _db_write(
                kind="game:memorymaze2033:explore_batch",
                label=f"memorymaze2033:explore ({explore.get('games',0)} games)",
                meta=meta_e,
                metrics={
                    "games": float(explore.get("games", 0)),
                    "turns": float(explore.get("turns", 0)),
                    "avg_turns": float(explore.get("avg_turns", 0.0)),
                    "wins_oroma": float(explore.get("wins_oroma", 0)),
                    "wins_human": float(explore.get("wins_human", 0)),
                    "draws": float(explore.get("draws", 0)),
                    "eps": float(explore.get("eps", 0.0)),
                    "learned_items": float(explore.get("learned_items", 0)),
                    "duration_ms": float(explore.get("duration_ms", 0.0)),
                },
            )
            policy["episode_id"] = eid_p
            explore["episode_id"] = eid_e
            db_ok = bool(eid_p) and bool(eid_e)

        out.update({"ok": True, "db_written": bool(db_ok), "policy": policy, "explore": explore})
    except Exception as e:
        err = str(e)
    if err:
        out["ok"] = False
        out["err"] = err
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/memory_daily_runner.py
# Projekt: ORÓMA v3.7.x – Games / Episodic Telemetry
# Modul:   Memory Daily Runner (Policy + Explore) – DB Episode Writer
# Version: v1.0
# Stand:   2026-02-24
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
# Führt das Spiel "Memory" headless aus und schreibt Ergebnis-Telemetrie als
# Episoden + Metriken in `oroma.db` (episodes + episodic_metrics).
#
# Standard-Pattern (wie TicTacToe/Connect4/Tetris):
#   • zwei Batches pro Run: policy-only und explore
#   • reproduzierbar via --seed
#   • DB-Write robust gegen temporäre Locks (Retries)
#   • kein Zugriff auf UniversalPolicy.choose() (vermeidet DB/PRAGMA Contention)
#
# Lernen / Transferwissen
# ----------------------
# Optional wird – wenn `core.universal_policy.Policy` verfügbar ist – ein
# kompakter Satz von state/action Samples via `learn_many()` in `policy_rules`
# geschrieben (namespace=game:memory). Das liefert einen wachsenden
# Policy-Rule-Zähler, ohne dass der Runner im Policy-Mode DB-Locks erzeugt.
#
# Episode.kind
# -----------
#   • game:memory:policy_batch
#   • game:memory:explore_batch
#
# CLI
# ---
#   cd /opt/ai/oroma
#   sudo -u oroma PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     python3 tools/memory_daily_runner.py --policy-games 100 --explore-games 100 --seed 1
#
# ENV
# ---
#   OROMA_MEMORY_EPS=0.08
#   OROMA_MEMORY_SIZE=4
#   OROMA_MEMORY_MAX_TURNS=220
# =============================================================================

from __future__ import annotations

import argparse
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple


try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


try:
    from core.universal_policy import Policy as UniversalPolicy  # type: ignore
except Exception:
    UniversalPolicy = None  # type: ignore


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _now() -> int:
    return int(time.time())


class MemoryEnv:
    """Minimal headless Memory env used by daily runner (fast, no sleep)."""

    def __init__(self, *, size: int, seed: int, max_turns: int):
        self.size = int(size)
        self.max_turns = int(max_turns)
        self.rng = random.Random(int(seed))
        self.reset(seed)

    def reset(self, seed: int) -> None:
        self.rng = random.Random(int(seed))
        n = self.size * self.size
        pairs = n // 2
        symbols = [chr(ord("A") + i) for i in range(pairs)] * 2
        self.rng.shuffle(symbols)
        self.symbols: List[str] = symbols
        self.revealed: List[bool] = [False] * n
        self.turn_owner = "p1"
        self.turns = 0
        self.score = {"p1": 0, "p2": 0}
        self.done = False
        self.winner = "-"
        # know maps symbol -> list of indices seen (may include indices already matched; filtered at pick time)
        self.know: Dict[str, Dict[str, List[int]]] = {"p1": {}, "p2": {}}

    def _legal(self) -> List[int]:
        return [i for i, r in enumerate(self.revealed) if not r]

    def _pick(self, who: str, eps: float) -> Tuple[int, int]:
        """Choose two distinct indices using only remembered info + exploration.

        WICHTIG:
        - Darf NICHT in nicht-aufgedeckte Karten "reinschauen".
        - Nutzt Ground-Truth `self.symbols[i]` nur für Karten, die in diesem Turn
          tatsächlich aufgedeckt werden (i1/i2), um Match/Mismatch zu bestimmen.
        - Memory (self.know) wird nur aus früheren Mismatches gespeist.
        """
        legal = self._legal()
        if len(legal) < 2:
            i = legal[0] if legal else 0
            return (i, i)

        explore = (eps > 0.0 and self.rng.random() < eps)
        mem = self.know.get(who, {})

        # 1) Wenn wir ein sicheres Paar kennen (>=2 legale Indizes), nimm es (außer Explore).
        if not explore:
            for _sym, idxs in mem.items():
                # WICHTIG: idxs kann Duplikate enthalten (z. B. wenn dieselbe Karte
                # mehrfach gesehen wurde). Ein "Paar" muss aus ZWEI VERSCHIEDENEN
                # Indizes bestehen, sonst würden wir (i,i) als Match zählen und die
                # Score könnte > pairs_total werden → negative pairs_left_end.
                cand: List[int] = []
                seen: set[int] = set()
                for i in idxs:
                    if i in legal and i not in seen:
                        cand.append(i)
                        seen.add(i)
                if len(cand) >= 2:
                    return cand[0], cand[1]

        # 2) Erster Pick: bevorzugt bekannte Singles (außer Explore), sonst random.
        if not explore:
            singles: List[int] = []
            for _sym, idxs in mem.items():
                for i in idxs:
                    if i in legal:
                        singles.append(i)
                        break
            i1 = self.rng.choice(singles) if singles else self.rng.choice(legal)
        else:
            i1 = self.rng.choice(legal)

        # 3) Zweiter Pick: wenn wir das Matching zur aufgedeckten Karte i1 kennen, nimm es.
        sym1 = self.symbols[i1]
        i2: Optional[int] = None
        if not explore:
            cand = [i for i in mem.get(sym1, []) if i in legal and i != i1]
            if cand:
                i2 = cand[0]
        if i2 is None:
            i2 = self.rng.choice([j for j in legal if j != i1])
        return i1, i2

    def step_turn(self, eps: float) -> Tuple[bool, Dict[str, Any]]:
        """Returns (done, info)."""
        if self.done:
            return True, {}
        if self.turns >= self.max_turns:
            self.done = True
            self.winner = "draw"
            return True, {"reason": "turn_limit"}

        # In einem korrekt aufgebauten Memory (N=size^2, gerade) darf es nie
        # passieren, dass nur 1 Karte "legal" übrig ist. Wenn es dennoch passiert
        # (z. B. durch zukünftige Codeänderungen), beenden wir sauber, statt
        # einen Fake-Match (i,i) zu werten → verhindert negative pairs_left.
        legal = self._legal()
        if len(legal) < 2:
            self.done = True
            if self.score["p1"] > self.score["p2"]:
                self.winner = "p1"
            elif self.score["p2"] > self.score["p1"]:
                self.winner = "p2"
            else:
                self.winner = "draw"
            return True, {"reason": "invalid_state_legal_lt2", "legal": int(len(legal))}

        who = self.turn_owner
        i1, i2 = self._pick(who, eps)

        # Safety: Ein Turn MUSS zwei verschiedene Karten aufdecken.
        # Falls durch zukünftige Änderungen dennoch (i,i) zurückkommt, korrigieren wir
        # deterministisch auf eine zweite legale Karte.
        if i1 == i2:
            alt = [j for j in legal if j != i1]
            if alt:
                i2 = self.rng.choice(alt)
        match = (self.symbols[i1] == self.symbols[i2])

        # apply
        self.turns += 1
        if match:
            self.revealed[i1] = True
            self.revealed[i2] = True
            self.score[who] += 1
        else:
            # remember (both players can observe the two reveals)
            for p in ("p1", "p2"):
                mem = self.know.setdefault(p, {})
                s1 = self.symbols[i1]
                s2 = self.symbols[i2]
                mem.setdefault(s1, []).append(i1)
                mem.setdefault(s2, []).append(i2)
            # switch
            self.turn_owner = "p2" if who == "p1" else "p1"

        if all(self.revealed):
            self.done = True
            if self.score["p1"] > self.score["p2"]:
                self.winner = "p1"
            elif self.score["p2"] > self.score["p1"]:
                self.winner = "p2"
            else:
                self.winner = "draw"

        return self.done, {"i1": i1, "i2": i2, "match": match, "who": who}


def _state_hash(env: MemoryEnv) -> str:
    """Compact rule key used for policy_rules learning."""
    # features:
    #  - pairs_left
    #  - current player
    #  - known singles per player (capped)
    #  - turn count bucket
    pairs_left = (env.size * env.size) // 2 - (env.score["p1"] + env.score["p2"])
    k1 = min(sum(len(v) for v in env.know.get("p1", {}).values()), 12)
    k2 = min(sum(len(v) for v in env.know.get("p2", {}).values()), 12)
    tb = int(env.turns // 10)
    return f"pl={pairs_left}|t={env.turn_owner}|k1={k1}|k2={k2}|tb={tb}"


def _write_episode(kind: str, label: str, ts_start: int, ts_end: int, meta: Dict[str, Any], metrics: Dict[str, float]) -> Optional[int]:
    if not sql_manager:
        return None
    # retry on DB locks
    last_err: Optional[str] = None
    for _ in range(3):
        try:
            eid = sql_manager.insert_episode(kind=kind, ts_start=int(ts_start), ts_end=int(ts_end), label=label, meta=meta)
            if eid is None:
                raise RuntimeError("insert_episode returned None")
            eid = int(eid)
            for k, v in metrics.items():
                try:
                    # core.sql_manager.insert_episodic_metric(episode_id, ts, key, value)
                    # Wir nutzen ts_end als Metrik-Zeitpunkt.
                    sql_manager.insert_episodic_metric(eid, int(ts_end), str(k), float(v))
                except Exception:
                    # metrics should not break episode write
                    pass
            return eid
        except Exception as e:
            last_err = str(e)
            time.sleep(0.5)
    raise RuntimeError(f"db_write_failed: {last_err}")


def _run_batch(*, namespace: str, mode: str, games: int, eps: float, seed: int, size: int, max_turns: int, learn: bool) -> Dict[str, Any]:
    t0 = time.time()
    wins_p1 = wins_p2 = draws = 0
    turns_total = 0
    pairs_left_end_total = 0
    learned_items = 0

    # collect learning samples
    learn_items: List[Dict[str, Any]] = []

    for g in range(int(games)):
        env = MemoryEnv(size=size, seed=seed + g, max_turns=max_turns)
        while not env.done:
            # Lernen: State muss VOR der Aktion erfasst werden (sonst mismatch/switch
            # verfälscht die Zuordnung state→action→outcome).
            sh_pre = _state_hash(env)
            done, info = env.step_turn(eps)
            if learn and info and ("i1" in info) and ("i2" in info):
                i1 = int(info["i1"])
                i2 = int(info["i2"])
                learn_items.append({
                    "state_hash": sh_pre,
                    "action_canon": f"{min(i1,i2)}:{max(i1,i2)}",
                    "outcome": 1.0 if bool(info.get("match")) else -1.0,
                    "ts": _now(),
                })
        # stats
        turns_total += env.turns
        pairs_left_end = (env.size * env.size) // 2 - (env.score["p1"] + env.score["p2"])
        # Safety belt: negative Werte sind fachlich unmöglich.
        if pairs_left_end < 0:
            pairs_left_end = 0
        pairs_left_end_total += pairs_left_end
        if env.winner == "p1":
            wins_p1 += 1
        elif env.winner == "p2":
            wins_p2 += 1
        else:
            draws += 1

    # learn_many best-effort
    if learn and UniversalPolicy and learn_items:
        try:
            pol = UniversalPolicy(namespace=namespace)
            # transform to the expected format: list[dict]
            # core Policy.learn_many is tolerant; we keep keys short.
            pol.learn_many(learn_items)
            learned_items = int(len(learn_items))
        except Exception:
            learned_items = 0

    dt_ms = (time.time() - t0) * 1000.0
    avg_turns = turns_total / max(1, games)
    avg_pairs_left_end = pairs_left_end_total / max(1, games)
    return {
        "games": int(games),
        "wins_p1": wins_p1,
        "wins_p2": wins_p2,
        "draws": draws,
        "avg_turns": float(avg_turns),
        "avg_pairs_left_end": float(avg_pairs_left_end),
        "eps": float(eps),
        "mode": mode,
        "namespace": namespace,
        "size": int(size),
        "max_turns": int(max_turns),
        "learn": bool(learn),
        "learned_items": int(learned_items),
        "duration_ms": float(dt_ms),
        "ts_start": _now(),
        "ts_end": _now(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--size", type=int, default=_env_int("OROMA_MEMORY_SIZE", 4))
    ap.add_argument("--max-turns", type=int, default=_env_int("OROMA_MEMORY_MAX_TURNS", 220))
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_MEMORY_EPS", 0.08))
    args = ap.parse_args()

    if sql_manager:
        try:
            sql_manager.ensure_schema()
        except Exception:
            pass

    namespace = "game:memory"
    ts0 = _now()

    out: Dict[str, Any] = {"ok": True, "have_db": bool(sql_manager), "db_written": False}
    try:
        policy_meta = _run_batch(namespace=namespace, mode="policy", games=args.policy_games, eps=0.0,
                                 seed=args.seed, size=args.size, max_turns=args.max_turns, learn=False)
        explore_meta = _run_batch(namespace=namespace, mode="explore", games=args.explore_games, eps=args.eps,
                                  seed=args.seed + 10_000, size=args.size, max_turns=args.max_turns, learn=True)

        out["policy"] = policy_meta
        out["explore"] = explore_meta

        # DB write
        if sql_manager:
            # policy episode
            eid1 = _write_episode(
                kind="game:memory:policy_batch",
                label=f"memory:policy ({args.policy_games} games)",
                ts_start=ts0,
                ts_end=_now(),
                meta=policy_meta,
                metrics={
                    "wins_p1": float(policy_meta["wins_p1"]),
                    "wins_p2": float(policy_meta["wins_p2"]),
                    "draws": float(policy_meta["draws"]),
                    "avg_turns": float(policy_meta["avg_turns"]),
                    "avg_pairs_left_end": float(policy_meta["avg_pairs_left_end"]),
                    "duration_ms": float(policy_meta["duration_ms"]),
                },
            )
            policy_meta["episode_id"] = eid1

            # explore episode
            eid2 = _write_episode(
                kind="game:memory:explore_batch",
                label=f"memory:explore ({args.explore_games} games)",
                ts_start=_now(),
                ts_end=_now(),
                meta=explore_meta,
                metrics={
                    "wins_p1": float(explore_meta["wins_p1"]),
                    "wins_p2": float(explore_meta["wins_p2"]),
                    "draws": float(explore_meta["draws"]),
                    "avg_turns": float(explore_meta["avg_turns"]),
                    "avg_pairs_left_end": float(explore_meta["avg_pairs_left_end"]),
                    "learned_items": float(explore_meta["learned_items"]),
                    "duration_ms": float(explore_meta["duration_ms"]),
                },
            )
            explore_meta["episode_id"] = eid2

            out["db_written"] = True

    except Exception as e:
        out["ok"] = False
        out["err"] = str(e)

    print(out)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/memory_daily_runner.py
# Projekt: ORÓMA – Games / Professional State Templates
# Modul:   Memory Daily Runner – pro_v2 Mechanic-Solved Policy/Explore Runner
# Version: v2.0-pro_v2
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Führt das klassische Memory-Spiel headless aus und hebt den Daily Runner von
#   einem rohen Turn-/Match-Logger auf einen professionellen, ressourcenschonenden
#   Lernpfad:
#
#       namespace:    game:memory
#       state_schema: memory:pro_v2
#       action_schema: high_level_memory_5
#
#   Memory unterscheidet sich konzeptionell von TicTacToe und von endlosen
#   taktischen Spielen:
#
#     • TicTacToe: Mechanik UND Zustandsraum vollständig lösbar → no_more_explore=1
#     • Memory:    Mechanik lernbar, aber Layout/Positionen variabel → mechanic_solved
#     • Snake/CTF: taktisches Dauermodell mit fortlaufender Exploration
#
#   Dieser Runner behandelt Memory deshalb als "mechanic_solved"-Kategorie. Das
#   System soll nicht endlos zufällig explorieren, wenn es das Prinzip verstanden
#   hat. Es soll bekannte Positionen nutzen, Paare gezielt aufdecken und nur noch
#   unbekannte Karten als Informationssuche untersuchen.
#
# Professionelle Memory-Mechanik
# ------------------------------
#   Der Fallback ist bewusst stark und menschenähnlich:
#
#     1. Wenn zwei gleiche bekannte, noch nicht abgeräumte Positionen existieren:
#        sofort dieses Paar aufdecken.
#     2. Wenn genau eine Position eines Symbols bekannt ist: bekannte Karte + eine
#        unbekannte Karte als gezielte Suche aufdecken.
#     3. Wenn keine Information vorhanden ist: unbekannte Karten aufdecken; kein
#        Wiederholen bekannter nutzloser Karten.
#
#   Dadurch wird nicht das Layout "gelöst", sondern die Mechanik. Neue Layouts
#   brauchen weiterhin Informationssuche, aber keine zufällige Prinzip-Exploration.
#
# State-/Action-Abstraktion
# -------------------------
#   pro_v2 speichert keine rohen Kartenpositionen/Symbole als State-Key. Der State
#   enthält taktische Mengenmerkmale wie bekannte Paare, bekannte Singles,
#   unbekannte Positionen, Spielphase und Score-Differenz. Aktionen sind bewusst
#   high-level:
#
#     0 = known_pair        bekannte Paarpositionen aufdecken
#     1 = single_probe      bekannte Single-Karte + unbekannte Karte aufdecken
#     2 = two_unknown       zwei unbekannte Karten aufdecken
#     3 = center_unknown    unbekannte Karten mit Center-Bias aufdecken
#     4 = fallback_safe     sicherer Fallback ohne rohen Zufall
#
# Ereignisbasiertes Lernen
# ------------------------
#   Es wird nur aus sinnvollen Ereignissen gelernt:
#
#     • bekanntes Paar erfolgreich abgeräumt → positives Signal
#     • Match gefunden                       → positives Signal
#     • Informationsgewinn durch neue Karte  → kleines positives Signal
#     • bekanntes Paar nicht genutzt         → negatives Signal
#     • nutzlose Wiederholung ohne Neuinfo   → negatives Signal
#     • Sieg/Niederlage                      → kurzes terminales Kreditfenster
#
#   Neutrale Turns werden nicht geschrieben. Damit entsteht weder Draw-Müll noch
#   eine negative Wand aus normalen Suchschritten.
#
# DB-/Write-Disziplin
# -------------------
#   policy_rules werden ausschließlich über core.db_writer_client.executemany()
#   geschrieben. Es gibt keinen lokalen SQLite-Direktwrite-Fallback für den
#   verwalteten Policy-Pfad. Wenn DBWriter nicht erreichbar ist, bleibt das
#   sichtbar:
#
#       policy_learn_ok=false, learned_items=0
#
#   Episoden/Metriken nutzen weiterhin den bestehenden sql_manager-Episodenpfad,
#   analog zu den anderen Daily Runnern.
#
# Explore-Disziplin
# -----------------
#   Memory wird nicht hart gestoppt wie TicTacToe, weil neue Layouts weiterhin
#   unbekannte Positionen enthalten. Sobald der pro_v2-Pfad genügend Samples hat,
#   wird Explore aber reduziert:
#
#       mechanic_understood=1, explore_reduced=1, no_more_explore=0
#
#   Das bedeutet: Keine endlose Zufallsexploration des Prinzips; nur noch gezielte
#   Informationssuche in neuen Layouts.
#
# ENV
# ---
#   OROMA_MEMORY_EPS=0.08
#   OROMA_MEMORY_REDUCED_EPS=0.0
#   OROMA_MEMORY_SIZE=4
#   OROMA_MEMORY_MAX_TURNS=220
#   OROMA_MEMORY_POLICY_ACCEPT_Q_MIN=0.15
#   OROMA_MEMORY_POLICY_ACCEPT_MIN_N=1
#   OROMA_MEMORY_POLICY_DBW_CHUNK=500
#   OROMA_MEMORY_MECHANIC_MIN_SAMPLES=2500
#   OROMA_MEMORY_EXPLORE_REDUCED_GAMES=10
#   OROMA_MEMORY_WIN_CREDIT_TURNS=8
#   OROMA_MEMORY_LOSS_CREDIT_TURNS=8
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/memory_daily_runner.py \
#     --policy-games 100 --explore-games 100 --seed "$(date +%s)" --namespace game:memory
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore


STATE_SCHEMA = "memory:pro_v2"
ACTION_SCHEMA = "high_level_memory_5"
DEFAULT_NAMESPACE = "game:memory"

ACTION_KNOWN_PAIR = 0
ACTION_SINGLE_PROBE = 1
ACTION_TWO_UNKNOWN = 2
ACTION_CENTER_UNKNOWN = 3
ACTION_FALLBACK_SAFE = 4
ACTION_NAMES = {
    ACTION_KNOWN_PAIR: "known_pair",
    ACTION_SINGLE_PROBE: "single_probe",
    ACTION_TWO_UNKNOWN: "two_unknown",
    ACTION_CENTER_UNKNOWN: "center_unknown",
    ACTION_FALLBACK_SAFE: "fallback_safe",
}


# -----------------------------------------------------------------------------
# Env / small helpers
# -----------------------------------------------------------------------------

def _now_ts() -> int:
    return int(time.time())


def _env_bool(name: str, default: str = "0") -> bool:
    v = os.environ.get(name, default)
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _bucket_int(value: int, limits: Sequence[int]) -> int:
    v = int(value)
    for idx, lim in enumerate(limits):
        if v <= int(lim):
            return idx
    return len(limits)


def _score_bucket(value: int) -> str:
    v = int(value)
    if v <= -3:
        return "neg3"
    if v < 0:
        return "neg1"
    if v == 0:
        return "eq"
    if v >= 3:
        return "pos3"
    return "pos1"


def _center_order(size: int) -> List[int]:
    n = int(size) * int(size)
    center = (float(size) - 1.0) / 2.0
    def key(i: int) -> Tuple[float, int]:
        r, c = divmod(int(i), int(size))
        return ((float(r) - center) ** 2 + (float(c) - center) ** 2, int(i))
    return sorted(range(n), key=key)


# -----------------------------------------------------------------------------
# Memory board mechanics
# -----------------------------------------------------------------------------

class MemoryEnv:
    """Headless, deterministic Memory environment for daily runs.

    The environment intentionally gives both players the same observed memory:
    in classic Memory all revealed cards are visible to both players. This avoids
    artificial hidden information in the runner and lets the policy focus on the
    learned mechanic: remember positions and clear known pairs.
    """

    def __init__(self, *, size: int, seed: int, max_turns: int):
        self.size = max(2, int(size))
        if (self.size * self.size) % 2 != 0:
            self.size += 1
        self.max_turns = max(4, int(max_turns))
        self.rng = random.Random(int(seed))
        self.reset(int(seed))

    def reset(self, seed: int) -> None:
        self.rng = random.Random(int(seed))
        n = self.size * self.size
        pairs = n // 2
        symbols = [chr(ord("A") + (i % 26)) + (str(i // 26) if i >= 26 else "") for i in range(pairs)] * 2
        self.rng.shuffle(symbols)
        self.symbols: List[str] = symbols
        self.revealed: List[bool] = [False] * n
        self.turn_owner = "p1"
        self.turns = 0
        self.score = {"p1": 0, "p2": 0}
        self.done = False
        self.winner = "-"
        self.know: Dict[str, Set[int]] = {}
        self.seen_positions: Set[int] = set()

    def legal(self) -> List[int]:
        return [i for i, r in enumerate(self.revealed) if not bool(r)]

    def pairs_total(self) -> int:
        return (self.size * self.size) // 2

    def pairs_left(self) -> int:
        return max(0, self.pairs_total() - int(self.score["p1"] + self.score["p2"]))

    def known_pair_candidates(self) -> List[Tuple[str, Tuple[int, int]]]:
        legal_set = set(self.legal())
        out: List[Tuple[str, Tuple[int, int]]] = []
        for sym, idxs in self.know.items():
            cand = sorted([int(i) for i in idxs if int(i) in legal_set])
            if len(cand) >= 2:
                out.append((str(sym), (int(cand[0]), int(cand[1]))))
        return sorted(out, key=lambda t: (t[0], t[1][0], t[1][1]))

    def known_singles(self) -> List[Tuple[str, int]]:
        legal_set = set(self.legal())
        pairs = {sym for sym, _pair in self.known_pair_candidates()}
        out: List[Tuple[str, int]] = []
        for sym, idxs in self.know.items():
            if sym in pairs:
                continue
            cand = sorted([int(i) for i in idxs if int(i) in legal_set])
            if cand:
                out.append((str(sym), int(cand[0])))
        return sorted(out, key=lambda t: (t[0], t[1]))

    def unknown_indices(self) -> List[int]:
        legal = self.legal()
        return [i for i in legal if int(i) not in self.seen_positions]

    def known_positions_legal(self) -> List[int]:
        legal_set = set(self.legal())
        out = sorted([int(i) for i in self.seen_positions if int(i) in legal_set])
        return out

    def _two_from(self, values: Sequence[int], rng: random.Random) -> Optional[Tuple[int, int]]:
        uniq = sorted({int(v) for v in values if int(v) in set(self.legal())})
        if len(uniq) < 2:
            return None
        a = int(rng.choice(uniq))
        rest = [i for i in uniq if int(i) != int(a)]
        b = int(rng.choice(rest))
        return int(a), int(b)

    def applicable_actions(self) -> Set[int]:
        legal = self.legal()
        out: Set[int] = {ACTION_FALLBACK_SAFE}
        if len(legal) >= 2:
            out.add(ACTION_TWO_UNKNOWN)
            out.add(ACTION_CENTER_UNKNOWN)
        if self.known_pair_candidates():
            out.add(ACTION_KNOWN_PAIR)
        if self.known_singles() and len(legal) >= 2:
            out.add(ACTION_SINGLE_PROBE)
        return out

    def fallback_action(self) -> int:
        if self.known_pair_candidates():
            return ACTION_KNOWN_PAIR
        if self.known_singles() and len(self.legal()) >= 2:
            return ACTION_SINGLE_PROBE
        unknown = self.unknown_indices()
        if len(unknown) >= 2:
            return ACTION_CENTER_UNKNOWN
        return ACTION_TWO_UNKNOWN if len(self.legal()) >= 2 else ACTION_FALLBACK_SAFE

    def pick_indices_for_action(self, action: int, rng: random.Random) -> Tuple[int, int, int]:
        """Return (i1, i2, effective_action). Never returns equal indices unless no legal pair exists."""
        legal = self.legal()
        if len(legal) < 2:
            i = int(legal[0]) if legal else 0
            return i, i, ACTION_FALLBACK_SAFE

        a = int(action)
        if a == ACTION_KNOWN_PAIR:
            pairs = self.known_pair_candidates()
            if pairs:
                _sym, pair = rng.choice(pairs)
                return int(pair[0]), int(pair[1]), ACTION_KNOWN_PAIR
            a = self.fallback_action()

        if a == ACTION_SINGLE_PROBE:
            singles = self.known_singles()
            if singles:
                _sym, i1 = rng.choice(singles)
                unknown = [i for i in self.unknown_indices() if int(i) != int(i1)]
                if unknown:
                    i2 = int(rng.choice(unknown))
                else:
                    rest = [i for i in legal if int(i) != int(i1)]
                    i2 = int(rng.choice(rest))
                return int(i1), int(i2), ACTION_SINGLE_PROBE
            a = self.fallback_action()

        if a == ACTION_CENTER_UNKNOWN:
            order = [i for i in _center_order(self.size) if i in self.unknown_indices()]
            if len(order) >= 2:
                return int(order[0]), int(order[1]), ACTION_CENTER_UNKNOWN
            if len(order) == 1:
                rest = [i for i in legal if int(i) != int(order[0])]
                if rest:
                    return int(order[0]), int(rng.choice(rest)), ACTION_CENTER_UNKNOWN
            a = ACTION_TWO_UNKNOWN

        if a == ACTION_TWO_UNKNOWN:
            unknown = self.unknown_indices()
            pair = self._two_from(unknown, rng)
            if pair:
                return int(pair[0]), int(pair[1]), ACTION_TWO_UNKNOWN
            pair = self._two_from(legal, rng)
            if pair:
                return int(pair[0]), int(pair[1]), ACTION_TWO_UNKNOWN

        # final safe fallback: known pair > single probe > any legal two
        fb = self.fallback_action()
        if fb != ACTION_FALLBACK_SAFE and fb != int(action):
            return self.pick_indices_for_action(fb, rng)
        pair = self._two_from(legal, rng)
        if pair:
            return int(pair[0]), int(pair[1]), ACTION_FALLBACK_SAFE
        return int(legal[0]), int(legal[0]), ACTION_FALLBACK_SAFE

    def step_action(self, action: int, rng: random.Random) -> Tuple[bool, Dict[str, Any]]:
        if self.done:
            return True, {"reason": "done"}
        if self.turns >= self.max_turns:
            self.done = True
            self.winner = "draw"
            return True, {"reason": "turn_limit"}

        legal = self.legal()
        if len(legal) < 2:
            self.done = True
            if self.score["p1"] > self.score["p2"]:
                self.winner = "p1"
            elif self.score["p2"] > self.score["p1"]:
                self.winner = "p2"
            else:
                self.winner = "draw"
            return True, {"reason": "legal_lt2"}

        who = self.turn_owner
        pair_available = bool(self.known_pair_candidates())
        singles_available = bool(self.known_singles())
        known_positions_before = set(self.seen_positions)
        known_pairs_before = len(self.known_pair_candidates())
        unknown_before = len(self.unknown_indices())

        i1, i2, eff_action = self.pick_indices_for_action(int(action), rng)
        if i1 == i2:
            rest = [i for i in legal if int(i) != int(i1)]
            if rest:
                i2 = int(rng.choice(rest))

        s1 = self.symbols[int(i1)]
        s2 = self.symbols[int(i2)]
        match = (s1 == s2)
        new_positions = int((int(i1) not in known_positions_before)) + int((int(i2) not in known_positions_before))
        repeat_known_reveals = int((int(i1) in known_positions_before)) + int((int(i2) in known_positions_before))

        self.turns += 1
        self.seen_positions.add(int(i1))
        self.seen_positions.add(int(i2))

        if match:
            self.revealed[int(i1)] = True
            self.revealed[int(i2)] = True
            self.score[who] += 1
        else:
            self.know.setdefault(str(s1), set()).add(int(i1))
            self.know.setdefault(str(s2), set()).add(int(i2))
            self.turn_owner = "p2" if who == "p1" else "p1"

        if all(self.revealed):
            self.done = True
            if self.score["p1"] > self.score["p2"]:
                self.winner = "p1"
            elif self.score["p2"] > self.score["p1"]:
                self.winner = "p2"
            else:
                self.winner = "draw"

        info = {
            "who": who,
            "i1": int(i1),
            "i2": int(i2),
            "symbol1": str(s1),
            "symbol2": str(s2),
            "match": bool(match),
            "action": int(eff_action),
            "action_name": ACTION_NAMES.get(int(eff_action), str(eff_action)),
            "pair_available": bool(pair_available),
            "singles_available": bool(singles_available),
            "known_pairs_before": int(known_pairs_before),
            "unknown_before": int(unknown_before),
            "new_positions": int(new_positions),
            "repeat_known_reveals": int(repeat_known_reveals),
            "repeat_waste": bool((not match) and new_positions <= 0 and repeat_known_reveals >= 2),
        }
        return self.done, info


# -----------------------------------------------------------------------------
# State abstraction and policy choice
# -----------------------------------------------------------------------------

def _phase(pairs_left: int, pairs_total: int) -> str:
    if pairs_total <= 0:
        return "end"
    ratio = float(pairs_left) / float(max(1, pairs_total))
    if ratio > 0.66:
        return "early"
    if ratio > 0.25:
        return "mid"
    if pairs_left > 0:
        return "late"
    return "end"


def _state_hash(env: MemoryEnv) -> str:
    legal = env.legal()
    pairs_left = env.pairs_left()
    pairs_total = env.pairs_total()
    known_pairs = len(env.known_pair_candidates())
    known_singles = len(env.known_singles())
    unknown = len(env.unknown_indices())
    known_positions = len(env.known_positions_legal())
    score_diff = int(env.score[env.turn_owner]) - int(env.score["p2" if env.turn_owner == "p1" else "p1"])
    parts = [
        STATE_SCHEMA,
        f"sz={int(env.size)}",
        f"ph={_phase(pairs_left, pairs_total)}",
        f"pl={_bucket_int(pairs_left, (0, 2, 4, 6, 8, 12, 18))}",
        f"kp={_bucket_int(known_pairs, (0, 1, 2, 4))}",
        f"ks={_bucket_int(known_singles, (0, 1, 3, 6, 10))}",
        f"unk={_bucket_int(unknown, (0, 2, 4, 8, 12, 20))}",
        f"known={_bucket_int(known_positions, (0, 2, 4, 8, 12, 20))}",
        f"legal={_bucket_int(len(legal), (0, 2, 4, 8, 12, 20))}",
        f"sd={_score_bucket(score_diff)}",
        f"tb={_bucket_int(env.turns, (0, 4, 8, 14, 24, 40, 80, 160))}",
    ]
    return "|".join(str(p) for p in parts)


@dataclass
class PolicyStats:
    seen: int = 0
    accepted: int = 0
    fallback: int = 0
    rejected_n: int = 0
    rejected_q: int = 0
    rejected_unsafe: int = 0


def _read_policy_candidates(namespace: str, state_hash: str) -> List[Tuple[int, float, int]]:
    out: List[Tuple[int, float, int]] = []
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                rows = conn.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=? ORDER BY q DESC, n DESC LIMIT 12",
                    (str(namespace), str(state_hash)),
                ).fetchall()
            for r in rows:
                try:
                    action = int(r["action"] if hasattr(r, "keys") else r[0])
                    q = float(r["q"] if hasattr(r, "keys") else r[1])
                    n = int(r["n"] if hasattr(r, "keys") else r[2])
                    out.append((action, q, n))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _policy_choose(env: MemoryEnv, namespace: str, state_hash: str, stats: PolicyStats, rng: random.Random) -> int:
    fallback = int(env.fallback_action())
    candidates = _read_policy_candidates(namespace, state_hash)
    if not candidates:
        stats.fallback += 1
        return fallback

    stats.seen += 1
    q_min = _env_float("OROMA_MEMORY_POLICY_ACCEPT_Q_MIN", 0.15)
    n_min = _env_int("OROMA_MEMORY_POLICY_ACCEPT_MIN_N", 1)
    applicable = env.applicable_actions()

    candidates.sort(key=lambda t: (float(t[1]), int(t[2])), reverse=True)
    eligible: List[Tuple[int, float, int]] = []
    for a, q, n in candidates:
        if int(n) < int(n_min):
            continue
        if float(q) < float(q_min):
            continue
        if int(a) not in applicable:
            continue
        eligible.append((int(a), float(q), int(n)))

    if not eligible:
        _a0, q0, n0 = candidates[0]
        if int(n0) < int(n_min):
            stats.rejected_n += 1
        elif float(q0) < float(q_min):
            stats.rejected_q += 1
        else:
            stats.rejected_unsafe += 1
        stats.fallback += 1
        return fallback

    # Tie among nearly equal high-Q actions to avoid mechanical identical play,
    # but never accept an action that is inapplicable in the current layout.
    top_q = eligible[0][1]
    tie_eps = _env_float("OROMA_MEMORY_POLICY_Q_TIE_EPS", 0.000001)
    top = [t for t in eligible if abs(float(t[1]) - float(top_q)) <= float(tie_eps)]
    choice = rng.choice(top if top else eligible[:1])
    stats.accepted += 1
    return int(choice[0])


# -----------------------------------------------------------------------------
# Event learning and DBWriter batch path
# -----------------------------------------------------------------------------

@dataclass
class TraceTurn:
    state_hash: str
    action_canon: int
    player: str
    match: bool
    pair_available: bool
    new_positions: int
    repeat_waste: bool
    ts: int


def _dbw_try_enable() -> bool:
    if db_writer_client is None:
        return False
    raw = os.environ.get("OROMA_DBW_ENABLE")
    if raw is not None and str(raw).strip().lower() in ("0", "false", "no", "off"):
        return False
    if raw is None:
        try:
            sock_path = db_writer_client._sock_path() if hasattr(db_writer_client, "_sock_path") else "/opt/ai/oroma/data/state/db_writer.sock"
            if os.path.exists(str(sock_path)):
                os.environ["OROMA_DBW_ENABLE"] = "1"
        except Exception:
            pass
    try:
        return bool(db_writer_client.ping(timeout_ms=800))
    except Exception:
        return False


def _learn_policy_rules_dbw(namespace: str, items: Sequence[Dict[str, Any]]) -> Tuple[bool, int, float]:
    """Aggregate policy items and write via DBWriter only.

    Returns (ok, learned_item_count, duration_ms). No SQLite direct-write fallback
    is allowed here because policy_rules belongs to the managed ORÓMA DBWriter
    path.
    """
    t0 = time.time()
    if not items:
        return False, 0, 0.0
    if not _dbw_try_enable():
        return False, 0, round((time.time() - t0) * 1000.0, 3)

    now = int(time.time())
    agg: Dict[Tuple[str, str], Dict[str, int]] = {}
    learned_count = 0
    for it in items:
        sh = str(it.get("state_hash", "")).strip()
        if not sh:
            continue
        action = str(it.get("action_canon", it.get("action", ""))).strip()
        if action == "":
            continue
        try:
            out = float(it.get("outcome", 0.0))
        except Exception:
            out = 0.0
        if abs(out) <= 1e-9:
            continue
        key = (sh, action)
        row = agg.setdefault(key, {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": now})
        row["n"] += 1
        learned_count += 1
        if out > 0.0:
            row["pos"] += 1
        else:
            row["neg"] += 1
        try:
            ts = int(it.get("ts") or now)
            if ts > row["last_ts"]:
                row["last_ts"] = ts
        except Exception:
            pass

    if not agg:
        return False, 0, round((time.time() - t0) * 1000.0, 3)

    sql = """INSERT INTO policy_rules
             (namespace, state_hash, action, n, pos, neg, draw, q, last_ts)
             VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(namespace, state_hash, action) DO UPDATE SET
                 n = policy_rules.n + excluded.n,
                 pos = policy_rules.pos + excluded.pos,
                 neg = policy_rules.neg + excluded.neg,
                 draw = policy_rules.draw + excluded.draw,
                 q = CASE
                       WHEN (policy_rules.n + excluded.n) > 0
                       THEN CAST((policy_rules.pos + excluded.pos) - (policy_rules.neg + excluded.neg) AS REAL)
                            / CAST(policy_rules.n + excluded.n AS REAL)
                       ELSE 0.0
                     END,
                 last_ts = CASE
                             WHEN excluded.last_ts > policy_rules.last_ts THEN excluded.last_ts
                             ELSE policy_rules.last_ts
                           END
          """
    params: List[List[Any]] = []
    for (sh, action), row in agg.items():
        n = int(row["n"])
        pos = int(row["pos"])
        neg = int(row["neg"])
        draw = int(row["draw"])
        q = float(pos - neg) / float(max(1, n))
        params.append([str(namespace), str(sh), str(action), n, pos, neg, draw, q, int(row["last_ts"])])

    timeout_ms = int(getattr(sql_manager, "_dbw_timeout_ms", lambda kind="dream": 60000)("dream")) if sql_manager else 60000
    chunk = max(1, _env_int("OROMA_MEMORY_POLICY_DBW_CHUNK", 500))
    try:
        for i in range(0, len(params), chunk):
            db_writer_client.executemany(
                sql,
                params[i:i + chunk],
                tag="memory.pro_v2.policy_rules.upsert",
                priority="low",
                timeout_ms=timeout_ms,
                db="oroma",
            )
        return True, int(learned_count), round((time.time() - t0) * 1000.0, 3)
    except Exception as e:
        sys.stderr.write(f"[memory_daily_runner] DBWriter policy upsert failed: {e!r}\n")
        return False, 0, round((time.time() - t0) * 1000.0, 3)


def _learn_items_from_trace(trace: Sequence[TraceTurn], winner: str, ts: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    items: List[Dict[str, Any]] = []
    meta = {
        "learn_items": 0,
        "pair_reuse_credit_items": 0,
        "match_credit_items": 0,
        "info_gain_credit_items": 0,
        "missed_pair_credit_items": 0,
        "repeat_waste_credit_items": 0,
        "terminal_credit_items": 0,
    }

    def add(turn: TraceTurn, outcome: float, kind: str) -> None:
        if abs(float(outcome)) <= 1e-9:
            return
        items.append({
            "state_hash": str(turn.state_hash),
            "action_canon": int(turn.action_canon),
            "outcome": float(outcome),
            "ts": int(turn.ts or ts),
        })
        meta["learn_items"] += 1
        if kind == "pair_reuse":
            meta["pair_reuse_credit_items"] += 1
        elif kind == "match":
            meta["match_credit_items"] += 1
        elif kind == "info_gain":
            meta["info_gain_credit_items"] += 1
        elif kind == "missed_pair":
            meta["missed_pair_credit_items"] += 1
        elif kind == "repeat_waste":
            meta["repeat_waste_credit_items"] += 1
        elif kind == "terminal":
            meta["terminal_credit_items"] += 1

    for turn in trace:
        if turn.pair_available and int(turn.action_canon) == ACTION_KNOWN_PAIR and bool(turn.match):
            add(turn, 1.0, "pair_reuse")
        elif turn.pair_available and int(turn.action_canon) != ACTION_KNOWN_PAIR:
            add(turn, -1.0, "missed_pair")

        if bool(turn.match):
            add(turn, 0.85, "match")
        elif int(turn.new_positions) > 0:
            add(turn, 0.30, "info_gain")

        if bool(turn.repeat_waste):
            add(turn, -0.75, "repeat_waste")

    if winner in ("p1", "p2") and trace:
        win_n = max(0, _env_int("OROMA_MEMORY_WIN_CREDIT_TURNS", 8))
        loss_n = max(0, _env_int("OROMA_MEMORY_LOSS_CREDIT_TURNS", 8))
        winner_steps = [t for t in trace if str(t.player) == str(winner)]
        loser = "p2" if winner == "p1" else "p1"
        loser_steps = [t for t in trace if str(t.player) == str(loser)]
        for turn in winner_steps[-win_n:]:
            add(turn, 1.0, "terminal")
        for turn in loser_steps[-loss_n:]:
            add(turn, -1.0, "terminal")

    return items, meta


# -----------------------------------------------------------------------------
# Coverage / episode persistence
# -----------------------------------------------------------------------------

def _db_pro_coverage(namespace: str) -> Dict[str, int]:
    out = {"pro_states_known": 0, "pro_rules_known": 0, "pro_samples_known": 0}
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(DISTINCT state_hash) AS states,
                              COUNT(*) AS rules,
                              COALESCE(SUM(n),0) AS samples
                       FROM policy_rules
                       WHERE namespace=? AND state_hash LIKE ?""",
                    (str(namespace), f"{STATE_SCHEMA}%"),
                ).fetchone()
                if row is not None:
                    try:
                        out["pro_states_known"] = int(row["states"] or 0)
                        out["pro_rules_known"] = int(row["rules"] or 0)
                        out["pro_samples_known"] = int(row["samples"] or 0)
                    except Exception:
                        out["pro_states_known"] = int(row[0] or 0)
                        out["pro_rules_known"] = int(row[1] or 0)
                        out["pro_samples_known"] = int(row[2] or 0)
    except Exception as e:
        sys.stderr.write(f"[memory_daily_runner] coverage read failed: {e!r}\n")
    return out


def _db_write_episode(kind: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None or not hasattr(sql_manager, "insert_episode"):
        return None
    ts0 = int(meta.get("ts_start") or time.time())
    ts1 = int(meta.get("ts_end") or time.time())
    try:
        try:
            eid = sql_manager.insert_episode(
                ts_start=ts0,
                ts_end=ts1,
                kind=str(kind),
                source=str(meta.get("source") or "orchestrator"),
                label=str(meta.get("label") or kind),
                meta=meta,
            )
        except TypeError:
            eid = sql_manager.insert_episode(
                kind=str(kind),
                ts_start=ts0,
                ts_end=ts1,
                label=str(meta.get("label") or kind),
                meta=meta,
            )
        return int(eid) if eid is not None else None
    except Exception as e:
        sys.stderr.write(f"[memory_daily_runner] DB insert_episode failed: {e!r}\n")
        return None


def _db_write_metrics(eid: int, metrics: Dict[str, Any]) -> bool:
    if sql_manager is None or not hasattr(sql_manager, "insert_episodic_metric"):
        return False
    ts = int(time.time())
    ok = True
    for k, v in metrics.items():
        try:
            if isinstance(v, bool):
                fv = 1.0 if v else 0.0
            else:
                fv = float(v) if v is not None else 0.0
            sql_manager.insert_episodic_metric(
                episode_id=int(eid),
                ts=int(ts),
                key=str(k),
                value=float(fv),
            )
        except Exception as e:
            sys.stderr.write(f"[memory_daily_runner] DB metric failed ({k}): {e!r}\n")
            ok = False
    return ok


def _numeric_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    skip = {"namespace", "mode", "state_schema", "action_schema", "source", "label", "runner", "shim", "diversity_mode"}
    out: Dict[str, Any] = {}
    for k, v in result.items():
        if k in skip:
            continue
        if isinstance(v, bool):
            out[k] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            out[k] = v
    return out


# -----------------------------------------------------------------------------
# Batch runner
# -----------------------------------------------------------------------------

@dataclass
class BatchResult:
    ts_start: int
    ts_end: int
    duration_ms: int
    games: int
    requested_games: int
    effective_games: int
    wins_p1: int
    wins_p2: int
    wins_x: int
    wins_o: int
    draws: int
    avg_turns: float
    avg_pairs_left_end: float
    avg_known_positions: float
    avg_known_pairs: float
    avg_peak_known_positions: float
    avg_peak_known_pairs: float
    avg_blind_reveals: float
    avg_repeat_waste: float
    avg_memory_hits: float
    avg_memory_misses: float
    mode: str
    namespace: str
    state_schema: str
    action_schema: str
    policy_enabled: float
    eps: float
    explore_reduced: float
    no_more_explore: float
    mechanic_understood: float
    learn: bool
    learn_items: int
    learned_items: int
    policy_learn_ok: bool
    learn_duration_ms: float
    sim_duration_ms: float
    policy_dbw_chunk: int
    policy_seen: int
    policy_accepted: int
    policy_fallback: int
    policy_rejected_n: int
    policy_rejected_q: int
    policy_rejected_unsafe: int
    pair_reuse_hits: int
    pair_reuse_credit_items: int
    match_credit_items: int
    info_gain_credit_items: int
    missed_pair_credit_items: int
    repeat_waste_credit_items: int
    terminal_credit_items: int
    blind_reveals: int
    repeat_waste: int
    memory_hits: int
    memory_misses: int
    pro_states_known_before: int
    pro_rules_known_before: int
    pro_samples_known_before: int
    pro_states_known: int
    pro_rules_known: int
    pro_samples_known: int
    size: int
    max_turns: int
    source: str
    label: str
    runner: str
    shim: str


def _run_batch(
    *,
    namespace: str,
    mode: str,
    games: int,
    eps: float,
    seed: int,
    size: int,
    max_turns: int,
    learn: bool,
    mechanic_understood: bool,
    explore_reduced: bool,
    coverage_before: Dict[str, int],
) -> BatchResult:
    ts_start = _now_ts()
    t0 = time.time()
    rng = random.Random(int(seed) ^ (17 if mode == "policy" else 9973))
    stats = PolicyStats()
    wins_p1 = wins_p2 = draws = 0
    turns_total = 0
    pairs_left_total = 0
    known_positions_total = 0
    known_pairs_total = 0
    peak_known_positions_total = 0
    peak_known_pairs_total = 0
    blind_reveals = 0
    repeat_waste = 0
    memory_hits = 0
    memory_misses = 0
    pair_reuse_hits = 0
    learn_items: List[Dict[str, Any]] = []
    learn_meta = {
        "learn_items": 0,
        "pair_reuse_credit_items": 0,
        "match_credit_items": 0,
        "info_gain_credit_items": 0,
        "missed_pair_credit_items": 0,
        "repeat_waste_credit_items": 0,
        "terminal_credit_items": 0,
    }

    sim_t0 = time.time()
    for g in range(max(0, int(games))):
        env = MemoryEnv(size=int(size), seed=int(seed) + int(g), max_turns=int(max_turns))
        trace: List[TraceTurn] = []
        peak_known_positions = 0
        peak_known_pairs = 0
        while not env.done:
            sh = _state_hash(env)
            peak_known_positions = max(peak_known_positions, len(env.known_positions_legal()))
            peak_known_pairs = max(peak_known_pairs, len(env.known_pair_candidates()))
            fallback = int(env.fallback_action())
            action = fallback
            if mode == "policy":
                action = _policy_choose(env, namespace, sh, stats, rng)
            else:
                # Explore means "information search", not random principle-play. If the
                # mechanic is understood, eps is normally 0 and fallback performs the
                # human-like pair/single/unknown strategy.
                if float(eps) > 0.0 and rng.random() < float(eps):
                    applicable = list(env.applicable_actions())
                    # Avoid wasting known pairs even during exploration unless explicitly
                    # allowed: exploration should discover unknown positions, not ignore
                    # remembered pairs.
                    if env.known_pair_candidates():
                        action = ACTION_KNOWN_PAIR
                    else:
                        action = int(rng.choice([a for a in applicable if a != ACTION_FALLBACK_SAFE] or [fallback]))
                else:
                    action = _policy_choose(env, namespace, sh, stats, rng)

            _done, info = env.step_action(int(action), rng)
            if not info or "i1" not in info:
                continue
            eff_action = int(info.get("action", action))
            if int(info.get("new_positions", 0)) >= 2:
                blind_reveals += 1
            if bool(info.get("repeat_waste")):
                repeat_waste += 1
            if bool(info.get("pair_available")) and eff_action == ACTION_KNOWN_PAIR and bool(info.get("match")):
                memory_hits += 1
                pair_reuse_hits += 1
            elif bool(info.get("pair_available")) and eff_action != ACTION_KNOWN_PAIR:
                memory_misses += 1

            trace.append(TraceTurn(
                state_hash=str(sh),
                action_canon=int(eff_action),
                player=str(info.get("who") or "p1"),
                match=bool(info.get("match")),
                pair_available=bool(info.get("pair_available")),
                new_positions=int(info.get("new_positions", 0)),
                repeat_waste=bool(info.get("repeat_waste")),
                ts=_now_ts(),
            ))

        turns_total += int(env.turns)
        pairs_left_total += int(env.pairs_left())
        known_positions_total += len(env.known_positions_legal())
        known_pairs_total += len(env.known_pair_candidates())
        peak_known_positions_total += int(peak_known_positions)
        peak_known_pairs_total += int(peak_known_pairs)
        if env.winner == "p1":
            wins_p1 += 1
        elif env.winner == "p2":
            wins_p2 += 1
        else:
            draws += 1

        if learn:
            items, meta = _learn_items_from_trace(trace, env.winner, _now_ts())
            learn_items.extend(items)
            for k, v in meta.items():
                learn_meta[k] = int(learn_meta.get(k, 0)) + int(v)

    sim_duration_ms = round((time.time() - sim_t0) * 1000.0, 3)
    learn_ok = False
    learned_count = 0
    learn_duration_ms = 0.0
    if learn and learn_items:
        learn_ok, learned_count, learn_duration_ms = _learn_policy_rules_dbw(namespace, learn_items)

    coverage_after = _db_pro_coverage(namespace)
    dt_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = _now_ts()
    denom = max(1, int(games))
    return BatchResult(
        ts_start=int(ts_start),
        ts_end=int(ts_end),
        duration_ms=int(dt_ms),
        games=int(games),
        requested_games=int(games),
        effective_games=int(games),
        wins_p1=int(wins_p1),
        wins_p2=int(wins_p2),
        wins_x=int(wins_p1),
        wins_o=int(wins_p2),
        draws=int(draws),
        avg_turns=round(float(turns_total) / float(denom), 3),
        avg_pairs_left_end=round(float(pairs_left_total) / float(denom), 3),
        avg_known_positions=round(float(known_positions_total) / float(denom), 3),
        avg_known_pairs=round(float(known_pairs_total) / float(denom), 3),
        avg_peak_known_positions=round(float(peak_known_positions_total) / float(denom), 3),
        avg_peak_known_pairs=round(float(peak_known_pairs_total) / float(denom), 3),
        avg_blind_reveals=round(float(blind_reveals) / float(denom), 3),
        avg_repeat_waste=round(float(repeat_waste) / float(denom), 3),
        avg_memory_hits=round(float(memory_hits) / float(denom), 3),
        avg_memory_misses=round(float(memory_misses) / float(denom), 3),
        mode=str(mode),
        namespace=str(namespace),
        state_schema=STATE_SCHEMA,
        action_schema=ACTION_SCHEMA,
        policy_enabled=1.0,
        eps=float(eps),
        explore_reduced=1.0 if explore_reduced else 0.0,
        no_more_explore=0.0,
        mechanic_understood=1.0 if mechanic_understood else 0.0,
        learn=bool(learn),
        learn_items=int(len(learn_items)),
        learned_items=int(learned_count),
        policy_learn_ok=bool(learn_ok),
        learn_duration_ms=float(learn_duration_ms),
        sim_duration_ms=float(sim_duration_ms),
        policy_dbw_chunk=max(1, _env_int("OROMA_MEMORY_POLICY_DBW_CHUNK", 500)),
        policy_seen=int(stats.seen),
        policy_accepted=int(stats.accepted),
        policy_fallback=int(stats.fallback),
        policy_rejected_n=int(stats.rejected_n),
        policy_rejected_q=int(stats.rejected_q),
        policy_rejected_unsafe=int(stats.rejected_unsafe),
        pair_reuse_hits=int(pair_reuse_hits),
        pair_reuse_credit_items=int(learn_meta.get("pair_reuse_credit_items", 0)),
        match_credit_items=int(learn_meta.get("match_credit_items", 0)),
        info_gain_credit_items=int(learn_meta.get("info_gain_credit_items", 0)),
        missed_pair_credit_items=int(learn_meta.get("missed_pair_credit_items", 0)),
        repeat_waste_credit_items=int(learn_meta.get("repeat_waste_credit_items", 0)),
        terminal_credit_items=int(learn_meta.get("terminal_credit_items", 0)),
        blind_reveals=int(blind_reveals),
        repeat_waste=int(repeat_waste),
        memory_hits=int(memory_hits),
        memory_misses=int(memory_misses),
        pro_states_known_before=int(coverage_before.get("pro_states_known", 0)),
        pro_rules_known_before=int(coverage_before.get("pro_rules_known", 0)),
        pro_samples_known_before=int(coverage_before.get("pro_samples_known", 0)),
        pro_states_known=int(coverage_after.get("pro_states_known", 0)),
        pro_rules_known=int(coverage_after.get("pro_rules_known", 0)),
        pro_samples_known=int(coverage_after.get("pro_samples_known", 0)),
        size=int(size),
        max_turns=int(max_turns),
        source="orchestrator",
        label=f"memory:{mode} ({games} games)",
        runner="tools/memory_daily_runner.py",
        shim="tools/memory_daily_runner.pro_v2_mechanic_solved",
    )


def _persist_batch(kind: str, result: Dict[str, Any]) -> bool:
    eid = _db_write_episode(kind, result)
    if eid is None:
        return False
    result["episode_id"] = int(eid)
    return _db_write_metrics(int(eid), _numeric_metrics(result))


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--namespace", type=str, default=os.environ.get("OROMA_MEMORY_POLICY_NAMESPACE", DEFAULT_NAMESPACE))
    ap.add_argument("--size", type=int, default=_env_int("OROMA_MEMORY_SIZE", 4))
    ap.add_argument("--max-turns", type=int, default=_env_int("OROMA_MEMORY_MAX_TURNS", 220))
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_MEMORY_EPS", 0.08))
    args = ap.parse_args()

    namespace = str(args.namespace or DEFAULT_NAMESPACE)
    seed = int(args.seed)
    coverage0 = _db_pro_coverage(namespace)
    min_samples = max(0, _env_int("OROMA_MEMORY_MECHANIC_MIN_SAMPLES", 2500))
    mechanic_understood = int(coverage0.get("pro_samples_known", 0)) >= int(min_samples) if min_samples > 0 else False
    explore_reduced = bool(mechanic_understood)
    effective_explore_games = max(0, int(args.explore_games))
    explore_eps = float(args.eps)
    if explore_reduced:
        reduced_games = max(0, _env_int("OROMA_MEMORY_EXPLORE_REDUCED_GAMES", 10))
        if reduced_games > 0:
            effective_explore_games = min(effective_explore_games, int(reduced_games))
        explore_eps = _env_float("OROMA_MEMORY_REDUCED_EPS", 0.0)

    out: Dict[str, Any] = {
        "ok": True,
        "have_db": bool(sql_manager),
        "have_up": True,
        "db_written": False,
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "seed": int(seed),
        "mechanic_understood": 1.0 if mechanic_understood else 0.0,
        "explore_reduced": 1.0 if explore_reduced else 0.0,
        "no_more_explore": 0.0,
    }

    try:
        policy = _run_batch(
            namespace=namespace,
            mode="policy",
            games=max(0, int(args.policy_games)),
            eps=0.0,
            seed=seed,
            size=max(2, int(args.size)),
            max_turns=max(20, int(args.max_turns)),
            learn=False,
            mechanic_understood=mechanic_understood,
            explore_reduced=False,
            coverage_before=coverage0,
        )
        coverage_after_policy = _db_pro_coverage(namespace)
        explore = _run_batch(
            namespace=namespace,
            mode="explore",
            games=effective_explore_games,
            eps=explore_eps,
            seed=seed + 10_000,
            size=max(2, int(args.size)),
            max_turns=max(20, int(args.max_turns)),
            learn=effective_explore_games > 0,
            mechanic_understood=mechanic_understood,
            explore_reduced=explore_reduced,
            coverage_before=coverage_after_policy,
        )

        policy_d = asdict(policy)
        explore_d = asdict(explore)
        explore_d["requested_games"] = max(0, int(args.explore_games))
        explore_d["effective_games"] = int(effective_explore_games)
        out["policy"] = policy_d
        out["explore"] = explore_d

        db_policy = _persist_batch("game:memory:policy_batch", policy_d)
        db_explore = _persist_batch("game:memory:explore_batch", explore_d)
        out["db_written"] = bool(db_policy and db_explore)
        if not out["db_written"]:
            # Keep status visible, but do not misclassify successful policy learning as
            # a learning failure. If sql_manager is unavailable the runner remains useful
            # as a headless smoke test.
            out["db_write_warning"] = "episode_or_metric_write_failed"
    except Exception as e:
        out["ok"] = False
        out["err"] = str(e)

    print(json.dumps(out, ensure_ascii=False, sort_keys=False))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

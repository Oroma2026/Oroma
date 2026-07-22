#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/memorymaze_hybrid_daily_runner.py
# Projekt: ORÓMA – Games / Episodic Telemetry / UniversalPolicy
# Modul:   MemoryMaze Hybrid Daily Runner – Spielziel + Lernloop
# Version: v2.1
# Stand:   2026-06-27
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Produktiver Headless-Daily-Runner für "MemoryMaze Hybrid"
#   (mini_programs/memorymaze_hybrid.py). Das Spiel kombiniert PacMan-Maze,
#   Memory-Blocker, Items, Fallgruben und optionalen Hard-P3-Jäger.
#
#   Version v2.0 hebt den Runner vom reinen Telemetriepfad auf einen echten
#   ORÓMA-Lernloop:
#     1) Das Memory-Ziel wird messbar: Reveals, Claims, Matches, Mismatches,
#        gelöste Paare, Pit-Hits und P3-Kontakte werden als Metriken geschrieben.
#     2) Policy-Games nutzen vorhandene policy_rules, fallen bei unbekannten
#        Zuständen aber bewusst auf die memory-fähige Heuristik zurück. Dadurch
#        bleibt das Spiel produktiv und hängt nicht an leeren State-Hashes.
#     3) Explore-Games erzeugen Lernitems mit state_hash/action/outcome und
#        schreiben UniversalPolicy-kompatibel in policy_rules unter
#        namespace="game:memorymaze_hybrid".
#
# Warum kein DB-Schema-Change?
# ----------------------------
#   ORÓMA nutzt bereits episodes, episodic_metrics und policy_rules. Dieser
#   Runner schreibt ausschließlich in diese bestehenden Pfade über sql_manager
#   und – falls aktiv – DBWriter-kompatible executemany-Aufrufe. Es gibt keine
#   neue Tabelle und keine Schemaänderung.
#
#   Version v2.1 ergänzt Policy-Reuse: Der state_hash ist jetzt taktisch
#   abstrahiert (Claim-Phase, Zielrichtung, Distanz- und Strike-Buckets)
#   statt positionsspezifisch. Dadurch können gelernte MemoryMaze-Regeln
#   im nächsten Policy-Lauf wiedergefunden werden, ohne Draw-Wände zu bauen.
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/memorymaze_hybrid_daily_runner.py \
#       --mode normal --policy-games 20 --explore-games 20 --seed "$(date +%s)"
#
# ENV (optional)
# --------------
#   OROMA_MMZ_EPS=0.10
#   OROMA_MMZ_MAP=sym|asym
#   OROMA_MMZ_MAX_STEPS=900
#   OROMA_MMZ_POLICY_NAMESPACE=game:memorymaze_hybrid
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

try:
    # Direktimport der Engine-Datei vermeidet Nebenwirkungen der mini_programs
    # Auto-Discovery (einige Mini-Programme initialisieren optionale Hardware-
    # oder UI-Abhängigkeiten). Der Runner braucht ausschließlich HybridGame.
    import importlib.util
    from pathlib import Path

    _ENGINE_PATH = Path(__file__).resolve().parents[1] / "mini_programs" / "memorymaze_hybrid.py"
    _ENGINE_SPEC = importlib.util.spec_from_file_location("oroma_memorymaze_hybrid_engine", str(_ENGINE_PATH))
    if _ENGINE_SPEC is None or _ENGINE_SPEC.loader is None:
        raise RuntimeError(f"Engine-Spec konnte nicht erzeugt werden: {_ENGINE_PATH}")
    _ENGINE_MOD = importlib.util.module_from_spec(_ENGINE_SPEC)
    sys.modules["oroma_memorymaze_hybrid_engine"] = _ENGINE_MOD
    _ENGINE_SPEC.loader.exec_module(_ENGINE_MOD)
    HybridGame = _ENGINE_MOD.HybridGame
except Exception as e:
    raise RuntimeError(f"memorymaze_hybrid import fehlgeschlagen: {e}")


ACTIONS: Tuple[str, ...] = ("U", "D", "L", "R", "REVEAL")


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


def _now_s() -> int:
    return int(time.time())


def _stable_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:24]


def _bin_delta(v: int) -> int:
    """Small positional binning keeps the state space reusable but meaningful."""
    if v < -12:
        return -4
    if v < -6:
        return -3
    if v < -2:
        return -2
    if v < 0:
        return -1
    if v == 0:
        return 0
    if v <= 2:
        return 1
    if v <= 6:
        return 2
    if v <= 12:
        return 3
    return 4


def _first_claim_symbol(g: HybridGame, who: str) -> str:
    try:
        idx = g._claim_first.get(who)  # type: ignore[attr-defined]
        if idx is None:
            return ""
        obj = g.objects[int(idx)]
        if obj.active:
            return str(obj.sym)
    except Exception:
        return ""
    return ""


def _target_info(g: HybridGame, who: str) -> Tuple[str, int, int]:
    try:
        idx = g._choose_memory_target_index(who)  # type: ignore[attr-defined]
        if idx is None:
            return "", 0, 0
        obj = g.objects[int(idx)]
        me = g.p1 if who == "p1" else g.p2
        return str(obj.sym), _bin_delta(int(obj.pos[0] - me[0])), _bin_delta(int(obj.pos[1] - me[1]))
    except Exception:
        return "", 0, 0


def _ahead_symbol(g: HybridGame, who: str) -> str:
    try:
        idx = g._object_ahead_index(who)  # type: ignore[attr-defined]
        if idx is None:
            return ""
        return str(g.objects[int(idx)].sym)
    except Exception:
        return ""


def _pairs_bucket(v: int) -> str:
    """Bucket open-pair counts for reusable MemoryMaze policy states."""
    try:
        x = int(v)
    except Exception:
        x = 0
    if x >= 5:
        return "5"
    if x >= 3:
        return "3-4"
    if x >= 1:
        return "1-2"
    return "0"


def _strike_bucket(v: int) -> str:
    """Small strike buckets: exact strike counts are too sparse for reuse."""
    try:
        x = int(v)
    except Exception:
        x = 0
    if x <= 0:
        return "0"
    if x == 1:
        return "1"
    if x <= 3:
        return "2-3"
    return "4+"


def _target_distance_bucket(dr: int, dc: int) -> str:
    """Reusable target-distance bucket from already binned row/col deltas."""
    d = abs(int(dr)) + abs(int(dc))
    if d <= 0:
        return "at"
    if d <= 1:
        return "near"
    if d <= 3:
        return "mid"
    return "far"


def _state_hash(g: HybridGame, who: str, mode: str) -> str:
    """Abstract tactical state for MemoryMaze policy reuse.

    Version v2.0 of this runner used a near-positional state (r/c buckets,
    exact target symbol, face vector, exact pairs_left). That produced valid
    learning rows, but the next policy run almost never saw the same hash again
    (e.g. policy_used=14 vs fallback=8986). For MemoryMaze the reusable decision
    context is tactical, not positional: whether a first claim exists, whether a
    second matching blocker is being pursued, whether a reveal is immediately
    useful, and the coarse direction/distance to the current target. Exact board
    coordinates and symbol letters are intentionally removed because they create
    variance without adding transferable policy value.
    """
    st = g.state()
    tgt_sym, tgt_dr, tgt_dc = _target_info(g, who)
    claim = _first_claim_symbol(g, who)
    ahead = _ahead_symbol(g, who)
    opp = "p2" if who == "p1" else "p1"
    strikes = st.get("strikes") or {}

    has_claim = bool(claim)
    has_target = bool(tgt_sym)
    ahead_obj = bool(ahead)
    ahead_is_claim = bool(has_claim and ahead == claim)
    ahead_is_target = bool(has_target and ahead == tgt_sym)

    payload = {
        "game": "memorymaze_hybrid",
        "schema": "reuse_v2",
        "mode": mode,
        "who": who,
        "phase": "second" if has_claim else "first",
        "pairs": _pairs_bucket(int(st.get("pairs_left") or 0)),
        "has_target": 1 if has_target else 0,
        "ahead": "target" if ahead_is_target else "claim" if ahead_is_claim else "other" if ahead_obj else "none",
        "tdr": int(tgt_dr),
        "tdc": int(tgt_dc),
        "tdist": _target_distance_bucket(int(tgt_dr), int(tgt_dc)),
        "my_strikes": _strike_bucket(int(strikes.get(who, 0) or 0)),
        "opp_strikes": _strike_bucket(int(strikes.get(opp, 0) or 0)),
    }
    return _stable_hash(payload)

class _PolicyReader:
    """Fast read-side adapter for policy_rules with heuristic fallback.

    Der normale UniversalPolicy-Read ist bewusst allgemein. Für einen Runner,
    der tens of thousands of low-level ticks ausführen kann, verwenden wir eine
    kleine per-process Cache-Leseseite und fragen jeden state_hash nur einmal ab.
    Unbekannte Zustände nutzen die professionelle Spielheuristik als Fallback.
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = str(namespace)
        self.cache: Dict[str, Dict[str, Tuple[float, float]]] = {}
        self.policy_used = 0
        self.fallback = 0

    def choose(self, state_hash: str, legal: List[str], fallback_action: str) -> str:
        if not legal:
            self.fallback += 1
            return fallback_action or ""
        if fallback_action not in legal:
            fallback_action = legal[0]
        table = self.cache.get(state_hash)
        if table is None:
            table = {}
            if sql_manager is not None:
                try:
                    with sql_manager.get_conn(None) as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                            (self.namespace, state_hash),
                        )
                        for row in cur.fetchall() or []:
                            try:
                                action = str(row["action"] if hasattr(row, "keys") else row[0])
                                q = float(row["q"] if hasattr(row, "keys") else row[1])
                                n = float(row["n"] if hasattr(row, "keys") else row[2])
                            except Exception:
                                continue
                            table[action] = (q, n)
                except Exception:
                    table = {}
            self.cache[state_hash] = table
        known = [(a, table[a][0], table[a][1]) for a in legal if a in table and table[a][1] > 0]
        if not known:
            self.fallback += 1
            return fallback_action
        known.sort(key=lambda x: (x[1], x[2], x[0]), reverse=True)
        self.policy_used += 1
        return str(known[0][0])


def _episode_outcome(winner: str, who: str) -> float:
    if winner == who:
        return 1.0
    if winner in ("p1", "p2") and winner != who:
        return -1.0
    return 0.0


def _side_delta(before: Dict[str, Any], after: Dict[str, Any], who: str) -> float:
    opp = "p2" if who == "p1" else "p1"
    b_pc = before.get("pairs_cleared") or {}
    a_pc = after.get("pairs_cleared") or {}
    b_st = before.get("strikes") or {}
    a_st = after.get("strikes") or {}
    my_pairs = int(a_pc.get(who, 0) or 0) - int(b_pc.get(who, 0) or 0)
    opp_pairs = int(a_pc.get(opp, 0) or 0) - int(b_pc.get(opp, 0) or 0)
    my_strikes = int(a_st.get(who, 0) or 0) - int(b_st.get(who, 0) or 0)
    opp_strikes = int(a_st.get(opp, 0) or 0) - int(b_st.get(opp, 0) or 0)
    return (1.0 * my_pairs) - (0.4 * opp_pairs) - (0.8 * my_strikes) + (0.25 * opp_strikes)


def _sign_outcome(v: float) -> float:
    if v > 1e-9:
        return 1.0
    if v < -1e-9:
        return -1.0
    return 0.0


def _learn_many_policy_rules(namespace: str, items: List[Dict[str, Any]]) -> bool:
    """UniversalPolicy-compatible batch upsert without importing the full module.

    Der Standard-Pfad core.universal_policy.Policy.learn_many() ruft beim Import
    ensure_schema() auf. In schlanken Backup-/Diagnoseumgebungen kann das unnötig
    lange blockieren. Dieser Runner schreibt deshalb exakt in dieselbe Tabelle
    und mit derselben Outcome-Semantik (+/−/draw), nutzt aber DBWriter, sobald
    OROMA_DBW_ENABLE aktiv ist.
    """
    if sql_manager is None or not items:
        return False
    aggregated: Dict[Tuple[str, str], Dict[str, int]] = {}
    now = int(time.time())
    for it in items:
        sh = str(it.get("state_hash") or "").strip()
        action = str(it.get("action") or "").strip()
        if not sh or not action:
            continue
        try:
            out_f = float(it.get("outcome", 0.0) or 0.0)
        except Exception:
            out_f = 0.0
        key = (sh, action)
        agg = aggregated.setdefault(key, {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": now})
        agg["n"] += 1
        if out_f > 1e-9:
            agg["pos"] += 1
        elif out_f < -1e-9:
            agg["neg"] += 1
        else:
            agg["draw"] += 1
        try:
            ts = int(it.get("ts") or now)
        except Exception:
            ts = now
        if ts > agg["last_ts"]:
            agg["last_ts"] = ts

    if not aggregated:
        return False

    params: List[List[Any]] = []
    for (sh, action), agg in aggregated.items():
        n = int(agg["n"])
        pos = int(agg["pos"])
        neg = int(agg["neg"])
        draw = int(agg["draw"])
        q = (float(pos - neg) / float(n)) if n > 0 else 0.0
        params.append([str(namespace), sh, action, n, pos, neg, draw, q, int(agg["last_ts"]), None])

    upsert_sql = """INSERT INTO policy_rules
                       (namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
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
                                     END,
                           centroid = COALESCE(excluded.centroid, policy_rules.centroid)
                    """

    use_dbw = False
    db_writer_client = None
    try:
        from core import db_writer_client as _dbw  # type: ignore
        db_writer_client = _dbw
        use_dbw = os.environ.get("OROMA_DBW_ENABLE", "0").strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        db_writer_client = None
        use_dbw = False

    try:
        if use_dbw and db_writer_client is not None:
            try:
                chunk_size = max(1, int(os.environ.get("OROMA_POLICY_DBW_CHUNK", "25")))
            except Exception:
                chunk_size = 25
            for i in range(0, len(params), chunk_size):
                db_writer_client.executemany(
                    upsert_sql,
                    params[i:i + chunk_size],
                    tag="memorymaze_hybrid.learn_many",
                    priority="low",
                    timeout_ms=60000,
                    db="oroma",
                )
        else:
            with sql_manager.get_conn(None) as conn:
                cur = conn.cursor()
                cur.executemany(upsert_sql, params)
                conn.commit()
        return True
    except Exception as e:
        print(f"[memorymaze_hybrid_runner] policy_rules upsert failed: {e}", file=sys.stderr)
        return False


def _run_batch(
    mode: str,
    policy: bool,
    games: int,
    seed: Optional[int],
    eps: float,
    map_kind: str,
    max_steps: int,
    namespace: str,
) -> Dict[str, Any]:
    g = HybridGame(map_kind=map_kind)
    reader = _PolicyReader(namespace=namespace)
    policy_learn_ok = False
    rng = random.Random(int(seed) if seed is not None else int(time.time()))

    t0 = time.time()
    steps_total = 0
    wins_p1 = 0
    wins_p2 = 0
    draws = 0
    wins_by_pairs = 0
    wins_by_strikes = 0
    pairs_left_end_sum = 0
    pairs_cleared_sum = 0
    strikes_p1_sum = 0
    strikes_p2_sum = 0
    p3_strikes_sum = 0
    reveal_sum = 0
    claim_sum = 0
    second_reveal_sum = 0
    match_sum = 0
    mismatch_sum = 0
    timeout_sum = 0
    pit_hit_sum = 0
    p3_contact_sum = 0
    learn_items: List[Dict[str, Any]] = []

    eps_i = 0.0 if policy else eps

    for gi in range(int(games)):
        gseed = None if seed is None else int(seed) + gi
        g.reset(seed=gseed, mode=mode)
        game_items: List[Dict[str, Any]] = []
        for _ in range(int(max_steps)):
            st_before = g.state()
            if st_before.get("winner") is not None:
                break

            per_side: Dict[str, Tuple[str, str]] = {}
            acts: Dict[str, str] = {}
            for who in ("p1", "p2"):
                sh = _state_hash(g, who, mode)
                legal = [a for a in g.legal_actions(who) if a in ACTIONS]
                fallback = g.ai_action(who, eps=eps_i)
                if policy:
                    action = reader.choose(sh, legal, fallback)
                else:
                    action = fallback if fallback in legal else (rng.choice(legal) if legal else fallback)
                acts[who] = action
                per_side[who] = (sh, action)

            if mode == "hard_p3":
                acts["p3"] = g.ai_action("p3", eps=0.0)

            st_after = g.step(acts)
            steps_total += 1

            # Learn from explore games only. Policy games are evaluation/usage;
            # Explore games supply fresher coverage without self-reinforcing a
            # possibly weak policy. Outcomes are shaped by immediate game events.
            if not policy:
                now_ts = int(time.time())
                for who in ("p1", "p2"):
                    sh, action = per_side[who]
                    delta = _side_delta(st_before, st_after, who)
                    game_items.append({
                        "state_hash": sh,
                        "action": action,
                        "event_delta": float(delta),
                        "side": who,
                        "ts": now_ts,
                    })

            if st_after.get("winner") is not None:
                break

        st = g.state()
        w = str(st.get("winner") or "")
        reason = str(st.get("winner_reason") or "")
        if w == "p1":
            wins_p1 += 1
        elif w == "p2":
            wins_p2 += 1
        else:
            draws += 1
        if reason == "pairs_cleared":
            wins_by_pairs += 1
        elif reason.endswith("_strikes"):
            wins_by_strikes += 1

        pc = st.get("pairs_cleared") or {}
        pairs_cleared_sum += int(pc.get("p1", 0) or 0) + int(pc.get("p2", 0) or 0)
        pairs_left_end_sum += int(st.get("pairs_left", 0))
        s = st.get("strikes", {}) or {}
        strikes_p1_sum += int(s.get("p1", 0))
        strikes_p2_sum += int(s.get("p2", 0))
        p3_strikes_sum += int(s.get("p3", 0))
        tel = st.get("telemetry") or {}
        reveal_sum += int(tel.get("reveal_attempts", 0) or 0)
        claim_sum += int(tel.get("claims", 0) or 0)
        second_reveal_sum += int(tel.get("second_reveals", 0) or 0)
        match_sum += int(tel.get("matches", 0) or 0)
        mismatch_sum += int(tel.get("mismatches", 0) or 0)
        timeout_sum += int(tel.get("claim_timeouts", 0) or 0)
        pit_hit_sum += int(tel.get("pit_hits", 0) or 0)
        p3_contact_sum += int(tel.get("p3_contacts", 0) or 0)

        if not policy and game_items:
            for it in game_items:
                side = str(it.get("side") or "")
                final = _episode_outcome(w, side)
                shaped = (0.70 * final) + float(it.get("event_delta") or 0.0)
                outcome = _sign_outcome(shaped)
                # Avoid recreating the old Pong-style draw wall: neutral ticks
                # without final or event signal are telemetry, not learning.
                if outcome == 0.0:
                    continue
                it["outcome"] = outcome
                it.pop("event_delta", None)
                it.pop("side", None)
                learn_items.append(it)

    if (not policy) and learn_items:
        policy_learn_ok = _learn_many_policy_rules(namespace, learn_items)

    dur_ms = (time.time() - t0) * 1000.0
    n_games = max(1, int(games))
    return {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": float(dur_ms),
        "games": int(games),
        "steps": int(steps_total),
        "wins_p1": int(wins_p1),
        "wins_p2": int(wins_p2),
        "draws": int(draws),
        "wins_by_pairs": int(wins_by_pairs),
        "wins_by_strikes": int(wins_by_strikes),
        "avg_pairs_left_end": float(pairs_left_end_sum / n_games),
        "avg_pairs_cleared": float(pairs_cleared_sum / n_games),
        "avg_strikes_p1": float(strikes_p1_sum / n_games),
        "avg_strikes_p2": float(strikes_p2_sum / n_games),
        "avg_strikes_p3": float(p3_strikes_sum / n_games),
        "avg_reveals": float(reveal_sum / n_games),
        "avg_claims": float(claim_sum / n_games),
        "avg_second_reveals": float(second_reveal_sum / n_games),
        "avg_matches": float(match_sum / n_games),
        "avg_mismatches": float(mismatch_sum / n_games),
        "avg_claim_timeouts": float(timeout_sum / n_games),
        "avg_pit_hits": float(pit_hit_sum / n_games),
        "avg_p3_contacts": float(p3_contact_sum / n_games),
        "learn_items": int(len(learn_items) if not policy else 0),
        "policy_used": int(reader.policy_used if policy else 0),
        "policy_fallback": int(reader.fallback if policy else 0),
        "policy_learn_ok": bool(policy_learn_ok),
        "mode": str(mode),
        "eps": float(eps_i),
        "map_kind": str(map_kind),
        "max_steps": int(max_steps),
        "namespace": str(namespace),
        "source": "orchestrator",
        "policy": bool(policy),
    }


def _db_write(kind: str, label: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None:
        return None
    ts0 = int(meta.get("ts_start", _now_s()))
    ts1 = int(meta.get("ts_end", _now_s()))
    if ts1 <= ts0:
        ts1 = ts0 + 1
        meta["ts_end"] = ts1
    try:
        eid = sql_manager.insert_episode(
            kind=str(kind),
            ts_start=int(ts0),
            ts_end=int(ts1),
            source="orchestrator",
            label=str(label),
            meta=meta,
        )
    except Exception as e:
        print(f"[memorymaze_hybrid_runner] DB write failed insert_episode: {e}", file=sys.stderr)
        return None

    try:
        base_ts = int(meta.get("ts_end", ts1))
        for k in (
            "duration_ms",
            "games",
            "steps",
            "wins_p1",
            "wins_p2",
            "draws",
            "wins_by_pairs",
            "wins_by_strikes",
            "avg_pairs_left_end",
            "avg_pairs_cleared",
            "avg_strikes_p1",
            "avg_strikes_p2",
            "avg_strikes_p3",
            "avg_reveals",
            "avg_claims",
            "avg_second_reveals",
            "avg_matches",
            "avg_mismatches",
            "avg_claim_timeouts",
            "avg_pit_hits",
            "avg_p3_contacts",
            "learn_items",
            "policy_used",
            "policy_fallback",
        ):
            v = meta.get(k)
            if v is None:
                continue
            sql_manager.insert_episodic_metric(episode_id=int(eid), ts=int(base_ts), key=str(k), value=float(v))
    except Exception as e:
        print(f"[memorymaze_hybrid_runner] DB write failed metrics: {e}", file=sys.stderr)

    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("OROMA_MMZ_MODE", "normal"), choices=["normal", "hard_p3"])
    ap.add_argument("--map", dest="map_kind", default=os.environ.get("OROMA_MMZ_MAP", "sym"), choices=["sym", "asym"])
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_MMZ_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_MMZ_EXPLORE_GAMES", 100))
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_MMZ_EPS", 0.10))
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_MMZ_MAX_STEPS", 900))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--namespace", default=os.environ.get("OROMA_MMZ_POLICY_NAMESPACE", "game:memorymaze_hybrid"))
    args = ap.parse_args()

    policy_meta = _run_batch(args.mode, True, args.policy_games, args.seed, args.eps, args.map_kind, args.max_steps, args.namespace)
    explore_meta = _run_batch(args.mode, False, args.explore_games, args.seed, args.eps, args.map_kind, args.max_steps, args.namespace)

    ok = True
    eid_policy = None
    eid_explore = None
    if sql_manager is not None:
        eid_policy = _db_write(
            kind=f"game:memorymaze_hybrid:{args.mode}:policy_batch",
            label=f"memorymaze_hybrid:{args.mode}:policy ({args.policy_games} games)",
            meta=policy_meta,
        )
        eid_explore = _db_write(
            kind=f"game:memorymaze_hybrid:{args.mode}:explore_batch",
            label=f"memorymaze_hybrid:{args.mode}:explore ({args.explore_games} games)",
            meta=explore_meta,
        )
        ok = (eid_policy is not None) and (eid_explore is not None)

    out = {
        "ok": True,
        "have_db": bool(sql_manager is not None),
        "have_policy": bool(sql_manager is not None),
        "db_written": bool(ok) if sql_manager is not None else False,
        "policy": dict(policy_meta, episode_id=eid_policy),
        "explore": dict(explore_meta, episode_id=eid_explore),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

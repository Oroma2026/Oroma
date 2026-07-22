#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/flappy_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   FlappyBird Daily Runner – professioneller Headless-Lernloop
# Version: v3.7.7-flappy-pro-v3
# Stand:   2026-06-28
# Autor:   ORÓMA · Jörg Werner + GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Führt FlappyBird automatisiert im Headless-Modus aus und schreibt aggregierte
# Ergebnisse in oroma.db. Der Runner ist bewusst vergleichbar zu Snake/Pong/
# MemoryMaze aufgebaut: policy_batch benchmarked gelernte Regeln, explore_batch
# erzeugt neue Lernitems für core.universal_policy.Policy.
#
# PROFESSIONELLER LERNPFAD v3
# ---------------------------
# Der historische Runner nutzte einen zu feinen flappy:v1-Hash und schrieb ein
# pauschales Episoden-Outcome auf die komplette Trajektorie. Dadurch war der
# Reuse gering und das Signal grob. v3 trennt den neuen Lernpfad sichtbar über
# state_hash-Präfix "flappy:pro_v3" und nutzt nur taktische, wiederverwendbare
# Zustandsmerkmale:
#   • relative Höhe zur Gap-Mitte (dy-Bucket)
#   • vertikale Geschwindigkeit (vy-Bucket)
#   • horizontale Distanz zur nächsten Pipe (dx-Bucket)
#   • Clearance/Risiko zur Gap-Kante (clr-Bucket)
#   • Gap-Höhe, Score- und Survival-Bucket
#   • grobe Weltzonen oben/unten sowie Pipe-Nähe
#
# ACTION SPACE
# ------------
#   0 = nichts tun / fallen lassen
#   1 = flap / Impuls nach oben
#
# FALLBACK / GUARD
# ----------------
# Wenn keine passende Policy-Regel vorhanden ist, nutzt der Runner eine robuste
# Gap-Tracking-Heuristik. In Policy-Läufen schützt ein leichter Safety-Guard vor
# direkt tödlichen Aktionen (Welt-/Pipe-Nähe). Explore-Läufe bleiben absichtlich
# ungeschützt genug, um negative Beispiele zu erzeugen.
#
# CREDIT ASSIGNMENT
# -----------------
# Lernen ist ereignisbasiert und vermeidet Draw-Wände:
#   • passierte Pipe      → positives Outcome für die letzten N Entscheidungen
#   • Kollision/Tod       → negatives Outcome für die letzten M Entscheidungen
#   • neutrale Survival-Ticks werden NICHT als Draw gelernt
#
# v3 korrigiert zusätzlich die Flappy-spezifische Credit-Asymmetrie:
#   • Welt-Tod sehr früh im Spiel erzeugt nur kurze negative Credits
#   • Pipe-Tod bleibt informativer negativer Credit
#   • Pipe-Passage erhält längere/gewichtete positive Credits
#   • Explore startet nach einer kurzen Safe-Warmup-Phase, statt direkt am
#     Anfang zufällige Aktionen zu erzwingen
#
# Dadurch wächst policy_rules nur mit echtem Signal. Alte flappy:v1/pro_v2-Regeln
# bleiben in der DB, beeinflussen aber flappy:pro_v3 nicht.
#
# PRODUKTIONSREGELN
# -----------------
#   • headless-only, kein Qt/Wayland/X11
#   • keine DB-Schemaänderung
#   • DB-Verbindungen über sql_manager-Kontextmanager
#   • Fehler sind sichtbar über stderr/JSON ok=false
# =============================================================================

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import sql_manager


def _load_flappy_classes():
    """Load mini_programs/flappybird.py without importing mini_programs package.

    The package __init__ performs registry auto-discovery and can start expensive
    imports on the Pi. The daily runner needs only the pure headless engine file,
    so it loads the module directly from disk.
    """
    module_path = Path(__file__).resolve().parents[1] / "mini_programs" / "flappybird.py"
    spec = importlib.util.spec_from_file_location("oroma_flappybird_engine", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load flappy engine from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("oroma_flappybird_engine", mod)
    spec.loader.exec_module(mod)
    return mod.FlappyBird, mod.FBConfig


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


def _bucket(v: float, cuts: List[float]) -> int:
    try:
        x = float(v)
    except Exception:
        x = 0.0
    for i, c in enumerate(cuts):
        if x <= c:
            return i
    return len(cuts)


class PolicyShim:
    """UniversalPolicy-Adapter mit Flappy-spezifischer Abstraktion und Fallback."""

    def __init__(self, namespace: str):
        self.namespace = str(namespace or "game:flappy")
        self.pol = None
        self.policy_used = 0
        self.policy_fallback = 0
        self.policy_guarded = 0
        self.policy_q_rejected = 0
        self.policy_q_min_gate = float(_env_float("OROMA_FLAPPY_POLICY_Q_MIN_GATE", -0.05))
        self.policy_min_n = int(_env_int("OROMA_FLAPPY_POLICY_MIN_N", 1))
        self.policy_enabled = (os.environ.get("OROMA_FLAPPY_POLICY_ENABLE", "1") or "1").lower() in {"1", "true", "yes", "on"}
        if self.policy_enabled:
            try:
                from core.universal_policy import Policy  # type: ignore
                self.pol = Policy(namespace=self.namespace)
            except Exception:
                self.pol = None

    @staticmethod
    def _state_vals(st: Dict[str, Any]) -> Tuple[float, float, float, float, float, int, int]:
        y = float(st.get("y", 0.5) or 0.5)
        vy = float(st.get("vy", 0.0) or 0.0)
        dx = float(st.get("dx", 1.0) or 1.0)
        gap_y = float(st.get("gap_y", 0.5) or 0.5)
        gap_h = float(st.get("gap_h", 0.25) or 0.25)
        score = int(st.get("score", 0) or 0)
        steps = int(st.get("steps", 0) or 0)
        return y, vy, dx, gap_y, gap_h, score, steps

    def state_hash(self, st: Dict[str, Any]) -> str:
        y, vy, dx, gap_y, gap_h, score, steps = self._state_vals(st)
        dy = gap_y - y
        half_gap = max(0.03, gap_h * 0.5)
        clearance = half_gap - abs(dy)
        near_pipe = 1 if dx <= 0.16 else 0
        danger_top = 1 if y > 0.90 else 0
        danger_bottom = 1 if y < 0.10 else 0
        # Grobe, wiederverwendbare Buckets. Keine exakten Koordinaten.
        dy_b = _bucket(dy, [-0.35, -0.22, -0.12, -0.05, 0.05, 0.12, 0.22, 0.35])
        vy_b = _bucket(vy, [-0.85, -0.45, -0.18, 0.00, 0.18, 0.45, 0.85])
        dx_b = _bucket(dx, [0.08, 0.16, 0.28, 0.45, 0.70, 0.95])
        clr_b = _bucket(clearance, [-0.12, -0.05, 0.00, 0.04, 0.10, 0.20])
        gh_b = _bucket(gap_h, [0.20, 0.23, 0.26, 0.30])
        score_b = _bucket(float(score), [0, 1, 3, 6, 10])
        steps_b = _bucket(float(steps), [20, 50, 100, 200, 400, 800])
        return (
            "flappy:pro_v3:"
            f"dy={dy_b}:vy={vy_b}:dx={dx_b}:clr={clr_b}:gh={gh_b}:"
            f"np={near_pipe}:top={danger_top}:bot={danger_bottom}:"
            f"score={score_b}:surv={steps_b}"
        )

    def _heuristic_action(self, st: Dict[str, Any]) -> int:
        y, vy, dx, gap_y, gap_h, _score, _steps = self._state_vals(st)
        # Lead-Ziel: bei naher Pipe stärker zur Gap-Mitte, sonst leicht unterhalb
        # der Mitte bleiben, damit bei Fallgeschwindigkeit rechtzeitig geflappt wird.
        lead = 0.04 if dx > 0.35 else 0.01
        target = max(0.08, min(0.92, gap_y - lead))
        dy = target - y

        # Harte Welt-Sicherungen.
        if y < 0.16 and vy > -0.45:
            return 1
        if y > 0.88 and vy < 0.10:
            return 0

        # Pipe-Fenster: lieber etwas unter Mitte mit geringer Fallgeschwindigkeit.
        if dx <= 0.22:
            if y < gap_y - (gap_h * 0.20) or vy > 0.22:
                return 1
            return 0

        # Freiflug: PD-ähnliche einfache Steuerung.
        if dy > 0.07 or vy > 0.30:
            return 1
        return 0

    @staticmethod
    def _predicted_next_y(st: Dict[str, Any], action: int) -> float:
        y, vy, _dx, _gap_y, _gap_h, _score, _steps = PolicyShim._state_vals(st)
        gravity = 1.5
        flap_impulse = -0.6
        dt = 0.05
        if int(action) == 1:
            vy += flap_impulse
        vy += gravity * dt
        return y + vy * dt

    def _unsafe_immediate(self, st: Dict[str, Any], action: int) -> bool:
        y, _vy, dx, gap_y, gap_h, _score, _steps = self._state_vals(st)
        ny = self._predicted_next_y(st, int(action))
        if ny < 0.015 or ny > 0.985:
            return True
        # Grobe Pipe-Sicherheitsprüfung nahe am Vogel-x.
        if dx <= 0.10:
            margin = 0.03
            if ny < (gap_y - gap_h * 0.5 + margin):
                return True
            if ny > (gap_y + gap_h * 0.5 - margin):
                return True
        return False

    def _policy_choice(self, sh: str, legal: List[int]) -> Tuple[Optional[int], bool]:
        """Return a usable learned action or None.

        Flappy starts with sparse positive events and many death events.  The
        runner therefore must not actively prefer known-bad early rules.  Unlike
        the generic UniversalPolicy chooser, this domain shim only uses rules
        that have at least minimal evidence and whose q is not below the local
        safety gate.  Rejected low-q states fall back to the deterministic
        Flappy heuristic and are counted via policy_q_rejected for diagnosis.
        """
        if self.pol is None:
            return None, False
        legal_str = [str(int(a)) for a in legal]
        try:
            with sql_manager.get_conn(None) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                    (self.namespace, sh),
                )
                rows = cur.fetchall() or []
        except Exception:
            rows = []

        candidates: List[Tuple[float, float, int]] = []
        seen_legal = 0
        for row in rows:
            try:
                a_raw = str(row["action"] if hasattr(row, "keys") else row[0])
                if a_raw not in legal_str:
                    continue
                seen_legal += 1
                q = float(row["q"] if hasattr(row, "keys") else row[1])
                n = float(row["n"] if hasattr(row, "keys") else row[2])
                if n < float(self.policy_min_n):
                    continue
                if q < float(self.policy_q_min_gate):
                    continue
                candidates.append((q, n, int(a_raw)))
            except Exception:
                continue

        if not candidates:
            if seen_legal:
                self.policy_q_rejected += 1
            return None, False

        candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return int(candidates[0][2]), True

    def choose(self, st: Dict[str, Any], legal: List[int], mode: str) -> int:
        sh = self.state_hash(st)
        a, used = self._policy_choice(sh, legal)
        if used and a is not None:
            self.policy_used += 1
            chosen = int(a)
        else:
            self.policy_fallback += 1
            chosen = int(self._heuristic_action(st))

        # Safety-Guard nur im Policy-Benchmark. Explore soll negative Beispiele
        # erzeugen dürfen; sonst lernt die Policy nie, welche Aktionen tödlich sind.
        if mode == "policy" and self._unsafe_immediate(st, chosen):
            safe = int(self._heuristic_action(st))
            if safe != chosen:
                self.policy_guarded += 1
                chosen = safe
        return int(chosen if chosen in legal else legal[0])

    def learn_many(self, items: List[Dict[str, Any]]) -> bool:
        if not items or self.pol is None:
            return False
        try:
            self.pol.learn_many(items)
            return True
        except Exception as e:
            sys.stderr.write(f"[flappy_daily_runner] policy.learn_many failed: {e!r}\n")
            return False


def _add_credit(
    items: List[Dict[str, Any]],
    traj: List[Dict[str, Any]],
    window: int,
    outcome: float,
    now: int,
    repeat: int = 1,
) -> int:
    """Append signed policy evidence and return the number of inserted items.

    Neutral ticks are intentionally ignored.  Positive pass events may be
    repeated to counter Flappy's sparse-reward asymmetry; this is equivalent to
    stronger evidence in ORÓMA's tabular policy table and does not require any
    DB-schema change.
    """
    if abs(float(outcome)) <= 1e-9:
        return 0
    added = 0
    tail = list(traj[-max(1, int(window)):])
    for _ in range(max(1, int(repeat))):
        for tr in tail:
            items.append({
                "state_hash": tr["state_hash"],
                "action_canon": int(tr["action_canon"]),
                "side": "X",
                "outcome": 1.0 if outcome > 0 else -1.0,
                "ts": int(now),
            })
            added += 1
    return int(added)


def run_one_episode(
    rng: random.Random,
    shim: PolicyShim,
    mode: str,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    max_steps: int,
    pass_credit_steps: int,
    pass_credit_repeats: int,
    death_world_credit_steps: int,
    death_world_early_credit_steps: int,
    death_world_early_steps: int,
    death_pipe_credit_steps: int,
    explore_warmup_steps: int,
) -> Dict[str, Any]:
    FlappyBird, FBConfig = _load_flappy_classes()

    ep_seed = int(rng.randrange(1, 2**31 - 1))
    cfg = FBConfig(seed=ep_seed, max_steps=max(50, int(max_steps)))
    env = FlappyBird(cfg)
    env.reset(seed=ep_seed)

    legal = [0, 1]
    explore_budget = 0
    traj: List[Dict[str, Any]] = []
    learn_items: List[Dict[str, Any]] = []

    t0 = time.time()
    total_r = 0.0
    passes = 0
    death_world = 0
    death_pipe = 0
    death_max_steps = 0
    final_reason = ""
    pass_credit_items = 0
    death_credit_items = 0
    early_world_death = 0

    while True:
        st = env.get_state()
        if not st.get("alive", True):
            break

        if mode == "explore":
            # v3: keine erzwungenen Zufallsaktionen direkt beim Start.
            # Frühe Welt-Tode erzeugen sonst viele negative Credits für kaum
            # wiederverwendbare Startzustände. Nach dem Warmup bleibt Explore
            # absichtlich stochastisch genug, um Gegenbeispiele zu erzeugen.
            step_now = int(st.get("steps", 0) or 0)
            if step_now >= int(explore_warmup_steps) and explore_budget < int(explore_moves_per_game):
                explore_budget += 1
                a = int(rng.choice(legal))
            elif step_now >= int(explore_warmup_steps) and rng.random() < float(eps):
                a = int(rng.choice(legal))
            else:
                a = int(shim.choose(st, legal, mode="explore"))
        else:
            a = int(shim.choose(st, legal, mode="policy"))

        if learn:
            traj.append({"state_hash": shim.state_hash(st), "action_canon": int(a)})

        _st2, r, done, info = env.step(int(a))
        try:
            total_r += float(r)
        except Exception:
            pass

        if learn and bool((info or {}).get("passed")):
            passes += 1
            pass_credit_items += _add_credit(
                learn_items,
                traj,
                int(pass_credit_steps),
                +1.0,
                int(time.time()),
                repeat=int(pass_credit_repeats),
            )

        if done:
            final_reason = str((info or {}).get("reason") or "done")
            if final_reason == "world_collision":
                death_world = 1
            elif final_reason == "pipe_collision":
                death_pipe = 1
            elif final_reason == "max_steps":
                death_max_steps = 1
            if learn and final_reason == "world_collision":
                steps_now = int((info or {}).get("steps") or st.get("steps") or len(traj) or 0)
                if steps_now <= int(death_world_early_steps):
                    early_world_death = 1
                    credit_window = int(death_world_early_credit_steps)
                else:
                    credit_window = int(death_world_credit_steps)
                death_credit_items += _add_credit(learn_items, traj, credit_window, -1.0, int(time.time()))
            elif learn and final_reason == "pipe_collision":
                death_credit_items += _add_credit(learn_items, traj, int(death_pipe_credit_steps), -1.0, int(time.time()))
            break

    policy_learn_ok = False
    if learn and learn_items:
        policy_learn_ok = bool(shim.learn_many(learn_items))

    dt_ms = int(round((time.time() - t0) * 1000.0))
    final = env.get_state()
    score = int(final.get("score", 0) or 0)
    steps = int(final.get("steps", 0) or 0)

    return {
        "score": score,
        "steps": steps,
        "return": float(total_r),
        "duration_ms": int(dt_ms),
        "death_world": int(death_world),
        "death_pipe": int(death_pipe),
        "death_max_steps": int(death_max_steps),
        "early_world_death": int(early_world_death),
        "passes": int(passes or score),
        "pass_credit_items": int(pass_credit_items),
        "death_credit_items": int(death_credit_items),
        "final_reason": final_reason,
        "learn_items": int(len(learn_items)),
        "policy_learn_ok": bool(policy_learn_ok),
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


def _avg(xs: List[float]) -> float:
    return float(sum(xs) / float(len(xs) or 1))


def run_batch(
    rng: random.Random,
    namespace: str,
    mode: str,
    games: int,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    source: str,
    label: str,
    max_steps: int,
    pass_credit_steps: int,
    pass_credit_repeats: int,
    death_world_credit_steps: int,
    death_world_early_credit_steps: int,
    death_world_early_steps: int,
    death_pipe_credit_steps: int,
    explore_warmup_steps: int,
) -> Tuple[Dict[str, Any], bool]:
    shim = PolicyShim(namespace=namespace)

    ts_start = int(time.time())
    t0 = time.time()

    scores: List[float] = []
    steps: List[float] = []
    rets: List[float] = []
    game_ms: List[float] = []
    learn_items_total = 0
    death_world = 0
    death_pipe = 0
    death_max_steps = 0
    early_world_deaths = 0
    passes_total = 0
    pass_credit_items_total = 0
    death_credit_items_total = 0
    policy_learn_ok = False

    for _ in range(max(0, int(games))):
        res = run_one_episode(
            rng=rng,
            shim=shim,
            mode=mode,
            eps=float(eps),
            explore_moves_per_game=int(explore_moves_per_game),
            learn=bool(learn),
            max_steps=int(max_steps),
            pass_credit_steps=int(pass_credit_steps),
            pass_credit_repeats=int(pass_credit_repeats),
            death_world_credit_steps=int(death_world_credit_steps),
            death_world_early_credit_steps=int(death_world_early_credit_steps),
            death_world_early_steps=int(death_world_early_steps),
            death_pipe_credit_steps=int(death_pipe_credit_steps),
            explore_warmup_steps=int(explore_warmup_steps),
        )
        scores.append(float(res["score"]))
        steps.append(float(res["steps"]))
        rets.append(float(res["return"]))
        game_ms.append(float(res["duration_ms"]))
        learn_items_total += int(res.get("learn_items") or 0)
        death_world += int(res.get("death_world") or 0)
        death_pipe += int(res.get("death_pipe") or 0)
        death_max_steps += int(res.get("death_max_steps") or 0)
        early_world_deaths += int(res.get("early_world_death") or 0)
        passes_total += int(res.get("passes") or 0)
        pass_credit_items_total += int(res.get("pass_credit_items") or 0)
        death_credit_items_total += int(res.get("death_credit_items") or 0)
        policy_learn_ok = bool(policy_learn_ok or res.get("policy_learn_ok"))

    duration_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = int(time.time())

    avg_score = _avg(scores)
    avg_steps = _avg(steps)
    avg_ret = _avg(rets)
    avg_game_ms = _avg(game_ms) if game_ms else 0.0
    high_score = max(scores) if scores else 0.0
    high_steps = max(steps) if steps else 0.0

    meta = {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": duration_ms,
        "games": int(games),
        "avg_score": avg_score,
        "high_score": high_score,
        "avg_steps": avg_steps,
        "high_steps": high_steps,
        "avg_return": avg_ret,
        "avg_game_ms": avg_game_ms,
        "mode": "policy" if mode == "policy" else "explore",
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(0.0 if mode == "policy" else eps),
        "explore_moves_per_game": int(0 if mode == "policy" else explore_moves_per_game),
        "learn": bool(learn),
        "learn_items": int(learn_items_total),
        "policy_used": int(shim.policy_used),
        "policy_fallback": int(shim.policy_fallback),
        "policy_guarded": int(shim.policy_guarded),
        "policy_learn_ok": bool(policy_learn_ok),
        "death_world": int(death_world),
        "death_pipe": int(death_pipe),
        "death_max_steps": int(death_max_steps),
        "early_world_deaths": int(early_world_deaths),
        "passes": int(passes_total),
        "pass_credit_items": int(pass_credit_items_total),
        "death_credit_items": int(death_credit_items_total),
        "max_steps": int(max_steps),
        "pass_credit_steps": int(pass_credit_steps),
        "pass_credit_repeats": int(pass_credit_repeats),
        "death_world_credit_steps": int(death_world_credit_steps),
        "death_world_early_credit_steps": int(death_world_early_credit_steps),
        "death_world_early_steps": int(death_world_early_steps),
        "death_pipe_credit_steps": int(death_pipe_credit_steps),
        "explore_warmup_steps": int(explore_warmup_steps),
        "policy_q_min_gate": float(shim.policy_q_min_gate),
        "policy_min_n": int(shim.policy_min_n),
        "policy_q_rejected": int(shim.policy_q_rejected),
        "death_credit_steps": int(death_pipe_credit_steps),
        "source": str(source),
        "label": str(label),
        "runner": "tools/flappy_daily_runner.py",
        "shim": "tools/flappy_daily_runner.PolicyShim.pro_v3",
        "state_schema": "flappy:pro_v3",
    }

    kind = f"game:flappy:{'policy_batch' if mode=='policy' else 'explore_batch'}"
    eid = _db_write_episode(kind, meta)
    db_written = eid is not None

    if eid is not None:
        m = {
            "games": float(games),
            "avg_score": float(avg_score),
            "high_score": float(high_score),
            "avg_steps": float(avg_steps),
            "high_steps": float(high_steps),
            "avg_return": float(avg_ret),
            "duration_ms": float(duration_ms),
            "avg_game_ms": float(avg_game_ms),
            "eps": float(meta["eps"]),
            "explore_moves_per_game": float(meta["explore_moves_per_game"]),
            "policy_enabled": 1.0,
            "learn_items": float(learn_items_total),
            "policy_used": float(shim.policy_used),
            "policy_fallback": float(shim.policy_fallback),
            "policy_guarded": float(shim.policy_guarded),
            "death_world": float(death_world),
            "death_pipe": float(death_pipe),
            "death_max_steps": float(death_max_steps),
            "early_world_deaths": float(early_world_deaths),
            "passes": float(passes_total),
            "pass_credit_items": float(pass_credit_items_total),
            "death_credit_items": float(death_credit_items_total),
            "policy_q_rejected": float(shim.policy_q_rejected),
            "pass_credit_repeats": float(pass_credit_repeats),
            "death_world_credit_steps": float(death_world_credit_steps),
            "death_world_early_credit_steps": float(death_world_early_credit_steps),
            "death_pipe_credit_steps": float(death_pipe_credit_steps),
            "explore_warmup_steps": float(explore_warmup_steps),
            "max_steps": float(max_steps),
        }
        if not _db_write_metrics(int(eid), m):
            db_written = False

    meta["episode_id"] = int(eid) if eid is not None else None
    return meta, db_written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_FLAPPY_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_FLAPPY_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--namespace", default=os.environ.get("OROMA_FLAPPY_NAMESPACE", "game:flappy"))
    args = ap.parse_args()

    base_seed = int(args.seed) if args.seed is not None else (int(time.time() * 1000) & 0xFFFFFFFF)
    rng = random.Random(base_seed)
    namespace = str(args.namespace or "game:flappy")
    eps = float(_env_float("OROMA_FLAPPY_EPS", 0.10))
    explore_moves_per_game = int(_env_int("OROMA_FLAPPY_EXPLORE_MOVES", 2))
    max_steps = int(_env_int("OROMA_FLAPPY_MAX_STEPS", 1200))
    pass_credit_steps = int(_env_int("OROMA_FLAPPY_PASS_CREDIT_STEPS", 30))
    pass_credit_repeats = int(_env_int("OROMA_FLAPPY_PASS_CREDIT_REPEATS", 2))
    death_world_credit_steps = int(_env_int("OROMA_FLAPPY_DEATH_WORLD_CREDIT_STEPS", 6))
    death_world_early_credit_steps = int(_env_int("OROMA_FLAPPY_DEATH_WORLD_EARLY_CREDIT_STEPS", 3))
    death_world_early_steps = int(_env_int("OROMA_FLAPPY_DEATH_WORLD_EARLY_STEPS", 40))
    death_pipe_credit_steps = int(_env_int("OROMA_FLAPPY_DEATH_PIPE_CREDIT_STEPS", 18))
    explore_warmup_steps = int(_env_int("OROMA_FLAPPY_EXPLORE_WARMUP_STEPS", 30))

    have_up = False
    if (os.environ.get("OROMA_FLAPPY_POLICY_ENABLE", "1") or "1").lower() in {"1", "true", "yes", "on"}:
        try:
            from core.universal_policy import Policy  # noqa: F401
            have_up = True
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
        max_steps=max_steps,
        pass_credit_steps=pass_credit_steps,
        pass_credit_repeats=pass_credit_repeats,
        death_world_credit_steps=death_world_credit_steps,
        death_world_early_credit_steps=death_world_early_credit_steps,
        death_world_early_steps=death_world_early_steps,
        death_pipe_credit_steps=death_pipe_credit_steps,
        explore_warmup_steps=explore_warmup_steps,
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
        max_steps=max_steps,
        pass_credit_steps=pass_credit_steps,
        pass_credit_repeats=pass_credit_repeats,
        death_world_credit_steps=death_world_credit_steps,
        death_world_early_credit_steps=death_world_early_credit_steps,
        death_world_early_steps=death_world_early_steps,
        death_pipe_credit_steps=death_pipe_credit_steps,
        explore_warmup_steps=explore_warmup_steps,
    )

    ok = bool(dbw1 and dbw2)
    out = {
        "ok": bool(ok),
        "have_up": bool(have_up),
        "db_written": bool(ok),
        "seed": int(base_seed),
        "policy": policy_res,
        "explore": explore_res,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

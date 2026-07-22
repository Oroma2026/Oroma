#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Snake Daily Runner – Professional Headless Learning Loop
# Version: v3.8.1-pro-snake-reconstructable-trace-import-bootstrap
# Stand:   2026-07-13
# Autor:   Jörg + GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieser Runner führt Snake vollständig headless aus und schreibt:
#   • episodes / episodic_metrics für die Daily-Summary,
#   • SnapChains zur späteren Trace-/Trainer-Nutzung,
#   • policy_rules über core.universal_policy.Policy.learn_many().
#
# WARUM DIESE VERSION
# ───────────────────
# Die ältere Runner-Version war technisch lauffähig, aber für Lernen schwach:
#   • Default-Seed war fest (=1), wenn der Orchestrator keinen Seed übergab.
#   • learn_many()-Items enthielten reward/pos/neg/draw, aber kein outcome;
#     UniversalPolicy wertet outcome aus. Dadurch konnte Snake neutral lernen.
#   • Der State-Hash enthielt das komplette Grid als Hexdump. Das erzeugt sehr
#     viele einmalige Zustände und kaum Reuse.
#   • Die Policy wählte bei unbekannten Zuständen faktisch aus Null-Q-Werten;
#     eine produktive Safe-Food-Heuristik fehlte im Daily-Runner.
#   • Es wurde nur die letzte Aktion des Spiels gelernt; Futter-/Tod-Ereignisse
#     wurden nicht als lokale Lernsignale zurückverteilt.
#
# PROFESSIONELLES DESIGN
# ──────────────────────
# 1) Relativer Action-Raum:
#      0 = forward, 1 = left, 2 = right
#    Dadurch muss die Policy nicht absolute Richtungen lernen.
#
# 2) Abstrahierter State-Hash snake:pro_v2:
#      danger front/left/right, Food-Richtung relativ zur Blickrichtung,
#      Distanz-Bucket, Längen-Bucket, freier Raum-Bucket, Hunger-Bucket.
#    Keine exakte Position, kein Tick-Zähler, kein komplettes Board im Hash.
#
# 3) Safe-Food-Fallback:
#      Bei unbekannten Zuständen wird eine robuste Heuristik genutzt:
#      sofortige Kollision vermeiden, Futterdistanz reduzieren, freien Raum
#      bevorzugen. Das hält den Runner produktiv, auch bevor policy_rules reifen.
#
# 4) Ereignisbasierter Lernloop ohne Draw-Wand:
#      Es werden nur echte Signale gelernt:
#        • Futter gegessen  → positive Credit-Zuweisung an die letzten Schritte
#        • Tod/Kollision   → negative Credit-Zuweisung an die letzten Schritte
#        • Ziel-Länge      → positive Terminal-Credit-Zuweisung
#      outcome=0 wird nicht in policy_rules geschrieben. Dadurch entsteht keine
#      alte Pong-ähnliche Draw-Wand.
#
# 5) Direct-Step-Credit im SnapChain-Trace:
#      Die vorhandenen lokalen Credit-Fenster des Runners werden zusätzlich im
#      kompakten Trace sichtbar gemacht. Food-/Target-Credit und Death-Credit
#      erscheinen pro betroffenem Step als numerische outcome/reward/result-
#      Felder mit credit_source=direct_step_window. Das ist keine zusätzliche
#      Policy-Schreiblogik, sondern reine Trace-Evidenz für Dream/Review.
#
# 6) Observability:
#      avg_food, high_food, avg_length_end, death_wall/self, policy_used,
#      policy_fallback, policy_guarded und learn_items werden als Metriken
#      persistiert und in /games sichtbar gemacht.
#
# 7) Reconstructable Trace v1:
#      Jeder Step speichert den vollständigen konkreten Vorzustand unmittelbar
#      vor der Aktion und einen kompakten, SHA-256-gebundenen Nachzustand. Die
#      Trace-Logik liegt zentral in core.snake_reconstructable_trace, damit
#      Runner, Audit und späterer Targeted-Evidence-Runner dieselbe kanonische
#      Zustands- und Verifikationssemantik verwenden. Es entsteht keine neue
#      Tabelle und keine neue Spalte; die Episode bleibt atomar im SnapChain-
#      Blob. Historische Traces werden nicht nachträglich umgedeutet.
#
# HEADLESS / PRODUKTIONSINVARIANTEN
# ─────────────────────────────────
#   • Keine pygame-/Qt-/Wayland-/X11-Abhängigkeit.
#   • Keine DB-Schemaänderung.
#   • SQLite-Verbindungen werden geschlossen.
#   • Keine lokalen Direct-Writes außerhalb sql_manager/UniversalPolicy.
#   • Fehler in optionalen Lern-/SnapChain-Pfaden dürfen den Batch nicht killen,
#     werden aber sichtbar auf stderr gemeldet.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

# Script-/Modul-Kompatibilitaet ------------------------------------------------
#
# Beim direkten produktiven Start mit
#
#   python3 tools/snake_daily_runner.py
#
# setzt Python nur das Verzeichnis ``tools`` auf ``sys.path``. Das Projekt-Root
# ``/opt/ai/oroma`` ist dann nicht automatisch importierbar und absolute ORÓMA-
# Imports wie ``from core import sql_manager`` schlagen fehl. Der Orchestrator
# kann den Runner zwar mit vorbereitetem PYTHONPATH starten; manuelle Live-Tests
# und systemd-Aufrufe muessen jedoch ebenso robust funktionieren. Deshalb wird
# vor allen ORÓMA-Imports defensiv das aus ``__file__`` abgeleitete Projekt-Root
# eingetragen. Es werden keine Umgebungsvariablen, Arbeitsverzeichnisse oder
# produktiven Importpfade ueberschrieben.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)

from core import sql_manager
from core import execution_mode
from core.snake_reconstructable_trace import (
    CREDIT_SCHEMA as SNAKE_CREDIT_SCHEMA,
    STATE_SCHEMA as SNAKE_STATE_SCHEMA,
    TRACE_SCHEMA as SNAKE_TRACE_SCHEMA,
    canonical_sha256 as _canonical_sha256,
    compact_after_state as _compact_after_state_core,
    state_digest as _trace_state_digest_core,
    state_payload as _trace_state_payload_core,
)

# Absolute direction encoding used internally.
# 0=up, 1=right, 2=down, 3=left
DIRS: Tuple[Tuple[int, int], ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))
# Relative policy actions.
# 0=forward, 1=left, 2=right
REL_ACTIONS: Tuple[int, ...] = (0, 1, 2)


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




def _trace_state_payload(
    snake: Sequence[Tuple[int, int]],
    direction: int,
    food: Tuple[int, int],
    w: int,
    h: int,
    steps_since_food: int,
) -> Dict[str, Any]:
    return _trace_state_payload_core(
        snake, direction, food, w, h, steps_since_food
    )


def _trace_state_digest(
    snake: Sequence[Tuple[int, int]],
    direction: int,
    food: Tuple[int, int],
    w: int,
    h: int,
    steps_since_food: int,
) -> str:
    return _trace_state_digest_core(
        snake, direction, food, w, h, steps_since_food
    )


def _compact_after_state(
    snake: Sequence[Tuple[int, int]],
    direction: int,
    food: Tuple[int, int],
    w: int,
    h: int,
    steps_since_food: int,
    *,
    terminal: bool,
    event: Optional[str],
) -> Dict[str, Any]:
    return _compact_after_state_core(
        snake, direction, food, w, h, steps_since_food,
        terminal=terminal, event=event,
    )


def _bucket(value: int, cuts: Sequence[int]) -> str:
    """Return a compact stable bucket label for a non-negative integer."""
    v = int(value)
    for c in cuts:
        if v <= int(c):
            return str(int(c))
    return f"gt{int(cuts[-1])}" if cuts else str(v)


def _sign(v: int) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def _rel_to_abs(direction: int, rel_action: int) -> int:
    """Map relative action to absolute direction."""
    d = int(direction) % 4
    a = int(rel_action)
    if a == 0:      # forward
        return d
    if a == 1:      # left
        return (d - 1) % 4
    if a == 2:      # right
        return (d + 1) % 4
    return d


def _idx(x: int, y: int, w: int) -> int:
    return int(y) * int(w) + int(x)


class PolicyShim:
    """Small UniversalPolicy bridge with hit/fallback counters.

    UniversalPolicy.choose() deliberately returns a legal action even when the
    DB has no row for the state. For observability and professional fallback
    logic the runner therefore checks policy_rules first. Only states with at
    least one matching legal action are counted as policy_used.
    """

    def __init__(self, namespace: str):
        self.namespace = str(namespace or "game:snake")
        self.pol = None
        self.policy_used = 0
        self.policy_fallback = 0
        self.policy_guarded = 0
        self.last_learn_decision: Dict[str, Any] = {}
        self._has_any_rules = False
        self._rule_cache: Dict[str, bool] = {}
        if _env_int("OROMA_SNAKE_POLICY_ENABLE", 1) <= 0:
            self.pol = None
            self._has_any_rules = False
            return
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=self.namespace)
            try:
                with sql_manager.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT 1 FROM policy_rules WHERE namespace=? LIMIT 1", (self.namespace,))
                    self._has_any_rules = cur.fetchone() is not None
            except Exception:
                self._has_any_rules = True
        except Exception as e:
            self.pol = None
            print(f"[snake_daily_runner] UniversalPolicy unavailable: {e!r}", file=sys.stderr)

    def has_rule(self, state_hash: str, legal: Sequence[int]) -> bool:
        if not self.pol or not state_hash or not self._has_any_rules:
            return False
        try:
            legal_s = [str(int(a)) for a in legal]
            if not legal_s:
                return False
            cache_key = str(state_hash) + "|" + ",".join(legal_s)
            if cache_key in self._rule_cache:
                return bool(self._rule_cache[cache_key])
            qs = ",".join(["?"] * len(legal_s))
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT 1 FROM policy_rules WHERE namespace=? AND state_hash=? AND action IN ({qs}) LIMIT 1",
                    tuple([self.namespace, str(state_hash)] + legal_s),
                )
                ok = cur.fetchone() is not None
                self._rule_cache[cache_key] = bool(ok)
                if len(self._rule_cache) > 5000:
                    self._rule_cache.clear()
                return bool(ok)
        except Exception:
            return False

    def choose_policy(self, state_hash: str, legal: Sequence[int]) -> Optional[int]:
        if not self.pol or not legal:
            return None
        try:
            a = self.pol.choose(str(state_hash), [int(x) for x in legal], side="X")
            if a is None:
                return None
            ia = int(a)
            return ia if ia in [int(x) for x in legal] else None
        except Exception as e:
            print(f"[snake_daily_runner] policy choose failed: {e!r}", file=sys.stderr)
            return None

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not self.pol or not items:
            return 0
        decision = execution_mode.legacy_policy_training_allowed(
            writer_id="writer:tools.snake_daily_runner:legacy",
            namespace=self.namespace,
        )
        self.last_learn_decision = decision.to_dict()
        if not decision.allowed:
            print(
                "[snake_daily_runner] legacy policy learning blocked: "
                f"mode={decision.execution_mode} namespace={decision.namespace} reason={decision.reason} items={len(items)}",
                file=sys.stderr,
            )
            return 0
        try:
            self.pol.learn_many(items)
            return int(len(items))
        except Exception as e:
            print(f"[snake_daily_runner] policy learn_many failed: {e!r}", file=sys.stderr)
            return 0


def _next_pos(head: Tuple[int, int], abs_dir: int) -> Tuple[int, int]:
    dx, dy = DIRS[int(abs_dir) % 4]
    return int(head[0]) + dx, int(head[1]) + dy


def _will_collide(pos: Tuple[int, int], snake: Sequence[Tuple[int, int]], w: int, h: int, *, ate: bool = False) -> bool:
    x, y = int(pos[0]), int(pos[1])
    if x < 0 or y < 0 or x >= int(w) or y >= int(h):
        return True
    # If not eating, tail moves away, so it is safe to move into the old tail.
    body = list(snake) if ate else list(snake[:-1])
    return (x, y) in set(body)


def _flood_space(start: Tuple[int, int], blocked: Iterable[Tuple[int, int]], w: int, h: int, limit: int = 128) -> int:
    """Small bounded flood-fill for safe-space estimation."""
    sx, sy = int(start[0]), int(start[1])
    if sx < 0 or sy < 0 or sx >= int(w) or sy >= int(h):
        return 0
    blocked_s = set(blocked)
    if (sx, sy) in blocked_s:
        return 0
    q: Deque[Tuple[int, int]] = deque([(sx, sy)])
    seen = {(sx, sy)}
    while q and len(seen) < int(limit):
        x, y = q.popleft()
        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= int(w) or ny >= int(h):
                continue
            p = (nx, ny)
            if p in seen or p in blocked_s:
                continue
            seen.add(p)
            q.append(p)
    return int(len(seen))


def _build_state_hash(
    snake: Sequence[Tuple[int, int]],
    direction: int,
    food: Tuple[int, int],
    w: int,
    h: int,
    steps_since_food: int,
) -> Tuple[str, Dict[str, Any]]:
    """Return abstract pro_v2 state hash plus diagnostic features."""
    head = tuple(snake[0])
    hx, hy = head
    fx, fy = int(food[0]), int(food[1])

    dangers: Dict[str, int] = {}
    spaces: Dict[str, int] = {}
    for rel, name in ((0, "f"), (1, "l"), (2, "r")):
        ad = _rel_to_abs(direction, rel)
        np = _next_pos(head, ad)
        ate = np == tuple(food)
        collide = _will_collide(np, snake, w, h, ate=ate)
        dangers[name] = 1 if collide else 0
        if collide:
            spaces[name] = 0
        else:
            # New body approximation after moving into np. If eating, old tail stays.
            blocked = list(snake) if ate else list(snake[:-1])
            spaces[name] = _flood_space(np, blocked, w, h, limit=128)

    # Food vector in world coordinates.
    dx = fx - hx
    dy = fy - hy
    # Rotate vector into local coordinates: forward component and right component.
    # direction: 0 up, 1 right, 2 down, 3 left.
    if direction == 0:       # up
        fwd, right = -dy, dx
    elif direction == 1:     # right
        fwd, right = dx, dy
    elif direction == 2:     # down
        fwd, right = dy, -dx
    else:                    # left
        fwd, right = -dx, -dy

    dist = _manhattan(head, food)
    space_best = max(spaces.values()) if spaces else 0
    length = len(snake)

    feat = {
        "df": dangers.get("f", 1),
        "dl": dangers.get("l", 1),
        "dr": dangers.get("r", 1),
        "food_fwd": _sign(fwd),
        "food_right": _sign(right),
        "dist_bucket": _bucket(dist, (0, 1, 2, 4, 7, 11, 16, 24)),
        "len_bucket": _bucket(length, (3, 5, 8, 12, 18, 25)),
        "space_bucket": _bucket(space_best, (0, 4, 8, 16, 32, 64, 96)),
        "hunger_bucket": _bucket(steps_since_food, (0, 4, 8, 16, 32, 64, 128, 256)),
    }
    sh = (
        "snake:pro_v2:"
        f"d={feat['df']}{feat['dl']}{feat['dr']}"
        f":ff={feat['food_fwd']}:fr={feat['food_right']}"
        f":dist={feat['dist_bucket']}:len={feat['len_bucket']}"
        f":space={feat['space_bucket']}:hun={feat['hunger_bucket']}"
    )
    return sh, feat


def _heuristic_action(
    rng: random.Random,
    snake: Sequence[Tuple[int, int]],
    direction: int,
    food: Tuple[int, int],
    w: int,
    h: int,
) -> int:
    """Safe food-seeking fallback in relative action space."""
    head = tuple(snake[0])
    cur_dist = _manhattan(head, food)
    candidates: List[Tuple[float, int]] = []
    for rel in REL_ACTIONS:
        ad = _rel_to_abs(direction, rel)
        np = _next_pos(head, ad)
        ate = np == tuple(food)
        collide = _will_collide(np, snake, w, h, ate=ate)
        if collide:
            # Keep a very bad candidate so the function always returns something.
            candidates.append((-100000.0 + rng.random() * 0.001, int(rel)))
            continue
        new_dist = _manhattan(np, food)
        blocked = list(snake) if ate else list(snake[:-1])
        space = _flood_space(np, blocked, w, h, limit=128)
        # Professional but cheap evaluation: survival first, then food progress,
        # then space. Small turn penalty prefers forward if otherwise equal.
        score = 1000.0
        score += 80.0 if ate else 0.0
        score += 8.0 * float(cur_dist - new_dist)
        score += min(float(space), 128.0) * 0.55
        if int(rel) == 0:
            score += 1.5
        else:
            score -= 0.25
        score += rng.random() * 0.01
        candidates.append((score, int(rel)))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return int(candidates[0][1]) if candidates else 0


def _place_food(rng: random.Random, snake: Sequence[Tuple[int, int]], w: int, h: int) -> Tuple[int, int]:
    occupied = set(snake)
    for _ in range(2000):
        fx, fy = rng.randrange(w), rng.randrange(h)
        if (fx, fy) not in occupied:
            return fx, fy
    for y in range(h):
        for x in range(w):
            if (x, y) not in occupied:
                return x, y
    return 0, 0


def _add_learn_item(items: List[Dict[str, Any]], sh: str, action: int, outcome: float, reason: str) -> None:
    """Append a UniversalPolicy-compatible item; skip neutral outcomes."""
    try:
        out = float(outcome)
    except Exception:
        return
    if abs(out) <= 1e-9:
        return
    items.append({
        "state_hash": str(sh),
        "action": int(action),
        "outcome": 1.0 if out > 0 else -1.0,
        "reward": 1.0 if out > 0 else -1.0,
        "ts": int(time.time()),
        "side": "X",
        "reason": str(reason),
    })


def _apply_direct_step_credit(
    chain_steps: List[Dict[str, Any]],
    n: int,
    outcome: float,
    reason: str,
    *,
    event_type: str,
    terminal: bool = False,
) -> int:
    """Markiere lokale Runner-Credits direkt im SnapChain-Step-Trace.

    Diese Funktion schreibt nicht in die Datenbank und verändert nicht den
    bestehenden Policy-Lernpfad. Sie spiegelt nur das bereits verwendete
    lokale Credit-Fenster in den kompakten Game-Trace, damit DreamWorker später
    zwischen direkter Step-Evidenz und Root-/Episoden-Outcome unterscheiden
    kann. Mehrfache Credits auf denselben Step werden als Summe/Anzahl geführt;
    das sichtbare outcome/reward/result-Feld bleibt bewusst auf -1/0/+1
    geklemmt.
    """
    try:
        out = float(outcome)
    except Exception:
        return 0
    if not chain_steps or abs(out) <= 1e-9:
        return 0
    count = 0
    selected = chain_steps[-max(1, int(n)):]
    for idx, step in enumerate(selected):
        if not isinstance(step, dict):
            continue
        prev_sum = 0.0
        prev_count = 0
        try:
            prev_sum = float(step.get("direct_credit_sum", 0.0) or 0.0)
        except Exception:
            prev_sum = 0.0
        try:
            prev_count = int(step.get("direct_credit_count", 0) or 0)
        except Exception:
            prev_count = 0
        new_sum = prev_sum + out
        new_count = prev_count + 1
        signed = 1.0 if new_sum > 1e-9 else -1.0 if new_sum < -1e-9 else 0.0
        reasons = step.get("credit_reasons")
        if not isinstance(reasons, list):
            reasons = []
        reasons.append(str(reason))
        events = step.get("event_types")
        if not isinstance(events, list):
            events = []
        if str(event_type) not in events:
            events.append(str(event_type))
        step.update({
            "outcome": float(signed),
            "reward": float(signed),
            "result": float(signed),
            "credit_source": "direct_step_window",
            "credit_model": "snake_runner_event_window_v1",
            "direct_credit": float(out),
            "direct_credit_sum": float(new_sum),
            "direct_credit_count": int(new_count),
            "credit_reason": str(reason),
            "credit_reasons": reasons,
            "event_type": str(event_type),
            "event_types": events,
            "credit_window_index": int(idx),
            "credit_window_size": int(len(selected)),
        })
        if terminal and idx == len(selected) - 1:
            step["terminal"] = True
            step["terminal_event_type"] = str(event_type)
        count += 1
    return int(count)


def run_one_game(
    rng: random.Random,
    shim: PolicyShim,
    mode: str,
    eps: float,
    explore_moves_per_game: int,
    max_steps: int,
    target_len: int,
    learn: bool,
    namespace: str,
    *,
    episode_seed: int,
    episode_index: int,
    episode_rng_state_before: Any,
) -> Dict[str, Any]:
    w = _env_int("OROMA_SNAKE_W", 16)
    h = _env_int("OROMA_SNAKE_H", 16)
    credit_steps = max(1, _env_int("OROMA_SNAKE_CREDIT_STEPS", 12))
    death_credit_steps = max(1, _env_int("OROMA_SNAKE_DEATH_CREDIT_STEPS", 4))
    runner_config = {
        "grid_w": int(w),
        "grid_h": int(h),
        "max_steps": int(max_steps),
        "target_len": int(target_len),
        "credit_steps": int(credit_steps),
        "death_credit_steps": int(death_credit_steps),
        "action_space": "relative:0=fwd,1=left,2=right",
        "state_schema": SNAKE_STATE_SCHEMA,
        "trace_schema": SNAKE_TRACE_SCHEMA,
        "credit_schema": SNAKE_CREDIT_SCHEMA,
    }
    runner_config_digest = _canonical_sha256(runner_config)

    # Init snake length 3 in the center, facing right.
    cx, cy = w // 2, h // 2
    snake: List[Tuple[int, int]] = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
    direction = 1
    food = _place_food(rng, snake, w, h)
    initial_state_payload = _trace_state_payload(snake, direction, food, w, h, 0)
    initial_state = {
        **initial_state_payload,
        "state_digest": _trace_state_digest(snake, direction, food, w, h, 0),
    }
    episode_rng_state_digest = _canonical_sha256({
        "python_random_state": episode_rng_state_before,
    })
    episode_identity_payload = {
        "namespace": str(namespace or "game:snake"),
        "mode": str(mode),
        "episode_seed": int(episode_seed),
        "episode_index": int(episode_index),
        "episode_rng_state_digest": episode_rng_state_digest,
        "runner_config_digest": runner_config_digest,
        "initial_state_digest": initial_state["state_digest"],
    }
    episode_id = "snake_episode:" + _canonical_sha256(episode_identity_payload).split(":", 1)[1][:24]

    score_food = 0
    steps_since_food = 0
    outcome = "D"
    death_reason = "timeout"
    learn_items: List[Dict[str, Any]] = []
    recent_sa: Deque[Tuple[str, int]] = deque(maxlen=max(credit_steps, death_credit_steps, 16))
    chain_steps: List[Dict[str, Any]] = []
    policy_used_start = shim.policy_used
    policy_fallback_start = shim.policy_fallback
    policy_guarded_start = shim.policy_guarded

    for step in range(1, int(max_steps) + 1):
        sh, feat = _build_state_hash(snake, direction, food, w, h, steps_since_food)
        head = tuple(snake[0])

        # Action selection in relative action space.
        legal = list(REL_ACTIONS)
        use_random = False
        if mode == "explore" and step <= int(explore_moves_per_game):
            use_random = True
        elif mode == "explore" and rng.random() < float(eps):
            use_random = True

        if use_random:
            action = int(rng.choice(legal))
        else:
            if shim.has_rule(sh, legal):
                chosen = shim.choose_policy(sh, legal)
                if chosen is None:
                    action = _heuristic_action(rng, snake, direction, food, w, h)
                    shim.policy_fallback += 1
                else:
                    shim.policy_used += 1
                    action = int(chosen)
            else:
                action = _heuristic_action(rng, snake, direction, food, w, h)
                shim.policy_fallback += 1

        # Safety guard: if policy selected immediate death while a safe fallback
        # exists, use the safe fallback. Explore-random remains unguarded so it
        # can still generate negative examples.
        if (not use_random) and shim.has_rule(sh, legal):
            ad_try = _rel_to_abs(direction, action)
            np_try = _next_pos(head, ad_try)
            if _will_collide(np_try, snake, w, h, ate=(np_try == food)):
                safe = _heuristic_action(rng, snake, direction, food, w, h)
                ad_safe = _rel_to_abs(direction, safe)
                np_safe = _next_pos(head, ad_safe)
                if not _will_collide(np_safe, snake, w, h, ate=(np_safe == food)):
                    shim.policy_guarded += 1
                    action = int(safe)

        abs_dir = _rel_to_abs(direction, action)
        np = _next_pos(head, abs_dir)
        ate = np == tuple(food)
        recent_sa.append((sh, int(action)))

        # Reconstructable trace snapshot before applying the transition.
        # The complete ordered body is stored only for the pre-state. The
        # post-state remains compact and is verified later through its digest.
        before_payload = _trace_state_payload(
            snake, direction, food, w, h, steps_since_food
        )
        trace_step: Dict[str, Any] = {
            "t": int(step) - 1,
            "step_index": int(step) - 1,
            "state_hash": sh,
            "sh": sh,
            "a": int(action),
            "abs_dir": int(abs_dir),
            "mode": str(mode),
            "len": int(len(snake)),
            "food": [int(food[0]), int(food[1])],
            "head": [int(head[0]), int(head[1])],
            "feat": feat,
            "trace_schema": SNAKE_TRACE_SCHEMA,
            "trace_context": {
                "before": {
                    **before_payload,
                    "head": [int(head[0]), int(head[1])],
                    "length": int(len(snake)),
                    "state_digest": _trace_state_digest(
                        snake, direction, food, w, h, steps_since_food
                    ),
                },
                "transition": {
                    "relative_action": int(action),
                    "absolute_direction": int(abs_dir),
                    "next_head": [int(np[0]), int(np[1])],
                    "ate": bool(ate),
                    "applied": False,
                },
                "after": None,
            },
        }
        chain_steps.append(trace_step)

        if np[0] < 0 or np[1] < 0 or np[0] >= w or np[1] >= h:
            trace_step["trace_context"]["transition"].update({
                "collision": "wall",
                "applied": False,
            })
            trace_step["trace_context"]["after"] = _compact_after_state(
                snake, direction, food, w, h, steps_since_food,
                terminal=True, event="death_wall",
            )
            outcome = "L"
            death_reason = "wall"
            if learn:
                for idx, (lsh, la) in enumerate(list(recent_sa)[-death_credit_steps:]):
                    _add_learn_item(learn_items, lsh, la, -1.0, f"death_wall_{idx}")
            _apply_direct_step_credit(
                chain_steps, death_credit_steps, -1.0, "death_wall",
                event_type="death_wall", terminal=True,
            )
            break

        if _will_collide(np, snake, w, h, ate=ate):
            trace_step["trace_context"]["transition"].update({
                "collision": "self",
                "applied": False,
            })
            trace_step["trace_context"]["after"] = _compact_after_state(
                snake, direction, food, w, h, steps_since_food,
                terminal=True, event="death_self",
            )
            outcome = "L"
            death_reason = "self"
            if learn:
                for idx, (lsh, la) in enumerate(list(recent_sa)[-death_credit_steps:]):
                    _add_learn_item(learn_items, lsh, la, -1.0, f"death_self_{idx}")
            _apply_direct_step_credit(
                chain_steps, death_credit_steps, -1.0, "death_self",
                event_type="death_self", terminal=True,
            )
            break

        direction = abs_dir
        snake.insert(0, np)

        if ate:
            score_food += 1
            steps_since_food = 0
            if learn:
                # Positive credit over the recent path into the food. Duplicates
                # are intentionally allowed; UniversalPolicy counts evidence.
                for idx, (lsh, la) in enumerate(list(recent_sa)[-credit_steps:]):
                    _add_learn_item(learn_items, lsh, la, +1.0, f"food_credit_{idx}")
            _apply_direct_step_credit(
                chain_steps, credit_steps, +1.0, "food_eaten",
                event_type="food_eaten", terminal=False,
            )
            food = _place_food(rng, snake, w, h)
        else:
            snake.pop()
            steps_since_food += 1

        terminal_event: Optional[str] = "food_eaten" if ate else None
        terminal_flag = False
        if len(snake) >= int(target_len):
            terminal_event = "target_len"
            terminal_flag = True
            outcome = "W"
            death_reason = "target_len"
            if learn:
                for idx, (lsh, la) in enumerate(list(recent_sa)[-credit_steps:]):
                    _add_learn_item(learn_items, lsh, la, +1.0, f"target_len_{idx}")
            _apply_direct_step_credit(
                chain_steps, credit_steps, +1.0, "target_len",
                event_type="target_len", terminal=True,
            )

        trace_step["trace_context"]["transition"].update({
            "collision": None,
            "applied": True,
        })
        trace_step["trace_context"]["after"] = _compact_after_state(
            snake, direction, food, w, h, steps_since_food,
            terminal=terminal_flag, event=terminal_event,
        )
        if terminal_flag:
            break
    else:
        step = int(max_steps)
        outcome = "D"
        death_reason = "timeout"

    learned = 0
    if learn and learn_items:
        learned = shim.learn_many(learn_items)

    result = 1 if outcome == "W" else -1 if outcome == "L" else 0
    chain = {
        "schema_version": "snake_daily_runner:pro_v3_reconstructable",
        "state_schema": SNAKE_STATE_SCHEMA,
        "trace_schema": SNAKE_TRACE_SCHEMA,
        "credit_schema": SNAKE_CREDIT_SCHEMA,
        "kind": "snake_policy_trace",
        "origin": str(namespace or "game:snake"),
        "namespace": str(namespace or "game:snake"),
        "episode_id": episode_id,
        "episode_seed": int(episode_seed),
        "episode_index": int(episode_index),
        "episode_rng_state_before": episode_rng_state_before,
        "episode_rng_state_digest": episode_rng_state_digest,
        "runner_config": runner_config,
        "runner_config_digest": runner_config_digest,
        "initial_state": initial_state,
        "mode": str(mode),
        "result": int(result),
        "score_food": int(score_food),
        "steps_total": int(step),
        "steps": chain_steps,
        "meta": {
            "runner": "tools/snake_daily_runner.py",
            "source": "snake_daily_runner",
            "mode": str(mode),
            "outcome": str(outcome),
            "death_reason": str(death_reason),
            "max_steps": int(max_steps),
            "target_len": int(target_len),
            "grid_w": int(w),
            "grid_h": int(h),
            "action_space": "relative:0=fwd,1=left,2=right",
            "state_schema": SNAKE_STATE_SCHEMA,
            "trace_schema": SNAKE_TRACE_SCHEMA,
            "credit_schema": SNAKE_CREDIT_SCHEMA,
            "episode_id": episode_id,
            "episode_seed": int(episode_seed),
            "episode_index": int(episode_index),
            "episode_rng_state_digest": episode_rng_state_digest,
            "runner_config_digest": runner_config_digest,
            "learn_items": int(learned),
            "direct_credit_model": "snake_runner_event_window_v1",
            "direct_credit_fields": ["outcome", "reward", "result", "credit_source", "credit_model"],
        },
    }

    return {
        "outcome": outcome,
        "death_reason": death_reason,
        "steps": int(step),
        "food": int(score_food),
        "length_end": int(len(snake)),
        "learn_items": int(learned),
        "chain": chain,
        "policy_used": int(shim.policy_used - policy_used_start),
        "policy_fallback": int(shim.policy_fallback - policy_fallback_start),
        "policy_guarded": int(shim.policy_guarded - policy_guarded_start),
    }


def run_batch(
    rng: random.Random,
    namespace: str,
    games: int,
    mode: str,
    eps: float,
    explore_moves_per_game: int,
    learn: bool,
    source: str,
    *,
    batch_seed: int,
) -> Dict[str, Any]:
    shim = PolicyShim(namespace)
    ts_start = int(time.time())
    t0 = time.time()

    wins = losses = draws = 0
    deaths_wall = deaths_self = 0
    steps_sum = 0
    food_sum = 0
    high_food = 0
    len_sum = 0
    learn_items_sum = 0
    policy_used_sum = 0
    policy_fallback_sum = 0
    policy_guarded_sum = 0

    max_steps = _env_int("OROMA_SNAKE_MAX_STEPS", 800)
    target_len = _env_int("OROMA_SNAKE_TARGET_LEN", 20)

    chains: List[Dict[str, Any]] = []

    for game_index in range(int(games)):
        # Preserve the historical gameplay RNG stream exactly. The complete
        # Python RNG state is captured once before episode initialization so a
        # later verifier can restore it with random.Random.setstate(). The
        # batch seed remains an additional anchor, never the sole source of
        # reconstructability.
        episode_rng_state_before = rng.getstate()
        r = run_one_game(
            rng, shim, mode, eps, explore_moves_per_game,
            max_steps, target_len, learn, namespace,
            episode_seed=int(batch_seed),
            episode_index=int(game_index),
            episode_rng_state_before=episode_rng_state_before,
        )
        steps_sum += int(r.get("steps", 0) or 0)
        food_sum += int(r.get("food", 0) or 0)
        high_food = max(int(high_food), int(r.get("food", 0) or 0))
        len_sum += int(r.get("length_end", 0) or 0)
        learn_items_sum += int(r.get("learn_items", 0) or 0)
        policy_used_sum += int(r.get("policy_used", 0) or 0)
        policy_fallback_sum += int(r.get("policy_fallback", 0) or 0)
        policy_guarded_sum += int(r.get("policy_guarded", 0) or 0)
        ch = r.get("chain")
        if isinstance(ch, dict) and ch.get("steps"):
            chains.append(ch)
        if r.get("outcome") == "W":
            wins += 1
        elif r.get("outcome") == "D":
            draws += 1
        else:
            losses += 1
            if r.get("death_reason") == "wall":
                deaths_wall += 1
            elif r.get("death_reason") == "self":
                deaths_self += 1

    dur_ms = int(round((time.time() - t0) * 1000.0))
    ts_end = int(time.time())
    g = float(games or 0)

    return {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": float(dur_ms),
        "games": float(games),
        "wins_x": float(wins),
        "wins_o": float(losses),
        "draws": float(draws),
        "wins_by_length": float(wins),
        "death_wall": float(deaths_wall),
        "death_self": float(deaths_self),
        "avg_moves": float(steps_sum / g) if g else 0.0,
        "avg_steps": float(steps_sum / g) if g else 0.0,
        "avg_game_ms": float(dur_ms / g) if g else 0.0,
        "avg_food": float(food_sum / g) if g else 0.0,
        "high_food": float(high_food),
        "avg_length_end": float(len_sum / g) if g else 0.0,
        "learn_items": float(learn_items_sum),
        "policy_used": float(policy_used_sum),
        "policy_fallback": float(policy_fallback_sum),
        "policy_guarded": float(policy_guarded_sum),
        "mode": mode,
        "namespace": namespace,
        "policy_enabled": 1.0,
        "eps": float(eps),
        "explore_moves_per_game": float(explore_moves_per_game),
        "learn": bool(learn),
        "source": source,
        "label": f"snake:{mode} ({games} games)",
        "runner": "tools/snake_daily_runner.py",
        "state_schema": SNAKE_STATE_SCHEMA,
        "trace_schema": SNAKE_TRACE_SCHEMA,
        "credit_schema": SNAKE_CREDIT_SCHEMA,
        "action_space": "relative:0=fwd,1=left,2=right",
        "chains": chains,
        "chains_count": float(len(chains)),
    }


def _write_snapchains(payload: Dict[str, Any]) -> int:
    """Persist Snake traces as snapchains for downstream trainers/inspection."""
    inserted = 0
    ts_now = int(payload.get("ts_end", time.time()) or time.time())
    namespace = str(payload.get("namespace") or "game:snake")
    mode = str(payload.get("mode") or "snake")
    chains = payload.get("chains") or []
    if not isinstance(chains, list):
        return 0

    for idx, chain in enumerate(chains, start=1):
        if not isinstance(chain, dict):
            continue
        steps = chain.get("steps")
        if not isinstance(steps, list) or len(steps) < 1:
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
                "notes": f"snake_daily:{mode}:steps={int(chain.get('steps_total', len(steps)) or len(steps))}",
                "namespace": namespace,
                "source_id": None,
                "version": "snake_daily_runner:v3.8.0-reconstructable_trace_v1",
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
        metric_keys = (
            "games", "wins_x", "wins_o", "draws", "wins_by_length",
            "death_wall", "death_self", "avg_moves", "avg_steps",
            "duration_ms", "avg_game_ms", "avg_food", "high_food",
            "avg_length_end", "learn_items", "policy_used", "policy_fallback",
            "policy_guarded", "eps", "explore_moves_per_game", "policy_enabled",
        )
        for k in metric_keys:
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
    # 0/negative means dynamic seed; kept as an int for shell/iOS simplicity.
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--namespace", type=str, default=_env_str("OROMA_SNAKE_POLICY_NAMESPACE", "game:snake"))
    args = ap.parse_args()

    seed = int(args.seed) if int(args.seed or 0) > 0 else (int(time.time()) & 0xFFFFFFFF)
    rng = random.Random(seed)
    eps = _env_float("OROMA_SNAKE_EPS", 0.10)
    explore_moves = _env_int("OROMA_SNAKE_EXPLORE_MOVES_PER_GAME", 4)

    policy_res = run_batch(
        rng, args.namespace, args.policy_games, mode="policy", eps=0.0,
        explore_moves_per_game=0, learn=False, source="orchestrator",
        batch_seed=seed,
    )
    explore_res = run_batch(
        rng, args.namespace, args.explore_games, mode="explore", eps=eps,
        explore_moves_per_game=explore_moves, learn=True, source="orchestrator",
        batch_seed=seed,
    )

    ok1 = _write_episode("game:snake:policy_batch", policy_res)
    ok2 = _write_episode("game:snake:explore_batch", explore_res)
    sc1 = _write_snapchains(policy_res)
    sc2 = _write_snapchains(explore_res)

    out = {
        "ok": bool(ok1 and ok2),
        "have_up": True,
        "db_written": bool(ok1 and ok2),
        "snapchains_written": int(sc1 + sc2),
        "seed": int(seed),
        "state_schema": SNAKE_STATE_SCHEMA,
        "trace_schema": SNAKE_TRACE_SCHEMA,
        "credit_schema": SNAKE_CREDIT_SCHEMA,
        "policy_games": int(policy_res.get("games", 0) or 0),
        "explore_games": int(explore_res.get("games", 0) or 0),
        "policy_avg_moves": float(policy_res.get("avg_moves", 0.0) or 0.0),
        "explore_avg_moves": float(explore_res.get("avg_moves", 0.0) or 0.0),
        "policy_avg_food": float(policy_res.get("avg_food", 0.0) or 0.0),
        "explore_avg_food": float(explore_res.get("avg_food", 0.0) or 0.0),
        "policy_high_food": float(policy_res.get("high_food", 0.0) or 0.0),
        "explore_high_food": float(explore_res.get("high_food", 0.0) or 0.0),
        "learn_items": int(explore_res.get("learn_items", 0) or 0),
        "execution_mode": execution_mode.get_execution_mode(),
        "legacy_policy_training_allowed": bool(execution_mode.legacy_policy_training_allowed(
            writer_id="writer:tools.snake_daily_runner:legacy", namespace=args.namespace
        ).allowed),
        "policy_used": int(policy_res.get("policy_used", 0) or 0),
        "policy_fallback": int(policy_res.get("policy_fallback", 0) or 0),
        "policy_guarded": int(policy_res.get("policy_guarded", 0) or 0),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if (ok1 and ok2) else 2


if __name__ == "__main__":
    raise SystemExit(main())

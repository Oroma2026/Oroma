#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake3d_daily_runner.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   Snake3D Daily Runner – Professional Policy-/Explore-Lernloop
# Version: v0.3.1-pro-policy-loop-snapchain-direct-step-credit
# Stand:   2026-07-06
# Autor:   ORÓMA · Jörg Werner + GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Snake3D ist nach dem erfolgreichen Schablonen-Transfer jetzt ein regulärer
# professioneller ORÓMA-Spielrunner. Die State-Abstraktion ist als
# `snake3d:pro_v1` im Template-Register validiert; dieser Runner führt deshalb
# nicht mehr nur Explore-Evidenz aus, sondern echte Policy-Games plus Explore-
# Games mit eventbasiertem Lernen.
#
# PRODUKTIVER POLICY-BETRIEB
# --------------------------
# Der Runner kennt zwei Batches:
#   • policy_batch  – liest policy_rules, wählt sichere Q-Gate-Aktionen und
#                     fällt bei unsicherer/zu schwacher Evidenz auf Heuristik zurück.
#   • explore_batch – erzeugt weiterhin Abdeckung und neue Ereignis-Evidenz.
#
# Beide Batches persistieren zusätzlich trainierbare SnapChains. Damit ist
# Snake3D an den gleichen nachgelagerten Replay-/Dream-/Audit-Pfad angebunden
# wie Snake2D und die anderen professionellen Runner.
#
# Die Policy darf niemals blind aus der DB übernommen werden. Jede Policy-Aktion
# durchläuft vor der Ausführung:
#   • Kollisions-Safety-Gate gegen Wand/Self,
#   • Mindest-Sample-Gate `OROMA_SNAKE3D_POLICY_MIN_N`,
#   • Mindest-Q-Gate `OROMA_SNAKE3D_POLICY_MIN_Q`,
#   • robusten Safe-Food-/Space-Fallback.
#
# Lernen bleibt eventbasiert: Futter/Ziel-Länge positiv, Tod/Timeout negativ,
# outcome=0 wird nicht in policy_rules geschrieben. Dadurch entsteht kein Draw-
# Müll und keine Pong-ähnliche Neutralwand.
#
# STATE-SCHEMA snake3d:pro_v1
# ---------------------------
# Basis: snake:pro_v2
# Ergänzung: Z-Achse
#   danger_front/left/right/up/down
#   food_fwd/food_right/food_up
#   dist_bucket, len_bucket, space_bucket, hunger_bucket
#
# ACTION-SCHEMA relative3d_5
# --------------------------
#   0 = forward, 1 = left, 2 = right, 3 = up, 4 = down
#
# DB-/HEADLESS-INVARIANTEN
# ------------------------
# - Keine pygame-/Qt-/Wayland-/X11-Abhängigkeit.
# - policy_rules ausschließlich über core.db_writer_client.executemany().
# - SnapChains über sql_manager.insert_snapchain(), das im DBWriter-Betrieb den
#   globalen Single-Writer nutzt. Fehler sind sichtbar, killen aber nicht den
#   Spielbatch.
# - Direct-Step-Credit im SnapChain-Trace: Food-/Death-/Target-Credit-Fenster
#   werden zusätzlich als outcome/reward/result pro betroffenem Trace-Step
#   markiert. Das ist reine Evidenz für Dream/Review und verändert keine
#   bestehenden Policy-Writes. Timeout/Hunger bleibt vorerst ohne Direct-Credit,
#   weil dieses Signal schwächer und nicht terminal eindeutig ist.
# - Kein lokaler SQLite-Fallback für policy_rules.
# - Interaktive Shells exportieren OROMA_DBW_ENABLE nicht immer, obwohl der
#   DBWriter-Daemon aktiv ist; dieser Runner aktiviert den DBWriter-Client nur
#   dann automatisch, wenn kein explizites Off gesetzt ist und der Socket per
#   Ping erreichbar ist.
# - SQLite-Reads/episode writes nur über bestehende ORÓMA-Pfade; Verbindungen
#   werden geschlossen.
# - Fehler bleiben sichtbar auf stderr und im JSON-Output.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict, deque
from typing import Any, Deque, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

# mini_programs besitzt ein historisch schweres __init__.py mit Auto-Discovery.
# Für diesen headless Runner laden wir die neue Environment-Datei direkt über
# importlib, damit Snake3D keinen Mini-Program-Registry-Start auslöst.
import importlib.util
from pathlib import Path

_SNAKE3D_PATH = Path(__file__).resolve().parents[1] / "mini_programs" / "snake3d.py"
_spec = importlib.util.spec_from_file_location("oroma_snake3d_env", str(_SNAKE3D_PATH))
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load Snake3D environment from {_SNAKE3D_PATH}")
_snake3d_env = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _snake3d_env
_spec.loader.exec_module(_snake3d_env)

ACTION_NAMES = _snake3d_env.ACTION_NAMES
REL_ACTIONS_3D = _snake3d_env.REL_ACTIONS_3D
Snake3DEnv = _snake3d_env.Snake3DEnv
manhattan3 = _snake3d_env.manhattan3
sign = _snake3d_env.sign

try:
    from core import db_writer_client, sql_manager
except Exception:  # pragma: no cover - sichtbar im JSON; Runner bleibt importierbar.
    db_writer_client = None  # type: ignore
    sql_manager = None  # type: ignore

try:
    from core.state_template import find_best_match
except Exception:  # pragma: no cover
    find_best_match = None  # type: ignore

STATE_SCHEMA = "snake3d:pro_v1"
ACTION_SCHEMA = "relative3d_5"
BASE_TEMPLATE = "snake:pro_v2"


def _env_int(name: str, default: int) -> int:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _env_str(name: str, default: str) -> str:
    v = (os.environ.get(name, "") or "").strip()
    return v if v else default


def _env_boolish(name: str) -> Optional[bool]:
    """Lese ein optionales boolesches ENV-Flag.

    Rückgabe:
      - True  bei explizitem Ein/Ja,
      - False bei explizitem Aus/Nein,
      - None  bei nicht gesetztem oder unbekanntem Wert.

    Diese Unterscheidung ist für den DBWriter wichtig: Ein explizites
    ``OROMA_DBW_ENABLE=0`` bleibt verbindlich. Fehlt das Flag in einer
    manuellen Shell, darf der Runner den offiziellen DBWriter-Client aber
    aktivieren, wenn der Daemon-Socket vorhanden und erreichbar ist.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return None


def _dbw_socket_path() -> str:
    return str(
        os.environ.get("OROMA_DBW_SOCKET")
        or os.environ.get("OROMA_DBW_SOCK")
        or "/opt/ai/oroma/data/state/db_writer.sock"
    )


def _dbw_available(timeout_ms: int = 1200) -> Tuple[bool, str]:
    """Prüfe den verwalteten DBWriter-Pfad ohne SQLite-Fallback.

    Snake3D wird häufig manuell aus einer Root-/SSH-Shell gestartet. Dort ist
    der DBWriter-Daemon oft aktiv, aber ``OROMA_DBW_ENABLE`` nicht exportiert.
    Der globale Client blockiert dann alle IPC-Aufrufe. Diese Funktion behebt
    nur diese Shell-Lücke: Wenn kein explizites Off gesetzt ist, der Socket
    existiert und ein Ping erfolgreich ist, wird ``OROMA_DBW_ENABLE=1`` für
    diesen Prozess gesetzt.

    Sicherheit:
      - Kein lokaler SQLite-Schreibfallback.
      - Explizites ``OROMA_DBW_ENABLE=0`` bleibt aus.
      - Ohne erfolgreichen Ping wird nicht geschrieben.
    """
    if db_writer_client is None:
        return False, "db_writer_client_import_failed"

    explicit = _env_boolish("OROMA_DBW_ENABLE")
    if explicit is False:
        return False, "db_writer_explicitly_disabled"

    sock = _dbw_socket_path()
    if explicit is None:
        if not os.path.exists(sock):
            return False, "db_writer_socket_missing"
        os.environ["OROMA_DBW_ENABLE"] = "1"

    try:
        if not bool(getattr(db_writer_client, "enabled", lambda: False)()):
            return False, "db_writer_disabled"
        if not bool(getattr(db_writer_client, "ping", lambda timeout_ms=500: False)(timeout_ms=int(timeout_ms))):
            return False, "db_writer_ping_failed"
        return True, "ok"
    except Exception as e:
        return False, f"db_writer_ping_error:{e!r}"


def _bucket(value: int, cuts: Sequence[int]) -> str:
    v = int(value)
    for c in cuts:
        if v <= int(c):
            return str(int(c))
    return f"gt{int(cuts[-1])}" if cuts else str(v)


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if float(den) else 0.0


def _state_template_match() -> Dict[str, Any]:
    """Read-only Template-Registry-Abfrage für den Transfer-Report."""
    fallback = {
        "domain": "snake3d",
        "best_template": BASE_TEMPLATE,
        "gaps": ["z_axis"],
        "score": 0.0,
        "recommendation": "snake:pro_v2 als Basis verwenden; Z-Achse ergänzen.",
    }
    if find_best_match is None:
        fallback["registry_available"] = False
        return fallback
    try:
        res = find_best_match("navigation", ["danger", "food_direction", "z_axis"])
        if isinstance(res, dict):
            res["registry_available"] = True
            return res
    except Exception as e:
        fallback["registry_error"] = repr(e)
    fallback["registry_available"] = False
    return fallback


def _build_template_validation(mode: str = "mixed") -> Dict[str, Any]:
    match = _state_template_match()
    # Seit snake3d:pro_v1 im Register steht, darf z_axis nicht künstlich als
    # offene Lücke weitergeführt werden. Die Registry ist die Quelle der Wahrheit.
    gaps = list(match.get("gaps") or [])
    return {
        "base_template": str(match.get("best_template") or STATE_SCHEMA),
        "gaps_from_registry": gaps,
        "dimensions_added": ["danger_up", "danger_down", "food_up"],
        "action_space_extended": True,
        "actions_base": 3,
        "actions_new": 5,
        "template_fit_score": float(match.get("score") or 0.0),
        "validation_mode": str(mode or "mixed"),
        "registry_match": match,
    }


def _next_point(env: Snake3DEnv, action: int) -> Tuple[int, Tuple[int, int, int]]:
    return env.next_head(int(action))


def _candidate_info(env: Snake3DEnv, action: int) -> Dict[str, Any]:
    new_heading, np = _next_point(env, int(action))
    ate = env.food is not None and np == env.food
    collision = env.will_collide(np, ate_food=ate)
    blocked = env.occupied_after_tail_move(ate_food=ate)
    space = 0 if collision else env.flood_space(np, blocked, limit=max(128, env.size ** 3))
    dist = manhattan3(np, env.food) if env.food is not None else 0
    return {
        "action": int(action),
        "new_heading": int(new_heading),
        "next": np,
        "ate": bool(ate),
        "collision": collision,
        "space": int(space),
        "dist": int(dist),
    }


def build_state_hash(env: Snake3DEnv) -> Tuple[str, Dict[str, Any]]:
    """Baue snake3d:pro_v1 als snake:pro_v2 + Z-Achse."""
    head = env.snake[0]
    food = env.food or head
    dx = int(food[0]) - int(head[0])
    dy = int(food[1]) - int(head[1])
    dz = int(food[2]) - int(head[2])

    # Food-Vektor in lokale horizontale Koordinaten rotieren.
    h = int(env.heading) % 4
    if h == 0:       # north
        fwd, right = -dy, dx
    elif h == 1:     # east
        fwd, right = dx, dy
    elif h == 2:     # south
        fwd, right = dy, -dx
    else:            # west
        fwd, right = -dx, -dy

    candidate_by_action = {int(a): _candidate_info(env, int(a)) for a in REL_ACTIONS_3D}
    danger_front = 1 if candidate_by_action[0]["collision"] else 0
    danger_left = 1 if candidate_by_action[1]["collision"] else 0
    danger_right = 1 if candidate_by_action[2]["collision"] else 0
    danger_up = 1 if candidate_by_action[3]["collision"] else 0
    danger_down = 1 if candidate_by_action[4]["collision"] else 0
    best_space = max(int(c["space"] or 0) for c in candidate_by_action.values()) if candidate_by_action else 0
    dist = manhattan3(head, food)

    feat = {
        "danger_front": danger_front,
        "danger_left": danger_left,
        "danger_right": danger_right,
        "danger_up": danger_up,
        "danger_down": danger_down,
        "food_fwd": sign(fwd),
        "food_right": sign(right),
        "food_up": sign(dz),
        "dist_bucket": _bucket(dist, (0, 1, 2, 4, 7, 11, 16, 24)),
        "len_bucket": _bucket(len(env.snake), (3, 5, 8, 12, 18, 25)),
        "space_bucket": _bucket(best_space, (0, 4, 8, 16, 32, 64, 128, 216, 512)),
        "hunger_bucket": _bucket(env.steps_since_food, (0, 4, 8, 16, 32, 64, 128, 256, 512)),
        "candidate": candidate_by_action,
    }
    sh = (
        f"{STATE_SCHEMA}:"
        f"d={danger_front}{danger_left}{danger_right}{danger_up}{danger_down}"
        f":ff={feat['food_fwd']}:fr={feat['food_right']}:fu={feat['food_up']}"
        f":dist={feat['dist_bucket']}:len={feat['len_bucket']}"
        f":space={feat['space_bucket']}:hun={feat['hunger_bucket']}"
    )
    return sh, feat


def _heuristic_explore_action(env: Snake3DEnv, rng: random.Random, unsafe_rate: float, random_safe_rate: float) -> Tuple[int, str]:
    """Explore-only Aktion: keine Policy, aber template-orientiert und messbar.

    Der Runner soll im ersten Test nicht sofort optimieren, aber auch nicht nur
    blind sterben. Deshalb kombiniert er:
      - selten bewusst unsichere Zufallsaktionen für negative Evidenz,
      - sichere Zufallsaktionen für Abdeckung,
      - sonst eine billige Food-/Space-Heuristik als Schablonen-Basis.
    """
    infos = [_candidate_info(env, a) for a in REL_ACTIONS_3D]
    safe = [i for i in infos if not i.get("collision")]
    unsafe = [i for i in infos if i.get("collision")]

    if unsafe and rng.random() < float(unsafe_rate):
        return int(rng.choice(unsafe)["action"]), "explore_unsafe_probe"
    if safe and rng.random() < float(random_safe_rate):
        return int(rng.choice(safe)["action"]), "explore_random_safe"
    if not safe:
        return int(rng.choice(list(REL_ACTIONS_3D))), "explore_no_safe"

    cur_dist = manhattan3(env.snake[0], env.food or env.snake[0])
    scored: List[Tuple[float, int]] = []
    for info in safe:
        action = int(info["action"])
        score = 1000.0
        if bool(info.get("ate")):
            score += 180.0
        score += 12.0 * float(cur_dist - int(info.get("dist") or cur_dist))
        score += min(float(info.get("space") or 0), 512.0) * 0.35
        # Kleine Bewegungspräferenzen: forward vor seitlich, vertikal nur wenn
        # Futter/Distanz es rechtfertigt. Das verhindert sinnloses Z-Zappeln.
        if action == 0:
            score += 2.0
        elif action in (1, 2):
            score += 0.4
        else:
            score -= 0.2
        score += rng.random() * 0.01
        scored.append((score, action))
    scored.sort(key=lambda x: x[0], reverse=True)
    return int(scored[0][1]), "explore_template_heuristic"



def _read_policy_rows(namespace: str, state_hash: str, cache: MutableMapping[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Lese policy_rules für genau einen State read-only und cache sie pro Lauf.

    Dieser Runner schreibt policy_rules ausschließlich über DBWriter. Für die
    Aktionswahl sind lokale SQLite-Reads erlaubt und laufen über
    `sql_manager.get_conn()`, das im DBWriter-Strict-Mode read-only öffnet.
    """
    sh = str(state_hash or "")
    if not sh:
        return []
    if sh in cache:
        return list(cache.get(sh) or [])
    rows_out: List[Dict[str, Any]] = []
    if sql_manager is None:
        cache[sh] = rows_out
        return rows_out
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT action, q, n, pos, neg, draw
                FROM policy_rules
                WHERE namespace = ? AND state_hash = ?
                """,
                (str(namespace), sh),
            )
            for row in cur.fetchall() or []:
                try:
                    if isinstance(row, dict):
                        d = dict(row)
                    elif hasattr(row, "keys"):
                        d = {k: row[k] for k in row.keys()}
                    else:
                        d = {
                            "action": row[0], "q": row[1], "n": row[2],
                            "pos": row[3], "neg": row[4], "draw": row[5],
                        }
                    rows_out.append({
                        "action": int(d.get("action", 0) or 0),
                        "q": float(d.get("q", 0.0) or 0.0),
                        "n": int(float(d.get("n", 0) or 0)),
                        "pos": int(float(d.get("pos", 0) or 0)),
                        "neg": int(float(d.get("neg", 0) or 0)),
                        "draw": int(float(d.get("draw", 0) or 0)),
                    })
                except Exception:
                    continue
    except Exception as e:
        sys.stderr.write(f"[snake3d_daily_runner] policy read failed: {e!r}\n")
        rows_out = []
    cache[sh] = rows_out
    return list(rows_out)


def _choose_policy_action(
    env: Snake3DEnv,
    rng: random.Random,
    namespace: str,
    state_hash: str,
    feat: Mapping[str, Any],
    cache: MutableMapping[str, List[Dict[str, Any]]],
) -> Tuple[int, str, Counter[str]]:
    """Wähle eine produktive Snake3D-Policy-Aktion mit Safety- und Q-Gate.

    Rückgabe ist immer eine ausführbare Aktion. Wenn die Policy unbekannt,
    unsicher oder zu schwach belegt ist, wird sichtbar auf die bewährte
    template-orientierte Heuristik zurückgefallen.
    """
    c: Counter[str] = Counter()
    min_n = max(1, _env_int("OROMA_SNAKE3D_POLICY_MIN_N", 2))
    min_q = max(-1.0, min(1.0, _env_float("OROMA_SNAKE3D_POLICY_MIN_Q", 0.05)))
    eps = max(0.0, min(1.0, _env_float("OROMA_SNAKE3D_POLICY_EPSILON", 0.03)))
    random_safe_rate = max(0.0, min(1.0, _env_float("OROMA_SNAKE3D_POLICY_FALLBACK_RANDOM_SAFE_RATE", 0.08)))

    candidate_raw = feat.get("candidate") if isinstance(feat, Mapping) else None
    candidate: Mapping[int, Mapping[str, Any]] = candidate_raw if isinstance(candidate_raw, Mapping) else {}
    safe_actions = {
        int(a) for a in REL_ACTIONS_3D
        if not bool((candidate.get(int(a)) or {}).get("collision"))
    }
    rows = _read_policy_rows(namespace, state_hash, cache)
    if rows:
        c["policy_seen"] += 1
    else:
        c["policy_miss"] += 1
        action, reason = _heuristic_explore_action(env, rng, unsafe_rate=0.0, random_safe_rate=random_safe_rate)
        c["policy_fallback"] += 1
        return int(action), "policy_fallback_no_rule", c

    eligible: List[Tuple[float, int, int, float]] = []
    for row in rows:
        action = int(row.get("action", 0) or 0)
        q = float(row.get("q", 0.0) or 0.0)
        n = int(row.get("n", 0) or 0)
        if action not in REL_ACTIONS_3D:
            c["policy_rejected_action"] += 1
            continue
        if action not in safe_actions:
            c["policy_rejected_unsafe"] += 1
            c["policy_guarded"] += 1
            continue
        if n < min_n:
            c["policy_rejected_n"] += 1
            continue
        if q < min_q:
            c["policy_rejected_q"] += 1
            continue
        info = candidate.get(action) or {}
        space = float(info.get("space") or 0.0)
        eligible.append((q, n, action, space))

    if not eligible:
        action, reason = _heuristic_explore_action(env, rng, unsafe_rate=0.0, random_safe_rate=random_safe_rate)
        c["policy_fallback"] += 1
        return int(action), "policy_fallback_gate", c

    # Kleine Epsilon-Komponente nur innerhalb der sicher/legal akzeptierten
    # Policy-Aktionen. Sie erzeugt Reuse-Abdeckung, aber keine unsafe Probes.
    if len(eligible) > 1 and rng.random() < eps:
        q, n, action, _space = rng.choice(eligible)
        c["policy_accepted"] += 1
        c["policy_epsilon"] += 1
        return int(action), f"policy_epsilon:q={q:.3f}:n={n}", c

    eligible.sort(key=lambda x: (float(x[0]), int(x[1]), float(x[3])), reverse=True)
    q, n, action, _space = eligible[0]
    c["policy_accepted"] += 1
    return int(action), f"policy_qgate:q={q:.3f}:n={n}", c


def _add_learn_item(items: List[Dict[str, Any]], sh: str, action: int, outcome: float, reason: str, ts: int) -> None:
    out = float(outcome)
    if abs(out) <= 1e-9:
        return
    items.append({
        "state_hash": str(sh),
        "action": int(action),
        "action_canon": int(action),
        "outcome": 1.0 if out > 0 else -1.0,
        "reward": 1.0 if out > 0 else -1.0,
        "side": "X",
        "ts": int(ts),
        "meta": {"reason": str(reason), "schema": STATE_SCHEMA, "action_schema": ACTION_SCHEMA},
    })


def _credit_recent(items: List[Dict[str, Any]], recent: Deque[Tuple[str, int]], outcome: float, n: int, reason: str, ts: int) -> int:
    count = 0
    for idx, (sh, action) in enumerate(list(recent)[-max(1, int(n)):]):
        _add_learn_item(items, sh, action, outcome, f"{reason}_{idx}", ts)
        count += 1
    return count


def _apply_direct_step_credit(
    trace: List[Dict[str, Any]],
    n: int,
    outcome: float,
    reason: str,
    *,
    event_type: str,
    terminal: bool = False,
) -> int:
    """Spiegle lokale Snake3D-Credit-Fenster in den kompakten Trace.

    Die Funktion ist rein in-memory: keine DB-Writes, keine Policy-Writes, keine
    Schemaänderung. Sie ergänzt nur die Step-Datensätze, die ohnehin später als
    SnapChain-Blob persistiert werden. Dadurch kann der DreamWorker direkte
    Runner-Credits von Root-/Episoden-Credits unterscheiden und die vorhandene
    Credit-Validation sinnvoll auswerten.
    """
    try:
        out = float(outcome)
    except Exception:
        return 0
    if not trace or abs(out) <= 1e-9:
        return 0
    selected = trace[-max(1, int(n)):]
    count = 0
    for idx, step in enumerate(selected):
        if not isinstance(step, dict):
            continue
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
            "credit_model": "snake3d_runner_event_window_v1",
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
    size: int,
    max_steps: int,
    target_len: int,
    namespace: str,
    mode: str = "explore",
    policy_cache: Optional[MutableMapping[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    env = Snake3DEnv(size=size, rng=rng)
    ts = int(time.time())
    mode = "policy" if str(mode or "").lower().strip() == "policy" else "explore"
    credit_steps = max(1, _env_int("OROMA_SNAKE3D_FOOD_CREDIT_STEPS", 12))
    death_credit_steps = max(1, _env_int("OROMA_SNAKE3D_DEATH_CREDIT_STEPS", 5))
    unsafe_rate = max(0.0, min(1.0, _env_float("OROMA_SNAKE3D_EXPLORE_UNSAFE_RATE", 0.08)))
    random_safe_rate = max(0.0, min(1.0, _env_float("OROMA_SNAKE3D_EXPLORE_RANDOM_SAFE_RATE", 0.22)))
    if policy_cache is None:
        policy_cache = {}

    recent: Deque[Tuple[str, int]] = deque(maxlen=max(credit_steps, death_credit_steps, 20))
    learn_items: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []
    counters: Counter[str] = Counter()
    food_credit_items = 0
    death_credit_items = 0
    timeout_credit_items = 0
    outcome = "D"
    death_reason = "timeout"

    for step in range(1, int(max_steps) + 1):
        sh, feat = build_state_hash(env)
        counters["states"] += 1
        counters[f"mode_{mode}_steps"] += 1
        if int(feat["danger_up"]):
            counters["danger_up"] += 1
        if int(feat["danger_down"]):
            counters["danger_down"] += 1
        if int(feat["danger_up"]) or int(feat["danger_down"]):
            counters["danger_z_any"] += 1
        if int(feat["food_up"]) != 0:
            counters["food_up_signal"] += 1
        if int(feat["food_up"]) > 0:
            counters["food_above"] += 1
        if int(feat["food_up"]) < 0:
            counters["food_below"] += 1

        if mode == "policy":
            action, action_reason, policy_counts = _choose_policy_action(env, rng, namespace, sh, feat, policy_cache)
            counters.update(policy_counts)
        else:
            action, action_reason = _heuristic_explore_action(env, rng, unsafe_rate=unsafe_rate, random_safe_rate=random_safe_rate)

        if action in (3, 4):
            counters["vertical_actions"] += 1
            fu = int(feat["food_up"])
            if (action == 3 and fu > 0) or (action == 4 and fu < 0):
                counters["food_up_aligned_actions"] += 1
        counters[f"action_{action}"] += 1
        counters[f"reason_{action_reason}"] += 1

        recent.append((sh, int(action)))
        trace.append({
            "t": int(step) - 1,
            "state_hash": sh,
            "action": int(action),
            "action_name": ACTION_NAMES.get(int(action), str(action)),
            "head": list(env.snake[0]),
            "food": list(env.food) if env.food is not None else None,
            "len": int(len(env.snake)),
            "feat": {k: v for k, v in feat.items() if k != "candidate"},
            "reason": action_reason,
            "mode": mode,
        })

        result = env.step(action)
        if result.ate_food:
            food_credit_items += _credit_recent(learn_items, recent, +1.0, credit_steps, f"{mode}_food_credit", ts)
            _apply_direct_step_credit(
                trace, credit_steps, +1.0, f"{mode}_food_eaten",
                event_type="food_eaten", terminal=False,
            )
        if not result.alive:
            outcome = "L"
            death_reason = result.collision or "collision"
            death_credit_items += _credit_recent(learn_items, recent, -1.0, death_credit_steps, f"{mode}_death_{death_reason}", ts)
            _apply_direct_step_credit(
                trace, death_credit_steps, -1.0, f"{mode}_death_{death_reason}",
                event_type=f"death_{death_reason}", terminal=True,
            )
            break
        if len(env.snake) >= int(target_len):
            outcome = "W"
            death_reason = "target_len"
            food_credit_items += _credit_recent(learn_items, recent, +1.0, credit_steps, f"{mode}_target_len_credit", ts)
            _apply_direct_step_credit(
                trace, credit_steps, +1.0, f"{mode}_target_len",
                event_type="target_len", terminal=True,
            )
            break
    else:
        # Kein Draw-Lernen. Timeout wird als begrenztes Hunger-/Ineffizienzsignal
        # auf die letzten Schritte gelegt, aber nicht als neutrales Draw-Item.
        outcome = "D"
        death_reason = "timeout"
        timeout_credit_items += _credit_recent(learn_items, recent, -1.0, min(3, death_credit_steps), f"{mode}_timeout_hunger", ts)
        # Timeout/Hunger bleibt vorerst bewusst ohne direct_step_credit im Trace:
        # Das Signal ist nützlich für den bestehenden Runner-Lernpfad, aber für
        # Dream-Write-Gates weniger eindeutig als Food-/Collision-/Target-Events.

    return {
        "outcome": outcome,
        "death_reason": death_reason,
        "steps": int(env.steps),
        "food": int(env.score_food),
        "length_end": int(len(env.snake)),
        "learn_items": learn_items,
        "learn_item_count": int(len(learn_items)),
        "food_credit_items": int(food_credit_items),
        "death_credit_items": int(death_credit_items),
        "timeout_credit_items": int(timeout_credit_items),
        "counters": dict(counters),
        "trace": trace,
        "chain": _build_snapchain_payload(
            trace=trace,
            namespace=namespace,
            mode=mode,
            outcome=outcome,
            death_reason=death_reason,
            steps=int(env.steps),
            food=int(env.score_food),
            length_end=int(len(env.snake)),
            learn_items_count=int(len(learn_items)),
            size=int(size),
            max_steps=int(max_steps),
            target_len=int(target_len),
        ),
        "namespace": str(namespace),
        "mode": str(mode),
    }


def _snapchains_enabled() -> bool:
    """Default-on Gate für Snake3D-SnapChains.

    Snake3D soll wie Snake2D/Chess/Pong trainierbare SnapChains schreiben.
    Das Flag existiert nur als Betriebsbremse, falls auf dem Pi kurzfristig DB-
    Druck entsteht. Es ändert nichts am Policy-Lernen.
    """
    v = str(os.environ.get("OROMA_SNAKE3D_EMIT_SNAPCHAINS", "1") or "1").strip().lower()
    return v not in ("0", "false", "no", "n", "off")


def _chain_quality(outcome: str) -> float:
    o = str(outcome or "").upper().strip()
    if o == "W":
        return 1.0
    if o == "L":
        return -1.0
    # Timeout/Draw ist kein Policy-Draw-Lernitem, aber für SnapChain-Qualität
    # als schwaches negatives Verlaufssignal nützlich.
    return -0.25


def _build_snapchain_payload(
    *,
    trace: Sequence[Dict[str, Any]],
    namespace: str,
    mode: str,
    outcome: str,
    death_reason: str,
    steps: int,
    food: int,
    length_end: int,
    learn_items_count: int,
    size: int,
    max_steps: int,
    target_len: int,
) -> Dict[str, Any]:
    """Baue eine trainierbare Snake3D-SnapChain im Runner-nahen JSON-Format.

    Bewusst kein core.snapchain.SnapChain-Objekt: Die bestehenden Game-Runner
    persistieren kompakte JSON-Traces in `snapchains.blob`, die nachgelagerte
    Tools/Trainer schemaabhängig lesen können. Snake3D folgt diesem Muster und
    hält die rohe 3D-Entscheidungsspur vollständig im Blob, aber nicht in
    episodes.meta_json.
    """
    return {
        "kind": "game_trace",
        "game": "snake3d",
        "namespace": str(namespace or "game:snake3d"),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "base_template": BASE_TEMPLATE,
        "mode": str(mode or "explore"),
        "outcome": str(outcome or "D"),
        "death_reason": str(death_reason or "timeout"),
        "result": float(_chain_quality(str(outcome or "D"))),
        "steps_total": int(steps),
        "food": int(food),
        "length_end": int(length_end),
        "steps": list(trace or []),
        "meta": {
            "runner": "tools/snake3d_daily_runner.py",
            "source": "snake3d_daily_runner",
            "version": "v0.3.1-pro_snapchain_direct_step_credit",
            "template_extension": "spatial_navigation_z_extension",
            "mode": str(mode or "explore"),
            "outcome": str(outcome or "D"),
            "death_reason": str(death_reason or "timeout"),
            "size": int(size),
            "max_steps": int(max_steps),
            "target_len": int(target_len),
            "action_space": "relative3d_5:0=fwd,1=left,2=right,3=up,4=down",
            "state_schema": STATE_SCHEMA,
            "action_schema": ACTION_SCHEMA,
            "learn_items": int(learn_items_count),
            "direct_credit_model": "snake3d_runner_event_window_v1",
            "direct_credit_fields": ["outcome", "reward", "result", "credit_source", "credit_model"],
            "timeout_direct_credit_enabled": False,
        },
    }

def _aggregate_learn_items(items: Sequence[Dict[str, Any]], namespace: str) -> Tuple[List[List[Any]], Dict[str, int]]:
    now = int(time.time())
    agg: MutableMapping[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": now})
    totals = {"n": 0, "pos": 0, "neg": 0, "draw": 0}
    for it in items:
        sh = str(it.get("state_hash") or "")
        action = str(int(it.get("action", it.get("action_canon", 0)) or 0))
        if not sh:
            continue
        out = float(it.get("outcome", it.get("reward", 0.0)) or 0.0)
        key = (sh, action)
        row = agg[key]
        row["n"] += 1
        totals["n"] += 1
        if out > 0:
            row["pos"] += 1
            totals["pos"] += 1
        elif out < 0:
            row["neg"] += 1
            totals["neg"] += 1
        else:
            row["draw"] += 1
            totals["draw"] += 1
        try:
            row["last_ts"] = max(int(row["last_ts"]), int(it.get("ts") or now))
        except Exception:
            row["last_ts"] = now

    params: List[List[Any]] = []
    for (sh, action), row in agg.items():
        n = int(row["n"])
        pos = int(row["pos"])
        neg = int(row["neg"])
        draw = int(row["draw"])
        q = float(pos - neg) / float(max(1, n))
        params.append([str(namespace), sh, action, n, pos, neg, draw, q, int(row["last_ts"]), None])
    return params, totals


def _write_policy_rules_dbwriter(namespace: str, items: Sequence[Dict[str, Any]]) -> Tuple[bool, int, Dict[str, int], float, str]:
    t0 = time.time()
    params, totals = _aggregate_learn_items(items, namespace)
    if not params:
        return True, 0, totals, round((time.time() - t0) * 1000.0, 3), "no_items"
    dbw_ok, dbw_status = _dbw_available(timeout_ms=_env_int("OROMA_SNAKE3D_POLICY_DBW_PING_TIMEOUT_MS", 1200))
    if not dbw_ok:
        return False, 0, totals, round((time.time() - t0) * 1000.0, 3), str(dbw_status)

    sql = """INSERT INTO policy_rules
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
    chunk = max(25, _env_int("OROMA_SNAKE3D_POLICY_DBW_CHUNK", 500))
    timeout_ms = max(1000, _env_int("OROMA_SNAKE3D_POLICY_DBW_TIMEOUT_MS", 60000))
    try:
        for i in range(0, len(params), chunk):
            db_writer_client.executemany(
                sql,
                params[i:i + chunk],
                tag="snake3d.pro_v1.policy_rules.upsert",
                priority="low",
                timeout_ms=int(timeout_ms),
                db="oroma",
            )
        return True, int(totals["n"]), totals, round((time.time() - t0) * 1000.0, 3), "ok"
    except Exception as e:
        sys.stderr.write(f"[snake3d_daily_runner] DBWriter policy upsert failed: {e!r}\n")
        return False, 0, totals, round((time.time() - t0) * 1000.0, 3), repr(e)


def _template_adjustment_suggestions(counters: Mapping[str, int]) -> List[Dict[str, Any]]:
    states = max(1, int(counters.get("states", 0) or 0))
    actions = max(1, sum(int(counters.get(f"action_{a}", 0) or 0) for a in REL_ACTIONS_3D))
    danger_z_rate = _safe_div(int(counters.get("danger_z_any", 0) or 0), states)
    food_up_rate = _safe_div(int(counters.get("food_up_signal", 0) or 0), states)
    vertical_action_rate = _safe_div(int(counters.get("vertical_actions", 0) or 0), actions)
    aligned_rate = _safe_div(int(counters.get("food_up_aligned_actions", 0) or 0), max(1, int(counters.get("vertical_actions", 0) or 0)))

    suggestions: List[Dict[str, Any]] = []
    if danger_z_rate < 0.05:
        suggestions.append({
            "dimension": "danger_up/down",
            "observation": f"trigger_rate={danger_z_rate:.3f}<0.050",
            "suggestion": "Z-Gefahr war in diesem Lauf selten; Dimension behalten, aber mit größerem/vertikalerem Explore erneut prüfen.",
            "severity": "observe",
        })
    else:
        suggestions.append({
            "dimension": "danger_up/down",
            "observation": f"trigger_rate={danger_z_rate:.3f}",
            "suggestion": "Z-Gefahr ist relevant; snake:pro_v2 braucht für Snake3D danger_up/down.",
            "severity": "important",
        })

    if food_up_rate > 0.20:
        suggestions.append({
            "dimension": "food_up",
            "observation": f"signal_rate={food_up_rate:.3f}>0.200",
            "suggestion": "food_up ist wichtig; ohne Z-Food-Richtung würde die Schablone vertikale Zielinformation verlieren.",
            "severity": "important",
        })
    else:
        suggestions.append({
            "dimension": "food_up",
            "observation": f"signal_rate={food_up_rate:.3f}",
            "suggestion": "food_up war in diesem Lauf nicht dominant; Dimension weiter beobachten, nicht entfernen.",
            "severity": "observe",
        })

    suggestions.append({
        "dimension": "action_space",
        "observation": f"vertical_action_rate={vertical_action_rate:.3f}, vertical_food_aligned_rate={aligned_rate:.3f}",
        "suggestion": "Relative Aktionen müssen von 3 auf 5 erweitert bleiben: forward/left/right/up/down.",
        "severity": "structural",
    })
    return suggestions


def run_batch(
    rng: random.Random,
    namespace: str,
    games: int,
    size: int,
    max_steps: int,
    target_len: int,
    mode: str,
    policy_cache: Optional[MutableMapping[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    ts_start = int(time.time())
    t0 = time.time()
    all_items: List[Dict[str, Any]] = []
    combined: Counter[str] = Counter()
    wins = losses = draws = 0
    death_wall = death_self = death_timeout = 0
    steps_sum = food_sum = len_sum = 0
    high_food = 0
    food_credit_items = death_credit_items = timeout_credit_items = 0
    raw_traces: List[List[Dict[str, Any]]] = []
    chains: List[Dict[str, Any]] = []
    mode = "policy" if str(mode or "").lower().strip() == "policy" else "explore"
    if policy_cache is None:
        policy_cache = {}

    for _ in range(max(0, int(games))):
        res = run_one_game(
            rng,
            size=size,
            max_steps=max_steps,
            target_len=target_len,
            namespace=namespace,
            mode=mode,
            policy_cache=policy_cache,
        )
        all_items.extend(res.get("learn_items") or [])
        combined.update(Counter(res.get("counters") or {}))
        steps_sum += int(res.get("steps", 0) or 0)
        food = int(res.get("food", 0) or 0)
        food_sum += food
        high_food = max(high_food, food)
        len_sum += int(res.get("length_end", 0) or 0)
        food_credit_items += int(res.get("food_credit_items", 0) or 0)
        death_credit_items += int(res.get("death_credit_items", 0) or 0)
        timeout_credit_items += int(res.get("timeout_credit_items", 0) or 0)
        trace = res.get("trace") or []
        if isinstance(trace, list) and len(raw_traces) < 3:
            raw_traces.append(trace[:80])
        chain = res.get("chain")
        if isinstance(chain, dict) and isinstance(chain.get("steps"), list) and chain.get("steps"):
            chains.append(chain)
        if res.get("outcome") == "W":
            wins += 1
        elif res.get("outcome") == "L":
            losses += 1
            if res.get("death_reason") == "wall":
                death_wall += 1
            elif res.get("death_reason") == "self":
                death_self += 1
        else:
            draws += 1
            death_timeout += 1

    policy_ok, learned_items, totals, learn_ms, learn_status = _write_policy_rules_dbwriter(namespace, all_items)
    ts_end = int(time.time())
    sim_ms = round((time.time() - t0) * 1000.0 - float(learn_ms), 3)
    games_f = float(max(1, int(games)))

    states = max(1, int(combined.get("states", 0) or 0))
    actions = max(1, sum(int(combined.get(f"action_{a}", 0) or 0) for a in REL_ACTIONS_3D))
    template_validation = _build_template_validation(mode=mode)
    template_adjustment_suggestions = _template_adjustment_suggestions(combined)

    payload: Dict[str, Any] = {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": round((time.time() - t0) * 1000.0, 3),
        "sim_duration_ms": float(sim_ms),
        "learn_duration_ms": float(learn_ms),
        "games": int(games),
        "requested_games": int(games),
        "effective_games": int(games),
        "policy_games": int(games) if mode == "policy" else 0,
        "explore_games": int(games) if mode == "explore" else 0,
        "explore_only": bool(mode == "explore"),
        "mode": str(mode),
        "namespace": str(namespace),
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "base_template": BASE_TEMPLATE,
        "policy_enabled": 1.0 if mode == "policy" else 0.0,
        "policy_seen": int(combined.get("policy_seen", 0) or 0),
        "policy_accepted": int(combined.get("policy_accepted", 0) or 0),
        "policy_fallback": int(combined.get("policy_fallback", 0) or 0),
        "policy_miss": int(combined.get("policy_miss", 0) or 0),
        "policy_epsilon": int(combined.get("policy_epsilon", 0) or 0),
        "policy_guarded": int(combined.get("policy_guarded", 0) or 0),
        "policy_rejected_n": int(combined.get("policy_rejected_n", 0) or 0),
        "policy_rejected_q": int(combined.get("policy_rejected_q", 0) or 0),
        "policy_rejected_unsafe": int(combined.get("policy_rejected_unsafe", 0) or 0),
        "policy_rejected_action": int(combined.get("policy_rejected_action", 0) or 0),
        "policy_min_n": int(max(1, _env_int("OROMA_SNAKE3D_POLICY_MIN_N", 2))),
        "policy_min_q": float(max(-1.0, min(1.0, _env_float("OROMA_SNAKE3D_POLICY_MIN_Q", 0.05)))),
        "learn": True,
        "learn_items": int(len(all_items)),
        "learned_items": int(learned_items),
        "policy_learn_ok": bool(policy_ok),
        "policy_learn_status": str(learn_status),
        "pos_items": int(totals.get("pos", 0) or 0),
        "neg_items": int(totals.get("neg", 0) or 0),
        "draw_items": int(totals.get("draw", 0) or 0),
        "wins_x": int(wins),
        "wins_o": int(losses),
        "draws": int(draws),
        "death_wall": int(death_wall),
        "death_self": int(death_self),
        "death_timeout": int(death_timeout),
        "avg_steps": _safe_div(steps_sum, games_f),
        "avg_moves": _safe_div(steps_sum, games_f),
        "avg_food": _safe_div(food_sum, games_f),
        "high_food": int(high_food),
        "avg_length_end": _safe_div(len_sum, games_f),
        "size": int(size),
        "max_steps": int(max_steps),
        "target_len": int(target_len),
        "food_credit_items": int(food_credit_items),
        "death_credit_items": int(death_credit_items),
        "timeout_credit_items": int(timeout_credit_items),
        "state_count": int(states),
        "danger_up_count": int(combined.get("danger_up", 0) or 0),
        "danger_down_count": int(combined.get("danger_down", 0) or 0),
        "danger_z_any_count": int(combined.get("danger_z_any", 0) or 0),
        "food_up_signal_count": int(combined.get("food_up_signal", 0) or 0),
        "vertical_action_count": int(combined.get("vertical_actions", 0) or 0),
        "food_up_aligned_action_count": int(combined.get("food_up_aligned_actions", 0) or 0),
        "danger_z_rate": round(_safe_div(int(combined.get("danger_z_any", 0) or 0), states), 6),
        "food_up_signal_rate": round(_safe_div(int(combined.get("food_up_signal", 0) or 0), states), 6),
        "vertical_action_rate": round(_safe_div(int(combined.get("vertical_actions", 0) or 0), actions), 6),
        "actions": {ACTION_NAMES[a]: int(combined.get(f"action_{a}", 0) or 0) for a in REL_ACTIONS_3D},
        "template_fit_score": float(template_validation.get("template_fit_score") or 0.0),
        "template_validation": template_validation,
        "template_adjustment_suggestions": template_adjustment_suggestions,
        "source": "orchestrator",
        "label": f"snake3d:{mode} ({games} games)",
        "runner": "tools/snake3d_daily_runner.py",
        "shim": "tools/snake3d_daily_runner.pro_v2_policy_loop",
        "raw_traces": raw_traces,
        "chains": chains,
        "chains_count": int(len(chains)),
    }
    return payload


def _write_episode(kind: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[int]]:
    if sql_manager is None:
        return False, None
    try:
        meta = dict(payload)
        # Episoden-Metadaten bleiben kompakt: vollständige Traces liegen in snapchains.blob.
        meta.pop("raw_traces", None)
        meta.pop("chains", None)
        eid = sql_manager.insert_episode(
            ts_start=int(payload.get("ts_start", time.time())),
            kind=str(kind),
            source=str(payload.get("source") or "orchestrator"),
            label=str(payload.get("label") or "snake3d"),
            meta=meta,
            ts_end=int(payload.get("ts_end", time.time())),
        )
        if not eid:
            return False, None
        ts = int(payload.get("ts_end", time.time()))
        metric_keys = (
            "games", "policy_games", "explore_games", "wins_x", "wins_o", "draws",
            "avg_steps", "avg_moves", "avg_food", "high_food", "avg_length_end",
            "learn_items", "learned_items", "pos_items", "neg_items", "draw_items",
            "danger_z_rate", "food_up_signal_rate", "vertical_action_rate",
            "template_fit_score", "duration_ms", "sim_duration_ms", "learn_duration_ms",
            "policy_enabled", "policy_seen", "policy_accepted", "policy_fallback",
            "policy_miss", "policy_epsilon", "policy_guarded", "policy_rejected_n",
            "policy_rejected_q", "policy_rejected_unsafe", "policy_rejected_action",
            "policy_min_n", "policy_min_q", "death_wall", "death_self", "death_timeout",
            "food_credit_items", "death_credit_items", "timeout_credit_items",
            "chains_count", "snapchains_written",
        )
        for key in metric_keys:
            if key in payload:
                try:
                    sql_manager.insert_episodic_metric(int(eid), ts, str(key), float(payload[key]))
                except Exception:
                    pass
        return True, int(eid)
    except Exception as e:
        sys.stderr.write(f"[snake3d_daily_runner] episode write failed: {e!r}\n")
        return False, None


def _write_snapchains(payload: Dict[str, Any], *, episode_id: Optional[int] = None) -> int:
    """Persistiere Snake3D-Traces als SnapChains für Replay/Dream/Audit.

    Dieser Pfad ist absichtlich optional-fail-safe wie bei den anderen Runnern:
    Ein SnapChain-Schreibfehler wird sichtbar auf stderr gemeldet, aber ein
    erfolgreicher Spiel-/Policy-Batch wird nicht nachträglich als fehlgeschlagen
    markiert. Im DBWriter-Betrieb routet sql_manager.insert_snapchain über den
    Single-Writer; vor dem Insert wird der DBWriter per Ping geprüft. Ohne
    erreichbaren DBWriter wird sichtbar übersprungen statt lokal zu schreiben.
    """
    if not _snapchains_enabled():
        return 0
    dbw_ok, dbw_status = _dbw_available(timeout_ms=_env_int("OROMA_SNAKE3D_SNAPCHAIN_DBW_PING_TIMEOUT_MS", 1200))
    if not dbw_ok:
        sys.stderr.write(f"[snake3d_daily_runner] snapchain write skipped: DBWriter unavailable ({dbw_status})\n")
        return 0
    if sql_manager is None or not hasattr(sql_manager, "insert_snapchain"):
        sys.stderr.write("[snake3d_daily_runner] snapchain write skipped: sql_manager.insert_snapchain unavailable\n")
        return 0
    chains = payload.get("chains") or []
    if not isinstance(chains, list):
        return 0
    inserted = 0
    ts_now = int(payload.get("ts_end", time.time()) or time.time())
    namespace = str(payload.get("namespace") or "game:snake3d")
    mode = str(payload.get("mode") or "snake3d")
    for idx, chain in enumerate(chains, start=1):
        if not isinstance(chain, dict):
            continue
        steps = chain.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        try:
            blob = json.dumps(chain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            chain_id = sql_manager.insert_snapchain({
                "ts": ts_now,
                "quality": float(chain.get("result", 0.0) or 0.0),
                "blob": blob,
                "exported": 0,
                "status": "active",
                "origin": namespace,
                "gap_flag": 0,
                "notes": f"snake3d_daily:{mode}:steps={int(chain.get('steps_total', len(steps)) or len(steps))}",
                "namespace": namespace,
                "source_id": int(episode_id) if episode_id is not None else None,
                "version": "snake3d_daily_runner:v0.3.1-pro_snapchain_direct_step_credit",
                "weight": 1.0,
            })
            if chain_id:
                inserted += 1
        except Exception as e:
            sys.stderr.write(f"[snake3d_daily_runner] snapchain write failed #{idx}: {e!r}\n")
    return int(inserted)


def _write_extra_metric(episode_id: Optional[int], ts: int, key: str, value: float) -> None:
    """Best-effort Zusatzmetrik nachgelagerter Writes, sichtbar aber nicht batch-kritisch."""
    if not episode_id or sql_manager is None or not hasattr(sql_manager, "insert_episodic_metric"):
        return
    try:
        sql_manager.insert_episodic_metric(int(episode_id), int(ts), str(key), float(value))
    except Exception as e:
        sys.stderr.write(f"[snake3d_daily_runner] extra metric write failed {key}: {e!r}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Snake3D professional Policy-/Explore-Lernloop")
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_SNAKE3D_EXPLORE_GAMES", 5))
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_SNAKE3D_POLICY_GAMES", 5))
    ap.add_argument("--explore-only", action="store_true", default=False, help="Erzwingt policy-games=0; für reine Transfer-/Smoke-Tests.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--namespace", type=str, default=_env_str("OROMA_SNAKE3D_NAMESPACE", "game:snake3d"))
    ap.add_argument("--size", type=int, default=_env_int("OROMA_SNAKE3D_SIZE", 6))
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_SNAKE3D_MAX_STEPS", 350))
    ap.add_argument("--target-len", type=int, default=_env_int("OROMA_SNAKE3D_TARGET_LEN", 20))
    args = ap.parse_args()

    seed = int(args.seed) if int(args.seed or 0) > 0 else (int(time.time()) & 0xFFFFFFFF)
    rng = random.Random(seed)
    namespace = str(args.namespace or "game:snake3d")
    policy_games = max(0, int(args.policy_games or 0))
    explore_games = max(0, int(args.explore_games or 0))
    if bool(args.explore_only):
        policy_games = 0
    size = max(4, int(args.size or 6))
    max_steps = max(10, int(args.max_steps or 350))
    target_len = max(4, int(args.target_len or 20))

    policy_cache: MutableMapping[str, List[Dict[str, Any]]] = {}
    policy_res: Optional[Dict[str, Any]] = None
    explore_res: Optional[Dict[str, Any]] = None
    ok_policy = True
    ok_explore = True
    eid_policy: Optional[int] = None
    eid_explore: Optional[int] = None
    sc_policy = 0
    sc_explore = 0

    if policy_games > 0:
        policy_res = run_batch(
            rng=rng,
            namespace=namespace,
            games=policy_games,
            size=size,
            max_steps=max_steps,
            target_len=target_len,
            mode="policy",
            policy_cache=policy_cache,
        )
        ok_policy, eid_policy = _write_episode("game:snake3d:policy_batch", policy_res)
        sc_policy = _write_snapchains(policy_res, episode_id=eid_policy)
        policy_res["snapchains_written"] = int(sc_policy)
        _write_extra_metric(eid_policy, int(policy_res.get("ts_end", time.time()) or time.time()), "snapchains_written", float(sc_policy))

    if explore_games > 0:
        explore_res = run_batch(
            rng=rng,
            namespace=namespace,
            games=explore_games,
            size=size,
            max_steps=max_steps,
            target_len=target_len,
            mode="explore",
            policy_cache=policy_cache,
        )
        ok_explore, eid_explore = _write_episode("game:snake3d:explore_batch", explore_res)
        sc_explore = _write_snapchains(explore_res, episode_id=eid_explore)
        explore_res["snapchains_written"] = int(sc_explore)
        _write_extra_metric(eid_explore, int(explore_res.get("ts_end", time.time()) or time.time()), "snapchains_written", float(sc_explore))

    def _num(src: Optional[Mapping[str, Any]], key: str, default: float = 0.0) -> float:
        if not isinstance(src, Mapping):
            return float(default)
        try:
            return float(src.get(key, default) or default)
        except Exception:
            return float(default)

    out: Dict[str, Any] = {
        "ok": bool(ok_policy and ok_explore),
        "have_db": bool(sql_manager is not None),
        "have_up": bool(db_writer_client is not None),
        "db_written": bool(ok_policy and ok_explore),
        "seed": int(seed),
        "namespace": namespace,
        "state_schema": STATE_SCHEMA,
        "action_schema": ACTION_SCHEMA,
        "base_template": BASE_TEMPLATE,
        "policy_games": int(policy_games),
        "explore_games": int(explore_games),
        "explore_only": bool(policy_games == 0),
        "policy_episode_id": int(eid_policy) if eid_policy else None,
        "explore_episode_id": int(eid_explore) if eid_explore else None,
        "policy_avg_food": _num(policy_res, "avg_food"),
        "explore_avg_food": _num(explore_res, "avg_food"),
        "policy_high_food": _num(policy_res, "high_food"),
        "explore_high_food": _num(explore_res, "high_food"),
        "policy_seen": int(_num(policy_res, "policy_seen")),
        "policy_accepted": int(_num(policy_res, "policy_accepted")),
        "policy_fallback": int(_num(policy_res, "policy_fallback")),
        "policy_guarded": int(_num(policy_res, "policy_guarded")),
        "policy_rejected_n": int(_num(policy_res, "policy_rejected_n")),
        "policy_rejected_q": int(_num(policy_res, "policy_rejected_q")),
        "policy_rejected_unsafe": int(_num(policy_res, "policy_rejected_unsafe")),
        "policy_learn_ok": bool((policy_res or {}).get("policy_learn_ok", True) if isinstance(policy_res, Mapping) else True),
        "explore_learn_ok": bool((explore_res or {}).get("policy_learn_ok", True) if isinstance(explore_res, Mapping) else True),
        "learn_items": int(_num(policy_res, "learn_items") + _num(explore_res, "learn_items")),
        "learned_items": int(_num(policy_res, "learned_items") + _num(explore_res, "learned_items")),
        "draw_items": int(_num(policy_res, "draw_items") + _num(explore_res, "draw_items")),
        "policy_snapchains_written": int(sc_policy),
        "explore_snapchains_written": int(sc_explore),
        "snapchains_written": int(sc_policy + sc_explore),
        "snapchains_attempted": int(_num(policy_res, "chains_count") + _num(explore_res, "chains_count")),
        "snapchain_write_ok": bool(int(sc_policy + sc_explore) == int(_num(policy_res, "chains_count") + _num(explore_res, "chains_count"))),
        "template_fit_score": max(_num(policy_res, "template_fit_score"), _num(explore_res, "template_fit_score")),
    }
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return 0 if bool(ok_policy and ok_explore) else 2


if __name__ == "__main__":
    raise SystemExit(main())

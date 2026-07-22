#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/ctf_daily_runner.py
# Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI · Headless · SQLite-First)
# Modul:   Daily Runner – Capture The Flag Professional Policy/Explore Runner
# Version: v4.1.1-professional-ctf-pro-v2-speed-dbwriter-autoenable
# Stand:   2026-06-28
# Autor:   ORÓMA Project · KI-JWG-X1 + GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Führt `mini_programs.capture_the_flag.CTFEnv` als headless Daily-Runner aus und
# schreibt Policy- und Explore-Batches als Episoden-Telemetrie in `data/oroma.db`.
# Zusätzlich integriert dieser Runner einen produktiven UniversalPolicy-Lernpfad
# für Capture-the-Flag:
#
#   Namespace:     game:ctf
#   State-Schema:  ctf:pro_v2
#   Action-Schema: lokale/canonische Aktionen 0..4
#
# WARUM v4 / pro_v2?
# ------------------
# Der alte Runner erzeugte zwar Episoden, aber praktisch keinen belastbaren
# Policy-Lernpfad:
#   - learn_items enthielten nur `reward`, UniversalPolicy erwartet aber `outcome`.
#   - Der alte State-Hash kodierte den gesamten Float-Feature-Vektor zu fein.
#   - Das Reward-Signal enthielt den positiven Step-Reward, wodurch bei direktem
#     Lernen eine falsche Positivwand entstehen könnte.
#   - Es gab keine Policy-Reuse-KPIs und keine taktische Fallback-Heuristik.
#
# Dieser Runner lernt deshalb nur aus CTF-Ereignissen und nutzt ab v4.1 einen
# CTF-spezifisch schnellen Batch-Lernpfad:
#   - score_credit_steps default 12 statt 24, damit ein Score nicht unnötig
#     viele schwach zuordenbare Gegen-Credits erzeugt.
#   - OROMA_CTF_POLICY_DBW_CHUNK default 500 für wenige große DBWriter-Batches.
#   - Der CTF-Speedpfad schreibt policy_rules direkt über db_writer_client und
#     triggert keinen Regelarchiv-AutoExport pro Batch. Das ist bewusst, weil
#     CTF viele kurzfristige Zwei-Spieler-Credits erzeugt.
#
# Dieser Runner lernt deshalb nur aus CTF-Ereignissen:
#   - Flag aufgenommen       -> positiver Credit für den Träger, leichter negativer
#                               Credit für den Gegner.
#   - Score erzielt          -> positiver Credit für den Scorer, negativer Credit
#                               für den Gegner.
#   - Carrier verliert Flag  -> negativer Credit für den Carrier, positiver Credit
#                               für den Verteidiger.
#   - Terminal-Sieg          -> optionaler finaler Credit, kein Draw-Müll.
#
# HEADLESS / PRODUKTIONSINVARIANTEN
# ---------------------------------
# - Keine GUI, kein Qt/Wayland/X11, kein pygame.
# - Keine lokalen SQLite-Direktwrites für Policy-Lernen; Policy-Upserts laufen über
#   core.db_writer_client.executemany() in aggregierten Chunks. Dadurch bleibt
#   der globale Single-Writer/DBWriter-Pfad erhalten und der teure
#   UniversalPolicy-Import-/AutoExport-Hotpath wird vermieden.
# - Interaktive Shells exportieren OROMA_DBW_ENABLE nicht immer, obwohl der
#   DBWriter-Daemon aktiv ist. Der Runner aktiviert den Client dann nur, wenn
#   der DBWriter-Socket existiert und erreichbar ist. Es gibt weiterhin keinen
#   lokalen SQLite-Schreibfallback.
# - SQLite wird nur für read-only Policy-Lookups und episodic_metrics über die
#   vorhandenen sql_manager-Helper genutzt.
# - Keine stillen Fehler: DB-/Policy-Fehler werden über JSON-Metriken sichtbar.
# - Bestehende `game:ctf`-Daten bleiben erhalten; `ctf:pro_v2` isoliert den neuen
#   State-Pfad innerhalb desselben Namespace.
#
# USAGE
# -----
#   cd /opt/ai/oroma && PYTHONPATH=. python3 tools/ctf_daily_runner.py \
#       --policy-games 10 --explore-games 10 --seed "$(date +%s)" --namespace game:ctf
#
# EXIT-CODES
# ----------
# 0 = ok
# 2 = Runner ok, aber DB-Schreiben der Episoden fehlgeschlagen
# 3 = fataler unerwarteter Fehler
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from core import sql_manager
try:
    from core import db_writer_client
except Exception:  # pragma: no cover - production path keeps this visible via metrics
    db_writer_client = None  # type: ignore

# Direct file import: avoid importing the mini_programs package registry, which may
# auto-discover realtime games and is unnecessary for this headless daily runner.
import importlib.util
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parents[1]
_CTF_PATH = _BASE_DIR / "mini_programs" / "capture_the_flag.py"
_CTF_SPEC = importlib.util.spec_from_file_location("oroma_ctf_env_direct", str(_CTF_PATH))
if _CTF_SPEC is None or _CTF_SPEC.loader is None:
    raise RuntimeError(f"Cannot load Capture-The-Flag environment from {_CTF_PATH}")
_CTF_MOD = importlib.util.module_from_spec(_CTF_SPEC)
sys.modules[str(_CTF_SPEC.name)] = _CTF_MOD
_CTF_SPEC.loader.exec_module(_CTF_MOD)
CTFConfig = _CTF_MOD.CTFConfig
CTFEnv = _CTF_MOD.CTFEnv
CTFState = _CTF_MOD.CTFState

# Canonical/local action schema for side-symmetric learning:
#   0 stay, 1 up, 2 down, 3 backward, 4 forward
# Raw env action schema:
#   0 stay, 1 up, 2 down, 3 left, 4 right
CANON_LEGAL = [0, 1, 2, 3, 4]


def _now() -> int:
    return int(time.time())


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return float(default)


def _env_boolish(name: str) -> Optional[bool]:
    """Return an explicit boolean ENV value, or None when unset/unknown.

    The DBWriter client gates all IPC calls behind OROMA_DBW_ENABLE. In systemd
    ORÓMA runs this flag through the orchestrator, but manual one-shot runner
    tests often inherit a shell where the daemon is active while the flag is not
    exported. This helper keeps explicit off-values authoritative and lets the
    CTF runner enable the client only for the common "socket exists" case.
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


def _dbw_available(timeout_ms: int = 1200) -> bool:
    """Return True when the managed DBWriter client can write.

    No local SQLite fallback is used. If the current shell forgot to export
    OROMA_DBW_ENABLE but the daemon socket is present, the runner enables the
    official client flag and verifies the connection with ping().
    """
    if db_writer_client is None:
        return False

    explicit = _env_boolish("OROMA_DBW_ENABLE")
    if explicit is False:
        return False

    sock = _dbw_socket_path()
    if explicit is None and os.path.exists(sock):
        os.environ["OROMA_DBW_ENABLE"] = "1"

    try:
        if not bool(getattr(db_writer_client, "enabled", lambda: False)()):
            return False
        return bool(getattr(db_writer_client, "ping", lambda timeout_ms=500: False)(timeout_ms=int(timeout_ms)))
    except Exception:
        return False


def _sign(side: str) -> int:
    return 1 if side == "A" else -1


def _side_label(side: str) -> str:
    return "X" if side == "A" else "O"


def _raw_to_canon(raw_action: int, side: str) -> int:
    """Map env action to side-local action. Horizontal movement is mirrored for B."""
    a = int(raw_action)
    if a in (0, 1, 2):
        return a
    if side == "A":
        return 4 if a == 4 else 3
    return 4 if a == 3 else 3


def _canon_to_raw(canon_action: int, side: str) -> int:
    """Map side-local action to env action."""
    a = int(canon_action)
    if a in (0, 1, 2):
        return a
    if side == "A":
        return 4 if a == 4 else 3
    return 3 if a == 4 else 4


def _move_raw(env: CTFEnv, pos: Tuple[int, int], raw_action: int) -> Tuple[int, int]:
    dx, dy = CTFEnv.ACTIONS.get(int(raw_action), (0, 0))
    return env._clip(pos[0] + dx, pos[1] + dy)


def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def _bucket(v: int | float, cuts: Iterable[int | float]) -> str:
    x = float(v)
    for i, c in enumerate(cuts):
        if x <= float(c):
            return str(i)
    return str(len(list(cuts)))


def _dist_bucket(d: int) -> str:
    if d <= 0:
        return "0"
    if d == 1:
        return "1"
    if d <= 3:
        return "2-3"
    if d <= 6:
        return "4-6"
    return "7+"


def _score_diff_bucket(diff: int) -> str:
    if diff <= -2:
        return "-2"
    if diff == -1:
        return "-1"
    if diff == 0:
        return "0"
    if diff == 1:
        return "+1"
    return "+2"


def _agent_view(env: CTFEnv, side: str) -> Dict[str, Any]:
    """Return a side-local tactical view for A or B."""
    if side == "A":
        pos = env.A_pos
        opp = env.B_pos
        base = env.A_base
        flag = env.A_flag
        my_carry = bool(env.A_carry)
        opp_carry = bool(env.B_carry)
        score = int(env.A_score)
        opp_score = int(env.B_score)
    else:
        pos = env.B_pos
        opp = env.A_pos
        base = env.B_base
        flag = env.B_flag
        my_carry = bool(env.B_carry)
        opp_carry = bool(env.A_carry)
        score = int(env.B_score)
        opp_score = int(env.A_score)

    if my_carry:
        phase = "return"
        target = base
    elif opp_carry:
        phase = "defend"
        target = opp
    else:
        phase = "raid"
        target = flag

    sx = _sign(side)
    dx = (target[0] - pos[0]) * sx
    dy = target[1] - pos[1]
    odx = (opp[0] - pos[0]) * sx
    ody = opp[1] - pos[1]

    return {
        "side": side,
        "pos": pos,
        "opp": opp,
        "base": base,
        "flag": flag,
        "target": target,
        "phase": phase,
        "my_carry": my_carry,
        "opp_carry": opp_carry,
        "score": score,
        "opp_score": opp_score,
        "score_diff": score - opp_score,
        "target_dist": _manhattan(pos, target),
        "opp_dist": _manhattan(pos, opp),
        "dx": dx,
        "dy": dy,
        "odx": odx,
        "ody": ody,
    }


def _dir_bucket(dx: int, dy: int) -> str:
    if abs(dx) >= abs(dy):
        if dx > 0:
            return "fwd"
        if dx < 0:
            return "back"
    if dy < 0:
        return "up"
    if dy > 0:
        return "down"
    return "here"


def _state_hash(env: CTFEnv, side: str) -> str:
    """Coarse, side-symmetric tactical state hash for CTF Policy reuse."""
    v = _agent_view(env, side)
    W = max(1, int(env.cfg.width))
    H = max(1, int(env.cfg.height))
    local_x = (int(v["pos"][0]) - int(v["base"][0])) * _sign(side)
    local_y = int(v["pos"][1]) - (H // 2)
    # Coarse map lanes; exact coordinates intentionally omitted.
    if local_x <= 1:
        lane_x = "home"
    elif local_x >= W - 3:
        lane_x = "enemy"
    else:
        lane_x = "mid"
    lane_y = "mid" if abs(local_y) <= 1 else ("top" if local_y < 0 else "bot")
    return "|".join([
        "ctf:pro_v2",
        f"ph={v['phase']}",
        f"td={_dist_bucket(int(v['target_dist']))}",
        f"tdir={_dir_bucket(int(v['dx']), int(v['dy']))}",
        f"od={_dist_bucket(int(v['opp_dist']))}",
        f"odir={_dir_bucket(int(v['odx']), int(v['ody']))}",
        f"score={_score_diff_bucket(int(v['score_diff']))}",
        f"x={lane_x}",
        f"y={lane_y}",
    ])




def _fallback_target(env: CTFEnv, side: str, view: Dict[str, Any]) -> Tuple[int, int]:
    """Side-aware route target for the fallback policy.

    The raw environment has both flags/bases on the center row. If both agents
    greedily run straight, they collide around midfield forever. The professional
    fallback therefore uses deterministic side-specific lanes for raid/return and
    only enters the center row near the flag/base. This keeps the game tactical
    without adding obstacles or changing the environment rules.
    """
    W = int(env.cfg.width)
    H = int(env.cfg.height)
    center = H // 2
    lane_y = max(0, min(H - 1, center - 2 if side == "A" else center + 2))
    pos = view["pos"]
    base = view["base"]
    flag = view["flag"]
    phase = str(view["phase"])

    if phase == "defend":
        return tuple(view["opp"])  # tag carrier

    if phase == "raid":
        # Move to own lane first, cross on lane, then enter the flag square.
        if pos[0] != flag[0]:
            if pos[1] != lane_y and abs(pos[0] - base[0]) <= max(2, W // 3):
                return (pos[0], lane_y)
            return (flag[0], lane_y)
        return tuple(flag)

    # return phase: carrier comes home on the same lane and only enters base at end.
    if pos[0] != base[0]:
        return (base[0], lane_y)
    return tuple(base)

def _heuristic_canon_action(env: CTFEnv, side: str, rng: Optional[random.Random] = None) -> int:
    """Professional side-local fallback: raid, return, or defend depending on phase."""
    v = _agent_view(env, side)
    pos = v["pos"]
    opp = v["opp"]
    target = _fallback_target(env, side, v)
    phase = str(v["phase"])

    best: List[Tuple[float, int]] = []
    for canon in CANON_LEGAL:
        raw = _canon_to_raw(canon, side)
        nxt = _move_raw(env, pos, raw)
        d_target = _manhattan(nxt, target)
        d_opp = _manhattan(nxt, opp)
        score = -float(d_target) * 10.0

        # In return phase the carrier must avoid tags aggressively.
        if phase == "return":
            if nxt == opp:
                score -= 1000.0
            score += float(d_opp) * 3.0
            if canon == 0:
                score -= 2.0
        # In defend phase collisions with opponent are useful because they tag carriers.
        elif phase == "defend":
            if nxt == opp:
                score += 500.0
            score -= float(d_opp) * 4.0
            if canon == 0:
                score -= 4.0
        # In raid phase move toward flag but avoid stepping onto defender unless at target.
        else:
            if nxt == opp:
                score -= 12.0
            if nxt == target:
                score += 50.0
            if canon == 0:
                score -= 3.0

        # Prefer horizontal progress over vertical dithering when scores tie.
        if canon == 4:
            score += 0.25
        if canon == 3:
            score -= 0.25
        best.append((score, int(canon)))

    best.sort(key=lambda t: (t[0], -t[1]), reverse=True)
    if len(best) > 1 and rng is not None and abs(best[0][0] - best[1][0]) < 1e-9:
        return int(rng.choice([best[0][1], best[1][1]]))
    return int(best[0][1])


class PolicyShim:
    """Read-gated UniversalPolicy wrapper for CTF.

    The write path uses Policy.learn_many(). The choose path performs explicit
    read-only inspection of policy_rules so that the runner can expose robust
    policy_seen/accepted/rejected metrics and avoid letting weak rules override
    the tactical fallback.
    """

    def __init__(self, namespace: str):
        self.namespace = str(namespace or "game:ctf")
        # Lazy Policy import: choose() uses read-only SQL lookups and does not need
        # core.universal_policy. This avoids expensive schema checks in pure
        # policy/evaluation runs. The UniversalPolicy object is imported only when
        # learn_many() actually has non-empty items to write.
        self.have_up = True
        self.pol = None
        self.accept_q_min = _env_float("OROMA_CTF_POLICY_ACCEPT_Q_MIN", 0.20)
        self.accept_min_n = _env_int("OROMA_CTF_POLICY_ACCEPT_MIN_N", 2)
        self.dbw_chunk = max(25, _env_int("OROMA_CTF_POLICY_DBW_CHUNK", 500))
        self.auto_export_enabled = bool(str(os.environ.get("OROMA_CTF_UP_AUTO_EXPORT", "0")).strip().lower() in ("1", "true", "yes", "on"))
        self.stats: Dict[str, int] = {
            "policy_seen": 0,
            "policy_accepted": 0,
            "policy_fallback": 0,
            "policy_rejected_n": 0,
            "policy_rejected_q": 0,
            "policy_rejected_unsafe": 0,
        }

    def _best_row(self, sh: str) -> Optional[Dict[str, Any]]:
        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                    (self.namespace, str(sh)),
                )
                rows = cur.fetchall() or []
        except Exception:
            rows = []
        best: Optional[Dict[str, Any]] = None
        for row in rows:
            try:
                a = str(row["action"] if hasattr(row, "keys") else row[0])
                if a not in {str(x) for x in CANON_LEGAL}:
                    continue
                q = float(row["q"] if hasattr(row, "keys") else row[1])
                n = int(row["n"] if hasattr(row, "keys") else row[2])
            except Exception:
                continue
            cand = {"action": int(a), "q": q, "n": n}
            if best is None or (q, n) > (float(best.get("q", 0.0)), int(best.get("n", 0))):
                best = cand
        return best

    def choose(self, env: CTFEnv, side: str, rng: random.Random) -> int:
        fallback = _heuristic_canon_action(env, side, rng)
        if not self.have_up:
            self.stats["policy_fallback"] += 1
            return int(fallback)

        sh = _state_hash(env, side)
        row = self._best_row(sh)
        if row is None:
            self.stats["policy_fallback"] += 1
            return int(fallback)

        self.stats["policy_seen"] += 1
        n = int(row.get("n", 0))
        q = float(row.get("q", 0.0))
        cand = int(row.get("action", fallback))
        if n < int(self.accept_min_n):
            self.stats["policy_rejected_n"] += 1
            self.stats["policy_fallback"] += 1
            return int(fallback)
        if q < float(self.accept_q_min):
            self.stats["policy_rejected_q"] += 1
            self.stats["policy_fallback"] += 1
            return int(fallback)
        if not _candidate_safe(env, side, cand, fallback):
            self.stats["policy_rejected_unsafe"] += 1
            self.stats["policy_fallback"] += 1
            return int(fallback)

        self.stats["policy_accepted"] += 1
        return int(cand)

    def learn_many(self, items: List[Dict[str, Any]]) -> Tuple[bool, int]:
        """Write CTF learning items through the managed DBWriter path.

        CTF emits many credit items per short match. Importing the full
        UniversalPolicy object can trigger optional adapter/AutoExport work, and
        small DBWriter chunks make this expensive on Raspberry Pi. This runner
        therefore performs the same aggregated policy_rules UPSERT as
        UniversalPolicy, but sends it directly to the global DBWriter daemon in
        large chunks.

        Important invariants:
        - No local SQLite direct-write fallback is introduced.
        - If DBWriter is disabled/unavailable, the failure remains visible via
          policy_learn_ok=false and learned_items=0.
        - state_hash/action/outcome semantics stay identical to UniversalPolicy.
        """
        if not items:
            return False, 0
        if not _dbw_available(timeout_ms=_env_int("OROMA_CTF_POLICY_DBW_PING_TIMEOUT_MS", 1200)):
            self.have_up = False
            return False, 0

        now = int(time.time())
        aggregated: Dict[Tuple[str, str], Dict[str, int]] = {}
        for it in items:
            try:
                sh = str(it.get("state_hash", "")).strip()
                if not sh:
                    continue
                action = str(it.get("action_canon", it.get("action", "0")))
                out_f = float(it.get("outcome", 0.0))
                outcome = 1 if out_f > 1e-9 else -1 if out_f < -1e-9 else 0
                ts = int(it.get("ts") or now)
            except Exception:
                continue
            key = (sh, action)
            agg = aggregated.get(key)
            if agg is None:
                agg = {"n": 0, "pos": 0, "neg": 0, "draw": 0, "last_ts": 0}
                aggregated[key] = agg
            agg["n"] += 1
            if outcome > 0:
                agg["pos"] += 1
            elif outcome < 0:
                agg["neg"] += 1
            else:
                agg["draw"] += 1
            if ts > int(agg["last_ts"]):
                agg["last_ts"] = ts

        if not aggregated:
            return False, 0

        params_list: List[List[Any]] = []
        for (sh, action), agg in aggregated.items():
            n = int(agg["n"])
            pos_inc = int(agg["pos"])
            neg_inc = int(agg["neg"])
            draw_inc = int(agg["draw"])
            q_seed = float(pos_inc - neg_inc) / float(n) if n > 0 else 0.0
            params_list.append([
                self.namespace,
                sh,
                action,
                n,
                pos_inc,
                neg_inc,
                draw_inc,
                q_seed,
                int(agg["last_ts"] or now),
                None,
            ])

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
        try:
            timeout_ms = _env_int("OROMA_CTF_POLICY_DBW_TIMEOUT_MS", 60000)
            chunk = max(25, int(self.dbw_chunk))
            for i in range(0, len(params_list), chunk):
                db_writer_client.executemany(
                    upsert_sql,
                    params_list[i:i + chunk],
                    tag="ctf_daily_runner.policy_rules.learn_many",
                    priority="low",
                    timeout_ms=int(timeout_ms),
                    db="oroma",
                )
            self.have_up = True
            return True, int(len(items))
        except Exception:
            self.have_up = False
            return False, 0


def _candidate_safe(env: CTFEnv, side: str, cand: int, fallback: int) -> bool:
    """Minimal CTF safety guard: do not let policy make carrier walk into a tag."""
    if int(cand) == int(fallback):
        return True
    v = _agent_view(env, side)
    raw = _canon_to_raw(int(cand), side)
    nxt = _move_raw(env, v["pos"], raw)
    # Carrier walking onto opponent loses the flag. Never allow policy to worsen this.
    if bool(v["my_carry"]) and nxt == v["opp"]:
        return False
    # During raid, avoid direct collision unless the fallback would also collide.
    fb_raw = _canon_to_raw(int(fallback), side)
    fb_nxt = _move_raw(env, v["pos"], fb_raw)
    if str(v["phase"]) == "raid" and nxt == v["opp"] and fb_nxt != v["opp"]:
        return False
    return True


def _add_recent(hist: Deque[Tuple[str, int, str]], sh: str, action: int, side_label: str, maxlen: int) -> None:
    hist.append((str(sh), int(action), str(side_label)))
    while len(hist) > int(maxlen):
        hist.popleft()


def _credit_recent(items: List[Dict[str, Any]],
                   hist: Deque[Tuple[str, int, str]],
                   outcome: float,
                   steps: int,
                   ts: int,
                   reason: str) -> int:
    if abs(float(outcome)) <= 1e-9:
        return 0
    n = 0
    for sh, action, side_label in list(hist)[-int(max(1, steps)):]:
        items.append({
            "state_hash": sh,
            "action": int(action),
            "action_canon": int(action),
            "outcome": float(outcome),
            "side": side_label,
            "ts": int(ts),
            "meta": {"reason": reason},
        })
        n += 1
    return n


def run_batch(rng: random.Random,
              games: int,
              mode: str,
              namespace: str,
              eps: float,
              explore_moves_per_game: int,
              learn: bool,
              max_steps: int,
              source: str) -> Dict[str, Any]:
    shim = PolicyShim(namespace=namespace)
    wins_x = wins_o = draws = 0
    steps_sum = 0
    scoreA_sum = 0.0
    scoreB_sum = 0.0
    scores_A = scores_B = 0
    carries_A = carries_B = 0
    drops_A = drops_B = 0
    tags_A = tags_B = 0
    max_step_games = 0
    score_limit_games = 0
    learn_items_all: List[Dict[str, Any]] = []
    score_credit_items = 0
    carry_credit_items = 0
    tag_credit_items = 0
    terminal_credit_items = 0
    explore_injected = 0

    score_credit_steps = _env_int("OROMA_CTF_SCORE_CREDIT_STEPS", 12)
    carry_credit_steps = _env_int("OROMA_CTF_CARRY_CREDIT_STEPS", 6)
    tag_credit_steps = _env_int("OROMA_CTF_TAG_CREDIT_STEPS", 8)
    terminal_credit_steps = _env_int("OROMA_CTF_TERMINAL_CREDIT_STEPS", 8)
    hist_len = max(score_credit_steps, carry_credit_steps, tag_credit_steps, terminal_credit_steps, 32)

    t0 = time.time()

    for _gi in range(int(games)):
        env = CTFEnv(CTFConfig(max_steps=max_steps, seed=rng.randrange(1, 2**31 - 1)))
        env.reset(seed=rng.randrange(1, 2**31 - 1))
        done = False
        st_last: Optional[CTFState] = None
        hist_A: Deque[Tuple[str, int, str]] = deque(maxlen=hist_len)
        hist_B: Deque[Tuple[str, int, str]] = deque(maxlen=hist_len)
        explore_budget = int(explore_moves_per_game) if mode == "explore" else 0

        while not done:
            shA = _state_hash(env, "A")
            shB = _state_hash(env, "B")

            if mode == "explore" and explore_budget > 0 and rng.random() < float(eps):
                cA = int(rng.choice(CANON_LEGAL))
                explore_budget -= 1
                explore_injected += 1
            else:
                cA = shim.choose(env, "A", rng)

            if mode == "explore" and explore_budget > 0 and rng.random() < float(eps):
                cB = int(rng.choice(CANON_LEGAL))
                explore_budget -= 1
                explore_injected += 1
            else:
                cB = shim.choose(env, "B", rng)

            _add_recent(hist_A, shA, cA, "X", hist_len)
            _add_recent(hist_B, shB, cB, "O", hist_len)

            rawA = _canon_to_raw(cA, "A")
            rawB = _canon_to_raw(cB, "B")
            st, _rewards, done, info = env.step({"A": int(rawA), "B": int(rawB)})
            st_last = st

            events = list(info.get("event") or [])
            do_credit = bool(learn and mode == "explore")
            ts = int(time.time())
            if "A_carry_start" in events:
                carries_A += 1
                if do_credit:
                    carry_credit_items += _credit_recent(learn_items_all, hist_A, +0.35, carry_credit_steps, ts, "carry_start")
                    carry_credit_items += _credit_recent(learn_items_all, hist_B, -0.15, max(1, carry_credit_steps // 2), ts, "opp_carry_start")
            if "B_carry_start" in events:
                carries_B += 1
                if do_credit:
                    carry_credit_items += _credit_recent(learn_items_all, hist_B, +0.35, carry_credit_steps, ts, "carry_start")
                    carry_credit_items += _credit_recent(learn_items_all, hist_A, -0.15, max(1, carry_credit_steps // 2), ts, "opp_carry_start")
            if "A_score" in events:
                scores_A += 1
                if do_credit:
                    score_credit_items += _credit_recent(learn_items_all, hist_A, +1.0, score_credit_steps, ts, "score")
                    score_credit_items += _credit_recent(learn_items_all, hist_B, -0.65, score_credit_steps, ts, "opp_score")
            if "B_score" in events:
                scores_B += 1
                if do_credit:
                    score_credit_items += _credit_recent(learn_items_all, hist_B, +1.0, score_credit_steps, ts, "score")
                    score_credit_items += _credit_recent(learn_items_all, hist_A, -0.65, score_credit_steps, ts, "opp_score")
            if "A_drop_flag" in events:
                drops_A += 1
                if do_credit:
                    tag_credit_items += _credit_recent(learn_items_all, hist_A, -1.0, tag_credit_steps, ts, "drop_flag")
                    tag_credit_items += _credit_recent(learn_items_all, hist_B, +0.45, tag_credit_steps, ts, "forced_drop")
            if "B_drop_flag" in events:
                drops_B += 1
                if do_credit:
                    tag_credit_items += _credit_recent(learn_items_all, hist_B, -1.0, tag_credit_steps, ts, "drop_flag")
                    tag_credit_items += _credit_recent(learn_items_all, hist_A, +0.45, tag_credit_steps, ts, "forced_drop")
            if "A_tagged" in events:
                tags_A += 1
            if "B_tagged" in events:
                tags_B += 1

        if st_last is None:
            st_last = env._mk_state()

        reason = ""
        try:
            reason = str(info.get("reason", ""))  # type: ignore[name-defined]
        except Exception:
            reason = ""
        if reason == "score_limit":
            score_limit_games += 1
        elif reason == "max_steps":
            max_step_games += 1

        sA = int(getattr(st_last, "A_score", 0))
        sB = int(getattr(st_last, "B_score", 0))
        scoreA_sum += sA
        scoreB_sum += sB
        steps = int(getattr(st_last, "steps", max_steps))
        steps_sum += steps

        if sA > sB:
            wins_x += 1
            if learn and mode == "explore":
                terminal_credit_items += _credit_recent(learn_items_all, hist_A, +0.50, terminal_credit_steps, int(time.time()), "terminal_win")
                terminal_credit_items += _credit_recent(learn_items_all, hist_B, -0.50, terminal_credit_steps, int(time.time()), "terminal_loss")
        elif sB > sA:
            wins_o += 1
            if learn and mode == "explore":
                terminal_credit_items += _credit_recent(learn_items_all, hist_B, +0.50, terminal_credit_steps, int(time.time()), "terminal_win")
                terminal_credit_items += _credit_recent(learn_items_all, hist_A, -0.50, terminal_credit_steps, int(time.time()), "terminal_loss")
        else:
            draws += 1

    sim_duration_ms = (time.time() - t0) * 1000.0
    policy_learn_ok = False
    learned = 0
    learn_duration_ms = 0.0
    if learn and learn_items_all:
        lt0 = time.time()
        policy_learn_ok, learned = shim.learn_many(learn_items_all)
        learn_duration_ms = (time.time() - lt0) * 1000.0

    t1 = time.time()
    duration_ms = int(round((t1 - t0) * 1000.0))
    games_d = float(max(1, int(games)))
    avg_steps = float(steps_sum) / games_d
    avg_scoreA = float(scoreA_sum) / games_d
    avg_scoreB = float(scoreB_sum) / games_d

    return {
        "ts_start": int(t0),
        "ts_end": int(t1),
        "duration_ms": duration_ms,
        "games": int(games),
        "wins_x": int(wins_x),
        "wins_o": int(wins_o),
        "draws": int(draws),
        "avg_steps": avg_steps,
        "avg_score_A": avg_scoreA,
        "avg_score_B": avg_scoreB,
        "scores_A": int(scores_A),
        "scores_B": int(scores_B),
        "carries_A": int(carries_A),
        "carries_B": int(carries_B),
        "drops_A": int(drops_A),
        "drops_B": int(drops_B),
        "tags_A": int(tags_A),
        "tags_B": int(tags_B),
        "score_limit_games": int(score_limit_games),
        "max_step_games": int(max_step_games),
        "mode": mode,
        "namespace": namespace,
        "state_schema": "ctf:pro_v2",
        "action_schema": "local_5",
        "policy_enabled": 1.0 if shim.have_up else 0.0,
        "eps": float(eps) if mode == "explore" else 0.0,
        "explore_moves_per_game": int(explore_moves_per_game) if mode == "explore" else 0,
        "explore_injected": int(explore_injected),
        "learn": bool(learn) if mode == "explore" else False,
        "learn_items": int(len(learn_items_all)),
        "learned_items": int(learned),
        "policy_learn_ok": bool(policy_learn_ok),
        "score_credit_items": int(score_credit_items),
        "carry_credit_items": int(carry_credit_items),
        "tag_credit_items": int(tag_credit_items),
        "terminal_credit_items": int(terminal_credit_items),
        "sim_duration_ms": float(sim_duration_ms),
        "learn_duration_ms": float(learn_duration_ms),
        "score_credit_steps": int(score_credit_steps),
        "carry_credit_steps": int(carry_credit_steps),
        "tag_credit_steps": int(tag_credit_steps),
        "terminal_credit_steps": int(terminal_credit_steps),
        "policy_auto_export": 1.0 if shim.auto_export_enabled else 0.0,
        "policy_dbw_chunk": int(shim.dbw_chunk),
        "max_steps": int(max_steps),
        "source": source,
        "label": f"ctf:{mode} ({int(games)} games)",
        "runner": "tools/ctf_daily_runner.py",
        "shim": "tools/ctf_daily_runner.PolicyShim.pro_v2",
        "policy_accept_q_min": float(shim.accept_q_min),
        "policy_accept_min_n": int(shim.accept_min_n),
        **{k: int(v) for k, v in shim.stats.items()},
    }


def _db_write_episode(kind: str, res: Dict[str, Any]) -> Optional[int]:
    ts_start = int(res.get("ts_start", _now()))
    ts_end = int(res.get("ts_end", ts_start))
    meta = {k: res.get(k) for k in (
        "mode", "namespace", "policy_enabled", "eps", "explore_moves_per_game",
        "learn", "max_steps", "runner", "shim", "source", "state_schema", "action_schema",
        "policy_accept_q_min", "policy_accept_min_n", "policy_auto_export",
        "policy_dbw_chunk", "score_credit_steps", "carry_credit_steps",
        "tag_credit_steps", "terminal_credit_steps",
    )}
    eid = sql_manager.insert_episode(
        ts_start=ts_start,
        kind=kind,
        source=str(res.get("source", "orchestrator")),
        label=str(res.get("label", kind)),
        meta=meta,
        ts_end=ts_end,
    )
    if not eid:
        return None

    metric_keys = [
        "games", "wins_x", "wins_o", "draws", "avg_steps", "avg_score_A", "avg_score_B",
        "scores_A", "scores_B", "carries_A", "carries_B", "drops_A", "drops_B",
        "tags_A", "tags_B", "score_limit_games", "max_step_games", "duration_ms",
        "policy_enabled", "eps", "explore_moves_per_game", "explore_injected", "max_steps",
        "learn_items", "learned_items", "policy_learn_ok", "score_credit_items",
        "carry_credit_items", "tag_credit_items", "terminal_credit_items", "sim_duration_ms",
        "learn_duration_ms", "score_credit_steps", "carry_credit_steps", "tag_credit_steps",
        "terminal_credit_steps", "policy_auto_export", "policy_dbw_chunk",
        "policy_seen", "policy_accepted", "policy_fallback", "policy_rejected_n",
        "policy_rejected_q", "policy_rejected_unsafe", "policy_accept_q_min", "policy_accept_min_n",
    ]
    for k in metric_keys:
        try:
            v = res.get(k, 0.0)
            if isinstance(v, bool):
                v = 1.0 if v else 0.0
            sql_manager.insert_episodic_metric(int(eid), ts_end, str(k), float(v or 0.0))
        except Exception:
            continue
    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=int(os.environ.get("OROMA_CTF_POLICY_GAMES", "100")))
    ap.add_argument("--explore-games", type=int, default=int(os.environ.get("OROMA_CTF_EXPLORE_GAMES", "100")))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--namespace", type=str, default="game:ctf")
    ap.add_argument("--eps", type=float, default=float(os.environ.get("OROMA_CTF_EPS", "0.10")))
    ap.add_argument("--explore-moves", type=int, default=int(os.environ.get("OROMA_CTF_EXPLORE_MOVES", "4")))
    ap.add_argument("--max-steps", type=int, default=int(os.environ.get("OROMA_CTF_MAX_STEPS", "400")))
    ap.add_argument("--source", type=str, default="orchestrator")
    args = ap.parse_args()

    rng = random.Random(args.seed or (int(time.time()) & 0xFFFFFFFF))

    policy_res = run_batch(
        rng=rng,
        games=max(0, int(args.policy_games)),
        mode="policy",
        namespace=args.namespace,
        eps=0.0,
        explore_moves_per_game=0,
        learn=False,
        max_steps=max(1, int(args.max_steps)),
        source=args.source,
    )
    explore_res = run_batch(
        rng=rng,
        games=max(0, int(args.explore_games)),
        mode="explore",
        namespace=args.namespace,
        eps=float(_clamp(args.eps, 0.0, 1.0)),
        explore_moves_per_game=max(0, int(args.explore_moves)),
        learn=True,
        max_steps=max(1, int(args.max_steps)),
        source=args.source,
    )

    db_written = True
    try:
        eid1 = _db_write_episode("game:ctf:policy_batch", policy_res)
        eid2 = _db_write_episode("game:ctf:explore_batch", explore_res)
        if not eid1 or not eid2:
            db_written = False
    except Exception as e:
        db_written = False
        print(f"[ctf_daily_runner] DB write failed: {e!r}", file=sys.stderr)

    out = {
        "ok": bool(db_written),
        "have_up": bool(policy_res.get("policy_enabled") or explore_res.get("policy_enabled")),
        "db_written": bool(db_written),
        "seed": int(args.seed or 0),
        "policy": policy_res,
        "explore": explore_res,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if db_written else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"[ctf_daily_runner] FATAL: {e!r}", file=sys.stderr)
        raise SystemExit(3)

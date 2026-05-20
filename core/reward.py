#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/reward.py
# Projekt: ORÓMA
# Version: v3.7 (Reward-Core + optionale Empathie-Δ-Brücke + Thread-Attach)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Zentrales Reward-System:
#     - Verwaltung von Belohnungssignalen (Games, Wrapper, Episoden, Tools)
#     - Aggregation/Statistik (aggregator)
#     - Speicherung in SQL-Logs (rewards_log)
#
# Neu in v3.7 (optional, idempotent)
# ─────────────────────────────────
#   • log_empathy_positive_shift(window_sec=600, min_delta=0.2, reward_value=0.02)
#     → Wenn Empathie-Score im Fenster um ≥ min_delta steigt, logge kleines Reward (source='empathy').
#   • Thread-Kontext automatisch an raw/info anhängen (roter Faden), steuerbar via:
#       OROMA_REWARD_ATTACH_THREAD=true|false (Default: true)
#
# Schema-Hinweise
# ───────────────
#   rewards_log     (ensure_schema)
#   empathy_snaps   (bereitgestellt via sql_manager; genutzt in log_empathy_positive_shift)
# =============================================================================

from __future__ import annotations

import json
import math
import os
import sys
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, List, Iterator
from core.log_guard import log_suppressed
import logging

# ----------------------------------------------------------------------------
# Projektbasis
# ----------------------------------------------------------------------------
BASE = os.environ.get("OROMA_BASE_DIR") or os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

_SQL_OK = True
try:
    from core import sql_manager  # type: ignore
    sql_manager.ensure_schema()
except Exception:
    _SQL_OK = False
    sql_manager = None  # type: ignore


@contextmanager
def _conn_cm() -> Iterator[object]:
    """Robuster DB-Contextmanager.

    Manche Runtime-Stände/Teilimporte hatten zeitweise kein `sql_manager.conn_cm`.
    Damit reward.log() niemals wegen eines fehlenden Attributes ausfällt,
    nutzen wir eine Fallback-Variante auf `get_conn()`.
    """

    if sql_manager is None:
        raise RuntimeError("sql_manager unavailable")

    cm = getattr(sql_manager, "conn_cm", None)
    if callable(cm):
        with cm() as conn:
            yield conn
        return

    get_conn = getattr(sql_manager, "get_conn", None)
    if callable(get_conn):
        conn = get_conn()
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return

    raise RuntimeError("sql_manager has no conn_cm/get_conn")

# ----------------------------------------------------------------------------
# Schema-Cache (verhindert wiederholte DDL-Locks im Hot-Path)
# ----------------------------------------------------------------------------
_REWARD_SCHEMA_DONE = False

# Thread-Attach (roter Faden) optional
_ATTACH_THREAD = (os.environ.get("OROMA_REWARD_ATTACH_THREAD", "true").lower()
                  not in ("0", "false", "no", "off"))
try:
    from core import roter_faden  # type: ignore
    def _attach_thread_info(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not _ATTACH_THREAD:
            return dict(info or {})
        try:
            return roter_faden.attach(dict(info or {}))  # robust
        except Exception:
            return dict(info or {})
except Exception:
    def _attach_thread_info(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return dict(info or {})

# ----------------------------------------------------------------------------
# Schema-Erweiterung (Index auf episode_id)
# ----------------------------------------------------------------------------
def ensure_schema() -> None:
    """Erweitert rewards_log um fehlende Indizes (idempotent).

    WICHTIG (Lock-Reduktion):
      - reward.log() kann sehr häufig aufgerufen werden.
      - DDL-Statements (auch CREATE INDEX IF NOT EXISTS) können kurze Write-Locks erzeugen.
      - daher einmal pro Prozess cachen (_REWARD_SCHEMA_DONE).
    """
    global _REWARD_SCHEMA_DONE
    if _REWARD_SCHEMA_DONE:
        return
    if not _SQL_OK:
        return
    try:
        with sql_manager.conn_cm() as conn:  # type: ignore
            cur = conn.cursor()
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rewards_src_time ON rewards_log(source, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rewards_src_step ON rewards_log(source, step)")
            conn.commit()
        _REWARD_SCHEMA_DONE = True
    except Exception:
        # Best-effort: Reward-Logging darf die Engine nicht blockieren
        return

class RewardLogger:
    """Schreibt Reward-Ereignisse in rewards_log und aktualisiert Metrics."""

    def __init__(self):
        ensure_schema()

    def log(
        self,
        source: str,
        step: int,
        reward: float,
        raw: Optional[Dict[str, Any]] = None,
        episode_id: Optional[int] = None,
        tag: Optional[str] = None,
        ts: Optional[int] = None,
    ) -> int:
        if not _SQL_OK:
            print(f"[reward] SQL nicht verfügbar – skip log {source}:{step}={reward}")
            return -1

        ensure_schema()

        # Controlled retry bei SQLite-Locks ("database is locked") – wichtig bei parallelen Writer-Jobs
        try:
            retry_sec = int(getattr(sql_manager, "_env_int", lambda n, d: d)("OROMA_DB_LOCK_RETRY_SEC", 60))  # type: ignore
        except Exception:
            retry_sec = 60

        # Thread-Kontext ggf. anhängen
        raw_attached = _attach_thread_info(raw)

        # Stufe C (DBWriter): Reward-Log ist Telemetrie/Diagnose → best-effort via globalem Single-Writer.
        # Wenn DBWriter aktiv ist, vermeiden wir lokale Writer-Kollisionen komplett.
        try:
            _dbw_enabled = getattr(sql_manager, "_dbw_enabled", None)
            _dbw = getattr(sql_manager, "_dbw", None)
            if callable(_dbw_enabled) and _dbw_enabled() and _dbw is not None:
                # NOTE: Historische Schema-Varianten nutzen i.d.R. die Spalte `raw` (TEXT).
                # Einige Patch-Stände hatten kurzzeitig `raw_json` im Code, ohne dass die DB-Spalte
                # überall migriert wurde. Um Schema-Mismatches ("no column named raw_json") zu vermeiden,
                # schreiben wir im DBWriter-Pfad konsistent nach `raw`.
                sql_stmt = """
                        INSERT INTO rewards_log(created_at, source, step, reward, raw, episode_id, tag)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    int(ts or int(time.time())),
                    str(source),
                    int(step),
                    float(reward),
                    json.dumps(raw_attached, ensure_ascii=False, separators=(",", ":")) if raw_attached is not None else None,
                    int(episode_id) if episode_id is not None else None,
                    str(tag) if tag is not None else None,
                )
                getattr(_dbw, "exec_write")(sql_stmt, params=list(params), tag="reward.log", priority="low", timeout_ms=int(os.getenv("OROMA_DBW_REWARD_TIMEOUT_MS","2000")), db="oroma")
                return 0
        except Exception as e:
            # Im Single-Writer-Modus niemals auf lokalen SQLite-Write zurückfallen.
            # Sichtbar loggen und überspringen, damit keine Writer-Kollisionen entstehen.
            try:
                if callable(getattr(sql_manager, "_dbw_enabled", None)) and getattr(sql_manager, "_dbw_enabled")():
                    print(f"[reward] DBWriter write failed – skip (no local fallback): {e}")
                    return -1
            except Exception:
                pass


        try:
            if callable(getattr(sql_manager, "_dbw_enabled", None)) and getattr(sql_manager, "_dbw_enabled")():
                return -1
        except Exception:
            pass

        def _do_once() -> int:
            wl = getattr(sql_manager, "writer_lock", None)  # type: ignore
            cm = wl("reward.log") if callable(wl) else nullcontext()
            with cm:
                with _conn_cm() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO rewards_log(created_at, source, episode_id, step, reward, raw, tag)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(ts or time.time()),
                            str(source),
                            None if episode_id is None else int(episode_id),
                            int(step),
                            float(reward),
                            json.dumps(raw_attached, ensure_ascii=False, separators=(",", ":")),
                            str(tag) if tag else None,
                        ),
                    )
                    conn.commit()
                    return int(cur.lastrowid)


        try:
            rid = int(getattr(sql_manager, "_run_with_lock_retry")(_do_once, int(retry_sec)))  # type: ignore
        except Exception as e:
            # Reward-Logging ist Best-Effort – bei dauerhaften Locks nicht crashen
            print(f"[reward] log() Fehler: {e}")
            return -1

        try:
            sql_manager.insert_metric(f"reward_{source}", float(reward))  # type: ignore
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.reward.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

        return rid

# ----------------------------------------------------------------------------
# Aggregator
# ----------------------------------------------------------------------------
class RewardAggregator:
    """Abfrage-/Statistik-Helfer für Rewards."""

    def __init__(self):
        ensure_schema()

    def _fetch_last(self, source: str, limit: int = 1000) -> List[float]:
        if not _SQL_OK:
            return []
        # Wichtig: Connection immer schließen, sonst können Leser-Transaktionen
        # Writer-Commits blockieren (klassische SQLite "database is locked" Falle).
        # Wir verwenden daher den Context-Manager (_conn_cm), der commit/rollback
        # und close garantiert.
        with _conn_cm() as conn:
            cur = conn.cursor()
            cur.execute(
            "SELECT reward FROM rewards_log WHERE source=? ORDER BY created_at DESC LIMIT ?",
            (str(source), int(limit))
            )
            rows = cur.fetchall() or []
            return [float(r["reward"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]) for r in rows]

    def window_sum(self, source: str, last_n: int = 1000) -> float:
        vals = self._fetch_last(source, last_n)
        return float(sum(vals))

    def window_mean(self, source: str, last_n: int = 1000) -> float:
        vals = self._fetch_last(source, last_n)
        return float(sum(vals) / max(1, len(vals)))

    def ema(self, source: str, span: int = 100) -> float:
        vals = self._fetch_last(source, span * 4)
        if not vals:
            return 0.0
        alpha = 2.0 / (float(span) + 1.0)
        ema = vals[-1]
        for v in reversed(vals[:-1]):
            ema = alpha * v + (1.0 - alpha) * ema
        return float(ema)

# ----------------------------------------------------------------------------
# Beispiel-Rewards
# ----------------------------------------------------------------------------
def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0

def compute_snake_reward(state_prev: Dict[str, Any], state_next: Dict[str, Any], done: bool) -> RewardSignal:
    comp: Dict[str, float] = {}
    r = 0.0
    if done:
        comp["death"] = -1.0; r += comp["death"]
    sp, sn = int(state_prev.get("score", 0)), int(state_next.get("score", 0))
    if sn > sp:
        comp["food"] = +1.0 * (sn - sp); r += comp["food"]
    try:
        hp, hn, food = state_prev["snake"][0], state_next["snake"][0], state_next["food"]
        d_prev = math.hypot(hp[0]-food[0], hp[1]-food[1])
        d_next = math.hypot(hn[0]-food[0], hn[1]-food[1])
        comp["towards_food" if d_next < d_prev else "away_food"] = 0.05 if d_next < d_prev else -0.02
        r += comp.get("towards_food", comp.get("away_food", 0))
    except Exception: pass
    comp["alive_step"] = +0.005; r += comp["alive_step"]
    return RewardSignal(total=r, components=comp, clip=(-1.0, +1.5)).clipped()

def compute_pong_reward(state_prev: Dict[str, Any], state_next: Dict[str, Any], done: bool) -> RewardSignal:
    comp: Dict[str, float] = {}; r = 0.0
    s_prev, s_next = state_prev.get("score", [0, 0]), state_next.get("score", [0, 0])
    if s_next[0] > s_prev[0]: comp["our_point"] = +1.0; r += comp["our_point"]
    if s_next[1] > s_prev[1]: comp["opp_point"] = -1.0; r += comp["opp_point"]
    try:
        bx, by, p1 = float(state_next["bx"]), float(state_next["by"]), float(state_next["p1"])
        comp["track"] = +0.01 * (1.0 - _sigmoid(abs(by - p1) / 50.0)); r += comp["track"]
    except Exception: pass
    if done: comp["done_penalty"] = -0.2; r += comp["done_penalty"]
    return RewardSignal(total=r, components=comp, clip=(-1.0, +1.2)).clipped()

def compute_vision_reward(metrics: Dict[str, Any]) -> RewardSignal:
    comp: Dict[str, float] = {}; r = 0.0
    comp["fps"] = +0.01 * min(2.0, max(0.0, metrics.get("fps", 0.0) / max(1.0, metrics.get("target_fps", 20.0))))
    r += comp["fps"]
    lat = float(metrics.get("lat_ms", 80.0))
    comp["lat"] = +0.01 * (1.0 - min(1.5, max(0.0, (lat - 20.0) / 120.0))); r += comp["lat"]
    if metrics.get("has_dets"): comp["dets"] = +0.02; r += comp["dets"]
    if metrics.get("throttling"): comp["thermal"] = -0.1; r += comp["thermal"]
    return RewardSignal(total=r, components=comp, clip=(-0.2, +0.2)).clipped()

# ----------------------------------------------------------------------------
# Legacy-API (einfacher Zugriff)
# ----------------------------------------------------------------------------
def log(source: str, value: float, info: Optional[Dict[str, Any]] = None,
        step: int = 0, episode_id: Optional[int] = None, tag: Optional[str] = None, ts: Optional[int] = None) -> int:
    try:
        logger = RewardLogger()
        # hier wird der Thread-Kontext angehängt
        info_attached = _attach_thread_info(info)
        return logger.log(source=str(source), step=int(step), reward=float(value),
                          raw=info_attached, episode_id=episode_id, tag=tag, ts=ts)
    except Exception as e:
        print(f"[reward] log() Fehler: {e}")
        return -1

def log_reward_generic(source: str, value: float, desc: str = "") -> int:
    return log(source=source, value=value, info={"desc": desc})

# ----------------------------------------------------------------------------
# Optionale Empathie-Brücke (positiver Stimmungswechsel)
# ----------------------------------------------------------------------------
def _row_to_tuple(row) -> Tuple[int, float]:
    """emp_row → (ts, score) robust."""
    if isinstance(row, dict) or hasattr(row, "keys"):
        return int(row.get("ts", 0)), float(row.get("score", 0.0))
    return int(row[0]), float(row[2])

def log_empathy_positive_shift(window_sec: int = 600, min_delta: float = 0.2, reward_value: float = 0.02) -> Optional[int]:
    """
    Prüft, ob sich der Empathie-Score innerhalb des Fensters um ≥ min_delta verbessert hat.
    Falls ja, loggt ein kleines Reward unter source='empathy'.
    Rückgabe: Reward-Row-ID oder None, wenn kein Logging stattfand/Fehler.
    """
    if not _SQL_OK or not sql_manager:
        return None
    try:
        rows = sql_manager.fetch_last_empathy(6)  # letzte Einträge holen (kleines Fenster)
        if not rows:
            return None
        now = time.time()
        within: List[Tuple[int, float]] = []
        for r in rows:
            ts, score = _row_to_tuple(r)
            if now - ts <= window_sec:
                within.append((ts, score))
        if len(within) < 2:
            return None
        within.sort(key=lambda x: x[0])
        base_ts, base_sc = within[0]
        last_ts, last_sc = within[-1]
        delta = last_sc - base_sc  # Verbesserung > 0
        if delta >= float(min_delta):
            return log("empathy", value=float(reward_value),
                       info={"delta": float(delta), "base": float(base_sc), "last": float(last_sc),
                             "window_sec": int(window_sec)}, tag="social-positive")
    except Exception as e:
        print(f"[reward] empathy shift check skipped: {e}")
    return None

# ----------------------------------------------------------------------------
# Selftest
# ----------------------------------------------------------------------------
def _selftest() -> None:
    print("[reward] selftest…")
    logger = RewardLogger(); agg = RewardAggregator()
    s0, s1 = {"snake":[[5,5]],"food":[8,5],"score":0}, {"snake":[[6,5]],"food":[8,5],"score":0}
    print(" snake rid:", log("snake", +0.1, {"demo":"ok","s0":s0,"s1":s1}))
    print(" pong  rid:", log("pong",  -0.1, {"demo":"ok"}))
    print(" vision rid:", log("wrapper:vision", +0.02, {"fps":18.5}))
    try:
        rid = log_empathy_positive_shift()
        print(" empathy shift rid:", rid)
    except Exception:
        print(" empathy shift skipped")
    print(" snake mean(50):", agg.window_mean("snake", 50))
    print(" pong  ema(100):", agg.ema("pong", 100))
    print(" vision sum(50):", agg.window_sum("wrapper:vision", 50))
    print("[reward] OK ✅")

if __name__ == "__main__":
    _selftest()

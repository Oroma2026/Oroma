#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/quota.py
# Projekt: ORÓMA
# Version: v3.7 (Daily-Quota – atomar, DB-basiert)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Tagesbudgets (z. B. "Schach max. 3/Tag") lokal durchsetzen.
#   - can_play_today(game, limit=None) -> (ok, remaining)
#   - claim_play(game, limit=None) -> (ok, remaining)
#   - remaining_today(game, limit=None) -> int
#
# Technik
# ───────
#   - Nutzt metrics-Tabelle: key = f"quota:{game}:play"
#   - Tagesfenster = lokale Mitternacht (start_of_day_ts)
#   - Atomare Claim-Transaktion (BEGIN IMMEDIATE) gegen Race-Conditions
#
# ENV
# ───
#   OROMA_CHESS_DAILY_LIMIT=3   (Default 3)
#   OROMA_QUOTA_DEFAULT=9999     (Fallback, falls kein spezifisches Limit)
# =============================================================================

from __future__ import annotations
import os, time, sqlite3
from typing import Tuple, Optional

import logging

from core.log_guard import log_suppressed
try:
    from core import sql_manager
except Exception:
    sql_manager = None  # graceful

def _start_of_day_ts() -> int:
    lt = time.localtime()
    sod = time.struct_time((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    return int(time.mktime(sod))

def _limit_for(game: str, fallback: int = 9999) -> int:
    if game == "chess":
        v = os.environ.get("OROMA_CHESS_DAILY_LIMIT", "")
        if str(v).strip():
            try: return max(0, int(v))
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="quota.limit_for.chess_env",
                    msg="quota: invalid OROMA_CHESS_DAILY_LIMIT; using default",
                    exc=e,
                    level=logging.WARNING,
                    interval_s=3600,
                )
        return 3
    v = os.environ.get("OROMA_QUOTA_DEFAULT", "")
    try:
        return max(0, int(v))
    except Exception:
        return fallback

def _conn() -> Optional[sqlite3.Connection]:
    if not sql_manager: return None
    try:
        return sql_manager.get_conn()
    except Exception:
        return None

def _count_today(game: str) -> int:
    c = _conn()
    if not c: return 0
    with c:
        cur = c.cursor()
        cur.execute(
            "SELECT COUNT(*) AS n FROM metrics WHERE key=? AND ts>=?",
            (f"quota:{game}:play", _start_of_day_ts())
        )
        row = cur.fetchone()
        return int(row["n"] if row and "n" in row else 0)

def remaining_today(game: str, limit: Optional[int] = None) -> int:
    lim = _limit_for(game) if limit is None else int(limit)
    used = _count_today(game)
    return max(0, lim - used)

def can_play_today(game: str, limit: Optional[int] = None) -> Tuple[bool, int]:
    r = remaining_today(game, limit)
    return (r > 0, r)

def claim_play(game: str, limit: Optional[int] = None) -> Tuple[bool, int]:
    """
    Atomarer Claim: prüft Limit & bucht 1 Nutzung, wenn möglich.
    Rückgabe: (ok, remaining_nach_claim)
    """
    c = _conn()
    if not c:
        # Fallback: kein DB → nicht blockieren
        return True, max(0, (_limit_for(game) if limit is None else int(limit)) - 1)

    lim = _limit_for(game) if limit is None else int(limit)
    sod = _start_of_day_ts()
    try:
        c.execute("BEGIN IMMEDIATE")
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM metrics WHERE key=? AND ts>=?",
                    (f"quota:{game}:play", sod))
        used = int(cur.fetchone()["n"])
        if used >= lim:
            c.execute("ROLLBACK")
            return False, 0
        # claim verbuchen
        cur.execute("INSERT INTO metrics (key, ts, value) VALUES (?, ?, ?)",
                    (f"quota:{game}:play", int(time.time()), 1.0))
        c.commit()
        remaining = max(0, lim - (used + 1))
        return True, remaining
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception as e2:
            log_suppressed(
                logging.getLogger(__name__),
                key="quota.rollback",
                msg="quota: rollback failed",
                exc=e2,
                level=logging.WARNING,
                interval_s=3600,
            )
        return False, max(0, lim - _count_today(game))

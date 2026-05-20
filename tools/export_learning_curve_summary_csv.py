#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/export_learning_curve_summary_csv.py
# Projekt: ORÓMA
# Version: v3.7.3+curve-r1 – Lernkurven-Summary (Tag/Woche/Monat) → CSV
# Stand:   2025-12-17
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Exportiert eine kompakte Lernkurven-Zusammenfassung als CSV, ohne DB-Schema
#   zu verändern.
#
#   Enthaltene Aggregationen (aus snapchains):
#     • Tag:   COUNT(chains), AVG(quality), MAX(quality)
#     • Woche: SUM(chains), MAX(q_max), MAX(q_avg)  (q_avg_max)
#     • Monat: SUM(chains), MAX(q_max), MAX(q_avg)  (q_avg_max)
#
#   Optional (wenn vorhanden):
#     • coverage_log: MAX(coverage) pro Periode (coverage_max)
#     • rewards_log:  MAX(reward) pro Periode (reward_max)
#
# Robustheit / Sampling
# ─────────────────────
#   Viele Projekt-ZIPs sind auf 1000 Zeilen pro Tabelle gekürzt.
#   Das Skript ankert daher am latest_ts in der DB (statt „now“) und exportiert
#   ein Fenster relativ zu dieser Aktivität.
#
# Ausgabe
# ───────
#   CSV mit Zeilen für day/week/month:
#     granularity, period, chains, q_max, q_avg_or_q_avg_max, coverage_max, reward_max
#
# Nutzung
# ───────
#   PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/tools/export_learning_curve_summary_csv.py \
#     --db  /opt/ai/oroma/data/oroma.db \
#     --out /opt/ai/oroma/data/learning_curve_summary.csv \
#     --days 365
#
# =============================================================================

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import time
from typing import Dict, Any, Tuple
import logging
from core.log_guard import log_suppressed

DEFAULT_DB = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
DEFAULT_OUT = "/opt/ai/oroma/data/learning_curve_summary.csv"


def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _max_ts(cur: sqlite3.Cursor, table: str, col: str) -> int:
    try:
        cur.execute(f"SELECT MAX({col}) FROM {table}")
        v = cur.fetchone()[0]
        return _safe_int(v, 0)
    except Exception:
        return 0


def latest_ts(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    latest = 0
    for (t, c) in [
        ("snapchains", "ts"),
        ("coverage_log", "ts"),
        ("rewards_log", "created_at"),
        ("empathy_snaps", "ts"),
        ("kpi_snapshots", "ts"),
        ("metrics", "ts"),
    ]:
        latest = max(latest, _max_ts(cur, t, c))
    return latest


def _period_keys_from_day(day_yyyy_mm_dd: str) -> Tuple[str, str]:
    # day: YYYY-MM-DD (UTC)
    y, m, d = [int(x) for x in day_yyyy_mm_dd.split("-")]
    ts = int(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)))
    t = time.gmtime(ts)
    try:
        week = time.strftime("%G-W%V", t)
    except Exception:
        week = time.strftime("%Y-W%W", t)
    month = time.strftime("%Y-%m", t)
    return week, month


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="Pfad zur oroma.db")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Ziel-CSV")
    ap.add_argument("--days", type=int, default=365, help="Fenster in Tagen (relativ zu latest_ts)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"❌ DB nicht gefunden: {args.db}")

    with sqlite3.connect(args.db) as conn:
        cur = conn.cursor()
        anchor = latest_ts(conn)
        if not anchor:
            raise SystemExit("❌ Keine Aktivität in DB gefunden (latest_ts=0).")
        since = max(0, anchor - int(args.days) * 24 * 3600)

        # Daily snapchains
        cur.execute(
            """
            SELECT date(ts, 'unixepoch') AS day,
                   COUNT(*)              AS chains,
                   AVG(quality)          AS q_avg,
                   MAX(quality)          AS q_max
              FROM snapchains
             WHERE ts >= ?
               AND (status IS NULL OR status != 'deleted')
          GROUP BY day
          ORDER BY day
            """,
            (since,),
        )
        daily_rows = cur.fetchall() or []

        daily: Dict[str, Dict[str, Any]] = {}
        for day, chains, q_avg, q_max in daily_rows:
            daily[str(day)] = {
                "period": str(day),
                "chains": _safe_int(chains),
                "q_avg": _safe_float(q_avg),
                "q_max": _safe_float(q_max),
                "coverage_max": 0.0,
                "reward_max": 0.0,
            }

        # Daily coverage max
        try:
            cur.execute(
                """
                SELECT date(ts, 'unixepoch') AS day,
                       MAX(coverage)         AS coverage_max
                  FROM coverage_log
                 WHERE ts >= ?
              GROUP BY day
              ORDER BY day
                """,
                (since,),
            )
            for day, cmax in cur.fetchall() or []:
                d = daily.setdefault(str(day), {"period": str(day), "chains": 0, "q_avg": 0.0, "q_max": 0.0, "coverage_max": 0.0, "reward_max": 0.0})
                d["coverage_max"] = _safe_float(cmax)
        except Exception as e:
            log_suppressed('tools/export_learning_curve_summary_csv.py:170', exc=e, level=logging.WARNING)
            pass

        # Daily reward max
        try:
            cur.execute(
                """
                SELECT date(created_at, 'unixepoch') AS day,
                       MAX(reward)                  AS reward_max
                  FROM rewards_log
                 WHERE created_at >= ?
              GROUP BY day
              ORDER BY day
                """,
                (since,),
            )
            for day, rmax in cur.fetchall() or []:
                d = daily.setdefault(str(day), {"period": str(day), "chains": 0, "q_avg": 0.0, "q_max": 0.0, "coverage_max": 0.0, "reward_max": 0.0})
                d["reward_max"] = _safe_float(rmax)
        except Exception as e:
            log_suppressed('tools/export_learning_curve_summary_csv.py:190', exc=e, level=logging.WARNING)
            pass

    # Weekly / Monthly buckets
    weekly: Dict[str, Dict[str, Any]] = {}
    monthly: Dict[str, Dict[str, Any]] = {}

    def upd(bucket: Dict[str, Dict[str, Any]], key: str, row: Dict[str, Any]) -> None:
        b = bucket.get(key)
        if not b:
            bucket[key] = {
                "period": key,
                "chains": _safe_int(row.get("chains", 0)),
                "q_max": _safe_float(row.get("q_max", 0.0)),
                "q_avg_max": _safe_float(row.get("q_avg", 0.0)),
                "coverage_max": _safe_float(row.get("coverage_max", 0.0)),
                "reward_max": _safe_float(row.get("reward_max", 0.0)),
            }
            return
        b["chains"] += _safe_int(row.get("chains", 0))
        b["q_max"] = max(b["q_max"], _safe_float(row.get("q_max", 0.0)))
        b["q_avg_max"] = max(b["q_avg_max"], _safe_float(row.get("q_avg", 0.0)))
        b["coverage_max"] = max(b["coverage_max"], _safe_float(row.get("coverage_max", 0.0)))
        b["reward_max"] = max(b["reward_max"], _safe_float(row.get("reward_max", 0.0)))

    for day in sorted(daily.keys()):
        row = daily[day]
        wk, mo = _period_keys_from_day(day)
        upd(weekly, wk, row)
        upd(monthly, mo, row)

    # CSV schreiben
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["granularity", "period", "chains", "q_max", "q_avg_or_q_avg_max", "coverage_max", "reward_max"])

        for day in sorted(daily.keys()):
            r = daily[day]
            w.writerow(["day", r["period"], r["chains"], f"{r['q_max']:.6f}", f"{r['q_avg']:.6f}", f"{r['coverage_max']:.6f}", f"{r['reward_max']:.6f}"])

        for k in sorted(weekly.keys()):
            r = weekly[k]
            w.writerow(["week", r["period"], r["chains"], f"{r['q_max']:.6f}", f"{r['q_avg_max']:.6f}", f"{r['coverage_max']:.6f}", f"{r['reward_max']:.6f}"])

        for k in sorted(monthly.keys()):
            r = monthly[k]
            w.writerow(["month", r["period"], r["chains"], f"{r['q_max']:.6f}", f"{r['q_avg_max']:.6f}", f"{r['coverage_max']:.6f}", f"{r['reward_max']:.6f}"])

    print("✅ CSV geschrieben:", args.out)
    print(f"   anchor_ts: {anchor}  (UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(anchor))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

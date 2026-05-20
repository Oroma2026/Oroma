#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/db_learning_curve.py
# Projekt: ORÓMA
# Version: v3.7.3+curve-r1 – SnapChains-basierte Lernkurve (PNG + optional CSV)
# Stand:   2025-12-17
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Offline/CLI-Tool zur schnellen Visualisierung der „Lernkurve“ aus der
#   produktiven SQLite-DB (oroma.db).
#
#   Warum dieses Tool?
#   ------------------
#   Frühe Versionen nutzten eine Tabelle `snaps`, die in aktuellen v3.7.x Builds
#   oft nicht existiert (stattdessen: `snapchains`, `coverage_log`, `rewards_log`).
#   Dadurch entstanden leere Plots oder SQL-Fehler.
#
#   Dieses Tool verwendet ausschließlich stabile Quellen:
#     • snapchains(ts, quality, status, origin, ...)
#     • coverage_log(ts, coverage, active, total, ...)
#
# Ergebnis
# ────────
#   • learning_curve.png  (default im gleichen Ordner)
#   • optional: learning_curve_daily.csv (falls --csv gesetzt)
#   • kurze Zusammenfassung auf der Konsole
#
# Headless
# ────────
#   • Setzt Matplotlib-Backend auf Agg (kein X11/Wayland/Qt nötig).
#
# Nutzung
# ───────
#   PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/tools/db_learning_curve.py
#
#   Optional:
#     --db   /pfad/zur/oroma.db
#     --out  /pfad/zur/learning_curve.png
#     --csv  /pfad/zur/learning_curve_daily.csv
#     --days 120    (Fenster relativ zur letzten DB-Aktivität)
#
# Hinweise
# ────────
#   • In Projekt-ZIPs sind Tabellen häufig auf max. 1000 Zeilen gekürzt
#     (Sampling/Truncation). Das Tool ankert daher am latest_ts in der DB und
#     bildet das Fenster relativ zu dieser Aktivität.
# =============================================================================

from __future__ import annotations

import argparse
import os
import sqlite3
import time

import matplotlib
import logging
from core.log_guard import log_suppressed
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

try:
    import pandas as pd  # noqa: E402
except Exception as e:  # pragma: no cover
    raise SystemExit(f"❌ pandas fehlt: {e}")

DEFAULT_DB = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
DEFAULT_OUT = "/opt/ai/oroma/tools/learning_curve.png"


def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


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
        try:
            cur.execute(f"SELECT MAX({c}) FROM {t}")
            v = cur.fetchone()[0]
            latest = max(latest, _safe_int(v, 0))
        except Exception as e:
            log_suppressed('tools/db_learning_curve.py:95', exc=e, level=logging.WARNING)
            pass
    return latest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="Pfad zur oroma.db")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Pfad zur PNG-Ausgabe")
    ap.add_argument("--csv", default="", help="Optional: Pfad zur CSV-Ausgabe (daily)")
    ap.add_argument("--days", type=int, default=120, help="Fenster in Tagen (relativ zu latest_ts)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"❌ Datenbank nicht gefunden: {args.db}")

    with sqlite3.connect(args.db) as conn:
        anchor = latest_ts(conn)
        if not anchor:
            raise SystemExit("❌ Keine Aktivität in DB gefunden (latest_ts=0).")

        since = max(0, anchor - int(args.days) * 24 * 3600)

        # Daily SnapChains
        snaps = pd.read_sql_query(
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
            conn,
            params=(since,),
        )

        # Daily Coverage
        try:
            cov = pd.read_sql_query(
                """
                SELECT date(ts, 'unixepoch') AS day,
                       AVG(coverage)         AS coverage_avg,
                       MAX(coverage)         AS coverage_max
                  FROM coverage_log
                 WHERE ts >= ?
              GROUP BY day
              ORDER BY day
                """,
                conn,
                params=(since,),
            )
        except Exception:
            cov = pd.DataFrame(columns=["day", "coverage_avg", "coverage_max"])

    # Merge
    df = pd.merge(snaps, cov, on="day", how="left")
    df["coverage_avg"] = df["coverage_avg"].fillna(method="ffill")
    df["coverage_max"] = df["coverage_max"].fillna(method="ffill")

    # Plot (2 Achsen)
    fig, ax1 = plt.subplots()
    ax1.set_title("ORÓMA – Lernkurve (SnapChains Qualität + Aktivität)")
    ax1.plot(df["day"], df["q_avg"], label="q_avg (SnapChains)")
    ax1.plot(df["day"], df["q_max"], label="q_max (SnapChains)")
    ax1.set_xlabel("Datum")
    ax1.set_ylabel("Qualität (0..1)")
    ax1.grid(True)

    ax2 = ax1.twinx()
    ax2.bar(df["day"], df["chains"], alpha=0.25, label="Chains/Tag")
    ax2.set_ylabel("Chains/Tag")

    # Coverage (optional) – als dünne Linie auf ax1
    if "coverage_avg" in df.columns and df["coverage_avg"].notna().any():
        ax1.plot(df["day"], df["coverage_avg"], label="coverage_avg", linestyle="--")

    # Legende kombiniert
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, loc="upper left")

    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(args.out, dpi=150)
    plt.close()

    print("✅ Lernkurve erzeugt:", args.out)
    print(f"   anchor_ts: {anchor}  (UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(anchor))})")
    print(df.tail(10).to_string(index=False))

    if args.csv:
        df.to_csv(args.csv, index=False)
        print("✅ Daily-CSV geschrieben:", args.csv)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

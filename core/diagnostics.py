#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/diagnostics.py
# Projekt: ORÓMA
# Version: v3.5
# Stand:   2025-09-21
#
# Zweck:
#   Diagnostics-Modul für ORÓMA:
#     - Führt System- und Lern-Diagnosen durch
#     - Berechnet Knowledge-Gap-Summaries (novelty, confidence, coverage, time_to_goal)
#     - Aggregiert Metriken aus DB (metrics, rewards, curiosity_log)
#     - Liefert kompakte JSON-APIs für das UI (Badges, Learning-Dashboard)
#
# Steuerung:
#   - quick_summary(window_sec: int) -> dict
#       → liefert Kurz-Zusammenfassung der Metriken
#   - record_gap_metrics(...) -> bool
#       → schreibt neue Gap-Metriken
#
# Hinweise:
#   - API-Endpunkte werden von ui/gaps_ui.py und ui/learning.py genutzt
#   - Nutzt core/sql_manager (ab v3.5 mit insert_metric, vector_index_sync)
# =============================================================================

from __future__ import annotations
import os
import sys
import time
from typing import Any, Dict, Optional
from core.log_guard import log_suppressed
import logging

# Projektbasis
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma/")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    from core import sql_manager
except Exception:  # Fallback: Mini-Stub für Tests
    class _SQLStub:
        def get_conn(self):
            import sqlite3
            c = sqlite3.connect(":memory:", check_same_thread=False)
            c.execute("CREATE TABLE IF NOT EXISTS metrics (key TEXT, ts INTEGER, value REAL)")
            c.row_factory = lambda cur, row: {d[0]: row[i] for i, d in enumerate(cur.description)}
            return c
        def count_snapchains(self) -> int: return 0  # type: ignore
        def insert_metric(self, k, v): return True  # type: ignore
        def vector_index_sync(self): return {"ok": True, "needs_vector_index": False}
    sql_manager = _SQLStub()  # type: ignore


# -------------------------- kleine SQL-Utils ---------------------------------

def _select_avg_for_key(conn, key: str, since_ts: int) -> Optional[float]:
    try:
        cur = conn.execute(
            "SELECT AVG(value) AS avgv FROM metrics WHERE key=? AND ts>=?",
            (key, int(since_ts))
        )
        row = cur.fetchone()
        if not row:
            return None
        v = row.get("avgv")
        return float(v) if v is not None else None
    except Exception:
        return None


def _select_recent_count(conn, key: str, since_ts: int) -> int:
    try:
        cur = conn.execute(
            "SELECT COUNT(1) AS c FROM metrics WHERE key=? AND ts>=?",
            (key, int(since_ts))
        )
        row = cur.fetchone()
        return int(row.get("c") or 0)
    except Exception:
        return 0


# ------------------------------- Kernlogik -----------------------------------

def quick_summary(window_sec: int = 24 * 3600) -> Dict[str, Any]:
    """
    Liefert eine kompakte Diagnose-Zusammenfassung für die UI.
    Robust gegen leere DBs / fehlende Feeds.
    """
    now = int(time.time())
    since = now - int(window_sec)

    conn = sql_manager.get_conn()

    # Coverage-Heuristik
    try:
        total_chains = int(sql_manager.count_snapchains())
    except Exception:
        total_chains = 0

    chains_norm = min(1.0, _log_norm(total_chains, k=1000))
    cov_boost = _select_avg_for_key(conn, "coverage_boost", since) or 0.0
    learn_events = _select_recent_count(conn, "learning_event", since)

    coverage = max(0.0, min(1.0, chains_norm + cov_boost + min(0.2, learn_events * 0.01)))

    novelty = _select_avg_for_key(conn, "novelty", since) or 0.40
    confidence = _select_avg_for_key(conn, "confidence", since) or 0.55
    ttg = _select_avg_for_key(conn, "time_to_goal_norm", since) or 0.50

    score = (1.0 - coverage) * 0.4 + (novelty) * 0.3 + (1.0 - confidence) * 0.2 + (ttg) * 0.1
    badge = "LOW"
    if score > 0.66:
        badge = "HIGH"
    elif score > 0.33:
        badge = "MED"

    # Optional: VectorDB-Status mitgeben
    try:
        vdb = sql_manager.vector_index_sync()
    except Exception:
        vdb = {"ok": False, "error": "vector_index_sync failed"}

    return {
        "ok": True,
        "summary": {
            "coverage": float(coverage),
            "novelty": float(novelty),
            "confidence": float(confidence),
            "time_to_goal_norm": float(ttg),
            "gap_badge": badge,
            "score": float(score),
            "basis": {
                "total_snapchains": total_chains,
                "window_sec": window_sec,
                "learning_events": learn_events,
                "coverage_norm": chains_norm,
                "coverage_boost_avg": cov_boost,
            },
            "vectordb": vdb,
        },
    }


def record_gap_metrics(
    coverage: Optional[float] = None,
    novelty: Optional[float] = None,
    confidence: Optional[float] = None,
    time_to_goal_norm: Optional[float] = None,
) -> bool:
    """
    Schreibt einzelne Gap-Metriken (falls übergeben) nach metrics.
    Nutzt zusätzlich sql_manager.insert_metric() für Spiegelung.
    """
    try:
        now = int(time.time())
        conn = sql_manager.get_conn()
        with conn:
            for k, v in (
                ("coverage", coverage),
                ("novelty", novelty),
                ("confidence", confidence),
                ("time_to_goal_norm", time_to_goal_norm),
            ):
                if v is None:
                    continue
                conn.execute("INSERT INTO metrics (key, ts, value) VALUES (?,?,?)", (k, now, float(v)))
                try:
                    sql_manager.insert_metric(k, float(v))
                except Exception as e:
                    log_suppressed(
                        logging.getLogger(__name__),
                        key="core.diagnostics.pass.1",
                        exc=e,
                        msg="Suppressed exception (was: pass)",
                    )
        return True
    except Exception:
        return False


# ------------------------------- Hilfszeug -----------------------------------

def _log_norm(n: int, k: int = 1000) -> float:
    """Grobe Log-Normalisierung: 0..1 bei 0..k..∞ (saturiert >k gegen 1)."""
    if n <= 0:
        return 0.0
    import math
    return min(1.0, math.log10(1 + n) / math.log10(1 + max(1, k)))


# ------------------------------- Selftest ------------------------------------

def _selftest() -> None:
    print("[diagnostics] selftest…")

    # Test-Metriken einfügen
    record_gap_metrics(coverage=0.4, novelty=0.6, confidence=0.5, time_to_goal_norm=0.7)

    # Zusammenfassung
    q = quick_summary(window_sec=3600)
    print("  summary:", q["summary"])

    print("[diagnostics] OK ✅")


if __name__ == "__main__":
    _selftest()
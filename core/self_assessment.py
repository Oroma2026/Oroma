#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/self_assessment.py
# Projekt: ORÓMA
# Version: v3.5patch1
# Stand:   2025-09-23
# =============================================================================

import time
import json
from contextlib import contextmanager
from typing import Dict, Any, Iterator

from core import sql_manager


@contextmanager
def _conn_cm() -> Iterator[object]:
    """Robuster Connection-Context.

    In einigen Patch-Kombinationen kann es beim Import/Startup zu Situationen
    kommen, in denen `core.sql_manager.conn_cm` (noch) nicht exportiert ist.
    Selbstbewertung ist Hot-Path – daher: fallback auf get_conn()/close().
    """
    cm = getattr(sql_manager, "conn_cm", None)
    if callable(cm):
        with cm() as conn:
            yield conn
        return

    conn = sql_manager.get_conn()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

class SelfAssessment:
    """
    Engine für Selbstbewertung: analysiert Lernrunden, berechnet Meta-Snaps
    und schreibt sie in die Datenbank.
    """

    @staticmethod
    def evaluate(stats: Dict[str, Any]) -> int:
        """
        Bewertet die Lernqualität und erzeugt einen Eintrag in meta_snaps.
        Erwartet keys: reward_avg, error_rate, duration.
        """
        label = "hoch" if stats.get("reward_avg", 0) > 0.7 else "niedrig"
        score = float(stats.get("reward_avg", 0))
        sources = json.dumps(stats)

        try:
            # Stufe C: MetaSnaps sind Write-Heavy in Peak-Phasen (Curriculum).
            # Wenn DBWriter aktiv ist, nutzen wir den globalen Single-Writer und vermeiden
            # lokale SQLite-Writer-Kollisionen.
            try:
                _dbw_enabled = getattr(sql_manager, "_dbw_enabled", None)
                _dbw = getattr(sql_manager, "_dbw", None)
                if callable(_dbw_enabled) and _dbw_enabled() and _dbw is not None:
                    rid = int(getattr(_dbw, "exec_lastrowid")(
                        "INSERT INTO meta_snaps (label, score, sources) VALUES (?,?,?)",
                        params=[label, float(score), str(sources)],
                        tag="self_assessment.metasnap",
                        priority="low",
                        timeout_ms=8000,
                        db="oroma",
                    ) or 0)
                    return rid
            except Exception as e:
                # Wenn DBWriter aktiv ist, vermeiden wir lokale Writer-Fallbacks (würden wieder
                # globale SQLite-Kollisionen erzeugen). Fehler bleibt sichtbar.
                try:
                    if callable(getattr(sql_manager, "_dbw_enabled", None)) and getattr(sql_manager, "_dbw_enabled")():
                        print(f"[SelfAssessment] DBWriter failed – skip (no local fallback): {e}")
                        return -1
                except Exception:
                    pass

            # Controlled retry bei SQLite-Locks ("database is locked") – SelfAssessment ist Hot-Path im Curriculum.
            try:
                retry_sec = int(getattr(sql_manager, "_env_int", lambda n, d: d)("OROMA_DB_LOCK_RETRY_SEC", 60))  # type: ignore
            except Exception:
                retry_sec = 60

            def _do_once() -> int:
                with _conn_cm() as conn:
                    cur = conn.execute(
                        "INSERT INTO meta_snaps (label, score, sources) VALUES (?,?,?)",
                        (label, score, sources),
                    )
                    conn.commit()
                    return int(cur.lastrowid)

            return int(getattr(sql_manager, "_run_with_lock_retry")(_do_once, int(retry_sec)))  # type: ignore
        except Exception as e:
            print(f"[SelfAssessment] Fehler beim Einfügen: {e}")
            return -1

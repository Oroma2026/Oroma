#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/hooks_patch2.py
# Projekt:   ORÓMA (AgentLoop Hooks · Orchestrator-safe)
# Modul:     Patch2 Hooks – Coverage-Log (Standard) + optional Empathy-Snaps, mit Lock-robuster DB-Schreiblogik + steuerbarem Logging
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul enthält zusätzliche AgentLoop-Hooks („Patch2“), die bewusst klein sind,
# aber im Live-System wichtige Telemetrie liefern:
#
#   (1) Coverage-Log (Standard, Default ON)
#       - schreibt periodisch den Abdeckungsgrad aktiver SnapChains:
#           coverage = active / total
#       - Ziel: Learning-UI und Langzeitdiagnose („läuft die Pipeline noch?“)
#
#   (2) Empathy-Snaps (Optional, Default OFF)
#       - simuliert/erzeugt einfache stimmungsbasierte Werte (happy/sad/neutral)
#       - Ziel: Test/UX/Research – darf niemals Kernpipeline blockieren
#
# ORCHESTRATOR-REALITÄT: SQLITE LOCKS
# ───────────────────────────────────
# ORÓMA hat oft parallele Writer (AgentLoop, StatsSnapshot, EnergyManager, DreamWorker).
# SQLite kann dennoch immer nur einen Writer committen.
#
# Dieses Modul ist deshalb „best effort“:
#   - DB-Insert-Fehler (locked/busy) sollen den AgentLoop nicht blockieren
#   - Fehler werden rate-limited geloggt (core.log_guard.log_suppressed)
#   - Coverage/Empathy kann in einzelnen Ticks ausfallen, ohne Systemwirkung
#
# WAS GENAU GESCHRIEBEN WIRD
# ──────────────────────────
# Coverage:
#   - Ziel: oroma.db Tabelle `coverage_log` (über sql_manager.* oder direkte sqlite fallback)
#   - Felder typischerweise: ts, coverage, active, total, meta_json
#
# Empathy:
#   - Ziel: oroma.db Tabelle `empathy_snaps` (wenn aktiv)
#   - Felder typischerweise: ts, score, label, meta_json
#
# Das Schema wird upstream durch core.sql_manager.ensure_schema() bereitgestellt.
#
# LOGGING-STEUERUNG (DIESE DATEI BIETET DAS EXPLIZIT)
# ──────────────────────────────────────────────────
# Damit Logs nicht „spammen“, ist Logging konfigurierbar:
#   OROMA_HOOKS2_LOG=0|1
#   OROMA_HOOKS2_LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
#   OROMA_HOOKS2_LOG_FILE=/path/to/logfile   (optional)
#   OROMA_HOOKS2_LOG_EVERY=20                (Status-Logs nur alle N Ticks)
#
# ENABLE-FLAGS
# ────────────
#   OROMA_ENABLE_COVERAGE=true|false   (Default: true)
#   OROMA_ENABLE_EMPATHY=true|false    (Default: false)
#
# TICK-FREQUENZEN (PRAXIS)
# ───────────────────────
# Coverage ist typischerweise nicht jedes dt nötig:
#   - intern wird meist ein Tick-Modulo genutzt (z. B. nur alle N Ticks schreiben)
# Empathy ebenfalls:
#   - sporadisch, damit DB nicht unnötig wächst
#
# FALLBACK-DB-PFAD (WHY)
# ──────────────────────
# Dieses Modul versucht bevorzugt core.sql_manager zu importieren.
# Wenn das ausnahmsweise scheitert (z. B. minimales Test-Env), existiert ein
# sqlite3-Fallback, damit der AgentLoop nicht crasht.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Coverage darf nicht „versehentlich“ deaktiviert werden: Default ON bleibt wichtig.
# - Hooks dürfen niemals lang blockieren (kein Warten auf DB).
# - Fehler müssen rate-limited sein (sonst Log-Explosion).
# - Kompatibilität: Wenn sql_manager nicht importierbar ist, muss fallback existieren.
#
# ÖFFENTLICHE API (HOOK-VERTRAG)
# ─────────────────────────────
# coverage_hook(dt: float, tick: int) -> None
# empathy_hook(dt: float, tick: int) -> None
# (oder entsprechende Registrierungsfunktionen; je nach Implementierung im File)
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os
import time
import random
import logging
from core.log_guard import log_suppressed
import sqlite3
from typing import Optional

# -------------------------------------------------------
# SQL-Backend (best effort)
# -------------------------------------------------------
try:
    from core import sql_manager  # korrekt: KEINE Leerzeichen/Unterstriche!
    _HAS_SQL = True
except Exception:
    sql_manager = None  # type: ignore
    _HAS_SQL = False

# -------------------------------------------------------
# DBWriter (Stufe C) – best-effort Writes (Hook2)
# -------------------------------------------------------
try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

def _dbw_enabled() -> bool:
    try:
        return bool(db_writer_client is not None and getattr(db_writer_client, 'enabled', lambda: False)())
    except Exception:
        return False

def _dbw_exec(sql: str, params, tag: str, timeout_ms: int, priority: str = 'low') -> bool:
    """Best-effort Write über DBWriter (verhindert multi-writer Locks).

    Rückgabe:
      True  → Request wurde an DBWriter übergeben
      False → DBWriter nicht aktiv

    WICHTIG:
      Wenn DBWriter aktiv ist, gibt es hier keinen lokalen Fallback mehr.
      Fehler werden sichtbar geloggt und der Write wird übersprungen.
    """
    if not _dbw_enabled():
        return False
    try:
        db_writer_client.exec_write(str(sql), list(params), tag=str(tag), priority=str(priority), timeout_ms=int(timeout_ms))
        return True
    except Exception as e:
        try:
            log_suppressed(logging.getLogger('oroma.hooks_patch2'), key='core_hooks_patch2.dbw.fail', msg='DBWriter write failed (skip; no local fallback).', exc=e, level=logging.WARNING, interval_s=60)
        except Exception:
            pass
        return True

# -------------------------------------------------------
# Logging (steuerbar per ENV)
# -------------------------------------------------------
def _parse_bool(env_val: str, default: bool) -> bool:
    if env_val is None:
        return default
    v = str(env_val).strip().lower()
    return v in ("1", "true", "yes", "on")

_H2_LOG_ENABLED = _parse_bool(os.environ.get("OROMA_HOOKS2_LOG", "1"), True)
_H2_LOG_LEVEL = os.environ.get("OROMA_HOOKS2_LOG_LEVEL", "INFO").upper()
_H2_LOG_FILE = os.environ.get("OROMA_HOOKS2_LOG_FILE", "").strip() or None
try:
    _H2_LOG_EVERY = int(os.environ.get("OROMA_HOOKS2_LOG_EVERY", "20"))
    if _H2_LOG_EVERY < 1:
        _H2_LOG_EVERY = 1
except Exception:
    _H2_LOG_EVERY = 20

LOG = logging.getLogger("oroma.hooks_patch2")

# Logger nur konfigurieren, wenn noch keine Handler vorhanden (idempotent)
if not LOG.handlers:
    if _H2_LOG_ENABLED:
        if _H2_LOG_FILE:
            fh = logging.FileHandler(_H2_LOG_FILE, encoding="utf-8")
            fh.setFormatter(logging.Formatter("[hooks_patch2] %(levelname)s: %(message)s"))
            LOG.addHandler(fh)
        else:
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("[hooks_patch2] %(levelname)s: %(message)s"))
            LOG.addHandler(sh)

# Level & Disabled-Flag setzen
try:
    LOG.setLevel(getattr(logging, _H2_LOG_LEVEL, logging.INFO))
except Exception:
    LOG.setLevel(logging.INFO)

LOG.disabled = not _H2_LOG_ENABLED  # hartes Ausschalten aller Logs

# -------------------------------------------------------
# ENV-Flags (Funktionalität)
# -------------------------------------------------------
_ENABLE_EMPATHY = os.environ.get("OROMA_ENABLE_EMPATHY", "false").strip().lower() in ("1", "true", "yes", "on")
_ENABLE_COVERAGE = os.environ.get("OROMA_ENABLE_COVERAGE", "true").strip().lower() not in ("0", "false", "no", "off")

# -------------------------------------------------------
# Fast-DB-Modus (NEU in 2026-01-05 Hotfix)
# -------------------------------------------------------
#
# Problem (aus Live-Debugging):
#   sql_manager.get_conn() setzt standardmäßig busy_timeout=60000ms.
#   Wenn parallel ein Writer (Dream/Stats/Replay/Import) läuft,
#   blockiert ein INSERT im AgentLoop dann bis zu 60s.
#   Ergebnis: tick bleibt stehen, Hooks "hängen", Chains/Telemetrie brechen ein.
#
# Lösung:
#   Patch2-Hooks (Empathy/Coverage) dürfen NIE den AgentLoop blockieren.
#   Wir schreiben daher in einem "fast"-Conn mit sehr kleinem busy_timeout
#   und überspringen Writes bei Lock sofort.
#
# Steuerung:
#   OROMA_HOOKS2_FAST_DB=1            → aktiv (Default: 1)
#   OROMA_HOOKS2_FAST_DB=0            → fallback: sql_manager.get_conn() (busy_timeout wird auf _H2_FAST_DB_BUSY_MS gesetzt)
#   OROMA_HOOKS2_FAST_DB_BUSY_MS=50   → busy_timeout in ms (Default: 50)
#   OROMA_HOOKS2_FAST_DB_TIMEOUT=0.2  → sqlite connect timeout (Default: 0.2)
#
# Zusätzlich: Rate-Limit pro Hook, damit nicht 4x/s in SQLite geschrieben wird:
#   OROMA_HOOKS2_EMPATHY_PERIOD_SEC=10
#   OROMA_HOOKS2_COVERAGE_PERIOD_SEC=10

def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception as e:
        log_suppressed(LOG, key="core_hooks_patch2.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return float(default)

_H2_FAST_DB = _parse_bool(os.environ.get("OROMA_HOOKS2_FAST_DB", "1"), True)
_H2_FAST_DB_BUSY_MS = int(os.environ.get("OROMA_HOOKS2_FAST_DB_BUSY_MS", "50") or "50")
_H2_FAST_DB_TIMEOUT = _env_float("OROMA_HOOKS2_FAST_DB_TIMEOUT", 0.2)

try:
    _H2_EMPATHY_PERIOD_SEC = float(os.environ.get("OROMA_HOOKS2_EMPATHY_PERIOD_SEC", "10") or "10")
    if _H2_EMPATHY_PERIOD_SEC < 0.25:
        _H2_EMPATHY_PERIOD_SEC = 0.25
except Exception:
    _H2_EMPATHY_PERIOD_SEC = 10.0

try:
    _H2_COVERAGE_PERIOD_SEC = float(os.environ.get("OROMA_HOOKS2_COVERAGE_PERIOD_SEC", "10") or "10")
    if _H2_COVERAGE_PERIOD_SEC < 0.25:
        _H2_COVERAGE_PERIOD_SEC = 0.25
except Exception:
    _H2_COVERAGE_PERIOD_SEC = 10.0

# Coverage-Fenster (Days) – reduziert den "Denominator drift" durch monotones Wachstum von total.
# Default: 30 Tage (kann bei Bedarf auf 7/14/60 gesetzt werden).
try:
    _H2_COVERAGE_WINDOW_DAYS = int(os.environ.get("OROMA_COVERAGE_WINDOW_DAYS", "30") or "30")
    if _H2_COVERAGE_WINDOW_DAYS < 1:
        _H2_COVERAGE_WINDOW_DAYS = 1
except Exception:
    _H2_COVERAGE_WINDOW_DAYS = 30

_LAST_EMPATHY_TS = 0
_LAST_COVERAGE_TS = 0
_LAST_LOCK_WARN_TS = 0

def _fast_db_path() -> Optional[str]:
    if not (_HAS_SQL and sql_manager is not None):
        return None
    if hasattr(sql_manager, "get_db_path"):
        try:
            return str(sql_manager.get_db_path())  # type: ignore[attr-defined]
        except Exception as e:
            log_suppressed(LOG, key="core_hooks_patch2.ret.2", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None
    return None

def _fast_conn() -> Optional[sqlite3.Connection]:
    """Kurze DB-Connection, die bei Locks schnell zurückkehrt."""
    if not _H2_FAST_DB:
        return None
    dbp = _fast_db_path()
    if not dbp:
        return None
    try:
        conn = sqlite3.connect(dbp, timeout=float(_H2_FAST_DB_TIMEOUT), check_same_thread=False)
        try:
            conn.execute(f"PRAGMA busy_timeout={int(_H2_FAST_DB_BUSY_MS)}")
        except Exception as e:
            log_suppressed(LOG, key="core_hooks_patch2.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return conn
    except Exception as e:
        log_suppressed(LOG, key="core_hooks_patch2.ret.4", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def _is_locked_err(e: BaseException) -> bool:
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg

def _best_effort_conn() -> Optional[sqlite3.Connection]:
    """Nicht-blockierende DB-Connection für Patch2-Hooks.

    Priorität:
      1) Wenn OROMA_HOOKS2_FAST_DB=1 → eigener sqlite3-Conn (siehe _fast_conn()).
      2) Wenn OROMA_HOOKS2_FAST_DB=0 → sql_manager.get_conn(), aber busy_timeout wird *sofort*
         auf _H2_FAST_DB_BUSY_MS reduziert, damit Writes den AgentLoop nicht hängen lassen.

    Hinweis:
      - Diese Funktion ist bewusst "best effort": bei Fehlern/Locks → None zurückgeben.
    """
    fc = _fast_conn()
    if fc is not None:
        return fc
    if not (_HAS_SQL and sql_manager is not None):
        return None
    try:
        conn = sql_manager.get_conn()  # type: ignore[union-attr]
        try:
            conn.execute(f"PRAGMA busy_timeout={int(_H2_FAST_DB_BUSY_MS)}")
        except Exception as e:
            log_suppressed(LOG, key="core_hooks_patch2.pass.best_effort_busy_timeout", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return conn
    except Exception as e:
        log_suppressed(LOG, key="core_hooks_patch2.ret.best_effort_conn", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def _rate_limit_lock_warn() -> bool:
    """Logs zu Locks nur selten, damit wir nicht wieder IO/Log-Spam erzeugen."""
    global _LAST_LOCK_WARN_TS
    now = int(time.time())
    if now - int(_LAST_LOCK_WARN_TS or 0) >= 30:
        _LAST_LOCK_WARN_TS = now
        return True
    return False

# -------------------------------------------------------
# Interne Fallbacks (falls sql_manager bestimmte Helfer nicht hat)
# -------------------------------------------------------
def _count_snapchains(status: Optional[str] = None) -> int:
    """
    Zählt Snapchains. Nutzt bevorzugt sql_manager.count_snapchains(status),
    fällt ansonsten auf eine direkte SQL-Abfrage via get_conn() zurück.
    """
    if not _HAS_SQL or sql_manager is None:
        return 0

    # 0) Fast-DB bevorzugen (nicht blockierend)
    fc = _fast_conn()
    if fc is not None:
        try:
            cur = fc.cursor()
            if status:
                row = cur.execute("SELECT COUNT(*) FROM snapchains WHERE status=?", (str(status),)).fetchone()
            else:
                row = cur.execute("SELECT COUNT(*) FROM snapchains").fetchone()
            return int(row[0] if row else 0)
        except Exception as e:
            log_suppressed(LOG, key="core_hooks_patch2.ret.5", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return 0
        finally:
            try:
                fc.close()
            except Exception as e:
                log_suppressed(LOG, key="core_hooks_patch2.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # 1) Bevorzugt: bereitgestellte Helferfunktion
    if hasattr(sql_manager, "count_snapchains"):
        try:
            return int(sql_manager.count_snapchains(status))  # type: ignore[attr-defined]
        except Exception as e:
            LOG.debug("count_snapchains() via sql_manager fehlgeschlagen, nutze Fallback: %s", e)

    # 2) Fallback: direkte SQL-Zahl
    try:
        with sql_manager.get_conn() as conn:  # type: ignore[union-attr]
            if status:
                row = conn.execute("SELECT COUNT(*) AS n FROM snapchains WHERE status=?", (str(status),)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM snapchains").fetchone()
            return int(row["n"] if row and "n" in row else 0)
    except Exception as e:
        LOG.warning("Coverage-Fallback COUNT fehlgeschlagen: %s", e)
        return 0

# -------------------------------------------------------
# Empathy Hook
# -------------------------------------------------------
_MOODS = ["happy", "neutral", "sad"]

def empathy_hook(dt: float, tick: int) -> None:
    """
    Optionaler Empathy-Tick: schreibt stochastische Stimmungsschnappschüsse,
    wenn OROMA_ENABLE_EMPATHY=true gesetzt ist.
    """
    global _LAST_EMPATHY_TS
    if not (_HAS_SQL and _ENABLE_EMPATHY):
        return
    try:
        now = time.time()
        if (now - float(_LAST_EMPATHY_TS or 0)) < float(_H2_EMPATHY_PERIOD_SEC or 0):
            return
        _LAST_EMPATHY_TS = int(now)

        ts = int(now)
        mood = random.choice(_MOODS)
        score = (
            round(random.uniform(0.7, 1.0), 3) if mood == "happy" else
            round(random.uniform(0.0, 0.4), 3) if mood == "sad" else
            round(random.uniform(0.3, 0.7), 3)
        )

        # Non-blocking Write (fast-conn oder fallback sql_manager.get_conn mit kleinem busy_timeout)
        # DBWriter (Stufe C): bevorzugt für Writes (verhindert multi-writer Konkurrenz)
        if _dbw_enabled():
            ok = _dbw_exec(
                "INSERT INTO empathy_snaps (ts, mood, score) VALUES (?, ?, ?)",
                [int(ts), str(mood), float(score)],
                tag="hooks.patch2.empathy_snaps",
                timeout_ms=int(os.getenv("OROMA_H2_DBW_TIMEOUT_MS", "800")),
                priority=os.getenv("OROMA_H2_DBW_PRIORITY", "low"),
            )
            if ok:
                return

        conn = _best_effort_conn()

        if conn is None:

            return

        try:

            conn.execute(

                "INSERT INTO empathy_snaps (ts, mood, score) VALUES (?, ?, ?)",

                (int(ts), str(mood), float(score)),

            )

            conn.commit()

        except Exception as e:

            # Locks sind im produktiven Parallelbetrieb normal → skip

            if _is_locked_err(e):

                if (not LOG.disabled) and _rate_limit_lock_warn():

                    LOG.warning("Empathy: DB locked/busy → skip (best_effort)")

                return

            raise

        finally:

            try:

                conn.close()

            except Exception as e:

                log_suppressed(LOG, key="core_hooks_patch2.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        # Nur jede N Ticks loggen (reduziert Spam) und nur wenn Logging aktiv ist:
        if (_H2_LOG_EVERY > 0) and (tick % _H2_LOG_EVERY == 0) and not LOG.disabled:
            LOG.info("Empathy: mood=%s score=%.2f", mood, score)
    except Exception as e:
        if not LOG.disabled:
            LOG.warning("Empathy-Hook Fehler: %s", e)

# -------------------------------------------------------
# Coverage Hook
# -------------------------------------------------------
def coverage_hook(dt: float, tick: int) -> None:
    """
    Coverage-Tick: berechnet active/total und schreibt in coverage_log.
    Standardmäßig aktiv (OROMA_ENABLE_COVERAGE=true).
    """
    global _LAST_COVERAGE_TS
    if not (_HAS_SQL and _ENABLE_COVERAGE):
        return
    try:
        now = time.time()
        if (now - float(_LAST_COVERAGE_TS or 0)) < float(_H2_COVERAGE_PERIOD_SEC or 0):
            return
        _LAST_COVERAGE_TS = int(now)

        ts = int(now)

        # Non-blocking (Counts + Insert in einer Verbindung; fast-conn oder fallback sql_manager.get_conn mit kleinem busy_timeout)
        # DBWriter (Stufe C): bevorzugt für Writes (Counts bleiben lokal read-only)
        # Hinweis: Die SELECTs (Counts) bleiben bewusst lokal; nur INSERTs gehen über DBW.

        conn = _best_effort_conn()

        if conn is None:

            return


        try:

            cur = conn.cursor()

            row_total = cur.execute("SELECT COUNT(*) FROM snapchains").fetchone()

            row_active = cur.execute("SELECT COUNT(*) FROM snapchains WHERE status='active'").fetchone()

            total = int(row_total[0] if row_total else 0)

            active = int(row_active[0] if row_active else 0)

            coverage = (active / total) if total > 0 else 0.0

            # Windowed Coverage (default: last 30 days)
            # Motivation: total wächst monoton (non-destruktiv), dadurch fällt active/total über Monate.
            # Windowed Coverage ist für die Learning-UI besser interpretierbar.
            win_days = int(_H2_COVERAGE_WINDOW_DAYS or 30)
            cutoff_ts = int(ts) - int(win_days) * 86400
            row_total_w = cur.execute("SELECT COUNT(*) FROM snapchains WHERE ts >= ?", (int(cutoff_ts),)).fetchone()
            row_active_w = cur.execute("SELECT COUNT(*) FROM snapchains WHERE status='active' AND ts >= ?", (int(cutoff_ts),)).fetchone()
            total_w = int(row_total_w[0] if row_total_w else 0)
            active_w = int(row_active_w[0] if row_active_w else 0)
            coverage_w = (active_w / total_w) if total_w > 0 else 0.0

            if _dbw_enabled():
                _dbw_exec(
                    "INSERT INTO coverage_log (ts, coverage, active, total) VALUES (?, ?, ?, ?)",
                    [int(ts), float(coverage), int(active), int(total)],
                    tag="hooks.patch2.coverage_log",
                    timeout_ms=int(os.getenv("OROMA_H2_DBW_TIMEOUT_MS", "800")),
                    priority=os.getenv("OROMA_H2_DBW_PRIORITY", "low"),
                )
            else:
                cur.execute(

                    "INSERT INTO coverage_log (ts, coverage, active, total) VALUES (?, ?, ?, ?)",

                    (int(ts), float(coverage), int(active), int(total))

                )

            # Best-effort: coverage_log_30d (falls Schema in älteren DBs noch fehlt)
            try:
                cur.execute(
                    "INSERT INTO coverage_log_30d (ts, coverage, active, total) VALUES (?, ?, ?, ?)",
                    (int(ts), float(coverage_w), int(active_w), int(total_w))
                )
            except Exception as e:
                # Häufigster Grund: Tabelle fehlt nach Restore/alte ZIP → optional CREATE IF NOT EXISTS
                msg = str(e).lower()
                if 'no such table' in msg and 'coverage_log_30d' in msg:
                    try:
                        cur.execute("CREATE TABLE IF NOT EXISTS coverage_log_30d (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, coverage REAL NOT NULL, active INTEGER NOT NULL, total INTEGER NOT NULL)")
                        cur.execute(
                            "INSERT INTO coverage_log_30d (ts, coverage, active, total) VALUES (?, ?, ?, ?)",
                            (int(ts), float(coverage_w), int(active_w), int(total_w))
                        )
                    except Exception:
                        # komplett best-effort – wir blockieren nie den AgentLoop
                        pass
                else:
                    # Andere Fehler ebenso best-effort (z. B. lock/busy)
                    pass

            conn.commit()

        except Exception as e:

            if _is_locked_err(e):

                if (not LOG.disabled) and _rate_limit_lock_warn():

                    LOG.warning("Coverage: DB locked/busy → skip (best_effort)")

                return

            raise

        finally:

            try:

                conn.close()

            except Exception as e:

                log_suppressed(LOG, key="core_hooks_patch2.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        if (_H2_LOG_EVERY > 0) and (tick % _H2_LOG_EVERY == 0) and not LOG.disabled:
            LOG.info("Coverage: active=%d total=%d coverage=%.2f", active, total, coverage)
    except Exception as e:
        if not LOG.disabled:
            LOG.warning("Coverage-Hook Fehler: %s", e)

# -------------------------------------------------------
# Selftest (manuell)
# -------------------------------------------------------
if __name__ == "__main__":
    print("[hooks_patch2] Selftest …")
    for i in range(10):
        empathy_hook(0.1, i)
        coverage_hook(0.1, i)
        time.sleep(0.1)
    print("[hooks_patch2] OK ✅")
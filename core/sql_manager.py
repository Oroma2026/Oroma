#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/sql_manager.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
#            Offline-First · Headless · SQLite-First · Edge Runtime
# Modul:     SQLManager – zentrale SQLite-Schicht (Autoritativ)
#            Connections/PRAGMAs · Schema/Migrationen · Read/Write Helper · Lock-Disziplin
# Version:   v3.7.3+dbwriter-hygiene-v1.1
# Stand:     2026-06-14
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
# =============================================================================
#
# 0) ZWECK / SYSTEMROLLE (WARUM DIESE DATEI KRITISCH IST)
# ───────────────────────────────────────────────────────
# `sql_manager.py` ist die **autoritative** DB-Schicht in ORÓMA:
#   - definiert stabile Connect-Defaults (busy_timeout/WAL/synchronous)
#   - stellt eine robuste Row-API bereit (dict rows) → weniger UI/Tool-Fragilität
#   - kapselt Lock-Disziplin (writer_lock + lock-retry) für 24/7 Betrieb
#   - implementiert schema-ensure / additive Migrationen (non-destructive)
#   - bietet Write-Helper für Hotpaths (snapchains, metrics, policy_rules, snap_index, etc.)
#   - integriert optional den DBWriter (Single-Writer-Funnel) für Burst-Writes
#
# Diese Datei ist bewusst “Herzstück”: Änderungen müssen minimal-invasiv, testbar
# und kompatibel zur DBWriter-Strategie erfolgen (keine stillen Fallback-Writes).
#
# PATCH-HINWEIS 2026-06-14 / P2.5a:
# - Eine tote doppelte `_dbw_enabled()`-Definition wurde entfernt.
# - Die aktive, ENV-basierte Implementierung bleibt erhalten und entspricht dem
#   Verhalten von `core.db_writer_client.enabled()` im aktuellen Stand.
# - Ziel ist reine Wartbarkeit/Hygiene: kein Semantikwechsel, kein DB-Pfadwechsel,
#   kein lokaler Write-Fallback und keine Änderung der Connection-/Schema-Logik.
#
# 1) HEADLESS / PRODUKTIONSINVARIANTEN (DO NOT BREAK)
# ────────────────────────────────────────────────────
# - Headless: keine GUI-Abhängigkeiten (kein Qt/Wayland/X11).
# - Non-Destructive: Migrationen sind additiv (CREATE IF NOT EXISTS, defensive ALTER).
# - Lock-Safety: Writes laufen unter writer_lock + optionalem Retry-Fenster.
# - Kein "silent fail": Fehler werden sichtbar geloggt (rate-limited) und sind im Betrieb erkennbar.
# - Connections werden **immer geschlossen** (Context Manager schließt wirklich → verhindert DB-Locks).
#
# 2) DB-PFAD / MULTI-DB (OROMA/STATS/KNOWLEDGE/REGISTRY)
# ───────────────────────────────────────────────────────
# DB-Pfad-Ermittlung:
#   1) OROMA_DB_PATH (wenn gesetzt → gewinnt)
#   2) OROMA_BASE / OROMA_BASE_DIR (Default /opt/ai/oroma) → {BASE}/data/oroma.db
#
# Zusätzlich existieren Pfade für Neben-DBs (z.B. stats/knowledge/registry), die u.a.
# vom DBWriter-Allowlist-Mapping genutzt werden (siehe core/db_writer.py).
#
# 3) CONNECTION-SETUP (PRAGMA DEFAULTS)
# ─────────────────────────────────────
# get_conn() setzt pro Connection:
#   - sqlite3.connect(..., timeout=OROMA_DB_TIMEOUT_SEC, check_same_thread=False)
#   - PRAGMA busy_timeout=OROMA_DB_BUSY_TIMEOUT_MS
#   - WAL Default AN: OROMA_DB_WAL=1 → journal_mode=WAL + synchronous=NORMAL
#   - row_factory liefert dict rows: {"col": value, ...}
#
# 4) CLOSING CONNECTION FIX (LOCK-VERMEIDUNG)
# ───────────────────────────────────────────
# sqlite3.Connection schließt per with-Block standardmäßig nicht automatisch.
# ORÓMA nutzt daher eine Connection-Subklasse, die beim Context-Exit **immer** schließt,
# um DB-Locks/FD-Leaks im 24/7 Betrieb zu vermeiden.
#
# 5) WRITER LOCK (THREAD + OPTIONAL FILE LOCK)
# ────────────────────────────────────────────
# writer_lock(kind, timeout_sec):
#   - inproc RLock (thread-safe, re-entrant)
#   - optional interprocess flock (Lockfile) via:
#       OROMA_DB_WRITE_FLOCK=1|0
#       OROMA_DB_WRITELOCK_TIMEOUT_SEC
#
# 6) LOCK-RETRY FENSTER (WRITE HOTPATHS)
# ──────────────────────────────────────
# _run_with_lock_retry(fn, retry_sec):
#   - wiederholt Writes bei SQLITE_BUSY/LOCKED bis:
#       OROMA_DB_LOCK_RETRY_SEC
#   - wichtig für Hotpaths (snapchains/metrics/policy_rules), wenn DBWriter nicht aktiv ist
#
# 7) DBWRITER INTEGRATION (SINGLE-WRITER, STUFE C)
# ────────────────────────────────────────────────
# Wenn OROMA_DBW_ENABLE=1:
#   - ausgewählte Writes werden über core.db_writer_client an den DBWriter geroutet
#   - Tags (z.B. "sql_manager.insert_snapchain") ermöglichen Ops-TopTags
#
# STRICT-LOCAL-WRITES (KRITISCH)
#   - OROMA_DBW_STRICT_LOCAL_WRITES=1 (Default)
#   - Managed DBs (Basename): oroma.db, stats.db, knowledge.db, registry.db
#   - Lokale Connections zu managed DBs werden dann als mode=ro geöffnet
#     → lokale Writes sind **hart verhindert** (kein Bypass des Single-Writer-Modells).
#
# 8) SCHEMA / MIGRATIONEN (ADDIV, IDEMPOTENT)
# ────────────────────────────────────────────
# ensure_schema() / _ensure_* helpers:
#   - erstellen Tabellen/Indizes, falls fehlend
#   - aktualisieren Schema defensiv (ohne Datenverlust)
#   - Schema-Ensure kann gecacht werden:
#       OROMA_SCHEMA_CACHE=1|0
#
# 9) ÖFFENTLICHE API (PRAGMATISCHE KERNFUNKTIONEN)
# ───────────────────────────────────────────────
# Connections:
#   - get_conn(db_path=None) -> sqlite3.Connection (dict row_factory)
#   - close_conn(conn) / Context Manager (auto-close)
#
# Schema:
#   - ensure_schema(conn=None)  (idempotent)
#
# Writes (typische Hotpaths, je nach Modus DBWriter oder local + lock-retry):
#   - insert_snapchain(...)
#   - insert_metric(...)
#   - upsert_policy_rule(...) / bulk upserts (policy_rules)
#   - insert_snap_index(...)  (Indexpfad)
#   - update_curriculum_state(...) / fetch_curriculum_state(...)
#
# Reads:
#   - query helpers, row factories, safe JSON parsing utilities
#
# 10) FEHLERFÄLLE & OPS-SICHTBARKEIT
# ──────────────────────────────────
# - DBWriter down → DBWriter-Calls schlagen sichtbar fehl (kein stiller Local-Fallback in Strict-Mode)
# - DB locked → lock-retry bis OROMA_DB_LOCK_RETRY_SEC, danach sichtbarer Fehler
# - Schema drift → defensive Ensure + sichtbare Warnungen (statt Crash)
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os
import sys
import sqlite3
import errno
import threading
import random
import time
import json
import logging
from core.log_guard import log_suppressed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import contextmanager

# Optional: Global Single-Writer (Stufe C)
# ---------------------------------------
# Wenn OROMA_DBW_ENABLE=1, können ausgewählte Heavy-Write-Pfade über den
# DB Writer Daemon geroutet werden. Das ist bewusst "best-effort":
# - fehlender Writer / Socket → sichtbarer Fehlerpfad
# - Reads bleiben lokal (WAL nutzt Vorteil)
try:
    from core import db_writer_client as _dbw  # type: ignore
except Exception:  # pragma: no cover
    _dbw = None

# Optional: POSIX file locks (Linux). Headless-safe; if unavailable, ORÓMA falls back to best-effort retries.
try:
    import fcntl  # type: ignore
    _HAVE_FCNTL = True
except Exception:
    fcntl = None  # type: ignore
    _HAVE_FCNTL = False
from core.log_guard import log_suppressed
import logging

_log = logging.getLogger(__name__)

# ----------------------------- Pfade & RowFactory ----------------------------

def get_base_dir() -> str:
    # Akzeptiert beide ENV-Varianten (z. B. Import-Gate nutzt OROMA_BASE_DIR)
    return os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma"

def get_db_path() -> str:
    return os.path.join(get_base_dir(), "data", "oroma.db")

def _row_factory(cursor: sqlite3.Cursor, row: Tuple[Any, ...]):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, None)
    if v is None:
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return int(default)


def _dbw_timeout_ms(kind: str = "dream") -> int:
    # UI vs Dream/Worker Timeouts trennen.
    k = str(kind).lower().strip()
    if k.startswith("ui"):
        return _env_int("OROMA_DBW_CLIENT_TIMEOUT_MS_UI", 2000)
    return _env_int("OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM", 60000)

class _ClosingConnection(sqlite3.Connection):
    """
    ORÓMA SQLite Connection-Klasse, die beim Verlassen eines `with`-Blocks
    die Connection zuverlässig schließt.

    Motivation:
      - `sqlite3.Connection` als Context-Manager schließt NICHT automatisch.
        Es macht nur commit()/rollback().
      - ORÓMA nutzt an vielen Stellen `with sql_manager.get_conn() as conn:`.
        Ohne Close wächst die Zahl offener Handles (lsof) kontinuierlich an
        und `database is locked` wird wahrscheinlicher.

    Diese Klasse bleibt eine echte sqlite3.Connection (isinstance bleibt True).
    """
    def __exit__(self, exc_type, exc, tb):
        try:
            # Standard-Verhalten: commit/rollback
            super().__exit__(exc_type, exc, tb)
        finally:
            try:
                self.close()
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.sql_manager.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        # False = Exceptions nicht unterdrücken
        return False


def _dbw_enabled() -> bool:
    """True, wenn der globale DBWriter aktiv sein soll."""
    try:
        return _env_bool("OROMA_DBW_ENABLE", False)
    except Exception:
        return False


def _dbw_strict_local_writes() -> bool:
    """
    Phase 1 (DBWriter-only): Wenn aktiv, werden lokale SQLite-Verbindungen für
    vom DBWriter verwaltete DBs nur noch read-only geöffnet. Dadurch sind lokale
    Writes zentral verboten und schlagen sichtbar fehl, statt die Single-Writer-
    Architektur zu unterlaufen.

    Default: 1 sobald OROMA_DBW_ENABLE=1 (kann nur bewusst per ENV deaktiviert werden).
    """
    try:
        if not _dbw_enabled():
            return False
        return _env_bool("OROMA_DBW_STRICT_LOCAL_WRITES", True)
    except Exception:
        return False


def _dbw_is_managed_db(db_path: Optional[str]) -> bool:
    """
    True für DBs, die im Stufe-C Modell ausschließlich über den DBWriter
    geschrieben werden sollen. Die Erkennung ist absichtlich pragmatisch über
    Basenames/Standardpfade, damit auch explizit übergebene Pfade greifen.
    """
    if not db_path:
        return True
    try:
        bp = os.path.basename(str(db_path)).lower()
    except Exception:
        bp = str(db_path).lower()
    return bp in {"oroma.db", "stats.db", "knowledge.db", "registry.db"}



def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Liefert eine SQLite-Connection.

    - db_path=None → Standard-DB aus get_db_path()
    - db_path!=None → expliziter Pfad (wird z.B. von Import/Tools genutzt)

    Robustheit (Locks / Parallel-Writer)
    ------------------------------------
    ORÓMA schreibt parallel (Engine/DreamWorker/Stats/Forget/ExportGate/Tools).
    SQLite erlaubt trotzdem nur einen Writer-Commit gleichzeitig.

    Wir setzen daher:
      • connect(timeout=...)          → wartet beim Verbindungsaufbau/Busy
      • PRAGMA busy_timeout=<ms>      → wartet bei Locks (SQLite_BUSY)
      • optional WAL via OROMA_DB_WAL → bessere Read/Write-Koexistenz

    ENV
    ---
      OROMA_DB_TIMEOUT_SEC=60
      OROMA_DB_BUSY_TIMEOUT_MS=60000
      OROMA_DB_WAL=0|1  (Default 0; wenn 1: journal_mode=WAL + synchronous=NORMAL)
      OROMA_DB_LOCK_RETRY_SEC=60  (kontrolliertes Retry-Fenster für _run_with_lock_retry)
    """
    if db_path is None:
        db_path = get_db_path()

    # Ordner sicherstellen (erste Ausführung ohne data/-Ordner)
    dirn = os.path.dirname(db_path)
    if dirn:
        os.makedirs(dirn, exist_ok=True)

    timeout_sec = _env_int("OROMA_DB_TIMEOUT_SEC", 60)

    # Phase 1 (DBWriter-only): Für vom DBWriter verwaltete DBs werden lokale
    # Connections bei aktivem Strict-Mode nur read-only geöffnet. Dadurch sind
    # lokale Writes zentral verboten; Leser funktionieren weiterhin normal.
    # WICHTIG: Das ist bewusst hart, damit keine verdeckten SQLite-Writes am
    # DBWriter vorbei passieren.
    if _dbw_strict_local_writes() and _dbw_is_managed_db(db_path):
        try:
            db_uri = f"file:{os.path.abspath(str(db_path))}?mode=ro"
            conn = sqlite3.connect(db_uri, timeout=float(timeout_sec), uri=True, check_same_thread=False, factory=_ClosingConnection)
        except Exception as e:
            log_suppressed(
                _log,
                key="sql_manager.dbw.strict.ro.fail",
                msg=f"DBWriter strict mode: read-only open failed for {db_path!r}",
                exc=e,
                level=logging.WARNING,
                interval_s=300,
            )
            raise
    else:
        conn = sqlite3.connect(db_path, timeout=float(timeout_sec), check_same_thread=False, factory=_ClosingConnection)

    # Busy-Timeout (ms) – reduziert "database is locked" in Write-Peaks massiv
    try:
        # Default bewusst hoch: viele ORÓMA-Jobs sind bursty (Orchestrator/Dream/Export/Stats).
        # Zu kleine busy_timeout-Werte führen in der Praxis zu sporadischem "database is locked",
        # obwohl WAL aktiv ist. 60s ist ein guter produktiver Standard.
        busy_ms = _env_int("OROMA_DB_BUSY_TIMEOUT_MS", 60000)
        conn.execute(f"PRAGMA busy_timeout={busy_ms}")
    except Exception as e:
        log_suppressed(_log, key="sql_manager.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # Optional WAL
    # Default: WAL EIN (bessere Parallelität: UI-Reads blockieren nicht bei Writer-Jobs).
    # Abschalten: OROMA_DB_WAL=0
    # (idempotent; Fehler werden ignoriert)
    if _env_bool("OROMA_DB_WAL", True):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    conn.row_factory = _row_factory
    return conn



@contextmanager
def conn_cm(db_path=None):
    """
    Context-manager alias for obtaining a DB connection.

    Many older modules expect `core.sql_manager.conn_cm` to exist.
    We keep this as a thin wrapper around `get_conn()` for backwards
    compatibility.

    Usage:
        from core import sql_manager
        with sql_manager.conn_cm() as conn:
            conn.execute(...)

    Commit/rollback + close are handled by the underlying connection's
    context manager (see _ClosingConnection).
    """
    with get_conn(db_path) as conn:
        yield conn
def _is_lock_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return ("database is locked" in msg) or ("database is busy" in msg) or ("locked" in msg)

def _run_with_lock_retry(fn, total_retry_sec: int):
    """Führt fn() aus und retried kontrolliert bei SQLite-Locks.

    Design:
    - für echte Lock-Kollisionen nicht sofort Daten verlieren
    - Backoff startet klein und capped bei 5s
    - total_retry_sec steuert das Gesamtfenster (Default: OROMA_DB_LOCK_RETRY_SEC=60)

    WICHTIG:
    - fn() sollte pro Attempt eine frische Connection öffnen (kein halb-offener Tx).
    """
    t0 = time.time()
    delay = 0.25
    last_err = None
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last_err = e
            if not _is_lock_error(e):
                raise
            if (time.time() - t0) >= float(total_retry_sec):
                raise
            time.sleep(delay)
            # leichter Jitter, um Thundering Herd zu vermeiden
            delay = min(delay * 1.7 + (random.random() * 0.05), 5.0)
        except Exception:
            raise

# ------------------------- Interprozess-Write-Lock (FLOCK) -------------------------
#
# Problem (aus Live-Logs belegt):
#   - Parallel laufende Writer-Jobs (Orchestrator/Timers/AgentLoop) kollidieren in SQLite,
#     obwohl busy_timeout/WAL/retry existieren → "database is locked".
#
# Warum WAL allein nicht reicht:
#   - WAL verbessert Reader/Writer-Parallelität, aber SQLite erlaubt weiterhin nur **einen Writer**
#     gleichzeitig. Wenn mehrere Prozesse/Threads gleichzeitig schreiben wollen (Rewards, SnapChains,
#     Metrics, Transfer-Snaps …), entstehen Kollisionen.
#
# Elegante Lösung (minimal-invasiv, headless, non-destructive):
#   - Wir serialisieren Writer-KRITISCHE Abschnitte über ein kurzes Interprozess-FLOCK.
#   - Das reduziert Lock-Kollisionen drastisch, ohne die Datenbank-Struktur zu ändern.
#   - Falls fcntl/flock nicht verfügbar ist, fällt ORÓMA automatisch auf Retry-only zurück.
#
# ENV:
#   OROMA_DB_WRITE_FLOCK=0|1                (Default: 1)
#   OROMA_DB_WRITELOCK_TIMEOUT_SEC=30       (Default: 30)
#

# In-Process Write-Lock (Threads)
# -------------------------------
# flock() serialisiert zuverlässig zwischen PROZESSEN, ist aber pro Prozess re-entrant.
# Das bedeutet: Threads innerhalb desselben Prozesses können trotz flock parallel in
# Writer-Pfade laufen → SQLite "database is locked".
#
# Außerdem kann writer_lock() in ORÓMA indirekt verschachtelt aufgerufen werden
# (Helper-Funktion ruft wiederum eine Writer-Funktion). Ein normales Lock() würde
# dann den Thread selbst blockieren (Deadlock). Deshalb: RLock().
_INPROC_WRITELOCK = threading.RLock()

def _db_writelock_path() -> str:
    # Lock-Datei bewusst im data/-Verzeichnis (Backup/Diagnose sichtbar, reboot-stabil)
    base = get_base_dir()
    return os.path.join(base, "data", ".oroma_db_write.lock")

@contextmanager
def writer_lock(kind: str = "write", timeout_sec: Optional[int] = None):
    """Write-Lock (best-effort): In-Process (Threads) + optional Interprozess flock.

    Hintergrund
    -----------
    - SQLite erlaubt weiterhin nur einen Writer-Commit gleichzeitig.
    - WAL verbessert Reader/Writer, löst aber Writer/Writer-Kollisionen nicht.
    - flock() serialisiert gut zwischen Prozessen.
    - flock() ist jedoch pro Prozess re-entrant und verhindert keine Parallel-Writes
      zwischen Threads innerhalb desselben Prozesses.

    Deshalb:
      1) In-Process RLock (thread-sicher + re-entrant → kein Self-Deadlock)
      2) optional Interprozess flock
    """

    to = int(timeout_sec if timeout_sec is not None else _env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30))
    t0 = time.time()

    acquired_inproc = _INPROC_WRITELOCK.acquire(timeout=float(to))
    if not acquired_inproc:
        raise TimeoutError(f"DB write inproc-lock timeout after {to}s (kind={kind})")

    try:
        # Optionaler Interprozess-Lock
        if (not _env_bool("OROMA_DB_WRITE_FLOCK", True)) or (not _HAVE_FCNTL):
            yield
            return

        p = _db_writelock_path()
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
        except Exception:
            yield
            return

        fd = None
        acquired = False
        try:
            fd = open(p, "a+", encoding="utf-8")

            def _holder_preview() -> str:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as _rf:
                        return _rf.read(256).strip()
                except Exception:
                    return ""

            while True:
                try:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                    acquired = True
                    break
                except BlockingIOError:
                    if (time.time() - t0) >= float(to):
                        raise TimeoutError(
                            f"DB write flock timeout after {to}s (kind={kind}) holder={_holder_preview()!r}"
                        )
                    time.sleep(0.05 + (random.random() * 0.03))
                except OSError as e:
                    if getattr(e, "errno", None) in (errno.EWOULDBLOCK, errno.EAGAIN):
                        if (time.time() - t0) >= float(to):
                            raise TimeoutError(
                                f"DB write flock timeout after {to}s (kind={kind}) holder={_holder_preview()!r}"
                            )
                        time.sleep(0.05 + (random.random() * 0.03))
                        continue
                    raise

            waited = time.time() - t0
            if waited >= 0.2:
                log_suppressed(
                    _log,
                    key="sql_manager.writelock.wait.1",
                    msg=f"DB write flock waited {waited:.2f}s (kind={kind})",
                    level=logging.INFO,
                    interval_s=30,
                )

            # Debug-Info in Lockdatei
            try:
                fd.seek(0)
                fd.truncate(0)
                fd.write(
                    json.dumps(
                        {"pid": os.getpid(), "kind": str(kind), "ts": int(time.time()), "argv": sys.argv[:8]},
                        ensure_ascii=False,
                    )
                )
                fd.write("\n")
                fd.flush()
            except Exception:
                pass

            yield
        finally:
            if acquired and fd is not None:
                try:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
                except Exception:
                    pass
            if fd is not None:
                try:
                    fd.close()
                except Exception:
                    pass
    finally:
        try:
            _INPROC_WRITELOCK.release()
        except Exception:
            pass

# Schema-Cache (verhindert, dass ensure_schema() im Hot-Path ständig Write-Locks erzeugt)
_SCHEMA_DONE_FOR: set[str] = set()
_SCHEMA_GUARD = threading.Lock()

def _schema_should_skip(db_path: str) -> bool:
    if _env_bool("OROMA_SCHEMA_CACHE", True):
        with _SCHEMA_GUARD:
            return db_path in _SCHEMA_DONE_FOR
    return False

def _schema_mark_done(db_path: str) -> None:
    if _env_bool("OROMA_SCHEMA_CACHE", True):
        with _SCHEMA_GUARD:
            _SCHEMA_DONE_FOR.add(db_path)

# ----------------------------- Optionale Wrapper-API -------------------------

class SqlManager:
    """
    Schlanke Convenience-API (separate Connections je Aufruf).
    Hinweis: Verwende bevorzugt cur.lastrowid aus execute(), nicht last_insert_rowid().
    """
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or get_db_path()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(
            self._db_path,
            timeout=float(_env_int("OROMA_DB_TIMEOUT_SEC", 60)),
            check_same_thread=False,
            factory=_ClosingConnection,
        )# Busy-Timeout (ms)
        try:
            c.execute(f"PRAGMA busy_timeout={_env_int('OROMA_DB_BUSY_TIMEOUT_MS', 60000)}")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        # Optional WAL
        # Default: WAL EIN (bessere Parallelität: UI-Reads blockieren nicht bei Writer-Jobs).
        # Abschalten: OROMA_DB_WAL=0

        if _env_bool("OROMA_DB_WAL", True):
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:
                log_suppressed(_log, key="sql_manager.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        c.row_factory = _row_factory
        return c

    def execute(self, sql: str, params: Tuple[Any, ...] = (), commit: bool = False):
        with self._conn() as c:
            cur = c.execute(sql, params)
            if commit:
                c.commit()
            return cur  # cur.lastrowid bei INSERTs verwenden

    def insert_and_get_id(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[int]:
        def _do() -> int:
            with self._conn() as c:
                cur = c.execute(sql, params)
                c.commit()
                return int(cur.lastrowid)

        try:
            # Default bewusst großzügiger: parallel laufende ORÓMA-Worker können
            # kurzzeitig lange Write-Tx halten (z. B. Bulk-Imports/Kompression).
            retry_sec = _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)
            return int(_run_with_lock_retry(_do, retry_sec))
        except Exception as e:
            log_suppressed(_log, key="sql_manager.ret.6", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def fetchone(self, sql: str, params: Tuple[Any, ...] = ()):
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Tuple[Any, ...] = ()):
        return self.execute(sql, params).fetchall()

# ----------------------------- Schema-SQL ------------------------------------

_SCHEMA_SNAPCHAINS = """
CREATE TABLE IF NOT EXISTS snapchains (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  quality REAL NOT NULL DEFAULT 0.0,
  blob BLOB NOT NULL,
  exported INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  origin TEXT DEFAULT NULL,
  gap_flag INTEGER NOT NULL DEFAULT 0,
  notes TEXT DEFAULT NULL,
  namespace TEXT DEFAULT NULL,
  source_id TEXT DEFAULT NULL,
  version TEXT DEFAULT 'v3.5',
  weight REAL NOT NULL DEFAULT 1.0
);
"""

# NEU: SnapIndex – dünner Snap-Metadaten-Index (Norm-Cache / Fingerprint)
_SCHEMA_SNAP_INDEX = """
CREATE TABLE IF NOT EXISTS snap_index (
  id INTEGER PRIMARY KEY,
  ts REAL,
  source TEXT,
  privacy_tier TEXT,
  feature_dim INTEGER,
  l2_norm REAL,
  fingerprint TEXT UNIQUE,
  ref_table TEXT,
  ref_id INTEGER,
  ref_key TEXT,
  payload BLOB
)
"""
_SCHEMA_SNAP_INDEX_IX_REF = "CREATE INDEX IF NOT EXISTS ix_snap_index_ref ON snap_index(ref_table, ref_id)"
_SCHEMA_SNAP_INDEX_IX_TS  = "CREATE INDEX IF NOT EXISTS ix_snap_index_ts  ON snap_index(ts)"

_SCHEMA_RULES = """
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 0.0,
  active INTEGER NOT NULL DEFAULT 1,
  exported INTEGER NOT NULL DEFAULT 0,
  created_at REAL,
  updated_at REAL
);
"""

# ──────── NEU: PolicyEngine State→Action-Tabelle (v3.7.1+ kompatibel zu v3.8-r3) ────────
_SCHEMA_POLICY_RULES = """
CREATE TABLE IF NOT EXISTS policy_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace   TEXT NOT NULL,
  state_hash  TEXT NOT NULL,
  action      TEXT NOT NULL,
  n    INTEGER NOT NULL DEFAULT 0,
  pos  INTEGER NOT NULL DEFAULT 0,
  neg  INTEGER NOT NULL DEFAULT 0,
  draw INTEGER NOT NULL DEFAULT 0,
  q    REAL    NOT NULL DEFAULT 0.0,
  last_ts INTEGER,
  centroid TEXT
);
"""

_SCHEMA_METRICS = """
CREATE TABLE IF NOT EXISTS metrics (
  key TEXT NOT NULL,
  ts INTEGER NOT NULL,
  value REAL NOT NULL
);
"""

_SCHEMA_MODELS = """
CREATE TABLE IF NOT EXISTS models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task TEXT NOT NULL,
  family TEXT,
  version TEXT DEFAULT 'v3.5',
  input_size TEXT,
  preproc_json TEXT,
  postproc_json TEXT,
  labels_txt TEXT,
  hef_path TEXT,
  source_hash TEXT,
  calib_hash TEXT,
  created_at INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active'
);
"""

_SCHEMA_QUALITY_HISTORY = """
CREATE TABLE IF NOT EXISTS quality_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapchain_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  quality REAL NOT NULL,
  FOREIGN KEY(snapchain_id) REFERENCES snapchains(id)
);
"""

_SCHEMA_REWARDS_LOG = """
CREATE TABLE IF NOT EXISTS rewards_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  source TEXT NOT NULL,
  episode_id INTEGER,
  step INTEGER,
  reward REAL NOT NULL,
  raw TEXT,
  tag TEXT
);
"""

_SCHEMA_CURIOSITY_LOG = """
CREATE TABLE IF NOT EXISTS curiosity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  source TEXT,
  signal REAL NOT NULL,
  raw TEXT,
  tag TEXT
);
"""

# Replay-Logging (UI /replay/) -------------------------------------------------
_SCHEMA_REPLAY_LOG = """
CREATE TABLE IF NOT EXISTS replay_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_id TEXT NOT NULL,
  ts_run INTEGER NOT NULL,
  steps INTEGER DEFAULT 0,
  speed REAL DEFAULT 1.0,
  status TEXT DEFAULT '',
  info TEXT DEFAULT ''
);
"""

_SCHEMA_REPLAY_LOG_IDX = """
CREATE INDEX IF NOT EXISTS idx_replay_log_ts_run ON replay_log(ts_run);
CREATE INDEX IF NOT EXISTS idx_replay_log_chain_id ON replay_log(chain_id);
"""


_SCHEMA_META_SNAPS = """
CREATE TABLE IF NOT EXISTS meta_snaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT,
  score REAL,
  sources TEXT
);
"""

# Patch 1 – Curriculum/Calculator
_SCHEMA_TRANSFER_SNAPS = """
CREATE TABLE IF NOT EXISTS transfer_snaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  sequence TEXT NOT NULL,
  pattern TEXT,
  score REAL DEFAULT 0.0,
  marked INTEGER DEFAULT 0,
  mark_ts INTEGER
);
"""

_SCHEMA_CALCULATOR_TASKS = """
CREATE TABLE IF NOT EXISTS calculator_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  level INTEGER NOT NULL,
  expr TEXT NOT NULL,
  truth REAL NOT NULL
);
"""

_SCHEMA_CALCULATOR_RESULTS = """
CREATE TABLE IF NOT EXISTS calculator_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  got REAL NOT NULL,
  correct INTEGER NOT NULL,
  reward REAL NOT NULL,
  error_type TEXT,
  FOREIGN KEY(task_id) REFERENCES calculator_tasks(id)
);
"""

_SCHEMA_SCICALC_RESULTS = """
CREATE TABLE IF NOT EXISTS scicalc_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  expr TEXT,
  method TEXT,
  input TEXT,
  result TEXT,
  error TEXT
);
"""

# Patch 2 – Empathy/Coverage
_SCHEMA_EMPATHY_SNAPS = """
CREATE TABLE IF NOT EXISTS empathy_snaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  mood TEXT NOT NULL,
  score REAL NOT NULL
);
"""

_SCHEMA_COVERAGE_LOG = """
CREATE TABLE IF NOT EXISTS coverage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  coverage REAL NOT NULL,
  active INTEGER NOT NULL,
  total INTEGER NOT NULL
);
"""

# Patch 2.3 – Coverage Window (30d)
# -----------------------------------------------------------------------------
# Motivation (2026-01):
#   coverage_log (active/total) fällt über Monate stark ab, weil total monoton wächst
#   (non-destruktive Archivierung/Kompression). Für die Learning-UI ist deshalb
#   zusätzlich eine Fenster-Variante sinnvoll: active_30d / total_30d.
#
# Design:
#   - Neue Tabelle coverage_log_30d (gleiche Spalten wie coverage_log).
#   - Keine Änderung an coverage_log (Backwards compatible).
#   - Hooks schreiben beide Reihen (best-effort).
#   - StatsSnapshot/UI können windowed Coverage als eigene Serie darstellen.
# -----------------------------------------------------------------------------
_SCHEMA_COVERAGE_LOG_30D = """
CREATE TABLE IF NOT EXISTS coverage_log_30d (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  coverage REAL NOT NULL,
  active INTEGER NOT NULL,
  total INTEGER NOT NULL
);
"""


# v3.6 – Hypothesen
_SCHEMA_HYPOTHESES = """
CREATE TABLE IF NOT EXISTS hypotheses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  score REAL NOT NULL DEFAULT 0.0,
  confidence REAL NOT NULL DEFAULT 0.0,
  plan TEXT,
  last_tested INTEGER,
  meta TEXT,
  created INTEGER NOT NULL
);
"""

# Patch 2 – SetCalc Logging (NEU in v3.6-P2)
_SCHEMA_SETCALC_LOG = """
CREATE TABLE IF NOT EXISTS setcalc_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  op TEXT NOT NULL,
  setA TEXT,
  setB TEXT,
  result TEXT
);
"""

_SCHEMA_AUDIO_STUDENT = """
CREATE TABLE IF NOT EXISTS audio_student_pairs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  source TEXT NOT NULL,
  transcript_teacher TEXT NOT NULL,
  transcript_student TEXT,
  distance REAL,
  feat_json TEXT,
  meta_json TEXT
);
"""

_SCHEMA_SETCALC_IDX = """
CREATE INDEX IF NOT EXISTS idx_setcalc_log_ts ON setcalc_log(ts);
"""

# v3.7 – Curriculum State (NEU)
_SCHEMA_CURRICULUM_STATE = """
CREATE TABLE IF NOT EXISTS curriculum_state (
  id INTEGER PRIMARY KEY CHECK (id=1),
  stage INTEGER NOT NULL DEFAULT 1,
  progress TEXT,
  window  TEXT,
  last_update INTEGER
);
"""

# v3.7.4 – Dream-State / Worker-Checkpoint (NEU)
# -----------------------------------------------------------------------------
# Zweck
#   Kleine Key/Value-Tabelle, um DreamWorker-Schritte idempotent zu machen.
#   Beispiel: ObjectExtractor nur laufen lassen, wenn seit dem letzten Run
#   neue relevante SceneGraphs/ObjectGraphs hinzugekommen sind.
#
# Design
#   - key    : stabiler Schlüssel (z.B. "object_extractor:last_scenegraph_id:object:auto:")
#   - value  : Text (typisch int als String)
#   - ts     : Unix-Zeitstempel (Last-Update)
#
# Robustheit
#   - bewusst minimal gehalten (keine Foreign Keys, keine komplexen Indizes),
#     damit es auch auf Pi/SQLite zuverlässig bleibt.
_SCHEMA_DREAM_STATE = """
CREATE TABLE IF NOT EXISTS dream_state (
  key TEXT PRIMARY KEY,
  value TEXT,
  ts INTEGER
);
"""


# Episodisches Gedächtnis – NEU v3.7.3
_SCHEMA_EPISODES = """
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start INTEGER NOT NULL,
  ts_end INTEGER,
  kind TEXT NOT NULL,
  source TEXT,
  label TEXT,
  meta_json TEXT
);
"""

_SCHEMA_EPISODE_EVENTS = """
CREATE TABLE IF NOT EXISTS episode_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  ref_table TEXT,
  ref_id INTEGER,
  meta_json TEXT,
  FOREIGN KEY(episode_id) REFERENCES episodes(id)
);
"""

_SCHEMA_EPISODIC_METRICS = """
CREATE TABLE IF NOT EXISTS episodic_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  key TEXT NOT NULL,
  value REAL NOT NULL,
  FOREIGN KEY(episode_id) REFERENCES episodes(id)
);
"""
# -----------------------------------------------------------------------------
# ObjectGraph – explizite Objekt- und Relationsschicht (2.5D / 3D SnapSpace)
# -----------------------------------------------------------------------------

_SCHEMA_OBJECT_NODES = """
CREATE TABLE IF NOT EXISTS object_nodes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,
  label      TEXT NOT NULL,
  meta_json  TEXT,
  created_ts INTEGER NOT NULL
);
"""

_SCHEMA_OBJECT_RELATIONS = """
CREATE TABLE IF NOT EXISTS object_relations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  a_id            INTEGER NOT NULL,
  relation        TEXT    NOT NULL,
  b_id            INTEGER NOT NULL,
  confidence      REAL    NOT NULL DEFAULT 1.0,
  source_scene_id INTEGER,
  ts              INTEGER NOT NULL,
  notes           TEXT,
  FOREIGN KEY(a_id) REFERENCES object_nodes(id),
  FOREIGN KEY(b_id) REFERENCES object_nodes(id)
);
"""

# Zusätzliche sinnvolle Indizes (Performance)
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapchains_status ON snapchains(status)",
    "CREATE INDEX IF NOT EXISTS idx_snapchains_origin ON snapchains(origin)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_key_ts ON metrics(key, ts)",
    "CREATE INDEX IF NOT EXISTS idx_quality_history_snap_ts ON quality_history(snapchain_id, ts)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_ns_state_action ON policy_rules(namespace, state_hash, action)",
    # NEU: SnapIndex-Indices (Zeit + Source)
    "CREATE INDEX IF NOT EXISTS idx_snap_ts ON snap_index(ts)",
    "CREATE INDEX IF NOT EXISTS idx_snap_src ON snap_index(source)",
    "CREATE INDEX IF NOT EXISTS idx_episode_events_episode_ts ON episode_events(episode_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_episodic_metrics_episode_ts ON episodic_metrics(episode_id, ts)",
    # NEU: ObjectGraph-Indizes
    "CREATE INDEX IF NOT EXISTS idx_object_nodes_kind   ON object_nodes(kind)",
    "CREATE INDEX IF NOT EXISTS idx_object_nodes_label  ON object_nodes(label)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_object_nodes_unique_kind_label ON object_nodes(kind, label)",
    "CREATE INDEX IF NOT EXISTS idx_object_relations_a   ON object_relations(a_id)",
    "CREATE INDEX IF NOT EXISTS idx_object_relations_b   ON object_relations(b_id)",
    "CREATE INDEX IF NOT EXISTS idx_object_relations_rel ON object_relations(relation)",
    "CREATE INDEX IF NOT EXISTS idx_object_relations_ts  ON object_relations(ts)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_object_relations_unique_triplet ON object_relations(a_id, relation, b_id)",
]

def _ensure_episodic_tables(conn: sqlite3.Connection) -> None:
    """
    Stellt sicher, dass episodes / episode_events / episodic_metrics das neue Schema haben.

    Strategie:
      - Wenn Tabelle fehlt → normal CREATE TABLE.
      - Wenn Tabelle existiert, aber keine passenden Spalten, und LEER ist → DROP + CREATE.
      - Wenn Tabelle existiert und Daten enthält → nicht anfassen, nur warnen.
        (Bei dir sind episodes/episode_events/episodic_metrics aktuell leer → sicher.)
    """
    def _cols(table: str) -> List[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except Exception as e:
            log_suppressed(_log, key="sql_manager.ret.7", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return []
        out: List[str] = []
        for r in rows or []:
            if isinstance(r, dict) or hasattr(r, "keys"):
                name = r.get("name")
            else:
                name = r[1]
            if name:
                out.append(str(name))
        return out

    def _count_rows(table: str) -> int:
        try:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            return int(row["n"]) if row and "n" in row else 0
        except Exception as e:
            log_suppressed(_log, key="sql_manager.ret.8", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return 0

    # episodes
    cols = _cols("episodes")
    required = {"id", "ts_start", "ts_end", "kind", "source", "label", "meta_json"}
    if not cols:
        # Tabelle existiert nicht → normal anlegen
        conn.execute(_SCHEMA_EPISODES)
    elif not required.issubset(set(cols)):
        if _count_rows("episodes") == 0:
            print("[sql_manager] Hinweis: episodes hat altes Schema, ist leer → DROP + CREATE.")
            conn.execute("DROP TABLE episodes")
            conn.execute(_SCHEMA_EPISODES)
        else:
            print("[sql_manager] WARN: episodes hat altes Schema mit Daten → Schema bleibt unverändert.")

    # episode_events
    cols = _cols("episode_events")
    required = {"id", "episode_id", "ts", "event_type", "ref_table", "ref_id", "meta_json"}
    if not cols:
        conn.execute(_SCHEMA_EPISODE_EVENTS)
    elif not required.issubset(set(cols)):
        if _count_rows("episode_events") == 0:
            print("[sql_manager] Hinweis: episode_events hat altes Schema, ist leer → DROP + CREATE.")
            conn.execute("DROP TABLE episode_events")
            conn.execute(_SCHEMA_EPISODE_EVENTS)
        else:
            print("[sql_manager] WARN: episode_events hat altes Schema mit Daten → Schema bleibt unverändert.")

    # episodic_metrics
    cols = _cols("episodic_metrics")
    required = {"id", "episode_id", "ts", "key", "value"}
    if not cols:
        conn.execute(_SCHEMA_EPISODIC_METRICS)
    elif not required.issubset(set(cols)):
        if _count_rows("episodic_metrics") == 0:
            print("[sql_manager] Hinweis: episodic_metrics hat altes Schema, ist leer → DROP + CREATE.")
            conn.execute("DROP TABLE episodic_metrics")
            conn.execute(_SCHEMA_EPISODIC_METRICS)
        else:
            print("[sql_manager] WARN: episodic_metrics hat altes Schema mit Daten → Schema bleibt unverändert.")

def _migrate_transfer_snaps(conn: sqlite3.Connection) -> None:
    """
    Stellt sicher, dass transfer_snaps das erweiterte Schema benutzt, das von
    core/transfer_engine.py erwartet wird.

    Ziel-Schema:
      - id INTEGER PRIMARY KEY AUTOINCREMENT
      - ts INTEGER NOT NULL
      - sequence TEXT NOT NULL
      - pattern TEXT
      - score REAL DEFAULT 0.0
      - marked INTEGER DEFAULT 0
      - mark_ts INTEGER

    Vorgehen:
      - PRAGMA table_info(transfer_snaps) lesen.
      - Falls Tabelle fehlt → nichts tun (CREATE folgt über _SCHEMA_TRANSFER_SNAPS).
      - Fehlende Spalten idempotent via ALTER TABLE ergänzen.
    """
    try:
        rows = conn.execute("PRAGMA table_info(transfer_snaps)").fetchall()
    except Exception:
        # Tabelle existiert noch nicht – wird später über _SCHEMA_TRANSFER_SNAPS erzeugt.
        return

    # PRAGMA table_info liefert (mit unserer row_factory) dicts:
    #   {"cid":..., "name":..., "type":..., "notnull":..., "dflt_value":..., "pk":...}
    cols = set()
    for r in rows or []:
        if isinstance(r, dict) or hasattr(r, "keys"):
            name = r.get("name")
        else:
            # Fallback, falls row_factory jemals geändert wird
            name = r[1]
        if name:
            cols.add(str(name))

    if "score" not in cols:
        conn.execute("ALTER TABLE transfer_snaps ADD COLUMN score REAL DEFAULT 0.0")
    if "marked" not in cols:
        conn.execute("ALTER TABLE transfer_snaps ADD COLUMN marked INTEGER DEFAULT 0")
    if "mark_ts" not in cols:
        conn.execute("ALTER TABLE transfer_snaps ADD COLUMN mark_ts INTEGER")

# ----------------------- Calculator JSON-Spalten (NEU) -----------------------

def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """
    Prüft, ob eine Spalte in einer Tabelle existiert.

    Hintergrund / Bugfix (2026-01):
      - sqlite3.Row hat keys(), aber KEIN .get().
      - Die frühere Heuristik "hasattr(r,'keys') -> r.get('name')" hat daher
        AttributeError ausgelöst, wurde von guard geloggt, und führte dazu,
        dass _col_exists() fälschlich False lieferte.
      - Folge: ensure_schema() führte trotzdem ALTER TABLE ... ADD COLUMN aus →
        duplicate column name + unnötige DDL-Locks (verschärft database-locked).

    Diese Version ist robust für:
      - dict
      - sqlite3.Row
      - tuple/list
    """
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for r in rows or []:
            name = None
            # dict
            if isinstance(r, dict):
                name = r.get("name")
            else:
                # sqlite3.Row (Mapping/Sequence) oder tuple/list
                try:
                    name = r["name"]  # sqlite3.Row unterstützt das
                except Exception:
                    try:
                        name = r[1]      # Fallback: (cid, name, type, ...)
                    except Exception:
                        name = None
            if name == col:
                return True
    except Exception as e:
        log_suppressed(_log, key="sql_manager.pass.9", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    return False

def ensure_calculator_json_columns() -> None:
    """
    Ergänzt TEXT-Spalten für vollständige Vektorwerte als JSON-String:
      • calculator_tasks.truth_json  (optional, parallel zu truth REAL)
      • calculator_results.got_json  (optional, parallel zu got REAL)
    Idempotent: ALTER TABLE wird nur ausgeführt, wenn Spalten fehlen.
    """
    with get_conn() as conn:
        try:
            if not _col_exists(conn, "calculator_tasks", "truth_json"):
                conn.execute("ALTER TABLE calculator_tasks ADD COLUMN truth_json TEXT")
        except Exception as e:
            # bereits vorhanden oder ältere SQLite-Version → ignorieren
            log_suppressed(
                logging.getLogger(__name__),
                key="core.sql_manager.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
        try:
            if not _col_exists(conn, "calculator_results", "got_json"):
                conn.execute("ALTER TABLE calculator_results ADD COLUMN got_json TEXT")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.10", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        conn.commit()

# ----------------------------- Schema anwenden -------------------------------

def ensure_schema(db_path: Optional[str] = None) -> None:
    """
    Stellt sicher, dass das Schema vorhanden ist.

    - db_path=None  → Standard-DB aus get_db_path()
    - db_path!=None → expliziter Pfad (für Tools wie object_extractor)
    """
    db_path_eff = db_path or get_db_path()
    if _schema_should_skip(db_path_eff):
        return

    schemas = [
        _SCHEMA_SNAPCHAINS,
        _SCHEMA_SNAP_INDEX,                 # NEU: SnapIndex
        _SCHEMA_SNAP_INDEX_IX_REF,
        _SCHEMA_SNAP_INDEX_IX_TS,
        _SCHEMA_RULES,
        _SCHEMA_POLICY_RULES,               # NEU
        _SCHEMA_METRICS,
        _SCHEMA_MODELS,
        _SCHEMA_QUALITY_HISTORY,
        _SCHEMA_REWARDS_LOG,
        _SCHEMA_CURIOSITY_LOG,
        _SCHEMA_REPLAY_LOG,
        _SCHEMA_REPLAY_LOG_IDX,
        _SCHEMA_META_SNAPS,
        _SCHEMA_TRANSFER_SNAPS,
        _SCHEMA_CALCULATOR_TASKS,
        _SCHEMA_CALCULATOR_RESULTS,
        _SCHEMA_SCICALC_RESULTS,
        _SCHEMA_EMPATHY_SNAPS,
        _SCHEMA_COVERAGE_LOG,
        _SCHEMA_COVERAGE_LOG_30D,
        _SCHEMA_HYPOTHESES,
        _SCHEMA_SETCALC_LOG,
        _SCHEMA_AUDIO_STUDENT,              # Audio-Lernpaare
        _SCHEMA_SETCALC_IDX,
        _SCHEMA_CURRICULUM_STATE,           # v3.7
        _SCHEMA_DREAM_STATE,                # v3.7.4 – DreamWorker Checkpoints
        _SCHEMA_EPISODES,                   # Episoden-Kopf (2.5D)
        _SCHEMA_EPISODE_EVENTS,             # Episoden-Events
        _SCHEMA_EPISODIC_METRICS,           # Episoden-Metriken
        _SCHEMA_OBJECT_NODES,
        _SCHEMA_OBJECT_RELATIONS,
    ]
    with get_conn(db_path_eff) as conn:
        # Basis-Schemata (CREATE IF NOT EXISTS)
        #
        # WICHTIG / BUGFIX (2026-02-21):
        #   Einige Schema-Strings enthalten mehrere SQL-Statements (z.B. CREATE TABLE + CREATE INDEX).
        #   sqlite3.Connection.execute() erlaubt jedoch genau *ein* Statement pro Aufruf und wirft sonst:
        #     "You can only execute one statement at a time."
        #
        #   Deshalb führen wir Multi-Statement-Schema-Blöcke via executescript() aus.
        #   Single-Statement-Blöcke bleiben bei execute() (schneller, sauberere Fehlerpositionen).
        #
        def _exec_schema(stmt: str) -> None:
            s = (stmt or '').strip()
            if not s:
                return
            # grobe Multi-Statement-Erkennung: mehr als 1 nicht-leeres Segment bei ';'
            parts = [x.strip() for x in s.split(';') if x.strip()]
            if len(parts) > 1:
                conn.executescript(s if s.endswith(';') else (s + ';'))
            else:
                conn.execute(s)

        for s in schemas:
            _exec_schema(s)
        # NEU: TransferSnaps-Schema ggf. erweitern (score/marked/mark_ts)
        try:
            _migrate_transfer_snaps(conn)
        except Exception as e:
            print(f"[sql_manager] WARN: _migrate_transfer_snaps() fehlgeschlagen: {e}")
        # Episoden-Tabellen ggf. auf neues Schema migrieren
        try:
            _ensure_episodic_tables(conn)
        except Exception as e:
            print(f"[sql_manager] WARN: _ensure_episodic_tables() fehlgeschlagen: {e}")

        # Patch 2.2 Spalten (idempotent, falls altes Schema)
        # ------------------------------------------------------------------
        # WICHTIG:
        #   Diese Migration muss *innerhalb* der offenen DB-Connection laufen.
        #   (In älteren ZIPs war hier ein Einrückungsfehler, wodurch die ALTERs
        #   nicht zuverlässig ausgeführt wurden.)
        #
        # MetaSnaps-only SnapIndex:
        #   Wir halten snap_index als dünnen Cache/Index und referenzieren die
        #   kanonische Tabelle via (ref_table, ref_id, ref_key).
        # ------------------------------------------------------------------
        # --- snap_index: optionale Ref-Spalten (für Normalform-Brücke) ---
        # NOTE: SQLite kennt kein `ADD COLUMN IF NOT EXISTS`.
        #       Damit `ensure_schema()` nicht permanent `duplicate column` produziert,
        #       prüfen wir vorher die vorhandenen Spalten.
        try:
            _cols_snap_index = {r[1] for r in conn.execute("PRAGMA table_info(snap_index)").fetchall()}
        except Exception:
            _cols_snap_index = set()

        _snap_index_add_cols = {
            "ref_table": "ALTER TABLE snap_index ADD COLUMN ref_table TEXT",
            "ref_id": "ALTER TABLE snap_index ADD COLUMN ref_id INTEGER",
            "ref_key": "ALTER TABLE snap_index ADD COLUMN ref_key TEXT",
        }
        for _cname, _sql in _snap_index_add_cols.items():
            if _cname in _cols_snap_index:
                continue
            try:
                conn.execute(_sql)
            except sqlite3.OperationalError as e:
                # DB kann in einem Zwischenstand sein (z. B. parallele Migration).
                # In diesem Fall ist "duplicate column name" erwartbar und soll
                # NICHT als Stacktrace im Log landen.
                _msg = str(e).lower()
                if "duplicate column name" in _msg or "already exists" in _msg:
                    continue
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.sql_manager.pass.2",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        try:
            if not _col_exists(conn, "snapchains", "weight"):
                conn.execute("ALTER TABLE snapchains ADD COLUMN weight REAL DEFAULT 1.0")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.11", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # --- meta_snaps: sources (Brücke: MetaSnap -> ursprüngliche SnapChain-IDs) ---
        # Idempotent: nur hinzufügen, wenn Spalte fehlt (vermeidet duplicate-column Locks/Noise)
        try:
            if not _col_exists(conn, "meta_snaps", "sources"):
                conn.execute("ALTER TABLE meta_snaps ADD COLUMN sources TEXT")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.12", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # --- snap_index: Indizes (nach Migration erneut versuchen) ---
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS ix_snap_index_ref ON snap_index(ref_table, ref_id)")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.13", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS ix_snap_index_ts ON snap_index(ts)")
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.14", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # sonstige Indizes
        for idx in _INDEXES:
            try:
                conn.execute(idx)
            except sqlite3.IntegrityError as e:
                # Produktsicherheit: UNIQUE-Index kann fehlschlagen, wenn bereits Duplikate existieren.
                # Das ist kein Crash-Grund; wir loggen sichtbar, damit ein Dedupe gezielt erfolgen kann.
                if "idx_object_relations_unique_triplet" in str(idx):
                    log_suppressed(
                        _log,
                        key="sql_manager.object_rel.unique.fail",
                        msg="Unique-Index für object_relations konnte wegen Duplikaten nicht angelegt werden (a_id,relation,b_id).",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=600,
                    )
                elif "idx_object_nodes_unique_kind_label" in str(idx):
                    log_suppressed(
                        _log,
                        key="sql_manager.object_nodes.unique.fail",
                        msg="Unique-Index für object_nodes konnte wegen Duplikaten nicht angelegt werden (kind,label).",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=600,
                    )
                else:
                    log_suppressed(_log, key="sql_manager.pass.15", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            except Exception as e:
                log_suppressed(_log, key="sql_manager.pass.15", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # v3.7 – Initial-Row für curriculum_state (id=1), falls fehlend
        try:
            row = conn.execute("SELECT 1 FROM curriculum_state WHERE id=1").fetchone()
            if not row:
                now = int(time.time())
                conn.execute(
                    "INSERT INTO curriculum_state (id, stage, progress, window, last_update) VALUES (1, 1, ?, ?, ?)",
                    ("{}", "{}", now)
                )
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.16", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # NEU: Calculator JSON-Spalten sicherstellen (idempotent)
        try:
            ensure_calculator_json_columns()
        except Exception as e:
            log_suppressed(_log, key="sql_manager.pass.17", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        conn.commit()

    _schema_mark_done(db_path_eff)
    print("[sql_manager] ensure_schema() OK")

# ----------------------------- Inserts ---------------------------------------

# NEU: SnapChain Insert (+ Aliase für Legacy-Calls)
def insert_snapchain(data: Dict[str, Any]) -> Optional[int]:
    """
    Fügt eine SnapChain in die Tabelle 'snapchains' ein.
    Erwartet ein Dict mit optionalen Feldern:
        ts, quality, blob, exported, status, origin, gap_flag,
        notes, namespace, source_id, version, weight
    Pflicht: blob (bytes oder memoryview)

    Robustheit:
      - nutzt busy_timeout/timeout aus get_conn()
      - zusätzlich: kontrolliertes Retry-Fenster bei "database is locked"
        (ENV: OROMA_DB_LOCK_RETRY_SEC, Default 60s)
    """
    try:
        retry_sec = _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)

        def _do_once() -> int:
            # Stufe C (DBWriter): globaler Single-Writer → vermeidet flock/SQLite-Writelocks.
            # Fallback bleibt lokal (writer_lock + get_conn), falls DBWriter deaktiviert/nicht verfügbar ist.
            if _dbw_enabled():
                sql_stmt = """
                        INSERT INTO snapchains
                          (ts, quality, blob, exported, status, origin,
                           gap_flag, notes, namespace, source_id, version, weight)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """
                params = (
                    int(data.get("ts", int(time.time()))),
                    float(data.get("quality", 0.0) or 0.0),
                    data.get("blob"),
                    int(data.get("exported", 0) or 0),
                    str(data.get("status", "active") or "active"),
                    str(data.get("origin", "") or ""),
                    int(data.get("gap_flag", 0) or 0),
                    (str(data.get("notes")) if data.get("notes") is not None else None),
                    (str(data.get("namespace")) if data.get("namespace") is not None else None),
                    (int(data.get("source_id")) if data.get("source_id") is not None else None),
                    (str(data.get("version")) if data.get("version") is not None else None),
                    (float(data.get("weight")) if data.get("weight") is not None else None),
                )
                return int(getattr(_dbw, "exec_lastrowid")(sql_stmt, params=params, tag="sql_manager.insert_snapchain", priority="normal", timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS","60000")), db="oroma"))

            with writer_lock("insert_snapchain"):
                with get_conn() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO snapchains
                          (ts, quality, blob, exported, status, origin,
                           gap_flag, notes, namespace, source_id, version, weight)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(data.get("ts", int(time.time()))),
                            float(data.get("quality", 0.0)),
                            data["blob"],  # Pflichtfeld
                            int(data.get("exported", 0)),
                            data.get("status", "active"),
                            data.get("origin", "ui"),
                            int(data.get("gap_flag", 0)),
                            data.get("notes"),
                            data.get("namespace"),
                            data.get("source_id"),
                            data.get("version", "v3.7"),
                            float(data.get("weight", 1.0)),
                        ),
                    )
                    return int(cur.lastrowid)

        return int(_run_with_lock_retry(_do_once, retry_sec))
    except Exception as e:
        print(f"[sql_manager] Fehler insert_snapchain: {e}")
        return None

# Aliase für Legacy-Code (z. B. pong_arena / vs_ui)
insert_chain = insert_snapchain
insert_chain_quick = insert_snapchain

# NEU: SnapIndex – Insert/Upsert basierend auf Fingerprint
def insert_snap_index(
    ts: Optional[float] = None,
    source: str = "",
    privacy_tier: Optional[str] = None,
    feature_dim: int = 0,
    l2_norm: float = 0.0,
    fingerprint: Optional[str] = None,
    payload: Optional[bytes] = None,
    dedup: bool = True,
) -> Optional[int]:
    """
    Fügt einen Eintrag in 'snap_index' ein oder aktualisiert ihn (Fingerprint-Index).

    Parameter
    ---------
    ts : float|None
        Zeitstempel (Sekunden, float). Default: time.time().
    source : str
        Quelle des Snaps (z.B. 'replay', 'ask', 'game:snake').
    privacy_tier : str|None
        Privacy-Klasse ('public' | 'internal' | 'private' ...), optional.
    feature_dim : int
        Feature-Dimension der zugrundeliegenden Snap-Centroid.
    l2_norm : float
        L2-Norm der Centroid (Norm-Cache, passt zu SnapPattern/snap v1.1).
    fingerprint : str|None
        Stabiler Fingerprint des Snaps (z.B. SHA1-Short); bei None findet kein
        Dedupe statt.
    payload : bytes|None
        Dünnes Payload-Blob (z.B. Snap.to_blob() oder kompaktes Dict-JSON).
    dedup : bool
        Wenn True und fingerprint gesetzt:
          • existierender Eintrag wird aktualisiert (ts/source/…),
            ID bleibt erhalten (Upsert-Semantik).

    Rückgabewert
    ------------
    int|None : ID des SnapIndex-Eintrags oder None bei Fehler.

    Robustheit (Locks)
    ------------------
    snap_index wird in High-Rate Pfaden genutzt (Dedupe/Indexierung). Daher:
      - busy_timeout/timeout via get_conn()
      - zusätzlich Retry-Fenster (ENV OROMA_DB_LOCK_RETRY_SEC, Default 60s)
    """
    try:
        retry_sec = _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)
        now = float(ts if ts is not None else time.time())

        def _do_once() -> int:
            with get_conn() as conn:
                # Dedupe über Fingerprint (optional)
                if dedup and fingerprint:
                    row = conn.execute(
                        "SELECT id FROM snap_index WHERE fingerprint=?",
                        (fingerprint,),
                    ).fetchone()
                    if row:
                        sid = int(row["id"] if isinstance(row, dict) else row[0])
                        conn.execute(
                            """UPDATE snap_index
                                   SET ts=?, source=?, privacy_tier=?, feature_dim=?, l2_norm=?, payload=?
                                 WHERE id=?""",
                            (
                                now,
                                source or "",
                                privacy_tier,
                                int(feature_dim),
                                float(l2_norm),
                                payload,
                                sid,
                            ),
                        )
                        conn.commit()
                        return sid

                # Neu-Insert: Fingerprint darf NULL sein, dann kein Dedupe
                cur = conn.execute(
                    """INSERT INTO snap_index
                         (ts, source, privacy_tier, feature_dim, l2_norm, fingerprint, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now,
                        source or "",
                        privacy_tier,
                        int(feature_dim),
                        float(l2_norm),
                        fingerprint,
                        payload,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

        return int(_run_with_lock_retry(_do_once, retry_sec))
    except Exception as e:
        print(f"[sql_manager] Fehler insert_snap_index: {e}")
        return None

def fetch_snap_index_by_fingerprint(fingerprint: str) -> Optional[Dict[str, Any]]:
    """
    Holt einen SnapIndex-Eintrag anhand des Fingerprints.
    Rückgabe: dict mit allen Spalten oder None, wenn nicht vorhanden.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM snap_index WHERE fingerprint=?",
                (fingerprint,)
            ).fetchone()
            return row or None
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.18", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def insert_metric(key: str, value: float, ts: Optional[int] = None) -> Optional[int]:
    """Insert in metrics (Time-Series) – lock-robust.

    Warum:
      metrics ist ein Hot-Path (Rewards/Heartbeat/Coverage/…).
      Bei parallelen Writer-Spitzen wollen wir nicht still Daten verlieren.

    ENV:
      OROMA_DB_LOCK_RETRY_SEC (Default 60s)
    """
    try:
        retry_sec = _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)

        def _do_once() -> int:
            with writer_lock("insert_metric"):
                with get_conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO metrics (key, ts, value) VALUES (?, ?, ?)",
                        (str(key), int(ts if ts is not None else time.time()), float(value)),
                    )
                    return int(cur.lastrowid)


        return int(_run_with_lock_retry(_do_once, retry_sec))
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.19", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

# Patch 1
def insert_transfer_snap(ts: int, sequence: str, pattern: str) -> Optional[int]:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO transfer_snaps (ts, sequence, pattern) VALUES (?, ?, ?)",
                (int(ts), str(sequence), str(pattern))
            )
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.20", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def insert_calculator_task(ts: int, level: int, expr: str,
                           truth: float, truth_json: Optional[str] = None) -> Optional[int]:
    """
    Fügt einen Calculator-Task ein.
    truth        → REAL (skalarisierte Wahrheit; Schema-Vorgabe)
    truth_json   → optionaler JSON-String (voller Vektor), wird nur geschrieben,
                   wenn Spalte existiert; sonst Fallback ohne JSON.
    """
    try:
        with get_conn() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO calculator_tasks (ts, level, expr, truth, truth_json) VALUES (?, ?, ?, ?, ?)",
                    (int(ts), int(level), str(expr), float(truth), truth_json)
                )
            except Exception:
                # Fallback: altes Schema ohne truth_json
                cur = conn.execute(
                    "INSERT INTO calculator_tasks (ts, level, expr, truth) VALUES (?, ?, ?, ?)",
                    (int(ts), int(level), str(expr), float(truth))
                )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.21", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def insert_calculator_result(task_id: int, ts: int, got: float,
                             correct: bool, reward: float,
                             error_type: Optional[str] = None,
                             got_json: Optional[str] = None) -> Optional[int]:
    """
    Fügt ein Calculator-Result ein.
    got         → REAL (skalarisierte Antwort; Schema-Vorgabe)
    got_json    → optionaler JSON-String (voller Vektor), wird nur geschrieben,
                  wenn Spalte existiert; sonst Fallback ohne JSON.
    """
    try:
        with get_conn() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO calculator_results
                       (task_id, ts, got, correct, reward, error_type, got_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (int(task_id), int(ts), float(got), 1 if correct else 0, float(reward), error_type, got_json)
                )
            except Exception:
                # Fallback: altes Schema ohne got_json
                cur = conn.execute(
                    """INSERT INTO calculator_results
                       (task_id, ts, got, correct, reward, error_type)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (int(task_id), int(ts), float(got), 1 if correct else 0, float(reward), error_type)
                )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.22", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def insert_scicalc_result(ts: int, expr: str, method: str,
                          input: str, result: str, error: str = "") -> Optional[int]:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO scicalc_results (ts, expr, method, input, result, error)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (int(ts), str(expr), str(method), str(input), str(result), str(error))
            )
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.23", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

# Patch 2
def insert_empathy_snap(ts: int, mood: str, score: float) -> Optional[int]:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO empathy_snaps (ts, mood, score) VALUES (?, ?, ?)",
                (int(ts), str(mood), float(score))
            )
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.24", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def insert_coverage(ts: int, coverage: float, active: int, total: int) -> Optional[int]:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO coverage_log (ts, coverage, active, total) VALUES (?, ?, ?, ?)",
                (int(ts), float(coverage), int(active), int(total))
            )
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.25", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

# Patch 2 – SetCalc Logging (NEU)

def insert_coverage_30d(ts: int, coverage: float, active: int, total: int) -> Optional[int]:
    """
    Schreibt einen Coverage-Punkt in coverage_log_30d (Fenster-Variante, default 30 Tage).

    Hinweis:
      - Diese Funktion ist optional/backwards compatible: Wenn die Tabelle in einer
        älteren DB noch nicht existiert, wird eine Exception auftreten; Caller
        sollen das best-effort behandeln.
      - Schema wird in ensure_schema() bereitgestellt.
    """
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO coverage_log_30d (ts, coverage, active, total) VALUES (?, ?, ?, ?)",
                (int(ts), float(coverage), int(active), int(total)),
            )
            conn.commit()
            try:
                return int(cur.lastrowid)
            except Exception:
                return None
    except Exception as e:
        log_suppressed(_log, key="sql_manager.insert_coverage_30d", msg="Suppressed exception (best-effort)", exc=e, level=logging.WARNING, interval_s=300)
        return None


def insert_replay_log(chain_id: str, ts_run: int, steps: int = 0, speed: float = 1.0,
                     status: str = "running", info: str = "") -> Optional[int]:
    """
    Append-only Replay-Log für UI/Replay-Steuerung.

    DBWriter-Regel (Phase 2, produktiv, strikt):
    - Wenn OROMA_DBW_ENABLE aktiv ist, darf dieser Runtime-Write nicht mehr lokal
      über sqlite3 laufen, weil sql_manager.get_conn() die verwalteten DBs im
      Strict-Mode bewusst read-only öffnet.
    - Deshalb wird replay_log bei aktivem DBWriter ausschließlich per IPC an den
      zentralen Single-Writer übergeben.
    - Ein lokaler Fallback ist in diesem Modus absichtlich NICHT erlaubt, damit
      Restpfade sichtbar bleiben und keine verdeckten Neben-Writer entstehen.
    """
    try:
        sql_stmt = (
            "INSERT INTO replay_log (chain_id, ts_run, steps, speed, status, info) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        params = (
            str(chain_id),
            int(ts_run),
            int(steps),
            float(speed),
            str(status),
            str(info),
        )
        if _dbw_enabled():
            return int(getattr(_dbw, "exec_lastrowid")(
                sql_stmt,
                params=params,
                tag="sql_manager.insert_replay_log",
                priority="normal",
                timeout_ms=_dbw_timeout_ms("ui"),
                db="oroma",
            ))
        with get_conn() as conn:
            cur = conn.execute(sql_stmt, params)
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.insert_replay_log", msg="Suppressed exception (best-effort)", exc=e, level=logging.WARNING, interval_s=300)
        return None


def update_replay_log(row_id: int, steps: Optional[int] = None, speed: Optional[float] = None,
                      status: Optional[str] = None, info: Optional[str] = None) -> None:
    """
    Aktualisiert einen bestehenden Replay-Log-Eintrag.

    Auch hier gilt im DBWriter-Strict-Mode: kein lokaler sqlite-Write auf die
    ORÓMA-Haupt-DB. Das Update läuft dann ausschließlich über den zentralen
    DBWriter, damit Replay/UI unter Last keinen zusätzlichen Writer-Pfad öffnen.
    """
    try:
        sets = []
        vals = []
        if steps is not None:
            sets.append("steps=?")
            vals.append(int(steps))
        if speed is not None:
            sets.append("speed=?")
            vals.append(float(speed))
        if status is not None:
            sets.append("status=?")
            vals.append(str(status))
        if info is not None:
            sets.append("info=?")
            vals.append(str(info))
        if not sets:
            return
        vals.append(int(row_id))
        sql_stmt = f"UPDATE replay_log SET {', '.join(sets)} WHERE id=?"
        if _dbw_enabled():
            getattr(_dbw, "exec_write")(
                sql_stmt,
                params=tuple(vals),
                tag="sql_manager.update_replay_log",
                priority="normal",
                timeout_ms=_dbw_timeout_ms("ui"),
                db="oroma",
            )
            return
        with get_conn() as conn:
            conn.execute(sql_stmt, tuple(vals))
            conn.commit()
    except Exception as e:
        log_suppressed(_log, key="sql_manager.update_replay_log", msg="Suppressed exception (best-effort)", exc=e, level=logging.WARNING, interval_s=300)
        return


def insert_setcalc_log(ts: int, op: str,
                       setA: Optional[str] = None,
                       setB: Optional[str] = None,
                       result: Optional[str] = None) -> Optional[int]:
    """
    Einfaches Append-Log für SetCalc-Operationen.
    Erwartet Strings (z. B. JSON-dumps) für setA/setB/result.
    """
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO setcalc_log (ts, op, setA, setB, result) VALUES (?, ?, ?, ?, ?)",
                (int(ts), str(op), setA, setB, result)
            )
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.26", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

# v3.6 – Hypothesen
def insert_hypothesis(ts: int, text: str, plan: Optional[str] = None,
                      status: str = "open", score: float = 0.0,
                      confidence: float = 0.0, meta: Optional[str] = None) -> Optional[int]:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO hypotheses
                   (text, status, score, confidence, plan, last_tested, meta, created)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(text), str(status), float(score), float(confidence),
                 plan, None, meta, int(ts))
            )
            return int(cur.lastrowid)
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.27", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def update_hypothesis(hid: int, status: Optional[str] = None,
                      score: Optional[float] = None,
                      confidence: Optional[float] = None,
                      last_tested: Optional[int] = None,
                      meta: Optional[str] = None) -> bool:
    try:
        fields, params = [], []
        if status is not None:
            fields.append("status=?");      params.append(str(status))
        if score is not None:
            fields.append("score=?");       params.append(float(score))
        if confidence is not None:
            fields.append("confidence=?");  params.append(float(confidence))
        if last_tested is not None:
            fields.append("last_tested=?"); params.append(int(last_tested))
        if meta is not None:
            fields.append("meta=?");        params.append(meta)
        if not fields:
            return False
        params.append(int(hid))
        with get_conn() as conn:
            conn.execute(f"UPDATE hypotheses SET {', '.join(fields)} WHERE id=?", params)
            conn.commit()
        return True
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.28", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

# ----------------------------- Episoden-Helper -------------------------------

def insert_episode(ts_start: int,
                   kind: str,
                   source: Optional[str] = None,
                   label: Optional[str] = None,
                   meta: Optional[Dict[str, Any]] = None,
                   ts_end: Optional[int] = None) -> Optional[int]:
    """
    Legt einen Episoden-Kopf in 'episodes' an.

    Parameter
    ---------
    ts_start : int
        Startzeitpunkt der Episode (UNIX-Timestamp).
    kind : str
        Episoden-Typ, z. B. "audio", "vision", "game".
    source : str|None
        Quelle, z. B. "audio_student", "snake_trainer".
    label : str|None
        Menschlich lesbare Beschreibung.
    meta : dict|None
        Zusatz-Metadaten (JSON).
    ts_end : int|None
        Optionales End-Timestamp (kann später gesetzt werden).

    Rückgabe
    --------
    int|None: ID der Episode oder None bei Fehler.
    """
    # WICHTIG (Produktiv): insert_episode wird von vielen Jobs parallel verwendet.
    # Ohne writer_lock + Retry kann es bei gleichzeitigem SnapChain/Rules/Stats-Write zu
    # sporadischem "database is locked" kommen (z. B. memorymaze_daily_runner).
    try:
        retry_sec = _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":")) if meta is not None else None

        def _do_once() -> int:
            # Stufe C (DBWriter): globaler Single-Writer.
            if _dbw_enabled():
                sql_stmt = """
                        INSERT INTO episodes (ts_start, ts_end, kind, source, label, meta_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """
                params = (int(ts_start), int(ts_end) if ts_end is not None else None, str(kind), source, label, meta_json)
                return int(getattr(_dbw, "exec_lastrowid")(sql_stmt, params=params, tag="sql_manager.insert_episode", priority="normal", timeout_ms=_dbw_timeout_ms("dream"), db="oroma"))

            with writer_lock("insert_episode"):
                with get_conn() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO episodes (ts_start, ts_end, kind, source, label, meta_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(ts_start),
                            int(ts_end) if ts_end is not None else None,
                            str(kind),
                            source,
                            label,
                            meta_json,
                        )
                    )
                    conn.commit()
                    return int(cur.lastrowid)

        eid = _run_with_lock_retry(_do_once, retry_sec)
        try:
            return int(eid) if eid else None
        except Exception:
            return None
    except Exception as e:
        print(f"[sql_manager] Fehler insert_episode: {e}")
        return None


def update_episode_end(episode_id: int, ts_end: Optional[int] = None) -> bool:
    """
    Setzt ts_end für eine Episode (z. B. beim Schließen einer Session).
    """
    if episode_id is None:
        return False
    try:
        ts_end_i = int(ts_end if ts_end is not None else time.time())
        if _dbw_enabled():
            getattr(_dbw, "exec_write")(
                "UPDATE episodes SET ts_end=? WHERE id=?",
                params=[ts_end_i, int(episode_id)],
                tag="sql_manager.update_episode_end",
                priority="normal",
                timeout_ms=_dbw_timeout_ms("dream"),
                db="oroma",
            )
            return True
        with get_conn() as conn:
            conn.execute(
                "UPDATE episodes SET ts_end=? WHERE id=?",
                (ts_end_i, int(episode_id))
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"[sql_manager] Fehler update_episode_end: {e}")
        return False


def insert_episode_event(episode_id: int,
                         ts: int,
                         event_type: str,
                         ref_table: Optional[str] = None,
                         ref_id: Optional[int] = None,
                         meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """
    Fügt ein Ereignis in episode_events ein.

    Beispiel:
      event_type = "audio_pair"
      ref_table  = "audio_student_pairs"
      ref_id     = ID in ref_table
    """
    try:
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":")) if meta is not None else None

        # Stufe C (DBWriter): globaler Single-Writer. Falls aktiv, route Inserts über IPC.
        if _dbw_enabled():
            sql_stmt = """
                INSERT INTO episode_events
                    (episode_id, ts, event_type, ref_table, ref_id, meta_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            params = (
                int(episode_id),
                int(ts),
                str(event_type),
                ref_table,
                int(ref_id) if ref_id is not None else None,
                meta_json,
            )
            return int(getattr(_dbw, "exec_lastrowid")(sql_stmt, params=params, tag="sql_manager.insert_episode_event", priority="normal", timeout_ms=_dbw_timeout_ms("dream"), db="oroma"))

        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO episode_events
                    (episode_id, ts, event_type, ref_table, ref_id, meta_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(episode_id),
                    int(ts),
                    str(event_type),
                    ref_table,
                    int(ref_id) if ref_id is not None else None,
                    meta_json,
                )
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        print(f"[sql_manager] Fehler insert_episode_event: {e}")
        return None
def insert_episodic_metric(episode_id: int,
                           ts: int,
                           key: str,
                           value: float) -> Optional[int]:
    """
    Einfache Metrik pro Episode, z. B. Reward-Mittelwert, Anzahl Events etc.
    """
    # episodic_metrics wird im gleichen Zeitfenster wie episodes geschrieben.
    # Daher identische Lock-Strategie wie insert_snapchain/insert_episode.
    try:
        retry_sec = _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)

        def _do_once() -> int:
            # Stufe C (DBWriter): globaler Single-Writer.
            if _dbw_enabled():
                sql_stmt = """
                        INSERT INTO episodic_metrics (episode_id, ts, key, value)
                        VALUES (?, ?, ?, ?)
                        """
                params = (int(episode_id), int(ts), str(key), float(value))
                getattr(_dbw, "exec_write")(sql_stmt, params=params, tag="sql_manager.insert_episodic_metric", priority="normal", timeout_ms=_dbw_timeout_ms("dream"), db="oroma")
                return 0

            with writer_lock("insert_episodic_metric"):
                with get_conn() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO episodic_metrics (episode_id, ts, key, value)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            int(episode_id),
                            int(ts),
                            str(key),
                            float(value),
                        )
                    )
                    conn.commit()
                    return int(cur.lastrowid)

        mid = _run_with_lock_retry(_do_once, retry_sec)
        try:
            return int(mid) if mid else None
        except Exception:
            return None
    except Exception as e:
        print(f"[sql_manager] Fehler insert_episodic_metric: {e}")
        return None
     
def insert_cam_token(ts: int, q: float, vec: list[float],
                     motion: float | None = None,
                     edges: float | None = None,
                     color: float | None = None,
                     source: str = "vision/token") -> int | None:
    """
    Speichert einen Kamera-Token als SnapChain-Blob (JSON) – ohne neue Tabelle
    und loggt zusätzlich (best effort) ein Episoden-Event.

    blob = {"kind":"cam_token","v":[...],
            "motion":..., "edges":..., "color":...}

    Episoden-Integration
    --------------------
    • Nach erfolgreichem Insert in snapchains wird
        episodic_writer.log_vision_cam_token_global(...)
      aufgerufen (falls Modul verfügbar).
    """
    try:
        payload = {
            "kind": "cam_token",
            "v": [float(x) for x in (vec or [])],
            "motion": None if motion is None else float(motion),
            "edges":  None if edges  is None else float(edges),
            "color":  None if color  is None else float(color),
        }
        blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        snap_id = insert_snapchain({
            "ts": int(ts),
            "quality": float(q),
            "blob": blob,
            "exported": 0,
            "status": "active",
            "origin": str(source),          # z.B. "vision/token"
            "namespace": "vision",
            "notes": "cam_token",
            "version": "v3.8",
            "weight": 1.0,
        })
        # Episoden-Logging (nicht kritisch – Fehler hier nie weiterreichen)
        if snap_id is not None:
            try:
                from core import episodic_writer  # lazy import, um Zyklen zu vermeiden
                episodic_writer.log_vision_cam_token_global(
                    ts=int(ts),
                    snap_id=int(snap_id),
                    q=float(q),
                    origin=str(source),
                    motion=motion,
                    edges=edges,
                    color=color,
                    dim=len(vec or []),
                )
            except Exception as e:
                # Episoden-Fehler nie propagieren
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.sql_manager.pass.3",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        return snap_id
    except Exception as e:
        print(f"[sql_manager] Fehler insert_cam_token: {e}")
        return None

# ----------------------------- Curriculum-Helper -----------------------------


def insert_audio_token(
    ts: int,
    vec,
    *,
    sr: int,
    win_sec: float,
    rms: float,
    zcr: float,
    centroid_hz: float,
    band,
    vad: int,
    quality: float = 0.0,
    origin: str = "audio/token",
    namespace: str = "audio:mic",
    status: Union[int, str] = "active",
) -> Optional[int]:
    """Schreibt einen *Audio-SnapToken* als SnapChain in die Haupt-DB.

    Analog zu `insert_cam_token()` – aber bewusst ohne große Binary-Daten (WAV).
    Der Token ist ein kleiner Feature-Vektor + Metadaten für Resonanz/Lernen.

    Wichtig:
      - Wir bleiben kompatibel zur bestehenden Tabelle `snapchains`
        (kein neues Schema nötig).
      - `status` darf int (0/1) oder str sein; es wird auf 'active'/'inactive'
        normalisiert.

    Returns:
        snapchain_id (int|None)
    """
    try:
        v = [float(x) for x in (list(vec) if not isinstance(vec, (list, tuple)) else vec)]
    except Exception:
        v = []

    try:
        band_list = [float(x) for x in (list(band) if not isinstance(band, (list, tuple)) else band)]
    except Exception:
        band_list = []

    # status normalisieren (snapchains.status ist TEXT)
    if isinstance(status, (int, float)):
        status_s = "active" if int(status) != 0 else "inactive"
    else:
        status_s = str(status) if str(status).strip() else "active"

    payload = {
        "kind": "audio_token",
        "v": v,
        "sr": int(sr),
        "win_sec": float(win_sec),
        "rms": float(rms),
        "zcr": float(zcr),
        "centroid_hz": float(centroid_hz),
        "band": band_list,
        "vad": int(vad),
    }
    blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return insert_snapchain(
        {
            "ts": int(ts),
            "quality": float(quality),
            "blob": blob,
            "exported": 0,
            "status": status_s,
            "origin": str(origin),
            "namespace": str(namespace),
            "notes": "audio_token",
            "version": "v3.8",
            "weight": 1.0,
        }
    )

def fetch_curriculum_state() -> Dict[str, Any]:
    """Liest den Curriculum-Zustand (id=1)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM curriculum_state WHERE id=1")
        return cur.fetchone() or {}

def update_curriculum_state(stage: Optional[int] = None,
                            progress_json: Optional[str] = None,
                            window_json: Optional[str] = None,
                            last_update: Optional[int] = None) -> bool:
    """
    Partielles Update des Curriculum-Zustands.
    Übergib nur die Felder, die du ändern willst.
    """
    try:
        fields, params = [], []
        if stage is not None:
            fields.append("stage=?");        params.append(int(stage))
        if progress_json is not None:
            fields.append("progress=?");     params.append(str(progress_json))
        if window_json is not None:
            fields.append("window=?");       params.append(str(window_json))
        if last_update is not None:
            fields.append("last_update=?");  params.append(int(last_update))
        if not fields:
            return False
        params.append(1)  # id=1
        with get_conn() as conn:
            conn.execute(f"UPDATE curriculum_state SET {', '.join(fields)} WHERE id=?", params)
            conn.commit()
        return True
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.29", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

# =============================================================================
# ObjectGraph-Helpers: object_nodes / object_relations
# =============================================================================

def _json_dumps_or_none(obj: Optional[dict]) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        _log.exception("object_nodes: meta_json dump failed")
        return None


def ensure_object_node(
    kind: str,
    label: str,
    meta: Optional[dict] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Sorgt dafür, dass ein Objektknoten (kind, label) existiert und gibt seine ID zurück.

    - kind:  z.B. "object", "scene", "concept"
    - label: kurze Bezeichnung ("Ball", "Lampe", "Snake-Head", ...)
    - meta:  optionales Dict mit zusätzlichen Infos (SceneGraph-IDs, Stats, ...)

    Dedupe-Strategie (konservativ):
      - Es wird nach (kind, label) gesucht.
      - Falls gefunden, wird die vorhandene ID zurückgegeben.
      - meta_json wird nur gesetzt, wenn bislang None war.
    """
    meta_json = _json_dumps_or_none(meta)

    # NOTE (PRODUKTION): ObjectGraph-Writes sind lock-anfällig (mehrere Writer-Jobs).
    # Wir halten die Logik bewusst einfach, aber machen Fehlerpfade *sichtbar* und
    # nutzen den bestehenden Writer-Lock, um "database is locked" zu reduzieren.
    with get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row

        try:
            row = conn.execute(
                """
                SELECT id, meta_json
                  FROM object_nodes
                 WHERE kind = ? AND label = ?
                """,
                (kind, label),
            ).fetchone()
        except Exception as e:
            log_suppressed(
                _log,
                key="sql_manager.object_node.select",
                msg=f"ObjectGraph: SELECT object_nodes failed (kind={kind!r} label={label!r})",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
            raise

        if row:
            obj_id = int(row["id"])
            if meta_json and not row["meta_json"]:
                try:
                    if _dbw_enabled():
                        # Global Single-Writer: Write via db_writer
                        _dbw.exec_write(
                            "UPDATE object_nodes SET meta_json = ? WHERE id = ?",
                            (meta_json, int(obj_id)),
                            tag="sql_manager.object_nodes.update_meta",
                            priority="normal",
                            timeout_ms=_dbw_timeout_ms("dream"),
                        )
                    else:
                        with writer_lock(kind="object_nodes.update", timeout_sec=_env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30)):
                            conn.execute(
                                "UPDATE object_nodes SET meta_json = ? WHERE id = ?",
                                (meta_json, obj_id),
                            )
                except Exception as e:
                    log_suppressed(
                        _log,
                        key="sql_manager.object_node.update_meta",
                        msg=f"ObjectGraph: UPDATE object_nodes.meta_json failed (id={obj_id} kind={kind!r} label={label!r})",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=120,
                    )
                    # Update ist best-effort – ID existiert bereits.
            return obj_id

        created_ts = int(time.time())
        try:
            if _dbw_enabled():
                # Global Single-Writer: insert best-effort via db_writer.
                rid = int(
                    _dbw.exec_lastrowid(
                        """
                        INSERT OR IGNORE INTO object_nodes (kind, label, meta_json, created_ts)
                        VALUES (?, ?, ?, ?)
                        """.strip(),
                        (str(kind), str(label), meta_json, int(created_ts)),
                        tag="sql_manager.object_nodes.insert",
                        priority="normal",
                        timeout_ms=_dbw_timeout_ms("dream"),
                    )
                )
                if rid > 0:
                    return rid
                # Fallback: row exists (unique) → select id
                row2 = conn.execute(
                    "SELECT id FROM object_nodes WHERE kind = ? AND label = ? LIMIT 1",
                    (kind, label),
                ).fetchone()
                if row2:
                    return int(row2["id"])
                raise RuntimeError("ObjectGraph: INSERT OR IGNORE produced no row; SELECT also empty")
            else:
                with writer_lock(kind="object_nodes.insert", timeout_sec=_env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30)):
                    cur = conn.execute(
                        """
                        INSERT INTO object_nodes (kind, label, meta_json, created_ts)
                        VALUES (?, ?, ?, ?)
                        """,
                        (kind, label, meta_json, created_ts),
                    )
                return int(cur.lastrowid)
        except Exception as e:
            log_suppressed(
                _log,
                key="sql_manager.object_node.insert",
                msg=f"ObjectGraph: INSERT object_nodes failed (kind={kind!r} label={label!r})",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
            raise


def insert_object_relation(
    a_id: int,
    b_id: int,
    relation: str,
    confidence: float = 1.0,
    source_scene_id: Optional[int] = None,
    ts: Optional[int] = None,
    notes: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Fügt eine Object-Relation ein, vermeidet aber Duplikate.

    Uniqueness-Regel (bewusst einfach gehalten):
      - (a_id, relation, b_id) ist eindeutig.
        D.h. wenn es bereits eine Relation mit genau diesem Tripel gibt,
        wird KEIN neuer Datensatz angelegt, sondern die bestehende id zurückgegeben.

    Hintergrund:
      - Der ObjectExtractor kann mehrfach laufen und dieselben Meta/SnapChain-Paare
        immer wieder sehen (z.B. bei neuen ObjectGraphs über die gleichen Chains).
      - Semantisch reicht eine Kante:
          meta --[meta_to_chain]--> snapchain
          snapchain --[chain_to_origin]--> origin
    """
    if ts is None:
        ts = int(time.time())

    notes_json: Optional[str]
    if notes is None:
        notes_json = None
    else:
        try:
            notes_json = json.dumps(notes, ensure_ascii=False)
        except Exception:
            # Fallback: best effort String-Repräsentation
            notes_json = json.dumps({"_raw": str(notes)}, ensure_ascii=False)

    # NOTE (PRODUKTION): Relation-Writes können bei Parallel-Workloads locken.
    # - writer_lock serialisiert pro Prozess (flock) best-effort.
    # - Trotzdem können echte SQLite-Writer-Kollisionen auftreten.
    #   Daher nutzen wir _run_with_lock_retry() für kontrolliertes Re-Open/Retry.

    # Global Single-Writer (Stufe C): Falls aktiv, Write via db_writer.
    # Wir nutzen INSERT ... ON CONFLICT DO NOTHING (Unique-Triplet Index vorhanden)
    # und lesen danach lokal die ID. Das vermeidet Race/Duplikate und reduziert Lock-Druck.
    if _dbw_enabled():
        try:
            rid = int(
                _dbw.exec_lastrowid(
                    """
                    INSERT INTO object_relations
                        (a_id, b_id, relation, confidence, source_scene_id, ts, notes)
                    VALUES
                        (?,   ?,   ?,        ?,          ?,              ?,  ?)
                    ON CONFLICT(a_id, relation, b_id) DO NOTHING
                    """.strip(),
                    (int(a_id), int(b_id), str(relation), float(confidence), source_scene_id, int(ts), notes_json),
                    tag="sql_manager.object_rel.insert",
                    priority="normal",
                    timeout_ms=_dbw_timeout_ms("dream"),
                )
            )
            if rid > 0:
                return rid
            # already exists → select id (read-only, WAL-friendly)
            with get_conn() as conn:
                row = conn.execute(
                    """
                    SELECT id FROM object_relations
                     WHERE a_id = ? AND relation = ? AND b_id = ?
                     LIMIT 1
                    """,
                    (int(a_id), str(relation), int(b_id)),
                ).fetchone()
                if row is not None:
                    return int(row["id"])
            raise RuntimeError("ObjectGraph: INSERT DO NOTHING produced no row; SELECT also empty")
        except Exception as e:
            log_suppressed(
                _log,
                key="sql_manager.object_rel.insert",
                msg=f"ObjectGraph: INSERT object_relations via db_writer failed (a_id={a_id} rel={relation!r} b_id={b_id})",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
            raise

    def _attempt():
        with get_conn() as conn:
            # 1) Duplikate prüfen
            cur = conn.execute(
                """
                SELECT id
                  FROM object_relations
                 WHERE a_id = ?
                   AND relation = ?
                   AND b_id = ?
                 LIMIT 1
                """,
                (int(a_id), str(relation), int(b_id)),
            )
            row = cur.fetchone()
            if row is not None:
                return int(row["id"])

            # 2) Neu einfügen
            with writer_lock(kind="object_relations.insert", timeout_sec=_env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30)):
                cur2 = conn.execute(
                    """
                    INSERT INTO object_relations
                        (a_id, b_id, relation, confidence, source_scene_id, ts, notes)
                    VALUES
                        (?,   ?,   ?,        ?,          ?,              ?,  ?)
                    """,
                    (int(a_id), int(b_id), str(relation), float(confidence),
                     source_scene_id, int(ts), notes_json),
                )
            return int(cur2.lastrowid)

    try:
        return int(_run_with_lock_retry(_attempt, _env_int("OROMA_DB_LOCK_RETRY_SEC", 60)))
    except Exception as e:
        log_suppressed(
            _log,
            key="sql_manager.object_rel.insert",
            msg=f"ObjectGraph: INSERT object_relations failed (a_id={a_id} rel={relation!r} b_id={b_id})",
            exc=e,
            level=logging.WARNING,
            interval_s=120,
        )
        raise


def upsert_synaptic_relation(
    a_id: int,
    b_id: int,
    weight: float,
    hebb_delta: float,
    cooc_inc: float,
    scope: Optional[str] = None,
    half_life_sec: int = 2592000,
    ts: Optional[int] = None,
) -> int:
    """
    =============================================================================
    Datei:      /opt/ai/oroma/core/sql_manager.py
    Projekt:    🧠 ORÓMA v3.8 – NMR Synaptische Plastizität (Relationstyp 'synaptic')
    Stand:      2026-03-03
    Autor:      ORÓMA · KI-JWG-X1
    =============================================================================

    Zweck
    -----
    Upsert/Update einer synaptischen Kante im ObjectGraph-Backbone:

        object_relations(a_id, relation='synaptic', b_id, confidence, ts, notes)

    Hintergrund / Warum hier?
    --------------------------
    ORÓMA besitzt bereits ein robustes Relationenschema (object_nodes/object_relations),
    inklusive Writer-Lock und best-effort Fehlerpfade. Anstatt eine zweite Graph-DB
    (synaptic_links) einzuführen, speichern wir Synapsen als Relationstyp 'synaptic'.

    Semantik
    --------
    - confidence == sichtbarer Edge-Weight w in [0..1] (UI + NMR Soft-Evidence)
    - notes JSON enthält Langzeit-Potenzial (hebb) + Diagnostik (cooc/sim/etc.)
    - Die Kante ist eindeutig über (a_id, relation, b_id). Bei Existenz wird aktualisiert.

    Wichtig (Produktionsregeln)
    ---------------------------
    - Kein stilles Scheitern: Fehlerpfade werden rate-limited sichtbar geloggt.
    - Verbindungen/Transactions werden immer sauber geschlossen (context manager).
    - Writes sind durch writer_lock serialisiert, um SQLite Locks zu minimieren.

    Parameter
    ---------
    a_id, b_id:
      object_nodes.id Endpunkte.
    weight:
      neuer normierter Weight w [0..1] (nach Update/Normierung berechnet).
    hebb_delta:
      Delta, das auf den (decay-korrigierten) hebb addiert wurde (Diagnose).
      Der gespeicherte 'hebb' Wert wird aus bestehendem hebb + hebb_delta gebildet.
    cooc_inc:
      Ko-Okkurrenz-Inkrement (Diagnose; wird akkumuliert/EMA-artig gemerged).
    scope:
      Optionaler Scope/Namespace (z.B. episode.source oder episode.kind), um
      universelle Vermischung zu vermeiden.
    half_life_sec:
      Halbwertszeit für Decay (wird im notes gespeichert).
    ts:
      Unix timestamp (default: now).

    Return
    ------
    id der object_relations Zeile.
    """
    if ts is None:
        ts = int(time.time())

    # Clamp weight defensiv
    w = float(weight)
    if w < 0.0:
        w = 0.0
    elif w > 1.0:
        w = 1.0

    with get_conn() as conn:
        try:
            cur = conn.execute(
                """
                SELECT id, confidence, ts, notes
                  FROM object_relations
                 WHERE a_id = ?
                   AND relation = 'synaptic'
                   AND b_id = ?
                 LIMIT 1
                """,
                (int(a_id), int(b_id)),
            )
            row = cur.fetchone()
        except Exception as e:
            log_suppressed(
                _log,
                key="sql_manager.synaptic.select",
                msg=f"Synaptic: SELECT failed (a_id={a_id} b_id={b_id})",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
            raise

        now_ts = int(ts)
        if row is None:
            notes = {
                "hebb": float(max(0.0, hebb_delta)),
                "cooc": float(max(0.0, cooc_inc)),
                "sim": 0.0,
                "react_count": 1,
                "first_ts": now_ts,
                "last_ts": now_ts,
                "half_life_sec": int(half_life_sec),
            }
            if scope:
                notes["scope"] = str(scope)

            try:
                notes_json = json.dumps(notes, ensure_ascii=False)
                if _dbw_enabled():
                    # UPSERT ist möglich (Unique-Triplet Index). Wir nutzen DO UPDATE, damit
                    # parallel laufende Dreams nicht duplizieren.
                    rid = int(
                        _dbw.exec_lastrowid(
                            """
                            INSERT INTO object_relations
                                (a_id, b_id, relation, confidence, source_scene_id, ts, notes)
                            VALUES
                                (?,   ?,   'synaptic', ?,          NULL,           ?,  ?)
                            ON CONFLICT(a_id, relation, b_id) DO UPDATE SET
                                confidence = excluded.confidence,
                                ts = excluded.ts,
                                notes = excluded.notes
                            """.strip(),
                            (int(a_id), int(b_id), float(w), int(now_ts), notes_json),
                            tag="sql_manager.synaptic.upsert",
                            priority="low",
                            timeout_ms=_dbw_timeout_ms("dream"),
                        )
                    )
                    if rid > 0:
                        return rid
                    # conflict path → select id
                    cur2 = conn.execute(
                        "SELECT id FROM object_relations WHERE a_id=? AND relation='synaptic' AND b_id=? LIMIT 1",
                        (int(a_id), int(b_id)),
                    )
                    row2 = cur2.fetchone()
                    if row2 is None:
                        raise RuntimeError("Synaptic: UPSERT conflict but row missing")
                    return int(row2["id"])
                else:
                    with writer_lock(kind="object_relations.synaptic.insert", timeout_sec=_env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30)):
                        cur = conn.execute(
                            """
                            INSERT INTO object_relations
                                (a_id, b_id, relation, confidence, source_scene_id, ts, notes)
                            VALUES
                                (?,   ?,   'synaptic', ?,          NULL,           ?,  ?)
                            """,
                            (int(a_id), int(b_id), float(w), now_ts, notes_json),
                        )
                    return int(cur.lastrowid)
            except Exception as e:
                log_suppressed(
                    _log,
                    key="sql_manager.synaptic.insert",
                    msg=f"Synaptic: INSERT failed (a_id={a_id} b_id={b_id})",
                    exc=e,
                    level=logging.WARNING,
                    interval_s=120,
                )
                raise

        # Existing row → merge notes and update
        rid = int(row["id"])
        prev_notes_raw = row["notes"]
        try:
            prev_notes = json.loads(prev_notes_raw) if prev_notes_raw else {}
            if not isinstance(prev_notes, dict):
                prev_notes = {"_raw": prev_notes_raw}
        except Exception:
            prev_notes = {"_raw": prev_notes_raw}

        prev_hebb = float(prev_notes.get("hebb") or 0.0)
        prev_cooc = float(prev_notes.get("cooc") or 0.0)
        prev_react = int(prev_notes.get("react_count") or 0)
        first_ts = int(prev_notes.get("first_ts") or now_ts)

        # Update merged notes
        prev_notes["hebb"] = float(max(0.0, prev_hebb + float(hebb_delta)))
        prev_notes["cooc"] = float(max(0.0, prev_cooc + float(cooc_inc)))
        prev_notes["react_count"] = int(prev_react + 1)
        prev_notes["last_ts"] = int(now_ts)
        prev_notes["first_ts"] = int(first_ts)
        prev_notes["half_life_sec"] = int(half_life_sec)
        if scope:
            prev_notes["scope"] = str(scope)

        notes_json = json.dumps(prev_notes, ensure_ascii=False)

        try:
            if _dbw_enabled():
                _dbw.exec_write(
                    """
                    UPDATE object_relations
                       SET confidence = ?,
                           ts = ?,
                           notes = ?
                     WHERE id = ?
                    """.strip(),
                    (float(w), int(now_ts), notes_json, int(rid)),
                    tag="sql_manager.synaptic.update",
                    priority="low",
                    timeout_ms=_dbw_timeout_ms("dream"),
                )
            else:
                with writer_lock(kind="object_relations.synaptic.update", timeout_sec=_env_int("OROMA_DB_WRITELOCK_TIMEOUT_SEC", 30)):
                    conn.execute(
                        """
                        UPDATE object_relations
                           SET confidence = ?,
                               ts = ?,
                               notes = ?
                         WHERE id = ?
                        """,
                        (float(w), int(now_ts), notes_json, int(rid)),
                    )
            return int(rid)
        except Exception as e:
            log_suppressed(
                _log,
                key="sql_manager.synaptic.update",
                msg=f"Synaptic: UPDATE failed (id={rid} a_id={a_id} b_id={b_id})",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
            raise


def ensure_event_object_node(
    event_id: int,
    meta: Optional[dict] = None,
) -> int:
    """
    Stellt sicher, dass ein object_nodes Eintrag für ein Episode-Event existiert.

    Dedupe:
      - kind='event'
      - label='event:<episode_events.id>'

    So kann die UI/NMR später stabil auf Nodes referenzieren, ohne eine zweite
    Mapping-Tabelle einzuführen.
    """
    return ensure_object_node(kind="event", label=f"event:{int(event_id)}", meta=meta)


def fetch_object_nodes(
    kind: Optional[str] = None,
    limit: int = 200,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Liefert eine Liste von Objektknoten als Dicts:

      [{
        "id": int,
        "kind": str,
        "label": str,
        "meta": dict | None,
        "created_ts": int,
      }, ...]
    """
    sql = """
        SELECT id, kind, label, meta_json, created_ts
          FROM object_nodes
    """
    params: List[Any] = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    with get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        meta = None
        if row["meta_json"]:
            try:
                meta = json.loads(row["meta_json"])
            except Exception:
                _log.exception("fetch_object_nodes: invalid meta_json for id=%s", row["id"])
        result.append(
            {
                "id": int(row["id"]),
                "kind": row["kind"],
                "label": row["label"],
                "meta": meta,
                "created_ts": int(row["created_ts"]),
            }
        )
    return result


def fetch_object_relations_for_node(
    node_id: int,
    limit: int = 200,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Liefert Relationen, in denen der gegebene Objektknoten beteiligt ist
    (als a_id oder b_id).
    """
    sql = """
        SELECT id, a_id, relation, b_id, confidence,
               source_scene_id, ts, notes
          FROM object_relations
         WHERE a_id = ? OR b_id = ?
         ORDER BY id DESC
         LIMIT ?
    """
    params = [int(node_id), int(node_id), int(limit)]

    with get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"]),
                "a_id": int(row["a_id"]),
                "relation": row["relation"],
                "b_id": int(row["b_id"]),
                "confidence": float(row["confidence"]),
                "source_scene_id": row["source_scene_id"],
                "ts": int(row["ts"]),
                "notes": row["notes"],
            }
        )
    return result


def fetch_object_relations(
    limit: int = 200,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Liefert die letzten object_relations (global, ohne Filter).
    Praktisch für die /objects-UI-Übersicht.
    """
    sql = """
        SELECT id, a_id, relation, b_id, confidence,
               source_scene_id, ts, notes
          FROM object_relations
         ORDER BY id DESC
         LIMIT ?
    """
    params = [int(limit)]

    with get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"]),
                "a_id": int(row["a_id"]),
                "relation": row["relation"],
                "b_id": int(row["b_id"]),
                "confidence": float(row["confidence"]),
                "source_scene_id": row["source_scene_id"],
                "ts": int(row["ts"]),
                "notes": row["notes"],
            }
        )
    return result

# ----------------------------- Fetches ---------------------------------------

def fetch_hypotheses(limit: int = 10) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM hypotheses ORDER BY id DESC LIMIT ?", (int(limit),))
        return cur.fetchall() or []

def fetch_scicalc_results(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM scicalc_results ORDER BY id DESC LIMIT ?", (int(limit),))
        return cur.fetchall() or []

def fetch_last_empathy(limit: int = 1) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ts, mood, score FROM empathy_snaps ORDER BY id DESC LIMIT ?", (int(limit),))
        return cur.fetchall() or []

def fetch_last_coverage(limit: int = 1) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ts, coverage, active, total FROM coverage_log ORDER BY id DESC LIMIT ?", (int(limit),))
        return cur.fetchall() or []

# Patch 2 – SetCalc Logging (NEU)

def fetch_last_coverage_30d(limit: int = 1) -> List[Dict[str, Any]]:
    """
    Liefert die letzten N Coverage-Punkte aus coverage_log_30d (Fenster-Variante).

    Backwards compatible:
      - Wenn die Tabelle (noch) nicht existiert, wird [] zurückgegeben.
    """
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT ts, coverage, active, total FROM coverage_log_30d ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                if isinstance(r, dict):
                    out.append(dict(r))
                else:
                    # sqlite3.Row
                    out.append({"ts": int(r[0]), "coverage": float(r[1]), "active": int(r[2]), "total": int(r[3])})
            return out
    except Exception as e:
        # Häufigster Grund: Tabelle fehlt in älterer DB → best-effort []
        log_suppressed(_log, key="sql_manager.fetch_last_coverage_30d", msg="Suppressed exception (best-effort)", exc=e, level=logging.DEBUG, interval_s=600)
        return []

def fetch_setcalc_log(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM setcalc_log ORDER BY id DESC LIMIT ?",
            (int(limit),)
        )
        return cur.fetchall() or []

def fetch_episodes(limit: int = 50,
                   kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Liefert die letzten Episoden (ts_start DESC).
    Optional nach kind filtern ('vision', 'snake', 'audio', ...).
    """
    q = "SELECT * FROM episodes"
    args: List[Any] = []
    if kind:
        q += " WHERE kind = ?"
        args.append(str(kind))
    q += " ORDER BY ts_start DESC LIMIT ?"
    args.append(int(limit))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(q, tuple(args))
        return cur.fetchall() or []


def fetch_episode_events(episode_id: int,
                         limit: int = 500) -> List[Dict[str, Any]]:
    """
    Liefert Events zu einer Episode, chronologisch.
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM episode_events
               WHERE episode_id = ?
               ORDER BY ts ASC, id ASC
               LIMIT ?""",
            (int(episode_id), int(limit))
        )
        return cur.fetchall() or []

def fetch_cam_tokens_window(window_sec: int = 3600,
                            min_q: float = 0.0,
                            limit: int = 10000) -> list[dict]:
    """
    Liest jüngste Kamera-Token aus snapchains (origin ~ 'vision/%').
    Erwartet JSON-Blob {"kind":"cam_token","v":[...], ...}.
    """
    try:
        cutoff = int(time.time()) - int(window_sec)
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, ts, quality, origin, blob "
                "FROM snapchains "
                "WHERE ts >= ? AND quality >= ? "
                "  AND (origin = 'vision/token' OR origin = 'vision/stream' OR origin LIKE 'vision/%') "
                "ORDER BY ts DESC LIMIT ?",
                (cutoff, float(min_q), int(limit))
            ).fetchall() or []

        out: list[dict] = []
        for r in rows:
            b = r["blob"]
            try:
                txt = b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else (b if isinstance(b, str) else "")
                payload = json.loads(txt) if txt else {}
                vec = payload.get("v")
                if isinstance(payload, dict) and (payload.get("kind") == "cam_token") and isinstance(vec, list):
                    out.append({
                        "id": r["id"],
                        "ts": r["ts"],
                        "q": r["quality"],
                        "motion": payload.get("motion"),
                        "edges": payload.get("edges"),
                        "color": payload.get("color"),
                        "vec": vec,
                        "source": r["origin"],
                    })
            except Exception:
                continue
        return out
    except Exception as e:
        log_suppressed(_log, key="sql_manager.ret.30", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return []

# ----------------------------- Counters & Coverage ---------------------------

def count_snapchains(status: Optional[str] = None,
                     origin: Optional[str] = None) -> int:
    """
    Zählt Snapchains insgesamt oder gefiltert.
    status: z.B. 'active' | 'inactive' | 'archived' ...
    origin: exakter Match oder Prefix-Suche (import/zip, import/tar, ...)
    """
    q = "SELECT COUNT(*) AS n FROM snapchains"
    conds: List[str] = []
    args: List[Any] = []
    if status is not None:
        conds.append("status = ?")
        args.append(str(status))
    if origin is not None and origin != "":
        conds.append("(origin = ? OR origin LIKE ?)")
        args.extend([origin, f"{origin}%"])
    if conds:
        q += " WHERE " + " AND ".join(conds)
    with get_conn() as conn:
        row = conn.execute(q, tuple(args)).fetchone()
        return int(row["n"]) if row and "n" in row else 0

def get_coverage_numbers() -> Dict[str, Any]:
    """
    Liefert eine momentane Coverage-Sicht:
      coverage = active / total (0..1), plus absolute Zahlen.
    """
    total = count_snapchains()
    active = count_snapchains(status="active")
    coverage = (active / total) if total else 0.0
    return {"coverage": float(coverage), "active": int(active), "total": int(total)}

# ----------------------------- Rebuild & CLI --------------------------------

def _rebuild() -> None:
    db_path = get_db_path()
    dirn = os.path.dirname(db_path)
    if dirn:
        os.makedirs(dirn, exist_ok=True)
    if os.path.exists(db_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = db_path + f".bak_{ts}"
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        os.replace(db_path, bak)
        print(f"[sql_manager] Bestehende DB nach {bak} verschoben.")
    ensure_schema()
    print(f"[sql_manager] Schema erstellt: {db_path}")

if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        _rebuild()
    elif "--ensure" in sys.argv:
        ensure_schema()
    elif "--test" in sys.argv:
        ensure_schema()
        print("[sql_manager] Selftest v3.8-r2 …")
        ts = int(time.time())

        # Hypothese
        hid = insert_hypothesis(ts, "Testhypothese: mehr Training steigert Qualität", plan="A/B-Test")
        print(f"Hypothese eingefügt ID={hid}")

        # Empathy
        eid = insert_empathy_snap(ts, "happy", 0.9)
        print(f"EmpathySnap eingefügt ID={eid}")

        # Coverage
        cid = insert_coverage(ts, 0.75, 15, 20)
        print(f"CoverageLog eingefügt ID={cid}")

        # Metric
        mid = insert_metric("heartbeat", 1.0, ts)
        print(f"Metric eingefügt ID={mid}")

        # SciCalc
        sid = insert_scicalc_result(ts, "x^2", "eval", "{'x':2}", "4.0")
        print(f"SciCalcResult eingefügt ID={sid}")

        # SetCalc (NEU)
        lid = insert_setcalc_log(ts, "union",
                                 setA='["1","2","3"]',
                                 setB='["3","4"]',
                                 result='["1","2","3","4"]')
        print(f"SetCalcLog eingefügt ID={lid}")

        # SnapChain (NEU)
        sc_id = insert_snapchain({"blob": b'{}', "origin": "selftest", "notes": "dummy"})
        print(f"SnapChain eingefügt ID={sc_id}")

        # SnapIndex (NEU Snap v1.1)
        fp = "selftest-fp"
        si_id = insert_snap_index(
            ts=time.time(),
            source="selftest",
            privacy_tier="public",
            feature_dim=3,
            l2_norm=1.0,
            fingerprint=fp,
            payload=b"{}",
            dedup=True,
        )
        print(f"SnapIndex eingefügt/aktualisiert ID={si_id}")
        row = fetch_snap_index_by_fingerprint(fp)
        print("SnapIndex by fingerprint:", row)

        # Curriculum-State (NEU v3.7)
        state = fetch_curriculum_state()
        print("Curriculum-State (vor Update):", state)
        ok = update_curriculum_state(stage=state.get("stage", 1),
                                     progress_json='{"acc":0.8,"episodes":10,"reward_mean":0.1,"difficulty":2}',
                                     window_json='{"repeat_queue":[]}',
                                     last_update=int(time.time()))
        print("Curriculum-State Update OK:", ok)
        print("Curriculum-State (nach Update):", fetch_curriculum_state())

        print("[sql_manager] OK ✅")
    else:
        print("Usage: python -m core.sql_manager [--ensure | --rebuild | --test]")
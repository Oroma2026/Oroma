#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/learning.py
# Projekt:   ORÓMA (Flask UI · Learning Dashboard · Headless)
# Modul:     Learning Blueprint – Charts/Curves/CSV/Selftest/Energy (stats.db bevorzugt, oroma.db read-only Fallback + On-Demand Sampling)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist ein Flask-Blueprint (`learning_bp`) für das ORÓMA Learning-Dashboard.
# Fokus:
#   - stabile, schnelle UI-Kurven im Live-Betrieb (Orchestrator + viele Writer)
#   - minimale Locks/Wait-Times (kurze sqlite timeouts, busy_timeout)
#   - robust gegen „Sampling/Truncation“ der DBs in den Projekt-ZIPs
#
# Kernprinzip:
#   - stats.db ist der bevorzugte Read-Pfad (Cache/Spiegel-DB, UI-freundlich)
#   - oroma.db wird nur read-only und nur als Fallback/On-Demand genutzt
#
# WARUM stats.db (PRODUKTIONSREALITÄT)
# ────────────────────────────────────
# oroma.db ist im Live-System stark genutzt:
#   - AgentLoop Hooks schreiben Metrics/SnapChains
#   - Orchestrator startet parallel Tools (stats_snapshot, energy_manager, dream, …)
#   - SQLite hat nur einen Writer gleichzeitig → UI kann sonst hängen
#
# Deshalb:
#   - UI liest bevorzugt stats.db (WAL, kurze Queries, rebuildbar)
#   - Wenn stats.db „Lücken“ hat, wird kontrolliert nachgesampelt (On-Demand)
#
# ROUTES / ENDPOINTS (Blueprint Prefix: /learning)
# ───────────────────────────────────────────────
#   GET  /learning/                   → HTML: templates/learning.html
#   GET  /learning/api/data            → Quick-Status/Defaults (DB-Pfad, Limits, counts)
#   GET  /learning/api/history         → Zeitreihenpunkte für Charts (Rewards/Metrics/Empathy/Coverage)
#   GET  /learning/api/curve           → Lernkurve (Tag/Woche/Monat Tabellen + Serien)
#   GET  /learning/api/curve.csv       → CSV-Export der Kurven-Summary
#   GET  /learning/api/selftest        → Diagnose (DB-Zugriff, Schema, Punktzahlen)
#   GET  /learning/api/testgraph       → Testdaten (Phantasie; unabhängig von DB)
#   GET  /learning/api/energy/top      → Energy-Top-Listen (Cache in stats.db)
#
# DB-VERBINDUNGEN (WICHTIGE UI-SICHERHEIT)
# ────────────────────────────────────────
# Dieses Modul nutzt bewusst eigene sqlite-Connect-Helper:
#   - kurze timeouts (UI darf nicht blockieren)
#   - PRAGMA busy_timeout zusätzlich (ms)
#   - Row-Factory sqlite3.Row
#
# oroma.db:
#   - wird via URI `mode=ro` read-only geöffnet (file:...?...mode=ro)
#
# stats.db:
#   - wird read-write geöffnet (damit On-Demand Sampling + Migration möglich ist)
#   - setzt PRAGMA journal_mode=WAL, synchronous=NORMAL, temp_store=MEMORY
#
# stats.db SCHEMA + MIGRATION (IN DIESEM MODUL ENTHALTEN)
# ───────────────────────────────────────────────────────
# Dieses Modul stellt sicher, dass stats.db für die UI konsistent ist:
#
# Tabellen:
#   - stats_meta   (k TEXT PRIMARY KEY, v TEXT) → Checkpoints/Migrationsmarker
#   - stats_points (ts, series, value, src_table, src_id, src_uid, meta)
#   - energy_top_cache (kind PRIMARY KEY, ts, payload_json)
#
# Performance-Indizes:
#   - idx_stats_points_series_ts ON stats_points(series, ts)
#   - idx_stats_points_ts        (ergänzt für ORDER BY ts DESC LIMIT)
#
# KRITISCHER REAL-WORLD BUGFIX: Unique-Index Migration
# ───────────────────────────────────────────────────
# In realen stats.db-Instanzen wurde ein falscher Unique Index gesehen:
#   UNIQUE(src_table, src_id, series)
# → für "metrics" fatal, weil src_id dort oft 0 ist → pro series bleibt nur 1 Punkt.
#
# Dieses Modul erkennt das und migriert auf:
#   UNIQUE(src_table, src_uid, series)
#
# Zusätzlich:
#   - src_uid wird bei Legacy-Rows nachträglich berechnet (sha1 über ts|series|value|meta|rowid)
#   - vorhandene Duplikate werden best effort dedupliziert (latest rowid bleibt)
#
# ON-DEMAND SAMPLING (LÜCKENFÜLLER)
# ─────────────────────────────────
# Wenn stats.db zwar Daten hat, aber einzelne Gruppen „zu leer“ sind (z. B. rewards vorhanden,
# aber metrics/empathy/coverage fehlen), wird kontrolliert aus oroma.db nachgezogen:
#   - Mindestpunktzahl pro Gruppe: OROMA_LEARNING_MIN_POINTS_PER_GROUP (Default 25)
#   - Nachgezogene Punkte werden in stats_points geschrieben (idempotent über ux_stats_points_src)
#
# ENERGY-TOP LISTEN (CACHE)
# ─────────────────────────
# /learning/api/energy/top liest bevorzugt aus stats.db:
#   - energy_top_cache(kind="objects"|"relations") → payload_json
# Der Producer ist typischerweise ein Tool/Job (z. B. energy_manager).
# Dieses Modul ist Consumer (read-only Semantik), kann aber Cache-Age prüfen.
#
# WICHTIGE ENV-VARIABLEN (AKTUELLER CODEPFAD)
# ───────────────────────────────────────────
# DB:
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_STATS_DB_PATH=/opt/ai/oroma/data/stats.db
#
# Limits/Keys:
#   OROMA_LEARNING_LIMIT=2000
#   OROMA_LEARNING_METRICS_KEYS=<comma list>
#   OROMA_LEARNING_MIN_POINTS_PER_GROUP=25
#
# Energy:
#   OROMA_ENERGY_TOP_LIMIT=20
#   OROMA_ENERGY_CACHE_MAX_AGE_SEC=600
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - UI darf niemals „hängen“: kurze sqlite timeouts + busy_timeout bleiben Pflicht.
# - stats.db ist der primäre Read-Pfad; oroma.db nur read-only Fallback.
# - Unique-Index Migration (src_uid) ist produktionsrelevant und muss bleiben.
# - On-Demand Sampling ist bewusst enthalten (Backup/Sampling-ZIPs führen sonst zu „0“ Gruppen).
# - Modul bleibt headless (keine Browser-Automation, keine externen Chart-Libs serverseitig).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import hashlib
import json
import os
import random
import math
import sqlite3
from core import db_writer_client as _dbw
import time
import threading
from pathlib import Path
import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, render_template, request
import logging
# NOTE (2026-01-25): core.log_guard.log_suppressed() ist in v3.7.3 als
#   log_suppressed(logger, *, key, msg, exc, level, interval_s)
# implementiert (kw-only key/msg + expliziter Logger).
#
# In der Learning-UI wurde historisch jedoch ein kompakter Wrapper ohne
# expliziten Logger genutzt:
#   log_suppressed('ui/learning.py:...', exc=e, level=logging.ERROR)
#
# Durch mehrere Hot-Patches an learning.py/learning.html/energy_manager.py kann
# es passieren, dass Call-Sites den alten Wrapper-Stil benutzen, während die
# Core-Implementierung bereits das neue Signatur-Design erwartet.
#
# Konsequenz in der Praxis:
# - Der Endpoint baut zwar Daten, bricht aber beim Logging mit TypeError ab,
#   wodurch Safari/Fetch dann "AbortError" o. a. sieht und UI scheinbar "—" zeigt.
#
# Lösung:
# - Wir importieren die Core-Funktion als _guard_log_suppressed und definieren
#   hier einen kompatiblen Wrapper mit der alten Call-Site-Signatur.
# - Wichtig: Jeder Fallback wird weiterhin geloggt – es gibt keine stillen
#   Rueckfaelle.
from core.log_guard import log_suppressed as _guard_log_suppressed

# Flask Blueprint (URL-Prefix: /learning)
# NOTE: Diese Definition MUSS vor allen @learning_bp.* Decorators existieren.
learning_bp = Blueprint("learning", __name__, url_prefix="/learning")


def log_suppressed(*args, **kwargs) -> None:
    """Kompatibler Suppress-Logger (UI-sicher, niemals TypeError).

    Hintergrund:
      core.log_guard.log_suppressed() hat eine *keyword-only* Signatur:
        log_suppressed(logger, *, key, msg, exc=None, level=logging.WARNING, interval_s=...)
      Historische Call-Sites nutzen aber auch die Kurzform:
        log_suppressed("some/key", exc=e, level=...)
        log_suppressed("some/key", "message", exc=e, level=...)

    Dieses Wrapper akzeptiert beide Formen und verhindert, dass API/Worker
    durch Logging-Fehler "still" abbrechen (wichtig für Export/Maxima/Energy).
    """
    try:
        # Form (1): Neuer Guard-Call: erster Parameter ist Logger
        if args and isinstance(args[0], logging.Logger):
            return _guard_log_suppressed(*args, **kwargs)

        # Form (2): Legacy Kurzform
        key = kwargs.pop("key", None)
        msg = kwargs.pop("msg", None)
        exc = kwargs.pop("exc", None)
        level = kwargs.pop("level", logging.WARNING)
        interval_s = kwargs.pop("interval_s", 30.0)

        if args:
            if key is None:
                key = str(args[0])
            if len(args) > 1 and msg is None:
                msg = str(args[1])

        if key is None:
            key = "ui/learning.py:unknown"
        if msg is None:
            msg = str(exc) if exc is not None else ""

        logger = logging.getLogger("oroma.learning")
        return _guard_log_suppressed(logger, key=key, msg=msg, exc=exc, level=level, interval_s=interval_s)
    except Exception:
        # Logging must never break API paths.
        return


# =============================================================================
# BLOCK: ENV_DEFAULTS (SWAPPABLE)
# =============================================================================
OROMA_DB_PATH = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
OROMA_STATS_DB_PATH = os.environ.get("OROMA_STATS_DB_PATH", "/opt/ai/oroma/data/stats.db")

LEARNING_LIMIT = int(os.environ.get("OROMA_LEARNING_LIMIT", "2000"))

DEFAULT_METRICS_KEYS = (
    "reward_curriculum,reward_speech,reward_empathy,"
    "cam:token:candidate,cam:token:skip_quality,self_rec_score"
)
METRICS_KEYS_ENV = os.environ.get("OROMA_LEARNING_METRICS_KEYS", DEFAULT_METRICS_KEYS)

ENERGY_TOP_LIMIT = int(os.environ.get("OROMA_ENERGY_TOP_LIMIT", "20"))
ENERGY_CACHE_MAX_AGE_SEC = int(os.environ.get("OROMA_ENERGY_CACHE_MAX_AGE_SEC", "600"))

# Learning API Cache (stats.db.energy_top_cache) – default 2h
LEARNING_CACHE_MAX_AGE_SEC = int(os.environ.get("OROMA_LEARNING_CACHE_MAX_AGE_SEC", "7200"))
LEARNING_HISTORY_CACHE_MAX_AGE_SEC = int(os.environ.get("OROMA_LEARNING_HISTORY_CACHE_MAX_AGE_SEC", "300"))
LEARNING_VISION_PRIMITIVES_CACHE_MAX_AGE_SEC = int(os.environ.get("OROMA_LEARNING_VISION_PRIMITIVES_CACHE_MAX_AGE_SEC", "180"))

# Alles-Export CSV Cache (auf Dateisystem; dient als schneller Download ohne DB-Scan)
OROMA_LEARNING_EXPORT_CACHE_PATH = os.environ.get("OROMA_LEARNING_EXPORT_CACHE_PATH", "/opt/ai/oroma/data/cache/learning_export_all.csv")

# Maximal erlaubtes Alter (Sekunden) für den Alles-Export Cache.
# Default: 2h – passend zum User-Wunsch "alle 2 Stunden".
# Hinweis: Falls der Cache älter ist, wird weiterhin *sofort* ausgeliefert
# (stale), aber ein Rebuild wird asynchron angestoßen und geloggt.
OROMA_LEARNING_EXPORT_MAX_AGE_SEC = int(os.environ.get("OROMA_LEARNING_EXPORT_MAX_AGE_SEC", "7200"))

# Hintergrund-Worker Intervall (Maxima + Export), Default: alle 2 Stunden
OROMA_LEARNING_BG_INTERVAL_SEC = int(os.environ.get("OROMA_LEARNING_BG_INTERVAL_SEC", "7200"))
# Wenn 1 (default), startet der Learning-Background-Worker beim Modulimport
# (und zusaetzlich beim UI-Page-Load als Absicherung). Dadurch werden
# Maxima/Exports auch ohne manuelle UI-Aktion periodisch erzeugt,
# beispielsweise nach einem Service-Neustart.
OROMA_LEARNING_BG_AUTOSTART = int(os.environ.get("OROMA_LEARNING_BG_AUTOSTART", "1"))


# Neu: Mindestpunkte pro Gruppe, bevor der On-Demand-Sampler nachzieht.
# Hintergrund: stats_points kann durch Limit/Backup/Sampling reward-lastig sein → metrics/empathy/coverage “leer”.
MIN_POINTS_PER_GROUP = int(os.environ.get("OROMA_LEARNING_MIN_POINTS_PER_GROUP", "25"))
# =============================================================================
# END BLOCK: ENV_DEFAULTS
# =============================================================================

def _now() -> int:
    return int(time.time())


def _safe_int(x: Any, d: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return d


def _safe_float(x: Any, d: float = 0.0) -> float:
    """Converts to float and guarantees JSON-safe finite output.

    Hintergrund
    ----------
    In der Learning-UI werden viele Werte (AVG/MAX/Quoten) als JSON an den Browser geliefert.
    Sobald irgendwo NaN/Inf entsteht, wird das JSON ungueltig (Browser-JSON ist strikt) und
    die UI faellt auf '—' zurueck.

    Regel: Nur endliche floats werden akzeptiert; sonst Default d.
    """
    try:
        v = float(x)
        if not math.isfinite(v):
            return d
        return v
    except Exception:
        return d


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


def _json_sanitize(obj: Any) -> Any:
    """Recursively sanitize an object tree for strict JSON.

    Hintergrund
    ----------
    Python/Flask koennen (je nach Encoder) NaN/Inf in JSON schreiben. Der Browser nicht.
    Sobald das passiert, ist das Response-JSON ungueltig und die Learning-UI zeigt
    fuer ganze Sektionen nur noch '—' (z.B. Maxima-Tables oder Mind-Widgets).

    Strategie
    ---------
    - float NaN/Inf -> None
    - dict/list -> rekursiv
    - sonst: unveraendert
    """
    if obj is None:
        return None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (int, str, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]
    return obj



# =============================================================================
# BLOCK: DB_HELPERS (SWAPPABLE)
# =============================================================================
def _connect_sqlite(path: str, readonly: bool = False, timeout_sec: float = 1.0) -> sqlite3.Connection:
    """
    SQLite connect helper:
    - kurzer timeout (UI darf nicht hängen)
    - busy_timeout PRAGMA (zusätzlich)
    - row_factory = sqlite3.Row
    """
    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
    else:
        conn = sqlite3.connect(path, timeout=timeout_sec)

    conn.execute("PRAGMA busy_timeout = 800")  # ms
    conn.row_factory = sqlite3.Row
    return conn



def _db_connect(path: str, readonly: bool = False, timeout_sec: float = 1.0) -> sqlite3.Connection:
    """Backward-compatible alias for older code paths.

    Hintergrund
    ----------
    In mehreren Patch-Iterationen wurde historisch `_db_connect()` verwendet.
    Spätere Refactors haben DB-Zugriffe auf `_connect_sqlite()` konsolidiert.
    Wenn einzelne Stellen noch `_db_connect()` referenzieren, führt das zu
    `NameError` → 500 (z.B. /learning/api/selftest, /learning/api/energy/top).

    Minimal-Invasiv
    ----------------
    - Keine Semantik-Änderung: Delegation auf `_connect_sqlite()`.
    - Caller müssen die Connection weiterhin schließen.
    """
    return _connect_sqlite(path, readonly=readonly, timeout_sec=timeout_sec)

def _get_main_conn_readonly() -> sqlite3.Connection:
    return _connect_sqlite(OROMA_DB_PATH, readonly=True, timeout_sec=1.0)


def _get_stats_conn_rw() -> sqlite3.Connection:
    """
    Öffnet stats.db für Schreib-Zugriffe (RW) für den UI-Sampler/Cache.

    Produktions-Problem (observed):
      - Parallel-Jobs (z.B. oroma-energy.timer) schreiben ebenfalls nach stats.db.
      - Ein gleichzeitiges PRAGMA `journal_mode=WAL` benötigt zeitweise einen exklusiven Lock.
      - Wenn stats.db in diesem Moment "busy" ist, kann sqlite3.OperationalError("database is locked")
        auftreten und UI-Endpunkte unnötig als 500 crashen.

    Lösung (minimal-invasiv):
      - Connection bleibt kurz (timeout klein, busy_timeout zusätzlich).
      - WAL/PRAGMA werden **best-effort** gesetzt: Lock-Fehler werden ignoriert,
        damit UI-Reads/Exports nicht abbrechen.
      - Caller müssen die Connection immer schließen (finally/with).
    """
    conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=False, timeout_sec=1.5)

    # UI darf nicht hängen, aber wir wollen kurze Lock-Kollisionen abfedern.
    try:
        conn.execute("PRAGMA busy_timeout = 2500")  # ms (überschreibt ggf. _connect_sqlite Default)
    except Exception:
        pass

    # PRAGMA: best-effort (keine harten 500er bei writer-peaks)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA temp_store = MEMORY")
    except Exception:
        pass

    return conn
# =============================================================================

def _get_stats_conn_ro_fast() -> sqlite3.Connection:
    """Open stats.db read-only for ultra-fast UI reads.

    Rationale:
    - /learning/api/intelligence must never block on writer activity.
    - Use readonly connection with tiny timeouts; no WAL/PRAGMA changes here.
    - Caller should use 'with' to ensure close.
    """
    conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=0.25)
    try:
        conn.execute("PRAGMA busy_timeout = 200")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA query_only = 1")
    except Exception:
        pass
    return conn

# END BLOCK: DB_HELPERS
# =============================================================================


# =============================================================================
# BLOCK: STATS_DB_SCHEMA (SWAPPABLE)
# =============================================================================
def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == col for r in rows)
    except Exception:
        return False


def _ensure_stats_schema(conn: sqlite3.Connection) -> None:
    """
    Stellt stats.db Schema sicher + Migration.

    WICHTIG:
    - idx_stats_points_ts(ts) wird ergänzt, damit ORDER BY ts DESC LIMIT N schnell bleibt.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS stats_meta (k TEXT PRIMARY KEY, v TEXT)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stats_points (
          ts        INTEGER NOT NULL,
          series    TEXT    NOT NULL,
          value     REAL    NOT NULL,
          src_table TEXT    NOT NULL,
          src_id    INTEGER NOT NULL DEFAULT 0,
          src_uid   TEXT    NOT NULL,
          meta      TEXT    NULL
        )
        """
    )

    # Migration legacy columns
    if not _has_col(conn, "stats_points", "src_table"):
        conn.execute("ALTER TABLE stats_points ADD COLUMN src_table TEXT NOT NULL DEFAULT 'legacy'")
    if not _has_col(conn, "stats_points", "src_id"):
        conn.execute("ALTER TABLE stats_points ADD COLUMN src_id INTEGER NOT NULL DEFAULT 0")
    if not _has_col(conn, "stats_points", "src_uid"):
        conn.execute("ALTER TABLE stats_points ADD COLUMN src_uid TEXT NOT NULL DEFAULT ''")
    if not _has_col(conn, "stats_points", "meta"):
        conn.execute("ALTER TABLE stats_points ADD COLUMN meta TEXT NULL")

    # Indizes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_points_series_ts ON stats_points(series, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_points_ts ON stats_points(ts)")

    # Legacy uid-fill (best effort)
    mig_key = "migrated_stats_points_uid_v1"
    row = conn.execute("SELECT v FROM stats_meta WHERE k=?", (mig_key,)).fetchone()
    if not row:
        try:
            conn.execute("UPDATE stats_points SET src_table='legacy' WHERE src_table IS NULL OR src_table=''")
            rows = conn.execute(
                """
                SELECT rowid, ts, series, value, COALESCE(meta,'') AS meta
                  FROM stats_points
                 WHERE src_uid IS NULL OR src_uid=''
                """
            ).fetchall()

            for r in rows:
                rid = _safe_int(r["rowid"])
                ts = _safe_int(r["ts"])
                series = str(r["series"])
                value = _safe_float(r["value"])
                meta = str(r["meta"] or "")
                uid = _sha1(f"{ts}|{series}|{value}|{meta}|{rid}")
                conn.execute("UPDATE stats_points SET src_uid=? WHERE rowid=?", (uid, rid))

            conn.execute(
                "INSERT INTO stats_meta(k,v) VALUES(?,?)",
                (mig_key, str(_now())),
            )
        except Exception as e:
            log_suppressed('ui/learning.py:242', exc=e, level=logging.WARNING)
            pass

    # -------------------------------------------------------------------------
    # Unique Index MIGRATION (WICHTIG)
    #
    # Legacy-Bug (in realen stats.db gesehen):
    #   ux_stats_points_src war teils definiert als UNIQUE(src_table, src_id, series)
    # Das ist für "metrics" fatal, weil src_id dort oft 0 ist → pro series nur 1 Punkt.
    #
    # Ziel:
    #   UNIQUE(src_table, src_uid, series)
    # Damit kann metrics/history wachsen, Dupe-Schutz funktioniert sauber.
    # -------------------------------------------------------------------------
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_stats_points_src' LIMIT 1"
        ).fetchone()

        legacy_wrong = False
        if row and row["sql"]:
            s = str(row["sql"]).lower()
            # legacy: src_id drin, src_uid NICHT drin
            if ("src_id" in s) and ("src_uid" not in s):
                legacy_wrong = True
        elif row and (row["sql"] is None):
            # defensiv: auch bei NULL-SQL (selten) lieber neu aufbauen
            legacy_wrong = True

        if legacy_wrong:
            try:
                conn.execute("DROP INDEX IF EXISTS ux_stats_points_src")
            except Exception as e:
                log_suppressed('ui/learning.py:275', exc=e, level=logging.WARNING)
                pass

        # Dedup best-effort (nur falls bereits Duplikate existieren)
        try:
            dup = conn.execute(
                """
                SELECT 1
                  FROM stats_points
              GROUP BY src_table, src_uid, series
                HAVING COUNT(*) > 1
                 LIMIT 1
                """
            ).fetchone()
            if dup:
                conn.execute(
                    """
                    DELETE FROM stats_points
                     WHERE rowid NOT IN (
                       SELECT MAX(rowid)
                         FROM stats_points
                     GROUP BY src_table, src_uid, series
                     )
                    """
                )
        except Exception as e:
            log_suppressed('ui/learning.py:301', exc=e, level=logging.WARNING)
            pass

        # Finaler, korrekter Unique Index
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_stats_points_src "
                "ON stats_points(src_table, src_uid, series)"
            )
        except Exception as e:
            log_suppressed('ui/learning.py:311', exc=e, level=logging.WARNING)
            pass

    except Exception as e:
        log_suppressed('ui/learning.py:315', exc=e, level=logging.WARNING)
        pass

    # energy_top_cache (read-only consumer in UI; producer ist Energy-Collector)

    # energy_top_cache (read-only consumer in UI; producer ist Energy-Collector)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS energy_top_cache (
          kind         TEXT PRIMARY KEY,
          ts           INTEGER NOT NULL,
          payload_json TEXT NOT NULL
        )
        """
    )

    conn.commit()
# =============================================================================
# END BLOCK: STATS_DB_SCHEMA
# =============================================================================
# =============================================================================
# BLOCK: LEARNING_API_CACHE (SWAPPABLE)
# =============================================================================
def _cache_get_json(
    kind: str,
    *,
    max_age_sec: int,
    allow_stale: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Reads a cached JSON payload from stats.db.energy_top_cache.

    We intentionally reuse energy_top_cache as a generic lightweight cache table.
    This avoids schema changes and works well in highly concurrent environments.

    Parameters
    ----------
    kind:
        Cache key, e.g. "learning:intelligence" or "learning:maxima".
    max_age_sec:
        Freshness threshold.
    allow_stale:
        If True, return cached payload even if older than max_age_sec.

    Returns
    -------
    (payload_or_none, meta)
    meta contains:
      - cache: "hit"|"miss"
      - kind
      - cache_ts
      - cache_age_sec
      - stale (bool)
      - max_age_sec
    """
    now = _now()
    meta: Dict[str, Any] = {
        "cache": "miss",
        "kind": kind,
        "cache_ts": 0,
        "cache_age_sec": None,
        "stale": None,
        "max_age_sec": int(max_age_sec),
    }

    try:
        conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.0)
        row = conn.execute(
            "SELECT ts, payload_json FROM energy_top_cache WHERE kind=?",
            (kind,),
        ).fetchone()
        conn.close()

        if not row:
            return None, meta

        cache_ts = _safe_int(row["ts"])
        age = (now - cache_ts) if cache_ts else None
        stale = (age is not None) and (age > int(max_age_sec))

        meta.update({"cache": "hit", "cache_ts": cache_ts, "cache_age_sec": age, "stale": stale})

        if stale and (not allow_stale):
            return None, meta

        try:
            payload = json.loads(row["payload_json"] or "{}")
            if isinstance(payload, dict):
                return payload, meta
        except Exception as e:
            log_suppressed("ui/learning.py:cache_get_json", exc=e, level=logging.WARNING)
            return None, meta

        return None, meta

    except Exception as e:
        log_suppressed("ui/learning.py:cache_get_json_outer", exc=e, level=logging.WARNING)
        meta["error"] = str(e)
        return None, meta




def _dbw_enabled() -> bool:
    try:
        return bool(int(os.getenv("OROMA_DBW_ENABLE", "0")))
    except Exception:
        return False

def _cache_put_json(kind: str, payload: Dict[str, Any], *, ts: Optional[int] = None) -> bool:
    """Writes JSON payload to stats.db.energy_top_cache.

    Stufe C (DBWriter Multi-DB):
      - bevorzugt DBWriter (db='stats') um SQLite Write-Contention zu vermeiden.
      - Fallback bleibt lokal (nur wenn DBWriter deaktiviert/nicht verfuegbar).

    Returns True on success, False otherwise.
    """
    try:
        now_ts = _safe_int(ts, _now())
        payload_json = json.dumps(_json_sanitize(payload), ensure_ascii=False, allow_nan=False)

        if _dbw_enabled() and '_dbw' in globals() and _dbw is not None:
            try:
                sql = (
                    "INSERT INTO energy_top_cache(kind, ts, payload_json) VALUES(?,?,?) "
                    "ON CONFLICT(kind) DO UPDATE SET ts=excluded.ts, payload_json=excluded.payload_json"
                )
                _dbw.exec_write(
                    sql,
                    [str(kind), int(now_ts), str(payload_json)],
                    tag="ui.learning.cache_put_json",
                    priority="low",
                    timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_STATS", "5000")),
                    db="stats",
                )
                return True
            except Exception as e:
                log_suppressed("ui/learning.py:cache_put_json.dbw", exc=e, level=logging.WARNING)

        # Legacy/local fallback
        conn = _get_stats_conn_rw()
        _ensure_stats_schema(conn)
        conn.execute(
            "INSERT INTO energy_top_cache(kind, ts, payload_json) VALUES(?,?,?) "
            "ON CONFLICT(kind) DO UPDATE SET ts=excluded.ts, payload_json=excluded.payload_json",
            (kind, now_ts, payload_json),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log_suppressed("ui/learning.py:cache_put_json", exc=e, level=logging.WARNING)
        return False
# =============================================================================
# BLOCK: LEARNING_HISTORY_CACHE_HELPERS (SWAPPABLE)
# =============================================================================
def _normalize_metrics_keys(metrics_keys: Optional[List[str]]) -> List[str]:
    """Normalisiert optionale metrics_keys fuer stabile Cache-Keys und Responses."""
    out: List[str] = []
    seen = set()
    for item in metrics_keys or []:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out)


def _history_cache_kind(window_sec: int, metrics_keys: Optional[List[str]]) -> str:
    """Bildet einen stabilen Cache-Key fuer /learning/api/history.

    Warum parametrisiert?
    -------------------
    Die Learning-UI kann unterschiedliche Fenster und optional unterschiedliche
    metric:* Serien anfragen. Ein pauschaler Key wuerde sonst Antworten
    vermischen und spaeter schwer nachvollziehbare UI-Artefakte erzeugen.
    """
    norm = _normalize_metrics_keys(metrics_keys)
    metrics_part = ",".join(norm) if norm else "default"
    return f"learning:history:v1:window={int(window_sec)}:metrics={metrics_part}"


def _build_history_payload(window_sec: int, metrics_keys: Optional[List[str]]) -> Dict[str, Any]:
    """Berechnet den Payload fuer /learning/api/history ohne Cache-Logik.

    Diese Funktion kapselt bewusst den teuren stats.db-Lesepfad, damit
    api_history() sowohl einen Fast-Path (Cache) als auch einen optionalen
    Hintergrund-Recompute nutzen kann.
    """
    try:
        window_sec = int(window_sec)
    except Exception:
        window_sec = 86400
    metrics_keys = _normalize_metrics_keys(metrics_keys)

    def _counts(pts: List[Dict[str, Any]]) -> Dict[str, int]:
        c = {"rewards": 0, "metrics": 0, "empathy": 0, "coverage": 0}
        for p in pts or []:
            s = str(p.get("series", ""))
            if s.startswith("reward:"):
                c["rewards"] += 1
            elif s.startswith("metric:"):
                c["metrics"] += 1
            elif s == "empathy_score":
                c["empathy"] += 1
            elif s == "coverage_30d":
                c["coverage"] += 1
        return c

    # Zusatz: Snap/Token/Label Counts (aus oroma.db, read-only)
    def _snap_counts(window_seconds: int) -> Dict[str, int]:
        now_ts = _now()
        since_ts = now_ts - int(max(1, window_seconds))
        want = ("audio/token", "vision/token", "link/a_label", "link/av_label")
        outc: Dict[str, int] = {k: 0 for k in want}
        conn = None
        try:
            conn = _get_main_conn_readonly()
            rows = conn.execute(
                """
                SELECT origin, COUNT(*) AS n
                FROM snapchains
                WHERE ts >= ?
                  AND origin IN (?,?,?,?)
                  AND (status IS NULL OR status != 'deleted')
                GROUP BY origin
                """,
                (int(since_ts), *want),
            ).fetchall()
            for r in rows or []:
                try:
                    outc[str(r["origin"])] = int(r["n"])
                except Exception:
                    outc[str(r[0])] = int(r[1])
            return outc
        except Exception:
            return outc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    t0 = time.time()
    ok_stats, pts = _fetch_points_from_stats(window_sec, metrics_keys=metrics_keys)
    counts = _counts(pts)
    snapcounts = _snap_counts(window_sec)

    need_sampler = (
        (not pts)
        or (counts["empathy"] < MIN_POINTS_PER_GROUP)
        or (counts["coverage"] < MIN_POINTS_PER_GROUP)
        or (counts["metrics"] < MIN_POINTS_PER_GROUP)
    )

    sampler_result: Optional[Dict[str, Any]] = None
    sampler_triggered = False
    force_sampler = False
    if need_sampler:
        sampler_triggered = True
        try:
            _trigger_bg_one_shot(
                "sampler",
                lambda: _sample_into_stats_from_main(metrics_keys=metrics_keys, max_rows_each=800),
                reason="history_cache_build",
            )
        except Exception as e:
            log_suppressed("ui/learning.py:history_cache_trigger_sampler", exc=e, level=logging.ERROR)

    now_ts = int(time.time())

    def _latest_ts(pred) -> int:
        for p in pts:
            try:
                if pred(p):
                    return int(p.get("ts", 0) or 0)
            except Exception:
                continue
        return 0

    last_ts = {
        "rewards": _latest_ts(lambda p: str(p.get("series", "")).startswith("reward:")),
        "metrics": _latest_ts(lambda p: str(p.get("series", "")).startswith("metric:")),
        "empathy": _latest_ts(lambda p: str(p.get("series", "")) == "empathy_score"),
        "coverage": _latest_ts(lambda p: str(p.get("series", "")).startswith("coverage")),
    }

    payload = {
        "meta": {
            "stats_db_ok": bool(ok_stats),
            "window_sec": window_sec,
            "metrics_keys": metrics_keys,
            "points": len(pts),
            "counts": counts,
            "snapcounts": snapcounts,
            "sampler": sampler_result,
            "sampler_triggered": bool(sampler_triggered),
            "force_sampler": bool(force_sampler),
            "dur_ms": int((time.time() - t0) * 1000.0),
            "server_ts": now_ts,
            "server_ts_local": datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S"),
            "last_ts": last_ts,
            "last_ts_local": {
                k: (datetime.datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S") if v else "")
                for k, v in last_ts.items()
            },
        },
        "points": pts,
    }
    return _json_sanitize(payload)


def _recompute_history_cache(*, window_sec: int, metrics_keys: Optional[List[str]], reason: str = "manual") -> bool:
    """Berechnet den History-Payload und schreibt ihn in den generischen JSON-Cache."""
    try:
        payload = _build_history_payload(window_sec=window_sec, metrics_keys=metrics_keys)
        kind = _history_cache_kind(window_sec=window_sec, metrics_keys=metrics_keys)
        ok = _cache_put_json(kind, payload, ts=_now())
        level = logging.INFO if ok else logging.ERROR
        log_suppressed("ui/learning.py:history_cache_recompute", exc=Exception(f"ok={ok} reason={reason} kind={kind}"), level=level)
        return bool(ok)
    except Exception as e:
        log_suppressed("ui/learning.py:history_cache_recompute_failed", exc=e, level=logging.ERROR)
        return False
# =============================================================================
# END BLOCK: LEARNING_HISTORY_CACHE_HELPERS
# =============================================================================

# =============================================================================
# END BLOCK: LEARNING_API_CACHE
# =============================================================================

# =============================================================================
# BLOCK: LEARNING_BG_WORKER (SWAPPABLE)
# =============================================================================
# Zweck
# -----
# Einige Learning-Endpunkte koennen bei grossen stats_points Tabellen langsam
# werden (Maxima-Recompute, CSV Alles-Export). Safari bricht Fetches nach einer
# Weile ab (AbortError).
#
# Design
# ------
# - HTTP-Endpunkte liefern bevorzugt Cache/Stale, niemals still leer.
# - Recompute/Export laufen *asynchron* im Hintergrund (Thread).
# - Jede Fallback-Entscheidung wird geloggt (keine stillen Rueckfaelle).
#
# Konfiguration
# -------------
# - OROMA_LEARNING_BG_INTERVAL_SEC (default 7200)
# - OROMA_LEARNING_EXPORT_CACHE_PATH
#
# Hinweis
# -------
# ORÓMA laeuft typischerweise als Single-Process Flask Service. Falls mehrere
# Prozesse aktiv sind, koennen mehrere Threads starten. Das ist akzeptabel:
# - Maxima Cache ist idempotent (UPSERT)
# - Export Cache schreibt atomar (os.replace)
#
_LEARNING_BG_LOCK = threading.Lock()
_LEARNING_BG_STARTED = False
_LEARNING_BG_THREAD: Optional[threading.Thread] = None


def _ensure_dir_for_file(path: str) -> None:
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    except Exception as e:
        log_suppressed('ui/learning.py:ensure_dir_for_file', exc=e, level=logging.WARNING)


def _start_learning_bg_worker(reason: str = 'auto') -> None:
    global _LEARNING_BG_STARTED, _LEARNING_BG_THREAD
    with _LEARNING_BG_LOCK:
        if _LEARNING_BG_STARTED:
            return
        _LEARNING_BG_STARTED = True
        _LEARNING_BG_THREAD = threading.Thread(
            target=_learning_bg_loop,
            name='oroma-learning-bg',
            daemon=True,
        )
        try:
            log_suppressed('ui/learning.py:bg_worker_start', exc=Exception(f'start reason={reason} interval={OROMA_LEARNING_BG_INTERVAL_SEC}s'), level=logging.INFO)
        except Exception:
            pass
        _LEARNING_BG_THREAD.start()


def _trigger_bg_one_shot(job_name: str, fn, reason: str = 'manual') -> None:
    """Startet einen One-Shot-Thread fuer teure Jobs (UI darf nicht blockieren).

    Wichtig: niemals Exceptions nach oben werfen – alles wird geloggt.
    """
    def _runner():
        t0 = time.time()
        try:
            fn()
            dt = time.time() - t0
            log_suppressed('ui/learning.py:bg_one_shot_ok', exc=Exception(f'{job_name} ok reason={reason} dur={dt:.3f}s'), level=logging.INFO)
        except Exception as e:
            dt = time.time() - t0
            log_suppressed('ui/learning.py:bg_one_shot_fail', exc=Exception(f'{job_name} fail reason={reason} dur={dt:.3f}s err={e!r}'), level=logging.ERROR)

    try:
        th = threading.Thread(target=_runner, name=f'oroma-learning-{job_name}', daemon=True)
        th.start()
    except Exception as e:
        log_suppressed('ui/learning.py:bg_one_shot_spawn_fail', exc=e, level=logging.ERROR)


def _learning_bg_loop() -> None:
    """Periodischer Worker: aktualisiert Maxima Cache + baut Export-CSV.

    Intervalle
    ----------
    Das Loop-Intervall ist OROMA_LEARNING_BG_INTERVAL_SEC. Falls ein Durchlauf
    laenger dauert, schlafen wir mindestens 30s, damit keine Busy-Loops entstehen.
    """
    while True:
        t0 = time.time()
        try:
            _recompute_maxima_cache(reason='periodic')
        except Exception as e:
            log_suppressed('ui/learning.py:bg_maxima_fail', exc=e, level=logging.ERROR)

        try:
            _build_export_cache_file(reason='periodic')
        except Exception as e:
            log_suppressed('ui/learning.py:bg_export_fail', exc=e, level=logging.ERROR)

        dt = time.time() - t0
        sleep_s = max(30, int(OROMA_LEARNING_BG_INTERVAL_SEC) - int(dt))
        time.sleep(sleep_s)

# =============================================================================
# END BLOCK: LEARNING_BG_WORKER
# =============================================================================



def _parse_metrics_keys(raw: Optional[str]) -> List[str]:
    s = (raw or "").strip() or METRICS_KEYS_ENV
    keys: List[str] = []
    for part in s.split(","):
        k = part.strip()
        if k:
            keys.append(k)

    out: List[str] = []
    seen = set()
    for k in keys:
        if k not in seen:
            out.append(k)
            seen.add(k)
    return out


def _stats_insert_points(conn: sqlite3.Connection, pts: List[Dict[str, Any]]) -> int:
    """
    Insert OR IGNORE (Dupe-Schutz via ux_stats_points_src falls vorhanden)
    """
    n = 0
    for p in pts:
        try:
            ts = _safe_int(p.get("ts"))
            series = str(p.get("series", ""))
            value = _safe_float(p.get("value"))
            meta = p.get("meta")

            src_table = str(p.get("src_table") or "ui_sampler")
            src_id = _safe_int(p.get("src_id", 0))
            src_uid = str(p.get("src_uid") or "")
            if not src_uid:
                src_uid = _sha1(f"{ts}|{series}|{value}|{meta or ''}|{src_table}|{src_id}")

            conn.execute(
                """
                INSERT OR IGNORE INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta)
                VALUES(?,?,?,?,?,?,?)
                """,
                (ts, series, value, src_table, src_id, src_uid, meta),
            )
            n += 1
        except Exception as e:
            log_suppressed('ui/learning.py:381', exc=e, level=logging.WARNING)
            pass
    return n


# =============================================================================
# BLOCK: ON_DEMAND_SAMPLER (SWAPPABLE)
# =============================================================================
def _sample_into_stats_from_main(metrics_keys: List[str], max_rows_each: int = 500) -> Dict[str, Any]:
    """
    Kleiner UI-Sampler:
    - liest nur LIMIT-Batches aus oroma.db (read-only)
    - schreibt in stats.db
    """
    out = {"ok": False, "inserted": 0, "error": ""}

    try:
        sconn = _get_stats_conn_rw()
        _ensure_stats_schema(sconn)
    except Exception as e:
        out["error"] = f"stats.db open failed: {e}"
        return out

    try:
        mconn = _get_main_conn_readonly()
    except Exception as e:
        try:
            sconn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:410', exc=e, level=logging.WARNING)
            pass
        out["error"] = f"oroma.db open failed: {e}"
        return out

    inserted = 0
    try:
        # rewards_log -> reward:<source>
        try:
            rrows = mconn.execute(
                "SELECT id, created_at AS ts, source, reward FROM rewards_log ORDER BY id DESC LIMIT ?",
                (int(max_rows_each),),
            ).fetchall()
            pts: List[Dict[str, Any]] = []
            for r in rrows:
                pts.append(
                    {
                        "ts": _safe_int(r["ts"]),
                        "series": f"reward:{str(r['source'])}",
                        "value": _safe_float(r["reward"]),
                        "src_table": "rewards_log",
                        "src_id": _safe_int(r["id"]),
                        "src_uid": str(_safe_int(r["id"])),
                    }
                )
            inserted += _stats_insert_points(sconn, pts)
        except Exception as e:
            log_suppressed('ui/learning.py:437', exc=e, level=logging.WARNING)
            pass

        # coverage_log_30d -> coverage_30d (Fenster-Variante, Default: 30 Tage)
        # coverage_log     -> coverage_total (Legacy / Gesamt-Historie)
        try:
            crows = mconn.execute(
                "SELECT id, ts, coverage FROM coverage_log_30d ORDER BY id DESC LIMIT ?",
                (int(max_rows_each),),
            ).fetchall()
            pts = []
            for r in crows:
                pts.append(
                    {
                        "ts": _safe_int(r["ts"]),
                        "series": "coverage_30d",
                        "value": _safe_float(r["coverage"]),
                        "src_table": "coverage_log_30d",
                        "src_id": _safe_int(r["id"]),
                        "src_uid": str(_safe_int(r["id"])),
                    }
                )
            inserted += _stats_insert_points(sconn, pts)
        except Exception as e:
            # Best-effort: ältere DB ohne coverage_log_30d
            log_suppressed('ui/learning.py:460', exc=e, level=logging.WARNING)
            pass

        try:
            crows = mconn.execute(
                "SELECT id, ts, coverage FROM coverage_log ORDER BY id DESC LIMIT ?",
                (int(max_rows_each),),
            ).fetchall()
            pts = []
            for r in crows:
                pts.append(
                    {
                        "ts": _safe_int(r["ts"]),
                        "series": "coverage_total",
                        "value": _safe_float(r["coverage"]),
                        "src_table": "coverage_log",
                        "src_id": _safe_int(r["id"]),
                        "src_uid": str(_safe_int(r["id"])),
                    }
                )
            inserted += _stats_insert_points(sconn, pts)
        except Exception as e:
            log_suppressed('ui/learning.py:461', exc=e, level=logging.WARNING)
            pass

        # empathy_snaps -> empathy_score
        try:
            erows = mconn.execute(
                "SELECT id, ts, score FROM empathy_snaps ORDER BY id DESC LIMIT ?",
                (int(max_rows_each),),
            ).fetchall()
            pts = []
            for r in erows:
                pts.append(
                    {
                        "ts": _safe_int(r["ts"]),
                        "series": "empathy_score",
                        "value": _safe_float(r["score"]),
                        "src_table": "empathy_snaps",
                        "src_id": _safe_int(r["id"]),
                        "src_uid": str(_safe_int(r["id"])),
                    }
                )
            inserted += _stats_insert_points(sconn, pts)
        except Exception as e:
            log_suppressed('ui/learning.py:483', exc=e, level=logging.WARNING)
            pass

        # metrics -> metric:<key> (metrics hat kein id)
        if metrics_keys:
            try:
                placeholders = ",".join(["?"] * len(metrics_keys))
                q = f"""
                    SELECT ts, key, value
                      FROM metrics
                     WHERE key IN ({placeholders})
                  ORDER BY ts DESC
                     LIMIT ?
                """
                mrows = mconn.execute(q, (*metrics_keys, int(max_rows_each))).fetchall()
                pts = []
                for r in mrows:
                    ts = _safe_int(r["ts"])
                    k = str(r["key"])
                    v = _safe_float(r["value"])
                    uid = _sha1(f"{ts}|{k}|{v}")
                    pts.append(
                        {
                            "ts": ts,
                            "series": f"metric:{k}",
                            "value": v,
                            "src_table": "metrics",
                            "src_id": 0,
                            "src_uid": uid,
                        }
                    )
                inserted += _stats_insert_points(sconn, pts)
            except Exception as e:
                log_suppressed('ui/learning.py:516', exc=e, level=logging.WARNING)
                pass

        sconn.commit()
        

        out["ok"] = True
        out["inserted"] = inserted
        return out

    finally:
        try:
            mconn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:527', exc=e, level=logging.WARNING)
            pass
        try:
            sconn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:532', exc=e, level=logging.WARNING)
            pass
# =============================================================================
# END BLOCK: ON_DEMAND_SAMPLER
# =============================================================================


def _fetch_points_from_stats(window_sec: int, metrics_keys: Optional[List[str]] = None) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Schnelles Lesen aus stats.db – aber "balanced", damit Rewards nicht alles verdrängen.

    Problem (real): Wenn Rewards sehr häufig sind, dann liefert
      ORDER BY ts DESC LIMIT N
    oft **nur** reward:* und Metrics/Empathy/Coverage verschwinden im Chart.

    Lösung:
    - Wir wählen zuerst die "relevanten Serien" (reward:*, metric:* + empathy_score + coverage)
      im Zeitfenster aus und begrenzen die Anzahl der Serien (damit es schnell bleibt).
    - Dann holen wir pro Serie die letzten K Punkte (per Window-Function row_number()).
    - Am Ende sortieren wir ASC (chart-friendly).

    Hinweis:
    - Window Functions brauchen SQLite >= 3.25 (auf RasPi OS i.d.R. ok).
    - Falls nicht verfügbar → Fallback auf alten globalen LIMIT-Query.
    """
    since = _now() - max(60, int(window_sec))

    # Welche Serien sollen rein? (Patterns + feste Serien)
    allow_prefixes = ("reward:", "metric:")
    allow_exact = {"empathy_score", "coverage_30d", "coverage_total"}

    # Optional: wenn metrics_keys gesetzt, priorisieren wir diese (falls vorhanden)
    wanted_metric_series = set()
    if metrics_keys:
        for k in metrics_keys:
            k = (k or "").strip()
            if k:
                wanted_metric_series.add(f"metric:{k}")

    try:
        conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.0)

        # 1) Kandidaten-Serien (neueste zuerst), begrenzt
        series_rows = conn.execute(
            """
            SELECT series, MAX(ts) AS last_ts
              FROM stats_points
             WHERE ts >= ?
          GROUP BY series
          ORDER BY last_ts DESC
             LIMIT 80
            """,
            (since,),
        ).fetchall()

        series: List[str] = []
        for r in series_rows:
            s = str(r["series"] or "")
            if not s:
                continue
            if s in allow_exact or s.startswith(allow_prefixes):
                series.append(s)

        # metrics_keys: gewünschte Serien nachziehen (auch wenn im window nicht Top-80)
        for s in sorted(wanted_metric_series):
            if s not in series:
                series.append(s)

        # harte Kappe, damit Query klein bleibt
        series = series[:40]

        if not series:
            conn.close()
            return True, []

        per_series = max(30, min(400, int(LEARNING_LIMIT // max(1, len(series)))))

        # 2) Balanced Fetch per series
        placeholders = ",".join(["?"] * len(series))
        try:
            rows = conn.execute(
                f"""
                SELECT ts, series, value
                  FROM (
                    SELECT ts, series, value,
                           ROW_NUMBER() OVER (PARTITION BY series ORDER BY ts DESC) AS rn
                      FROM stats_points
                     WHERE ts >= ?
                       AND series IN ({placeholders})
                  )
                 WHERE rn <= ?
              ORDER BY ts ASC
                """,
                (since, *series, per_series),
            ).fetchall()
        except Exception:
            # Fallback, falls Window-Function nicht geht
            rows = conn.execute(
                f"""
                SELECT ts, series, value
                  FROM stats_points
                 WHERE ts >= ?
                   AND series IN ({placeholders})
              ORDER BY ts DESC
                 LIMIT ?
                """,
                (since, *series, int(LEARNING_LIMIT)),
            ).fetchall()

        conn.close()

        pts = [
            {"ts": _safe_int(r["ts"]), "series": str(r["series"]), "value": _safe_float(r["value"])}
            for r in rows
        ]
        pts.sort(key=lambda p: p["ts"])
        return True, pts
    except Exception:
        return False, []

# =============================================================================
# BLOCK: CURVE (SWAPPABLE)
# =============================================================================
def _curve_from_stats(days: int = 120) -> Dict[str, Any]:
    """
    Wenn stats_curve_day existiert (Collector), nutze sie.
    """
    days = max(7, int(days))
    try:
        conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.0)
    except Exception:
        return {"ok": False, "daily": [], "weekly": [], "monthly": []}

    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stats_curve_day' LIMIT 1"
        ).fetchone()
        if not row:
            return {"ok": False, "daily": [], "weekly": [], "monthly": []}

        since_day = time.strftime("%Y-%m-%d", time.gmtime(_now() - days * 86400))
        rows = conn.execute(
            """
            SELECT day, chains, sum_quality, q_max
              FROM stats_curve_day
             WHERE day >= ?
          ORDER BY day ASC
            """,
            (since_day,),
        ).fetchall()

        if not rows:
            return {"ok": False, "daily": [], "weekly": [], "monthly": []}

        daily: List[Dict[str, Any]] = []
        for r in rows:
            chains = _safe_int(r["chains"])
            sum_q = _safe_float(r["sum_quality"])
            q_avg = (sum_q / chains) if chains > 0 else 0.0
            daily.append(
                {
                    "period": str(r["day"]),
                    "chains": chains,
                    "q_avg": float(q_avg),
                    "q_max": _safe_float(r["q_max"]),
                }
            )

        # Weekly/Monthly aggregations
        # NOTE: Wir wollen q_avg *und* q_max für Woche/Monat. Da stats_curve_day bereits
        #       chains + sum_quality enthält, berechnen wir q_avg gewichtet über chains.
        weekly: Dict[str, Dict[str, Any]] = {}
        monthly: Dict[str, Dict[str, Any]] = {}

        def week_key(day: str) -> str:
            try:
                y, m, d = [int(x) for x in day.split("-")]
                t = time.gmtime(int(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))))
                return time.strftime("%G-W%V", t)
            except Exception:
                return day[:7] + "-W??"

        def month_key(day: str) -> str:
            return day[:7]

        def upd(bucket: Dict[str, Dict[str, Any]], k: str, row2: Dict[str, Any]) -> None:
            # row2 enthält period, chains, q_avg, q_max
            chains2 = _safe_int(row2.get("chains", 0))
            sum_q2 = float(_safe_float(row2.get("q_avg", 0.0)) * chains2)
            b = bucket.get(k)
            if not b:
                bucket[k] = {"period": k, "chains": chains2, "sum_q": sum_q2, "q_max": _safe_float(row2.get("q_max", 0.0))}
                return
            b["chains"] = _safe_int(b.get("chains", 0)) + chains2
            b["sum_q"] = float(_safe_float(b.get("sum_q", 0.0))) + sum_q2
            b["q_max"] = max(_safe_float(b.get("q_max", 0.0)), _safe_float(row2.get("q_max", 0.0)))

        for r in daily:
            upd(weekly, week_key(r["period"]), r)
            upd(monthly, month_key(r["period"]), r)

        weekly_out: List[Dict[str, Any]] = []
        for k in sorted(weekly.keys()):
            b = weekly[k]
            ch = _safe_int(b.get("chains", 0))
            q_avg = (float(_safe_float(b.get("sum_q", 0.0))) / ch) if ch > 0 else 0.0
            weekly_out.append({"period": k, "chains": ch, "q_avg": float(q_avg), "q_max": _safe_float(b.get("q_max", 0.0))})

        monthly_out: List[Dict[str, Any]] = []
        for k in sorted(monthly.keys()):
            b = monthly[k]
            ch = _safe_int(b.get("chains", 0))
            q_avg = (float(_safe_float(b.get("sum_q", 0.0))) / ch) if ch > 0 else 0.0
            monthly_out.append({"period": k, "chains": ch, "q_avg": float(q_avg), "q_max": _safe_float(b.get("q_max", 0.0))})

        return {"ok": True, "daily": daily, "weekly": weekly_out, "monthly": monthly_out}
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:736', exc=e, level=logging.WARNING)
            pass


def _curve_from_main(days: int = 120) -> Dict[str, Any]:
    """
    Fallback Kurve aus oroma.db via snapchains daily agg.
    """
    days = max(7, int(days))
    try:
        conn = _get_main_conn_readonly()
    except Exception:
        return {"ok": False, "daily": [], "weekly": [], "monthly": []}

    try:
        latest = 0
        try:
            row = conn.execute("SELECT ts FROM snapchains ORDER BY ts DESC LIMIT 1").fetchone()
            latest = _safe_int(row["ts"]) if row else 0
        except Exception:
            latest = 0

        if not latest:
            return {"ok": True, "daily": [], "weekly": [], "monthly": []}

        since = max(0, latest - days * 86400)

        daily_rows = conn.execute(
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
        ).fetchall()

        daily: List[Dict[str, Any]] = []
        for r in daily_rows:
            daily.append(
                {
                    "period": str(r["day"]),
                    "chains": _safe_int(r["chains"]),
                    "q_avg": _safe_float(r["q_avg"]),
                    "q_max": _safe_float(r["q_max"]),
                }
            )

        # Weekly/Monthly aggregations (q_avg + q_max)
        weekly: Dict[str, Dict[str, Any]] = {}
        monthly: Dict[str, Dict[str, Any]] = {}

        def week_key(day: str) -> str:
            try:
                y, m, d = [int(x) for x in day.split("-")]
                t = time.gmtime(int(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))))
                return time.strftime("%G-W%V", t)
            except Exception:
                return day[:7] + "-W??"

        def month_key(day: str) -> str:
            return day[:7]

        def upd(bucket: Dict[str, Dict[str, Any]], k: str, row2: Dict[str, Any]) -> None:
            chains2 = _safe_int(row2.get("chains", 0))
            sum_q2 = float(_safe_float(row2.get("q_avg", 0.0)) * chains2)
            b = bucket.get(k)
            if not b:
                bucket[k] = {
                    "period": k,
                    "chains": chains2,
                    "sum_q": sum_q2,
                    "q_max": _safe_float(row2.get("q_max", 0.0)),
                }
                return
            b["chains"] = _safe_int(b.get("chains", 0)) + chains2
            b["sum_q"] = float(_safe_float(b.get("sum_q", 0.0))) + sum_q2
            b["q_max"] = max(_safe_float(b.get("q_max", 0.0)), _safe_float(row2.get("q_max", 0.0)))

        for r in daily:
            upd(weekly, week_key(r["period"]), r)
            upd(monthly, month_key(r["period"]), r)

        weekly_out: List[Dict[str, Any]] = []
        for k in sorted(weekly.keys()):
            b = weekly[k]
            ch = _safe_int(b.get("chains", 0))
            q_avg = (float(_safe_float(b.get("sum_q", 0.0))) / ch) if ch > 0 else 0.0
            weekly_out.append({"period": k, "chains": ch, "q_avg": float(q_avg), "q_max": _safe_float(b.get("q_max", 0.0))})

        monthly_out: List[Dict[str, Any]] = []
        for k in sorted(monthly.keys()):
            b = monthly[k]
            ch = _safe_int(b.get("chains", 0))
            q_avg = (float(_safe_float(b.get("sum_q", 0.0))) / ch) if ch > 0 else 0.0
            monthly_out.append({"period": k, "chains": ch, "q_avg": float(q_avg), "q_max": _safe_float(b.get("q_max", 0.0))})

        return {"ok": True, "daily": daily, "weekly": weekly_out, "monthly": monthly_out}
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:825', exc=e, level=logging.WARNING)
            pass
# =============================================================================
# END BLOCK: CURVE
# =============================================================================


def _make_testgraph_payload() -> Dict[str, Any]:
    now = _now()
    pts: List[Dict[str, Any]] = []

    for i in range(120):
        ts = now - (120 - i) * 120
        pts.append({"ts": ts, "series": "reward:curriculum", "value": 0.2 + 0.05 * random.random() + 0.05 * (i / 120)})
        pts.append({"ts": ts, "series": "reward:speech", "value": 0.02 + 0.01 * random.random()})
        pts.append({"ts": ts, "series": "metric:reward_empathy", "value": 0.15 + 0.06 * random.random()})
        pts.append({"ts": ts, "series": "metric:cam:token:candidate", "value": 50 + 10 * random.random()})
        pts.append({"ts": ts, "series": "empathy_score", "value": 0.45 + 0.15 * random.random()})
        pts.append({"ts": ts, "series": "coverage_30d", "value": 0.30 + 0.20 * random.random()})

    daily = []
    for d in range(30):
        day_ts = now - (29 - d) * 86400
        day = time.strftime("%Y-%m-%d", time.gmtime(day_ts))
        chains = int(50 * random.random())
        q_avg = 0.15 + 0.10 * (d / 30) + 0.03 * random.random()
        q_max = min(1.0, q_avg + 0.08 * random.random())
        daily.append({"period": day, "chains": chains, "q_avg": q_avg, "q_max": q_max})

    wmap: Dict[str, Dict[str, Any]] = {}
    mmap: Dict[str, Dict[str, Any]] = {}

    for r in daily:
        try:
            y, m, d = [int(x) for x in r["period"].split("-")]
            wk = time.strftime("%G-W%V", time.gmtime(int(time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)))))
        except Exception:
            wk = r["period"][:7] + "-W??"
        mo = r["period"][:7]

        b = wmap.get(wk) or {"period": wk, "chains": 0, "q_max": 0.0}
        b["chains"] += r["chains"]
        b["q_max"] = max(b["q_max"], r["q_max"])
        wmap[wk] = b

        b2 = mmap.get(mo) or {"period": mo, "chains": 0, "q_max": 0.0}
        b2["chains"] += r["chains"]
        b2["q_max"] = max(b2["q_max"], r["q_max"])
        mmap[mo] = b2

    return {
        "points": pts,
        "curve": {
            "ok": True,
            "daily": daily,
            "weekly": [wmap[k] for k in sorted(wmap.keys())],
            "monthly": [mmap[k] for k in sorted(mmap.keys())],
        },
    }


# =============================================================================
# Routes
# =============================================================================
@learning_bp.get("/")
def page() -> str:
    # Startet den periodischen Background-Worker (Maxima + Alles-Export) so
    # frueh wie moeglich, sobald die Learning-UI einmal aufgerufen wurde.
    #
    # Motivation:
    # - "CSV (Alles-Export)" ist fuer grosse stats_points Tabellen teuer.
    # - Safari kann lange Requests abbrechen (AbortError).
    # - Der UI-Download soll daher bevorzugt eine bereits erzeugte Datei
    #   (auch stale) ausliefern, waehrend die Neuberechnung im Hintergrund
    #   laeuft.
    #
    # Der Worker ist idempotent (wird nur 1x pro Prozess gestartet).
    try:
        _start_learning_bg_worker(reason='page_load')
    except Exception as e:
        log_suppressed('ui/learning.py:page_start_bg_fail', exc=e, level=logging.WARNING)
    return render_template("learning.html")


# =============================================================================
# Binding Stage A (Vision) – latest stats_points snapshot (measure-only)
# =============================================================================
def _fetch_latest_binding_vision_points(limit_series: int = 64) -> Dict[str, float]:
    """Fetch latest values for binding.v.* from stats.db.stats_points (bounded).

    Design goals:
    - Never block /learning/api/intelligence: use RO-fast connection.
    - Accept sqlite3.Row OR tuple/list.
    - Return latest value per series (ORDER BY ts DESC LIMIT N; first hit wins).
    """
    out: Dict[str, float] = {}
    try:
        with _get_stats_conn_ro_fast() as conn:
            rows = conn.execute(
                """
                SELECT series, value
                FROM stats_points
                WHERE series LIKE 'binding.v.%'
                ORDER BY ts DESC
                LIMIT ?
                """,
                (int(limit_series),),
            ).fetchall()

        for r in rows:
            try:
                if hasattr(r, "keys") and ("series" in r.keys()):
                    s = str(r["series"])
                    v = float(r["value"])
                else:
                    s = str(r[0])
                    v = float(r[1])
                if s in out:
                    continue
                out[s] = v
            except Exception:
                continue
    except Exception as e:
        # fail-open: do not break the whole intelligence payload
        try:
            log_suppressed(logger="binding_v.latest", key="binding_v.latest", msg="fetch failed", exc=e)
        except Exception:
            pass
    return out


def _fetch_latest_synapses_points(limit_series: int = 32) -> Dict[str, float]:
    """Fetch latest synapses.* KPI points from stats_points (stats.db)."""
    out: Dict[str, float] = {}
    try:
        with _get_stats_conn_ro_fast() as conn:
            rows = conn.execute(
                """
                SELECT series, value
                FROM stats_points
                WHERE series LIKE 'synapses.%'
                ORDER BY ts DESC
                LIMIT ?
                """,
                (int(limit_series),),
            ).fetchall()

        for r in rows:
            try:
                if hasattr(r, "keys") and ("series" in r.keys()):
                    s = str(r["series"])
                    v = float(r["value"])
                else:
                    s = str(r[0])
                    v = float(r[1])
                if s in out:
                    continue
                out[s] = v
            except Exception:
                continue
    except Exception as e:
        try:
            log_suppressed(logger="synapses.latest", key="synapses.latest", msg="fetch failed", exc=e)
        except Exception:
            pass
    return out

def api_data():
    return jsonify(
        {
            "ok": True,
            "ts": _now(),
            "stats_db_path": OROMA_STATS_DB_PATH,
            "main_db_path": OROMA_DB_PATH,
            "metrics_keys_default": _parse_metrics_keys(None),
            "learning_limit": LEARNING_LIMIT,
            "energy_top_limit": ENERGY_TOP_LIMIT,
            "energy_cache_max_age_sec": ENERGY_CACHE_MAX_AGE_SEC,
            "binding_vision_latest": _fetch_latest_binding_vision_points(),
            "synapses_latest": _fetch_latest_synapses_points(),
        }
    )




# =============================================================================
# BLOCK: INTELLIGENCE_INDEX_API (SWAPPABLE)
# =============================================================================
def _compute_intelligence_index(
    *,
    window_rewards_sec: int = 3600,
    window_policy_update_sec: int = 600,
    window_compression_days: int = 30,
    window_latency_sec: int = 300,
    policy_state_sample: int = 800,
) -> Dict[str, Any]:
    """
    ORÓMA Intelligence Index – kompakte Kennzahlen für Learning-UI (read-only).

    Minimal-invasives Design:
    - Kein Schema-Change
    - Keine neuen Tabellen
    - Nur read-only Queries gegen oroma.db (mode=ro)
    - Robust: jede Metrik ist separat try/except → UI bricht nie komplett

    Werte:
    - reward: rewards_per_min, rewards_per_100_episodes (falls episode_id vorhanden)
    - compression: active vs compressed (SnapChains.status)
    - policy: Drift (last_ts Updates) + Decision-Margin (Top1–Top2) als Entropy-Proxy
    - latency_ms: optional aus metrics (vision:infer_ms:*)
    """
    now = _now()

    out: Dict[str, Any] = {
        "ok": False,
        "ts": now,
        "reward": {"window_sec": int(window_rewards_sec)},
        "compression": {"window_days": int(window_compression_days)},
        "policy": {"window_update_sec": int(window_policy_update_sec), "state_sample": int(policy_state_sample)},
        "latency_ms": {"window_sec": int(window_latency_sec)},
        "meta": {"errors": []},
    }

    try:
        conn = _get_main_conn_readonly()
    except Exception as e:
        out["meta"]["errors"].append(f"oroma.db open failed: {e}")
        return out

    try:
        # ------------------------------------------------------------
        # 1) Reward density
        # ------------------------------------------------------------
        try:
            since = now - max(60, int(window_rewards_sec))
            row = conn.execute(
                """
                WITH w AS (
                  SELECT
                    COUNT(*) AS rewards_n,
                    COUNT(DISTINCT episode_id) AS episodes_n
                  FROM rewards_log
                  WHERE created_at >= ?
                )
                SELECT rewards_n, episodes_n
                FROM w
                """,
                (since,),
            ).fetchone()

            rewards_n = _safe_int(row["rewards_n"]) if row else 0
            episodes_n = _safe_int(row["episodes_n"]) if row else 0

            out["reward"]["rewards_n"] = rewards_n
            out["reward"]["episodes_n"] = episodes_n
            out["reward"]["rewards_per_min"] = round(rewards_n / (max(1, int(window_rewards_sec)) / 60.0), 6)

            if episodes_n > 0:
                out["reward"]["rewards_per_100_episodes"] = round(100.0 * rewards_n / float(episodes_n), 6)
            else:
                out["reward"]["rewards_per_100_episodes"] = None

        except Exception as e:
            out["meta"]["errors"].append(f"reward density failed: {e}")

        # ------------------------------------------------------------
        # 2) Compression / Forgetting ratio
        # ------------------------------------------------------------
        try:
            since = now - max(1, int(window_compression_days)) * 86400
            row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS n_active,
                  SUM(CASE WHEN status='compressed' THEN 1 ELSE 0 END) AS n_compressed,
                  COUNT(*) AS n_total
                FROM snapchains
                WHERE ts >= ?
                  AND (status IS NULL OR status != 'deleted')
                """,
                (since,),
            ).fetchone()

            n_active = _safe_int(row["n_active"]) if row else 0
            n_compressed = _safe_int(row["n_compressed"]) if row else 0
            n_total = _safe_int(row["n_total"]) if row else 0

            out["compression"]["n_active"] = n_active
            out["compression"]["n_compressed"] = n_compressed
            out["compression"]["n_total"] = n_total

            out["compression"]["active_per_compressed"] = (
                round(n_active / float(n_compressed), 6) if n_compressed > 0 else None
            )
            out["compression"]["compressed_share"] = (
                round(n_compressed / float(n_total), 6) if n_total > 0 else None
            )

        except Exception as e:
            out["meta"]["errors"].append(f"compression ratio failed: {e}")

        
        # ------------------------------------------------------------
        # 2b) Learning Pulse – letzte „Synapse“ (link/*)
        # ------------------------------------------------------------
        #
        # Motivation / Semantik:
        # - In ORÓMA entstehen „Synapsen“ als SnapChains mit origin="link/<...>".
        # - In frühen/unausgebauten Setups existieren oft bereits technische Links
        #   (z. B. link/calc_vision), während Label-Links (link/a_label, link/av_label)
        #   noch 0 sein können.
        # - Die UI soll dennoch „leben“ zeigen: daher Fallback-Logik.
        #
        # Ausgabe:
        #   out["pulse"]["last_synapse"]       → bevorzugt Label-Link, sonst irgendein link/*
        #   out["pulse"]["last_synapse_label"] → nur link/a_label|link/av_label (kann None sein)
        #   out["pulse"]["last_synapse_any"]   → irgendein link/* (kann None sein)
        #
        try:
            out["pulse"] = {
                "last_synapse": None,
                "last_synapse_label": None,
                "last_synapse_any": None,
            }

            def _row_to_synapse(r: sqlite3.Row) -> Dict[str, Any]:
                ts = _safe_int(r["ts"])
                return {
                    "id": _safe_int(r["id"]),
                    "ts": ts,
                    "age_sec": max(0, now - ts),
                    "origin": (r["origin"] if r["origin"] is not None else ""),
                    "status": (r["status"] if r["status"] is not None else ""),
                    "quality": _safe_float(r["quality"]),
                }

            # (a) Label-Link bevorzugt (falls vorhanden)
            r_label = conn.execute(
                """
                SELECT id, ts, origin, status, quality
                FROM snapchains
                WHERE origin IN ('link/a_label', 'link/av_label')
                ORDER BY ts DESC
                LIMIT 1
                """
            ).fetchone()

            if r_label is not None:
                out["pulse"]["last_synapse_label"] = _row_to_synapse(r_label)

            # (b) Fallback: beliebiger Link (link/*)
            r_any = conn.execute(
                """
                SELECT id, ts, origin, status, quality
                FROM snapchains
                WHERE origin LIKE 'link/%'
                ORDER BY ts DESC
                LIMIT 1
                """
            ).fetchone()

            if r_any is not None:
                out["pulse"]["last_synapse_any"] = _row_to_synapse(r_any)

            out["pulse"]["last_synapse"] = (
                out["pulse"]["last_synapse_label"]
                if out["pulse"]["last_synapse_label"] is not None
                else out["pulse"]["last_synapse_any"]
            )

        except Exception as e:
            out["meta"]["errors"].append(f"pulse/last synapse failed: {e}")

# ------------------------------------------------------------
        # 3) Policy stability
        # ------------------------------------------------------------
        try:
            since = now - max(30, int(window_policy_update_sec))

            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS n_rules,
                  SUM(CASE WHEN last_ts IS NOT NULL AND last_ts >= ? THEN 1 ELSE 0 END) AS updated_n
                FROM policy_rules
                """,
                (since,),
            ).fetchone()

            n_rules = _safe_int(row["n_rules"]) if row else 0
            updated_n = _safe_int(row["updated_n"]) if row else 0

            out["policy"]["n_rules"] = n_rules
            out["policy"]["updated_n"] = updated_n
            out["policy"]["frac_updated_recent"] = round((updated_n / float(n_rules)) if n_rules > 0 else 0.0, 8)

            # Zusatzinfo fuer UI/Debug/Erklaerbarkeit:
            # - last_ts: letzter Zeitstempel (unix) eines Policy-Rule Updates
            # - last_ts_local: lokale Darstellung fuer die UI (Europe/Berlin via System-Localtime)
            try:
                row_last = conn.execute(
                    "SELECT MAX(last_ts) AS mx FROM policy_rules"
                ).fetchone()
                mx = _safe_int(row_last["mx"]) if row_last and row_last["mx"] is not None else None
                out["policy"]["last_ts"] = mx
                out["policy"]["last_ts_local"] = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mx)) if mx else None
                )
            except Exception:
                out["policy"]["last_ts"] = None
                out["policy"]["last_ts_local"] = None


            # Optional: Mutations-Aktivität (rules-Tabelle) als separater Plasticity-Proxy
            # ------------------------------------------------------------------------
            # Hintergrund:
            # - policy_rules bildet die gelernte Policy für Mini-Programme ab.
            # - core/mutation.py mutiert hingegen (klassische) rules-Weights und loggt optional
            #   in rule_mutations (Audit-Tabelle).
            #
            # Wichtig: Wir mischen diese Signale NICHT in frac_updated_recent hinein, damit
            # der Drift-Wert semantisch sauber bleibt ("Policy-Updates").
            # Stattdessen liefern wir ein zusätzliches, getrenntes Signal, das in der UI
            # als "mut" angezeigt werden kann.
            try:
                # 1) Wenn eine Audit-Tabelle existiert, nutze sie direkt (präziseste Form).
                t = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rule_mutations'"
                ).fetchone()
                if t:
                    rowm = conn.execute(
                        "SELECT COUNT(*) AS n FROM rule_mutations WHERE ts >= ?",
                        (since,),
                    ).fetchone()
                    mut_n = _safe_int(rowm["n"]) if rowm else 0
                    out["policy"]["rule_mutations_n"] = mut_n
                    out["policy"]["rule_mutations_per_min"] = round(
                        mut_n / (max(1, int(window_policy_update_sec)) / 60.0), 6
                    )
                    out["policy"]["rule_mutations_source"] = "rule_mutations"
                else:
                    # 2) Fallback: rules.updated_at (existiert in deiner aktuellen DB-Struktur).
                    #    Das ist kein exaktes Mutations-Log, aber ein robustes, reales Signal
                    #    für Plastizität (Gewichte/Rules wurden verändert).
                    try:
                        # Hinweis: SQLite kann bei hoher Writer-Last kurzzeitig "database is locked" melden.
                        # Für UI-Readouts ist ein sehr kurzer Retry sinnvoller als "n/a".
                        try:
                            rowm = conn.execute(
                                "SELECT COUNT(*) AS n FROM rules WHERE updated_at IS NOT NULL AND updated_at >= ?",
                                (since,),
                            ).fetchone()
                        except sqlite3.OperationalError as e:
                            if "locked" in str(e).lower():
                                time.sleep(0.05)
                                rowm = conn.execute(
                                    "SELECT COUNT(*) AS n FROM rules WHERE updated_at IS NOT NULL AND updated_at >= ?",
                                    (since,),
                                ).fetchone()
                            else:
                                raise
                        mut_n = _safe_int(rowm["n"]) if rowm else 0
                        out["policy"]["rule_mutations_n"] = mut_n
                        out["policy"]["rule_mutations_per_min"] = round(
                            mut_n / (max(1, int(window_policy_update_sec)) / 60.0), 6
                        )
                        out["policy"]["rule_mutations_source"] = "rules.updated_at"
                    except Exception:
                        out["policy"]["rule_mutations_n"] = None
                        out["policy"]["rule_mutations_per_min"] = None
                        out["policy"]["rule_mutations_source"] = None
            except Exception:
                out["policy"]["rule_mutations_n"] = None
                out["policy"]["rule_mutations_per_min"] = None
                out["policy"]["rule_mutations_source"] = None

            # Tages-Check (Kontrollsignal): hat heute bereits eine Mutation/Rule-Aenderung stattgefunden?
            # -------------------------------------------------------------------------------
            # Zweck:
            # - Du wolltest in der UI *taeglich* sehen, ob das System (Rules/Mutation) wirklich gearbeitet hat.
            # - /min im kurzen Fenster (z.B. 600s) ist als Live-Rate sinnvoll, kann aber bei bursty Jobs oft 0 sein.
            # - Dieser Tages-Count ist deshalb die robuste, auditierbare Kontrollzahl.
            #
            # Quelle:
            # - Wenn rule_mutations existiert und als Quelle genutzt wird: COUNT(rule_mutations.ts >= start_of_day_local)
            # - Sonst (dein aktueller Stand): COUNT(rules.updated_at >= start_of_day_local)
            #
            # Implementations-Details (wichtig fuer Stabilitaet):
            # - start_of_day_local wird in Python aus localtime berechnet (ohne SQLite-Query),
            #   damit die UI bei kurzen DB-Write-Spitzen nicht in "n/a" kippt.
            # - Der Count-Query selbst hat ein kurzes, begrenztes Retry, falls SQLite kurz "locked" meldet.
            try:
                lt = time.localtime(now)
                start_day = int(now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec))

                src = out["policy"].get("rule_mutations_source")
                # Fallback: auch wenn das Mutations-Signal im Fenster nicht ermittelbar war,
                # soll der Tages-Check trotzdem laufen (dein System mutiert aktuell ueber rules.updated_at).
                if src not in ("rule_mutations", "rules.updated_at"):
                    src = "rules.updated_at"
                if src not in ("rule_mutations", "rules.updated_at"):
                    src = "rules.updated_at"
                today_n = None
                today_src = None
                today_state = "n/a"

                def _count_with_retry(sql: str, param: int):
                    delays = [0.0, 0.05, 0.10]
                    last_err = None
                    for d in delays:
                        if d:
                            time.sleep(d)
                        try:
                            row = conn.execute(sql, (param,)).fetchone()
                            return row, None
                        except sqlite3.OperationalError as e:
                            last_err = e
                            if "locked" in str(e).lower():
                                continue
                            raise
                    return None, last_err

                if src == "rule_mutations":
                    rowd, err = _count_with_retry(
                        "SELECT COUNT(*) AS n FROM rule_mutations WHERE ts >= ?",
                        start_day,
                    )
                    if err is not None:
                        today_state = "locked"
                    else:
                        today_n = _safe_int(rowd["n"]) if rowd else 0
                        today_src = "rule_mutations"
                        today_state = "ok"

                elif src == "rules.updated_at":
                    rowd, err = _count_with_retry(
                        "SELECT COUNT(*) AS n FROM rules WHERE updated_at IS NOT NULL AND updated_at >= ?",
                        start_day,
                    )
                    if err is not None:
                        today_state = "locked"
                    else:
                        today_n = _safe_int(rowd["n"]) if rowd else 0
                        today_src = "rules.updated_at"
                        today_state = "ok"

                out["policy"]["mut_today_n"] = today_n
                out["policy"]["mut_today_source"] = today_src
                out["policy"]["mut_today_yes"] = bool(today_n) if today_n is not None else None
                out["policy"]["mut_today_state"] = today_state
            except Exception:
                out["policy"]["mut_today_n"] = None
                out["policy"]["mut_today_source"] = None
                out["policy"]["mut_today_yes"] = None
                out["policy"]["mut_today_state"] = "n/a"


            # Policy-Data-Aktivitaet (Erklaer-Signal): Game-Episoden
            # --------------------------------------------------------------------
            # Ziel:
            # - In der UI transparent machen, *warum* policy drift (=policy_rules.last_ts Updates)
            #   ggf. dauerhaft 0.00% bleibt.
            # - In deinem Betrieb ist das oft der Fall, wenn keine Spiele (Mini-Programme)
            #   genutzt werden und damit keine neuen Game-Episoden/Outcomes entstehen.
            #
            # Quelle:
            # - episodes.kind == 'game'
            # - episodes.ts_start (Unix)
            #
            # Ausgabe (für UI):
            # - game_eps_24h: Anzahl gestarteter Game-Episoden im letzten 24h-Fenster
            # - last_game_ep_start_local: letzter Game-Episoden-Start (all-time) lokal
            #
            # Zustandsfelder (Robustheit/Explainability):
            # - game_eps_24h_state:  ok | locked | n/a
            # - last_game_ep_state: ok | locked | n/a
            #
            # Wichtig:
            # - Dieses Signal beeinflusst KEINE Drift-Berechnung. Es ist reines "Explainability".
            # - Kein Write, nur Read.
            # - Bei kurzzeitigem "database is locked" wird ein sehr kurzer Retry versucht.
            try:
                since_ep = now - 86400

                def _row_with_retry(sql: str, params: tuple = ()):  # local helper
                    delays = [0.0, 0.05, 0.10]
                    last_err = None
                    for d in delays:
                        if d:
                            time.sleep(d)
                        try:
                            return conn.execute(sql, params).fetchone(), None
                        except sqlite3.OperationalError as e:
                            last_err = e
                            if 'locked' in str(e).lower():
                                continue
                            raise
                    return None, last_err

                # 24h Window
                rowg, err = _row_with_retry(
                    """
                    SELECT
                      COUNT(*) AS n,
                      MAX(ts_start) AS mx
                    FROM episodes
                    WHERE kind='game'
                      AND ts_start >= ?
                    """,
                    (since_ep,),
                )

                if err is not None:
                    out['policy']['game_eps_24h'] = None
                    out['policy']['game_eps_24h_state'] = 'locked'
                else:
                    game_n_24h = _safe_int(rowg['n']) if rowg else 0
                    out['policy']['game_eps_24h'] = game_n_24h
                    out['policy']['game_eps_24h_state'] = 'ok'

                # All-time last game episode start
                rowg2, err2 = _row_with_retry(
                    "SELECT MAX(ts_start) AS mx FROM episodes WHERE kind='game'",
                    (),
                )

                if err2 is not None:
                    out['policy']['last_game_ep_start'] = None
                    out['policy']['last_game_ep_start_local'] = None
                    out['policy']['last_game_ep_state'] = 'locked'
                else:
                    mxg = _safe_int(rowg2['mx']) if rowg2 and rowg2['mx'] is not None else None
                    out['policy']['last_game_ep_start'] = mxg
                    out['policy']['last_game_ep_start_local'] = (
                        time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mxg)) if mxg else None
                    )
                    out['policy']['last_game_ep_state'] = 'ok'

            except Exception:
                out['policy']['game_eps_24h'] = None
                out['policy']['game_eps_24h_state'] = 'n/a'
                out['policy']['last_game_ep_start'] = None
                out['policy']['last_game_ep_start_local'] = None
                out['policy']['last_game_ep_state'] = 'n/a'



            # Entropy-Proxy: Top1–Top2 Margin (auf Sample der zuletzt geänderten state_hashes)
            sample_n = max(100, min(5000, int(policy_state_sample)))

            row2 = conn.execute(
                """
                WITH st AS (
                  SELECT state_hash
                    FROM policy_rules
                GROUP BY state_hash
                ORDER BY MAX(COALESCE(last_ts, 0)) DESC
                   LIMIT ?
                ),
                ranked AS (
                  SELECT state_hash, q,
                         DENSE_RANK() OVER (PARTITION BY state_hash ORDER BY q DESC) AS r
                    FROM policy_rules
                   WHERE state_hash IN (SELECT state_hash FROM st)
                ),
                top2 AS (
                  SELECT
                    state_hash,
                    MAX(CASE WHEN r=1 THEN q END) AS q1,
                    MAX(CASE WHEN r=2 THEN q END) AS q2
                  FROM ranked
                  GROUP BY state_hash
                )
                SELECT
                  COUNT(*) AS n_states,
                  AVG(COALESCE(q1,0) - COALESCE(q2,0)) AS avg_margin,
                  MIN(COALESCE(q1,0) - COALESCE(q2,0)) AS min_margin
                FROM top2
                """,
                (sample_n,),
            ).fetchone()

            out["policy"]["n_states_sampled"] = _safe_int(row2["n_states"]) if row2 else 0
            out["policy"]["avg_margin_top1_top2"] = round(_safe_float(row2["avg_margin"]) if row2 else 0.0, 9)
            out["policy"]["min_margin_top1_top2"] = round(_safe_float(row2["min_margin"]) if row2 else 0.0, 9)

        except Exception as e:
            out["meta"]["errors"].append(f"policy stability failed: {e}")

        # ------------------------------------------------------------
        # 4) Optional latency metrics (if they exist)
        #
        # Ziel:
        #   - "infer ms(5m)" im UI-Header sollte stabil Werte liefern, ohne die UI
        #     zu blockieren. In der Praxis können jedoch sehr seltene Producer-Intervalle
        #     (oder Window zu eng) dazu führen, dass im 5m-Fenster keine Punkte liegen.
        #
        # Umsetzung (minimal-invasiv):
        #   1) Primär oroma.db.metrics (live/roh) im Fenster
        #   2) Fallback stats.db.stats_points (metric:vision:infer_ms:*) im Fenster
        #   3) Final fallback: letzter bekannter Wert (bis 24h) aus stats_points
        try:
            since = now - max(30, int(window_latency_sec))
            keys = ("vision:infer_ms:hailo", "vision:infer_ms:cpu", "vision:infer_ms:degirum")
            placeholders = ",".join(["?"] * len(keys))

            rows = conn.execute(
                f"""
                SELECT key, AVG(value) AS avg_v, COUNT(*) AS n
                  FROM metrics
                 WHERE ts >= ?
                   AND key IN ({placeholders})
              GROUP BY key
                """,
                (since, *keys),
            ).fetchall()

            m: Dict[str, Any] = {}
            for r in rows or []:
                k = str(r["key"])
                m[k] = {"avg": round(_safe_float(r["avg_v"]), 6), "n": _safe_int(r["n"])}

            out["latency_ms"]["by_backend"] = m

            # Fallback 1: stats.db im selben Fenster (falls metrics leer/selten/locked)
            if not m:
                try:
                    sconn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.0)
                    try:
                        series2 = (
                            "metric:vision:infer_ms:hailo",
                            "metric:vision:infer_ms:cpu",
                            "metric:vision:infer_ms:degirum",
                        )
                        placeholders2 = ",".join(["?"] * len(series2))
                        rows2 = sconn.execute(
                            f"""
                            SELECT series, AVG(value) AS avg_v, COUNT(*) AS n
                              FROM stats_points
                             WHERE ts >= ?
                               AND series IN ({placeholders2})
                          GROUP BY series
                            """,
                            (since, *series2),
                        ).fetchall()
                        for r in rows2 or []:
                            s = str(r["series"] or "")
                            k = s.replace("metric:", "")
                            m[k] = {"avg": round(_safe_float(r["avg_v"]), 6), "n": _safe_int(r["n"])}
                    finally:
                        try:
                            sconn.close()
                        except Exception:
                            pass
                except Exception:
                    pass

            # Fallback 2: letzter bekannter Wert (bis 24h) – verhindert "n/a" bei sehr seltenen Punkten
            if not m:
                try:
                    sconn3 = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.0)
                    try:
                        series3 = (
                            "metric:vision:infer_ms:hailo",
                            "metric:vision:infer_ms:cpu",
                            "metric:vision:infer_ms:degirum",
                        )
                        placeholders3 = ",".join(["?"] * len(series3))
                        rows3 = sconn3.execute(
                            f"""
                            SELECT series, ts, value
                              FROM stats_points
                             WHERE ts >= ?
                               AND series IN ({placeholders3})
                          ORDER BY ts DESC
                            """,
                            (now - 86400, *series3),
                        ).fetchall()
                        seen = set()
                        for r in rows3 or []:
                            s = str(r["series"] or "")
                            if s in seen:
                                continue
                            seen.add(s)
                            k = s.replace("metric:", "")
                            v = _safe_float(r["value"])
                            if v == v:
                                m[k] = {"avg": round(v, 6), "n": 1, "ts": _safe_int(r["ts"])}
                    finally:
                        try:
                            sconn3.close()
                        except Exception:
                            pass
                except Exception:
                    pass

        except Exception as e:
            out["meta"]["errors"].append(f"latency metrics failed: {e}")

        # 5) Dream efficiency (offline learning proxy)
        #
        # Motivation:
        #   Investors / paper angle: show that ORÓMA gains value in "sleep" (dream/replay)
        #   without sensors running. A pragmatic proxy is reward efficiency:
        #     avg_reward_dream = mean reward where source='dream/replay'
        #     avg_reward_live  = mean reward for all other sources
        #   plus counts and rewards/min in the same window.
        # ------------------------------------------------------------
        dream = {
            "window_sec": 86400,
            "n_dream": 0,
            "n_live": 0,
            "avg_reward_dream": None,
            "avg_reward_live": None,
            "sum_reward_dream": 0.0,
            "sum_reward_live": 0.0,
            "reward_per_min_dream": None,
            "reward_per_min_live": None,
            "ratio_avg_dream_vs_live": None,
        }
        try:
            w0 = now - 86400

            r = conn.execute(
                "SELECT COUNT(*) AS n, AVG(reward) AS a, SUM(reward) AS s "
                "FROM rewards_log WHERE created_at>=? AND source=?",
                (w0, "dream/replay"),
            ).fetchone()
            dream["n_dream"] = int(r[0] or 0)
            dream["avg_reward_dream"] = (float(r[1]) if (r[1] is not None) else None)
            dream["sum_reward_dream"] = float(r[2] or 0.0)

            r = conn.execute(
                "SELECT COUNT(*) AS n, AVG(reward) AS a, SUM(reward) AS s "
                "FROM rewards_log WHERE created_at>=? AND source<>?",
                (w0, "dream/replay"),
            ).fetchone()
            dream["n_live"] = int(r[0] or 0)
            dream["avg_reward_live"] = (float(r[1]) if (r[1] is not None) else None)
            dream["sum_reward_live"] = float(r[2] or 0.0)

            win_min = 86400.0 / 60.0
            dream["reward_per_min_dream"] = dream["sum_reward_dream"] / win_min
            dream["reward_per_min_live"] = dream["sum_reward_live"] / win_min

            ad = dream["avg_reward_dream"]
            al = dream["avg_reward_live"]
            if (ad is not None) and (al is not None) and (al != 0.0):
                dream["ratio_avg_dream_vs_live"] = float(ad) / float(al)
        except Exception:
            # This is optional telemetry; keep the endpoint resilient.
            pass

        out["dream_efficiency"] = dream


        # ------------------------------------------------------------
        # 5b) Replay-Mutations (UI-Signal fuer DreamWorker Variation)
        #
        # Hintergrund / Motivation:
        # - DreamWorker erzeugt im Replay-Pfad optionale Variationen:
        #     origin='dream/meta'  (Meta-Zentroid / verdichtete Ableitung)
        #     origin='dream/mut'   (Mutation/Variation der Chain)
        # - Diese Ableitungen werden bewusst *non-destruktiv* gespeichert.
        # - Fuer die Learning-UI ist interessant:
        #     (a) ob Replay-Mutation ueberhaupt aktiv ist,
        #     (b) wie viel erzeugt wird (24h-Fenster),
        #     (c) wie die Rate im Verhaeltnis zu den Replay-Schritten ist.
        #
        # Stabilitaets-Regel:
        # - Nur Zaehlen / MAX(ts) -> keine heavy joins.
        # - Keine Schema-Aenderung; reines Telemetrie-Signal.
        # ------------------------------------------------------------
        replay_mut = {
            "window_sec": 86400,
            "batch_runs_24h": 0,
            "batch_no_chains_24h": 0,
            "batch_candidates_24h": 0,
            "last_batch_ts": None,
            "last_batch_ts_local": None,
            "mut_chains_n": 0,
            "meta_chains_n": 0,
            # Metrics-basierte Accounting-Werte (DreamWorker Replay-Pfad)
            #
            # Hintergrund:
            # - In einigen Pfaden (FS/Events, Dedupe-Update, etc.) kann es sein,
            #   dass keine neuen snapchains-ROWS mit origin='dream/*' entstehen,
            #   obwohl der Replay-Pfad tatsächlich aktiv ist und Speicher-Calls
            #   erfolgreich durchlaufen.
            # - Deshalb sind diese Werte *zusaetzlich* zur snapchains-Origin-
            #   Zaehllogik vorhanden und werden in der UI bevorzugt genutzt,
            #   wenn snapchains-origin leer ist.
            "mut_attempt_24h": 0,
            "mut_saved_24h": 0,
            "mut_save_fail_24h": 0,
            "mut_skipped_none_24h": 0,
            "meta_attempt_24h": 0,
            "meta_saved_24h": 0,
            "meta_save_fail_24h": 0,
            "meta_skipped_empty_24h": 0,
            "meta_skipped_disabled_24h": 0,
            "last_saved_ts": None,
            "last_saved_ts_local": None,
            # Effektivwerte fuer UI (snapchains-origin wenn >0, sonst metrics)
            "mut_effective_n": 0,
            "meta_effective_n": 0,
            "last_activity_ts": None,
            "last_activity_ts_local": None,
            "replay_steps_n": int(dream.get("n_dream") or 0),
            "mut_per_replay_step": None,
            "meta_per_replay_step": None,
            "last_mut_ts": None,
            "last_mut_ts_local": None,
            "last_meta_ts": None,
            "last_meta_ts_local": None,
        }
        try:
            w0 = now - 86400

            r = conn.execute(
                "SELECT COUNT(*) AS n, MAX(ts) AS mx FROM snapchains WHERE ts>=? AND origin=?",
                (w0, "dream/mut"),
            ).fetchone()
            mut_n = _safe_int(r[0]) if r else 0
            mut_mx = _safe_int(r[1]) if (r and r[1] is not None) else None
            replay_mut["mut_chains_n"] = mut_n
            replay_mut["last_mut_ts"] = mut_mx
            replay_mut["last_mut_ts_local"] = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mut_mx)) if mut_mx else None
            )

            r = conn.execute(
                "SELECT COUNT(*) AS n, MAX(ts) AS mx FROM snapchains WHERE ts>=? AND origin=?",
                (w0, "dream/meta"),
            ).fetchone()
            meta_n = _safe_int(r[0]) if r else 0
            meta_mx = _safe_int(r[1]) if (r and r[1] is not None) else None
            replay_mut["meta_chains_n"] = meta_n
            replay_mut["last_meta_ts"] = meta_mx
            replay_mut["last_meta_ts_local"] = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta_mx)) if meta_mx else None
            )

            # Replay-Batch Telemetrie (Metrics) – optionaler Fallback, wenn snapchains-origin leer bleibt
            try:
                def _metric_sum_max(key: str) -> tuple[float, int | None]:
                    rr = conn.execute(
                        "SELECT SUM(value) AS s, MAX(ts) AS mx FROM metrics WHERE ts>=? AND key=?",
                        (w0, key),
                    ).fetchone()
                    ss = float((rr[0] if rr else 0.0) or 0.0)
                    mm = _safe_int(rr[1]) if (rr and rr[1] is not None) else None
                    return ss, mm

                r = conn.execute(
                    "SELECT SUM(value) AS s, MAX(ts) AS mx FROM metrics WHERE ts>=? AND key=?",
                    (w0, "dream:replay_batch:run"),
                ).fetchone()
                s = float((r[0] if r else 0.0) or 0.0)
                mx = _safe_int(r[1]) if (r and r[1] is not None) else None
                replay_mut["batch_runs_24h"] = int(round(s))
                replay_mut["last_batch_ts"] = mx
                replay_mut["last_batch_ts_local"] = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mx)) if mx else None
                )

                r = conn.execute(
                    "SELECT SUM(value) AS s FROM metrics WHERE ts>=? AND key=?",
                    (w0, "dream:replay_batch:no_chains"),
                ).fetchone()
                replay_mut["batch_no_chains_24h"] = int(round(float((r[0] if r else 0.0) or 0.0)))

                r = conn.execute(
                    "SELECT SUM(value) AS s FROM metrics WHERE ts>=? AND key=?",
                    (w0, "dream:replay_batch:candidates"),
                ).fetchone()
                replay_mut["batch_candidates_24h"] = int(round(float((r[0] if r else 0.0) or 0.0)))

                # Replay-Save Accounting (pro Origin) – authoritative, wenn snapchains-origin leer bleibt
                # mut
                s, mx2 = _metric_sum_max("dream:replay_attempt:dream/mut")
                replay_mut["mut_attempt_24h"] = int(round(s))
                s, mx3 = _metric_sum_max("dream:replay_saved:dream/mut")
                replay_mut["mut_saved_24h"] = int(round(s))
                s, mx4 = _metric_sum_max("dream:replay_save_fail:dream/mut")
                replay_mut["mut_save_fail_24h"] = int(round(s))
                s, _ = _metric_sum_max("dream:replay_skipped:mut_none")
                replay_mut["mut_skipped_none_24h"] = int(round(s))

                # meta
                s, mx5 = _metric_sum_max("dream:replay_attempt:dream/meta")
                replay_mut["meta_attempt_24h"] = int(round(s))
                s, mx6 = _metric_sum_max("dream:replay_saved:dream/meta")
                replay_mut["meta_saved_24h"] = int(round(s))
                s, mx7 = _metric_sum_max("dream:replay_save_fail:dream/meta")
                replay_mut["meta_save_fail_24h"] = int(round(s))
                s, _ = _metric_sum_max("dream:replay_skipped:meta_empty")
                replay_mut["meta_skipped_empty_24h"] = int(round(s))
                s, _ = _metric_sum_max("dream:replay_skipped:meta_disabled")
                replay_mut["meta_skipped_disabled_24h"] = int(round(s))

                # last_saved_ts: max over attempt/saved/fail timestamps (mut/meta)
                last_saved = None
                for _mx in (mx2, mx3, mx4, mx5, mx6, mx7):
                    if _mx and (last_saved is None or _mx > last_saved):
                        last_saved = _mx
                replay_mut["last_saved_ts"] = last_saved
                replay_mut["last_saved_ts_local"] = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_saved)) if last_saved else None
                )
            except Exception:
                pass

            # Effektivwerte fuer UI: wenn snapchains-origin leer bleibt, nutze metrics saved
            eff_mut = mut_n if mut_n > 0 else int(replay_mut.get("mut_saved_24h") or 0)
            eff_meta = meta_n if meta_n > 0 else int(replay_mut.get("meta_saved_24h") or 0)
            replay_mut["mut_effective_n"] = int(eff_mut)
            replay_mut["meta_effective_n"] = int(eff_meta)

            # Letzte Aktivitaet (max aus snapchains ts + metrics saved/attempt + batch)
            last_act = None
            for _mx in (
                replay_mut.get("last_mut_ts"),
                replay_mut.get("last_meta_ts"),
                replay_mut.get("last_saved_ts"),
                replay_mut.get("last_batch_ts"),
            ):
                if _mx and (last_act is None or int(_mx) > int(last_act)):
                    last_act = int(_mx)
            replay_mut["last_activity_ts"] = last_act
            replay_mut["last_activity_ts_local"] = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_act)) if last_act else None
            )

            steps = int(replay_mut.get("replay_steps_n") or 0)
            if steps > 0:
                replay_mut["mut_per_replay_step"] = round(float(replay_mut.get("mut_effective_n") or 0) / float(steps), 6)
                replay_mut["meta_per_replay_step"] = round(float(replay_mut.get("meta_effective_n") or 0) / float(steps), 6)
        except Exception:
            # Optional telemetry; keep the endpoint resilient.
            pass

        out["replay_mutations"] = replay_mut

        # ------------------------------------------------------------
        # 6) "Abstract thinking" proxies
        #
        # A) Concept-Reuse Index (abstraction / reuse)
        #    - How much of the recent relation activity is about compressed concepts?
        #    - Proxy: share of relations involving nodes labeled "compressed_*" and
        #             share of meta_to_chain/chain_to_origin relations.
        #
        # B) Cross-Modal Binding Rate (world model coupling)
        #    - How much of the recent relation activity are explicit "link/*" bindings
        #      between modalities (vision↔calc, vision↔audio, etc.)?
        # ------------------------------------------------------------
        abstract = {
            "window_sec": 86400,
            "concept_reuse": {
                "rel_total": 0,
                "rel_meta_to_chain": 0,
                "rel_chain_to_origin": 0,
                "share_meta_relations": None,          # (meta_to_chain+chain_to_origin)/rel_total
                "rel_with_compressed": 0,
                "share_relations_with_compressed": None,
                "nodes_total": 0,
                "nodes_compressed": 0,
                "share_nodes_compressed": None,
                "_compressed_from_nodes": False,
            },
            "cross_modal": {
                "rel_total": 0,
                "link_total": 0,
                "share_link_relations": None,          # link_total/rel_total
                "top_links": [],                       # [{relation,c}]
                "top_link": None,
                "link_calc_vision": 0,                 # explicit commonly used link
            },
            # NOTE:
            #   Falls object_relations (ObjectGraph) noch nicht gefüllt ist,
            #   versuchen wir *best effort* einen Fallback aus snapchains.origin.
            #   Das hält die UI lebendig ("n/a" verschwindet), ohne das Schema
            #   zu verändern oder harte Abhängigkeiten an den ObjectGraph zu binden.
            #   
            #   - link/* wird häufig direkt als origin gespeichert (z.B. link/calc_vision)
            #   - compressed_* kann je nach Build ebenfalls als origin auftauchen
            #
            #   Wenn später object_relations existiert, gewinnt automatisch der
            #   präzisere Pfad oben.
            "_fallback_from_snapchains": False,
        }
        try:
            w0 = now - 86400

            # total relations in window
            rel_total = int(
                conn.execute("SELECT COUNT(*) FROM object_relations WHERE ts>=?", (w0,)).fetchone()[0] or 0
            )
            abstract["concept_reuse"]["rel_total"] = rel_total
            abstract["cross_modal"]["rel_total"] = rel_total

            # Node-based compressed share (robust even if relations are sparse/old)
            # ------------------------------------------------------------------
            # object_nodes hat nur created_ts (kein last_ts). Daher: Aktivität/Anteil
            # über neu angelegte Nodes im Zeitfenster.
            try:
                n_nodes = int(conn.execute("SELECT COUNT(*) FROM object_nodes WHERE created_ts>=?", (w0,)).fetchone()[0] or 0)
                n_comp_nodes = int(conn.execute("SELECT COUNT(*) FROM object_nodes WHERE created_ts>=? AND label LIKE 'compressed_%'", (w0,)).fetchone()[0] or 0)
                abstract["concept_reuse"]["nodes_total"] = n_nodes
                abstract["concept_reuse"]["nodes_compressed"] = n_comp_nodes
                if n_nodes > 0:
                    abstract["concept_reuse"]["share_nodes_compressed"] = float(n_comp_nodes) / float(n_nodes)
            except Exception:
                pass

            if rel_total == 0:
                abstract["concept_reuse"]["share_meta_relations"] = 0.0

            # relation-type counts (cheap)
            r = conn.execute(
                "SELECT "
                "SUM(CASE WHEN relation='meta_to_chain' THEN 1 ELSE 0 END) AS n_m2c, "
                "SUM(CASE WHEN relation='chain_to_origin' THEN 1 ELSE 0 END) AS n_cto "
                "FROM object_relations WHERE ts>=?",
                (w0,),
            ).fetchone()
            n_m2c = int(r[0] or 0)
            n_cto = int(r[1] or 0)
            abstract["concept_reuse"]["rel_meta_to_chain"] = n_m2c
            abstract["concept_reuse"]["rel_chain_to_origin"] = n_cto
            if rel_total > 0:
                abstract["concept_reuse"]["share_meta_relations"] = float(n_m2c + n_cto) / float(rel_total)

            # share of relations involving compressed_* nodes (slower but informative)
            r = conn.execute(
                "SELECT "
                "SUM(CASE WHEN (na.label LIKE 'compressed_%' OR nb.label LIKE 'compressed_%') THEN 1 ELSE 0 END) AS n_comp, "
                "COUNT(*) AS n_total "
                "FROM object_relations r "
                "LEFT JOIN object_nodes na ON na.id=r.a_id "
                "LEFT JOIN object_nodes nb ON nb.id=r.b_id "
                "WHERE r.ts>=?",
                (w0,),
            ).fetchone()
            n_comp = int(r[0] or 0)
            n_tot2 = int(r[1] or 0)
            abstract["concept_reuse"]["rel_with_compressed"] = n_comp
            # Relation-basierter Anteil ist informativ, aber in ORÓMA oft extrem dünn besetzt
            # (z.B. nur wenige meta_to_chain/chain_to_origin Kanten), obwohl "compressed_*"
            # als Konzepte bereits stark aktiv sind. Damit die UI-Zahl für "compressed"
            # tatsächlich Bedeutung trägt, priorisieren wir für die Anzeige den Node-basierten
            # Anteil (neu angelegte compressed_* Nodes im Fenster), und behalten den
            # Relation-basierten Wert separat für Debug/Forensik.
            if n_tot2 > 0:
                abstract["concept_reuse"]["share_relations_with_compressed_rel"] = float(n_comp) / float(n_tot2)
            else:
                abstract["concept_reuse"]["share_relations_with_compressed_rel"] = None

            # Anzeige-Wert: bevorzugt Node-basierte "compressed" Aktivität (new nodes),
            # ansonsten Relation-basiert.
            sn = abstract["concept_reuse"].get("share_nodes_compressed")
            sr = abstract["concept_reuse"].get("share_relations_with_compressed_rel")
            if (isinstance(sn, (int, float)) and (sn is not None)):
                abstract["concept_reuse"]["share_relations_with_compressed"] = float(sn)
                abstract["concept_reuse"]["_compressed_from_nodes"] = True
            elif (isinstance(sr, (int, float)) and (sr is not None)):
                abstract["concept_reuse"]["share_relations_with_compressed"] = float(sr)
                abstract["concept_reuse"]["_compressed_from_nodes"] = False
            else:
                abstract["concept_reuse"]["share_relations_with_compressed"] = None
                abstract["concept_reuse"]["_compressed_from_nodes"] = False

            # Cross-modal binding (24h): Multi-Modal Binding Index (MBI)
            # -----------------------------------------------------------
            # Hintergrund:
            #   In ORÓMA entstehen "Bindings" historisch primär als SnapChains (origin 'link/*')
            #   durch Linker (calc_vision_linker, av_label_linker, audio_label_linker).
            #   Der ObjectGraph enthält dagegen oft nur generische Relations (describes/origin/...),
            #   wodurch eine reine Query auf object_relations.relation LIKE 'link/%' in der UI
            #   fälschlich 0.00% ergeben kann, obwohl Bindings real existieren.
            #
            # Ziel:
            #   - robuste Kennzahl, die sowohl SnapChain-Bindings als auch Graph-Kopplung
            #     und (optional) Dream/Replay-Reinforcement berücksichtigt
            #   - keine Schemaänderungen erforderlich
            #
            # Komponenten (0..1):
            #   B_snap:      Anteil link/* SnapChains im Fenster (explizite multimodale Links)
            #   B_graph:     Anteil Cross-Modal Edges im ObjectGraph (aus Node-Labels abgeleitet)
            #   B_reinforce: Anteil Replay-Schritte auf link/* Chains (Konsolidierungs-Signal)
            #
            # MBI = w1*B_snap + w2*B_graph + w3*B_reinforce
            # Default-Gewichte sind bewusst konservativ (ohne übermäßiges Vertrauen auf Replay-Logs).
            try:
                # (A) Legacy/Debug: explizite link/* relations im ObjectGraph
                #     -> in ORÓMA meist 0, weil Relation-Typen "origin"/"describes" sind.
                rows = conn.execute(
                    "SELECT relation, COUNT(*) AS c "
                    "FROM object_relations WHERE ts>=? AND relation LIKE 'link/%' "
                    "GROUP BY relation ORDER BY c DESC LIMIT 10",
                    (w0,),
                ).fetchall()
                top_links_legacy = [{"relation": str(rr[0]), "c": int(rr[1] or 0)} for rr in rows]
                link_total_rel_legacy = sum(int(rr["c"]) for rr in top_links_legacy)
                abstract["cross_modal"]["top_links_legacy"] = top_links_legacy
                abstract["cross_modal"]["link_total_legacy"] = link_total_rel_legacy

                # (B) Robust (node-basiert): eine Relation zählt als "link-rel",
                #     wenn mindestens ein Endpunkt-Node label LIKE 'link/%' ist.
                row = conn.execute(
                    """
                    WITH n AS (SELECT id, label FROM object_nodes),
                         r AS (
                           SELECT a_id, b_id, ts
                           FROM object_relations
                           WHERE ts>=?
                         )
                    SELECT
                      COUNT(*) AS rel_total_join,
                      SUM(CASE WHEN (a.label LIKE 'link/%' OR b.label LIKE 'link/%') THEN 1 ELSE 0 END) AS rel_linknode
                    FROM r
                    LEFT JOIN n a ON a.id=r.a_id
                    LEFT JOIN n b ON b.id=r.b_id
                    """,
                    (w0,),
                ).fetchone()
                link_total_rel = int((row[1] if row is not None else 0) or 0)

                # Top link/* labels by participation in relations.
                rows = conn.execute(
                    """
                    WITH n AS (SELECT id, label FROM object_nodes),
                         r AS (SELECT a_id, b_id, ts FROM object_relations WHERE ts>=?),
                         u AS (
                           SELECT a.label AS label
                           FROM r LEFT JOIN n a ON a.id=r.a_id LEFT JOIN n b ON b.id=r.b_id
                           WHERE a.label LIKE 'link/%'
                           UNION ALL
                           SELECT b.label AS label
                           FROM r LEFT JOIN n a ON a.id=r.a_id LEFT JOIN n b ON b.id=r.b_id
                           WHERE b.label LIKE 'link/%'
                         )
                    SELECT label, COUNT(*) c
                    FROM u
                    GROUP BY label
                    ORDER BY c DESC
                    LIMIT 10
                    """,
                    (w0,),
                ).fetchall()
                top_links = [{"relation": str(rr[0]), "c": int(rr[1] or 0)} for rr in rows]
                abstract["cross_modal"]["top_links"] = top_links
                abstract["cross_modal"]["link_total"] = link_total_rel
                abstract["cross_modal"]["top_link"] = (top_links[0]["relation"] if top_links else None)
                abstract["cross_modal"]["link_calc_vision"] = next(
                    (int(x["c"]) for x in top_links if x["relation"] == "link/calc_vision"),
                    0,
                )
                if rel_total > 0:
                    abstract["cross_modal"]["share_link_relations"] = float(link_total_rel) / float(rel_total)
            except Exception as e:
                log_suppressed("ui/learning.py:cross_modal:link_relations", exc=e, level=logging.WARNING)

            # (B) SnapChain-Bindings: link/* origins (immer berechnen; nicht nur Fallback)
            #
            # WICHTIG (Produktiv-Definition):
            #   Linker erzeugen Bindings oft direkt als status='compressed' (kristallisiert),
            #   damit sie nicht als "Live-Rohdaten" behandelt werden. Für die Binding-Kennzahl
            #   sollen diese Bindings aber selbstverständlich mitgezählt werden.
            #
            #   Deshalb zählen wir hier bewusst NUR status IN ('active','compressed') – das
            #   deckt den "Working Set" + kristallisierte Bindings ab und verhindert, dass
            #   sonstige Status (z.B. disabled/archived/null) den Nenner künstlich aufblasen.
            try:
                total_ch = int(conn.execute(
                    "SELECT COUNT(*) FROM snapchains WHERE ts>=? AND status IN ('active','compressed')",
                    (w0,),
                ).fetchone()[0] or 0)
                link_ch = int(conn.execute(
                    "SELECT COUNT(*) FROM snapchains WHERE ts>=? AND origin LIKE 'link/%' AND status IN ('active','compressed')",
                    (w0,),
                ).fetchone()[0] or 0)
                abstract["cross_modal"]["snap_total"] = total_ch
                abstract["cross_modal"]["snap_link_total"] = link_ch
                abstract["cross_modal"]["share_link_snapchains"] = (float(link_ch) / float(total_ch)) if total_ch > 0 else 0.0
            except Exception as e:
                log_suppressed("ui/learning.py:cross_modal:snapchains", exc=e, level=logging.WARNING)
                abstract["cross_modal"]["snap_total"] = 0
                abstract["cross_modal"]["snap_link_total"] = 0
                abstract["cross_modal"]["share_link_snapchains"] = 0.0
            # (C) Graph-Kopplung: Cross-Modal Edges (aus Node-Label abgeleitet)
            # Hinweis: object_nodes.kind ist in der Praxis oft generisch ('object'); daher nutzen wir label-Prefixe.
            # Mapping ist bewusst konservativ (nur klare Modalitäten).
            try:
                q = (
                    "SELECT "
                    "SUM(CASE WHEN (ma!='other' AND mb!='other' AND ma!=mb) THEN 1 ELSE 0 END) AS n_cross, "
                    "COUNT(*) AS n_total "
                    "FROM ( "
                    "  SELECT r.ts, "
                    "    (CASE "
                    "      WHEN na.label LIKE 'audio/%' THEN 'audio' "
                    "      WHEN na.label LIKE 'vision/%' THEN 'vision' "
                    "      WHEN na.label LIKE 'video/%' THEN 'vision' "
                    "      WHEN na.label LIKE 'cam/%' THEN 'vision' "
                    "      WHEN na.label LIKE 'scenegraph:%' THEN 'vision' "
                    "      WHEN na.label LIKE 'speech/%' THEN 'speech' "
                    "      WHEN na.label LIKE 'text/%' THEN 'text' "
                    "      WHEN na.label LIKE 'calc/%' THEN 'calc' "
                    "      WHEN na.label LIKE 'link/%' THEN 'link' "
                    "      ELSE 'other' "
                    "    END) AS ma, "
                    "    (CASE "
                    "      WHEN nb.label LIKE 'audio/%' THEN 'audio' "
                    "      WHEN nb.label LIKE 'vision/%' THEN 'vision' "
                    "      WHEN nb.label LIKE 'video/%' THEN 'vision' "
                    "      WHEN nb.label LIKE 'cam/%' THEN 'vision' "
                    "      WHEN nb.label LIKE 'scenegraph:%' THEN 'vision' "
                    "      WHEN nb.label LIKE 'speech/%' THEN 'speech' "
                    "      WHEN nb.label LIKE 'text/%' THEN 'text' "
                    "      WHEN nb.label LIKE 'calc/%' THEN 'calc' "
                    "      WHEN nb.label LIKE 'link/%' THEN 'link' "
                    "      ELSE 'other' "
                    "    END) AS mb "
                    "  FROM object_relations r "
                    "  LEFT JOIN object_nodes na ON na.id=r.a_id "
                    "  LEFT JOIN object_nodes nb ON nb.id=r.b_id "
                    "  WHERE r.ts>=? "
                    ") t"
                )
                r2 = conn.execute(q, (w0,)).fetchone()
                n_cross = int(r2[0] or 0)
                n_total = int(r2[1] or 0)
                abstract["cross_modal"]["graph_total"] = n_total
                abstract["cross_modal"]["graph_cross_total"] = n_cross
                abstract["cross_modal"]["share_cross_modal_edges"] = (float(n_cross) / float(n_total)) if n_total > 0 else 0.0
            except Exception as e:
                log_suppressed("ui/learning.py:cross_modal:graph", exc=e, level=logging.WARNING)
                abstract["cross_modal"]["graph_total"] = 0
                abstract["cross_modal"]["graph_cross_total"] = 0
                abstract["cross_modal"]["share_cross_modal_edges"] = 0.0

            # (D) Replay/Dream Reinforcement: Anteil Replay-Schritte auf link/* Chains
            #
            # Hintergrund:
            #   - replay_log ist historisch gewachsen. In manchen Ständen heißt die Zeitspalte "ts_run",
            #     in älteren Patches auch "ts" o.ä.
            #   - Damit das Dashboard stabil bleibt, wird die Zeitspalte zur Laufzeit anhand PRAGMA table_info()
            #     ermittelt und *nur* aus bekannten Kandidaten gewählt.
            #
            # Hinweis: replay_log.chain_id ist TEXT (historisch); wir joinen best-effort via CAST auf snapchains.id.
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(replay_log)").fetchall()]  # (cid,name,type,...)
                cand_cols = ["ts_run", "ts", "run_ts", "created_ts", "created_at", "t", "time_ts"]
                time_col = next((c for c in cand_cols if c in cols), None)

                abstract["cross_modal"]["replay_time_col"] = time_col or ""

                if not time_col:
                    raise RuntimeError("replay_log: no supported time column found (expected one of: %s)" % ",".join(cand_cols))

                q = (
                    "SELECT "
                    "SUM(COALESCE(rl.steps,0)) AS steps_total, "
                    "SUM(CASE WHEN sc.origin LIKE 'link/%' THEN COALESCE(rl.steps,0) ELSE 0 END) AS steps_link "
                    "FROM replay_log rl "
                    "LEFT JOIN snapchains sc ON sc.id = CAST(rl.chain_id AS INTEGER) "
                    f"WHERE rl.{time_col} >= ?"
                )

                r3 = conn.execute(q, (w0,)).fetchone()
                steps_total = int(r3[0] or 0)
                steps_link = int(r3[1] or 0)
                abstract["cross_modal"]["replay_steps_total"] = steps_total
                abstract["cross_modal"]["replay_steps_link"] = steps_link
                abstract["cross_modal"]["share_replay_steps_on_link"] = (float(steps_link) / float(steps_total)) if steps_total > 0 else 0.0
            except Exception as e:
                log_suppressed("ui/learning.py:cross_modal:replay", exc=e, level=logging.WARNING)
                abstract["cross_modal"]["replay_time_col"] = ""
                abstract["cross_modal"]["replay_steps_total"] = 0
                abstract["cross_modal"]["replay_steps_link"] = 0
                abstract["cross_modal"]["share_replay_steps_on_link"] = 0.0

            # (E) MBI Aggregation
            try:
                w1 = float(os.getenv("OROMA_BINDING_W_SNAP", "0.40") or 0.40)
                w2 = float(os.getenv("OROMA_BINDING_W_GRAPH", "0.40") or 0.40)
                w3 = float(os.getenv("OROMA_BINDING_W_REPLAY", "0.20") or 0.20)
                ws = max(1e-9, (w1 + w2 + w3))
                w1, w2, w3 = (w1 / ws), (w2 / ws), (w3 / ws)

                b_snap = float(abstract["cross_modal"].get("share_link_snapchains") or 0.0)
                b_graph = float(abstract["cross_modal"].get("share_cross_modal_edges") or 0.0)
                b_rep = float(abstract["cross_modal"].get("share_replay_steps_on_link") or 0.0)
                mbi = (w1 * b_snap) + (w2 * b_graph) + (w3 * b_rep)

                abstract["cross_modal"]["mbi"] = float(mbi)
                abstract["cross_modal"]["mbi_w"] = {"snap": float(w1), "graph": float(w2), "replay": float(w3)}
            except Exception as e:
                log_suppressed("ui/learning.py:cross_modal:mbi", exc=e, level=logging.WARNING)
                abstract["cross_modal"]["mbi"] = None
                abstract["cross_modal"]["mbi_w"] = None

# ------------------------------------------------------------
            # Fallback: ObjectGraph ist leer (rel_total==0) → SnapChains origin
            # ------------------------------------------------------------
            if rel_total == 0:
                # Total chains in window as denominator (cheap)
                total_ch = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM snapchains WHERE ts>=?",
                        (w0,),
                    ).fetchone()[0]
                    or 0
                )

                # link/* origins (cross-modal binding proxy)
                rows2 = conn.execute(
                    "SELECT origin, COUNT(*) AS c "
                    "FROM snapchains WHERE ts>=? AND origin LIKE 'link/%' "
                    "GROUP BY origin ORDER BY c DESC LIMIT 10",
                    (w0,),
                ).fetchall()
                top_links2 = [{"relation": str(rr[0]), "c": int(rr[1] or 0)} for rr in rows2]
                link_total2 = sum(int(x["c"]) for x in top_links2)

                # compressed_* origins (concept reuse proxy)
                # -------------------------------------------
                # NOTE: Wir nutzen SnapChains hier nur als Fallback, falls sowohl
                # object_relations leer IST als auch aus object_nodes keine Aussage
                # moeglich ist (nodes_total==0). Andernfalls wuerden wir ein
                # besseres Signal ueberschreiben.
                comp = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM snapchains WHERE ts>=? AND origin LIKE 'compressed_%'",
                        (w0,),
                    ).fetchone()[0]
                    or 0
                )

                # Populate abstract dict if we have any denominator.
                abstract["_fallback_from_snapchains"] = True
                # No ObjectGraph relations available in the window; only snapchains are present.
                abstract["cross_modal"]["rel_total"] = 0
                abstract["cross_modal"]["snap_total"] = total_ch
                abstract["cross_modal"]["top_links"] = top_links2
                abstract["cross_modal"]["link_total"] = link_total2
                abstract["cross_modal"]["top_link"] = (top_links2[0]["relation"] if top_links2 else None)
                abstract["cross_modal"]["link_calc_vision"] = next(
                    (int(x["c"]) for x in top_links2 if x["relation"] == "link/calc_vision"),
                    0,
                )
                if total_ch > 0:
                    # Proxy metric: share of link/* snapchains among all snapchains in the window.
                    # NOTE: We intentionally do NOT write this into share_link_relations,
                    # because share_link_relations is reserved for ObjectGraph relation stats.
                    abstract["cross_modal"]["share_link_snapchains_proxy"] = float(link_total2) / float(total_ch)

                # Concept reuse: bevorzuge node-basiertes Signal, wenn vorhanden.
                if abstract["concept_reuse"].get("share_nodes_compressed") is not None:
                    abstract["concept_reuse"]["share_relations_with_compressed"] = abstract["concept_reuse"]["share_nodes_compressed"]
                    abstract["concept_reuse"]["_compressed_from_nodes"] = True
                else:
                    abstract["concept_reuse"]["rel_with_compressed"] = comp
                    if total_ch > 0:
                        abstract["concept_reuse"]["share_relations_with_compressed"] = float(comp) / float(total_ch)
        except Exception:
            # Optional telemetry; ignore if schema missing or queries fail.
            pass

        out["abstract"] = abstract


        # ------------------------------------------------------------
        try:
            sg_since = now - 86400
            # Begrenztes Sample für Headless/Edge: wir parsen max. 200 Graphen.
            rows = conn.execute(
                """
                SELECT ts, graph_json
                  FROM scenegraphs
                 WHERE ts >= ?
              ORDER BY ts DESC
                 LIMIT 200
                """,
                (int(sg_since),),
            ).fetchall()

            n = 0
            nodes_list = []
            edges_list = []

            for r in rows or []:
                try:
                    g = json.loads(r["graph_json"] or "{}")
                    if not isinstance(g, dict):
                        continue

                    # Robust gegen Varianten:
                    # - nodes / edges
                    # - objects / relations
                    nodes = g.get("nodes", g.get("objects", []))
                    edges = g.get("edges", g.get("relations", []))

                    nn = len(nodes) if isinstance(nodes, list) else 0
                    ne = len(edges) if isinstance(edges, list) else 0

                    nodes_list.append(nn)
                    edges_list.append(ne)
                    n += 1
                except Exception:
                    continue

            if n > 0:
                out["scenegraph"] = {
                    "window_sec": 86400,
                    "n": int(n),
                    "avg_nodes": float(sum(nodes_list) / max(1, n)),
                    "avg_edges": float(sum(edges_list) / max(1, n)),
                    "max_nodes": int(max(nodes_list) if nodes_list else 0),
                    "max_edges": int(max(edges_list) if edges_list else 0),
                }
            else:
                out["scenegraph"] = {"window_sec": 86400, "n": 0}
        except Exception as e:
            out["meta"]["errors"].append(f"scenegraph summary failed: {e}")

        out["ok"] = True
        return out

    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed("ui/learning.py:intelligence_index", exc=e, level=logging.WARNING)






def _fmt_local_ts(ts: Any) -> str:
    """Best-effort local timestamp formatter for lightweight JSON APIs.

    Wichtig:
    - darf niemals werfen
    - gibt bei ungueltigen Werten eine leere Zeichenkette zurueck
    - bewusst lokalzeitbasiert, damit Learning-/UI-Debug direkt menschenlesbar ist
    """
    try:
        iv = int(float(ts or 0))
        if iv <= 0:
            return ''
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(iv))
    except Exception:
        return ''
def _parse_cam_token_blob_learning(raw: Any) -> Optional[Dict[str, Any]]:
    try:
        if raw is None:
            return None
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode('utf-8', errors='ignore')
        obj = json.loads(raw or '{}')
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _compute_vision_primitives(window_sec: int = 86400, *, take_limit: int = 1500) -> Dict[str, Any]:
    now = _now()
    ts_from = max(0, now - max(60, int(window_sec)))
    conn = None
    rows = []
    try:
        conn = _get_main_conn_readonly()
        rows = conn.execute(
            """
            SELECT ts, quality, blob
            FROM snapchains
            WHERE namespace='vision' AND origin='vision/token' AND ts >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ts_from, max(50, min(int(take_limit), 5000))),
        ).fetchall()
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    items: List[Dict[str, Any]] = []
    for r in rows:
        obj = _parse_cam_token_blob_learning(r['blob'])
        if not obj:
            continue
        items.append({
            'ts': _safe_int(r['ts']),
            'q': None if r['quality'] is None else _safe_float(r['quality'], None),
            'motion': None if obj.get('motion') is None else _safe_float(obj.get('motion'), None),
            'edges': None if obj.get('edges') is None else _safe_float(obj.get('edges'), None),
            'color': None if obj.get('color') is None else _safe_float(obj.get('color'), None),
        })
    payload: Dict[str, Any] = {
        'ok': True,
        'window_sec': int(window_sec),
        'count': len(items),
        'last': {}, 'avg': {}, 'max': {}, 'min': {},
        'meta': {'server_ts': now, 'server_ts_local': _fmt_local_ts(now)},
    }
    if not items:
        return payload
    last = items[0]
    for k in ('motion','edges','color','q'):
        vals = [float(x[k]) for x in items if x.get(k) is not None]
        payload['last'][k] = last.get(k)
        payload['avg'][k] = round(sum(vals)/len(vals), 6) if vals else None
        payload['max'][k] = max(vals) if vals else None
        payload['min'][k] = min(vals) if vals else None
    payload['last_ts'] = int(last.get('ts') or 0)
    payload['age_sec'] = max(0, now - int(last.get('ts') or 0)) if last.get('ts') else None
    return _json_sanitize(payload)


@learning_bp.get('/api/vision_primitives')
def api_vision_primitives():
    try:
        window_sec = max(300, min(_safe_int(request.args.get('window_sec'), 86400), 30*86400))
        allow_stale = str(request.args.get('allow_stale', '1')).lower() in ('1','true','yes','on')
        force = str(request.args.get('force', '0')).lower() in ('1','true','yes','on')
        kind = f'learning:vision_primitives:v1:window={int(window_sec)}'
        if not force:
            cached, cache_meta = _cache_get_json(kind, max_age_sec=LEARNING_VISION_PRIMITIVES_CACHE_MAX_AGE_SEC, allow_stale=allow_stale)
            if cached:
                try:
                    m = cached.get('meta') or {}
                    m.update({'cache': cache_meta})
                    cached['meta'] = m
                except Exception:
                    pass
                return jsonify(cached)
        payload = _compute_vision_primitives(window_sec=window_sec)
        try:
            _cache_put_json(kind, payload)
        except Exception as e:
            log_suppressed('ui/learning.py:api_vision_primitives_cache_put', exc=e, level=logging.WARNING)
        return jsonify(payload)
    except Exception as e:
        log_suppressed('ui/learning.py:api_vision_primitives', exc=e, level=logging.ERROR)
        return jsonify({'ok': False, 'error': str(e), 'window_sec': _safe_int(request.args.get('window_sec'), 86400)})

@learning_bp.get("/api/intelligence")
def api_intelligence():
    """
    JSON API für Learning-UI: /learning/api/intelligence

    Cache-Strategie (Produktion):
    - Standard: schnell aus Cache (stats.db.energy_top_cache, kind="learning:intelligence")
    - force=1: Cache neu berechnen & schreiben (kann dauern)
    - allow_stale=1 (Default): auch „stale“ Cache zurückgeben (UI bleibt schnell)
    - max_age_sec: Freshness-Schwelle (Default: OROMA_LEARNING_CACHE_MAX_AGE_SEC, i.d.R. 2h)
    """
    try:
        force = int(request.args.get("force", "0")) == 1
    except Exception:
        force = False

    try:
        allow_stale = int(request.args.get("allow_stale", "1")) == 1
    except Exception:
        allow_stale = True

    try:
        max_age = int(request.args.get("max_age_sec", str(LEARNING_CACHE_MAX_AGE_SEC)))
    except Exception:
        max_age = LEARNING_CACHE_MAX_AGE_SEC

    kind = "learning:intelligence"

    if not force:
        cached, cmeta = _cache_get_json(kind, max_age_sec=max_age, allow_stale=allow_stale)
        if cached is not None:
            # Meta anreichern – ohne Payload zu verändern
            cached.setdefault("meta", {})
            if isinstance(cached.get("meta"), dict):
                cached["meta"]["cache"] = cmeta
            # Vision Binding Stage A (latest stats_points snapshot)
            try:
                cached["binding_vision_latest"] = _fetch_latest_binding_vision_points()
                cached["synapses_latest"] = _fetch_latest_synapses_points()
                cached.setdefault("meta", {})
                if isinstance(cached.get("meta"), dict):
                    cached["meta"]["binding_v_count"] = len(cached.get("binding_vision_latest") or {})
            except Exception as e:
                try:
                    log_suppressed(logger="learning.intelligence.binding_v", key="binding_v", msg="cached inject failed", exc=e)
                except Exception:
                    pass
            # Ensure binding_vision_latest is computed live (cache may omit / be stale)
            try:
                cached["binding_vision_latest"] = _fetch_latest_binding_vision_points()
                cached["synapses_latest"] = _fetch_latest_synapses_points()
            except Exception as e:
                try:
                    log_suppressed(logger="binding_v.inject", key="binding_v.inject", msg="inject failed", exc=e)
                except Exception:
                    pass
            return jsonify(cached)

    # --- compute (may be slow) ---
    try:
        window_rewards_sec = int(request.args.get("window_rewards_sec", "3600"))
    except Exception:
        window_rewards_sec = 3600

    try:
        window_policy_update_sec = int(request.args.get("window_policy_update_sec", "600"))
    except Exception:
        window_policy_update_sec = 600

    try:
        window_compression_days = int(request.args.get("window_compression_days", "30"))
    except Exception:
        window_compression_days = 30

    try:
        window_latency_sec = int(request.args.get("window_latency_sec", "300"))
    except Exception:
        window_latency_sec = 300

    try:
        policy_state_sample = int(request.args.get("policy_state_sample", "800"))
    except Exception:
        policy_state_sample = 800

    payload = _compute_intelligence_index(
        window_rewards_sec=window_rewards_sec,
        window_policy_update_sec=window_policy_update_sec,
        window_compression_days=window_compression_days,
        window_latency_sec=window_latency_sec,
        policy_state_sample=policy_state_sample,
    )

    # JSON-Safety: sanitize NaN/Inf before cache + response
    sanitized = False
    try:
        payload2 = _json_sanitize(payload)
        sanitized = (payload2 != payload)
        payload = payload2
    except Exception:
        pass
    if sanitized:
        payload.setdefault("meta", {})
        if isinstance(payload.get("meta"), dict):
            payload["meta"]["sanitized"] = True
            log_suppressed("ui/learning.py:api_intelligence_sanitized", exc=Exception("sanitized non-finite float(s) in payload"), level=logging.WARNING)

    # Cache write (best effort)
    _cache_put_json(kind, payload, ts=_now())

    # attach cache meta so UI can show age later if desired
    payload.setdefault("meta", {})
    if isinstance(payload.get("meta"), dict):
        payload["meta"]["cache"] = {
            "cache": "recomputed",
            "kind": kind,
            "cache_ts": _now(),
            "cache_age_sec": 0,
            "stale": False,
            "max_age_sec": int(max_age),
        }

    # Vision Binding Stage A (latest stats_points snapshot)


    try:


        payload["binding_vision_latest"] = _fetch_latest_binding_vision_points()
        payload["synapses_latest"] = _fetch_latest_synapses_points()
        payload.setdefault("meta", {})
        if isinstance(payload.get("meta"), dict):
            payload["meta"]["binding_v_count"] = len(payload.get("binding_vision_latest") or {})


    except Exception as e:


        try:


            log_suppressed(logger="learning.intelligence.binding_v", key="binding_v", msg="payload inject failed", exc=e)


        except Exception:


            pass


    return jsonify(payload)
# =============================================================================
# END BLOCK: INTELLIGENCE_INDEX_API

# =============================================================================

# =============================================================================
# BLOCK: MAXIMA_API (SWAPPABLE)
# =============================================================================
def _maxima_for_series_prefix(
    sconn: sqlite3.Connection,
    *,
    since_ts: int,
    series_where_sql: str,
    period_kind: str,
    limit_rows: int,
) -> List[Dict[str, Any]]:
    """Aggregiert stats_points pro Zeitraum *und* Serie.

    Motivation
    ----------
    In der UI waren die Tabellen "Max pro Tag/Woche/Monat" bisher so gebaut, dass pro
    Zeitraum nur *eine* Serie gezeigt wurde (die Serie, die den Maximalwert hatte).
    Für Diagnose/Transparenz ist es jedoch wesentlich hilfreicher, *jede* Serie pro
    Zeitraum zu sehen – inkl. Durchschnitt.

    Output
    ------
    List of dicts (ascending by period, then by v_max desc):
      {period, series, n, v_avg, v_max}

    Robustness
    ----------
    - Query nutzt kein Window-Feature (kompatibel mit älteren SQLite Builds).
    - Limitiert Anzahl der Perioden (limit_rows) – innerhalb der Perioden werden
      alle Serien geliefert.
    """
    if period_kind == "day":
        period_expr = "date(ts,'unixepoch','localtime')"
    elif period_kind == "week":
        period_expr = "strftime('%Y-W%W', ts,'unixepoch','localtime')"
    elif period_kind == "month":
        period_expr = "strftime('%Y-%m', ts,'unixepoch','localtime')"
    else:
        raise ValueError("invalid period_kind")

    # 1) Perioden-Window ermitteln (descending), damit limit_rows = Anzahl Perioden
    q_periods = f"""
    SELECT DISTINCT {period_expr} AS period
      FROM stats_points
     WHERE ts >= ?
       AND ({series_where_sql})
  ORDER BY period DESC
     LIMIT ?
    """
    periods_rows = sconn.execute(q_periods, (int(since_ts), int(limit_rows))).fetchall()
    periods = [str(r["period"]) for r in (periods_rows or []) if r and r["period"] is not None]
    if not periods:
        return []

    # 2) Für diese Perioden: pro Serie avg/max/n berechnen
    ph = ",".join(["?"] * len(periods))
    q = f"""
    WITH base AS (
      SELECT {period_expr} AS period, series, value
        FROM stats_points
       WHERE ts >= ?
         AND ({series_where_sql})
         AND {period_expr} IN ({ph})
    )
    SELECT period,
           series,
           COUNT(*)        AS n,
           AVG(value)      AS v_avg,
           MAX(value)      AS v_max
      FROM base
  GROUP BY period, series
  ORDER BY period ASC, v_max DESC, series ASC
    """
    bind: List[Any] = [int(since_ts)] + periods
    rows = sconn.execute(q, tuple(bind)).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        out.append(
            {
                "period": str(r["period"]),
                "series": str(r["series"] or ""),
                "n": _safe_int(r["n"]) if r and ("n" in r.keys()) else 0,
                "v_avg": _safe_float(r["v_avg"]) if r and ("v_avg" in r.keys()) else 0.0,
                "v_max": _safe_float(r["v_max"]) if r and ("v_max" in r.keys()) else 0.0,
            }
        )
    return out


# =============================================================================
# BLOCK: LEARNING_MAXIMA_HELPERS (SWAPPABLE)
# =============================================================================
# Ziel
# ----
# Maxima-Recompute kann bei grossen stats_points Tabellen mehrere Sekunden bis
# >30s dauern. Browser (Safari) bricht dann Fetch ab. Deshalb:
# - API liefert Cache/Stale schnell aus
# - Recompute laeuft asynchron (One-Shot oder Background-Loop)
# - Jede Fallback-Entscheidung wird geloggt
# =============================================================================

def _compute_maxima_payload(*, days: int, take_day: int, take_week: int, take_month: int) -> Dict[str, Any]:
    now = _now()
    out: Dict[str, Any] = {
        "ok": False,
        "ts": now,
        "days": int(days),
        "rewards": {"daily": [], "weekly": [], "monthly": []},
        "metrics": {"daily": [], "weekly": [], "monthly": []},
        "empathy": {"daily": [], "weekly": [], "monthly": []},
        "coverage": {"daily": [], "weekly": [], "monthly": []},
        "meta": {"stats_db_ok": False, "errors": []},
    }

    since_ts = now - int(days) * 86400

    sconn = None
    try:
        sconn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=2.0)
        out["meta"]["stats_db_ok"] = True

        def _fill(series_where_sql: str, key: str) -> None:
            try:
                out[key]["daily"] = _maxima_for_series_prefix(
                    sconn,
                    since_ts=since_ts,
                    series_where_sql=series_where_sql,
                    period_kind="day",
                    limit_rows=take_day,
                )
                out[key]["weekly"] = _maxima_for_series_prefix(
                    sconn,
                    since_ts=since_ts,
                    series_where_sql=series_where_sql,
                    period_kind="week",
                    limit_rows=take_week,
                )
                out[key]["monthly"] = _maxima_for_series_prefix(
                    sconn,
                    since_ts=since_ts,
                    series_where_sql=series_where_sql,
                    period_kind="month",
                    limit_rows=take_month,
                )
            except Exception as e:
                out["meta"]["errors"].append(f"{key}_maxima_failed:{e!r}")

        _fill("series LIKE 'reward:%'", "rewards")
        _fill("series LIKE 'metric:%'", "metrics")
        _fill("series = 'empathy_score'", "empathy")
        _fill("series = 'coverage_30d'", "coverage")

        out["ok"] = True
        return _json_sanitize(out)

    except Exception as e:
        out["meta"]["errors"].append(f"stats_db_failed:{e!r}")
        return _json_sanitize(out)

    finally:
        try:
            if sconn is not None:
                sconn.close()
        except Exception:
            pass


def _recompute_maxima_cache(*, reason: str = "manual") -> bool:
    """Recomputes the maxima cache and writes it to stats.db cache table.

    Returns True on success.
    """
    t0 = time.time()
    days, take_day, take_week, take_month = 365, 14, 12, 12
    payload = _compute_maxima_payload(days=days, take_day=take_day, take_week=take_week, take_month=take_month)
    ok = _cache_put_json("learning:maxima", payload, ts=_now())
    dt = time.time() - t0
    if ok:
        log_suppressed('ui/learning.py:maxima_cache_recomputed', exc=Exception(f'ok reason={reason} dur={dt:.3f}s'), level=logging.INFO)
    else:
        log_suppressed('ui/learning.py:maxima_cache_recompute_failed', exc=Exception(f'fail reason={reason} dur={dt:.3f}s'), level=logging.ERROR)
    return bool(ok)

# =============================================================================
# END BLOCK: LEARNING_MAXIMA_HELPERS
# =============================================================================

@learning_bp.get("/api/maxima")
def api_maxima():
    """
    Liefert Maxima pro Tag/Woche/Monat aus stats.db → stats_points.

    Cache-Strategie (Produktion):
    - Standard: schnell aus Cache (stats.db.energy_top_cache, kind="learning:maxima")
    - force=1: Cache neu berechnen & schreiben (kann dauern)
    - allow_stale=1 (Default): auch „stale“ Cache zurückgeben (UI bleibt schnell)
    - max_age_sec: Freshness-Schwelle (Default: OROMA_LEARNING_CACHE_MAX_AGE_SEC, i.d.R. 2h)
    """
    now = _now()

    try:
        force = int(request.args.get("force", "0")) == 1
    except Exception:
        force = False

    try:
        allow_stale = int(request.args.get("allow_stale", "1")) == 1
    except Exception:
        allow_stale = True

    try:
        max_age = int(request.args.get("max_age_sec", str(LEARNING_CACHE_MAX_AGE_SEC)))
    except Exception:
        max_age = LEARNING_CACHE_MAX_AGE_SEC

    kind = "learning:maxima"

    # Niemals "still leer": Cache als Fallback (auch bei force=1)
    cached_fallback, cached_meta = _cache_get_json(kind, max_age_sec=max_age, allow_stale=True)
    if not isinstance(cached_fallback, dict) or cached_fallback.get("ok") is not True:
        cached_fallback = None

    if not force:
        cached, cmeta = _cache_get_json(kind, max_age_sec=max_age, allow_stale=allow_stale)
        if cached is not None:
            # ignore bad cache
            if not isinstance(cached, dict) or cached.get("ok") is not True:
                cached = None
            else:
                # required keys
                for _k in ("rewards", "metrics", "empathy", "coverage"):
                    if _k not in cached or not isinstance(cached.get(_k), dict):
                        cached = None
                        break
            # meta merge
            cached.setdefault("meta", {})
            if isinstance(cached.get("meta"), dict):
                cached["meta"]["cache"] = cmeta
            return jsonify(cached)
    # force=1: niemals synchron rechnen (Safari AbortError).
    # Stattdessen: Cache sofort liefern + Hintergrund-Recompute anstossen.
    if force:
        try:
            _start_learning_bg_worker(reason='api_force')
        except Exception:
            pass
        try:
            _trigger_bg_one_shot('maxima', lambda: _recompute_maxima_cache(reason='force_api'), reason='force_api')
        except Exception as e:
            log_suppressed('ui/learning.py:api_maxima_force_spawn_fail', exc=e, level=logging.ERROR)

        if cached_fallback is not None:
            try:
                cached_fallback = dict(cached_fallback)
                cached_fallback.setdefault('meta', {})
                if isinstance(cached_fallback.get('meta'), dict):
                    cached_fallback['meta']['cache'] = cached_meta
                    cached_fallback['meta']['recompute'] = {'started': True, 'kind': kind, 'reason': 'force_api'}
                    cached_fallback['meta']['stale_used'] = True
            except Exception:
                pass
            log_suppressed('ui/learning.py:api_maxima_force_return_stale', exc=Exception('returned cached_fallback and started bg recompute'), level=logging.INFO)
            return jsonify(cached_fallback)

        # Kein Cache vorhanden (Erstlauf) -> inline compute als Notfall (kann dauern, wird aber geloggt).
        log_suppressed('ui/learning.py:api_maxima_force_no_cache_inline', exc=Exception('no maxima cache present; doing inline compute'), level=logging.WARNING)
        payload = _compute_maxima_payload(days=365, take_day=14, take_week=12, take_month=12)
        _cache_put_json(kind, payload, ts=_now())
        return jsonify(payload)

    # --- compute (may be slow) ---
    try:
        days = int(request.args.get("days", "365"))
    except Exception:
        days = 365

    try:
        take_day = int(request.args.get("take_day", "14"))
    except Exception:
        take_day = 14

    try:
        take_week = int(request.args.get("take_week", "12"))
    except Exception:
        take_week = 12

    try:
        take_month = int(request.args.get("take_month", "12"))
    except Exception:
        take_month = 12

    since_ts = now - max(1, int(days)) * 86400

    out: Dict[str, Any] = {
        "ok": False,
        "ts": now,
        "days": int(days),
        "rewards": {"daily": [], "weekly": [], "monthly": []},
        "metrics": {"daily": [], "weekly": [], "monthly": []},
        "empathy": {"daily": [], "weekly": [], "monthly": []},
        "coverage": {"daily": [], "weekly": [], "monthly": []},
        "meta": {"stats_db_ok": False, "errors": [], "sampled": False},
    }

    try:
        # READ-ONLY Query-Connection (robust gegen Writer-Locks)
        s = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.2)
        try:
            s.execute("PRAGMA busy_timeout = 2500")
        except Exception:
            pass
        out["meta"]["stats_db_ok"] = True
    except Exception as e:
        out["meta"]["errors"].append(f"stats.db open(ro) failed: {e}")
        log_suppressed('ui/learning.py:api_maxima_open_ro', exc=e, level=logging.ERROR)
        if cached_fallback is not None:
            try:
                cached_fallback = dict(cached_fallback)
                cached_fallback.setdefault("meta", {})
                if isinstance(cached_fallback.get("meta"), dict):
                    cached_fallback["meta"]["cache"] = cached_meta
                    cached_fallback["meta"]["errors"] = (cached_fallback["meta"].get("errors") or []) + [f"open_ro_failed:{e}"]
                    cached_fallback["meta"]["stale_used"] = True
            except Exception:
                pass
            return jsonify(cached_fallback)
        return jsonify(out)

    try:
        # Rewards
        out["rewards"]["daily"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series LIKE 'reward:%'", period_kind="day", limit_rows=take_day
        )
        out["rewards"]["weekly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series LIKE 'reward:%'", period_kind="week", limit_rows=take_week
        )
        out["rewards"]["monthly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series LIKE 'reward:%'", period_kind="month", limit_rows=take_month
        )

        # Metrics
        out["metrics"]["daily"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series LIKE 'metric:%'", period_kind="day", limit_rows=take_day
        )
        out["metrics"]["weekly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series LIKE 'metric:%'", period_kind="week", limit_rows=take_week
        )
        out["metrics"]["monthly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series LIKE 'metric:%'", period_kind="month", limit_rows=take_month
        )

        # Empathy
        out["empathy"]["daily"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series = 'empathy_score'", period_kind="day", limit_rows=take_day
        )
        out["empathy"]["weekly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series = 'empathy_score'", period_kind="week", limit_rows=take_week
        )
        out["empathy"]["monthly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series = 'empathy_score'", period_kind="month", limit_rows=take_month
        )

        # Coverage
        out["coverage"]["daily"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series = 'coverage_30d'", period_kind="day", limit_rows=take_day
        )
        out["coverage"]["weekly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series = 'coverage_30d'", period_kind="week", limit_rows=take_week
        )
        out["coverage"]["monthly"] = _maxima_for_series_prefix(
            s, since_ts=since_ts, series_where_sql="series = 'coverage_30d'", period_kind="month", limit_rows=take_month
        )

        out["ok"] = True

        # JSON-Safety: sanitize NaN/Inf before cache + response
        sanitized = False
        try:
            out2 = _json_sanitize(out)
            sanitized = (out2 != out)
            out = out2
        except Exception:
            pass
        if sanitized:
            out.setdefault("meta", {})
            if isinstance(out.get("meta"), dict):
                out["meta"]["sanitized"] = True
                log_suppressed("ui/learning.py:api_maxima_sanitized", exc=Exception("sanitized non-finite float(s) in maxima"), level=logging.WARNING)

        # Cache write (best effort)
        _cache_put_json(kind, out, ts=now)

        out.setdefault("meta", {})
        if isinstance(out.get("meta"), dict):
            out["meta"]["cache"] = {
                "cache": "recomputed",
                "kind": kind,
                "cache_ts": now,
                "cache_age_sec": 0,
                "stale": False,
                "max_age_sec": int(max_age),
            }

        return jsonify(out)

    except Exception as e:
        out["meta"]["errors"].append(f"maxima query failed: {e}")
        log_suppressed('ui/learning.py:api_maxima_query', exc=e, level=logging.ERROR)

        # Niemals "still leer": wenn Cache existiert, liefern wir ihn als Stale-Fallback.
        if cached_fallback is not None:
            try:
                cf = dict(cached_fallback)
                cf.setdefault("meta", {})
                if isinstance(cf.get("meta"), dict):
                    cf["meta"]["cache"] = cached_meta
                    cf["meta"]["errors"] = (cf["meta"].get("errors") or []) + [f"recompute_failed:{e}"]
                    cf["meta"]["stale_used"] = True
            except Exception:
                cf = cached_fallback
            return jsonify(cf)

        return jsonify(out)
    finally:
        try:
            s.close()
        except Exception:
            pass

# =============================================================================
# END BLOCK: MAXIMA_API

# =============================================================================


@learning_bp.get("/api/history")
def api_history():
    """
    Punkte für 4 Charts (Rewards/Metrics/Empathy/Coverage)

    Produktionsstrategie ab dieser Revision
    --------------------------------------
    - Standard: schnell aus generischem JSON-Cache (stats.db.energy_top_cache)
    - allow_stale=1 (Default): auch leicht gealterten Cache liefern
    - force=1: synchron *nicht* gegen 40-GB-stats.db blockieren; stattdessen
      sofort vorhandenen Cache liefern und Hintergrund-Recompute anstossen
    - force_sampler=1: expliziter Debug-Pfad fuer synchrones Sampling bleibt erhalten

    Motivation
    ----------
    Der 24h-History-Endpoint war der langsamste Initial-Load der Learning-UI
    und blockierte damit den gesamten Seitenaufbau. Diese API soll fuer die UI
    nun bewusst "fast first" arbeiten.
    """
    try:
        window_sec = int(request.args.get("window_sec", "86400"))
    except Exception:
        window_sec = 86400

    metrics_keys = _normalize_metrics_keys(_parse_metrics_keys(request.args.get("metrics_keys")))

    try:
        force = int(request.args.get("force", "0")) == 1
    except Exception:
        force = False

    try:
        allow_stale = int(request.args.get("allow_stale", "1")) == 1
    except Exception:
        allow_stale = True

    try:
        max_age = int(request.args.get("max_age_sec", str(LEARNING_HISTORY_CACHE_MAX_AGE_SEC)))
    except Exception:
        max_age = LEARNING_HISTORY_CACHE_MAX_AGE_SEC

    force_sampler = str(request.args.get("force_sampler", "0")).strip().lower() in ("1", "true", "yes", "on")
    kind = _history_cache_kind(window_sec=window_sec, metrics_keys=metrics_keys)

    cached_fallback, cached_meta = _cache_get_json(kind, max_age_sec=max_age, allow_stale=True)
    if not isinstance(cached_fallback, dict) or not isinstance(cached_fallback.get("meta"), dict):
        cached_fallback = None

    if not force and not force_sampler:
        cached, cmeta = _cache_get_json(kind, max_age_sec=max_age, allow_stale=allow_stale)
        if isinstance(cached, dict):
            cached.setdefault("meta", {})
            if isinstance(cached.get("meta"), dict):
                cached["meta"]["cache"] = cmeta
            return jsonify(cached)

    if force and not force_sampler:
        try:
            _start_learning_bg_worker(reason='api_force_history')
        except Exception:
            pass
        try:
            _trigger_bg_one_shot(
                f"history:{kind}",
                lambda: _recompute_history_cache(window_sec=window_sec, metrics_keys=metrics_keys, reason='force_api'),
                reason='force_api',
            )
        except Exception as e:
            log_suppressed('ui/learning.py:api_history_force_spawn_fail', exc=e, level=logging.ERROR)

        if cached_fallback is not None:
            cached_fallback = dict(cached_fallback)
            cached_fallback.setdefault('meta', {})
            if isinstance(cached_fallback.get('meta'), dict):
                cached_fallback['meta']['cache'] = cached_meta
                cached_fallback['meta']['recompute'] = {'started': True, 'kind': kind, 'reason': 'force_api'}
                cached_fallback['meta']['stale_used'] = True
            return jsonify(cached_fallback)

    # Debug-/No-Cache-Pfad: explizit live berechnen.
    payload = _build_history_payload(window_sec=window_sec, metrics_keys=metrics_keys)

    try:
        payload.setdefault("meta", {})
        if isinstance(payload.get("meta"), dict):
            payload["meta"]["cache"] = {
                "cache": "recomputed",
                "kind": kind,
                "cache_ts": _now(),
                "cache_age_sec": 0,
                "stale": False,
                "max_age_sec": int(max_age),
            }
        _cache_put_json(kind, payload, ts=_now())
    except Exception as e:
        log_suppressed("ui/learning.py:api_history_cache_put_failed", exc=e, level=logging.WARNING)

    return jsonify(payload)



@learning_bp.get("/api/testgraph")
def api_testgraph():
    """Liefert einen synthetischen Testgraphen (offline, ohne DB) für UI-Checks."""
    try:
        return jsonify(_make_testgraph_payload())
    except Exception as e:
        return jsonify({"ok": False, "error": f"testgraph failed: {e}"}), 500


@learning_bp.get("/api/selftest")
def api_selftest():
    """Kompakter Selftest für /learning.

    Erwartet von der UI als TEXT (nicht JSON).
    """
    lines = []
    now = _now()
    lines.append(f"[learning] selftest @ {datetime.datetime.fromtimestamp(now).isoformat(sep=' ', timespec='seconds')}")
    lines.append("")

    # Stats DB
    try:
        lines.append(f"stats.db: {OROMA_STATS_DB_PATH}")
        conn = _db_connect(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=2.0)
        try:
            n_points = conn.execute("SELECT COUNT(*) FROM stats_points").fetchone()[0]
            lines.append(f"  stats_points: {int(n_points)} rows")
            if _table_exists(conn, "stats_curve_day"):
                n_curve = conn.execute("SELECT COUNT(*) FROM stats_curve_day").fetchone()[0]
                lines.append(f"  stats_curve_day: {int(n_curve)} rows")
            if _table_exists(conn, "energy_top_cache"):
                r = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM energy_top_cache").fetchone()
                lines.append(f"  energy_top_cache: {int(r[0])} rows (ts min={r[1]} max={r[2]})")
            if _table_exists(conn, "energy_state"):
                cols = [rr[1] for rr in conn.execute("PRAGMA table_info(energy_state)").fetchall()]
                want = [c for c in ["weights_rel_applied","weights_node_applied","retro_rel_cursor","retro_node_cursor"] if c in cols]
                r = None
                if want:
                    r = conn.execute("SELECT " + ",".join(want) + " FROM energy_state WHERE id=1").fetchone()
                if r is not None:
                    vals = [int((r[i] or 0)) for i in range(len(r))]
                    parts = []
                    for i, name in enumerate(want):
                        try:
                            parts.append(f"{name}={vals[i]}")
                        except Exception:
                            pass
                    lines.append("  energy_state: " + (" ".join(parts) if parts else "(no columns)"))
        finally:
            conn.close()
    except Exception as e:
        lines.append(f"stats.db: ERROR -> {e}")

    lines.append("")

    # Main DB (optional)
    try:
        lines.append(f"oroma.db: {OROMA_DB_PATH}")
        conn = _db_connect(OROMA_DB_PATH, readonly=True, timeout_sec=2.0)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 8").fetchall()]
            lines.append(f"  tables(sample): {', '.join(tables) if tables else '-'}")
        finally:
            conn.close()
    except Exception as e:
        lines.append(f"oroma.db: ERROR -> {e}")

    return Response("\n".join(lines) + "\n", mimetype="text/plain; charset=utf-8")


@learning_bp.get("/api/curve")
def api_curve():
    try:
        days = int(request.args.get("days", "120"))
    except Exception:
        days = 120

    data = _curve_from_stats(days=days)
    if not data.get("ok"):
        data = _curve_from_main(days=days)
    return jsonify(data)


@learning_bp.get("/api/curve.csv")
def api_curve_csv():
    try:
        days = int(request.args.get("days", "365"))
    except Exception:
        days = 365

    data = _curve_from_stats(days=days)
    if not data.get("ok"):
        data = _curve_from_main(days=days)

    # CSV-Format bewusst stabil halten (für externe Auswertung / Austausch):
    # Reihenfolge: q_avg vor q_max (User-Wunsch, Logik: Durchschnitt → Maximum)
    rows: List[str] = ["granularity,period,chains,q_avg,q_max"]

    for r in data.get("daily", []):
        rows.append(
            f"day,{r.get('period','')},{r.get('chains',0)},{_safe_float(r.get('q_avg')):.6f},{_safe_float(r.get('q_max')):.6f}"
        )
    for r in data.get("weekly", []):
        rows.append(
            f"week,{r.get('period','')},{r.get('chains',0)},{_safe_float(r.get('q_avg')):.6f},{_safe_float(r.get('q_max')):.6f}"
        )
    for r in data.get("monthly", []):
        rows.append(
            f"month,{r.get('period','')},{r.get('chains',0)},{_safe_float(r.get('q_avg')):.6f},{_safe_float(r.get('q_max')):.6f}"
        )

    csv_text = "\n".join(rows) + "\n"
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=oroma_learning_curve_summary.csv"},
    )


@learning_bp.get("/api/export.csv")
def api_export_csv():
    """Export *alle* Daten, die die Learning-UI anzeigt, als CSV (Semikolon-getrennt).

    WICHTIG (User-Regel)
    --------------------
    - Die UI darf nicht "still" ausfallen. Bei Problemen wird immer geloggt.
    - Safari bricht lange Fetch-Requests ab. Daher bevorzugt diese Route einen
      File-Cache (alles-export) und startet einen Rebuild asynchron.

    Cache-Verhalten
    ---------------
    - Default: cached=1 → wenn Cache-Datei existiert, sofort ausliefern
    - Wenn Cache... (long string) ...
    """

    raw = _safe_int(request.args.get("raw", "0"), 0)
    now = _now()

    # Cache-Parameter
    use_cache = True
    try:
        use_cache = int(request.args.get("cached", "1")) == 1
    except Exception:
        use_cache = True

    cache_path = str(OROMA_LEARNING_EXPORT_CACHE_PATH)
    max_age = int(OROMA_LEARNING_EXPORT_MAX_AGE_SEC)

    def _file_age_sec(path: str) -> float:
        try:
            st = os.stat(path)
            return max(0.0, float(time.time() - float(st.st_mtime)))
        except Exception:
            return 1e12

    # 1) Schneller Pfad: Cache liefern
    if use_cache and cache_path:
        age = _file_age_sec(cache_path)
        if age < 1e10:
            # Cache existiert → sofort liefern, ggf. stale markieren
            if age > float(max_age):
                # stale: Hintergrund-Rebuild anstossen, aber trotzdem liefern
                log_suppressed('ui/learning.py:export_cache_stale', exc=Exception(f'age={age:.0f}s max_age={max_age}s'), level=logging.WARNING)
                try:
                    _start_learning_bg_worker(reason='export_cache_stale')
                    _trigger_bg_one_shot('export', lambda: _build_export_cache_file(reason='stale'), reason='stale')
                except Exception as e:
                    log_suppressed('ui/learning.py:export_cache_stale_spawn_fail', exc=e, level=logging.ERROR)
                try:
                    body = Path(cache_path).read_text(encoding='utf-8')
                    return Response(
                        body,
                        mimetype='text/csv; charset=utf-8',
                        headers={
                            'Content-Disposition': 'attachment; filename=oroma_learning_export_all.csv',
                            'Cache-Control': 'no-store',
                            'X-OROMA-Cache-Stale': '1',
                            'X-OROMA-Cache-Age-Sec': str(int(age)),
                        },
                    )
                except Exception as e:
                    log_suppressed('ui/learning.py:export_cache_read_fail', exc=e, level=logging.ERROR)
            else:
                # fresh
                try:
                    body = Path(cache_path).read_text(encoding='utf-8')
                    return Response(
                        body,
                        mimetype='text/csv; charset=utf-8',
                        headers={
                            'Content-Disposition': 'attachment; filename=oroma_learning_export_all.csv',
                            'Cache-Control': 'no-store',
                            'X-OROMA-Cache-Stale': '0',
                            'X-OROMA-Cache-Age-Sec': str(int(age)),
                        },
                    )
                except Exception as e:
                    log_suppressed('ui/learning.py:export_cache_read_fail', exc=e, level=logging.ERROR)

    # 2) Slow path: inline bauen (wird geloggt) + Cache schreiben (best-effort)
    t0 = time.time()
    log_suppressed('ui/learning.py:export_inline_build_start', exc=Exception(f'raw={raw} cached={int(use_cache)}'), level=logging.INFO)

    body = _export_csv_build_body(raw=raw, now=now)

    # Cache write (best-effort)
    if use_cache and cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + '.tmp'
            Path(tmp).write_text(body, encoding='utf-8')
            os.replace(tmp, cache_path)
            dt = time.time() - t0
            log_suppressed('ui/learning.py:export_cache_write_ok', exc=Exception(f'dur={dt:.3f}s path={cache_path}'), level=logging.INFO)
        except Exception as e:
            log_suppressed('ui/learning.py:export_cache_write_fail', exc=e, level=logging.ERROR)

    dt = time.time() - t0
    log_suppressed('ui/learning.py:export_inline_build_done', exc=Exception(f'dur={dt:.3f}s'), level=logging.INFO)

    return Response(
        body,
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename=oroma_learning_export_all.csv',
            'Cache-Control': 'no-store',
        },
    )


@learning_bp.get("/api/export_meta")
def api_export_meta():
    """Return metadata about the cached "Alles-Export" CSV.

    The export file is cached at:
      /opt/ai/oroma/data/cache/learning_export_all.csv

    This endpoint is intentionally lightweight and safe:
      • Never raises (best-effort).
      • Does NOT open oroma.db / stats.db.
      • Enables the UI to show when the export was generated.
    """

    now = int(time.time())
    cache_path = str(OROMA_LEARNING_EXPORT_CACHE_PATH)

    out = {
        "ok": True,
        "ts": now,
        "path": cache_path,
        "exists": False,
        "bytes": 0,
        "mtime_ts": 0,
        "mtime_local": "",
        "age_sec": None,
        "export_generated_at": "",
    }

    try:
        p = Path(cache_path)
        if p.exists() and p.is_file():
            st = p.stat()
            out["exists"] = True
            out["bytes"] = int(st.st_size)
            out["mtime_ts"] = int(st.st_mtime)
            out["age_sec"] = max(0, int(now) - int(st.st_mtime))
            try:
                out["mtime_local"] = datetime.datetime.fromtimestamp(int(st.st_mtime)).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                out["mtime_local"] = str(int(st.st_mtime))

            # Parse first line (user requested the explicit export timestamp).
            try:
                with p.open('r', encoding='utf-8', errors='replace') as f:
                    first = f.readline().strip()
                if first.startswith('# export_generated_at='):
                    # The export header is written in local time already.
                    dt_txt = first.split('=', 1)[1].strip()
                    out["export_generated_at"] = dt_txt
                    # UI/clients may look for a "*_local" variant; provide it explicitly.
                    out["export_generated_at_local"] = dt_txt
            except Exception as e:
                log_suppressed('ui/learning.py:export_meta_read_fail', exc=e, level=logging.ERROR)

    except Exception as e:
        # Keep endpoint stable; provide minimal diagnostics via suppressed logger.
        out["ok"] = False
        log_suppressed('ui/learning.py:export_meta_fail', exc=e, level=logging.ERROR)

    return jsonify(out)


# =============================================================================
# BLOCK: LEARNING_EXPORT_BUILD_BODY (SWAPPABLE)
# =============================================================================
def _export_csv_build_body(*, raw: int, now: int) -> str:
    """Builds the "Alles-Export" CSV body as text.

    This function is used by:
    - /learning/api/export.csv (inline export)
    - Background worker (periodic cache file generation)

    Design goals
    ------------
    - Best-effort: never raises to caller; errors are embedded as comment lines
      and logged via log_suppressed.
    - Always closes DB connections (avoid DB locks).
    """

    def _csv_cell(v):
        if v is None:
            return ""
        s = str(v)
        # Minimal CSV escaping for ';' and newlines
        if any(ch in s for ch in [";", "\n", "\r", '"']):
            s = s.replace('"', '""')
            return f'"{s}"'
        return s

    def _emit_section(lines, title: str):
        lines.append("")
        lines.append(f"# --- {title} ---")

    lines = []
    # Erste Zeile: explizites Export-Datum inkl. Uhrzeit (User-Wunsch).
    # Wir verwenden lokale Zeit (wie die UI-Anzeige). Falls der Container/
    # Host auf UTC laeuft, bleibt dies trotzdem konsistent (ts ist separat).
    try:
        dt_txt = datetime.datetime.fromtimestamp(int(now)).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        dt_txt = str(int(now))
    lines.append(f"# export_generated_at={dt_txt}")

    lines.append("# ORÓMA Learning – Alles-Export")
    lines.append(f"# ts={int(now)}")
    lines.append(f"# raw={int(raw)}")

    # ---------------------------------------------------------------------
    # 1) stats.db – stats_points summary + maxima cache + energy cache snapshot
    # ---------------------------------------------------------------------
    sconn = None
    try:
        try:
            sconn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=2.0)
            try:
                sconn.row_factory = sqlite3.Row
            except Exception:
                pass
        except Exception as e:
            lines.append(f"# error=stats_db_open_failed:{e!r}")
            log_suppressed('ui/learning.py:export_stats_open_fail', exc=e, level=logging.ERROR)
            return "\n".join(lines) + "\n"

        _emit_section(lines, "stats_points_summary")
        try:
            rows = sconn.execute(
                "SELECT series, COUNT(*) n, ROUND(AVG(value),6) avg, ROUND(MAX(value),6) mx "
                "FROM stats_points GROUP BY series ORDER BY n DESC LIMIT 500"
            ).fetchall()
            lines.append("series;n;avg;max")
            for r in rows or []:
                lines.append(
                    f"{_csv_cell(r['series'])};{_safe_int(r['n'],0)};{_safe_float(r['avg'],0.0):.6f};{_safe_float(r['mx'],0.0):.6f}"
                )
        except Exception as e:
            lines.append(f"# error=stats_points_summary_failed:{e!r}")
            log_suppressed('ui/learning.py:export_stats_points_summary_fail', exc=e, level=logging.ERROR)

        _emit_section(lines, "maxima_cache")
        try:
            kind = 'learning:maxima'
            payload, meta = _cache_get_json(kind, max_age_sec=int(LEARNING_CACHE_MAX_AGE_SEC), allow_stale=True)
            if isinstance(payload, dict):
                lines.append("# cache_meta_json=" + json.dumps(meta, ensure_ascii=False, sort_keys=True))
                for k in ['rewards', 'metrics', 'empathy', 'coverage']:
                    try:
                        dd = payload.get(k, {}) or {}
                        lines.append(
                            f"{k};day_rows={len(dd.get('daily',[]) or [])};week_rows={len(dd.get('weekly',[]) or [])};month_rows={len(dd.get('monthly',[]) or [])}"
                        )
                    except Exception:
                        pass
            else:
                lines.append("# note=no_maxima_cache")
        except Exception as e:
            lines.append(f"# error=maxima_cache_read_failed:{e!r}")
            log_suppressed('ui/learning.py:export_maxima_cache_read_fail', exc=e, level=logging.ERROR)

        _emit_section(lines, "energy_top_cache")
        try:
            # energy_top_cache wird vom Energy-Producer geschrieben (Timer/Orchestrator).
            # Historisch gab es verschiedene "kind"-Werte; wir lesen deshalb explizit die drei
            # relevanten Streams und erzeugen eine kleine Cache-Meta-Zeile als JSON (eine Zeile).
            rows = sconn.execute(
                "SELECT kind, payload_json, ts FROM energy_top_cache "
                "WHERE kind IN ('objects','relations','nodes') ORDER BY ts DESC"
            ).fetchall()
            if not rows:
                lines.append("# note=no_energy_cache")
                try:
                    log_suppressed("ui/learning.py:export_all:no_energy_cache", level=logging.WARNING)
                except Exception:
                    pass
            else:
                latest = {}
                for kind, payload, ts in rows:
                    if kind not in latest:
                        latest[kind] = (payload, _safe_int(ts, 0))

                now_ts = int(time.time())
                max_ts = max(v[1] for v in latest.values()) if latest else 0
                age = max(0, now_ts - max_ts) if max_ts else 0
                max_age = 7200  # 2h – analog zur UI-Cache-Annahme
                stale = bool(max_ts and age > max_age)

                meta = {
                    "cache": "hit",
                    "kind": "energy_top_cache",
                    "cache_ts": max_ts,
                    "cache_age_sec": age,
                    "stale": stale,
                    "max_age_sec": max_age,
                    "kinds": sorted(list(latest.keys())),
                }
                lines.append("# cache_meta=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")))

                # Pro Kind eine Zeile (CSV-kompatibel).
                for kind in ("objects", "relations", "nodes"):
                    if kind not in latest:
                        continue
                    payload, ts = latest[kind]
                    k_age = max(0, now_ts - ts) if ts else 0
                    lines.append(f"{kind};ts={ts};age_sec={k_age};payload_json={_csv_cell(payload)}")

                if stale:
                    try:
                        log_suppressed(
                            "ui/learning.py:export_all:energy_cache_stale",
                            extra={"age_sec": age, "max_age_sec": max_age, "cache_ts": max_ts},
                            level=logging.WARNING,
                        )
                    except Exception:
                        pass
        except Exception as e:
            lines.append(f"# error=energy_cache_read_failed:{_csv_cell(str(e))}")

    finally:
        try:
            if sconn is not None:
                sconn.close()
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # 2) oroma.db – Learning-relevant counts
    # ---------------------------------------------------------------------
    mconn = None
    try:
        try:
            mconn = _connect_sqlite(OROMA_DB_PATH, readonly=True, timeout_sec=2.0)
            try:
                mconn.row_factory = sqlite3.Row
            except Exception:
                pass
        except Exception as e:
            lines.append(f"# error=oroma_db_open_failed:{e!r}")
            log_suppressed('ui/learning.py:export_oroma_open_fail', exc=e, level=logging.ERROR)
            return "\n".join(lines) + "\n"

        _emit_section(lines, "oroma_db_counts")

        def _count(sql, params=()):
            try:
                r = mconn.execute(sql, params).fetchone()
                if not r:
                    return 0
                if isinstance(r, sqlite3.Row):
                    return int(list(r)[0])
                return int(r[0])
            except Exception:
                return 0

        lines.append(f"snapchains;{_count('SELECT COUNT(*) FROM snapchains')}")
        lines.append(f"rewards_log;{_count('SELECT COUNT(*) FROM rewards_log')}")
        lines.append(f"metrics;{_count('SELECT COUNT(*) FROM metrics')}")
        lines.append(f"empathy_snaps;{_count('SELECT COUNT(*) FROM empathy_snaps')}")
        lines.append(f"coverage_log_30d;{_count('SELECT COUNT(*) FROM coverage_log_30d')}")

    finally:
        try:
            if mconn is not None:
                mconn.close()
        except Exception:
            pass

    return "\n".join(lines) + "\n"


def _build_export_cache_file(*, reason: str = 'manual') -> bool:
    """Builds and writes the export cache file.

    Called by background worker / on-demand triggers.
    """
    path = str(OROMA_LEARNING_EXPORT_CACHE_PATH)
    if not path:
        return False
    t0 = time.time()
    body = _export_csv_build_body(raw=0, now=_now())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        Path(tmp).write_text(body, encoding='utf-8')
        os.replace(tmp, path)
        dt = time.time() - t0
        log_suppressed('ui/learning.py:export_cache_built', exc=Exception(f'ok reason={reason} dur={dt:.3f}s path={path}'), level=logging.INFO)
        return True
    except Exception as e:
        dt = time.time() - t0
        log_suppressed('ui/learning.py:export_cache_build_fail', exc=Exception(f'fail reason={reason} dur={dt:.3f}s err={e!r}'), level=logging.ERROR)
        return False

# =============================================================================
# END BLOCK: LEARNING_EXPORT_BUILD_BODY
# =============================================================================

# =============================================================================
# BLOCK: ENERGY_TOP_LABEL_HYDRATION (SWAPPABLE)
# Pfad: /opt/ai/oroma/ui/learning.py
# Zweck: Top-N Nodes/Relations aus stats.db Cache + Labels aus oroma.db
# =============================================================================
def _hydrate_node_labels_from_main(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ergänzt bei Energy-Node-Items (node_id) das Label/Kind aus oroma.db.object_nodes.

    Erwartete Item-Form (energy_manager.py):
      { "node_id": int, "energy": float, "count": int }

    Ergebnis:
      { "node_id":..., "energy":..., "count":..., "label": "...", "kind": "..." }
    """
    if not items:
        return items

    ids: List[int] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("label") or it.get("name"):
            continue
        nid = _safe_int(it.get("node_id"), 0)
        if nid > 0:
            ids.append(nid)

    if not ids:
        return items

    ids = list(dict.fromkeys(ids))[:300]

    try:
        conn = _get_main_conn_readonly()
    except Exception:
        return items

    try:
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"""
            SELECT id, label, kind
              FROM object_nodes
             WHERE id IN ({placeholders})
            """,
            tuple(ids),
        ).fetchall()

        m = {
            _safe_int(r["id"]): {
                "label": (r["label"] or ""),
                "kind": (r["kind"] or ""),
            }
            for r in rows
        }

        for it in items:
            if not isinstance(it, dict):
                continue
            nid = _safe_int(it.get("node_id"), 0)
            if nid > 0 and nid in m:
                lab = (m[nid].get("label") or "").strip()
                it["label"] = lab or it.get("label") or ""
                it["kind"] = (m[nid].get("kind") or "").strip()

        return items
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:1251', exc=e, level=logging.WARNING)
            pass


def _hydrate_relation_labels_from_main(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ergänzt bei Energy-Relation-Items (src_id/dst_id) die Labels aus oroma.db.object_nodes
    und setzt ein Display-Feld (name), damit learning.html nicht "?" zeigt.

    Erwartete Item-Form (energy_manager.py):
      { "relation_id": int, "src_id": int, "dst_id": int, "rel_type": str, "energy": float, "count": int }

    Ergebnis (zusätzlich):
      - src_label, dst_label
      - name: "<src_label> -<rel_type>-> <dst_label>"  (oder Fallback "<src_id> -> <dst_id>")
    """
    if not items:
        return items

    need_ids: List[int] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = _safe_int(it.get("src_id"), 0)
        did = _safe_int(it.get("dst_id"), 0)
        if sid > 0:
            need_ids.append(sid)
        if did > 0:
            need_ids.append(did)

    if not need_ids:
        # Mindestens einen name-Fallback setzen
        for it in items:
            if isinstance(it, dict) and not (it.get("name") or it.get("label")):
                sid = _safe_int(it.get("src_id"), 0)
                did = _safe_int(it.get("dst_id"), 0)
                it["name"] = f"{sid} -> {did}" if sid or did else (it.get("rel_type") or it.get("relation") or "?")
        return items

    need_ids = list(dict.fromkeys(need_ids))[:600]

    try:
        conn = _get_main_conn_readonly()
    except Exception:
        # Fallback ohne DB
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("name") or it.get("label"):
                continue
            sid = _safe_int(it.get("src_id"), 0)
            did = _safe_int(it.get("dst_id"), 0)
            rt = (it.get("rel_type") or it.get("relation") or "").strip()
            if rt:
                it["name"] = f"{sid} -{rt}-> {did}"
            else:
                it["name"] = f"{sid} -> {did}" if sid or did else "?"
        return items

    try:
        placeholders = ",".join(["?"] * len(need_ids))
        rows = conn.execute(
            f"""
            SELECT id, label, kind
              FROM object_nodes
             WHERE id IN ({placeholders})
            """,
            tuple(need_ids),
        ).fetchall()

        m = {
            _safe_int(r["id"]): {
                "label": (r["label"] or "").strip(),
                "kind": (r["kind"] or "").strip(),
            }
            for r in rows
        }

        for it in items:
            if not isinstance(it, dict):
                continue

            sid = _safe_int(it.get("src_id"), 0)
            did = _safe_int(it.get("dst_id"), 0)

            s_lab = m.get(sid, {}).get("label", "")
            d_lab = m.get(did, {}).get("label", "")

            it["src_label"] = s_lab
            it["dst_label"] = d_lab

            # Display: rel_type kann in manchen DBs leer sein → fallback "rel"
            rt = (it.get("rel_type") or it.get("relation") or "").strip()
            rt_disp = rt if rt else "rel"

            left = s_lab if s_lab else (f"id:{sid}" if sid else "id:?")
            right = d_lab if d_lab else (f"id:{did}" if did else "id:?")

            # WICHTIG: learning.html nutzt bevorzugt label/name → wir setzen name
            it["name"] = f"{left} -{rt_disp}-> {right}"

        return items
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed('ui/learning.py:1357', exc=e, level=logging.WARNING)
            pass
# =============================================================================
# END BLOCK: ENERGY_TOP_LABEL_HYDRATION
# =============================================================================

@learning_bp.get("/api/energy/top")
def api_energy_top():
    """
    Liefert Top-N energiereiche Objects/Relations.

    Producer (typisch):
      tools/energy_manager.py schreibt in stats.db.energy_top_cache:
        - kind='nodes'     (Objects / Nodes)
        - kind='relations' (Relations)
      payload_json ist jeweils eine JSON-Liste.

    In manchen Installationen (Legacy / Experimente) existieren zusätzlich:
        - kind='objects_top'
        - kind='relations_top'
    Die enthalten oft *bessere Labels* (z.B. relation='meta_to_chain'), während
    kind='relations' im "no new relations"-Fall leere rel_type/IDs enthalten kann.
    (Dann zeigt die UI nur '?' an.)

    Daher:
      - wir lesen mehrere Cache-Kinds
      - bevorzugen die "gelabelte" Variante
      - wenn alles unbrauchbar → schneller Fallback aus oroma.db (count-basiert)
    """
    try:
        limit = int(request.args.get("limit", str(ENERGY_TOP_LIMIT)))
    except Exception:
        limit = ENERGY_TOP_LIMIT
    limit = max(5, min(50, int(limit)))

    # UI-Filter (Default: aktiv)
    # raw=1 → ungefilterte Anzeige (Debug / Diagnose)
    try:
        raw = int(request.args.get("raw", "0")) == 1
    except Exception:
        raw = False

    try:
        max_age = int(request.args.get("max_age_sec", str(ENERGY_CACHE_MAX_AGE_SEC)))
    except Exception:
        max_age = ENERGY_CACHE_MAX_AGE_SEC
    max_age = max(30, min(86400, int(max_age)))

    now = _now()

    def _load_kind(conn: sqlite3.Connection, kind: str) -> Tuple[int, List[Dict[str, Any]]]:
        row = conn.execute(
            "SELECT ts, payload_json FROM energy_top_cache WHERE kind=?",
            (kind,),
        ).fetchone()
        if not row:
            return 0, []
        ts = _safe_int(row["ts"])
        try:
            raw = json.loads(row["payload_json"] or "[]")
            if isinstance(raw, list):
                return ts, [x for x in raw if isinstance(x, dict)]
        except Exception as e:
            log_suppressed('ui/learning.py:1418', exc=e, level=logging.WARNING)
            pass
        return ts, []

    def _has_relation_labels(items: List[Dict[str, Any]]) -> bool:
        for it in items or []:
            s = str(it.get("relation") or it.get("rel_type") or it.get("name") or it.get("label") or "").strip()
            if s and s != "?":
                return True
        return False

    objects: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    obj_ts = 0
    rel_ts = 0
    used_kind_objects: Optional[str] = None
    used_kind_relations: Optional[str] = None

    try:
        conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=1.0)

        # --- Load candidates (some systems have multiple kinds)
        ts_nodes, nodes = _load_kind(conn, "nodes")
        ts_objects_top, objects_top = _load_kind(conn, "objects_top")
        ts_objects, objects_legacy = _load_kind(conn, "objects")

        ts_rel_top, rel_top = _load_kind(conn, "relations_top")
        ts_rel, rel_std = _load_kind(conn, "relations")

        conn.close()

        # --- Choose objects payload (prefer nodes, then objects_top, then objects)
        if nodes:
            objects = nodes
            obj_ts = ts_nodes
            used_kind_objects = "nodes"
        elif objects_top:
            objects = objects_top
            obj_ts = ts_objects_top
            used_kind_objects = "objects_top"
        elif objects_legacy:
            objects = objects_legacy
            obj_ts = ts_objects
            used_kind_objects = "objects"

        # --- Choose relations payload:
        # Prefer the one with labels; if both have labels, choose fresher.
        rel_top_labeled = _has_relation_labels(rel_top)
        rel_std_labeled = _has_relation_labels(rel_std)

        if rel_top_labeled and rel_std_labeled:
            if ts_rel_top >= ts_rel:
                relations, rel_ts, used_kind_relations = rel_top, ts_rel_top, "relations_top"
            else:
                relations, rel_ts, used_kind_relations = rel_std, ts_rel, "relations"
        elif rel_top_labeled:
            relations, rel_ts, used_kind_relations = rel_top, ts_rel_top, "relations_top"
        elif rel_std_labeled:
            relations, rel_ts, used_kind_relations = rel_std, ts_rel, "relations"
        else:
            relations, rel_ts, used_kind_relations = rel_std, ts_rel, "relations"

    except Exception as e:
        return jsonify(
            {
                "ok": False,
                "ts": now,
                "objects": [],
                "relations": [],
                "meta": {"error": str(e)},
            }
        )

    # Hydratisiere Node/Relation-Labels (damit UI nicht "?" zeigt)
    meta_errors: List[str] = []

    try:
        objects = _hydrate_node_labels_from_main(objects)
    except Exception as e:
        meta_errors.append(f"hydrate_nodes_failed:{e}")
        log_suppressed('ui/learning.py:api_energy_top_hydrate_nodes', exc=e, level=logging.WARNING)

    try:
        relations = _hydrate_relation_labels_from_main(relations)
    except Exception as e:
        meta_errors.append(f"hydrate_relations_failed:{e}")
        log_suppressed('ui/learning.py:api_energy_top_hydrate_relations', exc=e, level=logging.WARNING)

    # Wenn Cache komplett leer oder Cache-Lese-Fehler: schneller Fallback aus oroma.db.
    if (not objects) and (not relations):
        try:
            fb_all = _energy_fallback_from_main(limit=limit, window_days=7)
            if isinstance(fb_all, dict) and fb_all.get("ok"):
                objects = fb_all.get("objects", []) or []
                relations = fb_all.get("relations", []) or []
                used_kind_objects = used_kind_objects or "fallback_main_db"
                used_kind_relations = used_kind_relations or "fallback_main_db"
                obj_ts = now
                rel_ts = now
                meta_errors.append("energy_cache_empty_used_main_fallback")
        except Exception as e:
            meta_errors.append(f"fallback_main_failed:{e}")
            log_suppressed('ui/learning.py:api_energy_top_fallback_main', exc=e, level=logging.WARNING)

    # Relations "?" Fix:
    # Wenn Cache-Relations keine Labels haben → fallback aus oroma.db (count-basiert).
    if not _has_relation_labels(relations):
        fb = _energy_fallback_from_main(limit=limit, window_days=7)
        if fb.get("ok"):
            relations = fb.get("relations", []) or []
            used_kind_relations = "fallback_main_db"
            rel_ts = now

    # ---------------------------------------------------------------------
    # Display-only Relation-Weighting (UI-Fix, sofort wirksam)
    # ---------------------------------------------------------------------
    # Hintergrund:
    # - tools/energy_manager.py wendet Relation-Gewichte an (RELATION_WEIGHTS_DEFAULT),
    #   aber bestehende relation_energy Einträge werden ggf. über mehrere Timer-Runs
    #   per Retro-Migration umgewichtet (energy_state.weights_rel_applied == 0).
    # - Während dieser Phase wirken technische Relations „gleich wichtig“ (z.B. 4.0),
    #   obwohl sie Infrastruktur sind (meta_to_chain/chain_to_origin).
    #
    # Lösung:
    # - Wenn weights_rel_applied==0, skalieren wir NUR für die Anzeige die Relation-
    #   Energie mit denselben Kern-Gewichten. Keine DB-Schreibzugriffe, kein Lock-Risiko.
    weights_rel_applied = True
    try:
        tmp = _db_connect(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=2.0)
        try:
            r = tmp.execute("SELECT weights_rel_applied FROM energy_state WHERE id=1").fetchone()
            if r is not None:
                weights_rel_applied = bool(int(r[0] or 0))
        finally:
            tmp.close()
    except Exception:
        # Wenn wir den Status nicht lesen können, lieber NICHT doppelt gewichten.
        weights_rel_applied = True

    if not weights_rel_applied:
        rel_w = {
            "chain_to_origin": 0.05,
            "origin_to_chain": 0.05,
            "meta_to_chain": 0.10,
            "chain_to_meta": 0.10,
            "episode_to_chain": 0.05,
            "chain_to_episode": 0.05,
        }
        for it in relations:
            rt = str(it.get("rel_type") or it.get("type") or "").strip()
            w = float(rel_w.get(rt, 1.0))
            if w != 1.0:
                try:
                    it["energy"] = round(float(it.get("energy", 0.0)) * w, 6)
                except Exception:
                    pass

# Optional: UI-Filter (Standard) um Infrastruktur-Hubs nicht die Energy-Story
    # dominieren zu lassen. Das ist *nur* eine Präsentations-Schicht – die Energy-
    # Daten bleiben unverändert in stats.db.
    if not raw:
        TECH_LABELS_OBJ = {
            "vision/token",
            "link/calc_vision",
            "calc/result",
            "scenegraph:vision_token:niedrig",
            "scenegraph:vision_token:hoch",
        }
        # Für Relations erlauben wir explizit scenegraph:vision_token:* (weil das aktuell
        # oft die einzige semantische Kante ist: ... -describes-> Chain ...).
        TECH_LABELS_REL = {
            "vision/token",
            "link/calc_vision",
            "calc/result",
        }
        # NOTE:
        #   Für Objects ist 'scenegraph:' meist Infrastruktur/Meta (dominiert Energy).
        #   Für Relations sind 'scenegraph:* -describes-> Chain ...' jedoch aktuell
        #   oft die *einzigen* vorhandenen semantischen Kanten. Wenn wir 'scenegraph:'
        #   auch bei Relations filtern, wirkt "Top Relations" leer.
        #   Daher trennen wir Object-vs-Relation-Tech-Heuristik.
        TECH_LABEL_PREFIXES_OBJ = ("scenegraph:",)
        TECH_LABEL_PREFIXES_REL = tuple()  # bewusst leer: Relations dürfen scenegraph:* enthalten
        TECH_REL_TYPES = {
            "meta_to_chain",
            "chain_to_origin",
            "origin",
            "episode_to_chain",
            "chain_to_episode",
        }

        def _is_tech_label(label: str, prefixes: tuple, labels_set: set) -> bool:
            lab = (label or "").strip()
            if not lab:
                return False
            if lab in labels_set:
                return True
            for pfx in prefixes:
                if lab.startswith(pfx):
                    return True
            return False

        def _filter_objects(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for it in items or []:
                lab = str(it.get("label") or it.get("name") or "")
                if _is_tech_label(lab, TECH_LABEL_PREFIXES_OBJ, TECH_LABELS_OBJ):
                    continue
                out.append(it)
            return out

        def _filter_relations(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for it in items or []:
                rt = str(it.get("rel_type") or it.get("relation") or "").strip()
                if rt in TECH_REL_TYPES:
                    continue
                s_lab = str(it.get("src_label") or "")
                d_lab = str(it.get("dst_label") or "")
                if _is_tech_label(s_lab, TECH_LABEL_PREFIXES_REL, TECH_LABELS_REL) or _is_tech_label(d_lab, TECH_LABEL_PREFIXES_REL, TECH_LABELS_REL):
                    continue
                out.append(it)
            return out

        objects = _filter_objects(objects)
        relations = _filter_relations(relations)

    # Slicing
    objects = (objects or [])[:limit]
    relations = (relations or [])[:limit]

    obj_age = (now - obj_ts) if obj_ts else None
    rel_age = (now - rel_ts) if rel_ts else None

    data_ok = bool(objects or relations)

    # Freshness separat vom "data_ok" behandeln:
    # - data_ok signalisiert: es gibt Daten (auch wenn stale)
    # - cache_fresh signalisiert: Cache ist innerhalb max_age
    cache_fresh_objects = (obj_age is not None and obj_age <= max_age)
    cache_fresh_relations = (rel_age is not None and rel_age <= max_age)
    cache_fresh = bool(cache_fresh_objects or cache_fresh_relations)

    if data_ok and (not cache_fresh):
        meta_errors.append("energy_cache_stale")

    return jsonify(
        {
            "ok": data_ok,
            "ts": now,
            "objects": objects,
            "relations": relations,
            "meta": {
                "kinds_meta": {
                    "objects": {
                        "kind": used_kind_objects,
                        "ts": int(obj_ts) if obj_ts else 0,
                        "ts_local": (datetime.datetime.fromtimestamp(obj_ts).strftime("%Y-%m-%d %H:%M:%S") if obj_ts else ""),
                        "age_sec": int(obj_age) if obj_age is not None else None,
                        "stale": bool(obj_age is not None and obj_age > max_age),
                    },
                    "relations": {
                        "kind": used_kind_relations,
                        "ts": int(rel_ts) if rel_ts else 0,
                        "ts_local": (datetime.datetime.fromtimestamp(rel_ts).strftime("%Y-%m-%d %H:%M:%S") if rel_ts else ""),
                        "age_sec": int(rel_age) if rel_age is not None else None,
                        "stale": bool(rel_age is not None and rel_age > max_age),
                    },
                },
                "objects_age_sec": obj_age,
                "relations_age_sec": rel_age,
                "limit": limit,
                "max_age_sec": max_age,
                "cache_kind_objects": used_kind_objects,
                "cache_kind_relations": used_kind_relations,
                "cache_fresh": cache_fresh,
                "cache_fresh_objects": cache_fresh_objects,
                "cache_fresh_relations": cache_fresh_relations,
                "errors": meta_errors,
            },
        }
    )

# =============================================================================
# END BLOCK: API_ENERGY_TOP
# =============================================================================

# =============================================================================
# AUTOSTART: LEARNING BG WORKER (Maxima + Alles-Export)
# =============================================================================
#
# Ziel
# ----
# Der Learning-Background-Worker erzeugt periodisch:
#   • Maxima-Cache (stats_points Aggregationen)
#   • "CSV (Alles-Export)" Cache-Datei (teuer bei grossen Tabellen)
#
# Motivation
# ----------
# 1) Safari (insb. iOS) bricht lange Fetch-Requests gelegentlich ab
#    (AbortError). Wenn die Daten bereits gecached sind, ist der UI-Download
#    sofort und robust.
# 2) Nach Service-Neustart soll das System selbststaendig wieder in einen
#    "ready" Zustand kommen, ohne dass die UI manuell zuerst einen Force-
#    Build ausloest.
#
# Steuerung
# ---------
# - OROMA_LEARNING_BG_AUTOSTART=1 (default) aktiv
# - OROMA_LEARNING_BG_INTERVAL_SEC (default 7200)
#
# Logging
# -------
# Keine stillen Rueckfaelle: Fehler werden ueber log_suppressed() geloggt.
#
try:
    if OROMA_LEARNING_BG_AUTOSTART:
        _start_learning_bg_worker(reason="autostart")
except Exception as e:
    log_suppressed('ui/learning.py:bg_autostart_fail', exc=e, level=logging.WARNING)

if __name__ == "__main__":
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(learning_bp)
    app.run(host="0.0.0.0", port=5001, debug=True)

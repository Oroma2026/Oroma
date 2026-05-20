#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/stats_snapshot.py
# Projekt:   ORÓMA (Telemetry Snapshot · stats.db Collector · UI-Resilience)
# Modul:     StatsSnapshot – inkrementelles Spiegeln von oroma.db Zeitreihen nach stats.db + Curve-Aggregate + Stale-Repair
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Tool spiegelt ausgewählte Telemetrie-Zeitreihen aus der produktiven Haupt-DB
# (oroma.db) in eine separate, UI-freundliche Stats-DB (stats.db).
#
# WICHTIGER BETRIEBSHINWEIS (Cutover 2026-04-08)
# ────────────────────────────────────────────────
# - tools/stats_event_aggregator.py ist der regulaere Fast-Path fuer den
#   laufenden Betrieb und haeufige Aktualisierung.
# - Diese Datei (tools/stats_snapshot.py) ist bewusst nur noch der
#   Repair-/Audit-/Rebuild-Pfad.
# - Sie darf produktiv weiterhin manuell oder selten per Timer laufen,
#   soll aber NICHT mehr als enger Hochfrequenz-Job im Orchestrator laufen.
#
# Warum?
# - oroma.db kann im Orchestrator-Betrieb kurzzeitig „busy/locked“ sein (viele Writer).
# - Die Learning-UI (/learning) soll dennoch schnell laden und Kurven zeichnen können.
# - stats.db ist daher ein „Read-Optimized Cache“: klein, indiziert, stabil.
#
# WAS GENAU GESPIEGELT WIRD (QUELLTABELLEN)
# ─────────────────────────────────────────
# Aus oroma.db (Quelle) werden inkrementell gelesen:
#   - rewards_log   : Rewards pro Quelle (curriculum, speech, ...)
#   - metrics       : generische Key/Value Zeitreihe (key, ts, value)
#   - empathy_snaps : Empathie/Score Ereignisse (ts, score, ...)
#   - coverage_log  : Coverage Zeitreihe (ts, coverage, ...)
#
# Zusätzlich (nur für Kurven-Aggregate):
#   - snapchains    : daily Aggregation → stats_curve_day (chains, sum_quality, q_max)
#
# ZIELSCHEMA (stats.db)
# ─────────────────────
# stats.db wird (idempotent) mit folgenden Tabellen betrieben:
#
# 1) stats_meta
#   - (k TEXT PRIMARY KEY, v TEXT)
#   - speichert Checkpoints/State für inkrementelle Läufe
#     z. B. ck:rewards_log, ck:metrics, ck:coverage, ck:empathy, stale_repair_ts, ...
#
# 2) stats_points
#   - (ts INTEGER, series TEXT, value REAL, src_table TEXT, src_id INTEGER, src_uid TEXT, meta TEXT)
#   - zentrale Zeitreihen-Tabelle (UI liest nur hieraus)
#   - wichtige Eigenschaft:
#       UNIQUE Index über (src_table, src_uid, series)
#     → INSERT OR IGNORE verhindert Duplikate (idempotent, sichere Wiederholung)
#
# 3) stats_curve_day
#   - (day TEXT PRIMARY KEY, chains INTEGER, sum_quality REAL, q_max REAL, updated_ts INTEGER)
#   - schnelle Tageskurve, damit UI nicht jedes Mal snapchains scannen muss
#
# INKREMENTELLER SNAPSHOT (KERNMECHANIK)
# ──────────────────────────────────────
# Das Tool arbeitet checkpoint-basiert:
#   - Für Tabellen mit id/created_at: checkpoint ist MAX(id) (oder MAX(created_at))
#   - Für metrics ohne id: src_uid wird aus sha1(ts|key|value|meta) gebildet
#
# Ablauf pro „Serie“:
#   1) checkpoint aus stats_meta lesen
#   2) nur neue Rows aus oroma.db holen (LIMIT BATCH)
#   3) in stats_points schreiben (INSERT OR IGNORE)
#   4) checkpoint in stats_meta aktualisieren
#
# Ziel:
# - kein Vollscan im Normalbetrieb
# - wiederholbar ohne Duplikate
# - schnell auf Raspberry Pi
#
# STALE-REPAIR (WICHTIGER REAL-WORLD FIX)
# ───────────────────────────────────────
# Problemklasse:
# - Nach Restore / Sampling-ZIP / Truncate kann stats_meta „fertig“ signalisieren,
#   aber stats_points enthält veraltete TS (z. B. coverage/empathy erscheinen im UI als 0).
#
# Lösung in diesem Tool:
# - _stale_repair_needed() prüft „Drift“ zwischen oroma.db und stats.db
# - bei Bedarf wird ein TS-basiertes Backfill ab MAX(ts in stats_points) ausgelöst
# - rate-limited über OROMA_STATS_STALE_REPAIR_SEC (Default: 3600s)
#
# PERFORMANCE- UND SICHERHEITSPRINZIPIEN
# ──────────────────────────────────────
# - oroma.db wird read-only geöffnet (kurze Abfragen, TIMEOUT-guarded)
# - stats.db wird in kurzen Transaktionen beschrieben
# - BATCH-Limit verhindert lange Locks/IO
# - idempotent: mehrfaches Ausführen ist erwünscht und sicher
#
# WICHTIGE ENV-VARIABLEN (DIESE DATEI VERWENDET SIE TATSÄCHLICH)
# ─────────────────────────────────────────────────────────────
# DB-Pfade:
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_STATS_DB_PATH=/opt/ai/oroma/data/stats.db
#
# Batch/Timeout:
#   OROMA_STATS_BATCH=2000
#   OROMA_STATS_TIMEOUT_SEC=1.2
#
# Kurven:
#   OROMA_STATS_CURVE_DAYS=180
#
# Keys/Serien (Metrics):
#   OROMA_STATS_METRICS_KEYS="reward_curriculum,reward_speech,..."
#
# Stale-Repair:
#   OROMA_STATS_STALE_REPAIR_SEC=3600
#
# ÖFFENTLICHE ENTRYPOINTS
# ───────────────────────
# ensure_stats_schema(sconn)           → erstellt stats.db Schema idempotent
# snapshot_rewards/snapshot_metrics/
# snapshot_empathy/snapshot_coverage  → inkrementelle Spiegelung
# refresh_curve_day(...)              → daily Aggregation aus snapchains
# run_once()                          → kompletter Durchlauf (alle Serien + curve)
#
# CLI / SYSTEMD
# ─────────────
# Manuell:
#   PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/tools/stats_snapshot.py --once
#
# Systemd/Orchestrator:
#   - wird typischerweise periodisch ausgeführt (Timer oder Orchestrator Job)
#   - designed für häufige Runs (z. B. alle 1–5 Minuten) ohne Duplikate
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Idempotenz ist Pflicht (INSERT OR IGNORE + src_uid).
# - Kein Vollscan im Normalbetrieb (Checkpoints müssen erhalten bleiben).
# - Stale-Repair ist bewusst enthalten (real world Restore/Sampling-Fälle).
# - Muss headless und dependency-arm bleiben (stdlib sqlite3 + json + hashlib).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional
import logging
from core.log_guard import log_suppressed


# -----------------------------------------------------------------------------
# ENV
# -----------------------------------------------------------------------------
OROMA_DB_PATH = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
OROMA_STATS_DB_PATH = os.environ.get("OROMA_STATS_DB_PATH", "/opt/ai/oroma/data/stats.db")

BATCH = int(os.environ.get("OROMA_STATS_BATCH", "2000"))
CURVE_DAYS = int(os.environ.get("OROMA_STATS_CURVE_DAYS", "180"))
TIMEOUT_SEC = float(os.environ.get("OROMA_STATS_TIMEOUT_SEC", "1.2"))
# Curve-Refresh-Throttle (2026-04):
# Die Tagesaggregation ueber snapchains ist der teuerste Teil dieses Tools, weil
# sie auch im inkrementellen Normalfall einen groesseren Bereich ueberfliegen
# muss. Im Orchestrator fuehrte das wiederholt zu 60s-Timeouts, obwohl die
# eigentlichen stats_points-Snapshots klein/inkrementell bleiben.
#
# Strategie:
# - Curve nur dann neu berechnen, wenn seit dem letzten Refresh genug Zeit
#   vergangen ist ODER neue snapchains hinzugekommen sind.
# - Default bewusst konservativ (15 Minuten), damit die Learning-UI aktuell
#   bleibt, ohne bei jedem Orchestrator-Tick die Snapchain-Aggregation erneut
#   anzustoßen.
CURVE_REFRESH_MIN_SEC = int(os.environ.get("OROMA_STATS_CURVE_REFRESH_MIN_SEC", "900") or "900")
CURVE_FULL_REBUILD_SEC = int(os.environ.get("OROMA_STATS_CURVE_FULL_REBUILD_SEC", "86400") or "86400")

# Stale-Repair (2026-01):
# Wenn stats_meta-Checkpoint auf "fertig" steht, stats_points aber alte TS hat (z.B. nach Restore,
# Sampling-ZIP oder versehentlichem Löschen/Trunkierung von stats_points), dann würden Empathy/Coverage
# im UI als 0 erscheinen. Mit Stale-Repair machen wir ein TS-basiertes Backfill ab MAX(ts) in stats_points.
# Rate-Limit: nicht öfter als alle N Sekunden versuchen.
STALE_REPAIR_SEC = int(os.environ.get("OROMA_STATS_STALE_REPAIR_SEC", "3600") or "3600")

DEFAULT_METRICS_KEYS = (
    "reward_curriculum,reward_speech,reward_empathy,"
    "cam:token:candidate,cam:token:skip_quality,self_rec_score,agent_heartbeat"
)
METRICS_KEYS_ENV = os.environ.get("OROMA_STATS_METRICS_KEYS", DEFAULT_METRICS_KEYS)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _now() -> int:
    return int(time.time())


def _safe_int(x: Any, d: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return d


def _safe_float(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return d


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


def _clamp_ck_id(mconn: sqlite3.Connection, sconn: sqlite3.Connection, ck_key: str, table: str) -> int:
    """
    Schutz gegen "Geister-Checkpoint" nach Restore/Backup/Sampling.

    Problem:
      Wenn stats_meta(ck_..._last_id) *höher* ist als der aktuelle MAX(id) in der
      Haupt-DB, dann würde der Collector auf "id > ck" warten – was bei SQLite
      niemals erreicht wird, solange neue IDs wieder bei MAX(id)+1 starten.

    Lösung:
      Wir clampen ck zurück auf MAX(id). Dadurch werden ab dem nächsten Insert
      wieder neue Rows erfasst, ohne Voll-Rescan.

    Sicherheit:
      - mconn ist read-only.
      - MAX(id) ist O(1) mit INTEGER PRIMARY KEY.
      - Falls Tabelle leer/unavailable: clampen wir nicht.
    """
    last_id = _safe_int(_meta_get(sconn, ck_key, "0"), 0)
    try:
        row = mconn.execute(f"SELECT MAX(id) AS mx FROM {table}").fetchone()
        mx = _safe_int(row["mx"] if row else 0, 0)
    except Exception:
        return last_id

    if mx > 0 and last_id > mx:
        _meta_set(sconn, ck_key, str(mx))
        return mx
    return last_id


def _connect_sqlite(path: str, readonly: bool, timeout_sec: float) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
    else:
        conn = sqlite3.connect(path, timeout=timeout_sec)

    conn.row_factory = sqlite3.Row
    # -------------------------------------------------------------------------
    # Busy-Timeout (WAL-Contention)
    # -------------------------------------------------------------------------
    # stats.db wird regelmäßig aktualisiert (Orchestrator + ggf. manuelle Tools).
    # Bei parallelen Zugriffen kann SQLite kurzzeitig "database is locked" liefern.
    # Ein zu kleiner busy_timeout (z.B. 800ms) führt dann zu häufigen Fehlzyklen,
    # wodurch Learning/Charts scheinbar "stehen bleiben".
    #
    # Strategie:
    #   - Default bleibt kompatibel (800ms),
    #   - kann aber über ENV erhöht werden.
    #
    # ENV:
    #   OROMA_STATS_DB_BUSY_TIMEOUT_MS (priorisiert)
    #   OROMA_DB_BUSY_TIMEOUT_MS       (Fallback)
    try:
        bt = int(os.getenv("OROMA_STATS_DB_BUSY_TIMEOUT_MS", os.getenv("OROMA_DB_BUSY_TIMEOUT_MS", "800")) or "800")
    except Exception:
        bt = 800
    bt = max(0, min(120000, bt))
    conn.execute(f"PRAGMA busy_timeout = {bt}")
    return conn


def _get_main_conn_ro() -> sqlite3.Connection:
    return _connect_sqlite(OROMA_DB_PATH, readonly=True, timeout_sec=TIMEOUT_SEC)


def _get_stats_conn_rw() -> sqlite3.Connection:
    conn = _connect_sqlite(OROMA_STATS_DB_PATH, readonly=False, timeout_sec=max(1.0, TIMEOUT_SEC))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == col for r in rows)
    except Exception:
        return False


# -----------------------------------------------------------------------------
# stats.db schema
# -----------------------------------------------------------------------------
def ensure_stats_schema(sconn: sqlite3.Connection) -> None:
    # meta
    sconn.execute("CREATE TABLE IF NOT EXISTS stats_meta (k TEXT PRIMARY KEY, v TEXT)")

    # points (learning.py kompatibel)
    sconn.execute(
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

    # migration: add missing cols (legacy)
    if not _has_col(sconn, "stats_points", "src_table"):
        sconn.execute("ALTER TABLE stats_points ADD COLUMN src_table TEXT NOT NULL DEFAULT 'legacy'")
    if not _has_col(sconn, "stats_points", "src_id"):
        sconn.execute("ALTER TABLE stats_points ADD COLUMN src_id INTEGER NOT NULL DEFAULT 0")
    if not _has_col(sconn, "stats_points", "src_uid"):
        sconn.execute("ALTER TABLE stats_points ADD COLUMN src_uid TEXT NOT NULL DEFAULT ''")
    if not _has_col(sconn, "stats_points", "meta"):
        sconn.execute("ALTER TABLE stats_points ADD COLUMN meta TEXT NULL")

    sconn.execute("CREATE INDEX IF NOT EXISTS idx_stats_points_series_ts ON stats_points(series, ts)")

    # best-effort: legacy src_uid fill so UNIQUE index doesn't explode
    mig_key = "migrated_stats_points_uid_v2"
    row = sconn.execute("SELECT v FROM stats_meta WHERE k=?", (mig_key,)).fetchone()
    if not row:
        try:
            sconn.execute("UPDATE stats_points SET src_table='legacy' WHERE src_table IS NULL OR src_table=''")
            rows = sconn.execute(
                """
                SELECT rowid, ts, series, value, COALESCE(meta,'') AS meta
                  FROM stats_points
                 WHERE src_uid IS NULL OR src_uid=''
                """
            ).fetchall()
            for r in rows:
                rid = _safe_int(r["rowid"])
                uid = _sha1(f"{_safe_int(r['ts'])}|{r['series']}|{_safe_float(r['value'])}|{r['meta']}|{rid}")
                sconn.execute("UPDATE stats_points SET src_uid=? WHERE rowid=?", (uid, rid))
            sconn.execute("INSERT INTO stats_meta(k,v) VALUES(?,?)", (mig_key, str(_now())))
        except Exception as e:
            log_suppressed('tools/stats_snapshot.py:248', exc=e, level=logging.WARNING)
            pass

    # UNIQUE index (best effort; don't crash if legacy data is messy)
    try:
        sconn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_stats_points_src ON stats_points(src_table, src_uid, series)"
        )
    except Exception as e:
        log_suppressed('tools/stats_snapshot.py:257', exc=e, level=logging.WARNING)
        pass

    # curve table (optional but recommended)
    sconn.execute(
        """
        CREATE TABLE IF NOT EXISTS stats_curve_day (
          day        TEXT PRIMARY KEY,         -- YYYY-MM-DD
          chains     INTEGER NOT NULL,
          sum_quality REAL NOT NULL,
          q_max      REAL NOT NULL,
          updated_ts INTEGER NOT NULL
        )
        """
    )

    sconn.commit()


def _meta_get(sconn: sqlite3.Connection, k: str, default: str = "") -> str:
    try:
        row = sconn.execute("SELECT v FROM stats_meta WHERE k=?", (k,)).fetchone()
        return str(row["v"]) if row else default
    except Exception:
        return default


def _meta_set(sconn: sqlite3.Connection, k: str, v: str) -> None:
    sconn.execute(
        "INSERT INTO stats_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, v),
    )





def _stats_max_ts(sconn: sqlite3.Connection, series: str, src_table: Optional[str] = None) -> int:
    """MAX(ts) aus stats_points für eine Serie (optional je Quelle)."""
    try:
        if src_table:
            row = sconn.execute(
                "SELECT MAX(ts) AS mx FROM stats_points WHERE series=? AND src_table=?",
                (str(series), str(src_table)),
            ).fetchone()
        else:
            row = sconn.execute(
                "SELECT MAX(ts) AS mx FROM stats_points WHERE series=?",
                (str(series),),
            ).fetchone()
        return _safe_int(row["mx"] if row else 0, 0)
    except Exception:
        return 0


def _main_max_ts(mconn: sqlite3.Connection, table: str) -> int:
    """MAX(ts) aus oroma.db-Quelle."""
    try:
        row = mconn.execute(f"SELECT MAX(ts) AS mx FROM {table}").fetchone()
        return _safe_int(row["mx"] if row else 0, 0)
    except Exception:
        return 0


def _should_refresh_curve_day(mconn: sqlite3.Connection, sconn: sqlite3.Connection, *, min_sec: int) -> bool:
    """Entscheidet, ob die teure snapchains->stats_curve_day Aggregation jetzt laufen soll.

    Ziel:
      - die inkrementellen Punktesnapshots weiterhin in jedem Lauf ausfuehren
      - die Kurvenaggregation dagegen drosseln, weil sie bei grossen snapchains
        Tabellen den Orchestrator sprengen kann

    Logik:
      - erster Lauf: ja
      - wenn seit letztem Lauf >= min_sec vergangen: ja
      - wenn neuere snapchains-ts als beim letzten erfolgreichen Refresh: ja
      - sonst: nein
    """
    min_sec = max(0, int(min_sec))
    now_ts = _now()
    last_run_ts = _safe_int(_meta_get(sconn, "curve_day_last_run_ts", "0"), 0)
    last_src_ts = _safe_int(_meta_get(sconn, "curve_day_last_src_ts", "0"), 0)

    try:
        row = mconn.execute("SELECT MAX(ts) AS mx FROM snapchains WHERE status IS NULL OR status != 'deleted'").fetchone()
        src_ts = _safe_int(row["mx"] if row else 0, 0)
    except Exception:
        # Wenn wir den Zustand der Snapchains nicht bestimmen koennen, lieber
        # keinen teuren Refresh forcieren. Die Punktesnapshots sollen dennoch
        # weiterlaufen koennen.
        return False

    if src_ts <= 0:
        return False
    if last_run_ts <= 0:
        return True
    if src_ts > last_src_ts:
        return True
    if (now_ts - last_run_ts) >= min_sec:
        return True
    return False


def _stale_repair_needed(
    sconn: sqlite3.Connection,
    mconn: sqlite3.Connection,
    *,
    series: str,
    src_table: str,
    main_table: str,
) -> bool:
    """Erkennt, ob eine Serie in stats.db 'eingefroren' ist und per TS backfilled werden sollte.

    Hintergrund:
      - Checkpoints in stats_meta können nach Restore/Sampling/Trunkierung 'zu weit' stehen.
      - Dann liefert der inkrementelle id-Pfad keine Rows, obwohl stats_points für die Serie alt ist.

    Kriterien:
      - Haupt-DB hat neuere ts als stats_points für diese Serie/Quelle
      - Rate-Limit: Backfill nicht häufiger als STALE_REPAIR_SEC
    """
    try:
        st_ts = _stats_max_ts(sconn, series, src_table=src_table)
        mn_ts = _main_max_ts(mconn, main_table)

        if mn_ts <= 0:
            return False
        if st_ts <= 0:
            return True
        if mn_ts <= st_ts:
            return False

        now_ts = _now()
        if (now_ts - st_ts) < max(30, int(STALE_REPAIR_SEC)):
            return False
        return True
    except Exception:
        return False

def _insert_points(sconn: sqlite3.Connection, pts: Iterable[Dict[str, Any]]) -> int:
    """Insert points idempotent.

    Live-Fix (2026-01):
      - stats.db kann nach Restore/Backup/Sampling älter oder teil-geleert sein.
      - Dann kann (src_table, src_uid, series) bereits existieren, aber ts/value/meta passen nicht mehr
        zur aktuellen Haupt-DB.

    Strategie:
      - Primär: UPSERT (ON CONFLICT .. DO UPDATE) auf ux_stats_points_src.
      - Fallback: INSERT OR IGNORE, falls UNIQUE-Constraint fehlt/defekt.
    """
    n = 0
    for p in pts:
        try:
            ts = _safe_int(p.get("ts"))
            series = str(p.get("series", ""))
            value = _safe_float(p.get("value"))
            src_table = str(p.get("src_table") or "unknown")
            src_id = _safe_int(p.get("src_id", 0))
            src_uid = str(p.get("src_uid") or "")
            meta = p.get("meta")

            if not series:
                continue
            if not src_uid:
                src_uid = _sha1(f"{ts}|{series}|{value}|{meta or ''}|{src_table}|{src_id}")

            try:
                sconn.execute(
                    """
                    INSERT INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(src_table, src_uid, series) DO UPDATE SET
                      ts=excluded.ts,
                      value=excluded.value,
                      src_id=excluded.src_id,
                      meta=excluded.meta
                    """,
                    (ts, series, value, src_table, src_id, src_uid, meta),
                )
            except sqlite3.OperationalError:
                sconn.execute(
                    """
                    INSERT OR IGNORE INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (ts, series, value, src_table, src_id, src_uid, meta),
                )
            n += 1
        except Exception as e:
            log_suppressed('tools/stats_snapshot.py:insert_points', exc=e, level=logging.WARNING)
            pass
    return n



# -----------------------------------------------------------------------------
# Snapshot steps (incremental)
# -----------------------------------------------------------------------------
def _parse_metrics_keys() -> List[str]:
    raw = (METRICS_KEYS_ENV or "").strip()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    out: List[str] = []
    seen = set()
    for k in keys:
        if k not in seen:
            out.append(k)
            seen.add(k)
    return out


def snapshot_rewards(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    last_id = _clamp_ck_id(mconn, sconn, "ck_rewards_last_id", "rewards_log")

    rows = mconn.execute(
        """
        SELECT id, created_at AS ts, source, reward
          FROM rewards_log
         WHERE id > ?
      ORDER BY id ASC
         LIMIT ?
        """,
        (last_id, int(batch)),
    ).fetchall()

    if not rows:
        return 0

    pts = []
    max_id = last_id
    for r in rows:
        rid = _safe_int(r["id"])
        max_id = max(max_id, rid)
        pts.append(
            {
                "ts": _safe_int(r["ts"]),
                "series": f"reward:{str(r['source'])}",
                "value": _safe_float(r["reward"]),
                "src_table": "rewards_log",
                "src_id": rid,
                "src_uid": str(rid),
            }
        )

    n = _insert_points(sconn, pts)
    _meta_set(sconn, "ck_rewards_last_id", str(max_id))
    return n



def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (str(name),)).fetchone()
        return bool(r)
    except Exception:
        return False


def snapshot_coverage(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    """Spiegelt Coverage Zeitreihen nach stats.db.

    Ab 2026-01 wird Coverage in zwei Varianten geführt:
      - coverage_30d   (Fenster, interpretiert im Learning-Dashboard als "Coverage")
      - coverage_total (legacy: active/total über gesamte Historie)

    Backwards compatible:
      - Wenn coverage_log_30d nicht existiert, wird coverage_30d nicht geschrieben.
      - coverage_total wird weiterhin aus coverage_log gespiegelt.

    Hinweis:
      - stats_points' src_table wird entsprechend gesetzt (coverage_log vs coverage_log_30d).
      - Seriennamen bleiben stabil, damit die UI gezielt auswählen kann.
    """
    inserted = 0

    # ------------------------------------------------------------------
    # 1) Windowed Coverage (coverage_log_30d → series=coverage_30d)
    # ------------------------------------------------------------------
    if _table_exists(mconn, 'coverage_log_30d'):
        last_id_w = _clamp_ck_id(mconn, sconn, "ck_coverage30_last_id", "coverage_log_30d")

        rows_w = mconn.execute(
            """
            SELECT id, ts, coverage
              FROM coverage_log_30d
             WHERE id > ?
          ORDER BY id ASC
             LIMIT ?
            """,
            (last_id_w, int(batch)),
        ).fetchall()

        repair_mode_w = "id"
        if not rows_w and _stale_repair_needed(
            sconn, mconn, series="coverage_30d", src_table="coverage_log_30d", main_table="coverage_log_30d"
        ):
            st_ts = _stats_max_ts(sconn, "coverage_30d", src_table="coverage_log_30d")
            rows_w = mconn.execute(
                """
                SELECT id, ts, coverage
                  FROM coverage_log_30d
                 WHERE ts > ?
              ORDER BY ts ASC
                 LIMIT ?
                """,
                (int(st_ts), int(batch)),
            ).fetchall()
            repair_mode_w = "ts"

        if rows_w:
            pts = []
            max_id = last_id_w
            max_ts = 0
            for r in rows_w:
                rid = _safe_int(r["id"]) if isinstance(r, dict) else _safe_int(r["id"])
                max_id = max(max_id, rid)
                max_ts = max(max_ts, _safe_int(r["ts"]))
                pts.append(
                    {
                        "ts": _safe_int(r["ts"]),
                        "series": "coverage_30d",
                        "value": _safe_float(r["coverage"]),
                        "src_table": "coverage_log_30d",
                        "src_id": rid,
                        "src_uid": str(rid),
                    }
                )

            inserted += _insert_points(sconn, pts)
            _meta_set(sconn, "ck_coverage30_last_id", str(max_id))
            if max_ts > 0:
                _meta_set(sconn, "ck_coverage30_last_ts", str(max_ts))
            if repair_mode_w == "ts":
                _meta_set(sconn, "ck_coverage30_last_repair_ts", str(_now()))

    # ------------------------------------------------------------------
    # 2) Total Coverage (coverage_log → series=coverage_total)
    # ------------------------------------------------------------------
    last_id = _clamp_ck_id(mconn, sconn, "ck_coverage_total_last_id", "coverage_log")

    rows = mconn.execute(
        """
        SELECT id, ts, coverage
          FROM coverage_log
         WHERE id > ?
      ORDER BY id ASC
         LIMIT ?
        """,
        (last_id, int(batch)),
    ).fetchall()

    repair_mode = "id"
    if not rows and _stale_repair_needed(
        sconn, mconn, series="coverage_total", src_table="coverage_log", main_table="coverage_log"
    ):
        st_ts = _stats_max_ts(sconn, "coverage_total", src_table="coverage_log")
        rows = mconn.execute(
            """
            SELECT id, ts, coverage
              FROM coverage_log
             WHERE ts > ?
          ORDER BY ts ASC
             LIMIT ?
            """,
            (int(st_ts), int(batch)),
        ).fetchall()
        repair_mode = "ts"

    if rows:
        pts = []
        max_id = last_id
        max_ts = 0
        for r in rows:
            rid = _safe_int(r["id"])
            max_id = max(max_id, rid)
            max_ts = max(max_ts, _safe_int(r["ts"]))
            pts.append(
                {
                    "ts": _safe_int(r["ts"]),
                    "series": "coverage_total",
                    "value": _safe_float(r["coverage"]),
                    "src_table": "coverage_log",
                    "src_id": rid,
                    "src_uid": str(rid),
                }
            )

        inserted += _insert_points(sconn, pts)
        _meta_set(sconn, "ck_coverage_total_last_id", str(max_id))
        if max_ts > 0:
            _meta_set(sconn, "ck_coverage_total_last_ts", str(max_ts))
        if repair_mode == "ts":
            _meta_set(sconn, "ck_coverage_total_last_repair_ts", str(_now()))

    return int(inserted)





def snapshot_empathy(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    last_id = _clamp_ck_id(mconn, sconn, "ck_empathy_last_id", "empathy_snaps")

    # Primär: inkrementell über id
    rows = mconn.execute(
        """
        SELECT id, ts, score, mood
          FROM empathy_snaps
         WHERE id > ?
      ORDER BY id ASC
         LIMIT ?
        """,
        (last_id, int(batch)),
    ).fetchall()

    # Sekundär: TS-Backfill, wenn Serie in stats.db "stale" ist
    repair_mode = "id"
    if not rows and _stale_repair_needed(
        sconn, mconn, series="empathy_score", src_table="empathy_snaps", main_table="empathy_snaps"
    ):
        st_ts = _stats_max_ts(sconn, "empathy_score", src_table="empathy_snaps")
        rows = mconn.execute(
            """
            SELECT id, ts, score, mood
              FROM empathy_snaps
             WHERE ts > ?
          ORDER BY ts ASC
             LIMIT ?
            """,
            (int(st_ts), int(batch)),
        ).fetchall()
        repair_mode = "ts"

    if not rows:
        return 0

    pts = []
    max_id = last_id
    max_ts = 0
    for r in rows:
        rid = _safe_int(r["id"])
        max_id = max(max_id, rid)
        max_ts = max(max_ts, _safe_int(r["ts"]))
        meta = None
        try:
            meta = json.dumps({"mood": r["mood"]}, ensure_ascii=False)
        except Exception:
            meta = None
        pts.append(
            {
                "ts": _safe_int(r["ts"]),
                "series": "empathy_score",
                "value": _safe_float(r["score"]),
                "src_table": "empathy_snaps",
                "src_id": rid,
                "src_uid": str(rid),
                "meta": meta,
            }
        )

    n = _insert_points(sconn, pts)
    _meta_set(sconn, "ck_empathy_last_id", str(max_id))
    if max_ts > 0:
        _meta_set(sconn, "ck_empathy_last_ts", str(max_ts))
    if repair_mode == "ts":
        _meta_set(sconn, "ck_empathy_last_repair_ts", str(_now()))
    return n




def snapshot_metrics(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int, keys: List[str]) -> int:
    if not keys:
        return 0

    last_ts = _safe_int(_meta_get(sconn, "ck_metrics_last_ts", "0"), 0)
    # Schutz gegen Restore/Sampling: falls ck_metrics_last_ts in der Zukunft liegt
    # (oder höher als MAX(ts) in der Haupt-DB), clampen wir zurück.
    try:
        row = mconn.execute("SELECT MAX(ts) AS mx FROM metrics").fetchone()
        mx = _safe_int(row["mx"] if row else 0, 0)
        if mx > 0 and last_ts > mx:
            _meta_set(sconn, "ck_metrics_last_ts", str(mx))
            last_ts = mx
    except Exception as e:
        log_suppressed('tools/stats_snapshot.py:471', exc=e, level=logging.WARNING)
        pass

    placeholders = ",".join(["?"] * len(keys))
    rows = mconn.execute(
        f"""
        SELECT ts, key, value
          FROM metrics
         WHERE ts > ?
           AND key IN ({placeholders})
      ORDER BY ts ASC
         LIMIT ?
        """,
        (last_ts, *keys, int(batch)),
    ).fetchall()

    if not rows:
        return 0

    pts = []
    max_ts = last_ts
    for r in rows:
        ts = _safe_int(r["ts"])
        max_ts = max(max_ts, ts)
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

    n = _insert_points(sconn, pts)
    _meta_set(sconn, "ck_metrics_last_ts", str(max_ts))
    return n


def refresh_curve_day(mconn: sqlite3.Connection, sconn: sqlite3.Connection, days: int) -> int:
    days = max(7, int(days))
    now_ts = _now()
    last_full_run_ts = _safe_int(_meta_get(sconn, "curve_day_last_run_ts", "0"), 0)
    force_full = last_full_run_ts <= 0 or (now_ts - last_full_run_ts) >= CURVE_FULL_REBUILD_SEC

    if force_full:
        row = mconn.execute("SELECT ts FROM snapchains ORDER BY ts DESC LIMIT 1").fetchone()
        latest = _safe_int(row["ts"]) if row else 0
        if not latest:
            return 0
        since = max(0, latest - days * 86400)
        daily_rows = mconn.execute(
            """
            SELECT date(ts, 'unixepoch') AS day,
                   COUNT(*)              AS chains,
                   SUM(quality)          AS sum_quality,
                   MAX(quality)          AS q_max
              FROM snapchains
             WHERE ts >= ?
               AND (status IS NULL OR status != 'deleted')
          GROUP BY day
          ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
        if not daily_rows:
            return 0
        updated = 0
        for r in daily_rows:
            day = str(r["day"])
            chains = _safe_int(r["chains"])
            sum_q = _safe_float(r["sum_quality"])
            q_max = _safe_float(r["q_max"])
            sconn.execute(
                """
                INSERT INTO stats_curve_day(day, chains, sum_quality, q_max, updated_ts)
                VALUES(?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET
                  chains=excluded.chains,
                  sum_quality=excluded.sum_quality,
                  q_max=excluded.q_max,
                  updated_ts=excluded.updated_ts
                """,
                (day, chains, sum_q, q_max, now_ts),
            )
            updated += 1
        row = mconn.execute("SELECT MAX(id) AS mx FROM snapchains").fetchone()
        mx_id = _safe_int(row["mx"] if row else 0, 0)
        if mx_id > 0:
            _meta_set(sconn, "curve_day_ck_last_id", str(mx_id))
        return updated

    last_id = _safe_int(_meta_get(sconn, "curve_day_ck_last_id", "0"), 0)
    rows = mconn.execute(
        """
        SELECT id, ts, quality
          FROM snapchains
         WHERE id > ?
           AND (status IS NULL OR status != 'deleted')
      ORDER BY id ASC
        """,
        (last_id,),
    ).fetchall()
    if not rows:
        return 0
    touched = set()
    max_id = last_id
    for r in rows:
        sc_id = _safe_int(r["id"])
        ts = _safe_int(r["ts"])
        q = _safe_float(r["quality"])
        if sc_id > max_id:
            max_id = sc_id
        if ts <= 0:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        sconn.execute(
            """
            INSERT INTO stats_curve_day(day, chains, sum_quality, q_max, updated_ts)
            VALUES(?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET
              chains=stats_curve_day.chains + 1,
              sum_quality=stats_curve_day.sum_quality + excluded.sum_quality,
              q_max=CASE WHEN excluded.q_max > stats_curve_day.q_max THEN excluded.q_max ELSE stats_curve_day.q_max END,
              updated_ts=excluded.updated_ts
            """,
            (day, 1, q, q, now_ts),
        )
        touched.add(day)
    _meta_set(sconn, "curve_day_ck_last_id", str(max_id))
    return len(touched)


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------
def run_once() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "ts": _now(),
        "inserted": 0,
        "curve_days_updated": 0,
        "errors": [],
    }

    sconn: Optional[sqlite3.Connection] = None
    mconn: Optional[sqlite3.Connection] = None
    try:
        sconn = _get_stats_conn_rw()
        ensure_stats_schema(sconn)

        mconn = _get_main_conn_ro()

        keys = _parse_metrics_keys()

        inserted = 0
        inserted += snapshot_rewards(mconn, sconn, BATCH)
        inserted += snapshot_coverage(mconn, sconn, BATCH)
        inserted += snapshot_empathy(mconn, sconn, BATCH)
        inserted += snapshot_metrics(mconn, sconn, BATCH, keys)

        curve_updated = 0
        try:
            if _should_refresh_curve_day(mconn, sconn, min_sec=CURVE_REFRESH_MIN_SEC):
                curve_updated = refresh_curve_day(mconn, sconn, CURVE_DAYS)
                _meta_set(sconn, "curve_day_last_run_ts", str(_now()))
                try:
                    row = mconn.execute("SELECT MAX(ts) AS mx FROM snapchains WHERE status IS NULL OR status != 'deleted'").fetchone()
                    mx = _safe_int(row["mx"] if row else 0, 0)
                    if mx > 0:
                        _meta_set(sconn, "curve_day_last_src_ts", str(mx))
                except Exception as e:
                    log_suppressed('tools/stats_snapshot.py:curve_last_src', exc=e, level=logging.WARNING)
            else:
                out["curve_refresh_skipped"] = True
        except Exception as e:
            out["errors"].append(f"curve_refresh_failed: {e}")

        sconn.commit()
        out["ok"] = True
        out["inserted"] = int(inserted)
        out["curve_days_updated"] = int(curve_updated)
        return out
    except Exception as e:
        out["errors"].append(str(e))
        return out
    finally:
        try:
            if mconn:
                mconn.close()
        except Exception as e:
            log_suppressed('tools/stats_snapshot.py:611', exc=e, level=logging.WARNING)
            pass
        try:
            if sconn:
                sconn.close()
        except Exception as e:
            log_suppressed('tools/stats_snapshot.py:617', exc=e, level=logging.WARNING)
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA stats.db snapshot collector")
    ap.add_argument("--once", action="store_true", help="Run one snapshot and exit")
    args = ap.parse_args()

    if args.once:
        out = run_once()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 2

    while True:
        out = run_once()
        print(json.dumps(out, ensure_ascii=False))
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/stats_event_aggregator.py
# Projekt:   ORÓMA (Telemetry Delta Queue · stats.db Read Model · Serial Aggregator)
# Modul:     StatsEventAggregator – Event-/Delta-Queue für Learning-/Stats-Kennzahlen
# Version:   v3.8.x
# Stand:     2026-04-08
# Autor:     ORÓMA · GPT-5.4 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZIEL / WARUM DIESE DATEI EXISTIERT
# ──────────────────────────────────
# Bisher wurden ORÓMA-Stats primär über periodische Snapshot-Läufe aus Rohdaten erzeugt.
# Das ist robust, aber auf großer Live-DB teuer: dieselben Tabellen werden immer wieder
# gelesen, gruppiert und in stats.db gespiegelt. Besonders bei snapchains/curve-Aggregaten
# führt das zu langen Läufen und Orchestrator-Timeouts.
#
# Diese Datei setzt bewusst die langfristigere Architekturvariante um:
#
#   1) Neue Rohdaten werden NICHT sofort komplett neu ausgewertet.
#   2) Stattdessen werden aus Rohdaten kleine, idempotente Stats-Events erzeugt.
#   3) Diese Events landen in einer Queue in stats.db.
#   4) Ein serieller Aggregator verarbeitet die Queue und hält stats.db fast live.
#
# Damit wird stats.db zu einem echten „Read Model“ / materialisierten Stats-Cache.
# Der teure Vollscan-Pfad bleibt nur noch für Audit/Rebuild (weiterhin via
# tools/stats_snapshot.py möglich), ist aber nicht mehr der Hauptpfad für den laufenden
# Betrieb.
#
# ARCHITEKTUR
# ───────────
# Quellen (read-only, oroma.db):
#   - rewards_log       → stats_points: reward:<source>
#   - coverage_log      → stats_points: coverage_total
#   - coverage_log_30d  → stats_points: coverage_30d
#   - empathy_snaps     → stats_points: empathy_score (+ mood in meta)
#   - metrics           → stats_points: metric:<key>
#   - snapchains        → stats_curve_day delta events
#
# Ziel / Read Model (stats.db):
#   - stats_points
#   - stats_curve_day
#   - stats_meta
#   - stats_event_queue   (neu)
#
# WICHTIGE BETRIEBSREGEL
# ──────────────────────
# Für managed DB-Schreibpfade wird KEIN lokaler Direktwrite-Fallback verwendet.
# Alle Writes nach stats.db laufen über den DBWriter-kompatiblen Pfad
# (core.db_writer_client, db="stats").
#
# VERARBEITUNGSPHASEN PRO LAUF
# ────────────────────────────
# A) ensure_schema
#    - stats.db Queue-/Read-Model Schema idempotent sicherstellen
#
# B) emit_deltas
#    - nur neue Rohdaten seit letztem Checkpoint lesen
#    - je Rohdatensatz ein kleines Event in stats_event_queue schreiben
#    - Producer-Checkpoint in stats_meta fortschreiben
#
# C) apply_queue
#    - pending Events seriell in stats_points / stats_curve_day anwenden
#    - Event danach als done markieren
#
# D) optional: queue backlog / health sichtbar machen
#
# IDEMPOTENZ / SICHERHEIT
# ───────────────────────
# - Jedes Event hat event_uid UNIQUE.
# - Wiederholte Emission ist deshalb unkritisch.
# - Der Aggregator arbeitet seriell in kleinen Batches.
# - Quellen werden read-only geöffnet.
# - stats.db Writes laufen über DBWriter transaction/executemany.
#
# ENV
# ───
# OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
# OROMA_STATS_DB_PATH=/opt/ai/oroma/data/stats.db
# OROMA_STATS_BATCH=2000
# OROMA_STATS_EVENT_BATCH=2000
# OROMA_STATS_APPLY_BATCH=1000
# OROMA_STATS_EMIT_MAX_PER_RUN=10000
# OROMA_STATS_PENDING_LOW_WATER=5000
# OROMA_STATS_METRICS_KEYS=reward_curriculum,reward_speech,...
# OROMA_STATS_TIMEOUT_SEC=1.2
# OROMA_DBW_ENABLE=1
# OROMA_DBW_SOCKET=/opt/ai/oroma/data/state/db_writer.sock
#
# HINWEIS ZU tools/stats_snapshot.py
# ──────────────────────────────────
# tools/stats_snapshot.py bleibt bewusst erhalten:
# - als Reparatur-/Audit-Werkzeug
# - für Full-Rebuilds / Konsistenzprüfungen
#
# Dieser Aggregator ist jedoch der neue primäre Fast Path für den laufenden Betrieb.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core import db_writer_client

LOG = logging.getLogger("oroma.stats_event_aggregator")

OROMA_DB_PATH = str(os.environ.get("OROMA_DB_PATH") or "/opt/ai/oroma/data/oroma.db")
OROMA_STATS_DB_PATH = str(os.environ.get("OROMA_STATS_DB_PATH") or "/opt/ai/oroma/data/stats.db")
TIMEOUT_SEC = float(os.environ.get("OROMA_STATS_TIMEOUT_SEC") or "1.2")
BATCH = int(os.environ.get("OROMA_STATS_BATCH") or "5000")
EVENT_BATCH = int(os.environ.get("OROMA_STATS_EVENT_BATCH") or str(BATCH))
APPLY_BATCH = int(os.environ.get("OROMA_STATS_APPLY_BATCH") or "5000")
APPLY_MAX_PER_RUN = int(os.environ.get("OROMA_STATS_APPLY_MAX_PER_RUN") or "20000")
PENDING_HIGH_WATER = int(os.environ.get("OROMA_STATS_PENDING_HIGH_WATER") or "20000")
PENDING_LOW_WATER = int(os.environ.get("OROMA_STATS_PENDING_LOW_WATER") or "5000")
EMIT_MAX_PER_RUN = int(os.environ.get("OROMA_STATS_EMIT_MAX_PER_RUN") or "10000")
DBW_TIMEOUT_MS = int(os.environ.get("OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM") or "60000")

DEFAULT_METRIC_KEYS = [
    "reward_curriculum",
    "reward_speech",
    "dream_eff_24h",
    "infer_ms_5m",
    "binding_mbi",
    "binding_snap",
    "binding_graph",
    "binding_rep",
    "coverage_total",
    "coverage_30d",
]


def _now() -> int:
    return int(time.time())


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return int(default)
        return int(v)
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()


def _connect_sqlite(path: str, readonly: bool, timeout_sec: float) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=max(0.2, float(timeout_sec)))
    else:
        conn = sqlite3.connect(path, timeout=max(0.2, float(timeout_sec)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 800")
    return conn


def _main_ro() -> sqlite3.Connection:
    return _connect_sqlite(OROMA_DB_PATH, readonly=True, timeout_sec=TIMEOUT_SEC)


def _stats_ro() -> sqlite3.Connection:
    # Nach ensure_schema existiert stats.db. Für Reads genügt ro.
    return _connect_sqlite(OROMA_STATS_DB_PATH, readonly=True, timeout_sec=max(1.0, TIMEOUT_SEC))


def _parse_metrics_keys() -> List[str]:
    raw = str(os.environ.get("OROMA_STATS_METRICS_KEYS") or "").strip()
    items = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
    if not items:
        items = list(DEFAULT_METRIC_KEYS)
    seen = set()
    out: List[str] = []
    for k in items:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _dbw_required() -> None:
    if not db_writer_client.enabled():
        raise RuntimeError("DBWriter disabled – stats_event_aggregator requires OROMA_DBW_ENABLE=1")
    if not db_writer_client.ping(timeout_ms=min(2000, DBW_TIMEOUT_MS)):
        raise RuntimeError("DBWriter not reachable – stats_event_aggregator uses DBWriter-only writes")


def _schema_stmts() -> List[Tuple[str, Sequence[Any]]]:
    return [
        ("CREATE TABLE IF NOT EXISTS stats_meta (k TEXT PRIMARY KEY, v TEXT)", ()),
        (
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
            """,
            (),
        ),
        ("CREATE INDEX IF NOT EXISTS idx_stats_points_series_ts ON stats_points(series, ts)", ()),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_stats_points_src ON stats_points(src_table, src_uid, series)",
            (),
        ),
        (
            """
            CREATE TABLE IF NOT EXISTS stats_curve_day (
              day         TEXT PRIMARY KEY,
              chains      INTEGER NOT NULL,
              sum_quality REAL NOT NULL,
              q_max       REAL NOT NULL,
              updated_ts  INTEGER NOT NULL
            )
            """,
            (),
        ),
        (
            """
            CREATE TABLE IF NOT EXISTS stats_event_queue (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              event_uid   TEXT    NOT NULL,
              event_type  TEXT    NOT NULL,
              src_table   TEXT    NOT NULL,
              src_id      INTEGER NOT NULL DEFAULT 0,
              src_ts      INTEGER NOT NULL DEFAULT 0,
              day         TEXT    NULL,
              payload_json TEXT   NULL,
              status      TEXT    NOT NULL DEFAULT 'pending',
              created_ts  INTEGER NOT NULL,
              applied_ts  INTEGER NULL
            )
            """,
            (),
        ),
        ("CREATE UNIQUE INDEX IF NOT EXISTS ux_stats_event_uid ON stats_event_queue(event_uid)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_stats_event_status_id ON stats_event_queue(status, id)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_stats_event_src ON stats_event_queue(src_table, src_id)", ()),
    ]


def _ensure_schema() -> None:
    _dbw_required()
    db_writer_client.transaction(_schema_stmts(), tag="stats.event.schema", priority="high", timeout_ms=DBW_TIMEOUT_MS, db="stats")


def _meta_get(conn: sqlite3.Connection, k: str, default: str = "") -> str:
    row = conn.execute("SELECT v FROM stats_meta WHERE k=?", (str(k),)).fetchone()
    return str(row["v"]) if row else str(default)


def _queue_insert_sql() -> str:
    return (
        "INSERT OR IGNORE INTO stats_event_queue(event_uid, event_type, src_table, src_id, src_ts, day, payload_json, status, created_ts) "
        "VALUES(?,?,?,?,?,?,?,'pending',?)"
    )


def _meta_upsert_sql() -> str:
    return "INSERT INTO stats_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v"


def _emit_event_batch(params_list: List[Sequence[Any]], meta_updates: List[Tuple[str, str]]) -> int:
    if not params_list and not meta_updates:
        return 0
    stmts: List[Tuple[str, Sequence[Any]]] = []
    if params_list:
        # executemany cannot be combined with other statements in one transaction via helper,
        # therefore we unfold batches deliberately. This keeps order and visibility explicit.
        for p in params_list:
            stmts.append((_queue_insert_sql(), p))
    for k, v in meta_updates:
        stmts.append((_meta_upsert_sql(), (str(k), str(v))))
    db_writer_client.transaction(stmts, tag="stats.event.emit", priority="normal", timeout_ms=DBW_TIMEOUT_MS, db="stats")
    return len(params_list)


def _point_event(event_uid: str, src_table: str, src_id: int, ts: int, series: str, value: float, src_uid: str, meta: Optional[str] = None) -> Tuple[str, str, str, int, int, Optional[str], str, int]:
    payload = json.dumps(
        {
            "kind": "point",
            "ts": int(ts),
            "series": str(series),
            "value": float(value),
            "src_table": str(src_table),
            "src_id": int(src_id),
            "src_uid": str(src_uid),
            "meta": meta,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (event_uid, "point", str(src_table), int(src_id), int(ts), None, payload, _now())


def _curve_event(event_uid: str, src_id: int, ts: int, quality: float) -> Tuple[str, str, str, int, int, Optional[str], str, int]:
    day = time.strftime("%Y-%m-%d", time.gmtime(int(ts))) if int(ts) > 0 else "1970-01-01"
    payload = json.dumps(
        {
            "kind": "curve_day_delta",
            "day": day,
            "chains": 1,
            "sum_quality": float(quality),
            "q_max": float(quality),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (event_uid, "curve_day_delta", "snapchains", int(src_id), int(ts), day, payload, _now())


def emit_rewards(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    last_id = _safe_int(_meta_get(sconn, "evtq_ck_rewards_last_id", "0"), 0)
    rows = mconn.execute(
        "SELECT id, created_at AS ts, source, reward FROM rewards_log WHERE id > ? ORDER BY id ASC LIMIT ?",
        (last_id, int(batch)),
    ).fetchall()
    if not rows:
        return 0
    params_list: List[Sequence[Any]] = []
    max_id = last_id
    for r in rows:
        rid = _safe_int(r["id"])
        max_id = max(max_id, rid)
        src = str(r["source"])
        ts = _safe_int(r["ts"])
        reward = _safe_float(r["reward"])
        uid = f"rewards_log:{rid}:reward:{src}"
        params_list.append(_point_event(uid, "rewards_log", rid, ts, f"reward:{src}", reward, str(rid)))
    _emit_event_batch(params_list, [("evtq_ck_rewards_last_id", str(max_id))])
    return len(params_list)


def emit_coverage(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    emitted = 0
    # coverage_log -> coverage_total
    last_id = _safe_int(_meta_get(sconn, "evtq_ck_coverage_last_id", "0"), 0)
    rows = mconn.execute(
        "SELECT id, ts, coverage FROM coverage_log WHERE id > ? ORDER BY id ASC LIMIT ?",
        (last_id, int(batch)),
    ).fetchall()
    if rows:
        params_list: List[Sequence[Any]] = []
        max_id = last_id
        for r in rows:
            rid = _safe_int(r["id"])
            max_id = max(max_id, rid)
            ts = _safe_int(r["ts"])
            cov = _safe_float(r["coverage"])
            uid = f"coverage_log:{rid}:coverage_total"
            params_list.append(_point_event(uid, "coverage_log", rid, ts, "coverage_total", cov, str(rid)))
        _emit_event_batch(params_list, [("evtq_ck_coverage_last_id", str(max_id))])
        emitted += len(params_list)
    # coverage_log_30d -> coverage_30d
    try:
        exists = mconn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coverage_log_30d' LIMIT 1").fetchone()
    except Exception:
        exists = None
    if exists:
        last_id30 = _safe_int(_meta_get(sconn, "evtq_ck_coverage30_last_id", "0"), 0)
        rows30 = mconn.execute(
            "SELECT id, ts, coverage FROM coverage_log_30d WHERE id > ? ORDER BY id ASC LIMIT ?",
            (last_id30, int(batch)),
        ).fetchall()
        if rows30:
            params_list = []
            max_id30 = last_id30
            for r in rows30:
                rid = _safe_int(r["id"])
                max_id30 = max(max_id30, rid)
                ts = _safe_int(r["ts"])
                cov = _safe_float(r["coverage"])
                uid = f"coverage_log_30d:{rid}:coverage_30d"
                params_list.append(_point_event(uid, "coverage_log_30d", rid, ts, "coverage_30d", cov, str(rid)))
            _emit_event_batch(params_list, [("evtq_ck_coverage30_last_id", str(max_id30))])
            emitted += len(params_list)
    return emitted


def emit_empathy(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    last_id = _safe_int(_meta_get(sconn, "evtq_ck_empathy_last_id", "0"), 0)
    rows = mconn.execute(
        "SELECT id, ts, score, mood FROM empathy_snaps WHERE id > ? ORDER BY id ASC LIMIT ?",
        (last_id, int(batch)),
    ).fetchall()
    if not rows:
        return 0
    params_list: List[Sequence[Any]] = []
    max_id = last_id
    for r in rows:
        rid = _safe_int(r["id"])
        max_id = max(max_id, rid)
        ts = _safe_int(r["ts"])
        score = _safe_float(r["score"])
        meta = json.dumps({"mood": str(r["mood"])}, ensure_ascii=False, separators=(",", ":"))
        uid = f"empathy_snaps:{rid}:empathy_score"
        params_list.append(_point_event(uid, "empathy_snaps", rid, ts, "empathy_score", score, str(rid), meta=meta))
    _emit_event_batch(params_list, [("evtq_ck_empathy_last_id", str(max_id))])
    return len(params_list)


def emit_metrics(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int, keys: List[str]) -> int:
    if not keys:
        return 0
    last_ts = _safe_int(_meta_get(sconn, "evtq_ck_metrics_last_ts", "0"), 0)
    placeholders = ",".join(["?"] * len(keys))
    rows = mconn.execute(
        f"SELECT ts, key, value FROM metrics WHERE ts > ? AND key IN ({placeholders}) ORDER BY ts ASC LIMIT ?",
        (last_ts, *keys, int(batch)),
    ).fetchall()
    if not rows:
        return 0
    params_list: List[Sequence[Any]] = []
    max_ts = last_ts
    for r in rows:
        ts = _safe_int(r["ts"])
        max_ts = max(max_ts, ts)
        key = str(r["key"])
        value = _safe_float(r["value"])
        src_uid = _sha1(f"{ts}|{key}|{value}")
        uid = f"metrics:{src_uid}:metric:{key}"
        params_list.append(_point_event(uid, "metrics", 0, ts, f"metric:{key}", value, src_uid))
    _emit_event_batch(params_list, [("evtq_ck_metrics_last_ts", str(max_ts))])
    return len(params_list)


def emit_snapchains(mconn: sqlite3.Connection, sconn: sqlite3.Connection, batch: int) -> int:
    last_id = _safe_int(_meta_get(sconn, "evtq_ck_snapchains_last_id", "0"), 0)
    rows = mconn.execute(
        """
        SELECT id, ts, quality
          FROM snapchains
         WHERE id > ?
           AND (status IS NULL OR status != 'deleted')
      ORDER BY id ASC
         LIMIT ?
        """,
        (last_id, int(batch)),
    ).fetchall()
    if not rows:
        return 0
    params_list: List[Sequence[Any]] = []
    max_id = last_id
    for r in rows:
        rid = _safe_int(r["id"])
        max_id = max(max_id, rid)
        ts = _safe_int(r["ts"])
        q = _safe_float(r["quality"])
        uid = f"snapchains:{rid}:curve_day"
        params_list.append(_curve_event(uid, rid, ts, q))
    _emit_event_batch(params_list, [("evtq_ck_snapchains_last_id", str(max_id))])
    return len(params_list)


def _fetch_pending_events(sconn: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
    return sconn.execute(
        "SELECT id, event_uid, event_type, payload_json FROM stats_event_queue WHERE status='pending' ORDER BY id ASC LIMIT ?",
        (int(limit),),
    ).fetchall()


def _apply_point_stmts(payload: Dict[str, Any], event_id: int, now_ts: int) -> List[Tuple[str, Sequence[Any]]]:
    return [
        (
            """
            INSERT INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(src_table, src_uid, series) DO UPDATE SET
              ts=excluded.ts,
              value=excluded.value,
              src_id=excluded.src_id,
              meta=CASE WHEN excluded.meta IS NOT NULL THEN excluded.meta ELSE stats_points.meta END
            """,
            (
                _safe_int(payload.get("ts"), 0),
                str(payload.get("series") or ""),
                _safe_float(payload.get("value"), 0.0),
                str(payload.get("src_table") or "legacy"),
                _safe_int(payload.get("src_id"), 0),
                str(payload.get("src_uid") or ""),
                payload.get("meta"),
            ),
        ),
        ("UPDATE stats_event_queue SET status='done', applied_ts=? WHERE id=?", (int(now_ts), int(event_id))),
    ]


def _apply_curve_stmts(payload: Dict[str, Any], event_id: int, now_ts: int) -> List[Tuple[str, Sequence[Any]]]:
    day = str(payload.get("day") or "1970-01-01")
    chains = _safe_int(payload.get("chains"), 0)
    sum_q = _safe_float(payload.get("sum_quality"), 0.0)
    q_max = _safe_float(payload.get("q_max"), 0.0)
    return [
        (
            """
            INSERT INTO stats_curve_day(day, chains, sum_quality, q_max, updated_ts)
            VALUES(?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET
              chains=stats_curve_day.chains + excluded.chains,
              sum_quality=stats_curve_day.sum_quality + excluded.sum_quality,
              q_max=CASE WHEN excluded.q_max > stats_curve_day.q_max THEN excluded.q_max ELSE stats_curve_day.q_max END,
              updated_ts=excluded.updated_ts
            """,
            (day, int(chains), float(sum_q), float(q_max), int(now_ts)),
        ),
        ("UPDATE stats_event_queue SET status='done', applied_ts=? WHERE id=?", (int(now_ts), int(event_id))),
    ]


def apply_queue(sconn: sqlite3.Connection, batch: int) -> int:
    rows = _fetch_pending_events(sconn, limit=batch)
    if not rows:
        return 0
    now_ts = _now()
    stmts: List[Tuple[str, Sequence[Any]]] = []
    for r in rows:
        event_id = _safe_int(r["id"], 0)
        try:
            payload = json.loads(str(r["payload_json"] or "{}"))
        except Exception as e:
            LOG.warning("stats_event bad payload id=%s: %s", event_id, e)
            stmts.append(("UPDATE stats_event_queue SET status='error', applied_ts=? WHERE id=?", (int(now_ts), int(event_id))))
            continue
        kind = str(payload.get("kind") or r["event_type"] or "")
        if kind == "point":
            stmts.extend(_apply_point_stmts(payload, event_id, now_ts))
        elif kind == "curve_day_delta":
            stmts.extend(_apply_curve_stmts(payload, event_id, now_ts))
        else:
            stmts.append(("UPDATE stats_event_queue SET status='error', applied_ts=? WHERE id=?", (int(now_ts), int(event_id))))
    db_writer_client.transaction(stmts, tag="stats.event.apply", priority="normal", timeout_ms=DBW_TIMEOUT_MS, db="stats")
    return len(rows)


def queue_backlog(sconn: sqlite3.Connection) -> int:
    row = sconn.execute("SELECT COUNT(*) AS n FROM stats_event_queue WHERE status='pending'").fetchone()
    return _safe_int(row["n"] if row else 0, 0)




def _emit_budget_from_backlog(pending_before: int) -> int:
    """
    Leitet aus dem aktuellen Queue-Backlog ein Emissionsbudget pro Lauf ab.

    Ziele:
    - oberhalb HIGH_WATER: keinerlei neue Emission
    - zwischen LOW_WATER und HIGH_WATER: nur gedrosselte Emission
    - unterhalb LOW_WATER: normaler, aber gedeckelter Emit-Betrieb

    Damit entsteht eine Hysterese statt eines harten Ein/Aus-Umschaltens und
    der Producer flutet die Queue nach Catch-up-Phasen nicht sofort wieder.
    """
    pb = max(0, int(pending_before))
    hi = max(1, int(PENDING_HIGH_WATER))
    lo = max(0, min(int(PENDING_LOW_WATER), hi - 1))
    cap = max(0, int(EMIT_MAX_PER_RUN))
    if cap <= 0:
        return 0
    if pb >= hi:
        return 0
    if pb <= lo:
        return cap
    span = max(1, hi - lo)
    ratio = float(hi - pb) / float(span)
    budget = int(round(cap * max(0.15, min(1.0, ratio))))
    return max(1000, min(cap, budget))

def _apply_catchup(sconn: sqlite3.Connection, *, batch: int, max_total: int) -> Tuple[int, int]:
    """
    Fuehrt mehrere Apply-Runden in einem Lauf aus, damit der Consumer den
    Backlog real abbauen kann.

    Verhalten:
    - wendet zunaechst einen Batch an
    - laeuft weiter, solange volle Batches gefunden werden
    - stoppt spaetestens bei max_total Events pro Lauf

    Rueckgabe:
    - applied_total: insgesamt angewendete Events
    - rounds: Anzahl der Apply-Runden
    """
    applied_total = 0
    rounds = 0
    hard_cap = max(int(batch), int(max_total))
    while applied_total < hard_cap:
        remaining = hard_cap - applied_total
        this_batch = min(int(batch), int(remaining))
        if this_batch <= 0:
            break
        n = apply_queue(sconn, this_batch)
        if n <= 0:
            break
        applied_total += int(n)
        rounds += 1
        if n < this_batch:
            break
    return int(applied_total), int(rounds)


def run_once() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "ts": _now(),
        "emitted": 0,
        "applied": 0,
        "pending": 0,
        "pending_before": 0,
        "apply_rounds": 0,
        "emit_skipped_due_to_backlog": False,
        "emit_budget": 0,
        "errors": [],
    }
    mconn: Optional[sqlite3.Connection] = None
    sconn: Optional[sqlite3.Connection] = None
    try:
        _ensure_schema()
        mconn = _main_ro()
        sconn = _stats_ro()
        keys = _parse_metrics_keys()
        pending_before = queue_backlog(sconn)
        emitted = 0
        emit_skipped = False
        emit_budget = _emit_budget_from_backlog(pending_before)
        if emit_budget > 0:
            per_source_batch = max(200, min(int(EVENT_BATCH), int(emit_budget)))
            remaining_budget = int(emit_budget)
            for emit_fn, args in (
                (emit_rewards, (mconn, sconn, per_source_batch)),
                (emit_coverage, (mconn, sconn, per_source_batch)),
                (emit_empathy, (mconn, sconn, per_source_batch)),
                (emit_metrics, (mconn, sconn, per_source_batch, keys)),
                (emit_snapchains, (mconn, sconn, per_source_batch)),
            ):
                if remaining_budget <= 0:
                    break
                n = int(emit_fn(*args) or 0)
                emitted += n
                remaining_budget -= n
                if n >= per_source_batch and remaining_budget < per_source_batch:
                    break
        else:
            emit_skipped = True
        # Re-open stats ro so producer-side DBWriter writes are visible for queue scan.
        try:
            sconn.close()
        except Exception:
            pass
        sconn = _stats_ro()
        applied, apply_rounds = _apply_catchup(sconn, batch=APPLY_BATCH, max_total=APPLY_MAX_PER_RUN)
        try:
            sconn.close()
        except Exception:
            pass
        sconn = _stats_ro()
        pending = queue_backlog(sconn)
        out.update({
            "ok": True,
            "emitted": int(emitted),
            "applied": int(applied),
            "pending_before": int(pending_before),
            "pending": int(pending),
            "apply_rounds": int(apply_rounds),
            "emit_skipped_due_to_backlog": bool(emit_skipped),
            "emit_budget": int(emit_budget),
        })
        return out
    except Exception as e:
        out["errors"].append(str(e))
        return out
    finally:
        try:
            if mconn:
                mconn.close()
        except Exception:
            pass
        try:
            if sconn:
                sconn.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA stats event aggregator (delta queue -> stats.db read model)")
    ap.add_argument("--once", action="store_true", help="Run one producer+consumer cycle and exit")
    args = ap.parse_args()
    if args.once:
        out = run_once()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 2
    while True:
        out = run_once()
        print(json.dumps(out, ensure_ascii=False))
        time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main())

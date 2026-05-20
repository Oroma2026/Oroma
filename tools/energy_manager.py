#!/usr/bin/env python3
# =============================================================================
# Pfad:     /opt/ai/oroma/tools/energy_manager.py
# Projekt:  ORÓMA – Energy Manager (Stats-DB Producer)
# Version:  v3.8.0 – Stats-DB schema-robust (node_energy/relation_energy) – Top-Cache Labels + leere rel_type Fix (UI ohne '?')
# Stand:    2026-04-24
# Autor:    Jörg Werner (public) / ORÓMA Project (internal)
# =============================================================================
#
# Zweck
# -----
# Dieses Tool pflegt "Energy"-Summaries in der stats.db, die in der UI (Learning)
# angezeigt werden: Top Objects / Top Relations inkl. Cache-Age.
#
# Patch v3.7.5:
#   - Relations/Nodes: Payload enthält jetzt id/relation/name (und bei Bedarf Label-Hydration),
#     damit learning.html nicht auf '?' fällt.
#   - Leere relation-Strings werden beim Ingest auf "unknown" gesetzt.
#   - Fallback: Wenn relation_energy kaputt ist (src/dst=0 oder rel_type leer), werden
#     Top-Relations direkt aus oroma.db/object_relations (Counts) gebaut.
#
# ENV
# ---
#   OROMA_DB_PATH                         (Default: /opt/ai/oroma/data/oroma.db)
#   OROMA_STATS_DB_PATH                   (Default: /opt/ai/oroma/data/stats.db)
#   OROMA_ENERGY_FALLBACK_WINDOW_DAYS     (Default: 7)
#
# Nutzung
# ------
#   PYTHONPATH=/opt/ai/oroma python3 tools/energy_manager.py --once
#   (oder via systemd timer/service: oroma-energy.timer)
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import logging
from core.log_guard import log_suppressed

# --------------------------------------
# DBWriter (Single-Writer) – STRICT MODE
# --------------------------------------
# ORÓMA läuft produktiv als Single-Writer System. Dieses Tool schreibt daher *ausschließlich*
# über den DBWriter in die stats.db. Lokale RW-Verbindungen sind absichtlich verboten.
try:
    from core import db_writer_client as db_writer_client  # type: ignore
except Exception:  # pragma: no cover
    db_writer_client = None  # type: ignore


def _dbw_required() -> bool:
    try:
        return bool(db_writer_client and db_writer_client.enabled())
    except Exception:
        return False


def _dbw_timeout_ms() -> int:
    try:
        return int(os.getenv("OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM", "60000"))
    except Exception:
        return 60000


def _dbw_exec(sql: str, params: Tuple[Any, ...], tag: str) -> int:
    if not _dbw_required():
        raise RuntimeError("DBWriter required but not available/enabled")
    return int(
        db_writer_client.exec_write(
            sql,
            params=params,
            tag=tag,
            priority="normal",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
    )


def _dbw_executemany(sql: str, params_list: List[Tuple[Any, ...]], tag: str) -> int:
    if not params_list:
        return 0
    if not _dbw_required():
        raise RuntimeError("DBWriter required but not available/enabled")
    return int(
        db_writer_client.executemany(
            sql,
            params_list=params_list,
            tag=tag,
            priority="normal",
            timeout_ms=_dbw_timeout_ms(),
            db="stats",
        )
    )


def _dbw_tx(stmts: List[Tuple[str, Tuple[Any, ...]]], tag: str) -> None:
    if not stmts:
        return
    if not _dbw_required():
        raise RuntimeError("DBWriter required but not available/enabled")
    db_writer_client.transaction(
        stmts=stmts,
        tag=tag,
        priority="normal",
        timeout_ms=_dbw_timeout_ms(),
        db="stats",
    )



def _ensure_basic_logging() -> None:
    """Ensure predictable logging for CLI/oneshot execution.

    Why this exists
    ---------------
    In ORÓMA, the Energy Manager can be executed via:
      • systemd oneshot (oroma-energy.service)
      • systemd timer (oroma-energy.timer)
      • ORÓMA Orchestrator job runner (oroma-orchestrator)
      • manual CLI call for debugging

    In all of those modes, Python's root logger may be *unconfigured*.
    When that happens, log output can be missing or inconsistent, and
    exceptions might not surface in the expected log targets.

    This helper configures a minimal, non-destructive logging setup:
      • If handlers already exist, only the level is ensured.
      • If no handlers exist, installs a basicConfig() handler.

    Notes
    -----
    - We intentionally keep the format compact and systemd/journal-friendly.
    - Level can be overridden via OROMA_LOG_LEVEL (INFO|DEBUG|...)
    """

    lvl_name = (os.environ.get("OROMA_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, lvl_name, logging.INFO)

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    else:
        root.setLevel(level)


# --------------------------------------
# Defaults / Paths
# --------------------------------------

OROMA_DB_PATH = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
OROMA_STATS_DB_PATH = os.environ.get("OROMA_STATS_DB_PATH", "/opt/ai/oroma/data/stats.db")
OROMA_ENERGY_FALLBACK_WINDOW_DAYS = int(os.environ.get("OROMA_ENERGY_FALLBACK_WINDOW_DAYS", "7"))


# --------------------------------------
# Energy Model Tunables (Weights / Decay)
# --------------------------------------
#
# Hintergrund:
#   In der Energy-Topliste dominieren oft Infrastruktur-Hubs (vision/token, scenegraph:*, meta_to_chain …).
#   Das ist technisch korrekt (sie sind "sehr oft gesehen"), aber semantisch unhilfreich.
#   Daher gibt es zwei Hebel:
#     (1) Relation-Gewichte: technische Relations bekommen einen kleineren inkrementellen Beitrag.
#     (2) Globaler Decay-Sweep: Energy fällt auch ohne "re-seeing" (nicht nur bei UPDATE).
#
# ENV:
#   OROMA_ENERGY_REL_WEIGHTS_JSON          JSON dict: {"rel_type": weight, ...} (optional override)
#   OROMA_ENERGY_DECAY_ENABLED             "1" (default) / "0"
#   OROMA_ENERGY_DECAY_BATCH_SIZE          default 1000 (inkrementell pro Run)
#   OROMA_ENERGY_RETRO_WEIGHT_BATCH_SIZE   default 2000 (einmalige Rel-Rescale Migration)
#

RELATION_WEIGHTS_DEFAULT: Dict[str, float] = {
    # Infrastruktur / technische Ketten: stark runter
    "chain_to_origin": 0.05,
    "meta_to_chain": 0.10,
    "origin": 0.05,

    # Semantisch / erklärend: moderat
    "describes": 0.30,
    "tag": 0.30,
    "label": 0.40,

    # Spatio-temporal / Ereignis: normal bis leicht höher
    "co_occur": 1.00,
    "temporal_next": 0.80,
    "spatial_near": 1.50,
}

_REL_WEIGHTS_CACHE: Optional[Dict[str, float]] = None

def _rel_weight(rel_type: str) -> float:
    """Liefert Gewicht für rel_type (Default + optional ENV override)."""
    global _REL_WEIGHTS_CACHE
    if _REL_WEIGHTS_CACHE is None:
        w = dict(RELATION_WEIGHTS_DEFAULT)
        raw = os.environ.get("OROMA_ENERGY_REL_WEIGHTS_JSON", "").strip()
        if raw:
            try:
                j = json.loads(raw)
                if isinstance(j, dict):
                    for k, v in j.items():
                        try:
                            w[str(k)] = float(v)
                        except Exception:
                            pass
            except Exception:
                pass
        _REL_WEIGHTS_CACHE = w
    rt = str(rel_type or "").strip()
    if not rt:
        return 1.0
    return float(_REL_WEIGHTS_CACHE.get(rt, 1.0))

def _decay_enabled() -> bool:
    return str(os.environ.get("OROMA_ENERGY_DECAY_ENABLED", "1")).strip() not in ("0", "false", "False", "no", "NO")

def _decay_batch_size() -> int:
    try:
        return max(100, int(os.environ.get("OROMA_ENERGY_DECAY_BATCH_SIZE", "1000")))
    except Exception:
        return 1000

def _retro_weight_batch_size() -> int:
    try:
        return max(100, int(os.environ.get("OROMA_ENERGY_RETRO_WEIGHT_BATCH_SIZE", "2000")))
    except Exception:
        return 2000

# --------------------------------------
# Helpers
# --------------------------------------

def _now() -> int:
    return int(time.time())


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _json_sanitize(obj: Any) -> Any:
    """Recursively sanitize objects for strict JSON output (no NaN/Inf).

    Background
    ----------
    Browsers parse JSON strictly (JSON.parse), while Python's json encoder may
    emit NaN/Infinity tokens if allow_nan=True (default). If any producer
    writes non-finite floats into stats.db, the UI fetch().json() will fail and
    the Learning page will silently lose numbers (shows '—').

    Policy
    ------
    - float('nan') / +/-inf are mapped to 0.0
    - dict/list/tuple are processed recursively
    - other values are returned unchanged
    """
    try:
        if isinstance(obj, float):
            return obj if math.isfinite(obj) else 0.0
        if isinstance(obj, dict):
            return {str(k): _json_sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [ _json_sanitize(v) for v in obj ]
    except Exception:
        return 0.0
    return obj



def _connect_sqlite(path: str, readonly: bool, timeout_sec: float = 30.0) -> sqlite3.Connection:
    """Open a SQLite connection with ORÓMA-safe defaults.

    IMPORTANT (ORÓMA production policy)
    -----------------------------------
    - Writes to managed DBs are funneled through DBWriter (Single-Writer).
    - This tool therefore opens stats.db **read-only** and performs all writes via DBWriter.
    - The oroma.db remains read-only as well.

    We still need a local RO connection for PRAGMA table_info and selects.
    """
    if not readonly:
        raise RuntimeError("energy_manager must not open local RW sqlite connections; use DBWriter")

    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
    except Exception:
        pass
    # Do NOT set journal_mode/synchronous here (RO), and never fight WAL locks locally.
    return conn


# Cache: Table-Columns in stats.db (Schema kann zwischen Versionen variieren)
_TABLE_COLS_CACHE: Dict[str, List[str]] = {}


def _table_cols(conn: sqlite3.Connection, table: str) -> List[str]:
    """Gibt Spaltennamen für eine Tabelle zurück (gecached).

    Wichtig:
      - stats.db wird im Projekt weiterentwickelt.
      - Tools müssen daher schema-robust sein (NOT NULL + neue Spalten),
        damit Orchestrator-Jobs nicht dauerhaft crashen.
    """
    if table in _TABLE_COLS_CACHE:
        return _TABLE_COLS_CACHE[table]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    cols: List[str] = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            cols.append(str(r["name"]))
        else:
            cols.append(str(r[1]))
    _TABLE_COLS_CACHE[table] = cols
    return cols


def _has_col(cols: List[str], name: str) -> bool:
    return name in cols


def _labels_map_for_ids(main_conn: sqlite3.Connection, ids: List[int]) -> Dict[int, Dict[str, str]]:
    """
    Liefert Labels/Kinds für object_nodes IDs aus der oroma.db.

    Warum:
      - learning.html zeigt bei Energy-Relations/Nodes bevorzugt 'label'/'name'/'relation'.
      - Wenn diese Felder fehlen, landet die UI bei '?'.
      - Wir hydratisieren daher *nur für Top-N* (kleine Menge) Labels direkt im Producer.

    Rückgabe:
      { id: { 'label': str, 'kind': str } }
    """
    if not ids:
        return {}

    # Dedup + defensiv begrenzen
    try:
        ids = [int(x) for x in ids if int(x) > 0]
    except Exception:
        return {}
    if not ids:
        return {}
    ids = list(dict.fromkeys(ids))[:500]

    # object_nodes muss existieren (Slim-DB kann Tabellen auslassen, je nach Setup)
    try:
        ok = main_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='object_nodes' LIMIT 1"
        ).fetchone()
        if not ok:
            return {}
    except Exception:
        return {}

    placeholders = ",".join(["?"] * len(ids))
    try:
        rows = main_conn.execute(
            f"SELECT id, COALESCE(label,'') AS label, COALESCE(kind,'') AS kind FROM object_nodes WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()

        out: Dict[int, Dict[str, str]] = {}
        for r in rows:
            try:
                rid = int(r["id"])
                out[rid] = {
                    "label": (r["label"] or "").strip(),
                    "kind": (r["kind"] or "").strip(),
                }
            except Exception:
                continue
        return out
    except Exception:
        return {}


# --------------------------------------
# Schema (stats.db)
# --------------------------------------

def _ensure_schema(stats_conn: sqlite3.Connection) -> None:
    """Stellt sicher, dass die Energy-Tabellen in stats.db existieren (Writes via DBWriter).

    Policy (ORÓMA Stufe C / Single-Writer)
    --------------------------------------
    - stats_conn ist read-only und wird nur für Schema-Inspektion (PRAGMA table_info) und SELECTs genutzt.
    - Alle DDL/DML laufen über DBWriter, um Multi-Writer Locks zu vermeiden.
    - Änderungen sind additiv & non-destructive (CREATE IF NOT EXISTS, ALTER ADD COLUMN).
    """
    stmts: List[Tuple[str, Tuple[Any, ...]]] = []

    stmts.append((
        """
        CREATE TABLE IF NOT EXISTS energy_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_ts INTEGER NOT NULL DEFAULT 0
        )
        """,
        tuple(),
    ))
    stmts.append(("INSERT OR IGNORE INTO energy_state(id,last_ts) VALUES (1,0)", tuple()))

    stmts.append((
        """
        CREATE TABLE IF NOT EXISTS node_energy (
            node_id INTEGER PRIMARY KEY,
            energy REAL NOT NULL DEFAULT 0.0,
            last_seen_ts INTEGER NOT NULL DEFAULT 0,
            seen_count INTEGER NOT NULL DEFAULT 0
        )
        """,
        tuple(),
    ))

    stmts.append((
        """
        CREATE TABLE IF NOT EXISTS relation_energy (
            relation_id INTEGER PRIMARY KEY,
            src_id INTEGER NOT NULL DEFAULT 0,
            dst_id INTEGER NOT NULL DEFAULT 0,
            rel_type TEXT NOT NULL DEFAULT '',
            energy REAL NOT NULL DEFAULT 0.0,
            last_seen_ts INTEGER NOT NULL DEFAULT 0,
            seen_count INTEGER NOT NULL DEFAULT 0
        )
        """,
        tuple(),
    ))

    stmts.append((
        """
        CREATE TABLE IF NOT EXISTS energy_top_cache (
            kind TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
        """,
        tuple(),
    ))

    _dbw_tx(stmts, tag="energy_manager.ensure_schema.base")
    _TABLE_COLS_CACHE.clear()

    # --- Additive Schema Erweiterungen (robust gegen alte stats.db) ---
    stmts = []
    cols_state = _table_cols(stats_conn, "energy_state")

    # Cursor/Flags für inkrementelle Hintergrundjobs (Decay & Retro-Weights)
    if not _has_col(cols_state, "decay_node_cursor"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN decay_node_cursor INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_state, "decay_rel_cursor"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN decay_rel_cursor INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_state, "last_decay_ts"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN last_decay_ts INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_state, "retro_rel_cursor"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN retro_rel_cursor INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_state, "weights_rel_applied"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN weights_rel_applied INTEGER NOT NULL DEFAULT 0", tuple()))

    # UI/Selftest-Kompatibilität (ui/learning.py):
    if not _has_col(cols_state, "retro_node_cursor"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN retro_node_cursor INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_state, "weights_node_applied"):
        stmts.append(("ALTER TABLE energy_state ADD COLUMN weights_node_applied INTEGER NOT NULL DEFAULT 0", tuple()))

    cols_node = _table_cols(stats_conn, "node_energy")
    if not _has_col(cols_node, "first_seen_ts"):
        stmts.append(("ALTER TABLE node_energy ADD COLUMN first_seen_ts INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_node, "updated_ts"):
        stmts.append(("ALTER TABLE node_energy ADD COLUMN updated_ts INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_node, "hits"):
        stmts.append(("ALTER TABLE node_energy ADD COLUMN hits INTEGER NOT NULL DEFAULT 0", tuple()))

    cols_rel = _table_cols(stats_conn, "relation_energy")
    if not _has_col(cols_rel, "first_seen_ts"):
        stmts.append(("ALTER TABLE relation_energy ADD COLUMN first_seen_ts INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_rel, "updated_ts"):
        stmts.append(("ALTER TABLE relation_energy ADD COLUMN updated_ts INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_rel, "hits"):
        stmts.append(("ALTER TABLE relation_energy ADD COLUMN hits INTEGER NOT NULL DEFAULT 0", tuple()))
    if not _has_col(cols_rel, "is_active"):
        stmts.append(("ALTER TABLE relation_energy ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1", tuple()))

    if stmts:
        _dbw_tx(stmts, tag="energy_manager.ensure_schema.alter")
        _TABLE_COLS_CACHE.clear()

def _get_state_last_ts(stats_conn: sqlite3.Connection) -> int:
    row = stats_conn.execute("SELECT last_ts FROM energy_state WHERE id=1").fetchone()
    return int(row["last_ts"] or 0) if row else 0


def _set_state_last_ts(stats_conn: sqlite3.Connection, ts: int) -> None:
    _dbw_exec("UPDATE energy_state SET last_ts=? WHERE id=1", (int(ts),), tag="energy_manager.state.last_ts")

def _get_state(stats_conn: sqlite3.Connection) -> Dict[str, Any]:
    """Lädt energy_state (id=1) als dict. Spalten sind schema-robust (nur Keys die existieren)."""
    cols = _table_cols(stats_conn, "energy_state")
    row = stats_conn.execute("SELECT * FROM energy_state WHERE id=1").fetchone()
    out: Dict[str, Any] = {}
    if not row:
        return out
    for c in cols:
        try:
            out[c] = row[c]
        except Exception:
            pass
    return out


def _state_update(stats_conn: sqlite3.Connection, **kwargs: Any) -> None:
    """Update energy_state (id=1) – nur Spalten, die tatsächlich existieren (Write via DBWriter)."""
    cols = _table_cols(stats_conn, "energy_state")
    sets: List[str] = []
    bind: List[Any] = []
    for k, v in kwargs.items():
        if _has_col(cols, k):
            sets.append(f"{k}=?")
            bind.append(v)
    if not sets:
        return
    bind.append(1)
    sql = "UPDATE energy_state SET " + ", ".join(sets) + " WHERE id=?"
    _dbw_exec(sql, tuple(bind), tag="energy_manager.state.update")

def _col_names(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def _detect_relation_columns(main_conn: sqlite3.Connection) -> Tuple[str, str, str]:
    """
    Versucht die Spaltennamen für object_relations zu erkennen:
      - src_id / a_id / from_id
      - dst_id / b_id / to_id
      - relation / rel / type
    """
    cols = set(_col_names(main_conn, "object_relations"))
    if "a_id" in cols:
        src_col = "a_id"
    elif "src_id" in cols:
        src_col = "src_id"
    elif "from_id" in cols:
        src_col = "from_id"
    else:
        src_col = "a_id"

    if "b_id" in cols:
        dst_col = "b_id"
    elif "dst_id" in cols:
        dst_col = "dst_id"
    elif "to_id" in cols:
        dst_col = "to_id"
    else:
        dst_col = "b_id"

    if "relation" in cols:
        rel_col = "relation"
    elif "rel" in cols:
        rel_col = "rel"
    elif "type" in cols:
        rel_col = "type"
    else:
        rel_col = "relation"

    return src_col, dst_col, rel_col
# --------------------------------------
# Energy Update
# --------------------------------------

@dataclass
class EnergyParams:
    half_life_sec: float = 3600.0 * 24.0 * 7.0  # default ~7 Tage
    inc_node: float = 1.0
    inc_rel: float = 2.0


def _decay_factor(dt_sec: float, half_life_sec: float) -> float:
    if dt_sec <= 0:
        return 1.0
    if half_life_sec <= 0:
        return 1.0
    # exp decay via half-life: factor = 0.5^(dt/half)
    return 0.5 ** (dt_sec / half_life_sec)


def _apply_decay(energy: float, last_ts: int, now_ts: int, half_life_sec: float) -> float:
    dt = float(max(0, now_ts - int(last_ts)))
    return float(energy) * _decay_factor(dt, half_life_sec)



def _upsert_node(stats_conn: sqlite3.Connection, node_id: int, now_ts: int, params: EnergyParams) -> None:
    """Upsert für node_energy – schema-robust, inkl. Decay über updated_ts (wenn vorhanden).

    Design:
      - last_seen_ts: wann der Node zuletzt "gesehen" wurde (Relation-Event).
      - updated_ts: wann die Energy zuletzt berechnet/geändert wurde (für globalen Decay-Sweep).
      - first_seen_ts/hits/seen_count: rein diagnostisch.
    """
    cols = _table_cols(stats_conn, "node_energy")

    row = stats_conn.execute("SELECT * FROM node_energy WHERE node_id=?", (int(node_id),)).fetchone()

    if not row:
        energy0 = float(params.inc_node)

        insert_cols: List[str] = []
        insert_vals: List[Any] = []

        def add(col: str, val: Any) -> None:
            if _has_col(cols, col):
                insert_cols.append(col)
                insert_vals.append(val)

        add("node_id", int(node_id))
        add("energy", float(energy0))
        add("hits", 1)
        add("first_seen_ts", int(now_ts))
        add("last_seen_ts", int(now_ts))
        add("updated_ts", int(now_ts))
        add("seen_count", 1)

        cols_sql = ",".join(insert_cols)
        ph = ",".join(["?"] * len(insert_vals))
        _dbw_exec(f"INSERT INTO node_energy({cols_sql}) VALUES ({ph})", tuple(insert_vals), tag="energy_manager.node.insert")
        return

    energy_prev = float(row["energy"] or 0.0) if ("energy" in row.keys()) else 0.0
    last_seen_prev = int(row["last_seen_ts"] or 0) if _has_col(cols, "last_seen_ts") else 0
    updated_prev = int(row["updated_ts"] or 0) if _has_col(cols, "updated_ts") else 0

    base_ts = updated_prev if updated_prev > 0 else last_seen_prev
    energy = _apply_decay(energy_prev, base_ts, now_ts, params.half_life_sec)
    energy += float(params.inc_node)

    set_parts: List[str] = []
    bind: List[Any] = []

    if _has_col(cols, "energy"):
        set_parts.append("energy=?")
        bind.append(float(energy))
    if _has_col(cols, "last_seen_ts"):
        set_parts.append("last_seen_ts=?")
        bind.append(int(now_ts))
    if _has_col(cols, "updated_ts"):
        set_parts.append("updated_ts=?")
        bind.append(int(now_ts))

    if _has_col(cols, "hits"):
        set_parts.append("hits=COALESCE(hits,0)+1")
    if _has_col(cols, "seen_count"):
        set_parts.append("seen_count=COALESCE(seen_count,0)+1")
    if _has_col(cols, "first_seen_ts"):
        set_parts.append("first_seen_ts=CASE WHEN COALESCE(first_seen_ts,0)=0 THEN ? ELSE first_seen_ts END")
        bind.append(int(now_ts))

    if not set_parts:
        return

    sql = "UPDATE node_energy SET " + ", ".join(set_parts) + " WHERE node_id=?"
    bind.append(int(node_id))
    _dbw_exec(sql, tuple(bind), tag="energy_manager.node.update")
def _upsert_relation(
    stats_conn: sqlite3.Connection,
    relation_id: int,
    src_id: int,
    dst_id: int,
    rel_type: str,
    now_ts: int,
    params: EnergyParams,
) -> None:
    """Upsert für relation_energy – schema-robust, inkl. Relation-Gewicht und Decay über updated_ts."""
    cols = _table_cols(stats_conn, "relation_energy")

    rt = str(rel_type or "").strip() or "unknown"
    inc = float(params.inc_rel) * float(_rel_weight(rt))

    row = stats_conn.execute(
        "SELECT * FROM relation_energy WHERE relation_id=?",
        (int(relation_id),),
    ).fetchone()

    if not row:
        insert_cols: List[str] = []
        insert_vals: List[Any] = []

        def add(col: str, val: Any) -> None:
            if _has_col(cols, col):
                insert_cols.append(col)
                insert_vals.append(val)

        add("relation_id", int(relation_id))
        add("src_id", int(src_id))
        add("dst_id", int(dst_id))
        add("rel_type", str(rt))
        add("energy", float(inc))
        add("hits", 1)
        add("first_seen_ts", int(now_ts))
        add("last_seen_ts", int(now_ts))
        add("updated_ts", int(now_ts))
        add("seen_count", 1)
        if _has_col(cols, "is_active"):
            add("is_active", 1)

        cols_sql = ",".join(insert_cols)
        ph = ",".join(["?"] * len(insert_vals))
        _dbw_exec(f"INSERT INTO relation_energy({cols_sql}) VALUES ({ph})", tuple(insert_vals), tag="energy_manager.rel.insert")
        return

    energy_prev = float(row["energy"] or 0.0) if ("energy" in row.keys()) else 0.0
    last_seen_prev = int(row["last_seen_ts"] or 0) if _has_col(cols, "last_seen_ts") else 0
    updated_prev = int(row["updated_ts"] or 0) if _has_col(cols, "updated_ts") else 0

    base_ts = updated_prev if updated_prev > 0 else last_seen_prev
    energy = _apply_decay(energy_prev, base_ts, now_ts, params.half_life_sec)
    energy += float(inc)

    set_parts: List[str] = []
    bind: List[Any] = []

    if _has_col(cols, "energy"):
        set_parts.append("energy=?")
        bind.append(float(energy))
    if _has_col(cols, "last_seen_ts"):
        set_parts.append("last_seen_ts=?")
        bind.append(int(now_ts))
    if _has_col(cols, "updated_ts"):
        set_parts.append("updated_ts=?")
        bind.append(int(now_ts))

    if _has_col(cols, "hits"):
        set_parts.append("hits=COALESCE(hits,0)+1")
    if _has_col(cols, "seen_count"):
        set_parts.append("seen_count=COALESCE(seen_count,0)+1")
    if _has_col(cols, "first_seen_ts"):
        set_parts.append("first_seen_ts=CASE WHEN COALESCE(first_seen_ts,0)=0 THEN ? ELSE first_seen_ts END")
        bind.append(int(now_ts))

    if _has_col(cols, "src_id"):
        set_parts.append("src_id=?")
        bind.append(int(src_id))
    if _has_col(cols, "dst_id"):
        set_parts.append("dst_id=?")
        bind.append(int(dst_id))
    if _has_col(cols, "rel_type"):
        set_parts.append("rel_type=?")
        bind.append(str(rt))
    if _has_col(cols, "is_active"):
        set_parts.append("is_active=1")

    if not set_parts:
        return

    sql = "UPDATE relation_energy SET " + ", ".join(set_parts) + " WHERE relation_id=?"
    bind.append(int(relation_id))
    _dbw_exec(sql, tuple(bind), tag="energy_manager.rel.update")

# --------------------------------------
# Background passes: Retro-Weights + Global Decay (incremental)
# --------------------------------------

def _retro_apply_relation_weights(stats_conn: sqlite3.Connection, now_ts: int) -> int:
    """Einmalige Migration: relation_energy.energy wird für bekannte rel_type skaliert.

    Motivation:
      Frühere Versionen inkrementierten alle Relations gleich stark (inc_rel).
      Damit technische Relations (meta_to_chain/chain_to_origin) nicht dauerhaft die Toplist dominieren,
      wird hier einmalig *bestehende* Energy ungefähr re-gewichtet.

    Umsetzung:
      - inkrementell (Cursor in energy_state), batchweise.
      - wird nur ausgeführt solange weights_rel_applied==0.

    Rückgabe:
      Anzahl aktualisierter Relation-Zeilen (in diesem Run).
    """
    state = _get_state(stats_conn)
    if int(state.get("weights_rel_applied") or 0) != 0:
        return 0

    cols_rel = _table_cols(stats_conn, "relation_energy")
    if not (_has_col(cols_rel, "rel_type") and _has_col(cols_rel, "energy")):
        _state_update(stats_conn, weights_rel_applied=1)
        return 0

    cursor = int(state.get("retro_rel_cursor") or 0)
    batch = _retro_weight_batch_size()

    rows = stats_conn.execute(
        "SELECT relation_id, rel_type, energy FROM relation_energy WHERE relation_id > ? ORDER BY relation_id LIMIT ?",
        (cursor, batch),
    ).fetchall()

    if not rows:
        # fertig
        _state_update(stats_conn, retro_rel_cursor=0, weights_rel_applied=1)
        return 0

    updates = []
    last_id = cursor
    for r in rows:
        rid = int(r["relation_id"])
        rt = str(r["rel_type"] or "").strip() or "unknown"
        w = float(_rel_weight(rt))
        e0 = float(r["energy"] or 0.0)
        e1 = e0 * w
        if abs(e1 - e0) > 1e-9:
            if _has_col(cols_rel, "updated_ts"):
                updates.append((float(e1), int(now_ts), rid))
            else:
                updates.append((float(e1), rid))
        last_id = rid

    if updates:
        if _has_col(cols_rel, "updated_ts"):
            _dbw_executemany("UPDATE relation_energy SET energy=?, updated_ts=? WHERE relation_id=?", updates, tag="energy_manager.retro.rel")
        else:
            _dbw_executemany("UPDATE relation_energy SET energy=? WHERE relation_id=?", updates, tag="energy_manager.retro.rel")

    _state_update(stats_conn, retro_rel_cursor=int(last_id))
    return len(updates)


def _decay_sweep_step(stats_conn: sqlite3.Connection, now_ts: int, params: EnergyParams) -> Dict[str, int]:
    """Globaler Decay – inkrementell über node_energy + relation_energy (Cursor in energy_state).

    Wichtig:
      Ohne diesen Sweep würde Decay nur bei "Touch" (Update durch neue Sichtung) passieren.
      Der Sweep sorgt dafür, dass alte Einträge auch ohne neue Events ausklingen.

    Rückgabe:
      dict mit Anzahl aktualisierter Nodes/Relations in diesem Run.
    """
    if not _decay_enabled():
        return {"nodes": 0, "relations": 0}

    batch = _decay_batch_size()
    out = {"nodes": 0, "relations": 0}

    state = _get_state(stats_conn)

    # --- Nodes ---
    cols_node = _table_cols(stats_conn, "node_energy")
    if _has_col(cols_node, "energy") and (_has_col(cols_node, "updated_ts") or _has_col(cols_node, "last_seen_ts")):
        cur = int(state.get("decay_node_cursor") or 0)
        ts_col = "updated_ts" if _has_col(cols_node, "updated_ts") else "last_seen_ts"

        rows = stats_conn.execute(
            f"SELECT node_id, energy, {ts_col} AS t0 FROM node_energy WHERE node_id > ? ORDER BY node_id LIMIT ?",
            (cur, batch),
        ).fetchall()

        if rows:
            updates = []
            last_id = cur
            for r in rows:
                nid = int(r["node_id"])
                e0 = float(r["energy"] or 0.0)
                t0 = int(r["t0"] or 0)
                e1 = _apply_decay(e0, t0, now_ts, params.half_life_sec)
                if abs(e1 - e0) > 1e-9:
                    if _has_col(cols_node, "updated_ts"):
                        updates.append((float(e1), int(now_ts), nid))
                    else:
                        updates.append((float(e1), nid))
                last_id = nid
            if updates:
                if _has_col(cols_node, "updated_ts"):
                    _dbw_executemany("UPDATE node_energy SET energy=?, updated_ts=? WHERE node_id=?", updates, tag="energy_manager.decay.node")
                else:
                    _dbw_executemany("UPDATE node_energy SET energy=? WHERE node_id=?", updates, tag="energy_manager.decay.node")
            _state_update(stats_conn, decay_node_cursor=int(last_id))
            out["nodes"] = len(updates)
        else:
            _state_update(stats_conn, decay_node_cursor=0)

    # --- Relations ---
    cols_rel = _table_cols(stats_conn, "relation_energy")
    if _has_col(cols_rel, "energy") and (_has_col(cols_rel, "updated_ts") or _has_col(cols_rel, "last_seen_ts")):
        cur = int(state.get("decay_rel_cursor") or 0)
        ts_col = "updated_ts" if _has_col(cols_rel, "updated_ts") else "last_seen_ts"

        rows = stats_conn.execute(
            f"SELECT relation_id, energy, {ts_col} AS t0 FROM relation_energy WHERE relation_id > ? ORDER BY relation_id LIMIT ?",
            (cur, batch),
        ).fetchall()

        if rows:
            updates = []
            last_id = cur
            for r in rows:
                rid = int(r["relation_id"])
                e0 = float(r["energy"] or 0.0)
                t0 = int(r["t0"] or 0)
                e1 = _apply_decay(e0, t0, now_ts, params.half_life_sec)
                if abs(e1 - e0) > 1e-9:
                    if _has_col(cols_rel, "updated_ts"):
                        updates.append((float(e1), int(now_ts), rid))
                    else:
                        updates.append((float(e1), rid))
                last_id = rid
            if updates:
                if _has_col(cols_rel, "updated_ts"):
                    _dbw_executemany("UPDATE relation_energy SET energy=?, updated_ts=? WHERE relation_id=?", updates, tag="energy_manager.retro.rel")
                else:
                    _dbw_executemany("UPDATE relation_energy SET energy=? WHERE relation_id=?", updates, tag="energy_manager.retro.rel")
            _state_update(stats_conn, decay_rel_cursor=int(last_id))
            out["relations"] = len(updates)
        else:
            _state_update(stats_conn, decay_rel_cursor=0)

    _state_update(stats_conn, last_decay_ts=int(now_ts))
    return out

def _write_top_cache(stats_conn: sqlite3.Connection, kind: str, payload: Any, now_ts: int) -> None:
    _dbw_exec(
        """
        INSERT INTO energy_top_cache(kind, payload_json, ts)
        VALUES (?, ?, ?)
        ON CONFLICT(kind) DO UPDATE SET
            payload_json=excluded.payload_json,
            ts=excluded.ts
        """,
        (str(kind), json.dumps(_json_sanitize(payload), ensure_ascii=False, allow_nan=False), int(now_ts)),
        tag="energy_manager.top_cache",
    )

def _compute_top_lists(stats_conn: sqlite3.Connection, top_n: int, main_conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    # Top Relations
    top_rel: List[Dict[str, Any]] = []
    rel_rows = stats_conn.execute(
        """
        SELECT relation_id, src_id, dst_id, rel_type, energy, last_seen_ts, seen_count
          FROM relation_energy
      ORDER BY energy DESC
         LIMIT ?
        """,
        (int(top_n),),
    ).fetchall()

    # Optional: Labels für src_id/dst_id (Top-N, kleine IN-Abfrage) – damit UI nicht '?' zeigt
    rel_label_map: Dict[int, Dict[str, str]] = {}
    if main_conn is not None and rel_rows:
        ids: List[int] = []
        for rr in rel_rows:
            sid = int(rr["src_id"] or 0)
            did = int(rr["dst_id"] or 0)
            if sid > 0: ids.append(sid)
            if did > 0: ids.append(did)
        rel_label_map = _labels_map_for_ids(main_conn, ids)

    # Fallback: Wenn relation_energy noch 'kaputt' ist (z.B. src/dst=0 oder rel_type leer),
    # baue Top-Relations direkt aus oroma.db/object_relations (Count-Surrogat).
    if main_conn is not None:
        try:
            bad = 0
            bad_rel = 0
            for rr in rel_rows:
                try:
                    sid = int(rr["src_id"] or 0)
                    did = int(rr["dst_id"] or 0)
                    if sid == 0 and did == 0:
                        bad += 1
                    rels = str(rr["rel_type"] or "").strip()
                    if not rels:
                        bad_rel += 1
                except Exception:
                    bad += 1
                    bad_rel += 1

            if (not rel_rows) or (bad == len(rel_rows)) or (bad_rel == len(rel_rows)):
                now_ts = int((main_conn.execute("SELECT COALESCE(MAX(ts),0) AS m FROM object_relations").fetchone()["m"] or 0))
                if now_ts <= 0:
                    now_ts = _now()
                since_ts = max(0, now_ts - int(OROMA_ENERGY_FALLBACK_WINDOW_DAYS) * 86400)

                fb = main_conn.execute(
                    """
                    SELECT COALESCE(relation,'') AS rel, COUNT(*) AS c
                      FROM object_relations
                     WHERE ts >= ?
                       AND COALESCE(relation,'') != ''
                  GROUP BY rel
                  ORDER BY c DESC
                     LIMIT ?
                    """,
                    (since_ts, int(top_n)),
                ).fetchall()

                top_rel = []
                for fr in fb:
                    rel = str(fr["rel"] or "").strip()
                    c = int(fr["c"] or 0)
                    if not rel:
                        continue
                    top_rel.append(
                        {
                            "id": rel,
                            "relation": rel,
                            "rel_type": rel,
                            "name": rel,
                            "energy": float(c),
                            "count": c,
                            "seen_count": c,
                            "last_seen_ts": now_ts,
                        }
                    )

                # Überspringe relation_energy-Loop unten
                rel_rows = []
        except Exception as e:
            log_suppressed('tools/energy_manager.py:597', exc=e, level=logging.WARNING)
            pass

    for r in rel_rows:
        rel_type_raw = str(r["rel_type"] or "").strip()
        rel_type = rel_type_raw if rel_type_raw else "unknown"

        src_id = int(r["src_id"] or 0)
        dst_id = int(r["dst_id"] or 0)
        src_label = (rel_label_map.get(src_id, {}).get("label") if rel_label_map else "") or ""
        dst_label = (rel_label_map.get(dst_id, {}).get("label") if rel_label_map else "") or ""

        # name/relation/id sind für learning.html wichtig (sonst fällt es auf '?')
        name = f"{src_label or f'id:{src_id}'} -{rel_type}-> {dst_label or f'id:{dst_id}'}"

        top_rel.append({
            "id": int(r["relation_id"] or 0),
            "relation_id": int(r["relation_id"] or 0),
            "rel_type": rel_type,
            "relation": rel_type,
            "src_id": src_id,
            "dst_id": dst_id,
            "src_label": src_label,
            "dst_label": dst_label,
            "name": name,
            "label": name,
            "energy": float(r["energy"] or 0.0),
            "count": int(r["seen_count"] or 0),
            "seen_count": int(r["seen_count"] or 0),
            "last_seen_ts": int(r["last_seen_ts"] or 0),
        })

    # Top Nodes
    top_nodes: List[Dict[str, Any]] = []
    node_rows = stats_conn.execute(
        """
        SELECT node_id, energy, last_seen_ts, seen_count
          FROM node_energy
      ORDER BY energy DESC
         LIMIT ?
        """,
        (int(top_n),),
    ).fetchall()

    # Optional: Labels für node_id (Top-N) – damit UI direkt Labels anzeigen kann
    node_label_map: Dict[int, Dict[str, str]] = {}
    if main_conn is not None and node_rows:
        ids = [int(rr["node_id"] or 0) for rr in node_rows if int(rr["node_id"] or 0) > 0]
        node_label_map = _labels_map_for_ids(main_conn, ids)

    for r in node_rows:
        nid = int(r["node_id"] or 0)
        item = {
            "id": nid,
            "node_id": nid,
            "energy": float(r["energy"] or 0.0),
            "last_seen_ts": int(r["last_seen_ts"] or 0),
            "seen_count": int(r["seen_count"] or 0),
            "count": int(r["seen_count"] or 0),
        }
        if node_label_map and nid in node_label_map:
            lab = node_label_map[nid].get("label", "")
            kind = node_label_map[nid].get("kind", "")
            if lab:
                item["label"] = lab
                item["name"] = lab
            if kind:
                item["kind"] = kind
        top_nodes.append(item)

    return {
        "top_relations": top_rel,
        "top_nodes": top_nodes,
    }
def run_once(
    oroma_db_path: str,
    stats_db_path: str,
    batch_limit: int,
    top_n: int,
    params: EnergyParams,
) -> Dict[str, Any]:
    now_ts = _now()

    main_conn = _connect_sqlite(oroma_db_path, readonly=True, timeout_sec=30.0)
    if not _dbw_required():
        raise RuntimeError("DBWriter required (OROMA_DBW_ENABLE=1) but not available/enabled")

    stats_conn = _connect_sqlite(stats_db_path, readonly=True, timeout_sec=30.0)

    try:
        _ensure_schema(stats_conn)

        last_ts = _get_state_last_ts(stats_conn)

        # object_relations columns erkennen
        src_col, dst_col, rel_col = _detect_relation_columns(main_conn)

        # neue Relations holen (seit last_ts)
        # NOTE: relation_id ist die PK in object_relations (id)
        rel_events = main_conn.execute(
            f"""
            SELECT id AS relation_id,
                   ts,
                   {src_col} AS src_id,
                   {dst_col} AS dst_id,
                   {rel_col} AS rel
              FROM object_relations
             WHERE ts > ?
          ORDER BY ts ASC
             LIMIT ?
            """,
            (int(last_ts), int(batch_limit)),
        ).fetchall()

        fetched = len(rel_events)

        # Updates anwenden
        updated_rel = 0
        updated_nodes = 0

        for ev in rel_events:
            relation_id = _safe_int(ev["relation_id"])
            src_id = _safe_int(ev["src_id"])
            dst_id = _safe_int(ev["dst_id"])
            rel_ts = _safe_int(ev["ts"])

            rel_type = str(ev["rel"] or "").strip()
            if not rel_type:
                rel_type = "unknown"

            if src_id > 0:
                _upsert_node(stats_conn, src_id, rel_ts or now_ts, params)
                updated_nodes += 1

            if dst_id > 0:
                _upsert_node(stats_conn, dst_id, rel_ts or now_ts, params)
                updated_nodes += 1

            _upsert_relation(stats_conn, relation_id, src_id, dst_id, rel_type, rel_ts or now_ts, params)
            updated_rel += 1


        # last_ts fortschreiben
        if rel_events:
            max_ts = max(int(ev["ts"] or 0) for ev in rel_events)
            if max_ts > last_ts:
                _set_state_last_ts(stats_conn, max_ts)
        # Retro-Weights (einmalig, inkrementell): bestehende relation_energy skalieren nach rel_type
        retro_updated = _retro_apply_relation_weights(stats_conn, now_ts)

        # Globaler Decay-Sweep (inkrementell): Energy fällt auch ohne erneute Sichtung
        decay_res = _decay_sweep_step(stats_conn, now_ts, params)

        # Top-Cache schreiben (mit Label-Hydration + Relation-Fallback)
        tops = _compute_top_lists(stats_conn, top_n, main_conn=main_conn)
        top_rel = tops.get("top_relations", []) or []
        top_nodes = tops.get("top_nodes", []) or []

        _write_top_cache(stats_conn, "relations", top_rel, now_ts)
        # Kompatibilität: neue UI erwartet kind='nodes', ältere UI nutzt kind='objects'
        _write_top_cache(stats_conn, "nodes", top_nodes, now_ts)
        _write_top_cache(stats_conn, "objects", top_nodes, now_ts)

        return {
            "ok": True,
            "ts_run": now_ts,
            "db_main": oroma_db_path,
            "db_stats": stats_db_path,
            "batch_limit": int(batch_limit),
            "top_n": int(top_n),
            "fetched_relations": int(fetched),
            "updated_relations": int(updated_rel),
            "updated_nodes": int(updated_nodes),
            "retro_rel_reweighted": int(retro_updated),
            "decay_sweep_nodes": int(decay_res.get("nodes",0)),
            "decay_sweep_relations": int(decay_res.get("relations",0)),
            "note": "no new relations" if fetched == 0 else "",
        }

    finally:
        try:
            main_conn.close()
        except Exception as e:
            log_suppressed('tools/energy_manager.py:771', exc=e, level=logging.WARNING)
            pass
        try:
            stats_conn.close()
        except Exception as e:
            log_suppressed('tools/energy_manager.py:776', exc=e, level=logging.WARNING)
            pass


def _parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ORÓMA Energy Manager (stats.db producer)")
    ap.add_argument("--oroma-db", default=OROMA_DB_PATH, help="Pfad zur oroma.db (read-only)")
    ap.add_argument("--stats-db", default=OROMA_STATS_DB_PATH, help="Pfad zur stats.db (read-only local; writes via DBWriter)")
    ap.add_argument("--once", action="store_true", help="Einmal ausführen und beenden")
    ap.add_argument("--batch-limit", type=int, default=5000, help="Max. object_relations pro Run")
    ap.add_argument("--top-n", type=int, default=20, help="Top-N Einträge im Cache")
    ap.add_argument("--half-life-sec", type=float, default=3600.0 * 24.0 * 7.0, help="Half-life für Energy-Decay")
    ap.add_argument("--inc-node", type=float, default=1.0, help="Energy increment pro Node-Event")
    ap.add_argument("--inc-rel", type=float, default=2.0, help="Energy increment pro Relation-Event")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_basic_logging()
    args = _parse_args(argv or sys.argv[1:])

    params = EnergyParams(
        half_life_sec=float(args.half_life_sec),
        inc_node=float(args.inc_node),
        inc_rel=float(args.inc_rel),
    )

    try:
        res = run_once(
            oroma_db_path=str(args.oroma_db),
            stats_db_path=str(args.stats_db),
            batch_limit=int(args.batch_limit),
            top_n=int(args.top_n),
            params=params,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        # Niemals still scheitern: sowohl stderr als auch ORÓMA-Log.
        try:
            log_suppressed('tools/energy_manager.py:main_failed', exc=e, level=logging.ERROR)
        except Exception:
            pass
        try:
            import traceback as _tb
            print("[energy_manager] ERROR:", repr(e), file=sys.stderr)
            _tb.print_exc()
        except Exception:
            pass
        res = {"ok": False, "error": repr(e)}
        try:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except Exception:
            pass
        return 2



if __name__ == "__main__":
    raise SystemExit(main())
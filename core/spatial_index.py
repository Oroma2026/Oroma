#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/spatial_index.py
# Projekt: ORÓMA
# Version: v3.5
# Stand:   2025-09-21
#
# Zweck:
#   Raumrepräsentation mit SQLite-Persistenz:
#     - Wegpunkte (Nodes) und Relationen (Edges)
#     - Distanz- und Nachbarschaftsberechnung
#     - Speicherung & Query von Positionsdaten
#
# Anwendungsfälle:
#   - Spiele (Hide&Seek, Maze): Wegpunkte, Abstände, Pfadfindung
#   - Wrapper (z. B. Kamera, PiCar): relative Positionen, "visited areas"
#   - Langzeitgedächtnis: Navigation im Snap-/Token-Raum
#
# Neu in v3.5:
#   - Korrigierter Basis-Pfad (/opt/ai/oroma)
#   - Optionales KNN-Backend mit Annoy (falls installiert)
#     → pip install annoy
#     Annoy = Approximate Nearest Neighbors in C++/Python
#     Vorteil: sehr schneller KNN-Suchindex, niedriger RAM-Verbrauch
#
# Abhängigkeiten:
#   - Standardbibliothek (sqlite3, math, logging)
#   - Optional: Annoy (empfohlen bei >1000 Punkten)
# =============================================================================

from __future__ import annotations
import os, sys, time, math
from typing import Any, Dict, List, Optional, Tuple
from core.log_guard import log_suppressed
import logging

# Optional: DBWriter (Stufe C). SpatialIndex schreibt in SQLite; im Produktivbetrieb
# darf das den globalen Single-Writer nicht umgehen.
try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

# Projektbasis
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    from core import sql_manager
except Exception:
    # Minimal-Stub (nur für Offline-Tests)
    import sqlite3 as _sqlite3
    class _SQLStub:
        def get_conn(self):
            db = os.environ.get("OROMA_SPATIAL_TMP_DB", ":memory:")
            c = _sqlite3.connect(db, check_same_thread=False)
            c.row_factory = lambda cur, row: {d[0]: row[i] for i, d in enumerate(cur.description)}
            return c
    sql_manager = _SQLStub()  # type: ignore

# Optional: Annoy-Backend
try:
    from annoy import AnnoyIndex
    HAVE_ANNOY = True
except ImportError:
    HAVE_ANNOY = False

BIDIRECTIONAL = True
_ANNOY_INDEX = None
_ANNOY_BUILT = False

_SP_SCHEMA_POINTS = """
CREATE TABLE IF NOT EXISTS spatial_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL,
    label TEXT
);
"""
_SP_SCHEMA_EDGES = """
CREATE TABLE IF NOT EXISTS spatial_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    a_id INTEGER NOT NULL,
    b_id INTEGER NOT NULL,
    dist REAL NOT NULL,
    bucket INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    UNIQUE(a_id, b_id)
);
"""
_SP_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_sp_ts ON spatial_points(ts)",
    "CREATE INDEX IF NOT EXISTS idx_se_a ON spatial_edges(a_id)",
    "CREATE INDEX IF NOT EXISTS idx_se_b ON spatial_edges(b_id)",
]

def _dbw_enabled() -> bool:
    return (db_writer_client is not None) and (os.environ.get("OROMA_DBW_ENABLE", "").strip().lower() in ("1","true","yes","on"))

def _sp_db() -> str:
    # SpatialIndex speichert standardmäßig in oroma.db (Schema im selben File).
    return os.environ.get("OROMA_SPATIAL_DB", "oroma").strip() or "oroma"

def ensure_schema() -> None:
    if _dbw_enabled():
        stmts = [(_SP_SCHEMA_POINTS, []), (_SP_SCHEMA_EDGES, [])] + [(s, []) for s in _SP_IDX]
        try:
            db_writer_client.transaction(
                stmts,
                tag="spatial_index.ensure_schema",
                priority="low",
                timeout_ms=60000,
                db=_sp_db(),
            )
            return
        except Exception as e:
            log_suppressed(logging.getLogger(__name__), key="core.spatial_index.dbw.schema.fail", exc=e, msg="DBWriter schema failed", level=logging.WARNING)

    conn = sql_manager.get_conn()
    with conn:
        conn.execute(_SP_SCHEMA_POINTS)
        conn.execute(_SP_SCHEMA_EDGES)
        for stmt in _SP_IDX:
            try:
                conn.execute(stmt)
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.spatial_index.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )

def add_point(x: float, y: float, z: Optional[float] = None, label: Optional[str] = None) -> int:
    ensure_schema()
    ts = int(time.time())
    if _dbw_enabled():
        pid = int(db_writer_client.exec_lastrowid(
            "INSERT INTO spatial_points (ts, x, y, z, label) VALUES (?, ?, ?, ?, ?)",
            [ts, float(x), float(y), z if z is None else float(z), label],
            tag="spatial_index.add_point",
            priority="normal",
            timeout_ms=60000,
            db=_sp_db(),
        ))
    else:
        conn = sql_manager.get_conn()
        with conn:
            cur = conn.execute(
                "INSERT INTO spatial_points (ts, x, y, z, label) VALUES (?, ?, ?, ?, ?)",
                (ts, float(x), float(y), z if z is None else float(z), label),
            )
            pid = int(cur.lastrowid)
    _invalidate_index()
    return pid

def _dist2(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

def _invalidate_index():
    global _ANNOY_INDEX, _ANNOY_BUILT
    _ANNOY_INDEX = None
    _ANNOY_BUILT = False

def _build_index():
    """Annoy-Index neu aufbauen (falls installiert)."""
    global _ANNOY_INDEX, _ANNOY_BUILT
    if not HAVE_ANNOY:
        return
    conn = sql_manager.get_conn()
    cur = conn.execute("SELECT id, x, y FROM spatial_points")
    rows = cur.fetchall() or []
    if not rows:
        return
    index = AnnoyIndex(2, "euclidean")
    for r in rows:
        index.add_item(int(r["id"]), [float(r["x"]), float(r["y"])])
    index.build(10)  # 10 Bäume = guter Kompromiss
    _ANNOY_INDEX = index
    _ANNOY_BUILT = True

def nearest(x: float, y: float, k: int = 5, max_dist: Optional[float] = None,
            max_points: int = 5000) -> List[Tuple[int, float]]:
    """
    KNN-Suche nach nächsten Punkten.
      - Wenn Annoy installiert & genug Punkte: Annoy nutzen
      - Sonst: einfacher linearer Scan (Fallback)
    """
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.execute("SELECT COUNT(*) AS c FROM spatial_points")
    total = int(cur.fetchone()["c"])

    if HAVE_ANNOY and total > 1000:
        global _ANNOY_BUILT
        if not _ANNOY_BUILT:
            _build_index()
        if _ANNOY_INDEX:
            idxs = _ANNOY_INDEX.get_nns_by_vector([x, y], k, include_distances=True)
            return list(zip(idxs[0], idxs[1]))

    # Fallback: lineare Suche
    cur = conn.execute("SELECT id, x, y FROM spatial_points ORDER BY id DESC LIMIT ?", (int(max_points),))
    cand = [(int(r["id"]), float(r["x"]), float(r["y"])) for r in cur.fetchall() or []]
    qs = (float(x), float(y))
    scored: List[Tuple[int, float]] = []
    for pid, px, py in cand:
        d = _dist2(qs, (px, py))
        if max_dist is not None and d > max_dist:
            continue
        scored.append((pid, d))
    scored.sort(key=lambda t: t[1])
    return scored[: max(1, int(k))]

def bucket_distance(dist: float) -> int:
    d = float(dist)
    if d <= 0.5: return 0
    if d <= 1.5: return 1
    if d <= 3.0: return 2
    if d <= 6.0: return 3
    return 4

def relate(a_id: int, b_id: int) -> int:
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.execute("SELECT id, x, y FROM spatial_points WHERE id IN (?,?)", (int(a_id), int(b_id)))
    rows = cur.fetchall() or []
    if len(rows) != 2:
        raise ValueError("relate(): Punkte nicht gefunden")
    pts = {int(r["id"]): (float(r["x"]), float(r["y"])) for r in rows}
    d = _dist2(pts[int(a_id)], pts[int(b_id)])
    buck = bucket_distance(d)
    ts = int(time.time())
    ids: List[int] = []
    sql_upsert = (
        "INSERT INTO spatial_edges (a_id, b_id, dist, bucket, ts) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(a_id, b_id) DO UPDATE SET dist=excluded.dist, bucket=excluded.bucket, ts=excluded.ts"
    )
    pairs = [(a_id, b_id)] + ([(b_id, a_id)] if BIDIRECTIONAL else [])
    if _dbw_enabled():
        for (src, dst) in pairs:
            db_writer_client.exec_write(
                sql_upsert,
                [int(src), int(dst), float(d), int(buck), ts],
                tag="spatial_index.relate",
                priority="normal",
                timeout_ms=60000,
                db=_sp_db(),
            )
            # id lookup bleibt lokal (read)
            cur2 = conn.execute("SELECT id FROM spatial_edges WHERE a_id=? AND b_id=?", (int(src), int(dst)))
            got = cur2.fetchone()
            if got:
                ids.append(int(got["id"]))
    else:
        with conn:
            for (src, dst) in pairs:
                conn.execute(sql_upsert, (int(src), int(dst), float(d), int(buck), ts))
                cur2 = conn.execute("SELECT id FROM spatial_edges WHERE a_id=? AND b_id=?", (int(src), int(dst)))
                got = cur2.fetchone()
                if got:
                    ids.append(int(got["id"]))
    return ids[0] if ids else -1

def edges_for(node_id: int, k: int = 10) -> List[Tuple[int, float, int]]:
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.execute(
        "SELECT b_id AS oid, dist, bucket FROM spatial_edges WHERE a_id=? ORDER BY dist ASC LIMIT ?",
        (int(node_id), int(k))
    )
    return [(int(r["oid"]), float(r["dist"]), int(r["bucket"])) for r in cur.fetchall() or []]

if __name__ == "__main__":  # Quick self-check
    a = add_point(0.0, 0.0, label="origin")
    b = add_point(1.0, 0.7, label="p1")
    e = relate(a, b)
    print("edge id(s):", e)
    print("nearest:", nearest(0.9, 0.7, k=2))
    print("edges_for(a):", edges_for(a))
    print("edges_for(b):", edges_for(b))
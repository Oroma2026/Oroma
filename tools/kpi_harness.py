#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/kpi_harness.py
# Projekt: ORÓMA
# Version: v1.1 – KPI Nightly (DB-Lock robust: busy_timeout + retry/backoff)
# Stand:   2025-12-23
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Aggregiert jeden Lauf harte Lern-KPIs aus der DB und schreibt sie als
#   Zeitreihe in die Tabelle `kpi_snapshots`. Headless, robust, idempotent.
#
#   • kpi:dedupe_rate_24h           → Anteil Dedupe gegenüber New-Inserts (24h)
#   • kpi:export_yield_30d          → Marked/Considered bei Export (30d)
#   • kpi:crossmodal_recall10_24h   → Audio→Video Recall@10 (24h)
#   • kpi:ttt_winrate_7d            → Winrate vs. Heuristik (7d)
#
# Annahmen
# ────────
#   • Tabelle `metrics` existiert (über sql_manager.ensure_schema).
#   • Tabelle `snapchains` enthält JSON-Blobs mit snaps[].features[0:9].
#   • Audio/Video-Token verwenden origin = 'audio/token' / 'vision/token'.
#
# ENV
# ───
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_DB_TIMEOUT_SEC=30        (sqlite3 connect timeout; Default 30)
#   OROMA_DB_BUSY_TIMEOUT_MS=30000 (PRAGMA busy_timeout; Default 30000)
#   OROMA_DB_LOCK_RETRY_SEC=90     (Gesamt-Retry-Zeitfenster bei "database is locked")
#
# Hinweis zum "database is locked"
# -------------------------------
# ORÓMA schreibt parallel (Engine/DreamWorker/Stats/Forget/Export). Auch mit WAL
# gibt es immer nur *einen* Writer-Commit gleichzeitig. Dieses Tool ist daher so
# gebaut, dass es bei "database is locked" nicht sofort fehlschlägt, sondern
# kontrolliert wartet (busy_timeout) und zusätzlich mit Backoff neu versucht.
#
# Systemd (optional)
# ──────────────────
#   ExecStart=/usr/bin/python3 -m tools.kpi_harness
#   Timer: täglich 03:10
# =============================================================================

import os
import time
import json
import random
import sqlite3
import numpy as np
import logging
from core.log_guard import log_suppressed


DB_PATH = os.getenv("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")

# Cache: Spaltenname in Tabelle 'metrics' kann historisch 'key' oder 'name' heißen.
_METRICS_COL_CACHE = None  # type: ignore


def _connect_db() -> sqlite3.Connection:
    """Erzeugt eine Connection mit robusten Lock-Settings.

    Wichtig:
    - sqlite3.connect(timeout=...) ist bereits ein Busy-Timeout.
    - Zusätzlich setzen wir PRAGMA busy_timeout (ms), weil manche Umgebungen
      das zuverlässiger auswerten.
    - WAL wird NICHT hier erzwungen, weil ORÓMA das global über ENV/Service
      steuert (OROMA_DB_WAL=1 via core/sql_manager). Wenn die DB bereits in WAL
      ist, profitiert auch diese Verbindung automatisch.
    """
    timeout_sec = int(os.getenv("OROMA_DB_TIMEOUT_SEC", "30") or "30")
    conn = sqlite3.connect(DB_PATH, timeout=float(timeout_sec), check_same_thread=False)
    try:
        busy_ms = int(os.getenv("OROMA_DB_BUSY_TIMEOUT_MS", "30000") or "30000")
        conn.execute(f"PRAGMA busy_timeout={busy_ms}")
    except Exception as e:
        log_suppressed('tools/kpi_harness.py:77', exc=e, level=logging.WARNING)
        pass
    # Foreign-Keys hier bewusst aus (Tool-only writes), um Edge-Cases zu vermeiden.
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
    except Exception as e:
        log_suppressed('tools/kpi_harness.py:83', exc=e, level=logging.WARNING)
        pass
    return conn


def _is_lock_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return "database is locked" in msg or "database is busy" in msg or "locked" in msg


def _run_with_lock_retry(fn, total_retry_sec: int) -> None:
    """Führt fn() aus und retried kontrolliert bei SQLite-Locks.

    - total_retry_sec: Gesamtzeitfenster (Default über ENV: OROMA_DB_LOCK_RETRY_SEC)
    - Backoff: startet klein, capped bei 5s

    Design:
    - wir öffnen die DB *pro Versuch neu*, damit keine halboffenen Transaktionen
      oder Cursor in einem Fehlerzustand hängen bleiben.
    """
    t0 = time.time()
    delay = 0.25
    last_err = None
    while True:
        try:
            fn()
            return
        except sqlite3.OperationalError as e:
            last_err = e
            if not _is_lock_error(e):
                raise
            if (time.time() - t0) >= float(total_retry_sec):
                raise
            time.sleep(delay)
            delay = min(delay * 1.7, 5.0)
        except Exception as e:
            # Nicht stumm schlucken: KPI Harness soll echte Fehler sichtbar machen.
            raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Legt die KPI-Tabelle an (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kpi_snapshots(
            ts   INTEGER NOT NULL,
            key  TEXT    NOT NULL,
            value REAL,
            meta  TEXT,
            PRIMARY KEY(ts, key)
        );
        """
    )


def _metrics_key_column(conn: sqlite3.Connection) -> str:
    """Ermittelt den Spaltennamen in metrics ('key' vs 'name').

    Hintergrund:
    - In ORÓMA-Core ist die Time-Series historisch als metrics(key, ts, value) angelegt.
    - Einige Tools nutzten früher 'name' (legacy).
    - Wir unterstützen beides, ohne Schema zu erraten oder zu migrieren.
    """
    global _METRICS_COL_CACHE
    if _METRICS_COL_CACHE in ("key", "name"):
        return _METRICS_COL_CACHE
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)").fetchall()]
        if "key" in cols:
            _METRICS_COL_CACHE = "key"
            return "key"
        if "name" in cols:
            _METRICS_COL_CACHE = "name"
            return "name"
    except Exception as e:
        log_suppressed('tools/kpi_harness.py:158', exc=e, level=logging.WARNING)
        pass
    # Fallback: 'key' ist in ORÓMA-Core der Standard
    _METRICS_COL_CACHE = "key"
    return "key"


def _sum_metric(conn: sqlite3.Connection, name: str, since_ts: int) -> float:
    """Summiert metrics.value ab since_ts; bei Fehlern 0.0.

    name ist der *Serien-Key*, z.B. 'kpi:dedupe_event'.
    """
    try:
        col = _metrics_key_column(conn)
        row = conn.execute(
            f"SELECT SUM(value) FROM metrics WHERE {col}=? AND ts>=?",
            (name, since_ts),
        ).fetchone()
        return float(row[0] or 0.0)
    except Exception:
        return 0.0


def kpi_dedupe_rate_24h(conn: sqlite3.Connection, since_ts: int) -> float:
    d = _sum_metric(conn, "kpi:dedupe_event", since_ts)
    n = _sum_metric(conn, "kpi:new_chain_event", since_ts)
    return d / (d + n) if (d + n) > 0 else 0.0


def kpi_export_yield_30d(conn: sqlite3.Connection, since_ts: int) -> float:
    marked = _sum_metric(conn, "kpi:export_marked", since_ts)
    considered = _sum_metric(conn, "kpi:export_considered", since_ts)
    return marked / considered if considered > 0 else 0.0


def _fetch_token_vectors(
    conn: sqlite3.Connection, origin: str, since_ts: int, limit: int = 50000
):
    """Liest (ts, vektor) Paare aus snapchains.blob (nur aktive, nicht compressed)."""
    rows = conn.execute(
        """
        SELECT ts, blob
          FROM snapchains
         WHERE origin = ?
           AND ts >= ?
           AND (status IS NULL OR status!='compressed')
         LIMIT ?
        """,
        (origin, since_ts, limit),
    ).fetchall()

    out = []
    for ts, blob in rows:
        try:
            d = json.loads(blob)
            # Erwartet: d["snaps"][0]["features"] (mind. 9D)
            v = d.get("snaps", [{}])[0].get("features", [])[:9]
            if v:
                out.append((int(ts), np.array(v, dtype=float)))
        except Exception as e:
            log_suppressed('tools/kpi_harness.py:218', exc=e, level=logging.WARNING)
            pass
    # nach Zeit sortieren (ts)
    out.sort(key=lambda x: x[0])
    return out


def kpi_crossmodal_recall10_24h(conn: sqlite3.Connection, since_ts: int):
    """Einfaches Cross-Modal Recall@10 (Audio→Video) über zeitnahe Paare."""
    A = _fetch_token_vectors(conn, "audio/token", since_ts)
    V = _fetch_token_vectors(conn, "vision/token", since_ts)
    if not A or not V:
        return 0.0, {"pairs": 0, "sample": 0}

    # Paarbildung |Δt|<=2s
    i = j = 0
    pairs = []
    while i < len(A) and j < len(V):
        dt = V[j][0] - A[i][0]
        if abs(dt) <= 2:
            pairs.append((A[i][1], V[j][1]))
            i += 1
            j += 1
        elif dt > 2:
            i += 1
        else:
            j += 1
        if len(pairs) >= 1000:
            break

    if len(pairs) < 20:
        return 0.0, {"pairs": len(pairs), "sample": 0}

    Xa = np.stack([p[0] for p in pairs])  # N×9
    Xv = np.stack([p[1] for p in pairs])  # N×9

    # Lineare Projektion (leicht) per Least Squares
    W, *_ = np.linalg.lstsq(Xa, Xv, rcond=None)

    # Normalisierte Datenbank-Matrix der Vision-Vektoren
    V_all = np.stack([vec for _, vec in V])
    V_all = V_all / (np.linalg.norm(V_all, axis=1, keepdims=True) + 1e-9)

    # Stichprobe aus Audio-Vektoren
    sample = random.sample(A, min(100, len(A)))
    hits = 0
    for ts_a, va in sample:
        qa = va @ W
        qa = qa / (np.linalg.norm(qa) + 1e-9)
        sims = V_all @ qa
        topk_idx = np.argsort(sims)[-10:]
        ok = any(abs(V[k][0] - ts_a) <= 2 for k in topk_idx)
        hits += 1 if ok else 0

    recall = hits / len(sample) if sample else 0.0
    meta = {"pairs": len(pairs), "sample": len(sample)}
    return float(recall), meta


def kpi_ttt_winrate_7d(conn: sqlite3.Connection, since_ts: int) -> float:
    """Winrate vs. Heuristik (falls games_ttt vorhanden)."""
    try:
        w = conn.execute(
            "SELECT COUNT(*) FROM games_ttt WHERE ts>=? AND result='win_vs_heur'",
            (since_ts,),
        ).fetchone()[0]
        l = conn.execute(
            "SELECT COUNT(*) FROM games_ttt WHERE ts>=? AND result='loss_vs_heur'",
            (since_ts,),
        ).fetchone()[0]
        w = int(w or 0)
        l = int(l or 0)
        return w / (w + l) if (w + l) > 0 else 0.0
    except Exception:
        return 0.0


def main() -> None:
    now = int(time.time())
    d1 = now - 24 * 3600
    d7 = now - 7 * 24 * 3600
    d30 = now - 30 * 24 * 3600

    total_retry_sec = int(os.getenv("OROMA_DB_LOCK_RETRY_SEC", "90") or "90")

    def _do_once() -> None:
        conn = _connect_db()
        try:
            ensure_schema(conn)

            rows = []

            dedupe = kpi_dedupe_rate_24h(conn, d1)
            rows.append((now, "kpi:dedupe_rate_24h", dedupe, None))

            export_yield = kpi_export_yield_30d(conn, d30)
            rows.append((now, "kpi:export_yield_30d", export_yield, None))

            rec10, meta = kpi_crossmodal_recall10_24h(conn, d1)
            rows.append((now, "kpi:crossmodal_recall10_24h", rec10, json.dumps(meta)))

            winrate = kpi_ttt_winrate_7d(conn, d7)
            rows.append((now, "kpi:ttt_winrate_7d", winrate, None))

            # Kurze, gebatchte Write-Phase
            conn.executemany(
                "INSERT OR REPLACE INTO kpi_snapshots(ts,key,value,meta) VALUES (?,?,?,?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    _run_with_lock_retry(_do_once, total_retry_sec)


if __name__ == "__main__":
    main()
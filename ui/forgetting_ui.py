#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/forgetting_ui.py
# Projekt: ORÓMA – Headless UI
# Version: v3.7.2 (PUBLIC-Flag, Auto-Token, robustes Rendering)
# Stand:   2025-11-01
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
# Forgetting-/Kompressions-Dashboard:
#   • HTML  /forgetting             → immer frei (nie Token nötig)
#   • API   /forgetting/api/state   → entweder PUBLIC (kein Token) ODER Token
#
# API-Schutz (Gate)
# ─────────────────
#   • PUBLIC, wenn EINES der Flags gesetzt ist:
#       OROMA_FORGETTING_PUBLIC=1         (feature-spezifisch)
#       OROMA_UI_PUBLIC=1                 (globales UI-Öffnen)
#   • Sonst Token nötig, wenn OROMA_UI_TOKEN gesetzt ist.
#     Akzeptierte Quellen: X-OROMA-TOKEN | Authorization: Bearer <t>
#                          ?token=<t>    | Cookie OROMA_UI_TOKEN
#
# Antwort
# ───────
#   {"ok":true, "ts": int, "avg_quality": float, "avg_weight": float,
#    "n_active": int, "n_compressed": int, "n_meta": int,
#    "counts_mode": str, "counts_window": int, "compression_rate": float}
#   • Alle API-Antworten mit: Cache-Control: no-store
# =============================================================================

from __future__ import annotations
import os
import time
import logging
import sqlite3
from core import db_writer_client as _dbw
import sys
import math
import faulthandler
from typing import Optional, Any
from flask import Blueprint, render_template, jsonify, request

# Core-DB
from core import sql_manager

bp = Blueprint("forgetting", __name__, url_prefix="/forgetting")




@bp.get("/api/ping")
def api_ping():
    """Healthcheck for forgetting blueprint (debugging helper).

    This endpoint must NEVER touch the DB and must always return immediately.
    """
    return _json({
        "ok": True,
        "pong": "forgetting",
        "path": request.path,
        "method": request.method,
        "ts": int(time.time()),
    })

# -----------------------------------------------------------------------------
# In-process cache for /forgetting/api/state
# -----------------------------------------------------------------------------
# The forgetting badge is polled periodically by ui/static/scripts.js.
# On large databases, full-table aggregates (e.g. AVG(quality) over all snapchains)
# can become expensive and can saturate CPU / I/O.
#
# We keep a tiny TTL cache to prevent stampedes and we compute avg_quality from a
# recent window (last N chains) rather than scanning the entire table.
# This keeps the UI responsive while still being representative for "current" state.
#
# NOTE: This cache is per-process.
_STATE_CACHE = {
    "ts": 0,
    "payload": None,
}
_STATE_CACHE_TTL_SEC = 10
_STATE_AVG_WINDOW = 50000  # last N snapchains for avg_quality
# Default size for *count* window (active/compressed) used as a stable reference
# without any DB-wide COUNT(*) scans.
COUNTS_WINDOW_DEFAULT = 200000
log = logging.getLogger("oroma.ui.forgetting")
log.setLevel(logging.INFO)

# ------------------------------ Config / Gate -------------------------------

def _public_api_enabled() -> bool:
    """API ohne Token, wenn ein PUBLIC-Flag aktiv ist."""
    val = (os.environ.get("OROMA_FORGETTING_PUBLIC", "").strip().lower())
    if val in ("1", "true", "yes", "on"):
        return True
    g = (os.environ.get("OROMA_UI_PUBLIC", "").strip().lower())
    return g in ("1", "true", "yes", "on")

def _cfg_token() -> str:
    """Konfigurierter UI-Token (leer → kein Token vorausgesetzt)."""
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token() -> Optional[str]:
    # 1) Header
    t = request.headers.get("X-OROMA-TOKEN")
    if t:
        return t.strip()
    # 2) Bearer
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 3) Query
    q = request.args.get("token")
    if q:
        return q.strip()
    # 4) Cookie
    c = request.cookies.get("OROMA_UI_TOKEN")
    if c:
        return c.strip()
    return None

def _token_valid_for_api() -> bool:
    """API-Gate: PUBLIC-Flag ODER korrekter Token (falls konfiguriert)."""
    if _public_api_enabled():
        return True
    cfg = _cfg_token()
    if not cfg:
        # kein Token konfiguriert → offen
        return True
    incoming = _extract_token()
    ok = (incoming == cfg)
    if not ok:
        log.warning("Forgetting-API Tokenprüfung fehlgeschlagen (remote=%s path=%s)",
                    request.remote_addr, request.path)
    return ok

def _json(data: Any, status: int = 200):
    resp = jsonify(data)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp

def _json_error(msg: str, status: int = 400):
    return _json({"ok": False, "error": msg}, status)

# Nur API schützen – HTML bleibt frei
@bp.before_request
def _mw_api_only():
    p = request.path or ""
    if p.startswith("/forgetting/api"):
        if not _token_valid_for_api():
            return _json_error("Unauthorized", 401)
    return None

# ------------------------------ Helpers --------------------------------------

def _get_val(row, key: str, idx: int, default):
    """Row sicher auslesen (sqlite3.Row | dict | tuple)."""
    if row is None:
        return default
    try:
        # dict-row (sql_manager) oder sqlite3.Row
        if isinstance(row, dict):
            v = row.get(key, default)
        elif hasattr(row, "keys") and not hasattr(row, "get"):
            # sqlite3.Row: index via column name
            try:
                v = row[key]
            except Exception:
                v = default
        elif hasattr(row, "keys") or isinstance(row, dict):
            v = row.get(key, default)  # type: ignore[call-arg]
        else:
            v = row[idx]
        return default if v is None else v
    except Exception:
        return default


def _ui_conn_ro(timeout_sec: float = 1.0, busy_timeout_ms: int = 1000) -> sqlite3.Connection:
    """UI-sichere Read-Only Connection.

    Warum nicht sql_manager.get_conn()?
      - sql_manager ist absichtlich *writer-robust* (busy_timeout oft 60s).
      - UI-Endpunkte (wie /forgetting/api/state) dürfen niemals so lange blockieren,
        sonst haengen Browser & curl.

    Strategie:
      - SQLite URI mode=ro (read-only)
      - kurzer timeout + kurzer PRAGMA busy_timeout
      - dict row_factory (kompatibel zu bestehender _get_val Logik)
    """
    db_path = sql_manager.get_db_path()
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        timeout=float(timeout_sec),
        check_same_thread=False,
    )
    conn.row_factory = lambda cur, row: {col[0]: row[i] for i, col in enumerate(cur.description)}
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass
    return conn

# ------------------------------ HTML -----------------------------------------

@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
def page():
    # Token/Flags optional ins Template injizieren (JS nutzt es, muss aber nicht)
    return render_template(
        "forgetting.html",
        ui_token=_cfg_token(),
        public=_public_api_enabled()
    )

# ------------------------------ API ------------------------------------------

@bp.route("/api/state", methods=["GET"])
def api_state():
    """Kurzstatus für die Badge/UI.

    Ziel: Der Endpoint darf NIE "hängen".
    - Verwendet eine UI-sichere RO-Connection (kurzer timeout)
    - Vermeidet Full-Table-Scans (window-basierte Statistik)
    - Liefert bei Busy/Timeout schnell eine sichtbare Fehlantwort (503)
    """
    # Watchdog: falls dieser Handler >3s blockiert, dumpen wir Thread-Stacks
    # in stderr/service.err.log (sehr hilfreich bei seltenen Lockups).
    try:
        faulthandler.dump_traceback_later(3.0, repeat=False)
    except Exception:
        pass

    def _fh_cancel():
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass

    # Fast path: cached result
    now = time.time()
    cached = _STATE_CACHE.get("payload")
    ts = float(_STATE_CACHE.get("ts") or 0)
    if cached is not None and (now - ts) < float(_STATE_CACHE_TTL_SEC):
        _fh_cancel()
        return _json(cached)

    # Window-Größen
    # -------------
    # /api/state muss *immer* schnell antworten. In der Praxis kann ein großes
    # LIMIT (z.B. 200k) unter Storage-Last oder bei parallelen Threads zu langen
    # Laufzeiten führen (curl: "0 bytes received").
    #
    # Strategie:
    #   - primary: großes Fenster (stabilere Raten)
    #   - fallback: kleineres Fenster, wenn die Query > soft_limit dauert
    #
    # Transparenz: die UI bekommt counts_window + counts_mode.
    win_avg = int(_STATE_AVG_WINDOW)
    win_counts_primary = max(win_avg, 200000)
    win_counts_fallback = max(win_avg, 50000)
    soft_limit_sec = 0.6

    conn = None
    try:
        conn = _ui_conn_ro(timeout_sec=0.3, busy_timeout_ms=300)
        cur = conn.cursor()

        def _fetch_recent_rows(limit_n: int):
            """Lese recent rows aus snapchains mit Soft-Abbruch via progress_handler."""
            started = time.time()

            def _progress():
                # return non-zero => abort query (OperationalError: interrupted)
                if (time.time() - started) > float(soft_limit_sec):
                    return 1
                return 0

            try:
                conn.set_progress_handler(_progress, 10000)
            except Exception:
                pass

            try:
                cur.execute(
                    """
                    SELECT status, quality
                    FROM snapchains
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(limit_n),),
                )
                return cur.fetchall(), True
            except Exception:
                return [], False
            finally:
                try:
                    conn.set_progress_handler(None, 0)
                except Exception:
                    pass

        # 1) Recent window für status + quality (primary -> fallback)
        rows, ok_rows = _fetch_recent_rows(win_counts_primary)
        counts_window = int(win_counts_primary)
        counts_mode = "recent_window"
        if not ok_rows:
            rows, ok_rows2 = _fetch_recent_rows(win_counts_fallback)
            counts_window = int(win_counts_fallback)
            counts_mode = "recent_window_fallback"
            if not ok_rows2:
                _fh_cancel()
                return _json_error("timeout: recent_window", status=503)

        n_active = 0
        n_compressed = 0
        q_sum = 0.0
        q_n = 0

        # Für avg_quality nur die ersten win_avg Samples (recentest)
        q_list = []  # recent qualities (win_avg), used for quantiles/top-k
        for i, r in enumerate(rows):
            st = (r["status"] if isinstance(r, dict) else r[0]) or ""
            if st == "active":
                n_active += 1
            elif st == "compressed":
                n_compressed += 1

            if i < win_avg:
                try:
                    q = float(r["quality"] if isinstance(r, dict) else r[1])
                except Exception:
                    q = None
                if q is not None:
                    q_sum += q
                    q_n += 1
                    q_list.append(q)

        avg_quality = (q_sum / q_n) if q_n else 0.0

        # 1b) Quantile & Top-K-Averages (window-basiert, schnell)
        #     Diese Werte sind deutlich aussagekräftiger als nur der Mittelwert.
        #     p90 = Schwelle, ab der die besten 10% beginnen; p99 = Schwelle für die besten 1%.
        p90_quality = 0.0
        p99_quality = 0.0
        avg_top_k_quality = 0.0
        avg_top_100_quality = 0.0
        top_k = 1000
        try:
            if q_list:
                qs = sorted(float(x) for x in q_list)
                nqs = len(qs)
                # nearest-rank (robust, ohne numpy)
                i90 = int(round(0.90 * (nqs - 1))) if nqs > 1 else 0
                i99 = int(round(0.99 * (nqs - 1))) if nqs > 1 else 0
                p90_quality = float(qs[max(0, min(nqs - 1, i90))])
                p99_quality = float(qs[max(0, min(nqs - 1, i99))])

                k = int(min(max(1, top_k), nqs))
                top = qs[-k:]
                avg_top_k_quality = float(sum(top) / float(len(top))) if top else 0.0

                k100 = int(min(100, nqs))
                top100 = qs[-k100:]
                avg_top_100_quality = float(sum(top100) / float(len(top100))) if top100 else 0.0
        except Exception:
            # keine harte Fehlersituation – UI soll trotzdem laufen
            p90_quality = 0.0
            p99_quality = 0.0
            avg_top_k_quality = 0.0
            avg_top_100_quality = 0.0


        # 2) MetaSnaps: niemals COUNT(*) (kann extrem teuer sein).
        #    MAX(id) ist robust & schnell genug für UI-Übersicht.
        n_meta_mode = "max_id"
        try:
            cur.execute("SELECT IFNULL(MAX(id), 0) AS max_id FROM meta_snaps")
            rr = cur.fetchone()
            n_meta = int(rr["max_id"] if isinstance(rr, dict) else rr[0])
        except Exception:
            n_meta = 0
            n_meta_mode = "error"

        denom = float(n_active + n_compressed) if (n_active + n_compressed) > 0 else 0.0
        compr_rate = (float(n_compressed) / denom) if denom > 0 else 0.0

        payload = {
            "ok": True,
            "ts": int(now),
            "avg_quality": round(float(avg_quality), 4),
            "avg_weight": round(float(avg_quality), 4),  # UI kompatibel
            "p90_quality": round(float(p90_quality), 4),
            "p99_quality": round(float(p99_quality), 4),
            "avg_top_k_quality": round(float(avg_top_k_quality), 4),
            "avg_top_100_quality": round(float(avg_top_100_quality), 4),
            "top_k": int(top_k),
            "n_active": int(n_active),
            "n_compressed": int(n_compressed),
            "n_meta": int(n_meta),
            "n_meta_mode": n_meta_mode,
            "counts_mode": str(counts_mode),
            "counts_window": int(counts_window),
            "compression_rate": round(float(compr_rate), 4),
        }


        # 3) Snapshot in stats.db für historische Anzeige (throttled)
        #
        # WICHTIG (Produktiv-Policy):
        #   - UI-Seite darf NICHT "nebenbei" rechnen/samplen, nur weil sie offen ist.
        #   - Historische Punkte sollen durch einen Hintergrund-Job (Orchestrator) entstehen
        #     oder explizit per "Ermitteln"-Button.
        #
        # Daher: nur wenn explizit angefordert (sample=1).
        try:
            if str(request.args.get("sample") or "0") == "1":
                _maybe_record_history_points(payload, now)
        except Exception:
            pass

        _STATE_CACHE["ts"] = now
        _STATE_CACHE["payload"] = payload

        _fh_cancel()
        return _json(payload)

    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "locked" in msg or "busy" in msg or "timeout" in msg:
            log.warning("[forgetting_ui] /api/state DB busy/locked (ui-fastfail): %s", e)
            _fh_cancel()
            return _json_error("db busy (retry)", status=503)
        log.exception("[forgetting_ui] /api/state operational error", exc_info=e)
        _fh_cancel()
        return _json_error("internal error", status=500)
    except TimeoutError as e:
        log.warning("[forgetting_ui] /api/state timeout: %s", e)
        _fh_cancel()
        return _json_error(f"timeout: {e}", status=503)
    except Exception as e:
        log.exception("[forgetting_ui] /api/state failed", exc_info=e)
        _fh_cancel()
        return _json_error("internal error", status=500)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Historische Werte (stats.db → stats_points)
# -----------------------------------------------------------------------------
# Ziel:
#   - /forgetting Seite soll 24h / 7d / 30d Trends zeigen (ohne oroma.db Vollscan).
#   - Wir schreiben daher einen *sehr kleinen* Snapshot in stats.db (Zeitreihen-Cache),
#     throttled (standard: 60s), und lesen ihn über /forgetting/api/history wieder aus.
#
# WICHTIG:
#   - oroma.db kann busy sein; stats.db ist unser UI-Cache.
#   - Schreiben/Lesen muss ultra-robust sein: kurzer busy_timeout, keine Locks halten.
#   - Datenmenge begrenzen: wir prunen alte Punkte (default ~35d) für forgetting/*.
# -----------------------------------------------------------------------------

_HIST_LAST_WRITE_TS = 0.0
_HIST_WRITE_INTERVAL_SEC = 60.0  # max 1 point/minute pro Serie
_HIST_PRUNE_DAYS = 35


def _stats_db_path() -> str:
    """Bestimme stats.db Pfad aus Projektlayout.

    Erwartet Standardlayout:
      /opt/ai/oroma/ui/forgetting_ui.py  → root=/opt/ai/oroma  → data/stats.db
    """
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        return os.path.join(root, "data", "stats.db")
    except Exception:
        return "/opt/ai/oroma/data/stats.db"




def _dbw_enabled() -> bool:
    try:
        return bool(int(os.getenv("OROMA_DBW_ENABLE", "0")))
    except Exception:
        return False

def _stats_conn_rw(timeout_sec: float = 0.3, busy_timeout_ms: int = 300) -> sqlite3.Connection:
    """Öffnet stats.db in RW (kurz, robust)."""
    dbp = _stats_db_path()
    conn = sqlite3.connect(dbp, timeout=float(timeout_sec))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # Für UI-Cache: WAL hilft enorm gegen Read/Write-Contention.
        # Best-effort: falls FS read-only oder SQLite-Flags, ignorieren.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Indexe: beschleunigt die /api/history-Reads deutlich.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_points_series_ts ON stats_points(series, ts)")
    except Exception:
        pass
    return conn


def _stats_conn_ro(timeout_sec: float = 0.1, busy_timeout_ms: int = 100) -> sqlite3.Connection:
    """Öffnet stats.db in Read-Only (ultra-kurz, robust).

    Wichtig:
      - /forgetting/api/history darf niemals blockieren.
      - Read-Only via URI verhindert Lock-Upgrades.
      - timeout/busy_timeout sind absichtlich klein: bei Contention lieber 503 als Hänger.
    """
    dbp = _stats_db_path()
    conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=float(timeout_sec))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass
    return conn


def _now_bucket(ts: float, bucket_sec: int = 60) -> int:
    """Zeit-Bucket, damit wir pro Minute pro Serie *aktualisieren* statt spammen."""
    it = int(ts)
    return int(it - (it % int(bucket_sec)))


def _maybe_record_history_points(payload: dict, now_ts: float) -> None:
    """Schreibe einen throttled Snapshot in stats_points (best-effort)."""
    global _HIST_LAST_WRITE_TS
    try:
        if not payload or payload.get("ok") is not True:
            return
        if (now_ts - float(_HIST_LAST_WRITE_TS or 0.0)) < float(_HIST_WRITE_INTERVAL_SEC):
            return

        # Sicherstellen: wir schreiben nur, wenn die Kernwerte da sind
        ts_bucket = _now_bucket(now_ts, 60)
        series_map = {
            "forgetting/avg_quality": payload.get("avg_quality"),
            "forgetting/p90_quality": payload.get("p90_quality"),
            "forgetting/p99_quality": payload.get("p99_quality"),
            "forgetting/avg_top_k_quality": payload.get("avg_top_k_quality"),
            "forgetting/avg_top_100_quality": payload.get("avg_top_100_quality"),
            "forgetting/compression_rate": payload.get("compression_rate"),
            "forgetting/n_active": payload.get("n_active"),
            "forgetting/n_compressed": payload.get("n_compressed"),
        }

        # Minimal: nur numerische Werte schreiben
        points = []
        for series, v in series_map.items():
            if v is None:
                continue
            try:
                fv = float(v)
            except Exception:
                continue
            points.append((ts_bucket, series, fv))

        if not points:
            return

        sconn = None
        try:
            # Stufe C (DBWriter Multi-DB): stats.db Writes bevorzugt ueber DBWriter.
            if _dbw_enabled() and '_dbw' in globals() and _dbw is not None:
                try:
                    prune_before = int(now_ts) - int(_HIST_PRUNE_DAYS) * 86400
                    _dbw.exec_write(
                        "DELETE FROM stats_points WHERE series LIKE 'forgetting/%' AND ts < ?",
                        [int(prune_before)],
                        tag="ui.forgetting.prune",
                        priority="low",
                        timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_STATS", "5000")),
                        db="stats",
                    )
                except Exception:
                    pass

                sql_ins = (
                    "INSERT INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta) "
                    "VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(src_table, src_uid, series) DO UPDATE SET "
                    "  ts=excluded.ts, "
                    "  value=excluded.value, "
                    "  src_id=excluded.src_id, "
                    "  meta=excluded.meta"
                )
                params_list = []
                for ts_i, series, value in points:
                    src_uid = f"{ts_i}:{series}"
                    params_list.append([int(ts_i), str(series), float(value), "forgetting_ui", 0, str(src_uid), None])

                if params_list:
                    _dbw.executemany(
                        sql_ins,
                        params_list,
                        tag="ui.forgetting.write_points",
                        priority="low",
                        timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_STATS", "5000")),
                        db="stats",
                    )
                _HIST_LAST_WRITE_TS = float(now_ts)
                return

            # Fallback (legacy/local)
            sconn = _stats_conn_rw(timeout_sec=0.3, busy_timeout_ms=300)
            try:
                prune_before = int(now_ts) - int(_HIST_PRUNE_DAYS) * 86400
                sconn.execute(
                    "DELETE FROM stats_points WHERE series LIKE 'forgetting/%' AND ts < ?",
                    (prune_before,),
                )
            except Exception:
                pass

            for ts_i, series, value in points:
                src_uid = f"{ts_i}:{series}"
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
                    (int(ts_i), str(series), float(value), "forgetting_ui", 0, str(src_uid), None),
                )

            sconn.commit()
            _HIST_LAST_WRITE_TS = float(now_ts)
        finally:
            try:
                if sconn is not None:
                    sconn.close()
            except Exception:
                pass
    except Exception:
        # Niemals UI request kaputt machen
        return


def _parse_range(s: str) -> int:
    """range string → seconds (supported: '24h','7d','30d','1w')."""
    ss = (s or "").strip().lower()
    if ss in ("24h", "1d", "day"):
        return 24 * 3600
    if ss in ("7d", "1w", "week"):
        return 7 * 86400
    if ss in ("30d", "1m", "month"):
        return 30 * 86400
    # default
    return 7 * 86400


def _downsample(points: list[tuple[int, float]], max_points: int = 800) -> list[list[float]]:
    """Downsample ts/value pairs to a max size (even stride)."""
    if not points:
        return []
    if len(points) <= max_points:
        return [[float(t), float(v)] for (t, v) in points]
    step = int(math.ceil(len(points) / float(max_points)))
    out = []
    for i in range(0, len(points), step):
        t, v = points[i]
        out.append([float(t), float(v)])
    return out


@bp.get("/api/history")
def api_history():
    """Historische Kurven aus stats.db (Read-Optimized Cache).

    Query:
      /forgetting/api/history?range=7d
    Antwort:
      {"ok":true,"ts":..., "range":"7d","from_ts":..., "series": {"forgetting/avg_quality":[[ts,val],..], ...}}
    """
    # API-Gate wie /api/state (PUBLIC oder Token wenn gesetzt)
    if (not _public_api_enabled()) and _cfg_token():
        t = _extract_token()
        if not t or t != _cfg_token():
            return _json_error("token required", status=401)

    now_ts = int(time.time())
    rng = request.args.get("range", "7d")
    sec = _parse_range(rng)
    from_ts = int(now_ts - sec)

    conn = None
    try:
        # Read-only + sehr kurzer Timeout, damit UI nie hängt.
        conn = _stats_conn_ro(timeout_sec=0.12, busy_timeout_ms=120)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts, series, value
            FROM stats_points
            WHERE ts >= ?
              AND series LIKE 'forgetting/%'
            ORDER BY series ASC, ts ASC
            """,
            (from_ts,),
        )
        rows = cur.fetchall() or []

        by = {}
        for r in rows:
            series = (r["series"] if isinstance(r, dict) else r[1]) or ""
            ts_i = int(r["ts"] if isinstance(r, dict) else r[0])
            val = float(r["value"] if isinstance(r, dict) else r[2])
            by.setdefault(series, []).append((ts_i, val))

        # Downsample per series
        out = {k: _downsample(v, max_points=800) for k, v in by.items()}

        # NOTE: Der History-Endpunkt liefert bewusst keine "globalen" DB-Scans.
        #       Die UI kann aber besser erklären/validieren, wenn sie weiß,
        #       welche Window-Größen im /api/state genutzt werden.
        return _json({
            "ok": True,
            "ts": now_ts,
            "range": str(rng),
            "from_ts": int(from_ts),
            "series": out,
            # Konstante Referenzwerte für die Frontend-Interpretation
            "counts_window_default": int(COUNTS_WINDOW_DEFAULT),
            "counts_window_fallback": 50000,
        })
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "locked" in msg or "busy" in msg or "timeout" in msg:
            return _json_error("stats db busy (retry)", status=503)
        log.exception("[forgetting_ui] /api/history operational error", exc_info=e)
        return _json_error("internal error", status=500)
    except Exception as e:
        log.exception("[forgetting_ui] /api/history failed", exc_info=e)
        return _json_error("internal error", status=500)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

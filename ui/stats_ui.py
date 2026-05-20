#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/stats_ui.py
# Projekt: ORÓMA – Live Learning Dashboard
# Version: v3.8.5-open (Health/Metrics/Curriculum APIs, DB-/ENV-Fallbacks)
# Stand:   2025-11-03
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   /stats              → HTML-Dashboard (stats.html)
#   /stats/api/live     → JSON: Lernen/Replay/Dream/System
#   /stats/api/health   → JSON: SQLite- und Datei-Gesundheit (read-only)
#   /stats/api/metrics  → JSON: Heartbeats/Event-Eingänge (letzte Stunde)
#   /stats/api/curriculum → JSON: Curriculum-State (sql_manager oder DB)
#
# Sicherheit
# ──────────
#   GET-Endpunkte sind offen (wie bei control_ui.py).
#   Ein before_request-Guard schützt nur POST (hier derzeit ungenutzt).
#
# Merkmale
# ────────
#   • Defensive DB-Zugriffe (fehlende Tabellen ≙ 0)
#   • Tagesabfragen lokal (…,'localtime')
#   • Qualitätshistorie via core.model_registry.fetch_quality_history()
#   • Health/Telemetry ohne schreibende PRAGMAs (reiner Read-Mode)
#   • ENV-/Pfad-Fallbacks: OROMA_DB_PATH → OROMA_DB → OROMA_BASE/data/oroma.db
# =============================================================================

from __future__ import annotations

import os
import time
import threading
import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any, Optional, Tuple, List, Dict

from flask import Blueprint, render_template, jsonify, request
import logging
from core.log_guard import log_suppressed

# -----------------------------------------------------------------------------
# Pfad-Resolver
# -----------------------------------------------------------------------------
def _resolve_db_path() -> str:
    p = os.environ.get("OROMA_DB_PATH")
    if p:
        return p
    p = os.environ.get("OROMA_DB")
    if p:
        return p
    base = os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
    return os.path.join(base, "data", "oroma.db")

DB_PATH = _resolve_db_path()

VECTOR_HINTS = [
    "/opt/ai/oroma/data/vector_index.faiss",
    "/opt/ai/oroma/data/vector_index.ann",
    "/opt/ai/oroma/data/vectors",
]

stats_bp = Blueprint("stats_ui", __name__, url_prefix="/stats")

# -----------------------------------------------------------------------------
# (Optionaler) Token-Guard – NUR für POST (aktuell nicht genutzt)
# -----------------------------------------------------------------------------
def _cfg_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token() -> Optional[str]:
    h = request.headers.get("X-OROMA-TOKEN")
    if h:
        return h.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = request.args.get("token")
    if q:
        return q.strip()
    c = request.cookies.get("OROMA_UI_TOKEN")
    if c:
        return c.strip()
    return None

@stats_bp.before_request
def _guard_posts_only():
    if request.method != "POST":
        return None
    need = _cfg_token()
    if not need:
        return None
    if _extract_token() == need:
        return None
    return jsonify({"ok": False, "error": "Unauthorized"}), 401

# -----------------------------------------------------------------------------
# DB-Utils
# -----------------------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    # kleine timeout, Row-Objekte
    con = sqlite3.connect(DB_PATH, timeout=1.5)
    con.row_factory = sqlite3.Row
    return con

def _scalar(c: sqlite3.Cursor, sql: str, params: Tuple[Any, ...] = (), default: int = 0) -> int:
    try:
        c.execute(sql, params)
        row = c.fetchone()
        return int(row[0]) if row and row[0] is not None else default
    except Exception:
        return default

def _one(c: sqlite3.Cursor, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Any]:
    try:
        c.execute(sql, params)
        row = c.fetchone()
        return row[0] if row else None
    except Exception:
        return None

def _ts_to_str(ts: Any) -> str:
    try:
        ival = int(float(ts))
        return _dt.datetime.fromtimestamp(ival).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "–"

def _human_bytes(n: int) -> str:
    units = ["B","KB","MB","GB","TB"]
    x = float(n)
    i = 0
    while x >= 1024 and i < len(units)-1:
        x /= 1024.0
        i += 1
    return f"{x:.1f} {units[i]}"

def _db_files_info(path: str) -> Dict[str, Any]:
    p = Path(path)
    info = {
        "path": str(p),
        "exists": p.exists(),
        "size_bytes": 0,
        "size_human": "0 B",
        "mtime": "–",
        "wal_path": str(p.with_suffix(p.suffix + "-wal")),
        "wal_exists": False,
        "wal_size_bytes": 0,
        "wal_size_human": "0 B",
        "shm_path": str(p.with_suffix(p.suffix + "-shm")),
        "shm_exists": False,
        "shm_size_bytes": 0,
        "shm_size_human": "0 B",
    }
    try:
        if p.exists():
            sb = p.stat().st_size
            info["size_bytes"] = int(sb)
            info["size_human"] = _human_bytes(int(sb))
            info["mtime"] = _dt.datetime.fromtimestamp(int(p.stat().st_mtime)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        log_suppressed('ui/stats_ui.py:163', exc=e, level=logging.WARNING)
        pass
    # -wal / -shm
    wp = Path(info["wal_path"])
    sp = Path(info["shm_path"])
    try:
        if wp.exists():
            info["wal_exists"] = True
            info["wal_size_bytes"] = int(wp.stat().st_size)
            info["wal_size_human"]  = _human_bytes(int(wp.stat().st_size))
    except Exception as e:
        log_suppressed('ui/stats_ui.py:174', exc=e, level=logging.WARNING)
        pass
    try:
        if sp.exists():
            info["shm_exists"] = True
            info["shm_size_bytes"] = int(sp.stat().st_size)
            info["shm_size_human"]  = _human_bytes(int(sp.stat().st_size))
    except Exception as e:
        log_suppressed('ui/stats_ui.py:182', exc=e, level=logging.WARNING)
        pass
    return info

# -----------------------------------------------------------------------------
# Routen
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# UI-CACHE (CPU-Schutz)
# -----------------------------------------------------------------------------
# Problem: Die /stats-Seite pollt mehrere JSON-Endpunkte (Default: alle 10s).
# Je nach DB-Größe/Indizes können diese Queries mehrere Sekunden dauern und
# damit quasi "dauerlaufen" (überlappende Requests), was auf dem Pi als 100%
# CPU auffällt. Zusätzlich darf dabei keine SQLite-Connection offen bleiben.
#
# Lösung (minimal-invasiv):
#  • Kleine In-Memory-Caches mit TTL (Sekunden) für LIVE/HEALTH/METRICS.
#  • api_live schließt die Connection jetzt garantiert (finally).
#
# Hinweis: Das ist bewusst konservativ – die UI braucht keine Millisekunden-
# Genauigkeit, sondern "near real-time".
# -----------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()
_CACHE: dict = {}  # key -> {"ts": float, "val": Any}

def _cache_get(key: str, ttl_s: float):
    now = time.time()
    with _CACHE_LOCK:
        e = _CACHE.get(key)
        if not e:
            return None
        if (now - float(e.get("ts", 0.0))) < float(ttl_s):
            return e.get("val")
        return None

def _cache_set(key: str, val):
    with _CACHE_LOCK:
        _CACHE[key] = {"ts": time.time(), "val": val}

@stats_bp.route("/")
def page():
    return render_template("stats.html")

@stats_bp.route("/api/live")
def api_live():
    cached = _cache_get("stats.live", ttl_s=2.0)
    if cached is not None:
        return jsonify(cached)

    con = None
    try:
        con = _conn()
        c = con.cursor()

        # SnapChains / Snaps
        snapchains = _scalar(c, "SELECT COUNT(*) FROM snapchains WHERE status='active'")
        snaps_ram  = _scalar(c, "SELECT COUNT(*) FROM snaps")

        # Exports / Rules
        exports        = _scalar(c, "SELECT COUNT(*) FROM exports WHERE status='ok'")
        rules_active   = _scalar(c, "SELECT COUNT(*) FROM rules WHERE active=1")
        rules_inactive = _scalar(c, "SELECT COUNT(*) FROM rules WHERE active=0")

        # Traumphasen
        dream_cycles = _scalar(c, "SELECT COUNT(*) FROM dream_cycles")
        last_dream_ts = _one(
            c,
            "SELECT ts_end FROM dream_cycles WHERE ts_end IS NOT NULL "
            "ORDER BY ts_end DESC LIMIT 1"
        )
        last_dream = _ts_to_str(last_dream_ts) if last_dream_ts else "–"

        # Replay & Games (heute)
        replays_today = _scalar(
            c,
            "SELECT COUNT(*) FROM replay_log "
            "WHERE date(ts_run, 'unixepoch','localtime') = date('now','localtime')"
        )
        games_today = _scalar(
            c,
            "SELECT COUNT(*) FROM game_sessions "
            "WHERE date(ts_start, 'unixepoch','localtime') = date('now','localtime')"
        )

        # Qualitätshistorie
        labels: List[str] = []
        values: List[float] = []
        quality_avg = 0.0
        try:
            from core import model_registry  # type: ignore
            q = model_registry.fetch_quality_history(limit=30) or []
            for item in q:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    labels.append(str(item[0])); values.append(float(item[1]))
                elif isinstance(item, dict):
                    labels.append(str(item.get("label") or item.get("ts") or item.get("x") or ""))
                    values.append(float(item.get("value") or item.get("y") or 0.0))
            if values:
                quality_avg = round(sum(values) / len(values), 3)
        except Exception as e:
            log_suppressed('ui/stats_ui.py:api_live:quality', exc=e, level=logging.WARNING)

        # Systemstatus
        mode = os.environ.get("OROMA_MODE", "day").lower()
        vector_db = any(Path(p).exists() for p in VECTOR_HINTS)
        up_h = int(time.monotonic() // 3600)
        up_m = int((time.monotonic() % 3600) // 60)
        uptime = f"{up_h}h {up_m}m"

        stats = {
            "snaps_ram": snaps_ram,
            "snapchains_db": snapchains,
            "exports": exports,
            "rules_active": rules_active,
            "rules_inactive": rules_inactive,
            "dream_cycles": dream_cycles,
            "replays_today": replays_today,
            "games_played": games_today,
            "vector_db": vector_db,
            "mode": mode,
            "last_dream": last_dream,
            "quality_avg": quality_avg,
            "uptime": uptime,
        }
        chart = {"labels": labels, "values": values}
        payload = {"ok": True, "chart": chart, "stats": stats}
        _cache_set("stats.live", payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass

@stats_bp.route("/api/health")
def api_health():
    cached = _cache_get("stats.health", ttl_s=5.0)
    if cached is not None:
        return jsonify(cached)
    con = None
    try:
        info = _db_files_info(DB_PATH)
        pragmas = {}
        con = _conn()
        c = con.cursor()
        # nur lesende PRAGMAs / Abfragen
        pragmas["journal_mode"]    = _one(c, "PRAGMA journal_mode") or "unknown"
        pragmas["page_size"]       = int(_one(c, "PRAGMA page_size") or 0)
        pragmas["page_count"]      = int(_one(c, "PRAGMA page_count") or 0)
        pragmas["freelist_count"]  = int(_one(c, "PRAGMA freelist_count") or 0)
        pragmas["cache_size"]      = int(_one(c, "PRAGMA cache_size") or 0)
        pragmas["wal_autocheckpoint"] = int(_one(c, "PRAGMA wal_autocheckpoint") or 0)
        try:
            rows = c.execute("PRAGMA compile_options").fetchall()
            pragmas["compile_options"] = [r[0] for r in rows] if rows else []
        except Exception:
            pragmas["compile_options"] = []

        page_count = pragmas.get("page_count", 0) or 0
        freelist   = pragmas.get("freelist_count", 0) or 0
        used_pages = max(0, int(page_count) - int(freelist))
        used_pct   = (used_pages / page_count) if page_count else 0.0

        payload = {
            "ok": True,
            "db": info,
            "pragmas": pragmas,
            "usage": {
                "used_pages": used_pages,
                "used_pct": round(used_pct, 4),
            }
        }
        _cache_set("stats.health", payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass

@stats_bp.route("/api/metrics")
def api_metrics():
    cached = _cache_get("stats.metrics", ttl_s=5.0)
    if cached is not None:
        return jsonify(cached)
    con = None
    try:
        now = int(time.time())
        hour_ago = now - 3600
        con = _conn()
        c = con.cursor()
        hb_last = _one(c, "SELECT MAX(ts) FROM metrics WHERE key='agent_heartbeat'")
        hb_1h   = _scalar(c, "SELECT COUNT(*) FROM metrics WHERE key='agent_heartbeat' AND ts>=?", (hour_ago,))
        ev_last = _one(c, "SELECT MAX(ts) FROM metrics WHERE key='agent_event_injected'")
        ev_1h   = _scalar(c, "SELECT COUNT(*) FROM metrics WHERE key='agent_event_injected' AND ts>=?", (hour_ago,))

        payload = {
            "ok": True,
            "now": now,
            "summary": {
                "heartbeats_last_hour": hb_1h,
                "events_last_hour": ev_1h,
            },
            "last": {
                "heartbeat_ts": int(hb_last) if hb_last else None,
                "heartbeat_at": _ts_to_str(hb_last) if hb_last else "–",
                "event_ts": int(ev_last) if ev_last else None,
                "event_at": _ts_to_str(ev_last) if ev_last else "–",
            }
        }
        _cache_set("stats.metrics", payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass

@stats_bp.route("/api/curriculum")
def api_curriculum():
    con = None
    try:
        # bevorzugt sql_manager (stabile API)
        try:
            from core import sql_manager  # type: ignore
            state = sql_manager.fetch_curriculum_state() or {}
        except Exception:
            # Fallback: direkte DB-Query
            con = _conn()
            c = con.cursor()
            row = c.execute("SELECT * FROM curriculum_state WHERE id=1").fetchone()
            state = dict(row) if row else {}
        return jsonify({"ok": True, "state": state})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass

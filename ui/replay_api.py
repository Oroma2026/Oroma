#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/replay_api.py
# Projekt:   ORÓMA – Headless UI / Replay-API
# Version:   v3.8-r4 (Chains: Paging/Filter + robuste Token-Erkennung)
# Stand:     2025-11-05
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
#  HTTP-API für das Replay-System:
#    • GET  /replay/api/healthz          → Ping
#    • GET  /replay/api/status           → Status (state dict)
#    • POST /replay/api/start            → Replay starten (chain_id, speed)
#    • POST /replay/api/pause            → pausieren
#    • POST /replay/api/resume           → fortsetzen
#    • POST /replay/api/stop             → stoppen
#    • GET  /replay/api/logs?limit=50    → letzte Einträge aus replay_log
#    • GET  /replay/api/chains           → Chain-Listing (FS + DB) mit Paging/Filter
#    • GET  /replay/api/debug/config     → sichere Debug-Flag-Ansicht (ohne Token)
#
# CHAINS-API
# ──────────
#  Parameter:
#    • source=fs|db|all        (Default: all)
#    • q=<substring>           (case-insensitive Teilstringfilter)
#    • offset=<int>            (Default: 0)
#    • limit=<int>             (Default: 50, Max: 500)
#    • sort=id_asc|id_desc|ts_desc|ts_asc    (Default: ts_desc)
#
#  Antwort:
#    { ok, source, q, offset, limit, sort,
#      fs_count, db_count, total_count,
#      items: [
#        { "source":"fs", "id":"...", "ts":1700000000, "size_kb":123, "path":".../selftest.json" },
#        { "source":"db", "id":123, "ts":1700000123, "origin":"...", "notes":"..." }
#      ],
#      page_count
#    }
#
# SICHERHEIT
# ──────────
#  Tokenpflicht nur wenn:
#    • OROMA_DISABLE_TOKEN != 1  UND
#    • (OROMA_REQUIRE_TOKEN=1|true|yes  ODER  OROMA_UI_TOKEN.strip() != "")
#  Header: "Authorization: Bearer <token>" ODER "X-Api-Token: <token>"
#
# ENV
# ───
#  • OROMA_BASE=/opt/ai/oroma
#  • OROMA_UI_TOKEN=...            (leer → keine Tokenpflicht)
#  • OROMA_REQUIRE_TOKEN=0|1       (erzwingt Tokenpflicht)
#  • OROMA_DISABLE_TOKEN=0|1       (Kill-Switch: 1 → nie Token)
#  • REPLAY_API_DEBUG=0|1
# =============================================================================

from __future__ import annotations

import os
import time
import glob
from typing import Any, Dict, Optional, List, Tuple
from flask import Blueprint, request, jsonify

import sys
BASE = os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# Core-Module (defensiv)
try:
    from core import replay_manager
except Exception:
    replay_manager = None  # type: ignore

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

replay_bp = Blueprint("replay_api", __name__, url_prefix="/replay/api")

DEBUG = os.getenv("REPLAY_API_DEBUG", "0").strip().lower() in ("1", "true", "yes")

# ----------------------------- Helpers --------------------------------------

def _truthy(env_name: str) -> bool:
    return os.getenv(env_name, "").strip().lower() in ("1", "true", "yes", "on")

def _clean_token(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s

def _token_required() -> bool:
    if _truthy("OROMA_DISABLE_TOKEN"):
        return False
    if _truthy("OROMA_REQUIRE_TOKEN"):
        return True
    t = _clean_token(os.getenv("OROMA_UI_TOKEN"))
    return t != ""

def _require_token() -> Optional[str]:
    if not _token_required():
        return None
    expected = _clean_token(os.getenv("OROMA_UI_TOKEN"))
    if expected == "":
        return "invalid or missing token"
    got = request.headers.get("Authorization", "").strip()
    if got.lower().startswith("bearer "):
        got = got.split(" ", 1)[1].strip()
    if not got:
        got = request.headers.get("X-Api-Token", "").strip()
    got = _clean_token(got)
    if got == expected:
        return None
    return "invalid or missing token"

def _j(err: Optional[str] = None, **payload: Any):
    base = {"ok": err is None}
    if err:
        base["error"] = err
    base.update(payload)
    return jsonify(base)

def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[replay_api DEBUG] {msg}", flush=True)

# ----------------------------- Chains (FS/DB) --------------------------------

def _scan_fs_chains(q: str, sort: str) -> Tuple[int, List[Dict[str, Any]]]:
    snap_dir = os.path.join(BASE, "data", "snapchains")
    items: List[Dict[str, Any]] = []
    if not os.path.isdir(snap_dir):
        return 0, items

    q_low = q.lower()
    try:
        paths = glob.glob(os.path.join(snap_dir, "*.json"))
        fs_count = len(paths)
        for p in paths:
            name = os.path.splitext(os.path.basename(p))[0]
            if q and q_low not in name.lower():
                continue
            try:
                st = os.stat(p)
                items.append({
                    "source": "fs",
                    "id": name,
                    "ts": int(st.st_mtime),
                    "size_kb": int(st.st_size // 1024),
                    "path": p,
                })
            except Exception:
                items.append({"source": "fs", "id": name, "ts": None, "size_kb": None, "path": p})
        if sort == "id_asc":
            items.sort(key=lambda x: str(x["id"]))
        elif sort == "id_desc":
            items.sort(key=lambda x: str(x["id"]), reverse=True)
        elif sort == "ts_asc":
            items.sort(key=lambda x: (x["ts"] is None, x["ts"]))
        else:
            items.sort(key=lambda x: (x["ts"] is None, x["ts"]), reverse=True)
        return fs_count, items
    except Exception as e:
        _dbg(f"FS-Scan Fehler: {e}")
        return 0, items

def _scan_db_chains(q: str, sort: str) -> Tuple[int, List[Dict[str, Any]]]:
    items: List[Dict[str, Any]] = []
    if not sql_manager:
        return 0, items
    try:
        with sql_manager.get_conn() as conn:  # type: ignore
            row = conn.execute("SELECT COUNT(*) AS c FROM snapchains").fetchone()
            db_count = int(row["c"]) if row and "c" in row.keys() else 0

            where = ""
            params: List[Any] = []
            if q:
                where = "WHERE CAST(id AS TEXT) LIKE ? OR ifnull(origin,'') LIKE ? OR ifnull(notes,'') LIKE ?"
                like = f"%{q}%"
                params = [like, like, like]

            order = "ORDER BY ts DESC"
            if sort == "id_asc":
                order = "ORDER BY id ASC"
            elif sort == "id_desc":
                order = "ORDER BY id DESC"
            elif sort == "ts_asc":
                order = "ORDER BY ts ASC"

            rows = conn.execute(
                f"SELECT id, ts, origin, notes FROM snapchains {where} {order} LIMIT 10000",
                params
            ).fetchall() or []

            for r in rows:
                items.append({
                    "source": "db",
                    "id": r["id"],
                    "ts": int(r["ts"]) if r["ts"] is not None else None,
                    "origin": r.get("origin") if hasattr(r, "get") else r["origin"],
                    "notes": r.get("notes") if hasattr(r, "get") else r["notes"],
                })
            return db_count, items
    except Exception as e:
        _dbg(f"DB-Scan Fehler: {e}")
        return 0, items

# ----------------------------- Routes ---------------------------------------

@replay_bp.route("/debug/config", methods=["GET"])
def debug_config():
    t = _clean_token(os.getenv("OROMA_UI_TOKEN"))
    data = {
        "token_required": _token_required(),
        "ui_token_len": len(t),
        "require_token_env": _truthy("OROMA_REQUIRE_TOKEN"),
        "disable_token_env": _truthy("OROMA_DISABLE_TOKEN"),
    }
    return _j(**data)

@replay_bp.route("/healthz", methods=["GET"])
def healthz():
    err = _require_token()
    if err:
        return _j(err), 401
    return _j(ts=int(time.time()))

@replay_bp.route("/status", methods=["GET"])
def status():
    err = _require_token()
    if err:
        return _j(err), 401
    if not replay_manager:
        return _j("replay_manager not available"), 500
    try:
        return _j(state=replay_manager.status())
    except Exception as e:
        return _j(str(e)), 500

@replay_bp.route("/start", methods=["POST"])
def start():
    err = _require_token()
    if err:
        return _j(err), 401
    if not replay_manager:
        return _j("replay_manager not available"), 500
    try:
        data = request.get_json(silent=True) or {}
        chain_id = data.get("chain_id") or data.get("id") or data.get("chain")
        speed = float(data.get("speed", 1.0))
        if not chain_id:
            return _j("chain_id required"), 400
        replay_manager.start(str(chain_id), speed=speed)
        return _j(started=True, chain_id=str(chain_id), speed=float(speed))
    except RuntimeError as e:
        return _j(str(e)), 409
    except Exception as e:
        return _j(str(e)), 500

@replay_bp.route("/pause", methods=["POST"])
def pause():
    err = _require_token()
    if err:
        return _j(err), 401
    if not replay_manager:
        return _j("replay_manager not available"), 500
    try:
        replay_manager.pause()
        return _j(paused=True)
    except Exception as e:
        return _j(str(e)), 500

@replay_bp.route("/resume", methods=["POST"])
def resume():
    err = _require_token()
    if err:
        return _j(err), 401
    if not replay_manager:
        return _j("replay_manager not available"), 500
    try:
        replay_manager.resume()
        return _j(resumed=True)
    except Exception as e:
        return _j(str(e)), 500

@replay_bp.route("/stop", methods=["POST"])
def stop():
    err = _require_token()
    if err:
        return _j(err), 401
    if not replay_manager:
        return _j("replay_manager not available"), 500
    try:
        replay_manager.stop()
        return _j(stopped=True)
    except Exception as e:
        return _j(str(e)), 500

@replay_bp.route("/logs", methods=["GET"])
def logs():
    err = _require_token()
    if err:
        return _j(err), 401
    if not sql_manager:
        return _j("sql_manager not available"), 500
    try:
        limit = max(1, min(500, int(request.args.get("limit", "50"))))
        with sql_manager.get_conn() as conn:  # type: ignore
            rows = conn.execute(
                "SELECT id, chain_id, ts_run, steps, speed, status, info "
                "FROM replay_log ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall() or []
        return _j(items=rows, count=len(rows))
    except Exception as e:
        return _j(str(e)), 500

@replay_bp.route("/chains", methods=["GET"])
def chains():
    err = _require_token()
    if err:
        return _j(err), 401

    source = request.args.get("source", "all").strip().lower()
    q = (request.args.get("q") or "").strip()
    offset = max(0, int(request.args.get("offset", "0")))
    limit = max(1, min(500, int(request.args.get("limit", "50"))))
    sort = request.args.get("sort", "ts_desc").strip().lower()
    if sort not in ("id_asc", "id_desc", "ts_asc", "ts_desc"):
        sort = "ts_desc"

    fs_count = db_count = 0
    fs_items: List[Dict[str, Any]] = []
    db_items: List[Dict[str, Any]] = []

    if source in ("fs", "all"):
        fs_count, fs_items = _scan_fs_chains(q=q, sort=sort)
    if source in ("db", "all"):
        db_count, db_items = _scan_db_chains(q=q, sort=sort)

    if source == "fs":
        items = fs_items
    elif source == "db":
        items = db_items
    else:
        items = fs_items + db_items
        if sort == "id_asc":
            items.sort(key=lambda x: str(x["id"]))
        elif sort == "id_desc":
            items.sort(key=lambda x: str(x["id"]), reverse=True)
        elif sort == "ts_asc":
            items.sort(key=lambda x: (x["ts"] is None, x["ts"]))
        else:
            items.sort(key=lambda x: (x["ts"] is None, x["ts"]), reverse=True)

    total_count = (fs_count if source in ("fs", "all") else 0) + (db_count if source in ("db", "all") else 0)
    items_page = items[offset: offset + limit]

    return _j(
        source=source, q=q, offset=offset, limit=limit, sort=sort,
        fs_count=fs_count, db_count=db_count, total_count=total_count,
        items=items_page, page_count=len(items_page)
    )
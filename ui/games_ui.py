#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/games_ui.py
# Projekt: ORÓMA – Headless UI (kein Qt/Wayland/X11)
# Version: v3.8.0
# Stand:   2026-07-22
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Registriert verfügbare Spiel-Blueprints und stellt eine schnelle
#   Übersichtsseite unter /games bereit (nur Liste + Notizen, kein Preview).
#
# Erweiterung v3.7.4
# ──────────────────
#   • ChessPro wird als normales ORÓMA-Spiel in der Übersicht geführt, obwohl
#     die Implementierung bewusst unter core/chess_pro/ liegt und keine eigene
#     Brett-UI benötigt. Die neue Route /games/chesspro/ zeigt den produktiven
#     Ist-Stand aus episodes/episodic_metrics und policy_rules.
#   • Daily Summary normalisiert Siegmetriken aus mehreren historischen Runner-
#     Konventionen: wins_p1/wins_p2, wins_oroma/wins_human, wins_x/wins_o,
#     wins_white/wins_black und wins. Dadurch werden Connect4, TicTacToe,
#     Chess/Chess2, Snake, Pong, CTF, Hide & Seek und ChessPro nicht mehr als
#     0/0 angezeigt, wenn die DB reale Siege enthält.
#   • ChessPro-spezifische Lernsignale werden zusätzlich gelesen: score_cp,
#     plies, nodes/qnodes, nps, depth_reached_avg/max, rule_hits, learn_items
#     und shaped_pos/shaped_neg/shaped_draw. Diese Werte sind bei ChessPro keine
#     Dekoration, sondern der eigentliche entscheidungsbasierte Lernnachweis.
#   • v3.7.5 ergänzt MemoryMaze-Hybrid-Lernmetriken: Reveals, Claims, Matches,
#     gelöste Paare, Pit-Hits, P3-Kontakte, policy_used/fallback und learn_items.
#   • v3.7.6 ergänzt Flappy-Pro-v2/v3-Metriken: high_score/high_steps,
#     death_world/death_pipe/death_max_steps, policy_used/fallback/guarded
#     und learn_items. Damit ist sichtbar, ob Flappy nur läuft oder Regeln
#     wiederverwendet und echte Pipe-/Crash-Signale lernt.
#   • v3.7.7 ergänzt Tetris-Pro-v2/v3-Metriken: high_score/high_lines,
#     learn_items, line/risk/topout credit, policy_used/fallback/q_rejected,
#     Speed-Timing und Boardqualität (holes).
#   • v3.7.8 ergänzt Tetris-Reuse-Guard-Metriken: policy_seen/accepted,
#     rejected_n/q/quality/unsafe und score_delta. Damit ist sichtbar, ob eine
#     Policy-Regel nur vorhanden ist oder board-technisch akzeptiert wurde.
#   • v3.7.9 ergänzt TicTacToe-Solved-Game-Diversity: unique_lines,
#     unique_openings und unique_final_boards zeigen, ob ein gelöstes Spiel
#     perfekte, aber nicht stumpf identische Partien erzeugt.
#   • v3.7.10 ergänzt Hide&Seek-Pro-v2-Metriken: avg_found, Captures,
#     BFS-/Path-Credits, Policy-Reuse, Safety-Rejects und pro_v2-Coverage.
#   • v3.7.11 ergänzt Sudoku-Pro-v2-Metriken: Constraint-Techniken,
#     mechanic_solved/explore_reduced, Policy-Reuse, Lernitems und Coverage.
#   • v3.7.12 ergänzt PTZ Zoom Observe als read-only Safety-/Audit-Kachel.
#     Diese Karte zeigt Preview-/Apply-Gate, Motor-Reason und Confidence-Werte,
#     ohne PTZ-Kommandos auszulösen oder Auto-Zoom freizugeben.
#   • v3.7.13 ergänzt Snake3D als explore-only Schablonen-Transfer-Spiel.
#     Die Route /snake3d/ zeigt read-only Template-Fit, Z-Achsen-Relevanz,
#     DBWriter-Lernstatus und policy_rules-Coverage, ohne Runner zu starten.
#
#   • v3.8.0 ergänzt eine strikt read-only Vertical-Learning-Observability.
#     Die API /games/api/vertical_learning aggregiert vorhandene DB-Spuren aus
#     SnapChains, Policy, Promotion, Targeted Acquisition, Outcome Queue,
#     Mini-Write-Ledger und Evidence-Lineage. Alle Werte sind ausdrücklich als
#     aktueller DB-Bestand bzw. Retention-Fenster gekennzeichnet; sie starten
#     keine Runner und führen keinerlei Mutation aus.
#
# =============================================================================

from __future__ import annotations

import logging
import os
import importlib
import time
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any, Dict

from flask import Blueprint, render_template, render_template_string, jsonify

from core import sql_manager

log = logging.getLogger("oroma.ui.games")
log.setLevel(getattr(logging, str(os.environ.get("OROMA_GAMES_LOG_LEVEL", "INFO") or "INFO").upper(), logging.INFO))
log.propagate = True


@dataclass
class GameMeta:
    key: str
    title: str
    module: str
    attr_fallbacks: List[str]
    url_hint: str
    note: Optional[str] = None
    registered_as: Optional[str] = field(default=None, init=False)
    url: Optional[str] = field(default=None, init=False)
    error: Optional[str] = field(default=None, init=False)


GAMES: List[GameMeta] = [
    GameMeta("snake",      "Snake",             "ui.snake_ui",      ["snake_bp", "bp"],        "/snake",      "Headless Snake (Pfeiltasten)"),
    GameMeta("snake3d",    "Snake3D",           "ui.snake3d_ui",    ["snake3d_bp", "bp"],      "/snake3d",    "3D-Snake Policy+Explore Template-Transfer"),
    GameMeta("pong",       "Pong",              "ui.pong_ui",       ["pong_bp", "bp"],         "/pong",       "Headless Pong (Canvas)"),
    GameMeta("flappy",     "Flappy",            "ui.flappy_ui",     ["bp", "flappy_bp"],       "/flappy",     "Erfordert pygame/OpenCV"),
    GameMeta("ctf",        "Capture the Flag",  "ui.ctf_ui",        ["bp", "ctf_bp"],          "/ctf",        None),
    GameMeta("hideseek",   "Hide & Seek",       "ui.hideseek_ui",   ["bp", "hideseek_bp"],     "/hideseek",   None),
    GameMeta("ptz_arena",  "PTZ Arena",         "ui.ptz_arena_ui",  ["ptz_arena_bp", "bp"],   "/ptz_arena",  "PTZ Policy Training (DeviceHub)"),
    GameMeta("ptz_target", "PTZ Target",        "ui.ptz_target_ui", ["ptz_target_bp", "bp"], "/ptz_target", "PTZ Targeting (Motion-Centroid)"),
    GameMeta("ptz_coverage", "PTZ Coverage",     "ui.ptz_coverage_ui", ["ptz_coverage_bp", "bp"], "/ptz_coverage", "Staubsauger-Sweep (Coverage über stats.db)"),
    GameMeta("ptz_zoom_observe", "PTZ Zoom Observe", "ui.ptz_zoom_observe_ui", ["ptz_zoom_observe_bp", "bp"], "/ptz_zoom_observe", "Read-only Audit: Preview/Apply-Gate, Motor-Reason und Confidence"),
    GameMeta("memory",     "Memory",            "ui.memory_ui",     ["bp", "memory_bp"],       "/memory",     None),
    GameMeta("tictactoe",  "Tic Tac Toe",       "ui.tictactoe_ui",  ["bp", "tictactoe_bp"],    "/tictactoe",  "9-Felder-Board, KI/Heuristik"),
    GameMeta("tetris",     "Tetris",            "ui.tetris_ui",     ["tetris_bp", "bp"],   "/tetris",   "WASD/←↑→↓, SPACE=Hard Drop, Autoplay client-side"),
    GameMeta("connect4",   "Connect Four",      "ui.connect4_ui",   ["bp", "connect4_bp"],     "/connect4",   None),
    GameMeta("memorymaze", "MemoryMaze Hybrid", "ui.memorymaze_ui", ["bp", "memorymaze_bp"],   "/memorymaze", "PacMan-Maze + Memory-Blocker + Items (Hybrid)"),
    GameMeta("vs",         "ORÓMA vs ORÓMA",    "ui.vs_ui",         ["bp", "vs_bp"],           "/vs",         None),
    GameMeta("chess",      "Chess",             "ui.chess_ui",      ["chess_bp", "bp"],        "/chess",      "Mini-Schach UI (PURE)"),
    GameMeta("chess2",     "Chess2",            "ui.chess2_ui",     ["chess2_bp", "bp"],      "/chess2",     "Mobility-native Chess Parallel-Stack"),
    GameMeta("chess_pro",  "ChessPro",          "__internal__",     [],                         "/games/chesspro", "Professionelles ORÓMA-Schach mit Long-Search, Positions-Trace und Policy-Lernen"),
    GameMeta("sudoku",     "Sudoku",            "ui.sudoku_ui",     ["sudoku_bp", "bp"],       "/sudoku",     "Headless Sudoku (Generator + Check + Hint)"),
]


def _import_blueprint(mod_name: str, attrs: List[str]) -> Tuple[Optional[Any], Optional[str]]:
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        return None, f"Importfehler: {e}"
    for a in attrs:
        bp = getattr(mod, a, None)
        if bp is not None:
            return bp, None
    return None, f"Kein Blueprint in {mod_name} ({attrs})"


def _safe_register(app, bp, name_hint: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        bp_name = getattr(bp, "name", None) or name_hint
        if bp_name in app.blueprints:
            return bp_name, None
        app.register_blueprint(bp)
        return bp_name, None
    except Exception as e:
        return None, f"Registrierfehler: {e}"


games_bp = Blueprint("games", __name__, url_prefix="/games", template_folder="templates")


@games_bp.route("/", methods=["GET"])
def page() -> str:
    available = [g for g in GAMES if g.registered_as and g.url]
    unavailable = [g for g in GAMES if not g.registered_as]
    return render_template("games.html", games_available=available, games_unavailable=unavailable)




_VERTICAL_CACHE_LOCK = threading.Lock()
_VERTICAL_CACHE: Dict[str, Any] = {"payload": None, "built_monotonic": 0.0}
_VERTICAL_CACHE_TTL_SEC = max(5, int(os.environ.get("OROMA_GAMES_VERTICAL_CACHE_TTL_SEC", "30") or 30))
_VERTICAL_QUERY_BUDGET_SEC = max(0.25, float(os.environ.get("OROMA_GAMES_VERTICAL_QUERY_BUDGET_SEC", "2.0") or 2.0))
_VERTICAL_BUSY_TIMEOUT_MS = max(100, int(os.environ.get("OROMA_GAMES_VERTICAL_BUSY_TIMEOUT_MS", "1500") or 1500))
_VERTICAL_TAIL_LIMIT = max(100, min(20000, int(os.environ.get("OROMA_GAMES_VERTICAL_TAIL_LIMIT", "2000") or 2000)))

_VERTICAL_TABLES = {
    "snapchains": "snapchains",
    "policy": "policy_rules",
    "promotions": "gap_policy_promotion_queue",
    "acquisitions": "gap_targeted_acquisition_lifecycle",
    "outcomes": "gap_evidence_outcome_queue",
    "ledger": "gap_policy_mini_write_ledger",
    "links": "policy_rule_evidence_links",
}


def _vertical_family(namespace: Any) -> str:
    """Map a concrete learning namespace to a stable UI family.

    The mapping is intentionally conservative.  It never rewrites persisted
    identities and is used only for read-only presentation.  Snake and Snake3D
    remain separate because they use distinct state/action schemas; Chess and
    PTZ variants are grouped so the overview remains useful despite multiple
    production namespaces.
    """
    ns = str(namespace or "").strip()
    if ns == "game:snake":
        return "snake"
    if ns == "game:snake3d":
        return "snake3d"
    if ns.startswith("game:chess"):
        return "chess"
    if ns.startswith("game:ptz") or ns.startswith("ptz:"):
        return "ptz"
    if ns.startswith("game:"):
        tail = ns[5:]
        return tail.split(":", 1)[0] or ns
    return ns or "unknown"



@contextmanager
def _vertical_read_conn():
    """Open a bounded, strictly read-only connection for UI aggregation.

    This intentionally bypasses ``sql_manager.get_conn()`` because that helper
    configures WAL pragmas for general runtime connections.  In DBWriter strict
    mode the database is opened read-only; trying to re-assert journal mode on
    such a connection can wait behind SQLite locks and is unnecessary for a
    dashboard read.  The dedicated UI connection therefore uses ``mode=ro``,
    ``query_only`` and a short busy timeout and never mutates schema or data.
    """
    db_path = os.path.abspath(str(sql_manager.get_db_path()))
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(
        uri,
        uri=True,
        timeout=float(_VERTICAL_BUSY_TIMEOUT_MS) / 1000.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(_VERTICAL_BUSY_TIMEOUT_MS)}")
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()


def _vertical_rows_bounded(
    conn: Any,
    sql: str,
    params: Tuple[Any, ...] = (),
    *,
    query_name: str,
    warnings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Execute one UI aggregation with a hard VM-step time budget.

    A slow historical table must not occupy a Flask request thread for minutes.
    SQLite's progress handler aborts only this SELECT; the endpoint continues
    with partial, explicitly marked data.
    """
    started = time.monotonic()

    def _abort_if_over_budget() -> int:
        return 1 if (time.monotonic() - started) >= _VERTICAL_QUERY_BUDGET_SEC else 0

    conn.set_progress_handler(_abort_if_over_budget, 10_000)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        warnings.append({
            "query": query_name,
            "error": str(exc),
            "elapsed_ms": elapsed_ms,
            "partial": True,
        })
        log.warning("Vertical Learning query skipped name=%s elapsed_ms=%s err=%s", query_name, elapsed_ms, exc)
        return []
    finally:
        conn.set_progress_handler(None, 0)


def _vertical_table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (str(table),),
    ).fetchone()
    return bool(row)


def _vertical_rows(conn: Any, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _vertical_tail_rows(
    conn: Any,
    *,
    table: str,
    columns: str,
    order_expr: str,
    query_name: str,
    warnings: List[Dict[str, Any]],
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Read only a bounded newest-row window without global aggregation.

    ORÓMA production databases can be tens of gigabytes large.  Dashboard
    requests must therefore follow the same scale-safe rule as the productive
    backup tool: never COUNT or GROUP the whole table; read the newest bounded
    row window through an integer primary key/rowid and aggregate in Python.
    """
    row_limit = max(1, int(limit or _VERTICAL_TAIL_LIMIT))
    sql = f"SELECT {columns} FROM {table} ORDER BY {order_expr} DESC LIMIT ?"
    return _vertical_rows_bounded(
        conn,
        sql,
        (row_limit,),
        query_name=query_name,
        warnings=warnings,
    )


def _vertical_pct(numerator: int, denominator: int) -> Optional[float]:
    if int(denominator or 0) <= 0:
        return None
    return round((100.0 * int(numerator or 0)) / int(denominator), 2)


def build_vertical_learning_status() -> Dict[str, Any]:
    """Build a bounded, read-only cross-game vertical-learning status.

    Scale contract
    --------------
    * no COUNT(*), GROUP BY, DISTINCT, MIN or MAX over complete live tables;
    * each source is read through a bounded newest-row window;
    * aggregation occurs in Python after the bounded read;
    * values are window counts, never advertised as lifetime totals;
    * no writes, schema changes, runner starts or policy mutations.
    """
    now = int(time.time())
    families: Dict[str, Dict[str, Any]] = {}
    available_tables: Dict[str, bool] = {}
    table_windows: Dict[str, Dict[str, Any]] = {}
    warnings: List[Dict[str, Any]] = []

    def ensure(ns: Any) -> Dict[str, Any]:
        family = _vertical_family(ns)
        row = families.setdefault(family, {
            "family": family,
            "namespaces": set(),
            "snapchains": 0,
            "policy_rules": 0,
            "policy_samples": 0,
            "promotions": 0,
            "promotion_reviews": 0,
            "promotion_policy_written": 0,
            "acquisitions": 0,
            "acquisitions_evidence_acquired": 0,
            "outcomes": 0,
            "outcomes_ready": 0,
            "outcomes_policy_written": 0,
            "mini_writes": 0,
            "blocked_writes": 0,
            "evidence_links": 0,
            "newest_ts": 0,
        })
        if ns:
            row["namespaces"].add(str(ns))
        return row

    def observe_window(key: str, rows: List[Dict[str, Any]], ts_fields: Tuple[str, ...]) -> None:
        timestamps: List[int] = []
        for item in rows:
            for field in ts_fields:
                try:
                    value = int(item.get(field) or 0)
                except Exception:
                    value = 0
                if value > 0:
                    timestamps.append(value)
        table_windows[key] = {
            "sampled_rows": len(rows),
            "sample_limit": _VERTICAL_TAIL_LIMIT,
            "truncated": len(rows) >= _VERTICAL_TAIL_LIMIT,
            "oldest_ts": min(timestamps) if timestamps else 0,
            "newest_ts": max(timestamps) if timestamps else 0,
            "semantics": "bounded_newest_rows",
        }

    with _vertical_read_conn() as conn:
        for key, table in _VERTICAL_TABLES.items():
            available_tables[key] = _vertical_table_exists(conn, table)

        if available_tables["snapchains"]:
            rows = _vertical_tail_rows(conn, table="snapchains", columns="namespace, origin, ts", order_expr="id", query_name="snapchains", warnings=warnings)
            observe_window("snapchains", rows, ("ts",))
            for item in rows:
                ns = item.get("namespace") or item.get("origin") or "unknown"
                if not str(ns).startswith("game:"):
                    continue
                dst = ensure(ns)
                dst["snapchains"] += 1
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("ts") or 0))

        if available_tables["policy"]:
            rows = _vertical_tail_rows(conn, table="policy_rules", columns="namespace, n, last_ts", order_expr="id", query_name="policy", warnings=warnings)
            observe_window("policy", rows, ("last_ts",))
            for item in rows:
                ns = str(item.get("namespace") or "")
                if not (ns.startswith("game:") or ns.startswith("ptz:")):
                    continue
                dst = ensure(ns)
                dst["policy_rules"] += 1
                dst["policy_samples"] += int(item.get("n") or 0)
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("last_ts") or 0))

        if available_tables["promotions"]:
            rows = _vertical_tail_rows(conn, table="gap_policy_promotion_queue", columns="namespace, status, created_ts, updated_ts", order_expr="id", query_name="promotions", warnings=warnings)
            observe_window("promotions", rows, ("created_ts", "updated_ts"))
            for item in rows:
                ns = item.get("namespace")
                if not ns:
                    continue
                dst = ensure(ns)
                dst["promotions"] += 1
                dst["promotion_reviews"] += int(item.get("status") == "promotion_review")
                dst["promotion_policy_written"] += int(item.get("status") == "policy_written")
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("updated_ts") or 0))

        if available_tables["acquisitions"]:
            rows = _vertical_tail_rows(conn, table="gap_targeted_acquisition_lifecycle", columns="namespace, status, created_ts, updated_ts", order_expr="rowid", query_name="acquisitions", warnings=warnings)
            observe_window("acquisitions", rows, ("created_ts", "updated_ts"))
            for item in rows:
                ns = item.get("namespace")
                if not ns:
                    continue
                dst = ensure(ns)
                dst["acquisitions"] += 1
                dst["acquisitions_evidence_acquired"] += int(item.get("status") == "evidence_acquired")
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("updated_ts") or 0))

        if available_tables["outcomes"]:
            rows = _vertical_tail_rows(conn, table="gap_evidence_outcome_queue", columns="namespace, status, created_ts, updated_ts", order_expr="id", query_name="outcomes", warnings=warnings)
            observe_window("outcomes", rows, ("created_ts", "updated_ts"))
            for item in rows:
                ns = item.get("namespace")
                if not ns:
                    continue
                dst = ensure(ns)
                dst["outcomes"] += 1
                dst["outcomes_ready"] += int(item.get("status") == "outcome_ready")
                dst["outcomes_policy_written"] += int(item.get("status") == "policy_written")
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("updated_ts") or 0))

        if available_tables["ledger"]:
            rows = _vertical_tail_rows(conn, table="gap_policy_mini_write_ledger", columns="namespace, status, policy_written, created_ts, updated_ts", order_expr="id", query_name="ledger", warnings=warnings)
            observe_window("ledger", rows, ("created_ts", "updated_ts"))
            for item in rows:
                ns = item.get("namespace")
                if not ns:
                    continue
                dst = ensure(ns)
                dst["mini_writes"] += int(item.get("policy_written") or 0) == 1
                dst["blocked_writes"] += int(item.get("status") == "blocked")
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("updated_ts") or 0))

        if available_tables["links"]:
            rows = _vertical_tail_rows(conn, table="policy_rule_evidence_links", columns="namespace, created_ts", order_expr="id", query_name="links", warnings=warnings)
            observe_window("links", rows, ("created_ts",))
            for item in rows:
                ns = item.get("namespace")
                if not ns:
                    continue
                dst = ensure(ns)
                dst["evidence_links"] += 1
                dst["newest_ts"] = max(dst["newest_ts"], int(item.get("created_ts") or 0))

    order = {"snake": 0, "snake3d": 1, "chess": 2, "ptz": 3}
    result_rows: List[Dict[str, Any]] = []
    for row in families.values():
        row["namespaces"] = sorted(row["namespaces"])
        row["promotion_to_acquisition_pct"] = _vertical_pct(row["acquisitions"], row["promotions"])
        row["acquisition_to_outcome_pct"] = _vertical_pct(row["outcomes"], row["acquisitions"])
        row["outcome_to_write_pct"] = _vertical_pct(row["mini_writes"], row["outcomes"])
        row["age_sec"] = max(0, now - int(row["newest_ts"])) if row["newest_ts"] else None
        result_rows.append(row)
    result_rows.sort(key=lambda r: (order.get(str(r["family"]), 100), str(r["family"])))

    return {
        "ok": True,
        "generated_ts": now,
        "read_only": True,
        "count_semantics": "bounded_newest_row_window",
        "conversion_semantics": "window_stock_ratio_not_cohort_conversion",
        "tail_limit_per_table": _VERTICAL_TAIL_LIMIT,
        "tables": available_tables,
        "table_windows": table_windows,
        "partial": bool(warnings),
        "warnings": warnings,
        "rows": result_rows,
    }

@games_bp.route("/api/vertical_learning", methods=["GET"])
def api_vertical_learning():
    now_mono = time.monotonic()
    cached = _VERTICAL_CACHE.get("payload")
    cache_age = now_mono - float(_VERTICAL_CACHE.get("built_monotonic") or 0.0)
    if cached is not None and cache_age < float(_VERTICAL_CACHE_TTL_SEC):
        payload = dict(cached)
        payload["cache"] = {"hit": True, "age_sec": round(cache_age, 3), "ttl_sec": _VERTICAL_CACHE_TTL_SEC}
        return jsonify(payload)

    acquired = _VERTICAL_CACHE_LOCK.acquire(timeout=0.25)
    if not acquired:
        if cached is not None:
            payload = dict(cached)
            payload["cache"] = {"hit": True, "stale": True, "age_sec": round(cache_age, 3), "ttl_sec": _VERTICAL_CACHE_TTL_SEC}
            return jsonify(payload)
        return jsonify({
            "ok": False,
            "read_only": True,
            "err": "vertical_learning_refresh_in_progress",
            "retry_after_sec": 1,
        }), 503

    try:
        # A second request may have populated the cache while this request was
        # waiting for the single-flight lock.
        now_mono = time.monotonic()
        cached = _VERTICAL_CACHE.get("payload")
        cache_age = now_mono - float(_VERTICAL_CACHE.get("built_monotonic") or 0.0)
        if cached is not None and cache_age < float(_VERTICAL_CACHE_TTL_SEC):
            payload = dict(cached)
            payload["cache"] = {"hit": True, "age_sec": round(cache_age, 3), "ttl_sec": _VERTICAL_CACHE_TTL_SEC}
            return jsonify(payload)

        payload = build_vertical_learning_status()
        _VERTICAL_CACHE["payload"] = payload
        _VERTICAL_CACHE["built_monotonic"] = time.monotonic()
        response = dict(payload)
        response["cache"] = {"hit": False, "age_sec": 0.0, "ttl_sec": _VERTICAL_CACHE_TTL_SEC}
        return jsonify(response)
    except Exception as exc:
        log.exception("/games/api/vertical_learning failed")
        cached = _VERTICAL_CACHE.get("payload")
        if cached is not None:
            payload = dict(cached)
            payload["cache"] = {"hit": True, "stale": True, "build_error": f"{type(exc).__name__}: {exc}"}
            return jsonify(payload)
        return jsonify({
            "ok": False,
            "read_only": True,
            "err": f"{type(exc).__name__}: {exc}",
        }), 500
    finally:
        _VERTICAL_CACHE_LOCK.release()


@games_bp.route("/api/list", methods=["GET"])
def api_list():
    def to_dict(g: GameMeta) -> Dict[str, Any]:
        return {
            "key": g.key,
            "title": g.title,
            "url": g.url,
            "registered": bool(g.registered_as),
            "blueprint": g.registered_as,
            "note": g.note,
            "error": g.error,
        }
    return jsonify({
        "ok": True,
        "available": [to_dict(g) for g in GAMES if g.registered_as],
        "unavailable": [to_dict(g) for g in GAMES if not g.registered_as],
    })


def _kind_to_game_variant(kind: str) -> Tuple[str, str]:
    """Map episodes.kind → (game, variant).

    Expected patterns in ORÓMA:
      • game:<game>:policy_batch / game:<game>:explore_batch
      • game:<game>:<variant>:policy_batch / ... (e.g. memorymaze_hybrid)

    If the pattern is unknown, we fall back to (raw, "").
    """
    if not kind or not kind.startswith("game:"):
        return kind or "", ""
    core = kind[len("game:"):]
    # strip batch suffix
    for suf in (":policy_batch", ":explore_batch"):
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    parts = core.split(":")
    if len(parts) == 1:
        return parts[0], ""
    # game + remaining segments as variant
    return parts[0], ":".join(parts[1:])


def _fmt_time_local(ts: Any) -> str:
    """Format unix ts as HH:MM:SS (localtime)."""
    try:
        t = int(ts or 0)
    except Exception:
        t = 0
    if t <= 0:
        return ""
    try:
        return time.strftime("%H:%M:%S", time.localtime(t))
    except Exception:
        return ""


def _to_float(v: Any) -> Optional[float]:
    """Best-effort float conversion for DB metric values.

    ORÓMA runner history contains several insert helper signatures and row
    factories. The UI must therefore not assume one exact sqlite return type.
    Invalid or missing values remain None so the frontend can distinguish
    "0.0" from "not produced by this game".
    """
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if not (f == f):
        return None
    return f


DAILY_METRIC_KEYS: Tuple[str, ...] = (
    # common batch/runtime metrics
    "duration_ms",
    "avg_reward",
    "avg_score",
    "avg_return",
    "avg_found",
    "avg_ticks",
    "avg_score_A",
    "avg_score_B",
    "avg_moves",
    "avg_turns",
    "avg_steps",
    # Flappy professional runner metrics
    "high_score",
    "high_steps",
    "death_world",
    "death_pipe",
    "death_max_steps",
    "max_steps",
    # Snake professional runner metrics
    "avg_food",
    "high_food",
    # Snake3D template-transfer telemetry
    "pos_items",
    "neg_items",
    "draw_items",
    "danger_z_rate",
    "food_up_signal_rate",
    "vertical_action_rate",
    "template_fit_score",
    "avg_length_end",
    "wins_by_length",
    "death_wall",
    "death_self",
    "policy_guarded",
    "steps",
    "avg_commands",
    "games",
    # outcome conventions used by the current and legacy daily runners
    "wins",
    "wins_x",
    "wins_o",
    "wins_p1",
    "wins_p2",
    "wins_oroma",
    "wins_human",
    "wins_white",
    "wins_black",
    "draws",
    "draws_by_cap",
    "unique_lines",
    "unique_openings",
    "unique_final_boards",
    # Memory / MemoryMaze
    "avg_pairs_left_end",
    "avg_pairs_cleared",
    "avg_reveals",
    "avg_claims",
    "avg_second_reveals",
    "avg_matches",
    "avg_mismatches",
    "avg_claim_timeouts",
    "avg_pit_hits",
    "avg_p3_contacts",
    "wins_by_pairs",
    "wins_by_strikes",
    "wins_by_length",
    "death_wall",
    "death_self",
    "policy_used",
    "policy_fallback",
    "policy_q_rejected",
    "policy_seen",
    "policy_accepted",
    "policy_miss",
    "policy_epsilon",
    "policy_min_n",
    "policy_min_q",
    "policy_rejected_n",
    "policy_rejected_q",
    "policy_rejected_quality",
    "policy_rejected_unsafe",
    "policy_guarded",
    "avg_strikes_p1",
    "avg_strikes_p2",
    "avg_strikes_p3",
    # Tetris
    "avg_score_end",
    "high_score_end",
    "avg_lines_end",
    "high_lines_end",
    "avg_level_end",
    "avg_pieces",
    "high_pieces",
    "line_credit_items",
    "improve_credit_items",
    "risk_credit_items",
    "topout_credit_items",
    "topouts",
    "policy_score_delta_avg",
    "policy_accept_q_min",
    "policy_accept_min_n",
    "policy_max_score_gap",
    "policy_dbw_chunk",
    "sim_duration_ms",
    "learn_duration_ms",
    "avg_final_holes",
    "avg_final_height",
    "avg_final_bumpiness",
    # PTZ domains
    "avg_dist",
    "lock_rate",
    "coverage_rate",
    "coverage_unique_cells",
    "avg_motion",
    "avg_sharp",
    # ChessPro decision-learning telemetry
    "plies",
    "score_cp",
    "nodes",
    "qnodes",
    "total_nodes",
    "search_ms",
    "nps",
    "depth_target",
    "depth_reached_max",
    "depth_reached_avg",
    "tt_hits",
    "cutoffs",
    "timed_out_moves",
    "game_budget_hit",
    "game_budget_sec",
    "repetition_guard_moves",
    "repetition_penalty_abs_cp",
    "api_guard_ok",
    "push_failures",
    "pop_failures",
    "rule_hits",
    "learn_items",
    "learned_items",
    "policy_learn_ok",
    "sim_duration_ms",
    "learn_duration_ms",
    "policy_dbw_chunk",
    "effective_games",
    "requested_games",
    "explore_complete",
    "no_more_explore",
    "solver_states_total",
    "solver_rules_total",
    "solver_items_total",
    "solver_best_items",
    "solver_blunder_items",
    "solver_safe_draw_items",
    "solver_forced_loss_best_items",
    "solver_states_known",
    "solver_rules_known",
    "solver_states_missing",
    "solver_rules_missing",
    "solver_value_win_states",
    "solver_value_draw_states",
    "solver_value_loss_states",
    "unique_lines",
    "unique_openings",
    "unique_final_boards",
    "learn_items_pos",
    "learn_items_neg",
    "learn_items_draw",
    "shaped_pos",
    "shaped_neg",
    "shaped_draw",
    "snapchain_steps",
)


SUM_METRIC_KEYS = {
    "games",
    "wins",
    "wins_x",
    "wins_o",
    "wins_p1",
    "wins_p2",
    "wins_oroma",
    "wins_human",
    "wins_white",
    "wins_black",
    "draws",
    "draws_by_cap",
    "unique_lines",
    "unique_openings",
    "unique_final_boards",
    "death_world",
    "death_pipe",
    "death_max_steps",
    "policy_guarded",
    "pos_items",
    "neg_items",
    "draw_items",
    "learn_items",
    "learned_items",
    "policy_seen",
    "policy_accepted",
    "policy_fallback",
    "policy_miss",
    "policy_epsilon",
    "policy_rejected_n",
    "policy_rejected_q",
    "solver_best_items",
    "solver_blunder_items",
    "solver_safe_draw_items",
    "solver_forced_loss_best_items",
    "wins_by_pairs",
    "wins_by_strikes",
    "policy_used",
    "policy_fallback",
    "policy_q_rejected",
    "policy_seen",
    "policy_accepted",
    "policy_rejected_n",
    "policy_rejected_q",
    "policy_rejected_quality",
    "policy_rejected_unsafe",
    "line_credit_items",
    "improve_credit_items",
    "risk_credit_items",
    "topout_credit_items",
    "topouts",
    "nodes",
    "qnodes",
    "total_nodes",
    "search_ms",
    "tt_hits",
    "cutoffs",
    "timed_out_moves",
    "game_budget_hit",
    "repetition_guard_moves",
    "repetition_penalty_abs_cp",
    "push_failures",
    "pop_failures",
    "rule_hits",
    "learn_items",
    "learn_items_pos",
    "learn_items_neg",
    "learn_items_draw",
    "shaped_pos",
    "shaped_neg",
    "shaped_draw",
    "snapchain_steps",
    "coverage_unique_cells",
}


AVG_METRIC_KEYS = tuple(k for k in DAILY_METRIC_KEYS if k not in SUM_METRIC_KEYS)


def _metric_avg(agg_row: Dict[str, Any], metric_key: str) -> Optional[float]:
    n = int(agg_row.get(f"{metric_key}_n") or 0)
    if n <= 0:
        return None
    return float(agg_row.get(f"{metric_key}_sum") or 0.0) / float(n)


def _metric_sum(agg_row: Dict[str, Any], metric_key: str) -> Optional[float]:
    if int(agg_row.get(f"{metric_key}_seen") or 0) <= 0:
        return None
    return float(agg_row.get(f"{metric_key}_sum") or 0.0)


def _normalise_outcome_counts(game: str, variant: str, metrics: Dict[str, Any]) -> Tuple[float, float, float, str]:
    """Return (wins, losses, draws, source) for heterogeneous game runners.

    The old UI only understood wins_p1/wins_p2 and wins_oroma/wins_human.
    Most board/arcade runners write wins_x/wins_o instead. ChessPro writes
    wins_white/wins_black because every run has a focus side variant. This
    function keeps those conventions explicit instead of silently discarding
    real outcomes.
    """
    draws = float(metrics.get("draws") or 0.0) + float(metrics.get("draws_by_cap") or 0.0)

    if game == "chess_pro":
        ww = float(metrics.get("wins_white") or 0.0)
        wb = float(metrics.get("wins_black") or 0.0)
        v = (variant or "").lower()
        if v == "white":
            return ww, wb, draws, "wins_white/wins_black:focus_white"
        if v == "black":
            return wb, ww, draws, "wins_white/wins_black:focus_black"
        return float(metrics.get("wins") or (ww + wb)), 0.0, draws, "wins_white/wins_black"

    if metrics.get("wins_p1") is not None or metrics.get("wins_p2") is not None:
        return float(metrics.get("wins_p1") or 0.0), float(metrics.get("wins_p2") or 0.0), draws, "wins_p1/wins_p2"

    if metrics.get("wins_oroma") is not None or metrics.get("wins_human") is not None:
        return float(metrics.get("wins_oroma") or 0.0), float(metrics.get("wins_human") or 0.0), draws, "wins_oroma/wins_human"

    if metrics.get("wins_x") is not None or metrics.get("wins_o") is not None:
        return float(metrics.get("wins_x") or 0.0), float(metrics.get("wins_o") or 0.0), draws, "wins_x/wins_o"

    if metrics.get("wins_white") is not None or metrics.get("wins_black") is not None:
        return float(metrics.get("wins_white") or 0.0), float(metrics.get("wins_black") or 0.0), draws, "wins_white/wins_black"

    if metrics.get("wins") is not None:
        return float(metrics.get("wins") or 0.0), 0.0, draws, "wins"

    return 0.0, 0.0, draws, ""


def _compact_kpi(game: str, variant: str, out_row: Dict[str, Any]) -> str:
    """Human-readable, compact per-game KPI string for the existing table.

    This intentionally avoids a dashboard redesign. It gives games with special
    metrics (especially ChessPro, Tetris and PTZ) one compact explanation cell.
    """
    def v(name: str) -> str:
        raw = out_row.get(name)
        if raw is None:
            return ""
        if isinstance(raw, float):
            if abs(raw - round(raw)) < 1e-9:
                return str(int(round(raw)))
            return f"{raw:.3f}".rstrip("0").rstrip(".")
        return str(raw)

    def join_parts(parts: List[str]) -> str:
        return " · ".join([p for p in parts if p and not p.endswith("=")])

    g = (game or "").lower()
    if g == "chess_pro":
        depth_avg = v("depth_reached_avg_avg")
        depth_max = v("depth_reached_max_avg")
        shape_pos = v("shaped_pos_sum")
        shape_neg = v("shaped_neg_sum")
        shape_draw = v("shaped_draw_sum")
        return join_parts([
            f"cp={v('score_cp_avg')}" if v("score_cp_avg") else "",
            f"plies={v('plies_avg')}" if v("plies_avg") else "",
            f"depth={depth_avg}/{depth_max}" if (depth_avg or depth_max) else "",
            f"nps={v('nps_avg')}" if v("nps_avg") else "",
            f"rules={v('rule_hits_sum')}" if v("rule_hits_sum") else "",
            f"learn={v('learn_items_sum')}" if v("learn_items_sum") else "",
            f"shape={shape_pos}/{shape_neg}/{shape_draw}" if (shape_pos or shape_neg or shape_draw) else "",
        ])
    if g == "tetris":
        return join_parts([
            f"score={v('avg_score_end_avg')}" if v("avg_score_end_avg") else "",
            f"high={v('high_score_end_avg')}" if v("high_score_end_avg") else "",
            f"lines={v('avg_lines_end_avg')}" if v("avg_lines_end_avg") else "",
            f"high_lines={v('high_lines_end_avg')}" if v("high_lines_end_avg") else "",
            f"pieces={v('avg_pieces_avg')}" if v("avg_pieces_avg") else "",
            f"learn={v('learn_items_sum')}" if v("learn_items_sum") else "",
            f"credit={v('line_credit_items_sum')}/{v('risk_credit_items_sum')}/{v('topout_credit_items_sum')}" if (v("line_credit_items_sum") or v("risk_credit_items_sum") or v("topout_credit_items_sum")) else "",
            f"pol={v('policy_used_sum')}/{v('policy_fallback_sum')}" if (v("policy_used_sum") or v("policy_fallback_sum")) else "",
            f"acc={v('policy_accepted_sum')}/{v('policy_seen_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_quality_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_quality_sum") or v("policy_rejected_unsafe_sum")) else "",
            f"qrej={v('policy_q_rejected_sum')}" if v("policy_q_rejected_sum") else "",
            f"Δ={v('policy_score_delta_avg_avg')}" if v("policy_score_delta_avg_avg") else "",
            f"gate={v('policy_accept_q_min_avg')}/{v('policy_accept_min_n_avg')}/{v('policy_max_score_gap_avg')}" if (v("policy_accept_q_min_avg") or v("policy_accept_min_n_avg") or v("policy_max_score_gap_avg")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
            f"dbw={v('policy_dbw_chunk_avg')}" if v("policy_dbw_chunk_avg") else "",
            f"holes={v('avg_final_holes_avg')}" if v("avg_final_holes_avg") else "",
        ])
    if g == "ptz_target":
        return join_parts([
            f"lock={v('lock_rate_avg')}" if v("lock_rate_avg") else "",
            f"dist={v('avg_dist_avg')}" if v("avg_dist_avg") else "",
            f"motion={v('avg_motion_avg')}" if v("avg_motion_avg") else "",
            f"sharp={v('avg_sharp_avg')}" if v("avg_sharp_avg") else "",
        ])
    if g == "ptz_coverage":
        return join_parts([
            f"coverage={v('coverage_rate_avg')}" if v("coverage_rate_avg") else "",
            f"cells={v('coverage_unique_cells_sum')}" if v("coverage_unique_cells_sum") else "",
            f"reward={v('avg_reward')}" if v("avg_reward") else "",
        ])
    if g == "tictactoe":
        solved = ""
        if v("solver_states_known_avg") or v("solver_states_total_avg"):
            solved = f"states={v('solver_states_known_avg')}/{v('solver_states_total_avg')}"
        return join_parts([
            f"moves={v('avg_moves_avg')}" if v("avg_moves_avg") else "",
            f"uniq={v('unique_lines_sum')}/{v('unique_openings_sum')}/{v('unique_final_boards_sum')}" if (v("unique_lines_sum") or v("unique_openings_sum") or v("unique_final_boards_sum")) else "",
            solved,
            f"missing={v('solver_states_missing_avg')}" if v("solver_states_missing_avg") else "",
            f"safe={v('solver_safe_draw_items_avg')}" if v("solver_safe_draw_items_avg") else "",
            f"blunder={v('solver_blunder_items_avg')}" if v("solver_blunder_items_avg") else "",
            f"learn={v('learned_items_sum')}/{v('learn_items_sum')}" if (v("learned_items_sum") or v("learn_items_sum")) else "",
            f"done={v('no_more_explore_avg')}" if v("no_more_explore_avg") else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_seen_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum") or v("policy_fallback_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
        ])
    if g == "connect4":
        return join_parts([
            f"moves={v('avg_moves_avg')}" if v("avg_moves_avg") else "",
            f"threat={v('own_win_available_sum')}/{v('opp_win_available_sum')}" if (v("own_win_available_sum") or v("opp_win_available_sum")) else "",
            f"play={v('win_moves_played_sum')}/{v('blocks_played_sum')}/{v('missed_blocks_sum')}" if (v("win_moves_played_sum") or v("blocks_played_sum") or v("missed_blocks_sum")) else "",
            f"learn={v('learned_items_sum')}/{v('learn_items_sum')}" if (v("learned_items_sum") or v("learn_items_sum")) else "",
            f"credit={v('win_move_credit_items_sum')}/{v('block_credit_items_sum')}/{v('missed_block_credit_items_sum')}/{v('terminal_credit_items_sum')}" if (v("win_move_credit_items_sum") or v("block_credit_items_sum") or v("missed_block_credit_items_sum") or v("terminal_credit_items_sum")) else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_seen_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum") or v("policy_fallback_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_unsafe_sum")) else "",
            f"cov={v('pro_rules_known_avg')}/{v('pro_samples_known_avg')}" if (v("pro_rules_known_avg") or v("pro_samples_known_avg")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
        ])
    if g in {"chess", "chess2"} or g.startswith("chess2"):
        return join_parts([f"moves={v('avg_moves_avg')}" if v("avg_moves_avg") else ""])
    if g == "sudoku":
        return join_parts([
            f"moves={v('avg_moves_avg')}" if v("avg_moves_avg") else "",
            f"solve={v('solved_games_sum')}/{v('games_sum')}" if (v("solved_games_sum") or v("games_sum")) else "",
            f"logic={v('avg_logic_moves_avg')}" if v("avg_logic_moves_avg") else "",
            f"assist={v('avg_assist_moves_avg')}" if v("avg_assist_moves_avg") else "",
            f"tech={v('naked_single_moves_sum')}/{v('hidden_row_moves_sum')}/{v('hidden_col_moves_sum')}/{v('hidden_box_moves_sum')}/{v('solution_guard_moves_sum')}" if (v("naked_single_moves_sum") or v("hidden_row_moves_sum") or v("hidden_col_moves_sum") or v("hidden_box_moves_sum") or v("solution_guard_moves_sum")) else "",
            f"mech={v('mechanic_understood_avg')}" if v("mechanic_understood_avg") else "",
            f"expl={v('explore_reduced_avg')}" if v("explore_reduced_avg") else "",
            f"learn={v('learned_items_sum')}/{v('learn_items_sum')}" if (v("learned_items_sum") or v("learn_items_sum")) else "",
            f"credit={v('logic_credit_items_sum')}/{v('assist_credit_items_sum')}/{v('terminal_credit_items_sum')}" if (v("logic_credit_items_sum") or v("assist_credit_items_sum") or v("terminal_credit_items_sum")) else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_seen_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum") or v("policy_fallback_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_unsafe_sum")) else "",
            f"cov={v('pro_rules_known_avg')}/{v('pro_samples_known_avg')}" if (v("pro_rules_known_avg") or v("pro_samples_known_avg")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
        ])
    if g == "memory":
        return join_parts([
            f"turns={v('avg_turns_avg')}" if v("avg_turns_avg") else "",
            f"pairs_left={v('avg_pairs_left_end_avg') or v('avg_pairs_left_end')}" if (v("avg_pairs_left_end_avg") or v("avg_pairs_left_end")) else "",
            f"known={v('avg_peak_known_positions_avg')}/{v('avg_peak_known_pairs_avg')}" if (v("avg_peak_known_positions_avg") or v("avg_peak_known_pairs_avg")) else "",
            f"pair={v('pair_reuse_hits_sum')}" if v("pair_reuse_hits_sum") else "",
            f"blind={v('blind_reveals_sum')}" if v("blind_reveals_sum") else "",
            f"waste={v('repeat_waste_sum')}" if v("repeat_waste_sum") else "",
            f"mech={v('mechanic_understood_avg')}" if v("mechanic_understood_avg") else "",
            f"expl={v('explore_reduced_avg')}" if v("explore_reduced_avg") else "",
            f"learn={v('learned_items_sum')}/{v('learn_items_sum')}" if (v("learned_items_sum") or v("learn_items_sum")) else "",
            f"credit={v('pair_reuse_credit_items_sum')}/{v('info_gain_credit_items_sum')}/{v('repeat_waste_credit_items_sum')}/{v('terminal_credit_items_sum')}" if (v("pair_reuse_credit_items_sum") or v("info_gain_credit_items_sum") or v("repeat_waste_credit_items_sum") or v("terminal_credit_items_sum")) else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_seen_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum") or v("policy_fallback_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_unsafe_sum")) else "",
            f"cov={v('pro_rules_known_avg')}/{v('pro_samples_known_avg')}" if (v("pro_rules_known_avg") or v("pro_samples_known_avg")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
        ])
    if g == "memorymaze_hybrid":
        return join_parts([
            f"pairs_left={v('avg_pairs_left_end')}" if v("avg_pairs_left_end") else "",
            f"cleared={v('avg_pairs_cleared_avg')}" if v("avg_pairs_cleared_avg") else "",
            f"match={v('avg_matches_avg')}" if v("avg_matches_avg") else "",
            f"claim={v('avg_claims_avg')}" if v("avg_claims_avg") else "",
            f"pit={v('avg_pit_hits_avg')}" if v("avg_pit_hits_avg") else "",
            f"learn={v('learn_items_sum')}" if v("learn_items_sum") else "",
            f"pol={v('policy_used_sum')}/{v('policy_fallback_sum')}" if (v("policy_used_sum") or v("policy_fallback_sum")) else "",
        ])
    if g == "pong":
        return join_parts([f"ticks={v('avg_ticks_avg')}" if v("avg_ticks_avg") else ""])
    if g == "snake3d":
        return join_parts([
            f"food={v('avg_food_avg')}" if v("avg_food_avg") else "",
            f"high={v('high_food_avg')}" if v("high_food_avg") else "",
            f"len={v('avg_length_end_avg')}" if v("avg_length_end_avg") else "",
            f"steps={v('avg_steps_avg')}" if v("avg_steps_avg") else "",
            f"learn={v('learned_items_sum')}/{v('learn_items_sum')}" if (v("learned_items_sum") or v("learn_items_sum")) else "",
            f"ev={v('pos_items_sum')}/{v('neg_items_sum')}/{v('draw_items_sum')}" if (v("pos_items_sum") or v("neg_items_sum") or v("draw_items_sum")) else "",
            f"z={v('danger_z_rate_avg')}" if v("danger_z_rate_avg") else "",
            f"food_z={v('food_up_signal_rate_avg')}" if v("food_up_signal_rate_avg") else "",
            f"vert={v('vertical_action_rate_avg')}" if v("vertical_action_rate_avg") else "",
            f"fit={v('template_fit_score_avg')}" if v("template_fit_score_avg") else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_seen_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum") or v("policy_fallback_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_unsafe_sum")) else "",
            f"guard={v('policy_guarded_sum')}" if v("policy_guarded_sum") else "",
            f"sc={v('snapchains_written_sum')}" if v("snapchains_written_sum") else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
        ])
    if g == "snake":
        deaths = ""
        if v("death_wall_sum") or v("death_self_sum"):
            deaths = f"death={v('death_wall_sum')}/{v('death_self_sum')}"
        return join_parts([
            f"food={v('avg_food_avg')}" if v("avg_food_avg") else "",
            f"high={v('high_food_avg')}" if v("high_food_avg") else "",
            f"len={v('avg_length_end_avg')}" if v("avg_length_end_avg") else "",
            f"steps={v('avg_steps_avg')}" if v("avg_steps_avg") else "",
            deaths,
            f"learn={v('learn_items_sum')}" if v("learn_items_sum") else "",
            f"pol={v('policy_used_sum')}/{v('policy_fallback_sum')}" if (v("policy_used_sum") or v("policy_fallback_sum")) else "",
            f"guard={v('policy_guarded_sum')}" if v("policy_guarded_sum") else "",
        ])
    if g == "flappy":
        deaths = ""
        if v("death_world_sum") or v("death_pipe_sum") or v("death_max_steps_sum"):
            deaths = f"death={v('death_world_sum')}/{v('death_pipe_sum')}/{v('death_max_steps_sum')}"
        credit = ""
        if v("pass_credit_items_sum") or v("death_credit_items_sum"):
            credit = f"credit={v('pass_credit_items_sum')}/{v('death_credit_items_sum')}"
        return join_parts([
            f"score={v('avg_score_avg')}" if v("avg_score_avg") else "",
            f"high={v('high_score_avg')}" if v("high_score_avg") else "",
            f"steps={v('avg_steps_avg')}" if v("avg_steps_avg") else "",
            f"high_steps={v('high_steps_avg')}" if v("high_steps_avg") else "",
            f"pass={v('passes_sum')}" if v("passes_sum") else "",
            deaths,
            f"early_world={v('early_world_deaths_sum')}" if v("early_world_deaths_sum") else "",
            f"learn={v('learn_items_sum')}" if v("learn_items_sum") else "",
            credit,
            f"pol={v('policy_used_sum')}/{v('policy_fallback_sum')}" if (v("policy_used_sum") or v("policy_fallback_sum")) else "",
            f"guard={v('policy_guarded_sum')}" if v("policy_guarded_sum") else "",
            f"qrej={v('policy_q_rejected_sum')}" if v("policy_q_rejected_sum") else "",
        ])
    if g == "ctf":
        return join_parts([
            f"score={v('avg_score_A_avg')}/{v('avg_score_B_avg')}" if (v("avg_score_A_avg") or v("avg_score_B_avg")) else "",
            f"steps={v('avg_steps_avg')}" if v("avg_steps_avg") else "",
            f"events=s{v('scores_A_sum')}/{v('scores_B_sum')}" if (v("scores_A_sum") or v("scores_B_sum")) else "",
            f"carry={v('carries_A_sum')}/{v('carries_B_sum')}" if (v("carries_A_sum") or v("carries_B_sum")) else "",
            f"drop={v('drops_A_sum')}/{v('drops_B_sum')}" if (v("drops_A_sum") or v("drops_B_sum")) else "",
            f"learn={v('learn_items_sum')}" if v("learn_items_sum") else "",
            f"credit={v('score_credit_items_sum')}/{v('carry_credit_items_sum')}/{v('tag_credit_items_sum')}/{v('terminal_credit_items_sum')}" if (v("score_credit_items_sum") or v("carry_credit_items_sum") or v("tag_credit_items_sum") or v("terminal_credit_items_sum")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
            f"win={v('score_credit_steps_avg')}/{v('carry_credit_steps_avg')}/{v('tag_credit_steps_avg')}/{v('terminal_credit_steps_avg')}" if (v("score_credit_steps_avg") or v("carry_credit_steps_avg") or v("tag_credit_steps_avg") or v("terminal_credit_steps_avg")) else "",
            f"dbw={v('policy_dbw_chunk_avg')}" if v("policy_dbw_chunk_avg") else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_fallback_sum")) else "",
            f"seen={v('policy_seen_sum')}" if v("policy_seen_sum") else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_unsafe_sum")) else "",
        ])
    if g == "hideseek":
        return join_parts([
            f"steps={v('avg_steps_avg')}" if v("avg_steps_avg") else "",
            f"found={v('avg_found_avg')}" if v("avg_found_avg") else "",
            f"cap={v('captures_sum')}" if v("captures_sum") else "",
            f"target={v('target_known_steps_sum')}" if v("target_known_steps_sum") else "",
            f"path={v('path_moves_played_sum')}" if v("path_moves_played_sum") else "",
            f"learn={v('learned_items_sum')}/{v('learn_items_sum')}" if (v("learned_items_sum") or v("learn_items_sum")) else "",
            f"credit={v('capture_credit_items_sum')}/{v('path_credit_items_sum')}/{v('missed_path_credit_items_sum')}/{v('timeout_credit_items_sum')}/{v('terminal_credit_items_sum')}" if (v("capture_credit_items_sum") or v("path_credit_items_sum") or v("missed_path_credit_items_sum") or v("timeout_credit_items_sum") or v("terminal_credit_items_sum")) else "",
            f"pol={v('policy_accepted_sum')}/{v('policy_seen_sum')}/{v('policy_fallback_sum')}" if (v("policy_accepted_sum") or v("policy_seen_sum") or v("policy_fallback_sum")) else "",
            f"rej={v('policy_rejected_n_sum')}/{v('policy_rejected_q_sum')}/{v('policy_rejected_unsafe_sum')}" if (v("policy_rejected_n_sum") or v("policy_rejected_q_sum") or v("policy_rejected_unsafe_sum")) else "",
            f"cov={v('pro_rules_known_avg')}/{v('pro_samples_known_avg')}" if (v("pro_rules_known_avg") or v("pro_samples_known_avg")) else "",
            f"ms={v('sim_duration_ms_avg')}/{v('learn_duration_ms_avg')}" if (v("sim_duration_ms_avg") or v("learn_duration_ms_avg")) else "",
        ])
    return ""



@games_bp.route("/api/daily_summary", methods=["GET"])
def api_daily_summary():
    """Daily summary of game episodes for /games.

    Design goals:
      • Read-only aggregation; no schema changes.
      • Close DB connections deterministically (avoid locks).
      • Works across heterogeneous games by aggregating common metrics if present.
      • Preserve game-specific learning signals without forcing every game into
        the same outcome-only schema.
    """
    from flask import request

    def _safe_div(a: Any, b: Any) -> Optional[float]:
        """Small helper: safe division returning None on invalid input."""
        try:
            af = float(a)
            bf = float(b)
        except Exception:
            return None
        if bf == 0.0:
            return None
        return af / bf

    def _r6(v: Any) -> Optional[float]:
        """Round numeric to 6 decimals; return None if not finite."""
        try:
            f = float(v)
        except Exception:
            return None
        if not (f == f):
            return None
        return round(f, 6)

    try:
        days = int(request.args.get("days", "30") or "30")
    except Exception:
        days = 30
    days = max(1, min(365, days))

    game_filter = (request.args.get("game", "") or "").strip()
    variant_filter = (request.args.get("variant", "") or "").strip()

    try:
        limit = int(request.args.get("limit", "1000") or "1000")
    except Exception:
        limit = 1000
    limit = max(50, min(5000, limit))

    ts_min = int(time.time()) - days * 86400

    # Static allow-list: metric names are controlled by DAILY_METRIC_KEYS above,
    # not by request parameters. This keeps the dynamic pivot SQL injection-safe.
    metric_select_sql = ",\n                  ".join(
        f"MAX(CASE WHEN m.key='{k}' THEN m.value END) AS {k}" for k in DAILY_METRIC_KEYS
    )

    rows: List[Dict[str, Any]] = []
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                  e.id,
                  e.kind,
                  e.ts_start,
                  e.ts_end,
                  date(e.ts_start,'unixepoch','localtime') AS day,
                  (e.ts_end - e.ts_start) AS dt_s,
                  {metric_select_sql}
                FROM episodes e
                LEFT JOIN episodic_metrics m ON m.episode_id = e.id
                WHERE e.kind LIKE 'game:%'
                  AND e.ts_start >= ?
                GROUP BY e.id
                ORDER BY e.ts_start DESC
                LIMIT ?
                """,
                (ts_min, limit),
            )

            cols = [d[0] for d in (cur.description or [])]

            def _row_to_dict(row: Any) -> Dict[str, Any]:
                if row is None:
                    return {}
                if isinstance(row, dict):
                    return row
                try:
                    return {k: row[k] for k in cols}
                except Exception:
                    pass
                try:
                    return {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
                except Exception:
                    return {}

            for row in cur.fetchall():
                m = _row_to_dict(row)
                item: Dict[str, Any] = {
                    "kind": m.get("kind") or "",
                    "ts_start": int(m.get("ts_start") or 0),
                    "ts_end": int(m.get("ts_end") or 0),
                    "day": m.get("day") or "",
                    "dt_s": int(m.get("dt_s") or 0),
                }
                for mk in DAILY_METRIC_KEYS:
                    item[mk] = _to_float(m.get(mk))
                rows.append(item)
    except Exception as e:
        log.exception("/games/api/daily_summary failed")
        return jsonify({"ok": False, "err": f"{type(e).__name__}: {e}", "days": days, "rows": []})

    agg: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    present_games: Dict[str, int] = {}
    for r in rows:
        day = r.get("day") or ""
        kind = r.get("kind") or ""
        game, variant = _kind_to_game_variant(kind)

        if game_filter and game != game_filter:
            continue
        if variant_filter and variant != variant_filter:
            continue

        present_games[game] = present_games.get(game, 0) + 1
        batch = "policy" if kind.endswith(":policy_batch") else ("explore" if kind.endswith(":explore_batch") else "")
        key = (day, game, variant)
        a = agg.get(key)
        if a is None:
            a = {
                "day": day,
                "game": game,
                "variant": variant,
                "policy_n": 0,
                "explore_n": 0,
                "ts_start_min": 0,
                "ts_end_max": 0,
                "dt_s_sum": 0,
                "dt_s_n": 0,
                "wins": 0.0,
                "losses": 0.0,
                "draws": 0.0,
                "wins_sources": {},
            }
            agg[key] = a

        if batch == "policy":
            a["policy_n"] += 1
        elif batch == "explore":
            a["explore_n"] += 1

        try:
            ts0 = int(r.get("ts_start") or 0)
            ts1 = int(r.get("ts_end") or 0)
        except Exception:
            ts0 = 0
            ts1 = 0
        if ts0 > 0:
            if not a.get("ts_start_min") or ts0 < int(a.get("ts_start_min") or 0):
                a["ts_start_min"] = ts0
        if ts1 > 0:
            if ts1 > int(a.get("ts_end_max") or 0):
                a["ts_end_max"] = ts1

        dt_s = int(r.get("dt_s") or 0)
        if dt_s > 0:
            a["dt_s_sum"] += dt_s
            a["dt_s_n"] += 1

        # Aggregate every known metric into generic *_sum / *_n / *_seen buckets.
        # Sum metrics keep totals. Average metrics preserve both sum and count so
        # the frontend receives stable daily averages.
        for mk in DAILY_METRIC_KEYS:
            val = r.get(mk)
            if not isinstance(val, (int, float)):
                continue
            a[f"{mk}_sum"] = float(a.get(f"{mk}_sum") or 0.0) + float(val)
            a[f"{mk}_seen"] = int(a.get(f"{mk}_seen") or 0) + 1
            if mk in AVG_METRIC_KEYS:
                a[f"{mk}_n"] = int(a.get(f"{mk}_n") or 0) + 1

        ow, ol, od, osrc = _normalise_outcome_counts(game, variant, r)
        a["wins"] += ow
        a["losses"] += ol
        a["draws"] += od
        if osrc:
            sources = a.setdefault("wins_sources", {})
            sources[osrc] = int(sources.get(osrc, 0) or 0) + 1

        # Preserve the previous UI semantics: highscore means best episode score
        # inside the day/game/variant bucket, not the daily average. The generic
        # score_avg below remains the mean; highscore is the maximum comparable
        # score-like value available for this runner.
        score_val = None
        for sk in ("score_cp", "high_score", "avg_score_end", "avg_score", "avg_return", "avg_found", "avg_ticks"):
            rv = r.get(sk)
            if isinstance(rv, (int, float)):
                score_val = float(rv)
                break
        if score_val is None:
            sa = r.get("avg_score_A")
            sb = r.get("avg_score_B")
            if isinstance(sa, (int, float)) or isinstance(sb, (int, float)):
                score_val = float(sa or 0.0) + float(sb or 0.0)
        if score_val is not None:
            prev = a.get("highscore")
            if prev is None or float(score_val) > float(prev):
                a["highscore"] = float(score_val)

    out_rows: List[Dict[str, Any]] = []
    for a in agg.values():
        dt_s_avg = (a["dt_s_sum"] / a["dt_s_n"]) if a["dt_s_n"] else 0.0

        # Score/Performance metrics (best-effort, per game). The generic score
        # column remains backwards compatible, but the compact KPI field below
        # carries richer game-specific information.
        score_key = None
        score_avg = None
        highscore = None
        if a.get("score_cp_seen"):
            score_key = "score_cp"
            score_avg = _metric_avg(a, "score_cp")
            highscore = score_avg
        elif a.get("avg_score_end_seen"):
            score_key = "avg_score_end"
            score_avg = _metric_avg(a, "avg_score_end")
            highscore = score_avg
        else:
            for sk in ("high_score", "avg_score", "avg_return", "avg_found", "avg_ticks"):
                if a.get(f"{sk}_seen"):
                    score_key = sk
                    score_avg = _metric_avg(a, sk)
                    highscore = score_avg
                    break

        scoreA_avg = _metric_avg(a, "avg_score_A")
        scoreB_avg = _metric_avg(a, "avg_score_B")
        if score_avg is None and (scoreA_avg is not None or scoreB_avg is not None):
            score_key = "avg_score_A/B"
            score_avg = float(scoreA_avg or 0.0) + float(scoreB_avg or 0.0)
            highscore = score_avg

        out: Dict[str, Any] = {
            "day": a["day"],
            "game": a["game"],
            "variant": a["variant"],
            "policy_n": a["policy_n"],
            "explore_n": a["explore_n"],
            "n_total": int(a["policy_n"] + a["explore_n"]),
            "start": _fmt_time_local(a.get("ts_start_min") or 0),
            "end": _fmt_time_local(a.get("ts_end_max") or 0),
            "dt_s_avg": round(dt_s_avg, 2),
            "duration_ms_avg": _r6(_metric_avg(a, "duration_ms")),
            "avg_reward": _r6(_metric_avg(a, "avg_reward")),
            "score_avg": _r6(score_avg),
            "score_key": score_key,
            "highscore": _r6(a.get("highscore")),
            "scoreA_avg": _r6(scoreA_avg),
            "scoreB_avg": _r6(scoreB_avg),
            "wins": a["wins"],
            "losses": a["losses"],
            "draws": a["draws"],
            "wins_source": ",".join(sorted((a.get("wins_sources") or {}).keys())),
            "avg_pairs_left_end": _r6(_metric_avg(a, "avg_pairs_left_end")),
            "avg_pairs_cleared": _r6(_metric_avg(a, "avg_pairs_cleared")),
            "avg_strikes_p1": _r6(_metric_avg(a, "avg_strikes_p1")),
            "avg_strikes_p2": _r6(_metric_avg(a, "avg_strikes_p2")),
            "avg_strikes_p3": _r6(_metric_avg(a, "avg_strikes_p3")),
        }

        # Expose normalized metric details for API users and for the compact KPI
        # table column. Sums and averages use explicit suffixes.
        for mk in DAILY_METRIC_KEYS:
            if mk in SUM_METRIC_KEYS:
                v = _metric_sum(a, mk)
                if v is not None:
                    out[f"{mk}_sum"] = _r6(v)
            else:
                v = _metric_avg(a, mk)
                if v is not None:
                    out[f"{mk}_avg"] = _r6(v)

        out["kpi"] = _compact_kpi(str(out.get("game") or ""), str(out.get("variant") or ""), out)
        out_rows.append(out)

    out_rows.sort(key=lambda x: (x.get("day", ""), x.get("game", ""), x.get("variant", "")), reverse=True)
    return jsonify({
        "ok": True,
        "days": days,
        "limit": limit,
        "filters": {"game": game_filter, "variant": variant_filter},
        "games_present": sorted(present_games.keys()),
        "rows": out_rows,
    })


@games_bp.route("/chesspro/", methods=["GET"])
def chesspro_page() -> str:
    """Small ChessPro status page under /games/chesspro/.

    This is intentionally not a full board UI. ChessPro is headless and learns
    from long-search decisions; therefore the useful UI is its latest episode,
    policy_rules status and decision-learning telemetry.
    """
    summary: Dict[str, Any] = {"ok": True, "latest": None, "policy": {}, "err": ""}
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, ts_start, ts_end, kind, label, meta_json
                FROM episodes
                WHERE kind LIKE 'game:chess_pro:%'
                ORDER BY ts_start DESC
                LIMIT 1
                """
            )
            cols = [d[0] for d in (cur.description or [])]
            row = cur.fetchone()
            if row is not None:
                try:
                    latest = {k: row[k] for k in cols}
                except Exception:
                    latest = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
                eid = int(latest.get("id") or 0)
                cur.execute(
                    "SELECT key, value FROM episodic_metrics WHERE episode_id=? ORDER BY key",
                    (eid,),
                )
                metrics = {str(k): _to_float(v) for k, v in cur.fetchall()}
                latest["start"] = _fmt_time_local(latest.get("ts_start") or 0)
                latest["end"] = _fmt_time_local(latest.get("ts_end") or 0)
                latest["metrics"] = metrics
                summary["latest"] = latest

            cur.execute(
                """
                SELECT
                  COUNT(*) AS rules,
                  COALESCE(SUM(n),0) AS n_sum,
                  COALESCE(SUM(pos),0) AS pos_sum,
                  COALESCE(SUM(neg),0) AS neg_sum,
                  COALESCE(SUM(draw),0) AS draw_sum,
                  COALESCE(AVG(q),0.0) AS q_avg,
                  COALESCE(MIN(q),0.0) AS q_min,
                  COALESCE(MAX(q),0.0) AS q_max,
                  COALESCE(MAX(last_ts),0) AS last_ts
                FROM policy_rules
                WHERE namespace='game:chess_pro'
                """
            )
            cols = [d[0] for d in (cur.description or [])]
            prow = cur.fetchone()
            if prow is not None:
                try:
                    policy = {k: prow[k] for k in cols}
                except Exception:
                    policy = {cols[i]: prow[i] for i in range(min(len(cols), len(prow)))}
                policy["last_time"] = _fmt_time_local(policy.get("last_ts") or 0)
                summary["policy"] = policy
    except Exception as e:
        log.exception("/games/chesspro failed")
        summary = {"ok": False, "latest": None, "policy": {}, "err": f"{type(e).__name__}: {e}"}

    return render_template_string(
        """
{% extends "base.html" %}
{% block content %}
<main style="padding:2rem; max-width:1100px; margin:auto;">
  <h1>♟️ ORÓMA – ChessPro</h1>
  <p class="text-muted">
    Headless Long-Search-Schach aus <code>core/chess_pro/</code>. ChessPro wird hier als normales Spiel geführt;
    entscheidend sind Positions-Trace, Centipawn-Bewertung, Rule-Hits und geformte Lernitems je Halbzug.
  </p>
  {% if not summary.ok %}
    <div class="alert alert-danger">Fehler: {{ summary.err }}</div>
  {% endif %}

  <h3 class="mt-4">Letzter Lauf</h3>
  {% if summary.latest %}
    <table class="table table-dark table-striped table-sm">
      <tbody>
        <tr><th>Kind</th><td><code>{{ summary.latest.kind }}</code></td></tr>
        <tr><th>Zeit</th><td>{{ summary.latest.start }} – {{ summary.latest.end }}</td></tr>
        <tr><th>Label</th><td>{{ summary.latest.label or '–' }}</td></tr>
      </tbody>
    </table>

    <h4 class="mt-4">Lern-/Suchmetriken</h4>
    <table class="table table-dark table-striped table-sm">
      <thead><tr><th>Metrik</th><th class="text-end">Wert</th></tr></thead>
      <tbody>
      {% for k in ['score_cp','plies','nodes','qnodes','total_nodes','search_ms','nps','depth_reached_avg','depth_reached_max','rule_hits','learn_items','learn_items_pos','learn_items_neg','learn_items_draw','shaped_pos','shaped_neg','shaped_draw','snapchain_steps','wins','wins_white','wins_black','draws'] %}
        <tr><td><code>{{ k }}</code></td><td class="text-end">{{ summary.latest.metrics.get(k, '') }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  {% else %}
    <div class="alert alert-warning">Noch kein ChessPro-Lauf in <code>episodes</code> gefunden.</div>
  {% endif %}

  <h3 class="mt-4">Policy Rules</h3>
  <table class="table table-dark table-striped table-sm">
    <tbody>
      <tr><th>Namespace</th><td><code>game:chess_pro</code></td></tr>
      <tr><th>Regeln</th><td>{{ summary.policy.rules or 0 }}</td></tr>
      <tr><th>n_sum</th><td>{{ summary.policy.n_sum or 0 }}</td></tr>
      <tr><th>pos / neg / draw</th><td>{{ summary.policy.pos_sum or 0 }} / {{ summary.policy.neg_sum or 0 }} / {{ summary.policy.draw_sum or 0 }}</td></tr>
      <tr><th>q Ø / min / max</th><td>{{ summary.policy.q_avg or 0 }} / {{ summary.policy.q_min or 0 }} / {{ summary.policy.q_max or 0 }}</td></tr>
      <tr><th>letztes Update</th><td>{{ summary.policy.last_time or '–' }}</td></tr>
    </tbody>
  </table>

  <p><a class="btn btn-sm btn-outline-primary" href="/games/">← zurück zur Spieleliste</a></p>
</main>
{% endblock %}
        """,
        summary=summary,
    )


def register_games(app) -> None:
    ok = 0
    for g in GAMES:
        if g.module == "__internal__":
            # Internal status-only games live under games_bp. They are marked as
            # available here, while games_bp itself is registered once below.
            g.url = (g.url_hint or f"/games/{g.key}").rstrip("/") or "/"
            g.registered_as = "games"
            g.error = None
            ok += 1
            log.debug(f"Internes Spiel registriert: {g.title} @ {g.url}")
            continue

        bp, err = _import_blueprint(g.module, g.attr_fallbacks)
        if bp is None:
            g.error = err
            log.warning(f"{g.title} nicht verfügbar: {err}")
            continue
        bp_name, err = _safe_register(app, bp, g.key)
        if not bp_name:
            g.error = err
            log.error(f"{g.title} konnte nicht registriert werden: {err}")
            continue
        g.url = getattr(bp, "url_prefix", None) or g.url_hint or f"/{g.key}"
        g.url = g.url.rstrip("/") or "/"
        g.registered_as = bp_name
        g.error = None
        ok += 1
        log.debug(f"Spiel registriert: {g.title} @ {g.url}")

    _safe_register(app, games_bp, "games")
    log.info(f"Games fertig: {ok}/{len(GAMES)} registriert.")
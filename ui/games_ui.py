#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/games_ui.py
# Projekt: ORÓMA – Headless UI (kein Qt/Wayland/X11)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Registriert verfügbare Spiel-Blueprints und stellt eine schnelle
#   Übersichtsseite unter /games bereit (nur Liste + Notizen, kein Preview).
#
# Erweiterung v3.7.3
# ──────────────────
#   • Sudoku hinzugefügt: ui.sudoku_ui → /sudoku
#
# =============================================================================

from __future__ import annotations

import logging
import os
import importlib
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any, Dict

from flask import Blueprint, render_template, jsonify

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
    GameMeta("pong",       "Pong",              "ui.pong_ui",       ["pong_bp", "bp"],         "/pong",       "Headless Pong (Canvas)"),
    GameMeta("flappy",     "Flappy",            "ui.flappy_ui",     ["bp", "flappy_bp"],       "/flappy",     "Erfordert pygame/OpenCV"),
    GameMeta("ctf",        "Capture the Flag",  "ui.ctf_ui",        ["bp", "ctf_bp"],          "/ctf",        None),
    GameMeta("hideseek",   "Hide & Seek",       "ui.hideseek_ui",   ["bp", "hideseek_bp"],     "/hideseek",   None),
    GameMeta("ptz_arena",  "PTZ Arena",         "ui.ptz_arena_ui",  ["ptz_arena_bp", "bp"],   "/ptz_arena",  "PTZ Policy Training (DeviceHub)"),
    GameMeta("ptz_target", "PTZ Target",        "ui.ptz_target_ui", ["ptz_target_bp", "bp"], "/ptz_target", "PTZ Targeting (Motion-Centroid)"),
    GameMeta("ptz_coverage", "PTZ Coverage",     "ui.ptz_coverage_ui", ["ptz_coverage_bp", "bp"], "/ptz_coverage", "Staubsauger-Sweep (Coverage über stats.db)"),
    GameMeta("memory",     "Memory",            "ui.memory_ui",     ["bp", "memory_bp"],       "/memory",     None),
    GameMeta("tictactoe",  "Tic Tac Toe",       "ui.tictactoe_ui",  ["bp", "tictactoe_bp"],    "/tictactoe",  "9-Felder-Board, KI/Heuristik"),
    GameMeta("tetris",     "Tetris",            "ui.tetris_ui",     ["tetris_bp", "bp"],   "/tetris",   "WASD/←↑→↓, SPACE=Hard Drop, Autoplay client-side"),
    GameMeta("connect4",   "Connect Four",      "ui.connect4_ui",   ["bp", "connect4_bp"],     "/connect4",   None),
    GameMeta("memorymaze", "MemoryMaze Hybrid", "ui.memorymaze_ui", ["bp", "memorymaze_bp"],   "/memorymaze", "PacMan-Maze + Memory-Blocker + Items (Hybrid)"),
    GameMeta("vs",         "ORÓMA vs ORÓMA",    "ui.vs_ui",         ["bp", "vs_bp"],           "/vs",         None),
    GameMeta("chess",      "Chess",             "ui.chess_ui",      ["chess_bp", "bp"],        "/chess",      "Mini-Schach UI (PURE)"),
    GameMeta("chess2",     "Chess2",            "ui.chess2_ui",     ["chess2_bp", "bp"],      "/chess2",     "Mobility-native Chess Parallel-Stack"),
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


@games_bp.route("/api/daily_summary", methods=["GET"])
def api_daily_summary():
    """Daily summary of game episodes for /games.

    Design goals:
      • Read-only aggregation; no schema changes.
      • Close DB connections deterministically (avoid locks).
      • Works across heterogeneous games by aggregating common metrics if present.
    """
    # NOTE
    #   This endpoint is intentionally read-only and must be stable even if
    #   SQLite row factories differ (tuple vs sqlite3.Row vs dict-like).
    #   Therefore we normalize rows using cursor.description and access values
    #   by column name.
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

    rows: List[Dict[str, Any]] = []
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                  e.id,
                  e.kind,
                  e.ts_start,
                  e.ts_end,
                  date(e.ts_start,'unixepoch','localtime') AS day,
                  (e.ts_end - e.ts_start) AS dt_s,
                  MAX(CASE WHEN m.key='duration_ms' THEN m.value END) AS duration_ms,
                  MAX(CASE WHEN m.key='avg_reward' THEN m.value END) AS avg_reward,
                  MAX(CASE WHEN m.key='avg_score' THEN m.value END) AS avg_score,
        MAX(CASE WHEN m.key='avg_return' THEN m.value END) AS avg_return,
        MAX(CASE WHEN m.key='avg_found' THEN m.value END) AS avg_found,
        MAX(CASE WHEN m.key='avg_ticks' THEN m.value END) AS avg_ticks,
        MAX(CASE WHEN m.key='avg_score_A' THEN m.value END) AS avg_score_A,
        MAX(CASE WHEN m.key='avg_score_B' THEN m.value END) AS avg_score_B,
MAX(CASE WHEN m.key='wins_p1' THEN m.value END) AS wins_p1,
                  MAX(CASE WHEN m.key='wins_p2' THEN m.value END) AS wins_p2,
                  MAX(CASE WHEN m.key='wins_oroma' THEN m.value END) AS wins_oroma,
                  MAX(CASE WHEN m.key='wins_human' THEN m.value END) AS wins_human,
                  MAX(CASE WHEN m.key='draws' THEN m.value END) AS draws,
                  MAX(CASE WHEN m.key='avg_pairs_left_end' THEN m.value END) AS avg_pairs_left_end,
                  MAX(CASE WHEN m.key='avg_strikes_p1' THEN m.value END) AS avg_strikes_p1,
                  MAX(CASE WHEN m.key='avg_strikes_p2' THEN m.value END) AS avg_strikes_p2,
                  MAX(CASE WHEN m.key='avg_strikes_p3' THEN m.value END) AS avg_strikes_p3
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
                rows.append(
                    {
                        "kind": m.get("kind") or "",
                        "ts_start": int(m.get("ts_start") or 0),
                        "ts_end": int(m.get("ts_end") or 0),
                        "day": m.get("day") or "",
                        "dt_s": int(m.get("dt_s") or 0),
                        "duration_ms": float(m["duration_ms"]) if m.get("duration_ms") is not None else None,
                        "avg_reward": float(m["avg_reward"]) if m.get("avg_reward") is not None else None,
                        # Raw score-like metrics (best-effort; used later for aggregation)
                        "avg_score": float(m["avg_score"]) if m.get("avg_score") is not None else None,
                        "avg_return": float(m["avg_return"]) if m.get("avg_return") is not None else None,
                        "avg_found": float(m["avg_found"]) if m.get("avg_found") is not None else None,
                        "avg_ticks": float(m["avg_ticks"]) if m.get("avg_ticks") is not None else None,
                        "avg_score_A": float(m["avg_score_A"]) if m.get("avg_score_A") is not None else None,
                        "avg_score_B": float(m["avg_score_B"]) if m.get("avg_score_B") is not None else None,
                        "wins_p1": float(m["wins_p1"]) if m.get("wins_p1") is not None else None,
                        "wins_p2": float(m["wins_p2"]) if m.get("wins_p2") is not None else None,
                        "wins_oroma": float(m["wins_oroma"]) if m.get("wins_oroma") is not None else None,
                        "wins_human": float(m["wins_human"]) if m.get("wins_human") is not None else None,
                        "draws": float(m["draws"]) if m.get("draws") is not None else None,
                        "avg_pairs_left_end": float(m["avg_pairs_left_end"]) if m.get("avg_pairs_left_end") is not None else None,
                        "avg_strikes_p1": float(m["avg_strikes_p1"]) if m.get("avg_strikes_p1") is not None else None,
                        "avg_strikes_p2": float(m["avg_strikes_p2"]) if m.get("avg_strikes_p2") is not None else None,
                        "avg_strikes_p3": float(m["avg_strikes_p3"]) if m.get("avg_strikes_p3") is not None else None,
                    }
                )
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
                # best-effort local time window (for UI visibility)
                "ts_start_min": 0,
                "ts_end_max": 0,
                "dt_s_sum": 0,
                "dt_s_n": 0,
                "duration_ms_sum": 0.0,
                "duration_ms_n": 0,
                "avg_reward_sum": 0.0,
                "avg_reward_n": 0,
                "wins": 0.0,
                "losses": 0.0,
                "draws": 0.0,
                "avg_pairs_left_end_sum": 0.0,
                "avg_pairs_left_end_n": 0,
                "avg_strikes_p1_sum": 0.0,
                "avg_strikes_p1_n": 0,
                "avg_strikes_p2_sum": 0.0,
                "avg_strikes_p2_n": 0,
                "avg_strikes_p3_sum": 0.0,
                "avg_strikes_p3_n": 0,
            }
            agg[key] = a

        if batch == "policy":
            a["policy_n"] += 1
        elif batch == "explore":
            a["explore_n"] += 1

        # best-effort daily window (min start / max end)
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

        dms = r.get("duration_ms")
        if isinstance(dms, (int, float)) and dms >= 0:
            a["duration_ms_sum"] += float(dms)
            a["duration_ms_n"] += 1

        ar = r.get("avg_reward")
        if isinstance(ar, (int, float)):
            a["avg_reward_sum"] += float(ar)
            a["avg_reward_n"] += 1


        # --- Score/Performance metrics (best-effort, per game) ---
        # Some games don't produce avg_reward. For UI/telemetry we also track a generic score_avg
        # and a highscore (best episode value) based on whichever score-like metrics exist.
        score_key = None
        score_val = None

        # Primary numeric score-like metrics
        for _k in ("avg_score", "avg_return", "avg_found", "avg_ticks"):
            _v = r.get(_k)
            if isinstance(_v, (int, float)):
                score_key = _k
                score_val = float(_v)
                break

        # CTF has two channels; we aggregate both for display and for comparisons (A+B).
        scoreA = r.get("avg_score_A")
        scoreB = r.get("avg_score_B")
        if score_val is None and (isinstance(scoreA, (int, float)) or isinstance(scoreB, (int, float))):
            score_key = "avg_score_A/B"
            a_val = float(scoreA) if isinstance(scoreA, (int, float)) else 0.0
            b_val = float(scoreB) if isinstance(scoreB, (int, float)) else 0.0
            score_val = a_val + b_val
            a.setdefault("scoreA_sum", 0.0); a.setdefault("scoreA_n", 0)
            a.setdefault("scoreB_sum", 0.0); a.setdefault("scoreB_n", 0)
            if isinstance(scoreA, (int, float)):
                a["scoreA_sum"] += float(scoreA); a["scoreA_n"] += 1
            if isinstance(scoreB, (int, float)):
                a["scoreB_sum"] += float(scoreB); a["scoreB_n"] += 1

        if score_val is not None:
            a.setdefault("score_sum", 0.0); a.setdefault("score_n", 0)
            a["score_sum"] += float(score_val); a["score_n"] += 1
            a["score_key"] = score_key

            # highscore = best episode score within the day+game+variant group
            a.setdefault("highscore", None)
            if a["highscore"] is None or score_val > float(a["highscore"]):
                a["highscore"] = float(score_val)

        if r.get("wins_p1") is not None or r.get("wins_p2") is not None:
            a["wins"] += float(r.get("wins_p1") or 0.0)
            a["losses"] += float(r.get("wins_p2") or 0.0)
        elif r.get("wins_oroma") is not None or r.get("wins_human") is not None:
            a["wins"] += float(r.get("wins_oroma") or 0.0)
            a["losses"] += float(r.get("wins_human") or 0.0)
        a["draws"] += float(r.get("draws") or 0.0)

        apl = r.get("avg_pairs_left_end")
        if isinstance(apl, (int, float)):
            a["avg_pairs_left_end_sum"] += float(apl)
            a["avg_pairs_left_end_n"] += 1

        s1 = r.get("avg_strikes_p1")
        if isinstance(s1, (int, float)):
            a["avg_strikes_p1_sum"] += float(s1)
            a["avg_strikes_p1_n"] += 1
        s2 = r.get("avg_strikes_p2")
        if isinstance(s2, (int, float)):
            a["avg_strikes_p2_sum"] += float(s2)
            a["avg_strikes_p2_n"] += 1
        s3 = r.get("avg_strikes_p3")
        if isinstance(s3, (int, float)):
            a["avg_strikes_p3_sum"] += float(s3)
            a["avg_strikes_p3_n"] += 1

    out_rows: List[Dict[str, Any]] = []
    for a in agg.values():
        dt_s_avg = (a["dt_s_sum"] / a["dt_s_n"]) if a["dt_s_n"] else 0.0
        dur_ms_avg = (a["duration_ms_sum"] / a["duration_ms_n"]) if a["duration_ms_n"] else None
        ar_avg = (a["avg_reward_sum"] / a["avg_reward_n"]) if a["avg_reward_n"] else None
        apl_avg = (a["avg_pairs_left_end_sum"] / a["avg_pairs_left_end_n"]) if a["avg_pairs_left_end_n"] else None
        s1_avg = (a["avg_strikes_p1_sum"] / a["avg_strikes_p1_n"]) if a["avg_strikes_p1_n"] else None
        s2_avg = (a["avg_strikes_p2_sum"] / a["avg_strikes_p2_n"]) if a["avg_strikes_p2_n"] else None
        s3_avg = (a["avg_strikes_p3_sum"] / a["avg_strikes_p3_n"]) if a["avg_strikes_p3_n"] else None
        out_rows.append(
            {
                "day": a["day"],
                "game": a["game"],
                "variant": a["variant"],
                "policy_n": a["policy_n"],
                "explore_n": a["explore_n"],
                "n_total": int(a["policy_n"] + a["explore_n"]),
                "start": _fmt_time_local(a.get("ts_start_min") or 0),
                "end": _fmt_time_local(a.get("ts_end_max") or 0),
                "dt_s_avg": round(dt_s_avg, 2),
                "duration_ms_avg": round(dur_ms_avg, 2) if dur_ms_avg is not None else None,
                "avg_reward": round(ar_avg, 6) if ar_avg is not None else None,
                # Score-like metrics (best-effort)
                "score_avg": _r6(_safe_div(a.get("score_sum"), a.get("score_n"))),
                "score_key": a.get("score_key"),
                "highscore": _r6(a.get("highscore")),
                "scoreA_avg": _r6(_safe_div(a.get("scoreA_sum"), a.get("scoreA_n"))),
                "scoreB_avg": _r6(_safe_div(a.get("scoreB_sum"), a.get("scoreB_n"))),
                "wins": a["wins"],
                "losses": a["losses"],
                "draws": a["draws"],
                "avg_pairs_left_end": round(apl_avg, 3) if apl_avg is not None else None,
                "avg_strikes_p1": round(s1_avg, 3) if s1_avg is not None else None,
                "avg_strikes_p2": round(s2_avg, 3) if s2_avg is not None else None,
                "avg_strikes_p3": round(s3_avg, 3) if s3_avg is not None else None,
            }
        )

    out_rows.sort(key=lambda x: (x.get("day", ""), x.get("game", ""), x.get("variant", "")), reverse=True)
    return jsonify({
        "ok": True,
        "days": days,
        "limit": limit,
        "filters": {"game": game_filter, "variant": variant_filter},
        "games_present": sorted(present_games.keys()),
        "rows": out_rows,
    })


def register_games(app) -> None:
    ok = 0
    for g in GAMES:
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
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/sudoku_ui.py
# Projekt: ORÓMA – Headless UI – Sudoku
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Flask-Blueprint für ein headless Sudoku-Spiel als „Langeweile-Brecher“.
#   Enthält Generator, Check, Hint und (neu) einen ORÓMA-Autoplay-Modus, bei dem
#   du dem System beim Lösen zuschauen kannst.
#
# Routen
# ──────
#   GET  /sudoku                 → HTML UI
#   GET  /sudoku/api/new          → neues Puzzle (difficulty, optional seed)
#   POST /sudoku/api/check        → Konsistenz prüfen + solved Status
#   POST /sudoku/api/hint         → korrekte Zahl für eine leere Zelle
#
#   ORÓMA-Play (zuschauen + Stop-Button):
#   POST /sudoku/api/oroma/start  → ORÓMA-Session starten (Moves vorbereiten)
#   POST /sudoku/api/oroma/next   → nächsten Move abrufen (UI pollt/animiert)
#   POST /sudoku/api/oroma/stop   → ORÓMA-Session stoppen
#   GET  /sudoku/api/oroma/status → Status (playing/cursor/total/done)
#
# Persistenz / Logging (ohne Schema-Änderung)
# ───────────────────────────────────────────
#   Nutzt vorhandene Tabellen:
#     • episodes
#     • episode_events
#     • snapchains (optional, nur Summary bei solved)
#
# Logging-Events (episode_events)
# ──────────────────────────────
#   sudoku_start, sudoku_check, sudoku_hint, sudoku_solved
#   sudoku_oroma_start, sudoku_oroma_move, sudoku_oroma_stop, sudoku_oroma_done
#
# Design / Sicherheit
# ──────────────────
#   • Keine GUI-Abhängigkeiten. Kein Qt/Wayland/X11.
#   • Kein Hintergrund-Thread: Autoplay ist “servergesteuert, client-getaktet”.
#     Das UI holt periodisch /oroma/next und zeigt Moves an.
#   • “Stop” ist hart: serverseitig wird playing=false gesetzt, /next liefert dann
#     sofort playing=false zurück.
#
# =============================================================================

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Dict, Optional, List

from flask import Blueprint, jsonify, render_template, request

from core import sudoku_game
import logging
from core.log_guard import log_suppressed

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # graceful

bp = Blueprint("sudoku", __name__, url_prefix="/sudoku", template_folder="templates")

# In-Memory Puzzle Cache:
# _PUZZLES[puzzle_id] = {
#   "ts": int,
#   "seed": int,
#   "difficulty": str,
#   "puzzle": 9x9,
#   "solution": 9x9,
#   "clues": int,
#   "unique": bool,
#   "episode_id": Optional[int],
#   "solved_logged": bool,
#   "oroma": {
#       "playing": bool,
#       "cursor": int,
#       "total": int,
#       "moves": [ {"r":int,"c":int,"value":int}, ... ],
#       "mode": "fill_empty" | "correct",
#       "started_ts": int,
#       "stopped_ts": Optional[int],
#   }
# }
_PUZZLES: Dict[str, Dict[str, Any]] = {}


def _new_id() -> str:
    return secrets.token_urlsafe(10)


def _prune(max_age_sec: int = 6 * 3600, max_items: int = 64) -> None:
    now = int(time.time())
    for pid in list(_PUZZLES.keys()):
        if (now - int(_PUZZLES[pid].get("ts", now))) > int(max_age_sec):
            _PUZZLES.pop(pid, None)
    if len(_PUZZLES) > int(max_items):
        items = sorted(_PUZZLES.items(), key=lambda kv: int(kv[1].get("ts", 0)))
        for pid, _ in items[: max(0, len(_PUZZLES) - int(max_items))]:
            _PUZZLES.pop(pid, None)


def _log_episode_start(seed: int, difficulty: str, clues: int, unique: bool) -> Optional[int]:
    if not sql_manager:
        return None
    ts = int(time.time())
    ep_id = sql_manager.insert_episode(
        ts_start=ts,
        kind="game",
        source="sudoku_ui",
        label=f"sudoku:{difficulty}",
        meta={"seed": seed, "difficulty": difficulty, "clues": clues, "unique": bool(unique)},
    )
    if ep_id:
        sql_manager.insert_episode_event(ep_id, ts, "sudoku_start", meta={"seed": seed, "difficulty": difficulty, "clues": clues})
    return ep_id


def _log_event(ep_id: Optional[int], event_type: str, meta: Optional[Dict[str, Any]] = None) -> None:
    if not sql_manager or not ep_id:
        return
    try:
        sql_manager.insert_episode_event(int(ep_id), int(time.time()), str(event_type), meta=meta or {})
    except Exception as e:
        log_suppressed('ui/sudoku_ui.py:130', exc=e, level=logging.WARNING)
        pass


def _get_rec(pid: str) -> Optional[Dict[str, Any]]:
    if not pid:
        return None
    return _PUZZLES.get(pid)


def _ensure_oroma_state(rec: Dict[str, Any]) -> Dict[str, Any]:
    o = rec.get("oroma")
    if not isinstance(o, dict):
        o = {
            "playing": False,
            "cursor": 0,
            "total": 0,
            "moves": [],
            "mode": "fill_empty",
            "started_ts": 0,
            "stopped_ts": None,
        }
        rec["oroma"] = o
    return o


def _build_moves(rec: Dict[str, Any], grid: Optional[List[List[int]]], mode: str) -> List[Dict[str, int]]:
    """
    Erstellt eine deterministische Move-Liste aus der Lösung.
    mode:
      • fill_empty: füllt nur leere Nicht-Fix-Zellen (überschreibt nichts)
      • correct: korrigiert auch falsche Werte in Nicht-Fix-Zellen
    """
    puzzle = rec["puzzle"]
    sol = rec["solution"]
    out: List[Dict[str, int]] = []

    for r in range(9):
        for c in range(9):
            # Fixe Felder niemals anfassen
            if int(puzzle[r][c]) != 0:
                continue
            target = int(sol[r][c])

            if grid is None:
                out.append({"r": r, "c": c, "value": target})
                continue

            cur = int(grid[r][c] or 0)
            if mode == "fill_empty":
                if cur == 0:
                    out.append({"r": r, "c": c, "value": target})
            else:  # "correct"
                if cur != target:
                    out.append({"r": r, "c": c, "value": target})

    return out


@bp.get("/")
def page():
    return render_template("sudoku.html")


@bp.get("/api/new")
def api_new():
    _prune()
    difficulty = (request.args.get("difficulty") or "medium").strip().lower()
    seed_arg = request.args.get("seed")
    seed = int(seed_arg) if seed_arg and str(seed_arg).strip().isdigit() else None

    rec = sudoku_game.generate_puzzle(seed=seed, difficulty=difficulty, ensure_unique=True)
    pid = _new_id()

    ep_id = _log_episode_start(int(rec["seed"]), rec["difficulty"], int(rec.get("clues", 0)), bool(rec.get("unique", True)))

    _PUZZLES[pid] = {
        "ts": int(time.time()),
        "seed": int(rec["seed"]),
        "difficulty": rec["difficulty"],
        "puzzle": rec["puzzle"],
        "solution": rec["solution"],
        "clues": int(rec.get("clues", 0)),
        "unique": bool(rec.get("unique", True)),
        "episode_id": ep_id,
        "solved_logged": False,
        "oroma": {
            "playing": False,
            "cursor": 0,
            "total": 0,
            "moves": [],
            "mode": "fill_empty",
            "started_ts": 0,
            "stopped_ts": None,
        }
    }

    # Lösung niemals im new zurückgeben
    return jsonify({
        "ok": True,
        "puzzle_id": pid,
        "seed": _PUZZLES[pid]["seed"],
        "difficulty": _PUZZLES[pid]["difficulty"],
        "clues": _PUZZLES[pid]["clues"],
        "unique": _PUZZLES[pid]["unique"],
        "puzzle": _PUZZLES[pid]["puzzle"],
    })


@bp.post("/api/check")
def api_check():
    _prune()
    data = request.get_json(force=True) or {}
    pid = (data.get("puzzle_id") or "").strip()
    grid = data.get("grid")

    rec = _get_rec(pid)
    if not rec:
        return jsonify({"ok": False, "error": "unknown puzzle_id"}), 404

    ok, err = sudoku_game.validate_grid(grid)
    if not ok:
        _log_event(rec.get("episode_id"), "sudoku_check", {"valid": False, "error": err})
        return jsonify({"ok": True, "valid": False, "solved": False, "error": err})

    solved = sudoku_game.is_solved(rec["puzzle"], grid)
    _log_event(rec.get("episode_id"), "sudoku_check", {"valid": True, "solved": bool(solved)})

    if solved and sql_manager and rec.get("episode_id") and not rec.get("solved_logged"):
        rec["solved_logged"] = True
        _log_event(rec.get("episode_id"), "sudoku_solved", {"seed": rec["seed"], "difficulty": rec["difficulty"], "clues": rec["clues"]})
        try:
            sql_manager.update_episode_end(int(rec["episode_id"]), int(time.time()))
        except Exception as e:
            log_suppressed('ui/sudoku_ui.py:264', exc=e, level=logging.WARNING)
            pass

        # Optional: SnapChain Summary (klein halten)
        try:
            blob = json.dumps({
                "type": "sudoku_summary",
                "seed": rec["seed"],
                "difficulty": rec["difficulty"],
                "clues": rec["clues"],
                "unique": rec["unique"],
                "solved": True,
                "ts": int(time.time()),
            }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            sql_manager.insert_snapchain({
                "ts": int(time.time()),
                "quality": 0.65,
                "blob": blob,
                "origin": "game:sudoku",
                "notes": "Sudoku solved summary",
                "version": "v3.7.3",
                "weight": 0.35,
            })
        except Exception as e:
            log_suppressed('ui/sudoku_ui.py:288', exc=e, level=logging.WARNING)
            pass

    return jsonify({"ok": True, "valid": True, "solved": bool(solved)})


@bp.post("/api/hint")
def api_hint():
    _prune()
    data = request.get_json(force=True) or {}
    pid = (data.get("puzzle_id") or "").strip()
    r = int(data.get("r", -1))
    c = int(data.get("c", -1))

    rec = _get_rec(pid)
    if not rec:
        return jsonify({"ok": False, "error": "unknown puzzle_id"}), 404
    if not (0 <= r < 9 and 0 <= c < 9):
        return jsonify({"ok": False, "error": "r/c out of range"}), 400
    if int(rec["puzzle"][r][c]) != 0:
        return jsonify({"ok": False, "error": "cell is fixed in puzzle"}), 400

    v = int(rec["solution"][r][c])
    _log_event(rec.get("episode_id"), "sudoku_hint", {"r": r, "c": c, "value": v})
    return jsonify({"ok": True, "r": r, "c": c, "value": v})


# -----------------------------------------------------------------------------
# ORÓMA Play API
# -----------------------------------------------------------------------------

@bp.post("/api/oroma/start")
def api_oroma_start():
    """
    Startet ORÓMA-Autoplay für ein Puzzle (Moves werden aus der Lösung erzeugt).
    UI ruft danach periodisch /api/oroma/next auf.
    """
    _prune()
    data = request.get_json(force=True) or {}
    pid = (data.get("puzzle_id") or "").strip()
    mode = (data.get("mode") or "fill_empty").strip().lower()
    if mode not in ("fill_empty", "correct"):
        mode = "fill_empty"

    grid = data.get("grid")
    rec = _get_rec(pid)
    if not rec:
        return jsonify({"ok": False, "error": "unknown puzzle_id"}), 404

    o = _ensure_oroma_state(rec)

    moves = _build_moves(rec, grid=grid, mode=mode)
    o["moves"] = moves
    o["total"] = int(len(moves))
    o["cursor"] = 0
    o["mode"] = mode
    o["playing"] = True
    o["started_ts"] = int(time.time())
    o["stopped_ts"] = None

    _log_event(rec.get("episode_id"), "sudoku_oroma_start", {"mode": mode, "total": o["total"]})

    return jsonify({
        "ok": True,
        "playing": True,
        "mode": mode,
        "cursor": o["cursor"],
        "total": o["total"],
        "done": (o["total"] == 0),
    })


@bp.post("/api/oroma/next")
def api_oroma_next():
    """
    Liefert den nächsten Move. Wenn stopped oder done: playing=false zurück.
    UI kann grid mitsenden, damit fill_empty nichts überschreibt.
    """
    _prune()
    data = request.get_json(force=True) or {}
    pid = (data.get("puzzle_id") or "").strip()
    grid = data.get("grid")

    rec = _get_rec(pid)
    if not rec:
        return jsonify({"ok": False, "error": "unknown puzzle_id"}), 404

    o = _ensure_oroma_state(rec)
    if not o.get("playing"):
        return jsonify({"ok": True, "playing": False, "done": bool(o.get("cursor", 0) >= o.get("total", 0))})

    moves: List[Dict[str, int]] = o.get("moves") or []
    total = int(o.get("total") or 0)
    cursor = int(o.get("cursor") or 0)

    # Keine Moves mehr → done
    if cursor >= total:
        o["playing"] = False
        _log_event(rec.get("episode_id"), "sudoku_oroma_done", {"total": total})
        return jsonify({"ok": True, "playing": False, "done": True})

    # Bei fill_empty: wenn UI-grid das Feld schon gefüllt hat, skippen wir weiter.
    mode = str(o.get("mode") or "fill_empty")
    while cursor < total:
        mv = moves[cursor]
        r = int(mv["r"])
        c = int(mv["c"])
        v = int(mv["value"])
        cursor += 1

        if mode == "fill_empty" and isinstance(grid, list):
            try:
                if int(grid[r][c] or 0) != 0:
                    # bereits gefüllt → skip
                    continue
            except Exception as e:
                log_suppressed('ui/sudoku_ui.py:404', exc=e, level=logging.WARNING)
                pass

        o["cursor"] = cursor

        # Logging (bewusst klein, aber nachvollziehbar)
        _log_event(rec.get("episode_id"), "sudoku_oroma_move", {"idx": cursor, "total": total, "r": r, "c": c, "value": v})

        done = bool(cursor >= total)
        if done:
            o["playing"] = False
            _log_event(rec.get("episode_id"), "sudoku_oroma_done", {"total": total})

        return jsonify({
            "ok": True,
            "playing": bool(o.get("playing")),
            "done": done,
            "cursor": cursor,
            "total": total,
            "move": {"r": r, "c": c, "value": v},
        })

    # Falls alles geskippt → done
    o["cursor"] = cursor
    o["playing"] = False
    _log_event(rec.get("episode_id"), "sudoku_oroma_done", {"total": total, "reason": "skipped_all"})
    return jsonify({"ok": True, "playing": False, "done": True})


@bp.post("/api/oroma/stop")
def api_oroma_stop():
    _prune()
    data = request.get_json(force=True) or {}
    pid = (data.get("puzzle_id") or "").strip()

    rec = _get_rec(pid)
    if not rec:
        return jsonify({"ok": False, "error": "unknown puzzle_id"}), 404

    o = _ensure_oroma_state(rec)
    o["playing"] = False
    o["stopped_ts"] = int(time.time())

    _log_event(rec.get("episode_id"), "sudoku_oroma_stop", {"cursor": int(o.get("cursor") or 0), "total": int(o.get("total") or 0)})
    return jsonify({"ok": True, "playing": False})


@bp.get("/api/oroma/status")
def api_oroma_status():
    _prune()
    pid = (request.args.get("puzzle_id") or "").strip()
    rec = _get_rec(pid)
    if not rec:
        return jsonify({"ok": False, "error": "unknown puzzle_id"}), 404

    o = _ensure_oroma_state(rec)
    cursor = int(o.get("cursor") or 0)
    total = int(o.get("total") or 0)
    return jsonify({
        "ok": True,
        "playing": bool(o.get("playing")),
        "mode": str(o.get("mode") or "fill_empty"),
        "cursor": cursor,
        "total": total,
        "done": bool(cursor >= total),
        "started_ts": int(o.get("started_ts") or 0),
        "stopped_ts": o.get("stopped_ts"),
    })


# games_ui.py Kompatibilität
sudoku_bp = bp
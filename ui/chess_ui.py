#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/chess_ui.py
# Projekt:   ORÓMA – Headless UI (Flask)
# Modul:     Chess Arena (UI + Daily-Status + ehrliche Einordnung) – PURE + DB-Fallback
# Version:   v3.8-r7b (Daily-Status + ehrliche Chess-Einordnung)
# Stand:     2026-03-11
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
# Headless-Schach-Arena **ohne Fremdpaket**:
#   • Legale Züge & FEN via mini_programs.chess (eigene Engine)
#   • Policy-Integration (core/policy_engine) über integrierten ChessAdapterPure
#   • SnapChain-Append pro Zug (66-D Vektor; v[65] Outcome bei Terminal)
#   • DB-Insert in snapchains (origin="game:chess")
#   • UI-kompatibel zu chess.html:
#       /api/state, /api/move, /api/reset, /api/toggle, /api/mode, /api/speed, /api/oromaSide
#       /api/snaps, /api/counters, /api/flush, /api/dbdiag
#
# WICHTIG (DB)
# ────────────
#   • Robuster DB-Zugriff:
#       1) Primär: core.sql_manager.get_conn()  → liefert in ORÓMA dict-Rows
#       2) Fallback: sqlite3.connect(OROMA_DB_PATH) → liefert Tupel oder sqlite3.Row
#   • CREATE IF NOT EXISTS sicher gestellt.
#
# NEU (r7/r7a)
# ────────────
#   • Row-Factory-sichere Reads via _row_val() & AS-Aliase (cnt/n).
#   • /api/dbdiag: konsistente Keys + using_fallback.
#   • RF-Gaps (db_fallback, db_error, illegal_move, runtime_loop_error, db_diag_error).
#   • Gap-Bridge: nutzt rf.note_gap(), sonst core.gaps.add_gap() (falls vorhanden).
# NEU (r7b)
# ─────────
#   • /api/daily_status liefert den letzten Chess-Daily-/Nightly-Stand aus
#     episodes + episodic_metrics (policy/explore separat).
#   • UI zeigt damit sichtbar, ob der nächtliche Orchestrator-Lauf wirklich
#     geschrieben wurde und mit welchen Kennzahlen er endete.
#   • Ehrliche Einordnung in der UI: Chess ist produktiv vor allem als
#     Trace-/SnapChain-Lieferant; tabellarisches Policy-Lernen bleibt
#     experimentell und ist kein Leitbenchmark des Systems.
#
# UMGEBUNG
# ────────
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_CHESS_USE_POLICY=1
#   OROMA_CHESS_EPS=0.08
#   OROMA_CHESS_SPEED=normal|turbo
#   # Roter Faden:
#   #   OROMA_THREAD_AUTO_GAPS=1|0
#   #   OROMA_THREAD_GAP_MIN_GAP_SEC=300
#   #   OROMA_THREAD_NUDGE_MIN_GAP_SEC=600
# =============================================================================

from __future__ import annotations

import os
import json
import time
import random
import logging
import threading
import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Tuple

from flask import Blueprint, jsonify, request, current_app, render_template
from core.log_guard import log_suppressed

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.chess")
if not LOG.handlers or os.environ.get("OROMA_CHESS_LOG") == "1":
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [Chess] %(message)s"))
    LOG.addHandler(sh)
lvl = os.environ.get("OROMA_CHESS_LOGLEVEL", "INFO").upper()
LOG.setLevel(getattr(logging, lvl, logging.INFO))
LOG.debug("=== Chess UI init (PURE + r7a) ===")

# -----------------------------------------------------------------------------
# Core / Engine (eigene Module, keine Fremdpakete)
# -----------------------------------------------------------------------------
try:
    from mini_programs.chess.chess_game import ChessGame
    from mini_programs.chess.chess_rules import FILES  # "abcdefgh"
except Exception as e:
    raise RuntimeError(f"[chess_ui] Chess-Core-Importfehler: {e}")

try:
    from core import sql_manager, reward
    from core.policy_engine import PolicyEngine
    from core.snappattern import SnapPattern
    from core.snapchain import SnapChain
except Exception as e:
    raise RuntimeError(f"[chess_ui] Core-Importfehler: {e}")

# -----------------------------------------------------------------------------
# OPTIONAL: Roter Faden (Knowledge-Gaps) + Fallback zu core.gaps
# -----------------------------------------------------------------------------
try:
    from core import roter_faden as rf
    _RF_OK = True
except Exception:
    rf = None  # type: ignore
    _RF_OK = False

try:
    from core import gaps as _gaps_core
except Exception:
    _gaps_core = None  # type: ignore

def _safe_err(e: Exception, n: int = 180) -> str:
    try:
        return (str(e) or "").strip()[:n]
    except Exception:
        return "error"

def _gap(kind: str, desc: str, confidence: float = 0.0, meta: Optional[Dict[str, Any]] = None) -> None:
    """Fail-safe Gap-Helper. Bevorzugt rf.note_gap(); sonst core.gaps.add_gap()."""
    m = {"origin": "game:chess"}
    if isinstance(meta, dict):
        m.update(meta)
    try:
        if _RF_OK and rf and hasattr(rf, "note_gap"):
            rf.note_gap(kind, desc, float(confidence or 0.0), m)  # Bridge im Roter Faden
            return
    except Exception as e:
        log_suppressed('ui/chess_ui.py:122', exc=e, level=logging.WARNING)
        pass
    try:
        if _gaps_core and hasattr(_gaps_core, "add_gap"):
            _gaps_core.add_gap(kind, desc, float(confidence or 0.0), m)  # direkter Core-Fallback
    except Exception as e:
        log_suppressed('ui/chess_ui.py:128', exc=e, level=logging.WARNING)
        pass

# -----------------------------------------------------------------------------
# DB-Fallback / Connection
# -----------------------------------------------------------------------------
DB_ENV_PATH = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
OROMA_BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
ORCH_STATE_PATH = os.path.join(OROMA_BASE, "state", "orchestrator_state.json")
_DB_FALLBACK_ONCE = False   # einmaliges Gap "db_fallback"
_USING_FALLBACK = False     # Flag für Diag-Ausgabe

def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snapchains (
          id       INTEGER PRIMARY KEY AUTOINCREMENT,
          blob     BLOB    NOT NULL,
          origin   TEXT    DEFAULT NULL,
          status   TEXT    DEFAULT 'active',
          weight   REAL    DEFAULT 1.0,
          quality  REAL    DEFAULT 0.0,
          version  TEXT    DEFAULT NULL,
          ts       INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapchains_origin_ts ON snapchains(origin, ts)")
    conn.commit()

def _sqlm_db_hint() -> Optional[str]:
    for attr in ("DB_PATH", "db_path", "DB", "path"):
        p = getattr(sql_manager, attr, None)
        if isinstance(p, str) and p:
            return p
    return None

@contextmanager
def _db_conn():
    """
    Connection mit garantiertem Schema.
    1) sql_manager.get_conn() → gleiche DB wie Rest (dict-Rows)

    Hinweis (Stufe C / Single Writer):
    - Direkte sqlite3.connect()-Writer umgehen den globalen DBWriter.
    - Diese UI läuft im ORÓMA Runtime-Kontext, daher wird kein sqlite3-Fallback
      verwendet. Falls sql_manager nicht verfügbar ist, wird ein sichtbarer Gap
      gemeldet und die Exception weitergereicht.
    """
    global _DB_FALLBACK_ONCE, _USING_FALLBACK
    try:
        if sql_manager and hasattr(sql_manager, "get_conn"):
            with sql_manager.get_conn() as conn:
                try:
                    _ensure_schema(conn)
                except Exception as e:
                    LOG.debug("[db] ensure_schema(sql_manager) fail: %s", e)
                _USING_FALLBACK = False
                yield conn
                return
    except Exception as e:
        LOG.error("[db] sql_manager.get_conn() fehlgeschlagen: %s", e)
        _gap("db_error", "sql_manager.get_conn() fehlgeschlagen", 0.1, {"err": _safe_err(e)})
        raise

def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info('{table}')")
        info = cur.fetchall() or []
        for r in info:
            try:
                name = r["name"] if hasattr(r, "keys") else r[1]
                if name == col:
                    return True
            except Exception:
                continue
    except Exception as e:
        log_suppressed('ui/chess_ui.py:218', exc=e, level=logging.WARNING)
        pass
    return False

# -----------------------------------------------------------------------------
# Row-Factory-safe Utilities
# -----------------------------------------------------------------------------
def _row_val(row: Any, prefer_key: Optional[str] = None, fallback_idx: int = 0) -> Any:
    """Extrahiert robust einen Wert aus Dict/sqlite3.Row/Tuple."""
    if row is None:
        return None
    if isinstance(row, dict) or hasattr(row, "keys"):
        if prefer_key and (prefer_key in row):
            return row[prefer_key]
        try:
            return next(iter(row.values()))
        except Exception:
            return None
    try:
        return row[fallback_idx]
    except Exception:
        return None

# -----------------------------------------------------------------------------
# Defaults aus ENV
# -----------------------------------------------------------------------------
USE_POLICY_DEFAULT = os.environ.get("OROMA_CHESS_USE_POLICY", "1").strip().lower() in ("1", "true", "on", "yes")
EPS_DEFAULT = float(os.environ.get("OROMA_CHESS_EPS", "0.08") or "0.08")
EPS_DEFAULT = max(0.0, min(1.0, EPS_DEFAULT))
DEFAULT_SPEED = os.environ.get("OROMA_CHESS_SPEED", "normal").lower()
DEF_TICK = 0.05 if DEFAULT_SPEED == "normal" else 0.0
DEF_END  = 0.20 if DEFAULT_SPEED == "normal" else 0.0

chess_bp = Blueprint("chess_ui", __name__, url_prefix="/chess")

# -----------------------------------------------------------------------------
# 66-D Vektorisierung
# -----------------------------------------------------------------------------
_PVAL = {"P": 1.0, "N": 2.0, "B": 3.0, "R": 4.0, "Q": 5.0, "K": 6.0}
def _vec_from_game(g: ChessGame) -> List[float]:
    v = [0.0] * 66
    try:
        for (rc, p) in g.pos.board:
            r, c = rc
            if p == ".":
                continue
            sgn = 1.0 if p.isupper() else -1.0
            v[r * 8 + c] = sgn * _PVAL.get(p.upper(), 0.0)
    except Exception as e:
        log_suppressed('ui/chess_ui.py:267', exc=e, level=logging.WARNING)
        pass
    v[64] = 1.0 if g.turn == "w" else -1.0
    v[65] = 0.0
    return v

def _index_to_sq(idx: int) -> str:
    r = idx // 8
    c = idx % 8
    file = FILES[c]
    rank = 8 - r
    return f"{file}{rank}"

def _terminal_outcome_from_winner(w: Optional[str]) -> float:
    if w == "white": return +1.0
    if w == "black": return -1.0
    if w == "draw":  return 0.0
    return 0.0

def _pattern_from_game(g: ChessGame, md: Dict[str, Any]) -> Any:
    v = _vec_from_game(g)
    return SnapPattern.from_snaps([v], metadata=dict(md or {}))

# -----------------------------------------------------------------------------
# ChessAdapterPure – Adapter für PolicyEngine
# -----------------------------------------------------------------------------
class ChessAdapterPure:
    namespace: str = "game:chess"

    def extract_vectors(self, chain_or_dict: Any) -> List[List[float]]:
        d: Dict[str, Any]
        if isinstance(chain_or_dict, dict):
            d = chain_or_dict
        else:
            try:
                if hasattr(chain_or_dict, "to_dict"):
                    d = chain_or_dict.to_dict()
                else:
                    d = json.loads(chain_or_dict) if isinstance(chain_or_dict, (bytes, bytearray, str)) else {}
            except Exception:
                d = {}
        out: List[List[float]] = []
        pats = d.get("patterns") or []
        for p in pats:
            vec: Optional[List[float]] = None
            try:
                arrs = p.get("patterns") or []
                if isinstance(arrs, list) and arrs and isinstance(arrs[0], list) and len(arrs[0]) >= 64:
                    vec = [float(x) for x in arrs[0]]
                elif isinstance(p.get("centroid"), list) and len(p["centroid"]) >= 64:
                    vec = [float(x) for x in p["centroid"]]
            except Exception:
                vec = None
            if vec is None:
                continue
            if len(vec) < 66:
                vec = (vec + [0.0] * (66 - len(vec)))[:66]
            else:
                vec = vec[:66]
            out.append(vec)

        if out:
            try:
                last_md = (pats[-1].get("metadata") or {})
                if "outcome" in last_md:
                    val = float(last_md.get("outcome") or 0.0)
                    out[-1][65] = 1.0 if val > 0 else (-1.0 if val < 0 else 0.0)
            except Exception as e:
                log_suppressed('ui/chess_ui.py:335', exc=e, level=logging.WARNING)
                pass
        return out

    def final_outcome(self, final_vec: List[float]) -> int:
        try:
            o = float(final_vec[65])
            return 1 if o > 0 else (-1 if o < 0 else 0)
        except Exception:
            return 0

    def action_from_delta(self, prev: List[float], nxt: List[float]) -> Optional[str]:
        p = [float(x) for x in prev[:64]]
        n = [float(x) for x in nxt[:64]]
        mover_white = (prev[64] >= 0.0)

        def _castle_uci(is_white: bool) -> Optional[str]:
            frm = 60 if is_white else 4
            to_g = 62 if is_white else 6
            to_c = 58 if is_white else 2
            if abs(p[frm]) == 6.0 and n[frm] == 0.0:
                if abs(n[to_g]) == 6.0: return f"{_index_to_sq(frm)}{_index_to_sq(to_g)}"
                if abs(n[to_c]) == 6.0: return f"{_index_to_sq(frm)}{_index_to_sq(to_c)}"
            return None

        cu = _castle_uci(mover_white)
        if cu: return cu

        diffs = [i for i in range(64) if p[i] != n[i]]
        if not diffs: return None

        moved_val = None
        for i in diffs:
            if n[i] != 0.0:
                moved_val = n[i]; break
        if moved_val is None: return None

        cand_from = [i for i in diffs if p[i] != 0.0 and (p[i] > 0) == (moved_val > 0) and n[i] == 0.0]
        cand_to   = [i for i in diffs if n[i] != 0.0 and (n[i] > 0) == (moved_val > 0)]
        if not cand_from or not cand_to: return None
        frm = cand_from[0]; to = cand_to[0]

        prom = ""
        if abs(moved_val) >= 2.0 and abs(p[frm]) == 1.0:
            rank_to = to // 8
            if (moved_val > 0 and rank_to == 0) or (moved_val < 0 and rank_to == 7):
                prom = "q"

        return f"{_index_to_sq(frm)}{_index_to_sq(to)}{prom}"

    def canonicalize(self, vec: List[float]) -> Tuple[str, List[int], List[int]]:
        sym = []
        for i in range(64):
            val = float(vec[i])
            if val == 0.0:
                sym.append("_")
            else:
                piece = "PNBRQK" if val > 0 else "pnbrqk"
                idx = int(abs(val))
                sym.append(piece[idx - 1] if 1 <= idx <= 6 else "?")
        sym.append("w" if (vec[64] >= 0.0) else "b")
        h = "".join(sym)
        perm = list(range(64))
        return h, perm, perm

    def map_action_through_perm(self, action: str, perm_or_invperm: List[int]) -> str:
        return action

    def fallback_action(self, state_vec: List[float]) -> Optional[str]:
        return "e2e4" if (state_vec[64] >= 0.0) else "e7e5"

# -----------------------------------------------------------------------------
# Runtime
# -----------------------------------------------------------------------------
class ChessRuntime:
    MODES = ("oroma_vs_human", "oroma_vs_oroma_policy", "oroma_vs_oroma_explore", "ki_vs_ki", "oroma_vs_ki", "oroma_vs_oroma", "oroma_solo")

    def __init__(self):
        self.game = ChessGame()
        self.mode = "oroma_vs_human"
        self.auto = False

        self.lock = threading.Lock()
        self._lock_held_since = 0.0

        # UI-Kompatibilität
        self.speed_mode = DEFAULT_SPEED
        self.tick_delay = DEF_TICK
        self.end_delay  = DEF_END
        self.oroma_side = "white"     # nur für Modus oroma_vs_ki
        self.last_move: Optional[str] = None
        self.winner: Optional[str] = None  # "white"|"black"|"draw"|None

        # SnapChain RAM
        self.chain = SnapChain(patterns=[], metadata={"game": "chess"})

        # Policy
        self.adapter = ChessAdapterPure()
        self.policy = PolicyEngine(self.adapter)
        self.policy.namespace = "game:chess"
        self.policy_enabled = USE_POLICY_DEFAULT

        # Exploration-Rate
        self.eps: float = EPS_DEFAULT

        self.stats = {"games": 0, "wins": {"white": 0, "black": 0, "draw": 0}, "snaps_total": 0}

        threading.Thread(target=self._loop, daemon=True, name="Chess-Loop").start()

    # --------------------------- Spielsteuerung ------------------------------
    def reset(self):
        self.game = ChessGame()
        # neue Chain je Partie
        self.chain = SnapChain(patterns=[], metadata={"game": "chess"})
        self.stats["games"] += 1
        self.last_move = None
        self.winner = None
        LOG.info("♟️ Neues Spiel #%d (Weiß am Zug)", self.stats["games"])

    def _append_snapshot(self, move_uci: Optional[str] = None, side: Optional[str] = None, outcome: float = 0.0):
        md = {"uci": move_uci, "side": side or ("W" if self.game.turn == "w" else "B")}
        if abs(outcome) > 0.0 or (outcome == 0.0 and (self.winner is not None)):
            md["outcome"] = outcome
        try:
            pat = _pattern_from_game(self.game, md)
            self.chain.append(pat)
            self.stats["snaps_total"] += 1
        except Exception as e:
            LOG.error("[snapshot] Append fehlgeschlagen: %s", e)

    def _legal_uci(self) -> List[str]:
        try:
            return self.game.legal_uci()
        except Exception:
            return []

    def _policy_pick(self) -> Optional[str]:
        if not self.policy_enabled:
            return None
        svec = _vec_from_game(self.game)
        try:
            a = self.policy.choose_action(svec)
        except Exception as e:
            LOG.debug("[policy_pick] %s", e)
            a = None
        return a

    def _pick_move(self, who: str) -> Optional[str]:
        legal = self._legal_uci()
        if not legal:
            return None
        use_explore = random.random() < max(0.0, min(1.0, float(self.eps)))
        if who == "oroma" and self.policy_enabled and not use_explore:
            a = self._policy_pick()
            if a in legal:
                return a
        prefs = [u for u in legal if u[:2] in ("e2", "d2", "e7", "d7")]
        if prefs:
            return random.choice(prefs)
        return random.choice(legal)

    def _apply_move(self, uci: str, actor: str):
        try:
            ok = self.game.play_uci(uci)
            if not ok:
                raise ValueError("illegaler Zug")
            self.last_move = uci
            self._append_snapshot(move_uci=uci, side=("W" if self.game.turn == "w" else "B"))

            w = self.game.winner()
            if w:
                self.winner = w  # "white"/"black"/"draw"
                outcome = _terminal_outcome_from_winner(w)
                self._append_snapshot(move_uci=None, side=("W" if self.game.turn == "w" else "B"), outcome=outcome)
                self._finish_game(actor, outcome)
        except Exception as e:
            LOG.error("[apply_move] %s (%s)", e, uci)
            _gap("illegal_move", "Illegaler Zug im ChessRuntime", 0.5, {"uci": uci, "actor": actor, "err": _safe_err(e)})

    def _finish_game(self, last_actor: str, outcome: float):
        if outcome > 0:
            self.stats["wins"]["white"] += 1
        elif outcome < 0:
            self.stats["wins"]["black"] += 1
        else:
            self.stats["wins"]["draw"] += 1

        if reward and hasattr(reward, "log"):
            try:
                r = 1.0 if outcome != 0.0 else 0.2
                reward.log("chess", r)
            except Exception as e:
                log_suppressed('ui/chess_ui.py:527', exc=e, level=logging.WARNING)
                pass

        export = self._export_chain_safe()
        try:
            blob_b = json.dumps(export, ensure_ascii=False).encode("utf-8")
            # Stufe C: SnapChain Insert ueber sql_manager (routet via DBWriter wenn aktiv)
            sql_manager.insert_snapchain({
                'ts': int(time.time()),
                'quality': 0.0,
                'blob': blob_b,
                'exported': 0,
                'status': 'active',
                'origin': 'game:chess',
                'version': 'v3.8',
                'weight': 1.0,
            })
            LOG.info("💾 Chess SnapChain gespeichert (%d Patterns)", len(export.get("patterns", []) or []))
        except Exception as e:
            LOG.error("[finish_game] DB-Insert-Fehler: %s", e)
            _gap("db_error", "Chess SnapChain-Insert fehlgeschlagen", 0.1,
                 {"where": "finish_game", "err": _safe_err(e)})

        time.sleep(self.end_delay)
        self.reset()

    def _export_chain_safe(self) -> Dict[str, Any]:
        ch = self.chain
        try:
            if hasattr(ch, "to_dict"):
                d = ch.to_dict()
                if isinstance(d, dict) and d.get("patterns"):
                    return d
        except Exception as e:
            log_suppressed('ui/chess_ui.py:558', exc=e, level=logging.WARNING)
            pass
        pats = []
        for p in getattr(ch, "patterns", []) or []:
            if hasattr(p, "to_dict"):
                try:
                    d = p.to_dict()
                    if isinstance(d, dict):
                        pats.append(d); continue
                except Exception as e:
                    log_suppressed('ui/chess_ui.py:568', exc=e, level=logging.WARNING)
                    pass
            pd = {
                "created_at": getattr(p, "created_at", int(time.time())),
                "centroid":   getattr(p, "centroid", []) or [],
                "metadata":   getattr(p, "metadata", {}) or {},
                "patterns":   getattr(p, "patterns", []) or [],
            }
            pats.append(pd)
        return {"schema_version": "ui-export-1", "patterns": pats, "metadata": {"game": "chess"}}

    # ------------------------------ Auto-Loop --------------------------------
    def set_speed(self, mode: str):
        m = (mode or "").lower()
        if m == "turbo":
            self.speed_mode = "turbo"; self.tick_delay = 0.0; self.end_delay  = 0.0
        else:
            self.speed_mode = "normal"; self.tick_delay = 0.05; self.end_delay  = 0.20
        LOG.info("⚙️ Geschwindigkeit: %s", self.speed_mode)

    def _loop(self):
        while True:
            time.sleep(self.tick_delay or 0.05)
            if not self.auto or (self.game.winner() is not None):
                continue
            if not self.lock.acquire(timeout=0.01):
                continue
            self._lock_held_since = time.time()
            try:
                if self.mode in ("oroma_vs_oroma_policy", "oroma_vs_oroma_explore", "oroma_vs_oroma", "oroma_solo"):
                    actor = "oroma"
                elif self.mode == "ki_vs_ki":
                    actor = "ki"
                elif self.mode == "oroma_vs_ki":
                    is_white_turn = (self.game.turn == "w")
                    actor = "oroma" if (self.oroma_side == "white" and is_white_turn) or (self.oroma_side == "black" and not is_white_turn) else "ki"
                else:
                    actor = "oroma" if self.game.turn == "w" else "ki"

                uci = self._pick_move(actor)
                if uci:
                    self._apply_move(uci, actor)
            except Exception as e:
                LOG.error("[loop] %s", e)
                _gap("runtime_loop_error", "Fehler in Chess-Auto-Loop", 0.2, {"err": _safe_err(e)})
            finally:
                self._lock_held_since = 0.0
                self.lock.release()

    # ------------------------------ API-Helfer --------------------------------
    def state(self) -> Dict[str, Any]:
        w = self.game.winner()
        res = "1-0" if w == "white" else ("0-1" if w == "black" else "1/2-1/2") if w else None
        return {
            "fen": self.game.fen(),
            "turn": "w" if self.game.turn == "w" else "b",
            "legal": self._legal_uci()[:200],
            "is_over": bool(w),
            "result": res,
            "winner": w or None,
            "last_move": self.last_move,
            "mode": self.mode,
            "speed": self.speed_mode,
            "auto": self.auto,
            "oroma_side": self.oroma_side,
            "policy": {"enabled": self.policy_enabled, "eps": self.eps, "namespace": self.policy.namespace},
            "patterns_in_ram": len(getattr(self.chain, "patterns", []) or []),
            "stats": self.stats,
        }

    def move_human(self, uci: str):
        if self.game.winner() is not None:
            return
        if uci not in self._legal_uci():
            LOG.info("⛔ Illegaler User-Zug: %s", uci)
            _gap("illegal_move", "Illegaler User-Zug (verworfen)", 0.4, {"uci": uci})
            return
        self._apply_move(uci, "human")

# -----------------------------------------------------------------------------
# DB-Helpers für /snaps & /counters – immer über _db_conn()
# -----------------------------------------------------------------------------
def _safe_decode_chain(blob: Any) -> Dict[str, Any]:
    try:
        if isinstance(blob, (bytes, bytearray, memoryview)):
            return json.loads(bytes(blob).decode("utf-8"))
        if isinstance(blob, str):
            return json.loads(blob)
    except Exception as e:
        log_suppressed('ui/chess_ui.py:659', exc=e, level=logging.WARNING)
        pass
    return {}

def _chain_summary(d: Dict[str, Any]) -> Tuple[str, int]:
    pats = d.get("patterns") or []
    moves = max(0, len(pats) - 1) if isinstance(pats, list) else 0
    result = "–"
    try:
        if pats:
            md = pats[-1].get("metadata") or {}
            if "outcome" in md:
                o = float(md.get("outcome") or 0.0)
                result = "1-0" if o > 0 else ("0-1" if o < 0 else "1/2-1/2")
    except Exception as e:
        log_suppressed('ui/chess_ui.py:674', exc=e, level=logging.WARNING)
        pass
    return result, moves

def _db_list_chess(limit: int = 25) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            has_ts = _has_col(conn, "snapchains", "ts")
            if has_ts:
                cur.execute(
                    "SELECT id, blob, ts FROM snapchains WHERE origin=? ORDER BY id DESC LIMIT ?",
                    ("game:chess", int(limit))
                )
            else:
                cur.execute(
                    "SELECT id, blob, NULL as ts FROM snapchains WHERE origin=? ORDER BY id DESC LIMIT ?",
                    ("game:chess", int(limit))
                )
            rows = cur.fetchall() or []
            for r in rows:
                if isinstance(r, dict) or hasattr(r, "keys"):
                    idv = int(r.get("id"))
                    b   = r.get("blob")
                    ts  = r.get("ts")
                else:
                    idv, b, ts = r
                d = _safe_decode_chain(b)
                res, nm = _chain_summary(d)
                items.append({"id": int(idv), "result": res, "n_moves": int(nm), "ts": int(ts) if ts else None})
    except Exception as e:
        LOG.debug("[_db_list_chess] %s", e)
    return items

def _db_counters() -> Dict[str, int]:
    out = {"snapchains_chess": 0, "snapchains_total": 0}
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM snapchains")
            out["snapchains_total"] = int(_row_val(cur.fetchone(), "cnt") or 0)
            cur.execute("SELECT COUNT(*) AS cnt FROM snapchains WHERE origin=?", ("game:chess",))
            out["snapchains_chess"] = int(_row_val(cur.fetchone(), "cnt") or 0)
    except Exception as e:
        LOG.debug("[_db_counters] %s", e)
    return out


def _orch_job_status(job: str) -> Optional[Dict[str, Any]]:
    """Liest den letzten Orchestrator-Status für einen Job aus state/orchestrator_state.json."""
    try:
        if not os.path.exists(ORCH_STATE_PATH):
            return None
        with open(ORCH_STATE_PATH, "r", encoding="utf-8") as fh:
            st = json.load(fh) or {}
        if not isinstance(st, dict):
            return None
        last_ts = int((st.get("last_ts") or {}).get(job, 0) or 0)
        last_rc = int((st.get("last_rc") or {}).get(job, 0) or 0)
        last_dur = float((st.get("last_dur_s") or {}).get(job, 0.0) or 0.0)
        last_to = bool((st.get("last_timeout") or {}).get(job, False))
        last_day = str((st.get("last_day") or {}).get(job, "") or "")
        fail_ts = int((st.get("daily_fail_ts") or {}).get(job, 0) or 0)
        fail_rc = int((st.get("daily_fail_rc") or {}).get(job, 0) or 0)
        fail_dur = float((st.get("daily_fail_dur_s") or {}).get(job, 0.0) or 0.0)
        fail_count = int((st.get("daily_fail_count") or {}).get(job, 0) or 0)
        return {
            "job": str(job),
            "ts": last_ts,
            "rc": last_rc,
            "dur_s": last_dur,
            "timed_out": last_to,
            "last_day": last_day,
            "fail_ts": fail_ts,
            "fail_rc": fail_rc,
            "fail_dur_s": fail_dur,
            "fail_count": fail_count,
        }
    except Exception as e:
        LOG.debug("[_orch_job_status] %s", e)
        return None


def _db_latest_chess_daily_status() -> Dict[str, Any]:
    """Liest die letzten Chess-Batches aus episodes/episodic_metrics.

    Ziel:
      • Sichtbar machen, ob der nächtliche Chess-Daily-Lauf wirklich geschrieben wurde
      • Die wichtigsten Kennzahlen ohne zusätzliche Runner-Logs direkt in der UI zeigen
      • Ehrlich bleiben: Die UI zeigt Chess damit als Trace-/Daily-Datenpfad; sie behauptet
        nicht, dass tabellarisches Lernen hier bereits "gelöst" ist.
    """
    def _load_metrics(conn: sqlite3.Connection, episode_id: int) -> Dict[str, float]:
        out: Dict[str, float] = {}
        cur = conn.cursor()
        cur.execute(
            "SELECT key, value FROM episodic_metrics WHERE episode_id=? ORDER BY id ASC",
            (int(episode_id),)
        )
        for row in (cur.fetchall() or []):
            if isinstance(row, dict) or hasattr(row, "keys"):
                k = row.get("key")
                v = row.get("value")
            else:
                k, v = row
            if k is None:
                continue
            try:
                out[str(k)] = float(v)
            except Exception:
                continue
        return out

    def _load_latest(conn: sqlite3.Connection, kind: str) -> Optional[Dict[str, Any]]:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, ts_start, ts_end, kind, source, label, meta_json FROM episodes WHERE kind=? ORDER BY COALESCE(ts_end, ts_start) DESC, id DESC LIMIT 1",
            (str(kind),)
        )
        row = cur.fetchone()
        if not row:
            return None
        if isinstance(row, dict) or hasattr(row, "keys"):
            eid = int(row.get("id") or 0)
            ts_start = int(row.get("ts_start") or 0)
            ts_end = int(row.get("ts_end") or 0)
            source = row.get("source")
            label = row.get("label")
            meta_json = row.get("meta_json")
        else:
            eid, ts_start, ts_end, _kind, source, label, meta_json = row
            eid = int(eid or 0)
            ts_start = int(ts_start or 0)
            ts_end = int(ts_end or 0)
        meta: Dict[str, Any] = {}
        try:
            if meta_json:
                meta = json.loads(meta_json)
                if not isinstance(meta, dict):
                    meta = {}
        except Exception:
            meta = {}
        metrics = _load_metrics(conn, eid)
        return {
            "episode_id": int(eid),
            "kind": str(kind),
            "ts_start": int(ts_start),
            "ts_end": int(ts_end),
            "source": str(source or ""),
            "label": str(label or ""),
            "meta": meta,
            "metrics": metrics,
        }

    out: Dict[str, Any] = {
        "ok": True,
        "latest_any_ts": 0,
        "latest_policy": None,
        "latest_explore": None,
        "latest_train": None,
        "latest_export": None,
        "note": (
            "Chess ist hier produktiv vor allem Daily-/Trace-Lieferant "
            "(episodes + snapchains). Das tabellarische Policy-Lernen bleibt "
            "experimentell und wird in der UI bewusst nur als Diagnose gezeigt."
        ),
    }
    try:
        with _db_conn() as conn:
            out["latest_policy"] = _load_latest(conn, "game:chess:policy_batch")
            out["latest_explore"] = _load_latest(conn, "game:chess:explore_batch")
            out["latest_train"] = _orch_job_status("chess_policy_train")
            out["latest_export"] = _orch_job_status("chess_policy_export")
            ts_vals = []
            for item in (out.get("latest_policy"), out.get("latest_explore"), out.get("latest_train"), out.get("latest_export")):
                if isinstance(item, dict):
                    try:
                        ts_vals.append(int(item.get("ts_end") or item.get("ts_start") or 0))
                    except Exception:
                        pass
            out["latest_any_ts"] = int(max(ts_vals) if ts_vals else 0)
    except Exception as e:
        LOG.debug("[_db_latest_chess_daily_status] %s", e)
        out["ok"] = False
        out["error"] = str(e)
    return out

# -----------------------------------------------------------------------------
# Flask-API
# -----------------------------------------------------------------------------
def _rt() -> ChessRuntime:
    if "_chess_rt" not in current_app.config:
        current_app.config["_chess_rt"] = ChessRuntime()
    return current_app.config["_chess_rt"]

@chess_bp.route("/")
def page():
    return render_template("chess.html")

@chess_bp.route("/api/state")
def api_state():
    return jsonify(_rt().state())

@chess_bp.route("/api/daily_status")
def api_daily_status():
    return jsonify(_db_latest_chess_daily_status())

@chess_bp.route("/api/move", methods=["POST"])
def api_move():
    d = request.get_json(force=True, silent=True) or {}
    uci = str(d.get("uci") or "")
    _rt().move_human(uci)
    return jsonify(_rt().state())

@chess_bp.route("/api/reset", methods=["POST"])
def api_reset():
    _rt().reset()
    return jsonify({"ok": True})

@chess_bp.route("/api/toggle", methods=["POST"])
def api_toggle():
    rt = _rt()
    rt.auto = not rt.auto
    return jsonify({"ok": True, "running": rt.auto})

@chess_bp.route("/api/mode", methods=["POST"])
def api_mode():
    d = request.get_json(force=True, silent=True) or {}
    m = (d.get("mode") or "").lower()
    rt = _rt()

    # Alias-Kompatibilität (Alt-UI/Bookmarks)
    if m in ("oroma_vs_oroma", "oroma_solo"):
        m = "oroma_vs_oroma_explore"

    if m in rt.MODES:
        rt.mode = m
        # Mode-Semantik: policy = eps 0, explore = default eps
        try:
            if m == "oroma_vs_oroma_policy":
                rt.eps = 0.0
            elif m == "oroma_vs_oroma_explore":
                rt.eps = float(EPS_DEFAULT)
        except Exception:
            pass
        return jsonify({"ok": True, "mode": m, "eps": rt.eps})
    return jsonify({"ok": False, "error": "Ungültiger Modus"}), 400

@chess_bp.route("/api/speed", methods=["POST"])
def api_speed():
    d = request.get_json(force=True, silent=True) or {}
    s = (d.get("speed") or "").lower()
    rt = _rt()
    if s in ("normal", "turbo"):
        rt.set_speed(s)
        return jsonify({"ok": True, "speed": s})
    return jsonify({"ok": False, "error": "Ungültige Speed-Angabe"}), 400

@chess_bp.route("/api/oromaSide", methods=["POST"])
def api_oroma_side():
    d = request.get_json(force=True, silent=True) or {}
    side = (d.get("side") or "").lower()
    rt = _rt()
    if side in ("white", "black"):
        rt.oroma_side = side
        return jsonify({"ok": True, "side": side})
    return jsonify({"ok": False, "error": "Ungültige Seite"}), 400

@chess_bp.route("/api/policy", methods=["GET", "POST"])
def api_policy():
    rt = _rt()
    if request.method == "GET":
        return jsonify({"enabled": rt.policy_enabled, "eps": rt.eps, "namespace": rt.policy.namespace})
    d = request.get_json(force=True, silent=True) or {}
    changed = False
    if "enabled" in d:
        rt.policy_enabled = bool(d.get("enabled")); changed = True
    if "eps" in d:
        try:
            val = float(d.get("eps")); rt.eps = max(0.0, min(1.0, val)); changed = True
        except Exception as e:
            log_suppressed('ui/chess_ui.py:799', exc=e, level=logging.WARNING)
            pass
    st = rt.state(); st["policy_changed"] = changed
    return jsonify(st)

# --- Diagnose & Notfall-Insert ----------------------------------------------
@chess_bp.route("/api/dbdiag", methods=["GET"])
def api_dbdiag():
    info: Dict[str, Any] = {
        "ok": True,
        "db_hint": None,
        "schema_ok": True,
        "by_origin": [],
        "total": 0,
        "last_ids": [],
        "using_fallback": _USING_FALLBACK,
    }
    try:
        info["db_hint"] = _sqlm_db_hint() or DB_ENV_PATH
        with _db_conn() as conn:
            cur = conn.cursor()
            _ensure_schema(conn)

            cur.execute("SELECT COUNT(*) AS cnt FROM snapchains")
            info["total"] = int(_row_val(cur.fetchone(), "cnt") or 0)

            cur.execute("SELECT origin, COUNT(*) AS n FROM snapchains GROUP BY origin ORDER BY n DESC")
            rows = cur.fetchall() or []
            by_origin: List[Dict[str, Any]] = []
            for r in rows:
                if isinstance(r, dict) or hasattr(r, "keys"):
                    origin = r.get("origin"); n = r.get("n", 0)
                else:
                    origin, n = r
                by_origin.append({"origin": (origin or None), "count": int(n)})
            info["by_origin"] = by_origin

            try:
                cur.execute("SELECT id FROM snapchains ORDER BY id DESC LIMIT 3")
                rows = cur.fetchall() or []
                info["last_ids"] = [int(_row_val(r, None, 0)) for r in rows if _row_val(r, None, 0) is not None]
            except Exception as e:
                log_suppressed('ui/chess_ui.py:841', exc=e, level=logging.WARNING)
                pass

        info["using_fallback"] = _USING_FALLBACK
    except Exception as e:
        info["ok"] = False
        info["schema_ok"] = False
        info["error"] = str(e)
        _gap("db_diag_error", "Fehler in /chess/api/dbdiag", 0.1, {"err": _safe_err(e)})
    return jsonify(info)

@chess_bp.route("/api/flush", methods=["POST", "GET"])
def api_flush():
    rt = _rt()
    try:
        if not getattr(rt.chain, "patterns", None):
            rt._append_snapshot(move_uci=None, side=("W" if rt.game.turn == "w" else "B"), outcome=0.0)

        export = rt._export_chain_safe()
        blob_b = json.dumps(export, ensure_ascii=False).encode("utf-8")

        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO snapchains (blob, origin, status, weight, quality, version, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (blob_b, "game:chess", "active", 1.0, 0.0, "v3.8", int(time.time()))
            )
            conn.commit()

            cur.execute("SELECT COUNT(*) AS cnt FROM snapchains")
            total = int(_row_val(cur.fetchone(), "cnt") or 0)
            cur.execute("SELECT COUNT(*) AS cnt FROM snapchains WHERE origin=?", ("game:chess",))
            chess = int(_row_val(cur.fetchone(), "cnt") or 0)

        return jsonify({"ok": True, "inserted": True,
                        "snapchains_total": total,
                        "snapchains_chess": chess})
    except Exception as e:
        LOG.error("[api_flush] DB-Insert-Fehler: %s", e)
        _gap("db_error", "Chess SnapChain-Insert fehlgeschlagen", 0.1,
             {"where": "api_flush", "err": _safe_err(e)})
        return jsonify({"ok": False, "inserted": False, "error": str(e)}), 500

@chess_bp.route("/api/snaps")
def api_snaps():
    items = _db_list_chess(25)
    return jsonify({"ok": True, "items": items})

@chess_bp.route("/api/counters")
def api_counters():
    return jsonify({"ok": True, **_db_counters()})
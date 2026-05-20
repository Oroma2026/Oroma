#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/mini_programs/chess/chess_game.py
# Projekt:   ORÓMA – Mini Programs
# Modul:     Schach – Game-Orchestrierung (Position, Züge, Persist)
# Version:   v1.9 (DB-Fallback fix: ts/quality/exported, sql_manager.insert_snapchain bevorzugt)
# Stand:     2025-10-29
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
#  Schlanker Wrapper um chess_rules.ChessPosition mit stabiler API:
#    • legal_uci()   → Liste legaler UCI-Züge
#    • play_uci()    → UCI-Zug anwenden
#    • fen()         → FEN aus Position (as_fen)
#    • winner()      → "white" | "black" | "draw" | None
#    • board_ascii() → ASCII-Board via Board.render()
#    • best_move()   → UCI mittels ChessAI
#
# Persist (SnapChain):
#  • Primär: core.snapchain.SnapChain (falls verfügbar)
#  • Sekundär: core.sql_manager.insert_snapchain({...})
#  • Tertiär: direkter INSERT mit vollständigen Spalten (ts/quality/exported/…)
#    → Schema-kompatibel zu core/sql_manager.py v3.8-r1 (ts NOT NULL, weight vorhanden)
# =============================================================================

from __future__ import annotations
from typing import List, Optional
import json
import time
import logging

from . import chess_rules as rules
from core import sql_manager
from core.log_guard import log_suppressed

LOG = logging.getLogger("oroma.chess.game")


FILES = "abcdefgh"


def _is_uci(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 4:
        return False
    return (s[0] in FILES and s[2] in FILES and s[1] in "12345678" and s[3] in "12345678")


class ChessGame:
    def __init__(self):
        self.pos = rules.ChessPosition()
        self.moves: List[str] = []

    @property
    def turn(self) -> str:
        # 'w' oder 'b'
        try:
            return "w" if getattr(self.pos, "stm", "white") == "white" else "b"
        except Exception:
            return "w"

    # ------------------------------------------------------------------ API --
    def legal_uci(self) -> List[str]:
        try:
            return [m for m in self.pos.legal_uci() if _is_uci(m)]
        except Exception:
            out: List[str] = []
            for m in self.pos.generate_legal_moves():
                try:
                    u = self.pos.move_to_uci(m)
                except Exception:
                    u = None
                if isinstance(u, str) and _is_uci(u):
                    out.append(u)
            return out

    def play_uci(self, uci: str) -> bool:
        uci = (uci or "").strip()
        if not _is_uci(uci):
            return False
        # bevorzugt: Positionsmethode
        if hasattr(self.pos, "play_uci"):
            try:
                res = self.pos.play_uci(uci)
                ok = (True if res is None else bool(res))
                if ok:
                    self.moves.append(uci)
                return ok
            except Exception as e:
                log_suppressed('mini_programs/chess/chess_game.py:93', exc=e, level=logging.WARNING)
                pass
        # Fallback: parse → apply
        try:
            mv = self.pos.parse_uci(uci)
            legals = set(self.legal_uci())
            if legals and uci not in legals:
                return False
            ok = self.pos.apply(mv)
            if ok:
                self.moves.append(uci)
            return ok
        except Exception:
            return False

    def winner(self) -> Optional[str]:
        try:
            st = self.pos.status()
        except Exception:
            return None
        if st in ("stalemate", "fifty_moves", "threefold"):
            return "draw"
        if st == "checkmate_black":  # Schwarz mattgesetzt → Weiß gewinnt
            return "white"
        if st == "checkmate_white":  # Weiß mattgesetzt → Schwarz gewinnt
            return "black"
        if st in ("white_won",):
            return "white"
        if st in ("black_won",):
            return "black"
        return None

    def fen(self) -> str:
        try:
            return self.pos.as_fen()
        except Exception:
            return "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1"

    def board_ascii(self) -> str:
        try:
            return self.pos.board.render()
        except Exception:
            return "<no-board>"

    def best_move(self, depth: int = 2) -> Optional[str]:
        try:
            from .chess_ai import ChessAI  # lazy
            ai = ChessAI(depth=depth)
            return ai.choose(self.pos)
        except Exception:
            return None

    # ------------------------------------------------------------ Persist -----
    def save_to_snapchain(self, result_tag: Optional[str]):
        """
        Speichert die Partie als SnapChain.
        Priorität: SnapChain.save() → sql_manager.insert_snapchain() → direkter INSERT (Schema-sicher).
        """
        # 1) SnapChain-Klasse vorhanden?
        try:
            from core.snapchain import SnapChain, SnapPattern  # type: ignore
            ch = SnapChain(
                origin="game:chess",
                status="active",
                weight=1.0,
                metadata={"result": (result_tag or "*"), "ts": int(time.time())},
            )
            for u in self.moves:
                ch.patterns.append(SnapPattern(metadata={"uci": u}))
            if hasattr(ch, "save"):
                ch.save()  # type: ignore[attr-defined]
                return
            else:
                from core.snapchain import save_chain  # type: ignore
                save_chain(ch)
                return
        except Exception as e:
            LOG.debug("SnapChain.save() nicht nutzbar (%s) – gehe zu DB-Insert.", e)

        # 2) sql_manager.insert_snapchain() nutzen, wenn verfügbar
        try:
            payload = {
                "schema_version": "chess-game-1",
                "metadata": {"game": "chess", "result": (result_tag or "*"), "ts": int(time.time())},
                "patterns": [{"metadata": {"uci": u}} for u in self.moves],
            }
            blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if hasattr(sql_manager, "insert_snapchain"):
                sql_manager.insert_snapchain({
                    "ts": int(time.time()),
                    "quality": 0.0,
                    "blob": blob,
                    "exported": 0,
                    "status": "active",
                    "origin": "game:chess",
                    "version": "v3.8",
                    "weight": 1.0,
                    "notes": "uci-seq",
                })
                return
        except Exception as e:
            LOG.debug("insert_snapchain() Pfad übersprungen (%s) – versuche direkten INSERT.", e)

        # 3) Direkter INSERT – vollständige Spalten (Schema v3.8-r1 kompatibel)
        try:
            now = int(time.time())
            payload = {
                "schema_version": "chess-game-1",
                "metadata": {"game": "chess", "result": (result_tag or "*"), "ts": now},
                "patterns": [{"metadata": {"uci": u}} for u in self.moves],
            }
            blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            with sql_manager.get_conn() as conn:
                conn.execute(
                    """INSERT INTO snapchains
                       (ts, quality, blob, exported, status, origin, weight, version, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (now, 0.0, blob, 0, "active", "game:chess", 1.0, "v3.8", "uci-seq"),
                )
                conn.commit()
        except Exception as e:
            LOG.error("DB-Fallback fehlgeschlagen: %s", e)
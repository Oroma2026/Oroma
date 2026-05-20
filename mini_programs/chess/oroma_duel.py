#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/mini_programs/chess/oroma_duel.py
# Projekt:   ORÓMA – Headless Mini-Program
# Modul:     Schach – Duel Runner (ORÓMA vs ORÓMA / KI vs KI / ORÓMA vs KI)
# Version:   v1.1 (Turn via game.turn, saubere SnapChain-Persist)
# Stand:     2025-10-29
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================

from __future__ import annotations
import os
import time
import json
import random
import logging
import argparse
from typing import Optional, Tuple

from mini_programs.chess.chess_game import ChessGame
from mini_programs.chess.chess_ai import ChessAI
from core import sql_manager

LOG = logging.getLogger("oroma.chess.duel")
if not LOG.handlers:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [Chess-Duel] %(message)s"))
    LOG.addHandler(sh)
LOG.setLevel(logging.INFO)

TICK_PROFILES = {
    "normal": {"tick": 0.15, "end": 0.5},
    "turbo":  {"tick": 0.00, "end": 0.0},
}

def _result_tag(w: Optional[str]) -> str:
    return "1-0" if w == "white" else ("0-1" if w == "black" else ("1/2-1/2" if w == "draw" else "*"))

def _legal_random(g: ChessGame) -> Optional[str]:
    opts = g.legal_uci()
    return random.choice(opts) if opts else None

def _choose_move(player: str, g: ChessGame, ai_oroma: ChessAI, ai_ki: Optional[ChessAI]) -> Optional[str]:
    if player == "oroma":
        return ai_oroma.choose(g.pos) or _legal_random(g)
    else:
        if ai_ki:
            return ai_ki.choose(g.pos) or _legal_random(g)
        return _legal_random(g)

def _save_chain_db(g: ChessGame, result: Optional[str]) -> None:
    try:
        g.save_to_snapchain(result)
    except Exception as e:
        LOG.warning("SnapChain-Export (intern) scheiterte: %s", e)
        try:
            d = {
                "schema_version": "chess-duel-1",
                "metadata": {"game":"chess","result": result or "*","moves": len(g.moves)},
                "patterns": [{"metadata":{"uci":u}} for u in g.moves]
            }
            blob = json.dumps(d, ensure_ascii=False).encode("utf-8")
            with sql_manager.get_conn() as conn:
                conn.execute(
                    "INSERT INTO snapchains (blob, origin, status, weight) VALUES (?,?,?,?)",
                    (blob, "game:chess", "active", 1.0)
                )
                conn.commit()
        except Exception as ex:
            LOG.error("DB-Fallback fehlgeschlagen: %s", ex)

def play_one(mode: str, speed: str, oroma_side: str, ai_oroma: ChessAI, ai_ki: Optional[ChessAI], save_db: bool) -> Tuple[str, int]:
    delays = TICK_PROFILES.get(speed, TICK_PROFILES["normal"])
    tick = delays["tick"]
    end  = delays["end"]

    g = ChessGame()
    halfmoves = 0

    while True:
        w = g.winner()
        if w:
            res = _result_tag(w)
            if save_db:
                _save_chain_db(g, res)
            time.sleep(end)
            return res, halfmoves

        side = g.turn   # 'w' oder 'b'
        if mode == "oroma_vs_oroma":
            who = "oroma"
        elif mode == "ki_vs_ki":
            who = "ki"
        elif mode == "oroma_vs_ki":
            who = "oroma" if ((oroma_side == "white" and side == "w") or (oroma_side == "black" and side == "b")) else "ki"
        else:
            who = "ki"

        mv = _choose_move(who, g, ai_oroma, ai_ki)
        if not mv:
            w = g.winner() or "draw"
            res = _result_tag(w)
            if save_db:
                _save_chain_db(g, res)
            time.sleep(end)
            return res, halfmoves

        ok = g.play_uci(mv)
        if ok:
            halfmoves += 1

        if tick > 0:
            time.sleep(tick)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--mode", type=str, default="oroma_vs_oroma",
                    choices=("oroma_vs_oroma","ki_vs_ki","oroma_vs_ki"))
    ap.add_argument("--oroma-side", type=str, default="white", choices=("white","black"))
    ap.add_argument("--speed", type=str, default="normal", choices=("normal","turbo"))
    ap.add_argument("--save-db", type=int, default=0)
    ap.add_argument("--seed", type=int, default=None)

    env_oroma_depth = int(os.environ.get("OROMA_CHESS_DEPTH", "2"))
    env_ki_depth    = int(os.environ.get("OROMA_CHESS_KI_DEPTH", "0"))
    ap.add_argument("--oroma-depth", type=int, default=env_oroma_depth)
    ap.add_argument("--ki-depth",    type=int, default=env_ki_depth)

    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    ai_oroma = ChessAI(depth=args.oroma_depth)
    ai_ki    = ChessAI(depth=args.ki_depth) if args.ki_depth and args.ki_depth > 0 else None

    Ww = Wb = D = 0
    plies_total = 0

    for _ in range(max(1, args.games)):
        try:
            res, plies = play_one(
                mode=args.mode,
                speed=args.speed,
                oroma_side=args.oroma_side,
                ai_oroma=ai_oroma,
                ai_ki=ai_ki,
                save_db=bool(args.save_db),
            )
            plies_total += plies
            if res == "1-0": Ww += 1
            elif res == "0-1": Wb += 1
            else: D += 1
        except Exception as e:
            LOG.error("Partie abgebrochen: %s", e)

    games = max(1, args.games)
    LOG.info("FERTIG – Weiß: %d  Schwarz: %d  Remis: %d  |  ⌀Halbzüge/Partie: %.1f",
             Ww, Wb, D, plies_total / games)

if __name__ == "__main__":
    main()
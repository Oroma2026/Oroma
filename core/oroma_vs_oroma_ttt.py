#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/mini_programs/oroma_vs_oroma_ttt.py
# Projekt:   ORÓMA – Headless Mini-Program
# Modul:     TicTacToe – ORÓMA vs ORÓMA (Policy→Heuristik, SnapChain optional in DB)
# Version:   v1.0 (Headless, Kanon-Adapter intern, robustes DB-Write)
# Stand:     2025-10-28
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
# Führt eine Serie von TicTacToe-Duellen **ORÓMA vs ORÓMA** ohne UI aus, um
# die gelernte Policy (SQLite policy_rules) real zu testen. Pro Zug werden
# Feature-Snaps (9D) mit präzisen Metadaten angehängt. Optional wird die
# komplette SnapChain als JSON in die DB geschrieben.
#
# MERKMALE
# ────────
#  • Headless (kein Qt/X11/Wayland), reine Konsolenlogs.
#  • Policy-First (DecisionEngine/TTTDecision → Adapter-intern).
#  • Heuristik-Fallback (Win/Block → Adapter-Fallback → Zufall).
#  • Exakte Metadaten je Zug: {"game":"tictactoe","player":"oroma","side":"X|O","idx":int}
#  • Optionaler DB-Insert (snapchains.blob, origin="game:tictactoe").
#
# NUTZUNG
# ───────
#   PYTHONPATH=/opt/ai/oroma \
#   python3 /opt/ai/oroma/mini_programs/oroma_vs_oroma_ttt.py \
#       --games 200 --save-db 1 --sleep 0.0
#
# ARGUMENTE
# ─────────
#   --games   <int>    Anzahl Spiele (Default: 50)
#   --save-db 0|1      1 = SnapChain jedes Spiels in DB speichern (Default: 0)
#   --sleep   <float>  Pause in Sekunden zwischen Zügen (Default: 0.0)
#   --seed    <int>    Optionaler RNG-Seed für Reproduzierbarkeit
#
# UMGEBUNG
# ────────
#   • PolicyEngine nutzt Namespace "game:tictactoe".
#   • PYTHONPATH muss /opt/ai/oroma enthalten.
# =============================================================================

from __future__ import annotations
import argparse
import logging
import random
import time
import json
from typing import List, Optional, Tuple

# Core
from core.decision_engine import TTTDecision
from core.ttt_adapter import TTTAdapter
from core.snapchain import SnapChain
from core import sql_manager

LOG = logging.getLogger("oroma.duel.ttt")
if not LOG.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [TTT-Duel] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Spiel-Utilities
# -----------------------------------------------------------------------------
WIN_LINES = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def check_winner(board: List[str]) -> Optional[str]:
    for a,b,c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]  # "X" oder "O"
    if all(board):
        return "D"
    return None

def policy_pick(dec: TTTDecision, board: List[str]) -> Optional[int]:
    a = dec.choose_action_from_board(board)
    if a is None:
        return None
    i = int(a)
    if 0 <= i < 9 and not board[i]:
        return i
    return None

def adapter_fallback(board: List[str]) -> Optional[int]:
    vec = TTTAdapter.vectorize_board(board)
    a = TTTAdapter.fallback_action(vec)
    if a is None:
        return None
    i = int(a)
    return i if 0 <= i < 9 and not board[i] else None

# -----------------------------------------------------------------------------
# Ein Spiel
# -----------------------------------------------------------------------------
def play_one(save_db: bool=False, sleep_s: float=0.0) -> Tuple[str, int]:
    board = [""]*9
    turn = "X"
    dec = TTTDecision()
    chain = SnapChain(patterns=[], metadata={"game":"tictactoe","mode":"oroma_vs_oroma","runner":"headless"})

    moves = 0
    while True:
        # Policy → Heuristik → Adapter-Fallback → Random
        idx = policy_pick(dec, board)
        if idx is None:
            # Heuristik: Win/Block
            legal = [i for i,v in enumerate(board) if not v]
            played = False
            for sym in ("X", "O"):
                for i in legal:
                    tmp = board.copy()
                    tmp[i] = sym
                    if check_winner(tmp) == sym:
                        idx = i
                        played = True
                        break
                if played:
                    break
            if idx is None:
                idx = adapter_fallback(board)
            if idx is None and legal:
                idx = random.choice(legal)

        if idx is None:
            # Kein Zug möglich
            break

        # Zug anwenden
        side = turn
        board[idx] = side
        moves += 1

        # Snap anhängen (Pattern-First ist in SnapChain/SnapPattern implementiert; hier: Feature direkt)
        vec = TTTAdapter.vectorize_board(board)
        try:
            # SnapChain unterstützt append_features(vec, meta=...)
            chain.append_features(vec, meta={"player":"oroma", "side":side, "idx":idx})
        except Exception:
            # Fallback: falls append_features nicht existiert, über from_snaps realisieren
            try:
                from core.snappattern import SnapPattern
                pat = SnapPattern.from_snaps([list(map(float, vec))], metadata={"player":"oroma","side":side,"idx":idx,"game":"tictactoe"})
                chain.append(pat)  # type: ignore
            except Exception as e:
                LOG.warning("Append-Fallback fehlgeschlagen: %s", e)

        w = check_winner(board)
        if w:
            if save_db:
                try:
                    d = chain.to_dict() if hasattr(chain, "to_dict") else {"patterns": [], "metadata": {"game":"tictactoe"}}
                    if not d.get("patterns"):
                        # Minimal-Fallback: nichts speichern, wenn leer
                        LOG.warning("Nichts zu speichern – keine Patterns.")
                    else:
                        blob_b = json.dumps(d, ensure_ascii=False).encode("utf-8")
                        if hasattr(sql_manager, "insert_snapchain"):
                            sql_manager.insert_snapchain({
                                "blob": blob_b,
                                "origin": "game:tictactoe",
                                "quality": 0.0,
                                "status": "active",
                                "version": "v3.8",
                                "ts": int(time.time())
                            })
                        else:
                            with sql_manager.get_conn() as conn:
                                conn.execute(
                                    "INSERT INTO snapchains (blob, origin, status, weight) VALUES (?,?,?,?)",
                                    (blob_b, "game:tictactoe", "active", 1.0)
                                )
                                conn.commit()
                        LOG.info("💾 SnapChain gespeichert (%d Züge)", moves)
                except Exception as e:
                    LOG.error("DB-Speicherfehler: %s", e)
            return (w, moves)

        turn = "O" if turn=="X" else "X"
        if sleep_s>0:
            time.sleep(sleep_s)

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=50, help="Anzahl Spiele")
    ap.add_argument("--save-db", type=int, default=0, help="1=SnapChains speichern")
    ap.add_argument("--sleep", type=float, default=0.0, help="Pause zwischen Zügen (Sek.)")
    ap.add_argument("--seed", type=int, default=None, help="RNG-Seed (optional)")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    wins = {"X":0, "O":0, "D":0}
    moves_total = 0
    for _ in range(max(1,args.games)):
        w, m = play_one(save_db=bool(args.save_db), sleep_s=max(0.0,args.sleep))
        wins[w] += 1
        moves_total += m
    LOG.info("FERTIG – X:%d  O:%d  D:%d  |  ⌀Züge/Spiel: %.2f",
             wins["X"], wins["O"], wins["D"], moves_total/max(1,args.games))

if __name__ == "__main__":
    main()
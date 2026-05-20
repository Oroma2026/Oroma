#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/chess/cli.py
# Projekt: ORÓMA
# Modul:   Schach – CLI-Frontend (Mensch vs. KI / KI vs. KI)
# Version: v3.8 (nutzt ChessGame.board_ascii/best_move, SnapChain on end)
# Stand:   2025-10-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================

from __future__ import annotations
import sys
from .chess_game import ChessGame

def main():
    print("ORÓMA Chess – CLI\n")
    game = ChessGame()
    depth = 2  # Pi-geeignet
    human_color = "white"  # 'white' oder 'black' oder None (KI vs KI)

    while True:
        print(game.board_ascii(), "\n")
        w = game.winner()
        if w:
            print("Ergebnis:", w)
            game.save_to_snapchain("1-0" if w == "white" else "0-1" if w == "black" else "1/2-1/2")
            break

        stm = "white" if game.turn == "w" else "black"
        if human_color is None or human_color != stm:
            mv = game.best_move(depth=depth)
            if mv is None:
                print("Kein legaler KI-Zug – Remis?")
                game.save_to_snapchain("1/2-1/2")
                break
            game.play_uci(mv)
            print(f"KI ({stm}) spielt: {mv}")
            continue

        # Mensch
        legal = set(game.legal_uci())
        prompt = f"Dein Zug ({stm}, UCI, z.B. e2e4, 'quit' zum Beenden): "
        mv = input(prompt).strip()
        if mv.lower() in ("quit","exit"):
            print("Abbruch.")
            game.save_to_snapchain("abort")
            sys.exit(0)
        if mv not in legal:
            if mv + "q" in legal:  # Promotion-Abkürzung tolerieren
                mv = mv + "q"
            else:
                print("Ungültig. Legale Züge:", sorted(list(legal))[:20], "...")
                continue
        game.play_uci(mv)

if __name__ == "__main__":
    main()
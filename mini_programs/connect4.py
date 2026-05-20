#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/connect4.py
# Projekt: ORÓMA v3.7.x
# Modul:   Connect4 Standalone / Legacy Minimax Console Helper
# Version: v3.7.3
# Stand:   2026-03-11
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul stellt einen einfachen, eigenständig startbaren Connect4-Kern mit
# CLI und Minimax-basierter Gegenseite bereit. Es bleibt im Projekt bewusst als
# kompakter Referenz-/Vergleichspfad erhalten, ist aber NICHT der primäre
# produktive Lernpfad für ORÓMA.
#
# PRODUKTIVE EINORDNUNG
# ---------------------
# Für ORÓMA v3.7.x gilt bei Connect4:
#   • UI / Runtime:         ui/connect4_ui.py
#   • Daily-Batches:        tools/connect4_daily_runner.py
#   • Nightly-Orchestrator: tools/oroma_orchestrator.py
#
# Diese drei Pfade bilden den eigentlichen UniversalPolicy-/Episode-/Daily-
# Standard. Dieses Mini-Programm bleibt dagegen absichtlich als:
#   • standalone CLI-Helfer,
#   • lokaler Minimax-Vergleichspfad,
#   • kompakter Debug-/Regressionstest.
#
# WICHTIG
# -------
# Dieses Modul schreibt NICHT dieselben Daily-Metriken wie der moderne Runner
# und ersetzt ihn auch nicht. Es ist damit eher ein Legacy-/Baseline-Werkzeug
# als der führende Connect4-Lernpfad. Die ehrliche Trennung ist wichtig, damit
# später keine Missverständnisse zwischen Minimax-Baseline und UniversalPolicy-
# Training entstehen.
#
# SNAPCHAIN / DB
# --------------
# Falls core.snapchain / core.sql_manager verfügbar sind, kann der Spielverlauf
# weiterhin als SnapChain gespeichert werden. Fehler werden bewusst abgefangen;
# offene DB-Verbindungen werden in finally-Blöcken geschlossen.
# =============================================================================

import random
import math
from typing import List, Optional

try:
    from core.snapchain import SnapChain
    from core.snappattern import SnapPattern
    from core.sql_manager import get_db_connection
except ImportError:
    SnapChain = None
    SnapPattern = None
    get_db_connection = None


ROWS = 6
COLS = 7
CONNECT_N = 4


class Connect4:
    def __init__(self):
        self.board = [[0] * COLS for _ in range(ROWS)]
        self.current_player = 1  # 1 = Mensch / KI1, -1 = KI2
        self.moves = []  # History für SnapChain

    def reset(self):
        self.board = [[0] * COLS for _ in range(ROWS)]
        self.current_player = 1
        self.moves = []

    def valid_moves(self) -> List[int]:
        return [c for c in range(COLS) if self.board[0][c] == 0]

    def play(self, col: int) -> bool:
        """Führe einen Zug aus. Gibt True zurück, wenn gültig."""
        if col not in self.valid_moves():
            return False
        for row in reversed(range(ROWS)):
            if self.board[row][col] == 0:
                self.board[row][col] = self.current_player
                self.moves.append((self.current_player, col))
                self.current_player *= -1
                return True
        return False

    def winner(self) -> int:
        """Überprüft, ob jemand gewonnen hat. Rückgabe: 1, -1 oder 0"""
        for row in range(ROWS):
            for col in range(COLS):
                if self.board[row][col] == 0:
                    continue
                if self.check_dir(row, col, 1, 0) or \
                   self.check_dir(row, col, 0, 1) or \
                   self.check_dir(row, col, 1, 1) or \
                   self.check_dir(row, col, 1, -1):
                    return self.board[row][col]
        return 0

    def check_dir(self, row: int, col: int, dr: int, dc: int) -> bool:
        """Hilfsfunktion: prüft Sieg in Richtung (dr, dc)."""
        start = self.board[row][col]
        for i in range(1, CONNECT_N):
            r, c = row + dr * i, col + dc * i
            if not (0 <= r < ROWS and 0 <= c < COLS):
                return False
            if self.board[r][c] != start:
                return False
        return True

    def full(self) -> bool:
        return all(self.board[0][c] != 0 for c in range(COLS))

    def print_board(self):
        """CLI-Ausgabe"""
        for row in self.board:
            print(" ".join(["." if x == 0 else ("X" if x == 1 else "O") for x in row]))
        print("-" * COLS)

    # ---------- KI-Logik ----------

    def ai_move(self, depth: int = 4) -> int:
        """Berechnet den besten KI-Zug mit Minimax/Alpha-Beta."""
        _, move = self.minimax(depth, -math.inf, math.inf, True)
        if move is None:
            return random.choice(self.valid_moves())
        return move

    def score_position(self, player: int) -> int:
        """Bewertet die aktuelle Stellung grob."""
        score = 0
        center = [self.board[r][COLS // 2] for r in range(ROWS)]
        score += center.count(player) * 3
        # horizontale Fenster
        for r in range(ROWS):
            for c in range(COLS - 3):
                window = self.board[r][c:c + 4]
                score += self.evaluate_window(window, player)
        # vertikale Fenster
        for c in range(COLS):
            col_array = [self.board[r][c] for r in range(ROWS)]
            for r in range(ROWS - 3):
                window = col_array[r:r + 4]
                score += self.evaluate_window(window, player)
        return score

    def evaluate_window(self, window: List[int], player: int) -> int:
        score = 0
        opp = -player
        if window.count(player) == 4:
            score += 100
        elif window.count(player) == 3 and window.count(0) == 1:
            score += 5
        elif window.count(player) == 2 and window.count(0) == 2:
            score += 2
        if window.count(opp) == 3 and window.count(0) == 1:
            score -= 4
        return score

    def minimax(self, depth: int, alpha: float, beta: float, maximizing: bool):
        valid_moves = self.valid_moves()
        winner = self.winner()
        if depth == 0 or winner != 0 or not valid_moves:
            if winner == 1:
                return (math.inf, None)
            elif winner == -1:
                return (-math.inf, None)
            else:
                return (self.score_position(1), None)

        if maximizing:
            value = -math.inf
            best_move = random.choice(valid_moves)
            for col in valid_moves:
                tmp = Connect4()
                tmp.board = [row[:] for row in self.board]
                tmp.current_player = self.current_player
                tmp.play(col)
                score, _ = tmp.minimax(depth - 1, alpha, beta, False)
                if score > value:
                    value, best_move = score, col
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
            return value, best_move
        else:
            value = math.inf
            best_move = random.choice(valid_moves)
            for col in valid_moves:
                tmp = Connect4()
                tmp.board = [row[:] for row in self.board]
                tmp.current_player = self.current_player
                tmp.play(col)
                score, _ = tmp.minimax(depth - 1, alpha, beta, True)
                if score < value:
                    value, best_move = score, col
                beta = min(beta, value)
                if alpha >= beta:
                    break
            return value, best_move

    # ---------- SnapChain Anbindung ----------

    def save_to_snapchain(self, result: int):
        """Speichert den Verlauf optional als einfache Connect4-SnapChain.

        Hinweis:
          Dieser Pfad ist bewusst konservativ gehalten und dient primär der
          lokalen Nachvollziehbarkeit des Standalone-Minimax-Spiels. Der moderne
          produktive ORÓMA-Connect4-Datenpfad läuft über ui/connect4_ui.py und
          tools/connect4_daily_runner.py.
        """
        if not SnapChain or not SnapPattern or not get_db_connection:
            return
        conn = None
        try:
            conn = get_db_connection()
            chain = SnapChain(patterns=[], metadata={"game": "connect4", "result": result, "source": "mini_programs.connect4"})
            for player, col in self.moves:
                pattern = SnapPattern(features=[col], metadata={"player": player})
                chain.patterns.append(pattern)
            chain.save(conn)
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass


# ---------- CLI Start ----------

def main():
    game = Connect4()
    print("ORÓMA Connect4 Standalone – Mensch vs KI (X = Mensch, O = KI)\n")
    game.print_board()

    while True:
        if game.current_player == 1:
            move = int(input("Dein Zug (0-6): "))
            if not game.play(move):
                print("Ungültig, nochmal.")
                continue
        else:
            ai_col = game.ai_move()
            print(f"KI spielt: {ai_col}")
            game.play(ai_col)

        game.print_board()

        if game.winner() != 0:
            print("Spielende! Gewinner:", "Mensch" if game.winner() == 1 else "KI")
            game.save_to_snapchain(game.winner())
            break
        if game.full():
            print("Unentschieden!")
            game.save_to_snapchain(0)
            break


if __name__ == "__main__":
    main()
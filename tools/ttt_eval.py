#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/ttt_eval.py
# Projekt: ORÓMA – TTT Self-Play Evaluator (Side-aware, Rule-first)
# Version: v1.1
# Datum:   2025-11-05
# Autor:   ORÓMA · KI-JWG-X1
# Zweck:
#   Self-Play N Partien. Beide Seiten nutzen dieselbe DecisionEngine,
#   aber O wird per Symbol-Swap als "X" betrachtet (X↔O), damit die
#   Policy/Regeln symmetrisch angewendet werden.
# Nutzung:
#   PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/tools/ttt_eval.py 500
# =============================================================================
import sys, random

BASE="/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.decision_engine import TTTDecision

WINS=[(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

def swap_symbols(board):
    # X<->O tauschen
    return ["X" if c=="O" else "O" if c=="X" else "" for c in board]

def winner(board):
    for a,b,c in WINS:
        if board[a] and board[a]==board[b]==board[c]:
            return board[a]
    return "draw" if all(board) else None

def choose_side_aware(dec: TTTDecision, board, player):
    # Die meisten Engines denken aus X-Sicht. Für O tauschen wir Symbols.
    if player == "X":
        act = dec.choose_action_from_board(board)
        return int(str(act))
    else:
        tmp = swap_symbols(board)
        act = dec.choose_action_from_board(tmp)
        return int(str(act))  # Index ist invariant unter X/O-Tausch

def play_one(dec: TTTDecision, start="X"):
    b = [""]*9
    p = start
    moves=0
    while True:
        idx = choose_side_aware(dec, b, p)
        if idx<0 or idx>8 or b[idx]:
            return {"result":"illegal","moves":moves}
        b[idx]=p
        moves+=1
        w = winner(b)
        if w: return {"result":w,"moves":moves}
        p = "O" if p=="X" else "X"

def main():
    n = int(sys.argv[1]) if len(sys.argv)>1 else 200
    random.seed(1337)
    dec = TTTDecision()
    stats={"X":0,"O":0,"draw":0,"illegal":0}
    for i in range(n):
        r = play_one(dec, start=("X" if i%2==0 else "O"))
        stats[r["result"]] = stats.get(r["result"],0)+1
    total=sum(stats.values())
    print("== TTT Self-Play (side-aware) ==")
    print(f"Games: {total}")
    draw_rate=100.0*stats["draw"]/max(1,total)
    print(f"Draws: {stats['draw']}  ({draw_rate:.1f}%)")
    print(f"X wins: {stats['X']}   O wins: {stats['O']}")
    print(f"Illegal: {stats['illegal']}")
    print("\nErwartung bei guter Regel-/Policyabdeckung: ≥ 90 % Draws.")

if __name__=="__main__":
    main()
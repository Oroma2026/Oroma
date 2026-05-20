#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/tictactoe.py
# Projekt: ORÓMA – Mini-Programme
# Modul:   TicTacToe (Console) – Universal Policy Shim + D4-Kanonisierung
# Version: v3.7.3-r3 (UI-Policy-Shim-Style, leiser SnapChain-Export)
# Stand:   2025-12-07
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# =============================================================================
#
# ZWECK
# ─────
#   Konsolenbasiertes TicTacToe-Spiel:
#     • X = Mensch, O = ORÓMA (Policy/Heuristik)
#     • Verwendet, wenn möglich, denselben UniversalPolicyShim wie die UI:
#         - D4-Kanonisierung (stabiler state_hash)
#         - DB-UPSERT in policy_rules (namespace='game:tictactoe')
#         - Schutz gegen Exceptions im Policy-Code
#     • Fällt bei Problemen auf eine sichere Heuristik zurück
#
# WICHTIGE EIGENSCHAFTEN
# ──────────────────────
#   • Kein direkter Zugriff auf core.universal_policy.Policy.
#     → Alle Policy-Details laufen über ui.tictactoe_ui.UniversalPolicyShim.
#   • Lernfeedback am Spielende:
#       items = [{state_hash, action_canon, side, outcome(+1/-1/0), ts}, ...]
#     → policy.learn_many(items) (Shim kümmert sich um DB & impl.learn()).
#   • Optionaler SnapChain-Export:
#       - benutzt eigene INSERT-Logik via sql_manager.get_conn()
#       - Fehler (inkl. "database is locked") werden stumm geschluckt.
#
# NUTZUNG
# ───────
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma python3 -m mini_programs.tictactoe
#
#   Ablauf:
#     • Du bist 'X' und spielst über Eingaben 0..8.
#     • ORÓMA ist 'O' und nutzt Policy + Heuristik.
# =============================================================================

from __future__ import annotations

import time
import random
import json
from typing import List, Dict, Any, Optional
import logging
from core.log_guard import log_suppressed

# -----------------------------------------------------------------------------
# Versuche, UniversalPolicyShim + _state_hash aus der UI zu verwenden
# -----------------------------------------------------------------------------
try:
    # liefert: UniversalPolicyShim(namespace="game:tictactoe"), _state_hash(board, side)
    from ui.tictactoe_ui import UniversalPolicyShim, _state_hash  # type: ignore
    _HAVE_UP_SHIM = True
except Exception:
    UniversalPolicyShim = None  # type: ignore
    _state_hash = None          # type: ignore
    _HAVE_UP_SHIM = False

# Optional: DB-Helper
try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


# -----------------------------------------------------------------------------
# Fallback-Policy (wenn UI-Shim nicht verfügbar ist)
# -----------------------------------------------------------------------------
class _FallbackPolicy:
    """
    Minimaler Policy-Stub:
      • .choose(board, side, legal_moves) → None (nur Heuristik wird genutzt)
      • .learn_many(items) → no-op
    """
    def __init__(self, namespace: str = "game:tictactoe"):
        self.namespace = namespace
        self.enabled = False  # Signalisiert, dass keine echte Policy aktiv ist

    def choose(self, board: List[str], side: str, legal_moves: List[int]) -> Optional[int]:
        return None

    def learn_many(self, items: List[Dict[str, Any]]) -> None:
        return None


# -----------------------------------------------------------------------------
# Hilfsfunktionen für das Spielfeld
# -----------------------------------------------------------------------------
def _check_winner(board: List[str]) -> Optional[str]:
    """Ermittelt den Gewinner: 'X', 'O', 'draw' oder None (läuft noch)."""
    wins = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    for a, b, c in wins:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None


def _print_board(board: List[str]) -> None:
    """Einfaches ASCII-Board."""
    def v(i: int) -> str:
        return board[i] or " "
    print(f"""
 {v(0)} | {v(1)} | {v(2)}
---+---+---
 {v(3)} | {v(4)} | {v(5)}
---+---+---
 {v(6)} | {v(7)} | {v(8)}
""")


def _safe_random_move(board: List[str], sym_me: str) -> Optional[int]:
    """
    Simple Sicherheits-Heuristik:
      • Nimmt direkten Gewinn, falls vorhanden.
      • Sonst vermeidet 1-Zug-Niederlage, falls möglich.
    """
    legal = [i for i, v in enumerate(board) if not v]
    if not legal:
        return None
    sym_you = "O" if sym_me == "X" else "X"

    # 1) Sofortgewinn für mich
    for i in legal:
        tmp = board.copy()
        tmp[i] = sym_me
        if _check_winner(tmp) == sym_me:
            return i

    # 2) Vermeide 1-Zug-Matt für den Gegner
    safe = []
    for i in legal:
        tmp = board.copy()
        tmp[i] = sym_me
        bad = False
        for j, v in enumerate(tmp):
            if not v:
                tmp2 = tmp.copy()
                tmp2[j] = sym_you
                if _check_winner(tmp2) == sym_you:
                    bad = True
                    break
        if not bad:
            safe.append(i)

    if safe:
        return random.choice(safe)
    return random.choice(legal)


# -----------------------------------------------------------------------------
# Spiel-Engine für die Konsole
# -----------------------------------------------------------------------------
class ConsoleTicTacToe:
    """
    Einfacher Mensch-vs-ORÓMA TicTacToe Runner für die Konsole.

    • X = Mensch
    • O = ORÓMA (Policy/Heuristik)
    """

    def __init__(self) -> None:
        # Board & Status
        self.board: List[str] = [""] * 9
        self.turn: str = "X"  # Mensch beginnt
        self.winner: Optional[str] = None

        # Trajektorie der ORÓMA-Züge:
        #   [{"state_hash":..., "action_canon":..., "side":"O"}, ...]
        self._traj: List[Dict[str, Any]] = []

        # Policy wählen (UI-Shim bevorzugt)
        if _HAVE_UP_SHIM and UniversalPolicyShim is not None:
            self.policy = UniversalPolicyShim(namespace="game:tictactoe")  # type: ignore
        else:
            self.policy = _FallbackPolicy(namespace="game:tictactoe")

    # ---------------------------------------------------------------------
    # Policy-Zug
    # ---------------------------------------------------------------------
    def _policy_move(self) -> Optional[int]:
        """Fragt die Policy (Shim) nach einem Zug; Fallback auf Heuristik."""
        legal = [i for i, v in enumerate(self.board) if not v]
        if not legal:
            return None

        # 1) Versuch über UniversalPolicyShim
        try:
            if getattr(self.policy, "enabled", False):
                a = self.policy.choose(self.board, self.turn, legal)  # type: ignore[attr-defined]
            else:
                a = None
        except Exception:
            # Alle Policy-Fehler werden abgefangen → keine Crashes
            a = None

        # 2) Fallback: Heuristik
        if a is None:
            if 4 in legal:
                return 4
            corners = [i for i in (0, 2, 6, 8) if i in legal]
            if corners:
                return _safe_random_move(self.board, self.turn) or random.choice(corners)
            return _safe_random_move(self.board, self.turn) or random.choice(legal)

        return a if a in legal else random.choice(legal)

    # ---------------------------------------------------------------------
    # Züge anwenden
    # ---------------------------------------------------------------------
    def _apply_move(self, idx: int, side: str, record_traj: bool) -> bool:
        """Setzt einen Zug, optional mit Trajektorien-Update für Policy-Lernen."""
        if idx < 0 or idx > 8:
            return False
        if self.board[idx]:
            return False

        # Vor dem Setzen: state_hash + action_canon (nur für ORÓMA-Züge relevant)
        if record_traj and _state_hash is not None:
            try:
                sh, M, _M_inv = _state_hash(self.board, side)  # type: ignore[misc]
                a_canon = M[idx]
                self._traj.append({
                    "state_hash": sh,
                    "action_canon": int(a_canon),
                    "side": side,
                })
            except Exception as e:
                # Trajektorien-Fehler sollen das Spiel nicht abbrechen
                log_suppressed('mini_programs/tictactoe.py:234', exc=e, level=logging.WARNING)
                pass

        self.board[idx] = side
        self.winner = _check_winner(self.board)
        self.turn = "O" if side == "X" else "X"
        return True

    # ---------------------------------------------------------------------
    # Lernfeedback
    # ---------------------------------------------------------------------
    def _finish_and_learn(self) -> None:
        """Berechnet Outcome pro Trajektorien-Schritt und ruft policy.learn_many()."""
        if not self._traj:
            return
        winner = self.winner or "draw"
        items: List[Dict[str, Any]] = []
        now = int(time.time())

        for tr in self._traj:
            side = tr["side"]
            if winner == side:
                out = +1.0
            elif winner in ("X", "O"):
                out = -1.0
            else:
                out = 0.0
            items.append({
                "state_hash": tr["state_hash"],
                "action_canon": tr["action_canon"],
                "side": side,
                "outcome": out,
                "ts": now,
            })

        try:
            # Shim kümmert sich um DB-UPSERT + impl.learn(...)
            self.policy.learn_many(items)  # type: ignore[attr-defined]
        except Exception as e:
            # Lernfehler sind nicht kritisch für das Spiel
            log_suppressed('mini_programs/tictactoe.py:274', exc=e, level=logging.WARNING)
            pass

    # ---------------------------------------------------------------------
    # Optionaler SnapChain-Export (kompakt, leise)
    # ---------------------------------------------------------------------
    def _export_snapchain(self) -> None:
        """
        Kompakter SnapChain-Export in die Tabelle 'snapchains'.

        • Nutzt direkt sql_manager.get_conn() mit eigenem INSERT.
        • Alle Fehler (inkl. "database is locked") werden stumm ignoriert.
        """
        if not sql_manager or not hasattr(sql_manager, "get_conn"):
            return
        try:
            export = {
                "schema_version": "console-ttt-1",
                "patterns": [{
                    "centroid": [1.0 if v == "X" else -1.0 if v == "O" else 0.0 for v in self.board],
                    "num_snaps": len([v for v in self.board if v]),
                }],
                "metadata": {
                    "game": "tictactoe",
                    "winner": self.winner or "draw",
                    "mode": "console_human_vs_oroma",
                },
                "valid": True,
                "ts_created": time.time(),
            }
            blob_b = json.dumps(export, ensure_ascii=False).encode("utf-8")
            ts_now = int(time.time())

            # Direkter INSERT, ohne globales insert_snapchain() → keine Debug-Ausgabe
            with sql_manager.get_conn() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    """
                    INSERT INTO snapchains
                      (ts, quality, blob, exported, status, origin,
                       gap_flag, notes, namespace, source_id, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts_now,
                        0.0,
                        blob_b,
                        0,
                        "active",
                        "game:tictactoe",
                        0,
                        "console_export",
                        "game:tictactoe",
                        None,
                        "v3.7.3",
                    ),
                )
                conn.commit()
        except Exception:
            # Export ist "best effort" – Fehler stillschweigend ignorieren
            return

    # ---------------------------------------------------------------------
    # Haupt-Loop
    # ---------------------------------------------------------------------
    def play(self) -> None:
        print("Start TicTacToe – X = Du (Mensch), O = ORÓMA (Policy/Heuristik).")
        print("Felder werden als Index 0–8 adressiert:")
        print("  0 | 1 | 2")
        print(" ---+---+---")
        print("  3 | 4 | 5")
        print(" ---+---+---")
        print("  6 | 7 | 8\n")

        while not self.winner:
            _print_board(self.board)
            if self.turn == "X":
                # Mensch
                try:
                    raw = input("Dein Zug (0–8, q=Quit): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAbbruch.")
                    return
                if raw.lower() in ("q", "quit", "exit"):
                    print("Spiel beendet (manueller Abbruch).")
                    return
                try:
                    i = int(raw)
                except ValueError:
                    print("Bitte eine Zahl 0–8 eingeben.\n")
                    continue
                if not self._apply_move(i, "X", record_traj=False):
                    print("Ungültiger Zug – Feld belegt oder außerhalb von 0–8.\n")
                    continue
            else:
                # ORÓMA
                idx = self._policy_move()
                if idx is None:
                    legal = [i for i, v in enumerate(self.board) if not v]
                    if not legal:
                        self.winner = _check_winner(self.board)
                        break
                    idx = random.choice(legal)
                self._apply_move(idx, "O", record_traj=True)
                print(f"ORÓMA zieht auf Feld {idx}.\n")
                time.sleep(0.1)

        # Spielende
        _print_board(self.board)
        if self.winner == "draw":
            print("⚖️ Unentschieden!")
        else:
            print(f"🏁 Gewinner: {self.winner}")

        # Lernfeedback + optionaler SnapChain-Export
        self._finish_and_learn()
        self._export_snapchain()


# -----------------------------------------------------------------------------
# CLI-Entry
# -----------------------------------------------------------------------------
def main() -> None:
    game = ConsoleTicTacToe()
    game.play()


if __name__ == "__main__":
    main()
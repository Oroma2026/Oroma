#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/chess/chess_rules.py
# Projekt: ORÓMA
# Modul:   Schach – Regeln & Position (legal, Apply/Undo, FEN, UCI)
# Version: v3.8-r3 (FEN/EP-Fix, legal_uci/move_to_uci, robustes Undo)
# Stand:   2025-10-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# KERNAUSBAUTEN ggü. alt:
#  • NEU: ChessPosition.as_fen() → vollständige FEN (board/side/castle/ep/half/full)
#  • NEU: ChessPosition.legal_uci(), move_to_uci(), parse_uci(self, s)
#  • FIX: En-passant-Undo robust (merkt Capturesquare separat)
#  • FIX: Status-Mapping klar: 'checkmate_black' = Schwarz mattgesetzt (Weiß gewinnt)
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Iterable
from .board import Board, Coord

WHITE, BLACK = "white", "black"
FILES = "abcdefgh"


def rc_to_sq(rc: Coord) -> str:
    r, c = rc
    file = FILES[c]
    rank = 8 - r
    return f"{file}{rank}"


def sq_to_rc(sq: str) -> Coord:
    file = sq[0]
    rank = int(sq[1])
    c = FILES.index(file)
    r = 8 - rank
    return (r, c)


# --- Move ---------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    frm: Coord
    to: Coord
    promo: Optional[str] = None  # 'q','r','b','n' (Kleinbuchstaben)


def parse_uci(s: str) -> Move:
    s = (s or "").strip()
    frm = sq_to_rc(s[:2])
    to = sq_to_rc(s[2:4])
    promo = s[4].lower() if len(s) >= 5 else None
    return Move(frm, to, promo)


def algebraic(move: Move) -> str:
    s = rc_to_sq(move.frm) + rc_to_sq(move.to)
    if move.promo:
        s += move.promo
    return s


# --- Position / State ---------------------------------------------------------

def _initial_setup() -> Dict[Coord, str]:
    m: Dict[Coord, str] = {}
    # Weiße Figuren
    pieces = "RNBQKBNR"
    for i, ch in enumerate(pieces):
        m[(7, i)] = ch
        m[(6, i)] = "P"
    # Schwarze Figuren
    for i, ch in enumerate(pieces.lower()):
        m[(0, i)] = ch
        m[(1, i)] = "p"
    return m


def _color_of(p: str) -> Optional[str]:
    if p == ".":
        return None
    return WHITE if p.isupper() else BLACK


PIECE_VALUES = {
    "P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 0,
    "p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 0
}


class Castle:
    # Rechte: KQkq
    def __init__(self, WK=True, WQ=True, BK=True, BQ=True):
        self.WK = WK
        self.WQ = WQ
        self.BK = BK
        self.BQ = BQ

    def __repr__(self) -> str:
        s = "".join([c for c, v in zip("KQkq", [self.WK, self.WQ, self.BK, self.BQ]) if v])
        return s or "-"

    def clone(self) -> "Castle":
        return Castle(self.WK, self.WQ, self.BK, self.BQ)

    def as_str(self) -> str:
        return repr(self)


class RepetitionTable:
    def __init__(self):
        self.counts: Dict[str, int] = {}

    def add(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def count(self, key: str) -> int:
        return self.counts.get(key, 0)


class ChessPosition:
    """
    Vollständige Regel-Engine mit legaler Zuggenerierung, Apply/Undo, Status, FEN & UCI.
    """
    __slots__ = ("board", "stm", "castle", "ep_target", "halfmove", "fullmove", "rep", "history")

    def __init__(self):
        self.board = Board(8, 8, ".")
        self.board.reset(_initial_setup(), ".")
        self.stm: str = WHITE
        self.castle = Castle(True, True, True, True)
        self.ep_target: Optional[Coord] = None
        self.halfmove = 0
        self.fullmove = 1
        self.rep = RepetitionTable()
        # History: (Move, captured, old_castle, old_ep, old_half, old_full, ep_capture_square?)
        self.history: List[Tuple[Move, str, Castle, Optional[Coord], int, int, Optional[Coord]]] = []
        self._add_rep()

    # --- Repetition Key (vereinfachtes FEN ohne Zähler) ----------------------
    def _board_fen_only(self) -> str:
        rows = []
        for r in range(8):
            run = 0
            row = ""
            for c in range(8):
                p = self.board.piece_at((r, c))
                if p == ".":
                    run += 1
                else:
                    if run > 0:
                        row += str(run); run = 0
                    row += p
            if run > 0:
                row += str(run)
            rows.append(row)
        return "/".join(rows)

    def _make_key(self) -> str:
        ep = rc_to_sq(self.ep_target) if self.ep_target else "-"
        return f"{self._board_fen_only()} {self.stm} {self.castle.as_str()} {ep}"

    def _add_rep(self) -> None:
        self.rep.add(self._make_key())

    # --- Hilfen / API-Sugar ---------------------------------------------------
    def at(self, rc: Coord) -> str:
        return self.board.piece_at(rc)

    def king_pos(self, color: str) -> Optional[Coord]:
        tgt = "K" if color == WHITE else "k"
        for (rc, p) in self.board:
            if p == tgt:
                return rc
        return None

    def move_to_uci(self, move: Move) -> str:
        return algebraic(move)

    def legal_uci(self) -> Iterable[str]:
        for m in self.generate_legal_moves():
            yield algebraic(m)

    def parse_uci(self, s: str) -> Move:
        return parse_uci(s)

    def as_fen(self) -> str:
        """Vollständige FEN der aktuellen Stellung."""
        board_part = self._board_fen_only()
        side = "w" if self.stm == WHITE else "b"
        castle = self.castle.as_str()
        ep = rc_to_sq(self.ep_target) if self.ep_target else "-"
        return f"{board_part} {side} {castle} {ep} {self.halfmove} {self.fullmove}"

    # --- Checks / Angriffe ----------------------------------------------------
    def is_attacked_by(self, rc: Coord, attacker_color: str) -> bool:
        r, c = rc
        # Bauern
        if attacker_color == WHITE:
            for dc in (-1, +1):
                rr, cc = r + 1, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8 and self.at((rr, cc)) == "P":
                    return True
        else:
            for dc in (-1, +1):
                rr, cc = r - 1, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8 and self.at((rr, cc)) == "p":
                    return True
        # Springer
        for dr, dc in [(+2, +1), (+2, -1), (-2, +1), (-2, -1), (+1, +2), (+1, -2), (-1, +2), (-1, -2)]:
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8:
                p = self.at((rr, cc))
                if attacker_color == WHITE and p == "N":
                    return True
                if attacker_color == BLACK and p == "n":
                    return True
        # Läufer/Dame (Diagonalen)
        for dr, dc in [(-1, -1), (-1, +1), (+1, -1), (+1, +1)]:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                p = self.at((rr, cc))
                if p != ".":
                    if attacker_color == WHITE and p in {"B", "Q"}:
                        return True
                    if attacker_color == BLACK and p in {"b", "q"}:
                        return True
                    break
                rr += dr; cc += dc
        # Turm/Dame (Geraden)
        for dr, dc in [(-1, 0), (+1, 0), (0, -1), (0, +1)]:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                p = self.at((rr, cc))
                if p != ".":
                    if attacker_color == WHITE and p in {"R", "Q"}:
                        return True
                    if attacker_color == BLACK and p in {"r", "q"}:
                        return True
                    break
                rr += dr; cc += dc
        # König Nachbarfelder
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    p = self.at((rr, cc))
                    if attacker_color == WHITE and p == "K":
                        return True
                    if attacker_color == BLACK and p == "k":
                        return True
        return False

    def in_check(self, color: str) -> bool:
        kp = self.king_pos(color)
        if kp is None:
            return False
        return self.is_attacked_by(kp, WHITE if color == BLACK else BLACK)

    # --- Move generation (pseudo-legal) --------------------------------------
    def _gen_pawn(self, rc: Coord, color: str, moves: List[Move]):
        r, c = rc
        # Weiß startet unten (r=6/7) und zieht r-1; Schwarz r+1
        dir = -1 if color == WHITE else +1
        # Vorwärts
        one = (r + dir, c)
        if self.board.inside(one) and self.at(one) == ".":
            moves.append(Move(rc, one))
            # Doppelschritt
            start_row = 6 if color == WHITE else 1
            two = (r + 2 * dir, c)
            if r == start_row and self.at(two) == ".":
                moves.append(Move(rc, two))
        # Schlagen
        for dc in (-1, +1):
            cap = (r + dir, c + dc)
            if self.board.inside(cap):
                tgt = self.at(cap)
                if tgt != "." and _color_of(tgt) != color:
                    moves.append(Move(rc, cap))
        # En passant – Ziel ist freies Feld (ep_target), das im letzten Zug entstand
        if self.ep_target:
            if (r + dir, c - 1) == self.ep_target or (r + dir, c + 1) == self.ep_target:
                moves.append(Move(rc, self.ep_target))

    def _gen_knight(self, rc: Coord, color: str, moves: List[Move]):
        r, c = rc
        for dr, dc in [(+2, +1), (+2, -1), (-2, +1), (-2, -1), (+1, +2), (+1, -2), (-1, +2), (-1, -2)]:
            to = (r + dr, c + dc)
            if not self.board.inside(to):
                continue
            p = self.at(to)
            if p == "." or _color_of(p) != color:
                moves.append(Move(rc, to))

    def _gen_sliders(self, rc: Coord, color: str, moves: List[Move], directions: List[Tuple[int, int]]):
        r, c = rc
        for dr, dc in directions:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                p = self.at((rr, cc))
                if p == ".":
                    moves.append(Move(rc, (rr, cc)))
                else:
                    if _color_of(p) != color:
                        moves.append(Move(rc, (rr, cc)))
                    break
                rr += dr; cc += dc

    def _gen_king(self, rc: Coord, color: str, moves: List[Move], include_castle: bool = True):
        r, c = rc
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                to = (r + dr, c + dc)
                if not self.board.inside(to):
                    continue
                p = self.at(to)
                if p == "." or _color_of(p) != color:
                    moves.append(Move(rc, to))
        # Rochade
        if not include_castle or self.in_check(color):
            return
        if color == WHITE:
            # Kurz: e1g1
            if self.castle.WK and self.at((7, 5)) == "." and self.at((7, 6)) == ".":
                if not self.is_attacked_by((7, 5), BLACK) and not self.is_attacked_by((7, 6), BLACK) and self.at((7, 7)) == "R":
                    moves.append(Move((7, 4), (7, 6)))
            # Lang: e1c1 (b1,c1,d1 leer)
            if self.castle.WQ and self.at((7, 3)) == "." and self.at((7, 2)) == "." and self.at((7, 1)) == ".":
                if not self.is_attacked_by((7, 3), BLACK) and not self.is_attacked_by((7, 2), BLACK) and self.at((7, 0)) == "R":
                    moves.append(Move((7, 4), (7, 2)))
        else:
            # Kurz: e8g8
            if self.castle.BK and self.at((0, 5)) == "." and self.at((0, 6)) == ".":
                if not self.is_attacked_by((0, 5), WHITE) and not self.is_attacked_by((0, 6), WHITE) and self.at((0, 7)) == "r":
                    moves.append(Move((0, 4), (0, 6)))
            # Lang: e8c8
            if self.castle.BQ and self.at((0, 3)) == "." and self.at((0, 2)) == "." and self.at((0, 1)) == ".":
                if not self.is_attacked_by((0, 3), WHITE) and not self.is_attacked_by((0, 2), WHITE) and self.at((0, 0)) == "r":
                    moves.append(Move((0, 4), (0, 2)))

    def generate_pseudo_legal(self) -> List[Move]:
        color = self.stm
        moves: List[Move] = []
        for (rc, p) in self.board:
            if p == "." or _color_of(p) != color:
                continue
            u = p.upper()
            if u == "P":
                self._gen_pawn(rc, color, moves)
            elif u == "N":
                self._gen_knight(rc, color, moves)
            elif u == "B":
                self._gen_sliders(rc, color, moves, [(-1, -1), (-1, +1), (+1, -1), (+1, +1)])
            elif u == "R":
                self._gen_sliders(rc, color, moves, [(-1, 0), (+1, 0), (0, -1), (0, +1)])
            elif u == "Q":
                self._gen_sliders(rc, color, moves, [(-1, -1), (-1, +1), (+1, -1), (+1, +1), (-1, 0), (+1, 0), (0, -1), (0, +1)])
            elif u == "K":
                self._gen_king(rc, color, moves, include_castle=True)
        return moves

    # --- Apply/Undo (inkl. EP/Rochade) ---------------------------------------
    def _apply_no_check(self, m: Move) -> Tuple[str, Optional[str], Optional[Coord], Castle, int, int, Optional[Coord], Optional[Coord]]:
        """
        Führt m ohne Schachprüfung aus.
        Rückgabe:
          (captured, promo_out, old_ep, old_castle, old_half, old_full, rook_from, ep_capture_square)
        Hinweis: rook_to ergibt sich deterministisch aus m; für Undo reicht rook_from.
        """
        captured = self.board.piece_at(m.to)
        piece = self.board.piece_at(m.frm)
        old_ep = self.ep_target
        old_castle = self.castle.clone()
        old_half = self.halfmove
        old_full = self.fullmove
        rook_from = None
        ep_cap_square = None
        promo_out = None

        # en passant Capture: Zielfeld ist ep_target & leer → Bauer hinter Ziel schlagen
        if piece.upper() == "P" and self.ep_target and m.to == self.ep_target and captured == ".":
            dir = -1 if piece.isupper() else +1
            cap_rc = (m.to[0] - dir, m.to[1])
            captured = self.board.piece_at(cap_rc)
            self.board.set_piece(cap_rc, ".")
            ep_cap_square = cap_rc  # für Undo merken

        self.board.move(m.frm, m.to)

        # Rochade: König zieht → Turm umsetzen
        if piece.upper() == "K":
            if piece.isupper():
                self.castle.WK = False; self.castle.WQ = False
                if m.frm == (7, 4) and m.to == (7, 6):  # kurz
                    rook_from = (7, 7)
                    self.board.move((7, 7), (7, 5))
                elif m.frm == (7, 4) and m.to == (7, 2):  # lang
                    rook_from = (7, 0)
                    self.board.move((7, 0), (7, 3))
            else:
                self.castle.BK = False; self.castle.BQ = False
                if m.frm == (0, 4) and m.to == (0, 6):
                    rook_from = (0, 7)
                    self.board.move((0, 7), (0, 5))
                elif m.frm == (0, 4) and m.to == (0, 2):
                    rook_from = (0, 0)
                    self.board.move((0, 0), (0, 3))

        # Turm bewegt → Rechte anpassen
        if piece.upper() == "R":
            if piece.isupper():
                if m.frm == (7, 0): self.castle.WQ = False
                if m.frm == (7, 7): self.castle.WK = False
            else:
                if m.frm == (0, 0): self.castle.BQ = False
                if m.frm == (0, 7): self.castle.BK = False

        # Turm geschlagen → gegnerische Rechte anpassen
        if captured.upper() == "R":
            if m.to == (7, 0): self.castle.WQ = False
            if m.to == (7, 7): self.castle.WK = False
            if m.to == (0, 0): self.castle.BQ = False
            if m.to == (0, 7): self.castle.BK = False

        # en passant Ziel aktualisieren (nur nach Doppelzug Bauer)
        self.ep_target = None
        if piece.upper() == "P":
            start_row = 6 if piece.isupper() else 1
            dir = -1 if piece.isupper() else +1
            if m.frm[0] == start_row and m.to[0] == start_row + 2 * dir:
                self.ep_target = (start_row + dir, m.frm[1])

        # Promotion
        if piece.upper() == "P":
            if (piece.isupper() and m.to[0] == 0) or (piece.islower() and m.to[0] == 7):
                promo_piece = (m.promo or "q")
                promo_ch = promo_piece.upper() if piece.isupper() else promo_piece.lower()
                self.board.set_piece(m.to, promo_ch)
                promo_out = promo_ch

        # Halbzug-/Vollzugzähler
        if piece.upper() == "P" or captured != ".":
            self.halfmove = 0
        else:
            self.halfmove += 1
        if self.stm == BLACK:
            self.fullmove += 1

        # Seite wechseln
        self.stm = WHITE if self.stm == BLACK else BLACK

        return captured, promo_out, old_ep, old_castle, old_half, old_full, rook_from, ep_cap_square

    def _undo_no_check(
        self, m: Move, captured: str, promo_out: Optional[str],
        old_ep: Optional[Coord], old_castle: Castle, old_half: int, old_full: int,
        rook_from: Optional[Coord], ep_cap_square: Optional[Coord]
    ):
        # Seite zurück
        self.stm = WHITE if self.stm == BLACK else BLACK
        # Zähler zurück
        self.halfmove = old_half
        self.fullmove = old_full
        # Rochadenturm zurück
        if rook_from:
            # Ziel ergibt sich deterministisch aus m
            if rook_from == (7, 7):  # W kurz
                self.board.move((7, 5), (7, 7))
            elif rook_from == (7, 0):  # W lang
                self.board.move((7, 3), (7, 0))
            elif rook_from == (0, 7):  # B kurz
                self.board.move((0, 5), (0, 7))
            elif rook_from == (0, 0):  # B lang
                self.board.move((0, 3), (0, 0))
        # Promotion rückgängig
        if promo_out:
            base = "P" if promo_out.isupper() else "p"
            self.board.set_piece(m.to, base)
        # Zug rückgängig
        self.board.move(m.to, m.frm)
        # EP-Capture rückgängig (falls vorhanden)
        if ep_cap_square is not None:
            self.board.set_piece(ep_cap_square, captured)
        else:
            self.board.set_piece(m.to, captured)
        # EP/Schlossrechte zurück
        self.ep_target = old_ep
        self.castle = old_castle

    # --- Legale Züge (mit Schachprüfung) -------------------------------------
    def generate_legal_moves(self) -> List[Move]:
        legal: List[Move] = []
        for m in self.generate_pseudo_legal():
            caps, promo, old_ep, old_castle, old_h, old_f, rf, ep_cap = self._apply_no_check(m)
            in_chk = self.in_check(WHITE if self.stm == BLACK else BLACK)
            self._undo_no_check(m, caps, promo, old_ep, old_castle, old_h, old_f, rf, ep_cap)
            if not in_chk:
                # Promotion defaultieren
                piece = self.at(m.frm)
                if piece.upper() == "P":
                    rto = m.to[0]
                    if (piece.isupper() and rto == 0) or (piece.islower() and rto == 7):
                        if m.promo is None:
                            m = Move(m.frm, m.to, "q")
                legal.append(m)
        return legal

    # --- Status ---------------------------------------------------------------
    def status(self) -> str:
        """ 'ongoing' | 'checkmate_white'/'checkmate_black' | 'stalemate' | 'fifty_moves' | 'threefold' """
        if self.halfmove >= 100:
            return "fifty_moves"
        if self.rep.count(self._make_key()) >= 3:
            return "threefold"
        moves = self.generate_legal_moves()
        if moves:
            return "ongoing"
        # keine legalen Züge:
        if self.in_check(self.stm):
            # Seite am Zug ist mattgesetzt → Gegner gewinnt
            return "checkmate_black" if self.stm == BLACK else "checkmate_white"
        return "stalemate"

    # --- Öffentliches Apply/Undo (mit Legalitätscheck + History + Repetition) -
    def apply(self, m: Move) -> bool:
        legal = self.generate_legal_moves()
        if m not in legal:
            if m.promo:
                norm = Move(m.frm, m.to, m.promo.lower())
                if norm not in legal:
                    return False
                m = norm
            else:
                return False
        caps, promo, old_ep, old_castle, old_h, old_f, rf, ep_cap = self._apply_no_check(m)
        self.history.append((m, caps, old_castle, old_ep, old_h, old_f, ep_cap))
        self._add_rep()
        return True

    def undo(self) -> bool:
        if not self.history:
            return False
        m, captured, old_castle, old_ep, old_h, old_f, ep_cap = self.history.pop()
        rook_from = None
        piece = self.board.piece_at(m.to)
        # Für Undo der Rochade den Turmweg erkennen
        if piece.upper() == "K":
            if piece.isupper():
                if m.frm == (7, 4) and m.to == (7, 6): rook_from = (7, 7)
                if m.frm == (7, 4) and m.to == (7, 2): rook_from = (7, 0)
            else:
                if m.frm == (0, 4) and m.to == (0, 6): rook_from = (0, 7)
                if m.frm == (0, 4) and m.to == (0, 2): rook_from = (0, 0)
        self._undo_no_check(m, captured, None, old_ep, old_castle, old_h, old_f, rook_from, ep_cap)
        return True
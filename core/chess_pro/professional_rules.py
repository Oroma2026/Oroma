#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/professional_rules.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro ProfessionalRuleBook
# Version: v0.1.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Dieses Modul bildet bekannte professionelle Schachprinzipien als messbare,
#   gewichtete Features ab. Es ist absichtlich KEIN starres Regelwerk nach dem
#   Muster "wenn X, dann spiele Y". Schachregeln professioneller Spieler sind
#   kontextabhängige Bewertungsbeiträge:
#
#     Stellung → erkannte Motive/Prinzipien → Bonus/Malus → Suche → Entscheidung
#
#   Damit kann ORÓMA später über Partieverlauf, Replay, NMR und Dream lernen,
#   wann ein Prinzip hilfreich, neutral oder schädlich war.
#
# IMPLEMENTIERTE REGELFAMILIEN
# ────────────────────────────
#   • Material-nahe Strukturmerkmale werden NICHT hier bewertet, sondern im
#     Evaluator separat addiert. Dieses Modul bewertet positionsstrategische
#     Prinzipien.
#   • Eröffnung/Entwicklung: Zentrum, Leichtfigurenentwicklung, frühe Dame,
#     Rochade, Tempoverlust durch mehrfach bewegte Figuren.
#   • Mittelspiel: Mobilisierbare Figuren, Türme auf offenen/halboffenen Linien,
#     Läuferpaar, Springer am Rand, aktive Dame, Königssicherheit.
#   • Bauernstruktur: Doppelbauern, isolierte Bauern, Freibauern, Zentrumspawns.
#   • Taktische Sicherheit: hängende/unterverteidigte Figuren, angegriffener
#     König, bedrohte Dame, Schachgebote.
#   • Endspiel: König aktivieren, Freibauerngewicht erhöhen.
#
# PRODUKTIONSINVARIANTEN
# ──────────────────────
#   • Headless, stdlib-only plus vorhandene ORÓMA-Schachregeln.
#   • Keine DB-Zugriffe, keine Seiteneffekte, keine Mutation der Position.
#   • Fehlerrobust: bei unerwartetem Board-Zustand konservativ 0 statt Crash.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.chess_pro.rules import WHITE, BLACK, PIECE_VALUES, ChessPosition, rc_to_sq

Coord = Tuple[int, int]

_CENTER = {(3, 3), (3, 4), (4, 3), (4, 4)}          # d5/e5/d4/e4
_EXT_CENTER = {
    (2, 2), (2, 3), (2, 4), (2, 5),
    (3, 2), (3, 3), (3, 4), (3, 5),
    (4, 2), (4, 3), (4, 4), (4, 5),
    (5, 2), (5, 3), (5, 4), (5, 5),
}
_KING_HOME = {WHITE: (7, 4), BLACK: (0, 4)}
_QUEEN_HOME = {WHITE: (7, 3), BLACK: (0, 3)}
_MINOR_HOME = {WHITE: {(7, 1), (7, 2), (7, 5), (7, 6)}, BLACK: {(0, 1), (0, 2), (0, 5), (0, 6)}}
_CASTLED_KING_SQS = {WHITE: {(7, 6), (7, 2)}, BLACK: {(0, 6), (0, 2)}}


@dataclass(frozen=True)
class RuleHit:
    """Ein einzelner erkannter Regelbeitrag.

    `score_cp` ist immer aus Sicht der bewerteten Seite positiv, wenn das
    Merkmal gut ist, und negativ, wenn es schadet. Der Evaluator subtrahiert die
    Beiträge der Gegenseite entsprechend.
    """

    name: str
    score_cp: int
    detail: str
    weight: float = 1.0


def other(color: str) -> str:
    return BLACK if color == WHITE else WHITE


def color_of(piece: str) -> Optional[str]:
    if not piece or piece == ".":
        return None
    return WHITE if piece.isupper() else BLACK


def _piece_upper(piece: str) -> str:
    return (piece or ".").upper()


def _iter_pieces(pos: ChessPosition, color: Optional[str] = None) -> Iterable[Tuple[Coord, str]]:
    for rc, p in pos.board:
        if not p or p == ".":
            continue
        if color is None or color_of(str(p)) == color:
            yield rc, str(p)


class ProfessionalRuleBook:
    """Bewertet professionelle Schachprinzipien als weiche Features.

    Die Gewichtung ist absichtlich konservativ. Der Suchbaum soll taktische
    Widerlegungen erkennen können; die Regelwerte sollen nur gute Kandidaten und
    langfristige Strukturen bevorzugen.
    """

    def __init__(self) -> None:
        self.piece_values = dict(PIECE_VALUES)

    # ------------------------------------------------------------------
    # Angriffskarten / Verteidigung
    # ------------------------------------------------------------------
    def attacks_from(self, pos: ChessPosition, rc: Coord, piece: str) -> List[Coord]:
        """Pseudo-Attack-Squares einer Figur ohne eigene Königssicherheit.

        Für Bewertungszwecke reicht diese Attackmap. Sie ist nicht als Legal-
        Move-Generator gedacht; legale Züge kommen weiterhin aus ChessPosition.
        """
        out: List[Coord] = []
        p = str(piece or ".")
        if p == ".":
            return out
        color = color_of(p)
        if color is None:
            return out
        r, c = rc
        u = p.upper()
        if u == "P":
            dr = -1 if color == WHITE else 1
            for dc in (-1, 1):
                sq = (r + dr, c + dc)
                if pos.board.inside(sq):
                    out.append(sq)
            return out
        if u == "N":
            for dr, dc in ((2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2)):
                sq = (r + dr, c + dc)
                if pos.board.inside(sq):
                    out.append(sq)
            return out
        if u == "K":
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    sq = (r + dr, c + dc)
                    if pos.board.inside(sq):
                        out.append(sq)
            return out
        dirs: List[Tuple[int, int]] = []
        if u in {"B", "Q"}:
            dirs.extend([(-1, -1), (-1, 1), (1, -1), (1, 1)])
        if u in {"R", "Q"}:
            dirs.extend([(-1, 0), (1, 0), (0, -1), (0, 1)])
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                out.append((rr, cc))
                if pos.at((rr, cc)) != ".":
                    break
                rr += dr
                cc += dc
        return out

    def attack_counts(self, pos: ChessPosition) -> Tuple[Dict[Coord, int], Dict[Coord, int]]:
        white: Dict[Coord, int] = {}
        black: Dict[Coord, int] = {}
        for rc, p in _iter_pieces(pos):
            target = white if color_of(p) == WHITE else black
            for sq in self.attacks_from(pos, rc, p):
                target[sq] = int(target.get(sq, 0)) + 1
        return white, black

    # ------------------------------------------------------------------
    # Phasen / Struktur
    # ------------------------------------------------------------------
    def game_phase(self, pos: ChessPosition) -> str:
        """opening|middlegame|endgame anhand Material und Zugnummer."""
        try:
            non_pawn = 0
            queens = 0
            for _rc, p in _iter_pieces(pos):
                u = p.upper()
                if u == "Q":
                    queens += 1
                if u in {"N", "B", "R", "Q"}:
                    non_pawn += int(self.piece_values.get(p, 0))
            if int(getattr(pos, "fullmove", 1) or 1) <= 10 and queens >= 2 and non_pawn >= 5000:
                return "opening"
            if queens == 0 and non_pawn <= 2600:
                return "endgame"
            if non_pawn <= 1800:
                return "endgame"
            return "middlegame"
        except Exception:
            return "middlegame"

    def _file_pawns(self, pos: ChessPosition, color: str) -> Dict[int, List[Coord]]:
        out: Dict[int, List[Coord]] = {i: [] for i in range(8)}
        pawn = "P" if color == WHITE else "p"
        for rc, p in _iter_pieces(pos, color):
            if p == pawn:
                out[rc[1]].append(rc)
        return out

    def _is_passed_pawn(self, pos: ChessPosition, rc: Coord, color: str) -> bool:
        r, c = rc
        enemy_pawn = "p" if color == WHITE else "P"
        files = [cc for cc in (c - 1, c, c + 1) if 0 <= cc < 8]
        if color == WHITE:
            ranks = range(r - 1, -1, -1)
        else:
            ranks = range(r + 1, 8)
        for rr in ranks:
            for cc in files:
                if pos.at((rr, cc)) == enemy_pawn:
                    return False
        return True

    def _is_open_file(self, pos: ChessPosition, file_idx: int) -> bool:
        return all(pos.at((r, file_idx)).upper() != "P" for r in range(8))

    def _is_half_open_file(self, pos: ChessPosition, file_idx: int, color: str) -> bool:
        own_pawn = "P" if color == WHITE else "p"
        return all(pos.at((r, file_idx)) != own_pawn for r in range(8))

    # ------------------------------------------------------------------
    # Hauptbewertung
    # ------------------------------------------------------------------
    def evaluate_side(self, pos: ChessPosition, color: str) -> Tuple[int, List[RuleHit], Dict[str, float]]:
        hits: List[RuleHit] = []
        metrics: Dict[str, float] = {}
        phase = self.game_phase(pos)
        enemy = other(color)
        white_att, black_att = self.attack_counts(pos)
        own_att = white_att if color == WHITE else black_att
        enemy_att = black_att if color == WHITE else white_att

        def add(name: str, cp: int, detail: str, weight: float = 1.0) -> None:
            if cp:
                hits.append(RuleHit(name=name, score_cp=int(round(cp * weight)), detail=detail, weight=float(weight)))

        # Zentrum und Entwicklung
        center_pawns = 0
        ext_center_control = 0
        developed_minors = 0
        undeveloped_minors = 0
        bishops = 0
        rooks_open = 0
        rooks_halfopen = 0
        knights_edge = 0
        queen_home = False
        castled = False
        king_rc: Optional[Coord] = None

        for rc, p in _iter_pieces(pos, color):
            u = p.upper()
            if u == "P" and rc in _CENTER:
                center_pawns += 1
            if u in {"N", "B"}:
                if rc in _MINOR_HOME[color]:
                    undeveloped_minors += 1
                else:
                    developed_minors += 1
            if u == "B":
                bishops += 1
            if u == "N" and rc[1] in (0, 7):
                knights_edge += 1
            if u == "Q" and rc == _QUEEN_HOME[color]:
                queen_home = True
            if u == "K":
                king_rc = rc
                castled = rc in _CASTLED_KING_SQS[color]
            if u == "R":
                if self._is_open_file(pos, rc[1]):
                    rooks_open += 1
                elif self._is_half_open_file(pos, rc[1], color):
                    rooks_halfopen += 1
            if rc in _EXT_CENTER:
                ext_center_control += int(own_att.get(rc, 0))

        metrics["developed_minors"] = float(developed_minors)
        metrics["undeveloped_minors"] = float(undeveloped_minors)
        metrics["center_pawns"] = float(center_pawns)
        metrics["phase_opening"] = 1.0 if phase == "opening" else 0.0
        metrics["phase_middlegame"] = 1.0 if phase == "middlegame" else 0.0
        metrics["phase_endgame"] = 1.0 if phase == "endgame" else 0.0

        if phase == "opening":
            add("opening_center_pawns", 18 * center_pawns, f"central pawns={center_pawns}")
            add("opening_minor_development", 16 * developed_minors - 12 * undeveloped_minors, f"developed={developed_minors} undeveloped={undeveloped_minors}")
            if not queen_home and undeveloped_minors >= 2:
                add("opening_early_queen", -28, "queen developed before minor pieces")
            if castled:
                add("opening_castled_king", 35, "king castled")
            elif int(getattr(pos, "fullmove", 1) or 1) >= 8:
                add("opening_delayed_castle", -26, "king still central after move 8")
        else:
            add("center_control", min(40, 3 * ext_center_control), f"extended-center attack count={ext_center_control}")
            if castled:
                add("king_safety_castled", 20, "king castled")

        # Königssicherheit
        if king_rc is not None:
            danger = int(enemy_att.get(king_rc, 0))
            shield = 0
            kr, kc = king_rc
            pawn = "P" if color == WHITE else "p"
            shield_rank = kr - 1 if color == WHITE else kr + 1
            if 0 <= shield_rank < 8:
                for cc in (kc - 1, kc, kc + 1):
                    if 0 <= cc < 8 and pos.at((shield_rank, cc)) == pawn:
                        shield += 1
            metrics["king_attackers"] = float(danger)
            metrics["king_pawn_shield"] = float(shield)
            if phase != "endgame":
                add("king_zone_attackers", -22 * danger, f"attackers on king square={danger}")
                add("king_pawn_shield", 9 * shield - 18, f"shield pawns={shield}")
            else:
                # Im Endspiel ist ein aktiver König ein Vorteil.
                center_dist = abs(king_rc[0] - 3.5) + abs(king_rc[1] - 3.5)
                add("endgame_active_king", int(round(28 - 7 * center_dist)), f"king center distance={center_dist:.1f}")

        # Figurenaktivität
        if bishops >= 2:
            add("bishop_pair", 34, "two bishops")
        if rooks_open:
            add("rook_open_file", 24 * rooks_open, f"rooks on open files={rooks_open}")
        if rooks_halfopen:
            add("rook_half_open_file", 13 * rooks_halfopen, f"rooks on half-open files={rooks_halfopen}")
        if knights_edge:
            add("knight_on_rim", -22 * knights_edge, f"knights on a/h-file={knights_edge}")

        # Bauernstruktur
        files = self._file_pawns(pos, color)
        doubled = sum(max(0, len(v) - 1) for v in files.values())
        isolated = 0
        passed = 0
        for file_idx, pawns in files.items():
            if not pawns:
                continue
            has_left = file_idx > 0 and bool(files.get(file_idx - 1))
            has_right = file_idx < 7 and bool(files.get(file_idx + 1))
            if not has_left and not has_right:
                isolated += len(pawns)
            for pawn_rc in pawns:
                if self._is_passed_pawn(pos, pawn_rc, color):
                    passed += 1
        metrics["doubled_pawns"] = float(doubled)
        metrics["isolated_pawns"] = float(isolated)
        metrics["passed_pawns"] = float(passed)
        add("doubled_pawns", -12 * doubled, f"doubled={doubled}")
        add("isolated_pawns", -10 * isolated, f"isolated={isolated}")
        add("passed_pawns", (28 if phase == "endgame" else 18) * passed, f"passed={passed}")

        # Hängende / unterverteidigte Figuren
        hanging_penalty = 0
        underdefended_penalty = 0
        queen_threat = 0
        for rc, p in _iter_pieces(pos, color):
            u = p.upper()
            if u == "K":
                continue
            attackers = int(enemy_att.get(rc, 0))
            defenders = int(own_att.get(rc, 0))
            value = int(self.piece_values.get(p, 0))
            if attackers > 0 and defenders == 0:
                hanging_penalty += min(90, max(12, value // 10))
                if u == "Q":
                    queen_threat += 1
            elif attackers > defenders:
                underdefended_penalty += min(45, max(8, value // 18))
        metrics["hanging_piece_penalty"] = float(hanging_penalty)
        metrics["underdefended_piece_penalty"] = float(underdefended_penalty)
        if hanging_penalty:
            add("hanging_pieces", -hanging_penalty, f"penalty={hanging_penalty}")
        if underdefended_penalty:
            add("underdefended_pieces", -underdefended_penalty, f"penalty={underdefended_penalty}")
        if queen_threat:
            add("queen_under_attack", -45 * queen_threat, f"queen threat={queen_threat}")

        # Schach/Initiative
        try:
            if pos.in_check(enemy):
                add("gives_check_pressure", 32, "enemy king currently in check")
        except Exception:
            pass

        total = sum(int(h.score_cp) for h in hits)
        metrics["rule_score_cp"] = float(total)
        metrics["rule_hits"] = float(len(hits))
        return total, hits, metrics

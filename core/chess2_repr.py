#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess2_repr.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess2 Repräsentationskern – mobility-native Zustände für game:chess2
# Version: v3.8-r2
# Stand:   2026-03-12
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Einheitlicher Repräsentationskern für Chess2.
#
# WICHTIG:
# Dieses Modul ist absichtlich OHNE python-chess implementiert, damit Chess2
# im bestehenden ORÓMA-Headless-Stack ohne zusätzliche Fremdpakete lauffähig
# bleibt. Für Partielogik / legale Züge kann das System weiter die vorhandene
# Mini-Engine (`mini_programs.chess.chess_game.ChessGame`) nutzen; für die
# Repräsentation genügt eine robuste FEN-/Brettableitung.
#
# DARSTELLUNG
# -----------
# mobility_vec64_from_fen(fen) erzeugt pro Feld einen additiven Wert aus:
#   • normiertem Figurwert auf dem Ursprungsfeld
#   • Einfluss/Mobilität auf erreichbare Zielfelder
#
# Sliding-Figuren:
#   • freies Feld       ±0.125
#   • erstes Schlagfeld ±0.0625
#   • dahinter 0
#
# Springer/König:
#   • erreichbares freies Feld       ±0.125
#   • erreichbares feindliches Feld  ±0.0625
#
# Bauern:
#   • kontrollierte Diagonalen       ±0.125
#
# HASHING / GENERALISIERUNG
# -------------------------
# Aus dem quantisierten Mobility-Vektor wird zusammen mit groben Strukturmerkmalen
# ein deterministischer State-Hash gebildet. Damit werden ähnliche Stellungen
# gröber zusammengeführt als beim exakten FEN-Key.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Dict, Iterable, List, Optional, Tuple

_FREE_INFL = 0.125
_CAPTURE_INFL = 0.0625
_MOBILITY_BUCKET = 0.125

_PVAL_NORM: Dict[str, float] = {
    "P": 1.0 / 9.0,
    "N": 3.0 / 9.0,
    "B": 3.0 / 9.0,
    "R": 5.0 / 9.0,
    "Q": 1.0,
    "K": 6.0 / 9.0,
}

_FILES = "abcdefgh"
_RANKS = "12345678"


@dataclass(frozen=True)
class Chess2StateSummary:
    turn: str
    material_bucket: str
    phase: str
    castling: str
    in_check: bool
    pieces: int


@dataclass(frozen=True)
class ParsedFen:
    board: Dict[int, str]
    turn: str
    castling: str
    ep: str
    halfmove: int
    fullmove: int


def sq_index(file_idx: int, rank_idx: int) -> int:
    return rank_idx * 8 + file_idx


def sq_to_rc(sq: int) -> Tuple[int, int]:
    return sq // 8, sq % 8


def square_name(sq: int) -> str:
    r, c = sq_to_rc(sq)
    return f"{_FILES[c]}{_RANKS[r]}"


def parse_square(name: str) -> Optional[int]:
    s = (name or "").strip()
    if len(s) != 2 or s[0] not in _FILES or s[1] not in _RANKS:
        return None
    return sq_index(_FILES.index(s[0]), _RANKS.index(s[1]))


def parse_fen(fen: str) -> ParsedFen:
    parts = (fen or "").strip().split()
    if len(parts) < 2:
        raise ValueError(f"invalid fen: {fen!r}")
    board_part = parts[0]
    turn = parts[1]
    castling = parts[2] if len(parts) >= 3 else "-"
    ep = parts[3] if len(parts) >= 4 else "-"
    halfmove = int(parts[4]) if len(parts) >= 5 and str(parts[4]).isdigit() else 0
    fullmove = int(parts[5]) if len(parts) >= 6 and str(parts[5]).isdigit() else 1
    board: Dict[int, str] = {}
    rows = board_part.split("/")
    if len(rows) != 8:
        raise ValueError(f"invalid fen rows: {fen!r}")
    for fen_row_idx, row in enumerate(rows):
        rank_idx = 7 - fen_row_idx
        file_idx = 0
        for ch in row:
            if ch.isdigit():
                file_idx += int(ch)
                continue
            if 0 <= file_idx <= 7:
                board[sq_index(file_idx, rank_idx)] = ch
            file_idx += 1
    return ParsedFen(board=board, turn=turn, castling=castling, ep=ep, halfmove=halfmove, fullmove=fullmove)


def _flip_piece(piece: str) -> str:
    return str(piece or '').swapcase()


def flip_square_name(name: str) -> str:
    s = (name or '').strip()
    if len(s) != 2 or s[0] not in _FILES or s[1] not in _RANKS:
        return s
    return f"{_FILES[7 - _FILES.index(s[0])]}{_RANKS[7 - _RANKS.index(s[1])]}"


def flip_uci(uci: str) -> str:
    s = (uci or '').strip()
    if len(s) < 4:
        return s
    base = flip_square_name(s[:2]) + flip_square_name(s[2:4])
    return base + (s[4:] if len(s) > 4 else '')


def flip_castling(castling: str) -> str:
    out = []
    for ch in str(castling or '-').strip():
        if ch == 'K':
            out.append('k')
        elif ch == 'Q':
            out.append('q')
        elif ch == 'k':
            out.append('K')
        elif ch == 'q':
            out.append('Q')
    return ''.join(out) or '-'


def board_to_fen_rows(board: Dict[int, str]) -> List[str]:
    rows: List[str] = []
    for row in board_to_rows(board):
        out: List[str] = []
        empties = 0
        for ch in row:
            if ch == '.':
                empties += 1
            else:
                if empties:
                    out.append(str(empties))
                    empties = 0
                out.append(ch)
        if empties:
            out.append(str(empties))
        rows.append(''.join(out) or '8')
    return rows


def flip_board(board: Dict[int, str]) -> Dict[int, str]:
    return {63 - int(sq): _flip_piece(piece) for sq, piece in board.items()}


def flip_fen(fen: str) -> str:
    p = parse_fen(fen)
    board_flip = flip_board(p.board)
    rows = board_to_fen_rows(board_flip)
    ep = '-' if p.ep == '-' else flip_square_name(str(p.ep))
    turn = 'w' if p.turn == 'b' else 'b'
    return f"{'/'.join(rows)} {turn} {flip_castling(p.castling)} {ep} {int(p.halfmove)} {int(p.fullmove)}"


def canonical_flip_needed(fen: str) -> bool:
    try:
        return parse_fen(fen).turn == 'b'
    except Exception:
        return False


def canonicalize_fen(fen: str) -> Tuple[str, bool]:
    flip = canonical_flip_needed(fen)
    return (flip_fen(fen), True) if flip else (fen, False)


def canonicalize_uci(uci: str, flipped: bool) -> str:
    return flip_uci(uci) if flipped else str(uci or '')


def decanonicalize_uci(uci: str, flipped: bool) -> str:
    return flip_uci(uci) if flipped else str(uci or '')


def board_to_rows(board: Dict[int, str]) -> List[str]:
    rows: List[str] = []
    for rank_idx in range(7, -1, -1):
        row = []
        for file_idx in range(8):
            row.append(board.get(sq_index(file_idx, rank_idx), "."))
        rows.append("".join(row))
    return rows


def _piece_color(piece: str) -> int:
    return 1 if piece.isupper() else -1


def _bucket_token(value: float, bucket: float = _MOBILITY_BUCKET) -> str:
    q = int(round(float(value) / float(bucket))) if bucket > 0.0 else int(round(float(value) * 1000.0))
    return str(q)


def _inside(r: int, c: int) -> bool:
    return 0 <= r <= 7 and 0 <= c <= 7


def _sliding_influence(board: Dict[int, str], sq: int, sign: float, vec: List[float], dirs: Iterable[Tuple[int, int]]) -> None:
    r0, c0 = sq_to_rc(sq)
    own_is_white = sign > 0.0
    for dr, dc in dirs:
        step = 1
        while True:
            nr, nc = r0 + dr * step, c0 + dc * step
            if not _inside(nr, nc):
                break
            tsq = sq_index(nc, nr)
            target = board.get(tsq)
            if target is None:
                vec[tsq] += sign * _FREE_INFL
                step += 1
                continue
            if target.isupper() != own_is_white:
                vec[tsq] += sign * _CAPTURE_INFL
            break


def _jumper_influence(board: Dict[int, str], sq: int, sign: float, vec: List[float], offsets: Iterable[Tuple[int, int]]) -> None:
    r0, c0 = sq_to_rc(sq)
    own_is_white = sign > 0.0
    for dr, dc in offsets:
        nr, nc = r0 + dr, c0 + dc
        if not _inside(nr, nc):
            continue
        tsq = sq_index(nc, nr)
        target = board.get(tsq)
        if target is None:
            vec[tsq] += sign * _FREE_INFL
        elif target.isupper() != own_is_white:
            vec[tsq] += sign * _CAPTURE_INFL


def _pawn_influence(sq: int, sign: float, vec: List[float]) -> None:
    r0, c0 = sq_to_rc(sq)
    dr = 1 if sign > 0.0 else -1
    for dc in (-1, 1):
        nr, nc = r0 + dr, c0 + dc
        if _inside(nr, nc):
            vec[sq_index(nc, nr)] += sign * _FREE_INFL


def mobility_vec64_from_board(board: Dict[int, str]) -> List[float]:
    vec = [0.0] * 64
    for sq, piece in board.items():
        up = piece.upper()
        sign = float(_piece_color(piece))
        vec[sq] += sign * _PVAL_NORM.get(up, 0.0)
        if up == "R":
            _sliding_influence(board, sq, sign, vec, ((1, 0), (-1, 0), (0, 1), (0, -1)))
        elif up == "B":
            _sliding_influence(board, sq, sign, vec, ((1, 1), (1, -1), (-1, 1), (-1, -1)))
        elif up == "Q":
            _sliding_influence(board, sq, sign, vec, ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)))
        elif up == "N":
            _jumper_influence(board, sq, sign, vec, ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)))
        elif up == "K":
            _jumper_influence(board, sq, sign, vec, ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)))
        elif up == "P":
            _pawn_influence(sq, sign, vec)
    return vec


def mobility_vec64_from_fen(fen: str) -> List[float]:
    return mobility_vec64_from_board(parse_fen(fen).board)


def _sliding_attack_counts(board: Dict[int, str], sq: int, counts: List[int], dirs: Iterable[Tuple[int, int]]) -> None:
    r0, c0 = sq_to_rc(sq)
    for dr, dc in dirs:
        step = 1
        while True:
            nr, nc = r0 + dr * step, c0 + dc * step
            if not _inside(nr, nc):
                break
            tsq = sq_index(nc, nr)
            counts[tsq] += 1
            if board.get(tsq) is not None:
                break
            step += 1


def _jumper_attack_counts(sq: int, counts: List[int], offsets: Iterable[Tuple[int, int]]) -> None:
    r0, c0 = sq_to_rc(sq)
    for dr, dc in offsets:
        nr, nc = r0 + dr, c0 + dc
        if _inside(nr, nc):
            counts[sq_index(nc, nr)] += 1


def _pawn_attack_counts(sq: int, sign: float, counts: List[int]) -> None:
    r0, c0 = sq_to_rc(sq)
    dr = 1 if sign > 0.0 else -1
    for dc in (-1, 1):
        nr, nc = r0 + dr, c0 + dc
        if _inside(nr, nc):
            counts[sq_index(nc, nr)] += 1


def attack_count_maps_from_board(board: Dict[int, str]) -> Tuple[List[int], List[int]]:
    white = [0] * 64
    black = [0] * 64
    for sq, piece in board.items():
        up = piece.upper()
        counts = white if piece.isupper() else black
        sign = float(_piece_color(piece))
        if up == 'R':
            _sliding_attack_counts(board, sq, counts, ((1, 0), (-1, 0), (0, 1), (0, -1)))
        elif up == 'B':
            _sliding_attack_counts(board, sq, counts, ((1, 1), (1, -1), (-1, 1), (-1, -1)))
        elif up == 'Q':
            _sliding_attack_counts(board, sq, counts, ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)))
        elif up == 'N':
            _jumper_attack_counts(sq, counts, ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)))
        elif up == 'K':
            _jumper_attack_counts(sq, counts, ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)))
        elif up == 'P':
            _pawn_attack_counts(sq, sign, counts)
    return white, black




def _king_square(board: Dict[int, str], white: bool) -> Optional[int]:
    target = 'K' if white else 'k'
    for sq, piece in board.items():
        if piece == target:
            return int(sq)
    return None


def _chebyshev_distance_sq(a: Optional[int], b: int) -> int:
    if a is None:
        return 7
    ar, ac = sq_to_rc(int(a))
    br, bc = sq_to_rc(int(b))
    return max(abs(ar - br), abs(ac - bc))


def _king_zone_weight(dist: int) -> float:
    d = max(0, int(dist))
    if d <= 1:
        return 2.0
    if d == 2:
        return 1.5
    return 1.0


def cooperation_components_from_board(board: Dict[int, str]) -> Tuple[List[float], List[float]]:
    """Liefert den Cooperation-Layer getrennt nach Weiß und Schwarz.

    Diese Trennung ist wichtig für den nachfolgenden King-Layer, weil wir
    Schutz- und Angriffsgewichte farbspezifisch modulieren wollen, bevor die
    beiden Seiten wieder zu einem signed Vector zusammengeführt werden.
    """
    white_att, black_att = attack_count_maps_from_board(board)
    white_vec = [0.0] * 64
    black_vec = [0.0] * 64
    for sq in range(64):
        wa = int(white_att[sq])
        ba = int(black_att[sq])
        piece = board.get(sq)
        if wa >= 2:
            white_vec[sq] += min(0.25, 0.0625 * float(wa - 1))
        if ba >= 2:
            black_vec[sq] += min(0.25, 0.0625 * float(ba - 1))
        if piece:
            if piece.isupper():
                defenders = wa
                attackers = ba
                if defenders > 0:
                    white_vec[sq] += min(0.375, 0.09375 * float(defenders))
                if attackers > 0 and defenders >= attackers:
                    white_vec[sq] += min(0.125, 0.03125 * float(defenders))
            else:
                defenders = ba
                attackers = wa
                if defenders > 0:
                    black_vec[sq] += min(0.375, 0.09375 * float(defenders))
                if attackers > 0 and defenders >= attackers:
                    black_vec[sq] += min(0.125, 0.03125 * float(defenders))
    return white_vec, black_vec

def cooperation_vec64_from_board(board: Dict[int, str]) -> List[float]:
    """Kooperations-Layer als separater 64D-Vektor."""
    white_vec, black_vec = cooperation_components_from_board(board)
    return [float(white_vec[i]) - float(black_vec[i]) for i in range(64)]


def king_weighted_cooperation_vec64_from_board(board: Dict[int, str]) -> List[float]:
    """Cooperation-Layer mit Königsgewichtung.

    Idee:
      • Nähe zum eigenen König verstärkt Schutz-/Deckungssignale
      • Nähe zum gegnerischen König verstärkt Angriffs-/Drucksignale

    Die Gewichtung bleibt bewusst moderat, damit keine künstlichen
    Dauerschach-Schleifen allein durch überstarke Königszonen entstehen.
    """
    white_vec, black_vec = cooperation_components_from_board(board)
    w_king = _king_square(board, True)
    b_king = _king_square(board, False)
    out = [0.0] * 64
    for sq in range(64):
        w_def = _king_zone_weight(_chebyshev_distance_sq(w_king, sq))
        w_att = _king_zone_weight(_chebyshev_distance_sq(b_king, sq))
        b_def = _king_zone_weight(_chebyshev_distance_sq(b_king, sq))
        b_att = _king_zone_weight(_chebyshev_distance_sq(w_king, sq))
        white_factor = max(w_def, w_att)
        black_factor = max(b_def, b_att)
        out[sq] = float(white_vec[sq]) * float(white_factor) - float(black_vec[sq]) * float(black_factor)
    return out


def king_weighted_cooperation_vec64_from_fen(fen: str) -> List[float]:
    return king_weighted_cooperation_vec64_from_board(parse_fen(fen).board)


def cooperation_vec64_from_fen(fen: str) -> List[float]:
    return cooperation_vec64_from_board(parse_fen(fen).board)


def _territory_zone_weight(sq: int, piece: Optional[str] = None) -> float:
    """Leichte Zonen-Gewichtung für Raumkontrolle.

    Ziel ist kein harter Positionsbegriff wie in einer klassischen Engine,
    sondern ein billiges, headless-taugliches Raumdruck-Signal:
      • Zentrum / erweiterte Mitte wichtiger als Rand
      • gegnerische Hälfte leicht wichtiger als eigene
      • eigene Besetzung zählt positiv, gegnerische negativ

    Die Gewichtung bleibt bewusst moderat, damit Territory den bestehenden
    Mobility-/Coop-/King-Raum ergänzt statt dominiert.
    """
    r, c = sq_to_rc(sq)
    w = 1.0
    if 2 <= r <= 5 and 2 <= c <= 5:
        w += 0.25
    if 3 <= r <= 4 and 3 <= c <= 4:
        w += 0.25
    if piece:
        if piece.isupper() and r >= 4:
            w += 0.15
        elif piece.islower() and r <= 3:
            w += 0.15
    return w


def territory_vec64_from_board(board: Dict[int, str]) -> List[float]:
    """Territory-/Dominanz-Layer als signed 64D-Vektor.

    Pro Feld wird ein leichtes Raumkontrollsignal gebildet aus:
      • eigener vs. gegnerischer Angreiferzahl
      • Besetzung des Feldes
      • Zonen-Gewichtung (Zentrum / gegnerische Hälfte)

    Positive Werte bedeuten: Weiß dominiert das Feld eher.
    Negative Werte bedeuten: Schwarz dominiert das Feld eher.
    """
    white_att, black_att = attack_count_maps_from_board(board)
    out = [0.0] * 64
    for sq in range(64):
        piece = board.get(sq)
        occ = 0.0
        if piece:
            occ = 0.25 if piece.isupper() else -0.25
        delta = (0.125 * float(white_att[sq])) - (0.125 * float(black_att[sq])) + occ
        out[sq] = float(delta) * float(_territory_zone_weight(sq, piece))
    return out


def territory_vec64_from_fen(fen: str) -> List[float]:
    return territory_vec64_from_board(parse_fen(fen).board)


def vector66_from_fen(fen: str, outcome: float = 0.0) -> List[float]:
    p = parse_fen(fen)
    vec = mobility_vec64_from_board(p.board)
    vec.append(1.0 if p.turn == "w" else -1.0)
    vec.append(1.0 if outcome > 0.0 else (-1.0 if outcome < 0.0 else 0.0))
    return vec


def _material_bucket(board: Dict[int, str]) -> str:
    val = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 0}
    score = 0
    for piece in board.values():
        pv = val.get(piece.upper(), 0)
        score += pv if piece.isupper() else -pv
    if score <= -9:
        return "m3"
    if score <= -4:
        return "m2"
    if score <= -1:
        return "m1"
    if score < 1:
        return "00"
    if score < 4:
        return "p1"
    if score < 9:
        return "p2"
    return "p3"


def _phase(board: Dict[int, str]) -> str:
    heavy = sum(1 for p in board.values() if p.upper() in {"Q", "R"})
    light = sum(1 for p in board.values() if p.upper() in {"B", "N"})
    if heavy >= 5 and light >= 6:
        return "o"
    if heavy >= 2 or light >= 3:
        return "m"
    return "e"


def _castling_bucket(castling: str) -> str:
    w = ("K" in castling) or ("Q" in castling)
    b = ("k" in castling) or ("q" in castling)
    if w and b:
        return "bb"
    if w:
        return "wo"
    if b:
        return "bo"
    return "nn"


def summarize_fen(fen: str) -> Chess2StateSummary:
    p = parse_fen(fen)
    return Chess2StateSummary(
        turn=p.turn,
        material_bucket=_material_bucket(p.board),
        phase=_phase(p.board),
        castling=_castling_bucket(p.castling),
        in_check=False,
        pieces=len(p.board),
    )


def mobility_signature(fen: str) -> str:
    p = parse_fen(fen)
    vec = mobility_vec64_from_board(p.board)
    prefix = [p.turn, _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body = ",".join(_bucket_token(v, _MOBILITY_BUCKET) for v in vec)
    return "|".join(prefix) + "|" + body


def mobility_state_hash(fen: str) -> str:
    sig = mobility_signature(fen)
    digest = sha1(sig.encode("utf-8")).hexdigest()[:24]
    s = summarize_fen(fen)
    return f"chess2:m1:{s.turn}:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"


def canonical_mobility_signature(fen: str) -> str:
    fen_c, _flipped = canonicalize_fen(fen)
    p = parse_fen(fen_c)
    vec = mobility_vec64_from_board(p.board)
    prefix = ['w', _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body = ','.join(_bucket_token(v, _MOBILITY_BUCKET) for v in vec)
    return '|'.join(prefix) + '|' + body


def cooperation_signature(fen: str) -> str:
    p = parse_fen(fen)
    vec = cooperation_vec64_from_board(p.board)
    prefix = [p.turn, _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body = ','.join(_bucket_token(v, 0.0625) for v in vec)
    return '|'.join(prefix) + '|' + body


def cooperation_state_hash(fen: str) -> str:
    sig = cooperation_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    s = summarize_fen(fen)
    return f"chess2:m2:{s.turn}:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"


def canonical_cooperation_signature(fen: str) -> str:
    fen_c, _flipped = canonicalize_fen(fen)
    p = parse_fen(fen_c)
    mv = mobility_vec64_from_board(p.board)
    cv = cooperation_vec64_from_board(p.board)
    prefix = ['w', _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body_m = ','.join(_bucket_token(v, _MOBILITY_BUCKET) for v in mv)
    body_c = ','.join(_bucket_token(v, 0.0625) for v in cv)
    return '|'.join(prefix) + '|' + body_m + '||' + body_c


def canonical_cooperation_state_hash(fen: str) -> str:
    sig = canonical_cooperation_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    fen_c, _flipped = canonicalize_fen(fen)
    s = summarize_fen(fen_c)
    return f"chess2c:m2:w:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"




def cooperation_king_signature(fen: str) -> str:
    p = parse_fen(fen)
    mv = mobility_vec64_from_board(p.board)
    cv = king_weighted_cooperation_vec64_from_board(p.board)
    prefix = [p.turn, _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body_m = ','.join(_bucket_token(v, _MOBILITY_BUCKET) for v in mv)
    body_c = ','.join(_bucket_token(v, 0.0625) for v in cv)
    return '|'.join(prefix) + '|' + body_m + '||' + body_c


def cooperation_king_state_hash(fen: str) -> str:
    sig = cooperation_king_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    s = summarize_fen(fen)
    return f"chess2:m3:{s.turn}:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"


def canonical_cooperation_king_signature(fen: str) -> str:
    fen_c, _flipped = canonicalize_fen(fen)
    p = parse_fen(fen_c)
    mv = mobility_vec64_from_board(p.board)
    cv = king_weighted_cooperation_vec64_from_board(p.board)
    prefix = ['w', _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body_m = ','.join(_bucket_token(v, _MOBILITY_BUCKET) for v in mv)
    body_c = ','.join(_bucket_token(v, 0.0625) for v in cv)
    return '|'.join(prefix) + '|' + body_m + '||' + body_c


def canonical_cooperation_king_state_hash(fen: str) -> str:
    sig = canonical_cooperation_king_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    fen_c, _flipped = canonicalize_fen(fen)
    s = summarize_fen(fen_c)
    return f"chess2c:m3:w:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"



def cooperation_king_territory_signature(fen: str) -> str:
    p = parse_fen(fen)
    mv = mobility_vec64_from_board(p.board)
    cv = king_weighted_cooperation_vec64_from_board(p.board)
    tv = territory_vec64_from_board(p.board)
    prefix = [p.turn, _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body_m = ','.join(_bucket_token(v, _MOBILITY_BUCKET) for v in mv)
    body_c = ','.join(_bucket_token(v, 0.0625) for v in cv)
    body_t = ','.join(_bucket_token(v, 0.0625) for v in tv)
    return '|'.join(prefix) + '|' + body_m + '||' + body_c + '||' + body_t


def cooperation_king_territory_state_hash(fen: str) -> str:
    sig = cooperation_king_territory_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    s = summarize_fen(fen)
    return f"chess2:m4:{s.turn}:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"


def canonical_cooperation_king_territory_signature(fen: str) -> str:
    fen_c, _flipped = canonicalize_fen(fen)
    p = parse_fen(fen_c)
    mv = mobility_vec64_from_board(p.board)
    cv = king_weighted_cooperation_vec64_from_board(p.board)
    tv = territory_vec64_from_board(p.board)
    prefix = ['w', _material_bucket(p.board), _phase(p.board), _castling_bucket(p.castling)]
    body_m = ','.join(_bucket_token(v, _MOBILITY_BUCKET) for v in mv)
    body_c = ','.join(_bucket_token(v, 0.0625) for v in cv)
    body_t = ','.join(_bucket_token(v, 0.0625) for v in tv)
    return '|'.join(prefix) + '|' + body_m + '||' + body_c + '||' + body_t


def canonical_cooperation_king_territory_state_hash(fen: str) -> str:
    sig = canonical_cooperation_king_territory_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    fen_c, _flipped = canonicalize_fen(fen)
    s = summarize_fen(fen_c)
    return f"chess2c:m4:w:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"

def canonical_mobility_state_hash(fen: str) -> str:
    sig = canonical_mobility_signature(fen)
    digest = sha1(sig.encode('utf-8')).hexdigest()[:24]
    fen_c, _flipped = canonicalize_fen(fen)
    s = summarize_fen(fen_c)
    return f"chess2c:m1:w:{s.material_bucket}:{s.phase}:{s.castling}:{digest}"

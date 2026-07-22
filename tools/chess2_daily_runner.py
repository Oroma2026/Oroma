#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess2_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess2 Daily Runner – mobility-native Daily + optionaler Domain-Transfer
# Version: v3.8-r2-v1.2.3
# Stand:   2026-06-27
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
#
# SIDE-PROFILE / FARBE-WECHSEL (2026-06-27)
# ------------------------------------------
# Der Runner unterstützt einen expliziten Tagesprofil-Schalter
# `--side-profile white|black|auto`. Für `white` läuft der normale Mobility-
# Raum; für `black` nutzt der primäre Lauf den vorhandenen Flip-Pass, damit
# schwarze Stellungen aus ORÓMA-Sicht kanonisch wie weiße Stellungen bewertet
# und gelernt werden. Das ist bewusst minimal-invasiv: Die Engine bleibt
# headless, alle Züge/Traces bleiben erhalten, und die neue Information wird
# additiv in Episode-Meta/JSON persistiert. Der Orchestrator kann dadurch zwei
# ressourcenschonende Tagesläufe mit 1 Stunde Abstand planen: einmal Weiß-,
# einmal Schwarz-Perspektive.
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import random
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Script-/Modul-Kompatibilität -------------------------------------------------
#
# Dieser Runner soll sowohl als Modul
#
#   python3 -m tools.chess2_daily_runner
#
# als auch – produktiv auf dem Live-System sehr häufig – direkt als Datei
#
#   python3 tools/chess2_daily_runner.py
#
# funktionieren. Beim Direktstart setzt Python nur das Script-Verzeichnis
# (`.../tools`) auf `sys.path`, nicht aber automatisch das Projekt-Root
# (`/opt/ai/oroma`). Dadurch würden absolute Projektimporte wie
# `from core import sql_manager` fehlschlagen. Genau das ist beim User im
# Live-System passiert.
#
# Wir ergänzen deshalb VOR den ORÓMA-Imports defensiv den Projektwurzelpfad.
# Das ist minimal-invasiv, headless-tauglich und bewahrt die bestehende
# Importstruktur des Projekts.
if __package__ in {None, ""}:
    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT_STR)

from core import sql_manager
from core.chess2_repr import board_to_rows, mobility_state_hash, canonical_mobility_state_hash, cooperation_state_hash, canonical_cooperation_state_hash, cooperation_king_state_hash, canonical_cooperation_king_state_hash, cooperation_king_territory_state_hash, canonical_cooperation_king_territory_state_hash, parse_fen, parse_square, flip_fen, flip_uci, canonicalize_fen, canonicalize_uci, decanonicalize_uci, attack_count_maps_from_board
from mini_programs.chess.chess_game import ChessGame
from mini_programs.chess import chess_rules as chess_rules_mod


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if not v:
        return bool(default)
    return v in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _env_str(name: str, default: str) -> str:
    v = (os.environ.get(name, "") or "").strip()
    return v if v else default


def _sanitize_side_profile(value: Any) -> str:
    """Normalisiert das Chess2-Tagesprofil für den Farbe-Wechsel.

    Gültige Werte:
      • auto  -> historisches Verhalten, keine erzwungene Perspektive
      • white -> normaler Mobility-Raum; ORÓMA-Tagesprofil Weiß
      • black -> primärer Flip-Raum; ORÓMA-Tagesprofil Schwarz

    Der Wert ist additiv und ändert keine Datenbank-Schemata. Unbekannte Werte
    fallen produktiv auf `auto` zurück, damit fehlerhafte ENV-Werte den Runner
    nicht stoppen.
    """
    v = str(value or "auto").strip().lower()
    if v in {"w", "white", "weiss", "weiß"}:
        return "white"
    if v in {"b", "black", "schwarz"}:
        return "black"
    return "auto"


_PIECE_ABS_VALUE: Dict[str, float] = {
    "P": 1.0,
    "N": 3.0,
    "B": 3.0,
    "R": 5.0,
    "Q": 9.0,
    "K": 0.0,
}


def _piece_type(piece: str) -> str:
    s = str(piece or "").strip()
    return s.upper()[:1] if s else ""


def _piece_value(piece: str) -> float:
    return float(_PIECE_ABS_VALUE.get(_piece_type(piece), 0.0))


def _sq_name_to_index(name: str) -> Optional[int]:
    try:
        return parse_square(str(name or '').strip())
    except Exception:
        return None


_OPENING_SEED_BOOK: List[List[str]] = [
    ["e2e4", "e7e5", "g1f3", "b8c6"],
    ["d2d4", "d7d5", "c2c4", "e7e6"],
    ["c2c4", "e7e5", "b1c3", "g8f6"],
    ["g1f3", "d7d5", "d2d4", "g8f6"],
    ["e2e4", "c7c5", "g1f3", "d7d6"],
    ["d2d4", "g8f6", "c2c4", "g7g6"],
    ["e2e4", "e7e6", "d2d4", "d7d5"],
    ["c2c4", "c7c5", "g2g3", "g7g6"],
]


def _select_opening_seed(index: int) -> List[str]:
    if not _OPENING_SEED_BOOK:
        return []
    return list(_OPENING_SEED_BOOK[int(index) % len(_OPENING_SEED_BOOK)])


def _apply_opening_seed(g: ChessGame, seed_moves: List[str]) -> Tuple[int, Dict[str, List[str]], Dict[str, List[str]], int]:
    recent_actions_by_side: Dict[str, List[str]] = {"W": [], "B": []}
    recent_piece_types_by_side: Dict[str, List[str]] = {"W": [], "B": []}
    applied = 0
    for mv in list(seed_moves or []):
        legal = g.legal_uci() or []
        if str(mv) not in legal:
            break
        side = _side_from_turn(g.turn)
        try:
            pfen = parse_fen(g.fen())
            src = _sq_name_to_index(str(mv)[:2])
            mover = str(pfen.board.get(int(src), '') or '') if src is not None else ''
            mover_t = _piece_type(mover)
        except Exception:
            mover_t = ''
        if not g.play_uci(str(mv)):
            break
        applied += 1
        recent_actions_by_side.setdefault(side, []).append(str(mv))
        if len(recent_actions_by_side[side]) > 4:
            recent_actions_by_side[side] = recent_actions_by_side[side][-4:]
        if mover_t:
            recent_piece_types_by_side.setdefault(side, []).append(str(mover_t))
            if len(recent_piece_types_by_side[side]) > 4:
                recent_piece_types_by_side[side] = recent_piece_types_by_side[side][-4:]
    return applied, recent_actions_by_side, recent_piece_types_by_side, applied




def _inside_rc(r: int, c: int) -> bool:
    return 0 <= int(r) < 8 and 0 <= int(c) < 8


def _piece_attacks_square(board: Dict[int, str], from_sq: int, piece: str, target_sq: int) -> bool:
    try:
        fr, fc = divmod(int(from_sq), 8)
        tr, tc = divmod(int(target_sq), 8)
        dr = tr - fr
        dc = tc - fc
        up = _piece_type(piece)
        if not up:
            return False
        if up == 'P':
            step = 1 if str(piece).isupper() else -1
            return dr == step and abs(dc) == 1
        if up == 'N':
            return (abs(dr), abs(dc)) in {(1, 2), (2, 1)}
        if up == 'K':
            return max(abs(dr), abs(dc)) == 1
        dirs: Tuple[Tuple[int, int], ...]
        if up == 'R':
            if dr != 0 and dc != 0:
                return False
            dirs = ((0, 1), (0, -1), (1, 0), (-1, 0))
        elif up == 'B':
            if abs(dr) != abs(dc):
                return False
            dirs = ((1, 1), (1, -1), (-1, 1), (-1, -1))
        elif up == 'Q':
            if not (dr == 0 or dc == 0 or abs(dr) == abs(dc)):
                return False
            dirs = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
        else:
            return False
        for sdr, sdc in dirs:
            r = fr + sdr
            c = fc + sdc
            while _inside_rc(r, c):
                sq = r * 8 + c
                if sq == int(target_sq):
                    return True
                if board.get(int(sq)) is not None:
                    break
                r += sdr
                c += sdc
        return False
    except Exception:
        return False


def _least_attacker_value(board: Dict[int, str], target_sq: int, by_white: bool) -> float:
    best: Optional[float] = None
    for sq, piece in board.items():
        p = str(piece or '')
        if not p:
            continue
        if bool(p.isupper()) != bool(by_white):
            continue
        if _piece_attacks_square(board, int(sq), p, int(target_sq)):
            pv = float(_piece_value(p))
            if best is None or pv < best:
                best = pv
    return float(best if best is not None else 99.0)


def _find_king_square(board: Dict[int, str], white: bool) -> Optional[int]:
    target = 'K' if white else 'k'
    for sq, piece in (board or {}).items():
        if str(piece or '') == target:
            return int(sq)
    return None


def _square_attack_counts(board: Dict[int, str], sq: int, own_is_white: bool) -> Tuple[int, int]:
    white_att, black_att = attack_count_maps_from_board(board)
    own_att = white_att if own_is_white else black_att
    opp_att = black_att if own_is_white else white_att
    return int(own_att[int(sq)] or 0), int(opp_att[int(sq)] or 0)


def _king_zone_balance(board: Dict[int, str], king_sq: Optional[int], own_is_white: bool) -> float:
    if king_sq is None:
        return 0.0
    kr, kc = divmod(int(king_sq), 8)
    white_att, black_att = attack_count_maps_from_board(board)
    own_att = white_att if own_is_white else black_att
    opp_att = black_att if own_is_white else white_att
    total = 0.0
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            rr = kr + dr
            cc = kc + dc
            if not _inside_rc(rr, cc):
                continue
            sq = rr * 8 + cc
            own_c = int(own_att[int(sq)] or 0)
            opp_c = int(opp_att[int(sq)] or 0)
            total += float(own_c - opp_c)
    return float(total)


def _rooks_connected(board: Dict[int, str], own_is_white: bool) -> bool:
    rooks: List[int] = []
    for sq, piece in (board or {}).items():
        p = str(piece or '')
        if not p:
            continue
        if _piece_type(p) != 'R':
            continue
        if bool(p.isupper()) != bool(own_is_white):
            continue
        rooks.append(int(sq))
    if len(rooks) < 2:
        return False
    rooks = sorted(rooks)
    for i in range(len(rooks)):
        for j in range(i + 1, len(rooks)):
            a = rooks[i]
            b = rooks[j]
            ar, ac = divmod(a, 8)
            br, bc = divmod(b, 8)
            if ar == br:
                step = 1 if bc > ac else -1
                clear = True
                for cc in range(ac + step, bc, step):
                    if board.get(ar * 8 + cc) is not None:
                        clear = False
                        break
                if clear:
                    return True
            elif ac == bc:
                step = 1 if br > ar else -1
                clear = True
                for rr in range(ar + step, br, step):
                    if board.get(rr * 8 + ac) is not None:
                        clear = False
                        break
                if clear:
                    return True
    return False


def _count_open_files(board: Dict[int, str]) -> int:
    n = 0
    for file_idx in range(8):
        have_pawn = False
        for rank_idx in range(8):
            p = str(board.get(rank_idx * 8 + file_idx, '') or '')
            if _piece_type(p) == 'P':
                have_pawn = True
                break
        if not have_pawn:
            n += 1
    return int(n)


def _is_castle_move(mover_t: str, src: int, dst: int) -> bool:
    return str(mover_t) == 'K' and abs(int(dst) - int(src)) == 2


def _is_en_passant_move(parsed: Any, mover_t: str, own_is_white: bool, dst: int, target_before: str) -> bool:
    if str(mover_t) != 'P' or str(target_before or ''):
        return False
    ep = str(getattr(parsed, 'ep', '-') or '-')
    if ep == '-':
        return False
    try:
        ep_sq = parse_square(ep)
    except Exception:
        ep_sq = None
    return ep_sq is not None and int(ep_sq) == int(dst)


def _simulate_board_after_uci(fen: str, uci: str) -> Optional[Dict[str, Any]]:
    s = str(uci or '').strip()
    if len(s) < 4:
        return None
    try:
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        src = parse_square(s[:2])
        dst = parse_square(s[2:4])
        if src is None or dst is None:
            return None
        mover = str(board.get(int(src), '') or '')
        if not mover:
            return None
        own_is_white = bool(mover.isupper())
        target_before = str(board.get(int(dst), '') or '')
        promotion = s[4:5].strip().lower() if len(s) >= 5 else ''
        # en passant
        ep_capture_sq: Optional[int] = None
        if _piece_type(mover) == 'P' and str(parsed.ep or '-') != '-' and s[2:4] == str(parsed.ep):
            dr = -1 if own_is_white else 1
            tr, tc = divmod(int(dst), 8)
            cr = tr + dr
            if _inside_rc(cr, tc):
                ep_capture_sq = cr * 8 + tc
        board.pop(int(src), None)
        if ep_capture_sq is not None:
            board.pop(int(ep_capture_sq), None)
        # castling rook move
        if _piece_type(mover) == 'K' and abs(int(dst) - int(src)) == 2:
            if int(dst) > int(src):
                rook_src = int(src) + 3
                rook_dst = int(src) + 1
            else:
                rook_src = int(src) - 4
                rook_dst = int(src) - 1
            rook_piece = str(board.pop(int(rook_src), '') or '')
            if rook_piece:
                board[int(rook_dst)] = rook_piece
        placed = mover
        if promotion and _piece_type(mover) == 'P':
            promo = promotion.upper() if own_is_white else promotion.lower()
            if promo in {'Q', 'R', 'B', 'N', 'q', 'r', 'b', 'n'}:
                placed = promo
        board[int(dst)] = placed
        return {
            'board_after': board,
            'src': int(src),
            'dst': int(dst),
            'mover': str(mover),
            'mover_after': str(placed),
            'target_before': str(target_before),
            'own_is_white': bool(own_is_white),
        }
    except Exception:
        return None


def _sum_exposed_own_value(board: Dict[int, str], own_is_white: bool, exclude: Optional[Set[int]] = None) -> Tuple[float, int]:
    exclude_set = set(int(x) for x in (exclude or set()))
    white_att, black_att = attack_count_maps_from_board(board)
    own_att = white_att if own_is_white else black_att
    opp_att = black_att if own_is_white else white_att
    total = 0.0
    count = 0
    for sq, piece in board.items():
        if int(sq) in exclude_set:
            continue
        p = str(piece or '')
        if not p or bool(p.isupper()) != bool(own_is_white):
            continue
        attackers = int(opp_att[int(sq)] or 0)
        defenders = int(own_att[int(sq)] or 0)
        if attackers <= 0:
            continue
        pv = float(_piece_value(p))
        if defenders <= 0 or attackers > defenders:
            total += pv
            count += 1
    return float(total), int(count)

def _action_piece_context(
    fen: str,
    uci: str,
    side: str,
    recent_own_actions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Liefert leichte taktisch-strategische Kontextmerkmale für einen Zug.

    Die Bewertung bleibt absichtlich klein und policy-zentriert. Ziel ist nicht,
    eine klassische Engine zu ersetzen, sondern mehrere im Live-Spiel beobachtete
    Fehlmuster gezielt zu dämpfen:
      1. klare Materialchancen werden übersehen
      2. der König dient als sicherer Parkplatz für monotone Züge
      3. immer dieselbe Figur wird wiederholt gezogen
      4. hängende/unterverteidigte Figuren werden zu selten sofort eingesammelt
      5. aggressive Züge lassen eigene Figuren ungeschützt zurück
      6. ein Zug entblößt dahinterliegende eigene Figuren/Linien
    """
    info: Dict[str, Any] = {
        "piece": "",
        "piece_value": 0.0,
        "capture_value": 0.0,
        "capture_target_type": "",
        "is_capture": False,
        "is_king_move": False,
        "other_major_minor_count": 0,
        "variety_new_type": False,
        "repeat_same_type": False,
        "recent_king_count": 0,
        "defenders_before": 0,
        "attackers_before": 0,
        "is_hanging_capture": False,
        "is_underdefended_capture": False,
        "is_favorable_capture": False,
        "self_attackers_after": 0,
        "self_defenders_after": 0,
        "self_is_hanging": False,
        "self_is_underdefended": False,
        "self_hanging_severity": 0.0,
        "retaliation_loss": 0.0,
        "is_defended_attack": False,
        "discovery_exposure_count": 0,
        "discovery_exposure_value": 0.0,
        "is_castle": False,
        "castle_king_safety_delta": 0.0,
        "castle_rook_connection": False,
        "is_promotion": False,
        "promotion_piece": "",
        "promotion_gain": 0.0,
        "is_en_passant": False,
        "en_passant_open_file_delta": 0,
        "is_check": False,
        "is_safe_check": False,
        "check_pressure_delta": 0.0,
    }
    s = str(uci or "").strip()
    if len(s) < 4:
        return info
    try:
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        src = parse_square(s[:2])
        dst = parse_square(s[2:4])
        if src is None or dst is None:
            return info
        mover = str(board.get(int(src), "") or "")
        if not mover:
            return info
        mover_t = _piece_type(mover)
        mover_v = float(_piece_value(mover))
        info["piece"] = mover_t
        info["piece_value"] = mover_v
        info["is_king_move"] = mover_t == "K"
        own_is_white = str(side) == "W"
        dst_piece = str(board.get(int(dst), "") or "")
        if dst_piece and ((own_is_white and dst_piece.islower()) or ((not own_is_white) and dst_piece.isupper())):
            capture_v = float(_piece_value(dst_piece))
            info["is_capture"] = True
            info["capture_value"] = capture_v
            info["capture_target_type"] = _piece_type(dst_piece)
            white_att, black_att = attack_count_maps_from_board(board)
            own_att = white_att if own_is_white else black_att
            opp_att = black_att if own_is_white else white_att
            attackers_before = int(own_att[int(dst)] or 0)
            defenders_before = int(opp_att[int(dst)] or 0)
            info["attackers_before"] = attackers_before
            info["defenders_before"] = defenders_before
            info["is_hanging_capture"] = defenders_before <= 0
            info["is_underdefended_capture"] = attackers_before > defenders_before
            favorable = False
            if defenders_before <= 0:
                favorable = True
            elif capture_v >= mover_v:
                favorable = True
            elif attackers_before > defenders_before and capture_v >= max(1.0, mover_v * 0.6):
                favorable = True
            info["is_favorable_capture"] = bool(favorable)
        other_major_minor = 0
        for sq, piece in board.items():
            p = str(piece or "")
            if not p:
                continue
            own_piece = p.isupper() if own_is_white else p.islower()
            if not own_piece:
                continue
            pt = _piece_type(p)
            if pt in {"Q", "R", "B", "N"} and int(sq) != int(src):
                other_major_minor += 1
        info["other_major_minor_count"] = int(other_major_minor)
        recent = list(recent_own_actions or [])[-4:]
        recent_types: List[str] = []
        recent_king_count = 0
        for mv in recent:
            mv_s = str(mv or "").strip()
            if len(mv_s) < 2:
                continue
            rsq = parse_square(mv_s[:2])
            if rsq is None:
                continue
            rp = str(board.get(int(rsq), "") or "")
            if not rp and len(mv_s) >= 4:
                rd = parse_square(mv_s[2:4])
                if rd is not None:
                    rp = str(board.get(int(rd), "") or "")
            rt = _piece_type(rp)
            if rt:
                recent_types.append(rt)
                if rt == "K":
                    recent_king_count += 1
        recent_set: Set[str] = set(t for t in recent_types if t)
        info["variety_new_type"] = bool(mover_t and mover_t not in recent_set)
        info["repeat_same_type"] = bool(recent_types and all(t == mover_t for t in recent_types if t))
        info["recent_king_count"] = int(recent_king_count)

        info['is_castle'] = bool(_is_castle_move(mover_t, int(src), int(dst)))
        info['is_en_passant'] = bool(_is_en_passant_move(parsed, mover_t, own_is_white, int(dst), dst_piece))
        promotion_piece = s[4:5].strip().lower() if len(s) >= 5 else ''
        if promotion_piece and mover_t == 'P':
            info['is_promotion'] = True
            info['promotion_piece'] = promotion_piece.upper()
            promo_abs = float(_piece_value(promotion_piece.upper()))
            info['promotion_gain'] = max(0.0, promo_abs - mover_v)

        sim = _simulate_board_after_uci(fen, s)
        if sim:
            board_after = dict(sim.get('board_after') or {})
            dst_after = int(sim.get('dst'))
            mover_after = str(sim.get('mover_after') or mover)
            white_att_after, black_att_after = attack_count_maps_from_board(board_after)
            own_att_after = white_att_after if own_is_white else black_att_after
            opp_att_after = black_att_after if own_is_white else white_att_after
            self_attackers_after = int(opp_att_after[dst_after] or 0)
            self_defenders_after = int(own_att_after[dst_after] or 0)
            info['self_attackers_after'] = self_attackers_after
            info['self_defenders_after'] = self_defenders_after
            info['self_is_hanging'] = bool(self_attackers_after > 0 and self_defenders_after <= 0)
            info['self_is_underdefended'] = bool(self_attackers_after > self_defenders_after)
            opp_lva = _least_attacker_value(board_after, dst_after, by_white=not own_is_white)
            moved_value = float(_piece_value(mover_after))
            if self_attackers_after > 0:
                if self_defenders_after <= 0:
                    sev = moved_value
                elif self_attackers_after > self_defenders_after:
                    sev = max(0.0, moved_value - min(moved_value, opp_lva if opp_lva < 90.0 else 0.0))
                else:
                    sev = 0.0
                if moved_value >= 5.0:
                    sev *= 1.25
                info['self_hanging_severity'] = float(sev)
                if info['self_is_hanging']:
                    info['retaliation_loss'] = float(moved_value)
                elif self_attackers_after > self_defenders_after:
                    info['retaliation_loss'] = float(max(0.0, moved_value - (opp_lva if opp_lva < 90.0 else 0.0)))
            if bool(info['is_capture']) and self_attackers_after > 0 and self_defenders_after > 0 and self_defenders_after >= self_attackers_after:
                info['is_defended_attack'] = True
            elif bool(info['is_capture']) and self_attackers_after <= 0:
                info['is_defended_attack'] = True
            before_exposed_val, before_exposed_cnt = _sum_exposed_own_value(board, own_is_white, exclude={int(src)})
            after_exposed_val, after_exposed_cnt = _sum_exposed_own_value(board_after, own_is_white, exclude={int(dst_after)})
            disc_cnt = max(0, int(after_exposed_cnt) - int(before_exposed_cnt))
            disc_val = max(0.0, float(after_exposed_val) - float(before_exposed_val))
            info['discovery_exposure_count'] = int(disc_cnt)
            info['discovery_exposure_value'] = float(disc_val)

            opp_king_sq = _find_king_square(board_after, white=not own_is_white)
            if opp_king_sq is not None:
                own_att_king, opp_att_king = _square_attack_counts(board_after, int(opp_king_sq), own_is_white)
                info['is_check'] = bool(own_att_king > 0)
                info['is_safe_check'] = bool(own_att_king > 0 and not bool(info.get('self_is_hanging')) and float(info.get('retaliation_loss', 0.0) or 0.0) <= 0.0)
                before_opp_king_sq = _find_king_square(board, white=not own_is_white)
                before_pressure = 0.0
                after_pressure = 0.0
                if before_opp_king_sq is not None:
                    before_pressure = _king_zone_balance(board, before_opp_king_sq, own_is_white)
                after_pressure = _king_zone_balance(board_after, opp_king_sq, own_is_white)
                info['check_pressure_delta'] = float(after_pressure - before_pressure)

            if bool(info.get('is_castle')):
                own_king_before = _find_king_square(board, white=own_is_white)
                own_king_after = _find_king_square(board_after, white=own_is_white)
                before_safe = _king_zone_balance(board, own_king_before, own_is_white)
                after_safe = _king_zone_balance(board_after, own_king_after, own_is_white)
                info['castle_king_safety_delta'] = float(after_safe - before_safe)
                info['castle_rook_connection'] = bool(_rooks_connected(board_after, own_is_white))

            if bool(info.get('is_en_passant')):
                info['en_passant_open_file_delta'] = int(_count_open_files(board_after) - _count_open_files(board))
    except Exception:
        return info
    return info

def _special_move_counts_for_action(fen: str, uci: str, side: str) -> Dict[str, int]:
    """Ermittelt Spezialzug-Zähler für den *tatsächlich ausgeführten* Zug.

    Die Policy-Heuristik bewertet Spezialzüge bereits im Kandidatenvergleich über
    `_action_piece_context()`. Für die Batch-/Runner-Diagnostik brauchen wir
    zusätzlich eine robuste, von der Heuristik getrennte Sicht darauf, welche
    Spezialzüge im realen Spielverlauf tatsächlich gespielt wurden.

    WICHTIG:
    - gezählt wird nur der ausgeführte Zug, nicht jeder Kandidat
    - Legalität kommt weiter aus dem Spiel/Runner, nicht aus dem Logging
    - die Zähler sind absichtlich klein und rein additiv, damit bestehende
      Batch- und DB-Workflows unverändert weiterlaufen
    """
    ctx = _action_piece_context(fen, uci, side, recent_own_actions=None)
    return {
        "castles": 1 if bool(ctx.get("is_castle")) else 0,
        "promotions": 1 if bool(ctx.get("is_promotion")) else 0,
        "en_passant": 1 if bool(ctx.get("is_en_passant")) else 0,
        "checks": 1 if bool(ctx.get("is_check")) else 0,
    }




def _flat_phase_weight(fen: str) -> float:
    """Leichter Phasenschalter gegen frühe Verflachung und Symmetrie.

    Die Funktion bleibt absichtlich grob und billig. Sie soll keine exakte
    Mittelspiel-Erkennung sein, sondern nur den Heuristikdruck in frühen bis
    mittleren Phasen erhöhen und ihn im schweren Endspiel stark reduzieren.
    """
    try:
        parsed = parse_fen(fen)
        fullmove = int(parsed.fullmove or 1)
        pieces = sum(1 for p in dict(parsed.board or {}).values() if str(p or ''))
        if pieces <= 8:
            return 0.0
        if fullmove < 6:
            return 0.35
        if fullmove <= 14:
            return 1.0
        if fullmove <= 24:
            return 0.95
        if fullmove <= 36:
            return 0.75
        if fullmove <= 52:
            return 0.45
        return 0.20
    except Exception:
        return 0.0


def _square_centrality(sq: int) -> float:
    try:
        r, c = divmod(int(sq), 8)
        dist = abs(3.5 - float(r)) + abs(3.5 - float(c))
        return max(0.0, 1.0 - (dist / 7.0))
    except Exception:
        return 0.0


def _piece_activity_proxy(sq: int, piece: str, own_is_white: bool, fullmove: int) -> float:
    """Billiger Aktivitätsproxy für Positionsverbesserung.

    Wir wollen hier keine schwere klassische Engine-Mobilität nachbauen,
    sondern nur erkennen, ob eine Figur offensichtlich passiv/undeveloped steht
    oder durch den Zug spürbar besser koordiniert wird.
    """
    try:
        pt = _piece_type(piece)
        if pt in {'', 'P', 'K'}:
            return 0.0
        sq = int(sq)
        r, c = divmod(sq, 8)
        if own_is_white:
            progress = (7.0 - float(r)) / 7.0
            home_minor = {'N': {int(parse_square('b1') or -1), int(parse_square('g1') or -1)}, 'B': {int(parse_square('c1') or -1), int(parse_square('f1') or -1)}}
            home_rook = {int(parse_square('a1') or -1), int(parse_square('h1') or -1)}
            home_queen = int(parse_square('d1') or -1)
        else:
            progress = float(r) / 7.0
            home_minor = {'N': {int(parse_square('b8') or -1), int(parse_square('g8') or -1)}, 'B': {int(parse_square('c8') or -1), int(parse_square('f8') or -1)}}
            home_rook = {int(parse_square('a8') or -1), int(parse_square('h8') or -1)}
            home_queen = int(parse_square('d8') or -1)
        score = 0.45 * _square_centrality(sq)
        if pt in {'N', 'B'}:
            if sq in home_minor.get(pt, set()):
                score -= 0.80
            else:
                score += 0.15
            score += 0.20 * progress
        elif pt == 'R':
            if sq in home_rook and fullmove >= 8:
                score -= 0.35
            score += 0.10 * progress
        elif pt == 'Q':
            if sq == home_queen and fullmove >= 10:
                score -= 0.15
            score += 0.08 * progress
        if c in {0, 7}:
            score -= 0.08
        return float(score)
    except Exception:
        return 0.0


def _worst_piece_improve_score(fen: str, uci: str, side: str, ctx: Optional[Dict[str, Any]] = None, base_bias: float = 0.05) -> Dict[str, Any]:
    """Kleiner Bonus, wenn der Zug eine klar passive Figur verbessert.

    Die Leitlinie ist bewusst einfach: Wenn der gezogene Nicht-Bauer vor dem Zug
    zu den schwächsten eigenen Figuren zählt und seine Aktivität nach dem Zug
    sichtbar steigt, bekommt Chess3 einen kleinen Positionsbonus.
    """
    out: Dict[str, Any] = {'bonus': 0.0, 'improved': False, 'before': 0.0, 'after': 0.0, 'delta': 0.0}
    try:
        if float(base_bias or 0.0) <= 0.0:
            return out
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        own_is_white = str(side or 'W').upper().startswith('W')
        fullmove = int(parsed.fullmove or 1)
        s = str(uci or '').strip().lower()
        if len(s) < 4:
            return out
        src = parse_square(s[:2])
        dst = parse_square(s[2:4])
        if src is None or dst is None:
            return out
        src = int(src); dst = int(dst)
        mover = str(board.get(src, '') or str((ctx or {}).get('piece', '') or ''))
        pt = _piece_type(mover)
        if pt in {'', 'P', 'K'}:
            return out
        sim = _simulate_board_after_uci(fen, s)
        if not sim:
            return out
        moved_after = str(sim.get('mover_after') or mover)
        candidates: List[Tuple[float, int]] = []
        for sq, piece in board.items():
            ps = str(piece or '')
            if not ps or (ps.isupper() != own_is_white):
                continue
            if _piece_type(ps) in {'', 'P', 'K'}:
                continue
            candidates.append((_piece_activity_proxy(int(sq), ps, own_is_white, fullmove), int(sq)))
        if not candidates:
            return out
        candidates.sort(key=lambda it: (float(it[0]), int(it[1])))
        worst_score = float(candidates[0][0])
        moved_before = float(_piece_activity_proxy(src, mover, own_is_white, fullmove))
        moved_after_score = float(_piece_activity_proxy(dst, moved_after, own_is_white, fullmove))
        delta = moved_after_score - moved_before
        out['before'] = float(moved_before)
        out['after'] = float(moved_after_score)
        out['delta'] = float(delta)
        if moved_before <= (worst_score + 0.15) and delta > 0.22 and not bool((ctx or {}).get('self_is_hanging')):
            bonus = float(base_bias) * min(1.0, max(0.0, delta))
            out['bonus'] = float(bonus)
            out['improved'] = bool(bonus > 0.0)
        return out
    except Exception:
        return out


def _fetch_policy_action_stats(namespace: str, state_hash: str, actions: List[str]) -> Dict[str, Tuple[int, float]]:
    """Liest (n, q) für alle legalen Aktionen eines gegebenen Zustands.

    Dadurch können kleine Biases *auf* die bestehende Policy gelegt werden,
    statt sie zu ersetzen. Unbekannte Aktionen bleiben bei (0, 0.0).
    """
    out: Dict[str, Tuple[int, float]] = {}
    legal = [str(a) for a in (actions or []) if str(a or "").strip()]
    if not legal:
        return out
    try:
        qmarks = ",".join(["?"] * len(legal))
        sql = (
            "SELECT action, n, q FROM policy_rules "
            "WHERE namespace=? AND state_hash=? AND action IN (" + qmarks + ")"
        )
        params: List[Any] = [str(namespace), str(state_hash), *legal]
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            for row in cur.execute(sql, params).fetchall() or []:
                if isinstance(row, dict):
                    act = str(row.get("action", ""))
                    n = int(row.get("n", 0) or 0)
                    q = float(row.get("q", 0.0) or 0.0)
                else:
                    act = str(row[0])
                    n = int(row[1] or 0)
                    q = float(row[2] or 0.0)
                out[act] = (n, q)
    except Exception as e:
        print(f"[chess2_daily_runner] policy action stats read failed: {e!r}", file=sys.stderr)
    return out


def _sq_to_rc(sq: int) -> Tuple[int, int]:
    return divmod(int(sq), 8)


def _king_metric_distance(a: Optional[int], b: Optional[int]) -> int:
    if a is None or b is None:
        return 99
    ar, ac = _sq_to_rc(int(a))
    br, bc = _sq_to_rc(int(b))
    return int(max(abs(ar - br), abs(ac - bc)))


def _piece_attacks_square(board: Dict[int, str], src: int, piece: str, dst: int) -> bool:
    src = int(src)
    dst = int(dst)
    if src == dst:
        return False
    p = str(piece or '')
    pt = _piece_type(p)
    if not pt:
        return False
    sr, sc = _sq_to_rc(src)
    dr, dc = _sq_to_rc(dst)
    rr = dr - sr
    cc = dc - sc
    own_is_white = bool(p.isupper())
    if pt == 'P':
        step = 1 if own_is_white else -1
        return rr == step and abs(cc) == 1
    if pt == 'N':
        return (abs(rr), abs(cc)) in {(1, 2), (2, 1)}
    if pt == 'K':
        return max(abs(rr), abs(cc)) == 1
    if pt in {'B', 'R', 'Q'}:
        if pt in {'B', 'Q'} and abs(rr) == abs(cc) and rr != 0:
            sr_step = 1 if rr > 0 else -1
            sc_step = 1 if cc > 0 else -1
        elif pt in {'R', 'Q'} and ((rr == 0 and cc != 0) or (cc == 0 and rr != 0)):
            sr_step = 0 if rr == 0 else (1 if rr > 0 else -1)
            sc_step = 0 if cc == 0 else (1 if cc > 0 else -1)
        else:
            return False
        cr = sr + sr_step
        cc2 = sc + sc_step
        while (cr, cc2) != (dr, dc):
            if not _inside_rc(cr, cc2):
                return False
            if str(board.get(cr * 8 + cc2, '') or ''):
                return False
            cr += sr_step
            cc2 += sc_step
        return True
    return False


def _king_ring_squares(king_sq: Optional[int], radius: int = 1) -> Set[int]:
    if king_sq is None:
        return set()
    kr, kc = _sq_to_rc(int(king_sq))
    out: Set[int] = set()
    for dr in range(-int(radius), int(radius) + 1):
        for dc in range(-int(radius), int(radius) + 1):
            rr = kr + dr
            cc = kc + dc
            if _inside_rc(rr, cc):
                out.add(rr * 8 + cc)
    return out


def _friendly_shield_count(board: Dict[int, str], king_sq: Optional[int], own_is_white: bool) -> int:
    if king_sq is None:
        return 0
    ring = _king_ring_squares(king_sq, radius=1)
    cnt = 0
    for sq in ring:
        p = str(board.get(int(sq), '') or '')
        if not p:
            continue
        if bool(p.isupper()) != bool(own_is_white):
            continue
        cnt += 1
    return int(cnt)


def _non_pawn_material_value(board: Dict[int, str], own_is_white: Optional[bool] = None) -> float:
    total = 0.0
    for _, p in (board or {}).items():
        s = str(p or '')
        if not s:
            continue
        if own_is_white is not None and bool(s.isupper()) != bool(own_is_white):
            continue
        pt = _piece_type(s)
        if pt in {'', 'P', 'K'}:
            continue
        total += float(_piece_value(s))
    return float(total)


def _pawn_promotion_distance(board: Dict[int, str], own_is_white: bool) -> int:
    best = 99
    for sq, p in (board or {}).items():
        s = str(p or '')
        if not s or _piece_type(s) != 'P' or bool(s.isupper()) != bool(own_is_white):
            continue
        r, _ = _sq_to_rc(int(sq))
        dist = (7 - r) if own_is_white else r
        if dist < best:
            best = int(dist)
    return int(best)


def _game_from_fen(fen: str) -> ChessGame:
    """Erzeugt eine ChessGame-Instanz exakt aus einer gegebenen FEN.

    Dieser Helper ist die zentrale Brücke für den selektiven Lookahead. Er
    nutzt bewusst dieselbe ORÓMA-Regelengine wie der restliche Runner, damit
    Topologie, Rochade, En-passant und Endspiel-Details im Lookahead dieselbe
    Semantik haben wie im eigentlichen Spielbetrieb.
    """
    parsed = parse_fen(fen)
    g = ChessGame()
    mapping: Dict[Tuple[int, int], str] = {}
    for sq, piece in (parsed.board or {}).items():
        # HINWEIS ZUR ORIENTIERUNG:
        # `parse_fen()`/`state_hash` nutzen Square-Indizes mit Rank1=0,
        # die ORÓMA-Schachregelengine (`mini_programs.chess.chess_rules`)
        # arbeitet intern aber mit Zeile 0 = Brettoberkante (= Rank8).
        # Für die FEN→ChessGame-Brücke muss die Zeile daher gespiegelt
        # werden, sonst landet z. B. a8 fälschlich auf a1 und praktisch
        # jeder Lookahead-Zweig scheitert bereits beim Anwenden des Zuges.
        r_idx, c_idx = _sq_to_rc(int(sq))
        mapping[(7 - int(r_idx), int(c_idx))] = str(piece)
    g.pos.board.reset(mapping, ".")
    g.pos.stm = chess_rules_mod.WHITE if str(parsed.turn) == 'w' else chess_rules_mod.BLACK
    castling = str(parsed.castling or '-')
    g.pos.castle = chess_rules_mod.Castle('K' in castling, 'Q' in castling, 'k' in castling, 'q' in castling)
    ep = str(parsed.ep or '-')
    g.pos.ep_target = chess_rules_mod.sq_to_rc(ep) if ep and ep != '-' else None
    g.pos.halfmove = int(parsed.halfmove or 0)
    g.pos.fullmove = int(parsed.fullmove or 1)
    g.pos.rep = chess_rules_mod.RepetitionTable()
    try:
        g.pos._add_rep()
    except Exception:
        pass
    g.moves = []
    return g

def _legal_uci_from_game_fen(fen: str) -> List[str]:
    """Ermittelt legal spielbare UCI-Züge direkt aus dem rekonstruierten ChessGame.

    Dieser Helper dient als Konsistenzanker für Chess3-Selective-Lookahead. In
    einigen Stellungen können policy-/heuristikseitig betrachtete Kandidaten und
    die aus FEN rekonstruierte Regelengine minimal voneinander abweichen. Bevor
    der Lookahead einen Top-Kandidaten tief prüft, normalisieren wir ihn daher
    gegen die echte Legal-Liste des rekonstruierten Zustands.
    """
    try:
        g = _game_from_fen(fen)
        return [str(mv) for mv in (g.legal_uci() or []) if str(mv or '').strip()]
    except Exception:
        return []



def _empty_lookahead_counts() -> Dict[str, Any]:
    return {
        'lookahead_2ply_used': 0,
        'lookahead_3ply_used': 0,
        'lookahead_king_pressure_cases': 0,
        'lookahead_endgame_cases': 0,
        'lookahead_check_cases': 0,
        'lookahead_capture_cases': 0,
        'lookahead_agreement_count': 0,
        'lookahead_correction_count': 0,
        'lookahead_bonus_sum': 0.0,
        'lookahead_penalty_sum': 0.0,
        'lookahead_errors': 0,
        'lookahead_fallbacks': 0,
        'line_pressure_cases': 0,
        'line_pressure_bonus_sum': 0.0,
        'line_pressure_opening_cases': 0,
        'line_pressure_middlegame_cases': 0,
        'line_pressure_endgame_cases': 0,
        'line_pressure_errors': 0,
        'defense_disruption_cases': 0,
        'defense_disruption_bonus_sum': 0.0,
        'defense_disruption_king_zone_cases': 0,
        'defense_disruption_outpost_cases': 0,
        'defense_disruption_color_complex_cases': 0,
        'defense_disruption_errors': 0,
        'lookahead_conversion_bonus_sum': 0.0,
        'penalty_damper_cases': 0,
        'penalty_damper_sum': 0.0,
        'anti_flat_cases': 0,
        'anti_flat_penalty_sum': 0.0,
        'trade_without_gain_cases': 0,
        'trade_without_gain_penalty_sum': 0.0,
        'asymmetry_keep_cases': 0,
        'asymmetry_keep_bonus_sum': 0.0,
        'worst_piece_improve_cases': 0,
        'worst_piece_improve_bonus_sum': 0.0,
        'coordination_cases': 0,
        'coordination_bonus_sum': 0.0,
        'rook_file_activity_cases': 0,
        'rook_file_activity_bonus_sum': 0.0,
        'attack_coordination_cases': 0,
        'attack_coordination_bonus_sum': 0.0,
        'king_line_open_cases': 0,
        'king_line_open_bonus_sum': 0.0,
        'attacker_trade_penalty_cases': 0,
        'attacker_trade_penalty_sum': 0.0,
        'orbit_penalty_cases': 0,
        'orbit_penalty_sum': 0.0,
        'neutral_path_penalty_cases': 0,
        'neutral_path_penalty_sum': 0.0,
        'productive_asymmetry_cases': 0,
        'productive_asymmetry_bonus_sum': 0.0,
        'fixpoint_warning_cases': 0,
        'fixpoint_warning_penalty_sum': 0.0,
    }


def _king_pressure_score(fen: str, side: str) -> Dict[str, Any]:
    """Bewertet Bedrängnis um den eigenen König für Phase 3 vorbereitet.

    Die Funktion ist bewusst selektiv und leichtgewichtig gehalten. Sie nutzt
    keine Vollsuche, sondern nur lokale Geometrie + funktionale Angriffswirkung:
    gegnerische Figuren im 5-Felder-Radius zählen nur dann stark, wenn sie den
    Königsring oder den König real beeinflussen können.
    """
    out: Dict[str, Any] = {
        'score': 0,
        'near_attackers': 0,
        'extended_attackers': 0,
        'ring_attackers': 0,
        'heavy_near': 0,
        'knight_near': 0,
        'shield_count': 0,
        'king_in_check': False,
        'own_king_sq': None,
    }
    try:
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        own_is_white = str(side) == 'W'
        own_king_sq = _find_king_square(board, white=own_is_white)
        out['own_king_sq'] = int(own_king_sq) if own_king_sq is not None else None
        if own_king_sq is None:
            return out
        ring = _king_ring_squares(own_king_sq, radius=1)
        shield = _friendly_shield_count(board, own_king_sq, own_is_white)
        out['shield_count'] = int(shield)
        white_att, black_att = attack_count_maps_from_board(board)
        opp_att = black_att if own_is_white else white_att
        own_att = white_att if own_is_white else black_att
        out['king_in_check'] = bool(int(opp_att[int(own_king_sq)] or 0) > 0)
        score = 5 if out['king_in_check'] else 0
        for sq, p in board.items():
            s = str(p or '')
            if not s or bool(s.isupper()) == bool(own_is_white):
                continue
            dist = _king_metric_distance(int(sq), own_king_sq)
            if dist > 5:
                continue
            pt = _piece_type(s)
            ring_hits = sum(1 for rsq in ring if _piece_attacks_square(board, int(sq), s, int(rsq)))
            attacks_king = bool(_piece_attacks_square(board, int(sq), s, int(own_king_sq)))
            if dist <= 2:
                out['near_attackers'] = int(out['near_attackers']) + 1
                if pt in {'Q', 'R'}:
                    out['heavy_near'] = int(out['heavy_near']) + 1
                    score += 4 if (ring_hits > 0 or attacks_king) else 2
                elif pt in {'N', 'B'}:
                    if pt == 'N':
                        out['knight_near'] = int(out['knight_near']) + 1
                    score += 3 if (ring_hits > 0 or attacks_king) else 1
                elif pt == 'P':
                    score += 2 if ring_hits > 0 else 1
                elif pt == 'K':
                    score += 1
            else:
                out['extended_attackers'] = int(out['extended_attackers']) + 1
                if ring_hits > 0 or attacks_king:
                    out['ring_attackers'] = int(out['ring_attackers']) + 1
                    if pt in {'Q', 'R'}:
                        score += 2
                    elif pt == 'N':
                        # Druckzone 3–5: Springer zählen nur stark, wenn sie den
                        # Königsring real attackieren; bloße geometrische Nähe soll
                        # in der Kalibrierung v1.1 nicht mehr dieselbe Schärfe wie
                        # schwere Figuren oder Nahbereichsangriffe auslösen.
                        score += 1
                    elif pt == 'B':
                        # Läufer in der Druckzone wirken nur dann mit mittlerem
                        # Gewicht, wenn die Diagonale den Königsring tatsächlich
                        # erreicht; dadurch wird reine Distanz ohne echte Wirkung
                        # schwächer bewertet als im ersten aktiven v1-Stand.
                        score += 1
                    else:
                        score += 1
        if shield <= 2:
            score += 2
        elif shield <= 4:
            score += 1
        if int(own_att[int(own_king_sq)] or 0) <= 0 and int(opp_att[int(own_king_sq)] or 0) > 0:
            score += 1
        out['score'] = int(score)
    except Exception:
        return out
    return out


def _endgame_pressure(fen: str, side: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        'active': False,
        'pure_pawnish': False,
        'own_promo_dist': 99,
        'opp_promo_dist': 99,
        'non_pawn_material_total': 0.0,
        'score': 0,
    }
    try:
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        own_is_white = str(side) == 'W'
        own_mat = _non_pawn_material_value(board, own_is_white=True)
        opp_mat = _non_pawn_material_value(board, own_is_white=False)
        total_mat = float(own_mat + opp_mat)
        own_promo = _pawn_promotion_distance(board, own_is_white)
        opp_promo = _pawn_promotion_distance(board, not own_is_white)
        active = bool(total_mat <= 15.0 or own_promo <= 2 or opp_promo <= 2)
        pure_pawnish = bool(total_mat <= 3.0)
        score = 0
        if total_mat <= 15.0:
            score += 1
        if total_mat <= 8.0:
            score += 1
        if own_promo <= 2 or opp_promo <= 2:
            score += 2
        if pure_pawnish:
            score += 1
        out.update({
            'active': active,
            'pure_pawnish': pure_pawnish,
            'own_promo_dist': int(own_promo),
            'opp_promo_dist': int(opp_promo),
            'non_pawn_material_total': float(total_mat),
            'score': int(score),
        })
    except Exception:
        return out
    return out



def _line_pressure_raw_for_piece_on_square(board: Dict[int, str], sq: int, piece: str, enemy_king_sq: Optional[int]) -> Dict[str, Any]:
    pt = _piece_type(piece)
    out: Dict[str, Any] = {
        'targets': 0,
        'raw_score': 0.0,
        'line_factor': 1.0,
    }
    if pt not in {'Q', 'R', 'B'}:
        return out
    targets = _collect_line_pressure_targets(board, int(sq), piece)
    if not targets:
        return out
    line_factor = _line_pressure_piece_line_factor(board, int(sq), piece)
    raw = 0.0
    for tsq in targets:
        raw += float(_line_pressure_square_weight(int(tsq), enemy_king_sq))
    out.update({
        'targets': int(len(targets)),
        'raw_score': float(raw),
        'line_factor': float(line_factor),
    })
    return out


def _collect_pawn_supported_enemy_half_squares(board: Dict[int, str], own_is_white: bool) -> Set[int]:
    white_att, black_att = attack_count_maps_from_board(board)
    own_att = white_att if own_is_white else black_att
    out: Set[int] = set()
    for sq in range(64):
        r, _c = _sq_to_rc(int(sq))
        if own_is_white:
            if r < 4:
                continue
        else:
            if r > 3:
                continue
        if int(own_att[int(sq)] or 0) > 0:
            out.add(int(sq))
    return out


def _count_non_pawn_defenders_near_king(board: Dict[int, str], own_is_white: bool, king_sq: Optional[int], radius: int = 3) -> int:
    if king_sq is None:
        return 0
    ring = _king_ring_squares(king_sq, radius=radius)
    cnt = 0
    for sq in ring:
        p = str(board.get(int(sq), '') or '')
        if not p or bool(p.isupper()) != bool(own_is_white):
            continue
        pt = _piece_type(p)
        if pt in {'', 'P', 'K'}:
            continue
        cnt += 1
    return int(cnt)


def _compute_defense_disruption_score(
    fen: str,
    uci: str,
    side: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    legal_count: Optional[int] = None,
    base_bias: float = 0.05,
) -> Dict[str, Any]:
    """Bewertet strukturbrechende Schlagzüge für Chess3 v1.2.2.

    Ziel ist bewusst nicht pauschales Vereinfachen, sondern das Erkennen von
    Schlag-/Tauschzügen, die bei stabiler Lage die gegnerische Verteidigung
    messbar verschlechtern: weniger Verteidiger am König, freiere Einbruchsfelder,
    schwächerer Farbkomplex oder höherer nachgelagerter Linien-/Königsdruck.
    """
    out: Dict[str, Any] = {
        'active': False,
        'threat_score': 0,
        'gate': 0.0,
        'capture_piece_type': '',
        'mover_piece_type': '',
        'defender_drop': 0,
        'outpost_liberated': 0,
        'color_complex_weakened': 0,
        'king_zone_delta': 0.0,
        'line_pressure_delta': 0.0,
        'raw_score': 0.0,
        'bonus': 0.0,
        'error': '',
        'error_count': 0,
    }
    try:
        sim = _simulate_board_after_uci(fen, uci)
        if not sim:
            out['error'] = 'defense_disruption_sim_failed'
            out['error_count'] = 1
            return out
        parsed = parse_fen(fen)
        board_before = dict(parsed.board or {})
        board_after = dict(sim.get('board_after') or {})
        src = sim.get('src')
        dst = sim.get('dst')
        mover = str(sim.get('mover') or '')
        mover_after = str(sim.get('mover_after') or mover)
        target_before = str(sim.get('target_before') or '')
        own_is_white = bool(str(side) == 'W')
        if bool(mover.isupper()) != own_is_white:
            out['error'] = 'defense_disruption_side_mismatch'
            out['error_count'] = 1
            return out
        if not target_before:
            return out
        target_pt = _piece_type(target_before)
        mover_pt = _piece_type(mover)
        out['capture_piece_type'] = str(target_pt)
        out['mover_piece_type'] = str(mover_pt)
        if target_pt in {'', 'K', 'P'}:
            return out
        threat = _compute_threat_score(fen, uci, side, ctx=ctx, legal_count=legal_count)
        threat_score = int(threat.get('score', 0) or 0)
        out['threat_score'] = int(threat_score)
        if threat_score >= 6:
            out['gate'] = 0.0
            return out
        gate = 1.0 if threat_score < 3 else 0.45
        out['gate'] = float(gate)
        enemy_is_white = not own_is_white
        enemy_king_before = _find_king_square(board_before, white=enemy_is_white)
        enemy_king_after = _find_king_square(board_after, white=enemy_is_white)
        defenders_before = _count_non_pawn_defenders_near_king(board_before, enemy_is_white, enemy_king_before, radius=3)
        defenders_after = _count_non_pawn_defenders_near_king(board_after, enemy_is_white, enemy_king_after, radius=3)
        defender_drop = max(0, int(defenders_before) - int(defenders_after))
        out['defender_drop'] = int(defender_drop)
        king_zone_before = _king_zone_balance(board_before, enemy_king_before, own_is_white)
        king_zone_after = _king_zone_balance(board_after, enemy_king_after, own_is_white)
        king_zone_delta = float(king_zone_after - king_zone_before)
        out['king_zone_delta'] = float(king_zone_delta)
        line_delta = 0.0
        if src is not None and dst is not None and mover_pt in {'Q', 'R', 'B'}:
            before_raw = _line_pressure_raw_for_piece_on_square(board_before, int(src), mover, enemy_king_before)
            after_raw = _line_pressure_raw_for_piece_on_square(board_after, int(dst), mover_after, enemy_king_after)
            line_delta = max(0.0, float(after_raw.get('raw_score', 0.0) or 0.0) * float(after_raw.get('line_factor', 1.0) or 1.0) - float(before_raw.get('raw_score', 0.0) or 0.0) * float(before_raw.get('line_factor', 1.0) or 1.0))
        out['line_pressure_delta'] = float(line_delta)
        outposts = _collect_pawn_supported_enemy_half_squares(board_before, own_is_white)
        outpost_liberated = 0
        if outposts and dst is not None:
            for sq in outposts:
                if _piece_attacks_square(board_before, int(dst), target_before, int(sq)):
                    outpost_liberated += 1
        out['outpost_liberated'] = int(outpost_liberated)
        color_complex_weakened = 0
        if target_pt == 'B' and dst is not None:
            parity = int(sum(_sq_to_rc(int(dst))) % 2)
            remaining_same = 0
            for sq2, p2 in (board_after or {}).items():
                ps = str(p2 or '')
                if not ps or bool(ps.isupper()) != enemy_is_white or _piece_type(ps) != 'B':
                    continue
                if int(sum(_sq_to_rc(int(sq2))) % 2) == parity:
                    remaining_same += 1
            if remaining_same == 0:
                color_complex_weakened = 1
        out['color_complex_weakened'] = int(color_complex_weakened)
        phase = _phase_for_line_pressure(fen)
        phase_mul = 1.35 if phase == 'middlegame' else (0.95 if phase == 'endgame' else 1.00)
        pressure_seed = float(((ctx or {}).get('line_pressure') or {}).get('bonus', 0.0) or 0.0)
        raw = (
            float(defender_drop) * 1.55
            + max(0.0, float(king_zone_delta)) * 0.70
            + min(3.0, float(outpost_liberated)) * 0.72
            + float(color_complex_weakened) * 0.90
            + max(0.0, float(line_delta)) * 0.10
        )
        if pressure_seed > 0.0 and raw > 0.0:
            raw *= (1.0 + min(0.45, pressure_seed * 1.10))
        if raw <= 0.0:
            return out
        material_ratio = min(1.0, float(_piece_value(target_before)) / max(3.0, float(_piece_value(mover) or 1.0)))
        if pressure_seed > 0.0 and threat_score < 3 and (defender_drop > 0 or outpost_liberated > 0 or king_zone_delta > 0.0):
            phase_mul *= 1.08
        bonus = float(base_bias) * float(gate) * float(phase_mul) * float(raw) * (0.85 + 0.15 * material_ratio)
        out.update({
            'active': bool(bonus > 0.0),
            'raw_score': float(raw),
            'bonus': float(bonus),
        })
        return out
    except Exception as e:
        out['error'] = f'defense_disruption_exception:{type(e).__name__}'
        out['error_count'] = 1
        return out


def _opening_bias_weight(fen: str) -> float:
    """Gewichtet sanfte Eröffnungsleitlinien per frühem Fade-Out.

    Architekturidee:
    - Züge 1-10: voller Leitlinien-Bias
    - Züge 11-20: linear ausblenden
    - ab Zug 21: kein Eröffnungsbias mehr
    """
    try:
        fullmove = int(parse_fen(fen).fullmove or 1)
        if fullmove <= 10:
            return 1.0
        if fullmove >= 21:
            return 0.0
        return max(0.0, (20.0 - float(fullmove)) / 10.0)
    except Exception:
        return 0.0


def _opening_guideline_score(fen: str, uci: str, side: str, ctx: Optional[Dict[str, Any]] = None, recent_own_actions: Optional[List[str]] = None, base_bias: float = 0.075) -> Dict[str, Any]:
    """Weicher Entwicklungs-/Eröffnungsbonus für Chess3.

    Die Leitlinie soll bewusst keine Eröffnungsbibliothek sein, sondern nur
    frühe Verflachung bremsen und gesunde Entwicklung bevorzugen.
    Aktivierung:
    - nur in der Eröffnung
    - nur als kleiner Bias
    - per Fade-Out bis Zug 20
    """
    out: Dict[str, Any] = {
        'bonus': 0.0,
        'weight': 0.0,
        'development_bonus': 0.0,
        'castle_bonus': 0.0,
        'center_bonus': 0.0,
        'early_queen_penalty': 0.0,
        'repeat_piece_penalty': 0.0,
        'phase': 'off',
    }
    try:
        weight = float(_opening_bias_weight(fen))
        out['weight'] = float(weight)
        if weight <= 0.0 or float(base_bias or 0.0) <= 0.0:
            return out
        out['phase'] = 'opening'
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        s = str(uci or '').strip().lower()
        if len(s) < 4:
            return out
        src = parse_square(s[:2])
        dst = parse_square(s[2:4])
        if src is None or dst is None:
            return out
        src = int(src)
        dst = int(dst)
        mover = str(board.get(src, '') or str((ctx or {}).get('piece', '') or ''))
        pt = _piece_type(mover)
        if not pt:
            return out
        own_is_white = bool(str(side or 'W').upper().startswith('W'))
        bonus = 0.0
        start_sq = lambda name: int(parse_square(name) or -999)
        # 1) Leichtfiguren entwickeln
        if pt in {'N', 'B'}:
            home = ({'N': {start_sq('b1'), start_sq('g1')}, 'B': {start_sq('c1'), start_sq('f1')}} if own_is_white else {'N': {start_sq('b8'), start_sq('g8')}, 'B': {start_sq('c8'), start_sq('f8')}}).get(pt, set())
            if src in home and dst not in home:
                out['development_bonus'] += float(base_bias) * 0.70 * weight
                bonus += out['development_bonus']
        # 2) Rochade fördern
        if bool((ctx or {}).get('is_castle')):
            out['castle_bonus'] += float(base_bias) * 0.90 * weight
            bonus += out['castle_bonus']
        elif pt in {'N', 'B'}:
            # kleine Rochade-Vorbereitung: Figuren von Königsseite lösen
            if own_is_white and src in {start_sq('g1'), start_sq('f1')} and dst not in {start_sq('g1'), start_sq('f1')}:
                out['castle_bonus'] += float(base_bias) * 0.20 * weight
                bonus += out['castle_bonus']
            elif (not own_is_white) and src in {start_sq('g8'), start_sq('f8')} and dst not in {start_sq('g8'), start_sq('f8')}:
                out['castle_bonus'] += float(base_bias) * 0.20 * weight
                bonus += out['castle_bonus']
        # 3) Zentrumskontrolle / -besetzung
        center_names = ['d4', 'e4', 'd5', 'e5']
        center_sq = [int(parse_square(n) or -1) for n in center_names]
        if dst in center_sq and pt in {'P', 'N', 'B'}:
            out['center_bonus'] += float(base_bias) * 0.45 * weight
        sim = _simulate_board_after_uci(fen, s)
        if sim:
            board_after = dict(sim.get('board_after') or {})
            dst_after = int(sim.get('dst', dst))
            mover_after = str(sim.get('mover_after') or mover)
            control_bonus = 0.0
            for csq in center_sq:
                if csq < 0:
                    continue
                if _piece_attacks_square(board_after, dst_after, mover_after, csq):
                    control_bonus += float(base_bias) * 0.10 * weight
            out['center_bonus'] += min(float(base_bias) * 0.40 * weight, control_bonus)
        bonus += out['center_bonus']
        # 4) Frühe Dame leicht bremsen, wenn kein realer Druck entsteht
        if pt == 'Q':
            fullmove = int(parsed.fullmove or 1)
            if fullmove <= 10:
                lp_bonus = float(((ctx or {}).get('line_pressure') or {}).get('bonus', 0.0) or 0.0)
                dd_bonus = float(((ctx or {}).get('defense_disruption') or {}).get('bonus', 0.0) or 0.0)
                if not bool((ctx or {}).get('is_capture')) and not bool((ctx or {}).get('is_check')) and (lp_bonus + dd_bonus) < 0.20:
                    out['early_queen_penalty'] = float(base_bias) * 0.55 * weight
                    bonus -= out['early_queen_penalty']
        # 5) unnötige Wiederholung derselben Figur in der Eröffnung bremsen
        recent = list(recent_own_actions or [])[-2:]
        if recent:
            same_src_repeat = any(str(mv or '')[:2].lower() == s[:2] for mv in recent if str(mv or '').strip())
            same_type_repeat = False
            for mv in recent:
                mv_s = str(mv or '').strip().lower()
                if len(mv_s) < 2:
                    continue
                rsq = parse_square(mv_s[:2])
                if rsq is None:
                    continue
                rp = str(board.get(int(rsq), '') or '')
                if not rp and len(mv_s) >= 4:
                    rd = parse_square(mv_s[2:4])
                    if rd is not None:
                        rp = str(board.get(int(rd), '') or '')
                if _piece_type(rp) == pt:
                    same_type_repeat = True
                    break
            if (same_src_repeat or same_type_repeat) and not bool((ctx or {}).get('is_capture')) and not bool((ctx or {}).get('is_check')) and not bool((ctx or {}).get('is_castle')):
                out['repeat_piece_penalty'] = float(base_bias) * 0.35 * weight
                bonus -= out['repeat_piece_penalty']
        out['bonus'] = float(bonus)
        return out
    except Exception as e:
        out['error'] = f'opening_guideline_exception:{type(e).__name__}'
        return out


def _phase_for_line_pressure(fen: str) -> str:
    """Leitet eine grobe, stabile Partiephase für Line-Pressure ab.

    Die erste produktive v1.2-Stufe soll bewusst leichtgewichtig bleiben.
    Deshalb nutzen wir keine teure globale Heatmap und kein schweres
    Positionsmodell, sondern nur wenige robuste Signale:

    - Gesamtmenge an Nicht-Bauern-Material
    - Zugnummer/FEN-Fullmove als Öffnungsanker

    Das genügt, um die Dame in der Eröffnung klein zu halten, Türme im
    Mittelspiel auf Linienkontrolle zu fokussieren und Dame/Turm im Endspiel
    für Restriktion/Einschnüren stärker zu gewichten.
    """
    try:
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        total_non_pawn = float(_non_pawn_material_value(board, own_is_white=None))
        fullmove = int(parsed.fullmove or 1)
        if total_non_pawn <= 16.0:
            return 'endgame'
        if fullmove <= 10 or total_non_pawn >= 26.0:
            return 'opening'
        return 'middlegame'
    except Exception:
        return 'middlegame'


def _line_pressure_piece_phase_weight(piece_type: str, phase: str) -> float:
    pt = _piece_type(piece_type)
    phase_s = str(phase or 'middlegame')
    table: Dict[str, Dict[str, float]] = {
        'opening': {'Q': 0.10, 'R': 0.50, 'B': 1.20},
        'middlegame': {'Q': 0.80, 'R': 1.50, 'B': 1.00},
        'endgame': {'Q': 2.00, 'R': 1.80, 'B': 1.20},
    }
    return float((table.get(phase_s) or {}).get(pt, 0.0))


def _line_pressure_gate_multiplier(threat_score: int) -> float:
    """Koppelt Positionsdruck bewusst an die taktische Lage.

    Architekturidee aus dem Brainstorming:
    - hohe akute Gefahr  -> Taktik/Überleben dominiert, kein Pressure-Bonus
    - ruhige Stellung     -> voller Positionsdruck darf wirken
    - Übergangsbereich    -> nur gedämpfter Bonus
    """
    ts = int(threat_score or 0)
    if ts >= 6:
        return 0.0
    if ts < 3:
        return 1.0
    return 0.35


def _line_pressure_square_weight(sq: int, enemy_king_sq: Optional[int]) -> float:
    r, c = _sq_to_rc(int(sq))
    weight = 1.0
    if 2 <= int(r) <= 5 and 2 <= int(c) <= 5:
        weight *= 1.5
    elif int(r) in {0, 7} or int(c) in {0, 7}:
        weight *= 0.65
    if enemy_king_sq is not None:
        kr, kc = _sq_to_rc(int(enemy_king_sq))
        if max(abs(int(r) - int(kr)), abs(int(c) - int(kc))) <= 2:
            weight *= 2.0
    return float(weight)


def _line_pressure_piece_line_factor(board: Dict[int, str], sq: int, piece: str) -> float:
    pt = _piece_type(piece)
    if pt == 'R':
        _r, c = _sq_to_rc(int(sq))
        own_pawn = False
        opp_pawn = False
        own_is_white = bool(str(piece or '').isupper())
        for rank in range(8):
            p = str(board.get(rank * 8 + int(c), '') or '')
            if _piece_type(p) != 'P':
                continue
            if bool(p.isupper()) == own_is_white:
                own_pawn = True
            else:
                opp_pawn = True
        if not own_pawn and not opp_pawn:
            return 1.20
        if not own_pawn:
            return 1.10
    return 1.0


def _collect_line_pressure_targets(board: Dict[int, str], sq: int, piece: str) -> List[int]:
    pt = _piece_type(piece)
    if pt not in {'Q', 'R', 'B'}:
        return []
    directions: List[Tuple[int, int]] = []
    if pt in {'Q', 'R'}:
        directions.extend([(1, 0), (-1, 0), (0, 1), (0, -1)])
    if pt in {'Q', 'B'}:
        directions.extend([(1, 1), (1, -1), (-1, 1), (-1, -1)])
    own_is_white = bool(str(piece or '').isupper())
    r0, c0 = _sq_to_rc(int(sq))
    out: List[int] = []
    for dr, dc in directions:
        rr = int(r0) + int(dr)
        cc = int(c0) + int(dc)
        while _inside_rc(rr, cc):
            tsq = rr * 8 + cc
            occ = str(board.get(int(tsq), '') or '')
            if occ:
                if bool(occ.isupper()) != own_is_white:
                    out.append(int(tsq))
                break
            out.append(int(tsq))
            rr += int(dr)
            cc += int(dc)
    return out


def _compute_line_pressure_score(fen: str, uci: str, side: str, *, ctx: Optional[Dict[str, Any]] = None, legal_count: Optional[int] = None, base_bias: float = 0.02, middlegame_lift: float = 1.0) -> Dict[str, Any]:
    """Berechnet eine kleine, deterministische v1.2-Line-Pressure-Korrektur.

    Wichtige Designgrenzen für den ersten produktiven Patch:
    - nur Dame/Turm/Läufer
    - nur Strahlkontrolle legaler Linien, keine globale Influence-Engine
    - phasenabhängige Gewichtung
    - an `threat_score` gekoppelt, damit Taktik bei Krisenlage Priorität behält

    Bewertet bewusst die *resultierende Stellung nach dem Zug* statt nur das
    Quellfeld. Fehler werden nicht still verschluckt, sondern als sichtbare
    Telemetrie im Rückgabeobjekt markiert.
    """
    out: Dict[str, Any] = {
        'active': False,
        'phase': 'middlegame',
        'threat_score': 0,
        'gate': 0.0,
        'piece_type': '',
        'targets': 0,
        'raw_score': 0.0,
        'bonus': 0.0,
        'error': '',
        'error_count': 0,
    }
    try:
        sim = _simulate_board_after_uci(fen, uci)
        if not sim:
            out['error'] = 'line_pressure_sim_failed'
            out['error_count'] = 1
            return out
        board_after = dict(sim.get('board_after') or {})
        dst = sim.get('dst')
        if dst is None:
            out['error'] = 'line_pressure_missing_dst'
            out['error_count'] = 1
            return out
        piece = str(sim.get('mover_after') or sim.get('mover') or '')
        pt = _piece_type(piece)
        if pt not in {'Q', 'R', 'B'}:
            return out
        own_is_white = bool(str(side) == 'W')
        if bool(piece.isupper()) != own_is_white:
            out['error'] = 'line_pressure_side_mismatch'
            out['error_count'] = 1
            return out
        phase = _phase_for_line_pressure(fen)
        weight = _line_pressure_piece_phase_weight(pt, phase)
        if phase == 'middlegame':
            weight *= max(1.0, float(middlegame_lift))
        out.update({'phase': phase, 'piece_type': pt})
        if weight <= 0.0:
            return out
        threat = _compute_threat_score(fen, uci, side, ctx=ctx, legal_count=legal_count)
        threat_score = int(threat.get('score', 0) or 0)
        gate = _line_pressure_gate_multiplier(threat_score)
        out.update({'threat_score': threat_score, 'gate': float(gate)})
        if gate <= 0.0:
            return out
        enemy_king_sq = _find_king_square(board_after, white=not own_is_white)
        targets = _collect_line_pressure_targets(board_after, int(dst), piece)
        if not targets:
            return out
        line_factor = _line_pressure_piece_line_factor(board_after, int(dst), piece)
        raw = 0.0
        for tsq in targets:
            raw += float(_line_pressure_square_weight(int(tsq), enemy_king_sq))
        bonus = float(base_bias) * float(weight) * float(line_factor) * float(gate) * float(raw)
        out.update({
            'active': bool(bonus > 0.0),
            'targets': int(len(targets)),
            'raw_score': float(raw),
            'bonus': float(bonus),
        })
        return out
    except Exception as e:
        out['error'] = f'line_pressure_exception:{type(e).__name__}'
        out['error_count'] = 1
        return out


def _compute_threat_score(fen: str, uci: str, side: str, ctx: Optional[Dict[str, Any]] = None, legal_count: Optional[int] = None) -> Dict[str, Any]:
    """Aggregiert Phase-3-Trigger in einen selektiven Threat Score."""
    ctx_d = dict(ctx or _action_piece_context(fen, uci, side))
    king_p = _king_pressure_score(fen, side)
    end_p = _endgame_pressure(fen, side)
    score = 0
    reasons: List[str] = []
    if bool(king_p.get('king_in_check')):
        score += 5
        reasons.append('king_in_check')
    if float(ctx_d.get('retaliation_loss', 0.0) or 0.0) >= 5.0:
        score += 3
        reasons.append('heavy_retaliation_risk')
    if bool(ctx_d.get('self_is_hanging')) and float(ctx_d.get('self_hanging_severity', 0.0) or 0.0) >= 5.0:
        score += 3
        reasons.append('self_hanging_major')
    if legal_count is not None and int(legal_count) <= 4:
        score += 2
        reasons.append('forced_legal_count')
    if bool(ctx_d.get('is_check')):
        score += 1
        reasons.append('candidate_check')
    if bool(ctx_d.get('is_capture')):
        score += 1
        reasons.append('candidate_capture')
    if bool(ctx_d.get('is_promotion')):
        score += 1
        reasons.append('candidate_promotion')
    if bool(ctx_d.get('is_en_passant')):
        score += 1
        reasons.append('candidate_en_passant')
    kscore = int(king_p.get('score', 0) or 0)
    if kscore >= 3:
        score += min(4, kscore)
        reasons.append('king_pressure')
    escore = int(end_p.get('score', 0) or 0)
    if bool(end_p.get('active')):
        score += max(1, escore)
        reasons.append('endgame_pressure')
    return {
        'score': int(score),
        'reasons': reasons,
        'king_pressure': king_p,
        'endgame_pressure': end_p,
    }


def _select_lookahead_depth(fen: str, uci: str, side: str, ctx: Optional[Dict[str, Any]] = None, legal_count: Optional[int] = None) -> Dict[str, Any]:
    """Phase-3-Infrastruktur: bestimmt vorbereitete Selektivtiefe, noch ohne Aktivierung."""
    threat = _compute_threat_score(fen, uci, side, ctx=ctx, legal_count=legal_count)
    score = int(threat.get('score', 0) or 0)
    depth = 0
    if score >= 7:
        depth = 3
    elif score >= 3:
        depth = 2
    return {
        'depth': int(depth),
        'threat_score': int(score),
        'reasons': list(threat.get('reasons') or []),
        'king_pressure': dict(threat.get('king_pressure') or {}),
        'endgame_pressure': dict(threat.get('endgame_pressure') or {}),
    }


def _evaluate_move_with_selective_lookahead(fen: str, uci: str, side: str, *, base_score: float, ctx: Optional[Dict[str, Any]] = None, legal_count: Optional[int] = None, enabled: bool = False, shim: Optional["PolicyShim"] = None) -> Dict[str, Any]:
    """Selektiver aktiver Lookahead für Chess3 (Runner/Policy-only).

    Der Mechanismus bleibt bewusst klein: Top-Kandidaten werden mit 2-Ply oder
    3-Ply gegen die beste gegnerische Sofortantwort bzw. unsere beste
    Rückantwort geprüft. Das Ergebnis überschreibt den Basisscore nicht,
    sondern wirkt nur als moderater Korrekturterm.
    """
    sel = _select_lookahead_depth(fen, uci, side, ctx=ctx, legal_count=legal_count)
    out: Dict[str, Any] = {
        'enabled': bool(enabled),
        'active': False,
        'prepared_depth': int(sel.get('depth', 0) or 0),
        'used_depth': 0,
        'delta': 0.0,
        'bonus': 0.0,
        'penalty': 0.0,
        'agreement': False,
        'correction': False,
        'error': None,
        'fallback': False,
        'reasons': list(sel.get('reasons') or []),
        'threat_score': int(sel.get('threat_score', 0) or 0),
        'king_pressure': dict(sel.get('king_pressure') or {}),
        'endgame_pressure': dict(sel.get('endgame_pressure') or {}),
        'base_score': float(base_score),
        'final_score': float(base_score),
        'conversion_bonus': 0.0,
        'penalty_damper': 0.0,
    }
    if not enabled or shim is None:
        return out
    depth = int(sel.get('depth', 0) or 0)
    if depth <= 0:
        return out
    out['active'] = True
    out['used_depth'] = int(depth)
    try:
        g = _game_from_fen(fen)
        legal0 = list(g.legal_uci() or [])
        if uci not in legal0 or not g.play_uci(uci):
            out['fallback'] = True
            out['error'] = 'candidate_apply_failed'
            return out
        fen1 = g.fen()
        side1 = _side_from_turn(g.turn)
        legal1 = list(g.legal_uci() or [])
        opp_best = shim._best_action_for_position(fen1, legal1, side1)
        if opp_best is None:
            out['agreement'] = True
            return out
        opp_score = float(opp_best.get('score', 0.0) or 0.0)
        delta = -(opp_score * 0.35)
        if depth >= 3 and str(opp_best.get('action') or '') in legal1:
            g2 = _game_from_fen(fen1)
            if g2.play_uci(str(opp_best.get('action') or '')):
                fen2 = g2.fen()
                side2 = _side_from_turn(g2.turn)
                legal2 = list(g2.legal_uci() or [])
                own_best = shim._best_action_for_position(fen2, legal2, side2)
                if own_best is not None:
                    own_score = float(own_best.get('score', 0.0) or 0.0)
                    delta += own_score * 0.20
        pressure_bonus = float(((ctx or {}).get('line_pressure') or {}).get('bonus', 0.0) or 0.0)
        disruption_bonus = float(((ctx or {}).get('defense_disruption') or {}).get('bonus', 0.0) or 0.0)
        threat_score = int(sel.get('threat_score', 0) or 0)
        pressure_driver = min(2.0, pressure_bonus * 6.0)
        disruption_driver = min(2.5, disruption_bonus * 12.0)
        combined_driver = float(pressure_driver + disruption_driver)
        conversion_bonus = 0.0
        if threat_score < 5 and shim.lookahead_conversion_bias > 0.0:
            if pressure_bonus > 0.0 and disruption_bonus > 0.0:
                combined_driver *= 1.15
            conversion_bonus = float(shim.lookahead_conversion_bias) * min(3.0, combined_driver)
            if conversion_bonus > 0.0:
                delta += conversion_bonus
                out['conversion_bonus'] = float(conversion_bonus)
        penalty = float(max(0.0, -delta))
        bonus = float(max(0.0, delta))
        if penalty > 0.0:
            damper = 0.0
            if threat_score < 5 and shim.penalty_damper_ratio > 0.0:
                driver = min(1.35, (pressure_bonus * 4.5) + (disruption_bonus * 12.0))
                if pressure_bonus > 0.0 and disruption_bonus > 0.0:
                    driver = min(1.65, driver * 1.20)
                if threat_score < 3:
                    driver = min(1.80, driver + 0.15)
                damper = float(penalty) * float(shim.penalty_damper_ratio) * float(driver)
            if damper > 0.0:
                penalty = max(0.0, penalty - damper)
                out['penalty_damper'] = float(damper)
            penalty *= 0.78 if ((pressure_bonus > 0.0 or disruption_bonus > 0.0) and threat_score < 4) else 0.85
            delta = -penalty
        else:
            delta = bonus
        out['delta'] = float(delta)
        out['bonus'] = float(bonus + max(0.0, conversion_bonus))
        out['penalty'] = float(penalty)
        out['final_score'] = float(base_score + delta)
        out['agreement'] = bool(abs(delta) < 0.05)
        out['correction'] = bool(abs(delta) >= 0.05)
        return out
    except Exception as e:
        out['error'] = repr(e)
        out['fallback'] = True
        return out


def _canonicalize_for_flip_pass(fen: str, legal: List[str], side: str) -> Tuple[str, List[str], bool]:
    if str(side) != 'B':
        return fen, list(legal or []), False
    return flip_fen(fen), [flip_uci(u) for u in (legal or [])], True


def _phase_move_count(fen: str) -> int:
    """Liefert eine robuste Vollzugzahl aus FEN für phasenabhängige E.1.1-Regeln."""
    try:
        parts = str(fen or '').split()
        if len(parts) >= 6:
            return max(1, int(parts[5]))
    except Exception:
        pass
    return 1


def _compute_productive_asymmetry_bonus(
    fen: str,
    action: str,
    side: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    base_bias: float = 0.03,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {'bonus': 0.0, 'move_count': _phase_move_count(fen)}
    if base_bias <= 0.0:
        return out
    ctx = dict(ctx or {})
    mc = int(out['move_count'])
    if not (8 <= mc <= 80):
        return out
    lp = float(((ctx.get('line_pressure') or {}).get('bonus', 0.0)) or 0.0)
    dd = float(((ctx.get('defense_disruption') or {}).get('bonus', 0.0)) or 0.0)
    ac = float(((ctx.get('attack_coordination') or {}).get('bonus', 0.0)) or 0.0)
    klo = float(((ctx.get('king_line_open') or {}).get('bonus', 0.0)) or 0.0)
    pressure = max(0.0, lp + dd + 0.70 * ac + 0.60 * klo)
    if pressure < 0.08:
        return out
    repeat_same_type = bool(ctx.get('repeat_same_type'))
    is_capture = bool(ctx.get('is_capture'))
    if repeat_same_type and not is_capture and pressure < 0.18:
        return out
    scale = 0.95 if mc <= 30 else 1.10 if mc <= 55 else 1.00
    bonus = float(base_bias) * scale * min(2.0, pressure / 0.16)
    out['bonus'] = float(bonus)
    out['pressure'] = float(pressure)
    return out


def _compute_neutral_path_penalty(
    fen: str,
    action: str,
    side: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    base_penalty: float = 0.03,
    current_score: float = 0.0,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {'penalty': 0.0, 'move_count': _phase_move_count(fen)}
    if base_penalty <= 0.0:
        return out
    ctx = dict(ctx or {})
    mc = int(out['move_count'])
    if not (8 <= mc <= 80):
        return out
    lp = float(((ctx.get('line_pressure') or {}).get('bonus', 0.0)) or 0.0)
    dd = float(((ctx.get('defense_disruption') or {}).get('bonus', 0.0)) or 0.0)
    ac = float(((ctx.get('attack_coordination') or {}).get('bonus', 0.0)) or 0.0)
    klo = float(((ctx.get('king_line_open') or {}).get('bonus', 0.0)) or 0.0)
    pressure = max(0.0, lp + dd + 0.60 * ac + 0.55 * klo)
    if pressure >= 0.09:
        return out
    if bool(ctx.get('is_capture')) or bool(ctx.get('is_check')):
        return out
    if abs(float(current_score)) > 0.40:
        return out
    repeat_same_type = bool(ctx.get('repeat_same_type'))
    no_novelty = not bool(ctx.get('variety_new_type'))
    if not (repeat_same_type or no_novelty or mc >= 34):
        return out
    scale = 0.55 if mc <= 20 else 0.72 if mc <= 45 else 0.88
    pen = float(base_penalty) * scale * (1.0 + (0.20 if repeat_same_type else 0.0))
    out['penalty'] = float(pen)
    out['pressure'] = float(pressure)
    return out


def _compute_orbit_penalty(
    fen: str,
    action: str,
    side: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    base_penalty: float = 0.03,
    current_score: float = 0.0,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {'penalty': 0.0, 'move_count': _phase_move_count(fen)}
    if base_penalty <= 0.0:
        return out
    ctx = dict(ctx or {})
    mc = int(out['move_count'])
    if not (8 <= mc <= 80):
        return out
    repeat_same_type = bool(ctx.get('repeat_same_type'))
    if not repeat_same_type:
        return out
    lp = float(((ctx.get('line_pressure') or {}).get('bonus', 0.0)) or 0.0)
    dd = float(((ctx.get('defense_disruption') or {}).get('bonus', 0.0)) or 0.0)
    ac = float(((ctx.get('attack_coordination') or {}).get('bonus', 0.0)) or 0.0)
    klo = float(((ctx.get('king_line_open') or {}).get('bonus', 0.0)) or 0.0)
    pressure = max(0.0, lp + dd + 0.55 * ac + 0.50 * klo)
    if pressure >= 0.11 or bool(ctx.get('is_capture')) or bool(ctx.get('is_check')):
        return out
    if abs(float(current_score)) > 0.45:
        return out
    scale = 0.95 if mc <= 24 else 1.10 if mc <= 50 else 1.20
    pen = float(base_penalty) * scale
    out['penalty'] = float(pen)
    out['pressure'] = float(pressure)
    return out


def _compute_fixpoint_warning_penalty(
    fen: str,
    action: str,
    side: str,
    *,
    ctx: Optional[Dict[str, Any]] = None,
    base_penalty: float = 0.03,
    current_score: float = 0.0,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {'penalty': 0.0, 'move_count': _phase_move_count(fen)}
    if base_penalty <= 0.0:
        return out
    ctx = dict(ctx or {})
    mc = int(out['move_count'])
    if not (12 <= mc <= 80):
        return out
    lp = float(((ctx.get('line_pressure') or {}).get('bonus', 0.0)) or 0.0)
    dd = float(((ctx.get('defense_disruption') or {}).get('bonus', 0.0)) or 0.0)
    ac = float(((ctx.get('attack_coordination') or {}).get('bonus', 0.0)) or 0.0)
    klo = float(((ctx.get('king_line_open') or {}).get('bonus', 0.0)) or 0.0)
    asym = float(ctx.get('asymmetry_keep_bonus', 0.0) or 0.0)
    pressure = max(0.0, lp + dd + 0.50 * ac + 0.50 * klo + 0.40 * asym)
    if pressure >= 0.08 or bool(ctx.get('is_capture')) or bool(ctx.get('is_check')):
        return out
    if abs(float(current_score)) > 0.24:
        return out
    scale = 0.50 if mc <= 24 else 0.65 if mc <= 45 else 0.82
    pen = float(base_penalty) * scale
    out['penalty'] = float(pen)
    out['pressure'] = float(pressure)
    return out



class PolicyShim:
    """Schmale, aber produktionswichtige Universal-Policy-Brücke für Chess2.

    WICHTIGE FACHLICHE EINORDNUNG:
    -----------------------------
    Die zentrale Frage für Chess2 war zuletzt, ob der neue Mobility-Raum nur
    Traces sammelt oder bereits – analog zu Chess1 – wirklich über
    `core.universal_policy.Policy` entscheidet.

    Genau das kapselt diese Klasse:
      • `state_hash(fen)`      -> mobility_state_hash(fen)
      • `choose_meta(...)`     -> Policy(namespace="game:chess2").choose(...)
      • `learn_many(items)`    -> tabellarisches Lernen in policy_rules

    Damit liegt der semantische Policy-Raum für Chess2 konsistent auf
    `mobility_state_hash` und NICHT nur auf Replay-/Trace-Daten.

    Zusätzlich liefern wir `choose_meta()` mit einer Source-Klassifikation
    zurück. Das ist für die aktuelle Diagnose von Chess2 entscheidend, weil wir
    damit sichtbar machen können:
      • kam ein Zug wirklich aus der Universal Policy?
      • fiel der Runner auf Zufall zurück?
      • war es ein Explore-Zug?

    So können wir die Frage "nutzt Chess2 die Universal Policy wirklich?" nicht
    nur architektonisch, sondern auch metrisch beantworten.
    """

    def __init__(self, namespace: str, flip_mode: bool = False, canon_mode: bool = False, cooperation_mode: bool = False, king_mode: bool = False, territory_mode: bool = False, capture_bias: float = 0.0, king_shuffle_penalty: float = 0.0, piece_variety_bias: float = 0.0, hanging_piece_bias: float = 0.0, underdefended_piece_bias: float = 0.0, self_hanging_penalty: float = 0.0, retaliation_penalty: float = 0.0, defended_attack_bonus: float = 0.0, discovery_exposure_penalty: float = 0.0, castle_bias: float = 0.0, promotion_bias: float = 0.0, en_passant_bias: float = 0.0, check_bias: float = 0.0, line_pressure_bias: float = 0.02, line_pressure_middlegame_lift: float = 1.50, defense_disruption_bias: float = 0.065, lookahead_conversion_bias: float = 0.09, penalty_damper_ratio: float = 0.24, opening_guideline_bias: float = 0.075, anti_flat_bias: float = 0.060, asymmetry_keep_bias: float = 0.050, worst_piece_improve_bias: float = 0.050, coordination_bias: float = 0.040, rook_file_activity_bias: float = 0.035, attack_coordination_bias: float = 0.045, king_line_open_bias: float = 0.040, attacker_trade_penalty: float = 0.035, orbit_penalty_bias: float = 0.040, neutral_path_penalty_bias: float = 0.024, productive_asymmetry_bias: float = 0.050, fixpoint_warning_bias: float = 0.022, aggro: float = 1.0):
        self.namespace = namespace.strip() or "game:chess2"
        self.flip_mode = bool(flip_mode)
        self.canon_mode = bool(canon_mode)
        self.cooperation_mode = bool(cooperation_mode)
        self.king_mode = bool(king_mode)
        self.territory_mode = bool(territory_mode)
        self.capture_bias = max(0.0, float(capture_bias))
        self.king_shuffle_penalty = max(0.0, float(king_shuffle_penalty))
        self.piece_variety_bias = max(0.0, float(piece_variety_bias))
        self.hanging_piece_bias = max(0.0, float(hanging_piece_bias))
        self.underdefended_piece_bias = max(0.0, float(underdefended_piece_bias))
        self.self_hanging_penalty = max(0.0, float(self_hanging_penalty))
        self.retaliation_penalty = max(0.0, float(retaliation_penalty))
        self.defended_attack_bonus = max(0.0, float(defended_attack_bonus))
        self.discovery_exposure_penalty = max(0.0, float(discovery_exposure_penalty))
        self.castle_bias = max(0.0, float(castle_bias))
        self.promotion_bias = max(0.0, float(promotion_bias))
        self.en_passant_bias = max(0.0, float(en_passant_bias))
        self.check_bias = max(0.0, float(check_bias))
        self.line_pressure_bias = max(0.0, float(line_pressure_bias))
        self.line_pressure_middlegame_lift = max(1.0, float(line_pressure_middlegame_lift))
        self.defense_disruption_bias = max(0.0, float(defense_disruption_bias))
        self.lookahead_conversion_bias = max(0.0, float(lookahead_conversion_bias))
        self.penalty_damper_ratio = min(0.50, max(0.0, float(penalty_damper_ratio)))
        self.opening_guideline_bias = max(0.0, float(opening_guideline_bias))
        self.anti_flat_bias = max(0.0, float(anti_flat_bias))
        self.asymmetry_keep_bias = max(0.0, float(asymmetry_keep_bias))
        self.worst_piece_improve_bias = max(0.0, float(worst_piece_improve_bias))
        self.coordination_bias = max(0.0, float(coordination_bias))
        self.rook_file_activity_bias = max(0.0, float(rook_file_activity_bias))
        self.attack_coordination_bias = max(0.0, float(attack_coordination_bias))
        self.king_line_open_bias = max(0.0, float(king_line_open_bias))
        self.attacker_trade_penalty = max(0.0, float(attacker_trade_penalty))
        self.orbit_penalty_bias = max(0.0, float(orbit_penalty_bias))
        self.neutral_path_penalty_bias = max(0.0, float(neutral_path_penalty_bias))
        self.productive_asymmetry_bias = max(0.0, float(productive_asymmetry_bias))
        self.fixpoint_warning_bias = max(0.0, float(fixpoint_warning_bias))
        self.aggro = min(2.0, max(1.0, float(aggro)))
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=self.namespace)
        except Exception:
            self.pol = None
        self.last_lookahead_meta: Dict[str, Any] = _empty_lookahead_counts()
        self._active_mode = "policy"

    def state_hash(self, fen: str) -> str:
        if self.canon_mode:
            if self.cooperation_mode and self.king_mode and self.territory_mode:
                return canonical_cooperation_king_territory_state_hash(fen)
            if self.cooperation_mode and self.king_mode:
                return canonical_cooperation_king_state_hash(fen)
            return canonical_cooperation_state_hash(fen) if self.cooperation_mode else canonical_mobility_state_hash(fen)
        if self.cooperation_mode and self.king_mode and self.territory_mode:
            return cooperation_king_territory_state_hash(fen)
        if self.cooperation_mode and self.king_mode:
            return cooperation_king_state_hash(fen)
        return cooperation_state_hash(fen) if self.cooperation_mode else mobility_state_hash(fen)

    def _lookahead_enabled_for_current_choice(self) -> bool:
        return bool(self.namespace.lower().startswith("game:chess3") and str(getattr(self, "_active_mode", "policy")) == "policy")

    def _aggro_top_n(self) -> int:
        aggro = float(getattr(self, "aggro", 1.0) or 1.0)
        if aggro >= 1.55:
            return 6
        if aggro >= 1.40:
            return 5
        if aggro >= 1.20:
            return 4
        return 3

    def _aggro_stage(self) -> str:
        aggro = float(getattr(self, "aggro", 1.0) or 1.0)
        if aggro >= 1.55:
            return "berserker"
        if aggro >= 1.40:
            return "hard"
        if aggro >= 1.20:
            return "challenger"
        return "baseline"

    def _score_action(self, fen_c: str, act_c: str, side_c: str, q_cur: float, recent_canon: Optional[List[str]] = None, recent_piece_types: Optional[List[str]] = None, legal_count: Optional[int] = None) -> Tuple[float, Dict[str, Any]]:
        score = float(q_cur)
        ctx = _action_piece_context(fen_c, act_c, side_c, recent_own_actions=list(recent_canon or []))
        if recent_piece_types:
            recent_set: Set[str] = set(recent_piece_types)
            mover_t = _piece_type(str(ctx.get("piece", "")))
            if mover_t:
                ctx["variety_new_type"] = bool(mover_t not in recent_set)
                ctx["repeat_same_type"] = bool(recent_piece_types and all(t == mover_t for t in recent_piece_types))
                ctx["recent_king_count"] = int(sum(1 for t in recent_piece_types if t == "K"))
        capture_value = float(ctx.get("capture_value", 0.0) or 0.0)
        if self.capture_bias > 0.0 and capture_value > 0.0:
            score += float(self.capture_bias) * min(1.0, capture_value / 9.0)
        if self.hanging_piece_bias > 0.0 and bool(ctx.get("is_hanging_capture")):
            score += float(self.hanging_piece_bias) * min(1.0, max(capture_value, float(ctx.get("piece_value", 0.0) or 0.0)) / 9.0)
        elif self.underdefended_piece_bias > 0.0 and bool(ctx.get("is_underdefended_capture")):
            score += float(self.underdefended_piece_bias) * min(1.0, max(capture_value, float(ctx.get("piece_value", 0.0) or 0.0)) / 9.0)
        if self.underdefended_piece_bias > 0.0 and bool(ctx.get("is_favorable_capture")):
            score += float(self.underdefended_piece_bias) * 0.35
        if self.self_hanging_penalty > 0.0:
            self_severity = float(ctx.get("self_hanging_severity", 0.0) or 0.0)
            if bool(ctx.get("self_is_hanging")):
                score -= float(self.self_hanging_penalty) * (1.0 + min(1.0, self_severity / 9.0))
            elif bool(ctx.get("self_is_underdefended")) and self_severity > 0.0:
                score -= float(self.self_hanging_penalty) * 0.5 * min(1.0, self_severity / 9.0)
        if self.retaliation_penalty > 0.0:
            retaliation_loss = float(ctx.get("retaliation_loss", 0.0) or 0.0)
            if retaliation_loss > 0.0:
                score -= float(self.retaliation_penalty) * min(1.25, retaliation_loss / 9.0)
        if self.defended_attack_bonus > 0.0 and bool(ctx.get("is_defended_attack")):
            score += float(self.defended_attack_bonus)
        if self.discovery_exposure_penalty > 0.0:
            disc_val = float(ctx.get("discovery_exposure_value", 0.0) or 0.0)
            disc_cnt = int(ctx.get("discovery_exposure_count", 0) or 0)
            if disc_val > 0.0 or disc_cnt > 0:
                score -= float(self.discovery_exposure_penalty) * min(1.5, (disc_val / 9.0) + (0.15 * float(disc_cnt)))
        if self.castle_bias > 0.0 and bool(ctx.get("is_castle")):
            castle_delta = float(ctx.get("castle_king_safety_delta", 0.0) or 0.0)
            castle_mul = 0.75 + max(-0.5, min(1.0, castle_delta / 6.0))
            score += float(self.castle_bias) * castle_mul
            if bool(ctx.get("castle_rook_connection")):
                score += float(self.castle_bias) * 0.35
        if self.promotion_bias > 0.0 and bool(ctx.get("is_promotion")):
            promo_gain = float(ctx.get("promotion_gain", 0.0) or 0.0)
            promo_piece = str(ctx.get("promotion_piece", "") or "").upper()
            promo_mul = 1.0 + min(0.5, promo_gain / 8.0)
            if promo_piece == 'Q':
                promo_mul += 0.1
            elif promo_piece in {'R', 'B', 'N'}:
                promo_mul -= 0.05
            score += float(self.promotion_bias) * max(0.5, promo_mul)
        if self.en_passant_bias > 0.0 and bool(ctx.get("is_en_passant")):
            ep_mul = 1.0
            if bool(ctx.get("is_favorable_capture")):
                ep_mul += 0.25
            if int(ctx.get("en_passant_open_file_delta", 0) or 0) > 0:
                ep_mul += 0.15
            score += float(self.en_passant_bias) * ep_mul
        if self.check_bias > 0.0 and bool(ctx.get("is_check")):
            check_mul = 0.5
            if bool(ctx.get("is_safe_check")):
                check_mul += 0.5
            check_pressure = float(ctx.get("check_pressure_delta", 0.0) or 0.0)
            if check_pressure > 0.0:
                check_mul += min(0.5, check_pressure / 8.0)
            score += float(self.check_bias) * check_mul
        if self.line_pressure_bias > 0.0 and self.namespace.lower().startswith("game:chess3") and str(getattr(self, "_active_mode", "policy")) == "policy":
            lp = _compute_line_pressure_score(fen_c, act_c, side_c, ctx=ctx, legal_count=legal_count, base_bias=float(self.line_pressure_bias), middlegame_lift=float(self.line_pressure_middlegame_lift))
            ctx["line_pressure"] = lp
            lp_bonus = float(lp.get("bonus", 0.0) or 0.0)
            if lp_bonus > 0.0:
                score += lp_bonus
        if self.defense_disruption_bias > 0.0 and self.namespace.lower().startswith("game:chess3") and str(getattr(self, "_active_mode", "policy")) == "policy":
            dd = _compute_defense_disruption_score(fen_c, act_c, side_c, ctx=ctx, legal_count=legal_count, base_bias=float(self.defense_disruption_bias))
            ctx["defense_disruption"] = dd
            dd_bonus = float(dd.get("bonus", 0.0) or 0.0)
            if dd_bonus > 0.0:
                score += dd_bonus
        if self.opening_guideline_bias > 0.0 and self.namespace.lower().startswith("game:chess3") and str(getattr(self, "_active_mode", "policy")) == "policy":
            og = _opening_guideline_score(fen_c, act_c, side_c, ctx=ctx, recent_own_actions=list(recent_canon or []), base_bias=float(self.opening_guideline_bias))
            ctx["opening_guideline"] = og
            og_bonus = float(og.get("bonus", 0.0) or 0.0)
            if og_bonus != 0.0:
                score += og_bonus
        if self.namespace.lower().startswith("game:chess3") and str(getattr(self, "_active_mode", "policy")) == "policy":
            lp_map = dict(ctx.get("line_pressure") or {})
            dd_map = dict(ctx.get("defense_disruption") or {})
            og_map = dict(ctx.get("opening_guideline") or {})
            lp_bonus = float(lp_map.get("bonus", 0.0) or 0.0)
            dd_bonus = float(dd_map.get("bonus", 0.0) or 0.0)
            og_bonus = float(og_map.get("bonus", 0.0) or 0.0)
            pressure_gain = max(0.0, lp_bonus + dd_bonus + (0.35 * max(0.0, og_bonus)))
            flat_weight = float(_flat_phase_weight(fen_c))
            capture_value = float(ctx.get("capture_value", 0.0) or 0.0)
            moved_value = float(ctx.get("piece_value", 0.0) or 0.0)
            is_capture = bool(ctx.get("is_capture"))
            # Patch A.1: Vereinfachung ohne echten Gewinn etwas deutlicher abwerten.
            if flat_weight > 0.0 and self.anti_flat_bias > 0.0 and is_capture and abs(float(score)) < 0.34 and pressure_gain < 0.20:
                anti_pen = float(self.anti_flat_bias) * flat_weight * (0.95 + min(0.65, max(capture_value, moved_value) / 7.0))
                if pressure_gain < 0.08:
                    anti_pen *= 1.10
                score -= anti_pen
                ctx["anti_flat_penalty"] = float(anti_pen)
                if abs(capture_value - moved_value) <= 2.00 and max(capture_value, moved_value) >= 1.0:
                    twg_pen = float(self.anti_flat_bias) * (0.95 + (0.30 if pressure_gain < 0.08 else 0.0)) * max(0.55, flat_weight)
                    score -= twg_pen
                    ctx["trade_without_gain_penalty"] = float(twg_pen)
            # A.2: Asymmetrie/Spannung im frühen Mittelspiel stärker und länger halten.
            if flat_weight > 0.0 and self.asymmetry_keep_bias > 0.0 and (not is_capture) and abs(float(score)) < 0.42 and pressure_gain > 0.04 and not bool(ctx.get("repeat_same_type")):
                ak_bonus = float(self.asymmetry_keep_bias) * (0.85 + 0.45 * flat_weight) * min(1.35, max(0.18, pressure_gain) / 0.22)
                score += ak_bonus
                ctx["asymmetry_keep_bonus"] = float(ak_bonus)
            # Patch B: schlechteste Figur verbessern.
            if self.worst_piece_improve_bias > 0.0 and flat_weight > 0.0:
                wp = _worst_piece_improve_score(fen_c, act_c, side_c, ctx=ctx, base_bias=float(self.worst_piece_improve_bias) * flat_weight)
                ctx["worst_piece_improve"] = wp
                wp_bonus = float(wp.get("bonus", 0.0) or 0.0)
                if wp_bonus > 0.0:
                    score += wp_bonus
            # Patch C.1: koordinierter Angriff und Linien gegen den König.
            if self.attack_coordination_bias > 0.0:
                ac = _compute_attack_coordination_score(fen_c, act_c, side_c, ctx=ctx, base_bias=float(self.attack_coordination_bias) * max(0.5, flat_weight + 0.25))
                ctx["attack_coordination"] = ac
                ac_bonus = float(ac.get("bonus", 0.0) or 0.0)
                if ac_bonus > 0.0:
                    score += ac_bonus
            if self.king_line_open_bias > 0.0:
                klo = _compute_king_line_open_score(fen_c, act_c, side_c, ctx=ctx, base_bias=float(self.king_line_open_bias) * max(0.5, flat_weight + 0.20))
                ctx["king_line_open"] = klo
                klo_bonus = float(klo.get("bonus", 0.0) or 0.0)
                if klo_bonus > 0.0:
                    score += klo_bonus
            if self.attacker_trade_penalty > 0.0:
                atp = _compute_attacker_trade_penalty(fen_c, act_c, side_c, ctx=ctx, base_penalty=float(self.attacker_trade_penalty) * max(0.5, flat_weight + 0.20))
                ctx["attacker_trade_penalty"] = atp
                atp_pen = float(atp.get("penalty", 0.0) or 0.0)
                if atp_pen > 0.0:
                    score -= atp_pen
            # Patch E.1.1: Anti-Fixpunkt / Anti-Orbit mit breiteren, real erreichbaren Triggern.
            if self.productive_asymmetry_bias > 0.0:
                pab = _compute_productive_asymmetry_bonus(fen_c, act_c, side_c, ctx=ctx, base_bias=float(self.productive_asymmetry_bias) * max(0.55, flat_weight + 0.20))
                ctx["productive_asymmetry"] = pab
                pab_bonus = float(pab.get("bonus", 0.0) or 0.0)
                if pab_bonus > 0.0:
                    score += pab_bonus
            if self.neutral_path_penalty_bias > 0.0:
                npp = _compute_neutral_path_penalty(fen_c, act_c, side_c, ctx=ctx, base_penalty=float(self.neutral_path_penalty_bias) * max(0.55, flat_weight + 0.20), current_score=float(score))
                ctx["neutral_path_penalty"] = npp
                npp_pen = float(npp.get("penalty", 0.0) or 0.0)
                if npp_pen > 0.0:
                    score -= npp_pen
            if self.orbit_penalty_bias > 0.0:
                op = _compute_orbit_penalty(fen_c, act_c, side_c, ctx=ctx, base_penalty=float(self.orbit_penalty_bias) * max(0.55, flat_weight + 0.20), current_score=float(score))
                ctx["orbit_penalty"] = op
                op_pen = float(op.get("penalty", 0.0) or 0.0)
                if op_pen > 0.0:
                    score -= op_pen
            if self.fixpoint_warning_bias > 0.0:
                fwp = _compute_fixpoint_warning_penalty(fen_c, act_c, side_c, ctx=ctx, base_penalty=float(self.fixpoint_warning_bias) * max(0.55, flat_weight + 0.20), current_score=float(score))
                ctx["fixpoint_warning"] = fwp
                fwp_pen = float(fwp.get("penalty", 0.0) or 0.0)
                if fwp_pen > 0.0:
                    score -= fwp_pen
        if self.namespace.lower().startswith("game:chess3") and str(getattr(self, "_active_mode", "policy")) == "policy" and float(getattr(self, "aggro", 1.0) or 1.0) > 1.0:
            aggro = float(getattr(self, "aggro", 1.0) or 1.0)
            lp_map = dict(ctx.get("line_pressure") or {})
            dd_map = dict(ctx.get("defense_disruption") or {})
            lp_bonus = float(lp_map.get("bonus", 0.0) or 0.0)
            dd_bonus = float(dd_map.get("bonus", 0.0) or 0.0)
            pressure_gain = max(0.0, lp_bonus + dd_bonus)
            pressure_threat = max(int(lp_map.get("threat_score", 0) or 0), int(dd_map.get("threat_score", 0) or 0))
            scale = max(0.0, aggro - 1.0)
            if pressure_gain > 0.0:
                score += pressure_gain * (0.35 * scale)
            if aggro >= 1.40:
                is_simplifying = bool(ctx.get("is_capture"))
                if abs(float(score)) < 0.15 and is_simplifying and pressure_gain < 0.1:
                    malus = min(0.35, 0.5 * scale)
                    score -= malus
                    ctx["aggro_flattening_penalty"] = float(malus)
            hanging_sev = float(ctx.get("self_hanging_severity", 0.0) or 0.0)
            if hanging_sev > 0.0 and pressure_threat < 4:
                gain_threshold = (2.0 - aggro) * 0.4
                if pressure_gain > gain_threshold:
                    if aggro < 1.4:
                        damper = 0.30
                    elif aggro < 1.5:
                        damper = 0.60
                    else:
                        damper = 0.75
                    score += hanging_sev * damper
                    ctx["aggro_hanging_damper"] = float(hanging_sev * damper)
            if aggro >= 1.55 and pressure_gain > 0.25 and pressure_threat < 4:
                king_zone = 0.0
                if lp_bonus > 0.0:
                    king_zone += min(0.20, lp_bonus * 0.10)
                if dd_bonus > 0.0:
                    king_zone += min(0.20, dd_bonus * 0.15)
                if king_zone > 0.0:
                    score += king_zone
                    ctx["aggro_king_zone_bonus"] = float(king_zone)
        if self.king_shuffle_penalty > 0.0 and bool(ctx.get("is_king_move")):
            if int(ctx.get("other_major_minor_count", 0) or 0) >= 2:
                recent_king = int(ctx.get("recent_king_count", 0) or 0)
                if recent_king >= 1:
                    score -= float(self.king_shuffle_penalty) * (1.0 + min(1.0, float(recent_king) / 2.0))
        if self.piece_variety_bias > 0.0:
            if bool(ctx.get("variety_new_type")):
                score += float(self.piece_variety_bias)
            elif bool(ctx.get("repeat_same_type")):
                score -= float(self.piece_variety_bias) * 0.5
        return float(score), ctx

    def _best_action_for_position(self, fen: str, legal: List[str], side: str, recent_own_actions: Optional[List[str]] = None, recent_own_pieces: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if self.pol is None or not legal:
            return None
        fen_c, legal_c, flipped = self.canonicalize(fen, legal, side)
        sh = self.state_hash(fen_c)
        side_c = "W" if flipped else str(side)
        recent_canon = [canonicalize_uci(str(mv), flipped) for mv in (recent_own_actions or []) if str(mv or "").strip()]
        recent_piece_types = [_piece_type(str(pt)) for pt in (recent_own_pieces or []) if _piece_type(str(pt))]
        stats = _fetch_policy_action_stats(self.namespace, sh, legal_c)
        ranked: List[Dict[str, Any]] = []
        for act_c in legal_c:
            n_cur, q_cur = stats.get(str(act_c), (0, 0.0))
            score, ctx = self._score_action(fen_c, str(act_c), side_c, float(q_cur), recent_canon=recent_canon, recent_piece_types=recent_piece_types, legal_count=len(legal_c))
            ranked.append({
                'action': str(act_c),
                'score': float(score),
                'q': float(q_cur),
                'n': int(n_cur),
                'ctx': ctx,
                'play_action': decanonicalize_uci(str(act_c), flipped),
                'fen_c': fen_c,
                'state_hash': sh,
            })
        if not ranked:
            return None
        ranked.sort(key=lambda it: (float(it['score']), float(it['q']), int(it['n'])), reverse=True)
        return ranked[0]

    def canonicalize(self, fen: str, legal: List[str], side: str) -> Tuple[str, List[str], bool]:
        fen_cur = str(fen)
        legal_cur = list(legal or [])
        flipped = False
        if self.canon_mode:
            fen_cur, flipped = canonicalize_fen(fen_cur)
            if flipped:
                legal_cur = [canonicalize_uci(u, True) for u in legal_cur]
        if self.flip_mode:
            fen_cur, legal_cur, flip2 = _canonicalize_for_flip_pass(fen_cur, legal_cur, 'B' if not flipped and str(side) == 'B' else 'W')
            flipped = bool(flipped or flip2)
        return fen_cur, legal_cur, flipped

    def choose_meta(self, fen: str, legal: List[str], side: str, recent_own_actions: Optional[List[str]] = None, recent_own_pieces: Optional[List[str]] = None) -> Tuple[str, str, str, str, str]:
        self.last_lookahead_meta = _empty_lookahead_counts()
        if not legal:
            return "", "no_legal", "", fen, fen
        fen_c, legal_c, flipped = self.canonicalize(fen, legal, side)
        sh = self.state_hash(fen_c)
        side_c = "W" if flipped else str(side)
        recent_canon = [canonicalize_uci(str(mv), flipped) for mv in (recent_own_actions or []) if str(mv or "").strip()]
        recent_piece_types = [_piece_type(str(pt)) for pt in (recent_own_pieces or []) if _piece_type(str(pt))]
        if self.pol is None:
            chosen_play = str(random.choice(legal))
            chosen_learn = canonicalize_uci(chosen_play, flipped)
            return chosen_play, "no_policy_random", sh, fen_c, chosen_learn

        try:
            stats = _fetch_policy_action_stats(self.namespace, sh, legal_c)
            ranked: List[Dict[str, Any]] = []
            for act_c in legal_c:
                n_cur, q_cur = stats.get(str(act_c), (0, 0.0))
                score, ctx = self._score_action(fen_c, str(act_c), side_c, float(q_cur), recent_canon=recent_canon, recent_piece_types=recent_piece_types, legal_count=len(legal_c))
                ranked.append({
                    'action': str(act_c),
                    'score': float(score),
                    'base_score': float(score),
                    'q': float(q_cur),
                    'n': int(n_cur),
                    'ctx': ctx,
                })
            ranked.sort(key=lambda it: (float(it['score']), float(it['q']), int(it['n'])), reverse=True)
            if self._lookahead_enabled_for_current_choice() and ranked:
                verified_legal = set(_legal_uci_from_game_fen(fen_c) or [])
                top_n = min(self._aggro_top_n(), len(ranked))
                for item in ranked[:top_n]:
                    act_cur = str(item.get('action') or '')
                    if verified_legal and act_cur not in verified_legal:
                        lr = {
                            'enabled': True,
                            'active': False,
                            'prepared_depth': 0,
                            'used_depth': 0,
                            'delta': 0.0,
                            'bonus': 0.0,
                            'penalty': 0.0,
                            'agreement': False,
                            'correction': False,
                            'error': None,
                            'fallback': False,
                            'reasons': ['candidate_legal_mismatch_skipped'],
                            'threat_score': 0,
                            'king_pressure': {},
                            'endgame_pressure': {},
                            'base_score': float(item.get('base_score', item.get('score', 0.0)) or 0.0),
                            'final_score': float(item.get('score', 0.0) or 0.0),
                            'conversion_bonus': 0.0,
                            'penalty_damper': 0.0,
                        }
                    else:
                        lr = _evaluate_move_with_selective_lookahead(
                            fen_c,
                            act_cur,
                            side_c,
                            base_score=float(item['base_score']),
                            ctx=dict(item.get('ctx') or {}),
                            legal_count=len(legal_c),
                            enabled=True,
                            shim=self,
                        )
                    item['lookahead'] = lr
                    item['score'] = float(lr.get('final_score', item['score']) or item['score'])
                    used_depth = int(lr.get('used_depth', 0) or 0)
                    if used_depth == 2:
                        self.last_lookahead_meta['lookahead_2ply_used'] += 1
                    elif used_depth >= 3:
                        self.last_lookahead_meta['lookahead_3ply_used'] += 1
                    if int((lr.get('king_pressure') or {}).get('score', 0) or 0) > 0:
                        self.last_lookahead_meta['lookahead_king_pressure_cases'] += 1
                    if bool((lr.get('endgame_pressure') or {}).get('active')):
                        self.last_lookahead_meta['lookahead_endgame_cases'] += 1
                    if bool((item.get('ctx') or {}).get('is_check')):
                        self.last_lookahead_meta['lookahead_check_cases'] += 1
                    if bool((item.get('ctx') or {}).get('is_capture')):
                        self.last_lookahead_meta['lookahead_capture_cases'] += 1
                    if bool(lr.get('agreement')):
                        self.last_lookahead_meta['lookahead_agreement_count'] += 1
                    if bool(lr.get('correction')):
                        self.last_lookahead_meta['lookahead_correction_count'] += 1
                    self.last_lookahead_meta['lookahead_bonus_sum'] += float(lr.get('bonus', 0.0) or 0.0)
                    self.last_lookahead_meta['lookahead_penalty_sum'] += float(lr.get('penalty', 0.0) or 0.0)
                    self.last_lookahead_meta['lookahead_conversion_bonus_sum'] += float(lr.get('conversion_bonus', 0.0) or 0.0)
                    _pd = float(lr.get('penalty_damper', 0.0) or 0.0)
                    if _pd > 0.0:
                        self.last_lookahead_meta['penalty_damper_cases'] += 1
                        self.last_lookahead_meta['penalty_damper_sum'] += float(_pd)
                    if bool(lr.get('fallback')):
                        self.last_lookahead_meta['lookahead_fallbacks'] += 1
                    if lr.get('error'):
                        self.last_lookahead_meta['lookahead_errors'] += 1
                        try:
                            print(f"[chess3_selective_lookahead] action={item.get('action')} error={lr.get('error')} fallback={bool(lr.get('fallback'))}", file=sys.stderr)
                        except Exception:
                            pass
                ranked.sort(key=lambda it: (float(it['score']), float(it['q']), int(it['n'])), reverse=True)
            best = ranked[0] if ranked else None
            if best and str(best.get('action') or '') in legal_c:
                lp_best = dict(((best.get('ctx') or {}).get('line_pressure') or {}))
                lp_bonus = float(lp_best.get('bonus', 0.0) or 0.0)
                lp_error_count = int(lp_best.get('error_count', 0) or 0)
                if lp_error_count > 0:
                    self.last_lookahead_meta['line_pressure_errors'] += int(lp_error_count)
                    print(f"[chess3_line_pressure] {lp_best.get('error') or 'unknown_error'}", file=sys.stderr)
                if lp_bonus > 0.0:
                    self.last_lookahead_meta['line_pressure_cases'] += 1
                    self.last_lookahead_meta['line_pressure_bonus_sum'] += float(lp_bonus)
                    phase_lp = str(lp_best.get('phase') or 'middlegame')
                    if phase_lp == 'opening':
                        self.last_lookahead_meta['line_pressure_opening_cases'] += 1
                    elif phase_lp == 'endgame':
                        self.last_lookahead_meta['line_pressure_endgame_cases'] += 1
                    else:
                        self.last_lookahead_meta['line_pressure_middlegame_cases'] += 1
                dd_best = dict(((best.get('ctx') or {}).get('defense_disruption') or {}))
                dd_bonus = float(dd_best.get('bonus', 0.0) or 0.0)
                dd_error_count = int(dd_best.get('error_count', 0) or 0)
                if dd_error_count > 0:
                    self.last_lookahead_meta['defense_disruption_errors'] += int(dd_error_count)
                    print(f"[chess3_defense_disruption] {dd_best.get('error') or 'unknown_error'}", file=sys.stderr)
                if dd_bonus > 0.0:
                    self.last_lookahead_meta['defense_disruption_cases'] += 1
                    self.last_lookahead_meta['defense_disruption_bonus_sum'] += float(dd_bonus)
                    if int(dd_best.get('defender_drop', 0) or 0) > 0:
                        self.last_lookahead_meta['defense_disruption_king_zone_cases'] += 1
                    if int(dd_best.get('outpost_liberated', 0) or 0) > 0:
                        self.last_lookahead_meta['defense_disruption_outpost_cases'] += 1
                    if int(dd_best.get('color_complex_weakened', 0) or 0) > 0:
                        self.last_lookahead_meta['defense_disruption_color_complex_cases'] += 1
                af_pen = float(((best.get('ctx') or {}).get('anti_flat_penalty', 0.0)) or 0.0)
                if af_pen > 0.0:
                    self.last_lookahead_meta['anti_flat_cases'] += 1
                    self.last_lookahead_meta['anti_flat_penalty_sum'] += float(af_pen)
                twg_pen = float(((best.get('ctx') or {}).get('trade_without_gain_penalty', 0.0)) or 0.0)
                if twg_pen > 0.0:
                    self.last_lookahead_meta['trade_without_gain_cases'] += 1
                    self.last_lookahead_meta['trade_without_gain_penalty_sum'] += float(twg_pen)
                ak_bonus = float(((best.get('ctx') or {}).get('asymmetry_keep_bonus', 0.0)) or 0.0)
                if ak_bonus > 0.0:
                    self.last_lookahead_meta['asymmetry_keep_cases'] += 1
                    self.last_lookahead_meta['asymmetry_keep_bonus_sum'] += float(ak_bonus)
                wp_best = dict(((best.get('ctx') or {}).get('worst_piece_improve') or {}))
                wp_bonus = float(wp_best.get('bonus', 0.0) or 0.0)
                if wp_bonus > 0.0:
                    self.last_lookahead_meta['worst_piece_improve_cases'] += 1
                    self.last_lookahead_meta['worst_piece_improve_bonus_sum'] += float(wp_bonus)
                coord_best = dict(((best.get('ctx') or {}).get('coordination') or {}))
                coord_bonus = float(coord_best.get('bonus', 0.0) or 0.0)
                if coord_bonus > 0.0:
                    self.last_lookahead_meta['coordination_cases'] += 1
                    self.last_lookahead_meta['coordination_bonus_sum'] += float(coord_bonus)
                rook_best = dict(((best.get('ctx') or {}).get('rook_file_activity') or {}))
                rook_bonus = float(rook_best.get('bonus', 0.0) or 0.0)
                if rook_bonus > 0.0:
                    self.last_lookahead_meta['rook_file_activity_cases'] += 1
                    self.last_lookahead_meta['rook_file_activity_bonus_sum'] += float(rook_bonus)
                ac_best = dict(((best.get('ctx') or {}).get('attack_coordination') or {}))
                ac_bonus = float(ac_best.get('bonus', 0.0) or 0.0)
                if ac_bonus > 0.0:
                    self.last_lookahead_meta['attack_coordination_cases'] += 1
                    self.last_lookahead_meta['attack_coordination_bonus_sum'] += float(ac_bonus)
                klo_best = dict(((best.get('ctx') or {}).get('king_line_open') or {}))
                klo_bonus = float(klo_best.get('bonus', 0.0) or 0.0)
                if klo_bonus > 0.0:
                    self.last_lookahead_meta['king_line_open_cases'] += 1
                    self.last_lookahead_meta['king_line_open_bonus_sum'] += float(klo_bonus)
                atp_best = dict(((best.get('ctx') or {}).get('attacker_trade_penalty') or {}))
                atp_pen = float(atp_best.get('penalty', 0.0) or 0.0)
                if atp_pen > 0.0:
                    self.last_lookahead_meta['attacker_trade_penalty_cases'] += 1
                    self.last_lookahead_meta['attacker_trade_penalty_sum'] += float(atp_pen)
                op_best = dict(((best.get('ctx') or {}).get('orbit_penalty') or {}))
                op_pen = float(op_best.get('penalty', 0.0) or 0.0)
                if op_pen > 0.0:
                    self.last_lookahead_meta['orbit_penalty_cases'] += 1
                    self.last_lookahead_meta['orbit_penalty_sum'] += float(op_pen)
                np_best = dict(((best.get('ctx') or {}).get('neutral_path_penalty') or {}))
                np_pen = float(np_best.get('penalty', 0.0) or 0.0)
                if np_pen > 0.0:
                    self.last_lookahead_meta['neutral_path_penalty_cases'] += 1
                    self.last_lookahead_meta['neutral_path_penalty_sum'] += float(np_pen)
                pa_best = dict(((best.get('ctx') or {}).get('productive_asymmetry') or {}))
                pa_bonus = float(pa_best.get('bonus', 0.0) or 0.0)
                if pa_bonus > 0.0:
                    self.last_lookahead_meta['productive_asymmetry_cases'] += 1
                    self.last_lookahead_meta['productive_asymmetry_bonus_sum'] += float(pa_bonus)
                fw_best = dict(((best.get('ctx') or {}).get('fixpoint_warning') or {}))
                fw_pen = float(fw_best.get('penalty', 0.0) or 0.0)
                if fw_pen > 0.0:
                    self.last_lookahead_meta['fixpoint_warning_cases'] += 1
                    self.last_lookahead_meta['fixpoint_warning_penalty_sum'] += float(fw_pen)
                play = decanonicalize_uci(str(best.get('action') or ''), flipped)
                return play, "policy", sh, fen_c, str(best.get('action') or '')
            chosen = self.pol.choose(sh, legal_c, side=side_c)
            chosen_s = str(chosen)
            if chosen_s in legal_c:
                play = decanonicalize_uci(chosen_s, flipped)
                return play, "policy", sh, fen_c, chosen_s
        except Exception as e:
            self.last_lookahead_meta['lookahead_errors'] += 1
            self.last_lookahead_meta['lookahead_fallbacks'] += 1
            print(f"[chess3_selective_lookahead] choose_meta fallback: {e!r}", file=sys.stderr)
        chosen_play = str(random.choice(legal))
        chosen_learn = canonicalize_uci(chosen_play, flipped)
        return chosen_play, "policy_fallback_random", sh, fen_c, chosen_learn

    def choose(self, fen: str, legal: List[str], side: str, recent_own_actions: Optional[List[str]] = None, recent_own_pieces: Optional[List[str]] = None) -> str:
        action, _src, _sh, _fen_c, _learn_action = self.choose_meta(fen, legal, side, recent_own_actions=recent_own_actions, recent_own_pieces=recent_own_pieces)
        return str(action)

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if self.pol is None or not items:
            return 0
        self.pol.learn_many(items)
        return len(items)


def _winner_to_outcome(w: Optional[str]) -> str:
    if w == "white":
        return "X"
    if w == "black":
        return "O"
    return "D"


def _side_from_turn(turn: str) -> str:
    return "W" if turn == "w" else "B"


def _side_outcome(outcome: str, side: str) -> int:
    if outcome == "D":
        return 0
    if outcome == "X":
        return 1 if side == "W" else -1
    if outcome == "O":
        return 1 if side == "B" else -1
    return 0


def _trace_repeat_count(index_1based: int, total: int, outcome_sign: int) -> int:
    total = max(1, int(total))
    idx = max(1, int(index_1based))
    if int(outcome_sign) == 0:
        return 1
    max_extra = max(0, _env_int("OROMA_CHESS2_TRACE_MAX_EXTRA_REPEATS", 3))
    if max_extra <= 0 or total <= 1:
        return 1
    frac = float(idx - 1) / float(max(1, total - 1))
    return 1 + int(frac * float(max_extra) + 1e-9)


def _pick_ki_move(g: ChessGame, rng: random.Random) -> str:
    legal = g.legal_uci() or []
    if not legal:
        return ""
    legal = sorted(legal, key=lambda u: (len(u) >= 5, rng.random()), reverse=True)
    return legal[0]


def _build_side_chain(namespace: str, mode: str, side: str, outcome: str, max_plies: int, decision_trace: List[Dict[str, Any]], pass_name: str = "normal") -> Optional[Dict[str, Any]]:
    if not decision_trace:
        return None
    rel = _side_outcome(outcome, side)
    steps: List[Dict[str, Any]] = []
    first = decision_trace[0]
    steps.append({
        "t": 0,
        "state_hash": str(first["state_hash"]),
        "sh": str(first["state_hash"]),
        "fen": str(first["fen"]),
        "ply": int(first["ply"]),
        "side": str(side),
        "mode": str(mode),
        "pass_name": str(pass_name),
    })
    for idx in range(1, len(decision_trace)):
        cur = decision_trace[idx]
        prev = decision_trace[idx - 1]
        steps.append({
            "t": int(idx),
            "state_hash": str(cur["state_hash"]),
            "sh": str(cur["state_hash"]),
            "fen": str(cur["fen"]),
            "a": str(prev["action"]),
            "ply": int(cur["ply"]),
            "side": str(side),
            "mode": str(mode),
        "pass_name": str(pass_name),
        })
    last = decision_trace[-1]
    steps.append({
        "t": int(len(decision_trace)),
        "state_hash": f"chess2:terminal:{side}:{outcome}",
        "sh": f"chess2:terminal:{side}:{outcome}",
        "a": str(last["action"]),
        "fen": str(last["fen_after"]),
        "terminal": "win" if rel > 0 else ("loss" if rel < 0 else "draw"),
        "ply": int(last["ply_after"]),
        "side": str(side),
        "mode": str(mode),
        "pass_name": str(pass_name),
    })
    return {
        "schema_version": "chess2-1",
        "kind": "chess2_policy_trace",
        "origin": str(namespace),
        "namespace": str(namespace),
        "mode": str(mode),
        "pass_name": str(pass_name),
        "side": str(side),
        "result": int(rel),
        "steps_total": int(max(0, len(steps) - 1)),
        "steps": steps,
        "meta": {
            "runner": "tools/chess2_daily_runner.py",
            "source": "chess2_daily_runner",
            "outcome": str(outcome),
            "max_plies": int(max_plies),
            "decision_count": int(len(decision_trace)),
            "state_mode": "mobility",
        },
    }


def _build_learn_items(side: str, outcome: str, decision_trace: List[Dict[str, Any]], win_weight: float = 1.0) -> List[Dict[str, Any]]:
    """Erzeugt Learn-Items für die Universal-Policy.

    `win_weight` verstärkt ausschließlich echte Gewinnpfade leicht, damit
    seltene Explore-Siege nicht komplett von häufigen neutralen Draw-Pfaden
    überdeckt werden. Draws und Verluste bleiben unverändert; dadurch bleibt
    das bereits gelernte Sicherheitswissen erhalten, während gewonnene Linien
    schneller in den tabellarischen Raum einsickern.
    """
    rel = _side_outcome(outcome, side)
    total = len(decision_trace)
    now = int(time.time())
    items: List[Dict[str, Any]] = []
    win_mul = max(1.0, float(win_weight)) if float(rel) > 0.0 else 1.0
    for i, entry in enumerate(decision_trace, start=1):
        reps = _trace_repeat_count(i, total, rel)
        reps_eff = int(max(1, reps))
        if win_mul > 1.0:
            reps_eff = max(1, int(round(float(reps_eff) * float(win_mul))))
        base = {"state_hash": str(entry["state_hash"]), "action": str(entry["action"]), "outcome": float(rel), "ts": now, "side": str(side)}
        for _ in range(reps_eff):
            items.append(dict(base))
    return items


def _fetch_policy_rule_stats(namespace: str, keys: List[Tuple[str, str]]) -> Dict[Tuple[str, str], Tuple[int, float]]:
    """Liest bestehende (n, q)-Werte für eine Menge (state_hash, action)-Paare.

    Der Draw-Stress darf nur auf bereits *bekannte* neutrale Remis-Pfade wirken.
    Deshalb lesen wir vor dem Lernen den aktuellen Reifegrad (`n`) und die
    Neutralität (`q`) der betroffenen Regeln aus `policy_rules`.

    Rückgabe:
      {(state_hash, action): (n, q)}
    """
    out: Dict[Tuple[str, str], Tuple[int, float]] = {}
    if not keys:
        return out
    try:
        uniq: List[Tuple[str, str]] = []
        seen = set()
        for sh, act in keys:
            k = (str(sh), str(act))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(k)
        chunk_size = 300
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(uniq), chunk_size):
                chunk = uniq[i:i + chunk_size]
                where = " OR ".join(["(state_hash=? AND action=?)" for _ in chunk])
                params: List[Any] = [str(namespace)]
                for sh, act in chunk:
                    params.extend([str(sh), str(act)])
                sql = (
                    "SELECT state_hash, action, n, q FROM policy_rules "
                    "WHERE namespace=? AND (" + where + ")"
                )
                for row in cur.execute(sql, params).fetchall() or []:
                    if isinstance(row, dict):
                        sh = str(row.get("state_hash", ""))
                        act = str(row.get("action", ""))
                        n = int(row.get("n", 0) or 0)
                        q = float(row.get("q", 0.0) or 0.0)
                    else:
                        sh = str(row[0])
                        act = str(row[1])
                        n = int(row[2] or 0)
                        q = float(row[3] or 0.0)
                    out[(sh, act)] = (n, q)
    except Exception as e:
        print(f"[chess2_daily_runner] draw-stress stats read failed: {e!r}", file=sys.stderr)
    return out


def _apply_draw_stress(
    namespace: str,
    items: List[Dict[str, Any]],
    stress: float,
    threshold_n: int,
    q_band: float,
    max_extra_per_key: int,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Fügt kleinen Negativdruck auf hochbekannte, neutrale Draw-Pfade hinzu.

    WICHTIG:
    - Kein globaler Draw-Malus
    - Nur für Regeln mit bereits hohem `n` und q≈0
    - Negativdruck wird als *wenige zusätzliche negative Learn-Items* modelliert
      (die Universal Policy kennt nur signierte Outcomes).

    Rückgabe: (items_neu, stress_items, stress_keys)
    """
    if not items or float(stress) <= 0.0 or int(threshold_n) <= 0:
        return items, 0, 0

    draw_counts: Dict[Tuple[str, str], int] = {}
    now = int(time.time())
    for it in items:
        try:
            out = float(it.get("outcome", 0.0) or 0.0)
        except Exception:
            out = 0.0
        if abs(out) > 1e-9:
            continue
        sh = str(it.get("state_hash", "") or "")
        act = str(it.get("action", it.get("action_canon", "")) or "")
        if not sh or not act:
            continue
        k = (sh, act)
        draw_counts[k] = int(draw_counts.get(k, 0) or 0) + 1

    if not draw_counts:
        return items, 0, 0

    stats = _fetch_policy_rule_stats(namespace, list(draw_counts.keys()))
    if not stats:
        return items, 0, 0

    stressed: List[Dict[str, Any]] = list(items)
    stress_items = 0
    stress_keys = 0
    for key, count in draw_counts.items():
        n_cur, q_cur = stats.get(key, (0, 0.0))
        if int(n_cur) < int(threshold_n):
            continue
        if abs(float(q_cur)) > float(q_band):
            continue
        # Stress wächst mit Wiederholungsdichte des Draw-Pfads und logarithmisch
        # mit dessen Bekanntheit. So bleiben neue/seltene Draws unbehelligt,
        # während hochbekannte Remis-Autobahnen leicht unattraktiver werden.
        pressure = float(stress) * float(count) * max(1.0, math.log2(float(n_cur) / float(max(1, threshold_n)) + 1.0))
        extra = int(pressure)
        if extra <= 0 and pressure >= 0.5:
            extra = 1
        extra = max(0, min(int(max_extra_per_key), int(extra)))
        if extra <= 0:
            continue
        sh, act = key
        for _ in range(extra):
            stressed.append({
                "state_hash": str(sh),
                "action": str(act),
                "outcome": -1.0,
                "ts": now,
                "side": "stress",
                "tag": "draw_stress",
            })
        stress_items += int(extra)
        stress_keys += 1
    return stressed, int(stress_items), int(stress_keys)


def run_one_game(rng: random.Random, shim: PolicyShim, mode: str, eps_white: float, eps_black: float, explore_moves_white: int, explore_moves_black: int, max_plies: int, learn: bool, namespace: str, pass_name: str = "normal", win_weight: float = 1.0, opening_seed: Optional[List[str]] = None) -> Dict[str, Any]:
    g = ChessGame()
    plies = 0
    explore_budget = {"W": 0, "B": 0}
    explore_budget_limit = {"W": int(max(0, explore_moves_white)), "B": int(max(0, explore_moves_black))}
    explore_eps = {"W": float(max(0.0, eps_white)), "B": float(max(0.0, eps_black))}
    decision_trace: Dict[str, List[Dict[str, Any]]] = {"W": [], "B": []}
    recent_actions_by_side: Dict[str, List[str]] = {"W": [], "B": []}
    recent_piece_types_by_side: Dict[str, List[str]] = {"W": [], "B": []}
    opening_seed_applied = 0
    if opening_seed:
        seed_plies, seed_recent_actions, seed_recent_piece_types, opening_seed_applied = _apply_opening_seed(g, list(opening_seed))
        plies += int(seed_plies)
        recent_actions_by_side = {"W": list(seed_recent_actions.get("W", [])), "B": list(seed_recent_actions.get("B", []))}
        recent_piece_types_by_side = {"W": list(seed_recent_piece_types.get("W", [])), "B": list(seed_recent_piece_types.get("B", []))}
    decision_source_counts: Dict[str, int] = {}
    special_move_counts: Dict[str, int] = {
        "castles": 0,
        "promotions": 0,
        "en_passant": 0,
        "checks": 0,
    }
    lookahead_counts: Dict[str, Any] = _empty_lookahead_counts()
    invalid_action_fallbacks = 0
    terminal_reason = "draw_unknown"
    while True:
        w = g.winner()
        if w is not None:
            outcome = _winner_to_outcome(w)
            terminal_reason = "winner"
            break
        if plies >= max_plies:
            outcome = "D"
            terminal_reason = "max_plies"
            break
        legal = g.legal_uci() or []
        if not legal:
            outcome = "D"
            terminal_reason = "no_legal"
            break
        fen = g.fen()
        side = _side_from_turn(g.turn)
        fen_c, legal_c, flipped = shim.canonicalize(fen, legal, side)
        fen_before_learn = fen_c
        state_hash = shim.state_hash(fen_c)
        learn_action = ""
        if mode == "ki_vs_ki":
            action = _pick_ki_move(g, rng)
            learn_action = canonicalize_uci(action, flipped)
            source = "ki_heuristic"
        else:
            do_explore = False
            if mode == "explore":
                if explore_budget[side] < explore_budget_limit[side]:
                    do_explore = True
                    explore_budget[side] += 1
                elif rng.random() < explore_eps[side]:
                    do_explore = True
            if do_explore:
                action = str(rng.choice(legal))
                learn_action = canonicalize_uci(action, flipped)
                source = "explore_random"
            else:
                shim._active_mode = str(mode)
                action, source, state_hash, fen_before_learn, learn_action = shim.choose_meta(fen, legal, side=side, recent_own_actions=recent_actions_by_side.get(side, []), recent_own_pieces=recent_piece_types_by_side.get(side, []))
                action = str(action)
                for lk, lv in (getattr(shim, 'last_lookahead_meta', {}) or {}).items():
                    if isinstance(lv, float):
                        lookahead_counts[str(lk)] = float(lookahead_counts.get(str(lk), 0.0) or 0.0) + float(lv or 0.0)
                    else:
                        lookahead_counts[str(lk)] = int(lookahead_counts.get(str(lk), 0) or 0) + int(lv or 0)
        if action not in legal:
            invalid_action_fallbacks += 1
            action = str(rng.choice(legal))
            source = f"{source}:invalid_fallback"
        for key, val in _special_move_counts_for_action(fen, action, side).items():
            special_move_counts[str(key)] = int(special_move_counts.get(str(key), 0) or 0) + int(val or 0)
        if not g.play_uci(action):
            outcome = "D"
            terminal_reason = "invalid_move_runtime"
            break
        recent_actions_by_side.setdefault(side, []).append(str(action))
        if len(recent_actions_by_side[side]) > 6:
            recent_actions_by_side[side] = recent_actions_by_side[side][-6:]
        try:
            src_sq = parse_square(str(action)[:2])
            moved_piece_t = _piece_type(str(parse_fen(fen).board.get(int(src_sq) if src_sq is not None else -1, "") or ""))
            if moved_piece_t:
                recent_piece_types_by_side.setdefault(side, []).append(moved_piece_t)
                if len(recent_piece_types_by_side[side]) > 4:
                    recent_piece_types_by_side[side] = recent_piece_types_by_side[side][-4:]
        except Exception:
            pass
        fen_after = g.fen()
        fen_after_learn = canonicalize_fen(fen_after)[0] if flipped else fen_after
        decision_source_counts[source] = int(decision_source_counts.get(source, 0) or 0) + 1
        decision_trace[side].append({
            "state_hash": state_hash,
            "fen": fen_before_learn,
            "fen_after": fen_after_learn,
            "action": (learn_action or action),
            "played_action": action,
            "ply": int(plies),
            "ply_after": int(plies + 1),
            "side": side,
            "mode": mode,
            "pass_name": str(pass_name),
            "decision_source": source,
        })
        plies += 1
    learn_items: List[Dict[str, Any]] = []
    chains: List[Dict[str, Any]] = []
    for side in ("W", "B"):
        trace = decision_trace.get(side) or []
        if not trace:
            continue
        chain = _build_side_chain(namespace, mode, side, outcome, max_plies, trace, pass_name=pass_name)
        if chain is not None:
            chains.append(chain)
        if learn:
            learn_items.extend(_build_learn_items(side, outcome, trace, win_weight=win_weight))
    return {
        "outcome": outcome,
        "plies": int(plies),
        "learn_items": learn_items,
        "learn_items_count": int(len(learn_items)),
        "chains": chains,
        "draw_by_cap": 1 if (str(outcome) == "D" and str(terminal_reason) == "max_plies") else 0,
        "terminal_reason": str(terminal_reason),
        "invalid_action_fallbacks": int(invalid_action_fallbacks),
        "opening_seed_applied": int(opening_seed_applied),
        "decision_source_counts": dict(decision_source_counts),
        "special_move_counts": dict(special_move_counts),
        "lookahead_counts": dict(lookahead_counts),
    }


def _write_snapchains(payload: Dict[str, Any]) -> int:
    inserted = 0
    ts_now = int(payload.get("ts_end", time.time()) or time.time())
    namespace = str(payload.get("namespace") or "game:chess2")
    mode = str(payload.get("mode") or "chess2")
    for chain in payload.get("chains") or []:
        if not isinstance(chain, dict) or not (chain.get("steps") or []):
            continue
        try:
            blob = json.dumps(chain, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            chain_id = sql_manager.insert_snapchain({
                "ts": ts_now,
                "quality": float(chain.get("result", 0) or 0.0),
                "blob": blob,
                "exported": 0,
                "status": "active",
                "origin": namespace,
                "gap_flag": 0,
                "notes": f"chess2_daily:{mode}:side={chain.get('side','?')}:steps={max(0, len(chain.get('steps') or []) - 1)}",
                "namespace": namespace,
                "source_id": None,
                "version": "chess2_daily_runner:v3.8-r2",
                "weight": 1.0,
            })
            if chain_id:
                inserted += 1
        except Exception as e:
            print(f"[chess2_daily_runner] snapchain write failed: {e!r}", file=sys.stderr)
    return inserted


def _namespace_phase2_identity(namespace: str) -> Dict[str, Any]:
    """Leitet stabile Chess2/Chess3-Kennzeichen aus der Namespace ab.

    Phase 2 von Chess3 soll noch ohne selektiven Lookahead starten, aber bereits
    sauber messbar sein. Deshalb erzeugen wir hier bewusst eine minimale,
    produktive Identitätsschicht, die sowohl im Abschluss-JSON als auch in den
    episodic_metrics sichtbar wird:

    - `engine_line` trennt Chess2 vs. Chess3 logisch,
    - `is_chess3` erlaubt einfache bool-/SQL-Filter,
    - `lookahead_profile` dokumentiert explizit, dass diese Phase noch ohne
      Lookahead läuft.

    Die Funktion ist absichtlich defensiv und rein namespace-basiert gehalten,
    damit bestehende Chess2-Runs unverändert weiterlaufen und Chess3 ohne
    zusätzliche CLI-Flags sofort erkennbar ist.
    """
    ns = str(namespace or "").strip()
    ns_lower = ns.lower()
    if ns_lower.startswith("game:chess3"):
        engine_line = "chess3"
    elif ns_lower.startswith("game:chess2"):
        engine_line = "chess2"
    else:
        engine_line = "chess"
    chess3 = engine_line == "chess3"
    return {
        "engine_line": engine_line,
        "is_chess3": 1 if chess3 else 0,
        "lookahead_enabled": 1 if chess3 else 0,
        "lookahead_profile": "active_selective_v1" if chess3 else "none",
    }



def _apply_phase2_batch_defaults(batch: Dict[str, Any], namespace: str) -> Dict[str, Any]:
    """Ergänzt Chess3-Phase-2-Telemetrie deterministisch und minimal-invasiv.

    Der Batch erhält dabei ausschließlich additive Felder. Bestehende Werte
    bleiben unangetastet, damit spätere Lookahead-Patches diese Zähler direkt
    überschreiben oder fortschreiben können.
    """
    ident = _namespace_phase2_identity(namespace)
    for key, value in ident.items():
        batch.setdefault(key, value)
    for key, value in {
        "lookahead_2ply_used": 0,
        "lookahead_3ply_used": 0,
        "lookahead_king_pressure_cases": 0,
        "lookahead_endgame_cases": 0,
        "lookahead_check_cases": 0,
        "lookahead_capture_cases": 0,
        "lookahead_agreement_count": 0,
        "lookahead_correction_count": 0,
        "lookahead_bonus_sum": 0.0,
        "lookahead_penalty_sum": 0.0,
        "lookahead_errors": 0,
        "lookahead_fallbacks": 0,
        "line_pressure_cases": 0,
        "line_pressure_bonus_sum": 0.0,
        "line_pressure_opening_cases": 0,
        "line_pressure_middlegame_cases": 0,
        "line_pressure_endgame_cases": 0,
        "line_pressure_errors": 0,
        "defense_disruption_cases": 0,
        "defense_disruption_bonus_sum": 0.0,
        "defense_disruption_king_zone_cases": 0,
        "defense_disruption_outpost_cases": 0,
        "defense_disruption_color_complex_cases": 0,
        "defense_disruption_errors": 0,
        "lookahead_conversion_bonus_sum": 0.0,
        "penalty_damper_cases": 0,
        "penalty_damper_sum": 0.0,
    }.items():
        batch.setdefault(key, value)
    return batch



def _db_write_episode(batch: Dict[str, Any], kind: str) -> bool:
    try:
        # Side-Daily-Jobs nutzen gezielt `--policy-games 0`, damit pro
        # Tagesprofil exakt eine lernende Explore-Partie entsteht. Null-Batches
        # dürfen deshalb keine leeren Episode-Zeilen erzeugen, sonst würde die
        # Daily Summary scheinbar zusätzliche Chess-Durchläufe anzeigen.
        if int(batch.get("games") or 0) <= 0:
            return True
        ts_start = int(batch.get("ts_start") or time.time())
        ts_end = int(batch.get("ts_end") or ts_start)
        label = str(batch.get("label") or kind)
        eid = sql_manager.insert_episode(ts_start=ts_start, ts_end=ts_end, kind=kind, source=str(batch.get("source") or "orchestrator"), label=label, meta={
            "namespace": batch.get("namespace"),
            "mode": batch.get("mode"),
            "runner": batch.get("runner"),
            "side_profile": batch.get("side_profile"),
            "eps": batch.get("eps"),
            "chains_count": batch.get("chains_count"),
            "learn_items_count": batch.get("learn_items_count"),
            "bootstrap_items": batch.get("bootstrap_items"),
            "draw_stress_items": batch.get("draw_stress_items"),
            "draw_stress_keys": batch.get("draw_stress_keys"),
            "win_weight": batch.get("win_weight"),
            "engine_line": batch.get("engine_line"),
            "is_chess3": batch.get("is_chess3"),
            "lookahead_enabled": batch.get("lookahead_enabled"),
            "lookahead_profile": batch.get("lookahead_profile"),
        })
        if not eid:
            return False
        # Chess2 schreibt bewusst sowohl die alten X/O-Schlüssel als auch die
        # schachspezifischen White/Black-Schlüssel. Dadurch bleiben bestehende
        # Auswertungen kompatibel, während neue UIs und SQL-Abfragen sofort
        # semantisch saubere Metriken lesen können.
        for key, val in {
            "games": batch.get("games") or 0,
            "wins_x": batch.get("wins_x") or 0,
            "wins_o": batch.get("wins_o") or 0,
            "wins_white": batch.get("wins_white") or 0,
            "wins_black": batch.get("wins_black") or 0,
            "draws": batch.get("draws") or 0,
            "draws_by_cap": batch.get("draws_by_cap") or 0,
            "avg_moves": batch.get("avg_moves") or 0,
            "avg_game_ms": batch.get("avg_game_ms") or 0,
            "duration_ms": batch.get("duration_ms") or 0,
            "eps": batch.get("eps") or 0,
            "flip_pass": batch.get("flip_pass") or 0,
            "eps_white": batch.get("eps_white") or 0,
            "eps_black": batch.get("eps_black") or 0,
            "explore_moves_white": batch.get("explore_moves_white") or 0,
            "explore_moves_black": batch.get("explore_moves_black") or 0,
            "policy_enabled": batch.get("policy_enabled") or 0,
            "chains_count": batch.get("chains_count") or 0,
            "learn_items_count": batch.get("learn_items_count") or 0,
            "bootstrap_items": batch.get("bootstrap_items") or 0,
            "max_plies_seen": batch.get("max_plies_seen") or 0,
            "invalid_action_fallbacks": batch.get("invalid_action_fallbacks") or 0,
            "terminal_winner": batch.get("terminal_winner") or 0,
            "terminal_max_plies": batch.get("terminal_max_plies") or 0,
            "terminal_no_legal": batch.get("terminal_no_legal") or 0,
            "terminal_invalid_move_runtime": batch.get("terminal_invalid_move_runtime") or 0,
            "src_policy": batch.get("src_policy") or 0,
            "src_policy_fallback_random": batch.get("src_policy_fallback_random") or 0,
            "src_explore_random": batch.get("src_explore_random") or 0,
            "src_no_policy_random": batch.get("src_no_policy_random") or 0,
            "src_ki_heuristic": batch.get("src_ki_heuristic") or 0,
            "castles": batch.get("castles") or 0,
            "promotions": batch.get("promotions") or 0,
            "en_passant": batch.get("en_passant") or 0,
            "checks": batch.get("checks") or 0,
            "win_weight": batch.get("win_weight") or 0,
            "is_chess3": batch.get("is_chess3") or 0,
            "lookahead_enabled": batch.get("lookahead_enabled") or 0,
            "lookahead_2ply_used": batch.get("lookahead_2ply_used") or 0,
            "lookahead_3ply_used": batch.get("lookahead_3ply_used") or 0,
            "lookahead_king_pressure_cases": batch.get("lookahead_king_pressure_cases") or 0,
            "lookahead_endgame_cases": batch.get("lookahead_endgame_cases") or 0,
            "lookahead_check_cases": batch.get("lookahead_check_cases") or 0,
            "lookahead_capture_cases": batch.get("lookahead_capture_cases") or 0,
            "lookahead_agreement_count": batch.get("lookahead_agreement_count") or 0,
            "lookahead_correction_count": batch.get("lookahead_correction_count") or 0,
            "lookahead_bonus_sum": batch.get("lookahead_bonus_sum") or 0,
            "lookahead_penalty_sum": batch.get("lookahead_penalty_sum") or 0,
            "lookahead_errors": batch.get("lookahead_errors") or 0,
            "lookahead_fallbacks": batch.get("lookahead_fallbacks") or 0,
            "line_pressure_cases": batch.get("line_pressure_cases") or 0,
            "line_pressure_bonus_sum": batch.get("line_pressure_bonus_sum") or 0,
            "line_pressure_opening_cases": batch.get("line_pressure_opening_cases") or 0,
            "line_pressure_middlegame_cases": batch.get("line_pressure_middlegame_cases") or 0,
            "line_pressure_endgame_cases": batch.get("line_pressure_endgame_cases") or 0,
            "line_pressure_errors": batch.get("line_pressure_errors") or 0,
            "defense_disruption_cases": batch.get("defense_disruption_cases") or 0,
            "defense_disruption_bonus_sum": batch.get("defense_disruption_bonus_sum") or 0,
            "defense_disruption_king_zone_cases": batch.get("defense_disruption_king_zone_cases") or 0,
            "defense_disruption_outpost_cases": batch.get("defense_disruption_outpost_cases") or 0,
            "defense_disruption_color_complex_cases": batch.get("defense_disruption_color_complex_cases") or 0,
            "defense_disruption_errors": batch.get("defense_disruption_errors") or 0,
            "lookahead_conversion_bonus_sum": batch.get("lookahead_conversion_bonus_sum") or 0,
            "penalty_damper_cases": batch.get("penalty_damper_cases") or 0,
            "penalty_damper_sum": batch.get("penalty_damper_sum") or 0,
        }.items():
            try:
                sql_manager.insert_episodic_metric(int(eid), int(ts_end), str(key), float(val))
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"[chess2_daily_runner] DB write failed: {e!r}", file=sys.stderr)
        return False


def run_batch(rng: random.Random, shim: PolicyShim, games: int, mode: str, eps_white: float, eps_black: float, explore_moves_white: int, explore_moves_black: int, max_plies: int, learn: bool, label: str, source: str, pass_name: str = "normal", draw_stress: float = 0.0, draw_stress_threshold_n: int = 0, draw_stress_q_band: float = 0.05, draw_stress_max_extra_per_key: int = 3, win_weight: float = 1.0, opening_seed_book: bool = False, opening_seed_policy_only: bool = True, opening_seed_offset: int = 0, side_profile: str = "auto") -> Dict[str, Any]:
    """Führt einen Chess2-Batch aus und sammelt Diagnosemetriken.

    Der aktuelle Chess2-Fokus ist nicht nur "mehr Spiele", sondern die Frage,
    ob die Mobility-Repräsentation bereits sauber über die Universal Policy
    entscheidet und warum Policy-Läufe teils deutlich kürzer wirken als
    Explore-Läufe. Deshalb aggregieren wir hier bewusst zusätzliche Signale:

      • draws_by_cap               -> wie oft endet ein Spiel nur am Ply-Limit?
      • max_plies_seen             -> ob Batches das Limit tatsächlich erreichen
      • terminal_*                 -> echter Abbruchgrund
      • src_policy / src_explore   -> wie viele Züge kamen aus welcher Quelle?
      • invalid_action_fallbacks   -> Policy/Runtime-Lücken sichtbar machen

    Diese Werte landen sowohl im Abschluss-JSON als auch in episodic_metrics,
    damit UI, SQL-Analyse und Log-Inspektion denselben Befund sehen.
    """
    t0 = time.time()
    wins_x = wins_o = draws = 0
    wins_white = wins_black = 0
    draws_by_cap = 0
    max_plies_seen = 0
    total_plies = 0
    total_learn_items = 0
    total_invalid_action_fallbacks = 0
    terminal_reason_counts: Dict[str, int] = {}
    decision_source_counts: Dict[str, int] = {}
    special_move_counts: Dict[str, int] = {
        "castles": 0,
        "promotions": 0,
        "en_passant": 0,
        "checks": 0,
    }
    lookahead_counts: Dict[str, Any] = _empty_lookahead_counts()
    chains: List[Dict[str, Any]] = []
    learn_buffer: List[Dict[str, Any]] = []
    learn_game = bool(learn) or (str(mode) == "policy" and float(draw_stress) > 0.0)
    opening_seed_games = 0
    opening_seed_variants: Set[int] = set()
    for game_idx in range(max(0, int(games))):
        opening_seed: Optional[List[str]] = None
        if bool(opening_seed_book) and (str(mode) == "policy" or not bool(opening_seed_policy_only)):
            seed_idx = int(opening_seed_offset) + int(game_idx)
            opening_seed = _select_opening_seed(seed_idx)
            if opening_seed:
                opening_seed_games += 1
                opening_seed_variants.add(seed_idx % max(1, len(_OPENING_SEED_BOOK)))
        r = run_one_game(rng, shim, mode, eps_white if mode == "explore" else 0.0, eps_black if mode == "explore" else 0.0, explore_moves_white if mode == "explore" else 0, explore_moves_black if mode == "explore" else 0, max_plies, learn_game, shim.namespace, pass_name=pass_name, win_weight=win_weight, opening_seed=opening_seed)
        plies = int(r.get("plies") or 0)
        total_plies += plies
        max_plies_seen = max(max_plies_seen, plies)
        total_learn_items += int(r.get("learn_items_count") or 0)
        total_invalid_action_fallbacks += int(r.get("invalid_action_fallbacks") or 0)
        draws_by_cap += int(r.get("draw_by_cap") or 0)
        term = str(r.get("terminal_reason") or "unknown")
        terminal_reason_counts[term] = int(terminal_reason_counts.get(term, 0) or 0) + 1
        for src, cnt in (r.get("decision_source_counts") or {}).items():
            decision_source_counts[str(src)] = int(decision_source_counts.get(str(src), 0) or 0) + int(cnt or 0)
        for key, cnt in (r.get("special_move_counts") or {}).items():
            special_move_counts[str(key)] = int(special_move_counts.get(str(key), 0) or 0) + int(cnt or 0)
        for key, cnt in (r.get("lookahead_counts") or {}).items():
            if isinstance(cnt, float):
                lookahead_counts[str(key)] = float(lookahead_counts.get(str(key), 0.0) or 0.0) + float(cnt or 0.0)
            else:
                lookahead_counts[str(key)] = int(lookahead_counts.get(str(key), 0) or 0) + int(cnt or 0)
        chains.extend([c for c in (r.get("chains") or []) if isinstance(c, dict)])
        learn_buffer.extend([i for i in (r.get("learn_items") or []) if isinstance(i, dict)])
        oc = str(r.get("outcome") or "D")
        if oc == "X":
            wins_x += 1
            wins_white += 1
        elif oc == "O":
            wins_o += 1
            wins_black += 1
        else:
            draws += 1
    draw_stress_items = 0
    draw_stress_keys = 0
    # Draw-Stress wirkt absichtlich auf echte Policy-Traces. Deshalb sammeln wir
    # im Policy-Modus bei aktivem Stress ebenfalls Learn-Items ein, wenden dann
    # aber ausschließlich den gezielten Negativdruck auf bekannte neutrale
    # Remis-Pfade an.
    if learn_buffer and str(mode) == "policy" and float(draw_stress) > 0.0:
        learn_buffer, draw_stress_items, draw_stress_keys = _apply_draw_stress(
            str(shim.namespace),
            learn_buffer,
            float(draw_stress),
            int(draw_stress_threshold_n),
            float(draw_stress_q_band),
            int(draw_stress_max_extra_per_key),
        )
    if learn_buffer:
        shim.learn_many(learn_buffer)
    chains_written = _write_snapchains({"ts_end": int(time.time()), "namespace": shim.namespace, "mode": mode, "chains": chains})
    dt = max(0.0, time.time() - t0)
    batch_out = {
        "ts_start": int(t0),
        "ts_end": int(time.time()),
        "duration_ms": int(dt * 1000.0),
        "games": int(games),
        "wins_x": int(wins_x),
        "wins_o": int(wins_o),
        "wins_white": int(wins_white),
        "wins_black": int(wins_black),
        "draws": int(draws),
        "draws_by_cap": int(draws_by_cap),
        "avg_moves": float(total_plies / max(1, games)),
        "avg_game_ms": float((dt * 1000.0) / max(1, games)),
        "max_plies_seen": int(max_plies_seen),
        "mode": str(mode),
        "pass_name": str(pass_name),
        "side_profile": _sanitize_side_profile(side_profile),
        "namespace": shim.namespace,
        "policy_enabled": 1.0 if shim.pol else 0.0,
        "eps": float(max(eps_white, eps_black) if mode == "explore" else 0.0),
        "eps_white": float(eps_white if mode == "explore" else 0.0),
        "eps_black": float(eps_black if mode == "explore" else 0.0),
        "explore_moves_white": int(explore_moves_white if mode == "explore" else 0),
        "explore_moves_black": int(explore_moves_black if mode == "explore" else 0),
        "flip_pass": 1 if str(pass_name) == "flip" else 0,
        "learn": bool(learn),
        "source": str(source),
        "label": str(label),
        "runner": "tools/chess2_daily_runner.py",
        "learn_items_count": int(total_learn_items),
        "chains_count": int(chains_written),
        "invalid_action_fallbacks": int(total_invalid_action_fallbacks),
        "terminal_winner": int(terminal_reason_counts.get("winner", 0) or 0),
        "terminal_max_plies": int(terminal_reason_counts.get("max_plies", 0) or 0),
        "terminal_no_legal": int(terminal_reason_counts.get("no_legal", 0) or 0),
        "terminal_invalid_move_runtime": int(terminal_reason_counts.get("invalid_move_runtime", 0) or 0),
        "src_policy": int(decision_source_counts.get("policy", 0) or 0),
        "src_policy_fallback_random": int(decision_source_counts.get("policy_fallback_random", 0) or 0),
        "src_explore_random": int(decision_source_counts.get("explore_random", 0) or 0),
        "src_no_policy_random": int(decision_source_counts.get("no_policy_random", 0) or 0),
        "src_ki_heuristic": int(decision_source_counts.get("ki_heuristic", 0) or 0),
        "castles": int(special_move_counts.get("castles", 0) or 0),
        "promotions": int(special_move_counts.get("promotions", 0) or 0),
        "en_passant": int(special_move_counts.get("en_passant", 0) or 0),
        "checks": int(special_move_counts.get("checks", 0) or 0),
        "draw_stress_items": int(draw_stress_items),
        "draw_stress_keys": int(draw_stress_keys),
        "opening_seed_games": int(opening_seed_games),
        "opening_seed_variants": int(len(opening_seed_variants)),
        "lookahead_2ply_used": int(lookahead_counts.get("lookahead_2ply_used", 0) or 0),
        "lookahead_3ply_used": int(lookahead_counts.get("lookahead_3ply_used", 0) or 0),
        "lookahead_king_pressure_cases": int(lookahead_counts.get("lookahead_king_pressure_cases", 0) or 0),
        "lookahead_endgame_cases": int(lookahead_counts.get("lookahead_endgame_cases", 0) or 0),
        "lookahead_check_cases": int(lookahead_counts.get("lookahead_check_cases", 0) or 0),
        "lookahead_capture_cases": int(lookahead_counts.get("lookahead_capture_cases", 0) or 0),
        "lookahead_agreement_count": int(lookahead_counts.get("lookahead_agreement_count", 0) or 0),
        "lookahead_correction_count": int(lookahead_counts.get("lookahead_correction_count", 0) or 0),
        "lookahead_bonus_sum": float(lookahead_counts.get("lookahead_bonus_sum", 0.0) or 0.0),
        "lookahead_penalty_sum": float(lookahead_counts.get("lookahead_penalty_sum", 0.0) or 0.0),
        "lookahead_errors": int(lookahead_counts.get("lookahead_errors", 0) or 0),
        "lookahead_fallbacks": int(lookahead_counts.get("lookahead_fallbacks", 0) or 0),
        "line_pressure_cases": int(lookahead_counts.get("line_pressure_cases", 0) or 0),
        "line_pressure_bonus_sum": float(lookahead_counts.get("line_pressure_bonus_sum", 0.0) or 0.0),
        "line_pressure_opening_cases": int(lookahead_counts.get("line_pressure_opening_cases", 0) or 0),
        "line_pressure_middlegame_cases": int(lookahead_counts.get("line_pressure_middlegame_cases", 0) or 0),
        "line_pressure_endgame_cases": int(lookahead_counts.get("line_pressure_endgame_cases", 0) or 0),
        "line_pressure_errors": int(lookahead_counts.get("line_pressure_errors", 0) or 0),
        "defense_disruption_cases": int(lookahead_counts.get("defense_disruption_cases", 0) or 0),
        "defense_disruption_bonus_sum": float(lookahead_counts.get("defense_disruption_bonus_sum", 0.0) or 0.0),
        "defense_disruption_king_zone_cases": int(lookahead_counts.get("defense_disruption_king_zone_cases", 0) or 0),
        "defense_disruption_outpost_cases": int(lookahead_counts.get("defense_disruption_outpost_cases", 0) or 0),
        "defense_disruption_color_complex_cases": int(lookahead_counts.get("defense_disruption_color_complex_cases", 0) or 0),
        "defense_disruption_errors": int(lookahead_counts.get("defense_disruption_errors", 0) or 0),
        "lookahead_conversion_bonus_sum": float(lookahead_counts.get("lookahead_conversion_bonus_sum", 0.0) or 0.0),
        "penalty_damper_cases": int(lookahead_counts.get("penalty_damper_cases", 0) or 0),
        "penalty_damper_sum": float(lookahead_counts.get("penalty_damper_sum", 0.0) or 0.0),
        "anti_flat_cases": int(lookahead_counts.get("anti_flat_cases", 0) or 0),
        "anti_flat_penalty_sum": float(lookahead_counts.get("anti_flat_penalty_sum", 0.0) or 0.0),
        "trade_without_gain_cases": int(lookahead_counts.get("trade_without_gain_cases", 0) or 0),
        "trade_without_gain_penalty_sum": float(lookahead_counts.get("trade_without_gain_penalty_sum", 0.0) or 0.0),
        "asymmetry_keep_cases": int(lookahead_counts.get("asymmetry_keep_cases", 0) or 0),
        "asymmetry_keep_bonus_sum": float(lookahead_counts.get("asymmetry_keep_bonus_sum", 0.0) or 0.0),
        "worst_piece_improve_cases": int(lookahead_counts.get("worst_piece_improve_cases", 0) or 0),
        "worst_piece_improve_bonus_sum": float(lookahead_counts.get("worst_piece_improve_bonus_sum", 0.0) or 0.0),
    }
    return _apply_phase2_batch_defaults(batch_out, shim.namespace)


def _namespace_rule_count(namespace: str) -> int:
    """Liefert den echten Regelbestand eines Namespace robust für tuple- UND dict-Rows.

    Hintergrund des Fixes:
    ---------------------
    `core.sql_manager.get_conn()` verwendet in ORÓMA standardmäßig eine dict-artige
    Row-Factory. In dieser Konfiguration liefert `fetchone()` also z. B.
    `{"COUNT(*)": 58909}` statt eines tuple-artigen `(58909,)`.

    Die bisherige Implementierung griff jedoch immer per `row[0]` zu. Das wirft
    bei dict-Rows einen `KeyError: 0`, der hier vom broad `except` verschluckt
    wurde. Ergebnis: Der Runner meldete fälschlich `0` Regeln, obwohl die reale
    DB bereits zehntausende `game:chess2`-Regeln enthielt. Dadurch wurden sowohl
    die Diagnosefelder `policy_rules_before/after` als auch die
    Bootstrap-"if-empty"-Prüfung verfälscht.

    Dieser Helper akzeptiert deshalb jetzt explizit beide Row-Typen:
      • dict/sqlite3.Row-ähnlich  -> Zugriff über ersten Value bzw. bekannte Keys
      • tuple/list                -> Zugriff über Index 0
      • skalare Rückgabe          -> direkter int()-Versuch

    Dadurch sieht der Runner wieder denselben realen Bestand wie direkte
    SQLite-Abfragen auf `/opt/ai/oroma/data/oroma.db`.
    """
    try:
        with sql_manager.get_conn(None) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM policy_rules WHERE namespace=?",
                (namespace,),
            ).fetchone()
            if row is None:
                return 0
            if hasattr(row, 'keys'):
                try:
                    if 'cnt' in row:
                        return int(row['cnt'] or 0)
                except Exception:
                    pass
                try:
                    values = list(row.values())  # dict-row in sql_manager
                    if values:
                        return int(values[0] or 0)
                except Exception:
                    pass
            if isinstance(row, (tuple, list)):
                return int(row[0] if row else 0)
            return int(row)
    except Exception:
        return 0


def _iter_old_chess_snapchains(limit: int) -> Iterable[Tuple[int, Dict[str, Any]]]:
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, blob FROM snapchains WHERE origin='game:chess' OR namespace='game:chess' ORDER BY id DESC LIMIT ?", (int(max(1, limit)),))
            for row in cur.fetchall() or []:
                try:
                    payload = json.loads(row[1] if not hasattr(row, 'keys') else row['blob'])
                    if isinstance(payload, dict):
                        yield int(row[0] if not hasattr(row, 'keys') else row['id']), payload
                except Exception:
                    continue
    except Exception:
        return


def _extract_old_moves_and_outcome(payload: Dict[str, Any]) -> Tuple[List[str], float]:
    moves: List[str] = []
    outcome = 0.0
    for p in payload.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        md = p.get("metadata") or {}
        uci = (md.get("uci") or "").strip()
        if uci:
            moves.append(uci)
        if md.get("outcome") is not None:
            try:
                outcome = float(md.get("outcome") or 0.0)
            except Exception:
                outcome = 0.0
    return moves, outcome


def bootstrap_from_old_chess(shim: PolicyShim, rng: random.Random) -> Dict[str, Any]:
    enabled = _env_bool("OROMA_CHESS2_BOOTSTRAP_FROM_CHESS", True)
    if not enabled or shim.pol is None:
        return {"attempted": False, "seeded": 0, "chains": 0, "skipped": "disabled_or_no_policy"}
    empty_only = _env_bool("OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY", True)
    if empty_only and _namespace_rule_count(shim.namespace) > 0:
        return {"attempted": False, "seeded": 0, "chains": 0, "skipped": "namespace_not_empty"}
    max_chains = max(1, _env_int("OROMA_CHESS2_BOOTSTRAP_MAX_CHAINS", 200))
    weight = min(1.0, max(0.0, _env_float("OROMA_CHESS2_BOOTSTRAP_WEIGHT", 0.30)))
    items: List[Dict[str, Any]] = []
    chains_used = 0
    now = int(time.time())
    for _cid, payload in _iter_old_chess_snapchains(max_chains):
        moves, white_outcome = _extract_old_moves_and_outcome(payload)
        if not moves:
            continue
        g = ChessGame()
        chain_items = 0
        for uci in moves:
            fen = g.fen()
            legal = g.legal_uci() or []
            if uci not in legal:
                break
            if weight < 1.0 and rng.random() > weight:
                g.play_uci(uci)
                continue
            side = _side_from_turn(g.turn)
            rel = float(white_outcome if side == "W" else -white_outcome)
            items.append({"state_hash": shim.state_hash(fen), "action": canonicalize_uci(uci, canonicalize_fen(fen)[1]) if shim.canon_mode else uci, "outcome": rel, "ts": now, "side": side})
            chain_items += 1
            if not g.play_uci(uci):
                break
        if chain_items > 0:
            chains_used += 1
    if items:
        shim.learn_many(items)
    return {"attempted": True, "seeded": len(items), "chains": chains_used, "skipped": None}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_CHESS2_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_CHESS2_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_CHESS2_EPS", 0.08))
    ap.add_argument("--eps-white", type=float, default=_env_float("OROMA_CHESS2_EPS_WHITE", _env_float("OROMA_CHESS2_EPS", 0.08)))
    ap.add_argument("--eps-black", type=float, default=_env_float("OROMA_CHESS2_EPS_BLACK", _env_float("OROMA_CHESS2_EPS", 0.08)))
    _explore_moves_base = _env_int("OROMA_CHESS2_EXPLORE_MOVES_PER_GAME", 2)
    _explore_moves_black_extra = _env_int("OROMA_CHESS2_EXPLORE_MOVES_BLACK_EXTRA", 0)
    ap.add_argument("--explore-moves-white", type=int, default=_env_int("OROMA_CHESS2_EXPLORE_MOVES_WHITE", _explore_moves_base))
    ap.add_argument("--explore-moves-black", type=int, default=_env_int("OROMA_CHESS2_EXPLORE_MOVES_BLACK", _explore_moves_base + _explore_moves_black_extra))
    ap.add_argument("--max-plies", type=int, default=_env_int("OROMA_CHESS2_MAX_PLIES", 180))
    _default_namespace = _env_str("OROMA_CHESS2_POLICY_NAMESPACE", "game:chess2")
    _default_canon = _env_bool("OROMA_CHESS2_CANONICAL", _default_namespace.endswith("_canon"))
    _default_flip = _env_bool("OROMA_CHESS2_ENABLE_FLIP_PASS", not _default_canon)
    ap.add_argument("--enable-flip-pass", type=int, default=1 if _default_flip else 0)
    ap.add_argument("--side-profile", type=str, default=_env_str("OROMA_CHESS2_SIDE_PROFILE", "auto"), help="auto|white|black; black uses the primary flip perspective for daily color alternation")
    ap.add_argument("--flip-policy-games", type=int, default=_env_int("OROMA_CHESS2_FLIP_POLICY_GAMES", _env_int("OROMA_CHESS2_POLICY_GAMES", 100)))
    ap.add_argument("--flip-explore-games", type=int, default=_env_int("OROMA_CHESS2_FLIP_EXPLORE_GAMES", _env_int("OROMA_CHESS2_EXPLORE_GAMES", 100)))
    ap.add_argument("--namespace", type=str, default=_default_namespace)
    ap.add_argument("--canonical", type=int, default=1 if _default_canon else 0)
    _default_coop = _env_bool("OROMA_CHESS2_COOPERATION", str(_default_namespace).endswith("_coop") or str(_default_namespace).endswith("_coop_king") or str(_default_namespace).endswith("_territory"))
    ap.add_argument("--cooperation", type=int, default=1 if _default_coop else 0)
    _default_king = _env_bool("OROMA_CHESS2_KING_WEIGHT", str(_default_namespace).endswith("_king") or str(_default_namespace).endswith("_territory"))
    ap.add_argument("--king", type=int, default=1 if _default_king else 0)
    _default_territory = _env_bool("OROMA_CHESS2_TERRITORY", str(_default_namespace).endswith("_territory"))
    ap.add_argument("--territory", type=int, default=1 if _default_territory else 0)
    ap.add_argument("--draw-stress", type=float, default=_env_float("OROMA_CHESS2_DRAW_STRESS", 0.0))
    ap.add_argument("--draw-stress-threshold-n", type=int, default=_env_int("OROMA_CHESS2_DRAW_STRESS_THRESHOLD_N", 8))
    ap.add_argument("--draw-stress-q-band", type=float, default=_env_float("OROMA_CHESS2_DRAW_STRESS_Q_BAND", 0.05))
    ap.add_argument("--draw-stress-max-extra-per-key", type=int, default=_env_int("OROMA_CHESS2_DRAW_STRESS_MAX_EXTRA_PER_KEY", 3))
    ap.add_argument("--win-weight", type=float, default=_env_float("OROMA_CHESS2_WIN_WEIGHT", 1.0))
    ap.add_argument("--capture-bias", type=float, default=_env_float("OROMA_CHESS2_CAPTURE_BIAS", 0.12))
    ap.add_argument("--king-shuffle-penalty", type=float, default=_env_float("OROMA_CHESS2_KING_SHUFFLE_PENALTY", 0.10))
    ap.add_argument("--piece-variety-bias", type=float, default=_env_float("OROMA_CHESS2_PIECE_VARIETY_BIAS", 0.04))
    ap.add_argument("--hanging-piece-bias", type=float, default=_env_float("OROMA_CHESS2_HANGING_PIECE_BIAS", 0.18))
    ap.add_argument("--underdefended-piece-bias", type=float, default=_env_float("OROMA_CHESS2_UNDERDEFENDED_PIECE_BIAS", 0.08))
    ap.add_argument("--opening-seed-book", type=int, default=1 if _env_bool("OROMA_CHESS2_OPENING_SEED_BOOK", True) else 0)
    ap.add_argument("--opening-seed-policy-only", type=int, default=1 if _env_bool("OROMA_CHESS2_OPENING_SEED_POLICY_ONLY", True) else 0)
    ap.add_argument("--self-hanging-penalty", type=float, default=_env_float("OROMA_CHESS2_SELF_HANGING_PENALTY", 0.24))
    ap.add_argument("--retaliation-penalty", type=float, default=_env_float("OROMA_CHESS2_RETALIATION_PENALTY", 0.18))
    ap.add_argument("--defended-attack-bonus", type=float, default=_env_float("OROMA_CHESS2_DEFENDED_ATTACK_BONUS", 0.06))
    ap.add_argument("--discovery-exposure-penalty", type=float, default=_env_float("OROMA_CHESS2_DISCOVERY_EXPOSURE_PENALTY", 0.12))
    ap.add_argument("--castle-bias", type=float, default=_env_float("OROMA_CHESS2_CASTLE_BIAS", 0.22))
    ap.add_argument("--promotion-bias", type=float, default=_env_float("OROMA_CHESS2_PROMOTION_BIAS", 0.55))
    ap.add_argument("--en-passant-bias", type=float, default=_env_float("OROMA_CHESS2_EN_PASSANT_BIAS", 0.10))
    ap.add_argument("--check-bias", type=float, default=_env_float("OROMA_CHESS2_CHECK_BIAS", 0.06))
    ap.add_argument("--line-pressure-bias", type=float, default=_env_float("OROMA_CHESS2_LINE_PRESSURE_BIAS", 0.02))
    ap.add_argument("--line-pressure-middlegame-lift", type=float, default=_env_float("OROMA_CHESS2_LINE_PRESSURE_MIDDLEGAME_LIFT", 1.50))
    ap.add_argument("--defense-disruption-bias", type=float, default=_env_float("OROMA_CHESS2_DEFENSE_DISRUPTION_BIAS", 0.065))
    ap.add_argument("--lookahead-conversion-bias", type=float, default=_env_float("OROMA_CHESS2_LOOKAHEAD_CONVERSION_BIAS", 0.09))
    ap.add_argument("--penalty-damper-ratio", type=float, default=_env_float("OROMA_CHESS2_PENALTY_DAMPER_RATIO", 0.24))
    ap.add_argument("--aggro", type=float, default=_env_float("OROMA_CHESS2_AGGRO", 1.0))
    args = ap.parse_args(argv)
    side_profile = _sanitize_side_profile(getattr(args, "side_profile", "auto"))
    seed = int(args.seed) if int(args.seed) != 0 else int(time.time())
    rng = random.Random(seed)
    canon_mode = bool(int(args.canonical))
    cooperation_mode = bool(int(args.cooperation))
    king_mode = bool(int(args.king))
    territory_mode = bool(int(args.territory))
    draw_stress = float(args.draw_stress)
    draw_stress_threshold_n = int(args.draw_stress_threshold_n)
    draw_stress_q_band = float(args.draw_stress_q_band)
    draw_stress_max_extra_per_key = int(args.draw_stress_max_extra_per_key)
    win_weight = max(1.0, float(args.win_weight))
    capture_bias = max(0.0, float(args.capture_bias))
    king_shuffle_penalty = max(0.0, float(args.king_shuffle_penalty))
    piece_variety_bias = max(0.0, float(args.piece_variety_bias))
    hanging_piece_bias = max(0.0, float(args.hanging_piece_bias))
    underdefended_piece_bias = max(0.0, float(args.underdefended_piece_bias))
    opening_seed_book = bool(int(args.opening_seed_book))
    opening_seed_policy_only = bool(int(args.opening_seed_policy_only))
    # `side_profile=black` nutzt den vorhandenen Flip-Raum als primäre
    # Perspektive. Dadurch kann der Orchestrator genau eine Schwarz-Partie
    # auslösen, ohne zusätzlich den alten Flip-Zusatzbatch zu starten.
    primary_flip_mode = bool(side_profile == "black" and not bool(canon_mode))
    self_hanging_penalty = max(0.0, float(args.self_hanging_penalty))
    retaliation_penalty = max(0.0, float(args.retaliation_penalty))
    defended_attack_bonus = max(0.0, float(args.defended_attack_bonus))
    discovery_exposure_penalty = max(0.0, float(args.discovery_exposure_penalty))
    castle_bias = max(0.0, float(args.castle_bias))
    promotion_bias = max(0.0, float(args.promotion_bias))
    en_passant_bias = max(0.0, float(args.en_passant_bias))
    check_bias = max(0.0, float(args.check_bias))
    line_pressure_bias = max(0.0, float(args.line_pressure_bias))
    line_pressure_middlegame_lift = max(1.0, float(args.line_pressure_middlegame_lift))
    defense_disruption_bias = max(0.0, float(args.defense_disruption_bias))
    lookahead_conversion_bias = max(0.0, float(args.lookahead_conversion_bias))
    penalty_damper_ratio = min(0.50, max(0.0, float(args.penalty_damper_ratio)))
    # -------------------------------------------------------------------------
    # Phase-2 Heuristik-Gewichte: Diese Werte muessen in main() immer explizit
    # initialisiert werden, bevor sie an PolicyShim(...) uebergeben oder in die
    # Ergebnis-JSON geschrieben werden.
    #
    # Hintergrund des Hotfixes (2026-04-08):
    # In der aktuellen Chess2-Linie wurden mehrere neue Bias-Parameter bereits in
    # PolicyShim.__init__() aufgenommen und spaeter auch in PolicyShim(...)
    # uebergeben. Im main()-Pfad fehlte jedoch die Initialisierung mehrerer
    # Variablen. Dadurch brach der Daily-Runner beim ersten Zugriff mit
    #   NameError: name 'opening_guideline_bias' is not defined
    # ab.
    #
    # Wichtig: Wir initialisieren hier nicht nur den zuerst sichtbaren Fehler,
    # sondern den gesamten zusammengehoerigen Phase-2-Block. Sonst wuerde nach
    # dem ersten Fix sofort der naechste NameError auflaufen
    # (orbit_penalty_bias / neutral_path_penalty_bias /
    # productive_asymmetry_bias / fixpoint_warning_bias).
    #
    # Die Defaults orientieren sich bewusst an den bereits im PolicyShim
    # hinterlegten produktiven Standardwerten, damit Chess2 wieder konsistent,
    # reproduzierbar und ohne Nebenpfad-Regression startet.
    # -------------------------------------------------------------------------
    opening_guideline_bias = 0.075
    anti_flat_bias = 0.070
    asymmetry_keep_bias = 0.060
    worst_piece_improve_bias = 0.050
    coordination_bias = 0.040
    rook_file_activity_bias = 0.035
    attack_coordination_bias = 0.040
    king_line_open_bias = 0.032
    attacker_trade_penalty = 0.045
    orbit_penalty_bias = 0.040
    neutral_path_penalty_bias = 0.024
    productive_asymmetry_bias = 0.050
    fixpoint_warning_bias = 0.022
    aggro = min(2.0, max(1.0, float(args.aggro)))
    _ns_default = (
        "game:chess2_canon_coop_king_territory" if (canon_mode and cooperation_mode and king_mode and territory_mode) else
        ("game:chess2_canon_coop_king" if (canon_mode and cooperation_mode and king_mode) else
         ("game:chess2_canon_coop" if (canon_mode and cooperation_mode) else
          ("game:chess2_canon" if canon_mode else
           ("game:chess2_coop_king_territory" if (cooperation_mode and king_mode and territory_mode) else
            ("game:chess2_coop_king" if (cooperation_mode and king_mode) else
             ("game:chess2_coop" if cooperation_mode else "game:chess2"))))))
    )
    shim = PolicyShim(namespace=str(args.namespace or _ns_default), flip_mode=primary_flip_mode, canon_mode=canon_mode, cooperation_mode=cooperation_mode, king_mode=king_mode, territory_mode=territory_mode, capture_bias=capture_bias, king_shuffle_penalty=king_shuffle_penalty, piece_variety_bias=piece_variety_bias, hanging_piece_bias=hanging_piece_bias, underdefended_piece_bias=underdefended_piece_bias, self_hanging_penalty=self_hanging_penalty, retaliation_penalty=retaliation_penalty, defended_attack_bonus=defended_attack_bonus, discovery_exposure_penalty=discovery_exposure_penalty, castle_bias=castle_bias, promotion_bias=promotion_bias, en_passant_bias=en_passant_bias, check_bias=check_bias, line_pressure_bias=line_pressure_bias, line_pressure_middlegame_lift=line_pressure_middlegame_lift, defense_disruption_bias=defense_disruption_bias, lookahead_conversion_bias=lookahead_conversion_bias, penalty_damper_ratio=penalty_damper_ratio, opening_guideline_bias=opening_guideline_bias, anti_flat_bias=anti_flat_bias, asymmetry_keep_bias=asymmetry_keep_bias, worst_piece_improve_bias=worst_piece_improve_bias, coordination_bias=coordination_bias, rook_file_activity_bias=rook_file_activity_bias, attack_coordination_bias=attack_coordination_bias, king_line_open_bias=king_line_open_bias, attacker_trade_penalty=attacker_trade_penalty, orbit_penalty_bias=orbit_penalty_bias, neutral_path_penalty_bias=neutral_path_penalty_bias, productive_asymmetry_bias=productive_asymmetry_bias, fixpoint_warning_bias=fixpoint_warning_bias, aggro=aggro)
    # Bei explizitem Tagesprofil white/black wird kein zusätzlicher Flip-Batch
    # gestartet. Sonst würde ein einzelner geplanter Tageslauf wieder mehr als
    # eine Partie erzeugen und die Ressourcenbegrenzung unterlaufen.
    flip_enabled = bool(int(args.enable_flip_pass)) and (not canon_mode) and side_profile == "auto"
    shim_flip = PolicyShim(namespace=str(shim.namespace), flip_mode=True, canon_mode=canon_mode, cooperation_mode=cooperation_mode, king_mode=king_mode, territory_mode=territory_mode, capture_bias=capture_bias, king_shuffle_penalty=king_shuffle_penalty, piece_variety_bias=piece_variety_bias, hanging_piece_bias=hanging_piece_bias, underdefended_piece_bias=underdefended_piece_bias, self_hanging_penalty=self_hanging_penalty, retaliation_penalty=retaliation_penalty, defended_attack_bonus=defended_attack_bonus, discovery_exposure_penalty=discovery_exposure_penalty, castle_bias=castle_bias, promotion_bias=promotion_bias, en_passant_bias=en_passant_bias, check_bias=check_bias, line_pressure_bias=line_pressure_bias, line_pressure_middlegame_lift=line_pressure_middlegame_lift, defense_disruption_bias=defense_disruption_bias, lookahead_conversion_bias=lookahead_conversion_bias, penalty_damper_ratio=penalty_damper_ratio, opening_guideline_bias=opening_guideline_bias, anti_flat_bias=anti_flat_bias, asymmetry_keep_bias=asymmetry_keep_bias, worst_piece_improve_bias=worst_piece_improve_bias, coordination_bias=coordination_bias, rook_file_activity_bias=rook_file_activity_bias, attack_coordination_bias=attack_coordination_bias, king_line_open_bias=king_line_open_bias, attacker_trade_penalty=attacker_trade_penalty, orbit_penalty_bias=orbit_penalty_bias, neutral_path_penalty_bias=neutral_path_penalty_bias, productive_asymmetry_bias=productive_asymmetry_bias, fixpoint_warning_bias=fixpoint_warning_bias, aggro=aggro) if flip_enabled else None
    rules_before = _namespace_rule_count(shim.namespace)
    bootstrap_info = bootstrap_from_old_chess(shim, rng)
    label_side = "" if side_profile == "auto" else f":{side_profile}"
    primary_pass_name = "flip" if primary_flip_mode else "normal"
    policy_res = run_batch(rng, shim, int(args.policy_games), "policy", 0.0, 0.0, 0, 0, int(args.max_plies), False, f"chess2{label_side}:policy ({int(args.policy_games)} games)", "orchestrator", pass_name=primary_pass_name, draw_stress=draw_stress, draw_stress_threshold_n=draw_stress_threshold_n, draw_stress_q_band=draw_stress_q_band, draw_stress_max_extra_per_key=draw_stress_max_extra_per_key, win_weight=win_weight, opening_seed_book=opening_seed_book, opening_seed_policy_only=opening_seed_policy_only, opening_seed_offset=0, side_profile=side_profile)
    explore_res = run_batch(rng, shim, int(args.explore_games), "explore", float(args.eps_white), float(args.eps_black), int(args.explore_moves_white), int(args.explore_moves_black), int(args.max_plies), True, f"chess2{label_side}:explore ({int(args.explore_games)} games)", "orchestrator", pass_name=primary_pass_name, draw_stress=0.0, draw_stress_threshold_n=draw_stress_threshold_n, draw_stress_q_band=draw_stress_q_band, draw_stress_max_extra_per_key=draw_stress_max_extra_per_key, win_weight=win_weight, opening_seed_book=opening_seed_book, opening_seed_policy_only=opening_seed_policy_only, opening_seed_offset=int(args.policy_games), side_profile=side_profile)
    flip_policy_res = None
    flip_explore_res = None
    if flip_enabled and shim_flip is not None:
        flip_policy_games = max(0, int(args.flip_policy_games))
        flip_explore_games = max(0, int(args.flip_explore_games))
        if flip_policy_games > 0:
            flip_policy_res = run_batch(rng, shim_flip, flip_policy_games, "policy", 0.0, 0.0, 0, 0, int(args.max_plies), False, f"chess2:policy:flip ({flip_policy_games} games)", "orchestrator", pass_name="flip", draw_stress=draw_stress, draw_stress_threshold_n=draw_stress_threshold_n, draw_stress_q_band=draw_stress_q_band, draw_stress_max_extra_per_key=draw_stress_max_extra_per_key, win_weight=win_weight, opening_seed_book=opening_seed_book, opening_seed_policy_only=opening_seed_policy_only, opening_seed_offset=0, side_profile="black")
        if flip_explore_games > 0:
            flip_explore_res = run_batch(rng, shim_flip, flip_explore_games, "explore", float(args.eps_white), float(args.eps_black), int(args.explore_moves_white), int(args.explore_moves_black), int(args.max_plies), True, f"chess2:explore:flip ({flip_explore_games} games)", "orchestrator", pass_name="flip", draw_stress=0.0, draw_stress_threshold_n=draw_stress_threshold_n, draw_stress_q_band=draw_stress_q_band, draw_stress_max_extra_per_key=draw_stress_max_extra_per_key, win_weight=win_weight, opening_seed_book=opening_seed_book, opening_seed_policy_only=opening_seed_policy_only, opening_seed_offset=flip_policy_games, side_profile="black")
    rules_after = _namespace_rule_count(shim.namespace)
    for batch in (policy_res, explore_res, flip_policy_res, flip_explore_res):
        if isinstance(batch, dict):
            batch["bootstrap_items"] = int(bootstrap_info.get("seeded") or 0)
    engine_line = str(_namespace_phase2_identity(shim.namespace).get("engine_line") or "chess2")
    if canon_mode and cooperation_mode and king_mode and territory_mode:
        base_kind = f"game:{engine_line}_canon_coop_king_territory"
    elif canon_mode and cooperation_mode and king_mode:
        base_kind = f"game:{engine_line}_canon_coop_king"
    elif canon_mode and cooperation_mode:
        base_kind = f"game:{engine_line}_canon_coop"
    elif canon_mode:
        base_kind = f"game:{engine_line}_canon"
    elif cooperation_mode:
        base_kind = f"game:{engine_line}_coop"
    else:
        base_kind = f"game:{engine_line}"
    ok_policy = _db_write_episode(policy_res, kind=f"{base_kind}:policy_batch")
    ok_expl = _db_write_episode(explore_res, kind=f"{base_kind}:explore_batch")
    ok_flip_policy = True if flip_policy_res is None else _db_write_episode(flip_policy_res, kind=f"{base_kind}:policy_batch_flip")
    ok_flip_expl = True if flip_explore_res is None else _db_write_episode(flip_explore_res, kind=f"{base_kind}:explore_batch_flip")
    ident = _namespace_phase2_identity(shim.namespace)
    out = {
        "ok": bool(ok_policy and ok_expl and ok_flip_policy and ok_flip_expl),
        "have_up": bool(shim.pol is not None),
        "db_written": bool(ok_policy and ok_expl and ok_flip_policy and ok_flip_expl),
        "namespace": str(shim.namespace),
        "engine_line": str(ident.get("engine_line") or "chess2"),
        "is_chess3": bool(int(ident.get("is_chess3") or 0)),
        "lookahead_enabled": bool(int(ident.get("lookahead_enabled") or 0)),
        "lookahead_profile": str(ident.get("lookahead_profile") or "none"),
        "canonical": bool(canon_mode),
        "cooperation": bool(cooperation_mode),
        "king_weight": bool(king_mode),
        "territory": bool(territory_mode),
        "draw_stress": float(draw_stress),
        "draw_stress_threshold_n": int(draw_stress_threshold_n),
        "draw_stress_q_band": float(draw_stress_q_band),
        "draw_stress_max_extra_per_key": int(draw_stress_max_extra_per_key),
        "win_weight": float(win_weight),
        "capture_bias": float(capture_bias),
        "king_shuffle_penalty": float(king_shuffle_penalty),
        "piece_variety_bias": float(piece_variety_bias),
        "hanging_piece_bias": float(hanging_piece_bias),
        "underdefended_piece_bias": float(underdefended_piece_bias),
        "opening_seed_book": bool(opening_seed_book),
        "opening_seed_policy_only": bool(opening_seed_policy_only),
        "self_hanging_penalty": float(self_hanging_penalty),
        "retaliation_penalty": float(retaliation_penalty),
        "defended_attack_bonus": float(defended_attack_bonus),
        "discovery_exposure_penalty": float(discovery_exposure_penalty),
        "castle_bias": float(castle_bias),
        "promotion_bias": float(promotion_bias),
        "en_passant_bias": float(en_passant_bias),
        "check_bias": float(check_bias),
        "line_pressure_bias": float(line_pressure_bias),
        "line_pressure_middlegame_lift": float(line_pressure_middlegame_lift),
        "defense_disruption_bias": float(defense_disruption_bias),
        "lookahead_conversion_bias": float(lookahead_conversion_bias),
        "penalty_damper_ratio": float(penalty_damper_ratio),
        "opening_guideline_bias": float(opening_guideline_bias),
        "anti_flat_bias": float(anti_flat_bias),
        "asymmetry_keep_bias": float(asymmetry_keep_bias),
        "worst_piece_improve_bias": float(worst_piece_improve_bias),
        "coordination_bias": float(coordination_bias),
        "rook_file_activity_bias": float(rook_file_activity_bias),
        "attack_coordination_bias": float(attack_coordination_bias),
        "king_line_open_bias": float(king_line_open_bias),
        "attacker_trade_penalty": float(attacker_trade_penalty),
        "orbit_penalty_bias": float(orbit_penalty_bias),
        "neutral_path_penalty_bias": float(neutral_path_penalty_bias),
        "productive_asymmetry_bias": float(productive_asymmetry_bias),
        "fixpoint_warning_bias": float(fixpoint_warning_bias),
        "aggro": float(aggro),
        "bootstrap": bootstrap_info,
        "policy_rules_before": int(rules_before),
        "policy_rules_after": int(rules_after),
        "policy_rules_delta": int(max(0, rules_after - rules_before)),
        "policy_games": int(policy_res.get("games", 0) or 0),
        "side_profile": str(side_profile),
        "primary_flip_pass": bool(primary_flip_mode),
        "flip_pass_enabled": bool(flip_enabled),
        "flip_policy_games": int((flip_policy_res or {}).get("games", 0) or 0),
        "flip_explore_games": int((flip_explore_res or {}).get("games", 0) or 0),
        "flip_policy_white_wins": int((flip_policy_res or {}).get("wins_white", 0) or 0),
        "flip_policy_black_wins": int((flip_policy_res or {}).get("wins_black", 0) or 0),
        "flip_policy_draws": int((flip_policy_res or {}).get("draws", 0) or 0),
        "flip_explore_white_wins": int((flip_explore_res or {}).get("wins_white", 0) or 0),
        "flip_explore_black_wins": int((flip_explore_res or {}).get("wins_black", 0) or 0),
        "flip_explore_draws": int((flip_explore_res or {}).get("draws", 0) or 0),
        "flip_policy_avg_moves": float((flip_policy_res or {}).get("avg_moves", 0.0) or 0.0),
        "policy_opening_seed_games": int(policy_res.get("opening_seed_games", 0) or 0),
        "policy_opening_seed_variants": int(policy_res.get("opening_seed_variants", 0) or 0),
        "explore_opening_seed_games": int(explore_res.get("opening_seed_games", 0) or 0),
        "explore_opening_seed_variants": int(explore_res.get("opening_seed_variants", 0) or 0),
        "flip_explore_avg_moves": float((flip_explore_res or {}).get("avg_moves", 0.0) or 0.0),
        "explore_games": int(explore_res.get("games", 0) or 0),
        "policy_white_wins": int(policy_res.get("wins_white", 0) or 0),
        "policy_black_wins": int(policy_res.get("wins_black", 0) or 0),
        "policy_draws": int(policy_res.get("draws", 0) or 0),
        "policy_draws_by_cap": int(policy_res.get("draws_by_cap", 0) or 0),
        "explore_white_wins": int(explore_res.get("wins_white", 0) or 0),
        "explore_black_wins": int(explore_res.get("wins_black", 0) or 0),
        "explore_draws": int(explore_res.get("draws", 0) or 0),
        "explore_draws_by_cap": int(explore_res.get("draws_by_cap", 0) or 0),
        "policy_avg_moves": float(policy_res.get("avg_moves", 0.0) or 0.0),
        "explore_avg_moves": float(explore_res.get("avg_moves", 0.0) or 0.0),
        "policy_max_plies_seen": int(policy_res.get("max_plies_seen", 0) or 0),
        "explore_max_plies_seen": int(explore_res.get("max_plies_seen", 0) or 0),
        "policy_src_policy": int(policy_res.get("src_policy", 0) or 0),
        "policy_src_policy_fallback_random": int(policy_res.get("src_policy_fallback_random", 0) or 0),
        "explore_src_policy": int(explore_res.get("src_policy", 0) or 0),
        "explore_src_explore_random": int(explore_res.get("src_explore_random", 0) or 0),
        "explore_learn_items": int(explore_res.get("learn_items_count", 0) or 0),
        "policy_castles": int(policy_res.get("castles", 0) or 0),
        "policy_promotions": int(policy_res.get("promotions", 0) or 0),
        "policy_en_passant": int(policy_res.get("en_passant", 0) or 0),
        "policy_checks": int(policy_res.get("checks", 0) or 0),
        "explore_castles": int(explore_res.get("castles", 0) or 0),
        "explore_promotions": int(explore_res.get("promotions", 0) or 0),
        "explore_en_passant": int(explore_res.get("en_passant", 0) or 0),
        "explore_checks": int(explore_res.get("checks", 0) or 0),
        "flip_policy_castles": int((flip_policy_res or {}).get("castles", 0) or 0),
        "flip_policy_promotions": int((flip_policy_res or {}).get("promotions", 0) or 0),
        "flip_policy_en_passant": int((flip_policy_res or {}).get("en_passant", 0) or 0),
        "flip_policy_checks": int((flip_policy_res or {}).get("checks", 0) or 0),
        "flip_explore_castles": int((flip_explore_res or {}).get("castles", 0) or 0),
        "flip_explore_promotions": int((flip_explore_res or {}).get("promotions", 0) or 0),
        "flip_explore_en_passant": int((flip_explore_res or {}).get("en_passant", 0) or 0),
        "flip_explore_checks": int((flip_explore_res or {}).get("checks", 0) or 0),
        "policy_lookahead_2ply_used": int(policy_res.get("lookahead_2ply_used", 0) or 0),
        "policy_lookahead_3ply_used": int(policy_res.get("lookahead_3ply_used", 0) or 0),
        "policy_lookahead_king_pressure_cases": int(policy_res.get("lookahead_king_pressure_cases", 0) or 0),
        "policy_lookahead_endgame_cases": int(policy_res.get("lookahead_endgame_cases", 0) or 0),
        "policy_lookahead_check_cases": int(policy_res.get("lookahead_check_cases", 0) or 0),
        "policy_lookahead_capture_cases": int(policy_res.get("lookahead_capture_cases", 0) or 0),
        "policy_lookahead_agreement_count": int(policy_res.get("lookahead_agreement_count", 0) or 0),
        "policy_lookahead_correction_count": int(policy_res.get("lookahead_correction_count", 0) or 0),
        "policy_lookahead_bonus_sum": float(policy_res.get("lookahead_bonus_sum", 0.0) or 0.0),
        "policy_lookahead_penalty_sum": float(policy_res.get("lookahead_penalty_sum", 0.0) or 0.0),
        "policy_lookahead_errors": int(policy_res.get("lookahead_errors", 0) or 0),
        "policy_lookahead_fallbacks": int(policy_res.get("lookahead_fallbacks", 0) or 0),
        "policy_line_pressure_cases": int(policy_res.get("line_pressure_cases", 0) or 0),
        "policy_line_pressure_bonus_sum": float(policy_res.get("line_pressure_bonus_sum", 0.0) or 0.0),
        "policy_line_pressure_opening_cases": int(policy_res.get("line_pressure_opening_cases", 0) or 0),
        "policy_line_pressure_middlegame_cases": int(policy_res.get("line_pressure_middlegame_cases", 0) or 0),
        "policy_line_pressure_endgame_cases": int(policy_res.get("line_pressure_endgame_cases", 0) or 0),
        "policy_line_pressure_errors": int(policy_res.get("line_pressure_errors", 0) or 0),
        "policy_defense_disruption_cases": int(policy_res.get("defense_disruption_cases", 0) or 0),
        "policy_defense_disruption_bonus_sum": float(policy_res.get("defense_disruption_bonus_sum", 0.0) or 0.0),
        "policy_defense_disruption_king_zone_cases": int(policy_res.get("defense_disruption_king_zone_cases", 0) or 0),
        "policy_defense_disruption_outpost_cases": int(policy_res.get("defense_disruption_outpost_cases", 0) or 0),
        "policy_defense_disruption_color_complex_cases": int(policy_res.get("defense_disruption_color_complex_cases", 0) or 0),
        "policy_defense_disruption_errors": int(policy_res.get("defense_disruption_errors", 0) or 0),
        "policy_lookahead_conversion_bonus_sum": float(policy_res.get("lookahead_conversion_bonus_sum", 0.0) or 0.0),
        "policy_penalty_damper_cases": int(policy_res.get("penalty_damper_cases", 0) or 0),
        "policy_penalty_damper_sum": float(policy_res.get("penalty_damper_sum", 0.0) or 0.0),
        "policy_anti_flat_cases": int(policy_res.get("anti_flat_cases", 0) or 0),
        "policy_anti_flat_penalty_sum": float(policy_res.get("anti_flat_penalty_sum", 0.0) or 0.0),
        "policy_trade_without_gain_cases": int(policy_res.get("trade_without_gain_cases", 0) or 0),
        "policy_trade_without_gain_penalty_sum": float(policy_res.get("trade_without_gain_penalty_sum", 0.0) or 0.0),
        "policy_asymmetry_keep_cases": int(policy_res.get("asymmetry_keep_cases", 0) or 0),
        "policy_asymmetry_keep_bonus_sum": float(policy_res.get("asymmetry_keep_bonus_sum", 0.0) or 0.0),
        "policy_worst_piece_improve_cases": int(policy_res.get("worst_piece_improve_cases", 0) or 0),
        "policy_worst_piece_improve_bonus_sum": float(policy_res.get("worst_piece_improve_bonus_sum", 0.0) or 0.0),
        "policy_coordination_cases": int(policy_res.get("coordination_cases", 0) or 0),
        "policy_coordination_bonus_sum": float(policy_res.get("coordination_bonus_sum", 0.0) or 0.0),
        "policy_rook_file_activity_cases": int(policy_res.get("rook_file_activity_cases", 0) or 0),
        "policy_rook_file_activity_bonus_sum": float(policy_res.get("rook_file_activity_bonus_sum", 0.0) or 0.0),
        "policy_attack_coordination_cases": int(policy_res.get("attack_coordination_cases", 0) or 0),
        "policy_attack_coordination_bonus_sum": float(policy_res.get("attack_coordination_bonus_sum", 0.0) or 0.0),
        "policy_king_line_open_cases": int(policy_res.get("king_line_open_cases", 0) or 0),
        "policy_king_line_open_bonus_sum": float(policy_res.get("king_line_open_bonus_sum", 0.0) or 0.0),
        "policy_attacker_trade_penalty_cases": int(policy_res.get("attacker_trade_penalty_cases", 0) or 0),
        "policy_attacker_trade_penalty_sum": float(policy_res.get("attacker_trade_penalty_sum", 0.0) or 0.0),
        "policy_orbit_penalty_cases": int(policy_res.get("orbit_penalty_cases", 0) or 0),
        "policy_orbit_penalty_sum": float(policy_res.get("orbit_penalty_sum", 0.0) or 0.0),
        "policy_neutral_path_penalty_cases": int(policy_res.get("neutral_path_penalty_cases", 0) or 0),
        "policy_neutral_path_penalty_sum": float(policy_res.get("neutral_path_penalty_sum", 0.0) or 0.0),
        "policy_productive_asymmetry_cases": int(policy_res.get("productive_asymmetry_cases", 0) or 0),
        "policy_productive_asymmetry_bonus_sum": float(policy_res.get("productive_asymmetry_bonus_sum", 0.0) or 0.0),
        "policy_fixpoint_warning_cases": int(policy_res.get("fixpoint_warning_cases", 0) or 0),
        "policy_fixpoint_warning_penalty_sum": float(policy_res.get("fixpoint_warning_penalty_sum", 0.0) or 0.0),
        "explore_lookahead_2ply_used": int(explore_res.get("lookahead_2ply_used", 0) or 0),
        "explore_lookahead_3ply_used": int(explore_res.get("lookahead_3ply_used", 0) or 0),
        "explore_lookahead_king_pressure_cases": int(explore_res.get("lookahead_king_pressure_cases", 0) or 0),
        "explore_lookahead_endgame_cases": int(explore_res.get("lookahead_endgame_cases", 0) or 0),
        "explore_lookahead_check_cases": int(explore_res.get("lookahead_check_cases", 0) or 0),
        "explore_lookahead_capture_cases": int(explore_res.get("lookahead_capture_cases", 0) or 0),
        "explore_lookahead_agreement_count": int(explore_res.get("lookahead_agreement_count", 0) or 0),
        "explore_lookahead_correction_count": int(explore_res.get("lookahead_correction_count", 0) or 0),
        "explore_lookahead_bonus_sum": float(explore_res.get("lookahead_bonus_sum", 0.0) or 0.0),
        "explore_lookahead_penalty_sum": float(explore_res.get("lookahead_penalty_sum", 0.0) or 0.0),
        "explore_lookahead_errors": int(explore_res.get("lookahead_errors", 0) or 0),
        "explore_lookahead_fallbacks": int(explore_res.get("lookahead_fallbacks", 0) or 0),
        "explore_line_pressure_cases": int(explore_res.get("line_pressure_cases", 0) or 0),
        "explore_line_pressure_bonus_sum": float(explore_res.get("line_pressure_bonus_sum", 0.0) or 0.0),
        "explore_line_pressure_errors": int(explore_res.get("line_pressure_errors", 0) or 0),
        "explore_defense_disruption_cases": int(explore_res.get("defense_disruption_cases", 0) or 0),
        "explore_defense_disruption_bonus_sum": float(explore_res.get("defense_disruption_bonus_sum", 0.0) or 0.0),
        "explore_defense_disruption_errors": int(explore_res.get("defense_disruption_errors", 0) or 0),
        "explore_lookahead_conversion_bonus_sum": float(explore_res.get("lookahead_conversion_bonus_sum", 0.0) or 0.0),
        "explore_penalty_damper_cases": int(explore_res.get("penalty_damper_cases", 0) or 0),
        "explore_penalty_damper_sum": float(explore_res.get("penalty_damper_sum", 0.0) or 0.0),
        "explore_anti_flat_cases": int(explore_res.get("anti_flat_cases", 0) or 0),
        "explore_anti_flat_penalty_sum": float(explore_res.get("anti_flat_penalty_sum", 0.0) or 0.0),
        "explore_trade_without_gain_cases": int(explore_res.get("trade_without_gain_cases", 0) or 0),
        "explore_trade_without_gain_penalty_sum": float(explore_res.get("trade_without_gain_penalty_sum", 0.0) or 0.0),
        "explore_asymmetry_keep_cases": int(explore_res.get("asymmetry_keep_cases", 0) or 0),
        "explore_asymmetry_keep_bonus_sum": float(explore_res.get("asymmetry_keep_bonus_sum", 0.0) or 0.0),
        "explore_worst_piece_improve_cases": int(explore_res.get("worst_piece_improve_cases", 0) or 0),
        "explore_worst_piece_improve_bonus_sum": float(explore_res.get("worst_piece_improve_bonus_sum", 0.0) or 0.0),
        "explore_coordination_cases": int(explore_res.get("coordination_cases", 0) or 0),
        "explore_coordination_bonus_sum": float(explore_res.get("coordination_bonus_sum", 0.0) or 0.0),
        "explore_rook_file_activity_cases": int(explore_res.get("rook_file_activity_cases", 0) or 0),
        "explore_rook_file_activity_bonus_sum": float(explore_res.get("rook_file_activity_bonus_sum", 0.0) or 0.0),
        "flip_policy_lookahead_2ply_used": int((flip_policy_res or {}).get("lookahead_2ply_used", 0) or 0),
        "flip_policy_lookahead_3ply_used": int((flip_policy_res or {}).get("lookahead_3ply_used", 0) or 0),
        "flip_policy_lookahead_king_pressure_cases": int((flip_policy_res or {}).get("lookahead_king_pressure_cases", 0) or 0),
        "flip_policy_lookahead_endgame_cases": int((flip_policy_res or {}).get("lookahead_endgame_cases", 0) or 0),
        "flip_policy_lookahead_check_cases": int((flip_policy_res or {}).get("lookahead_check_cases", 0) or 0),
        "flip_policy_lookahead_capture_cases": int((flip_policy_res or {}).get("lookahead_capture_cases", 0) or 0),
        "flip_policy_lookahead_agreement_count": int((flip_policy_res or {}).get("lookahead_agreement_count", 0) or 0),
        "flip_policy_lookahead_correction_count": int((flip_policy_res or {}).get("lookahead_correction_count", 0) or 0),
        "flip_policy_lookahead_bonus_sum": float((flip_policy_res or {}).get("lookahead_bonus_sum", 0.0) or 0.0),
        "flip_policy_lookahead_penalty_sum": float((flip_policy_res or {}).get("lookahead_penalty_sum", 0.0) or 0.0),
        "flip_policy_lookahead_errors": int((flip_policy_res or {}).get("lookahead_errors", 0) or 0),
        "flip_policy_lookahead_fallbacks": int((flip_policy_res or {}).get("lookahead_fallbacks", 0) or 0),
        "flip_policy_line_pressure_cases": int((flip_policy_res or {}).get("line_pressure_cases", 0) or 0),
        "flip_policy_line_pressure_bonus_sum": float((flip_policy_res or {}).get("line_pressure_bonus_sum", 0.0) or 0.0),
        "flip_policy_line_pressure_errors": int((flip_policy_res or {}).get("line_pressure_errors", 0) or 0),
        "flip_policy_defense_disruption_cases": int((flip_policy_res or {}).get("defense_disruption_cases", 0) or 0),
        "flip_policy_defense_disruption_bonus_sum": float((flip_policy_res or {}).get("defense_disruption_bonus_sum", 0.0) or 0.0),
        "flip_policy_defense_disruption_errors": int((flip_policy_res or {}).get("defense_disruption_errors", 0) or 0),
        "flip_policy_lookahead_conversion_bonus_sum": float((flip_policy_res or {}).get("lookahead_conversion_bonus_sum", 0.0) or 0.0),
        "flip_policy_penalty_damper_cases": int((flip_policy_res or {}).get("penalty_damper_cases", 0) or 0),
        "flip_policy_penalty_damper_sum": float((flip_policy_res or {}).get("penalty_damper_sum", 0.0) or 0.0),
        "flip_policy_anti_flat_cases": int((flip_policy_res or {}).get("anti_flat_cases", 0) or 0),
        "flip_policy_anti_flat_penalty_sum": float((flip_policy_res or {}).get("anti_flat_penalty_sum", 0.0) or 0.0),
        "flip_policy_trade_without_gain_cases": int((flip_policy_res or {}).get("trade_without_gain_cases", 0) or 0),
        "flip_policy_trade_without_gain_penalty_sum": float((flip_policy_res or {}).get("trade_without_gain_penalty_sum", 0.0) or 0.0),
        "flip_policy_asymmetry_keep_cases": int((flip_policy_res or {}).get("asymmetry_keep_cases", 0) or 0),
        "flip_policy_asymmetry_keep_bonus_sum": float((flip_policy_res or {}).get("asymmetry_keep_bonus_sum", 0.0) or 0.0),
        "flip_policy_worst_piece_improve_cases": int((flip_policy_res or {}).get("worst_piece_improve_cases", 0) or 0),
        "flip_policy_worst_piece_improve_bonus_sum": float((flip_policy_res or {}).get("worst_piece_improve_bonus_sum", 0.0) or 0.0),
        "flip_policy_coordination_cases": int((flip_policy_res or {}).get("coordination_cases", 0) or 0),
        "flip_policy_coordination_bonus_sum": float((flip_policy_res or {}).get("coordination_bonus_sum", 0.0) or 0.0),
        "flip_policy_rook_file_activity_cases": int((flip_policy_res or {}).get("rook_file_activity_cases", 0) or 0),
        "flip_policy_rook_file_activity_bonus_sum": float((flip_policy_res or {}).get("rook_file_activity_bonus_sum", 0.0) or 0.0),
        "flip_policy_attack_coordination_cases": int((flip_policy_res or {}).get("attack_coordination_cases", 0) or 0),
        "flip_policy_attack_coordination_bonus_sum": float((flip_policy_res or {}).get("attack_coordination_bonus_sum", 0.0) or 0.0),
        "flip_policy_king_line_open_cases": int((flip_policy_res or {}).get("king_line_open_cases", 0) or 0),
        "flip_policy_king_line_open_bonus_sum": float((flip_policy_res or {}).get("king_line_open_bonus_sum", 0.0) or 0.0),
        "flip_policy_attacker_trade_penalty_cases": int((flip_policy_res or {}).get("attacker_trade_penalty_cases", 0) or 0),
        "flip_policy_attacker_trade_penalty_sum": float((flip_policy_res or {}).get("attacker_trade_penalty_sum", 0.0) or 0.0),
        "flip_policy_orbit_penalty_cases": int((flip_policy_res or {}).get("orbit_penalty_cases", 0) or 0),
        "flip_policy_orbit_penalty_sum": float((flip_policy_res or {}).get("orbit_penalty_sum", 0.0) or 0.0),
        "flip_policy_neutral_path_penalty_cases": int((flip_policy_res or {}).get("neutral_path_penalty_cases", 0) or 0),
        "flip_policy_neutral_path_penalty_sum": float((flip_policy_res or {}).get("neutral_path_penalty_sum", 0.0) or 0.0),
        "flip_policy_productive_asymmetry_cases": int((flip_policy_res or {}).get("productive_asymmetry_cases", 0) or 0),
        "flip_policy_productive_asymmetry_bonus_sum": float((flip_policy_res or {}).get("productive_asymmetry_bonus_sum", 0.0) or 0.0),
        "flip_policy_fixpoint_warning_cases": int((flip_policy_res or {}).get("fixpoint_warning_cases", 0) or 0),
        "flip_policy_fixpoint_warning_penalty_sum": float((flip_policy_res or {}).get("fixpoint_warning_penalty_sum", 0.0) or 0.0),
        "flip_explore_lookahead_2ply_used": int((flip_explore_res or {}).get("lookahead_2ply_used", 0) or 0),
        "flip_explore_lookahead_3ply_used": int((flip_explore_res or {}).get("lookahead_3ply_used", 0) or 0),
        "flip_explore_lookahead_king_pressure_cases": int((flip_explore_res or {}).get("lookahead_king_pressure_cases", 0) or 0),
        "flip_explore_lookahead_endgame_cases": int((flip_explore_res or {}).get("lookahead_endgame_cases", 0) or 0),
        "flip_explore_lookahead_check_cases": int((flip_explore_res or {}).get("lookahead_check_cases", 0) or 0),
        "flip_explore_lookahead_capture_cases": int((flip_explore_res or {}).get("lookahead_capture_cases", 0) or 0),
        "flip_explore_lookahead_agreement_count": int((flip_explore_res or {}).get("lookahead_agreement_count", 0) or 0),
        "flip_explore_lookahead_correction_count": int((flip_explore_res or {}).get("lookahead_correction_count", 0) or 0),
        "flip_explore_lookahead_bonus_sum": float((flip_explore_res or {}).get("lookahead_bonus_sum", 0.0) or 0.0),
        "flip_explore_lookahead_penalty_sum": float((flip_explore_res or {}).get("lookahead_penalty_sum", 0.0) or 0.0),
        "flip_explore_lookahead_errors": int((flip_explore_res or {}).get("lookahead_errors", 0) or 0),
        "flip_explore_lookahead_fallbacks": int((flip_explore_res or {}).get("lookahead_fallbacks", 0) or 0),
        "flip_explore_line_pressure_cases": int((flip_explore_res or {}).get("line_pressure_cases", 0) or 0),
        "flip_explore_line_pressure_bonus_sum": float((flip_explore_res or {}).get("line_pressure_bonus_sum", 0.0) or 0.0),
        "flip_explore_line_pressure_errors": int((flip_explore_res or {}).get("line_pressure_errors", 0) or 0),
        "flip_explore_defense_disruption_cases": int((flip_explore_res or {}).get("defense_disruption_cases", 0) or 0),
        "flip_explore_defense_disruption_bonus_sum": float((flip_explore_res or {}).get("defense_disruption_bonus_sum", 0.0) or 0.0),
        "flip_explore_defense_disruption_errors": int((flip_explore_res or {}).get("defense_disruption_errors", 0) or 0),
        "flip_explore_lookahead_conversion_bonus_sum": float((flip_explore_res or {}).get("lookahead_conversion_bonus_sum", 0.0) or 0.0),
        "flip_explore_penalty_damper_cases": int((flip_explore_res or {}).get("penalty_damper_cases", 0) or 0),
        "flip_explore_penalty_damper_sum": float((flip_explore_res or {}).get("penalty_damper_sum", 0.0) or 0.0),
        "flip_explore_anti_flat_cases": int((flip_explore_res or {}).get("anti_flat_cases", 0) or 0),
        "flip_explore_anti_flat_penalty_sum": float((flip_explore_res or {}).get("anti_flat_penalty_sum", 0.0) or 0.0),
        "flip_explore_trade_without_gain_cases": int((flip_explore_res or {}).get("trade_without_gain_cases", 0) or 0),
        "flip_explore_trade_without_gain_penalty_sum": float((flip_explore_res or {}).get("trade_without_gain_penalty_sum", 0.0) or 0.0),
        "flip_explore_asymmetry_keep_cases": int((flip_explore_res or {}).get("asymmetry_keep_cases", 0) or 0),
        "flip_explore_asymmetry_keep_bonus_sum": float((flip_explore_res or {}).get("asymmetry_keep_bonus_sum", 0.0) or 0.0),
        "flip_explore_worst_piece_improve_cases": int((flip_explore_res or {}).get("worst_piece_improve_cases", 0) or 0),
        "flip_explore_worst_piece_improve_bonus_sum": float((flip_explore_res or {}).get("worst_piece_improve_bonus_sum", 0.0) or 0.0),
        "flip_explore_coordination_cases": int((flip_explore_res or {}).get("coordination_cases", 0) or 0),
        "flip_explore_coordination_bonus_sum": float((flip_explore_res or {}).get("coordination_bonus_sum", 0.0) or 0.0),
        "flip_explore_rook_file_activity_cases": int((flip_explore_res or {}).get("rook_file_activity_cases", 0) or 0),
        "flip_explore_rook_file_activity_bonus_sum": float((flip_explore_res or {}).get("rook_file_activity_bonus_sum", 0.0) or 0.0),
        "policy_draw_stress_items": int(policy_res.get("draw_stress_items", 0) or 0),
        "policy_draw_stress_keys": int(policy_res.get("draw_stress_keys", 0) or 0),
        "flip_policy_draw_stress_items": int((flip_policy_res or {}).get("draw_stress_items", 0) or 0),
        "flip_policy_draw_stress_keys": int((flip_policy_res or {}).get("draw_stress_keys", 0) or 0),
        "snapchains_written": int((policy_res.get("chains_count", 0) or 0) + (explore_res.get("chains_count", 0) or 0) + ((flip_policy_res or {}).get("chains_count", 0) or 0) + ((flip_explore_res or {}).get("chains_count", 0) or 0)),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


def _compute_attack_coordination_score(fen: str, uci: str, side: str, *, ctx: Optional[Dict[str, Any]] = None, base_bias: float = 0.045) -> Dict[str, Any]:
    """Weicher Bonus für koordinierte Angriffe mit mehreren Figuren."""
    out: Dict[str, Any] = {'bonus': 0.0, 'supporters': 0, 'king_targets': 0, 'safe': False}
    try:
        if float(base_bias or 0.0) <= 0.0:
            return out
        sim = _simulate_board_after_uci(fen, uci)
        if not sim:
            return out
        board_after = dict(sim.get('board_after') or {})
        dst = sim.get('dst')
        mover_after = str(sim.get('mover_after') or sim.get('mover') or '')
        if dst is None or not mover_after:
            return out
        own_is_white = bool(str(side or 'W').upper().startswith('W'))
        enemy_king_sq = _find_king_square(board_after, not own_is_white)
        if enemy_king_sq is None:
            return out
        pt = _piece_type(mover_after)
        if pt in {'', 'P', 'K'}:
            return out
        white_att, black_att = attack_count_maps_from_board(board_after)
        own_att = white_att if own_is_white else black_att
        opp_att = black_att if own_is_white else white_att
        er, ec = divmod(int(enemy_king_sq), 8)
        king_zone = set()
        for rr in range(max(0, er-1), min(7, er+1)+1):
            for cc in range(max(0, ec-1), min(7, ec+1)+1):
                king_zone.add(rr*8+cc)
        supporters = sum(1 for sq in king_zone if int(own_att[sq] or 0) > 0)
        target_count = sum(int(own_att[sq] or 0) for sq in king_zone)
        dst_def = int(own_att[int(dst)] or 0)
        dst_att = int(opp_att[int(dst)] or 0)
        safe = dst_def >= dst_att
        bonus = 0.0
        if supporters >= 2 and safe:
            bonus += float(base_bias) * min(1.0, supporters / 4.0)
        if target_count >= 5:
            bonus += float(base_bias) * 0.40 * min(1.0, target_count / 9.0)
        if bool((ctx or {}).get('is_check')) and safe:
            bonus += float(base_bias) * 0.30
        if safe and supporters >= 3:
            bonus += float(base_bias) * 0.18
        elif supporters < 2:
            bonus *= 0.5
        out.update({'bonus': float(bonus), 'supporters': int(supporters), 'king_targets': int(target_count), 'safe': bool(safe)})
        return out
    except Exception:
        return out


def _compute_king_line_open_score(fen: str, uci: str, side: str, *, ctx: Optional[Dict[str, Any]] = None, base_bias: float = 0.040) -> Dict[str, Any]:
    """Belohnt leichte Linienöffnung gegen den gegnerischen König."""
    out: Dict[str, Any] = {'bonus': 0.0, 'opened': False, 'central_file': False}
    try:
        if float(base_bias or 0.0) <= 0.0:
            return out
        sim = _simulate_board_after_uci(fen, uci)
        if not sim:
            return out
        own_is_white = bool(str(side or 'W').upper().startswith('W'))
        board_before = dict(parse_fen(fen).board or {})
        board_after = dict(sim.get('board_after') or {})
        enemy_king_sq = _find_king_square(board_after, not own_is_white)
        if enemy_king_sq is None:
            return out
        dst = int(sim.get('dst')) if sim.get('dst') is not None else None
        mover_after = str(sim.get('mover_after') or sim.get('mover') or '')
        if dst is None or not mover_after:
            return out
        opened = False
        central_file = False
        er, ec = divmod(int(enemy_king_sq), 8)
        dr, dc = divmod(dst, 8)
        if _piece_type(mover_after) in {'R','Q'}:
            if dc == ec or dc in {2,3,4,5}:
                central_file = dc in {2,3,4,5}
                opened = True
        elif _piece_type(mover_after) == 'B':
            if abs(dr-er) == abs(dc-ec):
                opened = True
        elif _piece_type(mover_after) == 'P' and bool((ctx or {}).get('is_capture')):
            if abs(dc-ec) <= 1:
                opened = True
        bonus = 0.0
        support = float((((ctx or {}).get('attack_coordination') or {}).get('supporters', 0)) or 0.0)
        if opened:
            bonus += float(base_bias) * (0.75 if support >= 2 else 0.35)
            if central_file:
                bonus += float(base_bias) * (0.30 if support >= 2 else 0.12)
            if bool((ctx or {}).get('is_check')) and support >= 1:
                bonus += float(base_bias) * 0.20
        out.update({'bonus': float(bonus), 'opened': bool(opened), 'central_file': bool(central_file)})
        return out
    except Exception:
        return out


def _compute_attacker_trade_penalty(fen: str, uci: str, side: str, *, ctx: Optional[Dict[str, Any]] = None, base_penalty: float = 0.035) -> Dict[str, Any]:
    """Leichter Malus, wenn ein aktiver Angreifer ohne klaren Vorteil getauscht wird."""
    out: Dict[str, Any] = {'penalty': 0.0, 'active_attacker': False}
    try:
        if float(base_penalty or 0.0) <= 0.0:
            return out
        if not bool((ctx or {}).get('is_capture')):
            return out
        mover = str((ctx or {}).get('piece', '') or '')
        pt = _piece_type(mover)
        if pt not in {'Q','R','B','N'}:
            return out
        capture_value = float((ctx or {}).get('capture_value', 0.0) or 0.0)
        piece_value = float((ctx or {}).get('piece_value', 0.0) or 0.0)
        lp_bonus = float((((ctx or {}).get('line_pressure') or {}).get('bonus', 0.0)) or 0.0)
        dd_bonus = float((((ctx or {}).get('defense_disruption') or {}).get('bonus', 0.0)) or 0.0)
        ac_support = int((((ctx or {}).get('attack_coordination') or {}).get('supporters', 0)) or 0)
        active = lp_bonus + dd_bonus > 0.06 or bool((ctx or {}).get('is_check')) or ac_support >= 2
        if not active:
            return out
        if capture_value + 0.45 >= piece_value and not bool((ctx or {}).get('is_check')):
            return out
        pen = float(base_penalty) * min(1.35, max(0.5, (piece_value - capture_value) / 4.5))
        out.update({'penalty': float(max(0.0, pen)), 'active_attacker': True})
        return out
    except Exception:
        return out


def _coordination_improve_score(fen: str, uci: str, side: str, ctx: Optional[Dict[str, Any]] = None, base_bias: float = 0.040) -> Dict[str, Any]:
    """Kleiner Bonus für bessere Figurenkoordination.

    Patch B.2 macht die Trigger bewusst robuster: Bereits sinnvolle
    Entwicklung aus Grundreihe/Randzone, gedeckte Entwicklungsfelder,
    kleine Aktivitätsgewinne und gemeinsam gestützte Feldkomplexe sollen
    zählen. Der Bonus bleibt klein und wird phasenabhängig gedämpft.
    """
    out: Dict[str, Any] = {'bonus': 0.0, 'supported': False, 'before': 0.0, 'after': 0.0, 'delta': 0.0}
    try:
        if float(base_bias or 0.0) <= 0.0:
            return out
        parsed = parse_fen(fen)
        fullmove = int(parsed.fullmove or 1)
        board = dict(parsed.board or {})
        own_is_white = str(side or 'W').upper().startswith('W')
        s = str(uci or '').strip().lower()
        if len(s) < 4:
            return out
        src = parse_square(s[:2]); dst = parse_square(s[2:4])
        if src is None or dst is None:
            return out
        src = int(src); dst = int(dst)
        mover = str(board.get(src, '') or str((ctx or {}).get('piece', '') or ''))
        pt = _piece_type(mover)
        if pt in {'', 'P', 'K'}:
            return out
        sim = _simulate_board_after_uci(fen, s)
        if not sim:
            return out
        board_after = dict(sim.get('board_after') or {})
        mover_after = str(sim.get('mover_after') or mover)
        white_att_b, black_att_b = attack_count_maps_from_board(board)
        own_att_b = white_att_b if own_is_white else black_att_b
        white_att_a, black_att_a = attack_count_maps_from_board(board_after)
        own_att_a = white_att_a if own_is_white else black_att_a
        opp_att_a = black_att_a if own_is_white else white_att_a
        before_def = int(own_att_b[src] or 0)
        after_def = int(own_att_a[dst] or 0)
        before_act = float(_piece_activity_proxy(src, mover, own_is_white, fullmove))
        after_act = float(_piece_activity_proxy(dst, mover_after, own_is_white, fullmove))
        delta = max(0.0, after_act - before_act)
        out['before'] = float(before_act)
        out['after'] = float(after_act)
        out['delta'] = float(delta)
        bonus = 0.0

        sr, sc = divmod(src, 8)
        dr, dc = divmod(dst, 8)
        home_rank = 7 if own_is_white else 0
        src_edge = sc in {0, 7}
        dst_edge = dc in {0, 7}
        dst_central = dc in {2, 3, 4, 5}
        safe_after = after_def >= int(opp_att_a[dst] or 0)

        if after_def > before_def:
            bonus += float(base_bias) * min(0.85, 0.18 * float(after_def - before_def))
        elif after_def == before_def and after_def > 0:
            bonus += float(base_bias) * 0.10

        if int((ctx or {}).get('self_attackers_after', 0) or 0) <= int((ctx or {}).get('self_defenders_after', 0) or 0):
            bonus += float(base_bias) * 0.18
            out['supported'] = True

        if delta > 0.01:
            bonus += float(base_bias) * min(0.95, 0.65 * delta)

        if pt in {'N', 'B'}:
            start_squares = {'b1', 'g1', 'c1', 'f1'} if own_is_white else {'b8', 'g8', 'c8', 'f8'}
            if s[:2] in start_squares and (dst_central or dr != home_rank or safe_after):
                bonus += float(base_bias) * 0.28
        if sr == home_rank and dr != home_rank and safe_after:
            bonus += float(base_bias) * 0.12
        if src_edge and not dst_edge and after_act >= before_act:
            bonus += float(base_bias) * 0.10
        if dst_central and after_act >= before_act:
            bonus += float(base_bias) * 0.08

        if after_def >= 2 and dst_central:
            bonus += float(base_bias) * 0.10
        if safe_after and after_act >= before_act and dr != sr:
            bonus += float(base_bias) * 0.08

        bonus *= (0.62 + 0.38 * float(_flat_phase_weight(fen)))
        out['bonus'] = max(0.0, float(bonus))
        return out
    except Exception:
        return out


def _rook_file_activity_score(fen: str, uci: str, side: str, ctx: Optional[Dict[str, Any]] = None, base_bias: float = 0.035) -> Dict[str, Any]:
    """Leichter Bonus für aktive Türme.

    Patch B.2 weitet die Trigger deutlich aus: offene/halboffene Linien bleiben
    relevant, aber bereits sinnvolle Aktivierung nach Rochade, das Verlassen des
    Eckfelds, zentrale Dateien und verbundene Türme zählen jetzt früher.
    """
    out: Dict[str, Any] = {'bonus': 0.0, 'open_file': False, 'half_open_file': False, 'central_file': False}
    try:
        if float(base_bias or 0.0) <= 0.0:
            return out
        parsed = parse_fen(fen)
        board = dict(parsed.board or {})
        own_is_white = str(side or 'W').upper().startswith('W')
        s = str(uci or '').strip().lower()
        if len(s) < 4:
            return out
        src = parse_square(s[:2]); dst = parse_square(s[2:4])
        if src is None or dst is None:
            return out
        src = int(src); dst = int(dst)
        mover = str(board.get(src, '') or str((ctx or {}).get('piece', '') or ''))
        if _piece_type(mover) != 'R':
            return out
        sim = _simulate_board_after_uci(fen, s)
        if not sim:
            return out
        board_after = dict(sim.get('board_after') or {})
        dr, file_idx = divmod(dst, 8)
        sr, src_file = divmod(src, 8)
        own_pawns = 0
        opp_pawns = 0
        for sq, piece in board_after.items():
            ps = str(piece or '')
            if not ps or _piece_type(ps) != 'P':
                continue
            _, c = divmod(int(sq), 8)
            if c != file_idx:
                continue
            if ps.isupper() == own_is_white:
                own_pawns += 1
            else:
                opp_pawns += 1
        bonus = 0.0
        if own_pawns == 0 and opp_pawns == 0:
            out['open_file'] = True
            bonus += float(base_bias) * 0.90
        elif own_pawns == 0:
            out['half_open_file'] = True
            bonus += float(base_bias) * 0.58

        if file_idx in {2, 3, 4, 5}:
            out['central_file'] = True
            bonus += float(base_bias) * 0.26
        elif file_idx in {1, 6}:
            bonus += float(base_bias) * 0.12

        home_rank = 7 if own_is_white else 0
        if sr == home_rank and src_file in {0, 7} and (dr != home_rank or file_idx not in {0, 7}):
            bonus += float(base_bias) * 0.24

        if sr == home_rank and file_idx in {2, 3, 4, 5}:
            bonus += float(base_bias) * 0.16
        if sr == home_rank and dr == home_rank and file_idx in {3, 4}:
            bonus += float(base_bias) * 0.12

        rook_sqs = []
        for sq, piece in board_after.items():
            ps = str(piece or '')
            if _piece_type(ps) == 'R' and (ps.isupper() == own_is_white):
                rook_sqs.append(int(sq))
        if len(rook_sqs) >= 2:
            connected = False
            for i in range(len(rook_sqs)):
                for j in range(i + 1, len(rook_sqs)):
                    a = rook_sqs[i]; b = rook_sqs[j]
                    ar, ac = divmod(a, 8); br, bc = divmod(b, 8)
                    if ar == br:
                        blocked = False
                        lo, hi = sorted([ac, bc])
                        for cc in range(lo + 1, hi):
                            if str(board_after.get(ar * 8 + cc, '') or ''):
                                blocked = True
                                break
                        if not blocked:
                            bonus += float(base_bias) * 0.24
                            connected = True
                            break
                if connected:
                    break

        if int((ctx or {}).get('self_attackers_after', 0) or 0) <= int((ctx or {}).get('self_defenders_after', 0) or 0):
            bonus += float(base_bias) * 0.12
        bonus *= (0.52 + 0.48 * float(_flat_phase_weight(fen)))
        out['bonus'] = max(0.0, float(bonus))
        return out
    except Exception:
        return out

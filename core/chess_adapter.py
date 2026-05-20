#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_adapter.py
# Projekt: ORÓMA – Adapter für PolicyEngine (Schach)
# Version: v3.8-r2 (kanonische Hashes, Delta→UCI, Outcome-Fix, Fallback)
# Stand:   2025-10-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
# Adapter-Implementierung für Schach, passend zur PolicyEngine-Schnittstelle:
#   - namespace: "game:chess"
#   - extract_vectors(chain_or_dict) → List[List[float]]
#       * Liest UI-Export der SnapChain (patterns[]).
#       * Nutzt 66-D-Vektoren (0..63 Felder, 64: side-to-move, 65: outcome).
#       * Falls das letzte Pattern im Metadata-Feld "outcome" trägt, wird v[65]
#         auf dieses Ergebnis gesetzt (so versteht die PolicyEngine den Reward).
#   - final_outcome(final_vec) → +1 / 0 / -1 (aus v[65])
#   - action_from_delta(prev, next) → UCI-String (z. B. "e2e4")
#       * Rekonstruiert den Zug aus dem Unterschied der 64 Felder (Promotion
#         heuristisch → 'q'; Castle-Pattern erkannt; en passant best effort).
#   - canonicalize(vec) → (state_hash, perm, inv_perm)
#       * Für Schach verwenden wir eine einfache, stabile Kodierung ohne
#         Symmetriegruppen (perm = Identität).
#   - map_action_through_perm(a, perm) → a (Identität)
#   - fallback_action(state_vec) → simple Heuristik (z. B. e2e4 / e7e5)
#   - vectorize_board(board) → 66-D Vektor (Komfort für UI)
#
# Abhängigkeiten
# ──────────────
#   pip install python-chess
#
# Hinweise
# ────────
# - Diese Implementierung ist bewusst robust gegen unterschiedliche SnapPattern-
#   Serialisierungen (centroid vs. patterns[0]).
# - Die Herleitung von UCI aus Delta arbeitet rein auf Vektor-Ebene, ohne
#   Castling-/EP-Rechte zu benötigen. Die Policy wird in der Praxis von der
#   UI durch Legalitätscheck gefiltert (nicht-legale Vorschläge werden verworfen).
# =============================================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import json
from core.log_guard import log_suppressed
import logging

try:
    import chess
except Exception as e:
    raise RuntimeError(f"[chess_adapter] python-chess fehlt: {e}")

class ChessAdapter:
    namespace: str = "game:chess"

    # ----------------------------- Vektor <-> Board --------------------------

    _PTYPE = {
        1.0: chess.PAWN, 2.0: chess.KNIGHT, 3.0: chess.BISHOP,
        4.0: chess.ROOK, 5.0: chess.QUEEN, 6.0: chess.KING
    }

    @staticmethod
    def _vec_from_board(b: chess.Board) -> List[float]:
        v = [0.0]*66
        for sq, piece in b.piece_map().items():
            sgn = 1.0 if piece.color == chess.WHITE else -1.0
            v[sq] = sgn * {chess.PAWN:1.0, chess.KNIGHT:2.0, chess.BISHOP:3.0,
                           chess.ROOK:4.0, chess.QUEEN:5.0, chess.KING:6.0}[piece.piece_type]
        v[64] = 1.0 if b.turn == chess.WHITE else -1.0
        v[65] = 0.0
        return v

    @staticmethod
    def _board_from_vec(v: List[float]) -> chess.Board:
        b = chess.Board.empty()
        for sq in range(64):
            val = float(v[sq])
            if val == 0.0:
                continue
            color = chess.WHITE if val > 0 else chess.BLACK
            ptype = ChessAdapter._PTYPE.get(abs(val), None)
            if ptype is None:
                continue
            b.set_piece_at(sq, chess.Piece(ptype, color))
        b.turn = (v[64] >= 0.0)
        # Castling/EP-Rechte sind unbekannt; für unsere Zwecke nicht erforderlich.
        b.clear_stack()  # keine History
        return b

    @staticmethod
    def _sq_to_str(sq: int) -> str:
        file = "abcdefgh"[sq % 8]
        rank = "12345678"[sq // 8]
        return file + rank

    # ----------------------------- PolicyEngine API --------------------------

    def extract_vectors(self, chain_or_dict: Any) -> List[List[float]]:
        """
        Erwartet den SnapChain-Export aus der UI:
          { "patterns":[ { "patterns":[[...66...]], "metadata":{...} }, ... ] }
        Fallbacks:
          - wenn 'patterns' fehlt → 'centroid' verwenden (falls vorhanden)
        Outcome-Fix:
          - Wenn das letzte Pattern metadata.outcome trägt, setze v[65] entsprechend.
        """
        d: Dict[str, Any]
        if isinstance(chain_or_dict, dict):
            d = chain_or_dict
        else:
            try:
                if hasattr(chain_or_dict, "to_dict"):
                    d = chain_or_dict.to_dict()
                else:
                    d = json.loads(chain_or_dict) if isinstance(chain_or_dict, (bytes, bytearray, str)) else {}
            except Exception:
                d = {}

        out: List[List[float]] = []
        pats = d.get("patterns") or []
        for p in pats:
            vec: Optional[List[float]] = None
            try:
                arrs = p.get("patterns") or []
                if isinstance(arrs, list) and arrs and isinstance(arrs[0], list) and len(arrs[0]) >= 64:
                    vec = [float(x) for x in arrs[0]]
                elif isinstance(p.get("centroid"), list) and len(p["centroid"]) >= 64:
                    vec = [float(x) for x in p["centroid"]]
            except Exception:
                vec = None
            if vec is None:
                continue
            # Norm auf 66D
            if len(vec) < 66:
                vec = (vec + [0.0]*(66-len(vec)))[:66]
            else:
                vec = vec[:66]
            out.append(vec)

        if out:
            # Outcome-Fix aus letztem Pattern-Metadata
            try:
                last_md = (pats[-1].get("metadata") or {})
                if "outcome" in last_md:
                    val = float(last_md.get("outcome") or 0.0)
                    out[-1][65] = 1.0 if val > 0 else (-1.0 if val < 0 else 0.0)
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.chess_adapter.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
        return out

    def final_outcome(self, final_vec: List[float]) -> int:
        try:
            o = float(final_vec[65])
            return 1 if o > 0 else (-1 if o < 0 else 0)
        except Exception:
            return 0

    def action_from_delta(self, prev: List[float], nxt: List[float]) -> Optional[str]:
        """
        Leite UCI-Zug aus Delta der 64 Felder ab (ohne Engine/History).
        Behandelt:
          - Normale Züge / Captures
          - Promotion (→ 'q' standard)
          - Castling (King-Sprung 2 Felder)
        EP wird best effort erkannt (optional).
        """
        p = [float(x) for x in prev[:64]]
        n = [float(x) for x in nxt[:64]]

        # 1) Castling erkennung: King hat sich 2 Files bewegt
        def _king_move_uci(side_white: bool) -> Optional[str]:
            # e1->g1/c1 bzw. e8->g8/c8
            from_sq = chess.E1 if side_white else chess.E8
            to_g = chess.G1 if side_white else chess.G8
            to_c = chess.C1 if side_white else chess.C8
            if abs(p[from_sq]) == 6.0 and n[from_sq] == 0.0:
                # King weg von e1/e8?
                if abs(n[to_g]) == 6.0:  # O-O
                    return f"{self._sq_to_str(from_sq)}{self._sq_to_str(to_g)}"
                if abs(n[to_c]) == 6.0:  # O-O-O
                    return f"{self._sq_to_str(from_sq)}{self._sq_to_str(to_c)}"
            return None

        color_to_move_prev_white = (prev[64] >= 0.0)
        u_castle = _king_move_uci(color_to_move_prev_white)
        if u_castle:
            return u_castle

        # 2) Generisches Delta: ein From, ein To (ggf. Capture/Promotion)
        from_sq, to_sq = None, None
        moved_val = None
        diffs = [i for i in range(64) if p[i] != n[i]]

        # Heuristik: From = Feld, das vorher eigene Figur hatte und nun 0/andere Farbe ist
        #            To   = Feld, das vorher 0/Feind war und nun eigene Figur trägt
        # Farbe des Movers = Farbe des einzigen unveränderten Vorzeichenwerts, der 'wandert'
        # (einfacher: n[to] != 0 → moved_val = n[to])
        for i in diffs:
            if n[i] != 0.0:
                moved_val = n[i]
                break
        if moved_val is None:
            return None
        mover_white = moved_val > 0

        cand_from = [i for i in diffs if p[i] != 0.0 and (p[i] > 0) == mover_white and n[i] == 0.0]
        cand_to   = [i for i in diffs if n[i] != 0.0 and (n[i] > 0) == mover_white]

        if cand_from:
            from_sq = cand_from[0]
        if cand_to:
            to_sq = cand_to[0]
        if from_sq is None or to_sq is None:
            return None

        # Promotion?
        prom = ""
        # Pawn vor Promotion: von Rank 7->8 (weiß) oder 2->1 (schwarz)
        moved_abs = abs(moved_val)
        if moved_abs >= 2.0:  # Ziel ist Nicht-Pawn
            # War Quelle ein Pawn?
            if abs(p[from_sq]) == 1.0:
                rank_to = to_sq // 8
                if (mover_white and rank_to == 7) or ((not mover_white) and rank_to == 0):
                    prom = "q"  # Standard: zur Dame

        return f"{self._sq_to_str(from_sq)}{self._sq_to_str(to_sq)}{prom}"

    def canonicalize(self, vec: List[float]) -> Tuple[str, List[int], List[int]]:
        """
        Einfache kanonische Kodierung (keine Symmetrien): Hash aus Brettbelegung + Zugfarbe.
        """
        sym = []
        for i in range(64):
            val = float(vec[i])
            if val == 0.0:
                sym.append("_")
            else:
                piece = "PNBRQK" if val > 0 else "pnbrqk"
                idx = int(abs(val))
                sym.append(piece[idx-1] if 1 <= idx <= 6 else "?")
        sym.append("w" if (vec[64] >= 0.0) else "b")
        h = "".join(sym)
        perm = list(range(64))
        return h, perm, perm

    def map_action_through_perm(self, action: str, perm_or_invperm: List[int]) -> str:
        # Keine Brettsymmetrien → Identität
        return action

    def fallback_action(self, state_vec: List[float]) -> Optional[str]:
        """
        Einfache Fallback-Heuristik:
          - Weiß: "e2e4" wenn möglich, sonst "d2d4", sonst irgendein plausibler Bauer-Zug,
                  sonst None
          - Schwarz: analog "e7e5" / "d7d5"
        UI verwirft ILLEGAL, falls es nicht passt.
        """
        # Wir generieren nur "Standardzüge"; Legalität wird in der UI geprüft.
        white = (state_vec[64] >= 0.0)
        return "e2e4" if white else "e7e5"

    # Komfort (wird aktuell von chess_ui nicht benötigt, aber vollständig)
    def vectorize_board(self, board: List[str]) -> List[float]:
        """
        Nicht genutzt für Schach; nur API-Vollständigkeit (TTT verwendet das).
        """
        raise NotImplementedError("vectorize_board wird für Schach nicht verwendet.")
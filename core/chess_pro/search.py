#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/search.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro Suche
# Version: v0.2.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Ressourcenschonende professionelle Suchlogik für Raspberry Pi:
#
#     • Alpha-Beta-Suche
#     • einfache Transposition Table pro Suchentscheidung
#     • Move Ordering: Captures, Promotions, Checks, Rule-/Eval-Delta
#     • Quiescence-Light auf Captures, damit grobe Hängefiguren nicht blind
#       am Horizont stehen bleiben
#     • optional ε-Exploration am Root, damit ORÓMA nicht komplett deterministisch
#       in einer einzigen Linie einfriert
#
# v0.1.1 HARDENING
# ────────────────
#   Der erste Live-Smoke-Test zeigte eine frühe Threefold-Schleife. Deshalb
#   bewertet ChessPro Root-Züge zusätzlich danach, ob sie eine bereits bekannte
#   Partieposition wiederholen würden. Wiederholungen werden nicht illegal
#   gemacht, sondern weich bestraft. Zusätzlich prüft die Suche beim Start die
#   private Apply/Undo-API der vorhandenen Regelengine sichtbar.
#
# v0.2.0 LONG SEARCH
# ──────────────────
#   ChessPro ist kein schneller Mini-Game-Bot mehr, sondern darf im produktiven
#   Nachtfenster deutlich länger rechnen. Die Suche nutzt Iterative Deepening
#   bis zur angeforderten Maximal-Tiefe und liefert immer das letzte vollständig
#   bewertete Root-Ergebnis zurück, falls das Zeitbudget während einer tieferen
#   Iteration erreicht wird. Dadurch kann der Orchestrator große Zeitfenster
#   geben, ohne dass ein Zug ohne Ergebnis endet.
# =============================================================================

from __future__ import annotations

import inspect
import random
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.chess_pro.rules import WHITE, BLACK, Move, ChessPosition, algebraic, PIECE_VALUES

from .evaluator import ProfessionalEvaluator


@dataclass(frozen=True)
class SearchResult:
    move: Optional[Move]
    uci: Optional[str]
    score_cp: int
    depth: int
    nodes: int
    qnodes: int
    elapsed_ms: int
    source: str
    root_scores: Tuple[Tuple[str, int], ...]
    repetition_penalties: Tuple[Tuple[str, int, int], ...] = tuple()
    repetition_guard_moves: int = 0
    repetition_penalty_abs_cp: int = 0
    api_guard_ok: bool = True
    push_failures: int = 0
    pop_failures: int = 0
    depth_reached: int = 0
    tt_hits: int = 0
    cutoffs: int = 0
    timed_out: bool = False


class ChessProSearch:
    """Alpha-Beta + professionelle Bewertung für ChessPro."""

    def __init__(self, evaluator: ProfessionalEvaluator | None = None) -> None:
        self.evaluator = evaluator or ProfessionalEvaluator()
        self.nodes = 0
        self.qnodes = 0
        self.deadline = 0.0
        self.tt: Dict[Tuple[str, int], int] = {}
        self.private_api_ok = self._check_private_api()
        self.push_failures = 0
        self.pop_failures = 0
        self.tt_hits = 0
        self.cutoffs = 0
        self._warned_private_api = False
        self._warned_pop_failure = False

    @staticmethod
    def _move_uci(move: Move) -> str:
        try:
            return algebraic(move)
        except Exception:
            return str(move)

    @staticmethod
    def _is_capture(pos: ChessPosition, move: Move) -> bool:
        try:
            return pos.at(move.to) != "."
        except Exception:
            return False

    @staticmethod
    def _capture_value(pos: ChessPosition, move: Move) -> int:
        try:
            moving = pos.at(move.frm)
            target = pos.at(move.to)
            return int(PIECE_VALUES.get(target, 0)) - max(0, int(PIECE_VALUES.get(moving, 0)) // 12)
        except Exception:
            return 0

    @staticmethod
    def _check_private_api() -> bool:
        """Prüft die private Regelengine-API sichtbar und deterministisch.

        Erwartet wird die im aktuellen `mini_programs/chess/chess_rules.py`
        vorhandene Signatur:

          ChessPosition._apply_no_check(self, move) -> tuple[8]
          ChessPosition._undo_no_check(self, move, captured, promo, old_ep,
                                      old_castle, old_half, old_full,
                                      rook_from, ep_capture_square)

        Die Suche nutzt diese API nur für temporäre Suchbaumzüge. Reale Partien
        verwenden weiter `ChessPosition.apply()`, damit History/RepetitionTable
        korrekt gepflegt werden.
        """
        try:
            apply_fn = getattr(ChessPosition, "_apply_no_check", None)
            undo_fn = getattr(ChessPosition, "_undo_no_check", None)
            if apply_fn is None or undo_fn is None:
                return False
            apply_params = list(inspect.signature(apply_fn).parameters)
            undo_params = list(inspect.signature(undo_fn).parameters)
            return len(apply_params) == 2 and len(undo_params) == 10
        except Exception:
            return False

    def _warn_private_api_once(self, detail: str) -> None:
        if self._warned_private_api:
            return
        self._warned_private_api = True
        print(f"[chess_pro_search] private apply/undo API unavailable or failed: {detail}", file=sys.stderr)

    def _warn_pop_once(self, detail: str) -> None:
        if self._warned_pop_failure:
            return
        self._warned_pop_failure = True
        print(f"[chess_pro_search] undo/pop failure during search: {detail}", file=sys.stderr)

    def _time_up(self) -> bool:
        return bool(self.deadline and time.time() >= self.deadline)

    def _terminal_or_eval(self, pos: ChessPosition) -> int:
        return int(self.evaluator.evaluate(pos).score_cp)

    def _ordered_moves(self, pos: ChessPosition, moves: List[Move], shallow_eval: bool = True) -> List[Move]:
        scored: List[Tuple[int, str, Move]] = []
        for m in moves:
            base = 0
            if self._is_capture(pos, m):
                base += 10_000 + self._capture_value(pos, m)
            if getattr(m, "promo", None):
                base += 8_000
            try:
                piece = pos.at(m.frm)
                # Castling bevorzugen, aber nicht erzwingen.
                if piece.upper() == "K" and abs(m.to[1] - m.frm[1]) == 2:
                    base += 700
                # Professionelle Root-Näherung ohne teuren Evaluator: Zentrum und Entwicklung.
                if m.to in {(3, 3), (3, 4), (4, 3), (4, 4)}:
                    base += 120
                if piece.upper() in {"N", "B"} and m.frm in {(7, 1), (7, 2), (7, 5), (7, 6), (0, 1), (0, 2), (0, 5), (0, 6)}:
                    base += 90
            except Exception:
                pass
            scored.append((int(base), self._move_uci(m), m))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [m for _s, _u, m in scored]

    def _push(self, pos: ChessPosition, move: Move):
        """Schneller Such-Push ohne Repetition-/History-Mutation."""
        if not self.private_api_ok:
            self.push_failures += 1
            self._warn_private_api_once("signature check failed")
            return None
        try:
            state = pos._apply_no_check(move)  # type: ignore[attr-defined]
            if not isinstance(state, tuple) or len(state) != 8:
                self.push_failures += 1
                self._warn_private_api_once(f"unexpected apply state={type(state).__name__}/len={len(state) if isinstance(state, tuple) else 'n/a'}")
                return None
            return state
        except Exception as e:
            self.push_failures += 1
            self._warn_private_api_once(repr(e))
            return None

    def _pop(self, pos: ChessPosition, move: Move, state) -> None:
        try:
            caps, promo, old_ep, old_castle, old_h, old_f, rf, ep_cap = state
            pos._undo_no_check(move, caps, promo, old_ep, old_castle, old_h, old_f, rf, ep_cap)  # type: ignore[attr-defined]
        except Exception as e:
            self.pop_failures += 1
            self._warn_pop_once(repr(e))

    @staticmethod
    def _side_score(score_cp: int, side: str) -> int:
        return int(score_cp if side == WHITE else -score_cp)

    def _repetition_after_push(self, pos: ChessPosition) -> int:
        """Anzahl früherer Vorkommen der Stellung nach temporärem Root-Push.

        `_push()` selbst verändert die RepetitionTable nicht. Daher bedeutet:
          count == 2  → reales `apply()` würde daraus sofort Threefold machen
          count == 1  → Stellung war schon einmal da, Schleifenrisiko
        """
        try:
            key = pos._make_key()  # type: ignore[attr-defined]
            return int(pos.rep.count(key))
        except Exception:
            return 0

    def _repetition_penalty(self, moving_side: str, score_cp: int, previous_count: int) -> int:
        if previous_count <= 0:
            return 0

        # Aus Sicht der ziehenden Seite: negative Werte = schlechter Stand.
        mover_score = self._side_score(int(score_cp), moving_side)

        if previous_count >= 2:
            # Sofortige dreifache Wiederholung soll in Lernpartien vermieden werden,
            # darf aber als Rettungsremis bleiben, wenn die Stellung klar schlecht ist.
            if mover_score <= -350:
                base = 30
            elif mover_score <= -150:
                base = 90
            else:
                base = 240
        else:
            # Erste Wiederholung ist noch legal und manchmal korrekt, aber als
            # Lernsignal oft arm. Deshalb nur milder Malus.
            if mover_score <= -350:
                base = 0
            elif mover_score <= -150:
                base = 15
            else:
                base = 45

        if base <= 0:
            return 0
        return -base if moving_side == WHITE else base

    def _quiescence(self, pos: ChessPosition, alpha: int, beta: int, depth_left: int = 4) -> int:
        self.qnodes += 1
        stand = self._terminal_or_eval(pos)
        if pos.stm == WHITE:
            if stand >= beta:
                return beta
            alpha = max(alpha, stand)
        else:
            if stand <= alpha:
                return alpha
            beta = min(beta, stand)
        if depth_left <= 0 or self._time_up():
            return stand
        try:
            moves = [m for m in pos.generate_legal_moves() if self._is_capture(pos, m) or getattr(m, "promo", None)]
        except Exception:
            return stand
        if not moves:
            return stand
        for m in self._ordered_moves(pos, moves, shallow_eval=False)[:24]:
            st = self._push(pos, m)
            if st is None:
                continue
            score = self._quiescence(pos, alpha, beta, depth_left - 1)
            self._pop(pos, m, st)
            if pos.stm == WHITE:
                if score > alpha:
                    alpha = score
                if alpha >= beta:
                    self.cutoffs += 1
                    break
            else:
                if score < beta:
                    beta = score
                if alpha >= beta:
                    self.cutoffs += 1
                    break
        return alpha if pos.stm == WHITE else beta

    def _alphabeta(self, pos: ChessPosition, depth: int, alpha: int, beta: int) -> int:
        self.nodes += 1
        if self._time_up():
            return self._terminal_or_eval(pos)
        try:
            status = pos.status()
        except Exception:
            status = "ongoing"
        if status != "ongoing":
            return self._terminal_or_eval(pos)
        if depth <= 0:
            return self._quiescence(pos, alpha, beta)
        key = (pos.as_fen(), int(depth))
        cached = self.tt.get(key)
        if cached is not None:
            self.tt_hits += 1
            return int(cached)
        try:
            legal = pos.generate_legal_moves()
        except Exception:
            legal = []
        if not legal:
            return self._terminal_or_eval(pos)
        if pos.stm == WHITE:
            value = -10**9
            for m in self._ordered_moves(pos, legal, shallow_eval=(depth >= 2)):
                st = self._push(pos, m)
                if st is None:
                    continue
                score = self._alphabeta(pos, depth - 1, alpha, beta)
                self._pop(pos, m, st)
                if score > value:
                    value = score
                if value > alpha:
                    alpha = value
                if alpha >= beta:
                    self.cutoffs += 1
                    break
        else:
            value = 10**9
            for m in self._ordered_moves(pos, legal, shallow_eval=(depth >= 2)):
                st = self._push(pos, m)
                if st is None:
                    continue
                score = self._alphabeta(pos, depth - 1, alpha, beta)
                self._pop(pos, m, st)
                if score < value:
                    value = score
                if value < beta:
                    beta = value
                if alpha >= beta:
                    self.cutoffs += 1
                    break
        self.tt[key] = int(value)
        return int(value)

    def choose(self, pos: ChessPosition, depth: int = 2, time_budget_ms: int = 1500, eps: float = 0.0, rng: random.Random | None = None) -> SearchResult:
        """Wählt einen Root-Zug per Iterative Deepening + Alpha-Beta.

        v0.2.0-Verhalten:
          • `depth` ist die maximale Ziel-Tiefe, nicht zwingend garantiert.
          • Die Suche rechnet Tiefe 1..depth, solange das Zeitbudget reicht.
          • Zurückgegeben wird das beste vollständig erzeugte Root-Ergebnis.
          • Telemetrie `depth_reached`, `tt_hits`, `cutoffs`, `timed_out` macht
            sichtbar, ob eine lange Nachtpartie wirklich tiefer gerechnet hat.
        """
        t0 = time.time()
        self.nodes = 0
        self.qnodes = 0
        self.push_failures = 0
        self.pop_failures = 0
        self.tt_hits = 0
        self.cutoffs = 0
        self.deadline = t0 + max(0.05, float(time_budget_ms) / 1000.0)
        self.tt.clear()
        rng = rng or random.Random()
        moving_side = str(getattr(pos, "stm", WHITE))
        try:
            legal = pos.generate_legal_moves()
        except Exception:
            legal = []
        if not legal:
            ev = self.evaluator.evaluate(pos)
            return SearchResult(None, None, ev.score_cp, int(depth), self.nodes, self.qnodes, int((time.time() - t0) * 1000), "no_legal", tuple(), api_guard_ok=bool(self.private_api_ok), push_failures=int(self.push_failures), pop_failures=int(self.pop_failures), depth_reached=0, tt_hits=int(self.tt_hits), cutoffs=int(self.cutoffs), timed_out=bool(self._time_up()))

        ordered = self._ordered_moves(pos, legal, shallow_eval=True)
        best_root_scores: List[Tuple[str, int, Move]] = []
        best_repetition_penalties: List[Tuple[str, int, int]] = []
        best_depth = 0
        target_depth = max(1, int(depth))

        for current_depth in range(1, target_depth + 1):
            if self._time_up() and best_root_scores:
                break
            root_scores: List[Tuple[str, int, Move]] = []
            repetition_penalties: List[Tuple[str, int, int]] = []
            for m in ordered:
                if self._time_up() and root_scores:
                    break
                st = self._push(pos, m)
                if st is None:
                    continue
                raw_score = self._alphabeta(pos, max(0, int(current_depth) - 1), -10**8, 10**8)
                repeat_count = self._repetition_after_push(pos)
                penalty = self._repetition_penalty(moving_side, int(raw_score), repeat_count)
                score = int(raw_score) + int(penalty)
                if penalty:
                    repetition_penalties.append((self._move_uci(m), int(repeat_count), int(penalty)))
                self._pop(pos, m, st)
                root_scores.append((self._move_uci(m), int(score), m))

            if root_scores:
                if pos.stm == WHITE:
                    root_scores.sort(key=lambda t: (t[1], t[0]), reverse=True)
                else:
                    root_scores.sort(key=lambda t: (t[1], t[0]))
                best_root_scores = root_scores
                best_repetition_penalties = repetition_penalties
                best_depth = int(current_depth)
            if self._time_up():
                break

        if not best_root_scores:
            m = ordered[0]
            ev = self.evaluator.evaluate(pos)
            elapsed_ms = int((time.time() - t0) * 1000)
            return SearchResult(
                m,
                self._move_uci(m),
                ev.score_cp,
                int(depth),
                self.nodes,
                self.qnodes,
                elapsed_ms,
                "fallback_ordered",
                tuple(),
                tuple(best_repetition_penalties[:12]),
                repetition_guard_moves=len(best_repetition_penalties),
                repetition_penalty_abs_cp=sum(abs(p) for _u, _c, p in best_repetition_penalties),
                api_guard_ok=bool(self.private_api_ok),
                push_failures=int(self.push_failures),
                pop_failures=int(self.pop_failures),
                depth_reached=int(best_depth),
                tt_hits=int(self.tt_hits),
                cutoffs=int(self.cutoffs),
                timed_out=bool(self._time_up()),
            )

        source = "iterdeep_search"
        chosen = best_root_scores[0]
        if float(eps) > 0.0 and rng.random() < float(eps):
            top_n = min(4, len(best_root_scores))
            chosen = rng.choice(best_root_scores[:top_n])
            source = "iterdeep_search_eps_topn"
        if best_repetition_penalties:
            source = f"{source}_repguard"
        if best_depth < target_depth:
            source = f"{source}_partial_depth"
        elapsed_ms = int((time.time() - t0) * 1000)
        return SearchResult(
            move=chosen[2],
            uci=chosen[0],
            score_cp=int(chosen[1]),
            depth=int(target_depth),
            nodes=int(self.nodes),
            qnodes=int(self.qnodes),
            elapsed_ms=elapsed_ms,
            source=source,
            root_scores=tuple((u, int(s)) for u, s, _m in best_root_scores[:12]),
            repetition_penalties=tuple(best_repetition_penalties[:12]),
            repetition_guard_moves=len(best_repetition_penalties),
            repetition_penalty_abs_cp=sum(abs(p) for _u, _c, p in best_repetition_penalties),
            api_guard_ok=bool(self.private_api_ok),
            push_failures=int(self.push_failures),
            pop_failures=int(self.pop_failures),
            depth_reached=int(best_depth),
            tt_hits=int(self.tt_hits),
            cutoffs=int(self.cutoffs),
            timed_out=bool(self._time_up()),
        )

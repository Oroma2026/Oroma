#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/chess_pro/episode_trace.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro Episoden- und SnapChain-Trace
# Version: v0.2.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Persistierbarer Trace für professionelle ChessPro-Partien. Der Trace hält
#   jede Entscheidung als Stellungssnapshot fest, damit ORÓMA nicht nur den
#   terminalen Reward sieht, sondern die komplette Kette:
#
#     State_t → Move_t → State_t+1 → ... → Ergebnis
#
#   Das ist die notwendige Grundlage für spätere NMR-Ähnlichkeit, Dream-Replay,
#   Policy-Kalibrierung und Domänentransfer.
#
# v0.2.0 LEARNING-LOOP-KONTROLLE
# ───────────────────────────────
#   UniversalPolicy wertet `outcome` nur nach Vorzeichen aus. Reine Remispartien
#   würden deshalb ohne Zusatzlogik fast nur Draw-Zähler erzeugen. ChessPro hält
#   nun pro Entscheidung die Stellungsänderung aus Sicht der ziehenden Seite fest
#   und erzeugt bei nicht-entscheidenden Partien ein kontrolliertes, schwellen-
#   basiertes Lernsignal: gute Stellungsverbesserung positiv, klare Verschlechterung
#   negativ, kleine Bewertungsrauschen neutral. Damit machen lange Suchpartien auch
#   dann Sinn, wenn sie nicht mit Matt enden.
# =============================================================================

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .position_encoder import PositionEncoding
from .search import SearchResult


@dataclass
class ChessProDecision:
    ply: int
    side_to_move: str
    focus_side: str
    fen_before: str
    fen_after: str
    state_hash: str
    action: str
    score_cp: int
    eval_before_cp: int
    eval_after_cp: int
    eval_delta_cp: int
    mover_delta_cp: int
    learning_signal: float
    legal_count: int
    search_nodes: int
    search_qnodes: int
    search_ms: int
    source: str
    tokens: List[str]
    vector: List[float]
    rule_hits: List[str]


@dataclass
class ChessProTrace:
    namespace: str = "game:chess_pro"
    focus_side: str = "white"
    ts_start: int = field(default_factory=lambda: int(time.time()))
    ts_end: int = 0
    outcome: str = "D"
    terminal_reason: str = "unknown"
    decisions: List[ChessProDecision] = field(default_factory=list)

    @staticmethod
    def _learning_signal(mover_delta_cp: int) -> float:
        """Schwellenbasiertes Signal für UniversalPolicy.

        Die Policy speichert nur Pos/Neg/Draw. Deshalb wird bewusst nicht jede
        minimale Centipawn-Schwankung als Lernen gewertet. Erst ab ca. 35 cp
        gilt ein Zug als verwertbare Verbesserung/Verschlechterung.
        """
        d = int(mover_delta_cp)
        if d >= 35:
            return 1.0
        if d <= -35:
            return -1.0
        return 0.0

    def add_decision(self, ply: int, enc: PositionEncoding, result: SearchResult, fen_after: str, legal_count: int, eval_after_cp: int | None = None) -> None:
        ev = enc.evaluation
        eval_before = int(ev.score_cp)
        eval_after = int(eval_after_cp if eval_after_cp is not None else result.score_cp)
        eval_delta = int(eval_after - eval_before)
        mover_delta = int(eval_delta if enc.side_to_move == "white" else -eval_delta)
        signal = float(self._learning_signal(mover_delta))
        own_hits = ev.white_hits if enc.side_to_move == "white" else ev.black_hits
        hit_names = [f"{h.name}:{h.score_cp}" for h in sorted(own_hits, key=lambda h: abs(h.score_cp), reverse=True)[:8]]
        self.decisions.append(
            ChessProDecision(
                ply=int(ply),
                side_to_move=str(enc.side_to_move),
                focus_side=str(enc.focus_side),
                fen_before=str(enc.fen),
                fen_after=str(fen_after),
                state_hash=str(enc.state_hash),
                action=str(result.uci or ""),
                score_cp=int(result.score_cp),
                eval_before_cp=int(eval_before),
                eval_after_cp=int(eval_after),
                eval_delta_cp=int(eval_delta),
                mover_delta_cp=int(mover_delta),
                learning_signal=float(signal),
                legal_count=int(legal_count),
                search_nodes=int(result.nodes),
                search_qnodes=int(result.qnodes),
                search_ms=int(result.elapsed_ms),
                source=str(result.source),
                tokens=list(enc.tokens),
                vector=[float(x) for x in enc.vector],
                rule_hits=hit_names,
            )
        )

    def finish(self, outcome: str, terminal_reason: str) -> None:
        self.outcome = str(outcome or "D")
        self.terminal_reason = str(terminal_reason or "unknown")
        self.ts_end = int(time.time())

    def to_blob_dict(self) -> Dict[str, Any]:
        return {
            "kind": "chess_pro_trace",
            "version": "v0.2.0",
            "namespace": self.namespace,
            "focus_side": self.focus_side,
            "ts_start": int(self.ts_start),
            "ts_end": int(self.ts_end or time.time()),
            "outcome": self.outcome,
            "terminal_reason": self.terminal_reason,
            "steps": [d.__dict__ for d in self.decisions],
        }

    def to_blob_bytes(self) -> bytes:
        return json.dumps(self.to_blob_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def learning_summary(self) -> Dict[str, int]:
        pos = neg = draw = 0
        for d in self.decisions:
            sig = float(getattr(d, "learning_signal", 0.0) or 0.0)
            if sig > 0:
                pos += 1
            elif sig < 0:
                neg += 1
            else:
                draw += 1
        return {"shaped_pos": int(pos), "shaped_neg": int(neg), "shaped_draw": int(draw)}

    def learn_items(self) -> List[Dict[str, Any]]:
        """UniversalPolicy-kompatible Lernitems aus allen Entscheidungen.

        Outcome wird aus Sicht der entscheidenden Seite berechnet. Bei echten
        Siegen/Niederlagen dominiert das terminale Ergebnis. Bei Remis oder
        Abbruch nach `max_plies`/Zeitbudget wird kontrolliert über die
        Stellungsverbesserung gelernt, damit lange Berechnungen nicht als
        reiner Draw-Müll in `policy_rules` landen.
        """
        items: List[Dict[str, Any]] = []
        for d in self.decisions:
            if not d.state_hash or not d.action:
                continue
            if self.outcome == "W":
                out = 1.0 if d.side_to_move == "white" else -1.0
            elif self.outcome == "B":
                out = 1.0 if d.side_to_move == "black" else -1.0
            else:
                out = float(getattr(d, "learning_signal", 0.0) or 0.0)
            items.append({
                "state_hash": d.state_hash,
                "action": d.action,
                "outcome": out,
                "ts": int(self.ts_end or time.time()),
                "centroid": list(d.vector),
                "meta": {
                    "kind": "chess_pro_learning_item",
                    "terminal_outcome": self.outcome,
                    "terminal_reason": self.terminal_reason,
                    "mover_delta_cp": int(getattr(d, "mover_delta_cp", 0) or 0),
                    "learning_signal": float(out),
                },
            })
        return items

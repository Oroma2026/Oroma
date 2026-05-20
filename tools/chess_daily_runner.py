#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess Daily Runner – Policy+Explore → episodes + DB-SnapChains
# Version: v3.7.3
# Stand:   2026-03-10
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Führt Schach automatisiert im Headless-Modus aus und schreibt die Ergebnisse
# in die ORÓMA-DB – analog zu den inzwischen reparierten Snake-/Pong-Runnern.
#
# Dieser Runner erzeugt jetzt ZWEI Persistenzebenen:
#   • episodes / episodic_metrics
#       - game:chess:policy_batch
#       - game:chess:explore_batch
#   • snapchains
#       - origin="game:chess"
#       - namespace="game:chess"
#       - kind="chess_policy_trace"
#
# WARUM DIESER UMBAU
# ------------------
# Der frühere Daily-Runner lernte nur extrem grob:
#   • pro Partie maximal den letzten Zug pro Seite
#   • ausschließlich terminaler Reward (+1/-1/0)
#   • keine trainierbare DB-SnapChain-Basis für nachgelagerte Policy-/Replay-Pfade
#
# Für ORÓMA war das zu schwach. Diese Version behebt das produktiv:
#   1) Vollständige seitengetrennte Partietraces (White / Black)
#   2) Dichtes Online-Lernen über ALLE Entscheidungen der jeweiligen Seite
#   3) Späterer Partieverlauf wird stärker gewichtet (MC-ähnliche Wiederholung)
#   4) Persistente DB-SnapChains für späteres Training via policy_engine
#   5) Optionaler, standardmäßig DEAKTIVIERTER Opponent-Credit für direkte
#      Vorlagen auf späte gegnerische Gewinnantworten
#
# LERNLOGIK (WICHTIG)
# -------------------
# Da UniversalPolicy.learn_many() intern nur das Outcome-SIGN nutzt
# (pos/neg/draw), wird eine dichtere Kreditvergabe über WIEDERHOLUNG erreicht:
#   • Jede Entscheidung einer Seite wird gelernt, nicht nur der letzte Zug.
#   • Spätere Entscheidungen werden häufiger wiederholt als frühe Züge.
#   • Dadurch werden Endspiel/konkrete taktische Entscheidungen stärker,
#     Eröffnung/Mittelspiel aber weiterhin sichtbar berücksichtigt.
#
# EHRLICHE EINORDNUNG / PRODUKTIVER STATUS
# ---------------------------------------
# Der Chess-Pfad liefert in ORÓMA heute vor allem SAUBERE, trainierbare
# Partietraces (episodes + snapchains + policy_rules) für Replay, Analyse,
# spätere policy_engine-Ingestion und zukünftige stärkere Modelle.
#
# Das tabellarische Online-Lernen über UniversalPolicy ist für Chess weiterhin
# experimentell und strukturell begrenzt, weil der kombinierte
# (state_hash, action)-Raum trotz Feature-Key groß bleibt. Chess ist damit
# produktiv nützlich als Trace-/Daten-Lieferant, aber derzeit kein starker
# Leitbenchmark für tabellarisches Policy-Lernen.
#
# CHAIN-FORMAT / PREHASH-KOMPATIBILITÄT
# -------------------------------------
# Jede Trace-Chain ist bewusst prehash-kompatibel aufgebaut:
#   • steps[*].state_hash / steps[*].sh
#   • steps[*].a         (UCI-Zug der VORHERIGEN Entscheidung)
#   • terminaler Endzustand mit terminal:<win|loss|draw>
#
# So kann core.policy_engine.PolicyEngine.ingest_chain() die Züge direkt aus
# der DB lesen, ohne Adapter-Zwang oder UI-Export.
#
# PRODUKTIONSREGELN
# -----------------
# • Headless, keine pygame/UI-Abhängigkeit
# • Keine offenen DB-Conns: nur sql_manager-Helfer
# • Keine stillen Fehler: DB-Fehler → stderr + ok=false
# • DBWriter-kompatibel über sql_manager.insert_snapchain()/insert_episode()
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from core import sql_manager


def _env_float(name: str, default: float) -> float:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        v = (os.environ.get(name, "") or "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def _env_str(name: str, default: str) -> str:
    v = (os.environ.get(name, "") or "").strip()
    return v if v else default


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if not v:
        return bool(default)
    return v in {"1", "true", "yes", "on"}


class PolicyShim:
    """UniversalPolicy Wrapper (namespace=game:chess).

    WICHTIG ZUR STABILITÄT DER POLICY-DB:
    ------------------------------------
    Der Chess-State-Key kann per ENV zwischen klassischem FEN-Key und einem
    gröber aggregierten Feature-Key umgeschaltet werden. Sobald die Buckets der
    Feature-Repräsentation geändert werden, ändern sich auch ALLE state_hashes.
    Bereits gelernte policy_rules-Zeilen für namespace=game:chess werden dann
    fachlich inkompatibel, weil neue Läufe die alten Keys nicht mehr treffen.

    Deshalb gilt produktiv:
      • OROMA_CHESS_STATE_MODE nicht leichtfertig wechseln
      • Änderungen an _material_bucket(), _game_phase(), _castling_bucket()
        oder _material_structure() wie einen Schema-/Key-Wechsel behandeln
      • bei bewusstem Umbau Chess-Policy-Daten gezielt neu aufbauen
    """

    def __init__(self, namespace: str):
        self.namespace = namespace
        self.pol = None
        self.state_mode = (_env_str("OROMA_CHESS_STATE_MODE", "fen") or "fen").strip().lower()
        if self.state_mode not in {"fen", "features"}:
            self.state_mode = "fen"
        try:
            from core.universal_policy import Policy  # type: ignore
            self.pol = Policy(namespace=namespace)
        except Exception:
            self.pol = None

    @staticmethod
    def stable_fen_key(fen: str) -> str:
        # FEN: "pieces side castling ep halfmove fullmove"
        # Wir nehmen nur die ersten 4 Felder.
        parts = (fen or "").strip().split()
        if len(parts) >= 4:
            return " ".join(parts[:4])
        return (fen or "").strip()

    @staticmethod
    def _material_bucket(board: Any) -> str:
        try:
            import chess  # type: ignore
            val = {
                chess.PAWN: 1,
                chess.KNIGHT: 3,
                chess.BISHOP: 3,
                chess.ROOK: 5,
                chess.QUEEN: 9,
            }
            score = 0
            for piece_t, v in val.items():
                score += len(board.pieces(piece_t, chess.WHITE)) * int(v)
                score -= len(board.pieces(piece_t, chess.BLACK)) * int(v)
            if score <= -6:
                return "m2"
            if score <= -2:
                return "m1"
            if score <= 2:
                return "00"
            if score <= 6:
                return "p1"
            return "p2"
        except Exception:
            return "00"

    @staticmethod
    def _game_phase(board: Any) -> str:
        try:
            import chess  # type: ignore
            total = (
                len(board.pieces(chess.QUEEN, chess.WHITE))
                + len(board.pieces(chess.QUEEN, chess.BLACK))
                + len(board.pieces(chess.ROOK, chess.WHITE))
                + len(board.pieces(chess.ROOK, chess.BLACK))
                + len(board.pieces(chess.BISHOP, chess.WHITE))
                + len(board.pieces(chess.BISHOP, chess.BLACK))
                + len(board.pieces(chess.KNIGHT, chess.WHITE))
                + len(board.pieces(chess.KNIGHT, chess.BLACK))
            )
            if total >= 12:
                return "o"
            if total >= 6:
                return "m"
            return "e"
        except Exception:
            return "m"

    @staticmethod
    def _castling_bucket(board: Any) -> str:
        try:
            import chess  # type: ignore
            w = bool(board.has_castling_rights(chess.WHITE))
            b = bool(board.has_castling_rights(chess.BLACK))
            if w and b:
                return "bb"
            if w:
                return "wo"
            if b:
                return "bo"
            return "nn"
        except Exception:
            return "nn"

    @staticmethod
    def _material_structure(board: Any) -> str:
        """Grobe Heavy/Light-Materialstruktur mit neun stabilen Klassen.

        WICHTIG:
        Diese Buckets sind Teil des Chess-Feature-State-Keys. Änderungen an
        dieser Funktion verändern damit bestehende state_hash-Werte für
        namespace=game:chess. Für produktive Vergleiche muss deshalb entweder
        bewusst mit getrenntem Bestand gearbeitet oder game:chess in
        policy_rules vor dem Test gezielt neu aufgebaut werden.
        """
        try:
            import chess  # type: ignore
            w_heavy = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.ROOK, chess.WHITE))
            b_heavy = len(board.pieces(chess.QUEEN, chess.BLACK)) + len(board.pieces(chess.ROOK, chess.BLACK))
            w_light = len(board.pieces(chess.BISHOP, chess.WHITE)) + len(board.pieces(chess.KNIGHT, chess.WHITE))
            b_light = len(board.pieces(chess.BISHOP, chess.BLACK)) + len(board.pieces(chess.KNIGHT, chess.BLACK))
            heavy = ">" if w_heavy > b_heavy else "<" if w_heavy < b_heavy else "="
            light = ">" if w_light > b_light else "<" if w_light < b_light else "="
            return f"{heavy}{light}"
        except Exception:
            return "=="

    def feature_state_key(self, fen: str) -> str:
        try:
            import chess  # type: ignore
            board = chess.Board(fen)
        except Exception:
            return self.stable_fen_key(fen)
        turn = "w" if bool(getattr(board, "turn", True)) else "b"
        material = self._material_bucket(board)
        in_check = "c" if bool(board.is_check()) else "n"
        castling = self._castling_bucket(board)
        phase = self._game_phase(board)
        struct = self._material_structure(board)
        return f"chess:f:{turn}:{material}:{in_check}:{castling}:{phase}:{struct}"

    def state_hash(self, fen: str) -> str:
        if self.state_mode == "features":
            return self.feature_state_key(fen)
        return f"chess:v1:{self.stable_fen_key(fen)}"

    def choose(self, fen: str, legal: List[str], side: str) -> str:
        if not legal:
            return ""
        if not self.pol:
            return str(random.choice(legal))
        sh = self.state_hash(fen)
        return str(self.pol.choose(sh, legal, side=side))

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not self.pol or not items:
            return 0
        res = self.pol.learn_many(items)
        if res is None:
            return len(items)
        try:
            return int(res)
        except Exception:
            return len(items)


def _winner_to_outcome(w: Optional[str]) -> Optional[str]:
    if w == "white":
        return "X"
    if w == "black":
        return "O"
    if w == "draw":
        return "D"
    return None


def _side_from_turn(turn: str) -> str:
    return "W" if str(turn) == "w" else "B"


def _side_outcome(outcome: str, side: str) -> int:
    if outcome == "D":
        return 0
    if outcome == "X":
        return 1 if side == "W" else -1
    if outcome == "O":
        return 1 if side == "B" else -1
    return 0


def _terminal_hash(outcome: str, side: str, plies: int) -> str:
    rel = _side_outcome(outcome, side)
    tag = "win" if rel > 0 else ("loss" if rel < 0 else "draw")
    return f"chess:terminal:{side}:{tag}:plies={int(plies)}"


def _trace_repeat_count(index_1based: int, total: int, outcome_sign: int) -> int:
    """
    Liefert die Wiederholungszahl für eine Entscheidung innerhalb der Partie.

    Hintergrund:
      UniversalPolicy.learn_many() verarbeitet nur die Sign-Richtung des Outcomes.
      Um spätere Züge stärker zu gewichten, wiederholen wir spätere Entscheidungen
      kontrolliert häufiger. So entsteht eine einfache, robuste MC-artige
      Kreditvergabe ohne die UniversalPolicy-DB-Struktur zu ändern.
    """
    total = max(1, int(total))
    idx = max(1, int(index_1based))

    # Draws nicht künstlich aufblasen – sonst verdünnen sich pos/neg zu stark.
    if int(outcome_sign) == 0:
        return 1

    max_extra = max(0, _env_int("OROMA_CHESS_TRACE_MAX_EXTRA_REPEATS", 3))
    if max_extra <= 0 or total <= 1:
        return 1

    frac = float(idx - 1) / float(max(1, total - 1))
    extra = int(math.floor(frac * float(max_extra) + 1e-9))
    return 1 + max(0, extra)


def _build_side_chain(namespace: str,
                      mode: str,
                      side: str,
                      outcome: str,
                      plies: int,
                      max_plies: int,
                      decision_trace: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Baut eine prehash-kompatible Trace-Chain für genau EINE Seite.

    steps-Semantik:
      • steps[0] enthält nur den ersten Entscheidungszustand (noch ohne Aktion)
      • steps[i>0] enthält den NÄCHSTEN Entscheidungszustand und trägt die
        Aktion aus dem VORHERIGEN Entscheidungszustand in `a`
      • letzter Eintrag ist ein terminaler Pseudo-Zustand mit der letzten Aktion
    """
    if not decision_trace:
        return None

    rel = _side_outcome(outcome, side)
    steps: List[Dict[str, Any]] = []

    first = decision_trace[0]
    steps.append({
        "t": 0,
        "state_hash": str(first["state_hash"]),
        "sh": str(first["state_hash"]),
        "fen_key": str(first["fen_key"]),
        "mode": str(mode),
        "side": str(side),
        "ply": int(first["ply"]),
    })

    for idx in range(1, len(decision_trace)):
        cur = decision_trace[idx]
        prev = decision_trace[idx - 1]
        steps.append({
            "t": int(idx),
            "state_hash": str(cur["state_hash"]),
            "sh": str(cur["state_hash"]),
            "fen_key": str(cur["fen_key"]),
            "a": str(prev["action"]),
            "mode": str(mode),
            "side": str(side),
            "ply": int(cur["ply"]),
        })

    last = decision_trace[-1]
    term_tag = "win" if rel > 0 else ("loss" if rel < 0 else "draw")
    steps.append({
        "t": int(len(decision_trace)),
        "state_hash": _terminal_hash(outcome, side, plies),
        "sh": _terminal_hash(outcome, side, plies),
        "a": str(last["action"]),
        "mode": str(mode),
        "side": str(side),
        "ply": int(plies),
        "terminal": str(term_tag),
    })

    return {
        "schema_version": "3.7.3",
        "kind": "chess_policy_trace",
        "origin": str(namespace or "game:chess"),
        "namespace": str(namespace or "game:chess"),
        "mode": str(mode),
        "side": str(side),
        "result": int(rel),
        "plies_total": int(plies),
        "steps_total": int(max(0, len(steps) - 1)),
        "steps": steps,
        "meta": {
            "runner": "tools/chess_daily_runner.py",
            "source": "chess_daily_runner",
            "mode": str(mode),
            "side": str(side),
            "outcome": str(outcome),
            "outcome_rel": int(rel),
            "max_plies": int(max_plies),
            "decision_count": int(len(decision_trace)),
            "reward_mode": "full_trace_repeated_terminal",
        },
    }


def _build_learn_items_for_side(side: str,
                                outcome: str,
                                decision_trace: List[Dict[str, Any]],
                                opponent_trace: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Erzeugt dichte Online-Lernitems für alle Entscheidungen einer Seite.

    Design:
      • Jede Entscheidung erhält das finale Ergebnis aus Sicht der Seite.
      • Spätere Entscheidungen werden häufiger wiederholt als frühe.
      • Optionaler Opponent-Credit kann direkte Vorlagen auf späte,
        entscheidende gegnerische Gewinnzüge leicht negativer markieren.

    WICHTIG:
      Der Opponent-Credit ist absichtlich konservativ:
        • nur aktiv, wenn OROMA_CHESS_ENABLE_OPPONENT_CREDIT=1 (Default: aus)
        • nur wenn die GEGNERSEITE die Partie gewinnt
        • nur für das letzte Viertel der gegnerischen Entscheidungen
        • nur ein einzelnes Zusatz-Negativitem pro betroffener Vorlage

      So wird Rauschen aus frühen oder rein zufälligen Spielphasen begrenzt.
    """
    rel = _side_outcome(outcome, side)
    out_f = float(rel)
    total = len(decision_trace)
    now = int(time.time())
    items: List[Dict[str, Any]] = []

    opp_credit_enabled = _env_bool("OROMA_CHESS_ENABLE_OPPONENT_CREDIT", False)
    opp_side = "B" if side == "W" else "W"
    opp_rel = _side_outcome(outcome, opp_side)
    decisive_opp_plies: set[int] = set()

    if opp_credit_enabled and opponent_trace and opp_rel > 0:
        opp_total = len(opponent_trace)
        cutoff = max(0, opp_total - max(2, opp_total // 4))
        for opp_entry in opponent_trace[cutoff:]:
            try:
                decisive_opp_plies.add(int(opp_entry["ply"]))
            except Exception:
                continue

    for i, entry in enumerate(decision_trace, start=1):
        reps = _trace_repeat_count(i, total, rel)
        base = {
            "state_hash": str(entry["state_hash"]),
            "action": str(entry["action"]),
            "outcome": out_f,
            "ts": now,
            "side": str(side),
        }
        for _ in range(max(1, reps)):
            items.append(dict(base))

        if decisive_opp_plies:
            next_ply = int(entry["ply"]) + 1
            if next_ply in decisive_opp_plies:
                items.append({
                    "state_hash": str(entry["state_hash"]),
                    "action": str(entry["action"]),
                    "outcome": -1.0,
                    "ts": now,
                    "side": str(side),
                })
    return items


def run_one_game(rng: random.Random,
                 shim: PolicyShim,
                 mode: str,
                 eps_white: float,
                 eps_black: float,
                 explore_moves_white: int,
                 explore_moves_black: int,
                 max_plies: int,
                 learn: bool,
                 namespace: str) -> Dict[str, Any]:
    from mini_programs.chess.chess_game import ChessGame

    g = ChessGame()
    plies = 0
    # Explore-Budget und -EPS werden seitengetrennt geführt.
    # Hintergrund: In der Praxis zeigte Chess bisher einen starken Weiß-Vorteil
    # (X gewinnt gelegentlich, O praktisch nie). Ein asymmetrisch leicht höheres
    # Schwarz-Explore-Budget erlaubt dem Runner, für O überhaupt positive Traces
    # zu sammeln, ohne den Policy-Modus oder andere Spiele zu beeinflussen.
    explore_budget = {"W": 0, "B": 0}
    explore_budget_limit = {"W": int(max(0, explore_moves_white)), "B": int(max(0, explore_moves_black))}
    explore_eps = {"W": float(max(0.0, eps_white)), "B": float(max(0.0, eps_black))}

    # Vollständige Entscheidungsfolge je Seite.
    decision_trace: Dict[str, List[Dict[str, Any]]] = {"W": [], "B": []}

    while True:
        w = g.winner()
        if w is not None:
            outcome = _winner_to_outcome(w)
            break
        if plies >= max_plies:
            outcome = "D"
            break

        fen = g.fen()
        legal = g.legal_uci() or []
        if not legal:
            outcome = "D"
            break

        side = _side_from_turn(g.turn)
        side_for_policy = side
        state_hash = shim.state_hash(fen)
        fen_key = shim.stable_fen_key(fen)

        def pick() -> str:
            if mode == "explore":
                side_budget = int(explore_budget_limit.get(side, 0))
                side_eps = float(explore_eps.get(side, 0.0))
                if explore_budget[side] < side_budget:
                    explore_budget[side] += 1
                    return str(rng.choice(legal))
                if rng.random() < side_eps:
                    return str(rng.choice(legal))
            return str(shim.choose(fen, legal, side=side_for_policy))

        a = pick()
        if a not in legal:
            a = str(rng.choice(legal))

        decision_trace[side].append({
            "state_hash": state_hash,
            "fen_key": fen_key,
            "action": str(a),
            "ply": int(plies),
            "side": str(side),
            "mode": str(mode),
        })

        ok = g.play_uci(a)
        if not ok:
            outcome = "D"
            break

        plies += 1

        w2 = g.winner()
        if w2 is not None:
            outcome = _winner_to_outcome(w2)
            break

    learn_items: List[Dict[str, Any]] = []
    chains: List[Dict[str, Any]] = []

    for side in ("W", "B"):
        opp_side = "B" if side == "W" else "W"
        trace = decision_trace.get(side) or []
        opp_trace = decision_trace.get(opp_side) or []
        if not trace:
            continue
        chain = _build_side_chain(namespace, mode, side, str(outcome), plies, max_plies, trace)
        if chain is not None:
            chains.append(chain)
        if learn:
            learn_items.extend(
                _build_learn_items_for_side(
                    side,
                    str(outcome),
                    trace,
                    opponent_trace=opp_trace,
                )
            )

    return {
        "outcome": str(outcome),
        "plies": int(plies),
        "learn_items_count": int(len(learn_items)),
        "learn_items": learn_items,
        "chains": chains,
    }


def run_batch(rng: random.Random,
              shim: PolicyShim,
              games: int,
              mode: str,
              eps_white: float,
              eps_black: float,
              explore_moves_white: int,
              explore_moves_black: int,
              max_plies: int,
              learn: bool,
              label: str,
              source: str,
              flush_every_games: int = 0,
              progress_every_games: int = 0) -> Dict[str, Any]:
    """
    Führt einen Chess-Batch robust aus.

    Wichtige Produktionsänderung gegenüber der ersten Chess-DB-Chain-Version:
      • SnapChains werden CHUNKWEISE geflusht statt erst ganz am Ende
      • Fortschritt wird regelmäßig kompakt ausgegeben
      • Spiele, die nur über die Ply-Grenze beendet werden, werden gezählt

    Hintergrund:
      Bei Chess sind Partien deutlich länger und teurer als bei Snake/Pong.
      Wenn Persistenz und Summary erst ganz am Ende passieren, sieht ein längerer
      Lauf von außen leicht wie ein Hänger aus. Chunk-Flush + Progress-Output
      macht den Runner beobachtbar und reduziert das Risiko, dass bei einem
      Abbruch die gesamte DB-Trace eines großen Batches verloren geht.
    """
    t0 = time.time()
    wins_x = 0
    wins_o = 0
    draws = 0
    total_plies = 0
    total_learn_items = 0
    total_chains_written = 0
    total_learn_flushed = 0
    total_learn_flushes = 0
    max_plies_seen = 0
    draws_by_cap = 0
    chunk_chains: List[Dict[str, Any]] = []
    learn_buffer: List[Dict[str, Any]] = []

    games = max(0, int(games))
    flush_every_games = max(1, int(flush_every_games or _env_int("OROMA_CHESS_FLUSH_EVERY_GAMES", 10)))
    progress_every_games = max(1, int(progress_every_games or _env_int("OROMA_CHESS_PROGRESS_EVERY_GAMES", 10)))
    progress_enabled = _env_bool("OROMA_CHESS_PROGRESS_ENABLED", True)

    learn_flush_items = max(25, int(_env_int("OROMA_CHESS_LEARN_FLUSH_ITEMS", 50)))
    learn_subchunk_items = max(10, int(_env_int("OROMA_CHESS_LEARN_SUBCHUNK_ITEMS", 20)))
    eps_white = float(max(0.0, eps_white if mode == "explore" else 0.0))
    eps_black = float(max(0.0, eps_black if mode == "explore" else 0.0))
    explore_moves_white = int(max(0, explore_moves_white if mode == "explore" else 0))
    explore_moves_black = int(max(0, explore_moves_black if mode == "explore" else 0))

    def _flush_learn(force: bool = False) -> int:
        nonlocal learn_buffer, total_learn_flushed, total_learn_flushes
        if not learn_buffer:
            return 0
        if (not force) and len(learn_buffer) < learn_flush_items:
            return 0
        batch = list(learn_buffer)
        learn_buffer = []
        flushed = 0
        try:
            for off in range(0, len(batch), learn_subchunk_items):
                sub = batch[off:off + learn_subchunk_items]
                if not sub:
                    continue
                if progress_enabled:
                    print(json.dumps({
                        "phase": "learn_many_begin",
                        "game": "chess",
                        "mode": mode,
                        "subchunk_items": int(len(sub)),
                        "subchunk_index": int((off // learn_subchunk_items) + 1),
                        "subchunk_total": int((len(batch) + learn_subchunk_items - 1) // learn_subchunk_items),
                        "learn_items_flushed_total_before": int(total_learn_flushed),
                    }, ensure_ascii=False), flush=True)
                shim.learn_many(sub)
                flushed += int(len(sub))
                total_learn_flushed += int(len(sub))
                total_learn_flushes += 1
                if progress_enabled:
                    print(json.dumps({
                        "phase": "learn_many_done",
                        "game": "chess",
                        "mode": mode,
                        "subchunk_items": int(len(sub)),
                        "subchunk_index": int((off // learn_subchunk_items) + 1),
                        "subchunk_total": int((len(batch) + learn_subchunk_items - 1) // learn_subchunk_items),
                        "learn_items_flushed_total": int(total_learn_flushed),
                        "learn_flushes": int(total_learn_flushes),
                    }, ensure_ascii=False), flush=True)
            if progress_enabled and (force or flushed >= learn_flush_items):
                print(json.dumps({
                    "phase": "learn_flush",
                    "game": "chess",
                    "mode": mode,
                    "learn_items_flushed": int(flushed),
                    "learn_items_flushed_total": int(total_learn_flushed),
                    "learn_flushes": int(total_learn_flushes),
                    "learn_subchunk_items": int(learn_subchunk_items),
                }, ensure_ascii=False), flush=True)
            return int(flushed)
        except Exception as e:
            print(f"[chess_daily_runner] learn_many flush failed: {e!r}", file=sys.stderr)
            return int(flushed)

    def _flush_chunk(force: bool = False) -> int:
        nonlocal chunk_chains, total_chains_written
        if not chunk_chains:
            return 0
        written = _write_snapchains({
            "ts_end": int(time.time()),
            "namespace": shim.namespace,
            "mode": mode,
            "chains": list(chunk_chains),
        })
        total_chains_written += int(written)
        chunk_chains = []
        if progress_enabled and (force or written):
            print(json.dumps({
                "phase": "batch_flush",
                "game": "chess",
                "mode": mode,
                "chains_written": int(written),
                "chains_written_total": int(total_chains_written),
                "eps_white": float(eps_white),
                "eps_black": float(eps_black),
                "explore_moves_white": int(explore_moves_white),
                "explore_moves_black": int(explore_moves_black),
            }, ensure_ascii=False), flush=True)
        return int(written)

    for idx in range(1, games + 1):
        r = run_one_game(
            rng=rng,
            shim=shim,
            mode=mode,
            eps_white=eps_white,
            eps_black=eps_black,
            explore_moves_white=explore_moves_white,
            explore_moves_black=explore_moves_black,
            max_plies=max_plies,
            learn=learn,
            namespace=shim.namespace,
        )
        plies = int(r.get("plies") or 0)
        total_plies += plies
        total_learn_items += int(r.get("learn_items_count") or 0)
        if learn:
            for it in (r.get("learn_items") or []):
                if isinstance(it, dict) and it.get("state_hash"):
                    learn_buffer.append(it)
        max_plies_seen = max(max_plies_seen, plies)
        if plies >= int(max_plies) and str(r.get("outcome") or "") == "D":
            draws_by_cap += 1
        for ch in (r.get("chains") or []):
            if isinstance(ch, dict) and (ch.get("steps") or []):
                chunk_chains.append(ch)
        oc = r.get("outcome")
        if oc == "X":
            wins_x += 1
        elif oc == "O":
            wins_o += 1
        else:
            draws += 1

        if len(chunk_chains) >= max(1, flush_every_games * 2):
            _flush_chunk()
        if learn and len(learn_buffer) >= learn_flush_items:
            _flush_learn()

        if progress_enabled and (idx % progress_every_games == 0 or idx == games):
            print(json.dumps({
                "phase": "batch_progress",
                "game": "chess",
                "mode": mode,
                "games_done": int(idx),
                "games_total": int(games),
                "wins_x": int(wins_x),
                "wins_o": int(wins_o),
                "draws": int(draws),
                "draws_by_cap": int(draws_by_cap),
                "avg_moves_so_far": float(total_plies / float(max(1, idx))),
                "max_plies_seen": int(max_plies_seen),
                "learn_items_so_far": int(total_learn_items),
                "learn_items_buffered": int(len(learn_buffer)),
                "learn_items_flushed_total": int(total_learn_flushed),
                "learn_flushes": int(total_learn_flushes),
                "chains_buffered": int(len(chunk_chains)),
                "chains_written_total": int(total_chains_written),
            }, ensure_ascii=False), flush=True)

    if learn:
        _flush_learn(force=True)
    _flush_chunk(force=True)

    t1 = time.time()
    duration_ms = int((t1 - t0) * 1000.0)
    avg_game_ms = (duration_ms / float(games)) if games else 0.0
    avg_moves = (total_plies / float(games)) if games else 0.0

    return {
        "ts_start": int(t0),
        "ts_end": int(t1),
        "duration_ms": duration_ms,
        "games": int(games),
        "wins_x": int(wins_x),
        "wins_o": int(wins_o),
        "draws": int(draws),
        "draws_by_cap": int(draws_by_cap),
        "avg_moves": float(avg_moves),
        "avg_game_ms": float(avg_game_ms),
        "max_plies_seen": int(max_plies_seen),
        "mode": mode,
        "namespace": shim.namespace,
        "policy_enabled": 1.0 if shim.pol else 0.0,
        "eps": float(max(eps_white, eps_black) if mode == "explore" else 0.0),
        "eps_white": float(eps_white if mode == "explore" else 0.0),
        "eps_black": float(eps_black if mode == "explore" else 0.0),
        "explore_moves_per_game": int(max(explore_moves_white, explore_moves_black) if mode == "explore" else 0),
        "explore_moves_white": int(explore_moves_white if mode == "explore" else 0),
        "explore_moves_black": int(explore_moves_black if mode == "explore" else 0),
        "learn": bool(learn),
        "source": source,
        "label": label,
        "runner": "tools/chess_daily_runner.py",
        "shim": "tools/chess_daily_runner.PolicyShim",
        "learn_items_count": int(total_learn_items),
        "chains": [],
        "chains_count": int(total_chains_written),
        "reward_mode": "full_trace_repeated_terminal",
    }


def _write_snapchains(payload: Dict[str, Any]) -> int:
    """
    Persistiert trainierbare Chess-SnapChains direkt in der DB.

    Jede Partie liefert bis zu zwei Chains:
      • White-Trace
      • Black-Trace

    Diese Chains bilden die spätere DB-Basis für policy_engine / Replay und
    schließen damit dieselbe Struktur-Lücke, die zuvor bei Snake und Pong
    sichtbar war.
    """
    inserted = 0
    ts_now = int(payload.get("ts_end", time.time()) or time.time())
    namespace = str(payload.get("namespace") or "game:chess")
    mode = str(payload.get("mode") or "chess")
    chains = payload.get("chains") or []
    if not isinstance(chains, list):
        return 0

    for idx, chain in enumerate(chains, start=1):
        if not isinstance(chain, dict):
            continue
        steps = chain.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
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
                "notes": f"chess_daily:{mode}:side={chain.get('side','?')}:steps={max(0, len(steps)-1)}",
                "namespace": namespace,
                "source_id": None,
                "version": "chess_daily_runner:v3.7.3-dbchain",
                "weight": 1.0,
            })
            if chain_id:
                inserted += 1
        except Exception as e:
            print(f"[chess_daily_runner] snapchain write failed #{idx}: {e!r}", file=sys.stderr)
    return int(inserted)


def _db_write_episode(batch: Dict[str, Any], kind: str) -> bool:
    try:
        ts_start = int(batch.get("ts_start") or time.time())
        ts_end = int(batch.get("ts_end") or ts_start)
        label = str(batch.get("label") or kind)
        meta = {
            "namespace": batch.get("namespace"),
            "mode": batch.get("mode"),
            "runner": batch.get("runner"),
            "shim": batch.get("shim"),
            "eps": batch.get("eps"),
            "eps_white": batch.get("eps_white"),
            "eps_black": batch.get("eps_black"),
            "explore_moves_per_game": batch.get("explore_moves_per_game"),
            "explore_moves_white": batch.get("explore_moves_white"),
            "explore_moves_black": batch.get("explore_moves_black"),
            "reward_mode": batch.get("reward_mode"),
            "chains_count": batch.get("chains_count"),
            "learn_items_count": batch.get("learn_items_count"),
        }
        eid = sql_manager.insert_episode(
            ts_start=ts_start,
            ts_end=ts_end,
            kind=kind,
            source=str(batch.get("source") or "orchestrator"),
            label=label,
            meta=meta,
        )
        if not eid:
            return False

        def m(key: str, val: Any) -> None:
            try:
                sql_manager.insert_episodic_metric(int(eid), str(key), float(val), ts=int(ts_end))
            except Exception:
                try:
                    sql_manager.insert_episodic_metric(int(eid), str(key), float(val))
                except Exception:
                    pass

        m("games", batch.get("games") or 0)
        m("wins_x", batch.get("wins_x") or 0)
        m("wins_o", batch.get("wins_o") or 0)
        m("draws", batch.get("draws") or 0)
        m("duration_ms", batch.get("duration_ms") or 0)
        m("avg_game_ms", batch.get("avg_game_ms") or 0)
        m("avg_moves", batch.get("avg_moves") or 0)
        m("eps", batch.get("eps") or 0)
        m("eps_white", batch.get("eps_white") or 0)
        m("eps_black", batch.get("eps_black") or 0)
        m("explore_moves_per_game", batch.get("explore_moves_per_game") or 0)
        m("explore_moves_white", batch.get("explore_moves_white") or 0)
        m("explore_moves_black", batch.get("explore_moves_black") or 0)
        m("policy_enabled", batch.get("policy_enabled") or 0)
        m("chains_count", batch.get("chains_count") or 0)
        m("learn_items_count", batch.get("learn_items_count") or 0)
        m("draws_by_cap", batch.get("draws_by_cap") or 0)
        m("max_plies_seen", batch.get("max_plies_seen") or 0)
        return True
    except Exception as e:
        print(f"[chess_daily_runner] DB write failed: {e!r}", file=sys.stderr)
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_CHESS_DAILY_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_CHESS_DAILY_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_CHESS_EPS", 0.08))
    ap.add_argument("--eps-white", type=float, default=_env_float("OROMA_CHESS_EPS_WHITE", _env_float("OROMA_CHESS_EPS", 0.08)))
    ap.add_argument("--eps-black", type=float, default=_env_float("OROMA_CHESS_EPS_BLACK", _env_float("OROMA_CHESS_EPS", 0.08)))
    ap.add_argument("--explore-moves-per-game", type=int, default=_env_int("OROMA_CHESS_EXPLORE_MOVES_PER_GAME", 2))
    ap.add_argument("--explore-moves-white", type=int, default=_env_int("OROMA_CHESS_EXPLORE_MOVES_WHITE", _env_int("OROMA_CHESS_EXPLORE_MOVES_PER_GAME", 2)))
    ap.add_argument("--explore-moves-black", type=int, default=_env_int("OROMA_CHESS_EXPLORE_MOVES_BLACK", max(_env_int("OROMA_CHESS_EXPLORE_MOVES_PER_GAME", 2), _env_int("OROMA_CHESS_EXPLORE_MOVES_PER_GAME", 2) + _env_int("OROMA_CHESS_EXPLORE_MOVES_BLACK_EXTRA", 1))))
    ap.add_argument("--max-plies", type=int, default=_env_int("OROMA_CHESS_MAX_PLIES", 180))
    ap.add_argument("--namespace", type=str, default=_env_str("OROMA_CHESS_POLICY_NAMESPACE", "game:chess"))
    args = ap.parse_args(argv)

    seed = int(args.seed) if int(args.seed) != 0 else int(time.time())
    rng = random.Random(seed)

    shim = PolicyShim(namespace=str(args.namespace or "game:chess"))

    policy_res = run_batch(
        rng=rng,
        shim=shim,
        games=int(args.policy_games),
        mode="policy",
        eps_white=0.0,
        eps_black=0.0,
        explore_moves_white=0,
        explore_moves_black=0,
        max_plies=int(args.max_plies),
        learn=False,
        label=f"chess:policy ({int(args.policy_games)} games)",
        source="orchestrator",
    )

    explore_res = run_batch(
        rng=rng,
        shim=shim,
        games=int(args.explore_games),
        mode="explore",
        eps_white=float(args.eps_white),
        eps_black=float(args.eps_black),
        explore_moves_white=int(args.explore_moves_white),
        explore_moves_black=int(args.explore_moves_black),
        max_plies=int(args.max_plies),
        learn=True,
        label=f"chess:explore ({int(args.explore_games)} games)",
        source="orchestrator",
    )

    ok_policy = _db_write_episode(policy_res, kind="game:chess:policy_batch")
    ok_expl = _db_write_episode(explore_res, kind="game:chess:explore_batch")
    sc1 = int(policy_res.get("chains_count", 0) or 0)
    sc2 = int(explore_res.get("chains_count", 0) or 0)

    out = {
        "ok": bool(ok_policy and ok_expl),
        "have_up": bool(shim.pol is not None),
        "db_written": bool(ok_policy and ok_expl),
        "snapchains_written": int(sc1 + sc2),
        "policy_games": int(policy_res.get("games", 0) or 0),
        "explore_games": int(explore_res.get("games", 0) or 0),
        "policy_avg_moves": float(policy_res.get("avg_moves", 0.0) or 0.0),
        "explore_avg_moves": float(explore_res.get("avg_moves", 0.0) or 0.0),
        "policy_draws_by_cap": int(policy_res.get("draws_by_cap", 0) or 0),
        "explore_draws_by_cap": int(explore_res.get("draws_by_cap", 0) or 0),
        "policy_max_plies_seen": int(policy_res.get("max_plies_seen", 0) or 0),
        "explore_max_plies_seen": int(explore_res.get("max_plies_seen", 0) or 0),
        "explore_learn_items": int(explore_res.get("learn_items_count", 0) or 0),
        "explore_eps_white": float(explore_res.get("eps_white", 0.0) or 0.0),
        "explore_eps_black": float(explore_res.get("eps_black", 0.0) or 0.0),
        "explore_moves_white": int(explore_res.get("explore_moves_white", 0) or 0),
        "explore_moves_black": int(explore_res.get("explore_moves_black", 0) or 0),
    }

    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

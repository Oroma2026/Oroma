#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/memory_ui.py
# Projekt: ORÓMA v3.7.x – Mini-Programme / Games
# Modul:   Memory (Classic Pairs) – Standard-UI + Headless-Step API
# Version: v1.0
# Stand:   2026-02-24
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
# Dieses Modul stellt die Standard-Integration des Spiels "Memory" bereit –
# nach dem gleichen Pattern wie TicTacToe/Connect4/Tetris:
#
#   • Headless-safe: keine pygame/Qt/X11 Abhängigkeiten
#   • UI ohne Server-Thread: Autoplay wird client-seitig getaktet
#   • Server-API ist deterministisch und pro Call "ein Zug":
#       - /memory/api/step  -> 1 Turn (2 Picks) des aktuellen Spielers
#       - /memory/api/state -> State Snapshot
#       - /memory/api/reset -> Reset / neues Spiel
#       - /memory/api/apply -> Mode/Epsilon/Seed
#
# Modus-Split (Standard)
# ----------------------
#   • oroma_vs_oroma_explore : beide Seiten spielen mit ε-Randomisierung
#   • oroma_vs_oroma_policy  : beide Seiten deterministisch (ε=0)
#
# Wichtig: Animation / Sichtbarkeit
# -------------------------------
# In Memory muss der Nutzer "sehen", welche Karten versucht werden.
# Daher liefert /api/step zusätzlich "reveal" (Indices+Symbole) und
# "hide_after_ms" für Mismatches. Die UI zeigt die 2 Karten kurz an und
# lädt danach automatisch den State neu.
#
# Ende / Anti-Endlosschleife
# -------------------------
# Das Spiel endet, wenn alle Paare gefunden sind oder eine max_turns Grenze
# erreicht ist (Default 220). Bei Turn-Limit wird ein Draw gesetzt.
#
# DB / Lernen
# -----------
# Dieses UI-Modul schreibt *nicht* in die DB. Lernen/Telemetry passiert
# ausschließlich über tools/memory_daily_runner.py (Episoden + Policy-Rules).
# Damit bleibt die UI stabil und DB-lock-frei.
#
# ENV
# ---
#   OROMA_MEMORY_SIZE            (default 4)
#   OROMA_MEMORY_MAX_TURNS       (default 220)
#   OROMA_MEMORY_EPS_DEFAULT     (default 0.08)
#
# =============================================================================

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, render_template, request, current_app

try:
    from core.universal_policy import get_policy_rules_count  # type: ignore
except Exception:
    get_policy_rules_count = None  # type: ignore


memory_bp = Blueprint("memory_ui", __name__, url_prefix="/memory", template_folder="templates")


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


@dataclass
class _Cfg:
    mode: str = "oroma_vs_oroma_explore"
    eps: float = 0.08
    seed: Optional[int] = None
    size: int = 4
    max_turns: int = 220


class MemoryGame:
    """Deterministischer Memory-Game-State (ohne Sleep/Threads)."""

    def __init__(self, cfg: _Cfg):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed if cfg.seed is not None else int(time.time()))
        self._init_game()

    def _init_game(self) -> None:
        n = self.cfg.size * self.cfg.size
        pairs = n // 2
        symbols = [chr(ord("A") + i) for i in range(pairs)] * 2
        self.rng.shuffle(symbols)
        self.symbols: List[str] = symbols
        self.revealed: List[bool] = [False] * n
        self.turns: int = 0
        self.turn_owner: str = "p1"  # p1 / p2
        self.score_p1: int = 0
        self.score_p2: int = 0
        self.winner: str = "-"  # p1/p2/draw/-
        self.done: bool = False
        # Per-Spieler Memory (fair, ohne "Peek")
        # -------------------------------------
        # Wichtig: Memory darf NICHT in verdeckte Karten reinschauen.
        # Daher speichern wir nur Informationen, die real beobachtet wurden:
        #   • symbol -> Liste der gesehenen Indizes
        #
        # Die Listen können veraltete Einträge enthalten (z. B. Karten wurden später
        # als Match aufgedeckt). Beim Picken filtern wir daher immer gegen `legal`.
        self.know: Dict[str, Dict[str, List[int]]] = {"p1": {}, "p2": {}}
        # Reveal/animation support
        self.face_up: Dict[int, str] = {}  # indices visible for UI
        self.last_reveal: List[Dict[str, Any]] = []
        self.hide_after_ms: int = 0
        self.last_action: str = "reset"

    def reset(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            self.cfg.seed = int(seed)
            self.rng = random.Random(self.cfg.seed)
        self._init_game()

    def _legal(self) -> List[int]:
        return [i for i, r in enumerate(self.revealed) if not r]

    def _pick_logic(self, who: str, eps: float) -> Tuple[int, int, bool]:
        """Returns (i1, i2, match).

        Fairness-Regel (Produktiv, wichtig)
        ----------------------------------
        Diese Funktion darf NICHT über alle verdeckten Karten iterieren und
        `self.symbols[idx]` lesen, um gezielt Paare zu finden.

        Erlaubt ist:
          - zufälliges Explorieren (ε)
          - Nutzung der eigenen Erinnerung (`self.know[who]`)
          - Nutzung des Symbols der *tatsächlich aufgedeckten* Karte i1, um i2
            ggf. aus der Erinnerung zu wählen.
        """
        legal = self._legal()
        if len(legal) < 2:
            return (legal[0] if legal else 0, legal[0] if legal else 0, False)

        explore = bool(eps > 0.0 and self.rng.random() < eps)
        mem = self.know.get(who, {})

        # 1) Sicheres Paar aus Memory? (>=2 legale Indizes)
        if not explore:
            for _sym, idxs in mem.items():
                # WICHTIG: idxs kann Duplikate enthalten (wenn dieselbe Karte mehrfach
                # beobachtet wurde). Ein "Paar" muss aus zwei VERSCHIEDENEN Indizes
                # bestehen, sonst würden wir (i,i) als Match zählen.
                cand: List[int] = []
                seen: set[int] = set()
                for i in idxs:
                    if i in legal and i not in seen:
                        cand.append(i)
                        seen.add(i)
                if len(cand) >= 2:
                    i1, i2 = cand[0], cand[1]
                    return (i1, i2, True)

        # 2) Erster Pick: wenn möglich eine bekannte "Single"-Karte, sonst random.
        if not explore:
            singles: List[int] = []
            for _sym, idxs in mem.items():
                for i in idxs:
                    if i in legal:
                        singles.append(i)
                        break
            i1 = self.rng.choice(singles) if singles else self.rng.choice(legal)
        else:
            i1 = self.rng.choice(legal)

        # 3) Zweiter Pick: wenn wir das Matching zu i1 in Memory haben, nimm es.
        s1 = self.symbols[i1]
        legal2 = [i for i in legal if i != i1]
        if not legal2:
            return (i1, i1, False)

        i2: Optional[int] = None
        if not explore:
            cand = [i for i in mem.get(s1, []) if i in legal2]
            if cand:
                i2 = cand[0]
        if i2 is None:
            i2 = self.rng.choice(legal2)

        # Safety: Zwei verschiedene Karten pro Turn.
        if i1 == i2 and legal2:
            i2 = self.rng.choice(legal2)

        return (i1, i2, self.symbols[i2] == s1)

    def step_turn(self) -> Dict[str, Any]:
        if self.done:
            return {"ok": True, "done": True, "state": self.state()}

        if self.turns >= int(self.cfg.max_turns):
            self.done = True
            self.winner = "draw"
            self.last_action = "turn_limit"
            return {"ok": True, "done": True, "state": self.state()}

        who = self.turn_owner
        eps = float(self.cfg.eps if self.cfg.mode == "oroma_vs_oroma_explore" else 0.0)
        i1, i2, match = self._pick_logic(who, eps)

        # reveal for UI
        self.face_up = {i1: self.symbols[i1], i2: self.symbols[i2]}
        self.last_reveal = [{"i": i1, "s": self.symbols[i1]}, {"i": i2, "s": self.symbols[i2]}]
        self.hide_after_ms = 0

        self.revealed[i1] = True
        self.revealed[i2] = True
        self.turns += 1
        self.last_action = "match" if match else "mismatch"

        if match:
            if who == "p1":
                self.score_p1 += 1
            else:
                self.score_p2 += 1
            # same player continues
        else:
            # remember both cards (beide Spieler sehen die zwei Reveals)
            # Hinweis: wir speichern *Indices* (nicht "Symbol -> last index"), damit
            # sichere Paare später gefunden werden können.
            s1 = self.symbols[i1]
            s2 = self.symbols[i2]
            for p in ("p1", "p2"):
                mem = self.know.setdefault(p, {})
                mem.setdefault(s1, [])
                mem.setdefault(s2, [])
                if i1 not in mem[s1]:
                    mem[s1].append(i1)
                if i2 not in mem[s2]:
                    mem[s2].append(i2)
            # mismatch: hide again after short time (UI will reload)
            self.hide_after_ms = 550
            # immediately mark as hidden in logic (so no endless reveal)
            self.revealed[i1] = False
            self.revealed[i2] = False
            # switch player
            self.turn_owner = "p2" if who == "p1" else "p1"

        # finish?
        if all(self.revealed):
            self.done = True
            if self.score_p1 > self.score_p2:
                self.winner = "p1"
            elif self.score_p2 > self.score_p1:
                self.winner = "p2"
            else:
                self.winner = "draw"

        return {
            "ok": True,
            "done": self.done,
            "reveal": self.last_reveal,
            "hide_after_ms": self.hide_after_ms,
            "state": self.state(),
        }

    def state(self) -> Dict[str, Any]:
        pairs_left = (self.cfg.size * self.cfg.size) // 2 - (self.score_p1 + self.score_p2)
        # Safety belt: negative Werte sind fachlich unmöglich.
        if pairs_left < 0:
            pairs_left = 0
        board = []
        for i, sym in enumerate(self.symbols):
            if self.revealed[i] or i in self.face_up:
                board.append(sym)
            else:
                board.append("?")
        return {
            "ok": True,
            "cfg": {"mode": self.cfg.mode, "eps": self.cfg.eps, "seed": self.cfg.seed, "size": self.cfg.size, "max_turns": self.cfg.max_turns},
            "turn": self.turn_owner,
            "turns": self.turns,
            "winner": self.winner,
            "done": self.done,
            "p1": {"score": self.score_p1},
            "p2": {"score": self.score_p2},
            "pairs_left": pairs_left,
            "board": board,
            "last_action": self.last_action,
        }


def _rt() -> MemoryGame:
    if "_memory_rt" not in current_app.config:
        cfg = _Cfg(
            size=_env_int("OROMA_MEMORY_SIZE", 4),
            max_turns=_env_int("OROMA_MEMORY_MAX_TURNS", 220),
            eps=_env_float("OROMA_MEMORY_EPS_DEFAULT", 0.08),
        )
        current_app.config["_memory_rt"] = MemoryGame(cfg)
    return current_app.config["_memory_rt"]


@memory_bp.route("/")
def page() -> Any:
    return render_template("memory.html")


@memory_bp.route("/api/state")
def api_state() -> Any:
    rt = _rt()
    st = rt.state()
    # policy count (best-effort)
    if get_policy_rules_count:
        try:
            st["policy_rules"] = int(get_policy_rules_count("game:memory") or 0)
        except Exception:
            st["policy_rules"] = 0
    else:
        st["policy_rules"] = 0
    return jsonify(st)


@memory_bp.route("/api/reset", methods=["POST"])
def api_reset() -> Any:
    rt = _rt()
    rt.reset(seed=request.args.get("seed") or None)
    return jsonify({"ok": True})


@memory_bp.route("/api/apply", methods=["POST"])
def api_apply() -> Any:
    rt = _rt()
    data = request.get_json(force=True) or {}
    mode = str(data.get("mode") or rt.cfg.mode)
    eps = float(data.get("eps") if data.get("eps") is not None else rt.cfg.eps)
    seed = data.get("seed")
    if mode not in ("oroma_vs_oroma_explore", "oroma_vs_oroma_policy"):
        mode = "oroma_vs_oroma_explore"
    rt.cfg.mode = mode
    rt.cfg.eps = max(0.0, min(1.0, eps))
    if seed not in (None, ""):
        try:
            rt.cfg.seed = int(seed)
        except Exception:
            rt.cfg.seed = None
    return jsonify({"ok": True, "cfg": rt.cfg.__dict__})


@memory_bp.route("/api/step", methods=["POST"])
def api_step() -> Any:
    rt = _rt()
    out = rt.step_turn()
    return jsonify(out)

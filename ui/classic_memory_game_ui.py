#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/classic_memory_game_ui.py
# Projekt: ORÓMA
# Modul:   Classic Memory (Pairs) – Flask UI + API
# Version: v3.7 (final)
# Stand:   2025-10-01
#
# Zweck / Rolle
# ─────────────
#  Dieses Modul stellt das klassische Memory-/Pairs-Spiel als Flask-Blueprint
#  bereit – inklusive einfacher Lern-Hooks (Rewards & SnapChain-Logging).
#
#  • UI-Seite:          GET  /memory                  → classic_memory_game.html
#  • Spielstate:        GET  /memory/api/state        → JSON
#  • Reset Board:       POST /memory/api/reset        → JSON {ok:true}
#  • Karte aufdecken:   POST /memory/api/pick         → JSON (neuer State)
#
# Wichtig (Kompatibilität)
# ────────────────────────
#  • Der **URL-Pfad bleibt /memory** (Backward-Compatibility mit Navbar/Links),
#    aber **Datei & Blueprint** wurden in "classic_memory_game" umbenannt.
#  • Blueprint-Variablenname:  classic_memory_bp
#
# Integration (Games-Registry)
# ────────────────────────────
#  Die Registrierung erfolgt **zentral** in `ui/games_ui.py`.
#  Bitte dort die Import-/Registrierzeilen anpassen:
#
#    from ui.classic_memory_game_ui import classic_memory_bp
#    safe_register(app, classic_memory_bp, "classic-memory")
#
#  Aus `run_oroma.py` sollte KEIN direkter Import/Blueprint-Register für
#  einzelne Spiele erfolgen; dafür ist `games_ui.register_games(app)` zuständig.
#
# Lern-Hooks (optional)
# ─────────────────────
#  • reward.log_event("memory", value)   – kleiner Reward-Impuls pro Zug
#  • SnapChain-Logging (Snap/SnapChain/save_chain), falls Core verfügbar
#  → Beide Hooks sind robust per try/except (keine harten Abhängigkeiten).
#
# Konfig / ENV
# ────────────
#  • OROMA_MEMORY_SIZE  – Seitenlänge des Spielfelds (Default: 4 → 4x4=16 Karten)
#                         Muss gerade Fläche ergeben (size*size ist gerade).
#
# Abhängigkeiten
# ──────────────
#  • Flask (Blueprint, render_template, request, jsonify)
#  • (optional) ORÓMA-Core: reward, Snap, SnapChain, save_chain
#
# Lizenz
# ──────
#  MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations
import os
import random
import time
import logging
from flask import Blueprint, jsonify, render_template, request, current_app

# ───────────── Logger ─────────────
logger = logging.getLogger("oroma.classic_memory")
if not logger.handlers:
    _h = logging.StreamHandler()
    _f = logging.Formatter("[classic_memory] %(levelname)s: %(message)s")
    _h.setFormatter(_f)
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# ───────────── optionale ORÓMA-Core-Module ─────────────
try:
    from core import reward
except Exception:
    reward = None  # type: ignore

try:
    from core.snap import Snap
    from core.snapchain import SnapChain, save_chain
except Exception:
    Snap = None       # type: ignore
    SnapChain = None  # type: ignore
    save_chain = None # type: ignore

# ───────────── Blueprint ─────────────
# URL-Pfad bewusst **/memory** für Backwards-Compat mit base.html & alten Links
classic_memory_bp = Blueprint(
    "classic_memory_game",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/memory",
)

# ───────────── Spiel-Laufzeit ─────────────
def _pair_symbols(n_pairs: int) -> list[str]:
    """
    Liefert n_pairs unterschiedliche Symbole (jede wird später verdoppelt).
    Priorität: A..Z → 0..9 → a..z → Unicode-Backup.
    """
    base = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz")
    if n_pairs <= len(base):
        return base[:n_pairs]
    # Fallback: generische Symbole ergänzen
    extra = [f"·{i}" for i in range(n_pairs - len(base))]
    return base + extra

class MemoryRuntime:
    """
    Zustandsmaschine für das Memory-Spiel.
    - size: Seitenlänge (z. B. 4 → 16 Karten)
    - board: flache Liste der Symbole (verdeckt/aufgedeckt via revealed[]).
    """
    def __init__(self, size: int = 4):
        self.size = max(2, int(size))
        if (self.size * self.size) % 2 == 1:
            # Fläche muss gerade sein → auf nächste Größe erhöhen
            self.size += 1
        self.reset()

    def reset(self) -> None:
        total = self.size * self.size
        n_pairs = total // 2
        syms = _pair_symbols(n_pairs)
        deck = syms * 2
        random.shuffle(deck)
        self.board: list[str] = deck
        self.revealed: list[bool] = [False] * total
        self.turns: int = 0
        self.last_pick: int | None = None
        self.found_pairs: int = 0
        logger.info(f"Reset Board: size={self.size} ({total} Karten, {n_pairs} Paare)")

    def _log_reward(self, value: float, pair=None) -> None:
        """Optional: Reward- & SnapChain-Logging."""
        ts = int(time.time())

        if reward:
            try:
                # Klein halten – pro (Miss/Hit) ein Impuls
                reward.log_event("memory", float(value))  # type: ignore[attr-defined]
            except Exception as e:
                logger.debug(f"Reward-Log Fehler: {e}")

        if Snap and SnapChain and save_chain:
            try:
                snap = Snap(
                    features=[float(value)],
                    metadata={"game": "classic_memory", "value": float(value), "pair": pair, "ts": ts},
                )
                chain = SnapChain([snap], metadata={"source": "classic_memory"})
                save_chain(ts, chain)  # type: ignore
            except Exception as e:
                logger.debug(f"SnapChain-Log Fehler: {e}")

    def pick(self, idx: int) -> dict:
        """Deckt eine Karte auf und wertet ggf. ein Pärchen."""
        total = len(self.board)
        if idx < 0 or idx >= total:
            return {"ok": False, "error": "invalid index"}
        if self.revealed[idx]:
            return {"ok": False, "error": "already revealed"}

        # 1. Karte
        if self.last_pick is None:
            self.last_pick = idx
            self.revealed[idx] = True
            return {"ok": True, "board": self.get_state()}

        # 2. Karte
        first = self.last_pick
        self.last_pick = None
        self.turns += 1
        self.revealed[idx] = True

        if self.board[first] == self.board[idx]:
            self.found_pairs += 1
            self._log_reward(+1.0, pair=(self.board[first],))
        else:
            # kurz aufgedeckt lassen? – hier direkt wieder verdecken, simpel & deterministisch
            self.revealed[first] = False
            self.revealed[idx] = False
            self._log_reward(-0.2)

        return {"ok": True, "board": self.get_state()}

    def get_state(self) -> dict:
        return {
            "size": self.size,
            "turns": self.turns,
            "found_pairs": self.found_pairs,
            "board": [s if r else "?" for s, r in zip(self.board, self.revealed)],
            "done": int(self.found_pairs * 2 == len(self.board)),
        }

# ───────────── Singleton-Helper ─────────────
def _rt() -> MemoryRuntime:
    key = "_classic_memory_runtime"
    if key not in current_app.config:
        # Größe aus ENV übernehmen; fällt auf 4 zurück
        size = int(os.environ.get("OROMA_MEMORY_SIZE", "4") or "4")
        current_app.config[key] = MemoryRuntime(size=size)
    return current_app.config[key]

# ───────────── Routes ─────────────

@classic_memory_bp.route("/", methods=["GET"])
def page():
    """UI-Seite – rendert das Template classic_memory_game.html"""
    return render_template("classic_memory_game.html")

@classic_memory_bp.route("/api/state", methods=["GET"])
def api_state():
    return jsonify(_rt().get_state())

@classic_memory_bp.route("/api/reset", methods=["POST"])
def api_reset():
    _rt().reset()
    return jsonify({"ok": True})

@classic_memory_bp.route("/api/pick", methods=["POST"])
def api_pick():
    try:
        data = request.get_json(force=True) or {}
        idx = int(data.get("idx", -1))
    except Exception:
        return jsonify({"ok": False, "error": "invalid request"}), 400
    return jsonify(_rt().pick(idx))
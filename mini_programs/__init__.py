#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/__init__.py
# Projekt: ORÓMA
# Version: v1.1 (quiet-mode support)
# Stand:   2025-12-25
#
# Zweck
# ─────
#   Zentrale Registry und Laufzeitverwaltung für ORÓMA-Mini-Programme (Spiele).
#   - Auto-Discovery aller Module/Packages unter mini_programs/
#   - Einheitliche API für Start/Stop/Move/Tick/AI-Move
#   - Optionale Hintergrund-Tick-Threads für "realtime"-Spiele
#   - SnapChain-Protokollierung (game events) mit Feature-Hash
#
# Wichtige Eigenschaften
# ──────────────────────
#   • Thread-sicher: interne Registry-Operationen sind über ein RLock geschützt.
#   • Fehlertolerant: SnapChain/SQL optional; bei fehlendem core.* wird sauber
#     weitergearbeitet (Debug-Log „SnapChain disabled“).
#   • Adapter-Pattern: ModuleAdapter erlaubt „Modul-Form“-Spiele (Funktionen
#     start/stop/get_state/make_move/tick/ai_move) ohne Klassen-Objekt.
#   • Realtime-Unterstützung: Für Spiele mit GAME_TYPE="realtime" kann automatisch
#     ein Tick-Thread mit konfigurierbarem Intervall (default ≈ 60 FPS) gestartet werden.
#
# Integration / Aufrufmuster
# ──────────────────────────
#   from mini_programs import list_games, start_game, make_move, ai_move, tick, stop_game
#   games = list_games()
#   start_game("chess", tick_interval=0.05)       # optionaler Tick-Thread, falls realtime
#   make_move("chess", {"from": "e2", "to": "e4"})
#   ai_move("chess")
#   stop_game("chess")
#
# SnapChain-Logging (Schema-Hinweis)
# ──────────────────────────────────
#   Jeder Spiel-Event (start/move/tick/ai_move/stop) wird als SnapChain-Singleton
#   geloggt (falls core.snapchain/sql_manager verfügbar):
#     - chain.f_game = <game_id>
#     - chain.f_act  = <action>
#     - chain.f_st   = state.get("status")
#     - chain.f_ex   = json.dumps(extra)[:200]
#
# ENV / Konfiguration
# ───────────────────
#   OROMA_LOG_LEVEL=INFO|DEBUG|...
#   (Realtime-Intervall kann pro Start-Aufruf via start_game(..., tick_interval=0.016)
#   gesetzt werden; kein globaler ENV benötigt.)
#
# Sicherheit & Stabilität
# ───────────────────────
#   - Keine Platzhalter, keine TODOs.
#   - Defensive Programmierung: klare Exceptions bei unbekannten/nicht gestarteten
#     Spielen, Schonung der Registry bei Laufzeitfehlern.
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations
import os
import time
import json
import logging
import threading
import pkgutil
import importlib
import traceback
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, Any
from core.log_guard import log_suppressed

# --- Logger -----------------------------------------------------------------
LOGGER = logging.getLogger("mini_programs")

QUIET = str(os.getenv("OROMA_MINIPROGRAMS_QUIET", "1")).lower() in ("1","true","yes","on")
_ATTACH_STDERR = str(os.getenv("OROMA_MINIPROGRAMS_ATTACH_STDERR", "0")).lower() in ("1","true","yes","on")

if _ATTACH_STDERR and not LOGGER.handlers:
    h = logging.StreamHandler()
    fmt = logging.Formatter("[%(levelname)s] mini_programs: %(message)s")
    h.setFormatter(fmt)
    LOGGER.addHandler(h)
level_name = os.environ.get("OROMA_LOG_LEVEL", "INFO").upper()
LOGGER.setLevel(getattr(logging, level_name, logging.INFO))
LOGGER.propagate = True

# --- SnapChain / SQL Integration --------------------------------------------
try:
    from core.snap import Snap
    from core.snapchain import SnapChain
    from core.sql_manager import insert_chain_quick
    _SNAPCHAIN_OK = True
except Exception as e:
    _SNAPCHAIN_OK = False
    LOGGER.debug("SnapChain disabled (core.* Importfehler): %s", e)
    Snap = None            # type: ignore
    SnapChain = None       # type: ignore
    insert_chain_quick = None  # type: ignore

# =============================================================================
# Hilfsfunktion für SnapChain-Logging
# =============================================================================
def _log_chain_event(game_id: str, action: str, state: Optional[dict] = None, extra: Optional[dict] = None) -> None:
    """Loggt ein Spiel-Event als SnapChain in die DB (best effort)."""
    if not _SNAPCHAIN_OK:
        LOGGER.debug("SnapChain log skipped (%s:%s) – disabled", game_id, action)
        return
    try:
        s = Snap(
            kind="game",
            label=f"{game_id}:{action}",
            data={
                "game": game_id,
                "action": action,
                "state": state or {},
                "extra": extra or {},
            },
        )
        chain = SnapChain(snaps=[s])
        # Feature-Hash
        chain.f_game = game_id
        chain.f_act = action
        chain.f_st = (state or {}).get("status")
        chain.f_ex = json.dumps(extra or {}, ensure_ascii=False)[:200]
        insert_chain_quick(chain)
    except Exception as e:
        LOGGER.warning("SnapChain-Log für %s:%s fehlgeschlagen: %s", game_id, action, e)

# =============================================================================
# Typen / Basisklassen
# =============================================================================
@dataclass
class GameMeta:
    game_id: str
    name: str
    kind: str  # "turn" | "realtime"
    module: str
    description: str = ""
    difficulty: Tuple[str, ...] = field(default_factory=tuple)
    ai_available: bool = False

class BaseGame:
    GAME_ID: str = ""
    GAME_NAME: str = ""
    GAME_TYPE: str = "turn"  # "turn" | "realtime"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = False
        self._start_ts = 0.0

    def start(self, **kwargs) -> None:
        with self._lock:
            self._started = True
            self._start_ts = time.time()

    def stop(self) -> None:
        with self._lock:
            self._started = False

    def get_state(self) -> dict: raise NotImplementedError
    def make_move(self, payload: dict) -> dict: raise NotImplementedError
    def tick(self, dt: float) -> dict: raise NotImplementedError
    def ai_move(self, state: dict) -> Optional[dict]: return None
    def difficulty_levels(self) -> Tuple[str, ...]: return tuple()

class ModuleAdapter(BaseGame):
    """Adapter für Spiele, die als Modul-Funktionen implementiert sind."""
    def __init__(self, module) -> None:
        super().__init__()
        self._m = module
        self.GAME_ID = getattr(module, "GAME_ID", module.__name__.split(".")[-1])
        self.GAME_NAME = getattr(module, "GAME_NAME", self.GAME_ID)
        self.GAME_TYPE = getattr(module, "GAME_TYPE", "turn")

    def start(self, **kwargs) -> None:
        super().start(**kwargs)
        if hasattr(self._m, "start"):
            self._m.start(**kwargs)

    def stop(self) -> None:
        try:
            if hasattr(self._m, "stop"):
                self._m.stop()
        finally:
            super().stop()

    def get_state(self) -> dict:
        if hasattr(self._m, "get_state"):
            return self._m.get_state() or {}
        return {}

    def make_move(self, payload: dict) -> dict:
        if hasattr(self._m, "make_move"):
            return self._m.make_move(payload or {}) or {}
        return {}

    def tick(self, dt: float) -> dict:
        if hasattr(self._m, "tick"):
            return self._m.tick(float(dt)) or {}
        return {}

    def ai_move(self, state: dict) -> Optional[dict]:
        if hasattr(self._m, "ai_move"):
            return self._m.ai_move(state or {})
        return None

# =============================================================================
# Registry / Laufzeitstate
# =============================================================================
_REGISTRY_LOCK = threading.RLock()
_REGISTRY: Dict[str, GameMeta] = {}
_INSTANCES: Dict[str, BaseGame] = {}
_THREADS: Dict[str, threading.Thread] = {}
_TICK_EVENTS: Dict[str, threading.Event] = {}

# =============================================================================
# Discovery / Registrierung
# =============================================================================
def _register_module(mod: Any) -> None:
    with _REGISTRY_LOCK:
        gid = getattr(mod, "GAME_ID", mod.__name__.split(".")[-1])
        if not gid or gid in _REGISTRY:
            return
        meta = GameMeta(
            gid,
            getattr(mod, "GAME_NAME", gid),
            getattr(mod, "GAME_TYPE", "turn"),
            mod.__name__,
            getattr(mod, "__doc__", "") or "",
            tuple(getattr(mod, "difficulty_levels", lambda: [])() or []),
            hasattr(mod, "ai_move"),
        )
        _REGISTRY[gid] = meta
        (LOGGER.debug if QUIET else LOGGER.info)("Registriert Spiel: %s (%s)", meta.name, gid)

def _discover_games() -> None:
    base_pkg = __name__
    pkg_path = os.path.dirname(__file__)
    for m in pkgutil.iter_modules([pkg_path]):
        name = m.name
        if name.startswith("_") or name == "__init__":
            continue
        fqmn = f"{base_pkg}.{name}"
        try:
            mod = importlib.import_module(fqmn)
            _register_module(mod)
        except Exception as e:
            (LOGGER.debug if QUIET else LOGGER.warning)("Konnte Spiel %s nicht laden: %s", fqmn, e)

_discover_games()

def _instantiate(meta: GameMeta) -> BaseGame:
    """Erzeugt eine Game-Instanz als ModuleAdapter (einheitliches Interface)."""
    mod = importlib.import_module(meta.module)
    return ModuleAdapter(mod)

# =============================================================================
# Realtime-Tick: Thread-Management
# =============================================================================
def _start_tick_thread(game_id: str, interval: float) -> None:
    """Startet einen Hintergrundthread, der regelmäßig tick(dt=interval) aufruft."""
    stop_ev = threading.Event()
    _TICK_EVENTS[game_id] = stop_ev

    def loop():
        last = time.time()
        LOGGER.info("Tick-Thread gestartet: %s (dt=%.4fs)", game_id, interval)
        while not stop_ev.is_set():
            try:
                now = time.time()
                dt = now - last
                last = now
                with _REGISTRY_LOCK:
                    inst = _INSTANCES.get(game_id)
                    if not inst:
                        break
                    inst.tick(dt if dt > 0 else interval)
                time.sleep(interval)
            except Exception as e:
                LOGGER.warning("Tick-Loop Fehler (%s): %s", game_id, e)
                time.sleep(interval)
        LOGGER.info("Tick-Thread gestoppt: %s", game_id)

    th = threading.Thread(target=loop, name=f"game-tick-{game_id}", daemon=True)
    _THREADS[game_id] = th
    th.start()

def _stop_tick_thread(game_id: str) -> None:
    ev = _TICK_EVENTS.pop(game_id, None)
    th = _THREADS.pop(game_id, None)
    if ev:
        ev.set()
    if th and th.is_alive():
        try:
            th.join(timeout=2.0)
        except Exception as e:
            log_suppressed('mini_programs/__init__.py:297', exc=e, level=logging.WARNING)
            pass

# =============================================================================
# Öffentliche API (thread-sicher) – mit SnapChain-Logging & Fehlerprüfungen
# =============================================================================
def list_games() -> Dict[str, dict]:
    with _REGISTRY_LOCK:
        return {gid: meta.__dict__ for gid, meta in _REGISTRY.items()}

def start_game(game_id: str, **kwargs) -> dict:
    with _REGISTRY_LOCK:
        meta = _REGISTRY.get(game_id)
        if not meta:
            raise KeyError(f"Unbekanntes Spiel: {game_id}")

        # ggf. alte Instanz sauber stoppen
        old = _INSTANCES.pop(game_id, None)
        if old:
            try:
                old.stop()
            finally:
                _stop_tick_thread(game_id)

        inst = _instantiate(meta)
        inst.start(**kwargs)
        _INSTANCES[game_id] = inst

        # Realtime-Thread optional starten (nur bei GAME_TYPE="realtime")
        tick_interval = float(kwargs.get("tick_interval", 0.016))
        if str(meta.kind).lower() == "realtime" and tick_interval > 0:
            _start_tick_thread(game_id, tick_interval)

        state = inst.get_state()
        _log_chain_event(game_id, "start", state=state)
        return {"ok": True, "state": state}

def stop_game(game_id: str) -> dict:
    with _REGISTRY_LOCK:
        inst = _INSTANCES.pop(game_id, None)
        _stop_tick_thread(game_id)
        if inst:
            inst.stop()
        _log_chain_event(game_id, "stop")
        return {"ok": True}

def _require_started(game_id: str) -> BaseGame:
    inst = _INSTANCES.get(game_id)
    if not inst:
        raise RuntimeError(f"Spiel '{game_id}' ist nicht gestartet.")
    return inst

def make_move(game_id: str, payload: dict) -> dict:
    with _REGISTRY_LOCK:
        inst = _require_started(game_id)
        res = inst.make_move(payload or {})
        _log_chain_event(game_id, "move", state=res, extra=payload)
        return {"ok": True, "result": res}

def tick(game_id: str, dt: float = 0.016) -> dict:
    with _REGISTRY_LOCK:
        inst = _require_started(game_id)
        res = inst.tick(float(dt))
        _log_chain_event(game_id, "tick", state=res)
        return {"ok": True, "result": res}

def ai_move(game_id: str) -> dict:
    with _REGISTRY_LOCK:
        inst = _require_started(game_id)
        sug = inst.ai_move(inst.get_state())
        if sug:
            res = inst.make_move(sug)
            _log_chain_event(game_id, "ai_move", state=res, extra=sug)
            return {"ok": True, "result": res, "suggestion": sug}
        raise RuntimeError(f"AI move für '{game_id}' nicht verfügbar")

# =============================================================================
# CLI-Schnelltest (optional)
# =============================================================================
if __name__ == "__main__":
    print("Verfügbare Spiele:")
    for gid, meta in list_games().items():
        print(f"- {gid:12s} | {meta['name']:<20s} | kind={meta['kind']}")
    # Beispiel: falls "tictactoe" vorhanden ist
    if "tictactoe" in _REGISTRY:
        print("\nStarte tictactoe…")
        start_game("tictactoe")
        make_move("tictactoe", {"x": 0, "y": 0})
        stop_game("tictactoe")
        print("OK.")
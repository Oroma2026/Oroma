#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/options.py
# Projekt: ORÓMA
# Version: v1.0 – Hierarchische Optionen (Subgoals) ohne DB-Migration
# Stand:   2025-10-26
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Leichte Options-Schicht (Subziele) für die hierarchische Policy:
#     • Optionen werden im *Regelarchiv* (Tabelle `rules`) als JSON in `content`
#       gespeichert – keine zusätzliche Migration erforderlich.
#     • Jede Option hat enter/act/terminate-Logik.
#     • Ein einfacher Runner aktiviert passende Option und liefert Aktionen.
#
# Datenformat in rules.content
# ────────────────────────────
#   {
#     "type": "option",
#     "namespace": "game:tictactoe",
#     "name": "block_fork",
#     "enter": {"phase": "mid"},
#     "body":  {"action": "block"},
#     "terminate": {"phase": "post"}
#   }
#
# API
# ───
#   class Option:
#       enter(state) -> bool
#       act(state) -> Optional[str]
#       terminate(state) -> bool
#
#   load_options(namespace: str) -> List[Option]
#
#   class OptionRunner:
#       step(state: Dict[str, Any]) -> Optional[str]
#
# Headless
# ────────
#   Keine Qt/Wayland/X11-Abhängigkeiten. Reine Standardbibliothek.
# =============================================================================

from __future__ import annotations

import json
import time
import sqlite3
import os
from typing import Optional, Dict, Any, List

import logging
from core import log_guard
logger = logging.getLogger(__name__)
__all__ = ["Option", "OptionRunner", "load_options"]

# Pfad zur Hauptdatenbank (sql_manager-kompatibel)
DB = os.getenv("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")


class Option:
    """
    Einfache Option mit Eintritts-, Aktions- und Terminierungsbedingungen.
    Die Bedingungen sind als {key: value}-Matcher definiert und werden
    eins-zu-eins gegen den State verglichen.
    """

    def __init__(self, name: str, namespace: str, enter: Dict[str, Any], body: Dict[str, Any], term: Dict[str, Any]):
        self.name = name
        self.namespace = namespace
        self._enter = dict(enter or {})
        self._body = dict(body or {})
        self._term = dict(term or {})

    def enter(self, s: Dict[str, Any]) -> bool:
        """True, wenn alle enter-Bedingungen exakt erfüllt sind."""
        try:
            return all(s.get(k) == v for k, v in self._enter.items())
        except Exception:
            return False

    def act(self, s: Dict[str, Any]) -> Optional[str]:
        """Liefert die Option-Aktion (String) oder None."""
        return self._body.get("action")

    def terminate(self, s: Dict[str, Any]) -> bool:
        """True, wenn alle terminate-Bedingungen erfüllt sind."""
        try:
            return all(s.get(k) == v for k, v in self._term.items())
        except Exception:
            return False


def load_options(namespace: str) -> List[Option]:
    """
    Lädt alle aktiven Optionen eines Namespace aus der Tabelle `rules`.
    Es wird *nur* content geparst, das JSON mit {"type":"option", ...} ist.
    """
    out: List[Option] = []
    if not os.path.exists(DB):
        return out

    con = None
    try:
        # sqlite3.Connection als Context-Manager schließt NICHT automatisch.
        # In Langläufern kann das zu FD-Leaks und späteren Lock-Problemen führen.
        con = sqlite3.connect(DB)
        cur = con.execute("SELECT content FROM rules WHERE active=1")
        rows = cur.fetchall() or []
    except Exception:
        return out
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    for (content,) in rows:
        try:
            d = json.loads(content) if isinstance(content, str) else content
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") != "option":
            continue
        if d.get("namespace", "") != namespace:
            continue
        out.append(
            Option(
                d.get("name", "option"),
                namespace,
                d.get("enter", {}),
                d.get("body", {}),
                d.get("terminate", {}),
            )
        )
    return out


class OptionRunner:
    """
    Minimaler Runner: hält genau *eine* aktive Option.
    Wenn die aktive Option terminieren soll, wird sie verworfen und
    eine neue passende Option gesucht. step(state) gibt dann eine
    (optionale) Aktion zurück.
    """

    def __init__(self, options: List[Option]):
        self.options = list(options or [])
        self.active: Optional[Option] = None
        self.started_ts: int = 0

    def step(self, state: Dict[str, Any]) -> Optional[str]:
        # Aktive Option beenden?
        if self.active and self.active.terminate(state):
            self.active = None

        # Neue Option wählen?
        if self.active is None:
            for o in self.options:
                try:
                    if o.enter(state):
                        self.active = o
                        self.started_ts = int(time.time())
                        break
                except Exception:
                    continue

        # Aktion der aktiven Option liefern
        return self.active.act(state) if self.active else None
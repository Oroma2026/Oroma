#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Datei:    /opt/ai/oroma/core/adapters/base_adapter.py
# Projekt:  🧠 ORÓMA – Adapter-Schnittstellen (AV/Text/Sensor)
# Version:  v3.7
# Stand:    2025-10-18
# Autor:    ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#  Gemeinsames Adapter-Interface für Wahrnehmungs-Streams (Audio/Video/Text/Sensor).
#  Adapter liefern:
#    1) robuste, symbolische Tokens (menschenlesbar)
#    2) einen stabilen State-Vektor fester Länge für Policy/Regeln
#
# API
# ───
#  class AdapterBase:
#     - name: str
#     - state_dim: int
#     - observe(raw) -> Dict:   Rohdaten → Zwischenrepräsentation (Tokens, Messwerte)
#     - encode_state(obs) -> (vec, tokens, readable): Vektor + Tokens + Kurztext
#     - actions() -> List[str]: erlaubte Aktionslabels für diesen Adapter
#
# Hinweise
# ────────
#  • Diese Basis vermeidet harte Abhängigkeiten (ffmpeg/torch etc.).
#  • AV-spezifische Details kommen in av_adapter.py (optionale Backends).
# =============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Tuple

class AdapterBase:
    """Abstraktes Adapter-Interface."""

    name: str = "base"
    state_dim: int = 16  # Default; spezialisierte Adapter setzen passend

    def observe(self, raw: Any) -> Dict[str, Any]:
        """
        Rohdaten → Zwischenrepräsentation.
        Muss robust sein (dürfen Felder fehlen); idealerweise side-effect-frei.
        """
        raise NotImplementedError

    def encode_state(self, obs: Dict[str, Any]) -> Tuple[List[float], List[str], str]:
        """
        Beobachtung → (State-Vektor, Token-Liste, menschenlesbare Kurzbeschreibung).
        - Vektor-Länge MUSS == state_dim sein (pad/trim bei Bedarf).
        - Tokens sind stabile, kurze Symbole (z. B. "person@2m", "speech:hello").
        - readable fasst die Situation für das Regelarchiv zusammen.
        """
        raise NotImplementedError

    def actions(self) -> List[str]:
        """
        Mögliche Aktionen des Agenten für diesen Adapter-Kontext.
        Beispiele (AV): "greet", "track_left", "answer:yes", "silence".
        """
        return []
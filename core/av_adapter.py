#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Datei:    /opt/ai/oroma/core/adapters/av_adapter.py
# Projekt:  🧠 ORÓMA – Audio/Video Adapter (Tokens → State-Vektor)
# Version:  v3.7
# Stand:    2025-10-18
# Autor:    ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#  Bringt Audio/Video auf die gleiche Entscheidungs-Schiene wie Text/Snap:
#    • extrahiert robuste **Tokens** (z. B. "person@2m", "gaze_me", "speech:hello")
#    • bildet einen **State-Vektor** fixer Länge (Default 24)
#    • liefert **lesbaren Zustandstext** fürs Regelarchiv
#
# Eingaben (raw)
# ──────────────
#  Ein flexibles Dict, z. B.:
#    {
#      "video": {
#         "persons": [{"dist": 1.8, "gaze_me": true}, ...],
#         "objects": [{"label":"ball","conf":0.91}, ...]
#      },
#      "audio": {
#         "speech": {"text":"hello", "lang":"en", "conf":0.88},
#         "pitch_trend": "up",        # "up"/"down"/"flat"
#         "energy": 0.42              # 0..1
#      }
#    }
#
# Anmerkung
# ─────────
#  Der Adapter ist **backend-agnostisch**. Ob face/person/speech aus Hailo,
#  OpenVINO, PyTorch, GStreamer kommt, ist egal – Hauptsache, die Felder sind
#  befüllt. So können Edge-NPUs oder CPU-Pipelines *per Wrapper* andocken.
# =============================================================================

from __future__ import annotations
from typing import Any, Dict, List, Tuple
import math
from core.log_guard import log_suppressed
import logging

from core.adapters.base_adapter import AdapterBase

class AVAdapter(AdapterBase):
    name = "av"
    state_dim = 24  # überschaubar, reicht für Demo/Regeln – erweiterbar

    def actions(self) -> List[str]:
        # Beispiel-Action-Space (edge-geeignet, erweiterbar):
        return [
            "idle",
            "greet",
            "answer:yes",
            "answer:no",
            "track_left",
            "track_right",
            "approach",
            "retreat",
            "listen",
            "speak",
        ]

    # ----------------------------- Beobachtung --------------------------------
    def observe(self, raw: Any) -> Dict[str, Any]:
        # defensive Normalisierung
        obs: Dict[str, Any] = {"video": {}, "audio": {}}
        if isinstance(raw, dict):
            if isinstance(raw.get("video"), dict):
                obs["video"] = raw["video"]
            if isinstance(raw.get("audio"), dict):
                obs["audio"] = raw["audio"]
        return obs

    # ----------------------------- State-Encode -------------------------------
    def encode_state(self, obs: Dict[str, Any]) -> Tuple[List[float], List[str], str]:
        v = [0.0] * self.state_dim
        tokens: List[str] = []

        vid = obs.get("video") or {}
        aud = obs.get("audio") or {}

        # --- VIDEO: Personen + Blickrichtung + Distanz -----------------------
        persons = vid.get("persons") or []
        # Leitperson = nächste Person
        lead_dist = 99.0
        gaze_me = False
        if isinstance(persons, list) and persons:
            for p in persons:
                try:
                    d = float(p.get("dist", 99.0))
                    g = bool(p.get("gaze_me", False))
                    if d < lead_dist:
                        lead_dist = d
                        gaze_me = g
                except Exception as e:
                    log_suppressed(
                        logging.getLogger(__name__),
                        key="core.av_adapter.pass.1",
                        exc=e,
                        msg="Suppressed exception (was: pass)",
                    )
        person_present = 1.0 if lead_dist < 5.0 else 0.0  # grobe Präsenz
        v[0] = max(0.0, min(1.0, 1.0 - (lead_dist / 5.0)))  # Nähe (0..1, 1=nah)
        v[1] = 1.0 if gaze_me else 0.0

        if person_present:
            tokens.append(f"person@{round(lead_dist,1)}m")
            if gaze_me:
                tokens.append("gaze_me")

        # --- VIDEO: Objekte (einfach: Top-Objekt, falls vorhanden) -----------
        top_obj = None
        if isinstance(vid.get("objects"), list) and vid["objects"]:
            top_obj = max(vid["objects"], key=lambda o: float(o.get("conf", 0.0)))
        if top_obj:
            lbl = str(top_obj.get("label", "obj"))
            cf  = float(top_obj.get("conf", 0.0))
            v[2] = max(0.0, min(1.0, cf))
            tokens.append(f"object:{lbl}")

        # --- AUDIO: Speech / Pitch / Energy ----------------------------------
        sp = aud.get("speech") or {}
        if isinstance(sp, dict) and sp:
            text = str(sp.get("text", "")).strip().lower()
            conf = float(sp.get("conf", 0.0))
            v[3] = max(0.0, min(1.0, conf))
            if text:
                # Nur sehr kurze, robuste Stichworte als Token
                key = text.split(" ")[0][:12]
                if key:
                    tokens.append(f"speech:{key}")

        pitch = (aud.get("pitch_trend") or "flat").lower()
        v[4] = {"down": 0.25, "flat": 0.5, "up": 0.75}.get(pitch, 0.5)
        if pitch in ("up", "down"):
            tokens.append("pitch↑" if pitch == "up" else "pitch↓")

        energy = float(aud.get("energy", 0.0))
        v[5] = max(0.0, min(1.0, energy))

        # --- Ableitungen / einfache Logik ------------------------------------
        # 'Engagement' – wenn Person nah + Blickkontakt + Sprache/hohe Energie
        engagement = 0.0
        engagement += v[0] * 0.5
        engagement += v[1] * 0.3
        engagement += max(v[3], v[5]) * 0.2
        v[6] = max(0.0, min(1.0, engagement))
        if v[6] > 0.6:
            tokens.append("engaged")

        # Freie Plätze für spätere Features (Kopfpose, Bewegung, Rhythmus, …)
        # v[7..23] bleiben 0.0

        # --- Lesbarer Zustand -------------------------------------------------
        readable_bits = []
        if person_present:
            readable_bits.append(f"person≈{round(lead_dist,1)}m")
        if gaze_me:
            readable_bits.append("gaze_me")
        if top_obj:
            readable_bits.append(f"obj:{top_obj.get('label','?')}")
        if v[3] > 0:
            readable_bits.append("speech")
        if pitch in ("up", "down"):
            readable_bits.append(f"pitch:{pitch}")
        if v[6] > 0.6:
            readable_bits.append("engaged")

        readable = ", ".join(readable_bits) if readable_bits else "idle"

        return (v, tokens, readable)
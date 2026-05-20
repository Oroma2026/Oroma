#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – UI Route Tests
----------------------------
- Startet Flask-App im Test-Mode
- Prüft, ob alle wichtigen Routen (Blueprints/Tabs) erreichbar sind
- Nutzt Flask TestClient, keine echten Modelle oder Wrapper nötig
"""

import sys, os, pytest
from flask import Flask

BASE = "/opt/ai/oroma"
sys.path.insert(0, BASE)

# Haupt-Dashboard importieren
try:
    from ui import flask_ui
except Exception as e:
    raise RuntimeError(f"UI-Import fehlgeschlagen: {e}")

@pytest.fixture(scope="module")
def client():
    """Erzeuge TestClient aus der Flask-App"""
    app = Flask(__name__)
    app.register_blueprint(flask_ui.bp)  # Blueprint einhängen
    app.testing = True
    with app.test_client() as client:
        yield client

# --- Liste der erwarteten Routen -------------------------------
ROUTES = [
    "/",             # Startseite
    "/games",        # Mini-Spiele
    "/chat",         # LLM Chat
    "/ask",          # Faktenbasierte Fragen (RAG)
    "/knowledge",    # Wissensbasis (Dokument-Import)
    "/models",       # Modellverwaltung
    "/learning",     # Lernkurve
    "/episodic",     # Episoden-Browser
    "/why",          # Explainability
    "/synapses",     # Graph-Visualisierung
    "/replay",       # SnapChain Replay
    "/dream",        # Traum-Modus
    "/video",        # Video-Stream/Overlay
    "/asr",          # Live-ASR
    "/control",      # Steuerung (AgentLoop, Phasen)
    "/export",       # Export/Import
    "/health",       # Systemstatus
]

@pytest.mark.parametrize("route", ROUTES)
def test_routes_status_ok(client, route):
    """Prüft, ob die Routen ohne Fehlercode erreichbar sind"""
    resp = client.get(route)
    assert resp.status_code in (200, 302), f"{route} nicht erreichbar"
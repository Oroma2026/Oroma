# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Flappy Bird UI/API Tests
Pfad: /opt/ai/oroma/tests/test_flappy_ui.py

Zweck:
- Integrationstest für Flappy Bird im Dashboard
- Prüft Rendering, API-Endpunkte, Config, Reset, Autopilot
- Läuft innerhalb weniger Sekunden
"""

import json
import time
import pytest


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def app_client():
    """Fixture: Flask-Test-Client"""
    from ui.flask_ui import app
    app.testing = True
    with app.test_client() as c:
        yield c

def _json(resp):
    """Hilfsparser: JSON/Text → Python-Objekt"""
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.data[:200]!r}"
    if resp.mimetype and resp.mimetype.startswith("text/"):
        return resp.get_data(as_text=True)
    data = resp.get_data(as_text=True) or "{}"
    return json.loads(data)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@pytest.mark.games
def test_flappy_page_renders(app_client):
    r = app_client.get("/flappy")
    assert r.status_code == 200
    assert b"Flappy Bird" in r.data


@pytest.mark.games
def test_flappy_state_and_ascii(app_client):
    st = _json(app_client.get("/api/flappy/state"))
    assert isinstance(st, dict)
    for k in ("y", "vy", "dx", "gap_y", "gap_h", "score", "alive", "steps"):
        assert k in st, f"{k} fehlt im State"

    ascii_view = _json(app_client.get("/api/flappy/ascii"))
    assert isinstance(ascii_view, str)
    assert "score=" in ascii_view


@pytest.mark.games
def test_flappy_config_get_set_and_reset(app_client):
    cfg = _json(app_client.get("/api/flappy/config"))
    assert "gravity" in cfg and "dt" in cfg

    # Patch zwei Werte
    patched = _json(app_client.post("/api/flappy/config", json={"gravity": 1.23, "dt": 0.04}))
    assert float(patched["gravity"]) == pytest.approx(1.23, rel=1e-6)
    assert float(patched["dt"]) == pytest.approx(0.04, rel=1e-6)

    # Reset mit Defaults
    st = _json(app_client.post("/api/flappy/reset", json={}))
    assert st["steps"] == 0
    assert st["alive"] is True


@pytest.mark.games
def test_flappy_step_and_autopilot(app_client):
    st1 = _json(app_client.get("/api/flappy/state"))
    steps1 = st1["steps"]

    # Manueller Flap
    res = _json(app_client.post("/api/flappy/step", json={"action": 1}))
    assert "state" in res and "reward" in res
    st2 = res["state"]
    assert st2["steps"] >= steps1

    # Autopilot einschalten
    _json(app_client.post("/api/flappy/autopilot", json={"enabled": True}))
    time.sleep(0.5)  # etwas mehr Zeit geben (Pi5-Load)
    st3 = _json(app_client.get("/api/flappy/state"))
    assert st3["steps"] > st2["steps"], "Autopilot sollte Schritte ausgeführt haben"

    # Autopilot ausschalten
    _json(app_client.post("/api/flappy/autopilot", json={"enabled": False}))
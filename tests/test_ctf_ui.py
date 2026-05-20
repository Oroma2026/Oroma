# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Capture-the-Flag UI/API Tests
Pfad: /opt/ai/oroma/tests/test_ctf_ui.py

Zweck:
- Integrationstest für CTF-Spiel im Dashboard
- Prüft Rendering, API-Endpunkte, Config, Policies, Autopilot
- Läuft innerhalb weniger Sekunden
"""

import time
import json
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

def _j(resp):
    """Hilfsparser: JSON oder Text → Python-Objekt"""
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.data[:200]!r}"
    if resp.mimetype and resp.mimetype.startswith("text/"):
        return resp.get_data(as_text=True)
    return json.loads(resp.get_data(as_text=True) or "{}")


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@pytest.mark.games
def test_ctf_page_renders(app_client):
    r = app_client.get("/ctf")
    assert r.status_code == 200
    assert b"Capture the Flag" in r.data


@pytest.mark.games
def test_ctf_state_and_ascii(app_client):
    s1 = _j(app_client.get("/api/ctf/state"))
    assert {"A_pos", "B_pos", "width", "height"} <= set(s1)

    text = _j(app_client.get("/api/ctf/ascii"))
    assert isinstance(text, str)
    assert "score" in text and "steps" in text


@pytest.mark.games
def test_ctf_config_get_set_and_reset(app_client):
    cfg = _j(app_client.get("/api/ctf/config"))
    assert "width" in cfg and "height" in cfg

    patched = _j(app_client.post("/api/ctf/config", json={"max_steps": 123, "respawn_on_tag": True}))
    assert int(patched["max_steps"]) == 123
    assert bool(patched["respawn_on_tag"]) is True

    st = _j(app_client.post("/api/ctf/reset", json={"seed": 42}))
    assert st["steps"] == 0
    assert st["done"] is False


@pytest.mark.games
def test_ctf_policies_endpoint(app_client):
    data = _j(app_client.get("/api/ctf/policies"))
    assert "policies" in data
    assert {"off", "greedy", "chaser"} <= set(data["policies"])


@pytest.mark.games
def test_ctf_manual_step_cycle(app_client):
    resp = _j(app_client.post("/api/ctf/step", json={"A": 4, "B": 0}))
    assert "state" in resp and "reward" in resp
    st = resp["state"]
    assert isinstance(st["A_pos"], (list, tuple))

    resp2 = _j(app_client.post("/api/ctf/step", json={"A": 0, "B": 0}))
    assert "state" in resp2


@pytest.mark.games
def test_ctf_autopilot_advances_state(app_client):
    _j(app_client.post("/api/ctf/autopilot", json={"A": "greedy", "B": "chaser", "speed": 0.05}))
    steps1 = _j(app_client.get("/api/ctf/state"))["steps"]

    time.sleep(0.5)  # etwas länger warten für Pi5-Load
    steps2 = _j(app_client.get("/api/ctf/state"))["steps"]

    assert steps2 > steps1, f"Autopilot hat nicht weitergeschaltet: {steps1} -> {steps2}"

    _j(app_client.post("/api/ctf/autopilot", json={"A": "off", "B": "off", "speed": 0.15}))
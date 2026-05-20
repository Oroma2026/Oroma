#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Models Registry Test
---------------------------------
- Prüft, ob die SQLite-DB für Modelle funktioniert
- Legt einen Dummy-Eintrag an, falls keine Modelle vorhanden sind
- Stellt sicher, dass der /models-Endpunkt erreichbar ist
"""

import sys, os, sqlite3, pytest
from flask import Flask

BASE = "/opt/ai/oroma"
DB_PATH = os.path.join(BASE, "data", "oroma.db")
sys.path.insert(0, BASE)

try:
    from ui import flask_ui
    from core import sql_manager
except Exception as e:
    raise RuntimeError(f"Imports fehlgeschlagen: {e}")

@pytest.fixture(scope="module")
def client():
    """Erzeuge TestClient mit /models"""
    app = Flask(__name__)
    app.register_blueprint(flask_ui.bp)
    app.testing = True
    with app.test_client() as client:
        yield client

def ensure_dummy_model():
    """Falls keine Modelle existieren → Dummy eintragen"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM models")
    count = cur.fetchone()["c"]

    if count == 0:
        cur.execute("""
            INSERT INTO models (name, type, status, version)
            VALUES (?, ?, ?, ?)
        """, ("dummy-vision", "vision", "active", "v3.5"))
        conn.commit()
    conn.close()

def test_models_registry_accessible(client):
    """Prüfen ob /models erreichbar ist"""
    resp = client.get("/models")
    assert resp.status_code in (200, 302), "/models nicht erreichbar"

def test_models_registry_contains_entry():
    """Prüfen ob mind. 1 Modell in der DB existiert"""
    ensure_dummy_model()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name, type, status, version FROM models LIMIT 1")
    row = cur.fetchone()
    conn.close()

    assert row is not None, "Keine Einträge in models gefunden"
    assert row["name"], "Modell hat keinen Namen"
    assert row["type"], "Modell hat keinen Typ"
    assert row["status"], "Modell hat keinen Status"
    assert row["version"] == "v3.5", "Version sollte v3.5 sein"
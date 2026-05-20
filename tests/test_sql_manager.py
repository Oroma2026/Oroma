#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit-Tests für core/sql_manager.py
Pfad: /opt/ai/oroma/tests/test_sql_manager.py
"""

import os
import sqlite3
import pytest
import tempfile
import shutil

import core.sql_manager as sm

@pytest.fixture(scope="module")
def temp_db(monkeypatch):
    # Temp-Verzeichnis + DB-Pfad erzeugen
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "oroma.db")
    monkeypatch.setenv("OROMA_BASE", tmpdir)

    # Schema sicherstellen
    sm.ensure_schema()
    yield db_path

    shutil.rmtree(tmpdir)

def test_tables_exist(temp_db):
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "snapchains", "rules", "metrics", "models", "quality_history",
        "rewards_log", "curiosity_log", "hypotheses", "meta_snaps"
    }
    missing = expected - tables
    assert not missing, f"Fehlende Tabellen: {missing}"

def test_insert_chain_and_quality(temp_db):
    # Insert SnapChain
    cid = sm.insert_chain_quick(blob=b"test", quality=0.75, origin="test")
    assert isinstance(cid, int) and cid > 0

    # Quality loggen
    ok = sm.log_quality(cid, 0.9)
    assert ok

    hist = sm.get_quality_history(cid)
    assert hist and isinstance(hist, list)
    assert hist[0]["quality"] == pytest.approx(0.9)

def test_model_registry(temp_db):
    mid = sm.register_model(task="vision", path="/tmp/model.onnx", family="onnx")
    assert isinstance(mid, int) and mid > 0

    models = sm.list_models(task="vision")
    assert any(m["id"] == mid for m in models)

    ok = sm.archive_model(mid)
    assert ok
    models = sm.list_models(task="vision", status="archived")
    assert any(m["id"] == mid for m in models)

def test_metrics(temp_db):
    ok = sm.insert_metric("coverage", 0.42)
    assert ok
    with sm.get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM metrics").fetchall()
        keys = [r["key"] for r in rows]
        assert "coverage" in keys
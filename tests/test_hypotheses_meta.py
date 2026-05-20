#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit-Tests für Hypothesen & MetaSnaps Tabellen
Pfad: /opt/ai/oroma/tests/test_hypotheses_meta.py
"""

import os
import sqlite3
import pytest
import tempfile
import shutil
import time

import core.sql_manager as sm

@pytest.fixture(scope="module")
def temp_db(monkeypatch):
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "oroma.db")
    monkeypatch.setenv("OROMA_BASE", tmpdir)

    sm.ensure_schema()
    yield db_path
    shutil.rmtree(tmpdir)

def test_tables_exist(temp_db):
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hypotheses" in tables
    assert "meta_snaps" in tables

def test_insert_hypothesis(temp_db):
    with sm.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO hypotheses (text, created) VALUES (?, ?)",
            ("Test-Hypothese", int(time.time())),
        )
        hid = cur.lastrowid
        conn.commit()
        assert hid > 0

        row = conn.execute("SELECT text FROM hypotheses WHERE id=?", (hid,)).fetchone()
        assert row["text"] == "Test-Hypothese"

def test_insert_meta_snap(temp_db):
    with sm.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO meta_snaps (label, score, sources) VALUES (?, ?, ?)",
            ("Meta-Test", 0.88, "chain:1,chain:2"),
        )
        mid = cur.lastrowid
        conn.commit()
        assert mid > 0

        row = conn.execute("SELECT label, score FROM meta_snaps WHERE id=?", (mid,)).fetchone()
        assert row["label"] == "Meta-Test"
        assert row["score"] == pytest.approx(0.88)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tests/test_games_vertical_learning_ui.py
# Projekt: ORÓMA – Headless Game UI / Vertical Learning Observability
# Stand:   2026-07-22
# =============================================================================
#
# Zweck
# ─────
#   Regressionstest für die read-only Vertical-Learning-Übersicht. Der Test
#   verwendet eine isolierte SQLite-Datenbank und prüft bewusst nicht nur die
#   HTTP-Form, sondern die fachliche Aggregation über alle produktiven
#   Pipeline-Stufen: SnapChain, Policy, Promotion, Acquisition, Outcome,
#   Mini-Write und Policy-Evidence-Lineage.
#
# Sicherheitsvertrag
# ──────────────────
#   • keine produktive Datenbank;
#   • keine Runner-Starts;
#   • keine Policy-Mutation;
#   • fehlende Tabellen werden als unavailable gemeldet und niemals angelegt.
# =============================================================================

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from ui import games_ui


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE snapchains (
            id INTEGER PRIMARY KEY, ts INTEGER, namespace TEXT, origin TEXT
        );
        CREATE TABLE policy_rules (
            id INTEGER PRIMARY KEY, namespace TEXT, n INTEGER, last_ts INTEGER
        );
        CREATE TABLE gap_policy_promotion_queue (
            id INTEGER PRIMARY KEY, namespace TEXT, status TEXT,
            created_ts INTEGER, updated_ts INTEGER
        );
        CREATE TABLE gap_targeted_acquisition_lifecycle (
            acquisition_id TEXT PRIMARY KEY, namespace TEXT, status TEXT,
            created_ts INTEGER, updated_ts INTEGER
        );
        CREATE TABLE gap_evidence_outcome_queue (
            id INTEGER PRIMARY KEY, namespace TEXT, status TEXT,
            created_ts INTEGER, updated_ts INTEGER
        );
        CREATE TABLE gap_policy_mini_write_ledger (
            id INTEGER PRIMARY KEY, namespace TEXT, status TEXT,
            policy_written INTEGER, created_ts INTEGER, updated_ts INTEGER
        );
        CREATE TABLE policy_rule_evidence_links (
            id INTEGER PRIMARY KEY, namespace TEXT, created_ts INTEGER
        );
        """
    )


def test_vertical_learning_aggregates_families_read_only(tmp_path, monkeypatch):
    db_path = tmp_path / "oroma.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.executemany(
        "INSERT INTO snapchains(id,ts,namespace,origin) VALUES(?,?,?,?)",
        [(1, 100, "game:snake", None), (2, 110, "game:snake3d", None)],
    )
    conn.executemany(
        "INSERT INTO policy_rules(id,namespace,n,last_ts) VALUES(?,?,?,?)",
        [(1, "game:snake", 8, 120), (2, "game:chess2_canon", 5, 121)],
    )
    conn.executemany(
        "INSERT INTO gap_policy_promotion_queue(id,namespace,status,created_ts,updated_ts) VALUES(?,?,?,?,?)",
        [
            (1, "game:snake", "promotion_review", 130, 130),
            (2, "game:snake", "policy_written", 131, 140),
            (3, "game:chess2_canon", "promotion_review", 132, 132),
        ],
    )
    conn.execute(
        "INSERT INTO gap_targeted_acquisition_lifecycle VALUES(?,?,?,?,?)",
        ("a1", "game:snake", "evidence_acquired", 141, 142),
    )
    conn.execute(
        "INSERT INTO gap_evidence_outcome_queue VALUES(?,?,?,?,?)",
        (1, "game:snake", "policy_written", 143, 144),
    )
    conn.executemany(
        "INSERT INTO gap_policy_mini_write_ledger VALUES(?,?,?,?,?,?)",
        [
            (1, "game:snake", "written", 1, 145, 145),
            (2, "game:snake", "blocked", 0, 146, 146),
        ],
    )
    conn.execute(
        "INSERT INTO policy_rule_evidence_links VALUES(?,?,?)",
        (1, "game:snake", 145),
    )
    conn.commit()
    conn.close()

    @contextmanager
    def isolated_conn_cm():
        test_conn = sqlite3.connect(db_path)
        test_conn.row_factory = sqlite3.Row
        try:
            yield test_conn
        finally:
            test_conn.close()

    monkeypatch.setattr(games_ui, "_vertical_read_conn", isolated_conn_cm)
    monkeypatch.setattr(games_ui.time, "time", lambda: 200)

    result = games_ui.build_vertical_learning_status()
    assert result["ok"] is True
    assert result["read_only"] is True
    assert all(result["tables"].values())

    by_family = {row["family"]: row for row in result["rows"]}
    snake = by_family["snake"]
    assert snake["snapchains"] == 1
    assert snake["policy_rules"] == 1
    assert snake["policy_samples"] == 8
    assert snake["promotions"] == 2
    assert snake["promotion_reviews"] == 1
    assert snake["promotion_policy_written"] == 1
    assert snake["acquisitions"] == 1
    assert snake["acquisitions_evidence_acquired"] == 1
    assert snake["outcomes"] == 1
    assert snake["outcomes_policy_written"] == 1
    assert snake["mini_writes"] == 1
    assert snake["blocked_writes"] == 1
    assert snake["evidence_links"] == 1
    assert snake["promotion_to_acquisition_pct"] == 50.0
    assert snake["acquisition_to_outcome_pct"] == 100.0
    assert snake["outcome_to_write_pct"] == 100.0
    assert snake["age_sec"] == 54

    assert by_family["snake3d"]["snapchains"] == 1
    assert by_family["chess"]["policy_samples"] == 5


def test_vertical_learning_does_not_create_missing_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    @contextmanager
    def isolated_conn_cm():
        test_conn = sqlite3.connect(db_path)
        test_conn.row_factory = sqlite3.Row
        try:
            yield test_conn
        finally:
            test_conn.close()

    monkeypatch.setattr(games_ui, "_vertical_read_conn", isolated_conn_cm)
    result = games_ui.build_vertical_learning_status()

    assert result["ok"] is True
    assert result["rows"] == []
    assert not any(result["tables"].values())

    conn = sqlite3.connect(db_path)
    tables = list(conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    conn.close()
    assert tables == []


def test_vertical_learning_aborts_slow_query_and_returns_partial(tmp_path, monkeypatch):
    db_path = tmp_path / "slow.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.executemany(
        "INSERT INTO snapchains(id,ts,namespace,origin) VALUES(?,?,?,?)",
        [(idx, idx, "game:snake", None) for idx in range(1, 5001)],
    )
    conn.execute(
        "INSERT INTO policy_rules(id,namespace,n,last_ts) VALUES(?,?,?,?)",
        (1, "game:snake", 3, 10),
    )
    conn.commit()
    conn.close()

    @contextmanager
    def isolated_conn_cm():
        test_conn = sqlite3.connect(db_path)
        test_conn.row_factory = sqlite3.Row
        try:
            yield test_conn
        finally:
            test_conn.close()

    monkeypatch.setattr(games_ui, "_vertical_read_conn", isolated_conn_cm)
    monkeypatch.setattr(games_ui, "_VERTICAL_QUERY_BUDGET_SEC", 0.000001)

    result = games_ui.build_vertical_learning_status()
    assert result["ok"] is True
    assert result["partial"] is True
    assert result["warnings"]
    assert any(item["query"] == "snapchains" for item in result["warnings"])


def test_vertical_learning_uses_bounded_tail_without_global_aggregate(tmp_path, monkeypatch):
    """Large-table observability must never regress to COUNT/GROUP full scans."""
    db_path = tmp_path / "large.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.executemany(
        "INSERT INTO snapchains(id,ts,namespace,origin) VALUES(?,?,?,?)",
        [(idx, idx, "game:snake", None) for idx in range(1, 501)],
    )
    conn.executemany(
        "INSERT INTO policy_rules(id,namespace,n,last_ts) VALUES(?,?,?,?)",
        [(idx, "game:snake", 1, idx) for idx in range(1, 501)],
    )
    conn.commit()
    conn.close()

    statements = []

    @contextmanager
    def isolated_conn_cm():
        test_conn = sqlite3.connect(db_path)
        test_conn.row_factory = sqlite3.Row
        test_conn.set_trace_callback(statements.append)
        try:
            yield test_conn
        finally:
            test_conn.close()

    monkeypatch.setattr(games_ui, "_vertical_read_conn", isolated_conn_cm)
    monkeypatch.setattr(games_ui, "_VERTICAL_TAIL_LIMIT", 100)

    result = games_ui.build_vertical_learning_status()
    snake = {row["family"]: row for row in result["rows"]}["snake"]
    assert snake["snapchains"] == 100
    assert snake["policy_rules"] == 100
    assert result["count_semantics"] == "bounded_newest_row_window"
    assert result["tail_limit_per_table"] == 100

    traced = "\n".join(statements).upper()
    assert "COUNT(" not in traced
    assert "GROUP BY" not in traced
    assert "DISTINCT" not in traced

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tests/test_gap_policy_mini_write_outcome_queue.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Regressionstest · Outcome Queue -> Policy Mini-Write -> Evidence-Link
# Stand:     2026-07-12
# =============================================================================
#
# ZWECK
# -----
# Dieser Test sichert den ersten produktiven Migrationsschritt der Referenz-
# domäne game:snake ab. Er prüft auf einer vollständig isolierten SQLite-
# Minimaldatenbank, dass ein aktuelles, ausreichend belastbares Outcome aus
# gap_evidence_outcome_queue atomar durch den Mini-Write-Gate verarbeitet wird.
#
# GEPRÜFTE INVARIANTEN
# --------------------
# - Quelle ist die Outcome Queue, nicht Promotion-Meta.
# - Genau ein Policy-Write wird geplant und ausgeführt.
# - Ledger, Policy-Upsert, Evidence-Link und Queue-Status entstehen atomar.
# - Die Evidence-ID ist deterministisch aus der Outcome-Signatur abgeleitet.
# - Ein zweiter Lauf ist idempotent und schreibt nicht erneut.
# - Kein echter DBWriter-Daemon und keine produktive Datenbank werden benötigt.
# =============================================================================

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core import gap_policy_mini_write as mini


def _create_db(path: Path) -> int:
    now = mini._now_ts()
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE policy_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            state_hash TEXT NOT NULL,
            action TEXT NOT NULL,
            n INTEGER NOT NULL DEFAULT 0,
            pos INTEGER NOT NULL DEFAULT 0,
            neg INTEGER NOT NULL DEFAULT 0,
            draw INTEGER NOT NULL DEFAULT 0,
            q REAL NOT NULL DEFAULT 0.0,
            last_ts INTEGER,
            centroid TEXT,
            UNIQUE(namespace,state_hash,action)
        );
        CREATE TABLE gap_policy_promotion_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_signature TEXT NOT NULL UNIQUE,
            request_signature TEXT NOT NULL,
            evidence_queue_id INTEGER,
            plan_id TEXT,
            focus_id TEXT,
            target TEXT NOT NULL,
            promotion_bucket TEXT NOT NULL,
            namespace TEXT,
            state_hash TEXT,
            primary_action TEXT,
            kind TEXT,
            reason TEXT,
            recommended_next TEXT,
            score REAL,
            status TEXT NOT NULL DEFAULT 'promotion_review',
            policy_write_allowed INTEGER NOT NULL DEFAULT 0,
            source_validation_bucket TEXT,
            source_validation_ts INTEGER,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            meta_json TEXT
        );
        CREATE TABLE gap_evidence_outcome_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_signature TEXT NOT NULL UNIQUE,
            promotion_signature TEXT,
            request_signature TEXT,
            promotion_id INTEGER,
            target TEXT,
            namespace TEXT,
            state_hash TEXT,
            action TEXT,
            outcome TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            evidence_source TEXT,
            replay_source TEXT,
            status TEXT NOT NULL DEFAULT 'outcome_ready',
            policy_write_allowed INTEGER NOT NULL DEFAULT 0,
            source_probe_ts INTEGER,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            meta_json TEXT
        );
        """
    )
    con.execute(
        """
        INSERT INTO gap_policy_promotion_queue(
            promotion_signature,request_signature,target,promotion_bucket,
            namespace,state_hash,primary_action,score,status,
            created_ts,updated_ts,meta_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "prom-snake-1",
            "req-snake-1",
            "replay",
            "promotion_candidate_replay",
            "game:snake",
            "snake:pro_v2:test",
            "1",
            1.0,
            "promotion_review",
            now,
            now,
            "{}",
        ),
    )
    con.execute(
        """
        INSERT INTO gap_evidence_outcome_queue(
            outcome_signature,promotion_signature,request_signature,promotion_id,
            target,namespace,state_hash,action,outcome,confidence,
            evidence_source,replay_source,status,source_probe_ts,
            created_ts,updated_ts,meta_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "outcome-snake-1",
            "prom-snake-1",
            "req-snake-1",
            1,
            "replay",
            "game:snake",
            "snake:pro_v2:test",
            "1",
            "pos",
            0.9,
            "gap_replay_evidence_probe",
            "snake_pro_v2_headless_replay",
            "outcome_ready",
            now,
            now,
            now,
            json.dumps({"evidence_class": "replay_reconstructed"}),
        ),
    )
    con.commit()
    con.close()
    return now


def _local_transaction(db_path: Path):
    def _run(statements, timeout_ms):
        del timeout_ms
        con = sqlite3.connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            for sql, params in statements:
                con.execute(sql, params)
            con.commit()
            return {"ok": True}
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    return _run


def test_outcome_queue_to_policy_link_is_atomic_and_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "oroma.db"
    state_path = tmp_path / "state.json"
    _create_db(db_path)

    monkeypatch.setattr(mini, "_dbwriter_ready", lambda timeout_ms: (True, "test_ready"))
    monkeypatch.setattr(mini, "_run_transaction", _local_transaction(db_path))
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_ENABLE", "1")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_CONFIRM_REQUIRED", "GAP_POLICY_MINI_WRITE_REVIEWED")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_CONFIRM", "GAP_POLICY_MINI_WRITE_REVIEWED")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_SOURCE", "outcome_queue")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_ALLOW_PROMOTION_META_FALLBACK", "0")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_NAMESPACE_ALLOWLIST", "game:snake")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_MIN_CONFIDENCE", "0.5")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_OUTCOME_MAX_AGE_SEC", "7200")

    first = mini.run_once(db_path=db_path, state_path=state_path, limit=10, topk=10, max_writes=1)
    assert first["ok"] is True
    assert first["summary"]["selected_source"] == "outcome_queue"
    assert first["summary"]["policy_writes_planned"] == 1
    assert first["summary"]["transaction_ok"] is True

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM policy_rules").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM gap_policy_mini_write_ledger").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM policy_rule_evidence_links").fetchone()[0] == 1
    evidence_id, evidence_class, mutation_type = con.execute(
        "SELECT evidence_id,evidence_class,mutation_type FROM policy_rule_evidence_links"
    ).fetchone()
    assert evidence_id == "evq:outcome-snake-1"
    assert evidence_class == "replay_reconstructed"
    assert mutation_type == "INSERT_RULE"
    assert con.execute(
        "SELECT status,policy_write_allowed FROM gap_evidence_outcome_queue"
    ).fetchone() == ("policy_written", 1)
    con.close()

    second = mini.run_once(db_path=db_path, state_path=state_path, limit=10, topk=10, max_writes=1)
    assert second["ok"] is True
    assert second["summary"]["source_rows_loaded"] == 0
    assert second["summary"]["policy_writes_planned"] == 0

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM policy_rules").fetchone()[0] == 1
    assert con.execute("SELECT n,pos,neg,draw,q FROM policy_rules").fetchone() == (1, 1, 0, 0, 1.0)
    assert con.execute("SELECT COUNT(*) FROM policy_rule_evidence_links").fetchone()[0] == 1
    con.close()


def test_state_schema_allowlist_blocks_other_schema(tmp_path, monkeypatch):
    """Sichert den produktiven Snake-only-Automatik-Gate gegen Fremdschemas ab."""
    db_path = tmp_path / "oroma.db"
    state_path = tmp_path / "state.json"
    _create_db(db_path)

    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE gap_policy_promotion_queue SET state_hash='snake:legacy_v1:test' WHERE id=1"
    )
    con.execute(
        "UPDATE gap_evidence_outcome_queue SET state_hash='snake:legacy_v1:test' WHERE id=1"
    )
    con.commit()
    con.close()

    monkeypatch.setattr(mini, "_dbwriter_ready", lambda timeout_ms: (True, "test_ready"))
    monkeypatch.setattr(mini, "_run_transaction", _local_transaction(db_path))
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_ENABLE", "1")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_CONFIRM_REQUIRED", "GAP_POLICY_MINI_WRITE_REVIEWED")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_CONFIRM", "GAP_POLICY_MINI_WRITE_REVIEWED")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_SOURCE", "outcome_queue")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_NAMESPACE_ALLOWLIST", "game:snake")
    monkeypatch.setenv("OROMA_GAP_POLICY_MINI_WRITE_STATE_SCHEMA_ALLOWLIST", "snake:pro_v2")

    result = mini.run_once(db_path=db_path, state_path=state_path, limit=10, topk=10, max_writes=1)

    assert result["ok"] is True
    assert result["summary"]["policy_writes_planned"] == 0
    assert result["summary"]["per_blocked_reason_counts"]["state_schema_not_allowed"] == 1
    assert result["gate"]["state_schema_allowlist"] == ["snake:pro_v2"]


def test_blocked_signature_ignores_only_volatile_age_fields():
    row = {
        "promotion_signature": "promotion-1",
        "request_signature": "request-1",
        "namespace": "game:snake",
        "state_hash": "snake:pro_v2:test",
        "action": "2",
        "outcome_signature": "outcome-1",
        "source_kind": "outcome_queue",
    }
    first_payload = {
        "outcome_queue_id": 605,
        "outcome_signature": "outcome-1",
        "evidence_class": "replay_reconstructed",
        "age_sec": 8000,
        "promotion_age_sec": 9000,
        "queue_meta": {"lineage": "fixed"},
    }
    later_payload = dict(first_payload, age_sec=8300, promotion_age_sec=9300)
    reasons = ["outcome_queue_row_stale", "promotion_row_stale"]

    first = mini._write_signature(row, "pos", "gap_evidence_outcome_queue", first_payload, reasons)
    later = mini._write_signature(row, "pos", "gap_evidence_outcome_queue", later_payload, reasons)
    assert first == later

    changed_lineage = dict(later_payload, queue_meta={"lineage": "changed"})
    changed = mini._write_signature(row, "pos", "gap_evidence_outcome_queue", changed_lineage, reasons)
    assert changed != first

    changed_reason = mini._write_signature(
        row,
        "pos",
        "gap_evidence_outcome_queue",
        later_payload,
        ["promotion_row_stale"],
    )
    assert changed_reason != first

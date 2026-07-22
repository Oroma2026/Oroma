#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tests/test_gap_policy_promotion_refresh.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Regressionstest · append-only Promotion-Dedupe ohne Row-Refresh
# Version:   v0.2.0
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieser Test sichert die historische Unveränderlichkeit der Promotion Queue.
# Eine fachlich identische Revalidierung besitzt dieselbe deterministische
# promotion_signature und darf weder ein Duplikat noch ein UPDATE erzeugen.
# Freshness wird downstream ausschließlich durch exakt gebundene Targeted-
# Evidence attestiert.
#
# GEPRUEFTE INVARIANTEN
# --------------------
# - Identische Promotion-Signaturen bleiben eindeutig und dedupliziert.
# - Neuere, gleiche oder ältere Validierungen verändern keine bestehende Zeile.
# - created_ts, updated_ts, source_validation_ts und Score bleiben unverändert.
# - Abgeschlossene policy_written-Zeilen werden niemals reaktiviert.
# - Der Test arbeitet ausschliesslich auf einer isolierten SQLite-In-Memory-DB.
# =============================================================================

from __future__ import annotations

import sqlite3

from core import gap_policy_promotion as promotion


def _candidate(validation_ts: int, score: float = 0.8):
    item = {
        "promotion_signature": "prom-refresh-1",
        "request_signature": "req-refresh-1",
        "evidence_queue_id": 11,
        "plan_id": "plan-1",
        "focus_id": "focus-1",
        "target": "replay",
        "promotion_bucket": "promotion_candidate_replay",
        "namespace": "game:snake",
        "state_hash": "snake:pro_v2:test",
        "primary_action": "1",
        "kind": "gap",
        "reason": "validated",
        "recommended_next": "replay",
        "score": score,
        "status": "promotion_review",
        "policy_write_allowed": 0,
        "source_validation_bucket": "validated_replay_execution_candidate",
        "source_validation_ts": validation_ts,
        "promotion_reason": "validated_replay_needs_final_policy_gate",
        "policy_validation_basis": "direct_replay_evidence",
        "policy_evidence_for_validation": {},
        "current_policy_evidence": {},
        "execution": {},
        "future_gate": {},
    }
    return item


def _db():
    con = sqlite3.connect(":memory:")
    for sql, params in promotion._schema_statements():
        con.execute(sql, params)
    return con


def test_newer_validation_does_not_mutate_open_promotion():
    con = _db()
    con.execute(*promotion._insert_statement(_candidate(100, 0.8), 110))
    con.execute(*promotion._insert_statement(_candidate(200, 0.95), 210))
    rows = con.execute(
        "SELECT COUNT(*), created_ts, updated_ts, source_validation_ts, score, status "
        "FROM gap_policy_promotion_queue"
    ).fetchone()
    assert rows == (1, 110, 110, 100, 0.8, "promotion_review")


def test_older_or_equal_validation_cannot_reduce_freshness():
    con = _db()
    con.execute(*promotion._insert_statement(_candidate(200, 0.95), 210))
    con.execute(*promotion._insert_statement(_candidate(200, 0.10), 220))
    con.execute(*promotion._insert_statement(_candidate(150, 0.20), 230))
    row = con.execute(
        "SELECT created_ts, updated_ts, source_validation_ts, score FROM gap_policy_promotion_queue"
    ).fetchone()
    assert row == (210, 210, 200, 0.95)


def test_completed_promotion_is_never_reactivated_by_revalidation():
    con = _db()
    con.execute(*promotion._insert_statement(_candidate(100, 0.8), 110))
    con.execute(
        "UPDATE gap_policy_promotion_queue SET status='policy_written', "
        "policy_write_allowed=1, updated_ts=120"
    )
    con.execute(*promotion._insert_statement(_candidate(300, 0.99), 310))
    row = con.execute(
        "SELECT status, policy_write_allowed, updated_ts, source_validation_ts, score "
        "FROM gap_policy_promotion_queue"
    ).fetchone()
    assert row == ("policy_written", 1, 120, 100, 0.8)

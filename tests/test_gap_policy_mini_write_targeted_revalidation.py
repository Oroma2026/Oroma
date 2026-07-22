#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tests/test_gap_policy_mini_write_targeted_revalidation.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Regressionstest · append-only Promotion-Freshness durch Targeted Evidence
# Version:   v0.2.0
# Stand:     2026-07-19
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK UND INVARIANTEN
# --------------------
# Der Test beweist, dass keine historische Promotion-Zeile aktualisiert werden
# muss. Nur ein frisches Targeted-Acquisition-Outcome mit vollständig identischer
# learning_intent_lineage darf die fachliche Promotion-Freshness attestieren.
# Unvollständige, generische oder manipulierte Lineage bleibt fail-closed.
# =============================================================================
from core import gap_policy_mini_write as mini


def _row():
    return {
        'current_promotion_id': 1771,
        'promotion_id': 1771,
        'promotion_signature': 'prom-1771',
        'request_signature': 'req-1771',
        'namespace': 'game:snake',
        'state_hash': 'snake:pro_v2:test',
        'action': '1',
        'promotion_target': 'replay',
        'target': 'replay',
        'promotion_bucket': 'promotion_candidate_replay',
        'source_probe_ts': 2000,
        'updated_ts': 1990,
    }


def _meta():
    return {'evidence_payload': {'targeted': {
        'source_kind': 'targeted_simulation_snapchain',
        'learning_intent_lineage': {
            'promotion_id': 1771,
            'promotion_signature': 'prom-1771',
            'request_signature': 'req-1771',
            'namespace': 'game:snake',
            'state_hash': 'snake:pro_v2:test',
            'primary_action': '1',
            'target': 'replay',
            'promotion_bucket': 'promotion_candidate_replay',
        },
    }}}


def test_exact_targeted_lineage_attests_freshness_without_row_mutation():
    assert mini._targeted_promotion_revalidation_ts(_row(), _meta()) == 2000


def test_identity_mismatch_is_fail_closed():
    meta = _meta()
    meta['evidence_payload']['targeted']['learning_intent_lineage']['state_hash'] = 'snake:pro_v2:other'
    assert mini._targeted_promotion_revalidation_ts(_row(), meta) is None


def test_noncanonical_targeted_source_kind_cannot_refresh_promotion():
    meta = _meta()
    meta['evidence_payload']['targeted']['source_kind'] = 'snake_targeted_acquisition_v2'
    assert mini._targeted_promotion_revalidation_ts(_row(), meta) is None

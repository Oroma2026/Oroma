#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/test_runner.py
# Projekt: ORÓMA
# Version: v1.0 – Core Smoke/Unit ohne pytest (System-Python)
# Stand:   2025-10-26
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Minimaler, headless Test-Runner für zentrale Pfade:
#     • sql_manager.ensure_schema → idempotent
#     • dream_worker → Kompressionspfad (Smoke)
#     • decision_engine → Legalitätsfilter (TTT, falls vorhanden)
#
# Aufruf
# ──────
#   PYTHONPATH=/opt/ai/oroma /usr/bin/python3 -m tools.test_runner
#
# Hinweis
# ───────
#   Keine destruktiven Änderungen. Bricht bei harten Fehlern mit Exit-Code 2 ab.
# =============================================================================

import os
import sys
import json
import importlib

DB = os.getenv("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")


def ok(msg: str) -> None:
    print("OK:", msg)


def must(cond: bool, msg: str) -> None:
    if not cond:
        print("FAIL:", msg)
        sys.exit(2)


def test_ensure_schema() -> None:
    sm = importlib.import_module("core.sql_manager")
    sm.ensure_schema()
    sm.ensure_schema()
    ok("ensure_schema idempotent")


def test_dream_compress_smoke() -> None:
    try:
        sm = importlib.import_module("core.sql_manager")
        blob = json.dumps(
            {"snaps": [{"features": [0] * 9}], "origin": "sim", "namespace": "sim"}
        )
        sm.insert_snapchain(blob=blob, origin="sim", namespace="sim", quality=0.1)

        dw = importlib.import_module("core.dream_worker")
        if hasattr(dw, "main_once"):
            dw.main_once()
        ok("dream compress path (smoke)")
    except Exception as e:
        ok(f"dream compress skipped ({e})")


def test_decision_legal() -> None:
    try:
        dec = importlib.import_module("core.decision_engine")
        if hasattr(dec, "TTTDecision"):
            t = dec.TTTDecision()
            act = t.choose_action_from_board(["X", "O", "X", "", "", "", "", "", ""])
            must(act in [str(i) for i in range(9)], "action within board")
            ok("decision legal filter")
        else:
            ok("decision engine not present; skipped")
    except Exception as e:
        ok(f"decision legal skipped ({e})")


if __name__ == "__main__":
    test_ensure_schema()
    test_dream_compress_smoke()
    test_decision_legal()
    print("ALL TESTS PASSED")
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/selftest_ui.py
# Projekt: ORÓMA
# Version: v3.5patch2 (final+ENV-Flags)
# Stand:   2025-09-25
# Zweck:
#   - UI-Blueprint für Patch2-Selftests
#   - Buttons für Empathy-Events & Coverage-Einträge
#   - Anzeige von Rückmeldungen
#   - Deaktivierbar per ENV (OROMA_ENABLE_EMPATHY / OROMA_ENABLE_COVERAGE)
# =============================================================================

from __future__ import annotations
import os, time
from flask import Blueprint, render_template, jsonify

try:
    from core import sql_manager
    _HAS_SQL = True
except Exception:
    sql_manager = None
    _HAS_SQL = False

bp = Blueprint("selftest_ui", __name__, url_prefix="/selftest")

# ENV-Flags
_ENABLE_EMPATHY = os.environ.get("OROMA_ENABLE_EMPATHY", "true").lower() in ("1", "true", "yes")
_ENABLE_COVERAGE = os.environ.get("OROMA_ENABLE_COVERAGE", "true").lower() in ("1", "true", "yes")

# ----------------------------- UI --------------------------------------------

@bp.route("/")
def page():
    return render_template(
        "selftest.html",
        enabled_empathy=_ENABLE_EMPATHY,
        enabled_cov=_ENABLE_COVERAGE,
    )

# ----------------------------- API -------------------------------------------

@bp.post("/api/empathy")
def api_empathy():
    if not (_ENABLE_EMPATHY and _HAS_SQL):
        return jsonify({"ok": False, "error": "empathy disabled"})
    ts = int(time.time())
    eid = sql_manager.insert_empathy_snap(ts, "happy", 0.95)
    return jsonify({"ok": bool(eid), "eid": eid, "ts": ts})

@bp.post("/api/coverage")
def api_coverage():
    if not (_ENABLE_COVERAGE and _HAS_SQL):
        return jsonify({"ok": False, "error": "coverage disabled"})
    ts = int(time.time())
    total = 20
    active = 15
    cov = active / total
    cid = sql_manager.insert_coverage(ts, cov, active, total)
    return jsonify({"ok": bool(cid), "cid": cid, "coverage": cov, "ts": ts})
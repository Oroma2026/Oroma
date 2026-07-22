"""Regression contract for G3.1 persistence/verification error separation."""
from pathlib import Path


def test_prewrite_dbwriter_failure_is_not_reported_as_postwrite_verification_failure():
    source = (Path(__file__).resolve().parents[1] / "tools" / "snake_targeted_acquisition_v2_persist.py").read_text(encoding="utf-8")
    assert "atomic_lifecycle_evidence_persistence_failed:{persistence_reason}" in source
    assert source.index("if not persistence.get(\"ok\")") < source.index("after_write = _find_evidence")

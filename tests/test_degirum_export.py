# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – DeGirum Export Tests
Pfad: /opt/ai/oroma/tests/test_degirum_export.py

Zweck:
- Testet degirum_export im Simulationsmodus (kein echtes SDK notwendig)
- Prüft Policy-, Meta- und Compile-Dateien
- Stellt sicher, dass Registry-Einträge im Simulationsmodus unterbleiben
"""

import os
import json
import pytest
from exports import degirum_export
from core import sql_manager


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@pytest.mark.exports
def test_export_degirum_simulation(tmp_path, ensure_db):
    """Simulierter DeGirum-Export ohne echtes SDK"""
    # Dummy-Input anlegen
    onnx = tmp_path / "dummy.onnx"
    onnx.write_bytes(b"fake-onnx-binary")
    out_root = tmp_path / "out"

    # Export im Simulationsmodus
    result = degirum_export.export_degirum_package(
        onnx_path=str(onnx),
        out_root=str(out_root),
        simulate=True,
        task="detector"
    )

    # Basis-Checks
    assert "out_dir" in result and os.path.isdir(result["out_dir"])
    for fname in ("meta.json", "policy.json", "compile.json"):
        assert os.path.exists(os.path.join(result["out_dir"], fname))

    # Compile-Result: Simulationsmodus → ok=False, keine Registry
    with open(os.path.join(result["out_dir"], "compile.json"), "r", encoding="utf-8") as f:
        compile_info = json.load(f)
    assert compile_info["ok"] is False
    assert result.get("registry_id") is None


@pytest.mark.exports
def test_export_degirum_paths_and_policy(tmp_path, ensure_db):
    """Testet Pfade und Policies im Simulationsmodus"""
    # Dummy-Inputs
    onnx = tmp_path / "model.onnx"
    onnx.write_bytes(b"x" * 1024)
    labels = tmp_path / "labels.txt"
    labels.write_text("class0\nclass1\n")

    out_root = tmp_path / "out"

    result = degirum_export.export_degirum_package(
        onnx_path=str(onnx),
        out_root=str(out_root),
        name="dg_model",
        labels_path=str(labels),
        simulate=True
    )

    # Dateien vorhanden
    assert os.path.isdir(result["out_dir"])
    for fname in ("labels.txt", "preproc.json", "postproc.json"):
        assert os.path.exists(os.path.join(result["out_dir"], fname))
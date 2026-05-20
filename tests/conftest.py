# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – conftest.py
Pytest-Fixtures für reproduzierbare Tests:
- Setzt ENV-Variablen
- Stellt Stub-Wrapper bereit (CPU/Hailo/DeGirum)
- Initialisiert Datenbank-Schema
- Unterstützt v3.5 Features (MetaSnaps)
"""

import os
import sys
import types
import importlib
import pytest


# ----------------------------------------------------------------------
# Basis-ENV für alle Tests
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def set_base_env(monkeypatch, tmp_path):
    # Basis-Pfad
    monkeypatch.setenv("OROMA_BASE", "/opt/ai/oroma")

    # Standard-Test-DB (isoliert)
    test_db = tmp_path / "oroma_test.db"
    monkeypatch.setenv("OROMA_DB", str(test_db))

    # Feature-Flags
    monkeypatch.setenv("OROMA_BACKEND_PREF", "cpu")
    monkeypatch.setenv("OROMA_FAILOVER", "true")
    monkeypatch.setenv("OROMA_ENABLE_METASNAP", "false")

    yield


# ----------------------------------------------------------------------
# DB-Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def ensure_db():
    """Initialisiert das DB-Schema für Tests."""
    from core.sql_manager import ensure_schema
    ensure_schema()


# ----------------------------------------------------------------------
# Wrapper-Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def reset_oroma_wrapper():
    """Reload des Meta-Wrappers, damit Stubs greifen."""
    import wrappers.oroma_wrapper as ow
    importlib.reload(ow)
    return ow


@pytest.fixture
def stub_cpu_detector(monkeypatch):
    """Stub für Vision-CPU-Backend (deterministisch)."""
    from wrappers import vision_wrapper

    class CpuDetectorStub:
        def __init__(self, meta=None):
            self.meta = meta
            self.initialized = True
        def detect(self, frame):
            return [{"label": "stub", "conf": 1.0, "box": [0, 0, 1, 1]}]
        def embed(self, frame):
            return [0.0, 1.0, 0.5]

    monkeypatch.setattr(vision_wrapper, "CpuDetector", CpuDetectorStub, raising=True)
    return CpuDetectorStub


@pytest.fixture
def fake_hailo_module(monkeypatch):
    """Stub-Modul für Hailo-Wrapper."""
    mod = types.ModuleType("wrappers.hailo_wrapper")

    class HailoDetectorStub:
        def __init__(self, meta=None):
            self.meta = meta
        def detect(self, frame):
            return [{"label": "hailo", "conf": 0.99, "box": [1, 1, 2, 2]}]

    mod.HailoDetector = HailoDetectorStub
    monkeypatch.setitem(sys.modules, "wrappers.hailo_wrapper", mod)
    return mod


@pytest.fixture
def fake_degirum_module(monkeypatch):
    """Stub-Modul für DeGirum-Wrapper."""
    mod = types.ModuleType("wrappers.degirum_wrapper")

    class DeGirumDetectorStub:
        def __init__(self, meta=None):
            self.meta = meta
        def detect(self, frame):
            return [{"label": "degirum", "conf": 0.98, "box": [2, 2, 3, 3]}]

    mod.DeGirumDetector = DeGirumDetectorStub
    monkeypatch.setitem(sys.modules, "wrappers.degirum_wrapper", mod)
    return mod


# ----------------------------------------------------------------------
# Erweiterung: MetaSnaps
# ----------------------------------------------------------------------
@pytest.fixture
def fake_meta_snap(monkeypatch):
    """Stub für MetaSnaps (v3.5)."""
    from core import meta_snap

    class FakeMetaSnap:
        def __init__(self, label="test_meta", score=0.5):
            self.label = label
            self.score = score
            self.sources = [1, 2, 3]

    monkeypatch.setattr(meta_snap, "MetaSnap", FakeMetaSnap, raising=True)
    return FakeMetaSnap
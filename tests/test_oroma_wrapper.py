# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Tests für den Meta-Wrapper
Pfad: /opt/ai/oroma/tests/test_oroma_wrapper.py

Zweck:
- Testet Auswahl-Logik des Meta-Wrappers (Hailo, DeGirum, CPU-Fallback)
- Prüft Stub-Implementierungen, damit kein echtes SDK benötigt wird
- Sicherstellt, dass reload_backend() konsistent arbeitet
"""

import importlib
import numpy as np
import pytest


@pytest.mark.wrapper
def test_cpu_fallback_detect(reset_oroma_wrapper, stub_cpu_detector, monkeypatch):
    """Wenn CPU erzwungen ist → Stub-CPU-Detector liefern"""
    monkeypatch.setenv("OROMA_BACKEND_PREF", "cpu")
    ow = reset_oroma_wrapper
    det = ow.get_detector()
    assert det.backend() == "cpu"
    out = det.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert isinstance(out, list)
    assert out and out[0]["label"] == "stub"


@pytest.mark.wrapper
def test_hailo_preferred_when_available(reset_oroma_wrapper, monkeypatch, fake_hailo_module):
    """auto → bevorzugt Hailo, wenn verfügbar"""
    monkeypatch.setenv("OROMA_BACKEND_PREF", "auto")

    ow = reset_oroma_wrapper

    # Monkeypatch can_use_hailo → True
    ow._BackendDetector._can_use_hailo = staticmethod(lambda: True)
    # und DeGirum deaktivieren
    ow._BackendDetector._can_use_degirum = staticmethod(lambda: False)

    importlib.reload(ow)
    det = ow.get_detector()
    assert det.backend() == "hailo"
    out = det.detect(np.zeros((5, 5, 3), dtype=np.uint8))
    assert out and out[0]["label"] == "hailo"


@pytest.mark.wrapper
def test_degirum_when_hailo_not_available(reset_oroma_wrapper, monkeypatch, fake_degirum_module):
    """auto → wenn Hailo nicht geht, aber DeGirum geht → DeGirum wählen"""
    monkeypatch.setenv("OROMA_BACKEND_PREF", "auto")

    ow = reset_oroma_wrapper
    ow._BackendDetector._can_use_hailo = staticmethod(lambda: False)
    ow._BackendDetector._can_use_degirum = staticmethod(lambda: True)

    importlib.reload(ow)
    det = ow.get_detector()
    assert det.backend() == "degirum"
    out = det.detect(np.zeros((5, 5, 3), dtype=np.uint8))
    assert out and out[0]["label"] == "degirum"


@pytest.mark.wrapper
def test_reload_backend(reset_oroma_wrapper, monkeypatch):
    """reload_backend() soll konsistenten Backend-Typ liefern"""
    monkeypatch.setenv("OROMA_BACKEND_PREF", "cpu")
    ow = reset_oroma_wrapper
    b1 = ow.get_detector().backend()
    b2 = ow.reload_backend()
    assert b1 == b2
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/graceful.py
# Projekt: ORÓMA
# Modul:   Graceful Shutdown (Signale + zentraler Stop)
# Version: v1.0
# Stand:   2025-10-03
# -----------------------------------------------------------------------------
# Zweck
# ─────
#  - Einheitliche Beenden-Logik für ORÓMA:
#      • stoppt DeviceHub (Kamera, Mic)
#      • stoppt TTS-Wrapper
#      • stoppt VisionWrapper der Video-UI (falls aktiv)
#      • flush’t Logging und beendet den Prozess nur, wenn gewünscht
#  - Kann über Signale (SIGTERM/SIGINT) oder programmatisch aufgerufen werden.
# =============================================================================

from __future__ import annotations

import os
import sys
import time
import signal
import logging
from typing import Optional
from core.log_guard import log_suppressed

from core import log_guard
logger = logging.getLogger(__name__)
LOG = logging.getLogger("oroma.graceful")
if not LOG.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [graceful] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(os.environ.get("OROMA_LOG_LEVEL", "INFO"))

def graceful_shutdown(reason: str = "manual", exit_process: bool = False, exit_code: int = 0) -> None:
    """Stoppt alle zentralen Ressourcen best effort."""
    LOG.info("Graceful shutdown gestartet (reason=%s) …", reason)

    # 1) DeviceHub: Audio + Kamera
    try:
        from core.device_hub import get_hub  # type: ignore
        hub = get_hub()
        try:
            hub.stop_mic(client="graceful")
        except Exception as e:
            log_guard.log_suppressed(logger, key="graceful.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        try:
            hub.stop()  # stoppt Capture-Thread + Kamera
        except Exception as e:
            log_guard.log_suppressed(logger, key="graceful.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        LOG.info("DeviceHub gestoppt.")
    except Exception as e:
        LOG.debug("DeviceHub nicht verfügbar: %s", e)

    # 2) TTS-Wrapper
    try:
        from wrappers.tts_wrapper import _get_singleton  # type: ignore
        try:
            _get_singleton().stop()
            LOG.info("TTS gestoppt.")
        except Exception as e:
            log_guard.log_suppressed(logger, key="graceful.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    except Exception as e:
        LOG.debug("TTS-Wrapper nicht verfügbar: %s", e)

    # 3) Video-UI VisionWrapper (hat bereits atexit, hier proaktiv)
    try:
        # Der Blueprint exportiert eine _shutdown()-Funktion
        from ui.video_ui import _shutdown as video_shutdown  # type: ignore
        try:
            video_shutdown()
            LOG.info("Video-UI VisionWrapper gestoppt.")
        except Exception as e:
            log_guard.log_suppressed(logger, key="graceful.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    except Exception as e:
        # Falls nicht importierbar (kein UI-Prozess) → egal
        log_suppressed(
            logging.getLogger(__name__),
            key="core.graceful.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    try:
        logging.shutdown()
    except Exception as e:
        log_guard.log_suppressed(logger, key="graceful.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    LOG.info("Graceful shutdown fertig.")
    if exit_process:
        # Mini-Delay, damit Antworten (HTTP) noch rausgehen
        try:
            time.sleep(0.25)
        except Exception as e:
            log_guard.log_suppressed(logger, key="graceful.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        os._exit(exit_code)


# ----------------------------------------------------------------------------- 
# Signal-Handler installieren (im App-Main oder WSGI-Entry aufrufen)
# -----------------------------------------------------------------------------
def install_signal_handlers() -> None:
    """Registriert SIGTERM/SIGINT → graceful_shutdown + sauberer Exit."""
    def _handler(signum, frame):
        nm = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}.get(signum, str(signum))
        graceful_shutdown(reason=nm, exit_process=True, exit_code=0)

    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        LOG.info("Signal-Handler installiert (SIGTERM/SIGINT).")
    except Exception as e:
        LOG.warning("Signal-Handler konnten nicht installiert werden: %s", e)
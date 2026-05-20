#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/forgetting_worker.py
# Projekt: ORÓMA
# Version: v3.5patch2.2
# Stand:   2025-09-26
#
# Zweck:
#   ForgettingWorker für ORÓMA:
#     - Führt periodische Forgetting-Phasen aus
#     - Nutzt core/forgetting.py (decay, compress, meta)
#     - Getrennt vom DreamWorker → flexibler via systemd Timer steuerbar
#
# Steuerung:
#   - ForgettingWorker(interval: int)
#       → interval > 0 = Loop-Modus
#       → interval = 0 = einmaliger Lauf (für systemd Timer)
#
# Logging:
#   /opt/ai/oroma/logs/forgetting.out.log + forgetting.err.log
# =============================================================================

import logging
import threading
import time
import os
import sqlite3

from core import forgetting, sql_manager
# -----------------------------------------------------------------------------
# Logging Setup
# -----------------------------------------------------------------------------
LOG_DIR = os.environ.get("OROMA_LOG_DIR", "/opt/ai/oroma/logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG = logging.getLogger("oroma.forgetting_worker")
if not LOG.handlers:
    fh_out = logging.FileHandler(os.path.join(LOG_DIR, "forgetting.out.log"))
    fh_err = logging.FileHandler(os.path.join(LOG_DIR, "forgetting.err.log"))
    sh = logging.StreamHandler()

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    fh_out.setFormatter(fmt)
    fh_err.setFormatter(fmt)
    sh.setFormatter(fmt)

    fh_out.setLevel(logging.INFO)
    fh_err.setLevel(logging.ERROR)
    sh.setLevel(logging.INFO)

    LOG.addHandler(fh_out)
    LOG.addHandler(fh_err)
    LOG.addHandler(sh)

LOG.setLevel(logging.INFO)


class ForgettingWorker(threading.Thread):
    """
    Hintergrund-Thread für Forgetting-Mechanismen (Verblassen, Kompression, Meta).
    """

    def __init__(self, interval: int = 0):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop_event = threading.Event()

        LOG.info("ForgettingWorker initialisiert – Interval=%s", self.interval)

    def stop(self):
        self._stop_event.set()

    def run(self):
        LOG.info("ForgettingWorker gestartet")
        if self.interval == 0:
            self._safe_forget()
            LOG.info("ForgettingWorker Single-Run beendet")
            return

        while not self._stop_event.is_set():
            self._safe_forget()
            time.sleep(self.interval)

        LOG.info("ForgettingWorker gestoppt")

    def _safe_forget(self):
        """
        Führt einen Forgetting-Durchlauf robust aus.

        Bei kurzzeitigem DB-Lock (sqlite3.OperationalError: database is locked)
        wird bis zu MAX_RETRIES mal mit kurzer Pause erneut versucht.
        """
        MAX_RETRIES = 3
        RETRY_DELAY = 2.0  # Sekunden

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                sql_manager.ensure_schema()
                stats = forgetting.nightly_forgetting()
                LOG.info("Vergessen abgeschlossen: %s", stats)
                return
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "database is locked" in msg and attempt < MAX_RETRIES:
                    LOG.warning(
                        "ForgettingWorker: DB locked (Versuch %d/%d), Retry in %.1fs",
                        attempt,
                        MAX_RETRIES,
                        RETRY_DELAY,
                    )
                    time.sleep(RETRY_DELAY)
                    continue
                LOG.error("Fehler im ForgettingWorker (OperationalError): %s", e)
                return
            except Exception as e:
                LOG.error("Fehler im ForgettingWorker: %s", e)
                return

# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    fw = ForgettingWorker(interval=0)
    fw.start()
    fw.join()
    LOG.info("Selftest ForgettingWorker abgeschlossen")
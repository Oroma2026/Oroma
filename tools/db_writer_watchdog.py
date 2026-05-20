#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/db_writer_watchdog.py
# Projekt:   ORÓMA – DBWriter Watchdog Helper
# Version:   v3.7.3+
# Stand:     2026-03-05
#
# Zweck
# -----
# Dieses Script ist ein extrem robuster Health-Check für den DBWriter-Daemon.
# Es wird von systemd (oneshot) aufgerufen und vermeidet bewusst komplexes
# Quoting innerhalb der Unit (kein heredoc, keine verschachtelten Quotes).
#
# Verhalten
# ---------
# - Ping via core.db_writer_client.ping(timeout_ms)
# - Exitcode 0 bei Erfolg, 2 bei Fail
# - Keine Exceptions nach außen (im Fehlerfall exit(2))
#
# ENV
# ---
# - OROMA_DBW_ENABLE=1
# - OROMA_DBW_WATCHDOG_TIMEOUT_MS (Default: 500)
# =============================================================================

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_project_root() -> None:
    """
    Stellt sicher, dass der ORÓMA-Projektroot in sys.path vorhanden ist.

    Hintergrund:
    - systemd ruft dieses Script per absolutem Pfad aus /opt/ai/oroma/tools/ auf.
    - Python setzt dann sys.path[0] auf das Script-Verzeichnis (/opt/ai/oroma/tools).
    - Das Package "core" liegt jedoch eine Ebene höher im Projektroot.

    Ohne diese explizite Bootstrap-Logik kann der Watchdog im Produktivbetrieb
    mit "No module named 'core'" fehlschlagen, obwohl WorkingDirectory korrekt
    gesetzt ist.
    """
    try:
        project_root = Path(__file__).resolve().parent.parent
        root_s = str(project_root)
        if root_s and root_s not in sys.path:
            sys.path.insert(0, root_s)
    except Exception:
        pass


def main() -> int:
    try:
        _bootstrap_project_root()
        os.environ["OROMA_DBW_ENABLE"] = "1"
        from core import db_writer_client  # type: ignore
        to = int(os.getenv("OROMA_DBW_WATCHDOG_TIMEOUT_MS", "500"))
        ok = bool(db_writer_client.ping(to))
        print(f"dbw_ping={ok} timeout_ms={to}")
        return 0 if ok else 2
    except Exception as e:
        try:
            print(f"dbw_ping=false error={e}")
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

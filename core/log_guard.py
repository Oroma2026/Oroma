# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/log_guard.py
# Projekt:   ORÓMA (Offline-First · Headless · Core Safety Utilities)
# Modul:     LogGuard – rate-limited Logging für wiederholte Fehler + optionale globale Excepthooks (ohne Zirkularimporte)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# ORÓMA läuft im Live-Betrieb mit vielen Loops/Hooks/Jobs. Fehler (z. B. DB Locks,
# Device-Glitches, optionale Imports) können *sehr häufig* auftreten.
#
# Ohne Schutz führt das zu:
#   - Log-Spam (Logs wachsen, Debug wird unbrauchbar)
#   - CPU/IO Overhead (jede Exception schreibt erneut)
#   - indirekten Hängern/Slowdowns auf Edge-Hardware (Pi)
#
# LogGuard löst das durch:
#   1) log_suppressed(): identische Fehler nur alle N Sekunden loggen (rate-limited)
#   2) log_once(): bestimmte Meldungen exakt einmal loggen
#   3) install_global_excepthooks(): optional, um unhandled Exceptions stabil zu protokollieren
#
# WICHTIGSTE INVARIANTE (PRODUKTIONSKRITISCH)
# ──────────────────────────────────────────
# Dieses Modul muss „dependency-light“ bleiben und darf **keine** Zirkularimporte erzeugen.
# Deshalb gilt:
#   - keine Imports aus ui/*
#   - keine Imports, die wiederum log_guard importieren
#   - Logging-Fallbacks dürfen niemals erneut log_guard aufrufen (keine Rekursion)
#
# RATE-LIMITING MECHANIK
# ──────────────────────
# - Key-basierter Speicher (_LAST): dict[str, float]
# - Für jeden Key wird die letzte Log-Zeit gespeichert.
# - Wird derselbe Key erneut geloggt bevor `interval_s` abgelaufen ist, wird er unterdrückt.
#
# Thread-Safety:
# - interne Strukturen sind durch einen Lock geschützt.
#
# LEVEL-COERCION (HISTORISCHE KOMPATIBILITÄT)
# ───────────────────────────────────────────
# In ORÓMA wurden teils Strings ("WARN", "INFO") oder ungewöhnliche Werte als Level übergeben.
# _coerce_level() konvertiert das defensiv zu int, damit der Guard selbst nicht crasht.
#
# ENV-VARIABLEN
# ─────────────
#   OROMA_LOG_GUARD_INTERVAL_S
#     Default: 300
#     Bedeutung: minimaler Abstand in Sekunden zwischen Logs für denselben Key.
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# log_suppressed(logger, key: str, msg: str, exc: Exception|None=None, level=WARNING, interval_s=None)
#   - loggt msg (+ optional Exception) nur rate-limited pro key
#
# log_once(logger, key: str, msg: str, level=INFO)
#   - loggt msg genau einmal pro Prozess
#
# install_global_excepthooks(logger=None)
#   - installiert sys.excepthook + threading.excepthook (wenn verfügbar)
#   - Ziel: unhandled Exceptions landen zuverlässig in Logs, ohne Crash-Schleifen
#
# _safe_stderr(line)
#   - letzter Fallback: schreibt direkt auf stderr, darf niemals raisen
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT ÄNDERN)
# ───────────────────────────────────────────
# - Muss ohne zusätzliche Dependencies laufen (stdlib only).
# - Darf niemals selbst Exceptions werfen, die Rekursion/Crash verursachen.
# - Muss Zirkularimporte strikt vermeiden.
#
# =============================================================================
# END HEADER
# =============================================================================

# NOTE: log_guard must stay dependency-light (no self-imports, no ui imports) to avoid circular imports.
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Optional

_LOCK = threading.Lock()
_LAST: dict[str, float] = {}
_ONCE: set[str] = set()


def _env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name, "").strip()
        return int(v) if v else default
    except Exception:
        return default


def _default_interval_s() -> int:
    return max(0, _env_int("OROMA_LOG_GUARD_INTERVAL_S", 300))


def _safe_stderr(line: str) -> None:
    """Last-resort output. Must never raise."""
    try:
        sys.stderr.write(line.rstrip("\n") + "\n")
        sys.stderr.flush()
    except Exception:
        # no recursion, no re-raise
        pass


def _coerce_level(level) -> int:
    """Coerce arbitrary log-level values to an integer.

    The stdlib logging API requires `level` to be an int. Some hooks/patches
    historically passed strings like "WARN"/"INFO" or other values.
    We accept those inputs and map them to ints to avoid crashing the
    suppression guard itself.
    """

    try:
        if isinstance(level, int):
            return level

        # Common case: string level names
        if isinstance(level, str):
            s = level.strip()
            if not s:
                return logging.WARNING

            # Numeric string
            try:
                return int(s)
            except Exception:
                pass

            # Map aliases and names
            s2 = s.upper()
            if s2 == "WARN":
                s2 = "WARNING"
            if s2 == "FATAL":
                s2 = "CRITICAL"

            # logging._nameToLevel exists in stdlib; fallback to WARNING.
            return int(getattr(logging, "_nameToLevel", {}).get(s2, logging.WARNING))

        # Last resort: try int conversion
        return int(level)
    except Exception:
        return logging.WARNING


def log_suppressed(
    logger: Optional[logging.Logger],
    *,
    key: str,
    msg: str,
    exc: Optional[BaseException] = None,
    level: int = logging.WARNING,
    interval_s: Optional[int] = None,
) -> bool:
    """Rate-limited logging for repeating failures.

    Returns True if something was logged, False if suppressed.
    """
    if not key:
        key = "(no-key)"

    now = time.time()
    interval = _default_interval_s() if interval_s is None else max(0, int(interval_s))

    try:
        with _LOCK:
            last = _LAST.get(key, 0.0)
            if interval > 0 and (now - last) < interval:
                return False
            _LAST[key] = now

        lg = logger if isinstance(logger, logging.Logger) else logging.getLogger("oroma.guard")
        lvl = _coerce_level(level)

        full = f"[guard:{key}] {msg}"

        if exc is not None:
            exc_info = (type(exc), exc, exc.__traceback__)
            lg.log(lvl, full, exc_info=exc_info)
        else:
            lg.log(lvl, full)

        return True

    except Exception as e:
        # Never call log_suppressed() again here -> avoids recursion.
        _safe_stderr(f"[guard:fatal] log_suppressed failed: {e!r} | key={key} msg={msg}")
        return False


def log_once(
    logger: Optional[logging.Logger],
    *,
    key: str,
    msg: str,
    level: int = logging.WARNING,
) -> bool:
    """Log a message once per process lifetime."""
    if not key:
        key = "(no-key)"

    try:
        with _LOCK:
            if key in _ONCE:
                return False
            _ONCE.add(key)

        lg = logger if isinstance(logger, logging.Logger) else logging.getLogger("oroma.guard")
        lg.log(level, f"[once:{key}] {msg}")
        return True

    except Exception as e:
        _safe_stderr(f"[guard:fatal] log_once failed: {e!r} | key={key} msg={msg}")
        return False


def install_global_excepthooks(logger: Optional[logging.Logger] = None) -> None:
    """Install sys/thread exception hooks that route into log_suppressed().

    This must be safe to call even if partial imports are happening.
    """
    lg = logger if isinstance(logger, logging.Logger) else logging.getLogger("oroma")

    def _sys_hook(exctype, value, tb):  # type: ignore[no-untyped-def]
        try:
            exc = value if isinstance(value, BaseException) else Exception(str(value))
            log_suppressed(lg, key="unhandled.sys", msg=f"Unhandled exception (sys.excepthook): {exc!r}", exc=exc, level=logging.ERROR, interval_s=0)
        except Exception as e:
            _safe_stderr(f"[guard:fatal] sys.excepthook failed: {e!r}")

    sys.excepthook = _sys_hook

    # Python 3.8+ supports threading.excepthook
    if hasattr(threading, "excepthook"):
        def _thread_hook(args):  # type: ignore[no-untyped-def]
            try:
                exc = args.exc_value if isinstance(args.exc_value, BaseException) else Exception(str(args.exc_value))
                tname = getattr(args.thread, "name", "(thread)")
                log_suppressed(lg, key=f"unhandled.thread.{tname}", msg=f"Unhandled exception in thread {tname}", exc=exc, level=logging.ERROR, interval_s=0)
            except Exception as e:
                _safe_stderr(f"[guard:fatal] threading.excepthook failed: {e!r}")

        threading.excepthook = _thread_hook  # type: ignore[attr-defined]


__all__ = [
    "log_suppressed",
    "log_once",
    "install_global_excepthooks",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/universal_policy/ram_sched.py
# Projekt: ORÓMA
# Modul:   RAMFlushScheduler (in-process, RAM→PolicyEngine→DB→Archiv)
# Version: v3.9-rc2
# Stand:   2025-11-10
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#  Hintergrund-Worker im selben Prozess, der in festen Intervallen:
#    1) direkt aus dem RAM-Writer trainiert (ohne SD/DB-Schreiblast),
#    2) optional nur „gute“ Episoden in die DB promotet,
#    3) bei Erreichen einer Steps-Schwelle automatisch Regeln exportiert.
#
#  • Keine tmpfs-Pflicht. Optionaler einmaliger tmpfs→RAM-Recover beim Start.
#  • Keine Überschneidungen (Overlap-Lock) – ein Lauf zur Zeit.
#  • Adaptive Ruhe: bei Leerlauf exponentielles Backoff (bis max_backoff_sec).
#  • Jitter gegen Synchron-Effekte (Lastspitzen).
#
# ENV (alle optional, sensible Defaults)
# ──────────────────────────────────────
#  OROMA_RAM_SCHED_ENABLE=1            → nur als Beispiel für Integration in run_oroma.py
#  OROMA_RAM_RECOVER_ON_START=1        → einmalig tmpfs→RAM übernehmen (falls vorhanden)
#  OROMA_ADAPTER=auto|universal|ttt    → Adapterwahl
#
#  OROMA_RAM_SCHED_INTERVAL=30         → Zielintervall in Sekunden
#  OROMA_RAM_SCHED_JITTER=5            → ±Jitter in Sekunden
#  OROMA_RAM_SCHED_SELECTOR=best       → all|best|label:<k>
#  OROMA_RAM_SCHED_LIMIT=500           → max Episoden/Flush
#  OROMA_RAM_SCHED_PROMOTE=1           → gute Episoden in DB promoten
#  OROMA_RAM_SCHED_PRUNE=1             → RAM-Cache sanft aufräumen pro Lauf
#  OROMA_RAM_SCHED_EXPORT=1            → nach Export-Schwelle rules exportieren
#  OROMA_PE_EXPORT_EVERY_STEPS=500     → Steps-Schwelle je Sammellauf
#  OROMA_PE_MIN_N=3                    → Export: Mindest n
#  OROMA_PE_MIN_ABS_Q=0.15             → Export: Mindest |q|
#
#  OROMA_METRICS=1                     → einfache Metriken in DB loggen (sparsam)
#
# INTEGRATION (Beispiel in run_oroma.py)
# ──────────────────────────────────────
#  from mini_programs.universal_policy.ram_sched import RAMFlushScheduler
#  sched = RAMFlushScheduler.from_env(engine=my_engine)  # oder ohne engine → Auto-Adapter
#  sched.start()
#  ...
#  # Beim Shutdown:
#  sched.stop(); sched.join(timeout=5)
#
# ABHÄNGIGKEITEN
# ──────────────
#  • core.policy_engine   – bereits von dir übernommen
#  • mini_programs.universal_policy.ram_writer (RAM-first Puffer)
#  • optional mini_programs.universal_policy.adapter_universal (UniversalAdapter)
#  • optional core.ttt_adapter (Fallback)
#
# =============================================================================

from __future__ import annotations
import os
import sys
import time
import json
import random
import logging
import threading
from typing import Optional, Dict, Any
from core.log_guard import log_suppressed

# Pfad-Fallback
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

LOG = logging.getLogger("oroma.ram_sched")
if not LOG.handlers:
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    LOG.addHandler(_sh)
LOG.setLevel(logging.INFO)

# --- Imports (robust) ---------------------------------------------------------
try:
    from core.policy_engine import PolicyEngine
except Exception as e:
    raise RuntimeError(f"[ram_sched] policy_engine Importfehler: {e}")

# RAM-Writer
try:
    from mini_programs.universal_policy import ram_writer as RW
except Exception as e:
    raise RuntimeError(f"[ram_sched] ram_writer Importfehler: {e}")

# optionale DB-Metriken
try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "")
    if v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip() or default)
    except Exception:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "").strip() or default)
    except Exception:
        return default


def _load_adapter():
    mode = os.environ.get("OROMA_ADAPTER", "auto").strip().lower()
    # 1) Universal bevorzugt
    if mode in ("auto", "universal"):
        try:
            from mini_programs.universal_policy.adapter_universal import UniversalAdapter
            return UniversalAdapter()
        except Exception as e:
            if mode == "universal":
                LOG.warning("UniversalAdapter nicht verfügbar: %s", e)
    # 2) TTT-Fallback
    if mode in ("auto", "ttt"):
        try:
            from core.ttt_adapter import TTTAdapter
            return TTTAdapter()
        except Exception as e:
            if mode == "ttt":
                LOG.warning("TTTAdapter nicht verfügbar: %s", e)
    raise RuntimeError("Kein Adapter verfügbar (Universal/TTT). Installiere einen Adapter.")


class RAMFlushScheduler:
    """
    In-process Hintergrund-Worker: zyklisches RAM-Training + optionaler Export.
    Thread-sicher, über start/stop steuerbar.
    """

    def __init__(self,
                 engine: Optional[PolicyEngine] = None,
                 *,
                 interval_sec: int = 30,
                 jitter_sec: int = 5,
                 selector: str = "best",
                 limit: Optional[int] = 500,
                 promote_to_db: bool = True,
                 do_prune: bool = True,
                 auto_export: bool = True,
                 export_every_steps: int = 500,
                 export_min_n: int = 3,
                 export_min_abs_q: float = 0.15,
                 recover_tmpfs_on_start: bool = False,
                 metrics_enabled: bool = True,
                 max_backoff_sec: int = 300) -> None:

        self.interval_sec = max(1, int(interval_sec))
        self.jitter_sec = max(0, int(jitter_sec))
        self.selector = str(selector)
        self.limit = None if limit is None else max(1, int(limit))
        self.promote_to_db = bool(promote_to_db)
        self.do_prune = bool(do_prune)
        self.auto_export = bool(auto_export)
        self.export_every_steps = max(1, int(export_every_steps))
        self.export_min_n = max(1, int(export_min_n))
        self.export_min_abs_q = float(export_min_abs_q)
        self.recover_tmpfs_on_start = bool(recover_tmpfs_on_start)
        self.metrics_enabled = bool(metrics_enabled)
        self.max_backoff_sec = max(5, int(max_backoff_sec))

        # Engine/Adapter
        self.engine = engine
        if self.engine is None:
            adapter = _load_adapter()
            self.engine = PolicyEngine(adapter=adapter)

        # Threading
        self._thr: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running = False

        # Export-Akkumulator
        self._steps_since_export = 0

        # Backoff-State
        self._idle_runs = 0

    # ---------------- CONTROL ----------------

    def start(self) -> None:
        if self._running:
            return
        RW.init_dirs()

        if self.recover_tmpfs_on_start:
            try:
                n = RW.recover_from_tmpfs(None)
                LOG.info("[ram_sched] recover_from_tmpfs: %d", n)
            except Exception as e:
                LOG.debug("recover_from_tmpfs Fehler: %s", e)

        self._stop.clear()
        self._thr = threading.Thread(target=self._loop, name="oroma-ram-sched", daemon=True)
        self._thr.start()
        self._running = True
        LOG.info("[ram_sched] gestartet (interval=%ds, jitter=%ds, selector=%s, promote=%s, export=%s)",
                 self.interval_sec, self.jitter_sec, self.selector,
                 self.promote_to_db, self.auto_export)

    def stop(self) -> None:
        if not self._running:
            return
        self._stop.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thr:
            self._thr.join(timeout)
        self._running = False

    def is_running(self) -> bool:
        return self._running and not self._stop.is_set()

    # --------------- LOOP CORE ---------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Backoff/Jitter
            base = self.interval_sec
            if self._idle_runs > 0:
                back = min(self.max_backoff_sec, base * (2 ** min(self._idle_runs, 6)))
            else:
                back = base
            jitter = random.uniform(-self.jitter_sec, self.jitter_sec) if self.jitter_sec > 0 else 0.0
            sleep_for = max(1.0, back + jitter)
            self._stop.wait(sleep_for)
            if self._stop.is_set():
                break

            # Überschneidungsschutz
            if not self._lock.acquire(blocking=False):
                # Vorheriger Durchlauf noch aktiv
                continue

            try:
                self._run_once()
            except Exception as e:
                LOG.error("run_once Fehler: %s", e)
            finally:
                self._lock.release()

    def _run_once(self) -> None:
        t0 = time.time()

        # Trainieren aus RAM
        res = RW.flush(
            self.engine,
            selector=self.selector,
            limit=self.limit,
            promote_to_db=self.promote_to_db,
            db_origin=None,
            auto_export=False  # Export gesteuert durch Schwelle unten
        )

        trained_steps = int(res.get("trained_steps", 0))
        promoted = int(res.get("promoted", 0))
        kept = int(res.get("kept_in_ram", 0))
        dropped = int(res.get("dropped_from_ram", 0))

        self._steps_since_export += trained_steps

        # Idle/Backoff-Logik
        if trained_steps == 0 and promoted == 0:
            self._idle_runs += 1
        else:
            self._idle_runs = 0

        # Auto-Export, wenn Steps-Akkumulator die Schwelle erreicht
        exported = 0
        if self.auto_export and self._steps_since_export >= self.export_every_steps:
            try:
                exported = int(self.engine.export_archiv(min_n=self.export_min_n,
                                                         min_abs_q=self.export_min_abs_q))
            except Exception as e:
                LOG.debug("export_archiv Fehler: %s", e)
            self._steps_since_export = 0

        dur = time.time() - t0

        LOG.info("[ram_sched] flush: steps=%d promoted=%d kept=%d dropped=%d exported=%d idle_runs=%d dur=%.3fs",
                 trained_steps, promoted, kept, dropped, exported, self._idle_runs, dur)

        # Metriken (sparsam)
        if self.metrics_enabled and sql_manager:
            try:
                ts = int(time.time())
                if trained_steps:
                    sql_manager.insert_metric("ramflush.steps", float(trained_steps), ts)
                if exported:
                    sql_manager.insert_metric("ramflush.exported", float(exported), ts)
            except Exception as e:
                log_suppressed('mini_programs/universal_policy/ram_sched.py:311', exc=e, level=logging.WARNING)
                pass

    # --------------- BUILDERS ---------------

    @classmethod
    def from_env(cls, engine: Optional[PolicyEngine] = None) -> "RAMFlushScheduler":
        return cls(
            engine=engine,
            interval_sec=_env_int("OROMA_RAM_SCHED_INTERVAL", 30),
            jitter_sec=_env_int("OROMA_RAM_SCHED_JITTER", 5),
            selector=os.environ.get("OROMA_RAM_SCHED_SELECTOR", "best"),
            limit=None if os.environ.get("OROMA_RAM_SCHED_LIMIT", "").strip() == "" else _env_int("OROMA_RAM_SCHED_LIMIT", 500),
            promote_to_db=_env_bool("OROMA_RAM_SCHED_PROMOTE", True),
            do_prune=_env_bool("OROMA_RAM_SCHED_PRUNE", True),
            auto_export=_env_bool("OROMA_RAM_SCHED_EXPORT", True),
            export_every_steps=_env_int("OROMA_PE_EXPORT_EVERY_STEPS", 500),
            export_min_n=_env_int("OROMA_PE_MIN_N", 3),
            export_min_abs_q=_env_float("OROMA_PE_MIN_ABS_Q", 0.15),
            recover_tmpfs_on_start=_env_bool("OROMA_RAM_RECOVER_ON_START", False),
            metrics_enabled=_env_bool("OROMA_METRICS", True),
            max_backoff_sec=_env_int("OROMA_RAM_SCHED_MAX_BACKOFF", 300),
        )
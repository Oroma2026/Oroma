#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/oroma_orchestrator.py
# Projekt: ORÓMA – Zentraler Job-Orchestrator (Modus B: serielle Worker-Steuerung)
# Version: v1.1.6-vertical-proof-isolation
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Dieses Tool löst das klassische SQLite-Problem "database is locked" in
#   Multi-Process-Setups nicht durch mehr Retries, sondern durch bessere
#   Orchestrierung:
#
#     • ORÓMA hat viele systemd Timer (Dream, Stats, Energy, KPI, Policy, ...)
#     • Jede Unit startet einen eigenen Python-Prozess → mehrere Writer konkurrieren
#     • SQLite (auch mit WAL) erlaubt trotzdem nur einen Writer-Commit gleichzeitig
#
#   Der Orchestrator läuft als *ein* systemd Service (Daemon) und führt
#   die bekannten Worker-Jobs *seriell* aus. Damit sind "Write-Stürme" durch
#   gleichzeitige Timer-Starts praktisch eliminiert.
#
# Design-Prinzipien
# ────────────────
#   1) Produktions-Ready, Headless
#      - kein Qt/Wayland/X11
#      - nur stdlib (kein zusätzlicher Runtime-Dependency)
#
#   2) Nicht-destruktiv & kompatibel
#      - vorhandene systemd Units bleiben bestehen
#      - deren Services bekommen (per Patch) eine Condition:
#          ConditionPathExists=!/opt/ai/oroma/.use_orchestrator
#        → Wenn der Flag existiert, werden Timer zwar ausgelöst, die Services
#          werden aber übersprungen.
#
#   3) Serielle Ausführung + Lock-Guard
#      - globaler Lock via /tmp/oroma_orchestrator.lock
#      - pro Job: subprocess-Execution im bekannten Modus (wie systemd ExecStart)
#      - Zustand persistiert in state/orchestrator_state.json
#
# Aktivierung (Live-System)
# ─────────────────────────
#   1) Flag setzen:
#        sudo -u oroma touch /opt/ai/oroma/.use_orchestrator
#   2) Service installieren/aktivieren:
#        sudo cp systemd/oroma-orchestrator.service /etc/systemd/system/
#        sudo systemctl daemon-reload
#        sudo systemctl enable --now oroma-orchestrator.service
#
# Deaktivierung
# ─────────────
#   sudo rm -f /opt/ai/oroma/.use_orchestrator
#   sudo systemctl stop oroma-orchestrator.service
#   (Timer/Oneshots laufen dann wieder normal.)
#
# ENV
# ───
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_ORCH_TICK_SEC=5                 (Sleep zwischen Checks)
#   OROMA_ORCH_LOG_DIR=/opt/ai/oroma/logs
#   OROMA_ORCH_STATE=/opt/ai/oroma/state/orchestrator_state.json
#
#   Job-Feintuning:
#   OROMA_ORCH_ENABLE_TRAIN_SNAKE=1|0
#   OROMA_ORCH_ENABLE_DREAM=1|0
#   OROMA_ORCH_DREAM_REQUIRE_PHASE=1|0  (Default: 1; Dream nur wenn phase.json == DREAM)
#
#   Dream→Policy Auto-Mini-Write (Default-EIN-ENV-Gate, DBWriter-only):
#   OROMA_ORCH_ENABLE_DREAM_AUTO_MINI_WRITE=1|0        (Default: 1; bewusst default-ein, aber abschaltbar)
#   OROMA_ORCH_INT_DREAM_AUTO_MINI_WRITE=3600          (Default: 1h)
#   OROMA_ORCH_TIMEOUT_DREAM_AUTO_MINI_WRITE=120
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_NAMESPACE=game:snake3d
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_STATE_SCHEMA_PREFIX=snake3d:pro_v1
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_LIMIT=20
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_MAX=1
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_DAILY_CAP=2
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_COOLDOWN_S=3600
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_MIN_DIRECT_EVIDENCE=3
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_MIN_ABS_DELTA=0.0001
#   OROMA_ORCH_DREAM_AUTO_MINI_WRITE_CONFIRM=AUTO_DIRECT_STEP_CREDIT_ONLY
#
#   OROMA_ORCH_ENABLE_KPI=1|0
#   OROMA_ORCH_ENABLE_POLICY=1|0
#   OROMA_ORCH_ENABLE_EXPORTGATE=1|0
#   OROMA_ORCH_ENABLE_ARCHIVE=1|0
#   OROMA_ORCH_ENABLE_FORGETTING=1|0
#   OROMA_ORCH_ENABLE_GAP_MINER=1|0
#   OROMA_ORCH_ENABLE_TTT_ORACLE=1|0
#   OROMA_ORCH_ENABLE_TTT_DAILY_RUN=1|0
#   OROMA_ORCH_ENABLE_C4_DAILY_RUN=1|0
#   OROMA_ORCH_ENABLE_SNAKE_DAILY_RUN=1|0
#   OROMA_ORCH_ENABLE_SNAKE3D_DAILY_RUN=1|0      (Default: 1; Policy+Explore Daily)
#   OROMA_ORCH_ENABLE_PONG_DAILY_RUN=1|0
#     Hinweis v1.1.3: Pong erhält pro Daily-Lauf einen dynamischen --seed
#     (now_ts & 0xFFFFFFFF), damit Tagesläufe nicht dauerhaft identisch sind.
#   OROMA_ORCH_ENABLE_CHESS_DAILY_RUN=1|0          (Default ab 2026-06-27: 0; Legacy Chess1)
#   OROMA_ORCH_ENABLE_CHESS2_DAILY_RUN=1|0         (Default ab 2026-06-27: 0; Legacy 100+100 Batch)
#   OROMA_ORCH_ENABLE_CHESS_PRO_DAILY_RUN=1|0      (Default: 1; neuer professioneller ChessPro-Zielpfad)
#   OROMA_ORCH_ENABLE_CHESS2_SIDE_DAILY_RUN=1|0    (Default ab ChessPro: 0; Legacy-Vorstufe)
#   OROMA_ORCH_ENABLE_CHESS_POLICY_TRAIN=1|0       (Default ab 2026-06-27: 0; Legacy Chess1)
#   OROMA_ORCH_ENABLE_CHESS_POLICY_EXPORT=1|0      (Default ab 2026-06-27: 0; Legacy Chess1)
#   OROMA_ORCH_ENABLE_FLAPPY_DAILY_RUN=1|0
#   OROMA_ORCH_ENABLE_MEMORY_DAILY_RUN=1|0
#   OROMA_ORCH_ENABLE_MEMORYMAZE_DAILY_RUN=1|0      (Legacy MemoryMaze2033; Default: 0; Runner nicht mehr produktiv referenziert)
#   OROMA_ORCH_ENABLE_PTZ_ARENA_DAILY_RUN=1|0    (Default: 0; echte Hardware, nur manuell/ENV)
#   OROMA_ORCH_ENABLE_PTZ_TARGET_DAILY_RUN=1|0   (Default: 0; echte Hardware, nur manuell/ENV)
#   OROMA_ORCH_ENABLE_PTZ_COVERAGE_DAILY_RUN=1|0 (Default: 0; echte Hardware, nur manuell/ENV)
#
#   Synapses Bridge Materializer Stage B Mini (konservativ, DBWriter-only):
#   OROMA_ORCH_ENABLE_SYNAPSES_BRIDGE_MATERIALIZER=1|0  (Default: 1)
#   OROMA_ORCH_INT_SYNAPSES_BRIDGE_MATERIALIZER=21600  (Default: 6h)
#   OROMA_ORCH_TIMEOUT_SYNAPSES_BRIDGE_MATERIALIZER=120
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_WINDOW_SEC=604800
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_LIMIT_EDGES=50000
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_TOPK=25
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_MAX_BRIDGES=1
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_MAX_PER_DAY=3
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_DAY_WINDOW_SEC=86400
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_MIN_SCORE=0.70
#   OROMA_ORCH_SYNAPSES_BRIDGE_MAT_CONFIDENCE=0.14
#
#   Intervalle (Sekunden):
#   OROMA_ORCH_INT_STATS=120
#   OROMA_ORCH_INT_SOCIAL=120
#   OROMA_ORCH_INT_ENERGY=300
#   OROMA_ORCH_INT_TRAIN_SNAKE=300
#   OROMA_ORCH_INT_DREAM=1800
#   OROMA_ORCH_INT_GAP_MINER=600   (Default 10min; proaktives Gap-Mining)
#   OROMA_ORCH_TIMEOUT_GAP_MINER=90
#   OROMA_ORCH_GAP_MINER_MAX_RUNTIME_S=45
#   OROMA_ORCH_GAP_MINER_LIMIT_PER_KIND=50
#   OROMA_ORCH_GAP_MINER_MAX_INSERTS_PER_RUN=50
#   OROMA_ORCH_GAP_MINER_LE_ROW_SCAN_LIMIT=600
#   OROMA_ORCH_GAP_MINER_HU_ROW_SCAN_LIMIT=600
#   OROMA_ORCH_GAP_MINER_LC_ROW_SCAN_LIMIT=150
#   OROMA_ORCH_GAP_MINER_NIGHT_SWEEP_PASSES=60
#   OROMA_ORCH_GAP_MINER_NIGHT_MAX_RUNTIME_S=3000
#   OROMA_ORCH_GAP_MINER_NIGHT_LE_ROW_SCAN_LIMIT=3000
#   OROMA_ORCH_GAP_MINER_NIGHT_HU_ROW_SCAN_LIMIT=3000
#   OROMA_ORCH_GAP_MINER_NIGHT_LC_ROW_SCAN_LIMIT=3000

#   Timeouts (Sekunden):
#   OROMA_ORCH_TIMEOUT_STATS=60
#   OROMA_ORCH_TIMEOUT_SOCIAL=60
#   OROMA_ORCH_TIMEOUT_ENERGY=120
#   OROMA_ORCH_TIMEOUT_LEARNING_CACHE=180
#   OROMA_ORCH_TIMEOUT_PTZ_ATTENTION=10
#   OROMA_ORCH_TIMEOUT_DREAM=900
#   OROMA_ORCH_TIMEOUT_POLICY_TRAIN=900
#   OROMA_ORCH_TIMEOUT_POLICY_EXPORT=240
#   OROMA_ORCH_TIMEOUT_CHESS_POLICY_TRAIN=900
#   OROMA_ORCH_TIMEOUT_CHESS_POLICY_EXPORT=240
#   OROMA_ORCH_TIMEOUT_EXPORTGATE=900
#   OROMA_ORCH_TIMEOUT_ARCHIVE=2400
#   OROMA_ORCH_TIMEOUT_FORGETTING=2400
#
# Nightly (TicTacToe)
#   OROMA_ORCH_TTT_ORACLE_AT=03:17  (HH:MM in Europe/Berlin)
#   OROMA_ORCH_TTT_DAILY_AT=03:20   (HH:MM in Europe/Berlin)
#   OROMA_ORCH_C4_DAILY_AT=03:30    (HH:MM in Europe/Berlin)
#   OROMA_ORCH_SNAKE_DAILY_AT=03:35 (HH:MM in Europe/Berlin)
#   OROMA_ORCH_PONG_DAILY_AT=03:40  (HH:MM in Europe/Berlin)
#   OROMA_ORCH_CHESS_DAILY_AT=03:45 (HH:MM in Europe/Berlin; Legacy Chess1, Default disabled)
#   OROMA_ORCH_CHESS_PRO_WHITE_AT=03:55 (HH:MM in Europe/Berlin; 1. Partie, ORÓMA-Fokus Weiß)
#   OROMA_ORCH_CHESS_PRO_BLACK_AT=04:55 (HH:MM in Europe/Berlin; 2. Partie, ORÓMA-Fokus Schwarz, +1h)
#   OROMA_ORCH_CHESS_PRO_DEPTH=4
#   OROMA_ORCH_CHESS_PRO_MAX_PLIES=180
#   OROMA_ORCH_CHESS_PRO_TIME_BUDGET_MS=12000
#   OROMA_ORCH_CHESS_PRO_GAME_BUDGET_SEC=1680  (28min harte Obergrenze pro Partie)
#   OROMA_ORCH_CHESS_PRO_EPS=0.01
#   OROMA_ORCH_CHESS2_SIDE_WHITE_AT=03:55 (HH:MM in Europe/Berlin; Legacy-Vorstufe)
#   OROMA_ORCH_CHESS2_SIDE_BLACK_AT=04:55 (HH:MM in Europe/Berlin; Legacy-Vorstufe)
#   OROMA_ORCH_CHESS2_SIDE_GAMES=1 (pro Side-Lauf; Default total 2 Partien/Tag, falls Legacy aktiviert)
#   OROMA_ORCH_CHESS_POLICY_TRAIN_AT=03:47 (HH:MM in Europe/Berlin; Legacy Chess1, Default disabled)
#   OROMA_ORCH_CHESS_POLICY_EXPORT_AT=03:49 (HH:MM in Europe/Berlin; Legacy Chess1, Default disabled)
#   OROMA_ORCH_FLAPPY_DAILY_AT=03:50 (HH:MM in Europe/Berlin)
#   OROMA_ORCH_CTF_DAILY_AT=04:00   (HH:MM in Europe/Berlin)
#
# Hinweise
# ────────
#   • Der Orchestrator verhindert Konkurrenz zwischen *Worker*-Prozessen.
#     Die Engine (oroma.service) schreibt weiterhin parallel. Das ist OK:
#     wir reduzieren damit den größten Lock-Verursacher: parallele Timer.
#
#   • Dieser Modus (B) ist bewusst der erste Schritt. Ein späterer Modus (A)
#     "Single DB Writer Daemon" (IPC/Queue) kann darauf aufbauen.
#
# PATCH-HINWEIS 2026-07-07 / Dream→Policy Auto-Mini-Write Orchestrator-Gate v1
# ─────────────────────────────────────────────────────────────────────────────
#   Der Orchestrator kann nun die bereits validierte DreamWorker-Phase
#   adapter_auto_mini_write_gated seriell einplanen. Der Anschluss ist bewusst
#   als Default-EIN-ENV-Gate umgesetzt: Im Orchestrator ist der Job per Default
#   aktiv, bleibt aber ueber OROMA_ORCH_ENABLE_DREAM_AUTO_MINI_WRITE=0 sofort
#   abschaltbar. Der Subprozess bekommt harte produktive Limits (DBWriter-only,
#   Direct-Step-Credit-only, Ledger-Pflicht, MAX=1, DAILY_CAP=2, COOLDOWN=1h)
#   und schreibt niemals ohne die internen DreamWorker-Gates. Dadurch wird der
#   erste Automatikbetrieb ermoeglicht, ohne die zuvor getesteten Sicherheits-
#   barrieren aufzuweichen.
#
# PATCH-HINWEIS 2026-07-09 / Gap-Learning-Bridge Dry-Run v1
# ─────────────────────────────────────────────────────────
#   Der Orchestrator kann nun eine read-only Gap-Learning-Bridge starten.
#   Diese Bridge liest knowledge_gaps und policy_rules, schreibt aber keine DB,
#   keine policy_rules und keine Schemaänderung. Ergebnis ist ausschließlich
#   data/state/gap_learning_focus.json als Learning-Focus-Kandidatenliste für
#   spätere Explore-/Replay-/Dream-Verbraucher. Damit wird Gap→Learning erstmals
#   nutzbar, ohne Gaps fälschlich als direkte Rewards zu behandeln.
#
# PATCH-HINWEIS 2026-07-09 / Gap-Focus Consumer Read-Only v1
# ───────────────────────────────────────────────────────────
#   Der Orchestrator kann direkt nach bzw. neben der Gap-Learning-Bridge eine
#   reine Verbrauchersicht erzeugen: data/state/gap_focus_consumer.json. Diese
#   Sicht routet Fokus-Kandidaten in read-only Buckets für Explore, Replay,
#   Dream und Runner-Priorität. Sie startet keine Jobs, öffnet keine Datenbank
#   und schreibt keine Policy. Sie ist nur die maschinenlesbare Anschlussfläche,
#   damit spätere Verbraucher denselben Fokus sehen, aber weiterhin eigene Gates
#   für Evidenz, Review und Writes brauchen.
#
# PATCH-HINWEIS 2026-07-09 / Gap-Focus Shadow Plan Read-Only v1
# ───────────────────────────────────────────────────────────────
#   Der Orchestrator kann nun aus data/state/gap_focus_consumer.json einen
#   reinen Anschluss-/Shadow-Plan erzeugen: data/state/gap_focus_shadow_plan.json.
#   Diese Stufe ist bewusst noch kein echter Verbraucher: Sie startet weder
#   Replay noch Dream noch Runner, schreibt keine DB und keine Policy. Sie macht
#   lediglich sichtbar, welche Kandidaten spaeter in welchen Review-/Gate-Pfad
#   gehen duerften. Damit bleibt Gap→Learning kontrolliert und auditierbar.
#
# PATCH-HINWEIS 2026-07-10 / Gap Evidence Queue Review Dry-Run v1
# ─────────────────────────────────────────────────────────────────
#   Der Orchestrator kann nun die DBWriter-only Evidence Queue read-only
#   auswerten und data/state/gap_evidence_review.json erzeugen. Diese Review-
#   Sicht klassifiziert Queue-Kandidaten fuer spaetere Replay-/Dream-/Explore-
#   Gates, bleibt aber strikt ohne DB-Write, ohne policy_rules-Write und ohne
#   Runner-/Replay-/Dream-Start.
#
# PATCH-HINWEIS 2026-07-07 / Gap-Miner Orchestrator-Bounding v1
# ───────────────────────────────────────────────────────────────
#   Live-Logs zeigten wiederholte gap_miner.py-Timeouts nach 300s. Der
#   Orchestrator startet den Gap-Miner deshalb nun mit expliziten produktiven
#   Bounds und DBWriter-ENV: max_runtime_s, Limit pro Gap-Art, Insert-Budget,
#   Strict-Local-Writes und kurzer Gap-Lock-Retry. Das Tool bleibt optional,
#   aber wenn es aktiviert ist, darf es die serielle Orchestrator-Pipeline nicht
#   mehr minutenlang blockieren.
# =============================================================================

from __future__ import annotations

import argparse
import datetime as _dt
import errno
import fcntl
import json
import os
import random
import subprocess
import sys
import time
from typing import Dict, Any, Optional, List, Tuple

from core import execution_mode


def _env_bool(name: str, default: bool = True) -> bool:
    v = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    """Liest eine ENV als float robust.

    Beispiele:
      OROMA_ORCH_MEMORYMAZE_HYBRID_EPS=0.08
    """
    try:
        return float(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return float(default)


def _env_hhmm(name: str, default_hh: int, default_mm: int) -> Tuple[int, int]:
    """Parst eine HH:MM-ENV (Europe/Berlin) robust.

    Beispiele:
      OROMA_ORCH_TTT_ORACLE_AT=03:17
    """
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return int(default_hh), int(default_mm)
    try:
        if ":" not in raw:
            return int(default_hh), int(default_mm)
        hh_s, mm_s = raw.split(":", 1)
        hh = int(hh_s.strip())
        mm = int(mm_s.strip())
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return int(default_hh), int(default_mm)
        return hh, mm
    except Exception:
        return int(default_hh), int(default_mm)


def _now_ts() -> int:
    return int(time.time())


def _today_ymd(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now()
    return now.strftime("%Y-%m-%d")


def _month_ym(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now()
    return now.strftime("%Y-%m")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _read_phase(path: str) -> Dict[str, Any]:
    """Liest die aktuelle Phase-Datei robust.

    Rückgabe ist immer ein Dict. Bei Fehlern oder fehlender Datei wird ein leeres
    Dict geliefert, damit der Orchestrator sichtbar fail-open/fail-safe entscheiden
    kann, ohne selbst abzustürzen.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _phase_allows_dream(path: str) -> Tuple[bool, str]:
    """Entscheidet, ob der Orchestrator-Dream aktuell laufen darf.

    Erwartet eine phase.json aus run_oroma/CircadianController. Nur wenn dort die
    Phase explizit DREAM ist, wird True zurückgegeben. Alle anderen Fälle werden
    sichtbar mit einem Grundtext zurückgegeben.
    """
    data = _read_phase(path)
    phase = str(data.get("phase", "") or "").strip().upper()
    source = str(data.get("source", "") or "").strip() or "unknown"
    ts = int(data.get("ts", 0) or 0)
    if not data:
        return False, "phase file missing/invalid"
    if phase != "DREAM":
        return False, f"phase={phase or 'unknown'} source={source} ts={ts}"
    return True, f"phase=DREAM source={source} ts={ts}"


def _load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        # nicht blockieren, lieber neu starten
        return {}


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class _GlobalLock:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.fd = None

    def __enter__(self):
        self.fd = open(self.lock_path, "w")
        try:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise RuntimeError("orchestrator already running")
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.fd is not None:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
                self.fd.close()
        finally:
            self.fd = None


def _run_cmd(
    cmd: List[str],
    log_out: str,
    log_err: str,
    env: Dict[str, str],
    timeout_sec: Optional[int] = None,
) -> Tuple[int, float]:
    t0 = time.time()
    with open(log_out, "a", encoding="utf-8") as fo, open(log_err, "a", encoding="utf-8") as fe:
        fo.write(f"\n==== [ORCH] {time.strftime('%Y-%m-%d %H:%M:%S')} CMD: {' '.join(cmd)} ====\n")
        fo.flush()
        try:
            p = subprocess.run(
                cmd,
                stdout=fo,
                stderr=fe,
                env=env,
                cwd=env.get("OROMA_BASE") or None,
                timeout=(int(timeout_sec) if timeout_sec else None),
            )
            rc = int(p.returncode)
        except subprocess.TimeoutExpired:
            # Absolut kritisch im Orchestrator-Modus: Ein einzelner langer Job darf
            # niemals die gesamte Job-Pipeline (u.a. Energy/Stats) blockieren.
            # Wir fail-open und loggen sichtbar.
            fe.write(
                f"[ORCH] TIMEOUT after {timeout_sec}s: {' '.join(cmd)}\n"
            )
            fe.flush()
            rc = 124
        except Exception as e:
            fe.write(f"[ORCH] EXCEPTION while running {' '.join(cmd)}: {e!r}\n")
            fe.flush()
            rc = 99
    return rc, (time.time() - t0)


def _should_run_interval(state: Dict[str, Any], job: str, interval_sec: int, now_ts: int) -> bool:
    """Return True when an interval job is due.

    The orchestrator persists job timestamps across restarts (state file).
    After a reboot, the system clock can briefly jump backwards/forwards (NTP,
    RTC drift, timezone corrections). If a persisted last_ts is *in the future*
    relative to now_ts, the raw (now_ts-last_ts) would become negative and the
    job would be suppressed for a long time.

    We treat a future timestamp as invalid and reset it (fail-open) so periodic
    jobs (notably PTZ attention) do not stall silently after a reboot.
    """
    interval_sec = int(interval_sec)
    last_map = state.get("last_ts", {}) or {}
    last = int(last_map.get(job, 0) or 0)
    if interval_sec <= 0:
        return False

    # Clock skew guard: if last_ts is in the future, reset to 0 so the job runs.
    if last > (now_ts + 10):
        # Store a hint in state for observability; the loop will persist state.
        state.setdefault("notes", {})
        state["notes"][f"clock_skew:{job}"] = {
            "last_ts": last,
            "now_ts": now_ts,
            "ts": now_ts,
        }
        last_map[job] = 0
        state["last_ts"] = last_map
        return True

    if last <= 0:
        return True
    return (now_ts - last) >= interval_sec



def _should_run_interval_with_retry(
    state: Dict[str, Any],
    job: str,
    interval_sec: int,
    retry_sec: int,
    now_ts: int,
) -> bool:
    """Like _should_run_interval, but retries sooner after failures.

    Rationale:
      When we deploy a new job (e.g. link_probe Stage A) and it fails due to a
      missing file/table, the orchestrator would otherwise record last_ts and
      suppress the job for the full interval (e.g. 1h). For jobs that are safe
      and bounded, a shorter retry is preferable.

    Policy:
      - If last_rc[job] == 0: use interval_sec
      - Else: use min(interval_sec, retry_sec) (but never below 30s)
    """
    retry_sec = int(max(30, int(retry_sec)))
    last_rc_map = state.get("last_rc", {}) or {}
    last_rc = int(last_rc_map.get(job, 0) or 0)
    eff_interval = int(interval_sec) if last_rc == 0 else int(min(int(interval_sec), retry_sec))
    return _should_run_interval(state, job, eff_interval, now_ts)


def _mark_ran(state: Dict[str, Any], job: str, now_ts: int, rc: int, dur_s: float, timed_out: bool = False) -> None:
    state.setdefault("last_ts", {})[job] = int(now_ts)
    state.setdefault("last_rc", {})[job] = int(rc)
    state.setdefault("last_dur_s", {})[job] = float(dur_s)
    state.setdefault("last_timeout", {})[job] = bool(timed_out)


def _dream_state_path(base: str) -> str:
    env_path = str(os.getenv("OROMA_DREAM_STATE_PATH", "").strip() or "")
    if env_path:
        return env_path
    return os.path.join(base, "data", "state", "dream_worker_state.json")


def _load_dream_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dream_should_continue(state: Dict[str, Any]) -> bool:
    run_status = str(state.get("run_status", "") or "").strip().lower()
    return run_status == "budget_hit"


def _should_run_daily(state: Dict[str, Any], job: str, hh: int, mm: int, jitter_min: int, now: _dt.datetime) -> bool:
    # läuft max 1x pro Tag
    last_day = str(state.get("last_day", {}).get(job, "") or "")
    today = _today_ymd(now)
    if last_day == today:
        return False
    # Retry-Gate bei vorherigem Daily-Fail (z.B. Timeout/rc!=0): verhindert Busy-Loop pro Tick
    # Default: 30 Minuten, über ENV steuerbar.
    fail_ts = int(state.get("daily_fail_ts", {}).get(job, 0) or 0)
    if fail_ts > 0:
        retry_min = max(1, int(os.getenv("OROMA_ORCH_DAILY_RETRY_MIN", "30").strip() or "30"))
        if int(now.timestamp()) - fail_ts < retry_min * 60:
            return False


    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    # Jitter deterministisch pro Tag+Job (stabil über Restarts)
    seed = f"{today}:{job}"
    rnd = random.Random(seed)
    jitter = rnd.randint(0, max(0, int(jitter_min)))
    target = target + _dt.timedelta(minutes=jitter)

    return now >= target


def _mark_daily(state: Dict[str, Any], job: str, now: _dt.datetime) -> None:
    state.setdefault("last_day", {})[job] = _today_ymd(now)

def _mark_daily_fail(state: Dict[str, Any], job: str, now: _dt.datetime, rc: int, dur_s: float) -> None:
    # Daily-Fails werden NICHT als 'ran today' markiert.
    # Stattdessen merken wir den Fail-Timestamp, damit _should_run_daily nicht pro Tick busy-looped.
    ts = int(now.timestamp())
    state.setdefault("daily_fail_ts", {})[job] = ts
    state.setdefault("daily_fail_rc", {})[job] = int(rc)
    state.setdefault("daily_fail_dur_s", {})[job] = float(dur_s)
    state.setdefault("daily_fail_count", {})[job] = int(state.get("daily_fail_count", {}).get(job, 0) or 0) + 1


def _clear_daily_fail(state: Dict[str, Any], job: str) -> None:
    for k in ("daily_fail_ts", "daily_fail_rc", "daily_fail_dur_s", "daily_fail_count"):
        try:
            if job in state.get(k, {}):
                del state[k][job]
        except Exception:
            pass



def _should_run_monthly(state: Dict[str, Any], job: str, day: int, hh: int, mm: int, jitter_min: int, now: _dt.datetime) -> bool:
    # läuft max 1x pro Monat
    last_month = str(state.get("last_month", {}).get(job, "") or "")
    this_month = _month_ym(now)
    if last_month == this_month:
        return False

    if now.day != int(day):
        return False

    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    seed = f"{this_month}:{job}"
    rnd = random.Random(seed)
    jitter = rnd.randint(0, max(0, int(jitter_min)))
    target = target + _dt.timedelta(minutes=jitter)

    return now >= target


def _mark_monthly(state: Dict[str, Any], job: str, now: _dt.datetime) -> None:
    state.setdefault("last_month", {})[job] = _month_ym(now)


def _base_env(base: str) -> Dict[str, str]:
    e = dict(os.environ)
    e.setdefault("OROMA_BASE", base)
    e.setdefault("PYTHONPATH", base)
    e.setdefault("TZ", "Europe/Berlin")
    # Log dir
    log_dir = os.getenv("OROMA_ORCH_LOG_DIR", os.path.join(base, "logs"))
    e.setdefault("OROMA_LOG_DIR", log_dir)
    return e


def run_due_jobs(once: bool = False) -> int:
    base = os.getenv("OROMA_BASE", "/opt/ai/oroma")
    log_dir = os.getenv("OROMA_ORCH_LOG_DIR", os.path.join(base, "logs"))
    state_path = os.getenv("OROMA_ORCH_STATE", os.path.join(base, "state", "orchestrator_state.json"))
    tick = _env_int("OROMA_ORCH_TICK_SEC", 5)

    _ensure_dir(log_dir)
    _ensure_dir(os.path.dirname(state_path))

    log_out = os.path.join(log_dir, "orchestrator.out.log")
    log_err = os.path.join(log_dir, "orchestrator.err.log")

    env = _base_env(base)
    phase_path = os.getenv("OROMA_PHASE_PATH", os.path.join(base, "data", "state", "phase.json"))

    # Defensive defaults: falls im Flag-Parsing vorher eine Exception fliegt,
    # dürfen Interval-Jobs nicht mit NameError abstürzen.
    en_crossmodal_linker = False


    # enable flags
    en_train = _env_bool("OROMA_ORCH_ENABLE_TRAIN_SNAKE", True)
    en_dream = _env_bool("OROMA_ORCH_ENABLE_DREAM", True)
    dream_require_phase = _env_bool("OROMA_ORCH_DREAM_REQUIRE_PHASE", True)
    # Dream→Policy Auto-Mini-Write (Default-EIN-ENV-Gate):
    #   - Default bewusst EIN, weil die Schreibphase selbst weiterhin harte
    #     Gates besitzt (DBWriter, Confirm-Token, Ledger, Daily-Cap, Cooldown).
    #   - Sofort abschaltbar mit OROMA_ORCH_ENABLE_DREAM_AUTO_MINI_WRITE=0.
    #   - Separater Orchestrator-Job, damit normale Dream-Rotation und Auto-
    #     Mini-Write seriell, sichtbar und mit eigenem Timeout laufen.
    en_dream_auto_mini_write = _env_bool("OROMA_ORCH_ENABLE_DREAM_AUTO_MINI_WRITE", True)
    en_kpi = _env_bool("OROMA_ORCH_ENABLE_KPI", True)
    en_policy = _env_bool("OROMA_ORCH_ENABLE_POLICY", True)
    en_export = _env_bool("OROMA_ORCH_ENABLE_EXPORTGATE", True)
    en_arch = _env_bool("OROMA_ORCH_ENABLE_ARCHIVE", True)
    en_forget = _env_bool("OROMA_ORCH_ENABLE_FORGETTING", True)
    # Forgetting: History-Sampling (stats_points) – bewusst NICHT an offene UI gekoppelt
    en_forget_sample = _env_bool("OROMA_ORCH_ENABLE_FORGETTING_SAMPLE", True)
    en_crossmodal_linker = _env_bool("OROMA_ORCH_ENABLE_CROSSMODAL_LINKER", True)
    # Binding Stage A (Vision): measure repetition density in vision/token stream
    en_vision_binding_probe = _env_bool("OROMA_ORCH_ENABLE_VISION_BINDING_PROBE", True)
    # Compression Stage A: measure SnapChain redundancy + compressed_* materialization (ObjectGraph)
    en_compression_probe = _env_bool("OROMA_ORCH_ENABLE_COMPRESSION_PROBE", True)
    # ObjectGraph AutoMeta (compressed_* nodes): extract objects from scenegraphs into object_nodes/object_relations
    en_object_extractor = _env_bool("OROMA_ORCH_ENABLE_OBJECT_EXTRACTOR", True)
    # Compression Stage B (Materialize): create minimal compressed_* nodes in ObjectGraph from repeat signals
    en_compression_materializer = _env_bool("OROMA_ORCH_ENABLE_COMPRESSION_MATERIALIZER", True)
    # MetaSnap Indexer: DBWriter-safe materialization of compressed_* meta_snaps into ObjectGraph
    en_meta_snap_indexer = _env_bool("OROMA_ORCH_ENABLE_META_SNAP_INDEXER", True)
    # Synapses KPI Probe: summarize object_relations(relation="synaptic") into stats_points (Learning UI bridge)
    en_synapses_probe = _env_bool("OROMA_ORCH_ENABLE_SYNAPSES_PROBE", True)
    # Synapses Bridge Probe Stage A: measure-only bridge candidates between fragmented components.
    en_synapses_bridge_probe = _env_bool("OROMA_ORCH_ENABLE_SYNAPSES_BRIDGE_PROBE", True)
    # Synapses Bridge Materializer Stage B Mini:
    # schreibt streng limitiert relation="synaptic_bridge" auf Basis der zuvor
    # gemessenen mittelstarken Kontextanker. Dieser Job ist produktiv bewusst
    # konservativ: DBWriter-only, Tagesbudget, Min-Score und eigene Relation.
    en_synapses_bridge_materializer = _env_bool("OROMA_ORCH_ENABLE_SYNAPSES_BRIDGE_MATERIALIZER", True)
    # Synapses Origin Probe: measure Herkunft/Zusammensetzung synaptic-Kanten.
    en_synapses_origin_probe = _env_bool("OROMA_ORCH_ENABLE_SYNAPSES_ORIGIN_PROBE", True)
    en_gap_miner = _env_bool("OROMA_ORCH_ENABLE_GAP_MINER", False)
    # Gap-Miner Night Sweep: default EIN, aber nur als täglicher bounded Sweep.
    # Tagsüber bleibt gap_miner kurz; nachts arbeitet er Cursor-basiert Rückstände ab.
    en_gap_miner_night = _env_bool("OROMA_ORCH_ENABLE_GAP_MINER_NIGHT", True)
    en_ttt_oracle = _env_bool("OROMA_ORCH_ENABLE_TTT_ORACLE", True)
    en_ttt_daily_run = _env_bool("OROMA_ORCH_ENABLE_TTT_DAILY_RUN", True)
    en_c4_daily_run = _env_bool("OROMA_ORCH_ENABLE_C4_DAILY_RUN", True)
    en_snake_daily_run = _env_bool("OROMA_ORCH_ENABLE_SNAKE_DAILY_RUN", True)
    # Snake3D ist nach validiertem Template-Transfer ein regulärer
    # Policy+Explore-Daily-Runner. Die UI bleibt read-only; PTZ bleibt unberührt.
    en_snake3d_daily_run = _env_bool("OROMA_ORCH_ENABLE_SNAKE3D_DAILY_RUN", True)
    en_pong_daily_run = _env_bool("OROMA_ORCH_ENABLE_PONG_DAILY_RUN", True)
    en_tetris_daily_run = _env_bool("OROMA_ORCH_ENABLE_TETRIS_DAILY_RUN", True)
    en_chess_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS_DAILY_RUN", False)
    # Chess2 bleibt als Mobility-/Policy-Stack erhalten, ist aber ab ChessPro
    # nicht mehr der produktive Default-Tagespfad. Manuelle Reaktivierung bleibt
    # per ENV möglich.
    en_chess2_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS2_DAILY_RUN", False)
    # Neuer produktiver Chess-Zielpfad: ChessPro.
    # Exakt zwei ressourcenschonende Partien pro Tag, getrennt nach
    # ORÓMA-Fokus Weiß/Schwarz und mit 1 Stunde Abstand. Chess2-Side bleibt als
    # Legacy-Vorstufe manuell aktivierbar, ist aber nicht mehr Default.
    en_chess_pro_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS_PRO_DAILY_RUN", True)
    en_chess2_side_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS2_SIDE_DAILY_RUN", False)
    en_chess2_canon_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS2_CANON_DAILY_RUN", False)
    en_chess2_canon_coop_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS2_CANON_COOP_DAILY_RUN", False)
    en_chess2_canon_coop_king_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS2_CANON_COOP_KING_DAILY_RUN", False)
    en_chess2_canon_coop_king_territory_daily_run = _env_bool("OROMA_ORCH_ENABLE_CHESS2_CANON_COOP_KING_TERRITORY_DAILY_RUN", False)
    en_chess_policy_train = _env_bool("OROMA_ORCH_ENABLE_CHESS_POLICY_TRAIN", False)
    en_chess_policy_export = _env_bool("OROMA_ORCH_ENABLE_CHESS_POLICY_EXPORT", False)
    en_flappy_daily_run = _env_bool("OROMA_ORCH_ENABLE_FLAPPY_DAILY_RUN", True)
    # Daily: Memory (classic pairs) – headless, schnelle Episoden, lernt via policy_rules.
    en_memory_daily_run = _env_bool("OROMA_ORCH_ENABLE_MEMORY_DAILY_RUN", True)
    # Daily: Sudoku – mechanic_solved Constraint-Learning-Pfad.
    # Sudoku hat variable Puzzles, aber eine wiederverwendbare Regelmechanik;
    # deshalb wird Explore nach ausreichend Samples reduziert, nicht hart gestoppt.
    en_sudoku_daily_run = _env_bool("OROMA_ORCH_ENABLE_SUDOKU_DAILY_RUN", True)
    # MemoryMaze2033 ist ein Legacy-/Referenzexperiment und wird seit
    # memory:pro_v2 (mechanic_solved) und memorymaze_hybrid nicht mehr vom
    # produktiven Orchestrator gestartet. Die ENV bleibt nur als dokumentierter
    # Alt-Schalter erhalten; der automatische Runner-Block ist unten entfernt,
    # damit tools/memorymaze_daily_runner.py nach manueller Kontrolle gelöscht
    # werden kann, ohne den Nachtlauf zu beschädigen.
    en_memorymaze_daily_run = _env_bool("OROMA_ORCH_ENABLE_MEMORYMAZE_DAILY_RUN", False)
    # MemoryMaze Hybrid (PacMan-Maze + Memory-Blocker) – separates Spiel (nicht memory_maze2033).
    en_memorymaze_hybrid_daily_run = _env_bool("OROMA_ORCH_ENABLE_MEMORYMAZE_HYBRID_DAILY_RUN", True)
    en_hideseek_daily_run = _env_bool("OROMA_ORCH_ENABLE_HIDESEEK_DAILY_RUN", True)
    en_ctf_daily_run = _env_bool("OROMA_ORCH_ENABLE_CTF_DAILY_RUN", True)
    # PTZ-Games bewegen echte Hardware. Sie bleiben als manuelle UIs/Runner
    # erhalten, sind aber im produktiven Orchestrator fail-closed. Wer einen
    # aktiven PTZ-Game-Lauf wirklich möchte, muss ihn explizit per ENV-Gate
    # einschalten. PTZ Zoom Observe ist davon unabhängig und read-only.
    en_ptz_arena_daily_run = _env_bool("OROMA_ORCH_ENABLE_PTZ_ARENA_DAILY_RUN", False)
    en_ptz_target_daily_run = _env_bool("OROMA_ORCH_ENABLE_PTZ_TARGET_DAILY_RUN", False)
    # PTZ Coverage (Staubsauger-Sweep) bewegt ebenfalls echte Hardware; daher
    # defaultmäßig aus und nur explizit per ENV aktivierbar.
    en_ptz_coverage_daily_run = _env_bool("OROMA_ORCH_ENABLE_PTZ_COVERAGE_DAILY_RUN", False)
    en_learning_cache = _env_bool("OROMA_ORCH_ENABLE_LEARNING_CACHE", True)
    # Gap-Learning-Bridge Dry-Run: erzeugt nur data/state/gap_learning_focus.json.
    # Keine DB-Writes, keine policy_rules-Writes, keine Runner-Aktivierung.
    en_gap_learning_bridge = _env_bool("OROMA_ORCH_ENABLE_GAP_LEARNING_BRIDGE", True)
    # Gap-Focus Consumer Read-Only: erzeugt nur data/state/gap_focus_consumer.json.
    # Keine DB-Writes, keine policy_rules-Writes, keine Runner-/Replay-/Dream-Aktivierung.
    en_gap_focus_consumer = _env_bool("OROMA_ORCH_ENABLE_GAP_FOCUS_CONSUMER", True)
    # Gap-Focus Shadow Plan Read-Only: erzeugt nur data/state/gap_focus_shadow_plan.json.
    # Keine DB-Writes, keine policy_rules-Writes, keine Runner-/Replay-/Dream-Aktivierung.
    en_gap_focus_shadow_plan = _env_bool("OROMA_ORCH_ENABLE_GAP_FOCUS_SHADOW_PLAN", True)
    # Gap Evidence Queue Writer: erster sicherer DBWriter-only Write der Gap-Kette.
    # Schreibt nur deduplizierte Review-/Evidence-Requests in eigene Queue-Tabelle,
    # niemals policy_rules und startet keine Runner-/Replay-/Dream-Jobs.
    en_gap_evidence_queue = _env_bool("OROMA_ORCH_ENABLE_GAP_EVIDENCE_QUEUE", True)
    # Gap Evidence Queue Review Dry-Run: liest die Queue read-only und erzeugt
    # data/state/gap_evidence_review.json. Keine DB-/Policy-Writes, keine Starts.
    en_gap_evidence_review = _env_bool("OROMA_ORCH_ENABLE_GAP_EVIDENCE_REVIEW", True)
    # Gap Evidence Execution/Validation Dry-Run: prueft Review-Kandidaten
    # read-only gegen Queue und policy_rules und erzeugt
    # data/state/gap_evidence_validation.json. Keine DB-/Policy-Writes, keine Starts.
    en_gap_evidence_validation = _env_bool("OROMA_ORCH_ENABLE_GAP_EVIDENCE_VALIDATION", True)
    # Gap Policy Promotion Queue: schreibt validierte Kandidaten dedupliziert in
    # eine eigene Promotion-/Approval-Queue. Kein policy_rules-Write, keine Starts.
    en_gap_policy_promotion = _env_bool("OROMA_ORCH_ENABLE_GAP_POLICY_PROMOTION", True)
    # Gap Evidence Outcome Collector: sucht vorhandene direkte Outcomes fuer
    # Promotion-Kandidaten. Keine DB-/Policy-Writes, keine Runner-/Replay-/Dream-Starts.
    # Reine policy_rules-Statistik wird nur als Snapshot dokumentiert und nicht als
    # neuer Beweis fuer Policy-Mini-Writes verwendet.
    en_gap_evidence_outcome = _env_bool("OROMA_ORCH_ENABLE_GAP_EVIDENCE_OUTCOME", True)
    # Gap Targeted Evidence Probe: prueft wenige Promotion-Kandidaten gezielt auf
    # historische Adapter-Hinweise oder Bedarf fuer Replay-/Dream-Evidence.
    # Kein DB-Write, kein policy_rules-Write, keine Starts, kein Massenscan.
    # Default nach Live-Befund aus: der Probe ist fuer Auto-Betrieb zu schwer.
    en_gap_targeted_evidence_probe = _env_bool("OROMA_ORCH_ENABLE_GAP_TARGETED_EVIDENCE_PROBE", False)
    # Gap Targeted Replay Evidence Probe: nutzt explizite lokale Headless-Adapter
    # fuer wenige Kandidaten und erzeugt nur State-JSON mit Outcome-Vorschlaegen.
    # Kein globaler ReplayManager-Start, kein Runner, kein Dream, kein Policy-Write.
    # Default nach Live-Befund aus: der manuelle Probe ist korrekt, aber fuer
    # Auto-Betrieb aktuell zu langsam.
    en_gap_replay_evidence_probe = _env_bool("OROMA_ORCH_ENABLE_GAP_REPLAY_EVIDENCE_PROBE", False)
    en_snake_autonomous_acquisition = _env_bool("OROMA_ORCH_ENABLE_SNAKE_AUTONOMOUS_ACQUISITION", False)
    # Gap Evidence Outcome Queue Gate: uebernimmt fertige Replay-Probe-Outcomes
    # DBWriter-only in eine eigene Outcome-Queue. Kein policy_rules-Write.
    en_gap_evidence_outcome_queue = _env_bool("OROMA_ORCH_ENABLE_GAP_EVIDENCE_OUTCOME_QUEUE", True)
    # Gap Policy Mini-Write Gate: finales, fail-closed Policy-Gate mit Ledger.
    # Default bleibt policy-write-seitig aus. Selbst bei Aktivierung verlangt das
    # Tool echte Evidence-Outcome-Daten; reine Gap-Wahrscheinlichkeiten werden
    # nicht als Wissen in policy_rules geschrieben.
    en_gap_policy_mini_write = _env_bool("OROMA_ORCH_ENABLE_GAP_POLICY_MINI_WRITE", True)
    en_ptz_attention = _env_bool("OROMA_ORCH_ENABLE_PTZ_ATTENTION", False)

    # intervals
    int_stats = _env_int("OROMA_ORCH_INT_STATS", 120)
    int_social = _env_int("OROMA_ORCH_INT_SOCIAL", 120)
    int_energy = _env_int("OROMA_ORCH_INT_ENERGY", 300)
    int_learning_cache = _env_int("OROMA_ORCH_INT_LEARNING_CACHE", 7200)  # 2h
    int_gap_learning_bridge = _env_int("OROMA_ORCH_INT_GAP_LEARNING_BRIDGE", 1800)  # 30min, read-only/state-only
    gap_learning_bridge_max_runtime_s = _env_int("OROMA_ORCH_GAP_LEARNING_BRIDGE_MAX_RUNTIME_S", 30)
    gap_learning_bridge_limit_gaps = _env_int("OROMA_ORCH_GAP_LEARNING_BRIDGE_LIMIT_GAPS", 500)
    gap_learning_bridge_topk = _env_int("OROMA_ORCH_GAP_LEARNING_BRIDGE_TOPK", 25)
    int_gap_focus_consumer = _env_int("OROMA_ORCH_INT_GAP_FOCUS_CONSUMER", 1800)  # 30min, state-only Consumer-View
    gap_focus_consumer_topk = _env_int("OROMA_ORCH_GAP_FOCUS_CONSUMER_TOPK", 10)
    gap_focus_consumer_max_age_sec = _env_int("OROMA_ORCH_GAP_FOCUS_CONSUMER_MAX_AGE_SEC", 7200)
    int_gap_focus_shadow_plan = _env_int("OROMA_ORCH_INT_GAP_FOCUS_SHADOW_PLAN", 1800)  # 30min, read-only Shadow-Plan
    gap_focus_shadow_plan_topk = _env_int("OROMA_ORCH_GAP_FOCUS_SHADOW_PLAN_TOPK", 10)
    gap_focus_shadow_plan_max_age_sec = _env_int("OROMA_ORCH_GAP_FOCUS_SHADOW_PLAN_MAX_AGE_SEC", 7200)
    int_gap_evidence_queue = _env_int("OROMA_ORCH_INT_GAP_EVIDENCE_QUEUE", 1800)  # 30min, DBWriter-only Queue-Write
    gap_evidence_queue_topk = _env_int("OROMA_ORCH_GAP_EVIDENCE_QUEUE_TOPK", 10)
    gap_evidence_queue_max_age_sec = _env_int("OROMA_ORCH_GAP_EVIDENCE_QUEUE_MAX_AGE_SEC", 7200)
    int_gap_evidence_review = _env_int("OROMA_ORCH_INT_GAP_EVIDENCE_REVIEW", 1800)  # 30min, read-only Queue-Review
    gap_evidence_review_limit = _env_int("OROMA_ORCH_GAP_EVIDENCE_REVIEW_LIMIT", 200)
    gap_evidence_review_topk = _env_int("OROMA_ORCH_GAP_EVIDENCE_REVIEW_TOPK", 10)
    int_gap_evidence_validation = _env_int("OROMA_ORCH_INT_GAP_EVIDENCE_VALIDATION", 1800)  # 30min, read-only Validation
    gap_evidence_validation_limit = _env_int("OROMA_ORCH_GAP_EVIDENCE_VALIDATION_LIMIT", 200)
    gap_evidence_validation_topk = _env_int("OROMA_ORCH_GAP_EVIDENCE_VALIDATION_TOPK", 10)
    gap_evidence_validation_max_age_sec = _env_int("OROMA_ORCH_GAP_EVIDENCE_VALIDATION_MAX_AGE_SEC", 7200)
    int_gap_policy_promotion = _env_int("OROMA_ORCH_INT_GAP_POLICY_PROMOTION", 1800)  # 30min, DBWriter-only Promotion-Queue
    gap_policy_promotion_limit = _env_int("OROMA_ORCH_GAP_POLICY_PROMOTION_LIMIT", 200)
    gap_policy_promotion_topk = _env_int("OROMA_ORCH_GAP_POLICY_PROMOTION_TOPK", 10)
    gap_policy_promotion_max_age_sec = _env_int("OROMA_ORCH_GAP_POLICY_PROMOTION_MAX_AGE_SEC", 7200)
    int_gap_evidence_outcome = _env_int("OROMA_ORCH_INT_GAP_EVIDENCE_OUTCOME", 1800)  # 30min, read-only Outcome Collector
    gap_evidence_outcome_limit = _env_int("OROMA_ORCH_GAP_EVIDENCE_OUTCOME_LIMIT", 50)
    gap_evidence_outcome_topk = _env_int("OROMA_ORCH_GAP_EVIDENCE_OUTCOME_TOPK", 10)
    gap_evidence_outcome_lookback_sec = _env_int("OROMA_ORCH_GAP_EVIDENCE_OUTCOME_LOOKBACK_SEC", 1209600)
    int_gap_targeted_evidence_probe = _env_int("OROMA_ORCH_INT_GAP_TARGETED_EVIDENCE_PROBE", 1800)  # 30min, bounded targeted probe
    gap_targeted_evidence_probe_limit = _env_int("OROMA_ORCH_GAP_TARGETED_EVIDENCE_PROBE_LIMIT", 3)
    gap_targeted_evidence_probe_topk = _env_int("OROMA_ORCH_GAP_TARGETED_EVIDENCE_PROBE_TOPK", 3)
    gap_targeted_evidence_probe_scan_limit = _env_int("OROMA_ORCH_GAP_TARGETED_EVIDENCE_PROBE_SCAN_LIMIT", 5000)
    int_gap_replay_evidence_probe = _env_int("OROMA_ORCH_INT_GAP_REPLAY_EVIDENCE_PROBE", 1800)  # 30min, state-only local replay adapter
    int_snake_autonomous_acquisition = _env_int("OROMA_ORCH_INT_SNAKE_AUTONOMOUS_ACQUISITION", 300)
    gap_replay_evidence_probe_limit = _env_int("OROMA_ORCH_GAP_REPLAY_EVIDENCE_PROBE_LIMIT", 3)
    gap_replay_evidence_probe_topk = _env_int("OROMA_ORCH_GAP_REPLAY_EVIDENCE_PROBE_TOPK", 3)
    gap_replay_evidence_probe_horizon = _env_int("OROMA_ORCH_GAP_REPLAY_EVIDENCE_PROBE_HORIZON_STEPS", 80)
    int_gap_evidence_outcome_queue = _env_int("OROMA_ORCH_INT_GAP_EVIDENCE_OUTCOME_QUEUE", 1800)  # 30min, DBWriter-only Outcome-Queue
    gap_evidence_outcome_queue_limit = _env_int("OROMA_ORCH_GAP_EVIDENCE_OUTCOME_QUEUE_LIMIT", 10)
    gap_evidence_outcome_queue_topk = _env_int("OROMA_ORCH_GAP_EVIDENCE_OUTCOME_QUEUE_TOPK", 10)
    gap_evidence_outcome_queue_min_confidence = _env_float("OROMA_ORCH_GAP_EVIDENCE_OUTCOME_QUEUE_MIN_CONFIDENCE", 0.50)
    int_gap_policy_mini_write = _env_int("OROMA_ORCH_INT_GAP_POLICY_MINI_WRITE", 1800)  # 30min, fail-closed Policy-Gate status
    gap_policy_mini_write_limit = _env_int("OROMA_ORCH_GAP_POLICY_MINI_WRITE_LIMIT", 50)
    gap_policy_mini_write_topk = _env_int("OROMA_ORCH_GAP_POLICY_MINI_WRITE_TOPK", 10)
    gap_policy_mini_write_max_writes = _env_int("OROMA_ORCH_GAP_POLICY_MINI_WRITE_MAX_WRITES", 1)
    # Default: 6 Stunden (Produktiv-Kompromiss)
    int_forget_sample = _env_int("OROMA_ORCH_INT_FORGETTING_SAMPLE", 21600)  # 6h
    int_crossmodal_linker = _env_int("OROMA_ORCH_INT_CROSSMODAL_LINKER", 3600)  # 1h (Binding/Crossmodal: schnelleres Feedback)
    # Binding Stage A (Vision): repetition density probe for vision/token stream
    int_vision_binding_probe = _env_int("OROMA_ORCH_INT_VISION_BINDING_PROBE", 3600)  # 1h (low cost)
    to_vision_binding_probe = _env_int("OROMA_ORCH_TIMEOUT_VISION_BINDING_PROBE", 45)
    # Compression Stage A (Measure-only): redundancy/materialization probe (nightly default)
    int_compression_probe = _env_int("OROMA_ORCH_INT_COMPRESSION_PROBE", 86400)  # 24h
    to_compression_probe = _env_int("OROMA_ORCH_TIMEOUT_COMPRESSION_PROBE", 60)
    # ObjectExtractor (AutoMeta -> object_nodes, incl. compressed_*); nightly default
    int_object_extractor = _env_int("OROMA_ORCH_INT_OBJECT_EXTRACTOR", 86400)  # 24h
    to_object_extractor = _env_int("OROMA_ORCH_TIMEOUT_OBJECT_EXTRACTOR", 180)
    # Limit how many newest scenegraphs are processed per run (avoid DB spikes)
    object_extractor_max_graphs = _env_int("OROMA_ORCH_OBJECT_EXTRACTOR_MAX_GRAPHS", 25)
    # Compression Materializer Stage B (nightly default)
    int_compression_materializer = _env_int("OROMA_ORCH_INT_COMPRESSION_MATERIALIZER", 86400)  # 24h
    to_compression_materializer = _env_int("OROMA_ORCH_TIMEOUT_COMPRESSION_MATERIALIZER", 120)
    comp_mat_topk = _env_int("OROMA_ORCH_COMP_MAT_TOPK", 10)
    comp_mat_min_repeat = _env_int("OROMA_ORCH_COMP_MAT_MIN_REPEAT", 5)
    comp_mat_limit_chains = _env_int("OROMA_ORCH_COMP_MAT_LIMIT_CHAINS", 5000)
    # MetaSnap Indexer (nightly default)
    int_meta_snap_indexer = _env_int("OROMA_ORCH_INT_META_SNAP_INDEXER", 86400)  # 24h
    to_meta_snap_indexer = _env_int("OROMA_ORCH_TIMEOUT_META_SNAP_INDEXER", 600)
    meta_snap_indexer_max_n = _env_int("OROMA_ORCH_META_SNAP_INDEXER_MAX_N", 250)
    # Gates for compressed_* MetaSnap materialization.
    # Wichtig: Diese Werte wurden bereits beim Runner-Aufruf verwendet, waren in
    # der Orchestrator-Baseline aber nicht initialisiert. Dadurch konnte der
    # meta_snap_indexer-Job beim ersten Lauf mit NameError abbrechen, obwohl
    # .env.systemd korrekt geladen wurde. Die ORCH_* Namen bleiben bewusst die
    # führende systemd-/Orchestrator-Konfiguration; der Runner bekommt sie als
    # explizite CLI-Argumente und benötigt dadurch keinen direkten ENV-Fallback.
    meta_snap_indexer_min_score = _env_float("OROMA_ORCH_META_SNAP_INDEXER_MIN_SCORE", 0.0)
    meta_snap_indexer_budget_per_day = _env_int("OROMA_ORCH_META_SNAP_INDEXER_BUDGET_PER_DAY", 0)
    # Synapses probe defaults (hourly; lightweight read-only + stats_points write)
    int_synapses_probe = _env_int("OROMA_ORCH_INT_SYNAPSES_PROBE", 3600)
    to_synapses_probe = _env_int("OROMA_ORCH_TIMEOUT_SYNAPSES_PROBE", 60)
    synapses_limit_edges = _env_int("OROMA_ORCH_SYNAPSES_LIMIT_EDGES", 50000)
    synapses_window_sec_7d = _env_int("OROMA_ORCH_SYNAPSES_WINDOW_SEC_7D", 604800)
    # Synapses Bridge Probe Stage A (measure-only):
    # Kandidaten für Brücken zwischen fragmentierten Synapsen-Komponenten werden
    # nur gemessen und in stats_points/state JSON sichtbar gemacht. Es werden
    # in Stage A ausdrücklich keine object_relations geschrieben.
    int_synapses_bridge_probe = _env_int("OROMA_ORCH_INT_SYNAPSES_BRIDGE_PROBE", 3600)
    to_synapses_bridge_probe = _env_int("OROMA_ORCH_TIMEOUT_SYNAPSES_BRIDGE_PROBE", 60)
    synapses_bridge_window_sec = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_WINDOW_SEC", 604800)
    synapses_bridge_limit_edges = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_LIMIT_EDGES", 50000)
    synapses_bridge_topk = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_TOPK", 25)

    # Synapses Bridge Materializer Stage B Mini:
    # Dieser Job materialisiert nur bereits plausible Brückenkandidaten als
    # eigene Relation "synaptic_bridge". Er verändert keine bestehenden
    # synaptic-Kanten und bleibt über Tagesbudget + Score-Gate streng begrenzt.
    int_synapses_bridge_materializer = _env_int("OROMA_ORCH_INT_SYNAPSES_BRIDGE_MATERIALIZER", 21600)
    to_synapses_bridge_materializer = _env_int("OROMA_ORCH_TIMEOUT_SYNAPSES_BRIDGE_MATERIALIZER", 120)
    synapses_bridge_mat_window_sec = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_WINDOW_SEC", 604800)
    synapses_bridge_mat_limit_edges = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_LIMIT_EDGES", 50000)
    synapses_bridge_mat_topk = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_TOPK", 25)
    synapses_bridge_mat_max_bridges = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_MAX_BRIDGES", 1)
    synapses_bridge_mat_max_per_day = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_MAX_PER_DAY", 3)
    synapses_bridge_mat_day_window_sec = _env_int("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_DAY_WINDOW_SEC", 86400)
    synapses_bridge_mat_min_score = _env_float("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_MIN_SCORE", 0.70)
    synapses_bridge_mat_confidence = _env_float("OROMA_ORCH_SYNAPSES_BRIDGE_MAT_CONFIDENCE", 0.14)
    # Synapses Origin Probe (measure-only):
    # Diagnostiziert, ob synaptic-Kanten nur event↔event-Cooccurrence-Inseln sind
    # oder ob bereits non-event/source_scene/notes-Kontext als Bridge-Evidenz vorliegt.
    int_synapses_origin_probe = _env_int("OROMA_ORCH_INT_SYNAPSES_ORIGIN_PROBE", 21600)
    to_synapses_origin_probe = _env_int("OROMA_ORCH_TIMEOUT_SYNAPSES_ORIGIN_PROBE", 90)
    synapses_origin_window_sec = _env_int("OROMA_ORCH_SYNAPSES_ORIGIN_WINDOW_SEC", 604800)
    synapses_origin_limit_edges = _env_int("OROMA_ORCH_SYNAPSES_ORIGIN_LIMIT_EDGES", 50000)
    synapses_origin_topk = _env_int("OROMA_ORCH_SYNAPSES_ORIGIN_TOPK", 25)
    int_train = _env_int("OROMA_ORCH_INT_TRAIN_SNAKE", 300)
    int_dream = _env_int("OROMA_ORCH_INT_DREAM", 1800)
    int_dream_auto_mini_write = _env_int("OROMA_ORCH_INT_DREAM_AUTO_MINI_WRITE", 3600)
    int_gap_miner = _env_int("OROMA_ORCH_INT_GAP_MINER", 600)
    # Gap-Miner Tagbetrieb: sehr kurz und rotations-/cursorbasiert.
    # Low-Evidence und High-Uncertainty dürfen kleine Fenster prüfen;
    # Logic-Conflict ist auf grossen ORÓMA-DBs besonders teuer und bleibt
    # tagsüber bewusst stark gedrosselt. Der Nacht-Sweep darf breiter arbeiten.
    gap_miner_max_runtime_s = _env_int("OROMA_ORCH_GAP_MINER_MAX_RUNTIME_S", 45)
    gap_miner_limit_per_kind = _env_int("OROMA_ORCH_GAP_MINER_LIMIT_PER_KIND", 50)
    gap_miner_max_inserts = _env_int("OROMA_ORCH_GAP_MINER_MAX_INSERTS_PER_RUN", 50)
    gap_miner_hu_row_scan_limit = _env_int("OROMA_ORCH_GAP_MINER_HU_ROW_SCAN_LIMIT", 600)
    gap_miner_le_row_scan_limit = _env_int("OROMA_ORCH_GAP_MINER_LE_ROW_SCAN_LIMIT", 600)
    gap_miner_lc_row_scan_limit = _env_int("OROMA_ORCH_GAP_MINER_LC_ROW_SCAN_LIMIT", 150)

    # Gap-Miner Nachtbetrieb: mehrere kleine bounded Slices in einem langen Fenster.
    # Ziel: Rückstände nachts abarbeiten, ohne tagsüber TIMEOUTs/DB-Locks zu erzeugen.
    gap_miner_night_hh, gap_miner_night_mm = _env_hhmm("OROMA_ORCH_GAP_MINER_NIGHT_AT", 2, 35)
    gap_miner_night_max_runtime_s = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_MAX_RUNTIME_S", 3000)
    gap_miner_night_limit_per_kind = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_LIMIT_PER_KIND", 500)
    gap_miner_night_max_inserts = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_MAX_INSERTS_PER_RUN", 1000)
    gap_miner_night_sweep_passes = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_SWEEP_PASSES", 60)
    gap_miner_night_hu_row_scan_limit = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_HU_ROW_SCAN_LIMIT", 3000)
    gap_miner_night_le_row_scan_limit = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_LE_ROW_SCAN_LIMIT", 3000)
    gap_miner_night_lc_row_scan_limit = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_LC_ROW_SCAN_LIMIT", 3000)
    gap_miner_night_slice_runtime_s = _env_int("OROMA_ORCH_GAP_MINER_NIGHT_SLICE_RUNTIME_S", 120)
    int_ptz_attention = _env_int("OROMA_ORCH_INT_PTZ_ATTENTION", 2)

    # ---------------------------------------------------------------------
    # TIMEOUTS (Sekunden)
    # ---------------------------------------------------------------------
    # Warum:
    #   Im Orchestrator-Modus laufen Jobs seriell. Wenn ein einzelner Job
    #   (z.B. dream_worker oder ein Export) unerwartet lange hängt, würde er
    #   dadurch *alle* nachfolgenden Jobs blockieren – inklusive ENERGY.
    #
    #   Genau das führt in der Learning-UI zu "energy_cache_stale" und leeren
    #   Top-Relations, obwohl relation_energy und object_relations vorhanden sind.
    #
    #   Wir setzen deshalb konservative Defaults und erlauben Override per ENV.
    # ---------------------------------------------------------------------
    to_stats = _env_int("OROMA_ORCH_TIMEOUT_STATS", 60)
    to_social = _env_int("OROMA_ORCH_TIMEOUT_SOCIAL", 60)
    # ---------------------------------------------------------------------
    # PTZ-Attention Timeout
    # ---------------------------------------------------------------------
    # In der Praxis kann core.ptz_attention_loop --once je nach Kamera-Backend,
    # DeviceHub-Status und DB-Contention >10s benötigen (z.B. initiale
    # Hub-Initialisierung / v4l2-Query / kurze Lock-Retries).
    #
    # Ein zu aggressiver Default (10s) führt dann zu wiederholten Orchestrator-
    # TIMEOUTs und damit zu "PTZ ist instabil" in den Logs, obwohl der Loop
    # grundsätzlich funktioniert.
    #
    # Daher: Default hochsetzen, weiterhin über ENV steuerbar.
    to_ptz_attention = _env_int("OROMA_ORCH_TIMEOUT_PTZ_ATTENTION", 25)
    to_energy = _env_int("OROMA_ORCH_TIMEOUT_ENERGY", 120)
    to_learning_cache = _env_int("OROMA_ORCH_TIMEOUT_LEARNING_CACHE", 180)
    to_gap_learning_bridge = _env_int("OROMA_ORCH_TIMEOUT_GAP_LEARNING_BRIDGE", 45)
    to_gap_focus_consumer = _env_int("OROMA_ORCH_TIMEOUT_GAP_FOCUS_CONSUMER", 20)
    to_gap_focus_shadow_plan = _env_int("OROMA_ORCH_TIMEOUT_GAP_FOCUS_SHADOW_PLAN", 20)
    to_gap_evidence_queue = _env_int("OROMA_ORCH_TIMEOUT_GAP_EVIDENCE_QUEUE", 30)
    to_gap_evidence_review = _env_int("OROMA_ORCH_TIMEOUT_GAP_EVIDENCE_REVIEW", 20)
    to_gap_evidence_validation = _env_int("OROMA_ORCH_TIMEOUT_GAP_EVIDENCE_VALIDATION", 20)
    to_gap_policy_promotion = _env_int("OROMA_ORCH_TIMEOUT_GAP_POLICY_PROMOTION", 30)
    to_gap_evidence_outcome = _env_int("OROMA_ORCH_TIMEOUT_GAP_EVIDENCE_OUTCOME", 20)
    to_gap_targeted_evidence_probe = _env_int("OROMA_ORCH_TIMEOUT_GAP_TARGETED_EVIDENCE_PROBE", 30)
    to_gap_replay_evidence_probe = _env_int("OROMA_ORCH_TIMEOUT_GAP_REPLAY_EVIDENCE_PROBE", 30)
    to_snake_autonomous_acquisition = _env_int("OROMA_ORCH_TIMEOUT_SNAKE_AUTONOMOUS_ACQUISITION", 240)
    to_gap_evidence_outcome_queue = _env_int("OROMA_ORCH_TIMEOUT_GAP_EVIDENCE_OUTCOME_QUEUE", 30)
    to_gap_policy_mini_write = _env_int("OROMA_ORCH_TIMEOUT_GAP_POLICY_MINI_WRITE", 20)
    to_forget_sample = _env_int("OROMA_ORCH_TIMEOUT_FORGETTING_SAMPLE", 60)
    to_crossmodal_linker = _env_int("OROMA_ORCH_TIMEOUT_CROSSMODAL_LINKER", 180)
    to_train_snake = _env_int("OROMA_ORCH_TIMEOUT_TRAIN_SNAKE", 900)
    to_dream = _env_int("OROMA_ORCH_TIMEOUT_DREAM", 900)
    to_dream_auto_mini_write = _env_int("OROMA_ORCH_TIMEOUT_DREAM_AUTO_MINI_WRITE", 120)
    to_gap_miner = _env_int("OROMA_ORCH_TIMEOUT_GAP_MINER", 90)
    to_gap_miner_night = _env_int("OROMA_ORCH_TIMEOUT_GAP_MINER_NIGHT", 3600)
    to_kpi = _env_int("OROMA_ORCH_TIMEOUT_KPI", 900)
    to_policy_train = _env_int("OROMA_ORCH_TIMEOUT_POLICY_TRAIN", 900)
    to_policy_export = _env_int("OROMA_ORCH_TIMEOUT_POLICY_EXPORT", 240)
    to_ttt_oracle = _env_int("OROMA_ORCH_TIMEOUT_TTT_ORACLE", 900)
    to_ttt_daily_run = _env_int("OROMA_ORCH_TIMEOUT_TTT_DAILY_RUN", 1800)
    to_c4_daily_run = _env_int("OROMA_ORCH_TIMEOUT_C4_DAILY_RUN", 1800)
    to_snake_daily_run = _env_int("OROMA_ORCH_TIMEOUT_SNAKE_DAILY_RUN", 1800)
    to_snake3d_daily_run = _env_int("OROMA_ORCH_TIMEOUT_SNAKE3D_DAILY_RUN", 900)
    to_pong_daily_run = _env_int("OROMA_ORCH_TIMEOUT_PONG_DAILY_RUN", 1800)
    to_tetris_daily_run = _env_int("OROMA_ORCH_TIMEOUT_TETRIS_DAILY_RUN", 1800)
    to_chess_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS_DAILY_RUN", 2400)
    to_chess2_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS2_DAILY_RUN", 3000)
    to_chess_pro_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS_PRO_DAILY_RUN", 2100)
    to_chess2_side_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS2_SIDE_DAILY_RUN", to_chess2_daily_run)
    to_chess2_canon_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS2_CANON_DAILY_RUN", 3000)
    to_chess2_canon_coop_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS2_CANON_COOP_DAILY_RUN", 3000)
    to_chess2_canon_coop_king_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS2_CANON_COOP_KING_DAILY_RUN", 3000)
    to_chess2_canon_coop_king_territory_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CHESS2_CANON_COOP_KING_TERRITORY_DAILY_RUN", 3000)
    to_chess_policy_train = _env_int("OROMA_ORCH_TIMEOUT_CHESS_POLICY_TRAIN", 900)
    to_chess_policy_export = _env_int("OROMA_ORCH_TIMEOUT_CHESS_POLICY_EXPORT", 240)
    to_flappy_daily_run = _env_int("OROMA_ORCH_TIMEOUT_FLAPPY_DAILY_RUN", 1800)
    to_memory_daily_run = _env_int("OROMA_ORCH_TIMEOUT_MEMORY_DAILY_RUN", 1200)
    to_sudoku_daily_run = _env_int("OROMA_ORCH_TIMEOUT_SUDOKU_DAILY_RUN", 1200)
    to_memorymaze_daily_run = _env_int("OROMA_ORCH_TIMEOUT_MEMORYMAZE_DAILY_RUN", 2400)
    to_memorymaze_hybrid_daily_run = _env_int("OROMA_ORCH_TIMEOUT_MEMORYMAZE_HYBRID_DAILY_RUN", 3600)
    to_hideseek_daily_run = _env_int("OROMA_ORCH_TIMEOUT_HIDESEEK_DAILY_RUN", 1800)
    to_ctf_daily_run = _env_int("OROMA_ORCH_TIMEOUT_CTF_DAILY_RUN", 1800)
    # Daily: PTZ Arena (real hardware) – default höher, da episoden länger dauern können
    to_ptz_arena_daily_run = _env_int("OROMA_ORCH_TIMEOUT_PTZ_ARENA_DAILY_RUN", 3600)
    to_ptz_target_daily_run = _env_int("OROMA_ORCH_TIMEOUT_PTZ_TARGET_DAILY_RUN", 3600)
    to_ptz_coverage_daily_run = _env_int("OROMA_ORCH_TIMEOUT_PTZ_COVERAGE_DAILY_RUN", 3600)
    to_exportgate = _env_int("OROMA_ORCH_TIMEOUT_EXPORTGATE", 900)
    to_archive = _env_int("OROMA_ORCH_TIMEOUT_ARCHIVE", 2400)
    to_forgetting = _env_int("OROMA_ORCH_TIMEOUT_FORGETTING", 2400)

    # daily jitter (minutes)
    jitter_daily = _env_int("OROMA_ORCH_DAILY_JITTER_MIN", 30)
    jitter_monthly = _env_int("OROMA_ORCH_MONTHLY_JITTER_MIN", 120)

    # Nightly: TicTacToe Oracle (Minimax → rules)
    ttt_oracle_hh, ttt_oracle_mm = _env_hhmm("OROMA_ORCH_TTT_ORACLE_AT", 3, 17)
    # Daily: TicTacToe self-play batches (policy + explore)
    ttt_daily_hh, ttt_daily_mm = _env_hhmm("OROMA_ORCH_TTT_DAILY_AT", 3, 20)

    # Daily: Connect4 self-play batches (policy + explore)
    # NOTE (Ops): bewusst nach 04:00 Uhr geplant.
    # Siehe auch "Daily: Memory" weiter unten – gleicher Grund (DB-Writer-Contention um ~03:xx).
    c4_daily_hh, c4_daily_mm = _env_hhmm("OROMA_ORCH_C4_DAILY_AT", 4, 30)
    # Daily: Snake self-play batches (policy + explore)
    snake_daily_hh, snake_daily_mm = _env_hhmm("OROMA_ORCH_SNAKE_DAILY_AT", 3, 35)

    # Daily: Snake3D Template-Transfer (explore-only, kein Policy-Spielmodus).
    # Default bewusst nach Sudoku: kleine Evidenzprobe ohne frühe DBWriter-Spitzen.
    snake3d_daily_hh, snake3d_daily_mm = _env_hhmm("OROMA_ORCH_SNAKE3D_DAILY_AT", 5, 55)

    # Daily: Pong self-play batches (policy + explore)
    pong_daily_hh, pong_daily_mm = _env_hhmm("OROMA_ORCH_PONG_DAILY_AT", 3, 40)

    # Daily: Tetris batches (policy + explore) – headless, keine UI-Abhängigkeit
    tetris_daily_hh, tetris_daily_mm = _env_hhmm("OROMA_ORCH_TETRIS_DAILY_AT", 3, 42)

    # Daily: Memory (classic pairs)
    # NOTE (Ops): bewusst nach 04:00 Uhr geplant.
    # Hintergrund: in der Praxis gab es gelegentlich kurze SQLite-Writer-Contention um ~03:xx
    # (Dream/Replay/AgentLoop/WAL-Checkpoint/Backups). Memory/Connect4 triggerten zusätzlich
    # sporadisch Gap-Emits (UniversalPolicy→core.gaps) → Lock-Retry → unnötige Latenz.
    memory_daily_hh, memory_daily_mm = _env_hhmm("OROMA_ORCH_MEMORY_DAILY_AT", 4, 40)

    # Daily: Sudoku (mechanic_solved) – nach Memory, damit beide
    # Constraint-/Positionsspiele nicht um dieselbe frühe DBWriter-Phase konkurrieren.
    sudoku_daily_hh, sudoku_daily_mm = _env_hhmm("OROMA_ORCH_SUDOKU_DAILY_AT", 5, 35)

    # Daily: Chess self-play batches (policy + explore)
    chess_daily_hh, chess_daily_mm = _env_hhmm("OROMA_ORCH_CHESS_DAILY_AT", 3, 45)
    # Daily: Chess2 self-play (mobility-native Parallel-Stack). Standard bewusst aus,
    # damit das System erst nach expliziter Aktivierung zusätzliche Nachtlast erzeugt.
    chess2_daily_hh, chess2_daily_mm = _env_hhmm("OROMA_ORCH_CHESS2_DAILY_AT", 3, 55)
    chess_pro_white_hh, chess_pro_white_mm = _env_hhmm("OROMA_ORCH_CHESS_PRO_WHITE_AT", 3, 55)
    chess_pro_black_hh, chess_pro_black_mm = _env_hhmm("OROMA_ORCH_CHESS_PRO_BLACK_AT", 4, 55)
    chess2_side_white_hh, chess2_side_white_mm = _env_hhmm("OROMA_ORCH_CHESS2_SIDE_WHITE_AT", 3, 55)
    chess2_side_black_hh, chess2_side_black_mm = _env_hhmm("OROMA_ORCH_CHESS2_SIDE_BLACK_AT", 4, 55)
    chess2_canon_daily_hh, chess2_canon_daily_mm = _env_hhmm("OROMA_ORCH_CHESS2_CANON_DAILY_AT", 4, 25)
    chess2_canon_coop_daily_hh, chess2_canon_coop_daily_mm = _env_hhmm("OROMA_ORCH_CHESS2_CANON_COOP_DAILY_AT", 4, 45)
    chess2_canon_coop_king_daily_hh, chess2_canon_coop_king_daily_mm = _env_hhmm("OROMA_ORCH_CHESS2_CANON_COOP_KING_DAILY_AT", 5, 5)
    chess2_canon_coop_king_territory_daily_hh, chess2_canon_coop_king_territory_daily_mm = _env_hhmm("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_DAILY_AT", 5, 25)

    chess_policy_train_hh, chess_policy_train_mm = _env_hhmm("OROMA_ORCH_CHESS_POLICY_TRAIN_AT", 3, 47)
    chess_policy_export_hh, chess_policy_export_mm = _env_hhmm("OROMA_ORCH_CHESS_POLICY_EXPORT_AT", 3, 49)

    # Daily: FlappyBird self-play batches (policy + explore)
    flappy_daily_hh, flappy_daily_mm = _env_hhmm("OROMA_ORCH_FLAPPY_DAILY_AT", 3, 50)

    # Daily: Memory Maze 2033 self-play batches (policy + explore)
    memorymaze_daily_hh, memorymaze_daily_mm = _env_hhmm("OROMA_ORCH_MEMORYMAZE_DAILY_AT", 4, 5)

    # Daily: MemoryMaze Hybrid (normal + hard_p3)
    memorymaze_hybrid_daily_hh, memorymaze_hybrid_daily_mm = _env_hhmm("OROMA_ORCH_MEMORYMAZE_HYBRID_DAILY_AT", 4, 12)

    # Daily: Hide&Seek self-play batches (policy + explore)
    hideseek_daily_hh, hideseek_daily_mm = _env_hhmm("OROMA_ORCH_HIDESEEK_DAILY_AT", 3, 55)

    # Daily: Capture The Flag (CTF) self-play batches (policy + explore)
    ctf_daily_hh, ctf_daily_mm = _env_hhmm("OROMA_ORCH_CTF_DAILY_AT", 4, 0)

    # Daily: PTZ Arena (Policy + Explore) – bewusst nach Games (morgens)
    ptz_arena_daily_hh, ptz_arena_daily_mm = _env_hhmm("OROMA_ORCH_PTZ_ARENA_DAILY_AT", 4, 10)
    ptz_target_daily_hh, ptz_target_daily_mm = _env_hhmm("OROMA_ORCH_PTZ_TARGET_DAILY_AT", 4, 20)
    ptz_coverage_daily_hh, ptz_coverage_daily_mm = _env_hhmm("OROMA_ORCH_PTZ_COVERAGE_DAILY_AT", 4, 30)

    # Batch sizes (TicTacToe)
    ttt_policy_games = _env_int("OROMA_ORCH_TTT_POLICY_GAMES", 100)
    ttt_explore_games = _env_int("OROMA_ORCH_TTT_EXPLORE_GAMES", 100)

    # Batch sizes (Connect4)
    c4_policy_games = _env_int("OROMA_ORCH_C4_POLICY_GAMES", 100)
    c4_explore_games = _env_int("OROMA_ORCH_C4_EXPLORE_GAMES", 100)

    # Batch sizes (Snake)
    snake_policy_games = _env_int("OROMA_ORCH_SNAKE_POLICY_GAMES", 100)
    snake_explore_games = _env_int("OROMA_ORCH_SNAKE_EXPLORE_GAMES", 100)

    # Batch sizes (Snake3D) – professional Policy+Explore.
    snake3d_policy_games = _env_int("OROMA_ORCH_SNAKE3D_POLICY_GAMES", 5)
    snake3d_explore_games = _env_int("OROMA_ORCH_SNAKE3D_EXPLORE_GAMES", 5)
    snake3d_size = _env_int("OROMA_ORCH_SNAKE3D_SIZE", 6)
    snake3d_max_steps = _env_int("OROMA_ORCH_SNAKE3D_MAX_STEPS", 350)
    snake3d_target_len = _env_int("OROMA_ORCH_SNAKE3D_TARGET_LEN", 20)

    # Batch sizes (Pong)
    pong_policy_games = _env_int("OROMA_ORCH_PONG_POLICY_GAMES", 100)
    pong_explore_games = _env_int("OROMA_ORCH_PONG_EXPLORE_GAMES", 100)

    # Batch sizes (Tetris)
    tetris_policy_games = _env_int("OROMA_ORCH_TETRIS_POLICY_GAMES", 100)
    tetris_explore_games = _env_int("OROMA_ORCH_TETRIS_EXPLORE_GAMES", 100)

    # Batch sizes (Memory)
    memory_policy_games = _env_int("OROMA_ORCH_MEMORY_POLICY_GAMES", 100)
    memory_explore_games = _env_int("OROMA_ORCH_MEMORY_EXPLORE_GAMES", 100)
    memory_size = _env_int("OROMA_ORCH_MEMORY_SIZE", 4)
    memory_max_turns = _env_int("OROMA_ORCH_MEMORY_MAX_TURNS", 220)
    memory_eps = _env_float("OROMA_ORCH_MEMORY_EPS", 0.08)

    # Batch sizes (Sudoku)
    # Default bewusst kleiner als Arcade-Spiele: Sudoku ist CPU-intensiver im
    # Puzzle-Generator/Uniqueness-Check und lernt Techniken statt Rohzustände.
    sudoku_policy_games = _env_int("OROMA_ORCH_SUDOKU_POLICY_GAMES", 10)
    sudoku_explore_games = _env_int("OROMA_ORCH_SUDOKU_EXPLORE_GAMES", 10)
    sudoku_difficulty = os.getenv("OROMA_ORCH_SUDOKU_DIFFICULTY", "medium")
    sudoku_eps = _env_float("OROMA_ORCH_SUDOKU_EPS", 0.08)

    # Batch sizes (Chess)
    chess_policy_games = _env_int("OROMA_ORCH_CHESS_POLICY_GAMES", 100)
    chess_explore_games = _env_int("OROMA_ORCH_CHESS_EXPLORE_GAMES", 100)

    # Parameter (ChessPro Zielpfad)
    chess_pro_depth = _env_int("OROMA_ORCH_CHESS_PRO_DEPTH", 4)
    chess_pro_max_plies = _env_int("OROMA_ORCH_CHESS_PRO_MAX_PLIES", 180)
    chess_pro_time_budget_ms = _env_int("OROMA_ORCH_CHESS_PRO_TIME_BUDGET_MS", 12000)
    chess_pro_game_budget_sec = _env_int("OROMA_ORCH_CHESS_PRO_GAME_BUDGET_SEC", 1680)
    chess_pro_eps = _env_float("OROMA_ORCH_CHESS_PRO_EPS", 0.01)

    # Batch sizes / Parameter (Chess2)
    chess2_policy_games = _env_int("OROMA_ORCH_CHESS2_POLICY_GAMES", 100)
    chess2_explore_games = _env_int("OROMA_ORCH_CHESS2_EXPLORE_GAMES", 100)
    chess2_max_plies = _env_int("OROMA_ORCH_CHESS2_MAX_PLIES", 180)
    chess2_eps = _env_float("OROMA_ORCH_CHESS2_EPS", 0.08)
    chess2_eps_white = _env_float("OROMA_ORCH_CHESS2_EPS_WHITE", chess2_eps)
    chess2_eps_black = _env_float("OROMA_ORCH_CHESS2_EPS_BLACK", chess2_eps)
    chess2_explore_moves_white = _env_int("OROMA_ORCH_CHESS2_EXPLORE_MOVES_WHITE", 2)
    chess2_explore_moves_black = _env_int("OROMA_ORCH_CHESS2_EXPLORE_MOVES_BLACK", 3)
    chess2_side_games = _env_int("OROMA_ORCH_CHESS2_SIDE_GAMES", 1)
    chess2_side_max_plies = _env_int("OROMA_ORCH_CHESS2_SIDE_MAX_PLIES", chess2_max_plies)
    chess2_side_eps = _env_float("OROMA_ORCH_CHESS2_SIDE_EPS", chess2_eps)
    chess2_side_eps_white = _env_float("OROMA_ORCH_CHESS2_SIDE_EPS_WHITE", chess2_eps_white)
    chess2_side_eps_black = _env_float("OROMA_ORCH_CHESS2_SIDE_EPS_BLACK", chess2_eps_black)
    chess2_side_explore_moves_white = _env_int("OROMA_ORCH_CHESS2_SIDE_EXPLORE_MOVES_WHITE", chess2_explore_moves_white)
    chess2_side_explore_moves_black = _env_int("OROMA_ORCH_CHESS2_SIDE_EXPLORE_MOVES_BLACK", chess2_explore_moves_black)
    chess2_canon_policy_games = _env_int("OROMA_ORCH_CHESS2_CANON_POLICY_GAMES", chess2_policy_games)
    chess2_canon_explore_games = _env_int("OROMA_ORCH_CHESS2_CANON_EXPLORE_GAMES", chess2_explore_games)
    chess2_canon_max_plies = _env_int("OROMA_ORCH_CHESS2_CANON_MAX_PLIES", chess2_max_plies)
    chess2_canon_eps = _env_float("OROMA_ORCH_CHESS2_CANON_EPS", chess2_eps)
    chess2_canon_eps_white = _env_float("OROMA_ORCH_CHESS2_CANON_EPS_WHITE", chess2_canon_eps)
    chess2_canon_eps_black = _env_float("OROMA_ORCH_CHESS2_CANON_EPS_BLACK", chess2_canon_eps)
    chess2_canon_explore_moves_white = _env_int("OROMA_ORCH_CHESS2_CANON_EXPLORE_MOVES_WHITE", chess2_explore_moves_white)
    chess2_canon_explore_moves_black = _env_int("OROMA_ORCH_CHESS2_CANON_EXPLORE_MOVES_BLACK", chess2_explore_moves_black)
    chess2_canon_coop_king_territory_policy_games = _env_int("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_POLICY_GAMES", chess2_canon_policy_games)
    chess2_canon_coop_king_territory_explore_games = _env_int("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EXPLORE_GAMES", chess2_canon_explore_games)
    chess2_canon_coop_king_territory_max_plies = _env_int("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_MAX_PLIES", chess2_canon_max_plies)
    chess2_canon_coop_king_territory_eps = _env_float("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EPS", chess2_canon_eps)
    chess2_canon_coop_king_territory_eps_white = _env_float("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EPS_WHITE", chess2_canon_coop_king_territory_eps)
    chess2_canon_coop_king_territory_eps_black = _env_float("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EPS_BLACK", chess2_canon_coop_king_territory_eps)
    chess2_canon_coop_king_territory_explore_moves_white = _env_int("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EXPLORE_MOVES_WHITE", chess2_canon_explore_moves_white)
    chess2_canon_coop_king_territory_explore_moves_black = _env_int("OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EXPLORE_MOVES_BLACK", chess2_canon_explore_moves_black)

    # Batch sizes (Flappy)
    flappy_policy_games = _env_int("OROMA_ORCH_FLAPPY_POLICY_GAMES", 100)
    flappy_explore_games = _env_int("OROMA_ORCH_FLAPPY_EXPLORE_GAMES", 100)

    # Batch sizes (Memory Maze 2033)
    memorymaze_policy_games = _env_int("OROMA_ORCH_MEMORYMAZE_POLICY_GAMES", 100)
    memorymaze_explore_games = _env_int("OROMA_ORCH_MEMORYMAZE_EXPLORE_GAMES", 100)
    memorymaze_mem_size = os.environ.get("OROMA_ORCH_MEMORYMAZE_MEM_SIZE", "4x4")
    memorymaze_max_turns = _env_int("OROMA_ORCH_MEMORYMAZE_MAX_TURNS", 220)

    # Batch sizes (MemoryMaze Hybrid) – Normal + Hard-P3 getrennt
    mmzh_map = os.environ.get("OROMA_ORCH_MEMORYMAZE_HYBRID_MAP", "sym")
    mmzh_eps = _env_float("OROMA_ORCH_MEMORYMAZE_HYBRID_EPS", 0.08)
    mmzh_max_steps = _env_int("OROMA_ORCH_MEMORYMAZE_HYBRID_MAX_STEPS", 900)
    mmzh_normal_policy_games = _env_int("OROMA_ORCH_MEMORYMAZE_HYBRID_NORMAL_POLICY_GAMES", 80)
    mmzh_normal_explore_games = _env_int("OROMA_ORCH_MEMORYMAZE_HYBRID_NORMAL_EXPLORE_GAMES", 80)
    mmzh_hard_policy_games = _env_int("OROMA_ORCH_MEMORYMAZE_HYBRID_HARD_POLICY_GAMES", 20)
    mmzh_hard_explore_games = _env_int("OROMA_ORCH_MEMORYMAZE_HYBRID_HARD_EXPLORE_GAMES", 20)

    # Batch sizes / runtime (Hide&Seek pro_v2 daily runner)
    hideseek_policy_games = _env_int("OROMA_ORCH_HIDESEEK_POLICY_GAMES", 100)
    hideseek_explore_games = _env_int("OROMA_ORCH_HIDESEEK_EXPLORE_GAMES", 100)
    hideseek_max_steps = _env_int("OROMA_ORCH_HIDESEEK_MAX_STEPS", _env_int("OROMA_HIDESEEK_MAX_STEPS", 400))
    hideseek_namespace = str(os.environ.get("OROMA_HIDESEEK_POLICY_NAMESPACE", "game:hideseek") or "game:hideseek")

    # Batch sizes (CTF)
    ctf_policy_games = _env_int("OROMA_ORCH_CTF_POLICY_GAMES", 100)
    ctf_explore_games = _env_int("OROMA_ORCH_CTF_EXPLORE_GAMES", 100)

    # Dream→Policy Auto-Mini-Write Defaults (Default-EIN-ENV-Gate)
    # ---------------------------------------------------------------------
    # Diese Werte werden nur an den Subprozess core.dream_worker weitergereicht.
    # Die eigentliche Schreibentscheidung bleibt in adapter_auto_mini_write_gated
    # und nutzt DBWriter-only + Ledger + Direct-Step-Credit-only.
    dream_auto_mini_write_namespace = str(os.getenv("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_NAMESPACE", "game:snake3d") or "game:snake3d").strip()
    dream_auto_mini_write_schema = str(os.getenv("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_STATE_SCHEMA_PREFIX", "snake3d:pro_v1") or "snake3d:pro_v1").strip()
    dream_auto_mini_write_limit = max(1, min(_env_int("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_LIMIT", 20), 500))
    dream_auto_mini_write_max = max(1, min(_env_int("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_MAX", 1), 2))
    dream_auto_mini_write_daily_cap = max(0, min(_env_int("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_DAILY_CAP", 2), 50))
    dream_auto_mini_write_cooldown_s = max(0, min(_env_int("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_COOLDOWN_S", 3600), 86400))
    dream_auto_mini_write_min_direct = max(1, min(_env_int("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_MIN_DIRECT_EVIDENCE", 3), 1000))
    dream_auto_mini_write_min_abs_delta = max(0.0, min(_env_float("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_MIN_ABS_DELTA", 0.0001), 1.0))
    dream_auto_mini_write_confirm = str(os.getenv("OROMA_ORCH_DREAM_AUTO_MINI_WRITE_CONFIRM", "AUTO_DIRECT_STEP_CREDIT_ONLY") or "AUTO_DIRECT_STEP_CREDIT_ONLY").strip()

    # Batch sizes (PTZ Arena)
    # NOTE: PTZ ist echte Hardware. 100×60steps×250ms ≈ 25min pro Batch.
    # Daher Default bewusst klein halten; per ENV kann man hochdrehen.
    ptz_arena_policy_games = _env_int("OROMA_ORCH_PTZ_ARENA_POLICY_GAMES", 10)
    ptz_arena_explore_games = _env_int("OROMA_ORCH_PTZ_ARENA_EXPLORE_GAMES", 10)
    ptz_target_policy_games = _env_int("OROMA_ORCH_PTZ_TARGET_POLICY_GAMES", 10)
    ptz_target_explore_games = _env_int("OROMA_ORCH_PTZ_TARGET_EXPLORE_GAMES", 10)
    # Coverage darf klein bleiben, weil max_steps real-time ist.
    ptz_coverage_policy_games = _env_int("OROMA_ORCH_PTZ_COVERAGE_POLICY_GAMES", 20)
    ptz_coverage_explore_games = _env_int("OROMA_ORCH_PTZ_COVERAGE_EXPLORE_GAMES", 20)

    state = _load_state(state_path)
    execution_status = execution_mode.execution_mode_status()
    state["execution_mode"] = execution_status
    state.setdefault("legacy_jobs_suppressed", {})
    _atomic_write_json(state_path, state)

    # helper: run job and store state
    def _run(
        job: str,
        cmd: List[str],
        timeout_sec: Optional[int] = None,
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, float]:
        """Führt einen Job seriell aus und persistiert den Orchestrator-Status.

        Warum env_overrides:
          Für einzelne Jobs – aktuell besonders Chess2 – wollen wir produktive
          Defaults erzwingen, ohne globale Prozess-ENV oder andere Jobs zu
          beeinflussen. Konkret darf Chess2 im Orchestrator NICHT bei jedem Lauf
          wieder Bootstrap aus altem game:chess anwerfen, sobald die Shell-ENV
          des Daemons oder ein manuell gesetzter Systemd-Override das aktiviert.

          Deshalb erlauben wir pro Job gezielte ENV-Overrides, die nur für genau
          diesen Subprozess gelten.
        """
        nonlocal state
        now_ts = _now_ts()
        run_env = dict(env)
        if env_overrides:
            for k, v in dict(env_overrides).items():
                run_env[str(k)] = str(v)
        rc, dur = _run_cmd(cmd, log_out=log_out, log_err=log_err, env=run_env, timeout_sec=timeout_sec)
        _mark_ran(state, job, now_ts, rc, dur, timed_out=(rc == 124))
        _atomic_write_json(state_path, state)
        return int(rc), float(dur)

    # main loop
    while True:
        now_ts = _now_ts()
        now = _dt.datetime.now()

        # If we detected clock skew (last_ts in the future) inside _should_run_interval,
        # surface it visibly in orchestrator.err.log once, then clear the note.
        try:
            notes = state.get("notes", {}) or {}
            skew_keys = [k for k in notes.keys() if isinstance(k, str) and k.startswith("clock_skew:")]
            if skew_keys:
                with open(log_err, "a", encoding="utf-8") as fe:
                    for k in sorted(skew_keys):
                        fe.write(f"\n[ORCH] WARN: clock skew detected; resetting last_ts for job '{k.split(':',1)[1]}'\n")
                        try:
                            del notes[k]
                        except Exception:
                            pass
                state["notes"] = notes
                _atomic_write_json(state_path, state)
        except Exception:
            # Never fail the loop due to logging issues.
            pass

        # Interval-Jobs (kurz, häufig)
        try:
            # Stats-Fast-Path:
            #   Seit dem Stats-Event-/Delta-Umbau laeuft der regulaere, haeufige
            #   Stats-Pfad bewusst NUR noch ueber tools/stats_event_aggregator.py.
            #   tools/stats_snapshot.py ist KEIN Fast-Path mehr und wird im
            #   Orchestrator absichtlich nicht regelmaessig eingeplant.
            #   Snapshot/Rebuild/Reparatur laeuft separat ueber einen dedizierten
            #   Repair-/Audit-Pfad (z. B. systemd Timer) deutlich seltener.
            if _should_run_interval(state, "stats", int_stats, now_ts):
                _run("stats", [sys.executable, os.path.join(base, "tools", "stats_event_aggregator.py"), "--once"], timeout_sec=to_stats)

            if en_synapses_probe and _should_run_interval(state, "synapses_probe", int_synapses_probe, now_ts):
                _run(
                    "synapses_probe",
                    [sys.executable, os.path.join(base, "tools", "synapses_probe.py"), "--once"],
                    timeout_sec=to_synapses_probe,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_SYNAPSES_LIMIT_EDGES": str(int(synapses_limit_edges)),
                        "OROMA_SYNAPSES_WINDOW_SEC_7D": str(int(synapses_window_sec_7d)),
                    },
                )

            if en_synapses_bridge_probe and _should_run_interval(state, "synapses_bridge_probe", int_synapses_bridge_probe, now_ts):
                # Synapses Bridge Stage A: measure-only candidate discovery.
                # Schreibt ausschließlich stats_points in stats.db und ein State-JSON;
                # keine object_relations, keine neuen Tabellen, keine Materialisierung.
                _run(
                    "synapses_bridge_probe",
                    [sys.executable, os.path.join(base, "tools", "synapses_bridge_probe.py"),
                     "--once",
                     "--window-sec", str(int(synapses_bridge_window_sec))],
                    timeout_sec=to_synapses_bridge_probe,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_SYNAPSES_BRIDGE_WINDOW_SEC": str(int(synapses_bridge_window_sec)),
                        "OROMA_SYNAPSES_BRIDGE_LIMIT_EDGES": str(int(synapses_bridge_limit_edges)),
                        "OROMA_SYNAPSES_BRIDGE_TOPK": str(int(synapses_bridge_topk)),
                    },
                )

            if en_synapses_bridge_materializer and _should_run_interval(state, "synapses_bridge_materializer", int_synapses_bridge_materializer, now_ts):
                # Synapses Bridge Materializer Stage B Mini:
                # materialisiert nur streng geprüfte Kandidaten als relation="synaptic_bridge".
                # Wichtig für die ORÓMA-DB-Disziplin:
                #   - DBWriter wird pro Subprozess erzwungen.
                #   - Es wird NICHT in relation="synaptic" geschrieben.
                #   - --max-bridges und --max-bridges-per-day begrenzen das Wachstum.
                #   - Das Tool selbst dedupliziert Komponentenpaare und schreibt Stats.
                _run(
                    "synapses_bridge_materializer",
                    [sys.executable, os.path.join(base, "tools", "synapses_bridge_materializer.py"),
                     "--once",
                     "--materialize",
                     "--window-sec", str(int(synapses_bridge_mat_window_sec)),
                     "--limit-edges", str(int(synapses_bridge_mat_limit_edges)),
                     "--topk", str(int(synapses_bridge_mat_topk)),
                     "--max-bridges", str(int(synapses_bridge_mat_max_bridges)),
                     "--max-bridges-per-day", str(int(synapses_bridge_mat_max_per_day)),
                     "--day-window-sec", str(int(synapses_bridge_mat_day_window_sec)),
                     "--min-score", str(float(synapses_bridge_mat_min_score)),
                     "--confidence", str(float(synapses_bridge_mat_confidence))],
                    timeout_sec=to_synapses_bridge_materializer,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_SYNAPSES_BRIDGE_MAT_WINDOW_SEC": str(int(synapses_bridge_mat_window_sec)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_LIMIT_EDGES": str(int(synapses_bridge_mat_limit_edges)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_TOPK": str(int(synapses_bridge_mat_topk)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_MAX_EDGES": str(int(synapses_bridge_mat_max_bridges)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_MAX_PER_DAY": str(int(synapses_bridge_mat_max_per_day)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_DAY_WINDOW": str(int(synapses_bridge_mat_day_window_sec)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_MIN_SCORE": str(float(synapses_bridge_mat_min_score)),
                        "OROMA_SYNAPSES_BRIDGE_MAT_CONFIDENCE": str(float(synapses_bridge_mat_confidence)),
                    },
                )

            if en_synapses_origin_probe and _should_run_interval(state, "synapses_origin_probe", int_synapses_origin_probe, now_ts):
                # Synapses Origin Probe: measure-only Herkunftsdiagnose.
                # Prüft event↔event-Dominanz, source_scene_id-Anteil, kind-/label-prefix-Paare
                # und Notes-Muster. Keine oroma.db-Writes, keine neuen Tabellen.
                _run(
                    "synapses_origin_probe",
                    [sys.executable, os.path.join(base, "tools", "synapses_origin_probe.py"),
                     "--once",
                     "--window-sec", str(int(synapses_origin_window_sec))],
                    timeout_sec=to_synapses_origin_probe,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_SYNAPSES_ORIGIN_WINDOW_SEC": str(int(synapses_origin_window_sec)),
                        "OROMA_SYNAPSES_ORIGIN_LIMIT_EDGES": str(int(synapses_origin_limit_edges)),
                        "OROMA_SYNAPSES_ORIGIN_TOPK": str(int(synapses_origin_topk)),
                    },
                )

            if _should_run_interval(state, "social", int_social, now_ts):
                _run("social", [sys.executable, os.path.join(base, "tools", "social_resonance_tick.py")], timeout_sec=to_social)

            if en_ptz_attention and _should_run_interval(state, "ptz_attention", int_ptz_attention, now_ts):
                _run("ptz_attention", [sys.executable, "-m", "core.ptz_attention_loop", "--once"], timeout_sec=to_ptz_attention)

            if _should_run_interval(state, "energy", int_energy, now_ts):
                _run("energy", [sys.executable, os.path.join(base, "tools", "energy_manager.py"), "--once"], timeout_sec=to_energy)

            if en_learning_cache and _should_run_interval(state, "learning_cache", int_learning_cache, now_ts):
                # Refresh Learning-UI caches (maxima + intelligence) so the dashboard stays instant.
                _run("learning_cache", [sys.executable, os.path.join(base, "tools", "learning_cache_refresh.py")], timeout_sec=to_learning_cache)

            if en_gap_learning_bridge and _should_run_interval(state, "gap_learning_bridge", int_gap_learning_bridge, now_ts):
                # Gap-Learning-Bridge Dry-Run:
                # liest knowledge_gaps + policy_rules read-only und schreibt nur
                # data/state/gap_learning_focus.json als Fokusliste. Kein DB-Write,
                # kein policy_rules-Write, keine Runner- oder Dream-Aktivierung.
                _run(
                    "gap_learning_bridge",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_learning_bridge.py"),
                        "--once",
                        "--max-runtime-s",
                        str(int(gap_learning_bridge_max_runtime_s)),
                        "--limit-gaps",
                        str(int(gap_learning_bridge_limit_gaps)),
                        "--topk",
                        str(int(gap_learning_bridge_topk)),
                    ],
                    timeout_sec=to_gap_learning_bridge,
                    env_overrides={
                        "OROMA_GAP_LEARNING_MAX_RUNTIME_S": str(int(gap_learning_bridge_max_runtime_s)),
                        "OROMA_GAP_LEARNING_LIMIT_GAPS": str(int(gap_learning_bridge_limit_gaps)),
                        "OROMA_GAP_LEARNING_TOPK": str(int(gap_learning_bridge_topk)),
                    },
                )

            if en_gap_focus_consumer and _should_run_interval(state, "gap_focus_consumer", int_gap_focus_consumer, now_ts):
                # Gap-Focus Consumer Read-Only:
                # liest nur data/state/gap_learning_focus.json und erzeugt daraus
                # data/state/gap_focus_consumer.json mit Buckets für Explore,
                # Replay, Dream und Runner-Priorität. Kein DB-Write, kein
                # policy_rules-Write, keine Runner-/Replay-/Dream-Aktivierung.
                _run(
                    "gap_focus_consumer",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_focus_consumer.py"),
                        "--once",
                        "--topk",
                        str(int(gap_focus_consumer_topk)),
                        "--max-age-sec",
                        str(int(gap_focus_consumer_max_age_sec)),
                    ],
                    timeout_sec=to_gap_focus_consumer,
                    env_overrides={
                        "OROMA_GAP_FOCUS_CONSUMER_TOPK": str(int(gap_focus_consumer_topk)),
                        "OROMA_GAP_FOCUS_CONSUMER_MAX_AGE_SEC": str(int(gap_focus_consumer_max_age_sec)),
                    },
                )

            if en_gap_focus_shadow_plan and _should_run_interval(state, "gap_focus_shadow_plan", int_gap_focus_shadow_plan, now_ts):
                # Gap-Focus Shadow Plan Read-Only:
                # liest nur data/state/gap_focus_consumer.json und erzeugt daraus
                # data/state/gap_focus_shadow_plan.json als Review-/Anschlussplan.
                # Kein DB-Write, kein policy_rules-Write, keine Runner-/Replay-/
                # Dream-Aktivierung. Diese Stufe bleibt bewusst Shadow-only.
                _run(
                    "gap_focus_shadow_plan",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_focus_shadow_plan.py"),
                        "--once",
                        "--topk",
                        str(int(gap_focus_shadow_plan_topk)),
                        "--max-age-sec",
                        str(int(gap_focus_shadow_plan_max_age_sec)),
                    ],
                    timeout_sec=to_gap_focus_shadow_plan,
                    env_overrides={
                        "OROMA_GAP_FOCUS_SHADOW_PLAN_TOPK": str(int(gap_focus_shadow_plan_topk)),
                        "OROMA_GAP_FOCUS_SHADOW_PLAN_MAX_AGE_SEC": str(int(gap_focus_shadow_plan_max_age_sec)),
                    },
                )

            if en_gap_evidence_queue and _should_run_interval(state, "gap_evidence_queue", int_gap_evidence_queue, now_ts):
                # Gap Evidence Queue Writer:
                # liest nur data/state/gap_focus_shadow_plan.json und schreibt
                # deduplizierte Review-/Evidence-Requests per DBWriter in die
                # eigene Tabelle gap_focus_evidence_queue. Kein policy_rules-Write,
                # keine Runner-/Replay-/Dream-Aktivierung, kein lokaler SQLite-Fallback.
                _run(
                    "gap_evidence_queue",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_evidence_queue_writer.py"),
                        "--once",
                        "--topk",
                        str(int(gap_evidence_queue_topk)),
                        "--max-age-sec",
                        str(int(gap_evidence_queue_max_age_sec)),
                    ],
                    timeout_sec=to_gap_evidence_queue,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_GAP_EVIDENCE_QUEUE_TOPK": str(int(gap_evidence_queue_topk)),
                        "OROMA_GAP_EVIDENCE_QUEUE_MAX_AGE_SEC": str(int(gap_evidence_queue_max_age_sec)),
                    },
                )

            if en_gap_evidence_review and _should_run_interval(state, "gap_evidence_review", int_gap_evidence_review, now_ts):
                # Gap Evidence Queue Review Dry-Run:
                # liest gap_focus_evidence_queue read-only und erzeugt
                # data/state/gap_evidence_review.json. Kein DB-Write, kein
                # policy_rules-Write, keine Runner-/Replay-/Dream-Aktivierung.
                _run(
                    "gap_evidence_review",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_evidence_review.py"),
                        "--once",
                        "--limit",
                        str(int(gap_evidence_review_limit)),
                        "--topk",
                        str(int(gap_evidence_review_topk)),
                    ],
                    timeout_sec=to_gap_evidence_review,
                    env_overrides={
                        "OROMA_GAP_EVIDENCE_REVIEW_LIMIT": str(int(gap_evidence_review_limit)),
                        "OROMA_GAP_EVIDENCE_REVIEW_TOPK": str(int(gap_evidence_review_topk)),
                    },
                )

            if en_gap_evidence_validation and _should_run_interval(state, "gap_evidence_validation", int_gap_evidence_validation, now_ts):
                # Gap Evidence Execution/Validation Dry-Run:
                # liest gap_evidence_review.json und validiert Kandidaten read-only
                # gegen gap_focus_evidence_queue und policy_rules. Kein DB-Write,
                # kein policy_rules-Write, keine Runner-/Replay-/Dream-Aktivierung.
                _run(
                    "gap_evidence_validation",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_evidence_validation.py"),
                        "--once",
                        "--limit",
                        str(int(gap_evidence_validation_limit)),
                        "--topk",
                        str(int(gap_evidence_validation_topk)),
                        "--max-age-sec",
                        str(int(gap_evidence_validation_max_age_sec)),
                    ],
                    timeout_sec=to_gap_evidence_validation,
                    env_overrides={
                        "OROMA_GAP_EVIDENCE_VALIDATION_LIMIT": str(int(gap_evidence_validation_limit)),
                        "OROMA_GAP_EVIDENCE_VALIDATION_TOPK": str(int(gap_evidence_validation_topk)),
                        "OROMA_GAP_EVIDENCE_VALIDATION_MAX_AGE_SEC": str(int(gap_evidence_validation_max_age_sec)),
                    },
                )


            if en_gap_policy_promotion and _should_run_interval(state, "gap_policy_promotion", int_gap_policy_promotion, now_ts):
                # Gap Policy Promotion Queue:
                # schreibt validierte Kandidaten per DBWriter in eine eigene
                # Promotion-/Approval-Tabelle. Kein policy_rules-Write, keine
                # Runner-/Replay-/Dream-Aktivierung.
                _run(
                    "gap_policy_promotion",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_policy_promotion.py"),
                        "--once",
                        "--limit",
                        str(int(gap_policy_promotion_limit)),
                        "--topk",
                        str(int(gap_policy_promotion_topk)),
                        "--max-age-sec",
                        str(int(gap_policy_promotion_max_age_sec)),
                    ],
                    timeout_sec=to_gap_policy_promotion,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_GAP_POLICY_PROMOTION_LIMIT": str(int(gap_policy_promotion_limit)),
                        "OROMA_GAP_POLICY_PROMOTION_TOPK": str(int(gap_policy_promotion_topk)),
                        "OROMA_GAP_POLICY_PROMOTION_MAX_AGE_SEC": str(int(gap_policy_promotion_max_age_sec)),
                    },
                )


            if en_gap_evidence_outcome and _should_run_interval(state, "gap_evidence_outcome", int_gap_evidence_outcome, now_ts):
                # Gap Evidence Outcome Collector:
                # liest Promotion-Kandidaten und sucht read-only nach direkten
                # Outcomes in rewards_log, episode_events und Ledgers. Kein DB-Write,
                # kein policy_rules-Write, keine Runner-/Replay-/Dream-Aktivierung.
                _run(
                    "gap_evidence_outcome",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_evidence_outcome.py"),
                        "--once",
                        "--limit",
                        str(int(gap_evidence_outcome_limit)),
                        "--topk",
                        str(int(gap_evidence_outcome_topk)),
                        "--lookback-sec",
                        str(int(gap_evidence_outcome_lookback_sec)),
                    ],
                    timeout_sec=to_gap_evidence_outcome,
                    env_overrides={
                        "OROMA_GAP_EVIDENCE_OUTCOME_LIMIT": str(int(gap_evidence_outcome_limit)),
                        "OROMA_GAP_EVIDENCE_OUTCOME_TOPK": str(int(gap_evidence_outcome_topk)),
                        "OROMA_GAP_EVIDENCE_OUTCOME_LOOKBACK_SEC": str(int(gap_evidence_outcome_lookback_sec)),
                    },
                )

            if en_gap_targeted_evidence_probe and _should_run_interval(state, "gap_targeted_evidence_probe", int_gap_targeted_evidence_probe, now_ts):
                # Gap Targeted Evidence Probe:
                # prueft nur wenige Promotion-Kandidaten mit bounded newest-row
                # scans. Kein DB-Write, kein policy_rules-Write, keine Starts.
                _run(
                    "gap_targeted_evidence_probe",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_targeted_evidence_probe.py"),
                        "--once",
                        "--limit",
                        str(int(gap_targeted_evidence_probe_limit)),
                        "--topk",
                        str(int(gap_targeted_evidence_probe_topk)),
                        "--scan-limit",
                        str(int(gap_targeted_evidence_probe_scan_limit)),
                    ],
                    timeout_sec=to_gap_targeted_evidence_probe,
                    env_overrides={
                        "OROMA_GAP_TARGETED_EVIDENCE_PROBE_LIMIT": str(int(gap_targeted_evidence_probe_limit)),
                        "OROMA_GAP_TARGETED_EVIDENCE_PROBE_TOPK": str(int(gap_targeted_evidence_probe_topk)),
                        "OROMA_GAP_TARGETED_EVIDENCE_PROBE_SCAN_LIMIT": str(int(gap_targeted_evidence_probe_scan_limit)),
                    },
                )

            if en_gap_replay_evidence_probe and _should_run_interval(state, "gap_replay_evidence_probe", int_gap_replay_evidence_probe, now_ts):
                # Gap Targeted Replay Evidence Probe:
                # nutzt nur lokale, explizite Headless-Adapter fuer wenige
                # Kandidaten. Kein globaler ReplayManager-Start, kein DB-Write,
                # kein policy_rules-Write und kein Runner-/Dream-Start.
                _run(
                    "gap_replay_evidence_probe",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_replay_evidence_probe.py"),
                        "--once",
                        "--limit",
                        str(int(gap_replay_evidence_probe_limit)),
                        "--topk",
                        str(int(gap_replay_evidence_probe_topk)),
                        "--horizon-steps",
                        str(int(gap_replay_evidence_probe_horizon)),
                    ],
                    timeout_sec=to_gap_replay_evidence_probe,
                    env_overrides={
                        "OROMA_GAP_REPLAY_EVIDENCE_PROBE_LIMIT": str(int(gap_replay_evidence_probe_limit)),
                        "OROMA_GAP_REPLAY_EVIDENCE_PROBE_TOPK": str(int(gap_replay_evidence_probe_topk)),
                        "OROMA_GAP_REPLAY_EVIDENCE_PROBE_HORIZON_STEPS": str(int(gap_replay_evidence_probe_horizon)),
                    },
                )

            if en_snake_autonomous_acquisition and _should_run_interval(state, "snake_autonomous_acquisition", int_snake_autonomous_acquisition, now_ts):
                # Autonome Snake-Pro-v2-Brücke: verarbeitet ausschließlich den
                # frischen, promotion-gebundenen Missing-Source-Blocker des
                # Replay-Probes. Die Evidence-Persistenz bleibt atomar und
                # DBWriter-only im bestehenden V2-Akquisitionswriter.
                _run(
                    "snake_autonomous_acquisition",
                    [sys.executable, os.path.join(base, "tools", "snake_autonomous_acquisition_bridge.py")],
                    timeout_sec=to_snake_autonomous_acquisition,
                    env_overrides={"OROMA_DBW_ENABLE": "1"},
                )

            if en_gap_evidence_outcome_queue and _should_run_interval(state, "gap_evidence_outcome_queue", int_gap_evidence_outcome_queue, now_ts):
                # Gap Evidence Outcome Queue Gate:
                # uebernimmt fertige replay-probe Outcomes DBWriter-only in
                # gap_evidence_outcome_queue. Kein policy_rules-Write und keine Starts.
                _run(
                    "gap_evidence_outcome_queue",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_evidence_outcome_queue.py"),
                        "--once",
                        "--limit",
                        str(int(gap_evidence_outcome_queue_limit)),
                        "--topk",
                        str(int(gap_evidence_outcome_queue_topk)),
                        "--min-confidence",
                        str(float(gap_evidence_outcome_queue_min_confidence)),
                    ],
                    timeout_sec=to_gap_evidence_outcome_queue,
                    env_overrides={
                        "OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_LIMIT": str(int(gap_evidence_outcome_queue_limit)),
                        "OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_TOPK": str(int(gap_evidence_outcome_queue_topk)),
                        "OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_MIN_CONFIDENCE": str(float(gap_evidence_outcome_queue_min_confidence)),
                    },
                )

            if en_gap_policy_mini_write and _should_run_interval(state, "gap_policy_mini_write", int_gap_policy_mini_write, now_ts):
                # Gap Policy Mini-Write Gate:
                # finales Policy-Gate mit Ledger. Default ENV bleibt write-seitig
                # geschlossen. Reine Gap-Wahrscheinlichkeit reicht nie fuer einen
                # policy_rules-Write; echte Evidence-Outcomes sind Pflicht.
                _run(
                    "gap_policy_mini_write",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_policy_mini_write.py"),
                        "--once",
                        "--limit",
                        str(int(gap_policy_mini_write_limit)),
                        "--topk",
                        str(int(gap_policy_mini_write_topk)),
                        "--max-writes",
                        str(int(gap_policy_mini_write_max_writes)),
                    ],
                    timeout_sec=to_gap_policy_mini_write,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_GAP_POLICY_MINI_WRITE_LIMIT": str(int(gap_policy_mini_write_limit)),
                        "OROMA_GAP_POLICY_MINI_WRITE_TOPK": str(int(gap_policy_mini_write_topk)),
                        "OROMA_GAP_POLICY_MINI_WRITE_MAX_WRITES_PER_RUN": str(int(gap_policy_mini_write_max_writes)),
                    },
                )

            if en_forget_sample and _should_run_interval(state, "forgetting_sample", int_forget_sample, now_ts):
                # Write stats_points for Forgetting/Kompression history.
                _run("forgetting_sample", [sys.executable, os.path.join(base, "tools", "forgetting_sampler.py"), "--once"], timeout_sec=to_forget_sample)

            if en_vision_binding_probe and _should_run_interval(state, "vision_binding_probe", int_vision_binding_probe, now_ts):
                _run("vision_binding_probe", [sys.executable, os.path.join(base, "tools", "vision_binding_probe.py")], timeout_sec=to_vision_binding_probe)
            if en_compression_probe and _should_run_interval(state, "compression_probe", int_compression_probe, now_ts):
                # Stage A: measure redundancy + compressed_* materialization (no destructive actions).
                _run(                    "compression_probe",
                    [sys.executable, os.path.join(base, "tools", "compression_probe.py")],
                    timeout_sec=to_compression_probe,
                    env_overrides={"OROMA_DBW_ENABLE": "1"},
                )
            if en_object_extractor and _should_run_interval(state, "object_extractor", int_object_extractor, now_ts):
                # Re-materialize ObjectGraph nodes from recent SceneGraphs
                # (incl. compressed_* via auto_meta pipeline in object:auto:vision namespace).
                _run(
                    "object_extractor",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "object_extractor_once.py"),
                        "--namespace",
                        os.environ.get("OROMA_OBJECTGRAPH_TARGET_NS", "object:auto:vision"),
                        "--max-graphs",
                        str(int(object_extractor_max_graphs)),
                    ],
                    timeout_sec=to_object_extractor,
                    env_overrides={"OROMA_DBW_ENABLE": "1"},
                )

            if en_compression_materializer and _should_run_interval(state, "compression_materializer", int_compression_materializer, now_ts):
                # Stage B: materialize a small number of compressed_* nodes (ObjectGraph) from repeat signals.
                # Conservative defaults: Top-K + Min-Repeat gates; DBWriter-only.
                _run(
                    "compression_materializer",
                    [sys.executable, os.path.join(base, "tools", "compression_materializer.py"),
                     "--window-sec", "86400",
                     "--limit-chains", str(int(comp_mat_limit_chains)),
                     "--topk", str(int(comp_mat_topk)),
                     "--min-repeat", str(int(comp_mat_min_repeat))],
                    timeout_sec=to_compression_materializer,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_COMP_MAT_ORIGINS": os.environ.get("OROMA_COMP_MAT_ORIGINS", "audio/token"),
                    },
                )

            if en_meta_snap_indexer and _should_run_interval(state, "meta_snap_indexer", int_meta_snap_indexer, now_ts):
                try:
                    print(
                        "[orchestrator] meta_snap_indexer run: "
                        f"max_n={int(meta_snap_indexer_max_n)} "
                        f"min_score={float(meta_snap_indexer_min_score):.4f} "
                        f"budget_per_day={int(meta_snap_indexer_budget_per_day)}"
                    )
                except Exception:
                    pass

                # Materialize compressed_* MetaSnaps into ObjectGraph (object_nodes/object_relations) in a DBWriter-safe way.
                _run(
                    "meta_snap_indexer",
                    [sys.executable, os.path.join(base, "tools", "meta_snap_indexer_runner.py"),
                     "--once",
                     "--max-n", str(int(meta_snap_indexer_max_n)),
                     "--min-score", str(meta_snap_indexer_min_score),
                     "--budget-per-day", str(int(meta_snap_indexer_budget_per_day))],
                    timeout_sec=to_meta_snap_indexer,
                    env_overrides={"OROMA_DBW_ENABLE": "1"},
                )



            if en_crossmodal_linker and _should_run_interval(state, "crossmodal_linker", int_crossmodal_linker, now_ts):
                # Batch-Linker: calc/result ↔ vision/token → link/calc_vision (SnapChains)
                _run("crossmodal_linker", [sys.executable, os.path.join(base, "tools", "crossmodal_linker_runner.py"), "--once"], timeout_sec=to_crossmodal_linker)

            if en_train and _should_run_interval(state, "train_snake", int_train, now_ts):
                train_decision = execution_mode.legacy_policy_training_allowed(
                    writer_id="writer:core.train_snake_policy:legacy", namespace="game:snake"
                )
                if train_decision.allowed:
                    _run("train_snake", [sys.executable, "-m", "core.train_snake_policy", "--limit", "3000", "--verbose"], timeout_sec=to_train_snake)
                else:
                    state.setdefault("legacy_jobs_suppressed", {})["train_snake"] = {
                        **train_decision.to_dict(),
                        "ts": int(now_ts),
                    }
                    _mark_ran(state, "train_snake", now_ts, 0, 0.0, timed_out=False)
                    _atomic_write_json(state_path, state)
                    with open(log_out, "a", encoding="utf-8") as fo:
                        fo.write(
                            "[ORCH] controlled_skip job=train_snake "
                            f"mode={train_decision.execution_mode} "
                            f"namespace={train_decision.namespace} reason={train_decision.reason}\n"
                        )

            if en_dream and _should_run_interval(state, "dream", int_dream, now_ts):
                allow_dream = True
                allow_reason = "phase gating disabled"
                if dream_require_phase:
                    allow_dream, allow_reason = _phase_allows_dream(phase_path)
                if allow_dream:
                    dream_cmd = [sys.executable, "-m", "core.dream_worker", "--interval=0"]
                    dream_chain_enabled = _env_bool("OROMA_ORCH_DREAM_CHAIN_ENABLED", True)
                    dream_chain_max_runs_raw = _env_int("OROMA_ORCH_DREAM_CHAIN_MAX_RUNS", 0)
                    dream_chain_max_runs = 0 if dream_chain_max_runs_raw <= 0 else max(1, dream_chain_max_runs_raw)
                    dream_chain_total_budget_s = max(60, _env_int("OROMA_ORCH_DREAM_CHAIN_TOTAL_BUDGET_S", 5400))
                    dream_chain_started_ts = time.time()
                    dream_state_path = _dream_state_path(base)
                    chain_runs = 0
                    while True:
                        chain_runs += 1
                        _run("dream", dream_cmd, timeout_sec=to_dream)
                        if not dream_chain_enabled:
                            break
                        if dream_chain_max_runs > 0 and chain_runs >= dream_chain_max_runs:
                            with open(log_out, "a", encoding="utf-8") as fo:
                                fo.write(
                                    f"[ORCH] dream chain stop: max_runs reached run={chain_runs}/{dream_chain_max_runs}\n"
                                )
                                fo.flush()
                            break
                        elapsed_chain_s = max(0.0, time.time() - dream_chain_started_ts)
                        if elapsed_chain_s >= float(dream_chain_total_budget_s):
                            with open(log_out, "a", encoding="utf-8") as fo:
                                fo.write(
                                    f"[ORCH] dream chain stop: total_budget_hit elapsed_s={elapsed_chain_s:.1f} budget_s={dream_chain_total_budget_s}\n"
                                )
                                fo.flush()
                            break
                        if dream_require_phase:
                            allow_more, allow_more_reason = _phase_allows_dream(phase_path)
                            if not allow_more:
                                with open(log_out, "a", encoding="utf-8") as fo:
                                    fo.write(f"[ORCH] dream chain stop: {allow_more_reason} (phase_path={phase_path})\n")
                                    fo.flush()
                                break
                        dstate = _load_dream_state(dream_state_path)
                        if _dream_should_continue(dstate):
                            with open(log_out, "a", encoding="utf-8") as fo:
                                fo.write(
                                    f"[ORCH] dream chain continue: run_status={dstate.get('run_status')} phase={dstate.get('last_completed_phase')} run={chain_runs}/{'∞' if dream_chain_max_runs <= 0 else dream_chain_max_runs} elapsed_s={elapsed_chain_s:.1f}/{dream_chain_total_budget_s}\n"
                                )
                                fo.flush()
                            continue
                        with open(log_out, "a", encoding="utf-8") as fo:
                            fo.write(
                                f"[ORCH] dream chain stop: work complete run={chain_runs} elapsed_s={elapsed_chain_s:.1f}/{dream_chain_total_budget_s}\n"
                            )
                            fo.flush()
                        break
                else:
                    state.setdefault("notes", {})["dream_skip"] = {
                        "ts": int(now_ts),
                        "reason": allow_reason,
                        "phase_path": phase_path,
                    }
                    _mark_ran(state, "dream", now_ts, 0, 0.0, timed_out=False)
                    _atomic_write_json(state_path, state)
                    with open(log_out, "a", encoding="utf-8") as fo:
                        fo.write(
                            f"[ORCH] dream skipped: {allow_reason} (phase_path={phase_path})\n"
                        )
                        fo.flush()

            if en_dream_auto_mini_write and _should_run_interval(state, "dream_auto_mini_write", int_dream_auto_mini_write, now_ts):
                # Dream→Policy Auto-Mini-Write (Default-EIN-ENV-Gate)
                # -------------------------------------------------------
                # Der Orchestrator startet hier NICHT den manuellen Mini-Write,
                # sondern die eigenstaendige Auto-Phase mit konservativen
                # Default-Limits. Alle produktiven Writes bleiben im DreamWorker
                # nochmals abgesichert: DBWriter-only, Confirm-Token, Ledger-
                # Pflicht, Direct-Step-Credit-only, Tageslimit und Cooldown.
                _run(
                    "dream_auto_mini_write",
                    [
                        sys.executable,
                        "-m",
                        "core.dream_worker",
                        "--once",
                        "--phase",
                        "adapter_auto_mini_write_gated",
                        "--adapter-namespace",
                        str(dream_auto_mini_write_namespace),
                        "--adapter-state-schema-prefix",
                        str(dream_auto_mini_write_schema),
                        "--adapter-limit",
                        str(int(dream_auto_mini_write_limit)),
                    ],
                    timeout_sec=to_dream_auto_mini_write,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_DBW_STRICT_LOCAL_WRITES": "1",
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_ENABLE": "1",
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_CONFIRM": str(dream_auto_mini_write_confirm),
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_MAX": str(int(dream_auto_mini_write_max)),
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_DAILY_CAP": str(int(dream_auto_mini_write_daily_cap)),
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_COOLDOWN_S": str(int(dream_auto_mini_write_cooldown_s)),
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_MIN_DIRECT_EVIDENCE": str(int(dream_auto_mini_write_min_direct)),
                        "OROMA_DREAM_ADAPTER_AUTO_MINI_WRITE_MIN_ABS_DELTA": str(float(dream_auto_mini_write_min_abs_delta)),
                    },
                )

            if en_gap_miner and _should_run_interval(state, "gap_miner", int_gap_miner, now_ts):
                _run(
                    "gap_miner",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_miner.py"),
                        "--once",
                        "--mode",
                        "rotate",
                        "--max-runtime-s",
                        str(int(gap_miner_max_runtime_s)),
                        "--limit-per-kind",
                        str(int(gap_miner_limit_per_kind)),
                    ],
                    timeout_sec=to_gap_miner,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_DBW_STRICT_LOCAL_WRITES": "1",
                        "OROMA_GAP_MINER_REQUIRE_DBWRITER": "1",
                        "OROMA_GAP_MINER_MAX_INSERTS_PER_RUN": str(int(gap_miner_max_inserts)),
                        "OROMA_GAP_MINER_MAX_RUNTIME_S": str(int(gap_miner_max_runtime_s)),
                        "OROMA_GAP_MINER_LIMIT_PER_KIND": str(int(gap_miner_limit_per_kind)),
                        "OROMA_GAP_MINER_HU_ROW_SCAN_LIMIT": str(int(gap_miner_hu_row_scan_limit)),
                        "OROMA_GAP_MINER_LE_ROW_SCAN_LIMIT": str(int(gap_miner_le_row_scan_limit)),
                        "OROMA_GAP_MINER_LC_ROW_SCAN_LIMIT": str(int(gap_miner_lc_row_scan_limit)),
                        "OROMA_GAPS_LOCK_RETRY_SEC": "2",
                        "OROMA_DB_LOCK_RETRY_SEC": "2",
                    },
                )

            # Daily-Jobs (Nightly)
            # Gap-Miner Night Sweep
            # ---------------------
            # Tagsüber läuft gap_miner nur kurz und cursorbasiert. Dieser tägliche
            # Nacht-Sweep arbeitet dagegen mehrere bounded Rotate-Slices in einem
            # längeren Fenster ab. Dadurch kann ORÓMA Rückstände systematisch
            # abbauen, ohne den seriellen Orchestrator tagsüber zu blockieren.
            if en_gap_miner_night and _should_run_daily(state, "gap_miner_night_sweep", gap_miner_night_hh, gap_miner_night_mm, jitter_daily, now):
                rc, dur = _run(
                    "gap_miner_night_sweep",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "gap_miner.py"),
                        "--once",
                        "--mode",
                        "sweep",
                        "--max-runtime-s",
                        str(int(gap_miner_night_max_runtime_s)),
                        "--limit-per-kind",
                        str(int(gap_miner_night_limit_per_kind)),
                        "--sweep-passes",
                        str(int(gap_miner_night_sweep_passes)),
                    ],
                    timeout_sec=to_gap_miner_night,
                    env_overrides={
                        "OROMA_DBW_ENABLE": "1",
                        "OROMA_DBW_STRICT_LOCAL_WRITES": "1",
                        "OROMA_GAP_MINER_REQUIRE_DBWRITER": "1",
                        "OROMA_GAP_MINER_MAX_INSERTS_PER_RUN": str(int(gap_miner_night_max_inserts)),
                        "OROMA_GAP_MINER_MAX_RUNTIME_S": str(int(gap_miner_night_max_runtime_s)),
                        "OROMA_GAP_MINER_LIMIT_PER_KIND": str(int(gap_miner_night_limit_per_kind)),
                        "OROMA_GAP_MINER_HU_ROW_SCAN_LIMIT": str(int(gap_miner_night_hu_row_scan_limit)),
                        "OROMA_GAP_MINER_LE_ROW_SCAN_LIMIT": str(int(gap_miner_night_le_row_scan_limit)),
                        "OROMA_GAP_MINER_LC_ROW_SCAN_LIMIT": str(int(gap_miner_night_lc_row_scan_limit)),
                        "OROMA_GAP_MINER_SWEEP_SLICE_RUNTIME_S": str(int(gap_miner_night_slice_runtime_s)),
                        "OROMA_GAPS_LOCK_RETRY_SEC": "2",
                        "OROMA_DB_LOCK_RETRY_SEC": "2",
                    },
                )
                if int(rc) == 0:
                    _mark_daily(state, "gap_miner_night_sweep", now)
                else:
                    try:
                        with open(log_err, "a", encoding="utf-8") as fe:
                            fe.write(f"\n[ORCH] DAILY FAIL job=gap_miner_night_sweep rc={int(rc)} dur_s={float(dur):.3f}\n")
                    except Exception:
                        pass
                _atomic_write_json(state_path, state)

            if en_kpi and _should_run_daily(state, "kpi", 3, 10, jitter_daily, now):
                _run("kpi", [sys.executable, "-m", "tools.kpi_harness"], timeout_sec=to_kpi)
                _mark_daily(state, "kpi", now)
                _atomic_write_json(state_path, state)

            if en_policy and _should_run_daily(state, "policy", 3, 15, jitter_daily, now):
                # entspricht oroma-policy.service (Training + Export Archiv)
                _run("policy_train", [sys.executable, "-m", "core.policy_engine", "--train-db", "--limit", "50000", "--namespace", "game:tictactoe"], timeout_sec=to_policy_train)
                _run("policy_export", [sys.executable, "-m", "core.policy_engine", "--export-archiv", "--namespace", "game:tictactoe", "--min-n", "3", "--min-abs-q", "0.1"], timeout_sec=to_policy_export)
                _mark_daily(state, "policy", now)
                _atomic_write_json(state_path, state)

            # Nightly: TicTacToe Oracle überschreibt fehlerhafte Archiv-Policy sicher mit Minimax.
            # (Damit sollte ttt_eval im Mirror-Self-Play wieder ≈100% Draws erreichen.)
            if en_ttt_oracle and _should_run_daily(state, "ttt_oracle", ttt_oracle_hh, ttt_oracle_mm, jitter_daily, now):
                _run("ttt_oracle", [sys.executable, os.path.join(base, "tools", "ttt_oracle_export.py"), "--once"], timeout_sec=to_ttt_oracle)
                _mark_daily(state, "ttt_oracle", now)
                _atomic_write_json(state_path, state)

            # Daily: TicTacToe self-play (policy-benchmark + explore-learning)
            #
            # Ziel:
            #   • 1x pro Tag 2 Batches:
            #       - policy-only  (Benchmark)
            #       - explore      (Lernen/Abweichung)
            #   • Ergebnisse & Dauer werden in oroma.db als Episoden + Metriken abgelegt.
            #
            # Hinweis:
            #   Der Runner ist bewusst unabhängig vom Flask-UI-Loop.
            if en_ttt_daily_run and _should_run_daily(state, "ttt_daily_run", ttt_daily_hh, ttt_daily_mm, jitter_daily, now):
                _run(
                    "ttt_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "tictactoe_daily_runner.py"),
                        "--policy-games", str(max(0, int(ttt_policy_games))),
                        "--explore-games", str(max(0, int(ttt_explore_games))),
                        "--namespace", "game:tictactoe",
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--once",
                    ],
                    timeout_sec=to_ttt_daily_run,
                )
                _mark_daily(state, "ttt_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Connect4 self-play (policy-benchmark + explore-learning)
            #
            # Ziel:
            #   • 1x pro Tag 2 Batches:
            #       - policy-only  (Benchmark)
            #       - explore      (Lernen/Abweichung)
            #   • Ergebnisse & Dauer werden in oroma.db als Episoden + Metriken abgelegt.
            if en_c4_daily_run and _should_run_daily(state, "c4_daily_run", c4_daily_hh, c4_daily_mm, jitter_daily, now):
                rc, dur = _run(
                    "c4_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "connect4_daily_runner.py"),
                        "--policy-games", str(max(0, int(c4_policy_games))),
                        "--explore-games", str(max(0, int(c4_explore_games))),
                        "--namespace", "game:connect4",
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--once",
                    ],
                    timeout_sec=to_c4_daily_run,
                )
                if int(rc) == 0:
                    _clear_daily_fail(state, "c4_daily_run")
                    _mark_daily(state, "c4_daily_run", now)
                else:
                    _mark_daily_fail(state, "c4_daily_run", now, int(rc), float(dur))
                    try:
                        retry_min = max(1, int(os.getenv("OROMA_ORCH_DAILY_RETRY_MIN", "30").strip() or "30"))
                        with open(log_err, "a", encoding="utf-8") as fe:
                            fe.write(f"\n[ORCH] DAILY FAIL job=c4_daily_run rc={int(rc)} dur_s={float(dur):.3f} (retry>=\"{retry_min}\"m; env=OROMA_ORCH_DAILY_RETRY_MIN)\n")
                    except Exception:
                        pass
                _atomic_write_json(state_path, state)


            # Daily: Snake self-play (policy-benchmark + explore-learning)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_snake_daily_run and _should_run_daily(state, "snake_daily_run", snake_daily_hh, snake_daily_mm, jitter_daily, now):
                _run(
                    "snake_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "snake_daily_runner.py"),
                        "--policy-games", str(max(0, int(snake_policy_games))),
                        "--explore-games", str(max(0, int(snake_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:snake",
                    ],
                    timeout_sec=to_snake_daily_run,
                    env_overrides={
                        "OROMA_EXECUTION_MODE": execution_mode.get_execution_mode(),
                        "OROMA_VERTICAL_PROOF_NAMESPACE_ALLOWLIST": ",".join(execution_mode.proof_namespace_allowlist()),
                    },
                )
                _mark_daily(state, "snake_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Snake3D professional Policy+Explore.
            #
            # Ziel:
            #   • Policy-Games nutzen policy_rules mit Safety-/Q-Gate.
            #   • Explore-Games liefern weiter neue Z-Achsen-Abdeckung.
            #   • Keine UI-Starts, kein subprocess aus Flask, kein PTZ-Bezug.
            #   • policy_rules werden nur über den DBWriter-kompatiblen Pfad des
            #     Snake3D-Runners geschrieben.
            if en_snake3d_daily_run and _should_run_daily(state, "snake3d_daily_run", snake3d_daily_hh, snake3d_daily_mm, jitter_daily, now):
                rc, dur = _run(
                    "snake3d_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "snake3d_daily_runner.py"),
                        "--policy-games", str(max(0, int(snake3d_policy_games))),
                        "--explore-games", str(max(0, int(snake3d_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:snake3d",
                        "--size", str(max(4, int(snake3d_size))),
                        "--max-steps", str(max(10, int(snake3d_max_steps))),
                        "--target-len", str(max(4, int(snake3d_target_len))),
                    ],
                    timeout_sec=to_snake3d_daily_run,
                )
                if int(rc) == 0:
                    _clear_daily_fail(state, "snake3d_daily_run")
                    _mark_daily(state, "snake3d_daily_run", now)
                else:
                    _mark_daily_fail(state, "snake3d_daily_run", now, int(rc), float(dur))
                    try:
                        retry_min = max(1, int(os.getenv("OROMA_ORCH_DAILY_RETRY_MIN", "30").strip() or "30"))
                        with open(log_err, "a", encoding="utf-8") as fe:
                            fe.write(f"\n[ORCH] DAILY FAIL job=snake3d_daily_run rc={int(rc)} dur_s={float(dur):.3f} (retry>=\"{retry_min}\"m; env=OROMA_ORCH_DAILY_RETRY_MIN)\n")
                    except Exception:
                        pass
                _atomic_write_json(state_path, state)

            # Daily: Pong self-play (policy-benchmark + explore-learning)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_pong_daily_run and _should_run_daily(state, "pong_daily_run", pong_daily_hh, pong_daily_mm, jitter_daily, now):
                _run(
                    "pong_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "pong_daily_runner.py"),
                        "--policy-games", str(max(0, int(pong_policy_games))),
                        "--explore-games", str(max(0, int(pong_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:pong",
                    ],
                    timeout_sec=to_pong_daily_run,
                )
                _mark_daily(state, "pong_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Tetris (policy-benchmark + explore)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_tetris_daily_run and _should_run_daily(state, "tetris_daily_run", tetris_daily_hh, tetris_daily_mm, jitter_daily, now):
                _run(
                    "tetris_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "tetris_daily_runner.py"),
                        "--policy-games", str(max(0, int(tetris_policy_games))),
                        "--explore-games", str(max(0, int(tetris_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:tetris",
                    ],
                    timeout_sec=to_tetris_daily_run,
                )
                _mark_daily(state, "tetris_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Memory (classic pairs) – policy-benchmark + explore-learning
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_memory_daily_run and _should_run_daily(state, "memory_daily_run", memory_daily_hh, memory_daily_mm, jitter_daily, now):
                rc, dur = _run(
                    "memory_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "memory_daily_runner.py"),
                        "--policy-games", str(max(0, int(memory_policy_games))),
                        "--explore-games", str(max(0, int(memory_explore_games))),
                        "--size", str(max(2, int(memory_size))),
                        "--max-turns", str(max(20, int(memory_max_turns))),
                        "--eps", str(float(memory_eps)),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:memory",
                    ],
                    timeout_sec=to_memory_daily_run,
                )
                if int(rc) == 0:
                    _clear_daily_fail(state, "memory_daily_run")
                    _mark_daily(state, "memory_daily_run", now)
                else:
                    _mark_daily_fail(state, "memory_daily_run", now, int(rc), float(dur))
                    try:
                        retry_min = max(1, int(os.getenv("OROMA_ORCH_DAILY_RETRY_MIN", "30").strip() or "30"))
                        with open(log_err, "a", encoding="utf-8") as fe:
                            fe.write(f"\n[ORCH] DAILY FAIL job=memory_daily_run rc={int(rc)} dur_s={float(dur):.3f} (retry>=\"{retry_min}\"m; env=OROMA_ORCH_DAILY_RETRY_MIN)\n")
                    except Exception:
                        pass
                _atomic_write_json(state_path, state)

            # Daily: Sudoku (mechanic_solved Constraint Runner)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db sowie
            # policy_rules(namespace='game:sudoku', state_hash LIKE 'sudoku:pro_v2%').
            if en_sudoku_daily_run and _should_run_daily(state, "sudoku_daily_run", sudoku_daily_hh, sudoku_daily_mm, jitter_daily, now):
                rc, dur = _run(
                    "sudoku_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "sudoku_daily_runner.py"),
                        "--policy-games", str(max(0, int(sudoku_policy_games))),
                        "--explore-games", str(max(0, int(sudoku_explore_games))),
                        "--difficulty", str(sudoku_difficulty or "medium"),
                        "--eps", str(float(sudoku_eps)),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:sudoku",
                    ],
                    timeout_sec=to_sudoku_daily_run,
                )
                if int(rc) == 0:
                    _clear_daily_fail(state, "sudoku_daily_run")
                    _mark_daily(state, "sudoku_daily_run", now)
                else:
                    _mark_daily_fail(state, "sudoku_daily_run", now, int(rc), float(dur))
                    try:
                        retry_min = max(1, int(os.getenv("OROMA_ORCH_DAILY_RETRY_MIN", "30").strip() or "30"))
                        with open(log_err, "a", encoding="utf-8") as fe:
                            fe.write(f"\n[ORCH] DAILY FAIL job=sudoku_daily_run rc={int(rc)} dur_s={float(dur):.3f} (retry>=\"{retry_min}\"m; env=OROMA_ORCH_DAILY_RETRY_MIN)\n")
                    except Exception:
                        pass
                _atomic_write_json(state_path, state)

            # Daily: Chess self-play (policy-benchmark + explore-learning)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_chess_daily_run and _should_run_daily(state, "chess_daily_run", chess_daily_hh, chess_daily_mm, jitter_daily, now):
                _run(
                    "chess_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess_daily_runner.py"),
                        "--policy-games", str(max(0, int(chess_policy_games))),
                        "--explore-games", str(max(0, int(chess_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_chess_daily_run,
                )
                _mark_daily(state, "chess_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: ChessPro professional self-play (2 Partien/Tag, Farbe-Wechsel)
            #
            # Zielarchitektur für ORÓMA-Schach:
            #   • eigener Namespace `game:chess_pro` statt weiterer Chess2-Ausbau.
            #   • 1. Lauf: ORÓMA-Fokus Weiß.
            #   • 2. Lauf: ORÓMA-Fokus Schwarz.
            #   • 1 Stunde Abstand zwischen den Default-Zeitpunkten.
            #   • Professionelle Bewertungsregeln + Iterative-Deepening-Alpha-Beta.
            #   • v0.2.0 Long-Search-Profil: bis 28min pro Partie, aber hart begrenzt.
            #   • Kontrollierter Lernloop: terminales Ergebnis + Stellungsdelta-Signal
            #     für Remis-/Budgetpartien, damit lange Berechnungen verwertbar bleiben.
            if en_chess_pro_daily_run and _should_run_daily(state, "chess_pro_white_daily_run", chess_pro_white_hh, chess_pro_white_mm, jitter_daily, now):
                _run(
                    "chess_pro_white_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess_pro_daily_runner.py"),
                        "--side", "white",
                        "--games", "1",
                        "--depth", str(max(1, int(chess_pro_depth))),
                        "--max-plies", str(max(20, int(chess_pro_max_plies))),
                        "--time-budget-ms", str(max(100, int(chess_pro_time_budget_ms))),
                        "--game-budget-sec", str(max(60, int(chess_pro_game_budget_sec))),
                        "--eps", str(float(chess_pro_eps)),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:chess_pro",
                    ],
                    timeout_sec=to_chess_pro_daily_run,
                    env_overrides={"OROMA_CHESS_PRO_DB": "1", "OROMA_CHESS_PRO_LEARN": "1"},
                )
                _mark_daily(state, "chess_pro_white_daily_run", now)
                _atomic_write_json(state_path, state)

            if en_chess_pro_daily_run and _should_run_daily(state, "chess_pro_black_daily_run", chess_pro_black_hh, chess_pro_black_mm, jitter_daily, now):
                _run(
                    "chess_pro_black_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess_pro_daily_runner.py"),
                        "--side", "black",
                        "--games", "1",
                        "--depth", str(max(1, int(chess_pro_depth))),
                        "--max-plies", str(max(20, int(chess_pro_max_plies))),
                        "--time-budget-ms", str(max(100, int(chess_pro_time_budget_ms))),
                        "--game-budget-sec", str(max(60, int(chess_pro_game_budget_sec))),
                        "--eps", str(float(chess_pro_eps)),
                        "--seed", str((int(now_ts) + 3600) & 0xFFFFFFFF),
                        "--namespace", "game:chess_pro",
                    ],
                    timeout_sec=to_chess_pro_daily_run,
                    env_overrides={"OROMA_CHESS_PRO_DB": "1", "OROMA_CHESS_PRO_LEARN": "1"},
                )
                _mark_daily(state, "chess_pro_black_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess2 Side-Profile self-play (2 Partien/Tag, Farbe-Wechsel)
            #
            # Ziel:
            #   • Exakt zwei ressourcenschonende Chess-Partien pro Tag.
            #   • 1. Lauf: Weiß-Perspektive im normalen Mobility-Raum.
            #   • 2. Lauf: Schwarz-Perspektive über primären Flip-Raum.
            #   • 1 Stunde Abstand zwischen den Default-Zeitpunkten, damit
            #     Replay/NMR/DBWriter nicht unnötig verdichtet werden.
            #
            # Wichtig:
            #   `--policy-games 0 --explore-games 1` bedeutet: jeder Side-Lauf
            #   erzeugt genau eine lernende Partie. Der alte große Chess2-Batch
            #   (`OROMA_ORCH_ENABLE_CHESS2_DAILY_RUN`) bleibt vorhanden, ist aber
            #   ab 2026-06-27 nicht mehr Default.
            if en_chess2_side_daily_run and _should_run_daily(state, "chess2_side_white_daily_run", chess2_side_white_hh, chess2_side_white_mm, jitter_daily, now):
                _run(
                    "chess2_side_white_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess2_daily_runner.py"),
                        "--policy-games", "0",
                        "--explore-games", str(max(1, int(chess2_side_games))),
                        "--enable-flip-pass", "0",
                        "--side-profile", "white",
                        "--max-plies", str(max(20, int(chess2_side_max_plies))),
                        "--eps", str(float(chess2_side_eps)),
                        "--eps-white", str(float(chess2_side_eps_white)),
                        "--eps-black", str(float(chess2_side_eps_black)),
                        "--explore-moves-white", str(max(0, int(chess2_side_explore_moves_white))),
                        "--explore-moves-black", str(max(0, int(chess2_side_explore_moves_black))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:chess2",
                    ],
                    timeout_sec=to_chess2_side_daily_run,
                    env_overrides={
                        "OROMA_CHESS2_BOOTSTRAP_FROM_CHESS": "0",
                        "OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY": "1",
                    },
                )
                _mark_daily(state, "chess2_side_white_daily_run", now)
                _atomic_write_json(state_path, state)

            if en_chess2_side_daily_run and _should_run_daily(state, "chess2_side_black_daily_run", chess2_side_black_hh, chess2_side_black_mm, jitter_daily, now):
                _run(
                    "chess2_side_black_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess2_daily_runner.py"),
                        "--policy-games", "0",
                        "--explore-games", str(max(1, int(chess2_side_games))),
                        "--enable-flip-pass", "0",
                        "--side-profile", "black",
                        "--max-plies", str(max(20, int(chess2_side_max_plies))),
                        "--eps", str(float(chess2_side_eps)),
                        "--eps-white", str(float(chess2_side_eps_white)),
                        "--eps-black", str(float(chess2_side_eps_black)),
                        "--explore-moves-white", str(max(0, int(chess2_side_explore_moves_white))),
                        "--explore-moves-black", str(max(0, int(chess2_side_explore_moves_black))),
                        "--seed", str((int(now_ts) + 3600) & 0xFFFFFFFF),
                        "--namespace", "game:chess2",
                    ],
                    timeout_sec=to_chess2_side_daily_run,
                    env_overrides={
                        "OROMA_CHESS2_BOOTSTRAP_FROM_CHESS": "0",
                        "OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY": "1",
                    },
                )
                _mark_daily(state, "chess2_side_black_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess2 self-play (mobility-native Parallel-Stack)
            #
            # Ziel:
            #   • Chess2 lernt regelmäßig im Orchestrator und hängt nicht nur an
            #     manuellen SSH-Starts.
            #   • Die wichtigsten Diagnosen (UP-Nutzung, Rule-Delta, Draw-by-cap,
            #     max_plies_seen) landen dabei automatisch in Logs + DB.
            #
            # Hinweis:
            #   Chess2 ist bewusst ein eigener Job / eigener Namespace und berührt
            #   bestehendes game:chess nicht destruktiv.
            if en_chess2_daily_run and _should_run_daily(state, "chess2_daily_run", chess2_daily_hh, chess2_daily_mm, jitter_daily, now):
                _run(
                    "chess2_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess2_daily_runner.py"),
                        "--policy-games", str(max(0, int(chess2_policy_games))),
                        "--explore-games", str(max(0, int(chess2_explore_games))),
                        "--max-plies", str(max(20, int(chess2_max_plies))),
                        "--eps", str(float(chess2_eps)),
                        "--eps-white", str(float(chess2_eps_white)),
                        "--eps-black", str(float(chess2_eps_black)),
                        "--explore-moves-white", str(max(0, int(chess2_explore_moves_white))),
                        "--explore-moves-black", str(max(0, int(chess2_explore_moves_black))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:chess2",
                    ],
                    timeout_sec=to_chess2_daily_run,
                    env_overrides={
                        # Produktiv ab jetzt KEIN regelmäßiger Bootstrap mehr:
                        # Chess2 soll im Nightly-Betrieb eigene Tiefe aufbauen,
                        # nicht bei jedem Lauf alte Chess1-Traces nachladen.
                        "OROMA_CHESS2_BOOTSTRAP_FROM_CHESS": "0",
                        "OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY": "1",
                    },
                )
                _mark_daily(state, "chess2_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess2 Canon self-play (kanonischer Side-Normalization-Raum)
            #
            # Ziel:
            #   • game:chess2 bleibt als bestehender Mobility-Raum erhalten.
            #   • game:chess2_canon startet parallel in einem neuen Namespace,
            #     damit Weiß-/Schwarz-Symmetrien auf denselben Zustandsraum
            #     verdichtet werden können.
            if en_chess2_canon_daily_run and _should_run_daily(state, "chess2_canon_daily_run", chess2_canon_daily_hh, chess2_canon_daily_mm, jitter_daily, now):
                _run(
                    "chess2_canon_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess2_canon_daily_runner.py"),
                        "--policy-games", str(max(0, int(chess2_canon_policy_games))),
                        "--explore-games", str(max(0, int(chess2_canon_explore_games))),
                        "--max-plies", str(max(20, int(chess2_canon_max_plies))),
                        "--eps", str(float(chess2_canon_eps)),
                        "--eps-white", str(float(chess2_canon_eps_white)),
                        "--eps-black", str(float(chess2_canon_eps_black)),
                        "--explore-moves-white", str(max(0, int(chess2_canon_explore_moves_white))),
                        "--explore-moves-black", str(max(0, int(chess2_canon_explore_moves_black))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_chess2_canon_daily_run,
                    env_overrides={
                        "OROMA_CHESS2_BOOTSTRAP_FROM_CHESS": "0",
                        "OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY": "1",
                        "OROMA_CHESS2_CANONICAL": "1",
                    },
                )
                _mark_daily(state, "chess2_canon_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess2 Canon + Cooperation self-play.
            #
            # Ziel:
            #   • game:chess2_canon bleibt der reine kanonische Mobility-Raum.
            #   • game:chess2_canon_coop erweitert diesen Raum um einen separaten
            #     Kooperations-Layer (Deckung/Mehrfachkontrolle/Synergie), ohne
            #     die bestehenden canon-Regeln zu überschreiben.
            if en_chess2_canon_coop_daily_run and _should_run_daily(state, "chess2_canon_coop_daily_run", chess2_canon_coop_daily_hh, chess2_canon_coop_daily_mm, jitter_daily, now):
                _run(
                    "chess2_canon_coop_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess2_canon_coop_daily_runner.py"),
                        "--policy-games", str(max(0, int(chess2_canon_policy_games))),
                        "--explore-games", str(max(0, int(chess2_canon_explore_games))),
                        "--max-plies", str(max(20, int(chess2_canon_max_plies))),
                        "--eps", str(float(chess2_canon_eps)),
                        "--eps-white", str(float(chess2_canon_eps_white)),
                        "--eps-black", str(float(chess2_canon_eps_black)),
                        "--explore-moves-white", str(max(0, int(chess2_canon_explore_moves_white))),
                        "--explore-moves-black", str(max(0, int(chess2_canon_explore_moves_black))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_chess2_canon_coop_daily_run,
                    env_overrides={
                        "OROMA_CHESS2_BOOTSTRAP_FROM_CHESS": "0",
                        "OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY": "1",
                        "OROMA_CHESS2_CANONICAL": "1",
                        "OROMA_CHESS2_COOPERATION": "1",
                    },
                )
                _mark_daily(state, "chess2_canon_coop_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess2 Canon + Cooperation + King + Territory self-play.
            #
            # Ziel:
            #   • game:chess2_canon_coop_king bleibt der gerichtete, aber noch
            #     eher defensive Raum mit Königszentrierung.
            #   • game:chess2_canon_coop_king_territory ergänzt diesen Raum um
            #     explizite Raumkontrolle / Feldherrschaft / Dominanz-Zonen,
            #     ohne die bestehenden King-Regeln zu überschreiben.
            if en_chess2_canon_coop_king_territory_daily_run and _should_run_daily(state, "chess2_canon_coop_king_territory_daily_run", chess2_canon_coop_king_territory_daily_hh, chess2_canon_coop_king_territory_daily_mm, jitter_daily, now):
                _run(
                    "chess2_canon_coop_king_territory_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "chess2_canon_coop_king_territory_daily_runner.py"),
                        "--policy-games", str(max(0, int(chess2_canon_coop_king_territory_policy_games))),
                        "--explore-games", str(max(0, int(chess2_canon_coop_king_territory_explore_games))),
                        "--max-plies", str(max(20, int(chess2_canon_coop_king_territory_max_plies))),
                        "--eps", str(float(chess2_canon_coop_king_territory_eps)),
                        "--eps-white", str(float(chess2_canon_coop_king_territory_eps_white)),
                        "--eps-black", str(float(chess2_canon_coop_king_territory_eps_black)),
                        "--explore-moves-white", str(max(0, int(chess2_canon_coop_king_territory_explore_moves_white))),
                        "--explore-moves-black", str(max(0, int(chess2_canon_coop_king_territory_explore_moves_black))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_chess2_canon_coop_king_territory_daily_run,
                    env_overrides={
                        "OROMA_CHESS2_BOOTSTRAP_FROM_CHESS": "0",
                        "OROMA_CHESS2_BOOTSTRAP_IF_EMPTY_ONLY": "1",
                        "OROMA_CHESS2_CANONICAL": "1",
                        "OROMA_CHESS2_COOPERATION": "1",
                        "OROMA_CHESS2_KING_WEIGHT": "1",
                        "OROMA_CHESS2_TERRITORY": "1",
                    },
                )
                _mark_daily(state, "chess2_canon_coop_king_territory_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess policy training from DB-traces.
            #
            # Ziel:
            #   • Chess bekommt – analog zu TicTacToe – einen expliziten, sichtbaren
            #     Nightly-Train-Schritt im Orchestrator.
            #   • Fachlich bleibt Chess experimenteller als TTT; infrastrukturell ist
            #     der Pfad aber jetzt gleichartig: Daily-Runner -> Train -> Export.
            if en_chess_policy_train and _should_run_daily(state, "chess_policy_train", chess_policy_train_hh, chess_policy_train_mm, jitter_daily, now):
                _run(
                    "chess_policy_train",
                    [
                        sys.executable,
                        "-m",
                        "core.policy_engine",
                        "--train-db",
                        "--limit",
                        "50000",
                        "--namespace",
                        "game:chess",
                    ],
                    timeout_sec=to_chess_policy_train,
                )
                _mark_daily(state, "chess_policy_train", now)
                _atomic_write_json(state_path, state)

            # Daily: Chess policy export to archive/rules.
            if en_chess_policy_export and _should_run_daily(state, "chess_policy_export", chess_policy_export_hh, chess_policy_export_mm, jitter_daily, now):
                _run(
                    "chess_policy_export",
                    [
                        sys.executable,
                        "-m",
                        "core.policy_engine",
                        "--export-archiv",
                        "--namespace",
                        "game:chess",
                        "--min-n",
                        "3",
                        "--min-abs-q",
                        "0.1",
                    ],
                    timeout_sec=to_chess_policy_export,
                )
                _mark_daily(state, "chess_policy_export", now)
                _atomic_write_json(state_path, state)

            # Daily: FlappyBird self-play (policy-benchmark + explore-learning)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_flappy_daily_run and _should_run_daily(state, "flappy_daily_run", flappy_daily_hh, flappy_daily_mm, jitter_daily, now):
                _run(
                    "flappy_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "flappy_daily_runner.py"),
                        "--policy-games", str(max(0, int(flappy_policy_games))),
                        "--explore-games", str(max(0, int(flappy_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", "game:flappy",
                    ],
                    timeout_sec=to_flappy_daily_run,
                )
                _mark_daily(state, "flappy_daily_run", now)
                _atomic_write_json(state_path, state)

            # MemoryMaze2033 legacy-retired:
            # Der alte Daily-Runner tools/memorymaze_daily_runner.py wird nicht
            # mehr automatisch gestartet. Produktive Nachfolger sind:
            #   • tools/memory_daily_runner.py         → memory:pro_v2 / mechanic_solved
            #   • tools/memorymaze_hybrid_daily_runner.py → MemoryMaze Hybrid
            # Historische episodes/episodic_metrics bleiben in der DB erhalten
            # und können weiterhin in der Daily Summary erscheinen. Hier wird
            # bewusst nur die Runner-Referenz entfernt; keine Datei wird gelöscht
            # und keine UI-/DB-Historie wird versteckt.
            if en_memorymaze_daily_run:
                # Sichtbarer, nicht-destruktiver Hinweis für alte ENV-Setups:
                # Selbst wenn OROMA_ORCH_ENABLE_MEMORYMAZE_DAILY_RUN=1 gesetzt
                # ist, wird der Legacy-Runner aus Ressourcengründen nicht mehr
                # durch den produktiven Orchestrator ausgeführt.
                pass

            # Daily: MemoryMaze Hybrid (PacMan-Maze + Memory-Blocker) – normal + hard_p3
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            # Hard-P3 ist separat wählbar und wird hier produktiv mit kleinerem Anteil gefahren.
            if en_memorymaze_hybrid_daily_run and _should_run_daily(state, "memorymaze_hybrid_daily_run", memorymaze_hybrid_daily_hh, memorymaze_hybrid_daily_mm, jitter_daily, now):
                _run(
                    "memorymaze_hybrid_daily_run_normal",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "memorymaze_hybrid_daily_runner.py"),
                        "--mode", "normal",
                        "--map", str(mmzh_map),
                        "--policy-games", str(max(0, int(mmzh_normal_policy_games))),
                        "--explore-games", str(max(0, int(mmzh_normal_explore_games))),
                        "--eps", str(float(mmzh_eps)),
                        "--max-steps", str(max(50, int(mmzh_max_steps))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_memorymaze_hybrid_daily_run,
                )
                _run(
                    "memorymaze_hybrid_daily_run_hard_p3",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "memorymaze_hybrid_daily_runner.py"),
                        "--mode", "hard_p3",
                        "--map", str(mmzh_map),
                        "--policy-games", str(max(0, int(mmzh_hard_policy_games))),
                        "--explore-games", str(max(0, int(mmzh_hard_explore_games))),
                        "--eps", str(float(mmzh_eps)),
                        "--max-steps", str(max(50, int(mmzh_max_steps))),
                        "--seed", str((int(now_ts) + 1337) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_memorymaze_hybrid_daily_run,
                )
                _mark_daily(state, "memorymaze_hybrid_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Hide&Seek self-play (policy-benchmark + explore-learning)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_hideseek_daily_run and _should_run_daily(state, "hideseek_daily_run", hideseek_daily_hh, hideseek_daily_mm, jitter_daily, now):
                _run(
                    "hideseek_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "hideseek_daily_runner.py"),
                        "--policy-games", str(max(0, int(hideseek_policy_games))),
                        "--explore-games", str(max(0, int(hideseek_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                        "--namespace", str(hideseek_namespace),
                        "--max-steps", str(max(10, int(hideseek_max_steps))),
                    ],
                    timeout_sec=to_hideseek_daily_run,
                )
                _mark_daily(state, "hideseek_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: Capture The Flag (CTF) self-play (policy-benchmark + explore-learning)
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_ctf_daily_run and _should_run_daily(state, "ctf_daily_run", ctf_daily_hh, ctf_daily_mm, jitter_daily, now):
                _run(
                    "ctf_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "ctf_daily_runner.py"),
                        "--policy-games", str(max(0, int(ctf_policy_games))),
                        "--explore-games", str(max(0, int(ctf_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_ctf_daily_run,
                )
                _mark_daily(state, "ctf_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: PTZ Arena (real hardware) policy+explore
            #
            # Ziel:
            #   - PTZ als "Spiel" (UniversalPolicy Namespace ptz:arena)
            #   - mechanisch schonend: Step-Rate und Episode-Länge sind klein
            #
            # Ergebnis: episodes + episodic_metrics in oroma.db
            if en_ptz_arena_daily_run and _should_run_daily(state, "ptz_arena_daily_run", ptz_arena_daily_hh, ptz_arena_daily_mm, jitter_daily, now):
                _run(
                    "ptz_arena_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "ptz_arena_daily_runner.py"),
                        "--policy-games", str(max(0, int(ptz_arena_policy_games))),
                        "--explore-games", str(max(0, int(ptz_arena_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_ptz_arena_daily_run,
                )
                _mark_daily(state, "ptz_arena_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: PTZ Targeting (motion-centroid -> center/hold)
            # - separater Namespace: ptz:target
            # - Ziel: schnellerer Lern-Boost als reine "sharp/motion" Heuristik
            if en_ptz_target_daily_run and _should_run_daily(state, "ptz_target_daily_run", ptz_target_daily_hh, ptz_target_daily_mm, jitter_daily, now):
                _run(
                    "ptz_target_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "ptz_target_daily_runner.py"),
                        "--policy-games", str(max(0, int(ptz_target_policy_games))),
                        "--explore-games", str(max(0, int(ptz_target_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_ptz_target_daily_run,
                )
                _mark_daily(state, "ptz_target_daily_run", now)
                _atomic_write_json(state_path, state)

            # Daily: PTZ Coverage (Staubsauger-Sweep)
            # - Namespace: ptz:coverage (persistenter Zellzustand in stats.db)
            # - Ziel: systematisch verschiedene Blickwinkel besuchen (Coverage)
            # - Default klein (Hardware, real-time)
            if en_ptz_coverage_daily_run and _should_run_daily(state, "ptz_coverage_daily_run", ptz_coverage_daily_hh, ptz_coverage_daily_mm, jitter_daily, now):
                _run(
                    "ptz_coverage_daily_run",
                    [
                        sys.executable,
                        os.path.join(base, "tools", "ptz_coverage_daily_runner.py"),
                        "--policy-games", str(max(0, int(ptz_coverage_policy_games))),
                        "--explore-games", str(max(0, int(ptz_coverage_explore_games))),
                        "--seed", str(int(now_ts) & 0xFFFFFFFF),
                    ],
                    timeout_sec=to_ptz_coverage_daily_run,
                )
                _mark_daily(state, "ptz_coverage_daily_run", now)
                _atomic_write_json(state_path, state)
            if en_export and _should_run_daily(state, "exportgate", 3, 15, jitter_daily, now):
                _run("exportgate", [sys.executable, "-m", "core.export_gate"], timeout_sec=to_exportgate)
                _mark_daily(state, "exportgate", now)
                _atomic_write_json(state_path, state)

            # Monthly
            if en_arch and _should_run_monthly(state, "archive", 1, 3, 15, jitter_monthly, now):
                _run("archive", ["/usr/bin/env", "bash", "-lc", os.path.join(base, "tools", "monthly_archive.sh")], timeout_sec=to_archive)
                _mark_monthly(state, "archive", now)
                _atomic_write_json(state_path, state)

            if en_forget and _should_run_monthly(state, "forgetting", 1, 3, 25, jitter_monthly, now):
                _run("forgetting", [sys.executable, "-m", "core.forgetting_worker"], timeout_sec=to_forgetting)
                _mark_monthly(state, "forgetting", now)
                _atomic_write_json(state_path, state)

        except Exception as e:
            # Orchestrator darf nicht sterben wegen eines einzelnen Jobs.
            with open(log_err, "a", encoding="utf-8") as fe:
                fe.write(f"[ORCH] LOOP EXCEPTION: {e!r}\n")

        if once:
            return 0

        time.sleep(max(1, int(tick)))


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA job orchestrator (Mode B)")
    ap.add_argument("--once", action="store_true", help="Run due jobs once and exit")
    args = ap.parse_args()

    lock_path = os.getenv("OROMA_ORCH_LOCK", "/tmp/oroma_orchestrator.lock")
    try:
        with _GlobalLock(lock_path):
            try:
                return run_due_jobs(once=bool(args.once))
            except KeyboardInterrupt:
                # Ctrl+C soll nicht als hässlicher Stacktrace im Terminal enden.
                # Wichtig: kein "silent" – wir loggen sichtbar.
                try:
                    sys.stderr.write("[oroma_orchestrator] Abgebrochen durch KeyboardInterrupt (Ctrl+C).\n")
                except Exception:
                    pass
                return 130
    except RuntimeError as e:
        # already running
        sys.stderr.write(f"[oroma_orchestrator] {e}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

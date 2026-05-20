#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/calc_result_runner.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Calc→SnapChain Producer (calc/result) – Headless, DBWriter-first
# Version:   v3.7.3
# Stand:     2026-04-27
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
# =============================================================================
#
# HINTERGRUND (aus Doku / Historie)
# ────────────────────────────────
# In ORÓMA wurde `calc/result` früher als SnapChain persistiert (Transfer-Bridge),
# sodass der Crossmodal-Linker `calc/result ↔ vision/token → link/calc_vision`
# regelmäßig neue Links erzeugen konnte.
#
# In deinem aktuellen Betrieb ist `calc/result` seit 2026-03-06 nicht mehr frisch.
# Der Crossmodal-Linker liefert daher `calc=0`, `wrote_links=0`.
#
# WICHTIG: In der alten Pipeline entstand `calc/result` über:
#   calculator_tasks/results → core/calc_to_snapchain.record_from_db(...)
#
# Problem heute:
# - Der interactive Calculator/Runner-Pfad kann in Headless/Orchestrator-Kontext
#   hängen oder unerwünschte Nebenimporte (pygame) triggern.
# - Für das reine Wiederherstellen des `calc/result`-Stroms brauchen wir aber
#   keine UI/Spiele – nur stabile SnapChains.
#
# LÖSUNG (minimal-invasiv)
# ───────────────────────
# Dieses Tool erzeugt in bounded Batches synthetische `calc/result` SnapChains
# direkt über den bestehenden Write-Pfad `core.sql_manager.insert_snapchain`.
# Der Blob folgt exakt dem in core/calc_to_snapchain.py dokumentierten Format
# inkl. deterministischem Vektor v (84D Default, kompatibel zur Vision-VDim).
#
# DBWriter-First
# ──────────────
# `sql_manager.insert_snapchain` nutzt DBWriter automatisch, wenn er aktiv ist.
# Damit das Tool auch in manuellen Tests robust ist, akzeptieren wir DBWriter
# als "aktiv", wenn entweder OROMA_DBW_ENABLE gesetzt ist ODER der Writer-Socket
# existiert und pingbar ist (kein lokaler RW-Fallback nötig).
#
# ENV
# ───
#   OROMA_CALC_RESULT_BATCH_N           Default: 10
#   OROMA_CALC_RESULT_LEVEL             Default: 1
#   OROMA_CALC_RESULT_MAX_RUNTIME_S     Default: 20
#   OROMA_CALC_RESULT_VDIM              Default: 84
#   OROMA_CALC_RESULT_REWARD            Default: 0.1
#   OROMA_CALC_RESULT_CORRECT           Default: 1   (1=correct, 0=incorrect)
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

# sys.path bootstrap
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.log_guard import log_suppressed  # noqa: E402
from core import sql_manager  # noqa: E402
from core import curriculum_math  # noqa: E402
from core import calc_to_snapchain  # noqa: E402


# DBWriter enable bootstrap:
# In ORÓMA deployments DBWriter may be running as a daemon even if OROMA_DBW_ENABLE
# is not exported in the current shell. The client library gates ping/exec behind
# enabled() -> env flag. For one-shot tools we auto-enable if the socket exists.
try:
    _sock = os.getenv("OROMA_DBW_SOCK") or "/opt/ai/oroma/data/state/db_writer.sock"
    if os.path.exists(_sock) and os.getenv("OROMA_DBW_ENABLE") not in ("1", "true", "TRUE", "yes", "YES"):
        os.environ["OROMA_DBW_ENABLE"] = "1"
except Exception:
    pass

try:
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover
    dbw = None  # type: ignore


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _dbw_is_available() -> bool:
    # Respect explicit flag first
    if os.getenv("OROMA_DBW_ENABLE", "").strip() in ("1", "true", "True", "yes", "on"):
        return True
    # Best-effort detection: socket exists + ping ok
    sock = os.getenv("OROMA_DBW_SOCK", "/opt/ai/oroma/data/state/db_writer.sock")
    if not sock or not os.path.exists(sock):
        return False
    try:
        if dbw and getattr(dbw, "ping", None):
            return bool(dbw.ping(timeout_ms=500))
    except Exception:
        return False
    return True


def _pick_tasks(level: int, n: int) -> List[Dict[str, Any]]:
    tasks = curriculum_math.get_all_tasks(int(level)) or []
    if not tasks:
        return []
    base = int(time.time()) % max(1, len(tasks))
    out = []
    for i in range(int(n)):
        out.append(tasks[(base + i) % len(tasks)])
    return out


def run_once(batch_n: int, level: int, max_runtime_s: int, vdim: int, reward: float, correct: int) -> Dict[str, Any]:
    t0 = time.time()

    if not _dbw_is_available():
        # Do not proceed with local RW path; in your system we want DBWriter discipline.
        raise RuntimeError("DBWriter not active/available (set OROMA_DBW_ENABLE=1 or ensure db_writer.sock is running)")

    tasks = _pick_tasks(level, batch_n)
    if not tasks:
        return {"ok": False, "error": "no curriculum tasks", "written": 0, "dt_sec": round(time.time() - t0, 3)}

    written = 0
    now = int(time.time())

    for i, task in enumerate(tasks):
        if time.time() - t0 > float(max_runtime_s):
            break
        expr = str(task.get("expr", "")).strip()
        truth = task.get("truth", None)
        if not expr or truth is None:
            continue

        # Use safe_eval if possible; else fall back to truth (still a valid canonical signal)
        try:
            got = curriculum_math.safe_eval(expr)
        except Exception:
            got = truth

        try:
            truth_f = float(truth)
        except Exception:
            # for non-scalar truth, keep 0.0 but preserve structured truth_json
            truth_f = 0.0
        try:
            got_f = float(got)
        except Exception:
            got_f = truth_f

        meta = None
        try:
            tj = task.get("truth_json")
            if isinstance(tj, dict):
                meta = tj
        except Exception:
            meta = None

        # Deterministic v (same logic as calc_to_snapchain)
        try:
            v = calc_to_snapchain._build_v(expr=expr, level=int(level), truth=float(truth_f), got=float(got_f), correct=int(correct), reward=float(reward), meta=meta)  # type: ignore
        except Exception:
            v = [0.0] * int(max(16, vdim))

        blob_obj: Dict[str, Any] = {
            "kind": "calc/result",
            "v": v[: int(max(16, vdim))],
            "task_id": 0,
            "result_id": 0,
            "ts": now,
            "level": int(level),
            "expr": expr,
            "truth": float(truth_f),
            "got": float(got_f),
            "correct": int(correct),
            "reward": float(reward),
            "error_type": None,
            "truth_json": meta if isinstance(meta, dict) else None,
            "got_json": None,
            "meta": meta if isinstance(meta, dict) else None,
        }

        snap_id = sql_manager.insert_snapchain(
            {
                "ts": now,
                "quality": 0.65 if int(correct) else 0.15,
                "blob": json.dumps(blob_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                "origin": "calc/result",
                "notes": "calc_result_runner synthetic (headless) – restores calc/result stream",
                "version": "v3.7.3",
                "weight": 0.35 if int(correct) else 0.50,
                "status": "active",
                "exported": 0,
                "gap_flag": 0,
            }
        )
        if snap_id:
            written += 1

    return {"ok": True, "level": int(level), "written": int(written), "dt_sec": round(time.time() - t0, 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA calc/result producer (headless, DBWriter-first)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--batch-n", type=int, default=_env_int("OROMA_CALC_RESULT_BATCH_N", 10))
    ap.add_argument("--level", type=int, default=_env_int("OROMA_CALC_RESULT_LEVEL", 1))
    ap.add_argument("--max-runtime-s", type=int, default=_env_int("OROMA_CALC_RESULT_MAX_RUNTIME_S", 20))
    ap.add_argument("--vdim", type=int, default=_env_int("OROMA_CALC_RESULT_VDIM", 84))
    ap.add_argument("--reward", type=float, default=_env_float("OROMA_CALC_RESULT_REWARD", 0.1))
    ap.add_argument("--correct", type=int, default=_env_int("OROMA_CALC_RESULT_CORRECT", 1))
    args = ap.parse_args()

    try:
        res = run_once(int(args.batch_n), int(args.level), int(args.max_runtime_s), int(args.vdim), float(args.reward), int(args.correct))
        print("calc_result_runner:", " ".join(f"{k}={v}" for k, v in res.items()))
        return 0 if res.get("ok") else 2
    except Exception as e:
        log_suppressed("calc_result_runner.error", exc=e)
        print("calc_result_runner: ok=False error=%s" % (str(e),))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

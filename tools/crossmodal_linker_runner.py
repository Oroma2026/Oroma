#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/crossmodal_linker_runner.py
# Projekt:   ORÓMA (Offline-First · Headless · Orchestrator-ready)
# Modul:     Crossmodal-Linker Runner (Batch)
# Version:   v3.7.4
# Stand:     2026-02-27
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / SYSTEMROLLE
# ──────────────────
# Dieses Tool führt den Crossmodal-Linker (Calculator↔Vision) als BATCH aus.
#
# Hintergrund:
#   In ORÓMA wird calc↔vision Linking üblicherweise als Hook im AgentLoop
#   (core/agent_loop.py) registriert. Im Orchestrator-Betrieb ist der AgentLoop
#   jedoch häufig deaktiviert (OROMA_AGENT_ENABLED=false), während trotzdem
#   weiterhin SnapChains aus Vision/Calc entstehen. In diesem Modus kann die
#   Linking-Rate (und damit "binding") auf 0 fallen, obwohl die Inputs vorhanden
#   sind.
#
# Lösung:
#   Dieses Tool ruft core.calc_vision_linker.link_window_now() einmalig auf und
#   schreibt neue Link-SnapChains mit origin="link/calc_vision" in oroma.db.
#   Es ist bewusst DB-schonend:
#     - Limit / Window / Freshness sind im Linker selbst konfigurierbar
#       (siehe core/calc_vision_linker.py ENV-Header).
#
# WICHTIG (Sichtbarkeit / Keine stillen Fehler)
# ────────────────────────────────────────────
# - Exceptions werden als Traceback nach stderr ausgegeben und führen zu rc!=0.
# - Ein "0 Links" Ergebnis ist *kein* Fehler, wird aber klar geloggt.
#
# AUFRUF
# ──────
#   python3 /opt/ai/oroma/tools/crossmodal_linker_runner.py --once
#
# ORCHESTRATOR
# ────────────
# Wird typischerweise vom Orchestrator aufgerufen:
#   tools/oroma_orchestrator.py → Job "crossmodal_linker"
#
# ENV (im Linker ausgewertet)
# ──────────────────────────
#   OROMA_CROSSMODAL_LINKS                  (1|0)    Default: 1
#   OROMA_CROSSMODAL_LINK_EVERY_TICKS       (int)    Default: 10   (Hook-Mode)
#   OROMA_CROSSMODAL_LINK_WINDOW_SEC        (int)    Default: 10
#   OROMA_CROSSMODAL_LINK_LIMIT             (int)    Default: 40
#   OROMA_CROSSMODAL_LINK_MIN_SCORE         (float)  Default: -1.0
#   OROMA_CROSSMODAL_LINK_STRICT_MAX_DT_SEC (int)    Default: 120
#   OROMA_CROSSMODAL_LINK_REQUIRE_FRESH_VISION (1|0) Default: 1
#   OROMA_CROSSMODAL_LINK_FRESH_VISION_SEC     (int) Default: 300
#   OROMA_CROSSMODAL_LINK_LOOKBACK_SEC         (int) Default: 3600  (Batch/Backfill)
#   OROMA_CROSSMODAL_LINK_BACKFILL_MAX_N       (int) Default: 5000 (Batch/Backfill)
#
# EXITCODES
# ─────────
#   0  Erfolg (auch wenn 0 Links geschrieben wurden)
#   2  Fehler (Exception)
# =============================================================================

from __future__ import annotations

import argparse
import os
import sys
import traceback
import time
import json


def _write_report(path: str, payload: dict) -> None:
    """Best-effort: persist last run summary for observability.

    This is intentionally independent from orchestrator.out.log rotation/truncation.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # do not fail the job because of reporting issues
        return


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Crossmodal-Linker Runner (calc↔vision)")
    ap.add_argument("--once", action="store_true", help="Einmaliger Lauf (Default).")
    ap.add_argument("--lookback-sec", type=int, default=int(os.getenv("OROMA_CROSSMODAL_LINK_LOOKBACK_SEC", "3600") or 3600),
                    help="Lookback-Fenster in Sekunden (Batch/Backfill). Default via OROMA_CROSSMODAL_LINK_LOOKBACK_SEC.")
    ap.add_argument("--max-n", type=int, default=int(os.getenv("OROMA_CROSSMODAL_LINK_BACKFILL_MAX_N", "5000") or 5000),
                    help="Max. Anzahl Rows pro Origin im Backfill (DB-schonend). Default via OROMA_CROSSMODAL_LINK_BACKFILL_MAX_N.")
    args = ap.parse_args(argv)

    t0 = time.time()
    try:
        # Lazy import, damit Tracebacks sauber sind (ImportError sichtbar).
        from core import calc_vision_linker

        stats = None
        if hasattr(calc_vision_linker, "link_backfill"):
            stats = calc_vision_linker.link_backfill(lookback_sec=int(args.lookback_sec), max_n=int(args.max_n))
            n = int(stats.get("created", 0))
        else:
            n = int(calc_vision_linker.link_window_now())  # legacy
        dt = time.time() - t0
        # stdout: orchestrator.out.log
        if stats:
            print(
                "crossmodal_linker_runner: wrote_links=%d matched=%d calc=%d vision=%d lookback=%ds window=%ds dt_sec=%.3f"
                % (
                    n,
                    int(stats.get("matched", 0)),
                    int(stats.get("calc", 0)),
                    int(stats.get("vision", 0)),
                    int(stats.get("lookback", 0)),
                    int(stats.get("window", 0)),
                    dt,
                )
            )
        else:
            print("crossmodal_linker_runner: wrote_links=%d dt_sec=%.3f" % (n, dt))

        report_path = os.getenv("OROMA_CROSSMODAL_LINK_REPORT", "/opt/ai/oroma/state/crossmodal_linker_last.json")
        _write_report(
            report_path,
            {
                "ok": True,
                "ts_run": int(time.time()),
                "wrote_links": int(n),
                "matched": int(stats.get("matched", 0)) if stats else None,
                "calc": int(stats.get("calc", 0)) if stats else None,
                "vision": int(stats.get("vision", 0)) if stats else None,
                "lookback_sec": int(args.lookback_sec),
                "window_sec": int(stats.get("window", 0)) if stats else None,
                "dt_sec": float(dt),
            },
        )
        return 0
    except Exception:
        dt = time.time() - t0
        print("crossmodal_linker_runner: ERROR after %.3fs" % dt, file=sys.stderr)
        traceback.print_exc()
        report_path = os.getenv("OROMA_CROSSMODAL_LINK_REPORT", "/opt/ai/oroma/state/crossmodal_linker_last.json")
        _write_report(
            report_path,
            {
                "ok": False,
                "ts_run": int(time.time()),
                "wrote_links": None,
                "lookback_sec": int(getattr(args, "lookback_sec", 0) or 0) if "args" in locals() else None,
                "dt_sec": float(dt),
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

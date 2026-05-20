#!/usr/bin/env python3
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/forgetting_sampler.py
# Projekt: ORÓMA – Forgetting/Kompression History Sampler
# Version: v3.7.3
# Stand:   2026-02-19
# Autor:   Jörg + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
# Dieses Tool erzeugt periodisch (typisch via Orchestrator) einen *History-Point*
# für die Forgetting/Kompression-Ansicht, ohne dass die UI offen sein muss.
#
# Hintergrund
# -----------
# Die UI (/forgetting) zeigt aktuelle Kennzahlen (avg/p90/p99/top-k + counts).
# Historische Charts/Tabelle basieren auf `stats.db:stats_points`.
#
# WICHTIG: Die UI soll NICHT automatisch im Hintergrund samplen, nur weil sie
# geöffnet ist. Das Sampling soll entweder:
#   (a) durch den Orchestrator in festen Intervallen erfolgen (Default 6h), oder
#   (b) manuell per Button/Endpoint ausgelöst werden.
#
# Vorgehen
# --------
# - Ruft lokal das HTTP-Endpoint `/forgetting/api/state?sample=1` auf.
# - Das Endpoint berechnet den aktuellen State und schreibt *best-effort* einen
#   Snapshot in `stats_points`.
#
# Vorteile
# --------
# - Kein doppelter SQL-Code im Sampler.
# - Gleiche Berechnungslogik wie in der UI.
# - Respektiert Token-Policy (optional über OROMA_UI_TOKEN).
#
# Environment
# -----------
# OROMA_BASE_DIR   (default: /opt/ai/oroma)
# OROMA_HTTP_HOST  (default: 127.0.0.1)
# OROMA_HTTP_PORT  (default: 8080)
# OROMA_UI_TOKEN   (optional; wenn gesetzt, wird ?token=... verwendet)
#
# Nutzung
# ------
#   sudo -u oroma PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/tools/forgetting_sampler.py --once
#
# Exit Codes
# ----------
# 0 = ok
# 2 = HTTP/Network Fehler
# 3 = Response nicht ok
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _build_url() -> str:
    host = os.environ.get("OROMA_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("OROMA_HTTP_PORT", "8080"))
    token = os.environ.get("OROMA_UI_TOKEN", "").strip()

    q = {"sample": "1"}
    if token:
        q["token"] = token

    return f"http://{host}:{port}/forgetting/api/state?{urllib.parse.urlencode(q)}"


def sample_once(timeout_sec: float = 8.0) -> dict:
    url = _build_url()
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_sec) as r:
        raw = r.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            raise RuntimeError(f"invalid json from {url}: {raw[:200]}")
        return data


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Sample exactly once and exit")
    ap.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds")
    args = ap.parse_args(argv)

    if not args.once:
        ap.error("only --once is supported")

    try:
        data = sample_once(timeout_sec=float(args.timeout))
    except urllib.error.URLError as e:
        print(f"[forgetting_sampler] HTTP error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[forgetting_sampler] error: {e}", file=sys.stderr)
        return 2

    if not isinstance(data, dict) or not data.get("ok"):
        print(f"[forgetting_sampler] not ok: {data}", file=sys.stderr)
        return 3

    # Minimal, stable log line (orchestrator-friendly)
    print(
        "[forgetting_sampler] ok "
        f"avg={data.get('avg_quality')} p90={data.get('p90_quality')} p99={data.get('p99_quality')} "
        f"active={data.get('n_active')} compressed={data.get('n_compressed')} rate={data.get('compression_rate')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

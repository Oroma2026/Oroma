#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/learning_cache_refresh.py
# Projekt:   ORÓMA (Orchestrator Tool · Learning Cache Refresher)
# Version:   v3.7.3
# Stand:     2026-01-13
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool refresht die Learning-UI Caches, sodass das Dashboard ohne lange
# SQL-Aggregationen sofort antwortet.
#
# Hintergrund (Produktion):
# - /learning/api/maxima kann bei großen stats_points Datenmengen mehrere Minuten
#   dauern (Window-Functions + Period-Aggregation).
# - /learning/api/intelligence ist meist schnell, soll aber ebenfalls „instant“
#   sein und nicht am Live-System „zerren“.
#
# Design:
# - keine DB-Schema-Änderungen
# - nutzt die bestehenden Endpoints mit force=1, die wiederum in stats.db
#   (energy_top_cache) unter:
#     - kind="learning:maxima"
#     - kind="learning:intelligence"
#   ablegen.
# - läuft idealerweise periodisch (z.B. via oroma_orchestrator.py Interval-Job)
#
# ENV
# ---
#   OROMA_BASE_URL           Default: http://127.0.0.1:8080
#   OROMA_LEARNING_CACHE_MAX_AGE_SEC  (nur Info; Cache wird per force erneuert)
#
# USAGE
# -----
#   python3 /opt/ai/oroma/tools/learning_cache_refresh.py
#
# Exit Codes:
#   0  ok
#   2  partial (mind. eine der beiden Refreshes fehlgeschlagen)
#   3  total failure
#
# =============================================================================

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error


def _now() -> int:
    return int(time.time())


def _http_get_json(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "oroma-learning-cache/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", "ignore") or "{}")

def _http_drain(url: str, timeout: float = 60.0) -> int:
    """GET an endpoint and drain the response body without storing it in memory.

    Used for CSV endpoints (e.g., /learning/api/export.csv) where the side effect is
    important (writing the export cache to disk), but we do not want to keep the
    full payload in RAM. Returns the number of bytes drained.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "OROMA/learning_cache_refresh"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = 0
        while True:
            chunk = r.read(8192)
            if not chunk:
                break
            total += len(chunk)
        return total


def main() -> int:
    base = os.environ.get("OROMA_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    ts0 = _now()

    out = {
        "ok": False,
        "ts": ts0,
        "base_url": base,
        "results": {},
        "errors": [],
    }

    # Endpoints (force=1 schreibt Cache)
    urls = {
        "intelligence": f"{base}/learning/api/intelligence?force=1",
        "maxima": f"{base}/learning/api/maxima?force=1&days=365&take_day=14&take_week=12&take_month=12",
        "export_all": f"{base}/learning/api/export.csv?cached=0&raw=0",
    }

    ok_any = False
    ok_all = True

    for k, url in urls.items():
        t1 = time.time()
        try:
            if k == "export_all":
                n_bytes = _http_drain(url, timeout=120.0)
                dt = round(time.time() - t1, 3)
                out["results"][k] = {
                    "ok": bool(n_bytes > 0),
                    "dt_sec": dt,
                    "bytes": int(n_bytes),
                }
                ok_any = ok_any or bool(n_bytes > 0)
                ok_all = ok_all and bool(n_bytes > 0)
            else:
                payload = _http_get_json(url, timeout=45.0)
                dt = round(time.time() - t1, 3)
                out["results"][k] = {
                    "ok": bool(payload.get("ok", False)),
                    "dt_sec": dt,
                    "meta": payload.get("meta", {}),
                }
                ok_any = ok_any or bool(payload.get("ok", False))
                ok_all = ok_all and bool(payload.get("ok", False))
        except urllib.error.HTTPError as e:
            ok_all = False
            out["errors"].append(f"{k} HTTPError: {e.code} {e.reason}")
        except Exception as e:
            ok_all = False
            out["errors"].append(f"{k} failed: {e!r}")

    out["ok"] = ok_all
    out["dt_total_sec"] = round(time.time() - ts0, 3)

    print(json.dumps(out, ensure_ascii=False))
    if ok_all:
        return 0
    if ok_any:
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

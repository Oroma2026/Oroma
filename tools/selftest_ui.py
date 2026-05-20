#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/selftest_ui.py
# Projekt: ORÓMA – UI-Selftest (erweiterte Ausgabe)
# Version: v3.8-r2
# Stand:   2025-12-08
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Erweiterter UI-Selftest für ORÓMA:
#
#   - Ruft zentrale UI-Routen auf (Index, Spiele, Modelle, Lernen, Control …).
#   - Bewertet HTTP-Status (OK / AUTH / WARN / FAIL).
#   - Ausführliche CLI-Ausgabe, geeignet für manuelle Diagnose.
#
#   Angepasste Routen (Stand v3.8):
#     • /overlay wird nicht mehr getestet (alte Overlay-Route, entfernt).
#     • /api/agent/status wird nicht mehr getestet (nicht implementiert).
#     • /video darf 503 liefern, wenn Video-Backend nicht aktiv ist → WARN, kein FAIL.
#
# ENV
# ───
#   - OROMA_UI_URL       : Basis-URL (z. B. http://127.0.0.1:5000)
#   - OROMA_UI_HOST      : Host (Fallback, Default: 127.0.0.1)
#   - OROMA_UI_PORT      : Port (Fallback, Default: 5000)
#   - OROMA_UI_TOKEN     : Optionaler Bearer-Token für Authorization
#
# Nutzung
# ───────
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma python3 tools/selftest_ui.py
#
#   Exit-Codes:
#     0 → alle Routen OK/WARN/AUTH
#     1 → mindestens eine Route FAIL
# =============================================================================

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

import requests


# ------------------------------ Konfiguration --------------------------------

ROUTES: List[str] = [
    "/",             # Index
    "/games",        # Spiele
    "/models",       # Modelle
    "/learning",     # Lernkurve
    "/control",      # Steuerung
    "/episodic",     # Episodisches Gedächtnis
    "/why",          # Explainability
    "/synapses",     # Synapsen-Graph
    "/memory",       # Memory (falls aktiv)
    "/video",        # Video-UI
    "/knowledge",    # Knowledge Import
    "/ask",          # Ask-UI
    "/chat",         # Chat-UI
    "/export",       # Export
    "/import",       # Import
    "/health",       # Health
    "/gaps",         # Knowledge-Gaps
    "/dream",        # Dream-UI
    "/replay",       # Replay-UI
]


def get_base_url() -> str:
    """
    Liefert die Basis-URL der ORÓMA-UI aus ENV oder Default.
    """
    url = os.environ.get("OROMA_UI_URL")
    if url:
        return url.rstrip("/")

    host = os.environ.get("OROMA_UI_HOST", "127.0.0.1")
    # Default-Port an dein System angepasst (UI läuft auf 8080)
    port = os.environ.get("OROMA_UI_PORT", "8080")
    return f"http://{host}:{port}".rstrip("/")



def get_auth_headers() -> Dict[str, str]:
    """
    Erzeugt optional Authorization-Header aus OROMA_UI_TOKEN.
    """
    token = os.environ.get("OROMA_UI_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


# ------------------------------ HTTP-Helper ----------------------------------


def http_get(
    session: requests.Session,
    url: str,
    timeout: float = 3.0,
) -> Tuple[Optional[int], Optional[str]]:
    """
    GET-Request mit einfachem Fehler-Handling.
    Liefert (Statuscode, Fehlertext) zurück.
    """
    headers = get_auth_headers()
    try:
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return resp.status_code, None
    except requests.exceptions.RequestException as e:
        return None, str(e)


def classify_status(route: str, status: Optional[int]) -> str:
    """
    Klassifiziert den Status:
      - OK   : 200, 302
      - AUTH : 401, 403
      - WARN : 503 bei /video oder /video/stream
      - FAIL : alles andere oder None
    """
    if status is None:
        return "FAIL"

    if status in (200, 302):
        return "OK"

    if status in (401, 403):
        return "AUTH"

    if status == 503 and route in ("/video", "/video/stream"):
        return "WARN"

    return "FAIL"


# ------------------------------ Hauptlogik -----------------------------------


def run_selftest() -> int:
    base_url = get_base_url()
    print("[selftest_ui] Base-URL:", base_url)

    session = requests.Session()

    had_fail = False
    summary: Dict[str, int] = {"OK": 0, "AUTH": 0, "WARN": 0, "FAIL": 0}

    for route in ROUTES:
        url = f"{base_url}{route}"
        status, err = http_get(session, url)

        kind = classify_status(route, status)
        summary[kind] = summary.get(kind, 0) + 1

        if kind == "OK":
            print(f"[OK]   {route:<20} -> {status}")
        elif kind == "AUTH":
            print(f"[AUTH] {route:<20} -> {status} (Auth benötigt)")
        elif kind == "WARN":
            print(f"[WARN] {route:<20} -> {status} (Dienst nicht aktiv, erwartbar)")
        else:
            had_fail = True
            if status is None:
                print(f"[FAIL] {route:<20} -> keine Antwort ({err})")
            else:
                print(f"[FAIL] {route:<20} -> {status}")

    print("\n[selftest_ui] Zusammenfassung:")
    print(f"  OK   : {summary.get('OK', 0)}")
    print(f"  AUTH : {summary.get('AUTH', 0)}")
    print(f"  WARN : {summary.get('WARN', 0)}")
    print(f"  FAIL : {summary.get('FAIL', 0)}")

    if had_fail:
        print("[selftest_ui] Ergebnis: ❌ Einige Routen sind fehlgeschlagen.")
        return 1

    print("[selftest_ui] Ergebnis: ✅ Alle geprüften Routen erreichbar (OK/WARN/AUTH).")
    return 0


def main() -> int:
    try:
        return run_selftest()
    except KeyboardInterrupt:
        print("\n[selftest_ui] Abgebrochen durch Benutzer.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/ui_selftest.py
# Projekt: ORÓMA – UI-Selftest (Routen-Smoke-Test)
# Version: v3.8-r2
# Stand:   2025-12-08
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Dieses Skript führt einen einfachen HTTP-Selftest gegen die Flask-UI von
#   ORÓMA durch. Es ruft eine feste Menge von Routen auf und prüft den
#   HTTP-Status.
#
#   Fokus:
#     - Startseite & Kern-Tabs (/, /games, /models, /learning, /control, …)
#     - UI-Seiten für Dream / Replay / Export / Import
#     - Health-API (/health, /api/health)
#
#   Nicht mehr geprüft:
#     - /overlay (alte Overlay-Route, im aktuellen Headless-Design entfernt)
#     - /api/agent/status (war nie implementiert, erzeugt nur 404-Noise)
#
# Besonderheiten
# ──────────────
#   - Base-URL wird über ENV gesetzt:
#       • OROMA_UI_URL (z. B. "http://127.0.0.1:5000")
#       • alternativ OROMA_UI_HOST / OROMA_UI_PORT
#       • Fallback: http://127.0.0.1:5000
#   - Optionaler Auth-Header über ENV:
#       • OROMA_UI_TOKEN  → Authorization: Bearer <TOKEN>
#
# Bewertung der Routen
# ────────────────────
#   - 200 / 302 → [OK]
#   - 401 / 403 → [AUTH] (vermutlich Token/Passwort nötig)
#   - 503 bei /video → [WARN] (Video-Backend nicht aktiv, erwartbar)
#   - alles andere → [FAIL]
#
# Nutzung
# ───────
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma python3 tools/ui_selftest.py
#
#   Exit-Codes:
#     0 → alle Routen OK/WARN/AUTH, kein harter FAIL
#     1 → mindestens eine Route [FAIL]
# =============================================================================

from __future__ import annotations

import os
import sys
import textwrap
from typing import Dict, List, Optional, Tuple

import requests


def wait_for_ui(
    session: requests.Session,
    base_url: str,
    total_wait_s: float = 15.0,
    # ---------------------------------------------------------------------
    # Compat-Alias
    # ---------------------------------------------------------------------
    # In der Praxis wurde diese Funktion in verschiedenen ZIP-Ständen
    # uneinheitlich aufgerufen:
    #   - ältere Versionen: wait_for_ui(..., total_wait_s=...)
    #   - neuere Call-Sites: wait_for_ui(..., timeout_s=...)
    #
    # Der Selftest soll niemals wegen eines Keyword-Namens crashen.
    # Daher akzeptieren wir timeout_s als Alias für total_wait_s.
    timeout_s: Optional[float] = None,
) -> bool:
    """
    Wait until the Flask UI is reachable.

    This selftest is often triggered very early during startup (e.g. via
    orchestrator/health checks). Without a short wait, it can produce
    misleading 'Connection refused' failures although the UI starts a
    moment later.

    Returns True once we get any HTTP response (including AUTH), otherwise
    False after the deadline.
    """
    import time
    if timeout_s is not None:
        try:
            total_wait_s = float(timeout_s)
        except Exception:
            pass
    deadline = time.time() + float(total_wait_s)
    probe_paths = ["/api/health", "/health", "/"]
    while time.time() < deadline:
        for p in probe_paths:
            try:
                r = session.get(base_url + p, timeout=1.0, allow_redirects=False)
                # Any HTTP status means the server socket is up.
                _ = r.status_code
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


# ------------------------------ Konfiguration --------------------------------

ROUTES: List[str] = [
    "/", "/games", "/models", "/learning", "/control",
    "/episodic", "/why", "/synapses", "/memory",
    "/video", "/chat", "/ask", "/knowledge",
    "/dream", "/replay", "/export", "/import",
    "/health", "/api/health",
]


def get_base_url() -> str:
    """
    Liefert die Basis-URL der ORÓMA-UI aus ENV oder Default.
    Priorität:
      1. OROMA_UI_URL
      2. OROMA_UI_HOST + OROMA_UI_PORT
      3. http://127.0.0.1:8080
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
    Erzeugt optional einen Authorization-Header, wenn OROMA_UI_TOKEN gesetzt ist.
    """
    token = os.environ.get("OROMA_UI_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


# ------------------------------ HTTP-Helper ----------------------------------


def check_route(
    session: requests.Session,
    base_url: str,
    route: str,
    timeout: float = 3.0,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Ruft eine Route mit GET ab und liefert (Statuscode, Fehlertext-fuer-Logs).

    Statuscode None → harter Ausnahmefehler (ConnectionError etc.).
    """
    url = f"{base_url}{route}"
    headers = get_auth_headers()
    try:
        resp = session.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        return resp.status_code, None
    except requests.exceptions.RequestException as e:
        return None, str(e)


# ------------------------------ Bewertung ------------------------------------


def classify_status(route: str, status: Optional[int]) -> str:
    """
    Klassifiziert Statuscode in [OK]/[WARN]/[AUTH]/[FAIL].

    Nur die Klassifizierung, keine Ausgabe.
    """
    if status is None:
        return "FAIL"

    if status in (200, 302):
        return "OK"

    if status in (401, 403):
        return "AUTH"

    if status == 503 and route in ("/video",):
        # Video-Backend oft nicht aktiv → kein harter Fehler
        return "WARN"

    return "FAIL"


def main() -> int:
    base_url = get_base_url()
    print("[ui_selftest] Base-URL:", base_url)

    session = requests.Session()

    # Avoid false negatives on boot (UI may not be bound yet).
    wait_s = float(os.environ.get("OROMA_UI_SELFTEST_WAIT_S", "15"))
    if not wait_for_ui(session, base_url, timeout_s=wait_s):
        print(f"[ui_selftest] UI nicht erreichbar nach {wait_s:.1f}s: {base_url}")
        return 1

    results: List[Tuple[str, str, Optional[int], Optional[str]]] = []
    had_fail = False

    for r in ROUTES:
        status, err = check_route(session, base_url, r)
        kind = classify_status(r, status)
        results.append((r, kind, status, err))

        if kind == "OK":
            print(f"[OK]   {r:<20} -> {status}")
        elif kind == "AUTH":
            print(f"[AUTH] {r:<20} -> {status} (Auth benötigt)")
        elif kind == "WARN":
            print(f"[WARN] {r:<20} -> {status} (nicht aktiv, erwartbar)")
        else:
            had_fail = True
            if status is None:
                print(f"[FAIL] {r:<20} -> keine Antwort ({err})")
            else:
                print(f"[FAIL] {r:<20} -> {status}")

    print("\n[ui_selftest] Zusammenfassung:")
    ok_count = sum(1 for _, k, *_ in results if k == "OK")
    auth_count = sum(1 for _, k, *_ in results if k == "AUTH")
    warn_count = sum(1 for _, k, *_ in results if k == "WARN")
    fail_count = sum(1 for _, k, *_ in results if k == "FAIL")

    print(
        textwrap.dedent(
            f"""
            - OK   : {ok_count}
            - AUTH : {auth_count}
            - WARN : {warn_count}
            - FAIL : {fail_count}
            """
        ).strip()
    )

    if had_fail:
        print("[ui_selftest] Ergebnis: ❌ Einige Routen sind fehlgeschlagen.")
        return 1

    print("[ui_selftest] Ergebnis: ✅ Alle geprüften Routen erreichbar (OK/WARN/AUTH).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[ui_selftest] Abgebrochen durch Benutzer.")
        sys.exit(1)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/dream_ui.py
# Projekt: ORÓMA – Circadian Learning Cycle
# Version: v3.7-r4 (Dream UI 2.0)
# Stand:   2025-10-14
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Web-UI und REST-API für den Traumzyklus (DreamWorker).
#   Dient sowohl der Beobachtung des automatischen nächtlichen Lernens
#   als auch der manuellen Auslösung (Batch-Dreams).
#
# Hauptfunktionen
# ───────────────
#   • /dream                → HTML-UI mit Steuerung und Live-Status
#   • /dream/api/start      → Startet manuellen DreamWorker-Lauf
#   • /dream/api/status     → Liefert Status, Fortschritt, Memory-Statistik
#   • /dream/api/stop       → Stoppt laufenden manuellen Dream-Thread
#
# Integration
# ───────────
#   - Wird von Flask-UI (run_oroma.py) registriert.
#   - Nutzt core.dream_worker.run_batch() (Generator für Fortschritt).
#   - Prüft DreamWorker-Thread, Seed, Timer-Intervall und Aktivitätsstatus.
#   - Fällt automatisch auf Dummy-Simulation zurück, falls DreamWorker fehlt.
#
# Hinweise
# ────────
#   - Läuft vollständig threadbasiert, um Flask-Hauptloop nicht zu blockieren.
#   - Fortschritt wird in _state{} global gepflegt (running/progress).
#   - Statusabfrage (Frontend) erfolgt alle 3 Sekunden über fetch().
# =============================================================================

from flask import Blueprint, jsonify, render_template, request
import threading
import time
import logging
from core.log_guard import log_suppressed

# -----------------------------------------------------------------------------
# Core-Imports
# -----------------------------------------------------------------------------
try:
    from core import dream_worker
except Exception:
    dream_worker = None

try:
    from core.langzeitgedaechtnis import LangzeitGedaechtnis
except Exception:
    LangzeitGedaechtnis = None

# -----------------------------------------------------------------------------
# Blueprint
# -----------------------------------------------------------------------------
bp = Blueprint("dream", __name__, url_prefix="/dream")

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.dream_ui")
if not LOG.handlers:
    sh = logging.StreamHandler()
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [DreamUI] %(message)s")
    sh.setFormatter(fmt)
    LOG.addHandler(sh)
LOG.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Globaler Status
# -----------------------------------------------------------------------------
_state = {
    "running": False,
    "progress": 0.0,
    "last_seed": None,
    "last_run": 0,
    "worker_online": False,
    "interval_sec": None,
}
_thread = None

# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _detect_worker_status() -> dict:
    """Ermittelt, ob DreamWorker importiert wurde und aktiv konfiguriert ist."""
    status = {
        "online": bool(dream_worker),
        "interval": None,
    }
    try:
        if dream_worker and hasattr(dream_worker, "DreamWorker"):
            # Temporär instanziieren, um den Default-Intervall zu lesen
            wk = dream_worker.DreamWorker(interval=60)
            status["interval"] = getattr(wk, "interval", None)
            del wk
    except Exception as e:
        log_suppressed('ui/dream_ui.py:101', exc=e, level=logging.WARNING)
        pass
    return status


def _run_dream(seed: int | None = None):
    """Manueller Batch-Dream (UI-getriggerter Hintergrundlauf)."""
    global _state
    _state.update({
        "running": True,
        "progress": 0.0,
        "last_seed": seed,
        "last_run": int(time.time())
    })

    LOG.info("🌙 Manueller DreamWorker-Start via UI (seed=%s)", seed)

    try:
        # Echter Worker vorhanden
        if dream_worker and hasattr(dream_worker, "run_batch"):
            for p in dream_worker.run_batch(seed=seed):
                _state["progress"] = float(p)
        else:
            # Fallback: Dummy-Simulation
            for i in range(1, 101):
                if not _state["running"]:
                    break
                time.sleep(0.05)
                _state["progress"] = i / 100.0

        LOG.info("✅ Manueller DreamWorker abgeschlossen (seed=%s)", seed)

    except Exception as e:
        LOG.error("❌ Fehler im DreamWorker: %s", e)
        _state["progress"] = -1
    finally:
        _state["running"] = False
        _state["last_run"] = int(time.time())

# =============================================================================
# Routen
# =============================================================================

@bp.route("/")
def page():
    """HTML-Oberfläche für Dream-Modus."""
    return render_template("dream.html")


@bp.route("/api/start", methods=["POST"])
def api_start():
    """Startet manuellen DreamWorker als Thread."""
    global _thread

    if _state["running"]:
        return jsonify({"ok": False, "error": "DreamWorker läuft bereits."})

    seed = None
    try:
        data = request.get_json(force=True)
        seed = int(data.get("seed", 0)) or None
    except Exception as e:
        log_suppressed('ui/dream_ui.py:163', exc=e, level=logging.WARNING)
        pass

    _thread = threading.Thread(target=_run_dream, args=(seed,), daemon=True)
    _thread.start()
    return jsonify({"ok": True, "msg": "DreamWorker gestartet"})


@bp.route("/api/status")
def api_status():
    """Liefert erweiterten Status des DreamWorker + Gedächtnis-Statistiken."""
    try:
        st = dict(_state)
        worker_info = _detect_worker_status()
        st["worker_online"] = worker_info["online"]
        st["interval_sec"] = worker_info["interval"]

        # Gedächtnisstatistik (sofern verfügbar)
        stats = {}
        if LangzeitGedaechtnis:
            try:
                mem = LangzeitGedaechtnis()
                stats = mem.stats() if hasattr(mem, "stats") else {}
            except Exception as e:
                LOG.debug("LangzeitGedächtnis-Stats fehlgeschlagen: %s", e)

        st["chains"] = stats.get("chains", 0)
        st["snaps"] = stats.get("snaps", 0)
        st["avg_quality"] = round(stats.get("avg_quality", 0.0), 3)
        st["exported"] = stats.get("exported", False)

        return jsonify({"ok": True, "status": st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    """Stoppt manuellen DreamWorker (falls aktiv)."""
    global _state
    if not _state["running"]:
        return jsonify({"ok": False, "msg": "Kein DreamWorker aktiv"})
    _state["running"] = False
    LOG.info("⏹️ Manueller DreamWorker über UI gestoppt")
    return jsonify({"ok": True, "msg": "Dream gestoppt"})
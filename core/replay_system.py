#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/replay_system.py
# Projekt:   ORÓMA (Offline-First · Headless · Replay Runtime)
# Modul:     ReplaySystem – SnapChain Replay Controller (Start/Pause/Resume/Stop/Status) + AgentLoop-Event-Injection + best-effort Metrics
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert das **produktive Replay-System** in ORÓMA.
# Es spielt eine SnapChain (Sequenz von events/patterns) deterministisch ab und
# kann dabei:
#   - Replay starten (start)
#   - pausieren / fortsetzen (pause / resume)
#   - stoppen (stop)
#   - Status liefern (status)
#
# Der Replay-Lauf passiert in einem **dedizierten Thread** (daemon=True), damit
# UI/AgentLoop nicht blockieren.
#
# DESIGNZIEL (ORÓMA REALITÄT)
# ───────────────────────────
# - ORÓMA läuft headless (Pi/Server) häufig im Orchestrator-Modus.
# - Replay muss robust sein gegen:
#     • fehlende optionale Module (SnapChain Loader, sql_manager, agent_loop)
#     • heterogene SnapChain-Formate (Objekt vs dict)
#     • temporäre DB-Locks (Metrics write ist best effort)
# - Replay darf niemals „hart“ das Gesamtsystem killen, sondern muss:
#     • Fehler in _state["error"] spiegeln
#     • logs schreiben (replay_manager.log)
#     • sauber auf running=False zurückfallen
#
# WICHTIG: DATEINAME VS. LOGFILE-HISTORIE
# ───────────────────────────────────────
# Der Code nutzt historisch den Logfile-Namen "replay_manager.log" (unter logs/),
# obwohl dieses Modul in v3.7.3 als core/replay_system.py geführt wird.
# Diese Diskrepanz ist **absichtlich** für Backward-Kompatibilität:
# - Tools/Diagnosen suchen ggf. weiterhin nach replay_manager.log
#
# KERNKOMPONENTEN
# ───────────────
# 1) Globaler Replay-Zustand (_state)
#    - Ein Dict, das jederzeit per status() kopiert zurückgegeben werden kann.
#    - Fields (aktueller Codepfad):
#        running, paused, chain_id,
#        step, total_steps, speed,
#        last_event,
#        started_at, elapsed_time, progress_pct,
#        error
#
# 2) Steuerflags
#    - _stop_flag  (threading.Event): beendet den Worker frühzeitig
#    - _pause_flag (threading.Event): hält den Worker in einer Sleep-Schleife
#
# 3) Worker-Thread (_worker)
#    - Lädt eine SnapChain (best effort)
#    - Iteriert über sc.patterns als Event-Liste
#    - Aktualisiert _state pro Schritt
#    - Optional:
#        • schreibt Metrik replay_step (sql_manager.insert_metric)
#        • injiziert Events in agent_loop.inject_event(ev) (falls vorhanden)
#
# SNAPCHAIN-LOADING (SELF-HEALING / FORMAT-TOLERANZ)
# ─────────────────────────────────────────────────
# _ensure_snapchain(chain_id):
#   - versucht load_chain(chain_id)
#   - akzeptiert:
#       A) SnapChain Objekt (preferred)
#       B) dict mit "events" + "metadata" (Legacy/Exporter)
#          → wird in SnapChain(patterns=..., metadata=...) konvertiert
#   - wenn die Chain leer/invalid ist:
#       → Dummy-SnapChain wird erzeugt (patterns=[], metadata={"origin": "..."} )
#
# WICHTIG:
# - Wenn SnapChain Core NICHT verfügbar ist (Import fail):
#     _ensure_snapchain() gibt None zurück, Replay stoppt sauber mit error.
#
# REPLAY-TIMING
# ─────────────
# Der Worker schläft pro Schritt:
#   time.sleep(max(0.05, 1.0 / max(0.1, speed)))
#
# Bedeutung:
# - speed=1.0 → ~1 Event/sec (mit Minimum 0.05s)
# - speed>1.0 → schneller
# - speed sehr klein → wird auf 0.1 geklemmt (sonst Division/Freeze)
#
# METRICS (BEST EFFORT, NICHT KRITISCH)
# ─────────────────────────────────────
# Wenn core.sql_manager importierbar ist:
#   sql_manager.insert_metric("replay_step", float(idx+1))
#
# Das ist bewusst best effort:
# - DB locked / Fehler → log_guard.log_suppressed(... "core.replay_system.pass.1")
# - Replay läuft trotzdem weiter
#
# AGENTLOOP-INTEGRATION (OPTIONAL, RUNTIME-INJECTION)
# ──────────────────────────────────────────────────
# Wenn core.agent_loop importierbar ist und inject_event existiert:
#   agent_loop.inject_event(ev)
#
# Zweck:
# - Replay kann „Events“ in den laufenden AgentLoop einspeisen, um z. B.
#   Entscheidungs- oder Lernpfade zu reproduzieren.
#
# WICHTIG:
# - inject_event ist optional. Fehler werden nur DEBUG geloggt.
# - Replay bleibt funktionsfähig, auch wenn AgentLoop nicht verfügbar ist.
#
# OPTIONAL: EXPORT NACH REPLAY (BEST EFFORT)
# ─────────────────────────────────────────
# Wenn save_chain verfügbar ist und sc SnapChain ist:
#   fn = save_chain(f"replay_<ts>", sc)
#
# Hinweis:
# - Der aktuelle Code exportiert die gleiche Chain erneut (kein „Replay result rewrite“),
#   primär als Debug-Artefakt / Kompatibilitätsanker.
# - Fehler → Warning, Replay endet trotzdem erfolgreich.
#
# LOGGING (PRODUKTIONSWICHTIG)
# ───────────────────────────
# Logger: "oroma.replay"
# Handler:
#   - FileHandler: /opt/ai/oroma/logs/replay_manager.log
#   - StreamHandler: stdout/stderr (systemd journal)
#
# Format:
#   "%(asctime)s [%(levelname)s] [Replay] %(message)s"
#
# Dieses Modul initialisiert Handler nur, wenn noch keine existieren (idempotent).
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# start(chain_id: Any, speed: float = 1.0) -> None
#   - startet Worker-Thread
#   - wirft RuntimeError, wenn bereits running=True
#
# pause() -> None
# resume() -> None
# stop() -> None
# status() -> Dict[str, Any]
#
# SELFTEST (CLI)
# ─────────────
# Direktaufruf:
#   python3 /opt/ai/oroma/core/replay_system.py
# startet einen kurzen Selbsttest und druckt status() mehrfach.
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Headless: keine UI/Qt Abhängigkeiten.
# - Worker muss thread-basiert bleiben (UI darf nicht blocken).
# - _ensure_snapchain muss dict→obj tolerieren (Legacy/Exporter).
# - Metrics + inject_event sind optional/best effort (nie Replay killen).
# - stop() muss join(timeout) nutzen (kein Hängen beim Stop).
#
# =============================================================================
# END HEADER
# =============================================================================

import os
import time
import threading
import logging
from typing import Optional, Dict, Any
from core.log_guard import log_suppressed
import logging

# -----------------------------------------------------------------------------
# Core-Imports (robust)
# -----------------------------------------------------------------------------
import sys
sys.path.append("/opt/ai/oroma")  # absolute Pfadangabe für systemd

try:
    from core.snapchain import SnapChain, load_chain, save_chain
except Exception:
    SnapChain = load_chain = save_chain = None

try:
    from core import sql_manager
    _HAS_SQL = True
except Exception:
    _HAS_SQL = False

try:
    from core import agent_loop
    _HAS_AGENT = True
except Exception:
    _HAS_AGENT = False

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_DIR = "/opt/ai/oroma/logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG = logging.getLogger("oroma.replay")
if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [Replay] %(message)s")
    fh = logging.FileHandler(os.path.join(LOG_DIR, "replay_manager.log"), encoding="utf-8")
    sh = logging.StreamHandler()
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    LOG.addHandler(fh)
    LOG.addHandler(sh)

# -----------------------------------------------------------------------------
# Globaler Zustand
# -----------------------------------------------------------------------------
_state: Dict[str, Any] = {
    "running": False,
    "paused": False,
    "chain_id": None,
    "step": 0,
    "total_steps": 0,
    "speed": 1.0,
    "last_event": None,
    "started_at": None,
    "progress_pct": 0.0,
    "elapsed_time": 0.0,
    "error": None,
}

_thread: Optional[threading.Thread] = None
_stop_flag = threading.Event()
_pause_flag = threading.Event()

# -----------------------------------------------------------------------------
# Hilfsfunktionen
# -----------------------------------------------------------------------------
def _ensure_snapchain(chain_id: Any) -> Optional[SnapChain]:
    """
    Selbstheilende SnapChain-Ladefunktion.
    Versucht, eine Chain zu laden; erstellt andernfalls Dummy-Chain.
    """
    if not (SnapChain and callable(load_chain)):
        LOG.error("⚠️ SnapChain-Core nicht verfügbar.")
        return None

    try:
        sc = load_chain(chain_id)

        # 🔹 Falls load_chain() ein dict liefert (z. B. {"events":[...],"metadata":{...}})
        if isinstance(sc, dict) and "events" in sc:
            try:
                from core.snapchain import SnapChain
                pats = [p for p in sc.get("events", []) if p]
                meta = dict(sc.get("metadata", {}))
                sc = SnapChain(patterns=pats, metadata=meta)
                LOG.info("♻️ SnapChain(dict→obj) konvertiert (id=%s, len=%d)", chain_id, len(pats))
                return sc
            except Exception as e:
                LOG.warning("⚠️ Konvertierung dict→SnapChain fehlgeschlagen: %s", e)

        # 🔹 Normales Verhalten, wenn load_chain() bereits SnapChain zurückgibt
        if sc and getattr(sc, "patterns", None):
            LOG.info("✅ SnapChain geladen (id=%s, len=%d)", chain_id, len(sc.patterns))
            return sc

        LOG.warning("⚠️ SnapChain leer – Dummy erzeugt")
        return SnapChain(patterns=[], metadata={"origin": "replay-fallback"})

    except Exception as e:
        LOG.error("❌ Fehler beim Laden der SnapChain #%s: %s", chain_id, e)
        try:
            dummy = SnapChain(patterns=[], metadata={"origin": "replay-recovery"})
            LOG.info("🔁 Dummy-SnapChain erzeugt (%s)", chain_id)
            return dummy
        except Exception as e2:
            LOG.error("❌ SnapChain-Recovery fehlgeschlagen: %s", e2)
            return None
# -----------------------------------------------------------------------------
# Worker
# -----------------------------------------------------------------------------
def _worker(chain_id: Any, speed: float):
    global _state
    sc = _ensure_snapchain(chain_id)
    if sc is None:
        _state.update({"running": False, "error": "SnapChain konnte nicht geladen werden"})
        return

    events = getattr(sc, "patterns", [])
    total = len(events)
    LOG.info("▶️ Replay gestartet: chain=%s steps=%d speed=%.2f", chain_id, total, speed)

    _state.update({
        "running": True,
        "paused": False,
        "chain_id": chain_id,
        "step": 0,
        "total_steps": total,
        "speed": speed,
        "started_at": time.time(),
        "error": None,
    })

    _stop_flag.clear()
    _pause_flag.clear()

    try:
        for idx, ev in enumerate(events):
            if _stop_flag.is_set():
                LOG.info("⏹️ Replay gestoppt (user interrupt)")
                break

            while _pause_flag.is_set() and not _stop_flag.is_set():
                time.sleep(0.1)

            _state["step"] = idx + 1
            _state["last_event"] = ev
            _state["elapsed_time"] = time.time() - (_state["started_at"] or time.time())
            _state["progress_pct"] = round(100.0 * _state["step"] / max(1, total), 2)

            if _HAS_SQL:
                try:
                    sql_manager.insert_metric("replay_step", float(idx + 1))
                except Exception as e:
                    log_suppressed(
                        logging.getLogger(__name__),
                        key="core.replay_system.pass.1",
                        exc=e,
                        msg="Suppressed exception (was: pass)",
                    )

            if _HAS_AGENT and hasattr(agent_loop, "inject_event"):
                try:
                    agent_loop.inject_event(ev)
                except Exception as e:
                    LOG.debug("inject_event fehlgeschlagen: %s", e)

            time.sleep(max(0.05, 1.0 / max(0.1, speed)))

        # Optional: Replay-Export
        if save_chain and isinstance(sc, SnapChain):
            try:
                fn = save_chain(f"replay_{int(time.time())}", sc)
                LOG.info("💾 ReplayChain exportiert → %s", fn)
            except Exception as e:
                LOG.warning("Replay-Export fehlgeschlagen: %s", e)

        LOG.info("🏁 Replay abgeschlossen (%s Steps)", total)

    except Exception as e:
        LOG.error("❌ Replay-Fehler: %s", e)
        _state["error"] = str(e)
    finally:
        _state.update({"running": False, "paused": False, "chain_id": None})

# -----------------------------------------------------------------------------
# Öffentliche API
# -----------------------------------------------------------------------------
def start(chain_id: Any, speed: float = 1.0) -> None:
    """Startet ein Replay mit automatischer SnapChain-Sicherung."""
    global _thread
    if _state["running"]:
        raise RuntimeError("Replay läuft bereits")
    _thread = threading.Thread(target=_worker, args=(chain_id, speed), daemon=True)
    _thread.start()

def pause() -> None:
    if not _state["running"]:
        return
    _pause_flag.set()
    _state["paused"] = True
    LOG.info("⏸️ Replay pausiert")

def resume() -> None:
    if not _state["running"]:
        return
    _pause_flag.clear()
    _state["paused"] = False
    LOG.info("▶️ Replay fortgesetzt")

def stop() -> None:
    if not _state["running"]:
        return
    _stop_flag.set()
    if _thread:
        try:
            _thread.join(timeout=3.0)
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.replay_system.pass.2",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
    _state.update({"running": False, "paused": False, "chain_id": None})
    LOG.info("⏹️ Replay gestoppt")

def status() -> Dict[str, Any]:
    return dict(_state)

# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("[replay_manager] Selftest …")
    start("selftest", speed=2.0)
    for _ in range(5):
        print(status())
        time.sleep(1)
    pause(); print("Pause:", status())
    time.sleep(1)
    resume(); print("Resume:", status())
    time.sleep(1)
    stop(); print("Stop:", status())
    print("[replay_manager] OK ✅")
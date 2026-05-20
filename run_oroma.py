#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/run_oroma.py
# Projekt:   ORÓMA (Offline-First · Headless · Orchestrator-ready)
# Modul:     Runner/Glue – startet Flask-UI + AgentLoop + DeviceHub-Init + Luma-Sampler + CircadianController↔DreamWorker Bridge + Safe Blueprint Registration
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / SYSTEMROLLE
# ──────────────────
# run_oroma.py ist der „Produktions-Entry“ für den ORÓMA Stack in einem Prozess:
#   1) Flask UI (Dashboard) – App kommt aus ui/flask_ui.py
#   2) Safe Blueprint Registration – viele UI-Module sind optional → Importfehler dürfen nicht boot-kill sein
#   3) AgentLoop – Ticker-Engine mit Hooks (core/agent_loop)
#   4) DeviceHub – Kamera/Audio/Light Zugriff als Singleton (core/device_hub)
#   5) Luma-Sampler (optional) – berechnet Helligkeit aus DeviceHub Frames (für Circadian)
#   6) DreamWorker – optionaler Background-Thread (core/dream_worker)
#   7) CircadianController – optionaler Thread (core/circadian_controller) inkl. Bridge:
#        - bei Modewechsel DREAM/DAY wird DreamWorker gestartet/gestoppt
#        - Phase wird als JSON in PHASE_PATH geschrieben (UI/Status-Fallback)
#
# Dieses Modul ist bewusst „Glue“:
# - Fachlogik liegt in core/* bzw. in ui/* Blueprints
# - run_oroma.py verbindet nur Komponenten, startet Threads und sorgt für sauberen Shutdown
#
# HEADLESS / ORCHESTRATOR-KONTEXT
# ──────────────────────────────
# ORÓMA läuft typischerweise headless (Pi/Server) und oft im Orchestrator-Modus (.use_orchestrator).
# Das bedeutet:
# - systemd oneshot/timer können ConditionPathExists-basiert übersprungen werden
# - der Orchestrator startet Tools/Jobs zyklisch (StatsSnapshot, EnergyManager, Dream, …)
# Trotzdem bleibt run_oroma.py zentral:
# - stellt Web-UI bereit (Flask)
# - stellt AgentLoop bereit (Hooks/Realtime)
# - hält DeviceHub am Leben (optional autostart)
#
# SAFE BLUEPRINT REGISTRATION (WICHTIG)
# ─────────────────────────────────────
# Viele UI-Module sind optional (abhängig von Features/Dependencies).
# run_oroma.py importiert Blueprints via _imp("ui.modul") und registriert sie per safe_register():
# - Exception/ImportError → LOG + Skip (Boot bleibt möglich)
# - Damit ist ORÓMA auch in „Slim“-Deploys oder während refactors startfähig.
#
# Die Registrierung umfasst (abhängig von Verfügbarkeit) u. a.:
#   research_ui, meta_ui, control_ui, health_ui(+compat), gaps_ui, scenegraph_ui, objects_ui,
#   export_ui, episodic_ui, synapses_ui, memory_ui, why_ui,
#   replay_api, stats_ui, pong_panel, forgetting_ui, video_ui, ask_ui, knowledge_ui, learning,
#   tetris_ui (ggf. bereits via games_ui), replay_ui, models_ui, dream_ui, bundle_ui, chat_ui,
#   picar_ui, asr_ui, asr2_ui,
#   calculator_ui, scicalc_ui, setcalc_ui, empathy_ui, coverage_ui, selftest_ui, missions_ui,
#   curriculum_ui, audio_ui, selfrec_ui,
#   + optional admin_bp (falls vorhanden)
#
# (Hinweis: Genau diese Namen/Module sind in dieser ZIP im Codepfad sichtbar;
#  safe_register verhindert Crash bei Nichtverfügbarkeit.)
#
# AGENTLOOP THREAD
# ────────────────
# AgentLoop wird (wenn enabled) in einem daemon Thread gestartet:
#   - core.agent_loop.start(dt)
#   - run_oroma.py pollt status() und läuft solange running=True
# Shutdown:
#   - core.agent_loop.stop()
#
# ENV:
#   OROMA_AGENT_ENABLED=true|false    (Default: true)
#   OROMA_AGENT_DT=0.25              (Default: 0.25)
#
# DEVICEHUB INIT + PRODUKTIONSFIX EXTERNAL PROVIDER
# ────────────────────────────────────────────────
# run_oroma.py initialisiert DeviceHub best effort (core.device_hub.get_hub()) und kann
# je nach Setup autostarten:
#   - OROMA_DEVICEHUB_AUTOSTART=0|1  (Default: 1)
#
# PRODUKTIONSFIX: Externe Kamera-Provider (PiCar/camera_hub)
# - Wenn externe Frames über Provider kommen, darf run_oroma keine internen Kamera-Starts erzwingen.
# - run_oroma prüft daher:
#     • OROMA_PICAR_CAMERA=0|1
#     • optional core.camera_hub._providers_active()
# - DeviceHub status wird ausgewertet:
#     • external_source=="external" oder external_frames>0 → „externes Feed aktiv“
#     • running==True → interner Capture läuft
#     • sonst → info, aber kein harter Fehler
#
# LUMA / LIGHT SENSOR (für Circadian)
# ───────────────────────────────────
# run_oroma.py kann eine Light-Sensor-Funktion bauen, abhängig von OROMA_LIGHT_SOURCE:
#   - "camera" → startet Luma-Sampler Thread, liest Helligkeit aus DeviceHub:
#       • bevorzugt echte Light-Getter am Hub (get_light_level/light_level/luma/get_luma)
#       • fallback: berechnet Luma aus Frame (numpy mean, RGB->Y) und skaliert auf 0..100
#   - "dummy" → liefert konstant 80.0
#   - "off"   → liefert None (Controller nutzt intern Default)
#
# ENV:
#   OROMA_LIGHT_SOURCE=camera|dummy|off  (Default: dummy)
#   OROMA_LIGHT_CAMERA_INTERVAL=300      (Sekunden; Luma Sampler Loop)
#   OROMA_LIGHT_MIN=0 / OROMA_LIGHT_MAX=100 (Clamp/Skalierung)
#
# DREAMWORKER + CIRCADIANCONTROLLER BRIDGE
# ───────────────────────────────────────
# Historisch hing run_oroma.py zwei Rollen an denselben Schalter OROMA_DREAM_ENABLED:
#   A) CircadianController starten (Phase DAY/DREAM erkennen)
#   B) lokalen DreamWorker im selben Prozess starten/stoppen
#
# Im Orchestrator-Modus führte das zu einem Doppelpfad:
#   - run_oroma.py startete lokal einen DreamWorker
#   - tools/oroma_orchestrator.py startete zusätzlich periodisch core.dream_worker
#
# Diese Version trennt die Rollen sauber:
#   - CircadianController darf weiterlaufen und die Phase schreiben
#   - der *lokale* DreamWorker wird im Orchestrator-Modus hart unterdrückt
#   - der Orchestrator bleibt damit der einzige Dream-Executor
#
# ENV / Verhalten:
#   OROMA_DREAM_ENABLED=true|false
#       Nicht-Orchestrator-Modus:
#         steuert weiterhin Circadian + lokalen DreamWorker gemeinsam.
#       Orchestrator-Modus:
#         steuert nur noch den lokalen DreamWorker-Wunsch; die lokale
#         Dream-Ausführung wird trotzdem unterdrückt, damit Dream nur einmal läuft.
#
#   OROMA_CIRCADIAN_ENABLED=true|false
#       Optionaler separater Schalter für den CircadianController.
#       Default:
#         - im Orchestrator-Modus: true
#         - sonst: entspricht OROMA_DREAM_ENABLED
#
#   OROMA_PHASE_PATH=/opt/ai/oroma/data/state/phase.json
#       Phase-Datei für UI/Orchestrator-Gating.
#
# Bridge für Kompatibilität:
# - run_oroma.py setzt (best effort):
#     CircadianController.INSTANCE = <thread>
#   und ergänzt ggf. eine classmethod instance(), falls nicht vorhanden.
#
# Phase JSON (Fallback für UI/Status):
# - Pfad: PHASE_PATH
# - Inhalt (typisch):
#     {"phase":"DAY|DREAM","ts":..., "threshold":..., "delay_min":..., "poll_sec":..., "source":"controller"}
#
# ENV:
#   OROMA_DREAM_ENABLED=true|false   (Default: true)
#   OROMA_DREAM_INTERVAL=1800        (Sekunden)
#   OROMA_PHASE_PATH=/opt/ai/oroma/data/state/phase.json
#
# AUDIO ALWAYS-ON (OPTIONAL)
# ──────────────────────────
# run_oroma.py kann beim Boot das Mikro öffnen, um Audio stabil bereitzuhalten
# (z. B. für SnapTokens/ASR ohne UI-Klick):
# - Aktiv, wenn:
#     OROMA_AUDIO_ENABLE=true
#     UND (OROMA_AUDIO_ALWAYS_ON=true ODER OROMA_AUDIO_SNAPS=true)
# - Umsetzung:
#     hub.start_mic(client="boot") (best effort; Fehler werden geloggt, kein Crash)
#
# ENV:
#   OROMA_AUDIO_ENABLE=true|false
#   OROMA_AUDIO_ALWAYS_ON=1|0
#   OROMA_AUDIO_SNAPS=1|0
#
# FLASK UI START
# ──────────────
# Flask wird ohne Reloader gestartet:
#   flask_app.run(host=FLASK_RUN_HOST, port=FLASK_RUN_PORT, debug=False, use_reloader=False)
#
# ENV:
#   FLASK_RUN_HOST=0.0.0.0
#   FLASK_RUN_PORT=8080
#   OROMA_LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
#
# SIGNAL HANDLING / CLEAN SHUTDOWN
# ────────────────────────────────
# SIGINT/SIGTERM:
# - stop AgentLoop
# - stop DreamWorker
# - stop CircadianController (best effort)
# - stop Luma-Sampler
# - stop DeviceHub (best effort)
# Zusätzlich im finally-Block nach Flask-Ende wird Shutdown wiederholt, um Leaks zu vermeiden.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - safe_register muss bleiben (UI-Module sind optional; Boot darf nicht scheitern).
# - External Provider Detection muss bleiben (PiCar/Provider verhindert Doppel-Capture).
# - Luma-Sampler ist bewusst best effort (kein Crash bei numpy/devicehub Problemen).
# - Circadian↔Dream Bridge über gepatchtes _set_mode ist Absicht (stabile Kopplung ohne Core-Änderung).
# - Audio always-on ist optional und darf nie boot-kill sein.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os, sys, json, time, signal, threading, logging, importlib
from logging.handlers import RotatingFileHandler
from typing import Optional, Callable
from queue import Queue

# -----------------------------------------------------------------------------
# Basis & .env
# -----------------------------------------------------------------------------
BASE = os.path.abspath(os.path.dirname(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(BASE, ".env"))
except Exception as e:
    # .env ist optional – aber wenn es nicht klappt, soll es sichtbar sein.
    try:
        sys.stderr.write(f"[run_oroma] WARN: python-dotenv nicht verfügbar / .env konnte nicht geladen werden: {e}\n")
    except Exception:
        pass
    pass

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("oroma.run")
if not logger.handlers:
    level = os.environ.get("OROMA_LOG_LEVEL", "INFO").upper()
    rotate_bytes = max(1_000_000, int(os.environ.get("OROMA_LOG_ROTATE_BYTES", str(20 * 1024 * 1024)) or str(20 * 1024 * 1024)))
    rotate_backups = max(1, int(os.environ.get("OROMA_LOG_ROTATE_BACKUPS", "6") or "6"))
    attach_stderr = str(os.environ.get("OROMA_RUN_ATTACH_STDERR", "0") or "0").lower() in ("1", "true", "yes", "on")
    stderr_level = str(os.environ.get("OROMA_RUN_STDERR_LEVEL", "WARNING") or "WARNING").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [Run] %(message)s")
    try:
        fh = RotatingFileHandler(
            os.path.join(LOG_DIR, "service.out.log"),
            maxBytes=rotate_bytes,
            backupCount=rotate_backups,
            encoding="utf-8",
        )
        fh.setFormatter(fmt); logger.addHandler(fh)
    except Exception as e:
        # Logging darf nicht hart scheitern – aber niemals stillschweigend.
        try:
            sys.stderr.write(f"[run_oroma] WARN: Logfile logs/service.out.log kann nicht geöffnet werden: {e}\n")
        except Exception:
            pass
        pass
    if attach_stderr:
        sh = logging.StreamHandler()
        sh.setLevel(getattr(logging, stderr_level, logging.WARNING))
        sh.setFormatter(fmt)
        logger.addHandler(sh)

# Global: unhandled exceptions (Main/Threads) niemals still verlieren
try:
    from core.log_guard import install_global_excepthooks
    install_global_excepthooks(logger=logger)
except Exception as e:
    # Letzte Rettung: darf niemals den Runner stoppen
    try:
        sys.stderr.write(f"[run_oroma] WARN: install_global_excepthooks failed: {e}\n")
    except Exception:
        pass

def log(msg: str, level: int = logging.INFO) -> None:
    logger.log(level, msg)

# -----------------------------------------------------------------------------
# Flask-App & Helfer
# -----------------------------------------------------------------------------
from ui.flask_ui import app as flask_app

def safe_register(bp, name: str) -> None:
    try:
        flask_app.register_blueprint(bp)
        log(f"Blueprint {name} registriert")
    except Exception as e:
        log(f"Blueprint {name} fehlgeschlagen: {e}", logging.ERROR)

def _imp(modname: str):
    """Import mit Protokoll; bei Fehler → None statt Crash."""
    try:
        return importlib.import_module(modname)
    except Exception as e:
        log(f"{modname} nicht verfügbar: {e}", logging.INFO)
        return None

# -----------------------------------------------------------------------------
# UI-Module (guarded imports)
# -----------------------------------------------------------------------------
control_ui   = _imp("ui.control_ui")
health_ui    = _imp("ui.health_ui")
gaps_ui      = _imp("ui.gaps_ui")
export_ui    = _imp("ui.export_ui")
bundle_ui    = _imp("ui.bundle_ui")
episodic_ui  = _imp("ui.episodic_ui")
synapses_ui  = _imp("ui.synapses_ui")
memory_ui    = _imp("ui.memory_ui")
why_ui       = _imp("ui.why_ui")

games_ui     = _imp("ui.games_ui")
replay_api   = _imp("ui.replay_api")
stats_ui     = _imp("ui.stats_ui")
pong_panel   = _imp("ui.pong_panel_ui")
forgetting_ui= _imp("ui.forgetting_ui")

ask_ui       = _imp("ui.ask_ui")
knowledge_ui = _imp("ui.knowledge_ui")
learning     = _imp("ui.learning")
video_ui     = _imp("ui.video_ui")
replay_ui    = _imp("ui.replay_ui")
models_ui    = _imp("ui.models_ui")
dream_ui     = _imp("ui.dream_ui")
chat_ui      = _imp("ui.chat_ui")
tetris_ui    = _imp("ui.tetris_ui")

picar_ui     = _imp("ui.picar_ui")
asr_ui       = _imp("ui.asr_ui")
asr2_ui      = _imp("ui.asr2_ui")
audio_ui     = _imp("ui.audio_ui")

research_ui  = _imp("ui.research_ui")
meta_ui      = _imp("ui.meta_ui")
calculator_ui= _imp("ui.calculator_ui")
scicalc_ui   = _imp("ui.scicalc_ui")
setcalc_ui   = _imp("ui.setcalc_ui")
empathy_ui   = _imp("ui.empathy_ui")
coverage_ui  = _imp("ui.coverage_ui")
selftest_ui  = _imp("ui.selftest_ui")
missions_ui  = _imp("ui.missions_ui")
curriculum_ui= _imp("ui.curriculum_ui")
selfrec_ui   = _imp("ui.selfrec_ui")
scenegraph_ui= _imp("ui.scenegraph_ui")
objects_ui   = _imp("ui.objects_ui")

admin_mod    = _imp("ui.admin")
admin_bp     = getattr(admin_mod, "admin_bp", None) if admin_mod else None

# -----------------------------------------------------------------------------
# Registrierung der Blueprints
# -----------------------------------------------------------------------------
if research_ui:  safe_register(research_ui.bp, "research_ui")
if meta_ui:      safe_register(meta_ui.bp, "meta_ui")
if control_ui:   safe_register(control_ui.bp, "control_ui")
if health_ui:
    safe_register(health_ui.bp, "health_ui")
    if hasattr(health_ui, "bp_compat"):
        safe_register(health_ui.bp_compat, "health_ui_compat")
if gaps_ui:      safe_register(gaps_ui.bp, "gaps_ui")
if scenegraph_ui and hasattr(scenegraph_ui, "bp"):
    safe_register(scenegraph_ui.bp, "scenegraph_ui")
if objects_ui and hasattr(objects_ui, "bp"):
    safe_register(objects_ui.bp, "objects_ui")
if export_ui:    safe_register(export_ui.bp, "export_ui")
if episodic_ui and hasattr(episodic_ui, "episodic_bp"):
    safe_register(episodic_ui.episodic_bp, "episodic_ui")
if synapses_ui and hasattr(synapses_ui, "synapses_bp"):
    safe_register(synapses_ui.synapses_bp, "synapses_ui")
if memory_ui and hasattr(memory_ui, "memory_bp"):
    safe_register(memory_ui.memory_bp, "memory_ui")
if why_ui:       safe_register(why_ui.bp, "why_ui")

# Spiele/Extras
try:
    if games_ui and hasattr(games_ui, "register_games"):
        games_ui.register_games(flask_app); log("Games registriert")
except Exception as e:
    log(f"Games-Registrierung fehlgeschlagen: {e}", logging.INFO)
if replay_api and hasattr(replay_api, "replay_bp"):
    safe_register(replay_api.replay_bp, "replay_api")
if stats_ui and hasattr(stats_ui, "stats_bp"):
    safe_register(stats_ui.stats_bp, "stats_ui")
if pong_panel and hasattr(pong_panel, "pong_panel_bp"):
    safe_register(pong_panel.pong_panel_bp, "pong_panel_ui")
if forgetting_ui:
    safe_register(forgetting_ui.bp, "forgetting_ui")

# Haupt-UI
if video_ui and hasattr(video_ui, "video_bp"): safe_register(video_ui.video_bp, "video_ui")
if ask_ui:       safe_register(ask_ui.bp, "ask_ui")
if knowledge_ui: safe_register(knowledge_ui.bp, "knowledge_ui")
if learning and hasattr(learning, "learning_bp"):
    safe_register(learning.learning_bp, "learning")
if tetris_ui and hasattr(tetris_ui, "tetris_bp"):
    # Hinweis: Das Game-Subsystem (ui/games_ui.py) registriert Tetris bereits.
    #          Doppelte Registrierung führt zu Flask-Fehler:
    #          "The name 'tetris' is already registered for this blueprint".
    if "tetris" not in flask_app.blueprints:
        safe_register(tetris_ui.tetris_bp, "tetris_ui")
    else:
        log("Blueprint tetris_ui bereits registriert (via games_ui)")
if replay_ui:    safe_register(replay_ui.bp, "replay_ui")
if models_ui:    safe_register(models_ui.bp, "models_ui")
if dream_ui:     safe_register(dream_ui.bp, "dream_ui")
if bundle_ui:    safe_register(bundle_ui.bp, "bundle_ui")
if chat_ui:      safe_register(chat_ui.bp, "chat_ui")
if picar_ui:     safe_register(picar_ui.bp, "picar_ui")
if asr_ui:       safe_register(asr_ui.bp, "asr_ui")
if asr2_ui:      safe_register(asr2_ui.bp, "asr2_ui")

# Patch-/Analyse-UIs
if calculator_ui: safe_register(calculator_ui.bp, "calculator_ui")
if scicalc_ui:    safe_register(scicalc_ui.bp, "scicalc_ui")
if setcalc_ui:    safe_register(setcalc_ui.bp, "setcalc_ui")
if empathy_ui:    safe_register(empathy_ui.bp, "empathy_ui")
if coverage_ui:   safe_register(coverage_ui.bp, "coverage_ui")
if selftest_ui:   safe_register(selftest_ui.bp, "selftest_ui")
if missions_ui:   safe_register(missions_ui.bp, "missions_ui")
if curriculum_ui: safe_register(curriculum_ui.bp, "curriculum_ui")
if audio_ui:      safe_register(audio_ui.bp, "audio_ui")
if selfrec_ui:    safe_register(selfrec_ui.bp, "selfrec_ui")


# Admin
if admin_bp:
    try:
        flask_app.register_blueprint(admin_bp)
        log("Blueprint admin_bp registriert")
    except Exception as e:
        log(f"Blueprint admin_bp fehlgeschlagen: {e}", logging.ERROR)

# -----------------------------------------------------------------------------
# AgentLoop
# -----------------------------------------------------------------------------
from core import agent_loop
AGENT_ENABLED = os.environ.get("OROMA_AGENT_ENABLED", "true").lower() not in ("0","false","no","off")
AGENT_DT = float(os.environ.get("OROMA_AGENT_DT", "0.25"))

_agent_thread: Optional[threading.Thread] = None

def _start_agent() -> None:
    try:
        agent_loop.start(AGENT_DT)
        while agent_loop.status().get("running", False):
            time.sleep(1.0)
    except Exception as e:
        log(f"AgentLoop aborted: {e}", logging.ERROR)

def _stop_agent() -> None:
    try:
        agent_loop.stop()
    except Exception as e:
        logger.warning("AgentLoop.stop() failed during shutdown", exc_info=e)
        pass

# -----------------------------------------------------------------------------
# DeviceHub / Luma-Sensor
# -----------------------------------------------------------------------------
_LIGHT_SRC = os.environ.get("OROMA_LIGHT_SOURCE", "dummy").strip().lower()
_LIGHT_IV  = int(os.environ.get("OROMA_LIGHT_CAMERA_INTERVAL", "300"))
_LIGHT_MIN = float(os.environ.get("OROMA_LIGHT_MIN", "0"))
_LIGHT_MAX = float(os.environ.get("OROMA_LIGHT_MAX", "100"))

# -----------------------------------------------------------------------------
# PRODUKTIONSFIX – External Provider Detection (PiCar/camera_hub)
# -----------------------------------------------------------------------------
# Hintergrund:
#   ORÓMA kann Kamera-Frames über externe Provider erhalten (z.B. PiCarWrapper
#   via camera_hub). In diesem Modus darf run_oroma keine internen Kamera-Starts
#   erzwingen und sollte Status/Log entsprechend ausgeben.
#
# ENV:
#   OROMA_DEVICEHUB_AUTOSTART=0|1   (Default 1)
#   OROMA_PICAR_CAMERA=0|1         (Wenn 1 → externer Provider aktiv/erwartet)
# -----------------------------------------------------------------------------

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")


def _external_camera_provider_active() -> bool:
    # PiCar nutzt typischerweise den camera_hub Provider
    if _bool_env("OROMA_PICAR_CAMERA", False):
        return True
    try:
        from core import camera_hub  # type: ignore
        fn = getattr(camera_hub, "_providers_active", None)
        if callable(fn):
            return bool(fn())
    except Exception as e:
        logger.debug("External provider detection failed (camera_hub/_providers_active)", exc_info=e)
        pass
    return False


_devicehub = None
_devicehub_warmup_started = False  # one-shot warmup status log
_luma_thread: Optional[threading.Thread] = None
_luma_running = False
_luma_cached: float = 80.0  # konservativ hell

def _try_init_devicehub():
    global _devicehub
    if _devicehub is not None:
        return _devicehub
    try:
        from core.device_hub import get_hub  # type: ignore
        _devicehub = get_hub()
        log("DeviceHub erkannt und initialisiert")

        # ---------------------------------------------------------------------
        # PRODUKTIONSFIX – Autostart & korrektes Logging (intern vs. extern)
        # ---------------------------------------------------------------------
        autostart = os.environ.get("OROMA_DEVICEHUB_AUTOSTART", "1").strip().lower() not in ("0", "false", "no", "off")
        try:
            if autostart:
                _devicehub.start()
        except Exception as e:
            log(f"DeviceHub Startfehler: {e}", logging.ERROR)

        # Status nach Startversuch auswerten (externe Frames zählen ebenfalls als 'alive')
        try:
            st = _devicehub.status() if hasattr(_devicehub, "status") else {}
        except Exception:
            st = {}

        # Status nach Startversuch korrekt auswerten:
        # DeviceHub.status() liefert die Kamera-Infos verschachtelt unter st["camera"].
        # (Historisch gab es hier ein falsches st.get("running") – das führt zu irreführenden Logs.)
        cam = st.get("camera") if isinstance(st, dict) else None
        cam = cam if isinstance(cam, dict) else {}
        ext_src = cam.get("external_source")
        ext_frames = int(cam.get("external_frames") or 0)
        ext_active = bool(cam.get("external_active"))
        ok_by_frame = bool(cam.get("ok_by_frame"))
        cam_running = bool(cam.get("running"))
        last_age = cam.get("last_frame_age")
        last_err = (cam.get("diag") or {}).get("last_error") if isinstance(cam.get("diag"), dict) else None

        if ext_frames > 0 or ext_src or ext_active:
            # Externes Feed ist ein gültiger Betriebsmodus (Provider/Hub-Mode): kein interner Capture-Thread.
            log(f"DeviceHub: externes Kamera-Feed aktiv (src={ext_src!r}, frames={ext_frames}, active={ext_active}).")
        elif cam_running or ok_by_frame:
            # Interner Capture-Thread läuft ODER Frames sind frisch genug (ok_by_frame).
            log(f"DeviceHub gestartet (intern aktiv: running={cam_running}, ok_by_frame={ok_by_frame}, last_frame_age={last_age}).")
        else:
            # Wichtig: Nicht als Fehler werten – kann im Provider-Modus normal sein (oder Kamera noch im Startup).
            extra = []
            if last_err:
                extra.append(f"last_error={last_err!r}")
            if last_age is not None:
                extra.append(f"last_frame_age={last_age}")
            suffix = (" · " + " · ".join(extra)) if extra else ""
            log("DeviceHub initialisiert, aber nicht running (kein Feed/kein Capture-Thread)." + suffix, logging.INFO)


        # =================================================================
        # BLOCK: DEVICEHUB_STATUS_WARMUP (SWAPPABLE)
        # Zweck:
        #   Direkt nach start() ist status() oft "zu früh" (first-frame warmup).
        #   Wir loggen deshalb einmalig nach kurzer Verzögerung den echten
        #   Kamera-Status (Frame-Freshness/last_error), ohne Busy-Wait.
        #
        # ENV:
        #   OROMA_DEVICEHUB_STATUS_WARMUP=1|0        (Default: 1)
        #   OROMA_DEVICEHUB_STATUS_WARMUP_SEC=2.0    (Default: 2.0)
        # =================================================================
        try:
            warmup_on = os.environ.get("OROMA_DEVICEHUB_STATUS_WARMUP", "1").strip().lower() not in ("0", "false", "no", "off")
            if warmup_on:

                    def _warmup_status_once(hub):
                        try:
                            try:
                                delay = float(os.environ.get("OROMA_DEVICEHUB_STATUS_WARMUP_SEC", "2.0"))
                            except Exception:
                                delay = 2.0
                            time.sleep(max(0.25, min(10.0, delay)))

                            try:
                                st2 = hub.status() if hasattr(hub, "status") else {}
                            except Exception:
                                st2 = {}

                            cam2 = st2.get("camera") if isinstance(st2, dict) else None
                            cam2 = cam2 if isinstance(cam2, dict) else {}
                            ext_src2 = cam2.get("external_source")
                            ext_frames2 = int(cam2.get("external_frames") or 0)
                            ext_active2 = bool(cam2.get("external_active"))
                            ok_by_frame2 = bool(cam2.get("ok_by_frame"))
                            cam_running2 = bool(cam2.get("running"))
                            last_age2 = cam2.get("last_frame_age")
                            last_err2 = (cam2.get("diag") or {}).get("last_error") if isinstance(cam2.get("diag"), dict) else None

                            # Einzeilige Warmup-Zusammenfassung (sichtbar, aber nicht spammy)
                            extra2 = []
                            if last_err2:
                                extra2.append(f"last_error={last_err2!r}")
                            if last_age2 is not None:
                                extra2.append(f"last_frame_age={last_age2}")
                            suffix2 = (" · " + " · ".join(extra2)) if extra2 else ""
                            if ext_frames2 > 0 or ext_src2 or ext_active2:
                                log(f"DeviceHub warmup: externes Feed aktiv (src={ext_src2!r}, frames={ext_frames2}, active={ext_active2})." + suffix2)
                            else:
                                log(f"DeviceHub warmup: intern (running={cam_running2}, ok_by_frame={ok_by_frame2})." + suffix2)
                        except Exception as e:
                            log_suppressed(LOG, key="run.devicehub.warmup", msg="Suppressed exception (warmup status)", exc=e, level=logging.WARNING, interval_s=60)

                    threading.Thread(target=_warmup_status_once, args=(_devicehub,), daemon=True).start()
        except Exception as e:
            log_suppressed(LOG, key="run.devicehub.warmup.outer", msg="Suppressed exception (warmup status outer)", exc=e, level=logging.WARNING, interval_s=60)
        # =================================================================
        # END BLOCK: DEVICEHUB_STATUS_WARMUP
        # =================================================================

    except Exception as e:
        log(f"DeviceHub nicht verfügbar: {e}", logging.INFO)
        _devicehub = None
    return _devicehub

def _compute_luma_from_frame(frame) -> Optional[float]:
    try:
        import numpy as np  # lazy
        if isinstance(frame, (bytes, bytearray)):
            return None
        arr = np.asarray(frame)
        if arr.size == 0:
            return None
        if arr.ndim == 2:
            y = float(arr.mean())
        elif arr.ndim == 3 and arr.shape[2] >= 3:
            R, G, B = arr[..., 2], arr[..., 1], arr[..., 0]
            y = float(0.2126 * R.mean() + 0.7152 * G.mean() + 0.0722 * B.mean())
        else:
            y = float(arr.mean())
        y = max(_LIGHT_MIN, min(_LIGHT_MAX, (y / 255.0) * 100.0))
        return y
    except Exception:
        return None

def _start_luma_sampler() -> None:
    global _luma_thread, _luma_running, _luma_cached
    if _luma_thread and _luma_thread.is_alive():
        return
    hub = _try_init_devicehub()
    if not hub:
        log("Luma-Sampler nicht gestartet (kein DeviceHub).", logging.INFO)
        return
    _luma_running = True

    def loop():
        global _luma_cached
        log(f"Luma-Sampler aktiv (Intervall={_LIGHT_IV}s, Quelle=camera)")
        while _luma_running:
            try:
                val = None
                for attr in ("get_light_level", "light_level", "luma", "get_luma"):
                    if hasattr(hub, attr):
                        try:
                            v = getattr(hub, attr)()
                            if isinstance(v, (int, float)):
                                val = float(v); break
                        except Exception as e:
                            logger.debug("DeviceHub luma getter failed", exc_info=e)
                            pass
                if val is not None:
                    _luma_cached = max(_LIGHT_MIN, min(_LIGHT_MAX, val))
                else:
                    frame = None
                    for getter in ("get_frame", "read_frame", "read", "last_frame", "get_latest_frame"):
                        if hasattr(hub, getter):
                            try:
                                if getter == "get_latest_frame":
                                    got = getattr(hub, getter)(ensure_start=(not _external_camera_provider_active()))
                                else:
                                    got = getattr(hub, getter)()
                                frame = got[0] if isinstance(got, (tuple, list)) and got else got
                                if frame is not None:
                                    break
                            except Exception:
                                frame = None
                    y = _compute_luma_from_frame(frame) if frame is not None else None
                    if y is not None:
                        _luma_cached = y
            except Exception as e:
                log(f"Luma-Sampler Warnung: {e}", logging.WARNING)

            for _ in range(_LIGHT_IV):
                if not _luma_running:
                    break
                time.sleep(1)

        log("Luma-Sampler gestoppt")

    _luma_thread = threading.Thread(target=loop, daemon=True)
    _luma_thread.start()

def _stop_luma_sampler() -> None:
    global _luma_running, _luma_thread
    _luma_running = False
    t = _luma_thread; _luma_thread = None
    if t:
        try: t.join(timeout=3)
        except Exception: pass

def _build_light_sensor() -> Optional[Callable[[], float]]:
    src = _LIGHT_SRC
    if src == "camera":
        if _try_init_devicehub():
            _start_luma_sampler()
            return lambda: _luma_cached
        log("LIGHT_SOURCE=camera, aber kein DeviceHub → Fallback dummy=80", logging.WARNING)
        return lambda: 80.0
    if src == "dummy":
        return lambda: 80.0
    if src == "off":
        return None
    return lambda: 80.0

# -----------------------------------------------------------------------------
# DreamWorker + CircadianController
# -----------------------------------------------------------------------------
from core.dream_worker import DreamWorker
from core.langzeitgedaechtnis import LangzeitGedaechtnis
from core.circadian_controller import CircadianController

DREAM_ENABLED = os.environ.get("OROMA_DREAM_ENABLED", "true").lower() not in ("0","false","no","off")
DREAM_INTERVAL = int(os.environ.get("OROMA_DREAM_INTERVAL", "1800"))
OROMA_BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
ORCHESTRATOR_MARKER = os.path.join(OROMA_BASE, ".use_orchestrator")
ORCHESTRATOR_MODE = os.path.exists(ORCHESTRATOR_MARKER)
_CIRCADIAN_ENV = os.environ.get("OROMA_CIRCADIAN_ENABLED", "").strip().lower()
if _CIRCADIAN_ENV:
    CIRCADIAN_ENABLED = _CIRCADIAN_ENV in ("1", "true", "yes", "y", "on")
else:
    CIRCADIAN_ENABLED = True if ORCHESTRATOR_MODE else DREAM_ENABLED
LOCAL_DREAM_EXEC_ENABLED = bool(DREAM_ENABLED and not ORCHESTRATOR_MODE)
PHASE_PATH = os.environ.get("OROMA_PHASE_PATH", os.path.join(OROMA_BASE, "data", "state", "phase.json"))

_dream_thread: Optional[DreamWorker] = None
_circ_thread: Optional[CircadianController] = None
_circ_queue: Optional[Queue] = None

def _start_dream() -> None:
    global _dream_thread
    try:
        mem = LangzeitGedaechtnis()
        _dream_thread = DreamWorker(memory=mem, interval=DREAM_INTERVAL)
        _dream_thread.start()
        log(f"DreamWorker gestartet (Intervall={DREAM_INTERVAL}s)")
    except Exception as e:
        log(f"DreamWorker Fehler: {e}", logging.ERROR)

def _stop_dream() -> None:
    global _dream_thread
    if _dream_thread:
        try:
            _dream_thread.stop()
            _dream_thread.join(timeout=5)
        except Exception as e:
            logger.warning("DreamWorker stop/join failed", exc_info=e)
            pass
        _dream_thread = None

def _circ_callback(mode: str) -> None:
    if mode == "DREAM":
        if not _dream_thread or not _dream_thread.is_alive():
            _start_dream()
    elif mode == "DAY":
        _stop_dream()

def _write_phase_file(mode: str) -> None:
    try:
        os.makedirs(os.path.dirname(PHASE_PATH), exist_ok=True)
        st = _circ_thread.get_status() if _circ_thread and hasattr(_circ_thread, "get_status") else {}
        d = {
            "phase": mode,
            "ts": int(time.time()),
            "threshold": st.get("threshold"),
            "delay_min": st.get("delay_min"),
            "poll_sec": st.get("poll_sec"),
            "source": "controller",
        }
        with open(PHASE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        logger.debug("Phase file write failed", exc_info=e)
        pass

# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
def run() -> None:
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", "8080"))

    if AGENT_ENABLED:
        log(f"Starting AgentLoop (dt={AGENT_DT}s)")
        global _agent_thread
        _agent_thread = threading.Thread(target=_start_agent, daemon=True)
        _agent_thread.start()
    else:
        log("AgentLoop disabled")

    # --------------------------------------------------------------------------
    # Audio – Always-On Mic (optional, analog zu Kamera-Always-On-Logik)
    # --------------------------------------------------------------------------
    # Motivation:
    #   - Wenn OROMA_AUDIO_ENABLE aktiv ist, kann ORÓMA das Mikro dauerhaft offen
    #     halten (z.B. Jabra Speak2 55). Das stabilisiert Capture + ermöglicht
    #     Audio-SnapTokens / ASR ohne "erst UI klicken".
    #
    # Steuerung:
    #   OROMA_AUDIO_ALWAYS_ON=1  -> Mic beim Service-Start öffnen
    #   OROMA_AUDIO_SNAPS=1      -> AgentLoop erzeugt Audio-SnapTokens (Hook)
    #
    # Robustheit:
    #   - Wenn kein Gerät vorhanden ist oder Start scheitert, wird nur geloggt.
    # --------------------------------------------------------------------------
    try:
        ae = str(os.environ.get("OROMA_AUDIO_ENABLE", "")).strip().lower()
        ao = str(os.environ.get("OROMA_AUDIO_ALWAYS_ON", "")).strip().lower()
        snaps = str(os.environ.get("OROMA_AUDIO_SNAPS", "")).strip().lower()
        if ae in ("1", "true", "yes", "y", "on") and (ao in ("1", "true", "yes", "y", "on") or snaps in ("1", "true", "yes", "y", "on")):
            hub = _try_init_devicehub()
            ok = bool(hub and hub.start_mic(client="boot"))
            if ok:
                log("Audio mic: always-on gestartet")
            else:
                log("Audio mic: always-on NICHT gestartet (ok=false)")
    except Exception as e:
        log(f"Audio mic: always-on Start fehlgeschlagen: {e!r}")

    if ORCHESTRATOR_MODE and DREAM_ENABLED and not LOCAL_DREAM_EXEC_ENABLED:
        log("Orchestrator-Modus erkannt: lokaler DreamWorker in run_oroma.py bleibt deaktiviert; Phase-Datei/Circadian bleiben aktiv")

    if CIRCADIAN_ENABLED:
        global _circ_thread, _circ_queue
        _circ_queue = Queue()
        light_sensor = _build_light_sensor()  # None → Controller nutzt internen Default
        try:
            _circ_thread = CircadianController(light_sensor=light_sensor, event_queue=_circ_queue)
            # Modus-Patch: Phase-Datei immer schreiben; lokaler Dream nur außerhalb
            # des Orchestrator-Modus starten/stoppen.
            orig_set_mode = getattr(_circ_thread, "_set_mode", None)
            if callable(orig_set_mode):
                def patched_set_mode(mode: str):
                    orig_set_mode(mode)
                    if LOCAL_DREAM_EXEC_ENABLED:
                        _circ_callback(mode)
                    _write_phase_file(mode)
                _circ_thread._set_mode = patched_set_mode  # type: ignore[attr-defined]
            _circ_thread.start()
            src = _LIGHT_SRC if light_sensor else "controller-default"
            log(
                "CircadianController gestartet "
                f"(LightSource={src}, orchestrator_mode={ORCHESTRATOR_MODE}, "
                f"local_dream_exec={LOCAL_DREAM_EXEC_ENABLED})"
            )

            # INSTANCE-Bridge nur setzen, wenn Thread existiert
            try:
                if _circ_thread is not None:
                    setattr(CircadianController, "INSTANCE", _circ_thread)
                    if not hasattr(CircadianController, "instance"):
                        @classmethod
                        def instance(cls):
                            return getattr(cls, "INSTANCE", None)
                        setattr(CircadianController, "instance", instance)
            except Exception as e:
                log(f"INSTANCE-Bridge für CircadianController fehlgeschlagen: {e}", logging.WARNING)

            # Erste Phase-Datei initial schreiben
            try:
                st0 = _circ_thread.get_status() if hasattr(_circ_thread, "get_status") else {}
                _write_phase_file(st0.get("phase", "DAY"))
            except Exception as e:
                logger.debug("Initial phase file write failed", exc_info=e)
                pass

        except Exception as e:
            log(f"CircadianController Fehler: {e}", logging.ERROR)
    else:
        log(f"CircadianController disabled (orchestrator_mode={ORCHESTRATOR_MODE}, dream_enabled={DREAM_ENABLED})")

    # Saubere Signale
    def _sig(signum, frame):
        log(f"Received signal {signum}, stopping...")
        _stop_agent()
        _stop_dream()
        if _circ_thread:
            try: _circ_thread.stop()
            except Exception: pass
        _stop_luma_sampler()
        try:
            if _devicehub: _devicehub.stop()
        except Exception:
            pass

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig)
        except Exception as e:
            logger.debug("signal.signal registration failed", exc_info=e)
            pass

    # -------------------------------------------------------------------------
    # Flask Server Concurrency (PRODUKTIONSFIX)
    # -------------------------------------------------------------------------
    # Problem (aus Feld-Logs):
    #   Ein blockierender Request (z.B. PTZ-Command, Snapshot, DB busy_timeout)
    #   kann in single-threaded Flask den *gesamten* UI-Server blockieren →
    #   curl/Safari timeouts, UI wirkt "eingefroren".
    #
    # Lösung:
    #   threaded=True (Werkzeug) – pro Request ein Thread.
    #   Default: EIN (1), kann per ENV deaktiviert werden.
    #
    # ENV:
    #   OROMA_FLASK_THREADED=1|0   (Default 1)
    # -------------------------------------------------------------------------
    try:
        threaded = os.environ.get("OROMA_FLASK_THREADED", "1").strip().lower() in ("1","true","yes","on")
    except Exception:
        threaded = True

    # Werkzeug/Flask Access-Logging beruhigen:
    # Standardmäßig schreibt der Development-Server jeden 200er-Request (z. B.
    # /video/snapshot.jpg Polling) als INFO in den StreamHandler. Unter systemd
    # landet das im service.err.log und bläht das Error-Log künstlich auf.
    # Für den produktiven Headless-Betrieb senken wir daher den werkzeug-Logger
    # standardmäßig auf ERROR. Detail-Access-Logs können bei Bedarf gezielt per
    # ENV wieder aktiviert werden.
    flask_access_level = str(os.environ.get("OROMA_FLASK_ACCESS_LOG_LEVEL", "ERROR") or "ERROR").upper()
    try:
        wz = logging.getLogger("werkzeug")
        wz.setLevel(getattr(logging, flask_access_level, logging.ERROR))
    except Exception as e:
        logger.debug("werkzeug logger level setup failed", exc_info=e)
    try:
        flask_app.logger.setLevel(max(getattr(logging, flask_access_level, logging.ERROR), logging.WARNING))
    except Exception as e:
        logger.debug("flask_app logger level setup failed", exc_info=e)

    log(f"Starting Flask UI on {host}:{port} (threaded={threaded}, access_log={flask_access_level})")
    try:
        flask_app.run(host=host, port=port, debug=False, use_reloader=False, threaded=threaded)
    finally:
        log("Flask UI stopped")
        _stop_agent()
        _stop_dream()
        if _circ_thread:
            try: _circ_thread.stop()
            except Exception as e:
                logger.warning("CircadianController.stop() failed", exc_info=e)
        _stop_luma_sampler()
        try:
            if _devicehub: _devicehub.stop()
        except Exception as e:
            logger.warning("DeviceHub.stop() failed during shutdown", exc_info=e)
            pass
        log("run_oroma shutdown complete")

if __name__ == "__main__":
    run()
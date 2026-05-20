#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/health_ui.py
# Projekt:   ORÓMA (Headless · Ops/Health Dashboard)
# Modul:     Health UI Blueprint – System-/Service-Status, Logs, History (CPU/RAM/GPU temp), Update-Check/Run, Compat-Routen
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert das „Health“-Dashboard für ORÓMA als Flask Blueprints:
#   - bp        (url_prefix="/health")  → neue, saubere Health-Routen
#   - bp_compat (ohne prefix)           → Kompatibilitäts-Endpunkte für ältere UIs/Clients
#
# Das Health-Dashboard liefert:
#   1) UI Seite: health.html
#   2) JSON Health Payload: CPU/RAM, Pi GPU (vcgencmd), Disk partitions, db/log file sizes
#   3) Log-Auszug: tail von /opt/ai/oroma/logs/service.out.log
#   4) History: In-Memory Verlauf für Charts (window_sec Filter)
#   5) Selftest: schnelle textuelle Diagnose-Ausgabe
#   6) System-Info: OS/Kernel/Python, uptime etc.
#   7) Updates: apt-get -s upgrade (check) und apt-get update && apt-get -y upgrade (run)
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine GUI-Bibliotheken.
# - Best effort: Health darf ORÓMA nicht crashen, selbst wenn Tools fehlen.
# - Keine DB Writes: Health liest Systeminfos + Dateien; DB-Größen nur via os.path.getsize().
# - Schnell: CPU/RAM read nutzt kurze psutil Abfrage (cpu_percent interval=0.1).
#
# ABHÄNGIGKEITEN (EXAKT IM CODE)
# ──────────────────────────────
# - psutil (CPU/RAM/Disk partitions/usage)
# - subprocess (apt-get check/run)
# - platform, shutil, os, time, datetime.timedelta
# - Flask: Blueprint, jsonify, render_template, Response, request
# - optional: core.agent_loop (für Status/indikative Infos; Import kann fehlschlagen → None)
#
# BLUEPRINTS / ROUTES (EXAKT IM CODE)
# ───────────────────────────────────
# bp = Blueprint("health", __name__, url_prefix="/health")
#
# UI:
#   GET  /health/                         → render_template("health.html")
#
# API (bp):
#   GET  /health/api/health               → JSON: _health_payload()
#   GET  /health/api/health/logs?n=300    → text/plain: letzte n Zeilen aus service.out.log
#   GET  /health/api/history?window_sec=86400 → JSON: In-Memory Verlauf (HISTORY) gefiltert
#   GET  /health/api/selftest             → text/plain: kompakter Selftest
#   GET  /health/api/system               → JSON: _system_info()
#   GET  /health/api/updates/check        → JSON: apt-get -s upgrade Auswertung
#   POST /health/api/updates/run          → text/plain: apt-get update && apt-get -y upgrade (gekürzt)
#
# Kompatibilität:
# bp_compat = Blueprint("health_compat", __name__)
#   GET  /api/health                      → mapped auf api_health()
#   GET  /api/health/logs                 → mapped auf api_health_logs()
#
# HISTORY-MODELL (IN-MEMORY)
# ──────────────────────────
# Globale Strukturen:
#   START_TS = time.time()
#   HISTORY: List[Dict[str,Any]] = []
#
# Bei jedem api_health() wird ein Datensatz appended:
#   {"ts": <unix>, "cpu": <percent>, "ram": <percent>, "gpu_temp": <temp|None>}
#
# Vorteil:
# - kein DB write overhead
# - Charts funktionieren sofort nach Start
# Nachteil:
# - History geht bei Neustart verloren (bewusst akzeptiert; „Health“ ist ops-nah)
#
# GPU / vcgencmd
# ──────────────
# _read_pi_gpu() nutzt vcgencmd, falls verfügbar:
# - temp (measure_temp)
# - core freq (measure_clock core) → MHz
# - core volts (measure_volts core)
# Falls vcgencmd fehlt → status="unavailable"
#
# CPU/RAM
# ───────
# _read_cpu_ram():
# - cpu_usage = psutil.cpu_percent(interval=None)
# - ram_usage = psutil.virtual_memory().percent
#
# DATEIGRÖSSEN (DB/LOG)
# ────────────────────
# In _health_payload() werden Größen zusammengetragen:
# - db_dir  = "/opt/ai/oroma/data"  → alle *.db → size MB
# - log_dir = "/opt/ai/oroma/logs"  → alle *.log → size MB
# Dazu Disk partitions (psutil.disk_partitions + disk_usage).
#
# LOG-TAIL
# ────────
# _read_logs(n=300):
# - liest /opt/ai/oroma/logs/service.out.log (falls vorhanden)
# - gibt die letzten n Zeilen zurück
# - Response mimetype="text/plain"
#
# UPDATES (APT)
# ─────────────
# _check_updates():
# - subprocess.run(["apt-get","-s","upgrade"], timeout=30)
# - extrahiert Zeile mit "upgraded," für UI
#
# _run_updates():
# - subprocess.run("apt-get update && apt-get -y upgrade", shell=True, timeout=900)
# - gibt stdout+stderr gekürzt auf die letzten 5000 Zeichen zurück (Response text/plain)
#
# WICHTIG:
# - Diese Funktionen erfordern Root/Rechte bzw. passende sudo/systemd Umgebung.
# - In nicht-privilegierten Deploys wird status="error" zurückgegeben (best effort).
#
# ENV / KONFIG
# ────────────
# Dieses Modul nutzt kaum ENV direkt, außer:
# - OROMA_NPU_STATUS (string, default "no-streams") → wird in payload aufgenommen
#
# Alles andere sind feste Pfade:
# - /opt/ai/oroma/data
# - /opt/ai/oroma/logs/service.out.log
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Best-effort bleibt: fehlendes psutil/vcgencmd darf Health nicht crashen (return status="error/unavailable").
# - History bleibt in-memory (kein DB write für Health).
# - Log-Tail bleibt text/plain (UI/Debug kompatibel, copy/paste-freundlich).
# - Compat-Routen bleiben erhalten (/api/health, /api/health/logs), weil ältere Clients das nutzen können.
# - Updates output bleibt gekürzt (UI darf nicht riesige apt logs ziehen).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import time
import shutil
import subprocess
import platform
import psutil
from datetime import timedelta
from typing import Dict, Any, List
from flask import Blueprint, jsonify, render_template, Response, request

try:
    from core import agent_loop  # type: ignore
except Exception:
    agent_loop = None  # type: ignore

# Optional: DBWriter state (Stufe C) – best effort
try:
    from core import db_writer_client  # type: ignore
except Exception:
    db_writer_client = None  # type: ignore

# ---------------------------------------------------------------------------#
# Blueprints
# ---------------------------------------------------------------------------#
bp = Blueprint("health", __name__, url_prefix="/health")
bp_compat = Blueprint("health_compat", __name__)

START_TS = time.time()

# Prime psutil CPU measurement (first call would otherwise return 0.0).
try:
    psutil.cpu_percent(interval=None)
except Exception:
    pass
HISTORY: List[Dict[str, Any]] = []

# ---------------------------------------------------------------------------#
# Lightweight Cache (Health-UI Hardening)
# ---------------------------------------------------------------------------#
# Motivation:
#   Die Health-Seite pollt /health/api/health regelmäßig. Einige Bestandteile
#   (vcgencmd, disk_partitions/disk_usage, dir-listing) können auf Pi-Systemen
#   spürbar CPU kosten – insbesondere wenn mehrere Clients gleichzeitig offen sind
#   oder wenn Mountpoints/FS-Backends träge reagieren.
#
#   Daher: kleiner In-Memory Cache mit TTL pro Datenblock. CPU/RAM bleibt „live“.
#
# Design:
#   - Headless, stdlib-only
#   - thread-safe (Flask threaded=True)
#   - best-effort: Fehler fallen auf leere/Default Werte zurück
#
# ENV (optional):
#   OROMA_HEALTH_CACHE_GPU_SEC        (default 2.0)
#   OROMA_HEALTH_CACHE_FILES_SEC      (default 5.0)
#   OROMA_HEALTH_CACHE_PARTITIONS_SEC (default 30.0)
#   OROMA_HEALTH_CACHE_SYSTEM_SEC     (default 60.0)
#
# Hinweis:
#   Diese Cache-Schicht ändert keine Semantik, nur die Berechnungshäufigkeit.

import threading

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}   # key -> (ts: float, value: Any)

def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except Exception:
        return float(default)

def _cache_get(key: str, ttl_sec: float, fn):
    now = time.time()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is not None:
            ts, val = item
            if (now - float(ts)) <= float(ttl_sec):
                return val
    # Compute outside lock (avoid long hold)
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now, val)
    return val

# ---------------------------------------------------------------------------#
# Helpers
# ---------------------------------------------------------------------------#
def _uptime_human() -> str:
    return str(timedelta(seconds=int(time.time() - START_TS)))


def _vcgencmd_exists() -> bool:
    return shutil.which("vcgencmd") is not None


def _read_pi_gpu() -> Dict[str, Any]:
    if not _vcgencmd_exists():
        return {"status": "unavailable", "temp_c": None, "freq_mhz": None, "core_voltage_v": None}
    try:
        raw_t = os.popen("vcgencmd measure_temp").read().strip()
        temp_c = float(raw_t.split("=")[-1].replace("'C", "")) if "temp=" in raw_t else None

        raw_f = os.popen("vcgencmd measure_clock core").read().strip()
        freq_mhz = int(raw_f.split("=")[-1]) // 1_000_000 if "=" in raw_f else None

        raw_v = os.popen("vcgencmd measure_volts core").read().strip()
        core_voltage_v = float(raw_v.split("=")[-1].lower().replace("v", "")) if "volt=" in raw_v else None

        return {"status": "ok", "temp_c": temp_c, "freq_mhz": freq_mhz, "core_voltage_v": core_voltage_v}
    except Exception as e:
        return {"status": f"error: {e}", "temp_c": None, "freq_mhz": None, "core_voltage_v": None}


def _read_cpu_ram() -> Dict[str, float]:
    return {
        "cpu_usage": round(psutil.cpu_percent(interval=0.1), 1),
        "ram_usage": round(psutil.virtual_memory().percent, 1),
    }


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _system_info() -> Dict[str, Any]:
    return {
        "os_release": " ".join(platform.linux_distribution()) if hasattr(platform, "linux_distribution") else platform.platform(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_total_mb": round(psutil.virtual_memory().total / (1024 * 1024), 1),
    }


def _append_history(sample: Dict[str, Any]) -> None:
    HISTORY.append(sample)
    if len(HISTORY) > 2000:
        del HISTORY[: len(HISTORY) - 2000]


def _make_status_payload() -> Dict[str, Any]:
    cr = _read_cpu_ram()
    gpu_ttl = _float_env("OROMA_HEALTH_CACHE_GPU_SEC", 2.0)
    gpu = _cache_get("gpu", gpu_ttl, _read_pi_gpu)

    npu_status = os.environ.get("OROMA_NPU_STATUS", "no-streams")
    gpu_status_str = "ok" if gpu["status"] == "ok" else gpu["status"]

    files_ttl = _float_env("OROMA_HEALTH_CACHE_FILES_SEC", 5.0)
    parts_ttl = _float_env("OROMA_HEALTH_CACHE_PARTITIONS_SEC", 30.0)
    sys_ttl   = _float_env("OROMA_HEALTH_CACHE_SYSTEM_SEC", 60.0)

    def _read_files_payload() -> Dict[str, Any]:
        db_dir = "/opt/ai/oroma/data"
        log_dir = "/opt/ai/oroma/logs"
        db_files = {f: _file_size_mb(os.path.join(db_dir, f)) for f in os.listdir(db_dir) if f.endswith(".db")} if os.path.isdir(db_dir) else {}
        log_files = {f: _file_size_mb(os.path.join(log_dir, f)) for f in os.listdir(log_dir) if f.endswith(".log")} if os.path.isdir(log_dir) else {}
        return {"db_files": db_files, "log_files": log_files}

    def _read_partitions() -> List[Dict[str, Any]]:
        partitions: List[Dict[str, Any]] = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
                partitions.append({"device": p.device, "mount": p.mountpoint, "fstype": p.fstype,
                                   "total_gb": round(u.total / (1024**3), 1),
                                   "used_gb": round(u.used / (1024**3), 1),
                                   "percent": u.percent})
            except Exception:
                continue
        return partitions

    files_payload = _cache_get("files", files_ttl, _read_files_payload)
    partitions = _cache_get("partitions", parts_ttl, _read_partitions)

    payload = {
        "ok": True,
        "cpu_usage": cr["cpu_usage"],
        "ram_usage": cr["ram_usage"],
        "uptime_human": _uptime_human(),
        "agent_running": bool(agent_loop and agent_loop.status().get("running", False)),
        "npu_status": npu_status,
        "gpu_status": gpu_status_str,
        "gpu": {"temp_c": gpu["temp_c"], "freq_mhz": gpu["freq_mhz"], "core_voltage_v": gpu["core_voltage_v"]},
        "db_files": (files_payload or {}).get("db_files", {}),
        "log_files": (files_payload or {}).get("log_files", {}),
        "partitions": partitions,
        "system": _cache_get("system", sys_ttl, _system_info),
    }

    # DBWriter Queue/Stats (best effort; darf Health niemals brechen)
    # Ziel: Transparenz für Backpressure/Hotspots ("wer schreibt am meisten?").
    try:
        if db_writer_client is not None and bool(getattr(db_writer_client, "enabled", lambda: False)()):
            payload["db_writer"] = db_writer_client.state(timeout_ms=800)
        else:
            payload["db_writer"] = {"ok": False, "error": "db_writer not enabled"}
    except Exception as e:
        payload["db_writer"] = {"ok": False, "error": str(e)}

    _append_history({"ts": int(time.time()), "cpu": cr["cpu_usage"], "ram": cr["ram_usage"], "gpu_temp": gpu["temp_c"]})
    return payload


def _read_logs(n: int = 300) -> str:
    log_path = "/opt/ai/oroma/logs/service.out.log"
    if not os.path.isfile(log_path):
        return "Logdatei nicht gefunden."
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        return "".join(f.readlines()[-n:])


def _check_updates() -> Dict[str, Any]:
    try:
        res = subprocess.run(["apt-get", "-s", "upgrade"], capture_output=True, text=True, timeout=30)
        line = [l for l in res.stdout.splitlines() if "upgraded," in l]
        return {"status": "ok", "info": line[0] if line else "keine Info"}
    except Exception as e:
        return {"status": "error", "info": str(e)}


def _run_updates() -> str:
    try:
        res = subprocess.run("apt-get update && apt-get -y upgrade", shell=True, capture_output=True, text=True, timeout=900)
        return (res.stdout + "\n" + res.stderr)[-5000:]
    except Exception as e:
        return f"Update-Fehler: {e}"

# ---------------------------------------------------------------------------#
# Routes
# ---------------------------------------------------------------------------#
@bp.route("/")
def index():
    return render_template("health.html")

@bp.route("/api/health")
def api_health(): return jsonify(_make_status_payload())

@bp.route("/api/health/logs")
def api_health_logs():
    n = int(request.args.get("n", 300))
    return Response(_read_logs(n=n), mimetype="text/plain")

@bp.route("/api/history")
def api_history():
    window = int(request.args.get("window_sec", 86400))
    cutoff = int(time.time()) - window
    items = [h for h in HISTORY if h["ts"] >= cutoff]
    return jsonify({"ok": True, "items": items, "count": len(items)})

@bp.route("/api/selftest")
def api_selftest():
    last = HISTORY[-1] if HISTORY else {}
    return Response(f"✅ ORÓMA Health Selftest\nCPU: {last.get('cpu','?')} %\nRAM: {last.get('ram','?')} %\nGPU Temp: {last.get('gpu_temp','?')} °C\n", mimetype="text/plain")

@bp.route("/api/system")
def api_system(): return jsonify(_system_info())

@bp.route("/api/updates/check")
def api_updates_check(): return jsonify(_check_updates())

@bp.route("/api/updates/run", methods=["POST"])
def api_updates_run(): return Response(_run_updates(), mimetype="text/plain")

# Kompat-Routen
@bp_compat.route("/api/health")
def api_health_compat(): return api_health()

@bp_compat.route("/api/health/logs")
def api_health_logs_compat(): return api_health_logs()
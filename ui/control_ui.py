#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/control_ui.py
# Projekt:   ORÓMA (Flask UI · Headless Ops)
# Modul:     Control UI – AgentLoop Start/Stop + Phase/Circadian Status + Systemd Service Control + optional Reboot
# Version:   v3.7.3+nmr-lite-status-v1
# Stand:     2026-05-26
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / OPS-ROLLE
# ─────────────────
# Dieses Modul liefert die „Ops/Control“-Webseite, um ORÓMA im Live-Betrieb
# zu überwachen und gezielt zu steuern – ohne SSH und ohne Desktop:
#   - AgentLoop: Start/Stop + Status (dt/tick/heartbeat/in_hook)
#   - NMR-Lite: optionaler Live-Statusblock aus agent_loop.status()["nmr_lite"]
#   - Phase/Circadian: robuste Phase-Ermittlung (DAY/DREAM/unknown)
#   - Services: erlaubte systemd Units abfragen und (re)starten/stoppen
#   - Optional: System-Reboot via API (hart abgesichert)
#
# HEADLESS-PRINZIP
# ────────────────
# Keine GUI-Abhängigkeiten. Systemd-Aufrufe erfolgen via subprocess/systemctl.
# Responses sind JSON, UI ist HTML+JS (control.html).
#
# BLUEPRINT
# ─────────
#   Blueprint: "control"
#   url_prefix: /control
#
# ROUTES
# ──────
# UI:
#   GET  /control/                      -> control.html
#
# API: Status
#   GET  /control/api/status            -> { ok, running, dt, tick, phase, circadian{...}, nmr_lite{...}, ... }
#
# API: AgentLoop
#   POST /control/api/start
#   POST /control/api/stop
#   POST /control/api/selftest
#
# API: systemd Services
#   GET  /control/api/services
#        Optional Query-Overrides:
#          ?allow=svc1,svc2   überschreibt ENV OROMA_SERVICE_ALLOW (Whitelist)
#          ?prefix=oroma      überschreibt ENV OROMA_SERVICE_PREFIX (Prefix-Filter)
#
#   POST /control/api/service/start     {name}
#   POST /control/api/service/stop      {name}
#   POST /control/api/service/restart   {name}
#
# API: Reboot (optional, default OFF)
#   POST /control/api/system/reboot
#
# SICHERHEIT (KRITISCH)
# ─────────────────────
# 1) Token-Gate nur für POST:
#    - Wenn OROMA_UI


from __future__ import annotations

import os
import json
import re
import time
import logging
import shlex
import subprocess
from typing import Tuple, Dict, Any, Optional, List

from flask import Blueprint, jsonify, render_template, request

# Optional: AgentLoop
try:
    from core import agent_loop
except Exception:
    agent_loop = None  # type: ignore

bp = Blueprint("control", __name__, url_prefix="/control")
LOG = logging.getLogger("oroma.ui.control")
LOG.setLevel(logging.INFO)

# ------------------------------- Token-Gate ----------------------------------

def _cfg_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN", "").strip()

def _extract_token() -> Optional[str]:
    h = request.headers.get("X-OROMA-TOKEN")
    if h:
        return h.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = request.args.get("token")
    if q:
        return q.strip()
    c = request.cookies.get("OROMA_UI_TOKEN")
    if c:
        return c.strip()
    return None

def _token_valid() -> bool:
    cfg = _cfg_token()
    if not cfg:
        return True
    return _extract_token() == cfg

def _json(data: Dict[str, Any], status: int = 200):
    resp = jsonify(data)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp

@bp.before_request
def _guard_posts():
    if request.method == "POST" and not _token_valid():
        return _json({"ok": False, "error": "Unauthorized"}, 401)
    return None

# ------------------------------- Phase-Status --------------------------------

def _get_circadian_status() -> Tuple[str, Dict[str, Any]]:
    phase = "unknown"
    st: Dict[str, Any] = {}
    try:
        from core.circadian_controller import CircadianController  # type: ignore
        cc = None
        if hasattr(CircadianController, "instance"):
            try:
                cc = CircadianController.instance()  # type: ignore[attr-defined]
            except Exception:
                cc = None
        if cc is None and hasattr(CircadianController, "INSTANCE"):
            try:
                cc = getattr(CircadianController, "INSTANCE", None)
            except Exception:
                cc = None
        if cc is not None and hasattr(cc, "get_status"):
            st = cc.get_status() or {}
            p = st.get("phase")
            if isinstance(p, str) and p:
                phase = p
    except Exception as e:
        LOG.debug("Circadian live status not available: %s", e)

    if (phase or "unknown").lower() == "unknown":
        path = os.environ.get("OROMA_PHASE_PATH", "/opt/ai/oroma/data/state/phase.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f) or {}
                p = str(d.get("phase") or "").upper()
                if p in ("DAY", "DREAM"):
                    phase = p
                st = dict(st or {})
                st.setdefault("ts", int(d.get("ts") or time.time()))
                st.setdefault("source", d.get("source", "file"))
                for k in ("threshold", "delay_min", "poll_sec"):
                    if k in d and k not in st:
                        st[k] = d[k]
        except Exception as e:
            LOG.debug("Phase file fallback failed: %s", e)

    return phase, st

# ------------------------------ Systemctl Helpers ----------------------------

def _servicectl_cmd() -> List[str]:
    raw = os.environ.get("OROMA_SERVICECTL_CMD", "/usr/bin/systemctl")
    return [p for p in shlex.split(raw) if p]

def _parse_allow_prefix_from_request() -> Tuple[List[str], str]:
    """
    Liest optionale Query-Overrides (?allow=...,?prefix=...) ODER fällt auf ENV zurück.
    """
    allow_q = (request.args.get("allow") or "").strip()
    prefix_q = (request.args.get("prefix") or "").strip()
    if allow_q or prefix_q:
        allow = [s.strip() for s in allow_q.split(",") if s.strip()] if allow_q else []
        prefix = prefix_q
        return allow, prefix

    # ENV-Fallback
    allow_env = [s.strip() for s in os.environ.get("OROMA_SERVICE_ALLOW", "oroma").split(",") if s.strip()]
    prefix_env = os.environ.get("OROMA_SERVICE_PREFIX", "").strip()
    return allow_env, prefix_env

def _allowed_services() -> List[str]:
    allow, prefix = _parse_allow_prefix_from_request()
    scanned: List[str] = []
    if prefix:
        try:
            cmd = _servicectl_cmd() + ["list-unit-files", "--type=service", "--no-legend", "--no-pager"]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=10)
            for line in (proc.stdout or "").splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                name = parts[0]
                if name.endswith(".service") and name.startswith(prefix):
                    scanned.append(name.replace(".service", ""))
        except Exception as e:
            LOG.debug("Prefix scan failed: %s", e)

    out: List[str] = []
    for n in allow + scanned:
        if n and n not in out:
            out.append(n)
    return out

def _service_status(name: str) -> Dict[str, Any]:
    cmd_is_active = _servicectl_cmd() + ["is-active", f"{name}.service"]
    cmd_is_enabled = _servicectl_cmd() + ["is-enabled", f"{name}.service"]
    try:
        a = subprocess.run(cmd_is_active, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        e = subprocess.run(cmd_is_enabled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        return {
            "name": name,
            "active": (a.stdout.strip() if a.stdout else ("active" if a.returncode == 0 else "inactive")),
            "enabled": (e.stdout.strip() if e.stdout else ("enabled" if e.returncode == 0 else "disabled")),
            "rc_active": a.returncode,
            "rc_enabled": e.returncode,
        }
    except Exception as ex:
        return {"name": name, "active": "unknown", "enabled": "unknown", "error": str(ex)}

def _run_cmd(cmd: List[str], timeout: int = 30) -> Dict[str, Any]:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "rc": p.returncode, "stdout": (p.stdout or "").strip(), "stderr": (p.stderr or "").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": 124, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {"ok": False, "rc": 1, "stdout": "", "stderr": str(e)}

# ------------------------------- UI / Status ---------------------------------

@bp.route("/")
def index():
    return render_template("control.html")

@bp.route("/api/status")
def api_status():
    try:
        running = False
        loop_dt = None
        tick = None
        last_heartbeat = None
        hooks = None
        nmr_lite = None

        # Debug/Breadcrumbs: defensiv initialisieren (falls agent_loop fehlt/kaputt)
        in_hook = None
        in_hook_since = 0
        last_hook = None
        last_hook_ms = 0.0

        if agent_loop:
            st = agent_loop.status() or {}
            running = bool(st.get("running", False))
            loop_dt = st.get("dt")
            tick = st.get("tick")
            last_heartbeat = st.get("last_heartbeat")

            # Optional: Hook-Breadcrumbs (nur vorhanden, wenn agent_loop sie führt)
            in_hook = st.get("in_hook")
            in_hook_since = st.get("in_hook_since")
            last_hook = st.get("last_hook")
            last_hook_ms = st.get("last_hook_ms")
            nmr_lite = st.get("nmr_lite")

            # Optional: Hook-Liste (sehr hilfreich bei Debugging)
            if hasattr(agent_loop, "get_registered_hooks"):
                try:
                    hooks = agent_loop.get_registered_hooks()
                except Exception:
                    hooks = None

        phase, circ = _get_circadian_status()
        return _json({
            "ok": True,
            "running": running,
            "dt": loop_dt,
            "tick": tick,
            "last_heartbeat": last_heartbeat,

            # Debug/Breadcrumbs (helfen Hänger zu identifizieren)
            "in_hook": in_hook,
            "in_hook_since": in_hook_since,
            "last_hook": last_hook,
            "last_hook_ms": last_hook_ms,

            "hooks": hooks or [],
            "nmr_lite": nmr_lite or {},
            "phase": phase or "unknown",
            "circadian": circ or {},
        })
    except Exception as e:
        LOG.exception("status failed: %s", e)
        return _json({"ok": False, "error": str(e)}, 500)

# ------------------------------- AgentLoop -----------------------------------

@bp.route("/api/start", methods=["POST"])
def api_start():
    if not agent_loop:
        return _json({"ok": False, "error": "agent_loop Modul fehlt"}, 500)
    try:
        dt = float(os.environ.get("OROMA_AGENT_DT", "0.25"))
        agent_loop.start(dt)
        return _json({"ok": True, "msg": "AgentLoop gestartet", "dt": dt})
    except Exception as e:
        LOG.exception("start failed: %s", e)
        return _json({"ok": False, "error": str(e)}, 500)

@bp.route("/api/stop", methods=["POST"])
def api_stop():
    if not agent_loop:
        return _json({"ok": False, "error": "agent_loop Modul fehlt"}, 500)
    try:
        agent_loop.stop()
        return _json({"ok": True, "msg": "AgentLoop gestoppt"})
    except Exception as e:
        LOG.exception("stop failed: %s", e)
        return _json({"ok": False, "error": str(e)}, 500)

@bp.route("/api/selftest", methods=["POST"])
def api_selftest():
    try:
        return _json({"ok": True, "result": {"snaps": True, "registry": True, "status": "OK"}})
    except Exception as e:
        LOG.exception("selftest failed: %s", e)
        return _json({"ok": False, "error": str(e)}, 500)

# ------------------------------- Services ------------------------------------

@bp.route("/api/services", methods=["GET"])
def api_services():
    try:
        items = []
        for name in _allowed_services():
            items.append(_service_status(name))
        return _json({"ok": True, "items": items})
    except Exception as e:
        LOG.exception("services failed: %s", e)
        return _json({"ok": False, "error": str(e)}, 500)

def _svc_action(name: str, verb: str):
    if not name:
        return _json({"ok": False, "error": "Service-Name fehlt"}, 400)
    allowed = _allowed_services()
    if name not in allowed:
        return _json({"ok": False, "error": f"Service '{name}' nicht erlaubt", "allow": allowed}, 403)
    cmd = _servicectl_cmd() + [verb, f"{name}.service"]
    res = _run_cmd(cmd)
    res["service"] = name
    res["cmd"] = " ".join(cmd)
    return _json(res, 200 if res.get("ok") else 500)

@bp.route("/api/service/start", methods=["POST"])
def api_service_start():
    data = request.get_json(silent=True) or {}
    return _svc_action(str(data.get("name","")).strip(), "start")

@bp.route("/api/service/stop", methods=["POST"])
def api_service_stop():
    data = request.get_json(silent=True) or {}
    return _svc_action(str(data.get("name","")).strip(), "stop")

@bp.route("/api/service/restart", methods=["POST"])
def api_service_restart():
    data = request.get_json(silent=True) or {}
    return _svc_action(str(data.get("name","")).strip(), "restart")



# ------------------------------- Logs (Tail) ---------------------------------
#
# Zweck:
#   • Schnelles "Tail" der wichtigsten ORÓMA-Logs direkt aus der UI heraus.
#   • Hilft enorm beim Debuggen, wenn der AgentLoop "steht" aber kein Fehler im
#     Status-JSON auftaucht.
#
# Sicherheit:
#   • Standardmäßig nur Zugriff auf /opt/ai/oroma/logs/ + Whitelist.
#   • Optional Token-Schutz auch für GET (/control/api/logtail) via OROMA_UI_TOKEN.
#
# ENV:
#   • OROMA_LOG_DIR="/opt/ai/oroma/logs"
#   • OROMA_LOGTAIL_ALLOW="service.err.log,orchestrator.out.log,devicehub_audit.log"
#   • OROMA_LOGTAIL_MAX_LINES="2000"
#
# API:
#   GET /control/api/logs                    → {ok, items:[...]}
#   GET /control/api/logtail?file=...&n=200  → {ok, file, n, lines:[...], truncated}
#     Optional:
#       &grep=TEXT   → filter (case-insensitive substring match)
#       &since_ts=UNIX → nur Zeilen mit Zeitstempel >= since_ts (best effort)
#       &reverse=1   → newest-first
# -----------------------------------------------------------------------------


def _log_dir() -> str:
    return os.environ.get("OROMA_LOG_DIR", "/opt/ai/oroma/logs").strip() or "/opt/ai/oroma/logs"


def _logtail_allow() -> List[str]:
    raw = os.environ.get("OROMA_LOGTAIL_ALLOW", "service.err.log,orchestrator.out.log,devicehub_audit.log")
    items = [x.strip() for x in raw.split(",") if x.strip()]
    out: List[str] = []
    for x in items:
        b = os.path.basename(x)
        if b and b not in out:
            out.append(b)
    return out


def _logtail_max_lines() -> int:
    try:
        v = int(os.environ.get("OROMA_LOGTAIL_MAX_LINES", "2000"))
        return max(50, min(20000, v))
    except Exception:
        return 2000


def _safe_log_path(basename: str) -> Optional[str]:
    """
    Erlaubt nur:
      • Basename in Whitelist
      • Pfad unterhalb OROMA_LOG_DIR
    """
    base = os.path.basename(basename or "")
    if not base:
        return None
    if base not in _logtail_allow():
        return None
    root_dir = os.path.abspath(_log_dir())
    path = os.path.abspath(os.path.join(root_dir, base))
    if not path.startswith(root_dir + os.sep) and path != root_dir:
        return None
    return path


def _parse_log_ts(line: str) -> Optional[int]:
    """
    Best effort: erkennt "YYYY-MM-DD HH:MM:SS" am Anfang der Zeile (wie in service.err.log).
    Rückgabe: Unix-Timestamp (lokal), sonst None.
    """
    try:
        m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", line)
        if not m:
            return None
        dt_s = f"{m.group(1)} {m.group(2)}"
        # local time -> epoch (naiv)
        import datetime as _dt
        dt = _dt.datetime.strptime(dt_s, "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp())
    except Exception:
        return None


def _tail_lines(path: str, n: int) -> List[str]:
    """
    Einfaches Tail ohne externe Tools: liest die Datei komplett ein (für Logs ok, da whitelisted).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[-n:] if n > 0 else []
    except Exception as e:
        raise RuntimeError(str(e))


@bp.route("/api/logs", methods=["GET"])
def api_logs():
    # Optional: Token schützen (auch für GET), da Logs intern sein können.
    if not _token_valid():
        return _json({"ok": False, "error": "Unauthorized"}, 401)
    return _json({"ok": True, "items": _logtail_allow(), "dir": _log_dir()})


@bp.route("/api/logtail", methods=["GET"])
def api_logtail():
    # Optional: Token schützen (auch für GET), da Logs intern sein können.
    if not _token_valid():
        return _json({"ok": False, "error": "Unauthorized"}, 401)

    f = (request.args.get("file") or "service.err.log").strip()
    n_raw = (request.args.get("n") or "200").strip()
    grep = (request.args.get("grep") or "").strip()
    reverse = (request.args.get("reverse") or "").strip() in ("1", "true", "yes", "on")
    since_ts_raw = (request.args.get("since_ts") or "").strip()

    try:
        n = int(n_raw)
    except Exception:
        n = 200
    n = max(1, min(_logtail_max_lines(), n))

    p = _safe_log_path(f)
    if not p:
        return _json({"ok": False, "error": f"Log '{f}' nicht erlaubt", "allow": _logtail_allow()}, 403)

    try:
        lines = _tail_lines(p, n)
        truncated = False

        # since_ts filter (best effort)
        if since_ts_raw:
            try:
                since_ts = int(float(since_ts_raw))
            except Exception:
                since_ts = 0
            if since_ts > 0:
                flt: List[str] = []
                for line in lines:
                    ts = _parse_log_ts(line)
                    if ts is None or ts >= since_ts:
                        flt.append(line)
                lines = flt

        # grep filter
        if grep:
            g = grep.lower()
            lines = [ln for ln in lines if g in ln.lower()]

        if reverse:
            lines = list(reversed(lines))

        return _json({"ok": True, "file": os.path.basename(p), "n": n, "lines": lines, "truncated": truncated})
    except Exception as e:
        LOG.exception("logtail failed: %s", e)
        return _json({"ok": False, "error": str(e)}, 500)



# ------------------------------- Reboot --------------------------------------

@bp.route("/api/system/reboot", methods=["POST"])
def api_system_reboot():
    if os.environ.get("OROMA_ALLOW_REBOOT", "0").strip() not in ("1", "true", "yes", "on"):
        return _json({"ok": False, "error": "Reboot nicht erlaubt (setze OROMA_ALLOW_REBOOT=1)"}, 403)
    cmd = [p for p in shlex.split(os.environ.get("OROMA_REBOOT_CMD", "/usr/bin/systemctl reboot")) if p]
    res = _run_cmd(cmd, timeout=10)
    res["cmd"] = " ".join(cmd)
    return _json(res, 200 if res.get("ok") else 500)
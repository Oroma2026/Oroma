#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/__init__.py
# Projekt: ORÓMA
# Version: v3.7 (final)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Zentrales UI-Paket-Init für ORÓMA (Flask-Dashboard & Web-UI).
#   Bietet:
#     • .env-Lader (ohne Fremdabhängigkeiten; mehrstufige Suche)
#     • Defaults & Verzeichnis-Setup (logs/models/db/uploads/exports/data)
#     • Security-Decorator (require_ui_token): Header/Query/Cookie/Bearer
#     • App-Fabrik (create_app) – Lazy-Import, vermeidet Zyklus
#
# Änderungen ggü. v3.0/3.6
# ────────────────────────
#   • DEFAULTS korrigiert: OROMA_VECTORDB_THRESHOLD (Tippfehler beseitigt)
#   • .env-Pfade: zuerst .env.local, dann .env, dann /etc/oroma/.env
#   • ensure_dirs(): legt zusätzlich data/ an (für DBs o. Ä.)
#   • create_app(): best-effort ensure_schema() + robustes Port-Parsing
#
# Sicherheit
# ──────────
#   • Für Produktivbetrieb OROMA_UI_TOKEN in .env setzen.
#   • Decorator liefert 401 bei Fehlschlag ohne sensitive Details.
#
# Struktur
# ────────
#   /ui/templates/  → Jinja2-HTML
#   /ui/static/     → CSS/JS/Assets
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

import os
import io
import time
from functools import wraps
from typing import Callable, Optional, Dict, Any, Iterable, List, TYPE_CHECKING
import logging
from core.log_guard import log_suppressed

if TYPE_CHECKING:
    # Nur für Typing, kein harter Import zur Laufzeit
    from flask import Flask

# -----------------------------------------------------------------------------
# Konstanten & Pfade
# -----------------------------------------------------------------------------

BASE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_FILE: str = os.path.join(BASE_DIR, ".env")         # Standard
ENV_LOCAL_FILE: str = os.path.join(BASE_DIR, ".env.local")
ENV_ETC_FILE: str = "/etc/oroma/.env"

DEFAULTS: Dict[str, Any] = {
    "FLASK_RUN_HOST": "127.0.0.1",
    "FLASK_RUN_PORT": "8080",
    "FLASK_ENV": "production",
    "OROMA_UI_TOKEN": "",  # leer => kein Token nötig
    "OROMA_EXPORT_DELAY_DAYS": "30",
    "OROMA_VECTORDB_THRESHOLD": "100000",  # ✔ korrigiert
    "OROMA_LOG_LEVEL": "INFO",
    "OROMA_LOG_DIR": os.path.join(BASE_DIR, "logs"),
    "OROMA_MODELS_DIR": os.path.join(BASE_DIR, "models"),
    "OROMA_LLM_DIR": os.path.join(BASE_DIR, "models", "llm"),
    "OROMA_UPLOAD_DIR": os.path.join(BASE_DIR, "uploads"),
    "OROMA_EXPORT_DIR": os.path.join(BASE_DIR, "exports"),
    # Optional: lokaler Datenpfad (DBs etc.)
    "OROMA_DATA_DIR": os.path.join(BASE_DIR, "data"),
    # Nightmode-Defaults
    "OROMA_NIGHTMODE_LIGHT_THRESHOLD": "25",
    "OROMA_NIGHTMODE_DELAY_MINUTES": "30",
}

# -----------------------------------------------------------------------------
# ENV laden (ohne Zusatzabhängigkeiten)
# -----------------------------------------------------------------------------

def _parse_env_file(env_path: str) -> Dict[str, str]:
    """Parst eine .env-Datei in ein dict (einfach, ohne Variablenersetzung)."""
    result: Dict[str, str] = {}
    if not os.path.isfile(env_path):
        return result
    try:
        with io.open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    result[k] = v
    except Exception:
        # .env ist "best effort" – kein harter Fail
        return {}
    return result


def _merge_env_if_missing(env_map: Dict[str, str]) -> None:
    """
    Übernimmt Werte aus env_map nur dann in os.environ,
    wenn der Key noch nicht gesetzt ist.
    """
    for k, v in env_map.items():
        if k not in os.environ or str(os.environ[k]).strip() == "":
            os.environ[k] = v


def load_env_from_files(paths: Iterable[str]) -> None:
    """Lädt mehrere .env-Dateien in definierter Reihenfolge (nur fehlende Keys)."""
    for p in paths:
        if p:
            _merge_env_if_missing(_parse_env_file(p))


def load_env() -> None:
    """Öffentliche API: lädt .env.local → .env → /etc/oroma/.env (ohne Override)."""
    load_env_from_files([ENV_LOCAL_FILE, ENV_FILE, ENV_ETC_FILE])


def apply_defaults() -> None:
    """Setzt Standardwerte für fehlende ENV-Keys."""
    for k, v in DEFAULTS.items():
        if k not in os.environ or str(os.environ[k]).strip() == "":
            os.environ[k] = str(v)

# -----------------------------------------------------------------------------
# Verzeichnis-Setup
# -----------------------------------------------------------------------------

def ensure_dirs() -> None:
    """Erzeugt benötigte Projektverzeichnisse (idempotent)."""
    os.makedirs(os.environ.get("OROMA_LOG_DIR", DEFAULTS["OROMA_LOG_DIR"]), exist_ok=True)
    # historisch genutzter lokaler DB-Ordner
    os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
    # neue, klarere Datenbasis
    os.makedirs(os.environ.get("OROMA_DATA_DIR", DEFAULTS["OROMA_DATA_DIR"]), exist_ok=True)
    os.makedirs(os.environ.get("OROMA_MODELS_DIR", DEFAULTS["OROMA_MODELS_DIR"]), exist_ok=True)
    os.makedirs(os.environ.get("OROMA_LLM_DIR", DEFAULTS["OROMA_LLM_DIR"]), exist_ok=True)
    os.makedirs(os.environ.get("OROMA_UPLOAD_DIR", DEFAULTS["OROMA_UPLOAD_DIR"]), exist_ok=True)
    os.makedirs(os.environ.get("OROMA_EXPORT_DIR", DEFAULTS["OROMA_EXPORT_DIR"]), exist_ok=True)

# -----------------------------------------------------------------------------
# Security: Optionaler Token-Schutz für Routen
# -----------------------------------------------------------------------------

def get_ui_token() -> str:
    return os.environ.get("OROMA_UI_TOKEN", "").strip()


def _extract_token_from_request(request) -> Optional[str]:
    """
    Akzeptiert:
      • Header: X-OROMA-TOKEN: <token>
      • Query : ?token=<token>
      • Cookie: oroma_token=<token>
      • Header: Authorization: Bearer <token>
    """
    # 1) Expliziter Header
    t = (request.headers.get("X-OROMA-TOKEN") or "").strip()
    if t:
        return t
    # 2) Bearer
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 3) Query
    t = (request.args.get("token") or "").strip()
    if t:
        return t
    # 4) Cookie
    t = (request.cookies.get("oroma_token") or "").strip()
    if t:
        return t
    return None


def require_ui_token(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token_expected = get_ui_token()
        if not token_expected:
            # Kein Token konfiguriert → Route offen (Dev/On-Prem)
            return fn(*args, **kwargs)
        from flask import request, abort
        supplied = _extract_token_from_request(request)
        if supplied and supplied == token_expected:
            return fn(*args, **kwargs)
        abort(401, description="Unauthorized: missing or invalid token")
    return wrapper

# -----------------------------------------------------------------------------
# Kleinere Utilities
# -----------------------------------------------------------------------------

def get_version() -> str:
    ver_path = os.path.join(BASE_DIR, "VERSION")
    try:
        if os.path.isfile(ver_path):
            with io.open(ver_path, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
    except Exception as e:
        log_suppressed('ui/__init__.py:212', exc=e, level=logging.WARNING)
        pass
    return "dev"


def get_runtime_info() -> Dict[str, Any]:
    return {
        "version": get_version(),
        "base_dir": BASE_DIR,
        "ts": int(time.time()),
        "env": {
            "FLASK_RUN_HOST": os.environ.get("FLASK_RUN_HOST"),
            "FLASK_RUN_PORT": os.environ.get("FLASK_RUN_PORT"),
            "FLASK_ENV": os.environ.get("FLASK_ENV"),
        },
    }

# -----------------------------------------------------------------------------
# App-Fabrik (Lazy-Import ui.flask_ui) – vermeidet Zyklen
# -----------------------------------------------------------------------------

def _parse_port(value: Any, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(fallback)


def create_app() -> "Flask":
    # 1) ENV laden (ohne bestehende Variablen zu überschreiben)
    load_env()
    # 2) Defaults anwenden
    apply_defaults()
    # 3) Verzeichnisse sicherstellen
    ensure_dirs()
    # 4) Schema best effort
    try:
        from core.sql_manager import ensure_schema
        ensure_schema()
    except Exception as e:
        log_suppressed('ui/__init__.py:252', exc=e, level=logging.WARNING)
        pass
    # 5) App importieren (Lazy)
    from ui import flask_ui  # noqa: E402
    return flask_ui.app  # type: ignore[return-value]

# -----------------------------------------------------------------------------
# Dev-Start (optional)
# -----------------------------------------------------------------------------

def _dev_main() -> None:
    app = create_app()
    host = os.environ.get("FLASK_RUN_HOST", DEFAULTS["FLASK_RUN_HOST"])
    port = _parse_port(os.environ.get("FLASK_RUN_PORT", DEFAULTS["FLASK_RUN_PORT"]), int(DEFAULTS["FLASK_RUN_PORT"]))
    app.run(host=host, port=port, debug=False, use_reloader=False)

__all__ = [
    "BASE_DIR",
    "ENV_FILE",
    "load_env_from_files",
    "load_env",
    "apply_defaults",
    "ensure_dirs",
    "get_ui_token",
    "require_ui_token",
    "get_version",
    "get_runtime_info",
    "create_app",
]
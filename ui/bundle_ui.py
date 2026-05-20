#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/bundle_ui.py
# Projekt: ORÓMA
# Modul:   Bundle-Import (SnapChains & Modelle) – Flask UI + API
# Version: v3.7 (final, produktiv)
# Stand:   2025-10-01
#
# Rolle / Zweck
# ─────────────
#  - ERSETZT das frühere ui/import_ui.py (Name kollidierte semantisch mit "import")
#  - Bietet eine Web-Oberfläche (GET /import) und API (POST /import/api/upload),
#    um ORÓMA-Bundles (z. B. SnapChain-Archive, Modellpakete) hochzuladen
#    und direkt über core/import_gate.py zu verarbeiten.
#
# Warum "bundle_ui"?
# ──────────────────
#  - Klarer als "import_ui" (kein Missverständnis mit Python-Schlüsselworten).
#  - Blueprint-Name ist jetzt eindeutig "bundle_ui".
#  - URL bleibt absichtlich bei /import, damit bestehende Links/Bookmarks (base.html)
#    weiterhin funktionieren.
#
# Sicherheit / Limits
# ───────────────────
#  - Optionaler UI-Token via ENV OROMA_UI_TOKEN (Header: X-OROMA-TOKEN oder ?token=...).
#  - Größenlimit via ENV OROMA_MAX_IMPORT_MB (Default 50 MB).
#  - Upload-Verzeichnis via ENV OROMA_UPLOAD_DIR (Default /opt/ai/oroma/uploads).
#  - Erlaubte Endungen: .zip, .tar, .tar.gz
#
# Abhängigkeiten
# ──────────────
#  - Flask, Werkzeug (secure_filename)
#  - core/import_gate.import_bundle(path) → führt die eigentliche Verarbeitung aus.
#
# Rückgabewerte (API)
# ───────────────────
#  • 200 OK: {"ok": true, "file": "...", "size_mb": 12.3, "result": {...}}
#  • 400/429: {"ok": false, "error": "..."}   (Validierungs-/Limitfehler)
#  • 500:     {"ok": false, "error": "..."}   (ImportGate-/Serverfehler)
#
# Changelog v3.7
# ──────────────
#  - Umbenennung in bundle_ui.py; Blueprint-Name "bundle_ui".
#  - Token-Check als Before-Request-Middleware.
#  - Robuste Fehlertexte; Logging auf Logger "oroma.bundle".
#  - Template-Name jetzt "bundle.html" (statt "import.html").
# =============================================================================

from __future__ import annotations

import os
import logging
from flask import Blueprint, render_template, request, jsonify, make_response
from werkzeug.utils import secure_filename

# --- Logger ------------------------------------------------------------------
logger = logging.getLogger("oroma.bundle")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[bundle] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# --- optional: ImportGate ----------------------------------------------------
try:
    from core import import_gate  # erwartet: import_bundle(path) -> dict
except Exception as e:
    logger.warning("ImportGate nicht verfügbar: %s", e)
    import_gate = None  # type: ignore

# --- Blueprint ---------------------------------------------------------------
# WICHTIG: URL bleibt /import (Kompatibilität zur Navbar in base.html),
#          der Blueprint-Name ist aber eindeutig "bundle_ui".
bp = Blueprint("bundle_ui", __name__, url_prefix="/import")

# --- ENV / Limits ------------------------------------------------------------
BASE = os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma")
UPLOAD_DIR = os.environ.get("OROMA_UPLOAD_DIR", os.path.join(BASE, "uploads"))
MAX_MB = float(os.environ.get("OROMA_MAX_IMPORT_MB", "50"))
ALLOWED_EXT = {".zip", ".tar", ".tar.gz"}

# --- optionaler UI-Token -----------------------------------------------------
def _check_auth() -> bool:
    token_cfg = (os.environ.get("OROMA_UI_TOKEN") or "").strip()
    if not token_cfg:
        return True
    tok = request.headers.get("X-OROMA-TOKEN") or request.args.get("token") or ""
    return tok == token_cfg

@bp.before_request
def _auth_middleware():
    if _check_auth():
        return
    return make_response("Unauthorized", 401)

# --- Helpers -----------------------------------------------------------------
def _ensure_dirs() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)

def _allowed(filename: str) -> bool:
    name = filename.lower()
    return any(name.endswith(ext) for ext in ALLOWED_EXT)

def _stream_size_mb(f) -> float:
    """Ermittelt die Größe eines werkzeug.datastructures.FileStorage in MB."""
    pos = f.stream.tell()
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(pos, os.SEEK_SET)
    return float(size) / (1024.0 * 1024.0)

# --- UI-Route ----------------------------------------------------------------
@bp.get("/")
def page():
    """
    Liefert die Upload-Seite. Erwartet Template: templates/bundle.html
    (Kompatibel zu deiner base.html Navigation: /import)
    """
    return render_template("bundle.html", max_mb=int(MAX_MB), exts=", ".join(sorted(ALLOWED_EXT)))

# --- API: Limits --------------------------------------------------------------
@bp.get("/api/limits")
def api_limits():
    return jsonify({
        "ok": True,
        "limits": {
            "max_mb": MAX_MB,
            "allowed_ext": sorted(ALLOWED_EXT),
            "upload_dir": UPLOAD_DIR,
        }
    })

# --- API: Upload --------------------------------------------------------------
@bp.post("/api/upload")
def api_upload():
    _ensure_dirs()

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei im Formular gefunden."}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Leerer Dateiname."}), 400

    fname = secure_filename(f.filename)
    if not _allowed(fname):
        return jsonify({"ok": False, "error": f"Ungültige Endung. Erlaubt: {', '.join(sorted(ALLOWED_EXT))}"}), 400

    try:
        size_mb = _stream_size_mb(f)
    except Exception:
        # Fallback, falls Stream tell/seek nicht möglich (selten)
        size_mb = 0.0

    if size_mb > MAX_MB:
        return jsonify({"ok": False, "error": f"Datei zu groß ({size_mb:.1f} MB > {MAX_MB:.0f} MB)."}), 400

    dest_path = os.path.join(UPLOAD_DIR, fname)
    try:
        f.save(dest_path)
    except Exception as e:
        logger.error("Speichern fehlgeschlagen: %s", e)
        return jsonify({"ok": False, "error": f"Speichern fehlgeschlagen: {e}"}), 500

    if not import_gate:
        return jsonify({"ok": False, "error": "ImportGate nicht verfügbar (core/import_gate.py fehlt)."}), 500

    logger.info("Bundle empfangen: %s (%.1f MB) → %s", fname, size_mb, dest_path)

    try:
        result = import_gate.import_bundle(dest_path)  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("ImportGate-Fehler")
        return jsonify({"ok": False, "error": f"Import fehlgeschlagen: {e}"}), 500

    return jsonify({
        "ok": bool(result.get("ok", False)),
        "file": fname,
        "size_mb": round(size_mb, 1),
        "result": result
    })
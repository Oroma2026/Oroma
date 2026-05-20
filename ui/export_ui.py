#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/export_ui.py
# Projekt:   ORÓMA (Offline-First · Headless · Export/Upload UI)
# Modul:     Export UI Blueprint – UI + API für Export-Dateien (List/Download) + Upload (Import staging) + Export trigger (core.export_gate)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul stellt einen Flask Blueprint bereit, der:
#   - eine HTML-Seite für Export/Import anbietet
#   - Export-Dateien im Export-Verzeichnis listet
#   - Export-Dateien per Download bereitstellt
#   - Upload von Bundles in ein Upload/Staging-Verzeichnis ermöglicht
#   - den eigentlichen Export-Prozess via core.export_gate.create_export() anstößt
#
# Wichtig:
# - Diese Datei ist „UI/Transport“-Schicht. Sie enthält bewusst keine DB-SQLs.
# - Export-Eligibility, Policy, Bundle-Format etc. sind in core.export_gate implementiert.
#
# BLUEPRINT / URLS (EXAKT IM CODE)
# ───────────────────────────────
# bp = Blueprint("export", __name__, url_prefix="/export")
#
# UI:
#   GET  /export/                         → templates/export.html
#
# API:
#   GET  /export/api/list                 → listet Dateien aus EXPORT_DIR
#   GET  /export/api/download/<fname>     → send_file(EXPORT_DIR/fname)
#   POST /export/api/upload               → speichert Upload nach IMPORT_DIR
#   POST /export/api/export               → export_gate.create_export() und gibt Dateinamen zurück
#
# AUTH / TOKEN
# ────────────
# Diese Datei macht keine Auth. Der globale /api/* Guard liegt in ui/flask_ui.py.
# Hier sind die Pfade unter /export/... (nicht /api/...), also typischerweise offen,
# sofern du nicht zusätzlich reverse-proxy Auth nutzt.
#
# ABHÄNGIGKEITEN
# ──────────────
# - Flask: Blueprint, jsonify, render_template, request, send_file
# - werkzeug.utils: secure_filename
# - stdlib: os, time
# - core.export_gate (optional)
#
# Wenn core.export_gate fehlt:
# - export_gate = None
# - /export/api/export liefert {"ok":False,"error":"export_gate fehlt"} (HTTP 500)
#
# VERZEICHNISSE / ENV (EXAKT IM CODE)
# ───────────────────────────────────
# BASE       = ENV["OROMA_BASE_DIR"]   oder "/opt/ai/oroma"
# EXPORT_DIR = ENV["OROMA_EXPORT_DIR"] oder f"{BASE}/exports"
# IMPORT_DIR = ENV["OROMA_UPLOAD_DIR"] oder f"{BASE}/uploads"
#
# _ensure_dirs():
# - legt EXPORT_DIR und IMPORT_DIR an (exist_ok=True)
#
# DATEI-LISTING (api_list)
# ───────────────────────
# api_list listet ausschließlich reguläre Dateien in EXPORT_DIR und liefert pro Datei:
#   {
#     "name": <filename>,
#     "size_kb": <float>,
#     "mtime": <time.ctime(...)>
#   }
# Response:
#   {"ok": True, "files": [ ... ]}
#
# DOWNLOAD (api_download/<fname>)
# ────────────────────────────────
# - fname wird mit secure_filename() bereinigt
# - Datei muss existieren, sonst 404 JSON
# - send_file(..., as_attachment=True)
#
# UPLOAD (api_upload)
# ───────────────────
# - erwartet multipart/form-data mit Feldname "file"
# - nutzt secure_filename() und speichert nach:
#     IMPORT_DIR/<fname>
# Response:
#   {"ok": True, "msg": "<fname> hochgeladen"}
#
# Hinweis:
# - Upload ist nur „staging“. Der eigentliche Import passiert über:
#     ui/import_manager.py (CLI) oder import_gate Tools/Jobs
#
# EXPORT START (api_export)
# ─────────────────────────
# - ruft export_gate.create_export() auf
# - wenn None/False zurückkommt → HTTP 400 "Keine exportierbaren Items"
# - sonst → {"ok": True, "file": "<basename>"}
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - url_prefix="/export" bleibt stabil (UI/Links/Clients).
# - secure_filename() muss bleiben (Path traversal verhindern).
# - _ensure_dirs() muss vor IO laufen (sonst Fehler bei frischen Deploys).
# - Export-Logik bleibt in core.export_gate (hier nur Trigger/Transport).
# - Upload ist staging-only (kein Auto-Import in diesem Blueprint ohne Policy).
#
# =============================================================================
# END HEADER
# =============================================================================

import os
import time
from flask import Blueprint, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

try:
    from core import export_gate
except ImportError:
    export_gate = None

bp = Blueprint("export", __name__, url_prefix="/export")

# ----------------------------------------------------------------------------
# ENV
# ----------------------------------------------------------------------------
BASE = os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma")
EXPORT_DIR = os.environ.get("OROMA_EXPORT_DIR", os.path.join(BASE, "exports"))
IMPORT_DIR = os.environ.get("OROMA_UPLOAD_DIR", os.path.join(BASE, "uploads"))

def _ensure_dirs():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(IMPORT_DIR, exist_ok=True)

# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
@bp.route("/")
def index():
    _ensure_dirs()
    return render_template("export.html")

# ----------------------------------------------------------------------------
# API – Liste
# ----------------------------------------------------------------------------
@bp.route("/api/list")
def api_list():
    try:
        _ensure_dirs()
        files = [
            {
                "name": f,
                "size_kb": round(os.path.getsize(os.path.join(EXPORT_DIR, f)) / 1024, 1),
                "mtime": time.ctime(os.path.getmtime(os.path.join(EXPORT_DIR, f))),
            }
            for f in os.listdir(EXPORT_DIR)
            if os.path.isfile(os.path.join(EXPORT_DIR, f))
        ]
        return jsonify({"ok": True, "files": files})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ----------------------------------------------------------------------------
# API – Download
# ----------------------------------------------------------------------------
@bp.route("/api/download/<fname>")
def api_download(fname):
    try:
        _ensure_dirs()
        path = os.path.join(EXPORT_DIR, secure_filename(fname))
        if not os.path.isfile(path):
            return jsonify({"ok": False, "error": "Datei nicht gefunden"}), 404
        return send_file(path, as_attachment=True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ----------------------------------------------------------------------------
# API – Upload (Import)
# ----------------------------------------------------------------------------
@bp.route("/api/upload", methods=["POST"])
def api_upload():
    _ensure_dirs()
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"ok": False, "error": "Leerer Dateiname"}), 400
    fname = secure_filename(f.filename)
    path = os.path.join(IMPORT_DIR, fname)
    f.save(path)
    return jsonify({"ok": True, "msg": f"{fname} hochgeladen"})

# ----------------------------------------------------------------------------
# API – Export starten
# ----------------------------------------------------------------------------
@bp.route("/api/export", methods=["POST"])
def api_export():
    try:
        _ensure_dirs()
        if not export_gate:
            return jsonify({"ok": False, "error": "export_gate fehlt"}), 500
        fpath = export_gate.create_export()
        if not fpath:
            return jsonify({"ok": False, "msg": "Keine exportierbaren Items"}), 400
        return jsonify({"ok": True, "file": os.path.basename(fpath)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
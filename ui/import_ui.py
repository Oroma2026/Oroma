#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/import_ui.py
# Projekt: ORÓMA
# Version: v3.8-r1 (Upload + Entpacken + Token-Guard)
# Stand:   2025-11-03
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   UI/API für den Import von Archiven (SnapChains/Modelle):
#     • GET  /import/                 → import.html (zeigt Max-Upload-Größe)
#     • POST /import/api/upload       → Datei hochladen (.zip/.tar/.tar.gz) und entpacken
#
# Merkmale
# ────────
#   • Optionaler Token-Guard über OROMA_UI_TOKEN bzw. ui.require_ui_token
#   • Größenlimit via OROMA_UPLOAD_MAX_MB (Default 256 MB)
#   • Upload-Verzeichnis OROMA_UPLOAD_DIR (Default /opt/ai/oroma/uploads)
#   • Ziel-Verzeichnis  OROMA_IMPORT_DEST (Default /opt/ai/oroma/uploads/imports)
#   • Optionaler Post-Hook: OROMA_IMPORT_POST_HOOK (Script wird mit dest_dir aufgerufen)
#
# ENV
# ───
#   OROMA_UPLOAD_DIR=/opt/ai/oroma/uploads
#   OROMA_UPLOAD_MAX_MB=256
#   OROMA_IMPORT_DEST=/opt/ai/oroma/uploads/imports
#   OROMA_IMPORT_POST_HOOK=/opt/ai/oroma/tools/import_post_hook.sh
#
# Integration
# ───────────
#   from ui import import_ui
#   app.register_blueprint(import_ui.bp)
#
# Sicherheit/FS
# ─────────────
#   • Läuft idealerweise als Service-User "oroma". Verzeichnisse müssen schreibbar sein.
#   • Systemd: ReadWritePaths muss UPLOAD_DIR und IMPORT_DEST enthalten.
# =============================================================================

from __future__ import annotations

import os
import io
import tarfile
import zipfile
import time
import json
import subprocess
from pathlib import Path
from typing import Tuple

from flask import Blueprint, render_template, request, jsonify, make_response
from werkzeug.utils import secure_filename
import logging
from core.log_guard import log_suppressed

# --------------------------------- Token-Guard --------------------------------

try:
    # Bevorzugt die zentrale Middleware, falls vorhanden
    from ui import require_ui_token as _require_ui_token  # type: ignore
except Exception:
    _require_ui_token = None  # Fallback unten

def _check_token_header() -> bool:
    tok_cfg = os.environ.get("OROMA_UI_TOKEN", "").strip()
    if not tok_cfg:
        return True
    tok = request.headers.get("X-OROMA-TOKEN") or request.args.get("token") or ""
    return tok == tok_cfg

def require_ui_token(fn):
    if _require_ui_token:
        return _require_ui_token(fn)
    # lokaler Fallback
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _check_token_header():
            return fn(*args, **kwargs)
        return make_response(("Unauthorized", 401))
    return wrapper

# --------------------------------- Config -------------------------------------

UPLOAD_DIR = Path(os.environ.get("OROMA_UPLOAD_DIR", "/opt/ai/oroma/uploads"))
IMPORT_DEST = Path(os.environ.get("OROMA_IMPORT_DEST", "/opt/ai/oroma/uploads/imports"))
MAX_MB = int(os.environ.get("OROMA_UPLOAD_MAX_MB", "256"))
POST_HOOK = os.environ.get("OROMA_IMPORT_POST_HOOK", "").strip() or None

ALLOWED_EXT = {".zip", ".tar", ".tgz", ".tar.gz"}

bp = Blueprint("import_ui", __name__, url_prefix="/import")

# --------------------------------- Utils --------------------------------------

def _ensure_dirs():
    for d in (UPLOAD_DIR, IMPORT_DEST):
        d.mkdir(parents=True, exist_ok=True)

def _ext_ok(name: str) -> bool:
    n = name.lower()
    return any(n.endswith(ext) for ext in ALLOWED_EXT)

def _save_streamed(file_storage, dst: Path, max_bytes: int) -> Tuple[int, str]:
    """
    Speichert Upload gestreamt nach dst. Bricht ab, wenn Limit überschritten.
    Rückgabe: (bytes_written, error_msg)
    """
    written = 0
    with open(dst, "wb") as f:
        while True:
            chunk = file_storage.stream.read(1024 * 1024)  # 1 MB
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                return written, "Upload überschreitet erlaubte Größe"
            f.write(chunk)
    return written, ""

def _extract_archive(src: Path, dest: Path) -> Tuple[int, list]:
    """
    Entpackt src nach dest. Liefert (file_count, file_list<=50).
    """
    count = 0
    listed = []

    dest.mkdir(parents=True, exist_ok=True)
    s = str(src).lower()

    def _maybe_list(p: Path):
        nonlocal count, listed
        count += 1
        if len(listed) < 50:
            try:
                rel = p.relative_to(dest)
            except Exception:
                rel = p.name
            listed.append(str(rel))

    if s.endswith(".zip"):
        with zipfile.ZipFile(src, "r") as zf:
            for m in zf.infolist():
                # Sicherheit: keine absoluten Pfade / Traversal
                name = Path(m.filename).name if m.is_dir() else m.filename
                name = name.replace("..", "")
                if not name:
                    continue
                zf.extract(m, path=dest)
                _maybe_list(dest / m.filename)
    else:
        # tar / tgz / tar.gz
        mode = "r:gz" if (s.endswith(".tgz") or s.endswith(".tar.gz")) else "r:"
        with tarfile.open(src, mode) as tf:
            for m in tf.getmembers():
                # Traversal verhindern
                if not m.name or ".." in m.name:
                    continue
                tf.extract(m, path=dest)
                _maybe_list(dest / m.name)

    return count, listed

def _run_post_hook(dest_dir: Path) -> Tuple[bool, str]:
    if not POST_HOOK:
        return True, ""
    try:
        res = subprocess.run(
            [POST_HOOK, str(dest_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True
        )
        ok = (res.returncode == 0)
        out = (res.stdout or "") + (("\n" + res.stderr) if res.stderr else "")
        return ok, out.strip()
    except Exception as e:
        return False, f"Post-Hook Fehler: {e}"

# --------------------------------- Routes -------------------------------------

@bp.route("/")
@require_ui_token
def page():
    _ensure_dirs()
    return render_template("import.html", max_mb=MAX_MB)

@bp.route("/api/upload", methods=["POST"])
@require_ui_token
def api_upload():
    _ensure_dirs()

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Kein Datei-Feld 'file' im Formular"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Keine Datei übergeben"}), 400

    if not _ext_ok(f.filename):
        return jsonify({"ok": False, "error": "Nicht unterstützte Endung (erlaubt: .zip, .tar, .tar.gz, .tgz)"}), 400

    safe_name = secure_filename(f.filename)
    ts = time.strftime("%Y%m%d-%H%M%S")
    upload_name = f"{ts}__{safe_name}"
    upload_path = UPLOAD_DIR / upload_name

    max_bytes = MAX_MB * 1024 * 1024
    written, err = _save_streamed(f, upload_path, max_bytes)
    if err:
        try:
            upload_path.unlink(missing_ok=True)
        except Exception as e:
            log_suppressed('ui/import_ui.py:213', exc=e, level=logging.WARNING)
            pass
        return jsonify({"ok": False, "error": err, "limit_mb": MAX_MB}), 400

    # Entpacken
    dest_dir = IMPORT_DEST / f"import-{ts}-{safe_name.rsplit('.', 1)[0]}"
    try:
        files_count, listed = _extract_archive(upload_path, dest_dir)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Entpacken fehlgeschlagen: {e}"}), 500

    # Optionaler Hook (z.B. SnapChain-Import, Model-Registry-Update, …)
    hook_ok, hook_out = _run_post_hook(dest_dir)

    return jsonify({
        "ok": True,
        "msg": "Upload & Import erfolgreich",
        "size_mb": round(written / (1024*1024), 2),
        "upload_path": str(upload_path),
        "dest_dir": str(dest_dir),
        "files_count": files_count,
        "files_sample": listed,
        "post_hook_ok": hook_ok,
        "post_hook_out": hook_out,
    })
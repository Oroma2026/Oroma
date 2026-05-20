#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts_ui.py – ORÓMA v3.0
-----------------------
UI + API für Text-to-Speech (TTS).
- /tts            → HTML-Seite mit Eingabefeld
- /tts/api/speak  → Gibt Text aus (OromaWrapper.tts_say)
"""

from flask import Blueprint, render_template, jsonify, request
from wrappers import tts_wrapper

bp = Blueprint("tts", __name__, url_prefix="/tts")

# ---------------- UI ----------------
@bp.route("/")
def page():
    return render_template("tts.html")

# ---------------- API ----------------
@bp.route("/api/speak", methods=["POST"])
def api_speak():
    try:
        data = request.get_json(force=True) if request.is_json else {}
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"ok": False, "error": "Kein Text angegeben"}), 400
        res = tts_wrapper.speak(text, blocking=True)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
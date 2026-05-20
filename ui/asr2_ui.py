#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/asr2_ui.py
# Projekt: ORÓMA – Headless Lern-KI (Edge)
# Version: v3.8-r2 (ASR2 mit Reflex-Hook, robust & produktiv)
# Stand:   2025-10-25
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
# Alternative ASR-UI auf Basis von OromaWrapper.asr_stream():
#   • GET  /asr2             → HTML-Seite (Parameter & Ergebnisfeld)
#   • POST /asr2/api/run     → Kurze Aufnahme + Transkription (One-Shot)
#   • GET  /asr2/api/status  → Leichter Status-Check (ohne Audioaufnahme)
#
# LERN-HOOK
# ─────────
# Das Transkript wird – falls vorhanden – unmittelbar an den ASR-Reflex
# (core.asr_reflex.process_text) übergeben. Dadurch entstehen Empathie-Snaps/
# Intents und ggf. leichte Rewards → indirekte Integration in den Lernkreislauf.
#
# EINGABEN (POST /asr2/api/run, JSON)
# ───────────────────────────────────
#   {
#     "language": "de" | "en" | ...   (optional; Default aus ENV oder "de")
#     "model":    "tiny|base|small|medium|..."  (optional; Default "small")
#     "duration": float Sekunden (0.5 .. 30.0; Default 5.0)
#   }
#
# ENV-DEFAULTS (werden benutzt, wenn Felder fehlen)
# ─────────────────────────────────────────────────
#   OROMA_WHISPER_LANG   (Default: "de")
#   OROMA_WHISPER_MODEL  (Default: "small")
#
# SICHERHEIT & ROBUSTHEIT
# ────────────────────────
#   • Keine GUI/Qt/Wayland-Abhängigkeiten (Headless).
#   • Robustes JSON-Parsing, sensible Dauer-Klammerung (0.5..30s).
#   • Reflex-Hook in try/except; Fehler werden sauber geloggt.
#   • Response-Text wird via core.privacy.redact() redigiert (PII-Safe).
#
# INTEGRATION
# ───────────
#   In die Flask-App einbinden:
#       from ui.asr2_ui import bp as asr2_bp
#       app.register_blueprint(asr2_bp)
#
# TEST
# ────
#   curl -sS -X POST http://127.0.0.1:8080/asr2/api/run \
#        -H 'Content-Type: application/json' \
#        -d '{"language":"de","model":"small","duration":3.0}' | jq
# =============================================================================

from __future__ import annotations

import os
from flask import Blueprint, render_template, jsonify, request, current_app
from wrappers.oroma_wrapper import OromaWrapper

# optionaler Lern-Hook (Empathie/Intents/Rewards)
try:
    from core import asr_reflex  # type: ignore
except Exception:
    asr_reflex = None  # Reflex nicht verfügbar → kein Lern-Side-Effect

# optionale Privacy-Redaction (PII-Safe Response)
try:
    from core.privacy import redact  # type: ignore
except Exception:
    def redact(s: str) -> str:  # Fallback: No-Op
        return s

bp = Blueprint("asr2_ui", __name__, url_prefix="/asr2")

# Singleton-Wrapper (leichtgewichtig; hält interne Audio-State/Handles)
_oroma = OromaWrapper()


# ---------------- UI ----------------
@bp.route("/")
def page():
    """
    HTML-Seite mit Parametern & Ergebnisfeld.
    Erwartet 'templates/asr2.html'. Falls das Template fehlt,
    wird ein 500 erzeugt – bewusst, um Deploy-Fehler sichtbar zu machen.
    """
    return render_template("asr2.html")


# ---------------- API ----------------
@bp.route("/api/run", methods=["POST"])
def api_run():
    """
    Startet eine kurze Aufnahme und gibt das Transkript zurück.
    Zusätzlich (falls verfügbar) wird das Transkript an den ASR-Reflex
    übergeben (indirektes Lernen via Empathie/Intents/Rewards).
    Der zurückgegebene Text wird zur Sicherheit redigiert (PII-Safe).
    """
    try:
        data = request.get_json(silent=True) or {}
        lang = str(data.get("language") or os.getenv("OROMA_WHISPER_LANG", "de")).strip()
        model = str(data.get("model") or os.getenv("OROMA_WHISPER_MODEL", "small")).strip()

        # Dauer (Sekunden) hart klammern, um Ausreißer zu vermeiden
        try:
            dur = float(data.get("duration", 5.0))
        except Exception:
            dur = 5.0
        dur = max(0.5, min(30.0, dur))
        # One-shot ASR
        # gain_db: UI kann als gain_db (dB) oder gain (legacy) senden
        gain_db = data.get("gain_db")
        if gain_db is None:
            gain_db = data.get("gain")
        result = _oroma.asr_stream(language=lang, model_name=model, duration=dur, gain_db=gain_db)

        # Lern-Hook: ASR-Reflex (Empathie/Intents/Rewards) + optionaler AV-Label-Link
        txt = ""
        try:
            if isinstance(result, dict):
                # viele Wrapper liefern {"text": "..."} oder {"result": {"text": "..." } }
                if "text" in result:
                    txt = (result.get("text") or "").strip()
                else:
                    inner = result.get("result") or {}
                    txt = str(inner.get("text") or "").strip()

            if txt:
                # 1) Reflex (falls verfügbar)
                if asr_reflex:
                    try:
                        asr_reflex.process_text(txt)
                    except Exception as _e:
                        current_app.logger.warning("ASR2: Reflex-Hook fehlgeschlagen: %s", _e)

                # 2) Crossmodal Teacher-Link (Audio↔Vision) – robust, best effort
                try:
                    from core import av_label_linker  # lazy import
                    av_label_linker.link_text_now(txt)
                except Exception as _e:
                    current_app.logger.warning("ASR2: AV-Label-Link fehlgeschlagen: %s", _e)

                # 3) Unimodaler Teacher-Link (Audio-only) – default ON via ENV
                try:
                    alink = str(os.environ.get("OROMA_ASR_ALINK", "1")).strip().lower() in ("1","true","yes","y","on")
                    if alink:
                        from core import audio_label_linker  # lazy import
                        audio_label_linker.link_text_now(txt)
                except Exception as _e:
                    current_app.logger.warning("ASR2: A-Label-Link fehlgeschlagen: %s", _e)
        except Exception as _e:
            current_app.logger.warning("ASR2: Text-Extraktion/Hook fehlgeschlagen: %s", _e)

        # Privacy: Falls Text im Ergebnis, sicherheitshalber redigieren
        if isinstance(result, dict):
            try:
                if "text" in result and isinstance(result["text"], str):
                    result["text"] = redact(result["text"])
                if "result" in result and isinstance(result["result"], dict):
                    t = result["result"].get("text")
                    if isinstance(t, str):
                        result["result"]["text"] = redact(t)
            except Exception as _e:
                current_app.logger.warning("ASR2: Redaction fehlgeschlagen: %s", _e)

        return jsonify({
            "ok": True,
            "result": result,
            "meta": {"language": lang, "model": model, "duration": dur}
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/status", methods=["GET"])
def api_status():
    """
    Leichter Status-Check ohne Audioaufnahme.

    Ziel:
      • UI-Badge kann "aktiv" anzeigen
      • Debug-Infos (Backend + Pfade + ENV) sichtbar machen, ohne Mikro zu öffnen

    Rückgabe:
      { ok, active, backend, whispercpp:{ok,bin,model,exists_bin,exists_model}, audio:{input_index,input_name,gain_db} }
    """
    try:
        import os
        from pathlib import Path

        active = _oroma.is_ready() if hasattr(_oroma, "is_ready") else True

        backend = (os.environ.get("OROMA_ASR_BACKEND", "") or "").strip() or "whisper_py"
        gain_db = (os.environ.get("OROMA_AUDIO_GAIN", "0.0") or "0.0").strip()
        in_idx = (os.environ.get("OROMA_AUDIO_INPUT_INDEX", "") or "").strip()
        in_name = (os.environ.get("OROMA_AUDIO_INPUT_NAME", "") or "").strip()

        # whisper.cpp Pfade (nur Check, kein Auto-Build hier)
        bin_path = (os.environ.get("OROMA_WHISPERCPP_BIN", "") or "").strip() or "/opt/ai/oroma/third_party/whisper.cpp/build/bin/whisper-cli"
        model_path = (os.environ.get("OROMA_WHISPERCPP_MODEL", "") or "").strip() or "/opt/ai/oroma/third_party/whisper.cpp/models/ggml-base.bin"

        exists_bin = Path(bin_path).exists()
        exists_model = Path(model_path).exists()

        return jsonify({
            "ok": True,
            "active": bool(active),
            "backend": backend,
            "whispercpp": {
                "ok": bool(exists_bin and exists_model),
                "bin": bin_path,
                "model": model_path,
                "exists_bin": bool(exists_bin),
                "exists_model": bool(exists_model),
            },
            "audio": {
                "input_index": in_idx,
                "input_name": in_name,
                "gain_db": gain_db,
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------- Standalone-Test -----------------------------------

if __name__ == "__main__":
    # Nur für lokalen Schnelltest; im Betrieb über oroma.service starten
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.run(port=5002, debug=True)

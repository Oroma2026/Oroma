#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/audio_ui.py
# Projekt:   ORÓMA (Flask UI · Headless Audio)
# Modul:     Audio Blueprint – Browser-UI + API für DeviceHub Audio (Mic Start/Stop, Pegel, Devices, WAV Record)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist ein Flask-Blueprint (`bp`) für ORÓMAs Audio-Seite.
# Es liefert:
#   - eine HTML-UI (templates/audio.html)
#   - eine kleine, robuste API für Mic-Status, Pegel, Geräteübersicht und
#     eine kurze WAV-Testaufnahme (für Debug im Browser)
#
# Wichtig: Dieses Modul ist **headless** (Web-UI) und nutzt ausschließlich den
# zentralen DeviceHub:
#   from core.device_hub import get_hub
#
# Token-Schutz:
#   - erfolgt NICHT hier, sondern zentral in ui/flask_ui.py
#   - im Token-Modus sind /api/* Endpoints tokenpflichtig
#
# ROUTING / URLS (ROBUST GEGEN SLASH/PROXY)
# ─────────────────────────────────────────
# url_prefix = "/audio"
#
# Das Modul stellt absichtlich zwei Page-Routen bereit:
#   GET /audio     (strict_slashes=False)  → render_template("audio.html")
#   GET /audio/                         → render_template("audio.html")
#
# Hintergrund:
# - In realen Proxy/Redirect-Setups kann ein erzwungener Slash-Redirect stören.
# - Beide Varianten sollen sicher funktionieren.
#
# API-ENDPOINTS (AKTUELLER STAND)
# ───────────────────────────────
# GET  /audio/api/status
#   → liefert DeviceHub Audio-Status + Debug-Felder:
#       {
#         ok, enabled,
#         mic_active, mic,                # mic ist Legacy-Alias für alte UI-Stände
#         level, sr, ch,
#         last_error, last_error_ts,
#         in_name, out_name,              # gewünschte Konfiguration (DeviceHub)
#         in_dev_index/out_dev_index,     # tatsächlich gewählte Geräte (status())
#         in_dev_name/out_dev_name,
#         mic_open_sr                     # low-level Debug (DeviceHub intern)
#       }
#
# GET  /audio/api/level
#   → {ok, level}  (schneller Poll für Pegelanzeige)
#
# GET  /audio/api/devices
#   → {ok, input:[...], output:[...]}
#   - erwartet von DeviceHub.list_audio_devices() ein dict:
#       {"input":[...], "output":[...]}
#
# POST /audio/api/mic
#   JSON: {"action":"start"} oder {"action":"stop"}
#   → startet/stoppt das Mikrofon im DeviceHub:
#       hub.start_mic(client="audio_ui")
#       hub.stop_mic(client="audio_ui")
#   - stop() wird als „ok=True“ behandelt, wenn keine Exception kommt
#   - client="audio_ui" ist wichtig für Multi-Client Debug/Arbitration im Hub
#
# GET  /audio/api/wav?sec=3&gain_db=<str>
#   → liefert eine WAV-Datei (audio/wav) direkt aus dem DeviceHub:
#       hub.record_wav(sec, client="audio_ui", gain_db=gain_raw)
#   - sec ist geklemmt: 0.2 .. 30.0 Sekunden
#   - wenn kein Audio geliefert wird → HTTP 503 {ok:false, error:"no_audio"}
#   - WAV wird per BytesIO + send_file zurückgegeben (headless browser-friendly)
#
# ABHÄNGIGKEITEN / OWNER-SHIPS
# ────────────────────────────
# Dieses Modul besitzt keine eigene Audio-Engine.
# Owner ist DeviceHub:
#   - status() / get_audio_level() / start_mic() / stop_mic()
#   - list_audio_devices()
#   - record_wav()
#
# Die UI-Schicht darf keine langfristigen Audio-Ressourcen halten:
# - Kein „globales mic open“ in der UI
# - UI triggert nur Aktionen und pollt Status
#
# PATCH-HINTERGRUND (KOMPATIBILITÄT IM LIVE-BETRIEB)
# ──────────────────────────────────────────────────
# Dieses Modul enthält bewusst Kompatibilitätsdetails:
#   - BytesIO Import/Benutzung korrekt (WAV Endpoint)
#   - list_audio_devices() liefert dict {input, output} → wird exakt so behandelt
#   - stop_mic() kann None liefern → Stop wird als ok=True interpretiert
#   - /audio ohne Slash existiert, um Redirect-Kantenfälle abzufangen
#   - api/status liefert extra „gewählte“ Device-Felder (in_dev_*/out_dev_*)
#     für Headless Debug ohne Logsuche
#
# ÖFFENTLICHE API (FLASK-VERTRAG)
# ───────────────────────────────
# bp: Blueprint
#   - wird in run_oroma.py „safe“ registriert
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Blueprint-Prefix und Endpoints stabil halten (sonst bricht Frontend JS/Links).
# - status() muss mic_active UND Legacy-Alias mic liefern (UI-Kompatibilität).
# - WAV Endpoint muss bytes-only liefern (keine temporären Dateien, headless).
# - UI darf nicht blockieren: nur kurze DeviceHub Calls, keine langen Loops.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

from flask import Blueprint, render_template, jsonify, request, send_file
from io import BytesIO

from core.device_hub import get_hub  # type: ignore

bp = Blueprint(
    "audio_ui",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/audio",
)


@bp.route("", strict_slashes=False)
def page_noslash():
    """Audio-UI auch ohne Trailing-Slash rendern.

    Hintergrund
    ───────────
    - Manche Clients/Bookmarks rufen /audio (ohne Slash) auf.
    - Flask redirectet zwar normalerweise auf /audio/ (308), aber je nach
      Proxy/Client kann dieser Redirect nicht sauber folgen → 404/Fehlersuche.
    - Mit dieser Route sind /audio und /audio/ beide stabil verfügbar.
    """
    return render_template("audio.html")


@bp.route("/")
def page():
    return render_template("audio.html")


@bp.get("/api/status")
def api_status():
    """Audio-Status für UI: Mic-State, Pegel, Device-Infos + letzte Fehler."""
    hub = get_hub()

    devices = hub.list_audio_devices()
    try:
        st = hub.status() if hasattr(hub, "status") else {}
    except Exception:
        st = {}

    audio_st = (st.get("audio") if isinstance(st, dict) else {}) or {}

    in_list = devices.get("input", []) if isinstance(devices, dict) else []
    out_list = devices.get("output", []) if isinstance(devices, dict) else []

    return jsonify({
        "ok": True,

        # -----------------------------------------------------------------
        # ORÓMA Audio UI Compatibility
        # -----------------------------------------------------------------
        # audio.html (ältere Stände) erwarteten teils das Feld "mic" statt
        # "mic_active". Wir liefern deshalb beide, um die UI stabil zu halten.
        # Zusätzlich liefern wir die *tatsächlich* gewählte Device-Auswahl aus
        # DeviceHub.status() (in_dev_*/out_dev_*), damit Headless-Debugging
        # ohne Log-Suche möglich ist.
        # -----------------------------------------------------------------
        "enabled": bool(audio_st.get("enabled", False)),
        "mic_active": bool(audio_st.get("mic_active", False)),
        "mic": bool(audio_st.get("mic_active", False)),
        "level": float(audio_st.get("level", 0.0) or 0.0),
        "sr": int(audio_st.get("sr", 0) or 0),
        "ch": int(audio_st.get("ch", 0) or 0),

        # Konfiguration (ENV-Targets) + tatsächlich gewählte Geräte
        "in_name": str(audio_st.get("in_name", "") or ""),
        "out_name": str(audio_st.get("out_name", "") or ""),
        "in_dev_index": audio_st.get("in_dev_index", None),
        "out_dev_index": audio_st.get("out_dev_index", None),
        "in_dev_name": audio_st.get("in_dev_name", None),
        "out_dev_name": audio_st.get("out_dev_name", None),

        # Low-Level (DeviceHub intern)
        "mic_open_sr": int(getattr(hub, "_mic_open_sr", 0) or 0),
        "last_error": str(getattr(hub, "_mic_last_error", "") or ""),
        "last_error_ts": float(getattr(hub, "_mic_last_error_ts", 0.0) or 0.0),

        # Device-Übersicht (sounddevice query)
        "devices": {
            "input": in_list,
            "output": out_list,
            "n_input": len(in_list),
            "n_output": len(out_list),
        }
    })


@bp.get("/api/level")
def api_level():
    hub = get_hub()
    return jsonify({"ok": True, "level": float(hub.get_audio_level() or 0.0)})


@bp.get("/api/devices")
def api_devices():
    hub = get_hub()
    devices = hub.list_audio_devices()
    in_list = devices.get("input", []) if isinstance(devices, dict) else []
    out_list = devices.get("output", []) if isinstance(devices, dict) else []
    return jsonify({"ok": True, "input": in_list, "output": out_list})


@bp.post("/api/mic")
def api_mic():
    hub = get_hub()
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip().lower()

    if action == "start":
        ok = bool(hub.start_mic(client="audio_ui"))
        return jsonify({"ok": ok, "action": "start"})
    if action == "stop":
        try:
            hub.stop_mic(client="audio_ui")
            ok = True
        except Exception:
            ok = False
        return jsonify({"ok": ok, "action": "stop"})

    return jsonify({"ok": False, "error": "invalid_action", "action": action}), 400


@bp.get("/api/wav")
def api_wav():
    hub = get_hub()
    sec_raw = request.args.get("sec", "3")
    gain_raw = request.args.get("gain_db", "")
    try:
        sec = float(sec_raw)
    except Exception:
        sec = 3.0
    sec = max(0.2, min(30.0, sec))

    wav_bytes = hub.record_wav(sec, client="audio_ui", gain_db=gain_raw)
    if not wav_bytes:
        return jsonify({"ok": False, "error": "no_audio"}), 503

    return send_file(
        BytesIO(wav_bytes),
        mimetype="audio/wav",
        as_attachment=False,
        download_name="oroma_record.wav",
    )
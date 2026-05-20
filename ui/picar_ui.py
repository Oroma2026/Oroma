#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
picar_ui.py – ORÓMA v3.0
-------------------------
UI + API fürs PiCar:
- /picar/                 → HTML-Steuerseite
- /picar/api/status       → Status (Mode, Speed, Alive)
- /picar/api/cmd          → Fahrbefehl (forward/backward/left/right/stop)
- /picar/api/mode         → Mode setzen (manual | oroma)
- /picar/api/speed        → Default-Speed setzen (0..100)

Abhängigkeiten:
- wrappers.picar_wrapper.PiCar (dein Wrapper)
- (optional) core.agent_loop – nur für spätere enge Integration; hier nicht nötig
"""

from __future__ import annotations
import threading
import time
from typing import Optional

from flask import Blueprint, render_template, jsonify, request
import logging
from core.log_guard import log_suppressed

try:
    from wrappers.picar_wrapper import PiCar
except Exception as e:
    PiCar = None  # type: ignore
    print(f"[picar_ui] WARN: PiCar-Wrapper fehlt oder fehlerhaft: {e}")

bp = Blueprint("picar", __name__, url_prefix="/picar")

# ---------------------------------------------------------------------
# Singleton-Runtime
# ---------------------------------------------------------------------
class _PiCarRuntime:
    def __init__(self):
        self.car: Optional[PiCar] = PiCar() if PiCar else None
        self.default_speed = 40
        self.mode = "manual"  # "manual" | "oroma"
        self._auto_thread: Optional[threading.Thread] = None
        self._auto_stop = threading.Event()

    # ---------- Autopilot (einfaches Platzhalter-Verhalten) ----------
    def _auto_loop(self):
        """
        Sehr einfache Heuristik (Platzhalter):
        - fahre vorwärts 1.5s
        - kurze Linksdrehung 0.35s
        - stoppe kurz 0.1s
        - wiederhole
        Deadman im Wrapper sorgt zusätzlich für Sicherheit.
        """
        car = self.car
        if not car:
            return
        while not self._auto_stop.is_set() and self.mode == "oroma":
            try:
                car.forward(self.default_speed)
                time.sleep(1.5)
                car.left(min(self.default_speed + 10, 100))
                time.sleep(0.35)
                car.stop()
                time.sleep(0.1)
            except Exception:
                # Bei GPIO-Fehlern: Autopilot beenden
                break
        try:
            car.stop()
        except Exception as e:
            log_suppressed('ui/picar_ui.py:70', exc=e, level=logging.WARNING)
            pass

    def start_autopilot(self):
        if self.mode != "oroma" or not self.car:
            return
        if self._auto_thread and self._auto_thread.is_alive():
            return
        self._auto_stop.clear()
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()

    def stop_autopilot(self):
        self._auto_stop.set()
        t = self._auto_thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._auto_thread = None
        if self.car:
            try:
                self.car.stop()
            except Exception as e:
                log_suppressed('ui/picar_ui.py:92', exc=e, level=logging.WARNING)
                pass

# Globale Runtime
_RT = _PiCarRuntime()

# ---------------------------------------------------------------------
# Routes – UI
# ---------------------------------------------------------------------
@bp.route("/")
def page():
    return render_template("picar.html")

# ---------------------------------------------------------------------
# Routes – API
# ---------------------------------------------------------------------
@bp.route("/api/status")
def api_status():
    alive = _RT.car is not None
    return jsonify({
        "ok": True,
        "alive": alive,
        "mode": _RT.mode,
        "speed": _RT.default_speed
    })

@bp.route("/api/speed", methods=["POST"])
def api_speed():
    try:
        data = request.get_json(force=True)
        spd = int(data.get("speed", _RT.default_speed))
        spd = max(0, min(spd, 100))
        _RT.default_speed = spd
        return jsonify({"ok": True, "speed": spd})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@bp.route("/api/mode", methods=["POST"])
def api_mode():
    try:
        data = request.get_json(force=True)
        mode = str(data.get("mode", "manual")).lower()
        if mode not in ("manual", "oroma"):
            return jsonify({"ok": False, "error": "ungültiger mode"}), 400
        # Wechsel-Logik
        if mode == "manual":
            _RT.mode = "manual"
            _RT.stop_autopilot()
        else:
            _RT.mode = "oroma"
            _RT.start_autopilot()
        return jsonify({"ok": True, "mode": _RT.mode})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@bp.route("/api/cmd", methods=["POST"])
def api_cmd():
    if not _RT.car:
        return jsonify({"ok": False, "error": "PiCar nicht verfügbar"}), 503
    try:
        data = request.get_json(force=True)
        cmd = str(data.get("cmd", "")).lower()
        speed = int(data.get("speed", _RT.default_speed))
        speed = max(0, min(speed, 100))

        # Nur im Manual-Modus direkte Befehle zulassen
        if _RT.mode != "manual":
            return jsonify({"ok": False, "error": "Im ORÓMA-Modus keine Direktbefehle"}), 409

        if cmd == "forward":
            _RT.car.forward(speed)
        elif cmd == "backward":
            _RT.car.backward(speed)
        elif cmd == "left":
            _RT.car.left(speed)
        elif cmd == "right":
            _RT.car.right(speed)
        elif cmd == "stop":
            _RT.car.stop()
        else:
            return jsonify({"ok": False, "error": "unknown cmd"}), 400

        # Speed intern merken (UX)
        _RT.default_speed = speed
        return jsonify({"ok": True, "cmd": cmd, "speed": speed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
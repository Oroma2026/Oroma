#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/wrappers/ptz_controller.py
# Projekt:   ORÓMA (Headless Sensor Hub · Offline-First · Edge)
# Modul:     PTZController – v4l2-ctl basierter PTZ-Adapter (UVC/V4L2) für USB-PTZ Kameras
# Version:   v3.7.3
# Stand:     2026-01-27
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert einen robusten, headless PTZ-Controller auf Basis
# von Linux V4L2 Controls (UVC). Es ist dafür gedacht, USB-PTZ-Kameras wie
# "EMEET PIXY" zu steuern, sofern das Kernel-Device entsprechende Controls
# exportiert (typisch: pan_absolute, tilt_absolute, zoom_absolute).
#
# Designziele:
#   • Headless-only: keine GUI/Qt/X11 Abhängigkeiten
#   • Keine zusätzlichen Python Dependencies: ausschließlich stdlib
#   • Fail-Closed: wenn v4l2-ctl fehlt oder Controls nicht vorhanden sind,
#     ist der Controller "unsupported" und führt keine Moves aus
#   • Safety first:
#       - Cooldown / Rate-Limit (OROMA_PTZ_COOLDOWN_MS)
#       - Bounds / Clamps aus den gemeldeten Control-Ranges
#       - Step-Quantisierung (value wird auf step gerundet)
#       - Defensive Fehlerbehandlung (kein Crash der Hauptprozesse)
#
# WICHTIGE HINWEISE (LINUX/V4L2)
# ─────────────────────────────
# V4L2 Controls werden von Geräten unterschiedlich implementiert.
# Dieses Modul nutzt "v4l2-ctl" (Paket: v4l-utils), weil es:
#   - auf RasPi/PiOS typischerweise verfügbar ist,
#   - die Abfrage/Setzung von Controls zuverlässig kapselt,
#   - vendor-spezifische Besonderheiten weniger schmerzhaft macht.
#
# Für EMEET PIXY (Beispiel):
#   v4l2-ctl -d /dev/video8 --list-ctrls
# zeigt u.a.
#   pan_absolute  : min=-540000 max=540000 step=3600 value=0
#   tilt_absolute : min=-324000 max=324000 step=3600 value=0
#   zoom_absolute : min=100 max=150 step=1 value=100
#
# ENV-VARIABLEN
# ─────────────
#   OROMA_PTZ_COOLDOWN_MS   (Default: 250)
#       Mindestabstand zwischen zwei PTZ-Commands.
#
#   OROMA_PTZ_LOG_LEVEL     (Default: "info")
#       "debug" | "info" | "warn". Wird nur für interne Log-Details genutzt.
#
#   OROMA_PTZ_ALLOW_FOCUS   (Default: 0)
#       Falls 1, wird focus_absolute (wenn vorhanden) als optionales Control
#       mit aufgenommen. In ORÓMA standardmäßig deaktiviert, da Fokus-AF oft
#       gleichzeitig aktiv ist und Konflikte entstehen können.
#
#   OROMA_PTZ_PAN_MIN / OROMA_PTZ_PAN_MAX
#   OROMA_PTZ_TILT_MIN / OROMA_PTZ_TILT_MAX
#   OROMA_PTZ_ZOOM_MIN / OROMA_PTZ_ZOOM_MAX
#       Optionale, zusätzliche Soft-Limits pro Achse. Diese Limits werden
#       *zusätzlich* zu den vom Kernel gemeldeten Ranges angewandt.
#
#       Einheit: exakt die V4L2 Control-Einheit (Integer), z.B.
#         pan_absolute  : -540000..540000 (step=3600)
#         tilt_absolute : -324000..324000 (step=3600)
#         zoom_absolute : 100..150 (step=1)
#
#       Beispiel (sanfter Action-Space, ca. Mitte ± 90° und Zoom bis 130):
#         OROMA_PTZ_PAN_MIN=-324000
#         OROMA_PTZ_PAN_MAX= 324000
#         OROMA_PTZ_TILT_MIN=-162000
#         OROMA_PTZ_TILT_MAX= 162000
#         OROMA_PTZ_ZOOM_MIN=100
#         OROMA_PTZ_ZOOM_MAX=130
#
#       Hinweis: Falls Soft-Limits inkonsistent sind (min > max) oder außerhalb
#       der Device-Ranges liegen, werden sie defensiv ignoriert.
#
# API (für DeviceHub/UI)
# ─────────────────────
#   PTZController(device="/dev/video8")
#     - status()        -> dict (supported, ranges, values, last_error, etc.)
#     - center()        -> setzt pan/tilt auf default (oder 0, falls unknown)
#     - nudge(dir, n)   -> relative Bewegung in Steps (dir: left/right/up/down)
#     - zoom(delta)     -> relative zoom Schritte
#     - set_absolute(pan=..., tilt=..., zoom=...)
#
# SICHERHEIT / PRODUKTIONSREGEL
# ─────────────────────────────
# Dieses Modul darf NIEMALS blockierend lange laufen. Alle v4l2-ctl Aufrufe
# haben kurze Timeouts, und alle Exceptions werden abgefangen.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import re
import time
import json
import shutil
import tempfile
import threading
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any


_CTRL_RE = re.compile(
    r"^\s*(?P<name>[a-zA-Z0-9_]+)\s+0x[0-9a-fA-F]+\s+\([^)]*\)\s*:\s*"
    r"min=(?P<min>-?\d+)\s+max=(?P<max>-?\d+)\s+step=(?P<step>-?\d+)\s+"
    r"default=(?P<default>-?\d+)\s+value=(?P<value>-?\d+)(?:\s+flags=.*)?\s*$"
)


@dataclass
class _CtrlSpec:
    name: str
    # Effective limits (device range clamped by optional soft-limits)
    min: int
    max: int
    step: int
    default: int
    value: int
    # Raw device limits (as reported by the driver)
    raw_min: int
    raw_max: int


class PTZController:
    """Headless V4L2 PTZ Controller (via v4l2-ctl).

    Dieses Objekt ist threadsicher (interner Lock) und best-effort.
    """

    def __init__(self, device: str) -> None:
        self.device_raw = device
        self.device = self._resolve_device(device)
        self._lock = threading.Lock()
        self._supported = False
        self._ctrls: Dict[str, _CtrlSpec] = {}
        self._last_error: str = ""
        self._last_error_ts: float = 0.0
        self._last_cmd_ts: float = 0.0
        self._next_refresh_ts: float = 0.0
        self._last_refresh_ok_ts: float = 0.0

        try:
            self._cooldown_ms = int(os.environ.get("OROMA_PTZ_COOLDOWN_MS", "250"))
        except Exception:
            self._cooldown_ms = 250

        self._allow_focus = os.environ.get("OROMA_PTZ_ALLOW_FOCUS", "0").strip().lower() in ("1", "true", "yes", "on")
        self._log_level = os.environ.get("OROMA_PTZ_LOG_LEVEL", "info").strip().lower()

        # Optional additional soft-limits (env). Applied on top of device ranges.
        self._soft_limits = {
            "pan_absolute": (self._env_int("OROMA_PTZ_PAN_MIN"), self._env_int("OROMA_PTZ_PAN_MAX")),
            "tilt_absolute": (self._env_int("OROMA_PTZ_TILT_MIN"), self._env_int("OROMA_PTZ_TILT_MAX")),
            "zoom_absolute": (self._env_int("OROMA_PTZ_ZOOM_MIN"), self._env_int("OROMA_PTZ_ZOOM_MAX")),
        }

        self._v4l2ctl = shutil.which("v4l2-ctl")
        if not self._v4l2ctl:
            self._set_error("v4l2-ctl not found (install v4l-utils)")
            return

        # Prime/Detect controls at init (best effort; if it fails we remain unsupported)
        self._refresh_ctrls()

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def supported(self) -> bool:
        return bool(self._supported)

    def status(self) -> Dict[str, Any]:
        """Return a stable status dict for UI/API usage."""
        with self._lock:
            # refresh values opportunistically (non-fatal) – throttled
            now = time.time()
            if now >= float(self._next_refresh_ts or 0.0):
                self._refresh_ctrls(values_only=True)
            
            return {
                "ok": True,
                "supported": bool(self._supported),
                "device": self.device, "device_raw": self.device_raw,
                "cooldown_ms": int(self._cooldown_ms),
                "last_cmd_ts": float(self._last_cmd_ts),
                "last_error": self._last_error,
                "last_error_ts": float(self._last_error_ts),
                "reason": ("busy_or_permission" if ("Cannot open device" in (self._last_error or "")) else ""),
                "controls": {
                    k: {
                        "min": v.min,
                        "max": v.max,
                        "step": v.step,
                        "default": v.default,
                        "value": v.value,
                        "raw_min": v.raw_min,
                        "raw_max": v.raw_max,
                    }
                    for k, v in self._ctrls.items()
                },
            }

    def center(self) -> bool:
        """Center PTZ by setting pan/tilt to default (or 0 if unknown)."""
        with self._lock:
            if not self._supported:
                return False
            pan_def = self._ctrls.get("pan_absolute").default if "pan_absolute" in self._ctrls else 0
            tilt_def = self._ctrls.get("tilt_absolute").default if "tilt_absolute" in self._ctrls else 0
            ok1 = self._set_ctrl_abs("pan_absolute", pan_def)
            ok2 = self._set_ctrl_abs("tilt_absolute", tilt_def)
            return bool(ok1 and ok2)

    def nudge(self, direction: str, steps: int = 1) -> bool:
        """Nudge pan/tilt by a number of control steps."""
        direction = (direction or "").strip().lower()
        try:
            steps = int(steps)
        except Exception:
            steps = 1
        steps = max(-1000, min(1000, steps))

        with self._lock:
            if not self._supported:
                return False

            if direction in ("left", "right"):
                spec = self._ctrls.get("pan_absolute")
                if not spec:
                    self._set_error("pan_absolute not available")
                    return False
                delta = spec.step * (abs(steps) if direction == "right" else -abs(steps))
                return self._set_ctrl_abs("pan_absolute", spec.value + delta)

            if direction in ("up", "down"):
                spec = self._ctrls.get("tilt_absolute")
                if not spec:
                    self._set_error("tilt_absolute not available")
                    return False
                # V4L2 tilt sign is device-dependent; we keep conventional: up = +
                delta = spec.step * (abs(steps) if direction == "up" else -abs(steps))
                return self._set_ctrl_abs("tilt_absolute", spec.value + delta)

            self._set_error(f"invalid direction: {direction}")
            return False

    def zoom(self, delta: int) -> bool:
        """Relative zoom steps. Positive = zoom in, negative = zoom out."""
        try:
            delta = int(delta)
        except Exception:
            delta = 0
        delta = max(-1000, min(1000, delta))

        with self._lock:
            if not self._supported:
                return False
            spec = self._ctrls.get("zoom_absolute")
            if not spec:
                self._set_error("zoom_absolute not available")
                return False
            return self._set_ctrl_abs("zoom_absolute", spec.value + (spec.step * delta))

    def set_absolute(self, pan: Optional[int] = None, tilt: Optional[int] = None, zoom: Optional[int] = None,
                     focus: Optional[int] = None) -> bool:
        """Set absolute pan/tilt/zoom/focus (best-effort)."""
        with self._lock:
            if not self._supported:
                return False
            ok = True
            if pan is not None:
                ok = ok and self._set_ctrl_abs("pan_absolute", int(pan))
            if tilt is not None:
                ok = ok and self._set_ctrl_abs("tilt_absolute", int(tilt))
            if zoom is not None:
                ok = ok and self._set_ctrl_abs("zoom_absolute", int(zoom))
            if focus is not None:
                if not self._allow_focus:
                    self._set_error("focus control disabled by OROMA_PTZ_ALLOW_FOCUS=0")
                    return False
                ok = ok and self._set_ctrl_abs("focus_absolute", int(focus))
            return bool(ok)

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    def _set_error(self, msg: str) -> None:
        self._last_error = (msg or "")[:500]
        self._last_error_ts = time.time()

    def _env_int(self, key: str) -> Optional[int]:
        """Parse an int env var. Returns None if unset/empty/invalid.

        Accepts: "", "none", "null" -> None.
        """
        try:
            raw = os.environ.get(key)
            if raw is None:
                return None
            s = str(raw).strip()
            if not s or s.lower() in ("none", "null", "off"):
                return None
            return int(s)
        except Exception:
            return None
    def _resolve_device(self, dev: str) -> str:
        """Resolve stable /dev/v4l/* symlinks to their real /dev/videoX node (best effort).

        Hintergrund:
          Einige Tools/Stacks (und teils auch Container/Permissions) reagieren
          empfindlich auf Symlink-Pfade. Außerdem sind Fehlermeldungen wie
          „Cannot open device /dev/v4l/by-id/...“ schwerer zu interpretieren.

        Regel:
          - Wenn dev ein /dev/* Pfad ist und realpath auf /dev/videoX zeigt,
            nutzen wir den realen Pfad für v4l2-ctl Aufrufe.
          - Bei Fehlern bleiben wir beim Original.

        WICHTIG:
          Diese Funktion ist bewusst "best effort" und darf NIEMALS werfen.
        """
        try:
            s = str(dev or "").strip()
            if not s:
                return dev
            if s.startswith("/dev/"):
                rp = os.path.realpath(s)
                if rp and rp.startswith("/dev/video") and os.path.exists(rp):
                    return rp
            return s
        except Exception:
            return dev

    # ---------------------------------------------------------------------
    # Ctrl-Spec Cache (minimal-invasiv)
    # ---------------------------------------------------------------------
    # Hintergrund
    # ----------
    # Bei einigen USB/UVC-PTZ-Kameras kommt es gelegentlich vor, dass
    # `v4l2-ctl --list-ctrls` mit Exit-Code 0, aber **leerem Output**
    # zurückkommt (z.B. wenn das Device gerade sehr stark genutzt wird
    # oder der Treiber kurz nach Reconnect "halb" initialisiert ist).
    #
    # Wenn das beim *ersten* Zugriff passiert, markiert ein streng
    # implementierter Controller die Kamera als "nicht unterstützt" und
    # bleibt dann in diesem Zustand (DeviceHub cached die Instanz).
    #
    # Lösung
    # ------
    # 1) Kurz retries bei leerem Output (nur im Full-Refresh, nicht in
    #    values_only Polling),
    # 2) Persistenter Cache der zuletzt bekannten Control-Specs in
    #    /opt/ai/oroma/data/state (oder OROMA_STATE_DIR),
    # 3) Fallback: wenn list-ctrls leer/fehlschlägt, aber Cache existiert,
    #    wird dieser geladen und PTZ bleibt funktionsfähig.

    def _state_dir(self) -> str:
        return os.environ.get("OROMA_STATE_DIR", "/opt/ai/oroma/data/state")

    def _ctrl_cache_path(self) -> str:
        # Prefer a stable identifier: the original by-id/by-path name is
        # more stable than /dev/videoX after reboots.
        base = os.path.basename(self.device_raw or self.device or "ptz")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
        return os.path.join(self._state_dir(), f"ptz_ctrl_cache_{safe}.json")

    def _load_ctrl_cache(self) -> bool:
        """Load cached ctrl specs into self._ctrls.

        Returns True if a usable cache was loaded.
        Never raises.
        """
        try:
            path = self._ctrl_cache_path()
            if not os.path.exists(path):
                return False
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ctrls = data.get("ctrls") if isinstance(data, dict) else None
            if not isinstance(ctrls, dict) or not ctrls:
                return False

            loaded: Dict[str, CtrlSpec] = {}
            for name, spec in ctrls.items():
                if not isinstance(spec, dict):
                    continue
                try:
                    loaded[name] = CtrlSpec(
                        name=str(name),
                        type=str(spec.get("type", "")),
                        flags=str(spec.get("flags", "")),
                        min=int(spec.get("min", 0)),
                        max=int(spec.get("max", 0)),
                        step=int(spec.get("step", 1)),
                        default=int(spec.get("default", 0)),
                        value=int(spec.get("value", spec.get("default", 0))),
                        raw_min=int(spec.get("raw_min", spec.get("min", 0))),
                        raw_max=int(spec.get("raw_max", spec.get("max", 0))),
                    )
                except Exception:
                    continue

            if not loaded:
                return False

            self._ctrls = loaded
            self._supported = True
            return True
        except Exception:
            return False

    def _save_ctrl_cache(self) -> None:
        """Persist current ctrl specs to disk (best effort, atomic)."""
        try:
            if not self._ctrls:
                return
            state_dir = self._state_dir()
            try:
                os.makedirs(state_dir, exist_ok=True)
            except Exception:
                # If we can't create the state dir, do not fail PTZ.
                return

            payload = {
                "ts": int(time.time()),
                "device_raw": self.device_raw,
                "device_resolved": self.device,
                "ctrls": {k: {
                    "type": v.type,
                    "flags": v.flags,
                    "min": int(v.min),
                    "max": int(v.max),
                    "step": int(v.step),
                    "default": int(v.default),
                    "value": int(v.value),
                    "raw_min": int(getattr(v, "raw_min", v.min)),
                    "raw_max": int(getattr(v, "raw_max", v.max)),
                } for k, v in self._ctrls.items()}
            }

            path = self._ctrl_cache_path()
            fd, tmp = tempfile.mkstemp(prefix=".ptz_ctrl_cache_", suffix=".json", dir=state_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except Exception:
                    pass
        except Exception:
            # Silent by design: cache must never break PTZ.
            return

    def _apply_soft_limits(self, name: str, dev_min: int, dev_max: int) -> Tuple[int, int]:
        """Return (eff_min, eff_max) after applying optional env soft-limits."""
        env_min, env_max = self._soft_limits.get(name, (None, None))

        eff_min = dev_min
        eff_max = dev_max

        if env_min is not None:
            eff_min = max(eff_min, int(env_min))
        if env_max is not None:
            eff_max = min(eff_max, int(env_max))

        # Defensive: ignore invalid ranges.
        if eff_min > eff_max:
            return dev_min, dev_max
        return eff_min, eff_max

    def _cooldown_ok(self) -> bool:
        if self._cooldown_ms <= 0:
            return True
        now = time.time()
        return (now - self._last_cmd_ts) * 1000.0 >= float(self._cooldown_ms)

    def _run(self, args: list[str], timeout_sec: float = 2.5) -> Tuple[int, str, str]:
        """Run v4l2-ctl command. Never raises."""
        try:
            p = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            return int(p.returncode), (p.stdout or ""), (p.stderr or "")
        except Exception as e:
            return 99, "", repr(e)

    def _maybe_reresolve_device_on_open_error(self, err_s: str) -> bool:
        """Best-effort: re-resolve device path if open() fails.

        Hintergrund (Real-World, insbesondere nach Reboots/USB-Re-Enumerations):
          - /dev/videoX Nummern können wechseln
          - /dev/v4l/by-id/* bleibt stabil, aber wenn wir zu früh (bei init)
            auf /dev/videoX auflösen und das Device später neu enumeriert,
            kann der gecachte Pfad veraltet sein.
          - Dann schlagen v4l2-ctl Aufrufe mit "Cannot open device /dev/videoX"
            fehl, obwohl das by-id Device eigentlich verfügbar wäre.

        Policy:
          - Nur bei klaren Open-Errors (Cannot open device / Device or resource busy).
          - Einmalig re-resolve von device_raw -> realpath und device updaten.
          - Gibt True zurück, wenn sich der Pfad tatsächlich geändert hat.

        Diese Funktion darf NIE Exceptions werfen (headless-safe).
        """
        try:
            es = (err_s or "").strip()
            if not es:
                return False
            if ("Cannot open device" not in es) and ("cannot open device" not in es) and ("Device or resource busy" not in es):
                return False
            new_dev = self._resolve_device(self.device_raw)
            if new_dev and new_dev != self.device:
                self.device = new_dev
                return True
            return False
        except Exception:
            return False

    def _refresh_ctrls(self, values_only: bool = False) -> None:
        """Parse controls; if values_only, keep ranges and only refresh current values."""
        if not self._v4l2ctl:
            self._supported = False
            return

        # Always use --list-ctrls because it includes current value.
        rc, out, err = self._run([self._v4l2ctl, "-d", self.device, "--list-ctrls"], timeout_sec=3.0)

        # WICHTIG: In manchen Treiber-/Busy-Situationen kann v4l2-ctl mit rc=0
        # zurückkehren, aber dennoch keine parsebaren Controls liefern (stdout leer)
        # und nur Hinweise in stderr schreiben. Wir behandeln "leer" als Fehler.
        out_s = (out or "").strip()
        err_s = (err or "").strip()

        if rc != 0 or not out_s:
            # Minimal-invasive Recovery:
            #   Wenn das Device nicht geöffnet werden kann, versuchen wir einmalig
            #   den Pfad neu zu resolven (device_raw → realpath). Das hilft gegen
            #   /dev/videoX Drift nach Reboots/USB-Reconnects.
            if self._maybe_reresolve_device_on_open_error(err_s):
                rc, out, err = self._run([self._v4l2ctl, "-d", self.device, "--list-ctrls"], timeout_sec=3.0)
                out_s = (out or "").strip()
                err_s = (err or "").strip()

            # Viele UVC-Geräte liefern sporadisch *leeren* stdout (rc==0),
            # wenn ein anderer Prozess den Stream offen hält. Falls das beim
            # *Initial* kommt (self._ctrls leer), würden wir sonst dauerhaft
            # "unsupported" werden. Daher: kurze Retries + Ctrl-Cache.
            if (not values_only) and (not self._ctrls):
                try:
                    retries = int(os.environ.get("OROMA_PTZ_LIST_CTRLS_RETRY", "3"))
                except Exception:
                    retries = 3
                if retries > 0 and (rc == 0) and (not out_s):
                    for i in range(retries):
                        time.sleep(0.15 * (i + 1))
                        rc2, out2, err2 = self._run([self._v4l2ctl, "-d", self.device, "--list-ctrls"], timeout_sec=3.0)
                        out2s = (out2 or "").strip()
                        if rc2 == 0 and out2s:
                            rc, out, err = rc2, out2, err2
                            out_s = out2s
                            err_s = (err2 or "").strip()
                            break

                # Wenn weiterhin nichts parsebar ist, versuchen wir den Cache.
                if (rc != 0) or (not out_s):
                    if self._load_ctrl_cache():
                        # Backoff gegen Hot-Loop.
                        try:
                            self._next_refresh_ts = time.time() + float(os.environ.get('OROMA_PTZ_REFRESH_BACKOFF_SEC','5'))
                        except Exception:
                            self._next_refresh_ts = time.time() + 5.0
                        if rc != 0:
                            self._set_error(
                                f"v4l2-ctl list-ctrls failed: rc={rc} err={err_s} · using cached ctrl specs"
                            )
                        else:
                            self._set_error(
                                f"v4l2-ctl list-ctrls empty output: err={err_s} · using cached ctrl specs"
                            )
                        return

            # Bei values_only: bestehende Ranges/Support nicht kaputt machen –
            # wir refreshen dann einfach nicht.
            if values_only and self._ctrls:
                if rc != 0:
                    self._set_error(f"v4l2-ctl list-ctrls failed: rc={rc} err={err_s}")
                else:
                    self._set_error(f"v4l2-ctl list-ctrls empty output: err={err_s}")
                # Backoff: wenn Device nicht geöffnet werden kann (busy/permission),
                # vermeiden wir Hot-Loop Status-Refreshes (UI pollt häufig).
                try:
                    if 'Cannot open device' in err_s or 'cannot open device' in err_s:
                        self._next_refresh_ts = time.time() + float(os.environ.get('OROMA_PTZ_REFRESH_BACKOFF_SEC','5'))
                    else:
                        self._next_refresh_ts = time.time() + 1.0
                except Exception:
                    self._next_refresh_ts = time.time() + 5.0
                return

            self._supported = False
            try:
                if 'Cannot open device' in err_s or 'cannot open device' in err_s:
                    self._next_refresh_ts = time.time() + float(os.environ.get('OROMA_PTZ_REFRESH_BACKOFF_SEC','5'))
                else:
                    self._next_refresh_ts = time.time() + 1.0
            except Exception:
                self._next_refresh_ts = time.time() + 5.0
            if rc != 0:
                self._set_error(f"v4l2-ctl list-ctrls failed: rc={rc} err={err_s}")
            else:
                self._set_error(f"v4l2-ctl list-ctrls empty output: err={err_s}")
            return

        parsed: Dict[str, _CtrlSpec] = {}
        for line in (out or "").splitlines():
            m = _CTRL_RE.match(line)
            if not m:
                continue
            name = m.group("name")
            try:
                dev_min = int(m.group("min"))
                dev_max = int(m.group("max"))
                eff_min, eff_max = self._apply_soft_limits(name, dev_min, dev_max)
                spec = _CtrlSpec(
                    name=name,
                    min=int(eff_min),
                    max=int(eff_max),
                    step=int(m.group("step")),
                    default=int(m.group("default")),
                    value=int(m.group("value")),
                    raw_min=int(dev_min),
                    raw_max=int(dev_max),
                )
            except Exception:
                continue
            parsed[name] = spec

        # Required PTZ controls
        has_pan = "pan_absolute" in parsed
        has_tilt = "tilt_absolute" in parsed
        has_zoom = "zoom_absolute" in parsed

        if values_only and self._ctrls:
            # Only refresh current values if we already have ranges.
            for k in ("pan_absolute", "tilt_absolute", "zoom_absolute"):
                if k in parsed and k in self._ctrls:
                    self._ctrls[k].value = parsed[k].value
            if self._allow_focus and "focus_absolute" in parsed and "focus_absolute" in self._ctrls:
                self._ctrls["focus_absolute"].value = parsed["focus_absolute"].value
            return

        self._ctrls = {}
        for k in ("pan_absolute", "tilt_absolute", "zoom_absolute"):
            if k in parsed:
                self._ctrls[k] = parsed[k]
        if self._allow_focus and "focus_absolute" in parsed:
            self._ctrls["focus_absolute"] = parsed["focus_absolute"]

        self._supported = bool(has_pan or has_tilt or has_zoom)
        if not self._supported:
            self._set_error("No PTZ controls found (pan/tilt/zoom)")
        else:
            # Persist the ctrl specs so we can keep PTZ functional if --list-ctrls
            # returns empty output on later runs.
            if not values_only:
                self._save_ctrl_cache()

    def _quantize(self, spec: _CtrlSpec, value: int) -> int:
        if spec.step and spec.step > 0:
            # Round to nearest step around min.
            offset = value - spec.raw_min
            q = int(round(offset / float(spec.step)))
            value = spec.raw_min + (q * spec.step)
        return value

    def _clamp(self, spec: _CtrlSpec, value: int) -> int:
        if value < spec.min:
            return spec.min
        if value > spec.max:
            return spec.max
        return value

    def _set_ctrl_abs(self, name: str, value: int) -> bool:
        spec = self._ctrls.get(name)
        if not spec:
            self._set_error(f"control not available: {name}")
            return False

        if not self._cooldown_ok():
            # Rate-limited; do not log as hard error.
            return False

        value = int(value)
        value = self._clamp(spec, value)
        value = self._quantize(spec, value)

        rc, out, err = self._run([self._v4l2ctl, "-d", self.device, "--set-ctrl", f"{name}={value}"])
        if rc != 0:
            err_s = (err or "").strip()
            # Minimal-invasive Recovery:
            #   Wenn open() fehlschlägt, versuchen wir einmalig device_raw neu zu
            #   resolven (symlink → realpath) und wiederholen den set-ctrl.
            if self._maybe_reresolve_device_on_open_error(err_s):
                rc2, out2, err2 = self._run([self._v4l2ctl, "-d", self.device, "--set-ctrl", f"{name}={value}"])
                if rc2 == 0:
                    spec.value = value
                    self._last_cmd_ts = time.time()
                    return True
                # Fail with second error (more relevant after re-resolve)
                err_s = (err2 or "").strip()
                self._set_error(f"set-ctrl failed: {name}={value} rc={rc2} err={err_s}")
                return False

            self._set_error(f"set-ctrl failed: {name}={value} rc={rc} err={err_s}")
            return False

        spec.value = value
        self._last_cmd_ts = time.time()
        return True

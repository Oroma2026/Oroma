#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/wrappers/oroma_wrapper.py
# Projekt:   ORÓMA
# Modul:     Meta-Wrapper (Unified API + Backend Auto-Selection + Light Reactivity)
# Version:   v3.8-r2
# Stand:     2025-12-01
#
# Zweck / Rolle
# ─────────────
#  - Zentraler Meta-Wrapper, der mehrere Backend-Wrapper (Hailo, DeGirum, CPU)
#    einheitlich kapselt und automatisch auswählt – OHNE die Kamera exklusiv zu öffnen.
#  - Nutzt (wenn vorhanden) den CameraHub als gemeinsame Frame-/Luma-Quelle.
#  - Einheitliche API für Vision/Audio/TTS:
#       • detect(frame=None)  → Objekterkennung
#       • embed(frame=None)   → Embedding-Erzeugung
#       • asr_stream(...)     → Audio Speech-to-Text (Streaming)
#       • tts_say(text)       → Text-to-Speech
#  - Optionale Licht-Reaktivität (Circadian/Dream-Steuerung) mit Hysterese:
#       • enable_light_reactivity(on_change=..., interval_s=..., dark_thr=..., bright_thr=..., hold=...)
#       • disable_light_reactivity()
#       • get_light_level() → aktuelles {state, luma}
#
# Backend-Priorität (überschreibbar via ENV):
#   OROMA_BACKEND_PREF = "auto" | "hailo" | "degirum" | "cpu"    (Default: auto)
#   OROMA_FAILOVER     = true|false                              (Default: true)
#
# Licht-/CameraHub-ENV (optional):
#   OROMA_LIGHT_INTERVAL_SEC   (Default: 300)   – Abtastintervall
#   OROMA_LIGHT_DARK_THR       (Default: 30)    – Dunkel-Schwelle (0-255 Luma)
#   OROMA_LIGHT_BRIGHT_THR     (Default: 40)    – Hell-Schwelle  (0-255 Luma)
#   OROMA_LIGHT_HOLD           (Default: 2)     – benötigte konsekutive Messungen für Umschalten
#
# Dummy-Logging-Steuerung (NEU in v3.8-r2)
# ─────────────────────────────────────────
#   OROMA_WRAPPER_DUMMY_VERBOSE = 0 | 1
#
#   - Wenn 1:
#       • Jede Nutzung von Dummy-Embeddings wird als WARNING geloggt, sowohl:
#           – wenn kein Frame verfügbar ist
#           – wenn kein Vision-Backend verfügbar ist
#   - Wenn 0 oder nicht gesetzt:
#       • Die erste Dummy-Nutzung wird als WARNING geloggt
#       • Alle weiteren Dummy-Nutzungen werden nur auf DEBUG geloggt
#
#   → Ziel: service.err.log wird nicht mehr dauerhaft mit identischen
#     "Vision embed() → Dummy-Fallback" Messages geflutet, insbesondere
#     wenn kein Vision-Backend aktiv ist und hooks_av_snaptoken regelmäßig
#     embed() aufruft.
#
# Rückgaben (Vision):
#   {"ok": bool, "dummy": bool, ...} – Dummy-Fallbacks bleiben erhalten.
#
# Sicherheit / Stabilität
#  - Keine exklusiven Kamera-Handles; CameraHub bleibt „Single Source of Truth“.
#  - Thread-sichere Light-Worker-Schleife (abschaltbar).
#  - Fehler/Failover mit Logging; ohne CameraHub läuft Vision nur mit explizitem frame.
#
# Hinweis
#  - Beste Ergebnisse, wenn run_oroma.py den CameraHub/DeviceHub startet. Ohne CameraHub
#    werden detect/embed nur dann arbeiten, wenn du explizit ein frame übergibst – so
#    vermeiden wir Doppelzugriffe auf die Kamera.
# =============================================================================

from __future__ import annotations

import os
import time
import hashlib
import logging
import threading
from typing import Any, Dict, Optional, Callable
from core.log_guard import log_suppressed
import logging

# Optional für JPEG->Frame Fallback im gemeinsamen Hub-Pfad.
# Wichtig für headless/external-frame Setups: Die Video-UI kann oft noch
# über latest_jpeg() arbeiten, obwohl get_latest_frame() leer bleibt.
# Damit hooks_av_snaptoken -> OromaWrapper.embed(frame=None) in denselben
# Situationen nicht blind auf Dummy fällt, dekodieren wir best effort auch
# JPEG-Frames zurück nach BGR.
try:
    import cv2  # type: ignore
except Exception:
    cv2 = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore

# Optional: DBWriter (Stufe C, Multi-DB). Wrapper darf stats/oroma Writes nicht
# am Single-Writer vorbei durchführen.
try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

logger = logging.getLogger("oroma.wrapper")
if not logger.handlers:
    h = logging.StreamHandler()
    f = logging.Formatter("[%(asctime)s][%(levelname)s] oroma.wrapper: %(message)s")
    h.setFormatter(f)
    logger.addHandler(h)
logger.setLevel(logging.INFO)


def _dbw_enabled() -> bool:
    return (db_writer_client is not None) and (os.environ.get("OROMA_DBW_ENABLE", "").strip().lower() in ("1", "true", "yes", "on"))


_STATS_SCHEMA_DONE = False


def _ensure_stats_points_schema_best_effort() -> None:
    """Best-effort Schema für stats_points (stats.db) via DBWriter.

    Wir tun das nur einmal pro Prozess, um den Hot-Path klein zu halten.
    """
    global _STATS_SCHEMA_DONE
    if _STATS_SCHEMA_DONE:
        return
    _STATS_SCHEMA_DONE = True

    if not _dbw_enabled():
        return
    try:
        stmts = [
            (
                """
                CREATE TABLE IF NOT EXISTS stats_points (
                  ts        INTEGER NOT NULL,
                  series    TEXT    NOT NULL,
                  value     REAL    NOT NULL,
                  src_table TEXT    NOT NULL,
                  src_id    INTEGER NOT NULL DEFAULT 0,
                  src_uid   TEXT    NOT NULL,
                  meta      TEXT    NULL
                )
                """,
                [],
            ),
            ("CREATE INDEX IF NOT EXISTS idx_stats_points_series_ts ON stats_points(series, ts)", []),
            ("CREATE UNIQUE INDEX IF NOT EXISTS ux_stats_points_src ON stats_points(src_table, src_uid, series)", []),
        ]
        db_writer_client.transaction(
            stmts,
            tag="oroma_wrapper.stats_points.ensure",
            priority="low",
            timeout_ms=60000,
            db="stats",
        )
    except Exception:
        # Best effort: schema darf Wrapper nicht stoppen
        pass


def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")


# Dummy-Logging-Steuerung (NEU in v3.8-r2)
# ---------------------------------------
#   OROMA_WRAPPER_DUMMY_VERBOSE=1 → jede Dummy-Nutzung als WARNING
#   Default (0/unset): erste Dummy-Nutzung als WARNING, danach DEBUG
_DUMMY_VERBOSE: bool = _bool_env("OROMA_WRAPPER_DUMMY_VERBOSE", False)
_DUMMY_WARNED_NO_FRAME: bool = False
_DUMMY_WARNED_NO_BACKEND: bool = False


BACKEND_PREF = os.environ.get("OROMA_BACKEND_PREF", "auto").lower()  # auto|hailo|degirum|cpu
FAILOVER = _bool_env("OROMA_FAILOVER", True)


# Lazy imports der Backends
def _try_import(modname: str):
    try:
        return __import__(modname, fromlist=["*"])
    except Exception as e:
        logger.debug("Backend %s konnte nicht importiert werden: %s", modname, e)
        return None


_hailo = None
_degirum = None
_vis = None
_asr = None
_tts = None


def _load_backends() -> None:
    global _hailo, _degirum, _vis, _asr, _tts
    if _hailo is None:
        _hailo = _try_import("wrappers.hailo_wrapper")
    if _degirum is None:
        _degirum = _try_import("wrappers.degirum_wrapper")
    if _vis is None:
        _vis = _try_import("wrappers.vision_wrapper")
    if _asr is None:
        _asr = _try_import("wrappers.audio_wrapper")
    if _tts is None:
        _tts = _try_import("wrappers.tts_wrapper")


# Optionaler CameraHub (Kompatibilitätsschicht, kann intern DeviceHub nutzen)
try:
    from core import camera_hub  # type: ignore
except Exception:
    camera_hub = None  # type: ignore


class OromaWrapper:
    """Meta-Wrapper mit Backend-Auswahl, CameraHub-Integration und optionaler Light-Reaktivität."""

    # ---------------- Singleton / global access ----------------
    _INSTANCE: Optional['OromaWrapper'] = None
    _INSTANCE_LOCK = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'OromaWrapper':
        """
        Liefert eine globale Singleton-Instanz des Meta-Wrappers.

        Hintergrund:
        - Einige AgentLoop-Hooks (z.B. av_snaptoken_hook) erwarten eine stabile
          globale Wrapper-Instanz, um pro Tick keine Backends neu zu initialisieren.
        - Diese Methode stellt Abwärtskompatibilität her, falls Hooks/Module
          (noch) einen Singleton nutzen.

        Thread-Safety:
        - double-checked locking; die erste Instanz wird einmalig erzeugt.
        """
        inst = cls._INSTANCE
        if inst is not None:
            return inst
        with cls._INSTANCE_LOCK:
            inst = cls._INSTANCE
            if inst is None:
                inst = cls()
                cls._INSTANCE = inst
            return inst


    # ---------------- Konstruktion / Backend-Wahl ----------------
    def __init__(self) -> None:
        _load_backends()
        self.backend = self._choose_backend()

        # Inferenz-Latenz Telemetrie (Edge-Performance-Story)
        # - Wir loggen die End-to-End Zeit (ms) pro embed()-Call in die metrics-Tabelle.
        # - Rate-Limit per Sekundenfenster, damit DB & UI nicht überflutet werden.
        # - Keys: vision:infer_ms:cpu | vision:infer_ms:hailo | vision:infer_ms:degirum
        self._infer_metric_every_sec = int(os.environ.get("OROMA_VISION_INFER_METRIC_EVERY_SEC", "5"))
        self._last_infer_metric_ts = 0
        self._infer_bootstrap_done = False  # einmaliger Bootstrap-Write für infer_ms Keys
        logger.info("Meta-Wrapper aktiv: Backend=%s (Failover=%s)", self.backend, FAILOVER)

        # Light-Worker State
        self._light_thread: Optional[threading.Thread] = None
        self._light_stop = threading.Event()
        self._light_lock = threading.Lock()
        self._light_state = "BRIGHT"  # oder "DARK"
        self._light_luma: Optional[float] = None
        self._on_change: Optional[Callable[[str, float], None]] = None

        # Diagnose: CameraHub-Verfügbarkeit
        if camera_hub:
            logger.info("OromaWrapper: camera_hub verfügbar – Vision nutzt gemeinsamen Frame-Pfad.")
        else:
            logger.warning("OromaWrapper: kein camera_hub-Modul gefunden – Vision benötigt explizite Frames.")

    def _choose_backend(self) -> str:
        order = ["hailo", "degirum", "cpu"]
        if BACKEND_PREF in ("hailo", "degirum", "cpu"):
            order = [BACKEND_PREF] + [x for x in order if x != BACKEND_PREF]
        if "hailo" in order and _hailo is not None:
            return "hailo"
        if "degirum" in order and _degirum is not None:
            return "degirum"
        return "cpu"

    # ---------------- Frame/Luma-Helfer ----------------
    def _maybe_log_infer_ms(self, backend: str, t0_perf: float) -> None:
        """Schreibt (rate-limited) die Inferenzzeit in ms in die metrics-Tabelle.

        Nur hier kennen wir den echten Runtime-Backend-Pfad (cpu/hailo/degirum).
        """
        try:
            every = int(getattr(self, "_infer_metric_every_sec", 5) or 5)
        except Exception:
            every = 5
        if every < 1:
            every = 1

        now_ts = int(time.time())
        last = int(getattr(self, "_last_infer_metric_ts", 0) or 0)
        if (now_ts - last) < every:
            return

        ms = (time.perf_counter() - float(t0_perf)) * 1000.0
        try:
            from core import sql_manager
            sql_manager.insert_metric(key=f"vision:infer_ms:{backend}", value=float(ms), ts=now_ts)
            self._last_infer_metric_ts = now_ts
        except Exception as e:
            logger.debug("infer_ms metric write failed (%s): %s", backend, e)

        # Secondary sink: stats.db → stats_points (UI-friendly).
        # Writes laufen via DBWriter (Multi-DB), damit kein lokaler sqlite3 Writer entsteht.
        try:
            if _dbw_enabled():
                _ensure_stats_points_schema_best_effort()
                series = f"metric:vision:infer_ms:{backend}"
                src_uid = hashlib.sha1(f"{now_ts}|{series}|{ms:.6f}|{os.getpid()}".encode("utf-8", "ignore")).hexdigest()
                db_writer_client.exec_write(
                    "INSERT OR IGNORE INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta) VALUES(?,?,?,?,?,?,?)",
                    [int(now_ts), series, float(ms), "metrics", 0, src_uid, None],
                    tag="oroma_wrapper.infer_ms.stats_points",
                    priority="low",
                    timeout_ms=2000,
                    db="stats",
                )
        except Exception:
            pass


    def _bootstrap_infer_ms_once(self) -> None:
        """Einmaliger Bootstrap-Write für infer_ms Keys (Hard-Visibility nach Restart).

        Ziel:
        - Wenn nach einem Restart (oder frischem Deploy) noch **keine** Einträge für
          `vision:infer_ms:*` existieren, soll die UI/SQL innerhalb von Sekunden
          sichtbar werden ("nicht mehr n/a").
        - Das ist **kein** Performance-Messwert, sondern nur ein "Presence Marker".

        Verhalten:
        - Läuft nur 1× pro Prozess/Wrapper-Instanz.
        - Standard: aktiv. Abschaltbar via ENV: OROMA_VISION_INFER_BOOTSTRAP=0
        - Schreibt (best-effort, kurze Timeouts) in:
            1) oroma.db → metrics (key=vision:infer_ms:{cpu|hailo|degirum})
            2) stats.db → stats_points (series=metric:vision:infer_ms:{...})
        - Wenn DB gerade gelockt ist → wird still übersprungen (keine Blockade im Hot-Path).
        """
        if getattr(self, "_infer_bootstrap_done", False):
            return
        self._infer_bootstrap_done = True

        flag = str(os.environ.get("OROMA_VISION_INFER_BOOTSTRAP", "1")).strip().lower()
        if flag in ("0", "false", "no", "off"):
            return

        now_ts = int(time.time())
        try:
            val = float(os.environ.get("OROMA_VISION_INFER_BOOTSTRAP_VALUE", "0.0"))
        except Exception:
            val = 0.0

        backends = ("cpu", "hailo", "degirum")

        # 1) oroma.db → metrics (Presence Marker)
        try:
            from core import sql_manager
            # Existiert schon? (Read bleibt lokal)
            with sql_manager.get_conn() as conn:
                r = conn.execute("SELECT 1 FROM metrics WHERE key LIKE 'vision:infer_ms:%' LIMIT 1").fetchone()
            if not r:
                for b in backends:
                    sql_manager.insert_metric(key=f"vision:infer_ms:{b}", value=float(val), ts=now_ts)
        except Exception:
            pass

        # 2) stats.db → stats_points (UI-friendly)
        try:
            if _dbw_enabled():
                _ensure_stats_points_schema_best_effort()
                # Best-effort Presence Marker: wir nutzen OR IGNORE (unique index),
                # damit der Bootstrap nicht dupliziert.
                for b in backends:
                    series = f"metric:vision:infer_ms:{b}"
                    src_uid = hashlib.sha1(f"bootstrap|{now_ts}|{series}".encode("utf-8", "ignore")).hexdigest()
                    db_writer_client.exec_write(
                        "INSERT OR IGNORE INTO stats_points(ts, series, value, src_table, src_id, src_uid, meta) VALUES(?,?,?,?,?,?,?)",
                        [int(now_ts), series, float(val), "metrics", 0, src_uid, "bootstrap"],
                        tag="oroma_wrapper.infer_ms.bootstrap",
                        priority="low",
                        timeout_ms=5000,
                        db="stats",
                    )
        except Exception:
            pass

    def _decode_jpeg_frame_best_effort(self, jpeg: Any):
        """Dekodiert best effort JPEG-Bytes/bytearray/memoryview -> BGR-Frame.

        Hintergrund:
        - Im laufenden ORÓMA-System kann der gemeinsame Hub je nach Provider
          oder Timing ein aktuelles JPEG bereitstellen, obwohl kein frischer
          raw frame über get_latest_frame()/get_frame_with_ts() geliefert wird.
        - Die Video-UI nutzt diesen Pfad bereits erfolgreich. Für embed(None)
          und die AV-SnapToken-Lernkette benötigen wir dieselbe Robustheit.

        Rückgabe:
        - numpy.ndarray (BGR) oder None
        """
        if jpeg in (None, b"", bytearray(), memoryview(b"")):
            return None
        if cv2 is None or np is None:
            return None
        try:
            if isinstance(jpeg, memoryview):
                jpeg = jpeg.tobytes()
            elif isinstance(jpeg, bytearray):
                jpeg = bytes(jpeg)
            elif not isinstance(jpeg, (bytes, bytearray)):
                return None
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            if arr.size <= 0:
                return None
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return frame if frame is not None else None
        except Exception:
            return None

    def _get_frame_from_hub(self):
        """Best-effort Frame-Quelle für embed(frame=None).

        Hintergrund:
        - In der ORÓMA-Pipeline wird embed() teils ohne explizites Frame aufgerufen.
          (z. B. hooks_av_snaptoken / vision_scene_infer_hook).
        - Für echte Inferenz (und damit infer_ms Telemetrie) brauchen wir ein reales Bild.

        WICHTIG (v3.7.3 Realität):
        - `core.camera_hub` ist eine Kompatibilitäts-Bridge und bietet **kein** get_hub().
          Stattdessen: get_frame() / get_frame_with_ts().
        - `core.device_hub.DeviceHub.get_latest_frame()` akzeptiert nur `ensure_start=...`,
          nicht `max_age_sec=...`. Die Altersprüfung erfolgt hier anhand des zurückgegebenen ts.

        Strategie (best effort, niemals crashen):
        1) Nur bereits vorhandene Hub-Puffer lesen (kein aktives Starten im Hotpath)
        2) raw frame aus camera_hub / device_hub, falls bereits vorhanden
        3) JPEG-Fallback über latest_jpeg() + cv2.imdecode(...)

        WICHTIG für AV-SnapToken / embed(None):
        - Dieser Pfad ist bewusst non-blocking und buffer-only.
        - Er darf weder libcamera noch DeviceHub aktiv "hochziehen", weil der
          Hook sonst hängt oder in Konkurrenz zum laufenden Video-/Hub-Pfad tritt.
        - Wenn noch kein warmer Buffer vorhanden ist, wird schnell None geliefert.

        Rückgabe:
        - numpy.ndarray (typisch BGR ndarray) oder None
        """
        max_age = float(os.environ.get("OROMA_VISION_FRAME_MAX_AGE_SEC", "2.0") or 2.0)
        if max_age <= 0.0:
            max_age = 2.0

        now = time.time()

        # 1) camera_hub (Bridge) – nur bereits vorhandene Puffer lesen.
        #    Kein ensure_start=True im Hook-/embed(None)-Hotpath, damit wir nicht
        #    selbst Bridge/Kamera initialisieren und dadurch blockieren.
        try:
            from core import camera_hub  # Kompatibilitätsschicht (DeviceHub/Provider)
            get_frame_with_ts = getattr(camera_hub, "get_frame_with_ts", None)
            if callable(get_frame_with_ts):
                try:
                    fr, ts = get_frame_with_ts(ensure_start=False)
                except TypeError:
                    fr, ts = get_frame_with_ts()
                if fr is not None and float(ts or 0.0) > 0.0:
                    if (now - float(ts)) <= max_age:
                        return fr
            get_frame = getattr(camera_hub, "get_frame", None)
            if callable(get_frame):
                try:
                    fr = get_frame(ensure_start=False)
                except TypeError:
                    fr = get_frame()
                if fr is not None:
                    return fr
        except Exception:
            pass

        # 2) device_hub (Fallback) – ebenfalls nur bereits vorhandene Puffer lesen.
        try:
            from core.device_hub import get_hub as get_dev_hub
            hub = get_dev_hub()
            if hub is not None:
                get_latest_frame = getattr(hub, "get_latest_frame", None)
                if callable(get_latest_frame):
                    try:
                        fr, ts = get_latest_frame(ensure_start=False)
                    except TypeError:
                        fr, ts = get_latest_frame()
                    if fr is not None and float(ts or 0.0) > 0.0:
                        if (now - float(ts)) <= max_age:
                            return fr
        except Exception:
            pass

        # 3) JPEG-Fallback – angleichen an die robuste Video-UI-Logik.
        #    Dieser Pfad ist wichtig in External-Frame/Hub-Push Setups, in
        #    denen latest_jpeg() vorhanden ist, aber raw frames nicht frisch
        #    genug oder temporär leer sind.
        try:
            from core import camera_hub
            jpeg = None
            get_latest_jpeg = getattr(camera_hub, "get_latest_jpeg", None)
            if callable(get_latest_jpeg):
                try:
                    jpeg = get_latest_jpeg(client="oroma_wrapper", ensure_start=False)
                except TypeError:
                    try:
                        jpeg = get_latest_jpeg(client="oroma_wrapper")
                    except TypeError:
                        try:
                            jpeg = get_latest_jpeg(ensure_start=False)
                        except TypeError:
                            jpeg = get_latest_jpeg()
            frame = self._decode_jpeg_frame_best_effort(jpeg)
            if frame is not None:
                return frame
        except Exception:
            pass

        try:
            from core.device_hub import get_hub as get_dev_hub
            hub = get_dev_hub()
            if hub is not None:
                jpeg = None
                get_latest_jpeg = getattr(hub, "get_latest_jpeg", None)
                if callable(get_latest_jpeg):
                    try:
                        jpeg = get_latest_jpeg(client="oroma_wrapper", ensure_start=False)
                    except TypeError:
                        try:
                            jpeg = get_latest_jpeg(client="oroma_wrapper")
                        except TypeError:
                            try:
                                jpeg = get_latest_jpeg(ensure_start=False)
                            except TypeError:
                                jpeg = get_latest_jpeg()
                frame = self._decode_jpeg_frame_best_effort(jpeg)
                if frame is not None:
                    return frame
        except Exception:
            pass

        return None
    def _get_luma_from_hub(self) -> Optional[float]:
        """
        Holt (wenn möglich) die Luma 0..255 vom camera_hub (z. B. für Circadian).
        """
        if not camera_hub:
            return None
        try:
            return camera_hub.get_luma()  # 0..255 oder None
        except Exception as e:
            logger.debug("CameraHub get_luma() Fehler: %s", e)
            return None

    # ---------------- Vision API ----------------
    def detect(self, frame=None) -> Dict[str, Any]:
        """
        Objekterkennung.
        Wenn frame=None, versuchen wir CameraHub zu nutzen (ohne Kamera doppelt zu öffnen).
        """
        if frame is None:
            frame = self._get_frame_from_hub()
            if frame is None:
                logger.warning(
                    "detect(): kein Frame verfügbar (kein CameraHub / kein Frame) → Dummy-Fallback"
                )
                return {"ok": True, "dummy": True, "features": [], "reason": "no frame"}

        # 1) NPU-Backends
        if self.backend == "hailo" and _hailo:
            try:
                return _hailo.detect(frame)
            except Exception as e:
                logger.warning("Hailo detect() Fehler: %s", e)
                if not FAILOVER:
                    raise

        if self.backend == "degirum" and _degirum:
            try:
                return _degirum.detect(frame)
            except Exception as e:
                logger.warning("DeGirum detect() Fehler: %s", e)
                if not FAILOVER:
                    raise

        # 2) CPU/ONNX
        if _vis and hasattr(_vis, "detect"):
            try:
                return _vis.detect(frame)
            except Exception as e:
                logger.warning("CPU/ONNX detect() Fehler: %s", e)
                if not FAILOVER:
                    raise

        logger.warning("Vision detect() → Dummy-Fallback genutzt")
        return {"ok": True, "dummy": True, "features": [0.0], "reason": "no vision backend"}

    def embed(self, frame=None) -> Dict[str, Any]:
        """
        Embedding-Erzeugung fuer die primitive Vision-Lernkette.

        Architektur-Leitlinie:
        - `embed(frame=None)` ist fuer ORÓMA-SnapTokens / vision/token ein leichter,
          hub-first, non-blocking Wahrnehmungspfad.
        - Standardpfad ist deshalb immer der CPU-/VisionWrapper-Embed ueber ein
          bereits vorhandenes Hub-Frame.
        - Hailo/DeGirum bleiben fuer Detect-/Inferenzpfade oder explizite Aktivierung
          relevant, werden hier aber nicht mehr still als Default benutzt.

        Aktivierung optionaler NPU-Embeds in diesem Pfad:
        - OROMA_EMBED_BACKEND=cpu|hailo|degirum|auto   (Default: cpu)
          * cpu     -> immer _vis.embed(frame)
          * hailo   -> bevorzugt _hailo.embed(frame), Fallback _vis.embed(frame)
          * degirum -> bevorzugt _degirum.embed(frame), Fallback _vis.embed(frame)
          * auto    -> aktuell wie cpu behandeln, um die Lernkette nicht zu blockieren
        """
        global _DUMMY_WARNED_NO_FRAME, _DUMMY_WARNED_NO_BACKEND

        self._bootstrap_infer_ms_once()

        if frame is None:
            frame = self._get_frame_from_hub()
            if frame is None:
                if _DUMMY_VERBOSE or not _DUMMY_WARNED_NO_FRAME:
                    logger.warning("embed(): kein Frame verfügbar → Dummy-Fallback")
                else:
                    logger.debug("embed(): kein Frame verfügbar → Dummy-Fallback")
                _DUMMY_WARNED_NO_FRAME = True
                return {
                    "ok": True,
                    "dummy": True,
                    "embedding": [0.0],
                    "reason": "no frame",
                }

        embed_backend = os.environ.get("OROMA_EMBED_BACKEND", "cpu").strip().lower() or "cpu"
        if embed_backend == "auto":
            embed_backend = "cpu"

        # 2) Standardpfad fuer die primitive Lernkette: leichter CPU-/VisionWrapper-Embed.
        if embed_backend == "cpu":
            if _vis and hasattr(_vis, "embed"):
                try:
                    t0_perf = time.perf_counter()
                    res = _vis.embed(frame)
                    self._maybe_log_infer_ms("cpu", t0_perf)
                    return res
                except Exception as e:
                    logger.warning("CPU/ONNX embed() Fehler: %s", e)
                    if not FAILOVER:
                        raise
            embed_backend = "fallback"

        # 3) Optional explizite NPU-Embeds (nur wenn bewusst aktiviert).
        if embed_backend == "hailo" and _hailo and hasattr(_hailo, "embed"):
            try:
                t0_perf = time.perf_counter()
                res = _hailo.embed(frame)
                self._maybe_log_infer_ms("hailo", t0_perf)
                return res
            except Exception as e:
                logger.warning("Hailo embed() Fehler: %s", e)
                if not FAILOVER:
                    raise

        if embed_backend == "degirum" and _degirum and hasattr(_degirum, "embed"):
            try:
                t0_perf = time.perf_counter()
                res = _degirum.embed(frame)
                self._maybe_log_infer_ms("degirum", t0_perf)
                return res
            except Exception as e:
                logger.warning("DeGirum embed() Fehler: %s", e)
                if not FAILOVER:
                    raise

        # 4) Fallback immer wieder zur leichten Vision-CPU-Schicht.
        if _vis and hasattr(_vis, "embed"):
            try:
                t0_perf = time.perf_counter()
                res = _vis.embed(frame)
                self._maybe_log_infer_ms("cpu", t0_perf)
                return res
            except Exception as e:
                logger.warning("CPU/ONNX embed() Fehler: %s", e)
                if not FAILOVER:
                    raise

        if _DUMMY_VERBOSE or not _DUMMY_WARNED_NO_BACKEND:
            logger.warning("Vision embed() → Dummy-Fallback genutzt (kein Vision-Backend aktiv)")
        else:
            logger.debug("Vision embed() → Dummy-Fallback genutzt (kein Vision-Backend aktiv)")
        _DUMMY_WARNED_NO_BACKEND = True

        return {
            "ok": True,
            "dummy": True,
            "embedding": [0.0],
            "reason": "no embedding backend",
        }

    # ---------------- Audio / ASR ----------------
    def asr_stream(
        self,
        language: str = "de",
        model_name: str = "small",
        duration: float = 5.0,
        **kwargs,
    ) -> Dict[str, Any]:
        """Audio ASR (One-shot) via wrappers.audio_wrapper.asr_stream.

        WICHTIG:
          - Diese Methode ist absichtlich "passthrough" und akzeptiert **kwargs,
            damit UI/Tools neue Parameter senden können, ohne dass die Signatur
            sofort erneut angepasst werden muss.

        Beispiele (UI/REST):
          - gain_db: float (dB)  → wird bis in DeviceHub.record_wav durchgereicht
        """
        if _asr and hasattr(_asr, "asr_stream"):
            try:
                return _asr.asr_stream(
                    language=language,
                    model_name=model_name,
                    duration=duration,
                    **kwargs,
                )
            except Exception as e:
                logger.warning("ASR Fehler: %s", e)
                if not FAILOVER:
                    raise
        logger.warning("ASR asr_stream() → Dummy-Fallback genutzt")
        return {"ok": True, "dummy": True, "tokens": [], "reason": "no asr backend"}

    # ---------------- TTS ----------------
    def tts_say(self, text: str) -> Dict[str, Any]:
        if _tts and hasattr(_tts, "speak"):
            try:
                _tts.speak(text)
                return {"ok": True}
            except Exception as e:
                logger.warning("TTS Fehler: %s", e)
                if not FAILOVER:
                    raise
                return {"ok": False, "error": str(e)}
        logger.warning("TTS tts_say() → Dummy-Fallback genutzt")
        return {"ok": True, "dummy": True, "reason": "no tts backend"}

    # ---------------- Light Reactivity (optional) ----------------
    def enable_light_reactivity(
        self,
        on_change: Optional[Callable[[str, float], None]] = None,
        interval_s: Optional[int] = None,
        dark_thr: Optional[float] = None,
        bright_thr: Optional[float] = None,
        hold: Optional[int] = None,
    ) -> None:
        """
        Startet einen Hintergrund-Worker, der periodisch die Luma(0..255) vom CameraHub liest
        und bei Hysterese-Wechsel (DARK↔BRIGHT) optional on_change(state, luma) aufruft.
        """
        if self._light_thread and self._light_thread.is_alive():
            logger.info("Light-Reactivity bereits aktiv")
            return

        if not camera_hub:
            logger.warning(
                "Light-Reactivity aktiviert, aber kein CameraHub verfügbar – läuft im 'noop'-Modus."
            )
        self._on_change = on_change

        interval = int(os.environ.get("OROMA_LIGHT_INTERVAL_SEC", str(interval_s or 300)))
        d_thr = float(os.environ.get("OROMA_LIGHT_DARK_THR", str(dark_thr or 30)))
        b_thr = float(os.environ.get("OROMA_LIGHT_BRIGHT_THR", str(bright_thr or 40)))
        hold_n = int(os.environ.get("OROMA_LIGHT_HOLD", str(hold or 2)))

        if b_thr <= d_thr:
            b_thr = d_thr + 1.0

        self._light_stop.clear()

        def _worker():
            logger.info(
                "Light-Reactivity gestartet (interval=%ss, dark<=%.1f, bright>=%.1f, hold=%d)",
                interval,
                d_thr,
                b_thr,
                hold_n,
            )
            consec_dark = 0
            consec_bright = 0
            while not self._light_stop.is_set():
                try:
                    luma = self._get_luma_from_hub()
                    if luma is None:
                        # Kein Hub → konservativ: BRIGHT, aber Luma bleibt None
                        with self._light_lock:
                            self._light_luma = None
                        self._light_stop.wait(interval)
                        continue

                    with self._light_lock:
                        self._light_luma = float(luma)
                        cur_state = self._light_state

                    if luma <= d_thr:
                        consec_dark += 1
                        consec_bright = 0
                    elif luma >= b_thr:
                        consec_bright += 1
                        consec_dark = 0
                    else:
                        consec_dark = 0
                        consec_bright = 0

                    new_state: Optional[str] = None
                    if consec_dark >= hold_n and self._light_state != "DARK":
                        new_state = "DARK"
                    elif consec_bright >= hold_n and self._light_state != "BRIGHT":
                        new_state = "BRIGHT"

                    if new_state:
                        with self._light_lock:
                            self._light_state = new_state
                        logger.info("Light-Reactivity: %s (luma=%.1f)", new_state, luma)
                        if self._on_change:
                            try:
                                self._on_change(new_state, float(luma))
                            except Exception as e:
                                logger.warning("on_change() Fehler: %s", e)
                        consec_dark = 0
                        consec_bright = 0

                except Exception as e:
                    logger.warning("Light-Worker Fehler: %s", e)
                finally:
                    # Schlafen am Ende der Schleife (robust bei Fehlern)
                    self._light_stop.wait(interval)

            logger.info("Light-Reactivity gestoppt")

        self._light_thread = threading.Thread(target=_worker, daemon=True)
        self._light_thread.start()

    def disable_light_reactivity(self) -> None:
        if not self._light_thread:
            return
        self._light_stop.set()
        try:
            self._light_thread.join(timeout=3.0)
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="wrappers.oroma_wrapper.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
        self._light_thread = None

    def get_light_level(self) -> Dict[str, Any]:
        """
        Liefert den aktuellen Lichtzustand (state, luma), wie er vom Light-Worker
        verwaltet wird. Kann auch genutzt werden, wenn Light-Reactivity aktiv ist.
        """
        with self._light_lock:
            return {"state": self._light_state, "luma": self._light_luma}

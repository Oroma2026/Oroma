#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/nmr_lite.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     NMR-Lite – Lightweight Mismatch / Surprise / Replay-Priorisierung
# Version:   v0.1.1+dbwriter-persist-v1 (produktive NMR-Lite-Persistenz via DBWriter)
# Stand:     2026-05-26
# Autor:     ORÓMA · Jörg Werner + OpenAI GPT-5.4 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul implementiert die erste produktive, Raspberry-Pi-taugliche
# Laufzeitbasis für NMR-Lite in ORÓMA.
#
# NMR-Lite ist hier bewusst nicht als großes semantisches Subsystem ausgelegt,
# sondern als kleine, robuste und gut beobachtbare Signal-Schicht mit vier
# Hauptaufgaben:
#
#   1) Observation State O(t) billig zusammensetzen
#   2) Prediction-Error / Mismatch (PE) in-memory berechnen
#   3) daraus stabile Aggregat-Signale ableiten
#   4) frühe strukturelle Hints (binding_hint / crossmodal_hint) erzeugen
#
# Das Modul ist so gebaut, dass es später konservativ an replay_manager.py
# angeschlossen werden kann, ohne den bestehenden ORÓMA-Hotpath zu destabilisieren.
#
# WICHTIGE DESIGNENTSCHEIDUNGEN
# ─────────────────────────────
# 1) Raspberry Pi first
#    - keine schweren Embeddings
#    - keine GPU-/Cloud-Abhängigkeit
#    - keine per-Tick-DB-Schreiblast
#    - kleine Vektoren, feste / transparente Berechnung
#
# 2) Minimal-invasive Integration
#    - Empathy wird direkt in-memory gelesen
#    - Curriculum wird über kleinen TTL-Cache gelesen
#    - Audio/Vision laufen über leichte Runtime-Bridges (letzter bekannter Wert)
#    - snap_rate wird lokal gezählt
#
# 3) Keine stille Datenbank-Abhängigkeit im Fast Path
#    - Fast Tick (~0.25s): reine In-Memory-Berechnung
#    - Aggregation (~5s): In-Memory-Auswertung
#    - Persistenz (~30s): nur aggregierte Metriken, ENV-gated
#
# 4) Headless / Robust / best effort
#    - fehlende Modalitäten degradieren die Confidence statt das System zu stoppen
#    - Kamera-Ausfall wird NICHT als Novelty/Surprise interpretiert
#    - Audio-Ausfall deaktiviert crossmodal_hint, aber nicht NMR insgesamt
#
# PHASE-ABDECKUNG
# ───────────────
# Diese Datei deckt die produktive Basis für folgende Roadmap-Teile ab:
#
#   Phase A:
#     - O(t)-Erfassung
#     - working vector
#     - nmr_pe
#     - nmr_pe_ema
#     - nmr_surprise_event
#     - nmr_priority_score
#
#   Phase A.5:
#     - binding_hint
#     - binding_hint_score
#     - crossmodal_hint
#
# Nicht enthalten (spätere Phasen):
#   - direkter PTZ-/Motor-/Policy-Bias
#   - ObjectGraph-Rewiring
#   - Predictor-Kopplung
#   - tiefes Binding / binding_confidence
#   - Dream-spezifische Strukturintegration
#
# SIGNALQUELLEN / FAST-PATH-STRATEGIE
# ───────────────────────────────────
# Empathy:
#   - core.empathy.STATE / empathy.get_state()
#   - direkt in-memory
#
# Curriculum:
#   - core.curriculum.get_state()
#   - TTL-Cache (Default 30s)
#
# Audio:
#   - Bridge-Funktionen update_audio_signal(...)
#   - erwartet RMS und centroid_hz / pitch-proxy
#   - letzter Wert + Delta-Berechnung in-memory
#
# Vision:
#   - Bridge-Funktionen update_vision_signal(...)
#   - fp12, scene_change und optional repeat
#   - repeat kann zusätzlich per TTL-Quelle geliefert werden, wenn später vorhanden
#
# Snap-Rate:
#   - lokaler Zähler in diesem Modul
#   - kein externer DB-Pfad nötig
#
# ÖFFENTLICHE API (BEWUSST KLEIN)
# ───────────────────────────────
# Bridge-Updates:
#   update_audio_signal(rms, pitch, ts=None)
#   update_vision_signal(fp12=None, scene_change=None, repeat=None, ts=None)
#   increment_snap_counter(n=1)
#
# Laufzeit:
#   tick(now_ts=None)                 -> NMROutput
#   get_output()                      -> dict
#   get_observation_snapshot()        -> dict
#   maybe_persist(now_ts=None)        -> bool
#   reset_runtime_state()             -> None
#
# Singleton:
#   NMR = get_nmr()
#
# INTEGRATIONS-HINWEIS
# ────────────────────
# Dieses Modul erzeugt bereits produktive Signale, integriert sich aber noch
# bewusst NICHT selbstständig in AgentLoop, ReplayManager oder Audio-/Vision-Hooks.
# Das geschieht im nächsten Patch-Schritt bewusst separat, damit:
#   - Patch-Grenzen klein bleiben
#   - Metrik-/Runtime-Verhalten isoliert geprüft werden kann
#   - py_compile / Smoke-Tests klar bleiben
#
# FEHLERPOLITIK
# ────────────
# - Keine relevante Ausnahme darf den Hauptlauf stoppen.
# - Alle externen Reads sind defensiv.
# - Persistenz ist best effort.
# - Fehlende Signale reduzieren Confidence, aber nicht die Grundfunktion.
#
# =============================================================================

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from core.log_guard import log_suppressed

LOG = logging.getLogger(__name__)

try:
    from core import empathy
except Exception:  # pragma: no cover - defensive import
    empathy = None  # type: ignore

try:
    from core import curriculum
except Exception:  # pragma: no cover - defensive import
    curriculum = None  # type: ignore

try:
    from core import sql_manager
except Exception:  # pragma: no cover - defensive import
    sql_manager = None  # type: ignore

try:
    from core import db_writer_client
except Exception:  # pragma: no cover - defensive import
    db_writer_client = None  # type: ignore


# -----------------------------------------------------------------------------
# ENV helpers
# -----------------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return bool(default)
    return str(v).strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.environ.get(name, str(default))).strip()))
    except Exception:
        return int(default)


# -----------------------------------------------------------------------------
# Runtime configuration (Pi-safe defaults)
# -----------------------------------------------------------------------------
_NMR_ENABLE = _env_bool("OROMA_NMR_ENABLE", True)
_NMR_PERSIST = _env_bool("OROMA_NMR_PERSIST", True)
_NMR_TICK_SEC = max(0.05, _env_float("OROMA_NMR_TICK_SEC", 0.25))
_NMR_AGG_WINDOW_SEC = max(1.0, _env_float("OROMA_NMR_AGG_WINDOW_SEC", 5.0))
_NMR_PERSIST_WINDOW_SEC = max(5.0, _env_float("OROMA_NMR_PERSIST_WINDOW_SEC", 30.0))

_NMR_CURRICULUM_CACHE_TTL_SEC = max(1.0, _env_float("OROMA_NMR_CURRICULUM_CACHE_TTL_SEC", 30.0))
_NMR_REPEAT_CACHE_TTL_SEC = max(1.0, _env_float("OROMA_NMR_REPEAT_CACHE_TTL_SEC", 10.0))

_NMR_USE_VISION = _env_bool("OROMA_NMR_USE_VISION", True)
_NMR_USE_AUDIO = _env_bool("OROMA_NMR_USE_AUDIO", True)
_NMR_USE_CURRICULUM = _env_bool("OROMA_NMR_USE_CURRICULUM", True)
_NMR_USE_EMPATHY = _env_bool("OROMA_NMR_USE_EMPATHY", True)
_NMR_USE_RUNTIME = _env_bool("OROMA_NMR_USE_RUNTIME", True)

_NMR_SURPRISE_THRESHOLD = _env_float("OROMA_NMR_SURPRISE_THRESHOLD", 0.65)
_NMR_BINDING_PE_THRESHOLD = _env_float("OROMA_NMR_BINDING_PE_THRESHOLD", 0.65)
_NMR_BINDING_SCENE_THRESHOLD = _env_float("OROMA_NMR_BINDING_SCENE_THRESHOLD", 0.55)
_NMR_BINDING_REPEAT_MAX = _env_float("OROMA_NMR_BINDING_REPEAT_MAX", 0.75)
_NMR_BINDING_CURRICULUM_ACC_MAX = _env_float("OROMA_NMR_BINDING_CURRICULUM_ACC_MAX", 0.70)
_NMR_AUDIO_RMS_DELTA_THRESHOLD = _env_float("OROMA_NMR_AUDIO_RMS_DELTA_THRESHOLD", 0.4)
_NMR_AUDIO_PITCH_DELTA_THRESHOLD = _env_float("OROMA_NMR_AUDIO_PITCH_DELTA_THRESHOLD", 15.0)

_NMR_METRIC_PREFIX = (os.environ.get("OROMA_NMR_METRIC_PREFIX") or "nmr").strip() or "nmr"


# -----------------------------------------------------------------------------
# Persistence helpers
# -----------------------------------------------------------------------------

def _dbw_enabled() -> bool:
    """Return True when the ORÓMA DBWriter IPC path is active for writes."""
    try:
        return bool(db_writer_client is not None and hasattr(db_writer_client, "enabled") and db_writer_client.enabled())
    except Exception:
        return False


def _dbw_timeout_ms() -> int:
    """Timeout for NMR-Lite metric writes through the single-writer DB path."""
    return max(500, _env_int("OROMA_NMR_DBW_TIMEOUT_MS", _env_int("OROMA_DBW_TIMEOUT_MS", 60000)))


def _persist_metric_row(key: str, value: float, ts: int) -> int:
    """Persist one NMR metric and return a confirmed row id.

    DB discipline:
      - When OROMA_DBW_ENABLE=1, write through core.db_writer_client so the
        global single-writer owns the SQLite write path.
      - When DBWriter is disabled, keep the legacy sql_manager.insert_metric()
        path for local/dev operation.
      - A missing/zero row id is treated as failure; maybe_persist() must never
        report success for an unconfirmed write.
    """
    metric_key = str(key)
    metric_value = float(value)
    metric_ts = int(ts)

    if _dbw_enabled():
        if db_writer_client is None or not hasattr(db_writer_client, "exec_lastrowid"):
            raise RuntimeError("DBWriter is enabled, but db_writer_client.exec_lastrowid is unavailable")
        rowid = int(db_writer_client.exec_lastrowid(
            "INSERT INTO metrics (key, ts, value) VALUES (?, ?, ?)",
            params=(metric_key, metric_ts, metric_value),
            tag="nmr_lite.persist_metric",
            priority="normal",
            timeout_ms=_dbw_timeout_ms(),
            db="oroma",
        ))
        if rowid <= 0:
            raise RuntimeError(f"DBWriter returned invalid rowid for metric {metric_key!r}: {rowid}")
        return rowid

    if sql_manager is None or not hasattr(sql_manager, "insert_metric"):
        raise RuntimeError("sql_manager.insert_metric is unavailable and DBWriter is disabled")
    rowid = sql_manager.insert_metric(metric_key, metric_value, metric_ts)  # type: ignore[attr-defined]
    if rowid is None or int(rowid) <= 0:
        raise RuntimeError(f"sql_manager.insert_metric returned invalid rowid for metric {metric_key!r}: {rowid!r}")
    return int(rowid)


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

def _clip(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return float(v)


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return float(value)
    except Exception:
        return default


def _safe_now(now_ts: Optional[float] = None) -> float:
    try:
        return float(now_ts) if now_ts is not None else float(time.time())
    except Exception:
        return float(time.time())


def _normalize_zero_one(value: Optional[float], default: float = 0.0) -> float:
    if value is None:
        return float(default)
    return _clip(float(value), 0.0, 1.0)


def _normalize_signed(value: Optional[float], default: float = 0.0) -> float:
    if value is None:
        return _clip(default, -1.0, 1.0)
    return _clip(float(value), -1.0, 1.0)


def _normalize_pitch_hz(value: Optional[float]) -> float:
    """Normiert pitch-/centroid_hz grob in 0..1 für den working vector.

    Die Doku erlaubt pitch als Proxy; hooks_audio_snaptoken.py liefert centroid_hz.
    6000 Hz ist bereits in ORÓMA an anderer Stelle als grobe Normierung etabliert.
    """
    if value is None:
        return 0.0
    return _clip(float(value) / 6000.0, 0.0, 1.0)


def _normalize_curriculum_level(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    # konservative Kappung; höheres Level zählt, aber saturiert früh.
    return _clip(float(value) / 10.0, 0.0, 1.0)


def _normalize_repeat(value: Optional[float], default: float = 0.0) -> float:
    """Normiert Wiederholungszähler in 0..1.

    Live-Fix 2026-05-25:
      derive_working_vector(), _compute_priority_score() und
      _compute_binding_hints() rufen diese Helper-Funktion mit default=... auf.
      Ohne default-Parameter entsteht beim ersten aktiven NMR-Tick ein TypeError,
      wodurch nmr_pe/nmr_pe_ema/binding_hint/crossmodal_hint nicht produktiv
      aktualisiert werden. Die Semantik bleibt unverändert: None nutzt default,
      reale Werte saturieren konservativ bei 20 Wiederholungen.
    """
    if value is None:
        return _clip(float(default), 0.0, 1.0)
    # Wiederholung ist potentiell ungebunden; 20+ Wiederholungen werden saturiert.
    return _clip(float(value) / 20.0, 0.0, 1.0)


def _mean_abs_delta(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n <= 0:
        return 0.0
    total = 0.0
    for i in range(n):
        total += abs(float(a[i]) - float(b[i]))
    return _clip(total / float(n), 0.0, 1.0)


# -----------------------------------------------------------------------------
# TTL cache helper
# -----------------------------------------------------------------------------
@dataclass
class TTLValue:
    value: Any = None
    fetched_ts: float = 0.0
    ttl_sec: float = 10.0

    def is_fresh(self, now_ts: float) -> bool:
        return (now_ts - float(self.fetched_ts)) <= float(self.ttl_sec)

    def get_or_refresh(self, now_ts: float, fetcher: Callable[[], Any]) -> Any:
        if self.is_fresh(now_ts):
            return self.value
        try:
            self.value = fetcher()
            self.fetched_ts = float(now_ts)
        except Exception as e:
            log_suppressed(
                LOG,
                key="core.nmr_lite.ttl_fetch.pass.1",
                msg="Suppressed TTL fetch exception",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
        return self.value


# -----------------------------------------------------------------------------
# Shared fast-path bridges
# -----------------------------------------------------------------------------
@dataclass
class AudioSignalBridge:
    last_rms: Optional[float] = None
    last_pitch: Optional[float] = None
    last_ts: float = 0.0


@dataclass
class VisionSignalBridge:
    last_fp12: Optional[List[float]] = None
    last_scene_change: Optional[float] = None
    last_repeat: Optional[float] = None
    last_ts: float = 0.0


_AUDIO_BRIDGE = AudioSignalBridge()
_VISION_BRIDGE = VisionSignalBridge()
_BRIDGE_LOCK = threading.RLock()


def update_audio_signal(rms: Optional[float], pitch: Optional[float], ts: Optional[float] = None) -> None:
    """Fast-path bridge for audio RMS + pitch proxy.

    Erwartung für den produktiven Anschluss:
    - rms            aus hooks_audio_snaptoken.py
    - pitch / proxy  als centroid_hz aus hooks_audio_snaptoken.py

    Dieses Modul normiert den Pitch später selbst für den working vector.
    """
    now_ts = _safe_now(ts)
    with _BRIDGE_LOCK:
        _AUDIO_BRIDGE.last_rms = _to_float(rms)
        _AUDIO_BRIDGE.last_pitch = _to_float(pitch)
        _AUDIO_BRIDGE.last_ts = now_ts


def update_vision_signal(
    fp12: Optional[Iterable[float]] = None,
    scene_change: Optional[float] = None,
    repeat: Optional[float] = None,
    ts: Optional[float] = None,
) -> None:
    """Fast-path bridge for the latest compact visual signal state.

    fp12:
      12-dim compressed scene fingerprint (already compact, no recompute here)
    scene_change:
      scalar structural change / frame-diff signal
    repeat:
      optional familiarity / repeat hint if a cheap source is available
    """
    now_ts = _safe_now(ts)
    with _BRIDGE_LOCK:
        if fp12 is not None:
            try:
                vv = [float(x) for x in fp12]
                if len(vv) >= 12:
                    _VISION_BRIDGE.last_fp12 = vv[:12]
                elif vv:
                    _VISION_BRIDGE.last_fp12 = vv[:]
            except Exception:
                pass
        if scene_change is not None:
            _VISION_BRIDGE.last_scene_change = _to_float(scene_change)
        if repeat is not None:
            _VISION_BRIDGE.last_repeat = _to_float(repeat)
        _VISION_BRIDGE.last_ts = now_ts


# -----------------------------------------------------------------------------
# Observation / output dataclasses
# -----------------------------------------------------------------------------
@dataclass
class ObservationState:
    vision_fp12: Optional[List[float]] = None
    vision_repeat: Optional[float] = None
    vision_scene_change: Optional[float] = None
    audio_rms: Optional[float] = None
    audio_pitch: Optional[float] = None
    curriculum_acc: Optional[float] = None
    curriculum_level: Optional[float] = None
    empathy_valence: Optional[float] = None
    empathy_arousal: Optional[float] = None
    runtime_snap_rate: Optional[float] = None
    ts: float = 0.0
    modality_flags: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "vision_fp12": list(self.vision_fp12) if self.vision_fp12 is not None else None,
            "vision_repeat": self.vision_repeat,
            "vision_scene_change": self.vision_scene_change,
            "audio_rms": self.audio_rms,
            "audio_pitch": self.audio_pitch,
            "curriculum_acc": self.curriculum_acc,
            "curriculum_level": self.curriculum_level,
            "empathy_valence": self.empathy_valence,
            "empathy_arousal": self.empathy_arousal,
            "runtime_snap_rate": self.runtime_snap_rate,
            "ts": self.ts,
            "modality_flags": dict(self.modality_flags),
        }


@dataclass
class NMROutput:
    nmr_pe: float = 0.0
    nmr_pe_ema: float = 0.0
    nmr_surprise_event: int = 0
    nmr_priority_score: float = 0.0
    binding_hint: int = 0
    binding_hint_score: float = 0.0
    crossmodal_hint: int = 0
    confidence: float = 1.0
    ts: float = 0.0
    modality_flags: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nmr_pe": float(self.nmr_pe),
            "nmr_pe_ema": float(self.nmr_pe_ema),
            "nmr_surprise_event": int(self.nmr_surprise_event),
            "nmr_priority_score": float(self.nmr_priority_score),
            "binding_hint": int(self.binding_hint),
            "binding_hint_score": float(self.binding_hint_score),
            "crossmodal_hint": int(self.crossmodal_hint),
            "confidence": float(self.confidence),
            "ts": float(self.ts),
            "modality_flags": dict(self.modality_flags),
        }


@dataclass
class NMRLiteState:
    prev_working_vector: Optional[List[float]] = None
    current_pe: float = 0.0
    pe_ema: float = 0.0
    agg_window_start_ts: float = 0.0
    agg_window_values: List[float] = field(default_factory=list)
    surprise_event_count: int = 0
    last_persist_ts: float = 0.0
    vision_enabled: bool = True
    audio_enabled: bool = True
    prev_audio_rms: Optional[float] = None
    prev_audio_pitch: Optional[float] = None
    snap_counter: int = 0
    last_output: NMROutput = field(default_factory=NMROutput)
    last_observation: ObservationState = field(default_factory=ObservationState)


# -----------------------------------------------------------------------------
# Main NMR-Lite runtime
# -----------------------------------------------------------------------------
class NMRLite:
    """Erste produktive NMR-Lite Runtime.

    Wichtig:
    - arbeitet in-memory
    - kann bereits produktive Signale erzeugen
    - ist bewusst noch nicht selbst in Replay/PTZ integriert
    - ist so klein gehalten, dass ein Folge-Patch gezielt die Einhängepunkte
      anbinden kann
    """

    def __init__(self) -> None:
        self.enabled = bool(_NMR_ENABLE)
        self.state = NMRLiteState()
        self._lock = threading.RLock()
        self._curriculum_cache = TTLValue(ttl_sec=float(_NMR_CURRICULUM_CACHE_TTL_SEC))
        self._vision_repeat_cache = TTLValue(ttl_sec=float(_NMR_REPEAT_CACHE_TTL_SEC))
        self._ema_alpha = _clip(_NMR_TICK_SEC / max(0.5, _NMR_AGG_WINDOW_SEC), 0.02, 0.5)
        self._last_tick_ts = 0.0

    # ------------------------------------------------------------------
    # Public runtime helpers
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self.state = NMRLiteState()
            self._curriculum_cache = TTLValue(ttl_sec=float(_NMR_CURRICULUM_CACHE_TTL_SEC))
            self._vision_repeat_cache = TTLValue(ttl_sec=float(_NMR_REPEAT_CACHE_TTL_SEC))
            self._last_tick_ts = 0.0

    def increment_snap_counter(self, n: int = 1) -> None:
        with self._lock:
            self.state.snap_counter += max(0, int(n))

    # ------------------------------------------------------------------
    # Signal collection / fast-path integration
    # ------------------------------------------------------------------
    def _fetch_curriculum_state(self) -> Dict[str, Any]:
        if curriculum is None or not hasattr(curriculum, "get_state"):
            return {}
        try:
            st = curriculum.get_state()  # type: ignore[attr-defined]
            return st if isinstance(st, dict) else {}
        except Exception as e:
            log_suppressed(
                LOG,
                key="core.nmr_lite.curriculum_fetch.pass.1",
                msg="Suppressed curriculum.get_state() exception",
                exc=e,
                level=logging.WARNING,
                interval_s=120,
            )
            return {}

    def _fetch_curriculum_cached(self, now_ts: float) -> Dict[str, Any]:
        raw = self._curriculum_cache.get_or_refresh(now_ts, self._fetch_curriculum_state)
        return raw if isinstance(raw, dict) else {}

    def _fetch_vision_repeat(self, now_ts: float) -> Optional[float]:
        """Short TTL repeat access.

        Stand heute gibt es keinen sauberen generischen repeat_count-API-Call im Core,
        der billig genug für den Fast Path wäre. Deshalb nutzt die erste produktive
        Fassung primär den per Bridge gelieferten Wert und cached diesen kurz.

        Wenn später ein besserer billiger Source-Path existiert, kann hier ohne
        Hotpath-Umbau nachgeschärft werden.
        """
        def _fetch() -> Optional[float]:
            with _BRIDGE_LOCK:
                return _VISION_BRIDGE.last_repeat
        return _to_float(self._vision_repeat_cache.get_or_refresh(now_ts, _fetch))

    def collect_observation(self, now_ts: Optional[float] = None) -> ObservationState:
        ts = _safe_now(now_ts)
        flags: Dict[str, Any] = {
            "vision": False,
            "audio": False,
            "curriculum": False,
            "empathy": False,
            "runtime": False,
            "vision_degraded": False,
            "audio_degraded": False,
        }

        obs = ObservationState(ts=ts, modality_flags=flags)

        # Vision bridge
        if _NMR_USE_VISION:
            with _BRIDGE_LOCK:
                fp12 = list(_VISION_BRIDGE.last_fp12) if _VISION_BRIDGE.last_fp12 is not None else None
                sc = _to_float(_VISION_BRIDGE.last_scene_change)
            rep = self._fetch_vision_repeat(ts)
            obs.vision_fp12 = fp12
            obs.vision_scene_change = sc
            obs.vision_repeat = rep
            flags["vision"] = bool(fp12 is not None or sc is not None or rep is not None)
            flags["vision_degraded"] = fp12 is None
            self.state.vision_enabled = bool(flags["vision"])

        # Audio bridge
        if _NMR_USE_AUDIO:
            with _BRIDGE_LOCK:
                obs.audio_rms = _to_float(_AUDIO_BRIDGE.last_rms)
                obs.audio_pitch = _to_float(_AUDIO_BRIDGE.last_pitch)
            flags["audio"] = bool(obs.audio_rms is not None or obs.audio_pitch is not None)
            flags["audio_degraded"] = not bool(flags["audio"])
            self.state.audio_enabled = bool(flags["audio"])

        # Curriculum via TTL cache
        if _NMR_USE_CURRICULUM:
            cst = self._fetch_curriculum_cached(ts)
            prog = cst.get("progress") if isinstance(cst, dict) else None
            if isinstance(prog, dict):
                obs.curriculum_acc = _to_float(prog.get("acc"))
                lvl = prog.get("difficulty")
                if lvl is None:
                    lvl = cst.get("stage")
                obs.curriculum_level = _to_float(lvl)
            else:
                obs.curriculum_acc = _to_float(cst.get("acc")) if isinstance(cst, dict) else None
                obs.curriculum_level = _to_float(cst.get("level") or cst.get("stage")) if isinstance(cst, dict) else None
            flags["curriculum"] = bool(obs.curriculum_acc is not None or obs.curriculum_level is not None)

        # Empathy direct in-memory
        if _NMR_USE_EMPATHY and empathy is not None:
            try:
                est = empathy.get_state() if hasattr(empathy, "get_state") else {}  # type: ignore[attr-defined]
                if isinstance(est, dict):
                    obs.empathy_valence = _to_float(est.get("valence"))
                    obs.empathy_arousal = _to_float(est.get("arousal"))
                    flags["empathy"] = bool(
                        obs.empathy_valence is not None or obs.empathy_arousal is not None
                    )
            except Exception as e:
                log_suppressed(
                    LOG,
                    key="core.nmr_lite.empathy.pass.1",
                    msg="Suppressed empathy.get_state() exception",
                    exc=e,
                    level=logging.WARNING,
                    interval_s=120,
                )

        # Runtime snap rate: self-tracked
        if _NMR_USE_RUNTIME:
            elapsed = max(1e-6, float(_NMR_AGG_WINDOW_SEC))
            obs.runtime_snap_rate = _clip(float(self.state.snap_counter) / elapsed, 0.0, 1000.0)
            flags["runtime"] = True

        self.state.last_observation = obs
        return obs

    def derive_working_vector(self, obs: ObservationState) -> List[float]:
        vec: List[float] = []

        if _NMR_USE_VISION:
            if obs.vision_fp12 is not None:
                vv = [
                    _clip(_to_float(x, 0.0) or 0.0, 0.0, 1.0)
                    for x in list(obs.vision_fp12)[:12]
                ]
                if len(vv) < 12:
                    vv.extend([0.0] * (12 - len(vv)))
                vec.extend(vv)
            vec.append(_normalize_repeat(obs.vision_repeat, default=0.0))
            vec.append(_normalize_zero_one(obs.vision_scene_change, default=0.0))

        if _NMR_USE_AUDIO:
            vec.append(_normalize_zero_one(obs.audio_rms, default=0.0))
            vec.append(_normalize_pitch_hz(obs.audio_pitch))

        if _NMR_USE_CURRICULUM:
            vec.append(_normalize_zero_one(obs.curriculum_acc, default=0.0))
            vec.append(_normalize_curriculum_level(obs.curriculum_level))

        if _NMR_USE_EMPATHY:
            # map valence -1..1 -> 0..1 for working vector
            v = obs.empathy_valence
            if v is None:
                vec.append(0.5)
            else:
                vec.append(_clip((float(v) + 1.0) / 2.0, 0.0, 1.0))
            vec.append(_normalize_zero_one(obs.empathy_arousal, default=0.0))

        if _NMR_USE_RUNTIME:
            # saturate conservatively; snap rate is mostly salience density.
            rate = 0.0 if obs.runtime_snap_rate is None else min(float(obs.runtime_snap_rate), 10.0)
            vec.append(_clip(rate / 10.0, 0.0, 1.0))

        return vec

    # ------------------------------------------------------------------
    # Core update path
    # ------------------------------------------------------------------
    def _compute_confidence(self, obs: ObservationState) -> float:
        conf = 1.0
        if _NMR_USE_VISION and obs.vision_fp12 is None:
            conf -= 0.35
        if _NMR_USE_AUDIO and obs.audio_rms is None and obs.audio_pitch is None:
            conf -= 0.10
        if _NMR_USE_CURRICULUM and obs.curriculum_acc is None and obs.curriculum_level is None:
            conf -= 0.05
        if _NMR_USE_EMPATHY and obs.empathy_valence is None and obs.empathy_arousal is None:
            conf -= 0.05
        return _clip(conf, 0.2, 1.0)

    def _compute_priority_score(self, obs: ObservationState, pe_ema: float, confidence: float) -> float:
        scene = _normalize_zero_one(obs.vision_scene_change, default=0.0)
        repeat = _normalize_repeat(obs.vision_repeat, default=0.0)
        base = 0.55 * _clip(pe_ema, 0.0, 1.0) + 0.25 * scene + 0.20 * (1.0 - repeat)
        if confidence < 1.0:
            # keep effect, but dampen if core modalities are degraded
            base *= (0.7 + 0.3 * confidence)
        return _clip(base, 0.0, 1.0)

    def _compute_binding_hints(self, obs: ObservationState, out: NMROutput) -> None:
        repeat = _normalize_repeat(obs.vision_repeat, default=0.0)
        scene = _normalize_zero_one(obs.vision_scene_change, default=0.0)
        cur_acc = obs.curriculum_acc

        curriculum_ok = True
        if cur_acc is not None:
            curriculum_ok = float(cur_acc) < float(_NMR_BINDING_CURRICULUM_ACC_MAX)

        binding = (
            out.nmr_pe_ema > float(_NMR_BINDING_PE_THRESHOLD)
            and scene > float(_NMR_BINDING_SCENE_THRESHOLD)
            and repeat < float(_NMR_BINDING_REPEAT_MAX)
            and curriculum_ok
        )
        out.binding_hint = 1 if binding else 0
        out.binding_hint_score = _clip(
            0.5 * out.nmr_pe_ema
            + 0.3 * scene
            + 0.2 * (1.0 - repeat),
            0.0,
            1.0,
        )

        # Crossmodal hint: requires binding_hint plus audio deltas.
        rms_delta = 0.0
        pitch_delta = 0.0
        if obs.audio_rms is not None and self.state.prev_audio_rms is not None:
            rms_delta = abs(float(obs.audio_rms) - float(self.state.prev_audio_rms))
        if obs.audio_pitch is not None and self.state.prev_audio_pitch is not None:
            pitch_delta = abs(float(obs.audio_pitch) - float(self.state.prev_audio_pitch))
        out.crossmodal_hint = 1 if (
            out.binding_hint == 1
            and (
                rms_delta > float(_NMR_AUDIO_RMS_DELTA_THRESHOLD)
                or pitch_delta > float(_NMR_AUDIO_PITCH_DELTA_THRESHOLD)
            )
        ) else 0

    def tick(self, now_ts: Optional[float] = None) -> NMROutput:
        if not self.enabled:
            with self._lock:
                out = NMROutput(ts=_safe_now(now_ts), confidence=0.0)
                self.state.last_output = out
                return out

        ts = _safe_now(now_ts)
        with self._lock:
            obs = self.collect_observation(ts)
            vec = self.derive_working_vector(obs)
            prev_vec = self.state.prev_working_vector or []

            pe = _mean_abs_delta(prev_vec, vec) if prev_vec else 0.0
            self.state.current_pe = pe
            self.state.pe_ema = _clip(
                (1.0 - self._ema_alpha) * float(self.state.pe_ema) + self._ema_alpha * float(pe),
                0.0,
                1.0,
            )
            self.state.prev_working_vector = vec
            self.state.agg_window_values.append(float(pe))

            confidence = self._compute_confidence(obs)
            out = NMROutput(
                nmr_pe=float(pe),
                nmr_pe_ema=float(self.state.pe_ema),
                nmr_surprise_event=1 if self.state.pe_ema > float(_NMR_SURPRISE_THRESHOLD) and obs.vision_fp12 is not None else 0,
                confidence=float(confidence),
                ts=float(ts),
                modality_flags=dict(obs.modality_flags),
            )
            out.nmr_priority_score = self._compute_priority_score(obs, out.nmr_pe_ema, out.confidence)
            self._compute_binding_hints(obs, out)

            # house-keeping for audio deltas and local aggregation windows
            self.state.prev_audio_rms = obs.audio_rms
            self.state.prev_audio_pitch = obs.audio_pitch
            if self.state.agg_window_start_ts <= 0.0:
                self.state.agg_window_start_ts = ts
            if (ts - self.state.agg_window_start_ts) >= float(_NMR_AGG_WINDOW_SEC):
                # reset snap counter at aggregation boundary; rate already reflected in obs
                self.state.snap_counter = 0
                self.state.agg_window_values.clear()
                self.state.agg_window_start_ts = ts

            self.state.last_output = out
            self._last_tick_ts = ts
            return out

    # ------------------------------------------------------------------
    # Slow persistence
    # ------------------------------------------------------------------
    def maybe_persist(self, now_ts: Optional[float] = None) -> bool:
        if not self.enabled or not _NMR_PERSIST:
            return False
        ts = _safe_now(now_ts)
        with self._lock:
            if self.state.last_output.ts <= 0.0:
                return False
            if self.state.last_persist_ts > 0.0 and (ts - self.state.last_persist_ts) < float(_NMR_PERSIST_WINDOW_SEC):
                return False
            out = self.state.last_output
            payload = {
                f"{_NMR_METRIC_PREFIX}:pe": out.nmr_pe,
                f"{_NMR_METRIC_PREFIX}:pe_ema": out.nmr_pe_ema,
                f"{_NMR_METRIC_PREFIX}:surprise": float(out.nmr_surprise_event),
                f"{_NMR_METRIC_PREFIX}:priority": out.nmr_priority_score,
                f"{_NMR_METRIC_PREFIX}:binding_hint": float(out.binding_hint),
                f"{_NMR_METRIC_PREFIX}:binding_hint_score": out.binding_hint_score,
                f"{_NMR_METRIC_PREFIX}:crossmodal_hint": float(out.crossmodal_hint),
                f"{_NMR_METRIC_PREFIX}:confidence": out.confidence,
            }
            try:
                rowids: List[int] = []
                for key, val in payload.items():
                    rowids.append(_persist_metric_row(str(key), float(val), int(ts)))
                if len(rowids) != len(payload) or any(int(r) <= 0 for r in rowids):
                    raise RuntimeError(f"NMR metric persistence incomplete: rowids={rowids!r}")
                self.state.last_persist_ts = ts
                return True
            except Exception as e:
                log_suppressed(
                    LOG,
                    key="core.nmr_lite.persist.error",
                    msg="NMR-Lite metric persistence failed",
                    exc=e,
                    level=logging.WARNING,
                    interval_s=120,
                )
                return False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def get_output(self) -> Dict[str, Any]:
        with self._lock:
            return self.state.last_output.as_dict()

    def get_observation_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self.state.last_observation.as_dict()

    def debug_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self.enabled),
                "config": {
                    "tick_sec": float(_NMR_TICK_SEC),
                    "agg_window_sec": float(_NMR_AGG_WINDOW_SEC),
                    "persist_window_sec": float(_NMR_PERSIST_WINDOW_SEC),
                    "curriculum_cache_ttl_sec": float(_NMR_CURRICULUM_CACHE_TTL_SEC),
                    "repeat_cache_ttl_sec": float(_NMR_REPEAT_CACHE_TTL_SEC),
                },
                "observation": self.state.last_observation.as_dict(),
                "output": self.state.last_output.as_dict(),
                "has_prev_vector": bool(self.state.prev_working_vector),
                "vision_enabled": bool(self.state.vision_enabled),
                "audio_enabled": bool(self.state.audio_enabled),
                "last_persist_ts": float(self.state.last_persist_ts),
                "snap_counter": int(self.state.snap_counter),
            }


# -----------------------------------------------------------------------------
# Module singleton and convenience API
# -----------------------------------------------------------------------------
_NMR_SINGLETON: Optional[NMRLite] = None
_SINGLETON_LOCK = threading.RLock()


def get_nmr() -> NMRLite:
    global _NMR_SINGLETON
    with _SINGLETON_LOCK:
        if _NMR_SINGLETON is None:
            _NMR_SINGLETON = NMRLite()
        return _NMR_SINGLETON


def tick(now_ts: Optional[float] = None) -> Dict[str, Any]:
    return get_nmr().tick(now_ts).as_dict()


def maybe_persist(now_ts: Optional[float] = None) -> bool:
    return get_nmr().maybe_persist(now_ts)


def increment_snap_counter(n: int = 1) -> None:
    get_nmr().increment_snap_counter(n)


def get_output() -> Dict[str, Any]:
    return get_nmr().get_output()


def get_observation_snapshot() -> Dict[str, Any]:
    return get_nmr().get_observation_snapshot()


def debug_state() -> Dict[str, Any]:
    return get_nmr().debug_state()


def reset_runtime_state() -> None:
    get_nmr().reset()


# -----------------------------------------------------------------------------
# Tiny smoke-test CLI
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # small self-test with synthetic signals; intentionally no DB dependency.
    update_vision_signal(fp12=[0.1] * 12, scene_change=0.0, repeat=0.2)
    update_audio_signal(rms=0.02, pitch=800.0)
    increment_snap_counter(3)
    out1 = tick()
    time.sleep(min(_NMR_TICK_SEC, 0.25))
    update_vision_signal(fp12=[0.6] * 12, scene_change=0.9, repeat=0.1)
    update_audio_signal(rms=0.8, pitch=1200.0)
    increment_snap_counter(2)
    out2 = tick()
    print("NMR first:", out1)
    print("NMR second:", out2)
    print("Debug:", debug_state())

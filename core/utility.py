#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/utility.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Version: v3.7.3+structured-plasticity-utility-v1.1
# Stand:   2026-05-17
# Autor:   ORÓMA / ChatGPT Patch-Gate
#
# Zweck
# ─────
#   Domänenfreie Utility-Schicht für ORÓMAs Structured-Plasticity-Architektur.
#
#   Diese Datei beschreibt NICHT, wie PTZ, Vision, Audio, Sprache, Games oder
#   Crossmodal-Binding ihren lokalen Nutzen berechnen. Sie stellt ausschließlich
#   eine stabile, normalisierte und langfristig versionsfeste Schnittstelle bereit,
#   über die beliebige ORÓMA-Bahnen ein lokales Nützlichkeitssignal emittieren
#   können.
#
#   Rollenmodell im ORÓMA-Kern:
#
#     snap.py       → Was wurde erlebt?              (Gedächtnis-Atom)
#     reward.py     → Wo wird es gespeichert?        (technischer Logger)
#     utility.py    → War es lokal nützlich?         (kognitives Signal)
#     collector     → Wie wird es berechnet?         (domänenspezifisch)
#     dream_worker  → Was bedeutet es langfristig?   (Verdichtung)
#     forgetting    → Was bleibt, was geht?          (Pruning/Kompression)
#
# Architektur-Invarianten
# ───────────────────────
#   1. Keine Domänenlogik:
#      - Keine PTZ-spezifischen Schwellen.
#      - Keine Vision-/Audio-/Crossmodal-Regeln.
#      - Keine Policy-, Dream- oder Pruning-Entscheidung.
#
#   2. Keine Abhängigkeit nach oben:
#      - utility.py kennt keine Collector, keinen Motor-Worker und keinen
#        DreamWorker.
#      - utility.py nutzt ausschließlich die darunterliegende Reward-Schicht
#        als Persistenz-/Logging-Pfad.
#
#   3. Normalisierte Werte:
#      - value wird immer auf [-1.0, +1.0] geklemmt.
#      - confidence wird immer auf [0.0, 1.0] geklemmt.
#      - Persistierter reward = value * confidence.
#      - Rohwerte bleiben im raw-Payload sichtbar erhalten.
#
#   4. Zukunftsoffene Bahnen:
#      - bahn ist absichtlich keine harte Whitelist.
#      - Neue Bahnen wie "language", "memory", "energy", "social" oder
#        spätere Subsysteme dürfen UtilitySignal nutzen, ohne diese Datei zu
#        ändern.
#      - Das empfohlene source-Namensschema ist Dokumentationskonvention, kein
#        Code-Zwang: "bahn/bereich/signal_name", z. B.
#        "ptz_motor/follow_gain" oder "vision/feature_stability".
#
#   5. Kontext bleibt frei, aber sicher:
#      - context ist ein freies dict für domänenspezifische Zusatzdaten.
#      - Nicht JSON-serialisierbare Werte werden sicher repräsentiert.
#      - Sanitization darf keinen Crash auslösen.
#      - Jede Sanitization erhöht utility_context_sanitized, damit schmutzige
#        Collector-Kontexte sichtbar bleiben.
#
# Persistenzmodell
# ────────────────
#   emit(UtilitySignal) schreibt über core.reward.RewardLogger nach rewards_log.
#   RewardLogger wird lazy und pro Prozess wiederverwendet, damit langsame
#   Collector-Loops wie auch spätere höherfrequente Bahnen nicht bei jedem
#   Signal unnötig eine neue Logger-Instanz erzeugen.
#   Es wird KEIN neues DB-Schema eingeführt.
#
#   rewards_log.reward:
#       value * confidence
#
#   rewards_log.raw:
#       {
#         "utility": true,
#         "utility_version": 1,
#         "source": "...",
#         "bahn": "...",
#         "value": ...,
#         "confidence": ...,
#         "weighted_value": ...,
#         "context": {...},
#         "ts": ...
#       }
#
# DBWriter-/SQLite-Disziplin
# ─────────────────────────
#   Die DBWriter-Regeln werden NICHT in dieser Datei nachgebaut. utility.py ruft
#   RewardLogger auf; RewardLogger ist in ORÓMA für DBWriter-kompatibles Logging
#   und den Verzicht auf lokale Fallback-Writes bei aktivem DBWriter zuständig.
#
# Öffentliche API
# ───────────────
#   UtilitySignal        Dataclass für ein lokales Nützlichkeitssignal.
#   emit(signal)         Validiert, normalisiert und loggt ein Signal.
#   get_counters()       Gibt eine Kopie interner Diagnosezähler zurück.
#
# Nicht-Ziele
# ───────────
#   - Keine Aggregation.
#   - Keine Pruning-Entscheidung.
#   - Keine Policy-Entscheidung.
#   - Keine Interpretation des context-Inhalts.
#   - Keine domänenspezifischen Score-Formeln.
#
# Stabilitätsnotiz
# ───────────────
#   RewardLogger wird absichtlich NICHT auf Modulebene importiert. Der Lazy-
#   Singleton in _get_reward_logger() verhindert Import-Zyklen beim Import von
#   core.utility, vermeidet aber zugleich unnötige Neuinstanzen bei vielen
#   emit()-Aufrufen innerhalb eines Prozesses.
#
# Nutzung
# ───────
#   from core.utility import UtilitySignal, emit
#
#   emit(UtilitySignal(
#       source="ptz_motor/follow_gain",
#       bahn="ptz",
#       value=0.34,
#       confidence=0.82,
#       context={"before_dist": 0.42, "after_dist": 0.22},
#       tag="ptz_motor.collector",
#   ))
# =============================================================================

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Mapping, Optional, Tuple

UTILITY_VERSION = 1


@dataclass
class UtilitySignal:
    """Domänenfreies lokales Nützlichkeitssignal.

    Attributes:
        source: Lesbarer Signalname. Konvention: "bahn/bereich/signal_name".
        bahn:  Freier Bahn-Identifier, z. B. "ptz", "vision", "audio".
        value: Normalisierter Nutzen im Bereich [-1.0, +1.0]. Wird geklemmt.
        confidence: Sicherheit im Bereich [0.0, 1.0]. Wird geklemmt.
        context: Freier domänenspezifischer Kontext. Wird JSON-sicher gemacht.
        ts: Epoch-Zeitpunkt in Sekunden. Wenn None, wird time.time() genutzt.
        episode_id: Optionaler integer-kompatibler Episode-Verweis.
        step: Optionaler Schrittindex für rewards_log.step.
        tag: Optionaler Diagnose-/Subsystem-Tag.
    """

    source: str
    bahn: str
    value: float
    confidence: float = 1.0
    context: Dict[str, Any] = field(default_factory=dict)
    ts: Optional[float] = None
    episode_id: Optional[int] = None
    step: int = 0
    tag: Optional[str] = None


_COUNTER_LOCK = RLock()
_COUNTERS: Dict[str, int] = {
    "utility_emit_total": 0,
    "utility_emit_ok": 0,
    "utility_emit_failed": 0,
    "utility_invalid_signal": 0,
    "utility_context_sanitized": 0,
    "utility_value_clamped": 0,
    "utility_confidence_clamped": 0,
}

# Lazy prozessweiter RewardLogger.
#
# Warum hier und nicht in jedem emit()?
#   RewardLogger.__init__() führt ensure_schema() aus. Das ist korrekt, aber bei
#   vielen Utility-Signalen unnötige Wiederholung. Ein kleiner, thread-sicherer
#   Lazy-Singleton hält utility.py weiterhin importzyklusarm und domänenfrei,
#   vermeidet aber spätere Performance-Schulden in höherfrequenten Collectors.
#
# Warum keine Modul-Level-Initialisierung?
#   core.utility soll ohne Nebenwirkungen importierbar bleiben. core.reward wird
#   daher erst beim ersten echten emit() geladen.
_REWARD_LOGGER_LOCK = RLock()
_REWARD_LOGGER: Optional[Any] = None


def get_counters() -> Dict[str, int]:
    """Gibt eine Kopie der internen Utility-Diagnosezähler zurück.

    Die Counter sind bewusst domänenfrei. Sie messen nur, ob die Utility-Schicht
    selbst valide, geklemmte und JSON-sichere Signale verarbeitet.
    """

    with _COUNTER_LOCK:
        return dict(_COUNTERS)


def emit(signal: UtilitySignal) -> bool:
    """Validiert, normalisiert und persistiert ein UtilitySignal.

    Returns:
        True, wenn das Signal angenommen und erfolgreich an RewardLogger
        übergeben wurde. Im DBWriter-Modus gilt auch die erfolgreiche Übergabe an
        den Writer als Erfolg.

        False, wenn Validierung oder Logging sichtbar fehlschlägt. Diese Funktion
        wirft absichtlich keine domänenspezifischen Exceptions in Hot-Loops.
    """

    _inc("utility_emit_total")

    normalized = _normalize_signal(signal)
    if normalized is None:
        _inc("utility_invalid_signal")
        _inc("utility_emit_failed")
        return False

    source, bahn, value, confidence, context, ts, episode_id, step, tag = normalized
    weighted_value = float(value * confidence)

    raw = {
        "utility": True,
        "utility_version": UTILITY_VERSION,
        "source": source,
        "bahn": bahn,
        "value": value,
        "confidence": confidence,
        "weighted_value": weighted_value,
        "context": context,
        "ts": ts,
    }

    try:
        logger = _get_reward_logger()
        rid = logger.log(
            source=source,
            step=step,
            reward=weighted_value,
            raw=raw,
            episode_id=episode_id,
            tag=tag,
            ts=int(ts),
        )
        if int(rid) >= 0:
            _inc("utility_emit_ok")
            return True

        print(f"[utility] RewardLogger returned failure for source={source!r} bahn={bahn!r} rid={rid!r}")
        _inc("utility_emit_failed")
        return False
    except Exception as exc:
        print(f"[utility] emit() failed for source={source!r} bahn={bahn!r}: {exc}")
        _inc("utility_emit_failed")
        return False


def _inc(name: str, amount: int = 1) -> None:
    with _COUNTER_LOCK:
        _COUNTERS[name] = int(_COUNTERS.get(name, 0)) + int(amount)


def _get_reward_logger() -> Any:
    """Gibt die prozessweite RewardLogger-Instanz zurück.

    Der Import bleibt lazy innerhalb dieser Funktion, damit core.utility ohne
    direkte reward.py-Nebenwirkungen importiert werden kann. Die Instanz wird
    anschließend pro Prozess wiederverwendet. Das ist für langsame Collector
    unkritisch, vermeidet aber bewusst spätere Performance-Schulden, falls
    höherfrequente Bahnen Utility-Signale emittieren.
    """

    global _REWARD_LOGGER

    logger = _REWARD_LOGGER
    if logger is not None:
        return logger

    with _REWARD_LOGGER_LOCK:
        logger = _REWARD_LOGGER
        if logger is not None:
            return logger

        from core.reward import RewardLogger  # lazy: vermeidet Import-Seiteneffekte bei Modulimport

        logger = RewardLogger()
        _REWARD_LOGGER = logger
        return logger


def _normalize_signal(
    signal: UtilitySignal,
) -> Optional[Tuple[str, str, float, float, Dict[str, Any], float, Optional[int], int, Optional[str]]]:
    if not isinstance(signal, UtilitySignal):
        print(f"[utility] invalid signal type: {type(signal).__name__}")
        return None

    source = _safe_identifier(signal.source, field_name="source")
    bahn = _safe_identifier(signal.bahn, field_name="bahn")
    if source is None or bahn is None:
        return None

    value, value_ok, value_clamped = _coerce_clamped_float(signal.value, -1.0, 1.0, "value")
    confidence, confidence_ok, confidence_clamped = _coerce_clamped_float(
        signal.confidence,
        0.0,
        1.0,
        "confidence",
    )
    if not value_ok or not confidence_ok:
        return None
    if value_clamped:
        _inc("utility_value_clamped")
    if confidence_clamped:
        _inc("utility_confidence_clamped")

    if not isinstance(signal.context, Mapping):
        print(f"[utility] invalid context for source={source!r}: expected dict/mapping, got {type(signal.context).__name__}")
        return None

    context, sanitized = _json_safe_dict(signal.context)
    if sanitized:
        _inc("utility_context_sanitized")

    ts_value = signal.ts if signal.ts is not None else time.time()
    ts, ts_ok, _ = _coerce_clamped_float(ts_value, 0.0, 4102444800.0, "ts")  # bis 2100-01-01 UTC
    if not ts_ok:
        return None

    try:
        episode_id = int(signal.episode_id) if signal.episode_id is not None else None
    except Exception:
        print(f"[utility] invalid episode_id for source={source!r}: {signal.episode_id!r}")
        return None

    try:
        step = int(signal.step)
    except Exception:
        print(f"[utility] invalid step for source={source!r}: {signal.step!r}")
        return None

    tag = None if signal.tag is None else str(signal.tag).strip()
    if tag == "":
        tag = None

    return source, bahn, float(value), float(confidence), context, float(ts), episode_id, step, tag


def _safe_identifier(value: Any, *, field_name: str) -> Optional[str]:
    if value is None:
        print(f"[utility] invalid {field_name}: None")
        return None
    text = str(value).strip()
    if not text:
        print(f"[utility] invalid {field_name}: empty")
        return None
    return text


def _coerce_clamped_float(value: Any, low: float, high: float, field_name: str) -> Tuple[float, bool, bool]:
    try:
        number = float(value)
    except Exception:
        print(f"[utility] invalid {field_name}: not numeric ({value!r})")
        return 0.0, False, False

    if not math.isfinite(number):
        print(f"[utility] invalid {field_name}: not finite ({value!r})")
        return 0.0, False, False

    clamped = max(float(low), min(float(high), float(number)))
    return clamped, True, (clamped != number)


def _json_safe_dict(context: Mapping[str, Any]) -> Tuple[Dict[str, Any], bool]:
    safe: Dict[str, Any] = {}
    changed = False
    seen: set[int] = set()

    for key, value in context.items():
        safe_key = str(key)
        if safe_key != key:
            changed = True
        safe_value, value_changed = _json_safe(value, seen=seen, depth=0)
        if value_changed:
            changed = True
        safe[safe_key] = safe_value

    # Harte Endkontrolle: raw muss json.dumps zuverlässig überstehen.
    try:
        json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        changed = True
        safe = {"_sanitized_repr": repr(safe)}

    return safe, changed


def _json_safe(value: Any, *, seen: set[int], depth: int) -> Tuple[Any, bool]:
    if depth > 16:
        return "<max-depth>", True

    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return repr(value), True
        return value, False

    obj_id = id(value)
    if isinstance(value, (dict, list, tuple, set)):
        if obj_id in seen:
            return "<recursive>", True
        seen.add(obj_id)
        try:
            if isinstance(value, dict):
                out: Dict[str, Any] = {}
                changed = False
                for key, item in value.items():
                    safe_key = str(key)
                    if safe_key != key:
                        changed = True
                    safe_item, item_changed = _json_safe(item, seen=seen, depth=depth + 1)
                    if item_changed:
                        changed = True
                    out[safe_key] = safe_item
                return out, changed

            if isinstance(value, (list, tuple, set)):
                out_list = []
                changed = not isinstance(value, list)
                for item in value:
                    safe_item, item_changed = _json_safe(item, seen=seen, depth=depth + 1)
                    if item_changed:
                        changed = True
                    out_list.append(safe_item)
                return out_list, changed
        finally:
            seen.discard(obj_id)

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace"), True
        except Exception:
            return repr(value), True

    # Letzte sichere Darstellung für beliebige Objekte, numpy-Werte, Pfade,
    # Exceptions usw. Keine Domänenannahme, kein Crash.
    return repr(value), True

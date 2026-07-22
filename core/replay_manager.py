#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/replay_manager.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     ReplayManager – SnapChain Playback + Event-Bus + DB-Logging + optionales Replay-Lernen
# Version:   v3.7.4+replay-safe-policy-gate-v1
# Stand:     2026-07-09
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert den zentralen Replay-Laufzeit-Controller für ORÓMA:
#   - Abspielen (Replay) von SnapChains zur Diagnose, Forschung und (optional) Lernen
#   - Thread-basierter Player (start/pause/resume/stop) mit robustem Statusmodell
#   - Strukturierte Replay-Events für das Gesamtsystem (AgentLoop Event-Bus)
#   - Persistentes DB-Logging von Replay-Läufen (Start/Step/End), um Abläufe
#     auch ohne Logfiles nachträglich nachvollziehen zu können
#
# WARUM REPLAY WICHTIG IST (ORÓMA-KONZEPT)
# ───────────────────────────────────────
# ORÓMA speichert Wahrnehmung/Lernen als SnapChains (episodische Sequenzen).
# Replay ist das „Offline-Playback“ dieser Episoden:
#   - Qualität/Reward-Rekonstruktion
#   - Debugging: „Was ist wirklich passiert?“
#   - Dream/Research: Auswertung/Kompression/Policy-Updates außerhalb der Live-Perzeption
#
# KERNFUNKTIONEN
# ──────────────
# 1) Playback Control:
#    - start(chain_id, speed=1.0)
#    - pause()
#    - resume()
#    - stop()
#
# 2) Runtime Status:
#    - status() liefert ein JSON-freundliches Dict:
#        running, paused, chain_id, step, total_steps, speed,
#        progress_pct, elapsed_time, started_at, last_event, error, log_id
#    Ziel: UI / API kann ohne direkte Thread-Inspection stabil abfragen.
#
# 3) Event-Emission (AgentLoop Integration):
#    - replay_start  (Meta: chain_id, total_steps, speed, ts, log_id)
#    - replay_step   (Meta: chain_id, step, total_steps, speed, ts, log_id)
#    - replay_end    (Meta: chain_id, steps, speed, ts, ok/error, log_id)
#    Diese Events werden über core.agent_loop.inject_event(...) eingespeist,
#    falls agent_loop importierbar ist.
#
# 4) DB-Logging (Replay Audit Trail):
#    - Wenn core.sql_manager verfügbar und Funktionen existieren, wird ein
#      Replay-Lauf in einer Replay-Log-Tabelle protokolliert (Start/Updates/Ende).
#      Das ermöglicht:
#        • UI/Tools: „letzte Replays“, Laufdauer, Schrittanzahl, Fehler
#        • Diagnose ohne Zugriff auf filesystem logs
#    Implementiert via (best effort, feature-detect):
#      - sql_manager.insert_replay_log(...)
#      - sql_manager.update_replay_log(log_id, ...)
#
# 5) Optional: Replay-Lernen (Policy-Update während Playback)
#    - OROMA_REPLAY_LEARN=1 markiert nur den Replay-Lernwunsch.
#    - Produktive policy_rules-Writes sind zusätzlich hart gegatet über:
#        OROMA_REPLAY_POLICY_UPSERT_ENABLE=1
#        OROMA_REPLAY_POLICY_CONFIRM=REPLAY_POLICY_WRITE_REVIEWED
#        OROMA_REPLAY_POLICY_NS_ALLOWLIST=<csv>
#    - Writes laufen ausschließlich über core.db_writer_client / DBWriter.
#    - Es gibt keinen lokalen SQLite-Fallback und kein blindes sql_manager.policy_upsert.
#    - Fehlt eine belastbare Aktion/Credit-Zuordnung, bleibt Replay read-only.
#    Hinweis: Replay ist mögliche Evidenz, aber kein Direct-Step-Credit-Ersatz.
#
# ROBUSTHEIT / SELBSTHEILUNG
# ─────────────────────────
# SnapChains können in der Praxis „inkonsistent“ sein (Slim-Backup, ältere Formate,
# fehlende Felder, blob vs. file export). Dieses Modul ist defensiv:
#   - load_chain() kann fehlen → Dummy-Betrieb mit klarer Fehlermeldung
#   - Chain-Format (dict vs SnapChain) wird tolerant behandelt
#   - Extractor versucht aus Steps Vektoren/Meta zu gewinnen, ohne hart zu crashen
# Ziel: Replay soll selbst bei Teildefekten wenigstens „sichtbar“ machen, was geht.
#
# QUELLEN / SPEICHERORTE
# ─────────────────────
# - Hauptquelle: SnapChains aus DB (oroma.db → snapchains Tabelle; blob enthält chain JSON)
# - Fallback (wenn blob/loader es so vorsieht): JSON-Dateien unter data/snapchains/
#   (z. B. <source_id>.json oder <source_id>_*.json)
#
# SLEEP / SPEED / PERFORMANCE
# ──────────────────────────
# - speed skaliert das Playback-Tempo; minimale Schlafzeit wird begrenzt:
#     OROMA_REPLAY_MIN_SLEEP (Default z. B. 0.05s)
# - Ziel: UI bleibt responsiv, CPU nicht 100%, und Events bleiben “lesbar”.
#
# WICHTIGE ENV-VARIABLEN (AUSZUG)
# ──────────────────────────────
# Basis/Logging:
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_REPLAY_LOGLEVEL=INFO|DEBUG|WARNING|ERROR
#
# Lernen (optional / sicher gegatet):
#   OROMA_REPLAY_LEARN=1|0                       # Lernwunsch / Status sichtbar
#   OROMA_REPLAY_NS=replay                       # Ziel-Namespace
#   OROMA_REPLAY_POLICY_UPSERT_ENABLE=1|0        # produktiver Write-Gate, Default AUS
#   OROMA_REPLAY_POLICY_CONFIRM=REPLAY_POLICY_WRITE_REVIEWED
#   OROMA_REPLAY_POLICY_NS_ALLOWLIST=replay      # CSV-Allowlist
#   OROMA_REPLAY_POLICY_ALLOW_GENERIC_ACTION=0   # action=step bleibt Default blockiert
#   OROMA_REPLAY_POLICY_DBW_TIMEOUT_MS=5000
#   OROMA_REPLAY_DBW_PING_TIMEOUT_MS=300
#   OROMA_REPLAY_DBW_PING_CACHE_SEC=5.0
#
# Replay Timing:
#   OROMA_REPLAY_MIN_SLEEP=0.05
#   OROMA_REPLAY_EXPORT_EMPTY=1|0
#
# THREADING / STATE-INVARIANTEN
# ────────────────────────────
# - Es existiert genau ein Replay-Thread pro Prozess (Singleton-Player).
# - stop() setzt Flags und wartet kontrolliert; Status bleibt konsistent.
# - pause/resume arbeiten über Event-Flags, nicht über busy loops.
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
#   start(chain_id: int, speed: float = 1.0) -> bool
#   pause()  -> bool
#   resume() -> bool
#   stop()   -> bool
#   status() -> Dict[str, Any]
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import sys
import time
import json
import sqlite3
import hashlib
import threading
import logging
import argparse
from typing import Any, Dict, Optional, Tuple, List

from core import log_guard
logger = logging.getLogger(__name__)
# -----------------------------------------------------------------------------
# Basis / Pfade / Logging
# -----------------------------------------------------------------------------
BASE = os.environ.get("OROMA_BASE") or "/opt/ai/oroma"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG = logging.getLogger("oroma.replay")
if not LOG.handlers:
    level = os.environ.get("OROMA_REPLAY_LOGLEVEL", "INFO").upper()
    LOG.setLevel(getattr(logging, level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [Replay] %(message)s")
    fh = logging.FileHandler(os.path.join(LOG_DIR, "replay_manager.log"), encoding="utf-8")
    sh = logging.StreamHandler()
    fh.setFormatter(fmt); sh.setFormatter(fmt)
    LOG.addHandler(fh); LOG.addHandler(sh)

# -----------------------------------------------------------------------------
# Optionale Imports (robust)
# -----------------------------------------------------------------------------
try:
    from core.snapchain import SnapChain, load_chain, save_chain  # type: ignore
except Exception:
    SnapChain = None   # type: ignore
    load_chain = None  # type: ignore
    save_chain = None  # type: ignore
    LOG.warning("SnapChain-Core nicht verfügbar – nur Dummy-Betrieb möglich.")

try:
    from core import sql_manager  # type: ignore
    _HAS_SQL = True
except Exception:
    sql_manager = None  # type: ignore
    _HAS_SQL = False
    LOG.info("sql_manager nicht verfügbar – Replay-Logging/Legacy-Helper bleiben eingeschränkt.")

try:
    from core import db_writer_client  # type: ignore
    _HAS_DBW = True
except Exception:
    db_writer_client = None  # type: ignore
    _HAS_DBW = False
    LOG.info("db_writer_client nicht verfügbar – Replay-Policy-Writes bleiben gesperrt.")

try:
    # DB-backed SnapChain loader (snapchains table)
    from core import model_registry  # type: ignore
    _HAS_REGISTRY = True
except Exception:
    model_registry = None  # type: ignore
    _HAS_REGISTRY = False
    LOG.info("model_registry nicht verfügbar – DB-SnapChains können nicht geladen werden.")

try:
    from core import agent_loop  # type: ignore
    _HAS_AGENT = True
except Exception:
    _HAS_AGENT = False
    LOG.info("agent_loop nicht verfügbar – inject_event wird übersprungen.")

try:
    from core import nmr_lite  # type: ignore
    _HAS_NMR = True
except Exception:
    nmr_lite = None  # type: ignore
    _HAS_NMR = False
    LOG.info("nmr_lite nicht verfügbar – Replay-NMR-Integration wird übersprungen.")

# -----------------------------------------------------------------------------
# Interner Zustand
# -----------------------------------------------------------------------------
_state: Dict[str, Any] = {
    "running": False,
    "paused": False,
    "chain_id": None,
    "step": 0,
    "total_steps": 0,
    "speed": 1.0,
    "last_event": None,
    "started_at": None,
    "progress_pct": 0.0,
    "elapsed_time": 0.0,
    "error": None,
    "log_id": None,
}

_REPLAY_LEARNING_LOCK = threading.Lock()
_REPLAY_LEARNING_STATS: Dict[str, Any] = {
    "events_seen": 0,
    "events_requested": 0,
    "writes_attempted": 0,
    "writes_ok": 0,
    "writes_blocked": 0,
    "writes_skipped": 0,
    "errors": 0,
    "last_decision": "init",
    "last_reason": None,
    "last_namespace": None,
    "last_action": None,
    "last_ts": None,
    "last_error": None,
}
_REPLAY_DBW_PING_LOCK = threading.Lock()
_REPLAY_DBW_PING_CACHE: Dict[str, Any] = {"ts": 0.0, "ok": False}

_thread: Optional[threading.Thread] = None
_stop_flag = threading.Event()
_pause_flag = threading.Event()

# -----------------------------------------------------------------------------
# Optionale Replay↔NMR-Lite-Integration (konservativ / bounded)
# -----------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return float(default)


_REPLAY_NMR_ENABLE = _env_bool("OROMA_REPLAY_NMR_ENABLE", True)
_REPLAY_NMR_REWARD_SCALE = max(0.0, _env_float("OROMA_REPLAY_NMR_REWARD_SCALE", 0.15))
_REPLAY_NMR_STRUCT_BONUS = max(0.0, _env_float("OROMA_REPLAY_NMR_STRUCT_BONUS", 0.10))
_REPLAY_NMR_CROSSMODAL_BONUS = max(0.0, _env_float("OROMA_REPLAY_NMR_CROSSMODAL_BONUS", 0.05))


_REPLAY_POLICY_CONFIRM_TOKEN = "REPLAY_POLICY_WRITE_REVIEWED"


def _env_csv_set(name: str, default: str = "") -> set:
    raw = os.getenv(name, default) or ""
    return {p.strip() for p in str(raw).split(",") if p.strip()}


def _replay_learn_requested() -> bool:
    return _env_bool("OROMA_REPLAY_LEARN", False)


def _replay_dbwriter_configured() -> bool:
    """True, wenn Replay lokale Writes nicht nutzen darf, weil DBWriter konfiguriert ist."""
    try:
        if not _HAS_DBW or db_writer_client is None:
            return False
        if str(os.getenv("OROMA_DBW_ENABLE", "0")).strip().lower() not in ("1", "true", "yes", "on"):
            return False
        return bool(getattr(db_writer_client, "enabled", lambda: False)())
    except Exception:
        return False


def _replay_dbwriter_enabled() -> bool:
    """True nur, wenn der zentrale DBWriter für Writes konfiguriert und erreichbar ist."""
    try:
        if not _replay_dbwriter_configured() or db_writer_client is None:
            return False
        if not hasattr(db_writer_client, "ping"):
            return True
        now = time.time()
        ttl = max(0.25, _env_float("OROMA_REPLAY_DBW_PING_CACHE_SEC", 5.0))
        with _REPLAY_DBW_PING_LOCK:
            if (now - float(_REPLAY_DBW_PING_CACHE.get("ts", 0.0) or 0.0)) <= ttl:
                return bool(_REPLAY_DBW_PING_CACHE.get("ok", False))
        timeout_ms = max(100, int(float(os.getenv("OROMA_REPLAY_DBW_PING_TIMEOUT_MS", "300") or "300")))
        ok = bool(db_writer_client.ping(timeout_ms=timeout_ms))  # type: ignore[attr-defined]
        with _REPLAY_DBW_PING_LOCK:
            _REPLAY_DBW_PING_CACHE["ts"] = now
            _REPLAY_DBW_PING_CACHE["ok"] = ok
        return ok
    except Exception:
        try:
            with _REPLAY_DBW_PING_LOCK:
                _REPLAY_DBW_PING_CACHE["ts"] = time.time()
                _REPLAY_DBW_PING_CACHE["ok"] = False
        except Exception:
            pass
        return False


def _replay_policy_allow_generic_action() -> bool:
    return _env_bool("OROMA_REPLAY_POLICY_ALLOW_GENERIC_ACTION", False)


def _record_learning_decision(decision: str, reason: Optional[str] = None,
                              namespace: Optional[str] = None,
                              action: Optional[str] = None,
                              error: Optional[str] = None) -> None:
    """Aktualisiert sichtbare Replay-Learning-Zähler ohne harte Abhängigkeit zur UI."""
    try:
        with _REPLAY_LEARNING_LOCK:
            _REPLAY_LEARNING_STATS["last_decision"] = str(decision)
            _REPLAY_LEARNING_STATS["last_reason"] = reason
            _REPLAY_LEARNING_STATS["last_namespace"] = namespace
            _REPLAY_LEARNING_STATS["last_action"] = action
            _REPLAY_LEARNING_STATS["last_ts"] = int(time.time())
            if error is not None:
                _REPLAY_LEARNING_STATS["last_error"] = str(error)
            if decision == "seen":
                _REPLAY_LEARNING_STATS["events_seen"] = int(_REPLAY_LEARNING_STATS.get("events_seen", 0)) + 1
            elif decision == "requested":
                _REPLAY_LEARNING_STATS["events_requested"] = int(_REPLAY_LEARNING_STATS.get("events_requested", 0)) + 1
            elif decision == "attempted":
                _REPLAY_LEARNING_STATS["writes_attempted"] = int(_REPLAY_LEARNING_STATS.get("writes_attempted", 0)) + 1
            elif decision == "written":
                _REPLAY_LEARNING_STATS["writes_ok"] = int(_REPLAY_LEARNING_STATS.get("writes_ok", 0)) + 1
            elif decision == "blocked":
                _REPLAY_LEARNING_STATS["writes_blocked"] = int(_REPLAY_LEARNING_STATS.get("writes_blocked", 0)) + 1
            elif decision == "skipped":
                _REPLAY_LEARNING_STATS["writes_skipped"] = int(_REPLAY_LEARNING_STATS.get("writes_skipped", 0)) + 1
            elif decision == "error":
                _REPLAY_LEARNING_STATS["errors"] = int(_REPLAY_LEARNING_STATS.get("errors", 0)) + 1
    except Exception:
        # Status-Zähler dürfen Replay niemals destabilisieren.
        return


def _replay_policy_gate_snapshot(namespace: Optional[str] = None,
                                 action: Optional[str] = None) -> Dict[str, Any]:
    """Liefert den aktuell wirksamen Safe-Gate-Zustand für Replay→Policy.

    Der Gate ist absichtlich strenger als `OROMA_REPLAY_LEARN`: Replay-Lernen
    darf im Betrieb als Wunsch/Analysepfad aktiv sein, produktive policy_rules-
    Writes bleiben aber gesperrt, bis Review, Confirm-Token, Namespace-Allowlist,
    DBWriter und belastbare Aktion gleichzeitig passen.
    """
    ns = str(namespace or os.getenv("OROMA_REPLAY_NS", "replay") or "replay")
    action_txt = str(action or "")
    allowlist = _env_csv_set("OROMA_REPLAY_POLICY_NS_ALLOWLIST", "replay")
    learn_requested = _replay_learn_requested()
    upsert_enabled = _env_bool("OROMA_REPLAY_POLICY_UPSERT_ENABLE", False)
    confirm_value = str(os.getenv("OROMA_REPLAY_POLICY_CONFIRM", "") or "").strip()
    confirm_ok = confirm_value == _REPLAY_POLICY_CONFIRM_TOKEN
    dbwriter_ok = _replay_dbwriter_enabled()
    namespace_allowed = bool(ns and ns in allowlist)
    generic_allowed = _replay_policy_allow_generic_action()
    action_safe = bool(action_txt and (generic_allowed or action_txt != "step"))

    reason = None
    if not learn_requested:
        reason = "replay_learn_disabled"
    elif not upsert_enabled:
        reason = "policy_upsert_gate_disabled"
    elif not confirm_ok:
        reason = "policy_confirm_missing_or_invalid"
    elif not namespace_allowed:
        reason = "namespace_not_allowlisted"
    elif not dbwriter_ok:
        reason = "dbwriter_not_enabled_or_unavailable"
    elif not action_safe:
        reason = "missing_or_generic_action"

    return {
        "learn_requested": bool(learn_requested),
        "policy_upsert_enable": bool(upsert_enabled),
        "confirm_required": _REPLAY_POLICY_CONFIRM_TOKEN,
        "confirm_ok": bool(confirm_ok),
        "namespace": ns,
        "namespace_allowlist": sorted(allowlist),
        "namespace_allowed": bool(namespace_allowed),
        "dbwriter_configured": bool(_replay_dbwriter_configured()),
        "dbwriter_enabled": bool(dbwriter_ok),
        "generic_action_allowed": bool(generic_allowed),
        "action_safe": bool(action_safe),
        "write_ready": reason is None,
        "blocked_reason": reason,
        "mode": "dbwriter_policy_upsert" if reason is None else "read_only_or_blocked",
    }


def _replay_learning_status() -> Dict[str, Any]:
    """Statusobjekt für UI/API: macht Replay-Learning-Gates und Blockaden sichtbar."""
    gate = _replay_policy_gate_snapshot()
    with _REPLAY_LEARNING_LOCK:
        stats = dict(_REPLAY_LEARNING_STATS)
    out = dict(gate)
    out.update(stats)
    return out


def _dbwriter_policy_upsert(namespace: str, state_hash: str, action: str,
                            outcome: str, centroid: Optional[List[float]]) -> None:
    """Schreibt einen einzelnen Replay→Policy-Upsert ausschließlich über DBWriter.

    Kein lokaler SQLite-Fallback: Wenn DBWriter nicht aktiv ist, bleibt Replay
    read-only. Replay-Evidenz ist generischer als Direct-Step-Credit und darf
    deshalb nur durch diesen expliziten, überprüfbaren Pfad in `policy_rules`.
    """
    if not _replay_dbwriter_enabled() or db_writer_client is None:
        raise RuntimeError("Replay policy upsert requires active DBWriter")

    now = int(time.time())
    outcome_txt = str(outcome).strip().lower()
    pos_inc = 1 if outcome_txt in ("pos", "positive", "win", "success", "+1") else 0
    neg_inc = 1 if outcome_txt in ("neg", "negative", "loss", "fail", "-1") else 0
    draw_inc = 0 if (pos_inc or neg_inc) else 1
    q_value = float(pos_inc - neg_inc)
    cen_json = json.dumps(centroid, separators=(",", ":")) if centroid else None

    db_writer_client.exec_write(
        """
        INSERT INTO policy_rules
            (namespace,state_hash,action,n,pos,neg,draw,q,last_ts,centroid)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(namespace,state_hash,action) DO UPDATE SET
            n       = policy_rules.n + 1,
            pos     = policy_rules.pos + ?,
            neg     = policy_rules.neg + ?,
            draw    = policy_rules.draw + ?,
            q       = CAST((policy_rules.pos + ?) - (policy_rules.neg + ?) AS REAL) / (policy_rules.n + 1),
            last_ts = ?,
            centroid= COALESCE(?, policy_rules.centroid)
        """,
        [
            str(namespace), str(state_hash), str(action),
            1, pos_inc, neg_inc, draw_inc, q_value, now, cen_json,
            pos_inc, neg_inc, draw_inc, pos_inc, neg_inc, now, cen_json,
        ],
        tag="replay_manager.policy_rules.safe_upsert",
        priority="low",
        timeout_ms=max(500, int(float(os.getenv("OROMA_REPLAY_POLICY_DBW_TIMEOUT_MS", "5000") or "5000"))),
        db="oroma",
    )


def _insert_replay_metric(key: str, value: float) -> None:
    """Schreibt Replay-Metriken DBWriter-kompatibel; lokaler Fallback nur ohne DBWriter."""
    if _replay_dbwriter_configured():
        if not _replay_dbwriter_enabled() or db_writer_client is None:
            raise RuntimeError("DBWriter configured but unavailable for replay metric")
        db_writer_client.exec_lastrowid(
            "INSERT INTO metrics (key, ts, value) VALUES (?, ?, ?)",
            [str(key), int(time.time()), float(value)],
            tag="replay_manager.metric",
            priority="low",
            timeout_ms=max(500, int(float(os.getenv("OROMA_REPLAY_POLICY_DBW_TIMEOUT_MS", "5000") or "5000"))),
            db="oroma",
        )
        return
    if _HAS_SQL and sql_manager is not None and hasattr(sql_manager, "insert_metric"):
        sql_manager.insert_metric(str(key), float(value))  # type: ignore[attr-defined]


def _get_nmr_output() -> Dict[str, Any]:
    """Liest best effort den aktuellen NMR-Lite-Output.

    Diese Funktion darf Replay nie destabilisieren. Bei fehlendem Modul oder
    Laufzeitfehlern wird einfach ein leeres Dict geliefert.
    """
    if not (_HAS_NMR and _REPLAY_NMR_ENABLE and nmr_lite is not None):
        return {}
    try:
        if hasattr(nmr_lite, "get_output"):
            out = nmr_lite.get_output()  # type: ignore[attr-defined]
            return dict(out) if isinstance(out, dict) else {}
    except Exception as e:
        LOG.debug("NMR-Output nicht verfügbar: %s", e)
    return {}


def _bounded_nmr_replay_bonus(nmr_out: Optional[Dict[str, Any]]) -> float:
    """Leitet einen kleinen, konservativen Replay-Lernbonus aus NMR-Lite ab.

    Ziel: Replay kann NMR-Signale bereits konsumieren, ohne bestehende Reward-
    Pfade zu überfahren. Der Bonus bleibt absichtlich klein und bounded.
    """
    if not nmr_out:
        return 0.0
    try:
        priority = float(nmr_out.get("nmr_priority_score", 0.0) or 0.0)
        binding_score = float(nmr_out.get("binding_hint_score", 0.0) or 0.0)
        binding_hint = 1.0 if int(nmr_out.get("binding_hint", 0) or 0) == 1 else 0.0
        crossmodal_hint = 1.0 if int(nmr_out.get("crossmodal_hint", 0) or 0) == 1 else 0.0
        confidence = float(nmr_out.get("confidence", 1.0) or 1.0)
    except Exception:
        return 0.0

    base = (_REPLAY_NMR_REWARD_SCALE * max(0.0, min(1.0, priority)))
    struct = (_REPLAY_NMR_STRUCT_BONUS * max(0.0, min(1.0, binding_score)) * binding_hint)
    cross = (_REPLAY_NMR_CROSSMODAL_BONUS * crossmodal_hint)
    bonus = (base + struct + cross) * max(0.0, min(1.0, confidence))
    return max(0.0, min(0.30, float(bonus)))


def _nmr_event_meta(nmr_out: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not nmr_out:
        return {}
    out: Dict[str, Any] = {}
    try:
        out["nmr_pe_ema"] = round(float(nmr_out.get("nmr_pe_ema", 0.0) or 0.0), 6)
        out["nmr_priority_score"] = round(float(nmr_out.get("nmr_priority_score", 0.0) or 0.0), 6)
        out["nmr_surprise_event"] = int(nmr_out.get("nmr_surprise_event", 0) or 0)
        out["nmr_binding_hint"] = int(nmr_out.get("binding_hint", 0) or 0)
        out["nmr_binding_hint_score"] = round(float(nmr_out.get("binding_hint_score", 0.0) or 0.0), 6)
        out["nmr_crossmodal_hint"] = int(nmr_out.get("crossmodal_hint", 0) or 0)
        out["nmr_confidence"] = round(float(nmr_out.get("confidence", 1.0) or 1.0), 6)
        out["nmr_replay_bonus"] = round(_bounded_nmr_replay_bonus(nmr_out), 6)
    except Exception:
        return {}
    return out

# =============================================================================
# Utilities – Event->State Extraktion
# =============================================================================

def _extract_base_vector(ev: Any) -> Tuple[Optional[List[float]], Optional[List[float]], Dict[str, Any]]:
    centroid: Optional[List[float]] = None
    vec: Optional[List[float]] = None
    meta: Dict[str, Any] = {}

    if isinstance(ev, dict):
        meta = dict(ev.get("metadata") or {})
        if isinstance(ev.get("centroid"), list) and ev["centroid"]:
            try:
                centroid = [float(x) for x in ev["centroid"]]
            except Exception:
                centroid = None
        if vec is None and isinstance(ev.get("patterns"), list) and ev["patterns"]:
            v0 = ev["patterns"][0]
            if isinstance(v0, list) and v0:
                try:
                    vec = [float(x) for x in v0]
                except Exception:
                    vec = None
        if vec is None and isinstance(ev.get("snaps"), list) and ev["snaps"]:
            s0 = ev["snaps"][0]
            if isinstance(s0, dict) and isinstance(s0.get("features"), list):
                try:
                    vec = [float(x) for x in s0["features"]]
                except Exception:
                    vec = None
        return (vec or centroid, centroid, meta)

    try:
        m = getattr(ev, "metadata", None)
        if isinstance(m, dict):
            meta = dict(m)
        c = getattr(ev, "centroid", None)
        if isinstance(c, list) and c:
            try:
                centroid = [float(x) for x in c]
            except Exception:
                centroid = None
        plist = list(getattr(ev, "patterns", []) or [])
        if plist and isinstance(plist[0], (list, tuple)) and plist[0]:
            try:
                vec = [float(x) for x in plist[0]]
            except Exception:
                vec = None
        if vec is None:
            snaps = list(getattr(ev, "snaps", []) or getattr(ev, "events", []) or [])
            if snaps and hasattr(snaps[0], "features"):
                f = list(getattr(snaps[0], "features") or [])
                if f:
                    try:
                        vec = [float(x) for x in f]
                    except Exception:
                        vec = None
    except Exception as e:
        log_guard.log_suppressed(logger, key="replay_manager.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    return (vec or centroid, centroid, meta)

# =============================================================================
# Lernen aus Events (Policy-Upsert)
# =============================================================================

def _learn_from_event(ev: Any, nmr_out: Optional[Dict[str, Any]] = None) -> None:
    _record_learning_decision("seen")
    if not _replay_learn_requested():
        return

    ns = str(os.environ.get("OROMA_REPLAY_NS", "replay") or "replay")
    _record_learning_decision("requested", namespace=ns)

    try:
        base, centroid, meta = _extract_base_vector(ev)
        if not base:
            _record_learning_decision("skipped", "missing_state_vector", namespace=ns)
            return

        s = json.dumps([round(float(x), 3) for x in base], separators=(",", ":"))
        sh = hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

        meta = dict(meta or {})
        action = str(meta.get("policy_action") or meta.get("action") or "step")

        if "reward" in meta:
            try:
                reward = float(meta["reward"])
            except Exception:
                reward = 0.0
        elif meta.get("outcome") in ("win", "success"):
            reward = 1.0
        elif meta.get("outcome") in ("loss", "fail"):
            reward = -1.0
        else:
            reward = 0.0

        nmr_bonus = _bounded_nmr_replay_bonus(nmr_out)
        reward += nmr_bonus
        outcome = "pos" if reward > 0 else ("neg" if reward < 0 else "draw")

        gate = _replay_policy_gate_snapshot(namespace=ns, action=action)
        if not gate.get("write_ready"):
            reason = str(gate.get("blocked_reason") or "blocked")
            _record_learning_decision("blocked", reason, namespace=ns, action=action)
            log_guard.log_suppressed(
                logger,
                key=f"replay_manager.policy_write_blocked.{reason}",
                msg=f"Replay→Policy write blocked: {reason}",
                exc=None,
                level=logging.WARNING if reason not in ("missing_or_generic_action",) else logging.DEBUG,
                interval_s=300,
            )
            if nmr_bonus > 0.0:
                try:
                    _insert_replay_metric("replay_nmr_bonus_blocked", float(nmr_bonus))
                except Exception as e:
                    log_guard.log_suppressed(logger, key="replay_manager.nmr_metric_blocked", msg="Replay NMR metric write skipped", exc=e, level=logging.DEBUG, interval_s=300)
            return

        _record_learning_decision("attempted", namespace=ns, action=action)
        _dbwriter_policy_upsert(ns, sh, action, outcome=outcome, centroid=centroid)
        _record_learning_decision("written", namespace=ns, action=action)

        if nmr_bonus > 0.0:
            try:
                _insert_replay_metric("replay_nmr_bonus", float(nmr_bonus))
            except Exception as e:
                log_guard.log_suppressed(logger, key="replay_manager.nmr_metric", msg="Replay NMR metric write skipped", exc=e, level=logging.DEBUG, interval_s=300)
    except Exception as e:
        _record_learning_decision("error", "learn_hook_exception", namespace=ns, error=str(e))
        log_guard.log_suppressed(logger, key="replay_manager.learn_hook", msg="Replay LearnHook Fehler", exc=e, level=logging.WARNING, interval_s=300)

# =============================================================================
# SnapChain-Load (selbstheilend)
# =============================================================================

def _ensure_snapchain(chain_id: Any):
    if not (SnapChain and callable(load_chain)):  # type: ignore
        LOG.error("⚠️ SnapChain-Core nicht verfügbar.")
        return None
    try:
        # --- Bundle-Syntax ---------------------------------------------------
        # Replay ist "universell": Viele Origins (z.B. vision/token, audio/token,
        # calc/result) werden als Einzel-Snap (len=1) gespeichert. Für echtes
        # "Stream-Replay" kann man mit `bundle:<origin>:<n>` mehrere der jüngsten
        # Snaps zu einer temporären SnapChain bündeln (chronologisch), um den
        # Replay-Pfad (Hooks/Curiosity/ObjectGraph usw.) mit einer Sequenz zu füttern.
        #
        # Beispiele:
        #   bundle:vision/token:120      -> letzte 120 vision/token Snaps als Sequenz
        #   bundle:audio/token:240       -> letzte 240 audio/token Snaps als Sequenz
        #
        # Hinweis: Dieses Bundle ist flüchtig (nicht in DB geschrieben).
        def _bundle_from_db(spec: str):
            try:
                parts = spec.split(":", 2)
                if len(parts) != 3:
                    return None
                _pfx, origin, n_str = parts
                n = int(n_str) if n_str.isdigit() else 0
                if n <= 0:
                    return None
                db_path = getattr(model_registry, "DB_PATH", None)
                if not db_path:
                    return None

                con = None
                try:
                    con = sqlite3.connect(db_path, timeout=5)
                    cur = con.cursor()
                    cur.execute(
                        "SELECT id, ts, origin, status, CAST(blob AS TEXT) "
                        "FROM snapchains WHERE origin=? ORDER BY ts DESC LIMIT ?",
                        (origin, n),
                    )
                    rows = cur.fetchall()
                finally:
                    try:
                        if con:
                            con.close()
                    except Exception:
                        pass

                if not rows:
                    return None

                events = []
                for (_id, ts, _origin, _status, blob_txt) in rows:
                    try:
                        evt = json.loads(blob_txt) if blob_txt else None
                    except Exception:
                        evt = None
                    if isinstance(evt, dict):
                        evt.setdefault("ts", ts)
                        evt.setdefault("origin", _origin)
                        evt.setdefault("status", _status)
                        events.append(evt)

                if not events:
                    return None

                events.reverse()  # chronologisch
                sc = SnapChain()  # type: ignore
                for e in events:
                    try:
                        sc.add(e)  # type: ignore
                    except Exception:
                        try:
                            sc.patterns.append(e)  # type: ignore
                        except Exception:
                            pass

                try:
                    sc.id = f"{spec}"  # type: ignore
                except Exception:
                    pass

                LOG.info(
                    "✅ Bundle-SnapChain erzeugt (%s) len=%s",
                    spec,
                    len(getattr(sc, "patterns", []) or []),
                )
                return sc
            except Exception as e:
                LOG.error("Bundle-SnapChain fehlgeschlagen (%s): %s", spec, e)
                return None

        if isinstance(chain_id, str) and chain_id.startswith("bundle:"):
            sc = _bundle_from_db(chain_id)
            if sc is not None:
                return sc

        # 1) Legacy filesystem loader (backwards compatible)
        sc = load_chain(chain_id)  # type: ignore
        if isinstance(sc, dict) and "events" in sc:
            pats = [p for p in sc.get("events", []) if p]
            meta = dict(sc.get("metadata", {}) or {})
            sc = SnapChain(patterns=pats, metadata=meta)  # type: ignore
            LOG.info("♻️ SnapChain(dict→obj) konvertiert (id=%s, len=%d)", chain_id, len(pats))
            return sc
        if sc and getattr(sc, "patterns", None):
            LOG.info("✅ SnapChain geladen (id=%s, len=%d)", chain_id, len(getattr(sc, "patterns", [])))
            return sc

        # 2) DB-backed loader (snapchains table) — primary in some deployments
        if _HAS_REGISTRY and model_registry is not None and hasattr(model_registry, "load_chain"):
            try:
                sc_db = model_registry.load_chain(chain_id)  # type: ignore
                if isinstance(sc_db, dict) and "events" in sc_db:
                    pats = [p for p in sc_db.get("events", []) if p]
                    meta = dict(sc_db.get("metadata", {}) or {})
                    sc_db = SnapChain(patterns=pats, metadata=meta)  # type: ignore
                    LOG.info("♻️ SnapChain(DB dict→obj) konvertiert (id=%s, len=%d)", chain_id, len(pats))
                    return sc_db
                if sc_db and getattr(sc_db, "patterns", None):
                    LOG.info("✅ SnapChain aus DB geladen (id=%s, len=%d)", chain_id, len(getattr(sc_db, "patterns", [])))
                    return sc_db
            except Exception as e_db:
                LOG.debug("DB-load_chain fehlgeschlagen (%s): %s", chain_id, e_db)

        LOG.warning("⚠️ SnapChain leer/unbekannt – Dummy erzeugt")
        return SnapChain(patterns=[], metadata={"origin": "replay-fallback"})  # type: ignore
    except Exception as e:
        # If FS loader threw, one more chance via DB.
        if _HAS_REGISTRY and model_registry is not None and hasattr(model_registry, "load_chain"):
            try:
                sc_db = model_registry.load_chain(chain_id)  # type: ignore
                if isinstance(sc_db, dict) and "events" in sc_db:
                    pats = [p for p in sc_db.get("events", []) if p]
                    meta = dict(sc_db.get("metadata", {}) or {})
                    sc_db = SnapChain(patterns=pats, metadata=meta)  # type: ignore
                    LOG.info("♻️ SnapChain(DB dict→obj) konvertiert (id=%s, len=%d)", chain_id, len(pats))
                    return sc_db
                if sc_db and getattr(sc_db, "patterns", None):
                    LOG.info("✅ SnapChain aus DB geladen (id=%s, len=%d)", chain_id, len(getattr(sc_db, "patterns", [])))
                    return sc_db
            except Exception as e_db2:
                LOG.debug("DB-load_chain fehlgeschlagen (%s): %s", chain_id, e_db2)

        LOG.error("❌ Fehler beim Laden der SnapChain #%s (FS/DB): %s", chain_id, e)
        try:
            dummy = SnapChain(patterns=[], metadata={"origin": "replay-recovery"})  # type: ignore
            LOG.info("🔁 Dummy-SnapChain erzeugt (%s)", chain_id)
            return dummy
        except Exception as e2:
            LOG.error("❌ SnapChain-Recovery fehlgeschlagen: %s", e2)
            return None

# =============================================================================
# Worker
# =============================================================================

def _inject(kind: str, chain_id: Any, step: int, total: int, speed: float,
            status: Optional[str] = None, info: Optional[str] = None,
            nmr_out: Optional[Dict[str, Any]] = None) -> None:
    """Hilfsfunktion: sicheres Injizieren eines Replay-Events in den AgentLoop."""
    if not _HAS_AGENT or not hasattr(agent_loop, "inject_event"):
        return
    try:
        ev = {
            "kind": kind,
            "chain_id": chain_id,
            "step": int(step),
            "total": int(total),
            "speed": float(speed),
            "ts": int(time.time()),
        }
        if _state.get("log_id"):
            ev["log_id"] = _state["log_id"]
        if status is not None:
            ev["status"] = status
        if info is not None:
            ev["info"] = info
        if nmr_out:
            ev.update(_nmr_event_meta(nmr_out))
        agent_loop.inject_event(ev)  # type: ignore[attr-defined]
    except Exception as e:
        LOG.debug("inject_event fehlgeschlagen (%s): %s", kind, e)

def _worker(chain_id: Any, speed: float):
    global _state
    sc = _ensure_snapchain(chain_id)
    if sc is None:
        _state.update({"running": False, "error": "SnapChain konnte nicht geladen werden"})
        return

    events = getattr(sc, "patterns", [])
    total = len(events)
    LOG.info("▶️ Replay gestartet: chain=%s steps=%d speed=%.2f", chain_id, total, speed)

    _state.update({
        "running": True,
        "paused": False,
        "chain_id": chain_id,
        "step": 0,
        "total_steps": total,
        "speed": speed,
        "started_at": time.time(),
        "error": None,
        "log_id": None,
    })

    _stop_flag.clear()
    _pause_flag.clear()

    # --- DB: Start-Log (wenn möglich)
    if _HAS_SQL and hasattr(sql_manager, "insert_replay_log"):
        try:
            _state["log_id"] = sql_manager.insert_replay_log(chain_id=str(chain_id),
                                                             ts_run=int(time.time()),
                                                             steps=0, speed=float(speed),
                                                             status="run")  # type: ignore[attr-defined]
        except Exception:
            _state["log_id"] = None

    # --- Event: Start
    start_nmr_out = _get_nmr_output()
    _state["last_nmr"] = start_nmr_out
    _inject("replay_start", chain_id, 0, total, speed, nmr_out=start_nmr_out)

    try:
        try:
            min_sleep = float(os.environ.get("OROMA_REPLAY_MIN_SLEEP", "0.05"))
        except Exception:
            min_sleep = 0.05

        for idx, ev in enumerate(events):
            if _stop_flag.is_set():
                LOG.info("⏹️ Replay gestoppt (user interrupt)")
                break

            while _pause_flag.is_set() and not _stop_flag.is_set():
                time.sleep(0.1)

            _state["step"] = idx + 1
            _state["last_event"] = ev
            _state["elapsed_time"] = time.time() - (_state["started_at"] or time.time())
            _state["progress_pct"] = round(100.0 * _state["step"] / max(1, total), 2)

            nmr_out = _get_nmr_output()
            _state["last_nmr"] = nmr_out

            # leichte Telemetrie: DBWriter-kompatibel, kein lokaler Write-Bypass bei OROMA_DBW_ENABLE=1
            try:
                _insert_replay_metric("replay_step", float(idx + 1))
                if nmr_out:
                    if int(nmr_out.get("nmr_surprise_event", 0) or 0) == 1:
                        _insert_replay_metric("replay_nmr_surprise_step", 1.0)
                    if int(nmr_out.get("binding_hint", 0) or 0) == 1:
                        _insert_replay_metric("replay_nmr_binding_hint_step", 1.0)
                    if int(nmr_out.get("crossmodal_hint", 0) or 0) == 1:
                        _insert_replay_metric("replay_nmr_crossmodal_hint_step", 1.0)
            except Exception as e:
                log_guard.log_suppressed(logger, key="replay_manager.metric_write", msg="Replay metric write skipped", exc=e, level=logging.DEBUG, interval_s=300)

            # lokales Lernen (per ENV schaltbar)
            _learn_from_event(ev, nmr_out=nmr_out)

            # DB: Step-Update (wenn möglich)
            if _HAS_SQL and _state.get("log_id") and hasattr(sql_manager, "update_replay_log"):
                try:
                    sql_manager.update_replay_log(int(_state["log_id"]), steps=int(_state["step"]), speed=float(speed))  # type: ignore[attr-defined]
                except Exception as e:
                    log_guard.log_suppressed(logger, key="replay_manager.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

            # Event: Step
            _inject("replay_step", chain_id, int(_state["step"]), total, speed, nmr_out=nmr_out)

            time.sleep(max(min_sleep, 1.0 / max(0.1, speed)))

        # Export nur, wenn es Events gab – außer OROMA_REPLAY_EXPORT_EMPTY=1
        export_empty = os.environ.get("OROMA_REPLAY_EXPORT_EMPTY", "0").strip().lower() in ("1", "true", "yes")
        if (total > 0 or export_empty) and save_chain and isinstance(sc, SnapChain):  # type: ignore
            try:
                fn = save_chain(f"replay_{int(time.time())}", sc)  # type: ignore
                LOG.info("💾 ReplayChain exportiert → %s", fn)
            except Exception as e:
                LOG.warning("Replay-Export fehlgeschlagen: %s", e)

        LOG.info("🏁 Replay abgeschlossen (%s Steps)", total)
        final_status = "done"

    except Exception as e:
        LOG.error("❌ Replay-Fehler: %s", e)
        _state["error"] = str(e)
        final_status = "error"

    finally:
        # DB: Ende-Log
        if _HAS_SQL and _state.get("log_id") and hasattr(sql_manager, "update_replay_log"):
            try:
                info = None
                if final_status == "error":
                    info = _state.get("error")
                sql_manager.update_replay_log(int(_state["log_id"]),
                                              steps=int(_state.get("step", 0)),
                                              speed=float(_state.get("speed", speed)),
                                              status=final_status,
                                              info=info)  # type: ignore[attr-defined]
            except Exception as e:
                log_guard.log_suppressed(logger, key="replay_manager.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        # Event: Ende
        end_nmr_out = _get_nmr_output() or dict(_state.get("last_nmr") or {})
        _inject("replay_end", chain_id, int(_state.get("step", 0)), total, float(_state.get("speed", speed)),
                status=final_status, info=_state.get("error"), nmr_out=end_nmr_out)

        _state.update({"running": False, "paused": False, "chain_id": None, "log_id": None})

# =============================================================================
# Öffentliche API
# =============================================================================

def start(chain_id: Any, speed: float = 1.0) -> None:
    """Startet ein Replay (threaded)."""
    global _thread
    if _state["running"]:
        raise RuntimeError("Replay läuft bereits")
    _thread = threading.Thread(target=_worker, args=(chain_id, float(speed)), daemon=True)
    _thread.start()

def pause() -> None:
    """Pausiert ein laufendes Replay."""
    if not _state["running"]:
        return
    _pause_flag.set()
    _state["paused"] = True
    LOG.info("⏸️ Replay pausiert")

def resume() -> None:
    """Setzt ein pausiertes Replay fort."""
    if not _state["running"]:
        return
    _pause_flag.clear()
    _state["paused"] = False
    LOG.info("▶️ Replay fortgesetzt")

def stop() -> None:
    """Stoppt ein laufendes Replay (soft)."""
    if not _state["running"]:
        return
    _stop_flag.set()
    if _thread:
        try:
            _thread.join(timeout=3.0)
        except Exception as e:
            log_guard.log_suppressed(logger, key="replay_manager.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    _state.update({"running": False, "paused": False, "chain_id": None})
    LOG.info("⏹️ Replay gestoppt")

def status() -> Dict[str, Any]:
    """Liefert eine Momentaufnahme des Replay-Status inklusive Learning-Gate."""
    out = dict(_state)
    out["replay_learning"] = _replay_learning_status()
    return out

# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA SnapChain Replay Manager")
    ap.add_argument("--chain", type=str, help="Chain-ID (ohne .json)")
    ap.add_argument("--speed", type=float, default=1.0, help="Speed-Faktor (Events pro Sekunde)")
    args = ap.parse_args()

    cid = args.chain or "selftest"

    # Mini-Selftest-Chain erzeugen, falls gewünscht
    if cid == "selftest" and SnapChain and save_chain:
        try:
            ch = SnapChain(metadata={"origin": "replay_cli"})
            ch.add_text("cli selftest")
            save_chain("selftest", ch)  # type: ignore
        except Exception as e:
            log_guard.log_suppressed(logger, key="replay_manager.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    start(cid, speed=float(args.speed))
    while status().get("running"):
        time.sleep(0.2)
    return 0 if not status().get("error") else 1

if __name__ == "__main__":
    sys.exit(_cli())
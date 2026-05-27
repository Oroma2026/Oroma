#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/replay_manager.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     ReplayManager – SnapChain Playback + Event-Bus + DB-Logging + optionales Replay-Lernen
# Version:   v3.7.3
# Stand:     2026-01-10
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
#    - ENV-gesteuert: OROMA_REPLAY_LEARN=1
#    - nutzt sql_manager.policy_upsert() (falls vorhanden) um policy_rules zu aktualisieren
#    - Namespace/Parameter werden über ENV konfiguriert
#    Hinweis: Lernlogik ist bewusst optional und darf Replay nicht destabilisieren.
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
# Lernen (optional):
#   OROMA_REPLAY_LEARN=1|0
#   OROMA_REPLAY_NS=replay
#   OROMA_REPLAY_ALPHA=0.1
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
    LOG.info("sql_manager nicht verfügbar – Replay lernt nicht in policy_rules.")

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
    if not _HAS_SQL:
        return
    if os.getenv("OROMA_REPLAY_LEARN", "0").strip().lower() not in ("1", "true", "yes"):
        return
    if not hasattr(sql_manager, "policy_upsert"):
        LOG.debug("policy_upsert nicht verfügbar – Lernen übersprungen.")
        return

    try:
        ns = os.environ.get("OROMA_REPLAY_NS", "replay")
        base, centroid, meta = _extract_base_vector(ev)
        if not base:
            return

        s = json.dumps([round(float(x), 3) for x in base], separators=(",", ":"))
        sh = hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

        action = str((meta or {}).get("action", "step"))

        if "reward" in (meta or {}):
            try:
                reward = float(meta["reward"])
            except Exception:
                reward = 0.0
        elif (meta or {}).get("outcome") in ("win", "success"):
            reward = 1.0
        elif (meta or {}).get("outcome") in ("loss", "fail"):
            reward = -1.0
        else:
            reward = 0.0

        nmr_bonus = _bounded_nmr_replay_bonus(nmr_out)
        reward += nmr_bonus

        outcome = "pos" if reward > 0 else ("neg" if reward < 0 else "draw")
        centroid_txt = json.dumps(centroid, separators=(",", ":")) if centroid else None

        sql_manager.policy_upsert(ns, sh, action, outcome=outcome, reward=reward, centroid=centroid_txt)  # type: ignore

        if hasattr(sql_manager, "insert_metric") and nmr_bonus > 0.0:
            try:
                sql_manager.insert_metric("replay_nmr_bonus", float(nmr_bonus))  # type: ignore[attr-defined]
            except Exception as e:
                log_guard.log_suppressed(logger, key="replay_manager.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    except Exception as e:
        LOG.debug("LearnHook Fehler: %s", e)

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

            # leichte Telemetrie
            if _HAS_SQL and hasattr(sql_manager, "insert_metric"):
                try:
                    sql_manager.insert_metric("replay_step", float(idx + 1))  # type: ignore[attr-defined]
                    if nmr_out:
                        if int(nmr_out.get("nmr_surprise_event", 0) or 0) == 1:
                            sql_manager.insert_metric("replay_nmr_surprise_step", 1.0)  # type: ignore[attr-defined]
                        if int(nmr_out.get("binding_hint", 0) or 0) == 1:
                            sql_manager.insert_metric("replay_nmr_binding_hint_step", 1.0)  # type: ignore[attr-defined]
                        if int(nmr_out.get("crossmodal_hint", 0) or 0) == 1:
                            sql_manager.insert_metric("replay_nmr_crossmodal_hint_step", 1.0)  # type: ignore[attr-defined]
                except Exception as e:
                    log_guard.log_suppressed(logger, key="replay_manager.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

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
    """Liefert eine Momentaufnahme des Replay-Status."""
    return dict(_state)

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
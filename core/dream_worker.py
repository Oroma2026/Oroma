#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/dream_worker.py
# Projekt:   ORÓMA (Dream/Rest Phase · Offline Learning · Headless)
# Modul:     DreamWorker – periodischer Offline-Lernlauf
#            (Replay/Mutation/LTM/Rules-Prune/Research + SceneGraph/ObjectGraph Ableitungen)
# Version:   v3.7.3
# Stand:     2026-04-18
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# DreamWorker ist ORÓMAs Offline-Lernkomponente für die DREAM-Phase.
# Er wird typischerweise als Thread/Job ausgeführt und verarbeitet „jüngste Episoden“
# (SnapChains) aus dem SnapChains-Verzeichnis, um systemweit abgeleitete Artefakte
# zu erzeugen und das Gedächtnis zu optimieren – ohne Live-Perzeption zu stören.
#
# DreamWorker ist bewusst:
#   - headless (keine UI/GUI Abhängigkeiten)
#   - tolerant (best effort, optionale Imports)
#   - orchestrator-safe (Lock-Handling + RunLock)
#   - non-destructive (optimiert/archiviert, löscht nicht blind)
#
# DATENQUELLEN (AKTUELLER STAND IN DIESER DATEI)
# ─────────────────────────────────────────────
# Primärquelle ist das SnapChains-Dateiverzeichnis:
#   OROMA_SNAPCHAINS=/opt/ai/oroma/data/snapchains
#
# Der Worker iteriert über „recent chain files“ und lädt:
#   - JSON Chains (SnapChain-ähnliche dicts) → werden zu core.snapchain.SnapChain coerced
#   - heterogene Pattern/Snap Strukturen → werden defensiv normalisiert
#
# Hinweis: Diese Datei arbeitet zusätzlich mit der DB (oroma.db) über core.sql_manager,
# aber die Kandidatenauswahl (welche Chains) ist hier filesystem-basiert.
#
# KERNPIPELINES (WAS DREAMWORKER HIER TATSÄCHLICH MACHT)
# ──────────────────────────────────────────────────────
# DreamWorker bündelt mehrere „Offline-Pipelines“, die je nach Build/Imports aktiv sind:
#
# (1) Replay-Batch (Bewertung / Episoden-Verdichtung)
#   - _safe_replay / _replay_batch:
#     • lädt Chains → erzeugt Meta-Chains / bewertet / schreibt optional Ergebnisse
#     • verwendet optional core.reward / core.explain (wenn vorhanden)
#
# (2) Mutation / Exploration (leichtes Variation-Lernen)
#   - nutzt core.mutation.mutate_chain()
#   - Ziel: neue Varianten/Hypothesen aus bestehenden Episoden ableiten (kontrolliert)
#
# (3) Langzeitgedächtnis (LTM) Integration
#   - nutzt core.langzeitgedaechtnis.LangzeitGedaechtnis
#   - Ziel: Dedupe/Weight/Quality-Glättung, Similarity-Fähigkeit, Langzeit-Persistenzlogik
#
# (4) Regel-/Archiv-Pruning (Forgetting auf Regel-Ebene)
#   - nutzt core.regelarchiv.prune (Import: from core.regelarchiv import prune)
#   - Ziel: aktive Regeln schlank halten, Export/Explainability nicht zerstören
#
# (5) Forschung/Research Loop (Hypothesen-Simulation, Offline-Signals)
#   - _research_loop / _simulate_hypothesis_score:
#     • erzeugt/score’t Hypothesen best effort (ohne harte Abhängigkeiten)
#
# (6) Missions/Curriculum/Auto-Tuning (Meta-Steuerung)
#   - _missions_update, _curriculum_check, _auto_tune:
#     • passt interne Parameter/Schwerpunkte an (best effort)
#
# (7) SceneGraph/ObjectGraph Ableitungen (Cross-Layer Abstraktion)
#   - _scenegraph_from_vision:
#     • erzeugt scenegraph Snaps aus vision Tokens/Chains (quality-gated)
#   - _objectgraph_from_scenegraph:
#     • erzeugt ObjectGraph-Ableitungen aus SceneGraphs (Namespace-Routing)
#
# Wichtig:
# - Jeder Teil ist so gestaltet, dass er bei fehlenden optionalen Modulen nicht crasht.
# - Fehler werden über core.log_guard.log_suppressed rate-limited geloggt.
#
# RUNLOCK (WICHTIG IM ORCHESTRATOR-BETRIEB)
# ─────────────────────────────────────────
# Damit DreamWorker nicht parallel mehrfach läuft (z. B. Timer + manueller Start),
# existiert ein File-Lock:
#   OROMA_DREAM_LOCK=/opt/ai/oroma/data/state/dream_worker.lock   (typischer Pfad; in Code gesetzt)
# Mechanik:
#   - fcntl.flock exklusiv
#   - wenn Lock belegt → Run wird übersprungen (kein Warten/Blockieren)
#
# SNAP INDEX / DEDUPE (OPTIONALER SPEEDUP)
# ───────────────────────────────────────
# DreamWorker bindet core.snap_indexer an, um aus Chains Fingerprints/Indexpflege zu nutzen,
# damit spätere Merge/Transfer-Pipelines effizient arbeiten können.
#
# WICHTIGE ENV-VARIABLEN (DIESE DATEI VERWENDET SIE TATSÄCHLICH)
# ─────────────────────────────────────────────────────────────
# Logging/Paths:
#   OROMA_LOG_DIR=/opt/ai/oroma/logs
#   OROMA_SNAPCHAINS=/opt/ai/oroma/data/snapchains
#   OROMA_DREAM_LOCK=<lockfile>
#
# SceneGraph:
#   OROMA_SCENEGRAPH_NAMESPACE=<ns>     (Ziel-Namespace für scenegraph Ergebnisse)
#   OROMA_SCENEGRAPH_ORIGIN=<origin>    (Origin Marker)
#   OROMA_SCENEGRAPH_MIN_QUALITY=0.03   (Quality-Gate)
#
# ObjectGraph Routing:
#   OROMA_OBJECTGRAPH_SRC_NS=<src_ns>
#   OROMA_OBJECTGRAPH_TARGET_NS=<dst_ns>
#   OROMA_OBJECT_EXTRACTOR_NAMESPACES=<csv_ns_list>
#
# ÖFFENTLICHE API (STABILER VERTRAG)
# ─────────────────────────────────
# class DreamWorker:
#   - __init__(interval_sec: int|float = ...)
#   - run(): Thread-Loop (periodisch)
#   - stop(): Stop-Signal
#   - interne Pipelines: _replay_batch, _forgetting, _research_loop, ...
#
# CLI / MANUELLER LAUF
# ───────────────────
# Diese Datei besitzt Argument-Parsing (_parse_args) und kann typischerweise so laufen:
#   PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/core/dream_worker.py --once
# oder (je nach Args im File):
#   ... --interval 1800
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Muss headless bleiben (stdlib first; optionale Imports nur guarded).
# - Muss RunLock respektieren (kein Parallel-DreamWorker).
# - Muss tolerant zu heterogenen Chain-Formaten bleiben (alte Dumps/ZIP-Sampling).
# - Muss DB-Locks/Fehler rate-limited loggen und weitermachen (best effort).
#
# =============================================================================
# END HEADER
# =============================================================================


from __future__ import annotations
from pathlib import Path
import logging, threading, time, os, glob, json, argparse
from logging.handlers import RotatingFileHandler
import sys

# Optional: DBWriter client (Stufe C). DreamWorker soll – wenn aktiviert –
# Best-Effort Telemetrie/Policy-Updates über den globalen Single-Writer routen,
# um lokale SQLite-Writer-Kollisionen (database is locked) zu vermeiden.
try:
    from core import db_writer_client  # type: ignore
except Exception:
    db_writer_client = None  # type: ignore

# ----------------------------------------------------------------------------
# Bootstrapping für Direktaufruf (python3 core/dream_worker.py)
# ----------------------------------------------------------------------------
# In ORÓMA werden viele Module als Paketimport ("core.*") geladen. Wenn diese Datei
# jedoch direkt als Script gestartet wird, ist standardmäßig nur /opt/ai/oroma/core
# im sys.path, wodurch "import core" fehlschlagen kann. Daher fügen wir den
# Projekt-Root (/opt/ai/oroma) defensiv hinzu, falls er fehlt.
#
# Hinweis: Beim Aufruf über "python3 -m core.dream_worker" ist dieser Block harmlos.
# ----------------------------------------------------------------------------
_OROMA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _OROMA_ROOT not in sys.path:
    sys.path.insert(0, _OROMA_ROOT)

from core.log_guard import log_suppressed
_BOOT_LOG = logging.getLogger("oroma.dream_worker.boot")
from typing import List, Optional, Dict, Any, Iterable
# --- nur Linux/Unix ---
import fcntl

# ─────────────────────────────────────────────────────────────────────────────
# Optionale numerische Bibliothek
# ─────────────────────────────────────────────────────────────────────────────
try:
    import numpy as np
    _HAS_NP = True
except Exception:
    np = None  # type: ignore
    _HAS_NP = False

# ─────────────────────────────────────────────────────────────────────────────
# Kern-Objekte
# ─────────────────────────────────────────────────────────────────────────────
from core.snapchain import SnapChain
from core.mutation import mutate_chain
from core import snap_indexer
from core.langzeitgedaechtnis import LangzeitGedaechtnis
from core.regelarchiv import prune
from core import sql_manager

# ─────────────────────────────────────────────────────────────────────────────
# Optionale Komponenten
# ─────────────────────────────────────────────────────────────────────────────
try:
    from core.reward import RewardLogger  # SQL-Logger (rewards_log)
except Exception:
    RewardLogger = None  # type: ignore

# RewardEngine ist optional (in vielen Ständen existiert nur eine Funktions-API in core.reward).
# Wir halten den Import separat, damit RewardLogger nicht „mitfällt“, wenn RewardEngine fehlt.
try:
    from core.reward import RewardEngine  # type: ignore
except Exception:
    RewardEngine = None  # type: ignore

try:
    from core.episodic import EpisodicMemory
except Exception:
    EpisodicMemory = None  # type: ignore

try:
    from core.explain import ExplainEngine
except Exception:
    ExplainEngine = None  # type: ignore

# SceneGraph-Builder (NEU – Vision→Meta→SceneGraph)
try:
    from core import scenegraph_builder as _scenegraph_builder
except Exception:
    _scenegraph_builder = None  # type: ignore

# ObjectGraph-Builder (NEU – SceneGraph→ObjectGraph)
try:
    from core import objectgraph_builder as _objectgraph_builder
except Exception:
    _objectgraph_builder = None  # type: ignore

# ObjectGraph-Extractor (NEU – ObjectGraph/SceneGraph → object_nodes/object_relations)
try:
    from core import object_extractor as _object_extractor
except Exception:
    _object_extractor = None  # type: ignore
# NMR Synaptische Plastizität (NEU – Synapsen als Relationstyp 'synaptic')
try:
    from core import nmr_synaptic_plasticity as _nmr_synaptic_plasticity
except Exception:
    _nmr_synaptic_plasticity = None  # type: ignore



# =============================================================================
# Adapter-Schicht (Funktions-API → Klassen-Wrapper)
# =============================================================================

try:
    import core.reward as _reward_mod
except Exception:
    _reward_mod = None  # type: ignore

if RewardEngine is None and _reward_mod:
    class RewardEngine:  # type: ignore[no-redef]
        """Leichter Wrapper über core.reward-Funktions-API."""
        def __init__(self):
            try:
                if hasattr(_reward_mod, "ensure_schema"):
                    _reward_mod.ensure_schema()
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        def evaluate(self, chain) -> float:
            val = 0.0
            try:
                rs = float(getattr(chain, "resonance_score", 0.0))
                val = max(-1.0, min(1.0, rs)) if rs != 0 else min(1.0, len(getattr(chain, "patterns", [])) / 50.0)
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            try:
                if hasattr(_reward_mod, "log"):
                    _reward_mod.log("dream/replay", val, {"source": "dream_worker"})
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            return float(val)

# EpisodicMemory-Adapter
try:
    import core.episodic as _episodic_mod
except Exception:
    _episodic_mod = None  # type: ignore

if EpisodicMemory is None and _episodic_mod:
    class EpisodicMemory:  # type: ignore[no-redef]
        def __init__(self):
            try:
                if hasattr(_episodic_mod, "ensure_schema"):
                    _episodic_mod.ensure_schema()
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        def store(self, chain) -> bool:
            try:
                pats = getattr(chain, "patterns", []) or []
                # patterns can be SnapPattern objects (core.snappattern.SnapPattern) or raw vectors.
                vecs = []
                for p in pats:
                    try:
                        v = getattr(p, "features", None)
                        if v is None:
                            v = getattr(p, "vec", None)
                        if v is None:
                            v = p
                        v = list(map(float, v))
                        if v:
                            vecs.append(v)
                    except Exception:
                        continue
                centroid = [float(sum(v[i] for v in vecs)/len(vecs)) for i in range(len(vecs[0]))] if vecs else None
                if hasattr(_episodic_mod, "save_episode"):
                    _episodic_mod.save_episode("dream/replay", centroid=centroid, meta=getattr(chain, "metadata", {}))
                    return True
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.ret.5", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
                return False
            return False

# ExplainEngine-Adapter
try:
    import core.explain as _explain_mod
except Exception:
    _explain_mod = None  # type: ignore

if ExplainEngine is None and _explain_mod:
    class ExplainEngine:  # type: ignore[no-redef]
        def __init__(self):
            try:
                if hasattr(_explain_mod, "ensure_schema"):
                    _explain_mod.ensure_schema()
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        def trace(self, chain) -> bool:
            try:
                entry = {"when": int(time.time()), "origin": "dream/replay", "meta": getattr(chain, "metadata", {})}
                if hasattr(_explain_mod, "append"):
                    _explain_mod.append("dream", entry)
                    return True
                return False
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.ret.7", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
                return False

# Weitere optionale Module (Registry / Export / Research / Missions / Curriculum)
try:
    from core import model_registry, export_gate, hypothesis as _hypothesis, missions as _missions, curriculum as _curriculum, auto_tuner as _auto_tuner
except Exception:
    model_registry = export_gate = _hypothesis = _missions = _curriculum = _auto_tuner = None  # type: ignore

try:
    from core import snapchain as _snapchain_mod
except Exception:
    _snapchain_mod = None  # type: ignore

# =============================================================================
# ENV-Helper & Logging-Initialisierung
# =============================================================================

def _env_bool(name: str, default=False) -> bool:
    return os.environ.get(name, str(default)).lower().strip() in ("1", "true", "yes", "on")

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception as e:
        log_suppressed(_BOOT_LOG, key="dream_worker.ret.8", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception as e:
        log_suppressed(_BOOT_LOG, key="dream_worker.ret.9", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _log_reward_best_effort(*, source: str, reward: float, step: int = 0,
                           raw: Optional[Dict[str, Any]] = None,
                           episode_id: Optional[int] = None,
                           tag: Optional[str] = None,
                           ts: Optional[int] = None) -> None:
    """Schreibt *best-effort* in oroma.db.rewards_log.

    Warum hier nochmal direkt?
      - In manchen ZIP-Ständen ist core.reward.RewardEngine nicht vorhanden.
      - Der kombinierte Import `from core.reward import RewardEngine, RewardLogger` kann dazu führen,
        dass RewardLogger ebenfalls `None` wird (ImportError cascades).
      - Zusätzlich ist `dream-eff(24h)` in der Learning-UI an rewards_log(source='dream/replay') gebunden.
        Deshalb loggen wir für Dream-Schritte direkt in die Tabelle – ohne harte Abhängigkeit.
    """
    try:
        use_dbw = (db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"))
        if use_dbw:
            db_writer_client.exec_write(
                """
                    INSERT INTO rewards_log(created_at, source, episode_id, step, reward, raw, tag)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    int(ts or time.time()),
                    str(source),
                    None if episode_id is None else int(episode_id),
                    int(step),
                    float(reward),
                    json.dumps(raw or {}, ensure_ascii=False, separators=(",", ":")),
                    str(tag) if tag else None,
                ],
                tag="dream_worker.rewardlog",
                priority="low",
                timeout_ms=800,
                db="oroma",
            )
            return
        with sql_manager.writer_lock('dream_worker.rewardlog', timeout_sec=1):
            with sql_manager.get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO rewards_log(created_at, source, episode_id, step, reward, raw, tag)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(ts or time.time()),
                        str(source),
                        None if episode_id is None else int(episode_id),
                        int(step),
                        float(reward),
                        json.dumps(raw or {}, ensure_ascii=False, separators=(",", ":")),
                        str(tag) if tag else None,
                    ),
                )
                conn.commit()
    except TimeoutError as e:
        log_suppressed(_BOOT_LOG, key="dream_worker.rewardlog.skip.lock", msg="Reward log skipped (DB write-lock busy).", exc=e, level=logging.INFO, interval_s=300)
    except Exception as e:
        # Niemals eskalieren – Dream darf dadurch nicht abbrechen.
        log_suppressed(_BOOT_LOG, key="dream_worker.rewardlog.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

ENABLE_METASNAP = _env_bool("OROMA_ENABLE_METASNAP", False)
ENABLE_RESEARCH = _env_bool("ENABLE_RESEARCH", False)
ENABLE_MISSIONS = _env_bool("ENABLE_MISSIONS", False)
ENABLE_CURRICULUM = _env_bool("ENABLE_CURRICULUM", False)
RESEARCH_BUDGET_PER_NIGHT = _env_int("RESEARCH_BUDGET_PER_NIGHT", 0)
SNAP_DIR = os.environ.get("OROMA_SNAPCHAINS", "/opt/ai/oroma/data/snapchains")
LOG_DIR  = os.environ.get("OROMA_LOG_DIR", "/opt/ai/oroma/logs")
LOCK_PATH = os.environ.get("OROMA_DREAM_LOCK", "/opt/ai/oroma/data/state/dream.lock")
STATE_DIR = Path(os.environ.get("OROMA_STATE_DIR", os.path.dirname(LOCK_PATH) or "/opt/ai/oroma/data/state"))

# NEU: SceneGraph-Auto-Build-Schalter
ENABLE_DREAM_SCENEGRAPH = _env_bool("OROMA_DREAM_SCENEGRAPH", True)

# NEU: ObjectGraph-Auto-Build-Schalter
ENABLE_DREAM_OBJECTGRAPH = _env_bool("OROMA_DREAM_OBJECTGRAPH", True)

# NEU: ObjectGraph-Extractor-Auto-Step (Projection) – schreibt object_nodes/object_relations
ENABLE_DREAM_OBJECT_EXTRACTOR = _env_bool("OROMA_DREAM_OBJECT_EXTRACTOR", True)

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Logging
_DREAM_LOG_ROTATE_BYTES = max(1_000_000, _env_int("OROMA_LOG_ROTATE_BYTES", 20 * 1024 * 1024))
_DREAM_LOG_ROTATE_BACKUPS = max(1, _env_int("OROMA_LOG_ROTATE_BACKUPS", 6))
_DREAM_ATTACH_STDERR = _env_bool("OROMA_DREAM_ATTACH_STDERR", False)
_DREAM_STDERR_LEVEL = str(os.environ.get("OROMA_DREAM_STDERR_LEVEL", "WARNING") or "WARNING").upper()

LOG = logging.getLogger("oroma.dream_worker")
if not LOG.handlers:
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    fh_out = RotatingFileHandler(
        os.path.join(LOG_DIR, "dream.out.log"),
        maxBytes=_DREAM_LOG_ROTATE_BYTES,
        backupCount=_DREAM_LOG_ROTATE_BACKUPS,
        encoding="utf-8",
    )
    fh_err = RotatingFileHandler(
        os.path.join(LOG_DIR, "dream.err.log"),
        maxBytes=_DREAM_LOG_ROTATE_BYTES,
        backupCount=_DREAM_LOG_ROTATE_BACKUPS,
        encoding="utf-8",
    )
    for h in (fh_out, fh_err):
        h.setFormatter(fmt)
        LOG.addHandler(h)
    if _DREAM_ATTACH_STDERR:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(getattr(logging, _DREAM_STDERR_LEVEL, logging.WARNING))
        LOG.addHandler(sh)
    # nur INFO nach .out, WARN+ nach .err; stderr standardmäßig aus, um Orchestrator-Err-Spam zu vermeiden
    fh_out.setLevel(logging.INFO)
    fh_err.setLevel(logging.WARNING)
LOG.setLevel(logging.INFO)
LOG.propagate = False  # verhindert Doppel-Logs über Root-Logger

# =============================================================================
# FS-Fallback & Vector-First Normalisierung
# =============================================================================

def _is_num_seq(x: Any) -> bool:
    return isinstance(x, (list, tuple)) and all(isinstance(v, (int, float)) for v in x)

def _as_float_list(x) -> List[float]:
    return [float(v) for v in x]

def _board_to_vec(x: Any) -> Optional[List[float]]:
    """
    TicTacToe-Board → 9D-Vector:
      X → +1.0, O → −1.0, leer/sonst → 0.0
    """
    if isinstance(x, (list, tuple)) and len(x) == 9 and all(isinstance(v, str) for v in x):
        m = {"X": 1.0, "O": -1.0}
        return [m.get(v, 0.0) for v in x]
    return None

def _extract_feats_from_snap_dict(sd: Dict[str, Any]) -> Optional[List[float]]:
    # bevorzugte Keys
    for k in ("features", "vector", "feats", "embedding", "values", "centroid"):
        if k in sd and _is_num_seq(sd[k]):
            return _as_float_list(sd[k])
    # TTT-Board
    if "board" in sd:
        v = _board_to_vec(sd["board"])
        if v is not None:
            return v
    # verschachtelte payload/data
    pay = sd.get("payload") or sd.get("data")
    if isinstance(pay, dict):
        v = _extract_feats_from_snap_dict(pay)
        if v is not None:
            return v
    return None

def _extract_vectors_from_pattern_dict(pd: Dict[str, Any]) -> List[List[float]]:
    # 1) direkte Vektorlisten
    for k in ("patterns", "vectors", "embeddings", "samples", "items", "entries", "values"):
        if k in pd and isinstance(pd[k], list):
            arr = pd[k]
            if all(_is_num_seq(v) for v in arr):
                return [_as_float_list(v) for v in arr]
            vecs: List[List[float]] = []
            for item in arr:
                if _is_num_seq(item):
                    vecs.append(_as_float_list(item))
                elif isinstance(item, dict):
                    fv = _extract_feats_from_snap_dict(item)
                    if fv is None:
                        return []
                    vecs.append(fv)
                else:
                    return []
            return vecs

    # 2) Snaps/Events
    for k in ("snaps", "events"):
        if k in pd and isinstance(pd[k], list):
            vecs: List[List[float]] = []
            for sd in pd[k]:
                if isinstance(sd, dict):
                    fv = _extract_feats_from_snap_dict(sd)
                    if fv:
                        vecs.append(fv)
            if vecs:
                return vecs

    # 3) Board
    if "board" in pd:
        v = _board_to_vec(pd["board"])
        if v:
            return [v]

    # 4) alleinstehender Centroid
    if "centroid" in pd and _is_num_seq(pd["centroid"]):
        return [_as_float_list(pd["centroid"])]

    return []

def _coerce_json_to_snapchain(data: Any, *, default_meta: Optional[Dict[str, Any]] = None) -> Optional[SnapChain]:
    """
    Konvertiert typische JSON-Exporte robust zu einer echten SnapChain
    (Vector-First). Erlaubt Top-Level: events / patterns / data (+ metadata).
    """
    meta: Dict[str, Any] = dict(default_meta or {})
    vecs: List[List[float]] = []

    if not isinstance(data, dict):
        return None

    if isinstance(data.get("metadata"), dict):
        meta.update(data["metadata"])

    # a) events
    if isinstance(data.get("events"), list):
        for ev in data["events"]:
            if isinstance(ev, dict):
                vs = _extract_vectors_from_pattern_dict(ev)
                if vs:
                    vecs.extend(vs)

    # b) patterns
    if not vecs and isinstance(data.get("patterns"), list):
        pats = data["patterns"]
        if pats and all(_is_num_seq(v) for v in pats):
            vecs = [_as_float_list(v) for v in pats]
        else:
            for p in pats:
                if isinstance(p, dict):
                    vs = _extract_vectors_from_pattern_dict(p)
                    if vs:
                        vecs.extend(vs)
                elif _is_num_seq(p):
                    vecs.append(_as_float_list(p))

    # c) data-Fallback
    if not vecs and isinstance(data.get("data"), list) and all(_is_num_seq(v) for v in data["data"]):
        vecs = [_as_float_list(v) for v in data["data"]]

    if not vecs:
        return None

    try:
        return SnapChain(patterns=vecs, metadata=meta or {"origin": "fs_fallback"})
    except Exception as e:
        LOG.debug("JSON→SnapChain fehlgeschlagen: %s", e)
        return None

# =============================================================================
# FS-Fallback Loader
# =============================================================================

def _list_recent_snap_paths(limit: int = 20) -> List[str]:
    try:
        paths = glob.glob(os.path.join(SNAP_DIR, "*.json"))
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return paths[:max(0, int(limit))]
    except Exception as e:
        LOG.debug("FS-Fallback: Listing fehlgeschlagen (%s)", e)
        return []

def _load_chain_from_path(path: str) -> Optional[SnapChain]:
    """
    Robuster Loader für SnapChain-JSONs → echte SnapChain (Vector-First).
    Nutzt ggf. core.snapchain.load_chain(chain_id), das ein Dict oder eine
    SnapChain liefert. Fällt auf direkten JSON-Parse zurück.
    """
    # 1) Modul-Loader vorhanden?
    if _snapchain_mod and hasattr(_snapchain_mod, "load_chain"):
        try:
            chain_id = os.path.splitext(os.path.basename(path))[0]
            obj = _snapchain_mod.load_chain(chain_id)  # type: ignore[attr-defined]
            if isinstance(obj, SnapChain):
                return obj
            if isinstance(obj, dict):
                return _coerce_json_to_snapchain(obj)
        except Exception as e:
            LOG.debug("FS: core.snapchain.load_chain(%s) schlug fehl: %s", path, e)

    # 2) Direkter JSON-Parse
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _coerce_json_to_snapchain(data)
    except Exception as e:
        LOG.debug("FS: JSON-Laden fehlgeschlagen (%s): %s", path, e)
        return None

# =============================================================================
# Quellen-Multiplexer (Registry → LTM → FS)
# =============================================================================

def _chain_cursor_key(sc: SnapChain) -> str:
    """
    Erzeugt einen moeglichst stabilen Cursor-Schluessel fuer Replay-Fortsetzung.

    Ziel
    ----
    Replay soll in kleinen Haeppchen arbeiten und zwischen Laeufen dort
    weitermachen koennen, wo der letzte Lauf geendet hat. Dazu brauchen wir
    einen serialisierbaren Marker pro verarbeiteter Chain.

    Strategie
    ---------
    Bevorzugt werden vorhandene stabile IDs/Origins. Falls diese fehlen, wird
    aus Metadaten/Feldern ein best-effort JSON-Schluessel gebaut. Das ist kein
    kryptographischer Identifier, aber fuer den Dream-Resume-Pfad ausreichend.
    """
    try:
        sc_id = getattr(sc, 'id', None)
        origin = getattr(sc, 'origin', None)
        if sc_id is not None:
            return f"{origin or '-'}:{sc_id}"
        meta = getattr(sc, 'metadata', None) or {}
        payload = {
            'origin': origin,
            'ts': getattr(sc, 'ts', None),
            'created_at': getattr(sc, 'created_at', None),
            'updated_at': getattr(sc, 'updated_at', None),
            'meta': meta,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return f"fallback:{id(sc)}"


def _iter_recent_chains(batch_size: int = 20, meta_filter_substr: Optional[str] = None, resume_after_key: Optional[str] = None) -> Iterable[SnapChain]:
    """
    Liefert SnapChains aus bis zu drei Quellen; immer echte SnapChain-Objekte.
    meta_filter_substr: falls gesetzt, nur Chains, deren metadata den Substring enthält.
    """
    yielded = 0
    target = max(0, int(batch_size))
    fetch_limit = max(target, (target * 10) if resume_after_key else target)
    matched_cursor = resume_after_key is None

    def _accept(sc: SnapChain) -> bool:
        if not meta_filter_substr:
            return True
        try:
            s = json.dumps(getattr(sc, "metadata", {}) or {}, ensure_ascii=False).lower()
            return meta_filter_substr.lower() in s
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ret.10", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return True

    def _maybe_yield(sc: SnapChain):
        nonlocal yielded, matched_cursor
        if not _accept(sc):
            return None
        key = _chain_cursor_key(sc)
        if not matched_cursor:
            if key == resume_after_key:
                matched_cursor = True
            return None
        yielded += 1
        return sc

    # (1) Registry
    try:
        if model_registry and hasattr(model_registry, "list_recent") and hasattr(model_registry, "load_chain"):
            entries = model_registry.list_recent(limit=fetch_limit) or []  # type: ignore[attr-defined]
            for e in entries:
                try:
                    obj = model_registry.load_chain(e.get("id"))  # type: ignore[attr-defined]
                    sc = obj if isinstance(obj, SnapChain) else (_coerce_json_to_snapchain(obj) if isinstance(obj, dict) else None)
                    if sc:
                        sc2 = _maybe_yield(sc)
                        if sc2 is not None:
                            yield sc2
                            if yielded >= target:
                                return
                except Exception as ex:
                    LOG.debug("Registry load_chain(%s) Fehler: %s", e, ex)
    except Exception as ex:
        LOG.error("Registry-Zugriff fehlgeschlagen: %s", ex)

    # (2) LangzeitGedächtnis
    try:
        mem = LangzeitGedaechtnis()
        if hasattr(mem, "list_recent") and hasattr(mem, "load_snapchain"):
            entries = mem.list_recent(limit=max(0, fetch_limit - yielded))
            for e in entries or []:
                try:
                    obj = mem.load_snapchain(e.get("id"))  # type: ignore[attr-defined]
                    sc = obj if isinstance(obj, SnapChain) else (_coerce_json_to_snapchain(obj) if isinstance(obj, dict) else None)
                    if sc:
                        sc2 = _maybe_yield(sc)
                        if sc2 is not None:
                            yield sc2
                            if yielded >= target:
                                return
                except Exception as ex:
                    LOG.debug("Memory load_snapchain(%s) Fehler: %s", e, ex)
    except Exception as ex:
        LOG.debug("Memory-Zugriff übersprungen: %s", ex)

    # (3) Dateisystem-Fallback
    for p in _list_recent_snap_paths(limit=max(0, fetch_limit - yielded)):
        sc = _load_chain_from_path(p)
        if sc:
            sc2 = _maybe_yield(sc)
            if sc2 is not None:
                yield sc2
                if yielded >= target:
                    return

    if resume_after_key and not matched_cursor:
        # Cursor wurde in keinem Quellfenster gefunden (z.B. archiviert/verdraengt).
        # Dann lieber sauber von vorne starten statt dauerhaft auf einem toten Marker
        # zu verharren.
        yield from _iter_recent_chains(batch_size=target, meta_filter_substr=meta_filter_substr, resume_after_key=None)
        return

# =============================================================================
# Run-Lock (verhindert Doppel-Instanzen)
# =============================================================================

class _RunLock:
    def __init__(self, path: str):
        self.path = path
        self.fd = None
    def acquire(self) -> bool:
        try:
            # NOTE (2026-02-10):
            # Wir nutzen das Lockfile primär als *flock*-Träger (Kernel-Lock), nicht als
            # „Existenz-Markierung“. Ein existierendes dream.lock bedeutet daher NICHT,
            # dass der Worker noch läuft – nur ein *gehaltener* flock wäre relevant.
            #
            # In der Praxis wird das Lockfile jedoch oft von Admins als „läuft/läuft nicht“
            # Signal interpretiert. Deshalb räumen wir es beim Release wieder weg.
            #
            # a+) eröffnet die Datei ohne Truncate ("w" würde sofort auf 0 truncaten) und
            # erlaubt ein PID-Write für Debug/Diag.
            self.fd = open(self.path, "a+")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.fd.seek(0)
                self.fd.truncate(0)
                self.fd.write(str(os.getpid()))
                self.fd.flush()
                os.fsync(self.fd.fileno())
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.11", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            return True
        except BlockingIOError:
            return False
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ret.12", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return False
    def release(self) -> None:
        try:
            if self.fd:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                self.fd.close()
                # Best-effort Cleanup: Datei entfernen, damit "ls" nicht als "stuck" wirkt.
                # Ein fehlender Unlink darf den Dream-Run nie als Fehler markieren.
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    log_suppressed(_BOOT_LOG, key="dream_worker.pass.13.unlink", msg="Suppressed exception (unlink failed)", exc=e, level=logging.DEBUG, interval_s=600)
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.pass.13", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        self.fd = None

# =============================================================================
# Replay + Meta + Mutation + Speichern (mit LTM-Dedupe/Weight)
# =============================================================================

class DreamWorker(threading.Thread):
    """Offline-Lernschritte (Replay/Mutation/Vergessen …) – robust & self-healing."""

    def __init__(
        self,
        memory: Optional[LangzeitGedaechtnis] = None,
        interval: int = 60,
        enable_rewards: bool = True,
        enable_explain: bool = True,
        meta_filter_substr: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self.memory = memory or LangzeitGedaechtnis()
        self.interval = int(interval)
        self._stop_event = threading.Event()
        self.meta_filter_substr = meta_filter_substr

        # Reward-Engine + Reward-Logging (Dream-Efficiency) – optional & nicht-blockierend
        #
        # Hintergrund:
        #   Die Learning-UI berechnet dream-eff(24h) aus rewards_log(source='dream/replay').
        #   DreamWorker evaluierte Rewards zwar, persistierte sie aber nicht zuverlässig.
        #   Reward-Logging ist bewusst best-effort: DB-Lock/Fehler dürfen den Dream-Lauf nie stoppen.
        #
        # WICHTIG:
        #   RewardLogger darf erst nach reward_engine Initialisierung geprüft werden.
        #   (Bugfix: zuvor wurde self.reward_engine vor der Zuweisung referenziert.)
        #
        # Steuerung:
        #   OROMA_DREAM_LOG_REWARDS=1/0 (default: 1)
        self.reward_engine = RewardEngine() if (enable_rewards and RewardEngine) else None

        # RewardLogger (core.reward.RewardLogger) ist optional und kann in einigen Ständen
        # einen langen globalen Write-FLOCK halten, wenn SQLite selbst gerade busy ist.
        # Das blockiert produktive Jobs (Daily Runner / Orchestrator) und fuehrt zu
        # "DB write flock timeout".
        #
        # Daher ist RewardLogger im DreamWorker **standardmaessig deaktiviert**.
        # Dream nutzt fuer dream-eff(24h) ohnehin _log_reward_best_effort(), welches
        # ohne globalen Write-FLOCK arbeitet.
        #
        # Einschalten nur fuer Debug:
        #   OROMA_DREAM_USE_REWARDLOGGER=1
        self.reward_logger = None
        if (
            self.reward_engine
            and RewardLogger
            and _env_bool('OROMA_DREAM_LOG_REWARDS', True)
            and _env_bool('OROMA_DREAM_USE_REWARDLOGGER', False)
        ):
            try:
                self.reward_logger = RewardLogger()
            except Exception as e:
                LOG.debug('RewardLogger init fehlgeschlagen: %s', e)
        self.episodic = EpisodicMemory() if EpisodicMemory else None
        self.explain = ExplainEngine() if (enable_explain and ExplainEngine) else None

        self.fade_rate = _env_float("OROMA_FORGET_DECAY_RATE", 0.95)
        self.compress_threshold = _env_float("OROMA_FORGET_THRESHOLD", 0.20)
        self.forget_flush_items = max(10, _env_int("OROMA_FORGET_FLUSH_ITEMS", 100))
        self.forget_flush_sec = max(1.0, _env_float("OROMA_FORGET_FLUSH_SEC", 10.0))

        # Kompressions-Log nur einmal pro Snap-ID pro Lauf
        self._compressed_logged_ids: set = set()

        LOG.info("DreamWorker v3.7 initialisiert – Interval=%s", self.interval)
        LOG.info("  RewardEngine:   %s", "aktiv" if self.reward_engine else "inaktiv")
        LOG.info("  EpisodicMemory: %s", "aktiv" if self.episodic else "inaktiv")
        LOG.info("  ExplainEngine:  %s", "aktiv" if self.explain else "inaktiv")
        LOG.info("  MetaSnaps:      %s (ENV OROMA_ENABLE_METASNAP)", "aktiv" if ENABLE_METASNAP else "inaktiv")
        LOG.info("  Research:       %s (Budget/Nacht=%s)", "aktiv" if (ENABLE_RESEARCH and _hypothesis) else "inaktiv", RESEARCH_BUDGET_PER_NIGHT)
        LOG.info("  Missions:       %s", "aktiv" if (ENABLE_MISSIONS and _missions) else "inaktiv")
        LOG.info("  Curriculum:     %s", "aktiv" if (ENABLE_CURRICULUM and _curriculum) else "inaktiv")
        LOG.info("  AutoTuner:      %s", "aktiv" if _auto_tuner else "inaktiv")
        LOG.info("  SnapDir:        %s (FS-Fallback aktiv)", SNAP_DIR)
        LOG.info("  Dream-SceneGraph: %s", "aktiv" if (ENABLE_DREAM_SCENEGRAPH and _scenegraph_builder) else "inaktiv")
        LOG.info(
            "  Dream-ObjectGraph: %s",
            "aktiv" if (ENABLE_DREAM_OBJECTGRAPH and _objectgraph_builder) else "inaktiv"
        )
        LOG.info(
            "  Dream-ObjectExtractor: %s",
            "aktiv" if (ENABLE_DREAM_OBJECT_EXTRACTOR and _object_extractor) else "inaktiv"
        )
        LOG.info("  Forget-Flush:   %s Items oder %.1fs", self.forget_flush_items, self.forget_flush_sec)

    # Control
    def stop(self) -> None:
        self._stop_event.set()

    # --------------------------- Dream Run State ---------------------------
    def _dream_state_path(self) -> Path:
        raw = (os.environ.get("OROMA_DREAM_STATE_PATH", "") or "").strip()
        if raw:
            return Path(raw)
        return STATE_DIR / "dream_worker_state.json"

    def _load_dream_state(self) -> Dict[str, Any]:
        path = self._dream_state_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception as e:
            LOG.debug("Dream-State lesen fehlgeschlagen (%s): %s", path, e)
        return {
            "phase_idx": 0,
            "phases": [],
            "last_selected_phase": None,
            "last_completed_ts": 0,
            "last_mode": None,
        }

    def _save_dream_state(self, state: Dict[str, Any]) -> None:
        path = self._dream_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            LOG.warning("Dream-State speichern fehlgeschlagen (%s): %s", path, e)

    def _dream_state_update(self, **fields: Any) -> None:
        """
        Best-effort Merge-Update fuer den Dream-Run-State.

        Zweck
        -----
        Erlaubt feingranulare Fortschrittsmarker waehrend einer Phase, ohne dass
        Aufrufer immer den kompletten State-Block erneut zusammensetzen muessen.
        Fehler duerfen den Dream-Lauf nicht stoppen.
        """
        try:
            state = self._load_dream_state()
            state.update(fields)
            self._save_dream_state(state)
        except Exception as e:
            LOG.debug("Dream-State Update fehlgeschlagen: %s", e)

    def _clear_replay_state(self, state: Optional[Dict[str, Any]] = None, *, save: bool = True) -> Dict[str, Any]:
        """
        Entfernt Replay-spezifische State-Felder hart aus dem Dream-State.

        Kann auf einem bereits geladenen State-Dict arbeiten, damit Endpfade den
        finalen State atomar und ohne Zwischenstand mit alten Replay-Resten
        speichern koennen.
        """
        try:
            if state is None:
                state = self._load_dream_state()
            for key in (
                "replay_candidates",
                "replay_collected_ts",
                "replay_deadline_ts",
                "replay_processed",
                "replay_progress",
                "replay_cursor_key",
                "replay_last_chain_origin",
                "replay_last_chain_id",
                "replay_last_chain_ts",
            ):
                state.pop(key, None)
            if str(state.get("current_phase") or "") != "replay":
                if str(state.get("current_phase_step") or "").startswith("replay"):
                    state["current_phase_step"] = None
            if save:
                self._save_dream_state(state)
            return state
        except Exception as e:
            LOG.debug("Replay-State Cleanup fehlgeschlagen: %s", e)
            return state or {}

    def _phase_deadline_ts(self) -> Optional[float]:
        v = getattr(self, "_current_phase_budget_deadline_ts", None)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def _phase_budget_remaining_s(self) -> float:
        deadline_ts = self._phase_deadline_ts()
        if deadline_ts is None:
            return float("inf")
        return max(0.0, float(deadline_ts) - time.time())

    def _phase_defs(self) -> List[tuple[str, Any, bool]]:
        return [
            ("replay", self._safe_replay, False),
            ("replay_prune", self._safe_replay_prune, False),
            ("forgetting", self._forgetting, False),
            ("nmr_synapses", self._nmr_synapses, True),
            ("research", self._research_loop, True),
            ("missions", self._missions_update, True),
            ("curriculum", self._curriculum_check, True),
            ("auto_tune", self._auto_tune, True),
            ("ptz_policy_attention", self._ptz_policy_from_attention_gain, True),
            ("ptz_policy_motion", self._ptz_policy_from_motion_focus, True),
            ("ptz_policy_audio", self._ptz_policy_from_audio_probe, True),
            ("scenegraph", self._scenegraph_from_vision, True),
            ("objectgraph", self._objectgraph_from_scenegraph, True),
            ("objectextractor", self._objectextractor_from_scenegraphs, True),
        ]

    # Loop
    def run(self) -> None:
        # Run-Lock holen (verhindert Doppel-Instanzen)
        lock = _RunLock(LOCK_PATH)
        if not lock.acquire():
            LOG.info("DreamWorker: läuft bereits (Lock: %s) – Start übersprungen.", LOCK_PATH)
            return

        LOG.info("DreamWorker gestartet")
        try:
            current_run_state: Optional[Dict[str, Any]] = None
            current_run_budget_s: float = 0.0
            current_run_t0: float = 0.0

            def _budget_remaining_s() -> float:
                if current_run_budget_s <= 0:
                    return float("inf")
                return max(0.0, current_run_budget_s - (time.time() - current_run_t0))

            def _phase(name: str, fn) -> bool:
                """Phase-Wrapper: macht den Dream-Lauf sichtbar und debugbar.

                Motivation
                ----------
                DreamWorker kann in grossen Installationen "still" wirken (keine Logs),
                obwohl er arbeitet (z.B. SnapChain-Scan, Prune, SceneGraph/ObjectGraph).
                Dieser Wrapper liefert konsistente Start/End/Duration Logs und verhindert,
                dass einzelne Phasen Fehler nach aussen eskalieren.

                Rueckgabe
                --------
                True  -> Phase lief bis zum Ende des Wrappers.
                False -> Vor der Phase wurde wegen Budgetmangel sauber abgebrochen.
                """
                rem_before = _budget_remaining_s()
                if rem_before <= 0.0:
                    if current_run_state is not None:
                        current_run_state["current_phase"] = name
                        current_run_state["current_phase_started_ts"] = int(time.time())
                        current_run_state["last_phase_status"] = "phase_timeout_budget_hit"
                        current_run_state["last_phase_status_ts"] = int(time.time())
                        self._save_dream_state(current_run_state)
                    LOG.info("[DreamPhase] budget_hit_before: %s (remaining=%.1fs)", name, rem_before)
                    return False

                t0 = time.time()
                setattr(self, "_current_phase_budget_deadline_ts", (time.time() + rem_before) if rem_before != float("inf") else None)
                if current_run_state is not None:
                    current_run_state["current_phase"] = name
                    current_run_state["current_phase_started_ts"] = int(t0)
                    current_run_state["last_phase_status"] = "phase_start"
                    current_run_state["last_phase_status_ts"] = int(t0)
                    self._save_dream_state(current_run_state)
                LOG.info("[DreamPhase] start: %s (remaining=%.1fs)", name, rem_before)
                status = "phase_done"
                try:
                    fn()
                except Exception as e:
                    status = "phase_failed"
                    log_suppressed(
                        _BOOT_LOG,
                        key=f"dream_worker.phase.{name}.pass",
                        msg=f"Suppressed exception in phase {name}",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=120,
                    )
                finally:
                    dt = (time.time() - t0) * 1000.0
                    rem_after = _budget_remaining_s()
                    if current_run_state is not None:
                        current_run_state["last_completed_phase"] = name if status == "phase_done" else current_run_state.get("last_completed_phase")
                        current_run_state["last_phase_end_ts"] = int(time.time())
                        current_run_state["last_phase_duration_ms"] = round(float(dt), 1)
                        current_run_state["last_phase_status"] = status
                        current_run_state["last_phase_status_ts"] = int(time.time())
                        current_run_state["current_phase"] = None
                        current_run_state["current_phase_started_ts"] = None
                        self._save_dream_state(current_run_state)
                    setattr(self, "_current_phase_budget_deadline_ts", None)
                    LOG.info("[DreamPhase] %s: %s (%.1f ms, remaining=%.1fs)", status, name, dt, rem_after)
                return True

            if self.interval == 0:
                mode = str(os.environ.get("OROMA_DREAM_RUN_MODE", "rotate") or "rotate").strip().lower()
                max_runtime_s = max(30, _env_int("OROMA_DREAM_MAX_RUNTIME_S", 300))
                rotate_max_heavy = max(1, _env_int("OROMA_DREAM_ROTATE_MAX_HEAVY", 2))
                phase_defs = self._phase_defs()
                core_phase_defs = [(name, fn) for name, fn, heavy in phase_defs if not heavy]
                heavy_phase_defs = [(name, fn) for name, fn, heavy in phase_defs if heavy]
                heavy_names = [name for name, _fn in heavy_phase_defs]
                heavy_phase_map = {name: fn for name, fn in heavy_phase_defs}
                state = self._load_dream_state()
                state["phases"] = list(heavy_names)
                state["last_mode"] = mode
                started_ts = int(time.time())
                t_run0 = time.time()
                current_run_state = state
                current_run_budget_s = float(max_runtime_s)
                current_run_t0 = t_run0

                def _budget_reached() -> bool:
                    return (time.time() - t_run0) >= max_runtime_s

                idx = int(state.get("phase_idx") or 0)
                next_idx = idx
                selected: List[str] = []
                if mode == "full":
                    selected = list(heavy_names)
                    next_idx = idx
                else:
                    phase_count = len(heavy_names)
                    steps = 0
                    while phase_count > 0 and steps < min(phase_count, rotate_max_heavy):
                        name = heavy_names[next_idx % phase_count]
                        next_idx = (next_idx + 1) % phase_count
                        steps += 1
                        if name not in heavy_phase_map:
                            continue
                        selected.append(name)

                state["phase_idx"] = next_idx
                state["last_selected_phase"] = selected[-1] if selected else None
                state["last_selected_phases"] = list(selected)
                state["last_selected_ts"] = started_ts
                state["run_started_ts"] = started_ts
                state["run_status"] = "running"
                state["max_runtime_s"] = max_runtime_s
                state["rotate_max_heavy"] = rotate_max_heavy
                state["current_phase"] = None
                state["current_phase_started_ts"] = None
                state["last_phase_status"] = "planned"
                state["last_phase_status_ts"] = started_ts
                self._save_dream_state(state)
                LOG.info(
                    "DreamWorker Single-Run geplant (mode=%s core=%s heavy=%s max_runtime_s=%s state=%s)",
                    mode,
                    ",".join([n for n, _f in core_phase_defs]) or "-",
                    ",".join(selected) if selected else "-",
                    max_runtime_s,
                    self._dream_state_path(),
                )

                for name, fn in core_phase_defs:
                    if _budget_reached():
                        state["run_status"] = "budget_hit"
                        state["current_phase"] = name
                        state["current_phase_started_ts"] = int(time.time())
                        state["last_phase_status"] = "phase_timeout_budget_hit"
                        state["last_phase_status_ts"] = int(time.time())
                        self._save_dream_state(state)
                        LOG.info("DreamWorker Single-Run Budget erreicht vor Kernphase %s (mode=%s, max_runtime_s=%s, remaining=%.1fs)", name, mode, max_runtime_s, _budget_remaining_s())
                        break
                    if not _phase(name, fn):
                        state["run_status"] = "budget_hit"
                        self._save_dream_state(state)
                        break
                    if _budget_reached():
                        state["run_status"] = "budget_hit"
                        state["last_phase_status"] = "phase_timeout_budget_hit"
                        state["last_phase_status_ts"] = int(time.time())
                        self._save_dream_state(state)
                        LOG.info("DreamWorker Single-Run Budget erreicht nach Kernphase %s (mode=%s, max_runtime_s=%s, remaining=%.1fs)", name, mode, max_runtime_s, _budget_remaining_s())
                        break
                else:
                    for name in selected:
                        if _budget_reached():
                            state["run_status"] = "budget_hit"
                            state["current_phase"] = name
                            state["current_phase_started_ts"] = int(time.time())
                            state["last_phase_status"] = "phase_timeout_budget_hit"
                            state["last_phase_status_ts"] = int(time.time())
                            self._save_dream_state(state)
                            LOG.info("DreamWorker Single-Run Budget erreicht vor Phase %s (mode=%s, max_runtime_s=%s, remaining=%.1fs)", name, mode, max_runtime_s, _budget_remaining_s())
                            break
                        fn = heavy_phase_map.get(name)
                        if fn is None:
                            continue
                        if not _phase(name, fn):
                            state["run_status"] = "budget_hit"
                            self._save_dream_state(state)
                            break
                        if _budget_reached():
                            state["run_status"] = "budget_hit"
                            state["last_phase_status"] = "phase_timeout_budget_hit"
                            state["last_phase_status_ts"] = int(time.time())
                            self._save_dream_state(state)
                            LOG.info("DreamWorker Single-Run Budget erreicht nach Phase %s (mode=%s, max_runtime_s=%s, remaining=%.1fs)", name, mode, max_runtime_s, _budget_remaining_s())
                            break

                if state.get("run_status") == "running":
                    state["run_status"] = "completed"
                state["last_completed_ts"] = int(time.time())
                state["current_phase"] = None
                state["current_phase_started_ts"] = None
                state["current_phase_step"] = None
                state = self._clear_replay_state(state, save=False)
                self._save_dream_state(state)
                LOG.info(
                    "DreamWorker Single-Run beendet (mode=%s heavy_phases=%s max_runtime_s=%s)",
                    mode,
                    ",".join(selected) if selected else "-",
                    max_runtime_s,
                )
                return

            while not self._stop_event.is_set():
                _phase("replay", self._safe_replay)
                _phase("forgetting", self._forgetting)
                _phase("nmr_synapses", self._nmr_synapses)
                _phase("research", self._research_loop)
                _phase("missions", self._missions_update)
                _phase("curriculum", self._curriculum_check)
                _phase("auto_tune", self._auto_tune)
                _phase("ptz_policy_attention", self._ptz_policy_from_attention_gain)
                _phase("scenegraph", self._scenegraph_from_vision)
                _phase("objectgraph", self._objectgraph_from_scenegraph)
                _phase("objectextractor", self._objectextractor_from_scenegraphs)
                time.sleep(max(1, self.interval))
            LOG.info("DreamWorker gestoppt")
        finally:
            lock.release()

    def _research_loop(self) -> None:
        """
        Pfad: /opt/ai/oroma/core/dream_worker.py
        Projekt: ORÓMA
        Zweck: Defensive No-Op-Research-Phase für Installationen, in denen die
        frühere Research-Pipeline bereits aus dem Worker herausgelöst oder
        unvollständig zusammengeführt wurde.

        Hintergrund:
        - Der Run-Loop ruft die Phase "research" weiterhin explizit auf.
        - In manchen Live-Ständen fehlt jedoch die konkrete Methode
          `_research_loop`, wodurch der gesamte Dream-Run mit AttributeError
          abbricht, obwohl dies fachlich nur eine optionale Offline-Phase ist.

        Verhalten:
        - Wenn keine ältere Implementierung vorhanden ist, wird hier bewusst
          eine harmlose, sichtbar geloggte No-Op-Phase bereitgestellt.
        - So bleibt der DreamWorker lauffähig, bis eine fachliche Research-
          Pipeline wieder konkret hinterlegt wird.
        """
        log_suppressed(
            _BOOT_LOG,
            key="dream_worker.research.noop",
            msg="Research-Phase aktiv, aber keine konkrete _research_loop-Implementierung vorhanden – überspringe",
            level=logging.INFO,
            interval_s=1800,
        )
        return None

    # --------------------------- Metrics Helpers ----------------------------
    def _metric_inc(self, key: str, value: float = 1.0) -> None:
        """
        Best-effort Inkrement (Append) eines Metrics-Eintrags in oroma.db.

        Hintergrund
        ----------
        DreamWorker laeuft parallel zum Live-Agenten und anderen Jobs.
        Metriken sind Diagnose-/Telemetrie-Signale und duerfen NIE einen
        Dream-Run abbrechen oder DB-Locks eskalieren.

        Verhalten
        ---------
        - Schreibt eine einzelne Zeile in Tabelle `metrics(key, ts, value)`.
        - Schlaegt das Schreiben fehl (Lock/Schema/etc.), wird der Fehler
          rate-limited geloggt und ansonsten ignoriert.
        - Die Connection wird immer sauber geschlossen (sql_manager Connection
          ist _ClosingConnection und wird per context manager geschlossen).
        """
        try:
            ts = int(time.time())

            # Stufe C: Wenn DBWriter aktiv ist, Metrik best-effort über den Writer.
            # Telemetrie ist low-prio und darf droppen.
            if db_writer_client is not None:
                try:
                    if os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"):
                        db_writer_client.exec_write(
                            "INSERT INTO metrics(key, ts, value) VALUES(?,?,?)",
                            [str(key), int(ts), float(value)],
                            tag="dream_worker.metric_inc",
                            priority="low",
                            timeout_ms=800,
                            db="oroma",
                        )
                        return
                except Exception as e:
                    log_suppressed(_BOOT_LOG, key="dream_worker.metric_inc.skip.dbw", msg="Metric write skipped (DBWriter failed; no local fallback).", exc=e, level=logging.INFO, interval_s=300)
                    return

            with sql_manager.writer_lock('dream_worker.metric_inc', timeout_sec=1):
                with sql_manager.get_conn() as conn:
                    conn.execute(
                        "INSERT INTO metrics(key, ts, value) VALUES(?,?,?)",
                        (str(key), ts, float(value)),
                    )
        except TimeoutError as e:
            log_suppressed(
                _BOOT_LOG,
                key="dream_worker.metric_inc.skip.lock",
                msg="Metric write skipped (DB write-lock busy).",
                exc=e,
                level=logging.INFO,
                interval_s=300,
            )
        except Exception as e:
            # Telemetrie darf Replay nie stoppen.
            log_suppressed(
                _BOOT_LOG,
                key="dream_worker.metric_inc.pass.1",
                msg="Suppressed exception (was: pass)",
                exc=e,
                level=logging.WARNING,
                interval_s=600,
            )

    # --------------------------- Replay-Schutzmantel --------------------------
    def _safe_replay(self) -> None:
        replay_started_ts = int(time.time())
        self._dream_state_update(
            current_phase="replay",
            current_phase_started_ts=replay_started_ts,
            current_phase_step="replay_start",
            last_phase_status="replay_start",
            last_phase_status_ts=replay_started_ts,
        )
        LOG.info("[DreamReplay] start (remaining=%.1fs)", self._phase_budget_remaining_s())
        try:
            self._replay_batch()
            self._dream_state_update(
                current_phase_step="replay_done",
                last_phase_status="replay_done",
                last_phase_status_ts=int(time.time()),
            )
            LOG.info("[DreamReplay] done (remaining=%.1fs)", self._phase_budget_remaining_s())
        except Exception as e:
            self._dream_state_update(
                current_phase_step="replay_failed",
                last_phase_status="replay_failed",
                last_phase_status_ts=int(time.time()),
                last_phase_error=str(e),
            )
            LOG.warning("Fehler im DreamWorker Replay: %s", e)

    def _safe_replay_prune(self) -> None:
        try:
            self._replay_prune()
        except Exception as e:
            LOG.warning("Fehler im DreamWorker Replay-Prune: %s", e)

    # --------------------------- Replay-Batch ---------------------------------
    def _replay_batch(self, batch_size: int = 10) -> None:
        # ------------------------------------------------------------
        # Replay-Batch Telemetrie (Metrics) + Save-Accounting
        #
        # Ziel:
        # - Harte Diagnose, ob _replay_batch() laeuft und Kandidaten findet.
        # - Pro Kandidat sichtbar machen, ob Meta/Mutation gespeichert werden
        #   oder ob/warum es fehlschlaegt (save_fail / skipped).
        #
        # Keys (metrics.key) – Batch:
        #   dream:replay_batch:run         -> +1 pro Batch-Aufruf
        #   dream:replay_batch:no_chains   -> +1 wenn 0 Kandidaten
        #   dream:replay_batch:candidates  -> +N Kandidaten in diesem Batch
        #
        # Keys (metrics.key) – Persistenz / Ursachen:
        #   dream:replay_attempt:dream/meta
        #   dream:replay_saved:dream/meta
        #   dream:replay_save_fail:dream/meta
        #   dream:replay_skipped:meta_disabled
        #   dream:replay_skipped:meta_empty
        #
        #   dream:replay_attempt:dream/mut
        #   dream:replay_saved:dream/mut
        #   dream:replay_save_fail:dream/mut
        #   dream:replay_skipped:mut_none
        #   dream:replay_mutate_fail
        #
        #   dream:replay_iter_exception
        #
        # Stabilitaet:
        # - best-effort; Telemetrie darf Replay nie stoppen.
        # ------------------------------------------------------------
        self._metric_inc("dream:replay_batch:run", 1.0)

        state = self._load_dream_state()
        batch_size = max(1, _env_int("OROMA_DREAM_REPLAY_BATCH_SIZE", batch_size))
        replay_cursor_key = state.get("replay_cursor_key")
        replay_deadline_ts = self._phase_deadline_ts()
        replay_cap_s = max(15, _env_int("OROMA_DREAM_REPLAY_MAX_RUNTIME_S", 180))
        if replay_deadline_ts is None:
            replay_deadline_ts = time.time() + replay_cap_s
        else:
            replay_deadline_ts = min(float(replay_deadline_ts), time.time() + replay_cap_s)

        self._dream_state_update(
            current_phase="replay",
            current_phase_step="collect_recent_chains",
            current_phase_started_ts=int(time.time()),
            replay_deadline_ts=int(replay_deadline_ts),
        )
        LOG.info(
            "[DreamReplay] collecting candidates (batch_size=%s cap_s=%s remaining=%.1fs)",
            batch_size,
            replay_cap_s,
            max(0.0, replay_deadline_ts - time.time()),
        )

        chains: List[SnapChain] = []
        for sc in _iter_recent_chains(batch_size, self.meta_filter_substr, resume_after_key=replay_cursor_key):
            if time.time() >= replay_deadline_ts:
                self._dream_state_update(
                    current_phase="replay",
                    current_phase_step="replay_budget_hit_during_collect",
                    last_phase_status="replay_budget_hit",
                    last_phase_status_ts=int(time.time()),
                )
                LOG.info("[DreamReplay] budget_hit during collect_recent_chains after %s candidates", len(chains))
                break
            chains.append(sc)
            if len(chains) >= batch_size:
                break

        self._metric_inc("dream:replay_batch:candidates", float(len(chains)))
        self._dream_state_update(
            current_phase="replay",
            current_phase_step="process_candidates",
            replay_candidates=len(chains),
            replay_collected_ts=int(time.time()),
        )
        LOG.info("[DreamReplay] collected %s candidates (cursor=%s)", len(chains), replay_cursor_key or "-")

        if not chains:
            self._metric_inc("dream:replay_batch:no_chains", 1.0)
            if replay_cursor_key:
                self._dream_state_update(
                    current_phase="replay",
                    current_phase_step="cursor_reset_no_candidates",
                    replay_cursor_key=None,
                    replay_processed=0,
                    replay_candidates=0,
                )
                LOG.info("[DreamReplay] no candidates after cursor -> reset replay_cursor_key")
            else:
                LOG.debug("Keine Chains fuer Replay gefunden (Registry/Memory/FS).")
            return

        progress_every = max(1, _env_int("OROMA_DREAM_REPLAY_PROGRESS_EVERY", 5))

        for idx, sc in enumerate(chains, 1):
            if time.time() >= replay_deadline_ts:
                self._dream_state_update(
                    current_phase="replay",
                    current_phase_step="replay_budget_hit",
                    replay_processed=idx - 1,
                    last_phase_status="replay_budget_hit",
                    last_phase_status_ts=int(time.time()),
                )
                LOG.info(
                    "[DreamReplay] budget_hit after %s/%s candidates (remaining=%.1fs)",
                    idx - 1,
                    len(chains),
                    max(0.0, replay_deadline_ts - time.time()),
                )
                break

            if idx == 1 or idx % progress_every == 0:
                self._dream_state_update(
                    current_phase="replay",
                    current_phase_step="process_candidates",
                    replay_processed=idx - 1,
                    replay_candidates=len(chains),
                    replay_progress=f"{idx-1}/{len(chains)}",
                )
                LOG.info(
                    "[DreamReplay] progress %s/%s (remaining=%.1fs)",
                    idx - 1,
                    len(chains),
                    max(0.0, replay_deadline_ts - time.time()),
                )
            replay_key = _chain_cursor_key(sc)
            try:
                # 1) Meta-Zentroid -> dream/meta
                meta = None
                try:
                    meta = self._make_meta_chain(sc)
                except Exception as e:
                    log_suppressed(
                        _BOOT_LOG,
                        key="dream_worker.replay.meta_build_fail",
                        msg="Replay meta build failed (suppressed)",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=300,
                    )
                    meta = None

                if ENABLE_METASNAP:
                    if not meta:
                        self._metric_inc("dream:replay_skipped:meta_empty", 1.0)
                    else:
                        self._metric_inc("dream:replay_attempt:dream/meta", 1.0)
                        try:
                            self._save_chain(meta, origin="dream/meta")
                            self._metric_inc("dream:replay_saved:dream/meta", 1.0)
                        except Exception as e:
                            self._metric_inc("dream:replay_save_fail:dream/meta", 1.0)
                            log_suppressed(
                                _BOOT_LOG,
                                key="dream_worker.replay.save_fail.meta",
                                msg="Replay save failed for dream/meta (suppressed)",
                                exc=e,
                                level=logging.WARNING,
                                interval_s=300,
                            )
                else:
                    # MetaSnaps deaktiviert
                    self._metric_inc("dream:replay_skipped:meta_disabled", 1.0)

                # 2) Mutation/Variation -> dream/mut
                mutated = None
                try:
                    mutated = mutate_chain(sc, rate=0.2)
                except Exception as e:
                    self._metric_inc("dream:replay_mutate_fail", 1.0)
                    LOG.debug("Mutation uebersprungen/Fehler: %s", e)
                    mutated = None

                if not mutated:
                    self._metric_inc("dream:replay_skipped:mut_none", 1.0)
                else:
                    self._metric_inc("dream:replay_attempt:dream/mut", 1.0)
                    try:
                        self._save_chain(mutated, origin="dream/mut")
                        self._metric_inc("dream:replay_saved:dream/mut", 1.0)
                    except Exception as e:
                        self._metric_inc("dream:replay_save_fail:dream/mut", 1.0)
                        log_suppressed(
                            _BOOT_LOG,
                            key="dream_worker.replay.save_fail.mut",
                            msg="Replay save failed for dream/mut (suppressed)",
                            exc=e,
                            level=logging.WARNING,
                            interval_s=300,
                        )

                # 3) Reward (optional)
                if self.reward_engine:
                    try:
                        r = self.reward_engine.evaluate(sc)
                        LOG.debug("Reward=%.3f", r)
                        _log_reward_best_effort(
                            source="dream/replay",
                            step=int(idx),
                            reward=float(r),
                            raw={"origin": getattr(sc, "origin", None), "chain_id": getattr(sc, "id", None)},
                            tag="dream",
                            ts=int(time.time()),
                        )
                        if self.reward_logger:
                            try:
                                self.reward_logger.log(
                                    source="dream/replay",
                                    step=int(idx),
                                    reward=float(r),
                                    raw={
                                        "origin": getattr(sc, "origin", None),
                                        "chain_id": getattr(sc, "id", None),
                                    },
                                    tag="dream",
                                    ts=int(time.time()),
                                )
                            except Exception as e:
                                LOG.debug("RewardLogger Fehler: %s", e)
                    except Exception as e:
                        LOG.debug("Reward-Fehler: %s", e)

                # 4) Episodisch / Explain (optional)
                if self.episodic:
                    try:
                        self.episodic.store(sc)
                    except Exception as e:
                        log_suppressed(
                            _BOOT_LOG,
                            key="dream_worker.pass.14",
                            msg="Suppressed exception (was: pass)",
                            exc=e,
                            level=logging.WARNING,
                            interval_s=600,
                        )
                if self.explain:
                    try:
                        self.explain.trace(sc)
                    except Exception as e:
                        log_suppressed(
                            _BOOT_LOG,
                            key="dream_worker.pass.15",
                            msg="Suppressed exception (was: pass)",
                            exc=e,
                            level=logging.WARNING,
                            interval_s=600,
                        )

                # 5) Export-Gate (optional)
                if export_gate and hasattr(export_gate, "try_mark_for_export"):
                    try:
                        if export_gate.try_mark_for_export(sc):  # type: ignore[attr-defined]
                            LOG.info("Chain markiert fuer Export")
                    except Exception as e:
                        LOG.debug("ExportGate Fehler: %s", e)

                self._dream_state_update(
                    current_phase="replay",
                    current_phase_step="process_candidates",
                    replay_processed=idx,
                    replay_candidates=len(chains),
                    replay_progress=f"{idx}/{len(chains)}",
                    replay_cursor_key=replay_key,
                    replay_last_chain_origin=getattr(sc, "origin", None),
                    replay_last_chain_id=getattr(sc, "id", None),
                    replay_last_chain_ts=getattr(sc, "ts", None),
                )

            except Exception as e:
                self._metric_inc("dream:replay_iter_exception", 1.0)
                LOG.debug("Replay-Iteration #%d uebersprungen: %s", idx, e)

        self._dream_state_update(
            current_phase="replay",
            current_phase_step="replay_batch_done",
            replay_processed=len(chains),
            replay_candidates=len(chains),
        )
        LOG.info("[DreamReplay] batch_done processed=%s cursor=%s", len(chains), self._load_dream_state().get("replay_cursor_key"))

    def _replay_prune(self) -> None:
        prune_deadline_ts = self._phase_deadline_ts()
        prune_cap_s = max(15, _env_int("OROMA_DREAM_PRUNE_MAX_RUNTIME_S", 60))
        if prune_deadline_ts is None:
            prune_deadline_ts = time.time() + prune_cap_s
        else:
            prune_deadline_ts = min(float(prune_deadline_ts), time.time() + prune_cap_s)

        if time.time() >= prune_deadline_ts:
            self._dream_state_update(
                current_phase="replay_prune",
                current_phase_step="prune_budget_hit_before_start",
                last_phase_status="prune_budget_hit",
                last_phase_status_ts=int(time.time()),
            )
            LOG.info("[DreamReplay] prune skipped due to budget_hit before start")
            return

        self._dream_state_update(
            current_phase="replay_prune",
            current_phase_step="prune_start",
            prune_deadline_ts=int(prune_deadline_ts),
        )
        LOG.info("[DreamReplay] prune start (remaining=%.1fs)", max(0.0, prune_deadline_ts - time.time()))
        try:
            prune(threshold=0.01)
            self._dream_state_update(
                current_phase="replay_prune",
                current_phase_step="prune_done",
                last_phase_status="prune_done",
                last_phase_status_ts=int(time.time()),
            )
            LOG.info("[DreamReplay] prune done")
        except Exception as e:
            self._dream_state_update(
                current_phase="replay_prune",
                current_phase_step="prune_failed",
                last_phase_status="prune_failed",
                last_phase_status_ts=int(time.time()),
                last_phase_error=str(e),
            )
            log_suppressed(
                _BOOT_LOG,
                key="dream_worker.pass.16",
                msg="Suppressed exception (was: pass)",
                exc=e,
                level=logging.WARNING,
                interval_s=600,
            )

    # ---------------------- Meta-Zentroid robust berechnen --------------------
    def _pattern_centroid(self, p) -> List[float]:
        # 1) property oder methode
        try:
            c = getattr(p, "centroid", None)
            if callable(c):
                c = c()  # type: ignore[misc]
            if isinstance(c, (list, tuple)) and len(c) > 0:
                return [float(x) for x in c]
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.pass.17", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # 2) snaps/events
        snaps = list(getattr(p, "snaps", []) or getattr(p, "events", []) or [])
        vecs: List[List[float]] = []
        for s in snaps:
            feats = list(getattr(s, "features", []) or [])
            if feats:
                vecs.append([float(x) for x in feats])
        if vecs:
            d = min(len(v) for v in vecs)
            if d > 0:
                if _HAS_NP:
                    arr = np.asarray([v[:d] for v in vecs], dtype=float)  # type: ignore[arg-type, union-attr]
                    return np.mean(arr, axis=0).tolist()  # type: ignore[union-attr]
                acc = [0.0] * d
                for v in vecs:
                    for i in range(d):
                        acc[i] += float(v[i])
                return [v / len(vecs) for v in acc]

        # 3) patterns als Vektorlisten
        plist = list(getattr(p, "patterns", []) or [])
        if plist and all(_is_num_seq(v) for v in plist):
            d = min(len(v) for v in plist)
            if d > 0:
                if _HAS_NP:
                    arr = np.asarray([_as_float_list(v[:d]) for v in plist], dtype=float)  # type: ignore[arg-type, union-attr]
                    return np.mean(arr, axis=0).tolist()  # type: ignore[union-attr]
                acc = [0.0] * d
                for v in plist:
                    vv = _as_float_list(v)
                    for i in range(d):
                        acc[i] += vv[i]
                return [v / len(plist) for v in acc]

        return []

    def _make_meta_chain(self, chain: SnapChain) -> Optional[SnapChain]:
        cents: List[List[float]] = []
        for p in getattr(chain, "patterns", []) or []:
            c = self._pattern_centroid(p)
            if c:
                cents.append(c)
        if not cents:
            LOG.debug("MetaSnapChain leer → Skip")
            return None

        dims = [len(v) for v in cents if isinstance(v, list) and len(v) > 0]
        if not dims:
            LOG.debug("MetaSnapChain leer nach Dimensionsscan → Skip")
            return None
        dim = max(set(dims), key=dims.count)
        cents = [v[:dim] for v in cents if isinstance(v, list) and len(v) >= dim]
        if not cents:
            LOG.debug("MetaSnapChain leer nach Dimensionsfilter → Skip")
            return None
        if _HAS_NP:
            arr = np.asarray(cents, dtype=float)  # type: ignore[arg-type, union-attr]
            if getattr(arr, "ndim", 0) != 2:
                LOG.debug("MetaSnapChain Skip: ndarray ndim=%s", getattr(arr, "ndim", None))
                return None
            vec = np.mean(arr, axis=0).tolist()  # type: ignore[union-attr]
        else:
            acc = [0.0] * dim
            for v in cents:
                for i in range(dim):
                    acc[i] += float(v[i])
            vec = [v / len(cents) for v in acc]

        meta = SnapChain([vec], metadata={"origin": "dream/meta"})
        try:
            meta.score_resonance()
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.pass.18", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        LOG.debug("MetaSnapChain erstellt (dim=%d)", dim)
        return meta

    # ----------------------------- Persistenz ---------------------------------
    def _save_chain(self, chain: SnapChain, origin: str) -> None:
        """
        Speichert abgeleitete Chains (Registry bevorzugt, sonst LTM).
        ─────────────────────────────────────────────────────────────
        • Wenn eine Registry vorhanden ist, nutzt der Worker deren save_chain.
        • Ansonsten wird LangzeitGedaechtnis.save_snapchain(...) genutzt.
          Diese Methode führt *zentrale Deduplikation & Gewichtungs-Update* aus:

            Hash:   SHA-1 über stabil normalisierte Chain (ohne volatile Felder)
            Dedupe: SELECT id WHERE notes == hash
            Update bei Duplikat:
                weight  ← min(10.0, weight * 1.05)        # 5 % Verstärkung
                quality ← (quality_alt + quality_neu)/2    # sanfte Glättung
                ts      ← now                               # „jüngste“ Beobachtung

          Bei *neuen* Chains erfolgt ein INSERT mit weight=1.0.
        """
        # ------------------------------------------------------------
        # Replay/Muta-Accounting: ensure origin is persisted
        #
        # Problem (observed in Learning-UI): dream replay steps can be >0 while
        # counts for origin='dream/mut' or origin='dream/meta' stay at 0.
        # Root cause: LangzeitGedaechtnis.save_snapchain() derives the DB origin
        # from chain.metadata['origin'] (defaulting to 'memory') and uses that
        # value as part of the stable hash. The DreamWorker passed origin as an
        # argument to _save_chain(), but did not write it into the chain
        # metadata. Therefore, replay-derived chains were deduped/updated under
        # their original origin and never appeared as 'dream/mut' or 'dream/meta'.
        #
        # Fix (minimal-invasive, non-destructive):
        # - Ensure chain.metadata exists and is a dict
        # - Preserve previous origin in metadata (origin_prev) when switching
        # - Write the requested origin into metadata['origin'] BEFORE persisting
        #
        # This makes replay-derived streams observable, while keeping the same
        # dedupe semantics: repeated saves of identical (origin, vecs) update
        # weight/quality/ts instead of inserting unbounded rows.
        # ------------------------------------------------------------
        try:
            meta = getattr(chain, 'metadata', None)
            if meta is None or not isinstance(meta, dict):
                meta = {}
                setattr(chain, 'metadata', meta)
            prev_origin = meta.get('origin')
            if prev_origin != origin:
                # Keep lineage signal without affecting hashing/dedupe.
                meta.setdefault('origin_prev', prev_origin)
                meta.setdefault('dream_parent_origin', prev_origin)
            meta['origin'] = origin
            meta.setdefault('dream_saved_by', 'dream_worker')
            meta.setdefault('dream_saved_ts', float(time.time()))
        except Exception:
            # Never block persistence due to optional metadata decoration.
            pass

        try:
            if model_registry and hasattr(model_registry, "save_chain"):
                model_registry.save_chain(chain, origin=origin, status="active")  # type: ignore[attr-defined]
            else:
                q = float(getattr(chain, "resonance_score", 0.0) or 0.0)
                try:
                    self.memory.save_snapchain(chain, q)
                    LOG.debug("SnapChain gespeichert (origin=%s, quality=%.3f)", origin, q)
                except Exception as e:
                    LOG.debug("Speichern der Chain fehlgeschlagen (%s): %s", origin, e)
        except Exception as e:
            LOG.debug("Unerwarteter Fehler beim Speichern (%s): %s", origin, e)

    # --------------------------- Forgetting (Patch 2.3) -----------------------
    def _forgetting(self) -> None:
        """
        Gewichtetes Vergessen mit koaleszierendem Flush und sichtbaren Fortschrittsmarkern.

        Ziele
        -----
        - fruehes Sichtbarwerden des aktuellen Forgetting-Status im dream_worker_state.json
        - internes Laufzeitbudget innerhalb des Forgetting-Pfads
        - Batches statt monolithischem Langlauf ohne Fortschrittssignale
        - weiterhin koaleszierende Flushes fuer DBWriter-/DB-Lastreduktion
        """
        processed = 0
        compressed = 0
        flushes = 0
        errors = 0
        use_dbw = (db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"))
        timeout_w = max(5000, int(self.forget_flush_sec * 1000.0) + 3000)
        timeout_m = max(15000, int(self.forget_flush_sec * 1000.0) + 10000)

        cap_s = max(15, _env_int("OROMA_DREAM_FORGETTING_MAX_RUNTIME_S", 180))
        phase_deadline_ts = self._phase_deadline_ts()
        if phase_deadline_ts is None:
            phase_deadline_ts = time.time() + cap_s
        else:
            phase_deadline_ts = min(float(phase_deadline_ts), time.time() + cap_s)
        batch_size = max(50, _env_int("OROMA_DREAM_FORGETTING_BATCH_SIZE", 500))

        pending_weights: List[List[Any]] = []
        pending_status: List[List[Any]] = []
        pending_metas: List[Dict[str, Any]] = []
        flush_since = time.monotonic()

        def _flush_pending(reason: str) -> None:
            nonlocal flushes, errors, flush_since
            if not pending_weights and not pending_status and not pending_metas:
                return
            weight_n = len(pending_weights)
            status_n = len(pending_status)
            meta_n = len(pending_metas)
            self._dream_state_update(
                current_phase="forgetting",
                current_phase_step=f"flush_start:{reason}",
                forgetting_processed=processed,
                forgetting_compressed=compressed,
                forgetting_flushes=flushes,
                forgetting_deadline_ts=int(phase_deadline_ts),
            )
            try:
                if use_dbw:
                    if pending_weights:
                        db_writer_client.executemany(
                            "UPDATE snapchains SET weight=? WHERE id=?",
                            pending_weights,
                            tag="dream_worker.forgetting.weight",
                            priority="low",
                            timeout_ms=timeout_w,
                            db="oroma",
                        )
                    if pending_metas:
                        db_writer_client.executemany(
                            "INSERT INTO meta_snaps(label, score, sources) VALUES(?,?,?)",
                            [[m["label"], m["score"], m["sources_json"]] for m in pending_metas],
                            tag="dream_worker.forgetting.metasnap",
                            priority="low",
                            timeout_ms=timeout_m,
                            db="oroma",
                        )
                    if pending_status:
                        db_writer_client.executemany(
                            "UPDATE snapchains SET status='compressed' WHERE id=?",
                            pending_status,
                            tag="dream_worker.forgetting.status",
                            priority="low",
                            timeout_ms=timeout_w,
                            db="oroma",
                        )
                else:
                    with sql_manager.get_conn() as conn:
                        if pending_weights:
                            conn.executemany(
                                "UPDATE snapchains SET weight=? WHERE id=?",
                                [(float(p[0]), int(p[1])) for p in pending_weights],
                            )
                        meta_ids: List[tuple[int, Dict[str, Any]]] = []
                        for m in pending_metas:
                            cur2 = conn.execute(
                                "INSERT INTO meta_snaps(label, score, sources) VALUES(?,?,?)",
                                (m["label"], float(m["score"]), m["sources_json"]),
                            )
                            try:
                                meta_ids.append((int(cur2.lastrowid or 0), m))
                            except Exception:
                                pass
                        for meta_id, m in meta_ids:
                            if meta_id <= 0:
                                continue
                            try:
                                snap_indexer.index_meta_snap(
                                    conn,
                                    meta_id=meta_id,
                                    label=m["label"],
                                    score=float(m["score"]),
                                    sources=[int(m["wid"])],
                                    ts=time.time(),
                                    source="dream:compress",
                                    privacy_tier="local",
                                )
                            except Exception as e:
                                LOG.warning("snap_index MetaSnap write failed (meta_id=%s): %s", meta_id, e)
                        if pending_status:
                            conn.executemany(
                                "UPDATE snapchains SET status='compressed' WHERE id=?",
                                [(int(p[0]),) for p in pending_status],
                            )
                        conn.commit()
                flushes += 1
                self._dream_state_update(
                    current_phase="forgetting",
                    current_phase_step=f"flush_done:{reason}",
                    forgetting_processed=processed,
                    forgetting_compressed=compressed,
                    forgetting_flushes=flushes,
                    forgetting_deadline_ts=int(phase_deadline_ts),
                )
                LOG.info(
                    "[DreamForgetting] flush_done(%s): weight=%s meta=%s status=%s flushes=%s remaining=%.1fs",
                    reason,
                    weight_n,
                    meta_n,
                    status_n,
                    flushes,
                    max(0.0, phase_deadline_ts - time.time()),
                )
            except Exception as e:
                errors += 1
                self._dream_state_update(
                    current_phase="forgetting",
                    current_phase_step=f"flush_failed:{reason}",
                    forgetting_processed=processed,
                    forgetting_compressed=compressed,
                    forgetting_flushes=flushes,
                    forgetting_deadline_ts=int(phase_deadline_ts),
                    last_phase_error=str(e),
                )
                LOG.warning(
                    "Forgetting-Flush fehlgeschlagen (%s): weight=%s meta=%s status=%s err=%s",
                    reason,
                    weight_n,
                    meta_n,
                    status_n,
                    e,
                )
            finally:
                pending_weights.clear()
                pending_status.clear()
                pending_metas.clear()
                flush_since = time.monotonic()

        try:
            self._clear_replay_state()
            self._dream_state_update(
                current_phase="forgetting",
                current_phase_started_ts=int(time.time()),
                current_phase_step="forgetting_collect_start",
                forgetting_processed=0,
                forgetting_processed_this_run=0,
                forgetting_progress="0/?",
                forgetting_compressed=0,
                forgetting_flushes=0,
                forgetting_deadline_ts=int(phase_deadline_ts),
            )
            LOG.info(
                "[DreamForgetting] start (cap_s=%s remaining=%.1fs flush_items=%s flush_sec=%.1f batch_size=%s)",
                cap_s,
                max(0.0, phase_deadline_ts - time.time()),
                int(self.forget_flush_items),
                float(self.forget_flush_sec),
                batch_size,
            )

            state0 = self._load_dream_state()
            cursor_id = int(state0.get("forgetting_cursor_id") or 0)
            processed_cumulative_prev = int(state0.get("forgetting_processed_cumulative") or 0)
            with sql_manager.get_conn() as rconn:
                cur = rconn.cursor()
                cur.execute("SELECT COUNT(*) FROM snapchains WHERE id > ?", (int(cursor_id),))
                total_rows = int((cur.fetchone() or [0])[0] or 0)
                if total_rows <= 0 and int(cursor_id) > 0:
                    cursor_id = 0
                    cur.execute("SELECT COUNT(*) FROM snapchains WHERE id > ?", (0,))
                    total_rows = int((cur.fetchone() or [0])[0] or 0)
                cur.execute(
                    "SELECT id, weight FROM snapchains WHERE id > ? ORDER BY id LIMIT ?",
                    (int(cursor_id), int(batch_size)),
                )
                rows = cur.fetchall() or []

            total_rows = int(total_rows)
            total_scope = int(processed_cumulative_prev + total_rows)
            self._dream_state_update(
                current_phase="forgetting",
                current_phase_step="forgetting_collect_done",
                forgetting_cursor_id=int(cursor_id),
                forgetting_candidates=total_rows,
                forgetting_processed=0,
                forgetting_processed_this_run=0,
                forgetting_processed_cumulative=int(processed_cumulative_prev),
                forgetting_remaining_estimate=total_rows,
                forgetting_percent_this_run=0.0,
                forgetting_percent_cumulative=round((processed_cumulative_prev / total_scope * 100.0), 3) if total_scope else 0.0,
                forgetting_progress=f"0/{total_rows}",
                forgetting_compressed=0,
                forgetting_flushes=0,
                forgetting_deadline_ts=int(phase_deadline_ts),
            )
            LOG.info(
                "[DreamForgetting] collected %s rows after cursor_id=%s (remaining=%.1fs)",
                total_rows,
                cursor_id,
                max(0.0, phase_deadline_ts - time.time()),
            )

            for start_idx in range(0, total_rows, batch_size):
                if time.time() >= phase_deadline_ts:
                    self._dream_state_update(
                        current_phase="forgetting",
                        current_phase_step="forgetting_budget_hit_before_batch",
                        forgetting_candidates=total_rows,
                        forgetting_processed=processed,
                        forgetting_processed_this_run=processed,
                        forgetting_processed_cumulative=int(processed_cumulative_prev + processed),
                        forgetting_remaining_estimate=max(0, total_rows - processed),
                        forgetting_percent_this_run=round((processed / total_rows * 100.0), 3) if total_rows else 0.0,
                        forgetting_percent_cumulative=round(((processed_cumulative_prev + processed) / total_scope * 100.0), 3) if total_scope else 0.0,
                        forgetting_progress=f"{processed}/{total_rows}",
                        forgetting_compressed=compressed,
                        forgetting_flushes=flushes,
                        forgetting_deadline_ts=int(phase_deadline_ts),
                        forgetting_cursor_id=int(cursor_id),
                        last_phase_status="forgetting_budget_hit",
                        last_phase_status_ts=int(time.time()),
                    )
                    LOG.info(
                        "[DreamForgetting] budget_hit before batch start=%s processed=%s/%s",
                        start_idx,
                        processed,
                        total_rows,
                    )
                    break

                batch = rows[start_idx:start_idx + batch_size]
                self._dream_state_update(
                    current_phase="forgetting",
                    current_phase_step="forgetting_batch_start",
                    forgetting_candidates=total_rows,
                    forgetting_processed=processed,
                    forgetting_processed_this_run=processed,
                    forgetting_processed_cumulative=int(processed_cumulative_prev + processed),
                    forgetting_remaining_estimate=max(0, total_rows - processed),
                    forgetting_percent_this_run=round((processed / total_rows * 100.0), 3) if total_rows else 0.0,
                    forgetting_percent_cumulative=round(((processed_cumulative_prev + processed) / total_scope * 100.0), 3) if total_scope else 0.0,
                    forgetting_progress=f"{processed}/{total_rows}",
                    forgetting_compressed=compressed,
                    forgetting_flushes=flushes,
                    forgetting_deadline_ts=int(phase_deadline_ts),
                    forgetting_cursor_id=int(cursor_id),
                )

                for r in batch:
                    if time.time() >= phase_deadline_ts:
                        self._dream_state_update(
                            current_phase="forgetting",
                            current_phase_step="forgetting_budget_hit_in_batch",
                            forgetting_candidates=total_rows,
                            forgetting_processed=processed,
                            forgetting_progress=f"{processed}/{total_rows}",
                            forgetting_compressed=compressed,
                            forgetting_flushes=flushes,
                            forgetting_deadline_ts=int(phase_deadline_ts),
                            last_phase_status="forgetting_budget_hit",
                            last_phase_status_ts=int(time.time()),
                        )
                        LOG.info(
                            "[DreamForgetting] budget_hit during batch processed=%s/%s",
                            processed,
                            total_rows,
                        )
                        break

                    wid = r["id"] if hasattr(r, "keys") and "id" in r.keys() else r[0]
                    w = float(r["weight"] if hasattr(r, "keys") and "weight" in r.keys() else r[1])
                    new_w = w * float(self.fade_rate)
                    pending_weights.append([float(new_w), int(wid)])
                    processed += 1
                    cursor_id = int(wid)

                    if new_w < float(self.compress_threshold):
                        pending_metas.append({
                            "label": f"compressed_{wid}",
                            "score": float(new_w),
                            "sources_json": json.dumps([int(wid)]),
                            "wid": int(wid),
                        })
                        pending_status.append([int(wid)])
                        compressed += 1

                    now = time.monotonic()
                    if (
                        len(pending_weights) >= int(self.forget_flush_items)
                        or len(pending_metas) >= int(self.forget_flush_items)
                        or (now - flush_since) >= float(self.forget_flush_sec)
                    ):
                        _flush_pending("threshold")

                self._dream_state_update(
                    current_phase="forgetting",
                    current_phase_step="forgetting_batch_done",
                    forgetting_candidates=total_rows,
                    forgetting_processed=processed,
                    forgetting_processed_this_run=processed,
                    forgetting_processed_cumulative=int(processed_cumulative_prev + processed),
                    forgetting_remaining_estimate=max(0, total_rows - processed),
                    forgetting_percent_this_run=round((processed / total_rows * 100.0), 3) if total_rows else 0.0,
                    forgetting_percent_cumulative=round(((processed_cumulative_prev + processed) / total_scope * 100.0), 3) if total_scope else 0.0,
                    forgetting_progress=f"{processed}/{total_rows}",
                    forgetting_compressed=compressed,
                    forgetting_flushes=flushes,
                    forgetting_deadline_ts=int(phase_deadline_ts),
                    forgetting_cursor_id=int(cursor_id),
                )
                LOG.info(
                    "[DreamForgetting] progress %s/%s compressed=%s flushes=%s remaining=%.1fs",
                    processed,
                    total_rows,
                    compressed,
                    flushes,
                    max(0.0, phase_deadline_ts - time.time()),
                )

                if time.time() >= phase_deadline_ts:
                    break

            _flush_pending("final")
            self._dream_state_update(
                current_phase="forgetting",
                current_phase_step="forgetting_done",
                forgetting_candidates=total_rows,
                forgetting_processed=processed,
                forgetting_progress=f"{processed}/{total_rows}",
                forgetting_compressed=compressed,
                forgetting_flushes=flushes,
                forgetting_deadline_ts=int(phase_deadline_ts),
                last_phase_status="forgetting_done",
                last_phase_status_ts=int(time.time()),
            )
            LOG.info(
                "Forgetting-Zyklus: scanned=%s weights=%s compressed=%s flushes=%s errors=%s dbw=%s",
                total_rows,
                processed,
                compressed,
                flushes,
                errors,
                use_dbw,
            )
        except Exception as e:
            self._dream_state_update(
                current_phase="forgetting",
                current_phase_step="forgetting_failed",
                forgetting_processed=processed,
                forgetting_compressed=compressed,
                forgetting_flushes=flushes,
                forgetting_deadline_ts=int(phase_deadline_ts),
                last_phase_status="forgetting_failed",
                last_phase_status_ts=int(time.time()),
                last_phase_error=str(e),
            )
            LOG.warning("Forgetting-Lauf fehlgeschlagen: %s", e)

    # -------------------------- NMR Synapses (v3.7) ---------------------------
    def _nmr_synapses(self) -> None:
        """Führt synaptische NMR-Plastizität best effort aus.

        Der Phase-Name wird im Run-Loop fest verdrahtet genutzt. Falls das Modul
        nicht importiert werden konnte oder einzelne Läufe fehlschlagen, darf der
        DreamWorker nicht mit AttributeError abbrechen.
        """
        if _nmr_synaptic_plasticity is None:
            return
        try:
            res = _nmr_synaptic_plasticity.run_plasticity_once()  # type: ignore[attr-defined]
            if isinstance(res, dict):
                LOG.info(
                    "NMR-Synapses: nodes=%s edges=%s updates=%s scans=%s",
                    res.get("nodes"),
                    res.get("edges"),
                    res.get("updates"),
                    res.get("scans"),
                )
        except Exception as e:
            log_suppressed(
                _BOOT_LOG,
                key="dream_worker.nmr_synapses",
                msg="Suppressed exception in _nmr_synapses",
                exc=e,
                level=logging.WARNING,
                interval_s=300,
            )

    # ---------------------------- Missions (v3.6) -----------------------------
    def _missions_update(self) -> None:
        if not (ENABLE_MISSIONS and _missions):
            return
        try:
            actives = _missions.list_missions(active_only=True)  # type: ignore[attr-defined]
        except Exception:
            actives = []
        for m in actives or []:
            mid = (m or {}).get("id")
            if not mid:
                continue
            try:
                if _missions.check_and_complete(mid):  # type: ignore[attr-defined]
                    LOG.info("Mission #%s abgeschlossen.", mid)
            except Exception as e:
                LOG.debug("Mission check_and_complete fehlgeschlagen (#%s): %s", mid, e)

    # --------------------------- Curriculum (v3.6) ---------------------------
    def _curriculum_check(self) -> None:
        if not (ENABLE_CURRICULUM and _curriculum):
            return
        metrics: Dict[str, Any] = {}
        try:
            last = sql_manager.fetch_last_coverage(limit=1) or []
            if last:
                metrics["wins"] = int(last[0].get("active", 0))
                metrics["rate"] = float(last[0].get("coverage", 0.0))
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.pass.20", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        try:
            if _curriculum.advance_if_ready(metrics):  # type: ignore[attr-defined]
                LOG.info("Curriculum: Stage-Aufstieg → %s", _curriculum.current_stage_name())
        except Exception as e:
            LOG.debug("Curriculum advance_if_ready fehlgeschlagen: %s", e)

    # ---------------------------- Auto-Tuning (v3.6) -------------------------
    def _auto_tune(self) -> None:
        if not _auto_tuner:
            return
        try:
            tuned = _auto_tuner.auto_tune({  # type: ignore[attr-defined]
                "fade_rate": float(self.fade_rate),
                "compress_threshold": float(self.compress_threshold),
            })
            fr = float(tuned.get("fade_rate", self.fade_rate))
            ct = float(tuned.get("compress_threshold", self.compress_threshold))
            if 0.80 <= fr <= 0.999:
                self.fade_rate = fr
            if 0.05 <= ct <= 0.50:
                self.compress_threshold = ct
            LOG.debug("AutoTuner angewandt: fade_rate=%.4f, compress_threshold=%.3f",
                      self.fade_rate, self.compress_threshold)
        except Exception as e:
            LOG.debug("AutoTuning übersprungen: %s", e)

    # --------------------------- PTZ: Bandit Policy ---------------------------
    def _ptz_policy_from_attention_gain(self) -> None:
        """Aggregate PTZ attention_gain rewards into policy_rules.

        Motivation
        ----------
        Der PTZ-Attention-Loop (core/ptz_attention_loop.py) loggt ein sehr leichtes
        Reward-Signal (source='ptz/attention_gain') in rewards_log. Dieses Signal
        ist *kontinuierlich* (Delta eines Attention-Scores) und eignet sich für
        ein Bandit-Style Learning:
            state -> action -> expected_gain

        Diese Funktion laeuft im DreamWorker und aktualisiert policy_rules
        (namespace='ptz_att') als kompakten, erklaerbaren Action-Cache.

        Wichtige Design-Entscheidungen
        ------------------------------
        - Kein "virtuelles PTZ"/Simulation: wir lernen aus realen Moves.
        - DB-schonend: inkrementell per checkpoint (dream_state).
        - Robust gegen Legacy-Rewards ohne Pose: state_hash wird aus raw.state_hash
          genommen, andernfalls fallen wir auf "legacy".

        ENV
        ---
        OROMA_PTZ_ATT_POLICY_DREAM_ENABLE (Default: 1)
        OROMA_PTZ_ATT_POLICY_MAX_ROWS     (Default: 500)
        OROMA_PTZ_ATT_POLICY_POS_THR      (Default: 0.002)
        OROMA_PTZ_ATT_POLICY_NEG_THR      (Default: -0.002)
        """
        if not _env_bool("OROMA_PTZ_ATT_POLICY_DREAM_ENABLE", True):
            return

        max_rows = _env_int("OROMA_PTZ_ATT_POLICY_MAX_ROWS", 500)
        max_rows = max(10, min(int(max_rows), 5000))

        pos_thr = _env_float("OROMA_PTZ_ATT_POLICY_POS_THR", 0.002)
        neg_thr = _env_float("OROMA_PTZ_ATT_POLICY_NEG_THR", -0.002)
        if neg_thr > 0:
            neg_thr = -abs(float(neg_thr))

        # checkpoint in dream_state
        last_id = 0
        try:
            with sql_manager.get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM dream_state WHERE key=?",
                    ("ptz_policy:last_reward_id",),
                ).fetchone()
                if row:
                    try:
                        last_id = int(row[0])
                    except Exception:
                        last_id = 0
        except Exception:
            last_id = 0

        # Fetch new rewards
        rows = []
        try:
            with sql_manager.get_conn() as conn:
                conn.row_factory = None
                rows = conn.execute(
                    """
                    SELECT id, created_at, reward, raw
                    FROM rewards_log
                    WHERE source=? AND id>?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    ("ptz/attention_gain", int(last_id), int(max_rows)),
                ).fetchall() or []
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ptz_policy.fetch.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=300)
            return

        if not rows:
            return

        # Upsert aggregates
        new_last_id = last_id
        updated = 0
        skipped = 0

        def _parse_raw(raw_s: str) -> Dict[str, Any]:
            try:
                j = json.loads(raw_s) if raw_s else {}
                return j if isinstance(j, dict) else {}
            except Exception:
                return {}

        try:
            # Stufe C: Policy-Updates sind wichtig, aber nicht zeitkritisch.
            # Wir vermeiden lokale Writer-Kollisionen, indem wir – wenn möglich – über DBWriter
            # schreiben. Gleichzeitig eliminieren wir das SELECT->UPDATE Roundtrip, indem wir die
            # Running-Mean direkt in SQL updaten (n/q).
            use_dbw = (db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"))

            if not use_dbw:
                conn = sql_manager.get_conn()
            else:
                conn = None

            if conn is not None:
                conn.execute("PRAGMA busy_timeout=5000")

            for rid, created_at, reward, raw_s in rows:
                    try:
                        rid_i = int(rid)
                        new_last_id = rid_i
                    except Exception:
                        continue

                    try:
                        r = float(reward)
                    except Exception:
                        skipped += 1
                        continue

                    rawj = _parse_raw(raw_s)
                    action = str(rawj.get("action") or "")
                    if not action:
                        skipped += 1
                        continue

                    state_hash = str(rawj.get("state_hash") or "")
                    if not state_hash:
                        # legacy fallback
                        state_hash = "legacy"

                    # label pos/neg/draw for explainability
                    pos = 1 if r >= pos_thr else 0
                    neg = 1 if r <= neg_thr else 0
                    draw = 1 if (pos == 0 and neg == 0) else 0

                    ts1 = int(created_at) if created_at else int(time.time())

                    # Ensure row exists (idempotent)
                    ins_sql = (
                        "INSERT OR IGNORE INTO policy_rules(namespace, state_hash, action, n, pos, neg, draw, q, last_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?)"
                    )
                    ins_params = ["ptz_att", state_hash, action, 0, 0, 0, 0, 0.0, ts1]

                    # Running mean update in SQL (no SELECT roundtrip):
                    #   q <- (q*n + r)/(n+1)
                    #   n <- n+1
                    upd_sql = (
                        "UPDATE policy_rules "
                        "SET q=((q*CAST(n AS REAL))+?)/CAST((n+1) AS REAL), "
                        "    n=(n+1), pos=(pos+?), neg=(neg+?), draw=(draw+?), last_ts=? "
                        "WHERE namespace=? AND state_hash=? AND action=?"
                    )
                    upd_params = [float(r), int(pos), int(neg), int(draw), ts1, "ptz_att", state_hash, action]

                    if use_dbw:
                        db_writer_client.exec_write(ins_sql, ins_params, tag="dream_worker.ptz_policy.ins", priority="low", timeout_ms=2000, db="oroma")
                        db_writer_client.exec_write(upd_sql, upd_params, tag="dream_worker.ptz_policy.upd", priority="low", timeout_ms=2000, db="oroma")
                    else:
                        conn.execute(ins_sql, ins_params)
                        conn.execute(upd_sql, upd_params)
                    updated += 1

            # persist checkpoint
            ck_sql = "INSERT OR REPLACE INTO dream_state(key, value) VALUES(?,?)"
            ck_params = ["ptz_policy:last_reward_id", str(int(new_last_id))]
            if use_dbw:
                db_writer_client.exec_write(ck_sql, ck_params, tag="dream_worker.ptz_policy.ck", priority="low", timeout_ms=2000, db="oroma")
            else:
                conn.execute(ck_sql, ck_params)

            if conn is not None:
                try:
                    conn.commit()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ptz_policy.upsert.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=300)
            return

        # Light telemetry
        try:
            self._metric_inc("ptz:policy_updates", float(updated))
            self._metric_inc("ptz:policy_skipped", float(skipped))
        except Exception:
            pass


    def _ptz_policy_from_motion_focus(self) -> None:
        """Aggregate PTZ motion_focus rewards into policy_rules (namespace='ptz_motion').

        Source
        ------
        rewards_log.source = 'ptz/motion_focus' (logged by core/ptz_attention_loop.py in THREAT mode)

        ENV
        ---
        OROMA_PTZ_MOTION_POLICY_DREAM_ENABLE (Default: 1)
        OROMA_PTZ_MOTION_POLICY_MAX_ROWS     (Default: 500)
        OROMA_PTZ_MOTION_POLICY_POS_THR      (Default: 0.01)
        OROMA_PTZ_MOTION_POLICY_NEG_THR      (Default: -0.01)
        """
        if not _env_bool("OROMA_PTZ_MOTION_POLICY_DREAM_ENABLE", True):
            return

        max_rows = max(10, min(int(_env_int("OROMA_PTZ_MOTION_POLICY_MAX_ROWS", 500)), 5000))
        pos_thr = _env_float("OROMA_PTZ_MOTION_POLICY_POS_THR", 0.01)
        neg_thr = _env_float("OROMA_PTZ_MOTION_POLICY_NEG_THR", -0.01)
        if neg_thr > 0:
            neg_thr = -abs(float(neg_thr))

        ck_key = "ptz_motion_policy:last_reward_id"
        last_id = 0
        try:
            with sql_manager.get_conn() as conn:
                row = conn.execute("SELECT value FROM dream_state WHERE key=?", (ck_key,)).fetchone()
                if row:
                    last_id = int(row[0])
        except Exception:
            last_id = 0

        try:
            with sql_manager.get_conn() as conn:
                conn.row_factory = None
                rows = conn.execute(
                    """
                    SELECT id, created_at, reward, raw
                    FROM rewards_log
                    WHERE source=? AND id>?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    ("ptz/motion_focus", int(last_id), int(max_rows)),
                ).fetchall() or []
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ptz_motion_policy.fetch.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=300)
            return

        if not rows:
            return

        use_dbw = (db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"))
        conn = None
        new_last_id = last_id
        updated = 0
        skipped = 0

        def _parse_raw(raw_s: str) -> Dict[str, Any]:
            try:
                j = json.loads(raw_s) if raw_s else {}
                return j if isinstance(j, dict) else {}
            except Exception:
                return {}

        try:
            if not use_dbw:
                conn = sql_manager.get_conn()
                conn.execute("PRAGMA busy_timeout=5000")
            for rid, created_at, reward, raw_s in rows:
                try:
                    new_last_id = int(rid)
                    r = float(reward)
                except Exception:
                    skipped += 1
                    continue
                rawj = _parse_raw(raw_s)
                action = str(rawj.get("action") or "")
                state_hash = str(rawj.get("state_hash") or "")
                if not action or not state_hash:
                    skipped += 1
                    continue
                pos = 1 if r >= pos_thr else 0
                neg = 1 if r <= neg_thr else 0
                draw = 1 if (pos == 0 and neg == 0) else 0
                ts1 = int(created_at) if created_at else int(time.time())
                ins_sql = (
                    "INSERT OR IGNORE INTO policy_rules(namespace, state_hash, action, n, pos, neg, draw, q, last_ts) VALUES(?,?,?,?,?,?,?,?,?)"
                )
                upd_sql = (
                    "UPDATE policy_rules SET q=((q*CAST(n AS REAL))+?)/CAST((n+1) AS REAL), "
                    "n=(n+1), pos=(pos+?), neg=(neg+?), draw=(draw+?), last_ts=? "
                    "WHERE namespace=? AND state_hash=? AND action=?"
                )
                ins_params = ["ptz_motion", state_hash, action, 0, 0, 0, 0, 0.0, ts1]
                upd_params = [float(r), int(pos), int(neg), int(draw), ts1, "ptz_motion", state_hash, action]
                if use_dbw:
                    db_writer_client.exec_write(ins_sql, ins_params, tag="dream_worker.ptz_motion_policy.ins", priority="low", timeout_ms=2000, db="oroma")
                    db_writer_client.exec_write(upd_sql, upd_params, tag="dream_worker.ptz_motion_policy.upd", priority="low", timeout_ms=2000, db="oroma")
                else:
                    conn.execute(ins_sql, ins_params)
                    conn.execute(upd_sql, upd_params)
                updated += 1

            ck_sql = "INSERT OR REPLACE INTO dream_state(key, value) VALUES(?,?)"
            ck_params = [ck_key, str(int(new_last_id))]
            if use_dbw:
                db_writer_client.exec_write(ck_sql, ck_params, tag="dream_worker.ptz_motion_policy.ck", priority="low", timeout_ms=2000, db="oroma")
            else:
                conn.execute(ck_sql, ck_params)
                conn.commit()
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ptz_motion_policy.upsert.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=300)
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        try:
            self._metric_inc("ptz:motion_policy_updates", float(updated))
            self._metric_inc("ptz:motion_policy_skipped", float(skipped))
        except Exception:
            pass

    def _ptz_policy_from_audio_probe(self) -> None:
        """Aggregate PTZ audio_probe rewards into policy_rules (namespace='ptz_probe').

        Source
        ------
        rewards_log.source = 'ptz/audio_probe' (logged by core/ptz_attention_loop.py in PROBE mode)

        ENV
        ---
        OROMA_PTZ_PROBE_POLICY_DREAM_ENABLE (Default: 1)
        OROMA_PTZ_PROBE_POLICY_MAX_ROWS     (Default: 500)
        OROMA_PTZ_PROBE_POLICY_POS_THR      (Default: 0.5)
        OROMA_PTZ_PROBE_POLICY_NEG_THR      (Default: -0.01)
        """
        if not _env_bool("OROMA_PTZ_PROBE_POLICY_DREAM_ENABLE", True):
            return

        max_rows = max(10, min(int(_env_int("OROMA_PTZ_PROBE_POLICY_MAX_ROWS", 500)), 5000))
        pos_thr = _env_float("OROMA_PTZ_PROBE_POLICY_POS_THR", 0.5)
        neg_thr = _env_float("OROMA_PTZ_PROBE_POLICY_NEG_THR", -0.01)
        if neg_thr > 0:
            neg_thr = -abs(float(neg_thr))

        ck_key = "ptz_probe_policy:last_reward_id"
        last_id = 0
        try:
            with sql_manager.get_conn() as conn:
                row = conn.execute("SELECT value FROM dream_state WHERE key=?", (ck_key,)).fetchone()
                if row:
                    last_id = int(row[0])
        except Exception:
            last_id = 0

        try:
            with sql_manager.get_conn() as conn:
                conn.row_factory = None
                rows = conn.execute(
                    """
                    SELECT id, created_at, reward, raw
                    FROM rewards_log
                    WHERE source=? AND id>?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    ("ptz/audio_probe", int(last_id), int(max_rows)),
                ).fetchall() or []
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ptz_probe_policy.fetch.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=300)
            return

        if not rows:
            return

        use_dbw = (db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"))
        conn = None
        new_last_id = last_id
        updated = 0
        skipped = 0

        def _parse_raw(raw_s: str) -> Dict[str, Any]:
            try:
                j = json.loads(raw_s) if raw_s else {}
                return j if isinstance(j, dict) else {}
            except Exception:
                return {}

        try:
            if not use_dbw:
                conn = sql_manager.get_conn()
                conn.execute("PRAGMA busy_timeout=5000")
            for rid, created_at, reward, raw_s in rows:
                try:
                    new_last_id = int(rid)
                    r = float(reward)
                except Exception:
                    skipped += 1
                    continue
                rawj = _parse_raw(raw_s)
                action = str(rawj.get("action") or "")
                state_hash = str(rawj.get("state_hash") or "")
                if not action or not state_hash:
                    skipped += 1
                    continue
                pos = 1 if r >= pos_thr else 0
                neg = 1 if r <= neg_thr else 0
                draw = 1 if (pos == 0 and neg == 0) else 0
                ts1 = int(created_at) if created_at else int(time.time())
                ins_sql = (
                    "INSERT OR IGNORE INTO policy_rules(namespace, state_hash, action, n, pos, neg, draw, q, last_ts) VALUES(?,?,?,?,?,?,?,?,?)"
                )
                upd_sql = (
                    "UPDATE policy_rules SET q=((q*CAST(n AS REAL))+?)/CAST((n+1) AS REAL), "
                    "n=(n+1), pos=(pos+?), neg=(neg+?), draw=(draw+?), last_ts=? "
                    "WHERE namespace=? AND state_hash=? AND action=?"
                )
                ins_params = ["ptz_probe", state_hash, action, 0, 0, 0, 0, 0.0, ts1]
                upd_params = [float(r), int(pos), int(neg), int(draw), ts1, "ptz_probe", state_hash, action]
                if use_dbw:
                    db_writer_client.exec_write(ins_sql, ins_params, tag="dream_worker.ptz_probe_policy.ins", priority="low", timeout_ms=2000, db="oroma")
                    db_writer_client.exec_write(upd_sql, upd_params, tag="dream_worker.ptz_probe_policy.upd", priority="low", timeout_ms=2000, db="oroma")
                else:
                    conn.execute(ins_sql, ins_params)
                    conn.execute(upd_sql, upd_params)
                updated += 1

            ck_sql = "INSERT OR REPLACE INTO dream_state(key, value) VALUES(?,?)"
            ck_params = [ck_key, str(int(new_last_id))]
            if use_dbw:
                db_writer_client.exec_write(ck_sql, ck_params, tag="dream_worker.ptz_probe_policy.ck", priority="low", timeout_ms=2000, db="oroma")
            else:
                conn.execute(ck_sql, ck_params)
                conn.commit()
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.ptz_probe_policy.upsert.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=300)
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        try:
            self._metric_inc("ptz:probe_policy_updates", float(updated))
            self._metric_inc("ptz:probe_policy_skipped", float(skipped))
        except Exception:
            pass

    # ----------------------- SceneGraph aus Vision-Tokens ---------------------
    def _scenegraph_from_vision(self) -> None:
        """
        Optionaler Schritt: Vision-Tokens → MetaSnaps → SceneGraph.

        Wird ausgeführt, wenn:
          • ENABLE_DREAM_SCENEGRAPH True ist (ENV OROMA_DREAM_SCENEGRAPH)
          • core.scenegraph_builder erfolgreich importiert wurde.

        Parameter werden primär über ENV gesteuert:
          • OROMA_SCENEGRAPH_ORIGIN       (Default: "vision/token")
          • OROMA_SCENEGRAPH_MAX_CHAINS   (Default: 256)
          • OROMA_SCENEGRAPH_GROUP_SIZE   (Default: 32)
          • OROMA_SCENEGRAPH_MIN_QUALITY  (Default: 0.03)
          • OROMA_SCENEGRAPH_NAMESPACE    (Default: "scene:auto_meta:<origin_clean>")
        """
        if not ENABLE_DREAM_SCENEGRAPH:
            return
        if _scenegraph_builder is None:
            LOG.debug("SceneGraph-Builder nicht verfügbar – Schritt übersprungen.")
            return

        try:
            origin = os.environ.get("OROMA_SCENEGRAPH_ORIGIN", "vision/token")
            max_chains = _env_int("OROMA_SCENEGRAPH_MAX_CHAINS", 256)
            group_size = _env_int("OROMA_SCENEGRAPH_GROUP_SIZE", 32)

            min_q_env = (os.environ.get("OROMA_SCENEGRAPH_MIN_QUALITY", "") or "").strip()
            if min_q_env:
                try:
                    min_quality: Optional[float] = float(min_q_env)
                except Exception:
                    min_quality = 0.03
            else:
                min_quality = 0.03

            namespace_env = (os.environ.get("OROMA_SCENEGRAPH_NAMESPACE", "") or "").strip()
            namespace = namespace_env or None

            res = _scenegraph_builder.bootstrap_scenegraph_from_tokens(
                origin=origin,
                max_chains=max_chains,
                group_size=group_size,
                min_quality=min_quality,
                since_ts=None,
                max_meta=64,
                max_chains_per_meta=16,
                namespace=namespace,
                persist=True,
                notes=f"DreamWorker auto-run (origin={origin})",
                verbose=False,
            )
            sg = res.get("scenegraph_result") or {}
            meta = sg.get("meta") or {}
            graph_id = meta.get("saved_id") or sg.get("graph_id")
            num_nodes = meta.get("num_nodes") or sg.get("num_nodes")
            num_edges = meta.get("num_edges") or sg.get("num_edges")

            LOG.info(
                "Dream-SceneGraph (origin=%s max_chains=%s group_size=%s min_quality=%s): ok=%s graph_id=%s nodes=%s edges=%s",
                origin,
                max_chains,
                group_size,
                min_quality,
                bool(res.get("ok")),
                graph_id,
                num_nodes,
                num_edges,
            )
            # Dream-Eff: auch wenn Replay leer ist, wird ein Dream-Reward-Event geschrieben.
            if bool(res.get("ok")):
                _log_reward_best_effort(
                    source="dream/replay",
                    step=0,
                    reward=0.01,
                    raw={"step": "scenegraph", "origin": origin, "graph_id": graph_id, "nodes": num_nodes, "edges": num_edges},
                    tag="dream:scenegraph",
                    ts=int(time.time()),
                )

        except Exception as e:
            LOG.warning("Dream-SceneGraph aus Vision-Tokens fehlgeschlagen: %s", e)

    # --------------------- ObjectGraph aus SceneGraphs -----------------------
    def _objectgraph_from_scenegraph(self) -> None:
        """
        Optionaler Schritt: SceneGraphs → ObjectGraph.

        Wird ausgeführt, wenn:
          • ENABLE_DREAM_OBJECTGRAPH True ist (ENV OROMA_DREAM_OBJECTGRAPH)
          • core.objectgraph_builder erfolgreich importiert wurde.

        Parameter werden primär über ENV gesteuert:
          • OROMA_OBJECTGRAPH_SRC_NS        (Default: "scene:auto_meta:")
          • OROMA_OBJECTGRAPH_TARGET_NS     (Default: "object:auto:vision")
          • OROMA_OBJECTGRAPH_MAX_GRAPHS    (Default: 32)
          • OROMA_OBJECTGRAPH_MIN_QUALITY   (Default: 0.0)
        """
        if not ENABLE_DREAM_OBJECTGRAPH:
            return
        if _objectgraph_builder is None:
            LOG.debug("ObjectGraph-Builder nicht verfügbar – Schritt übersprungen.")
            return

        try:
            src_ns = os.environ.get("OROMA_OBJECTGRAPH_SRC_NS", "scene:auto_meta:")
            target_ns = os.environ.get("OROMA_OBJECTGRAPH_TARGET_NS", "object:auto:vision")
            max_graphs = _env_int("OROMA_OBJECTGRAPH_MAX_GRAPHS", 32)
            min_quality = _env_float("OROMA_OBJECTGRAPH_MIN_QUALITY", 0.0)

            res = _objectgraph_builder.auto_objectgraph_from_scenegraphs(
                source_namespace_prefix=src_ns,
                target_namespace=target_ns,
                max_graphs=max_graphs,
                min_quality=min_quality,
                persist=True,
                notes=f"DreamWorker objectgraph (src={src_ns})",
            )
            meta = res.get("meta") or {}
            stats = meta.get("stats") or {}
            graph_id = meta.get("saved_id")
            objects = stats.get("objects")
            edges = stats.get("object_edges")
            graphs_used = stats.get("graphs_used")

            LOG.info(
                "Dream-ObjectGraph (src_ns=%s): ok=%s graph_id=%s objects=%s edges=%s graphs_used=%s",
                src_ns,
                bool(res.get("ok")),
                graph_id,
                objects,
                edges,
                graphs_used,
            )
            # Dream-Eff: best-effort Reward-Event für ObjectGraph-Step.
            if bool(res.get("ok")):
                _log_reward_best_effort(
                    source="dream/replay",
                    step=0,
                    reward=0.01,
                    raw={"step": "objectgraph", "src_ns": src_ns, "graph_id": graph_id, "objects": objects, "edges": edges, "graphs_used": graphs_used},
                    tag="dream:objectgraph",
                    ts=int(time.time()),
                )

        except Exception as e:
            LOG.warning("Dream-ObjectGraph aus SceneGraphs fehlgeschlagen: %s", e)


# ---------------- ObjectExtractor: ObjectGraph→Tabellen-Projection ----------
def _objectextractor_from_scenegraphs(self) -> None:
    """
    Optionaler Schritt: schreibt aus `scenegraphs` (ObjectGraph/SceneGraph JSON)
    eine tabellarische Projektion in:

      • object_nodes
      • object_relations

    Warum:
      - ObjectGraphs werden als JSON in `scenegraphs` gespeichert (Namespace i.d.R. object:auto:*).
      - Die UI (/objects) und Tools profitieren stark von einer expliziten Tabellenform.

    ENV:
      • OROMA_DREAM_OBJECT_EXTRACTOR        0/1 (Default: 1)
      • OROMA_OBJECT_EXTRACTOR_NAMESPACES   CSV Liste (exakt oder Prefix: 'object:auto:' / 'object:auto:*')
      • OROMA_OBJECT_EXTRACTOR_MAX_GRAPHS   Default: 1   (neueste zuerst)

    Robustheit:
      - Wenn keine Treffer für die konfigurierten Namespaces existieren,
        fällt der Step automatisch auf Prefix 'object:auto:' zurück (max_graphs bleibt gleich).
    """
    if not ENABLE_DREAM_OBJECT_EXTRACTOR:
        return
    if _object_extractor is None:
        LOG.debug("ObjectExtractor nicht verfügbar – Schritt übersprungen.")
        return

    try:
        ns_env = (os.environ.get("OROMA_OBJECT_EXTRACTOR_NAMESPACES", "") or "").strip()
        if ns_env:
            namespaces = [p.strip() for p in ns_env.split(",") if p.strip()]
        else:
            target = (os.environ.get("OROMA_OBJECTGRAPH_TARGET_NS", "") or "").strip()
            namespaces = [target] if target else ["object:auto:"]

        max_graphs = _env_int("OROMA_OBJECT_EXTRACTOR_MAX_GRAPHS", 1)

        # Pre-Check: gibt es überhaupt passende SceneGraphs?
        def _ns_to_where(ns_list: List[str]) -> tuple[str, List[str]]:
            exact: List[str] = []
            like: List[str] = []
            for ns in ns_list:
                s = (ns or "").strip()
                if not s:
                    continue
                if s.endswith("*"):
                    like.append(s[:-1] + "%")
                elif "%" in s:
                    like.append(s)
                elif s.endswith(":"):
                    like.append(s + "%")
                else:
                    exact.append(s)
            clauses: List[str] = []
            params: List[str] = []
            if exact:
                clauses.append("namespace IN (" + ",".join("?" for _ in exact) + ")")
                params.extend(exact)
            if like:
                clauses.append("(" + " OR ".join("namespace LIKE ?" for _ in like) + ")")
                params.extend(like)
            where = " OR ".join(clauses) if clauses else "1=0"
            return where, params

        where, params = _ns_to_where(namespaces)
        match_cnt = 0
        try:
            with sql_manager.get_conn() as conn:
                row = conn.execute(f"SELECT COUNT(*) AS c FROM scenegraphs WHERE {where}", params).fetchone()
                if isinstance(row, dict):
                    match_cnt = int(row.get("c") or 0)
                else:
                    match_cnt = int(row[0] if row else 0)
        except Exception:
            match_cnt = 0

        if match_cnt == 0:
            LOG.warning(
                "Dream-ObjectExtractor: keine SceneGraphs passend zu namespaces=%s. "
                "Fallback auf Prefix 'object:auto:' (max_graphs=%s).",
                ",".join(namespaces),
                max_graphs,
            )
            namespaces = ["object:auto:"]

        # Idempotenz: ObjectExtractor nur laufen lassen, wenn seit dem letzten
        # erfolgreichen Run ein neuer passender SceneGraph/ObjectGraph
        # hinzugekommen ist.
        #
        # Warum:
        #   - spart IO/CPU (Pi/SSD/SD) und verhindert unnötige Rebuilds
        #   - hält Logs sauber (kein "jede Nacht alles neu")
        #
        # Persistenz:
        #   - wir speichern den zuletzt extrahierten scenegraphs.id pro
        #     Namespace-Set in der kleinen KV-Tabelle dream_state.
        # -----------------------------------------------------------------
        state_key = "object_extractor:last_scenegraph_id:" + "|".join(namespaces)

        latest_id = None
        last_id = 0
        try:
            where2, params2 = _ns_to_where(namespaces)
            with sql_manager.get_conn() as conn:
                row = conn.execute(
                    f"SELECT MAX(id) AS max_id FROM scenegraphs WHERE {where2}",
                    params2,
                ).fetchone()
                if isinstance(row, dict):
                    latest_id = row.get("max_id")
                else:
                    latest_id = row[0] if row else None

                # -----------------------------------------------------------------
                # Checkpoint lesen:
                #   - Primär: exakter Key für das aktuelle Namespace-Set.
                #   - Fallback: wenn wir mit Prefix-Namespaces arbeiten (z.B. "object:auto:"),
                #     kann bereits ein spezifischer Checkpoint existieren (z.B. "object:auto:vision").
                #     Dann nehmen wir den MAX-Wert aller kompatiblen Keys, damit "nur wenn neu"
                #     auch bei Prefix-Namespaces korrekt greift.
                # -----------------------------------------------------------------
                row2 = conn.execute(
                    "SELECT value FROM dream_state WHERE key=?",
                    (state_key,),
                ).fetchone()
                has_exact_ckpt = row2 is not None
                if isinstance(row2, dict):
                    last_val = row2.get("value")
                else:
                    last_val = row2[0] if row2 else None
                try:
                    last_id = int(str(last_val)) if last_val is not None else 0
                except Exception:
                    last_id = 0
                
                # Fallback nur dann, wenn kein exakter Checkpoint existiert *und* mindestens ein
                # Namespace wie ein Prefix aussieht (endet mit ':').
                if (not has_exact_ckpt) and any(str(ns).endswith(':') for ns in namespaces):
                    try:
                        key_prefix = "object_extractor:last_scenegraph_id:"
                        rows = conn.execute(
                            "SELECT key, value FROM dream_state WHERE key LIKE ?",
                            (key_prefix + "%",),
                        ).fetchall()
                        compat_max = 0
                        for r in rows or []:
                            k = r.get("key") if isinstance(r, dict) else r[0]
                            v = r.get("value") if isinstance(r, dict) else r[1]
                            if not isinstance(k, str) or not k.startswith(key_prefix):
                                continue
                            suffix = k[len(key_prefix):]
                            stored = suffix.split("|") if suffix else []
                            # Kompatibilität: gleiche Anzahl, je Paar entweder exakt gleich
                            # oder Prefix-Match in beide Richtungen.
                            if len(stored) != len(namespaces):
                                continue
                            ok = True
                            for want_ns, have_ns in zip([str(x) for x in namespaces], [str(x) for x in stored]):
                                if want_ns == have_ns:
                                    continue
                                if want_ns.endswith(":") and have_ns.startswith(want_ns):
                                    continue
                                if have_ns.endswith(":") and want_ns.startswith(have_ns):
                                    continue
                                ok = False
                                break
                            if not ok:
                                continue
                            try:
                                vi = int(str(v)) if v is not None else 0
                            except Exception:
                                vi = 0
                            if vi > compat_max:
                                compat_max = vi
                        if compat_max > 0:
                            last_id = compat_max
                    except Exception as e:
                        log_suppressed(_BOOT_LOG, key="dream_worker.pass.21", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        except Exception:
            latest_id = None
            last_id = 0

        if latest_id is None:
            LOG.info(
                "Dream-ObjectExtractor: skip (kein MAX(id) für namespaces=%s)",
                ",".join(namespaces),
            )
            return

        try:
            latest_id_int = int(latest_id)
        except Exception:
            latest_id_int = 0

        if latest_id_int <= 0:
            LOG.info(
                "Dream-ObjectExtractor: skip (latest_id<=0) namespaces=%s",
                ",".join(namespaces),
            )
            return

        if last_id >= latest_id_int:
            LOG.info(
                "Dream-ObjectExtractor: skip (nichts neu) namespaces=%s last_id=%s latest_id=%s",
                ",".join(namespaces),
                last_id,
                latest_id_int,
            )
            return

        LOG.info(
            "Dream-ObjectExtractor: neu erkannt → run (namespaces=%s last_id=%s latest_id=%s max_graphs=%s)",
            ",".join(namespaces),
            last_id,
            latest_id_int,
            max_graphs,
        )

        _object_extractor.run_extractor(  # type: ignore[attr-defined]
            namespaces=namespaces,
            max_graphs=max_graphs,
            dry_run=False,
            verbose=False,
            db_path=None,
        )

        # Checkpoint: zuletzt extrahierten scenegraphs.id speichern
        try:
            use_dbw = (db_writer_client is not None and os.environ.get("OROMA_DBW_ENABLE", "0") not in ("0", "false", "False", "no", "off"))
            if use_dbw:
                db_writer_client.exec_write(
                    "INSERT OR REPLACE INTO dream_state (key, value, ts) VALUES (?, ?, ?)",
                    [state_key, str(latest_id_int), int(time.time())],
                    tag="dream_worker.objectextractor.ck",
                    priority="low",
                    timeout_ms=2000,
                    db="oroma",
                )
            else:
                with sql_manager.get_conn() as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS dream_state (key TEXT PRIMARY KEY, value TEXT, ts INTEGER)"
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO dream_state (key, value, ts) VALUES (?, ?, ?)",
                        (state_key, str(latest_id_int), int(time.time())),
                    )
                    conn.commit()
        except Exception as e:
            log_suppressed(_BOOT_LOG, key="dream_worker.pass.22", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

        # Summary (Counts)
        try:
            with sql_manager.get_conn() as conn:
                r1 = conn.execute("SELECT COUNT(*) AS c FROM object_nodes").fetchone()
                r2 = conn.execute("SELECT COUNT(*) AS c FROM object_relations").fetchone()

                if isinstance(r1, dict):
                    n_nodes = int(r1.get("c") or 0)
                else:
                    n_nodes = int(r1[0] if r1 else 0)

                if isinstance(r2, dict):
                    n_rels = int(r2.get("c") or 0)
                else:
                    n_rels = int(r2[0] if r2 else 0)

            LOG.info(
                "Dream-ObjectExtractor: namespaces=%s max_graphs=%s → object_nodes=%s object_relations=%s",
                ",".join(namespaces),
                max_graphs,
                n_nodes,
                n_rels,
            )
        except Exception:
            LOG.info(
                "Dream-ObjectExtractor: namespaces=%s max_graphs=%s (Counts nicht verfügbar)",
                ",".join(namespaces),
                max_graphs,
            )

    except Exception as e:
        LOG.warning("Dream-ObjectExtractor fehlgeschlagen: %s", e)


# =============================================================================
# CLI
# =============================================================================

def _parse_args():
    ap = argparse.ArgumentParser(description="ORÓMA DreamWorker")
    ap.add_argument("--interval", type=int, default=0, help="0 = Single-Run (Timer), >0 = Loop-Sekunden")
    ap.add_argument("--verbose", action="store_true", help="Konsole auf DEBUG schalten")
    ap.add_argument("--filter", type=str, default="", help='Substring-Filter auf chain.metadata (z. B. "game:tictactoe")')
    return ap.parse_args()



# -------------------------------------------------------------------------
# Runtime-Bindung: Modul-Scope Helper -> DreamWorker Methode
# -------------------------------------------------------------------------
# Hinweis:
#   In einigen ZIP-Ständen wurde _objectextractor_from_scenegraphs() durch
#   einen Merge versehentlich auf Modul-Ebene definiert (ohne Einrückung)
#   und damit NICHT als Instanzmethode der DreamWorker-Klasse registriert.
#   Das führt zur Runtime-Exception:
#       AttributeError: 'DreamWorker' object has no attribute '_objectextractor_from_scenegraphs'
#
#   Um das System robust zu halten (und ohne destruktives Umsortieren von
#   großen Codeblöcken), binden wir den Helper hier explizit in die Klasse
#   ein, sobald beide Symbole verfügbar sind.
#
#   Effekt:
#     - DreamWorker kann den Projection-Step zuverlässig aufrufen.
#     - Bestehende Aufrufe/Signaturen bleiben kompatibel.
# -------------------------------------------------------------------------
try:
    if "DreamWorker" in globals() and "_objectextractor_from_scenegraphs" in globals():
        DreamWorker._objectextractor_from_scenegraphs = _objectextractor_from_scenegraphs  # type: ignore[attr-defined]
except Exception as e:
    log_suppressed(_BOOT_LOG, key="dream_worker.pass.23", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
if __name__ == "__main__":
    args = _parse_args()

    # Optional: Verbose-Logging aktivieren (nur Konsole & .out – .err bleibt WARN+)
    if args.verbose:
        LOG.setLevel(logging.DEBUG)
        for h in LOG.handlers:
            try:
                # .err-Handler auf WARN lassen
                if isinstance(h, logging.FileHandler) and h.baseFilename.endswith("dream.err.log"):
                    h.setLevel(logging.WARNING)
                else:
                    h.setLevel(logging.DEBUG)
            except Exception as e:
                log_suppressed(_BOOT_LOG, key="dream_worker.pass.24", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        LOG.debug("Verbose-Mode aktiv")

    # Hauptlauf (Night/Dream-Loop)
    mem = LangzeitGedaechtnis()
    wk = DreamWorker(
        mem,
        interval=int(args.interval),
        meta_filter_substr=(args.filter or None)
    )
    wk.run()

    # ------------------------------------------------------------
    # Optionaler Schritt NACH dem Night-Run:
    # Kontrastives Light-SSL (Audio→Video) trainieren, wenn aktiviert.
    # ENV: OROMA_SSL_LIGHT=1   (Default: aus)
    # ------------------------------------------------------------
    try:
        if os.getenv("OROMA_SSL_LIGHT", "0").lower() in ("1", "true", "yes"):
            try:
                from core.ssl_contrastive import train_linear_W, save_W
                W = train_linear_W(int(time.time()) - 86400)  # Paare aus den letzten 24h
                if W is not None:
                    save_W(W)  # schreibt nach /opt/ai/oroma/data/ssl_W_audio2video.json
                    LOG.info("Light-SSL: Mapping W (9x9) gespeichert.")
                else:
                    LOG.info("Light-SSL: zu wenige Audio/Video-Paare – übersprungen.")
            except Exception as e:
                LOG.warning("Light-SSL: Fehler beim Training/Speichern: %s", e)
    except Exception as e:
        # Niemals eskalieren – DreamWorker-Lauf soll dadurch nicht fehlschlagen
        log_suppressed(
            logging.getLogger(__name__),
            key="core.dream_worker.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

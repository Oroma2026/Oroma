#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/snapchain.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
#            Offline-First · Headless · SQLite-First · Edge Runtime
# Modul:     SnapChain – episodische Ketten (Zeitsequenzen) aus SnapPatterns
#            + optionale Spatio-Temporal-Spuren + Knowledge/RAG-light Bridge
# Version:   (unverändert wie im File: SCHEMA_VERSION Formatmarker in metadata["version"])
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
# 0) ZWECK / SYSTEMROLLE
# ──────────────────────
# SnapChain ist ORÓMAs episodische Gedächtniseinheit: eine **geordnete Sequenz**
# von SnapPatterns (Verdichtungen aus Snaps/Features). Sie konserviert die
# Reihenfolge von Ereignissen als “Episode” und ist damit Grundlage für:
#   - Replay (Wiederholung/Training aus Episoden)
#   - Dream/Konsolidierung (Verdichtung, Binding, Qualitäts-Updates)
#   - Transfer (episodische Muster wiederverwenden)
#   - Explainability (Warum wurde etwas als zusammengehörig betrachtet?)
#
# Wichtig: SnapChain ist bewusst **headless** und robust gegen Partial-/Legacy-Inputs.
#
# 1) FORMATMARKER (SCHEMA_VERSION) VS. PROJEKTVERSION
# ───────────────────────────────────────────────────
# Dieses Modul verwendet `SCHEMA_VERSION` als **Wire-/Formatmarker** und speichert
# diesen in `metadata["version"]`. Das ist NICHT die Projektversion.
# Ziel: Forward-Compatibility (Superset-Format), damit alte Chains weiterhin geladen
# werden können und neue Felder optional bleiben.
#
# 2) DATENMODELL (KERN)
# ────────────────────
#   - patterns: List[SnapPattern]      → Kette der Verdichtungen
#   - metadata: Dict[str, Any]         → freie Meta + Formatmarker + Kontextspuren
#   - resonance_score / reward_score   → optionale Score-Felder (nicht automatisch persistent)
#   - episodic_id / explain_trace      → optionale externe ID + Debug/Explain-Spuren
#   - ts_created                       → Erstellzeit (epoch)
#
# 3) APPEND-KONZEPT: ROBUSTE KOERZION
# ───────────────────────────────────
# SnapChain akzeptiert beim Append mehrere Inputtypen (best-effort):
#   - SnapPattern
#   - Snap
#   - Feature-Vektor (List[float])
#   - snap-ähnliches Dict
# Intern wird alles zu SnapPattern koerziert, um eine einheitliche Episode zu erhalten.
#
# 4) KONTEXT-APPEND (OPTIONAL): TIME + SPACE
# ─────────────────────────────────────────
# Zusätzlich zu plain append() gibt es append_with_context(...), das – wenn verfügbar –
# Kontextspuren in `metadata` pflegt:
#
# Timing:
#   - metadata["timing"]["ts"]         → ts pro Schritt
#   - metadata["timing"]["delta_time"] → dt zum vorherigen Schritt
#
# Space (nur wenn core.spatial_index verfügbar ist):
#   - metadata["space"]["waypoints"]   → IDs aus spatial index
#   - metadata["space"]["relations"]   → Beziehungen zwischen Waypoints
#
# Dadurch wird SnapChain zur Brücke zwischen Episoden und einfachen Raum-/Zeit-Bezügen,
# ohne harte Abhängigkeiten (optional, fail-safe).
#
# 5) KNOWLEDGE / RAG-LIGHT (OFFLINE-FREUNDLICH)
# ─────────────────────────────────────────────
# SnapChain kann Text/Knowledge als Pattern aufnehmen und einfache Query-Helpers anbieten
# (keine Cloud-Abhängigkeit). Ziel: “RAG-light” in Edge-Form:
#   - add_text / add_knowledge_snap
#   - ask_knowledge / synthesize_answer (best-effort, robust)
#
# 6) SIMILARITY / AGGREGATION
# ──────────────────────────
# - feature_centroid(): Centroid über Pattern-Centroids (tolerant bei leeren Vektoren)
# - score_resonance(...): Cosine-basierte Resonanz gegen andere Chain/Vector
# Optional NumPy-Beschleunigung, aber nicht erforderlich.
#
# 7) SERIALISIERUNG (ROBUST)
# ─────────────────────────
# - to_dict / from_dict: JSON-freundlich, legacy tolerant
# - as_blob / from_blob: kompakt (zlib/JSON-Pack), für DB/IPC/Replay
#
# Debug-Tracing (nur bei Bedarf):
#   - OROMA_SNAPCHAIN_TRACE_APPEND
#   - OROMA_SNAPCHAIN_TRACE_SERIALIZE
#
# 8) DB-INTEGRATION (HINWEIS)
# ──────────────────────────
# Dieses Modul beschreibt SnapChain als Datenstruktur. Die Persistenz (snapchains Tabelle)
# erfolgt in ORÓMA typischerweise über core.sql_manager (und ggf. DBWriter im Single-Writer Modus).
#
# 9) PRODUKTIONSINVARIANTEN
# ────────────────────────
# - Keine stillen Crashes bei Partial-Inputs (Legacy-Dicts, leere Vektoren, fehlende Felder).
# - Space/Timing sind optional: wenn Module fehlen, wird sauber degradiert.
# - Logging ist sichtbar, aber gedrosselt (keine Log-Explosion im 24/7 Betrieb).
#
# =============================================================================
# END HEADER
# =============================================================================
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from core import log_guard
# =============================================================================
# Optional: NumPy (Performance)
# =============================================================================
try:
    import numpy as _np  # type: ignore
    _HAS_NP = True
except Exception:
    _HAS_NP = False
    _np = None  # type: ignore

# =============================================================================
# Kernklassen
# =============================================================================
from .snap import Snap
from .snappattern import SnapPattern

# =============================================================================
# Optionale Bridges (Fusion / RAG)
# =============================================================================
try:
    from core.fusion import FusionPack  # type: ignore
except Exception:
    FusionPack = None  # type: ignore

try:
    from core.book_import import RAGStore, synthesize_answer  # type: ignore
except Exception:
    RAGStore = None  # type: ignore

    def synthesize_answer(question: str, passages: List[str]) -> str:  # type: ignore
        if passages:
            lines = ["Relevante Stellen:"]
            lines += [f"[{i+1}] {p}" for i, p in enumerate(passages)]
            return "\n".join(lines)
        return f"Keine relevanten Stellen gefunden für: {question}"

# =============================================================================
# Optionale Spatio-Temporal-Bridge (Raumgraph)
# =============================================================================
try:
    # core.spatial_index stellt u.a. bereit:
    #   ensure_schema(), add_point(x,y,z,label) -> point_id
    #   relate(a_id, b_id) -> edge_id
    from core import spatial_index  # type: ignore
    _HAS_SPATIAL = True
except Exception:
    spatial_index = None  # type: ignore
    _HAS_SPATIAL = False

# =============================================================================
# Logging
# =============================================================================
logger = logging.getLogger("oroma.snapchain")
if not logger.handlers and str(os.environ.get("OROMA_SNAPCHAIN_ATTACH_STDERR", "0") or "0").strip().lower() in ("1", "true", "yes", "on"):
    h = logging.StreamHandler()
    f = logging.Formatter("[snapchain] %(levelname)s: %(message)s")
    h.setFormatter(f)
    logger.addHandler(h)
_lvl = os.environ.get("OROMA_SNAPCHAIN_LOGLEVEL", "WARNING").upper()
logger.setLevel(getattr(logging, _lvl, logging.WARNING))
logger.propagate = True
_TRACE_APPEND = str(os.environ.get("OROMA_SNAPCHAIN_TRACE_APPEND", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
_TRACE_SERIALIZE = str(os.environ.get("OROMA_SNAPCHAIN_TRACE_SERIALIZE", "0") or "0").strip().lower() in ("1", "true", "yes", "on")

# =============================================================================
# Konstanten
# =============================================================================
EPS = 1e-8
SCHEMA_VERSION = "3.8.10"
SNAPCHAIN_DIR = os.environ.get("OROMA_SNAPCHAINS", "/opt/ai/oroma/data/snapchains")

# =============================================================================
# Hilfsfunktionen – Koerzierung & Serialisierung
# =============================================================================

def _as_float_list(x: Iterable[Any]) -> List[float]:
    return [float(v) for v in x]

def _is_num_seq(x: Any) -> bool:
    if not isinstance(x, (list, tuple)):
        return False
    return all(isinstance(v, (int, float)) for v in x)

def _maybe_board_to_vec(x: Any) -> Optional[List[float]]:
    # erkennt TicTacToe-Board ['X','O','',...] → [1,-1,0,...]
    if isinstance(x, (list, tuple)) and len(x) == 9 and all(isinstance(v, str) for v in x):
        m = {"X": 1.0, "O": -1.0}
        try:
            return [m.get(v, 0.0) for v in x]
        except Exception:
            return None
    return None

def _coerce_features(obj: Any) -> Optional[List[float]]:
    """Extrahiert einen Feature-Vektor aus diversen Eingaben."""
    # direkt Liste von Zahlen
    if _is_num_seq(obj):
        return _as_float_list(obj)

    # Board-Stringliste
    v = _maybe_board_to_vec(obj)
    if v is not None:
        return v

    # Dict-Formen
    if isinstance(obj, dict):
        for key in ("features", "vector", "feats", "centroid"):
            if key in obj and _is_num_seq(obj[key]):
                return _as_float_list(obj[key])
        if "board" in obj:
            v = _maybe_board_to_vec(obj["board"])
            if v is not None:
                return v
    return None

def _snap_to_dict_safe(s: Snap) -> Dict[str, Any]:
    if hasattr(s, "to_dict"):
        try:
            d = s.to_dict()  # type: ignore
            if isinstance(d, dict):
                return d
        except Exception as e:
            log_guard.log_suppressed(logger, key="snapchain.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return {
        "features": list(getattr(s, "features", []) or []),
        "metadata": dict(getattr(s, "metadata", {}) or {}),
        "content": getattr(s, "content", None),
    }

def _snap_from_dict_safe(d: Dict[str, Any]) -> Snap:
    if hasattr(Snap, "from_dict"):
        try:
            return Snap.from_dict(d)  # type: ignore
        except Exception as e:
            log_guard.log_suppressed(logger, key="snapchain.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return Snap(
        features=list(d.get("features", []) or []),
        metadata=dict(d.get("metadata", {}) or {}),
        content=d.get("content"),
    )

def _pattern_centroid_safe(p: SnapPattern) -> List[float]:
    # 1) vorhandene Centroid?
    c = getattr(p, "centroid", None)
    if isinstance(c, list) and c:
        return [float(x) for x in c]

    # 2) aus .snaps Feature-Mittel bilden
    snaps = list(getattr(p, "snaps", [])) or list(getattr(p, "events", [])) or []
    if snaps:
        vecs: List[List[float]] = []
        for s in snaps:
            feats = list(getattr(s, "features", []) or [])
            if feats:
                vecs.append([float(x) for x in feats])
        if vecs:
            d = min(len(v) for v in vecs if v)
            if d > 0:
                if _HAS_NP:
                    arr = _np.asarray([v[:d] for v in vecs], dtype=_np.float32)
                    return _np.mean(arr, axis=0).astype(_np.float32).tolist()
                acc = [0.0] * d
                for v in vecs:
                    for i in range(d):
                        acc[i] += float(v[i])
                n = max(1, len(vecs))
                return [v / n for v in acc]

    # 3) aus .patterns (Vektorlisten)
    plist = list(getattr(p, "patterns", []) or [])
    if plist and all(_is_num_seq(v) for v in plist):
        d = min(len(v) for v in plist if v)
        if d > 0:
            if _HAS_NP:
                arr = _np.asarray([_as_float_list(v[:d]) for v in plist], dtype=_np.float32)
                return _np.mean(arr, axis=0).astype(_np.float32).tolist()
            acc = [0.0] * d
            for v in plist:
                vv = _as_float_list(v)
                for i in range(d):
                    acc[i] += vv[i]
            n = max(1, len(plist))
            return [v / n for v in acc]

    return []

def _pattern_to_dict_safe(p: SnapPattern) -> Dict[str, Any]:
    # bevorzugt native to_dict()
    if hasattr(p, "to_dict"):
        try:
            d = p.to_dict()  # type: ignore
            if isinstance(d, dict) and (d.get("patterns") or d.get("snaps") or d.get("events")):
                return d
        except Exception as e:
            log_guard.log_suppressed(logger, key="snapchain.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    md = dict(getattr(p, "metadata", {}) or {})

    # hat .snaps?
    snaps = list(getattr(p, "snaps", [])) or list(getattr(p, "events", [])) or []
    if snaps:
        return {
            "snaps": [_snap_to_dict_safe(s) for s in snaps],
            "metadata": md,
            "centroid": _pattern_centroid_safe(p),
            "num_snaps": len(snaps),
        }

    # hat .patterns (Vektorlisten)?
    plist = list(getattr(p, "patterns", []) or [])
    if plist and all(_is_num_seq(v) for v in plist):
        d = len(plist[0]) if plist and _is_num_seq(plist[0]) else 0
        return {
            "patterns": [_as_float_list(v) for v in plist],
            "metadata": md,
            "centroid": _pattern_centroid_safe(p),
            "feature_dim": d,
            "num_snaps": len(plist),
        }

    # Fallback: minimal
    return {
        "metadata": md,
        "centroid": _pattern_centroid_safe(p),
    }

def _dict_to_pattern_safe(d: Dict[str, Any]) -> Optional[SnapPattern]:
    """Erzeugt aus gängigen Dict-Formen ein SnapPattern."""
    # 1) reine Feature-Dicts
    v = _coerce_features(d)
    if v is not None:
        return SnapPattern.from_snaps([v], metadata=dict(d.get("metadata", {}) or {}))

    # 2) explizite Pattern-Formen
    if "patterns" in d and isinstance(d["patterns"], list):
        # erwartet Liste von Vektoren
        plist = []
        for item in d["patterns"]:
            vv = _coerce_features(item)
            if vv is None:
                return None
            plist.append(vv)
        return SnapPattern.from_snaps(plist, metadata=dict(d.get("metadata", {}) or {}))

    if "snaps" in d and isinstance(d["snaps"], list):
        snaps = []
        for sd in d["snaps"]:
            if isinstance(sd, dict):
                snaps.append(_snap_from_dict_safe(sd))
        if snaps:
            return SnapPattern.from_snaps(snaps, metadata=dict(d.get("metadata", {}) or {}))

    if "events" in d and isinstance(d["events"], list):
        snaps = []
        for sd in d["events"]:
            if isinstance(sd, dict):
                snaps.append(_snap_from_dict_safe(sd))
        if snaps:
            return SnapPattern.from_snaps(snaps, metadata=dict(d.get("metadata", {}) or {}))

    # 3) Text-Only → Snap ohne Features
    if "text" in d and isinstance(d["text"], str):
        s = Snap(features=[], metadata={"kind": "text", **dict(d.get("metadata", {}) or {})}, content=d["text"])
        return SnapPattern.from_snaps([s], metadata={})

    return None

# =============================================================================
# Klasse: SnapChain
# =============================================================================

class SnapChain:
    """Kette von SnapPatterns plus Analyse-/IO-/Knowledge- und Spatio-Temporal-Utilities."""

    # -------------------------------------------------------------------------
    # Interner Spatio-Temporal-Init
    # -------------------------------------------------------------------------
    def _init_spatio_temporal_state(self) -> None:
        """
        Sorgt für robuste Defaults in metadata["timing"]/["space"] und
        rekonstruiert internen Laufzustand (_last_ts, _last_point_id), falls
        bereits Daten vorhanden sind (z.B. nach from_dict()).
        """
        # Timing-Block
        timing = self.metadata.get("timing")
        if not isinstance(timing, dict):
            timing = {}
            self.metadata["timing"] = timing
        ts_list = timing.setdefault("ts", [])
        dt_list = timing.setdefault("delta_time", [])
        if not isinstance(ts_list, list):
            ts_list = []
            timing["ts"] = ts_list
        if not isinstance(dt_list, list):
            dt_list = []
            timing["delta_time"] = dt_list

        # Space-Block
        space = self.metadata.get("space")
        if not isinstance(space, dict):
            space = {}
            self.metadata["space"] = space
        waypoints = space.setdefault("waypoints", [])
        relations = space.setdefault("relations", [])
        if not isinstance(waypoints, list):
            waypoints = []
            space["waypoints"] = waypoints
        if not isinstance(relations, list):
            relations = []
            space["relations"] = relations

        # Interner Laufzustand
        self._last_ts: Optional[float] = None
        if ts_list:
            try:
                self._last_ts = float(ts_list[-1])
            except Exception:
                self._last_ts = None

        self._last_point_id: Optional[int] = None
        if waypoints:
            try:
                self._last_point_id = int(waypoints[-1])
            except Exception:
                self._last_point_id = None

    def __init__(
        self,
        patterns: Optional[List[Union[Snap, SnapPattern, List[float], Dict[str, Any]]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.patterns: List[SnapPattern] = []
        if patterns:
            for p in patterns:
                self._append_any(p)

        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.metadata.setdefault("version", SCHEMA_VERSION)

        self.resonance_score: float = 0.0
        self.reward_score: float = 0.0

        self.episodic_id: Optional[str] = None
        self.explain_trace: Optional[Dict[str, Any]] = None

        self.ts_created: float = time.time()

        # Spatio-Temporal-Struktur vorbereiten (robuste Defaults + Laufzustand)
        self._init_spatio_temporal_state()

    # ---------------- Container-API ----------------
    def __len__(self) -> int:
        return len(self.patterns)

    def __iter__(self):
        return iter(self.patterns)

    def clear(self) -> None:
        self.patterns.clear()

        # Bei Clear auch Spatio-Temporal-Infos zurücksetzen
        timing = self.metadata.get("timing")
        if isinstance(timing, dict):
            timing["ts"] = []
            timing["delta_time"] = []
        space = self.metadata.get("space")
        if isinstance(space, dict):
            space["waypoints"] = []
            space["relations"] = []
        self._last_ts = None
        self._last_point_id = None

    def extend(self, items: Iterable[Union[Snap, SnapPattern, List[float], Dict[str, Any]]]) -> None:
        for it in items:
            self._append_any(it)

    def append(self, snap_or_features: Union[Snap, List[float], SnapPattern, Dict[str, Any]]) -> None:
        """
        Klassisches Append ohne automatische Spatio-Temporal-Pflege.
        Für Raum/Zeit-Kontext bitte append_with_context(...) verwenden.
        """
        self._append_any(snap_or_features)

    # Legacy alias
    def add_snap(self, snap_or_features):
        return self.append(snap_or_features)

    # -------------------------------------------------------------------------
    # Spatio-Temporal-Hilfsfunktionen
    # -------------------------------------------------------------------------
    def _update_timing(self, ts: Optional[float] = None) -> None:
        """
        Aktualisiert metadata["timing"]["ts"] und ["delta_time"] anhand eines
        neuen Schrittes. Wird nur von append_with_context() aufgerufen.
        """
        timing = self.metadata.get("timing")
        if not isinstance(timing, dict):
            self._init_spatio_temporal_state()
            timing = self.metadata.get("timing", {})
        ts_list = timing.setdefault("ts", [])
        dt_list = timing.setdefault("delta_time", [])
        if not isinstance(ts_list, list):
            ts_list = []
            timing["ts"] = ts_list
        if not isinstance(dt_list, list):
            dt_list = []
            timing["delta_time"] = dt_list

        now = float(ts if ts is not None else time.time())
        if self._last_ts is not None:
            try:
                dt = max(0.0, now - self._last_ts)
            except Exception:
                dt = 0.0
            dt_list.append(float(dt))
        ts_list.append(now)
        self._last_ts = now

    def _update_space_from_obj(self, obj: Any) -> None:
        """
        Extrahiert optional eine Position aus dem Objekt und aktualisiert
        metadata["space"]["waypoints"]/["relations"] via core.spatial_index.

        Unterstützte Formen:
          • Snap mit metadata["pos"]-Dict
          • Dict mit "pos" oder "metadata"."pos"
        """
        if not _HAS_SPATIAL or spatial_index is None:  # type: ignore[truthy-function]
            return

        pos = None
        try:
            # Snap-Objekt mit .metadata
            if hasattr(obj, "metadata"):
                md = getattr(obj, "metadata", {}) or {}
                if isinstance(md, dict):
                    pos = md.get("pos")

            # Fallback: Dict-Form
            if pos is None and isinstance(obj, dict):
                pos = obj.get("pos") or (obj.get("metadata") or {}).get("pos")
        except Exception:
            pos = None

        if not isinstance(pos, dict):
            return

        # Pflicht: x, y
        try:
            x = float(pos.get("x"))
            y = float(pos.get("y"))
        except Exception:
            return

        # Optional: z, label
        z_raw = pos.get("z", None)
        z_val: Optional[float]
        if z_raw is None:
            z_val = None
        else:
            try:
                z_val = float(z_raw)
            except Exception:
                z_val = None
        label = pos.get("label")

        # Punkt im Raumgraph eintragen
        try:
            pid = spatial_index.add_point(x, y, z_val, label)  # type: ignore[union-attr]
        except Exception as e:
            logger.debug("spatial: add_point fehlgeschlagen: %s", e)
            return

        space = self.metadata.get("space")
        if not isinstance(space, dict):
            self._init_spatio_temporal_state()
            space = self.metadata.get("space", {})
        waypoints = space.setdefault("waypoints", [])
        relations = space.setdefault("relations", [])
        if not isinstance(waypoints, list):
            waypoints = []
            space["waypoints"] = waypoints
        if not isinstance(relations, list):
            relations = []
            space["relations"] = relations

        waypoints.append(int(pid))

        # Kante zum letzten Punkt (falls vorhanden)
        if self._last_point_id is not None:
            try:
                eid = spatial_index.relate(self._last_point_id, pid)  # type: ignore[union-attr]
                if isinstance(eid, int) and eid >= 0:
                    relations.append(int(eid))
            except Exception as e:
                logger.debug("spatial: relate fehlgeschlagen: %s", e)

        self._last_point_id = int(pid)

    # -------------------------------------------------------------------------
    # Kontextbewusstes Append (Zeit + Raum)
    # -------------------------------------------------------------------------
    def append_with_context(
        self,
        snap_or_features: Union[Snap, List[float], SnapPattern, Dict[str, Any]],
        ts: Optional[float] = None,
    ) -> None:
        """
        Kontextbewusstes Append:
          • pflegt Zeitkontext in metadata["timing"]:
                - "ts": Liste der Append-Zeitstempel (float, Sekunden)
                - "delta_time": Differenzen zum jeweils vorigen Append
          • pflegt Raumkontext in metadata["space"] (falls core.spatial_index verfügbar):
                - "waypoints": IDs in spatial_points
                - "relations": IDs in spatial_edges (Kante vom vorherigen Punkt)

        Eingabeformen entsprechen append():
          • Snap
          • SnapPattern
          • rohe Feature-Listen / Boards
          • Dict-Formen (features/vector/feats/centroid/board/snaps/events/text)

        Positions-Extraktion:
          • Wenn snap_or_features ein Snap ist:
                snap.metadata["pos"] = {x,y,z?,label?}
          • Wenn es ein Dict ist:
                d["pos"] oder d["metadata"]["pos"] mit gleicher Struktur

        Hinweis:
          • Scheitert die Koerzierung zu einem Pattern intern, bleibt die
            Spatio-Temporal-Info evtl. „verwaist“, was aber in der Praxis
            selten sein sollte. Fehler werden geloggt.
        """
        # Zeitkontext zuerst aktualisieren
        self._update_timing(ts)

        # Raumkontext aktualisieren (falls möglich)
        self._update_space_from_obj(snap_or_features)

        # Klassisches Append der Inhalte
        self._append_any(snap_or_features)

    # ---------------- Robustes Append ----------------
    def _append_any(self, obj: Union[Snap, SnapPattern, List[float], Dict[str, Any]]) -> None:
        # 1) SnapPattern direkt
        if isinstance(obj, SnapPattern):
            self.patterns.append(obj)
            logger.debug(
                "append: SnapPattern ok (len=%d vectors or %d snaps)",
                len(getattr(obj, "patterns", []) or []),
                len(getattr(obj, "snaps", []) or getattr(obj, "events", []) or []),
            )
            return

        # 2) Snap-ähnlich (.features/.metadata)
        if hasattr(obj, "features") and hasattr(obj, "metadata"):
            try:
                sp = SnapPattern.from_snaps([obj])  # koerziert intern
                self.patterns.append(sp)
                if _TRACE_APPEND:
                    logger.debug("append: Snap erkannt (%s)", type(obj))
                return
            except Exception as e:
                logger.warning("append: Snap-artiges Objekt fehlerhaft (%s)", e)
                return

        # 3) rohe Feature-Liste / Board
        v = _coerce_features(obj)
        if v is not None:
            try:
                sp = SnapPattern.from_snaps([v])
                self.patterns.append(sp)
                if _TRACE_APPEND:
                    logger.debug("append: Feature-Vektor (%d Werte)", len(v))
            except Exception as e:
                logger.warning("append: Feature-Vektor fehlerhaft (%s)", e)
            return

        # 4) Dict-Formen
        if isinstance(obj, dict):
            sp = _dict_to_pattern_safe(obj)
            if sp is not None:
                self.patterns.append(sp)
                if _TRACE_APPEND:
                    logger.debug("append: Dict konvertiert → Pattern")
                return
            logger.error("append: Dict ohne erkennbaren Vektor/Pattern/Text-Schlüssel")
            return

        # 5) Fallback
        logger.error("append: Ungültiger Typ %s", type(obj))

    # ---------------- Komfort-APIs ----------------
    def add_text(self, text: str, **meta: Any) -> Snap:
        s = Snap(features=[], metadata={"kind": "text", **meta}, content=str(text))
        self._append_any(s)
        return s

    def add_knowledge_snap(self, chain_id: str, text: str, fusion: Optional["FusionPack"] = None) -> Optional[Snap]:
        s = Snap(features=[], metadata={"kind": "knowledge", "chain_id": chain_id, "text": text}, content=text)
        try:
            if FusionPack is not None and fusion is not None and hasattr(s, "attach_fusion"):
                s.attach_fusion(fusion)  # type: ignore[attr-defined]
        except Exception:
            logger.debug("add_knowledge_snap: Fusion nicht angehängt (optional).")
        self._append_any(s)
        return s

    # ---------------- Knowledge / RAG ----------------
    def ask_knowledge(self, db_path: str, question: str, top_k: int = 5) -> str:
        if RAGStore is None:
            return f"(RAG nicht verfügbar) Frage: {question}"
        try:
            store = RAGStore(db_path)  # type: ignore
            hits = store.search(question, top_k=top_k)
            passages: List[str] = []
            for h in hits:
                try:
                    passages.append(h["content"])
                except Exception:
                    try:
                        passages.append(h[2])
                    except Exception:
                        continue
            return synthesize_answer(question, passages)
        except Exception as e:
            logger.error("ask_knowledge Fehler: %s", e)
            return f"Fehler bei Knowledge-Suche: {e}"

    # ---------------- Analyse ----------------
    def feature_centroid(self) -> List[float]:
        cents: List[List[float]] = []
        for p in self.patterns:
            c = _pattern_centroid_safe(p)
            if c:
                cents.append(c)
        if not cents:
            return []
        if _HAS_NP:
            arr = _np.asarray(cents, dtype=_np.float32)
            return _np.mean(arr, axis=0).astype(_np.float32).tolist()
        d = len(cents[0])
        acc = [0.0] * d
        for c in cents:
            if len(c) < d:
                continue
            for i in range(d):
                acc[i] += float(c[i])
        n = max(1, len(cents))
        return [v / n for v in acc]

    def score_resonance(self, reference: Optional[Sequence[float]] = None) -> float:
        def _cos(a: Sequence[float], b: Sequence[float]) -> float:
            d = min(len(a), len(b))
            if d <= 0:
                return 0.0
            dot = sum(float(x) * float(y) for x, y in zip(a[:d], b[:d]))
            na = (sum(float(x) * float(x) for x in a[:d]) ** 0.5)
            nb = (sum(float(y) * float(y) for y in b[:d]) ** 0.5)
            return dot / (na * nb + EPS)

        if reference is not None:
            chain_c = self.feature_centroid()
            self.resonance_score = _cos(chain_c, reference) if chain_c else 0.0
            self.resonance_score = max(-1.0, min(1.0, self.resonance_score))
            return self.resonance_score

        cents = [_pattern_centroid_safe(p) for p in self.patterns]
        sims: List[float] = []
        for i in range(len(cents) - 1):
            a, b = cents[i], cents[i + 1]
            if a and b:
                sims.append(_cos(a, b))
        self.resonance_score = float(sum(sims) / len(sims)) if sims else 0.0
        self.resonance_score = max(-1.0, min(1.0, self.resonance_score))
        return self.resonance_score

    # ---------------- Serialisierung ----------------
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialisiert robust und markiert Validität:
          - Pattern ist gültig, wenn es .snaps/.events **oder** .patterns (Vektorlisten) enthält.
          - Spatio-Temporal-Metadaten (timing/space) werden unverändert im
            metadata-Block mitgeschrieben.
        """
        errors: List[str] = []

        if not self.patterns:
            errors.append("Keine Patterns")

        for i, p in enumerate(self.patterns):
            try:
                has_snaps = bool(getattr(p, "snaps", None) or getattr(p, "events", None))
                plist = list(getattr(p, "patterns", []) or [])
                has_vectors = bool(plist and all(_is_num_seq(v) for v in plist))
                if not (has_snaps or has_vectors):
                    errors.append(f"Pattern {i} leer")
            except Exception as e:
                errors.append(f"Pattern {i} Fehler: {e}")

        valid = (len(errors) == 0)

        data = {
            "schema_version": SCHEMA_VERSION,
            "patterns": [_pattern_to_dict_safe(p) for p in self.patterns] if valid else [],
            "metadata": self.metadata,
            "resonance_score": self.resonance_score,
            "reward_score": self.reward_score,
            "episodic_id": self.episodic_id,
            "explain_trace": self.explain_trace,
            "ts_created": self.ts_created,
            "valid": valid,
            "errors": errors,
        }

        if valid:
            if _TRACE_SERIALIZE:
                logger.debug("to_dict: SnapChain serialized (patterns=%d)", len(self.patterns))
        else:
            logger.warning("to_dict: SnapChain INVALID – %s", ", ".join(errors))

        return data

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SnapChain":
        pats_raw = d.get("patterns", [])
        pats: List[SnapPattern] = []
        for pd in pats_raw:
            try:
                # zuerst SnapPattern.from_dict probieren
                sp = SnapPattern.from_dict(pd)  # type: ignore
            except Exception:
                sp = None  # type: ignore
            if sp is None:
                sp = _dict_to_pattern_safe(pd)  # type: ignore
            if sp is None and isinstance(pd, dict) and "centroid" in pd:
                # Minimal-Fallback: leeres Pattern mit Centroid/Metadata
                sp = SnapPattern.from_snaps([], metadata=pd.get("metadata") or {})
                try:
                    setattr(sp, "centroid", list(pd.get("centroid") or []))
                except Exception as e:
                    log_guard.log_suppressed(logger, key="snapchain.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
            if sp is not None:
                pats.append(sp)

        chain = cls(pats, d.get("metadata", {}))
        chain.resonance_score = float(d.get("resonance_score", 0.0))
        chain.reward_score = float(d.get("reward_score", 0.0))
        chain.episodic_id = d.get("episodic_id")
        chain.explain_trace = d.get("explain_trace")
        chain.ts_created = float(d.get("ts_created", time.time()))

        # _init_spatio_temporal_state() wurde im __init__ bereits aufgerufen und
        # hat _last_ts/_last_point_id anhand von metadata rekonstruiert.
        return chain

    def as_blob(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_blob(cls, blob: bytes) -> "SnapChain":
        return cls.from_dict(json.loads(blob.decode("utf-8")))

    # ---------------- Debug Strings ----------------
    def __repr__(self) -> str:
        return f"<SnapChain n={len(self.patterns)} score={self.resonance_score:.3f} reward={self.reward_score:.3f}>"

    def short_info(self) -> str:
        return f"SnapChain(len={len(self.patterns)}, score={self.resonance_score:.3f}, reward={self.reward_score:.3f})"

# =============================================================================
# Datei-IO
# =============================================================================

def save_chain(chain_id: Union[int, str], chain: SnapChain) -> str:
    os.makedirs(SNAPCHAIN_DIR, exist_ok=True)
    path = os.path.join(SNAPCHAIN_DIR, f"{chain_id}.json")
    data = chain.to_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    n = len(chain.patterns)
    logger.info("💾 SnapChain %s gespeichert (%d Patterns) → %s", str(chain_id), n, path)
    if n > 0:
        try:
            preview = json.dumps(data.get("patterns", [])[:3], ensure_ascii=False)[:600]
            if _TRACE_SERIALIZE:
                logger.debug("save_chain preview (erste Patterns): %s", preview)
        except Exception as e:
            log_guard.log_suppressed(logger, key="snapchain.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return path

def load_chain(chain_id: Union[int, str]) -> Dict[str, Any]:
    path = os.path.join(SNAPCHAIN_DIR, f"{chain_id}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SnapChain-Datei nicht gefunden: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        chain = SnapChain.from_dict(data)
        events = [_pattern_to_dict_safe(p) for p in chain.patterns]
        metadata = chain.metadata
        logger.info("load_chain: %s geladen (%d Events)", chain_id, len(events))
        return {"id": chain_id, "events": events, "metadata": metadata}
    except Exception as e:
        logger.warning("load_chain: %s – Fallback-Parser (%s)", chain_id, e)
        events = data.get("events", data.get("patterns", []))
        metadata = data.get("metadata", {})
        return {"id": chain_id, "events": events, "metadata": metadata}

# =============================================================================
# Mini-Selftest
# =============================================================================
if __name__ == "__main__":
    try:
        # Basis-Snaps
        s1 = Snap([0.1, 0.2, 0.3], metadata={"m": 1, "pos": {"x": 0.0, "y": 0.0, "label": "origin"}}, content="hello")
        s2 = Snap([0.2, 0.2, 0.2], metadata={"m": 2, "pos": {"x": 1.0, "y": 0.7, "label": "p1"}}, content="world")

        p1 = SnapPattern.from_snaps([s1])
        p2 = SnapPattern.from_snaps([[0.2, 0.2, 0.2], [0.3, 0.2, 0.1]])

        ch = SnapChain(metadata={"source": "selftest"})
        ch.append_with_context(p1)       # mit Raum/Zeit
        ch.append_with_context(s2)       # mit Raum/Zeit
        ch.append({"features": [1, 0, 0]})  # klassisch, ohne Kontext

        print("Centroid:", ch.feature_centroid())
        print("Resonance:", ch.score_resonance())
        print("Timing metadata:", ch.metadata.get("timing"))
        print("Space metadata:", ch.metadata.get("space"))

        save_path = save_chain("selftest", ch)
        print("Saved:", save_path)
        loaded = load_chain("selftest")
        print("Loaded keys:", list(loaded.keys()))
        print("[snapchain] Selftest OK ✅")
    except Exception as e:
        print("[snapchain] Selftest FEHLER:", e)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/snappattern.py
# Projekt:   ORÓMA (Offline-First · Headless · Episodic Building Blocks)
# Modul:     SnapPattern – Muster/Cluster aus Snaps oder Feature-Vektoren (Centroid + Similarity + Gap-Detection) inkl. SQLite-Persistenz
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# SnapPattern ist die „Mittelstufe“ zwischen Snap (Atom) und SnapChain (Episode).
# Ein SnapPattern bündelt mehrere Snaps oder Feature-Vektoren zu einem Muster, das:
#   - einen Centroid (repräsentativen Feature-Vektor) besitzt
#   - Ähnlichkeit/Distanz zu anderen Patterns berechnen kann (Cosine/L2)
#   - optional als „Gap“ markiert werden kann (Knowledge-Gap Heuristik)
#   - in SQLite persistiert werden kann (snap_patterns Tabelle)
#
# Dieses Modul ist bewusst:
#   - headless (keine GUI)
#   - robust gegen heterogene Inputs (Snap, SnapToken, dict, Sequence[float])
#   - optional beschleunigt durch NumPy (wenn vorhanden)
#   - tolerant gegen Schema-/RowFactory-Varianten (dict rows vs sqlite3.Row vs tuple)
#
# BEGRIFFE / DATENSTRUKTUR
# ───────────────────────
# SnapPattern enthält:
#   - patterns: List[List[float]]
#       eine Liste von Feature-Vektoren (je „Snap“) – kann leer sein
#   - centroid: List[float]
#       Schwerpunkt/Mean der patterns
#   - created_at: int (unix seconds)
#   - meta: Dict[str, Any]
#       beliebige Zusatzinfos (origin, tags, debug, governance)
#
# HINWEIS: Dieses Modul akzeptiert Snap und SnapToken nur lose gekoppelt:
# - Wenn core.snap / core.snaptoken nicht importierbar ist, existieren Minimal-Stubs,
#   damit SnapPattern weiterhin importierbar bleibt (z. B. in Slim-Env/Tests).
#
# FEATURE-UTILS / SIMILARITY (KERNFUNKTIONEN)
# ───────────────────────────────────────────
# Numerische Helfer:
#   - _dot(), _norm()
#   - cosine_similarity(a,b, allow_mismatch=False)
#   - l2_distance(a,b, allow_mismatch=False)
#
# Mismatch-Toleranz:
# - allow_mismatch=True erlaubt unterschiedliche Dimensionen, indem nur bis min(len(a),len(b))
#   gerechnet wird. Default bleibt strikt (Dimension muss passen), damit Training deterministisch ist.
#
# SnapPattern Methods (typisch):
#   - add_snap(x): akzeptiert Snap/SnapToken/dict/Sequence → extrahiert Vector
#   - recompute_centroid(): bildet centroid aus patterns
#   - normalize_centroid(): L2-Normalisierung (optional)
#
# GAP-DETECTION (KNOWLEDGE GAP HEURISTIK)
# ──────────────────────────────────────
# detect_gap(sp, threshold=..., max_candidates=...) markiert, ob ein Pattern „neu/ungekannt“ ist.
# Strategie im aktuellen Codepfad:
#   1) Wenn eine Vector-DB/Migration verfügbar ist (core.vector_migration + sql_manager.vector_db_threshold()):
#      - query(centroid, topk=10), best score
#      - Gap, wenn best < threshold
#   2) Sonst Fallback: find_similar(sp) aus snap_patterns Tabelle
#      - Gap, wenn best < threshold
#
# Das ist bewusst „best effort“:
# - wenn VectorDB oder DB Query fehlschlägt, fällt es defensiv auf die nächste Stufe zurück.
#
# SQLITE-PERSISTENZ (snap_patterns)
# ─────────────────────────────────
# Dieses Modul hat eigene Persistenz-Utilities (nicht nur über sql_manager):
#   _ensure_snappattern_schema():
#     - ruft ensure_schema() und get_conn() aus sql_manager
#     - erstellt Tabelle snap_patterns falls nicht vorhanden:
#         id INTEGER PRIMARY KEY
#         created_at INTEGER
#         feature_dim INTEGER
#         num_snaps INTEGER
#         centroid BLOB
#         payload BLOB
#         metadata TEXT
#         gap_flag INTEGER DEFAULT 0
#     - erzeugt Indizes (created, dim, gap_flag)
#
# Speicherung:
#   save_pattern(sp, store_full_payload=False, detect_gap_flag=True, normalize=False) -> int
#     - centroid wird als JSON-Blob gespeichert:
#         {"centroid":[...], "feature_dim":..., "l2_norm":... optional}
#       (l2_norm ist optionaler Norm-Cache und bleibt rückwärtskompatibel)
#     - payload enthält as_blob(include_patterns=store_full_payload)
#       Default store_full_payload=False ist absichtlich (DB klein halten)
#
# Laden:
#   load_pattern(pattern_id, full=True) -> Optional[SnapPattern]
#     - liest centroid_blob und metadata robust
#     - JSON errors in metadata werden abgefangen (Warning + {})
#     - wenn full=False → payload kann ignoriert werden (schneller)
#
# Update helpers:
#   update_metadata(pattern_id, patch: dict) -> bool
#   set_gap_flag(pattern_id, flag: bool) -> bool
#
# Similarity Query:
#   find_similar(query, topk=10, require_same_dim=True) -> List[Tuple[int, float]]
#     - query kann SnapPattern oder Vector sein
#     - require_same_dim=True filtert serverseitig über feature_dim
#
# OPTIONAL: NUMPY
# ───────────────
# Wenn numpy verfügbar ist, werden Centroid-Berechnung und Similarity schneller.
# Ohne numpy: deterministische Python-Loops.
#
# LOGGING
# ───────
# Logger: "oroma.snappattern"
# Level:  respektiert OROMA_LOG_LEVEL (Default WARNING)
# Zusätzlich: log_guard.log_suppressed wird für wiederholte Fehler genutzt.
#
# WICHTIGE ENV-VARIABLEN (DIESER DATEI RELEVANT)
# ─────────────────────────────────────────────
# Logging:
#   OROMA_LOG_LEVEL=WARNING|INFO|DEBUG
#
# Gap-Detection:
#   (Thresholds sind in detect_gap Parameter; in Tools/UI typischerweise konfigurierbar)
#
# ÖFFENTLICHE API (STABIL, VON ANDEREN MODULEN GENUTZT)
# ───────────────────────────────────────────────────
# class SnapPattern:
#   - add_snap(), extend_snaps()
#   - recompute_centroid(), normalize_centroid()
#   - as_blob(include_patterns: bool) -> bytes
#   - from_blob(blob: bytes) -> SnapPattern
#
# detect_gap(...)
# save_pattern(...)
# load_pattern(...)
# update_metadata(...)
# set_gap_flag(...)
# find_similar(...)
# create_and_save_from_snaps(...)
# quick_similarity(...)
# _selftest(...)
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Muss ohne numpy / ohne core.snap / ohne core.snaptoken importierbar bleiben.
# - Default store_full_payload=False muss bleiben (DB-Wachstum).
# - RowFactory-Varianten müssen toleriert werden (dict rows aus sql_manager sind real).
# - Gap-Detection bleibt best effort (keine harten Abhängigkeiten an VectorDB).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import math
import time
import zlib
import struct
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from core.log_guard import log_suppressed

from core import log_guard
# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("oroma.snappattern")
if not logger.handlers:
    h = logging.StreamHandler()
    f = logging.Formatter("[snappattern] %(levelname)s: %(message)s")
    h.setFormatter(f)
    logger.addHandler(h)
# OROMA_LOG_LEVEL respektieren, Default WARNING
import os as _os
logger.setLevel(
    getattr(logging, _os.environ.get("OROMA_LOG_LEVEL", "WARNING").upper(), logging.WARNING)
)

EPS = 1e-8

# --- NumPy optional -----------------------------------------------------------
try:
    import numpy as _np  # type: ignore

    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False
    _np = None  # type: ignore

# --- Optionale, lose Kopplung an core.snap / core.snaptoken -------------------
try:
    from core.snap import Snap  # type: ignore
except Exception:  # pragma: no cover
    class Snap:  # Minimal-Stub
        def __init__(self, features: Sequence[float], metadata: Optional[Dict[str, Any]] = None):
            self.features = list(map(float, features))
            self.metadata = metadata or {}


try:
    from core.snaptoken import SnapToken  # type: ignore
except Exception:
    class SnapToken:  # Minimal-Stub
        def feature_vector(self, as_float: bool = True):
            return []


# --- SQL-Backend: benötigte Funktionen aus sql_manager ------------------------
try:
    from core import sql_manager  # type: ignore
    from core.sql_manager import get_conn, ensure_schema  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "snappattern.py benötigt core.sql_manager. "
        "Bitte sicherstellen, dass core/sql_manager.py vorhanden ist."
    ) from e


# =============================================================================
# Hilfsfunktionen – Vektormathematik
# =============================================================================


def _to_vector_list(v: Union[Sequence[float], Dict[str, Any], Snap, SnapToken]) -> List[float]:
    """Konvertiert verschiedene Eingabeformen in eine Float-Liste."""
    if isinstance(v, Snap):
        return list(map(float, getattr(v, "features", []) or []))
    if isinstance(v, SnapToken):
        return list(map(float, v.feature_vector()))
    if isinstance(v, dict) and "features" in v:
        return list(map(float, v["features"]))
    return list(map(float, v))


def _centroid(vectors: List[Sequence[float]]) -> List[float]:
    """Einfacher Mittelwert pro Dimension; Dimensionen müssen konsistent sein."""
    if not vectors:
        return []
    try:
        d = len(vectors[0])
        if any(len(vec) != d for vec in vectors):
            raise ValueError("Inkonstistente Feature-Dimensionen im SnapPattern.")
        acc = [0.0] * d
        for vec in vectors:
            for i in range(d):
                acc[i] += float(vec[i])
        n = float(len(vectors))
        return [v / n for v in acc]
    except Exception as ex:
        logger.warning("[SnapPattern._centroid] Fehler: %s → Fallback Nullvektor", ex)
        return []


def _dot(a: Sequence[float], b: Sequence[float], *, allow_mismatch: bool = False) -> float:
    if allow_mismatch:
        d = min(len(a), len(b))
        return sum(float(a[i]) * float(b[i]) for i in range(d))
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _norm(a: Sequence[float], *, upto: Optional[int] = None) -> float:
    if upto is not None:
        return math.sqrt(
            sum(float(a[i]) * float(a[i]) for i in range(min(len(a), upto)))
        )
    return math.sqrt(sum(float(x) * float(x) for x in a))


def cosine_similarity(
    a: Sequence[float],
    b: Sequence[float],
    *,
    allow_mismatch: bool = False,
) -> float:
    """
    Kosinus-Ähnlichkeit; optional tolerant bei Dimensions-Mismatch
      - allow_mismatch=False (Default): 0 bei ungleichen Dimensionen (alte Semantik)
      - allow_mismatch=True: vergleicht auf min(len(a), len(b))
    """
    if not a or not b:
        return 0.0
    if (not allow_mismatch) and (len(a) != len(b)):
        return 0.0
    if _HAS_NUMPY:
        try:
            va = (
                _np.asarray(a[: min(len(a), len(b))], dtype=_np.float32)
                if allow_mismatch
                else _np.asarray(a, dtype=_np.float32)
            )
            vb = _np.asarray(b[: len(va)], dtype=_np.float32)
            denom = float(_np.linalg.norm(va) * _np.linalg.norm(vb)) + EPS
            return float(float(va.dot(vb)) / denom)
        except Exception as e:
            log_guard.log_suppressed(logger, key="snappattern.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    d = min(len(a), len(b)) if allow_mismatch else len(a)
    if d <= 0:
        return 0.0
    dot = _dot(a, b, allow_mismatch=True)
    denom = (_norm(a, upto=d) * _norm(b, upto=d)) + EPS
    return dot / denom


def l2_distance(
    a: Sequence[float],
    b: Sequence[float],
    *,
    allow_mismatch: bool = False,
) -> float:
    """L2-Distanz; optional tolerant (auf min-Dimension)."""
    if not a or not b:
        return float("inf")
    if (not allow_mismatch) and (len(a) != len(b)):
        return float("inf")
    if _HAS_NUMPY:
        try:
            va = (
                _np.asarray(a[: min(len(a), len(b))], dtype=_np.float32)
                if allow_mismatch
                else _np.asarray(a, dtype=_np.float32)
            )
            vb = _np.asarray(b[: len(va)], dtype=_np.float32)
            return float(_np.linalg.norm(va - vb))
        except Exception as e:
            log_guard.log_suppressed(logger, key="snappattern.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    d = min(len(a), len(b)) if allow_mismatch else len(a)
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(d)))


# =============================================================================
# Datenträgerformat – kompaktes Blob
# =============================================================================

_MAGIC = b"SPAT"
_VER = 1
_HDR = struct.Struct(">4sBI")


def _pack_json(d: Dict[str, Any]) -> bytes:
    raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    z = zlib.compress(raw, level=6)
    return _HDR.pack(_MAGIC, _VER, len(z)) + z


def _unpack_json(blob: bytes) -> Dict[str, Any]:
    if len(blob) < _HDR.size:
        raise ValueError("Blob ist zu kurz.")
    magic, ver, zlen = _HDR.unpack_from(blob, 0)
    if magic != _MAGIC:
        raise ValueError("Ungültige Magic in SnapPattern-Blob.")
    if ver != _VER:
        raise ValueError(f"Nicht unterstützte SnapPattern-Blob-Version: {ver}")
    data = blob[_HDR.size : _HDR.size + zlen]
    raw = zlib.decompress(data)
    return json.loads(raw.decode("utf-8"))


# =============================================================================
# SnapPattern – Datenklasse
# =============================================================================


@dataclass
class SnapPattern:
    patterns: List[List[float]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=lambda: int(time.time()))
    centroid: List[float] = field(default_factory=list)

    # ---------------------- Erzeugung/Mutationen ------------------------------

    @classmethod
    def from_snaps(
        cls,
        snaps,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "SnapPattern":
        feats = [_to_vector_list(s) for s in snaps]
        sp = cls(patterns=feats, metadata=metadata or {})
        sp.recompute_centroid()
        return sp

    def add_snap(self, snap_or_vec: Union[Snap, Sequence[float], Dict[str, Any]]) -> None:
        vec = _to_vector_list(snap_or_vec)
        if self.patterns and len(vec) != len(self.patterns[0]):
            logger.warning(
                "add_snap: Dimensions-Mismatch (%d vs %d) → ignoriert",
                len(vec),
                len(self.patterns[0]),
            )
            return
        self.patterns.append(vec)
        self.recompute_centroid()

    def extend_snaps(
        self,
        snaps: Sequence[Union[Snap, Sequence[float], Dict[str, Any]]],
    ) -> None:
        for s in snaps:
            self.add_snap(s)

    def feature_dim(self) -> int:
        return len(self.patterns[0]) if self.patterns else 0

    def recompute_centroid(self) -> None:
        self.centroid = _centroid(self.patterns) if self.patterns else []

    def normalize_centroid(self) -> None:
        """L2-Normalisierung der Centroid (in-place)."""
        if not self.centroid:
            return
        n = math.sqrt(sum(x * x for x in self.centroid))
        if n > 0:
            self.centroid = [x / n for x in self.centroid]

    # ---------------------- (De-)Serialisierung -------------------------------

    def to_dict(self, include_patterns: bool = True) -> Dict[str, Any]:
        d = {
            "created_at": self.created_at,
            "feature_dim": self.feature_dim(),
            "num_snaps": len(self.patterns),
            "centroid": self.centroid,
            "metadata": self.metadata or {},
        }
        if include_patterns:
            d["patterns"] = self.patterns
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SnapPattern":
        patterns = [list(map(float, v)) for v in d.get("patterns", [])]
        sp = cls(
            patterns=patterns,
            metadata=d.get("metadata", {}) or {},
            created_at=int(d.get("created_at", int(time.time()))),
            centroid=list(map(float, d.get("centroid", []))),
        )
        if not sp.centroid and patterns:
            sp.recompute_centroid()
        return sp

    def as_blob(self, include_patterns: bool = True) -> bytes:
        return _pack_json(self.to_dict(include_patterns=include_patterns))

    @classmethod
    def from_blob(cls, blob: bytes) -> "SnapPattern":
        return cls.from_dict(_unpack_json(blob))

    # ---------------------- Ähnlichkeit ---------------------------------------

    def cosine_to(self, other, *, allow_mismatch: bool = False) -> float:
        vec = other.centroid if isinstance(other, SnapPattern) else other
        return cosine_similarity(self.centroid, vec, allow_mismatch=allow_mismatch)

    def l2_to(self, other, *, allow_mismatch: bool = False) -> float:
        vec = other.centroid if isinstance(other, SnapPattern) else other
        return l2_distance(self.centroid, vec, allow_mismatch=allow_mismatch)


# =============================================================================
# Gap-Detection
# =============================================================================


def detect_gap(
    sp: SnapPattern,
    threshold: float = 0.30,
    max_candidates: int = 500,
) -> bool:
    """Ermittelt, ob ein Pattern eine Wissenslücke darstellt."""
    if not sp.centroid:
        return True
    try:
        total = sql_manager.count_snapchains()
        # vector_db_threshold() kann fehlen → Exception → Fallback
        if hasattr(sql_manager, "vector_db_threshold"):
            if total >= sql_manager.vector_db_threshold():  # type: ignore[attr-defined]
                from core import vector_migration  # type: ignore

                res = vector_migration.query(sp.centroid, topk=10)
                if not res:
                    return True
                best = max(score for _, score in res)
                return best < float(threshold)
    except Exception as ex:
        logger.debug("[SnapPattern.detect_gap] Vector-DB Fallback: %s", ex)

    sims = find_similar(sp, topk=max_candidates)
    if not sims:
        return True
    best = max(score for _, score in sims)
    return best < float(threshold)


# =============================================================================
# SQL-Persistenz-Utilities für SnapPattern
# =============================================================================


def _ensure_snappattern_schema() -> None:
    """Erweitert das DB-Schema um die Tabelle snap_patterns (idempotent)."""
    ensure_schema()
    # Stufe C (Global Single Writer): Schema-Änderungen sind Writes → bevorzugt via DBWriter.
    # Wenn DBWriter aktiv ist, ist ein lokaler Schema-Fallback ausdrücklich verboten.
    try:
        if getattr(sql_manager, "_dbw_enabled", lambda: False)() and getattr(sql_manager, "_dbw", None) is not None:
            dbw = getattr(sql_manager, "_dbw")
            dbw.exec_write(
                """
                CREATE TABLE IF NOT EXISTS snap_patterns (
                    id INTEGER PRIMARY KEY,
                    created_at INTEGER,
                    feature_dim INTEGER,
                    num_snaps INTEGER,
                    centroid BLOB,
                    payload BLOB,
                    metadata TEXT,
                    gap_flag INTEGER DEFAULT 0
                )
                """,
                params=[],
                tag="snappattern.schema",
                priority="low",
                timeout_ms=getattr(sql_manager, "_dbw_timeout_ms", lambda k='dream': 60000)("dream"),
                db="oroma",
            )
            dbw.exec_write("CREATE INDEX IF NOT EXISTS idx_sp_created ON snap_patterns(created_at)", [], "snappattern.schema", "low", 60000, db="oroma")
            dbw.exec_write("CREATE INDEX IF NOT EXISTS idx_sp_dim   ON snap_patterns(feature_dim)", [], "snappattern.schema", "low", 60000, db="oroma")
            dbw.exec_write("CREATE INDEX IF NOT EXISTS idx_sp_gap   ON snap_patterns(gap_flag)", [], "snappattern.schema", "low", 60000, db="oroma")
            return
    except Exception as ex:
        logger.warning("[SnapPattern.schema] DBWriter schema failed → skip local fallback: %s", ex)
        return

    if getattr(sql_manager, "_dbw_enabled", lambda: False)():
        logger.warning("[SnapPattern.schema] DBWriter enabled but schema path not available – skip local fallback")
        return

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS snap_patterns (
                id INTEGER PRIMARY KEY,
                created_at INTEGER,
                feature_dim INTEGER,
                num_snaps INTEGER,
                centroid BLOB,
                payload BLOB,
                metadata TEXT,
                gap_flag INTEGER DEFAULT 0
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sp_created ON snap_patterns(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sp_dim   ON snap_patterns(feature_dim)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sp_gap   ON snap_patterns(gap_flag)")
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_pattern(
    sp: SnapPattern,
    store_full_payload: bool = False,
    detect_gap_flag: bool = True,
    normalize: bool = False,
) -> int:
    """
    Speichert ein SnapPattern in der DB, optional mit Gap-Detection.

    Parameter
    ---------
    sp : SnapPattern
        Zu speicherndes Pattern.
    store_full_payload : bool, default False
        Wenn True, werden die vollständigen Pattern-Vektoren im Feld payload
        mitgespeichert (größer, aber vollständiger Replay möglich).
        Default False → dünne Repräsentation (nur Centroid + Metadaten).
    detect_gap_flag : bool, default True
        Ob detect_gap() aufgerufen werden soll, um gap_flag zu setzen.
    normalize : bool, default False
        Optional: Centroid vor dem Speichern L2-normalisieren.

    v3.8-r1-Anpassung:
    ------------------
    - centroid_blob enthält zusätzlich l2_norm der Centroid, um später
      Norm-Cache-Optimierungen zu ermöglichen. Alte Einträge ohne l2_norm
      bleiben kompatibel.
    """
    _ensure_snappattern_schema()

    if sp.centroid is None or not sp.centroid:
        sp.recompute_centroid()
    if normalize:
        sp.normalize_centroid()

    # L2-Norm der Centroid für Norm-Cache speichern
    try:
        l2_norm = float(_norm(sp.centroid)) if sp.centroid else 0.0
    except Exception:
        l2_norm = 0.0

    centroid_info: Dict[str, Any] = {
        "centroid": sp.centroid or [],
        "feature_dim": sp.feature_dim(),
    }
    if l2_norm > 0.0:
        centroid_info["l2_norm"] = l2_norm

    centroid_blob = _pack_json(centroid_info)
    payload = sp.as_blob(include_patterns=store_full_payload)
    metadata_txt = json.dumps(
        sp.metadata or {},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    gap_flag = 0
    if detect_gap_flag:
        try:
            gap_flag = 1 if detect_gap(sp) else 0
        except Exception as ex:
            logger.debug("[SnapPattern.save_pattern] detect_gap Fehler: %s", ex)
            gap_flag = 0

    # Stufe C: Persistenz-Writes via DBWriter (BLOB-safe). Bei aktivem DBWriter kein lokaler Fallback.
    try:
        if getattr(sql_manager, "_dbw_enabled", lambda: False)() and getattr(sql_manager, "_dbw", None) is not None:
            dbw = getattr(sql_manager, "_dbw")
            rid = int(dbw.exec_lastrowid(
                """
                INSERT INTO snap_patterns
                    (created_at, feature_dim, num_snaps, centroid, payload, metadata, gap_flag)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                params=[
                    int(sp.created_at),
                    int(sp.feature_dim()),
                    int(len(sp.patterns)),
                    centroid_blob,
                    payload,
                    metadata_txt,
                    int(gap_flag),
                ],
                tag="snappattern.save",
                priority="low",
                timeout_ms=getattr(sql_manager, "_dbw_timeout_ms", lambda k='dream': 60000)("dream"),
                db="oroma",
            ) or 0)
            return rid
    except Exception as ex:
        logger.warning("[SnapPattern.save_pattern] DBWriter failed → skip local fallback: %s", ex)
        return -1

    if getattr(sql_manager, "_dbw_enabled", lambda: False)():
        logger.warning("[SnapPattern.save_pattern] DBWriter enabled but no DBWriter write path succeeded – skip (no local fallback)")
        return -1

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO snap_patterns
                (created_at, feature_dim, num_snaps, centroid, payload, metadata, gap_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sp.created_at,
                sp.feature_dim(),
                len(sp.patterns),
                centroid_blob,
                payload,
                metadata_txt,
                gap_flag,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_pattern(pattern_id: int, full: bool = True) -> Optional[SnapPattern]:
    """
    Lädt ein SnapPattern aus der DB.

    Verhalten:
      - gap_flag wird in metadata['_gap_flag'] gespiegelt
      - l2_norm (falls in centroid_blob vorhanden) wird zusätzlich in
        metadata['_centroid_l2_norm'] abgelegt (float), bleibt aber optional.

    WICHTIG v3.8-r2:
      - Anpassung an dict-RowFactory aus sql_manager.get_conn().
      - Ungültiges metadata-JSON wird geloggt und auf {} zurückgesetzt.
    """
    _ensure_snappattern_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT created_at, feature_dim, num_snaps, centroid, payload, metadata, gap_flag
          FROM snap_patterns
         WHERE id = ?
        """,
        (pattern_id,),
    )
    row = cur.fetchone()
    if not row:
        return None

    # RowFactory liefert dict; Fallback auf Sequenz für maximale Kompatibilität.
    if isinstance(row, dict):
        created_at = row.get("created_at")
        centroid_blob = row.get("centroid")
        payload_blob = row.get("payload")
        metadata_txt = row.get("metadata")
        gap_flag_val = row.get("gap_flag", 0)
    else:
        # Erwartete Reihenfolge im SELECT:
        # created_at, feature_dim, num_snaps, centroid, payload, metadata, gap_flag
        created_at = row[0] if len(row) > 0 else None
        centroid_blob = row[3] if len(row) > 3 else None
        payload_blob = row[4] if len(row) > 4 else None
        metadata_txt = row[5] if len(row) > 5 else None
        gap_flag_val = row[6] if len(row) > 6 else 0

    # Metadata robust parsen
    metadata: Dict[str, Any]
    try:
        if metadata_txt is None or metadata_txt == "":
            metadata = {}
        else:
            metadata = json.loads(metadata_txt)
            if not isinstance(metadata, dict):
                raise ValueError("metadata ist kein Objekt")
    except Exception as ex:
        logger.warning(
            "[SnapPattern.load_pattern] Ungültiges metadata JSON (id=%s): %r – Fallback auf {}",
            pattern_id,
            metadata_txt,
        )
        metadata = {}

    # gap_flag robust casten
    try:
        gap_flag_int = int(gap_flag_val)
    except Exception:
        gap_flag_int = 0
    metadata["_gap_flag"] = gap_flag_int

    # Vollständiges Pattern laden, wenn gewünscht und verfügbar
    if full and payload_blob:
        try:
            sp = SnapPattern.from_blob(payload_blob)
            merged = dict(sp.metadata or {})
            merged.update(metadata or {})
            sp.metadata = merged
            return sp
        except Exception as ex:
            logger.debug(
                "[SnapPattern.load_pattern] payload-parse Fallback (id=%s): %s",
                pattern_id,
                ex,
            )

    # Fallback: nur Centroid-Info nutzen (inkl. optionaler l2_norm)
    cinfo: Dict[str, Any] = {}
    try:
        if centroid_blob:
            cinfo = _unpack_json(centroid_blob)
    except Exception as ex:
        logger.warning(
            "[SnapPattern.load_pattern] centroid-Blob kaputt (id=%s): %s",
            pattern_id,
            ex,
        )
        cinfo = {}

    centroid = list(map(float, (cinfo.get("centroid") or [])))
    try:
        created_at_int = int(created_at) if created_at is not None else int(time.time())
    except Exception:
        created_at_int = int(time.time())

    sp = SnapPattern(
        patterns=[],
        metadata=metadata,
        created_at=created_at_int,
        centroid=centroid,
    )

    # l2_norm der Centroid, falls im Blob vorhanden, in Metadaten spiegeln
    try:
        if "l2_norm" in cinfo:
            sp.metadata["_centroid_l2_norm"] = float(cinfo.get("l2_norm") or 0.0)
    except Exception as e:
        # optional; Fehler hier sind nicht kritisch
        log_suppressed(
            logging.getLogger(__name__),
            key="core.snappattern.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    return sp


def update_metadata(pattern_id: int, patch: Dict[str, Any]) -> bool:
    """Patche Metadaten (JSON-Merge, serverseitig transformiert)."""
    _ensure_snappattern_schema()
    sp = load_pattern(pattern_id, full=False)
    if not sp:
        return False
    meta = dict(sp.metadata or {})
    meta.update(patch or {})
    txt = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE snap_patterns SET metadata=? WHERE id=?",
        (txt, int(pattern_id)),
    )
    conn.commit()
    return True


def set_gap_flag(pattern_id: int, flag: bool) -> bool:
    """Manuelles Setzen des gap_flag (override)."""
    _ensure_snappattern_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE snap_patterns SET gap_flag=? WHERE id=?",
        (1 if flag else 0, int(pattern_id)),
    )
    conn.commit()
    return True


def find_similar(
    query: Union[SnapPattern, Sequence[float]],
    topk: int = 10,
    max_age_days: Optional[int] = None,
    min_dim: Optional[int] = None,
    max_dim: Optional[int] = None,
    require_same_dim: bool = True,
) -> List[Tuple[int, float]]:
    """Sucht ähnliche SnapPatterns per Cosine-Ähnlichkeit (Fallback ohne ANN)."""
    _ensure_snappattern_schema()

    qvec = query.centroid if isinstance(query, SnapPattern) else list(map(float, query))
    if not qvec:
        return []

    conn = get_conn()
    cur = conn.cursor()

    where: List[str] = []
    params: List[Any] = []
    if max_age_days is not None and max_age_days > 0:
        cutoff = int(time.time()) - int(max_age_days * 86400)
        where.append("created_at >= ?")
        params.append(cutoff)
    if require_same_dim:
        where.append("feature_dim = ?")
        params.append(len(qvec))
    else:
        if min_dim is not None:
            where.append("feature_dim >= ?")
            params.append(int(min_dim))
        if max_dim is not None:
            where.append("feature_dim <= ?")
            params.append(int(max_dim))

    sql = "SELECT id, centroid FROM snap_patterns"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 2000"

    cur.execute(sql, tuple(params))
    rows = cur.fetchall() or []

    scores: List[Tuple[int, float]] = []
    for row in rows:
        try:
            if isinstance(row, dict):
                pid = int(row["id"])
                cblob = row["centroid"]
            else:
                # row als Sequenz: (id, centroid)
                pid = int(row[0])
                cblob = row[1]
            cinfo = _unpack_json(cblob)
            cvec = list(map(float, (cinfo.get("centroid") or [])))
            score = cosine_similarity(
                qvec,
                cvec,
                allow_mismatch=not require_same_dim,
            )
            scores.append((pid, float(score)))
        except Exception:
            continue

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[: max(1, topk)]


# =============================================================================
# Komfort-API
# =============================================================================


def create_and_save_from_snaps(
    snaps,
    metadata: Optional[Dict[str, Any]] = None,
    store_full_payload: bool = False,
    normalize: bool = False,
) -> int:
    """Convenience: SnapPattern aus Snaps bauen und direkt speichern."""
    sp = SnapPattern.from_snaps(snaps, metadata=metadata)
    return save_pattern(
        sp,
        store_full_payload=store_full_payload,
        detect_gap_flag=True,
        normalize=normalize,
    )


def quick_similarity(
    a: Union[SnapPattern, Sequence[float], int],
    b: Union[SnapPattern, Sequence[float], int],
    *,
    allow_mismatch: bool = False,
) -> float:
    """Schneller Cosine-Vergleich zwischen zwei Patterns/Vektoren/IDs."""
    if isinstance(a, int):
        pa = load_pattern(a, full=False)
        if pa is None:
            return 0.0
        va = pa.centroid
    elif isinstance(a, SnapPattern):
        va = a.centroid
    else:
        va = list(map(float, a))

    if isinstance(b, int):
        pb = load_pattern(b, full=False)
        if pb is None:
            return 0.0
        vb = pb.centroid
    elif isinstance(b, SnapPattern):
        vb = b.centroid
    else:
        vb = list(map(float, b))

    return cosine_similarity(va, vb, allow_mismatch=allow_mismatch)


# =============================================================================
# Debug-/Selftest
# =============================================================================


def _selftest(verbose: bool = True) -> None:
    import random

    def rand_vec(dim: int = 8) -> List[float]:
        return [random.uniform(-1.0, 1.0) for _ in range(dim)]

    if verbose:
        print("[SnapPattern] Selftest startet ...")

    # Zwei ähnliche Patterns
    p1 = SnapPattern.from_snaps(
        [rand_vec(8) for _ in range(5)],
        metadata={"label": "p1"},
    )
    base = p1.centroid or rand_vec(8)
    p2 = SnapPattern.from_snaps(
        [[x + random.uniform(-0.05, 0.05) for x in base] for _ in range(4)],
        metadata={"label": "p2-near-p1"},
    )

    id1 = save_pattern(
        p1,
        store_full_payload=False,
        detect_gap_flag=True,
        normalize=True,
    )
    id2 = save_pattern(
        p2,
        store_full_payload=False,
        detect_gap_flag=True,
        normalize=True,
    )

    l1 = load_pattern(id1, full=False)
    l2 = load_pattern(id2, full=False)

    c12 = quick_similarity(l1, l2)
    if verbose:
        print(f"  cos(l1,l2)={c12:.3f} (erwartet > 0)")
        print(
            "  l1._centroid_l2_norm:",
            l1.metadata.get("_centroid_l2_norm") if l1 else None,
        )
        print(
            "  l2._centroid_l2_norm:",
            l2.metadata.get("_centroid_l2_norm") if l2 else None,
        )

    top = find_similar(l1, topk=5)
    if verbose:
        print("  ähnliche zu l1:", top)

    # Mismatch-Test: Dimension-Missmatch → Nullvektor
    p_bad = SnapPattern.from_snaps(
        [rand_vec(8), rand_vec(9)],
        metadata={"label": "bad-dim"},
    )
    assert (
        p_bad.centroid == []
    ), "Erwartet: Nullvektor bei Dimension-Missmatch im Pattern"
    id_bad = save_pattern(
        p_bad,
        store_full_payload=False,
        detect_gap_flag=True,
    )
    l_bad = load_pattern(id_bad, full=False)
    if verbose:
        print(
            "  bad-dim pattern:",
            id_bad,
            "centroid len:",
            len(l_bad.centroid if l_bad else []),
        )

    if verbose:
        print("[SnapPattern] Selftest OK ✅")


if __name__ == "__main__":
    _ensure_snappattern_schema()
    _selftest(verbose=True)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/snap.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
#            Offline-First · Headless · SQLite-First · Edge Runtime
# Modul:     Snap – atomare Moment-Repräsentation (Features + Content + Metadata)
#            inkl. Norm-Cache & Fingerprint (Dedup) + optionaler Fusion-Anheftung
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
# ZWECK / SYSTEMROLLE
# ───────────────────
# „Snap“ ist die kleinste stabile Beobachtungseinheit im ORÓMA-System:
#   - numerischer Feature-Vektor (float[]) → Similarity/Clustering/Replay
#   - optionaler Content (Text/JSON/Any)   → semantische Payload (z.B. ASR/OCR/Board)
#   - freie Metadaten (dict)              → Herkunft, Kontext, Debug, Governance
#   - optional: FusionPack                → Crossmodal-Fusion (wenn core.fusion verfügbar)
#
# Snap bildet die Grundlage für:
#   - SnapPattern (Verdichtung/Cluster über viele Snaps)
#   - SnapChain (episodische Sequenzen)
#   - Dedup & Indexierung (snap_index via core.sql_manager)
#   - Replay/DreamWorker/Trainer (Caches sparen CPU im 24/7 Betrieb)
#
# HEADLESS / PRODUKTIONSINVARIANTEN
# ─────────────────────────────────
# - Keine GUI-Abhängigkeiten (kein Qt/Wayland/X11).
# - Defensive Verarbeitung: fehlende Felder/Legacy-Dicts dürfen keinen Crash erzeugen.
# - Caches (feature_dim, l2_norm, fingerprint) müssen konsistent bleiben:
#   Änderungen am Feature-Vektor erfordern `recompute_stats()`.
#
# SNAP-SCHEMA & KOMPATIBILITÄT
# ───────────────────────────
# Snap trägt ein Schema-Label (Formatmarker) und bleibt kompatibel zu älteren Dicts:
#   - schema:        Default "snap.v1.1" (ENV override möglich)
#   - id:            optional (DB-ID aus snap_index)
#   - ts:            wall clock (epoch seconds)
#   - ts_monotonic:  monotone Zeitbasis (time.monotonic) für stabile lokale Reihenfolge
#   - privacy_tier:  Governance-Stufe (Default "internal")
#   - feature_dim:   Cache (len(features))
#   - l2_norm:       Cache (L2-Norm der Features)
#   - fingerprint:   deterministischer SHA1-basierter Kurz-Hash (Dedup/Index-Key)
#
# WICHTIG: schema/fingerprint sind Format-/Dedup-Marker, nicht “Projektversion”.
# `from_dict()` akzeptiert fehlende Felder und setzt Defaults, um alte Snap-Dumps
# weiterhin importieren/abspielen zu können.
#
# DEDUP / FINGERPRINT (WARUM)
# ──────────────────────────
# Fingerprint dient als kurzer stabiler Schlüssel für:
#   - Deduplikation in flachen Index-Tabellen (snap_index)
#   - robuste Referenzierung (z.B. Debug/Explainability/Replay)
# Er ist bewusst “leichtgewichtig” und eignet sich für Edge-Betrieb.
#
# DB-INTEGRATION (SQLite / DBWriter-KOMPATIBILITÄT)
# ────────────────────────────────────────────────
# Dieses Modul selbst öffnet keine persistenten DB-Verbindungen dauerhaft.
# Inserts/Upserts in den Index laufen über `core.sql_manager` (Helper-Funktion),
# welches:
#   - WAL/busy_timeout setzt,
#   - writer_lock/lock-retry nutzt,
#   - optional DBWriter (Single-Writer) verwendet,
#   - und in Strict-Mode lokale Writes auf managed DBs verhindert.
#
# UMGEBUNGSVARIABLEN (ENV)
# ───────────────────────
#   OROMA_SNAP_SCHEMA="snap.v1.1"
#     - überschreibt die Standard-Schema-Kennung für neu erstellte Snaps
#
#   OROMA_SNAP_PRIVACY="internal"
#     - Default privacy_tier für neue Snaps (Governance/Export-Policy)
#
# Logging (Legacy/Kompat):
#   OROMA_SNAPCHAIN_LOGLEVEL=INFO|DEBUG|WARNING|ERROR
#   OROMA_SNAPCHAIN_LEVEL=INFO|DEBUG|WARNING|ERROR
#
# ÖFFENTLICHE API (KERNMETHODEN)
# ─────────────────────────────
# Klasse Snap (typisch):
#   - __init__(features, metadata, content=None, ts=None, uid=None,
#              schema=None, privacy_tier=None, snap_id=None)
#   - recompute_stats(): aktualisiert feature_dim / l2_norm / fingerprint
#   - normalize(): L2-normalisiert Features (wenn möglich) und refresh’t caches
#   - similarity(other): Cosine-ähnliche Similarity (nutzt Norm-Caches)
#   - to_dict() / from_dict(): JSON-freundliche Serialisierung (legacy tolerant)
#   - as_blob() / from_blob(): kompakte Payload (z.B. für Index/IPC)
#   - attach_fusion(fusion_pack) / get_fusion(): optionale Fusion-Anheftung
#
# Helper:
#   - dedup_or_insert_snap(...): Best-effort Insert/Upsert in snap_index über sql_manager
#     (setzt snap.id und metadata["snap_id"]).
#
# FEHLERFÄLLE & ROBUSTHEIT
# ───────────────────────
# - fehlende/ungültige Feature-Vektoren → Similarity/Norm werden defensiv behandelt.
# - DB-Probleme beim Index-Insert → Best-effort; keine Boot-Killer; Fehler werden sichtbar geloggt.
# - Fusion ist optional: wenn core.fusion nicht verfügbar ist, bleibt Snap vollständig nutzbar.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
import hashlib
from typing import Any, Dict, List, Optional, Union
from core.log_guard import log_suppressed
import logging

# -----------------------------------------------------------------------------
# Konstante: Schema & Defaults
# -----------------------------------------------------------------------------
SNAP_SCHEMA: str = os.environ.get("OROMA_SNAP_SCHEMA", "snap.v1.1")
DEFAULT_PRIVACY_TIER: str = os.environ.get("OROMA_SNAP_PRIVACY", "internal")

# -----------------------------------------------------------------------------
# Optional: FusionPack
# -----------------------------------------------------------------------------
try:
    from core.fusion import FusionPack  # type: ignore
except Exception:
    FusionPack = None  # type: ignore[misc, assignment]

# -----------------------------------------------------------------------------
# Optional: sql_manager (SnapIndex)
# -----------------------------------------------------------------------------
try:
    from core import sql_manager  # type: ignore
except Exception:
    sql_manager = None  # type: ignore[assignment]

# -----------------------------------------------------------------------------
# Logging-Konfiguration
# -----------------------------------------------------------------------------
logger = logging.getLogger("oroma.snapchain")
if not logger.handlers:
    h = logging.StreamHandler()
    f = logging.Formatter("[snapchain] %(levelname)s: %(message)s")
    h.setFormatter(f)
    logger.addHandler(h)

# Kompatibilität:
# - Neu: OROMA_SNAPCHAIN_LOGLEVEL (präferiert)
# - Alt/Bestand: OROMA_SNAPCHAIN_LEVEL (Fallback)
_lvl = (os.environ.get("OROMA_SNAPCHAIN_LOGLEVEL")
        or os.environ.get("OROMA_SNAPCHAIN_LEVEL", "INFO")).upper()
logger.setLevel(getattr(logging, _lvl, logging.INFO))

# -----------------------------------------------------------------------------
# PRODUKTIONSFIX – Logger-Alias "LOG" (Kompatibilität)
# -----------------------------------------------------------------------------
# Hintergrund:
#   Teile des Bestands-Codes (und einige Tools/Backports) benutzen "LOG.*".
#   Dieses Modul definierte bisher nur "logger" und nutzte dennoch LOG in
#   Ausnahme-Pfaden (z. B. fingerprint-Berechnung). Das führte im Livebetrieb zu:
#     NameError: name 'LOG' is not defined
#
# Ziel:
#   - 100% nicht-destruktiv: kein Logik-/Datenformat-Change
#   - nur Alias: LOG zeigt auf denselben Logger wie "logger"
# -----------------------------------------------------------------------------
LOG = logger

# -----------------------------------------------------------------------------
# Hilfsfunktionen – Norm & Fingerprint
# -----------------------------------------------------------------------------

def _compute_l2_norm(vec: List[float]) -> float:
    """Berechnet die L2-Norm eines Feature-Vektors (0.0 bei leerem Vektor)."""
    if not vec:
        return 0.0
    return math.sqrt(sum(float(x) * float(x) for x in vec))


def _compute_fingerprint(
    schema: str,
    features: List[float],
    content: Optional[str],
    metadata: Dict[str, Any],
) -> str:
    """
    Erzeugt einen stabilen, kurzen SHA1-Fingerprint für diesen Snap.

    Design:
      • Wenn Features vorhanden:
            → Hash über (schema + gerundete Feature-Werte).
      • Wenn keine Features, aber Text:
            → Hash über (schema + content).
      • Fallback:
            → Hash über (schema + metadata["modality"]/["kind"]).

    Ziel:
      • deterministischer Fingerprint für Deduplikation (snap_index),
        unabhängig von uid oder Laufzeitzustand.
    """
    try:
        if features:
            payload = {
                "schema": schema,
                "features": [round(float(x), 6) for x in features],
            }
        elif content:
            payload = {
                "schema": schema,
                "content": str(content),
            }
        else:
            payload = {
                "schema": schema,
                "modality": metadata.get("modality"),
                "kind": metadata.get("kind"),
            }

        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:16]
    except Exception as e:
        LOG.debug("[fingerprint] Fehler bei Berechnung: %s", e)
        return ""


# =============================================================================
# Klasse: Snap
# =============================================================================

class Snap:
    """
    Snap – Repräsentiert eine Momentaufnahme im ORÓMA-System (Snap v1.1).

    Attribute (Kern)
    ----------------
    features : List[float]
        Numerischer Feature-Vektor.
    content : Optional[str]
        Optionale Text-/Payload-Repräsentation.
    metadata : Dict[str, Any]
        Kontextinformationen (Quelle, Herkunft, Fusion, Debug, etc.).
    ts : int
        UNIX-Zeit der Erstellung (Sekunden, nicht notwendigerweise monoton).
    uid : str
        Kurz-UUID (12 hex) für Logs und Nachverfolgung.

    Zusätzliche v1.1-Attribute
    --------------------------
    schema : str
        Snap-Schemakennung (Default: SNAP_SCHEMA, z. B. "snap.v1.1").
    id : Optional[int]
        Optionale DB-ID, z. B. Primärschlüssel in snap_index.
    ts_monotonic : float
        Monotone Zeitbasis (time.monotonic) für lokale Reihenfolge.
    privacy_tier : str
        Governance-Stufe (z. B. "internal", "public", "sensitive").
    feature_dim : int
        Gecachte Feature-Dimension (len(features)).
    l2_norm : float
        Gecachte L2-Norm des Feature-Vektors.
    fingerprint : str
        Kurzer SHA1-Fingerprint (hex, ca. 16 Zeichen) zur Deduplikation.
    """

    # -------------------------------------------------------------------------
    def __init__(
        self,
        features: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        content: Optional[str] = None,
        ts: Optional[int] = None,
        uid: Optional[str] = None,
        # v1.1-Erweiterungen (alle optional, kompatibel zu alten Aufrufern)
        schema: Optional[str] = None,
        privacy_tier: Optional[str] = None,
        snap_id: Optional[int] = None,
        ts_monotonic: Optional[float] = None,
        l2_norm: Optional[float] = None,
        fingerprint: Optional[str] = None,
    ):
        # Eingaben validieren
        if features is None:
            features = []
        if not isinstance(features, list):
            raise TypeError("Snap.features muss eine Liste sein")
        if not all(isinstance(x, (int, float)) for x in features):
            raise ValueError("Snap.features darf nur numerische Werte enthalten")

        # Basis-Felder
        self.features: List[float] = [float(x) for x in features]
        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.content: Optional[str] = str(content) if content is not None else None
        self.ts: int = int(ts if ts is not None else time.time())
        self.uid: str = str(uid) if uid else uuid.uuid4().hex[:12]

        # v1.1: Schema / Privacy / ID / ts_monotonic
        # ------------------------------------------
        # Schema – kann von außen übergeben oder aus metadata["schema"] kommen.
        self.schema: str = (
            str(schema)
            if schema is not None
            else str(self.metadata.get("schema") or SNAP_SCHEMA)
        )

        # Privacy – Governance-Flag, ggf. aus metadata.
        self.privacy_tier: str = (
            str(privacy_tier)
            if privacy_tier is not None
            else str(self.metadata.get("privacy_tier") or DEFAULT_PRIVACY_TIER)
        )

        # Optionale DB-ID (snap_index.id)
        self.id: Optional[int] = int(snap_id) if snap_id is not None else (
            int(self.metadata.get("snap_id"))  # type: ignore[arg-type]
            if isinstance(self.metadata.get("snap_id"), (int, float))
            else None
        )

        # Monotone Zeitbasis für lokale Reihenfolge.
        try:
            mono = float(ts_monotonic) if ts_monotonic is not None else float(time.monotonic())
        except Exception:
            mono = float(time.monotonic())
        self.ts_monotonic: float = mono

        # Modality ermitteln (falls nicht gesetzt)
        if "modality" not in self.metadata:
            if self.content:
                self.metadata["modality"] = "text"
            elif self.features:
                self.metadata["modality"] = "vector"
            else:
                self.metadata["modality"] = "unknown"

        # Schema & Privacy beständig auch in metadata spiegeln (für Downstream)
        self.metadata.setdefault("schema", self.schema)
        self.metadata.setdefault("privacy_tier", self.privacy_tier)
        if self.id is not None:
            self.metadata.setdefault("snap_id", self.id)

        # v1.1: Statistiken & Fingerprint
        self.feature_dim: int = 0
        self.l2_norm: float = 0.0
        self.fingerprint: str = ""

        # initiale Berechnung (ggf. mit vorgegebenem l2_norm/fingerprint)
        self._recompute_stats(initial_l2_norm=l2_norm, initial_fingerprint=fingerprint)

        LOG.debug(
            "[init] Snap erstellt uid=%s mode=%s feats=%d schema=%s l2=%.4f",
            self.uid,
            self.metadata.get("modality"),
            len(self.features),
            self.schema,
            self.l2_norm,
        )

    # -------------------------------------------------------------------------
    # Interne Hilfsfunktionen für v1.1-Stats
    # -------------------------------------------------------------------------
    def _recompute_stats(
        self,
        initial_l2_norm: Optional[float] = None,
        initial_fingerprint: Optional[str] = None,
    ) -> None:
        """
        Setzt feature_dim, l2_norm und fingerprint konsistent.
        Wird im Konstruktor und nach Feature-Änderungen (normalize) aufgerufen.
        """
        self.feature_dim = len(self.features)

        if initial_l2_norm is not None:
            try:
                self.l2_norm = float(initial_l2_norm)
            except Exception:
                self.l2_norm = _compute_l2_norm(self.features)
        else:
            self.l2_norm = _compute_l2_norm(self.features)

        if initial_fingerprint:
            self.fingerprint = str(initial_fingerprint)
        else:
            self.fingerprint = _compute_fingerprint(
                schema=self.schema,
                features=self.features,
                content=self.content,
                metadata=self.metadata,
            )

    def recompute_stats(self) -> None:
        """
        Öffentliche Helper-Funktion, falls externe Aufrufer .features direkt
        verändern. Im Normalfall reicht normalize(), das automatisch aufruft.
        """
        self._recompute_stats()

    # -------------------------------------------------------------------------
    # Fusion-Handling
    # -------------------------------------------------------------------------
    def attach_fusion(self, fusion_pack: "FusionPack") -> None:
        """Hängt multimodale Repräsentation an (als JSON)."""
        if not fusion_pack or FusionPack is None:
            return
        try:
            self.metadata["fusion"] = fusion_pack.to_json()
            LOG.debug("[attach_fusion] Fusion an Snap %s angehängt", self.uid)
        except Exception as e:
            LOG.warning("[attach_fusion] Fehler: %s", e)

    def get_fusion(self) -> Optional["FusionPack"]:
        """Gibt FusionPack zurück, falls vorhanden."""
        if FusionPack is None:
            return None
        try:
            j = self.metadata.get("fusion")
            if j:
                fp = FusionPack.from_json(j)  # type: ignore[attr-defined]
                LOG.debug("[get_fusion] FusionPack rekonstruiert für %s", self.uid)
                return fp
        except Exception as e:
            LOG.warning("[get_fusion] Fehler: %s", e)
        return None

    # -------------------------------------------------------------------------
    # Feature-Operationen
    # -------------------------------------------------------------------------
    def normalize(self) -> None:
        """
        Normalisiert Feature-Vektor (L2) in-place.

        Hinweis:
          • Danach ist l2_norm ≈ 1.0 (sofern Features nicht alle 0.0 waren).
          • feature_dim und fingerprint werden aktualisiert.
        """
        if not self.features:
            return
        norm = _compute_l2_norm(self.features)
        if norm > 0.0:
            self.features = [float(f) / norm for f in self.features]
            LOG.debug("[normalize] Snap %s normalisiert (vorher L2=%.4f)", self.uid, norm)
        else:
            LOG.debug("[normalize] Snap %s: Norm=0 → keine Normalisierung", self.uid)
        # Stats & Fingerprint aktualisieren
        self._recompute_stats()

    def similarity(
        self,
        other: Union["Snap", List[float]],
        *,
        pad: bool = False,
    ) -> float:
        """
        Kosinus-Ähnlichkeit zu anderem Snap/Vector.

        Parameter
        ---------
        other : Snap | List[float]
            Vergleichsobjekt (anderer Snap oder roher Feature-Vektor).
        pad : bool, default False
            Wenn True und Dimensionen verschieden:
              → Vergleich auf min(len(a), len(b)); sonst 0.0 bei Mismatch.
        """
        vec = other.features if isinstance(other, Snap) else other
        if not vec:
            return 0.0
        a, b = self.features, vec
        if not a or not b:
            return 0.0
        if len(a) != len(b):
            if not pad:
                return 0.0
            d = min(len(a), len(b))
            a, b = a[:d], b[:d]
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        na = math.sqrt(sum(float(x) * float(x) for x in a))
        nb = math.sqrt(sum(float(y) * float(y) for y in b))
        if na <= 0.0 or nb <= 0.0:
            return 0.0
        sim = float(dot / (na * nb))
        LOG.debug(
            "[similarity] Snap %s vs %s -> %.3f",
            self.uid,
            getattr(other, "uid", "?"),
            sim,
        )
        return sim

    # -------------------------------------------------------------------------
    # Metadata Utilities
    # -------------------------------------------------------------------------
    def with_metadata(self, **kwargs: Any) -> "Snap":
        """
        Erzeugt eine (inhaltlich identische) Kopie dieses Snaps mit
        erweiterten/überschriebenen Metadaten.

        WICHTIG:
          • schema, privacy_tier, id, ts, ts_monotonic, uid und Features bleiben erhalten.
          • Fingerprint/L2 werden für den neuen Snap neu berechnet.
        """
        md = dict(self.metadata)
        md.update(kwargs)
        LOG.debug("[with_metadata] Snap %s erweitert mit %s", self.uid, list(kwargs.keys()))
        return Snap(
            features=list(self.features),
            metadata=md,
            content=self.content,
            ts=self.ts,
            uid=self.uid,
            schema=self.schema,
            privacy_tier=self.privacy_tier,
            snap_id=self.id,
            ts_monotonic=self.ts_monotonic,
        )

    def merge_metadata(self, extra: Dict[str, Any]) -> None:
        """
        Mergt zusätzliche Metadaten in-place in self.metadata.
        """
        if not extra:
            return
        self.metadata.update(extra)
        LOG.debug("[merge_metadata] Snap %s merge %s", self.uid, list(extra.keys()))

    # -------------------------------------------------------------------------
    # Serialisierung
    # -------------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialisiert den Snap in ein Dict.

        Felder:
          • features, content, metadata, ts, uid
          • schema, id, ts_monotonic, privacy_tier
          • feature_dim, l2_norm, fingerprint
        """
        d = {
            "features": self.features,
            "content": self.content,
            "metadata": self.metadata,
            "ts": int(self.ts),
            "uid": self.uid,
            # v1.1-Felder
            "schema": self.schema,
            "id": self.id,
            "ts_monotonic": float(self.ts_monotonic),
            "privacy_tier": self.privacy_tier,
            "feature_dim": int(self.feature_dim),
            "l2_norm": float(self.l2_norm),
            "fingerprint": self.fingerprint,
        }
        LOG.debug("[to_dict] Snap %s serialisiert (schema=%s)", self.uid, self.schema)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Snap":
        """
        Rekonstruiert einen Snap aus einem Dict.

        Kompatibilität:
          • Alte Snaps ohne v1.1-Felder funktionieren weiterhin:
                - schema → SNAP_SCHEMA
                - privacy_tier → DEFAULT_PRIVACY_TIER
                - id, ts_monotonic, l2_norm, fingerprint werden bei Bedarf neu gesetzt.
        """
        if not isinstance(d, dict):
            raise TypeError("Snap.from_dict erwartet ein Dict")

        features = d.get("features", [])
        metadata = d.get("metadata", {}) or {}
        content = d.get("content")
        ts = d.get("ts")
        uid = d.get("uid")

        schema = d.get("schema")
        privacy_tier = d.get("privacy_tier")
        snap_id = d.get("id") or d.get("snap_id")
        ts_monotonic = d.get("ts_monotonic")
        l2_norm = d.get("l2_norm")
        fingerprint = d.get("fingerprint")

        snap = cls(
            features=list(features or []),
            metadata=dict(metadata),
            content=str(content) if content is not None else None,
            ts=int(ts) if ts is not None else None,
            uid=str(uid) if uid is not None else None,
            schema=schema,
            privacy_tier=privacy_tier,
            snap_id=int(snap_id) if isinstance(snap_id, (int, float)) else None,
            ts_monotonic=float(ts_monotonic) if ts_monotonic is not None else None,
            l2_norm=float(l2_norm) if l2_norm is not None else None,
            fingerprint=str(fingerprint) if fingerprint is not None else None,
        )
        LOG.debug("[from_dict] Snap %s geladen (schema=%s)", snap.uid, snap.schema)
        return snap

    def as_blob(self) -> bytes:
        """
        Serialisiert den Snap als kompakten JSON-Blob (UTF-8).
        """
        blob = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        LOG.debug("[as_blob] Snap %s als Blob (%d Bytes)", self.uid, len(blob))
        return blob

    @classmethod
    def from_blob(cls, blob: bytes) -> "Snap":
        """
        Rekonstruiert einen Snap aus einem JSON-Blob.
        """
        d = json.loads(blob.decode("utf-8"))
        s = cls.from_dict(d)
        LOG.debug("[from_blob] Snap %s deserialisiert", s.uid)
        return s

    # -------------------------------------------------------------------------
    # Debug
    # -------------------------------------------------------------------------
    def __repr__(self) -> str:
        mode = self.metadata.get("modality")
        return (
            f"<Snap uid={self.uid} mode={mode} feats={len(self.features)} "
            f"schema={self.schema} l2={self.l2_norm:.3f} "
            f"content={'yes' if self.content else 'no'}>"
        )

    def short_info(self) -> str:
        return (
            f"Snap(uid={self.uid}, feats={len(self.features)}, "
            f"mode={self.metadata.get('modality')}, "
            f"schema={self.schema}, l2={self.l2_norm:.3f})"
        )


# =============================================================================
# Helper: Snap → snap_index (Deduplikation & Insert)
# =============================================================================

def dedup_or_insert_snap(
    snap: Snap,
    *,
    source: Optional[str] = None,
    privacy_tier: Optional[str] = None,
    payload: Optional[bytes] = None,
    dedup: bool = True,
    update_metadata: bool = True,
) -> Optional[int]:
    """
    Registriert einen Snap im SQL-SnapIndex (snap_index) und liefert die ID.

    Semantik
    --------
    • Nutzt Snap v1.1-Stats (feature_dim, l2_norm, fingerprint).
    • Wenn dedup=True und Fingerprint gesetzt:
         → sql_manager.insert_snap_index(..., dedup=True)
           aktualisiert bestehende Zeile oder legt neue an.
    • Bei Erfolg:
         → snap.id wird gesetzt/aktualisiert
         → snap.metadata["snap_id"] und snap.metadata["_snap_index_id"]
            spiegeln die ID.

    Parameter
    ---------
    snap : Snap
        Der zu registrierende Snap.
    source : str|None
        Optionaler Source-Override (sonst metadata["source"] oder "unknown").
    privacy_tier : str|None
        Optionaler Override für privacy_tier (sonst snap.privacy_tier).
    payload : bytes|None
        Optionale Payload (Default: snap.as_blob()).
    dedup : bool
        Fingerprint-basierte Deduplikation aktivieren (Default: True).
    update_metadata : bool
        Bei erfolgreichem Insert/Upsert ID in Snap.metadata spiegeln.

    Rückgabewert
    ------------
    int|None : snap_index.id oder None bei Fehler / fehlendem sql_manager.
    """
    if sql_manager is None or not hasattr(sql_manager, "insert_snap_index"):
        LOG.debug("[dedup_or_insert_snap] sql_manager.insert_snap_index nicht verfügbar")
        return None

    # Sicherstellen, dass Stats/Fingerprint aktuell sind
    if not snap.feature_dim or (snap.features and snap.feature_dim != len(snap.features)):
        snap.recompute_stats()
    if not snap.fingerprint:
        snap.recompute_stats()

    src = (
        source
        or str(snap.metadata.get("source"))
        or str(snap.metadata.get("origin"))
        or "unknown"
    )
    priv = privacy_tier or snap.privacy_tier
    blob = payload if payload is not None else snap.as_blob()

    try:
        sid = sql_manager.insert_snap_index(  # type: ignore[attr-defined]
            ts=float(snap.ts),
            source=src,
            privacy_tier=priv,
            feature_dim=int(snap.feature_dim),
            l2_norm=float(snap.l2_norm),
            fingerprint=snap.fingerprint or None,
            payload=blob,
            dedup=dedup,
        )
    except Exception as e:
        LOG.warning("[dedup_or_insert_snap] Fehler beim Insert: %s", e)
        return None

    if sid is None:
        return None

    # Snap-Objekt mit ID anreichern
    snap.id = int(sid)
    if update_metadata:
        try:
            snap.metadata["snap_id"] = int(sid)
            snap.metadata["_snap_index_id"] = int(sid)
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.snap.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

    LOG.debug(
        "[dedup_or_insert_snap] Snap uid=%s → snap_index.id=%s (src=%s, priv=%s, fp=%s)",
        snap.uid,
        sid,
        src,
        priv,
        snap.fingerprint,
    )
    return sid


# =============================================================================
# Mini-Selftest
# =============================================================================
if __name__ == "__main__":
    # Kleiner Sanity-Check für Snap v1.1 + SnapIndex-Helper
    v = [0.1, -0.2, 0.3, 0.4]
    s = Snap(v, metadata={"source": "selftest"}, content="hello world")
    print("Snap:", s)
    print("  feature_dim:", s.feature_dim)
    print("  l2_norm:", s.l2_norm)
    print("  schema:", s.schema)
    print("  privacy_tier:", s.privacy_tier)
    print("  fingerprint:", s.fingerprint)

    s.normalize()
    print("\nNach normalize():")
    print("  features:", s.features)
    print("  l2_norm:", s.l2_norm)
    print("  fingerprint:", s.fingerprint)

    blob = s.as_blob()
    s2 = Snap.from_blob(blob)
    print("\nRekonstruierter Snap:", s2.short_info())
    print("  gleiche fingerprint?:", s2.fingerprint == s.fingerprint)

    # Optional: SnapIndex-Selftest (nur wenn sql_manager verfügbar ist)
    sid = dedup_or_insert_snap(s)
    print("\nSnapIndex-ID:", sid)
    if sid is not None:
        print("  metadata.snap_id:", s.metadata.get("snap_id"))
        print("  metadata._snap_index_id:", s.metadata.get("_snap_index_id"))

    print("[snap] Selftest OK ✅")
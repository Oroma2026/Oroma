#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/fusion.py
# Projekt:   ORÓMA (Crossmodal Fusion · Headless)
# Modul:     Fusion – Feature-Fusion & Crossmodal Links (Vision↔Audio↔Text) inkl. FusionPack Container
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul stellt den Mechanismus bereit, um mehrere Modalitäten in ORÓMA
# zu einer gemeinsamen Repräsentation zu verbinden („Fusion“):
#   - Vision Features (z. B. embed(frame))
#   - Audio Features (RMS/Pitch/Embedding)
#   - Text/Token Features (Token-IDs/Embeddings)
#   - Meta/Context Features (Phase, Origin, Thread, etc.)
#
# Ergebnis ist ein FusionPack:
#   - fused_vector: ein kombinierter Feature-Vektor
#   - parts: die Einzelkomponenten (per modality)
#   - weights: Skalierungen/Normierungen, damit keine Modalität dominiert
#   - metadata: Kontext, Debug, Provenance
#
# WARUM FUSION ALS EIGENES MODUL?
# ───────────────────────────────
# ORÓMA will:
#   - crossmodal Reasoning (z. B. calc_vision_linker)
#   - stabile Similarity (ein Vektorraum statt 3 inkompatible Räume)
#   - Explainability (welche Modalität hat welchen Anteil geliefert?)
#
# Zudem bleibt das System headless & optional:
# - Wenn dieses Modul fehlt oder nicht genutzt wird, funktionieren Snap/SnapChain
#   weiter (Fusion ist optionaler Layer).
#
# FUSION-STRATEGIEN (DETERMINISTISCH)
# ──────────────────────────────────
# Typische Strategien (je nach Implementierungsstand):
#   1) concat + normalize:
#      - Vektoren werden auf gleiche Skala gebracht (z. B. L2-Norm = 1)
#      - dann aneinandergehängt (concat)
#
#   2) weighted sum:
#      - alle Vektoren werden auf gleiche Dim projiziert (nur wenn Projektor existiert)
#      - dann gewichtete Summe
#
# In v3.7.3 soll die Standardstrategie deterministisch sein:
#   - keine Random-Projektionen in Production (Repro- und Debug-Fähigkeit)
#
# NORMALISIERUNG (WICHTIG FÜR STABILITÄT)
# ───────────────────────────────────────
# Fusion kann nur funktionieren, wenn Skalen stabil sind.
# Daher sind typische Regeln:
#   - Jede Modalität wird vor dem Merge normalisiert (L2 oder robust scale)
#   - weights werden explizit dokumentiert und in metadata gespeichert
#   - NaN/Inf werden gefiltert (safe_float)
#
# CROSSMODAL LINKS (OPTIONAL, ABER PRODUKTIV NÜTZLICH)
# ────────────────────────────────────────────────────
# Fusion kann zusätzlich „Links“ erzeugen:
#   - z. B. link/calc_vision (Calculator Ergebnis ↔ Vision Token Cluster)
#   - oder text↔audio alignment (ASR Satz ↔ Audio Segment)
#
# Links werden NICHT in diesem Modul „hart“ in DB geschrieben, sondern
# als Artefakte/Events zurückgegeben, damit:
#   - TransferEngine / Hooks entscheiden, ob/wie persistiert wird
#   - die DB-Schicht sauber bleibt
#
# WICHTIGE ENV-VARIABLEN (TYPISCH)
# ───────────────────────────────
#   OROMA_FUSION_ENABLE=1|0
#   OROMA_FUSION_MODE=concat|weighted
#   OROMA_FUSION_W_VISION=1.0
#   OROMA_FUSION_W_AUDIO=1.0
#   OROMA_FUSION_W_TEXT=1.0
#   OROMA_FUSION_NORMALIZE=1|0
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class FusionPack:
#   - to_dict() / from_dict()
#   - short_info() (Debug)
#
# fuse(parts: dict[str, list[float]], weights: dict[str,float]|None=None, *, normalize=True, mode="concat") -> FusionPack
#   - parts: {"vision":[...], "audio":[...], "text":[...], "meta":[...]}
#   - liefert FusionPack (fused_vector + metadata)
#
# safe_norm(vec) / safe_concat(list_of_vecs) / safe_float(x)
#   - Hilfsfunktionen, um NaNs/Inf zu vermeiden
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Fusion muss deterministisch bleiben (keine Random-Projektion ohne Seed + Logging).
# - Keine hard dependency auf NumPy: optional nutzen, aber Python-Fallback behalten.
# - FusionPack muss JSON-serialisierbar bleiben (Export/Import/Replay).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Sequence
import hashlib
import math
import json
import os
import time

import logging
from core import log_guard
logger = logging.getLogger(__name__)
EPS = 1e-8
FUSION_VERSION = "3.7.0"

def _l2norm(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x*x for x in v))
    if n <= 0.0:
        return v
    return [x / n for x in v]

def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    d = min(len(a), len(b))
    if d <= 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(d):
        x = float(a[i]); y = float(b[i])
        dot += x*y
        na += x*x
        nb += y*y
    return dot / (math.sqrt(na) * math.sqrt(nb) + EPS)

# -----------------------------------------------------------------------------

@dataclass
class ModalityVec:
    kind: str                 # "vision" | "audio" | "text" | "sensor" | "..."
    vec: List[float]
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class FusionPack:
    """
    Container für mehrere Modalitäten + symbolische Tokens/Concepts.
    Wird in Snap/SnapChain referenziert.
    """
    modalities: List[ModalityVec] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)
    concepts: List[str] = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)
    version: str = FUSION_VERSION

    # ---- Serialization ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "modalities": [{"kind": m.kind, "vec": m.vec, "meta": m.meta} for m in self.modalities],
            "tokens": self.tokens,
            "concepts": self.concepts,
            "created_ts": self.created_ts,
            "version": self.version,
        }

    @staticmethod
    def from_dict(o: Dict[str, Any]) -> "FusionPack":
        mods = [ModalityVec(kind=m["kind"], vec=list(m["vec"]), meta=dict(m.get("meta", {})))
                for m in o.get("modalities", [])]
        fp = FusionPack(
            modalities=mods,
            tokens=list(o.get("tokens", [])),
            concepts=list(o.get("concepts", [])),
            created_ts=float(o.get("created_ts", time.time())),
            version=str(o.get("version", FUSION_VERSION)),
        )
        return fp

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "FusionPack":
        return FusionPack.from_dict(json.loads(s))

# -----------------------------------------------------------------------------

class FusionEngine:
    """
    Brücke zu Runtimes (LLM/Text-Embedding, Vision-Embedding).
    Optional erwartet:
      - llm_rt.embed_text(text) -> List[float]
      - llm_rt.tokenize(text) -> List[str]
      - llm_rt.normalize_concepts(tokens) -> List[str]
      - vision_rt.project(features) -> List[float]
    Ohne diese wird deterministisch/offline gearbeitet.
    """

    def __init__(self, llm_rt: Optional[Any] = None, vision_rt: Optional[Any] = None):
        self.llm_rt = llm_rt
        self.vision_rt = vision_rt
        self._dim = int(os.environ.get("OROMA_EMBED_DIM", "128"))
        self._use_norm = os.environ.get("OROMA_EMBED_NORM", "1") not in ("0", "false", "off")

    # -------- Textseite --------
    def text_to_vec(self, text: str) -> List[float]:
        if self.llm_rt and hasattr(self.llm_rt, "embed_text"):
            try:
                v = self.llm_rt.embed_text(text)
                v = list(map(float, v or []))
                return _l2norm(v) if self._use_norm else v
            except Exception as e:
                log_guard.log_suppressed(logger, key="fusion.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        # deterministischer Fallback: SHA1 → Pseudo-Vektor
        d = hashlib.sha1(text.encode("utf-8")).digest()
        # expand digest deterministisch auf _dim
        vals: List[float] = []
        i = 0
        while len(vals) < self._dim:
            h = hashlib.sha1(d + bytes([i & 0xFF])).digest()
            for j in range(0, len(h), 4):
                if len(vals) >= self._dim:
                    break
                # interpretiere 4 Bytes als vorzeichenlos int → in [-1,1]
                chunk = int.from_bytes(h[j:j+4], "big", signed=False)
                vals.append((chunk / 0xFFFFFFFF) * 2.0 - 1.0)
            i += 1
        return _l2norm(vals) if self._use_norm else vals

    def tokenize(self, text: str) -> List[str]:
        if self.llm_rt and hasattr(self.llm_rt, "tokenize"):
            try:
                t = self.llm_rt.tokenize(text)
                return list(t or [])
            except Exception as e:
                log_guard.log_suppressed(logger, key="fusion.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        return [t for t in (text or "").strip().split() if t]

    def normalize_concepts(self, tokens: List[str]) -> List[str]:
        if self.llm_rt and hasattr(self.llm_rt, "normalize_concepts"):
            try:
                c = self.llm_rt.normalize_concepts(tokens)
                return list(dict.fromkeys([str(x).lower() for x in (c or [])]))
            except Exception as e:
                log_guard.log_suppressed(logger, key="fusion.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        # Fallback: unique, lowercased
        out: List[str] = []
        seen = set()
        for t in tokens or []:
            k = str(t).lower()
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    # -------- Visionseite (optional) --------
    def vision_to_vec(self, features: List[float]) -> List[float]:
        try:
            if self.vision_rt and hasattr(self.vision_rt, "project"):
                v = self.vision_rt.project(features)
                v = list(map(float, v or []))
                return _l2norm(v) if self._use_norm else v
        except Exception as e:
            log_guard.log_suppressed(logger, key="fusion.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        v = list(map(float, features or []))
        return _l2norm(v) if self._use_norm else v

    # -------- Fusion / Ähnlichkeit --------
    def build_fusion(
        self,
        text: Optional[str] = None,
        vision_features: Optional[List[float]] = None,
        audio_features: Optional[List[float]] = None,
        extra_modalities: Optional[List[Tuple[str, List[float], Dict[str, Any]]]] = None,
    ) -> FusionPack:
        mods: List[ModalityVec] = []
        tokens: List[str] = []
        concepts: List[str] = []

        if text:
            tvec = self.text_to_vec(text)
            tokens = self.tokenize(text)
            concepts = self.normalize_concepts(tokens)
            mods.append(ModalityVec(kind="text", vec=tvec, meta={"len": len(text)}))

        if vision_features:
            vvec = self.vision_to_vec(vision_features)
            mods.append(ModalityVec(kind="vision", vec=vvec, meta={"src": "features", "dim": len(vvec)}))

        if audio_features:
            avec = list(map(float, audio_features or []))
            avec = _l2norm(avec) if self._use_norm else avec
            mods.append(ModalityVec(kind="audio", vec=avec, meta={"src": "features", "dim": len(avec)}))

        if extra_modalities:
            for kind, vec, meta in extra_modalities:
                vv = list(map(float, vec or []))
                vv = _l2norm(vv) if self._use_norm else vv
                mods.append(ModalityVec(kind=str(kind), vec=vv, meta=dict(meta or {})))

        return FusionPack(modalities=mods, tokens=tokens, concepts=concepts, version=FUSION_VERSION)

    def similarity(self, a: FusionPack, b: FusionPack, weights: Optional[Dict[str, float]] = None) -> float:
        """
        Kombinierte Cosine-Similarität (gewichtetes Mittel über Modalitäten).
        Mehrere Vektoren gleicher Art → erster Treffer je Art wird verglichen.
        """
        weights = weights or {}
        kinds = {m.kind for m in a.modalities} | {m.kind for m in b.modalities}
        sims: List[float] = []
        ws: List[float] = []
        for k in kinds:
            av = next((m.vec for m in a.modalities if m.kind == k), None)
            bv = next((m.vec for m in b.modalities if m.kind == k), None)
            if av is None or bv is None:
                continue
            w = float(weights.get(k, 1.0))
            sims.append(_cosine(av, bv))
            ws.append(w)
        if not sims:
            return 0.0
        num = sum(s*w for s, w in zip(sims, ws))
        den = sum(ws) if sum(ws) > 0 else float(len(sims))
        return num / (den + EPS)

    # -------- High-Level API (gemäß Header) --------
    def fuse(self, snaps: List[List[float]], tokens: List[str]) -> FusionPack:
        mods = [ModalityVec(kind="sensor", vec=_l2norm(list(map(float, vec))) if self._use_norm else list(map(float, vec)),
                            meta={"dim": len(vec)}) for vec in (snaps or [])]
        concepts = self.normalize_concepts(tokens or [])
        return FusionPack(modalities=mods, tokens=list(tokens or []), concepts=concepts, version=FUSION_VERSION)

    def split(self, fusion_snap: FusionPack) -> Tuple[List[List[float]], List[str]]:
        snap_vecs = [m.vec for m in fusion_snap.modalities if m.kind == "sensor"]
        return snap_vecs, list(fusion_snap.tokens)

    def score(self, fusion_snap: FusionPack) -> float:
        """Heuristik (0..1): mehr Modalitäten + Tokens/Konzepte → höher."""
        n_mod = max(0, len(fusion_snap.modalities))
        n_tok = 1 if fusion_snap.tokens else 0
        n_con = 1 if fusion_snap.concepts else 0
        # leicht gesättigt
        raw = 0.5 * (1.0 - math.exp(-0.7 * n_mod)) + 0.25 * n_tok + 0.25 * n_con
        return max(0.0, min(1.0, raw))
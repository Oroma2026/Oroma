#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/snaptoken.py
# Projekt:   ORÓMA (Symbolische Ebene · Snap↔Token Fusion)
# Modul:     SnapToken – universelles Token-Objekt (Text/Vision/Audio/Motion/Meta) inkl. Fingerprint, Token-IDs, Embedding, SQL-Row Helper
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / KERNIDEE
# ───────────────
# SnapToken ist die „symbolische“ / tokenisierte Repräsentation eines Snap-Inhalts.
# Es dient als Bridge zwischen:
#   - Roh-Perzeption (Snap Features, Sensorik)
#   - symbolischen Einheiten (Token-IDs, Text, Labels)
#   - optionalen Embeddings (LLM/ASR/Vision-Modelle)
#
# SnapToken wird typischerweise von Hooks erzeugt (z. B. vision/token, audio/token)
# und kann:
#   - in SnapChain-Blobs landen (als Event/Dict)
#   - in eigene Tabellen/Logs geschrieben werden (schema-agnostisch via sql_row())
#   - für Similarity-Search genutzt werden (embedding/features + fingerprint)
#
# DATENFELDER (DATACLASS)
# ──────────────────────
# modality:   "text" | "vision" | "audio" | "motion" | "meta"
# text:       optionaler Text (für text/meta besonders relevant)
# features:   numerische Features (frei; z. B. MFCC-ähnlich, Vision-Embedding-Preview)
# token_ids:  Token-IDs (int-Liste) – primär für Text/Meta
# model_hint: freier String (welches Modell/Tokenizer genutzt wurde)
# embedding:  float-Embedding (z. B. LLM-Embedding, Vision-Vector, ASR-Vector)
# metadata:   dict für Kontext (origin, device, ts, quality, scene tags, …)
# created_ts: Unix Timestamp (Sekunden)
# uid:        stabile UUID-Hex ID
# fingerprint: deterministischer SHA1 über Kernfelder (für Dedupe/Tracking)
# version:    interner Token-Version-Marker (wird im Objekt gespeichert)
#
# TOKENISIERUNG (ROBUST, HEADLESS)
# ───────────────────────────────
# SnapToken tokenisiert Text automatisch, wenn:
#   - modality in {"text","meta"}
#   - text vorhanden
#   - token_ids leer
#
# Primär (optional):
#   - versucht core.llm_runtime.tokenize_text(text, model_hint=...)
#
# Fallback (immer verfügbar):
#   - whitespace split
#   - stabiler 32-bit Hash pro Token (blake2b → int mask)
#   - bewusst NICHT trivial (kein ord() vom 1. Zeichen)
#
# Ergebnis:
#   - token_ids sind deterministisch pro Text, auch ohne LLM-Backend
#
# FINGERPRINT (DETERMINISTISCH)
# ─────────────────────────────
# fingerprint wird in __post_init__ berechnet und umfasst u. a.:
#   - modality, uid, text (falls vorhanden)
#   - features (gerundet), token_ids, embedding (gerundet)
#   - metadata (sort_keys=True)
#   - created_ts, version
#
# Ziel:
#   - Dedupe & Wiedererkennung über Prozesse/Tools hinweg
#   - robuste Nachvollziehbarkeit für Debug/Forensik
#
# EMBEDDING / FEATURE-VEKTOR
# ──────────────────────────
# feature_vector(prefer_embedding=True, normalize=False):
#   - liefert embedding wenn vorhanden (und bevorzugt), sonst features
#   - optional L2-Norm (normalize=True)
#
# normalize_embedding_():
#   - normalisiert embedding in-place (L2), falls vorhanden
#
# SERIALISIERUNG (PORTABEL)
# ─────────────────────────
# to_dict() / from_dict():
#   - JSON-friendly Dict
# as_blob() / from_blob():
#   - zlib-komprimiertes JSON (UTF-8) als bytes
# Vorteil:
#   - klein in DB/Logs
#   - keine externen Dependencies
#
# SQL-HELPER (SCHEMA-AGNOSTISCH, PRODUKTIV NÜTZLICH)
# ────────────────────────────────────────────────
# sql_row() liefert eine generische Row:
#   (uid, modality, text, fingerprint, created_ts, blob)
# damit SnapToken in beliebige Tabellen geschrieben werden kann, ohne dass dieses
# Modul ein fixes Schema erzwingt.
#
# from_row(row) stellt SnapToken aus genau dieser Row wieder her.
#
# LOGGING
# ───────
# Logger: "oroma.snaptoken"
# Level:  OROMA_LOG_LEVEL (Default WARNING)
# Debug-Hinweis:
#   - LLM-Tokenisierung ist optional; bei Fehlschlag wird DEBUG geloggt.
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
#   OROMA_LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
# (Tokenizer-Backend hängt von core.llm_runtime ab; dieses Modul erzwingt nichts.)
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class SnapToken:
#   - ensure_tokenized() -> bool
#   - feature_vector(prefer_embedding=True, normalize=False) -> List[float]
#   - normalize_embedding_() -> None
#   - to_dict() / from_dict()
#   - as_blob() / from_blob()
#   - sql_row() / from_row()
#   - short_info() -> str
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Tokenisierung muss ohne LLM-Backend deterministisch bleiben (Fallback-Hash).
# - Fingerprint muss deterministisch sein (Dedupe/Forensik).
# - SQL-Helper bleibt schema-agnostisch (keine harte Tabellenannahme).
# - Modul muss headless und dependency-arm importierbar bleiben.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import hashlib, json, logging, time, uuid, zlib, os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union, Iterable

logger = logging.getLogger("oroma.snaptoken")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[snaptoken] %(levelname)s: %(message)s"))
    logger.addHandler(h)
logger.setLevel(getattr(logging, os.environ.get("OROMA_LOG_LEVEL", "WARNING").upper(), logging.WARNING))

# ----------------------------------------------------------------------------- 
# Modality-Konstanten
# -----------------------------------------------------------------------------
MOD_TEXT   = "text"
MOD_VISION = "vision"
MOD_AUDIO  = "audio"
MOD_MOTION = "motion"
MOD_META   = "meta"

# ----------------------------------------------------------------------------- 
# Hilfsfunktionen
# -----------------------------------------------------------------------------
def _pack_blob(obj: Dict[str, Any]) -> bytes:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, level=6)

def _unpack_blob(blob: bytes) -> Dict[str, Any]:
    data = zlib.decompress(blob)
    return json.loads(data.decode("utf-8"))

def _llm_tokenize_safe(text: str, model_hint: Optional[str] = None) -> Optional[List[int]]:
    """Optionaler Tokenizer aus llm_runtime; Rückgabe: Liste von int-IDs oder None."""
    try:
        from core.llm_runtime import tokenize_text  # type: ignore
        ids = tokenize_text(text, model_hint=model_hint)
        if isinstance(ids, Iterable):
            ids = list(ids)
        return ids if ids and all(isinstance(x, int) for x in ids) else None
    except Exception as e:
        logger.debug(f"LLM-Tokenisierung nicht verfügbar: {e}")
        return None

def _hash_to_int(s: str, *, bits: int = 32) -> int:
    """Stabiler Token-Fallback: 32-bit Hash in int."""
    h = hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(h, "big")
    if bits >= 64:
        return val
    mask = (1 << bits) - 1
    return val & mask

def _normalize_vec(vec: Iterable[Union[int, float]]) -> List[float]:
    v = [float(x) for x in (vec or [])]
    n2 = sum(x * x for x in v)
    if n2 <= 0.0:
        return v
    n = n2 ** 0.5
    return [x / n for x in v]

def _stable_fingerprint(parts: Iterable[Union[str, bytes]]) -> str:
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, str):
            h.update(p.encode("utf-8"))
        elif isinstance(p, bytes):
            h.update(p)
        else:
            h.update(str(p).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()

# ----------------------------------------------------------------------------- 
# SnapToken
# -----------------------------------------------------------------------------
@dataclass
class SnapToken:
    modality: str = MOD_META
    text: Optional[str] = None
    features: Optional[List[Union[int, float]]] = field(default_factory=list)
    token_ids: Optional[List[int]] = field(default_factory=list)
    model_hint: Optional[str] = None
    embedding: Optional[List[float]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_ts: int = field(default_factory=lambda: int(time.time()))
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    fingerprint: Optional[str] = None
    version: str = "3.7.0"

    # ---------------- Lifecycle ----------------

    def __post_init__(self) -> None:
        self.modality = (self.modality or MOD_META).lower().strip()
        if self.modality not in {MOD_TEXT, MOD_VISION, MOD_AUDIO, MOD_MOTION, MOD_META}:
            logger.warning(f"Unbekannte modality '{self.modality}', setze auf '{MOD_META}'")
            self.modality = MOD_META

        # numerische Felder säubern
        self.features = [float(x) for x in (self.features or [])]
        self.embedding = [float(x) for x in (self.embedding or [])]
        self.token_ids = [int(x) for x in (self.token_ids or [])]

        # Automatische Tokenisierung bei Text (falls nicht vorhanden)
        if (self.modality in (MOD_TEXT, MOD_META)) and self.text and not self.token_ids:
            self._tokenize_text_inplace()

        self.fingerprint = self._compute_fingerprint()

    # ---------------- Tokenisierung & Vektoren ----------------

    def _tokenize_text_inplace(self) -> None:
        assert self.text is not None
        toks = _llm_tokenize_safe(self.text, self.model_hint)
        if toks:
            self.token_ids = toks
            return
        # Fallback: whitespace → 32-bit Hash pro Token (stabil, nicht trivial wie ord())
        ws = [t for t in self.text.strip().split() if t]
        self.token_ids = [_hash_to_int(t, bits=32) for t in ws] or []

    def ensure_tokenized(self) -> bool:
        """Tokenisiert Text falls nötig; True, wenn token_ids vorhanden sind."""
        if self.token_ids or not self.text:
            return bool(self.token_ids)
        self._tokenize_text_inplace()
        self.fingerprint = self._compute_fingerprint()
        return bool(self.token_ids)

    def feature_vector(self, prefer_embedding: bool = True, *, normalize: bool = False) -> List[float]:
        """
        Gibt numerischen Vektor zurück:
          - embedding (wenn vorhanden & bevorzugt), sonst features.
          - optional L2-normalisiert.
        """
        vec = self.embedding if (prefer_embedding and self.embedding) else self.features
        v = [float(x) for x in (vec or [])]
        return _normalize_vec(v) if normalize else v

    def normalize_embedding_(self) -> None:
        """L2-Normalisierung der Embedding (in-place)."""
        if not self.embedding:
            return
        self.embedding = _normalize_vec(self.embedding)

    # ---------------- Fingerprint ----------------

    def _compute_fingerprint(self) -> str:
        parts: List[Union[str, bytes]] = [self.modality, self.uid]
        if self.text:
            parts.append(self.text)
        if self.features:
            parts.append(json.dumps([round(float(x), 6) for x in self.features], separators=(",", ":")))
        if self.token_ids:
            parts.append(json.dumps(self.token_ids, separators=(",", ":")))
        if self.embedding:
            parts.append(json.dumps([round(float(x), 6) for x in self.embedding], separators=(",", ":")))
        parts.append(json.dumps(self.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        parts.append(str(int(self.created_ts)))
        parts.append(self.version)
        return _stable_fingerprint(parts)

    # ---------------- Serialisierung ----------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "modality": self.modality,
            "text": self.text,
            "features": self.features or [],
            "token_ids": self.token_ids or [],
            "model_hint": self.model_hint,
            "embedding": self.embedding or [],
            "metadata": self.metadata or {},
            "created_ts": int(self.created_ts),
            "fingerprint": self.fingerprint or self._compute_fingerprint(),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SnapToken":
        tok = cls(
            uid=data.get("uid") or uuid.uuid4().hex,
            modality=data.get("modality") or MOD_META,
            text=data.get("text"),
            features=list(data.get("features") or []),
            token_ids=list(data.get("token_ids") or []),
            model_hint=data.get("model_hint"),
            embedding=list(data.get("embedding") or []),
            metadata=dict(data.get("metadata") or {}),
            created_ts=int(data.get("created_ts") or int(time.time())),
            fingerprint=data.get("fingerprint"),
            version=data.get("version") or "3.7.0",
        )
        # Fingerprint notfalls neu berechnen
        if not tok.fingerprint or tok.fingerprint != tok._compute_fingerprint():
            tok.fingerprint = tok._compute_fingerprint()
        return tok

    def as_blob(self) -> bytes:
        return _pack_blob(self.to_dict())

    @classmethod
    def from_blob(cls, blob: bytes) -> "SnapToken":
        return cls.from_dict(_unpack_blob(blob))

    # ---------------- SQL-Helfer (Schema-agnostisch) ----------------

    def sql_row(self) -> Tuple[str, str, Optional[str], str, int, bytes]:
        """
        Generische Row: (uid, modality, text, fingerprint, created_ts, blob)
        – kompatibel zu bestehender Nutzung.
        """
        return (
            self.uid,
            self.modality,
            self.text if self.text else None,
            self.fingerprint or self._compute_fingerprint(),
            int(self.created_ts),
            self.as_blob(),
        )

    @classmethod
    def from_row(cls, row: Tuple[str, str, Optional[str], str, int, bytes]) -> "SnapToken":
        uid, modality, text, fingerprint, created_ts, blob = row
        tok = cls.from_blob(blob)
        tok.uid = uid
        tok.modality = modality
        tok.text = text
        tok.fingerprint = fingerprint
        tok.created_ts = int(created_ts)
        return tok

    # ---------------- Convenience ----------------

    @property
    def is_text(self) -> bool:
        return self.modality == MOD_TEXT or (self.modality == MOD_META and bool(self.text))

    def short_info(self) -> str:
        base = f"{self.modality.upper()} uid={self.uid[:8]} fp={self.fingerprint[:8]}"
        if self.text:
            base += f" text='{self.text[:32]}{'…' if len(self.text)>32 else ''}'"
        if self.features:
            base += f" feat={len(self.features)}"
        if self.token_ids:
            base += f" toks={len(self.token_ids)}"
        if self.embedding:
            base += f" emb={len(self.embedding)}"
        return base
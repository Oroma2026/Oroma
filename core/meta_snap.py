#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/meta_snap.py
# Projekt:   ORÓMA (Offline-First · Headless · Abstraction Layer)
# Modul:     MetaSnap – leichtgewichtige Abstraktion über Snaps/SnapChains (stable fingerprint, tags/sources, score) + kompakte Serialisierung (JSON+zlib)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# MetaSnap ist ein bewusst schlankes Datenobjekt („Abstraktions-Snap“), das mehrere
# Snaps/SnapChains/Events zu einer höheren Einheit bündelt – ohne harte Abhängigkeiten
# an Datenbank, Vision, Audio oder ML-Runtimes.
#
# Typische Verwendung in ORÓMA:
#   - DreamWorker erzeugt MetaSnaps aus komprimierten Sequenzen
#   - Explainability/UI zeigt MetaSnaps als „Themen“, „Verdichtungen“, „Konzepte“
#   - Transfer/Export kann MetaSnaps als portable Knowledge-Units nutzen
#
# Grundprinzip:
#   - minimal stabile Felder (label, sources, score, tags, notes, extra, timestamps)
#   - deterministischer Fingerprint für Dedupe/Indexing
#   - robuste Serialisierung für Speicherung in DB-Blob oder File-Bundle
#
# DATENMODELL (MetaSnap Dataclass)
# ───────────────────────────────
# Felder:
#   label: str
#     - Kurzname/Topic (z. B. "scene:livingroom", "compressed_67975")
#
#   sources: List[str]
#     - Referenzen auf Ursprungseinheiten, typischerweise Strings wie:
#         "chain:42", "snap:991", "vision/token:1234", "rule:ttt#17"
#
#   score: float
#     - Wichtigkeit/Resonanz in [0..1], intern konsequent geklemmt (_bounded)
#     - wird u. a. in DreamWorker per decay() langsam reduziert (Cooling)
#
#   tags: List[str]
#     - freie Schlagworte (z. B. ["vision","audio","calc_vision"])
#
#   notes: Optional[str]
#     - kurze Textnotiz (kompakt, kein Essay)
#
#   extra: Dict[str,Any]
#     - beliebige Zusatzdaten (debug, metrics, parameter)
#     - wird beim Fingerprint sortiert serialisiert (sort_keys=True)
#
#   created_at / updated_at: int
#     - Unix seconds
#
#   uid: Optional[str]
#     - optionaler externer Identifier (z. B. aus Import/Export)
#
#   fingerprint: Optional[str]
#     - stabiler Hash; wird in __post_init__ ggf. neu berechnet
#
# FINGERPRINT (STABILITÄT / DEDUPE)
# ─────────────────────────────────
# Fingerprint ist bewusst deterministisch und wird aus diesen Teilen gebildet:
#   - label
#   - sources (in aktueller Reihenfolge, verbunden mit "|")
#   - tags (in aktueller Reihenfolge)
#   - score (round 6)
#   - created_at, updated_at
#   - extra als JSON (sort_keys=True)
#   - notes, uid
#
# Hash-Funktion:
#   - SHA1 über Teile mit Trennzeichen "|"
#   - Ergebnis: hex digest
#
# Konsequenz:
# - Fingerprint ändert sich, wenn sich inhaltlich relevante Felder ändern.
# - from_dict() prüft Fingerprint-Konsistenz und berechnet bei Bedarf neu.
#
# SERIALISIERUNG (KOMPAKT, PORTABEL)
# ──────────────────────────────────
# JSON:
#   - to_json() / from_json() arbeiten mit UTF-8 JSON
#
# BLOB:
#   - as_blob() serialisiert to_dict() als JSON (compact separators) und komprimiert via zlib(level=6)
#   - from_blob() dekomprimiert + parsed JSON
#
# Motivation:
# - DB-Storage klein halten (zlib)
# - Export/Import „portable“ und robust (keine Pickles)
#
# MUTATION-APIs (NON-DESTRUCTIVE)
# ───────────────────────────────
# add_source(s), add_sources(...)
#   - fügt Quellen hinzu, ohne Duplikate (idempotent)
#
# add_tag(tag)
#   - fügt Tag hinzu, ohne Duplikate
#
# rescore(value)
#   - setzt score geklemmt [0..1], aktualisiert updated_at + fingerprint
#
# decay(factor=0.98)
#   - multiplicativer Cooling-Step (DreamWorker geeignet), danach touch()
#
# merge_from(other)
#   - nicht-destruktive Verschmelzung:
#       • Union sources
#       • Union tags
#       • score = max(score)
#       • notes werden bei Bedarf zusammengefügt
#       • extra wird merged (dict.update)
#
# touch()
#   - updated_at=now und fingerprint neu berechnen
#
# OUTPUT / KOMFORT
# ────────────────
# to_dict()/from_dict()
# __repr__() (kurze Debug-Darstellung)
# short_info() liefert kompakte menschliche Kurzzeile
#
# LOGGING
# ───────
# Logger: "meta_snap"
# - Handler wird nur angelegt, wenn noch keine Handler existieren (idempotent)
# - Level über ENV:
#     OROMA_LOG_LEVEL (Default: WARNING)
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class MetaSnap:
#   - __post_init__()
#   - add_source(), add_sources()
#   - add_tag()
#   - rescore(), decay()
#   - merge_from()
#   - touch()
#   - to_dict(), from_dict()
#   - to_json(), from_json()
#   - as_blob(), from_blob()
#   - short_info()
#
# SELFTEST (CLI)
# ─────────────
# python3 /opt/ai/oroma/core/meta_snap.py
#   - erstellt MetaSnap, Roundtrip JSON + Blob, Mutationen + decay(), Ausgabe short_info()
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - zlib(JSON) bleibt das BLOB-Format (portabel, klein, robust).
# - Fingerprint-Strategie muss deterministisch bleiben (Dedupe/Indexing hängt daran).
# - score bleibt konsequent geklemmt [0..1] (keine Ausreißer in Ranking/UI).
# - merge_from ist non-destructive (keine Quellen/Tags verlieren).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import time, json, zlib, hashlib, logging, os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Iterable

logger = logging.getLogger("oroma.meta_snap")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[meta_snap] %(levelname)s: %(message)s"))
    logger.addHandler(h)
logger.setLevel(getattr(logging, os.environ.get("OROMA_LOG_LEVEL", "WARNING").upper(), logging.WARNING))

def _now_i() -> int: return int(time.time())

def _bounded(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return hi if x > hi else lo if x < lo else x

def _stable_fingerprint(parts: Iterable[str]) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8")); h.update(b"|")
    return h.hexdigest()

def _pack_json(d: Dict[str, Any]) -> bytes:
    raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, level=6)

def _unpack_json(blob: bytes) -> Dict[str, Any]:
    data = zlib.decompress(blob)
    return json.loads(data.decode("utf-8"))

@dataclass
class MetaSnap:
    """
    Meta-Snap: Abstraktion über mehrere Snaps/SnapChains.
    Felder sind minimal & stabil, Zusatzinfos in 'extra'.
    """
    label: str
    sources: List[str] = field(default_factory=list)     # IDs/Keys (z. B. "chain:42")
    score: float = 0.0                                   # [0..1], Wichtigkeit/Resonanz
    tags: List[str] = field(default_factory=list)        # freie Schlagworte
    notes: Optional[str] = None                          # kurze Notiz
    extra: Dict[str, Any] = field(default_factory=dict)  # beliebige Zusatzdaten
    created_at: int = field(default_factory=_now_i)
    updated_at: int = field(default_factory=_now_i)
    uid: Optional[str] = None
    fingerprint: Optional[str] = None

    # ---------------- Lifecycle ----------------

    def __post_init__(self) -> None:
        self.label = (self.label or "meta").strip()[:120]
        self.sources = list(dict.fromkeys(x for x in (self.sources or []) if x))  # unique & non-empty
        self.tags = list(dict.fromkeys(t.strip().lower() for t in (self.tags or []) if t))
        self.score = _bounded(float(self.score))
        if not self.uid:
            # einfache, stabile UID aus Label+Zeit
            base = f"{self.label}|{self.created_at}"
            self.uid = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
        self.fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        parts = [
            self.label,
            "|".join(self.sources),
            "|".join(self.tags),
            str(round(self.score, 6)),
            str(int(self.created_at)),
            str(int(self.updated_at)),
            json.dumps(self.extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            self.notes or "",
            self.uid or "",
        ]
        return _stable_fingerprint(parts)

    # ---------------- Mutationen ----------------

    def add_source(self, key: str) -> None:
        if key and key not in self.sources:
            self.sources.append(key)
            self.touch()

    def add_sources(self, keys: Iterable[str]) -> None:
        for k in keys or []:
            self.add_source(k)

    def add_tag(self, tag: str) -> None:
        t = (tag or "").strip().lower()
        if t and t not in self.tags:
            self.tags.append(t)
            self.touch()

    def rescore(self, value: float) -> None:
        self.score = _bounded(float(value))
        self.touch()

    def decay(self, factor: float = 0.98) -> None:
        """Leichte zeitliche Abkühlung, nützlich für DreamWorker."""
        self.score = _bounded(self.score * float(factor))
        self.touch()

    def merge_from(self, other: "MetaSnap") -> None:
        """Nicht-destruktive Verschmelzung (Union von Quellen/Tags, Max-Score)."""
        if not isinstance(other, MetaSnap):
            return
        self.add_sources(other.sources)
        for t in other.tags:
            self.add_tag(t)
        self.score = max(self.score, other.score)
        if other.notes and other.notes != self.notes:
            self.notes = (self.notes or "") + (" | " if self.notes else "") + other.notes
        self.extra.update(other.extra or {})
        self.touch()

    def touch(self) -> None:
        self.updated_at = _now_i()
        self.fingerprint = self._compute_fingerprint()

    # ---------------- Serialisierung ----------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "label": self.label,
            "sources": list(self.sources),
            "score": float(self.score),
            "tags": list(self.tags),
            "notes": self.notes,
            "extra": self.extra or {},
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
            "fingerprint": self.fingerprint,
            "version": "3.7.0",
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MetaSnap":
        ms = cls(
            label=d.get("label", "meta"),
            sources=list(d.get("sources") or []),
            score=float(d.get("score", 0.0)),
            tags=list(d.get("tags") or []),
            notes=d.get("notes"),
            extra=dict(d.get("extra") or {}),
            created_at=int(d.get("created_at") or _now_i()),
            updated_at=int(d.get("updated_at") or _now_i()),
            uid=d.get("uid"),
            fingerprint=d.get("fingerprint"),
        )
        # Fingerprint ggf. neu berechnen, wenn inkonsistent
        if not ms.fingerprint or ms.fingerprint != ms._compute_fingerprint():
            ms.fingerprint = ms._compute_fingerprint()
        return ms

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "MetaSnap":
        return cls.from_dict(json.loads(s))

    def as_blob(self) -> bytes:
        return _pack_json(self.to_dict())

    @classmethod
    def from_blob(cls, blob: bytes) -> "MetaSnap":
        return cls.from_dict(_unpack_json(blob))

    # ---------------- Debug ----------------

    def __repr__(self) -> str:
        return f"<MetaSnap {self.label!r} score={self.score:.2f} sources={len(self.sources)} uid={self.uid[:8]}>"

    def short_info(self) -> str:
        return f"MetaSnap(label={self.label}, score={self.score:.2f}, sources={len(self.sources)}, tags={len(self.tags)})"


# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------
def _selftest() -> None:
    print("[meta_snap] Selftest startet …")
    ms = MetaSnap("test-meta", ["chain:1", "chain:2"], score=0.88, tags=["vision", "audio"], notes="probe")
    print(" Objekt:", ms)
    j = ms.to_json()
    ms2 = MetaSnap.from_json(j)
    print(" JSON len:", len(j), "re-fp:", ms2.fingerprint[:10])
    ms2.add_source("chain:3")
    ms2.add_tag("fusion")
    ms2.decay(0.97)
    b = ms2.as_blob()
    ms3 = MetaSnap.from_blob(b)
    print(" roundtrip:", ms3.short_info())
    print("[meta_snap] Selftest abgeschlossen ✅")

if __name__ == "__main__":
    _selftest()
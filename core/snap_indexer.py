#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/snap_indexer.py
# Projekt:   ORÓMA (SQLite-first · Dedup/Index · Headless)
# Modul:     SnapIndexer – MetaSnap → snap_index Upsert (Fingerprint) + Payload-Strategie (minimal/full) + Schema-kompatible Spalten-Erkennung
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist in v3.7.3 **kein** generischer „Index für alle SnapChains“,
# sondern eine fokussierte Hilfsschicht, um **MetaSnaps** zuverlässig in eine
# flache Index-Tabelle (`snap_index`) zu schreiben.
#
# Warum?
# - MetaSnaps entstehen typischerweise in Dream/Compression/Transfer-Pipelines
#   (z. B. "compressed_<wid>" oder ähnliche Ableitungen).
# - Für UI/Analyse/Explainability braucht man schnelle Lookups über einen stabilen
#   Schlüssel (Fingerprint), ohne die großen SnapChain-Blobs zu scannen.
#
# Dieses Modul liefert dafür:
#   - deterministischen MetaSnap-Fingerprint (label + sources)
#   - payload-Erzeugung (minimal oder full)
#   - robusten DB-Upsert in snap_index via ON CONFLICT(fingerprint)
#   - Spalten-Erkennung, damit verschiedene DB-Schemata kompatibel bleiben
#
# WICHTIG: SCOPE (WAS DIESE DATEI TATSÄCHLICH MACHT)
# ──────────────────────────────────────────────────
# ✅ Ja:
#   - Fingerprint für MetaSnaps erzeugen
#   - Einen MetaSnap als „Index-Record“ in `snap_index` upserten
#   - Optional „Brücken-Spalten“ setzen (ref_table/ref_id/ref_key), falls vorhanden
#
# ❌ Nein (bewusst nicht in diesem Modul):
#   - kein Scannen/Indexieren kompletter SnapChains
#   - kein Delete/Prune/Forgetting
#   - keine Abhängigkeit zu Vision/Audio/ML-Backends
#
# INPUT-VERTRAG
# ─────────────
# Die Public-Funktion erwartet einen **bereits geöffneten DB-Connection-Handle**:
#   index_meta_snap(conn, meta_id, label, score, sources, ts=None, source="dream:meta", privacy_tier="local")
#
# Hintergrund:
# - In ORÓMA ist Connection/PRAGMA/BusyTimeout zentral über sql_manager geregelt,
#   aber dieses Modul ist absichtlich „low-level“ und nimmt die Connection direkt an,
#   damit DreamWorker/Tools in bereits laufenden Transaktionen arbeiten können.
#
# FINGERPRINT (DEDUPE-KEY)
# ───────────────────────
# fingerprint_meta(label, sources) erzeugt einen stabilen Key:
#   fp = "meta:" + sha256( json({"kind":"meta_snap","label":..., "sources":[...]}) )
#
# Damit ist Dedupe über Systeme/Backups reproduzierbar, solange label+sources gleich sind.
#
# SNAP_INDEX SCHEMA (ERWARTUNGEN)
# ──────────────────────────────
# Dieses Modul schreibt in Tabelle `snap_index` und nutzt folgende Kernspalten:
#   - ts           REAL/INTEGER   (Unix seconds)
#   - source       TEXT           (z. B. "dream:meta")
#   - privacy_tier TEXT           (z. B. "local")
#   - feature_dim  INTEGER/NULL   (hier: None, da MetaSnap kein Feature-Vektor ist)
#   - l2_norm      REAL/NULL      (hier: None)
#   - fingerprint  TEXT           (UNIQUE; Konfliktziel)
#   - payload      BLOB/TEXT      (JSON-bytes; siehe Payload-Modus)
#
# Optional (nur wenn Spalten existieren – dynamisch erkannt):
#   - ref_table TEXT  (hier: "meta_snaps")
#   - ref_id    INT   (hier: meta_id)
#   - ref_key   TEXT  (hier: None)
#
# SPALTEN-ERKENNUNG (WICHTIGER PRODUKTIONSFIX)
# ───────────────────────────────────────────
# _snap_index_columns(conn) liest PRAGMA table_info(snap_index) robust aus.
# Grund:
# - ORÓMA nutzt häufig eine dict-row_factory (in sql_manager).
# - PRAGMA table_info(...) liefert dann Dicts statt Tuples.
# - Ältere Implementierungen nutzten row[1] und scheiterten mit KeyError(1).
# Diese Datei behandelt:
#   - dict rows (row.get("name"))
#   - sqlite3.Row (row["name"])
#   - tuple/list fallback (row[1])
#
# PAYLOAD-STRATEGIE (MINIMAL VS FULL)
# ───────────────────────────────────
# Um DB-Wachstum zu kontrollieren, kann der Payload reduziert werden.
#
# ENV:
#   OROMA_SNAP_INDEX_PAYLOAD_MODE = "minimal" | "full"   (Default: "minimal")
#
# minimal:
#   - enthält KEINE sources-Liste (nur sources_n)
#   - geeignet für UI/Explainability-Snippets ohne Datenlast
#
# full:
#   - enthält die komplette sources-Liste + meta_id + kind
#   - nur für Debug/Analyse (größer)
#
# UPSERT-VERHALTEN (ON CONFLICT)
# ─────────────────────────────
# Der Insert erfolgt als:
#   INSERT INTO snap_index(...)
#   ON CONFLICT(fingerprint) DO UPDATE SET <alle Felder außer fingerprint>
#
# Bedeutung:
# - Es gibt niemals Duplikate pro fingerprint.
# - Der „neueste“ Stand (payload/ts/source/privacy_tier/refs) wird im Index gehalten.
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# fingerprint_meta(label: str, sources: list) -> str
#   - deterministischer MetaSnap-Fingerprint
#
# index_meta_snap(conn, meta_id: int, label: str, score: float, sources: list, ts: float|None, source: str, privacy_tier: str) -> str
#   - schreibt/upsertet den snap_index Eintrag
#   - gibt den fingerprint zurück
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Fingerprint muss deterministisch bleiben (label+sources sind „Source of Truth“).
# - Payload default bleibt "minimal" (DB-Größe/Performance).
# - Spalten-Erkennung muss bestehen bleiben (Kompatibilität mit dict_row_factory + Schema-Varianten).
# - Kein Delete/Prune hier: Indexer ist nur Writer/Upserter.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_meta(label: str, sources: List[Any]) -> str:
    """
    Stabiler Fingerprint für MetaSnaps.
    Ziel: Idempotenz + (später) Dedupe gleicher Meta-Bedeutung.

    Hinweis:
      - Bei deinem aktuellen "compressed_<wid>" Label ist jeder MetaSnap
        faktisch einzigartig (weil wid einzigartig ist). Später, wenn Labels
        semantischer werden oder sources mehrere IDs enthalten, bringt Dedupe
        mehr.

    Wichtig:
      - Fingerprint bleibt bewusst QUELLNAH (label + sources), selbst wenn
        payload minimal ist. Das ist okay: Dedupe/Idempotenz beruht nicht auf payload.
    """
    canon = json.dumps(
        {"kind": "meta_snap", "label": label, "sources": sources},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "meta:" + _sha256_hex(canon)


def _snap_index_columns(conn) -> set:
    """
    Liefert die Spaltennamen von `snap_index` robust – unabhängig von row_factory.

    Hintergrund:
      ORÓMA nutzt in sql_manager standardmäßig eine dict-row_factory.
      PRAGMA table_info(...) liefert dann Dicts (z.B. {'cid':..., 'name':...}).
      Früher haben wir hier row[1] verwendet → KeyError(1) bei Dicts.
    """
    cols = set()
    for row in conn.execute("PRAGMA table_info(snap_index)"):
        name = None
        try:
            # dict_factory (sql_manager) → row ist dict
            if isinstance(row, dict):
                name = row.get("name")
            # sqlite3.Row oder ähnliche Mapping-Typen
            elif hasattr(row, "keys") and "name" in row.keys():
                name = row["name"]
        except Exception:
            name = None
        if name is None:
            try:
                # tuple/list-Fallback: (cid, name, type, notnull, dflt_value, pk)
                name = row[1]
            except Exception:
                name = None
        if name:
            cols.add(str(name))
    return cols


def _payload_mode() -> str:
    """
    Liest die Payload-Strategie aus ENV.
    Default ist "minimal" (klein halten).
    """
    mode = (os.environ.get("OROMA_SNAP_INDEX_PAYLOAD_MODE") or "minimal").strip().lower()
    if mode not in ("minimal", "full"):
        return "minimal"
    return mode


def _build_meta_payload_minimal(
    *,
    label: str,
    score: float,
    sources: List[Any],
    ts: float,
    privacy_tier: str,
) -> bytes:
    """
    Minimaler payload für snap_index:
      - KEINE sources-Liste (nur sources_n)
      - ausreichend für schnelle Explainability-Snippets ohne Datenlast
    """
    obj: Dict[str, Any] = {
        "label": str(label) if label is not None else "",
        "score": float(score) if score is not None else 0.0,
        "sources_n": int(len(sources) if sources else 0),
        "privacy_tier": str(privacy_tier) if privacy_tier is not None else "",
        "ts": float(ts),
    }
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _build_meta_payload_full(
    *,
    meta_id: int,
    label: str,
    score: float,
    sources: List[Any],
    ts: float,
    privacy_tier: str,
) -> bytes:
    """
    Voller payload (nur Debug/Analyse):
      - enthält sources-Liste
      - kostet mehr Platz, ist aber manchmal praktisch
    """
    obj: Dict[str, Any] = {
        "kind": "meta_snap",
        "meta_id": int(meta_id),
        "label": str(label),
        "score": float(score),
        "sources": sources,
        "privacy_tier": str(privacy_tier) if privacy_tier is not None else "",
        "ts": float(ts),
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def index_meta_snap(
    conn,
    meta_id: int,
    label: str,
    score: float,
    sources: List[Any],
    ts: Optional[float] = None,
    source: str = "dream:meta",
    privacy_tier: str = "local",
) -> str:
    """
    Schreibt/aktualisiert einen MetaSnap-Eintrag in snap_index.

    Returns:
        fingerprint (str)
    """
    if ts is None:
        ts = time.time()

    fp = fingerprint_meta(label, sources)

    mode = _payload_mode()
    if mode == "full":
        payload = _build_meta_payload_full(
            meta_id=meta_id,
            label=label,
            score=score,
            sources=sources,
            ts=float(ts),
            privacy_tier=privacy_tier,
        )
    else:
        payload = _build_meta_payload_minimal(
            label=label,
            score=score,
            sources=sources,
            ts=float(ts),
            privacy_tier=privacy_tier,
        )

    cols = _snap_index_columns(conn)

    fields = ["ts", "source", "privacy_tier", "feature_dim", "l2_norm", "fingerprint", "payload"]
    values: List[Any] = [float(ts), str(source), str(privacy_tier), None, None, str(fp), payload]

    # Optional: Normalform-Brücke (wenn Spalten existieren)
    if "ref_table" in cols:
        fields.append("ref_table")
        values.append("meta_snaps")
    if "ref_id" in cols:
        fields.append("ref_id")
        values.append(int(meta_id))
    if "ref_key" in cols:
        fields.append("ref_key")
        values.append(None)

    placeholders = ",".join(["?"] * len(fields))
    field_csv = ",".join(fields)

    # ON CONFLICT: Index soll "latest payload" tragen, ohne Duplikate.
    # (Wenn fingerprint bereits existiert, aktualisieren wir payload/ts/source.)
    update_set = []
    for f in fields:
        if f == "fingerprint":
            continue
        update_set.append(f"{f}=excluded.{f}")
    update_sql = ", ".join(update_set)

    sql = f"""
    INSERT INTO snap_index({field_csv})
    VALUES({placeholders})
    ON CONFLICT(fingerprint) DO UPDATE SET
      {update_sql}
    """

    conn.execute(sql, values)
    return fp

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/audio_label_linker.py
# Projekt:   ORÓMA – Headless Lern-KI (Edge · Offline-First)
# Modul:     AudioLabelLinker – Teacher-Links: ASR-Text ↔ Audio-SnapTokens (ohne Vision)
# Version:   v3.7.3
# Stand:     2026-01-21
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul persistiert *unimodale* Teacher-Links zwischen ASR-Text und den
# zuletzt beobachteten Audio-SnapTokens (snapchains.origin = 'audio/token').
#
# Motivation (analoge Intuition: „Augen zu und trotzdem verstehen“)
#   - ORÓMA erzeugt Audio-Snaps kontinuierlich, unabhängig von Vision.
#   - ASR liefert ein symbolisches Label (Text), das ohne Vision bereits
#     lernrelevant ist.
#   - Der existierende AV-Linker (core/av_label_linker.py) ist bewusst
#     crossmodal (Audio+Vision) und erzeugt nur dann Labels, wenn beide
#     Modalitäten zeitlich eng zusammenpassen.
#
# Dieses Modul ergänzt daher einen stabilen „Audio-only“ Pfad:
#   • origin = 'link/a_label'
#   • blob   = kleines JSON (keine WAV-Daten), das Text + referenzierte
#              Audio-Token-IDs enthält.
#
# OUTPUT / DB-FORMAT (snapchains)
# ───────────────────────────────
# origin:    link/a_label
# namespace: transfer
# blob (JSON, UTF-8, klein):
#   {
#     "kind": "a_label",
#     "audio_ids": [..],            # Token-IDs im Lookback-Fenster (max N)
#     "audio_ts_min": 123,
#     "audio_ts_max": 456,
#     "audio_last_id": 999,
#     "text_raw": "...",
#     "text_norm": "...",
#     "keywords": ["..."],
#     "window_sec": 6,
#     "max_tokens": 24
#   }
#
# PERFORMANCE / PRODUKTIONSREGELN
# ───────────────────────────────
# - Kein neues Schema, keine neue Tabelle.
# - DB-Connections werden immer geschlossen (with sql_manager.get_conn()).
# - Keine großen Blobs: JSON wird minimal gehalten, Audio-IDs sind limitiert.
# - Dedup über source_id: (letzte AudioToken-ID + Textnorm-Hash) – idempotent.
# - Designed für Orchestrator: kurze Transaktionen, busy_timeout/WAL via sql_manager.
#
# ENV
# ───
# OROMA_A_LABEL_LINK_ENABLE=0|1         (Default 1)
# OROMA_A_LABEL_WINDOW_SEC=<int>        (Default 6)   Lookback-Fenster in Sekunden
# OROMA_A_LABEL_MAX_TOKENS=<int>        (Default 24)  max. AudioToken-IDs im Blob
# OROMA_A_LABEL_MIN_TEXT_LEN=<int>      (Default 2)   ignoriert Mini-Noise
# OROMA_A_LABEL_STOPWORDS_DE=0|1        (Default 1)   einfache Stopwords (DE)
#
# USAGE
# ─────
#   from core import audio_label_linker
#   audio_label_linker.link_text_now("hallo welt")
#
# =============================================================================

from __future__ import annotations

import hashlib
import json
import logging
from core import log_guard
import os
import re
import time
from typing import Any, Dict, List, Optional

from core import sql_manager

_log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


_ENABLE = _env_bool("OROMA_A_LABEL_LINK_ENABLE", True)
_WINDOW_SEC = _env_int("OROMA_A_LABEL_WINDOW_SEC", 6)
_MAX_TOKENS = _env_int("OROMA_A_LABEL_MAX_TOKENS", 24)
_MIN_TEXT_LEN = _env_int("OROMA_A_LABEL_MIN_TEXT_LEN", 2)
_STOPWORDS_DE = _env_bool("OROMA_A_LABEL_STOPWORDS_DE", True)

_STOP_DE = {
    "der", "die", "das", "ein", "eine", "einen", "einem", "einer",
    "und", "oder", "aber", "ist", "sind", "war", "waren",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "sie",
    "da", "hier", "dort", "jetzt", "bitte", "danke",
    "das", "dass", "dem", "den", "im", "in", "am", "an", "auf", "zu", "mit",
}


def _row_get(row: Any, key: str, idx: int) -> Any:
    """Tolerant: unterstützt dict-row_factory, sqlite3.Row, tuple/list."""
    try:
        if isinstance(row, dict):
            return row.get(key)
        if isinstance(row, (list, tuple)):
            return row[idx]
        try:
            return row[key]  # sqlite3.Row als Mapping
        except Exception:
            return row[idx]
    except Exception:
        return None


def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("ß", "ss")
    return s


def _keywords(s: str) -> List[str]:
    s = _norm_text(s)
    parts = re.split(r"[^a-z0-9äöüß]+", s, flags=re.IGNORECASE)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if _STOPWORDS_DE and p in _STOP_DE:
            continue
        if len(p) <= 1:
            continue
        out.append(p)

    # dedupe, Reihenfolge stabil
    seen = set()
    uniq: List[str] = []
    for w in out:
        if w in seen:
            continue
        seen.add(w)
        uniq.append(w)
    return uniq[:16]


def _sha10(s: str) -> str:
    h = hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()
    return h[:10]


def _fetch_recent_audio_ids(since_ts: int, limit: int) -> List[int]:
    """Liefert Audio-Token-IDs (neueste zuerst) im Zeitfenster."""
    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, ts
                FROM snapchains
                WHERE origin = 'audio/token'
                  AND ts >= ?
                  AND (status IS NULL OR status != 'deleted')
                ORDER BY ts DESC
                LIMIT ?
                """,
                (int(since_ts), int(limit)),
            )
            rows = cur.fetchall() or []
            out: List[int] = []
            for r in rows:
                rid = _row_get(r, "id", 0)
                if rid is None:
                    continue
                try:
                    out.append(int(rid))
                except Exception:
                    continue
            return out
    except Exception as e:
        log_guard.log_suppressed(_log, key="audio_label_linker.fetch_recent_audio_ids", msg="fetch_recent_audio_ids failed", exc=e, level=logging.WARNING, interval_s=300)
        return []


def _fetch_audio_ts_range(audio_ids: List[int]) -> Optional[Dict[str, int]]:
    """Liefert ts_min/ts_max für gegebene IDs."""
    if not audio_ids:
        return None

    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            qmarks = ",".join(["?"] * len(audio_ids))
            cur.execute(
                f"""
                SELECT MIN(ts) AS ts_min, MAX(ts) AS ts_max
                FROM snapchains
                WHERE id IN ({qmarks})
                """,
                tuple(int(x) for x in audio_ids),
            )
            r = cur.fetchone()
            if not r:
                return None
            ts_min = _row_get(r, "ts_min", 0)
            ts_max = _row_get(r, "ts_max", 1)
            if ts_min is None or ts_max is None:
                return None
            return {"ts_min": int(ts_min), "ts_max": int(ts_max)}
    except Exception as e:
        log_guard.log_suppressed(_log, key="audio_label_linker.fetch_audio_ts_range", msg="fetch_audio_ts_range failed", exc=e, level=logging.WARNING, interval_s=300)
        return None


def _already_exists(source_id: str) -> Optional[int]:
    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id
                FROM snapchains
                WHERE origin='link/a_label' AND source_id=?
                LIMIT 1
                """,
                (str(source_id),),
            )
            r = cur.fetchone()
            if not r:
                return None
            rid = _row_get(r, "id", 0)
            return int(rid) if rid is not None else None
    except Exception:
        return None


def link_text_now(text: str, *, window_sec: int = _WINDOW_SEC) -> Optional[int]:
    """Erzeugt einen Teacher-Link (ASR-Text ↔ AudioTokens) für „jetzt“.

    Rückgabe:
      - neue SnapChain-ID oder bestehende (dedupe via source_id)
      - None wenn disabled / Text zu kurz / keine AudioTokens im Fenster
    """
    if not _ENABLE:
        return None

    text = (text or "").strip()
    if len(text) < _MIN_TEXT_LEN:
        return None

    now = int(time.time())
    since = now - int(max(1, window_sec))

    audio_ids = _fetch_recent_audio_ids(since, limit=max(4, int(_MAX_TOKENS)))
    if not audio_ids:
        return None

    audio_ids = audio_ids[: int(_MAX_TOKENS)]
    ts_range = _fetch_audio_ts_range(audio_ids) or {"ts_min": since, "ts_max": now}

    tnorm = _norm_text(text)
    kws = _keywords(text)

    audio_last_id = int(audio_ids[0])
    audio_first_id = int(audio_ids[-1])
    source_id = f"a_label:{audio_last_id}:{audio_first_id}:{_sha10(tnorm)}"

    existing = _already_exists(source_id)
    if existing:
        return existing

    # Qualität: sehr konservativ; primär „Existenz/Verknüpfung“.
    n = len(audio_ids)
    q = 0.15 + 0.85 * min(1.0, n / 10.0)

    blob_obj: Dict[str, Any] = {
        "kind": "a_label",
        "audio_ids": [int(x) for x in audio_ids],
        "audio_ts_min": int(ts_range["ts_min"]),
        "audio_ts_max": int(ts_range["ts_max"]),
        "audio_last_id": int(audio_last_id),
        "text_raw": text,
        "text_norm": tnorm,
        "keywords": kws,
        "window_sec": int(window_sec),
        "max_tokens": int(_MAX_TOKENS),
    }
    blob = json.dumps(blob_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    payload: Dict[str, Any] = {
        "ts": int(ts_range["ts_max"]),
        "quality": float(q),
        "blob": blob,
        "exported": 0,
        "status": "active",
        "origin": "link/a_label",
        "namespace": "transfer",
        "source_id": source_id,
        "notes": "Teacher link: audio/token (+ ASR text)",
        "version": "v3.7.3",
        "weight": 0.08,
    }

    try:
        return sql_manager.insert_snapchain(payload)
    except TypeError:
        # Kompatibilitäts-Fallback: falls insert_snapchain weniger Keys akzeptiert
        try:
            payload2 = dict(payload)
            payload2.pop("namespace", None)
            payload2.pop("source_id", None)
            payload2.pop("weight", None)
            return sql_manager.insert_snapchain(payload2)
        except Exception as e:
            log_guard.log_suppressed(_log, key="audio_label_linker.insert.fallback", msg="insert fallback failed", exc=e, level=logging.WARNING, interval_s=120)
            return None
    except Exception as e:
        log_guard.log_suppressed(_log, key="audio_label_linker.insert", msg="insert failed", exc=e, level=logging.WARNING, interval_s=120)
        return None

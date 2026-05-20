#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/av_label_linker.py
# Projekt: ORÓMA – Headless Lern-KI (Edge)
# Version: v3.7.4
# Stand:   2026-02-26
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieser Linker erzeugt *Crossmodal Teacher-Links* zwischen:
#   • Audio-SnapTokens  (snapchains.origin = "audio/token")
#   • Vision-SnapTokens (snapchains.origin = "vision/token")
#   • einem Text-Label (typischerweise aus ASR2 One-Shot oder ASR Live)
#
# Motivation:
#   ORÓMA speichert Audio-/Vision-Tokens als SnapChains, aber ASR/ASR2 speichern
#   das Transkript derzeit primär indirekt (Empathie/Rewards). Für schnellere
#   Multimodal-Zuordnung ("Ich zeige Ball + sage Ball") brauchen wir eine
#   stabile, DB-native Brücke: link/av_label.
#
# DESIGN (produktiv & DB-schonend)
# ────────────────────────────────
# - Kein neues Schema, keine neue Tabelle.
# - Es wird eine neue SnapChain in der bestehenden Tabelle `snapchains` erzeugt:
#       origin   = "link/av_label"
#       namespace= "transfer"
#       source_id= deterministisch (dedupe)
#       blob     = JSON (klein), keine WAV/Bilddaten
#
# - Robust gegen parallele Writer:
#   nutzt core.sql_manager.get_conn() + busy_timeout und dedupliziert über source_id.
#
# - Headless: keine UI-Abhängigkeiten.
#
# ENV (Optionen)
# ──────────────
# OROMA_AV_LABEL_LINK_ENABLE=0|1         (Default 1)
# OROMA_AV_LABEL_WINDOW_SEC=<int>        (Default 4)  Zeitfenster "jetzt" rückwärts
# OROMA_AV_LABEL_STRICT_MAX_DT=<int>     (Default 2)  max. |audio_ts-vision_ts| in Sekunden
# OROMA_AV_LABEL_MAX_CANDIDATES=<int>    (Default 25) Kandidaten je Modalität
#
# OROMA_AV_LABEL_MIN_TEXT_LEN=<int>      (Default 2)  ignoriert Mini-Noise
# OROMA_AV_LABEL_STOPWORDS_DE=<0|1>      (Default 1)  einfache Stopwords
#
# HINWEIS ZUR "MODELL AUS"-IDEE
# ─────────────────────────────
# Dieser Linker baut ein retrieval-basiertes Gedächtnis (Case-Based Learning).
# Das ist nicht parametric training, fühlt sich aber "modellartig" an:
# Wiederkehrende Vision-Features + gespeicherte Labels → Zuordnung per Resonanz.
#
# =============================================================================

from __future__ import annotations

import os
import re
import time
import json
import hashlib
import logging
from core import log_guard
from typing import Any, Dict, List, Optional, Tuple

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


_ENABLE = _env_bool("OROMA_AV_LABEL_LINK_ENABLE", True)
_WINDOW_SEC = _env_int("OROMA_AV_LABEL_WINDOW_SEC", 4)
_STRICT_MAX_DT = _env_int("OROMA_AV_LABEL_STRICT_MAX_DT", 2)
_MAX_CAND = _env_int("OROMA_AV_LABEL_MAX_CANDIDATES", 25)
_MIN_TEXT_LEN = _env_int("OROMA_AV_LABEL_MIN_TEXT_LEN", 2)
_STOPWORDS_DE = _env_bool("OROMA_AV_LABEL_STOPWORDS_DE", True)

_STOP_DE = {
    "der", "die", "das", "ein", "eine", "einen", "einem", "einer",
    "und", "oder", "aber", "ist", "sind", "war", "waren",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "sie",
    "da", "hier", "dort", "jetzt", "bitte", "danke",
    "das", "dass", "dem", "den", "im", "in", "am", "an", "auf", "zu", "mit",
}


def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    # leichte Normalisierung, aber keine aggressive Lautschrift
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


def _fetch_recent(origin: str, since_ts: int, limit: int) -> List[Tuple[int, int]]:
    """
    Liefert Liste (id, ts) absteigend nach ts.
    """
    def _get(row: Any, key: str, idx: int) -> int:
        """Tolerant reader: unterstützt dict-row_factory, sqlite3.Row und tuple/list."""
        if row is None:
            raise KeyError(key)
        if isinstance(row, dict):
            return int(row.get(key) or 0)
        # sqlite3.Row ist sowohl Sequence als auch Mapping
        try:
            return int(row[key])
        except Exception:
            return int(row[idx])

    try:
        # WICHTIG: Connection immer schließen (Lock-Vermeidung)
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, ts
                FROM snapchains
                WHERE origin = ?
                  AND ts >= ?
                  AND (status IS NULL OR status != 'deleted')
                ORDER BY ts DESC
                LIMIT ?
                """,
                (origin, int(since_ts), int(limit)),
            )
            rows = cur.fetchall() or []
            out: List[Tuple[int, int]] = []
            for r in rows:
                out.append((_get(r, "id", 0), _get(r, "ts", 1)))
            return out
    except Exception as e:
        _log.debug("av_label_linker: fetch_recent failed (%s): %s", origin, e)
        return []


def _pick_best_pair(a: List[Tuple[int, int]], v: List[Tuple[int, int]]) -> Optional[Tuple[int, int, int, int]]:
    """
    Wählt Paar mit minimalem |dt|. Rückgabe: (audio_id, audio_ts, vision_id, vision_ts)
    """
    if not a or not v:
        return None
    best = None
    best_dt = None
    for (aid, ats) in a:
        for (vid, vts) in v:
            dt = abs(int(ats) - int(vts))
            if best_dt is None or dt < best_dt:
                best_dt = dt
                best = (aid, ats, vid, vts)
            if best_dt == 0:
                return best
    return best


def _already_exists(source_id: str) -> Optional[int]:
    def _get_id(row: Any) -> int:
        if row is None:
            raise KeyError("id")
        if isinstance(row, dict):
            return int(row.get("id") or 0)
        try:
            return int(row[0])
        except Exception:
            return int(row["id"])

    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id
                FROM snapchains
                WHERE origin='link/av_label' AND source_id=?
                LIMIT 1
                """,
                (str(source_id),),
            )
            r = cur.fetchone()
            return _get_id(r) if r else None
    except Exception:
        return None


def link_text_now(
    text: str,
    *,
    window_sec: int = _WINDOW_SEC,
    strict_max_dt: int = _STRICT_MAX_DT,
) -> Optional[int]:
    """
    Erzeugt einen Teacher-Link für "jetzt":
      - sucht die letzten audio/token und vision/token im Zeitfenster
      - wählt das zeitlich beste Paar (min |dt|)
      - schreibt SnapChain origin=link/av_label

    Rückgabe:
      - neue SnapChain-ID oder bestehende (dedupe via source_id)
      - None wenn keine geeigneten Kandidaten gefunden
    """
    if not _ENABLE:
        return None

    text = (text or "").strip()
    if len(text) < _MIN_TEXT_LEN:
        return None

    now = int(time.time())
    since = now - int(max(1, window_sec))

    audio = _fetch_recent("audio/token", since, _MAX_CAND)
    vision = _fetch_recent("vision/token", since, _MAX_CAND)

    pick = _pick_best_pair(audio, vision)
    if not pick:
        return None

    aid, ats, vid, vts = pick
    dt_abs = abs(int(ats) - int(vts))
    if dt_abs > int(strict_max_dt):
        return None

    tnorm = _norm_text(text)
    kws = _keywords(text)
    # "Label" ist hier bewusst nicht hart extrahiert – wir speichern das robuste Rohsignal.
    # Später (Dream) kann man daraus canonical labels ableiten.
    source_id = f"av_label:{aid}:{vid}:{_sha10(tnorm)}"

    existing = _already_exists(source_id)
    if existing:
        return existing

    # Qualität: zeitliche Nähe + etwas Textsignal
    # dt=0 → 1.0 ; dt=strict_max_dt → ~0.0
    dt_score = max(0.0, 1.0 - (float(dt_abs) / float(max(1, strict_max_dt))))
    text_score = min(1.0, float(len(kws)) / 6.0)  # 0..1
    q = round(0.15 + 0.70 * dt_score + 0.15 * text_score, 4)  # konservativ (nicht zu hoch)

    blob_obj: Dict[str, Any] = {
        "kind": "av_label",
        "audio_id": int(aid),
        "audio_ts": int(ats),
        "vision_id": int(vid),
        "vision_ts": int(vts),
        "dt_abs": int(dt_abs),
        "text_raw": text,
        "text_norm": tnorm,
        "keywords": kws,
        "window_sec": int(window_sec),
        "strict_max_dt": int(strict_max_dt),
    }
    blob = json.dumps(blob_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    payload: Dict[str, Any] = {
        "ts": int(max(ats, vts)),
        "quality": float(q),
        "blob": blob,
        "exported": 0,
        "status": "active",
        "origin": "link/av_label",
        "namespace": "transfer",
        "source_id": source_id,
        "notes": "Teacher link: audio/token ↔ vision/token (+ ASR text)",
        "version": "v3.7.3",
        "weight": 0.10,
    }

    try:
        return sql_manager.insert_snapchain(payload)
    except TypeError:
        # Kompatibilitäts-Fallback: falls insert_snapchain in älteren Ständen weniger Keys akzeptiert
        try:
            payload2 = dict(payload)
            payload2.pop("namespace", None)
            payload2.pop("source_id", None)
            payload2.pop("weight", None)
            return sql_manager.insert_snapchain(payload2)
        except Exception as e:
            log_guard.log_suppressed(_log, key="av_label_linker.insert.fallback", msg="insert fallback failed", exc=e, level=logging.WARNING, interval_s=120)
            return None
    except Exception as e:
        log_guard.log_suppressed(_log, key="av_label_linker.insert", msg="insert failed", exc=e, level=logging.WARNING, interval_s=120)
        return None
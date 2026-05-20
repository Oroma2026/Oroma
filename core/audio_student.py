#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/audio_student.py
# Projekt: ORÓMA
# Modul:   Audio Student-Teacher Logger (Whisper → Student-ASR)
# Version: v3.7.3-r2
# Stand:   2026-01-03
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Dieses Modul implementiert den "Student-Teacher"-Baustein für Audio:
#
#     • Teacher:  externer ASR (z. B. Whisper über AudioWrapper)
#     • NEU: log_pair() Kompatibilität für AudioWrapper (ASR Teacher-Logging)
#     • Student:  ORÓMAs eigene zukünftige Audio-/ASR-Logik (noch im Aufbau)
#
#   Für jede Session wird eine Zeile in der Tabelle audio_student_pairs
#   angelegt:
#
#       id, ts, source,
#       transcript_teacher,
#       transcript_student,
#       distance,
#       feat_json,
#       meta_json
#
#   Aktueller Fokus (Phase 1 – nur Teacher):
#     • AudioWrapper (Hub-first) zeichnet ein kurzes Fenster auf
#     • Whisper (wenn aktiviert) liefert transcript_teacher
#     • audio_wrapper._features_from_signal() erzeugt einen Audio-Feature-Vektor
#     • Es wird ein Datensatz in audio_student_pairs gespeichert
#       und gleichzeitig ein Episoden-Event erzeugt.
#
#   Spätere Phase (Student):
#     • Ein Student-ASR-Modul kann transcript_student + distance nachtragen
#       (z. B. Levenshtein / CER / WER) und meta_json erweitern.
#
# Integration
# ───────────
#   • Kern-Abhängigkeiten:
#       - wrappers.audio_wrapper.AudioWrapper
#       - wrappers.audio_wrapper._features_from_signal (kein Code-Duplikat)
#       - core.sql_manager.get_conn()  (Tabelle audio_student_pairs existiert bereits)
#       - core.episodic_writer.EpisodeWriter (EpisodeWriter für Audio)
#
#   • Typischer Aufruf (CLI):
#
#       PYTHONPATH=/opt/ai/oroma \
#         python3 -m core.audio_student --duration 3 --source "asr/whisper"
#
#     → zeichnet ~3 s Audio auf, transkribiert mit Whisper (falls aktiv),
#       loggt einen Datensatz + Episoden-Event und gibt ID + Text zurück.
#
# ENV-Variablen
# ─────────────
#   OROMA_AUDIO_STUDENT_SOURCE      Default-Source für neue Paare
#                                   (z. B. "asr/whisper", Default: "asr/whisper")
#   OROMA_AUDIO_STUDENT_DURATION    Default-Dauer in Sekunden (float, Default: 3.0)
#
#   Audio-/ASR-spezifische ENV kommen direkt aus audio_wrapper.py:
#     - OROMA_AUDIO_SR, OROMA_AUDIO_CH, OROMA_AUDIO_WRAPPER_USE_HUB
#     - OROMA_WHISPER_ENABLE, OROMA_WHISPER_MODEL, OROMA_WHISPER_LANG
#
# CLI
# ───
#   python -m core.audio_student --duration 3 --source "asr/whisper"
#
#   Optionen:
#     --duration FLOAT   Aufnahmedauer in Sekunden (Default: ENV oder 3.0)
#     --source   TEXT    Quell-Label (Default: ENV oder "asr/whisper")
#
# Design-Hinweise
# ───────────────
#   • Kein doppelter DSP-Code: Features kommen direkt aus audio_wrapper.
#   • DB-Logik minimal und begrenzt auf audio_student_pairs.
#   • Episoden-Schicht: Jede Audio-Teacher-Session legt ein Episoden-Event an.
# =============================================================================

from __future__ import annotations

import os
import time
import json
import math
import logging
import argparse
from typing import Optional, Dict, Any, List
from core.log_guard import log_suppressed
import logging

try:
    import numpy as np  # noqa: F401  # aktuell nur indirekt über audio_wrapper genutzt
except Exception:  # pragma: no cover
    np = None  # type: ignore

# Reuse statt Duplizieren: AudioWrapper + Feature-Funktion
from wrappers.audio_wrapper import AudioWrapper, _features_from_signal  # type: ignore

from core import sql_manager
from core import episodic_writer  # Episoden-Writer für Audio


LOG = logging.getLogger("oroma.audio_student")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [AudioStudent] %(message)s"))
    LOG.addHandler(_h)
LOG.setLevel(os.environ.get("OROMA_LOG_LEVEL", "INFO"))


# =============================================================================
# ENV-Helper
# =============================================================================

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# =============================================================================
# Episoden-Writer für Audio-Teacher-Sessions
# =============================================================================

_AUDIO_EPISODE_WRITER: Optional[episodic_writer.EpisodeWriter] = None


def get_audio_episode_writer() -> episodic_writer.EpisodeWriter:
    """
    Liefert einen (globalen) EpisodeWriter für Audio-Sessions.
    Eine Instanz pro Prozess – Episoden rotieren automatisch nach
    max_duration / max_idle.
    """
    global _AUDIO_EPISODE_WRITER
    if _AUDIO_EPISODE_WRITER is None:
        _AUDIO_EPISODE_WRITER = episodic_writer.EpisodeWriter(
            kind="audio",
            source="audio_student",
            label="Audio-Teacher-Session",
            meta={"created_by": "audio_student"},
            max_duration_sec=3600,  # 1h Session
            max_idle_sec=600,       # 10min Pause → neue Episode
        )
    return _AUDIO_EPISODE_WRITER


# =============================================================================
# DB-Helfer für audio_student_pairs (inkl. Episoden-Logging)
# =============================================================================

def insert_audio_pair(
    ts: int,
    source: str,
    transcript_teacher: Optional[str],
    transcript_student: Optional[str] = None,
    distance: Optional[float] = None,
    feat: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Fügt einen Datensatz in audio_student_pairs ein UND erzeugt ein Episoden-Event.

    WICHTIG:
      - transcript_teacher ist im Schema NOT NULL.
        → None wird hier zu "" normalisiert, damit kein Constraint-Fehler entsteht.
      - Ob die Session semantisch "ok" war, entscheidet der Aufrufer (record_teacher_pair).

    Parameter
    ---------
    ts : int
        UNIX-Timestamp der Aufnahme.
    source : str
        Quelle/Label (z. B. "asr/whisper").
    transcript_teacher : str|None
        Teacher-Transkript (Whisper o. Ä.); kann None sein → wird zu "".
    transcript_student : str|None
        Student-Transkript (optional; Phase 2).
    distance : float|None
        Distanzmaß Teacher↔Student (z. B. WER/CER); optional.
    feat : dict|None
        Audio-Features (werden als JSON gespeichert).
    meta : dict|None
        Meta-Infos (ENV, Dauer, sr, etc.; JSON).

    Rückgabe
    --------
    int|None: ID des neuen Datensatzes oder None bei Fehler.
    """
    try:
        feat_json = json.dumps(feat, ensure_ascii=False, separators=(",", ":")) if feat is not None else None
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":")) if meta is not None else None

        dist_val: Optional[float]
        if distance is None or not isinstance(distance, (int, float)) or not math.isfinite(distance):
            dist_val = None
        else:
            dist_val = float(distance)

        teacher_text = (transcript_teacher or "").strip()

        with sql_manager.get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO audio_student_pairs
                    (ts, source, transcript_teacher,
                     transcript_student, distance,
                     feat_json, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(ts),
                    str(source),
                    teacher_text,
                    transcript_student,
                    dist_val,
                    feat_json,
                    meta_json,
                ),
            )
            conn.commit()
            pair_id = int(cur.lastrowid)
            LOG.info("audio_student_pairs: Insert id=%s source=%s", pair_id, source)
    except Exception as e:
        LOG.error("Insert in audio_student_pairs fehlgeschlagen: %s", e)
        return None

    # Episoden-Logging (nicht kritisch – Fehler hier sollen die Hauptlogik nicht killen)
    try:
        writer = get_audio_episode_writer()
        writer.log_audio_pair(
            pair_id=pair_id,
            ts=ts,
            distance=dist_val,
            extra={
                "source": source,
                "teacher_len": len(teacher_text),
                "student_len": len(transcript_student or "") if transcript_student else 0,
            },
        )
    except Exception as e:
        LOG.warning("AudioStudent: Episoden-Logging fehlgeschlagen: %s", e)

    return pair_id


# =============================================================================
# Compatibility Helper: log_pair() für AudioWrapper
# =============================================================================
#
# wrappers/audio_wrapper.py (und ggf. ältere Tools) rufen historisch
# _audio_student.log_pair(...) auf. In einigen Ständen existierte nur
# insert_audio_pair(). Damit ASR-Logging nie den Audio-Fluss stört,
# stellen wir hier eine kleine, robuste Kompatibilitätsfunktion bereit.
#
# Eigenschaften:
#   • Speichert Teacher-Text (z.B. Whisper/whisper.cpp Ergebnis) + optional Student
#   • Kodiert feat_vector als {"snap_feature": [...]} (JSON)
#   • Erstellt zusätzlich ein Episoden-Event über EpisodicWriter
#   • Gibt die DB-ID (audio_student_pairs.id) zurück oder None
# =============================================================================

def log_pair(
    teacher_text: str,
    student_text: Optional[str] = None,
    feat_vector: Optional[List[float]] = None,
    source: str = "asr/teacher",
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    ts = int(time.time())
    feat = {"snap_feature": (feat_vector or [])}
    return insert_audio_pair(
        ts=ts,
        source=str(source or "asr/teacher"),
        transcript_teacher=str(teacher_text or ""),
        transcript_student=(str(student_text) if student_text is not None else None),
        distance=None,
        feat=feat,
        meta=(meta or {}),
    )


def update_student_pair(
    pair_id: int,
    transcript_student: Optional[str] = None,
    distance: Optional[float] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Aktualisiert Student-Felder eines bestehenden Paares (Phase 2).

    - transcript_student: Student-Transkript
    - distance: Distanz Teacher↔Student
    - extra_meta: wird in meta_json gemergt (wenn möglich)
    """
    if pair_id is None:
        return False

    try:
        with sql_manager.get_conn() as conn:
            row = conn.execute(
                "SELECT meta_json FROM audio_student_pairs WHERE id=?",
                (int(pair_id),),
            ).fetchone()

            meta_loaded: Dict[str, Any]
            if row and row.get("meta_json"):
                try:
                    meta_loaded = json.loads(row["meta_json"])
                    if not isinstance(meta_loaded, dict):
                        meta_loaded = {}
                except Exception:
                    meta_loaded = {}
            else:
                meta_loaded = {}

            if extra_meta and isinstance(extra_meta, dict):
                meta_loaded.update(extra_meta)

            meta_json = json.dumps(meta_loaded, ensure_ascii=False, separators=(",", ":"))

            fields: List[str] = ["meta_json=?"]
            params: List[Any] = [meta_json]

            if transcript_student is not None:
                fields.append("transcript_student=?")
                params.append(transcript_student)

            if distance is not None and isinstance(distance, (int, float)) and math.isfinite(distance):
                fields.append("distance=?")
                params.append(float(distance))

            params.append(int(pair_id))
            conn.execute(
                f"UPDATE audio_student_pairs SET {', '.join(fields)} WHERE id=?",
                tuple(params),
            )
            conn.commit()
        LOG.info("audio_student_pairs: Update id=%s", pair_id)
        return True
    except Exception as e:
        LOG.error("Update audio_student_pairs id=%s fehlgeschlagen: %s", pair_id, e)
        return False


# =============================================================================
# Teacher-Recording: AudioWrapper + Whisper
# =============================================================================

def record_teacher_pair(
    source: Optional[str] = None,
    duration: Optional[float] = None,
    notes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Nimmt eine kurze Audio-Sequenz auf, transkribiert sie mit dem Teacher
    (Whisper via AudioWrapper) und speichert alles in audio_student_pairs
    (inkl. Episoden-Event).

    Rückgabe:
        {
          "ok": bool,
          "id": int|None,
          "teacher": str|None,
          "duration": float,
          "error": str|None
        }
    """
    src = source or _env_str("OROMA_AUDIO_STUDENT_SOURCE", "asr/whisper")
    dur = duration if duration is not None else _env_float("OROMA_AUDIO_STUDENT_DURATION", 3.0)
    dur = max(0.5, float(dur))  # mind. 0.5 s, um irgendwas zu haben

    ts = int(time.time())
    meta: Dict[str, Any] = dict(notes or {})

    # ENV+Kontext in Meta, damit man später sieht, wie der Teacher konfiguriert war:
    meta.setdefault("sr_env", os.environ.get("OROMA_AUDIO_SR", "16000"))
    meta.setdefault("lang", os.environ.get("OROMA_WHISPER_LANG", "de"))
    meta.setdefault("model", os.environ.get("OROMA_WHISPER_MODEL", "small"))
    meta.setdefault("duration", dur)
    meta.setdefault("source", src)

    aw = AudioWrapper()  # nutzt ENV (Hub, Whisper etc.)
    try:
        LOG.info("AudioStudent: Aufnahme starten (dur=%.2fs, source=%s)", dur, src)
        aw.start()
        # kurzes Aufwärmen, damit Ringbuffer gefüllt wird
        time.sleep(0.25)
        x = aw.read_audio(dur)
    finally:
        try:
            aw.stop()
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.audio_student.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

    if x is None or getattr(x, "size", 0) == 0:
        err = "no_audio"
        LOG.warning("AudioStudent: Keine Audiodaten erhalten.")
        meta["error"] = err
        pair_id = insert_audio_pair(
            ts=ts,
            source=src,
            transcript_teacher=None,
            transcript_student=None,
            distance=None,
            feat=None,
            meta=meta,
        )
        return {"ok": False, "id": pair_id, "teacher": None, "duration": dur, "error": err}

    # Features über bestehende Funktion aus audio_wrapper (kein Copy&Paste)
    try:
        feats = _features_from_signal(x, aw.sr)
    except Exception as e:
        LOG.warning("AudioStudent: Feature-Extraktion fehlgeschlagen: %s", e)
        feats = None

    # Teacher-Transkript via Whisper (falls aktiv)
    try:
        teacher = aw.transcribe(x)
        if teacher:
            LOG.info("AudioStudent: Teacher-Transkript erhalten: %s", teacher.strip()[:80])
        else:
            LOG.info("AudioStudent: Kein Teacher-Transkript.")
    except Exception as e:
        LOG.error("AudioStudent: Transkription fehlgeschlagen: %s", e)
        teacher = None

    if not teacher:
        meta["error"] = "no_text"
        meta["has_teacher"] = False
    else:
        meta["has_teacher"] = True

    pair_id = insert_audio_pair(
        ts=ts,
        source=src,
        transcript_teacher=teacher,
        transcript_student=None,
        distance=None,
        feat=feats,
        meta=meta,
    )

    ok = (pair_id is not None) and bool(teacher)
    return {
        "ok": ok,
        "id": pair_id,
        "teacher": teacher,
        "duration": dur,
        "error": None if ok else ("no_text" if not teacher else None),
    }


# =============================================================================
# CLI
# =============================================================================

def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ORÓMA Audio Student-Teacher Logger")
    ap.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Aufnahmedauer in Sekunden (Default: ENV OROMA_AUDIO_STUDENT_DURATION oder 3.0)",
    )
    ap.add_argument(
        "--source",
        type=str,
        default=None,
        help='Source-Label, z. B. "asr/whisper" (Default: ENV OROMA_AUDIO_STUDENT_SOURCE oder "asr/whisper")',
    )
    return ap.parse_args()


if __name__ == "__main__":
    # Optional etwas lauter für Standalone-Tests
    logging.getLogger().setLevel("INFO")
    LOG.setLevel("INFO")

    args = _parse_args()

    # Schema sicherstellen (audio_student_pairs wurde in sql_manager definiert)
    try:
        sql_manager.ensure_schema()
    except Exception as e:
        # Wenn ensure_schema bereits woanders aufgerufen wurde, ist es okay
        log_suppressed(
            logging.getLogger(__name__),
            key="core.audio_student.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    res = record_teacher_pair(
        source=args.source,
        duration=args.duration,
        notes=None,
    )

    print("OK:", res.get("ok"))
    print("ID:", res.get("id"))
    print("Teacher:", (res.get("teacher") or "").strip())
    print("Duration:", res.get("duration"))
    if res.get("error"):
        print("Error:", res.get("error"))

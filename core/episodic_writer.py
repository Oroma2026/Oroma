#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/episodic_writer.py
# Projekt:   ORÓMA (Offline-First · Headless · Episodisches Gedächtnis)
# Modul:     EpisodeWriter – schreibt „Sessions/Episoden“ aus kontinuierlichen Events (Audio/Vision) in episodes + episode_events
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist eine kleine, robuste Hilfsschicht, die aus fortlaufenden Ereignissen
# („Events“) logisch zusammenhängende Episoden („Sessions“) bildet und in der DB ablegt.
#
# Ziel:
#   - Der Datenstrom (Audio-Pairs, Vision cam_tokens, …) wird nicht als lose Events
#     betrachtet, sondern in sinnvolle, rotierende Episoden gruppiert:
#       • Beginn (ts_start)
#       • Ende   (ts_end)
#       • Quelle (source) und Art (kind)
#       • Events (episode_events) mit Referenzen auf Ursprungstabellen
#
# Dieses Modul ist bewusst:
#   - headless (keine UI/Qt/Wayland/X11 Anforderungen)
#   - dependency-arm (stdlib + core.sql_manager)
#   - „best effort“ (Fehler im Episoden-Logging dürfen niemals Hauptpfade stören)
#
# DB-SCHEMA (WIRD VON sql_manager BEREITGESTELLT)
# ───────────────────────────────────────────────
# EpisodeWriter nutzt ausschließlich Helper aus core.sql_manager:
#   - insert_episode(ts_start, kind, label, source, meta)
#   - update_episode_end(episode_id, ts_end)
#   - insert_episode_event(episode_id, ts, event_type, ref_table, ref_id, meta)
#
# Erwartete Tabellen:
#   1) episodes
#      - ts_start, ts_end, kind, source, label, meta_json
#   2) episode_events
#      - episode_id, ts, event_type, ref_table, ref_id, meta_json
# (episodic_metrics ist in der Dateiheader-Historie erwähnt, im aktuellen Codepfad
#  aber nicht zwingend genutzt – Events sind der zentrale Output.)
#
# ROTATION / SESSION-CUTS (WICHTIGES VERHALTEN)
# ────────────────────────────────────────────
# Eine Episode wird automatisch rotiert, wenn:
#   - max_duration_sec überschritten wird (Hard-Limit, z. B. 3600s)
#   - max_idle_sec überschritten wird (zu lange Pause zwischen Events)
#
# Ablauf bei Rotation:
#   1) bestehende Episode wird geschlossen (update_episode_end)
#   2) neue Episode wird gestartet (insert_episode)
#   3) Events werden in die neue Episode geschrieben
#
# Fehler in close/open werden defensiv geloggt und führen nicht zum Crash.
#
# EVENT-TYPEN (AKTUELLER STAND)
# ─────────────────────────────
# 1) Audio:
#   EpisodeWriter.log_audio_pair(pair_id, ts, distance, extra)
#     - event_type = "audio_pair"
#     - ref_table  = "audio_student_pairs"
#     - ref_id     = pair_id
#     - meta enthält u. a. distance + extra
#
# 2) Vision:
#   EpisodeWriter.log_vision_token(snap_id, ts, extra)
#     - event_type = "cam_token"
#     - ref_table  = "snapchains"
#     - ref_id     = snap_id
#     - meta enthält u. a. origin/q/motion/edges/color/dim
#
# GLOBALE WRITER (CONVENIENCE / VERDRAHTUNG)
# ─────────────────────────────────────────
# Dieses Modul bietet zwei globale Singletons (prozessweit):
#   - get_audio_writer()  → kind="audio",  source="audio_student"
#   - get_vision_writer() → kind="vision", source="vision/token"
#
# Vision ist explizit so gebaut, dass es automatisch mit der Vision-Token-Pipeline
# zusammenarbeitet:
#   - sql_manager.insert_cam_token() ruft (best effort) die Convenience-Funktion:
#       log_vision_cam_token_global(ts, snap_id, q, origin, motion, edges, color, dim)
#     auf, welche wiederum den globalen Vision-Writer nutzt und das Event loggt.
#
# Das ist bewusst entkoppelt:
#   - EpisodeWriter kennt keine Vision-Details (keine Embeddings/Model-Abhängigkeiten)
#   - er schreibt nur Referenzen und Meta.
#
# FEHLERBEHANDLUNG / PRODUKTIONSINVARIANTE
# ─────────────────────────────────────────
# Alle Public-Methoden sind „defensiv“:
#   - Exceptions werden gefangen
#   - es wird geloggt (LOG.warning)
#   - der Hauptprozess (AgentLoop, Hook, Tool) läuft weiter
#
# Grund:
# - Episoden-Logging ist wertvoll, aber niemals kritischer als Live-Perzeption.
#
# KONFIGURATION (PARAMETER)
# ─────────────────────────
# EpisodeWriter(...) akzeptiert:
#   kind, source, label, meta (Basis-Meta)
#   max_duration_sec (Default: 3600)
#   max_idle_sec     (Audio Default: 600; Vision Default: 300 im get_vision_writer)
#
# Diese Werte sind bewusst pragmatisch für Edge-Betrieb:
# - Vision: kürzeres idle, weil cam_tokens oft regelmäßiger kommen
# - Audio: längeres idle, weil Paare seltener kommen können
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class EpisodeWriter:
#   - log_audio_pair(...)
#   - log_vision_token(...)
#   - close(...)
#
# Convenience:
#   - get_audio_writer()
#   - get_vision_writer()
#   - log_vision_cam_token_global(...)
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Keine harten Dependencies außer sql_manager (Headless-Core muss booten).
# - „best effort“ muss bleiben (Logging darf Live-Pfade nie blockieren).
# - Rotation nach Duration/Idle ist Kernfunktion (sonst werden Episoden riesig).
# - Vision-Hook Integration über log_vision_cam_token_global muss stabil bleiben
#   (sonst fehlen Vision-Sessions im episodischen Gedächtnis).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from core import sql_manager  # nutzt insert_episode(), update_episode_end(), insert_episode_event()


LOG = logging.getLogger("oroma.episodic_writer")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [EpisodeWriter] %(message)s"))
    LOG.addHandler(_h)
LOG.setLevel(logging.INFO)


class EpisodeWriter:
    """
    Stateful Episoden-Schreiber für eine Quelle (z. B. Audio-Teacher, Vision-Stream).

    Parameter
    ---------
    kind : str
        Grober Typ der Episode ('audio', 'vision', 'game', ...).
    source : str
        Quelle/Subsystem ('audio_student', 'vision/token', 'game:snake', ...).
    label : str
        Menschlich lesbare Bezeichnung der Episode.
    meta : dict|None
        Basis-Metadaten, die in meta_json landen (z. B. {"created_by": "audio_student"}).
    max_duration_sec : float
        Hard-Limit für die Dauer einer Episode (wird bei Überschreitung rotiert).
    max_idle_sec : float
        Maximal zulässige Inaktivität zwischen zwei Events (danach neue Episode).
    """

    def __init__(
        self,
        kind: str,
        source: str,
        label: str,
        meta: Optional[Dict[str, Any]] = None,
        max_duration_sec: float = 3600.0,
        max_idle_sec: float = 600.0,
    ) -> None:
        self.kind = str(kind)
        self.source = str(source)
        self.label = str(label)
        self.meta_base: Dict[str, Any] = dict(meta or {})
        self.max_duration_sec = float(max_duration_sec)
        self.max_idle_sec = float(max_idle_sec)

        # Laufende Episode
        self._episode_id: Optional[int] = None
        self._ts_start: Optional[int] = None
        self._ts_last_event: Optional[int] = None
        self._event_count: int = 0

    # -------------------------------------------------------------------------
    # Intern: Episoden-Verwaltung
    # -------------------------------------------------------------------------

    def _should_rotate(self, now_ts: int) -> bool:
        """
        Entscheidet, ob eine neue Episode gestartet werden soll.
        """
        if self._episode_id is None or self._ts_start is None:
            return True

        # Dauer-Limit
        if self.max_duration_sec > 0 and (now_ts - self._ts_start) > self.max_duration_sec:
            return True

        # Idle-Limit
        if self.max_idle_sec > 0 and self._ts_last_event is not None:
            if (now_ts - self._ts_last_event) > self.max_idle_sec:
                return True

        return False

    def _open_new_episode(self, ts_start: int, extra_meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
        meta = dict(self.meta_base)
        if extra_meta:
            meta.update(extra_meta)

        eid = sql_manager.insert_episode(
            ts_start=ts_start,
            kind=self.kind,
            label=self.label,
            source=self.source,
            meta=meta,
        )
        if eid is not None:
            LOG.info("Neue Episode gestartet: id=%s kind=%s source=%s", eid, self.kind, self.source)
            self._episode_id = eid
            self._ts_start = ts_start
            self._ts_last_event = ts_start
            self._event_count = 0
        else:
            LOG.warning("Konnte Episode nicht anlegen (kind=%s, source=%s)", self.kind, self.source)
        return eid

    def _ensure_episode(self, ts: Optional[int] = None, extra_meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
        now_ts = int(ts if ts is not None else time.time())
        # Rotation prüfen
        if self._should_rotate(now_ts):
            # alte Episode ggf. schließen
            if self._episode_id is not None:
                try:
                    sql_manager.update_episode_end(self._episode_id, self._ts_last_event or now_ts)
                except Exception as e:  # pragma: no cover - defensive
                    LOG.warning("EpisodeWriter: update_episode_end(%s) fehlgeschlagen: %s", self._episode_id, e)
            # neue Episode anlegen
            return self._open_new_episode(now_ts, extra_meta=extra_meta)
        # bestehende Episode weiterverwenden
        return self._episode_id

    def _on_event(self, ts: Optional[int]) -> int:
        now_ts = int(ts if ts is not None else time.time())
        self._ts_last_event = now_ts
        self._event_count += 1
        return now_ts

    def close(self, ts_end: Optional[int] = None) -> None:
        """
        Schließt die aktuelle Episode explizit (optional, z. B. am Ende einer Session).
        """
        if self._episode_id is None:
            return
        try:
            sql_manager.update_episode_end(self._episode_id, ts_end or self._ts_last_event or int(time.time()))
            LOG.info("EpisodeWriter: Episode explizit geschlossen: id=%s", self._episode_id)
        except Exception as e:  # pragma: no cover - defensive
            LOG.warning("EpisodeWriter: close() fehlgeschlagen: %s", e)
        finally:
            self._episode_id = None
            self._ts_start = None
            self._ts_last_event = None
            self._event_count = 0

    # -------------------------------------------------------------------------
    # Spezifische Logger
    # -------------------------------------------------------------------------

    def log_audio_pair(
        self,
        pair_id: int,
        ts: Optional[int],
        distance: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Loggt ein Audio-Teacher/Student-Paar als Episoden-Event.

        Event:
          event_type = 'audio_pair'
          ref_table  = 'audio_student_pairs'
          ref_id     = pair_id
        """
        try:
            now_ts = self._on_event(ts)
            eid = self._ensure_episode(now_ts, extra_meta={"last_event": "audio_pair"})
            if eid is None:
                return

            meta = dict(extra or {})
            meta.setdefault("distance", distance)
            meta.setdefault("event", "audio_pair")

            sql_manager.insert_episode_event(
                episode_id=eid,
                ts=now_ts,
                event_type="audio_pair",
                ref_table="audio_student_pairs",
                ref_id=int(pair_id),
                meta=meta,
            )
        except Exception as e:  # pragma: no cover - defensive
            LOG.warning("EpisodeWriter.log_audio_pair(): Fehler: %s", e)

    def log_vision_token(
        self,
        snap_id: int,
        ts: Optional[int],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Loggt einen Vision-SnapToken (cam_token) als Episoden-Event.

        Event:
          event_type = 'cam_token'
          ref_table  = 'snapchains'
          ref_id     = snap_id
        """
        try:
            now_ts = self._on_event(ts)
            eid = self._ensure_episode(now_ts, extra_meta={"last_event": "cam_token"})
            if eid is None:
                return

            meta = dict(extra or {})
            meta.setdefault("event", "cam_token")

            sql_manager.insert_episode_event(
                episode_id=eid,
                ts=now_ts,
                event_type="cam_token",
                ref_table="snapchains",
                ref_id=int(snap_id),
                meta=meta,
            )
        except Exception as e:  # pragma: no cover - defensive
            LOG.warning("EpisodeWriter.log_vision_token(): Fehler: %s", e)


# =============================================================================
# Globale Convenience-Funktionen
# =============================================================================

_AUDIO_WRITER: Optional[EpisodeWriter] = None
_VISION_WRITER: Optional[EpisodeWriter] = None


def get_audio_writer() -> EpisodeWriter:
    """
    Standard-Writer für Audio-Teacher-Sessions.
    (Wird aktuell von core.audio_student genutzt.)
    """
    global _AUDIO_WRITER
    if _AUDIO_WRITER is None:
        _AUDIO_WRITER = EpisodeWriter(
            kind="audio",
            source="audio_student",
            label="Audio-Teacher-Session",
            meta={"created_by": "audio_student"},
            max_duration_sec=3600,
            max_idle_sec=600,
        )
    return _AUDIO_WRITER


def get_vision_writer() -> EpisodeWriter:
    """
    Standard-Writer für Vision-Sessions (cam_token-Stream).
    Wird über sql_manager.insert_cam_token() getriggert.
    """
    global _VISION_WRITER
    if _VISION_WRITER is None:
        _VISION_WRITER = EpisodeWriter(
            kind="vision",
            source="vision/token",
            label="Vision-Session",
            meta={"created_by": "insert_cam_token"},
            max_duration_sec=3600,
            max_idle_sec=300,
        )
    return _VISION_WRITER


def log_vision_cam_token_global(
    ts: int,
    snap_id: int,
    q: float,
    origin: str,
    motion: Optional[float] = None,
    edges: Optional[float] = None,
    color: Optional[float] = None,
    dim: Optional[int] = None,
) -> None:
    """
    Convenience-Funktion für sql_manager.insert_cam_token().

    Sie versucht:
      • einen globalen Vision-EpisodeWriter zu holen,
      • ein cam_token-Event zu loggen.

    Fehler werden intern geloggt und sonst ignoriert.
    """
    try:
        writer = get_vision_writer()
        extra: Dict[str, Any] = {
            "origin": origin,
            "q": float(q),
            "motion": motion,
            "edges": edges,
            "color": color,
            "dim": dim,
        }
        writer.log_vision_token(snap_id=int(snap_id), ts=int(ts), extra=extra)
    except Exception as e:  # pragma: no cover - defensive
        LOG.warning("log_vision_cam_token_global(): Fehler: %s", e)
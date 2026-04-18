#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Datei:      /opt/ai/oroma/core/nmr_synaptic_plasticity.py
# Projekt:    🧠 ORÓMA v3.8 – NMR Synaptische Plastizität (Dream-Job, Relationstyp)
# Modul:      NMR Synaptische Plastizität – „synaptic“-Relationen (ObjectGraph Backbone)
# Stand:      2026-04-18
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:     MIT
# =============================================================================
#
# ZWECK / SYSTEMROLLE
# ───────────────────
# Dieses Modul implementiert synaptische Plastizität als Dream-Job, indem es
# Beziehungen im bestehenden ObjectGraph-Backbone verstärkt/abschwächt:
#   - Synapsen sind object_relations mit relation='synaptic'
#   - Gewicht liegt in object_relations.confidence (w ∈ [0..1])
#   - Zusatzwerte/Diagnostik werden als JSON in object_relations.notes geführt
#
# Das Ziel ist eine biologisch inspirierte, robuste Langzeit-Anpassung, ohne eine
# separate Graph-DB oder neue Kern-Tabellen zu benötigen.
#
# PRODUKTIONSINVARIANTEN (EDGE / 24-7)
# ────────────────────────────────────
# - Dream-only: schwere Updates gehören in die Offline-Phase (keine Live-Locks).
# - Additiv/defensiv: keine destruktiven DB-Operationen, keine Schema-Brüche.
# - Sichtbare Fehler: keine stillen Failures; Logs sind rate-limited aber erkennbar.
# - DB-Disziplin: Connections werden sauber geschlossen (Context Manager).
#
# ALGORITHMUS (HIGH LEVEL)
# ────────────────────────
# - Eingabe: Episoden-/Event-Samples (co-occurrence/sequence) aus SnapChains/Events
# - Update: Hebbian/Cooc-ähnliche Verstärkung + Zeit-Decay (Half-Life)
# - Output: confidence update + notes JSON (hebb, cooc, sim, counts, timestamps, scope)
#
# ENV (AUSZUG)
# ────────────
#   OROMA_NMR_SYN_ENABLE
#   OROMA_NMR_SYN_MAX_EPISODES_PER_RUN
#   OROMA_NMR_SYN_EVENTS_PER_EP
#   OROMA_NMR_SYN_WINDOW
#   OROMA_NMR_SYN_LR
#   OROMA_NMR_SYN_HALF_LIFE_SEC
#   OROMA_NMR_SYN_MIN_EP_TS_GAP_SEC
#
# CHECKPOINT / IDEMPOTENZ
# ───────────────────────
# - Der Dream-Job ist so ausgelegt, dass er wiederanlaufbar ist (Checkpointing über
#   persistente Metriken/Marker), um doppelte Verarbeitung zu vermeiden.
# =============================================================================
# END HEADER
# =============================================================================
from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from core import sql_manager
# NOTE (PRODUKTION): ORÓMA nutzt für rate-limited Fehlerlogs zentral
# core.log_guard.log_suppressed. Ein Import aus core.utils ist in einigen
# Versionen nicht vorhanden und würde diese Dream-Phase komplett deaktivieren
# (ImportError → Dream meldet "Modul nicht verfügbar").
from core.log_guard import log_suppressed

import os


LOG = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    try:
        return int(str(os.environ.get(key, default)))
    except Exception:
        return int(default)


def _env_float(key: str, default: float) -> float:
    try:
        return float(str(os.environ.get(key, default)))
    except Exception:
        return float(default)


def _env_bool(key: str, default: bool) -> bool:
    v = str(os.environ.get(key, "1" if default else "0")).strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _get_last_checkpoint_ts(conn) -> int:
    """Liest den zuletzt verarbeiteten episodes.ts_start aus metrics (best-effort)."""
    try:
        cur = conn.execute(
            """
            SELECT value
              FROM metrics
             WHERE key = 'nmr:synapses:last_episode_ts'
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            return 0
        return int(float(row["value"] or 0.0))
    except Exception:
        return 0


def _set_checkpoint_ts(conn, episode_ts: int) -> None:
    """Persistiert Checkpoint als Append-Eintrag in metrics."""
    try:
        now = int(time.time())
        conn.execute(
            "INSERT INTO metrics(key, ts, value) VALUES(?,?,?)",
            ('nmr:synapses:last_episode_ts', now, float(int(episode_ts))),
        )
    except Exception:
        # Checkpoint darf nicht den Job brechen.
        pass


def _table_has_column(conn, table: str, col: str) -> bool:
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        for r in cur.fetchall() or []:
            if str(r[1]) == str(col) or str(r.get("name")) == str(col):  # robust across row formats
                return True
    except Exception:
        return False
    return False


def _fetch_episodes(conn, since_ts: int, limit: int) -> List[Dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT id, ts_start, kind, source, label
          FROM episodes
         WHERE ts_start > ?
         ORDER BY ts_start ASC
         LIMIT ?
        """,
        (int(since_ts), int(limit)),
    )
    return [dict(r) for r in (cur.fetchall() or [])]


def _fetch_episode_events(conn, episode_id: int, limit: int) -> List[Dict[str, Any]]:
    # Optional columns
    has_idx = _table_has_column(conn, "episode_events", "idx")
    order = "idx ASC" if has_idx else "ts ASC"
    cur = conn.execute(
        f"""
        SELECT id, ts, event_type, ref_table, ref_id, meta_json
          FROM episode_events
         WHERE episode_id = ?
         ORDER BY {order}
         LIMIT ?
        """,
        (int(episode_id), int(limit)),
    )
    return [dict(r) for r in (cur.fetchall() or [])]


def _cooc_inc(dist: int) -> float:
    # 1/(1+dist) in (0..1]
    d = max(1, int(dist))
    return 1.0 / (1.0 + float(d))


def _decay_factor(dt_sec: float, half_life_sec: float) -> float:
    if half_life_sec <= 0:
        return 1.0
    # exp(-ln(2) * dt / half_life)
    return math.exp(-math.log(2.0) * float(dt_sec) / float(half_life_sec))


def _norm_from_hebb(hebb: float) -> float:
    # w = 1 - exp(-hebb)
    h = max(0.0, float(hebb))
    w = 1.0 - math.exp(-h)
    if w < 0.0:
        return 0.0
    if w > 1.0:
        return 1.0
    return w


def run_plasticity_once() -> Dict[str, Any]:
    """Ein Lauf (MVP): Ko-Okkurrenz-Synapsen innerhalb neuer Episoden."""
    if not _env_bool("OROMA_NMR_SYN_ENABLE", True):
        return {"ok": True, "skipped": True, "reason": "disabled"}

    max_eps = _env_int("OROMA_NMR_SYN_MAX_EPISODES_PER_RUN", 500)
    ev_cap = _env_int("OROMA_NMR_SYN_EVENTS_PER_EP", 25)
    window = _env_int("OROMA_NMR_SYN_WINDOW", 3)
    lr = _env_float("OROMA_NMR_SYN_LR", 0.05)
    half_life_sec = _env_int("OROMA_NMR_SYN_HALF_LIFE_SEC", 2592000)
    min_ep_gap = _env_int("OROMA_NMR_SYN_MIN_EP_TS_GAP_SEC", 0)

    # Ensure core schema (includes object graph tables)
    try:
        sql_manager.ensure_schema()
    except Exception as e:
        log_suppressed(
            LOG,
            key="nmr_synapses.ensure_schema",
            msg="NMR synapses: ensure_schema failed",
            exc=e,
            level=logging.WARNING,
            interval_s=120,
        )
        return {"ok": False, "error": "ensure_schema_failed"}

    stats = {
        "ok": True,
        "episodes": 0,
        "events": 0,
        "edges_upsert": 0,
        "last_episode_ts": 0,
    }

    with sql_manager.get_conn() as conn:
        since_ts = _get_last_checkpoint_ts(conn)
        if min_ep_gap > 0 and since_ts > 0:
            # optional backfill guard: if checkpoint is very old, allow operator to stop
            pass

        episodes = _fetch_episodes(conn, since_ts, max_eps)
        if not episodes:
            return {**stats, "episodes": 0, "last_episode_ts": since_ts}

        now = int(time.time())
        for ep in episodes:
            ep_id = int(ep["id"])
            ep_ts = int(ep.get("ts_start") or 0)
            if min_ep_gap > 0 and ep_ts < (now - min_ep_gap):
                # older than allowed window → stop early
                continue

            scope = (ep.get("source") or ep.get("kind") or "unknown")
            evs = _fetch_episode_events(conn, ep_id, ev_cap)
            stats["episodes"] += 1
            stats["events"] += len(evs)
            stats["last_episode_ts"] = ep_ts

            # Pre-create nodes
            node_ids: List[int] = []
            for ev in evs:
                ev_id = int(ev["id"])
                meta = {
                    "episode_id": ep_id,
                    "event_id": ev_id,
                    "event_type": ev.get("event_type"),
                    "ref_table": ev.get("ref_table"),
                    "ref_id": ev.get("ref_id"),
                    "ts": int(ev.get("ts") or 0),
                    "scope": scope,
                }
                try:
                    nid = sql_manager.ensure_event_object_node(ev_id, meta=meta)
                    node_ids.append(int(nid))
                except Exception as e:
                    log_suppressed(
                        LOG,
                        key="nmr_synapses.ensure_event_node",
                        msg=f"NMR synapses: ensure_event_object_node failed (event_id={ev_id})",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=120,
                    )

            # Pair updates (window)
            m = len(node_ids)
            for i in range(m):
                for j in range(i + 1, min(m, i + 1 + max(1, window))):
                    a = int(node_ids[i])
                    b = int(node_ids[j])
                    co = _cooc_inc(j - i)

                    # Existing hebb is stored in notes; we need to apply decay before adding.
                    # We do decay in Python by reading current notes first (select), then
                    # pass the delta to sql_manager.upsert_synaptic_relation.
                    try:
                        # read existing
                        cur = conn.execute(
                            """
                            SELECT id, ts, notes
                              FROM object_relations
                             WHERE a_id = ?
                               AND relation = 'synaptic'
                               AND b_id = ?
                             LIMIT 1
                            """,
                            (a, b),
                        )
                        row = cur.fetchone()
                        if row is None:
                            hebb_new = lr * co
                            w = _norm_from_hebb(hebb_new)
                            sql_manager.upsert_synaptic_relation(
                                a_id=a,
                                b_id=b,
                                weight=w,
                                hebb_delta=hebb_new,
                                cooc_inc=co,
                                scope=scope,
                                half_life_sec=half_life_sec,
                                ts=now,
                            )
                            stats["edges_upsert"] += 1
                        else:
                            prev_ts = int(row["ts"] or now)
                            dt = max(0, now - prev_ts)
                            prev_notes_raw = row["notes"]
                            try:
                                prev_notes = json.loads(prev_notes_raw) if prev_notes_raw else {}
                                if not isinstance(prev_notes, dict):
                                    prev_notes = {"_raw": prev_notes_raw}
                            except Exception:
                                prev_notes = {"_raw": prev_notes_raw}
                            prev_hebb = float(prev_notes.get("hebb") or 0.0)
                            dec = _decay_factor(dt, float(half_life_sec))
                            hebb_decayed = prev_hebb * dec
                            hebb_delta = (lr * co)
                            hebb_new = hebb_decayed + hebb_delta
                            w = _norm_from_hebb(hebb_new)

                            # We store only delta, sql_manager merges + updates.
                            # To keep hebb correct, we pass (hebb_new - prev_hebb) as delta.
                            sql_manager.upsert_synaptic_relation(
                                a_id=a,
                                b_id=b,
                                weight=w,
                                hebb_delta=(hebb_new - prev_hebb),
                                cooc_inc=co,
                                scope=scope,
                                half_life_sec=half_life_sec,
                                ts=now,
                            )
                            stats["edges_upsert"] += 1
                    except Exception as e:
                        log_suppressed(
                            LOG,
                            key="nmr_synapses.upsert_edge",
                            msg="NMR synapses: upsert edge failed",
                            exc=e,
                            level=logging.WARNING,
                            interval_s=120,
                        )

        # Checkpoint after loop
        if stats["last_episode_ts"] > 0:
            try:
                _set_checkpoint_ts(conn, int(stats["last_episode_ts"]))
            except Exception:
                pass

    return stats

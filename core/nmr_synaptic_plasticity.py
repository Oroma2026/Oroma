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
#   Kontext-Anker (Stage A, produktiv-vorsichtig, optional):
#   OROMA_NMR_SYN_CONTEXT_ENABLE
#   OROMA_NMR_SYN_CONTEXT_MAX_EDGES_PER_RUN
#   OROMA_NMR_SYN_CONTEXT_WEIGHT
#   OROMA_NMR_SYN_CONTEXT_EVENT_STRIDE
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_SCOPE
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_REF
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_EPISODE
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_SCOPE_EVENT_TYPE
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_TIME_BUCKET
#   OROMA_NMR_SYN_CONTEXT_TIME_BUCKET_SEC
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_NEIGHBOR_TIME_BUCKET
#   OROMA_NMR_SYN_CONTEXT_NEIGHBOR_TIME_BUCKET_SPAN
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_EPISODE_SEQUENCE_BUCKET
#   OROMA_NMR_SYN_CONTEXT_EPISODE_BUCKET_SIZE
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_SNAPCHAIN_NEARBY_BUCKET
#   OROMA_NMR_SYN_CONTEXT_SNAPCHAIN_BUCKET_SIZE
#   OROMA_NMR_SYN_CONTEXT_ANCHOR_ORIGIN_TIME_BUCKET
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


def _safe_label_part(value: Any, default: str = "unknown", max_len: int = 96) -> str:
    """
    Normalisiert kurze Label-Bestandteile für Context-Anker.

    Warum konservativ?
    - Context-Anker werden zu stabilen object_nodes-Labels.
    - Labels dürfen keine langen JSON-Blobs oder Zeilenumbrüche enthalten.
    - Wir behalten '/' und ':' bewusst, weil ORÓMA-Namespaces wie "vision/token"
      dadurch lesbar bleiben.
    """
    raw = str(value if value is not None else default).strip()
    if not raw:
        raw = str(default)
    out = []
    for ch in raw:
        if ch.isalnum() or ch in ("_", "-", ".", "/", ":"):
            out.append(ch)
        elif ch.isspace():
            out.append("_")
        else:
            out.append("_")
    label = "".join(out).strip("_") or str(default)
    return label[: max(8, int(max_len))]


def _parse_event_meta(meta_json: Any) -> Dict[str, Any]:
    """Robustes JSON-Parsing für episode_events.meta_json."""
    if not meta_json:
        return {}
    if isinstance(meta_json, dict):
        return dict(meta_json)
    try:
        data = json.loads(str(meta_json))
        return data if isinstance(data, dict) else {"_raw": str(meta_json)}
    except Exception:
        return {"_raw": str(meta_json)}


def _event_timestamp(ev: Dict[str, Any], ev_meta: Dict[str, Any], fallback_ts: int = 0) -> int:
    """Robust event timestamp extraction for medium-granularity anchors."""
    for key in ("ts", "timestamp", "created_ts", "last_ts", "first_ts"):
        value = ev.get(key) if key in ev else ev_meta.get(key)
        try:
            if value not in (None, "", 0, "0"):
                return int(float(value))
        except Exception:
            continue
    return int(fallback_ts or time.time())


def _time_bucket_label(ts: int, bucket_sec: int) -> Tuple[str, Dict[str, Any]]:
    """Return a stable UTC time-bucket label and metadata."""
    bucket_sec = max(60, int(bucket_sec or 3600))
    start = (int(ts) // bucket_sec) * bucket_sec
    # UTC label prevents timezone-dependent node labels across hosts.
    label = time.strftime("%Y%m%dT%H%MZ", time.gmtime(start))
    return f"time_bucket:{bucket_sec}:{label}", {"bucket_sec": bucket_sec, "bucket_start_ts": start, "bucket_label_utc": label}


def _bucket_start(value: int, bucket_size: int) -> int:
    """Return deterministic integer bucket start for medium-granularity anchors."""
    bucket_size = max(1, int(bucket_size or 1))
    return (int(value) // bucket_size) * bucket_size


def _safe_int(value: Any, default: int = 0) -> int:
    """Robust int conversion for optional event/ref fields."""
    try:
        if value in (None, "", "0"):
            return int(default)
        return int(float(value))
    except Exception:
        return int(default)


def _context_anchor_specs(
    scope: str,
    ev: Dict[str, Any],
    ev_meta: Dict[str, Any],
    *,
    episode_id: int = 0,
    episode_ts: int = 0,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    Liefert konservative Context-Anker für ein Episode-Event.

    Stage A/A2/A3 materialisiert noch keine neuen "synaptic"-Kanten. Stattdessen
    werden separate relation='synaptic_context'-Kanten auf stabile Kontextknoten
    erzeugt. A2 ergänzt mittlere Anker zwischen zu-generisch (scope/event_type)
    und zu-spezifisch (ref:snapchains:<id>): episode, scope_event_type und
    time_bucket. A3 ergänzt streng begrenzte Nachbarschaftsanker
    (neighbor_time_bucket, episode_sequence_bucket, snapchain_nearby_bucket,
    origin_time_bucket), um mehrere lokale Inseln messbar, aber weiterhin
    reversibel/prüfbar, miteinander vergleichbar zu machen.
    """
    specs: List[Tuple[str, str, Dict[str, Any]]] = []

    scope_label = _safe_label_part(scope or ev_meta.get("origin") or "unknown")
    event_type = _safe_label_part(ev.get("event_type") or ev_meta.get("event") or "event")
    origin = _safe_label_part(ev_meta.get("origin") or scope or "unknown")
    ev_ts = _event_timestamp(ev, ev_meta, fallback_ts=int(episode_ts or 0))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_SCOPE", True):
        specs.append((
            "context",
            f"scope:{scope_label}",
            {
                "source": "nmr_synaptic_context",
                "anchor": "scope",
                "scope": str(scope or "unknown"),
                "origin": str(ev_meta.get("origin") or ""),
                "event_type": str(ev.get("event_type") or ""),
            },
        ))
        specs.append((
            "context",
            f"event_type:{event_type}",
            {
                "source": "nmr_synaptic_context",
                "anchor": "event_type",
                "scope": str(scope or "unknown"),
                "event_type": str(ev.get("event_type") or ""),
            },
        ))
        if origin and origin != scope_label:
            specs.append((
                "context",
                f"origin:{origin}",
                {
                    "source": "nmr_synaptic_context",
                    "anchor": "origin",
                    "scope": str(scope or "unknown"),
                    "origin": str(ev_meta.get("origin") or ""),
                },
            ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_REF", True):
        ref_table = _safe_label_part(ev.get("ref_table") or "")
        ref_id = ev.get("ref_id")
        if ref_table and ref_id not in (None, "", 0, "0"):
            specs.append((
                "context",
                f"ref:{ref_table}:{int(ref_id)}" if str(ref_id).isdigit() else f"ref:{ref_table}:{_safe_label_part(ref_id)}",
                {
                    "source": "nmr_synaptic_context",
                    "anchor": "ref",
                    "scope": str(scope or "unknown"),
                    "ref_table": str(ev.get("ref_table") or ""),
                    "ref_id": ev.get("ref_id"),
                    "event_type": str(ev.get("event_type") or ""),
                },
            ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_EPISODE", True) and int(episode_id or 0) > 0:
        specs.append((
            "context",
            f"episode:{int(episode_id)}",
            {
                "source": "nmr_synaptic_context",
                "anchor": "episode",
                "episode_id": int(episode_id),
                "episode_ts": int(episode_ts or 0),
                "scope": str(scope or "unknown"),
                "event_type": str(ev.get("event_type") or ""),
            },
        ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_SCOPE_EVENT_TYPE", True):
        specs.append((
            "context",
            f"scope_event_type:{scope_label}:{event_type}",
            {
                "source": "nmr_synaptic_context",
                "anchor": "scope_event_type",
                "scope": str(scope or "unknown"),
                "event_type": str(ev.get("event_type") or ""),
            },
        ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_TIME_BUCKET", True):
        bucket_sec = _env_int("OROMA_NMR_SYN_CONTEXT_TIME_BUCKET_SEC", 3600)
        label, bucket_meta = _time_bucket_label(ev_ts, bucket_sec)
        specs.append((
            "context",
            label,
            {
                "source": "nmr_synaptic_context",
                "anchor": "time_bucket",
                "scope": str(scope or "unknown"),
                "event_type": str(ev.get("event_type") or ""),
                "event_ts": int(ev_ts),
                **bucket_meta,
            },
        ))

    # Stage A3: mittlere Nachbarschaftsanker. Diese sind bewusst stärker als
    # scope/event_type, aber schwächer als direkte semantische Beweise. Sie
    # erzeugen nur relation='synaptic_context' und dienen Bridge-Probe/Materializer
    # als messbare Evidenzschicht, niemals als automatische Backbone-Kante.
    neighbor_bucket_sec = _env_int("OROMA_NMR_SYN_CONTEXT_TIME_BUCKET_SEC", 3600)
    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_NEIGHBOR_TIME_BUCKET", True):
        span = max(0, min(3, _env_int("OROMA_NMR_SYN_CONTEXT_NEIGHBOR_TIME_BUCKET_SPAN", 1)))
        bucket_sec = max(60, int(neighbor_bucket_sec or 3600))
        base_start = (int(ev_ts) // bucket_sec) * bucket_sec
        for offset in range(-span, span + 1):
            start = base_start + (offset * bucket_sec)
            if start <= 0:
                continue
            label_utc = time.strftime("%Y%m%dT%H%MZ", time.gmtime(start))
            specs.append((
                "context",
                f"neighbor_time_bucket:{bucket_sec}:{label_utc}",
                {
                    "source": "nmr_synaptic_context",
                    "anchor": "neighbor_time_bucket",
                    "scope": str(scope or "unknown"),
                    "event_type": str(ev.get("event_type") or ""),
                    "event_ts": int(ev_ts),
                    "bucket_sec": int(bucket_sec),
                    "bucket_start_ts": int(start),
                    "bucket_label_utc": str(label_utc),
                    "neighbor_offset": int(offset),
                },
            ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_EPISODE_SEQUENCE_BUCKET", True) and int(episode_id or 0) > 0:
        ep_bucket_size = max(2, _env_int("OROMA_NMR_SYN_CONTEXT_EPISODE_BUCKET_SIZE", 10))
        ep_start = _bucket_start(int(episode_id), ep_bucket_size)
        specs.append((
            "context",
            f"episode_sequence_bucket:{ep_bucket_size}:{ep_start}",
            {
                "source": "nmr_synaptic_context",
                "anchor": "episode_sequence_bucket",
                "episode_id": int(episode_id),
                "episode_bucket_size": int(ep_bucket_size),
                "episode_bucket_start": int(ep_start),
                "scope": str(scope or "unknown"),
                "event_type": str(ev.get("event_type") or ""),
            },
        ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_SNAPCHAIN_NEARBY_BUCKET", True):
        ref_table_raw = str(ev.get("ref_table") or "")
        ref_table = _safe_label_part(ref_table_raw)
        ref_id_int = _safe_int(ev.get("ref_id"), 0)
        if ref_table and ref_id_int > 0:
            snap_bucket_size = max(10, _env_int("OROMA_NMR_SYN_CONTEXT_SNAPCHAIN_BUCKET_SIZE", 500))
            ref_start = _bucket_start(ref_id_int, snap_bucket_size)
            specs.append((
                "context",
                f"snapchain_nearby_bucket:{ref_table}:{snap_bucket_size}:{ref_start}",
                {
                    "source": "nmr_synaptic_context",
                    "anchor": "snapchain_nearby_bucket",
                    "scope": str(scope or "unknown"),
                    "event_type": str(ev.get("event_type") or ""),
                    "ref_table": ref_table_raw,
                    "ref_id": ref_id_int,
                    "snapchain_bucket_size": int(snap_bucket_size),
                    "snapchain_bucket_start": int(ref_start),
                },
            ))

    if _env_bool("OROMA_NMR_SYN_CONTEXT_ANCHOR_ORIGIN_TIME_BUCKET", True):
        bucket_sec = max(60, int(neighbor_bucket_sec or 3600))
        start = (int(ev_ts) // bucket_sec) * bucket_sec
        label_utc = time.strftime("%Y%m%dT%H%MZ", time.gmtime(start))
        specs.append((
            "context",
            f"origin_time_bucket:{origin}:{bucket_sec}:{label_utc}",
            {
                "source": "nmr_synaptic_context",
                "anchor": "origin_time_bucket",
                "origin": str(ev_meta.get("origin") or scope or "unknown"),
                "scope": str(scope or "unknown"),
                "event_type": str(ev.get("event_type") or ""),
                "event_ts": int(ev_ts),
                "bucket_sec": int(bucket_sec),
                "bucket_start_ts": int(start),
                "bucket_label_utc": str(label_utc),
            },
        ))

    return specs


def _object_relation_exists(conn: sqlite3.Connection, a_id: int, relation: str, b_id: int) -> bool:
    """
    Prüft read-only, ob eine Object-Relation bereits existiert.

    Zweck im NMR-Kontextanker-Pfad:
    - sql_manager.insert_object_relation() ist idempotent und gibt bei Duplikaten die
      bestehende ID zurück. Das ist korrekt, aber für Coverage-Backfills würde ein
      wiederholtes Upsert sonst weiterhin Budget verbrauchen.
    - Dieser schnelle Read verhindert, dass bekannte synaptic_context-Kanten das
      pro Lauf begrenzte Kontextbudget aufbrauchen.

    Es wird hier bewusst nicht geschrieben. Alle Writes bleiben weiterhin auf dem
    bestehenden sql_manager/DBWriter-kompatiblen Pfad.
    """
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM object_relations
             WHERE a_id = ?
               AND relation = ?
               AND b_id = ?
             LIMIT 1
            """,
            (int(a_id), str(relation), int(b_id)),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _insert_synaptic_context_relation(
    event_node_id: int,
    context_node_id: int,
    *,
    confidence: float,
    source_scene_id: Optional[int],
    ts: int,
    notes: Dict[str, Any],
) -> int:
    """
    Fügt eine relation='synaptic_context' ein.

    Bewusst keine relation='synaptic': Die Stage ist kontrolliert und separiert,
    damit Bridge- und Origin-Probes den neuen Kontext erst messen können, bevor
    spätere Stages daraus echte Backbone-Brücken ableiten.
    """
    return sql_manager.insert_object_relation(
        a_id=int(event_node_id),
        b_id=int(context_node_id),
        relation="synaptic_context",
        confidence=float(max(0.0, min(1.0, confidence))),
        source_scene_id=source_scene_id,
        ts=int(ts),
        notes=dict(notes),
    )


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

    context_enable = _env_bool("OROMA_NMR_SYN_CONTEXT_ENABLE", True)
    context_max_edges = max(0, _env_int("OROMA_NMR_SYN_CONTEXT_MAX_EDGES_PER_RUN", 250))
    context_weight = _env_float("OROMA_NMR_SYN_CONTEXT_WEIGHT", 0.18)
    context_event_stride = max(1, _env_int("OROMA_NMR_SYN_CONTEXT_EVENT_STRIDE", 3))

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
        "context_enabled": bool(context_enable),
        "context_edges_upsert": 0,
        "context_nodes_ensured": 0,
        "context_edges_existing": 0,
        "context_edges_skipped_budget": 0,
        "context_errors": 0,
        "context_anchor_scope": 0,
        "context_anchor_event_type": 0,
        "context_anchor_ref": 0,
        "context_anchor_episode": 0,
        "context_anchor_scope_event_type": 0,
        "context_anchor_time_bucket": 0,
        "context_anchor_neighbor_time_bucket": 0,
        "context_anchor_episode_sequence_bucket": 0,
        "context_anchor_snapchain_nearby_bucket": 0,
        "context_anchor_origin_time_bucket": 0,
        "context_anchor_other": 0,
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
            node_id_by_event_id: Dict[int, int] = {}
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
                    node_id_by_event_id[int(ev_id)] = int(nid)
                except Exception as e:
                    log_suppressed(
                        LOG,
                        key="nmr_synapses.ensure_event_node",
                        msg=f"NMR synapses: ensure_event_object_node failed (event_id={ev_id})",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=120,
                    )

            # Context-Anker Stage A (separat als relation='synaptic_context').
            # Wichtig: Diese Kanten verändern den bestehenden relation='synaptic' Backbone
            # nicht. Sie liefern nur messbaren Kontext, aus dem spätere Bridge-Stages
            # vorsichtig echte Brücken ableiten können.
            if context_enable and context_max_edges > 0:
                for idx, ev in enumerate(evs):
                    if idx % context_event_stride != 0:
                        continue
                    if stats["context_edges_upsert"] >= context_max_edges:
                        stats["context_edges_skipped_budget"] += 1
                        break
                    ev_id_for_context = int(ev.get("id") or 0)
                    event_node_id = int(node_id_by_event_id.get(ev_id_for_context, 0))
                    if event_node_id <= 0:
                        continue
                    ev_meta = _parse_event_meta(ev.get("meta_json"))
                    source_scene_id: Optional[int] = None
                    raw_scene = ev_meta.get("source_scene_id") or ev_meta.get("scene_id") or ev_meta.get("scenegraph_id")
                    try:
                        if raw_scene not in (None, "", 0, "0"):
                            source_scene_id = int(raw_scene)
                    except Exception:
                        source_scene_id = None

                    for kind, label, meta in _context_anchor_specs(scope, ev, ev_meta, episode_id=int(ep_id), episode_ts=int(ep_ts or 0)):
                        if stats["context_edges_upsert"] >= context_max_edges:
                            stats["context_edges_skipped_budget"] += 1
                            break
                        try:
                            ctx_id = sql_manager.ensure_object_node(kind=kind, label=label, meta=meta)
                            stats["context_nodes_ensured"] += 1

                            # Coverage-Hotfix:
                            # Wiederholte NMR-Läufe sehen oft dieselben Episoden und Events.
                            # insert_object_relation() ist idempotent, würde aber bei bereits
                            # vorhandenen Kontextkanten trotzdem als erfolgreicher Upsert im
                            # lokalen Budget gezählt. Dadurch bleibt die Kontextabdeckung stehen.
                            # Bereits existierende synaptic_context-Kanten werden daher gelesen,
                            # gezählt und ohne Budgetverbrauch übersprungen.
                            if _object_relation_exists(conn, int(event_node_id), "synaptic_context", int(ctx_id)):
                                stats["context_edges_existing"] += 1
                                continue

                            _insert_synaptic_context_relation(
                                event_node_id,
                                int(ctx_id),
                                confidence=context_weight,
                                source_scene_id=source_scene_id,
                                ts=now,
                                notes={
                                    "source": "nmr_synaptic_context",
                                    "stage": "A_context_anchor",
                                    "scope": str(scope or "unknown"),
                                    "event_id": int(ev.get("id") or 0),
                                    "episode_id": int(ep_id),
                                    "event_type": str(ev.get("event_type") or ""),
                                    "ref_table": str(ev.get("ref_table") or ""),
                                    "ref_id": ev.get("ref_id"),
                                    "anchor_label": str(label),
                                    "anchor_kind": str(kind),
                                },
                            )
                            anchor_name = str(meta.get("anchor") or "other")
                            stat_key = "context_anchor_" + anchor_name
                            if stat_key in stats:
                                stats[stat_key] += 1
                            else:
                                stats["context_anchor_other"] += 1
                            stats["context_edges_upsert"] += 1
                        except Exception as e:
                            stats["context_errors"] += 1
                            log_suppressed(
                                LOG,
                                key="nmr_synapses.context_anchor",
                                msg="NMR synapses: context anchor upsert failed",
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

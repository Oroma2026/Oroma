#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/nmr_binding_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     NMR Phase 2.0a/2.0b – Binding-Probe + History-Review, messend / nicht materialisierend
# Version:   v1.3.0
# Stand:     2026-06-07
# Autor:     ORÓMA · Jörg Werner + OpenAI GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Tool ist der erste praktische Schritt der sensor-unabhängigen NMR-Phase 2.
# Es prüft, ob vorhandene Snap-/SnapChain-Daten wiederkehrende, zeitlich nahe und
# durch NMR-Lite verstärkte Binding-Kandidaten enthalten.
#
# WICHTIGER ARCHITEKTURGRUNDSATZ
# ──────────────────────────────
# Binding wird NICHT ausgelöst.
# Binding wird angesammelt.
# Binding ist kein Einzelereignis, sondern eine vom Gedächtnis bestätigte Beziehung.
#
# Deshalb ist diese Datei absichtlich eine reine Probe:
#   - liest vorhandene snapchains und nmr:* Metriken
#   - bildet Co-Aktivierungs-/Nähe-Kandidaten
#   - aligniert Kandidaten mit lokalen NMR-Lite-Zeitfenstern
#   - berechnet nachvollziehbare Binding-Scores
#   - schreibt nur Diagnose-Metriken, eine State-Datei und optional eine History-JSONL
#   - vergleicht Top-Kandidaten mit früheren Probe-Läufen, um Wiederkehr sichtbar zu machen
#   - schreibt KEINE object_nodes und KEINE object_relations
#   - legt KEINE neuen Tabellen an und verändert KEIN Schema
#
# WARUM SNAP/SNAPCHAIN UND KEIN NEUES DATENMODELL?
# ───────────────────────────────────────────────
# ORÓMA besitzt mit Snap, SnapPattern, SnapChain, object_relations, DreamWorker und
# Replay bereits die universellen Gedächtnisbausteine. Ein separates
# ObservationAtom-Persistenzmodell würde Doppelarbeit erzeugen. Diese Probe liest
# daher SnapChains als Snap-kompatible Observation-Einheiten und behandelt die
# NMR-Lite-Metriken als Resonanz-/Aufmerksamkeitsverstärker.
#
# SCORE-MODELL
# ────────────
# Die Probe trennt Basis-Evidenz und NMR-Verstärkung:
#
#   Basis (max. 0.70):
#     repeat_score       * 0.35
#   + temporal_score     * 0.20
#   + context_score      * 0.15
#
#   NMR-Verstärker (max. 0.30):
#   + binding_hint_score * 0.15
#   + priority_score     * 0.08
#   + pe_ema_score       * 0.05
#   + confidence_score   * 0.02
#
# NMR ist Verstärker, nicht Entscheider. Ohne Wiederholung entsteht kein starker
# Kandidat. Einzelne Surprise-/PE-Momente dürfen keine falschen Bindungen erzeugen.
#
# NMR-DICHTE / SPARSE-GUARD
# ─────────────────────────
# Bei OROMA_NMR_PERSIST_WINDOW_SEC=30 können kleine Zeitfenster wie pre=5/post=10
# zu dünn sein. Die Probe darf dann NICHT still in eine reine Co-Occurrence-Probe
# degradieren. Sie zählt deshalb explizit:
#   - nmr_aligned_candidate_count   Kandidaten mit NMR-Daten im Fenster
#   - nmr_boosted_candidate_count   Kandidaten mit messbarer NMR-Verstärkung
#   - nmr_sparse_window_count       Kandidaten mit zu wenigen NMR-Punkten
#   - nmr_missing_window_count      Kandidaten ohne NMR-Punkte
#
# OUTPUT
# ──────
# 1) State-Datei:
#      /opt/ai/oroma/data/state/nmr_binding_probe_state.json
#
# 2) optionale History-Datei:
#      /opt/ai/oroma/data/state/nmr_binding_probe_history.jsonl
#
# 3) Metriken via DBWriter in oroma.db.metrics:
#      nmr:binding_probe:raw_pair_key_count
#      nmr:binding_probe:candidates
#      nmr:binding_probe:nmr_aligned_candidates
#      nmr:binding_probe:nmr_boosted_candidates
#      nmr:binding_probe:nmr_sparse_windows
#      nmr:binding_probe:nmr_missing_windows
#      nmr:binding_probe:weak_candidates
#      nmr:binding_probe:strong_candidates
#      nmr:binding_probe:avg_score
#      nmr:binding_probe:max_score
#      nmr:binding_probe:materialized
#      nmr:binding_probe:history_seen_before_top
#      nmr:binding_probe:history_recurring_weak_top
#
# 4) optionaler Review-State für Phase 2.0b:
#      /opt/ai/oroma/data/state/nmr_binding_probe_review_state.json
#
#    Review-Metriken:
#      nmr:binding_probe_review:history_lines
#      nmr:binding_probe_review:pair_count
#      nmr:binding_probe_review:stable_candidates
#      nmr:binding_probe_review:recurring_weak_candidates
#      nmr:binding_probe_review:max_seen_count
#      nmr:binding_probe_review:max_score
#      nmr:binding_probe_review:materialized
#
# DB-/LOCK-DISZIPLIN
# ──────────────────
# - Reads: SQLite read-only URI auf oroma.db, Connections werden immer geschlossen.
# - Writes: ausschließlich DBWriter, kein lokaler SQLite-Write-Fallback.
# - Keine object_relations- oder object_nodes-Writes in dieser Phase.
# - State-JSON wird atomisch geschrieben.
#
# ENV
# ───
#   OROMA_BASE                                  Default: /opt/ai/oroma
#   OROMA_DATA_DIR                              Default: $OROMA_BASE/data
#   OROMA_DBW_ENABLE                            Muss für DB-Metriken aktiv sein
#   OROMA_DBW_SOCKET                            Default: $OROMA_DATA_DIR/state/db_writer.sock
#   OROMA_NMR_BINDING_PROBE_WINDOW_SEC          Default: 21600
#   OROMA_NMR_BINDING_PROBE_MAX_SNAPCHAINS      Default: 2000
#   OROMA_NMR_BINDING_PROBE_PAIR_WINDOW_SEC     Default: 20
#   OROMA_NMR_BINDING_PROBE_NMR_PRE_SEC         Default: 5
#   OROMA_NMR_BINDING_PROBE_NMR_POST_SEC        Default: 10
#   OROMA_NMR_BINDING_PROBE_MIN_NMR_ROWS        Default: 2
#   OROMA_NMR_BINDING_PROBE_MIN_REPEAT          Default: 3
#   OROMA_NMR_BINDING_PROBE_WEAK_SCORE          Default: 0.45
#   OROMA_NMR_BINDING_PROBE_STRONG_SCORE        Default: 0.65
#   OROMA_NMR_BINDING_PROBE_TOPK                Default: 25
#   OROMA_NMR_BINDING_PROBE_HISTORY_ENABLE      Default: 1
#   OROMA_NMR_BINDING_PROBE_HISTORY_MAX_LINES   Default: 1000
#   OROMA_NMR_BINDING_REVIEW_MIN_SEEN            Default: 3
#   OROMA_NMR_BINDING_REVIEW_TOPK                Default: $TOPK
#
# RUN
# ───
#   cd /opt/ai/oroma
#   sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma \
#     OROMA_DBW_ENABLE=1 \
#     python3 tools/nmr_binding_probe.py --once --window-sec 21600 --verbose
#
# Für reine lokale Syntax-/Smoke-Tests ohne DBWriter:
#   PYTHONPATH=. python3 tools/nmr_binding_probe.py --once --no-db-writes --window-sec 3600
#
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, DefaultDict, Deque, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Tool kann aus /opt/ai/oroma oder aus tools/ gestartet werden.
_BASE_FROM_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BASE_FROM_FILE not in sys.path:
    sys.path.insert(0, _BASE_FROM_FILE)

try:
    from core import db_writer_client as dbw  # type: ignore
except Exception:  # pragma: no cover - Importfehler wird später sichtbar gemeldet.
    dbw = None  # type: ignore


NMR_KEYS = (
    "nmr:binding_hint_score",
    "nmr:priority",
    "nmr:pe",
    "nmr:pe_ema",
    "nmr:confidence",
    "nmr:surprise",
)

METRIC_PREFIX = "nmr:binding_probe"


@dataclass
class Observation:
    """SnapChain als NMR-kompatible Observation-Sicht."""

    ref_id: int
    ts: int
    origin: str
    namespace: str
    kind: str
    key: str
    label: str
    quality: float
    notes: str


@dataclass
class PairOccurrence:
    """Eine konkrete zeitliche Co-Aktivierung zweier Observation-Keys."""

    ts_mid: int
    dt: int
    a_ref: int
    b_ref: int


@dataclass
class CandidateAgg:
    """Aggregat eines wiederkehrenden Binding-Kandidaten."""

    a_key: str
    b_key: str
    a_label: str
    b_label: str
    a_origin: str
    b_origin: str
    a_namespace: str
    b_namespace: str
    occurrences: List[PairOccurrence] = field(default_factory=list)

    def add(self, occ: PairOccurrence) -> None:
        self.occurrences.append(occ)


# -----------------------------------------------------------------------------
# ENV / Pfade / Hilfsfunktionen
# -----------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.environ.get(name, str(default))).strip()))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None else str(v)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _now_ts() -> int:
    return int(time.time())


def _base_dir() -> str:
    return os.path.abspath(_env_str("OROMA_BASE", _BASE_FROM_FILE))


def _data_dir() -> str:
    return os.path.abspath(_env_str("OROMA_DATA_DIR", os.path.join(_base_dir(), "data")))


def _oroma_db_path() -> str:
    return _env_str("OROMA_DB_PATH", os.path.join(_data_dir(), "oroma.db"))


def _state_path() -> str:
    return os.path.join(_data_dir(), "state", "nmr_binding_probe_state.json")


def _history_path() -> str:
    return os.path.join(_data_dir(), "state", "nmr_binding_probe_history.jsonl")


def _review_state_path() -> str:
    return os.path.join(_data_dir(), "state", "nmr_binding_probe_review_state.json")


def _clamp01(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if not math.isfinite(v):
            return float(default)
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v
    except Exception:
        return float(default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return float(default)


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _candidate_pair_id(a_ref: str, b_ref: str) -> str:
    # Stabiler, nicht-datenbankabhängiger Schlüssel für Verlaufsauswertung.
    # Die Reihenfolge ist bewusst kanonisiert, damit A↔B und B↔A identisch sind.
    left, right = sorted([str(a_ref or ""), str(b_ref or "")])
    return hashlib.sha1((left + "\0" + right).encode("utf-8", errors="replace")).hexdigest()[:20]


def _load_history_pair_ids(path: str, max_lines: int = 1000) -> Set[str]:
    ids: Set[str] = set()
    try:
        if not os.path.exists(path):
            return ids
        tail: Deque[str] = deque(maxlen=max(1, int(max_lines)))
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    tail.append(line)
        for line in tail:
            try:
                row = json.loads(line)
            except Exception:
                continue
            for c in row.get("top_candidates", []) or []:
                pid = str(c.get("pair_id") or "").strip()
                if pid:
                    ids.add(pid)
    except Exception:
        return ids
    return ids


def _append_history_jsonl(path: str, state: Dict[str, Any], max_lines: int = 1000) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": int(state.get("ts") or _now_ts()),
        "runtime_sec": state.get("runtime_sec"),
        "candidate_count": state.get("candidate_count"),
        "nmr_aligned_candidate_count": state.get("nmr_aligned_candidate_count"),
        "nmr_boosted_candidate_count": state.get("nmr_boosted_candidate_count"),
        "nmr_sparse_window_count": state.get("nmr_sparse_window_count"),
        "nmr_missing_window_count": state.get("nmr_missing_window_count"),
        "weak_candidate_count": state.get("weak_candidate_count"),
        "strong_candidate_count": state.get("strong_candidate_count"),
        "avg_score": state.get("avg_score"),
        "max_score": state.get("max_score"),
        "warning": state.get("warning"),
        "history": state.get("history", {}),
        "top_candidates": [
            {
                "pair_id": c.get("pair_id"),
                "a_ref": c.get("a_ref"),
                "b_ref": c.get("b_ref"),
                "a_origin": c.get("a_origin"),
                "b_origin": c.get("b_origin"),
                "repeat_count": c.get("repeat_count"),
                "binding_score": c.get("binding_score"),
                "decision": c.get("decision"),
            }
            for c in (state.get("top_candidates", []) or [])[:25]
        ],
    }
    old_lines: Deque[str] = deque(maxlen=max(1, int(max_lines) - 1))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        old_lines.append(line)
        except Exception:
            old_lines.clear()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for line in old_lines:
            f.write(line + "\n")
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _ro_conn() -> sqlite3.Connection:
    db = _oroma_db_path()
    uri = f"file:{db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def _dbw_available() -> bool:
    if dbw is None:
        return False
    try:
        if getattr(dbw, "enabled", lambda: False)() and getattr(dbw, "ping", lambda timeout_ms=500: False)(timeout_ms=800):
            return True
    except Exception:
        pass

    # Robuster Live-Fallback: Socket kann aktiv sein, obwohl OROMA_DBW_ENABLE im
    # Shell-Kontext nicht gesetzt wurde. Keine lokalen Writes, nur Ping-Prüfung.
    sock = _env_str("OROMA_DBW_SOCKET", os.path.join(_data_dir(), "state", "db_writer.sock"))
    if os.path.exists(sock):
        try:
            client_factory = getattr(dbw, "_client", None)
            if client_factory is not None:
                resp = client_factory().request(
                    op="ping",
                    timeout_ms=800,
                    expect="none",
                    tag="nmr_binding_probe.ping_fallback",
                )
                return bool(resp.get("ok"))
        except Exception:
            return False
    return False


def _require_dbw() -> None:
    if not _dbw_available():
        raise RuntimeError(
            "DBWriter required for metric writes. Set OROMA_DBW_ENABLE=1 and ensure db_writer.sock is available, "
            "or run with --no-db-writes for read-only smoke tests."
        )


def _write_metric(key: str, value: float, ts: int) -> int:
    """Schreibt eine oroma.metrics-Zeile ausschließlich über DBWriter."""
    _require_dbw()
    return int(
        dbw.exec_lastrowid(  # type: ignore[union-attr]
            "INSERT INTO metrics(key, ts, value) VALUES (?, ?, ?)",
            params=(str(key), int(ts), float(value)),
            tag="nmr.binding_probe.metric",
            priority="normal",
            timeout_ms=_env_int("OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM", 60000),
            db="oroma",
        )
    )


# -----------------------------------------------------------------------------
# SnapChain-Normalisierung
# -----------------------------------------------------------------------------


def _decode_blob(blob: Any) -> Dict[str, Any]:
    if blob is None:
        return {}
    try:
        if isinstance(blob, bytes):
            text = blob.decode("utf-8", errors="replace")
        elif isinstance(blob, memoryview):
            text = bytes(blob).decode("utf-8", errors="replace")
        else:
            text = str(blob)
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _vector_from_payload(payload: Dict[str, Any]) -> List[float]:
    for key in ("v", "vec", "features", "fp12", "embedding", "centroid"):
        v = payload.get(key)
        if isinstance(v, list):
            out: List[float] = []
            for x in v[:64]:
                out.append(_safe_float(x, 0.0))
            if out:
                return out
    return []


def _vector_fp(v: Sequence[float], dims: int = 16, decimals: int = 2) -> str:
    if not v:
        return "novec"
    dims = max(4, min(int(dims), 64))
    decimals = max(0, min(int(decimals), 4))
    fmt = "{:0." + str(decimals) + "f}"
    raw = ",".join(fmt.format(_safe_float(x, 0.0)) for x in list(v)[:dims])
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _short_text(x: Any, limit: int = 80) -> str:
    s = str(x or "").strip().replace("\n", " ").replace("\r", " ")
    return s[:limit]


def _observation_key(row: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[str, str, str]:
    origin = _short_text(row.get("origin") or payload.get("origin") or "unknown")
    namespace = _short_text(row.get("namespace") or payload.get("namespace") or origin or "unknown")
    kind = _short_text(payload.get("kind") or row.get("notes") or origin or "snapchain")
    vec = _vector_from_payload(payload)
    fp = _vector_fp(vec)

    # Grobe, wiederholungsfreundliche Quelle. ID/TS bewusst nicht im Key, damit
    # Wiederholung überhaupt messbar wird.
    key = f"origin={origin}|ns={namespace}|kind={kind}|fp={fp}"
    label = f"{origin}/{kind}/{fp}"
    return key, label, kind


def _fetch_observations(window_sec: int, max_snapchains: int, min_quality: float) -> List[Observation]:
    since = _now_ts() - int(window_sec)
    conn = _ro_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, ts, origin, namespace, quality, status, notes, blob
              FROM snapchains
             WHERE ts >= ?
               AND status = 'active'
             ORDER BY ts ASC
             LIMIT ?
            """,
            (int(since), int(max_snapchains)),
        ).fetchall()
    finally:
        conn.close()

    obs: List[Observation] = []
    for r in rows or []:
        d = dict(r)
        q = _safe_float(d.get("quality"), 0.0)
        if q < float(min_quality):
            continue
        payload = _decode_blob(d.get("blob"))
        key, label, kind = _observation_key(d, payload)
        try:
            obs.append(
                Observation(
                    ref_id=int(d.get("id")),
                    ts=int(d.get("ts")),
                    origin=_short_text(d.get("origin") or payload.get("origin") or "unknown"),
                    namespace=_short_text(d.get("namespace") or payload.get("namespace") or ""),
                    kind=kind,
                    key=key,
                    label=label,
                    quality=q,
                    notes=_short_text(d.get("notes") or ""),
                )
            )
        except Exception:
            continue
    return obs


# -----------------------------------------------------------------------------
# NMR-Metriken und Alignment
# -----------------------------------------------------------------------------


def _fetch_nmr_metrics(ts_min: int, ts_max: int) -> Dict[str, List[Tuple[int, float]]]:
    conn = _ro_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT key, ts, value
              FROM metrics
             WHERE ts >= ? AND ts <= ?
               AND key IN ({','.join('?' for _ in NMR_KEYS)})
             ORDER BY ts ASC
            """,
            (int(ts_min), int(ts_max), *NMR_KEYS),
        ).fetchall()
    finally:
        conn.close()

    out: Dict[str, List[Tuple[int, float]]] = {k: [] for k in NMR_KEYS}
    for r in rows or []:
        key = str(r["key"])
        out.setdefault(key, []).append((int(r["ts"]), _safe_float(r["value"], 0.0)))
    return out


def _values_in_window(series: Sequence[Tuple[int, float]], start_ts: int, end_ts: int) -> List[float]:
    return [float(v) for ts, v in series if int(start_ts) <= int(ts) <= int(end_ts)]


def _avg(values: Sequence[float], default: float = 0.0) -> float:
    if not values:
        return float(default)
    return float(sum(values) / max(1, len(values)))


def _align_nmr_for_occurrences(
    occurrences: Sequence[PairOccurrence],
    nmr: Dict[str, List[Tuple[int, float]]],
    pre_sec: int,
    post_sec: int,
    min_nmr_rows: int,
    pe_ema_scale: float,
) -> Dict[str, Any]:
    aligned = 0
    missing = 0
    sparse = 0
    total_windows = 0

    hint_values: List[float] = []
    priority_values: List[float] = []
    pe_values: List[float] = []
    pe_ema_values: List[float] = []
    confidence_values: List[float] = []
    surprise_values: List[float] = []

    for occ in occurrences:
        total_windows += 1
        start = int(occ.ts_mid) - int(pre_sec)
        end = int(occ.ts_mid) + int(post_sec)
        rows_in_any_metric = 0
        local: Dict[str, List[float]] = {}
        for key in NMR_KEYS:
            vals = _values_in_window(nmr.get(key, []), start, end)
            local[key] = vals
            rows_in_any_metric += len(vals)

        if rows_in_any_metric <= 0:
            missing += 1
            continue
        aligned += 1
        if rows_in_any_metric < int(min_nmr_rows):
            sparse += 1

        hint_values.append(_avg(local.get("nmr:binding_hint_score", []), 0.0))
        priority_values.append(_avg(local.get("nmr:priority", []), 0.0))
        pe_values.append(_avg(local.get("nmr:pe", []), 0.0))
        pe_ema_values.append(_avg(local.get("nmr:pe_ema", []), 0.0))
        confidence_values.append(_avg(local.get("nmr:confidence", []), 0.0))
        surprise_values.append(_avg(local.get("nmr:surprise", []), 0.0))

    hint_avg = _clamp01(_avg(hint_values, 0.0))
    priority_avg = _clamp01(_avg(priority_values, 0.0))
    pe_avg = max(0.0, _avg(pe_values, 0.0))
    pe_ema_avg = max(0.0, _avg(pe_ema_values, 0.0))
    confidence_avg = _clamp01(_avg(confidence_values, 0.0))
    surprise_avg = _clamp01(_avg(surprise_values, 0.0))
    pe_ema_score = _clamp01(pe_ema_avg * float(pe_ema_scale))

    nmr_component = (
        hint_avg * 0.15
        + priority_avg * 0.08
        + pe_ema_score * 0.05
        + confidence_avg * 0.02
    )

    return {
        "aligned_windows": int(aligned),
        "missing_windows": int(missing),
        "sparse_windows": int(sparse),
        "total_windows": int(total_windows),
        "nmr_binding_hint_score_avg": hint_avg,
        "nmr_priority_avg": priority_avg,
        "nmr_pe_avg": pe_avg,
        "nmr_pe_ema_avg": pe_ema_avg,
        "nmr_pe_ema_score": pe_ema_score,
        "nmr_confidence_avg": confidence_avg,
        "nmr_surprise_avg": surprise_avg,
        "nmr_component": _clamp01(nmr_component),
    }


# -----------------------------------------------------------------------------
# Candidate-Bildung und Scoring
# -----------------------------------------------------------------------------


def _context_score(a: CandidateAgg) -> float:
    # Minimaler, nachvollziehbarer Startwert: unterschiedliche Quellen sind für
    # Binding interessant, gleiche Namespace-/Origin-Kontexte stabilisieren aber
    # ebenfalls. Kein harter Crossmodal-Zwang.
    same_origin = a.a_origin == a.b_origin
    same_ns = bool(a.a_namespace and a.a_namespace == a.b_namespace)
    if same_ns:
        return 0.85
    if same_origin:
        return 0.65
    # Cross-source: potentiell bindungsrelevant, aber ohne weitere Semantik nicht
    # maximal stark.
    return 0.75


def _temporal_score(occurrences: Sequence[PairOccurrence], pair_window_sec: int) -> float:
    if not occurrences:
        return 0.0
    vals = []
    for occ in occurrences:
        vals.append(1.0 - min(1.0, max(0.0, float(occ.dt) / max(1.0, float(pair_window_sec)))))
    return _clamp01(_avg(vals, 0.0))


def _repeat_score(repeat_count: int, repeat_full: int) -> float:
    return _clamp01(float(repeat_count) / max(1.0, float(repeat_full)))


def _age_decay(last_ts: int, now_ts: int, window_sec: int, max_decay: float = 0.05) -> float:
    age = max(0.0, float(now_ts - int(last_ts)))
    return min(float(max_decay), (age / max(1.0, float(window_sec))) * float(max_decay))


def _noise_penalty(nmr_info: Dict[str, Any], repeated_count: int) -> float:
    total = max(1, int(nmr_info.get("total_windows") or 0))
    missing = int(nmr_info.get("missing_windows") or 0)
    sparse = int(nmr_info.get("sparse_windows") or 0)
    penalty = 0.0
    if missing:
        penalty += min(0.06, 0.06 * (missing / total))
    if sparse:
        penalty += min(0.03, 0.03 * (sparse / total))
    # Ein Kandidat knapp an der Mindestwiederholung bleibt vorsichtiger.
    if repeated_count <= 1:
        penalty += 0.02
    return min(0.12, penalty)


def _build_pair_candidates(observations: Sequence[Observation], pair_window_sec: int) -> Dict[Tuple[str, str], CandidateAgg]:
    candidates: Dict[Tuple[str, str], CandidateAgg] = {}
    obs = sorted(observations, key=lambda o: (o.ts, o.ref_id))
    n = len(obs)
    for i in range(n):
        a = obs[i]
        j = i + 1
        while j < n:
            b = obs[j]
            dt = int(b.ts - a.ts)
            if dt > int(pair_window_sec):
                break
            if a.key != b.key:
                if a.key < b.key:
                    key = (a.key, b.key)
                    aa, bb = a, b
                else:
                    key = (b.key, a.key)
                    aa, bb = b, a
                cand = candidates.get(key)
                if cand is None:
                    cand = CandidateAgg(
                        a_key=key[0],
                        b_key=key[1],
                        a_label=aa.label,
                        b_label=bb.label,
                        a_origin=aa.origin,
                        b_origin=bb.origin,
                        a_namespace=aa.namespace,
                        b_namespace=bb.namespace,
                    )
                    candidates[key] = cand
                cand.add(PairOccurrence(ts_mid=int((a.ts + b.ts) / 2), dt=abs(dt), a_ref=a.ref_id, b_ref=b.ref_id))
            j += 1
    return candidates


def _score_candidates(
    raw_candidates: Dict[Tuple[str, str], CandidateAgg],
    nmr: Dict[str, List[Tuple[int, float]]],
    args: argparse.Namespace,
) -> List[Dict[str, Any]]:
    now = _now_ts()
    scored: List[Dict[str, Any]] = []
    for cand in raw_candidates.values():
        repeat_count = len(cand.occurrences)
        if repeat_count < int(args.min_repeat):
            continue

        nmr_info = _align_nmr_for_occurrences(
            cand.occurrences,
            nmr,
            pre_sec=int(args.nmr_pre_sec),
            post_sec=int(args.nmr_post_sec),
            min_nmr_rows=int(args.min_nmr_rows),
            pe_ema_scale=float(args.pe_ema_scale),
        )
        r_score = _repeat_score(repeat_count, int(args.repeat_full))
        t_score = _temporal_score(cand.occurrences, int(args.pair_window_sec))
        c_score = _context_score(cand)
        base_component = r_score * 0.35 + t_score * 0.20 + c_score * 0.15
        nmr_component = float(nmr_info.get("nmr_component") or 0.0)
        last_ts = max((o.ts_mid for o in cand.occurrences), default=0)
        penalty = _noise_penalty(nmr_info, repeat_count)
        decay = _age_decay(last_ts, now, int(args.window_sec))
        binding_score = max(0.0, min(1.0, base_component + nmr_component - penalty - decay))

        decision = "candidate_probe_only"
        if binding_score >= float(args.strong_score):
            decision = "strong_candidate_probe_only"
        elif binding_score >= float(args.weak_score):
            decision = "weak_candidate_probe_only"

        pair_id = _candidate_pair_id(cand.a_label, cand.b_label)
        scored.append({
            "pair_id": pair_id,
            "a_ref": cand.a_label,
            "b_ref": cand.b_label,
            "a_origin": cand.a_origin,
            "b_origin": cand.b_origin,
            "a_namespace": cand.a_namespace,
            "b_namespace": cand.b_namespace,
            "repeat_count": int(repeat_count),
            "first_ts": min((o.ts_mid for o in cand.occurrences), default=0),
            "last_ts": int(last_ts),
            "example_pairs": [
                {"a_snapchain_id": int(o.a_ref), "b_snapchain_id": int(o.b_ref), "ts_mid": int(o.ts_mid), "dt": int(o.dt)}
                for o in cand.occurrences[:5]
            ],
            "repeat_score": round(r_score, 6),
            "temporal_score": round(t_score, 6),
            "context_score": round(c_score, 6),
            "base_component": round(base_component, 6),
            "nmr_component": round(nmr_component, 6),
            "noise_penalty": round(penalty, 6),
            "age_decay": round(decay, 6),
            "binding_score": round(binding_score, 6),
            "decision": decision,
            **{k: (round(v, 8) if isinstance(v, float) else v) for k, v in nmr_info.items()},
        })
    scored.sort(key=lambda x: (float(x.get("binding_score") or 0.0), int(x.get("repeat_count") or 0)), reverse=True)
    return scored


# -----------------------------------------------------------------------------
# Run / State / Metrics
# -----------------------------------------------------------------------------


def run_probe(args: argparse.Namespace) -> Dict[str, Any]:
    started = time.time()
    observations = _fetch_observations(
        window_sec=int(args.window_sec),
        max_snapchains=int(args.max_snapchains),
        min_quality=float(args.min_quality),
    )
    if observations:
        ts_min = min(o.ts for o in observations) - int(args.nmr_pre_sec)
        ts_max = max(o.ts for o in observations) + int(args.nmr_post_sec)
    else:
        now = _now_ts()
        ts_min = now - int(args.window_sec) - int(args.nmr_pre_sec)
        ts_max = now + int(args.nmr_post_sec)

    nmr = _fetch_nmr_metrics(ts_min, ts_max)
    raw_candidates = _build_pair_candidates(observations, int(args.pair_window_sec))
    scored = _score_candidates(raw_candidates, nmr, args)

    candidate_count = len(scored)
    nmr_aligned_candidate_count = sum(1 for c in scored if int(c.get("aligned_windows") or 0) > 0)
    nmr_boosted_candidate_count = sum(1 for c in scored if float(c.get("nmr_component") or 0.0) > float(args.nmr_boost_min))
    nmr_sparse_window_count = sum(1 for c in scored if int(c.get("sparse_windows") or 0) > 0)
    nmr_missing_window_count = sum(1 for c in scored if int(c.get("missing_windows") or 0) > 0)
    weak_candidate_count = sum(1 for c in scored if float(c.get("binding_score") or 0.0) >= float(args.weak_score))
    strong_candidate_count = sum(1 for c in scored if float(c.get("binding_score") or 0.0) >= float(args.strong_score))
    scores = [float(c.get("binding_score") or 0.0) for c in scored]

    warning: Optional[str] = None
    if candidate_count > 0 and nmr_aligned_candidate_count == 0:
        warning = "too_few_nmr_metrics_in_candidate_windows"
    elif candidate_count > 0 and (nmr_sparse_window_count + nmr_missing_window_count) >= max(1, candidate_count):
        warning = "nmr_metrics_sparse_or_missing_in_candidate_windows"

    nmr_metric_counts = {k: len(v) for k, v in nmr.items()}
    nmr_total_rows = sum(nmr_metric_counts.values())

    history_path = str(getattr(args, "history_path", _history_path()))
    history_enabled = bool(getattr(args, "history_enable", True))
    history_ids = _load_history_pair_ids(history_path, int(getattr(args, "history_max_lines", 1000))) if history_enabled else set()
    top_for_history = scored[: int(args.topk)]
    seen_before_top = sum(1 for c in top_for_history if str(c.get("pair_id") or "") in history_ids)
    recurring_weak_top = sum(
        1
        for c in top_for_history
        if str(c.get("pair_id") or "") in history_ids and float(c.get("binding_score") or 0.0) >= float(args.weak_score)
    )

    state: Dict[str, Any] = {
        "ok": True,
        "ts": _now_ts(),
        "runtime_sec": round(time.time() - started, 6),
        "mode": "probe_only",
        "source": "tools/nmr_binding_probe.py",
        "materialized_count": 0,
        "window_sec": int(args.window_sec),
        "pair_window_sec": int(args.pair_window_sec),
        "nmr_window": {
            "pre_sec": int(args.nmr_pre_sec),
            "post_sec": int(args.nmr_post_sec),
            "min_nmr_rows": int(args.min_nmr_rows),
            "metric_counts": nmr_metric_counts,
            "total_rows": int(nmr_total_rows),
        },
        "inputs": {
            "observation_count": int(len(observations)),
            "raw_pair_key_count": int(len(raw_candidates)),
            "oroma_db_path": _oroma_db_path(),
        },
        "candidate_count": int(candidate_count),
        "nmr_aligned_candidate_count": int(nmr_aligned_candidate_count),
        "nmr_boosted_candidate_count": int(nmr_boosted_candidate_count),
        "nmr_sparse_window_count": int(nmr_sparse_window_count),
        "nmr_missing_window_count": int(nmr_missing_window_count),
        "weak_candidate_count": int(weak_candidate_count),
        "strong_candidate_count": int(strong_candidate_count),
        "avg_score": round(_avg(scores, 0.0), 6),
        "max_score": round(max(scores) if scores else 0.0, 6),
        "warning": warning,
        "history": {
            "enabled": bool(history_enabled),
            "path": history_path,
            "known_pair_count": int(len(history_ids)),
            "seen_before_top_count": int(seen_before_top),
            "recurring_weak_top_count": int(recurring_weak_top),
            "max_lines": int(getattr(args, "history_max_lines", 1000)),
        },
        "thresholds": {
            "min_repeat": int(args.min_repeat),
            "repeat_full": int(args.repeat_full),
            "weak_score": float(args.weak_score),
            "strong_score": float(args.strong_score),
            "nmr_boost_min": float(args.nmr_boost_min),
        },
        "top_candidates": top_for_history,
    }
    return state



# -----------------------------------------------------------------------------
# Phase 2.0b: History Review / Stabilitätsauswertung
# -----------------------------------------------------------------------------


def _load_history_entries(path: str, max_lines: int = 1000) -> List[Dict[str, Any]]:
    """Lädt die letzten History-Einträge robust und read-only.

    Die History-Datei ist bewusst JSONL, damit periodische Probe-Läufe ohne neue
    Tabellen und ohne Schema-Änderungen einen Verlauf hinterlassen. Fehlerhafte
    oder unvollständige Zeilen werden sichtbar übersprungen, aber nicht gelöscht.
    """
    entries: List[Dict[str, Any]] = []
    try:
        if not os.path.exists(path):
            return entries
        tail: Deque[str] = deque(maxlen=max(1, int(max_lines)))
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    tail.append(line)
        for line in tail:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                entries.append(row)
    except Exception:
        return entries
    return entries


def _history_candidate_key(candidate: Dict[str, Any]) -> str:
    pid = str(candidate.get("pair_id") or "").strip()
    if pid:
        return pid
    return _candidate_pair_id(str(candidate.get("a_ref") or ""), str(candidate.get("b_ref") or ""))


def _origin_modality(origin: Any, ref: Any = "", namespace: Any = "") -> str:
    """Ordnet einen Kandidaten-Anker einer groben Modalitätsklasse zu.

    Diese Klassifikation ist absichtlich konservativ und explainable. Sie dient
    nur dem Review-Modus, damit audio-dominierte Kandidaten nicht die gesamte
    Top-Liste verdecken. Sie erzeugt keine Hypothesen und keine Relationen.
    """
    text = " ".join(str(x or "").lower() for x in (origin, ref, namespace))
    if "ptz" in text or "pan" in text or "tilt" in text or "zoom" in text:
        return "ptz"
    if "vision" in text or "camera" in text or "cam_token" in text or "image" in text:
        return "vision"
    if "audio" in text or "mic" in text or "sound" in text or "speech" in text:
        return "audio"
    if "game:" in text or "chess" in text or "tictactoe" in text or "connect4" in text or "snake" in text:
        return "game"
    if (
        "curriculum" in text
        or "calc" in text
        or "calculator" in text
        or "runtime" in text
        or "self" in text
        or "transfer" in text
        or "policy" in text
    ):
        return "internal"
    return "unknown"


def _pair_modality_class(a_modality: str, b_modality: str) -> str:
    """Kanonische Klasse für Review-Kandidaten.

    Die Klasse ist nicht semantisch endgültig. Sie ist eine Diagnose-Sicht:
    audio↔audio wird bewusst separat gehalten, während crossmodale und
    vision-/PTZ-nahe Paare sichtbar werden.
    """
    pair = sorted([str(a_modality or "unknown"), str(b_modality or "unknown")])
    a, b = pair[0], pair[1]
    if a == b == "audio":
        return "audio_audio"
    if a == b == "vision":
        return "vision_vision"
    if a == b == "ptz":
        return "ptz_ptz"
    if a == b == "game" or set(pair) <= {"game", "internal"}:
        return "game_internal"
    if pair == ["audio", "vision"]:
        return "audio_vision"
    if pair == ["ptz", "vision"]:
        return "ptz_vision"
    if pair == ["audio", "game"]:
        return "audio_game"
    if pair == ["game", "vision"]:
        return "game_vision"
    if pair == ["audio", "ptz"]:
        return "audio_ptz"
    if pair == ["internal", "vision"]:
        return "internal_vision"
    if pair == ["internal", "audio"]:
        return "audio_internal"
    if a != b:
        return "crossmodal_other"
    return f"{a}_{b}"


def _is_crossmodal_class(modality_class: str) -> bool:
    return modality_class in {
        "audio_vision",
        "ptz_vision",
        "audio_game",
        "game_vision",
        "audio_ptz",
        "internal_vision",
        "audio_internal",
        "crossmodal_other",
    }


def _is_vision_relevant_class(modality_class: str) -> bool:
    return modality_class in {"vision_vision", "audio_vision", "ptz_vision", "game_vision", "internal_vision"}


def _hypothesis_readiness(
    modality_class: str,
    seen: int,
    weak_seen: int,
    strong_seen: int,
    max_score: float,
    stability_score: float,
    min_seen: int,
    weak_score: float,
) -> Tuple[bool, str]:
    """Konservative Review-only Einschätzung für spätere Hypothesenfähigkeit.

    Hypothesis-ready bedeutet hier ausdrücklich NICHT, dass core/hypothesis.py
    beschrieben werden soll. Es ist nur ein Filter für Kandidaten, die in einer
    späteren Phase manuell/gezielt geprüft werden könnten.
    """
    if seen < min_seen:
        return False, "too_few_seen"
    if weak_seen < min_seen:
        return False, "too_few_weak_confirmations"
    if modality_class == "audio_audio":
        return False, "audio_audio_review_only"
    if modality_class in {"audio_game", "audio_internal"}:
        return False, "audio_internal_or_game_needs_separate_review"
    if modality_class in {"ptz_vision", "vision_vision", "audio_vision", "game_vision", "internal_vision"}:
        if stability_score >= 0.65 and (max_score >= max(0.60, weak_score) or strong_seen > 0):
            return True, "candidate_for_later_manual_hypothesis_review"
        return False, "vision_related_but_below_review_threshold"
    return False, "not_in_initial_hypothesis_class"


def _review_history(args: argparse.Namespace) -> Dict[str, Any]:
    """Aggregiert wiederkehrende Top-Kandidaten über mehrere Probe-Läufe.

    Diese Auswertung ist Phase 2.0b: Sie prüft Stabilität über Zeit, erzeugt aber
    keine Hypothesen und materialisiert keine object_relations. Der Zweck ist,
    nach der Beobachtungsphase zu entscheiden, welche Kandidaten überhaupt für
    einen späteren Hypothesis-Emit-Modus geeignet wären.
    """
    started = time.time()
    history_path = str(getattr(args, "history_path", _history_path()))
    max_lines = int(getattr(args, "history_max_lines", 1000))
    entries = _load_history_entries(history_path, max_lines=max_lines)
    min_seen = max(1, int(getattr(args, "review_min_seen", 3)))
    review_topk = max(1, int(getattr(args, "review_topk", getattr(args, "topk", 25))))
    weak_score = float(getattr(args, "weak_score", 0.45))
    strong_score = float(getattr(args, "strong_score", 0.65))

    agg: Dict[str, Dict[str, Any]] = {}
    run_ts_values: List[int] = []
    malformed_candidate_count = 0

    for entry_idx, entry in enumerate(entries):
        try:
            run_ts = int(entry.get("ts") or 0)
        except Exception:
            run_ts = 0
        if run_ts > 0:
            run_ts_values.append(run_ts)
        for c in entry.get("top_candidates", []) or []:
            if not isinstance(c, dict):
                malformed_candidate_count += 1
                continue
            key = _history_candidate_key(c)
            if not key:
                malformed_candidate_count += 1
                continue
            row = agg.get(key)
            if row is None:
                row = {
                    "pair_id": key,
                    "a_ref": c.get("a_ref"),
                    "b_ref": c.get("b_ref"),
                    "a_origin": c.get("a_origin"),
                    "b_origin": c.get("b_origin"),
                    "seen_count": 0,
                    "weak_seen_count": 0,
                    "strong_seen_count": 0,
                    "score_sum": 0.0,
                    "repeat_sum": 0.0,
                    "max_score": 0.0,
                    "last_score": 0.0,
                    "first_seen_ts": run_ts,
                    "last_seen_ts": run_ts,
                    "run_indices": [],
                    "decisions": {},
                }
                agg[key] = row
            score = _safe_float(c.get("binding_score"), 0.0)
            repeat = _safe_float(c.get("repeat_count"), 0.0)
            decision = str(c.get("decision") or "unknown")
            row["seen_count"] = int(row.get("seen_count") or 0) + 1
            row["score_sum"] = float(row.get("score_sum") or 0.0) + score
            row["repeat_sum"] = float(row.get("repeat_sum") or 0.0) + repeat
            row["max_score"] = max(float(row.get("max_score") or 0.0), score)
            row["last_score"] = score
            if score >= weak_score:
                row["weak_seen_count"] = int(row.get("weak_seen_count") or 0) + 1
            if score >= strong_score:
                row["strong_seen_count"] = int(row.get("strong_seen_count") or 0) + 1
            if run_ts > 0:
                first = int(row.get("first_seen_ts") or run_ts)
                last = int(row.get("last_seen_ts") or run_ts)
                row["first_seen_ts"] = min(first, run_ts)
                row["last_seen_ts"] = max(last, run_ts)
            row.setdefault("run_indices", []).append(int(entry_idx))
            decisions = row.setdefault("decisions", {})
            decisions[decision] = int(decisions.get(decision, 0)) + 1

    candidates: List[Dict[str, Any]] = []
    for row in agg.values():
        seen = max(1, int(row.get("seen_count") or 0))
        weak_seen = int(row.get("weak_seen_count") or 0)
        strong_seen = int(row.get("strong_seen_count") or 0)
        avg_score = float(row.get("score_sum") or 0.0) / float(seen)
        avg_repeat = float(row.get("repeat_sum") or 0.0) / float(seen)
        # Stabilität ist absichtlich konservativ: Wiederkehr zählt stärker als
        # ein einzelner hoher Score. Strong bleibt nur Hinweis, kein Write-Signal.
        stability_score = _clamp01(
            min(1.0, seen / max(1.0, float(min_seen) * 2.0)) * 0.35
            + min(1.0, weak_seen / max(1.0, float(min_seen))) * 0.30
            + _clamp01(avg_score) * 0.25
            + min(1.0, avg_repeat / 8.0) * 0.10
        )
        a_modality = _origin_modality(row.get("a_origin"), row.get("a_ref"))
        b_modality = _origin_modality(row.get("b_origin"), row.get("b_ref"))
        modality_class = _pair_modality_class(a_modality, b_modality)
        hyp_ready, hyp_reason = _hypothesis_readiness(
            modality_class=modality_class,
            seen=int(seen),
            weak_seen=int(weak_seen),
            strong_seen=int(strong_seen),
            max_score=float(row.get("max_score") or 0.0),
            stability_score=float(stability_score),
            min_seen=int(min_seen),
            weak_score=float(weak_score),
        )
        base_review_decision = (
            "stable_weak_candidate_review_only" if weak_seen >= min_seen else
            "seen_before_review_only" if seen >= min_seen else
            "insufficient_history_review_only"
        )
        if modality_class == "audio_audio" and weak_seen >= min_seen:
            base_review_decision = "stable_audio_audio_review_only"
        elif hyp_ready:
            base_review_decision = "hypothesis_ready_review_only"
        candidates.append({
            "pair_id": row.get("pair_id"),
            "a_ref": row.get("a_ref"),
            "b_ref": row.get("b_ref"),
            "a_origin": row.get("a_origin"),
            "b_origin": row.get("b_origin"),
            "a_modality": a_modality,
            "b_modality": b_modality,
            "modality_pair_class": modality_class,
            "is_crossmodal": bool(_is_crossmodal_class(modality_class)),
            "is_vision_relevant": bool(_is_vision_relevant_class(modality_class)),
            "hypothesis_ready": bool(hyp_ready),
            "hypothesis_ready_reason": hyp_reason,
            "seen_count": int(seen),
            "weak_seen_count": int(weak_seen),
            "strong_seen_count": int(strong_seen),
            "avg_score": round(avg_score, 6),
            "max_score": round(float(row.get("max_score") or 0.0), 6),
            "last_score": round(float(row.get("last_score") or 0.0), 6),
            "avg_repeat_count": round(avg_repeat, 3),
            "first_seen_ts": int(row.get("first_seen_ts") or 0),
            "last_seen_ts": int(row.get("last_seen_ts") or 0),
            "stability_score": round(stability_score, 6),
            "decisions": row.get("decisions") or {},
            "review_decision": base_review_decision,
        })

    candidates.sort(
        key=lambda c: (
            float(c.get("stability_score") or 0.0),
            int(c.get("weak_seen_count") or 0),
            float(c.get("max_score") or 0.0),
            int(c.get("seen_count") or 0),
        ),
        reverse=True,
    )

    stable_candidates = sum(1 for c in candidates if int(c.get("seen_count") or 0) >= min_seen)
    recurring_weak_candidates = sum(1 for c in candidates if int(c.get("weak_seen_count") or 0) >= min_seen)
    recurring_strong_candidates = sum(1 for c in candidates if int(c.get("strong_seen_count") or 0) >= min_seen)
    modality_class_summary: Dict[str, Dict[str, int]] = {}
    for c in candidates:
        cls = str(c.get("modality_pair_class") or "unknown_unknown")
        bucket = modality_class_summary.setdefault(cls, {"total": 0, "stable": 0, "weak": 0, "strong": 0, "hypothesis_ready": 0})
        bucket["total"] += 1
        if int(c.get("seen_count") or 0) >= min_seen:
            bucket["stable"] += 1
        if int(c.get("weak_seen_count") or 0) >= min_seen:
            bucket["weak"] += 1
        if int(c.get("strong_seen_count") or 0) >= min_seen:
            bucket["strong"] += 1
        if bool(c.get("hypothesis_ready")):
            bucket["hypothesis_ready"] += 1
    audio_audio_candidates = int(modality_class_summary.get("audio_audio", {}).get("total", 0))
    crossmodal_candidates = sum(1 for c in candidates if bool(c.get("is_crossmodal")))
    vision_relevant_candidates = sum(1 for c in candidates if bool(c.get("is_vision_relevant")))
    ptz_vision_candidates = int(modality_class_summary.get("ptz_vision", {}).get("total", 0))
    game_internal_candidates = int(modality_class_summary.get("game_internal", {}).get("total", 0))
    hypothesis_ready_candidates = sum(1 for c in candidates if bool(c.get("hypothesis_ready")))
    max_seen_count = max((int(c.get("seen_count") or 0) for c in candidates), default=0)
    max_score = max((float(c.get("max_score") or 0.0) for c in candidates), default=0.0)
    max_stability = max((float(c.get("stability_score") or 0.0) for c in candidates), default=0.0)

    warning: Optional[str] = None
    if not entries:
        warning = "history_missing_or_empty"
    elif len(entries) < min_seen:
        warning = "too_few_history_runs_for_stability_review"
    elif stable_candidates <= 0:
        warning = "no_stable_candidates_seen_enough_times"
    elif recurring_weak_candidates <= 0:
        warning = "stable_candidates_exist_but_not_weak_enough"

    state: Dict[str, Any] = {
        "ok": True,
        "ts": _now_ts(),
        "runtime_sec": round(time.time() - started, 6),
        "mode": "history_review_only",
        "source": "tools/nmr_binding_probe.py --review-history",
        "materialized_count": 0,
        "history_path": history_path,
        "review_state_path": str(getattr(args, "review_state_path", _review_state_path())),
        "history": {
            "lines_loaded": int(len(entries)),
            "max_lines": int(max_lines),
            "first_ts": min(run_ts_values) if run_ts_values else 0,
            "last_ts": max(run_ts_values) if run_ts_values else 0,
            "malformed_candidate_count": int(malformed_candidate_count),
        },
        "thresholds": {
            "review_min_seen": int(min_seen),
            "weak_score": float(weak_score),
            "strong_score": float(strong_score),
        },
        "pair_count": int(len(candidates)),
        "stable_candidate_count": int(stable_candidates),
        "recurring_weak_candidate_count": int(recurring_weak_candidates),
        "recurring_strong_candidate_count": int(recurring_strong_candidates),
        "modality_class_summary": modality_class_summary,
        "audio_audio_candidate_count": int(audio_audio_candidates),
        "crossmodal_candidate_count": int(crossmodal_candidates),
        "vision_relevant_candidate_count": int(vision_relevant_candidates),
        "ptz_vision_candidate_count": int(ptz_vision_candidates),
        "game_internal_candidate_count": int(game_internal_candidates),
        "hypothesis_ready_candidate_count": int(hypothesis_ready_candidates),
        "max_seen_count": int(max_seen_count),
        "max_score": round(float(max_score), 6),
        "max_stability_score": round(float(max_stability), 6),
        "warning": warning,
        "top_review_candidates": candidates[:review_topk],
        "top_hypothesis_ready_candidates": [c for c in candidates if bool(c.get("hypothesis_ready"))][:review_topk],
        "top_crossmodal_candidates": [c for c in candidates if bool(c.get("is_crossmodal"))][:review_topk],
        "top_vision_relevant_candidates": [c for c in candidates if bool(c.get("is_vision_relevant"))][:review_topk],
        "top_audio_audio_candidates": [c for c in candidates if c.get("modality_pair_class") == "audio_audio"][:review_topk],
    }
    return state


def _write_review_metrics(state: Dict[str, Any]) -> None:
    ts = int(state.get("ts") or _now_ts())
    history = state.get("history") or {}
    metrics = {
        "history_lines": float(history.get("lines_loaded") or 0),
        "pair_count": float(state.get("pair_count") or 0),
        "stable_candidates": float(state.get("stable_candidate_count") or 0),
        "recurring_weak_candidates": float(state.get("recurring_weak_candidate_count") or 0),
        "recurring_strong_candidates": float(state.get("recurring_strong_candidate_count") or 0),
        "audio_audio_candidates": float(state.get("audio_audio_candidate_count") or 0),
        "crossmodal_candidates": float(state.get("crossmodal_candidate_count") or 0),
        "vision_relevant_candidates": float(state.get("vision_relevant_candidate_count") or 0),
        "ptz_vision_candidates": float(state.get("ptz_vision_candidate_count") or 0),
        "game_internal_candidates": float(state.get("game_internal_candidate_count") or 0),
        "hypothesis_ready_candidates": float(state.get("hypothesis_ready_candidate_count") or 0),
        "max_seen_count": float(state.get("max_seen_count") or 0),
        "max_score": float(state.get("max_score") or 0.0),
        "max_stability_score": float(state.get("max_stability_score") or 0.0),
        "materialized": float(state.get("materialized_count") or 0),
    }
    for suffix, value in metrics.items():
        _write_metric(f"nmr:binding_probe_review:{suffix}", float(value), ts)


def _write_probe_metrics(state: Dict[str, Any]) -> None:
    ts = int(state.get("ts") or _now_ts())
    metrics = {
        "raw_pair_key_count": float((state.get("inputs") or {}).get("raw_pair_key_count") or 0),
        "candidates": float(state.get("candidate_count") or 0),
        "nmr_aligned_candidates": float(state.get("nmr_aligned_candidate_count") or 0),
        "nmr_boosted_candidates": float(state.get("nmr_boosted_candidate_count") or 0),
        "nmr_sparse_windows": float(state.get("nmr_sparse_window_count") or 0),
        "nmr_missing_windows": float(state.get("nmr_missing_window_count") or 0),
        "weak_candidates": float(state.get("weak_candidate_count") or 0),
        "strong_candidates": float(state.get("strong_candidate_count") or 0),
        "avg_score": float(state.get("avg_score") or 0.0),
        "max_score": float(state.get("max_score") or 0.0),
        "materialized": float(state.get("materialized_count") or 0),
        "history_seen_before_top": float((state.get("history") or {}).get("seen_before_top_count") or 0),
        "history_recurring_weak_top": float((state.get("history") or {}).get("recurring_weak_top_count") or 0),
    }
    for suffix, value in metrics.items():
        _write_metric(f"{METRIC_PREFIX}:{suffix}", float(value), ts)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ORÓMA NMR Phase 2 Binding Probe (measure-only, no object_relations writes)")
    p.add_argument("--once", action="store_true", help="Run once and exit. Present for orchestrator compatibility.")
    p.add_argument("--window-sec", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_WINDOW_SEC", 21600))
    p.add_argument("--max-snapchains", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_MAX_SNAPCHAINS", 2000))
    p.add_argument("--pair-window-sec", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_PAIR_WINDOW_SEC", 20))
    p.add_argument("--nmr-pre-sec", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_NMR_PRE_SEC", 5))
    p.add_argument("--nmr-post-sec", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_NMR_POST_SEC", 10))
    p.add_argument("--min-nmr-rows", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_MIN_NMR_ROWS", 2))
    p.add_argument("--min-repeat", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_MIN_REPEAT", 3))
    p.add_argument("--repeat-full", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_REPEAT_FULL", 8))
    p.add_argument("--weak-score", type=float, default=_env_float("OROMA_NMR_BINDING_PROBE_WEAK_SCORE", 0.45))
    p.add_argument("--strong-score", type=float, default=_env_float("OROMA_NMR_BINDING_PROBE_STRONG_SCORE", 0.65))
    p.add_argument("--nmr-boost-min", type=float, default=_env_float("OROMA_NMR_BINDING_PROBE_NMR_BOOST_MIN", 0.01))
    p.add_argument("--pe-ema-scale", type=float, default=_env_float("OROMA_NMR_BINDING_PROBE_PE_EMA_SCALE", 100.0))
    p.add_argument("--min-quality", type=float, default=_env_float("OROMA_NMR_BINDING_PROBE_MIN_QUALITY", -1.0))
    p.add_argument("--topk", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_TOPK", 25))
    p.add_argument("--state-path", type=str, default=_state_path())
    p.add_argument("--history-path", type=str, default=_history_path())
    p.add_argument("--history-enable", action=argparse.BooleanOptionalAction, default=_env_bool("OROMA_NMR_BINDING_PROBE_HISTORY_ENABLE", True))
    p.add_argument("--history-max-lines", type=int, default=_env_int("OROMA_NMR_BINDING_PROBE_HISTORY_MAX_LINES", 1000))
    p.add_argument("--review-history", action="store_true", help="Phase 2.0b: review nmr_binding_probe_history.jsonl only; no live DB observation scan.")
    p.add_argument("--review-state-path", type=str, default=_review_state_path())
    p.add_argument("--review-min-seen", type=int, default=_env_int("OROMA_NMR_BINDING_REVIEW_MIN_SEEN", 3))
    p.add_argument("--review-topk", type=int, default=_env_int("OROMA_NMR_BINDING_REVIEW_TOPK", _env_int("OROMA_NMR_BINDING_PROBE_TOPK", 25)))
    p.add_argument("--no-db-writes", action="store_true", help="Do not write DB metrics; still writes state JSON. Useful for local smoke tests.")
    p.add_argument("--print-json", action="store_true", help="Print full state JSON to stdout.")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if int(args.window_sec) <= 0:
        raise SystemExit("--window-sec must be > 0")
    if int(args.max_snapchains) <= 0:
        raise SystemExit("--max-snapchains must be > 0")
    if int(args.pair_window_sec) <= 0:
        raise SystemExit("--pair-window-sec must be > 0")

    if bool(getattr(args, "review_history", False)):
        state = _review_history(args)
        _atomic_write_json(str(args.review_state_path), state)
        if not args.no_db_writes:
            _write_review_metrics(state)
        if args.print_json:
            print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
        else:
            print(json.dumps({
                "ok": state.get("ok"),
                "mode": state.get("mode"),
                "history_lines": (state.get("history") or {}).get("lines_loaded"),
                "pair_count": state.get("pair_count"),
                "stable_candidate_count": state.get("stable_candidate_count"),
                "recurring_weak_candidate_count": state.get("recurring_weak_candidate_count"),
                "recurring_strong_candidate_count": state.get("recurring_strong_candidate_count"),
                "audio_audio_candidate_count": state.get("audio_audio_candidate_count"),
                "crossmodal_candidate_count": state.get("crossmodal_candidate_count"),
                "vision_relevant_candidate_count": state.get("vision_relevant_candidate_count"),
                "ptz_vision_candidate_count": state.get("ptz_vision_candidate_count"),
                "game_internal_candidate_count": state.get("game_internal_candidate_count"),
                "hypothesis_ready_candidate_count": state.get("hypothesis_ready_candidate_count"),
                "max_seen_count": state.get("max_seen_count"),
                "max_score": state.get("max_score"),
                "max_stability_score": state.get("max_stability_score"),
                "materialized_count": state.get("materialized_count"),
                "warning": state.get("warning"),
                "review_state_path": str(args.review_state_path),
                "db_writes": not args.no_db_writes,
            }, ensure_ascii=False, sort_keys=True))
        return 0

    state = run_probe(args)
    _atomic_write_json(str(args.state_path), state)
    if bool(getattr(args, "history_enable", True)):
        _append_history_jsonl(str(args.history_path), state, int(args.history_max_lines))

    if not args.no_db_writes:
        _write_probe_metrics(state)

    if args.print_json:
        print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(json.dumps({
            "ok": state.get("ok"),
            "candidate_count": state.get("candidate_count"),
            "nmr_aligned_candidate_count": state.get("nmr_aligned_candidate_count"),
            "nmr_boosted_candidate_count": state.get("nmr_boosted_candidate_count"),
            "nmr_sparse_window_count": state.get("nmr_sparse_window_count"),
            "nmr_missing_window_count": state.get("nmr_missing_window_count"),
            "weak_candidate_count": state.get("weak_candidate_count"),
            "strong_candidate_count": state.get("strong_candidate_count"),
            "avg_score": state.get("avg_score"),
            "max_score": state.get("max_score"),
            "warning": state.get("warning"),
            "history_seen_before_top_count": (state.get("history") or {}).get("seen_before_top_count"),
            "history_recurring_weak_top_count": (state.get("history") or {}).get("recurring_weak_top_count"),
            "state_path": str(args.state_path),
            "history_path": str(args.history_path),
            "db_writes": not args.no_db_writes,
        }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

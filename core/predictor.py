#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/predictor.py
# Projekt: ORÓMA
# Version: v3.7 (stabilisiert, performant)
# Stand:   2025-09-29
#
# Zweck
# ─────
# Leichtgewichtiges Prädiktionsmodul:
#   • Übergangszählung P(dst|src) aus beobachteten Sequenzen (Markov-1)
#   • Optional: Centroid-Ähnlichkeit für zustandsnahe Prognosen
#   • SQLite-Backendschema + Metrikprotokollierung
#
# Wichtige Verbesserungen ggü. v3.5
# ─────────────────────────────────
#   • SQLite-robuste UPSERTs via UNIQUE-Index (src_hash,dst_hash)
#   • Batch-Update in update_from_sequences() (eine Transaktion)
#   • Korrektur Scoring in predict_next_by_centroid() (kein p²)
#   • Laplace-Glättung optional in predict_next_k()
#   • Zusätzliche Indizes (best effort) für große Tabellen
#
# Öffentliche API (stabil)
# ────────────────────────
#   ensure_schema() -> None
#   make_state_hash(obj) -> str
#   observe_pair(src_hash, dst_hash, *, src_centroid=None, dst_centroid=None, meta=None) -> None
#   update_from_sequences(sequences, *, to_hash=make_state_hash, to_centroid=None) -> int
#   predict_next_k(state_hash, k=5, laplace=0.0) -> List[(dst_hash, prob)]
#   predict_next_by_centroid(query_vec, k=5, alpha=0.65) -> List[(dst_hash, score)]
#   evaluate_hit_at_k(pairs, k_list=(1,3,5)) -> Dict[str, float]
#
# Abhängigkeiten
# ──────────────
#   • core.sql_manager (ensure_schema, get_conn, optional insert_metric)
#   • Optional NumPy für Vektorrechnung (fallback reine Python)
# =============================================================================

from __future__ import annotations

import json
import math
import os
import sys
import time
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

# Projektbasis (v3.5): feste Default-Base auf /opt/ai/oroma
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# Optional: NumPy für Vektor-Operationen (nicht zwingend)
_HAS_NP = False
try:
    import numpy as _np  # type: ignore
    _HAS_NP = True
except Exception:
    _HAS_NP = False

_SQL_OK = True
try:
    from core import sql_manager  # type: ignore
    sql_manager.ensure_schema()
except Exception:
    _SQL_OK = False

_DBW_OK = False
try:
    from core import db_writer_client  # type: ignore
    _DBW_OK = True
except Exception:
    _DBW_OK = False


# -----------------------------------------------------------------------------
# Hilfsfunktionen
# -----------------------------------------------------------------------------

def _now() -> int:
    return int(time.time())

def _to_vec(x: Sequence[float]) -> List[float]:
    return [float(v) for v in (x or [])]

def _l2(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return float("inf")
    if _HAS_NP:
        va, vb = _np.asarray(a, dtype=_np.float32), _np.asarray(b, dtype=_np.float32)
        return float(_np.linalg.norm(va - vb))
    return math.sqrt(sum((float(x)-float(y))**2 for x, y in zip(a, b)))

def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

def _safe_json_loads(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

def make_state_hash(obj: Any) -> str:
    """
    Stabile, kurze Repräsentation eines Zustands:
      - dict/list → JSON normiert → SHA1 → 12 Hex
      - str       → SHA1 → 12 Hex
      - sonst     → JSON normiert → SHA1 → 12 Hex
    """
    try:
        if isinstance(obj, (dict, list, tuple)):
            raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        elif isinstance(obj, str):
            raw = obj.encode("utf-8")
        else:
            raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    except Exception:
        # Fallback – best effort
        raw = str(obj).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name, "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return bool(default)
    vv = str(v).strip().lower()
    if vv in ("1", "true", "yes", "y", "on"):
        return True
    if vv in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _dbw_enabled() -> bool:
    return bool(_DBW_OK and getattr(db_writer_client, "enabled", lambda: False)())


def _dbw_timeout_ms() -> int:
    return max(2000, _env_int("OROMA_DBW_CLIENT_TIMEOUT_MS_PREDICTOR", 15000))


def _write_exec(sql: str, params: Sequence[Any], *, tag: str, expect: str = "rowcount") -> int:
    if _dbw_enabled():
        if str(expect).lower() == "lastrowid":
            return int(db_writer_client.exec_lastrowid(sql, params=params, tag=tag, priority="normal", timeout_ms=_dbw_timeout_ms(), db="oroma"))
        return int(db_writer_client.exec_write(sql, params=params, tag=tag, priority="normal", timeout_ms=_dbw_timeout_ms(), db="oroma"))
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        if str(expect).lower() == "lastrowid":
            return int(cur.lastrowid or 0)
        return int(cur.rowcount or 0)


def _write_transaction(stmts: Sequence[Tuple[str, Sequence[Any]]], *, tag: str) -> None:
    if not stmts:
        return
    if _dbw_enabled():
        db_writer_client.transaction(stmts, tag=tag, priority="normal", timeout_ms=_dbw_timeout_ms(), db="oroma")
        return
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()
        for sql, params in stmts:
            cur.execute(sql, tuple(params))
        conn.commit()


# -----------------------------------------------------------------------------
# Schema (mit robusten Indizes/Unique-Constraints)
# -----------------------------------------------------------------------------

def ensure_schema() -> None:
    """
    Legt Tabellen & Indizes an (idempotent).
    Wichtig: UNIQUE(src_hash,dst_hash) für konfliktfreie UPSERTs.

    Zusätzlich werden Tabellen für Prediction-Reward/Surprise-Curriculum angelegt,
    damit ORÓMA Vorhersagegüte, Relevanz und freigegebene Horizonttiefe persistent
    und DBWriter-kompatibel nachhalten kann.
    """
    if not _SQL_OK:
        return

    stmts: List[Tuple[str, Sequence[Any]]] = [
        (
            """
            CREATE TABLE IF NOT EXISTS predictor_transitions(
                id INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL,
                src_hash TEXT NOT NULL,
                dst_hash TEXT NOT NULL,
                count INTEGER NOT NULL
            )
            """,
            (),
        ),
        ("CREATE INDEX IF NOT EXISTS idx_pred_tr_src ON predictor_transitions(src_hash)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_pred_tr_dst ON predictor_transitions(dst_hash)", ()),
        ("CREATE UNIQUE INDEX IF NOT EXISTS uniq_pred_tr_pair ON predictor_transitions(src_hash, dst_hash)", ()),
        (
            """
            CREATE TABLE IF NOT EXISTS predictor_states(
                id INTEGER PRIMARY KEY,
                updated_at INTEGER NOT NULL,
                state_hash TEXT UNIQUE,
                centroid TEXT,
                meta TEXT
            )
            """,
            (),
        ),
        ("CREATE INDEX IF NOT EXISTS idx_pred_state_hash ON predictor_states(state_hash)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_pred_state_updated ON predictor_states(updated_at)", ()),
        (
            """
            CREATE TABLE IF NOT EXISTS predictor_metrics(
                id INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL
            )
            """,
            (),
        ),
        ("CREATE INDEX IF NOT EXISTS idx_pred_metric ON predictor_metrics(metric,created_at)", ()),
        (
            """
            CREATE TABLE IF NOT EXISTS predictor_reward_events(
                id INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL,
                namespace TEXT NOT NULL,
                source TEXT,
                requested_horizon INTEGER NOT NULL DEFAULT 1,
                granted_horizon INTEGER NOT NULL DEFAULT 1,
                was_correct INTEGER NOT NULL DEFAULT 0,
                matched_rank INTEGER,
                confidence REAL,
                surprise REAL NOT NULL DEFAULT 0.0,
                value_gain REAL NOT NULL DEFAULT 0.0,
                value_weight REAL NOT NULL DEFAULT 1.0,
                surprise_weight REAL NOT NULL DEFAULT 1.0,
                triviality_weight REAL NOT NULL DEFAULT 1.0,
                horizon_weight REAL NOT NULL DEFAULT 1.0,
                reward REAL NOT NULL DEFAULT 0.0,
                meta TEXT
            )
            """,
            (),
        ),
        ("CREATE INDEX IF NOT EXISTS idx_pred_reward_ns_ts ON predictor_reward_events(namespace, created_at)", ()),
        (
            """
            CREATE TABLE IF NOT EXISTS predictor_curriculum(
                namespace TEXT PRIMARY KEY,
                updated_at INTEGER NOT NULL,
                current_horizon INTEGER NOT NULL DEFAULT 1,
                requested_horizon INTEGER NOT NULL DEFAULT 1,
                total_events INTEGER NOT NULL DEFAULT 0,
                total_correct INTEGER NOT NULL DEFAULT 0,
                stage_events INTEGER NOT NULL DEFAULT 0,
                stage_correct INTEGER NOT NULL DEFAULT 0,
                ema_acc REAL NOT NULL DEFAULT 0.0,
                promote_threshold REAL NOT NULL DEFAULT 0.85,
                demote_threshold REAL NOT NULL DEFAULT 0.55,
                max_horizon INTEGER NOT NULL DEFAULT 3,
                meta TEXT
            )
            """,
            (),
        ),
    ]
    _write_transaction(stmts, tag="core.predictor.ensure_schema")



# -----------------------------------------------------------------------------
# Kernfunktionen (mit UPSERTs)
# -----------------------------------------------------------------------------

def _upsert_state_cur(cur, state_hash: str,
                      centroid: Optional[Sequence[float]] = None,
                      meta: Optional[Dict[str, Any]] = None) -> None:
    """
    Cursor-Variante (für Batch-Import). Nutzt ON CONFLICT(state_hash) DO UPDATE.
    """
    cur.execute(
        """
        INSERT INTO predictor_states(updated_at, state_hash, centroid, meta)
        VALUES(?,?,?,?)
        ON CONFLICT(state_hash) DO UPDATE SET
            updated_at=excluded.updated_at,
            centroid = COALESCE(excluded.centroid, predictor_states.centroid),
            meta     = COALESCE(excluded.meta,     predictor_states.meta)
        """,
        (_now(), state_hash,
         _json(centroid) if centroid is not None else None,
         _json(meta) if meta is not None else None)
    )

def _upsert_transition_cur(cur, src_hash: str, dst_hash: str) -> None:
    """
    Cursor-Variante der Transition mit atomischem Zähler-Inkrement.
    """
    cur.execute(
        """
        INSERT INTO predictor_transitions(created_at, src_hash, dst_hash, count)
        VALUES(?,?,?,1)
        ON CONFLICT(src_hash, dst_hash) DO UPDATE SET
            count = predictor_transitions.count + 1,
            created_at = excluded.created_at
        """,
        (_now(), src_hash, dst_hash)
    )

def observe_pair(
    src_hash: str,
    dst_hash: str,
    *,
    src_centroid: Optional[Sequence[float]] = None,
    dst_centroid: Optional[Sequence[float]] = None,
    meta: Optional[Dict[str, Any]] = None
) -> None:
    """
    Nimmt eine beobachtete Transition (src→dst) auf (eigene Transaktion).
    Läuft bei aktivem DBWriter strikt über den zentralen Single-Writer.
    """
    if not _SQL_OK:
        raise RuntimeError("sql_manager nicht verfügbar")
    ensure_schema()
    state_sql = """
        INSERT INTO predictor_states(updated_at, state_hash, centroid, meta)
        VALUES(?,?,?,?)
        ON CONFLICT(state_hash) DO UPDATE SET
            updated_at=excluded.updated_at,
            centroid = COALESCE(excluded.centroid, predictor_states.centroid),
            meta     = COALESCE(excluded.meta, predictor_states.meta)
    """
    tr_sql = """
        INSERT INTO predictor_transitions(created_at, src_hash, dst_hash, count)
        VALUES(?,?,?,1)
        ON CONFLICT(src_hash, dst_hash) DO UPDATE SET
            count = predictor_transitions.count + 1,
            created_at = excluded.created_at
    """
    ts = _now()
    stmts = [
        (state_sql, (ts, src_hash, _json(src_centroid) if src_centroid is not None else None, _json(meta) if meta is not None else None)),
        (state_sql, (ts, dst_hash, _json(dst_centroid) if dst_centroid is not None else None, _json(meta) if meta is not None else None)),
        (tr_sql, (ts, src_hash, dst_hash)),
    ]
    _write_transaction(stmts, tag="core.predictor.observe_pair")

def update_from_sequences(
    sequences: Iterable[Sequence[Any]],
    *,
    to_hash = make_state_hash,
    to_centroid = None  # Callable[[Any], Sequence[float]] | None
) -> int:
    """
    Erzeugt Transitionen aus Sequenzen von Zuständen/Snaps.
    Nutzt eine Sammel-Transaktion; bei aktivem DBWriter werden die Statements in
    Blöcken an den Single-Writer übergeben.
    Rückgabe: Anzahl erzeugter Paare.
    """
    if not _SQL_OK:
        return 0
    ensure_schema()
    n_pairs = 0
    state_sql = """
        INSERT INTO predictor_states(updated_at, state_hash, centroid, meta)
        VALUES(?,?,?,?)
        ON CONFLICT(state_hash) DO UPDATE SET
            updated_at=excluded.updated_at,
            centroid = COALESCE(excluded.centroid, predictor_states.centroid),
            meta     = COALESCE(excluded.meta, predictor_states.meta)
    """
    tr_sql = """
        INSERT INTO predictor_transitions(created_at, src_hash, dst_hash, count)
        VALUES(?,?,?,1)
        ON CONFLICT(src_hash, dst_hash) DO UPDATE SET
            count = predictor_transitions.count + 1,
            created_at = excluded.created_at
    """
    stmts: List[Tuple[str, Sequence[Any]]] = []
    ts = _now()
    flush_every = max(200, _env_int("OROMA_PREDICTOR_BATCH_STMTS", 1000))
    for seq in sequences:
        if not seq or len(seq) < 2:
            continue
        for a, b in zip(seq[:-1], seq[1:]):
            h_a = to_hash(a)
            h_b = to_hash(b)
            c_a = to_centroid(a) if to_centroid else None
            c_b = to_centroid(b) if to_centroid else None
            stmts.append((state_sql, (ts, h_a, _json(c_a) if c_a is not None else None, None)))
            stmts.append((state_sql, (ts, h_b, _json(c_b) if c_b is not None else None, None)))
            stmts.append((tr_sql, (ts, h_a, h_b)))
            n_pairs += 1
            if len(stmts) >= flush_every:
                _write_transaction(stmts, tag="core.predictor.update_from_sequences")
                stmts = []
    if stmts:
        _write_transaction(stmts, tag="core.predictor.update_from_sequences")
    return n_pairs


# -----------------------------------------------------------------------------
# Vorhersage
# -----------------------------------------------------------------------------

def predict_next_k(state_hash: str, k: int = 5, *, laplace: float = 0.0) -> List[Tuple[str, float]]:
    """
    Liefert die Top-K nächsten Zustände aus der beobachteten Übergangszählung.
    P(dst|src) = (count + laplace) / (Sum(count über alle Ziele) + laplace * D)
      - `laplace` (Default 0.0) kann z. B. auf 1.0 gesetzt werden (Add-1).
      - Normalisierung erfolgt über ALLE Ziele (kein k-begrenztes Total).
    """
    if not _SQL_OK:
        return []
    ensure_schema()
    k = max(1, int(k))
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()
        # Total über ALLE Ziele (kein Limit!)
        cur.execute("SELECT SUM(count) AS s, COUNT(*) AS d FROM predictor_transitions WHERE src_hash=?", (state_hash,))
        row = cur.fetchone()
        total = int(row["s"] or 0)
        distinct_d = int(row["d"] or 0)

        if total == 0:
            return []

        # Beste Ziele (mehr als k, um Gleichstände abzufangen)
        cur.execute(
            """
            SELECT dst_hash, count
              FROM predictor_transitions
             WHERE src_hash=?
             ORDER BY count DESC, created_at DESC
             LIMIT ?
            """,
            (state_hash, k * 8),
        )
        rows = cur.fetchall() or []

    denom = float(total + (laplace * distinct_d if laplace > 0.0 else 0.0))
    out: List[Tuple[str, float]] = []
    for r in rows:
        c = int(r["count"])
        num = float(c + (laplace if laplace > 0.0 else 0.0))
        out.append((str(r["dst_hash"]), num / denom))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:k]

def predict_next_by_centroid(
    query_vec: Sequence[float],
    k: int = 5,
    *,
    alpha: float = 0.65
) -> List[Tuple[str, float]]:
    """
    Kombiniert (a) zentrumsnahe Quellenzustände und (b) deren häufige Ziele.
    score(dst) += w_src * p(dst|src) mit weicher Mischung:
      w_src' = (alpha * w_src + (1 - alpha))  ∈ [1 - alpha, 1]
      → score(dst) += w_src' * p
    (Kein versehentliches p² mehr.)
    """
    if not _SQL_OK:
        return []
    ensure_schema()
    k = max(1, int(k))
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()
        # 1) Kandidaten-Quellen mit Centroid
        cur.execute(
            "SELECT state_hash, centroid FROM predictor_states WHERE centroid IS NOT NULL ORDER BY updated_at DESC LIMIT 2000"
        )
        states = [(str(r["state_hash"]), _safe_json_loads(r["centroid"])) for r in (cur.fetchall() or [])]

        if not states:
            return []

        q = _to_vec(query_vec)
        weights: Dict[str, float] = {}
        for sh, cjson in states:
            vec = _to_vec(cjson or [])
            if not vec or len(vec) != len(q):
                continue
            d = _l2(q, vec)
            w = 1.0 / (1.0 + float(d))  # ∈ (0,1]
            weights[sh] = max(0.0, min(1.0, w))

        if not weights:
            return []

        # 2) Für Top-N Quellen deren Transitionen berücksichtigen
        src_sorted = sorted(weights.items(), key=lambda x: x[1], reverse=True)[: min(64, len(weights))]
        dst_scores: Dict[str, float] = {}
        for src_hash, wsrc in src_sorted:
            cur.execute(
                "SELECT dst_hash, count FROM predictor_transitions WHERE src_hash=? ORDER BY count DESC LIMIT 32",
                (src_hash,),
            )
            rows = cur.fetchall() or []
            tot = sum(int(r["count"]) for r in rows) or 1
            wsrc_prime = (alpha * wsrc) + (1.0 - alpha)  # ∈ [1-alpha, 1]
            for r in rows:
                dst = str(r["dst_hash"])
                p = float(int(r["count"]) / tot)
                dst_scores[dst] = dst_scores.get(dst, 0.0) + (wsrc_prime * p)

    ranked = sorted(dst_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:k]


# -----------------------------------------------------------------------------
# Evaluierung / Metriken
# -----------------------------------------------------------------------------

def hit_at_k(truth_hash: str, preds: Sequence[Tuple[str, float]]) -> int:
    """1, wenn truth_hash in der Prädiktionsliste vorkommt, sonst 0."""
    t = str(truth_hash)
    return 1 if any(str(h) == t for h, _ in preds) else 0

def evaluate_hit_at_k(
    pairs: Iterable[Tuple[str, str]],
    k_list: Sequence[int] = (1, 3, 5)
) -> Dict[str, float]:
    """
    Erwartet eine Menge an (src_hash, true_next_hash).
    Liefert Hit@K und Top-1-Accuracy über alle Paare und loggt in predictor_metrics.
    """
    if not _SQL_OK:
        return {f"hit@{int(k)}": 0.0 for k in k_list} | {"top1_acc": 0.0}
    ensure_schema()

    k_list = [int(k) for k in k_list if int(k) > 0] or [1]
    n = 0
    hits: Dict[int, int] = {int(k): 0 for k in k_list}
    top1_ok = 0

    for src_h, true_h in pairs:
        ranked = predict_next_k(src_h, k=max(k_list))
        if not ranked:
            continue
        n += 1
        for kk in k_list:
            hits[int(kk)] += hit_at_k(true_h, ranked[: int(kk)])
        top1_ok += 1 if ranked and str(ranked[0][0]) == str(true_h) else 0

    denom = float(max(1, n))
    out: Dict[str, float] = {f"hit@{kk}": float(h) / denom for kk, h in hits.items()}
    out["top1_acc"] = float(top1_ok) / denom

    # in metrics loggen (best effort)
    try:
        ts = _now()
        stmts = [
            ("INSERT INTO predictor_metrics(created_at, metric, value) VALUES(?,?,?)", (ts, str(k), float(v)))
            for k, v in out.items()
        ]
        _write_transaction(stmts, tag="core.predictor.evaluate_hit_at_k")
        for k, v in out.items():
            try:
                sql_manager.insert_metric(str(k), float(v))  # type: ignore[attr-defined]
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.predictor.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.predictor.pass.2",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

    return out


# -----------------------------------------------------------------------------
# Prediction-Reward / Surprise / Horizon-Curriculum
# -----------------------------------------------------------------------------


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _surprise_from_confidence(confidence: Optional[float]) -> float:
    floor = _env_float("OROMA_PREDICTOR_SURPRISE_PROB_FLOOR", 0.02)
    cap = _env_float("OROMA_PREDICTOR_SURPRISE_CAP", 4.0)
    p = _clip(float(confidence if confidence is not None else floor), floor, 1.0)
    return _clip(-math.log(p), 0.0, cap)


def _value_weight(value_gain: float) -> float:
    # Wertgewinne (z. B. Bauer→Turm) sollen stärker zählen als triviale korrekte Züge.
    gain = max(0.0, float(value_gain))
    scale = max(0.25, _env_float("OROMA_PREDICTOR_VALUE_GAIN_SCALE", 1.0))
    cap = max(1.0, _env_float("OROMA_PREDICTOR_VALUE_WEIGHT_CAP", 4.0))
    return _clip(1.0 + (gain / scale), 1.0, cap)


def _surprise_weight(surprise: float) -> float:
    scale = max(0.25, _env_float("OROMA_PREDICTOR_SURPRISE_SCALE", 1.5))
    cap = max(1.0, _env_float("OROMA_PREDICTOR_SURPRISE_WEIGHT_CAP", 3.0))
    return _clip(1.0 + (max(0.0, float(surprise)) / scale), 1.0, cap)


def _triviality_weight(confidence: Optional[float], value_gain: float) -> float:
    conf = _clip(float(confidence if confidence is not None else 0.0), 0.0, 1.0)
    trivial_prob = _env_float("OROMA_PREDICTOR_TRIVIAL_PROB", 0.85)
    trivial_value = _env_float("OROMA_PREDICTOR_TRIVIAL_VALUE_MAX", 0.5)
    if conf >= trivial_prob and float(value_gain) <= trivial_value:
        return _clip(_env_float("OROMA_PREDICTOR_TRIVIAL_WEIGHT", 0.35), 0.05, 1.0)
    return 1.0


def _horizon_weight(horizon: int) -> float:
    base = _env_float("OROMA_PREDICTOR_HORIZON_STEP_BONUS", 0.25)
    cap = _env_float("OROMA_PREDICTOR_HORIZON_WEIGHT_CAP", 2.0)
    return _clip(1.0 + (max(1, int(horizon)) - 1) * base, 1.0, cap)


def _get_curriculum_row(namespace: str) -> Dict[str, Any]:
    ensure_schema()
    ns = str(namespace or "global")
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM predictor_curriculum WHERE namespace=?", (ns,))
        row = cur.fetchone()
        if row:
            return dict(row)
    defaults = {
        "namespace": ns,
        "updated_at": _now(),
        "current_horizon": 1,
        "requested_horizon": 1,
        "total_events": 0,
        "total_correct": 0,
        "stage_events": 0,
        "stage_correct": 0,
        "ema_acc": 0.0,
        "promote_threshold": _env_float("OROMA_PREDICTOR_PROMOTE_THRESHOLD", 0.85),
        "demote_threshold": _env_float("OROMA_PREDICTOR_DEMOTE_THRESHOLD", 0.55),
        "max_horizon": _env_int("OROMA_PREDICTOR_MAX_HORIZON", 3),
        "meta": None,
    }
    _write_exec(
        """
        INSERT INTO predictor_curriculum(
            namespace, updated_at, current_horizon, requested_horizon, total_events,
            total_correct, stage_events, stage_correct, ema_acc,
            promote_threshold, demote_threshold, max_horizon, meta
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(namespace) DO NOTHING
        """,
        (
            defaults["namespace"], defaults["updated_at"], defaults["current_horizon"], defaults["requested_horizon"],
            defaults["total_events"], defaults["total_correct"], defaults["stage_events"], defaults["stage_correct"],
            defaults["ema_acc"], defaults["promote_threshold"], defaults["demote_threshold"], defaults["max_horizon"], defaults["meta"],
        ),
        tag="core.predictor.curriculum.init",
    )
    return defaults


def get_prediction_horizon(namespace: str = "global") -> int:
    row = _get_curriculum_row(namespace)
    return max(1, int(row.get("current_horizon") or 1))


def record_prediction_feedback(
    namespace: str = "global",
    *,
    was_correct: bool,
    confidence: Optional[float] = None,
    value_gain: float = 0.0,
    requested_horizon: int = 1,
    source: str = "",
    matched_rank: Optional[int] = None,
    surprise: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Persistiert ein Prediction-Feedback-Ereignis mit Surprise-/Value-Gewichtung und
    aktualisiert das Horizon-Curriculum.

    Design für ORÓMA:
      - Reward nur für korrekte Vorhersagen.
      - Wertvolle korrekte Vorhersagen (Mehrgewinn) zählen stärker.
      - Überraschende korrekte Vorhersagen (Aha-Moment) zählen stärker.
      - Triviale, fast sichere Vorhersagen werden gedämpft.
      - Die freigegebene Horizonttiefe wird erst nach stabil guter Trefferquote erhöht.
    """
    ensure_schema()
    ns = str(namespace or "global")
    row = _get_curriculum_row(ns)
    granted_horizon = max(1, min(int(requested_horizon or 1), int(row.get("current_horizon") or 1)))
    req_h = max(1, int(requested_horizon or 1))
    conf = None if confidence is None else _clip(float(confidence), 0.0, 1.0)
    surprise_v = float(surprise if surprise is not None else (_surprise_from_confidence(conf) if was_correct else 0.0))
    value_w = _value_weight(value_gain)
    surprise_w = _surprise_weight(surprise_v) if was_correct else 1.0
    trivial_w = _triviality_weight(conf, value_gain) if was_correct else 1.0
    horiz_w = _horizon_weight(granted_horizon)
    base = 1.0 if bool(was_correct) else 0.0
    reward = float(base * value_w * surprise_w * trivial_w * horiz_w)

    ema_alpha = _clip(_env_float("OROMA_PREDICTOR_EMA_ALPHA", 0.15), 0.01, 0.95)
    total_events = int(row.get("total_events") or 0) + 1
    total_correct = int(row.get("total_correct") or 0) + (1 if was_correct else 0)
    stage_events = int(row.get("stage_events") or 0) + 1
    stage_correct = int(row.get("stage_correct") or 0) + (1 if was_correct else 0)
    prev_ema = float(row.get("ema_acc") or 0.0)
    ema_acc = ((1.0 - ema_alpha) * prev_ema) + (ema_alpha * (1.0 if was_correct else 0.0))
    current_horizon = max(1, int(row.get("current_horizon") or 1))
    max_horizon = max(1, int(row.get("max_horizon") or _env_int("OROMA_PREDICTOR_MAX_HORIZON", 3)))
    promote_threshold = float(row.get("promote_threshold") or _env_float("OROMA_PREDICTOR_PROMOTE_THRESHOLD", 0.85))
    demote_threshold = float(row.get("demote_threshold") or _env_float("OROMA_PREDICTOR_DEMOTE_THRESHOLD", 0.55))
    min_obs = max(8, _env_int("OROMA_PREDICTOR_STAGE_MIN_OBS", 32))

    if stage_events >= min_obs:
        if ema_acc >= promote_threshold and current_horizon < max_horizon:
            current_horizon += 1
            stage_events = 0
            stage_correct = 0
        elif ema_acc < demote_threshold and current_horizon > 1:
            current_horizon -= 1
            stage_events = 0
            stage_correct = 0

    ts = _now()
    event_meta = dict(meta or {})
    event_meta.setdefault("confidence", conf)
    event_meta.setdefault("ema_acc", ema_acc)
    stmts = [
        (
            """
            INSERT INTO predictor_reward_events(
                created_at, namespace, source, requested_horizon, granted_horizon,
                was_correct, matched_rank, confidence, surprise, value_gain,
                value_weight, surprise_weight, triviality_weight, horizon_weight,
                reward, meta
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts, ns, str(source or ""), req_h, granted_horizon,
                1 if was_correct else 0, matched_rank, conf, surprise_v, float(value_gain),
                value_w, surprise_w, trivial_w, horiz_w, reward, _json(event_meta),
            ),
        ),
        (
            """
            INSERT INTO predictor_curriculum(
                namespace, updated_at, current_horizon, requested_horizon, total_events,
                total_correct, stage_events, stage_correct, ema_acc,
                promote_threshold, demote_threshold, max_horizon, meta
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(namespace) DO UPDATE SET
                updated_at=excluded.updated_at,
                current_horizon=excluded.current_horizon,
                requested_horizon=excluded.requested_horizon,
                total_events=excluded.total_events,
                total_correct=excluded.total_correct,
                stage_events=excluded.stage_events,
                stage_correct=excluded.stage_correct,
                ema_acc=excluded.ema_acc,
                promote_threshold=excluded.promote_threshold,
                demote_threshold=excluded.demote_threshold,
                max_horizon=excluded.max_horizon,
                meta=excluded.meta
            """,
            (
                ns, ts, current_horizon, req_h, total_events,
                total_correct, stage_events, stage_correct, ema_acc,
                promote_threshold, demote_threshold, max_horizon, _json({"last_source": str(source or "")}),
            ),
        ),
    ]
    _write_transaction(stmts, tag="core.predictor.record_prediction_feedback")
    return {
        "namespace": ns,
        "requested_horizon": req_h,
        "granted_horizon": granted_horizon,
        "current_horizon": current_horizon,
        "was_correct": bool(was_correct),
        "confidence": conf,
        "surprise": surprise_v,
        "value_gain": float(value_gain),
        "reward": reward,
        "ema_acc": ema_acc,
        "stage_events": stage_events,
        "stage_correct": stage_correct,
    }


def score_ranked_prediction(
    truth_hash: str,
    preds: Sequence[Tuple[str, float]],
    *,
    namespace: str = "global",
    value_gain: float = 0.0,
    requested_horizon: int = 1,
    source: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Komfortfunktion für bestehende predict_next_k()-Rankings.
    Bestimmt Treffer, Rang und Confidence des richtigen Nachfolgezustands und leitet
    diese Informationen in das Prediction-Curriculum weiter.
    """
    truth = str(truth_hash)
    matched_rank: Optional[int] = None
    matched_prob: Optional[float] = None
    for idx, (dst_hash, prob) in enumerate(preds or [], start=1):
        if str(dst_hash) == truth:
            matched_rank = idx
            matched_prob = float(prob)
            break
    return record_prediction_feedback(
        namespace=namespace,
        was_correct=matched_rank is not None,
        confidence=matched_prob,
        value_gain=value_gain,
        requested_horizon=requested_horizon,
        source=source,
        matched_rank=matched_rank,
        meta=meta,
    )


# -----------------------------------------------------------------------------
# Selbsttest
# -----------------------------------------------------------------------------

def _toy_sequences(n_paths: int = 4, length: int = 12) -> List[List[str]]:
    """Erzeugt Spielzeugpfade mit wiederkehrenden Mustern."""
    paths: List[List[str]] = []
    for i in range(n_paths):
        base = [f"s{i}_{j%4}" for j in range(length)]
        seq = []
        for j, token in enumerate(base):
            seq.append(token)
            if j % 5 == 0 and i + 1 < n_paths:
                seq.append(f"s{i+1}_{j%4}")
        paths.append(seq)
    return paths

def _selftest() -> None:
    print("[predictor] selftest…")
    ensure_schema()
    # 1) Daten einspeisen (Batch)
    seqs = _toy_sequences()
    made = update_from_sequences(seqs, to_hash=lambda x: x, to_centroid=None)
    print("  transitions:", made)
    # 2) Prognose
    ex = seqs[0][3]
    preds = predict_next_k(ex, k=5, laplace=1.0)
    print("  predict_next_k:", ex, "→", preds[:3])
    # 3) Centroid (Dummy): benutze Index als 2D-Vektor
    #    (nur zur Funktionsprobe; real kommen echte Embeddings)
    q = [0.0, 1.0]
    res_cent = predict_next_by_centroid(q, k=5, alpha=0.65)
    print("  predict_next_by_centroid:", res_cent[:3])
    # 4) Bewertung
    pairs: List[Tuple[str, str]] = []
    for seq in seqs:
        for a, b in zip(seq[:-1], seq[1:]):
            pairs.append((a, b))
    res = evaluate_hit_at_k(pairs, k_list=(1, 3, 5))
    print("  eval:", res)
    print("[predictor] OK ✅")

if __name__ == "__main__":
    _selftest()
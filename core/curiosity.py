#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/curiosity.py
# Projekt: ORÓMA
# Version: v3.5
# Stand:   2025-09-21
#
# Zweck:
#   Curiosity-Modul für ORÓMA:
#     - Erzeugt und speichert Neugier-Signale ("curiosity signals")
#     - Basierend auf Abweichungen, Neuheit, Unsicherheit oder Zielabweichung
#     - Unterstützt Motivation für Exploration (Spiele, Sensorik, SnapChains)
#     - Loggt Signale in SQLite (curiosity_log)
#
# Steuerung:
#   - log_signal(value: float)    → schreibt neuen Signalwert in DB
#   - recent(n: int)              → liefert letzte n Signale
#   - mean(window: int)           → berechnet Durchschnitt über Fenster
#
# Hinweise:
#   - Wird im Learning-Dashboard (learning.py) genutzt
#   - Kombiniert mit Reward-Signalen für AgentLoop & Policy-Learning
# =============================================================================

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

# Projektbasis
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma/")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

_SQL_OK = True
try:
    from core import sql_manager  # type: ignore
    sql_manager.ensure_schema()
except Exception:
    _SQL_OK = False


# Optional: DBWriter (Stufe C · Global Single Writer) — best-effort für Writes
_DBW_OK = False
try:
    from core import db_writer_client  # type: ignore
    _DBW_OK = True
except Exception:
    _DBW_OK = False

def _dbw_enabled() -> bool:
    """Return True if DBWriter is enabled and client module is available."""
    return _DBW_OK and (os.environ.get("OROMA_DBW_ENABLE", "0") in ("1", "true", "True", "yes", "on"))
# Optional: NumPy für Vektor-Operationen
_HAS_NP = False
try:
    import numpy as _np  # type: ignore
    _HAS_NP = True
except Exception:
    _HAS_NP = False


# ----------------------------- Utils -----------------------------------------

def _to_vec(x: Sequence[float]) -> List[float]:
    return [float(v) for v in (x or [])]

def _l2(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return float("inf")
    if _HAS_NP:
        va, vb = _np.asarray(a, dtype=_np.float32), _np.asarray(b, dtype=_np.float32)
        return float(_np.linalg.norm(va - vb))
    return math.sqrt(sum((float(x)-float(y))**2 for x, y in zip(a, b)))

def _softmax(logits: Sequence[float]) -> List[float]:
    if not logits:
        return []
    if _HAS_NP:
        z = _np.asarray(logits, dtype=_np.float32)
        z = z - _np.max(z)
        p = _np.exp(z)
        p = p / max(_np.sum(p), 1e-9)
        return [float(v) for v in p.tolist()]
    m = max(logits)
    ex = [math.exp(v - m) for v in logits]
    s = sum(ex) or 1e-9
    return [v / s for v in ex]

def _entropy(p: Sequence[float]) -> float:
    e = 0.0
    for v in p:
        v = float(max(1e-12, v))
        e -= v * math.log(v)
    return e

def _kl(p: Sequence[float], q: Sequence[float]) -> float:
    if not p or not q or len(p) != len(q):
        return 0.0
    s = 0.0
    for a, b in zip(p, q):
        a = float(max(1e-12, a))
        b = float(max(1e-12, b))
        s += a * math.log(a / b)
    return max(0.0, s)

def _minmax(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return (float(x) - float(lo)) / float(hi - lo)

def _hashable(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(obj)


# ----------------------------- Datenklassen ----------------------------------

@dataclass
class CuriositySignal:
    """Gesamtscore + Komponenten, bereits in [0..1] normalisiert."""
    signal: float
    components: Dict[str, float] = field(default_factory=dict)

    def clamp(self, lo: float = 0.0, hi: float = 1.0) -> "CuriositySignal":
        s = float(max(lo, min(hi, self.signal)))
        comp = {k: float(max(lo, min(hi, v))) for k, v in (self.components or {}).items()}
        return CuriositySignal(s, comp)

    def scale(self, factor: float) -> "CuriositySignal":
        s = float(self.signal) * float(factor)
        comp = {k: float(v) * float(factor) for k, v in (self.components or {}).items()}
        return CuriositySignal(s, comp)


class CuriosityBands:
    @staticmethod
    def classify(sig: float) -> str:
        if sig >= 0.66:
            return "high"
        if sig >= 0.33:
            return "mid"
        return "low"


# ----------------------------- Schema ----------------------------------------

def ensure_schema() -> None:
    if not _SQL_OK:
        return
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS curiosity_log (
            id INTEGER PRIMARY KEY,
            created_at INTEGER,
            source TEXT,
            signal REAL,
            raw TEXT,
            tag TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_curi_src_time ON curiosity_log(source, created_at)")
    conn.commit()


# ----------------------------- Kernfunktion ----------------------------------

def curiosity_score(
    pred: Optional[Sequence[float]] = None,
    obs: Optional[Sequence[float]] = None,
    pe_range: Tuple[float, float] = (0.0, 5.0),
    last_logits: Optional[Sequence[float]] = None,
    new_logits: Optional[Sequence[float]] = None,
    prior_probs: Optional[Sequence[float]] = None,
    post_probs: Optional[Sequence[float]] = None,
    seen_count: Optional[int] = None,
    novelty_range: Tuple[int, int] = (0, 10),
    w_pe: float = 0.45,
    w_ent: float = 0.20,
    w_kl: float = 0.20,
    w_nov: float = 0.15,
) -> CuriositySignal:
    """
    Liefert CuriositySignal (0..1) aus gewichteter Mischung:
      - pe_norm: Prediction Error
      - dH_norm: Entropie-Change
      - kl_norm: KL-Divergenz
      - nov_norm: Novelty
    """
    pe_norm = 0.0
    if pred is not None and obs is not None and len(pred) == len(obs) and len(pred) > 0:
        pe = _l2(_to_vec(pred), _to_vec(obs))
        pe_norm = float(max(0.0, min(1.0, _minmax(pe, pe_range[0], pe_range[1]))))

    dH_norm = 0.0
    if last_logits is not None and new_logits is not None and len(last_logits) == len(new_logits):
        p_last = _softmax(last_logits)
        p_new = _softmax(new_logits)
        dH = abs(_entropy(p_new) - _entropy(p_last))
        dH_norm = float(max(0.0, min(1.0, _minmax(dH, 0.0, 3.0))))

    kl_norm = 0.0
    if prior_probs is not None and post_probs is not None and len(prior_probs) == len(post_probs):
        klv = _kl(prior_probs, post_probs)
        kl_norm = float(max(0.0, min(1.0, _minmax(klv, 0.0, 2.0))))

    nov_norm = 0.0
    if seen_count is not None:
        s = int(max(0, seen_count))
        hi = max(novelty_range[1], novelty_range[0] + 1)
        nov_norm = float(max(0.0, min(1.0, 1.0 - _minmax(s, novelty_range[0], hi))))

    wsum = float(w_pe + w_ent + w_kl + w_nov) or 1.0
    w_pe, w_ent, w_kl, w_nov = [float(w) / wsum for w in (w_pe, w_ent, w_kl, w_nov)]

    sig = (w_pe * pe_norm) + (w_ent * dH_norm) + (w_kl * kl_norm) + (w_nov * nov_norm)
    comps = {"pe": pe_norm, "entropy": dH_norm, "kl": kl_norm, "novelty": nov_norm}
    return CuriositySignal(signal=float(max(0.0, min(1.0, sig))), components=comps)


# ----------------------------- Logger ----------------------------------------

class CuriosityLogger:
    def __init__(self):
        ensure_schema()

    def log(self, source: str, sig: CuriositySignal, tag: Optional[str] = None, ts: Optional[int] = None) -> int:
        if not _SQL_OK:
            raise RuntimeError("sql_manager nicht verfügbar")
        ensure_schema()
        ts_i = int(ts or time.time())
        raw_json = json.dumps(sig.components or {}, ensure_ascii=False, separators=(",", ":"))
        tag_v = str(tag) if tag else None

        # Writes müssen im Single-Writer-Modus über den DBWriter laufen, um globale
        # SQLite-Write-Kollisionen zu vermeiden. Wenn DBWriter aktiv ist, gibt es
        # keinen lokalen Fallback mehr.
        if _dbw_enabled():
            try:
                rid = int(db_writer_client.exec_lastrowid(
                    """INSERT INTO curiosity_log(created_at, source, signal, raw, tag) VALUES (?, ?, ?, ?, ?)""",
                    [ts_i, str(source), float(sig.signal), raw_json, tag_v],
                    tag="curiosity.log",
                    priority="low",
                    timeout_ms=int(os.environ.get("OROMA_DBW_CURIOSITY_TIMEOUT_MS", "2000")),
                    db="oroma",
                ))
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.curiosity.dbw.fail.1",
                    exc=e,
                    msg="DBWriter curiosity_log write failed; skip (no local fallback).",
                    level=logging.WARNING,
                    interval_s=60,
                )
                return -1
        else:
            rid = -1

        if rid < 0:
            # Im DBWriter-Modus niemals lokal schreiben. Wenn DBWriter deaktiviert ist,
            # bleibt der lokale Write-Pfad als Legacy-/Offline-Modus erhalten.
            if _dbw_enabled():
                return -1
            conn = None
            try:
                with sql_manager.writer_lock("curiosity.log", timeout_sec=1):
                    conn = sql_manager.get_conn()
                    cur = conn.cursor()
                    cur.execute(
                        """INSERT INTO curiosity_log(created_at, source, signal, raw, tag) VALUES (?, ?, ?, ?, ?)""",
                        (ts_i, str(source), float(sig.signal), raw_json, tag_v),
                    )
                    conn.commit()
                    rid = int(cur.lastrowid)
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass

        try:
            sql_manager.insert_metric(f"curiosity_{source}", float(sig.signal))
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.curiosity.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
        return rid


# ----------------------------- Quick-Adapter ---------------------------------

def log_curiosity_generic(
    logger: CuriosityLogger,
    *,
    source: str,
    pred: Optional[Sequence[float]] = None,
    obs: Optional[Sequence[float]] = None,
    last_logits: Optional[Sequence[float]] = None,
    new_logits: Optional[Sequence[float]] = None,
    prior_probs: Optional[Sequence[float]] = None,
    post_probs: Optional[Sequence[float]] = None,
    seen_count: Optional[int] = None,
    tag: Optional[str] = None,
) -> Tuple[int, CuriositySignal]:
    sig = curiosity_score(
        pred=pred, obs=obs,
        last_logits=last_logits, new_logits=new_logits,
        prior_probs=prior_probs, post_probs=post_probs,
        seen_count=seen_count,
    )
    rid = logger.log(source, sig, tag=tag)
    return rid, sig


# ----------------------------- Selftest --------------------------------------

def _selftest() -> None:
    print("[curiosity] selftest…")
    logger = CuriosityLogger()

    r1, s1 = log_curiosity_generic(
        logger, source="testA",
        pred=[0.1, 0.2, 0.3], obs=[1.1, 1.2, 1.3],
        seen_count=0, tag="train"
    )
    print("  testA:", r1, s1.signal, s1.components)

    r2, s2 = log_curiosity_generic(
        logger, source="testB",
        last_logits=[1.0, 0.5, -0.1], new_logits=[-0.5, 0.5, 1.2],
        seen_count=3, tag="eval"
    )
    print("  testB:", r2, s2.signal, s2.components)

    r3, s3 = log_curiosity_generic(
        logger, source="testC",
        prior_probs=[0.8, 0.1, 0.1], post_probs=[0.4, 0.3, 0.3],
        seen_count=7, tag="rollout"
    )
    print("  testC:", r3, s3.signal, s3.components)

    print("[curiosity] OK ✅")

if __name__ == "__main__":
    _selftest()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/calc_to_snapchain.py
# Projekt: ORÓMA – Calculator → SnapChain Bridge (Transfer-Wissen)
# Version: v3.7.3
# Stand:   2025-12-14
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Motivation / Zweck
# ─────────────────
#   Der Calculator ist (wie du es von Anfang an wolltest) ein “Struktur-Hub”:
#   Mathematische Muster sind in der Theorie überall vorhanden → daher eignet
#   sich der Calculator hervorragend, um sehr früh “kanonische” Konzepte zu
#   erzeugen, die später crossmodal verknüpft werden können.
#
#   Damit Sonnet’s Zielbild nicht nur Theorie ist, wird hier eine robuste,
#   schema-sichere Bridge implementiert:
#
#     calculator_tasks/results
#        ↓
#     SnapChain (snapchains) mit origin="calc/result" und JSON-Blob
#        ↓
#     (optional) MetaSnap-Aggregation in meta_snaps (label="calc:<type>[:<skill>]")
#
#   Wichtig:
#   • Keine DB-Schema-Änderung.
#   • Keine Hintergrund-Threads.
#   • Fail-safe: Wenn irgendwas schiefgeht, darf der Calculator NICHT crashen.
#
# SnapChain-Format (JSON)
# ──────────────────────
#   {
#     "kind": "calc/result",
#     "v": [...],                 # fester Vektor (Default: 84D, kompatibel zur Vision-Token-VDim)
#     "task_id": 123,
#     "result_id": 456,
#     "ts": 17657...,
#     "level": 7,
#     "expr": "fill: ? + 16 = 24",
#     "truth": 8.0,
#     "got": 8.0,
#     "correct": 1,
#     "reward": 0.1,
#     "error_type": null,
#     "truth_json": {...} | null,
#     "got_json": [...] | {...} | null,
#     "meta": {"type": "...", "skill":"...", ...} | null
#   }
#
# ENV (optional)
# ──────────────
#   OROMA_CALC_SNAPCHAINS          (true|false)  Default: true
#   OROMA_CALC_SNAP_EVERY          (int>=1)      Default: 1  (jede Lösung wird als SnapChain geloggt)
#   OROMA_CALC_SNAP_VDIM           (int>=16)     Default: 84
#   OROMA_CALC_METASNAP_AGG        (true|false)  Default: true
#
# Hinweise / Grenzen (ehrlich)
# ───────────────────────────
#   Diese Bridge erzeugt jetzt einen fassbaren gemeinsamen “Container” im
#   SnapSystem. Das ist die Voraussetzung für Transfer.
#   ABER: Ein gemeinsamer Vektorraum ist damit noch NICHT “magisch” gelernt.
#   Die v-Features sind absichtlich deterministisch/kanonisch, damit Dream/Linker
#   später verlässlich alignen kann (z.B. Co-Occurrence, Reward-Kopplung, etc.).
#
# =============================================================================

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional, Tuple, List

from core import sql_manager


import logging
from core import log_guard
logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# ENV
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool = True) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    s = str(v).strip().lower()
    return s not in ("0", "false", "no", "off")

def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None:
        return int(default)
    try:
        return int(str(v).strip())
    except Exception:
        return int(default)

_ENABLED = _env_bool("OROMA_CALC_SNAPCHAINS", True)
_SNAP_EVERY = max(1, _env_int("OROMA_CALC_SNAP_EVERY", 1))
_VDIM = max(16, _env_int("OROMA_CALC_SNAP_VDIM", 84))
_METASNAP_AGG = _env_bool("OROMA_CALC_METASNAP_AGG", True)


# ---------------------------------------------------------------------------
# Helpers: safe JSON parsing
# ---------------------------------------------------------------------------

def _json_loads_safe(s: Optional[str]) -> Optional[Any]:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

def _json_dumps_compact(o: Any) -> bytes:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Feature extraction: deterministic “canonical math features”
# ---------------------------------------------------------------------------

_RE_NUM = re.compile(r"(?<![\w.])(-?\d+)(?![\w.])")

def _infer_type(expr: str, meta: Optional[Dict[str, Any]]) -> str:
    if isinstance(meta, dict):
        t = meta.get("type")
        if isinstance(t, str) and t.strip():
            return t.strip().lower()
    e = (expr or "").strip().lower()
    if e.startswith("seq:"):
        return "sequence"
    if e.startswith("fill:"):
        return "fill"
    if "/" in e and "+" in e:
        return "fraction_add"
    if "/" in e and "-" in e:
        return "fraction_sub"
    if "?" in e and ("=" in e):
        return "fill"
    if any(op in e for op in ("+", "-", "*", "/")):
        return "arith"
    return "other"

def _infer_skill(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    s = meta.get("skill")
    return s if isinstance(s, str) and s.strip() else None

def _first_two_ints(expr: str) -> Tuple[Optional[int], Optional[int]]:
    nums = _RE_NUM.findall(expr or "")
    if not nums:
        return None, None
    a = int(nums[0])
    b = int(nums[1]) if len(nums) > 1 else None
    return a, b

def _norm_tanh(x: float, scale: float = 50.0) -> float:
    try:
        import math
        return float(math.tanh(float(x) / float(scale)))
    except Exception:
        return 0.0

def _one_hot(idx: int, size: int) -> List[float]:
    v = [0.0] * size
    if 0 <= idx < size:
        v[idx] = 1.0
    return v

def _build_v(expr: str,
             level: int,
             truth: float,
             got: float,
             correct: int,
             reward: float,
             meta: Optional[Dict[str, Any]]) -> List[float]:
    """
    Erzeugt einen festen Vektor der Länge _VDIM.
    Ziel: deterministisch/kanonisch, nicht “magisch semantisch”.
    Später kann ein Dream/Linker diesen Raum mit anderen Modalitäten alignen.
    """
    v = [0.0] * _VDIM

    # [0..6): Kontinuierliche Basisfeatures
    v[0] = 1.0                                  # bias
    v[1] = 1.0 if int(correct) else 0.0          # correctness
    v[2] = float(reward)                         # reward
    v[3] = _norm_tanh(float(truth), 50.0)        # truth normalized
    v[4] = _norm_tanh(float(got), 50.0)          # got normalized
    v[5] = _norm_tanh(abs(float(truth) - float(got)), 25.0)  # abs error normalized

    # [6..16): Task-Type OneHot (10)
    typ = _infer_type(expr, meta)
    type_map = {
        "arith": 0,
        "fill": 1,
        "sequence": 2,
        "fraction_add": 3,
        "fraction_sub": 4,
        "cmp": 5,
        "puzzle": 6,
        "const": 7,
        "logic": 8,
        "other": 9,
    }
    t_idx = type_map.get(typ, 9)
    oh_t = _one_hot(t_idx, 10)
    for i in range(10):
        if 6 + i < _VDIM:
            v[6 + i] = oh_t[i]

    # [16..26): Level OneHot (10) – clamp 1..10
    lvl = int(level)
    lvl_idx = max(0, min(9, (lvl - 1)))
    oh_l = _one_hot(lvl_idx, 10)
    for i in range(10):
        if 16 + i < _VDIM:
            v[16 + i] = oh_l[i]

    # [26..36): solution digit onehot (10), nur wenn “eher ganzzahlig”
    sol = int(round(float(truth))) if abs(float(truth) - round(float(truth))) < 1e-9 else None
    if sol is not None:
        d = abs(sol) % 10
        oh_d = _one_hot(d, 10)
        for i in range(10):
            if 26 + i < _VDIM:
                v[26 + i] = oh_d[i]

    # [36..46) & [46..56): erste zwei Zahlen aus expr (mod 10 onehot)
    a, b = _first_two_ints(expr)
    if a is not None:
        oh_a = _one_hot(abs(int(a)) % 10, 10)
        for i in range(10):
            if 36 + i < _VDIM:
                v[36 + i] = oh_a[i]
    if b is not None:
        oh_b = _one_hot(abs(int(b)) % 10, 10)
        for i in range(10):
            if 46 + i < _VDIM:
                v[46 + i] = oh_b[i]

    # Rest bleibt 0.0 – bewusst stabil & erweiterbar.
    return v


# ---------------------------------------------------------------------------
# DB Reads
# ---------------------------------------------------------------------------

def _fetch_task(task_id: int) -> Optional[Dict[str, Any]]:
    try:
        with sql_manager.get_conn() as conn:
            conn.row_factory = None
            row = conn.execute(
                "SELECT id, ts, level, expr, truth, truth_json FROM calculator_tasks WHERE id=?",
                (int(task_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "ts": int(row[1]),
            "level": int(row[2]),
            "expr": str(row[3]),
            "truth": float(row[4]),
            "truth_json": row[5],
        }
    except Exception:
        return None

def _fetch_result(result_id: int) -> Optional[Dict[str, Any]]:
    try:
        with sql_manager.get_conn() as conn:
            conn.row_factory = None
            row = conn.execute(
                "SELECT id, task_id, ts, got, correct, reward, error_type, got_json "
                "FROM calculator_results WHERE id=?",
                (int(result_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "task_id": int(row[1]),
            "ts": int(row[2]),
            "got": float(row[3]),
            "correct": int(row[4]),
            "reward": float(row[5]),
            "error_type": row[6],
            "got_json": row[7],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# MetaSnap Aggregation (optional)
# ---------------------------------------------------------------------------

def _metasnap_upsert(label: str,
                     correct: int,
                     reward: float,
                     last_expr: str,
                     last_task_id: int,
                     last_ts: int) -> None:
    """
    Aggregiert pro Label eine kleine Statistik in meta_snaps.
    Kein neues Schema: wir schreiben in meta_snaps.score + meta_snaps.sources (JSON).
    """
    if not _METASNAP_AGG:
        return

    try:
        with sql_manager.get_conn() as conn:
            conn.row_factory = None
            row = conn.execute(
                "SELECT id, score, sources FROM meta_snaps WHERE label=? ORDER BY id DESC LIMIT 1",
                (str(label),),
            ).fetchone()

            if not row:
                # Neu anlegen
                payload = {
                    "kind": "calc_metasnap",
                    "label": label,
                    "count": 1,
                    "correct_count": int(correct),
                    "reward_sum": float(reward),
                    "reward_avg": float(reward),
                    "last_task_id": int(last_task_id),
                    "last_ts": int(last_ts),
                    "sample_expr": str(last_expr)[:160],
                }
                conn.execute(
                    "INSERT INTO meta_snaps (label, score, sources) VALUES (?,?,?)",
                    (str(label), float(reward), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                )
                conn.commit()
                return

            mid = int(row[0])
            score_old = float(row[1] if row[1] is not None else 0.0)
            src_old = row[2]
            src = _json_loads_safe(src_old) if isinstance(src_old, str) else None
            if not isinstance(src, dict):
                src = {"kind": "calc_metasnap", "label": label, "count": 0, "correct_count": 0, "reward_sum": 0.0}

            cnt = int(src.get("count", 0)) + 1
            cc = int(src.get("correct_count", 0)) + int(correct)
            rsum = float(src.get("reward_sum", 0.0)) + float(reward)
            ravg = rsum / float(cnt) if cnt > 0 else 0.0

            src.update({
                "count": cnt,
                "correct_count": cc,
                "reward_sum": rsum,
                "reward_avg": ravg,
                "last_task_id": int(last_task_id),
                "last_ts": int(last_ts),
                "sample_expr": str(last_expr)[:160],
            })

            # Score als reward_avg (transparent)
            conn.execute(
                "UPDATE meta_snaps SET score=?, sources=? WHERE id=?",
                (float(ravg), json.dumps(src, ensure_ascii=False, separators=(",", ":")), int(mid)),
            )
            conn.commit()

    except Exception:
        # niemals den Calculator blockieren
        return


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_from_db(*, task_id: int, result_id: int) -> Optional[int]:
    """
    Hauptfunktion: holt task+result aus DB und schreibt SnapChain.
    Rückgabe: snapchain_id oder None.
    """
    if not _ENABLED:
        return None

    # Throttling: nur jeden N-ten Result (stabil, da result_id monoton steigt)
    if _SNAP_EVERY > 1:
        try:
            if int(result_id) % int(_SNAP_EVERY) != 0:
                return None
        except Exception as e:
            log_guard.log_suppressed(logger, key="calc_to_snapchain.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    t = _fetch_task(int(task_id))
    r = _fetch_result(int(result_id))
    if not t or not r:
        return None

    expr = str(t["expr"])
    level = int(t["level"])
    truth = float(t["truth"])
    got = float(r["got"])
    correct = int(r["correct"])
    reward = float(r["reward"])
    ts = int(r["ts"])

    meta = _json_loads_safe(t.get("truth_json"))
    if meta is not None and not isinstance(meta, dict):
        # truth_json kann historisch auch list sein → wir behandeln es als “meta=None”
        meta = None

    v = _build_v(expr=expr, level=level, truth=truth, got=got, correct=correct, reward=reward, meta=meta)

    blob_obj: Dict[str, Any] = {
        "kind": "calc/result",
        "v": v,
        "task_id": int(task_id),
        "result_id": int(result_id),
        "ts": ts,
        "level": level,
        "expr": expr,
        "truth": truth,
        "got": got,
        "correct": correct,
        "reward": reward,
        "error_type": r.get("error_type"),
        "truth_json": meta if isinstance(meta, dict) else None,
        "got_json": _json_loads_safe(r.get("got_json")) if isinstance(r.get("got_json"), str) else None,
        "meta": meta if isinstance(meta, dict) else None,
    }

    # Qualitätsheuristik: nicht zu hoch, aber differenziert
    q = 0.65 if correct else 0.15
    w = 0.35 if correct else 0.50

    snap_id: Optional[int] = None
    try:
        snap_id = sql_manager.insert_snapchain({
            "ts": ts,
            "quality": float(q),
            "blob": _json_dumps_compact(blob_obj),
            "origin": "calc/result",
            "notes": "Calculator result → SnapChain (transfer bridge)",
            "version": "v3.7.3",
            "weight": float(w),
        })
    except Exception:
        snap_id = None

    # MetaSnap-Aggregation: label nach type/skill
    try:
        typ = _infer_type(expr, meta)
        skill = _infer_skill(meta)
        label = f"calc:{typ}:{skill}" if skill else f"calc:{typ}"
        _metasnap_upsert(
            label=label,
            correct=correct,
            reward=reward,
            last_expr=expr,
            last_task_id=int(task_id),
            last_ts=ts,
        )
    except Exception as e:
        log_guard.log_suppressed(logger, key="calc_to_snapchain.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    return snap_id
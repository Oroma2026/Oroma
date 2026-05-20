#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/setcalc.py
# Projekt: ORÓMA v3.6 Patch 2 (SetCalc – Mengenlehre + Mengenleere)
# Stand:   2025-09-28
#
# Zweck
# ─────
#  - Kernfunktionen für Mengenlehre:
#      • Vereinigung, Schnitt, Differenz, Komplement (bezogen auf Universum U)
#      • Potenzmenge, Kartesisches Produkt
#      • Venn-Zählwerte (|A|, |B|, |A∩B|)
#  - Erweiterung Patch 2b: Mengenleere (∅) wird explizit als Symbol zurückgegeben,
#    statt als leere Liste – erleichtert Lernen und UI.
#
# Sicherheit / Grenzen
# ────────────────────
#  - Kartesisches Produkt wird bei >500 Paaren gekappt (Performance/UI).
# =============================================================================

from __future__ import annotations
from typing import Any, Dict, Iterable, List, Tuple
import json
import time
from core.log_guard import log_suppressed
import logging

# Optionales DB-Logging lazy import
_sql = None
def _get_sql():
    global _sql
    if _sql is None:
        try:
            from core import sql_manager
            _sql = sql_manager
        except Exception:
            _sql = None
    return _sql

# ---------- Parsing & Helpers -------------------------------------------------

def _to_scalar(x: str) -> Any:
    """Versucht einfache Typisierung (int/float), sonst String-Trim."""
    s = str(x).strip()
    if s == "":
        return ""
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        # Float
        return float(s)
    except Exception:
        return s

def parse_set(inp: Any) -> List[Any]:
    """
    Akzeptiert:
      - Liste/Tuple: wird flach in list gecastet
      - String: "a,b,c" → ['a','b','c'] (mit Auto-Koercion von Zahlen)
      - JSON-String: '["a", "b"]' → ['a','b']
    Liefert Liste ohne Duplikate (Set-Semantik), stabile Sortierung (str repr).
    """
    if inp is None:
        return []
    if isinstance(inp, (list, tuple, set, frozenset)):
        items = list(inp)
    else:
        s = str(inp).strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                arr = json.loads(s)
                items = list(arr) if isinstance(arr, list) else [arr]
            except Exception:
                items = [s]
        else:
            parts = [p for p in s.split(",")]
            items = [p for p in (pp.strip() for pp in parts) if p != ""]
    # koerzieren & unique
    seen = set()
    out = []
    for it in items:
        v = _to_scalar(it)
        key = json.dumps(v, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(v)
    # stabile Sortierung nach Textdarstellung
    out.sort(key=lambda z: str(z))
    return out

def _as_set(lst: List[Any]) -> set:
    return set(lst)

def _sorted_list(s: Iterable[Any]) -> List[Any]:
    return sorted(list(s), key=lambda z: str(z))

def _empty_if_needed(lst: List[Any]) -> Any:
    """Gibt ∅ zurück, wenn Liste leer ist, sonst die Liste selbst."""
    return "∅" if not lst else lst

# ---------- Operationen -------------------------------------------------------

def op_union(A: Any, B: Any) -> Any:
    a = _as_set(parse_set(A))
    b = _as_set(parse_set(B))
    return _empty_if_needed(_sorted_list(a | b))

def op_intersection(A: Any, B: Any) -> Any:
    a = _as_set(parse_set(A))
    b = _as_set(parse_set(B))
    return _empty_if_needed(_sorted_list(a & b))

def op_difference(A: Any, B: Any) -> Any:
    a = _as_set(parse_set(A))
    b = _as_set(parse_set(B))
    return _empty_if_needed(_sorted_list(a - b))

def op_complement(A: Any, U: Any) -> Any:
    a = _as_set(parse_set(A))
    u = _as_set(parse_set(U))
    return _empty_if_needed(_sorted_list(u - a))

def op_powerset(A: Any, limit: int = 256) -> List[Any]:
    """
    Potenzmenge als Liste von Teilmengen (jede Teilmenge ist Liste).
    Bei großen A wird hart gekappt (limit), um UI/JSON zu schützen.
    """
    arr = parse_set(A)
    n = len(arr)
    max_sets = 1 << n
    out: List[List[Any]] = []
    # harte Kappung (z. B. n>8 → 256+)
    cap = min(max_sets, limit)
    for mask in range(cap):
        subset = [arr[i] for i in range(n) if (mask >> i) & 1]
        out.append(subset)
    return _empty_if_needed(out)

def op_cartesian(A: Any, B: Any, cap: int = 500) -> Any:
    a = parse_set(A)
    b = parse_set(B)
    pairs: List[Tuple[Any, Any]] = []
    for x in a:
        for y in b:
            pairs.append((x, y))
            if len(pairs) >= cap:
                return _empty_if_needed(pairs)
    return _empty_if_needed(pairs)

def venn_counts(A: Any, B: Any) -> Dict[str, int]:
    a = _as_set(parse_set(A))
    b = _as_set(parse_set(B))
    return {
        "sizeA": len(a),
        "sizeB": len(b),
        "sizeAB": len(a & b),
    }

# ---------- Logging -----------------------------------------------------------

def log(op: str, setA: Any = None, setB: Any = None, result: Any = None) -> None:
    sm = _get_sql()
    if not sm:
        return
    try:
        # Für DB: "∅" → leere Liste, damit JSON konsistent bleibt
        res_db = [] if result == "∅" else result
        with sm.get_conn() as conn:
            conn.execute(
                "INSERT INTO setcalc_log (ts, op, setA, setB, result) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()),
                 str(op),
                 json.dumps(parse_set(setA), ensure_ascii=False),
                 json.dumps(parse_set(setB), ensure_ascii=False) if setB is not None else None,
                 json.dumps(res_db, ensure_ascii=False))
            )
            conn.commit()
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.setcalc.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

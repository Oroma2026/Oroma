#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/scicalc.py
# Projekt: ORÓMA v3.6 Patch 1 (SciCalc + Charts)
# Stand:   2025-09-28
#
# Zweck
# ─────
#  Backend-Logik für den wissenschaftlichen Taschenrechner (SciCalc).
#  Stellt symbolische und numerische Berechnungen über SymPy bereit:
#    • evaluate(expr, x)       – Funktionsauswertung
#    • find_roots(expr,[a,b])  – Nullstellen im Intervall
#    • taylor_expand(expr,x0,n)– Taylor-Reihenentwicklung
#    • limit(expr,x→a)         – Grenzwerte (LIM)
#    • plot_points(expr,[a,b]) – Datenpunkte für Liniendiagramme
#    • make_bar(data)          – JSON für Balkendiagramme
#    • make_pie(data)          – JSON für Tortendiagramme
#
# API-Anbindung
# ─────────────
#  - Wird von ui/scicalc_ui.py genutzt (Blueprint /scicalc).
#  - Alle Funktionen liefern Python-Objekte (float, str, dict),
#    die direkt als JSON serialisierbar sind.
#
# Chart-Integration
# ─────────────────
#  - plot_points → Chart.js LineChart
#  - make_bar    → Chart.js BarChart
#  - make_pie    → Chart.js PieChart
#
# Optionale DB-Integration
# ────────────────────────
#  - log_result(sql, expr, method, result, extra="")
#  - Erwartet Tabelle scicalc_results:
#
#    CREATE TABLE IF NOT EXISTS scicalc_results (
#      id INTEGER PRIMARY KEY AUTOINCREMENT,
#      ts INTEGER NOT NULL,
#      expr TEXT,
#      method TEXT,
#      result TEXT,
#      extra TEXT
#    );
#
# Hinweise
# ────────
#  - Nutzt SymPy (sympy>=1.13), daher abhängig von dessen Parser.
#  - Fehlerhafte Ausdrücke werden in _safe_parse() abgefangen.
#  - Nicht-destruktiv: ergänzt v3.6 um Patch 1 (SciCalc+Charts).
# =============================================================================

import sympy as sp
import time
from typing import List, Dict, Any
from core.log_guard import log_suppressed
import logging


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_parse(expr: str):
    x = sp.symbols("x")
    try:
        f = sp.sympify(expr)
        return f, x
    except Exception as e:
        raise ValueError(f"Ungültiger Ausdruck: {expr} ({e})")


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def evaluate(expr: str, xval: float) -> float:
    f, x = _safe_parse(expr)
    return float(f.evalf(subs={x: xval}))


def find_roots(expr: str, xmin: float = -10, xmax: float = 10) -> List[float]:
    f, x = _safe_parse(expr)
    sol = sp.solveset(f, x, domain=sp.Interval(xmin, xmax))
    roots = []
    for r in sol:
        try:
            roots.append(float(r))
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.scicalc.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
    return sorted(roots)


def taylor_expand(expr: str, x0: float = 0, n: int = 5) -> str:
    f, x = _safe_parse(expr)
    series = sp.series(f, x, x0, n+1)
    return str(series.removeO())


def plot_points(expr: str, xmin: float = -10, xmax: float = 10, steps: int = 200) -> Dict[str, Any]:
    f, x = _safe_parse(expr)
    xs = [xmin + i*(xmax-xmin)/steps for i in range(steps+1)]
    ys = [float(f.evalf(subs={x: xi})) for xi in xs]
    return {"x": xs, "y": ys}


def limit(expr: str, xval: str, to: float) -> Any:
    f, x = _safe_parse(expr)
    try:
        lim = sp.limit(f, x, to)
        return str(lim)
    except Exception as e:
        return f"Limit-Fehler: {e}"


def make_bar(data: Dict[str, float]) -> Dict[str, Any]:
    """Erzeugt JSON für Balkendiagramm."""
    return {
        "labels": list(data.keys()),
        "values": list(data.values())
    }


def make_pie(data: Dict[str, float]) -> Dict[str, Any]:
    """Erzeugt JSON für Tortendiagramm."""
    return {
        "labels": list(data.keys()),
        "values": list(data.values())
    }


# ----------------------------------------------------------------------
# Optionale DB-Integration (Logging)
# ----------------------------------------------------------------------
def log_result(sql, expr: str, method: str, result: str, extra: str = ""):
    sql.execute(
        "INSERT INTO scicalc_results (ts, expr, method, result, extra) VALUES (?,?,?,?,?)",
        (int(time.time()), expr, method, result, extra)
    )
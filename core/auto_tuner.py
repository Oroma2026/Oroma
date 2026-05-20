#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/auto_tuner.py
# Projekt:   ORÓMA – Offline-First Edge-KI (Headless)
# Modul:     AutoTuner – einfache, auditierbare Parameter-Vorschläge (Heuristik ±10%) + SQLite Log (tuning_suggestions)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert einen bewusst einfachen „AutoTuner“ für ORÓMA.
# Es generiert Parameter-Vorschläge (Suggest) für numerische Werte und protokolliert
# diese Vorschläge in SQLite (oroma.db), damit die Entscheidungen auditierbar bleiben.
#
# Wichtig (Architekturrolle):
# - Der AutoTuner ist *nicht* der Policy- oder Decision-Engine.
# - Er ist ein kleines Hilfsmodul, das „Feinjustierung“ ermöglichen soll:
#     • exploration_rate
#     • learning_rate
#     • thresholds / window sizes
#     • heuristische Parameter in Hooks
#
# Der AutoTuner selbst ändert **keine** globalen Parameter im System automatisch.
# Er erzeugt Vorschläge + schreibt Log; die Anwendung/Übernahme erfolgt durch den Caller
# (z. B. Orchestrator/Hooks/UI/Experiment-Layer).
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: nur stdlib + core.sql_manager (optional).
# - Robust: wenn sql_manager nicht importierbar ist, läuft das Modul im „No-DB Mode“:
#     • suggest() liefert dennoch einen Wert zurück
#     • list_suggestions() liefert []
# - Auditierbar: jede Suggestion kann in DB nachgeschlagen werden (Zeitpunkt, Basis, Note).
# - Minimal: keine komplexen Optimierer, kein Gradient, kein ML – nur klare Heuristik.
#
# HEURISTIK (EXAKT)
# ─────────────────
# suggest(param, current, basis="manual", note=""):
# - erzeugt zufällig ±10% Variation:
#     factor = 1.1  (wenn random.random() > 0.5)
#          oder 0.9 (sonst)
# - new_val = current * factor
#
# Der Zufall ist bewusst „simpel“; reproduzierbare Varianten gehören in den Experiment-Layer
# (core/experiment.py) oder in deterministische Tests.
#
# DB / SCHEMA (EXAKT IM CODE)
# ───────────────────────────
# Wenn core.sql_manager verfügbar ist, wird folgende Tabelle angelegt:
#
#   CREATE TABLE IF NOT EXISTS tuning_suggestions(
#       id         INTEGER PRIMARY KEY,
#       created_at INTEGER,
#       param      TEXT,
#       current    REAL,
#       suggested  REAL,
#       basis      TEXT,
#       note       TEXT
#   );
#
# Schreibpfad:
# - suggest() führt INSERT in tuning_suggestions aus, wenn _SQL_OK True ist.
#
# Lesepfad:
# - list_suggestions(limit=50) liest:
#     SELECT id,created_at,param,current,suggested,basis,note
#     FROM tuning_suggestions
#     ORDER BY id DESC
#     LIMIT ?
# - Rückgabe ist eine Liste aus Dicts mit genau diesen Feldern.
#
# SQL-MODUS / CORE-INTEGRATION
# ────────────────────────────
# Dieses Modul setzt ORÓMA_BASE und fügt es ggf. zu sys.path hinzu, damit:
# - `from core import sql_manager` funktioniert, auch wenn es direkt als Script läuft.
#
# ENV / KONFIGURATION (EXAKT)
# ───────────────────────────
# OROMA_BASE (default: "/opt/ai/oroma")
# - wird genutzt, um sys.path zu ergänzen (Script-Mode).
#
# Keine weiteren ENV-Schalter werden in diesem Modul ausgewertet.
# DB-Pfad, WAL/busy_timeout usw. kommen ausschließlich aus core.sql_manager.
#
# ÖFFENTLICHE API (FUNKTIONEN)
# ────────────────────────────
# ensure_schema() -> None
#   - legt tuning_suggestions an, wenn DB verfügbar (_SQL_OK True)
#
# suggest(param: str, current: float, basis: str="manual", note: str="") -> float
#   - erzeugt new_val (±10%) und loggt in tuning_suggestions (wenn DB verfügbar)
#   - gibt new_val zurück
#
# list_suggestions(limit: int=50) -> List[Dict[str,Any]]
#   - liefert letzte Vorschläge aus DB; wenn DB nicht verfügbar → []
#
# auto_tune(param_values: Dict[str,float]) -> Dict[str,float]
#   - iteriert über param_values und ruft suggest(..., basis="auto", note="periodic adjust")
#   - bei Fehlern bleibt Originalwert erhalten (try/except pro Parameter)
#
# _selftest() -> None
#   - lokaler Selftest (Script/Debug), erzeugt Beispielvorschläge und listet sie
#
# CLI / SCRIPT-NUTZUNG
# ────────────────────
# Dieses Modul ist primär als Import-Modul gedacht.
# Wenn es als Script ausgeführt wird, wird typischerweise _selftest() genutzt:
#   python3 /opt/ai/oroma/core/auto_tuner.py
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - suggest() muss auch ohne DB funktionieren (keine harte Abhängigkeit).
# - DB-Logging bleibt append-only (kein Update, kein Delete).
# - list_suggestions() muss stabile Keys liefern: id, created_at, param, current, suggested, basis, note.
# - auto_tune() darf niemals durch einen einzelnen Parameter crashen (per-item try/except).
# - Keine „automatische Anwendung“ im Modul selbst (nur Vorschlag + Log).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os, sys, time, random
from typing import Any, Dict, List

BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

_SQL_OK = True
try:
    from core import sql_manager  # type: ignore
    sql_manager.ensure_schema()
except Exception:
    _SQL_OK = False


# ---------------- Schema ----------------

def ensure_schema() -> None:
    """Tabelle tuning_suggestions idempotent anlegen."""
    if not _SQL_OK:
        return
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tuning_suggestions(
            id INTEGER PRIMARY KEY,
            created_at INTEGER,
            param TEXT,
            current REAL,
            suggested REAL,
            basis TEXT,
            note TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tune_param ON tuning_suggestions(param,created_at)")
    conn.commit()


# ---------------- Kernlogik --------------

def _now() -> int:
    return int(time.time())

def suggest(param: str, current: float, basis: str = "manual", note: str = "") -> float:
    """
    Erzeugt einen einfachen Vorschlag für 'param'.
    Heuristik: ±10% Variation.
    """
    ensure_schema()
    factor = 1.1 if random.random() > 0.5 else 0.9
    new_val = float(current) * factor
    if _SQL_OK:
        conn = sql_manager.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tuning_suggestions(created_at,param,current,suggested,basis,note) VALUES(?,?,?,?,?,?)",
            (_now(), str(param), float(current), float(new_val), basis, note)
        )
        conn.commit()
    return new_val


def list_suggestions(limit: int = 50) -> List[Dict[str, Any]]:
    """Liest die letzten Vorschläge aus der DB."""
    if not _SQL_OK:
        return []
    ensure_schema()
    conn = sql_manager.get_conn()
    conn.row_factory = None  # tuple statt Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id,created_at,param,current,suggested,basis,note FROM tuning_suggestions ORDER BY id DESC LIMIT ?",
        (int(limit),)
    )
    rows = cur.fetchall() or []
    return [
        {
            "id": r[0],
            "created_at": r[1],
            "param": r[2],
            "current": r[3],
            "suggested": r[4],
            "basis": r[5],
            "note": r[6],
        }
        for r in rows
    ]


# ---------------- High-Level --------------

def auto_tune(param_values: Dict[str,float]) -> Dict[str,float]:
    """
    Iteriert über param_values, erzeugt Vorschläge anhand einfacher Regeln.
    Beispiel: exploration, lr etc.
    """
    out = dict(param_values)
    for p,v in param_values.items():
        try:
            new_val = suggest(p, v, basis="auto", note="periodic adjust")
            out[p] = new_val
        except Exception:
            out[p] = v
    return out


# ---------------- Selftest ----------------

def _selftest() -> None:
    print("[auto_tuner] selftest...")
    params = {"lr": 0.01, "exploration": 0.2}
    tuned = auto_tune(params)
    print(" params in:", params)
    print(" tuned out:", tuned)
    sugg = list_suggestions(5)
    print(" suggestions:", sugg)
    print("[auto_tuner] OK ✅")

if __name__ == "__main__":
    _selftest()
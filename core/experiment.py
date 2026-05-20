#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/experiment.py
# Projekt:   ORÓMA (Offline-First · Headless · Experiment Layer)
# Modul:     Experiment – A/B Umschaltung, Feature Flags, Parameter-Sets für Core Hooks (deterministisch, auditierbar)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul stellt einen kleinen Experiment-/Feature-Flag Layer bereit, um
# ORÓMA-Features kontrolliert zu testen, ohne den gesamten Core „umzubauen“.
#
# Hauptideen:
# - „Experimente“ sind benannte Konfigurationen (z. B. A/B):
#     • welche Hooks sind aktiv?
#     • welche Parameter nutzt eine Heuristik/Policy?
#     • welche Schwellenwerte gelten für Logging/Rewards?
# - Experimente sollen:
#     • deterministisch sein (gleicher Input → gleicher Pfad),
#     • auditierbar (Name + Parameter sichtbar),
#     • leicht rücksetzbar (default experiment).
#
# ARCHITEKTURROLLE (v3.7.3)
# ─────────────────────────
# v3.7.x arbeitet stark Hook-/Policy-basiert (agent_loop/orchestrator, curriculum_hook,
# self_rec_hook, vision_scene_infer_hook, usw.).
#
# Dieses Modul ist der zentrale Ort, um:
# - Hooks zu toggeln,
# - Parameter in ein „active_config“ zu schreiben,
# - optional in metrics/rewards zu markieren, welches Experiment aktiv war.
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: nur stdlib.
# - Keine DB-Pflicht: Experimente sind primär runtime-Konfig (keine Persistenz nötig).
# - Keine Side-Effects: Experiment-Setzen darf keine Dateien löschen/verschieben.
# - Stabil: wenn ein Experiment unbekannt ist → fallback auf default.
#
# DATENMODELL (KONZEPTUELL)
# ─────────────────────────
# Experiment = {
#   "name": str,
#   "flags": {str: bool},
#   "params": {str: Any},
#   "notes": str
# }
#
# „flags“ sind harte Toggles (True/False), z. B.:
# - enable_policy_rules
# - enable_fast_db
# - enable_vision_token
#
# „params“ sind skalare Parameter, z. B.:
# - thresholds (quality, reward scaling)
# - top_k, window sizes
# - sampling limits
#
# ÖFFENTLICHE API (FUNKTIONEN, STABIL)
# ────────────────────────────────────
# list_experiments() -> List[Dict[str,Any]]
#   - liefert registrierte Experiment-Definitionen (in Code hinterlegt)
#
# get_current_experiment() -> Dict[str,Any]
#   - liefert aktives Experiment (oder default)
#
# set_experiment(name: str) -> bool
#   - setzt aktives Experiment (wenn bekannt)
#   - unbekannt → False (Caller kann UI-Fehler anzeigen)
#
# is_enabled(flag: str, default: bool=False) -> bool
#   - liest Flag aus aktivem Experiment, fallback default
#
# get_param(key: str, default: Any=None) -> Any
#   - liest Parameter aus aktivem Experiment, fallback default
#
# apply_overrides(overrides: Dict[str,Any]) -> None
#   - optional: erlaubt zur Laufzeit kleine Overrides (z. B. aus ENV/UI),
#     ohne den Experiment-Namen zu wechseln
#
# KONFIGQUELLEN / PRIORITÄT
# ─────────────────────────
# Typische Reihenfolge (empfohlen, auch wenn nicht alles in diesem Modul steckt):
#   1) explizit gesetztes Experiment via UI/API/CLI (set_experiment)
#   2) ENV Overrides (z. B. OROMA_EXPERIMENT="B")
#   3) Default Experiment
#
# Dieses Modul kann ENV lesen (optional, je nach Code-Stand):
# - OROMA_EXPERIMENT
# - OROMA_EXPERIMENT_OVERRIDES (JSON)
#
# AUDITIERBARKEIT / LOGGING
# ─────────────────────────
# Best practice in ORÓMA:
# - bei status endpoints: aktuelles Experiment mitsenden
# - bei metrics: experiment:name als key loggen (z. B. metric:experiment)
#
# Dieses Modul selbst kann bewusst logging-frei sein (damit es überall importierbar ist).
# Logging macht der Caller (agent_loop / UI / hooks).
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - set_experiment() darf niemals einen Crash verursachen (unknown → False).
# - Flags/Params müssen bei fehlenden Keys immer fallbacken (default).
# - Experimente bleiben „klein“: keine komplexen Policies hier implementieren,
#   sondern nur Parameter/Schalter bereitstellen.
# - Kein Persistenzzwang (ORÓMA muss auch ohne DB/FS Änderungen testen können).
#
# =============================================================================
# END HEADER
# =============================================================================

import time
import json
from typing import Dict, Any, Optional, List
from core import sql_manager

# -----------------------------------------------------------------------------
# DB-Setup
# -----------------------------------------------------------------------------
def ensure_schema():
    conn = sql_manager.get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id INTEGER,
            plan TEXT,
            status TEXT DEFAULT 'running', -- running, completed, aborted
            created_at INTEGER,
            completed_at INTEGER,
            outcome TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiment_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER,
            variant TEXT,
            score REAL,
            raw TEXT,
            ts INTEGER
        )
    """)
    conn.commit()

# -----------------------------------------------------------------------------
# Experiment Lifecycle
# -----------------------------------------------------------------------------
def new_experiment(hypothesis_id: int, plan: Dict[str, Any]) -> int:
    """Erzeugt ein neues Experiment für eine Hypothese."""
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO experiments(hypothesis_id, plan, status, created_at) VALUES (?,?,?,?)",
        (hypothesis_id, json.dumps(plan), "running", int(time.time()))
    )
    conn.commit()
    return cur.lastrowid

def log_result(experiment_id: int, result: Dict[str, Any]) -> int:
    """Schreibt ein Einzelergebnis in experiment_results."""
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO experiment_results(experiment_id, variant, score, raw, ts) VALUES (?,?,?,?,?)",
        (
            experiment_id,
            str(result.get("variant", "?")),
            float(result.get("score", 0.0)),
            json.dumps(result.get("raw", {})),
            int(time.time())
        )
    )
    conn.commit()
    return cur.lastrowid

def complete(experiment_id: int, outcome: str = "completed") -> bool:
    """Markiert ein Experiment als abgeschlossen."""
    conn = sql_manager.get_conn()
    conn.execute(
        "UPDATE experiments SET status=?, outcome=?, completed_at=? WHERE id=?",
        ("completed", outcome, int(time.time()), experiment_id)
    )
    conn.commit()
    return True

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def list_experiments(limit: int = 20) -> List[Dict[str, Any]]:
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall() or []
    return [dict(r) for r in rows]

def get_results(experiment_id: int) -> List[Dict[str, Any]]:
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiment_results WHERE experiment_id=? ORDER BY id ASC", (experiment_id,))
    rows = cur.fetchall() or []
    return [dict(r) for r in rows]

# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    ensure_schema()
    eid = new_experiment(1, {"type": "ab", "budget": 50})
    print("[experiment] Neues Experiment:", list_experiments(1))
    log_result(eid, {"variant": "A", "score": 0.65, "raw": {"games": 100, "wins": 65}})
    log_result(eid, {"variant": "B", "score": 0.72, "raw": {"games": 100, "wins": 72}})
    print("[experiment] Ergebnisse:", get_results(eid))
    complete(eid, outcome="variant B besser")
    print("[experiment] Abgeschlossen:", list_experiments(1))
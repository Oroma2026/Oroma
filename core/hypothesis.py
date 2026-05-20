#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/hypothesis.py
# Projekt:   ORÓMA (Offline-First · Headless · Research Loop)
# Modul:     Hypothesis Store – Hypothesen-Management (Research-Zyklus) in SQLite
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert ein sehr schlankes Hypothesen-Management für ORÓMA.
# Hypothesen sind hier bewusst als „Research-Artefakte“ definiert:
#   - Text + Status + Score + Confidence + last_tested (+ optionale Meta/Plan-Felder)
#   - Speicherung lokal in SQLite (oroma.db) über core.sql_manager.get_conn()
#
# Ziel in ORÓMA:
# - Gaps/Anomalien/Beobachtungen (z. B. aus diagnostics, learning curves, why-ui)
#   werden als Hypothesen festgehalten.
# - Hypothesen werden iterativ „getestet“ (manuell oder DreamWorker/Experiment-Tools),
#   wobei score/confidence/last_tested aktualisiert werden.
# - Das System bleibt auditierbar: keine magische Ableitung, nur nachvollziehbare Zustandsänderungen.
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine UI/Qt/Wayland/X11 Abhängigkeiten.
# - SQLite-first: Persistenz lokal, schnell, robust.
# - Minimalismus: bewusst keine komplexen wissenschaftlichen Modelle in diesem Modul.
# - Non-destructive: Es gibt bewusst keine „delete“-Operation. Hypothesen bleiben als Verlauf erhalten.
#
# DATENBANK / SCHEMA (EXAKT IM AKTUELLEN CODE)
# ───────────────────────────────────────────
# Dieses Modul legt die Tabelle selbst an (idempotent):
#
#   CREATE TABLE IF NOT EXISTS hypotheses (
#       id           INTEGER PRIMARY KEY AUTOINCREMENT,
#       text         TEXT NOT NULL,
#       status       TEXT DEFAULT 'open',   -- open, running, accepted, rejected
#       plan         TEXT,                 -- reserviert (z. B. JSON Testplan)
#       score        REAL DEFAULT 0.0,      -- frei: Effekt/Nutzen 0..1 oder domänenspezifisch
#       confidence   REAL DEFAULT 0.0,      -- frei: Evidenz/Konfidenz 0..1
#       last_tested  INTEGER,              -- Unix seconds; None wenn noch nie
#       meta         TEXT                  -- reserviert (z. B. JSON Referenzen/Links/Notizen)
#   );
#
# Hinweis:
# - Die Spalte "created" existiert hier NICHT im aktuellen Codepfad.
#   (Andere UI-Module können eine erweiterte Schema-Variante erwarten; dieses Modul
#    bleibt bei seiner minimalen Definition und fokussiert auf die Kernfelder.)
#
# STATUS-MODELL (VERTRAG)
# ──────────────────────
# Statuswerte, die dieses Modul nutzt:
#   - "open"      : frisch angelegt (noch nicht getestet)
#   - "running"   : Test/Iteration läuft (wird typischerweise durch update_result gesetzt)
#   - "accepted"  : Hypothese bestätigt (accept_hypothesis)
#   - "rejected"  : Hypothese verworfen (reject_hypothesis)
#
# Score/Confidence:
# - score        = Effekt-/Nutzenmaß (Interpretation abhängig vom Caller)
# - confidence   = Evidenzmaß (Interpretation abhängig vom Caller)
# - last_tested  = Zeitpunkt der letzten Bewertung (Unix seconds)
#
# ABHÄNGIGKEITEN (EXAKT)
# ──────────────────────
# - core.sql_manager:
#     • get_conn()  → liefert sqlite3.Connection
# - stdlib:
#     • time
#     • json (nur für optionale Meta/Plan Nutzung; im aktuellen Code minimal)
#     • typing (Dict/Any/Optional)
#
# ÖFFENTLICHE API (FUNKTIONEN, AKTUELLER STAND)
# ────────────────────────────────────────────
# ensure_schema() -> None
#   - legt Tabelle hypotheses an (CREATE TABLE IF NOT EXISTS)
#
# new_hypothesis(text: str, plan: Optional[dict]=None, meta: Optional[dict]=None) -> int
#   - fügt eine neue Hypothese ein:
#       status="open", score=0, confidence=0, last_tested=NULL
#   - gibt die neue id zurück (cursor.lastrowid)
#   - plan/meta werden im aktuellen Code als TEXT gespeichert (typisch JSON-String)
#
# get_hypothesis(hid: int) -> Optional[Dict[str,Any]]
#   - lädt eine Hypothese per id
#   - Rückgabe (Dict) enthält id, text, status, plan, score, confidence, last_tested, meta
#
# update_result(hid: int, result: Dict[str,Any]) -> bool
#   - aktualisiert score/confidence/status und setzt last_tested=now
#   - Default-Status, wenn nicht im result angegeben: "running"
#   - result Keys (typisch):
#       {"score":0.72, "confidence":0.81, "status":"running|accepted|rejected|open"}
#
# accept_hypothesis(hid: int) -> bool
#   - setzt status="accepted"
#
# reject_hypothesis(hid: int) -> bool
#   - setzt status="rejected"
#
# list_hypotheses(status: Optional[str]=None) -> List[Dict[str,Any]]
#   - listet Hypothesen, optional gefiltert nach status
#   - Sortierung (im aktuellen Code): ORDER BY id DESC (oder äquivalent)
#
# INTEGRATIONSPUNKTE (ORÓMA-KONTEXT)
# ──────────────────────────────────
# - ui/research_ui.py:
#     • nutzt ähnliche Konzepte (Hypothesen anlegen/listen/updaten)
# - ui/why_ui.py:
#     • nutzt core.explain.* Bindings, die u. a. hypotheses_add/list/update_result anbieten können
# - Dream/Experiment:
#     • Tools können update_result() nutzen, um nightly Tests zu dokumentieren
#
# FEHLER- / ROBUSTHEITSVERHALTEN
# ──────────────────────────────
# - Dieses Modul geht davon aus, dass sql_manager.get_conn() eine funktionierende
#   Connection liefert. DB-Locks/OperationalError werden hier NICHT aktiv behandelt
#   (Caller/Orchestrator sollen busy_timeout/WAL in sql_manager konfigurieren).
# - Inserts/Updates committen direkt (kein explizites Transaction-Batching in diesem Modul).
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Tabelle "hypotheses" bleibt minimal und stabil (id,text,status,plan,score,confidence,last_tested,meta).
# - Statuswerte bleiben exakt ("open","running","accepted","rejected"), da UI/Tools darauf filtern können.
# - Kein Delete in diesem Modul (Audit/Research-Trace bleibt erhalten).
# - update_result setzt last_tested immer auf now (damit „Re-Test“ nachvollziehbar ist).
#
# =============================================================================
# END HEADER
# =============================================================================

import time
import json
from typing import Dict, Any, Optional
from core import sql_manager

# -----------------------------------------------------------------------------
# DB-Setup
# -----------------------------------------------------------------------------
def ensure_schema():
    conn = sql_manager.get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            status TEXT DEFAULT 'open', -- open, running, accepted, rejected
            plan TEXT,
            score REAL DEFAULT 0.0,
            confidence REAL DEFAULT 0.0,
            last_tested INTEGER,
            meta TEXT
        )
    """)
    conn.commit()

# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------
def new_hypothesis(text: str,
                   plan: Optional[Dict[str, Any]] = None,
                   meta: Optional[Dict[str, Any]] = None) -> int:
    """Lege eine neue Hypothese an und gib ihre ID zurück."""
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO hypotheses(text, status, plan, score, confidence, last_tested, meta) VALUES (?,?,?,?,?,?,?)",
        (text, "open", json.dumps(plan or {}), 0.0, 0.0, None, json.dumps(meta or {}))
    )
    conn.commit()
    return cur.lastrowid

def get_hypothesis(hid: int) -> Optional[Dict[str, Any]]:
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM hypotheses WHERE id=?", (hid,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "text": row["text"],
        "status": row["status"],
        "plan": json.loads(row["plan"] or "{}"),
        "score": row["score"],
        "confidence": row["confidence"],
        "last_tested": row["last_tested"],
        "meta": json.loads(row["meta"] or "{}"),
    }

def update_result(hid: int, result: Dict[str, Any]) -> bool:
    """Update Hypothese mit Testergebnis (score/confidence)."""
    conn = sql_manager.get_conn()
    conn.execute(
        "UPDATE hypotheses SET score=?, confidence=?, last_tested=?, status=? WHERE id=?",
        (
            float(result.get("score", 0.0)),
            float(result.get("confidence", 0.0)),
            int(time.time()),
            result.get("status", "running"),
            hid
        )
    )
    conn.commit()
    return True

def accept_hypothesis(hid: int) -> bool:
    conn = sql_manager.get_conn()
    conn.execute("UPDATE hypotheses SET status=? WHERE id=?", ("accepted", hid))
    conn.commit()
    return True

def reject_hypothesis(hid: int) -> bool:
    conn = sql_manager.get_conn()
    conn.execute("UPDATE hypotheses SET status=? WHERE id=?", ("rejected", hid))
    conn.commit()
    return True

# -----------------------------------------------------------------------------
# Listing
# -----------------------------------------------------------------------------
def list_hypotheses(status: Optional[str] = None) -> list[Dict[str, Any]]:
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM hypotheses WHERE status=? ORDER BY id DESC", (status,))
    else:
        cur.execute("SELECT * FROM hypotheses ORDER BY id DESC")
    rows = cur.fetchall() or []
    return [get_hypothesis(r["id"]) for r in rows]

# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    ensure_schema()
    hid = new_hypothesis("Testhypothese: Mitte → mehr Siege", {"type": "game", "budget": 100})
    print("[hypothesis] Neu:", get_hypothesis(hid))
    update_result(hid, {"score": 0.68, "confidence": 0.85, "status": "running"})
    print("[hypothesis] Update:", get_hypothesis(hid))
    accept_hypothesis(hid)
    print("[hypothesis] Accepted:", get_hypothesis(hid))
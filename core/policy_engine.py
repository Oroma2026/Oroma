#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/policy_engine.py
# Projekt:   ORÓMA (Offline-First · Headless · Policy Learning)
# Modul:     PolicyEngine – tabellarische State→Action Policy aus SnapChains (policy_rules) + Auto-Export ins Regelarchiv
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert einen generischen Policy-Learner für ORÓMA:
#
#   (namespace, state_hash, action)  →  (n, pos, neg, draw, q, last_ts, centroid)
#
# Es lernt aus SnapChains (episodische Sequenzen) eine tabellarische Policy, die:
#   - domänen-agnostisch ist (TicTacToe, Snake, UniversalAdapter, …)
#   - robust im Headless/Edge-Betrieb läuft (Raspberry Pi, Orchestrator-Modus)
#   - mit SQLite-Locks umgehen kann (Atomic UPSERT + Lock-Retry)
#   - optional automatisch „gute“ Einträge ins Regelarchiv exportiert (Explainability)
#
# WICHTIGE REALITÄT IN DIESER DATEI (AKTUELLER CODESTAND)
# ──────────────────────────────────────────────────────
# 1) Datenquelle:
#    - Primär: oroma.db Tabelle `snapchains` (SELECT id, blob, source_id …)
#    - Fallback: JSON-Export im SnapChains-Verzeichnis über `source_id`
#      (z. B. {OROMA_SNAPCHAINS_DIR}/{source_id}.json oder {source_id}_*.json)
#
# 2) Adapter-System (Kanonisierung / Action-Ableitung):
#    - bevorzugt: mini_programs.universal_policy.adapter_universal.UniversalAdapter
#    - fallback:  core.ttt_adapter.TTTAdapter
#    - wenn kein Adapter verfügbar: RuntimeError beim Initialisieren (bewusst).
#
# 3) Zwei Lernpfade:
#    (A) Vector/Steps-Pfad:
#        - nutzt feature vectors aus chain["patterns"] / steps, berechnet optional centroid
#        - canonicalize(state_vec, spec) liefert state_hash + ggf. Permutation
#        - action wird aus delta/step/adapter abgeleitet, optional perm-mapped
#
#    (B) Pre-Hash-Fallback:
#        - wenn keine Vektoren vorhanden, werden Paare (state_hash, action) aus
#          chain steps gelesen (z. B. steps[].h + next[].a) → trotzdem lernbar
#
# DB-ZIEL / SCHEMA (policy_rules)
# ──────────────────────────────
# Dieses Modul schreibt in oroma.db Tabelle `policy_rules` und setzt voraus,
# dass sql_manager.ensure_schema() die Tabelle/Constraints bereitstellt.
#
# Erwartete Spalten (aus Codepfad ersichtlich):
#   namespace TEXT
#   state_hash TEXT
#   action TEXT
#   n INTEGER
#   pos INTEGER
#   neg INTEGER
#   draw INTEGER
#   q REAL
#   last_ts INTEGER
#   centroid TEXT|BLOB|NULL   (im Code: JSON string)
#
# Kritische Invariante:
#   UNIQUE(namespace, state_hash, action)
# → wird für ON CONFLICT(...) UPSERT benötigt.
#
# ATOMIC UPSERT + LOCK-RETRY (PRODUKTIONSKRITISCH)
# ───────────────────────────────────────────────
# Updates erfolgen über EIN atomisches Statement:
#   INSERT … ON CONFLICT(namespace,state_hash,action) DO UPDATE SET …
#
# Zusätzlich:
#   - PRAGMA busy_timeout wird auf der Connection gesetzt (typisch 8000ms)
#   - sqlite3.OperationalError "locked/busy" wird mit exponential backoff retried
#     (mehrere Versuche, dann Fehler nach oben)
#
# Ergebnis:
#   - hohe Stabilität bei parallelen Writer-Jobs (Orchestrator + Hooks + Tools)
#   - kein „lost update“ durch race conditions (UPSERT ist atomar)
#
# OUTCOME-MODELL (pos/neg/draw → q)
# ────────────────────────────────
# outcome ∈ {+1, 0, -1}
#   +1 → pos += 1
#   -1 → neg += 1
#    0 → draw += 1
#
# q wird als einfacher Score gepflegt:
#   q = (pos - neg) / n
# (im UPSERT exakt so berechnet; draw beeinflusst n, aber nicht pos/neg Differenz)
#
# TRAINING-MODI (ÖFFENTLICHE METHODEN)
# ────────────────────────────────────
# PolicyEngine.train_from_db(limit=..., origin=None, include_compressed=False)
#   - lädt die neuesten SnapChains aus oroma.db (ORDER BY id DESC)
#   - Filter:
#       • origin=None → alle Origins
#       • origin="game:tictactoe" → nur diese origin
#       • include_compressed=False → status IS NULL OR status='active'
#         (optional auch 'compressed')
#   - wenn blob nicht parsebar: Fallback über source_id JSON-Datei
#
# PolicyEngine.train_from_tmpfs(tmpfs_dir=None, limit=...)
#   - lädt JSON Chains aus /run/oroma (oder ENV OROMA_TMPFS_DIR)
#   - gedacht für RAM-first Training (sehr schnell, minimaler DB-Load)
#
# AUTO-EXPORT INS REGELARCHIV (Explainability / Archiv)
# ────────────────────────────────────────────────────
# Wenn aktiviert, werden „gute“ (state_hash, action) automatisch exportiert:
#   - Ziel: core.regelarchiv (falls importierbar)
#   - bevorzugte API: regelarchiv.upsert_policy(...)
#   - fallback APIs:   regelarchiv.upsert(...) oder save_rule(...)
#
# Gate-Bedingungen (ENV-gesteuert):
#   - Mindest-N (exp_min_n)
#   - Mindest-|q| (exp_min_abs_q)
#   - optional: Majority-Confidence (pos/neg/draw Verteilung)
#   - Cooldown pro (namespace,state_hash,action) (exp_cooldown_s)
#
# Wichtig:
# - Auto-Export ist best effort: bei Fehler wird nicht abgebrochen, nur geloggt.
# - Es wird ein interner last_export_ts Cache geführt (RAM), um Cooldown zu erzwingen.
#
# WICHTIGE ENV-VARIABLEN (AKZEPTIERT AUCH OROMA_UP_* ALS FALLBACK)
# ─────────────────────────────────────────────────────────────────
# Export:
#   OROMA_PE_AUTO_EXPORT        (Default: 1)   [Alias: OROMA_UP_AUTO_EXPORT]
#   OROMA_PE_EXPORT_MIN_N       (Default: 3)   [Alias: OROMA_UP_EXPORT_MIN_N]
#   OROMA_PE_EXPORT_MIN_ABS_Q   (Default: 0.15)[Alias: OROMA_UP_EXPORT_MIN_ABS_Q]
#   OROMA_PE_EXPORT_MAJ_CONF    (Default: 0.0) [Alias: OROMA_UP_EXPORT_MAJ_CONF]
#   OROMA_PE_EXPORT_COOLDOWN_S  (Default: 600) [Alias: OROMA_UP_EXPORT_COOLDOWN_S]
#
# Training/Files:
#   OROMA_TMPFS_DIR             (Default: /run/oroma)
#   OROMA_BASE / OROMA_BASE_DIR (Default: /opt/ai/oroma)
#   OROMA_SNAPCHAINS_DIR        (Default: {BASE}/data/snapchains)
#
# DB:
#   OROMA_DB_PATH (über sql_manager; hier indirekt genutzt)
#
# CLI / BETRIEB (typisch)
# ──────────────────────
# Dieses Modul besitzt argparse (je nach unterem File-Teil) und wird typischerweise via:
#   PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine --train-db --origin game:tictactoe --limit 5000
# oder:
#   PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine --train-tmpfs --tmpfs-dir /run/oroma --limit 2000
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Headless/Edge-tauglich: keine GUI, keine schweren ML-Dependencies notwendig.
# - Atomic UPSERT + Lock-Retry muss bleiben (sonst „DB locked“/Race-Bugs).
# - Adapter-System muss tolerant bleiben (UniversalAdapter bevorzugt, Fallbacks ok).
# - Pre-Hash-Fallback muss bleiben (sonst „0 Schritte“ trotz vorhandener Chains).
# - Export bleibt best effort (Regelarchiv optional, niemals Boot/Train blockieren).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import sys
import json
import time
import glob
import logging
from core.log_guard import log_suppressed
import argparse
import sqlite3  # ← NEU: für gezieltes Handling von Locked/Integrity
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ----------------------------------------------------------------------------- 
# Logging (headless, robust)
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.policy_engine")
if not LOG.handlers:
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    LOG.addHandler(_sh)
LOG.setLevel(logging.INFO)

# ----------------------------------------------------------------------------- 
# Core-DB
# -----------------------------------------------------------------------------
try:
    from core import sql_manager
except Exception as e:
    raise RuntimeError(f"[policy_engine] sql_manager Importfehler: {e}")

try:
    from core import db_writer_client as db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

# Optionales Archiv-Modul (nur wenn vorhanden)
try:
    from core import regelarchiv as _archiv
except Exception:
    _archiv = None  # type: ignore

# Bevorzugt: UniversalAdapter (optional)
_DEFAULT_ADAPTER = None
try:
    from mini_programs.universal_policy.adapter_universal import UniversalAdapter as _UNI
    _DEFAULT_ADAPTER = _UNI()
except Exception:
    try:
        from core.ttt_adapter import TTTAdapter as _TTT
        _DEFAULT_ADAPTER = _TTT()
    except Exception:
        _DEFAULT_ADAPTER = None


# =============================================================================
# Utils / ENV
# =============================================================================

def _env_bool(name: str, default: str = "0", *fallbacks: str) -> bool:
    val = os.environ.get(name, None)
    if val is None:
        for fb in fallbacks:
            val = os.environ.get(fb, None)
            if val is not None:
                break
    if val is None:
        val = default
    return str(val).lower() in ("1", "true", "yes", "on")

def _env_int(name: str, default: int, *fallbacks: str) -> int:
    val = os.environ.get(name, None)
    if val is None:
        for fb in fallbacks:
            val = os.environ.get(fb, None)
            if val is not None:
                break
    try:
        return int(val if val is not None else default)
    except Exception as e:
        log_suppressed(LOG, key="policy_engine.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _env_float(name: str, default: float, *fallbacks: str) -> float:
    val = os.environ.get(name, None)
    if val is None:
        for fb in fallbacks:
            val = os.environ.get(fb, None)
            if val is not None:
                break
    try:
        return float(val if val is not None else default)
    except Exception as e:
        log_suppressed(LOG, key="policy_engine.ret.2", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default

def _row_val(row: Any, key_or_idx: Any) -> Any:
    if row is None:
        return None
    try:
        if hasattr(row, "keys"):
            return row[key_or_idx]
    except Exception as e:
        log_suppressed(LOG, key="policy_engine.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    try:
        return row[key_or_idx]
    except Exception as e:
        log_suppressed(LOG, key="policy_engine.ret.4", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def _as_int(x: Any, default: int = 0) -> int:
    try: return int(x)
    except Exception: return default

def _as_float(x: Any, default: float = 0.0) -> float:
    try: return float(x)
    except Exception: return default

def _json_loads_safe(blob: bytes | str) -> Optional[Dict[str, Any]]:
    try:
        if isinstance(blob, (bytes, bytearray)):
            return json.loads(blob.decode("utf-8"))
        return json.loads(blob)
    except Exception as e:
        log_suppressed(LOG, key="policy_engine.ret.5", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def _snapchains_export_dir() -> str:
    """Export-Ordner für vollständige SnapChain-JSONs.

    Viele Mini-Programme (z.B. Snake) exportieren parallel zum DB-Insert eine Datei:
        {OROMA_SNAPCHAINS_DIR}/{source_id}.json
    """
    base = os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma"
    return os.environ.get("OROMA_SNAPCHAINS_DIR") or os.path.join(base, "data", "snapchains")


def _load_chain_from_source_id(source_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fallback: Lädt Chain-JSON über source_id aus dem Export-Verzeichnis.

    Best effort:
      - {source_id}.json
      - Fallback: {source_id}_*.json
    """
    if not source_id:
        return None
    sid = os.path.basename(str(source_id).strip())
    if not sid:
        return None

    d = _snapchains_export_dir()
    p1 = os.path.join(d, f"{sid}.json")
    try:
        if os.path.exists(p1):
            with open(p1, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_suppressed(
            LOG,
            key="policy_engine.ret.5b",
            msg="Suppressed exception (returning default)",
            exc=e,
            level=logging.DEBUG,
            interval_s=300,
        )
        return None

    try:
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.startswith(sid) and fn.endswith(".json"):
                    with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                        return json.load(f)
    except Exception as e:
        log_suppressed(
            LOG,
            key="policy_engine.ret.5c",
            msg="Suppressed exception (returning default)",
            exc=e,
            level=logging.DEBUG,
            interval_s=300,
        )
        return None

    return None

def _status_clause(include_compressed: bool) -> str:
    if include_compressed:
        return "(status IS NULL OR status IN ('active','compressed'))"
    return "(status IS NULL OR status = 'active')"

def _normalize_namespace(ns: Optional[str]) -> Optional[str]:
    if ns is None:
        return None
    s = str(ns).strip()
    if s == "" or s == "*":
        return None
    return s


# =============================================================================
# Policy-Tabellenoperationen (+ Rückgabe alt/neu für Auto-Export)
# =============================================================================

def _update_policy_row(conn,
                       namespace: str,
                       state_hash: str,
                       action: str,
                       outcome: int,
                       centroid: Optional[List[float]]) -> Tuple[int,int,int,int,float,int,float]:
    """
    Atomic UPSERT für (namespace, state_hash, action) mit Lock-Retry.
    Rückgabe: (n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old)
    outcome: +1 (pos), 0 (draw), -1 (neg)
    """
    now = int(time.time())
    cen_json = json.dumps(centroid) if centroid is not None else None
    pos_inc = 1 if outcome > 0 else 0
    neg_inc = 1 if outcome < 0 else 0
    draw_inc = 1 if outcome == 0 else 0

    # sicherstellen: busy_timeout auch auf dieser Conn
    try:
        conn.execute("PRAGMA busy_timeout=8000")
    except Exception as e:
        log_suppressed(LOG, key="policy_engine.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    attempts = 0
    while True:
        attempts += 1
        try:
            # Altwerte für Rückgabe (nicht kritisch falls zwischenzeitlich veraltet)
            r = conn.execute(
                "SELECT n,pos,neg,draw,q FROM policy_rules WHERE namespace=? AND state_hash=? AND action=?",
                (namespace, state_hash, action)
            ).fetchone()
            if r is None:
                n_old = pos_old = neg_old = draw_old = 0
                q_old = 0.0
            else:
                n_old   = _as_int(_row_val(r, "n") if hasattr(r, "keys") else r[0])
                pos_old = _as_int(_row_val(r, "pos") if hasattr(r, "keys") else r[1])
                neg_old = _as_int(_row_val(r, "neg") if hasattr(r, "keys") else r[2])
                draw_old= _as_int(_row_val(r, "draw") if hasattr(r, "keys") else r[3])
                q_old   = _as_float(_row_val(r, "q") if hasattr(r, "keys") else r[4])

            # EIN Statement: INSERT … ON CONFLICT … DO UPDATE (atomar)
            conn.execute("""
                INSERT INTO policy_rules
                    (namespace,state_hash,action,n,pos,neg,draw,q,last_ts,centroid)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(namespace,state_hash,action) DO UPDATE SET
                    n       = policy_rules.n + 1,
                    pos     = policy_rules.pos + ?,
                    neg     = policy_rules.neg + ?,
                    draw    = policy_rules.draw + ?,
                    q       = CAST((policy_rules.pos + ?) - (policy_rules.neg + ?) AS REAL) / (policy_rules.n + 1),
                    last_ts = ?,
                    centroid= COALESCE(?, policy_rules.centroid)
            """, (
                namespace, state_hash, action,
                1, pos_inc, neg_inc, draw_inc, (pos_inc - neg_inc), now, cen_json,
                pos_inc, neg_inc, draw_inc, pos_inc, neg_inc, now, cen_json
            ))
            conn.commit()

            r2 = conn.execute(
                "SELECT n,pos,neg,draw,q FROM policy_rules WHERE namespace=? AND state_hash=? AND action=?",
                (namespace, state_hash, action)
            ).fetchone()
            n_new   = _as_int(_row_val(r2, "n")   if hasattr(r2, "keys") else r2[0])
            pos_new = _as_int(_row_val(r2, "pos") if hasattr(r2, "keys") else r2[1])
            neg_new = _as_int(_row_val(r2, "neg") if hasattr(r2, "keys") else r2[2])
            draw_new= _as_int(_row_val(r2, "draw")if hasattr(r2, "keys") else r2[3])
            q_new   = _as_float(_row_val(r2, "q") if hasattr(r2, "keys") else r2[4])
            return n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old

        except sqlite3.OperationalError as e:
            s = str(e).lower()
            if "locked" in s or "busy" in s:
                if attempts <= 6:
                    time.sleep(0.05 * (2 ** attempts))  # Exponential Backoff
                    continue
            raise
        except sqlite3.IntegrityError as e:
            # sehr selten (z. B. konkurrierende Schemaänderung)
            if "unique" in str(e).lower() and attempts <= 6:
                time.sleep(0.01 * attempts)
                continue
            raise


def _dbw_enabled() -> bool:
    """
    Prüft robust, ob der zentrale DBWriter für policy_rules-Schreibpfade aktiv
    und verwendbar ist. Diese Härtung ist für den Snake-Policy-Trainingspfad
    kritisch, weil `sql_manager.get_conn()` bei Strict-Mode schreibgeschützt
    öffnet. Ohne diese Weiche laufen vorhandene Snake-SnapChains sonst in
    `attempt to write a readonly database`, obwohl die Trainingsdaten korrekt in
    der DB liegen.
    """
    try:
        if db_writer_client is None:
            return False
        if not getattr(db_writer_client, "enabled", lambda: False)():
            return False
        return str(os.environ.get("OROMA_DBW_ENABLE", "0")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _dbw_timeout_ms() -> int:
    return max(500, _env_int("OROMA_POLICY_DBW_TIMEOUT_MS", 5000, "OROMA_DBW_TIMEOUT_MS"))


def _update_policy_row_dbw(namespace: str,
                           state_hash: str,
                           action: str,
                           outcome: int,
                           centroid: Optional[List[float]]) -> Tuple[int,int,int,int,float,int,float]:
    """
    DBWriter-only Variante des atomaren Policy-UPSERTs.

    WICHTIG:
    - liest Alt-/Neuwerte weiterhin aus der echten DB, damit Auto-Export und
      Diagnosewerte unverändert funktionieren
    - schreibt die Mutation jedoch strikt über den zentralen DBWriter
    - verhindert damit readonly-Fehler im Snake-/Policy-Training bei aktivem
      Strict-Mode
    """
    if not _dbw_enabled():
        raise RuntimeError("DBWriter inactive for policy_engine DBW path")

    now = int(time.time())
    cen_json = json.dumps(centroid) if centroid is not None else None
    pos_inc = 1 if outcome > 0 else 0
    neg_inc = 1 if outcome < 0 else 0
    draw_inc = 1 if outcome == 0 else 0

    with sql_manager.get_conn() as conn:
        try:
            conn.execute("PRAGMA busy_timeout=8000")
        except Exception:
            pass
        r = conn.execute(
            "SELECT n,pos,neg,draw,q FROM policy_rules WHERE namespace=? AND state_hash=? AND action=?",
            (namespace, state_hash, action)
        ).fetchone()
        if r is None:
            n_old = pos_old = neg_old = draw_old = 0
            q_old = 0.0
        else:
            n_old   = _as_int(_row_val(r, "n") if hasattr(r, "keys") else r[0])
            pos_old = _as_int(_row_val(r, "pos") if hasattr(r, "keys") else r[1])
            neg_old = _as_int(_row_val(r, "neg") if hasattr(r, "keys") else r[2])
            draw_old= _as_int(_row_val(r, "draw") if hasattr(r, "keys") else r[3])
            q_old   = _as_float(_row_val(r, "q") if hasattr(r, "keys") else r[4])

    db_writer_client.exec_write(
        """
        INSERT INTO policy_rules
            (namespace,state_hash,action,n,pos,neg,draw,q,last_ts,centroid)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(namespace,state_hash,action) DO UPDATE SET
            n       = policy_rules.n + 1,
            pos     = policy_rules.pos + ?,
            neg     = policy_rules.neg + ?,
            draw    = policy_rules.draw + ?,
            q       = CAST((policy_rules.pos + ?) - (policy_rules.neg + ?) AS REAL) / (policy_rules.n + 1),
            last_ts = ?,
            centroid= COALESCE(?, policy_rules.centroid)
        """,
        [
            namespace, state_hash, action,
            1, pos_inc, neg_inc, draw_inc, (pos_inc - neg_inc), now, cen_json,
            pos_inc, neg_inc, draw_inc, pos_inc, neg_inc, now, cen_json,
        ],
        tag="policy_engine.policy_rules.upsert",
        priority="normal",
        timeout_ms=_dbw_timeout_ms(),
        db="oroma",
    )

    with sql_manager.get_conn() as conn:
        try:
            conn.execute("PRAGMA busy_timeout=8000")
        except Exception:
            pass
        r2 = conn.execute(
            "SELECT n,pos,neg,draw,q FROM policy_rules WHERE namespace=? AND state_hash=? AND action=?",
            (namespace, state_hash, action)
        ).fetchone()
    if r2 is None:
        n_new = pos_new = neg_new = draw_new = 0
        q_new = 0.0
    else:
        n_new   = _as_int(_row_val(r2, "n")   if hasattr(r2, "keys") else r2[0])
        pos_new = _as_int(_row_val(r2, "pos") if hasattr(r2, "keys") else r2[1])
        neg_new = _as_int(_row_val(r2, "neg") if hasattr(r2, "keys") else r2[2])
        draw_new= _as_int(_row_val(r2, "draw")if hasattr(r2, "keys") else r2[3])
        q_new   = _as_float(_row_val(r2, "q") if hasattr(r2, "keys") else r2[4])
    return n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old


# =============================================================================
# PolicyEngine – Kern
# =============================================================================

class PolicyEngine:
    """
    Tabellarische Policy aus SnapChain-Sequenzen. RAM-first/Auto-Export an Bord.
    """

    def __init__(self, adapter=None):
        self.adapter = adapter or _DEFAULT_ADAPTER
        if self.adapter is None:
            raise RuntimeError(
                "[policy_engine] Kein Adapter verfügbar. "
                "Installiere z. B. mini_programs/universal_policy/adapter_universal.py "
                "oder core/ttt_adapter.py und/oder übergib einen Adapter."
            )
        self.namespace: str = getattr(self.adapter, "namespace", "default")

        # Auto-Export Konfiguration
        self.auto_export_on  = _env_bool("OROMA_PE_AUTO_EXPORT", "1", "OROMA_UP_AUTO_EXPORT")
        self.exp_min_n       = _env_int("OROMA_PE_EXPORT_MIN_N", 3, "OROMA_UP_MIN_N")
        self.exp_min_abs_q   = _env_float("OROMA_PE_EXPORT_MIN_ABS_Q", 0.15, "OROMA_UP_MIN_ABS_Q")
        self.exp_maj_conf    = _env_float("OROMA_PE_EXPORT_MAJ_CONF", 0.0, "OROMA_UP_MAJ_CONF")
        self.exp_cooldown_s  = _env_int("OROMA_PE_EXPORT_COOLDOWN_S", 600, "OROMA_UP_COOLDOWN_S")
        self._last_export_ts: Dict[Tuple[str,str,str], int] = {}

        # Schema sichern + PRAGMAs für diese Engine-Verbindung
        try:
            sql_manager.ensure_schema()
            with sql_manager.get_conn() as _c:
                try:
                    _c.execute("PRAGMA journal_mode=WAL")
                    _c.execute("PRAGMA synchronous=NORMAL")
                    _c.execute("PRAGMA busy_timeout=8000")
                except Exception as e:
                    log_suppressed(LOG, key="policy_engine.pass.7", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
            LOG.debug("ensure_schema() OK")
        except Exception as e:
            LOG.warning("ensure_schema() / PRAGMA Fehler: %s", e)

    # --------- PLAYBACK / INFERENCE -----------------------------------------

    def choose_action_from_board(self, board: List[str]) -> Optional[str]:
        if hasattr(self.adapter, "vectorize_board"):
            try:
                vec = self.adapter.vectorize_board(board)  # type: ignore[attr-defined]
                return self.choose_action(vec)
            except Exception as e:
                log_suppressed(LOG, key="policy_engine.pass.8", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return None

    def choose_action(self, state_vec: List[float]) -> Optional[str]:
        try:
            with sql_manager.get_conn() as conn:
                try:
                    state_hash, perm, inv_perm = self.adapter.canonicalize(state_vec)  # type: ignore[arg-type]
                except TypeError:
                    state_hash, perm, inv_perm = self.adapter.canonicalize(state_vec, {})  # type: ignore[arg-type]
                pol = self._policy_for_state(conn, self.namespace, state_hash)
                if pol:
                    best = sorted(pol.items(), key=lambda kv: (kv[1]["q"], kv[1]["n"]), reverse=True)
                    chosen = best[0][0]
                    if hasattr(self.adapter, "map_action_through_perm"):
                        try:
                            return self.adapter.map_action_through_perm(chosen, inv_perm)  # type: ignore[arg-type]
                        except Exception as e:
                            log_suppressed(LOG, key="policy_engine.ret.9", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
                            return chosen
                    return chosen
        except Exception as e:
            LOG.debug("choose_action: %s", e)

        try:
            if hasattr(self.adapter, "fallback_action"):
                return self.adapter.fallback_action(state_vec)  # type: ignore[misc]
        except Exception as e:
            log_suppressed(LOG, key="policy_engine.pass.10", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return None

    # --------- INTERN: Pre-Hash-Erkennung -----------------------------------

    @staticmethod
    def _iter_prehash_pairs(chain: Dict[str, Any]) -> List[Tuple[str, str]]:
        """
        Liefert (state_hash, action)-Paare aus Sequenzen ohne Feature-Vektoren.
        Durchsucht in Reihenfolge: steps, events, frames, trace.
        state_hash: cur[i].(h|state_hash|sh|state.h|state.state_hash)
        action    : nxt[i+1].(a|action_canon|action|ac)
        """
        pairs: List[Tuple[str, str]] = []
        if not isinstance(chain, dict):
            return pairs

        def take_hash(d: Dict[str, Any]) -> Optional[str]:
            if not isinstance(d, dict):
                return None
            for k in ("h", "state_hash", "sh"):
                if k in d and isinstance(d[k], (str, int, float)):
                    return str(d[k])
            st = d.get("state")
            if isinstance(st, dict):
                for k in ("h", "state_hash", "sh"):
                    if k in st and isinstance(st[k], (str, int, float)):
                        return str(st[k])
            return None

        def take_act(d: Dict[str, Any]) -> Optional[str]:
            if not isinstance(d, dict):
                return None
            for k in ("a", "action_canon", "action", "ac"):
                if k in d:
                    v = d[k]
                    try:
                        return str(int(v))
                    except Exception as e:
                        log_suppressed(LOG, key="policy_engine.ret.11", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
                        return str(v)
            return None

        for key in ("steps", "events", "frames", "trace"):
            seq = chain.get(key)
            if not (isinstance(seq, list) and len(seq) >= 2):
                continue
            for i in range(len(seq) - 1):
                cur = seq[i]; nxt = seq[i + 1]
                sh = take_hash(cur)
                if not sh:
                    continue
                act = take_act(nxt) or "0"
                pairs.append((sh, act))
            if pairs:
                break
        return pairs

    # --------- TRAINING: eine Chain -----------------------------------------

    def ingest_chain(self, chain_or_dict: Any) -> int:
        """
        Verarbeitet EINE Chain und schreibt Policy-Updates.
        """
        chain = chain_or_dict if isinstance(chain_or_dict, dict) else {}
        spec  = chain.get("spec", {}) if isinstance(chain, dict) else {}

        # Outcome (+1/0/-1): bevorzugt aus Chain, sonst Adapter
        outcome_val = 0
        try:
            if isinstance(chain, dict) and "result" in chain:
                outcome_val = int(chain.get("result") or 0)
            else:
                try:
                    outcome_val = int(self.adapter.final_outcome(chain))  # type: ignore[arg-type]
                except TypeError:
                    outcome_val = int(self.adapter.final_outcome([]))  # type: ignore[arg-type]
        except Exception:
            outcome_val = 0
        outcome = 1 if outcome_val > 0 else -1 if outcome_val < 0 else 0

        # 1) Vektorpfad
        vecs: List[List[float]] = []
        try:
            vecs = self.adapter.extract_vectors(chain)  # type: ignore[arg-type]
        except Exception as e:
            LOG.debug("ingest_chain: extract_vectors Fehler: %s", e)

        if vecs and len(vecs) >= 2:
            centroid: Optional[List[float]] = None
            try:
                d = min(len(v) for v in vecs)
                if d > 0:
                    acc = [0.0] * d
                    for v in vecs:
                        for i in range(d):
                            acc[i] += float(v[i])
                    centroid = [x / len(vecs) for x in acc]
            except Exception:
                centroid = None

            steps = 0
            try:
                with sql_manager.get_conn() as conn:
                    # PRAGMA auch hier sicherheitshalber
                    try:
                        conn.execute("PRAGMA busy_timeout=8000")
                    except Exception as e:
                        log_suppressed(LOG, key="policy_engine.pass.12", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

                    for i in range(len(vecs) - 1):
                        prev = vecs[i]; nxt = vecs[i + 1]

                        # Aktion aus Chain oder Delta
                        act = None
                        try:
                            st = (chain.get("steps") or [])[i + 1] if isinstance(chain, dict) else None
                            act = (st or {}).get("a") if isinstance(st, dict) else None
                        except Exception:
                            act = None
                        if act is None:
                            try:
                                act = self.adapter.action_from_delta(
                                    prev, nxt, action_kind=(spec.get("action", {}) or {}).get("kind", "index")
                                )  # type: ignore[arg-type]
                            except TypeError:
                                act = self.adapter.action_from_delta(prev, nxt)  # type: ignore[misc]
                            except Exception:
                                act = None
                        if act is None and hasattr(self.adapter, "fallback_action"):
                            try:
                                act = self.adapter.fallback_action(
                                    prev, (spec.get("action", {}) or {}).get("kind", "index")
                                )  # type: ignore[arg-type]
                            except Exception:
                                act = None
                        if act is None:
                            act = "0"

                        # Kanonisierung (+ optionale Aktionsabbildung)
                        try:
                            state_hash, perm, inv_perm = self.adapter.canonicalize(prev, spec)  # type: ignore[arg-type]
                            if hasattr(self.adapter, "map_action_through_perm") and perm is not None:
                                try:
                                    act = self.adapter.map_action_through_perm(str(act), perm)  # type: ignore[arg-type]
                                except Exception as e:
                                    log_suppressed(LOG, key="policy_engine.pass.13", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                        except TypeError:
                            state_hash, _, _ = self.adapter.canonicalize(prev)  # type: ignore[misc]
                        except Exception:
                            state_hash = "".join("X" if v > 0.5 else "O" if v < -0.5 else "_" for v in prev)

                        # Update + Auto-Export
                        if _dbw_enabled():
                            n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old = _update_policy_row_dbw(
                                self.namespace, state_hash, str(act), outcome, centroid
                            )
                        else:
                            n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old = _update_policy_row(
                                conn, self.namespace, state_hash, str(act), outcome, centroid
                            )
                        self._maybe_auto_export(state_hash, str(act),
                                                n_new, pos_new, neg_new, draw_new, q_new,
                                                n_old, q_old, centroid)
                        steps += 1
                return steps
            except Exception as e:
                LOG.error("ingest_chain (vector): DB-Fehler: %s", e)
                return 0

        # 2) Pre-Hash-Fallback
        pairs = self._iter_prehash_pairs(chain)
        if not pairs:
            return 0

        steps = 0
        try:
            with sql_manager.get_conn() as conn:
                try:
                    conn.execute("PRAGMA busy_timeout=8000")
                except Exception as e:
                    log_suppressed(LOG, key="policy_engine.pass.14", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                for state_hash, act in pairs:
                    if _dbw_enabled():
                        n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old = _update_policy_row_dbw(
                            self.namespace, str(state_hash), str(act), outcome, None
                        )
                    else:
                        n_new, pos_new, neg_new, draw_new, q_new, n_old, q_old = _update_policy_row(
                            conn, self.namespace, str(state_hash), str(act), outcome, None
                        )
                    self._maybe_auto_export(str(state_hash), str(act),
                                            n_new, pos_new, neg_new, draw_new, q_new,
                                            n_old, q_old, None)
                    steps += 1
            LOG.debug("ingest_chain (prehash): steps=%d", steps)
            return steps
        except Exception as e:
            LOG.error("ingest_chain (prehash): DB-Fehler: %s", e)
            return steps

    # --------- TRAINING: DB & Files -----------------------------------------

    def train_from_db(self, *, limit: int = 10000,
                      origin: Optional[str] = None,
                      include_compressed: bool = False) -> int:
        org = _normalize_namespace(origin if origin is not None else self.namespace)
        total_steps = 0
        fetched = 0
        status_sql = _status_clause(include_compressed)

        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                if org is None:
                    cur.execute(f"""
                        SELECT id, blob, source_id
                          FROM snapchains
                         WHERE {status_sql}
                      ORDER BY id DESC
                         LIMIT ?
                    """, (int(max(0, limit)),))
                else:
                    cur.execute(f"""
                        SELECT id, blob, source_id
                          FROM snapchains
                         WHERE origin = ?
                           AND {status_sql}
                      ORDER BY id DESC
                         LIMIT ?
                    """, (org, int(max(0, limit))))
                rows = cur.fetchall() or []
        except Exception as e:
            LOG.error("train_from_db: SELECT-Fehler: %s", e)
            rows = []

        for row in rows:
            fetched += 1
            blob = _row_val(row, "blob") if hasattr(row, "keys") else _row_val(row, 1)
            data = _json_loads_safe(blob) if isinstance(blob, (bytes, bytearray, str)) else None

            # Fallback: wenn blob nur Hash/Handle ist, versuche source_id-Datei zu laden
            if not data:
                source_id = _row_val(row, "source_id") if hasattr(row, "keys") else None
                data = _load_chain_from_source_id(source_id)

            if not data:
                continue

            try:
                total_steps += self.ingest_chain(data)
            except Exception as e:
                LOG.debug("train_from_db: ingest_chain Fehler: %s", e)

        _msg = "[policy_engine] trainierte Schritte: %d (Chains: %d, Filter: %s%s)"
        _args = (total_steps, fetched, (org if org is not None else "<ALLE>"),
                 " +compressed" if include_compressed else "")
        if total_steps == 0:
            LOG.debug(_msg, *_args)
            LOG.debug("Hinweis: 0 Schritte → evtl. nur Pre-Hashes vorhanden oder kompaktes DB-Format. "
                      "Export-Verzeichnis mit JSON nutzen (siehe --train-tmpfs/--tmpfs-dir).")
        else:
            LOG.info(_msg, *_args)
        return total_steps

    def train_from_tmpfs(self, *, tmpfs_dir: Optional[str] = None, limit: int = 2000) -> int:
        base = tmpfs_dir or os.environ.get("OROMA_TMPFS_DIR") or "/run/oroma"
        if not os.path.isdir(base):
            base = "/dev/shm/oroma" if os.path.isdir("/dev/shm/oroma") else base
            if not os.path.isdir(base):
                LOG.info("Verzeichnis nicht gefunden (%s)", tmpfs_dir or "/run/oroma")
                return 0
        paths = sorted(glob.glob(os.path.join(base, "*.json")),
                       key=lambda p: os.path.getmtime(p), reverse=True)[:max(0, int(limit))]
        steps = 0
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    chain = json.load(f)
                steps += self.ingest_chain(chain)
            except Exception as e:
                LOG.debug("train_from_tmpfs: Fehler bei %s: %s", p, e)
        LOG.info("[policy_engine] file trainierte Schritte: %d (Files: %d, Dir: %s)",
                 steps, len(paths), base)
        return steps

    # --------- EXPORT → REGELARCHIV (manuell/CLI) ----------------------------

    def export_archiv(self, *, min_n: int = 1, min_abs_q: float = 0.0) -> int:
        if not _archiv:
            LOG.info("[policy_engine] kein Regelarchiv-Modul gefunden – Export übersprungen.")
            return 0

        _f_upsert_policy = getattr(_archiv, "upsert_policy", None)
        _f_upsert        = getattr(_archiv, "upsert", None)
        _f_save_rule     = getattr(_archiv, "save_rule", None)

        if not (_f_upsert_policy or _f_upsert or _f_save_rule):
            LOG.info("[policy_engine] Regelarchiv hat keine passende Upsert-API – Export übersprungen.")
            return 0

        done = 0
        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""SELECT namespace, state_hash, action, q, n, centroid
                                 FROM policy_rules
                                WHERE namespace=? AND n >= ? AND ABS(q) >= ?""",
                            (self.namespace, int(min_n), float(min_abs_q)))
                for row in cur.fetchall() or []:
                    ns  = str(_row_val(row, "namespace") if hasattr(row, "keys") else _row_val(row, 0))
                    sh  = str(_row_val(row, "state_hash") if hasattr(row, "keys") else _row_val(row, 1))
                    act = str(_row_val(row, "action") if hasattr(row, "keys") else _row_val(row, 2))
                    q   = _as_float(_row_val(row, "q") if hasattr(row, "keys") else _row_val(row, 3))
                    n   = _as_int(_row_val(row, "n") if hasattr(row, "keys") else _row_val(row, 4))
                    cen = _row_val(row, "centroid") if hasattr(row, "keys") else _row_val(row, 5)

                    centroid = None
                    if isinstance(cen, (bytes, bytearray)):
                        try: centroid = json.loads(cen.decode("utf-8"))
                        except Exception: centroid = None
                    elif isinstance(cen, str):
                        try: centroid = json.loads(cen)
                        except Exception: centroid = None
                    elif isinstance(cen, list):
                        centroid = cen

                    try:
                        if _f_upsert_policy: _f_upsert_policy(ns, sh, act, q, n, centroid)  # type: ignore[misc]
                        elif _f_upsert:      _f_upsert(ns, sh, act, q, n, centroid)        # type: ignore[misc]
                        else:                _f_save_rule({"namespace": ns, "state_hash": sh, "action": act, "q": q, "n": n, "centroid": centroid})  # type: ignore[misc]
                        done += 1
                    except Exception as e:
                        LOG.debug("export_archiv: upsert/save Fehler: %s", e)
        except Exception as e:
            LOG.error("export_archiv: SELECT-Fehler: %s", e)
            return done

        LOG.info("[policy_engine] exportierte Archiv-Regeln: %d", done)
        return done

    # --------- Intern: Policy-Read (kleiner Helfer) --------------------------

    def _policy_for_state(self, conn, namespace: str, state_hash: str) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        cur = conn.cursor()
        cur.execute("""SELECT action, q, n FROM policy_rules
                       WHERE namespace=? AND state_hash=?""",
                    (namespace, state_hash))
        rows = cur.fetchall() or []
        for row in rows:
            a = str(_row_val(row, "action") if hasattr(row, "keys") else _row_val(row, 0))
            q = _as_float(_row_val(row, "q") if hasattr(row, "keys") else _row_val(row, 1))
            n = _as_float(_row_val(row, "n") if hasattr(row, "keys") else _row_val(row, 2))
            out[a] = {"q": q, "n": n}
        return out

    # --------- Intern: Auto-Export (Crossing + Cooldown + Majority optional) -

    def _maybe_auto_export(self,
                           state_hash: str, action: str,
                           n_new: int, pos_new: int, neg_new: int, draw_new: int, q_new: float,
                           n_old: int, q_old: float,
                           centroid: Optional[List[float]]) -> None:
        if not self.auto_export_on:
            return
        if n_new < self.exp_min_n or abs(q_new) < self.exp_min_abs_q:
            return
        if self.exp_maj_conf > 0.0:
            counts = sorted([pos_new, neg_new, draw_new], reverse=True)
            top = counts[0]; second = counts[1] if len(counts) > 1 else 0
            conf = (top - second) / float(max(1, n_new))
            if conf < self.exp_maj_conf:
                return

        key = (self.namespace, state_hash, action)
        now = int(time.time())
        last = self._last_export_ts.get(key, 0)
        if last and (now - last) < self.exp_cooldown_s:
            return

        try:
            if _archiv and hasattr(_archiv, "upsert_policy"):
                _archiv.upsert_policy(self.namespace, state_hash, action, q_new, n_new, centroid)  # type: ignore[misc]
            elif _archiv and hasattr(_archiv, "upsert"):
                _archiv.upsert(self.namespace, state_hash, action, q_new, n_new, centroid)  # type: ignore[misc]
            else:
                self._direct_archive_upsert(self.namespace, state_hash, action, q_new, n_new, centroid)
            self._last_export_ts[key] = now
            LOG.debug("auto-export: %s | %s | a=%s | n=%d q=%.3f", self.namespace, state_hash, action, n_new, q_new)
        except Exception as e:
            LOG.debug("auto-export Fehler: %s", e)

    def _direct_archive_upsert(self, namespace: str, state_hash: str, action: str,
                               q: float, n: int, centroid: Optional[List[float]]) -> None:
        try:
            key = f'policy::{namespace}::{state_hash}::{action}'
            doc = {
                "type": "policy",
                "key": key,
                "namespace": namespace,
                "state_hash": state_hash,
                "action": action,
                "q": float(q),
                "n": int(n),
                "centroid": centroid if isinstance(centroid, list) else None,
                "updated_at": int(time.time())
            }
            content_str = json.dumps(doc, ensure_ascii=False, sort_keys=True)
            weight = (max(-1.0, min(1.0, float(q))) + 1.0) / 2.0
            now = time.time()
            with sql_manager.get_conn() as conn:
                try:
                    conn.execute("PRAGMA busy_timeout=8000")
                except Exception as e:
                    log_suppressed(LOG, key="policy_engine.pass.15", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
                like_pat = f'%\"key\": \"{key}\"%'
                row = conn.execute("SELECT id FROM rules WHERE content LIKE ? LIMIT 1", (like_pat,)).fetchone()
                if row:
                    rid = int(row["id"]) if hasattr(row, "keys") else int(row[0])
                    conn.execute(
                        "UPDATE rules SET content=?, weight=?, active=1, updated_at=? WHERE id=?",
                        (content_str, float(weight), now, rid)
                    )
                else:
                    conn.execute(
                        """INSERT INTO rules (content, weight, active, exported, created_at, updated_at)
                           VALUES (?,?,?,?,?,?)""",
                        (content_str, float(weight), 1, 0, now, now)
                    )
                conn.commit()
        except Exception as e:
            log_suppressed(LOG, key="policy_engine.pass.16", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


# =============================================================================
# CLI
# =============================================================================

def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ORÓMA PolicyEngine – State→Action Learning (RAM-first + Auto-Export)")
    ap.add_argument("--limit", type=int, default=2000, help="Max. Chains/Files fürs Training")
    ap.add_argument("--train-db", action="store_true", help="Aus DB trainieren (origin=Adapter-namespace, außer überschrieben)")
    ap.add_argument("--train-tmpfs", action="store_true", help="Aus Verzeichnis trainieren (tmpfs empfohlen)")
    ap.add_argument("--tmpfs-dir", type=str, default="", help="Pfad zu Verzeichnis (Default OROMA_TMPFS_DIR oder /run/oroma)")
    ap.add_argument("--namespace", type=str, default="",
                    help="Namespace/Origin-Filter (z. B. game:any). '' oder '*' → kein Filter (alle Origins).")
    ap.add_argument("--include-compressed", action="store_true",
                    help="Auch SnapChains mit status=compressed ins Training aufnehmen")
    ap.add_argument("--auto-export", action="store_true", help="Auto-Export erzwingen (über ENV)")
    ap.add_argument("--no-auto-export", action="store_true", help="Auto-Export deaktivieren (über ENV)")
    ap.add_argument("--export-min-n", type=int, default=-1, help="Schwelle: Mindest n (überschreibt ENV)")
    ap.add_argument("--export-min-abs-q", type=float, default=-1.0, help="Schwelle: Mindest |q| (überschreibt ENV)")
    ap.add_argument("--export-maj-conf", type=float, default=-1.0, help="Schwelle: Majority-Konfidenz [0..1] (überschreibt ENV)")
    ap.add_argument("--export-cooldown", type=int, default=-1, help="Sekunden Cooldown je (state,action) (überschreibt ENV)")
    ap.add_argument("--export-archiv", action="store_true", help="Manueller Komplett-Export (Schwellen separat via --min-n/--min-abs-q)")
    ap.add_argument("--min-n", type=int, default=1, help="Manueller Export: Mindest n")
    ap.add_argument("--min-abs-q", type=float, default=0.0, help="Manueller Export: Mindest |q|")
    ap.add_argument("--verbose", action="store_true", help="Mehr Logging")
    return ap.parse_args()


def _build_default_engine(ns_override: Optional[str] = None) -> PolicyEngine:
    eng = PolicyEngine(_DEFAULT_ADAPTER)
    if ns_override is not None:
        norm = _normalize_namespace(ns_override)
        if norm is not None:
            eng.namespace = str(norm)
    return eng


if __name__ == "__main__":
    args = _parse_args()
    if args.verbose:
        LOG.setLevel(logging.DEBUG)
        for h in LOG.handlers:
            try: h.setLevel(logging.DEBUG)
            except Exception: pass
        LOG.debug("Verbose an")

    try:
        eng = _build_default_engine(args.namespace or None)
    except Exception as e:
        LOG.error("Engine-Initialisierung fehlgeschlagen: %s", e)
        sys.exit(2)

    # CLI-Overrides für Auto-Export
    if args.auto_export:
        eng.auto_export_on = True
    if args.no_auto_export:
        eng.auto_export_on = False
    if args.export_min_n >= 0:
        eng.exp_min_n = int(args.export_min_n)
    if args.export_min_abs_q >= 0.0:
        eng.exp_min_abs_q = float(args.export_min_abs_q)
    if args.export_maj_conf >= 0.0:
        eng.exp_maj_conf = float(args.export_maj_conf)
    if args.export_cooldown >= 0:
        eng.exp_cooldown_s = int(args.export_cooldown)

    rc = 0

    # RAM-first: Files/Verzeichnis und/oder DB
    try:
        if args.train_tmpfs:
            eng.train_from_tmpfs(tmpfs_dir=(args.tmpfs_dir or None), limit=int(args.limit))
        if args.train_db:
            eng.train_from_db(limit=int(args.limit),
                              origin=(args.namespace if args.namespace != "" else None),
                              include_compressed=bool(args.include_compressed))
    except Exception as e:
        LOG.error("Training fehlgeschlagen: %s", e)
        rc = 1

    if args.export_archiv:
        try:
            eng.export_archiv(min_n=int(args.min_n), min_abs_q=float(args.min_abs_q))
        except Exception as e:
            LOG.error("Archiv-Export fehlgeschlagen: %s", e)
        # rc bleibt; Export-Fehler sind nicht kritisch fürs Training

    if not (args.train_tmpfs or args.train_db or args.export_archiv):
        LOG.info("Nichts zu tun – nutze --train-tmpfs / --train-db und/oder --export-archiv. Siehe --help.")

    sys.exit(rc)
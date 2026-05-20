#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gaps.py
# Projekt:   ORÓMA (Offline-First · Headless · Core Learning Signals)
# Modul:     Knowledge Gaps – leichte, robuste Wissenslücken-Tabelle (knowledge_gaps) + Lock-Retry Integration
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul verwaltet „Knowledge Gaps“ (Wissenslücken) als strukturiertes Signal:
#   - „Hier ist meine Datenbasis dünn / unsicher“
#   - „Hier gibt es einen Widerspruch“
#   - „Hier brauche ich mehr Evidenz / Exploration“
#
# Diese Gaps sind absichtlich:
#   - klein (stdlib-only)
#   - headless (keine UI-Abhängigkeiten)
#   - robust im Orchestrator-Betrieb (SQLite Locks)
#
# Typische Konsumenten/Erzeuger:
#   - Runtime Entscheider (z. B. Policy/UniversalPolicy/DecisionEngine)
#   - Offline Tools (DreamWorker / Analyse-Skripte)
#   - UI Badges (count/avg_confidence)
#
# DATENBANKSCHEMA
# ───────────────
# Tabelle: knowledge_gaps (in oroma.db; erstellt idempotent durch ensure_schema())
#   id         INTEGER PRIMARY KEY
#   ts         INTEGER  (Unix Epoch Sekunden)
#   kind       TEXT     (z. B. low_evidence, high_uncertainty, logic_conflict)
#   desc       TEXT     (Kurzbeschreibung, human readable)
#   confidence REAL     (0..1, subjektive Dringlichkeit/Sicherheit)
#   meta       TEXT     (JSON, frei: origin, namespace, state_hash, debug, etc.)
#
# Indizes:
#   idx_gap_ts ON knowledge_gaps(ts)
#
# ORCHESTRATOR / SQLITE LOCKS (WICHTIG)
# ─────────────────────────────────────
# ORÓMA schreibt oft parallel (Orchestrator + mehrere Jobs). SQLite erlaubt dennoch
# nur einen Writer-Commit gleichzeitig.
#
# add_gap() ist daher lock-robust:
#   - wenn verfügbar: nutzt sql_manager._run_with_lock_retry(fn, retry_sec)
#   - retry_sec wird über ENV OROMA_GAPS_LOCK_RETRY_SEC gesteuert (Default 2s)
#     (Fallback: OROMA_DB_LOCK_RETRY_SEC, Default 15s)
#
# Falls _run_with_lock_retry NICHT existiert:
#   - add_gap() führt eine kurze Insert-Transaktion aus (best effort)
#
# ROBUSTE ROW-AUSWERTUNG
# ─────────────────────
# list_gaps() und get_summary() arbeiten tolerant gegenüber Row-Typen:
#   - sqlite3.Row (mapping, row.keys())
#   - tuple/list (Indexzugriff)
# _row_get() kapselt das defensiv ab.
#
# JSON HANDLING
# ─────────────
# meta wird als JSON gespeichert:
#   - _json(obj) nutzt ensure_ascii=False und kompakte separators
#   - _from_json(txt) parse’t defensiv; bei Fehler wird der Rohtext zurückgegeben
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
# Debug:
#   OROMA_GAPS_DEBUG=1
#   OROMA_UP_GAPS_DEBUG=1          # kompatibler Alias
#   → aktiviert Warn/Debug-Ausgaben bei add_gap()-Fehlern
#
# Lock-Retry:
#   OROMA_GAPS_LOCK_RETRY_SEC=2
#   OROMA_DB_LOCK_RETRY_SEC=15
#
# Base (nur für sys.path Ergänzung – damit core importierbar bleibt):
#   OROMA_BASE=/opt/ai/oroma
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# ensure_schema() -> None
#   - idempotent: erstellt Tabelle/Index, wenn nicht vorhanden
#
# add_gap(kind: str, desc: str, confidence: float=0.0, meta: dict|None=None) -> int
#   - schreibt einen Gap-Eintrag (lock-robust)
#
# list_gaps(limit: int=100) -> List[dict]
#   - liefert neueste Gaps zuerst (id, ts, kind, desc, confidence, meta)
#
# get_summary() -> dict
#   - {ok, count, avg_confidence} für Badge/Übersicht
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Muss stdlib-only bleiben (kein numpy/pandas).
# - Muss im Lock-Fall stabil bleiben (Retry über sql_manager wenn vorhanden).
# - Muss tolerant zu Row-Varianten bleiben (sqlite3.Row vs tuple).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import logging
LOG = logging.getLogger("core_gaps")
from core.log_guard import log_suppressed
import os
import sys
import time
import sqlite3

# Optional: DBWriter (Stufe C Single-Writer). Wenn aktiv, werden Writes über
# den lokalen DBWriter-Daemon geroutet, um 'database is locked' zu eliminieren.
try:
    from core import db_writer_client  # type: ignore
    _DBW_OK = True
except Exception:
    db_writer_client = None  # type: ignore
    _DBW_OK = False

from typing import Any, Dict, List, Optional

BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE and BASE not in sys.path:
    sys.path.insert(0, BASE)

_LOG = logging.getLogger("oroma.gaps")

# Debug-Schalter
_GAPS_DEBUG = (
    (os.environ.get("OROMA_GAPS_DEBUG") or os.environ.get("OROMA_UP_GAPS_DEBUG") or "")
    .strip()
    .lower()
    in ("1", "true", "yes", "on")
)

# Lock-Retry-Wartezeit (Sekunden)
def _env_lock_retry_sec() -> int:
    """Spezifisches Retry-Fenster für Gap-Insert.

    Motivation:
      - Gap-Events sind "best effort" und dürfen niemals den Hauptpfad blockieren.
      - In der Praxis reicht ein kurzes Retry-Fenster (z. B. 2s), um transienten
        Writer-Contention zu überbrücken.
      - Das globale OROMA_DB_LOCK_RETRY_SEC (Default 15s) ist für kritische Writes
        gedacht und wäre hier unnötig langsam.
    """
    # 1) spezifisch
    try:
        v = int(str(os.environ.get("OROMA_GAPS_LOCK_RETRY_SEC", "")).strip() or "0")
        if v > 0:
            return v
    except Exception:
        pass
    # 2) fallback global
    try:
        v = int(str(os.environ.get("OROMA_DB_LOCK_RETRY_SEC", "15")).strip() or "15")
        return max(1, v)
    except Exception:
        return 15


_LOCK_RETRY_SEC = _env_lock_retry_sec()

# SQLite busy_timeout (Millisekunden)
# Hinweis: selbst mit _run_with_lock_retry kann ein zu niedriger busy_timeout
# unnötige "database is locked" Fehler erzeugen, wenn ein Writer gerade committet.
try:
    _BUSY_TIMEOUT_MS = int(os.environ.get("OROMA_DB_BUSY_TIMEOUT_MS", "5000"))
except Exception:
    _BUSY_TIMEOUT_MS = 5000

# SQL Manager laden (defensiv)
try:
    from core import sql_manager

    sql_manager.ensure_schema()
    _SQL_OK = True
except Exception as e:
    _SQL_OK = False
    if _GAPS_DEBUG:
        _LOG.warning("sql_manager import/ensure_schema failed: %s", e)


# ----------------------------- Utils -----------------------------

def _now() -> int:
    return int(time.time())


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _from_json(txt: Optional[str]) -> Any:
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception as e:
        log_suppressed(LOG, key="core_gaps.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return txt


def _row_get(row: Any, key: str, idx: int, default: Any = None) -> Any:
    """Robust: sqlite3.Row (mapping) oder tuple/list."""
    try:
        if hasattr(row, "keys"):
            return row[key]
        return row[idx]
    except Exception as e:
        log_suppressed(LOG, key="core_gaps.ret.2", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return default


# ----------------------------- Schema -----------------------------

def _dbw_enabled() -> bool:
    try:
        import os
        return bool(_DBW_OK and os.getenv('OROMA_DBW_ENABLE','').strip() not in ('', '0', 'false', 'False'))
    except Exception:
        return False

def ensure_schema() -> None:
    """Idempotent: stellt sicher, dass knowledge_gaps existiert."""
    if not _SQL_OK:
        return
    conn = sql_manager.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_gaps(
                id INTEGER PRIMARY KEY,
                ts INTEGER,
                kind TEXT,
                desc TEXT,
                confidence REAL,
                meta TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gap_ts ON knowledge_gaps(ts)")
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed(LOG, key="core_gaps.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


# ----------------------------- API -----------------------------

def add_gap(kind: str, desc: str, confidence: float = 0.0, meta: Optional[Dict[str, Any]] = None) -> int:
    """Neues Gap eintragen (fail-open bei SQLite-Lock).

    Warum:
      - Im Orchestrator-Betrieb laufen mehrere Writer-Jobs; trotzdem können
        kurzzeitige Locks auftreten (WAL-Checkpoint, externe Tools, etc.).
      - Ein Gap ist *Signal*, kein kritischer Pfad. Bei Lock darf ORÓMA nicht
        „blind/taub“ werden → kein Crash.

    Verhalten:
      - Wenn vorhanden, nutzt sql_manager._run_with_lock_retry(fn, retry_sec).
      - Bei Lock nach Retry: return 0 (WARN, rate-limited).
      - Bei anderen Fehlern: Exception (Bugs bleiben sichtbar).

    Rückgabe:
      - rowid (>0) bei Erfolg
      - 0 bei Lock-Fail (fail-open)
    """
    if not _SQL_OK:
        raise RuntimeError("sql_manager not available")

    k = str(kind or "").strip()
    d = str(desc or "").strip()
    if not k or not d:
        return 0

    ensure_schema()

    # Best-effort: Wenn DBWriter aktiv ist, route Write über den Single-Writer.
    # Das verhindert 'database is locked' in parallelen Daily-Runners/Dream/Service.
    if _dbw_enabled():
        try:
            to_ms = int(os.getenv('OROMA_DBW_GAPS_TIMEOUT_MS', '2000'))
        except Exception:
            to_ms = 2000
        try:
            rid = db_writer_client.exec_lastrowid(
                "INSERT INTO knowledge_gaps(ts, kind, desc, confidence, meta) VALUES(?,?,?,?,?)",
                [_now(), k, d, float(confidence or 0.0), _json(meta or {})],
                tag="gaps.add_gap",
                priority="low",
                timeout_ms=to_ms,
                db="oroma",
            )
            if int(rid or 0) > 0:
                return int(rid)
        except Exception as e:
            # Im Single-Writer-Modus kein lokaler Fallback mehr.
            log_suppressed(
                LOG,
                key="core_gaps.dbw.fail",
                msg="[gaps] DBWriter write failed – skip (no local fallback)",
                exc=e,
                level=logging.WARNING,
                interval_s=30,
            )
            return 0

    if _dbw_enabled():
        return 0

    def _do_once() -> int:
        conn = sql_manager.get_conn()
        try:
            # Defensive: busy_timeout lokal setzen, damit kurze Writer-Contention
            # nicht sofort als OperationalError eskaliert.
            try:
                conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_MS)}")
            except Exception as e:
                log_suppressed(
                    LOG,
                    key="core_gaps.pass.busy_timeout",
                    msg="Suppressed exception (busy_timeout)",
                    exc=e,
                    level=logging.DEBUG,
                    interval_s=600,
                )
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO knowledge_gaps(ts, kind, desc, confidence, meta) VALUES(?,?,?,?,?)",
                (_now(), k, d, float(confidence or 0.0), _json(meta or {})),
            )
            conn.commit()
            try:
                return int(cur.lastrowid)
            except Exception:
                return 0
        finally:
            try:
                conn.close()
            except Exception as e:
                log_suppressed(
                    LOG,
                    key="core_gaps.pass.4",
                    msg="Suppressed exception (was: close)",
                    exc=e,
                    level=logging.WARNING,
                    interval_s=600,
                )

    try:
        if hasattr(sql_manager, "_run_with_lock_retry"):
            try:
                return int(sql_manager._run_with_lock_retry(_do_once, int(_LOCK_RETRY_SEC)) or 0)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "database is locked" in msg or "locked" in msg:
                    log_suppressed(
                        LOG,
                        key="core_gaps.lock.1",
                        msg=f"[gaps] DB locked after retry (OROMA_DB_LOCK_RETRY_SEC={_LOCK_RETRY_SEC}s) – drop gap",
                        exc=e,
                        level=logging.WARNING,
                        interval_s=30,
                    )
                    return 0
                raise
        return _do_once()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "database is locked" in msg or "locked" in msg:
            log_suppressed(
                LOG,
                key="core_gaps.lock.2",
                msg="[gaps] DB locked – drop gap",
                exc=e,
                level=logging.WARNING,
                interval_s=30,
            )
            return 0
        if _GAPS_DEBUG:
            _LOG.warning("add_gap failed: %s", e)
        raise
    except Exception as e:
        if _GAPS_DEBUG:
            _LOG.warning("add_gap failed: %s", e)
        raise

def list_gaps(limit: int = 100) -> List[Dict[str, Any]]:
    """Liste der letzten Gaps (neueste zuerst)."""
    if not _SQL_OK:
        return []

    ensure_schema()
    conn = sql_manager.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, ts, kind, desc, confidence, meta FROM knowledge_gaps ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        )
        rows = cur.fetchall() or []

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(_row_get(r, "id", 0, 0) or 0),
                    "ts": int(_row_get(r, "ts", 1, 0) or 0),
                    "kind": _row_get(r, "kind", 2, ""),
                    "desc": _row_get(r, "desc", 3, ""),
                    "confidence": float(_row_get(r, "confidence", 4, 0.0) or 0.0),
                    "meta": _from_json(_row_get(r, "meta", 5, None)),
                }
            )
        return out
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed(LOG, key="core_gaps.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


def get_summary() -> Dict[str, Any]:
    """Aggregierte Statistik für Badge/Übersicht."""
    if not _SQL_OK:
        return {"ok": False, "error": "sql_manager not available"}

    ensure_schema()
    conn = sql_manager.get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) as n, AVG(confidence) as avg_conf FROM knowledge_gaps")
        row = cur.fetchone()
        n = _row_get(row, "n", 0, 0) if row is not None else 0
        avg_conf = _row_get(row, "avg_conf", 1, 0.0) if row is not None else 0.0
        return {
            "ok": True,
            "count": int(n or 0),
            "confidence": float(avg_conf or 0.0),
        }
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed(LOG, key="core_gaps.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


# ----------------------------- Selftest -----------------------------

def _selftest() -> None:
    print("[gaps] selftest…")
    ensure_schema()
    gid = add_gap("missing_fact", "Unbekannte Hauptstadt", 0.2, {"question": "What is the capital of X?"})
    print("  added gap id:", gid)
    items = list_gaps(limit=5)
    print("  list:", items)
    s = get_summary()
    print("  summary:", s)
    print("[gaps] OK ✅")


if __name__ == "__main__":
    _selftest()
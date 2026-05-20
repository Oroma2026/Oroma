#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/model_registry.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     Model Registry – DB-Registry (models/quality_history) + Runtime-Selection (mock) + DreamWorker-Compat (SnapChain Load)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# WICHTIGER HINWEIS (WARUM DIESE DATEI „MEHRERE ROLLEN“ HAT)
# ─────────────────────────────────────────────────────────
# Diese Datei ist historisch gewachsen und vereint aktuell 3 Funktionsbereiche,
# die im Projekt bewusst kompatibel gehalten werden:
#
#   (A) DB-Model-Registry (produktiv):
#       - Tabellen: models, quality_history (in oroma.db)
#       - Funktionen: add_model(), list_models(), set_active(), log_quality(), get_quality_history()
#
#   (B) UI-Runtime-Selection (derzeit „Mock“ / Minimal-State):
#       - verfügbare Modellnamen werden als Default-Listen geliefert (DEFAULT_MODELS)
#       - load_llm()/load_vision()/load_audio() setzen nur _state["llm"/"vision"/"audio"]
#       - status() liefert den aktuellen Auswahl-Status
#       - wird von ui/models_ui.py genutzt, um UI-Aktionen stabil zu bedienen,
#         auch wenn echte Runtime-Loader (noch) nicht angeschlossen sind.
#
#   (C) DreamWorker-Kompatibilitäts-Layer (SnapChain-Listing/Loading):
#       - list_recent(limit, status) liest SnapChains aus oroma.db Tabelle snapchains
#       - load_chain(id_or_path) lädt SnapChain by DB-ID (snapchains.blob) oder JSON-Datei
#       - Ziel: DreamWorker kann „optional zuerst“ über model_registry gehen, ohne dass
#         ältere Builds crashen, wenn diese Helper fehlen.
#
# Diese Mischung ist absichtlich non-breaking: UI, DreamWorker und Registry-DB
# funktionieren parallel. Kein Teil dieser Datei darf stillschweigend entfernt
# werden, weil sonst Schnittstellen im Feld brechen.
#
# =============================================================================
# (A) DB-MODEL-REGISTRY: ZWECK / DATENMODELL
# =============================================================================
#
# ZWECK
# ─────
# Persistente Verwaltung von „Modell-Metadaten“ (nicht die Gewichte selbst):
#   - Welche Modelle existieren? (Name, Typ, Version, Pfad, Meta)
#   - Welche sind aktiv? (active flag)
#   - Wie war die Qualität über die Zeit? (quality_history)
#
# WICHTIG: NON-DESTRUCTIVE POLICY
# ──────────────────────────────
# ORÓMA löscht Modelle nicht. Deaktivierung ist der Standard (active=0).
# Dadurch bleiben Historien und Reproduzierbarkeit erhalten.
#
# DB-ANBINDUNG (PRODUKTIONSRELEVANT)
# ─────────────────────────────────
# Dieses Modul nutzt bevorzugt core.sql_manager.get_conn(), damit:
#   - busy_timeout / WAL-Settings konsistent sind (Lock-Robustheit)
#   - RowFactory (dict-Rows) konsistent ist
# Fallback ist sqlite3.connect(DB_PATH).
#
# DB-PFAD
# ───────
# DB_PATH wird aus ENV gelesen:
#   OROMA_DB_PATH
# Fallback:
#   core.sql_manager.get_db_path() falls vorhanden, sonst /opt/ai/oroma/data/oroma.db
#
# PUBLIC API (DB-REGISTRY)
# ───────────────────────
# add_model(info: dict) -> int
#   - erwartet typ. Felder: name, type, version, path (+ optional meta/status)
#
# list_models(active_only: bool=False) -> List[Dict]
# set_active(model_id: int, active: bool) -> None
# log_quality(model_id: int, score: float) -> None
# get_quality_history(model_id: int) -> List[Dict]
#
# =============================================================================
# (B) UI-RUNTIME-SELECTION: AKTUELLER MINIMAL-STATE (MOCK)
# =============================================================================
#
# MOTIVATION
# ──────────
# Die UI soll „Modelle auswählen“ können, ohne dass zwingend ein schweres
# Runtime-Backend (llama.cpp/onnx/hailo/degirum/whisper) bereits integriert ist.
#
# Deshalb existiert ein interner Zustand:
#   _state = {"llm": None, "vision": None, "audio": None}
#
# Und Default-Listen:
#   DEFAULT_MODELS = {"llm":[...], "vision":[...], "audio":[...]}
#
# PUBLIC API (UI-SELECTION)
# ────────────────────────
# available_models() -> Dict[str, List[str]]
# load_llm(name) -> {ok: bool, msg|error: str}
# load_vision(name, backend="onnx") -> {ok: bool, msg|error: str}
# load_audio(name, kind="whisper") -> {ok: bool, msg|error: str}
# status() -> Dict[str, str|None]
#
# WICHTIG: Diese Loader laden KEINE echten Gewichte – sie dienen aktuell als
# UI-stabiler Platzhalter im Sinne von „Runtime-Auswahl merkt sich die Wahl“.
# Später kann man diese Funktionen mit echten Backends verbinden, ohne die UI-API
# zu ändern (kompatibler Upgrade-Pfad).
#
# =============================================================================
# (C) DREAMWORKER-COMPAT: SNAPCHAIN LISTING / LOADING
# =============================================================================
#
# Ziel:
#   DreamWorker kann optional:
#     - list_recent() nutzen, um jüngste SnapChains zu finden
#     - load_chain() nutzen, um Chains zu laden (DB oder JSON)
#
# list_recent(limit=20, status="active") -> List[Dict]
#   - liest id, ts, quality, origin, status, version aus Tabelle snapchains
#   - liefert [] bei fehlender Tabelle oder Fehler (best effort)
#
# load_chain(id_or_path) -> SnapChain | Dict | None
#   - DB by id: SELECT blob FROM snapchains WHERE id=?
#   - wenn core.snapchain.SnapChain verfügbar: SnapChain.from_blob(blob)
#   - sonst: json.loads(blob)
#   - FS by path: *.json Datei laden, optional SnapChain.from_dict(data)
#
# ROBUSTHEIT
# ──────────
# - Lazy Imports (sql_manager, SnapChain) um Zyklen zu vermeiden
# - Fehler werden suppressed geloggt (log_guard.log_suppressed), damit DreamWorker/UI
#   nicht hart ausfallen, wenn in Slim-Backups Tabellen fehlen.
#
# =============================================================================
# SELBSTTEST-BLOCK (HINWEIS)
# =============================================================================
#
# Diese Datei enthält Selftest-Codeblöcke unter `if __name__ == "__main__":`
# (historisch mehrfach). Das ist im Headless-Deploy unkritisch, solange die Datei
# als Modul importiert wird. Beim Direktaufruf gibt es Debug-Ausgaben.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import sqlite3
from core import db_writer_client as _dbw
import os
import time
from typing import List, Dict, Any, Optional
import logging
from core.log_guard import log_suppressed
LOG = logging.getLogger("oroma.model_registry")

# Verbindungshilfe
# -----------------------------------------------------------------------------
# Patch 2025-12-26:
#   Dieses Modul war historisch auf "/opt/ai/oroma/database/oroma.db" verdrahtet.
#   ORÓMA nutzt produktiv jedoch den Pfad aus core/sql_manager.get_db_path():
#       {BASE}/data/oroma.db
#   Damit die Tabellen (models, quality_history) tatsächlich in der aktiven DB
#   landen, richten wir den Default-Pfad an sql_manager aus – ENV OROMA_DB_PATH
#   bleibt als Override erhalten.
# -----------------------------------------------------------------------------
try:
    from core import sql_manager as _sqlm
except Exception:
    _sqlm = None  # type: ignore

_DEFAULT_DB_PATH = "/opt/ai/oroma/data/oroma.db"
try:
    if _sqlm and hasattr(_sqlm, "get_db_path"):
        _DEFAULT_DB_PATH = str(_sqlm.get_db_path())  # type: ignore[attr-defined]
except Exception as e:
    log_suppressed(LOG, key="model_registry.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

DB_PATH = os.environ.get("OROMA_DB_PATH", _DEFAULT_DB_PATH)



def _get_conn() -> sqlite3.Connection:
    """Interne Connection-Hilfe.

    Patch 2025-12-26:
      • bevorzugt core.sql_manager.get_conn(DB_PATH), damit PRAGMAs (busy_timeout,
        optional WAL) und RowFactory konsistent sind.
      • Fallback: sqlite3.connect(DB_PATH)

    Hinweis:
      sql_manager.get_conn() nutzt eine dict-RowFactory. Das ist im Projekt
      der Standard; bestehende Aufrufer (dict(r)) bleiben kompatibel.
    """
    try:
        if _sqlm and hasattr(_sqlm, "get_conn"):
            return _sqlm.get_conn(DB_PATH)  # type: ignore[attr-defined]
    except Exception as e:
        log_suppressed(LOG, key="model_registry.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# -----------------------------------------------------------------------------
# Registry-Funktionen
# -----------------------------------------------------------------------------



def _dbw_enabled() -> bool:
    try:
        return bool(int(os.getenv("OROMA_DBW_ENABLE", "0")))
    except Exception:
        return False

def add_model(info: Dict[str, Any]) -> int:
    """
    Neues Modell in Registry eintragen.
    Erwartet Keys: name, type, version, path
    """
    # Stufe C: Writes bevorzugt ueber DBWriter (oroma)
    if _dbw_enabled() and "_dbw" in globals() and _dbw is not None:
        sql = "INSERT INTO models (name, type, version, path, active, created_at) VALUES (?,?,?,?,?,?)"
        params = [info.get("name"), info.get("type"), info.get("version"), info.get("path"), 1, int(time.time())]
        return int(_dbw.exec_lastrowid(sql, params=params, tag="model_registry.add_model", priority="normal", timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS","60000")), db="oroma"))

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO models (name, type, version, path, active, created_at) VALUES (?,?,?,?,?,?)",
            (
                info.get("name"),
                info.get("type"),
                info.get("version"),
                info.get("path"),
                1,
                int(time.time()),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_models(active_only: bool = False) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if active_only:
            cur.execute("SELECT * FROM models WHERE active=1 ORDER BY id ASC")
        else:
            cur.execute("SELECT * FROM models ORDER BY id ASC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_active(model_id: int, active: bool) -> None:
    if _dbw_enabled() and '_dbw' in globals() and _dbw is not None:
        _dbw.exec_write("UPDATE models SET active=? WHERE id=?", [1 if active else 0, int(model_id)], tag="model_registry.set_active", priority="normal", timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS","60000")), db="oroma")
        return
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE models SET active=? WHERE id=?", (1 if active else 0, model_id))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def log_quality(model_id: int, score: float) -> None:
    if _dbw_enabled() and '_dbw' in globals() and _dbw is not None:
        _dbw.exec_write("INSERT INTO quality_history (model_id, score, ts) VALUES (?,?,?)", [int(model_id), float(score), int(time.time())], tag="model_registry.log_quality", priority="low", timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS","60000")), db="oroma")
        return
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO quality_history (model_id, score, ts) VALUES (?,?,?)", (model_id, float(score), int(time.time())))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_quality_history(model_id: int) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT ts, score FROM quality_history WHERE model_id=? ORDER BY ts ASC",
            (model_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print("📚 ORÓMA Model Registry Selftest")
    try:
        mid = add_model({"name": "test", "type": "asr", "version": "0.1", "path": "/tmp/test.onnx"})
        print("Added model ID:", mid)
        set_active(mid, True)
        log_quality(mid, 0.95)
        print("Models:", list_models())
        print("History:", get_quality_history(mid))
    except Exception as e:
        print("❌ Fehler:", e)

import os
import json
from typing import Dict, List

# Interner Zustand (vereinfacht – könnte später DB oder Config-Datei sein)
_state = {
    "llm": None,
    "vision": None,
    "audio": None,
}

# Verfügbare Modelle (Default-Listen, können via ENV angepasst werden)
DEFAULT_MODELS = {
    "llm": ["llama.cpp:tiny", "llama.cpp:small", "llama.cpp:medium"],
    "vision": ["mobilenet_v2", "yolov8", "resnet50"],
    "audio": ["whisper-tiny", "whisper-small", "whisper-medium"],
}

def available_models() -> Dict[str, List[str]]:
    """Liefert verfügbare Modellnamen (LLM, Vision, Audio)."""
    return DEFAULT_MODELS

def load_llm(name: str) -> Dict[str, str]:
    """LLM laden (Mock)."""
    if name not in DEFAULT_MODELS["llm"]:
        return {"ok": False, "error": f"Unbekanntes LLM: {name}"}
    _state["llm"] = name
    return {"ok": True, "msg": f"LLM '{name}' geladen"}

def load_vision(name: str, backend: str = "onnx") -> Dict[str, str]:
    """Vision-Modell laden (Mock)."""
    if name not in DEFAULT_MODELS["vision"]:
        return {"ok": False, "error": f"Unbekanntes Vision-Modell: {name}"}
    _state["vision"] = f"{name} (backend={backend})"
    return {"ok": True, "msg": f"Vision '{name}' mit Backend '{backend}' geladen"}

def load_audio(name: str, kind: str = "whisper") -> Dict[str, str]:
    """Audio-Modell laden (Mock)."""
    if name not in DEFAULT_MODELS["audio"]:
        return {"ok": False, "error": f"Unbekanntes Audio-Modell: {name}"}
    _state["audio"] = f"{name} (kind={kind})"
    return {"ok": True, "msg": f"Audio '{name}' als {kind} geladen"}

def status() -> Dict[str, str]:
    """Aktueller Status der geladenen Modelle."""
    return {
        "llm": _state.get("llm"),
        "vision": _state.get("vision"),
        "audio": _state.get("audio"),
    }

# =============================================================================
# Kompatibilitäts-Layer für DreamWorker (SnapChain-Listing/Loading)
# Projekt: ORÓMA – Headless, ohne GUI-Abhängigkeiten
# Version: v3.8-compat1
# Stand:   2025-10-15
#
# Zweck
# ─────
#   Der DreamWorker (v3.6+ / v3.7-r4) versucht optional zuerst über die
#   Model-Registry auf jüngste SnapChains zuzugreifen:
#       • model_registry.list_recent(limit=…)
#       • model_registry.load_chain(id_or_path)
#   In älteren Registry-Versionen fehlten diese Funktionen. Dieser Block
#   ergänzt sie additiv und delegiert sauber auf das SQL-Schema (Tabelle
#   'snapchains') bzw. im Notfall auf das Dateisystem.
#
# Eigenschaften
# ─────────────
#   • 100% headless (kein Qt/Wayland/X11).
#   • Keine Breaking Changes: bestehende Registry-APIs bleiben unberührt.
#   • Robust gegenüber fehlender Tabelle (liefert dann einfach []).
#
# Abhängigkeiten
# ──────────────
#   • core/sql_manager.ensure_schema() sollte einmalig gelaufen sein (bei dir OK).
#   • core/snapchain.SnapChain.from_blob() für DB-Load.
# =============================================================================

from typing import Any, Dict, List, Optional, Union
import os, json

# Lazy-Imports, um Zyklen zu vermeiden:
try:
    from core import sql_manager as _sql
except Exception:
    _sql = None  # type: ignore

try:
    from core.snapchain import SnapChain  # type: ignore
except Exception:
    SnapChain = None  # type: ignore


def list_recent(limit: int = 20, status: Optional[str] = "active") -> List[Dict[str, Any]]:
    """
    Liefert die jüngsten SnapChains aus der SQL-Tabelle 'snapchains'.
    Rückgabeformat (Liste von Dicts): [{id, ts, quality, origin, status, version}, ...]
    Fällt auf [] zurück, wenn keine Tabelle vorhanden oder ein Fehler auftritt.
    """
    try:
        if not _sql:
            return []
        q = "SELECT id, ts, quality, origin, status, version FROM snapchains"
        conds: List[str] = []
        args: List[Any] = []
        if status is not None:
            conds.append("status = ?")
            args.append(str(status))
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(int(max(0, limit)))

        with _sql.get_conn() as conn:
            rows = conn.execute(q, tuple(args)).fetchall()
            return [dict(r) for r in rows] if rows else []
    except Exception:
        # bewusst kein harter Fehler – DreamWorker kann danach seine
        # zweite/ dritte Pipeline (Langzeit/FS) nutzen
        return []


def load_chain(id_or_path: Union[int, str]) -> Optional[Union["SnapChain", Dict[str, Any]]]:
    """
    Lädt eine SnapChain:
      • Wenn 'id_or_path' wie eine Integer-ID aussieht → aus DB (snapchains.blob).
      • Wenn 'id_or_path' ein Pfad zu *.json ist → aus Datei (als Dict oder SnapChain).
    Rückgabe: SnapChain (falls Klasse verfügbar), sonst Dict; None bei Fehler.
    """
    # 1) Versuch: DB by ID
    try:
        if isinstance(id_or_path, int) or (isinstance(id_or_path, str) and str(id_or_path).isdigit()):
            if not _sql:
                return None
            chain_id = int(id_or_path)
            with _sql.get_conn() as conn:
                row = conn.execute("SELECT blob FROM snapchains WHERE id=?", (chain_id,)).fetchone()
                if not row:
                    return None
                blob = bytes(row["blob"])
                if SnapChain:
                    try:
                        sc = SnapChain.from_blob(blob)
                        sc.id = chain_id  # type: ignore[attr-defined]
                        return sc
                    except Exception:
                        # Fallback: JSON-Dict
                        return json.loads(blob.decode("utf-8"))
                # Kein SnapChain-Typ verfügbar → als Dict zurückgeben
                return json.loads(blob.decode("utf-8"))
    except Exception as e:
        log_suppressed(LOG, key="model_registry.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # 2) Versuch: FS-Pfad (*.json)
    try:
        if isinstance(id_or_path, str) and id_or_path.endswith(".json") and os.path.exists(id_or_path):
            with open(id_or_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if SnapChain and isinstance(data, dict):
                try:
                    # möglichst nah an DreamWorker-Konvention:
                    return SnapChain.from_dict(data)  # type: ignore[attr-defined]
                except Exception as e:
                    log_suppressed(LOG, key="model_registry.ret.4", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
                    return data
            return data
    except Exception as e:
        log_suppressed(LOG, key="model_registry.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    return None

# Selftest
if __name__ == "__main__":
    print("[model_registry] Verfügbare Modelle:", json.dumps(available_models(), indent=2))
    print("→ Lade LLM:", load_llm("llama.cpp:tiny"))
    print("→ Lade Vision:", load_vision("mobilenet_v2", backend="onnx"))
    print("→ Lade Audio:", load_audio("whisper-tiny", kind="asr"))
    print("→ Status:", json.dumps(status(), indent=2))
# =============================================================================
# Convenience facade
# =============================================================================
class ModelRegistry:
    """Kompakte OO-Fassade über das modulare model_registry.

    Hintergrund
    ----------
    Historisch wurde ORÓMA teils mit Modul-Funktionen (ensure_schema(), load_chain(), ...)
    genutzt, teils in Snippets/Tools als Klasse erwartet. Diese Fassade ist absichtlich
    klein und ruft die bestehenden Funktionen auf – ohne bestehendes Verhalten zu ändern.

    Hinweis
    -------
    `db_path` wird aktuell nur zur Kompatibilität akzeptiert. ORÓMA nutzt in diesem Modul
    primär DB_PATH (ENV: OROMA_DB_PATH). In produktiven Tools sollte weiter das Modul-API
    genutzt werden; diese Klasse ist v.a. für interaktive Snippets hilfreich.
    """
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH

    def ensure_schema(self):
        return ensure_schema()

    def list_recent_snapchains(self, limit: int = 50):
        return list_recent_snapchains(limit=limit)

    def load_chain(self, chain_id: str):
        return load_chain(chain_id)




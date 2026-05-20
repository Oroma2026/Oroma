#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/export_manager.py
# Projekt:   ORÓMA (Offline-First · Headless · Export/Transfer)
# Modul:     Export Manager – tar.gz Bundle Export von SnapChain-BLOBs (meta.json + chains/<id>.blob) + exported/archived Flags
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist ein **kleiner Export-Helper** für ORÓMA, der aus SnapChains ein
# portables **tar.gz Bundle** baut.
#
# Bundle-Inhalt:
#   - meta.json                 (Export-Metadaten + Policy + IDs)
#   - chains/<snapchain_id>.blob  (der rohe BLOB aus der Tabelle snapchains)
#
# Wichtig:
# - Dieses Modul ist headless und arbeitet ausschließlich datei-/sqlite-basiert.
# - Es ist primär ein „Manager/Helper“ und wird üblicherweise von UI/Admin-Logik
#   aufgerufen (nicht als interaktives CLI-Tool).
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine GUI-Abhängigkeiten.
# - Non-destructive Grundsatz:
#     • keine Deletes
#     • statt dessen Flagging:
#         - exported markieren
#         - optional status="archived" setzen
# - Portabel: Pfade werden über ENV + BASE abgeleitet (keine Hardcoded Pfadmagie).
#
# ABHÄNGIGKEITEN (EXAKT)
# ──────────────────────
# - core.sql_manager:
#     • ensure_schema()
#     • fetch_recent(limit, days)     → liefert Rows/Dicts mit mindestens: id, ts, quality, exported, status, origin
#     • get_conn()
#     • mark_exported(ids)
#     • set_status(ids, status)       (für optionales Archiving)
#
# - stdlib: os, io, json, tarfile, time, pathlib.Path, typing
#
# BASIS-PFADE (EXAKT IM CODE)
# ───────────────────────────
# BASE:
#   OROMA_BASE_DIR (default: "/opt/ai/oroma")
#
# EXPORT_DIR:
#   OROMA_EXPORT_DIR (default: BASE/"exports")
#
# TMP_DIR:
#   BASE/"tmp"  (wird angelegt, wird in diesem Modul aktuell nicht zwingend genutzt)
#
# Beide Verzeichnisse werden beim Import/Start angelegt:
#   EXPORT_DIR.mkdir(parents=True, exist_ok=True)
#   TMP_DIR.mkdir(parents=True, exist_ok=True)
#
# POLICY / DEFAULTS (EXAKT IM CODE)
# ─────────────────────────────────
# DEFAULT_DELAY_DAYS:
#   OROMA_EXPORT_DELAY_DAYS (default: "30")
#   → SnapChains müssen mindestens so alt sein, um „eligible“ zu sein
#
# DEFAULT_MIN_QUALITY:
#   OROMA_EXPORT_QUALITY_THRESHOLD (default: "0.7")
#
# WICHTIGER DETAILHINWEIS (AKTUELLER CODEVERTRAG)
# ───────────────────────────────────────────────
# - list_marked() filtert Eligibility ausschließlich nach:
#     • ts <= now - DEFAULT_DELAY_DAYS
#     • exported == 0
#   und nimmt quality NICHT als hartes Filter (liefert quality nur als Feld mit).
#
# - export_ready() prüft hingegen delay + exported + quality >= min_quality.
#
# - mark_all_recent() ermittelt Kandidaten (delay+quality+exported==0),
#   gibt aber aktuell NUR die Anzahl zurück (kein Persistieren/Markieren).
#
# Diese Unterschiede sind wichtig, damit man UI/Automation korrekt versteht:
# - „ready“ (Bool) kann True sein, obwohl list_marked() andere Kandidaten liefert.
# - Der echte Exportpfad (create_export_tar) exportiert **genau** das, was list_marked()
#   als eligible liefert.
#
# ÖFFENTLICHE API (EXAKT IM CODE)
# ───────────────────────────────
# mark_all_recent(days: int=7, min_quality: float=DEFAULT_MIN_QUALITY) -> int
#   - liest sql_manager.fetch_recent(limit=10000, days=days)
#   - zählt IDs, die:
#       • quality >= min_quality
#       • ts <= now - DEFAULT_DELAY_DAYS
#       • exported == 0
#   - Rückgabe: Anzahl der Kandidaten
#   - Side-Effect: keiner (keine DB-Updates)
#
# list_marked(limit: int=100) -> List[Dict[str,Any]]
#   - liest sql_manager.fetch_recent(limit=limit, days=365*5)
#   - liefert eine Liste „eligible“ Kandidaten:
#       eligible = (ts <= now - DEFAULT_DELAY_DAYS) and (exported == 0)
#   - Rückgabe-Items enthalten:
#       {"id":int,"ts":int,"quality":float,"status":..., "origin":...}
#
# export_ready(days_delay: int=DEFAULT_DELAY_DAYS, min_quality: float=DEFAULT_MIN_QUALITY) -> bool
#   - prüft, ob mindestens ein Eintrag existiert mit:
#       exported==0 AND ts <= now - days_delay AND quality >= min_quality
#
# _read_snapchain_blobs(ids: List[int]) -> List[Tuple[int, bytes]]
#   - liest Roh-BLOBs direkt aus snapchains:
#       SELECT id, blob FROM snapchains WHERE id IN (?,?,...)
#   - Rückgabe: Liste (id, bytes(blob))
#
# create_export_tar(archive_status: bool=False) -> Dict[str,Any]
#   - candidates = list_marked(limit=100000)
#   - wenn leer: {"ok":False,"error":"no eligible entries"}
#   - sonst:
#       • erstellt oroma_export_<ts>.tar.gz in EXPORT_DIR
#       • schreibt meta.json + chains/<id>.blob in tar.gz
#       • ruft danach sql_manager.mark_exported(ids)
#       • wenn archive_status True: sql_manager.set_status(ids,"archived")
#   - Rückgabe:
#       {"ok":True,"path": "<abs/rel path>", "count": <int>}
#
# EXPORT-BUNDLE-DETAILS (EXAKT)
# ─────────────────────────────
# meta.json (im tar root) enthält (genau so im Code aufgebaut):
#   {
#     "created_at": <ts>,
#     "version": "v3.5",                 # Achtung: Literal im Code; Header ist v3.7.3 (Dokumentation)
#     "count": <len(ids)>,
#     "policy": {
#       "delay_days": DEFAULT_DELAY_DAYS,
#       "min_quality": f">={DEFAULT_MIN_QUALITY}",
#       "no_delete": True,
#       "archived_on_export": <bool(archive_status)>
#     },
#     "ids": [ ... ]
#   }
#
# chains/<id>.blob:
# - enthält exakt die bytes aus snapchains.blob
# - mtime im TarInfo wird auf Export-ts gesetzt
#
# AUTH / SECURITY
# ───────────────
# Dieses Modul enthält keine Auth. Es ist eine Backend-Hilfsbibliothek.
# Zugriffsschutz/Rate-Limits sind Aufgabe der aufrufenden Flask-Routen bzw. des Reverse-Proxy.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - Keine Deletes (nur exported/status Flags setzen).
# - Tar-Struktur stabil halten: meta.json + chains/<id>.blob.
# - create_export_tar() muss nach erfolgreichem Tar-Write exported markieren (Audit/Idempotenz).
# - Eligibility-Regeln (delay/exported) dürfen nicht „still“ geändert werden – das beeinflusst ExportGate/Retention.
# - Pfade müssen über ENV konfigurierbar bleiben (BASE/EXPORT_DIR).
#
# =============================================================================
# END HEADER
# =============================================================================

import os
import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List

from core import sql_manager

# ---------------------------------------------------------------------------
# Basis-Verzeichnisse
# ---------------------------------------------------------------------------
BASE = Path(os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma"))
EXPORT_DIR = Path(os.environ.get("OROMA_EXPORT_DIR", BASE / "exports"))
TMP_DIR = BASE / "tmp"

EXPORT_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DELAY_DAYS = int(os.environ.get("OROMA_EXPORT_DELAY_DAYS", "30"))
DEFAULT_MIN_QUALITY = float(os.environ.get("OROMA_EXPORT_QUALITY_THRESHOLD", "0.7"))

# ---------------------------------------------------------------------------
# Funktionen
# ---------------------------------------------------------------------------
def mark_all_recent(days: int = 7, min_quality: float = DEFAULT_MIN_QUALITY) -> int:
    sql_manager.ensure_schema()
    rows = sql_manager.fetch_recent(limit=10000, days=days)
    mark_ids: List[int] = []
    now = int(time.time())
    delay_secs = DEFAULT_DELAY_DAYS * 86400
    for r in rows:
        if float(r["quality"]) >= float(min_quality):
            if int(r["ts"]) <= now - delay_secs and int(r["exported"]) == 0:
                mark_ids.append(int(r["id"]))
    return len(mark_ids)

def list_marked(limit: int = 100) -> List[Dict[str, Any]]:
    sql_manager.ensure_schema()
    rows = sql_manager.fetch_recent(limit=limit, days=365 * 5)
    out: List[Dict[str, Any]] = []
    now = int(time.time())
    delay_secs = DEFAULT_DELAY_DAYS * 86400
    for r in rows:
        eligible = (int(r["ts"]) <= now - delay_secs) and (int(r["exported"]) == 0)
        if eligible:
            out.append({
                "id": int(r["id"]),
                "ts": int(r["ts"]),
                "quality": float(r["quality"]),
                "status": r["status"],
                "origin": r["origin"],
            })
    return out

def export_ready(days_delay: int = DEFAULT_DELAY_DAYS,
                 min_quality: float = DEFAULT_MIN_QUALITY) -> bool:
    sql_manager.ensure_schema()
    now = int(time.time())
    delay_secs = int(days_delay) * 86400
    rows = sql_manager.fetch_recent(limit=10000, days=365 * 5)
    for r in rows:
        if (int(r["exported"]) == 0 and
            int(r["ts"]) <= now - delay_secs and
            float(r["quality"]) >= float(min_quality)):
            return True
    return False

def _read_snapchain_blobs(ids: List[int]):
    conn = sql_manager.get_conn()
    qmarks = ",".join("?" for _ in ids)
    cur = conn.execute(f"SELECT id, blob FROM snapchains WHERE id IN ({qmarks})", tuple(ids))
    return [(int(r["id"]), bytes(r["blob"])) for r in cur.fetchall()]

def create_export_tar(archive_status: bool = False) -> Dict[str, Any]:
    sql_manager.ensure_schema()
    candidates = list_marked(limit=100000)
    if not candidates:
        return {"ok": False, "error": "no eligible entries"}
    ids = [c["id"] for c in candidates]

    ts = int(time.time())
    out_name = f"oroma_export_{ts}.tar.gz"
    out_path = EXPORT_DIR / out_name

    blobs = _read_snapchain_blobs(ids)

    meta = {
        "created_at": ts,
        "version": "v3.5",
        "count": len(ids),
        "policy": {
            "delay_days": DEFAULT_DELAY_DAYS,
            "min_quality": f">={DEFAULT_MIN_QUALITY}",
            "no_delete": True,
            "archived_on_export": bool(archive_status),
        },
        "ids": ids,
    }

    with tarfile.open(out_path, "w:gz") as tar:
        meta_bytes = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="meta.json")
        info.size = len(meta_bytes)
        info.mtime = ts
        tar.addfile(info, io.BytesIO(meta_bytes))

        for sid, blob in blobs:
            binfo = tarfile.TarInfo(name=f"chains/{sid}.blob")
            binfo.size = len(blob)
            binfo.mtime = ts
            tar.addfile(binfo, io.BytesIO(blob))

    sql_manager.mark_exported(ids)
    if archive_status:
        sql_manager.set_status(ids, "archived")

    return {"ok": True, "path": str(out_path), "count": len(ids)}

# ---------------------------------------------------------------------------
# CLI (optional)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Export Manager – bitte über Flask-UI oder Admin-Skripte nutzen.")
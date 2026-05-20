#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/transfer_engine.py
# Projekt:   ORÓMA (Headless · SQLite-first · Knowledge Transfer Lite)
# Modul:     TransferEngine – Patch-1 kompatible TransferSnaps (sequence/pattern) + Export-Marking (score/len) + KPI metrics
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# TransferEngine ist in diesem Code-Stand (v3.7.3) eine **kleine, robuste Brücken-Utility**,
# die symbolische Sequenzen als „TransferSnaps“ persistiert und Kandidaten für Export/Archiv
# markieren kann.
#
# Wichtig:
# - Dieses Modul ist bewusst „Lightweight“ und enthält keine große Transfer-Pipeline.
# - Es stellt eine **Patch-1 API** bereit, damit ältere/andere Komponenten kompatibel bleiben.
#
# WAS HIER KONKRET ABGEBILDET WIRD
# ────────────────────────────────
# Ein TransferSnap ist ein Datensatz, der aus:
#   - sequence: Liste symbolischer Events (z. B. ["A:rise","V:edge","A:pause"])
#   - pattern : komprimierter Schlüssel/Label (z. B. "A↑→V|edge→A·pause")
# besteht und optional:
#   - score  : Bewertung (float)
#   - marked : exportwürdig (0/1)
#   - mark_ts: Timestamp der Markierung
#
# DB / PERSISTENZ (AKTUELLER STAND)
# ─────────────────────────────────
# Dieses Modul nutzt core.sql_manager als Primary:
#   - sql_manager.insert_transfer_snap(ts, sequence_csv, pattern)
#   - sql_manager.get_conn() für direkte SQL-Fallbacks
#   - sql_manager.insert_metric(...) für KPIs (best effort)
#
# Fallback (wenn insert_transfer_snap nicht existiert):
#   - Es wird eine Tabelle `transfer_snaps` bei Bedarf erzeugt:
#       id INTEGER PRIMARY KEY AUTOINCREMENT
#       ts INTEGER NOT NULL
#       sequence TEXT
#       pattern TEXT
#       score REAL
#       marked INTEGER DEFAULT 0
#       mark_ts INTEGER
#   - Danach wird ein Minimal-Insert ausgeführt.
#
# KPI / TELEMETRIE (BEST EFFORT)
# ──────────────────────────────
# Bei Bewertung/Markierung werden Metrics geschrieben (wenn möglich):
#   - kpi:export_considered  (jedes consider_export, bevor Thresholds greifen)
#   - kpi:export_marked      (wenn Markierung erfolgreich gesetzt wurde)
#
# Diese KPIs sind nicht kritisch:
# - Wenn metrics insert fehlschlägt oder DB locked ist → suppressed log, weiter.
#
# EXPORT-ENTSCHEIDUNG (consider_export)
# ─────────────────────────────────────
# consider_export(snap_id, score=..., min_score=0.80, min_len=2) prüft:
#   1) Score-Threshold: score >= min_score
#   2) Sequenz-Länge:   len(sequence.split(",")) >= min_len
#      (sequence wird aus transfer_snaps gelesen; wenn Query/Schema abweicht → best effort)
#
# Wenn beides erfüllt ist:
#   - mark_export(snap_id) setzt marked=1 und mark_ts=now (falls Spalten existieren)
#   - KPI kpi:export_marked wird erhöht
#
# KOMFORT-PFAD (process_sequence)
# ───────────────────────────────
# process_sequence(sequence, pattern, score=..., ...) führt in einem Schritt aus:
#   - save_pattern(...)
#   - best effort: UPDATE transfer_snaps SET score=...
#   - consider_export(...) → ggf. markieren
#
# LOCK-/FEHLERROBUSTHEIT
# ──────────────────────
# Alle DB-Aktionen sind kurz und best effort.
# „database is locked“ oder Schema-Varianten:
# - werden über log_guard.log_suppressed gedrosselt geloggt
# - führen nicht zum Crash
#
# ÖFFENTLICHE API (STABIL, WIRD VON ANDEREN TEILEN ERWARTET)
# ─────────────────────────────────────────────────────────
# TransferEngine.save_pattern(sequence: List[str], pattern: str) -> int
#   - speichert TransferSnap, gibt id zurück (oder 0 bei Fehler)
#
# TransferEngine.consider_export(snap_id: int, score: float, min_score: float=0.80, min_len: int=2) -> bool
#   - bewertet Kandidat; markiert bei Erfüllung; gibt True zurück, wenn markiert
#
# TransferEngine.mark_export(snap_id: int) -> bool
#   - setzt marked/mark_ts (best effort), True bei Erfolg
#
# TransferEngine.process_sequence(sequence: List[str], pattern: str, score: float, min_score: float=0.80, min_len: int=2) -> bool
#   - Komfortweg: speichern → score persistieren (best effort) → bewerten/markieren
#
# SELFTEST
# ────────
# Direktaufruf dieser Datei führt einen kleinen Selftest aus:
#   - save_pattern mit Beispielsequenz
#   - consider_export mit score 0.85
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Patch-1 API muss erhalten bleiben (save_pattern/consider_export/mark_export).
# - Fallback-Table-Create ist Absicht (Kompatibilität in minimalen Deploys).
# - KPI Inserts sind best effort (dürfen den Transfer nie blockieren).
# - Keine destruktiven Operationen (kein Delete/Prune).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import time
import logging
from core.log_guard import log_suppressed
from typing import Any, List, Optional, Sequence

# --- Logging -----------------------------------------------------------------
LOG = logging.getLogger("oroma.transfer_engine")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOG.addHandler(_h)
LOG.setLevel(logging.INFO)

# --- DB-Zugriff ---------------------------------------------------------------
try:
    from core import sql_manager  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"[transfer_engine] sql_manager fehlt: {e}")

try:
    from core import db_writer_client  # type: ignore
except Exception:
    db_writer_client = None  # type: ignore


def _dbw_enabled() -> bool:
    """
    True, wenn der globale DBWriter im aktuellen Prozess aktiv benutzt werden soll.

    Wichtig:
    - Bei aktivem DBWriter + Strict-Local-Writes dürfen lokale SQLite-Writes in
      verwaltete DBs nicht mehr als Fallback erfolgen.
    - Diese Hilfsfunktion kapselt die Entscheidung zentral, damit save_pattern(),
      mark_export() und process_sequence() konsistent arbeiten.
    """
    try:
        if db_writer_client is None:
            return False
        v = str(os.getenv("OROMA_DBW_ENABLE", "0")).strip().lower()
        return v in ("1", "true", "yes", "y", "on")
    except Exception:
        return False


def _dbw_timeout_ms(default: int = 60000) -> int:
    try:
        raw = os.getenv("OROMA_DBW_TIMEOUT_MS", "").strip()
        if raw:
            return max(200, int(raw))
    except Exception:
        pass
    return int(default)


def _dbw_exec_write(sql_stmt: str, params: Sequence[Any], *, tag: str, expect_lastrowid: bool = False) -> int:
    """
    Führt einen Write über den DBWriter aus.

    Rückgabe:
    - bei expect_lastrowid=True: lastrowid als int
    - sonst: rowcount als int (oder 0, wenn nicht geliefert)

    Es gibt bewusst **keinen** lokalen Write-Fallback, wenn DBWriter aktiv ist.
    Fehler müssen sichtbar bleiben, damit Restpfade nicht wieder verdeckt werden.
    """
    if db_writer_client is None:
        raise RuntimeError("db_writer_client unavailable")
    timeout_ms = _dbw_timeout_ms(60000)
    if expect_lastrowid:
        return int(db_writer_client.exec_lastrowid(
            sql_stmt,
            params=list(params),
            tag=tag,
            priority="normal",
            timeout_ms=timeout_ms,
            db="oroma",
        ))
    return int(db_writer_client.exec_write(
        sql_stmt,
        params=list(params),
        tag=tag,
        priority="normal",
        timeout_ms=timeout_ms,
        db="oroma",
    ) or 0)

# --- KPI Helper ---------------------------------------------------------------
def _kpi(name: str, v: float = 1.0) -> None:
    """Schreibt einen einfachen KPI-Zähler in metrics (best-effort)."""
    try:
        sql_manager.insert_metric(name, float(v))  # type: ignore[attr-defined]
    except Exception as e:
        log_suppressed(LOG, key="core_transfer_engine.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


class TransferEngine:
    """
    Engine für domänenübergreifende Muster (Cross-Domain Transfer).
    Erkennt Sequenzen und speichert sie als Transfer-Snaps; bewertet Kandidaten
    und markiert geeignete Muster für Export/Archiv.
    """

    # ----------------------------- Patch-1 API --------------------------------
    @staticmethod
    def save_pattern(sequence: List[str], pattern: str) -> int:
        """
        Legt einen Transfer-Snap an (Patch-1 kompatibel).
        sequence: Liste symbolischer Events, z. B. ["A:rise","V:edge","A:pause"]
        pattern:  komprimierte Bezeichnung/Schlüssel
        Rückgabe: ID des Datensatzes (oder 0 bei Fehler)

        DBWriter-Regel (Stufe C):
        - Wenn OROMA_DBW_ENABLE=1 aktiv ist, wird ausschließlich über den
          globalen Single-Writer geschrieben.
        - Es gibt dann **keinen** lokalen SQLite-Fallback mehr.
        - Nur wenn DBWriter in diesem Prozess wirklich deaktiviert ist, bleibt
          der historische lokale Kompatibilitätspfad erlaubt.
        """
        ts = int(time.time())
        seq_csv = ",".join(sequence)
        try:
            if _dbw_enabled():
                return _dbw_exec_write(
                    "INSERT INTO transfer_snaps (ts, sequence, pattern) VALUES (?, ?, ?)",
                    [int(ts), str(seq_csv), str(pattern)],
                    tag="transfer_engine.save_pattern",
                    expect_lastrowid=True,
                )
            return int(sql_manager.insert_transfer_snap(ts, seq_csv, pattern))  # type: ignore[attr-defined]
        except Exception:
            if _dbw_enabled():
                LOG.warning("save_pattern failed: DBWriter write failed (no local fallback)", exc_info=True)
                return 0
            # Fallback: Minimal-Insert (nur wenn DBWriter nicht aktiv ist)
            try:
                with sql_manager.get_conn() as conn:  # type: ignore[attr-defined]
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS transfer_snaps(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ts INTEGER NOT NULL,
                            sequence TEXT,
                            pattern TEXT,
                            score REAL,
                            marked INTEGER DEFAULT 0,
                            mark_ts INTEGER
                        );
                        """
                    )
                    cur = conn.execute(
                        "INSERT INTO transfer_snaps(ts, sequence, pattern, score, marked) VALUES (?,?,?,?,0)",
                        (ts, seq_csv, pattern, 0.0),
                    )
                    return int(cur.lastrowid)
            except Exception as e:
                LOG.warning("save_pattern failed: %s", e)
                return 0

    # --------------------------- Bewertung/Markierung -------------------------
    @staticmethod
    def consider_export(
        snap_id: int,
        *,
        score: float,
        min_score: float = 0.80,
        min_len: int = 2,
    ) -> bool:
        """
        Bewertet einen Kandidaten für Export/Archiv.
        Erhöht immer den KPI 'kpi:export_considered'. Markiert nur, wenn Schwellen erfüllt.
        """
        # KPI: Kandidat wird bewertet (vor Filter/Threshold)
        _kpi("kpi:export_considered", 1.0)

        # Länge prüfen (falls sequence vorhanden ist)
        try:
            with sql_manager.get_conn() as conn:  # type: ignore[attr-defined]
                row = conn.execute(
                    "SELECT sequence FROM transfer_snaps WHERE id=?", (int(snap_id),)
                ).fetchone()
                if row:
                    seq_txt = row[0] if not hasattr(row, "keys") else row["sequence"]
                    seq_len = len((seq_txt or "").split(",")) if seq_txt else 0
                    if seq_len < int(min_len):
                        return False
        except Exception as e:
            # Wenn Tabelle anders aussieht, nur Score-Pfad nutzen
            log_suppressed(
                logging.getLogger(__name__),
                key="core.transfer_engine.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
        if float(score) < float(min_score):
            return False

        # Markieren
        return TransferEngine.mark_export(snap_id)

    @staticmethod
    def mark_export(snap_id: int) -> bool:
        """
        Markiert den Transfer-Snap als exportwürdig (falls Spalten vorhanden).
        Erhöht den KPI 'kpi:export_marked'.

        Auch hier gilt im DBWriter-Modus: kein lokaler Write-Fallback.
        """
        ok = True
        try:
            now = int(time.time())
            if _dbw_enabled():
                _dbw_exec_write(
                    "UPDATE transfer_snaps SET marked=1, mark_ts=? WHERE id=?",
                    [int(now), int(snap_id)],
                    tag="transfer_engine.mark_export",
                    expect_lastrowid=False,
                )
            else:
                with sql_manager.get_conn() as conn:  # type: ignore[attr-defined]
                    # Versuche, Standard-Spalten zu setzen (marked/mark_ts)
                    try:
                        conn.execute(
                            "UPDATE transfer_snaps SET marked=1, mark_ts=? WHERE id=?",
                            (now, int(snap_id)),
                        )
                    except Exception as e:
                        # Fallback: existiert 'marked' nicht → kein harter Fehler
                        log_suppressed(
                            logging.getLogger(__name__),
                            key="core.transfer_engine.pass.2",
                            exc=e,
                            msg="Suppressed exception (was: pass)",
                        )
        except Exception as e:
            LOG.warning("mark_export(%s) failed: %s", snap_id, e)
            ok = False

        if ok:
            # KPI: Kandidat wird markiert/exportiert (nach Pass der Schwellen)
            _kpi("kpi:export_marked", 1.0)
        return ok

    # ------------------------------- Kurzweg ----------------------------------
    @staticmethod
    def process_sequence(
        sequence: List[str],
        pattern: str,
        *,
        score: float,
        min_score: float = 0.80,
        min_len: int = 2,
    ) -> bool:
        """
        Komfortweg: speichern → bewerten → ggf. markieren.
        Rückgabe: True, wenn markiert; sonst False.
        """
        snap_id = TransferEngine.save_pattern(sequence, pattern)
        if snap_id <= 0:
            return False
        # Score best-effort in DB persistieren (falls Spalte existiert)
        try:
            if _dbw_enabled():
                _dbw_exec_write(
                    "UPDATE transfer_snaps SET score=? WHERE id=?",
                    [float(score), int(snap_id)],
                    tag="transfer_engine.process_sequence.score",
                    expect_lastrowid=False,
                )
            else:
                with sql_manager.get_conn() as conn:  # type: ignore[attr-defined]
                    try:
                        conn.execute("UPDATE transfer_snaps SET score=? WHERE id=?", (float(score), int(snap_id)))
                    except Exception as e:
                        log_suppressed(LOG, key="core_transfer_engine.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        except Exception as e:
            log_suppressed(LOG, key="core_transfer_engine.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return TransferEngine.consider_export(snap_id, score=float(score), min_score=min_score, min_len=min_len)


# ------------------------------- Selftest ------------------------------------
if __name__ == "__main__":  # pragma: no cover
    LOG.info("TransferEngine Selftest …")
    seq = ["A:rise", "V:edge", "A:pause"]
    pid = TransferEngine.save_pattern(seq, "A↑→V|edge→A·pause")
    LOG.info("save_pattern → id=%s", pid)
    ok = TransferEngine.consider_export(pid, score=0.85, min_score=0.80, min_len=2)
    LOG.info("consider_export → marked=%s", ok)
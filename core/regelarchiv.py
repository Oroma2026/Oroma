#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/regelarchiv.py
# Projekt:   ORÓMA (Rules/Policy Archive · Headless · SQLite-safe)
# Modul:     Regelarchiv – Regeln + Policy-Export/Upsert in `rules` (Explainability + Runtime-Bridge)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul verwaltet ein kompaktes, persistentes Regelarchiv in SQLite:
#   Tabelle: `rules` (in oroma.db, via core.sql_manager.get_conn())
#
# Es dient zwei zentralen Zielen:
#   1) Explainability / Archiv:
#      - gelernte Regeln/Policy-Exports werden als JSON in rules.content gespeichert
#      - Regeln können aktiviert/deaktiviert, gewichtet und exportiert markiert werden
#
#   2) Runtime-Bridge:
#      - core.policy_engine kann gelernte Policy-Einträge in dieses Archiv exportieren
#      - DecisionEngine kann (je nach Build) rules.content interpretieren
#
# HEADLESS / STABILITÄT
# ────────────────────
# - keine UI-Abhängigkeiten, nur sqlite/json/logging
# - DB-Zugriff erfolgt ausschließlich über core.sql_manager (PRAGMAs/Locks zentral)
# - Schema/Indizes werden idempotent erzeugt (_ensure_schema)
#
# DATENMODELL (rules)
# ───────────────────
# Dieses Modul geht davon aus, dass `sql_manager.ensure_schema()` eine Tabelle
# `rules` bereitstellt. Es nutzt zusätzlich idempotente Indizes:
#   - idx_rules_active_weight: (active, weight)
#   - idx_rules_export:        (exported, created_at)
#
# In den SQL-Statements dieses Moduls werden u. a. folgende Felder genutzt:
#   id, content, weight, active, exported, created_at, updated_at
#
# content ist JSON-Text und enthält die eigentliche Regel/Policy-Struktur.
#
# WICHTIG: IDENTITÄT VON POLICY-REGELN
# ───────────────────────────────────
# Für Policy-Exports wird eine stabile Identität über einen „key“ im JSON genutzt:
#
#   key = "policy::<namespace>::<state_hash>::<action>"
#
# Da die Tabelle rules im aktuellen Stand nicht zwingend eine eigene key-Spalte hat,
# implementiert dieses Modul die Dedup/Update-Logik über:
#   - SELECT id FROM rules WHERE content LIKE "%<key>%" LIMIT 1
#
# Das ist bewusst pragmatisch (kompatibel mit bestehenden DBs), aber:
#   - KEY muss im JSON stets vorhanden sein, sonst entstehen Duplikate
#   - content LIKE ist teurer als eine echte key-Spalte → Indizes helfen nur begrenzt
#
# POLICY-WEIGHT (q → weight)
# ──────────────────────────
# upsert_policy(...) erwartet q ∈ [-1..+1] und mappt es auf weight ∈ [0..1]
# (einfaches, deterministisches Mapping, damit DecisionEngine & Listung stabil bleiben).
#
# FUNKTIONSBEREICHE
# ─────────────────
# 1) CRUD / Status:
#   - create_rule(content_json, weight, active, exported, created_at, updated_at)
#   - update_rule(rule_id, fields...)
#   - deactivate_rule(rule_id)
#   - activate_rule(rule_id)
#   - mark_exported(rule_id)
#   - get_rule(rule_id)
#   - list_rules(filters...)   (z. B. by active/exported/order/limit)
#
# 2) Export-Flags:
#   - reset_export_flags(): setzt exported=0 global (z. B. nach Neu-Export der Wissensbasis)
#
# 3) Policy Upsert (Bridge für core.policy_engine):
#   - upsert_policy(namespace, state_hash, action, q, n, centroid)
#     • erstellt oder aktualisiert eine Policy-Regel im Archiv
#     • Regelinhalt als JSON in rules.content
#     • setzt active=1
#     • setzt exported=0 (damit neue/aktualisierte Regeln wieder exportierbar sind)
#
# JSON-FORMAT (POLICY) – MINDESTFELDER (praktischer Vertrag)
# ─────────────────────────────────────────────────────────
# Dieses Modul schreibt typischerweise content mit:
#   {
#     "type": "policy",
#     "key": "policy::<namespace>::<state_hash>::<action>",
#     "namespace": "<namespace>",
#     "state_hash": "<canon-hash oder hash>",
#     "action": "<action>",
#     "q": <float [-1..+1]>,
#     "n": <int>,
#     "centroid": <list[float] | null>,
#     "ts": <unix>
#   }
#
# Hinweis:
# - DecisionEngine kann (je nach Build) solche JSON-Regeln konsumieren oder
#   sie bleiben „Archiv/Explainability“.
#
# ROBUSTHEIT / DB-LOCKS
# ────────────────────
# - Dieses Modul verlässt sich auf sql_manager (busy_timeout, WAL optional).
# - Write-Operationen sind kurz gehalten und vermeiden lange Transaktionen.
# - In Fehlersituationen wird geloggt und möglichst nicht geworfen, damit Trainer/UI
#   nicht komplett ausfallen.
#
# ÖFFENTLICHE API (STABIL, WICHTIG FÜR INTEGRATION)
# ────────────────────────────────────────────────
# - upsert_policy(...)         (core.policy_engine → Regelarchiv)
# - list_rules(...)            (UI / Debug / Export)
# - activate_rule/deactivate_rule/mark_exported/reset_export_flags
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from core.log_guard import log_suppressed

from core import sql_manager
try:
    from core import db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("oroma.regelarchiv")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

# Stabiler Präfix für Schlüssel in Policy-JSONs
_POLICY_KEY_PREFIX = "policy::"

# -----------------------------------------------------------------------------
# Interne Helfer
# -----------------------------------------------------------------------------


def _dbw_enabled() -> bool:
    """Prüft robust, ob der globale DBWriter für ORÓMA-Schreibpfade aktiv ist.

    Regelarchiv wird sowohl aus Runtime- als auch Dream-/Policy-Pfaden genutzt.
    Im Strict-Mode dürfen diese Pfade keine lokalen SQLite-Writes mehr ausführen.
    """
    try:
        if db_writer_client is None:
            return False
        v = str(os.environ.get("OROMA_DBW_ENABLE", "0") or "0").strip().lower()
        return v in ("1", "true", "yes", "on")
    except Exception:
        return False


def _dbw_timeout_ms(default: int = 5000) -> int:
    try:
        v = str(os.environ.get("OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM", "") or "").strip()
        return max(500, int(v)) if v else int(default)
    except Exception:
        return int(default)


def _dbw_exec_write(sql: str, params: tuple[Any, ...] | list[Any], *, tag: str, timeout_ms: int | None = None) -> int:
    if not _dbw_enabled():
        raise RuntimeError("db_writer not enabled")
    return int(db_writer_client.exec_write(
        sql,
        list(params),
        tag=tag,
        priority="low",
        timeout_ms=int(timeout_ms or _dbw_timeout_ms()),
        db="oroma",
    ))


def _dbw_transaction(stmts: list[tuple[str, list[Any] | tuple[Any, ...]]], *, tag: str, timeout_ms: int | None = None) -> dict[str, Any]:
    if not _dbw_enabled():
        raise RuntimeError("db_writer not enabled")
    norm = [(sql, list(params)) for sql, params in stmts]
    return dict(db_writer_client.transaction(
        norm,
        tag=tag,
        priority="low",
        timeout_ms=int(timeout_ms or _dbw_timeout_ms(15000)),
        db="oroma",
    ) or {})


def _clamp01(x: float) -> float:
    """Begrenzt einen Float sicher auf [0.0, 1.0]."""
    try:
        v = float(x)
    except Exception:
        return 0.0
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _policy_weight_from_q(q: float, n: int) -> float:
    """
    Leichtgewichtige Metrik, die q ∈ [-1, 1] auf weight ∈ [0, 1] mappt.
    (Optional könnte man n einfließen lassen; hier bewusst simpel.)
    """
    try:
        w = (float(q) + 1.0) / 2.0  # q=-1 -> 0.0 ; q=+1 -> 1.0
    except Exception:
        w = 0.5
    return _clamp01(w)


def _ensure_schema() -> None:
    """
    Stellt sicher, dass das DB-Schema existiert und sinnvolle Indizes vorhanden sind.
    Nutzt sql_manager.ensure_schema() und ergänzt Indizes idempotent.
    """
    sql_manager.ensure_schema()
    try:
        with sql_manager.get_conn() as conn:
            # Diese Indizes sind idempotent und verbessern die gängigen Abfragen.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_active_weight ON rules(active, weight)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_export ON rules(exported, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_updated_at ON rules(updated_at)")
            # Für schnelle Policy-Upserts: Suche nach dem key-Teil im JSON (LIKE)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_content ON rules(content)")
    except Exception as e:
        # Indizes sind „best effort“ – Schema darf dadurch nicht fehlschlagen.
        logger.debug("Index-Erzeugung übersprungen: %s", e)


# -----------------------------------------------------------------------------
# Public CRUD-API (generisch)
# -----------------------------------------------------------------------------

def add_rule(content: str, weight: float = 0.0) -> int:
    """
    Legt eine neue Regel an. Gibt die neue ID zurück.

    content: JSON-String (kanonisch sortiert empfohlen).
    weight:  Qualitätswert [0..1] (wird gecclampt).
    """
    _ensure_schema()
    now = time.time()
    w = _clamp01(weight)
    if _dbw_enabled():
        rid = int(db_writer_client.exec_lastrowid(
            """
            INSERT INTO rules (content, weight, active, exported, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            [str(content), float(w), 1, 0, now, now],
            tag="regelarchiv.add_rule",
            priority="low",
            timeout_ms=_dbw_timeout_ms(),
            db="oroma",
        ))
        logger.debug("add_rule: id=%s weight=%.3f dbw=1", rid, w)
        return rid
    with sql_manager.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO rules (content, weight, active, exported, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (str(content), float(w), 1, 0, now, now),
        )
        rid = int(cur.lastrowid)
        logger.debug("add_rule: id=%s weight=%.3f", rid, w)
        return rid


def update_rule(
    rule_id: int, *, weight: Optional[float] = None, content: Optional[str] = None
) -> None:
    """
    Aktualisiert Felder einer bestehenden Regel (weight/content) und updated_at.
    """
    _ensure_schema()
    sets: List[str] = []
    args: List[Any] = []

    if weight is not None:
        sets.append("weight=?")
        args.append(float(_clamp01(weight)))
    if content is not None:
        sets.append("content=?")
        args.append(str(content))

    # Immer updated_at setzen
    sets.append("updated_at=?")
    args.append(time.time())
    args.append(int(rule_id))

    sql = f"UPDATE rules SET {', '.join(sets)} WHERE id=?"
    if _dbw_enabled():
        _dbw_exec_write(sql, tuple(args), tag="regelarchiv.update_rule")
    else:
        with sql_manager.get_conn() as conn:
            conn.execute(sql, tuple(args))
    logger.debug("update_rule: id=%s sets=%s", rule_id, sets)


def deactivate_rule(rule_id: int) -> None:
    """Setzt active=0 und updated_at=NOW."""
    _ensure_schema()
    if _dbw_enabled():
        _dbw_exec_write(
            "UPDATE rules SET active=0, updated_at=? WHERE id=?",
            (time.time(), int(rule_id)),
            tag="regelarchiv.deactivate_rule",
        )
    else:
        with sql_manager.get_conn() as conn:
            conn.execute(
                "UPDATE rules SET active=0, updated_at=? WHERE id=?",
                (time.time(), int(rule_id)),
            )
    logger.debug("deactivate_rule: id=%s", rule_id)


def activate_rule(rule_id: int) -> None:
    """Setzt active=1 und updated_at=NOW."""
    _ensure_schema()
    if _dbw_enabled():
        _dbw_exec_write(
            "UPDATE rules SET active=1, updated_at=? WHERE id=?",
            (time.time(), int(rule_id)),
            tag="regelarchiv.activate_rule",
        )
    else:
        with sql_manager.get_conn() as conn:
            conn.execute(
                "UPDATE rules SET active=1, updated_at=? WHERE id=?",
                (time.time(), int(rule_id)),
            )
    logger.debug("activate_rule: id=%s", rule_id)


def mark_exported(rule_id: int) -> None:
    """Setzt exported=1 und updated_at=NOW (nach Export/Übernahme)."""
    _ensure_schema()
    if _dbw_enabled():
        _dbw_exec_write(
            "UPDATE rules SET exported=1, updated_at=? WHERE id=?",
            (time.time(), int(rule_id)),
            tag="regelarchiv.mark_exported",
        )
    else:
        with sql_manager.get_conn() as conn:
            conn.execute(
                "UPDATE rules SET exported=1, updated_at=? WHERE id=?",
                (time.time(), int(rule_id)),
            )
    logger.debug("mark_exported: id=%s", rule_id)


def get_rule(rule_id: int) -> Optional[Dict[str, Any]]:
    """Liest eine Regel als Dict, oder None wenn nicht vorhanden."""
    _ensure_schema()
    with sql_manager.get_conn() as conn:
        row = conn.execute("SELECT * FROM rules WHERE id=?", (int(rule_id),)).fetchone()
        return dict(row) if row else None


def list_rules(active_only: bool = True, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Liefert eine Liste von Regeln (neueste zuerst).
    Bei active_only=True (Default) nur aktive Regeln.
    """
    _ensure_schema()
    sql = "SELECT * FROM rules"
    args: List[Any] = []
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(int(limit))

    with sql_manager.get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall() or []
        return [dict(r) for r in rows]


def list_for_export(min_quality: float = 0.6, days_old: int = 30) -> List[Dict[str, Any]]:
    """
    Kandidaten-Liste für Export: aktive, noch nicht exportierte Regeln,
    die alt genug sind und eine Mindestqualität überschreiten.
    """
    _ensure_schema()
    cutoff = time.time() - (days_old * 86400)
    with sql_manager.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rules
             WHERE active=1
               AND exported=0
               AND weight>=?
               AND created_at<=?
             ORDER BY weight DESC, created_at ASC
            """,
            (float(_clamp01(min_quality)), float(cutoff)),
        ).fetchall() or []
        return [dict(r) for r in rows]


def count() -> int:
    """Anzahl aller Regeln (aktiv und inaktiv)."""
    _ensure_schema()
    with sql_manager.get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM rules").fetchone()
        return int(row["n"]) if row else 0


def reset_export_flags() -> None:
    """Setzt exported=0 für alle Regeln (z. B. nach Neuexport der Wissensbasis)."""
    _ensure_schema()
    if _dbw_enabled():
        _dbw_exec_write("UPDATE rules SET exported=0", (), tag="regelarchiv.reset_export_flags")
    else:
        with sql_manager.get_conn() as conn:
            conn.execute("UPDATE rules SET exported=0")
    logger.info("reset_export_flags: alle Export-Flags zurückgesetzt")


# -----------------------------------------------------------------------------
# POLICY-UPSERT-API (für core.policy_engine)
# -----------------------------------------------------------------------------

def upsert_policy(namespace: str,
                  state_hash: str,
                  action: str,
                  q: float,
                  n: int,
                  centroid: Optional[List[float]]) -> None:
    """
    Legt eine Policy-Regel an oder aktualisiert sie. Identität via 'key':
        key = "policy::<namespace>::<state_hash>::<action>"
    Die eigentliche Regel wird als JSON in 'content' gespeichert.

    q ∈ [-1, +1] → wird zu weight ∈ [0, 1] gemappt (einfaches Linear-Mapping).
    """
    _ensure_schema()

    key = f"{_POLICY_KEY_PREFIX}{namespace}::{state_hash}::{action}"
    doc = {
        "type": "policy",
        "key": key,
        "namespace": str(namespace),
        "state_hash": str(state_hash),
        "action": str(action),
        "q": float(q),
        "n": int(n),
        "centroid": centroid if isinstance(centroid, list) else None,
        "updated_at": int(time.time())
    }
    content_str = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    weight = _policy_weight_from_q(q, n)
    now = time.time()

    with sql_manager.get_conn() as conn:
        like_pat = f'%\"key\": \"{key}\"%'
        row = conn.execute(
            "SELECT id FROM rules WHERE content LIKE ? LIMIT 1",
            (like_pat,)
        ).fetchone()

    if row:
        rid = int(row["id"]) if hasattr(row, "keys") else int(row[0])
        if _dbw_enabled():
            _dbw_exec_write(
                "UPDATE rules SET content=?, weight=?, active=1, updated_at=? WHERE id=?",
                (content_str, float(weight), now, rid),
                tag="regelarchiv.upsert_policy.update",
            )
        else:
            with sql_manager.get_conn() as conn:
                conn.execute(
                    "UPDATE rules SET content=?, weight=?, active=1, updated_at=? WHERE id=?",
                    (content_str, float(weight), now, rid)
                )
    else:
        if _dbw_enabled():
            db_writer_client.exec_lastrowid(
                """
                INSERT INTO rules (content, weight, active, exported, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                [content_str, float(weight), 1, 0, now, now],
                tag="regelarchiv.upsert_policy.insert",
                priority="low",
                timeout_ms=_dbw_timeout_ms(),
                db="oroma",
            )
        else:
            with sql_manager.get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO rules (content, weight, active, exported, created_at, updated_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (content_str, float(weight), 1, 0, now, now)
                )


def upsert(namespace: str,
           state_hash: str,
           action: str,
           q: float,
           n: int,
           centroid: Optional[List[float]]) -> None:
    """Alias auf upsert_policy – kompatibel zu alternativen Aufrufern."""
    return upsert_policy(namespace, state_hash, action, q, n, centroid)


def save_rule(rule: Dict[str, Any]) -> None:
    """
    Erwartet bevorzugt ein Dict mit:
      {namespace, state_hash, action, q, n, centroid}
    und delegiert auf upsert_policy. Fällt ansonsten auf 'add_rule(content, weight)' zurück.
    """
    try:
        ns  = str(rule.get("namespace"))
        sh  = str(rule.get("state_hash"))
        act = str(rule.get("action"))
        q   = float(rule.get("q"))
        n   = int(rule.get("n"))
        cen = rule.get("centroid")
        if ns and sh and act:
            return upsert_policy(ns, sh, act, q, n, cen)
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.regelarchiv.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

    # Fallback: als normales Rule-Doc speichern (json + optionales 'weight')
    content_str = json.dumps(rule, ensure_ascii=False, sort_keys=True)
    add_rule(content_str, weight=_clamp01(float(rule.get("weight", 0.0))))


# -----------------------------------------------------------------------------
# PRUNE: schwache Regeln deaktivieren (SQLite-kompatibel)
# -----------------------------------------------------------------------------

def prune(threshold: float = 0.01, max_drop: int = 200) -> int:
    """
    Deaktiviert sehr schwache Regeln (weight < threshold) in Batch-Größe max_drop
    und „touch’t“ alte exportierte Regeln (updated_at refresh).
    Rückgabe: Anzahl veränderter Datensätze (Summe beider Updates).

    SQLite-Hinweis:
      - KEIN `UPDATE … LIMIT` möglich → Subselect mit LIMIT verwenden.
    """
    _ensure_schema()
    changed = 0
    now = time.time()

    if _dbw_enabled():
        result = _dbw_transaction([
            (
                """
                UPDATE rules
                   SET active=0,
                       updated_at=?
                 WHERE id IN (
                       SELECT id
                         FROM rules
                        WHERE active=1 AND weight < ?
                     ORDER BY weight ASC, updated_at ASC
                        LIMIT ?
                 )
                """,
                [now, float(_clamp01(threshold)), int(max_drop)],
            ),
            (
                "UPDATE rules SET updated_at=? WHERE exported=1 AND updated_at < ?",
                [now, now - 30 * 86400],
            ),
        ], tag="regelarchiv.prune", timeout_ms=_dbw_timeout_ms(15000))
        changed = int(result.get("rowcount") or 0)
    else:
        if _strict_local_writes_enabled():
            raise RuntimeError("regelarchiv.prune: local writes forbidden while DBWriter strict mode is active")
        with sql_manager.writer_lock('regelarchiv.prune'):
            with sql_manager.get_conn() as conn:
                # 1) sehr schwache Regeln deaktivieren (schwächste & älteste zuerst)
                cur = conn.execute(
                    """
                    UPDATE rules
                       SET active=0,
                           updated_at=?
                     WHERE id IN (
                           SELECT id
                             FROM rules
                            WHERE active=1 AND weight < ?
                         ORDER BY weight ASC, updated_at ASC
                            LIMIT ?
                     )
                    """,
                    (now, float(_clamp01(threshold)), int(max_drop)),  # threshold clamp
                )
                changed += max(0, cur.rowcount or 0)

                cur = conn.execute(
                    "UPDATE rules SET updated_at=? WHERE exported=1 AND updated_at < ?",
                    (now, now - 30 * 86400),
                )
                changed += max(0, cur.rowcount or 0)

    logger.info("prune: changed=%s (threshold=%.3f, max_drop=%d)", changed, threshold, max_drop)
    return int(changed)


# -----------------------------------------------------------------------------
# Selftest (optional)
# -----------------------------------------------------------------------------

def _selftest() -> None:
    print("[regelarchiv] selftest…")
    rid = add_rule(json.dumps({"type": "example", "msg": "hello"}, sort_keys=True), 0.42)
    print(" add:", rid)
    update_rule(rid, weight=0.55)
    r = get_rule(rid)
    print(" get:", r and r.get("id"), "weight=", r and r.get("weight"))
    print(" count:", count())
    # Policy-Upsert-Probe
    upsert_policy("game:tictactoe", "____X____", "4", q=0.7, n=12, centroid=[0]*9)
    # leichte Prune-Probe (tut nichts kaputt)
    n = prune(0.10, max_drop=50)
    print(" prune changed:", n)
    print("[regelarchiv] OK ✅")


if __name__ == "__main__":
    _selftest()

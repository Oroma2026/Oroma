#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess3_bootstrap_from_chess2.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess3 Bootstrap aus Chess2-Policy-Regeln
# Version: v3.8-r2
# Stand:   2026-03-21
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
# Zweck
# -----
# Dieses Tool bootstrapt die Chess3-Policy-Regelbasis direkt aus der bestehenden
# Chess2-Namespace innerhalb derselben ORÓMA-Datenbank. Damit startet Chess3
# nicht bei null, sondern übernimmt die bereits gelernte tabellarische Policy-
# Basis von Chess2 1:1 in eine neue, klar getrennte Namespace.
#
# Die Architekturentscheidung dahinter ist bewusst konservativ und produktiv:
# - gleiche physische DB / gleiche Tabellen / gleiche Infrastruktur
# - aber neue fachliche Namespace für Chess3
# - Wissen wird übernommen, Historie NICHT künstlich dupliziert
# - Chess2 bleibt Referenzlinie, Chess3 wird die neue Generation
#
# Umfang der Übernahme
# --------------------
# Dieses Tool kopiert bewusst NUR Datensätze aus `policy_rules`.
# NICHT kopiert werden z. B.:
# - episodes
# - episodic_metrics
# - snapchains
# - andere Historien-/Telemetry-Tabellen
#
# Sicherheitsregeln / Produktivverhalten
# --------------------------------------
# 1) Quelle muss Regeln enthalten, sonst Abbruch.
# 2) Ziel-Namespace muss leer sein, sonst Abbruch.
# 3) Kein stilles Überschreiben, kein DROP/DELETE.
# 4) Die Übernahme läuft in einer expliziten SQLite-Transaktion.
# 5) Das Ergebnis wird als JSON zusammengefasst ausgegeben.
# 6) Fehler werden NICHT verschwiegen. Bei Fehlern wird mit Exitcode != 0
#    beendet und ein verständlicher Fehlertext geliefert.
#
# Headless / Deployment
# ---------------------
# Das Tool ist vollständig headless-tauglich und verwendet nur stdlib plus die
# vorhandene ORÓMA-DB-Infrastruktur. Es kann sowohl direkt als Datei als auch
# als Modul gestartet werden.
#
# Nutzung
# -------
# Standard-Bootstrap mit Defaults:
#   python3 tools/chess3_bootstrap_from_chess2.py
#
# Nur prüfen, ohne zu schreiben:
#   python3 tools/chess3_bootstrap_from_chess2.py --dry-run
#
# Explizite Namespaces:
#   python3 tools/chess3_bootstrap_from_chess2.py \
#       --source-namespace game:chess2_canon_coop_king_territory \
#       --target-namespace game:chess3_canon_coop_king_territory_v1
#
# ENV / DB-Pfad
# -------------
# Standardmäßig nutzt das Tool dieselbe DB-Auflösung wie ORÓMA selbst über
# core.sql_manager.get_conn(). Optional kann über --db-path eine explizite DB
# verwendet werden.
#
# Wichtige Bemerkung
# ------------------
# Dieses v1-Tool erzwingt absichtlich ein leeres Ziel. Ein Force-/Replace-Modus
# ist hier bewusst NICHT enthalten, um versehentliche Vermischungen oder das
# Überschreiben einer bereits trainierten Chess3-Linie zu vermeiden.
# =============================================================================

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional


# Script-/Modul-Kompatibilität -------------------------------------------------
if __package__ in {None, ""}:
    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT_STR)

from core import sql_manager


DEFAULT_SOURCE_NAMESPACE = "game:chess2_canon_coop_king_territory"
DEFAULT_TARGET_NAMESPACE = "game:chess3_canon_coop_king_territory_v1"
POLICY_RULES_TABLE = "policy_rules"
REQUIRED_POLICY_RULES_COLUMNS = [
    "namespace",
    "state_hash",
    "action",
    "n",
    "pos",
    "neg",
    "draw",
    "q",
    "last_ts",
    "centroid",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrappt Chess3 aus Chess2 durch Kopie der policy_rules in eine "
            "neue Namespace. Das Ziel muss leer sein."
        )
    )
    parser.add_argument(
        "--source-namespace",
        default=DEFAULT_SOURCE_NAMESPACE,
        help="Quell-Namespace in policy_rules (Default: %(default)s)",
    )
    parser.add_argument(
        "--target-namespace",
        default=DEFAULT_TARGET_NAMESPACE,
        help="Ziel-Namespace in policy_rules (Default: %(default)s)",
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Expliziter Pfad zur ORÓMA-SQLite-DB; leer = Standardauflösung via sql_manager",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur prüfen und Statistik ausgeben, ohne Datensätze zu kopieren",
    )
    return parser.parse_args()


def _json_safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _get_db_path(explicit_db_path: str) -> str:
    db_path = str(explicit_db_path or "").strip()
    return db_path if db_path else str(sql_manager.get_db_path())


def _read_policy_rules_columns(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({POLICY_RULES_TABLE})").fetchall()
    cols: List[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            name = row.get("name")
        else:
            name = row[1] if len(row) > 1 else None
        name_s = str(name or "").strip()
        if name_s:
            cols.append(name_s)
    return cols


def _validate_policy_rules_schema(columns: List[str]) -> None:
    missing = [c for c in REQUIRED_POLICY_RULES_COLUMNS if c not in set(columns)]
    if missing:
        raise RuntimeError(
            "policy_rules-Schema unvollständig / unerwartet; fehlende Spalten: "
            + ", ".join(missing)
        )


def _count_namespace_rows(conn: sqlite3.Connection, namespace: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {POLICY_RULES_TABLE} WHERE namespace = ?",
        (namespace,),
    ).fetchone()
    if isinstance(row, dict):
        return _json_safe_int(row.get("n"))
    if row:
        return _json_safe_int(row[0])
    return 0


def _build_insert_sql() -> str:
    insert_cols = ", ".join(REQUIRED_POLICY_RULES_COLUMNS)
    select_cols = ", ".join(
        [
            "? AS namespace",
            "state_hash",
            "action",
            "n",
            "pos",
            "neg",
            "draw",
            "q",
            "last_ts",
            "centroid",
        ]
    )
    return (
        f"INSERT INTO {POLICY_RULES_TABLE} ({insert_cols}) "
        f"SELECT {select_cols} FROM {POLICY_RULES_TABLE} WHERE namespace = ?"
    )


def bootstrap_policy_rules(
    *,
    db_path: str,
    source_namespace: str,
    target_namespace: str,
    dry_run: bool,
) -> Dict[str, Any]:
    started_ts = int(time.time())
    result: Dict[str, Any] = {
        "ok": False,
        "db_path": db_path,
        "table": POLICY_RULES_TABLE,
        "source_namespace": source_namespace,
        "target_namespace": target_namespace,
        "dry_run": bool(dry_run),
        "started_ts": started_ts,
        "source_rule_count": 0,
        "target_rule_count_before": 0,
        "inserted_rule_count": 0,
        "target_rule_count_after": 0,
        "error": None,
    }

    with sql_manager.get_conn(db_path=db_path) as conn:
        columns = _read_policy_rules_columns(conn)
        _validate_policy_rules_schema(columns)

        source_count = _count_namespace_rows(conn, source_namespace)
        target_before = _count_namespace_rows(conn, target_namespace)
        result["source_rule_count"] = source_count
        result["target_rule_count_before"] = target_before
        result["schema_columns"] = list(columns)

        if source_count <= 0:
            raise RuntimeError(
                f"Quell-Namespace '{source_namespace}' enthält keine policy_rules"
            )
        if target_before > 0:
            raise RuntimeError(
                f"Ziel-Namespace '{target_namespace}' ist nicht leer ({target_before} Regeln)"
            )
        if source_namespace == target_namespace:
            raise RuntimeError("Quelle und Ziel dürfen nicht identisch sein")

        if dry_run:
            result["target_rule_count_after"] = target_before
            result["ok"] = True
            result["mode"] = "dry_run"
            result["finished_ts"] = int(time.time())
            result["duration_sec"] = max(0, result["finished_ts"] - started_ts)
            return result

        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(_build_insert_sql(), (target_namespace, source_namespace))
            inserted = _json_safe_int(cur.rowcount)
            target_after = _count_namespace_rows(conn, target_namespace)
            if target_after != source_count:
                raise RuntimeError(
                    "Plausibilitätsfehler nach Bootstrap: "
                    f"source={source_count} target_after={target_after} inserted={inserted}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        result["inserted_rule_count"] = inserted
        result["target_rule_count_after"] = target_after
        result["ok"] = True
        result["mode"] = "apply"
        result["finished_ts"] = int(time.time())
        result["duration_sec"] = max(0, result["finished_ts"] - started_ts)
        return result


def main() -> int:
    args = _parse_args()
    source_namespace = str(args.source_namespace or "").strip()
    target_namespace = str(args.target_namespace or "").strip()
    db_path = _get_db_path(str(args.db_path or ""))

    try:
        result = bootstrap_policy_rules(
            db_path=db_path,
            source_namespace=source_namespace,
            target_namespace=target_namespace,
            dry_run=bool(args.dry_run),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        err = {
            "ok": False,
            "db_path": db_path,
            "table": POLICY_RULES_TABLE,
            "source_namespace": source_namespace,
            "target_namespace": target_namespace,
            "dry_run": bool(args.dry_run),
            "error": str(exc),
        }
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

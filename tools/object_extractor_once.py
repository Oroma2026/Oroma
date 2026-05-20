#!/usr/bin/env python3
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/object_extractor_once.py
# Projekt: ORÓMA – KI-JWG-X1 (Headless)
# Version: v1.0
# Stand:   2025-12-27
# Autor:   Jörg Werner + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#  Dieses Tool führt den ObjectExtractor **einmalig** aus und liefert ein
#  nachvollziehbares Diagnose-Protokoll:
#
#    • Welche SceneGraph-Namespaces existieren in `scenegraphs`?
#    • Wie viele Treffer gibt es für die gewünschten Namespaces?
#    • Wieviele object_nodes / object_relations stehen danach in der DB?
#
# Hintergrund
# -----------
#  ORÓMA speichert SceneGraphs und ObjectGraphs als JSON in `scenegraphs`.
#  Für die UI (/objects) und Tools ist eine tabellarische Projektion hilfreich:
#
#    - object_nodes
#    - object_relations
#
#  Diese Projektion wird durch `core/object_extractor.py` erzeugt.
#
# Headless/Production Notes
# -------------------------
#  - Keine GUI, keine Qt/Wayland/X11-Abhängigkeiten.
#  - Robustes Logging, keine Schema-Änderungen ausser ensure_schema().
#  - Unterstützt exakte Namespaces und Prefix/Wildcards:
#      "object:auto:" oder "object:auto:*" oder "object:auto:%"
#
# Usage
# -----
#  Standard (neueste ObjectGraphs, 1 Stück):
#    PYTHONPATH=/opt/ai/oroma python3 tools/object_extractor_once.py
#
#  Bestimmte Namespaces:
#    PYTHONPATH=/opt/ai/oroma python3 tools/object_extractor_once.py \
#      --namespace object:auto:vision \
#      --namespace scene:auto_meta:vision_token \
#      --max-graphs 3
#
#  Prefix/Wildcard (robust gegen Suffixe):
#    PYTHONPATH=/opt/ai/oroma python3 tools/object_extractor_once.py \
#      --namespace-prefix object:auto:
#
#  Dry-Run:
#    PYTHONPATH=/opt/ai/oroma python3 tools/object_extractor_once.py --dry-run
#
# Exit Codes
# ----------
#  0 = ok
#  2 = Extractor lief, aber keine passenden SceneGraphs gefunden
#  3 = Fehler
# =============================================================================

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Tuple

from core import sql_manager
from core import object_extractor


def _ns_where(ns_list: List[str]) -> Tuple[str, List[str]]:
    exact: List[str] = []
    like: List[str] = []
    for ns in ns_list:
        s = (ns or "").strip()
        if not s:
            continue
        if s.endswith("*"):
            like.append(s[:-1] + "%")
        elif "%" in s:
            like.append(s)
        elif s.endswith(":"):
            like.append(s + "%")
        else:
            exact.append(s)

    clauses: List[str] = []
    params: List[str] = []
    if exact:
        clauses.append("namespace IN (" + ",".join("?" for _ in exact) + ")")
        params.extend(exact)
    if like:
        clauses.append("(" + " OR ".join("namespace LIKE ?" for _ in like) + ")")
        params.extend(like)

    where = " OR ".join(clauses) if clauses else "1=0"
    return where, params


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA – ObjectExtractor einmalig ausführen + Diagnose")
    ap.add_argument("--namespace", action="append", default=[], help="Exakter Namespace (mehrfach möglich)")
    ap.add_argument("--namespace-prefix", action="append", default=[], help="Prefix/Wildcard (z.B. object:auto: oder object:auto:*)")
    ap.add_argument("--max-graphs", type=int, default=1, help="Wie viele SceneGraphs (neueste zuerst) verarbeiten")
    ap.add_argument("--dry-run", action="store_true", help="Nur zählen, nichts schreiben")
    ap.add_argument("--db-path", default=None, help="Optionaler Pfad zur oroma.db (Default: sql_manager)")
    ap.add_argument("--show-top", type=int, default=12, help="Top N Namespaces anzeigen")
    args = ap.parse_args()

    namespaces: List[str] = []
    namespaces.extend([x.strip() for x in (args.namespace or []) if x and x.strip()])
    namespaces.extend([x.strip() for x in (args.namespace_prefix or []) if x and x.strip()])

    if not namespaces:
        # Default bewusst robust:
        target = (os.environ.get("OROMA_OBJECTGRAPH_TARGET_NS", "") or "").strip()
        namespaces = [target] if target else ["object:auto:"]

    # 1) Namespace-Übersicht
    with sql_manager.get_conn(args.db_path) as conn:
        top = conn.execute(
            "SELECT namespace, COUNT(*) AS c FROM scenegraphs GROUP BY namespace ORDER BY c DESC LIMIT ?",
            (int(args.show_top),),
        ).fetchall()
        print("[namespaces] top:", top)

    # 2) Treffer zählen
    #
    # IMPORTANT (Production/Headless):
    # sql_manager.get_conn() nutzt in ORÓMA bewusst eine Dict-RowFactory
    # (siehe sql_manager._row_factory), damit fast alle Call-Sites über
    # r["col"] arbeiten können.
    #
    # Dieses Tool ist aber älter und hat teils fetchone()[0] benutzt.
    # Bei Dict-Rows wirft das KeyError(0) → Ausgabe war dann nur "[ERROR] 0".
    #
    # Lösung: Wir geben der COUNT-Spalte einen Alias und lesen robust über
    # "c" (Dict) oder Index 0 (Tuple/Row).
    where, params = _ns_where(namespaces)
    with sql_manager.get_conn(args.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM scenegraphs WHERE " + where, params).fetchone()
        if isinstance(row, dict):
            total = int(row.get("c") or 0)
        else:
            total = int((row[0] if row else 0) or 0)
        print(f"[match] namespaces={','.join(namespaces)} -> scenegraphs={total}")

    if total == 0:
        print("[hint] Keine passenden SceneGraphs gefunden. Prüfe OROMA_DREAM_SCENEGRAPH/OBJECTGRAPH oder Namespace-Wahl.")
        return 2

    # 3) Extractor laufen lassen
    object_extractor.run_extractor(
        namespaces=namespaces,
        max_graphs=int(args.max_graphs) if args.max_graphs is not None else None,
        dry_run=bool(args.dry_run),
        verbose=True,
        db_path=args.db_path,
    )

    # 4) Ergebnis
    with sql_manager.get_conn(args.db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM object_nodes").fetchone()
        n_nodes = int((row.get("c") if isinstance(row, dict) else (row[0] if row else 0)) or 0)
        row = conn.execute("SELECT COUNT(*) AS c FROM object_relations").fetchone()
        n_rels = int((row.get("c") if isinstance(row, dict) else (row[0] if row else 0)) or 0)
        print(f"[result] object_nodes={n_nodes} object_relations={n_rels}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print("[ERROR]", e, file=sys.stderr)
        raise SystemExit(3)

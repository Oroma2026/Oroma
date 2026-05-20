#!/usr/bin/env python3
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/objectgraph_projection_selftest.py
# Projekt: ORÓMA – KI-JWG-X1 (Headless)
# Version: v1.0
# Stand:   2025-12-27
# Autor:   Jörg Werner + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
#  Schnelltest für das häufigste Problem:
#
#    „Dream läuft – aber object_nodes/object_relations bleiben 0.“
#
#  Dieser Selftest prüft:
#    1) Existieren SceneGraphs/ObjectGraphs in `scenegraphs`?
#    2) Welche Namespaces sind vorhanden?
#    3) Gibt es Treffer für object:auto:* oder dein OROMA_OBJECTGRAPH_TARGET_NS?
#    4) Läuft der ObjectExtractor und schreibt wirklich Tabellen?
#
#  Ergebnis:
#    - Exit 0: Projection ok (object_nodes/relations > 0)
#    - Exit 2: Keine passenden SceneGraphs vorhanden
#    - Exit 3: Fehler
#
# Usage
# -----
#  PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_projection_selftest.py
# =============================================================================

from __future__ import annotations

import os
import sys
from typing import List, Tuple

from core import sql_manager

# NOTE:
#  Der Selftest ist bewusst robust: Wenn `core.object_extractor` (z.B. durch
#  lokale Merge-/Patch-Fehler) nicht importierbar ist, wollen wir trotzdem die
#  wichtigsten Diagnosen (Namespaces, Counts) ausgeben.
try:
    from core import object_extractor  # type: ignore
except Exception as e:  # pragma: no cover
    object_extractor = None  # type: ignore
    _IMPORT_ERR = e
else:
    _IMPORT_ERR = None

def _scalar_first(row) -> int:
    """Robust: sql_manager kann dict-row_factory nutzen (KeyError bei [0])."""
    if row is None:
        return 0
    # dict / mapping
    if isinstance(row, dict):
        try:
            return int(next(iter(row.values())))
        except Exception:
            return 0
    # sqlite3.Row / tuple / list
    try:
        return int(row[0])
    except Exception:
        # fallback: first attribute
        try:
            return int(next(iter(row)))
        except Exception:
            return 0

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
    target = (os.environ.get("OROMA_OBJECTGRAPH_TARGET_NS", "") or "").strip()
    namespaces = [target] if target else ["object:auto:"]
    fallback = ["object:auto:"]

    with sql_manager.get_conn() as conn:
        top = conn.execute(
            "SELECT namespace, COUNT(*) AS c FROM scenegraphs GROUP BY namespace ORDER BY c DESC LIMIT 12"
        ).fetchall()
        print("[namespaces] top:", top)

    where, params = _ns_where(namespaces)
    with sql_manager.get_conn() as conn:
        cnt = _scalar_first(conn.execute("SELECT COUNT(*) FROM scenegraphs WHERE " + where, params).fetchone())
    print(f"[match] namespaces={','.join(namespaces)} -> scenegraphs={cnt}")

    if cnt == 0 and namespaces != fallback:
        where2, params2 = _ns_where(fallback)
        with sql_manager.get_conn() as conn:
            cnt2 = _scalar_first(conn.execute("SELECT COUNT(*) FROM scenegraphs WHERE " + where2, params2).fetchone())
        print(f"[match] fallback namespaces={','.join(fallback)} -> scenegraphs={cnt2}")
        if cnt2 > 0:
            namespaces = fallback
            cnt = cnt2

    if cnt == 0:
        print("[hint] Keine SceneGraphs/ObjectGraphs vorhanden. (Dream SceneGraph/ObjectGraph ggf. deaktiviert?)")
        return 2

    if object_extractor is None:
        print("[error] core.object_extractor konnte nicht importiert werden:")
        print(f"        {type(_IMPORT_ERR).__name__}: {_IMPORT_ERR}")
        print("[hint] Bitte core/object_extractor.py reparieren oder den Dream-Projection-Step nutzen.")
        return 3

    object_extractor.run_extractor(
        namespaces=namespaces,
        max_graphs=1,
        dry_run=False,
        verbose=True,
        db_path=None,
    )

    with sql_manager.get_conn() as conn:
        n_nodes = _scalar_first(conn.execute("SELECT COUNT(*) FROM object_nodes").fetchone())
        n_rels = _scalar_first(conn.execute("SELECT COUNT(*) FROM object_relations").fetchone())
    print(f"[result] object_nodes={n_nodes} object_relations={n_rels}")

    if n_nodes <= 0 or n_rels <= 0:
        print("[hint] Extractor lief, aber Tabellen bleiben leer. Prüfe Logs/Parsing (graph_json).")
        return 3

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print("[ERROR]", e, file=sys.stderr)
        raise SystemExit(3)

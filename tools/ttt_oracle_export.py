#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/ttt_oracle_export.py
# Projekt: ORÓMA – TicTacToe Oracle (Minimax) → Regelarchiv-Export
# Version: v1.0
# Stand:   2025-12-29
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
# Dieses Tool erzeugt für TicTacToe eine *perfekte* (spieltheoretisch optimale)
# Zug-Policy per Minimax und exportiert sie als JSON-Policy-Regeln in das
# Regelarchiv (Tabelle `rules`).
#
# Hintergrund (warum das wichtig ist)
# ----------------------------------
# ORÓMA lernt TicTacToe online über policy_rules/UniversalPolicy. Wenn Training
# gegen schwache Gegner oder unvollständige Exploration läuft, können Q-Werte
# fälschlich „Gewinn-Sicherheit“ suggerieren (z. B. q≈1.0 für Eröffnungen), was
# im Mirror-Self-Play zu *0% Draws* führt.
#
# Der Oracle-Export behebt das deterministisch:
#   • Für jeden erreichbaren Zustand (side-aware, D4-kanonisiert) wird
#     genau eine beste Aktion im *Kanonraum* exportiert.
#   • Vor dem Export werden vorhandene *Policy*-Regeln im Archiv für den
#     Namespace optional deaktiviert (nicht-destruktiv; bleibt in DB erhalten).
#   • DecisionEngine (regel-first) nutzt danach die Oracle-Regeln und spielt
#     gegen sich selbst praktisch immer Draw (perfektes Spiel).
#
# WICHTIG: Side-aware Kompatibilität
# ---------------------------------
# In ORÓMA ist TicTacToe side-aware: state_hash hängt von der Perspektive ab
# ("ich" = +1, "Gegner" = -1). Dieses Tool arbeitet wie die UI:
#   • Der Zustand ist immer aus Sicht des *Spielers am Zug*.
#   • Der Spielerwechsel wird durch Vorzeichenwechsel (vec *= -1) modelliert.
#   • Die D4-Kanonisierung (Symmetrien) wird über core.ttt_adapter.TTTAdapter
#     exakt so durchgeführt wie in UI/UniversalPolicy.
#
# Usage
# -----
#   cd /opt/ai/oroma
#   OROMA_DB_LOCK_RETRY_SEC=60 OROMA_DB_BUSY_TIMEOUT_MS=60000 \
#     PYTHONPATH=/opt/ai/oroma \
#     python3 tools/ttt_oracle_export.py --once
#
# Optionen
# --------
#   --namespace game:tictactoe     (default)
#   --deactivate-existing-policy   (default: 1)
#   --keep-existing-policy         (setzt deactivate=false)
#   --dry-run                      (nichts schreiben)
#   --max-export N                 (optional: begrenzt Export)
#
# Output
# ------
# JSON Summary auf stdout:
#   { ok, ts_run, namespace, dry_run, deactivate_existing_policy,
#     scanned_states, exported_rules, deactivated_count, duration_s }
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import time
from typing import Dict, List, Optional, Tuple

from core import sql_manager
from core import regelarchiv
from core.ttt_adapter import TTTAdapter


# TicTacToe Gewinnlinien (Index im 3x3 Raster)
_WINS = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def _now_ts() -> int:
    return int(time.time())


def _winner_value(vec_can: List[int]) -> Optional[int]:
    """Terminal-Auswertung aus Sicht des aktuellen Spielers (+1 = ich)."""
    for a, b, c in _WINS:
        v = vec_can[a]
        if v != 0 and v == vec_can[b] == vec_can[c]:
            return 1 if v == 1 else -1
    if all(v != 0 for v in vec_can):
        return 0
    return None


def _parse_vec_from_state_hash(sh: str) -> List[int]:
    """sh Format: 'v1|a,b,c,...' → List[int]"""
    try:
        if "|" not in sh:
            return [0] * 9
        _, payload = sh.split("|", 1)
        parts = payload.split(",")
        if len(parts) != 9:
            return [0] * 9
        return [int(x) for x in parts]
    except Exception:
        return [0] * 9


def _canon(adapter: TTTAdapter, vec: List[int]) -> Tuple[str, List[int]]:
    """Gibt (state_hash, vec_can) zurück."""
    sh, _, _ = adapter.canonicalize([float(x) for x in vec])
    return str(sh), _parse_vec_from_state_hash(str(sh))


def _policy_json(namespace: str, state_hash: str, action_can: int, q: float, n: int) -> str:
    """Kanonisch sortiertes JSON für rules.content."""
    d = {
        "type": "policy",
        "namespace": str(namespace),
        "state_hash": str(state_hash),
        "action": str(int(action_can)),
        "q": float(q),
        "n": int(n),
        "centroid": None,
        "src": "ttt_oracle_minimax",
        "ts_created": float(time.time()),
        "key": f"policy::{namespace}::{state_hash}::{int(action_can)}",
    }
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


class _Oracle:
    """Minimax mit Memoisierung auf kanonischen state_hash."""

    def __init__(self, adapter: TTTAdapter):
        self.adapter = adapter
        # memo[state_hash] = (best_value, best_action_can)
        self.memo: Dict[str, Tuple[int, Optional[int]]] = {}

    def solve(self, vec: List[int]) -> Tuple[int, Optional[int]]:
        sh, vec_can = _canon(self.adapter, vec)

        cached = self.memo.get(sh)
        if cached is not None:
            return cached

        w = _winner_value(vec_can)
        if w is not None:
            self.memo[sh] = (int(w), None)
            return self.memo[sh]

        legal = [i for i, v in enumerate(vec_can) if v == 0]
        best_val = -2
        best_act: Optional[int] = None

        # deterministische Reihenfolge
        for a in legal:
            nb = vec_can.copy()
            nb[a] = 1
            # Spielerwechsel: Perspektive flippen
            nb2 = [-x for x in nb]
            v2, _ = self.solve(nb2)
            my_v = -int(v2)
            if my_v > best_val:
                best_val = my_v
                best_act = int(a)
                if best_val == 1:
                    break  # cannot do better

        self.memo[sh] = (int(best_val), best_act)
        return self.memo[sh]


def _deactivate_existing_policy_rules(namespace: str) -> int:
    """Deaktiviert vorhandene JSON-Policy-Regeln im Archiv für den Namespace.

    WICHTIG:
    - keine lokalen Direktwrites auf managed DBs
    - Selektion darf read-only per sql_manager erfolgen
    - Deaktivierung selbst muss über den offiziellen regelarchiv/DBWriter-Pfad laufen
    """
    sql_manager.ensure_schema()

    like_ns_a = f'%"namespace":"{namespace}"%'
    like_ns_b = f'%"namespace": "{namespace}"%'
    like_type_a = '%"type":"policy"%'
    like_type_b = '%"type": "policy"%'

    ids: List[int] = []
    with sql_manager.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id
              FROM rules
             WHERE active=1
               AND (
                    (content LIKE ? OR content LIKE ?)
                AND (content LIKE ? OR content LIKE ?)
               )
            ORDER BY id
            """,
            (like_ns_a, like_ns_b, like_type_a, like_type_b),
        ).fetchall()
    for row in rows or []:
        try:
            rid = int(row["id"] if hasattr(row, "keys") else row[0])
            ids.append(rid)
        except Exception:
            continue

    changed = 0
    for rid in ids:
        regelarchiv.deactivate_rule(int(rid))
        changed += 1
    return int(changed)


def _upsert_oracle_rule(content_json: str, weight: float = 1.0) -> int:
    """Upsert über den offiziellen Regelarchiv-Pfad.

    Es gibt bewusst keinen lokalen SQLite-Write-Fallback. Das Tool darf auf managed
    DBs nur über regelarchiv/DBWriter schreiben. Die Rückgabe-ID ist für die
    Summary optional; bei DBWriter-Insert ist sie nicht zuverlässig verfügbar.
    """
    sql_manager.ensure_schema()

    try:
        d = json.loads(content_json)
    except Exception as exc:
        raise RuntimeError(f"invalid oracle policy json: {exc}")

    namespace = str(d.get("namespace") or "").strip()
    state_hash = str(d.get("state_hash") or "").strip()
    action = str(d.get("action") or "").strip()
    q = float(d.get("q") or 0.0)
    n = int(d.get("n") or 0)
    centroid = d.get("centroid") if isinstance(d.get("centroid"), list) else None

    if not namespace or not state_hash or action == "":
        raise RuntimeError("oracle policy payload missing namespace/state_hash/action")

    regelarchiv.upsert_policy(namespace, state_hash, action, q, n, centroid)

    key = str(d.get("key") or f"policy::{namespace}::{state_hash}::{action}")
    like_key = f'%"key": "{key}"%'
    with sql_manager.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM rules WHERE content LIKE ? ORDER BY id DESC LIMIT 1",
            (like_key,),
        ).fetchone()
    if row:
        return int(row["id"] if hasattr(row, "keys") else row[0])
    return -1


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA TicTacToe Oracle → rules export (minimax)")
    ap.add_argument("--once", action="store_true", help="Run once (default)")
    ap.add_argument("--namespace", default="game:tictactoe")
    ap.add_argument("--dry-run", action="store_true")

    ap.add_argument(
        "--deactivate-existing-policy",
        dest="deactivate_existing_policy",
        action="store_true",
        default=True,
        help="Deactivate existing JSON policy rules in rules for this namespace before exporting (default: on)",
    )
    ap.add_argument(
        "--keep-existing-policy",
        dest="deactivate_existing_policy",
        action="store_false",
        help="Do not deactivate existing JSON policy rules (may lead to mixed/unstable decisions)",
    )
    ap.add_argument("--max-export", type=int, default=0, help="Optional: limit exported rules (0 = no limit)")
    args = ap.parse_args()

    t0 = time.time()
    namespace = str(args.namespace or "game:tictactoe").strip() or "game:tictactoe"

    # Schema sicherstellen
    sql_manager.ensure_schema()
    adapter = TTTAdapter()
    oracle = _Oracle(adapter)

    # vollständiger State-Space aus leerem Board
    oracle.solve([0] * 9)

    deactivated = 0
    if args.deactivate_existing_policy and not args.dry_run:
        deactivated = _deactivate_existing_policy_rules(namespace)

    exported = 0
    ids: List[int] = []

    # deterministisch über state_hash sortieren
    items = sorted(oracle.memo.items(), key=lambda kv: kv[0])
    for sh, (val, act) in items:
        if act is None:
            continue  # terminal
        # Exportgewicht: immer 1.0 (Oracle gewinnt Rule-Ranking deterministisch)
        # q bleibt spieltheoretisch: +1 (Win), 0 (Draw), -1 (Loss)
        content = _policy_json(namespace, sh, int(act), float(val), int(100000))

        if not args.dry_run:
            rid = _upsert_oracle_rule(content, weight=1.0)
            ids.append(int(rid))
        exported += 1
        if args.max_export and exported >= int(args.max_export):
            break

    out = {
        "ok": True,
        "ts_run": _now_ts(),
        "namespace": namespace,
        "dry_run": bool(args.dry_run),
        "deactivate_existing_policy": bool(args.deactivate_existing_policy),
        "scanned_states": int(len(oracle.memo)),
        "exported_rules": int(exported),
        "deactivated_count": int(deactivated),
        "inserted_ids": ids[:50],
        "duration_s": round(time.time() - t0, 3),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

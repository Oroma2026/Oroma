#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/mutation.py
# Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:   Mutation – kontrollierte Regel-Mutationen (rules) + optionales Audit
# Version: v3.7 (stabil & auditierbar)
# Stand:   2026-04-18
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Leichte, kontrollierte Mutationen von Regeln in der `rules`-Tabelle:
#   • Auswahl schwach/alt aktualisierter aktiver Regeln
#   • Jitter-basierte Gewichtsänderung (bounded 0..1)
#   • Persistenz in EINER Transaktion (performant)
#   • Optionales Audit-Logging in `rule_mutations` + Metrics
#
# ÖFFENTLICHE API (RÜCKWÄRTSKOMPATIBEL)
# ─────────────────────────────────────
#   select_rules_for_mutation(limit=50, active_only=True, exclude_exported=True, ...)
#   mutate_weight(w, rate=0.1, noise=0.05, mode="jitter", target=None) -> float
#   mutate_rule(rule: dict, rate=0.1, noise=0.05, mode="jitter") -> dict
#   apply_mutations_and_persist(n=50, rate=0.1, noise=0.05, dry_run=False,
#                               seed=None, reason="auto", actor="auto",
#                               select_kwargs=None) -> List[dict]
#   mutate_chain(chain, rate=0.1) -> chain
#
# PRODUKTIONSINVARIANTEN
# ──────────────────────
# • Keine Änderungen an bestehenden Tabellen – Audit nutzt eine eigenständige Tabelle.
# • `sql_manager.ensure_schema()` wird respektiert; SQL-Operationen sind best-effort.
# • Audit ist “nice-to-have”: Fehler im Audit dürfen den Mutationslauf nicht abbrechen.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import time
import random
from typing import Any, Dict, List, Optional, Tuple
from core.log_guard import log_suppressed
import logging

from core import sql_manager

# -----------------------------------------------------------------------------
# Utils / Schema
# -----------------------------------------------------------------------------

def _now_ts() -> int:
    return int(time.time())

def _ensure_rules_schema() -> None:
    """Sicherstellt, dass das Basisschema aus sql_manager vorhanden ist."""
    sql_manager.ensure_schema()

def _ensure_audit_schema() -> None:
    """
    Legt eine optionale, eigenständige Audit-Tabelle an (idempotent).
    Keine Änderung an bestehenden Tabellen.
    """
    try:
        with sql_manager.get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rule_mutations(
                  id INTEGER PRIMARY KEY,
                  ts INTEGER NOT NULL,
                  rule_id INTEGER NOT NULL,
                  old_weight REAL NOT NULL,
                  new_weight REAL NOT NULL,
                  reason TEXT,
                  actor TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rule_mut_ts ON rule_mutations(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rule_mut_rule ON rule_mutations(rule_id)")
    except Exception as e:
        # audit ist nice-to-have – Fehler hier sollen Mutationslauf nicht abbrechen
        log_suppressed(
            logging.getLogger(__name__),
            key="core.mutation.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
def select_rules_for_mutation(
    limit: int = 50,
    active_only: bool = True,
    *,
    exclude_exported: bool = True,
    min_weight: Optional[float] = None,
    max_weight: Optional[float] = None,
    order: str = "oldest"  # "oldest" | "lowest" | "random"
) -> List[Dict[str, Any]]:
    """
    Wählt Regelkandidaten aus.
    - oldest:   älteste updated_at zuerst (Default, v3.5-kompatibel)
    - lowest:   geringstes weight zuerst
    - random:   zufällige Reihenfolge
    Filter:
      - active_only:      nur aktive Regeln
      - exclude_exported: exportierte Regeln überspringen
      - min/max_weight:   Gewichtsbereich begrenzen
    """
    _ensure_rules_schema()

    sql = "SELECT id, content, weight, active, exported, created_at, updated_at FROM rules"
    where: List[str] = []
    args: List[Any] = []

    if active_only:
        where.append("active=1")
    if exclude_exported:
        where.append("exported=0")
    if min_weight is not None:
        where.append("weight >= ?")
        args.append(float(min_weight))
    if max_weight is not None:
        where.append("weight <= ?")
        args.append(float(max_weight))

    if where:
        sql += " WHERE " + " AND ".join(where)

    if order == "lowest":
        sql += " ORDER BY weight ASC, updated_at ASC"
    elif order == "random":
        sql += " ORDER BY RANDOM()"
    else:  # oldest
        sql += " ORDER BY updated_at ASC"

    sql += " LIMIT ?"
    args.append(int(limit))

    with sql_manager.get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall() or []
        return [dict(r) for r in rows]

# -----------------------------------------------------------------------------
# Mutations
# -----------------------------------------------------------------------------

def _bounded(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return hi if x > hi else lo if x < lo else x

def mutate_weight(
    w: float,
    rate: float = 0.1,
    noise: float = 0.05,
    *,
    mode: str = "jitter",         # "jitter" | "anneal" | "towards"
    target: Optional[float] = None
) -> float:
    """
    Erzeugt ein neues Gewicht in [0,1].
      - jitter  : symm. Zufall um w (Default, v3.5-kompatibel)
      - anneal  : kleinerer Schritt bei extremen Werten (stabilisiert Enden)
      - towards : leichter Schritt in Richtung 'target' (0..1), plus leichtes Rauschen
    """
    w = float(w)
    rate = max(0.0, float(rate))
    noise = max(0.0, float(noise))

    if mode == "towards" and target is not None:
        tgt = _bounded(float(target))
        step = (tgt - w) * (rate * 0.5)
        jitter = random.gauss(0.0, noise * 0.25)
        return _bounded(w + step + jitter)

    if mode == "anneal":
        # je weiter von 0.5 entfernt, desto kleiner der Grundschritt
        scale = 1.0 - min(1.0, abs(w - 0.5) * 2.0)  # 1.0 @ 0.5, 0.0 @ {0,1}
        delta = (rate * scale) * (random.random() - 0.5) * 2.0 + random.gauss(0.0, noise * 0.25)
        return _bounded(w + delta)

    # Default: jitter (symmetrisch)
    delta = rate * (random.random() - 0.5) * 2.0 + random.gauss(0.0, noise * 0.25)
    return _bounded(w + delta)

def mutate_rule(
    rule: Dict[str, Any],
    rate: float = 0.1,
    noise: float = 0.05,
    *,
    mode: str = "jitter",
    target: Optional[float] = None
) -> Dict[str, Any]:
    """
    Liefert eine kopierte Regel mit mutiertem weight.
    Content bleibt unverändert (bewusst konservativ).
    """
    out = dict(rule)
    out["weight"] = mutate_weight(float(rule.get("weight", 0.5)), rate=rate, noise=noise, mode=mode, target=target)
    return out

# -----------------------------------------------------------------------------
# Persistenz (eine Transaktion, optionales Audit)
# -----------------------------------------------------------------------------

def _persist_rule_update_cur(cur, rule_id: int, weight: float, content: Optional[str] = None) -> None:
    sets, args = ["weight=?"], [float(weight)]
    if content is not None:
        sets.append("content=?")
        args.append(str(content))
    sets.append("updated_at=?")
    args.append(time.time())
    args.append(int(rule_id))
    cur.execute(f"UPDATE rules SET {', '.join(sets)} WHERE id=?", tuple(args))

def _persist_mutation_audit_cur(cur, rule_id: int, old_w: float, new_w: float, reason: str, actor: str) -> None:
    try:
        cur.execute(
            "INSERT INTO rule_mutations(ts, rule_id, old_weight, new_weight, reason, actor) VALUES (?,?,?,?,?,?)",
            (_now_ts(), int(rule_id), float(old_w), float(new_w), str(reason), str(actor)),
        )
    except Exception as e:
        # Audit optional – ignorieren, falls Tabelle nicht existiert
        log_suppressed(
            logging.getLogger(__name__),
            key="core.mutation.pass.2",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    try:
        sql_manager.insert_metric("rule_mutation_delta", float(new_w - old_w))  # type: ignore[attr-defined]
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.mutation.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

def apply_mutations_and_persist(
    n: int = 50,
    rate: float = 0.1,
    noise: float = 0.05,
    *,
    dry_run: bool = False,
    seed: Optional[int] = None,
    reason: str = "auto",
    actor: str = "auto",
    select_kwargs: Optional[Dict[str, Any]] = None,
    mode: str = "jitter",
    target: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    Führt bis zu n Mutationen durch und persistiert sie in EINER Transaktion.
    Rückgabe: Liste kurzer Änderungsobjekte.

    Parameter:
      - dry_run:  True → keine DB-Änderung (nur Simulation)
      - seed:     Reproduzierbarkeit (z. B. für Tests)
      - reason/actor: Audit-Felder
      - select_kwargs: Parameter an select_rules_for_mutation weiterreichen
      - mode/target:   Mutationsmodus (siehe mutate_weight)
    """
    if seed is not None:
        random.seed(int(seed))

    # Auswahl
    kw = dict(select_kwargs or {})
    kw.setdefault("limit", int(n))
    cands = select_rules_for_mutation(**kw)

    changed: List[Dict[str, Any]] = []
    if dry_run:
        for r in cands:
            rid = int(r["id"])
            old_w = float(r.get("weight", 0.5))
            new_w = mutate_weight(old_w, rate=rate, noise=noise, mode=mode, target=target)
            changed.append({"id": rid, "old_weight": old_w, "new_weight": new_w, "dry_run": True})
        return changed

    # Persistenz (eine Transaktion)
    _ensure_rules_schema()
    _ensure_audit_schema()
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()
        for r in cands:
            rid = int(r["id"])
            old_w = float(r.get("weight", 0.5))
            new_w = mutate_weight(old_w, rate=rate, noise=noise, mode=mode, target=target)
            _persist_rule_update_cur(cur, rid, new_w, content=None)
            _persist_mutation_audit_cur(cur, rid, old_w, new_w, reason, actor)
            changed.append({"id": rid, "old_weight": old_w, "new_weight": new_w})
        conn.commit()

    return changed

# -----------------------------------------------------------------------------
# Chain-level Mutation (non-destructive)
# -----------------------------------------------------------------------------

def mutate_chain(chain, rate: float = 0.1):
    """
    Leichte Variation auf Chain-Ebene (Dream-Replay):
      - erzeugt nach Moeglichkeit eine *Kopie* der Chain (nicht-destruktiv)
      - jitter auf resonance_score (bounded [-1,1])
      - metadata['mutated']=True + einfache Lineage-Hinweise
      - keine destruktiven Aenderungen an Strukturen/Patterns

    Motivation:
      - DreamWorker speichert mutierte Ableitungen als origin='dream/mut'.
      - LangzeitGedaechtnis dedupliziert ueber einen stabilen Hash;
        echte "Mutationen" muessen daher als abgeleitete Artefakte behandelt werden.
      - Diese Funktion bleibt bewusst konservativ und headless-geeignet.
    """
    try:
        # ------------------------------------------------------------
        # 1) Defensive Kopie (falls SnapChain verfuegbar)
        # ------------------------------------------------------------
        mutated = chain
        try:
            from core.snapchain import SnapChain  # lokale Importierung: minimiert Startup-Kosten
            if isinstance(chain, SnapChain):
                mutated = SnapChain.from_dict(chain.to_dict())
        except Exception:
            mutated = chain

        # ------------------------------------------------------------
        # 2) Konservative Variation
        # ------------------------------------------------------------
        mutated.resonance_score = _bounded(
            float(getattr(mutated, "resonance_score", 0.0)) + random.uniform(-rate, rate),
            -1.0,
            1.0,
        )

        md = dict(getattr(mutated, "metadata", {}) or {})
        md["mutated"] = True
        md.setdefault("mut_ts", time.time())

        # Lineage: best effort; nicht hash-relevant, aber hilfreich fuer Debug/UI.
        try:
            parent_md = getattr(chain, "metadata", {}) or {}
            if isinstance(parent_md, dict):
                if "mut_parent_origin" not in md and "origin" in parent_md:
                    md["mut_parent_origin"] = parent_md.get("origin")
                if "mut_parent_game" not in md and "game" in parent_md:
                    md["mut_parent_game"] = parent_md.get("game")
        except Exception:
            pass

        mutated.metadata = md
        return mutated
    except Exception:
        # Fail-open: niemals den DreamWorker brechen.
        return chain

def _selftest() -> None:
    print("[mutation] selftest…")
    _ensure_rules_schema()
    _ensure_audit_schema()
    # Dummy-Regel anlegen
    with sql_manager.get_conn() as conn:
        conn.execute(
            "INSERT INTO rules (content, weight, active, exported, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("test_rule", 0.5, 1, 0, _now_ts(), _now_ts()),
        )
        conn.commit()
    # Dry-Run
    sim = apply_mutations_and_persist(n=3, rate=0.1, dry_run=True, seed=42)
    print(" dry_run:", sim)
    # Live
    changed = apply_mutations_and_persist(n=5, rate=0.1, noise=0.05, seed=1337,
                                          reason="selftest", actor="mutation/_selftest",
                                          select_kwargs={"order": "oldest"})
    print(" changed:", changed[:3], "…")
    print("[mutation] OK ✅")

if __name__ == "__main__":
    _selftest()
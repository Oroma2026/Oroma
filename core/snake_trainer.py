#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/snake_trainer.py
# Projekt: ORÓMA
# Modul:   Snake Trainer (DB→policy_rules + optional Archiv-Export)
# Version: v3.7.4-r1
# Stand:   2025-11-09
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# ─────
#  Lernt aus Snake-SnapChains in SQLite (origin LIKE 'game:snake%'):
#    • Pfad A: versteht *snaps/events* mit metadata.action/reward (12D möglich)
#    • Pfad B: versteht *patterns* (reine Vektorlisten)
#         – rekonstruiert action aus Kopfbewegung Δ(hx,hy) (Wraparound-fest)
#         – liest terminal reward aus 1D-Feature am Ende (z. B. [0.5], [-2.0])
#    • schreibt State→Action via sql_manager.policy_upsert()
#    • optional: exportiert starke Policy-Einträge nach rules (Archiv)
#
# Feature-Schemata
# ────────────────
#   (1) Kompakt/7D pro Tick (historisch, Patterns):
#       [len_self, len_op, hx_n, hy_n, fx_n, fy_n, tick]
#
#   (2) Adapter/12D pro Tick (SnakeAdapter; bevorzugt bei snaps/events):
#       [len_self, len_op, hx_n, hy_n, fx_n, fy_n,
#        rel_dxN, rel_dyN, danger_R, danger_L, danger_D, danger_U]
#       • danger_* ∈ {0.0, 1.0}
#       • Action-IDs: 0=Right, 1=Left, 2=Down, 3=Up
#
# Hashing (diskretisiert; stabil)
# ───────────────────────────────
#   state_hash v1 aus ersten 7 Dimensionen:
#     v1|L{len_bucket}|P{op_bucket}|H{Hx}-{Hy}|F{Fx}-{Fy}|T{tick/10}
#
# Reward/Outcome
# ──────────────
#   • 1) Explizite Rewards aus snaps/events gewinnen Priorität.
#   • 2) Fallback: terminale 1D-Komponente als Reward interpretieren.
#   • 3) Fallback Heuristik (Distanz Kopf→Futter Anfang vs. Ende):
#        näher  → ("pos", +0.2), weiter → ("neg", -0.2), sonst draw/0.0
#   • 4) Schrittweises Shaping (wenn 12D verfügbar + Action bekannt):
#        – gewählte Richtung gefährlich?          → -0.35  (self/blocked)
#        – „Wunschrichtung“ ist blockiert?        → -0.25  (op_blocked_primary)
#        – Bewegung in grobe Zielrichtung (rel)   → +0.05  (toward_food)
#     Der pro Schritt berechnete shaping_reward wird zum Episoden-Reward
#     addiert (geclamped [-1, +1]); Outcome-Flag pro Schritt wird daraus
#     abgeleitet (pos/neg/draw).
#
# Export (Archiv)
# ───────────────
#   • Primär Q-basiert: n≥min_n UND ABS(q)≥min_abs_q
#   • Fallback Majority: pro state_hash die zählstärkste Action mit
#       confidence = (n_best - n_second)/n_total  ≥ majority_min_conf
#     → Export in rules mit weight = confidence
#
# ENV-Schalter
# ────────────
#   • OROMA_SNAKE_SHAPING = 1|true|yes (Default: an)
#       → schaltet die per- Schritt-Formung (12D) ein/aus (pure Snaps möglich)
# =============================================================================

from __future__ import annotations
import os, sys, json, time, argparse
from typing import Any, Dict, Iterable, List, Optional, Tuple
from core.log_guard import log_suppressed
import logging

if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

from core import sql_manager

Vec = List[float]
SHAPING_ENABLED = os.environ.get("OROMA_SNAKE_SHAPING", "1").lower() in ("1", "true", "yes")

# ------------------------- Quantisierung / Hash v1 ----------------------------

_LEN_BUCKETS = [(1,5), (6,10), (11,15), (16,20), (21,10**9)]

def _bucket_len(n: float) -> int:
    k = int(n)
    for idx,(a,b) in enumerate(_LEN_BUCKETS, start=1):
        if a <= k <= b:
            return idx
    return len(_LEN_BUCKETS)

def _bucket_pos01(x: float, bins: int) -> int:
    x = 0.0 if x is None else float(x)
    if x < 0.0: x = 0.0
    if x > 1.0: x = 1.0
    return min(bins-1, int(x * bins))

def _bucket_tick(t: float, step: int = 10) -> int:
    if t < 0: t = 0
    return int(t) // max(1, step)

def state_hash_v1(feats: Vec) -> str:
    if len(feats) < 7:
        feats = list(feats) + [0.0]*(7-len(feats))
    lo, lp, hx, hy, fx, fy, tk = feats[:7]
    L = _bucket_len(lo)
    P = _bucket_len(lp)
    Hx = _bucket_pos01(hx, 12)
    Hy = _bucket_pos01(hy, 9)
    Fx = _bucket_pos01(fx, 12)
    Fy = _bucket_pos01(fy, 9)
    Tb = _bucket_tick(tk, 10)
    return f"v1|L{L}|P{P}|H{Hx}-{Hy}|F{Fx}-{Fy}|T{Tb}"

# ----------------------------- JSON Parsing ----------------------------------

def _iter_snaps_from_snaps_or_events(chain: Dict[str, Any]) -> Iterable[Tuple[Vec, Optional[int], Optional[float], Optional[str]]]:
    """Liest (features, action, reward, tag) aus patterns[].snaps/events[]."""
    pats = chain.get("patterns") or []
    for p in pats:
        snaps = p.get("snaps") or p.get("events") or []
        for s in snaps:
            feats = s.get("features") or []
            if not isinstance(feats, list) or not feats:
                continue
            meta = s.get("metadata") or {}
            act  = meta.get("action")
            rew  = meta.get("reward")
            tag  = meta.get("tag")
            try:
                a = int(act) if act is not None else None
            except Exception:
                a = None
            r = float(rew) if isinstance(rew, (int, float, str)) else None
            yield ([float(x) for x in feats], a, r, tag)

def _iter_vectors_from_patterns(chain: Dict[str, Any]) -> List[Vec]:
    """Extrahiert reine Vektoren aus patterns[].patterns (ohne Meta)."""
    out: List[Vec] = []
    pats = chain.get("patterns") or []
    for p in pats:
        plist = p.get("patterns") or []
        if isinstance(plist, list):
            for v in plist:
                if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
                    out.append([float(x) for x in v])
    return out

# --------------------------- Bewegungs-/Shaping-Logik -------------------------

def _infer_actions_from_head(seq7: List[Vec]) -> List[int]:
    """
    Rekonstruiert Actions (0:R,1:L,2:D,3:U) aus Δ(hx,hy) zwischen aufeinanderfolgenden
    7D-Vektoren (Indices 2..5). Wraparound: |Δ|>0.5 → invertiere Richtung.
    Rückgabe hat len = len(seq7) (letzte Action als Wiederholung der vorigen).
    """
    if not seq7:
        return []
    acts: List[int] = []
    last_idx = 0
    for i in range(len(seq7)-1):
        _,_,hx,hy,_,_,_ = seq7[i][:7]
        _,_,hx2,hy2,_,_,_ = seq7[i+1][:7]
        dx = float(hx2) - float(hx)
        dy = float(hy2) - float(hy)
        if dx > 0.5:  dx = -(1.0 - dx)
        if dx < -0.5: dx = +(1.0 + dx)
        if dy > 0.5:  dy = -(1.0 - dy)
        if dy < -0.5: dy = +(1.0 + dy)
        if abs(dx) >= abs(dy):
            idx = 0 if dx > 0 else 1
        else:
            idx = 2 if dy > 0 else 3
        acts.append(idx)
        last_idx = idx
    acts.append(last_idx if acts else 0)
    return acts

def _episode_outcome_from_vectors(vecs: List[Vec], explicit_rewards: List[Optional[float]]) -> Tuple[str, float]:
    """
    Bestimmt Outcome:
      1) Bevorzugt letztes nicht-null explicit reward (aus snaps/events).
      2) Fallback: nutze letzte 1D-Vector-Komponente als Terminal-Reward.
      3) Fallback Heuristik: Distanz Kopf→Futter Start vs. Ende (7D).
    """
    for rv in reversed(explicit_rewards):
        if rv is not None and abs(float(rv)) > 1e-9:
            r = float(rv)
            if   r >  1e-9: return ("pos", +1.0)
            elif r < -1e-9: return ("neg", -1.0)
            else:           return ("draw", 0.0)
    for v in reversed(vecs):
        if isinstance(v, list) and len(v) == 1:
            r = float(v[0])
            if   r >  1e-9: return ("pos", +1.0)
            elif r < -1e-9: return ("neg", -1.0)
            else:           return ("draw", 0.0)
    try:
        seq7 = [vv for vv in vecs if isinstance(vv, list) and len(vv) >= 7]
        if seq7:
            hx0, hy0, fx0, fy0 = float(seq7[0][2]), float(seq7[0][3]), float(seq7[0][4]), float(seq7[0][5])
            hxe, hye, fxe, fye = float(seq7[-1][2]), float(seq7[-1][3]), float(seq7[-1][4]), float(seq7[-1][5])
            d0 = abs(fx0 - hx0) + abs(fy0 - hy0)
            de = abs(fxe - hxe) + abs(fye - hye)
            if   de < d0 - 1e-6: return ("pos", +0.2)
            elif de > d0 + 1e-6: return ("neg", -0.2)
            else:                return ("draw", 0.0)
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.snake_trainer.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    return ("draw", 0.0)

def _shaping_from_feats_action(feats: Vec, action: int) -> Tuple[float, List[str]]:
    """
    Schrittweises Reward-Shaping, falls 12D-Adapter-Features vorliegen:
      • gefährliche gewählte Richtung?      → -0.35  (tag: self_or_blocked)
      • „Wunschrichtung“ ist blockiert?    → -0.25  (tag: op_blocked_primary)
      • Bewegung grob in Futterrichtung?   → +0.05  (tag: toward_food)
    Rückgabe: (delta_reward, tags)
    """
    if not SHAPING_ENABLED:
        return (0.0, [])
    tags: List[str] = []
    if not isinstance(feats, list) or len(feats) < 12:
        return (0.0, tags)
    rel_dxN = float(feats[6]); rel_dyN = float(feats[7])
    dR, dL, dD, dU = (float(feats[8]), float(feats[9]), float(feats[10]), float(feats[11]))
    danger_map = {0: dR, 1: dL, 2: dD, 3: dU}
    delta = 0.0
    if danger_map.get(int(action), 0.0) >= 0.5:
        delta -= 0.35
        tags.append("self_or_blocked")
    if abs(rel_dxN) >= abs(rel_dyN):
        primary = 0 if rel_dxN > 0 else 1 if rel_dxN < 0 else None
    else:
        primary = 2 if rel_dyN > 0 else 3 if rel_dyN < 0 else None
    if primary is not None:
        prim_danger = danger_map.get(primary, 0.0)
        if prim_danger >= 0.5 and int(action) == primary:
            delta -= 0.25
            tags.append("op_blocked_primary")
    toward = None
    if abs(rel_dxN) >= abs(rel_dyN):
        toward = 0 if rel_dxN > 0 else 1 if rel_dxN < 0 else None
    else:
        toward = 2 if rel_dyN > 0 else 3 if rel_dyN < 0 else None
    if toward is not None and toward == int(action) and danger_map.get(toward, 0.0) < 0.5:
        delta += 0.05
        tags.append("toward_food")
    if delta > +1.0: delta = +1.0
    if delta < -1.0: delta = -1.0
    return (delta, tags)

# ----------------------------- Training --------------------------------------

def train_from_db(namespace: str = "game:snake",
                  since_sec: Optional[int] = None,
                  limit: Optional[int] = None,
                  verbose: bool = False) -> int:
    sql_manager.ensure_schema()
    now = int(time.time())
    cutoff = None if since_sec is None else (now - int(since_sec))

    q = "SELECT id, blob, ts FROM snapchains WHERE (origin = ? OR origin LIKE ?) "
    args: List[Any] = [namespace, f"{namespace}%"]
    if cutoff is not None:
        q += "AND ts >= ? "; args.append(int(cutoff))
    q += "ORDER BY id DESC "
    if limit is not None:
        q += "LIMIT ?"; args.append(int(limit))

    steps = 0
    with sql_manager.get_conn() as conn:
        rows = conn.execute(q, tuple(args)).fetchall() or []

    for r in rows:
        blob = r.get("blob")
        try:
            txt = blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else str(blob)
            chain = json.loads(txt)
        except Exception:
            continue

        feats_list: List[Vec] = []
        acts_list: List[int] = []
        rew_list_explicit: List[Optional[float]] = []

        # Pfad A: snaps/events vorhanden
        any_snap = False
        for feats, act, rew, _tag in _iter_snaps_from_snaps_or_events(chain):
            any_snap = True
            feats_list.append([float(x) for x in feats])
            acts_list.append(int(act) if act is not None else 0)
            rew_list_explicit.append(rew if rew is not None else None)

        # Pfad B: kompakte patterns (nur Vektoren)
        if not any_snap:
            vecs = _iter_vectors_from_patterns(chain)
            if vecs:
                seq7 = [v for v in vecs if isinstance(v, list) and len(v) >= 7]
                if seq7:
                    feats_list = [v[:7] for v in seq7]
                    acts_list  = _infer_actions_from_head(seq7)
                    rew_list_explicit = [None] * len(feats_list)
                else:
                    continue

        if not feats_list or not acts_list:
            continue

        base_outcome, base_reward = _episode_outcome_from_vectors(feats_list, rew_list_explicit)

        for feats, a in zip(feats_list, acts_list):
            delta_r, tags = _shaping_from_feats_action(feats, a)
            combined_r = base_reward + delta_r
            if combined_r > +1.0: combined_r = +1.0
            if combined_r < -1.0: combined_r = -1.0
            if   combined_r >  1e-9: outcome = "pos"
            elif combined_r < -1e-9: outcome = "neg"
            else:                    outcome = "draw"

            sh = state_hash_v1(feats)
            cent = {"f": feats[:7]}
            if tags: cent["tags"] = tags

            ok = sql_manager.policy_upsert(
                namespace=namespace,
                state_hash=sh,
                action=str(int(a)),
                outcome=outcome,
                reward=combined_r,
                centroid=json.dumps(cent, ensure_ascii=False, separators=(",", ":")),
                ts=None
            )
            if ok:
                steps += 1

    if verbose:
        print(f"[snake_trainer] trainierte Schritte: {steps} (Chains: {len(rows)}, Filter: {namespace})")
    return steps

# -------------------------- Archiv-Export (rules) -----------------------------

def export_archive(namespace: str = "game:snake",
                   min_n: int = 5,
                   min_abs_q: float = 0.05,
                   majority_min_conf: float = 0.25,
                   limit: Optional[int] = None,
                   verbose: bool = False) -> int:
    """
    Exportiert starke Policy-Einträge nach rules (Archiv).
    Kriterium A: |q| >= min_abs_q und n >= min_n
    Kriterium B: max(pos,neg,draw)/n >= majority_min_conf und n >= min_n
    - centroid wird, wenn möglich, zu JSON geparst
    - Duplikate (namespace+state_hash+action) werden nicht erneut exportiert
    - exported=1 setzen (für Archiv-Filter im UI)
    """
    sql_manager.ensure_schema()
    exported = 0
    with sql_manager.get_conn() as conn:
        rows: List[Dict[str, Any]] = []

        qrows = conn.execute(
            """SELECT state_hash, action, n, pos, neg, draw, q, centroid
                 FROM policy_rules
                WHERE namespace=? AND n>=? AND ABS(q)>=?
             ORDER BY ABS(q) DESC, n DESC""",
            (namespace, int(min_n), float(min_abs_q))
        ).fetchall() or []
        rows.extend(qrows)

        if majority_min_conf and majority_min_conf > 0.0:
            mrows = conn.execute(
                """SELECT state_hash, action, n, pos, neg, draw, q, centroid
                     FROM policy_rules
                    WHERE namespace=? AND n>=?
                 ORDER BY n DESC, ABS(q) DESC""",
                (namespace, int(min_n))
            ).fetchall() or []
            seen = {(r["state_hash"], r["action"]) for r in rows}
            for r in mrows:
                key = (r["state_hash"], r["action"])
                if key in seen:
                    continue
                n = max(1, int(r["n"]))
                maj = max(int(r["pos"]), int(r["neg"]), int(r["draw"])) / float(n)
                if maj >= float(majority_min_conf):
                    rows.append(r); seen.add(key)

        if limit is not None:
            rows = rows[:int(limit)]

        now = time.time()
        for r in rows:
            sh = r["state_hash"]; a = str(r["action"]); n = int(r["n"])
            pos = int(r["pos"]); neg = int(r["neg"]); draw = int(r["draw"])
            qv = float(r["q"]); cent_raw = r.get("centroid")

            centroid = None
            if isinstance(cent_raw, str) and cent_raw.strip():
                try: centroid = json.loads(cent_raw)
                except Exception: centroid = cent_raw
            elif isinstance(cent_raw, (dict, list)):
                centroid = cent_raw

            payload = {
                "type": "policy_export",
                "version": 1,
                "namespace": namespace,
                "state_hash": sh,
                "action": a,
                "n": n, "pos": pos, "neg": neg, "draw": draw,
                "q": qv, "centroid": centroid
            }

            exists = conn.execute(
                """SELECT 1
                     FROM rules
                    WHERE json_extract(content,'$.type')='policy_export'
                      AND json_extract(content,'$.namespace')=?
                      AND json_extract(content,'$.state_hash')=?
                      AND json_extract(content,'$.action')=?""",
                (namespace, sh, a)
            ).fetchone()
            if exists:
                continue

            try:
                conn.execute(
                    "INSERT INTO rules (content, weight, active, exported, created_at, updated_at) "
                    "VALUES (?, ?, 1, 1, ?, ?)",
                    (json.dumps(payload, ensure_ascii=False, sort_keys=True),
                     float(abs(qv)), now, now)
                )
                exported += 1
            except Exception:
                continue

        conn.commit()

    if verbose:
        print(f"[snake_trainer] exportierte Archiv-Regeln: {exported}")
    return exported

# ---------------------------------- CLI --------------------------------------

def main():
    ap = argparse.ArgumentParser(description="ORÓMA Snake Trainer (DB → policy_rules)")
    ap.add_argument("--namespace", default="game:snake")
    ap.add_argument("--since", type=int, default=None, help="Sekundenfenster (z. B. 86400 für 24h)")
    ap.add_argument("--limit", type=int, default=None, help="Max. Anzahl Chains")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--export-archive", action="store_true", help="Policy → rules exportieren")
    ap.add_argument("--min-n", type=int, default=1)
    ap.add_argument("--min-abs-q", type=float, default=0.05)
    ap.add_argument("--majority-min-conf", type=float, default=0.20,
                    help="Fallback-Export: Mindestkonfidenz (nbest-nsecond)/ntotal")
    args = ap.parse_args()

    if args.export_archive:
        export_archive(namespace=args.namespace,
                       min_n=args.min_n,
                       min_abs_q=args.min_abs_q,
                       limit=args.limit,
                       verbose=args.verbose,
                       majority_min_conf=args.majority_min_conf)
        return

    train_from_db(namespace=args.namespace, since_sec=args.since,
                  limit=args.limit, verbose=args.verbose)

if __name__ == "__main__":
    main()
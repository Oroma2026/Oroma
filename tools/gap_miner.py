#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:        /opt/ai/oroma/tools/gap_miner.py
# Projekt:     ORÓMA
# Komponente:  Autonomie / Offline-Learning
# Modul:       Gap-Miner (proaktives Mining von knowledge_gaps aus policy_rules + rules)
# Version:     v1.1
# Stand:       2026-04-27
# Autor:       Jörg Werner (public) / ORÓMA Project (internal)
# Lizenz:      MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool erzeugt "proaktive" Knowledge-Gaps, ohne auf Fehler/Crashes zu warten.
# Es scannt:
#   1) policy_rules  (State→Action-Erfahrung; n/q/last_ts)
#   2) optional rules (Archiv, z.B. exportierte Policy-Regeln)
# und schreibt strukturierte Lücken in knowledge_gaps.
#
# WARUM
# -----
# policy_rules sammelt Erfahrung – aber das System "fühlt" keine Unsicherheit,
# wenn niemand diese Stellen markiert. knowledge_gaps ist der "Hunger/Jagdinstinkt",
# der den nächsten Lern-/Planungsschritt motiviert (Roter Faden / Simulation / Replay).
#
# DESIGN-ZIELE
# ------------
# 1) Headless / Produktions-Ready
#    - nur Python stdlib
#    - keine UI-Abhängigkeiten
#
# 2) Nicht-destruktiv
#    - schreibt nur neue Zeilen in knowledge_gaps
#    - löscht/ändert nichts in policy_rules/rules
#
# 3) Lock-robust (SQLite + Orchestrator)
#    - nutzt core.sql_manager (busy_timeout + _run_with_lock_retry)
#    - respektiert ENV: OROMA_DB_BUSY_TIMEOUT_MS, OROMA_DB_LOCK_RETRY_SEC
#
# GAP-TYPEN
# ---------
# A) low_evidence
#    - n < LOW_EVIDENCE_N (Default: 5)
#    - "Ich entscheide zwar, aber ich habe kaum Daten zu diesem Zustand"
#
# B) high_uncertainty
#    - |q1 - q2| < UNCERTAINTY_EPS (Default: 0.05)
#    - "Top-2 Aktionen sind praktisch gleich gut → Dilemma"
#
# C) logic_conflict (optional)
#    - Policy "jetzt" (policy_rules) widerspricht Archiv/Regelbasis (rules)
#    - Sinn: Drift / inkonsistente Empfehlung → gezielt nachlernen / erklären
#    - Aktivierung: OROMA_GAP_MINER_ENABLE_LOGIC_CONFLICT=1
#
# DEDUPE / COOLDOWN
# -----------------
# Um Spam zu vermeiden, prüft der Miner vor dem Insert, ob es in den letzten
# COOLDOWN_S bereits ein Gap derselben Art für (namespace,state_hash) gab.
# Dedupe erfolgt über meta-JSON in knowledge_gaps via LIKE-Pattern.
#
# ENV
# ---
# Basis:
#   OROMA_BASE=/opt/ai/oroma
#
# Scanner:
#   OROMA_GAP_MINER_NAMESPACE=game:%            (Default, LIKE-Pattern)
#   OROMA_GAP_MINER_LIMIT_PER_KIND=200          (Default)
#   OROMA_GAP_MINER_COOLDOWN_S=21600            (6h Default)
#
# Schwellwerte:
#   OROMA_UP_GAPS_LOW_EVIDENCE_N=5
#   OROMA_UP_GAPS_UNCERTAINTY_EPS=0.05
#
# Logic-Conflict (Archiv vs. Policy):
#   OROMA_GAP_MINER_ENABLE_LOGIC_CONFLICT=1     (Default: 1)
#   OROMA_GAP_MINER_CONFLICT_LIMIT=150          (pro Namespace; Default: 150)
#   OROMA_GAP_MINER_CONFLICT_MIN_N=5            (policy n >= 5)
#   OROMA_GAP_MINER_CONFLICT_MIN_ABS_Q=0.25     (|q_policy| >= 0.25)
#   OROMA_GAP_MINER_CONFLICT_MIN_ARCH_W=0.55    (rule.weight >= 0.55)
#
# DB:
#   OROMA_DB_BUSY_TIMEOUT_MS=60000
#   OROMA_DB_LOCK_RETRY_SEC=60
#
# CLI
# ---
#   # einmalig scannen & schreiben
#   PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/tools/gap_miner.py --once
#
#   # mehrere Namespaces
#   python3 tools/gap_miner.py --once --namespace 'game:tictactoe' --namespace 'game:snake'
#
#   # dry-run (nur Report)
#   python3 tools/gap_miner.py --once --dry-run
#
# OUTPUT
# ------
# Gibt am Ende ein JSON-Summary aus (counts, inserts, timing), damit
# Orchestrator/systemd Logs maschinenlesbar bleiben.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Tuple, Optional
import logging
from pathlib import Path
from core.log_guard import log_suppressed


# ---- Bootstrap (PYTHONPATH) ----
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE and BASE not in sys.path:
    sys.path.insert(0, BASE)


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    v = str(os.environ.get(name, "")).strip().lower()
    if v == "":
        return bool(default)
    return v in ("1", "true", "yes", "y", "on")


def _now_ts() -> int:
    return int(time.time())


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _escape_like(s: str) -> str:
    # ESCAPE \'\\\' in SQL
    return str(s).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _state_path() -> str:
    return str(os.environ.get("OROMA_GAP_MINER_STATE_PATH", os.path.join(BASE or "/opt/ai/oroma", "data", "state", "gap_miner_state.json")) or os.path.join(BASE or "/opt/ai/oroma", "data", "state", "gap_miner_state.json"))


def _load_state(path: str) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(path: str, data: Dict[str, Any]) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        log_suppressed('tools/gap_miner.py:state_save', exc=e, level=logging.WARNING)


def _normalize_rotation_state(state: Dict[str, Any], namespaces: List[str], kinds: List[str]) -> Dict[str, Any]:
    out = dict(state or {})
    ns_list = [str(x) for x in namespaces if str(x)] or ["game:%"]
    kind_list = [str(x) for x in kinds if str(x)] or ["low_evidence"]
    if out.get("namespaces") != ns_list:
        out["namespaces"] = ns_list
        out["ns_idx"] = 0
    if out.get("kinds") != kind_list:
        out["kinds"] = kind_list
        out["kind_idx"] = 0
    out["ns_idx"] = int(out.get("ns_idx") or 0) % len(ns_list)
    out["kind_idx"] = int(out.get("kind_idx") or 0) % len(kind_list)
    return out


def _pick_rotation_slice(state: Dict[str, Any], namespaces: List[str], kinds: List[str]) -> Tuple[str, str, Dict[str, Any]]:
    cur = _normalize_rotation_state(state, namespaces, kinds)
    ns_list = list(cur.get("namespaces") or namespaces or ["game:%"])
    kind_list = list(cur.get("kinds") or kinds or ["low_evidence"])
    ns_idx = int(cur.get("ns_idx") or 0) % len(ns_list)
    kind_idx = int(cur.get("kind_idx") or 0) % len(kind_list)
    selected_ns = str(ns_list[ns_idx])
    selected_kind = str(kind_list[kind_idx])
    next_kind_idx = (kind_idx + 1) % len(kind_list)
    next_ns_idx = ns_idx
    if next_kind_idx == 0:
        next_ns_idx = (ns_idx + 1) % len(ns_list)
    cur["ns_idx"] = next_ns_idx
    cur["kind_idx"] = next_kind_idx
    cur["last_selected_namespace"] = selected_ns
    cur["last_selected_kind"] = selected_kind
    cur["last_selected_ts"] = _now_ts()
    return selected_ns, selected_kind, cur


# ---- Imports (defensiv) ----
try:
    from core import sql_manager
    from core import gaps
except Exception as e:
    sys.stderr.write(f"[gap_miner] import failed: {e!r}\n")
    raise


def _parse_namespaces(args_list: List[str]) -> List[str]:
    out: List[str] = []
    for item in args_list:
        if not item:
            continue
        parts = [p.strip() for p in str(item).split(",") if p.strip()]
        out.extend(parts)

    # fallback aus ENV
    if not out:
        out = [str(os.environ.get("OROMA_GAP_MINER_NAMESPACE", "game:%") or "game:%").strip()]

    # dedupe stable
    seen = set()
    uniq: List[str] = []
    for ns in out:
        if ns not in seen:
            uniq.append(ns)
            seen.add(ns)
    return uniq


def _exists_recent_gap(conn, kind: str, namespace: str, state_hash: str, since_ts: int) -> bool:
    # Dedupe über meta JSON (funktioniert für UniversalPolicy + Gap-Miner)
    ns_pat = f'%"namespace":"{_escape_like(namespace)}"%'
    sh_pat = f'%"state_hash":"{_escape_like(state_hash)}"%'
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM knowledge_gaps
        WHERE kind=?
          AND ts>=?
          AND COALESCE(meta,'') LIKE ? ESCAPE '\\'
          AND COALESCE(meta,'') LIKE ? ESCAPE '\\'
        LIMIT 1
        """,
        (str(kind), int(since_ts), ns_pat, sh_pat),
    )
    return cur.fetchone() is not None


def _like_match(text: str, pattern: str) -> bool:
    """Kleiner SQLite-LIKE-Matcher fuer In-Memory-Filterung der Namespace-Liste."""
    import re
    rx = "^" + re.escape(str(pattern)).replace("%", ".*").replace("_", ".") + "$"
    try:
        return re.match(rx, str(text)) is not None
    except Exception:
        return False


def _load_recent_gap_keys(conn, namespaces: List[str], since_ts: int) -> set[Tuple[str, str, str]]:
    """Laedt juengere Gap-Schluessel einmalig, damit Dedupe spaeter O(1) ist."""
    out: set[Tuple[str, str, str]] = set()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT kind, COALESCE(meta,'') AS meta
        FROM knowledge_gaps
        WHERE ts >= ?
          AND kind IN ('low_evidence','high_uncertainty','logic_conflict')
        ORDER BY ts DESC
        """,
        (int(since_ts),),
    )
    rows = cur.fetchall() or []
    for r in rows:
        kind = str((r["kind"] if hasattr(r, "keys") else r[0]) or "")
        meta_txt = str((r["meta"] if hasattr(r, "keys") else r[1]) or "")
        if not kind or not meta_txt:
            continue
        try:
            meta = json.loads(meta_txt)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        ns = str(meta.get("namespace") or "")
        sh = str(meta.get("state_hash") or "")
        if not ns or not sh:
            continue
        if namespaces and not any(_like_match(ns, pat) for pat in namespaces):
            continue
        out.add((kind, ns, sh))
    return out


def _fetch_best_archived_rules_map(conn, namespace: str, state_hashes: List[str]) -> Dict[str, Dict[str, Any]]:
    """Laedt Archiv-Policy-Regeln fuer einen Namespace in einem Schwung statt N+1-Abfragen."""
    out: Dict[str, Dict[str, Any]] = {}
    wanted = {str(x) for x in (state_hashes or []) if str(x)}
    if not namespace or not wanted:
        return out
    key_pat = f'%"key": "policy::{_escape_like(namespace)}::%'
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, content, weight
        FROM rules
        WHERE active=1
          AND COALESCE(content,'') LIKE ? ESCAPE '\\'
        ORDER BY weight DESC, updated_at DESC
        """,
        (key_pat,),
    )
    rows = cur.fetchall() or []
    for r in rows:
        content = r["content"] if hasattr(r, "keys") else r[1]
        weight = float((r["weight"] if hasattr(r, "keys") else r[2]) or 0.0)
        rid = int((r["id"] if hasattr(r, "keys") else r[0]) or 0)
        try:
            d = json.loads(content)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if str(d.get("type") or "") != "policy":
            continue
        if str(d.get("namespace") or "") != str(namespace):
            continue
        sh = str(d.get("state_hash") or "")
        if sh not in wanted:
            continue
        cand = {
            "id": rid,
            "action": str(d.get("action") or ""),
            "q": float(d.get("q") or 0.0),
            "n": int(d.get("n") or 0),
            "weight": float(weight),
        }
        best = out.get(sh)
        if best is None or (cand["weight"], abs(cand["q"]), cand["n"]) > (best["weight"], abs(best["q"]), best["n"]):
            out[sh] = cand
    return out


def _fetch_low_evidence(conn, ns_like: str, thr_n: int, row_limit: int) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT namespace, state_hash, action, n, q, COALESCE(last_ts,0) AS last_ts
        FROM policy_rules
        WHERE namespace LIKE ?
          AND n < ?
        ORDER BY n ASC, ABS(q) ASC, last_ts DESC
        LIMIT ?
        """,
        (str(ns_like), int(thr_n), int(max(0, row_limit))),
    )
    rows = cur.fetchall() or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append(dict(r))
        else:
            out.append(
                {
                    "namespace": r[0],
                    "state_hash": r[1],
                    "action": r[2],
                    "n": r[3],
                    "q": r[4],
                    "last_ts": r[5] if len(r) > 5 else 0,
                }
            )
    return out



def _chunked(iterable, n: int):
    """Yield lists of up to n items."""
    buf = []
    for x in iterable:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _fetch_uncertainty_candidates(conn, ns_like: str, min_n: int, limit_states: int) -> List[Tuple[str, str]]:
    """Pick candidate (namespace, state_hash) pairs for uncertainty mining.

    This is intentionally index-friendly and avoids full-table window scans.
    We prefer recently updated states (last_ts desc) and require `n >= min_n`.
    """
    cand_mult = _env_int("OROMA_GAP_MINER_HU_CAND_MULT", 80)
    cand_cap = _env_int("OROMA_GAP_MINER_HU_CAND_CAP", 20000)
    want = int(max(100, min(int(cand_cap), int(limit_states) * int(cand_mult))))
    cur = conn.cursor()
    # NOTE: DISTINCT across (namespace,state_hash) and order by max(last_ts) requires grouping.
    cur.execute(
        """
        SELECT namespace, state_hash
        FROM policy_rules
        WHERE namespace LIKE ?
          AND n >= ?
        GROUP BY namespace, state_hash
        ORDER BY MAX(last_ts) DESC
        LIMIT ?
        """,
        (str(ns_like), int(min_n), int(want)),
    )
    rows = cur.fetchall() or []
    out = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append((str(r["namespace"]), str(r["state_hash"])))
        else:
            out.append((str(r[0]), str(r[1])))
    return out


def _fetch_policy_rows_for_states(conn, states: List[Tuple[str, str]], min_n: int) -> List[Dict[str, Any]]:
    """Fetch policy_rules rows for a set of (namespace,state_hash) pairs.

    Uses chunking to avoid SQLite param limits.
    """
    if not states:
        return []
    cur = conn.cursor()
    out: List[Dict[str, Any]] = []
    # Build per-namespace chunks to keep query simple
    by_ns: Dict[str, List[str]] = {}
    for ns, sh in states:
        if ns and sh:
            by_ns.setdefault(ns, []).append(sh)

    for ns, sh_list in by_ns.items():
        for chunk in _chunked(sh_list, 900):
            ph = ",".join("?" for _ in chunk)
            sql = f"""
            SELECT namespace, state_hash, action, q, n, last_ts
            FROM policy_rules
            WHERE namespace = ?
              AND state_hash IN ({ph})
              AND n >= ?
            """
            cur.execute(sql, tuple([ns] + chunk + [int(min_n)]))
            rows = cur.fetchall() or []
            for r in rows:
                if hasattr(r, "keys"):
                    out.append(dict(r))
                else:
                    out.append(
                        {
                            "namespace": r[0],
                            "state_hash": r[1],
                            "action": r[2],
                            "q": r[3],
                            "n": r[4],
                            "last_ts": r[5],
                        }
                    )
    return out


def _fetch_high_uncertainty(conn, ns_like: str, eps: float, min_n: int, limit_states: int) -> List[Dict[str, Any]]:
    """Fetch high-uncertainty states (Top-2 actions nearly equal).

    IMPORTANT PERFORMANCE NOTE
    --------------------------
    The previous implementation used window functions (ROW_NUMBER / PARTITION BY) across
    the entire `policy_rules` table. On large DBs this can cause full scans + temp sorts
    and routinely exceed orchestrator timeouts.

    This version is index-friendly:
      1) select a bounded set of recent candidate states (GROUP BY + MAX(last_ts) DESC)
      2) fetch all rows for those states (chunked IN)
      3) compute Top-2 and q-gap in Python

    Result shape is compatible with the old caller.
    """
    if limit_states <= 0:
        return []

    eps = float(max(1e-9, float(eps)))
    min_n = int(max(0, int(min_n)))
    limit_states = int(max(0, int(limit_states)))

    # Step 1: candidates
    candidates = _fetch_uncertainty_candidates(conn, ns_like=ns_like, min_n=min_n, limit_states=limit_states)
    if not candidates:
        return []

    # Step 2: fetch rows
    rows = _fetch_policy_rows_for_states(conn, candidates, min_n=min_n)
    if not rows:
        return []

    # Step 3: aggregate top-2 per (ns, state_hash)
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        ns = str(r.get("namespace") or "")
        sh = str(r.get("state_hash") or "")
        if not ns or not sh:
            continue
        by_key.setdefault((ns, sh), []).append(r)

    out: List[Dict[str, Any]] = []
    for (ns, sh), lst in by_key.items():
        # Sort by q desc, n desc (same as old)
        lst_sorted = sorted(lst, key=lambda x: (float(x.get("q") or 0.0), int(x.get("n") or 0)), reverse=True)
        if len(lst_sorted) < 2:
            continue
        r1, r2 = lst_sorted[0], lst_sorted[1]
        q1 = float(r1.get("q") or 0.0)
        q2 = float(r2.get("q") or 0.0)
        qgap = abs(q1 - q2)
        if qgap >= eps:
            continue
        n1 = int(r1.get("n") or 0)
        n2 = int(r2.get("n") or 0)
        if min(n1, n2) < min_n:
            continue
        out.append(
            {
                "namespace": ns,
                "state_hash": sh,
                "a1": str(r1.get("action") or ""),
                "q1": q1,
                "n1": n1,
                "a2": str(r2.get("action") or ""),
                "q2": q2,
                "n2": n2,
                "qgap": qgap,
            }
        )

    # Final ordering matches old intent: smallest qgap first, then low evidence tie-break
    out_sorted = sorted(out, key=lambda x: (float(x.get("qgap") or 0.0), int(x.get("n1") or 0) + int(x.get("n2") or 0)))
    return out_sorted[:limit_states]

def _group_low_evidence(rows: List[Dict[str, Any]], max_actions_per_state: int = 3) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        ns = str(r.get("namespace") or "")
        sh = str(r.get("state_hash") or "")
        grouped.setdefault((ns, sh), []).append(r)

    out: List[Dict[str, Any]] = []
    for (ns, sh), lst in grouped.items():
        lst_sorted = sorted(lst, key=lambda x: (int(x.get("n") or 0), abs(float(x.get("q") or 0.0))))
        lst_sorted = lst_sorted[: max(1, int(max_actions_per_state))]
        out.append({"namespace": ns, "state_hash": sh, "actions": lst_sorted})
    return out


def _fetch_best_policy_states(conn, ns_like: str, limit_states: int) -> List[Dict[str, Any]]:
    """
    Liefert pro State den besten Policy-Kandidaten (q desc, n desc).
    """
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
            SELECT
                namespace,
                state_hash,
                action AS a_pol,
                q      AS q_pol,
                n      AS n_pol,
                ROW_NUMBER() OVER(
                    PARTITION BY namespace, state_hash
                    ORDER BY q DESC, n DESC
                ) AS rn
            FROM policy_rules
            WHERE namespace LIKE ?
        )
        SELECT namespace, state_hash, a_pol, q_pol, n_pol
        FROM ranked
        WHERE rn=1
        ORDER BY n_pol DESC, ABS(q_pol) DESC
        LIMIT ?
        """,
        (str(ns_like), int(max(0, limit_states))),
    )
    rows = cur.fetchall() or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append(dict(r))
        else:
            out.append({"namespace": r[0], "state_hash": r[1], "a_pol": r[2], "q_pol": r[3], "n_pol": r[4]})
    return out


def mine_once(
    namespaces: List[str],
    limit_per_kind: int,
    cooldown_s: int,
    low_evidence_n: int,
    uncertainty_eps: float,
    dry_run: bool,
    min_n_uncertainty: int,
    enable_logic_conflict: bool,
    conflict_limit: int,
    conflict_min_n: int,
    conflict_min_abs_q: float,
    conflict_min_arch_w: float,
    mode: str,
    state_path: str,
    max_runtime_s: int,
) -> Dict[str, Any]:
    t0 = time.time()
    all_namespaces = list(namespaces or [])
    enabled_kinds = ["low_evidence", "high_uncertainty"] + (["logic_conflict"] if enable_logic_conflict else [])
    rotation_state_before: Dict[str, Any] = {}
    rotation_state_after: Dict[str, Any] = {}
    selected_namespace: Optional[str] = None
    selected_kind: Optional[str] = None
    mode = str(mode or "rotate").strip().lower()
    if mode == "rotate":
        rotation_state_before = _load_state(state_path)
        selected_namespace, selected_kind, rotation_state_after = _pick_rotation_slice(rotation_state_before, all_namespaces, enabled_kinds)
        namespaces = [selected_namespace]

    gaps.ensure_schema()

    inserted: List[int] = []
    skipped: int = 0
    scanned: Dict[str, int] = {
        "low_evidence_rows": 0,
        "high_uncertainty_states": 0,
        "logic_conflict_states": 0,
        "logic_conflict_with_arch": 0,
    }

    conn = sql_manager.get_conn()
    try:
        now = _now_ts()
        since_ts = int(now - max(0, int(cooldown_s)))
        recent_gap_keys = _load_recent_gap_keys(conn, namespaces, since_ts)

        for ns_like in namespaces:
            if max_runtime_s > 0 and (time.time() - t0) >= float(max_runtime_s):
                break

            # --- A) low_evidence ---
            le_rows: List[Dict[str, Any]] = []
            if selected_kind in (None, "low_evidence"):
                le_rows = _fetch_low_evidence(
                    conn,
                    ns_like=ns_like,
                    thr_n=int(low_evidence_n),
                    row_limit=int(limit_per_kind) * 6,
                )
            scanned["low_evidence_rows"] += int(len(le_rows))
            le_grouped = _group_low_evidence(le_rows, max_actions_per_state=3)

            for item in le_grouped[: int(limit_per_kind)]:
                ns = str(item.get("namespace") or "")
                sh = str(item.get("state_hash") or "")
                if not ns or not sh:
                    continue

                if ('low_evidence', ns, sh) in recent_gap_keys:
                    skipped += 1
                    continue

                acts = item.get("actions") or []
                min_n = None
                parts: List[str] = []
                for a in acts:
                    an = int(a.get("n") or 0)
                    aq = float(a.get("q") or 0.0)
                    aa = str(a.get("action") or "")
                    parts.append(f"{aa}:n={an},q={aq:.3f}")
                    min_n = an if min_n is None else min(min_n, an)
                min_n = int(min_n or 0)

                conf = 1.0 - (float(min_n) / float(max(1, int(low_evidence_n))))
                conf = max(0.0, min(1.0, conf))
                desc = f"low_evidence(mined): ns={ns} state={sh} | " + "; ".join(parts)

                meta = {
                    "source": "gap_miner",
                    "namespace": ns,
                    "state_hash": sh,
                    "kind": "low_evidence",
                    "low_evidence_n": int(low_evidence_n),
                    "cooldown_s": int(cooldown_s),
                    "actions": [
                        {
                            "action": str(a.get("action") or ""),
                            "n": int(a.get("n") or 0),
                            "q": float(a.get("q") or 0.0),
                            "last_ts": int(a.get("last_ts") or 0),
                        }
                        for a in acts
                    ],
                }

                if not dry_run:
                    gid = gaps.add_gap("low_evidence", desc, conf, meta)
                    inserted.append(int(gid))
                    recent_gap_keys.add(('low_evidence', ns, sh))

            if max_runtime_s > 0 and (time.time() - t0) >= float(max_runtime_s):
                break

            # --- B) high_uncertainty ---
            hu_rows: List[Dict[str, Any]] = []
            if selected_kind in (None, "high_uncertainty"):
                hu_rows = _fetch_high_uncertainty(
                    conn,
                    ns_like=ns_like,
                    eps=float(uncertainty_eps),
                    min_n=int(min_n_uncertainty),
                    limit_states=int(limit_per_kind),
                )
            scanned["high_uncertainty_states"] += int(len(hu_rows))

            for r in hu_rows:
                ns = str(r.get("namespace") or "")
                sh = str(r.get("state_hash") or "")
                if not ns or not sh:
                    continue

                if ('high_uncertainty', ns, sh) in recent_gap_keys:
                    skipped += 1
                    continue

                a1 = str(r.get("a1") or "")
                q1 = float(r.get("q1") or 0.0)
                n1 = int(r.get("n1") or 0)
                a2 = str(r.get("a2") or "")
                q2 = float(r.get("q2") or 0.0)
                n2 = int(r.get("n2") or 0)
                qgap = abs(q1 - q2)

                eps = float(max(1e-9, float(uncertainty_eps)))
                conf = 1.0 - (float(qgap) / eps)
                conf = max(0.0, min(1.0, conf))
                desc = (
                    f"high_uncertainty(mined): ns={ns} state={sh} | "
                    f"q-gap={qgap:.3f} (a1={a1} q1={q1:.3f} n1={n1} | a2={a2} q2={q2:.3f} n2={n2})"
                )

                meta = {
                    "source": "gap_miner",
                    "namespace": ns,
                    "state_hash": sh,
                    "kind": "high_uncertainty",
                    "uncertainty_eps": float(uncertainty_eps),
                    "cooldown_s": int(cooldown_s),
                    "a1": a1,
                    "q1": float(q1),
                    "n1": int(n1),
                    "a2": a2,
                    "q2": float(q2),
                    "n2": int(n2),
                    "qgap": float(qgap),
                }

                if not dry_run:
                    gid = gaps.add_gap("high_uncertainty", desc, conf, meta)
                    inserted.append(int(gid))
                    recent_gap_keys.add(('high_uncertainty', ns, sh))

            if max_runtime_s > 0 and (time.time() - t0) >= float(max_runtime_s):
                break

            # --- C) logic_conflict (Archiv vs. aktuelle Policy) ---
            if enable_logic_conflict and selected_kind in (None, "logic_conflict"):
                best_states = _fetch_best_policy_states(conn, ns_like=ns_like, limit_states=int(conflict_limit))
                scanned["logic_conflict_states"] += int(len(best_states))
                arch_maps_by_ns: Dict[str, Dict[str, Dict[str, Any]]] = {}
                states_by_ns: Dict[str, List[str]] = {}
                for st in best_states:
                    ns0 = str(st.get("namespace") or "")
                    sh0 = str(st.get("state_hash") or "")
                    if ns0 and sh0:
                        states_by_ns.setdefault(ns0, []).append(sh0)
                for ns0, sh_list in states_by_ns.items():
                    arch_maps_by_ns[ns0] = _fetch_best_archived_rules_map(conn, ns0, sh_list)

                for st in best_states:
                    ns = str(st.get("namespace") or "")
                    sh = str(st.get("state_hash") or "")
                    a_pol = str(st.get("a_pol") or "")
                    q_pol = float(st.get("q_pol") or 0.0)
                    n_pol = int(st.get("n_pol") or 0)

                    if not ns or not sh or not a_pol:
                        continue
                    if n_pol < int(conflict_min_n):
                        continue
                    if abs(q_pol) < float(conflict_min_abs_q):
                        continue

                    if ('logic_conflict', ns, sh) in recent_gap_keys:
                        skipped += 1
                        continue

                    arch_map = arch_maps_by_ns.get(ns) or {}
                    arch = arch_map.get(sh) if arch_map else None
                    if not arch:
                        continue
                    scanned["logic_conflict_with_arch"] += 1

                    a_arch = str(arch.get("action") or "")
                    w_arch = float(arch.get("weight") or 0.0)
                    q_arch = float(arch.get("q") or 0.0)
                    n_arch = int(arch.get("n") or 0)

                    if not a_arch:
                        continue
                    if w_arch < float(conflict_min_arch_w):
                        continue

                    if a_arch == a_pol:
                        continue

                    # Confidence: je stärker beide, desto höher – inkl. Abweichung
                    # (Konservativ, 0..1)
                    strength = 0.5 * (min(1.0, abs(q_pol)) + min(1.0, abs(q_arch)))
                    dev = min(1.0, abs(q_pol - q_arch) / 2.0)  # q∈[-1..1]
                    conf = max(0.0, min(1.0, 0.55 * strength + 0.45 * dev))

                    desc = (
                        f"logic_conflict(mined): ns={ns} state={sh} | "
                        f"policy: a={a_pol} q={q_pol:.3f} n={n_pol}  "
                        f"vs archive(rule): a={a_arch} q={q_arch:.3f} n={n_arch} w={w_arch:.3f}"
                    )

                    meta = {
                        "source": "gap_miner",
                        "namespace": ns,
                        "state_hash": sh,
                        "kind": "logic_conflict",
                        "cooldown_s": int(cooldown_s),
                        "policy": {"action": a_pol, "q": float(q_pol), "n": int(n_pol)},
                        "archive_rule": {
                            "rule_id": int(arch.get("id") or 0),
                            "action": a_arch,
                            "q": float(q_arch),
                            "n": int(n_arch),
                            "weight": float(w_arch),
                        },
                        "thresholds": {
                            "min_n": int(conflict_min_n),
                            "min_abs_q": float(conflict_min_abs_q),
                            "min_arch_w": float(conflict_min_arch_w),
                        },
                    }

                    if not dry_run:
                        gid = gaps.add_gap("logic_conflict", desc, conf, meta)
                        inserted.append(int(gid))
                        recent_gap_keys.add(('logic_conflict', ns, sh))

    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed('tools/gap_miner.py:634', exc=e, level=logging.WARNING)
            pass

    if mode == "rotate":
        rotation_state_after["last_completed_ts"] = _now_ts()
        _save_state(state_path, rotation_state_after)

    return {
        "ok": True,
        "ts_run": _now_ts(),
        "namespaces": namespaces,
        "all_namespaces": all_namespaces,
        "mode": str(mode),
        "state_path": str(state_path),
        "selected_namespace": selected_namespace,
        "selected_kind": selected_kind,
        "dry_run": bool(dry_run),
        "cooldown_s": int(cooldown_s),
        "max_runtime_s": int(max_runtime_s),
        "limit_per_kind": int(limit_per_kind),
        "thresholds": {
            "low_evidence_n": int(low_evidence_n),
            "uncertainty_eps": float(uncertainty_eps),
            "min_n_uncertainty": int(min_n_uncertainty),
            "logic_conflict": {
                "enabled": bool(enable_logic_conflict),
                "conflict_limit": int(conflict_limit),
                "min_n": int(conflict_min_n),
                "min_abs_q": float(conflict_min_abs_q),
                "min_arch_w": float(conflict_min_arch_w),
            },
        },
        "scanned": scanned,
        "skipped": int(skipped),
        "inserted": {"count": int(len(inserted)), "ids": inserted[-50:]},
        "duration_s": round(time.time() - t0, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Gap-Miner (proaktives Mining aus policy_rules + rules)")
    ap.add_argument("--once", action="store_true", help="Run one scan and exit")
    ap.add_argument(
        "--namespace",
        action="append",
        default=[],
        help="SQLite LIKE-Pattern für policy_rules.namespace (mehrfach oder Komma-liste). Default: ENV OROMA_GAP_MINER_NAMESPACE oder 'game:%'",
    )
    ap.add_argument("--dry-run", action="store_true", help="Nur Report, keine Inserts")
    ap.add_argument(
        "--limit-per-kind",
        type=int,
        default=_env_int("OROMA_GAP_MINER_LIMIT_PER_KIND", 200),
        help="Max Inserts pro Gap-Art (Default: ENV OROMA_GAP_MINER_LIMIT_PER_KIND=200)",
    )
    ap.add_argument(
        "--cooldown-s",
        type=int,
        default=_env_int("OROMA_GAP_MINER_COOLDOWN_S", 21600),
        help="Cooldown pro (kind,namespace,state_hash) (Default: 21600=6h)",
    )
    ap.add_argument(
        "--low-evidence-n",
        type=int,
        default=_env_int("OROMA_UP_GAPS_LOW_EVIDENCE_N", 5),
        help="Schwellwert n < N für low_evidence (Default: ENV OROMA_UP_GAPS_LOW_EVIDENCE_N=5)",
    )
    ap.add_argument(
        "--uncertainty-eps",
        type=float,
        default=_env_float("OROMA_UP_GAPS_UNCERTAINTY_EPS", 0.05),
        help="Schwellwert |q1-q2| < eps für high_uncertainty (Default: ENV OROMA_UP_GAPS_UNCERTAINTY_EPS=0.05)",
    )
    ap.add_argument(
        "--min-n-uncertainty",
        type=int,
        default=1,
        help="Minimum n für beide Top-2 Aktionen, damit high_uncertainty zählt (Default: 1)",
    )

    ap.add_argument(
        "--enable-logic-conflict",
        action="store_true",
        help="Aktiviere logic_conflict Mining (Archiv rules vs. aktuelle Policy). Alternativ ENV OROMA_GAP_MINER_ENABLE_LOGIC_CONFLICT.",
    )
    ap.add_argument(
        "--conflict-limit",
        type=int,
        default=_env_int("OROMA_GAP_MINER_CONFLICT_LIMIT", 150),
        help="Wie viele States pro Namespace für logic_conflict geprüft werden (Default: ENV OROMA_GAP_MINER_CONFLICT_LIMIT=150)",
    )
    ap.add_argument(
        "--conflict-min-n",
        type=int,
        default=_env_int("OROMA_GAP_MINER_CONFLICT_MIN_N", 5),
        help="policy n >= N für logic_conflict (Default: ENV OROMA_GAP_MINER_CONFLICT_MIN_N=5)",
    )
    ap.add_argument(
        "--conflict-min-abs-q",
        type=float,
        default=_env_float("OROMA_GAP_MINER_CONFLICT_MIN_ABS_Q", 0.25),
        help="|q_policy| >= X für logic_conflict (Default: ENV OROMA_GAP_MINER_CONFLICT_MIN_ABS_Q=0.25)",
    )
    ap.add_argument(
        "--conflict-min-arch-w",
        type=float,
        default=_env_float("OROMA_GAP_MINER_CONFLICT_MIN_ARCH_W", 0.55),
        help="rule.weight >= W für logic_conflict (Default: ENV OROMA_GAP_MINER_CONFLICT_MIN_ARCH_W=0.55)",
    )
    ap.add_argument(
        "--mode",
        choices=("rotate", "full"),
        default=str(os.environ.get("OROMA_GAP_MINER_MODE", "rotate") or "rotate"),
        help="rotate=pro Lauf genau ein Namespace + ein Gap-Typ; full=klassischer Vollscan (Default: ENV OROMA_GAP_MINER_MODE=rotate)",
    )
    ap.add_argument(
        "--max-runtime-s",
        type=int,
        default=_env_int("OROMA_GAP_MINER_MAX_RUNTIME_S", 75),
        help="weiches Laufzeitbudget pro Durchlauf (Default: ENV OROMA_GAP_MINER_MAX_RUNTIME_S=75)",
    )
    ap.add_argument(
        "--state-path",
        default=_state_path(),
        help="Pfad fuer den Rotationszustand (Default: ENV OROMA_GAP_MINER_STATE_PATH oder /opt/ai/oroma/data/state/gap_miner_state.json)",
    )

    args = ap.parse_args()

    namespaces = _parse_namespaces(list(args.namespace or []))

    enable_logic_conflict = bool(args.enable_logic_conflict) or _env_bool("OROMA_GAP_MINER_ENABLE_LOGIC_CONFLICT", True)

    summary = mine_once(
        namespaces=namespaces,
        limit_per_kind=int(args.limit_per_kind),
        cooldown_s=int(args.cooldown_s),
        low_evidence_n=int(args.low_evidence_n),
        uncertainty_eps=float(args.uncertainty_eps),
        dry_run=bool(args.dry_run),
        min_n_uncertainty=int(args.min_n_uncertainty),
        enable_logic_conflict=bool(enable_logic_conflict),
        conflict_limit=int(args.conflict_limit),
        conflict_min_n=int(args.conflict_min_n),
        conflict_min_abs_q=float(args.conflict_min_abs_q),
        conflict_min_arch_w=float(args.conflict_min_arch_w),
        mode=str(args.mode or "rotate"),
        state_path=str(args.state_path or _state_path()),
        max_runtime_s=int(args.max_runtime_s),
    )

    sys.stdout.write(_json(summary) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_learning_bridge.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap-Learning-Bridge · Dry-Run Learning Focus aus knowledge_gaps
# Version:   v0.3.0-evidence-aware-reference-selector
# Stand:     2026-07-12
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool schließt NICHT direkt den Gap→Policy-Lernkreis. Es baut bewusst
# nur die erste sichere Brücke:
#
#     knowledge_gaps
#         -> read-only Analyse gegen policy_rules
#         -> data/state/gap_learning_focus.json
#         -> späterer Verbraucher: Explore / Replay / Dream / Runner-Priorität
#
# Der Kernbefund aus dem Lernloop-Audit war: Gaps sind Lernbedarf, aber kein
# belastbares Lernsignal. Deshalb darf ein Gap niemals unmittelbar policy_rules
# schreiben. Diese Bridge erzeugt nur eine priorisierte Fokusliste, die spätere
# Lernquellen gezielt nutzen können.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine GUI-, Qt-, Wayland- oder X11-Abhängigkeiten.
# - Kein DB-Write: dieses Tool öffnet oroma.db read-only über SQLite-URI mode=ro.
# - Keine Policy-Writes: weder policy_rules noch rules werden verändert.
# - Keine Schema-Migration: keine CREATE/ALTER/INSERT/UPDATE/DELETE auf DB-Ebene.
# - State-Write nur als JSON-Datei unter data/state; atomar per tmp+fsync+replace.
# - Bounded Runtime: Gaps werden per Lookback/Limit begrenzt, Policy-Abfragen in
#   kleinen Chunks ausgeführt, max-runtime-s wird vor teuren Schritten geprüft.
# - Fail-open für den Orchestrator: Fehler werden im JSON sichtbar, der Prozess
#   beendet sich kontrolliert mit Exit-Code 2 statt die Pipeline hart zu reißen.
#
# WARUM KEIN DBWRITER?
# --------------------
# DBWriter ist für DB-Writes zuständig. Dieses Tool schreibt bewusst nicht in
# managed SQLite-Datenbanken. Der einzige Persistenzpfad ist ein State-JSON für
# Diagnose und spätere Verbraucher. Damit wird die DBWriter-Disziplin nicht
# umgangen.
#
# OUTPUT
# ------
# Standard-State:
#   /opt/ai/oroma/data/state/gap_learning_focus.json
#
# Das JSON enthält:
#   - summary: Zähler, Gates, Laufzeit, DB-Pfade
#   - focus: priorisierte offene Lernfokus-Kandidaten
#   - covered: bereits durch policy_rules plausibel abgedeckte Gap-Gruppen
#   - blocked: diagnostische Gruppen ohne verwertbaren namespace/state_hash
#
# CLI-BEISPIELE (iPhone-/SSH-sicher als Einzeiler nutzbar)
# --------------------------------------------------------
#   cd /opt/ai/oroma; python3 tools/gap_learning_bridge.py --once --pretty
#   cd /opt/ai/oroma; python3 tools/gap_learning_bridge.py --once --limit-gaps 200 --topk 20
#   cd /opt/ai/oroma; python3 tools/gap_learning_bridge.py --once --no-write-state --pretty
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_GAP_LEARNING_STATE_PATH=/opt/ai/oroma/data/state/gap_learning_focus.json
#   OROMA_GAP_LEARNING_LOOKBACK_SEC=604800
#   OROMA_GAP_LEARNING_LIMIT_GAPS=500
#   OROMA_GAP_LEARNING_TOPK=25
#   OROMA_GAP_LEARNING_REFERENCE_SCHEMAS=snake:pro_v2
#   OROMA_GAP_LEARNING_REFERENCE_TOPK=5
#   OROMA_GAP_LEARNING_REFERENCE_SELECTOR=evidence_capability
#   OROMA_GAP_LEARNING_REFERENCE_CAPABILITY_SCAN_LIMIT=500
#   OROMA_GAP_LEARNING_MIN_CONFIDENCE=0.0
#   OROMA_GAP_LEARNING_NAMESPACE_ALLOWLIST=game:*
#   OROMA_GAP_LEARNING_COVERED_MIN_N=5
#   OROMA_GAP_LEARNING_UNCERTAINTY_EPS=0.05
#   OROMA_GAP_LEARNING_MAX_RUNTIME_S=30
# =============================================================================

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "v0.3.0-evidence-aware-reference-selector"
DEFAULT_STATE_NAME = "gap_learning_focus.json"


def _bootstrap_import_path() -> None:
    here = Path(__file__).resolve()
    base = here.parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))


_bootstrap_import_path()

from core.replay_evidence_capability import (  # noqa: E402
    build_replay_evidence_capability_context,
    check_replay_evidence_capability,
)


def _now() -> int:
    return int(time.time())


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


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return str(default)
    value = str(value).strip()
    return value if value != "" else str(default)


def _base_dir() -> Path:
    return Path(os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma").resolve()


def _db_path(default_base: Path) -> Path:
    explicit = os.environ.get("OROMA_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (default_base / "data" / "oroma.db").resolve()


def _state_path(default_base: Path) -> Path:
    explicit = os.environ.get("OROMA_GAP_LEARNING_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (default_base / "data" / "state" / DEFAULT_STATE_NAME).resolve()


def _parse_allowlist(raw: str) -> List[str]:
    items: List[str] = []
    for part in str(raw or "").replace(";", ",").split(","):
        item = part.strip()
        if item:
            items.append(item)
    return items



def _state_schema(state_hash: str) -> str:
    """Return the canonical schema prefix carried by a schema-prefixed state hash.

    ORÓMA state hashes such as ``snake:pro_v2:d=...`` carry a two-segment
    schema prefix. The bridge only uses this value for read-only selection; it
    never rewrites or infers missing state identities.
    """
    parts = str(state_hash or "").strip().split(":")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        return ""
    return f"{parts[0]}:{parts[1]}"


def _reference_schema_matches(state_hash: str, schemas: Sequence[str]) -> Optional[str]:
    schema = _state_schema(state_hash)
    if not schema:
        return None
    return schema if schema in set(str(x).strip() for x in schemas if str(x).strip()) else None


def _namespace_allowed(namespace: str, patterns: Sequence[str]) -> bool:
    ns = str(namespace or "").strip()
    if not patterns:
        return True
    if not ns:
        return False
    return any(fnmatch.fnmatchcase(ns, pat) for pat in patterns)


def _json_loads(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        obj = json.loads(str(value))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _clamp01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(str(text).encode("utf-8", "ignore")).hexdigest()[:max(6, int(n))]


def _open_ro(db_path: Path) -> sqlite3.Connection:
    uri = "file:" + str(db_path) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=3000")
    except Exception:
        pass
    return conn


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))


def _time_budget_hit(start: float, max_runtime_s: int, reserve_s: float = 0.5) -> bool:
    try:
        budget = float(max(0, int(max_runtime_s)))
    except Exception:
        budget = 0.0
    if budget <= 0:
        return False
    return (time.time() - start) >= max(0.0, budget - float(reserve_s))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (str(name),)).fetchone()
    return bool(row)


def _load_gaps(
    conn: sqlite3.Connection,
    *,
    since_ts: int,
    limit_gaps: int,
    min_confidence: float,
    namespace_patterns: Sequence[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    scanned = 0
    skipped = {"old": 0, "low_confidence": 0, "namespace_filtered": 0, "missing_meta": 0}

    sql = (
        "SELECT id, ts, kind, desc, confidence, meta "
        "FROM knowledge_gaps "
        "WHERE ts >= ? AND confidence >= ? "
        "ORDER BY ts DESC, id DESC LIMIT ?"
    )
    for row in conn.execute(sql, (int(since_ts), float(min_confidence), int(limit_gaps))):
        scanned += 1
        meta = _json_loads(row["meta"])
        namespace = str(meta.get("namespace") or meta.get("ns") or "").strip()
        if namespace_patterns and not _namespace_allowed(namespace, namespace_patterns):
            skipped["namespace_filtered"] += 1
            continue
        state_hash = str(meta.get("state_hash") or meta.get("state") or meta.get("hash") or "").strip()
        action = str(meta.get("action") or meta.get("chosen") or meta.get("a1") or "").strip()
        actions = []
        for key in ("action", "chosen", "a1", "a2"):
            val = str(meta.get(key) or "").strip()
            if val and val not in actions:
                actions.append(val)
        rows.append(
            {
                "id": int(row["id"]),
                "ts": int(row["ts"] or 0),
                "kind": str(row["kind"] or "unknown"),
                "desc": str(row["desc"] or ""),
                "confidence": _as_float(row["confidence"], 0.0),
                "meta": meta,
                "namespace": namespace,
                "state_hash": state_hash,
                "action": action,
                "actions": actions,
            }
        )
    return rows, {"scanned": scanned, "skipped": skipped}


def _load_reference_gaps(
    conn: sqlite3.Connection,
    *,
    since_ts: int,
    limit_gaps: int,
    min_confidence: float,
    namespace_patterns: Sequence[str],
    reference_schemas: Sequence[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load real reference-schema gaps outside the global recency window.

    This is a targeted read-only branch against the same ``knowledge_gaps``
    table. SQL ``instr`` narrows the scan, while the parsed state hash is
    validated again in Python. No rows are invented, promoted, or mutated.
    """
    rows_by_id: Dict[int, Dict[str, Any]] = {}
    scanned = 0
    skipped = {"namespace_filtered": 0, "schema_mismatch": 0, "missing_meta": 0}
    per_schema_loaded: Dict[str, int] = {}
    per_schema_scan_limit = max(1, int(limit_gaps))

    for schema in [str(x).strip() for x in reference_schemas if str(x).strip()]:
        sql = (
            "SELECT id, ts, kind, desc, confidence, meta "
            "FROM knowledge_gaps "
            "WHERE ts >= ? AND confidence >= ? AND instr(meta, ?) > 0 "
            "ORDER BY ts DESC, id DESC LIMIT ?"
        )
        loaded_for_schema = 0
        for row in conn.execute(sql, (int(since_ts), float(min_confidence), schema, per_schema_scan_limit)):
            scanned += 1
            meta = _json_loads(row["meta"])
            if not meta:
                skipped["missing_meta"] += 1
            namespace = str(meta.get("namespace") or meta.get("ns") or "").strip()
            if namespace_patterns and not _namespace_allowed(namespace, namespace_patterns):
                skipped["namespace_filtered"] += 1
                continue
            state_hash = str(meta.get("state_hash") or meta.get("state") or meta.get("hash") or "").strip()
            if _state_schema(state_hash) != schema:
                skipped["schema_mismatch"] += 1
                continue
            action = str(meta.get("action") or meta.get("chosen") or meta.get("a1") or "").strip()
            actions: List[str] = []
            for key in ("action", "chosen", "a1", "a2"):
                value = str(meta.get(key) or "").strip()
                if value and value not in actions:
                    actions.append(value)
            rec = {
                "id": int(row["id"]),
                "ts": int(row["ts"] or 0),
                "kind": str(row["kind"] or "unknown"),
                "desc": str(row["desc"] or ""),
                "confidence": _as_float(row["confidence"], 0.0),
                "meta": meta,
                "namespace": namespace,
                "state_hash": state_hash,
                "action": action,
                "actions": actions,
                "reference_schema": schema,
            }
            rows_by_id[int(rec["id"])] = rec
            loaded_for_schema += 1
        per_schema_loaded[schema] = loaded_for_schema

    rows = sorted(rows_by_id.values(), key=lambda x: (int(x.get("ts") or 0), int(x.get("id") or 0)), reverse=True)
    return rows, {
        "scanned": int(scanned),
        "loaded_unique": int(len(rows)),
        "per_schema_loaded": per_schema_loaded,
        "skipped": skipped,
    }


def _group_gaps(gaps: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for gap in gaps:
        namespace = str(gap.get("namespace") or "").strip()
        state_hash = str(gap.get("state_hash") or "").strip()
        kind = str(gap.get("kind") or "unknown").strip()
        action = str(gap.get("action") or "").strip()
        key = (namespace, state_hash, kind, action)
        item = groups.setdefault(
            key,
            {
                "namespace": namespace,
                "state_hash": state_hash,
                "kind": kind,
                "primary_action": action,
                "actions": [],
                "gap_ids": [],
                "gap_count": 0,
                "latest_ts": 0,
                "oldest_ts": 0,
                "confidence_sum": 0.0,
                "descriptions": [],
                "sources": [],
            },
        )
        item["gap_count"] += 1
        item["gap_ids"].append(int(gap.get("id") or 0))
        ts = int(gap.get("ts") or 0)
        item["latest_ts"] = max(int(item.get("latest_ts") or 0), ts)
        item["oldest_ts"] = ts if not item.get("oldest_ts") else min(int(item.get("oldest_ts") or ts), ts)
        item["confidence_sum"] += _as_float(gap.get("confidence"), 0.0)
        desc = str(gap.get("desc") or "").strip()
        if desc and desc not in item["descriptions"] and len(item["descriptions"]) < 3:
            item["descriptions"].append(desc)
        meta = gap.get("meta") if isinstance(gap.get("meta"), dict) else {}
        source = str(meta.get("source") or "").strip()
        if source and source not in item["sources"]:
            item["sources"].append(source)
        for act in gap.get("actions") or []:
            act_s = str(act or "").strip()
            if act_s and act_s not in item["actions"]:
                item["actions"].append(act_s)

    out: List[Dict[str, Any]] = []
    for item in groups.values():
        n = max(1, int(item.get("gap_count") or 1))
        item["avg_confidence"] = round(float(item.get("confidence_sum") or 0.0) / n, 4)
        item.pop("confidence_sum", None)
        if not item.get("primary_action") and item.get("actions"):
            item["primary_action"] = item["actions"][0]
        item["focus_id"] = "gap:" + _short_hash("|".join([str(item.get("namespace")), str(item.get("state_hash")), str(item.get("kind")), str(item.get("primary_action"))]))
        out.append(item)
    return out


def _load_policy_evidence(conn: sqlite3.Connection, groups: Sequence[Mapping[str, Any]], *, max_runtime_s: int, start_ts: float) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    by_ns: Dict[str, List[str]] = {}
    for group in groups:
        ns = str(group.get("namespace") or "").strip()
        sh = str(group.get("state_hash") or "").strip()
        if not ns or not sh:
            continue
        by_ns.setdefault(ns, [])
        if sh not in by_ns[ns]:
            by_ns[ns].append(sh)

    evidence: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for ns, hashes in by_ns.items():
        if _time_budget_hit(start_ts, max_runtime_s, reserve_s=1.0):
            break
        for i in range(0, len(hashes), 50):
            chunk = hashes[i : i + 50]
            if not chunk:
                continue
            placeholders = ",".join(["?"] * len(chunk))
            sql = (
                "SELECT namespace, state_hash, action, n, pos, neg, draw, q, last_ts "
                "FROM policy_rules WHERE namespace=? AND state_hash IN (" + placeholders + ")"
            )
            params = [ns] + chunk
            for row in conn.execute(sql, params):
                rec = {
                    "namespace": str(row["namespace"] or ""),
                    "state_hash": str(row["state_hash"] or ""),
                    "action": str(row["action"] or ""),
                    "n": _as_int(row["n"], 0),
                    "pos": _as_int(row["pos"], 0),
                    "neg": _as_int(row["neg"], 0),
                    "draw": _as_int(row["draw"], 0),
                    "q": _as_float(row["q"], 0.0),
                    "last_ts": _as_int(row["last_ts"], 0),
                }
                evidence.setdefault((rec["namespace"], rec["state_hash"]), []).append(rec)
    return evidence


def _summarize_policy(rows: Sequence[Mapping[str, Any]], actions: Sequence[str]) -> Dict[str, Any]:
    by_action = {str(r.get("action") or ""): r for r in rows}
    ranked = sorted(rows, key=lambda r: (_as_float(r.get("q"), 0.0), _as_int(r.get("n"), 0)), reverse=True)
    total_n = sum(_as_int(r.get("n"), 0) for r in rows)
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    q_gap = None
    if best is not None and second is not None:
        q_gap = abs(_as_float(best.get("q"), 0.0) - _as_float(second.get("q"), 0.0))
    action_stats: Dict[str, Any] = {}
    for act in actions:
        row = by_action.get(str(act))
        if row:
            action_stats[str(act)] = {"n": _as_int(row.get("n"), 0), "q": round(_as_float(row.get("q"), 0.0), 6)}
        elif act:
            action_stats[str(act)] = {"n": 0, "q": None}
    return {
        "rule_count": int(len(rows)),
        "total_n": int(total_n),
        "top_action": str(best.get("action") or "") if best else None,
        "top_q": round(_as_float(best.get("q"), 0.0), 6) if best else None,
        "top_n": _as_int(best.get("n"), 0) if best else 0,
        "second_action": str(second.get("action") or "") if second else None,
        "second_q": round(_as_float(second.get("q"), 0.0), 6) if second else None,
        "second_n": _as_int(second.get("n"), 0) if second else 0,
        "q_gap": round(float(q_gap), 6) if q_gap is not None else None,
        "action_stats": action_stats,
    }


def _classify_group(group: Mapping[str, Any], policy: Mapping[str, Any], *, covered_min_n: int, uncertainty_eps: float) -> Tuple[str, str, str]:
    namespace = str(group.get("namespace") or "").strip()
    state_hash = str(group.get("state_hash") or "").strip()
    kind = str(group.get("kind") or "unknown").strip()
    if not namespace:
        return "blocked", "missing_namespace", "review_meta"
    if not state_hash:
        return "blocked", "missing_state_hash", "review_meta"
    if int(policy.get("rule_count") or 0) <= 0:
        if kind == "logic_conflict":
            return "focus", "needs_archive_policy_review", "review"
        return "focus", "needs_policy_evidence", "explore"

    actions = group.get("actions") or []
    action_stats = policy.get("action_stats") if isinstance(policy.get("action_stats"), dict) else {}
    max_action_n = 0
    for act in actions:
        stat = action_stats.get(str(act)) if isinstance(action_stats, dict) else None
        if isinstance(stat, dict):
            max_action_n = max(max_action_n, _as_int(stat.get("n"), 0))

    if kind == "low_evidence":
        if actions and max_action_n >= int(covered_min_n):
            return "covered", "covered_by_action_evidence", "none"
        if int(policy.get("top_n") or 0) >= int(covered_min_n) and not actions:
            return "covered", "covered_by_state_evidence", "none"
        return "focus", "needs_direct_evidence", "explore"

    if kind == "high_uncertainty":
        q_gap = policy.get("q_gap")
        if q_gap is not None and float(q_gap) >= float(uncertainty_eps) and int(policy.get("top_n") or 0) >= int(covered_min_n):
            return "covered", "uncertainty_reduced", "none"
        return "focus", "needs_comparison_evidence", "replay_or_dream"

    if kind == "logic_conflict":
        return "focus", "needs_review_evidence", "review"

    if int(policy.get("total_n") or 0) < int(covered_min_n):
        return "focus", "needs_more_state_evidence", "explore"
    return "focus", "needs_focus_review", "review"


def _score_group(group: Mapping[str, Any], policy: Mapping[str, Any], *, now_ts: int, lookback_sec: int, covered_min_n: int) -> float:
    kind = str(group.get("kind") or "unknown")
    avg_conf = _clamp01(_as_float(group.get("avg_confidence"), 0.0))
    age = max(0, now_ts - _as_int(group.get("latest_ts"), now_ts))
    recency = 1.0 if lookback_sec <= 0 else _clamp01(1.0 - (float(age) / max(1.0, float(lookback_sec))))
    total_n = _as_int(policy.get("total_n"), 0)
    evidence_gap = 1.0 - _clamp01(float(total_n) / max(1.0, float(covered_min_n * 2)))
    kind_bonus = {
        "logic_conflict": 0.35,
        "high_uncertainty": 0.25,
        "low_evidence": 0.20,
    }.get(kind, 0.10)
    gap_count_bonus = min(0.15, 0.03 * _as_int(group.get("gap_count"), 1))
    score = (0.42 * avg_conf) + (0.23 * recency) + (0.20 * evidence_gap) + kind_bonus + gap_count_bonus
    return round(_clamp01(score), 6)


def build_focus(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    start = time.time()
    now_ts = _now()
    base = Path(args.base).resolve() if args.base else _base_dir()
    db_path = Path(args.db_path).resolve() if args.db_path else _db_path(base)
    state_path = Path(args.state_path).resolve() if args.state_path else _state_path(base)
    lookback_sec = int(args.lookback_sec)
    since_ts = int(now_ts - max(0, lookback_sec))
    patterns = _parse_allowlist(args.namespace_allowlist)
    reference_schemas = _parse_allowlist(args.reference_schemas)

    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "dry_run_focus_bridge",
        "generated_at_ts": now_ts,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now_ts)),
        "base": str(base),
        "db_path": str(db_path),
        "state_path": str(state_path),
        "safety": {
            "db_open_mode": "read_only_uri_mode_ro",
            "db_writes": False,
            "policy_writes": False,
            "schema_changes": False,
            "state_json_write": not bool(args.no_write_state),
        },
        "config": {
            "lookback_sec": int(lookback_sec),
            "limit_gaps": int(args.limit_gaps),
            "topk": int(args.topk),
            "reference_schemas": reference_schemas,
            "reference_topk": int(args.reference_topk),
            "reference_selector": str(args.reference_selector),
            "reference_capability_scan_limit": int(args.reference_capability_scan_limit),
            "min_confidence": float(args.min_confidence),
            "namespace_allowlist": patterns,
            "covered_min_n": int(args.covered_min_n),
            "uncertainty_eps": float(args.uncertainty_eps),
            "max_runtime_s": int(args.max_runtime_s),
        },
        "summary": {},
        "focus": [],
        "covered": [],
        "blocked": [],
        "errors": [],
    }

    if not db_path.exists():
        doc["summary"] = {"blocked_reason": "db_path_missing", "dt_sec": round(time.time() - start, 3)}
        doc["errors"].append(f"oroma.db not found: {db_path}")
        return doc, 2

    try:
        with _open_ro(db_path) as conn:
            if not _table_exists(conn, "knowledge_gaps"):
                doc["summary"] = {"blocked_reason": "knowledge_gaps_missing", "dt_sec": round(time.time() - start, 3)}
                return doc, 2
            if not _table_exists(conn, "policy_rules"):
                doc["summary"] = {"blocked_reason": "policy_rules_missing", "dt_sec": round(time.time() - start, 3)}
                return doc, 2

            gaps, load_summary = _load_gaps(
                conn,
                since_ts=since_ts,
                limit_gaps=int(args.limit_gaps),
                min_confidence=float(args.min_confidence),
                namespace_patterns=patterns,
            )
            reference_gaps, reference_load_summary = _load_reference_gaps(
                conn,
                since_ts=since_ts,
                limit_gaps=int(args.limit_gaps),
                min_confidence=float(args.min_confidence),
                namespace_patterns=patterns,
                reference_schemas=reference_schemas,
            )
            global_groups = _group_gaps(gaps)
            reference_groups = _group_gaps(reference_gaps)
            all_groups_by_id: Dict[str, Dict[str, Any]] = {}
            for group in list(global_groups) + list(reference_groups):
                all_groups_by_id[str(group.get("focus_id") or "")] = group
            evidence = _load_policy_evidence(conn, list(all_groups_by_id.values()), max_runtime_s=int(args.max_runtime_s), start_ts=start)

            def classify(groups_to_classify: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
                focus_local: List[Dict[str, Any]] = []
                covered_local: List[Dict[str, Any]] = []
                blocked_local: List[Dict[str, Any]] = []
                for group in groups_to_classify:
                    pol_rows = evidence.get((str(group.get("namespace") or ""), str(group.get("state_hash") or "")), [])
                    policy = _summarize_policy(pol_rows, group.get("actions") or [])
                    bucket, reason, recommended_next = _classify_group(
                        group, policy, covered_min_n=int(args.covered_min_n), uncertainty_eps=float(args.uncertainty_eps)
                    )
                    item = dict(group)
                    item["policy_evidence"] = policy
                    item["status"] = bucket
                    item["reason"] = reason
                    item["recommended_next"] = recommended_next
                    item["score"] = _score_group(
                        group, policy, now_ts=now_ts, lookback_sec=lookback_sec, covered_min_n=int(args.covered_min_n)
                    )
                    item["gap_ids"] = [int(x) for x in (item.get("gap_ids") or [])[:10]]
                    item["dedupe_key"] = str(item.get("focus_id") or "")
                    if bucket == "focus":
                        focus_local.append(item)
                    elif bucket == "covered":
                        covered_local.append(item)
                    else:
                        blocked_local.append(item)
                focus_local.sort(key=lambda x: (float(x.get("score") or 0.0), int(x.get("latest_ts") or 0), str(x.get("focus_id") or "")), reverse=True)
                covered_local.sort(key=lambda x: (int(x.get("latest_ts") or 0), float(x.get("score") or 0.0)), reverse=True)
                blocked_local.sort(key=lambda x: int(x.get("latest_ts") or 0), reverse=True)
                return focus_local, covered_local, blocked_local

            global_focus, covered_items, blocked_items = classify(global_groups)
            reference_focus, _, _ = classify(reference_groups)
            global_rank = {str(item.get("focus_id") or ""): idx for idx, item in enumerate(global_focus, start=1)}

            selected: Dict[str, Dict[str, Any]] = {}
            for item in global_focus[: int(args.topk)]:
                rec = dict(item)
                rec["selection_source"] = "global_priority"
                rec["selection_sources"] = ["global_priority"]
                rec["global_rank"] = global_rank.get(str(rec.get("focus_id") or ""))
                rec["reference_schema"] = _reference_schema_matches(str(rec.get("state_hash") or ""), reference_schemas)
                rec["reference_rank"] = None
                selected[str(rec.get("focus_id") or "")] = rec

            capability_context = None
            capability_checked = 0
            capability_available = 0
            capability_block_reasons: Dict[str, int] = {}
            if str(args.reference_selector) == "evidence_capability" and reference_schemas:
                capability_context = build_replay_evidence_capability_context(
                    conn, schemas=list(reference_schemas),
                    scan_limit=max(1, int(args.reference_capability_scan_limit)),
                )

            reference_rank_by_schema: Dict[str, int] = {}
            reference_selected = 0
            for item in reference_focus:
                schema = _reference_schema_matches(str(item.get("state_hash") or ""), reference_schemas)
                if not schema:
                    continue
                capability = None
                if str(args.reference_selector) == "evidence_capability":
                    capability_checked += 1
                    capability = check_replay_evidence_capability(
                        conn, namespace=str(item.get("namespace") or ""),
                        state_schema=schema, state_hash=str(item.get("state_hash") or ""),
                        action=str(item.get("primary_action") or ""),
                        scan_limit=max(1, int(args.reference_capability_scan_limit)),
                        context=capability_context,
                    )
                    if not bool(capability.get("available")):
                        reason = str(capability.get("blocked_reason") or "replay_evidence_capability_unavailable")
                        capability_block_reasons[reason] = capability_block_reasons.get(reason, 0) + 1
                        continue
                    capability_available += 1
                next_rank = reference_rank_by_schema.get(schema, 0) + 1
                reference_rank_by_schema[schema] = next_rank
                if next_rank > int(args.reference_topk):
                    continue
                key = str(item.get("focus_id") or "")
                if key in selected:
                    rec = selected[key]
                    rec["selection_sources"] = ["global_priority", "reference_evidence_capability"]
                    rec["reference_schema"] = schema
                    rec["reference_rank"] = next_rank
                    rec["reference_capability"] = capability
                    continue
                rec = dict(item)
                rec["selection_source"] = "reference_evidence_capability" if capability is not None else "reference_quota"
                rec["selection_sources"] = [rec["selection_source"]]
                rec["global_rank"] = global_rank.get(key)
                rec["reference_schema"] = schema
                rec["reference_rank"] = next_rank
                rec["reference_capability"] = capability
                selected[key] = rec
                reference_selected += 1

            doc["focus"] = list(selected.values())
            doc["covered"] = covered_items[: int(args.topk)]
            doc["blocked"] = blocked_items[: int(args.topk)]
            doc["summary"] = {
                "ok": True,
                "gap_rows_loaded": int(len(gaps)),
                "reference_gap_rows_loaded": int(len(reference_gaps)),
                "gap_groups_total": int(len(global_groups)),
                "reference_gap_groups_total": int(len(reference_groups)),
                "focus_total": int(len(global_focus)),
                "focus_returned": int(len(doc["focus"])),
                "global_focus_returned": int(min(len(global_focus), int(args.topk))),
                "reference_focus_added": int(reference_selected),
                "reference_capability_checked": int(capability_checked),
                "reference_capability_available": int(capability_available),
                "reference_capability_block_reason_counts": capability_block_reasons,
                "reference_capability_shared_scan_snapchains": int((capability_context or {}).get("snapchains_scanned") or 0),
                "reference_capability_shared_scan_steps": int((capability_context or {}).get("steps_scanned_total") or 0),
                "reference_capability_shared_scan_decode_errors": int((capability_context or {}).get("decode_errors") or 0),
                "reference_capability_shared_scan_build_dt_ms": float((capability_context or {}).get("build_dt_ms") or 0.0),
                "covered_total": int(len(covered_items)),
                "blocked_total": int(len(blocked_items)),
                "policy_state_keys_checked": int(len(evidence)),
                "load_summary": load_summary,
                "reference_load_summary": reference_load_summary,
                "selection_source_counts": {
                    "global_priority": sum(1 for item in doc["focus"] if item.get("selection_source") == "global_priority"),
                    "reference_quota": sum(1 for item in doc["focus"] if item.get("selection_source") == "reference_quota"),
                    "reference_evidence_capability": sum(1 for item in doc["focus"] if item.get("selection_source") == "reference_evidence_capability"),
                },
                "time_budget_hit": _time_budget_hit(start, int(args.max_runtime_s), reserve_s=0.0),
                "dt_sec": round(time.time() - start, 3),
            }
            doc["ok"] = True
    except sqlite3.OperationalError as e:
        doc["summary"] = {"blocked_reason": "sqlite_operational_error", "dt_sec": round(time.time() - start, 3)}
        doc["errors"].append(repr(e))
        return doc, 2
    except Exception as e:
        doc["summary"] = {"blocked_reason": "unexpected_error", "dt_sec": round(time.time() - start, 3)}
        doc["errors"].append(repr(e))
        return doc, 2

    if not args.no_write_state:
        try:
            _atomic_write_json(state_path, doc)
            doc.setdefault("summary", {})["state_written"] = True
        except Exception as e:
            doc.setdefault("summary", {})["state_written"] = False
            doc["errors"].append(f"state_write_failed: {e!r}")
            return doc, 2
    else:
        doc.setdefault("summary", {})["state_written"] = False
    return doc, 0 if doc.get("ok") else 2


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    base = _base_dir()
    parser = argparse.ArgumentParser(description="ORÓMA Gap-Learning-Bridge Dry-Run Focus Generator")
    parser.add_argument("--once", action="store_true", help="Einmaliger Lauf; aus Kompatibilitätsgründen vorhanden.")
    parser.add_argument("--base", default=str(base), help="ORÓMA-Basisverzeichnis")
    parser.add_argument("--db-path", default=str(_db_path(base)), help="Pfad zu oroma.db; wird read-only geöffnet")
    parser.add_argument("--state-path", default=str(_state_path(base)), help="Ziel-State-JSON")
    parser.add_argument("--lookback-sec", type=int, default=_env_int("OROMA_GAP_LEARNING_LOOKBACK_SEC", 604800))
    parser.add_argument("--limit-gaps", type=int, default=_env_int("OROMA_GAP_LEARNING_LIMIT_GAPS", 500))
    parser.add_argument("--topk", type=int, default=_env_int("OROMA_GAP_LEARNING_TOPK", 25))
    parser.add_argument(
        "--reference-schemas",
        default=_env_str("OROMA_GAP_LEARNING_REFERENCE_SCHEMAS", "snake:pro_v2"),
        help="Kommagetrennte State-Schemas für die read-only Referenz-Fairness",
    )
    parser.add_argument(
        "--reference-topk",
        type=int,
        default=_env_int("OROMA_GAP_LEARNING_REFERENCE_TOPK", 5),
        help="Maximale zusätzliche Fokusgruppen pro Referenzschema",
    )
    parser.add_argument(
        "--reference-selector",
        default=_env_str("OROMA_GAP_LEARNING_REFERENCE_SELECTOR", "evidence_capability"),
        choices=("evidence_capability", "priority_only"),
        help="Referenzauswahl: direkte Evidence-Capability bevorzugen oder Legacy-Priorität verwenden",
    )
    parser.add_argument(
        "--reference-capability-scan-limit",
        type=int,
        default=_env_int("OROMA_GAP_LEARNING_REFERENCE_CAPABILITY_SCAN_LIMIT", 500),
        help="Maximale SnapChains für den einmaligen read-only Capability-Context",
    )
    parser.add_argument("--min-confidence", type=float, default=_env_float("OROMA_GAP_LEARNING_MIN_CONFIDENCE", 0.0))
    parser.add_argument("--namespace-allowlist", default=_env_str("OROMA_GAP_LEARNING_NAMESPACE_ALLOWLIST", "game:*"))
    parser.add_argument("--covered-min-n", type=int, default=_env_int("OROMA_GAP_LEARNING_COVERED_MIN_N", 5))
    parser.add_argument("--uncertainty-eps", type=float, default=_env_float("OROMA_GAP_LEARNING_UNCERTAINTY_EPS", 0.05))
    parser.add_argument("--max-runtime-s", type=int, default=_env_int("OROMA_GAP_LEARNING_MAX_RUNTIME_S", 30))
    parser.add_argument("--no-write-state", action="store_true", help="Nur stdout, kein State-JSON schreiben")
    parser.add_argument("--pretty", action="store_true", help="JSON formatiert ausgeben")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    doc, rc = build_focus(args)
    print(json.dumps(doc, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=bool(args.pretty)))
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())

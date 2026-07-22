#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_evidence_validation.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Execution/Validation · Read-Only Dry-Run
# Version:   v0.3.0-targeted-evidence-observability
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die naechste Sicherheitsstufe nach der Gap Evidence Queue
# Review. Die bisherige Kette lautet:
#
#   knowledge_gaps
#       -> gap_learning_focus.json
#       -> gap_focus_consumer.json
#       -> gap_focus_shadow_plan.json
#       -> gap_focus_evidence_queue          (DBWriter-only Queue-Write)
#       -> gap_evidence_review.json          (read-only Review)
#       -> gap_evidence_validation.json      (dieses Modul, read-only)
#
# Die Validation-Stufe fuehrt KEINE Replay-, Dream- oder Runner-Jobs aus. Sie
# prueft nur, ob Review-Kandidaten technisch sauber genug sind, um spaeter in
# getrennte Evidence-Execution-/Promotion-Gates aufgenommen zu werden. Dazu liest
# sie die Review-State-Datei und gleicht die referenzierten Queue-Zeilen sowie
# den aktuellen policy_rules-Stand read-only gegen die SQLite-DB ab.
#
# WICHTIGER SEMANTISCHER UNTERSCHIED
# ----------------------------------
# - Review:     "Welche Queue-Zeilen sehen fachlich plausibel aus?"
# - Validation: "Welche davon sind technisch/semantisch stabil genug, um spaeter
#                in einen echten Replay-/Dream-/Runner-Prioritaets-Gate zu gehen?"
#
# Diese Stufe ist also noch kein Beweis fuer eine Policy-Aenderung. Sie erzeugt
# nur eine validierte, auditierbare Kandidatensicht:
#
#   - validated_replay_execution_candidate
#   - validated_dream_execution_candidate
#   - validated_runner_priority_hint
#   - validated_explore_candidate
#   - blocked_or_insufficient
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhaengigkeiten.
# - SQLite nur read-only via URI mode=ro; keine DBWriter-Nutzung, weil kein Write.
# - Keine DB-Writes, keine Schemaaenderungen, keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Kein lokaler SQLite-Schreib-Fallback.
# - State-Write nur atomar nach data/state/gap_evidence_validation.json.
# - Root-Manual-Laeufe setzen die State-Datei best-effort auf oroma:oroma 664.
# - Fail-soft: fehlende Queue-/Policy-/Review-Daten werden sichtbar dokumentiert.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_EVIDENCE_VALIDATION_SOURCE_PATH=/opt/ai/oroma/data/state/gap_evidence_review.json
#   OROMA_GAP_EVIDENCE_VALIDATION_STATE_PATH=/opt/ai/oroma/data/state/gap_evidence_validation.json
#   OROMA_GAP_EVIDENCE_VALIDATION_BUCKETS=ready_for_replay_review,ready_for_dream_review,runner_priority_hint,explore_plan_candidate
#   OROMA_GAP_EVIDENCE_VALIDATION_MAX_AGE_SEC=7200
#   OROMA_GAP_EVIDENCE_VALIDATION_ALLOW_STALE=0
#   OROMA_GAP_EVIDENCE_VALIDATION_LIMIT=200
#   OROMA_GAP_EVIDENCE_VALIDATION_TOPK=10
#   OROMA_GAP_EVIDENCE_VALIDATION_MIN_SCORE=0.0
#   OROMA_GAP_EVIDENCE_VALIDATION_MIN_RULE_COUNT=1
#   OROMA_GAP_EVIDENCE_VALIDATION_MIN_TOTAL_N=1
# =============================================================================

from __future__ import annotations

import json
import os
import pwd
import grp
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.replay_evidence_capability import build_replay_evidence_capability_context, check_replay_evidence_capability, state_schema_from_hash

VERSION = "v0.3.0-targeted-evidence-observability"
DEFAULT_SOURCE_NAME = "gap_evidence_review.json"
DEFAULT_STATE_NAME = "gap_evidence_validation.json"
DEFAULT_BUCKETS = (
    "ready_for_replay_review",
    "ready_for_dream_review",
    "runner_priority_hint",
    "explore_plan_candidate",
)
TABLE_NAME = "gap_focus_evidence_queue"
DEFAULT_REPLAY_CAPABILITY_SCHEMAS = ("snake:pro_v2",)


def _now_ts() -> int:
    return int(time.time())


def _iso(ts: Optional[int] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(int(ts if ts is not None else _now_ts())))


def _base_dir() -> Path:
    return Path(os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma").resolve()


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return str(default)
    value = str(value).strip()
    return value if value else str(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.environ.get(name, str(default))).strip()))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def parse_csv(raw: str, default: Sequence[str] = ()) -> List[str]:
    text = str(raw or "").replace(";", ",")
    out = [p.strip() for p in text.split(",") if p.strip()]
    return out if out else list(default)


def _safe_str(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > int(limit):
        return text[: max(0, int(limit) - 3)] + "..."
    return text


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def default_db_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "oroma.db").resolve()


def default_source_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_VALIDATION_SOURCE_PATH") or os.environ.get("OROMA_GAP_EVIDENCE_REVIEW_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_SOURCE_NAME).resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_VALIDATION_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_STATE_NAME).resolve()


def _sqlite_ro_uri(db_path: Path) -> str:
    return "file:%s?mode=ro" % str(db_path.resolve())


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (str(table),)).fetchone()
    return bool(row)


def _json_loads(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_json_file(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data if isinstance(data, dict) else None), None if isinstance(data, dict) else "source_not_json_object"
    except FileNotFoundError:
        return None, "source_missing"
    except Exception as exc:
        return None, f"source_load_failed:{exc}"


def _source_age(source: Mapping[str, Any], source_path: Path, now_ts: int) -> Optional[int]:
    ts = _as_int(source.get("generated_at_ts"), 0)
    if ts > 0:
        return max(0, int(now_ts - ts))
    try:
        return max(0, int(now_ts - int(source_path.stat().st_mtime)))
    except Exception:
        return None


def _iter_review_items(source: Mapping[str, Any], bucket_allowlist: Sequence[str], limit: int) -> List[Dict[str, Any]]:
    review = source.get("review") if isinstance(source.get("review"), dict) else {}
    out: List[Dict[str, Any]] = []
    for bucket in bucket_allowlist:
        block = review.get(bucket) if isinstance(review.get(bucket), dict) else {}
        items = block.get("items") if isinstance(block.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            clone = dict(item)
            clone.setdefault("source_review_bucket", bucket)
            out.append(clone)
            if len(out) >= int(limit):
                return out
    return out


def _queue_row_by_signature(con: sqlite3.Connection, signature: str) -> Optional[sqlite3.Row]:
    if not signature or not _table_exists(con, TABLE_NAME):
        return None
    return con.execute(
        "SELECT id, request_signature, plan_id, focus_id, target, namespace, state_hash, primary_action, "
        "kind, reason, recommended_next, score, status, source_plan_ts, created_ts, updated_ts, attempts, meta_json "
        f"FROM {TABLE_NAME} WHERE request_signature=? LIMIT 1",
        (signature,),
    ).fetchone()


def _current_policy_evidence(con: sqlite3.Connection, namespace: str, state_hash: str) -> Dict[str, Any]:
    if not namespace or not state_hash or not _table_exists(con, "policy_rules"):
        return {"available": False, "reason": "policy_rules_missing_or_identity_incomplete", "rule_count": 0, "total_n": 0}
    rows = con.execute(
        "SELECT action, n, q, pos, neg, draw FROM policy_rules WHERE namespace=? AND state_hash=? ORDER BY n DESC, q DESC LIMIT 20",
        (namespace, state_hash),
    ).fetchall()
    if not rows:
        return {"available": True, "rule_count": 0, "total_n": 0, "actions": [], "q_gap": None}
    actions: List[Dict[str, Any]] = []
    total_n = 0
    for r in rows:
        n = _as_int(r["n"], 0)
        total_n += n
        actions.append({
            "action": _safe_str(r["action"], 160),
            "n": n,
            "q": _as_float(r["q"], 0.0),
            "pos": _as_int(r["pos"], 0),
            "neg": _as_int(r["neg"], 0),
            "draw": _as_int(r["draw"], 0),
        })
    top = actions[0]
    second = actions[1] if len(actions) > 1 else None
    q_gap = None if second is None else abs(_as_float(top.get("q"), 0.0) - _as_float(second.get("q"), 0.0))
    return {
        "available": True,
        "rule_count": len(actions),
        "total_n": total_n,
        "q_gap": q_gap,
        "top_action": top.get("action"),
        "top_q": top.get("q"),
        "top_n": top.get("n"),
        "second_action": second.get("action") if second else None,
        "second_q": second.get("q") if second else None,
        "second_n": second.get("n") if second else None,
        "actions": actions[:6],
    }


def _meta_policy_evidence_from_row(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
    if row is None:
        return {}
    meta = _json_loads(row["meta_json"])
    pe = meta.get("policy_evidence") if isinstance(meta.get("policy_evidence"), dict) else {}
    return {
        "rule_count": _as_int(pe.get("rule_count"), 0),
        "total_n": _as_int(pe.get("total_n"), 0),
        "q_gap": None if pe.get("q_gap") is None else _as_float(pe.get("q_gap"), 0.0),
        "top_action": _safe_str(pe.get("top_action"), 160),
        "top_q": _as_float(pe.get("top_q"), 0.0),
        "top_n": _as_int(pe.get("top_n"), 0),
        "second_action": _safe_str(pe.get("second_action"), 160),
        "second_q": _as_float(pe.get("second_q"), 0.0),
        "second_n": _as_int(pe.get("second_n"), 0),
    }


def _best_policy_basis(current: Mapping[str, Any], review_item: Mapping[str, Any], queue_row: Optional[sqlite3.Row]) -> Tuple[str, Dict[str, Any]]:
    if _as_int(current.get("rule_count"), 0) > 0:
        return "current_policy_rules", dict(current)
    meta_review = review_item.get("meta_policy_evidence") if isinstance(review_item.get("meta_policy_evidence"), dict) else {}
    if _as_int(meta_review.get("rule_count"), 0) > 0:
        return "review_meta_policy_snapshot", dict(meta_review)
    meta_queue = _meta_policy_evidence_from_row(queue_row)
    if _as_int(meta_queue.get("rule_count"), 0) > 0:
        return "queue_meta_policy_snapshot", meta_queue
    return "missing_policy_evidence", {"rule_count": 0, "total_n": 0, "q_gap": None}


def _target_bucket(target: str, source_bucket: str) -> str:
    if target == "replay" or source_bucket == "ready_for_replay_review":
        return "validated_replay_execution_candidate"
    if target == "dream" or source_bucket == "ready_for_dream_review":
        return "validated_dream_execution_candidate"
    if target == "runner_priority" or source_bucket == "runner_priority_hint":
        return "validated_runner_priority_hint"
    if target == "explore" or source_bucket == "explore_plan_candidate":
        return "validated_explore_candidate"
    return "blocked_or_insufficient"


def _validate_item(
    item: Mapping[str, Any],
    queue_row: Optional[sqlite3.Row],
    current_policy: Mapping[str, Any],
    min_score: float,
    min_rule_count: int,
    min_total_n: int,
    replay_capability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    signature = _safe_str(item.get("request_signature"), 256)
    source_bucket = _safe_str(item.get("source_review_bucket") or item.get("review_bucket"), 120)
    row_target = _safe_str(queue_row["target"], 80) if queue_row is not None else _safe_str(item.get("target"), 80)
    namespace = _safe_str(queue_row["namespace"], 160) if queue_row is not None else _safe_str(item.get("namespace"), 160)
    state_hash = _safe_str(queue_row["state_hash"], 4000) if queue_row is not None else _safe_str(item.get("state_hash"), 4000)
    primary_action = _safe_str(queue_row["primary_action"], 160) if queue_row is not None else _safe_str(item.get("primary_action"), 160)
    score = _as_float(queue_row["score"], 0.0) if queue_row is not None else _as_float(item.get("score"), 0.0)
    status = _safe_str(queue_row["status"], 80) if queue_row is not None else _safe_str(item.get("status"), 80)

    basis_name, basis_pe = _best_policy_basis(current_policy, item, queue_row)
    rule_count = _as_int(basis_pe.get("rule_count"), 0)
    total_n = _as_int(basis_pe.get("total_n"), 0)

    blocked_reasons: List[str] = []
    if not signature:
        blocked_reasons.append("missing_request_signature")
    if queue_row is None:
        blocked_reasons.append("queue_row_missing")
    if status != "queued":
        blocked_reasons.append("queue_status_not_queued")
    if not namespace or not state_hash:
        blocked_reasons.append("missing_namespace_or_state_hash")
    if not primary_action:
        blocked_reasons.append("missing_primary_action")
    if score < float(min_score):
        blocked_reasons.append("score_below_min")
    if rule_count < int(min_rule_count):
        blocked_reasons.append("policy_rule_count_below_min")
    if total_n < int(min_total_n):
        blocked_reasons.append("policy_total_n_below_min")
    if source_bucket not in DEFAULT_BUCKETS:
        blocked_reasons.append("source_review_bucket_not_eligible")

    capability = dict(replay_capability or {})
    if row_target == "replay" and capability:
        if not bool(capability.get("supported", False)):
            blocked_reasons.append(str(capability.get("blocked_reason") or "replay_evidence_capability_not_implemented"))
        elif not bool(capability.get("available", False)):
            blocked_reasons.append(str(capability.get("blocked_reason") or "replay_evidence_capability_unavailable"))

    bucket = _target_bucket(row_target, source_bucket)
    if blocked_reasons:
        bucket = "blocked_or_insufficient"

    validation_reason = "validated_for_dry_run_execution_gate" if not blocked_reasons else ",".join(blocked_reasons)
    dry_run_action = {
        "validated_replay_execution_candidate": "would_validate_existing_replay_or_snapchain_evidence",
        "validated_dream_execution_candidate": "would_prepare_shadow_dream_validation_request",
        "validated_runner_priority_hint": "would_update_runner_priority_hint_after_separate_gate",
        "validated_explore_candidate": "would_prepare_explore_plan_after_separate_gate",
        "blocked_or_insufficient": "no_execution_candidate",
    }.get(bucket, "no_execution_candidate")

    return {
        "id": _as_int(queue_row["id"], _as_int(item.get("id"), 0)) if queue_row is not None else _as_int(item.get("id"), 0),
        "request_signature": signature,
        "plan_id": _safe_str(queue_row["plan_id"], 160) if queue_row is not None else _safe_str(item.get("plan_id"), 160),
        "focus_id": _safe_str(queue_row["focus_id"], 160) if queue_row is not None else _safe_str(item.get("focus_id"), 160),
        "target": row_target,
        "namespace": namespace,
        "state_hash": state_hash,
        "primary_action": primary_action,
        "kind": _safe_str(queue_row["kind"], 120) if queue_row is not None else _safe_str(item.get("kind"), 120),
        "reason": _safe_str(queue_row["reason"], 240) if queue_row is not None else _safe_str(item.get("reason"), 240),
        "recommended_next": _safe_str(queue_row["recommended_next"], 160) if queue_row is not None else _safe_str(item.get("recommended_next"), 160),
        "score": score,
        "status": status,
        "source_review_bucket": source_bucket,
        "validation_bucket": bucket,
        "validation_reason": validation_reason,
        "blocked_reasons": blocked_reasons,
        "policy_validation_basis": basis_name,
        "policy_evidence_for_validation": basis_pe,
        "current_policy_evidence": dict(current_policy),
        "replay_evidence_capability": capability if row_target == "replay" else {},
        "execution": {
            "validation_only": True,
            "dry_run_action": dry_run_action,
            "start_replay": False,
            "start_dream": False,
            "start_runner": False,
            "write_db": False,
            "write_policy": False,
        },
        "promotion": {
            "policy_write_allowed": False,
            "reason": "validation_is_not_promotion_gate",
            "next_required_gate": "gap_evidence_promotion_review",
        },
        "created_ts": _as_int(queue_row["created_ts"], _as_int(item.get("created_ts"), 0)) if queue_row is not None else _as_int(item.get("created_ts"), 0),
        "updated_ts": _as_int(queue_row["updated_ts"], _as_int(item.get("updated_ts"), 0)) if queue_row is not None else _as_int(item.get("updated_ts"), 0),
        "attempts": _as_int(queue_row["attempts"], _as_int(item.get("attempts"), 0)) if queue_row is not None else _as_int(item.get("attempts"), 0),
    }


def _bucket_map(items: Iterable[Mapping[str, Any]], topk: int) -> Dict[str, Dict[str, Any]]:
    names = (
        "validated_replay_execution_candidate",
        "validated_dream_execution_candidate",
        "validated_runner_priority_hint",
        "validated_explore_candidate",
        "blocked_or_insufficient",
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
    for it in items:
        bucket = _safe_str(it.get("validation_bucket"), 120)
        if bucket not in grouped:
            bucket = "blocked_or_insufficient"
        grouped[bucket].append(dict(it))
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        arr = sorted(grouped[name], key=lambda x: (_as_float(x.get("score"), 0.0), _as_int(x.get("updated_ts"), 0), _as_int(x.get("id"), 0)), reverse=True)
        out[name] = {
            "count_total": len(arr),
            "count_returned": min(len(arr), int(topk)),
            "execution": {
                "validation_only": True,
                "start_job": False,
                "write_db": False,
                "write_policy": False,
            },
            "items": arr[: int(topk)],
        }
    return out


def build_evidence_validation(
    *,
    db_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    buckets: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    max_age_sec: Optional[int] = None,
    allow_stale: Optional[bool] = None,
    min_score: Optional[float] = None,
    min_rule_count: Optional[int] = None,
    min_total_n: Optional[int] = None,
    replay_capability_schemas: Optional[Sequence[str]] = None,
    replay_capability_scan_limit: Optional[int] = None,
) -> Dict[str, Any]:
    start = time.time()
    now = _now_ts()
    base = _base_dir()
    dbp = (db_path or default_db_path(base)).resolve()
    srcp = (source_path or default_source_path(base)).resolve()
    outp = (state_path or default_state_path(base)).resolve()
    bucket_list = list(buckets or parse_csv(_env_str("OROMA_GAP_EVIDENCE_VALIDATION_BUCKETS", ",".join(DEFAULT_BUCKETS)), DEFAULT_BUCKETS))
    row_limit = max(1, int(limit if limit is not None else _env_int("OROMA_GAP_EVIDENCE_VALIDATION_LIMIT", 200)))
    top_n = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_EVIDENCE_VALIDATION_TOPK", 10)))
    max_age = int(max_age_sec if max_age_sec is not None else _env_int("OROMA_GAP_EVIDENCE_VALIDATION_MAX_AGE_SEC", 7200))
    stale_allowed = bool(allow_stale if allow_stale is not None else _env_bool("OROMA_GAP_EVIDENCE_VALIDATION_ALLOW_STALE", False))
    min_s = float(min_score if min_score is not None else _env_float("OROMA_GAP_EVIDENCE_VALIDATION_MIN_SCORE", 0.0))
    min_rc = int(min_rule_count if min_rule_count is not None else _env_int("OROMA_GAP_EVIDENCE_VALIDATION_MIN_RULE_COUNT", 1))
    min_n = int(min_total_n if min_total_n is not None else _env_int("OROMA_GAP_EVIDENCE_VALIDATION_MIN_TOTAL_N", 1))
    capability_schemas = list(replay_capability_schemas or parse_csv(_env_str("OROMA_GAP_EVIDENCE_VALIDATION_REPLAY_CAPABILITY_SCHEMAS", ",".join(DEFAULT_REPLAY_CAPABILITY_SCHEMAS)), DEFAULT_REPLAY_CAPABILITY_SCHEMAS))
    capability_scan_limit = max(1, int(replay_capability_scan_limit if replay_capability_scan_limit is not None else _env_int("OROMA_GAP_EVIDENCE_VALIDATION_REPLAY_CAPABILITY_SCAN_LIMIT", 500)))

    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "read_only_gap_evidence_validation",
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "base": str(base),
        "db_path": str(dbp),
        "source_path": str(srcp),
        "state_path": str(outp),
        "config": {
            "buckets": bucket_list,
            "limit": row_limit,
            "topk": top_n,
            "max_age_sec": max_age,
            "allow_stale": stale_allowed,
            "min_score": min_s,
            "min_rule_count": min_rc,
            "min_total_n": min_n,
            "replay_capability_schemas": capability_schemas,
            "replay_capability_scan_limit": capability_scan_limit,
            "replay_capability_enforcement": "scoped_reference_migration",
        },
        "source": {},
        "queue": {},
        "validation": {},
        "summary": {},
        "errors": [],
        "safety": {
            "db_open_mode": "read_only_uri_mode_ro",
            "db_writes": False,
            "policy_writes": False,
            "rules_writes": False,
            "schema_changes": False,
            "runner_starts": False,
            "replay_starts": False,
            "dream_starts": False,
            "execution_starts": False,
            "state_json_write": True,
        },
    }

    source, source_err = _load_json_file(srcp)
    if source_err or source is None:
        doc["source"] = {"ok": False, "reason": source_err or "source_invalid"}
        doc["summary"] = {"ok": False, "blocked_reason": source_err or "source_invalid", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc

    age = _source_age(source, srcp, now)
    stale = bool(age is not None and age > max_age)
    source_ok = bool(source.get("ok", False))
    doc["source"] = {
        "ok": source_ok,
        "mode": source.get("mode"),
        "version": source.get("version"),
        "generated_at_ts": source.get("generated_at_ts"),
        "generated_at_iso": source.get("generated_at_iso"),
        "age_sec": age,
        "stale": stale,
        "summary": source.get("summary") if isinstance(source.get("summary"), dict) else {},
    }
    if not source_ok:
        doc["summary"] = {"ok": False, "blocked_reason": "source_not_ok", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc
    if stale and not stale_allowed:
        doc["summary"] = {"ok": False, "blocked_reason": "source_stale", "source_age_sec": age, "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc

    source_items = _iter_review_items(source, bucket_list, row_limit)

    try:
        con = _connect_ro(dbp)
    except Exception as exc:
        doc["errors"].append({"where": "sqlite_connect_read_only", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "db_read_only_connect_failed", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc

    try:
        if not _table_exists(con, TABLE_NAME):
            doc["queue"] = {"ok": False, "reason": "gap_focus_evidence_queue_missing"}
            doc["summary"] = {"ok": False, "blocked_reason": "queue_table_missing", "dt_sec": round(time.time() - start, 3), "state_written": False}
            return doc

        validated: List[Dict[str, Any]] = []
        queue_missing = 0
        policy_basis_counts: Dict[str, int] = {}
        capability_context = build_replay_evidence_capability_context(
            con, schemas=capability_schemas, scan_limit=capability_scan_limit
        )
        for item in source_items:
            sig = _safe_str(item.get("request_signature"), 256)
            row = _queue_row_by_signature(con, sig)
            if row is None:
                queue_missing += 1
                current = {"available": False, "reason": "queue_row_missing", "rule_count": 0, "total_n": 0}
            else:
                current = _current_policy_evidence(con, _safe_str(row["namespace"], 160), _safe_str(row["state_hash"], 4000))
            capability: Dict[str, Any] = {}
            if row is not None and _safe_str(row["target"], 80) == "replay":
                schema = state_schema_from_hash(_safe_str(row["state_hash"], 4000))
                if schema in capability_schemas:
                    capability = check_replay_evidence_capability(
                        con,
                        namespace=_safe_str(row["namespace"], 160),
                        state_schema=schema,
                        state_hash=_safe_str(row["state_hash"], 4000),
                        action=_safe_str(row["primary_action"], 160),
                        scan_limit=capability_scan_limit,
                        context=capability_context,
                    )
            out = _validate_item(item, row, current, min_s, min_rc, min_n, capability)
            policy_basis_counts[out.get("policy_validation_basis", "unknown")] = policy_basis_counts.get(out.get("policy_validation_basis", "unknown"), 0) + 1
            validated.append(out)

        buckets_doc = _bucket_map(validated, top_n)
        per_bucket_counts = {k: int(v.get("count_total", 0)) for k, v in buckets_doc.items()}
        validated_total = sum(v for k, v in per_bucket_counts.items() if k != "blocked_or_insufficient")
        blocked_total = int(per_bucket_counts.get("blocked_or_insufficient", 0))
        capability_checked = sum(1 for item in validated if item.get("replay_evidence_capability"))
        capability_available = sum(1 for item in validated if isinstance(item.get("replay_evidence_capability"), dict) and item.get("replay_evidence_capability", {}).get("available"))
        capability_block_reasons: Dict[str, int] = {}
        for item in validated:
            cap = item.get("replay_evidence_capability") if isinstance(item.get("replay_evidence_capability"), dict) else {}
            reason = str(cap.get("blocked_reason") or "")
            if cap and reason:
                capability_block_reasons[reason] = capability_block_reasons.get(reason, 0) + 1
        capability_matching_steps_total = 0
        capability_direct_outcome_steps_total = 0
        capability_matching_snapchains_total = 0
        capability_scan_snapchains_total = 0
        capability_scan_steps_total = 0
        capability_decode_errors_total = 0
        capability_dt_ms_total = 0.0
        capability_near_miss_candidates = 0
        capability_conflict_candidates = 0
        capability_outcome_class_counts: Dict[str, int] = {}
        capability_source_kind_counts: Dict[str, int] = {}
        capability_targeted_valid_total = 0
        capability_targeted_invalid_total = 0
        capability_historical_usable_total = 0
        for item in validated:
            cap = item.get("replay_evidence_capability") if isinstance(item.get("replay_evidence_capability"), dict) else {}
            if not cap:
                continue
            matching_steps = _as_int(cap.get("matching_steps_total"), 0)
            direct_steps = _as_int(cap.get("matching_steps_with_direct_outcome", cap.get("matching_direct_outcomes_total")), 0)
            capability_matching_steps_total += matching_steps
            capability_direct_outcome_steps_total += direct_steps
            capability_matching_snapchains_total += _as_int(cap.get("matching_snapchains_total"), 0)
            capability_scan_snapchains_total += _as_int(cap.get("snapchains_scanned"), 0)
            capability_scan_steps_total += _as_int(cap.get("steps_scanned_total"), 0)
            capability_decode_errors_total += _as_int(cap.get("decode_errors"), 0)
            capability_dt_ms_total += _as_float(cap.get("capability_dt_ms"), 0.0)
            if matching_steps > 0 and direct_steps == 0:
                capability_near_miss_candidates += 1
            if str(cap.get("blocked_reason") or "") == "replay_direct_evidence_conflict":
                capability_conflict_candidates += 1
            source_kind = str(cap.get("selected_source_kind") or cap.get("source_kind") or "")
            if source_kind:
                capability_source_kind_counts[source_kind] = capability_source_kind_counts.get(source_kind, 0) + 1
            capability_targeted_valid_total += _as_int(cap.get("targeted_valid_total"), 0)
            capability_targeted_invalid_total += _as_int(cap.get("targeted_invalid_total"), 0)
            capability_historical_usable_total += _as_int(cap.get("historical_usable_total"), 0)
            classes = cap.get("matching_outcome_classes") if isinstance(cap.get("matching_outcome_classes"), list) else []
            for outcome_class in classes:
                key = str(outcome_class)
                capability_outcome_class_counts[key] = capability_outcome_class_counts.get(key, 0) + 1
        status_counts = [dict(r) for r in con.execute(f"SELECT target, status, COUNT(*) AS count FROM {TABLE_NAME} GROUP BY target, status ORDER BY target, status").fetchall()]
        total_queue = con.execute(f"SELECT COUNT(*) AS n FROM {TABLE_NAME}").fetchone()["n"]

        doc["ok"] = True
        doc["queue"] = {
            "ok": True,
            "table": TABLE_NAME,
            "rows_total": int(total_queue),
            "rows_checked": len(source_items),
            "missing_for_source_items": queue_missing,
            "status_counts": status_counts,
        }
        doc["validation"] = buckets_doc
        doc["summary"] = {
            "ok": True,
            "dt_sec": round(time.time() - start, 3),
            "source_items_loaded": len(source_items),
            "queue_rows_total": int(total_queue),
            "queue_rows_checked": len(source_items),
            "queue_missing": queue_missing,
            "validated_total": validated_total,
            "blocked_total": blocked_total,
            "per_bucket_counts": per_bucket_counts,
            "policy_basis_counts": policy_basis_counts,
            "replay_capability_checked": capability_checked,
            "replay_capability_available": capability_available,
            "replay_capability_block_reason_counts": capability_block_reasons,
            "replay_capability_matching_steps_total": capability_matching_steps_total,
            "replay_capability_direct_outcome_steps_total": capability_direct_outcome_steps_total,
            "replay_capability_matching_snapchains_total": capability_matching_snapchains_total,
            "replay_capability_scan_snapchains_total": capability_scan_snapchains_total,
            "replay_capability_scan_steps_total": capability_scan_steps_total,
            "replay_capability_decode_errors_total": capability_decode_errors_total,
            "replay_capability_dt_ms_total": round(capability_dt_ms_total, 3),
            "replay_capability_near_miss_candidates": capability_near_miss_candidates,
            "replay_capability_conflict_candidates": capability_conflict_candidates,
            "replay_capability_outcome_class_counts": capability_outcome_class_counts,
            "replay_capability_source_kind_counts": capability_source_kind_counts,
            "replay_capability_targeted_valid_total": capability_targeted_valid_total,
            "replay_capability_targeted_invalid_total": capability_targeted_invalid_total,
            "replay_capability_historical_usable_total": capability_historical_usable_total,
            "replay_capability_shared_scan_historical_snapchains": _as_int(capability_context.get("historical_snapchains_scanned"), 0),
            "replay_capability_shared_scan_targeted_snapchains": _as_int(capability_context.get("targeted_snapchains_scanned"), 0),
            "replay_capability_shared_scan_targeted_candidates": _as_int(capability_context.get("targeted_candidates_indexed"), 0),
            "replay_capability_shared_scan_used": True,
            "replay_capability_shared_scan_snapchains": _as_int(capability_context.get("snapchains_scanned"), 0),
            "replay_capability_shared_scan_steps": _as_int(capability_context.get("steps_scanned_total"), 0),
            "replay_capability_shared_scan_decode_errors": _as_int(capability_context.get("decode_errors"), 0),
            "replay_capability_shared_scan_build_dt_ms": _as_float(capability_context.get("build_dt_ms"), 0.0),
            "source_age_sec": age,
            "source_stale": stale,
            "db_writes": 0,
            "policy_writes": 0,
            "runner_starts": 0,
            "replay_starts": 0,
            "dream_starts": 0,
            "execution_starts": 0,
            "state_written": False,
        }
        return doc
    except Exception as exc:
        doc["errors"].append({"where": "validation_build", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "validation_build_failed", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc
    finally:
        try:
            con.close()
        except Exception:
            pass


def _apply_oroma_state_ownership(path: Path) -> None:
    """Best-effort: root-manual runs should not leave state JSON root-owned."""
    if os.geteuid() != 0:
        return
    try:
        uid = pwd.getpwnam("oroma").pw_uid
        gid = grp.getgrnam("oroma").gr_gid
        os.chown(str(path), uid, gid)
    except Exception:
        return


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    p = path.resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(p))
    try:
        os.chmod(str(p), 0o664)
    except Exception:
        pass
    _apply_oroma_state_ownership(p)


def write_state(doc: Mapping[str, Any], state_path: Optional[Path] = None) -> Path:
    base = _base_dir()
    out_path = (state_path or default_state_path(base)).resolve()
    data = dict(doc)
    data.setdefault("state_path", str(out_path))
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    summary = dict(summary)
    summary["state_written"] = True
    data["summary"] = summary
    atomic_write_json(out_path, data)
    return out_path


__all__ = [
    "VERSION",
    "DEFAULT_BUCKETS",
    "TABLE_NAME",
    "DEFAULT_REPLAY_CAPABILITY_SCHEMAS",
    "build_evidence_validation",
    "write_state",
    "default_db_path",
    "default_source_path",
    "default_state_path",
    "parse_csv",
]

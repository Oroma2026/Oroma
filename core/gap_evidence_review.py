#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_evidence_review.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Queue Review · Read-Only Dry-Run
# Version:   v0.1.0-read-only-review
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die naechste bewusst konservative Stufe nach dem ersten
# kontrollierten Gap-Write:
#
#   knowledge_gaps
#       -> gap_learning_focus.json
#       -> gap_focus_consumer.json
#       -> gap_focus_shadow_plan.json
#       -> gap_focus_evidence_queue          (DBWriter-only Queue-Write)
#       -> gap_evidence_review.json          (dieses Modul, read-only)
#
# Die Review-Stufe liest die Queue-Tabelle read-only, gleicht die zugehoerigen
# Policy-Evidence-Snapshots gegen den aktuellen policy_rules-Stand ab und erzeugt
# eine maschinenlesbare Review-Sicht. Sie entscheidet NICHT final ueber Policy-
# Writes. Sie markiert nur, welche Queue-Eintraege fuer spaetere getrennte Gates
# geeignet erscheinen:
#
#   - ready_for_replay_review
#   - ready_for_dream_review
#   - runner_priority_hint
#   - explore_plan_candidate
#   - blocked_or_insufficient
#
# WARUM NOCH KEIN POLICY-WRITE?
# -----------------------------
# Queue-Zeilen sind auditierbare Lernbedarfs-/Evidence-Anfragen, aber weiterhin
# keine Direct-Step-Credit-Evidenz. Selbst wenn ein Gap logisch plausibel ist,
# darf er nicht direkt policy_rules veraendern. Zwischen Review und Policy-Write
# muessen noch mindestens Evidence-Erzeugung/-Validierung und ein separates
# Promotion-Gate liegen.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhaengigkeiten.
# - SQLite nur read-only via URI mode=ro; keine DBWriter-Nutzung, weil kein Write.
# - Keine DB-Writes, keine Schemaaenderungen, keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - State-Write nur atomar nach data/state/gap_evidence_review.json.
# - Fail-soft: blockierte/fehlende Daten werden sichtbar im JSON dokumentiert.
# - iPhone-/SSH-freundliche CLI wird in tools/gap_evidence_review.py angeboten.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_EVIDENCE_REVIEW_STATE_PATH=/opt/ai/oroma/data/state/gap_evidence_review.json
#   OROMA_GAP_EVIDENCE_REVIEW_TARGETS=explore,replay,dream,runner_priority
#   OROMA_GAP_EVIDENCE_REVIEW_STATUSES=queued
#   OROMA_GAP_EVIDENCE_REVIEW_LIMIT=200
#   OROMA_GAP_EVIDENCE_REVIEW_TOPK=10
#   OROMA_GAP_EVIDENCE_REVIEW_MIN_SCORE=0.0
#   OROMA_GAP_EVIDENCE_REVIEW_REQUIRE_POLICY_RULE=1
#   OROMA_GAP_EVIDENCE_REVIEW_COVERED_MIN_N=5
#   OROMA_GAP_EVIDENCE_REVIEW_UNCERTAINTY_EPS=0.05
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

VERSION = "v0.1.0-read-only-review"
DEFAULT_STATE_NAME = "gap_evidence_review.json"
DEFAULT_TARGETS = ("explore", "replay", "dream", "runner_priority")
DEFAULT_STATUSES = ("queued",)
TABLE_NAME = "gap_focus_evidence_queue"


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


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_REVIEW_STATE_PATH")
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


def _policy_snapshot_from_meta(meta: Mapping[str, Any]) -> Dict[str, Any]:
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


def _current_policy_evidence(con: sqlite3.Connection, namespace: str, state_hash: str) -> Dict[str, Any]:
    if not namespace or not state_hash or not _table_exists(con, "policy_rules"):
        return {"available": False, "reason": "policy_rules_missing_or_identity_incomplete"}
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


def _row_to_item(row: sqlite3.Row, current_policy: Mapping[str, Any], min_score: float, covered_min_n: int, uncertainty_eps: float, require_policy_rule: bool) -> Dict[str, Any]:
    meta = _json_loads(row["meta_json"])
    meta_pe = _policy_snapshot_from_meta(meta)
    target = _safe_str(row["target"], 80)
    namespace = _safe_str(row["namespace"], 160)
    state_hash = _safe_str(row["state_hash"], 4000)
    primary_action = _safe_str(row["primary_action"], 160)
    status = _safe_str(row["status"], 80)
    score = _as_float(row["score"], 0.0)

    blocked_reasons: List[str] = []
    if status != "queued":
        blocked_reasons.append("status_not_queued")
    if not namespace or not state_hash:
        blocked_reasons.append("missing_namespace_or_state_hash")
    if not primary_action:
        blocked_reasons.append("missing_primary_action")
    if score < float(min_score):
        blocked_reasons.append("score_below_min")
    current_rule_count = _as_int(current_policy.get("rule_count"), 0)
    meta_rule_count = _as_int(meta_pe.get("rule_count"), 0)
    if require_policy_rule and current_rule_count <= 0 and meta_rule_count <= 0:
        blocked_reasons.append("no_policy_evidence")

    # Prefer current policy_rules evidence when available, but keep the
    # queue-time policy snapshot as a valid review basis. The snapshot was
    # captured when the Shadow Plan was queued and is important when the DB
    # backup or live policy table has already moved on.
    policy_basis = current_policy if current_rule_count > 0 else meta_pe
    total_n = _as_int(policy_basis.get("total_n"), 0)
    q_gap = policy_basis.get("q_gap")
    q_gap_num = None if q_gap is None else _as_float(q_gap, 0.0)
    enough_current_evidence = total_n >= int(covered_min_n)
    still_uncertain = bool(q_gap_num is None or q_gap_num <= float(uncertainty_eps))

    if blocked_reasons:
        review_bucket = "blocked_or_insufficient"
        review_reason = ",".join(blocked_reasons)
    elif target == "replay":
        review_bucket = "ready_for_replay_review"
        review_reason = "queued_replay_candidate_needs_existing_evidence_review"
    elif target == "dream":
        review_bucket = "ready_for_dream_review"
        review_reason = "queued_dream_candidate_needs_shadow_or_ledger_review"
    elif target == "runner_priority":
        review_bucket = "runner_priority_hint"
        review_reason = "queued_runner_priority_hint_only"
    elif target == "explore":
        review_bucket = "explore_plan_candidate"
        review_reason = "queued_explore_candidate_needs_scheduler_gate"
    else:
        review_bucket = "blocked_or_insufficient"
        review_reason = "unknown_target"

    if review_bucket != "blocked_or_insufficient" and not still_uncertain and enough_current_evidence:
        # If the policy state no longer looks uncertain, keep it visible but do
        # not promote it. A separate closer may later mark it resolved.
        review_bucket = "blocked_or_insufficient"
        review_reason = "appears_covered_by_current_policy_evidence"

    return {
        "id": _as_int(row["id"], 0),
        "request_signature": _safe_str(row["request_signature"], 128),
        "plan_id": _safe_str(row["plan_id"], 160),
        "focus_id": _safe_str(row["focus_id"], 160),
        "target": target,
        "namespace": namespace,
        "state_hash": state_hash,
        "primary_action": primary_action,
        "kind": _safe_str(row["kind"], 80),
        "reason": _safe_str(row["reason"], 160),
        "recommended_next": _safe_str(row["recommended_next"], 160),
        "score": score,
        "status": status,
        "created_ts": _as_int(row["created_ts"], 0),
        "updated_ts": _as_int(row["updated_ts"], 0),
        "attempts": _as_int(row["attempts"], 0),
        "review_bucket": review_bucket,
        "review_reason": review_reason,
        "blocked_reasons": blocked_reasons,
        "policy_review_basis": "current_policy_rules" if current_rule_count > 0 else "queue_meta_snapshot",
        "meta_policy_evidence": meta_pe,
        "current_policy_evidence": dict(current_policy),
        "execution": {
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_policy": False,
            "write_db": False,
            "review_only": True,
        },
    }


def _limited_bucket_map(items: Iterable[Mapping[str, Any]], topk: int) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {
        "ready_for_replay_review": {"count_total": 0, "count_returned": 0, "items": []},
        "ready_for_dream_review": {"count_total": 0, "count_returned": 0, "items": []},
        "runner_priority_hint": {"count_total": 0, "count_returned": 0, "items": []},
        "explore_plan_candidate": {"count_total": 0, "count_returned": 0, "items": []},
        "blocked_or_insufficient": {"count_total": 0, "count_returned": 0, "items": []},
    }
    for item in items:
        bucket_name = str(item.get("review_bucket") or "blocked_or_insufficient")
        if bucket_name not in buckets:
            bucket_name = "blocked_or_insufficient"
        b = buckets[bucket_name]
        b["count_total"] = int(b.get("count_total", 0)) + 1
        if len(b.get("items", [])) < int(topk):
            b["items"].append(dict(item))
    for b in buckets.values():
        b["count_returned"] = len(b.get("items", []))
        b["execution"] = {
            "review_only": True,
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_policy": False,
            "write_db": False,
        }
    return buckets


def build_evidence_review(
    *,
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    targets: Optional[Sequence[str]] = None,
    statuses: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    min_score: Optional[float] = None,
    covered_min_n: Optional[int] = None,
    uncertainty_eps: Optional[float] = None,
    require_policy_rule: Optional[bool] = None,
) -> Dict[str, Any]:
    start = time.time()
    base = _base_dir()
    dbp = (db_path or default_db_path(base)).resolve()
    out_path = (state_path or default_state_path(base)).resolve()
    target_list = list(targets or parse_csv(_env_str("OROMA_GAP_EVIDENCE_REVIEW_TARGETS", ",".join(DEFAULT_TARGETS)), DEFAULT_TARGETS))
    status_list = list(statuses or parse_csv(_env_str("OROMA_GAP_EVIDENCE_REVIEW_STATUSES", ",".join(DEFAULT_STATUSES)), DEFAULT_STATUSES))
    target_list = [t for t in target_list if t]
    status_list = [s for s in status_list if s]
    row_limit = max(1, int(limit if limit is not None else _env_int("OROMA_GAP_EVIDENCE_REVIEW_LIMIT", 200)))
    top_n = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_EVIDENCE_REVIEW_TOPK", 10)))
    min_s = float(min_score if min_score is not None else _env_float("OROMA_GAP_EVIDENCE_REVIEW_MIN_SCORE", 0.0))
    min_n = int(covered_min_n if covered_min_n is not None else _env_int("OROMA_GAP_EVIDENCE_REVIEW_COVERED_MIN_N", 5))
    eps = float(uncertainty_eps if uncertainty_eps is not None else _env_float("OROMA_GAP_EVIDENCE_REVIEW_UNCERTAINTY_EPS", 0.05))
    need_policy = bool(require_policy_rule if require_policy_rule is not None else _env_bool("OROMA_GAP_EVIDENCE_REVIEW_REQUIRE_POLICY_RULE", True))

    now = _now_ts()
    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "read_only_gap_evidence_review",
        "base": str(base),
        "db_path": str(dbp),
        "state_path": str(out_path),
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "config": {
            "targets": target_list,
            "statuses": status_list,
            "limit": row_limit,
            "topk": top_n,
            "min_score": min_s,
            "covered_min_n": min_n,
            "uncertainty_eps": eps,
            "require_policy_rule": need_policy,
        },
        "safety": {
            "db_open_mode": "read_only_uri_mode_ro",
            "db_writes": False,
            "policy_writes": False,
            "rules_writes": False,
            "schema_changes": False,
            "runner_starts": False,
            "replay_starts": False,
            "dream_starts": False,
            "state_json_write": True,
        },
        "queue": {},
        "review": {},
        "summary": {},
        "errors": [],
    }

    try:
        con = _connect_ro(dbp)
    except Exception as exc:
        doc["errors"].append({"where": "sqlite_connect_read_only", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "db_read_only_connect_failed", "dt_sec": round(time.time() - start, 3)}
        return doc

    try:
        if not _table_exists(con, TABLE_NAME):
            doc["queue"] = {"ok": False, "reason": "gap_focus_evidence_queue_missing"}
            doc["summary"] = {"ok": False, "blocked_reason": "queue_table_missing", "dt_sec": round(time.time() - start, 3)}
            return doc

        placeholders_t = ",".join("?" for _ in target_list)
        placeholders_s = ",".join("?" for _ in status_list)
        params: List[Any] = list(target_list) + list(status_list) + [row_limit]
        sql = (
            "SELECT id, request_signature, plan_id, focus_id, target, namespace, state_hash, "
            "primary_action, kind, reason, recommended_next, score, status, source_plan_ts, "
            "created_ts, updated_ts, attempts, meta_json "
            f"FROM {TABLE_NAME} WHERE target IN ({placeholders_t}) AND status IN ({placeholders_s}) "
            "ORDER BY score DESC, updated_ts DESC, id DESC LIMIT ?"
        )
        rows = con.execute(sql, params).fetchall()

        all_count = con.execute(f"SELECT COUNT(*) AS n FROM {TABLE_NAME}").fetchone()["n"]
        status_counts = [dict(r) for r in con.execute(f"SELECT target, status, COUNT(*) AS count FROM {TABLE_NAME} GROUP BY target, status ORDER BY target, status").fetchall()]

        reviewed: List[Dict[str, Any]] = []
        for row in rows:
            current_policy = _current_policy_evidence(con, _safe_str(row["namespace"], 160), _safe_str(row["state_hash"], 4000))
            reviewed.append(_row_to_item(row, current_policy, min_s, min_n, eps, need_policy))

        buckets = _limited_bucket_map(reviewed, top_n)
        per_bucket_counts = {k: int(v.get("count_total", 0)) for k, v in buckets.items()}
        ready_total = int(per_bucket_counts.get("ready_for_replay_review", 0)) + int(per_bucket_counts.get("ready_for_dream_review", 0)) + int(per_bucket_counts.get("explore_plan_candidate", 0)) + int(per_bucket_counts.get("runner_priority_hint", 0))

        doc["ok"] = True
        doc["queue"] = {
            "ok": True,
            "table": TABLE_NAME,
            "rows_total": int(all_count),
            "rows_loaded": len(rows),
            "status_counts": status_counts,
        }
        doc["review"] = buckets
        doc["summary"] = {
            "ok": True,
            "dt_sec": round(time.time() - start, 3),
            "queue_rows_total": int(all_count),
            "queue_rows_loaded": len(rows),
            "ready_total": ready_total,
            "blocked_total": int(per_bucket_counts.get("blocked_or_insufficient", 0)),
            "per_bucket_counts": per_bucket_counts,
            "db_writes": 0,
            "policy_writes": 0,
            "runner_starts": 0,
            "replay_starts": 0,
            "dream_starts": 0,
            "state_written": False,
        }
        return doc
    except Exception as exc:
        doc["errors"].append({"where": "review_build", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "review_build_failed", "dt_sec": round(time.time() - start, 3)}
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
    "TABLE_NAME",
    "DEFAULT_TARGETS",
    "DEFAULT_STATUSES",
    "build_evidence_review",
    "write_state",
    "default_db_path",
    "default_state_path",
    "parse_csv",
]

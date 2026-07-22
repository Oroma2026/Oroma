#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_evidence_outcome.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Outcome Collector · Read-Only · No Starts · No Policy
# Version:   v0.1.0-read-only-outcome-collector
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die fachliche Beweis-Suchstufe vor dem Gap Policy
# Mini-Write-Gate. Die bisherige Kette lautet:
#
#   knowledge_gaps
#       -> gap_learning_focus.json
#       -> gap_focus_consumer.json
#       -> gap_focus_shadow_plan.json
#       -> gap_focus_evidence_queue              (DBWriter-only Queue)
#       -> gap_evidence_review.json              (read-only Review)
#       -> gap_evidence_validation.json          (read-only Validation)
#       -> gap_policy_promotion_queue            (DBWriter-only Promotion)
#       -> gap_evidence_outcomes.json            (dieses Modul, read-only)
#       -> gap_policy_mini_write_gate.json       (fail-closed Policy-Gate)
#
# Dieses Modul schließt Lücken NICHT durch Wahrscheinlichkeit. Es sucht nur nach
# bereits vorhandenen, direkten Evidence-Outcomes für Promotion-Kandidaten:
#
#   namespace + state_hash + action -> reward/outcome aus rewards_log,
#                                      episode_events oder auditierbaren Ledgers
#
# Reine policy_rules-Statistik wird absichtlich nur als policy_snapshot_only
# ausgewiesen. Sie darf nicht als neuer Beweis für einen weiteren Policy-Write
# verwendet werden, weil sie bereits das alte Modellwissen ist und sonst ein
# zirkulärer Lernloop entstünde.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhängigkeiten.
# - SQLite nur read-only via URI mode=ro; kein DBWriter, weil kein Write.
# - Keine DB-Writes, keine Schemaänderungen, keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Kein lokaler SQLite-Schreib-Fallback.
# - State-Write nur atomar nach data/state/gap_evidence_outcomes.json.
# - Root-Manual-Läufe setzen die State-Datei best-effort auf oroma:oroma 664.
# - Fail-soft: fehlende direkte Evidence wird sichtbar als missing_direct_outcome
#   dokumentiert, nicht verschwiegen und nicht geraten.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_EVIDENCE_OUTCOME_STATE_PATH=/opt/ai/oroma/data/state/gap_evidence_outcomes.json
#   OROMA_GAP_EVIDENCE_OUTCOME_LIMIT=50
#   OROMA_GAP_EVIDENCE_OUTCOME_TOPK=10
#   OROMA_GAP_EVIDENCE_OUTCOME_BUCKETS=promotion_candidate_replay,promotion_candidate_dream
#   OROMA_GAP_EVIDENCE_OUTCOME_REWARD_EPS=0.000001
#   OROMA_GAP_EVIDENCE_OUTCOME_LOOKBACK_SEC=1209600
# =============================================================================

from __future__ import annotations

import fnmatch
import json
import os
import pwd
import grp
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "v0.1.0-read-only-outcome-collector"
PROMOTION_TABLE = "gap_policy_promotion_queue"
POLICY_TABLE = "policy_rules"
DEFAULT_STATE_NAME = "gap_evidence_outcomes.json"
DEFAULT_BUCKETS = ("promotion_candidate_replay", "promotion_candidate_dream")


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


def _json_loads(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def default_db_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "oroma.db").resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_OUTCOME_STATE_PATH")
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


def _apply_oroma_state_ownership(path: Path) -> None:
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
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(str(tmp), str(p))
    try:
        os.chmod(str(p), 0o664)
    except Exception:
        pass
    _apply_oroma_state_ownership(p)


def _normalize_outcome_from_reward(value: float, eps: float) -> str:
    if value > float(eps):
        return "pos"
    if value < -float(eps):
        return "neg"
    return "draw"


def _policy_snapshot(con: sqlite3.Connection, namespace: str, state_hash: str, action: str) -> Dict[str, Any]:
    if not namespace or not state_hash or not action or not _table_exists(con, POLICY_TABLE):
        return {"available": False, "reason": "identity_incomplete_or_policy_missing"}
    row = con.execute(
        f"SELECT id,n,pos,neg,draw,q,last_ts FROM {POLICY_TABLE} WHERE namespace=? AND state_hash=? AND action=? LIMIT 1",
        (namespace, state_hash, action),
    ).fetchone()
    if not row:
        return {"available": False, "reason": "policy_action_missing"}
    return {
        "available": True,
        "id": _as_int(row["id"], 0),
        "n": _as_int(row["n"], 0),
        "pos": _as_int(row["pos"], 0),
        "neg": _as_int(row["neg"], 0),
        "draw": _as_int(row["draw"], 0),
        "q": _as_float(row["q"], 0.0),
        "last_ts": _as_int(row["last_ts"], 0),
        "usable_as_new_outcome": False,
        "reason": "policy_snapshot_is_not_new_evidence",
    }


def _flatten_values(obj: Any, depth: int = 0) -> List[Any]:
    if depth > 7:
        return []
    if isinstance(obj, dict):
        vals: List[Any] = []
        for v in obj.values():
            vals.extend(_flatten_values(v, depth + 1))
        return vals
    if isinstance(obj, list):
        vals = []
        for v in obj:
            vals.extend(_flatten_values(v, depth + 1))
        return vals
    return [obj]


def _dict_get_deep(obj: Any, keys: Sequence[str]) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in keys:
                found.append(v)
            found.extend(_dict_get_deep(v, keys))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_dict_get_deep(v, keys))
    return found


def _raw_contains_identity(raw: str, state_hash: str, action: str, namespace: str = "") -> bool:
    if not raw or not state_hash or state_hash not in raw:
        return False
    # Action wird nicht nur per String-Contains geprüft, weil kurze Actions wie
    # "0" überall vorkommen können. Der JSON-Pfad unten ist die eigentliche
    # Absicherung. Diese Funktion ist nur ein schneller Vorfilter.
    if namespace and namespace in raw:
        return True
    return True


def _json_matches_identity(raw: str, namespace: str, state_hash: str, action: str) -> bool:
    data = _json_loads(raw)
    if not data:
        return False
    values = [str(v) for v in _flatten_values(data)]
    if state_hash and state_hash not in values and state_hash not in raw:
        return False
    ns_values = [str(v) for v in _dict_get_deep(data, ("namespace", "ns", "game_namespace", "policy_namespace"))]
    if namespace and ns_values and namespace not in ns_values:
        return False
    action_values = [str(v) for v in _dict_get_deep(data, ("action", "policy_action", "executed_action", "proposed_action", "primary_action"))]
    if action and action_values and action not in action_values:
        return False
    if action and not action_values:
        # Ohne explizites Aktionsfeld ist ein Match fuer Policy-Outcome zu riskant.
        return False
    return True


def _find_rewards_log_outcomes(con: sqlite3.Connection, namespace: str, state_hash: str, action: str, lookback_ts: int, eps: float, limit: int = 5) -> List[Dict[str, Any]]:
    if not _table_exists(con, "rewards_log") or not state_hash or not action:
        return []
    rows = con.execute(
        "SELECT id, created_at, source, episode_id, step, reward, raw, tag FROM rewards_log "
        "WHERE created_at>=? AND raw LIKE ? ORDER BY created_at DESC LIMIT 200",
        (int(lookback_ts), f"%{state_hash}%"),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        raw = _safe_str(r["raw"], 20000)
        if not _raw_contains_identity(raw, state_hash, action, namespace):
            continue
        if not _json_matches_identity(raw, namespace, state_hash, action):
            continue
        reward = _as_float(r["reward"], 0.0)
        outcome = _normalize_outcome_from_reward(reward, eps)
        out.append({
            "source_table": "rewards_log",
            "source_id": _as_int(r["id"], 0),
            "created_at": _as_int(r["created_at"], 0),
            "source": _safe_str(r["source"], 160),
            "tag": _safe_str(r["tag"], 160),
            "episode_id": _as_int(r["episode_id"], 0) if r["episode_id"] is not None else None,
            "step": _as_int(r["step"], 0) if r["step"] is not None else None,
            "reward": reward,
            "outcome": outcome,
            "usable_for_policy_write": True,
            "match_basis": "exact_state_hash_and_json_action",
        })
        if len(out) >= int(limit):
            break
    return out


def _find_episode_event_outcomes(con: sqlite3.Connection, namespace: str, state_hash: str, action: str, lookback_ts: int, eps: float, limit: int = 5) -> List[Dict[str, Any]]:
    if not _table_exists(con, "episode_events") or not state_hash or not action:
        return []
    rows = con.execute(
        "SELECT id, episode_id, ts, event_type, ref_table, ref_id, meta_json, state_hash, reward "
        "FROM episode_events WHERE ts>=? AND state_hash=? AND reward IS NOT NULL ORDER BY ts DESC LIMIT 200",
        (int(lookback_ts), state_hash),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        meta = _safe_str(r["meta_json"], 20000)
        if not _json_matches_identity(meta, namespace, state_hash, action):
            continue
        reward = _as_float(r["reward"], 0.0)
        outcome = _normalize_outcome_from_reward(reward, eps)
        out.append({
            "source_table": "episode_events",
            "source_id": _as_int(r["id"], 0),
            "episode_id": _as_int(r["episode_id"], 0),
            "created_at": _as_int(r["ts"], 0),
            "event_type": _safe_str(r["event_type"], 120),
            "reward": reward,
            "outcome": outcome,
            "usable_for_policy_write": True,
            "match_basis": "exact_episode_state_hash_and_json_action",
        })
        if len(out) >= int(limit):
            break
    return out


def _find_historical_ledger(con: sqlite3.Connection, namespace: str, state_hash: str, action: str, limit: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if _table_exists(con, "dream_policy_mini_write_ledger"):
        rows = con.execute(
            "SELECT id,evidence_signature,namespace,state_hash,action,direct_positive,direct_negative,direct_zero,policy_q_before,policy_q_after,source,status,created_ts "
            "FROM dream_policy_mini_write_ledger WHERE namespace=? AND state_hash=? AND action=? ORDER BY created_ts DESC LIMIT ?",
            (namespace, state_hash, action, int(limit)),
        ).fetchall()
        for r in rows:
            if _as_int(r["direct_positive"], 0) > 0:
                outcome = "pos"
            elif _as_int(r["direct_negative"], 0) > 0:
                outcome = "neg"
            elif _as_int(r["direct_zero"], 0) > 0:
                outcome = "draw"
            else:
                outcome = "unknown"
            out.append({
                "source_table": "dream_policy_mini_write_ledger",
                "source_id": _as_int(r["id"], 0),
                "created_at": _as_int(r["created_ts"], 0),
                "source": _safe_str(r["source"], 160),
                "status": _safe_str(r["status"], 80),
                "outcome": outcome,
                "usable_for_policy_write": False,
                "reason": "already_applied_historical_policy_ledger",
            })
    if _table_exists(con, "gap_policy_mini_write_ledger"):
        rows = con.execute(
            "SELECT id,write_signature,promotion_signature,namespace,state_hash,action,outcome,status,policy_written,created_ts "
            "FROM gap_policy_mini_write_ledger WHERE namespace=? AND state_hash=? AND action=? ORDER BY created_ts DESC LIMIT ?",
            (namespace, state_hash, action, int(limit)),
        ).fetchall()
        for r in rows:
            out.append({
                "source_table": "gap_policy_mini_write_ledger",
                "source_id": _as_int(r["id"], 0),
                "created_at": _as_int(r["created_ts"], 0),
                "status": _safe_str(r["status"], 80),
                "policy_written": bool(_as_int(r["policy_written"], 0)),
                "outcome": _safe_str(r["outcome"], 40),
                "usable_for_policy_write": False,
                "reason": "existing_gap_policy_gate_ledger_not_new_evidence",
            })
    return out


def _load_promotion_rows(con: sqlite3.Connection, buckets: Sequence[str], limit: int) -> Tuple[List[sqlite3.Row], List[str]]:
    if not _table_exists(con, PROMOTION_TABLE):
        return [], ["promotion_table_missing"]
    wanted = [b for b in buckets if b] or list(DEFAULT_BUCKETS)
    placeholders = ",".join("?" for _ in wanted)
    rows = list(con.execute(
        f"SELECT * FROM {PROMOTION_TABLE} WHERE status IN ('promotion_review','approved_for_policy_gate') AND promotion_bucket IN ({placeholders}) ORDER BY score DESC, updated_ts DESC, id ASC LIMIT ?",
        [*wanted, int(limit)],
    ).fetchall())
    return rows, []


def _classify_row(con: sqlite3.Connection, row: sqlite3.Row, lookback_ts: int, eps: float) -> Dict[str, Any]:
    namespace = _safe_str(row["namespace"], 160)
    state_hash = _safe_str(row["state_hash"], 4000)
    action = _safe_str(row["primary_action"], 160)
    meta = _json_loads(row["meta_json"])
    direct: List[Dict[str, Any]] = []
    direct.extend(_find_rewards_log_outcomes(con, namespace, state_hash, action, lookback_ts, eps))
    direct.extend(_find_episode_event_outcomes(con, namespace, state_hash, action, lookback_ts, eps))
    historical = _find_historical_ledger(con, namespace, state_hash, action)
    policy = _policy_snapshot(con, namespace, state_hash, action)

    usable = [d for d in direct if d.get("usable_for_policy_write")]
    outcomes = sorted({str(d.get("outcome")) for d in usable if d.get("outcome") in ("pos", "neg", "draw")})
    if len(outcomes) == 1:
        bucket = "outcome_ready_" + outcomes[0]
        reason = "direct_outcome_found"
        chosen = usable[0]
    elif len(outcomes) > 1:
        bucket = "ambiguous_outcome"
        reason = "conflicting_direct_outcomes"
        chosen = None
    elif historical:
        bucket = "historical_policy_ledger_only"
        reason = "historical_ledger_not_new_evidence"
        chosen = None
    elif policy.get("available"):
        bucket = "policy_snapshot_only"
        reason = "policy_rules_snapshot_is_not_new_outcome"
        chosen = None
    else:
        bucket = "missing_direct_outcome"
        reason = "no_matching_reward_episode_or_ledger_outcome"
        chosen = None

    return {
        "promotion_id": _as_int(row["id"], 0),
        "promotion_signature": _safe_str(row["promotion_signature"], 160),
        "request_signature": _safe_str(row["request_signature"], 160),
        "target": _safe_str(row["target"], 80),
        "promotion_bucket": _safe_str(row["promotion_bucket"], 120),
        "namespace": namespace,
        "state_hash": state_hash,
        "action": action,
        "score": _as_float(row["score"], 0.0),
        "status": _safe_str(row["status"], 80),
        "policy_write_allowed": False,
        "outcome_bucket": bucket,
        "outcome_reason": reason,
        "selected_outcome": chosen.get("outcome") if isinstance(chosen, dict) else None,
        "selected_evidence": chosen if isinstance(chosen, dict) else None,
        "direct_evidence_count": len(direct),
        "historical_ledger_count": len(historical),
        "policy_snapshot": policy,
        "direct_evidence_examples": direct[:3],
        "historical_ledger_examples": historical[:3],
        "source_meta_has_outcome": any(k in meta for k in ("outcome", "policy_outcome", "final_evidence", "evidence_result", "promotion_evidence", "direct_step_credit")),
        "execution": {
            "collector_only": True,
            "start_replay": False,
            "start_dream": False,
            "start_runner": False,
            "write_db": False,
            "write_policy": False,
        },
        "next_gate": {
            "would_feed_policy_mini_write": bool(chosen),
            "requires_separate_outcome_queue_write": bool(chosen),
            "policy_write_allowed_here": False,
        },
    }


def _bucket_map(items: Iterable[Mapping[str, Any]], topk: int) -> Dict[str, Dict[str, Any]]:
    names = (
        "outcome_ready_pos",
        "outcome_ready_neg",
        "outcome_ready_draw",
        "ambiguous_outcome",
        "historical_policy_ledger_only",
        "policy_snapshot_only",
        "missing_direct_outcome",
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {n: [] for n in names}
    for it in items:
        b = _safe_str(it.get("outcome_bucket"), 120)
        if b not in grouped:
            b = "missing_direct_outcome"
        grouped[b].append(dict(it))
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        arr = sorted(grouped[name], key=lambda x: (_as_float(x.get("score"), 0.0), _as_int(x.get("promotion_id"), 0)), reverse=True)
        out[name] = {
            "count_total": len(arr),
            "count_returned": min(len(arr), int(topk)),
            "execution": {"collector_only": True, "start_job": False, "write_db": False, "write_policy": False},
            "items": arr[: int(topk)],
        }
    return out


def collect_gap_evidence_outcomes(
    *,
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    buckets: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    reward_eps: Optional[float] = None,
    lookback_sec: Optional[int] = None,
) -> Dict[str, Any]:
    start = time.time()
    now = _now_ts()
    base = _base_dir()
    dbp = (db_path or default_db_path(base)).resolve()
    outp = (state_path or default_state_path(base)).resolve()
    bucket_list = list(buckets or parse_csv(_env_str("OROMA_GAP_EVIDENCE_OUTCOME_BUCKETS", ",".join(DEFAULT_BUCKETS)), DEFAULT_BUCKETS))
    row_limit = max(1, int(limit if limit is not None else _env_int("OROMA_GAP_EVIDENCE_OUTCOME_LIMIT", 50)))
    top_n = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_EVIDENCE_OUTCOME_TOPK", 10)))
    eps = float(reward_eps if reward_eps is not None else _env_float("OROMA_GAP_EVIDENCE_OUTCOME_REWARD_EPS", 0.000001))
    lookback = max(0, int(lookback_sec if lookback_sec is not None else _env_int("OROMA_GAP_EVIDENCE_OUTCOME_LOOKBACK_SEC", 1209600)))
    lookback_ts = 0 if lookback <= 0 else now - lookback

    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "read_only_gap_evidence_outcome_collector",
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "base": str(base),
        "db_path": str(dbp),
        "state_path": str(outp),
        "config": {"buckets": bucket_list, "limit": row_limit, "topk": top_n, "reward_eps": eps, "lookback_sec": lookback, "lookback_ts": lookback_ts},
        "outcomes": {},
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
            "state_json_write": True,
            "policy_snapshot_is_not_outcome": True,
        },
    }

    try:
        con = _connect_ro(dbp)
    except Exception as exc:
        doc["errors"].append({"where": "sqlite_connect_read_only", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "db_read_only_connect_failed", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc

    try:
        rows, load_errors = _load_promotion_rows(con, bucket_list, row_limit)
        doc["errors"].extend({"where": "load_promotion_rows", "error": e} for e in load_errors)
        items = [_classify_row(con, r, lookback_ts, eps) for r in rows]
        buckets_doc = _bucket_map(items, top_n)
        counts = {k: int(v.get("count_total", 0)) for k, v in buckets_doc.items()}
        outcome_ready_total = int(counts.get("outcome_ready_pos", 0) + counts.get("outcome_ready_neg", 0) + counts.get("outcome_ready_draw", 0))
        missing_total = int(counts.get("missing_direct_outcome", 0) + counts.get("policy_snapshot_only", 0) + counts.get("historical_policy_ledger_only", 0))

        table_counts: Dict[str, int] = {}
        for table in ("rewards_log", "episode_events", "dream_policy_mini_write_ledger", "gap_policy_mini_write_ledger", POLICY_TABLE, PROMOTION_TABLE):
            if _table_exists(con, table):
                try:
                    table_counts[table] = int(con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
                except Exception:
                    table_counts[table] = -1

        doc["ok"] = True
        doc["outcomes"] = buckets_doc
        doc["source_tables"] = table_counts
        doc["summary"] = {
            "ok": True,
            "dt_sec": round(time.time() - start, 3),
            "promotion_rows_loaded": len(rows),
            "outcome_ready_total": outcome_ready_total,
            "missing_or_snapshot_only_total": missing_total,
            "ambiguous_total": int(counts.get("ambiguous_outcome", 0)),
            "per_bucket_counts": counts,
            "policy_writes": 0,
            "db_writes": 0,
            "runner_starts": 0,
            "replay_starts": 0,
            "dream_starts": 0,
            "state_written": True,
            "next_step": "if_outcome_ready_total_gt_0_then_outcome_queue_write_gate_else_targeted_replay_or_dream_evidence",
        }
        atomic_write_json(outp, doc)
        return doc
    finally:
        try:
            con.close()
        except Exception:
            pass


def run_once(**kwargs: Any) -> Dict[str, Any]:
    return collect_gap_evidence_outcomes(**kwargs)

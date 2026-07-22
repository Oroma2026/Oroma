#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_evidence_queue.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Queue · DBWriter-only Review-/Evidence-Write
# Version:   v0.1.0-dbwriter-evidence-queue
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist der erste bewusst schreibende Schritt nach der read-only
# Gap-Kette:
#
#   knowledge_gaps
#       -> gap_learning_focus.json
#       -> gap_focus_consumer.json
#       -> gap_focus_shadow_plan.json
#       -> gap_focus_evidence_queue   (dieses Modul)
#
# Es schreibt KEINE Policy. Es startet KEINE Jobs. Es erzeugt lediglich eine
# deduplizierte, auditierbare Review-/Evidence-Queue in der ORÓMA-Hauptdatenbank.
# Diese Queue ist der sichere Zwischenpuffer zwischen "Lernbedarf erkannt" und
# spaeteren, separat gegateten Verbrauchern wie Replay-Review, Dream-Review,
# Explore-Planung oder Runner-Prioritaet.
#
# WARUM NICHT DIREKT policy_rules?
# -------------------------------
# Ein Gap ist nur Lernbedarf. Ein Shadow-Plan ist nur ein Kandidat. Beides ist
# noch kein belastbares Reward-/Credit-Signal. Direkte Gap->policy_rules-Writes
# koennten schlechte Aktionen verstaerken, wenn die Credit-Zuordnung unklar ist.
# Deshalb schreibt diese Stufe nur in eine Review-Queue. Spaetere Verbraucher
# duerfen daraus Evidenz erzeugen oder pruefen, muessen aber eigene Gates haben.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhaengigkeiten.
# - Quelle: data/state/gap_focus_shadow_plan.json.
# - DB-Writes ausschliesslich ueber core.db_writer_client / DBWriter.
# - Kein lokaler SQLite-Write-Fallback, auch nicht bei DBWriter-Ausfall.
# - Keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Schemaaenderung nur fuer eigene Queue-Tabelle + eigene Indizes.
# - Deduplizierung ueber request_signature; wiederholte Auto-Laeufe erzeugen
#   keine unbounded Duplikate.
# - State-Write nur atomar nach data/state/gap_evidence_queue_writer.json.
# - Sichtbare Blockgruende statt stiller No-ops.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DBW_ENABLE=1
#   OROMA_GAP_EVIDENCE_QUEUE_WRITE_ENABLE=1
#   OROMA_GAP_EVIDENCE_QUEUE_CONFIRM=GAP_EVIDENCE_QUEUE_WRITE_REVIEWED
#   OROMA_GAP_EVIDENCE_QUEUE_CONFIRM_REQUIRED=GAP_EVIDENCE_QUEUE_WRITE_REVIEWED
#   OROMA_GAP_EVIDENCE_QUEUE_SOURCE_PATH=/opt/ai/oroma/data/state/gap_focus_shadow_plan.json
#   OROMA_GAP_EVIDENCE_QUEUE_STATE_PATH=/opt/ai/oroma/data/state/gap_evidence_queue_writer.json
#   OROMA_GAP_EVIDENCE_QUEUE_TARGETS=explore,replay,dream,runner_priority
#   OROMA_GAP_EVIDENCE_QUEUE_TOPK=10
#   OROMA_GAP_EVIDENCE_QUEUE_MAX_AGE_SEC=7200
#   OROMA_GAP_EVIDENCE_QUEUE_ALLOW_STALE=0
#   OROMA_GAP_EVIDENCE_QUEUE_DBW_TIMEOUT_MS=15000
#   OROMA_GAP_EVIDENCE_QUEUE_DBW_PING_TIMEOUT_MS=500
# =============================================================================

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    from core import db_writer_client  # type: ignore
except Exception:  # pragma: no cover - production fallback is explicit in status
    db_writer_client = None  # type: ignore

VERSION = "v0.1.0-dbwriter-evidence-queue"
DEFAULT_SOURCE_NAME = "gap_focus_shadow_plan.json"
DEFAULT_STATE_NAME = "gap_evidence_queue_writer.json"
DEFAULT_TARGETS = ("explore", "replay", "dream", "runner_priority")
CONFIRM_REQUIRED_DEFAULT = "GAP_EVIDENCE_QUEUE_WRITE_REVIEWED"
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


def _safe_str(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > int(limit):
        return text[: max(0, int(limit) - 3)] + "..."
    return text


def default_source_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_QUEUE_SOURCE_PATH") or os.environ.get("OROMA_GAP_FOCUS_SHADOW_PLAN_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_SOURCE_NAME).resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_QUEUE_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_STATE_NAME).resolve()


def load_shadow_plan(path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    p = (path or default_source_path()).resolve()
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, "shadow_plan_not_object"
        return data, None
    except FileNotFoundError:
        return None, "shadow_plan_missing"
    except json.JSONDecodeError as exc:
        return None, "shadow_plan_json_invalid:%s" % exc
    except Exception as exc:
        return None, "shadow_plan_read_error:%s" % exc


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


def _request_signature(item: Mapping[str, Any]) -> str:
    raw = "|".join([
        str(item.get("target") or ""),
        str(item.get("plan_id") or ""),
        str(item.get("focus_id") or ""),
        str(item.get("namespace") or ""),
        str(item.get("state_hash") or ""),
        str(item.get("primary_action") or ""),
        str(item.get("reason") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _flatten_shadow_items(shadow_plan: Mapping[str, Any], target_list: Sequence[str], topk: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    plans = shadow_plan.get("plans") if isinstance(shadow_plan.get("plans"), dict) else {}
    candidates: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for target in target_list:
        bucket = plans.get(target) if isinstance(plans.get(target), dict) else {}
        items = bucket.get("items") if isinstance(bucket.get("items"), list) else []
        taken = 0
        for raw in items:
            if taken >= int(topk):
                break
            if not isinstance(raw, dict):
                blocked.append({"target": target, "reason": "malformed_shadow_item"})
                continue
            plan_id = _safe_str(raw.get("plan_id"), 160)
            focus_id = _safe_str(raw.get("focus_id"), 160)
            ns = _safe_str(raw.get("namespace"), 160)
            state_hash = _safe_str(raw.get("state_hash"), 4000)
            if not plan_id or not focus_id or not ns or not state_hash:
                blocked.append({
                    "target": target,
                    "plan_id": plan_id,
                    "focus_id": focus_id,
                    "reason": "missing_required_identity_fields",
                })
                continue
            item = dict(raw)
            item["target"] = target
            item["plan_id"] = plan_id
            item["focus_id"] = focus_id
            item["namespace"] = ns
            item["state_hash"] = state_hash
            item["request_signature"] = _request_signature(item)
            candidates.append(item)
            taken += 1
    return candidates, blocked


def _schema_statements() -> List[Tuple[str, Sequence[Any]]]:
    return [
        ("""
        CREATE TABLE IF NOT EXISTS gap_focus_evidence_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_signature TEXT NOT NULL UNIQUE,
            plan_id TEXT NOT NULL,
            focus_id TEXT,
            target TEXT NOT NULL,
            namespace TEXT,
            state_hash TEXT,
            primary_action TEXT,
            kind TEXT,
            reason TEXT,
            recommended_next TEXT,
            score REAL,
            status TEXT NOT NULL DEFAULT 'queued',
            source_plan_ts INTEGER,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            meta_json TEXT
        )
        """, ()),
        ("CREATE INDEX IF NOT EXISTS idx_gap_focus_evidence_queue_status_target ON gap_focus_evidence_queue(status, target, created_ts)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_gap_focus_evidence_queue_ns_state ON gap_focus_evidence_queue(namespace, state_hash)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_gap_focus_evidence_queue_source_ts ON gap_focus_evidence_queue(source_plan_ts)", ()),
    ]


def _insert_statement(item: Mapping[str, Any], source_plan_ts: int, now: int) -> Tuple[str, Sequence[Any]]:
    pe = item.get("policy_evidence") if isinstance(item.get("policy_evidence"), dict) else {}
    meta = {
        "version": VERSION,
        "shadow_action": item.get("shadow_action"),
        "avg_confidence": item.get("avg_confidence"),
        "actions": item.get("actions") if isinstance(item.get("actions"), list) else [],
        "gap_ids": item.get("gap_ids") if isinstance(item.get("gap_ids"), list) else [],
        "latest_ts": item.get("latest_ts"),
        "policy_evidence": pe,
        "future_gate": item.get("future_gate") if isinstance(item.get("future_gate"), dict) else {},
        "execution": {
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_policy": False,
            "queue_write_only": True,
        },
    }
    return (
        """
        INSERT OR IGNORE INTO gap_focus_evidence_queue (
            request_signature, plan_id, focus_id, target, namespace, state_hash,
            primary_action, kind, reason, recommended_next, score, status,
            source_plan_ts, created_ts, updated_ts, attempts, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, 0, ?)
        """,
        (
            _safe_str(item.get("request_signature"), 128),
            _safe_str(item.get("plan_id"), 160),
            _safe_str(item.get("focus_id"), 160),
            _safe_str(item.get("target"), 80),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("primary_action"), 160),
            _safe_str(item.get("kind"), 80),
            _safe_str(item.get("reason"), 160),
            _safe_str(item.get("recommended_next"), 160),
            _as_float(item.get("score"), 0.0),
            int(source_plan_ts or 0),
            int(now),
            int(now),
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
        ),
    )


def _dbwriter_ready(timeout_ms: int) -> Tuple[bool, str]:
    if db_writer_client is None:
        return False, "dbwriter_client_import_failed"
    try:
        if not bool(db_writer_client.enabled()):
            return False, "dbwriter_disabled"
        if not bool(db_writer_client.ping(timeout_ms=int(timeout_ms))):
            return False, "dbwriter_ping_failed"
        return True, "dbwriter_ready"
    except Exception as exc:
        return False, "dbwriter_error:%s" % exc


def build_queue_write_plan(
    *,
    source_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    targets: Optional[Sequence[str]] = None,
    topk: Optional[int] = None,
    max_age_sec: Optional[int] = None,
    allow_stale: Optional[bool] = None,
    write_enable: Optional[bool] = None,
    confirm_token: Optional[str] = None,
    confirm_required: Optional[str] = None,
    dbw_timeout_ms: Optional[int] = None,
    dbw_ping_timeout_ms: Optional[int] = None,
) -> Dict[str, Any]:
    start = time.time()
    base = _base_dir()
    src = (source_path or default_source_path(base)).resolve()
    out_path = (state_path or default_state_path(base)).resolve()
    target_list = list(targets or parse_csv(_env_str("OROMA_GAP_EVIDENCE_QUEUE_TARGETS", ",".join(DEFAULT_TARGETS)), DEFAULT_TARGETS))
    target_list = [t for t in target_list if t]
    if not target_list:
        target_list = list(DEFAULT_TARGETS)
    top_n = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_EVIDENCE_QUEUE_TOPK", 10)))
    max_age = int(max_age_sec if max_age_sec is not None else _env_int("OROMA_GAP_EVIDENCE_QUEUE_MAX_AGE_SEC", 7200))
    stale_allowed = bool(allow_stale if allow_stale is not None else _env_bool("OROMA_GAP_EVIDENCE_QUEUE_ALLOW_STALE", False))
    write_on = bool(write_enable if write_enable is not None else _env_bool("OROMA_GAP_EVIDENCE_QUEUE_WRITE_ENABLE", False))
    required = str(confirm_required if confirm_required is not None else _env_str("OROMA_GAP_EVIDENCE_QUEUE_CONFIRM_REQUIRED", CONFIRM_REQUIRED_DEFAULT)).strip()
    confirm = str(confirm_token if confirm_token is not None else os.environ.get("OROMA_GAP_EVIDENCE_QUEUE_CONFIRM", "")).strip()
    confirm_ok = bool(required and confirm == required)
    timeout_ms = int(dbw_timeout_ms if dbw_timeout_ms is not None else _env_int("OROMA_GAP_EVIDENCE_QUEUE_DBW_TIMEOUT_MS", 15000))
    ping_timeout_ms = int(dbw_ping_timeout_ms if dbw_ping_timeout_ms is not None else _env_int("OROMA_GAP_EVIDENCE_QUEUE_DBW_PING_TIMEOUT_MS", 500))

    now = _now_ts()
    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "dbwriter_gap_evidence_queue",
        "base": str(base),
        "source_path": str(src),
        "state_path": str(out_path),
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "config": {
            "targets": target_list,
            "topk_per_target": top_n,
            "max_age_sec": max_age,
            "allow_stale": stale_allowed,
            "write_enable": write_on,
            "confirm_required": required,
            "confirm_ok": confirm_ok,
            "dbw_timeout_ms": timeout_ms,
            "dbw_ping_timeout_ms": ping_timeout_ms,
        },
        "safety": {
            "db_access": "write_via_dbwriter_only",
            "db_writes": bool(write_on and confirm_ok),
            "policy_writes": False,
            "schema_changes": "own_queue_table_only_when_write_ready",
            "runner_starts": False,
            "dream_starts": False,
            "replay_starts": False,
            "state_json_write": True,
            "local_sqlite_write_fallback": False,
        },
        "source": {},
        "summary": {},
        "queue_candidates": [],
        "blocked": [],
        "errors": [],
    }

    shadow_plan, err = load_shadow_plan(src)
    if err or shadow_plan is None:
        doc["source"] = {"ok": False, "reason": err or "shadow_plan_unavailable"}
        doc["summary"] = {"ok": False, "blocked_reason": err or "shadow_plan_unavailable", "dt_sec": round(time.time() - start, 3)}
        return doc

    source_ts = _as_int(shadow_plan.get("generated_at_ts"), 0)
    source_age = max(0, now - source_ts) if source_ts > 0 else None
    source_stale = bool(source_age is None or (max_age > 0 and int(source_age) > max_age))
    source_summary = shadow_plan.get("summary") if isinstance(shadow_plan.get("summary"), dict) else {}
    doc["source"] = {
        "ok": bool(shadow_plan.get("ok", False)),
        "mode": shadow_plan.get("mode"),
        "version": shadow_plan.get("version"),
        "generated_at_ts": source_ts,
        "generated_at_iso": shadow_plan.get("generated_at_iso"),
        "age_sec": source_age,
        "stale": source_stale,
        "summary": source_summary,
    }

    candidates: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    if source_stale and not stale_allowed:
        blocked.append({
            "reason": "source_stale",
            "source_age_sec": source_age,
            "max_age_sec": max_age,
        })
    elif not bool(shadow_plan.get("ok", False)):
        blocked.append({"reason": "source_not_ok", "source_mode": shadow_plan.get("mode")})
    else:
        candidates, blocked_extra = _flatten_shadow_items(shadow_plan, target_list, top_n)
        blocked.extend(blocked_extra)

    per_target_counts: Dict[str, int] = {}
    for c in candidates:
        tgt = str(c.get("target") or "")
        per_target_counts[tgt] = int(per_target_counts.get(tgt, 0)) + 1

    dbw_ready, dbw_reason = _dbwriter_ready(ping_timeout_ms)
    write_ready = bool(write_on and confirm_ok and dbw_ready and candidates and not (source_stale and not stale_allowed))
    if not write_on:
        write_block_reason = "write_gate_disabled"
    elif not confirm_ok:
        write_block_reason = "confirm_token_missing_or_wrong"
    elif not dbw_ready:
        write_block_reason = dbw_reason
    elif not candidates:
        write_block_reason = "no_candidates"
    elif source_stale and not stale_allowed:
        write_block_reason = "source_stale"
    else:
        write_block_reason = "write_ready"

    schema_ok = False
    inserted_attempted = 0
    inserted_ok_or_existing = 0
    transaction_ok = False
    write_error: Optional[str] = None

    if write_ready:
        assert db_writer_client is not None
        stmts: List[Tuple[str, Sequence[Any]]] = []
        stmts.extend(_schema_statements())
        for item in candidates:
            stmts.append(_insert_statement(item, source_ts, now))
        inserted_attempted = len(candidates)
        try:
            db_writer_client.transaction(
                stmts,
                tag="gap_evidence_queue.write",
                priority="normal",
                timeout_ms=timeout_ms,
                db="oroma",
            )
            schema_ok = True
            transaction_ok = True
            inserted_ok_or_existing = len(candidates)
        except Exception as exc:
            write_error = str(exc)
            doc["errors"].append({"where": "dbwriter_transaction", "error": write_error})
            write_block_reason = "dbwriter_transaction_failed"
    else:
        schema_ok = False

    doc["ok"] = bool(not write_error)
    doc["queue_candidates"] = [
        {
            "request_signature": c.get("request_signature"),
            "plan_id": c.get("plan_id"),
            "focus_id": c.get("focus_id"),
            "target": c.get("target"),
            "namespace": c.get("namespace"),
            "state_hash": c.get("state_hash"),
            "primary_action": c.get("primary_action"),
            "kind": c.get("kind"),
            "reason": c.get("reason"),
            "recommended_next": c.get("recommended_next"),
            "score": c.get("score"),
            "status": "queued_or_existing" if transaction_ok else "candidate_only",
        }
        for c in candidates[:100]
    ]
    doc["blocked"] = blocked[:100]
    doc["summary"] = {
        "ok": bool(not write_error),
        "dt_sec": round(time.time() - start, 3),
        "source_stale": source_stale,
        "source_age_sec": source_age,
        "input_candidates": len(candidates),
        "per_target_counts": per_target_counts,
        "blocked_total": len(blocked),
        "write_enable": write_on,
        "confirm_ok": confirm_ok,
        "dbwriter_ready": dbw_ready,
        "dbwriter_reason": dbw_reason,
        "write_ready": write_ready,
        "write_block_reason": write_block_reason,
        "schema_ok": schema_ok,
        "table": TABLE_NAME,
        "insert_attempted": inserted_attempted,
        "queued_or_existing": inserted_ok_or_existing,
        "transaction_ok": transaction_ok,
        "policy_writes": 0,
        "runner_starts": 0,
        "replay_starts": 0,
        "dream_starts": 0,
        "state_written": False,
    }
    return doc


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
    "build_queue_write_plan",
    "write_state",
    "load_shadow_plan",
    "default_source_path",
    "default_state_path",
    "parse_csv",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_policy_promotion.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Policy Promotion Review Queue · DBWriter-only · No Policy Write
# Version:   v0.2.4-append-only-no-row-refresh
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die naechste Sicherheitsstufe nach der Gap Evidence
# Execution/Validation-Dry-Run-Kette:
#
#   knowledge_gaps
#       -> gap_learning_focus.json
#       -> gap_focus_consumer.json
#       -> gap_focus_shadow_plan.json
#       -> gap_focus_evidence_queue              (DBWriter-only Evidence Queue)
#       -> gap_evidence_review.json              (read-only Review)
#       -> gap_evidence_validation.json          (read-only Validation)
#       -> gap_policy_promotion_queue            (dieses Modul)
#
# Die Promotion-Queue ist absichtlich noch KEIN policy_rules-Write. Sie legt nur
# auditierbare, deduplizierte Review-Kandidaten in einer eigenen Tabelle ab. Der
# spaetere Policy-Mini-Write-Gate darf nur aus dieser Queue lesen und muss dann
# erneut Evidence, Confirm-Token, Namespace, Dedupe und Ledger pruefen.
#
# WARUM EINE EIGENE PROMOTION-QUEUE?
# ----------------------------------
# Validierte Gap-Kandidaten sind fachlich plausibel, aber noch kein direkter
# Lernbeweis. Die Promotion-Queue trennt deshalb drei Dinge sauber:
#
#   1) Lernbedarf / Gap
#   2) Evidence-/Promotion-Kandidat
#   3) echter policy_rules-Write
#
# Diese Stufe schreibt nur Punkt 2. Punkt 3 bleibt technisch gesperrt.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhaengigkeiten.
# - Queue-/Policy-Lesen nur read-only via SQLite URI mode=ro.
# - DB-Write nur via DBWriter-Client, niemals lokaler SQLite-Schreibfallback.
# - Schemaaenderung nur eigene Tabelle gap_policy_promotion_queue.
# - Keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Dedupe ueber promotion_signature; Auto-Laeufe duerfen keine Duplikate bauen.
# - Bestehende Promotion-Zeilen werden niemals per Revalidierung aktualisiert.
# - Fachliche Freshness wird ausschließlich downstream durch exakt gebundene,
#   frische Targeted-Evidence attestiert; die historische Promotion bleibt unverändert.
# - Abgeschlossene Lifecycle-Zustaende werden niemals zurueckgesetzt oder reaktiviert.
# - State-Write atomar nach data/state/gap_policy_promotion_queue_writer.json.
# - Root-Manual-Laeufe setzen State-Datei best-effort auf oroma:oroma 664.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_POLICY_PROMOTION_SOURCE_PATH=/opt/ai/oroma/data/state/gap_evidence_validation.json
#   OROMA_GAP_POLICY_PROMOTION_STATE_PATH=/opt/ai/oroma/data/state/gap_policy_promotion_queue_writer.json
#   OROMA_GAP_POLICY_PROMOTION_WRITE_ENABLE=1
#   OROMA_GAP_POLICY_PROMOTION_CONFIRM_REQUIRED=GAP_POLICY_PROMOTION_REVIEWED
#   OROMA_GAP_POLICY_PROMOTION_CONFIRM=GAP_POLICY_PROMOTION_REVIEWED
#   OROMA_GAP_POLICY_PROMOTION_BUCKETS=validated_replay_execution_candidate,validated_dream_execution_candidate,validated_runner_priority_hint,validated_explore_candidate
#   OROMA_GAP_POLICY_PROMOTION_NAMESPACE_ALLOWLIST=
#   OROMA_GAP_POLICY_PROMOTION_STATE_SCHEMA_ALLOWLIST=
#   OROMA_GAP_POLICY_PROMOTION_TARGET_ALLOWLIST=
#   OROMA_GAP_POLICY_PROMOTION_LIMIT=200
#   OROMA_GAP_POLICY_PROMOTION_TOPK=10
#   OROMA_GAP_POLICY_PROMOTION_MAX_AGE_SEC=7200
#   OROMA_GAP_POLICY_PROMOTION_ALLOW_STALE=0
#   OROMA_GAP_POLICY_PROMOTION_MIN_SCORE=0.0
#   OROMA_GAP_POLICY_PROMOTION_DBW_TIMEOUT_MS=15000
#   OROMA_GAP_POLICY_PROMOTION_DBW_PING_TIMEOUT_MS=500
# =============================================================================

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import pwd
import grp
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from core import db_writer_client
except Exception:  # pragma: no cover - defensive import for broken partial deployments
    db_writer_client = None  # type: ignore

VERSION = "v0.2.4-append-only-no-row-refresh"
DEFAULT_SOURCE_NAME = "gap_evidence_validation.json"
DEFAULT_STATE_NAME = "gap_policy_promotion_queue_writer.json"
TABLE_NAME = "gap_policy_promotion_queue"
EVIDENCE_TABLE = "gap_focus_evidence_queue"
DEFAULT_BUCKETS = (
    "validated_replay_execution_candidate",
    "validated_dream_execution_candidate",
    "validated_runner_priority_hint",
    "validated_explore_candidate",
)


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


def _matches_allowlist(value: str, allowlist: Sequence[str]) -> bool:
    """Return True when no scope is configured or one exact/glob pattern matches."""
    patterns = [str(p or "").strip() for p in allowlist if str(p or "").strip()]
    if not patterns:
        return True
    text = str(value or "").strip()
    return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns)


def _state_schema(state_hash: Any) -> str:
    """Extract the stable schema prefix used by ORÓMA state hashes.

    Professional schemas use ``domain:version:<payload>`` (for example
    ``snake:pro_v2:d=...``). Legacy hashes without such a prefix return an
    empty schema and can only pass when no schema scope is configured.
    """
    text = _safe_str(state_hash, 4000)
    parts = text.split(":", 2)
    if len(parts) >= 3 and parts[0] and parts[1]:
        return f"{parts[0]}:{parts[1]}"
    return ""


def _scope_block_reasons(
    item: Mapping[str, Any],
    *,
    namespace_allowlist: Sequence[str],
    state_schema_allowlist: Sequence[str],
    target_allowlist: Sequence[str],
) -> List[str]:
    reasons: List[str] = []
    namespace = _safe_str(item.get("namespace"), 160)
    target = _safe_str(item.get("target"), 80)
    schema = _state_schema(item.get("state_hash"))
    if not _matches_allowlist(namespace, namespace_allowlist):
        reasons.append("namespace_not_in_scope")
    if state_schema_allowlist and not _matches_allowlist(schema, state_schema_allowlist):
        reasons.append("state_schema_not_in_scope")
    if not _matches_allowlist(target, target_allowlist):
        reasons.append("target_not_in_scope")
    return reasons


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
    explicit = os.environ.get("OROMA_GAP_POLICY_PROMOTION_SOURCE_PATH") or os.environ.get("OROMA_GAP_EVIDENCE_VALIDATION_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_SOURCE_NAME).resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_POLICY_PROMOTION_STATE_PATH")
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


def _iter_validation_items(source: Mapping[str, Any], bucket_allowlist: Sequence[str], limit: int) -> List[Dict[str, Any]]:
    validation = source.get("validation") if isinstance(source.get("validation"), dict) else {}
    out: List[Dict[str, Any]] = []
    for bucket in bucket_allowlist:
        block = validation.get(bucket) if isinstance(validation.get(bucket), dict) else {}
        items = block.get("items") if isinstance(block.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["source_validation_bucket"] = bucket
            out.append(row)
            if len(out) >= int(limit):
                return out
    return out


def _queue_row_by_signature(con: sqlite3.Connection, signature: str) -> Optional[sqlite3.Row]:
    if not signature or not _table_exists(con, EVIDENCE_TABLE):
        return None
    return con.execute(f"SELECT * FROM {EVIDENCE_TABLE} WHERE request_signature=? LIMIT 1", (signature,)).fetchone()


def _promotion_signature(item: Mapping[str, Any]) -> str:
    payload = {
        "request_signature": _safe_str(item.get("request_signature"), 256),
        "target": _safe_str(item.get("target"), 80),
        "namespace": _safe_str(item.get("namespace"), 160),
        "state_hash": _safe_str(item.get("state_hash"), 4000),
        "primary_action": _safe_str(item.get("primary_action"), 160),
        "source_validation_bucket": _safe_str(item.get("source_validation_bucket") or item.get("validation_bucket"), 160),
        "version_scope": "gap_policy_promotion:v0.1",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _promotion_bucket(item: Mapping[str, Any]) -> Tuple[str, str, str, int]:
    src = _safe_str(item.get("source_validation_bucket") or item.get("validation_bucket"), 160)
    target = _safe_str(item.get("target"), 80)
    if src == "validated_replay_execution_candidate" or target == "replay":
        return "promotion_candidate_replay", "promotion_review", "validated_replay_needs_final_policy_gate", 0
    if src == "validated_dream_execution_candidate" or target == "dream":
        return "promotion_candidate_dream", "promotion_review", "validated_dream_needs_final_policy_gate", 0
    if src == "validated_runner_priority_hint" or target == "runner_priority":
        return "runner_priority_observation", "observation_only", "runner_priority_is_not_policy_write_candidate", 0
    if src == "validated_explore_candidate" or target == "explore":
        return "explore_promotion_candidate", "explore_review", "validated_explore_needs_execution_gate", 0
    return "blocked_or_insufficient", "blocked", "validation_bucket_not_promotable", 0


def _schema_statements() -> List[Tuple[str, Sequence[Any]]]:
    return [
        ("""
        CREATE TABLE IF NOT EXISTS gap_policy_promotion_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_signature TEXT NOT NULL UNIQUE,
            request_signature TEXT NOT NULL,
            evidence_queue_id INTEGER,
            plan_id TEXT,
            focus_id TEXT,
            target TEXT NOT NULL,
            promotion_bucket TEXT NOT NULL,
            namespace TEXT,
            state_hash TEXT,
            primary_action TEXT,
            kind TEXT,
            reason TEXT,
            recommended_next TEXT,
            score REAL,
            status TEXT NOT NULL DEFAULT 'promotion_review',
            policy_write_allowed INTEGER NOT NULL DEFAULT 0,
            source_validation_bucket TEXT,
            source_validation_ts INTEGER,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            meta_json TEXT
        )
        """, ()),
        ("CREATE INDEX IF NOT EXISTS idx_gap_policy_promotion_status_bucket ON gap_policy_promotion_queue(status, promotion_bucket, created_ts)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_gap_policy_promotion_ns_state ON gap_policy_promotion_queue(namespace, state_hash)", ()),
        ("CREATE INDEX IF NOT EXISTS idx_gap_policy_promotion_request ON gap_policy_promotion_queue(request_signature)", ()),
        ("""
        CREATE TRIGGER IF NOT EXISTS trg_gap_policy_promotion_no_delete
        BEFORE DELETE ON gap_policy_promotion_queue
        BEGIN
            SELECT RAISE(ABORT, 'gap_policy_promotion_queue is append-only; use lifecycle status transitions');
        END
        """, ()),
    ]


def _candidate_from_item(item: Mapping[str, Any], queue_row: Optional[sqlite3.Row], source_ts: int) -> Dict[str, Any]:
    bucket, status, reason, policy_allowed = _promotion_bucket(item)
    qid = _as_int(queue_row["id"], 0) if queue_row is not None else _as_int(item.get("id"), 0)
    namespace = _safe_str(queue_row["namespace"], 160) if queue_row is not None else _safe_str(item.get("namespace"), 160)
    state_hash = _safe_str(queue_row["state_hash"], 4000) if queue_row is not None else _safe_str(item.get("state_hash"), 4000)
    target = _safe_str(queue_row["target"], 80) if queue_row is not None else _safe_str(item.get("target"), 80)
    primary_action = _safe_str(queue_row["primary_action"], 160) if queue_row is not None else _safe_str(item.get("primary_action"), 160)
    cand: Dict[str, Any] = {
        "promotion_signature": "",
        "request_signature": _safe_str(item.get("request_signature"), 256),
        "evidence_queue_id": qid,
        "plan_id": _safe_str(queue_row["plan_id"], 160) if queue_row is not None else _safe_str(item.get("plan_id"), 160),
        "focus_id": _safe_str(queue_row["focus_id"], 160) if queue_row is not None else _safe_str(item.get("focus_id"), 160),
        "target": target,
        "promotion_bucket": bucket,
        "namespace": namespace,
        "state_hash": state_hash,
        "primary_action": primary_action,
        "kind": _safe_str(queue_row["kind"], 120) if queue_row is not None else _safe_str(item.get("kind"), 120),
        "reason": _safe_str(queue_row["reason"], 240) if queue_row is not None else _safe_str(item.get("reason"), 240),
        "recommended_next": _safe_str(queue_row["recommended_next"], 160) if queue_row is not None else _safe_str(item.get("recommended_next"), 160),
        "score": _as_float(queue_row["score"], 0.0) if queue_row is not None else _as_float(item.get("score"), 0.0),
        "status": status,
        "policy_write_allowed": int(policy_allowed),
        "source_validation_bucket": _safe_str(item.get("source_validation_bucket") or item.get("validation_bucket"), 160),
        "source_validation_ts": int(source_ts or 0),
        "promotion_reason": reason,
        "policy_validation_basis": _safe_str(item.get("policy_validation_basis"), 160),
        "policy_evidence_for_validation": item.get("policy_evidence_for_validation") if isinstance(item.get("policy_evidence_for_validation"), dict) else {},
        "current_policy_evidence": item.get("current_policy_evidence") if isinstance(item.get("current_policy_evidence"), dict) else {},
        "execution": {
            "promotion_queue_only": True,
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_policy": False,
            "policy_write_allowed": False,
        },
        "future_gate": {
            "next_required_gate": "gap_policy_mini_write_gate",
            "requires_confirm_token": True,
            "requires_ledger": True,
            "requires_namespace_allowlist": True,
            "requires_dedupe": True,
        },
    }
    cand["promotion_signature"] = _promotion_signature(cand)
    return cand


def _insert_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    meta = {
        "version": VERSION,
        "promotion_reason": item.get("promotion_reason"),
        "policy_validation_basis": item.get("policy_validation_basis"),
        "policy_evidence_for_validation": item.get("policy_evidence_for_validation") if isinstance(item.get("policy_evidence_for_validation"), dict) else {},
        "current_policy_evidence": item.get("current_policy_evidence") if isinstance(item.get("current_policy_evidence"), dict) else {},
        "execution": item.get("execution") if isinstance(item.get("execution"), dict) else {},
        "future_gate": item.get("future_gate") if isinstance(item.get("future_gate"), dict) else {},
    }
    return (
        """
        INSERT INTO gap_policy_promotion_queue (
            promotion_signature, request_signature, evidence_queue_id, plan_id, focus_id,
            target, promotion_bucket, namespace, state_hash, primary_action, kind, reason,
            recommended_next, score, status, policy_write_allowed, source_validation_bucket,
            source_validation_ts, created_ts, updated_ts, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(promotion_signature) DO NOTHING
        """,
        (
            _safe_str(item.get("promotion_signature"), 128),
            _safe_str(item.get("request_signature"), 128),
            _as_int(item.get("evidence_queue_id"), 0),
            _safe_str(item.get("plan_id"), 160),
            _safe_str(item.get("focus_id"), 160),
            _safe_str(item.get("target"), 80),
            _safe_str(item.get("promotion_bucket"), 120),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("primary_action"), 160),
            _safe_str(item.get("kind"), 120),
            _safe_str(item.get("reason"), 240),
            _safe_str(item.get("recommended_next"), 160),
            _as_float(item.get("score"), 0.0),
            _safe_str(item.get("status"), 80),
            _as_int(item.get("policy_write_allowed"), 0),
            _safe_str(item.get("source_validation_bucket"), 160),
            _as_int(item.get("source_validation_ts"), 0),
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


def build_promotion_queue_write(
    *,
    db_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    buckets: Optional[Sequence[str]] = None,
    namespace_allowlist: Optional[Sequence[str]] = None,
    state_schema_allowlist: Optional[Sequence[str]] = None,
    target_allowlist: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    max_age_sec: Optional[int] = None,
    allow_stale: Optional[bool] = None,
    min_score: Optional[float] = None,
    write_enable: Optional[bool] = None,
    confirm_token: Optional[str] = None,
    confirm_required: Optional[str] = None,
    dbw_timeout_ms: Optional[int] = None,
    dbw_ping_timeout_ms: Optional[int] = None,
) -> Dict[str, Any]:
    start = time.time()
    now = _now_ts()
    base = _base_dir()
    dbp = (db_path or default_db_path(base)).resolve()
    srcp = (source_path or default_source_path(base)).resolve()
    outp = (state_path or default_state_path(base)).resolve()
    bucket_list = list(buckets or parse_csv(_env_str("OROMA_GAP_POLICY_PROMOTION_BUCKETS", ",".join(DEFAULT_BUCKETS)), DEFAULT_BUCKETS))
    namespace_scope = list(namespace_allowlist if namespace_allowlist is not None else parse_csv(_env_str("OROMA_GAP_POLICY_PROMOTION_NAMESPACE_ALLOWLIST", "")))
    state_schema_scope = list(state_schema_allowlist if state_schema_allowlist is not None else parse_csv(_env_str("OROMA_GAP_POLICY_PROMOTION_STATE_SCHEMA_ALLOWLIST", "")))
    target_scope = list(target_allowlist if target_allowlist is not None else parse_csv(_env_str("OROMA_GAP_POLICY_PROMOTION_TARGET_ALLOWLIST", "")))
    row_limit = max(1, int(limit if limit is not None else _env_int("OROMA_GAP_POLICY_PROMOTION_LIMIT", 200)))
    top_n = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_POLICY_PROMOTION_TOPK", 10)))
    max_age = int(max_age_sec if max_age_sec is not None else _env_int("OROMA_GAP_POLICY_PROMOTION_MAX_AGE_SEC", 7200))
    stale_allowed = bool(allow_stale if allow_stale is not None else _env_bool("OROMA_GAP_POLICY_PROMOTION_ALLOW_STALE", False))
    min_s = float(min_score if min_score is not None else _env_float("OROMA_GAP_POLICY_PROMOTION_MIN_SCORE", 0.0))
    write_on = bool(write_enable if write_enable is not None else _env_bool("OROMA_GAP_POLICY_PROMOTION_WRITE_ENABLE", False))
    required = str(confirm_required if confirm_required is not None else _env_str("OROMA_GAP_POLICY_PROMOTION_CONFIRM_REQUIRED", "GAP_POLICY_PROMOTION_REVIEWED"))
    token = str(confirm_token if confirm_token is not None else os.environ.get("OROMA_GAP_POLICY_PROMOTION_CONFIRM", ""))
    confirm_ok = bool(required and token == required)
    timeout_ms = int(dbw_timeout_ms if dbw_timeout_ms is not None else _env_int("OROMA_GAP_POLICY_PROMOTION_DBW_TIMEOUT_MS", 15000))
    ping_timeout_ms = int(dbw_ping_timeout_ms if dbw_ping_timeout_ms is not None else _env_int("OROMA_GAP_POLICY_PROMOTION_DBW_PING_TIMEOUT_MS", 500))

    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "dbwriter_gap_policy_promotion_queue",
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "base": str(base),
        "db_path": str(dbp),
        "source_path": str(srcp),
        "state_path": str(outp),
        "config": {
            "buckets": bucket_list,
            "namespace_allowlist": namespace_scope,
            "state_schema_allowlist": state_schema_scope,
            "target_allowlist": target_scope,
            "limit": row_limit,
            "topk": top_n,
            "max_age_sec": max_age,
            "allow_stale": stale_allowed,
            "min_score": min_s,
            "write_enable": write_on,
            "confirm_required": required,
            "confirm_ok": confirm_ok,
        },
        "source": {},
        "promotion_candidates": [],
        "blocked": [],
        "summary": {},
        "errors": [],
        "safety": {
            "db_access": "read_only_plus_dbwriter_own_queue_write",
            "db_writes": True,
            "local_sqlite_write_fallback": False,
            "schema_changes": "own_promotion_queue_table_only_when_write_ready",
            "policy_writes": False,
            "rules_writes": False,
            "runner_starts": False,
            "replay_starts": False,
            "dream_starts": False,
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

    source_items = _iter_validation_items(source, bucket_list, row_limit)
    candidates: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    queue_missing = 0
    scope_blocked = 0
    scope_block_reason_counts: Dict[str, int] = {}

    try:
        con = _connect_ro(dbp)
    except Exception as exc:
        doc["errors"].append({"where": "sqlite_connect_read_only", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "db_read_only_connect_failed", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc

    try:
        if not _table_exists(con, EVIDENCE_TABLE):
            doc["summary"] = {"ok": False, "blocked_reason": "evidence_queue_missing", "dt_sec": round(time.time() - start, 3), "state_written": False}
            return doc
        for item in source_items:
            sig = _safe_str(item.get("request_signature"), 256)
            row = _queue_row_by_signature(con, sig)
            if row is None:
                queue_missing += 1
                blocked.append({"request_signature": sig, "reason": "evidence_queue_row_missing"})
                continue
            if _as_float(item.get("score"), 0.0) < min_s:
                blocked.append({"request_signature": sig, "reason": "score_below_min"})
                continue
            if _safe_str(row["status"], 80) != "queued":
                blocked.append({"request_signature": sig, "reason": "evidence_queue_status_not_queued", "status": row["status"]})
                continue
            scoped_item = dict(item)
            scoped_item.update({
                "namespace": _safe_str(row["namespace"], 160),
                "state_hash": _safe_str(row["state_hash"], 4000),
                "target": _safe_str(row["target"], 80),
            })
            scope_reasons = _scope_block_reasons(
                scoped_item,
                namespace_allowlist=namespace_scope,
                state_schema_allowlist=state_schema_scope,
                target_allowlist=target_scope,
            )
            if scope_reasons:
                scope_blocked += 1
                for reason in scope_reasons:
                    scope_block_reason_counts[reason] = int(scope_block_reason_counts.get(reason, 0)) + 1
                blocked.append({
                    "request_signature": sig,
                    "reason": ",".join(scope_reasons),
                    "blocked_reasons": scope_reasons,
                    "namespace": scoped_item["namespace"],
                    "state_schema": _state_schema(scoped_item["state_hash"]),
                    "target": scoped_item["target"],
                })
                continue
            cand = _candidate_from_item(item, row, _as_int(source.get("generated_at_ts"), 0))
            cand["state_schema"] = _state_schema(cand.get("state_hash"))
            cand["scope_match"] = True
            candidates.append(cand)
    except Exception as exc:
        doc["errors"].append({"where": "promotion_candidate_build", "error": str(exc)})
        doc["summary"] = {"ok": False, "blocked_reason": "promotion_candidate_build_failed", "dt_sec": round(time.time() - start, 3), "state_written": False}
        return doc
    finally:
        try:
            con.close()
        except Exception:
            pass

    candidates.sort(
        key=lambda c: (
            -_as_float(c.get("score"), 0.0),
            -_as_int(c.get("source_validation_ts"), 0),
            _safe_str(c.get("request_signature"), 256),
            _safe_str(c.get("promotion_signature"), 256),
        )
    )
    scope_candidates_total = len(candidates)
    selected_candidates = list(candidates[:top_n])
    deferred_candidates = list(candidates[top_n:])

    per_bucket_counts: Dict[str, int] = {}
    per_status_counts: Dict[str, int] = {}
    for c in selected_candidates:
        b = _safe_str(c.get("promotion_bucket"), 120)
        s = _safe_str(c.get("status"), 80)
        per_bucket_counts[b] = int(per_bucket_counts.get(b, 0)) + 1
        per_status_counts[s] = int(per_status_counts.get(s, 0)) + 1

    dbw_ready, dbw_reason = _dbwriter_ready(ping_timeout_ms)
    write_ready = bool(write_on and confirm_ok and dbw_ready and selected_candidates and not (stale and not stale_allowed))
    if not write_on:
        write_block_reason = "write_gate_disabled"
    elif not confirm_ok:
        write_block_reason = "confirm_token_missing_or_wrong"
    elif not dbw_ready:
        write_block_reason = dbw_reason
    elif not selected_candidates:
        write_block_reason = "no_candidates"
    elif stale and not stale_allowed:
        write_block_reason = "source_stale"
    else:
        write_block_reason = "write_ready"

    schema_ok = False
    transaction_ok = False
    attempted = 0
    queued_or_existing = 0
    write_error: Optional[str] = None
    if write_ready:
        assert db_writer_client is not None
        stmts: List[Tuple[str, Sequence[Any]]] = []
        stmts.extend(_schema_statements())
        for cand in selected_candidates:
            stmts.append(_insert_statement(cand, now))
        attempted = len(selected_candidates)
        try:
            db_writer_client.transaction(
                stmts,
                tag="gap_policy_promotion.write",
                priority="normal",
                timeout_ms=timeout_ms,
                db="oroma",
            )
            schema_ok = True
            transaction_ok = True
            queued_or_existing = len(selected_candidates)
        except Exception as exc:
            write_error = str(exc)
            doc["errors"].append({"where": "dbwriter_transaction", "error": write_error})
            write_block_reason = "dbwriter_transaction_failed"

    doc["ok"] = bool(not write_error)
    doc["promotion_candidates"] = [
        {
            "promotion_signature": c.get("promotion_signature"),
            "request_signature": c.get("request_signature"),
            "target": c.get("target"),
            "promotion_bucket": c.get("promotion_bucket"),
            "status": c.get("status"),
            "namespace": c.get("namespace"),
            "state_hash": c.get("state_hash"),
            "state_schema": c.get("state_schema"),
            "scope_match": c.get("scope_match"),
            "primary_action": c.get("primary_action"),
            "score": c.get("score"),
            "policy_write_allowed": False,
            "promotion_reason": c.get("promotion_reason"),
        }
        for c in selected_candidates[:100]
    ]
    doc["blocked"] = blocked[:100]
    doc["summary"] = {
        "ok": bool(not write_error),
        "dt_sec": round(time.time() - start, 3),
        "source_age_sec": age,
        "source_stale": stale,
        "source_items_loaded": len(source_items),
        "input_candidates": len(selected_candidates),
        "scope_candidates": scope_candidates_total,
        "scope_candidates_total": scope_candidates_total,
        "write_candidates_selected": len(selected_candidates),
        "write_candidates_deferred": len(deferred_candidates),
        "scope_blocked": scope_blocked,
        "scope_block_reason_counts": scope_block_reason_counts,
        "blocked_total": len(blocked),
        "queue_missing": queue_missing,
        "per_bucket_counts": per_bucket_counts,
        "per_status_counts": per_status_counts,
        "write_enable": write_on,
        "confirm_ok": confirm_ok,
        "dbwriter_ready": dbw_ready,
        "dbwriter_reason": dbw_reason,
        "write_ready": write_ready,
        "write_block_reason": write_block_reason,
        "schema_ok": schema_ok,
        "table": TABLE_NAME,
        "insert_attempted": attempted,
        "promotion_queued_or_existing": queued_or_existing,
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
    "DEFAULT_BUCKETS",
    "build_promotion_queue_write",
    "write_state",
    "default_db_path",
    "default_source_path",
    "default_state_path",
    "parse_csv",
]

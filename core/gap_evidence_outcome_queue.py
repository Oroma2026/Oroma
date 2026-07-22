#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_evidence_outcome_queue.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Evidence Outcome Queue Gate · DBWriter-only · No Policy Write
# Version:   v0.3.1-continuation-registry-outcome-queue
# Stand:     2026-07-16
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die Sicherheitsstufe nach dem Gap Targeted Replay Evidence
# Probe. Der Replay-Probe erzeugt nur State-JSON mit nachvollziehbaren Outcome-
# Vorschlaegen fuer wenige Kandidaten. Dieses Modul uebernimmt nur solche
# Kandidaten, die explizit als ready_for_outcome_queue markiert sind, und schreibt
# sie dedupliziert in eine eigene Tabelle:
#
#   gap_replay_evidence_probe.json
#       -> gap_evidence_outcome_queue
#
# Diese Tabelle ist noch KEIN policy_rules-Write. Sie ist ein auditierbarer,
# kanonischer Zwischenpuffer fuer echte Outcomes (pos/neg/draw), die spaeter vom
# Policy-Mini-Write-Gate erneut geprueft werden muessen.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11-, pygame- oder GUI-Abhaengigkeit.
# - Source-State lesen, DB nur fuer Schema-/Dedupe-Kontext read-only.
# - Jeder Source-Kandidat muss weiterhin exakt in der aktuellen Promotion-Queue existieren.
# - DB-Writes ausschliesslich via DBWriter-Client, niemals lokaler SQLite-
#   Schreibfallback.
# - Schemaaenderung nur eigene Tabelle gap_evidence_outcome_queue.
# - Keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Dedupe ueber outcome_signature UNIQUE; wiederholte Laeufe duerfen keine
#   doppelten Outcome-Zeilen erzeugen.
# - State-Write atomar nach data/state/gap_evidence_outcome_queue_writer.json.
# - Targeted Continuation-Protokolle werden ausschliesslich ueber die zentrale
#   Runner-Registry inklusive Protokollversion validiert; unbekannte oder
#   versionsfalsche Protokolle blockieren fail-closed.
# - Root-Manual-Laeufe setzen State-Datei best-effort auf oroma:oroma 664.
#
# WICHTIGER FACHLICHER RAHMEN
# ---------------------------
# Dieses Modul macht aus einem Replay-Probe-Outcome noch kein Policy-Wissen. Es
# schreibt nur das Outcome in eine eigene Queue. policy_rules duerfen erst spaeter
# durch ein separates Mini-Write-Gate veraendert werden, das Confirm-Token,
# Namespace, Dedupe, Ledger, Outcome-Signatur und Max-Write-Budget erneut prueft.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_SOURCE_PATH=/opt/ai/oroma/data/state/gap_replay_evidence_probe.json
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_STATE_PATH=/opt/ai/oroma/data/state/gap_evidence_outcome_queue_writer.json
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_WRITE_ENABLE=1
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_CONFIRM_REQUIRED=GAP_EVIDENCE_OUTCOME_QUEUE_REVIEWED
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_CONFIRM=GAP_EVIDENCE_OUTCOME_QUEUE_REVIEWED
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_MIN_CONFIDENCE=0.50
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_ALLOW_DRAW=0
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_LIMIT=10
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_TOPK=10
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_MAX_AGE_SEC=7200
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_ALLOW_STALE=0
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_DBW_TIMEOUT_MS=15000
#   OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_DBW_PING_TIMEOUT_MS=500
# =============================================================================

from __future__ import annotations

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
except Exception:  # pragma: no cover - defensive import for partial deployments
    db_writer_client = None  # type: ignore

try:
    from core.snake_targeted_evidence_runner import SUPPORTED_CONTINUATION_PROTOCOLS
except Exception:  # pragma: no cover - fail closed for partial deployments
    SUPPORTED_CONTINUATION_PROTOCOLS = {}

VERSION = "v0.3.1-continuation-registry-outcome-queue"
TABLE_NAME = "gap_evidence_outcome_queue"
PROMOTION_TABLE = "gap_policy_promotion_queue"
DEFAULT_SOURCE_NAME = "gap_replay_evidence_probe.json"
DEFAULT_STATE_NAME = "gap_evidence_outcome_queue_writer.json"
VALID_OUTCOMES = {"pos", "neg", "draw"}
TARGETED_SOURCE_KIND = "targeted_simulation_snapchain"
TARGETED_EVIDENCE_SCHEMA = "snake_targeted_evidence:v1"
TARGETED_EVIDENCE_CLASS = "targeted_simulation_observation"
TARGETED_WRITER_ID = "writer:core.snake_targeted_evidence_runner:v1"


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


def default_source_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_SOURCE_PATH") or os.environ.get("OROMA_GAP_REPLAY_EVIDENCE_PROBE_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_SOURCE_NAME).resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_STATE_PATH")
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


def _table_count(con: sqlite3.Connection, table: str) -> int:
    if not _table_exists(con, table):
        return 0
    try:
        return _as_int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
    except Exception:
        return 0


def _load_json_file(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data if isinstance(data, dict) else None), None if isinstance(data, dict) else "source_not_json_object"
    except FileNotFoundError:
        return None, "source_missing"
    except Exception as exc:
        return None, f"source_load_failed:{type(exc).__name__}:{exc}"


def _source_age(source: Mapping[str, Any], source_path: Path, now_ts: int) -> Optional[int]:
    ts = _as_int(source.get("generated_at_ts"), 0)
    if ts > 0:
        return max(0, int(now_ts - ts))
    try:
        return max(0, int(now_ts - int(source_path.stat().st_mtime)))
    except Exception:
        return None


def _apply_oroma_state_ownership(path: Path) -> None:
    if os.geteuid() != 0:
        return
    try:
        uid = pwd.getpwnam("oroma").pw_uid
        gid = grp.getgrnam("oroma").gr_gid
        os.chown(str(path), uid, gid)
    except Exception:
        return


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    try:
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
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _normalize_outcome(raw: Any) -> Optional[str]:
    r = str(raw or "").strip().lower()
    if r in ("pos", "positive", "win", "success", "+1", "1", "good", "reward_pos"):
        return "pos"
    if r in ("neg", "negative", "loss", "fail", "failure", "-1", "bad", "reward_neg"):
        return "neg"
    if r in ("draw", "neutral", "0", "tie", "unknown_neutral"):
        return "draw"
    return None


def _targeted_gate(candidate: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate the TOCTOU-verified targeted-evidence contract without reinterpreting credit.

    The replay probe is responsible for reloading and physically re-verifying the
    targeted SnapChain. This queue gate only accepts that result when every
    exported integrity flag is explicit and consistent. It never derives a later
    event back onto the intervention step and therefore cannot become a second
    credit-assignment implementation.
    """
    adapter = candidate.get("adapter_payload") if isinstance(candidate.get("adapter_payload"), Mapping) else {}
    lineage = adapter.get("lineage") if isinstance(adapter.get("lineage"), Mapping) else {}
    capability = adapter.get("capability") if isinstance(adapter.get("capability"), Mapping) else {}
    toctou = adapter.get("targeted_toctou") if isinstance(adapter.get("targeted_toctou"), Mapping) else {}
    source_kind = _safe_str(lineage.get("source_kind") or capability.get("source_kind"), 120)
    if source_kind != TARGETED_SOURCE_KIND:
        return None, []

    reasons: List[str] = []
    required_true = (
        "ok", "source_reloaded", "request_state_matches", "request_action_matches",
        "capability_digest_matches", "capability_experiment_matches", "capability_outcome_matches",
        "capability_learning_intent_matches",
    )
    for key in required_true:
        if not bool(toctou.get(key)):
            reasons.append(f"targeted_toctou_{key}_not_true")
    if not bool(adapter.get("toctou_verified")):
        reasons.append("targeted_toctou_verified_not_true")
    if not bool(adapter.get("replay_possible")):
        reasons.append("targeted_replay_not_possible")

    evidence_schema = _safe_str(capability.get("evidence_schema"), 160)
    evidence_class = _safe_str(capability.get("evidence_class"), 160)
    writer_id = _safe_str(capability.get("writer_id"), 200)
    continuation_protocol = _safe_str(capability.get("continuation_protocol"), 200)
    continuation_protocol_version = _safe_str(capability.get("continuation_protocol_version"), 80)
    expected_continuation_version = SUPPORTED_CONTINUATION_PROTOCOLS.get(continuation_protocol)
    experiment_id = _safe_str(toctou.get("experiment_id") or capability.get("experiment_id"), 240)
    evidence_digest = _safe_str(toctou.get("evidence_digest") or capability.get("evidence_digest"), 240)
    snapchain_id = _as_int(toctou.get("snapchain_id") or lineage.get("snapchain_id"), 0)
    targeted_outcome = _normalize_outcome(toctou.get("outcome"))
    candidate_outcome = _normalize_outcome(candidate.get("simulated_or_replayed_outcome") or candidate.get("outcome"))
    intent = toctou.get("learning_intent_lineage") if isinstance(toctou.get("learning_intent_lineage"), Mapping) else {}

    checks = (
        (evidence_schema == TARGETED_EVIDENCE_SCHEMA, "targeted_evidence_schema_invalid"),
        (evidence_class == TARGETED_EVIDENCE_CLASS, "targeted_evidence_class_invalid"),
        (writer_id == TARGETED_WRITER_ID, "targeted_writer_id_invalid"),
        (expected_continuation_version is not None, "targeted_continuation_protocol_invalid"),
        (continuation_protocol_version == str(expected_continuation_version or ""), "targeted_continuation_protocol_version_invalid"),
        (bool(experiment_id), "targeted_experiment_id_missing"),
        (bool(evidence_digest), "targeted_evidence_digest_missing"),
        (snapchain_id > 0, "targeted_snapchain_id_missing"),
        (targeted_outcome is not None, "targeted_outcome_missing"),
        (targeted_outcome == candidate_outcome, "targeted_candidate_outcome_mismatch"),
        (_as_int(lineage.get("snapchain_id"), 0) == snapchain_id, "targeted_lineage_snapchain_mismatch"),
        (_safe_str(lineage.get("state_hash"), 4000) == _safe_str(candidate.get("state_hash"), 4000), "targeted_lineage_state_mismatch"),
        (_safe_str(lineage.get("action"), 160) == _safe_str(candidate.get("action"), 160), "targeted_lineage_action_mismatch"),
        (_as_int(intent.get("promotion_id"), 0) == _as_int(candidate.get("id"), 0), "targeted_learning_intent_promotion_id_mismatch"),
        (_safe_str(intent.get("promotion_signature"), 160) == _safe_str(candidate.get("promotion_signature"), 160), "targeted_learning_intent_promotion_signature_mismatch"),
        (_safe_str(intent.get("request_signature"), 160) == _safe_str(candidate.get("request_signature"), 160), "targeted_learning_intent_request_signature_mismatch"),
        (_safe_str(intent.get("promotion_bucket"), 120) == _safe_str(candidate.get("promotion_bucket"), 120), "targeted_learning_intent_bucket_mismatch"),
        (_safe_str(intent.get("namespace"), 160) == _safe_str(candidate.get("namespace"), 160), "targeted_learning_intent_namespace_mismatch"),
        (_safe_str(intent.get("state_hash"), 4000) == _safe_str(candidate.get("state_hash"), 4000), "targeted_learning_intent_state_mismatch"),
        (_safe_str(intent.get("primary_action"), 160) == _safe_str(candidate.get("action"), 160), "targeted_learning_intent_action_mismatch"),
        (_safe_str(intent.get("target"), 80) == _safe_str(candidate.get("target"), 80), "targeted_learning_intent_target_mismatch"),
    )
    for ok, reason in checks:
        if not ok:
            reasons.append(reason)

    identity = {
        "source_kind": TARGETED_SOURCE_KIND,
        "snapchain_id": snapchain_id,
        "experiment_id": experiment_id,
        "evidence_digest": evidence_digest,
        "evidence_schema": evidence_schema,
        "evidence_class": evidence_class,
        "writer_id": writer_id,
        "continuation_protocol": continuation_protocol,
        "result_status": _safe_str(toctou.get("result_status"), 120),
        "event": _safe_str(toctou.get("event"), 120),
        "steps_total": _as_int(toctou.get("steps_total"), 0),
        "outcome": targeted_outcome,
        "source_state_hash": _safe_str(lineage.get("state_hash"), 4000),
        "target_action": _safe_str(lineage.get("action"), 160),
        "capability_version": _safe_str(lineage.get("capability_version") or capability.get("capability_version"), 160),
        "probe_outcome_reason": _safe_str(adapter.get("outcome_reason"), 200),
        "learning_intent_lineage": json.loads(json.dumps(dict(intent), ensure_ascii=False)) if intent else {},
    }
    return identity, reasons


def _canonical_evidence_payload(candidate: Mapping[str, Any], source: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    targeted_identity, targeted_reasons = _targeted_gate(candidate)
    if targeted_identity is not None:
        return {
            "queue_version": VERSION,
            "probe_version": _safe_str(source.get("version"), 160),
            "probe_mode": _safe_str(source.get("mode"), 160),
            "replay_probe_status": _safe_str(candidate.get("replay_probe_status"), 80),
            "replay_source": _safe_str(candidate.get("replay_source"), 200),
            "targeted": targeted_identity,
        }, targeted_reasons
    return {
        "queue_version": VERSION,
        "probe_version": _safe_str(source.get("version"), 160),
        "probe_mode": _safe_str(source.get("mode"), 160),
        "replay_probe_status": _safe_str(candidate.get("replay_probe_status"), 80),
        "replay_source": _safe_str(candidate.get("replay_source"), 200),
        "recommendation": _safe_str(candidate.get("recommendation"), 120),
        "recommendation_reason": _safe_str(candidate.get("recommendation_reason"), 240),
        "source_kind": _safe_str(((candidate.get("adapter_payload") or {}).get("lineage") or {}).get("source_kind") if isinstance(candidate.get("adapter_payload"), Mapping) else "", 120),
    }, []


def _outcome_signature(candidate: Mapping[str, Any], outcome: str, evidence_payload: Mapping[str, Any]) -> str:
    """Return a stable semantic ticket identity.

    Volatile policy snapshots, scan counters, timings and complete adapter dumps
    are deliberately excluded. Re-running the same verified experiment therefore
    resolves to the same UNIQUE outcome_signature even when unrelated policy or
    observability state has changed.
    """
    targeted = evidence_payload.get("targeted") if isinstance(evidence_payload.get("targeted"), Mapping) else {}
    payload = {
        "promotion_signature": _safe_str(candidate.get("promotion_signature"), 160),
        "request_signature": _safe_str(candidate.get("request_signature"), 160),
        "namespace": _safe_str(candidate.get("namespace"), 160),
        "state_hash": _safe_str(candidate.get("state_hash"), 4000),
        "action": _safe_str(candidate.get("action"), 160),
        "outcome": outcome,
        "source_kind": _safe_str(targeted.get("source_kind") or evidence_payload.get("source_kind"), 120),
        "snapchain_id": _as_int(targeted.get("snapchain_id"), 0),
        "experiment_id": _safe_str(targeted.get("experiment_id"), 240),
        "evidence_digest": _safe_str(targeted.get("evidence_digest"), 240),
        "replay_source": _safe_str(candidate.get("replay_source") or candidate.get("evidence_source"), 200),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _schema_statements() -> List[Tuple[str, Sequence[Any]]]:
    return [
        (f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_signature TEXT NOT NULL UNIQUE,
            promotion_signature TEXT,
            request_signature TEXT,
            promotion_id INTEGER,
            target TEXT,
            namespace TEXT,
            state_hash TEXT,
            action TEXT,
            outcome TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            evidence_source TEXT,
            replay_source TEXT,
            status TEXT NOT NULL DEFAULT 'outcome_ready',
            policy_write_allowed INTEGER NOT NULL DEFAULT 0,
            source_probe_ts INTEGER,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            meta_json TEXT
        )
        """, ()),
        (f"CREATE INDEX IF NOT EXISTS idx_gap_evidence_outcome_queue_status ON {TABLE_NAME}(status, created_ts)", ()),
        (f"CREATE INDEX IF NOT EXISTS idx_gap_evidence_outcome_queue_ns_state ON {TABLE_NAME}(namespace, state_hash, action)", ()),
        (f"CREATE INDEX IF NOT EXISTS idx_gap_evidence_outcome_queue_promotion ON {TABLE_NAME}(promotion_signature)", ()),
    ]


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
        return False, f"dbwriter_error:{exc}"


def _iter_ready_candidates(source: Mapping[str, Any], limit: int, min_confidence: float, allow_draw: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows = source.get("candidates") if isinstance(source.get("candidates"), list) else []
    ready: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        c = dict(raw)
        reasons: List[str] = []
        outcome = _normalize_outcome(c.get("simulated_or_replayed_outcome") or c.get("selected_outcome") or c.get("outcome"))
        conf = _as_float(c.get("outcome_confidence"), 0.0)
        if not bool(c.get("ready_for_outcome_queue")):
            reasons.append("not_marked_ready_for_outcome_queue")
        if not outcome:
            reasons.append("missing_or_invalid_outcome")
        elif outcome == "draw" and not allow_draw:
            reasons.append("draw_outcome_not_allowed")
        if conf < float(min_confidence):
            reasons.append("confidence_below_min")
        if not _safe_str(c.get("promotion_signature"), 160):
            reasons.append("missing_promotion_signature")
        if not _safe_str(c.get("namespace"), 160) or not _safe_str(c.get("state_hash"), 4000) or not _safe_str(c.get("action"), 160):
            reasons.append("identity_incomplete")
        if reasons:
            c["outcome_queue_status"] = "blocked"
            c["blocked_reasons"] = reasons
            blocked.append(c)
            continue
        evidence_payload, targeted_reasons = _canonical_evidence_payload(c, source)
        if targeted_reasons:
            c["outcome_queue_status"] = "blocked"
            c["blocked_reasons"] = targeted_reasons
            blocked.append(c)
            continue
        c["outcome"] = outcome
        c["outcome_confidence"] = conf
        c["evidence_payload"] = evidence_payload
        c["outcome_signature"] = _outcome_signature(c, outcome, evidence_payload)
        c["outcome_queue_status"] = "outcome_ready"
        ready.append(c)
        if len(ready) >= int(limit):
            break
    return ready, blocked


def _insert_statement(candidate: Mapping[str, Any], source_ts: int, now_ts: int) -> Tuple[str, Sequence[Any]]:
    meta = {
        "version": VERSION,
        "source": "gap_replay_evidence_probe",
        "policy_write_allowed_here": False,
        "requires_future_gate": "gap_policy_mini_write_gate_or_promotion_enrichment",
        "evidence_payload": candidate.get("evidence_payload") if isinstance(candidate.get("evidence_payload"), dict) else {},
        "raw_candidate": {k: candidate.get(k) for k in (
            "id", "promotion_bucket", "score", "state_hash_format", "state_schema_guess", "action_format",
            "replay_possible", "replay_probe_status", "recommendation", "recommendation_reason",
            "toctou_verified",
        )},
        "targeted_identity": ((candidate.get("evidence_payload") or {}).get("targeted")
                              if isinstance(candidate.get("evidence_payload"), Mapping) else None),
    }
    return (
        f"""
        INSERT OR IGNORE INTO {TABLE_NAME} (
            outcome_signature, promotion_signature, request_signature, promotion_id,
            target, namespace, state_hash, action, outcome, confidence,
            evidence_source, replay_source, status, policy_write_allowed,
            source_probe_ts, created_ts, updated_ts, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outcome_ready', 0, ?, ?, ?, ?)
        """,
        (
            _safe_str(candidate.get("outcome_signature"), 128),
            _safe_str(candidate.get("promotion_signature"), 160),
            _safe_str(candidate.get("request_signature"), 160),
            _as_int(candidate.get("id"), 0),
            _safe_str(candidate.get("target"), 80),
            _safe_str(candidate.get("namespace"), 160),
            _safe_str(candidate.get("state_hash"), 4000),
            _safe_str(candidate.get("action"), 160),
            _safe_str(candidate.get("outcome"), 40),
            _as_float(candidate.get("outcome_confidence"), 0.0),
            "gap_replay_evidence_probe",
            _safe_str(candidate.get("replay_source"), 200),
            int(source_ts),
            int(now_ts), int(now_ts),
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
        ),
    )


def _queue_counts_ro(con: sqlite3.Connection) -> Dict[str, Any]:
    if not _table_exists(con, TABLE_NAME):
        return {"exists": False, "total": 0, "by_outcome_status": []}
    rows = con.execute(
        f"SELECT outcome,status,count(*) AS c FROM {TABLE_NAME} GROUP BY outcome,status ORDER BY outcome,status"
    ).fetchall()
    return {"exists": True, "total": _table_count(con, TABLE_NAME), "by_outcome_status": [(r[0], r[1], int(r[2])) for r in rows]}


def _validate_current_promotions(
    con: sqlite3.Connection, candidates: Sequence[Mapping[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fail closed unless replay evidence still belongs to a current promotion.

    Promotion queues are live, bounded review windows. A State-JSON can remain on
    disk after the corresponding promotion row has expired or been replaced.
    Freshness alone is therefore insufficient: promotion_signature and the full
    namespace/state/action identity must still match the current DB row.
    """
    valid: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    if not _table_exists(con, PROMOTION_TABLE):
        for raw in candidates:
            item = dict(raw)
            item["outcome_queue_status"] = "blocked"
            item["blocked_reasons"] = ["current_promotion_table_missing"]
            blocked.append(item)
        return valid, blocked

    sql = f"""
        SELECT id,promotion_signature,request_signature,target,promotion_bucket,
               namespace,state_hash,primary_action,status
        FROM {PROMOTION_TABLE}
        WHERE promotion_signature=?
        LIMIT 1
    """
    for raw in candidates:
        item = dict(raw)
        sig = _safe_str(item.get("promotion_signature"), 160)
        row = con.execute(sql, (sig,)).fetchone() if sig else None
        reasons: List[str] = []
        if row is None:
            reasons.append("promotion_candidate_no_longer_current")
        else:
            checks = (
                ("request_signature", _safe_str(item.get("request_signature"), 160), _safe_str(row["request_signature"], 160)),
                ("target", _safe_str(item.get("target"), 80), _safe_str(row["target"], 80)),
                ("promotion_bucket", _safe_str(item.get("promotion_bucket"), 120), _safe_str(row["promotion_bucket"], 120)),
                ("namespace", _safe_str(item.get("namespace"), 160), _safe_str(row["namespace"], 160)),
                ("state_hash", _safe_str(item.get("state_hash"), 4000), _safe_str(row["state_hash"], 4000)),
                ("action", _safe_str(item.get("action"), 160), _safe_str(row["primary_action"], 160)),
            )
            for field, source_value, current_value in checks:
                if source_value != current_value:
                    reasons.append(f"current_promotion_identity_mismatch:{field}")
            if _safe_str(row["status"], 80) != "promotion_review":
                reasons.append("current_promotion_status_not_review")
        if reasons:
            item["outcome_queue_status"] = "blocked"
            item["blocked_reasons"] = reasons
            blocked.append(item)
            continue
        item["current_promotion_id"] = _as_int(row["id"], 0)
        item["current_promotion_verified"] = True
        valid.append(item)
    return valid, blocked


def run_once(
    *,
    db_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    start = time.time()
    now = _now_ts()
    base = _base_dir()
    db = (db_path or default_db_path(base)).resolve()
    source_p = (source_path or default_source_path(base)).resolve()
    state_p = (state_path or default_state_path(base)).resolve()

    lim = int(limit if limit is not None else _env_int("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_LIMIT", 10))
    tk = int(topk if topk is not None else _env_int("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_TOPK", lim))
    min_conf = float(min_confidence if min_confidence is not None else _env_float("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_MIN_CONFIDENCE", 0.50))
    max_age = _env_int("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_MAX_AGE_SEC", 7200)
    allow_stale = _env_bool("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_ALLOW_STALE", False)
    allow_draw = _env_bool("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_ALLOW_DRAW", False)
    write_enable = _env_bool("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_WRITE_ENABLE", True)
    confirm_required = _env_str("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_CONFIRM_REQUIRED", "GAP_EVIDENCE_OUTCOME_QUEUE_REVIEWED")
    confirm_value = _env_str("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_CONFIRM", "")
    confirm_ok = bool(confirm_required and confirm_value == confirm_required)
    timeout_ms = _env_int("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_DBW_TIMEOUT_MS", 15000)
    ping_timeout_ms = _env_int("OROMA_GAP_EVIDENCE_OUTCOME_QUEUE_DBW_PING_TIMEOUT_MS", 500)

    source, source_err = _load_json_file(source_p)
    source_age: Optional[int] = _source_age(source or {}, source_p, now) if source is not None else None
    source_stale = bool(source_age is not None and max_age > 0 and source_age > int(max_age))

    out: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "mode": "dbwriter_outcome_queue_gate",
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "db_path": str(db),
        "source_path": str(source_p),
        "state_path": str(state_p),
        "source": {
            "ok": source is not None and not source_err,
            "error": source_err,
            "generated_at_ts": (source or {}).get("generated_at_ts") if isinstance(source, dict) else None,
            "generated_at_iso": (source or {}).get("generated_at_iso") if isinstance(source, dict) else None,
            "mode": (source or {}).get("mode") if isinstance(source, dict) else None,
            "age_sec": source_age,
            "stale": source_stale,
        },
        "gate": {
            "write_enable": write_enable,
            "confirm_required": confirm_required,
            "confirm_ok": confirm_ok,
            "min_confidence": min_conf,
            "allow_draw": allow_draw,
            "max_age_sec": max_age,
            "allow_stale": allow_stale,
        },
        "safety": {
            "db_access": "read_only_plus_dbwriter_own_outcome_queue_write",
            "db_writes": bool(write_enable),
            "local_sqlite_write_fallback": False,
            "policy_writes": False,
            "rules_writes": False,
            "runner_starts": False,
            "replay_starts": False,
            "global_replay_starts": False,
            "dream_starts": False,
            "schema_changes": "own_outcome_queue_table_only_when_write_ready",
            "current_promotion_identity_required": True,
            "state_json_write": True,
        },
        "candidates": [],
        "blocked": [],
        "errors": [],
    }

    if source is None:
        out["ok"] = False
        out["summary"] = {"ok": False, "blocked_reason": source_err or "source_unavailable", "state_written": False, "dt_sec": round(time.time() - start, 3)}
        written, err = atomic_write_json(state_p, out)
        out["summary"]["state_written"] = bool(written)
        if err:
            out["summary"]["state_write_error"] = err
        return out

    ready_pre_identity, blocked = _iter_ready_candidates(source, lim, min_conf, allow_draw)
    ready: List[Dict[str, Any]] = []
    identity_blocked: List[Dict[str, Any]] = []
    queue_before: Dict[str, Any] = {"exists": False, "total": 0, "by_outcome_status": []}
    try:
        con = _connect_ro(db)
        queue_before = _queue_counts_ro(con)
        ready, identity_blocked = _validate_current_promotions(con, ready_pre_identity)
        con.close()
    except Exception as exc:
        out["errors"].append({"where": "current_promotion_validation", "error": str(exc)})
        for raw in ready_pre_identity:
            item = dict(raw)
            item["outcome_queue_status"] = "blocked"
            item["blocked_reasons"] = ["current_promotion_validation_failed"]
            identity_blocked.append(item)
    blocked.extend(identity_blocked)
    out["candidates"] = ready[: max(0, int(tk))]
    out["blocked"] = blocked[: max(0, int(tk))]

    dbw_ready, dbw_reason = _dbwriter_ready(ping_timeout_ms)
    if not write_enable:
        write_block_reason = "write_gate_disabled"
    elif not confirm_ok:
        write_block_reason = "confirm_token_missing_or_wrong"
    elif not dbw_ready:
        write_block_reason = dbw_reason
    elif source_stale and not allow_stale:
        write_block_reason = "source_stale"
    elif not ready:
        write_block_reason = "no_current_ready_outcomes"
    else:
        write_block_reason = "write_ready"
    write_ready = bool(write_enable and confirm_ok and dbw_ready and ready and not (source_stale and not allow_stale))

    stmts: List[Tuple[str, Sequence[Any]]] = []
    if write_ready:
        stmts.extend(_schema_statements())
        source_ts = _as_int(source.get("generated_at_ts"), 0)
        for c in ready:
            stmts.append(_insert_statement(c, source_ts, now))

    transaction_ok = False
    transaction_error: Optional[str] = None
    if write_ready and stmts:
        try:
            assert db_writer_client is not None
            db_writer_client.transaction(
                stmts,
                tag="gap_evidence_outcome_queue.write",
                priority="low",
                timeout_ms=int(timeout_ms),
                db="oroma",
            )
            transaction_ok = True
        except Exception as exc:
            transaction_error = f"{type(exc).__name__}: {exc}"
            out["errors"].append({"where": "dbwriter_transaction", "error": transaction_error})

    queue_after: Dict[str, Any] = queue_before
    try:
        con2 = _connect_ro(db)
        queue_after = _queue_counts_ro(con2)
        con2.close()
    except Exception as exc:
        out["errors"].append({"where": "queue_counts_after", "error": str(exc)})

    by_outcome: Dict[str, int] = {}
    for c in ready:
        k = _safe_str(c.get("outcome"), 40)
        by_outcome[k] = int(by_outcome.get(k, 0)) + 1
    targeted_ready_total = sum(
        1 for c in ready
        if isinstance(c.get("evidence_payload"), Mapping)
        and isinstance(c.get("evidence_payload", {}).get("targeted"), Mapping)
    )
    targeted_blocked_total = sum(
        1 for b in blocked
        if any(str(r).startswith("targeted_") for r in (b.get("blocked_reasons") or []))
    )
    blocked_reasons: Dict[str, int] = {}
    for b in blocked:
        for r in b.get("blocked_reasons") if isinstance(b.get("blocked_reasons"), list) else ["blocked"]:
            blocked_reasons[str(r)] = int(blocked_reasons.get(str(r), 0)) + 1

    out["gate"].update({
        "dbwriter_ready": dbw_ready,
        "dbwriter_reason": dbw_reason,
        "write_ready": write_ready,
        "write_block_reason": write_block_reason,
    })
    out["summary"] = {
        "ok": not bool(transaction_error),
        "source_candidates_loaded": len(source.get("candidates") if isinstance(source.get("candidates"), list) else []),
        "ready_before_current_promotion_check": len(ready_pre_identity),
        "current_promotion_valid_total": len(ready),
        "promotion_identity_blocked_total": len(identity_blocked),
        "ready_for_queue_total": len(ready),
        "targeted_ready_total": targeted_ready_total,
        "targeted_blocked_total": targeted_blocked_total,
        "blocked_total": len(blocked),
        "per_outcome_counts": by_outcome,
        "blocked_reason_counts": blocked_reasons,
        "insert_attempted": len(ready) if write_ready else 0,
        "queued_or_existing": len(ready) if transaction_ok else 0,
        "queue_before": queue_before,
        "queue_after": queue_after,
        "transaction_ok": transaction_ok,
        "transaction_error": transaction_error,
        "write_ready": write_ready,
        "write_block_reason": write_block_reason,
        "policy_writes": 0,
        "db_writes": 1 if transaction_ok else 0,
        "runner_starts": 0,
        "replay_starts": 0,
        "global_replay_starts": 0,
        "dream_starts": 0,
        "state_written": False,
        "dt_sec": round(time.time() - start, 3),
    }

    written, state_err = atomic_write_json(state_p, out)
    out["summary"]["state_written"] = bool(written)
    if state_err:
        out["summary"]["state_write_error"] = state_err
        out["errors"].append({"where": "state_write", "error": state_err})
    out["summary"]["dt_sec"] = round(time.time() - start, 3)
    return out


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run_once(), ensure_ascii=False, indent=2, sort_keys=True))

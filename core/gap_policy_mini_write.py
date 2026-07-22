#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_policy_mini_write.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Policy Mini-Write Gate · DBWriter-only · Ledger · Fail-Closed
# Version:   v0.3.2-stable-ledger-signature
# Stand:     2026-07-21
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist das finale technische Gate am Ende der konservativen
# Gap-Learning-Kette:
#
#   knowledge_gaps
#       -> gap_learning_focus.json
#       -> gap_focus_consumer.json
#       -> gap_focus_shadow_plan.json
#       -> gap_focus_evidence_queue
#       -> gap_evidence_review.json
#       -> gap_evidence_validation.json
#       -> gap_policy_promotion_queue
#       -> gap_evidence_outcome_queue
#       -> gap_policy_mini_write_ledger + policy_rule_evidence_links
#       -> optional policy_rules Mini-Write
#
# WICHTIGER FACHLICHER GRUNDSATZ
# ------------------------------
# Eine Gap-Lücke wird NICHT durch Wahrscheinlichkeit, Q-Wert oder Vermutung
# geschlossen. Wahrscheinlichkeiten helfen nur bei Priorisierung und Auswahl
# der nächsten Tests. Ein policy_rules-Write darf erst erfolgen, wenn ein
# belastbares Evidence-Outcome vorhanden ist (z.B. aus späterem Replay-/Dream-
# oder Direct-Step-Credit-Pfad). Kandidaten, die nur aus Gap/Validation stammen,
# werden deshalb sauber geblockt und optional im Ledger dokumentiert.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhängigkeiten.
# - Promotion-/Policy-Lesen nur read-only via SQLite URI mode=ro.
# - Jeder DB-Write ausschließlich via DBWriter-Client; kein lokaler SQLite-
#   Schreibfallback.
# - Schemaänderung nur eigene Ledger-Tabelle gap_policy_mini_write_ledger.
# - policy_rules-Write nur bei ALLEN Gates: ENABLE=1, Confirm-Token, DBWriter,
#   Namespace-Allowlist, Promotion-Bucket-Allowlist, echte Evidence-Outcome-
#   Quelle, Dedupe-Signatur und Max-Writes-Budget.
# - Runner-, Replay- und Dream-Jobs werden niemals gestartet.
# - Dedupe: write_signature UNIQUE; wiederholte Läufe dürfen nicht doppelt in
#   policy_rules schreiben.
# - Outcome- und Promotion-Freshness werden unabhängig geprüft; ein frisches
#   Outcome darf keine fachlich veraltete Promotion reaktivieren.
# - Erfolgreicher Policy-Write, Evidence-Link, Outcome-Abschluss, Promotion-
#   Abschluss und Ledger-Abschluss erfolgen in derselben DBWriter-Transaktion.
# - State-Write atomar nach data/state/gap_policy_mini_write_gate.json.
# - Root-Manual-Läufe setzen State-Datei best-effort auf oroma:oroma 664.
#
# DEFAULT-SICHERHEIT
# ------------------
# Das Gate ist in .env.systemd standardmäßig installiert, aber policy-write-
# seitig deaktiviert:
#
#   OROMA_GAP_POLICY_MINI_WRITE_ENABLE=0
#   OROMA_GAP_POLICY_MINI_WRITE_CONFIRM=
#
# Selbst wenn es aktiviert wird, akzeptiert es standardmäßig ausschließlich
# aktuelle, nicht-stale Zeilen aus gap_evidence_outcome_queue. Der historische
# Promotion-Meta-Pfad ist nur als explizit aktivierbarer Legacy-Fallback erhalten.
# Dadurch kann das Modul fail-closed in Produktion beobachtet werden.
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    from core import db_writer_client
except Exception:  # pragma: no cover - defensive partial deployment
    db_writer_client = None  # type: ignore

VERSION = "v0.3.2-stable-ledger-signature"
PROMOTION_TABLE = "gap_policy_promotion_queue"
POLICY_TABLE = "policy_rules"
LEDGER_TABLE = "gap_policy_mini_write_ledger"
OUTCOME_TABLE = "gap_evidence_outcome_queue"
LINK_TABLE = "policy_rule_evidence_links"
WRITER_ID = "writer:core.gap_policy_mini_write:v0.3"
CONTRACT_VERSION = "learning_contracts:2026-07-11"
BOUNDARY_VERSION = "policy_mutation_boundary:2026-07-11"
DEFAULT_STATE_NAME = "gap_policy_mini_write_gate.json"
DEFAULT_BUCKETS = (
    "promotion_candidate_replay",
    "promotion_candidate_dream",
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
    explicit = os.environ.get("OROMA_GAP_POLICY_MINI_WRITE_STATE_PATH")
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


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    v = str(value or "")
    for pat in patterns:
        p = str(pat or "").strip()
        if not p:
            continue
        if p == "*" or fnmatch.fnmatchcase(v, p):
            return True
    return False


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
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(str(tmp), str(p))
    try:
        os.chmod(str(p), 0o664)
    except Exception:
        pass
    _apply_oroma_state_ownership(p)


def _schema_statements() -> List[Tuple[str, Sequence[Any]]]:
    return [
        (f"""
        CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            write_signature TEXT NOT NULL UNIQUE,
            promotion_signature TEXT NOT NULL,
            promotion_id INTEGER,
            request_signature TEXT,
            target TEXT,
            promotion_bucket TEXT,
            namespace TEXT,
            state_hash TEXT,
            action TEXT,
            outcome TEXT,
            status TEXT NOT NULL,
            policy_written INTEGER NOT NULL DEFAULT 0,
            blocked_reason TEXT,
            n_inc INTEGER NOT NULL DEFAULT 0,
            pos_inc INTEGER NOT NULL DEFAULT 0,
            neg_inc INTEGER NOT NULL DEFAULT 0,
            draw_inc INTEGER NOT NULL DEFAULT 0,
            q_before REAL,
            n_before INTEGER,
            q_after_est REAL,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            meta_json TEXT
        )
        """, ()),
        (f"CREATE INDEX IF NOT EXISTS idx_gap_policy_mini_write_status ON {LEDGER_TABLE}(status, created_ts)", ()),
        (f"CREATE INDEX IF NOT EXISTS idx_gap_policy_mini_write_ns_state ON {LEDGER_TABLE}(namespace, state_hash, action)", ()),
        (f"CREATE INDEX IF NOT EXISTS idx_gap_policy_mini_write_promotion ON {LEDGER_TABLE}(promotion_signature)", ()),
        (f"""
        CREATE TABLE IF NOT EXISTS {LINK_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_signature TEXT NOT NULL UNIQUE,
            policy_rule_id INTEGER NOT NULL,
            namespace TEXT NOT NULL,
            state_hash TEXT NOT NULL,
            action TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            outcome_queue_id INTEGER,
            outcome_signature TEXT,
            evidence_class TEXT NOT NULL,
            mutation_type TEXT NOT NULL,
            writer_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            boundary_version TEXT NOT NULL,
            write_signature TEXT NOT NULL,
            created_ts INTEGER NOT NULL,
            meta_json TEXT
        )
        """, ()),
        (f"CREATE INDEX IF NOT EXISTS idx_policy_rule_evidence_rule ON {LINK_TABLE}(policy_rule_id, created_ts)", ()),
        (f"CREATE INDEX IF NOT EXISTS idx_policy_rule_evidence_evidence ON {LINK_TABLE}(evidence_id)", ()),
        (f"CREATE INDEX IF NOT EXISTS idx_policy_rule_evidence_ns_state ON {LINK_TABLE}(namespace, state_hash, action)", ()),
    ]


def _canonical_evidence_id(outcome_signature: str) -> str:
    sig = _safe_str(outcome_signature, 128)
    return "evq:" + sig if sig else ""


def _evidence_class(evidence_source: str, replay_source: str, meta: Mapping[str, Any]) -> str:
    explicit = _safe_str(meta.get("evidence_class") or meta.get("evidence_type"), 80).lower()
    if explicit in ("real_observed", "replay_reconstructed", "dream_counterfactual"):
        return explicit
    text = (str(evidence_source or "") + " " + str(replay_source or "")).lower()
    if "dream" in text:
        return "dream_counterfactual"
    if "replay" in text:
        return "replay_reconstructed"
    if "direct" in text or "observ" in text or "runner" in text:
        return "real_observed"
    return "unknown"


def _targeted_promotion_revalidation_ts(
    row: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> Optional[int]:
    """Return a fresh promotion attestation timestamp from verified targeted evidence.

    The promotion row itself remains immutable. A newer targeted-acquisition
    outcome may attest that the exact promotion identity was revalidated at
    execution time, but only when the immutable learning-intent lineage matches
    every policy-relevant identity field of the joined live promotion/outcome.
    Generic replay, legacy metadata and incomplete lineage never refresh the
    promotion window.
    """
    evidence_payload = meta.get("evidence_payload") if isinstance(meta.get("evidence_payload"), Mapping) else {}
    targeted = evidence_payload.get("targeted") if isinstance(evidence_payload.get("targeted"), Mapping) else {}
    lineage = targeted.get("learning_intent_lineage") if isinstance(targeted.get("learning_intent_lineage"), Mapping) else {}
    if _safe_str(targeted.get("source_kind"), 120) != "targeted_simulation_snapchain":
        return None
    checks = (
        (_as_int(lineage.get("promotion_id"), 0), _as_int(row.get("current_promotion_id") or row.get("promotion_id"), 0)),
        (_safe_str(lineage.get("promotion_signature"), 160), _safe_str(row.get("promotion_signature"), 160)),
        (_safe_str(lineage.get("request_signature"), 160), _safe_str(row.get("request_signature"), 160)),
        (_safe_str(lineage.get("namespace"), 160), _safe_str(row.get("namespace"), 160)),
        (_safe_str(lineage.get("state_hash"), 4000), _safe_str(row.get("state_hash"), 4000)),
        (_safe_str(lineage.get("primary_action"), 160), _safe_str(row.get("action"), 160)),
        (_safe_str(lineage.get("target"), 80), _safe_str(row.get("promotion_target") or row.get("target"), 80)),
        (_safe_str(lineage.get("promotion_bucket"), 120), _safe_str(row.get("promotion_bucket"), 120)),
    )
    if any(not expected or actual != expected for actual, expected in checks):
        return None
    ts = max(_as_int(row.get("source_probe_ts"), 0), _as_int(row.get("updated_ts"), 0))
    return ts if ts > 0 else None


def _load_outcome_candidates(
    con: sqlite3.Connection,
    buckets: Sequence[str],
    limit: int,
) -> Tuple[List[sqlite3.Row], List[str]]:
    errors: List[str] = []
    if not _table_exists(con, OUTCOME_TABLE):
        return [], ["outcome_queue_missing"]
    if not _table_exists(con, PROMOTION_TABLE):
        return [], ["promotion_table_missing"]
    if not _table_exists(con, POLICY_TABLE):
        return [], ["policy_rules_missing"]
    wanted = [b for b in buckets if b] or list(DEFAULT_BUCKETS)
    placeholders = ",".join("?" for _ in wanted)
    sql = f"""
        SELECT
            oq.*,
            pq.id AS current_promotion_id,
            pq.target AS promotion_target,
            pq.promotion_bucket AS promotion_bucket,
            pq.score AS promotion_score,
            pq.status AS promotion_status,
            pq.primary_action AS promotion_action,
            pq.updated_ts AS promotion_updated_ts,
            pq.source_validation_ts AS promotion_source_validation_ts,
            pq.focus_id AS promotion_focus_id,
            pq.plan_id AS promotion_plan_id,
            pq.meta_json AS promotion_meta_json
        FROM {OUTCOME_TABLE} AS oq
        JOIN {PROMOTION_TABLE} AS pq
          ON pq.promotion_signature = oq.promotion_signature
         AND pq.request_signature = oq.request_signature
         AND pq.namespace = oq.namespace
         AND pq.state_hash = oq.state_hash
         AND pq.primary_action = oq.action
        WHERE oq.status IN ('outcome_ready', 'approved_for_policy_gate')
          AND pq.status IN ('promotion_review', 'approved_for_policy_gate')
          AND pq.promotion_bucket IN ({placeholders})
        ORDER BY oq.confidence DESC, pq.score DESC, oq.updated_ts DESC, oq.id ASC
        LIMIT ?
    """
    return list(con.execute(sql, [*wanted, int(limit)]).fetchall()), errors


def _queue_candidate_decision(
    row: sqlite3.Row,
    before: Mapping[str, Any],
    namespace_allowlist: Sequence[str],
    state_schema_allowlist: Sequence[str],
    min_score: float,
    min_confidence: float,
    allow_draw: bool,
    outcome_max_age_sec: int,
    allow_stale_outcome: bool,
    promotion_max_age_sec: int,
    allow_stale_promotion: bool,
    now_ts: int,
) -> Dict[str, Any]:
    r = dict(row)
    meta = _json_loads(r.get("meta_json"))
    namespace = _safe_str(r.get("namespace"), 160)
    state_hash = _safe_str(r.get("state_hash"), 4000)
    action = _safe_str(r.get("action"), 160)
    outcome = _normalize_outcome(_safe_str(r.get("outcome"), 40))
    confidence = _as_float(r.get("confidence"), 0.0)
    score = _as_float(r.get("promotion_score"), 0.0)
    updated_ts = _as_int(r.get("updated_ts"), 0)
    source_probe_ts = _as_int(r.get("source_probe_ts"), 0)
    freshness_ts = max(updated_ts, source_probe_ts)
    age_sec = max(0, int(now_ts - freshness_ts)) if freshness_ts > 0 else None
    promotion_updated_ts = _as_int(r.get("promotion_updated_ts"), 0)
    promotion_source_validation_ts = _as_int(r.get("promotion_source_validation_ts"), 0)
    # Die fachliche Promotion-Freshness folgt bevorzugt der Validierungsbasis.
    # Nur bei historischen Zeilen ohne source_validation_ts wird updated_ts als
    # konservativer Kompatibilitätswert verwendet.
    promotion_freshness_ts = promotion_source_validation_ts or promotion_updated_ts
    targeted_revalidation_ts = _targeted_promotion_revalidation_ts(r, meta)
    if targeted_revalidation_ts is not None:
        promotion_freshness_ts = max(promotion_freshness_ts, targeted_revalidation_ts)
    promotion_age_sec = (
        max(0, int(now_ts - promotion_freshness_ts))
        if promotion_freshness_ts > 0 else None
    )
    blocked: List[str] = []

    if not namespace or not state_hash or not action:
        blocked.append("identity_incomplete")
    if not _matches_any(namespace, namespace_allowlist):
        blocked.append("namespace_not_allowed")
    state_schema = state_hash.split(":", 2)[0:2]
    state_schema = ":".join(state_schema) if len(state_schema) == 2 else ""
    if state_schema_allowlist and not _matches_any(state_schema, state_schema_allowlist):
        blocked.append("state_schema_not_allowed")
    if score < float(min_score):
        blocked.append("score_below_min")
    if confidence < float(min_confidence):
        blocked.append("confidence_below_min")
    if not outcome:
        blocked.append("missing_or_invalid_outcome")
    if outcome == "draw" and not allow_draw:
        blocked.append("draw_outcome_not_allowed")
    if _safe_str(r.get("promotion_status"), 80) not in ("promotion_review", "approved_for_policy_gate"):
        blocked.append("promotion_status_not_eligible")
    if _safe_str(r.get("promotion_bucket"), 120) not in DEFAULT_BUCKETS:
        blocked.append("promotion_bucket_not_policy_candidate")
    if action in ("", "step", "none", "null"):
        blocked.append("action_missing_or_generic")
    if age_sec is None:
        blocked.append("freshness_timestamp_missing")
    elif age_sec > int(outcome_max_age_sec) and not allow_stale_outcome:
        blocked.append("outcome_queue_row_stale")
    if promotion_age_sec is None:
        blocked.append("promotion_freshness_timestamp_missing")
    elif promotion_age_sec > int(promotion_max_age_sec) and not allow_stale_promotion:
        blocked.append("promotion_row_stale")

    outcome_signature = _safe_str(r.get("outcome_signature"), 128)
    evidence_id = _canonical_evidence_id(outcome_signature)
    replay_source = _safe_str(r.get("replay_source"), 200)
    evidence_source = _safe_str(r.get("evidence_source"), 200) or "gap_evidence_outcome_queue"
    evidence_class = _evidence_class(evidence_source, replay_source, meta)
    if not outcome_signature:
        blocked.append("outcome_signature_missing")
    if not evidence_id:
        blocked.append("evidence_id_missing")
    if evidence_class == "unknown":
        blocked.append("evidence_class_unknown")

    n_inc, pos_inc, neg_inc, draw_inc, outcome_q = (0, 0, 0, 0, 0.0)
    if outcome in ("pos", "neg", "draw"):
        n_inc, pos_inc, neg_inc, draw_inc, outcome_q = _increments(outcome)

    evidence_payload = {
        "queue_meta": meta,
        "outcome_queue_id": _as_int(r.get("id"), 0),
        "outcome_signature": outcome_signature,
        "confidence": confidence,
        "evidence_class": evidence_class,
        "replay_source": replay_source,
        "source_probe_ts": source_probe_ts,
        "age_sec": age_sec,
        "promotion_updated_ts": promotion_updated_ts,
        "promotion_source_validation_ts": promotion_source_validation_ts,
        "promotion_age_sec": promotion_age_sec,
        "promotion_focus_id": _safe_str(r.get("promotion_focus_id"), 200),
        "promotion_plan_id": _safe_str(r.get("promotion_plan_id"), 200),
        "promotion_previous_status": _safe_str(r.get("promotion_status"), 80),
        "promotion_new_status": "policy_written",
        "contract_version": CONTRACT_VERSION,
        "boundary_version": BOUNDARY_VERSION,
    }
    normalized = dict(r)
    normalized["primary_action"] = action
    write_sig = _write_signature(normalized, outcome or "blocked", evidence_source, evidence_payload, blocked)
    q_after = _estimate_q(before, pos_inc, neg_inc, draw_inc) if n_inc > 0 else _as_float(before.get("q"), 0.0)
    status = "write_candidate" if not blocked and n_inc > 0 else "blocked"
    return {
        "source_kind": "outcome_queue",
        "outcome_queue_id": _as_int(r.get("id"), 0),
        "outcome_signature": outcome_signature,
        "evidence_id": evidence_id,
        "evidence_class": evidence_class,
        "confidence": confidence,
        "age_sec": age_sec,
        "promotion_age_sec": promotion_age_sec,
        "promotion_targeted_revalidation_ts": targeted_revalidation_ts,
        "promotion_updated_ts": promotion_updated_ts,
        "promotion_source_validation_ts": promotion_source_validation_ts,
        "promotion_focus_id": _safe_str(r.get("promotion_focus_id"), 200),
        "promotion_plan_id": _safe_str(r.get("promotion_plan_id"), 200),
        "promotion_previous_status": _safe_str(r.get("promotion_status"), 80),
        "promotion_new_status": "policy_written",
        "promotion_id": _as_int(r.get("current_promotion_id") or r.get("promotion_id"), 0),
        "promotion_signature": _safe_str(r.get("promotion_signature"), 160),
        "write_signature": write_sig,
        "request_signature": _safe_str(r.get("request_signature"), 160),
        "target": _safe_str(r.get("promotion_target") or r.get("target"), 80),
        "promotion_bucket": _safe_str(r.get("promotion_bucket"), 120),
        "namespace": namespace,
        "state_hash": state_hash,
        "action": action,
        "outcome": outcome,
        "evidence_source": evidence_source,
        "evidence_payload": evidence_payload,
        "status": status,
        "blocked_reasons": blocked,
        "blocked_reason": ",".join(blocked) if blocked else None,
        "policy_before": dict(before),
        "n_inc": n_inc,
        "pos_inc": pos_inc,
        "neg_inc": neg_inc,
        "draw_inc": draw_inc,
        "outcome_q": outcome_q,
        "q_after_est": q_after,
        "score": score,
        "mutation_type": "UPDATE_RULE_STATISTICS" if bool(before.get("available")) else "INSERT_RULE",
        "writer_id": WRITER_ID,
        "contract_version": CONTRACT_VERSION,
        "boundary_version": BOUNDARY_VERSION,
        "execution": {
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_policy": status == "write_candidate",
            "dbwriter_only": True,
        },
    }


def _load_candidates(con: sqlite3.Connection, buckets: Sequence[str], limit: int) -> Tuple[List[sqlite3.Row], List[str]]:
    errors: List[str] = []
    if not _table_exists(con, PROMOTION_TABLE):
        return [], ["promotion_table_missing"]
    if not _table_exists(con, POLICY_TABLE):
        return [], ["policy_rules_missing"]
    wanted = [b for b in buckets if b]
    if not wanted:
        wanted = list(DEFAULT_BUCKETS)
    placeholders = ",".join("?" for _ in wanted)
    sql = f"""
        SELECT * FROM {PROMOTION_TABLE}
        WHERE status IN ('promotion_review', 'approved_for_policy_gate')
          AND promotion_bucket IN ({placeholders})
        ORDER BY score DESC, updated_ts DESC, id ASC
        LIMIT ?
    """
    rows = list(con.execute(sql, [*wanted, int(limit)]).fetchall())
    return rows, errors


def _policy_before(con: sqlite3.Connection, namespace: str, state_hash: str, action: str) -> Dict[str, Any]:
    if not namespace or not state_hash or not action or not _table_exists(con, POLICY_TABLE):
        return {"available": False, "reason": "identity_incomplete_or_policy_missing", "n": 0, "q": 0.0}
    row = con.execute(
        f"SELECT n,pos,neg,draw,q,last_ts FROM {POLICY_TABLE} WHERE namespace=? AND state_hash=? AND action=? LIMIT 1",
        (namespace, state_hash, action),
    ).fetchone()
    if not row:
        return {"available": False, "reason": "policy_action_missing", "n": 0, "pos": 0, "neg": 0, "draw": 0, "q": 0.0}
    return {
        "available": True,
        "n": _as_int(row["n"], 0),
        "pos": _as_int(row["pos"], 0),
        "neg": _as_int(row["neg"], 0),
        "draw": _as_int(row["draw"], 0),
        "q": _as_float(row["q"], 0.0),
        "last_ts": _as_int(row["last_ts"], 0),
    }


def _extract_evidence_outcome(meta: Mapping[str, Any]) -> Tuple[Optional[str], str, Dict[str, Any]]:
    """Findet echte Evidence im Promotion-Meta.

    Aktuelle Gap-only Kandidaten besitzen diese Felder noch nicht. Dann wird
    bewusst geblockt. Zukünftige Replay-/Dream-/Direct-Credit-Stufen können
    eines der unten genannten Felder setzen, ohne dieses Gate umzubauen.
    """
    paths = [
        ("final_evidence", "outcome"),
        ("final_evidence", "policy_outcome"),
        ("evidence_result", "outcome"),
        ("evidence_result", "policy_outcome"),
        ("promotion_evidence", "outcome"),
        ("promotion_evidence", "policy_outcome"),
        ("direct_step_credit", "outcome"),
    ]
    for root, key in paths:
        obj = meta.get(root)
        if isinstance(obj, dict) and obj.get(key) is not None:
            raw = _safe_str(obj.get(key), 80).lower()
            norm = _normalize_outcome(raw)
            if norm:
                return norm, f"meta.{root}.{key}", dict(obj)
    raw2 = meta.get("policy_outcome") or meta.get("outcome")
    if raw2 is not None:
        norm = _normalize_outcome(_safe_str(raw2, 80).lower())
        if norm:
            return norm, "meta.policy_outcome_or_outcome", {"raw": raw2}
    return None, "missing_evidence_outcome", {}


def _normalize_outcome(raw: str) -> Optional[str]:
    r = str(raw or "").strip().lower()
    if r in ("pos", "positive", "win", "success", "+1", "1", "good", "reward_pos"):
        return "pos"
    if r in ("neg", "negative", "loss", "fail", "failure", "-1", "bad", "reward_neg"):
        return "neg"
    if r in ("draw", "neutral", "0", "tie", "unknown_neutral"):
        return "draw"
    return None


def _increments(outcome: str) -> Tuple[int, int, int, int, float]:
    if outcome == "pos":
        return 1, 1, 0, 0, 1.0
    if outcome == "neg":
        return 1, 0, 1, 0, -1.0
    return 1, 0, 0, 1, 0.0


_VOLATILE_SIGNATURE_KEYS = frozenset({
    "age_sec",
    "promotion_age_sec",
})


def _stable_signature_value(value: Any) -> Any:
    """Return a deterministic evidence identity without observation-time drift.

    Ledger idempotency must describe the fachliche write/block decision, not the
    wall-clock age at which the same candidate happened to be inspected. The two
    age fields remain fully available in ``meta_json`` for diagnostics, but they
    must never generate a fresh UNIQUE ``write_signature`` every scheduler tick.
    Other evidence fields are preserved recursively so source lineage, contracts,
    confidence, queue identity and immutable timestamps remain auditable.
    """
    if isinstance(value, Mapping):
        return {
            str(key): _stable_signature_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _VOLATILE_SIGNATURE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_signature_value(item) for item in value]
    return value


def _write_signature(
    row: Mapping[str, Any],
    outcome: str,
    evidence_source: str,
    evidence_payload: Mapping[str, Any],
    blocked_reasons: Optional[Sequence[str]] = None,
) -> str:
    payload = {
        "promotion_signature": _safe_str(row.get("promotion_signature"), 160),
        "request_signature": _safe_str(row.get("request_signature"), 160),
        "namespace": _safe_str(row.get("namespace"), 160),
        "state_hash": _safe_str(row.get("state_hash"), 4000),
        "action": _safe_str(row.get("action") or row.get("primary_action"), 160),
        "evidence_id": _safe_str(row.get("evidence_id"), 200),
        "outcome_signature": _safe_str(row.get("outcome_signature"), 128),
        "source_kind": _safe_str(row.get("source_kind"), 80),
        "outcome": _safe_str(outcome, 40),
        "evidence_source": _safe_str(evidence_source, 160),
        "blocked_reasons": sorted({_safe_str(reason, 160) for reason in (blocked_reasons or []) if reason}),
        "evidence_identity": _stable_signature_value(evidence_payload),
        "contract_version": CONTRACT_VERSION,
        "boundary_version": BOUNDARY_VERSION,
        "version_scope": "gap_policy_mini_write:v0.3.2-stable-ledger-signature",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _estimate_q(before: Mapping[str, Any], pos_inc: int, neg_inc: int, draw_inc: int) -> float:
    n_before = _as_int(before.get("n"), 0)
    pos = _as_int(before.get("pos"), 0) + int(pos_inc)
    neg = _as_int(before.get("neg"), 0) + int(neg_inc)
    n = n_before + int(pos_inc) + int(neg_inc) + int(draw_inc)
    if n <= 0:
        return 0.0
    return float(pos - neg) / float(n)


def _candidate_decision(
    row: sqlite3.Row,
    before: Mapping[str, Any],
    namespace_allowlist: Sequence[str],
    min_score: float,
    require_evidence: bool,
    allow_draw: bool,
) -> Dict[str, Any]:
    r = dict(row)
    meta = _json_loads(r.get("meta_json"))
    namespace = _safe_str(r.get("namespace"), 160)
    state_hash = _safe_str(r.get("state_hash"), 4000)
    action = _safe_str(r.get("primary_action"), 160)
    score = _as_float(r.get("score"), 0.0)
    blocked: List[str] = []

    if not namespace or not state_hash or not action:
        blocked.append("identity_incomplete")
    if not _matches_any(namespace, namespace_allowlist):
        blocked.append("namespace_not_allowed")
    if score < float(min_score):
        blocked.append("score_below_min")
    if _safe_str(r.get("status"), 80) not in ("promotion_review", "approved_for_policy_gate"):
        blocked.append("promotion_status_not_eligible")
    if _safe_str(r.get("promotion_bucket"), 120) not in DEFAULT_BUCKETS:
        blocked.append("promotion_bucket_not_policy_candidate")
    if action in ("", "step", "none", "null"):
        blocked.append("action_missing_or_generic")

    outcome, evidence_source, evidence_payload = _extract_evidence_outcome(meta)
    if require_evidence and not outcome:
        blocked.append("missing_evidence_outcome")
    if outcome == "draw" and not allow_draw:
        blocked.append("draw_outcome_not_allowed")
    if not outcome:
        outcome = "blocked"

    n_inc, pos_inc, neg_inc, draw_inc, outcome_q = (0, 0, 0, 0, 0.0)
    if outcome in ("pos", "neg", "draw"):
        n_inc, pos_inc, neg_inc, draw_inc, outcome_q = _increments(outcome)

    write_sig = _write_signature(r, outcome, evidence_source, evidence_payload, blocked)
    q_after = _estimate_q(before, pos_inc, neg_inc, draw_inc) if n_inc > 0 else _as_float(before.get("q"), 0.0)
    status = "write_candidate" if not blocked and n_inc > 0 else "blocked"
    return {
        "promotion_id": _as_int(r.get("id"), 0),
        "promotion_signature": _safe_str(r.get("promotion_signature"), 160),
        "write_signature": write_sig,
        "request_signature": _safe_str(r.get("request_signature"), 160),
        "target": _safe_str(r.get("target"), 80),
        "promotion_bucket": _safe_str(r.get("promotion_bucket"), 120),
        "namespace": namespace,
        "state_hash": state_hash,
        "action": action,
        "outcome": outcome if outcome in ("pos", "neg", "draw") else None,
        "evidence_source": evidence_source,
        "evidence_payload": evidence_payload,
        "status": status,
        "blocked_reasons": blocked,
        "blocked_reason": ",".join(blocked) if blocked else None,
        "policy_before": dict(before),
        "n_inc": n_inc,
        "pos_inc": pos_inc,
        "neg_inc": neg_inc,
        "draw_inc": draw_inc,
        "outcome_q": outcome_q,
        "q_after_est": q_after,
        "score": score,
        "execution": {
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_policy": status == "write_candidate",
            "dbwriter_only": True,
        },
    }


def _blocked_ledger_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    meta = {
        "version": VERSION,
        "blocked_reasons": item.get("blocked_reasons") if isinstance(item.get("blocked_reasons"), list) else [],
        "evidence_source": item.get("evidence_source"),
        "evidence_payload": item.get("evidence_payload") if isinstance(item.get("evidence_payload"), dict) else {},
        "policy_before": item.get("policy_before") if isinstance(item.get("policy_before"), dict) else {},
        "execution": item.get("execution") if isinstance(item.get("execution"), dict) else {},
        "source_kind": item.get("source_kind"),
        "evidence_id": item.get("evidence_id"),
        "outcome_queue_id": item.get("outcome_queue_id"),
        "outcome_signature": item.get("outcome_signature"),
        "evidence_class": item.get("evidence_class"),
        "mutation_type": item.get("mutation_type"),
        "writer_id": item.get("writer_id"),
        "contract_version": item.get("contract_version"),
        "boundary_version": item.get("boundary_version"),
        "promotion_updated_ts": item.get("promotion_updated_ts"),
        "promotion_source_validation_ts": item.get("promotion_source_validation_ts"),
        "promotion_age_sec": item.get("promotion_age_sec"),
        "promotion_focus_id": item.get("promotion_focus_id"),
        "promotion_plan_id": item.get("promotion_plan_id"),
        "promotion_previous_status": item.get("promotion_previous_status"),
        "promotion_new_status": item.get("promotion_new_status"),
    }
    return (
        f"""
        INSERT OR IGNORE INTO {LEDGER_TABLE} (
            write_signature, promotion_signature, promotion_id, request_signature,
            target, promotion_bucket, namespace, state_hash, action, outcome,
            status, policy_written, blocked_reason, n_inc, pos_inc, neg_inc,
            draw_inc, q_before, n_before, q_after_est, created_ts, updated_ts, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'blocked', 0, ?, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            _safe_str(item.get("write_signature"), 128),
            _safe_str(item.get("promotion_signature"), 160),
            _as_int(item.get("promotion_id"), 0),
            _safe_str(item.get("request_signature"), 160),
            _safe_str(item.get("target"), 80),
            _safe_str(item.get("promotion_bucket"), 120),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
            _safe_str(item.get("outcome"), 40),
            _safe_str(item.get("blocked_reason"), 500),
            _as_float((item.get("policy_before") or {}).get("q"), 0.0) if isinstance(item.get("policy_before"), dict) else 0.0,
            _as_int((item.get("policy_before") or {}).get("n"), 0) if isinstance(item.get("policy_before"), dict) else 0,
            _as_float(item.get("q_after_est"), 0.0),
            int(now), int(now),
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
        ),
    )


def _pending_ledger_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    meta = {
        "version": VERSION,
        "evidence_source": item.get("evidence_source"),
        "evidence_payload": item.get("evidence_payload") if isinstance(item.get("evidence_payload"), dict) else {},
        "policy_before": item.get("policy_before") if isinstance(item.get("policy_before"), dict) else {},
        "execution": item.get("execution") if isinstance(item.get("execution"), dict) else {},
        "source_kind": item.get("source_kind"),
        "evidence_id": item.get("evidence_id"),
        "outcome_queue_id": item.get("outcome_queue_id"),
        "outcome_signature": item.get("outcome_signature"),
        "evidence_class": item.get("evidence_class"),
        "mutation_type": item.get("mutation_type"),
        "writer_id": item.get("writer_id"),
        "contract_version": item.get("contract_version"),
        "boundary_version": item.get("boundary_version"),
        "promotion_updated_ts": item.get("promotion_updated_ts"),
        "promotion_source_validation_ts": item.get("promotion_source_validation_ts"),
        "promotion_age_sec": item.get("promotion_age_sec"),
        "promotion_focus_id": item.get("promotion_focus_id"),
        "promotion_plan_id": item.get("promotion_plan_id"),
        "promotion_previous_status": item.get("promotion_previous_status"),
        "promotion_new_status": item.get("promotion_new_status"),
    }
    before = item.get("policy_before") if isinstance(item.get("policy_before"), dict) else {}
    return (
        f"""
        INSERT OR IGNORE INTO {LEDGER_TABLE} (
            write_signature, promotion_signature, promotion_id, request_signature,
            target, promotion_bucket, namespace, state_hash, action, outcome,
            status, policy_written, blocked_reason, n_inc, pos_inc, neg_inc,
            draw_inc, q_before, n_before, q_after_est, created_ts, updated_ts, meta_json
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE EXISTS (
            SELECT 1 FROM {PROMOTION_TABLE}
            WHERE id=? AND promotion_signature=? AND request_signature=?
              AND namespace=? AND state_hash=? AND primary_action=?
              AND status IN ('promotion_review', 'approved_for_policy_gate')
        )
        """,
        (
            _safe_str(item.get("write_signature"), 128),
            _safe_str(item.get("promotion_signature"), 160),
            _as_int(item.get("promotion_id"), 0),
            _safe_str(item.get("request_signature"), 160),
            _safe_str(item.get("target"), 80),
            _safe_str(item.get("promotion_bucket"), 120),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
            _safe_str(item.get("outcome"), 40),
            _as_int(item.get("n_inc"), 0),
            _as_int(item.get("pos_inc"), 0),
            _as_int(item.get("neg_inc"), 0),
            _as_int(item.get("draw_inc"), 0),
            _as_float(before.get("q"), 0.0),
            _as_int(before.get("n"), 0),
            _as_float(item.get("q_after_est"), 0.0),
            int(now), int(now),
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
            _as_int(item.get("promotion_id"), 0),
            _safe_str(item.get("promotion_signature"), 160),
            _safe_str(item.get("request_signature"), 160),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
        ),
    )


def _policy_upsert_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    n_inc = _as_int(item.get("n_inc"), 0)
    pos_inc = _as_int(item.get("pos_inc"), 0)
    neg_inc = _as_int(item.get("neg_inc"), 0)
    draw_inc = _as_int(item.get("draw_inc"), 0)
    return (
        f"""
        INSERT INTO {POLICY_TABLE} (namespace,state_hash,action,n,pos,neg,draw,q,last_ts,centroid)
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL
        WHERE EXISTS (SELECT 1 FROM {LEDGER_TABLE} WHERE write_signature=? AND status='pending' AND policy_written=0)
          AND EXISTS (
              SELECT 1 FROM {PROMOTION_TABLE}
              WHERE id=? AND promotion_signature=? AND request_signature=?
                AND namespace=? AND state_hash=? AND primary_action=?
                AND status IN ('promotion_review', 'approved_for_policy_gate')
          )
        ON CONFLICT(namespace,state_hash,action) DO UPDATE SET
            n       = {POLICY_TABLE}.n + excluded.n,
            pos     = {POLICY_TABLE}.pos + excluded.pos,
            neg     = {POLICY_TABLE}.neg + excluded.neg,
            draw    = {POLICY_TABLE}.draw + excluded.draw,
            q       = CASE
                        WHEN ({POLICY_TABLE}.n + excluded.n) > 0
                        THEN CAST(({POLICY_TABLE}.pos + excluded.pos) - ({POLICY_TABLE}.neg + excluded.neg) AS REAL)
                             / CAST(({POLICY_TABLE}.n + excluded.n) AS REAL)
                        ELSE 0.0
                      END,
            last_ts = excluded.last_ts,
            centroid = COALESCE(excluded.centroid, {POLICY_TABLE}.centroid)
        """,
        (
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
            n_inc, pos_inc, neg_inc, draw_inc, _as_float(item.get("outcome_q"), 0.0), int(now),
            _safe_str(item.get("write_signature"), 128),
            _as_int(item.get("promotion_id"), 0),
            _safe_str(item.get("promotion_signature"), 160),
            _safe_str(item.get("request_signature"), 160),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
        ),
    )


def _link_signature(item: Mapping[str, Any]) -> str:
    payload = {
        "write_signature": _safe_str(item.get("write_signature"), 128),
        "evidence_id": _safe_str(item.get("evidence_id"), 200),
        "outcome_queue_id": _as_int(item.get("outcome_queue_id"), 0),
        "namespace": _safe_str(item.get("namespace"), 160),
        "state_hash": _safe_str(item.get("state_hash"), 4000),
        "action": _safe_str(item.get("action"), 160),
        "mutation_type": _safe_str(item.get("mutation_type"), 80),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _policy_evidence_link_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    link_meta = {
        "version": VERSION,
        "promotion_signature": item.get("promotion_signature"),
        "request_signature": item.get("request_signature"),
        "outcome": item.get("outcome"),
        "confidence": item.get("confidence"),
        "evidence_source": item.get("evidence_source"),
    }
    return (
        f"""
        INSERT OR IGNORE INTO {LINK_TABLE} (
            link_signature, policy_rule_id, namespace, state_hash, action,
            evidence_id, outcome_queue_id, outcome_signature, evidence_class,
            mutation_type, writer_id, contract_version, boundary_version,
            write_signature, created_ts, meta_json
        )
        SELECT ?, p.id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        FROM {POLICY_TABLE} AS p
        WHERE p.namespace=? AND p.state_hash=? AND p.action=?
          AND EXISTS (
              SELECT 1 FROM {LEDGER_TABLE}
              WHERE write_signature=? AND status='pending' AND policy_written=0
          )
        LIMIT 1
        """,
        (
            _link_signature(item),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
            _safe_str(item.get("evidence_id"), 200),
            _as_int(item.get("outcome_queue_id"), 0),
            _safe_str(item.get("outcome_signature"), 128),
            _safe_str(item.get("evidence_class"), 80),
            _safe_str(item.get("mutation_type"), 80),
            _safe_str(item.get("writer_id"), 160),
            _safe_str(item.get("contract_version"), 160),
            _safe_str(item.get("boundary_version"), 160),
            _safe_str(item.get("write_signature"), 128),
            int(now),
            json.dumps(link_meta, ensure_ascii=False, sort_keys=True),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
            _safe_str(item.get("write_signature"), 128),
        ),
    )


def _promotion_consumed_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    """Schließt exakt die geprüfte Promotion atomar und race-safe ab.

    Die vollständige Identität wird erneut in der DBWriter-Transaktion geprüft.
    Dadurch kann eine zwischen Read-only-Analyse und Transaktionsausführung
    geänderte oder fachlich ersetzte Promotion keinen Policy-Write legitimieren.
    """
    return (
        f"""
        UPDATE {PROMOTION_TABLE}
        SET status='policy_written', policy_write_allowed=1, updated_ts=?
        WHERE id=? AND promotion_signature=? AND request_signature=?
          AND namespace=? AND state_hash=? AND primary_action=?
          AND status IN ('promotion_review', 'approved_for_policy_gate')
          AND EXISTS (
              SELECT 1 FROM {LEDGER_TABLE}
              WHERE write_signature=? AND status='pending' AND policy_written=0
          )
        """,
        (
            int(now),
            _as_int(item.get("promotion_id"), 0),
            _safe_str(item.get("promotion_signature"), 160),
            _safe_str(item.get("request_signature"), 160),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
            _safe_str(item.get("write_signature"), 128),
        ),
    )


def _outcome_queue_consumed_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    return (
        f"""
        UPDATE {OUTCOME_TABLE}
        SET status='policy_written', policy_write_allowed=1, updated_ts=?
        WHERE id=? AND outcome_signature=?
          AND status IN ('outcome_ready', 'approved_for_policy_gate')
          AND EXISTS (
              SELECT 1 FROM {LEDGER_TABLE}
              WHERE write_signature=? AND status='pending' AND policy_written=0
          )
          AND EXISTS (
              SELECT 1 FROM {PROMOTION_TABLE}
              WHERE id=? AND promotion_signature=? AND request_signature=?
                AND namespace=? AND state_hash=? AND primary_action=?
                AND status='policy_written'
          )
        """,
        (
            int(now),
            _as_int(item.get("outcome_queue_id"), 0),
            _safe_str(item.get("outcome_signature"), 128),
            _safe_str(item.get("write_signature"), 128),
            _as_int(item.get("promotion_id"), 0),
            _safe_str(item.get("promotion_signature"), 160),
            _safe_str(item.get("request_signature"), 160),
            _safe_str(item.get("namespace"), 160),
            _safe_str(item.get("state_hash"), 4000),
            _safe_str(item.get("action"), 160),
        ),
    )


def _ledger_written_statement(item: Mapping[str, Any], now: int) -> Tuple[str, Sequence[Any]]:
    return (
        f"""
        UPDATE {LEDGER_TABLE}
        SET status='written', policy_written=1, updated_ts=?
        WHERE write_signature=? AND status='pending' AND policy_written=0
        """,
        (int(now), _safe_str(item.get("write_signature"), 128)),
    )


def _run_transaction(statements: Sequence[Tuple[str, Sequence[Any]]], timeout_ms: int) -> Dict[str, Any]:
    if db_writer_client is None:
        raise RuntimeError("db_writer_client unavailable")
    return db_writer_client.transaction(
        list(statements),
        tag="gap_policy_mini_write.gate",
        priority="low",
        timeout_ms=int(timeout_ms),
        db="oroma",
    )


def run_once(
    *,
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    max_writes: Optional[int] = None,
) -> Dict[str, Any]:
    base = _base_dir()
    dbp = Path(db_path or default_db_path(base)).resolve()
    statep = Path(state_path or default_state_path(base)).resolve()
    now = _now_ts()

    write_enable = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_ENABLE", False)
    confirm_required = _env_str("OROMA_GAP_POLICY_MINI_WRITE_CONFIRM_REQUIRED", "GAP_POLICY_MINI_WRITE_REVIEWED")
    confirm_value = _env_str("OROMA_GAP_POLICY_MINI_WRITE_CONFIRM", "")
    confirm_ok = bool(confirm_required and confirm_value == confirm_required)
    ledger_enable = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_LEDGER_ENABLE", True)
    record_blocked = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_RECORD_BLOCKED", True)
    require_evidence = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_REQUIRE_EVIDENCE_OUTCOME", True)
    source_mode = _env_str("OROMA_GAP_POLICY_MINI_WRITE_SOURCE", "outcome_queue").lower()
    allow_promotion_meta_fallback = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_ALLOW_PROMOTION_META_FALLBACK", False)
    min_confidence = _env_float("OROMA_GAP_POLICY_MINI_WRITE_MIN_CONFIDENCE", 0.50)
    outcome_max_age_sec = _env_int("OROMA_GAP_POLICY_MINI_WRITE_OUTCOME_MAX_AGE_SEC", 7200)
    allow_stale_outcome = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_ALLOW_STALE_OUTCOME", False)
    promotion_max_age_sec = _env_int("OROMA_GAP_POLICY_MINI_WRITE_PROMOTION_MAX_AGE_SEC", 7200)
    allow_stale_promotion = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_ALLOW_STALE_PROMOTION", False)
    allow_draw = _env_bool("OROMA_GAP_POLICY_MINI_WRITE_ALLOW_DRAW_OUTCOME", False)
    namespace_allowlist = parse_csv(_env_str("OROMA_GAP_POLICY_MINI_WRITE_NAMESPACE_ALLOWLIST", "game:*"), ["game:*"])
    state_schema_allowlist = parse_csv(_env_str("OROMA_GAP_POLICY_MINI_WRITE_STATE_SCHEMA_ALLOWLIST", ""), [])
    bucket_allowlist = parse_csv(_env_str("OROMA_GAP_POLICY_MINI_WRITE_BUCKETS", ",".join(DEFAULT_BUCKETS)), DEFAULT_BUCKETS)
    min_score = _env_float("OROMA_GAP_POLICY_MINI_WRITE_MIN_SCORE", 0.0)
    lim = int(limit if limit is not None else _env_int("OROMA_GAP_POLICY_MINI_WRITE_LIMIT", 50))
    returned = int(topk if topk is not None else _env_int("OROMA_GAP_POLICY_MINI_WRITE_TOPK", 10))
    write_budget = int(max_writes if max_writes is not None else _env_int("OROMA_GAP_POLICY_MINI_WRITE_MAX_WRITES_PER_RUN", 1))
    dbw_timeout = _env_int("OROMA_GAP_POLICY_MINI_WRITE_DBW_TIMEOUT_MS", 15000)
    dbw_ping_timeout = _env_int("OROMA_GAP_POLICY_MINI_WRITE_DBW_PING_TIMEOUT_MS", 500)

    dbw_ok, dbw_reason = _dbwriter_ready(dbw_ping_timeout)
    write_ready = bool(write_enable and confirm_ok and dbw_ok and ledger_enable)
    if not write_enable:
        write_block_reason = "write_enable_disabled"
    elif not confirm_ok:
        write_block_reason = "confirm_token_missing_or_wrong"
    elif not dbw_ok:
        write_block_reason = dbw_reason
    elif not ledger_enable:
        write_block_reason = "ledger_disabled"
    else:
        write_block_reason = "write_ready"

    errors: List[str] = []
    candidates: List[Dict[str, Any]] = []
    statements: List[Tuple[str, Sequence[Any]]] = []
    policy_write_candidates = 0
    policy_writes_planned = 0
    blocked_total = 0

    selected_source = "outcome_queue"
    try:
        con = _connect_ro(dbp)
        try:
            rows: List[sqlite3.Row] = []
            load_errors: List[str] = []
            if source_mode in ("outcome_queue", "auto"):
                rows, load_errors = _load_outcome_candidates(con, bucket_allowlist, lim)
                errors.extend(load_errors)
                selected_source = "outcome_queue"
                for row in rows:
                    before = _policy_before(
                        con,
                        _safe_str(row["namespace"], 160),
                        _safe_str(row["state_hash"], 4000),
                        _safe_str(row["action"], 160),
                    )
                    item = _queue_candidate_decision(
                        row, before, namespace_allowlist, state_schema_allowlist, min_score, min_confidence,
                        allow_draw, outcome_max_age_sec, allow_stale_outcome,
                        promotion_max_age_sec, allow_stale_promotion, now,
                    )
                    if item["status"] == "write_candidate":
                        policy_write_candidates += 1
                    else:
                        blocked_total += 1
                    candidates.append(item)
            if not candidates and allow_promotion_meta_fallback and source_mode in ("auto", "promotion_meta"):
                selected_source = "promotion_meta_legacy_fallback"
                rows, fallback_errors = _load_candidates(con, bucket_allowlist, lim)
                errors.extend(fallback_errors)
                for row in rows:
                    before = _policy_before(
                        con,
                        _safe_str(row["namespace"], 160),
                        _safe_str(row["state_hash"], 4000),
                        _safe_str(row["primary_action"], 160),
                    )
                    item = _candidate_decision(
                        row, before, namespace_allowlist, min_score, require_evidence, allow_draw,
                    )
                    item["source_kind"] = "promotion_meta_legacy_fallback"
                    if item["status"] == "write_candidate":
                        policy_write_candidates += 1
                    else:
                        blocked_total += 1
                    candidates.append(item)
        finally:
            con.close()
    except Exception as exc:
        errors.append("load_failed:%s" % exc)

    write_items: List[Dict[str, Any]] = []
    blocked_items: List[Dict[str, Any]] = []
    if write_ready:
        for item in candidates:
            if item.get("status") == "write_candidate" and len(write_items) < max(0, write_budget):
                write_items.append(item)
            elif item.get("status") == "blocked" and record_blocked:
                blocked_items.append(item)
    # Schema is created only when DBWriter write path is explicitly ready.
    if write_ready:
        statements.extend(_schema_statements())
        for item in blocked_items:
            statements.append(_blocked_ledger_statement(item, now))
        for item in write_items:
            statements.append(_pending_ledger_statement(item, now))
            statements.append(_policy_upsert_statement(item, now))
            if item.get("source_kind") == "outcome_queue":
                statements.append(_policy_evidence_link_statement(item, now))
                statements.append(_promotion_consumed_statement(item, now))
                statements.append(_outcome_queue_consumed_statement(item, now))
            statements.append(_ledger_written_statement(item, now))
        policy_writes_planned = len(write_items)

    tx_ok = False
    tx_error: Optional[str] = None
    if statements:
        try:
            _run_transaction(statements, dbw_timeout)
            tx_ok = True
        except Exception as exc:
            tx_error = str(exc)
            errors.append("dbwriter_transaction_failed:%s" % exc)

    # Public output: keep item list bounded.
    shown_candidates = candidates[: max(0, returned)]
    per_status: Dict[str, int] = {}
    per_block: Dict[str, int] = {}
    for item in candidates:
        st = str(item.get("status") or "unknown")
        per_status[st] = per_status.get(st, 0) + 1
        for reason in item.get("blocked_reasons") or []:
            r = str(reason)
            per_block[r] = per_block.get(r, 0) + 1

    out: Dict[str, Any] = {
        "ok": len(errors) == 0,
        "version": VERSION,
        "mode": "policy_mini_write_gate" if write_ready else "policy_mini_write_gate_blocked_or_dry_run",
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "db_path": str(dbp),
        "state_path": str(statep),
        "safety": {
            "db_access": "read_only_plus_dbwriter_gate" if write_ready else "read_only_or_blocked",
            "db_writes": bool(statements),
            "policy_writes_possible": bool(write_ready),
            "rules_writes": False,
            "runner_starts": False,
            "replay_starts": False,
            "dream_starts": False,
            "local_sqlite_write_fallback": False,
            "requires_evidence_outcome": bool(require_evidence),
            "state_json_write": True,
        },
        "gate": {
            "write_enable": bool(write_enable),
            "confirm_required": confirm_required,
            "confirm_ok": bool(confirm_ok),
            "dbwriter_ready": bool(dbw_ok),
            "dbwriter_reason": dbw_reason,
            "ledger_enable": bool(ledger_enable),
            "record_blocked": bool(record_blocked),
            "source_mode": source_mode,
            "selected_source": selected_source,
            "allow_promotion_meta_fallback": bool(allow_promotion_meta_fallback),
            "min_confidence": float(min_confidence),
            "outcome_max_age_sec": int(outcome_max_age_sec),
            "allow_stale_outcome": bool(allow_stale_outcome),
            "promotion_max_age_sec": int(promotion_max_age_sec),
            "allow_stale_promotion": bool(allow_stale_promotion),
            "writer_id": WRITER_ID,
            "contract_version": CONTRACT_VERSION,
            "boundary_version": BOUNDARY_VERSION,
            "namespace_allowlist": list(namespace_allowlist),
            "state_schema_allowlist": list(state_schema_allowlist),
            "bucket_allowlist": list(bucket_allowlist),
            "min_score": float(min_score),
            "max_writes_per_run": int(write_budget),
            "write_ready": bool(write_ready),
            "write_block_reason": write_block_reason,
        },
        "summary": {
            "ok": len(errors) == 0,
            "source_rows_loaded": len(candidates),
            "selected_source": selected_source,
            "outcome_queue_candidates": sum(1 for x in candidates if x.get("source_kind") == "outcome_queue"),
            "legacy_fallback_candidates": sum(1 for x in candidates if x.get("source_kind") == "promotion_meta_legacy_fallback"),
            "policy_write_candidates": int(policy_write_candidates),
            "policy_writes_planned": int(policy_writes_planned),
            "policy_writes_attempted": int(policy_writes_planned if statements else 0),
            "policy_writes_allowed_by_gate": bool(write_ready),
            "blocked_total": int(blocked_total),
            "per_status_counts": per_status,
            "per_blocked_reason_counts": per_block,
            "ledger_statements": len(statements),
            "transaction_ok": bool(tx_ok),
            "transaction_error": tx_error,
            "state_written": True,
        },
        "candidates": shown_candidates,
        "errors": errors,
    }
    atomic_write_json(statep, out)
    return out


__all__ = ["run_once", "VERSION"]

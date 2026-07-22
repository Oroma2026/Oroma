#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_capability_diagnose.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Replay-Evidence-Capability · Read-only Deep-Diagnose
# Version:   v0.1.0-read-only-audit
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Werkzeug erklärt reproduzierbar, warum reale Referenz-Gap-Gruppen von
# core/replay_evidence_capability.py akzeptiert oder fail-closed abgelehnt
# werden. Es verwendet dieselben Gap-Lade-, Gruppierungs-, Policy-Evidence- und
# Klassifikationsfunktionen wie tools/gap_learning_bridge.py und prüft danach
# jede reale Fokusgruppe gegen den unveränderten produktiven Capability-Vertrag.
#
# SICHERHEITS- UND PRODUKTIONSINVARIANTEN
# --------------------------------------
# - Ausschließlich read-only: SQLite wird zwingend mit URI mode=ro geöffnet.
# - Keine DBWriter-Aufrufe, keine INSERT/UPDATE/DELETE/DDL und keine Migration.
# - Keine State-Dateien, Logs, Checkpoints, Queues oder Policy-Regeln werden
#   geschrieben oder verändert.
# - Keine künstlichen Test-Gaps, keine Platzhalter und keine simulierten Rows.
# - Der Scan ist über Gap-, SnapChain-, Stichproben- und Laufzeitlimits bounded.
# - Die produktiven Kernmodule werden nicht gepatcht und emittieren keine
#   zusätzlichen Debug-Ausgaben; die Diagnose bleibt vollständig isoliert.
# - Headless: keine Qt-, Wayland-, X11-, Browser- oder GUI-Abhängigkeiten.
#
# AUDIT-MODELL
# ------------
#   knowledge_gaps (reale Live-DB-Zeilen)
#       -> identische Referenz-Gruppierung/Klassifikation wie Learning Bridge
#       -> gemeinsamer unveränderlicher SnapChain-Capability-Context
#       -> check_replay_evidence_capability(...)
#       -> je Blockklasse bounded Stichproben mit Match-/Outcome-Feldern
#
# Die rohen Step-Keys werden nur für bereits exakt passende historische
# (state_hash, action)-Steps erneut aus den betreffenden SnapChain-Blobs gelesen.
# Dadurch kann zwischen fehlender Quelle, fehlendem Direct-Outcome, nicht
# unterstütztem Outcome-Wert und gültiger Targeted Evidence unterschieden
# werden, ohne historische Episode-Outcomes nachträglich als Step-Credit zu
# interpretieren.
#
# CLI-BEISPIELE
# -------------
#   cd /opt/ai/oroma; set -a; . ./.env.systemd; set +a; \
#     python3 tools/gap_capability_diagnose.py --once --pretty
#
#   cd /opt/ai/oroma; set -a; . ./.env.systemd; set +a; \
#     python3 tools/gap_capability_diagnose.py --once \
#       --samples-per-reason 3 --limit-gaps 500 --scan-limit 500
#
# OUTPUT
# ------
# Ein JSON-Dokument auf stdout mit summary, reason_counts und samples. Bei
# Fehlern bleibt stdout maschinenlesbar; Details stehen zusätzlich in errors.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

VERSION = "v0.1.0-read-only-audit"
DEFAULT_REASONS = (
    "replay_state_action_source_missing",
    "replay_direct_evidence_unavailable",
)


def _bootstrap_import_path() -> Path:
    base = Path(__file__).resolve().parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    return base


BASE = _bootstrap_import_path()

from core.replay_evidence_capability import (  # noqa: E402
    _decode_blob,
    _normalize_direct_outcome,
    _step_action,
    _step_state_hash,
    build_replay_evidence_capability_context,
    check_replay_evidence_capability,
)
from tools.gap_learning_bridge import (  # noqa: E402
    _classify_group,
    _group_gaps,
    _load_policy_evidence,
    _load_reference_gaps,
    _summarize_policy,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip())
    except Exception:
        return float(default)


def _default_db_path(base: Path) -> Path:
    explicit = str(os.environ.get("OROMA_DB_PATH") or "").strip()
    return Path(explicit).expanduser() if explicit else base / "data" / "oroma.db"


def _open_ro(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path.resolve()) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (str(name),),
    ).fetchone()
    return bool(row)


def _state_schema(state_hash: str) -> str:
    parts = str(state_hash or "").split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else ""


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)


def _raw_historical_step_audit(
    conn: sqlite3.Connection,
    *,
    snapchain_ids: Sequence[int],
    state_hash: str,
    action: str,
    max_steps: int,
) -> Dict[str, Any]:
    ids = sorted({int(x) for x in snapchain_ids if int(x) > 0})
    out: Dict[str, Any] = {
        "snapchains_requested": len(ids),
        "snapchains_loaded": 0,
        "decode_errors": 0,
        "matching_steps_inspected": 0,
        "detected_step_keys": [],
        "outcome_field_counts": {},
        "matching_step_examples": [],
    }
    if not ids:
        return out
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT id, blob FROM snapchains WHERE id IN (" + placeholders + ") ORDER BY id DESC",
        ids,
    ).fetchall()
    key_union = set()
    field_counts: Dict[str, int] = {}
    examples: List[Dict[str, Any]] = []
    for row in rows:
        out["snapchains_loaded"] += 1
        root, error = _decode_blob(row["blob"])
        if root is None:
            out["decode_errors"] += 1
            continue
        steps = root.get("steps") if isinstance(root.get("steps"), list) else []
        for step_index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                continue
            if _step_state_hash(step) != state_hash or _step_action(step) != action:
                continue
            out["matching_steps_inspected"] += 1
            key_union.update(str(k) for k in step.keys())
            normalized, field, raw = _normalize_direct_outcome(step)
            label = str(field or "missing_direct_outcome")
            field_counts[label] = field_counts.get(label, 0) + 1
            if len(examples) < max(0, int(max_steps)):
                examples.append({
                    "snapchain_id": int(row["id"]),
                    "step_index": int(step_index),
                    "step_keys": sorted(str(k) for k in step.keys()),
                    "outcome_present": "outcome" in step,
                    "result_present": "result" in step,
                    "reward_present": "reward" in step,
                    "outcome_raw": _json_safe(step.get("outcome")),
                    "result_raw": _json_safe(step.get("result")),
                    "reward_raw": _json_safe(step.get("reward")),
                    "normalized_outcome": normalized,
                    "normalizer_field": field,
                    "normalizer_raw": _json_safe(raw),
                })
    out["detected_step_keys"] = sorted(key_union)
    out["outcome_field_counts"] = dict(sorted(field_counts.items()))
    out["matching_step_examples"] = examples
    return out


def _sample_record(
    conn: sqlite3.Connection,
    *,
    group: Mapping[str, Any],
    capability: Mapping[str, Any],
    context: Mapping[str, Any],
    raw_steps_per_sample: int,
) -> Dict[str, Any]:
    state_hash = str(group.get("state_hash") or "")
    action = str(group.get("primary_action") or "")
    historical_matches = list((context.get("snake_index") or {}).get((state_hash, action), []))
    targeted_matches = list((context.get("snake_targeted_index") or {}).get((state_hash, action), []))
    historical_ids = [int(x.get("snapchain_id") or 0) for x in historical_matches]
    historical_block_counts: Dict[str, int] = {}
    for match in historical_matches:
        reason = str(match.get("blocked_reason") or "usable_direct_outcome")
        historical_block_counts[reason] = historical_block_counts.get(reason, 0) + 1
    return {
        "gap": {
            "focus_id": group.get("focus_id"),
            "namespace": group.get("namespace"),
            "state_schema": _state_schema(state_hash),
            "state_hash": state_hash,
            "primary_action": action,
            "kind": group.get("kind"),
            "gap_count": group.get("gap_count"),
            "latest_ts": group.get("latest_ts"),
            "reason": group.get("reason"),
            "recommended_next": group.get("recommended_next"),
            "policy_evidence": group.get("policy_evidence"),
        },
        "capability": dict(capability),
        "context_match_audit": {
            "historical_match_records": len(historical_matches),
            "historical_match_block_reason_counts": dict(sorted(historical_block_counts.items())),
            "targeted_match_records": len(targeted_matches),
            "historical_snapchain_ids": sorted(set(historical_ids))[:20],
            "targeted_snapchain_ids": sorted({int(x.get("snapchain_id") or 0) for x in targeted_matches if int(x.get("snapchain_id") or 0) > 0})[:20],
        },
        "raw_historical_step_audit": _raw_historical_step_audit(
            conn,
            snapchain_ids=historical_ids,
            state_hash=state_hash,
            action=action,
            max_steps=raw_steps_per_sample,
        ),
    }


def diagnose(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    started = time.time()
    db_path = Path(args.db_path).expanduser()
    reasons = [x.strip() for x in str(args.reasons).split(",") if x.strip()]
    doc: Dict[str, Any] = {
        "version": VERSION,
        "ok": False,
        "generated_at_ts": int(time.time()),
        "db_path": str(db_path),
        "safety": {
            "sqlite_mode": "ro",
            "query_only": True,
            "db_writes": False,
            "state_writes": False,
            "policy_writes": False,
        },
        "summary": {},
        "reason_counts": {},
        "samples": {reason: [] for reason in reasons},
        "errors": [],
    }
    if not db_path.exists():
        doc["errors"].append(f"oroma.db not found: {db_path}")
        doc["summary"] = {"blocked_reason": "db_path_missing", "dt_sec": round(time.time() - started, 3)}
        return doc, 2
    try:
        with _open_ro(db_path) as conn:
            for table in ("knowledge_gaps", "policy_rules", "snapchains"):
                if not _table_exists(conn, table):
                    doc["errors"].append(f"required table missing: {table}")
            if doc["errors"]:
                doc["summary"] = {"blocked_reason": "required_table_missing", "dt_sec": round(time.time() - started, 3)}
                return doc, 2

            namespace_patterns = [x.strip() for x in str(args.namespace_allowlist).split(",") if x.strip()]
            schemas = [x.strip() for x in str(args.reference_schemas).split(",") if x.strip()]
            since_ts = max(0, int(time.time()) - max(0, int(args.lookback_sec)))
            gaps, load_summary = _load_reference_gaps(
                conn,
                since_ts=since_ts,
                limit_gaps=max(1, int(args.limit_gaps)),
                min_confidence=float(args.min_confidence),
                namespace_patterns=namespace_patterns,
                reference_schemas=schemas,
            )
            groups = _group_gaps(gaps)
            evidence = _load_policy_evidence(
                conn,
                groups,
                max_runtime_s=max(1, int(args.max_runtime_s)),
                start_ts=started,
            )
            focus: List[Dict[str, Any]] = []
            for group in groups:
                rows = evidence.get((str(group.get("namespace") or ""), str(group.get("state_hash") or "")), [])
                policy = _summarize_policy(rows, group.get("actions") or [])
                status, reason, recommended_next = _classify_group(
                    group,
                    policy,
                    covered_min_n=max(1, int(args.covered_min_n)),
                    uncertainty_eps=float(args.uncertainty_eps),
                )
                if status != "focus":
                    continue
                item = dict(group)
                item.update({
                    "status": status,
                    "reason": reason,
                    "recommended_next": recommended_next,
                    "policy_evidence": policy,
                })
                focus.append(item)

            context = build_replay_evidence_capability_context(
                conn,
                schemas=schemas,
                scan_limit=max(1, int(args.scan_limit)),
            )
            reason_counts: Dict[str, int] = {}
            checked = 0
            available = 0
            for item in focus:
                capability = check_replay_evidence_capability(
                    conn,
                    namespace=str(item.get("namespace") or ""),
                    state_schema=_state_schema(str(item.get("state_hash") or "")),
                    state_hash=str(item.get("state_hash") or ""),
                    action=str(item.get("primary_action") or ""),
                    scan_limit=max(1, int(args.scan_limit)),
                    context=context,
                )
                checked += 1
                if bool(capability.get("available")):
                    available += 1
                reason = str(capability.get("blocked_reason") or "available")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                if reason in doc["samples"] and len(doc["samples"][reason]) < max(0, int(args.samples_per_reason)):
                    doc["samples"][reason].append(_sample_record(
                        conn,
                        group=item,
                        capability=capability,
                        context=context,
                        raw_steps_per_sample=max(0, int(args.raw_steps_per_sample)),
                    ))

            doc["ok"] = True
            doc["reason_counts"] = dict(sorted(reason_counts.items()))
            doc["summary"] = {
                "reference_gap_rows_loaded": len(gaps),
                "reference_gap_groups_total": len(groups),
                "reference_focus_groups_checked": checked,
                "capability_available": available,
                "capability_rejected": checked - available,
                "load_summary": load_summary,
                "snapchains_scanned": int(context.get("snapchains_scanned") or 0),
                "historical_snapchains_scanned": int(context.get("historical_snapchains_scanned") or 0),
                "targeted_snapchains_scanned": int(context.get("targeted_snapchains_scanned") or 0),
                "steps_scanned_total": int(context.get("steps_scanned_total") or 0),
                "decode_errors": int(context.get("decode_errors") or 0),
                "targeted_candidates_indexed": int(context.get("targeted_candidates_indexed") or 0),
                "samples_returned": {key: len(value) for key, value in doc["samples"].items()},
                "dt_sec": round(time.time() - started, 3),
            }
            return doc, 0
    except sqlite3.Error as exc:
        doc["errors"].append(f"sqlite_error: {exc}")
    except Exception as exc:
        doc["errors"].append(f"unexpected_error: {type(exc).__name__}: {exc}")
    doc["summary"] = {"blocked_reason": "diagnose_failed", "dt_sec": round(time.time() - started, 3)}
    return doc, 2


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORÓMA Replay-Evidence-Capability Read-only Deep-Diagnose")
    parser.add_argument("--once", action="store_true", help="Einmaliger Lauf; für Tool-Konvention vorhanden.")
    parser.add_argument("--db-path", default=str(_default_db_path(BASE)), help="Pfad zu oroma.db; zwingend read-only geöffnet")
    parser.add_argument("--lookback-sec", type=int, default=_env_int("OROMA_GAP_LEARNING_LOOKBACK_SEC", 604800))
    parser.add_argument("--limit-gaps", type=int, default=_env_int("OROMA_GAP_LEARNING_LIMIT_GAPS", 500))
    parser.add_argument("--reference-schemas", default=os.environ.get("OROMA_GAP_LEARNING_REFERENCE_SCHEMAS", "snake:pro_v2"))
    parser.add_argument("--namespace-allowlist", default=os.environ.get("OROMA_GAP_LEARNING_NAMESPACE_ALLOWLIST", "game:*"))
    parser.add_argument("--scan-limit", type=int, default=_env_int("OROMA_GAP_LEARNING_REFERENCE_CAPABILITY_SCAN_LIMIT", 500))
    parser.add_argument("--min-confidence", type=float, default=_env_float("OROMA_GAP_LEARNING_MIN_CONFIDENCE", 0.0))
    parser.add_argument("--covered-min-n", type=int, default=_env_int("OROMA_GAP_LEARNING_COVERED_MIN_N", 5))
    parser.add_argument("--uncertainty-eps", type=float, default=_env_float("OROMA_GAP_LEARNING_UNCERTAINTY_EPS", 0.05))
    parser.add_argument("--max-runtime-s", type=int, default=_env_int("OROMA_GAP_LEARNING_MAX_RUNTIME_S", 30))
    parser.add_argument("--reasons", default=",".join(DEFAULT_REASONS), help="Kommagetrennte Blockgründe für Stichproben")
    parser.add_argument("--samples-per-reason", type=int, default=3)
    parser.add_argument("--raw-steps-per-sample", type=int, default=3)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    doc, rc = diagnose(args)
    print(json.dumps(doc, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=bool(args.pretty)))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

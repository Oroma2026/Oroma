#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_shadow_plan.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap-Focus Shadow Plan · Read-Only Anschlussplan
# Version:   v0.1.0-read-only-shadow-plan
# Stand:     2026-07-09
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist der dritte konservative Schritt der Gap-Lernarchitektur:
#
#   1) tools/gap_learning_bridge.py
#      knowledge_gaps + policy_rules read-only -> gap_learning_focus.json
#
#   2) tools/gap_focus_consumer.py
#      gap_learning_focus.json -> gap_focus_consumer.json mit Buckets fuer
#      explore / replay / dream / runner_priority
#
#   3) dieses Modul
#      gap_focus_consumer.json -> gap_focus_shadow_plan.json
#
# Der Shadow-Plan formuliert maschinenlesbar, was ein spaeterer Verbraucher
# pruefen duerfte. Er startet nichts, schreibt keine DB und schreibt keine Policy.
# Dadurch bekommt ORÓMA eine explizite, auditierbare Planungsstufe zwischen
# "Gap erkannt" und "Evidenz erzeugen".
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhaengigkeiten.
# - Kein DB-Zugriff: Quelle ist ausschliesslich data/state/gap_focus_consumer.json.
# - Keine DB-Writes, kein DBWriter, keine Schemaaenderungen.
# - Keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - State-Write nur atomar nach data/state/gap_focus_shadow_plan.json.
# - Stale-Gate: Zu alte Consumer-Dateien werden im Default sichtbar blockiert.
# - Fail-soft: Fehler erscheinen im Ergebnisdokument; Orchestrator darf weiterlaufen.
#
# WARUM SHADOW-PLAN?
# ------------------
# Gaps sind Lernbedarf, aber kein Reward und keine Direct Evidence. Auch die
# Consumer-Buckets sind nur eine Sicht. Der Shadow-Plan bildet deshalb eine
# bewusste Review-Schicht:
#
#   replay           -> Kandidat fuer spaetere Replay-Pruefung, kein Replay-Start
#   dream            -> Kandidat fuer spaetere Dream-Konsolidierung, kein Dream-Start
#   explore          -> Kandidat fuer spaetere Episodenplanung, kein Runner-Start
#   runner_priority  -> Prioritaets-Hinweis, keine Scheduling-Aenderung
#
# Erst eine spaetere, separat gegatete Stufe darf daraus echte Jobs oder Writes
# machen. Dieses Modul liefert dafuer nur die nachvollziehbare, sichere Eingabe.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_GAP_FOCUS_CONSUMER_STATE_PATH=/opt/ai/oroma/data/state/gap_focus_consumer.json
#   OROMA_GAP_FOCUS_SHADOW_PLAN_SOURCE_PATH=/opt/ai/oroma/data/state/gap_focus_consumer.json
#   OROMA_GAP_FOCUS_SHADOW_PLAN_STATE_PATH=/opt/ai/oroma/data/state/gap_focus_shadow_plan.json
#   OROMA_GAP_FOCUS_SHADOW_PLAN_TARGETS=explore,replay,dream,runner_priority
#   OROMA_GAP_FOCUS_SHADOW_PLAN_TOPK=10
#   OROMA_GAP_FOCUS_SHADOW_PLAN_MAX_AGE_SEC=7200
#   OROMA_GAP_FOCUS_SHADOW_PLAN_ALLOW_STALE=0
# =============================================================================

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

VERSION = "v0.1.0-read-only-shadow-plan"
DEFAULT_SOURCE_NAME = "gap_focus_consumer.json"
DEFAULT_STATE_NAME = "gap_focus_shadow_plan.json"
DEFAULT_TARGETS = ("explore", "replay", "dream", "runner_priority")


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
    return raw in ("1", "true", "yes", "y", "on")


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
    explicit = os.environ.get("OROMA_GAP_FOCUS_SHADOW_PLAN_SOURCE_PATH") or os.environ.get("OROMA_GAP_FOCUS_CONSUMER_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_SOURCE_NAME).resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_FOCUS_SHADOW_PLAN_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_STATE_NAME).resolve()


def load_consumer_state(path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    p = (path or default_source_path()).resolve()
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, "consumer_state_not_object"
        return data, None
    except FileNotFoundError:
        return None, "consumer_state_missing"
    except json.JSONDecodeError as exc:
        return None, "consumer_state_json_invalid:%s" % exc
    except Exception as exc:
        return None, "consumer_state_read_error:%s" % exc


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


def _plan_id(target: str, candidate: Mapping[str, Any]) -> str:
    raw = "|".join([
        str(target or ""),
        str(candidate.get("focus_id") or ""),
        str(candidate.get("namespace") or ""),
        str(candidate.get("state_hash") or ""),
        str(candidate.get("primary_action") or ""),
    ])
    return "gap_shadow:%s" % hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _shadow_action_for_target(target: str, candidate: Mapping[str, Any]) -> str:
    reason = str(candidate.get("reason") or "").strip().lower()
    recommended = str(candidate.get("recommended_next") or "").strip().lower()
    if target == "replay":
        return "shadow_replay_review_candidate"
    if target == "dream":
        return "shadow_dream_consolidation_candidate"
    if target == "explore":
        if reason in ("needs_policy_evidence", "missing_policy_evidence") or recommended == "explore":
            return "shadow_explore_evidence_candidate"
        return "shadow_explore_probe_candidate"
    if target == "runner_priority":
        return "shadow_runner_priority_hint"
    return "shadow_unknown_target_candidate"


def _future_gate_for_target(target: str) -> Dict[str, Any]:
    if target == "replay":
        return {
            "required_future_stage": "replay_candidate_review",
            "must_remain_dbwriter_only": True,
            "policy_write_allowed_here": False,
            "notes": [
                "Nur vorhandene Erfahrung/SnapChains pruefen.",
                "Kein policy_rules-Write ohne separates Replay-Policy-Gate.",
            ],
        }
    if target == "dream":
        return {
            "required_future_stage": "dream_shadow_review_or_ledger_gate",
            "must_use_ledger_before_write": True,
            "policy_write_allowed_here": False,
            "notes": [
                "Nur Kandidat fuer Dream-Konsolidierung.",
                "Kein Dream-Start und kein Mini-Write in dieser Stufe.",
            ],
        }
    if target == "explore":
        return {
            "required_future_stage": "runner_or_explore_scheduler_gate",
            "runner_start_allowed_here": False,
            "policy_write_allowed_here": False,
            "notes": [
                "Nur Kandidat fuer spaetere Episodenplanung.",
                "Keine Scheduling-Aenderung in dieser Stufe.",
            ],
        }
    if target == "runner_priority":
        return {
            "required_future_stage": "runner_priority_shadow_review",
            "runner_start_allowed_here": False,
            "policy_write_allowed_here": False,
            "notes": [
                "Nur Prioritaets-Hinweis fuer spaetere Runner-Auswahl.",
                "Keine Laufzeit-Prioritaet wird hier veraendert.",
            ],
        }
    return {"required_future_stage": "unknown", "policy_write_allowed_here": False}


def _minimal_plan(target: str, candidate: Mapping[str, Any]) -> Dict[str, Any]:
    pe = candidate.get("policy_evidence") if isinstance(candidate.get("policy_evidence"), dict) else {}
    return {
        "plan_id": _plan_id(target, candidate),
        "target": target,
        "shadow_action": _shadow_action_for_target(target, candidate),
        "focus_id": _safe_str(candidate.get("focus_id"), 160),
        "namespace": _safe_str(candidate.get("namespace"), 160),
        "state_hash": _safe_str(candidate.get("state_hash"), 4000),
        "kind": _safe_str(candidate.get("kind"), 80),
        "score": round(_as_float(candidate.get("score"), 0.0), 6),
        "avg_confidence": round(_as_float(candidate.get("avg_confidence"), 0.0), 6),
        "reason": _safe_str(candidate.get("reason"), 120),
        "recommended_next": _safe_str(candidate.get("recommended_next"), 120),
        "primary_action": _safe_str(candidate.get("primary_action"), 120),
        "actions": list(candidate.get("actions") or [])[:10] if isinstance(candidate.get("actions"), list) else [],
        "gap_ids": list(candidate.get("gap_ids") or [])[:10] if isinstance(candidate.get("gap_ids"), list) else [],
        "latest_ts": _as_int(candidate.get("latest_ts"), 0),
        "policy_evidence": {
            "rule_count": _as_int(pe.get("rule_count"), 0),
            "total_n": _as_int(pe.get("total_n"), 0),
            "top_action": pe.get("top_action"),
            "top_n": _as_int(pe.get("top_n"), 0),
            "top_q": pe.get("top_q"),
            "second_action": pe.get("second_action"),
            "second_n": _as_int(pe.get("second_n"), 0),
            "second_q": pe.get("second_q"),
            "q_gap": pe.get("q_gap"),
        },
        "future_gate": _future_gate_for_target(target),
        "execution": {
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
            "write_db": False,
            "write_policy": False,
            "shadow_only": True,
        },
    }


def build_shadow_plan(
    *,
    source_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    targets: Optional[Sequence[str]] = None,
    topk: Optional[int] = None,
    max_age_sec: Optional[int] = None,
    allow_stale: Optional[bool] = None,
) -> Dict[str, Any]:
    start = time.time()
    base = _base_dir()
    src = (source_path or default_source_path(base)).resolve()
    out_path = (state_path or default_state_path(base)).resolve()
    target_list = list(targets or parse_csv(_env_str("OROMA_GAP_FOCUS_SHADOW_PLAN_TARGETS", ",".join(DEFAULT_TARGETS)), DEFAULT_TARGETS))
    target_list = [t for t in target_list if t]
    if not target_list:
        target_list = list(DEFAULT_TARGETS)
    top_n = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_FOCUS_SHADOW_PLAN_TOPK", 10)))
    max_age = int(max_age_sec if max_age_sec is not None else _env_int("OROMA_GAP_FOCUS_SHADOW_PLAN_MAX_AGE_SEC", 7200))
    stale_allowed = bool(allow_stale if allow_stale is not None else _env_bool("OROMA_GAP_FOCUS_SHADOW_PLAN_ALLOW_STALE", False))

    now = _now_ts()
    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "read_only_gap_shadow_plan",
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
        },
        "safety": {
            "db_access": False,
            "db_writes": False,
            "policy_writes": False,
            "schema_changes": False,
            "runner_starts": False,
            "dream_starts": False,
            "replay_starts": False,
            "state_json_write": True,
            "shadow_only": True,
        },
        "source": {},
        "summary": {},
        "plans": {},
        "blocked": [],
        "errors": [],
    }

    consumer_state, err = load_consumer_state(src)
    if err or consumer_state is None:
        doc["source"] = {"ok": False, "reason": err or "consumer_state_unavailable"}
        doc["summary"] = {"ok": False, "blocked_reason": err or "consumer_state_unavailable", "dt_sec": round(time.time() - start, 3)}
        return doc

    source_ts = _as_int(consumer_state.get("generated_at_ts"), 0)
    source_age = max(0, now - source_ts) if source_ts > 0 else None
    source_stale = bool(source_age is None or (max_age > 0 and int(source_age) > max_age))
    consumers = consumer_state.get("consumers") if isinstance(consumer_state.get("consumers"), dict) else {}
    source_summary = consumer_state.get("summary") if isinstance(consumer_state.get("summary"), dict) else {}

    doc["source"] = {
        "ok": bool(consumer_state.get("ok", False)),
        "mode": consumer_state.get("mode"),
        "version": consumer_state.get("version"),
        "generated_at_ts": source_ts,
        "generated_at_iso": consumer_state.get("generated_at_iso"),
        "age_sec": source_age,
        "stale": source_stale,
        "summary": source_summary,
    }

    plans: Dict[str, Any] = {}
    blocked: List[Dict[str, Any]] = []
    input_candidates = 0
    malformed = 0
    stale_blocked = 0
    per_target_counts: Dict[str, int] = {}
    per_target_returned: Dict[str, int] = {}

    if source_stale and not stale_allowed:
        # Nur sichtbare Blockierung, keine stillen alten Pläne.
        for target in target_list:
            bucket = consumers.get(target) if isinstance(consumers.get(target), dict) else {}
            cands = bucket.get("candidates") if isinstance(bucket.get("candidates"), list) else []
            stale_blocked += len(cands)
        blocked.append({
            "reason": "source_stale",
            "source_age_sec": source_age,
            "max_age_sec": max_age,
            "items_blocked": stale_blocked,
        })
    else:
        for target in target_list:
            bucket = consumers.get(target) if isinstance(consumers.get(target), dict) else {}
            cands = bucket.get("candidates") if isinstance(bucket.get("candidates"), list) else []
            input_candidates += len(cands)
            target_plans: List[Dict[str, Any]] = []
            for candidate in cands:
                if not isinstance(candidate, dict):
                    malformed += 1
                    continue
                if not str(candidate.get("focus_id") or "").strip() or not str(candidate.get("state_hash") or "").strip():
                    malformed += 1
                    blocked.append({
                        "target": target,
                        "focus_id": candidate.get("focus_id"),
                        "reason": "missing_focus_id_or_state_hash",
                    })
                    continue
                target_plans.append(_minimal_plan(target, candidate))
            target_plans = sorted(target_plans, key=lambda x: (float(x.get("score") or 0.0), int(x.get("latest_ts") or 0)), reverse=True)
            per_target_counts[target] = len(target_plans)
            selected = target_plans[:top_n]
            per_target_returned[target] = len(selected)
            plans[target] = {
                "status": "shadow_ready" if selected else "empty_shadow",
                "count_total": len(target_plans),
                "count_returned": len(selected),
                "items": selected,
                "execution": {
                    "start_runner": False,
                    "start_replay": False,
                    "start_dream": False,
                    "write_db": False,
                    "write_policy": False,
                    "note": "Nur Shadow-Plan; keine Ausfuehrung und keine Writes.",
                },
            }

    # Bei Stale-Blockierung trotzdem leere Zielcontainer schreiben, damit UI/Status
    # stabil bleibt und der Grund im selben Dokument sichtbar ist.
    for target in target_list:
        plans.setdefault(target, {
            "status": "blocked_shadow" if stale_blocked else "empty_shadow",
            "count_total": 0,
            "count_returned": 0,
            "items": [],
            "execution": {
                "start_runner": False,
                "start_replay": False,
                "start_dream": False,
                "write_db": False,
                "write_policy": False,
                "note": "Keine Ausfuehrung in dieser Stufe.",
            },
        })
        per_target_counts.setdefault(target, 0)
        per_target_returned.setdefault(target, 0)

    doc["ok"] = True
    doc["plans"] = plans
    doc["blocked"] = blocked[:50]
    doc["summary"] = {
        "ok": True,
        "dt_sec": round(time.time() - start, 3),
        "input_candidates": input_candidates,
        "malformed_items": malformed,
        "stale_blocked": stale_blocked,
        "blocked_total": len(blocked),
        "per_target_counts": per_target_counts,
        "per_target_returned": per_target_returned,
        "source_stale": source_stale,
        "source_age_sec": source_age,
        "state_written": False,
        "shadow_only": True,
    }
    return doc


def write_shadow_plan(doc: Mapping[str, Any], state_path: Optional[Path] = None) -> Path:
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
    "DEFAULT_TARGETS",
    "build_shadow_plan",
    "write_shadow_plan",
    "load_consumer_state",
    "default_source_path",
    "default_state_path",
    "parse_csv",
]

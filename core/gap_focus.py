#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_focus.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap-Focus Read-Only Consumer View
# Version:   v0.3.0-evidence-aware-replay-selector
# Stand:     2026-07-12
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist der zweite konservative Schritt nach der Gap-Learning-Bridge.
# Die Bridge erzeugt aus knowledge_gaps eine priorisierte Datei:
#
#     data/state/gap_learning_focus.json
#
# Dieses Modul liest diese Datei und baut daraus eine rein lesende Verbraucher-
# Sicht für spätere Lernpfade:
#
#     gap_learning_focus.json
#         -> read-only Consumer-Buckets für Explore / Replay / Dream / Runner
#         -> data/state/gap_focus_consumer.json
#
# WICHTIG: Dieser Code startet keine Runner, kein Replay, keinen DreamWorker und
# schreibt keine Policy. Er macht nur sichtbar, welche Gap-Fokus-Kandidaten für
# welchen späteren Verbraucher fachlich passen würden. Damit wird der Lernbedarf
# maschinenlesbar weitergereicht, ohne Gaps als Reward oder Direct Evidence zu
# missbrauchen.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine GUI-, Qt-, Wayland- oder X11-Abhängigkeiten.
# - Kein DB-Zugriff: Es wird keine SQLite-Datenbank geöffnet, gelesen oder
#   beschrieben. Quelle ist ausschließlich die State-JSON der Gap-Bridge.
# - Keine Policy-Writes: policy_rules/rules bleiben unberührt.
# - Keine Jobs: Dieses Modul startet keine Subprozesse und triggert keine Runner.
# - State-Write optional und atomar: Nur data/state/gap_focus_consumer.json als
#   Diagnose-/Routing-Sicht, tmp+fsync+replace.
# - Stale-Gate: Zu alte Fokusdateien werden im Default nicht als Kandidaten an
#   Consumer-Buckets durchgereicht, sondern sichtbar blockiert.
# - Fail-soft: Fehler erscheinen im Ergebnisdokument; der aufrufende Orchestrator
#   kann weiterlaufen.
#
# WARUM EIGENES CORE-MODUL?
# -------------------------
# Spätere Runner/Dream/Replay-Pfade sollen dieselbe sichere Lese- und Filterlogik
# wiederverwenden können, statt jeweils eigene JSON-Parser zu bauen. Das Modul ist
# daher klein, stdlib-only und importierbar, während tools/gap_focus_consumer.py
# nur die CLI/State-Refresh-Hülle bereitstellt.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_GAP_LEARNING_STATE_PATH=/opt/ai/oroma/data/state/gap_learning_focus.json
#   OROMA_GAP_FOCUS_CONSUMER_STATE_PATH=/opt/ai/oroma/data/state/gap_focus_consumer.json
#   OROMA_GAP_FOCUS_CONSUMER_TARGETS=explore,replay,dream,runner_priority
#   OROMA_GAP_FOCUS_CONSUMER_TOPK=10
#   OROMA_GAP_FOCUS_CONSUMER_MAX_AGE_SEC=7200
#   OROMA_GAP_FOCUS_CONSUMER_ALLOW_STALE=0
#   OROMA_GAP_FOCUS_CONSUMER_NAMESPACE_ALLOWLIST=game:*
#   OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_SCHEMAS=snake:pro_v2
#   OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_REQUIRE_POLICY=1
#
# CLI-Beispiele siehe tools/gap_focus_consumer.py.
# =============================================================================

from __future__ import annotations

import fnmatch
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "v0.3.0-evidence-aware-replay-selector"
DEFAULT_SOURCE_NAME = "gap_learning_focus.json"
DEFAULT_STATE_NAME = "gap_focus_consumer.json"
DEFAULT_TARGETS = ("explore", "replay", "dream", "runner_priority")
DEFAULT_REPLAY_REFERENCE_SCHEMAS = ("snake:pro_v2",)


def _now_ts() -> int:
    return int(time.time())


def _iso(ts: Optional[int] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(int(ts if ts is not None else _now_ts())))


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


def _base_dir() -> Path:
    return Path(os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma").resolve()


def default_source_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_LEARNING_STATE_PATH") or os.environ.get("OROMA_GAP_FOCUS_SOURCE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_SOURCE_NAME).resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_FOCUS_CONSUMER_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_STATE_NAME).resolve()


def parse_csv(raw: str, default: Sequence[str] = ()) -> List[str]:
    text = str(raw or "").replace(";", ",")
    out = [p.strip() for p in text.split(",") if p.strip()]
    return out if out else list(default)


def _namespace_allowed(namespace: str, patterns: Sequence[str]) -> bool:
    ns = str(namespace or "").strip()
    if not patterns:
        return True
    if not ns:
        return False
    return any(fnmatch.fnmatchcase(ns, pat) for pat in patterns)


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


def _safe_str(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > int(limit):
        return text[: max(0, int(limit) - 3)] + "..."
    return text


def load_focus_state(path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    p = (path or default_source_path()).resolve()
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, "focus_state_not_object"
        return data, None
    except FileNotFoundError:
        return None, "focus_state_missing"
    except json.JSONDecodeError as exc:
        return None, "focus_state_json_invalid:%s" % exc
    except Exception as exc:
        return None, "focus_state_read_error:%s" % exc


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


def _policy_total_n(item: Mapping[str, Any]) -> int:
    pe = item.get("policy_evidence") if isinstance(item.get("policy_evidence"), dict) else {}
    return _as_int(pe.get("total_n"), 0)


def _policy_rule_count(item: Mapping[str, Any]) -> int:
    pe = item.get("policy_evidence") if isinstance(item.get("policy_evidence"), dict) else {}
    return _as_int(pe.get("rule_count"), 0)


def _q_gap(item: Mapping[str, Any]) -> Optional[float]:
    pe = item.get("policy_evidence") if isinstance(item.get("policy_evidence"), dict) else {}
    value = pe.get("q_gap")
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _state_schema(item: Mapping[str, Any]) -> str:
    """Leitet das versionierte State-Schema konservativ aus Metadaten/Hash ab."""
    explicit = _safe_str(item.get("state_schema") or item.get("state_schema_guess"), 160)
    if explicit:
        return explicit
    state_hash = _safe_str(item.get("state_hash"), 4000)
    parts = state_hash.split(":", 2)
    if len(parts) >= 2 and parts[0] and parts[1]:
        return "%s:%s" % (parts[0], parts[1])
    return ""


def _reference_capability_available(item: Mapping[str, Any]) -> bool:
    cap = item.get("reference_capability") if isinstance(item.get("reference_capability"), dict) else {}
    return bool(cap.get("available"))


def _reference_replay_decision(
    item: Mapping[str, Any],
    schemas: Sequence[str],
    require_policy: bool,
) -> Tuple[bool, str]:
    """Klassifiziert nur die Referenzstrategie; erzeugt weder Intent noch Evidence."""
    schema = _state_schema(item)
    if not schema or schema not in set(str(x).strip() for x in schemas if str(x).strip()):
        return False, "state_schema_not_in_replay_reference_allowlist"
    if require_policy and (_policy_rule_count(item) <= 0 or _policy_total_n(item) <= 0):
        return False, "replay_reference_requires_policy_evidence"
    if not _safe_str(item.get("primary_action"), 160):
        return False, "replay_reference_requires_primary_action"
    return True, "reference_schema_with_policy_evidence"


def _targets_for_item(
    item: Mapping[str, Any],
    replay_reference_schemas: Sequence[str] = (),
    replay_reference_require_policy: bool = True,
    replay_reference_require_capability: bool = True,
) -> List[str]:
    """Mappt einen Gap-Fokus konservativ auf mögliche spätere Verbraucher.

    Die Rückgabe bedeutet NICHT: ausführen. Sie bedeutet nur: dieser spätere
    Verbraucher dürfte diesen Fokus fachlich lesen und in einem eigenen Gate als
    Lernziel berücksichtigen.
    """
    rec = str(item.get("recommended_next") or "").strip().lower()
    reason = str(item.get("reason") or "").strip().lower()
    kind = str(item.get("kind") or "").strip().lower()
    total_n = _policy_total_n(item)
    rule_count = _policy_rule_count(item)

    targets: List[str] = []

    # Keine oder sehr wenig Policy-Evidence: echte Exploration ist der sauberste
    # erste Verbraucher. Replay/Dream können ohne vorhandene Erfahrung wenig
    # belastbare Direct Evidence erzeugen.
    if rec == "explore" or reason in ("needs_policy_evidence", "missing_policy_evidence") or total_n <= 0 or rule_count <= 0:
        targets.append("explore")

    # Vergleichslücken oder Unsicherheit: Replay kann vorhandene SnapChains bzw.
    # Experience prüfen, ohne unmittelbar Policy zu schreiben.
    if "replay" in rec or reason in ("needs_comparison_evidence", "needs_replay_evidence") or kind == "high_uncertainty":
        targets.append("replay")

    # Referenzmigration: Ein ausdrücklich freigegebenes State-Schema darf bei
    # vorhandener Policy-Evidence zusätzlich als konservative Replay-Strategie-
    # hypothese erscheinen. Gap bleibt Lernbedarfsquelle; dieser Consumer wählt
    # nur eine spätere Prüfstrategie und startet weder Replay noch Policy-Write.
    reference_replay, _ = _reference_replay_decision(
        item, replay_reference_schemas, replay_reference_require_policy
    )
    if reference_replay:
        targets.append("replay")

    # Dream ist sinnvoll, wenn es bereits Policy-/Reward-Spuren gibt, aber die
    # Evidenz noch konsolidiert/verglichen werden muss.
    if "dream" in rec or (reason in ("needs_comparison_evidence", "needs_dream_evidence") and total_n > 0):
        targets.append("dream")

    # Runner-Priorität ist nur eine Sicht: Spiele/Tasks können später ihre
    # Episodenplanung daran ausrichten. Hier wird nichts gestartet.
    ns = str(item.get("namespace") or "")
    if ns.startswith("game:") and str(item.get("state_hash") or "").strip():
        targets.append("runner_priority")

    # Evidence-aware Referenzselektion: Für ausdrücklich migrierte Schemas
    # bleibt eine Replay-Strategie nur erhalten, wenn die neutrale Capability-
    # Registry bereits direkte, lineagefähige Evidence bestätigt hat. Andere
    # Ziele des Gap-Fokus bleiben unverändert sichtbar.
    schema = _state_schema(item)
    if replay_reference_require_capability and schema in set(str(x).strip() for x in replay_reference_schemas if str(x).strip()):
        if "replay" in targets and not _reference_capability_available(item):
            targets = [t for t in targets if t != "replay"]

    # Dedupe bei stabiler Reihenfolge.
    seen = set()
    out = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _consumer_hint(target: str, item: Mapping[str, Any]) -> str:
    reason = str(item.get("reason") or "").strip()
    rec = str(item.get("recommended_next") or "").strip()
    if target == "explore":
        return "read_only_target_for_future_explore_evidence"
    if target == "replay":
        return "read_only_target_for_future_replay_review"
    if target == "dream":
        return "read_only_target_for_future_dream_consolidation"
    if target == "runner_priority":
        return "read_only_target_for_future_runner_prioritization"
    return "read_only_target:%s:%s" % (rec, reason)


def _minimal_candidate(
    item: Mapping[str, Any],
    target: str,
    replay_reference_schemas: Sequence[str] = (),
    replay_reference_require_policy: bool = True,
) -> Dict[str, Any]:
    pe = item.get("policy_evidence") if isinstance(item.get("policy_evidence"), dict) else {}
    reference_ok, reference_reason = _reference_replay_decision(
        item, replay_reference_schemas, replay_reference_require_policy
    )
    return {
        "focus_id": _safe_str(item.get("focus_id"), 160),
        "namespace": _safe_str(item.get("namespace"), 160),
        "state_hash": _safe_str(item.get("state_hash"), 4000),
        "kind": _safe_str(item.get("kind"), 80),
        "score": round(_as_float(item.get("score"), 0.0), 6),
        "avg_confidence": round(_as_float(item.get("avg_confidence"), 0.0), 6),
        "reason": _safe_str(item.get("reason"), 120),
        "recommended_next": _safe_str(item.get("recommended_next"), 120),
        "consumer": target,
        "consumer_hint": _consumer_hint(target, item),
        "learning_need": {
            "kind": _safe_str(item.get("kind"), 80),
            "reason": _safe_str(item.get("reason"), 120),
            "recommended_next": _safe_str(item.get("recommended_next"), 120),
        },
        "reference_capability": dict(item.get("reference_capability") or {}) if isinstance(item.get("reference_capability"), dict) else None,
        "strategy_selection": {
            "selected_strategy": target,
            "selection_source": (
                "reference_schema_gate" if target == "replay" and reference_ok
                else "existing_gap_focus_mapping"
            ),
            "selection_reason": (
                reference_reason if target == "replay" and reference_ok
                else "existing_reason_recommendation_or_kind_mapping"
            ),
            "state_schema": _state_schema(item),
            "learning_intent_implemented": False,
        },
        "gap_count": _as_int(item.get("gap_count"), 0),
        "gap_ids": list(item.get("gap_ids") or [])[:10] if isinstance(item.get("gap_ids"), list) else [],
        "latest_ts": _as_int(item.get("latest_ts"), 0),
        "primary_action": _safe_str(item.get("primary_action"), 120),
        "actions": list(item.get("actions") or [])[:10] if isinstance(item.get("actions"), list) else [],
        "policy_evidence": {
            "rule_count": _as_int(pe.get("rule_count"), 0),
            "total_n": _as_int(pe.get("total_n"), 0),
            "top_action": pe.get("top_action"),
            "top_n": _as_int(pe.get("top_n"), 0),
            "top_q": pe.get("top_q"),
            "second_action": pe.get("second_action"),
            "second_n": _as_int(pe.get("second_n"), 0),
            "second_q": pe.get("second_q"),
            "q_gap": _q_gap(item),
        },
        "execution": {
            "start_job": False,
            "write_policy": False,
            "write_db": False,
            "requires_future_gate": True,
        },
    }


def build_consumer_view(
    *,
    source_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    targets: Optional[Sequence[str]] = None,
    topk: Optional[int] = None,
    max_age_sec: Optional[int] = None,
    allow_stale: Optional[bool] = None,
    namespace_allowlist: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    start = time.time()
    base = _base_dir()
    src = (source_path or default_source_path(base)).resolve()
    out_path = (state_path or default_state_path(base)).resolve()
    target_list = list(targets or parse_csv(_env_str("OROMA_GAP_FOCUS_CONSUMER_TARGETS", ",".join(DEFAULT_TARGETS)), DEFAULT_TARGETS))
    target_list = [t for t in target_list if t]
    if not target_list:
        target_list = list(DEFAULT_TARGETS)
    target_set = set(target_list)
    per_target_topk = int(topk if topk is not None else _env_int("OROMA_GAP_FOCUS_CONSUMER_TOPK", 10))
    per_target_topk = max(1, per_target_topk)
    max_age = int(max_age_sec if max_age_sec is not None else _env_int("OROMA_GAP_FOCUS_CONSUMER_MAX_AGE_SEC", 7200))
    stale_allowed = bool(allow_stale if allow_stale is not None else _env_bool("OROMA_GAP_FOCUS_CONSUMER_ALLOW_STALE", False))
    ns_patterns = list(namespace_allowlist or parse_csv(_env_str("OROMA_GAP_FOCUS_CONSUMER_NAMESPACE_ALLOWLIST", "game:*"), ("game:*",)))
    replay_reference_schemas = parse_csv(
        _env_str(
            "OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_SCHEMAS",
            ",".join(DEFAULT_REPLAY_REFERENCE_SCHEMAS),
        ),
        DEFAULT_REPLAY_REFERENCE_SCHEMAS,
    )
    replay_reference_require_policy = _env_bool(
        "OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_REQUIRE_POLICY", True
    )
    replay_reference_require_capability = _env_bool(
        "OROMA_GAP_FOCUS_CONSUMER_REPLAY_REFERENCE_REQUIRE_CAPABILITY", True
    )

    now = _now_ts()
    doc: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "read_only_focus_consumer",
        "base": str(base),
        "source_path": str(src),
        "state_path": str(out_path),
        "generated_at_ts": now,
        "generated_at_iso": _iso(now),
        "config": {
            "targets": target_list,
            "topk_per_target": per_target_topk,
            "max_age_sec": max_age,
            "allow_stale": stale_allowed,
            "namespace_allowlist": ns_patterns,
            "replay_reference_schemas": replay_reference_schemas,
            "replay_reference_require_policy": replay_reference_require_policy,
            "replay_reference_require_capability": replay_reference_require_capability,
            "learning_intent_implemented": False,
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
        },
        "source": {},
        "summary": {},
        "consumers": {},
        "blocked": [],
        "errors": [],
    }

    focus_state, err = load_focus_state(src)
    if err or focus_state is None:
        doc["source"] = {"ok": False, "reason": err or "focus_state_unavailable"}
        doc["summary"] = {"ok": False, "blocked_reason": err or "focus_state_unavailable", "dt_sec": round(time.time() - start, 3)}
        return doc

    source_ts = _as_int(focus_state.get("generated_at_ts"), 0)
    source_age = max(0, now - source_ts) if source_ts > 0 else None
    source_stale = bool(source_age is None or (max_age > 0 and int(source_age) > max_age))
    focus_items = focus_state.get("focus") if isinstance(focus_state.get("focus"), list) else []
    source_summary = focus_state.get("summary") if isinstance(focus_state.get("summary"), dict) else {}

    doc["source"] = {
        "ok": bool(focus_state.get("ok", False)),
        "mode": focus_state.get("mode"),
        "version": focus_state.get("version"),
        "generated_at_ts": source_ts,
        "generated_at_iso": focus_state.get("generated_at_iso"),
        "age_sec": source_age,
        "stale": source_stale,
        "focus_items": len(focus_items),
        "summary": source_summary,
    }

    blocked: List[Dict[str, Any]] = []
    buckets: Dict[str, List[Dict[str, Any]]] = {t: [] for t in target_list}
    eligible = 0
    namespace_filtered = 0
    stale_blocked = 0
    malformed = 0
    replay_capability_filtered = 0

    if source_stale and not stale_allowed:
        stale_blocked = len(focus_items)
        blocked.append({
            "reason": "source_stale",
            "source_age_sec": source_age,
            "max_age_sec": max_age,
            "items_blocked": len(focus_items),
        })
    else:
        for item in focus_items:
            if not isinstance(item, dict):
                malformed += 1
                continue
            namespace = str(item.get("namespace") or "").strip()
            state_hash = str(item.get("state_hash") or "").strip()
            focus_id = str(item.get("focus_id") or "").strip()
            if not namespace or not state_hash or not focus_id:
                malformed += 1
                blocked.append({
                    "focus_id": focus_id,
                    "namespace": namespace,
                    "reason": "missing_focus_id_namespace_or_state_hash",
                })
                continue
            if not _namespace_allowed(namespace, ns_patterns):
                namespace_filtered += 1
                continue
            raw_targets = _targets_for_item(
                item, replay_reference_schemas, replay_reference_require_policy,
                False
            )
            item_targets = [
                t for t in _targets_for_item(
                    item, replay_reference_schemas, replay_reference_require_policy,
                    replay_reference_require_capability
                ) if t in target_set
            ]
            if "replay" in raw_targets and "replay" not in item_targets and _state_schema(item) in set(replay_reference_schemas):
                replay_capability_filtered += 1
            if not item_targets:
                blocked.append({
                    "focus_id": focus_id,
                    "namespace": namespace,
                    "reason": "no_enabled_consumer_target",
                    "recommended_next": item.get("recommended_next"),
                })
                continue
            eligible += 1
            for target in item_targets:
                buckets.setdefault(target, []).append(
                    _minimal_candidate(
                        item, target, replay_reference_schemas,
                        replay_reference_require_policy,
                    )
                )

    consumers: Dict[str, Any] = {}
    per_target_counts: Dict[str, int] = {}
    per_target_returned: Dict[str, int] = {}
    for target in target_list:
        items = sorted(buckets.get(target, []), key=lambda x: (float(x.get("score") or 0.0), int(x.get("latest_ts") or 0)), reverse=True)
        per_target_counts[target] = len(items)
        selected = items[:per_target_topk]
        per_target_returned[target] = len(selected)
        consumers[target] = {
            "status": "ready_read_only" if selected else "empty_read_only",
            "count_total": len(items),
            "count_returned": len(selected),
            "candidates": selected,
            "execution": {
                "start_job": False,
                "write_policy": False,
                "write_db": False,
                "note": "Nur Sichtbarkeit fuer spaetere Gates; dieser Consumer startet nichts.",
            },
        }

    doc["ok"] = True
    doc["consumers"] = consumers
    doc["blocked"] = blocked[:50]
    doc["summary"] = {
        "ok": True,
        "dt_sec": round(time.time() - start, 3),
        "input_focus_items": len(focus_items),
        "eligible_focus_items": eligible,
        "namespace_filtered": namespace_filtered,
        "malformed_items": malformed,
        "replay_capability_filtered": replay_capability_filtered,
        "stale_blocked": stale_blocked,
        "blocked_total": len(blocked),
        "per_target_counts": per_target_counts,
        "per_target_returned": per_target_returned,
        "source_stale": source_stale,
        "source_age_sec": source_age,
        "state_written": False,
    }
    return doc


def write_consumer_view(doc: Mapping[str, Any], state_path: Optional[Path] = None) -> Path:
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
    "DEFAULT_REPLAY_REFERENCE_SCHEMAS",
    "build_consumer_view",
    "write_consumer_view",
    "load_focus_state",
    "default_source_path",
    "default_state_path",
]

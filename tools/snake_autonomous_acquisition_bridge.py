#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_autonomous_acquisition_bridge.py
# Projekt: ORÓMA (Offline-First · Headless · Vertical Learning Governance)
# Modul:   Autonome Brücke Replay-/Freshness-Gate → Snake Acquisition V2
# Version: v0.2.0-stale-promotion-revalidation
# Stand:   2026-07-21
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul verbindet den read-only Replay-Evidence-Probe mit dem bereits
# vorhandenen produktiven Snake-Targeted-Acquisition-V2-Writer. Es startet
# höchstens eine exakt gebundene Acquisition pro Aufruf und kennt zwei strikt
# getrennte, fachlich erlaubte Auslöser:
#
# 1. MISSING-SOURCE
#    Ein frischer Snake-Pro-v2-Replay-Kandidat besitzt noch keine historische
#    State-/Action-Evidence. Dieser bestehende Pfad verwendet Generation 0.
#
# 2. STALE-PROMOTION-REVALIDATION
#    Ein Replay-Kandidat besitzt zwar rekonstruierbare direkte Evidence, seine
#    Promotion-Validierungsbasis ist aber älter als das produktive Freshness-
#    Fenster. In diesem Fall wird keine alte Evidence direkt zur Policy-Mutation
#    verwendet. Stattdessen startet die Brücke eine neue, bounded Targeted
#    Acquisition mit ``reacquisition_generation = max(existing)+1``. Erst deren
#    atomar persistierte, exakt lineage-gebundene Evidence darf die Promotion im
#    nachgelagerten Mini-Write-Gate fachlich revalidieren.
#
# PRODUKTIONS- UND GOVERNANCE-INVARIANTEN
# --------------------------------------
# • keine Policy-, Outcome-Queue- oder Promotion-Mutation in diesem Modul
# • maximal eine Promotion und ein Kindprozess pro Aufruf
# • nur game:snake / snake:pro_v2 / target=replay / promotion_review
# • vollständige Live-Identitätsprüfung unmittelbar vor dem Start
# • Revalidation nur bei tatsächlich replay-fähigem direktem Outcome
# • keine neue Generation innerhalb des konfigurierten Acquisition-Cooldowns
# • Generation monoton aus der persistenten Lifecycle-Tabelle abgeleitet
# • exaktes ENV-Enable und unveränderter Confirm-Token erforderlich
# • produktive Persistenz ausschließlich im bestehenden DBWriter-only V2-Writer
# • kein lokaler SQLite-Schreibfallback, keine GUI-, Qt-, Wayland- oder X11-Pfade
# =============================================================================
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v0.2.0-stale-promotion-revalidation"
CONFIRM_REQUIRED = "SNAKE_AUTONOMOUS_ACQUISITION_REVIEWED"
ALLOWED_REASONS = {
    "snake_matching_state_action_step_missing",
    "replay_state_action_source_missing",
}
TERMINAL_ACQUISITION_STATUSES = {
    "evidence_acquired",
    "exhausted_no_direct_outcome",
    "blocked",
}


def _bool(name: str, default: bool = False) -> bool:
    return str(os.environ.get(name, "1" if default else "0")).strip().lower() in {"1", "true", "yes", "on"}


def _build_child_env(parent_env: Mapping[str, str]) -> Dict[str, str]:
    """Return the persistence-child environment with DBWriter explicitly on.

    The bridge has its own enable and review-token gates. Once both are passed,
    the child must reach the global DBWriter because its only productive action
    is the existing atomic lifecycle/evidence transaction. The inherited socket
    and all other operational settings remain untouched. No direct SQLite write
    path is introduced.
    """
    child_env = dict(parent_env)
    child_env["OROMA_DBW_ENABLE"] = "1"
    return child_env


def _load_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state_root_not_object")
    return data


def _candidate_rows(state: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    rows = state.get("candidates")
    if not isinstance(rows, list):
        rows = state.get("blocked")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _in_exact_scope(raw: Mapping[str, Any]) -> bool:
    return (
        str(raw.get("namespace") or "") == "game:snake"
        and str(raw.get("state_schema_guess") or "") == "snake:pro_v2"
        and str(raw.get("target") or "") == "replay"
        and str(raw.get("status") or "") == "promotion_review"
        and str(raw.get("promotion_bucket") or "promotion_candidate_replay") == "promotion_candidate_replay"
    )


def _select_candidate(state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Select the original missing-source candidate, preserving v0.1 behavior."""
    for raw in _candidate_rows(state):
        if not _in_exact_scope(raw):
            continue
        reason = str(raw.get("blocked_reason") or raw.get("recommendation_reason") or "")
        adapter = raw.get("adapter_payload") if isinstance(raw.get("adapter_payload"), dict) else {}
        capability_reason = str(adapter.get("capability_blocked_reason") or "")
        if reason in ALLOWED_REASONS or capability_reason in ALLOWED_REASONS:
            return dict(raw)
    return None


def _is_direct_replay_ready(raw: Mapping[str, Any]) -> bool:
    """Require a concrete, policy-usable direct replay outcome before reacquiring."""
    outcome = str(raw.get("simulated_or_replayed_outcome") or "").strip().lower()
    return (
        _in_exact_scope(raw)
        and bool(raw.get("replay_possible"))
        and bool(raw.get("ready_for_outcome_queue"))
        and str(raw.get("replay_probe_status") or "") == "ready"
        and outcome in {"pos", "neg", "draw"}
    )


def _connect_ro(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _verify_live(db_path: str, candidate: Mapping[str, Any]) -> Dict[str, Any]:
    con = _connect_ro(db_path)
    try:
        columns = {str(r["name"]) for r in con.execute("PRAGMA table_info(gap_policy_promotion_queue)").fetchall()}
        validation_expr = "source_validation_ts" if "source_validation_ts" in columns else "0 AS source_validation_ts"
        row = con.execute(
            f"""SELECT id,promotion_signature,request_signature,namespace,state_hash,
                       primary_action,target,status,promotion_bucket,updated_ts,
                       {validation_expr}
                  FROM gap_policy_promotion_queue WHERE id=?""",
            (int(candidate["id"]),),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError("promotion_missing_live")
    checks = {
        "promotion_signature": str(candidate.get("promotion_signature") or ""),
        "request_signature": str(candidate.get("request_signature") or ""),
        "namespace": "game:snake",
        "state_hash": str(candidate.get("state_hash") or ""),
        "primary_action": str(candidate.get("action") or ""),
        "target": "replay",
        "status": "promotion_review",
        "promotion_bucket": "promotion_candidate_replay",
    }
    for key, expected in checks.items():
        if str(row[key] or "") != expected:
            raise ValueError(f"live_identity_mismatch:{key}")
    return {key: row[key] for key in row.keys()}


def _promotion_age_sec(live: Mapping[str, Any], now_ts: int) -> Optional[int]:
    source_validation_ts = int(live.get("source_validation_ts") or 0)
    updated_ts = int(live.get("updated_ts") or 0)
    freshness_ts = source_validation_ts or updated_ts
    if freshness_ts <= 0:
        return None
    return max(0, int(now_ts) - freshness_ts)


def _reacquisition_plan(
    db_path: str,
    *,
    promotion_id: int,
    promotion_signature: str,
    now_ts: int,
    cooldown_sec: int,
) -> Dict[str, Any]:
    """Derive a monotonic generation and reject rapid repeat acquisitions.

    The plan is based only on persistent lifecycle rows for the exact promotion
    identity. A recent acquiring or terminal row suppresses another child start.
    If historical generations exist, the next generation is ``max+1``; otherwise
    generation 0 remains the first bounded acquisition for this promotion.
    """
    con = _connect_ro(db_path)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_targeted_acquisition_lifecycle'"
        ).fetchone()
        if exists is None:
            return {
                "eligible": True,
                "reason": "lifecycle_table_missing_first_generation",
                "next_generation": 0,
                "latest": None,
            }
        rows = con.execute(
            """SELECT acquisition_id,status,reacquisition_generation,updated_ts,terminal_ts
                 FROM gap_targeted_acquisition_lifecycle
                WHERE promotion_id=? AND promotion_signature=?
                ORDER BY reacquisition_generation DESC,updated_ts DESC,acquisition_id ASC""",
            (int(promotion_id), str(promotion_signature)),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return {"eligible": True, "reason": "first_generation", "next_generation": 0, "latest": None}

    latest = dict(rows[0])
    max_generation = max(int(row["reacquisition_generation"] or 0) for row in rows)
    latest_updated_ts = int(latest.get("updated_ts") or latest.get("terminal_ts") or 0)
    latest_age_sec = max(0, int(now_ts) - latest_updated_ts) if latest_updated_ts > 0 else None
    latest_status = str(latest.get("status") or "")
    recent = latest_age_sec is not None and latest_age_sec < int(cooldown_sec)
    if recent:
        return {
            "eligible": False,
            "reason": "recent_acquisition_cooldown",
            "next_generation": max_generation + 1,
            "latest_age_sec": latest_age_sec,
            "latest": latest,
        }
    if latest_status not in TERMINAL_ACQUISITION_STATUSES:
        return {
            "eligible": False,
            "reason": "previous_acquisition_not_terminal",
            "next_generation": max_generation + 1,
            "latest_age_sec": latest_age_sec,
            "latest": latest,
        }
    return {
        "eligible": True,
        "reason": "terminal_generation_expired",
        "next_generation": max_generation + 1,
        "latest_age_sec": latest_age_sec,
        "latest": latest,
    }


def _select_stale_revalidation_candidate(
    state: Mapping[str, Any],
    db_path: str,
    *,
    now_ts: int,
    promotion_max_age_sec: int,
    cooldown_sec: int,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
    """Select one replay-ready but stale promotion for bounded reacquisition."""
    for raw in _candidate_rows(state):
        if not _is_direct_replay_ready(raw):
            continue
        candidate = dict(raw)
        live = _verify_live(db_path, candidate)
        age_sec = _promotion_age_sec(live, now_ts)
        if age_sec is None or age_sec <= int(promotion_max_age_sec):
            continue
        plan = _reacquisition_plan(
            db_path,
            promotion_id=int(candidate["id"]),
            promotion_signature=str(candidate.get("promotion_signature") or ""),
            now_ts=now_ts,
            cooldown_sec=cooldown_sec,
        )
        plan["promotion_age_sec"] = age_sec
        if bool(plan.get("eligible")):
            return candidate, live, plan
    return None


def _candidate_summary(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "promotion_id": int(candidate["id"]),
        "promotion_signature": candidate.get("promotion_signature"),
        "request_signature": candidate.get("request_signature"),
        "state_hash": candidate.get("state_hash"),
        "action": str(candidate.get("action")),
    }


def main() -> int:
    db_path = os.environ.get("OROMA_DB_PATH", str(ROOT / "data" / "oroma.db"))
    state_path = Path(os.environ.get("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_SOURCE_PATH", str(ROOT / "data/state/gap_replay_evidence_probe.json")))
    out_path = Path(os.environ.get("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_STATE_PATH", str(ROOT / "data/state/snake_autonomous_acquisition_bridge.json")))
    max_age = max(1, int(os.environ.get("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_MAX_STATE_AGE_SEC", "900")))
    promotion_max_age = max(1, int(os.environ.get("OROMA_SNAKE_AUTONOMOUS_REVALIDATION_PROMOTION_MAX_AGE_SEC", os.environ.get("OROMA_GAP_POLICY_MINI_WRITE_PROMOTION_MAX_AGE_SEC", "7200"))))
    cooldown_sec = max(1, int(os.environ.get("OROMA_SNAKE_AUTONOMOUS_REACQUISITION_COOLDOWN_SEC", "7200")))
    enable = _bool("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_ENABLE", False)
    confirm_ok = os.environ.get("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_CONFIRM", "") == CONFIRM_REQUIRED
    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "write_enable": enable,
        "confirm_ok": confirm_ok,
        "promotion_max_age_sec": promotion_max_age,
        "reacquisition_cooldown_sec": cooldown_sec,
        "errors": [],
    }
    try:
        if not state_path.exists():
            result.update(status="source_state_missing", action_started=False)
        else:
            state_age = max(0, int(time.time() - state_path.stat().st_mtime))
            result["source_state_age_sec"] = state_age
            if state_age > max_age:
                result.update(status="source_state_stale", action_started=False)
            else:
                state = _load_json(state_path)
                mode = "missing_source"
                generation = 0
                candidate = _select_candidate(state)
                live: Optional[Dict[str, Any]] = None
                plan: Optional[Dict[str, Any]] = None
                if candidate is not None:
                    live = _verify_live(db_path, candidate)
                else:
                    selected = _select_stale_revalidation_candidate(
                        state,
                        db_path,
                        now_ts=int(time.time()),
                        promotion_max_age_sec=promotion_max_age,
                        cooldown_sec=cooldown_sec,
                    )
                    if selected is not None:
                        candidate, live, plan = selected
                        mode = "stale_promotion_revalidation"
                        generation = int(plan["next_generation"])

                if candidate is None or live is None:
                    result.update(status="no_eligible_acquisition_candidate", action_started=False)
                else:
                    result.update(
                        trigger_mode=mode,
                        candidate=_candidate_summary(candidate),
                        live_promotion=live,
                        reacquisition_generation=generation,
                    )
                    if plan is not None:
                        result["reacquisition_plan"] = plan
                    if not enable:
                        result.update(status="write_enable_disabled", action_started=False)
                    elif not confirm_ok:
                        result.update(ok=False, status="confirm_mismatch", action_started=False)
                    else:
                        cmd = [
                            sys.executable,
                            str(ROOT / "tools/snake_targeted_acquisition_v2_persist.py"),
                            "--db", db_path,
                            "--promotion-id", str(int(candidate["id"])),
                            "--source-scan-limit", os.environ.get("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_SOURCE_SCAN_LIMIT", "5000"),
                            "--reacquisition-generation", str(generation),
                            "--write-g3",
                            "--confirm", "PERSIST_TARGETED_ACQUISITION_EVIDENCE_V2",
                        ]
                        proc = subprocess.run(
                            cmd,
                            cwd=str(ROOT),
                            env=_build_child_env(os.environ),
                            text=True,
                            capture_output=True,
                            timeout=float(os.environ.get("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_TIMEOUT_SEC", "180")),
                        )
                        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
                        result.update(status="acquisition_invoked", action_started=True, child_rc=proc.returncode, acquisition=payload)
                        if proc.returncode != 0 or not bool(payload.get("ok")):
                            result["ok"] = False
                            result["errors"].append("targeted_acquisition_child_failed")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, out_path)
    except subprocess.TimeoutExpired as exc:
        result.update(ok=False, status="acquisition_timeout", action_started=True, child_timeout_sec=float(exc.timeout), child_rc=None)
        result["errors"].append(f"TimeoutExpired:{exc}")
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    except Exception as exc:
        result.update(ok=False, status="bridge_failed", action_started=False)
        result["errors"].append(f"{type(exc).__name__}:{exc}")
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

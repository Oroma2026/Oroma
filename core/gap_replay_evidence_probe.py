#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_replay_evidence_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Targeted Replay Evidence Probe · Headless · State-only
# Version:   v0.3.2-canonical-direct-outcome
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die direkte Anschlussstufe nach dem Targeted Evidence Probe.
# Der vorherige Probe hat fuer aktuelle Gap-Promotion-Kandidaten gezeigt, dass
# alte direkte Evidence nicht auditierbar rueckverlinkt ist. Dieses Modul sucht
# deshalb nicht noch einmal breit in historischen Tabellen, sondern prueft sehr
# wenige Kandidaten ueber einen expliziten, domänenspezifischen Replay-/Simulations-
# Adapter.
#
# Produktive Adapter:
#   1) game:flappy / flappy:v1
#      Deterministische kurze Headless-Simulation aus dem quantisierten State.
#   2) game:snake / snake:pro_v2
#      Keine Zustandsrekonstruktion. Der Adapter sucht ausschliesslich einen
#      bereits gespeicherten SnapChain-Step mit exakt identischem state_hash und
#      identischer Aktion. Nur ein direkt am selben Step gespeichertes Outcome
#      darf als replay_reconstructed Evidence vorgeschlagen werden. Fehlende,
#      mehrdeutige oder widerspruechliche Lineage blockiert fail-closed.
#
# WICHTIGER FACHLICHER RAHMEN
# ---------------------------
# Dieses Modul erzeugt noch KEINEN policy_rules-Write und auch noch keinen
# Evidence-DB-Write. Es schreibt nur:
#   data/state/gap_replay_evidence_probe.json
#
# Die erzeugten Outcomes sind "adapter_replay_probe"-Evidence-Vorschlaege. Sie
# muessen vor einem echten Policy-Mini-Write weiterhin durch eine eigene
# Outcome-Queue-/Promotion-Gate-Stufe gehen. Dadurch wird verhindert, dass ein
# approximierter Simulationszustand direkt als hartes Wissen in policy_rules
# gelangt.
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11-, pygame- oder GUI-Abhaengigkeit.
# - SQLite nur read-only via URI mode=ro.
# - Keine DB-Writes, keine Schemaaenderungen, keine policy_rules-/rules-Writes.
# - Keine globalen ReplayManager-Starts, keine Runner-Starts, keine Dream-Starts.
# - Kein Massenscan: nur 1-3 Promotion-Kandidaten, keine Millionen-Tabellen-Scans.
# - Source-Volumen nutzt High-Water-Marks; niemals COUNT(*) auf policy_rules.
# - Policy-Snapshot darf Kontext liefern, aber niemals allein Outcome sein.
# - State-Write ist best-effort; stdout bleibt immer vollstaendig nutzbar.
# - Root-Manual-Laeufe setzen die State-Datei best-effort auf oroma:oroma 664.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_REPLAY_EVIDENCE_PROBE_STATE_PATH=/opt/ai/oroma/data/state/gap_replay_evidence_probe.json
#   OROMA_GAP_REPLAY_EVIDENCE_PROBE_LIMIT=3
#   OROMA_GAP_REPLAY_EVIDENCE_PROBE_TOPK=3
#   OROMA_GAP_REPLAY_EVIDENCE_PROBE_HORIZON_STEPS=80
#   OROMA_GAP_REPLAY_EVIDENCE_PROBE_BUCKETS=promotion_candidate_replay
#   OROMA_GAP_REPLAY_EVIDENCE_PROBE_SNAKE_SNAPCHAIN_SCAN_LIMIT=500
# =============================================================================

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import grp
import re
import sqlite3
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.direct_outcome_normalization import normalize_direct_outcome as _normalize_direct_outcome
from core.replay_evidence_capability import (
    build_replay_evidence_capability_context,
    check_replay_evidence_capability,
    validate_targeted_evidence_snapchain,
)

VERSION = "v0.3.2-canonical-direct-outcome"
PROMOTION_TABLE = "gap_policy_promotion_queue"
POLICY_TABLE = "policy_rules"
DEFAULT_STATE_NAME = "gap_replay_evidence_probe.json"
DEFAULT_BUCKETS = ("promotion_candidate_replay",)
DEFAULT_SNAKE_SNAPCHAIN_SCAN_LIMIT = 500


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


def _sqlite_ro_uri(db_path: Path) -> str:
    return "file:%s?mode=ro" % str(db_path.resolve())


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (str(table),)).fetchone()
    return bool(row)


def _table_high_water_mark(con: sqlite3.Connection, table: str) -> Tuple[int, str]:
    """Return a fast, non-blocking row-volume indicator.

    The replay probe only needs source-volume observability. An exact COUNT(*) on
    policy_rules scans more than 21 million rows on the live Pi and dominated the
    complete runtime although only three indexed policy lookups were required.

    We therefore use SQLite's AUTOINCREMENT sequence or MAX(rowid), both of which
    are fast B-tree metadata/high-water-mark operations. The returned value is an
    estimate when rows were deleted and MUST NOT be interpreted as an exact count.
    """
    if not _table_exists(con, table):
        return 0, "missing"
    try:
        row = con.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (str(table),)).fetchone()
        if row is not None and row[0] is not None:
            return _as_int(row[0], 0), "sqlite_sequence_high_water_mark"
    except Exception:
        pass
    try:
        row = con.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()
        return _as_int(row[0] if row else 0, 0), "max_rowid_high_water_mark"
    except Exception as exc:
        return 0, f"unavailable:{type(exc).__name__}"


def default_db_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "oroma.db").resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_REPLAY_EVIDENCE_PROBE_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "state" / DEFAULT_STATE_NAME).resolve()


def _apply_oroma_state_ownership(path: Path) -> None:
    if os.geteuid() != 0:
        return
    try:
        uid = pwd.getpwnam("oroma").pw_uid
        gid = grp.getgrnam("oroma").gr_gid
        os.chown(str(path), uid, gid)
    except Exception:
        return


def atomic_write_json_best_effort(path: Path, data: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
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


def state_hash_format(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "none"
    if re.fullmatch(r"[0-9a-fA-F]{16,128}", text):
        return "raw_hex"
    if ":pro_v" in text or re.match(r"^[a-zA-Z0-9_]+:[a-zA-Z0-9_]+:", text):
        return "schema_prefixed"
    if "|" in text and (":" in text or "=" in text):
        return "structured_pipe"
    if ":" in text or "=" in text:
        return "structured_string"
    return "unknown"


def action_format(value: Any) -> str:
    if value is None:
        return "none"
    text = str(value).strip()
    if not text:
        return "none"
    if re.fullmatch(r"[-+]?\d+", text):
        return "int"
    if re.fullmatch(r"[-+]?\d+\.\d+", text):
        return "float_string"
    return "string"


def _state_schema_guess(state_hash: str) -> str:
    text = str(state_hash or "").strip()
    if text.startswith("flappy:v1:"):
        return "flappy:v1"
    parts = text.split(":")
    if len(parts) >= 3 and parts[1].startswith("pro_v"):
        return f"{parts[0]}:{parts[1]}"
    if len(parts) >= 2 and parts[1].startswith("v"):
        return f"{parts[0]}:{parts[1]}"
    if "|" in text:
        return "pipe_structured"
    if state_hash_format(text) == "raw_hex":
        return "raw_hex"
    return "unknown"


def _load_candidates(
    con: sqlite3.Connection,
    buckets: Sequence[str],
    limit: int,
    namespaces: Sequence[str],
    state_schemas: Sequence[str],
    targets: Sequence[str],
) -> List[sqlite3.Row]:
    if not _table_exists(con, PROMOTION_TABLE):
        return []
    bucket_list = [str(b).strip() for b in buckets if str(b).strip()]
    if not bucket_list:
        bucket_list = list(DEFAULT_BUCKETS)
    placeholders = ",".join(["?"] * len(bucket_list))
    clauses = [f"promotion_bucket IN ({placeholders})", "status='promotion_review'"]
    params: List[Any] = [*bucket_list]
    ns_list = [str(v).strip() for v in namespaces if str(v).strip()]
    target_list = [str(v).strip() for v in targets if str(v).strip()]
    schema_list = [str(v).strip() for v in state_schemas if str(v).strip()]
    if ns_list:
        clauses.append("namespace IN (" + ",".join(["?"] * len(ns_list)) + ")")
        params.extend(ns_list)
    if target_list:
        clauses.append("target IN (" + ",".join(["?"] * len(target_list)) + ")")
        params.extend(target_list)
    if schema_list:
        schema_clauses = []
        for schema in schema_list:
            schema_clauses.append("state_hash LIKE ?")
            params.append(schema + ":%")
        clauses.append("(" + " OR ".join(schema_clauses) + ")")
    sql = f"""
        SELECT * FROM {PROMOTION_TABLE}
        WHERE {' AND '.join(clauses)}
        ORDER BY score DESC, updated_ts DESC, id ASC
        LIMIT ?
    """
    params.append(int(limit))
    return list(con.execute(sql, params).fetchall())


def _policy_snapshot(con: sqlite3.Connection, namespace: str, state_hash: str, action: str) -> Dict[str, Any]:
    if not _table_exists(con, POLICY_TABLE):
        return {"available": False, "reason": "policy_rules_table_missing"}
    rows = list(con.execute(
        f"""
        SELECT id, namespace, state_hash, action, n, pos, neg, draw, q, last_ts
        FROM {POLICY_TABLE}
        WHERE namespace=? AND state_hash=?
        ORDER BY n DESC, q DESC, id ASC
        LIMIT 20
        """,
        (namespace, state_hash),
    ).fetchall())
    actions: List[Dict[str, Any]] = []
    selected: Optional[Dict[str, Any]] = None
    oldest: Optional[int] = None
    newest: Optional[int] = None
    for r in rows:
        item = {
            "id": _as_int(r["id"]),
            "action": _safe_str(r["action"], 120),
            "n": _as_int(r["n"]),
            "pos": _as_int(r["pos"]),
            "neg": _as_int(r["neg"]),
            "draw": _as_int(r["draw"]),
            "q": _as_float(r["q"]),
            "last_ts": _as_int(r["last_ts"], 0),
        }
        actions.append(item)
        if str(item["action"]) == str(action):
            selected = item
        ts = _as_int(r["last_ts"], 0)
        if ts:
            oldest = ts if oldest is None else min(oldest, ts)
            newest = ts if newest is None else max(newest, ts)
    return {
        "available": bool(rows),
        "rule_count": len(rows),
        "state_action_rule_count": 1 if selected else 0,
        "selected_action_rule": selected,
        "actions": actions,
        "oldest_policy_rule_ts": oldest,
        "newest_policy_rule_ts": newest,
    }


def _parse_flappy_v1_state_hash(state_hash: str) -> Tuple[Optional[Dict[str, int]], Optional[str]]:
    text = str(state_hash or "").strip()
    if not text.startswith("flappy:v1:"):
        return None, "unsupported_state_schema"
    # Format: flappy:v1:y=5:dx=26:gy=30:gh=10:vs=-1
    data: Dict[str, int] = {}
    for part in text.split(":")[2:]:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k in {"y", "dx", "gy", "gh", "vs"}:
            try:
                data[k] = int(float(v))
            except Exception:
                return None, f"invalid_flappy_v1_value:{k}={v}"
    missing = [k for k in ("y", "dx", "gy", "gh", "vs") if k not in data]
    if missing:
        return None, "missing_flappy_v1_keys:" + ",".join(missing)
    return data, None


def _bucket_mid(value: int, bins: int = 40, lo: float = 0.0, hi: float = 1.0) -> float:
    v = max(0, min(int(bins), int(value)))
    # _qb uses int(float * bins). Midpoint approximation of the original bucket.
    return max(lo, min(hi, (float(v) + 0.5) / float(bins)))


def _load_flappy_engine(base: Path):
    module_path = base / "mini_programs" / "flappybird.py"
    spec = importlib.util.spec_from_file_location("oroma_gap_replay_flappybird_engine", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load flappy engine from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("oroma_gap_replay_flappybird_engine", mod)
    spec.loader.exec_module(mod)
    return mod.FlappyBird, mod.FBConfig


def _heuristic_flappy_action(st: Mapping[str, Any]) -> int:
    y = float(st.get("y", 0.5) or 0.5)
    vy = float(st.get("vy", 0.0) or 0.0)
    dx = float(st.get("dx", 1.0) or 1.0)
    gap_y = float(st.get("gap_y", 0.5) or 0.5)
    gap_h = float(st.get("gap_h", 0.25) or 0.25)
    if y < 0.16 and vy > -0.45:
        return 1
    if y > 0.88 and vy < 0.10:
        return 0
    if dx <= 0.22:
        if y < gap_y - (gap_h * 0.20) or vy > 0.22:
            return 1
        return 0
    target = max(0.08, min(0.92, gap_y - (0.04 if dx > 0.35 else 0.01)))
    dy = target - y
    if dy > 0.07 or vy > 0.30:
        return 1
    return 0


def _run_flappy_v1_adapter(base: Path, state_hash: str, action: str, horizon_steps: int) -> Dict[str, Any]:
    parsed, parse_error = _parse_flappy_v1_state_hash(state_hash)
    if parse_error or parsed is None:
        return {
            "adapter": "flappy_v1_quantized_headless_replay",
            "replay_possible": False,
            "blocked_reason": parse_error or "parse_failed",
            "outcome": None,
            "confidence": 0.0,
        }
    if str(action).strip() not in {"0", "1"}:
        return {
            "adapter": "flappy_v1_quantized_headless_replay",
            "replay_possible": False,
            "blocked_reason": "unsupported_action_for_flappy_v1",
            "outcome": None,
            "confidence": 0.0,
        }

    FlappyBird, FBConfig = _load_flappy_engine(base)
    cfg = FBConfig(seed=1337)
    cfg.max_steps = max(10, int(horizon_steps) + 5)
    env = FlappyBird(cfg)
    env.reset(seed=1337)

    y = _bucket_mid(parsed["y"])
    dx = _bucket_mid(parsed["dx"], hi=1.2)
    gap_y = _bucket_mid(parsed["gy"])
    gap_h = max(0.05, min(0.6, _bucket_mid(parsed["gh"], hi=0.6)))
    vs = int(parsed["vs"])
    vy = -0.25 if vs < 0 else (0.25 if vs > 0 else 0.0)

    # Directly set the pure headless engine state. This is a local replay probe,
    # not a running ORÓMA game instance and not a global ReplayManager start.
    env.y = float(y)
    env.vy = float(vy)
    env.dx = float(dx)
    env.gap_y = float(gap_y)
    env.gap_h = float(gap_h)
    env.score = 0
    env.steps = 0
    env.alive = True

    initial_state = env.get_state()
    first_action = int(action)
    timeline: List[Dict[str, Any]] = []
    total_reward = 0.0
    done = False
    terminal_reason = None
    passed_any = False
    first_step_done = False
    first_step_reason = None

    for step in range(max(1, int(horizon_steps))):
        st_before = env.get_state()
        a = first_action if step == 0 else _heuristic_flappy_action(st_before)
        st_obj, reward, done, info = env.step(int(a))
        total_reward += float(reward)
        st_after = env.get_state()
        passed_any = bool(passed_any or bool(info.get("passed")))
        terminal_reason = str(info.get("reason") or terminal_reason or "") if done else terminal_reason
        if step == 0:
            first_step_done = bool(done)
            first_step_reason = str(info.get("reason") or "") if done else None
        if step < 12 or done or bool(info.get("passed")):
            timeline.append({
                "step": step,
                "action": int(a),
                "reward": round(float(reward), 6),
                "done": bool(done),
                "info": dict(info or {}),
                "state_after": {
                    "y": round(float(st_after.get("y", 0.0)), 4),
                    "vy": round(float(st_after.get("vy", 0.0)), 4),
                    "dx": round(float(st_after.get("dx", 0.0)), 4),
                    "gap_y": round(float(st_after.get("gap_y", 0.0)), 4),
                    "gap_h": round(float(st_after.get("gap_h", 0.0)), 4),
                    "score": int(st_after.get("score", 0) or 0),
                    "alive": bool(st_after.get("alive", False)),
                },
            })
        if done:
            break

    steps_simulated = int(env.steps)
    if first_step_done:
        outcome = "neg"
        outcome_reason = "candidate_action_immediate_terminal"
        confidence = 0.82
    elif done and terminal_reason in {"world_collision", "pipe_collision"}:
        outcome = "neg"
        outcome_reason = "terminal_within_short_replay_after_candidate_action"
        confidence = 0.62
    elif passed_any or int(env.score) > 0:
        outcome = "pos"
        outcome_reason = "pipe_passed_within_short_replay"
        confidence = 0.58
    else:
        outcome = "draw"
        outcome_reason = "survived_horizon_without_terminal_or_pipe_pass"
        confidence = 0.35

    return {
        "adapter": "flappy_v1_quantized_headless_replay",
        "replay_possible": True,
        "blocked_reason": None,
        "state_reconstruction": {
            "quantized": dict(parsed),
            "continuous_midpoint": {
                "y": round(float(y), 5),
                "vy": round(float(vy), 5),
                "dx": round(float(dx), 5),
                "gap_y": round(float(gap_y), 5),
                "gap_h": round(float(gap_h), 5),
            },
            "approximation": "bucket_midpoint_plus_velocity_sign; not historical exact state",
        },
        "first_action": first_action,
        "horizon_steps": int(horizon_steps),
        "steps_simulated": steps_simulated,
        "total_reward": round(total_reward, 6),
        "terminal": bool(done),
        "terminal_reason": terminal_reason,
        "passed_any": bool(passed_any),
        "final_score": int(env.score),
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "confidence": round(float(confidence), 3),
        "timeline_excerpt": timeline[:20],
        "initial_state": {
            "y": round(float(initial_state.get("y", 0.0)), 4),
            "vy": round(float(initial_state.get("vy", 0.0)), 4),
            "dx": round(float(initial_state.get("dx", 0.0)), 4),
            "gap_y": round(float(initial_state.get("gap_y", 0.0)), 4),
            "gap_h": round(float(initial_state.get("gap_h", 0.0)), 4),
        },
    }



def _decode_snapchain_blob(blob: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Decode one historical SnapChain payload without mutating source data."""
    if blob is None:
        return None, "snapchain_blob_null"
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if isinstance(blob, dict):
        return blob, None
    candidates: List[str] = []
    if isinstance(blob, bytes):
        try:
            candidates.append(blob.decode("utf-8"))
        except Exception:
            pass
        try:
            candidates.append(zlib.decompress(blob).decode("utf-8"))
        except Exception:
            pass
    else:
        candidates.append(str(blob))
    for text in candidates:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data, None
        except Exception:
            continue
    return None, "snapchain_blob_decode_failed"


def _step_state_hash(step: Mapping[str, Any]) -> str:
    for key in ("state_hash", "h", "sh"):
        value = step.get(key)
        if value not in (None, ""):
            return str(value)
    nested = step.get("state")
    if isinstance(nested, Mapping):
        for key in ("state_hash", "h", "sh"):
            value = nested.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _step_action(step: Mapping[str, Any]) -> str:
    for key in ("action", "a", "action_canon", "ac", "action_name"):
        value = step.get(key)
        if value not in (None, ""):
            return str(value)
    return ""



def _snake_snapchain_rows(con: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
    if not _table_exists(con, "snapchains"):
        return []
    lim = max(1, int(limit))
    return list(con.execute(
        """
        SELECT id,ts,quality,blob,status,origin,namespace,source_id,version,weight
        FROM snapchains
        WHERE status='active'
          AND (origin IN ('game:snake','snake') OR namespace IN ('game:snake','snake'))
        ORDER BY id DESC
        LIMIT ?
        """,
        (lim,),
    ).fetchall())


def _reverify_targeted_snapchain_toctou(
    con: sqlite3.Connection,
    capability: Mapping[str, Any],
    *,
    requested_state_hash: str,
    requested_action: str,
) -> Dict[str, Any]:
    """Reload and fully verify the capability-selected targeted SnapChain.

    This is the Probe's Time-of-Check-to-Time-of-Use boundary. No in-memory
    root from the shared capability scan is reused. The row is fetched again
    by id, decoded again, validated again, and bound again to the exact replay
    request before any Evidence proposal is emitted.
    """
    snapchain_id = _as_int(capability.get("snapchain_id"), 0)
    if snapchain_id <= 0:
        return {"ok": False, "reason": "targeted_toctou_snapchain_id_missing"}
    row = con.execute(
        """SELECT id,ts,quality,blob,status,origin,namespace,source_id,version,weight
           FROM snapchains WHERE id=? LIMIT 1""",
        (snapchain_id,),
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": "targeted_toctou_source_missing", "snapchain_id": snapchain_id}
    if str(row["status"] or "") != "active":
        return {"ok": False, "reason": "targeted_toctou_source_not_active", "snapchain_id": snapchain_id}
    root, decode_error = _decode_snapchain_blob(row["blob"])
    if root is None:
        return {
            "ok": False,
            "reason": "targeted_toctou_decode_failed",
            "decode_error": decode_error,
            "snapchain_id": snapchain_id,
        }
    checked = validate_targeted_evidence_snapchain(
        root,
        snapchain_id=_as_int(row["id"]),
        snapchain_source_id=row["source_id"],
        snapchain_ts=_as_int(row["ts"]),
        snapchain_origin=_safe_str(row["origin"], 200),
        snapchain_namespace=_safe_str(row["namespace"], 200),
        snapchain_version=_safe_str(row["version"], 200),
        snapchain_quality=_as_float(row["quality"], 0.0),
        snapchain_weight=_as_float(row["weight"], 1.0),
    )
    lineage = root.get("source_lineage") if isinstance(root.get("source_lineage"), Mapping) else {}
    intervention = root.get("intervention") if isinstance(root.get("intervention"), Mapping) else {}
    result = root.get("result") if isinstance(root.get("result"), Mapping) else {}
    request_state_matches = str(lineage.get("source_state_hash") or "") == str(requested_state_hash or "")
    request_action_matches = str(intervention.get("target_action")) == str(requested_action or "")
    capability_digest_matches = str(checked.get("evidence_digest") or "") == str(capability.get("evidence_digest") or "")
    capability_experiment_matches = str(checked.get("experiment_id") or "") == str(capability.get("experiment_id") or "")
    capability_outcome_matches = str(checked.get("outcome") or "") == str(capability.get("outcome") or "")
    checked_intent = checked.get("learning_intent_lineage") if isinstance(checked.get("learning_intent_lineage"), Mapping) else {}
    capability_intent = capability.get("learning_intent_lineage") if isinstance(capability.get("learning_intent_lineage"), Mapping) else {}
    capability_learning_intent_matches = checked_intent == capability_intent
    blocked_reasons = list(checked.get("blocked_reasons") or [])
    if not request_state_matches:
        blocked_reasons.append("targeted_toctou_request_state_mismatch")
    if not request_action_matches:
        blocked_reasons.append("targeted_toctou_request_action_mismatch")
    if not capability_digest_matches:
        blocked_reasons.append("targeted_toctou_evidence_digest_changed")
    if not capability_experiment_matches:
        blocked_reasons.append("targeted_toctou_experiment_id_changed")
    if not capability_outcome_matches:
        blocked_reasons.append("targeted_toctou_outcome_changed")
    if not capability_learning_intent_matches:
        blocked_reasons.append("targeted_toctou_learning_intent_changed")
    ok = bool(checked.get("valid")) and not blocked_reasons
    return {
        "ok": ok,
        "reason": None if ok else "targeted_toctou_reverification_failed",
        "blocked_reasons": sorted(set(blocked_reasons)),
        "snapchain_id": snapchain_id,
        "experiment_id": checked.get("experiment_id"),
        "evidence_digest": checked.get("evidence_digest"),
        "outcome": checked.get("outcome"),
        "steps_total": checked.get("steps_total"),
        "verification_reason_counts": checked.get("verification_reason_counts"),
        "request_state_matches": request_state_matches,
        "request_action_matches": request_action_matches,
        "capability_digest_matches": capability_digest_matches,
        "capability_experiment_matches": capability_experiment_matches,
        "capability_outcome_matches": capability_outcome_matches,
        "capability_learning_intent_matches": capability_learning_intent_matches,
        "learning_intent_lineage": checked_intent,
        "result_status": checked.get("result_status") or result.get("status"),
        "event": checked.get("event") or result.get("event"),
        "source_reloaded": True,
        "db_open_mode": "read_only_existing_connection",
    }


def _run_snake_pro_v2_snapchain_adapter(
    con: sqlite3.Connection,
    state_hash: str,
    action: str,
    scan_limit: int,
    capability_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Use the shared read-only capability truth and map it to probe output.

    The capability lookup identifies a concrete source. The probe still builds
    the Evidence payload itself and therefore remains the only module allowed to
    turn source metadata into replay_reconstructed Evidence.
    """
    adapter = "snake_pro_v2_snapchain_direct_step_replay"
    cap = check_replay_evidence_capability(
        con,
        namespace="game:snake",
        state_schema="snake:pro_v2",
        state_hash=str(state_hash or ""),
        action=str(action or ""),
        scan_limit=scan_limit,
        context=capability_context,
    )
    if not cap.get("available"):
        technical = cap.get("technical_blocked_reason")
        public_reason = cap.get("blocked_reason")
        legacy_reason = {
            "replay_state_action_source_missing": "snake_matching_state_action_step_missing",
            "replay_direct_evidence_unavailable": "snake_matching_step_without_supported_direct_outcome",
            "replay_direct_evidence_conflict": "snake_direct_step_outcome_conflict",
        }.get(str(public_reason), str(technical or public_reason or "replay_evidence_capability_unavailable"))
        out = {
            "adapter": adapter,
            "replay_possible": False,
            "blocked_reason": legacy_reason,
            "capability_blocked_reason": public_reason,
            "outcome": None,
            "confidence": 0.0,
            "capability": cap,
        }
        return out
    targeted_toctou = None
    if cap.get("source_kind") == "targeted_simulation_snapchain":
        targeted_toctou = _reverify_targeted_snapchain_toctou(
            con,
            cap,
            requested_state_hash=str(state_hash or ""),
            requested_action=str(action or ""),
        )
        if not targeted_toctou.get("ok"):
            return {
                "adapter": adapter,
                "replay_possible": False,
                "blocked_reason": "targeted_toctou_reverification_failed",
                "capability_blocked_reason": None,
                "outcome": None,
                "confidence": 0.0,
                "capability": cap,
                "targeted_toctou": targeted_toctou,
            }
    lineage = {
        "source_kind": cap.get("source_kind"),
        "snapchain_id": cap.get("snapchain_id"),
        "snapchain_ts": cap.get("snapchain_ts"),
        "snapchain_origin": cap.get("snapchain_origin"),
        "snapchain_namespace": cap.get("snapchain_namespace"),
        "snapchain_source_id": cap.get("snapchain_source_id"),
        "snapchain_version": cap.get("snapchain_version"),
        "snapchain_quality": cap.get("snapchain_quality"),
        "snapchain_weight": cap.get("snapchain_weight"),
        "step_index": cap.get("step_index"),
        "step_ts": cap.get("step_ts"),
        "state_schema": cap.get("state_schema"),
        "action_schema": cap.get("action_schema"),
        "step_mode": cap.get("step_mode"),
        "state_hash": cap.get("state_hash"),
        "action": cap.get("action"),
        "outcome_field": cap.get("outcome_field"),
        "outcome_raw": cap.get("outcome_raw"),
        "capability_version": cap.get("capability_version"),
        "learning_intent_lineage": cap.get("learning_intent_lineage") or {},
    }
    return {
        "adapter": adapter,
        "replay_possible": True,
        "blocked_reason": None,
        "outcome": cap.get("outcome"),
        "outcome_reason": (
            "targeted_simulation_snapchain_toctou_verified"
            if cap.get("source_kind") == "targeted_simulation_snapchain"
            else "stored_snake_step_direct_outcome_exact_state_action_match"
        ),
        "confidence": 0.98 if cap.get("source_kind") == "targeted_simulation_snapchain" else 0.95,
        "snapchains_scanned": cap.get("snapchains_scanned"),
        "matching_steps": cap.get("matching_steps_total"),
        "usable_matches": cap.get("matching_direct_outcomes_total"),
        "lineage": lineage,
        "state_reconstruction": None,
        "historical_state_exact": True,
        "simulation_used": False,
        "targeted_toctou": targeted_toctou,
        "toctou_verified": bool(targeted_toctou.get("ok")) if isinstance(targeted_toctou, Mapping) else None,
        "capability": cap,
    }


def _pick_replay_adapter(
    con: sqlite3.Connection, base: Path, namespace: str, state_hash: str, action: str,
    horizon_steps: int, snake_scan_limit: int,
    capability_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ns = str(namespace or "").strip()
    st = str(state_hash or "").strip()
    if ns in {"game:snake", "snake"} and st.startswith("snake:pro_v2:"):
        try:
            return _run_snake_pro_v2_snapchain_adapter(con, st, action, snake_scan_limit, capability_context)
        except Exception as exc:
            return {
                "adapter": "snake_pro_v2_snapchain_direct_step_replay",
                "replay_possible": False,
                "blocked_reason": f"adapter_error:{type(exc).__name__}: {exc}",
                "outcome": None,
                "confidence": 0.0,
            }
    if ns in {"game:flappy", "flappy"} and st.startswith("flappy:v1:"):
        try:
            return _run_flappy_v1_adapter(base, st, action, horizon_steps)
        except Exception as exc:
            return {
                "adapter": "flappy_v1_quantized_headless_replay",
                "replay_possible": False,
                "blocked_reason": f"adapter_error:{type(exc).__name__}: {exc}",
                "outcome": None,
                "confidence": 0.0,
            }
    return {
        "adapter": "none",
        "replay_possible": False,
        "blocked_reason": "replay_adapter_missing_for_namespace_or_schema",
        "outcome": None,
        "confidence": 0.0,
    }


def _recommend(adapter_out: Mapping[str, Any]) -> Tuple[str, str, bool]:
    if not bool(adapter_out.get("replay_possible")):
        br = str(adapter_out.get("blocked_reason") or "replay_not_possible")
        if "adapter_missing" in br:
            return "targeted_dream_needed", br, False
        return "replay_adapter_blocked", br, False
    outcome = str(adapter_out.get("outcome") or "")
    conf = _as_float(adapter_out.get("confidence"), 0.0)
    if outcome in {"pos", "neg"} and conf >= 0.5:
        return "ready_for_outcome_queue", "replay_probe_produced_pos_neg_outcome", True
    if outcome == "draw":
        return "draw_outcome_review_needed", "replay_probe_draw_or_non_terminal; draw policy-write disabled by default", False
    return "ambiguous_replay_outcome", "no_policy_usable_outcome", False


def _probe_candidate(
    con: sqlite3.Connection, row: sqlite3.Row, base: Path, horizon_steps: int,
    snake_scan_limit: int, capability_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ns = _safe_str(row["namespace"], 200)
    st = _safe_str(row["state_hash"], 2000)
    action = _safe_str(row["primary_action"], 200)
    meta = _json_loads(row["meta_json"] if "meta_json" in row.keys() else None)
    policy = _policy_snapshot(con, ns, st, action)
    adapter_out = _pick_replay_adapter(con, base, ns, st, action, horizon_steps, snake_scan_limit, capability_context)
    recommendation, recommendation_reason, ready_for_outcome_queue = _recommend(adapter_out)

    evidence_payload = {
        "source": "targeted_replay_probe",
        "adapter": adapter_out.get("adapter"),
        "namespace": ns,
        "state_hash": st,
        "action": action,
        "outcome": adapter_out.get("outcome"),
        "confidence": adapter_out.get("confidence"),
        "outcome_reason": adapter_out.get("outcome_reason"),
        "state_reconstruction": adapter_out.get("state_reconstruction"),
        "horizon_steps": adapter_out.get("horizon_steps"),
        "steps_simulated": adapter_out.get("steps_simulated"),
        "total_reward": adapter_out.get("total_reward"),
        "terminal": adapter_out.get("terminal"),
        "terminal_reason": adapter_out.get("terminal_reason"),
        "passed_any": adapter_out.get("passed_any"),
        "lineage": adapter_out.get("lineage") if isinstance(adapter_out.get("lineage"), dict) else {},
        "historical_state_exact": adapter_out.get("historical_state_exact"),
        "simulation_used": adapter_out.get("simulation_used"),
    }

    return {
        "id": _as_int(row["id"]),
        "promotion_signature": _safe_str(row["promotion_signature"], 200),
        "request_signature": _safe_str(row["request_signature"], 200),
        "namespace": ns,
        "state_hash": st,
        "state_hash_format": state_hash_format(st),
        "state_schema_guess": _state_schema_guess(st),
        "action": action,
        "action_format": action_format(action),
        "target": _safe_str(row["target"], 120),
        "promotion_bucket": _safe_str(row["promotion_bucket"], 120),
        "status": _safe_str(row["status"], 120),
        "score": _as_float(row["score"], 0.0),
        "policy_snapshot": policy,
        "replay_probe_status": "ready" if bool(adapter_out.get("replay_possible")) else "blocked",
        "replay_possible": bool(adapter_out.get("replay_possible")),
        "replay_source": adapter_out.get("adapter"),
        "simulated_or_replayed_outcome": adapter_out.get("outcome"),
        "outcome_confidence": adapter_out.get("confidence"),
        "evidence_payload": evidence_payload if adapter_out.get("outcome") else {},
        "adapter_result": adapter_out,
        "adapter_payload": adapter_out,
        "blocked_reason": adapter_out.get("blocked_reason"),
        "recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
        "ready_for_outcome_queue": bool(ready_for_outcome_queue),
        "policy_write_allowed": False,
        "execution": {
            "probe_only": True,
            "write_db": False,
            "write_policy": False,
            "start_runner": False,
            "start_global_replay": False,
            "start_dream": False,
            "local_headless_adapter_only": True,
        },
        "meta_excerpt": meta if len(json.dumps(meta, ensure_ascii=False, default=str)) <= 1500 else {"truncated": True},
    }


def run_once(
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    horizon_steps: Optional[int] = None,
    buckets: Optional[Sequence[str]] = None,
    namespaces: Optional[Sequence[str]] = None,
    state_schemas: Optional[Sequence[str]] = None,
    targets: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    started = time.time()
    now_ts = _now_ts()
    base = _base_dir()
    db = (db_path or default_db_path(base)).resolve()
    state = (state_path or default_state_path(base)).resolve()
    lim = max(1, int(limit if limit is not None else _env_int("OROMA_GAP_REPLAY_EVIDENCE_PROBE_LIMIT", 3)))
    tk = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_REPLAY_EVIDENCE_PROBE_TOPK", 3)))
    horizon = max(1, int(horizon_steps if horizon_steps is not None else _env_int("OROMA_GAP_REPLAY_EVIDENCE_PROBE_HORIZON_STEPS", 80)))
    snake_scan_limit = max(1, _env_int("OROMA_GAP_REPLAY_EVIDENCE_PROBE_SNAKE_SNAPCHAIN_SCAN_LIMIT", DEFAULT_SNAKE_SNAPCHAIN_SCAN_LIMIT))
    bucket_list = list(buckets) if buckets else parse_csv(_env_str("OROMA_GAP_REPLAY_EVIDENCE_PROBE_BUCKETS", ",".join(DEFAULT_BUCKETS)), DEFAULT_BUCKETS)
    namespace_list = list(namespaces) if namespaces is not None else parse_csv(_env_str("OROMA_GAP_REPLAY_EVIDENCE_PROBE_NAMESPACE_ALLOWLIST", ""), [])
    state_schema_list = list(state_schemas) if state_schemas is not None else parse_csv(_env_str("OROMA_GAP_REPLAY_EVIDENCE_PROBE_STATE_SCHEMA_ALLOWLIST", ""), [])
    target_list = list(targets) if targets is not None else parse_csv(_env_str("OROMA_GAP_REPLAY_EVIDENCE_PROBE_TARGET_ALLOWLIST", ""), [])

    out: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "targeted_replay_evidence_probe_state_only",
        "generated_at_ts": now_ts,
        "generated_at_iso": _iso(now_ts),
        "db_path": str(db),
        "state_path": str(state),
        "config": {
            "limit": lim,
            "topk": tk,
            "horizon_steps": horizon,
            "snake_snapchain_scan_limit": snake_scan_limit,
            "buckets": bucket_list,
            "namespace_allowlist": namespace_list,
            "state_schema_allowlist": state_schema_list,
            "target_allowlist": target_list,
        },
        "safety": {
            "db_open_mode": "read_only_uri_mode_ro",
            "db_writes": False,
            "policy_writes": False,
            "rules_writes": False,
            "schema_changes": False,
            "runner_starts": False,
            "global_replay_starts": False,
            "dream_starts": False,
            "mass_scan": False,
            "policy_snapshot_is_not_outcome": True,
            "state_json_write": "best_effort",
        },
        "source_tables": {},
        "source_table_count_modes": {},
        "candidates": [],
        "summary": {},
        "errors": [],
    }

    try:
        con = _connect_ro(db)
        try:
            source_stats_started = time.time()
            for table in (PROMOTION_TABLE, POLICY_TABLE, "gap_policy_mini_write_ledger", "dream_policy_mini_write_ledger"):
                value, mode = _table_high_water_mark(con, table)
                out["source_tables"][table] = value
                out["source_table_count_modes"][table] = mode
            source_stats_dt = round(time.time() - source_stats_started, 6)
            rows = _load_candidates(con, bucket_list, lim, namespace_list, state_schema_list, target_list)
            capability_context = build_replay_evidence_capability_context(
                con, schemas=["snake:pro_v2"], scan_limit=snake_scan_limit
            )
            candidates = [
                _probe_candidate(con, row, base, horizon, snake_scan_limit, capability_context)
                for row in rows
            ]
            # Keep output bounded in case a future adapter emits more details.
            out["candidates"] = candidates[:tk]
            recommendation_counts: Dict[str, int] = {}
            status_counts: Dict[str, int] = {}
            outcome_counts: Dict[str, int] = {}
            ready_total = 0
            replay_possible_total = 0
            for item in candidates:
                recommendation_counts[item.get("recommendation", "unknown")] = recommendation_counts.get(item.get("recommendation", "unknown"), 0) + 1
                status_counts[item.get("replay_probe_status", "unknown")] = status_counts.get(item.get("replay_probe_status", "unknown"), 0) + 1
                outcome = str(item.get("simulated_or_replayed_outcome") or "null")
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
                if bool(item.get("ready_for_outcome_queue")):
                    ready_total += 1
                if bool(item.get("replay_possible")):
                    replay_possible_total += 1
            out["summary"] = {
                "ok": True,
                "promotion_candidates_loaded": len(rows),
                "scope_active": bool(namespace_list or state_schema_list or target_list),
                "candidates_probed": len(candidates),
                "replay_possible_total": replay_possible_total,
                "ready_for_outcome_queue_total": ready_total,
                "recommendation_counts": recommendation_counts,
                "replay_probe_status_counts": status_counts,
                "outcome_counts": outcome_counts,
                "policy_writes": 0,
                "db_writes": 0,
                "runner_starts": 0,
                "global_replay_starts": 0,
                "dream_starts": 0,
                "next_step": "if_ready_for_outcome_queue_gt_0_then_gap_evidence_outcome_queue_gate_else_targeted_dream_probe",
                "source_table_stats_mode": "fast_high_water_mark_no_full_count",
                "source_table_stats_dt_sec": source_stats_dt,
                "replay_capability_shared_scan_used": True,
                "replay_capability_shared_scan_snapchains": int(capability_context.get("snapchains_scanned", 0) or 0),
                "replay_capability_shared_scan_steps": int(capability_context.get("steps_scanned_total", 0) or 0),
                "replay_capability_shared_scan_decode_errors": int(capability_context.get("decode_errors", 0) or 0),
                "replay_capability_shared_scan_build_dt_ms": float(capability_context.get("build_dt_ms", 0.0) or 0.0),
            }
            out["ok"] = True
        finally:
            con.close()
    except Exception as exc:
        out["errors"].append(f"{type(exc).__name__}: {exc}")
        out["summary"] = {"ok": False, "error": out["errors"][-1]}

    out["summary"]["dt_sec"] = round(time.time() - started, 3)
    wrote, write_error = atomic_write_json_best_effort(state, out)
    out["summary"]["state_written"] = bool(wrote)
    if write_error:
        out["summary"]["state_write_error"] = write_error
        out["errors"].append("state_write_failed: " + write_error)
    if wrote:
        wrote2, write_error2 = atomic_write_json_best_effort(state, out)
        if not wrote2 and write_error2:
            out["errors"].append("state_rewrite_failed: " + write_error2)
    return out

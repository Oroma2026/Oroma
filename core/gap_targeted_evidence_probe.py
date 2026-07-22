#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_targeted_evidence_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Targeted Evidence Probe · Read-Only · No Starts · No Policy
# Version:   v0.1.0-targeted-evidence-probe
# Stand:     2026-07-10
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Dieses Modul ist die kleine, gezielte Diagnose-Stufe nach dem schweren
# Gap Evidence Outcome Collector. Der Vollcollector hat gezeigt, dass fuer die
# aktuellen Gap-Promotion-Kandidaten keine direkt auditierbaren Outcomes unter
# exakt gleichem namespace + state_hash + action auffindbar sind. Dieses Modul
# prueft deshalb bewusst nur 1-3 Kandidaten tief genug, um die naechste fachliche
# Entscheidung zu treffen:
#
#   1) Historische Evidenz ist adapterfaehig, aber anders codiert.
#   2) Historische Evidenz ist nicht rekonstruierbar.
#   3) Neue gezielte Replay-/Dream-Evidence muss erzeugt werden.
#   4) Kandidat ist nicht verwertbar.
#
# Der Probe schliesst keine Luecken und lernt nicht aus Q-Wahrscheinlichkeiten.
# policy_rules wird nur als Kontext/Snapshot gelesen und zaehlt nicht als neues
# Outcome. Der Probe erzeugt ausschliesslich eine Diagnose-Datei:
#
#   data/state/gap_targeted_evidence_probe.json
#
# PRODUKTIONSINVARIANTEN
# ----------------------
# - Headless: keine Qt-, Wayland-, X11- oder GUI-Abhaengigkeiten.
# - SQLite nur read-only via URI mode=ro.
# - Keine DB-Writes, keine Schemaaenderungen, keine policy_rules-/rules-Writes.
# - Keine Runner-, Replay- oder Dream-Starts.
# - Kein Massenscan: standardmaessig nur 1-3 Kandidaten und begrenzte neue
#   Tabellenfenster (ORDER BY id DESC LIMIT N).
# - State-Write ist best-effort: Wenn data/state nicht beschreibbar ist, bleibt
#   stdout vollstaendig nutzbar und der Fehler wird im JSON sichtbar.
# - Root-Manual-Laeufe setzen die State-Datei best-effort auf oroma:oroma 664.
#
# ENV
# ---
#   OROMA_BASE=/opt/ai/oroma
#   OROMA_DB_PATH=/opt/ai/oroma/data/oroma.db
#   OROMA_GAP_TARGETED_EVIDENCE_PROBE_STATE_PATH=/opt/ai/oroma/data/state/gap_targeted_evidence_probe.json
#   OROMA_GAP_TARGETED_EVIDENCE_PROBE_LIMIT=3
#   OROMA_GAP_TARGETED_EVIDENCE_PROBE_TOPK=3
#   OROMA_GAP_TARGETED_EVIDENCE_PROBE_SCAN_LIMIT=5000
#   OROMA_GAP_TARGETED_EVIDENCE_PROBE_BUCKETS=promotion_candidate_replay,promotion_candidate_dream
# =============================================================================

from __future__ import annotations

import json
import os
import pwd
import grp
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "v0.1.0-targeted-evidence-probe"
PROMOTION_TABLE = "gap_policy_promotion_queue"
POLICY_TABLE = "policy_rules"
DEFAULT_STATE_NAME = "gap_targeted_evidence_probe.json"
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


def _flatten_dict(d: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in dict(d).items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, Mapping):
            out.update(_flatten_dict(v, key))
        else:
            out[key] = v
    return out


def _first_key(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    flat = _flatten_dict(data)
    lowered = {str(k).lower(): v for k, v in flat.items()}
    for key in keys:
        lk = str(key).lower()
        if lk in lowered:
            return lowered[lk]
    # Also try suffix match, e.g. context.state_hash.
    for key in keys:
        lk = str(key).lower()
        for actual, value in lowered.items():
            if actual.endswith("." + lk):
                return value
    return None


def default_db_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (b / "data" / "oroma.db").resolve()


def default_state_path(base: Optional[Path] = None) -> Path:
    b = (base or _base_dir()).resolve()
    explicit = os.environ.get("OROMA_GAP_TARGETED_EVIDENCE_PROBE_STATE_PATH")
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
    if not text:
        return "unknown"
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


def _namespace_variants(namespace: str) -> List[str]:
    ns = str(namespace or "").strip()
    variants: List[str] = []
    if ns:
        variants.append(ns)
        if ns.startswith("game:"):
            variants.append(ns.split(":", 1)[1])
        else:
            variants.append("game:" + ns)
    # preserve order, unique
    seen = set()
    out = []
    for item in variants:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _action_variants(action: str) -> List[str]:
    a = str(action or "").strip()
    variants = []
    if a:
        variants.append(a)
        if re.fullmatch(r"[-+]?\d+", a):
            variants.append(str(int(a)))
            # common binary game aliases for probe only; never used for policy write.
            if str(int(a)) == "0":
                variants.extend(["noop", "no_op", "stay", "none"])
            if str(int(a)) == "1":
                variants.extend(["flap", "jump", "up", "act"])
    seen = set()
    out = []
    for item in variants:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _load_candidates(con: sqlite3.Connection, buckets: Sequence[str], limit: int) -> List[Dict[str, Any]]:
    if not _table_exists(con, PROMOTION_TABLE):
        return []
    bucket_list = [str(b) for b in buckets if str(b or "").strip()]
    if not bucket_list:
        bucket_list = list(DEFAULT_BUCKETS)
    placeholders = ",".join("?" for _ in bucket_list)
    sql = (
        f"SELECT * FROM {PROMOTION_TABLE} "
        f"WHERE status='promotion_review' AND promotion_bucket IN ({placeholders}) "
        f"ORDER BY score DESC, updated_ts DESC, id ASC LIMIT ?"
    )
    rows = con.execute(sql, tuple(bucket_list) + (max(1, int(limit)),)).fetchall()
    return [dict(r) for r in rows]


def _policy_context(con: sqlite3.Connection, namespace: str, state_hash: str, action: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "available": False,
        "exact_action": None,
        "state_action_rule_count": 0,
        "state_rule_count": 0,
        "namespace_state_variants": [],
        "oldest_policy_rule_ts": None,
        "newest_policy_rule_ts": None,
    }
    if not _table_exists(con, POLICY_TABLE) or not state_hash:
        out["reason"] = "policy_table_missing_or_state_missing"
        return out
    ns_variants = _namespace_variants(namespace)
    action_variants = _action_variants(action)
    try:
        for ns in ns_variants:
            state_count = _as_int(
                con.execute(
                    f"SELECT COUNT(*) FROM {POLICY_TABLE} WHERE namespace=? AND state_hash=?",
                    (ns, state_hash),
                ).fetchone()[0],
                0,
            )
            if state_count:
                out["namespace_state_variants"].append({"namespace": ns, "state_rule_count": state_count})
        if namespace and action:
            row = con.execute(
                f"SELECT id,n,pos,neg,draw,q,last_ts FROM {POLICY_TABLE} WHERE namespace=? AND state_hash=? AND action=? LIMIT 1",
                (namespace, state_hash, action),
            ).fetchone()
            if row:
                out["available"] = True
                out["exact_action"] = {
                    "id": _as_int(row["id"], 0),
                    "n": _as_int(row["n"], 0),
                    "pos": _as_int(row["pos"], 0),
                    "neg": _as_int(row["neg"], 0),
                    "draw": _as_int(row["draw"], 0),
                    "q": _as_float(row["q"], 0.0),
                    "last_ts": _as_int(row["last_ts"], 0),
                }
            stats = con.execute(
                f"SELECT COUNT(*) AS c, MIN(last_ts) AS mn, MAX(last_ts) AS mx FROM {POLICY_TABLE} WHERE namespace=? AND state_hash=?",
                (namespace, state_hash),
            ).fetchone()
            if stats:
                out["state_rule_count"] = _as_int(stats["c"], 0)
                out["oldest_policy_rule_ts"] = _as_int(stats["mn"], 0) or None
                out["newest_policy_rule_ts"] = _as_int(stats["mx"], 0) or None
            exact_count = 0
            for av in action_variants:
                exact_count += _as_int(
                    con.execute(
                        f"SELECT COUNT(*) FROM {POLICY_TABLE} WHERE namespace=? AND state_hash=? AND action=?",
                        (namespace, state_hash, av),
                    ).fetchone()[0],
                    0,
                )
            out["state_action_rule_count"] = exact_count
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _extract_identity_from_json(data: Mapping[str, Any]) -> Dict[str, Any]:
    ns = _first_key(data, ["namespace", "ns", "policy_namespace", "game_namespace", "game"])
    st = _first_key(data, ["state_hash", "state", "policy_state_hash", "state_key", "hash"])
    act = _first_key(data, ["action", "policy_action", "selected_action", "move", "cmd", "act"])
    reward = _first_key(data, ["reward", "r", "outcome_reward", "value"])
    outcome = _first_key(data, ["outcome", "result", "label"])
    return {
        "namespace": _safe_str(ns, 300) if ns is not None else None,
        "state_hash": _safe_str(st, 1000) if st is not None else None,
        "action": _safe_str(act, 300) if act is not None else None,
        "reward": reward,
        "outcome": _safe_str(outcome, 100) if outcome is not None else None,
    }


def _matches_identity(candidate: Mapping[str, Any], found: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    c_ns = str(candidate.get("namespace") or "").strip()
    c_state = str(candidate.get("state_hash") or "").strip()
    c_action = str(candidate.get("primary_action") or "").strip()
    f_ns = str(found.get("namespace") or "").strip()
    f_state = str(found.get("state_hash") or "").strip()
    f_action = str(found.get("action") or "").strip()
    ns_ok = (not f_ns) or f_ns in _namespace_variants(c_ns)
    state_ok = f_state == c_state
    action_ok = (not f_action) or f_action in _action_variants(c_action)
    if not ns_ok:
        reasons.append("namespace_mismatch")
    if not state_ok:
        reasons.append("state_hash_mismatch")
    if not action_ok:
        reasons.append("action_mismatch")
    return bool(ns_ok and state_ok and action_ok), reasons


def _bounded_reward_probe(con: sqlite3.Connection, candidate: Mapping[str, Any], scan_limit: int, topk: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"table": "rewards_log", "scanned": 0, "exact_matches": [], "near_matches": [], "errors": []}
    if not _table_exists(con, "rewards_log"):
        out["missing"] = True
        return out
    try:
        rows = con.execute(
            "SELECT id,created_at,source,episode_id,step,reward,raw,tag FROM rewards_log ORDER BY id DESC LIMIT ?",
            (max(1, int(scan_limit)),),
        ).fetchall()
        c_state = str(candidate.get("state_hash") or "")
        c_action = str(candidate.get("primary_action") or "")
        c_ns = str(candidate.get("namespace") or "")
        for row in rows:
            out["scanned"] += 1
            raw_text = str(row["raw"] or "")
            data = _json_loads(raw_text)
            ident = _extract_identity_from_json(data)
            exact, mismatch = _matches_identity(candidate, ident)
            has_state_text = bool(c_state and c_state in raw_text)
            has_action_text = bool(c_action and (f'"{c_action}"' in raw_text or f":{c_action}" in raw_text or c_action in raw_text[:2000]))
            has_ns_text = bool(c_ns and c_ns in raw_text)
            item = {
                "id": _as_int(row["id"], 0),
                "ts": _as_int(row["created_at"], 0),
                "source": _safe_str(row["source"], 120),
                "episode_id": _as_int(row["episode_id"], 0) or None,
                "step": _as_int(row["step"], 0),
                "reward": _as_float(row["reward"], 0.0),
                "tag": _safe_str(row["tag"], 120),
                "identity": ident,
                "mismatch": mismatch,
                "text_hits": {"namespace": has_ns_text, "state_hash": has_state_text, "action": has_action_text},
            }
            if exact and len(out["exact_matches"]) < topk:
                out["exact_matches"].append(item)
            elif (has_state_text or (ident.get("state_hash") == c_state) or (has_ns_text and has_action_text)) and len(out["near_matches"]) < topk:
                out["near_matches"].append(item)
    except Exception as exc:
        out["errors"].append(f"{type(exc).__name__}: {exc}")
    return out


def _bounded_episode_probe(con: sqlite3.Connection, candidate: Mapping[str, Any], scan_limit: int, topk: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"table": "episode_events", "scanned": 0, "exact_matches": [], "near_matches": [], "errors": []}
    if not _table_exists(con, "episode_events"):
        out["missing"] = True
        return out
    try:
        rows = con.execute(
            "SELECT id,episode_id,ts,event_type,ref_table,ref_id,meta_json,state_hash,reward,idx FROM episode_events ORDER BY id DESC LIMIT ?",
            (max(1, int(scan_limit)),),
        ).fetchall()
        c_state = str(candidate.get("state_hash") or "")
        c_action = str(candidate.get("primary_action") or "")
        c_ns = str(candidate.get("namespace") or "")
        for row in rows:
            out["scanned"] += 1
            meta_text = str(row["meta_json"] or "")
            meta = _json_loads(meta_text)
            ident = _extract_identity_from_json(meta)
            if row["state_hash"]:
                ident["state_hash"] = _safe_str(row["state_hash"], 1000)
            if row["reward"] is not None and ident.get("reward") is None:
                ident["reward"] = row["reward"]
            exact, mismatch = _matches_identity(candidate, ident)
            has_state_text = bool(c_state and (c_state == str(row["state_hash"] or "") or c_state in meta_text))
            has_action_text = bool(c_action and (f'"{c_action}"' in meta_text or f":{c_action}" in meta_text or c_action in meta_text[:2000]))
            has_ns_text = bool(c_ns and c_ns in meta_text)
            item = {
                "id": _as_int(row["id"], 0),
                "ts": _as_int(row["ts"], 0),
                "episode_id": _as_int(row["episode_id"], 0),
                "event_type": _safe_str(row["event_type"], 120),
                "ref_table": _safe_str(row["ref_table"], 120),
                "ref_id": _as_int(row["ref_id"], 0) or None,
                "idx": _as_int(row["idx"], 0) if row["idx"] is not None else None,
                "reward": _as_float(row["reward"], 0.0) if row["reward"] is not None else None,
                "identity": ident,
                "mismatch": mismatch,
                "text_hits": {"namespace": has_ns_text, "state_hash": has_state_text, "action": has_action_text},
            }
            if exact and len(out["exact_matches"]) < topk:
                out["exact_matches"].append(item)
            elif (has_state_text or (ident.get("state_hash") == c_state) or (has_ns_text and has_action_text)) and len(out["near_matches"]) < topk:
                out["near_matches"].append(item)
    except Exception as exc:
        out["errors"].append(f"{type(exc).__name__}: {exc}")
    return out


def _ledger_probe(con: sqlite3.Connection, candidate: Mapping[str, Any], topk: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"tables": {}, "exact_matches_total": 0, "near_matches_total": 0}
    ns = str(candidate.get("namespace") or "")
    st = str(candidate.get("state_hash") or "")
    act = str(candidate.get("primary_action") or "")
    for table in ("dream_policy_mini_write_ledger", "gap_policy_mini_write_ledger"):
        info: Dict[str, Any] = {"exists": _table_exists(con, table), "exact_matches": [], "near_matches": [], "errors": []}
        if info["exists"]:
            try:
                # Exact query is acceptable for Ledgers; they are expected to be small/indexable.
                if table == "dream_policy_mini_write_ledger":
                    rows = con.execute(
                        f"SELECT * FROM {table} WHERE namespace=? AND state_hash=? AND action=? ORDER BY id DESC LIMIT ?",
                        (ns, st, act, max(1, int(topk))),
                    ).fetchall()
                    for row in rows:
                        info["exact_matches"].append({
                            "id": _as_int(row["id"], 0),
                            "created_ts": _as_int(row["created_ts"], 0),
                            "source": _safe_str(row["source"], 180),
                            "status": _safe_str(row["status"], 80),
                            "direct_positive": _as_int(row["direct_positive"], 0),
                            "direct_negative": _as_int(row["direct_negative"], 0),
                            "direct_zero": _as_int(row["direct_zero"], 0),
                        })
                else:
                    rows = con.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (max(1, int(topk * 10)),)).fetchall()
                    for row in rows:
                        raw = dict(row)
                        meta = _json_loads(raw.get("meta_json"))
                        text = json.dumps(raw, ensure_ascii=False, default=str)[:4000]
                        if st and st in text and len(info["near_matches"]) < topk:
                            info["near_matches"].append({"id": raw.get("id"), "created_ts": raw.get("created_ts"), "hint": "state_hash_text_match"})
                info["exact_count"] = len(info["exact_matches"])
                info["near_count"] = len(info["near_matches"])
                out["exact_matches_total"] += len(info["exact_matches"])
                out["near_matches_total"] += len(info["near_matches"])
            except Exception as exc:
                info["errors"].append(f"{type(exc).__name__}: {exc}")
        out["tables"][table] = info
    return out


def _recommendation(candidate: Mapping[str, Any], direct_total: int, near_total: int, policy_ctx: Mapping[str, Any]) -> Tuple[str, str]:
    if direct_total > 0:
        return "ready_for_outcome_queue", "direct_evidence_found"
    if near_total > 0:
        return "historical_adapter_possible", "near_evidence_found_but_identity_mismatch"
    bucket = str(candidate.get("promotion_bucket") or "")
    target = str(candidate.get("target") or "")
    if bucket == "promotion_candidate_replay" or target == "replay":
        return "targeted_replay_needed", "no_direct_or_adapter_evidence_for_replay_candidate"
    if bucket == "promotion_candidate_dream" or target == "dream":
        return "targeted_dream_needed", "no_direct_or_adapter_evidence_for_dream_candidate"
    if not candidate.get("state_hash") or not candidate.get("primary_action"):
        return "not_actionable", "candidate_identity_incomplete"
    return "not_actionable", "no_supported_target"


def _age_days(now_ts: int, ts: Optional[int]) -> Optional[float]:
    if not ts:
        return None
    try:
        return round(max(0.0, (int(now_ts) - int(ts)) / 86400.0), 3)
    except Exception:
        return None


def _probe_candidate(con: sqlite3.Connection, row: Mapping[str, Any], scan_limit: int, topk: int, now_ts: int) -> Dict[str, Any]:
    candidate = dict(row)
    meta = _json_loads(candidate.get("meta_json"))
    ns = _safe_str(candidate.get("namespace"), 300)
    st = _safe_str(candidate.get("state_hash"), 1000)
    act = _safe_str(candidate.get("primary_action"), 300)

    policy_ctx = _policy_context(con, ns, st, act)
    rewards = _bounded_reward_probe(con, candidate, scan_limit, topk)
    episodes = _bounded_episode_probe(con, candidate, scan_limit, topk)
    ledgers = _ledger_probe(con, candidate, topk)

    direct_total = len(rewards.get("exact_matches", [])) + len(episodes.get("exact_matches", [])) + int(ledgers.get("exact_matches_total", 0))
    near_total = len(rewards.get("near_matches", [])) + len(episodes.get("near_matches", [])) + int(ledgers.get("near_matches_total", 0))
    rec, rec_reason = _recommendation(candidate, direct_total, near_total, policy_ctx)
    oldest_policy_ts = policy_ctx.get("oldest_policy_rule_ts")
    newest_policy_ts = policy_ctx.get("newest_policy_rule_ts")

    # If evidence exists, compare newest direct evidence age with oldest policy rule.
    evidence_ts_values: List[int] = []
    for bucket in (rewards.get("exact_matches", []), episodes.get("exact_matches", [])):
        for item in bucket:
            ts = _as_int(item.get("ts"), 0)
            if ts:
                evidence_ts_values.append(ts)
    for table_info in ledgers.get("tables", {}).values():
        for item in table_info.get("exact_matches", []):
            ts = _as_int(item.get("created_ts"), 0)
            if ts:
                evidence_ts_values.append(ts)
    newest_evidence_ts = max(evidence_ts_values) if evidence_ts_values else None
    if newest_evidence_ts and oldest_policy_ts:
        evidence_age_gap_days = round(abs(int(newest_evidence_ts) - int(oldest_policy_ts)) / 86400.0, 3)
    else:
        evidence_age_gap_days = _age_days(now_ts, _as_int(oldest_policy_ts, 0) if oldest_policy_ts else None)

    return {
        "id": _as_int(candidate.get("id"), 0),
        "promotion_signature": _safe_str(candidate.get("promotion_signature"), 200),
        "request_signature": _safe_str(candidate.get("request_signature"), 200),
        "namespace": ns,
        "namespace_variants_checked": _namespace_variants(ns),
        "state_hash": st,
        "state_schema_guess": _state_schema_guess(st),
        "state_hash_format": state_hash_format(st),
        "action": act,
        "action_format": action_format(act),
        "action_variants_checked": _action_variants(act),
        "target": _safe_str(candidate.get("target"), 120),
        "promotion_bucket": _safe_str(candidate.get("promotion_bucket"), 120),
        "status": _safe_str(candidate.get("status"), 120),
        "score": _as_float(candidate.get("score"), 0.0),
        "created_ts": _as_int(candidate.get("created_ts"), 0),
        "updated_ts": _as_int(candidate.get("updated_ts"), 0),
        "oldest_policy_rule_ts": oldest_policy_ts,
        "newest_policy_rule_ts": newest_policy_ts,
        "oldest_policy_rule_age_days": _age_days(now_ts, _as_int(oldest_policy_ts, 0) if oldest_policy_ts else None),
        "evidence_age_gap_days": evidence_age_gap_days,
        "policy_context": policy_ctx,
        "probe_sources": {
            "rewards_log_bounded": rewards,
            "episode_events_bounded": episodes,
            "ledgers": ledgers,
        },
        "evidence_found": bool(direct_total > 0),
        "direct_evidence_count": direct_total,
        "near_evidence_count": near_total,
        "probe_status": "evidence_found" if direct_total else ("adapter_hint" if near_total else "no_direct_evidence"),
        "recommendation": rec,
        "recommendation_reason": rec_reason,
        "policy_write_allowed": False,
        "execution": {
            "probe_only": True,
            "write_db": False,
            "write_policy": False,
            "start_runner": False,
            "start_replay": False,
            "start_dream": False,
        },
        "meta_excerpt": meta if len(json.dumps(meta, ensure_ascii=False, default=str)) <= 1500 else {"truncated": True},
    }


def run_once(
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    limit: Optional[int] = None,
    topk: Optional[int] = None,
    scan_limit: Optional[int] = None,
    buckets: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    started = time.time()
    now_ts = _now_ts()
    base = _base_dir()
    db = (db_path or default_db_path(base)).resolve()
    state = (state_path or default_state_path(base)).resolve()
    lim = max(1, int(limit if limit is not None else _env_int("OROMA_GAP_TARGETED_EVIDENCE_PROBE_LIMIT", 3)))
    tk = max(1, int(topk if topk is not None else _env_int("OROMA_GAP_TARGETED_EVIDENCE_PROBE_TOPK", 3)))
    scan = max(1, int(scan_limit if scan_limit is not None else _env_int("OROMA_GAP_TARGETED_EVIDENCE_PROBE_SCAN_LIMIT", 5000)))
    bucket_list = list(buckets) if buckets else parse_csv(_env_str("OROMA_GAP_TARGETED_EVIDENCE_PROBE_BUCKETS", ",".join(DEFAULT_BUCKETS)), DEFAULT_BUCKETS)

    out: Dict[str, Any] = {
        "ok": False,
        "version": VERSION,
        "mode": "read_only_targeted_evidence_probe",
        "generated_at_ts": now_ts,
        "generated_at_iso": _iso(now_ts),
        "db_path": str(db),
        "state_path": str(state),
        "config": {
            "limit": lim,
            "topk": tk,
            "scan_limit_per_table": scan,
            "buckets": bucket_list,
        },
        "safety": {
            "db_open_mode": "read_only_uri_mode_ro",
            "db_writes": False,
            "policy_writes": False,
            "rules_writes": False,
            "schema_changes": False,
            "runner_starts": False,
            "replay_starts": False,
            "dream_starts": False,
            "mass_scan": False,
            "state_json_write": "best_effort",
        },
        "source_tables": {},
        "candidates": [],
        "summary": {},
        "errors": [],
    }

    try:
        con = _connect_ro(db)
        try:
            for table in (PROMOTION_TABLE, POLICY_TABLE, "rewards_log", "episode_events", "dream_policy_mini_write_ledger", "gap_policy_mini_write_ledger"):
                out["source_tables"][table] = _table_count(con, table)
            rows = _load_candidates(con, bucket_list, lim)
            candidates = [_probe_candidate(con, row, scan, tk, now_ts) for row in rows]
            out["candidates"] = candidates
            recommendation_counts: Dict[str, int] = {}
            status_counts: Dict[str, int] = {}
            format_counts: Dict[str, int] = {}
            direct_total = 0
            near_total = 0
            for item in candidates:
                recommendation_counts[item.get("recommendation", "unknown")] = recommendation_counts.get(item.get("recommendation", "unknown"), 0) + 1
                status_counts[item.get("probe_status", "unknown")] = status_counts.get(item.get("probe_status", "unknown"), 0) + 1
                format_counts[item.get("state_hash_format", "unknown")] = format_counts.get(item.get("state_hash_format", "unknown"), 0) + 1
                direct_total += _as_int(item.get("direct_evidence_count"), 0)
                near_total += _as_int(item.get("near_evidence_count"), 0)
            out["summary"] = {
                "ok": True,
                "promotion_candidates_loaded": len(rows),
                "candidates_probed": len(candidates),
                "direct_evidence_total": direct_total,
                "near_evidence_total": near_total,
                "recommendation_counts": recommendation_counts,
                "probe_status_counts": status_counts,
                "state_hash_format_counts": format_counts,
                "policy_writes": 0,
                "db_writes": 0,
                "runner_starts": 0,
                "replay_starts": 0,
                "dream_starts": 0,
                "next_step": "historical_adapter_if_near_evidence_else_targeted_replay_or_dream_probe",
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
        # Re-write with state_written status included. If this second write fails,
        # stdout still contains the complete diagnostic payload.
        wrote2, write_error2 = atomic_write_json_best_effort(state, out)
        if not wrote2 and write_error2:
            out["errors"].append("state_rewrite_failed: " + write_error2)
    return out

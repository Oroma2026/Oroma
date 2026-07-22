#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/targeted_acquisition_lifecycle.py
# Projekt: ORÓMA
# Modul:   Persistenter Targeted-Acquisition-Lifecycle + Append-only Ledger
# Version: v0.1.0-dbwriter-lifecycle
# Stand:   2026-07-15
# =============================================================================
"""Canonical identities and DBWriter-only atomic lifecycle transitions."""
from __future__ import annotations
import hashlib,json,time
from typing import Any,Dict,List,Mapping,Optional,Sequence,Tuple
from core.acquisition_state_machine import REGISTRY
try:
    from core import db_writer_client
except Exception:
    db_writer_client=None
VERSION="v0.1.0-dbwriter-lifecycle"; EVENT_SCHEMA="targeted_acquisition_event:v1"; WRITER_ID="writer:core.targeted_acquisition_lifecycle:v1"
def _canon(v:Any)->str: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def _sha(v:Any)->str: return "sha256:"+hashlib.sha256(_canon(v).encode()).hexdigest()
def build_source_identity(source_snapchain_id:int,source_step_index:int,source_before_state_digest:str)->str:
    return _sha({"source_snapchain_id":int(source_snapchain_id),"source_step_index":int(source_step_index),"source_before_state_digest":str(source_before_state_digest or "")})
def build_acquisition_id(*,promotion_id:int,promotion_signature:str,acquisition_protocol:str,protocol_version:str,schedule_digest:str,source_identity:str,reacquisition_generation:int=0)->str:
    return _sha({"promotion_id":int(promotion_id),"promotion_signature":str(promotion_signature),"acquisition_protocol":str(acquisition_protocol),"protocol_version":str(protocol_version),"schedule_digest":str(schedule_digest),"source_identity":str(source_identity),"reacquisition_generation":int(reacquisition_generation)})
def schema_statements()->List[Tuple[str,Sequence[Any]]]:
    return [("""CREATE TABLE IF NOT EXISTS gap_targeted_acquisition_lifecycle (acquisition_id TEXT PRIMARY KEY,promotion_id INTEGER NOT NULL,promotion_signature TEXT NOT NULL,request_signature TEXT NOT NULL,namespace TEXT NOT NULL,state_schema TEXT NOT NULL,state_hash TEXT NOT NULL,primary_action TEXT,source_snapchain_id INTEGER,source_step_index INTEGER,source_before_state_digest TEXT,source_identity TEXT NOT NULL,acquisition_protocol TEXT NOT NULL,protocol_version TEXT NOT NULL,schedule_id TEXT NOT NULL,schedule_digest TEXT NOT NULL,status TEXT NOT NULL,attempts_budget INTEGER NOT NULL,attempts_executed INTEGER NOT NULL DEFAULT 0,selected_attempt_index INTEGER,direct_outcome_acquired INTEGER NOT NULL DEFAULT 0,terminal_reason TEXT,source_high_water_mark TEXT NOT NULL,reacquisition_generation INTEGER NOT NULL DEFAULT 0,created_ts INTEGER NOT NULL,started_ts INTEGER,terminal_ts INTEGER,updated_ts INTEGER NOT NULL,meta_json TEXT)""",()),("CREATE INDEX IF NOT EXISTS idx_gta_lifecycle_promotion ON gap_targeted_acquisition_lifecycle(promotion_id,reacquisition_generation,updated_ts)",()),("CREATE INDEX IF NOT EXISTS idx_gta_lifecycle_status ON gap_targeted_acquisition_lifecycle(status,updated_ts)",()),("""CREATE TABLE IF NOT EXISTS gap_targeted_acquisition_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,event_signature TEXT NOT NULL UNIQUE,acquisition_id TEXT NOT NULL,from_status TEXT NOT NULL,to_status TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,writer_id TEXT NOT NULL,schema_version TEXT NOT NULL,payload_digest TEXT NOT NULL,payload_json TEXT,created_ts INTEGER NOT NULL)""",()),("CREATE INDEX IF NOT EXISTS idx_gta_events_acquisition ON gap_targeted_acquisition_events(acquisition_id,event_id)",())]
def dbwriter_ready(timeout_ms:int=500)->Tuple[bool,str]:
    if db_writer_client is None:return False,"dbwriter_client_import_failed"
    try:
        if not db_writer_client.enabled():return False,"dbwriter_disabled"
        if not db_writer_client.ping(timeout_ms=timeout_ms):return False,"dbwriter_ping_failed"
        return True,"dbwriter_ready"
    except Exception as exc:return False,f"dbwriter_error:{exc}"
def persist_transition(record:Mapping[str,Any],*,from_status:str,to_status:str,reason:str,actor:str,payload:Optional[Mapping[str,Any]]=None,timeout_ms:int=15000)->Dict[str,Any]:
    decision=REGISTRY.decision(from_status,to_status,{"record":dict(record),"payload":dict(payload or {})})
    if not decision["allowed"]: return {"ok":False,"write_attempted":False,"reason":decision["reason"],"decision":decision}
    ready,ready_reason=dbwriter_ready()
    if not ready:return {"ok":False,"write_attempted":False,"reason":ready_reason,"decision":decision}
    now=int(time.time()); terminal=to_status in {"evidence_acquired","exhausted_no_direct_outcome","blocked"}
    p=dict(payload or {}); payload_digest=_sha(p); event_sig=_sha({"acquisition_id":record["acquisition_id"],"from_status":from_status,"to_status":to_status,"reason":reason,"payload_digest":payload_digest})
    vals=(record["acquisition_id"],int(record["promotion_id"]),record["promotion_signature"],record["request_signature"],record["namespace"],record["state_schema"],record["state_hash"],str(record.get("primary_action") or ""),int(record.get("source_snapchain_id") or 0),int(record.get("source_step_index") or 0),str(record.get("source_before_state_digest") or ""),record["source_identity"],record["acquisition_protocol"],record["protocol_version"],record["schedule_id"],record["schedule_digest"],to_status,int(record["attempts_budget"]),int(record.get("attempts_executed") or 0),record.get("selected_attempt_index"),1 if record.get("direct_outcome_acquired") else 0,str(record.get("terminal_reason") or ""),str(record["source_high_water_mark"]),int(record.get("reacquisition_generation") or 0),int(record.get("created_ts") or now),now if to_status=="acquiring" else record.get("started_ts"),now if terminal else None,now,_canon(record.get("meta") or {}))
    stmts=schema_statements()+[("""INSERT INTO gap_targeted_acquisition_lifecycle (acquisition_id,promotion_id,promotion_signature,request_signature,namespace,state_schema,state_hash,primary_action,source_snapchain_id,source_step_index,source_before_state_digest,source_identity,acquisition_protocol,protocol_version,schedule_id,schedule_digest,status,attempts_budget,attempts_executed,selected_attempt_index,direct_outcome_acquired,terminal_reason,source_high_water_mark,reacquisition_generation,created_ts,started_ts,terminal_ts,updated_ts,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(acquisition_id) DO UPDATE SET status=excluded.status,attempts_executed=excluded.attempts_executed,selected_attempt_index=excluded.selected_attempt_index,direct_outcome_acquired=excluded.direct_outcome_acquired,terminal_reason=excluded.terminal_reason,started_ts=COALESCE(gap_targeted_acquisition_lifecycle.started_ts,excluded.started_ts),terminal_ts=excluded.terminal_ts,updated_ts=excluded.updated_ts,meta_json=excluded.meta_json""",vals),("""INSERT OR IGNORE INTO gap_targeted_acquisition_events (event_signature,acquisition_id,from_status,to_status,actor,reason,writer_id,schema_version,payload_digest,payload_json,created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",(event_sig,record["acquisition_id"],from_status,to_status,actor,reason,WRITER_ID,EVENT_SCHEMA,payload_digest,_canon(p),now))]
    db_writer_client.transaction(stmts,tag="targeted_acquisition_lifecycle.transition",priority="normal",timeout_ms=timeout_ms,db="oroma")
    return {"ok":True,"write_attempted":True,"reason":"transition_persisted","event_signature":event_sig,"decision":decision}
__all__=["VERSION","WRITER_ID","build_source_identity","build_acquisition_id","persist_transition","schema_statements","dbwriter_ready"]

def persist_completed_run(record: Mapping[str, Any], *, final_status: str, payload: Mapping[str, Any], timeout_ms: int = 15000) -> Dict[str, Any]:
    """Persist pending→acquiring→terminal as one atomic DBWriter transaction."""
    if final_status not in {"evidence_acquired", "exhausted_no_direct_outcome", "blocked"}:
        return {"ok": False, "write_attempted": False, "reason": "final_status_not_terminal"}
    d1=REGISTRY.decision("acquisition_pending","acquiring",record)
    d2=REGISTRY.decision("acquiring",final_status,record)
    if not d1["allowed"] or not d2["allowed"]:
        return {"ok":False,"write_attempted":False,"reason":"illegal_transition_sequence","decisions":[d1,d2]}
    ready,why=dbwriter_ready()
    if not ready:return {"ok":False,"write_attempted":False,"reason":why}
    now=int(time.time()); p=dict(payload); pd=_sha(p)
    event_specs=[("acquisition_pending","acquiring","bounded_schedule_started"),("acquiring",final_status,str(record.get("terminal_reason") or final_status))]
    stmts=schema_statements()
    vals=(record["acquisition_id"],int(record["promotion_id"]),record["promotion_signature"],record["request_signature"],record["namespace"],record["state_schema"],record["state_hash"],str(record.get("primary_action") or ""),int(record.get("source_snapchain_id") or 0),int(record.get("source_step_index") or 0),str(record.get("source_before_state_digest") or ""),record["source_identity"],record["acquisition_protocol"],record["protocol_version"],record["schedule_id"],record["schedule_digest"],final_status,int(record["attempts_budget"]),int(record.get("attempts_executed") or 0),record.get("selected_attempt_index"),1 if record.get("direct_outcome_acquired") else 0,str(record.get("terminal_reason") or ""),str(record["source_high_water_mark"]),int(record.get("reacquisition_generation") or 0),now,now,now,now,_canon(record.get("meta") or {}))
    stmts.append(("""INSERT OR IGNORE INTO gap_targeted_acquisition_lifecycle (acquisition_id,promotion_id,promotion_signature,request_signature,namespace,state_schema,state_hash,primary_action,source_snapchain_id,source_step_index,source_before_state_digest,source_identity,acquisition_protocol,protocol_version,schedule_id,schedule_digest,status,attempts_budget,attempts_executed,selected_attempt_index,direct_outcome_acquired,terminal_reason,source_high_water_mark,reacquisition_generation,created_ts,started_ts,terminal_ts,updated_ts,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",vals))
    for f,t,r in event_specs:
        es=_sha({"acquisition_id":record["acquisition_id"],"from_status":f,"to_status":t,"reason":r,"payload_digest":pd})
        stmts.append(("""INSERT OR IGNORE INTO gap_targeted_acquisition_events (event_signature,acquisition_id,from_status,to_status,actor,reason,writer_id,schema_version,payload_digest,payload_json,created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",(es,record["acquisition_id"],f,t,"core.snake_targeted_acquisition_schedule",r,WRITER_ID,EVENT_SCHEMA,pd,_canon(p),now)))
    db_writer_client.transaction(stmts,tag="targeted_acquisition_lifecycle.completed_run",priority="normal",timeout_ms=timeout_ms,db="oroma")
    return {"ok":True,"write_attempted":True,"reason":"completed_run_persisted_or_existing","acquisition_id":record["acquisition_id"],"events_planned":len(event_specs)}

__all__.append("persist_completed_run")

TERMINAL_STATUSES = frozenset({"evidence_acquired", "exhausted_no_direct_outcome", "blocked"})


def read_lifecycle_record(db_path: str, acquisition_id: str) -> Optional[Dict[str, Any]]:
    """Read one lifecycle row via SQLite URI mode=ro; never creates schema."""
    import sqlite3
    from pathlib import Path
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_targeted_acquisition_lifecycle'"
        ).fetchone()
        if exists is None:
            return None
        row = con.execute(
            "SELECT * FROM gap_targeted_acquisition_lifecycle WHERE acquisition_id=? LIMIT 1",
            (str(acquisition_id),),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def lifecycle_guard(db_path: str, acquisition_id: str) -> Dict[str, Any]:
    """Return a fail-closed pre-simulation decision for an acquisition identity."""
    record = read_lifecycle_record(db_path, acquisition_id)
    if record is None:
        return {
            "allowed": True,
            "reason": "lifecycle_not_found",
            "already_terminal": False,
            "record": None,
        }
    status = str(record.get("status") or "")
    if status in TERMINAL_STATUSES:
        return {
            "allowed": False,
            "reason": "already_terminal",
            "already_terminal": True,
            "status": status,
            "record": record,
        }
    return {
        "allowed": True,
        "reason": "lifecycle_nonterminal",
        "already_terminal": False,
        "status": status,
        "record": record,
    }


__all__.extend(["TERMINAL_STATUSES", "read_lifecycle_record", "lifecycle_guard"])


def compatible_terminal_guard(
    db_path: str,
    *,
    promotion_id: int,
    source_snapchain_id: int,
    source_step_index: int,
    acquisition_protocol: str,
    protocol_version: str,
    schedule_digest: str,
    reacquisition_generation: int = 0,
) -> Dict[str, Any]:
    """Recognize a terminal pre-G2 row whose before digest was not persisted.

    Compatibility is intentionally narrow: promotion, physical source coordinates,
    protocol, schedule and generation must all be identical. The row is never
    modified; new acquisitions continue to use the fully digest-bound identity.
    """
    import sqlite3
    from pathlib import Path
    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_targeted_acquisition_lifecycle'"
        ).fetchone()
        if exists is None:
            return {"matched": False, "reason": "lifecycle_table_missing"}
        row = con.execute(
            """
            SELECT * FROM gap_targeted_acquisition_lifecycle
             WHERE promotion_id=?
               AND source_snapchain_id=?
               AND source_step_index=?
               AND acquisition_protocol=?
               AND protocol_version=?
               AND schedule_digest=?
               AND reacquisition_generation=?
               AND COALESCE(source_before_state_digest,'')=''
               AND status IN ('evidence_acquired','exhausted_no_direct_outcome','blocked')
             ORDER BY updated_ts DESC
             LIMIT 1
            """,
            (
                int(promotion_id), int(source_snapchain_id), int(source_step_index),
                str(acquisition_protocol), str(protocol_version), str(schedule_digest),
                int(reacquisition_generation),
            ),
        ).fetchone()
        return {
            "matched": row is not None,
            "reason": "legacy_terminal_compatible" if row is not None else "legacy_terminal_not_found",
            "record": dict(row) if row is not None else None,
        }
    finally:
        con.close()


__all__.append("compatible_terminal_guard")


def equivalent_protocol_terminal_guard(
    db_path: str,
    *,
    promotion_id: int,
    promotion_signature: str,
    acquisition_protocol: str,
    protocol_version: str,
    schedule_digest: str,
    reacquisition_generation: int = 0,
) -> Dict[str, Any]:
    """Block an already terminal promotion/protocol generation independent of source.

    A different reconstructable source does not by itself authorize repeating the
    same bounded acquisition protocol. Re-execution requires an explicit new
    ``reacquisition_generation`` or a changed protocol/schedule identity. This
    prevents newly arriving equivalent traces from causing endless repeats while
    retaining the exact physical source in each persisted acquisition record.
    """
    import sqlite3
    from pathlib import Path

    path = Path(db_path).resolve()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_targeted_acquisition_lifecycle'"
        ).fetchone()
        if exists is None:
            return {"matched": False, "reason": "lifecycle_table_missing", "record": None}
        row = con.execute(
            """
            SELECT * FROM gap_targeted_acquisition_lifecycle
             WHERE promotion_id=?
               AND promotion_signature=?
               AND acquisition_protocol=?
               AND protocol_version=?
               AND schedule_digest=?
               AND reacquisition_generation=?
               AND status IN ('evidence_acquired','exhausted_no_direct_outcome','blocked')
             ORDER BY updated_ts DESC, acquisition_id ASC
             LIMIT 1
            """,
            (
                int(promotion_id),
                str(promotion_signature),
                str(acquisition_protocol),
                str(protocol_version),
                str(schedule_digest),
                int(reacquisition_generation),
            ),
        ).fetchone()
        return {
            "matched": row is not None,
            "reason": "terminal_equivalent_protocol_found" if row is not None else "terminal_equivalent_protocol_not_found",
            "record": dict(row) if row is not None else None,
        }
    finally:
        con.close()


__all__.append("equivalent_protocol_terminal_guard")


def persist_completed_run_with_evidence(
    record: Mapping[str, Any],
    *,
    final_status: str,
    payload: Mapping[str, Any],
    evidence_blob: Mapping[str, Any],
    timeout_ms: int = 15000,
) -> Dict[str, Any]:
    """Atomically persist a terminal lifecycle, its events, and one evidence SnapChain.

    This narrow G3.1 writer is intentionally separate from ``persist_completed_run``
    so all existing G1/G2 callers retain their exact behavior.  It accepts only an
    ``evidence_acquired`` terminal state and a fully materialized immutable targeted
    evidence blob.  The evidence insert is idempotent on the indexed numeric
    ``snapchains.source_id`` and remains inside the same DBWriter transaction as the
    lifecycle row and append-only events.

    No direct SQLite fallback exists.  Queue, promotion, and policy tables are never
    touched by this function.
    """
    if final_status != "evidence_acquired":
        return {"ok": False, "write_attempted": False, "reason": "evidence_writer_requires_evidence_acquired"}
    if not bool(record.get("direct_outcome_acquired")):
        return {"ok": False, "write_attempted": False, "reason": "direct_outcome_not_acquired"}

    blob = json.loads(json.dumps(dict(evidence_blob), ensure_ascii=False))
    experiment_id = str(blob.get("experiment_id") or "").strip()
    source_id = int(blob.get("source_id") or 0)
    if not experiment_id or source_id <= 0:
        return {"ok": False, "write_attempted": False, "reason": "evidence_identity_invalid"}
    if str(blob.get("evidence_schema") or "") != "snake_targeted_evidence:v1":
        return {"ok": False, "write_attempted": False, "reason": "evidence_schema_invalid"}
    if str(blob.get("evidence_class") or "") != "targeted_simulation_observation":
        return {"ok": False, "write_attempted": False, "reason": "evidence_class_invalid"}
    result_meta = blob.get("result") if isinstance(blob.get("result"), Mapping) else {}
    if not bool(result_meta.get("ready_for_replay_capability")):
        return {"ok": False, "write_attempted": False, "reason": "evidence_not_replay_capable"}

    d1 = REGISTRY.decision("acquisition_pending", "acquiring", record)
    d2 = REGISTRY.decision("acquiring", final_status, record)
    if not d1["allowed"] or not d2["allowed"]:
        return {
            "ok": False,
            "write_attempted": False,
            "reason": "illegal_transition_sequence",
            "decisions": [d1, d2],
        }
    ready, why = dbwriter_ready()
    if not ready:
        return {"ok": False, "write_attempted": False, "reason": why}

    now = int(time.time())
    p = dict(payload)
    pd = _sha(p)
    event_specs = [
        ("acquisition_pending", "acquiring", "food_directed_schedule_started"),
        ("acquiring", "evidence_acquired", str(record.get("terminal_reason") or "direct_outcome_acquired")),
    ]
    stmts = schema_statements()
    vals = (
        record["acquisition_id"], int(record["promotion_id"]), record["promotion_signature"],
        record["request_signature"], record["namespace"], record["state_schema"], record["state_hash"],
        str(record.get("primary_action") or ""), int(record.get("source_snapchain_id") or 0),
        int(record.get("source_step_index") or 0), str(record.get("source_before_state_digest") or ""),
        record["source_identity"], record["acquisition_protocol"], record["protocol_version"],
        record["schedule_id"], record["schedule_digest"], final_status, int(record["attempts_budget"]),
        int(record.get("attempts_executed") or 0), record.get("selected_attempt_index"), 1,
        str(record.get("terminal_reason") or "direct_outcome_acquired"), str(record["source_high_water_mark"]),
        int(record.get("reacquisition_generation") or 0), now, now, now, now,
        _canon(record.get("meta") or {}),
    )
    stmts.append((
        """INSERT OR IGNORE INTO gap_targeted_acquisition_lifecycle
           (acquisition_id,promotion_id,promotion_signature,request_signature,namespace,state_schema,state_hash,
            primary_action,source_snapchain_id,source_step_index,source_before_state_digest,source_identity,
            acquisition_protocol,protocol_version,schedule_id,schedule_digest,status,attempts_budget,
            attempts_executed,selected_attempt_index,direct_outcome_acquired,terminal_reason,
            source_high_water_mark,reacquisition_generation,created_ts,started_ts,terminal_ts,updated_ts,meta_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        vals,
    ))
    for from_status, to_status, reason in event_specs:
        event_signature = _sha({
            "acquisition_id": record["acquisition_id"],
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
            "payload_digest": pd,
        })
        stmts.append((
            """INSERT OR IGNORE INTO gap_targeted_acquisition_events
               (event_signature,acquisition_id,from_status,to_status,actor,reason,writer_id,
                schema_version,payload_digest,payload_json,created_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_signature, record["acquisition_id"], from_status, to_status,
                "core.snake_targeted_acquisition_schedule_v2", reason, WRITER_ID,
                EVENT_SCHEMA, pd, _canon(p), now,
            ),
        ))

    raw_blob = _canon(blob).encode("utf-8")
    stmts.append((
        """INSERT INTO snapchains
             (ts,quality,blob,exported,status,origin,gap_flag,notes,namespace,source_id,version,weight)
           SELECT ?,?,?,?,?,?,?,?,?,?,?,?
            WHERE NOT EXISTS (
                SELECT 1 FROM snapchains
                 WHERE source_id=? AND origin=? AND namespace=?
            )""",
        (
            now, 1.0, raw_blob, 0, "active", "targeted_evidence_runner:v1", 0,
            "immutable promotion-bound food-directed targeted simulation observation; no policy write",
            "game:snake", source_id, "snake_targeted_evidence:v1", 1.0,
            source_id, "targeted_evidence_runner:v1", "game:snake",
        ),
    ))

    db_writer_client.transaction(
        stmts,
        tag="targeted_acquisition_lifecycle.completed_run_with_evidence",
        priority="high",
        timeout_ms=timeout_ms,
        db="oroma",
    )
    return {
        "ok": True,
        "write_attempted": True,
        "reason": "lifecycle_and_evidence_persisted_or_existing",
        "acquisition_id": record["acquisition_id"],
        "experiment_id": experiment_id,
        "evidence_source_id": source_id,
        "events_planned": len(event_specs),
        "max_evidence_writes_per_run": 1,
    }


__all__.append("persist_completed_run_with_evidence")

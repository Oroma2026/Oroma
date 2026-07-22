#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/gap_policy_promotion_recovery.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Gap Policy Promotion Lineage Recovery · DBWriter-only
# Version:   v0.1.0-targeted-lineage-recovery
# Stand:     2026-07-18
# Autor:     Jörg Werner · ORÓMA Project · GPT-5.6 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Rekonstruiert ausschließlich historisch verwaiste Promotion-Zeilen, wenn eine
# bereits persistierte Outcome-Zeile einen vollständigen, unveränderlichen und
# intern konsistenten targeted-learning Lineage-Snapshot enthält. Das Modul ist
# kein allgemeiner Reparatur- oder Importpfad. Unvollständige, widersprüchliche
# oder bereits geschriebene Lineages werden strikt blockiert.
#
# SICHERHEITSINVARIANTEN
# ----------------------
# - DB-Lesen ausschließlich read-only über SQLite URI mode=ro.
# - DB-Schreiben ausschließlich über den zentralen DBWriter.
# - Kein lokaler SQLite-Write-Fallback.
# - Keine Policy-Mutation, keine Runner-/Replay-/Dream-Starts.
# - Keine künstliche Aktualisierung historischer Validierungszeitpunkte.
# - Exakte Identitätsprüfung zwischen Outcome-Zeile und eingebettetem Snapshot.
# - Explizites Double-Gate per ENABLE und Confirm-Token.
# - Dedupe über Promotion-ID und promotion_signature.
# =============================================================================
from __future__ import annotations
import json, os, sqlite3, time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, List
from core import db_writer_client
from core.gap_policy_promotion import _schema_statements

VERSION='v0.1.0-targeted-lineage-recovery'
CONFIRM_REQUIRED='GAP_POLICY_PROMOTION_RECOVERY_REVIEWED'

def _base()->Path: return Path(os.environ.get('OROMA_BASE') or '/opt/ai/oroma').resolve()
def _db()->Path: return Path(os.environ.get('OROMA_DB_PATH') or (_base()/'data/oroma.db')).resolve()
def _ro(p:Path):
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=5); c.row_factory=sqlite3.Row; return c
def _j(v:Any)->Dict[str,Any]:
    try:
        x=json.loads(str(v or '')); return x if isinstance(x,dict) else {}
    except Exception:return {}
def _s(v:Any,n:int=4000)->str:return str(v or '').strip()[:n]
def _i(v:Any,d:int=0)->int:
    try:return int(float(v))
    except Exception:return d
def _f(v:Any,d:float=0.0)->float:
    try:return float(v)
    except Exception:return d

def inspect_candidate(db_path:Path,outcome_id:int)->Dict[str,Any]:
    with _ro(db_path) as c:
        r=c.execute('SELECT * FROM gap_evidence_outcome_queue WHERE id=?',(int(outcome_id),)).fetchone()
        if not r:return {'ok':False,'blocked_reasons':['outcome_not_found']}
        row=dict(r); meta=_j(row.get('meta_json'))
        ep=meta.get('evidence_payload') if isinstance(meta.get('evidence_payload'),dict) else {}
        targeted=ep.get('targeted') if isinstance(ep.get('targeted'),dict) else {}
        lineage=targeted.get('learning_intent_lineage') if isinstance(targeted.get('learning_intent_lineage'),dict) else {}
        reasons=[]
        required=('promotion_id','promotion_signature','request_signature','evidence_queue_id','plan_id','focus_id','target','promotion_bucket','namespace','state_hash','primary_action','source_validation_bucket','source_validation_ts')
        for k in required:
            if lineage.get(k) in (None,''): reasons.append(f'lineage_missing:{k}')
        checks={
          'promotion_id':(_i(row.get('promotion_id')),_i(lineage.get('promotion_id'))),
          'promotion_signature':(_s(row.get('promotion_signature'),160),_s(lineage.get('promotion_signature'),160)),
          'request_signature':(_s(row.get('request_signature'),160),_s(lineage.get('request_signature'),160)),
          'namespace':(_s(row.get('namespace'),160),_s(lineage.get('namespace'),160)),
          'state_hash':(_s(row.get('state_hash')), _s(lineage.get('state_hash'))),
          'action':(_s(row.get('action'),160),_s(lineage.get('primary_action'),160)),
          'target':(_s(row.get('target'),80),_s(lineage.get('target'),80)),
        }
        for k,(a,b) in checks.items():
            if a!=b: reasons.append(f'identity_mismatch:{k}')
        existing_id=c.execute('SELECT id,promotion_signature FROM gap_policy_promotion_queue WHERE id=?',(checks['promotion_id'][0],)).fetchone()
        existing_sig=c.execute('SELECT id,promotion_signature FROM gap_policy_promotion_queue WHERE promotion_signature=?',(checks['promotion_signature'][0],)).fetchone()
        if existing_id: reasons.append('promotion_id_already_exists')
        if existing_sig: reasons.append('promotion_signature_already_exists')
        led=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gap_policy_mini_write_ledger'").fetchone()
        if led:
            wr=c.execute('SELECT 1 FROM gap_policy_mini_write_ledger WHERE promotion_signature=? LIMIT 1',(checks['promotion_signature'][0],)).fetchone()
            if wr: reasons.append('promotion_already_in_ledger')
        score=_f((meta.get('raw_candidate') or {}).get('score') if isinstance(meta.get('raw_candidate'),dict) else 0.0)
        candidate={
          'id':_i(lineage.get('promotion_id')),'promotion_signature':_s(lineage.get('promotion_signature'),128),
          'request_signature':_s(lineage.get('request_signature'),128),'evidence_queue_id':_i(lineage.get('evidence_queue_id')),
          'plan_id':_s(lineage.get('plan_id'),160),'focus_id':_s(lineage.get('focus_id'),160),
          'target':_s(lineage.get('target'),80),'promotion_bucket':_s(lineage.get('promotion_bucket'),120),
          'namespace':_s(lineage.get('namespace'),160),'state_hash':_s(lineage.get('state_hash')),
          'primary_action':_s(lineage.get('primary_action'),160),'score':score,
          'source_validation_bucket':_s(lineage.get('source_validation_bucket'),160),
          'source_validation_ts':_i(lineage.get('source_validation_ts')),
          'created_ts':_i(lineage.get('promotion_updated_ts') or lineage.get('source_validation_ts')),
          'updated_ts':_i(lineage.get('promotion_updated_ts') or lineage.get('source_validation_ts')),
          'meta_json':json.dumps({'version':VERSION,'recovery_source':'gap_evidence_outcome_queue','outcome_queue_id':_i(row.get('id')),'immutable_lineage_snapshot':lineage},sort_keys=True,ensure_ascii=False),
        }
        return {'ok':not reasons,'blocked_reasons':reasons,'candidate':candidate,'outcome_id':_i(row.get('id'))}

def run_once(*,db_path:Optional[Path]=None,outcome_id:int,write_enable:Optional[bool]=None,confirm_token:Optional[str]=None)->Dict[str,Any]:
    p=(db_path or _db()).resolve(); enable=bool(write_enable if write_enable is not None else os.environ.get('OROMA_GAP_POLICY_PROMOTION_RECOVERY_ENABLE')=='1'); token=str(confirm_token if confirm_token is not None else os.environ.get('OROMA_GAP_POLICY_PROMOTION_RECOVERY_CONFIRM',''))
    result={'ok':False,'version':VERSION,'db_path':str(p),'outcome_id':int(outcome_id),'write_enable':enable,'confirm_ok':token==CONFIRM_REQUIRED,'transaction_ok':False,'errors':[]}
    chk=inspect_candidate(p,outcome_id); result['inspection']=chk
    if not chk.get('ok'): result['blocked_reason']='candidate_not_recoverable'; return result
    if not enable: result['blocked_reason']='write_disabled'; result['ok']=True; return result
    if token!=CONFIRM_REQUIRED: result['blocked_reason']='confirm_token_mismatch'; return result
    c=chk['candidate']; stmt=('''INSERT INTO gap_policy_promotion_queue (id,promotion_signature,request_signature,evidence_queue_id,plan_id,focus_id,target,promotion_bucket,namespace,state_hash,primary_action,kind,reason,recommended_next,score,status,policy_write_allowed,source_validation_bucket,source_validation_ts,created_ts,updated_ts,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(c['id'],c['promotion_signature'],c['request_signature'],c['evidence_queue_id'],c['plan_id'],c['focus_id'],c['target'],c['promotion_bucket'],c['namespace'],c['state_hash'],c['primary_action'],'targeted_lineage_recovery','historical_promotion_missing','gap_policy_mini_write_gate',c['score'],'promotion_review',0,c['source_validation_bucket'],c['source_validation_ts'],c['created_ts'],c['updated_ts'],c['meta_json']))
    try:
        db_writer_client.transaction([*_schema_statements(),stmt],tag='gap_policy_promotion.recovery',priority='normal',timeout_ms=15000,db='oroma')
        result['transaction_ok']=True; result['ok']=True; result['blocked_reason']=None
    except Exception as e: result['errors'].append(str(e)); result['blocked_reason']='dbwriter_transaction_failed'
    return result

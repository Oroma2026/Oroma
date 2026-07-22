import json,sqlite3
from pathlib import Path
from core.gap_policy_promotion import _schema_statements
from core.gap_policy_promotion_recovery import inspect_candidate

def apply(con,stmts):
    for sql,p in stmts: con.execute(sql,p)

def test_promotion_table_rejects_delete(tmp_path:Path):
    db=tmp_path/'x.db'; con=sqlite3.connect(db); apply(con,_schema_statements())
    con.execute("INSERT INTO gap_policy_promotion_queue(promotion_signature,request_signature,target,promotion_bucket,created_ts,updated_ts) VALUES('p','r','replay','promotion_candidate_replay',1,1)")
    try: con.execute('DELETE FROM gap_policy_promotion_queue')
    except sqlite3.IntegrityError as e: assert 'append-only' in str(e)
    else: raise AssertionError('delete unexpectedly succeeded')

def test_recovery_requires_complete_matching_snapshot(tmp_path:Path):
    db=tmp_path/'x.db'; con=sqlite3.connect(db); apply(con,_schema_statements())
    con.execute('''CREATE TABLE gap_evidence_outcome_queue(id INTEGER PRIMARY KEY,outcome_signature TEXT,promotion_signature TEXT,request_signature TEXT,promotion_id INTEGER,target TEXT,namespace TEXT,state_hash TEXT,action TEXT,outcome TEXT,confidence REAL,evidence_source TEXT,replay_source TEXT,status TEXT,policy_write_allowed INTEGER,source_probe_ts INTEGER,created_ts INTEGER,updated_ts INTEGER,meta_json TEXT)''')
    lin={'promotion_id':7,'promotion_signature':'ps','request_signature':'rs','evidence_queue_id':9,'plan_id':'pl','focus_id':'fo','target':'replay','promotion_bucket':'promotion_candidate_replay','namespace':'game:snake','state_hash':'snake:pro_v2:x','primary_action':'1','source_validation_bucket':'validated_replay_execution_candidate','source_validation_ts':10,'promotion_updated_ts':11}
    meta={'evidence_payload':{'targeted':{'learning_intent_lineage':lin}},'raw_candidate':{'score':1.0}}
    con.execute("INSERT INTO gap_evidence_outcome_queue VALUES(1,'os','ps','rs',7,'replay','game:snake','snake:pro_v2:x','1','pos',1,'x','x','outcome_ready',0,12,12,12,?)",(json.dumps(meta),)); con.commit(); con.close()
    out=inspect_candidate(db,1); assert out['ok']; assert out['candidate']['id']==7

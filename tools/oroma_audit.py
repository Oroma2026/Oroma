#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad: /opt/ai/oroma/tools/oroma_audit.py
# Projekt: ORÓMA
# Modul: Einheitliches read-only Audit-CLI (G1: Acquisition/Promotion)
# Version: v0.1.0
# =============================================================================
from __future__ import annotations
import argparse,json,sqlite3,sys

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--db",default="data/oroma.db"); p.add_argument("--acquisition"); p.add_argument("--promotion",type=int); p.add_argument("--limit",type=int,default=100); p.add_argument("--pretty",action="store_true"); a=p.parse_args()
    con=sqlite3.connect(f"file:{a.db}?mode=ro",uri=True); con.row_factory=sqlite3.Row
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out={"ok":True,"version":"v0.1.0-read-only-audit","query":{},"lifecycle":[],"events":[],"safety":{"db_open_mode":"read_only_uri_mode_ro","db_writes":False,"policy_writes":False}}
    if "gap_targeted_acquisition_lifecycle" not in tables:
        out.update({"ok":False,"reason":"lifecycle_table_missing"})
    else:
        if a.acquisition:
            rows=con.execute("SELECT * FROM gap_targeted_acquisition_lifecycle WHERE acquisition_id=?",(a.acquisition,)).fetchall(); out["query"]={"acquisition_id":a.acquisition}
        elif a.promotion is not None:
            rows=con.execute("SELECT * FROM gap_targeted_acquisition_lifecycle WHERE promotion_id=? ORDER BY reacquisition_generation,updated_ts",(a.promotion,)).fetchall(); out["query"]={"promotion_id":a.promotion}
        else:
            rows=con.execute("SELECT * FROM gap_targeted_acquisition_lifecycle ORDER BY updated_ts DESC LIMIT ?",(max(1,a.limit),)).fetchall(); out["query"]={"latest":max(1,a.limit)}
        out["lifecycle"]=[dict(r) for r in rows]
        ids=[r["acquisition_id"] for r in rows]
        if ids and "gap_targeted_acquisition_events" in tables:
            q=",".join("?" for _ in ids); out["events"]=[dict(r) for r in con.execute(f"SELECT * FROM gap_targeted_acquisition_events WHERE acquisition_id IN ({q}) ORDER BY event_id",ids)]
    con.close(); print(json.dumps(out,ensure_ascii=False,indent=2 if a.pretty else None)); return 0 if out.get("ok") else 2
if __name__=="__main__": raise SystemExit(main())

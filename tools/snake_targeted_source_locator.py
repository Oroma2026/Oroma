#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/snake_targeted_source_locator.py
# Projekt: ORÓMA
# Modul:   Snake Targeted Source Locator CLI – Read-Only
# Version: v0.1.0-deterministic-source-locator
# Stand:   2026-07-15
# =============================================================================
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from core.snake_targeted_source_locator import locate_source
from tools.snake_targeted_evidence_runner import _load_promotion

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=os.environ.get("OROMA_DB_PATH","data/oroma.db"))
    p.add_argument("--promotion-id",type=int,required=True)
    p.add_argument("--scan-limit",type=int,default=5000)
    p.add_argument("--pretty",action="store_true")
    a=p.parse_args()
    try:
        promotion=_load_promotion(a.db,a.promotion_id)
        result=locate_source(a.db,state_hash=str(promotion["state_hash"]),scan_limit=a.scan_limit)
        result["promotion"]={
            "promotion_id":promotion["promotion_id"],
            "promotion_signature":promotion["promotion_signature"],
            "request_signature":promotion["request_signature"],
            "state_hash":promotion["state_hash"],
            "target_action":promotion["primary_action"],
        }
    except Exception as exc:
        result={"ok":False,"found":False,"reason":"source_locator_failed","errors":[f"{type(exc).__name__}:{exc}"],"safety":{"db_reads":True,"db_writes":False,"policy_writes":False,"queue_writes":False,"promotion_writes":False}}
    print(json.dumps(result,ensure_ascii=False,indent=2 if a.pretty else None))
    return 0 if result.get("ok") else 2
if __name__=="__main__": raise SystemExit(main())

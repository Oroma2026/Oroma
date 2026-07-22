#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/gap_policy_promotion_recovery.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Promotion Lineage Recovery CLI · DBWriter-only
# Version:   v0.1.0-targeted-lineage-recovery
# Stand:     2026-07-18
# =============================================================================
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from core import gap_policy_promotion_recovery as r
ap=argparse.ArgumentParser(description='Recover one fully snapshotted orphan promotion')
ap.add_argument('--once',action='store_true'); ap.add_argument('--outcome-id',type=int,required=True); ap.add_argument('--db'); ap.add_argument('--pretty',action='store_true')
a=ap.parse_args(); out=r.run_once(db_path=Path(a.db).resolve() if a.db else None,outcome_id=a.outcome_id); print(json.dumps(out,ensure_ascii=False,indent=2 if a.pretty else None,sort_keys=True)); raise SystemExit(0 if out.get('ok') else 2)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Smoke Test (Quick Integration Check)
-------------------------------------------------
- Importiert Kernmodule, Wrapper und UI
- Testet minimale Funktionen (Snap, SnapChain, SQL, MetaSnaps, RAG)
- Läuft in < 1 Minute → für CI oder manuelle Verifikation
"""

import os, sys, json, time

BASE = "/opt/ai/oroma"
sys.path.insert(0, BASE)

out = {}

# --- Imports --------------------------------------------------------
try:
    from core import (
        sql_manager, snap, snaptoken, snapchain, fusion,
        meta_snap, rag_bridge, reward, curiosity,
        diagnostics, predictor, episodic, explain,
        mutation, dream_worker
    )
    from wrappers import oroma_wrapper, vision_wrapper, audio_wrapper
except Exception as e:
    print("[FATAL] Imports fehlgeschlagen:", e)
    sys.exit(2)

# --- DB init --------------------------------------------------------
sql_manager.ensure_schema()
conn = sql_manager.get_conn()

# --- 1. Snap & SnapChain -------------------------------------------
try:
    s = snap.Snap(features=[0.1, 0.2], metadata={"test": True})
    ch = snapchain.SnapChain(patterns=[s], metadata={"origin": "smoketest"})
    sql_manager.insert_chain_quick(blob=ch.as_blob(), quality=0.9, origin="smoketest")
    out["snapchain"] = "OK"
except Exception as e:
    out["snapchain"] = f"FAIL: {e}"

# --- 2. Fusion & RAG ------------------------------------------------
try:
    tok = snaptoken.SnapToken(text="Hallo ORÓMA", token_id=1, source="smoketest")
    fused = fusion.fuse_snap_token(s, tok)
    rag = rag_bridge.RAGStore("/opt/ai/oroma/data/knowledge.db")
    rag.add_doc("smoketest", "ORÓMA ist aktiv.")
    out["fusion_rag"] = {"fusion": bool(fused), "docs": rag.count_docs()}
except Exception as e:
    out["fusion_rag"] = f"FAIL: {e}"

# --- 3. Meta-Snaps -------------------------------------------------
try:
    ms = meta_snap.MetaSnap(label="TestMeta", sources=[1, 2], score=0.75)
    sql_manager.insert_meta_snap(ms)
    out["metasnap"] = "OK"
except Exception as e:
    out["metasnap"] = f"FAIL: {e}"

# --- 4. Reward & Curiosity -----------------------------------------
try:
    reward.log_reward("pong", 1.0)
    agg = reward.RewardAggregator()
    cur_score = curiosity.curiosity_score(pred=[0.1, 0.2], obs=[0.2, 0.3])
    out["reward_curiosity"] = {"reward_mean": agg.window_mean("pong", 10), "curiosity": cur_score.signal}
except Exception as e:
    out["reward_curiosity"] = f"FAIL: {e}"

# --- 5. Diagnostics -------------------------------------------------
try:
    diag = diagnostics.quick_summary()
    out["diagnostics"] = diag.get("summary", {})
except Exception as e:
    out["diagnostics"] = f"FAIL: {e}"

# --- 6. DreamWorker (dry-run) --------------------------------------
try:
    dw = dream_worker.DreamWorker(dry_run=True)
    result = dw.run_once()
    out["dreamworker"] = "OK" if result is not None else "EMPTY"
except Exception as e:
    out["dreamworker"] = f"FAIL: {e}"

# --- 7. DB sanity ---------------------------------------------------
try:
    cur = conn.execute("SELECT COUNT(*) AS c FROM snapchains")
    out["db_snapchains"] = int(cur.fetchone()["c"])
except Exception as e:
    out["db_snapchains"] = f"FAIL: {e}"

# --- Ausgabe --------------------------------------------------------
print(json.dumps(out, indent=2, ensure_ascii=False))
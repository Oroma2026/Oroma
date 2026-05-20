# /opt/ai/oroma/tools/sim_learn.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Lernsimulation (Day/Dream/Export)
----------------------------------------------
- Erzeugt synthetische Features (~64D), baut SnapChains, speichert in Test-DB
- Simuliert Day-Modus (Insertion) und Dream-Modus (Verdichtung/MetaSnaps)
- Misst Insert-Performance, Recall, Export-Markierungen
- Nutzt aktualisierte Core-Module (DreamWorker, ExportGate, MetaSnaps optional)
"""

from __future__ import annotations
import os, sys, time, json, random, argparse
from pathlib import Path
import logging
from core.log_guard import log_suppressed

BASE = "/opt/ai/oroma"
sys.path.insert(0, BASE)

# ---------------- Imports ----------------
db_ok = True
try:
    from core.sql_manager import ensure_schema, get_db_path, insert_chain_quick, count_snapchains, fetch_recent
except Exception as e:
    print("[FATAL] sql_manager nicht verfügbar:", e)
    db_ok = False

snap_ok = True
try:
    from core.snap import Snap
    from core.snapchain import SnapChain
except Exception as e:
    print("[WARN] Snap/SnapChain nicht verfügbar:", e)
    snap_ok = False

try:
    from core.langzeitgedaechtnis import recall_similar
    recall_ok = True
except Exception:
    recall_ok = False

try:
    from core.dream_worker import compact_batch
    dream_ok = True
except Exception:
    dream_ok = False

try:
    from core.export_gate import mark_all_recent, export_ready
    export_ok = True
except Exception:
    export_ok = False

try:
    from core.meta_snap import MetaSnap
    metasnap_ok = True
except Exception:
    metasnap_ok = False

# ---------------- Feature Generator ----------------
def make_features(dim=64, cls=0) -> list[float]:
    base = 3.0 if cls == 1 else 0.0
    return [random.gauss(base, 1.0) for _ in range(dim)]

def make_blob(dim=64, cls=0):
    feats = make_features(dim, cls)
    if snap_ok:
        s = Snap(features=feats, metadata={"sim": True, "cls": cls})
        ch = SnapChain(patterns=[s], metadata={"origin": "simulation"})
        try:
            return ch.as_blob()
        except Exception as e:
            log_suppressed('tools/sim_learn.py:72', exc=e, level=logging.WARNING)
            pass
    return json.dumps({"features": feats, "cls": cls, "origin": "simulation"}).encode("utf-8")

# ---------------- Simulationen ----------------
def simulate_day(num=2000, dim=64):
    t0 = time.time()
    inserted = 0
    for i in range(num):
        cls = i % 2
        blob = make_blob(dim=dim, cls=cls)
        try:
            insert_chain_quick(blob=blob, quality=0.5 + 0.5 * random.random(), origin="sim_day")
            inserted += 1
        except Exception as e:
            print("[ERR] insert_chain_quick:", e)
            break
    dt = time.time() - t0
    return {"inserted": inserted, "sec": round(dt, 2), "rps": round(inserted / dt, 2) if dt > 0 else None}

def simulate_dream(batch=500, dim=64):
    if dream_ok:
        try:
            n = compact_batch(limit=batch, dim=dim, origin="sim_dream")
            return {"compacted": n, "via": "DreamWorker"}
        except Exception as e:
            return {"compacted": 0, "error": str(e)}
    return {"compacted": 0, "via": "manual-fallback"}

def try_recall_probe(dim=64):
    if not recall_ok:
        return {"ok": False, "msg": "recall_similar not available"}
    q = make_features(dim=dim, cls=random.randint(0,1))
    try:
        res = recall_similar(q, topk=5)
        return {"ok": True, "num": len(res) if res else 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def maybe_mark_export():
    if not export_ok:
        return {"ok": False, "msg": "export_gate not available"}
    try:
        n = mark_all_recent(days=1, min_quality=0.6)
        ready = export_ready()
        return {"ok": True, "marked": n, "export_ready": bool(ready)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def maybe_metasnap(dim=64):
    if not metasnap_ok or not os.getenv("OROMA_ENABLE_METASNAP", "false").lower() == "true":
        return {"enabled": False}
    feats = [make_features(dim=dim, cls=random.randint(0,1)) for _ in range(10)]
    try:
        m = MetaSnap(label="simulated", sources=[f"sim_{i}" for i in range(len(feats))], score=0.9)
        return {"enabled": True, "label": m.label, "score": m.score}
    except Exception as e:
        return {"enabled": False, "error": str(e)}

# ---------------- Main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="Pfad zu alternativer Test-DB", default=os.environ.get("OROMA_DB_SIM",""))
    ap.add_argument("--day", type=int, default=2000, help="Anzahl SnapChains Day-Modus")
    ap.add_argument("--dim", type=int, default=64, help="Feature-Dimension")
    args = ap.parse_args()

    if args.db:
        os.environ["OROMA_DB"] = args.db
        print("[i] OROMA_DB =", os.environ["OROMA_DB"])

    if not db_ok:
        print("[FATAL] sql_manager nicht verfügbar – Simulation abgebrochen.")
        sys.exit(2)

    ensure_schema()
    db_path = get_db_path()
    before = count_snapchains()

    print(f"[+] DB: {db_path} – Chains vor Simulation: {before}")

    r_day = simulate_day(num=args.day, dim=args.dim)
    mid = count_snapchains()

    r_dream = simulate_dream(batch=min(args.day, 1000), dim=args.dim)
    after = count_snapchains()

    r_recall = try_recall_probe(dim=args.dim)
    r_export = maybe_mark_export()
    r_meta = maybe_metasnap(dim=args.dim)

    report = {
        "db": db_path,
        "counts": {"before": before, "after_day": mid, "after_dream": after},
        "day_perf": r_day,
        "dream_compaction": r_dream,
        "recall_probe": r_recall,
        "export_marking": r_export,
        "metasnap": r_meta,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
import sqlite3, os, json
DB = os.environ.get("OROMA_DB_PATH") or os.path.join(os.environ.get("OROMA_BASE_DIR","/opt/ai/oroma"), "data", "oroma.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def count(tbl):
    try:
        return cur.execute(f"SELECT COUNT(*) c FROM {tbl}").fetchone()["c"]
    except Exception as e:
        return f"ERR: {e}"

tables = ["snapchains","meta_snaps","hypotheses","coverage_probe","tuning_suggestions"]
res = { "db": DB, "tables": {t: count(t) for t in tables} }

# Optional: einfache Stichprobe der letzten SnapChains
try:
    rows = cur.execute("SELECT id, created_at, weight, status FROM snapchains ORDER BY created_at DESC LIMIT 5").fetchall()
    res["snapchains_last5"] = [dict(r) for r in rows]
except Exception as e:
    res["snapchains_last5"] = f"ERR: {e}"

print(json.dumps(res, indent=2))
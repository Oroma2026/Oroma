# /opt/ai/oroma/tests/test_sim_learning.py
# -*- coding: utf-8 -*-
"""
ORÓMA v3.5 – Test: Lernsimulation (Day/Dream/Recall/Export/MetaSnaps)
- Führt tools/sim_learn.py mit Test-DB aus
- Erwartet JSON-Output mit counts, day_perf, dream_compaction, recall_probe
- Prüft, dass die Chain-Anzahl plausibel wächst
"""

import os, sys, json, subprocess

BASE = "/opt/ai/oroma"
PY = sys.executable
SIM = f"{BASE}/tools/sim_learn.py"

def run_sim(args, env=None):
    """führt sim_learn.py aus und gibt das JSON-Ergebnis zurück"""
    cmd = [PY, SIM] + args
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=BASE, env=env)
    # letzte JSON-Zeile finden
    out = p.stdout.strip().splitlines()
    jline = None
    for line in reversed(out):
        if line.strip().startswith("{") and line.strip().endswith("}"):
            jline = line.strip()
            break
    assert p.returncode == 0, f"rc={p.returncode}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
    assert jline, f"Keine JSON-Resultline gefunden.\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
    return json.loads(jline)

def test_learning_cycle(tmp_path):
    # isolierte Test-DB (damit Prod-Daten unberührt bleiben)
    test_db = tmp_path / "oroma_test.db"
    env = os.environ.copy()
    env["OROMA_DB_SIM"] = str(test_db)

    # kleiner Lauf
    rep = run_sim(["--day", "500", "--db", str(test_db)], env=env)

    before = rep["counts"]["before"]
    after_day = rep["counts"]["after_day"]
    after_dream = rep["counts"]["after_dream"]

    # Tagmodus sollte SnapChains erzeugen
    assert after_day >= before + 400, f"zu wenig Chains nach Tagmodus: {before} -> {after_day}"
    # Traummodus sollte nichts zerstören
    assert after_dream >= after_day, "Traummodus sollte Chains nicht reduzieren in dieser Simulation"

    # Performance sanity (Pi 5: >= 20 RPS realistisch)
    rps = rep["day_perf"].get("rps")
    assert rps is None or rps > 20, f"RPS sehr niedrig: {rps}"

    # Recall-Check
    if rep.get("recall_probe", {}).get("ok"):
        assert rep["recall_probe"]["num"] >= 0

    # Export-Markierung darf fehlschlagen (optional)
    if rep.get("export_marking", {}).get("ok"):
        assert rep["export_marking"]["marked"] >= 0

    # MetaSnap-Check (nur wenn ENV aktiviert)
    if "meta_probe" in rep:
        mp = rep["meta_probe"]
        assert isinstance(mp.get("ok"), bool)
        if mp.get("ok"):
            assert mp.get("num", 0) >= 0
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/universal_policy/ram_flush.py
# Projekt: ORÓMA
# Modul:   RAM-Flush Runner (tmpfs→RAM→PolicyEngine→DB→Archiv)
# Version: v3.9-rc2
# Stand:   2025-11-10
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# ─────
#  • Lädt finale Episoden-JSONs aus tmpfs (/dev/shm …) in den RAM-Writer.
#  • Trainiert die PolicyEngine direkt aus dem RAM (selector="best" o.ä.).
#  • Promotet nur „gute“ Episoden in die DB (SD-schonend).
#  • Optional triggert Export guter Policy-Regeln ins Regelarchiv.
#  • Verschiebt verarbeitete JSONs nach <ns>/.done/ (Re-Training vermeiden).
#  • Optionales Pruning von alten JSONs (Alter/Anzahl).
#
# Aufruf (Beispiele)
# ─────────────────
#  PYTHONPATH=/opt/ai/oroma \
#  /usr/bin/python3 -u /opt/ai/oroma/mini_programs/universal_policy/ram_flush.py \
#     --namespace '*' --selector best --limit 200 --promote --export --prune
#
# ENV (Zusatzsteuerung)
# ─────────────────────
#  OROMA_RAM_DIR        : Basis-tmpfs (Default /dev/shm/oroma/ram_chains)
#  OROMA_PE_AUTO_EXPORT : "1/true" → Export in regelarchiv nach Flush (Default off)
#  OROMA_PE_MIN_N       : Mindest-n für Export (Default 3)
#  OROMA_PE_MIN_ABS_Q   : Mindest-|q| (Default 0.15)
#  OROMA_ADAPTER        : "universal" | "ttt" | "auto" (Default "auto")
#
# Abhängigkeiten
# ──────────────
#  • core.policy_engine (schon von dir übernommen)
#  • mini_programs.universal_policy.ram_writer (RAM-first Puffer)
#  • mini_programs.universal_policy.adapter_universal (wenn verfügbar)
#  • optional: core.ttt_adapter als Fallback
#
# =============================================================================

from __future__ import annotations
import os, sys, json, time, argparse, shutil
import logging
from core.log_guard import log_suppressed

# PYTHONPATH wird in der systemd-Unit gesetzt; Fallback hier:
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

BASE_DIR = os.environ.get("OROMA_RAM_DIR", "/dev/shm/oroma/ram_chains")

# --- Imports (robust) --------------------------------------------------------
try:
    from core.policy_engine import PolicyEngine
except Exception as e:
    print(f"[ram_flush] FATAL: policy_engine Importfehler: {e}", flush=True)
    sys.exit(2)

# Adapter-Auswahl
def _load_adapter():
    mode = os.environ.get("OROMA_ADAPTER", "auto").strip().lower()
    # 1) Universal bevorzugen
    if mode in ("auto", "universal"):
        try:
            from mini_programs.universal_policy.adapter_universal import UniversalAdapter
            return UniversalAdapter()
        except Exception as e:
            if mode == "universal":
                print(f"[ram_flush] UniversalAdapter nicht verfügbar: {e}", flush=True)
            # sonst weiter zu TTT
    # 2) TTT-Fallback
    if mode in ("auto", "ttt"):
        try:
            from core.ttt_adapter import TTTAdapter
            return TTTAdapter()
        except Exception as e:
            if mode == "ttt":
                print(f"[ram_flush] TTTAdapter nicht verfügbar: {e}", flush=True)
    raise RuntimeError("Kein Adapter verfügbar (Universal/TTT). Installiere einen Adapter.")

# RAM-Writer
try:
    from mini_programs.universal_policy import ram_writer as RW
except Exception as e:
    print(f"[ram_flush] FATAL: ram_writer Importfehler: {e}", flush=True)
    sys.exit(2)

# --- Utilities ----------------------------------------------------------------

def _ns_dir(ns: str) -> str:
    d = os.path.join(BASE_DIR, ns.replace("/", "_"))
    os.makedirs(d, exist_ok=True)
    return d

def _done_dir(ns: str) -> str:
    d = os.path.join(_ns_dir(ns), ".done")
    os.makedirs(d, exist_ok=True)
    return d

def _move_processed_json(ns: str, processed_ids: list[str]) -> int:
    """Verschiebt JSONs der verarbeiteten Episoden in .done/ (falls vorhanden)."""
    moved = 0
    ndir = _ns_dir(ns)
    dd   = _done_dir(ns)
    for eid in processed_ids:
        src = os.path.join(ndir, f"{eid}.json")
        if os.path.exists(src):
            try:
                shutil.move(src, os.path.join(dd, f"{eid}.json"))
                moved += 1
            except Exception as e:
                log_suppressed('mini_programs/universal_policy/ram_flush.py:113', exc=e, level=logging.WARNING)
                pass
    return moved

# --- CLI ----------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="ORÓMA RAM Flush Runner")
    ap.add_argument("--namespace", default="*", help="Namespace oder '*' für alle")
    ap.add_argument("--selector", default="best", help="Episode-Selector: all|best|label:<k>")
    ap.add_argument("--limit", type=int, default=None, help="Max Episoden pro Lauf")
    ap.add_argument("--promote", action="store_true", help="Ausgewählte Episoden in DB promoten")
    ap.add_argument("--export", action="store_true", help="Nach Flush Policy-Export triggern")
    ap.add_argument("--prune", action="store_true", help="tmpfs-JSONs nach Alter/Anzahl aufräumen")
    return ap.parse_args()

# --- MAIN ---------------------------------------------------------------------

def main():
    args = parse_args()
    ns = None if args.namespace in ("", "*") else args.namespace

    RW.init_dirs()

    # Episoden aus tmpfs → RAM holen
    loaded = RW.recover_from_tmpfs(ns)
    print(f"[ram_flush] recovered_from_tmpfs: {loaded} episode(s)", flush=True)

    # Adapter + Engine
    adapter = _load_adapter()
    eng = PolicyEngine(adapter=adapter)

    # Flush aus RAM (train + optional promote + optional export)
    res = RW.flush(
        eng,
        selector=args.selector,
        limit=args.limit,
        promote_to_db=args.promote,
        db_origin=None,
        auto_export=args.export
    )
    print(f"[ram_flush] flush result: {json.dumps(res, separators=(',',':'))}", flush=True)

    # Verarbeitete JSONs nach .done verschieben, damit Timer nicht doppelt trainiert
    processed_ids: list[str] = []
    try:
        # gleiche Auswahl-Logik wie selector="best"
        eps = RW._EP_CACHE.values()  # bewusst intern – hier ok
        if args.selector == "all":
            processed_ids = [e["id"] for e in eps if e.get("closed")]
        elif args.selector.startswith("label:"):
            key = args.selector.split(":",1)[1]
            processed_ids = [e["id"] for e in eps if (e.get("labels") or {}).get(key) and e.get("closed")]
        else:
            # best
            processed_ids = [e["id"] for e in eps if RW._should_keep(e) and e.get("closed")]
    except Exception:
        processed_ids = []

    moved_total = 0
    if processed_ids:
        if ns is None:
            # über alle Namespaces iterieren
            for d in os.listdir(BASE_DIR):
                full = os.path.join(BASE_DIR, d)
                if not os.path.isdir(full): 
                    continue
                # nur die, deren ID mit "<ns>-..." beginnt, matchen
                ids_for_ns = [eid for eid in processed_ids if eid.startswith(d.replace("_", ":")) or eid.startswith(d)]
                moved_total += _move_processed_json(d, ids_for_ns)
        else:
            moved_total += _move_processed_json(ns, processed_ids)

    print(f"[ram_flush] moved_processed_json: {moved_total}", flush=True)

    # Optional: Pruning
    if args.prune:
        pruned = RW.prune_tmpfs(ns)
        print(f"[ram_flush] pruned_tmpfs: {pruned}", flush=True)

    # finaler Status
    st = RW.stats()
    print(f"[ram_flush] stats: {json.dumps(st, separators=(',',':'))}", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
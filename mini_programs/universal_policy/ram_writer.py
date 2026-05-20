#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/mini_programs/universal_policy/ram_writer.py
# Projekt: ORÓMA
# Modul:   RAM-Writer für Episoden/Steps (RAM-first, optional JSON in tmpfs)
# Version: v3.9-rc2
# Stand:   2025-11-10
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#  • Episoden (Chains) und Steps zuerst im RAM puffern (Zero SD-Writes).
#  • Optional zusätzlich als JSON im tmpfs sichern (Crash-Recovery/Debug).
#  • Aus dem RAM direkt die PolicyEngine trainieren (online / mini-batches).
#  • Nur „gute“ Episoden (Label/Quality/Heuristik) in DB promoten.
#  • Optionaler Auto-Export: gute Policy-Regeln ins Regelarchiv schreiben.
#
# VERZEICHNISSE
# ─────────────
#  • RAM (Prozessspeicher): _EP_CACHE (Python-Dict)
#  • tmpfs (optional): OROMA_RAM_DIR oder Default /dev/shm/oroma/ram_chains/<ns>/
#
# ENV (optionale Steuerung)
# ─────────────────────────
#  • OROMA_RAM_DIR           : Pfad zu tmpfs (Default: /dev/shm/oroma/ram_chains)
#  • OROMA_RAM_JSON_MODE     : "none"|"final"|"always"  (Default: "none")
#  • OROMA_RAM_MAX_EPISODES  : max. Episoden im RAM (Default: 5000)
#  • OROMA_RAM_MAX_JSON      : max. JSON-Dateien pro Namespace (Default: 2000)
#  • OROMA_RAM_MAX_AGE_SEC   : JSON-Prune Schwelle (Default: 7*86400)
#  • OROMA_PE_AUTO_EXPORT    : "1"/"true" → nach Flush export_archiv() (Default: off)
#  • OROMA_PE_MIN_N          : Mindest-n für Export (Default: 3)
#  • OROMA_PE_MIN_ABS_Q      : Mindest-|q| für Export (Default: 0.15)
#
# API (wichtige Funktionen)
# ─────────────────────────
#   init_dirs()
#   new_episode(namespace, spec:dict|None=None, labels:dict|None=None) -> ep_id
#   add_step(ep_id, f:list[float], r:float|None=None, outcome:str|None=None,
#            t:int|None=None, labels:dict|None=None) -> None
#   set_result(ep_id, result:int|float|str) -> None             # +1/0/-1 oder "win/lose/draw"
#   close_episode(ep_id) -> dict                                # fertige Chain (im RAM)
#   label_episode(ep_id, **labels) -> None
#   flush(engine, *, selector="all"|"best"|"label:keep", limit=None,
#         promote_to_db=True, db_origin=None, auto_export=True) -> dict
#   promote_to_db(ep:dict, origin:str|None=None) -> int|None
#   recover_from_tmpfs(namespace:str|None=None) -> int          # lädt finale JSONs in RAM
#   prune_tmpfs(namespace:str|None=None) -> int                 # löscht alte JSONs
#   stats() -> dict
#
# HINWEIS ZU POLICY-EXPORT
# ────────────────────────
#  Der Auto-Export wird hier ausgelöst (statt policy_engine.py zu ändern):
#    if OROMA_PE_AUTO_EXPORT: engine.export_archiv(min_n=..., min_abs_q=...)
#
# INTEGRATION
# ───────────
#  from mini_programs.universal_policy import ram_writer as RW
#  from mini_programs.universal_policy.adapter_universal import UniversalAdapter
#  from core.policy_engine import PolicyEngine
#
#  RW.init_dirs()
#  eng = PolicyEngine(adapter=UniversalAdapter())
#  ep = RW.new_episode("game:any", spec={"space":"world2d"})
#  RW.add_step(ep, f=[...], r=0.0)
#  ...
#  RW.set_result(ep, +1)
#  RW.close_episode(ep)
#  res = RW.flush(eng, selector="best", promote_to_db=True, auto_export=True)
#  print(res)
#
# =============================================================================

from __future__ import annotations
import os, json, time, random, threading
from typing import Any, Dict, List, Optional, Tuple
import logging
from core.log_guard import log_suppressed

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # Optional: Promote to DB nur wenn vorhanden

# ----------------------------- Konfiguration ---------------------------------

_BASE_DIR = os.environ.get("OROMA_RAM_DIR", "/dev/shm/oroma/ram_chains")
_JSON_MODE = os.environ.get("OROMA_RAM_JSON_MODE", "none").strip().lower()  # none|final|always
_MAX_RAM_EPISODES = int(os.environ.get("OROMA_RAM_MAX_EPISODES", "5000"))
_MAX_JSON_FILES = int(os.environ.get("OROMA_RAM_MAX_JSON", "2000"))
_MAX_AGE_SEC = int(os.environ.get("OROMA_RAM_MAX_AGE_SEC", str(7 * 86400)))

_PE_AUTO_EXPORT = os.environ.get("OROMA_PE_AUTO_EXPORT", "0").lower() in ("1","true","yes")
_PE_MIN_N = int(os.environ.get("OROMA_PE_MIN_N", "3"))
_PE_MIN_ABS_Q = float(os.environ.get("OROMA_PE_MIN_ABS_Q", "0.15"))

# ------------------------------ RAM-Cache ------------------------------------

# Struktur pro Episode:
#   {
#     "id": str, "namespace": str,
#     "spec": dict|None,
#     "labels": dict,
#     "steps": [ {"f":[...], "r":?, "outcome":?, "t":?, "labels":{}} ... ],
#     "result": +1/0/-1 oder "win"/"lose"/"draw" (optional),
#     "created": ts, "closed": ts|None
#   }
_EP_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.RLock()

# ------------------------------ Utilities ------------------------------------

def init_dirs() -> None:
    """legt Basisverzeichnis an (namespace-Unterordner bei Bedarf on-the-fly)."""
    os.makedirs(_BASE_DIR, exist_ok=True)

def _ns_dir(namespace: str) -> str:
    p = os.path.join(_BASE_DIR, namespace.replace("/", "_"))
    os.makedirs(p, exist_ok=True)
    return p

def _now() -> int:
    return int(time.time())

def _rand4() -> str:
    return "%04x" % random.randint(0, 0xFFFF)

def _episode_path(namespace: str, eid: str) -> str:
    return os.path.join(_ns_dir(namespace), f"{eid}.json")

def _norm_outcome(x: Any) -> int:
    # +1 / 0 / -1 Mapping
    if isinstance(x, (int, float)):
        v = float(x)
        if v > 1e-9: return +1
        if v < -1e-9: return -1
        return 0
    if isinstance(x, str):
        s = x.lower()
        if s in ("win","won","pos","positive","+1","+"): return +1
        if s in ("lose","lost","neg","negative","-1","-"): return -1
        return 0
    return 0

def _should_keep(ep: Dict[str, Any]) -> bool:
    """
    Heuristik: Behalte Episode für DB/Training, wenn:
      • labels.keep == True ODER
      • result == +1 ODER
      • viele Schritte (len>=5) UND letzter Reward > 0
    """
    lab = ep.get("labels") or {}
    if lab.get("keep"): return True
    if _norm_outcome(ep.get("result")) > 0: return True
    steps = ep.get("steps") or []
    if len(steps) >= 5:
        try:
            last = steps[-1]
            r = float(last.get("r") or 0.0)
            return r > 0.0
        except Exception as e:
            log_suppressed('mini_programs/universal_policy/ram_writer.py:161', exc=e, level=logging.WARNING)
            pass
    return False

def _write_json_atomic(path: str, obj: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)

def _json_mode_allows_step() -> bool:
    return _JSON_MODE == "always"

def _json_mode_allows_final() -> bool:
    return _JSON_MODE in ("final", "always")

# ------------------------------ Public API -----------------------------------

def new_episode(namespace: str,
                spec: Optional[Dict[str, Any]] = None,
                labels: Optional[Dict[str, Any]] = None) -> str:
    """
    Legt eine neue Episode im RAM an (und ggf. JSON-header im tmpfs bei 'always').
    Rückgabe: ep_id
    """
    with _LOCK:
        if len(_EP_CACHE) >= _MAX_RAM_EPISODES:
            # einfache RAM-Backpressure: drop älteste offene Episode
            oldest = min(_EP_CACHE.values(), key=lambda e: e.get("created", _now())) if _EP_CACHE else None
            if oldest:
                _EP_CACHE.pop(oldest["id"], None)

        eid = f"{namespace.replace(':','_')}-{_now()}-{os.getpid()}-{_rand4()}"
        ep = {
            "id": eid,
            "namespace": namespace,
            "spec": spec or {},
            "labels": dict(labels or {}),
            "steps": [],
            "result": None,
            "created": _now(),
            "closed": None
        }
        _EP_CACHE[eid] = ep

        if _json_mode_allows_step():
            # initial skeleton persistieren
            _write_json_atomic(_episode_path(namespace, eid), ep)

        return eid

def add_step(ep_id: str,
             f: List[float],
             r: Optional[float] = None,
             outcome: Optional[str] = None,
             t: Optional[int] = None,
             labels: Optional[Dict[str, Any]] = None) -> None:
    """Fügt einen Step hinzu (nur RAM; optional JSON je nach Mode)."""
    with _LOCK:
        ep = _EP_CACHE.get(ep_id)
        if not ep:
            return
        step = {"f": [float(x) for x in (f or [])]}
        if r is not None: step["r"] = float(r)
        if outcome is not None: step["outcome"] = str(outcome)
        if t is not None: step["t"] = int(t)
        if labels: step["labels"] = dict(labels)
        ep["steps"].append(step)

        if _json_mode_allows_step():
            _write_json_atomic(_episode_path(ep["namespace"], ep_id), ep)

def set_result(ep_id: str, result: Any) -> None:
    """Setzt finalen Ergebniswert (+1/0/-1 bzw. 'win/lose/draw')."""
    with _LOCK:
        ep = _EP_CACHE.get(ep_id)
        if not ep:
            return
        ep["result"] = result

        if _json_mode_allows_step():
            _write_json_atomic(_episode_path(ep["namespace"], ep_id), ep)

def label_episode(ep_id: str, **labels) -> None:
    """Ergänzt/aktualisiert Labels der Episode (z. B. keep=True, tag='boss')."""
    with _LOCK:
        ep = _EP_CACHE.get(ep_id)
        if not ep:
            return
        ep["labels"].update(labels or {})
        if _json_mode_allows_step():
            _write_json_atomic(_episode_path(ep["namespace"], ep_id), ep)

def close_episode(ep_id: str) -> Dict[str, Any]:
    """Schließt Episode (timestamp) und schreibt ggf. finale JSON."""
    with _LOCK:
        ep = _EP_CACHE.get(ep_id) or {}
        if not ep:
            return {}
        ep["closed"] = _now()
        if _json_mode_allows_final():
            _write_json_atomic(_episode_path(ep["namespace"], ep_id), ep)
        return dict(ep)

# -------------------------- Training & Promotion -----------------------------

def _to_chain_dict(ep: Dict[str, Any]) -> Dict[str, Any]:
    """
    Macht aus der Episode eine Chain, die der UniversalAdapter versteht.
    Schema:
      { "spec": ..., "steps": [ {"f":[...], "r":?, "outcome":?, "t":?}, ... ],
        "result": <+1/0/-1>, "labels": {...} }
    """
    res = _norm_outcome(ep.get("result"))
    chain = {
        "spec": ep.get("spec") or {},
        "steps": [ {k:v for k,v in s.items() if k in ("f","r","outcome","t","labels")} for s in (ep.get("steps") or []) ],
        "result": res,
        "labels": ep.get("labels") or {},
    }
    return chain

def _select_eps(selector: str) -> List[Dict[str, Any]]:
    sel = (selector or "all").lower()
    eps = list(_EP_CACHE.values())
    if sel == "all":
        return eps
    if sel.startswith("label:"):
        key = sel.split(":",1)[1]
        return [e for e in eps if (e.get("labels") or {}).get(key)]
    if sel == "best":
        return [e for e in eps if _should_keep(e)]
    return eps

def promote_to_db(ep: Dict[str, Any], origin: Optional[str] = None) -> Optional[int]:
    """Schreibt die Episode als SnapChain in DB (nur wenn sql_manager verfügbar)."""
    if not sql_manager:
        return None
    blob = json.dumps(_to_chain_dict(ep), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    try:
        return sql_manager.insert_snapchain({
            "ts": ep.get("created") or _now(),
            "quality": 1.0 if _should_keep(ep) else 0.5,
            "blob": blob,
            "exported": 0,
            "status": "active",
            "origin": origin or ep.get("namespace") or "ram",
            "namespace": ep.get("namespace") or "ram",
            "notes": "ram_flush",
            "version": "v3.9",
            "weight": 1.0,
        })
    except Exception:
        return None

def flush(engine,
          *,
          selector: str = "best",
          limit: Optional[int] = None,
          promote_to_db: bool = True,
          db_origin: Optional[str] = None,
          auto_export: bool = True) -> Dict[str, Any]:
    """
    Trainiert die PolicyEngine aus RAM-Episoden (ausgewählt via selector),
    promotet (optional) Episoden in die DB und triggert (optional) Auto-Export.
    Rückgabe: {"trained_steps":int, "trained_eps":int, "promoted":int, "exported":int}
    """
    eps = _select_eps(selector)
    if limit is not None:
        eps = eps[: int(limit)]
    trained_steps = 0
    trained_eps = 0
    promoted = 0
    exported = 0

    for ep in eps:
        chain = _to_chain_dict(ep)
        try:
            trained_steps += int(engine.ingest_chain(chain))
            trained_eps += 1
        except Exception:
            continue
        if promote_to_db and _should_keep(ep):
            rid = promote_to_db and promote_to_db is True and globals()["promote_to_db"](ep, db_origin)  # type: ignore
            if rid:
                promoted += 1

    # Auto-Export guter Policys (ohne policy_engine.py anfassen zu müssen)
    if auto_export and _PE_AUTO_EXPORT:
        try:
            exported = int(engine.export_archiv(min_n=_PE_MIN_N, min_abs_q=_PE_MIN_ABS_Q))
        except Exception:
            exported = 0

    return {"trained_steps": trained_steps, "trained_eps": trained_eps, "promoted": promoted, "exported": exported}

# --------------------------- Recovery & Pruning ------------------------------

def recover_from_tmpfs(namespace: Optional[str] = None) -> int:
    """
    Lädt finale JSON-Episoden vom tmpfs in den RAM-Cache (wenn JSON_MODE 'final/always').
    Rückgabe: Anzahl geladener Episoden.
    """
    if not _json_mode_allows_final():
        return 0
    base = _BASE_DIR
    if namespace:
        dirs = [_ns_dir(namespace)]
    else:
        os.makedirs(base, exist_ok=True)
        dirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    loaded = 0
    with _LOCK:
        for d in dirs:
            for name in os.listdir(d):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(d, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        ep = json.load(f)
                    if isinstance(ep, dict) and ep.get("id"):
                        _EP_CACHE[ep["id"]] = ep
                        loaded += 1
                except Exception:
                    continue
    return loaded

def prune_tmpfs(namespace: Optional[str] = None) -> int:
    """
    Löscht alte JSONs aus tmpfs: über Alter und über max Dateien pro Namespace.
    Rückgabe: Anzahl gelöschter Dateien.
    """
    base = _BASE_DIR
    dirs = [_ns_dir(namespace)] if namespace else []
    deleted = 0

    if not dirs:
        os.makedirs(base, exist_ok=True)
        dirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]

    now = _now()
    for d in dirs:
        files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json")]
        # 1) nach Alter
        for p in files:
            try:
                st = os.stat(p)
                if now - int(st.st_mtime) > _MAX_AGE_SEC:
                    os.remove(p); deleted += 1
            except Exception:
                continue
        # 2) nach Anzahl
        files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json")]
        if len(files) > _MAX_JSON_FILES:
            files.sort(key=lambda p: os.stat(p).st_mtime)
            for p in files[: max(0, len(files) - _MAX_JSON_FILES) ]:
                try:
                    os.remove(p); deleted += 1
                except Exception as e:
                    log_suppressed('mini_programs/universal_policy/ram_writer.py:421', exc=e, level=logging.WARNING)
                    pass
    return deleted

# ------------------------------- Monitoring ----------------------------------

def stats() -> Dict[str, Any]:
    with _LOCK:
        n = len(_EP_CACHE)
        by_ns: Dict[str, int] = {}
        for ep in _EP_CACHE.values():
            ns = ep.get("namespace","?")
            by_ns[ns] = by_ns.get(ns, 0) + 1
        return {
            "ram_episodes": n,
            "by_namespace": by_ns,
            "json_mode": _JSON_MODE,
            "ram_max": _MAX_RAM_EPISODES,
            "tmpfs_base": _BASE_DIR
        }

# -------------------------------- Selftest -----------------------------------

if __name__ == "__main__":
    init_dirs()
    eid = new_episode("game:any", spec={"space":"world2d"}, labels={"session":"demo"})
    add_step(eid, f=[0.1,0.2], r=0.0)
    add_step(eid, f=[0.2,0.2], r=+0.1)
    set_result(eid, "win")
    close_episode(eid)
    print("stats:", stats())
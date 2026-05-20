#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/missions.py
# Projekt: ORÓMA
# Version: v3.6 (voll)
# Stand:   2025-09-27
#
# Zweck:
#   - Vollwertiges Missions-Subsystem:
#       • Schema + Indizes (idempotent)
#       • Erstellen/Auflisten/Aktualisieren/Abschließen
#       • Robuste Kriterienauswertung (>=, <=, ==, in, all, any)
#       • Progress-Merge mit Inkrementen ("+1", "-2") und Typkonvert
#       • History-Log pro Mission (Events/Zeitstempel)
#       • Priority/Tags/Window/Attempts/last_update
#   - 100% kompatibel zur UI:
#       /missions/api/new, /missions/api/list, /missions/api/update, /missions/api/complete
# =============================================================================

from __future__ import annotations
import json
import time
from typing import Any, Dict, List, Optional, Union
from core.sql_manager import get_conn
import logging
from core.log_guard import log_suppressed
LOG = logging.getLogger("oroma.missions")

JSONMap = Dict[str, Any]

def _now() -> int: return int(time.time())
def _dumps(obj: Any) -> str: return json.dumps(obj if obj is not None else {}, ensure_ascii=False, separators=(",", ":"))
def _loads(s: Optional[str]) -> Any:
    if not s: return {}
    try: return json.loads(s)
    except Exception: return {}

def _as_number(x: Any) -> Optional[float]:
    try:
        if isinstance(x, (int, float)): return float(x)
        if isinstance(x, str): return float(x.strip().replace(",", "."))
        return float(x)
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.1", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

def _merge_progress(base: JSONMap, delta: JSONMap) -> JSONMap:
    out = dict(base or {})
    for k, v in (delta or {}).items():
        if isinstance(v, str) and v.strip().startswith(("+", "-")):
            inc = _as_number(v); cur = _as_number(out.get(k, 0))
            out[k] = (cur if cur is not None else 0.0) + (inc if inc is not None else 0.0)
            if float(out[k]).is_integer(): out[k] = int(out[k])
        else:
            num = _as_number(v)
            out[k] = num if num is not None else v
    return out

def ensure_schema() -> None:
    with get_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS missions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              name TEXT NOT NULL,
              goal TEXT,
              criteria TEXT,
              progress TEXT,
              window TEXT,
              done INTEGER NOT NULL DEFAULT 0,
              priority INTEGER NOT NULL DEFAULT 0,
              tags TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              last_update INTEGER,
              history TEXT
            )
        """)
        try: c.execute("CREATE INDEX IF NOT EXISTS idx_missions_done ON missions(done)")
        except Exception as e:
            log_suppressed(LOG, key="missions.schema.idx_done", msg="missions: ensure_schema failed to create idx_missions_done", exc=e, level=logging.WARNING, interval_s=3600)
        try: c.execute("CREATE INDEX IF NOT EXISTS idx_missions_ts ON missions(ts)")
        except Exception as e:
            log_suppressed(LOG, key="missions.schema.idx_ts", msg="missions: ensure_schema failed to create idx_missions_ts", exc=e, level=logging.WARNING, interval_s=3600)
        try: c.execute("CREATE INDEX IF NOT EXISTS idx_missions_prio ON missions(priority)")
        except Exception as e:
            log_suppressed(LOG, key="missions.schema.idx_prio", msg="missions: ensure_schema failed to create idx_missions_prio", exc=e, level=logging.WARNING, interval_s=3600)

def new_mission(name: str,
                criteria: Optional[JSONMap] = None,
                goal: Optional[str] = None,
                window: Optional[JSONMap] = None,
                priority: int = 0,
                tags: Optional[Union[List[str], str]] = None) -> int:
    ensure_schema()
    ts = _now(); history = [{"type": "create", "ts": ts, "name": name}]
    with get_conn() as c:
        cur = c.execute("""
            INSERT INTO missions (ts, name, goal, criteria, progress, window, done,
                                  priority, tags, attempts, last_update, history)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?, ?)
        """, (ts, name.strip(), goal or "", _dumps(criteria or {}), _dumps({}),
              _dumps(window or {}), int(priority),
              _dumps(tags if isinstance(tags, list) else ([tags] if isinstance(tags, str) else [])),
              ts, _dumps(history)))
        return int(cur.lastrowid)

def _row_to_obj(r: Dict[str, Any]) -> JSONMap:
    return {
        "id": r.get("id"), "ts": r.get("ts"), "name": r.get("name"), "goal": r.get("goal"),
        "criteria": _loads(r.get("criteria")), "progress": _loads(r.get("progress")),
        "window": _loads(r.get("window")), "done": bool(r.get("done")),
        "priority": int(r.get("priority") or 0), "tags": _loads(r.get("tags")),
        "attempts": int(r.get("attempts") or 0), "last_update": r.get("last_update"),
        "history": _loads(r.get("history")),
    }

def get_mission(mid: int) -> Optional[JSONMap]:
    ensure_schema()
    with get_conn() as c:
        r = c.execute("SELECT * FROM missions WHERE id=?", (int(mid),)).fetchone()
        return _row_to_obj(r) if r else None

def delete_mission(mid: int) -> bool:
    ensure_schema()
    try:
        with get_conn() as c:
            c.execute("DELETE FROM missions WHERE id=?", (int(mid),))
            return True
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.2", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

def reset_mission(mid: int, keep_criteria: bool = True) -> bool:
    ensure_schema()
    try:
        with get_conn() as c:
            row = c.execute("SELECT criteria, history FROM missions WHERE id=?", (int(mid),)).fetchone()
            if not row: return False
            crit = row.get("criteria"); hist = _loads(row.get("history"))
            if isinstance(hist, list): hist.append({"type": "reset", "ts": _now()})
            sql = ("UPDATE missions SET progress=?, attempts=0, done=0, last_update=?, history=?, criteria=? WHERE id=?"
                   if not keep_criteria
                   else "UPDATE missions SET progress=?, attempts=0, done=0, last_update=?, history=? WHERE id=?")
            params = (_dumps({}), _now(), _dumps(hist or []))
            if not keep_criteria: params += (_dumps({}), int(mid))
            else: params += (int(mid),)
            c.execute(sql, params)
            return True
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.3", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

def list_missions(active_only: bool = False, limit: Optional[int] = None) -> List[JSONMap]:
    ensure_schema()
    where = "WHERE done=0" if active_only else ""
    lim  = f"LIMIT {int(limit)}" if (limit and limit > 0) else ""
    sql = f"""SELECT id, ts, name, goal, criteria, progress, window, done,
                     priority, tags, attempts, last_update, history
              FROM missions {where}
              ORDER BY done ASC, priority DESC, id DESC
              {lim}"""
    with get_conn() as c:
        rows = c.execute(sql).fetchall()
    out = []
    for r in rows:
        obj = _row_to_obj(r)
        try:
            obj["ready"] = _meets(obj.get("criteria", {}), obj.get("progress", {}))
        except Exception:
            obj["ready"] = False
        out.append(obj)
    return out

def update_progress(mid: int, progress: JSONMap, add_attempt: bool = False, event: Optional[str] = None) -> bool:
    ensure_schema()
    try:
        with get_conn() as c:
            row = c.execute("SELECT progress, attempts, history FROM missions WHERE id=?", (int(mid),)).fetchone()
            if not row: return False
            cur = _loads(row.get("progress")); newp = _merge_progress(cur, progress or {})
            attempts = int(row.get("attempts") or 0) + (1 if add_attempt else 0)
            hist = _loads(row.get("history")); ts = _now()
            if isinstance(hist, list): hist.append({"type": event or "progress", "ts": ts, "delta": progress})
            c.execute("""UPDATE missions SET progress=?, attempts=?, last_update=?, history=? WHERE id=?""",
                      (_dumps(newp), attempts, ts, _dumps(hist or []), int(mid)))
            return True
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.4", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

def update_criteria(mid: int, criteria: JSONMap) -> bool:
    ensure_schema()
    try:
        with get_conn() as c:
            row = c.execute("SELECT history FROM missions WHERE id=?", (int(mid),)).fetchone()
            if not row: return False
            hist = _loads(row.get("history"))
            if isinstance(hist, list): hist.append({"type": "criteria_update", "ts": _now(), "criteria": criteria})
            c.execute("UPDATE missions SET criteria=?, history=? WHERE id=?",
                      (_dumps(criteria or {}), _dumps(hist or []), int(mid)))
            return True
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.5", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

def set_done(mid: int, done: bool = True, event: Optional[str] = None) -> bool:
    ensure_schema()
    try:
        with get_conn() as c:
            row = c.execute("SELECT history FROM missions WHERE id=?", (int(mid),)).fetchone()
            if not row: return False
            hist = _loads(row.get("history"))
            if isinstance(hist, list): hist.append({"type": event or ("done" if done else "reopen"), "ts": _now()})
            c.execute("UPDATE missions SET done=?, history=? WHERE id=?",
                      (1 if done else 0, _dumps(hist or []), int(mid)))
            return True
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.6", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

def _cmp(a: Any, op: str, b: Any) -> bool:
    op = op.strip().lower()
    if op in (">=", "gte"): na, nb = _as_number(a), _as_number(b); return (na is not None and nb is not None and na >= nb)
    if op in (">", "gt"):   na, nb = _as_number(a), _as_number(b); return (na is not None and nb is not None and na > nb)
    if op in ("<=", "lte"): na, nb = _as_number(a), _as_number(b); return (na is not None and nb is not None and na <= nb)
    if op in ("<", "lt"):   na, nb = _as_number(a), _as_number(b); return (na is not None and nb is not None and na < nb)
    if op in ("==", "eq"):  return str(a) == str(b)
    if op in ("!=", "ne"):  return str(a) != str(b)
    if op == "in":
        try: return a in (b if isinstance(b, (list, tuple, set)) else [b])
        except Exception: return False
    if op == "not_in":
        try: return a not in (b if isinstance(b, (list, tuple, set)) else [b])
        except Exception: return False
    if op == "all":
        try:
            aset = set(a if isinstance(a, (list, tuple, set)) else [a])
            bset = set(b if isinstance(b, (list, tuple, set)) else [b])
            return bset.issubset(aset)
        except Exception: return False
    if op == "any":
        try:
            aset = set(a if isinstance(a, (list, tuple, set)) else [a])
            bset = set(b if isinstance(b, (list, tuple, set)) else [b])
            return len(aset.intersection(bset)) > 0
        except Exception: return False
    return False

def _eval_target(got: Any, target: Any) -> bool:
    if isinstance(target, dict):
        for op, val in target.items():
            if not _cmp(got, op, val): return False
        return True
    if isinstance(target, list): return _cmp(got, "in", target)
    num = _as_number(target)
    if num is not None:
        g = _as_number(got); return (g is not None and g >= num)
    return str(got) == str(target)

def _meets(criteria: JSONMap, progress: JSONMap) -> bool:
    if not criteria: return True
    for key, target in criteria.items():
        if key not in progress: return False
        if not _eval_target(progress.get(key), target): return False
    return True

def check_and_complete(mid: int) -> bool:
    ensure_schema()
    try:
        with get_conn() as c:
            row = c.execute("SELECT criteria, progress, done, history FROM missions WHERE id=?", (int(mid),)).fetchone()
            if not row: return False
            if int(row.get("done") or 0) == 1: return True
            crit = _loads(row.get("criteria")); prog = _loads(row.get("progress"))
            ok = _meets(crit or {}, prog or {})
            if ok:
                hist = _loads(row.get("history"))
                if isinstance(hist, list): hist.append({"type": "auto_complete", "ts": _now(), "criteria": crit})
                c.execute("UPDATE missions SET done=1, history=? WHERE id=?", (_dumps(hist or []), int(mid)))
            return ok
    except Exception as e:
        log_suppressed(LOG, key="missions.ret.7", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return False

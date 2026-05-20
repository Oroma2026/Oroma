#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/curriculum.py
# Projekt: ORÓMA v3.7 (Adaptives Curriculum V2)
# Stand:   2025-09-28
#
# Zweck
# ─────
#   Curriculum-Subsystem (Stages + Progress + Advance) mit:
#     • Adaptiver Difficulty (↑/↓ nach rolling Accuracy)
#     • Spaced-Repetition-Queue für Wiederholungen
#     • Reward-Signal beim Stage-Aufstieg
#
# API (genutzt u. a. von curriculum_hook.py / curriculum_ui.py)
# ─────────────────────────────────────────────────────────────
#   ensure_schema()        → DB-Grundlage (curriculum_state) sicherstellen
#   get_state()            → Zustand lesen (stage, progress, window, last_update)
#   current_stage_name()   → Name der aktuellen Stage
#   advance_if_ready(m)    → Progress-Update + optionaler Stage-Aufstieg
#   queue_repeat(item, d)  → Item in Wiederholungs-Queue einplanen
#   pop_repeat(now=None)   → fälliges Wiederholungs-Item holen (oder None)
#
# Datenhaltung
# ────────────
#   Tabelle curriculum_state (wird idempotent von sql_manager.ensure_schema angelegt):
#     id=1, stage:int, progress:JSON{acc,episodes,reward_mean,difficulty}, 
#     window:JSON{repeat_queue:[{item, due:int}, …]}, last_update:int
#
# Hinweise
# ────────
#   • Diese Datei ist bewusst unabhängig von Flask/UI.
#   • Difficulty ist auf [1..10] geklemmt.
#   • Stage-Kriterien werden UND-verknüpft.
# =============================================================================

from __future__ import annotations
import json, time, os
from typing import Any, Dict, Optional, List
from core.sql_manager import get_conn
try:
    from core import db_writer_client as _dbw  # type: ignore
except Exception:
    _dbw = None  # type: ignore
from core import reward
from core.log_guard import log_suppressed
import logging

JSON = Dict[str, Any]
def _now() -> int: return int(time.time())
def _dumps(x: Any) -> str: return json.dumps(x if x is not None else {}, ensure_ascii=False, separators=(",", ":"))
def _loads(s: Optional[str]) -> Any:
    if not s: return {}
    try: return json.loads(s)
    except Exception: return {}

def _safe_int(value: Any, default: int = 0) -> int:
    """Robuster int-Coercer für persistierte Curriculum-States.

    Hintergrund:
      In Live-Systemen können ältere/teilweise beschädigte JSON-Fenster
      due/last_update/stage als None oder leere Strings enthalten. Der bisherige
      direkte int(...)-Zugriff ließ dann curriculum_hook pro Tick top-level
      scheitern und flutete service.err.log.
    """
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return int(default)
            return int(float(s)) if ('.' in s) else int(s)
        return int(value)
    except Exception:
        return int(default)

def _sanitize_repeat_queue(queue: Any) -> List[dict]:
    """Normalisiert die Repeat-Queue defensiv.

    Ungültige Einträge werden nicht verworfen, sondern auf konservative Defaults
    gebracht, damit bereits persistierte Fensterzustände nicht den Hook
    blockieren. Der eigentliche Item-Payload bleibt erhalten.

    Zusätzlich wird ein optionales Gewicht konserviert, damit neuere Hook-Pfade
    (queue_repeat(..., weight=...)) API-kompatibel bleiben, auch wenn ältere
    persistierte States dieses Feld noch nicht enthalten.
    """
    out: List[dict] = []
    for q in (queue or []):
        if not isinstance(q, dict):
            continue
        item = q.get("item") if isinstance(q.get("item"), dict) else q.get("item")
        due = _safe_int(q.get("due"), 0)
        rec = {"item": item, "due": due}
        if "weight" in q:
            try:
                rec["weight"] = float(q.get("weight"))
            except Exception:
                pass
        out.append(rec)
    return out

# ------------------------- Curriculum-Stages ---------------------------------

STAGES = [
    {"id": 1, "name": "Arithmetik – Basis",           "crit": {"acc": {">=": 0.75}, "episodes": {">=": 25}}},
    {"id": 2, "name": "Arithmetik – Fortgeschritten", "crit": {"acc": {">=": 0.82}, "episodes": {">=": 50}}},
    {"id": 3, "name": "Transfer – Muster",            "crit": {"acc": {">=": 0.88}, "episodes": {">=": 100}}},
    {"id": 4, "name": "Selbsttests – Stabilität",     "crit": {"acc": {">=": 0.90}, "episodes": {">=": 150}}},
]

# ------------------------- DB Schema (Initialisierung) -----------------------

def ensure_schema() -> None:
    """
    Sicherheitsnetz für Altsysteme.
    In v3.7 wird curriculum_state primär in core/sql_manager.ensure_schema() angelegt,
    diese Routine bleibt als Fallback idempotent.
    """
    with get_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS curriculum_state (
              id INTEGER PRIMARY KEY CHECK (id=1),
              stage INTEGER NOT NULL DEFAULT 1,
              progress TEXT,      -- JSON {acc, episodes, reward_mean, difficulty}
              window  TEXT,       -- JSON {repeat_queue:[{item, due}]}
              last_update INTEGER
            )
        """)
        row = c.execute("SELECT 1 FROM curriculum_state WHERE id=1").fetchone()
        if not row:
            c.execute("INSERT INTO curriculum_state (id, stage, progress, window, last_update) VALUES (1, 1, ?, ?, ?)",
                      (_dumps({"acc": 0.0, "episodes": 0, "reward_mean": 0.0, "difficulty": 1}),
                       _dumps({"repeat_queue": []}), _now()))

# ------------------------- Helpers -------------------------------------------

def _dbw_enabled() -> bool:
    try:
        return bool(_dbw and getattr(_dbw, "enabled")())
    except Exception:
        return False


def _dbw_timeout_ms(kind: str = "ui") -> int:
    name = "OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM" if str(kind).lower() == "dream" else "OROMA_DBW_CLIENT_TIMEOUT_MS_UI"
    try:
        v = str(os.environ.get(name, "")).strip()
        return int(v) if v else (60000 if str(kind).lower() == "dream" else 2000)
    except Exception:
        return 60000 if str(kind).lower() == "dream" else 2000


def _update_curriculum_state(*, stage: Optional[int] = None, progress: Optional[dict] = None, window: Optional[dict] = None, last_update: Optional[int] = None) -> None:
    """Zentraler Write-Helper für curriculum_state.

    Bei aktivem DBWriter und Strict-Mode darf curriculum.py keine lokalen SQLite-Writes
    mehr ausführen. Die bisherige lokale UPDATE-Logik in queue_repeat/pop_repeat/advance_if_ready
    führte deshalb zu readonly-Fehlern.
    """
    fields = []
    params = []
    if stage is not None:
        fields.append("stage=?")
        params.append(int(stage))
    if progress is not None:
        fields.append("progress=?")
        params.append(_dumps(progress))
    if window is not None:
        fields.append("window=?")
        params.append(_dumps(window))
    if last_update is not None:
        fields.append("last_update=?")
        params.append(_safe_int(last_update, _now()))
    if not fields:
        return
    sql = "UPDATE curriculum_state SET " + ", ".join(fields) + " WHERE id=1"
    if _dbw_enabled():
        if _dbw is None:
            raise RuntimeError("curriculum: DBWriter aktiviert, Client aber nicht verfügbar")
        getattr(_dbw, "exec_write")(
            sql,
            params=tuple(params),
            tag="curriculum.state.update",
            priority="normal",
            timeout_ms=_dbw_timeout_ms("ui"),
            db="oroma",
        )
        return
    with get_conn() as c:
        c.execute(sql, tuple(params))


def _stage_by_id(stage: int) -> Dict[str, Any]:
    for s in STAGES:
        if s["id"] == stage:
            return s
    return {"id": stage, "name": f"Stage {stage}", "crit": {}}

def current_stage_name() -> str:
    st = get_state()
    return _stage_by_id(st["stage"])["name"]

def get_state() -> Dict[str, Any]:
    ensure_schema()
    with get_conn() as c:
        r = c.execute("SELECT stage, progress, window, last_update FROM curriculum_state WHERE id=1").fetchone()
    if not r:
        # ultra-konservativer Fallback
        return {"stage": 1,
                "progress": {"acc": 0.0, "episodes": 0, "reward_mean": 0.0, "difficulty": 1},
                "window": {"repeat_queue": []},
                "last_update": _now()}
    window = _loads(r["window"]) or {}
    if not isinstance(window, dict):
        window = {}
    window["repeat_queue"] = _sanitize_repeat_queue(window.get("repeat_queue"))
    return {
        "stage": _safe_int(r["stage"], 1),
        "progress": _loads(r["progress"]) or {},
        "window": window,
        "last_update": _safe_int(r["last_update"], 0),
    }

def _ema(old: float, new: float, alpha: float = 0.2) -> float:
    try:
        return (1 - alpha) * float(old) + alpha * float(new)
    except Exception:
        return float(new)

def _as_num(x: Any) -> Optional[float]:
    try:
        if isinstance(x, (int, float)): return float(x)
        if isinstance(x, str): return float(x.strip().replace(",", "."))
        return float(x)
    except Exception:
        return None

def _meets(progress: JSON, crit: JSON) -> bool:
    if not crit: return True
    for k, target in (crit or {}).items():
        got = progress.get(k)
        if isinstance(target, dict):
            for op, val in target.items():
                if op == ">=":
                    g = _as_num(got); v = _as_num(val)
                    if g is None or v is None or g < v: return False
                else:
                    # bewusst konservativ: nur >= implementiert
                    return False
        else:
            v = _as_num(target); g = _as_num(got)
            if g is None or v is None or g < v: return False
    return True

# ------------------------- Repetition Queue ----------------------------------

def queue_repeat(item: dict, delay: int = 60, weight: Optional[float] = None) -> None:
    """Item in Wiederholungs-Queue einplanen (fällig in delay Sekunden).

    Hinweis:
      curriculum_hook nutzt im aktuellen Live-Stand teils queue_repeat(..., weight=...).
      Frühere Versionen von curriculum.py kannten dieses Argument nicht und erzeugten
      deshalb TypeError + lokalen readonly-Fallback. Das Gewicht ist hier optional
      und wird konservativ im Queue-Record mitgeführt, ohne ältere Leser zu brechen.
    """
    ensure_schema()
    st = get_state()
    queue: List[dict] = _sanitize_repeat_queue(st["window"].get("repeat_queue", []))
    rec = {"item": item, "due": _now() + _safe_int(delay, 60)}
    if weight is not None:
        try:
            rec["weight"] = float(weight)
        except Exception:
            pass
    queue.append(rec)
    st["window"]["repeat_queue"] = queue
    _update_curriculum_state(window=st["window"], last_update=_now())

def pop_repeat(now: Optional[int] = None) -> Optional[dict]:
    """Fälliges Wiederholungs-Item liefern (oder None)."""
    ensure_schema()
    now = _safe_int(now, _now()) if now is not None else _now()
    st = get_state()
    queue: List[dict] = _sanitize_repeat_queue(st["window"].get("repeat_queue", []))
    if not queue:
        return None
    ready_idx = next((i for i, q in enumerate(queue) if _safe_int(q.get("due"), 0) <= now), None)
    if ready_idx is None:
        return None
    rec = queue.pop(ready_idx)
    st["window"]["repeat_queue"] = queue
    _update_curriculum_state(window=st["window"], last_update=_now())
    return rec.get("item")

# ------------------------- Fortschritt & Advance -----------------------------

def advance_if_ready(metrics: JSON) -> bool:
    """
    metrics z.B.: {"acc": 0.84, "episodes": 10, "reward_mean": 0.12}
      - acc / reward_mean → EMA
      - episodes → kumuliert
      - difficulty → adaptiv:
            acc > 0.80 → +1 (max 10)
            acc < 0.60 → -1 (min 1)
      - Stage-Aufstieg bei erfüllten Kriterien (UND)
    """
    ensure_schema()
    st = get_state()
    prog = dict(st.get("progress") or {})

    # Rolling-Updates
    if "acc" in metrics:
        old = float(prog.get("acc", 0.0))
        prog["acc"] = _ema(old, float(metrics["acc"]))
    if "reward_mean" in metrics:
        old = float(prog.get("reward_mean", 0.0))
        prog["reward_mean"] = _ema(old, float(metrics["reward_mean"]))
    if "episodes" in metrics:
        eps_old = int(prog.get("episodes", 0))
        eps_add = int(_as_num(metrics["episodes"]) or 0)
        prog["episodes"] = eps_old + max(0, eps_add)

    # Adaptives Difficulty
    diff = int(prog.get("difficulty", 1))
    if prog.get("acc", 0.0) > 0.80:
        diff = min(diff + 1, 10)
    elif prog.get("acc", 0.0) < 0.60:
        diff = max(diff - 1, 1)
    prog["difficulty"] = diff

    # Stage-Check
    cur_stage = int(st["stage"])
    cur = _stage_by_id(cur_stage)
    ready = _meets(prog, cur.get("crit", {}))
    advanced = False
    if ready and cur_stage < (STAGES[-1]["id"]):
        cur_stage += 1
        advanced = True
        # kleiner Bonus-Reward für Stage-Aufstieg
        try:
            reward.log("curriculum", value=+0.5, info={"event": "stage_advance", "stage": cur_stage})
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.curriculum.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

    with get_conn() as c:
        c.execute("UPDATE curriculum_state SET stage=?, progress=?, last_update=? WHERE id=1",
                  (cur_stage, _dumps(prog), _now()))
    return advanced
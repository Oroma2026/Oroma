#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/empathy.py
# Projekt: ORÓMA
# Version: v3.7.3+ (Empathy unified → empathy_snaps; legacy empathy_log removed)
# Stand:   2025-12-26
# Autor:   ORÓMA · KI-JWG-X1 (Jörg) + GPT-5.2 Thinking
#
# Zweck
# ─────
#   Dieses Modul stellt einen *kleinen*, robusten Empathie-/Stimmungs-Layer bereit,
#   der in ORÓMA als optionales Signal für UI, Curriculum, Reflex- und Hook-Logik
#   dienen kann – ohne echte "Emotionen" zu behaupten.
#
#   WICHTIG (Patch 2025-12-26):
#   --------------------------
#   • Die frühere Tabelle `empathy_log` wird nicht mehr genutzt und war im Snapshot
#     leer. Das System nutzt produktiv `empathy_snaps(ts, mood, score)` aus
#     core/sql_manager.py (u.a. ASR-UI, Learning-UI, Hooks).
#   • Dieses Modul schreibt daher ab sofort *nur noch* in `empathy_snaps`.
#   • `empathy_log` kann optional per Tool `tools/db_cleanup_drop_empathy_log.py`
#     aus der DB entfernt werden (non-destructive: mit Backup).
#
# Eigenschaften
# ─────────────
#   • Headless, keine GUI-Abhängigkeiten.
#   • Robust: DB-Fehler blockieren den Hauptfluss nicht.
#   • Kompatibilität: bietet weiterhin einfache API (apply_event, fetch_history),
#     plus convenience-Funktionen (manual_event, reward_event).
#
# Datenmodell
# ───────────
#   - Valence:  -1.0 .. +1.0 (negativ → positiv)
#   - Arousal:   0.0 ..  1.0 (ruhig → aktiviert)
#   - Confidence:0.0 ..  1.0 (heuristische Stabilität)
#
# Persistenz
# ──────────
#   - `empathy_snaps` via sql_manager.insert_empathy_snap(ts, mood, score)
#     score: 0.0 .. 1.0 (kompatibel zu UI/Thresholds)
#
# ENV
# ───
#   (optional – defaults sind safe)
#   - OROMA_EMPATHY_ENABLED=true|false   (Default: true)
#   - OROMA_EMPATHY_MIN_GAP_SEC=0..      (Default: 0) Rate-Limit für Inserts
#
# Nutzung
# ──────
#   from core import empathy
#   empathy.reward_event(+0.3, tag="reward")
#   empathy.manual_event(-0.1, tag="gap")
#   st = empathy.get_state()
#   hist = empathy.fetch_history(limit=20)
# =============================================================================

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from core.log_guard import log_suppressed
import logging

try:
    from core import sql_manager
except Exception:  # pragma: no cover
    sql_manager = None  # type: ignore


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, None)
    if v is None:
        return bool(default)
    return str(v).strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, str(default))).strip())
    except Exception:
        return int(default)


_EMPATHY_ENABLED = _env_bool("OROMA_EMPATHY_ENABLED", True)
_MIN_GAP_SEC = max(0, _env_int("OROMA_EMPATHY_MIN_GAP_SEC", 0))

_last_insert_ts: float = 0.0


@dataclass
class EmpathyState:
    """
    Minimaler Zustand. Wird in-memory gehalten (nicht als "Wahrheit" interpretieren).
    """
    valence: float = 0.0
    arousal: float = 0.5
    confidence: float = 0.5

    mood: str = "neutral"
    score: float = 0.5  # 0..1 (abgeleitet aus valence/confidence)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "valence": float(self.valence),
            "arousal": float(self.arousal),
            "confidence": float(self.confidence),
            "mood": str(self.mood),
            "score": float(self.score),
        }


STATE = EmpathyState()


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _derive_score(valence: float, confidence: float, arousal: float) -> float:
    """
    Mappt Valence (-1..1) in Score (0..1) und mischt Confidence leicht dazu.
    Arousal dient hier nur als sehr kleiner Stabilitätsfaktor.
    """
    base = (float(valence) + 1.0) / 2.0  # -1..1 -> 0..1
    mixed = 0.78 * base + 0.18 * float(confidence) + 0.04 * float(arousal)
    return _clip(mixed, 0.0, 1.0)


def _derive_mood(score: float, arousal: float) -> str:
    """
    Einfache, UI-kompatible Mood-Labels (ähnlich zu ui/asr_ui.py).
    """
    s = float(score)
    a = float(arousal)
    if s >= 0.70:
        return "happy" if a < 0.75 else "excited"
    if s <= 0.30:
        return "frustrated" if a < 0.75 else "stressed"
    return "neutral"


def _maybe_insert_snap(ts: int, mood: str, score: float) -> None:
    """
    Schreibt in empathy_snaps, robust und optional rate-limitiert.
    """
    global _last_insert_ts
    if not _EMPATHY_ENABLED:
        return
    if sql_manager is None or not hasattr(sql_manager, "insert_empathy_snap"):
        return

    now = time.time()
    if _MIN_GAP_SEC > 0 and (now - _last_insert_ts) < float(_MIN_GAP_SEC):
        return

    try:
        # ensure_schema() ist idempotent; falls Schema nicht existiert, wird es ergänzt
        if hasattr(sql_manager, "ensure_schema"):
            try:
                sql_manager.ensure_schema()  # type: ignore[attr-defined]
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.empathy.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )

        sql_manager.insert_empathy_snap(int(ts), str(mood), float(score))  # type: ignore[attr-defined]
        _last_insert_ts = now
    except Exception:
        # bewusst still – Empathie darf niemals die Engine blockieren
        return


# -----------------------------------------------------------------------------
# Öffentliche API
# -----------------------------------------------------------------------------

def get_state() -> Dict[str, Any]:
    return STATE.as_dict()


def apply_event(event_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Empathie-Ereignis anwenden und als Snapshot persistieren.

    Rückgabe: {"ok": True, "state": {...}, "delta": (delta_v, delta_a)}
    """
    global STATE
    ts = int(time.time())
    delta_v, delta_a = 0.0, 0.0

    # Kleine, stabile Heuristik (nicht "wahr", nur ein Signal)
    if event_type == "reward":
        delta_v, delta_a = +0.20, +0.10
    elif event_type == "gap":
        delta_v, delta_a = -0.15, +0.10
    elif event_type == "calculator":
        correct = bool((data or {}).get("correct", True))
        delta_v, delta_a = (+0.08, +0.05) if correct else (-0.10, +0.08)
    elif event_type == "game":
        won = bool((data or {}).get("won", True))
        delta_v, delta_a = (+0.12, +0.08) if won else (-0.06, +0.06)
    else:
        delta_v, delta_a = 0.0, 0.0

    # Zustand anpassen (mit Grenzen)
    STATE.valence = _clip(STATE.valence + float(delta_v), -1.0, 1.0)
    STATE.arousal = _clip(STATE.arousal + float(delta_a), 0.0, 1.0)

    # Confidence: kleine, monotone Anpassung
    bump = (abs(float(delta_v)) + abs(float(delta_a))) / 12.0
    STATE.confidence = _clip(STATE.confidence + float(bump), 0.0, 1.0)

    # abgeleitete Felder
    STATE.score = _derive_score(STATE.valence, STATE.confidence, STATE.arousal)
    STATE.mood = _derive_mood(STATE.score, STATE.arousal)

    _maybe_insert_snap(ts, STATE.mood, STATE.score)

    return {"ok": True, "state": STATE.as_dict(), "delta": (float(delta_v), float(delta_a)), "type": str(event_type)}


def manual_event(delta: float, tag: str = "manual") -> Dict[str, Any]:
    """
    Direkte Valence-Anpassung: delta in -1..+1 (geclippt).
    Geeignet für Hooks (z. B. Gap → -0.1, Reward → +0.05).
    """
    d = _clip(float(delta), -1.0, 1.0)
    return apply_event(str(tag), data={"delta": d})


def reward_event(reward: float, tag: str = "reward") -> Dict[str, Any]:
    """
    Reward-Signal in eine kleine, stabile Stimmungskorrektur übersetzen.
    reward: beliebig, wird weich skaliert.
    """
    r = float(reward)
    scaled = _clip(r / 8.0, -0.25, 0.25)
    return apply_event("reward", data={"tag": str(tag), "scaled": scaled})


def fetch_history(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Liefert die letzten Empathie-Snapshots aus empathy_snaps.

    Rückgabeformat (kompatibel zu bisherigen Consumer):
      [{"ts":..., "type": mood, "delta": {"val": score-0.5, "arousal": 0.0}, "score": score}, ...]

    Hinweis:
      `empathy_log` (delta_val/delta_arousal) wurde entfernt. Für UI/Monitoring
      ist `score` die relevante Größe.
    """
    if sql_manager is None:
        return []
    try:
        with sql_manager.get_conn() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                "SELECT ts, mood, score FROM empathy_snaps ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in (rows or []):
            ts = int(r.get("ts", 0))
            mood = str(r.get("mood", "neutral"))
            score = float(r.get("score", 0.5))
            out.append(
                {
                    "ts": ts,
                    "type": mood,
                    "delta": {"val": float(score - 0.5), "arousal": 0.0},
                    "score": score,
                }
            )
        return out
    except Exception:
        return []


# Kein Import-Side-Effect mehr (keine Schema-Anlage für empathy_log).
# Die Schema-Anlage von empathy_snaps läuft zentral über core/sql_manager.ensure_schema().

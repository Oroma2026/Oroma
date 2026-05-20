#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/calc_vision_linker.py
# Projekt: ORÓMA – Crossmodal Linker (Calculator ↔ Vision)
# Version: v3.7.5
# Stand:   2026-04-27
# Autor:   Jörg Werner (public) / ORÓMA Project (internal)
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Minimaler, produktiver Transfer-Baustein:
#   Verknüpft SnapChains aus zwei Modalitäten über Co-Occurrence im Zeitfenster
#   (±Window) und persistiert eine eigene Link-SnapChain:
#
#       calc/result  ↔  vision/token  →  snapchains(origin="link/calc_vision")
#
#   Damit wird Sonnet’s “Transfer-Hub” messbar:
#   - calc/result liefert abstrakte Struktur-Snaps (Zahlen/Patterns/Rewards)
#   - vision/token liefert Wahrnehmungs-Snaps (cam_token v-Vektoren)
#   - link/calc_vision ist die explizite Brücke (IDs + dt_abs + cosine-score)
#
# WICHTIG (Headless/Robust)
# ────────────────────────
#   • Keine DB-Schema-Änderung.
#   • Kein Hintergrund-Thread.
#   • Best-effort: Fehler dürfen AgentLoop NICHT stoppen.
#   • Dedupe: links werden über snapchains.source_id eindeutig gemacht:
#       source_id="link:calc:<calc_id>:vision:<vision_id>"
#
# WARUM HARDENING?
# ────────────────
#   In dunkler Phase entstehen keine neuen vision/token Snaps.
#   Wenn man dann ein riesiges Fenster (z.B. 999999s) nutzt, entstehen zwar Links,
#   aber semantisch wertlose “Stunden-Links”. Deshalb:
#
#   ✅ HARD CAP: dt_abs darf NIE größer sein als STRICT_MAX_DT_SEC (Default: 120s)
#      → verhindert Quatsch-Links auch bei Fehlbedienung.
#
#   ✅ OPTIONAL: "Freshness-Gate" – wenn keine frischen Vision-Tokens in den letzten
#      N Sekunden existieren, wird gar nicht erst gelinkt.
#
# ENV
# ───
#   OROMA_CROSSMODAL_LINKS                  (1|0)        Default: 1
#   OROMA_CROSSMODAL_LINK_EVERY_TICKS       (int)        Default: 10
#   OROMA_CROSSMODAL_LINK_WINDOW_SEC        (int)        Default: 10
#   OROMA_CROSSMODAL_LINK_LIMIT             (int)        Default: 40   (pro Origin)
#   OROMA_CROSSMODAL_LINK_MIN_SCORE         (float)      Default: -1.0
#
#   OROMA_CROSSMODAL_LINK_STRICT_MAX_DT_SEC (int)        Default: 120
#     -> Hard cap gegen Stunden-Links (dt_abs > cap => skip).
#
#   OROMA_CROSSMODAL_LINK_REQUIRE_FRESH_VISION (1|0)     Default: 1
#   OROMA_CROSSMODAL_LINK_FRESH_VISION_SEC     (int)     Default: 300
#     -> Wenn 1: Linker läuft nur, wenn vision/token in den letzten X Sekunden vorkam.

#   OROMA_CROSSMODAL_LINK_VISION_ORIGINS        (csv)     Default: vision/token,scenegraph:vision_token:%
#
#   OROMA_CROSSMODAL_LINK_CALC_ORIGINS          (csv)     Default: calc/result
#     -> Calc ist in ORÓMA je nach Pipeline nicht immer exakt 'calc/result'.
#        Einträge mit '%' werden als SQL LIKE Muster behandelt.
#        Einträge ohne '%' sind exakte origin-Werte.
#        Beispiele:
#          OROMA_CROSSMODAL_LINK_CALC_ORIGINS=calc/result,calc/%

#     -> Vision ist in ORÓMA je nach Pipeline nicht immer exakt "vision/token".
#        Häufig werden Vision-Token als "scenegraph:vision_token:*" persistiert.
#
#        Einträge mit '%' werden als SQL LIKE Muster behandelt.
#        Einträge ohne '%' sind exakte origin-Werte.
#
#        Beispiele:
#          OROMA_CROSSMODAL_LINK_VISION_ORIGINS=vision/token,scenegraph:vision_token:%
#          OROMA_CROSSMODAL_LINK_VISION_ORIGINS=scenegraph:vision_token:%
#
# OUTPUT (SnapChain Blob)
# ──────────────────────
#   {
#     "kind": "link/calc_vision",
#     "ts": <now>,
#     "calc_id": <snapchains.id calc/result>,
#     "vision_id": <snapchains.id vision/token>,
#     "dt_abs": <abs(calc_ts - vision_ts)>,
#     "score": <cosine>,
#     "calc_ts": <calc snap ts>,
#     "vision_ts": <vision snap ts>,
#     "vdim": <len(v)>
#   }
#
# =============================================================================

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core import sql_manager


import logging
from core import log_guard
logger = logging.getLogger(__name__)


# ----------------------------- Logging/Lock helpers ---------------------------

def _is_lock_error(e: BaseException) -> bool:
    """Heuristik: erkennt typische SQLite/Lock-Fehlertexte.

    Wichtig:
      - ORÓMA nutzt zusätzlich File-Locks (writer_lock).
      - Je nach Fehlerpfad kommen Exceptions als sqlite3.OperationalError oder
        generische RuntimeErrors mit Text.

    Diese Heuristik ist bewusst defensiv und wird nur für Log-Level/Retry
    Entscheidungen genutzt.
    """
    try:
        s = str(e).lower()
        return (
            "database is locked" in s
            or "database table is locked" in s
            or "sqlite_busy" in s
            or "busy" in s
            or "locked" in s
            or "flock timeout" in s
        )
    except Exception:
        return False


def _log_supp(key: str, msg: str, exc: BaseException | None = None, level: int = logging.WARNING, interval_s: int = 300) -> None:
    """Rate-limited Logging Wrapper für dieses Modul."""
    try:
        log_guard.log_suppressed(logger, key=key, msg=msg, exc=exc, level=level, interval_s=interval_s)
    except Exception:
        # log_guard darf nie den Linker brechen
        pass
# ----------------------------- ENV helpers -----------------------------------

def _env_bool(key: str, default: bool = True) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    s = str(v).strip().lower()
    return s not in ("0", "false", "no", "off")

def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None:
        return int(default)
    try:
        return int(str(v).strip())
    except Exception:
        return int(default)

def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if v is None:
        return float(default)
    try:
        return float(str(v).strip())
    except Exception:
        return float(default)


_ENABLED = _env_bool("OROMA_CROSSMODAL_LINKS", True)
_EVERY_T = max(1, _env_int("OROMA_CROSSMODAL_LINK_EVERY_TICKS", 10))
_WINDOW_S = max(1, _env_int("OROMA_CROSSMODAL_LINK_WINDOW_SEC", 10))
_LIMIT = max(5, _env_int("OROMA_CROSSMODAL_LINK_LIMIT", 40))
_MIN_SC = _env_float("OROMA_CROSSMODAL_LINK_MIN_SCORE", -1.0)

# HARD CAP gegen “Stunden-Links”
_STRICT_MAX_DT = max(1, _env_int("OROMA_CROSSMODAL_LINK_STRICT_MAX_DT_SEC", 120))

# Optional: nur linken wenn Vision “frisch” ist
_REQ_FRESH_VISION = _env_bool("OROMA_CROSSMODAL_LINK_REQUIRE_FRESH_VISION", True)
_FRESH_VISION_SEC = max(10, _env_int("OROMA_CROSSMODAL_LINK_FRESH_VISION_SEC", 300))


def _parse_csv(s: str) -> List[str]:
    """Kleiner CSV-Parser (ohne csv-Modul), robust gegen Leerwerte."""
    out: List[str] = []
    for part in (s or "").split(","):
        p = (part or "").strip()
        if not p:
            continue
        out.append(p)
    return out


_VISION_ORIGINS: List[str] = _parse_csv(
    os.getenv("OROMA_CROSSMODAL_LINK_VISION_ORIGINS", "vision/token,scenegraph:vision_token:%")
)
if not _VISION_ORIGINS:
    _VISION_ORIGINS = ["vision/token", "scenegraph:vision_token:%"]


_CALC_ORIGINS: List[str] = _parse_csv(
    os.getenv("OROMA_CROSSMODAL_LINK_CALC_ORIGINS", "calc/result")
)
if not _CALC_ORIGINS:
    _CALC_ORIGINS = ["calc/result"]



# ----------------------------- JSON helpers ----------------------------------

def _json_loads_blob(blob: Any) -> Optional[Dict[str, Any]]:
    """Parse SnapChain blob stored either as BLOB(bytes) or TEXT(str).

    ORÓMA historically stores `snapchains.blob` mostly as BLOB, but some producers
    may write compact JSON as TEXT. The linker must accept both to avoid
    false `calc=0/vision=0` due to parse failures.
    """
    try:
        if blob is None:
            return None
        if isinstance(blob, (bytes, bytearray, memoryview)):
            s = bytes(blob).decode("utf-8", errors="replace")
        else:
            s = str(blob)
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ----------------------------- Math ------------------------------------------

def _cosine(a: List[float], b: List[float]) -> float:
    """
    Cosine Similarity ohne numpy (headless/minimal).
    Erwartet gleiche Dimension.
    """
    try:
        if not a or not b or len(a) != len(b):
            return -1.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            fx = float(x)
            fy = float(y)
            dot += fx * fy
            na += fx * fx
            nb += fy * fy
        if na <= 1e-12 or nb <= 1e-12:
            return -1.0
        import math
        return float(dot / (math.sqrt(na) * math.sqrt(nb)))
    except Exception:
        return -1.0


# ----------------------------- DB access -------------------------------------

def _fetch_recent(origin: str, since_ts: int, limit: int) -> List[Tuple[int, int, bytes]]:
    """
    Rückgabe: [(id, ts, blob), ...] newest-first
    """
    try:
        with sql_manager.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, ts, blob FROM snapchains WHERE origin=? AND ts>=? ORDER BY id DESC LIMIT ?",
                (str(origin), int(since_ts), int(limit)),
            ).fetchall()
        out: List[Tuple[int, int, bytes]] = []
        for r in rows:
            if hasattr(r, "keys"):
                out.append((int(r["id"]), int(r["ts"]), r["blob"]))
            else:
                out.append((int(r[0]), int(r[1]), r[2]))
        return out
    except Exception as e:
        _log_supp("calc_vision_linker.fetch_recent", f"fetch_recent failed (origin={origin})", e, level=logging.WARNING, interval_s=300)
        return []


def _fetch_recent_multi(origins: List[str], since_ts: int, limit_total: int) -> List[Tuple[int, int, bytes]]:
    """Fetch für mehrere Origins inkl. LIKE-Wildcards.

    Motivation (produktiv):
      In ORÓMA werden Vision-Tokens je nach Pipeline als 'vision/token' oder
      als 'scenegraph:vision_token:*' persistiert. Der Crossmodal-Linker soll
      dadurch nicht "still" ausfallen.

    Regeln:
      - Einträge mit '%' werden als SQL LIKE behandelt.
      - Einträge ohne '%' sind exakte origin-Werte.

    Rückgabe:
      Liste newest-first (id DESC), insgesamt bis limit_total.
    """
    if not origins:
        return []

    # Fair-Share pro Origin, damit ein starker Stream nicht alles verdrängt.
    per = max(1, int(limit_total) // max(1, len(origins)))
    buf: List[Tuple[int, int, bytes]] = []
    try:
        with sql_manager.get_conn() as conn:
            for o in origins:
                o = (o or "").strip()
                if not o:
                    continue
                if "%" in o:
                    rows = conn.execute(
                        "SELECT id, ts, blob FROM snapchains WHERE origin LIKE ? AND ts>=? ORDER BY id DESC LIMIT ?",
                        (str(o), int(since_ts), int(per)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, ts, blob FROM snapchains WHERE origin=? AND ts>=? ORDER BY id DESC LIMIT ?",
                        (str(o), int(since_ts), int(per)),
                    ).fetchall()

                for r in rows:
                    if hasattr(r, "keys"):
                        buf.append((int(r["id"]), int(r["ts"]), r["blob"]))
                    else:
                        buf.append((int(r[0]), int(r[1]), r[2]))
    except Exception as e:
        _log_supp("calc_vision_linker.fetch_recent_multi", f"fetch_recent_multi failed (origins={origins})", e, level=logging.WARNING, interval_s=300)
        return []

    buf.sort(key=lambda x: x[0], reverse=True)
    if len(buf) > int(limit_total):
        buf = buf[: int(limit_total)]
    return buf

def _fetch_last_ts_multi(origins: List[str]) -> int:
    """Return MAX(ts) across origins (supports LIKE patterns)."""
    if not origins:
        return 0
    try:
        with sql_manager.get_conn() as conn:
            best = 0
            for o in origins:
                o = (o or "").strip()
                if not o:
                    continue
                if "%" in o:
                    row = conn.execute("SELECT MAX(ts) AS m FROM snapchains WHERE origin LIKE ?", (str(o),)).fetchone()
                else:
                    row = conn.execute("SELECT MAX(ts) AS m FROM snapchains WHERE origin=?", (str(o),)).fetchone()
                if not row:
                    continue
                m = row["m"] if hasattr(row, "keys") else row[0]
                try:
                    mi = int(m) if m is not None else 0
                except Exception:
                    mi = 0
                if mi > best:
                    best = mi
            return int(best)
    except Exception as e:
        _log_supp("calc_vision_linker.fetch_last_ts_multi", "fetch_last_ts_multi failed", e, level=logging.WARNING, interval_s=300)
        return 0


def _has_fresh_vision(now: int) -> bool:
    """
    Optionales Gate: nur linken, wenn Vision-Tokens frisch sind.
    """
    if not _REQ_FRESH_VISION:
        return True
    try:
        since = int(now) - int(_FRESH_VISION_SEC)
        with sql_manager.get_conn() as conn:
            for o in _VISION_ORIGINS:
                o = (o or "").strip()
                if not o:
                    continue
                if "%" in o:
                    row = conn.execute(
                        "SELECT 1 FROM snapchains WHERE origin LIKE ? AND ts>=? LIMIT 1",
                        (str(o), int(since)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT 1 FROM snapchains WHERE origin=? AND ts>=? LIMIT 1",
                        (str(o), int(since)),
                    ).fetchone()
                if row:
                    return True
        return False
    except Exception as e:
        _log_supp("calc_vision_linker.fresh_gate", "freshness gate query failed; blocking linking (safe default)", e, level=logging.WARNING, interval_s=300)
        # im Zweifel lieber NICHT linken, damit nichts “komisch” wird
        return False

def _link_exists(source_id: str) -> bool:
    try:
        with sql_manager.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM snapchains WHERE source_id=? LIMIT 1",
                (str(source_id),),
            ).fetchone()
        return bool(row)
    except Exception:
        return False

def _insert_link_snapchain(*,
                           ts: int,
                           calc_id: int,
                           vision_id: int,
                           dt_abs: int,
                           score: float,
                           calc_ts: int,
                           vision_ts: int,
                           vdim: int) -> Optional[int]:
    """
    Persistiert Link-SnapChain. Dedup via source_id.
    """
    source_id = f"link:calc:{int(calc_id)}:vision:{int(vision_id)}"
    if _link_exists(source_id):
        return None

    # Qualität hier = Signalstärke, nicht “Wahrheit”.
    # cosine [-1..1] -> quality ca. [0.15..0.95]
    q = 0.15 + max(0.0, min(0.80, (float(score) + 1.0) / 2.0 * 0.80))

    blob_obj = {
        "kind": "link/calc_vision",
        "ts": int(ts),
        "calc_id": int(calc_id),
        "vision_id": int(vision_id),
        "dt_abs": int(dt_abs),
        "score": float(score),
        "calc_ts": int(calc_ts),
        "vision_ts": int(vision_ts),
        "vdim": int(vdim),
        "strict_max_dt": int(_STRICT_MAX_DT),
        "window_sec": int(_WINDOW_S),
    }
    blob = json.dumps(blob_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # insert_snapchain: je nach Projektstand können optional keys variieren.
    payload: Dict[str, Any] = {
        "ts": int(ts),
        "quality": float(q),
        "blob": blob,
        "exported": 0,
        "status": "active",
        "origin": "link/calc_vision",
        "namespace": "transfer",
        "source_id": source_id,
        "notes": "Crossmodal link: calc/result ↔ vision/token (time-window + cosine)",
        "version": "v3.7.3",
        "weight": 0.10,
    }

    try:
        sid = None
        try:
            sid = sql_manager.insert_snapchain(payload)
        except Exception as e_ins:
            if _is_lock_error(e_ins):
                _log_supp(
                    "calc_vision_linker.insert.retry",
                    f"insert_snapchain lock/timeout – retry once (source_id={source_id})",
                    e_ins,
                    level=logging.WARNING,
                    interval_s=60,
                )
                time.sleep(0.5)
                sid = sql_manager.insert_snapchain(payload)
            else:
                raise
        if sid is None:
            # sql_manager loggt bereits auf stdout; wir ergänzen strukturierte Logs.
            _log_supp(
                "calc_vision_linker.insert.none",
                f"insert_snapchain returned None (source_id={source_id} calc_id={calc_id} vision_id={vision_id})",
                level=logging.WARNING,
                interval_s=120,
            )
        return sid
    except TypeError as e:
        # Fallback: falls schema/insert nur subset akzeptiert
        try:
            payload2 = dict(payload)
            payload2.pop("namespace", None)
            payload2.pop("source_id", None)
            sid = None
            try:
                sid = sql_manager.insert_snapchain(payload2)
            except Exception as e_ins:
                if _is_lock_error(e_ins):
                    _log_supp(
                        "calc_vision_linker.insert.fallback.retry",
                        f"fallback insert lock/timeout – retry once (source_id={source_id})",
                        e_ins,
                        level=logging.WARNING,
                        interval_s=60,
                    )
                    time.sleep(0.5)
                    sid = sql_manager.insert_snapchain(payload2)
                else:
                    raise
            if sid is None:
                _log_supp(
                    "calc_vision_linker.insert.fallback.none",
                    f"fallback insert returned None (source_id={source_id} calc_id={calc_id} vision_id={vision_id})",
                    level=logging.WARNING,
                    interval_s=120,
                )
            return sid
        except Exception as e2:
            lvl = logging.WARNING if _is_lock_error(e2) else logging.ERROR
            _log_supp(
                "calc_vision_linker.insert.fallback.fail",
                f"fallback insert failed (source_id={source_id} calc_id={calc_id} vision_id={vision_id})",
                e2,
                level=lvl,
                interval_s=120,
            )
            return None
    except Exception as e:
        lvl = logging.WARNING if _is_lock_error(e) else logging.ERROR
        _log_supp(
            "calc_vision_linker.insert.fail",
            f"insert_snapchain failed (source_id={source_id} calc_id={calc_id} vision_id={vision_id})",
            e,
            level=lvl,
            interval_s=120,
        )
        return None


# ----------------------------- Core logic ------------------------------------

def link_window_now(window_sec: int = _WINDOW_S) -> int:
    """
    Ein einmaliger Link-Durchlauf.
    Rückgabe: Anzahl neu geschriebener Link-SnapChains.
    """
    if not _ENABLED:
        return 0

    now = int(time.time())

    # Freshness-Gate: in Dunkelphase / ohne Vision kein Link-Spam
    if not _has_fresh_vision(now):
        _log_supp("calc_vision_linker.fresh_gate.block", f"freshness gate blocked linking (no fresh vision within {_FRESH_VISION_SEC}s)", level=logging.INFO, interval_s=600)
        return 0

    # Effektives Fenster: zusätzlich Hard Cap (niemals größer als STRICT_MAX_DT)
    # Damit verhindert man Quatsch-Links auch wenn window_sec hoch gesetzt wird.
    effective_window = int(min(int(window_sec), int(_STRICT_MAX_DT)))
    since = now - effective_window - 2  # minimaler Puffer

    calc_rows = _fetch_recent_multi(_CALC_ORIGINS, since, _LIMIT)
    # Vision ist je nach Pipeline nicht immer exakt "vision/token".
    # Daher Multi-Origin (Default: vision/token + scenegraph:vision_token:%).
    vis_rows = _fetch_recent_multi(_VISION_ORIGINS, since, _LIMIT)

    if not calc_rows or not vis_rows:
        return 0

    # Vision v’s einmal parsen
    vis_parsed: List[Tuple[int, int, List[float]]] = []
    for vid, vts, vblob in vis_rows:
        obj = _json_loads_blob(vblob)
        if not obj:
            continue
        v = obj.get("v")
        if isinstance(v, list) and v:
            vis_parsed.append((int(vid), int(vts), v))

    if not vis_parsed:
        return 0

    created = 0

    for cid, cts, cblob in calc_rows:
        obj = _json_loads_blob(cblob)
        if not obj:
            continue
        # Correct-only Filter: Für Binding nur valide Calc-Resultate verwenden (Noise vermeiden)
        # Erwartet JSON-Feld "correct"==1; optional "error_type" ist None.
        try:
            if int(obj.get("correct", 0)) != 1:
                continue
            if obj.get("error_type", None) not in (None, "null"):
                continue
        except Exception:
            continue
        cv = obj.get("v")
        if not isinstance(cv, list) or not cv:
            continue

        # Best match: minimal dt_abs, dann maximal score
        best: Optional[Tuple[int, int, float, int]] = None  # (vision_id, vision_ts, score, dt_abs)

        for vid, vts, vv in vis_parsed:
            dt_abs = abs(int(cts) - int(vts))
            if dt_abs > effective_window:
                continue

            score = _cosine(cv, vv)

            if best is None:
                best = (vid, vts, float(score), int(dt_abs))
                continue

            # zuerst dt_abs, dann score
            if dt_abs < best[3] or (dt_abs == best[3] and score > best[2]):
                best = (vid, vts, float(score), int(dt_abs))

        if best is None:
            continue

        vid, vts, score, dt_abs = best

        if float(score) < float(_MIN_SC):
            continue

        sid = _insert_link_snapchain(
            ts=now,
            calc_id=int(cid),
            vision_id=int(vid),
            dt_abs=int(dt_abs),
            score=float(score),
            calc_ts=int(cts),
            vision_ts=int(vts),
            vdim=int(len(cv)),
        )
        if sid is not None:
            created += 1

    if created > 0:
        _log_supp("calc_vision_linker.created", f"created {created} link/calc_vision snapchains (window={effective_window}s limit={_LIMIT})", level=logging.INFO, interval_s=60)

    return created



def link_backfill(*, lookback_sec: int = 600, window_sec: int = _WINDOW_S, max_n: int = 5000) -> Dict[str, Any]:
    """
    Backfill-Linking für Orchestrator/Batchbetrieb.

    Hintergrund / Motivation:
      - link_window_now() arbeitet absichtlich in einem sehr engen Zeitfenster (Sekunden).
      - In realen Systemen kann es jedoch vorkommen, dass ein kurzer Batch-Lauf exakt
        in einem „leeren“ Moment landet (oder Inserts wegen Lock/Contention wegfallen).
      - Backfill scannt daher ein Lookback-Fenster (z.B. 10 Minuten) und erzeugt Links
        deduped via source_id. Dadurch ist das Verhalten robust, ohne DB-Spam.

    Rückgabe:
      Dict mit created/matched sowie den Input-Counts (für Logs/UI).
    """
    if not _ENABLED:
        return {"created": 0, "matched": 0, "calc": 0, "vision": 0, "window": int(window_sec), "lookback": int(lookback_sec)}

    now = int(time.time())

    # Backfill-Lookback als "Freshness" berücksichtigen:
    # In Praxis kommt Vision oft bursty (oder durch Energiespar-/Dunkelphase pausiert).
    # Wenn wir im Backfill ohnehin ein größeres Lookback scannen, darf das Freshness-Gate
    # nicht künstlich auf z.B. 600s begrenzen – sonst ist calc/result wieder frisch,
    # aber Vision war vor 20–60 Minuten zuletzt aktiv → Linking fällt fälschlich auf 0.
    lb = max(10, int(lookback_sec))
    fresh_sec = int(max(int(_FRESH_VISION_SEC), min(int(lb), 6 * 3600)))

    if _REQ_FRESH_VISION:
        try:
            since_fresh = int(now) - int(fresh_sec)
            ok_fresh = False
            with sql_manager.get_conn() as conn:
                for o in _VISION_ORIGINS:
                    o = (o or "").strip()
                    if not o:
                        continue
                    if "%" in o:
                        row = conn.execute("SELECT 1 FROM snapchains WHERE origin LIKE ? AND ts>=? LIMIT 1", (str(o), int(since_fresh))).fetchone()
                    else:
                        row = conn.execute("SELECT 1 FROM snapchains WHERE origin=? AND ts>=? LIMIT 1", (str(o), int(since_fresh))).fetchone()
                    if row:
                        ok_fresh = True
                        break
            if not ok_fresh:
                last_vis_ts = _fetch_last_ts_multi(_VISION_ORIGINS)
                _log_supp(
                    "calc_vision_linker.backfill.fresh_gate.block",
                    f"backfill blocked by freshness gate (no fresh vision within {fresh_sec}s). last_vis_ts={last_vis_ts}",
                    level=logging.INFO,
                    interval_s=600,
                )
                return {"created": 0, "matched": 0, "calc": 0, "vision": 0, "window": int(window_sec), "lookback": int(lookback_sec), "last_vis_ts": int(last_vis_ts)}
        except Exception as e:
            _log_supp(
                "calc_vision_linker.backfill.fresh_gate.error",
                "freshness gate query failed; blocking backfill (safe default)",
                e,
                level=logging.WARNING,
                interval_s=300,
            )
            # FAIL-OPEN: continue without freshness gate if the gate itself fails.
            pass

    effective_window = int(min(int(window_sec), int(_STRICT_MAX_DT)))
    # kleines Extra-Padding, damit Matches am Fensterrand nicht verloren gehen
    since = now - lb - effective_window - 2

    lim = max(50, int(max_n))
    calc_rows = _fetch_recent_multi(_CALC_ORIGINS, since, lim)
    vis_rows = _fetch_recent_multi(_VISION_ORIGINS, since, lim)

    if not calc_rows or not vis_rows:
        # Diagnose-Hilfe: Wenn Calc seit längerer Zeit nicht mehr schreibt, sieht man es sofort.
        last_calc_ts = _fetch_last_ts_multi(_CALC_ORIGINS) if not calc_rows else 0
        if not calc_rows:
            _log_supp(
                "calc_vision_linker.backfill.no_calc",
                f"backfill: no calc rows in lookback (lookback={lb}s window={effective_window}s). last_calc_ts={last_calc_ts}",
                level=logging.INFO,
                interval_s=600,
            )
        return {
            "created": 0,
            "matched": 0,
            "calc": int(len(calc_rows) if calc_rows else 0),
            "vision": int(len(vis_rows) if vis_rows else 0),
            "window": int(effective_window),
            "lookback": int(lb),
            "last_calc_ts": int(last_calc_ts),
        }

    # Vision v’s einmal parsen
    vis_parsed: List[Tuple[int, int, List[float]]] = []
    for vid, vts, vblob in vis_rows:
        obj = _json_loads_blob(vblob)
        if not obj:
            continue
        v = obj.get("v")
        if isinstance(v, list) and v:
            vis_parsed.append((int(vid), int(vts), v))

    if not vis_parsed:
        return {"created": 0, "matched": 0, "calc": int(len(calc_rows)), "vision": 0, "window": int(effective_window), "lookback": int(lb)}

    created = 0
    matched = 0

    lb_start = now - lb
    for cid, cts, cblob in calc_rows:
        cts_i = int(cts)
        if cts_i < lb_start:
            # _fetch_recent liefert DESC; sobald wir unter lb_start fallen, können wir abbrechen
            continue

        obj = _json_loads_blob(cblob)
        if not obj:
            continue
        # Correct-only Filter: Für Binding nur valide Calc-Resultate verwenden (Noise vermeiden)
        # Erwartet JSON-Feld "correct"==1; optional "error_type" ist None.
        try:
            if int(obj.get("correct", 0)) != 1:
                continue
            if obj.get("error_type", None) not in (None, "null"):
                continue
        except Exception:
            continue
        cv = obj.get("v")
        if not isinstance(cv, list) or not cv:
            continue

        best: Optional[Tuple[int, int, float, int]] = None  # (vision_id, vision_ts, score, dt_abs)
        for vid, vts, vv in vis_parsed:
            dt_abs = abs(cts_i - int(vts))
            if dt_abs > effective_window:
                continue
            score = _cosine(cv, vv)
            if best is None:
                best = (vid, int(vts), float(score), int(dt_abs))
                continue
            if dt_abs < best[3] or (dt_abs == best[3] and score > best[2]):
                best = (vid, int(vts), float(score), int(dt_abs))

        if best is None:
            continue

        vid, vts_i, score, dt_abs = best
        if float(score) < float(_MIN_SC):
            continue

        matched += 1
        # Link-TS: Zeitpunkt der Kopplung (max der beiden Events) – damit fällt es ins richtige 24h-Fenster.
        link_ts = int(max(cts_i, vts_i))
        sid = _insert_link_snapchain(
            ts=link_ts,
            calc_id=int(cid),
            vision_id=int(vid),
            dt_abs=int(dt_abs),
            score=float(score),
            calc_ts=int(cts_i),
            vision_ts=int(vts_i),
            vdim=int(len(cv)),
        )
        if sid is not None:
            created += 1

    if created > 0:
        _log_supp(
            "calc_vision_linker.backfill.created",
            f"backfill created {created} link/calc_vision snapchains (matched={matched} lookback={lb}s window={effective_window}s limit={lim})",
            level=logging.INFO,
            interval_s=60,
        )

    return {"created": int(created), "matched": int(matched), "calc": int(len(calc_rows)), "vision": int(len(vis_parsed)), "window": int(effective_window), "lookback": int(lb)}

def calc_vision_link_hook(dt: float, tick: int) -> None:
    """
    Hook für AgentLoop. Läuft alle _EVERY_T Ticks.
    """
    try:
        if not _ENABLED:
            return
        if int(tick) % int(_EVERY_T) != 0:
            return
        link_window_now(_WINDOW_S)
    except Exception:
        # Hook darf niemals den Loop brechen
        return

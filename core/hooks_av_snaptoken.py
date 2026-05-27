#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/hooks_av_snaptoken.py
# Projekt:   ORÓMA (Headless · AgentLoop Hooks · Orchestrator-safe)
# Modul:     av_snaptoken_hook – periodische Vision-SnapTokens (cam_token) aus OromaWrapper.embed() + DB-Persist (FastDB optional)
# Version:   v3.7.3+nmr-vision-bridge-v1
# Stand:     2026-05-26
# Autor:     ORÓMA · KI-JWG-X1 (Jörg) + OpenAI GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK / ROLLE IM LIVE-SYSTEM
# ───────────────────────────
# Dieser Hook läuft innerhalb des AgentLoop-Ticks und erzeugt in einem festen
# Sampling-Intervall leichte Vision-„SnapTokens“ aus dem aktuellen Kamera-Frame.
#
# Er ist speziell dafür gebaut, im Orchestrator-Betrieb nicht zu „hängen“:
#   - parallele Writer (DreamWorker, StatsSnapshot, EnergyManager, etc.) können
#     SQLite kurzfristig locken
#   - Standard-sqlite Verhalten wäre „warten“ → AgentLoop friert ein → UI zeigt
#     in_hook=av_snaptoken_hook → Chains/Telemetry fallen ab
#
# Daher gibt es hier zwei Persistenzpfade:
#
#   (A) Legacy (sql_manager.insert_metric / sql_manager.insert_cam_token)
#       - kompatibel, aber kann bei DB-Locks blockieren (busy_timeout des Projekts)
#
#   (B) FastDB (non-blocking, empfohlen wenn Orchestrator parallel schreibt)
#       - schreibt Metrics + SnapChain Insert über eine kurzlebige sqlite3 Connection
#       - mit bewusst kurzem busy_timeout → bei "database is locked" wird SKIPPED,
#         nicht blockiert (best effort)
#
# WICHTIGER REALITÄTS-CHECK (DEFAULTS)
# ───────────────────────────────────
# In der aktuellen Datei ist FastDB standardmäßig NICHT automatisch aktiv:
#   _FASTDB = env_bool("OROMA_AV_FASTDB", default=False)
# → Du musst OROMA_AV_FASTDB=1 setzen, wenn du den Non-Blocking Pfad erzwingen willst.
#
# DATENQUELLE (EMBEDDING)
# ──────────────────────
# Der Hook nutzt:
#   wrappers.oroma_wrapper.OromaWrapper.get_instance().embed(frame=None)
#
# Erwartete Keys im Rückgabedict (tolerant):
#   - embedding oder features : List[float]
#   - motion                  : float|None
#   - edges                   : float|None
#   - color                   : float|None
#   - q                       : float|None  (optional; wenn fehlt, wird heuristisch berechnet)
#
# QUALITÄTSGATE (MIN_Q)
# ─────────────────────
# q wird bevorzugt aus out["q"] genommen. Wenn q <= 0, wird eine schnelle Heuristik genutzt:
#   q ≈ clamp( motion*0.6 + edges*0.4 )
#
# Danach:
#   - wenn q < OROMA_AV_SNAPS_MIN_Q → skip + metrics (cam:token:skip_q, cam:token:skip_quality)
#
# OPTIONAL: MOTION-GATE (MIN_MOTION)
# ──────────────────────────────────
# Für ältere .env kompatibel:
#   OROMA_AV_MIN_MOTION (>0) → skip wenn motion < MIN_MOTION
# Das reduziert redundante Tokens bei „statischer Szene“.
#
# TAGESLIMIT / SAMPLING
# ─────────────────────
# Sampling erfolgt tick-gesteuert:
#   tick % OROMA_AV_SNAPS_EVERY_TICKS == 0
# Zusätzlich wird ein Tageszähler geführt:
#   - pro Tag maximal OROMA_AV_SNAPS_MAX_PER_DAY Inserts
#
# PERSISTENZFORMAT (WICHTIG)
# ─────────────────────────
# Persistiert wird in oroma.db Tabelle snapchains:
#   - origin/source: "vision/token"
#   - namespace:     "vision"
#   - notes:         "cam_token" (Marker)
#   - blob: JSON bytes mit:
#       {
#         "kind":"cam_token",
#         "v":[...],          # embedding/features (float)
#         "motion":float|null,
#         "edges":float|null,
#         "color":float|null
#       }
#
# Zusätzlich (best effort) werden Metrics geschrieben:
#   - cam:token:candidate
#   - cam:token:skip_motion
#   - cam:token:skip_q / cam:token:skip_quality
#   - cam:token:saved / cam:token:accepted
#   - cam:token:db_locked (nur im FastDB Lock-Fall)
#
# NMR-LITE VISION-BRIDGE (v1)
# ──────────────────────────
# Wenn core.nmr_lite verfügbar und OROMA_NMR_VISION_BRIDGE nicht deaktiviert ist,
# meldet dieser Hook pro erzeugtem Vision-Embedding zusätzlich einen kompakten
# 12-dimensionalen Fingerprint an NMR-Lite:
#
#   core.nmr_lite.update_vision_signal(fp12=..., scene_change=..., ts=...)
#
# Dadurch sieht NMR-Lite denselben headless Kamera-/Token-Pfad, der bereits
# cam:token:* Metriken und vision/token SnapChains erzeugt. Die Bridge schreibt
# selbst keine Datenbankzeilen; Persistenz bleibt bei nmr_lite.maybe_persist()
# im AgentLoop. Damit wird der bestehende DBWriter-kompatible NMR-Pfad genutzt
# und der AV-Hook bleibt reaktiv.
#
# ENV:
#   OROMA_NMR_VISION_BRIDGE=1   # default on, wenn NMR-Lite importierbar ist
#
# Signalwahl:
#   - fp12: erste 12 numerische Werte aus embedding/features, auf 0..1 geklemmt
#   - scene_change: bevorzugt explizites scene_change/vision_scene_change; sonst
#                   motion/motion_area als billiger struktureller Change-Proxy
#   - repeat: bewusst None, bis es eine robuste Repeat-/Familiarity-Quelle gibt
#
# EPISODIC INTEGRATION (BEST EFFORT)
# ─────────────────────────────────
# Nach erfolgreichem Insert versucht der Hook:
#   core.episodic_writer.log_vision_cam_token_global(...)
# Diese Zusatzspur ist nicht kritisch – Fehler dort werden suppressed.
#
# FASTDB DETAILS (NON-BLOCKING)
# ────────────────────────────
# FastDB nutzt _fast_conn():
#   - sqlite3.connect(db_path, timeout=OROMA_AV_DB_TIMEOUT_SEC, check_same_thread=False)
#   - PRAGMA busy_timeout=OROMA_AV_DB_BUSY_TIMEOUT_MS
#   - optional: setzt WAL/SYNCHRONOUS best effort, wenn OROMA_DB_WAL=1 (Default True im Codepfad)
#
# Bei sqlite3.OperationalError „database is locked“:
#   - es wird NICHT gewartet
#   - es wird rate-limited gewarnt (OROMA_AV_FASTDB_WARN_EVERY_TICKS)
#   - Hook macht in diesem Tick einfach nichts (System bleibt reaktiv)
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
# Aktivierung (Registrierung des Hooks geschieht extern, i. d. R. via agent_loop):
#   OROMA_AV_SNAPS=1|true|yes
#
# Sampling & Limits:
#   OROMA_AV_SNAPS_EVERY_TICKS=20
#   OROMA_AV_SNAPS_MIN_Q=0.10
#   OROMA_AV_SNAPS_MAX_PER_DAY=20000
#   OROMA_AV_MIN_MOTION=0.0                  # Legacy kompatibel
#
# FastDB (empfohlen im Orchestrator-Betrieb):
#   OROMA_AV_FASTDB=1
#   OROMA_AV_DB_TIMEOUT_SEC=1.50
#   OROMA_AV_DB_BUSY_TIMEOUT_MS=2000
#   OROMA_AV_FASTDB_WARN_EVERY_TICKS=200
#   OROMA_DB_WAL=1                            # best effort in FastDB Connection
#
# Logging:
#   OROMA_HOOKS_LOG=INFO|DEBUG|WARNING|ERROR
#
# ÖFFENTLICHE API (HOOK-VERTRAG)
# ─────────────────────────────
# av_snaptoken_hook(dt: float, tick: int) -> None
#   - wird vom AgentLoop mit dt/tick aufgerufen
#   - darf niemals hart crashen oder langfristig blockieren
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Hook muss unter DB-Locks best effort arbeiten (kein „Warten“ im Tick).
# - Keine harten Abhängigkeiten auf Kamera-GUI; embed(frame=None) muss headless bleiben.
# - Insert-Schema ist an bestehende snapchains-Spalten gekoppelt; bei Schema-Drift
#   wird rate-limited gewarnt, nicht gespammt.
# - NMR-Lite Vision-Bridge darf den AV-Hook niemals blockieren; Fehler werden
#   rate-limited sichtbar geloggt und nicht weitergereicht.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import logging
from core.log_guard import log_suppressed
import os
import sqlite3
import time
from typing import Any, Dict, Optional

from wrappers.oroma_wrapper import OromaWrapper

# Optional: sql_manager (DB-Pfad + episodic logging)
try:
    from core import sql_manager
    _HAS_SQL = True
except Exception:
    sql_manager = None  # type: ignore
    _HAS_SQL = False
# Optional: DBWriter IPC (Stufe C)
try:
    from core import db_writer_client
    _HAS_DBW = True
except Exception:
    db_writer_client = None  # type: ignore
    _HAS_DBW = False

# Optional: NMR-Lite Vision-Bridge (keine harte Abhängigkeit)
try:
    from core.nmr_lite import update_vision_signal as _nmr_update_vision_signal
    from core.nmr_lite import increment_snap_counter as _nmr_increment_snap_counter
    _HAS_NMR_LITE = True
except Exception:
    _nmr_update_vision_signal = None  # type: ignore
    _nmr_increment_snap_counter = None  # type: ignore
    _HAS_NMR_LITE = False


logger = logging.getLogger("oroma.hooks.av_snaptoken")
logger.setLevel(getattr(logging, os.getenv("OROMA_HOOKS_LOG", "INFO").upper(), logging.INFO))


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, None)
    if v is None:
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


_EVERY = max(1, int(os.getenv("OROMA_AV_SNAPS_EVERY_TICKS", "20")))

# Abwärtskompatibilität: ältere .env verwendete OROMA_AV_MIN_MOTION / OROMA_AV_MIN_QUALITY
_MIN_MV = float(os.getenv('OROMA_AV_MIN_MOTION', '0.0'))
_MIN_Q = float(os.getenv('OROMA_AV_SNAPS_MIN_Q', os.getenv('OROMA_AV_MIN_QUALITY', '0.10')))
_MAX_PER_DAY = int(os.getenv("OROMA_AV_SNAPS_MAX_PER_DAY", "20000"))

_FASTDB = _env_bool("OROMA_AV_FASTDB", False)
_FASTDB_TIMEOUT_SEC = float(os.getenv('OROMA_AV_DB_TIMEOUT_SEC', '1.50'))
_FASTDB_BUSY_MS = int(os.getenv('OROMA_AV_DB_BUSY_TIMEOUT_MS', '2000'))
_WARN_EVERY_TICKS = max(1, int(os.getenv("OROMA_AV_FASTDB_WARN_EVERY_TICKS", "200")))
_NMR_VISION_BRIDGE = _env_bool("OROMA_NMR_VISION_BRIDGE", True)


_last_day = None
_count_day = 0
_last_warn_tick = -10**9


def _is_locked_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return ("database is locked" in msg) or ("database is busy" in msg) or ("sqlite_busy" in msg)


def _fast_conn() -> sqlite3.Connection:
    """Kurzlebige Connection mit kurzem busy_timeout – für AgentLoop Hooks."""
    if not _HAS_SQL or not hasattr(sql_manager, "get_db_path"):
        # Fallback (sollte praktisch nie passieren)
        db_path = os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")
    else:
        db_path = sql_manager.get_db_path()  # type: ignore[union-attr]

    conn = sqlite3.connect(db_path, timeout=float(_FASTDB_TIMEOUT_SEC), check_same_thread=False)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(_FASTDB_BUSY_MS)}")
    except Exception as e:
        log_suppressed(logger, key="core_hooks_av_snaptoken.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    # WAL ist im Projekt inzwischen Default (siehe sql_manager.get_conn); hier best effort.
    try:
        if _env_bool("OROMA_DB_WAL", True):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        log_suppressed(logger, key="core_hooks_av_snaptoken.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
    return conn


def _metric_best_effort(key: str, value: float, tick: int) -> None:
    """Metrik-Insert ohne AgentLoop zu blockieren."""
    # DBWriter (Stufe C): bevorzugt, wenn aktiv
    if _dbw_enabled():
        _dbw_exec_best_effort(
            "INSERT INTO metrics(key, ts, value) VALUES(?,?,?)",
            [str(key), int(time.time()), float(value)],
            tag=f"hooks.av_snaptoken.metric:{key}",
            tick=tick,
            expect="rowcount",
        )
        return
    if not _FASTDB:
        if _HAS_SQL:
            try:
                sql_manager.insert_metric(str(key), float(value))  # type: ignore[union-attr]
            except Exception as e:
                log_suppressed(logger, key="core_hooks_av_snaptoken.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        return

    try:
        with _fast_conn() as conn:
            conn.execute(
                "INSERT INTO metrics(key, ts, value) VALUES(?,?,?)",
                (str(key), int(time.time()), float(value)),
            )
            conn.commit()
    except sqlite3.OperationalError as e:
        # Lock: ignorieren (kein Blocken)
        if _is_locked_error(e):
            _warn_lock_once(tick, where=f"metric:{key}")
            return
    except Exception:
        return


def _warn_lock_once(tick: int, where: str) -> None:
    global _last_warn_tick
    if tick - _last_warn_tick >= _WARN_EVERY_TICKS:
        logger.warning(
            "FastDB: DB-Lock → skip (where=%s, busy_timeout_ms=%s)",
            where,
            _FASTDB_BUSY_MS,
        )
        _last_warn_tick = tick

def _warn_once(tick: int, fmt: str, *args: Any) -> None:
    """Allgemeines Rate-Limit für Warnungen dieses Hooks (vermeidet Log-Spam)."""
    global _last_warn_tick
    if tick - _last_warn_tick >= _WARN_EVERY_TICKS:
        try:
            logger.warning(fmt, *args)
        except Exception:
            logger.warning(str(fmt))
        _last_warn_tick = tick



def _coerce_float_list(value: Any) -> list[float]:
    """Return a robust numeric list for embedding/features values.

    Some wrappers return a plain list/tuple, others may return numpy arrays. A
    dict-shaped feature payload is intentionally ignored here because its key
    order is not a stable compact fingerprint.
    """
    if value is None or isinstance(value, dict):
        return []
    try:
        if hasattr(value, "tolist"):
            value = value.tolist()
    except Exception:
        pass
    try:
        return [float(x) for x in list(value)]
    except Exception:
        return []


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v
    except Exception:
        return float(default)


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _nmr_bridge_vision_best_effort(
    *,
    vec: Any,
    out: Dict[str, Any],
    motion: Optional[float],
    q: Optional[float],
    tick: int,
    ts: int,
) -> None:
    """Feed the compact camera-token signal into NMR-Lite without blocking.

    The bridge is deliberately placed in the existing AV SnapToken hook because
    this hook already proves that a headless camera embedding exists. It does
    not perform DB writes; it only updates NMR-Lite's in-memory vision bridge.
    """
    if not _NMR_VISION_BRIDGE or not _HAS_NMR_LITE or _nmr_update_vision_signal is None:
        return

    fp = _coerce_float_list(vec)
    if not fp:
        return

    # NMR-Lite expects an already compact fp12. Values are clipped rather than
    # normalized across the vector, preserving the existing ORÓMA wrapper scale.
    fp12 = [_clip01(x) for x in fp[:12]]

    scene_change = _first_float(
        out.get("scene_change"),
        out.get("vision_scene_change"),
        out.get("motion_change"),
        motion,
    )
    if scene_change is not None:
        scene_change = _clip01(scene_change)

    try:
        _nmr_update_vision_signal(fp12=fp12, scene_change=scene_change, repeat=None, ts=float(ts))
    except Exception as e:
        log_suppressed(
            logger,
            key="core_hooks_av_snaptoken.nmr_vision_bridge",
            msg="NMR-Lite Vision-Bridge Fehler",
            exc=e,
            level=logging.WARNING,
            interval_s=300,
        )


def _nmr_increment_snap_counter_best_effort(tick: int) -> None:
    """Inform NMR-Lite about accepted vision tokens without coupling to DB state."""
    if not _NMR_VISION_BRIDGE or not _HAS_NMR_LITE or _nmr_increment_snap_counter is None:
        return
    try:
        _nmr_increment_snap_counter(1)
    except Exception as e:
        log_suppressed(
            logger,
            key="core_hooks_av_snaptoken.nmr_snap_counter",
            msg="NMR-Lite Snap-Counter Bridge Fehler",
            exc=e,
            level=logging.WARNING,
            interval_s=300,
        )



def _dbw_enabled() -> bool:
    if not _HAS_DBW:
        return False
    if not _env_bool("OROMA_DBW_ENABLE", False):
        return False
    return True

def _dbw_exec_best_effort(sql: str, params: list[Any], *, tag: str, tick: int, expect: str = "rowcount") -> Optional[int]:
    """Best-effort Write über DBWriter. Gibt optional lastrowid zurück."""
    if not _dbw_enabled():
        return None
    try:
        to_ms = int(os.getenv("OROMA_AV_DBW_TIMEOUT_MS", "500"))
        prio = os.getenv("OROMA_AV_DBW_PRIORITY", "low")
        res = db_writer_client.exec(sql, params=params, tag=tag, priority=prio, timeout_ms=to_ms, expect=expect)  # type: ignore[union-attr]
        if expect == "lastrowid":
            return int(res or 0)
        return None
    except Exception as e:
        _warn_once(tick, "DBW: write failed → fallback (tag=%s): %s", tag, e)
        return None

def _insert_cam_token_fast(
    *,
    ts: int,
    q: float,
    vec: list[float],
    motion: Optional[float],
    edges: Optional[float],
    color: Optional[float],
    tick: int,
    source: str = "vision/token",
) -> Optional[int]:
    """SnapToken Insert ohne lange DB-Blockade (FastDB)."""
    payload = {
        "kind": "cam_token",
        "v": [float(x) for x in (vec or [])],
        "motion": None if motion is None else float(motion),
        "edges": None if edges is None else float(edges),
        "color": None if color is None else float(color),
    }
    blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # DBWriter (Stufe C): bevorzugt (kein sqlite3.connect im Hook-Pfad)
    if _dbw_enabled():
        snap_id = _dbw_exec_best_effort(
            """
            INSERT INTO snapchains(
                ts, quality, blob, exported, status, origin, gap_flag,
                notes, namespace, source_id, version, weight
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                int(ts),
                float(q),
                blob,
                0,
                "active",
                str(source),
                0,
                "cam_token",
                "vision",
                None,
                "v3.8",
                1.0,
            ],
            tag="hooks.av_snaptoken.insert_snapchain",
            tick=tick,
            expect="lastrowid",
        )
        if snap_id:
            return int(snap_id)
        # wenn DBW fehlschlägt, fällt der Code unten auf FastDB sqlite zurück

    try:
        with _fast_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapchains(
                    ts, quality, blob, exported, status, origin, gap_flag,
                    notes, namespace, source_id, version, weight
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(ts),
                    float(q),
                    sqlite3.Binary(blob),
                    0,
                    "active",
                    str(source),
                    0,
                    "cam_token",
                    "vision",
                    None,
                    "v3.8",
                    1.0,
                ),
            )
            conn.commit()
            snap_id = int(cur.lastrowid)
    except sqlite3.OperationalError as e:
        if _is_locked_error(e):
            _metric_best_effort("cam:token:db_locked", 1.0, tick)
            _warn_lock_once(tick, where="insert_snapchain")
            return None
        # Nicht-Lock OperationalError (z.B. Schema-Drift / constraint failed / no such column)
        # → Rate-limited warn, damit die Ursache im service.err.log sichtbar ist.
        _warn_once(tick, "FastDB: OperationalError bei cam_token insert: %s", e)
        return None
    except Exception as e:
        log_suppressed(logger, key="core_hooks_av_snaptoken.ret.4", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
        return None

    # Episoden-Logging (nicht kritisch – Fehler hier nie weiterreichen)
    if _HAS_SQL:
        try:
            from core import episodic_writer  # lazy import, um Zyklen zu vermeiden
            episodic_writer.log_vision_cam_token_global(
                ts=int(ts),
                snap_id=int(snap_id),
                q=float(q),
                origin=str(source),
                motion=motion,
                edges=edges,
                color=color,
                dim=len(vec or []),
            )
        except Exception as e:
            log_suppressed(logger, key="core_hooks_av_snaptoken.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    return snap_id


def av_snaptoken_hook(dt: float, tick: int) -> None:
    """Kamera-SnapToken Sampling Hook für AgentLoop."""
    global _last_day, _count_day

    # Skip: Tick-Teiler
    if tick % _EVERY != 0:
        return

    # Tageslimit
    today = time.strftime("%Y-%m-%d")
    if _last_day != today:
        _last_day = today
        _count_day = 0
    if _count_day >= _MAX_PER_DAY:
        return

    ow = OromaWrapper.get_instance()

    # Candidate metric (non-blocking)
    _metric_best_effort("cam:token:candidate", 1.0, tick)

    # Embedding erzeugen
    try:
        out: Dict[str, Any] = ow.embed(frame=None) or {}
    except Exception as e:
        logger.warning("av_snaptoken_hook: embed() Fehler: %s", e)
        return

    vec = out.get("embedding") or out.get("features") or []

    # Kanonische Primitive zuerst lesen. Falls der Producer/Wrapper noch
    # Legacy-/Alias-Namen liefert, diese best effort auf die kanonischen
    # ORÓMA-Lernketten-Namen motion/edges/color abbilden, damit
    # q-Berechnung, VisionArbiter und DB-Persistenz dieselben Werte sehen.
    motion = out.get("motion")
    if motion is None:
        motion = out.get("motion_area")
    if motion is None:
        try:
            motion = (out.get("features") or {}).get("motion_norm")
        except Exception:
            motion = None

    edges = out.get("edges")
    if edges is None:
        edges = out.get("edge_mean")
    if edges is None:
        try:
            edges = (out.get("features") or {}).get("edges_ratio")
        except Exception:
            edges = None

    color = out.get("color")
    if color is None:
        color = out.get("colorfulness")
    if color is None:
        try:
            color = (out.get("features") or {}).get("colorfulness")
        except Exception:
            color = None

    # Optional: Motion-Gate (ältere .env: OROMA_AV_MIN_MOTION).
    # Wenn gesetzt (>0), wird bei zu wenig Bewegung früh beendet (reduziert redundante Tokens).
    try:
        if _MIN_MV and float(motion or 0.0) < float(_MIN_MV):
            _metric_best_effort('cam:token:skip_motion', 1.0, tick)
            return
    except Exception as e:
        log_suppressed(logger, key="core_hooks_av_snaptoken.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)


    # Qualität berechnen (Motion+Edges sind gute, schnelle Proxy-Signale)
    try:
        q = float(out.get("q") or 0.0)
        if q <= 0.0:
            mm = float(motion or 0.0)
            ee = float(edges or 0.0)
            # simple heuristic – schnell & stabil
            q = max(0.0, min(1.0, (mm * 0.6 + ee * 0.4)))
    except Exception:
        q = 0.0

    # ---------------------------------------------------------------------
    # Robustness-Fix (v3.7.3+): "Unknown quality" darf nicht alles blockieren
    #
    # In einigen Headless/External-Frame-Setups liefert OromaWrapper.embed()
    # zwar ein Embedding, aber keine sinnvollen motion/edges/q Signale
    # (z.B. wenn der Frame aus dem VisionWrapper-Stream kommt).
    #
    # Das alte Verhalten (q==0 → immer skip_quality) führt dann dazu, dass
    # *keine* vision/token SnapChains mehr entstehen – obwohl die Kamera läuft.
    #
    # Minimal-invasiver Fix:
    #   - Wenn ein Vektor existiert, aber q/motion/edges fehlen, behandeln wir
    #     die Qualität als "unknown" und setzen q genau auf _MIN_Q.
    #   - Damit bleibt das bestehende MIN_Q-Gate semantisch erhalten, aber
    #     die Pipeline bricht nicht komplett ab.
    #
    # Hinweis: Das Sampling/MaxPerDay bleibt weiterhin die Hauptbremse gegen
    #          zu viele Tokens bei statischen Szenen.
    # ---------------------------------------------------------------------
    try:
        if (q <= 0.0) and vec and (out.get("q") is None) and (motion is None) and (edges is None):
            q = float(_MIN_Q)
    except Exception as e:
        log_suppressed(logger, key="core_hooks_av_snaptoken.pass.6b", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)

    # NMR-Lite Vision-Bridge: vorhandenen Kamera-Fingerprint früh in-memory
    # melden, unabhängig davon ob der spätere SnapToken-DB-Insert gelingt.
    # Dadurch kann NMR-Lite PE/EMA aus dem visuellen Kanal berechnen, ohne den
    # AV-Hook an zusätzliche DB-Schreibpfade zu koppeln.
    _nmr_bridge_vision_best_effort(
        vec=vec,
        out=out,
        motion=(None if motion is None else float(motion)),
        q=float(q),
        tick=tick,
        ts=int(time.time()),
    )

    # Debug/Telemetry: geschätzte/gelieferte Qualität (best effort)
    _metric_best_effort("cam:token:q", float(q), tick)

    if q < _MIN_Q:
        _metric_best_effort("cam:token:skip_q", 1.0, tick)
        _metric_best_effort("cam:token:skip_quality", 1.0, tick)
        return

    if not vec:
        # Kein Vektor → nichts persistieren
        return

    ts = int(time.time())

    # Persist
    if _FASTDB:
        sid = _insert_cam_token_fast(
            ts=ts,
            q=q,
            vec=[float(x) for x in vec],
            motion=(None if motion is None else float(motion)),
            edges=(None if edges is None else float(edges)),
            color=(None if color is None else float(color)),
            tick=tick,
            source="vision/token",
        )
    else:
        # Legacy/Kompatibilität: kann bei DB-Locks bis zu OROMA_DB_BUSY_TIMEOUT_MS blockieren.
        sid = None
        if _HAS_SQL:
            try:
                sid = sql_manager.insert_cam_token(  # type: ignore[union-attr]
                    ts=ts,
                    q=float(q),
                    vec=[float(x) for x in vec],
                    motion=(None if motion is None else float(motion)),
                    edges=(None if edges is None else float(edges)),
                    color=(None if color is None else float(color)),
                    source="vision/token",
                )
            except Exception:
                sid = None

    if sid is not None:
        _count_day += 1
        _nmr_increment_snap_counter_best_effort(tick)
        _metric_best_effort("cam:token:saved", 1.0, tick)
        _metric_best_effort("cam:token:accepted", 1.0, tick)
    else:
        # Persist schlug fehl (z.B. DB-Lock/OperationalError/anderer Fehler).
        # Wir loggen rate-limited, damit man Ursache & Häufigkeit live erkennt.
        _warn_once(tick, "cam_token: persist fehlgeschlagen (fastdb=%s, q=%.3f, vec_dim=%d)", _FASTDB, float(q), int(len(vec or [])))

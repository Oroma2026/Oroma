#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_coverage_daily_runner.py
# Projekt:   ORÓMA (PTZ Coverage · Staubsauger-Sweep · Daily Runner)
# Version:   v3.7.6
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
#   Dieses Tool führt ein "PTZ Coverage" Spiel im Daily-Betrieb aus.
#   Ziel ist ein robuster, hardware-naher "Staubsauger-Sweep" über das PTZ-
#   Blickfeld, um systematisch verschiedene Blickwinkel/Zellen zu besuchen.
#
#   Wichtige Designanforderungen aus dem Projekt:
#     - Headless (kein Qt/Wayland/X11)
#     - Orchestrator-safe (kurze DB-Transaktionen, WAL/busy_timeout via sql_manager)
#     - Non-destructive
#     - Dauerbetrieb: Coverage-Status soll über Tage/Wochen stabil sein
#
# PERSISTENZ (KRITISCH, DAUERBETRIEB)
# ──────────────────────────────────
#   Für den Dauerbetrieb wird der Coverage-Zustand in stats.db persistiert,
#   damit der Sweep nicht bei jedem Run "bei Null" anfängt.
#
#   Tabelle (stats.db): ptz_coverage_cells
#     - namespace TEXT
#     - cell_id  TEXT        (quantisierte PTZ-Position: ix:iy:z)
#     - last_seen_ts INTEGER
#     - seen_count INTEGER
#     - best_motion REAL
#     - best_strength REAL
#     - best_sharp REAL
#     - best_ts INTEGER
#     - PRIMARY KEY(namespace, cell_id)
#
#   WICHTIG (User-Regel): Jede DB-Connection wird zuverlässig geschlossen.
#   Wir nutzen core.sql_manager.get_conn(db_path=...) als Context-Manager,
#   der via _ClosingConnection das Close() sicherstellt.
#
# EPISODEN-SCHREIBEN (oroma.db)
# ────────────────────────────
#   episodes.kind:
#     - game:ptz_coverage:policy_batch
#     - game:ptz_coverage:explore_batch
#   episodic_metrics:
#     - duration_ms, games, steps, moves, holds
#     - coverage_unique_cells, coverage_rate
#     - revisits, avg_reward
#     - snap_ok/snap_fail, decode_ok/decode_fail
#     - motion_norm_mean/p95, sharp_mean/p95, strength_mean/p95
#
# ALGORITHMUS (KLEIN, PRODUKTIV)
# ──────────────────────────────
#   - "Spiel" = kurze Sequenz aus Steps (Default max_steps=25)
#   - Actions sind relativ (left/right/up/down/hold + optional zoom)
#   - Coverage-Reward basiert primär auf Zell-Neuheit/Alter (Recency), optional
#     leicht auf Informativeness (motion/sharp/strength)
#
# ENV (Defaults bewusst klein)
# ─────────────────────────────
#   OROMA_PTZ_COV_BASE_URL              (default http://127.0.0.1:8080)
#   OROMA_PTZ_COV_HTTP_TIMEOUT_SEC      (default 4.0)
#
#   OROMA_PTZ_COV_MAX_STEPS             (default 25)
#   OROMA_PTZ_COV_DT_MS                 (default 300)
#   OROMA_PTZ_COV_AMOUNT                (default 10)
#
#   Quantisierung der PTZ-Position → Zellen:
#   OROMA_PTZ_COV_PAN_BUCKET            (default 60000)
#   OROMA_PTZ_COV_TILT_BUCKET           (default 60000)
#   OROMA_PTZ_COV_ZOOM_BUCKET           (default 50)
#
#   Reward-Gewichte:
#   OROMA_PTZ_COV_W_NEW                 (default 1.0)
#   OROMA_PTZ_COV_W_AGE                 (default 0.30)
#   OROMA_PTZ_COV_T_AGE_SEC             (default 21600 = 6h)
#   OROMA_PTZ_COV_W_REPEAT              (default 0.25)
#   OROMA_PTZ_COV_W_INFO                (default 0.08)
#
#   Coverage-Namespace (für stats.db)
#   OROMA_PTZ_COV_NAMESPACE             (default ptz:coverage)
#
#   Pruning / Begrenzung (Dauerbetrieb; wie "pruned")
#   OROMA_PTZ_COV_MAX_CELLS             (default 2000)  # max Zellen pro namespace in stats.db
#
# CLI
# ───
#   python3 tools/ptz_coverage_daily_runner.py --policy-games 20 --explore-games 20 --seed 1
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request
import logging
from typing import Any, Dict, List, Optional, Tuple


LOG = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return float(default)


def _now_ts() -> int:
    return int(time.time())


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


class HttpHub:
    """Small HTTP hub mirroring the UI strategy (use running ORÓMA source)."""

    def __init__(self, base_url: str, timeout_sec: float = 4.0):
        self.base_url = (base_url or "http://127.0.0.1:8080").rstrip("/")
        self.timeout_sec = float(timeout_sec)

    def ptz_status(self) -> Dict[str, Any]:
        url = f"{self.base_url}/video/api/ptz/status"
        with urllib.request.urlopen(url, timeout=self.timeout_sec) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))

    def ptz_command(self, action: str, amount: int) -> None:
        url = f"{self.base_url}/video/api/ptz/command"
        payload = json.dumps({"action": str(action), "amount": int(amount)}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as _r:
            pass

    def snapshot_jpeg(self) -> Optional[bytes]:
        url = f"{self.base_url}/video/snapshot.jpg"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_sec) as r:
                b = r.read()
            if not b:
                return None
            return b
        except Exception:
            return None


def _jpeg_to_gray_small(jpg: bytes, w: int = 160, h: int = 90) -> Optional[Any]:
    """Decode JPEG bytes to small grayscale array (cv2 preferred, PIL fallback)."""
    # 1) cv2 path
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        arr = np.frombuffer(jpg, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g2 = cv2.resize(g, (int(w), int(h)), interpolation=cv2.INTER_AREA)
        return g2
    except Exception:
        pass

    # 2) PIL path
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
        import io

        im = Image.open(io.BytesIO(jpg)).convert("L")
        im2 = im.resize((int(w), int(h)), resample=Image.Resampling.BILINEAR)
        return np.array(im2, dtype=np.uint8)
    except Exception:
        return None


def _motion_norm(g1: Any, g2: Any) -> float:
    try:
        import numpy as np  # type: ignore

        diff = np.abs(g2.astype(np.int16) - g1.astype(np.int16)).astype(np.uint8)
        return float(np.mean(diff)) / 255.0
    except Exception:
        return 0.0


def _sharpness_norm(g: Any) -> float:
    """Headless-friendly sharpness score in [0..1] (gradient magnitude mean)."""
    try:
        import numpy as np  # type: ignore

        gx = np.abs(np.diff(g.astype(np.int16), axis=1))
        gy = np.abs(np.diff(g.astype(np.int16), axis=0))
        m = float(np.mean(gx)) + float(np.mean(gy))
        # Empirical normalization: typical ranges are small; clamp to [0..1]
        return _clamp(m / 80.0, 0.0, 1.0)
    except Exception:
        return 0.0


def _strength_from_motion(motion_norm: float) -> float:
    """Coverage does not need a complex target; treat motion_norm as weak strength proxy."""
    return _clamp(float(motion_norm) * 6.0, 0.0, 1.0)


def _cell_id_from_status(st: Dict[str, Any], pan_bucket: int, tilt_bucket: int, zoom_bucket: int) -> Tuple[str, int, int, int]:
    pan = int(st.get("pan") or 0)
    tilt = int(st.get("tilt") or 0)
    zoom = int(st.get("zoom") or 0)
    ix = int(round(float(pan) / float(max(1, int(pan_bucket)))))
    iy = int(round(float(tilt) / float(max(1, int(tilt_bucket)))))
    iz = int(round(float(zoom) / float(max(1, int(zoom_bucket)))))
    return f"{ix}:{iy}:{iz}", ix, iy, iz


def _dbw_enabled() -> bool:
    """True, wenn der globale DBWriter für diesen Runner aktiv ist."""
    try:
        from core import db_writer_client as _dbw  # type: ignore
        return bool(_dbw is not None and getattr(_dbw, "enabled")())
    except Exception:
        return False


def _dbw_timeout_ms() -> int:
    try:
        raw = str(os.environ.get("OROMA_DBW_CLIENT_TIMEOUT_MS_UI", os.environ.get("OROMA_DBW_TIMEOUT_MS", "60000"))).strip()
        return int(raw or "60000")
    except Exception:
        return 60000


def _dbw_exec_write(sql_stmt: str, params: List[Any], *, tag: str) -> int:
    """Route einen Write strikt über den DBWriter auf stats.db."""
    from core import db_writer_client as _dbw  # type: ignore
    if _dbw is None or not getattr(_dbw, "enabled")():
        raise RuntimeError("db_writer_client unavailable or disabled")
    return int(getattr(_dbw, "exec_write")(
        str(sql_stmt),
        params=list(params),
        tag=str(tag),
        priority="normal",
        timeout_ms=_dbw_timeout_ms(),
        db="stats",
    ) or 0)


def _ensure_stats_schema(conn, table: str = "ptz_coverage_cells") -> None:
    sql_create = f"""
        CREATE TABLE IF NOT EXISTS {table} (
          namespace TEXT NOT NULL,
          cell_id TEXT NOT NULL,
          last_seen_ts INTEGER NOT NULL,
          seen_count INTEGER NOT NULL,
          best_motion REAL NOT NULL,
          best_strength REAL NOT NULL,
          best_sharp REAL NOT NULL,
          best_ts INTEGER NOT NULL,
          PRIMARY KEY(namespace, cell_id)
        )
        """
    sql_index = f"CREATE INDEX IF NOT EXISTS idx_{table}_last_seen ON {table}(namespace, last_seen_ts DESC)"
    if _dbw_enabled():
        _dbw_exec_write(sql_create, [], tag="ptz_coverage.stats.schema.create")
        _dbw_exec_write(sql_index, [], tag="ptz_coverage.stats.schema.index")
        return
    conn.execute(sql_create)
    conn.execute(sql_index)


def _stats_get_cell(conn, namespace: str, cell_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT namespace, cell_id, last_seen_ts, seen_count, best_motion, best_strength, best_sharp, best_ts "
        "FROM ptz_coverage_cells WHERE namespace=? AND cell_id=?",
        (str(namespace), str(cell_id)),
    )
    row = cur.fetchone()
    return row


def _stats_upsert_cell(
    conn,
    namespace: str,
    cell_id: str,
    ts: int,
    motion: float,
    strength: float,
    sharp: float,
) -> Tuple[bool, int]:
    """Return (is_new_cell, new_seen_count)."""
    row = _stats_get_cell(conn, namespace, cell_id)
    if not row:
        sql_ins = (
            "INSERT INTO ptz_coverage_cells(namespace, cell_id, last_seen_ts, seen_count, best_motion, best_strength, best_sharp, best_ts) "
            "VALUES(?,?,?,?,?,?,?,?)"
        )
        params_ins = [str(namespace), str(cell_id), int(ts), 1, float(motion), float(strength), float(sharp), int(ts)]
        if _dbw_enabled():
            _dbw_exec_write(sql_ins, params_ins, tag="ptz_coverage.stats.upsert.insert")
        else:
            conn.execute(sql_ins, tuple(params_ins))
        return True, 1

    seen = int(row.get("seen_count") or 0) + 1
    best_motion = float(row.get("best_motion") or 0.0)
    best_strength = float(row.get("best_strength") or 0.0)
    best_sharp = float(row.get("best_sharp") or 0.0)
    best_ts = int(row.get("best_ts") or 0)

    # Update best snapshot stats if informative.
    upd_best = False
    if motion > best_motion + 1e-9:
        best_motion = float(motion)
        upd_best = True
    if strength > best_strength + 1e-9:
        best_strength = float(strength)
        upd_best = True
    if sharp > best_sharp + 1e-9:
        best_sharp = float(sharp)
        upd_best = True
    if upd_best:
        best_ts = int(ts)

    sql_upd = (
        "UPDATE ptz_coverage_cells SET last_seen_ts=?, seen_count=?, best_motion=?, best_strength=?, best_sharp=?, best_ts=? "
        "WHERE namespace=? AND cell_id=?"
    )
    params_upd = [int(ts), int(seen), float(best_motion), float(best_strength), float(best_sharp), int(best_ts), str(namespace), str(cell_id)]
    if _dbw_enabled():
        _dbw_exec_write(sql_upd, params_upd, tag="ptz_coverage.stats.upsert.update")
    else:
        conn.execute(sql_upd, tuple(params_upd))
    return False, seen


def _stats_count_cells(conn, namespace: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ptz_coverage_cells WHERE namespace=?",
        (str(namespace),),
    ).fetchone() or {}
    try:
        return int(row.get("n") or 0)
    except Exception:
        return 0


def _stats_prune_namespace(conn, namespace: str, max_cells: int) -> int:
    """Prune oldest cells for namespace to keep stats.db bounded. Returns pruned row count."""
    mc = int(max(0, max_cells))
    if mc <= 0:
        return 0
    n = _stats_count_cells(conn, namespace)
    if n <= mc:
        return 0
    to_del = int(n - mc)
    sql_del = (
        "DELETE FROM ptz_coverage_cells WHERE namespace=? AND cell_id IN ("
        "  SELECT cell_id FROM ptz_coverage_cells WHERE namespace=?"
        "  ORDER BY last_seen_ts ASC LIMIT ?"
        ")"
    )
    params_del = [str(namespace), str(namespace), int(to_del)]
    if _dbw_enabled():
        _dbw_exec_write(sql_del, params_del, tag="ptz_coverage.stats.prune")
    else:
        conn.execute(sql_del, tuple(params_del))
    return int(to_del)



def _compute_reward(
    *,
    is_new: bool,
    last_seen_ts: int,
    now_ts: int,
    revisited: bool,
    motion: float,
    sharp: float,
    strength: float,
    w_new: float,
    w_age: float,
    t_age: float,
    w_repeat: float,
    w_info: float,
) -> float:
    r = 0.0
    if is_new:
        r += float(w_new)
    # Recency bonus (0..w_age)
    age = max(0.0, float(now_ts - int(last_seen_ts)))
    r += float(w_age) * _clamp(age / max(1.0, float(t_age)), 0.0, 1.0)
    if revisited:
        r -= float(w_repeat)
    # Small informativeness bonus
    info = _clamp(float(motion) + float(sharp) + float(strength), 0.0, 3.0) / 3.0
    r += float(w_info) * info
    return float(r)


def _choose_action(step_idx: int, mode: str, rng: random.Random) -> str:
    """Deterministic serpentine-like pattern with small randomness in explore."""
    # Base sweep (serpentine):
    #   - Horizontal: 4x right, 4x left
    #   - Vertical: alternates down/up to avoid long-term "drift nach unten"
    #     (ein häufiges Problem bei relativen PTZ nudges ohne absolute Koordinaten)
    vert = "down" if ((step_idx // 9) % 2 == 0) else "up"
    seq = ["right"] * 4 + [vert] + ["left"] * 4 + [vert]
    a = seq[int(step_idx) % len(seq)]
    if mode == "explore":
        # Small chance to deviate to avoid stuck patterns
        if rng.random() < 0.10:
            a = rng.choice(["left", "right", "up", "down", "hold"])
    return a


def _virtual_cell_id(ix: int, iy: int, iz: int) -> str:
    """Stable cell id even if backend does not expose PTZ coords.

    Hintergrund:
      Einige PTZ-Backends liefern in /video/api/ptz/status keine sinnvollen
      pan/tilt/zoom Werte (bleiben z.B. konstant 0). Dann bleibt die
      Quantisierung (ix:iy:iz) immer gleich und Coverage kann nicht wachsen.

    Lösung:
      Wir können in diesem Runner eine virtuelle Raster-Position (ix/iy/iz)
      führen, die bei jedem Move deterministisch mitläuft. Das ist bewusst
      "Runner-only" und verändert KEIN Core-Verhalten.
    """
    return f"v{int(ix)}:{int(iy)}:{int(iz)}"


def _run_batch(
    *,
    hub: HttpHub,
    mode: str,
    games: int,
    namespace: str,
    max_steps: int,
    dt_ms: int,
    amount: int,
    pan_bucket: int,
    tilt_bucket: int,
    zoom_bucket: int,
    stats_db_path: str,
    seed: int,
) -> Dict[str, Any]:
    from core import sql_manager  # late import (fast start for --help)

    rng = random.Random(int(seed) ^ (0xA5A5 if mode == "explore" else 0x5A5A))

    ts_start = _now_ts()
    steps = 0
    moves = 0
    holds = 0
    revisits = 0
    uniq_cells = set()
    reward_sum = 0.0

    # Telemetry
    snap_ok = snap_fail = 0
    dec_ok = dec_fail = 0
    motion_vals: List[float] = []
    sharp_vals: List[float] = []
    strength_vals: List[float] = []

    w_new = _env_float("OROMA_PTZ_COV_W_NEW", 1.0)
    w_age = _env_float("OROMA_PTZ_COV_W_AGE", 0.30)
    t_age = _env_float("OROMA_PTZ_COV_T_AGE_SEC", 21600.0)
    w_repeat = _env_float("OROMA_PTZ_COV_W_REPEAT", 0.25)
    w_info = _env_float("OROMA_PTZ_COV_W_INFO", 0.08)


    # ---------------------------------------------------------------------
    # stats.db Bounding / Pruning Telemetry
    # ---------------------------------------------------------------------
    # WHY:
    #   Das PTZ-Coverage-Game persistiert Coverage-Zellen dauerhaft in stats.db
    #   (Tabelle ptz_coverage_cells). Damit stats.db nicht unbounded wächst,
    #   wird optional pro Batch ein Pruning auf eine Maximalanzahl Zellen
    #   durchgeführt.
    #
    #   Zusätzlich wollen wir im Episode-Metrics JSON (oroma.db -> episodic_metrics)
    #   sichtbar machen, ob/was gepruned wurde.
    #
    #   WICHTIG:
    #     - Immer defaults definieren, damit es NIE zu NameError kommt
    #     - stats.db Verbindung sauber via context manager (close garantiert)
    #
    max_cells = _env_int("OROMA_PTZ_COV_MAX_CELLS", 2000)
    cells_before = 0
    cells_after = 0
    pruned_cells = 0
    stats_ok = True
    stats_err = None  # str, visible in episodic_metrics (no silent fails)

    # Initialize stats db schema
    # Initialize stats.db schema + count current cells for telemetry.
    # IMPORTANT: no silent failures – error is surfaced via stats_ok/stats_err in episodic_metrics JSON.
    try:
        with sql_manager.get_conn(stats_db_path) as conn_stats:
            _ensure_stats_schema(conn_stats)
            try:
                cells_before = _stats_count_cells(conn_stats, namespace)
            except Exception as e:
                stats_ok = False
                stats_err = f"stats_count_before_failed: {type(e).__name__}: {e}"
                cells_before = 0
            conn_stats.commit()
    except Exception as e:
        stats_ok = False
        stats_err = f"stats_schema_init_failed: {type(e).__name__}: {e}"
        cells_before = 0

    prev_gray = None
    last_cell_id = None
    now_ts = ts_start

    # PTZ-Status Range/Wechsel-Tracking (für Debug + Zell-Quantisierung)
    pan_min = pan_max = None
    tilt_min = tilt_max = None
    zoom_min = zoom_max = None
    cell_changes = 0
    ptz_status_changes = 0
    last_pan = last_tilt = last_zoom = None

    # Adaptive Quantisierung: falls sich PTZ bewegt, aber die Zelle nie wechselt,
    # reduzieren wir die Bucket-Größe schrittweise (bis Minimum), damit Coverage wächst.
    pan_bucket_min = _env_int('OROMA_PTZ_COV_PAN_BUCKET_MIN', 8000)
    tilt_bucket_min = _env_int('OROMA_PTZ_COV_TILT_BUCKET_MIN', 8000)
    zoom_bucket_min = _env_int('OROMA_PTZ_COV_ZOOM_BUCKET_MIN', 10)
    _bucket_adapted = False

    # Virtuelles Raster (Fallback), wenn PTZ-Status keine Koordinaten liefert.
    # Default: ON, weil in headless/hardware-nahen Setups je nach PTZ-Treiber
    # pan/tilt/zoom nicht zuverlässig zurückkommen.
    use_virtual_if_no_coords = bool(_env_int('OROMA_PTZ_COV_USE_VIRTUAL_GRID_IF_NO_COORDS', 1))
    grid_w = max(2, _env_int('OROMA_PTZ_COV_GRID_W', 9))
    grid_h = max(2, _env_int('OROMA_PTZ_COV_GRID_H', 5))
    grid_z = max(1, _env_int('OROMA_PTZ_COV_GRID_Z', 1))
    # Start in der Mitte (reduziert "drift nach unten" über viele Runs)
    v_ix = grid_w // 2
    v_iy = grid_h // 2
    v_iz = 0
    v_row_dir = 1  # +1 = down, -1 = up (serpentine)
    virtual_active = False
    no_coords_steps = 0

    # Centering telemetry (no silent failures): exported in episodic_metrics.
    center_at_start_enabled = (_env_int('OROMA_PTZ_COV_CENTER_AT_START', 1) == 1)
    center_each_game_enabled = (_env_int('OROMA_PTZ_COV_CENTER_EACH_GAME', 0) == 1)
    center_every = _env_int('OROMA_PTZ_COV_CENTER_EVERY_STEPS', 0)
    center_ok = 0
    center_fail = 0
    center_err = None

    # Optional: PTZ bei Run-Start zentrieren (verhindert, dass der Sweep
    # über Tage in einen Rand driftet; ist ein reiner Komfort-Reset).
    if center_at_start_enabled:
        try:
            hub.ptz_command('center', 1)
            time.sleep(0.20)
            center_ok += 1
        except Exception:
            center_fail += 1
            center_err = "center_at_start_failed"

    for g in range(int(games)):
        # Optional: je "Game" zentrieren (konservativ OFF per default).
        if center_each_game_enabled:
            try:
                hub.ptz_command('center', 1)
                time.sleep(0.20)
            except Exception:
                pass
        for s in range(int(max_steps)):
            steps += 1

            # Optional: regelmäßiges Centering (z.B. alle 200 Steps).
            # Default OFF, weil Center das Coverage-Raster "sprunghaft" resetten kann.
            center_every = _env_int('OROMA_PTZ_COV_CENTER_EVERY_STEPS', 0)
            if center_every > 0 and (steps % int(center_every) == 0):
                try:
                    hub.ptz_command('center', 1)
                    time.sleep(0.20)
                except Exception:
                    pass

            a = _choose_action(steps - 1, mode, rng)
            if a == "hold":
                holds += 1
            else:
                moves += 1
                try:
                    hub.ptz_command(a, int(amount))
                except Exception:
                    # treat as hold on command failure
                    holds += 1
            time.sleep(float(dt_ms) / 1000.0)

            jpg = hub.snapshot_jpeg()
            if not jpg:
                snap_fail += 1
                continue
            snap_ok += 1
            gray = _jpeg_to_gray_small(jpg)
            if gray is None:
                dec_fail += 1
                continue
            dec_ok += 1

            # Telemetry signals
            motion = 0.0
            if prev_gray is not None:
                motion = _motion_norm(prev_gray, gray)
            sharp = _sharpness_norm(gray)
            strength = _strength_from_motion(motion)
            prev_gray = gray
            motion_vals.append(float(motion))
            sharp_vals.append(float(sharp))
            strength_vals.append(float(strength))

            # Determine cell
            try:
                st = hub.ptz_status()
            except Exception:
                st = {}

            # Raw PTZ values (may be 0 if backend does not expose them)
            pan = int(st.get('pan') or 0)
            tilt = int(st.get('tilt') or 0)
            zoom = int(st.get('zoom') or 0)

            # "No coords" detection: wenn wir Moves senden, aber Status stabil 0 bleibt,
            # schalten wir nach kurzer Zeit in Virtual-Grid um.
            if use_virtual_if_no_coords and (not virtual_active):
                if (pan == 0 and tilt == 0 and zoom == 0) and (a != 'hold'):
                    no_coords_steps += 1
                # Wenn nach einigen Moves immer noch keine Statusänderung erkennbar ist:
                if no_coords_steps >= 6 and ptz_status_changes == 0:
                    virtual_active = True

            # Virtual grid update (nur wenn aktiv)
            if virtual_active:
                if a == 'left':
                    v_ix = max(0, int(v_ix) - 1)
                elif a == 'right':
                    v_ix = min(grid_w - 1, int(v_ix) + 1)
                elif a == 'up':
                    v_iy = max(0, int(v_iy) - 1)
                elif a == 'down':
                    v_iy = min(grid_h - 1, int(v_iy) + 1)
                elif a == 'zoom_in':
                    v_iz = min(grid_z - 1, int(v_iz) + 1)
                elif a == 'zoom_out':
                    v_iz = max(0, int(v_iz) - 1)

                # Serpentine safety: wenn wir am Rand sind, kippen wir die vertikale Richtung.
                if v_iy <= 0:
                    v_row_dir = 1
                elif v_iy >= (grid_h - 1):
                    v_row_dir = -1

            # Range tracking
            if pan_min is None or pan < pan_min:
                pan_min = pan
            if pan_max is None or pan > pan_max:
                pan_max = pan
            if tilt_min is None or tilt < tilt_min:
                tilt_min = tilt
            if tilt_max is None or tilt > tilt_max:
                tilt_max = tilt
            if zoom_min is None or zoom < zoom_min:
                zoom_min = zoom
            if zoom_max is None or zoom > zoom_max:
                zoom_max = zoom

            # Status change tracking
            if last_pan is not None and (pan != last_pan or tilt != last_tilt or zoom != last_zoom):
                ptz_status_changes += 1
            last_pan, last_tilt, last_zoom = pan, tilt, zoom

            if virtual_active:
                cell_id = _virtual_cell_id(v_ix, v_iy, v_iz)
            else:
                cell_id, _ix, _iy, _iz = _cell_id_from_status(st, pan_bucket, tilt_bucket, zoom_bucket)
            uniq_cells.add(cell_id)

            # Cell change tracking (Coverage-relevant)
            if last_cell_id is not None and cell_id != last_cell_id:
                cell_changes += 1

            revisited_step = (last_cell_id == cell_id)
            if revisited_step:
                revisits += 1
            last_cell_id = cell_id

            # Adaptive bucket shrink: after a few steps, if PTZ status changes but cell never changes,
            # shrink buckets to make quantization finer (only once per batch).
            if (not _bucket_adapted) and steps >= 12 and cell_changes == 0 and ptz_status_changes >= 3:
                pan_bucket = max(pan_bucket_min, int(max(1, pan_bucket)) // 2)
                tilt_bucket = max(tilt_bucket_min, int(max(1, tilt_bucket)) // 2)
                zoom_bucket = max(zoom_bucket_min, int(max(1, zoom_bucket)) // 2)
                _bucket_adapted = True

            now_ts = _now_ts()

            # Update stats.db & compute reward
            with sql_manager.get_conn(stats_db_path) as conn_stats:
                row = _stats_get_cell(conn_stats, namespace, cell_id)
                last_seen_ts = int(row.get("last_seen_ts") if row else 0)
                is_new, _seen = _stats_upsert_cell(conn_stats, namespace, cell_id, now_ts, motion, strength, sharp)
                conn_stats.commit()

            reward = _compute_reward(
                is_new=is_new,
                last_seen_ts=last_seen_ts,
                now_ts=now_ts,
                revisited=revisited_step,
                motion=motion,
                sharp=sharp,
                strength=strength,
                w_new=w_new,
                w_age=w_age,
                t_age=t_age,
                w_repeat=w_repeat,
                w_info=w_info,
            )
            reward_sum += float(reward)

    ts_end = _now_ts()
    dur_ms = float(max(0, ts_end - ts_start)) * 1000.0
    uniq = int(len(uniq_cells))
    total_cells_seen = max(1, int(steps))
    coverage_rate = float(uniq) / float(max(1, uniq + max(0, revisits)))

    def _p95(vals: List[float]) -> float:
        if not vals:
            return 0.0
        vs = sorted(vals)
        idx = int(round(0.95 * (len(vs) - 1)))
        return float(vs[max(0, min(len(vs) - 1, idx))])


    # ------------------------------------------------------------------
    # stats.db Pruning (optional) – am Ende des Batches
    # ------------------------------------------------------------------
    # NOTE:
    #   Wir prune'n bewusst erst nach dem Batch, damit wir während der Steps
    #   keinen zusätzlichen Delete-Load haben.
    #
    #   Das Pruning ist rein auf stats.db begrenzt (Coverage-Zellcache), nicht
    #   auf oroma.db. Es verändert NICHT die Episode-Logik.
    # Execute pruning + count after (if stats_ok is already False we still try once).
    try:
        with sql_manager.get_conn(stats_db_path) as conn_stats:
            # Prune (optional)
            try:
                pruned_cells = _stats_prune_namespace(conn_stats, namespace, int(max_cells))
            except Exception as e:
                stats_ok = False
                msg = f"stats_prune_failed: {type(e).__name__}: {e}"
                stats_err = (msg if not stats_err else (str(stats_err) + " | " + msg))
                pruned_cells = 0
            # Count after
            try:
                cells_after = _stats_count_cells(conn_stats, namespace)
            except Exception as e:
                stats_ok = False
                msg = f"stats_count_after_failed: {type(e).__name__}: {e}"
                stats_err = (msg if not stats_err else (str(stats_err) + " | " + msg))
                cells_after = cells_before
            try:
                conn_stats.commit()
            except Exception:
                pass
    except Exception as e:
        stats_ok = False
        msg = f"stats_open_failed: {type(e).__name__}: {e}"
        stats_err = (msg if not stats_err else (str(stats_err) + " | " + msg))
        # leave defaults

    out = {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "duration_ms": dur_ms,
        "games": int(games),
        "steps": int(steps),
        "moves": int(moves),
        "holds": int(holds),
        "revisits": int(revisits),
        "coverage_unique_cells": uniq,
        "coverage_rate": float(coverage_rate),
        "avg_reward": float(reward_sum) / float(max(1, steps)),
        "snap_ok": int(snap_ok),
        "snap_fail": int(snap_fail),
        "decode_ok": int(dec_ok),
        "decode_fail": int(dec_fail),
        "motion_norm_mean": float(sum(motion_vals) / float(max(1, len(motion_vals)))) if motion_vals else 0.0,
        "motion_norm_p95": _p95(motion_vals),
        "sharp_mean": float(sum(sharp_vals) / float(max(1, len(sharp_vals)))) if sharp_vals else 0.0,
        "sharp_p95": _p95(sharp_vals),
        "strength_mean": float(sum(strength_vals) / float(max(1, len(strength_vals)))) if strength_vals else 0.0,
        "strength_p95": _p95(strength_vals),

        # Debug / Acceptance: echte PTZ-Range + Zellwechsel sichtbar machen (wichtig für Dauerbetrieb)
        "pan_min": int(pan_min) if pan_min is not None else None,
        "pan_max": int(pan_max) if pan_max is not None else None,
        "tilt_min": int(tilt_min) if tilt_min is not None else None,
        "tilt_max": int(tilt_max) if tilt_max is not None else None,
        "zoom_min": int(zoom_min) if zoom_min is not None else None,
        "zoom_max": int(zoom_max) if zoom_max is not None else None,
        "cell_changes": int(cell_changes),
        "ptz_status_changes": int(ptz_status_changes),
        "pan_bucket_final": int(pan_bucket),
        "stats_db_path": str(stats_db_path),
        "stats_ok": bool(stats_ok),
        "stats_err": (str(stats_err) if stats_err else None),
        "max_cells": int(max_cells),
        "cells_before": cells_before,
        "cells_after": cells_after,
        "cells_pruned": int(pruned_cells),
        "tilt_bucket_final": int(tilt_bucket),
        "zoom_bucket_final": int(zoom_bucket),

        "center_at_start": bool(center_at_start_enabled),
        "center_each_game": bool(center_each_game_enabled),
        "center_every_steps": int(center_every),
        "center_ok": int(center_ok),
        "center_fail": int(center_fail),
        "center_err": (str(center_err) if center_err else None),
        "mode": str(mode),
        "namespace": str(namespace),
        "max_steps": int(max_steps),
        "dt_ms": int(dt_ms),
        "amount": int(amount),
        "source": "orchestrator",
        "have_up": True,
    }
    return out


def _write_episode(kind: str, label: str, meta: Dict[str, Any], metrics: Dict[str, Any]) -> int:
    """Write episode + episodic_metrics to oroma.db. Return episode_id (or 0)."""
    from core import sql_manager

    ts_start = int(meta.get("ts_start") or _now_ts())
    ts_end = int(meta.get("ts_end") or ts_start)

    # WICHTIG / WARUM SO:
    #   In ORÓMA existieren (historisch) mehrere sql_manager-Varianten im Umlauf.
    #   Einige ZIP-Stände hatten insert_episode()/insert_episodic_metric() bereits
    #   mit optionalem `conn=...`, andere nicht.
    #
    #   Damit ptz_coverage *stabil* auf deinem produktiven Stand läuft (ohne
    #   Signatur-Mismatch oder "You can only execute one statement at a time"),
    #   nutzen wir hier strikt die öffentliche, stabile API:
    #     - sql_manager.ensure_schema()             (öffnet/schließt selbst)
    #     - sql_manager.insert_episode(...)         (öffnet/schließt selbst)
    #     - sql_manager.insert_episodic_metric(...) (öffnet/schließt selbst)
    #
    #   Das ist bei kleinen Defaults (20/20, max_steps=25) ausreichend schnell
    #   und vermeidet DB-Locks + Multi-Statement-Probleme.

    # Schema idempotent sicherstellen (falls der Runner separat ohne Service-Start läuft)
    sql_manager.ensure_schema()

    eid = sql_manager.insert_episode(
        ts_start=ts_start,
        ts_end=ts_end,
        kind=str(kind),
        source=str(meta.get("source") or "orchestrator"),
        label=str(label),
        meta=meta,
    )
    if not eid:
        return 0

    ts = int(ts_end)
    for k, v in metrics.items():
        try:
            sql_manager.insert_episodic_metric(int(eid), ts, str(k), float(v))
        except Exception:
            # best effort: episodic_metrics darf den Run nicht abbrechen
            continue
    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=20)
    ap.add_argument("--explore-games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--namespace", type=str, default=os.environ.get("OROMA_PTZ_COV_NAMESPACE", "ptz:coverage"))
    args = ap.parse_args()

    base = os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma"
    stats_db_path = os.path.join(base, "data", "stats.db")

    hub = HttpHub(
        base_url=os.environ.get("OROMA_PTZ_COV_BASE_URL", "http://127.0.0.1:8080"),
        timeout_sec=_env_float("OROMA_PTZ_COV_HTTP_TIMEOUT_SEC", 4.0),
    )

    max_steps = _env_int("OROMA_PTZ_COV_MAX_STEPS", 25)
    dt_ms = _env_int("OROMA_PTZ_COV_DT_MS", 300)
    amount = _env_int("OROMA_PTZ_COV_AMOUNT", 10)
    pan_bucket = _env_int("OROMA_PTZ_COV_PAN_BUCKET", 60000)
    tilt_bucket = _env_int("OROMA_PTZ_COV_TILT_BUCKET", 60000)
    zoom_bucket = _env_int("OROMA_PTZ_COV_ZOOM_BUCKET", 50)

    out: Dict[str, Any] = {"ok": True, "have_up": True, "db_written": False}

    # Quick availability check
    try:
        _ = hub.ptz_status()
        have_up = True
    except Exception:
        have_up = False
    out["have_up"] = bool(have_up)
    if not have_up:
        out["ok"] = False
        out["err"] = "ptz_status_unavailable"
        print(json.dumps(out, ensure_ascii=False))
        return 2

    ns = str(args.namespace)
    seed = int(args.seed)

    # Run batches
    pol = _run_batch(
        hub=hub,
        mode="policy",
        games=max(0, int(args.policy_games)),
        namespace=ns,
        max_steps=max_steps,
        dt_ms=dt_ms,
        amount=amount,
        pan_bucket=pan_bucket,
        tilt_bucket=tilt_bucket,
        zoom_bucket=zoom_bucket,
        stats_db_path=stats_db_path,
        seed=seed,
    )
    exp = _run_batch(
        hub=hub,
        mode="explore",
        games=max(0, int(args.explore_games)),
        namespace=ns,
        max_steps=max_steps,
        dt_ms=dt_ms,
        amount=amount,
        pan_bucket=pan_bucket,
        tilt_bucket=tilt_bucket,
        zoom_bucket=zoom_bucket,
        stats_db_path=stats_db_path,
        seed=seed,
    )

    out["policy"] = pol
    out["explore"] = exp

    # Write episodes
    db_written = False
    try:
        if int(args.policy_games) > 0:
            meta = dict(pol)
            eid = _write_episode(
                kind="game:ptz_coverage:policy_batch",
                label=f"ptz_coverage:policy ({int(args.policy_games)} games)",
                meta=meta,
                metrics={
                    "duration_ms": pol.get("duration_ms", 0.0),
                    "games": pol.get("games", 0),
                    "steps": pol.get("steps", 0),
                    "moves": pol.get("moves", 0),
                    "holds": pol.get("holds", 0),
                    "coverage_unique_cells": pol.get("coverage_unique_cells", 0),
                    "coverage_rate": pol.get("coverage_rate", 0.0),
                    "revisits": pol.get("revisits", 0),
                    "avg_reward": pol.get("avg_reward", 0.0),
                    "snap_ok": pol.get("snap_ok", 0),
                    "snap_fail": pol.get("snap_fail", 0),
                    "decode_ok": pol.get("decode_ok", 0),
                    "decode_fail": pol.get("decode_fail", 0),
                    "motion_norm_mean": pol.get("motion_norm_mean", 0.0),
                    "motion_norm_p95": pol.get("motion_norm_p95", 0.0),
                    "sharp_mean": pol.get("sharp_mean", 0.0),
                    "sharp_p95": pol.get("sharp_p95", 0.0),
                    "strength_mean": pol.get("strength_mean", 0.0),
                    "strength_p95": pol.get("strength_p95", 0.0),
                },
            )
            pol["episode_id"] = int(eid)
            db_written = db_written or bool(eid)
        if int(args.explore_games) > 0:
            meta = dict(exp)
            eid = _write_episode(
                kind="game:ptz_coverage:explore_batch",
                label=f"ptz_coverage:explore ({int(args.explore_games)} games)",
                meta=meta,
                metrics={
                    "duration_ms": exp.get("duration_ms", 0.0),
                    "games": exp.get("games", 0),
                    "steps": exp.get("steps", 0),
                    "moves": exp.get("moves", 0),
                    "holds": exp.get("holds", 0),
                    "coverage_unique_cells": exp.get("coverage_unique_cells", 0),
                    "coverage_rate": exp.get("coverage_rate", 0.0),
                    "revisits": exp.get("revisits", 0),
                    "avg_reward": exp.get("avg_reward", 0.0),
                    "snap_ok": exp.get("snap_ok", 0),
                    "snap_fail": exp.get("snap_fail", 0),
                    "decode_ok": exp.get("decode_ok", 0),
                    "decode_fail": exp.get("decode_fail", 0),
                    "motion_norm_mean": exp.get("motion_norm_mean", 0.0),
                    "motion_norm_p95": exp.get("motion_norm_p95", 0.0),
                    "sharp_mean": exp.get("sharp_mean", 0.0),
                    "sharp_p95": exp.get("sharp_p95", 0.0),
                    "strength_mean": exp.get("strength_mean", 0.0),
                    "strength_p95": exp.get("strength_p95", 0.0),
                },
            )
            exp["episode_id"] = int(eid)
            db_written = db_written or bool(eid)
    except Exception as e:
        out["ok"] = False
        out["err"] = f"db_write_failed: {e}"
        out["db_written"] = False
        print(json.dumps(out, ensure_ascii=False))
        return 3

    out["db_written"] = bool(db_written)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

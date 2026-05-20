#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_target_daily_runner.py
# Projekt:   ORÓMA (PTZ Targeting · Daily Runner)
# Version:   v3.7.5
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# Zweck
# ─────
#   Führt 1× täglich (Orchestrator) PTZ-Targeting Episoden aus:
#     - policy_batch: eps=0, learn=false
#     - explore_batch: eps>0, learn=true
#
#   Schreibt Telemetrie in oroma.db:
#     episodes.kind:
#       - game:ptz_target:policy_batch
#       - game:ptz_target:explore_batch
#     episodic_metrics:
#       - games, steps, moves, holds
#       - avg_step_ms, avg_reward, avg_dist, lock_rate
#       - duration_ms, eps, amount, max_steps
#       - motion_* (snap/decode/motion_norm/motion_thr)
#       - lock_* (candidate/hits/unlock + thresholds)
#       - strength_* + EMA + dx/dy abs mean
#
#   Wichtig
#   ────────
#   Dieser Runner nutzt standardmäßig die laufende ORÓMA HTTP-API (wie die UI)
#   um sicher die konfigurierte PTZ-Videoquelle (z.B. /dev/video8) zu verwenden.
#
#   Motion/Target-Signal & Lock (produktiver Fix)
#   ────────────────────────────────────────────
#   In der Praxis kann es vorkommen, dass "motion" sehr oft 0 bleibt (statische
#   Szene, Schwellenwert zu hoch, fehlende CV2-Installation). Damit PTZ-Targeting
#   trotzdem verwertbare Telemetrie und Lern-Daten erzeugt, gilt hier:
#
#     - JPEG-Decoding: cv2 bevorzugt, ansonsten PIL+numpy Fallback (headless-safe)
#     - Motion-Telemetrie: snap_ok/snap_fail, decode_ok/decode_fail,
#                          motion_ok/motion_zero + motion_norm Mean/P95
#     - Adaptiver Motion-Threshold (windowed): senkt thr schrittweise, wenn fast
#       alles "0" ist, aber motion_norm im Fenster > 0 liegt.
#
#   Lock-Design (Explore stabilisieren)
#   ─────────────────────────────────
#   Stärke ("strength") ist häufig "spiky": seltene Peaks, aber niedriger Mittelwert.
#   Eine strikte Lock-Regel nur auf Instant-Strength kann deshalb lock_candidate=0
#   erzeugen, obwohl strength_p95 sichtbar hoch ist.
#
#   Lösung in diesem Runner:
#     - lock_thr_strength dynamisch aus Recent-P95 (ENV scale/min/base)
#     - Candidate über Strength-EMA (glättet Peaks) + Peak-Schnelltrigger
#     - Sticky-Hold bei Peaks/Candidates (Explore driftet nicht sofort weg)
#     - Lock/Unlock Debounce + lock_candidate/lock_hits/unlock_events
#
#   Hinweis zu policy-games==0
#   ─────────────────────────
#   Wenn --policy-games 0 gesetzt ist, wird KEINE policy-episode in die DB geschrieben
#   (kein Noise), aber das JSON enthält weiterhin einen konsistenten policy-Block.
# =============================================================================

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _now_ts() -> int:
    return int(time.time())


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


class PolicyShim:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.have_up = False
        self.pol = None
        try:
            from core.universal_policy import Policy  # type: ignore

            self.pol = Policy(namespace=namespace)
            self.have_up = True
        except Exception:
            self.pol = None
            self.have_up = False

    def choose(self, state_hash: str, legal: List[str]) -> str:
        if not legal:
            return "hold"
        if self.pol is None:
            return random.choice(legal)
        a = self.pol.choose(state_hash, legal, side="X")
        if not a:
            return random.choice(legal)
        return str(a)

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if self.pol is None:
            return 0
        try:
            res = self.pol.learn_many(items)
        except Exception:
            return 0
        # core.universal_policy.Policy.learn_many() kann bewusst None/void sein.
        # Für Telemetrie zählen wir in dem Fall die *Versuche*.
        if res is None:
            return int(len(items))
        try:
            return int(res)
        except Exception:
            return int(len(items))


class HttpHub:
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
    """Decode JPEG bytes to small grayscale array.

    Headless-safe strategy:
      1) Prefer cv2 (fast, robust)
      2) Fallback to PIL + numpy (works without OpenCV)
    """
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


def _motion_centroid(g1: Any, g2: Any, thr: int = 25) -> Tuple[float, float, float, float]:
    """Return (dx,dy,strength,motion_norm)."""
    try:
        import numpy as np  # type: ignore
    except Exception:
        return 0.0, 0.0, 0.0, 0.0

    try:
        diff = np.abs(g2.astype(np.int16) - g1.astype(np.int16)).astype(np.uint8)
        motion_norm = float(np.mean(diff)) / 255.0
        m = (diff >= int(thr)).astype(np.uint8)
        area = float(m.sum())
        h, w = m.shape[:2]
        if area <= 1.0:
            return 0.0, 0.0, 0.0, float(motion_norm)
        ys, xs = np.nonzero(m)
        cx = float(xs.mean())
        cy = float(ys.mean())
        dx = (cx - (w / 2.0)) / (w / 2.0)
        dy = (cy - (h / 2.0)) / (h / 2.0)
        dx = float(_clamp(dx, -1.0, 1.0))
        dy = float(_clamp(dy, -1.0, 1.0))
        strength = area / float(w * h)
        strength = float(_clamp(strength * 5.0, 0.0, 1.0))
        return dx, dy, strength, float(motion_norm)
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def _state_hash(dx: float, dy: float, strength: float, pan: int, tilt: int, zoom: int) -> str:
    def b(x: float, bins: int, lo: float, hi: float) -> int:
        x = float(_clamp(x, lo, hi))
        if bins <= 1:
            return 0
        z = (x - lo) / float(hi - lo)
        return int(_clamp(math.floor(z * bins), 0, bins - 1))

    dx_b = b(dx, 11, -1.0, 1.0)
    dy_b = b(dy, 11, -1.0, 1.0)
    st_b = b(strength, 8, 0.0, 1.0)
    pan_b = int(pan // 30000)
    tilt_b = int(tilt // 30000)
    zoom_b = int(zoom // 20)
    return f"dx{dx_b}|dy{dy_b}|st{st_b}|p{pan_b}|t{tilt_b}|z{zoom_b}"


def _insert_episode(*, kind: str, label: str, meta: Dict[str, Any]) -> Optional[int]:
    ts_start = int(meta.get("ts_start") or _now_ts())
    ts_end = meta.get("ts_end")
    try:
        from core.sql_manager import insert_episode  # type: ignore

        return int(
            insert_episode(
                ts_start=ts_start,
                ts_end=int(ts_end) if ts_end is not None else None,
                kind=str(kind),
                source=str(meta.get("source") or "orchestrator"),
                label=str(label),
                meta=meta,
            )
            or 0
        )
    except Exception:
        return None


def _insert_metric(ep_id: int, key: str, value: float, ts: int) -> None:
    try:
        from core.sql_manager import insert_episodic_metric  # type: ignore

        insert_episodic_metric(episode_id=int(ep_id), key=str(key), value=float(value), ts=int(ts))
    except Exception:
        pass


def _p95(xs: List[float]) -> float:
    if not xs:
        return 0.0
    xs2 = sorted(xs)
    idx = int(_clamp(math.floor(0.95 * (len(xs2) - 1)), 0, len(xs2) - 1))
    return float(xs2[idx])


def run_batch(
    *,
    rng: random.Random,
    namespace: str,
    games: int,
    eps: float,
    learn: bool,
    explore_moves_per_game: int,
    dt_ms: int,
    amount: int,
    max_steps: int,
    http_base: str,
    source: str,
) -> Dict[str, Any]:
    t0 = _now_ts()
    shim = PolicyShim(namespace)
    hub = HttpHub(http_base)

    legal = ["left", "right", "up", "down", "zoom_in", "zoom_out", "hold"]
    move_cost = _env_float("OROMA_PTZ_TARGET_MOVE_COST", 0.01)

    # Motion threshold + adaption
    motion_thr = _env_int("OROMA_PTZ_TARGET_MOTION_THR", 25)
    motion_thr_min = _env_int("OROMA_PTZ_TARGET_MOTION_THR_MIN", 8)
    motion_thr_step = _env_int("OROMA_PTZ_TARGET_MOTION_THR_STEP", 5)
    motion_norm_min = _env_float("OROMA_PTZ_TARGET_MOTION_NORM_MIN", 0.010)
    adapt_after_steps = _env_int("OROMA_PTZ_TARGET_ADAPT_AFTER_STEPS", 18)
    adapt_zero_ratio = _env_float("OROMA_PTZ_TARGET_ADAPT_ZERO_RATIO", 0.88)

    # Lock thresholds (dynamic strength threshold)
    lock_thr_strength_base = _env_float("OROMA_PTZ_TARGET_LOCK_THR_STRENGTH_BASE", 0.25)
    lock_thr_strength_min = _env_float("OROMA_PTZ_TARGET_LOCK_THR_STRENGTH_MIN", 0.05)
    lock_thr_strength_scale = _env_float("OROMA_PTZ_TARGET_LOCK_THR_STRENGTH_SCALE", 0.35)
    lock_thr_dist = _env_float("OROMA_PTZ_TARGET_LOCK_THR_DIST", 0.20)

    lock_debounce = _env_int("OROMA_PTZ_TARGET_LOCK_DEBOUNCE", 2)
    unlock_debounce = _env_int("OROMA_PTZ_TARGET_UNLOCK_DEBOUNCE", 2)

    sticky_hold_p = _env_float("OROMA_PTZ_TARGET_STICKY_HOLD_P", 0.75)
    peak_trigger_mul = _env_float("OROMA_PTZ_TARGET_PEAK_TRIGGER_MUL", 1.5)
    peak_force_hold_steps = _env_int("OROMA_PTZ_TARGET_PEAK_FORCE_HOLD_STEPS", 3)

    strength_ema_alpha = _env_float("OROMA_PTZ_TARGET_STRENGTH_EMA_ALPHA", 0.18)

    total_steps = 0
    moves = 0
    holds = 0
    lock_hits = 0
    total_reward = 0.0
    total_dist = 0.0
    learned_items = 0

    # Diagnostics
    snap_ok = 0
    snap_fail = 0
    decode_ok = 0
    decode_fail = 0
    motion_ok = 0
    motion_zero = 0
    motion_norm_samples: List[float] = []

    lock_candidate = 0
    unlock_events = 0
    strength_samples: List[float] = []
    strength_ema_samples: List[float] = []
    dx_abs_samples: List[float] = []
    dy_abs_samples: List[float] = []
    peak_hits = 0
    force_hold_steps = 0

    dyn_thr_strength_last = float(lock_thr_strength_base)

    for _g in range(int(max(0, games))):
        prev_dist: Optional[float] = None
        lock_count = 0
        unlock_count = 0
        locked = False
        strength_ema = 0.0
        force_hold_left = 0

        # warmup prev frame
        prev_g = None
        b = hub.snapshot_jpeg()
        if b:
            snap_ok += 1
            prev_g = _jpeg_to_gray_small(b)
            if prev_g is None:
                decode_fail += 1
            else:
                decode_ok += 1
        else:
            snap_fail += 1

        for _s in range(int(max_steps)):
            total_steps += 1

            # read status
            st: Dict[str, Any] = {}
            try:
                st = hub.ptz_status() or {}
            except Exception:
                st = {}

            def _v(path: List[str], default: int = 0) -> int:
                cur: Any = st
                for p in path:
                    if not isinstance(cur, dict):
                        return default
                    cur = cur.get(p)
                try:
                    return int(cur)
                except Exception:
                    return default

            pan = _v(["controls", "pan_absolute", "value"], 0)
            tilt = _v(["controls", "tilt_absolute", "value"], 0)
            zoom = _v(["controls", "zoom_absolute", "value"], 100)

            # frame
            b2 = hub.snapshot_jpeg()
            if b2:
                snap_ok += 1
                g2 = _jpeg_to_gray_small(b2)
                if g2 is None:
                    decode_fail += 1
                else:
                    decode_ok += 1
            else:
                snap_fail += 1
                g2 = None

            if prev_g is None or g2 is None:
                dx = dy = 0.0
                strength = 0.0
                motion_norm = 0.0
            else:
                dx, dy, strength, motion_norm = _motion_centroid(prev_g, g2, thr=int(motion_thr))
            prev_g = g2

            strength = float(_clamp(float(strength), 0.0, 1.0))
            dx = float(_clamp(float(dx), -1.0, 1.0))
            dy = float(_clamp(float(dy), -1.0, 1.0))
            dist = float(math.sqrt(dx * dx + dy * dy))

            strength_samples.append(strength)
            dx_abs_samples.append(abs(dx))
            dy_abs_samples.append(abs(dy))

            # EMA update
            if total_steps <= 1:
                strength_ema = strength
            else:
                a = float(_clamp(strength_ema_alpha, 0.01, 0.50))
                strength_ema = (a * strength) + ((1.0 - a) * float(strength_ema))
            strength_ema_samples.append(float(strength_ema))

            total_dist += dist

            # Motion diagnostics
            motion_norm = float(motion_norm)
            motion_norm_samples.append(motion_norm)
            if (strength > 0.0) or (motion_norm >= float(motion_norm_min)):
                motion_ok += 1
            else:
                motion_zero += 1

            # Adaptive motion threshold (windowed p95)
            if (total_steps >= int(adapt_after_steps)) and (motion_thr > motion_thr_min) and motion_norm_samples:
                z = float(motion_zero) / float(max(1, motion_ok + motion_zero))
                win = motion_norm_samples[-min(64, len(motion_norm_samples)) :]
                win_p95 = _p95(win)
                if (z >= float(adapt_zero_ratio)) and (win_p95 >= float(motion_norm_min)):
                    motion_thr = max(int(motion_thr_min), int(motion_thr) - int(motion_thr_step))

            # Dynamic lock strength threshold from recent p95
            recent_p95 = _p95(strength_samples[-min(64, len(strength_samples)) :])
            dyn_thr_strength = float(lock_thr_strength_scale) * float(recent_p95)
            dyn_thr_strength = float(_clamp(dyn_thr_strength, float(lock_thr_strength_min), float(lock_thr_strength_base)))
            dyn_thr_strength_last = float(dyn_thr_strength)

            # Candidate decision
            peak_thr = float(dyn_thr_strength) * float(_clamp(peak_trigger_mul, 1.1, 3.0))
            peak = (strength >= peak_thr) and (dist <= float(lock_thr_dist))
            cand = ((float(strength_ema) >= float(dyn_thr_strength)) or peak) and (dist <= float(lock_thr_dist))
            if cand:
                lock_candidate += 1
            if peak:
                peak_hits += 1
                force_hold_left = max(int(force_hold_left), int(peak_force_hold_steps))

            sh = _state_hash(dx, dy, strength, pan, tilt, zoom)

            # choose action (policy + eps explore)
            a_act = shim.choose(sh, legal)
            if eps > 0.0 and rng.random() < float(eps):
                a_act = rng.choice(legal)

            # Sticky hold for Explore
            if learn:
                if force_hold_left > 0:
                    a_act = "hold"
                    force_hold_left -= 1
                    force_hold_steps += 1
                elif cand and (rng.random() < float(_clamp(sticky_hold_p, 0.0, 1.0))):
                    a_act = "hold"

            # execute
            if a_act == "hold":
                holds += 1
            else:
                moves += 1
                try:
                    amt = int(amount)
                    if learn and (cand or locked):
                        amt = max(1, int(round(float(amt) * 0.5)))
                    hub.ptz_command(a_act, int(amt))
                except Exception:
                    pass

            # reward (dist improve)
            if prev_dist is None:
                d_improve = 0.0
            else:
                d_improve = float(prev_dist) - dist
            prev_dist = dist
            r = float(d_improve) * float(_clamp(strength, 0.0, 1.0))
            if a_act != "hold":
                r -= float(move_cost)
            total_reward += r

            # lock/unlock debounce
            if cand:
                lock_count += 1
                unlock_count = 0
            else:
                unlock_count += 1
                lock_count = 0

            if (not locked) and (lock_count >= int(max(1, lock_debounce))):
                locked = True
                lock_hits += 1
            if locked and (unlock_count >= int(max(1, unlock_debounce))):
                locked = False
                unlock_events += 1

            # learn
            if learn and shim.have_up and a_act:
                lbl = f"d={dist:.3f}|st={strength:.2f}|a={a_act}"
                learned_items += shim.learn_many([{"state_hash": sh, "action": str(a_act), "label": lbl}])

            if dt_ms > 0:
                time.sleep(max(0.0, float(dt_ms) / 1000.0))

    t1 = _now_ts()
    dur_ms = int((t1 - t0) * 1000)
    avg_step_ms = (dur_ms / max(1.0, float(total_steps)))

    # motion_norm stats (safe without numpy)
    motion_norm_mean = 0.0
    motion_norm_p95 = 0.0
    if motion_norm_samples:
        try:
            motion_norm_mean = float(sum(motion_norm_samples) / float(len(motion_norm_samples)))
            motion_norm_p95 = _p95(motion_norm_samples)
        except Exception:
            motion_norm_mean = 0.0
            motion_norm_p95 = 0.0

    strength_mean = float(sum(strength_samples) / max(1, len(strength_samples))) if strength_samples else 0.0
    strength_p95 = _p95(strength_samples) if strength_samples else 0.0
    strength_ema_mean = float(sum(strength_ema_samples) / max(1, len(strength_ema_samples))) if strength_ema_samples else 0.0
    strength_ema_p95 = _p95(strength_ema_samples) if strength_ema_samples else 0.0
    dx_abs_mean = float(sum(dx_abs_samples) / max(1, len(dx_abs_samples))) if dx_abs_samples else 0.0
    dy_abs_mean = float(sum(dy_abs_samples) / max(1, len(dy_abs_samples))) if dy_abs_samples else 0.0

    return {
        "ts_start": t0,
        "ts_end": t1,
        "duration_ms": float(dur_ms),
        "games": int(games),
        "steps": int(total_steps),
        "moves": int(moves),
        "holds": int(holds),
        "avg_step_ms": float(avg_step_ms),
        "avg_reward": float(total_reward / max(1.0, float(total_steps))),
        "avg_dist": float(total_dist / max(1.0, float(total_steps))),
        "lock_rate": float(lock_hits / max(1.0, float(games))),
        "mode": ("explore" if learn else "policy"),
        "namespace": str(namespace),
        "policy_enabled": 1.0,
        "eps": float(eps),
        "learn": bool(learn),
        "learned_items": int(learned_items),
        "snap_ok": int(snap_ok),
        "snap_fail": int(snap_fail),
        "decode_ok": int(decode_ok),
        "decode_fail": int(decode_fail),
        "motion_ok": int(motion_ok),
        "motion_zero": int(motion_zero),
        "motion_thr_final": int(motion_thr),
        "motion_norm_mean": float(motion_norm_mean),
        "motion_norm_p95": float(motion_norm_p95),
        "lock_hits": int(lock_hits),
        "unlock_events": int(unlock_events),
        "lock_candidate": int(lock_candidate),
        "lock_thr_strength": float(dyn_thr_strength_last),
        "lock_thr_dist": float(lock_thr_dist),
        "strength_mean": float(strength_mean),
        "strength_p95": float(strength_p95),
        "strength_ema_mean": float(strength_ema_mean),
        "strength_ema_p95": float(strength_ema_p95),
        "dx_abs_mean": float(dx_abs_mean),
        "dy_abs_mean": float(dy_abs_mean),
        "peak_hits": int(peak_hits),
        "force_hold_steps": int(force_hold_steps),
        "max_steps": int(max_steps),
        "dt_ms": int(dt_ms),
        "amount": int(amount),
        "source": str(source),
        "shim": "PolicyShim(core.universal_policy.Policy)",
    }


def _write_to_db(kind: str, label: str, res: Dict[str, Any]) -> Optional[int]:
    meta = {
        "runner": "tools/ptz_target_daily_runner.py",
        "ts_start": int(res.get("ts_start") or _now_ts()),
        "ts_end": int(res.get("ts_end") or _now_ts()),
        "namespace": res.get("namespace"),
        "mode": res.get("mode"),
        "eps": res.get("eps"),
        "dt_ms": res.get("dt_ms"),
        "amount": res.get("amount"),
        "max_steps": res.get("max_steps"),
        "source": res.get("source"),
        "motion_thr_final": res.get("motion_thr_final"),
    }
    ep_id = _insert_episode(kind=kind, label=label, meta=meta)
    if not ep_id:
        return None
    ts = int(res.get("ts_end") or _now_ts())

    keys = (
        "games",
        "steps",
        "moves",
        "holds",
        "avg_step_ms",
        "avg_reward",
        "avg_dist",
        "lock_rate",
        "duration_ms",
        "eps",
        "amount",
        "max_steps",
        "learned_items",
        "snap_ok",
        "snap_fail",
        "decode_ok",
        "decode_fail",
        "motion_ok",
        "motion_zero",
        "motion_thr_final",
        "motion_norm_mean",
        "motion_norm_p95",
        "lock_hits",
        "unlock_events",
        "lock_candidate",
        "lock_thr_strength",
        "lock_thr_dist",
        "strength_mean",
        "strength_p95",
        "strength_ema_mean",
        "strength_ema_p95",
        "dx_abs_mean",
        "dy_abs_mean",
        "peak_hits",
        "force_hold_steps",
    )

    for k in keys:
        if k in res:
            try:
                _insert_metric(ep_id, k, float(res[k]), ts)
            except Exception:
                pass
    return int(ep_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_PTZ_TARGET_POLICY_GAMES", 10))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_PTZ_TARGET_EXPLORE_GAMES", 10))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--http-base", type=str, default=os.environ.get("OROMA_PTZ_HTTP_BASE", "http://127.0.0.1:8080"))
    args = ap.parse_args()

    namespace = "ptz:target"
    dt_ms = _env_int("OROMA_PTZ_TARGET_DT_MS", 250)
    amount = _env_int("OROMA_PTZ_TARGET_AMOUNT", 10)
    max_steps = _env_int("OROMA_PTZ_TARGET_MAX_STEPS", 40)
    eps = _env_float("OROMA_PTZ_TARGET_EPS", 0.08)
    explore_moves_per_game = _env_int("OROMA_PTZ_TARGET_EXPLORE_MOVES_PER_GAME", 1)

    rng = random.Random(int(args.seed))

    out: Dict[str, Any] = {"ok": True, "have_up": True, "db_written": False}

    policy_res = run_batch(
        rng=rng,
        namespace=namespace,
        games=int(args.policy_games),
        eps=0.0,
        learn=False,
        explore_moves_per_game=0,
        dt_ms=dt_ms,
        amount=amount,
        max_steps=max_steps,
        http_base=str(args.http_base),
        source="orchestrator",
    )
    explore_res = run_batch(
        rng=rng,
        namespace=namespace,
        games=int(args.explore_games),
        eps=float(eps),
        learn=True,
        explore_moves_per_game=int(explore_moves_per_game),
        dt_ms=dt_ms,
        amount=amount,
        max_steps=max_steps,
        http_base=str(args.http_base),
        source="orchestrator",
    )

    out["policy"] = policy_res
    out["explore"] = explore_res

    try:
        ep1: Optional[int] = None
        if int(args.policy_games) > 0:
            ep1 = _write_to_db("game:ptz_target:policy_batch", f"ptz_target:policy ({int(args.policy_games)} games)", policy_res)
            if ep1:
                out["policy"]["episode_id"] = int(ep1)

        ep2 = _write_to_db("game:ptz_target:explore_batch", f"ptz_target:explore ({int(args.explore_games)} games)", explore_res)
        if ep2:
            out["explore"]["episode_id"] = int(ep2)

        out["db_written"] = bool(ep2) and (True if int(args.policy_games) == 0 else bool(ep1))
    except Exception:
        out["db_written"] = False

    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("db_written") else 2


if __name__ == "__main__":
    raise SystemExit(main())

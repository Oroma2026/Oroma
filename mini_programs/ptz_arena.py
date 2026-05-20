#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/mini_programs/ptz_arena.py
# Projekt:   ORÓMA (PTZ-Arena · Policy-Training · Headless)
# Version:   v3.7.3
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# Zweck
# ─────
#   Dieses Modul implementiert eine kleine, produktive RL-Umgebung (“Arena”) für
#   die PTZ-Kamera. Ziel ist nicht “perfektes Tracking”, sondern:
#     • schnelles, stabiles Lernen im vorhandenen UniversalPolicy-System
#     • Telemetrie über episodes/episodic_metrics (Daily policy+explore)
#     • Headless-Betrieb (kein Qt/Wayland/X11)
#
#   Die Arena nutzt ausschließlich bereits vorhandene Schnittstellen:
#     • DeviceHub.get_hub() → ptz_status(), ptz_command(), get_latest_frame()
#     • Kein eigener Kamera-Startzwang (ensure_start=False beim Frame-Pull)
#     • Keine UI-Abhängigkeit: Env ist CLI/DailyRunner-fähig.
#
# Design (absichtlich simpel)
# ─────────────────────────
#   - Actions: left/right/up/down/zoom_in/zoom_out/hold (+ optional center)
#   - State:   diskretisierte Bins aus PTZ-Status + Motion/Sharpness Score
#   - Reward:  Δ(score) - move_cost
#
#   Score (Default):
#     score = sharp_norm - motion_norm
#     → “ruhig und scharf” wird positiv.
#
# Produktiv-Invarianten
# ────────────────────
#   - PTZ-Kommandos werden best-effort ausgeführt (Exceptions → fail-closed).
#   - Frame-Scoring darf keinen Kamera-Start triggern.
#   - Bewegungsrate ist begrenzt (dt_ms, amount, max_steps) via ENV.
#
# ENV
# ───
#   OROMA_PTZ_ARENA_AMOUNT             (Default 10)  – Nudge-Amount (UI+Runner)
#   OROMA_PTZ_ARENA_DT_MS              (Default 250) – Wartezeit zwischen pre/post Frames
#   OROMA_PTZ_ARENA_MAX_STEPS          (Default 60)  – Steps pro Episode
#   OROMA_PTZ_ARENA_MOTION_CLIP        (Default 8.0) – Clip für Motion-Norm
#   OROMA_PTZ_ARENA_SHARP_CLIP         (Default 400.0) – Clip für Sharpness-Norm
#   OROMA_PTZ_ARENA_MOVE_COST          (Default 0.01) – Kosten pro Move != hold
#   OROMA_PTZ_ARENA_USE_CENTER_ACTION  (Default 0) – center als Action erlauben
#
# Hinweis
# ───────
#   Diese Arena ist bewusst “klein” und lernt typischerweise sehr schnell
#   einfache, robuste Regeln (z.B. nicht zittern, nicht zoomen spammen).
#   Später kann man den Reward um Coverage/Targeting erweitern.
# =============================================================================

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore


def _env_int(name: str, default: int) -> int:
    try:
        v = int(str(os.environ.get(name, str(default))).strip())
        return v
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        v = float(str(os.environ.get(name, str(default))).strip())
        return v
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.environ.get(name, "" if not default else "1")).strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on")


def _to_gray_small(frame_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Robuste, schnelle Pre-Processing Pipeline für Motion/Sharpness."""
    if frame_bgr is None:
        return None
    if cv2 is None:
        return None
    try:
        # Downscale (reduziert CPU) + Grayscale
        h, w = frame_bgr.shape[:2]
        scale = 256.0 / max(1.0, float(max(h, w)))
        nw = max(32, int(w * scale))
        nh = max(32, int(h * scale))
        small = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return gray
    except Exception:
        return None


def _motion_score(g1: np.ndarray, g2: np.ndarray) -> float:
    """Mean absolute difference between consecutive grayscale frames."""
    try:
        if cv2 is not None:
            diff = cv2.absdiff(g1, g2)
            return float(np.mean(diff))
        diff = np.abs(g1.astype(np.int16) - g2.astype(np.int16))
        return float(np.mean(diff))
    except Exception:
        return 0.0


def _sharpness_score(gray: np.ndarray) -> float:
    """Variance of Laplacian as a simple sharpness proxy."""
    if cv2 is None:
        return 0.0
    try:
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        return float(lap.var())
    except Exception:
        return 0.0


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


@dataclass
class ArenaObs:
    pan: int
    tilt: int
    zoom: int
    motion: float
    sharp: float
    score: float


class PTZArenaEnv:
    """PTZ Arena Environment (headless, production-safe)."""

    ACTIONS_BASE = ["left", "right", "up", "down", "zoom_in", "zoom_out", "hold"]

    def __init__(self, hub=None):
        self.hub = hub
        self.amount = max(1, _env_int("OROMA_PTZ_ARENA_AMOUNT", 10))
        self.dt_ms = int(max(50, min(2000, _env_int("OROMA_PTZ_ARENA_DT_MS", 250))))
        self.max_steps = int(max(5, min(2000, _env_int("OROMA_PTZ_ARENA_MAX_STEPS", 60))))
        self.motion_clip = float(max(0.1, _env_float("OROMA_PTZ_ARENA_MOTION_CLIP", 8.0)))
        self.sharp_clip = float(max(1.0, _env_float("OROMA_PTZ_ARENA_SHARP_CLIP", 400.0)))
        self.move_cost = float(max(0.0, _env_float("OROMA_PTZ_ARENA_MOVE_COST", 0.01)))
        self.use_center_action = _env_bool("OROMA_PTZ_ARENA_USE_CENTER_ACTION", False)

        self._steps = 0
        self._last_action = "hold"
        self._last_obs: Optional[ArenaObs] = None

    def legal_actions(self) -> List[str]:
        acts = list(self.ACTIONS_BASE)
        if self.use_center_action:
            acts.append("center")
        return acts

    def reset(self, do_center: bool = False) -> ArenaObs:
        self._steps = 0
        self._last_action = "hold"
        if do_center:
            try:
                if self.hub:
                    self.hub.ptz_command("center", amount=1)
            except Exception:
                pass
        self._last_obs = self._observe()
        return self._last_obs

    def step(self, action: str) -> Tuple[ArenaObs, float, bool, Dict[str, Any]]:
        if not action:
            action = "hold"
        action = str(action).strip().lower()
        if action not in self.legal_actions():
            action = "hold"

        pre = self._observe()
        moved = False
        if action != "hold":
            try:
                if self.hub:
                    self.hub.ptz_command(action, amount=int(self.amount))
                    moved = True
            except Exception:
                moved = False

        try:
            time.sleep(self.dt_ms / 1000.0)
        except Exception:
            pass
        post = self._observe()

        reward = float(post.score - pre.score)
        if moved:
            reward -= float(self.move_cost)

        self._steps += 1
        self._last_action = action
        self._last_obs = post
        done = self._steps >= self.max_steps

        info = {
            "steps": self._steps,
            "action": action,
            "moved": moved,
            "pre": pre.__dict__,
            "post": post.__dict__,
        }
        return post, reward, done, info

    def state_hash(self, obs: ArenaObs) -> str:
        pan_b, tilt_b, zoom_b = self._ptz_bins(obs.pan, obs.tilt, obs.zoom)

        motion_n = _clip01(float(obs.motion) / self.motion_clip)
        sharp_n = _clip01(float(obs.sharp) / self.sharp_clip)
        score_n = _clip01((float(obs.score) + 1.0) / 2.0)

        m_bin = int(round(motion_n * 10.0))
        s_bin = int(round(sharp_n * 10.0))
        sc_bin = int(round(score_n * 10.0))
        la = (self._last_action or "hold")[:8]
        return f"p{pan_b:02d}t{tilt_b:02d}z{zoom_b:02d}m{m_bin:02d}s{s_bin:02d}q{sc_bin:02d}la{la}"

    # ---------------- internals ----------------
    def _ptz_bins(self, pan: int, tilt: int, zoom: int) -> Tuple[int, int, int]:
        st: Dict[str, Any] = {}
        try:
            if self.hub:
                st = self.hub.ptz_status() or {}
        except Exception:
            st = {}

        pan_min, pan_max = -540000, 540000
        tilt_min, tilt_max = -324000, 324000
        zoom_min, zoom_max = 100, 150
        try:
            pr = st.get("pan_range") or {}
            pan_min = int(pr.get("min", pan_min))
            pan_max = int(pr.get("max", pan_max))
        except Exception:
            pass
        try:
            tr = st.get("tilt_range") or {}
            tilt_min = int(tr.get("min", tilt_min))
            tilt_max = int(tr.get("max", tilt_max))
        except Exception:
            pass
        try:
            zr = st.get("zoom_range") or {}
            zoom_min = int(zr.get("min", zoom_min))
            zoom_max = int(zr.get("max", zoom_max))
        except Exception:
            pass

        def _bin(v: int, vmin: int, vmax: int, bins: int) -> int:
            if vmax <= vmin:
                return 0
            v = max(vmin, min(vmax, int(v)))
            x = (float(v) - float(vmin)) / (float(vmax) - float(vmin))
            b = int(round(x * float(bins - 1)))
            return max(0, min(bins - 1, b))

        pan_b = _bin(pan, pan_min, pan_max, 13)
        tilt_b = _bin(tilt, tilt_min, tilt_max, 7)
        zoom_b = _bin(zoom, zoom_min, zoom_max, 8)
        return pan_b, tilt_b, zoom_b

    def _observe(self) -> ArenaObs:
        pan = 0
        tilt = 0
        zoom = 100
        try:
            if self.hub:
                st = self.hub.ptz_status() or {}
                pan = int(st.get("pan", 0) or 0)
                tilt = int(st.get("tilt", 0) or 0)
                zoom = int(st.get("zoom", 100) or 100)
        except Exception:
            pass

        motion = 0.0
        sharp = 0.0
        score = 0.0
        try:
            if self.hub is not None:
                f1, _ = self.hub.get_latest_frame(ensure_start=False)
                if f1 is not None:
                    try:
                        time.sleep(max(0.02, self.dt_ms / 1000.0 * 0.2))
                    except Exception:
                        pass
                    f2, _ = self.hub.get_latest_frame(ensure_start=False)
                    if f2 is not None:
                        g1 = _to_gray_small(f1)
                        g2 = _to_gray_small(f2)
                        if g1 is not None and g2 is not None:
                            motion = float(_motion_score(g1, g2))
                            sharp = float(_sharpness_score(g2))
        except Exception:
            pass

        motion_n = _clip01(float(motion) / self.motion_clip)
        sharp_n = _clip01(float(sharp) / self.sharp_clip)
        score = float(sharp_n - motion_n)
        if score < -1.0:
            score = -1.0
        if score > 1.0:
            score = 1.0
        return ArenaObs(pan=pan, tilt=tilt, zoom=zoom, motion=motion, sharp=sharp, score=score)

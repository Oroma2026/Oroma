#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/ptz_arena_daily_runner.py
# Projekt:   ORÓMA (PTZ Arena · Daily Self-Play Runner)
# Version:   v3.7.3
# Stand:     2026-02-21
# Autor:     ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# Lizenz:    MIT
# =============================================================================
#
# Zweck
# ─────
#   Führt 1× täglich (über Orchestrator) PTZ-Arena Episoden aus und schreibt
#   Ergebnisse in die ORÓMA DB (episodes + episodic_metrics).
#
#   Standard wie bei den Games:
#     - policy_batch:  N games, eps=0, learn=false
#     - explore_batch: N games, eps>0, learn=true
#
#   Namespace:
#     - UniversalPolicy Namespace: "ptz:arena"
#     - Episodes kind:
#         game:ptz_arena:policy_batch
#         game:ptz_arena:explore_batch
#
# Produktionshinweise
# ──────────────────
#   - Mechanische Schonung: Default max_steps=60 und dt_ms=250.
#     Das bedeutet pro Episode ~15s und 100 Episoden ~25 Minuten.
#     (anpassbar via ENV/CLI)
#   - Kein Kamera-Startzwang: DeviceHub.get_latest_frame(ensure_start=False)
#
# CLI
# ───
#   python3 tools/ptz_arena_daily_runner.py --policy-games 5 --explore-games 5 --seed 1
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _now_ts() -> int:
    return int(time.time())


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
        # UniversalPolicy.learn_many() is best-effort and may return None.
        # Treat None as 0 learned items to avoid TypeErrors in callers.
        try:
            res = self.pol.learn_many(items)
        except Exception:
            return 0
        if res is None:
            return 0
        try:
            return int(res)
        except Exception:
            return 0


class HttpHub:
    """Minimal Hub-Adapter über die laufende ORÓMA-HTTP-API.

    Hintergrund
    -----------
    Im laufenden oroma.service ist die PTZ-Kamera bereits korrekt als
    Videoquelle konfiguriert (Video-UI zeigt /dev/video8, PTZ ranges etc.).
    Wenn der DailyRunner jedoch einen neuen DeviceHub initialisiert, kann ein
    Default-Backend greifen (z.B. picamera2 dev=0). Dann bleiben Motion/Sharpness
    im Runner typischerweise 0.0, obwohl die UI Werte liefert.

    Lösung
    ------
    Der Runner nutzt daher (Default) die bereits laufenden Video-Endpunkte:
      - GET  /video/api/ptz/status
      - POST /video/api/ptz/command
      - GET  /video/snapshot.jpg
    Damit ist die Frame-Quelle identisch zur UI.
    """

    def __init__(self, base_url: str, timeout_sec: float = 3.0):
        self.base_url = (base_url or "http://127.0.0.1:8080").rstrip("/")
        self.timeout_sec = float(timeout_sec)

    def ptz_status(self) -> Dict[str, Any]:
        url = f"{self.base_url}/video/api/ptz/status"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_sec) as r:
                data = r.read()
            return json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            return {"ok": False, "supported": False, "device": "", "error": "ptz_status_failed"}

    def ptz_command(self, action: str, amount: int = 1, device: str = "") -> Dict[str, Any]:
        url = f"{self.base_url}/video/api/ptz/command"
        payload = json.dumps({"action": str(action), "amount": int(amount), "device": str(device or "")}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as r:
                data = r.read()
            return json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            return {"ok": False, "error": "ptz_command_failed"}

    def get_latest_frame(self, ensure_start: bool = False, client: str = "ptz_arena_runner"):
        url = f"{self.base_url}/video/snapshot.jpg"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_sec) as r:
                jpeg = r.read()
        except Exception:
            return None, None

        try:
            import numpy as np
            import cv2  # type: ignore
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return None, None
            return frame, _now_ts()
        except Exception:
            return None, None
        try:
            res = self.pol.learn_many(items)
            if res is None:
                return 0
            return int(res)
        except Exception:
            return 0


def _write_episode(kind: str, label: str, meta: Dict[str, Any], metrics: Dict[str, float]) -> bool:
    try:
        from core.sql_manager import insert_episode, insert_episodic_metric  # type: ignore
        ts = int(meta.get("ts_start", _now_ts()))
        ep_id = insert_episode(kind=kind, label=label, ts_start=ts, ts_end=int(meta.get("ts_end", ts)), meta=meta)
        if not ep_id:
            return False
        for k, v in metrics.items():
            try:
                insert_episodic_metric(int(ep_id), int(meta.get("ts_end", ts)), str(k), float(v))
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"[ptz_arena_daily_runner] DB write failed: {e!r}")
        return False


def run_batch(*, rng: random.Random, games: int, mode: str, eps: float, learn: bool, max_steps: int, dt_ms: int, amount: int, source: str,
              use_http_hub: bool, http_base: str) -> Dict[str, Any]:
    from mini_programs.ptz_arena import PTZArenaEnv  # type: ignore

    namespace = "ptz:arena"
    shim = PolicyShim(namespace)
    hub = None
    if use_http_hub:
        hub = HttpHub(base_url=http_base)
    else:
        # Fallback: direkter DeviceHub (kann je nach Systemkonfig auf Default-Kamera zeigen)
        from core.device_hub import get_hub  # type: ignore
        hub = get_hub()
    env = PTZArenaEnv(hub=hub)
    env.max_steps = int(max_steps)
    env.dt_ms = int(dt_ms)
    env.amount = int(amount)

    ts0 = _now_ts()
    t0 = time.perf_counter()

    total_steps = 0
    total_reward = 0.0
    total_motion = 0.0
    total_sharp = 0.0
    total_score = 0.0
    moves = 0
    holds = 0
    learned_items = 0

    legal = env.legal_actions()

    for _ in range(int(games)):
        obs = env.reset(do_center=False)
        done = False
        while not done:
            sh = env.state_hash(obs)
            a = "hold"
            if mode == "policy":
                a = shim.choose(sh, legal)
            else:
                if rng.random() < float(eps):
                    a = rng.choice(legal)
                else:
                    a = shim.choose(sh, legal)

            obs2, reward, done, info = env.step(a)
            obs = obs2

            total_steps += 1
            total_reward += float(reward)
            total_motion += float(getattr(obs, "motion", 0.0) or 0.0)
            total_sharp += float(getattr(obs, "sharp", 0.0) or 0.0)
            total_score += float(getattr(obs, "score", 0.0) or 0.0)
            if str(a) == "hold":
                holds += 1
            else:
                moves += 1

            if learn and shim.have_up:
                label = 1 if reward > 0.0 else (-1 if reward < 0.0 else 0)
                learned_items += shim.learn_many([{ "state_hash": sh, "action": str(a), "label": label }])

    t1 = time.perf_counter()
    ts1 = _now_ts()

    duration_ms = int(round((t1 - t0) * 1000.0))
    avg_step_ms = float(duration_ms) / float(max(1, total_steps))
    avg_reward = float(total_reward) / float(max(1, total_steps))

    res = {
        "ts_start": ts0,
        "ts_end": ts1,
        "duration_ms": duration_ms,
        "games": int(games),
        "steps": int(total_steps),
        "moves": int(moves),
        "holds": int(holds),
        "avg_step_ms": float(avg_step_ms),
        "avg_reward": float(avg_reward),
        "avg_motion": float(total_motion) / float(max(1, total_steps)),
        "avg_sharp": float(total_sharp) / float(max(1, total_steps)),
        "avg_score": float(total_score) / float(max(1, total_steps)),
        "mode": str(mode),
        "namespace": namespace,
        "policy_enabled": 1.0 if shim.have_up else 0.0,
        "eps": float(eps),
        "learn": bool(learn),
        "learned_items": int(learned_items),
        "max_steps": int(max_steps),
        "dt_ms": int(dt_ms),
        "amount": int(amount),
        "source": str(source),
        "label": f"ptz_arena:{mode} ({games} games)",
        "runner": "tools/ptz_arena_daily_runner.py",
    }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=_env_int("OROMA_ORCH_PTZ_ARENA_POLICY_GAMES", 100))
    ap.add_argument("--explore-games", type=int, default=_env_int("OROMA_ORCH_PTZ_ARENA_EXPLORE_GAMES", 100))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_PTZ_ARENA_MAX_STEPS", 60))
    ap.add_argument("--dt-ms", type=int, default=_env_int("OROMA_PTZ_ARENA_DT_MS", 250))
    ap.add_argument("--amount", type=int, default=_env_int("OROMA_PTZ_ARENA_AMOUNT", 10))
    ap.add_argument("--eps", type=float, default=float(os.environ.get("OROMA_PTZ_ARENA_EPS", "0.08")))
    ap.add_argument("--http-base", type=str, default=str(os.environ.get("OROMA_PTZ_ARENA_HTTP_BASE", "http://127.0.0.1:8080")))
    ap.add_argument("--no-http-hub", action="store_true", help="Nicht über /video/* arbeiten, sondern DeviceHub direkt initialisieren (fallback).")
    args = ap.parse_args()

    use_http_hub = (not bool(args.no_http_hub))

    rng = random.Random(int(args.seed))

    policy_res = run_batch(
        rng=rng,
        games=int(args.policy_games),
        mode="policy",
        eps=0.0,
        learn=False,
        max_steps=int(args.max_steps),
        dt_ms=int(args.dt_ms),
        amount=int(args.amount),
        source="orchestrator",
        use_http_hub=use_http_hub,
        http_base=str(args.http_base),
    )
    explore_res = run_batch(
        rng=rng,
        games=int(args.explore_games),
        mode="explore",
        eps=float(args.eps),
        learn=True,
        max_steps=int(args.max_steps),
        dt_ms=int(args.dt_ms),
        amount=int(args.amount),
        source="orchestrator",
        use_http_hub=use_http_hub,
        http_base=str(args.http_base),
    )

    # DB write
    db_ok = True
    for res, kind in ((policy_res, "game:ptz_arena:policy_batch"), (explore_res, "game:ptz_arena:explore_batch")):
        meta = dict(res)
        metrics = {
            "games": float(res.get("games", 0)),
            "steps": float(res.get("steps", 0)),
            "moves": float(res.get("moves", 0)),
            "holds": float(res.get("holds", 0)),
            "duration_ms": float(res.get("duration_ms", 0)),
            "avg_step_ms": float(res.get("avg_step_ms", 0.0)),
            "avg_reward": float(res.get("avg_reward", 0.0)),
            "avg_motion": float(res.get("avg_motion", 0.0)),
            "avg_sharp": float(res.get("avg_sharp", 0.0)),
            "avg_score": float(res.get("avg_score", 0.0)),
            "eps": float(res.get("eps", 0.0)),
            "policy_enabled": float(res.get("policy_enabled", 0.0)),
            "max_steps": float(res.get("max_steps", 0)),
            "dt_ms": float(res.get("dt_ms", 0)),
            "amount": float(res.get("amount", 0)),
        }
        ok = _write_episode(kind=kind, label=str(res.get("label")), meta=meta, metrics=metrics)
        if not ok:
            db_ok = False

    out = {
        "ok": bool(db_ok),
        "have_up": bool(policy_res.get("policy_enabled", 0.0) > 0.0 or explore_res.get("policy_enabled", 0.0) > 0.0),
        "db_written": bool(db_ok),
        "policy": policy_res,
        "explore": explore_res,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if db_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

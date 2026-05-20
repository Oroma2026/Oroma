#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/vision_scene_infer_hook.py
# Projekt: ORÓMA (Headless)
# Version: v3.8-r1 – Kamera-Szenen Inference (KMeans-Modelle)
# Stand:   2025-10-23
# Zweck:   Laufend aktuelle Kamera-Features in Cluster-ID übersetzen und
#          als metrics schreiben (cam:scene:id, cam:scene:conf).
# ENV:     OROMA_VISION_INFER=1
#          OROMA_VISION_INFER_EVERY_TICKS=8
# =============================================================================
from __future__ import annotations
import os, time, logging
import numpy as np
from typing import Optional, Tuple
from core import sql_manager
from wrappers import oroma_wrapper

LOG = logging.getLogger("oroma.vision_infer")
if not LOG.handlers:
    import sys; h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[vision_infer] %(levelname)s: %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.WARNING if os.getenv("OROMA_HOOKS_LOG","0") in ("0","false","no") else logging.INFO)

_ENABLE = os.getenv("OROMA_VISION_INFER","0") in ("1","true","yes")
_EVERY  = int(os.getenv("OROMA_VISION_INFER_EVERY_TICKS","8"))

# Cache
_MODEL_PATH: Optional[str] = None
_MU = _SIG = _C = None
_MTIME = 0.0

def _load_latest_model() -> bool:
    global _MODEL_PATH, _MU, _SIG, _C, _MTIME
    with sql_manager.get_conn() as conn:
        row = conn.execute(
            "SELECT hef_path, created_at FROM models WHERE task=? AND family=? AND status='active' ORDER BY id DESC LIMIT 1",
            ("vision_scene","kmeans_np")
        ).fetchone()
    if not row: return False
    path = row["hef_path"]
    if not path or not os.path.isfile(path): return False
    mtime = os.path.getmtime(path)
    if _MODEL_PATH == path and _MTIME == mtime and _MU is not None:
        return True
    data = np.load(path)
    _MU, _SIG, _C = data["mu"], data["sigma"], data["centroids"]
    _MODEL_PATH, _MTIME = path, mtime
    LOG.info("Modell geladen: %s", path)
    return True

def _infer(vec) -> Optional[Tuple[int, float]]:
    if _MU is None: return None
    x = np.asarray(vec, dtype=np.float32)
    if x.ndim != 1: return None
    x = (x - _MU) / _SIG
    d = ((x[None,:] - _C)**2).sum(axis=1)  # (k,)
    j = int(np.argmin(d))
    # Konfidenz aus Distanz (einfacher Softmax auf neg. Dist.)
    w = np.exp(-d); p = float(w[j]/(w.sum()+1e-12))
    return j, p

def vision_scene_infer_hook(dt: float, tick: int) -> None:
    if not _ENABLE or (tick % max(1,_EVERY) != 0): return
    if not _load_latest_model(): return
    try:
        ow = oroma_wrapper.OromaWrapper()
        emb = ow.embed(frame=None)
        vec = emb.get("vec") or emb.get("features") or []
        if not vec: return
        pred = _infer(vec)
        if not pred: return
        cid, conf = pred
        ts = int(time.time())
        sql_manager.insert_metric("cam:scene:id", float(cid), ts)
        sql_manager.insert_metric("cam:scene:conf", float(conf), ts)
        if LOG.isEnabledFor(logging.INFO):
            LOG.info("scene=%s conf=%.2f", cid, conf)
    except Exception as e:
        if LOG.isEnabledFor(logging.WARNING): LOG.warning("Hook Fehler: %s", e)
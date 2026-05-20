#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/cam_token_train.py
# Projekt: ORÓMA (Headless)
# Version: v3.8-r1 – Kamera-SnapToken Trainer (NumPy-KMeans)
# Stand:   2025-11-30
# Zweck:
#   Liest Kamera-SnapToken-Vektoren (origin='vision/token') aus der Tabelle
#   snapchains, führt ein KMeans-Clustering mit NumPy durch und speichert das
#   Modell als .npz in OROMA_MODELS_DIR. Zusätzlich wird ein Eintrag in der
#   models-Tabelle erzeugt (task='vision_scene', family='kmeans_np').
#
# ENV:
#   OROMA_CAM_TRAIN_K           Anzahl Cluster (default: 5)
#   OROMA_CAM_TRAIN_MIN_Q       Mindestqualität der Tokens (default: 0.20)
#   OROMA_CAM_TRAIN_WINDOW_SEC  Zeitfenster in Sekunden (default: 86400 = 24h)
#   OROMA_MODELS_DIR            Zielverzeichnis für Models (.npz)
#
# CLI:
#   python3 -m core.cam_token_train --k 6 --window-sec 43200 --min-q 0.15
#
# Hinweise:
#   • Erwartet, dass der AV-SnapToken-Hook (hooks_av_snaptoken) aktiv ist und
#     cam_tokens mit origin='vision/token' in snapchains schreibt.
#   • Bricht sauber ab, wenn im Zeitfenster keine Vektoren vorliegen.
# =============================================================================
from __future__ import annotations

import os
import time
import json
import argparse
from typing import List, Tuple

import numpy as np

from core import sql_manager
from core.snaptoken import SnapToken


def _load_vectors(window_sec: int, min_q: float) -> np.ndarray:
    """
    Lädt SnapToken-Vektoren aus snapchains für origin='vision/token'
    innerhalb eines Zeitfensters [now - window_sec, now] und quality >= min_q.

    Rückgabe:
        X : np.ndarray der Form (n, d) oder leeres Array (0, 0), falls keine
            Vektoren gefunden wurden.
    """
    now = int(time.time())
    cutoff = now - int(window_sec)
    vecs: List[List[float]] = []

    with sql_manager.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT blob, quality
              FROM snapchains
             WHERE origin = ?
               AND ts >= ?
               AND quality >= ?
             ORDER BY id DESC
            """,
            ("vision/token", cutoff, float(min_q)),
        ).fetchall() or []

    for r in rows:
        try:
            token = SnapToken.from_blob(r["blob"])
            v = token.vec or []
            if v:
                vecs.append([float(x) for x in v])
        except Exception:
            # defekte Einträge werden übersprungen
            continue

    if not vecs:
        return np.empty((0, 0), dtype=np.float32)

    return np.array(vecs, dtype=np.float32)


def _kmeans_np(X: np.ndarray, k: int, iters: int = 50, seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    """
    Einfache KMeans-Implementierung mit NumPy.

    Parameter:
        X     : (n, d) Datenmatrix
        k     : Anzahl Cluster
        iters : maximale Anzahl Iterationen
        seed  : RNG-Seed für Reproduzierbarkeit

    Rückgabe:
        C      : (k, d) Clusterzentren
        labels : (n,) Clusterzuordnung pro Datenpunkt
    """
    rng = np.random.default_rng(seed)
    n, d = X.shape

    # Initiale Zentren zufällig aus den Daten
    idx = rng.choice(n, size=min(k, n), replace=False)
    C = X[idx].copy()
    prev = None

    for _ in range(iters):
        # Zuordnung: euklidische Distanz zu allen Zentren
        dists = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)  # (n, k)
        labels = dists.argmin(axis=1)

        # Zentren aktualisieren
        C_new = np.zeros_like(C)
        for j in range(C.shape[0]):
            m = (labels == j)
            if not np.any(m):
                # Leeres Cluster → neues Zentrum zufällig wählen
                C_new[j] = X[rng.integers(0, n)]
            else:
                C_new[j] = X[m].mean(axis=0)

        if prev is not None and np.allclose(C_new, prev, atol=1e-5):
            break

        prev = C
        C = C_new

    return C, labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=int(os.getenv("OROMA_CAM_TRAIN_K", "5")))
    ap.add_argument("--window-sec", type=int, default=int(os.getenv("OROMA_CAM_TRAIN_WINDOW_SEC", "86400")))
    ap.add_argument("--min-q", type=float, default=float(os.getenv("OROMA_CAM_TRAIN_MIN_Q", "0.20")))
    args = ap.parse_args()

    X = _load_vectors(args.window_sec, args.min_q)
    if X.size == 0:
        print("[cam_token_train] Keine Vektoren im Fenster – Abbruch.")
        return 0

    # Standardisierung (z-Score)
    mu = X.mean(axis=0)
    sigma = X.std(axis=0) + 1e-8
    Xn = (X - mu) / sigma

    k = max(2, min(args.k, X.shape[0]))
    C, labels = _kmeans_np(Xn, k=k, iters=50)

    models_dir = os.getenv("OROMA_MODELS_DIR", "/opt/ai/oroma/models")
    os.makedirs(models_dir, exist_ok=True)
    ts = int(time.time())
    path = os.path.join(models_dir, f"cam_kmeans_{ts}.npz")
    np.savez(path, mu=mu, sigma=sigma, centroids=C)

    # models-Eintrag
    pre = json.dumps({"features": "SnapToken.vec", "scale": "zscore"})
    post = json.dumps({"out": "cluster_id", "family": "kmeans_np"})
    with sql_manager.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO models
                   (task, family, version,
                    preproc_json, postproc_json,
                    labels_txt, hef_path,
                    created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("vision_scene", "kmeans_np", "v1", pre, post, None, path, ts, "active"),
        )
        conn.commit()

    print(f"[cam_token_train] OK – k={k}, n={X.shape[0]}, model={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
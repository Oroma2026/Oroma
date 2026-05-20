#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/ssl_contrastive.py
# Projekt: ORÓMA
# Version: v1.0 – Kontrastives Light-SSL für 9D-Tokens (Audio→Video)
# Stand:   2025-10-26
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Trainiert eine lineare Projektion W (9×9) zwischen Audio- und Video-Tokens
#   auf Basis zeitnaher Paare (|Δt| ≤ 2 s). Läuft für gewöhnlich in der Dream-Phase.
#
# ENV
# ───
#   OROMA_DB_PATH  (Default: /opt/ai/oroma/data/oroma.db)
#
# Output
# ──────
#   /opt/ai/oroma/data/ssl_W_audio2video.json
#
# Hinweise
# ────────
#   • Kein Torch/TF – reine NumPy/SQLite-Lösung (headless, ohne venv-Annahmen).
#   • Robust gegen BLOB/Text-Varianten in snapchains.blob.
# =============================================================================

from __future__ import annotations

import os
import json
import time
import sqlite3
from typing import List, Tuple, Optional
from core.log_guard import log_suppressed
import logging

import numpy as np

__all__ = ["train_linear_W", "save_W"]

DB = os.getenv("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db")


# --------------------------------------------------------------------------- #
# Datenzugriff & Paarbildung
# --------------------------------------------------------------------------- #

def _parse_blob_to_vec(blob) -> Optional[np.ndarray]:
    """
    Erwartet im blob eine JSON-Serialisierung mit Struktur:
      {"snaps":[{"features":[...]}], ...}
    Liefert ersten Feature-Vektor (bis 9D) oder None.
    """
    try:
        if isinstance(blob, (bytes, bytearray)):
            d = json.loads(blob.decode("utf-8", errors="ignore"))
        elif isinstance(blob, str):
            d = json.loads(blob)
        else:
            return None
        vec = d.get("snaps", [{}])[0].get("features", [])[:9]
        if vec and isinstance(vec, (list, tuple)):
            return np.asarray(vec, dtype=float)
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.ssl_contrastive.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    return None


def _fetch_pairs(since: int, max_pairs: int = 2000) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Lädt zeitnahe Audio/Video-Tokenpaare aus snapchains (|Δt| ≤ 2 s).
    Gibt Liste (a_vec, v_vec) zurück.
    """
    if not os.path.exists(DB):
        return []
    conn = sqlite3.connect(DB)
    try:
        A_rows = conn.execute(
            "SELECT ts, blob FROM snapchains WHERE origin='audio/token' AND ts>=? LIMIT 50000",
            (since,),
        ).fetchall()
        V_rows = conn.execute(
            "SELECT ts, blob FROM snapchains WHERE origin='vision/token' AND ts>=? LIMIT 50000",
            (since,),
        ).fetchall()

        def _vecs(rows):
            out = []
            for ts, blob in rows:
                v = _parse_blob_to_vec(blob)
                if v is not None and v.size >= 1:
                    out.append((int(ts), v))
            out.sort(key=lambda x: x[0])
            return out

        A = _vecs(A_rows)
        V = _vecs(V_rows)

        i = j = 0
        pairs: List[Tuple[np.ndarray, np.ndarray]] = []
        while i < len(A) and j < len(V):
            dt = V[j][0] - A[i][0]
            if abs(dt) <= 2:
                pairs.append((A[i][1], V[j][1]))
                i += 1
                j += 1
            elif dt > 2:
                i += 1
            else:
                j += 1
            if len(pairs) >= max_pairs:
                break
        return pairs
    finally:
        try:
            conn.close()
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.ssl_contrastive.pass.2",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )


# --------------------------------------------------------------------------- #
# Training: lineare Projektion + leichter InfoNCE-Feinschliff
# --------------------------------------------------------------------------- #

def train_linear_W(
    since: int,
    *,
    steps: int = 200,
    lr: float = 0.05,
    neg_k: int = 8,
    temperature: float = 0.1,
) -> Optional[np.ndarray]:
    """
    Lernt eine 9×9-Projektionsmatrix W, die Audio-Token in den Video-Raum abbildet.
    Pipeline:
      1) Paare ziehen (|Δt|≤2s)
      2) Least-Squares-Init
      3) Mini-Batch InfoNCE-Feinschliff (nur NumPy)

    Rückgabe: W (9×9) oder None, wenn zu wenig Daten.
    """
    pairs = _fetch_pairs(since)
    if len(pairs) < 30:
        return None

    A = np.stack([p[0] for p in pairs])  # N×9
    V = np.stack([p[1] for p in pairs])  # N×9

    # Least-Squares-Start
    W, *_ = np.linalg.lstsq(A, V, rcond=None)

    # Vorberechnungen
    Vnrm = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    rng = np.random.default_rng(42)

    batch = min(128, len(A))
    for _ in range(int(max(1, steps))):
        idx = rng.integers(0, len(A), size=batch)
        A_b = A[idx]
        V_pos = Vnrm[idx]

        # Projektion + Normierung
        Q = A_b @ W
        Q = Q / (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)

        # Negatives
        ni = rng.integers(0, len(V), size=(batch, int(max(1, neg_k))))
        V_neg = Vnrm[ni]  # [B, K, 9]

        # Kosinusähnlichkeiten
        pos = np.sum(Q * V_pos, axis=1, keepdims=True)          # [B,1]
        neg = np.einsum("bd,bkd->bk", Q, V_neg)                 # [B,K]

        # InfoNCE
        logits = np.concatenate([pos, neg], axis=1) / float(temperature)
        logits = logits - logits.max(axis=1, keepdims=True)     # Stabilität
        ex = np.exp(logits)
        p = ex / (np.sum(ex, axis=1, keepdims=True) + 1e-12)    # [B, 1+K]

        # Gradient wrt Q (auf Einheitssphäre tangent)
        gQ = (p[:, [0]] - 1.0) * V_pos + np.einsum("bk,bkd->bd", p[:, 1:], V_neg)
        gQ_tan = gQ - Q * np.sum(Q * gQ, axis=1, keepdims=True)

        # dL/dW = A_b^T @ dL/dQ
        grad = A_b.T @ gQ_tan / (batch + 1e-9)
        W = W + float(lr) * grad

    return W


# --------------------------------------------------------------------------- #
# Persistenz
# --------------------------------------------------------------------------- #

def save_W(W: np.ndarray, path: str = "/opt/ai/oroma/data/ssl_W_audio2video.json") -> None:
    """
    Speichert Matrix W als JSON (float-Listen). Verzeichnis wird bei Bedarf angelegt.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"W": [list(map(float, row)) for row in W]}, f, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Optionaler Selftest
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    since_24h = int(time.time()) - 24 * 3600
    W = train_linear_W(since_24h)
    if W is None:
        print("ssl_contrastive: zu wenig Paare – kein W erzeugt.")
    else:
        save_W(W)
        print("ssl_contrastive: W gespeichert → /opt/ai/oroma/data/ssl_W_audio2video.json")
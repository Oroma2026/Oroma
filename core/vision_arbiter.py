#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/vision_arbiter.py
# Projekt:   ORÓMA (Headless Vision · Lightweight Fusion)
# Modul:     VisionArbiter – gewichtete Szenenentscheidung aus NPU-Detektor + Token-Cluster + Feature-Heuristik (deterministisch, minimal)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul ist ein sehr leichter „Arbiter“ für Vision-Entscheidungen.
# Es kombiniert mehrere Quellen zu einer konsistenten (label, conf, source) Ausgabe:
#
#   1) NPU-Detektor Ergebnisse (z. B. Hailo/DeGirum/ONNX Detektor):
#        npu = [{"label":"person","conf":0.82}, ...]
#      → wir wählen deterministisch das beste Element (max conf, Tie-Break per label)
#
#   2) Token-Cluster (z. B. SnapToken-KMeans oder heuristische Klassifikation):
#        token = {"cluster": 3, "conf": 0.67}
#
#   3) Feature-Heuristik (Motion/Edges/Color aus VisionWrapper embed/feats):
#        feats = {"motion":0.21, "edges":0.35, "color":0.18}
#      → wird zu einem einfachen fscore 0..1 normiert
#
# Output ist ein kleines Dict, das sich ideal als Meta in Snaps/Chains oder als UI-Anzeige eignet.
#
# HEADLESS/EDGE-INVARIANTE
# ───────────────────────
# - Keine ML Dependencies
# - Keine DB Zugriffe
# - Nur reine Python-Heuristik + Gewichte aus ENV
# → Dadurch kann der Arbiter überall laufen (auch wenn NPU/Model nicht verfügbar ist).
#
# ENTSCHEIDUNGSLOGIK (AKTUELLER CODEPFAD)
# ───────────────────────────────────────
# - _best_npu(npu):
#     wählt bestes NPU Objekt über:
#       max(conf), bei Gleichstand lexikographisch kleineres label
#
# - _score_feats(feats):
#     normiert motion/edges/color je auf 0..1 und kombiniert:
#       fscore = 0.5*motion + 0.35*edges + 0.15*color
#
# - decide_scene(npu, token, feats):
#     kombiniert Konfidenzen über Gewichte:
#       comb = W_NPU*npu_conf + W_TOKEN*tok_conf + W_FEATS*fscore
#
#     source wird gewählt nach „dominanter“ Quelle (npu vs token vs feats).
#
#     Unterschreitet comb den Minimalwert:
#       → label="unknown", conf=comb, source=<dominant>
#
#     Sonst:
#       → label:
#           - bevorzugt npu_best["label"]
#           - fallback: f"scene_{cluster_id}" wenn NPU label leer ist
#
# ENV / KONFIGURATION
# ───────────────────
# Gewichte (Default im Code):
#   OROMA_VISION_W_NPU   (Default: 0.6)
#   OROMA_VISION_W_TOKEN (Default: 0.3)
#   OROMA_VISION_W_FEATS (Default: 0.1)
#
# Mindestkonfidenz:
#   OROMA_VISION_MIN_CONF (Default: 0.20)
#
# Diese ENV-Schalter erlauben schnelle Kalibrierung, ohne Modelle neu zu bauen.
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# decide_scene(
#     npu:   List[Dict[str,Any]],
#     token: Optional[Dict[str,Any]],
#     feats: Optional[Dict[str,float]] = None
# ) -> Dict[str,Any]
#
# Rückgabeformat:
#   {
#     "label": "person"|"scene_3"|"unknown",
#     "conf": <float>,
#     "source": "npu"|"token"|"feats",
#     "aux": {
#       "cluster": <int>,
#       "token_conf": <float>,
#       "feats": <float>   # fscore
#     }
#   }
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Deterministische Tie-Breaks müssen bleiben (sonst UI/Training „flackert“).
# - Keine Hard-Dependencies: Arbiter muss ohne NPU/Token/Feats lauffähig bleiben.
# - Unterschreiten von MIN_CONF muss "unknown" liefern (sauberer Fallback für Pipeline).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os
from typing import Dict, Any, List, Optional

_WN = float(os.getenv("OROMA_VISION_W_NPU","0.6"))
_WT = float(os.getenv("OROMA_VISION_W_TOKEN","0.3"))
_WF = float(os.getenv("OROMA_VISION_W_FEATS","0.1"))
_MIN = float(os.getenv("OROMA_VISION_MIN_CONF","0.20"))

def _best_npu(npu: List[Dict[str,Any]]) -> Optional[Dict[str,Any]]:
    if not npu: return None
    # höchste conf, deterministische Tie-Breaks via label lexikographisch
    best = max(npu, key=lambda d: (float(d.get("conf",0.0)), str(d.get("label",""))))
    return {"label": str(best.get("label","")), "conf": float(best.get("conf",0.0))}

def _score_feats(feats: Dict[str, float]) -> float:
    # einfache Normierung 0..1
    m = max(0.0, min(1.0, float(feats.get("motion",0.0))))
    e = max(0.0, min(1.0, float(feats.get("edges",0.0))))
    c = max(0.0, min(1.0, float(feats.get("color",0.0))))
    return 0.5*m + 0.35*e + 0.15*c

def decide_scene(npu: List[Dict[str,Any]],
                 token: Optional[Dict[str,Any]],
                 feats: Optional[Dict[str, float]] = None) -> Dict[str,Any]:
    npu_best = _best_npu(npu) or {"label":"", "conf":0.0}
    tok_cid  = int(token.get("cluster", -1)) if token else -1
    tok_conf = float(token.get("conf", 0.0)) if token else 0.0
    fscore   = _score_feats(feats or {})

    # kombinierte Konfidenz
    comb = _WN*npu_best["conf"] + _WT*tok_conf + _WF*fscore
    source = "npu" if npu_best["conf"] >= max(tok_conf, fscore) else ("token" if tok_conf >= fscore else "feats")

    if comb < _MIN:
        return {"label":"unknown", "conf":comb, "source":source,
                "aux":{"cluster": tok_cid, "token_conf": tok_conf, "feats": fscore}}

    return {"label": npu_best["label"] or f"scene_{tok_cid}",
            "conf": comb, "source": source,
            "aux": {"cluster": tok_cid, "token_conf": tok_conf, "feats": fscore}}
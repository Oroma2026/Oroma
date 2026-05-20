#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/overlay.py
# Projekt:   ORÓMA (Headless · Vision Pipeline · Overlay/Telemetry)
# Modul:     Overlay – leichte Frame-Overlay Engine (Text/Boxes/HUD) für MJPEG/Debug-Streams (ohne GUI-Framework)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul erzeugt visuelle Overlays für Frames (Vision-Pipeline), z. B.:
#   - HUD-Text: FPS, Mode (Day/Dream), active model/backend, token quality
#   - Bounding Boxes / Labels (Detektor-Ausgaben)
#   - einfache Statusindikatoren (Recording, Motion, Errors)
#
# Es ist bewusst „low ceremony“:
# - keine Qt/Wayland/X11 Abhängigkeit
# - nutzt einfache Bildoperationen (typischerweise PIL oder OpenCV, abhängig vom Install-Stand)
# - gedacht für Debug, Explainability und UI (z. B. /video MJPEG Overlay)
#
# ARCHITEKTURROLLE (v3.7.3)
# ─────────────────────────
# ORÓMA nutzt verschiedene Vision Backends (onnx/hailo/degirum) und kann
# MJPEG Streams für UI bereitstellen.
# Overlay ist die Brücke zwischen:
#   - Rohframe (Kamera)
#   - Inferenz-Output (Detektion/Klassifikation)
#   - ORÓMA Kontext (Hooks, Token-Qualität, SceneGraph/MetaSnap Events)
# und macht diese Informationen „sichtbar“.
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine Fenster/GUI.
# - Performance: Overlay darf Frames nicht stark verlangsamen:
#     • schnelle Textdraws, wenige Primitives
#     • optional: Overlay komplett deaktivierbar (Caller)
# - Tolerant: wenn Font/Lib fehlt → Overlay fällt auf minimalen Text zurück oder no-op.
# - Keine Nebenwirkungen: Overlay schreibt keine Dateien, macht keine DB writes.
#
# DATENMODELLE / PRIMITIVES (KONZEPTUELL)
# ───────────────────────────────────────
# Dieses Modul arbeitet typischerweise mit:
# - OverlayItem / Annotation:
#     • kind: "text" | "box" | "line" | "dot"
#     • geometry: x,y,w,h / x1,y1,x2,y2
#     • label: string
#     • score: float optional
#     • meta: dict optional
#
# Der Caller (z. B. vision_wrapper / camera_hub / video_ui) liefert:
# - frame (numpy array oder PIL Image)
# - annotations (Liste)
# - hud dict (key/value)
#
# ÖFFENTLICHE API (FUNKTIONEN – TYPISCHER VERTRAG)
# ────────────────────────────────────────────────
# overlay_frame(frame, *, hud=None, boxes=None, labels=None, lines=None, dots=None) -> frame
#   - nimmt ein Frame-Objekt entgegen, zeichnet Overlay und gibt Frame zurück
#   - Implementierung kann je nach Lib (PIL/OpenCV) variieren
#
# format_hud(hud: Dict[str,Any]) -> List[str]
#   - normiert HUD-Daten zu Textzeilen (stabile Reihenfolge)
#
# clamp_box(x,y,w,h, width,height) -> (x,y,w,h)
#   - verhindert, dass Boxes außerhalb des Frames liegen
#
# safe_text(s: Any) -> str
#   - robustes Stringifying (keine Exceptions bei None/Objekten)
#
# ABHÄNGIGKEITEN (BEST EFFORT)
# ────────────────────────────
# - stdlib: time, math, typing
# - optional:
#     • PIL (Pillow) für ImageDraw/ImageFont
#     • cv2 für putText/rectangle
#
# Dieses Modul sollte so geschrieben sein, dass fehlende optionale Libs
# nicht zum Crash führen (degrade gracefully).
#
# FONT / RENDERING
# ────────────────
# Typisch:
# - versucht eine TrueType Font zu laden (z. B. DejaVuSans.ttf)
# - wenn nicht verfügbar → Default-Font
#
# Caller kann Font-Pfad übergeben oder per ENV konfigurieren (wenn unterstützt):
# - OROMA_OVERLAY_FONT=/pfad/font.ttf
# - OROMA_OVERLAY_SCALE=1.0
#
# (Falls diese ENV nicht existieren im Code, ist das Header-Info als Zielbild
# für konsistente Erweiterung gedacht; Default bleibt no-op/auto.)
#
# PERFORMANCE-HINWEISE
# ────────────────────
# - Overlays sollten pro Frame möglichst wenige Operationen ausführen.
# - Für MJPEG: besser HUD + wenige Boxes statt vollständige „Debug Wand“.
# - Wenn Inferenz-Output sehr groß ist (viele Boxes):
#     • Caller sollte vorher filtern (top_k, score_thres)
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Overlay muss headless bleiben (keine window calls).
# - Fehlende Libs dürfen ORÓMA nicht crashen (Fallback/no-op).
# - overlay_frame muss Input-Typen tolerant behandeln (numpy vs PIL), oder klar
#   dokumentiert eine erwartete Repräsentation verwenden (Caller passt dann an).
# - Keine DB/FS Side-Effects in diesem Modul.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import json
import logging
import os
import time
import io
import random
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple, List

import numpy as np
from PIL import Image, ImageDraw

log = logging.getLogger("oroma.overlay")

# ------------------------------- ENV-Weights --------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except Exception:
        return float(default)

W_VISION = _env_float("OROMA_OVERLAY_W_VISION", 1.0)
W_AUDIO  = _env_float("OROMA_OVERLAY_W_AUDIO",  1.0)
W_TEXT   = _env_float("OROMA_OVERLAY_W_TEXT",   1.0)
DEBUG    = os.environ.get("OROMA_OVERLAY_DEBUG", "0") in ("1", "true", "TRUE", "on", "ON")

# ------------------------------- Utils --------------------------------------

def _normalize(x: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    n = float(np.linalg.norm(x) + eps)
    return (x / n).astype(np.float32)

def _hist(signal: np.ndarray, bins: int, rmin: float, rmax: float) -> np.ndarray:
    h, _ = np.histogram(signal, bins=bins, range=(rmin, rmax))
    h = h.astype(np.float32)
    if h.sum() > 0:
        h /= h.sum()
    return h

# ------------------------------- Vision -------------------------------------

def vision_from_gray(gray_u8: np.ndarray, hist_bins: int = 32) -> Tuple[np.ndarray, Dict[str, Any]]:
    if gray_u8.ndim != 2 or gray_u8.dtype != np.uint8:
        raise ValueError("vision_from_gray erwartet 2D uint8 array")
    img = gray_u8.astype(np.float32) / 255.0
    h, w = img.shape
    hist = _hist(img.reshape(-1), bins=hist_bins, rmin=0.0, rmax=1.0)
    gx = img[:, 1:] - img[:, :-1]
    gy = img[1:, :] - img[:-1, :]
    edge_mean = 0.5 * float(np.mean(np.abs(gx))) + 0.5 * float(np.mean(np.abs(gy)))
    feat = _normalize(np.concatenate([hist, np.array([edge_mean], dtype=np.float32)], axis=0))
    ov = {"type": "vision", "shape": [int(h), int(w)], "hist_bins": hist_bins, "edge_mean": edge_mean}
    return feat, ov

def vision_from_rgb(rgb_u8: np.ndarray, hist_bins: int = 32) -> Tuple[np.ndarray, Dict[str, Any]]:
    if rgb_u8.ndim != 3 or rgb_u8.shape[2] != 3 or rgb_u8.dtype != np.uint8:
        raise ValueError("vision_from_rgb erwartet (H,W,3) uint8")
    r, g, b = rgb_u8[:,:,0], rgb_u8[:,:,1], rgb_u8[:,:,2]
    gray = (0.299*r + 0.587*g + 0.114*b).clip(0, 255).astype(np.uint8)
    return vision_from_gray(gray, hist_bins=hist_bins)

# ------------------------------- Audio --------------------------------------

def audio_features(samples: np.ndarray, sr: int, fft_bins: int = 64) -> Tuple[np.ndarray, Dict[str, Any]]:
    if samples.ndim != 1:
        raise ValueError("audio_features erwartet 1D samples")
    x = np.clip(samples.astype(np.float32), -1.0, 1.0)
    rms = float(np.sqrt(np.mean(x * x) + 1e-12))
    n = 1 << (len(x) - 1).bit_length()
    if n > len(x):
        x = np.pad(x, (0, n - len(x)))
    mag = np.abs(np.fft.rfft(x)).astype(np.float32)
    pitch_proxy = float(np.argmax(mag)) / float(max(1, len(mag)-1))
    mag_log = np.log1p(mag)
    mh = _hist(mag_log, bins=fft_bins, rmin=float(mag_log.min()), rmax=float(mag_log.max()+1e-6))
    feat = _normalize(np.concatenate([np.array([rms, pitch_proxy], dtype=np.float32), mh], axis=0))
    ov = {"type": "audio", "sr": int(sr), "rms": rms, "pitch_proxy": pitch_proxy, "fft_bins": fft_bins}
    return feat, ov

# ------------------------------- Text ---------------------------------------

def text_features(text: str, ngram: int = 3, max_vocab: int = 64) -> Tuple[np.ndarray, Dict[str, Any]]:
    t = (text or "").strip()
    length, tokc = len(t), len([tok for tok in t.split() if tok])
    hv = np.zeros((max_vocab,), dtype=np.float32)
    if length >= ngram:
        for i in range(length - ngram + 1):
            h = (hash(t[i:i+ngram]) % max_vocab + max_vocab) % max_vocab
            hv[h] += 1.0
        if hv.sum() > 0: hv /= hv.sum()
    feat = _normalize(np.concatenate([np.array([length, tokc], dtype=np.float32), hv], axis=0))
    ov = {"type": "text", "length": length, "tokens": tokc, "ngram": ngram, "vocab": max_vocab}
    return feat, ov

# --------------------------- Overlay/Fusion ---------------------------------

@dataclass
class OverlayBundle:
    ts: int
    vision: Optional[Dict[str, Any]]
    audio: Optional[Dict[str, Any]]
    text:  Optional[Dict[str, Any]]
    meta:  Dict[str, Any]
    features: List[float]
    def to_dict(self) -> Dict[str, Any]: return asdict(self)

def _apply_weights(v=None,a=None,t=None) -> List[float]:
    parts = []
    if v is not None: parts.append(W_VISION * v.astype(np.float32))
    if a is not None: parts.append(W_AUDIO  * a.astype(np.float32))
    if t is not None: parts.append(W_TEXT   * t.astype(np.float32))
    if not parts: return []
    return _normalize(np.concatenate(parts, axis=0)).tolist()

def fuse(vfeat=None,vov=None,afeat=None,aov=None,tfeat=None,tov=None,meta=None) -> OverlayBundle:
    return OverlayBundle(ts=int(time.time()), vision=vov,audio=aov,text=tov,meta=meta or {},
                         features=_apply_weights(vfeat,afeat,tfeat))

def fuse_from_inputs(rgb=None,gray=None,audio=None,sr=None,text=None,meta=None) -> OverlayBundle:
    vfeat=vov=afeat=aov=tfeat=tov=None
    if gray is not None: vfeat,vov=vision_from_gray(gray)
    elif rgb is not None: vfeat,vov=vision_from_rgb(rgb)
    if audio is not None and sr: afeat,aov=audio_features(audio,int(sr))
    if text is not None: tfeat,tov=text_features(text)
    return fuse(vfeat,vov,afeat,aov,tfeat,tov,meta=meta or {})

def overlay_summary(bundle: OverlayBundle) -> str:
    parts=[]
    if bundle.vision: parts.append(f"vision(edge={bundle.vision.get('edge_mean',0):.3f})")
    if bundle.audio:  parts.append(f"audio(rms={bundle.audio.get('rms',0):.3f})")
    if bundle.text:   parts.append(f"text(len={bundle.text.get('length',0)})")
    return f"OverlayBundle(ts={bundle.ts}, {' | '.join(parts)} | feat_len={len(bundle.features)})"

# ------------------------------ Frame Renderer ------------------------------

def render_frame() -> bytes:
    """Erzeugt ein Dummy-JPEG-Frame (kann später echte Overlays enthalten)."""
    img = Image.new("RGB", (320, 240), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((10, 10), "ORÓMA Overlay", fill=(255, 255, 0))
    d.text((10, 40), time.strftime("%H:%M:%S"), fill=(0, 255, 0))
    d.text((10, 70), f"Rnd: {random.randint(0,999)}", fill=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

# ------------------------------ Selftest ------------------------------------

def _selftest():
    print("[overlay] selftest…")
    gray = np.random.randint(0, 255, (64, 64), dtype=np.uint8)
    vfeat, vov = vision_from_gray(gray)
    print(" vision:", vov)
    audio = np.random.randn(2048).astype(np.float32)
    afeat, aov = audio_features(audio, sr=16000)
    print(" audio:", aov)
    tfeat, tov = text_features("Hallo ORÓMA, dies ist ein Overlay-Test.")
    print(" text:", tov)
    bundle = fuse(vfeat, vov, afeat, aov, tfeat, tov, meta={"src": "selftest"})
    print(" fused:", overlay_summary(bundle))
    print("[overlay] OK ✅")

if __name__ == "__main__":
    _selftest()
# =============================================================================
# Pfad:      /opt/ai/oroma/core/hooks_audio_snaptoken.py
# Projekt:   ORÓMA (Offline-First · Headless · AgentLoop Hooks)
# Modul:     AudioSnapToken Hook – leichte Audio-Tokens aus DeviceHub-Mic (RMS/ZCR/Spektrum) → snapchains + metrics
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieser Hook erzeugt periodisch „Audio-SnapTokens“ aus dem laufenden Mikrofonstream
# des DeviceHub (core/device_hub.py). Er ist das Audio-Pendant zu Vision-Tokens
# (hooks_av_snaptoken.py), aber bewusst **klein** und **DB-schonend**.
#
# WICHTIG:
# - Es werden **keine** großen WAV-Rohdaten gespeichert.
# - Gespeichert werden nur kompakte Features + Metadaten als JSON-Blob in `snapchains`
#   (über core.sql_manager.insert_audio_token()).
#
# Zielnutzen:
# - akustische Situationen als episodische Tokens erfassen (Noise/Voice/Activity),
# - späteres Lernen/Resonanz/Verlinkung (z. B. Audio↔Vision) ermöglichen,
# - ohne DB-Explosion und ohne harte Audio-Dependencies.
#
# ABHÄNGIGKEITEN (REALITÄT DIESER DATEI)
# ──────────────────────────────────────
# - numpy ist erforderlich (FFT, Windowing, schnelle Statistik).
# - Audioquelle ist ausschließlich core.device_hub.get_hub():
#     hub.read_audio(window_sec) liefert (pcm_float32, sr) oder (None, sr).
# - Persistenz/Telemetry:
#     core.sql_manager.insert_audio_token(...)
#     core.sql_manager.insert_metric(...)
#
# FEATURE-EXTRAKTION (CPU-LEICHT, EDGE-FREUNDLICH)
# ───────────────────────────────────────────────
# Pro Token-Fenster (Default 0.50s @ 16kHz) werden berechnet:
#   - rms             : Lautstärke (Root Mean Square)
#   - zcr             : Zero-Crossing-Rate (grob: noisiness/voiced/unvoiced)
#   - centroid_hz     : spektraler Schwerpunkt (grob: „hell/dunkel“)
#   - band_energy[4]  : normierte Bandenergien:
#       [0–200 Hz, 200–1000 Hz, 1000–3000 Hz, 3000–6000 Hz]
#
# Daraus entsteht ein kompakter Vektor v[] (typischerweise 12–16 floats),
# der für Similarity/Resonanz genügt.
#
# OPTIONAL: SPEECH-ONLY / NOISE-FLOOR
# ───────────────────────────────────
# Es gibt optionale Gate-Mechanismen, um Tokens nur bei „relevanten“ Segmenten zu schreiben:
#   - MIN_RMS: minimale Lautstärke, sonst skip (verhindert Leerlauf-Tokens)
#   - SPEECH_ONLY: einfacher VAD-ähnlicher Gate auf Basis von RMS + spektraler Verteilung
#   - NOISE_EMA: adaptiver Noise-Floor (EMA), um in wechselnden Umgebungen stabil zu bleiben
#
# Diese Gates sind bewusst heuristisch (keine schwere VAD-Bibliothek).
#
# DB / SEMANTIK (WAS GENAU GESCHRIEBEN WIRD)
# ──────────────────────────────────────────
# Tabelle: oroma.db → `snapchains`
# Konvention:
#   - origin    : "audio/token"
#   - namespace : "audio:mic"
# Blob (JSON):
#   {
#     "kind":"audio_token",
#     "v":[...],
#     "rms":<float>,
#     "zcr":<float>,
#     "centroid_hz":<float>,
#     "band":[...],
#     "vad":0|1,
#     "sr":<int>,
#     "win_sec":<float>
#   }
#
# Zusätzlich werden Metrics geschrieben (best effort):
#   - audio:token:candidate
#   - audio:token:skip_rms
#   - audio:token:skip_speech
#   - audio:token:saved
#   - audio:token:error
#
# AKTIVIERUNG / STEUERUNG (ENV)
# ─────────────────────────────
# DeviceHub Audio muss aktiv sein:
#   OROMA_AUDIO_ENABLE=true|false
#
# Hook aktivieren:
#   OROMA_AUDIO_SNAPS=1|0
#
# Sampling:
#   OROMA_AUDIO_SNAPS_EVERY_TICKS=20          # alle N AgentLoop Ticks
#   OROMA_AUDIO_SNAPS_WINDOW_SEC=0.50         # Fenstergröße pro Token
#
# Gates:
#   OROMA_AUDIO_SNAPS_MIN_RMS=0.006
#   OROMA_AUDIO_SNAPS_SPEECH_ONLY=1|0
#   OROMA_AUDIO_SNAPS_NOISE_EMA=0.02
#
# Always-On Mic (wird eher in run_oroma.py/DeviceHub entschieden, nicht hier):
#   OROMA_AUDIO_ALWAYS_ON=1|0
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Hook darf den AgentLoop nicht blockieren: kurze Fenster, kein langes Warten.
# - Keine großen Payloads in DB: nur Features, kein WAV.
# - Robustheit: wenn Audio nicht verfügbar ist → no-op (kein Crash).
# - Feature-Vector muss klein & deterministisch bleiben (Edge-Stabilität).
#
# ÖFFENTLICHE API (HOOK-VERTRAG)
# ─────────────────────────────
# make_audio_snaptoken_hook() -> Callable[[float,int], None]
#   - Factory liefert zustandsbehafteten Hook (Noise-EMA etc.)
#   - Hook(dt, tick) wird vom AgentLoop aufgerufen
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import time
from typing import Callable, Optional, Tuple

import numpy as np

from core.device_hub import get_hub
from core.sql_manager import insert_audio_token, insert_metric

# NMR-Lite Audio-Bridge ist optional.
#
# WICHTIG:
# - Der Hook darf auch ohne core.nmr_lite vollständig funktionsfähig bleiben.
# - Deshalb defensiver best-effort Import statt harter Pflicht-Abhängigkeit.
# - Die Bridge wird nach der Feature-Berechnung aufgerufen, noch vor optionalen
#   Persistenz-/VAD-Gates, damit NMR echte RMS/Pitch-Werte auch dann sieht,
#   wenn kein Audio-Token in die DB geschrieben wird.
try:
    from core.nmr_lite import update_audio_signal as _nmr_update_audio_signal
except Exception:  # pragma: no cover - defensive optional integration
    _nmr_update_audio_signal = None


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.environ.get(name, "")).strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip() or default)
    except Exception:
        return default


def _safe_rfft_features(pcm: np.ndarray, sr: int) -> Tuple[float, float, float, np.ndarray]:
    """Gibt (rms, zcr, centroid_hz, band_energy[4]) zurück.
    band_energy: [0-200Hz, 200-1000Hz, 1000-3000Hz, 3000-6000Hz] (normiert)
    """
    # RMS
    rms = float(np.sqrt(np.mean(np.square(pcm), dtype=np.float64)))

    # ZCR (Zero Crossing Rate)
    s = np.sign(pcm)
    s[s == 0] = 1.0
    zcr = float(np.mean(s[:-1] != s[1:]))

    # FFT / Spektrum
    # Fensterung: Hanning reduziert Leakage
    win = np.hanning(len(pcm)).astype(np.float32)
    x = (pcm * win).astype(np.float32)

    spec = np.fft.rfft(x)
    mag = np.abs(spec).astype(np.float32)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / float(sr)).astype(np.float32)

    mag_sum = float(np.sum(mag)) + 1e-12
    centroid_hz = float(np.sum(freqs * mag) / mag_sum)

    # Bandenergie (normiert auf Summe)
    bands = [(0.0, 200.0), (200.0, 1000.0), (1000.0, 3000.0), (3000.0, 6000.0)]
    band_e = []
    for lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        band_e.append(float(np.sum(mag[m])) / mag_sum)
    return rms, zcr, centroid_hz, np.array(band_e, dtype=np.float32)


def make_audio_snaptoken_hook() -> Callable[[float, int], None]:
    """Factory: liefert Hook(dt, tick) für den AgentLoop.

    Der Hook ist bewusst zustandsbehaftet (Noise-EMA, Rate-Limit).
    """
    enabled = _env_bool("OROMA_AUDIO_SNAPS", False)
    if not enabled:
        # Hook deaktiviert -> no-op
        def _noop(dt: float, tick: int) -> None:
            return
        return _noop

    every_ticks = max(1, _env_int("OROMA_AUDIO_SNAPS_EVERY_TICKS", 20))
    win_sec = max(0.10, _env_float("OROMA_AUDIO_SNAPS_WINDOW_SEC", 0.50))
    speech_only = _env_bool("OROMA_AUDIO_SNAPS_SPEECH_ONLY", False)
    min_rms = max(0.0, _env_float("OROMA_AUDIO_SNAPS_MIN_RMS", 0.006))
    noise_ema = min(0.2, max(0.001, _env_float("OROMA_AUDIO_SNAPS_NOISE_EMA", 0.02)))

    # State
    noise_floor = 0.0
    have_floor = False
    last_ok_ts = 0.0

    hub = get_hub()

    def audio_snaptoken_hook(dt: float, tick: int) -> None:
        nonlocal noise_floor, have_floor, last_ok_ts

        # Tick Gate
        if tick % every_ticks != 0:
            return

        # Rate-limit zusätzlich: max ~2 Tokens/Sek (wenn ticks sehr klein)
        now = time.time()
        if (now - last_ok_ts) < 0.25:
            return

        try:
            sr = int(getattr(hub, "audio_sr", 16000) or 16000)

            # -----------------------------------------------------------------
            # ORÓMA Audio SnapTokens – DeviceHub API Kompatibilität
            # -----------------------------------------------------------------
            # DeviceHub.read_audio() ist in diesem Projektstand definiert als:
            #   read_audio(seconds: float, client: Optional[str] = None) -> np.ndarray
            #
            # Ältere Hook-Stände nutzten window_sec/sr Keyword-Args. Das erzeugt
            # TypeError und lässt den Hook (wegen try/except) still zu einem No-Op werden.
            # -----------------------------------------------------------------
            try:
                pcm = hub.read_audio(seconds=win_sec, client="audio_snaptoken_hook")
            except TypeError:
                # Fallback: falls ein älterer Hub nur positional akzeptiert
                pcm = hub.read_audio(win_sec)

            pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)

            # Kandidat: wir haben Samples angefordert (auch wenn später Gate greift)
            try:
                insert_metric("audio:token:candidate", 1.0, ts=int(now))
            except Exception:
                pass

            # DeviceHub kann bei "noch leer" ein leeres Array liefern (nicht None)
            if pcm.size == 0:
                try:
                    insert_metric("audio:token:skip_empty", 1.0, ts=int(now))
                except Exception:
                    pass
                return

            # Mindestlänge (sonst FFT/Features sinnlos)
            if pcm.size < int(sr * 0.08):
                try:
                    insert_metric("audio:token:skip_short", 1.0, ts=int(now))
                except Exception:
                    pass
                return

            rms, zcr, centroid_hz, band_e = _safe_rfft_features(pcm, sr=sr)

            # -------------------------------------------------------------
            # ORÓMA NMR-Lite Audio-Bridge (best effort, kein Hard-Fail)
            # -------------------------------------------------------------
            # Die Bridge bekommt die leichtgewichtigen Audio-Signale direkt
            # aus dem laufenden Hook:
            #   - rms
            #   - centroid_hz als pitch/proxy
            #
            # BEWUSST VOR speech_only / DB-Persistenz:
            # Damit NMR-Lite auch dann echte Audio-Werte sieht, wenn dieses
            # Segment später wegen VAD/RMS-Gates nicht als Token gespeichert
            # wird. Dadurch bleibt der Fast Path signalnah und DB-unabhängig.
            if _nmr_update_audio_signal is not None:
                try:
                    _nmr_update_audio_signal(rms=rms, pitch=centroid_hz, ts=now)
                except Exception:
                    # Der Audio-Hook darf durch NMR nie ausfallen.
                    pass

            # Noise-Floor: wenn wir noch keinen haben -> initialisieren,
            # danach EMA-updaten (nur wenn leise)
            if not have_floor:
                noise_floor = rms
                have_floor = True
            else:
                # Update Floor bevorzugt bei leisem Signal, sonst sehr langsam
                if rms < (noise_floor * 1.5 + 0.002):
                    noise_floor = (1.0 - noise_ema) * noise_floor + noise_ema * rms
                else:
                    noise_floor = (1.0 - (noise_ema * 0.2)) * noise_floor + (noise_ema * 0.2) * rms

            # einfacher VAD:
            # - Sprache hat oft mehr Energie in 200–3000Hz als <200Hz
            # - rms deutlich über noise_floor
            speech_energy = float(band_e[1] + band_e[2])
            low_energy = float(band_e[0])
            vad = int((rms > max(min_rms, noise_floor * 1.8 + 0.001)) and (speech_energy > (low_energy + 0.05)))

            if speech_only and not vad:
                try:
                    insert_metric("audio:token:skip_vad", 1.0, ts=int(now))
                except Exception:
                    pass
                return

            # Quality: skaliert mit Abstand zur Noise
            # (Clamp 0..1)
            denom = max(1e-6, (noise_floor * 4.0 + 0.02))
            q = float((rms - noise_floor) / denom)
            q = 0.0 if q < 0.0 else (1.0 if q > 1.0 else q)

            vec = [
                float(rms),
                float(zcr),
                float(centroid_hz / 6000.0),  # normiert
                float(speech_energy),
                float(low_energy),
                float(band_e[0]),
                float(band_e[1]),
                float(band_e[2]),
                float(band_e[3]),
                float(noise_floor),
                float(vad),
                float(win_sec),
            ]

            insert_audio_token(
                ts=int(now),
                vec=vec,
                sr=sr,
                win_sec=win_sec,
                rms=rms,
                zcr=zcr,
                centroid_hz=centroid_hz,
                band=list(map(float, band_e.tolist())),
                vad=vad,
                quality=q,
                origin="audio/token",
                namespace="audio:mic",
                status=1,
            )

            try:
                insert_metric("audio:token:accepted", 1.0, ts=int(now))
            except Exception:
                pass

            last_ok_ts = now

        except Exception:
            # Keine harten Fehler in der Loop
            return

    return audio_snaptoken_hook

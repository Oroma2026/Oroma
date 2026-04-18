# SnapToken Hooks – Vision & Audio Sampling / Vision & Audio Sampling Hooks

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt die beiden AgentLoop-Hooks, die **periodisch SnapToken-Ereignisse erzeugen**:
- `core/hooks_av_snaptoken.py` (Vision/Kamera → “cam_token” SnapChain-Insert)
- `core/hooks_audio_snaptoken.py` (Audio/Mikrofon → “audio/token” Token-Insert)

Beide Hooks sind bewusst **headless**, **best-effort** und **DB-lock-robust**, damit sie im 24/7 Betrieb nicht als “Hard-Failure” wirken.

### Scope / Nicht-Ziele
- ✅ In scope: Aktivierungs-/Rate-Limit-Logik, Quality/Motion/VAD Gates, DBWriter-first Verhalten, FastDB-Insert, Telemetrie-Metriken.
- ❌ Out of scope: genaue Tabellen-Spezifikation (nur konzeptuell), Vision-Embedding-Algorithmen (Wrapper), ASR/VAD-Modelle (hier nur Heuristik), UI-Anzeige.

---

## DE – Vision Hook: `hooks_av_snaptoken.py`

### Architekturrolle
Konzeptuell:
**VisionWrapper/OromaWrapper.embed() → (motion/edges/color/q) → cam_token → persistieren → Metrics**

Der Hook erzeugt keine “SnapToken” im Sinne von `snaptoken.py`, sondern persistiert einen **cam_token** als **SnapChain-Row** (`snapchains` Tabelle) mit `origin="vision/token"`.

### Aktivierung & Sampling
- Tick-Gate: nur jeder `_EVERY`-te Tick (`OROMA_AV_SNAPS_EVERY_TICKS`)
- Tageslimit: maximal `_MAX_PER_DAY` Tokens pro Tag (`OROMA_AV_SNAPS_MAX_PER_DAY`)

### Quality / Motion Gates
- Optionales Motion-Gate: `OROMA_AV_MIN_MOTION`  
  Wenn gesetzt (>0), wird bei zu wenig Motion früh beendet (reduziert redundante Tokens).
- Minimum Quality Gate: `OROMA_AV_SNAPS_MIN_Q`  
  `q` wird entweder vom Wrapper geliefert oder aus Motion/Edges heuristisch berechnet.

**Robustness-Fix (v3.7.3+):**  
Wenn ein Vektor existiert, aber `q/motion/edges` fehlen (z.B. External-Frame-Setups), wird `q` als “unknown” behandelt und auf `_MIN_Q` gesetzt, damit die Pipeline nicht komplett blockiert.

### Persistenzpfad (FastDB vs Legacy)
- `OROMA_AV_FASTDB=1` → **FastDB Insert**:
  - bevorzugt **DBWriter** (`OROMA_DBW_ENABLE=1`) über IPC (kein lokales sqlite connect im Hook-Pfad)
  - falls DBWriter nicht verfügbar/fehlschlägt: lokaler sqlite3 insert mit kurzen Timeouts (`busy_timeout`, optional `WAL`)
- `OROMA_AV_FASTDB=0` → Legacy Insert über `sql_manager.insert_cam_token(...)` (kann länger blockieren)

### Payload & Schema (konzeptuell)
Der Hook schreibt eine `snapchains` Row mit:
- `ts` (sek), `quality=q`
- `blob` JSON: `{"kind":"cam_token","v":[...],"motion":...,"edges":...,"color":...}`
- `origin="vision/token"`, `namespace="vision"`, `notes="cam_token"`, `version="v3.8"`

### Telemetrie / Metrics (best-effort)
- `cam:token:candidate`
- `cam:token:q`
- `cam:token:skip_motion`
- `cam:token:skip_q` / `cam:token:skip_quality`
- `cam:token:saved` / `cam:token:accepted`
- `cam:token:db_locked` (FastDB lock path)

### Relevante ENVs (Vision)
- `OROMA_AV_SNAPS`, `OROMA_AV_SNAPS_EVERY_TICKS`, `OROMA_AV_SNAPS_MAX_PER_DAY`, `OROMA_AV_SNAPS_MIN_Q`
- `OROMA_AV_MIN_MOTION`, `OROMA_AV_MIN_QUALITY` (legacy)
- `OROMA_AV_FASTDB`, `OROMA_AV_DB_TIMEOUT_SEC`, `OROMA_AV_DB_BUSY_TIMEOUT_MS`
- `OROMA_DBW_ENABLE`, `OROMA_DB_PATH`, `OROMA_DB_WAL`, `OROMA_DB_BUSY_TIMEOUT_MS`
- `OROMA_HOOKS_LOG`

---

## DE – Audio Hook: `hooks_audio_snaptoken.py`

### Architekturrolle
Konzeptuell:
**DeviceHub.read_audio(seconds) → FFT Features (RMS/ZCR/Centroid/Bands) → VAD-Heuristik → persistieren → Metrics**

Der Audio-Hook ist als **Factory** gebaut:
- `make_audio_snaptoken_hook()` gibt `hook(dt, tick)` zurück
- Wenn deaktiviert, gibt er eine `_noop` Funktion zurück

### Aktivierung & Sampling
- Enable: `OROMA_AUDIO_SNAPS=1`
- Tick-Gate: `OROMA_AUDIO_SNAPS_EVERY_TICKS` (Default 20)
- Window: `OROMA_AUDIO_SNAPS_WINDOW_SEC` (Default 0.50)
- zusätzlicher Rate-Limit: max ~2 Tokens/s (min 0.25s Abstand)

### Feature-Extraktion (robust)
`_safe_rfft_features(pcm, sr)` liefert:
- `rms`, `zcr`, `centroid_hz`, `band_e` (4 Bandenergien)

Edge-Cases:
- leere Samples → `audio:token:skip_empty`
- zu kurze Fenster (< ~0.08s) → `audio:token:skip_short`

### Noise-Floor & VAD-Heuristik
- `noise_floor` wird initialisiert und über EMA gepflegt (`OROMA_AUDIO_SNAPS_NOISE_EMA`)
- VAD (heuristisch):  
  - rms deutlich über noise_floor + min_rms (`OROMA_AUDIO_SNAPS_MIN_RMS`)  
  - speech_energy (200–3000Hz) > low_energy (<200Hz) + margin

Optional:
- `OROMA_AUDIO_SNAPS_SPEECH_ONLY=1` → ohne VAD wird verworfen (`audio:token:skip_vad`)

### Persistenz
Der Hook ruft `insert_audio_token(...)` (SQL-Layer) und schreibt:
- `origin="audio/token"`, `namespace="audio:mic"`, `status=1`
- `vec` (12 floats): rms, zcr, centroid_norm, energies, noise_floor, vad, win_sec
- Zusatzfelder: `sr`, `band[]`, `quality=q`

### Telemetrie / Metrics
- `audio:token:candidate`
- `audio:token:skip_empty`, `audio:token:skip_short`, `audio:token:skip_vad`
- `audio:token:accepted`

### Relevante ENVs (Audio)
- `OROMA_AUDIO_SNAPS`, `OROMA_AUDIO_SNAPS_EVERY_TICKS`, `OROMA_AUDIO_SNAPS_WINDOW_SEC`
- `OROMA_AUDIO_SNAPS_SPEECH_ONLY`, `OROMA_AUDIO_SNAPS_MIN_RMS`, `OROMA_AUDIO_SNAPS_NOISE_EMA`
- `OROMA_AUDIO_ENABLE`, `OROMA_AUDIO_ALWAYS_ON` (um DeviceHub Audio zu halten)

---

## DE – Bezug zum Code
- Relevante Dateien:
  - `core/hooks_av_snaptoken.py`
  - `core/hooks_audio_snaptoken.py`
- Verwandte Core-Dokus:
  - `docs/core/12_snaptoken.md` (SnapToken Datenmodell)
  - `docs/core/10_snap.md`
  - `docs/core/24_snap_indexer.md` (Indexierungskonzept)

---

## EN

### Purpose
This document covers two AgentLoop hooks that **periodically emit token-like events**:
- `core/hooks_av_snaptoken.py` (vision/camera → “cam_token” snapchain insert)
- `core/hooks_audio_snaptoken.py` (audio/mic → “audio/token” insert)

Both hooks are intentionally **headless**, **best-effort**, and **DB-lock robust** to avoid hard failures in 24/7 operation.

### Scope / Non-goals
- ✅ In scope: enable/rate limiting, quality/motion/VAD gates, DBWriter-first behavior, fast insert path, telemetry metrics.
- ❌ Out of scope: full DB schema specs, vision embedding internals, model-based VAD/ASR, UI presentation.

---

## EN – Vision hook: `hooks_av_snaptoken.py`

### Role
Conceptually:
**OromaWrapper.embed() → (motion/edges/color/q) → cam_token → persist → metrics**

This hook does not create `SnapToken` objects from `snaptoken.py`. Instead, it persists a **cam_token** as a **snapchains table row** (`origin="vision/token"`).

### Enable & sampling
- tick gate: every `_EVERY` ticks (`OROMA_AV_SNAPS_EVERY_TICKS`)
- per-day cap: `_MAX_PER_DAY` (`OROMA_AV_SNAPS_MAX_PER_DAY`)

### Quality / motion gates
- optional motion gate: `OROMA_AV_MIN_MOTION`
- min quality gate: `OROMA_AV_SNAPS_MIN_Q`
- `q` comes from wrapper or is derived from motion/edges heuristics

**Robustness fix (v3.7.3+):**
If a vector exists but `q/motion/edges` are missing (external-frame setups), `q` is treated as “unknown” and set to `_MIN_Q` to prevent a full pipeline stall.

### Persistence (FastDB vs legacy)
- `OROMA_AV_FASTDB=1` → fast path:
  - prefer DBWriter IPC (`OROMA_DBW_ENABLE=1`)
  - fallback to local sqlite insert with short timeouts
- `OROMA_AV_FASTDB=0` → legacy `sql_manager.insert_cam_token(...)` (may block longer)

### Payload (conceptual)
`snapchains.blob` JSON:
`{"kind":"cam_token","v":[...],"motion":...,"edges":...,"color":...}`  
plus metadata columns (`quality`, `origin`, `namespace`, `notes`, `version`).

### Telemetry / metrics
- `cam:token:candidate`, `cam:token:q`
- `cam:token:skip_motion`, `cam:token:skip_q` / `cam:token:skip_quality`
- `cam:token:saved` / `cam:token:accepted`
- `cam:token:db_locked`

### Key env vars (vision)
- `OROMA_AV_SNAPS`, `OROMA_AV_SNAPS_EVERY_TICKS`, `OROMA_AV_SNAPS_MAX_PER_DAY`, `OROMA_AV_SNAPS_MIN_Q`
- `OROMA_AV_MIN_MOTION`, `OROMA_AV_FASTDB`, DB timeout/lock envs, `OROMA_DBW_ENABLE`, `OROMA_HOOKS_LOG`

---

## EN – Audio hook: `hooks_audio_snaptoken.py`

### Role
Conceptually:
**DeviceHub.read_audio(seconds) → FFT features → VAD heuristic → persist → metrics**

The audio hook is a factory:
- `make_audio_snaptoken_hook()` returns `hook(dt, tick)`
- when disabled, it returns a `_noop` hook.

### Enable & sampling
- enable: `OROMA_AUDIO_SNAPS=1`
- tick gate: `OROMA_AUDIO_SNAPS_EVERY_TICKS` (default 20)
- window: `OROMA_AUDIO_SNAPS_WINDOW_SEC` (default 0.50)
- additional rate limit: max ~2 tokens/sec (>=0.25s spacing)

### Feature extraction & gates
- `_safe_rfft_features` yields `rms`, `zcr`, `centroid_hz`, `band_e`
- empty/short windows are skipped with metrics
- noise floor is tracked via EMA (`OROMA_AUDIO_SNAPS_NOISE_EMA`)
- VAD heuristic uses speech-band vs low-band energy and rms vs noise floor
- optional `OROMA_AUDIO_SNAPS_SPEECH_ONLY=1` enforces VAD

### Persistence
Calls `insert_audio_token(...)` with:
- `origin="audio/token"`, `namespace="audio:mic"`, `status=1`
- `vec` (12 floats) plus extra fields (`sr`, `band[]`, `quality`)

### Telemetry / metrics
- `audio:token:candidate`
- `audio:token:skip_empty`, `audio:token:skip_short`, `audio:token:skip_vad`
- `audio:token:accepted`

### Key env vars (audio)
- `OROMA_AUDIO_SNAPS`, `OROMA_AUDIO_SNAPS_EVERY_TICKS`, `OROMA_AUDIO_SNAPS_WINDOW_SEC`
- `OROMA_AUDIO_SNAPS_SPEECH_ONLY`, `OROMA_AUDIO_SNAPS_MIN_RMS`, `OROMA_AUDIO_SNAPS_NOISE_EMA`
- `OROMA_AUDIO_ENABLE`, `OROMA_AUDIO_ALWAYS_ON`

---

## EN – Code mapping
- Relevant files:
  - `core/hooks_av_snaptoken.py`
  - `core/hooks_audio_snaptoken.py`
- Related core docs:
  - `docs/core/12_snaptoken.md`
  - `docs/core/10_snap.md`
  - `docs/core/24_snap_indexer.md`

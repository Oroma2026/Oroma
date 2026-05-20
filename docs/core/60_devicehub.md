# DeviceHub – Headless Sensor Hub / Headless Sensor Hub

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt den **Headless Device-/Sensor-Layer** von ORÓMA anhand der beiden zentralen Module:

- `core/device_hub.py` – **DeviceHub** als *Single Source of Truth* für Kamera, Light, Audio, Sessions, SensorChannels und Audit (v3.7.3).
- `core/camera_hub.py` – **CameraHub** als Kompatibilitäts-Bridge + Provider-Registry (Frame Injection) für ältere/hybride Komponenten.

Der Fokus liegt auf **Robustheit im 24/7 Edge-Betrieb**: keine GUI-Abhängigkeiten, defensive Imports, kurze Locks, auditierbare IO und ein Capture-Watchdog gegen “stalled camera”.

### Scope / Nicht-Ziele
- ✅ In scope: Kamera-Backends + Capture-Loop, External Frame Injection, Global Frame Cache (JPEG+JSON), Light (camera luma), Audio (PortAudio/sounddevice, Ringbuffer, WAV Record/Playback), Sessions, Audit-Logs, Watchdogs, Provider-Registry.
- ❌ Out of scope: UI-Templates, Vision-Model-Inferenz, PTZ-Attention-Policy (nur Schnittstellen/State-Paths), vollständige DeviceHub API-Doku jedes Helper.

---

## DE – Architekturrolle
Konzeptueller Fluss:
**UI/AgentLoop/Tools → DeviceHub (Kamera/Audio/Light) → Snap/Fusion/SnapChain**

DeviceHub ist absichtlich die einzige Stelle, die Hardware öffnet:
- verhindert doppelte Kamera-Opens (libcamera/Picamera2 kritisch)
- hält Audio im Ringbuffer stabil
- liefert “latest frame” und MJPEG/Snapshot aus einem Cache
- ermöglicht Replay/Remote/Tests über External Provider

---

## DE – `core/device_hub.py` (DeviceHub)

### Singleton & Orchestrator-Kompatibilität
- `DeviceHub.instance()` ist threadsicher; bevorzugte API: `get_hub()`
- Motivation: UI + AgentLoop + Tools sollen denselben Zustand sehen (latest frame, ringbuffer)

### Kamera: Backend-Abstraktion + Capture-Thread
Backends (defensiv importiert):
- `_PiCamera2Cam` (Picamera2/libcamera)
- `_OpenCVCam` (cv2.VideoCapture)
- `_DummyCam` (fallback, synthetisch)

Capture-Loop:
- `start()` startet `_loop` (daemon thread)
- `_loop` liest Frames, setzt `_latest_frame` + `_latest_ts`
- `get_latest_frame(ensure_start=True)` liefert (frame_copy, ts)

#### Rotation (zentral, auch für Provider)
`_apply_frame_rotation(frame)`:
- aktuell nur **0 oder 180°**
- ENV (mehrere historische Namen):
  - `OROMA_VISION_ROTATE_DEG`, `OROMA_CAMERA_ROTATE_DEG`, `VISION_ROTATE_DEG`, `CAMERA_ROTATE_DEG`
  - plus boolean-Varianten `OROMA_VISION_ROTATE`, `OROMA_CAMERA_ROTATE`

### External Frame Injection (Provider/Push)
`submit_frame(frame, source="external")`:
- schreibt `_latest_frame/_latest_ts` atomar unter `_frame_lock`
- setzt `_external_source/_external_ts` für External-Mode
- publiziert zusätzlich den globalen Frame-Cache (siehe unten)

**External-Mode Gate:**
- `_external_active()` nutzt TTL `OROMA_EXTERNAL_MODE_TTL_SEC` (Default 60s)
- Wenn External aktiv ist und `OROMA_ALLOW_INTERNAL_CAMERA_WITH_EXTERNAL` **nicht** gesetzt ist, wird ein interner Kamera-Start verhindert.

**Freshness Gate (Production Fix):**
`get_latest_frame()` prüft:
- `OROMA_EXTERNAL_FRAME_FRESH_SEC` (Default 8s)
- wenn externes Frame frisch: return ohne Kamera-Start
- wenn External-Mode aktiv: return (latest or None) ohne Start

→ verhindert Start-Schleifen und FD-Druck (“Too many open files”) bei Provider-Betrieb.

### Global Frame Cache (für One-Shot Consumer)
`_publish_global_frame_cache(frame, ts, source)`:
- schreibt periodisch ein JPEG + JSON Metadaten in `OROMA_STATE_DIR`
- atomare Writes via `os.replace()`
- rate-limited:
  - `OROMA_GLOBAL_FRAME_CACHE_MIN_INTERVAL_SEC`
  - JPEG Qualität: `OROMA_GLOBAL_FRAME_CACHE_JPEG_QUALITY`
  - Max Age (consumer-seitig): siehe `camera_hub` `OROMA_GLOBAL_FRAME_CACHE_MAX_AGE_SEC`

Ziel:
- One-shot Prozesse (z. B. PTZ Loop im Orchestrator) können ein Bild lesen, ohne die Kamera zu initialisieren.

### MJPEG / Snapshot (UI-Path)
- `get_latest_jpeg(quality=..., client=...)`
- `mjpeg_generator(boundary=b"frame", fps_cap=..., client=...)`
Beide pflegen Audit-Events, sind best-effort und dürfen nicht crashen.

### Light (0..100 aus Kamera-Luma)
- `get_light_level()` liefert 0..100 (skalierter Luma-Wert)
- Quelle via:
  - `OROMA_LIGHT_SOURCE = camera|dummy|off`
- Kamera-Abtastintervall:
  - `OROMA_LIGHT_CAMERA_INTERVAL` (Sekunden)
- Clamp:
  - `OROMA_LIGHT_MIN`, `OROMA_LIGHT_MAX`
- Audit-Mode:
  - `OROMA_LIGHT_AUDIT_MODE = changes|all|off`

### Audio (PortAudio/sounddevice, headless robust)
Enable:
- `OROMA_AUDIO_ENABLE=1|0`

Mikrofonstart:
- `start_mic(client=None)` (lazy, session-aware)
- Device Auswahl (robuste Reihenfolge):
  1) `OROMA_AUDIO_INPUT_INDEX`
  2) Name-Match `OROMA_AUDIO_INPUT_NAME`
  3) PortAudio default.device[0]
  4) best-scored first input device (USB/Jabra/sysdefault bevorzugt, SPDIF/HDMI penalisiert)

HostAPI Fix (Production):
- `OROMA_AUDIO_HOSTAPI` (Default "ALSA") → versucht `sd.default.hostapi` zu setzen, aber respektiert read-only property.

Ringbuffer:
- Länge: `OROMA_AUDIO_RING_SEC`
- Blockgröße: `OROMA_AUDIO_BLOCK_MS`
- Level-Berechnung: `OROMA_AUDIO_LEVEL_INTERVAL`

Aufnahme/Playback:
- `read_audio(seconds)` (mono float32 [-1,1])
- `record_wav(seconds, sr=..., gain_db=...)`:
  - Warmup gegen “record_empty” via `OROMA_AUDIO_RECORD_WARMUP_SEC`
  - optional Gain in dB via `OROMA_AUDIO_GAIN`
- Output device via `OROMA_AUDIO_OUTPUT_NAME` + Fallbacks

### Sessions & Audit
Sessions:
- `open_session(client, kind)` / `close_session(session_id)`
- wird z. B. in `mjpeg_generator` genutzt, um Client-Nutzung nachvollziehbar zu machen.

Audit:
- rotierende Datei (`OROMA_HUB_AUDIT_PATH`) + Backup-Count (`OROMA_HUB_AUDIT_BACKUPS`) + MaxBytes (`OROMA_HUB_AUDIT_MAX_BYTES`)
- `OROMA_HUB_AUDIT_ENABLE`
- Snapshot-Throttle: `OROMA_HUB_AUDIT_SNAPSHOT_THROTTLE`

### Watchdogs
Capture Watchdog:
- Stall/Fail-Streak Restart:
  - `OROMA_CAMERA_STALL_RESTART_SEC`
  - `OROMA_CAMERA_FAIL_STREAK_RESTART`
  - `OROMA_CAMERA_RESTART_MIN_INTERVAL_SEC`

DeviceHub Watchdog (Hung detection / restart decisions):
- `OROMA_DEVICEHUB_WATCHDOG`
- `OROMA_DEVICEHUB_WATCHDOG_INTERVAL_SEC`
- `OROMA_DEVICEHUB_WATCHDOG_HUNG_SEC`
- `OROMA_DEVICEHUB_WATCHDOG_MIN_RESTART_SEC`

### Wichtige ENVs (Zusammenfassung)
Kamera/Vision:
- `OROMA_VISION_BACKEND`, `OROMA_VISION_DEVICE`, `OROMA_VISION_W/H/FPS`, `OROMA_VISION_FOURCC`, `OROMA_VISION_BUFFERSIZE`
- `OROMA_OPENCV_FOURCC`
- Rotation: `OROMA_VISION_ROTATE_DEG`, `OROMA_CAMERA_ROTATE_DEG`, etc.
- External/Cache: `OROMA_EXTERNAL_FRAME_FRESH_SEC`, `OROMA_EXTERNAL_MODE_TTL_SEC`, `OROMA_GLOBAL_FRAME_CACHE_*`

Audio:
- `OROMA_AUDIO_ENABLE`, `OROMA_AUDIO_INPUT_INDEX`, `OROMA_AUDIO_INPUT_NAME`, `OROMA_AUDIO_OUTPUT_NAME`
- `OROMA_AUDIO_SR`, `OROMA_AUDIO_CH`, `OROMA_AUDIO_BLOCK_MS`, `OROMA_AUDIO_RING_SEC`
- `OROMA_AUDIO_HOSTAPI`, `OROMA_AUDIO_RECORD_WARMUP_SEC`, `OROMA_AUDIO_GAIN`

Light:
- `OROMA_LIGHT_SOURCE`, `OROMA_LIGHT_CAMERA_INTERVAL`, `OROMA_LIGHT_MIN/MAX`, `OROMA_LIGHT_AUDIT_MODE`

Audit/State:
- `OROMA_HUB_AUDIT_*`, `OROMA_STATE_DIR`, `OROMA_DEVICE_HUB_ATTACH_STDERR`

---

## DE – `core/camera_hub.py` (Bridge + Provider Registry)

### Rolle
CameraHub ist eine Kompatibilitäts-Schicht für Komponenten, die `camera_hub.get_frame()` erwarten.
In v3.7.3 gilt: **DeviceHub ist Owner**, CameraHub ist Bridge.

### Provider-Registry (Frame Injection)
- `set_provider(name, provider, replace=True)`
- `clear_provider(name)`
- `list_providers()`
- `submit_frame(frame, ts=None)`
Registry ist atomar (Lock + maps by name & provider-id), um Race-Conditions zu vermeiden.

### Provider-aware get_frame
- Wenn Provider aktiv: `ensure_start=False` (DeviceHub startet Kamera nicht)
- Wenn kein Provider: `ensure_start=True` (Live-Default)

### Global cached frame access
CameraHub bietet Fast-Path Getter für den globalen Frame-Cache:
- `get_global_cached_frame_with_ts()` / `get_cached_frame_with_ts_fast*`
→ wichtig für One-Shot Consumers ohne Hub-Init.

### ENV
- `OROMA_STATE_DIR`
- `OROMA_GLOBAL_FRAME_CACHE_MAX_AGE_SEC`
- `OROMA_CAMERA_HUB_BRIDGE_LOG_REPEAT_SEC`

---

## DE – Fehlerfälle & Robustheit
- Fehlende Libraries (cv2/picamera2/sounddevice) → Dummy/No-op statt Crash.
- External provider aktiv → **kein** interner Kamera-Start (Fail-closed).
- Audio HostAPI read-only → Umschaltung wird gedrosselt übersprungen.
- Audit ist throttled, um Logexplosion zu vermeiden.

---

## DE – Bezug zum Code
- Relevante Dateien:
  - `core/device_hub.py`
  - `core/camera_hub.py`
- Verwandte Core-Dokus:
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/26_snaptoken_hooks.md` (nutzt Hub-Schnittstellen)
  - `docs/core/80_ops_runtime.md` (systemd/orchestrator Kontext)

---

## EN

### Purpose
This document describes ORÓMA’s **headless device/sensor layer** based on:

- `core/device_hub.py` – **DeviceHub** as the single source of truth for camera, light, audio, sessions, sensor channels, and audit (v3.7.3).
- `core/camera_hub.py` – **CameraHub** as a compatibility bridge plus provider registry (frame injection) for legacy/hybrid components.

Focus: **24/7 edge robustness** (no GUI dependencies, defensive imports, short locks, auditable I/O, watchdogs against stalled cameras).

### Scope / Non-goals
- ✅ In scope: camera backends + capture loop, external frame injection, global frame cache (JPEG+JSON), light (camera luma), audio (PortAudio/sounddevice, ring buffer, WAV record/playback), sessions, audit logs, watchdogs, provider registry.
- ❌ Out of scope: UI templates, model inference, PTZ policy logic (only interfaces/state paths), exhaustive documentation of every helper.

---

## EN – Architectural role
Conceptual flow:
**UI/AgentLoop/Tools → DeviceHub (camera/audio/light) → Snap/Fusion/SnapChain**

DeviceHub is the only place that opens hardware resources:
- avoids duplicate camera opens (critical for libcamera/Picamera2)
- keeps audio ring buffer stable
- serves MJPEG/snapshot from cached frames
- supports replay/remote/tests through external providers

---

## EN – `core/device_hub.py` (DeviceHub)

### Singleton & orchestrator compatibility
- `DeviceHub.instance()` is thread-safe; preferred API: `get_hub()`

### Camera: backend abstraction + capture thread
Backends (defensive imports):
- Picamera2/libcamera, OpenCV VideoCapture, Dummy fallback

Capture loop:
- `start()` spawns `_loop` thread
- `_loop` updates `_latest_frame` + `_latest_ts`
- `get_latest_frame(ensure_start=True)` returns `(frame_copy, ts)`

Rotation:
- centralized, affects internal camera and external providers (`submit_frame`)
- currently only 0 or 180 degrees via env.

### External frame injection
`submit_frame(frame, source="external")` updates latest frame under lock and marks external mode.

External-mode gate:
- `_external_active()` uses TTL `OROMA_EXTERNAL_MODE_TTL_SEC`
- prevents internal camera starts when an external provider is active (unless explicitly allowed)

Freshness gate:
- `OROMA_EXTERNAL_FRAME_FRESH_SEC` prevents internal starts when external frames are fresh
- production fix to avoid “too many open files” in provider mode.

### Global frame cache
Publishes a lightweight JPEG + JSON metadata artifact into `OROMA_STATE_DIR` using atomic `os.replace()` writes, rate-limited by env. Used by one-shot consumers to avoid initializing the camera path.

### MJPEG / snapshot
`get_latest_jpeg()` and `mjpeg_generator()` are best-effort and audited.

### Light
`get_light_level()` returns 0..100 based on camera luma, with source selection and audit controls.

### Audio
Robust PortAudio/sounddevice handling:
- hostapi selection (`OROMA_AUDIO_HOSTAPI`)
- device selection order (index/name/default/first scored input)
- ring buffer (`OROMA_AUDIO_RING_SEC`) and level sampling
- `record_wav()` warmup (`OROMA_AUDIO_RECORD_WARMUP_SEC`) and optional gain (`OROMA_AUDIO_GAIN`)

### Sessions & audit
Sessions track who uses camera/audio; audit logs are rotating and throttled.

### Watchdogs
Camera stall/fail-streak restart and DeviceHub watchdog settings are env-controlled.

---

## EN – `core/camera_hub.py` (Bridge + provider registry)
CameraHub bridges legacy `get_frame()` callers to DeviceHub and hosts a race-free provider registry for frame injection. It ensures DeviceHub is not auto-started when a provider is active and exposes fast-path access to the global cached frame.

---

## EN – Failure modes & robustness
- missing libs → dummy/no-op, no crashes
- provider active → no internal camera start (fail-closed)
- audio hostapi read-only → skip with rate-limited logs
- audit throttling prevents log explosions

---

## EN – Code mapping
- `core/device_hub.py`
- `core/camera_hub.py`
- Related core docs:
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/26_snaptoken_hooks.md`
  - `docs/core/80_ops_runtime.md`

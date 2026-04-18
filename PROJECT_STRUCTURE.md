# ORÓMA Project Structure

**Project:** ORÓMA (Offline‑Realtime‑Organic‑Memory‑AI)  
**Document:** Repository / directory layout (snapshot)  
**Purpose:** Provide a stable, publishable map of the codebase and runtime folders for maintainers, reviewers, and academic readers.

> **Scope note**
> This document describes the structure found in the project snapshot (root folder). Runtime folders such as `data/`, `state/`, and `logs/` are instance‑specific and are typically excluded from public code snapshots.

---

## 1. Top‑level layout

- `run_oroma.py` – main entry point; wires Flask UI, agent loop, device hub, and optional background workers.
- `core/` – architectural core: memory primitives (Snap/SnapChain), consolidation (Dream/Replay), policy/rules, DB layer.
- `ui/` – Flask blueprints and templates for dashboards and control panels.
- `wrappers/` – backend wrappers/adapters (vision/audio/LLM/PTZ), optimized for headless deployment.
- `tools/` – offline jobs, maintenance scripts, diagnostics, runners.
- `systemd/` – service/timer units used by deployment and orchestrator mode.
- `docs/` – architecture documentation, specs, and operational notes.

Supporting / auxiliary directories:
- `tests/` – automated tests.
- `mini_programs/` – small standalone utilities/demos.
- `third_party/` – vendored dependencies (if present).

Runtime / instance‑specific directories (normally **not** published as “source”):
- `data/` – local databases, caches, and persisted runtime artifacts.
- `state/` – live runtime state files (JSON, caches, orchestrator state, etc.).
- `logs/`, `log/` – log files.
- `models/` – optional local model artifacts.
- `exports/`, `exports_out/`, `archives/`, `uploads/` – import/export and runtime I/O.

---

## 2. Core (`core/`) – key modules

The following files define the SnapChain architecture and long‑running edge behavior:

### 2.1 Memory primitives
- `core/snap.py` – atomic observation unit (“Snap”).
- `core/snapchain.py` – temporal episode structure (“SnapChain”).
- `core/snappattern.py` – pattern / centroid logic for similarity and consolidation.
- `core/snaptoken.py` – token/feature representations used across subsystems.
- `core/snap_indexer.py` – indexing and retrieval helpers.

### 2.2 Consolidation / replay
- `core/dream_worker.py` – Dream‑phase consolidation (replay, restructuring, compression).
- `core/replay_system.py` – replay engine (selection, execution, bookkeeping).
- `core/replay_manager.py` – replay orchestration and export‑friendly controls.

### 2.3 Policy, rules, and transfer
- `core/policy_engine.py` – policy learning/application layer.
- `core/universal_policy.py` – domain‑agnostic policy interface (state_hash/action).
- `core/transfer_engine.py` – transfer mechanisms across domains/subsystems.

### 2.4 Runtime stability (DB + device I/O)
- `core/sql_manager.py` – DB schema/PRAGMA control, migrations, and stable read paths.
- `core/db_writer.py` / `core/db_writer_client.py` – single‑writer queue/IPC for stable SQLite writes.
- `core/device_hub.py` – centralized device access (camera/audio/light) with auditability.
- `core/camera_hub.py` – camera provider abstraction (V4L2/by‑id stability, injection sources).
- `core/circadian_controller.py` – Day/Dream switching logic (e.g., light‑based scheduling).

---

## 3. UI (`ui/`) – dashboards and control planes

`ui/` contains Flask blueprints for:
- system health/metrics dashboards
- learning/curves and history views
- replay controls and inspection
- video/PTZ monitoring and control
- games/curriculum and policy experiments
- import/export and model selection pages

Templates and static assets live under `ui/templates/` and `ui/static/` (if present in your snapshot).

---

## 4. Wrappers (`wrappers/`) – backend routing (headless)

`wrappers/` contains adapter layers for:
- vision backends (OpenCV / Hailo / DeGirum / GStreamer routing)
- audio/ASR/TTS interfaces
- PTZ controllers and device control abstractions

These wrappers allow ORÓMA to run headless (no X11/Qt/Wayland requirements) while switching backends.

---

## 5. Tools (`tools/`) – jobs, runners, diagnostics

`tools/` provides scripts used by operators and the orchestrator, such as:
- daily/periodic runners (games, consolidation, metrics)
- snapshot/export helpers
- DB diagnostics, audit and repair tools
- cache refreshers and reporting

---

## 6. systemd (`systemd/`) – services and timers

`systemd/` contains unit files for:
- the main ORÓMA service
- the orchestrator service (job scheduling)
- periodic timers (dream, replay, stats, archive, etc.)

In orchestrator mode, some one‑shot units may be subordinate or skipped by condition flags.

---

## 7. Recommended publication split (Paper + Code)

For academic/public distribution, a clean split is:

- **Zenodo Preprint(s):** EN whitepaper + DE translation (already published).
- **Zenodo Software Snapshot:** source‑only archive (exclude `data/`, `state/`, `logs/`, large `models/`).
- **Codeberg Repo:** living development source with README linking to Zenodo DOIs.

---

## 8. Quick tree (top level)

```
run_oroma.py
core/
ui/
wrappers/
tools/
systemd/
docs/

# runtime / instance data (usually excluded from source snapshots)
data/
state/
logs/ , log/
models/
exports/ , exports_out/ , archives/ , uploads/

# support
mini_programs/
tests/
third_party/
```


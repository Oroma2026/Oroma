## Repository layout

This repository contains the ORÓMA (Offline‑Realtime‑Organic‑Memory‑AI) system as a long‑running, offline‑first edge architecture.

- `run_oroma.py` – main entry point (Flask UI + agent runtime wiring).
- `core/` – architectural core: Snap/SnapChain memory, Dream/Replay consolidation, policy/rules, transfer, DB layer.
- `ui/` – Flask blueprints + templates for dashboard, learning/health, replay, video/PTZ, games, tools.
- `wrappers/` – backend adapters for vision/audio/LLM/PTZ (headless, backend‑routed).
- `tools/` – maintenance jobs, runners, diagnostics, export/import helpers.
- `systemd/` – service + timer units (and orchestrator integration).
- `docs/` – architecture notes, specs, and operational documentation.

Additional project folders (often excluded from public “code snapshot” releases):
- `data/`, `state/` – runtime state, local DBs, caches (instance‑specific; do not commit).
- `logs/`, `log/` – runtime logs (instance‑specific; do not commit).
- `models/` – optional local model files/artifacts.
- `exports/`, `exports_out/`, `archives/`, `uploads/` – import/export artifacts and runtime I/O.
- `tests/` – automated tests.
- `mini_programs/` – small standalone utilities/demos.
- `third_party/` – vendored dependencies (if any).

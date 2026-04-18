<meta name="google-site-verification" content="DEIN_LANGER_CODE_HIER" />
# ORÓMA (Offline-Realtime-Organic-Memory-AI)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19596002.svg)](https://doi.org/10.5281/zenodo.19596002)

**Offline-first adaptive edge intelligence architecture** for persistent, memory-centric cognition on resource-constrained hardware.

**Whitepaper (Zenodo):**
- **EN (reference DOI):** 10.5281/zenodo.19596002  
- **DE (Übersetzung DOI):** 10.5281/zenodo.19629298  

> **Citation:** Please cite the **English reference version** (10.5281/zenodo.19596002).  
> The German translation is provided for accessibility.

---

## What is ORÓMA?
ORÓMA explores a system-architecture approach to long-running edge cognition built around:

- **persistent episodic memory** (Snap / SnapChain)
- **Day/Dream phase separation** (online operation vs. replay/consolidation)
- **replay-driven consolidation** as a first-class learning primitive
- **binding-oriented mechanisms** for relating events and multimodal signals over time
- **edge-runtime realism** (bounded budgets, stable operation, disciplined write paths)

ORÓMA is **not** positioned as a replacement for large-scale foundation models.
It is an architectural exploration of **persistent, edge-deployed, memory-centric cognition**.

---

## Repository layout (overview)
> **Note:** This repository currently serves as the **public landing page**.  
> A **software snapshot release** (versioned source archive) is planned as a separate release.

Full structure documentation: **`docs/PROJECT_STRUCTURE.md`**

Top-level overview (planned snapshot layout):
- `core/` – runtime engine, memory (Snap/SnapChain), replay/dream, policy/rules, persistence
- `ui/` – Flask-based dashboard and APIs
- `wrappers/` – vision/audio/LLM/PTZ backend adapters
- `tools/` – maintenance utilities, runners, diagnostics
- `systemd/` – service/timer units and orchestrator units
- `docs/` – architecture notes, specs, project structure

> Note: In public “software snapshot” releases, **DBs/logs/state** are typically excluded.

---

## What’s included / excluded (public software snapshot guidance)
Typically **included**:
- source code (`core/`, optionally `ui/`, `wrappers/`, `tools/`, `systemd/`, `docs/`)
- documentation files (`README.md`, `docs/*`)
- sample configs (if any)

Typically **excluded**:
- large SQLite databases (e.g. `*.db`)
- runtime logs (`logs/`)
- runtime state (`state/`, caches, backups)
- exported artifacts that may contain private or device-specific data

This keeps snapshots small, reviewable, and safe to redistribute.

---

## How to cite (BibTeX)
```bibtex
@misc{werner_oroma_2026,
  author       = {Werner, Jörg},
  title        = {ORÓMA: An Offline-First Persistent Episodic Memory Architecture for Edge Cognitive Agents},
  year         = {2026},
  publisher    = {Zenodo},
  doi          = {10.5281/zenodo.19596002}
}
```

---

## License
- **Whitepaper:** CC BY 4.0 (as specified on Zenodo)
- **Code / software snapshot:** MIT (a `LICENSE` file is included with each release)

---

## Contact
Jörg Werner — Independent Researcher (Germany)  
Project: ORÓMA

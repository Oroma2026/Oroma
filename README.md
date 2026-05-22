<meta name="google-site-verification" content="googlea8df0eecea5ff774.html" />

# ORÓMA (Offline-Realtime-Organic-Memory-AI)

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19596002.svg)](https://doi.org/10.5281/zenodo.19596002)

**Offline-first adaptive edge intelligence architecture** for persistent, memory-centric cognition on resource-constrained hardware.

ORÓMA is an experimental system architecture for long-running edge cognition. It combines persistent episodic memory, replay-driven consolidation, binding-oriented mechanisms, local runtime discipline, and observable system operation on constrained hardware.

---

## Whitepaper and Releases

**Whitepaper (Zenodo):**
- **EN (reference DOI):** `10.5281/zenodo.19596002`
- **DE (translation DOI):** `10.5281/zenodo.19629298`

**Software snapshot (Zenodo):**
- **Source release DOI:** `10.5281/zenodo.20262590`

> **Citation:** Please cite the **English reference version** (`10.5281/zenodo.19596002`).
> The German translation is provided for accessibility.

---

## Quick Start / Schnellstart

### EN

1. Clone the public repository.
2. Copy `.env.systemd.example` to `.env.systemd`.
3. Copy `.env.example` to `.env`.
4. Adjust local paths, tokens, camera/PTZ device settings, and runtime options.
5. Start ORÓMA either directly with Python or through systemd / the orchestrator setup used on your host.

Minimal example:

```bash
git clone <CODEBERG_OR_GITHUB_URL>
cd oroma
cp .env.systemd.example .env.systemd
cp .env.example .env
python3 run_oroma.py
```

### DE

1. Klone das öffentliche Repository.
2. Kopiere `.env.systemd.example` nach `.env.systemd`.
3. Kopiere `.env.example` nach `.env`.
4. Passe lokale Pfade, Tokens, Kamera-/PTZ-Geräte und Runtime-Optionen an.
5. Starte ORÓMA entweder direkt per Python oder über systemd bzw. den auf deinem Host genutzten Orchestrator.

Minimales Beispiel:

```bash
git clone <CODEBERG_OR_GITHUB_URL>
cd oroma
cp .env.systemd.example .env.systemd
cp .env.example .env
python3 run_oroma.py
```

See also / Siehe auch:
- [`QUICKSTART.md`](QUICKSTART.md)
- [`.env.example`](.env.example)
- [`.env.systemd.example`](.env.systemd.example)

---

## What is ORÓMA?

ORÓMA explores a system-architecture approach to persistent, memory-centric edge cognition built around:

- **persistent episodic memory** using Snap / SnapChain structures
- **Day/Dream phase separation** for online operation versus offline replay and consolidation
- **replay-driven consolidation** as a first-class learning primitive
- **binding-oriented mechanisms** for relating events, contexts, and multimodal signals over time
- **policy and reward feedback loops** for adaptive behavior and measurable improvement
- **edge-runtime realism** with bounded budgets, stable operation, disciplined write paths, and observable system state

ORÓMA is **not** positioned as a replacement for large-scale foundation models. It is an architectural exploration of **persistent, edge-deployed, memory-centric cognition**.

In practical terms, ORÓMA is closer to a local memory-and-adaptation system than to a chatbot frontend.

---

## Architecture Reference

The current architecture audit is available here:

- [`docs/architecture_audit.md`](docs/architecture_audit.md)

The audit describes the system as a layered architecture with:

- sensor and actuator integration
- Snap / SnapChain episodic memory
- SQLite-backed persistence
- replay and Dream consolidation
- binding and relation mechanisms
- policy learning and reward feedback
- Flask-based observability
- systemd and orchestrator-based edge operation

The audit is intended as a technical reference for readers who want to understand the project beyond the source tree.

---

## Repository Layout

Full structure documentation:

- [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md)
- [`docs/architecture_audit.md`](docs/architecture_audit.md)

Top-level overview:

- `core/` – runtime engine, memory, Snap/SnapChain, replay, Dream, policy, rules, persistence
- `ui/` – Flask-based dashboard, APIs, and observability tools
- `wrappers/` – vision, audio, LLM, PTZ, and backend adapters
- `tools/` – maintenance utilities, runners, diagnostics, policy and replay tools
- `systemd/` – service and timer units for long-running edge deployment
- `docs/` – architecture notes, specifications, audits, and project structure
- `mini_programs/` – controlled learning environments and policy test domains
- `tests/` – validation, smoke tests, and regression checks

This repository serves as the public development and documentation repository for ORÓMA. Citable, versioned software snapshots are published separately on Zenodo.

---

## What Is Included / Excluded in Public Software Snapshots

Public software snapshots are intended to be clean source distributions.

Typically **included**:

- source code (`core/`, `ui/`, `wrappers/`, `tools/`, `systemd/`, `mini_programs/`, `tests/`)
- documentation files (`README.md`, `docs/*`, manifests, release notes)
- sample configuration files, if applicable
- license and third-party notices

Typically **excluded**:

- large SQLite databases (`*.db`)
- runtime logs (`logs/`)
- runtime state (`state/`, caches, backups)
- private or device-specific exports
- local development artifacts such as `__pycache__/`, `.cache/`, `.local/`, or `.git/`

This keeps public snapshots small, reviewable, and safe to redistribute.

---

## How to Cite (BibTeX)

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

- **Whitepaper:** CC BY 4.0, as specified on Zenodo
- **Code / software snapshot:** MIT, with a `LICENSE` file included in each release

---

## Contact

Jörg Werner — Independent Researcher (Germany)  
Project: ORÓMA

---

# Deutsch

# ORÓMA (Offline-Realtime-Organic-Memory-AI)

**Offline-first adaptive Edge-Intelligence-Architektur** für persistente, gedächtniszentrierte Kognition auf ressourcenbegrenzter Hardware.

ORÓMA ist eine experimentelle Systemarchitektur für dauerhaft laufende Edge-Kognition. Das Projekt verbindet persistentes episodisches Gedächtnis, Replay-getriebene Konsolidierung, Binding-orientierte Mechanismen, lokale Laufzeitdisziplin und beobachtbaren Systembetrieb auf begrenzter Hardware.

---

## Whitepaper und Veröffentlichungen

**Whitepaper (Zenodo):**
- **EN (Referenz-DOI):** `10.5281/zenodo.19596002`
- **DE (Übersetzungs-DOI):** `10.5281/zenodo.19629298`

**Software-Snapshot (Zenodo):**
- **Source-Release-DOI:** `10.5281/zenodo.20262590`

> **Zitierhinweis:** Bitte die **englische Referenzversion** (`10.5281/zenodo.19596002`) zitieren.
> Die deutsche Version dient der Zugänglichkeit und Verständlichkeit.

---

## Was ist ORÓMA?

ORÓMA untersucht einen Systemarchitektur-Ansatz für persistente, gedächtniszentrierte Edge-Kognition. Der Schwerpunkt liegt auf:

- **persistentem episodischem Gedächtnis** durch Snap- und SnapChain-Strukturen
- **Day/Dream-Phasentrennung** zwischen Online-Betrieb und Offline-Replay/Konsolidierung
- **Replay-getriebener Konsolidierung** als zentralem Lernmechanismus
- **Binding-orientierten Mechanismen**, um Ereignisse, Kontexte und multimodale Signale über Zeit miteinander zu verbinden
- **Policy- und Reward-Rückkopplungen** für adaptives Verhalten und messbare Verbesserung
- **realistischem Edge-Betrieb** mit begrenzten Ressourcen, stabilen Laufzeitpfaden, kontrollierten Schreibzugriffen und beobachtbarem Systemzustand

ORÓMA versteht sich **nicht** als Ersatz für große Foundation Models. Es ist eine architektonische Untersuchung von **persistenter, lokal betriebener, gedächtniszentrierter Kognition**.

Praktisch betrachtet ist ORÓMA eher ein lokales Gedächtnis- und Anpassungssystem als ein Chatbot-Frontend.

---

## Architektur-Referenz

Das aktuelle Architektur-Audit ist hier verfügbar:

- [`docs/architecture_audit.md`](docs/architecture_audit.md)

Das Audit beschreibt ORÓMA als Schichtenarchitektur mit:

- Sensor- und Aktor-Integration
- Snap-/SnapChain-basiertem episodischem Gedächtnis
- SQLite-gestützter Persistenz
- Replay- und Dream-Konsolidierung
- Binding- und Relationsmechanismen
- Policy-Lernen und Reward-Rückkopplung
- Flask-basierter Beobachtbarkeit
- systemd- und Orchestrator-basiertem Edge-Betrieb

Das Audit dient als technische Referenz für Leserinnen und Leser, die das Projekt nicht nur als Dateibaum, sondern als Systemarchitektur verstehen möchten.

---

## Repository-Struktur

Vollständige Strukturdokumentation:

- [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md)
- [`docs/architecture_audit.md`](docs/architecture_audit.md)

Überblick der wichtigsten Ordner:

- `core/` – Runtime-Engine, Gedächtnis, Snap/SnapChain, Replay, Dream, Policy, Regeln, Persistenz
- `ui/` – Flask-basiertes Dashboard, APIs und Beobachtungswerkzeuge
- `wrappers/` – Adapter für Vision, Audio, LLM, PTZ und verschiedene Backends
- `tools/` – Wartungswerkzeuge, Runner, Diagnostik, Policy- und Replay-Tools
- `systemd/` – Service- und Timer-Units für dauerhaften Edge-Betrieb
- `docs/` – Architekturhinweise, Spezifikationen, Audits und Projektstruktur
- `mini_programs/` – kontrollierte Lernumgebungen und Policy-Testdomänen
- `tests/` – Validierung, Smoke-Tests und Regressionsprüfungen

Dieses Repository dient als öffentliches Entwicklungs- und Dokumentationsrepository für ORÓMA. Zitierbare, versionierte Software-Snapshots werden separat auf Zenodo veröffentlicht.

---

## Was öffentliche Software-Snapshots enthalten / nicht enthalten

Öffentliche Software-Snapshots sind als saubere Source-Distributionen gedacht.

Typischerweise **enthalten**:

- Quellcode (`core/`, `ui/`, `wrappers/`, `tools/`, `systemd/`, `mini_programs/`, `tests/`)
- Dokumentation (`README.md`, `docs/*`, Manifeste, Release Notes)
- Beispielkonfigurationen, falls vorhanden
- Lizenz und Hinweise zu Drittkomponenten

Typischerweise **ausgeschlossen**:

- große SQLite-Datenbanken (`*.db`)
- Laufzeit-Logs (`logs/`)
- Laufzeitstatus (`state/`, Caches, Backups)
- private oder gerätespezifische Exporte
- lokale Entwicklungsartefakte wie `__pycache__/`, `.cache/`, `.local/` oder `.git/`

Dadurch bleiben öffentliche Snapshots klein, prüfbar und sicher weiterverteilbar.

---

## Zitieren (BibTeX)

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

## Lizenz

- **Whitepaper:** CC BY 4.0, wie auf Zenodo angegeben
- **Code / Software-Snapshot:** MIT, mit enthaltener `LICENSE`-Datei in jedem Release

---

## Kontakt

Jörg Werner — Independent Researcher (Deutschland)  
Projekt: ORÓMA

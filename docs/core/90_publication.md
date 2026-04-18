# Veröffentlichung / Publication (Core Docs)

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument definiert die **öffentliche Veröffentlichungs- und Zitierlogik** von ORÓMA (Whitepaper + Landing-Repo + zukünftiger Software-Snapshot). Es ist bewusst kurz und operativ: Welche DOI ist Referenz, wie werden Übersetzungen verknüpft, welche Lizenz gilt für welches Artefakt, und wie werden öffentliche Snapshots sauber abgegrenzt (ohne DB/log/state).

### Scope / Nicht-Ziele
- ✅ In scope: DOIs, Crosslinks, Citation-Regel, Lizenztrennung (Paper vs Code), Snapshot-Scope (include/exclude), Versionierungsempfehlung.
- ❌ Out of scope: “Marketing”, detaillierte wissenschaftliche Related Work, interne Deployments.

---

## Veröffentlichte Records (Zenodo)

### Whitepaper (Referenz)
- **EN (Referenz):** https://doi.org/10.5281/zenodo.19596002  
  - Whitepaper v1.0 (Defensive Publication)
  - **Diese Version ist die zitierfähige Referenz.**

### Whitepaper (Übersetzung)
- **DE (Übersetzung):** https://doi.org/10.5281/zenodo.19629298  
  - German translation of v1.0 (accessibility)
  - **Zitation:** weiterhin EN-DOI als Referenz zitieren

### Crosslinks (EN ↔ DE)
- EN verweist auf DE (“Has translation” / “Is supplemented by”).
- DE verweist auf EN (“Is supplement to” / ideal: “Is translation of”, falls verfügbar).

> Hinweis: Zenodo UI bietet je nach Zustand unterschiedliche Relationen; inhaltlich ist entscheidend, dass **beide Richtungen** verlinkt sind.

---

## Landing Repository (Codeberg)
- Repository: https://codeberg.org/oromamaster/Oroma

Ziel des Landing-Repos:
- stabile, öffentliche Einstiegseite (README + DOIs)
- strukturierte Doku (`docs/`)
- später optional: “Architectural Core” Code oder Release-Verweise

---

## Lizenzlogik (wichtig)
ORÓMA trennt bewusst zwischen **Paper** und **Software**:

### Whitepaper
- Lizenz: **CC BY 4.0** (wie auf Zenodo gesetzt)

### Code / Software Snapshot
- Lizenz: **MIT** (für permissive Nachnutzung)
- Der Snapshot enthält eine `LICENSE` Datei im Root.

> Begründung: Creative Commons ist für Paper/Texte gut; für Software ist MIT/Apache üblicher und kompatibler mit Toolchains.

---

## Software Snapshot (geplant / empfohlen)
Ein Software Snapshot ist ein versioniertes Source-Archiv (Zenodo “Software” Record), das **ohne instanzspezifische Daten** verteilt werden kann.

### Include (typisch)
- `core/` (relevante Architekturmodule)
- optional: `ui/`, `wrappers/`, `tools/`, `systemd/`
- `docs/`
- `README.md`, `LICENSE`

### Exclude (öffentlich)
- Datenbanken: `*.db`, `data/`
- Logs: `logs/`, `log/`
- Runtime state: `state/`
- Backups/Archive: `oroma_backups/`, `archives/`, `exports/`, `uploads/`
- große Modellartefakte: `models/` (falls enthalten)

### Versionierungsempfehlung
- Paper: `v1.0` (EN) + `DE-1.0` (Übersetzung)
- Software Snapshot: z. B. `v1.0-software` oder `v2026.04-snapshot` (klar, semantisch, wiederholbar)

### Related identifiers (Snapshot → Paper)
Im Software-Record:
- referenziere EN/DE DOIs unter “Related works/identifiers”
- Relation: `IsSupplementTo` / `References`

---

## Kurz-Checkliste (wenn du etwas publizierst)
- [ ] DOI(s) korrekt
- [ ] EN ↔ DE Crosslink beidseitig
- [ ] Repo-Link in beiden Zenodo Records (“Repository URL”)
- [ ] README enthält DOIs + Citation Regel
- [ ] Snapshot (wenn veröffentlicht): ohne DB/log/state, mit MIT LICENSE

---

## EN

### Purpose
This document defines ORÓMA’s **public release and citation logic** (whitepaper + landing repo + planned software snapshot). It is intentionally short and operational: which DOI is the reference, how translations are linked, which license applies to which artifact, and how public source snapshots are scoped (excluding DB/log/state).

### Scope / Non-goals
- ✅ In scope: DOIs, crosslinks, citation rule, license separation (paper vs code), snapshot scope (include/exclude), versioning guidance.
- ❌ Out of scope: marketing, detailed related-work surveying, internal deployments.

---

## Published records (Zenodo)

### Whitepaper (reference)
- **EN (reference):** https://doi.org/10.5281/zenodo.19596002  
  - Whitepaper v1.0 (defensive publication)
  - **This is the canonical citable reference.**

### Whitepaper (translation)
- **DE (translation):** https://doi.org/10.5281/zenodo.19629298  
  - German translation of v1.0 (accessibility)
  - **Citation:** still cite the EN DOI as reference

### Crosslinks (EN ↔ DE)
- EN links to DE (“Has translation” / “Is supplemented by”).
- DE links to EN (“Is supplement to” / ideally “Is translation of”, if available).

---

## Landing repository (Codeberg)
- Repository: https://codeberg.org/oromamaster/Oroma

Purpose:
- stable public entry point (README + DOIs)
- structured documentation (`docs/`)
- optionally later: architectural core code or release pointers

---

## License logic (important)
ORÓMA intentionally separates **paper** and **software**:

### Whitepaper
- License: **CC BY 4.0** (as set on Zenodo)

### Code / software snapshot
- License: **MIT**
- Each snapshot includes a `LICENSE` file at the root.

---

## Software snapshot (planned / recommended)
A software snapshot is a versioned source archive (Zenodo “Software” record) that can be distributed **without instance-specific data**.

### Include (typical)
- `core/`
- optionally: `ui/`, `wrappers/`, `tools/`, `systemd/`
- `docs/`
- `README.md`, `LICENSE`

### Exclude (public)
- databases: `*.db`, `data/`
- logs: `logs/`, `log/`
- runtime state: `state/`
- backups/archives: `oroma_backups/`, `archives/`, `exports/`, `uploads/`
- large model artifacts: `models/`

### Versioning guidance
- Paper: `v1.0` (EN) + `DE-1.0` (translation)
- Software snapshot: e.g. `v1.0-software` or `v2026.04-snapshot`

### Related identifiers (snapshot → paper)
In the software record:
- reference EN/DE DOIs under “Related works/identifiers”
- relation: `IsSupplementTo` / `References`

---

## Quick release checklist
- [ ] DOIs correct
- [ ] EN ↔ DE crosslink both ways
- [ ] repo link present in both Zenodo records (“Repository URL”)
- [ ] README includes DOIs + citation rule
- [ ] snapshot (if published): excludes DB/log/state, includes MIT LICENSE

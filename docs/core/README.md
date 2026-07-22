# ORÓMA Core Docs / Kern-Dokumentation (DE/EN)

## DE
Diese `docs/core/`-Reihe dokumentiert die **kanonischen Kernmechanismen** von ORÓMA (Snap/SnapChain/Fusion/Replay usw.) als stabile, veröffentlichungsfähige Referenztexte.

**Öffentliche Referenzen:**
- Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002  
- Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298  
- Repository (Landing page): https://codeberg.org/oromamaster/Oroma  

> **Zitation:** Bitte die englische Referenzversion (EN DOI) zitieren. Die DE-Version dient der Zugänglichkeit.

**Status:** Die Übersicht (`00_overview.md`) wird **am Ende** erstellt, wenn alle Core-Dateien vorhanden sind.

### Geplante Core-Dateien (Reihenfolge)
- `10_snap.md`
- `12_snaptoken.md`
- `14_meta_snap.md`
- `15_fusion.md`
- `20_snappattern.md`
- `22_snapchain.md`
- `24_snap_indexer.md`
- `26_snaptoken_hooks.md`
- `28_calc_to_snapchain.md`
- `50_db_layer.md` (Phase 2)
- `60_devicehub.md` (Phase 2)
- `70_policy_transfer.md` (Phase 2)
- `80_ops_runtime.md` (Phase 2)
- `85_security_privacy.md` (optional, Phase 2)
- `90_publication.md`


---

## Architekturreview 2026-06-25 – Snap/SnapChain Core

### Ergebnis
Die Snap-/SnapChain-Kernarchitektur wurde als Fundament der ORÓMA-Gedächtnisebene geprüft.
Es wurde **keine Replay-ähnliche Doppelstruktur** festgestellt. Die vorhandenen Dateien bilden
eine bewusst gestufte Kernpipeline und sollen nicht konsolidiert oder zusammengelegt werden.

### Rollen der autoritativen Kernmodule
- `core/snap.py` / `docs/core/10_snap.md`
  - autoritative atomare Beobachtungseinheit mit Feature-Vektor, Content, Metadata, Norm-Cache und Fingerprint.
- `core/snaptoken.py` / `docs/core/12_snaptoken.md`
  - autoritative symbolische Token-Ebene für Text/Vision/Audio/Motion/Meta; kein Ersatz für Snap.
- `core/meta_snap.py` / `docs/core/14_meta_snap.md`
  - autoritative Abstraktionsebene über mehrere Snaps/SnapChains; kein Ersatz für SnapChain.
- `core/fusion.py` / `docs/core/15_fusion.md`
  - optionale Crossmodal-Fusion; erweitert Snaps, ersetzt aber keine Snap- oder SnapChain-Rolle.
- `core/snappattern.py` / `docs/core/20_snappattern.md`
  - Verdichtung/Cluster-Ebene zwischen Snap und SnapChain.
- `core/snapchain.py` / `docs/core/22_snapchain.md`
  - autoritative episodische Gedächtniseinheit und zeitliche Sequenz aus SnapPatterns.
- `core/snap_indexer.py` / `docs/core/24_snap_indexer.md`
  - fokussierte Index-Brücke für MetaSnaps in `snap_index`; kein kompletter SnapChain-Persistenzersatz.
- `core/hooks_av_snaptoken.py`, `core/hooks_audio_snaptoken.py` / `docs/core/26_snaptoken_hooks.md`
  - Live-Hooks, die kompakte Vision-/Audio-Tokenereignisse in die Gedächtnisstruktur einspeisen.
- `core/calc_to_snapchain.py` / `docs/core/28_calc_to_snapchain.md`
  - Calculator→SnapChain-Bridge für deterministische Transfer-Signale.

### Architekturfluss
```text
Perzeption / Tools / Spiele / Calculator
        ↓
Snap / SnapToken
        ↓
SnapPattern
        ↓
SnapChain
        ↓
Replay / Dream / Policy / Transfer / NMR
        ↓
MetaSnap / SnapIndexer / Explainability
```

### Bewertung
Die Dateien wirken teilweise ähnlich, erfüllen aber unterschiedliche Ebenen im Gedächtnismodell.
Eine Zusammenlegung würde die Architektur verschlechtern, weil atomare Beobachtung, symbolische
Tokenisierung, Musterbildung, episodische Sequenz und Abstraktion bewusst getrennt sind.

### Offene Beobachtungspunkte
- `docs/snaps_todo.md` ist historisch/TODO-orientiert und sollte nicht als aktuelle
  Architekturreferenz gelesen werden. Die aktuelle Referenz liegt in `docs/core/10_snap.md`
  bis `docs/core/28_calc_to_snapchain.md`.
- `core/replay_system.py` importiert weiterhin `core.snapchain`, ist aber seit der Replay-
  Konsolidierung als Legacy-Kompatibilitätsmodul markiert. Das ist kein SnapChain-Problem.
- Keine Codeänderung notwendig.

### Entscheidung
Für Snap/SnapChain wird aktuell **keine Konsolidierung** durchgeführt.
Die bestehende Core-Dokumentationsreihe bleibt die autoritative Referenz.

---

## EN
This `docs/core/` series documents the **canonical core mechanisms** of ORÓMA (Snap/SnapChain/Fusion/Replay, etc.) as stable, publishable reference texts.

**Public references:**
- Whitepaper (EN, reference): https://doi.org/10.5281/zenodo.19596002  
- Whitepaper (DE, translation): https://doi.org/10.5281/zenodo.19629298  
- Repository (Landing page): https://codeberg.org/oromamaster/Oroma  

> **Citation:** Please cite the English reference version (EN DOI). The German translation is provided for accessibility.

**Status:** The overview (`00_overview.md`) will be written **last**, once the core documents are in place.

### Planned core docs (order)
- `10_snap.md`
- `12_snaptoken.md`
- `14_meta_snap.md`
- `15_fusion.md`
- `20_snappattern.md`
- `22_snapchain.md`
- `24_snap_indexer.md`
- `26_snaptoken_hooks.md`
- `28_calc_to_snapchain.md`
- `50_db_layer.md` (Phase 2)
- `60_devicehub.md` (Phase 2)
- `70_policy_transfer.md` (Phase 2)
- `80_ops_runtime.md` (Phase 2)
- `85_security_privacy.md` (optional, Phase 2)
- `90_publication.md`

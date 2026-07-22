# Policy & Transfer – UniversalPolicy, PolicyEngine, TransferEngine, Roter Faden / Policy & Transfer (Core)

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt den **Policy- und Transfer-Kern** von ORÓMA, basierend auf vier Modulen, die zusammen die “Entscheidungsschicht” und die langfristige Verwertbarkeit von Erfahrungen abdecken:

- `core/universal_policy.py` (**UniversalPolicy / UP**) – domänenagnostische tabellarische Policy `(state_hash → action)` mit Online-Lernen und optionalem Auto-Export ins Regelarchiv.
- `core/policy_engine.py` (**PolicyEngine**) – lernt aus SnapChains eine tabellarische Policy (policy_rules) via Adapter-System; kann Regeln exportieren.
- `core/transfer_engine.py` (**TransferEngine**) – lightweight TransferSnaps (sequence/pattern) + Export-Marking + KPI-Metrics (kompatible Patch-API).
- `core/roter_faden.py` (**Roter Faden**) – persistenter Thread/Intent-Kontext mit Gap-Integration und Idle-Nudges (“kontextuelle Kontinuität”).

Zusätzlich wird `core/decision_engine.py` kurz referenziert, weil es runtime-seitig eine Domänen-Entscheidung (z. B. TicTacToe Solver) kapseln kann.

### Scope / Nicht-Ziele
- ✅ In scope: state_hash/action Modell, policy_rules Semantik, Adapter-Kanonisierung, Lernpfade (vector vs prehash), Auto-Export Kriterien, TransferSnaps, Thread-State Persistenz, Gap-Logging.
- ❌ Out of scope: vollständige Game-Implementierungen, UI, RL-Benchmarking, große Planner/LLM-Agent-Frameworks.

---

## DE – Architekturrolle (konzeptuell)
**SnapChain (Episode) → PolicyEngine/UP (lernen) → policy_rules (runtime choose) → Export (rules) → TransferEngine (pattern sequence)**  
Parallel dazu: **Roter Faden** hält einen minimalen Thread-Kontext und notiert Gaps/Idle-Friktionen für spätere Analyse.

---

## DE – Policy-Architekturreview 2026-06-25 / Rollenklärung nach Replay-Konsolidierung

### Anlass
Nach der erfolgreichen Replay-Konsolidierung wurde die Policy-Domäne mit derselben
Arbeitsweise geprüft: zuerst vorhandene Dokumentation lesen, dann Rollen anhand der
tatsächlichen Dateien bestimmen, erst danach entscheiden, ob eine Konsolidierung
notwendig ist.

### Ergebnis
Für die aktuell geprüfte ZIP ist **keine Replay-ähnliche technische Doppelstruktur**
nachgewiesen. Die wichtigsten Policy-Dateien übernehmen unterschiedliche Rollen:

| Datei | Aktuelle Rolle | Status |
|---|---|---|
| `core/universal_policy.py` | Laufzeitnahe, kleine UniversalPolicy für `choose`, `choose_vec`, `learn` und `learn_many` direkt auf `policy_rules` | aktiv / runtime-nah |
| `core/policy_engine.py` | Batch-/Trainings-/Export-Engine für SnapChains, DB-/TMPFS-Training, Adapter-Kanonisierung und Archiv-Export | aktiv / training-nah |
| `mini_programs/universal_policy/adapter_universal.py` | Domänenübergreifende Kanonisierung und Action-Mapping für PolicyEngine/UniversalPolicy-Pfade | aktiv / Adapter |
| `mini_programs/universal_policy/ram_writer.py` | RAM-/tmpfs-Episodenpuffer und Promotion/Flush-Vorbereitung | aktiv / Puffer |
| `mini_programs/universal_policy/ram_flush.py` | CLI-/Timer-Flush von RAM/tmpfs-Episoden über `PolicyEngine` | aktiv / Tool |
| `mini_programs/universal_policy/ram_sched.py` | In-Process Scheduler für periodisches RAM→PolicyEngine→DB/Archiv | aktiv / Scheduler |
| `core/decision_engine.py` | Entscheidungsschicht, die Regeln/Policy-Exports für konkrete Action-Wahl auswertet | aktiv / Entscheidung |

### Architekturentscheidung
Die Policy-Domäne wird aktuell **nicht konsolidiert wie Replay**.
Stattdessen wird die vorhandene Rollentrennung als beabsichtigte Architektur behandelt:

```text
UniversalPolicy
  = runtime-nahe, robuste State→Action-/Learn-Schicht

PolicyEngine
  = batch-/dream-/orchestrator-nahe Trainings- und Export-Schicht

Adapter/RAM-Komponenten
  = Zuführung, Kanonisierung und Entlastung der Policy-Schicht
```

### Verbindliche Arbeitsregel
Neue Policy-Funktionen sollen zuerst einer bestehenden Rolle zugeordnet werden:

- Runtime-Entscheidung / kleines Online-Lernen → `core/universal_policy.py`
- SnapChain-/Batch-/Export-Training → `core/policy_engine.py`
- Domänenkanonisierung / Action-Mapping → Adapter
- temporäre Episodenpufferung → `mini_programs/universal_policy/ram_writer.py`
- periodischer Flush/Scheduler → `ram_flush.py` / `ram_sched.py`

Eine neue Policy-Pipeline wird nur eingeführt, wenn keine dieser Rollen passt und die
Architekturentscheidung vorher dokumentiert wurde.

### Nicht-Ziele dieses Reviews
- keine Codeänderung
- keine Änderung an `policy_rules`
- keine Änderung am Regelarchiv `rules`
- keine Änderung an Game-UIs oder Daily-Runnern
- keine Zusammenlegung von `universal_policy.py` und `policy_engine.py`

### Nächster sinnvoller Schritt
Policy ist damit **nicht der nächste technische Konsolidierungskandidat**.
Sinnvoll wäre höchstens, einzelne UI-/Daily-Runner-Shims später zu vereinheitlichen,
aber erst nach gesonderter Analyse ihrer domänenspezifischen Unterschiede.

---

## DE – UniversalPolicy (`core/universal_policy.py`)
### Kernidee
Eine “kleine” Policy, die nur einen **String-Hash** kennt:
- `choose(state_hash, legal, side)` → wählt Aktion basierend auf `q` und `n`
- `learn_many(items)` → UPSERT in `policy_rules`

**policy_rules** (erwartet durch `sql_manager.ensure_schema()`):
`(namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)`  
UNIQUE(namespace, state_hash, action)

### Auswahl (choose)
- argmax über `q` (optional Softmax über Temperatur)
- Tiebreak über `n` (und optionale Pseudocounts)
- Side-aware: `side` kann Action-Kanonisierung beeinflussen (bei Adaptern)

**ENV (UP)**
- Sampling: `OROMA_UP_TEMP`, `OROMA_UP_PRIOR_N`
- Auto-Export: `OROMA_UP_AUTO_EXPORT`, `OROMA_UP_MIN_N`, `OROMA_UP_MIN_ABS_Q`, `OROMA_UP_MAJ_CONF`, `OROMA_UP_COOLDOWN_S`
- Export Cache: `OROMA_UP_EXPORT_CACHE_MAX`, `OROMA_UP_EXPORT_CACHE_GC_S`, `OROMA_UP_EXPORT_SCAN_CHUNK`
- Knowledge-Gaps in choose: `OROMA_UP_GAPS`, `OROMA_UP_GAPS_COOLDOWN_S`, `OROMA_UP_GAPS_LOW_EVIDENCE_N`, `OROMA_UP_GAPS_UNCERTAINTY_EPS`, `OROMA_UP_GAPS_DEBUG`
- DBWriter: `OROMA_DBW_ENABLE`, `OROMA_POLICY_DBW_CHUNK`

### Lernen (learn / learn_many)
- outcome ∈ [-1, 0, +1] (loss/draw/win) wird in pos/neg/draw gezählt
- `q` wird als normalisierte Erwartung gepflegt
- Upsert ist bewusst kompakt (Edge tauglich)

### Auto-Export (Explainability)
Wenn aktiviert, werden “gute” Einträge nach `rules` exportiert (Regelarchiv) – Kriterien über ENV.

---

## DE – PolicyEngine (`core/policy_engine.py`)
### Kernidee
PolicyEngine lernt aus **SnapChains** (episodische Sequenzen) und schreibt in `policy_rules`.
Datenquellen:
- primär: `oroma.db.snapchains`
- fallback: JSON-Files über `source_id` im SnapChains-Verzeichnis (`OROMA_SNAPCHAINS_DIR`)

### Adapter-System (Kanonisierung)
- bevorzugt: `mini_programs.universal_policy.adapter_universal.UniversalAdapter`
- fallback: `core.ttt_adapter.TTTAdapter`
Adapter liefern:
- Kanonisierung von state vectors → `state_hash`
- Mapping von Aktionen (Permutation) und side-aware Interpretationen

### Zwei Lernpfade
(A) **Vector/Steps Pfad**: nutzt Vektoren/Steps und berechnet optional centroids  
(B) **Prehash Fallback**: wenn keine Vektoren vorhanden sind, lernt aus vorgehashten Paaren `(state_hash, action)` aus Steps.

### Auto-Export ins Regelarchiv
Analog zur UP (eigene ENV-Namespace):
- `OROMA_PE_AUTO_EXPORT`, `OROMA_PE_EXPORT_COOLDOWN_S`, `OROMA_PE_EXPORT_MIN_N`, `OROMA_PE_EXPORT_MIN_ABS_Q`, `OROMA_PE_EXPORT_MAJ_CONF`

### DBWriter-Integration
- `OROMA_DBW_ENABLE`, `OROMA_DBW_TIMEOUT_MS`, `OROMA_POLICY_DBW_TIMEOUT_MS`  
PolicyEngine nutzt DBWriter-Write-Pfade, wenn aktiv, um Locks zu reduzieren.

---

## DE – TransferEngine (`core/transfer_engine.py`)
### Kernidee
TransferEngine ist eine **lightweight Transfer-Bridge**:
- persistiert **TransferSnaps** (sequence/pattern)
- markiert Kandidaten für Export (score/len thresholds)
- schreibt KPI Metrics (best effort)

### Datenmodell (TransferSnap, konzeptuell)
- `sequence`: Liste symbolischer Events (CSV in DB)
- `pattern`: komprimierter Schlüssel (string)
- optional `score`, `marked`, `mark_ts`

### Export-Heuristik
`consider_export(snap_id, score, min_score=0.80, min_len=2)`:
- threshold auf score
- minimum sequence length
- bei Erfolg: `mark_export(snap_id)` + KPI `kpi:export_marked`

**ENV**
- DBWriter: `OROMA_DBW_ENABLE`, `OROMA_DBW_TIMEOUT_MS`

---

## DE – Roter Faden (`core/roter_faden.py`)
### Kernidee
“Roter Faden” ist eine dünne, persistente **Thread-Schicht**:
- hält Titel, Objective, Steps, idx, Status (run/pause/done)
- persistiert in `curriculum_state.window` via:
  - `sql_manager.fetch_curriculum_state()`
  - `sql_manager.update_curriculum_state()`

### Gap-Integration
Optional (fail-safe):
- wenn `core.gaps` verfügbar: `note_gap(...)` protokolliert zentral mit Thread-Context
- Auto-Gaps bei Idle-Nudges / step_failed (ENV gesteuert, throttled)

**ENV**
- `OROMA_THREAD_AUTO_GAPS` (Default 1)
- `OROMA_THREAD_GAP_MIN_GAP_SEC` (Default 300)
- `OROMA_THREAD_NUDGE_MIN_GAP_SEC` (Default 600)

---

## DE – Decision Engine (kurz)
`core/decision_engine.py` enthält domänenspezifische Entscheidungen (z. B. TicTacToe Solver Toggle) über:
- `OROMA_TTT_SOLVER`

---

## DE – Bezug zum Code
- Relevante Dateien:
  - `core/universal_policy.py`
  - `core/policy_engine.py`
  - `core/transfer_engine.py`
  - `core/roter_faden.py`
  - (optional referenziert) `core/decision_engine.py`
- Verwandte Core-Dokus:
  - `docs/core/22_snapchain.md`
  - `docs/core/90_publication.md`
  - (Phase 2) `docs/core/50_db_layer.md`

---

## EN

### Purpose
This document describes ORÓMA’s **policy and transfer core**, based on four modules that together cover the decision layer and long-term reusability of experience:

- `core/universal_policy.py` (**UniversalPolicy / UP**) – domain-agnostic tabular policy `(state_hash → action)` with online learning and optional auto-export to a rule archive.
- `core/policy_engine.py` (**PolicyEngine**) – learns from SnapChains into `policy_rules` via adapters; can export rules.
- `core/transfer_engine.py` (**TransferEngine**) – lightweight “TransferSnaps” (sequence/pattern) + export marking + KPI metrics (compat patch API).
- `core/roter_faden.py` (**Roter Faden**) – persistent thread/intent context with gap integration and idle nudges (contextual continuity).

`core/decision_engine.py` is referenced briefly because it can encapsulate domain decisions (e.g., TicTacToe solver toggle).

### Scope / Non-goals
- ✅ In scope: state_hash/action model, policy_rules semantics, adapter canonicalization, learning paths (vector vs prehash), auto-export criteria, transfer snaps, thread-state persistence, gap logging.
- ❌ Out of scope: full game implementations, UI, benchmark RL, large planners/LLM agent frameworks.

---

## EN – Architectural role (conceptual)
**SnapChain (episode) → PolicyEngine/UP (learn) → policy_rules (runtime choose) → export (rules) → TransferEngine (pattern sequence)**  
In parallel: **Roter Faden** maintains a minimal thread context and records gaps/idle friction for later analysis.

---

## EN – UniversalPolicy (`core/universal_policy.py`)
### Core idea
A small policy operating purely on a **string state_hash**:
- `choose(state_hash, legal, side)` chooses an action based on `q` and `n`
- `learn_many(items)` upserts into `policy_rules`

`policy_rules` expected columns:
`(namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)`

### Choose
- argmax on `q` (optional softmax via temperature)
- tie-break via `n` (and optional priors)
- side-aware action canonicalization through adapters

Key env vars:
`OROMA_UP_TEMP`, `OROMA_UP_PRIOR_N`,  
`OROMA_UP_AUTO_EXPORT`, `OROMA_UP_MIN_N`, `OROMA_UP_MIN_ABS_Q`, `OROMA_UP_MAJ_CONF`, `OROMA_UP_COOLDOWN_S`,  
export cache controls, gaps controls, and DBWriter controls (`OROMA_DBW_ENABLE`, `OROMA_POLICY_DBW_CHUNK`).

### Learn
- outcomes update pos/neg/draw and `q`
- compact upsert for edge operation

### Auto export
Exports “good” rules into the archive when enabled.

---

## EN – PolicyEngine (`core/policy_engine.py`)
### Core idea
Learns from **SnapChains** into `policy_rules`.
Sources:
- DB `snapchains`
- fallback JSON exports by `source_id` in `OROMA_SNAPCHAINS_DIR`

### Adapter system
Prefers UniversalAdapter, falls back to TicTacToe adapter. Adapters provide state canonicalization and action mapping.

### Two learning paths
(A) vector/steps path  
(B) pre-hash fallback for chains without vectors

### Auto export
Controlled by `OROMA_PE_AUTO_EXPORT` and threshold/cooldown env vars.

### DBWriter integration
Uses DBWriter write paths when enabled to reduce lock contention.

---

## EN – TransferEngine (`core/transfer_engine.py`)
Lightweight transfer bridge:
- persists transfer snaps (sequence/pattern)
- marks export candidates by score/len thresholds
- emits KPI metrics (best effort)

DBWriter-related env vars: `OROMA_DBW_ENABLE`, `OROMA_DBW_TIMEOUT_MS`.

---

## EN – Roter Faden (`core/roter_faden.py`)
Thin persistent thread layer:
- stores title/objective/steps/idx/status in `curriculum_state.window`
- integrates with `core.gaps` when available
- idle nudges and gap throttling via env:
  `OROMA_THREAD_AUTO_GAPS`, `OROMA_THREAD_GAP_MIN_GAP_SEC`, `OROMA_THREAD_NUDGE_MIN_GAP_SEC`.

---

## EN – Decision engine (brief)
`core/decision_engine.py` exposes domain-specific toggles such as:
- `OROMA_TTT_SOLVER`

---

## EN – Code mapping
- `core/universal_policy.py`
- `core/policy_engine.py`
- `core/transfer_engine.py`
- `core/roter_faden.py`
- (optional) `core/decision_engine.py`
- Related core docs:
  - `docs/core/22_snapchain.md`
  - `docs/core/50_db_layer.md`
  - `docs/core/90_publication.md`

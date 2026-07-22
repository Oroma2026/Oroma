# ORÓMA – Dokumentation (gesplittet)
- [current_state_gap_policy_mini_write_outcome_queue_v02_20260712.md](current_state_gap_policy_mini_write_outcome_queue_v02_20260712.md) – Phase 2.1: Outcome Queue als Mini-Write-Quelle, atomarer Policy-/Evidence-Link und lokaler Snake-End-to-End-Nachweis

- [POLICY_WRITER_REGISTRY.md](POLICY_WRITER_REGISTRY.md) – lebendes Inventar aller bestätigten Policy-Writer, Mutationsmechanismen, Boundary-Status und Migrationsziele
Diese Dateien sind automatisch gesplittet (**max. 2000 Zeilen pro Datei**), damit du sie leichter in Chats kopieren kannst.

Quick-Entry:
- [ARCHITECTURAL_INVARIANTS.md](ARCHITECTURAL_INVARIANTS.md) – übergeordnete, projektweit geltende Systemgesetze für Evidence, Audit, Policy-Mutation, Contracts, Confidence, Reproduzierbarkeit und Versionierung
- [VERTICAL_LEARNING_ARCHITECTURE.md](VERTICAL_LEARNING_ARCHITECTURE.md) – Zielarchitektur und Migrationsrahmen für den projektweiten Lernfluss von Sensor/Episode über Gap, Replay/Dream, Evidence und Gate bis zur Policy
- [LEARNING_CONTRACTS.md](LEARNING_CONTRACTS.md) – technische Verträge für Execution, Sensor, Episode, Evidence, Outcome, Policy und Explanation
- [POLICY_MUTATION_BOUNDARY.md](POLICY_MUTATION_BOUNDARY.md) – projektweite Kontrollgrenze für registrierte, gegatete und auditierbare Änderungen am Persistent Policy Storage
- [OROMA_CORE_MODULE_ROLE_AUDIT_20260712.md](OROMA_CORE_MODULE_ROLE_AUDIT_20260712.md) – vollständige Rollenkarte aller produktiven Core-Python-Module im vertikalen Lernen
- [OROMA_ALL_PYTHON_MODULE_ROLE_AUDIT_20260712.md](OROMA_ALL_PYTHON_MODULE_ROLE_AUDIT_20260712.md) – projektweite Rollenkarte aller 356 Python-Dateien als Migrationsreferenz
- [current.md](current.md) – kurzer Copy-Block / „Wo stehe ich gerade?“
- [current_state_snake3d_dream_policy_loop_gap_miner_20260707.md](current_state_snake3d_dream_policy_loop_gap_miner_20260707.md) – Snake3D Dream→Policy Loop, Auto-Mini-Write und Gap-Miner Large-DB-Stand
- [current_state_learning_loop_autarky_audit_20260707.md](current_state_learning_loop_autarky_audit_20260707.md) – Lernloop-/Autarkie-Audit: NMR, Gaps, Replay, PTZ, Curiosity, Synapsen und Learning Focus Queue
- [current_state_gap_learning_bridge_20260709.md](current_state_gap_learning_bridge_20260709.md) – Gap-Learning-Bridge Dry-Run: knowledge_gaps werden als read-only Learning-Focus-State nutzbar
- [current_state_gap_focus_consumer_20260709.md](current_state_gap_focus_consumer_20260709.md) – Gap-Focus Consumer Read-Only: Learning-Focus-State als sichere Verbraucher-Buckets sichtbar
- [current_state_gap_focus_shadow_plan_20260709.md](current_state_gap_focus_shadow_plan_20260709.md) – Gap-Focus Shadow Plan Read-Only: sichere Review-/Anschlussplanung ohne Runner-, Replay-, Dream- oder Policy-Start
- [current_state_gap_evidence_queue_20260710.md](current_state_gap_evidence_queue_20260710.md) – Gap Evidence Queue Writer: erster sicherer DBWriter-only Write für Review-/Evidence-Requests ohne Policy-Write
- [current_state_gap_evidence_review_20260710.md](current_state_gap_evidence_review_20260710.md) – Gap Evidence Queue Review Dry-Run: Queue read-only bewerten und Review-Buckets ohne Writes/Starts erzeugen
- [current_state_gap_evidence_validation_20260710.md](current_state_gap_evidence_validation_20260710.md) – Gap Evidence Execution/Validation Dry-Run: Review-Kandidaten read-only validieren, ohne Starts/Writes
- [current_state_gap_policy_promotion_20260710.md](current_state_gap_policy_promotion_20260710.md) – Gap Policy Promotion Queue: DBWriter-only Approval-Queue vor Policy-Mini-Write, ohne policy_rules-Write
- [current_state_replay_safe_policy_gate_20260709.md](current_state_replay_safe_policy_gate_20260709.md) – Replay Safe Policy Gate: sichtbarer Replay-Lernwunsch, DBWriter-only Write-Gate, Default read-only
- [current_state_ptz_zoom_20260629.md](current_state_ptz_zoom_20260629.md) – P3z1c Wide-Observe-Zoom Live-Verifikation
- [current_state_ptz_nmr_20260619.md](current_state_ptz_nmr_20260619.md) – PTZ-/NMR-Evidence-Stand und nächste Gates

Hinweis zur Historie:
- ORÓMA-Projektstart: **Juli 2025** (nicht 2023)

Altbestände:
- Ältere/ersetzte Doku-Dateien liegen unter **docs/history/**.

## Inhalt

- [ARCHITECTURAL_INVARIANTS.md](ARCHITECTURAL_INVARIANTS.md)

- [LEARNING_CONTRACTS.md](LEARNING_CONTRACTS.md)

- [POLICY_MUTATION_BOUNDARY.md](POLICY_MUTATION_BOUNDARY.md)

- [current.md](current.md)
- [current_state_snake3d_dream_policy_loop_gap_miner_20260707.md](current_state_snake3d_dream_policy_loop_gap_miner_20260707.md)
- [current_state_learning_loop_autarky_audit_20260707.md](current_state_learning_loop_autarky_audit_20260707.md)
- [current_state_gap_learning_bridge_20260709.md](current_state_gap_learning_bridge_20260709.md)
- [current_state_gap_focus_shadow_plan_20260709.md](current_state_gap_focus_shadow_plan_20260709.md)
- [current_state_gap_evidence_queue_20260710.md](current_state_gap_evidence_queue_20260710.md)
- [current_state_gap_evidence_review_20260710.md](current_state_gap_evidence_review_20260710.md)
- [current_state_gap_evidence_validation_20260710.md](current_state_gap_evidence_validation_20260710.md)
- [current_state_replay_safe_policy_gate_20260709.md](current_state_replay_safe_policy_gate_20260709.md)
- [current_state_ptz_zoom_20260629.md](current_state_ptz_zoom_20260629.md)
- [current_state_ptz_nmr_20260619.md](current_state_ptz_nmr_20260619.md)
- [architecture_part01.md](architecture_part01.md)
- [architecture_part02.md](architecture_part02.md)
- [architecture_part03.md](architecture_part03.md)
- [architecture_part04.md](architecture_part04.md)
- [architecture_part05.md](architecture_part05.md)
- [changelog_full_part01.md](changelog_full_part01.md)
- [changelog_full_part02.md](changelog_full_part02.md)
- [deployment.md](deployment.md)
- [docs_konsolidierung.md](docs_konsolidierung.md)
- [games_and_tasks_part01.md](games_and_tasks_part01.md)
- [games_and_tasks_part02.md](games_and_tasks_part02.md)
- [konzeption_architektur_v3_7_1.md](konzeption_architektur_v3_7_1.md)
- [maintenance.md](maintenance.md)
- [projektstruktur.md](projektstruktur.md)
- [roadmap_part01.md](roadmap_part01.md)
- [roadmap_part02.md](roadmap_part02.md)
- [snaps_todo.md](snaps_todo.md)
- [ui.md](ui.md)

- `current_state_gap_policy_mini_write_gate_20260710.md` – Fail-closed Gap Policy Mini-Write Gate mit Ledger und Evidence-Outcome-Pflicht.


- `current_state_gap_evidence_outcome_20260710.md` – Gap Evidence Outcome Collector Dry-Run: read-only Beweis-Suchstufe vor dem Policy-Mini-Write-Gate.

- [Gap Targeted Evidence Probe 2026-07-10](current_state_gap_targeted_evidence_probe_20260710.md) – bounded Read-Only-Diagnose für wenige Promotion-Kandidaten, um historische Format-/Evidence-Mismatches sichtbar zu machen.

- [Gap Targeted Replay Evidence Probe 2026-07-10](current_state_gap_replay_evidence_probe_20260710.md) – lokaler Headless-Replay-Probe für Gap-Promotion-Kandidaten.

- [Gap Evidence Outcome Queue Gate – 2026-07-11](current_state_gap_evidence_outcome_queue_20260711.md)

- [Replay-Evidence-Probe Performance-/Freshness-Fix 2026-07-11](current_state_gap_replay_probe_performance_fix_20260711.md) – entfernt den Millionenzeilen-COUNT aus dem Probe-Hot-Path.

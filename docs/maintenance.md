<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__maintenance.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

# ORÓMA – Wartung, Audits & Migrationen (konsolidiert)

> Hinweis: Projektstart war **Juli 2025** (nicht 2023).


Stand: 2025-12-25


Interne Wartungsnotizen, Audit-Dokumente und Migrationshinweise (zusammengeführt).

## Quellen (konsolidiert)

- `docs/audit_v3_7_1_deltareview.md`

- `docs/doc_link_audit.md`

- `docs/doc_ref_audit.md`

- `docs/docs_konsolidierung.md`

- `docs/module_vector_migration.md`

---

<a id="docs_audit_v3_7_1_deltareview_md"></a>

## Quelle: `docs/audit_v3_7_1_deltareview.md`

**Originaltitel:** Logs prüfen: Meta, Mutation, Forgetting, Kompression

📄 docs/audit_v3_7_1_deltareview.md
Projekt: ORÓMA
Titel: Architektur-Delta-Review v3.7 → v3.7.1 (Regelarchiv-First)
Version: v1.0
Stand: 2025-10-18
Autor: ORÓMA · KI-JWG-X1

⸻

1) Executive Summary

• Bewertung: Die Umstellung auf Regelarchiv-First mit Kanonraum, PolicyEngine v3.8 und DreamWorker v3.7 ist konsistent, erklärbar und produktionstauglich.
• Wirkung: Höhere Entscheidungsstabilität (Archiv/Policy), geringerer Fallback-Anteil, bessere Datenökonomie (Forgetting/Kompression).
• Reifegrad: „Erwachsenen-Zustand“ – Debuggbar, auditierbar, mit klaren KPIs.

⸻

2) Delta (v3.7 → v3.7.1)

Neu
• core/decision_engine.py: 3-stufig (Archiv → Policy → Heuristik), Legalitätscheck, Kanonraum-Mapping.
• Adapter (z. B. core/ttt_adapter.py v1.1): robuste Extractors, D4-Symmetrien, Fallback-Heuristik.
• policy_engine.py v3.8-r3: Training inkl. status='compressed', robustes Upsert, Archiv-Export.

Geändert
• core/dream_worker.py v3.7: Vector-First Loader, Meta-Zentroid, Mutation, Forgetting (weight-decay + Kompression), LTM-Dedupe.

⸻

3) Risiken & Gegenmaßnahmen

R1 Kanonraum-Fehlmapping (falsche Permutation)
  • Maßnahme: Unit-Tests für _SYMM, map_action_through_perm; Goldens für 10 TTT-Boards.

R2 Leere Policy trotz Daten (Filter)
  • Maßnahme: Train-CLI mit --include-compressed nutzen; Namespace-Filter auf '*' testen.

R3 Archiv-Regeln mit illegalen Aktionen
  • Maßnahme: DecisionEngine prüft legal_actions(); zusätzlich beim Export Soft-Check einbauen.

R4 Über-Kompression (zu frühes Vergessen)
  • Maßnahme: OROMA_FORGET_THRESHOLD auf 0.25–0.30 testen; Coverage-Trend beobachten.

⸻

4) KPIs & Akzeptanz-Gates

K1 Archiv-Hit-Rate (Anteil Entscheidungen Stufe-1)
  • Ziel ≥ 60 % nach erstem Training; Alert < 40 %.

K2 Fallback-Rate (Heuristik)
  • Ziel ≤ 10 %; Alert > 25 %.

K3 Policy-Abdeckung
  • ≥ 1.5 Aktionen/State im Median; |q|-Median ≥ 0.1 nach 10k Schritten.

K4 Kompressionsquote (snapchains compressed/gesamt, 7-Tage)
  • Ziel 20–50 %; Alert > 70 %.

K5 Latenz DecisionEngine
  • p95 < 5 ms auf Pi-Klasse (Archiv + Policy-Lookup).

⸻

5) Monitoring / Instrumentierung

Logs
• decision.path: 'archive' | 'policy' | 'fallback'
• decision.ns, state_hash, action, legal=1/0
• policy.stats: q, n, candidates
• dream.forget: {id, old_w, new_w, compressed?}

Indizes (ergänzen, wenn nicht vorhanden)
• CREATE INDEX IF NOT EXISTS idx_rules_active_ns ON rules(active);
• CREATE INDEX IF NOT EXISTS idx_policy_ns_sh ON policy_rules(namespace, state_hash);
• CREATE INDEX IF NOT EXISTS idx_snapchains_status ON snapchains(status);

⸻

6) Performance-Einschätzung (Pi-Edge + optionale Hailo-NPU)

Laufzeit
• DecisionEngine: String-Parse + kleine DB-Scans → typ. 1–3 ms, p95 < 5 ms.
• Policy-Training: I/O-gebunden; 10k Chains ~ Sekundenbereich (Batchweise).
• DreamWorker: Hintergrund; I/O-dominiert; minimaler CPU-Impact.

NPU-Bezug
• Archiv/Policy sind tabellarisch (CPU). NPU lohnt sich später für AV-Adapter (Feature-Extraktion).
• Export-Pfad: Archiv/Policy → kompakte Tabellenschnappschüsse; AV-Modelle → Hailo via separatem Adapter.

⸻

7) Test-/Rollout-Plan (kompakt)

Schema
  python3 -m core.sql_manager --ensure

Training + Export
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --train-db --limit 20000 --namespace game:tictactoe --include-compressed --verbose
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --export-archiv --namespace game:tictactoe --min-n 3 --min-abs-q 0.15 --verbose

Smoke für Entscheidungen (TTT)
  from core.decision_engine import TTTDecision
  dec = TTTDecision()
  act = dec.choose_action_from_board(["X","O","","","","","","",""])  # legal? nicht None?

DreamWorker Single-Run
  PYTHONPATH=/opt/ai/oroma python3 -m core.dream_worker --interval 0 --verbose
  # Logs prüfen: Meta, Mutation, Forgetting, Kompression

Akzeptanz
• K1 ≥ 60 %, K2 ≤ 10 %, p95 < 5 ms über 500 Entscheidungen.

⸻

8) Roadmap-Hinweise (v3.7.2 / v3.8)

• AV-Adapter (Audio/Video): Token-Extraktion + Kanonisierung (Zeit/Geometrie-Invarianten).
• Archiv-Explainability: „Top-Grund“ je Entscheidung (welche Regel/Policy gewann).
• Cross-Domain-Affinität: Zustände Audio↔Video↔LLM verlinken (gemeinsamer Meta-Hash).
• Edge-Export: Hailo-kompatible Pfade (Operator-Subset, Quantisierung) + Policy-Snapshot.

⸻

Anhang A – Health-Queries

-- Archiv-Treffer (Top 20 States)
SELECT substr(content, 1, 60) rule, weight
FROM rules WHERE active=1 ORDER BY weight DESC LIMIT 20;

-- Policy-Coverage
SELECT state_hash, COUNT(*) actions, AVG(ABS(q)) absq
FROM policy_rules WHERE namespace='game:tictactoe'
GROUP BY state_hash ORDER BY actions DESC LIMIT 20;

<a id="docs_doc_link_audit_md"></a>

## Quelle: `docs/doc_link_audit.md`

**Originaltitel:** DOC Link Audit – Auto-Fix Runde 3 (Doc→Doc)

> **Hinweis (nach Konsolidierung):** `docs/` und `docs/` wurden entfernt. Historische/Module-Dokumente liegen jetzt in `docs/` und sind anhand des Prefix `history_` bzw. `module_` erkennbar.
> Zusätzlich wurden einige alte Dateinamen mit kaputtem Encoding bereinigt; in diesem Audit können noch Artefakte aus der Vorbereinigung stehen.

Stand: 2025-12-24

Quelle: oroma_20251224_081440_with_db.zip

## Ergebnis

- Automatisch korrigierte Referenzen (eindeutig): **19**
- Übrig (nicht eindeutig / nicht auflösbar ohne Handarbeit): **48**

## Was wurde korrigiert (eindeutig)

### docs/dream_cycle.md
- `docs/dream_worker.md` → `docs/dream_worker.md`  _(match: basename)_

### docs/gesamtanalyse_projektstand_2025-12-03.md
- `docs/scenegraph_builder.md` → `docs/scenegraph_builder.md`  _(match: basename)_

### docs/scenegraph_builder.md
- `docs/dream_worker.md` → `docs/dream_worker.md`  _(match: basename)_

### docs/history_entwicklungsleiter.md
- `docs/history_entwicklungsleiter.md` → `docs/history_entwicklungsleiter.md`  _(match: basename)_

### docs/history_oroma_v2_30_doku.md
- `docs/referenz_handbuch.md` → `docs/referenz_handbuch.md`  _(match: basename)_
- `docs/administrator-handbuch.md` → `docs/administrator-handbuch.md`  _(match: basename)_
- `docs/projektstruktur.md` → `docs/projektstruktur.md`  _(match: basename)_

### docs/history_projektstruktur_patch_roadmap.md
- `docs/README.md` → `docs/README.md`  _(match: basename)_
- `docs/changelog.md` → `docs/changelog.md`  _(match: basename)_
- `docs/referenz_handbuch.md` → `docs/referenz_handbuch.md`  _(match: basename)_
- `docs/administrator-handbuch.md` → `docs/administrator-handbuch.md`  _(match: basename)_
- `docs/konzeption_architektur.md` → `docs/konzeption_architektur.md`  _(match: basename)_
- `docs/projektstruktur.md` → `docs/projektstruktur.md`  _(match: basename)_
- `docs/integrationstest.md` → `docs/integrationstest.md`  _(match: basename)_
- `docs/changelog.md` → `docs/changelog.md`  _(match: basename)_
- `docs/readme_addons.md` → `docs/readme_addons.md`  _(match: basename)_
- `docs/projektstruktur.md` → `docs/projektstruktur.md`  _(match: basename)_

### docs/history_fahrplan_3_5.md
- `docs/changelog.md` → `docs/changelog.md`  _(match: basename)_
- `docs/changelog.md` → `docs/changelog.md`  _(match: basename)_

## Übrig (nicht eindeutig)

Hinweis: Diese Stellen wurden **absichtlich nicht** automatisch geändert, weil mehrere plausible Ziele existieren oder die Referenz historisch/konzeptionell ist.

### core/snaps_todo.md (3)
- `TODO.md`
- `docs/todo_snaps_v3_8.md`
- `docs/todo_snaps_v3_8.md`

### docs/ai_assisted_development.md (4)
- `Shared_KI_Memory.md`
- `api_spec.md`
- `docs/Shared_KI_Memory.md`
- `docs/machine/api_spec.md`

### docs/ankeitung_auslesen_sql_komsole.md (2)
- `DB_Auslesen_OROMA.md`
- `docs/DB_Auslesen_OROMA.md`

### docs/audit_v3_7_1_deltareview.md (1)
- `1_DeltaReview.md`

### docs/changelog_v3_5patch1.md (2)
- `5patch1.md`
- `5patch2.md`

### docs/gesamtanalyse_projektstand_2025-12-03.md (1)
- `7.md`

### docs/konzeption_architektur_v3_5_patch2_1.md (2)
- `1.md`
- `PatchLevel2.md`

### docs/konzeption_architektur_v3_5_patch2.md (2)
- `5_PatchLevel2.md`
- `docs/konzeption_architektur_v3_5_patch2.md`

### docs/konzeption_architektur_v3_6.md (1)
- `6.md`

### docs/konzeption_architektur_v3_6_patch2_mengenlehre.md (1)
- `6_Patch2_Mengenlehre.md`

### docs/konzeption_architektur_v3_7_1.md (1)
- `1.md`

### docs/konzeption_architektur_v3_7_2.md (2)
- `2.md`
- `docs/benchmarks/ttt_v3.7.2.md`

### docs/konzeption_architektur_v3_7_3.md (1)
- `3.md`

### docs/konzeption_architektur_v3_7.md (1)
- `7.md`

### docs/konzeption_architektur_v3_8.md (1)
- `8.md`

### docs/konzeption_architektur_v3_9.md (1)
- `9.md`

### docs/oroma_wissenschaftliche_tiefenanalyse.md (1)
- `8.md`

### docs/readme_db.md (2)
- `5.md`
- `docs/datenbankverwaltung.md`

### docs/roadmap_v3_5_patch_2_1.md (2)
- `1.md`
- `docs/roadmap_v3_5_patch_2_1.md`

### docs/Roadmap docs/roadmap_v3_5_patch_2_0.md (2)
- `5patch2.md`
- `docs/roadmap_v3_5_patch_2_1.md`

### docs/technische_aenderungen.md (1)
- `8.md`

### docs/todo_snaps_v3_8.md (1)
- `8.md`

### docs/upgrade-guide-v2_11-to-3_8.md (1)
- `8.md`

### docs/analysevonzweikis.md (2)
- `Meta_Review_Discussion.md`
- `docs/Meta_Review_Discussion.md`

### docs/fazit_3_8.md (2)
- `8.md`
- `docs/DB_Analyse_OROMA_v3.8.md`

### docs/history_architektur_final_v3_5.md (1)
- `5.md`

### docs/history_changelog_final_v3_0.md (1)
- `0.md`

### docs/OROªⁿdocs/ORO╠üMA_v3.5_Master-Zusammenfassung.md (2)
- `docs/ORO╠üMA_v3.5_Master-Zusammenfassung.md`
- `docs/history_oroma_v3_0_master-zusammenfassung.md`

### docs/ORO╠üdocs/ORO╠üMA_v3.5_Master-Zusammenfassung.md (2)
- `docs/ORO╠üMA_v3.5_Master-Zusammenfassung.md`
- `docs/history_oroma_v3_0_master-zusammenfassung.md`

### docs/history_fahrplan_3_5.md (1)
- `5.md`

### docs/simulationsvergleich_3_6vs4_0.md (1)
- `0.md`

<a id="docs_doc_ref_audit_md"></a>

## Quelle: `docs/doc_ref_audit.md`

**Originaltitel:** ORÓMA – DOC Reference Audit & Auto-Fix

Stand: 2025-12-24 14:01:31 (lokal)
Basis-ZIP: oroma_20251224_122253_with_db.zip

## Summary
- Geprüfte Markdown-Dateien: 178
- Dateien mit Änderungen: 94
- Auto-Fixes/Normalisierungen: 225
- Unaufgelöste Referenzen (Tokens): 69

## Top Dateien nach Anzahl Änderungen
- `docs/doc_link_audit.md`: 32
- `docs/changelog_full.md`: 27
- `docs/scenegraph_builder.md`: 14
- `docs/roadmap.md`: 7
- `docs/ai_assisted_development.md`: 7
- `docs/module_besonderheit_miniprogramme.md`: 6
- `docs/dream_worker.md`: 4
- `docs/oroma_bewusstsein_map.md`: 4
- `docs/konzeption_architektur_kurz.md`: 4
- `docs/changelog_v3_5patch1.md`: 4
- `docs/history_fahrplan_3_5.md`: 4
- `docs/readme_db.md`: 3
- `docs/readme_addons.md`: 3
- `docs/konzeption_architektur_v3_7.md`: 3
- `docs/konzeption_architektur_v3_7_1.md`: 3
- `docs/konzeption_architektur_v3_6.md`: 3
- `docs/ki_readme.md`: 3
- `docs/module_ui.md`: 3
- `docs/history_projektstruktur_patch_roadmap.md`: 3
- `docs/history_oroma_v2_30_doku.md`: 3
- `docs/vergleich_markt.md`: 2
- `docs/todo_snaps_v3_8.md`: 2
- `docs/status_v3_7_abgeschlossenheit.md`: 2
- `docs/readme_replay.md`: 2
- `docs/modelle_installation.md`: 2

## Top Dateien nach Anzahl ungefixter Referenzen
- `docs/doc_link_audit.md`: 40
- `docs/oroma_wissenschaftliche_tiefenanalyse.md`: 12
- `docs/analysevonzweikis.md`: 5
- `docs/technische_aenderungen.md`: 2
- `docs/konzeption_architektur_v3_7_2.md`: 2
- `docs/ai_assisted_development.md`: 2
- `docs/fazit_3_8.md`: 1
- `docs/readme_models.md`: 1
- `docs/konzeption_architektur_v3_5_patch2_1.md`: 1
- `docs/gesamtanalyse_3_7.md`: 1
- `docs/ankeitung_auslesen_sql_komsole.md`: 1
- `core/snaps_todo.md`: 1

## Änderungslog (gekürzt)
Format: Datei | alt → neu | Grund

- `ui/static/chart.min.js.md` | `current.md` → `docs/current.md` | basename_unique
- `systemd/dienste.md` | `fusion.md` → `docs/module_fusion.md` | basename_unique
- `systemd/README-systemd.md` | `/opt/ai/oroma/v2.30/systemd/README-systemd.md` → `systemd/README-systemd.md` | basename_unique
- `docs/snaps_vs_embeddings.md` | `/opt/ai/oroma/docs/snaps_vs_embeddings.md` → `docs/snaps_vs_embeddings.md` | normalize_prefix
- `docs/simulationsvergleich_3_6vs4_0.md` | `/opt/ai/oroma/docs/simulationsvergleich_3_6vs4_0.md` → `docs/simulationsvergleich_3_6vs4_0.md` | normalize_prefix
- `docs/konzept_2d_3d_space.md` | `/opt/ai/oroma/docs/konzept_2d_3d_space.md` → `docs/konzept_2d_3d_space.md` | normalize_prefix
- `docs/dream_worker.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/dream_worker.md` | `circadian_controller.md` → `docs/module_circadian_controller.md` | basename_unique
- `docs/dream_worker.md` | `OROMA_changelog.md` → `docs/history_oroma_changelog.md` | basename_unique
- `docs/dream_worker.md` | `logikcheck.md` → `docs/logikcheck.md` | basename_unique
- `docs/vergleich_markt.md` | `/opt/ai/oroma/docs/vergleich_markt.md` → `docs/vergleich_markt.md` | normalize_prefix
- `docs/vergleich_markt.md` | `/opt/ai/oroma/docs/vergleich_markt.md` → `docs/vergleich_markt.md` | normalize_prefix
- `docs/upgrade-guide-v2_11-to-3_8.md` | `upgrade-guide-v2_11-to-3_8.md` → `docs/upgrade-guide-v2_11-to-3_8.md` | basename_unique
- `docs/todo_snaps_v3_8.md` | `/opt/ai/oroma/docs/todo_snaps_v3_8.md` → `docs/todo_snaps_v3_8.md` | normalize_prefix
- `docs/todo_snaps_v3_8.md` | `TODO_Snaps.md` → `docs/todo_snaps_v3_8.md` | fuzzy_auto(0.85)
- `docs/technische_aenderungen.md` | `/opt/ai/oroma/docs/upgrade-guide-v2_11-to-3_8.md` → `docs/upgrade-guide-v2_11-to-3_8.md` | normalize_prefix
- `docs/status_v3_7_abgeschlossenheit.md` | `/opt/ai/oroma/docs/status_v3_7_abgeschlossenheit.md` → `docs/status_v3_7_abgeschlossenheit.md` | normalize_prefix
- `docs/status_v3_7_abgeschlossenheit.md` | `/opt/ai/oroma/docs/status_v3_7_abgeschlossenheit.md` → `docs/status_v3_7_abgeschlossenheit.md` | normalize_prefix
- `docs/stats_db.md` | `/opt/ai/oroma/docs/stats_db.md` → `docs/stats_db.md` | normalize_prefix
- `docs/simulation_patch2.md` | `simulation_patch2.md` → `docs/simulation_patch2.md` | basename_unique
- `docs/sensor_architektur.md` | `sensor_architektur.md` → `docs/sensor_architektur.md` | basename_unique
- `docs/schema_dbs.md` | `/opt/ai/oroma/docs/schema_dbs.md` → `docs/schema_dbs.md` | normalize_prefix
- `docs/scenegraph_builder.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/scenegraph_builder.md` | `current.md` → `docs/current.md` | basename_unique
- `docs/scenegraph_builder.md` | `dbcleanhealth.md` → `docs/dbcleanhealth.md` | basename_unique
- `docs/scenegraph_builder.md` | `cores_konzept.md` → `docs/cores_konzept.md` | basename_unique
- `docs/scenegraph_builder.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/scenegraph_builder.md` | `circadian_controller.md` → `docs/module_circadian_controller.md` | basename_unique
- `docs/scenegraph_builder.md` | `OROMA_changelog.md` → `docs/history_oroma_changelog.md` | basename_unique
- `docs/scenegraph_builder.md` | `logikcheck.md` → `docs/logikcheck.md` | basename_unique
- `docs/scenegraph_builder.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/scenegraph_builder.md` | `quick_check_3_6.md` → `docs/quick_check_3_6.md` | basename_unique
- `docs/scenegraph_builder.md` | `curriculum_math_tasks.md` → `docs/curriculum_math_tasks.md` | basename_unique
- `docs/scenegraph_builder.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/scenegraph_builder.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/scenegraph_builder.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/roadmap_nmr_concept.md` | `/opt/ai/oroma/docs/roadmap_nmr_concept.md` → `docs/roadmap_nmr_concept.md` | normalize_prefix
- `docs/roadmap_v3_5_patch_2_0.md` | `/opt/ai/oroma/docs/ROADMAP_v3.5patch2.md` → `docs/roadmap_v3_5_patch_2_1.md` | fuzzy_auto(0.92)
- `docs/referenz_handbuch.md` | `/opt/ai/oroma/v3.7/docs/referenz_handbuch.md` → `docs/referenz_handbuch.md` | basename_unique
- `docs/roadmap_v3_5_patch_2_1.md` | `/opt/ai/oroma/docs/ROADMAP_v3.5patch2.1.md` → `docs/roadmap_v3_5_patch_2_1.md` | fuzzy_auto(0.97)
- `docs/roadmap.md` | `/opt/ai/oroma/docs/roadmap.md` → `docs/roadmap.md` | normalize_prefix
- `docs/roadmap.md` | `roadmap_2026.md` → `docs/roadmap_2026.md` | basename_unique
- `docs/roadmap.md` | `quick_check_3_6.md` → `docs/quick_check_3_6.md` | basename_unique
- `docs/roadmap.md` | `curriculum_math_tasks.md` → `docs/curriculum_math_tasks.md` | basename_unique
- `docs/roadmap.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/roadmap.md` | `changelog_full.md` → `docs/changelog_full.md` | basename_unique
- `docs/roadmap.md` | `roadmap_2026.md` → `docs/roadmap_2026.md` | basename_unique
- `docs/readme_synapses.md` | `/opt/ai/oroma/v3.5/docs/readme_synapses.md` → `docs/readme_synapses.md` | basename_unique
- `docs/readme_replay.md` | `readme_replay.md` → `docs/readme_replay.md` | basename_unique
- `docs/readme_replay.md` | `/opt/ai/oroma/v3.5/docs/readme_replay.md` → `docs/readme_replay.md` | basename_unique
- `docs/readme_knowledge.md` | `/opt/ai/oroma/v3.5/docs/readme_knowledge.md` → `docs/readme_knowledge.md` | basename_unique
- `docs/readme_db.md` | `Datenbankverwaltung_v3.5.md` → `docs/datenbankverwaltung.md` | fuzzy_auto(0.90)
- `docs/readme_db.md` | `/opt/ai/oroma/docs/Datenbankverwaltung_v3.5.md` → `docs/datenbankverwaltung.md` | fuzzy_auto(0.90)
- `docs/readme_db.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/readme_addons.md` | `README.md` → `docs/README.md` | basename_unique
- `docs/readme_addons.md` | `README.md` → `docs/README.md` | basename_unique
- `docs/readme_addons.md` | `/opt/ai/oroma/v3.5/docs/readme_addons.md` → `docs/readme_addons.md` | basename_unique
- `docs/README.md` | `docs/scenegraph_builder.md` → `docs/scenegraph_builder.md` | fuzzy_auto(1.01)
- `docs/projektstruktur.md` | `/opt/ai/oroma/docs/projektstruktur.md` → `docs/projektstruktur.md` | normalize_prefix
- `docs/projektbeschreibung.md` | `/opt/ai/oroma/docs/projektbeschreibung.md` → `docs/projektbeschreibung.md` | normalize_prefix
- `docs/oroma_bewusstsein_map.md` | `oroma_bewusstsein_map.md` → `docs/oroma_bewusstsein_map.md` | basename_unique
- `docs/oroma_bewusstsein_map.md` | `oroma_bewusstsein_map.md` → `docs/oroma_bewusstsein_map.md` | basename_unique
- `docs/oroma_bewusstsein_map.md` | `/opt/ai/oroma/docs/oroma_bewusstsein_map.md` → `docs/oroma_bewusstsein_map.md` | normalize_prefix
- `docs/oroma_bewusstsein_map.md` | `/opt/ai/oroma/docs/oroma_bewusstsein_map.md` → `docs/oroma_bewusstsein_map.md` | normalize_prefix
- `docs/oroma_reifestufen.md` | `/opt/ai/oroma/docs/oroma_reifestufen.md` → `docs/oroma_reifestufen.md` | normalize_prefix
- `docs/oroma_reifestufe6_evolutionaere_intelligenz.md` | `/opt/ai/oroma/docs/oroma_reifestufe6_evolutionaere_intelligenz.md` → `docs/oroma_reifestufe6_evolutionaere_intelligenz.md` | normalize_prefix
- `docs/oroma_emergenz_und_agi_indikatoren.md` | `/opt/ai/oroma/docs/oroma_emergenz_und_agi_indikatoren.md` → `docs/oroma_emergenz_und_agi_indikatoren.md` | normalize_prefix
- `docs/oroma_audio_lernen_lehrmodell.md` | `/opt/ai/oroma/docs/oroma_audio_lernen_lehrmodell.md` → `docs/oroma_audio_lernen_lehrmodell.md` | normalize_prefix
- `docs/oroma_addon_tvstream.md` | `/opt/ai/oroma/docs/oroma_addon_tvstream.md` → `docs/oroma_addon_tvstream.md` | normalize_prefix
- `docs/oroma_addon_commwrapper.md` | `/opt/ai/oroma/docs/oroma_addon_commwrapper.md` → `docs/oroma_addon_commwrapper.md` | normalize_prefix
- `docs/ops_sqlite_locks_and_timers.md` | `/opt/ai/oroma/docs/ops_sqlite_locks_and_timers.md` → `docs/ops_sqlite_locks_and_timers.md` | normalize_prefix
- `docs/modelle_installation.md` | `/opt/ai/oroma/docs/modelle_installation.md` → `docs/modelle_installation.md` | normalize_prefix
- `docs/modelle_installation.md` | `/opt/ai/oroma/docs/modelle_installation.md` → `docs/modelle_installation.md` | normalize_prefix
- `docs/mensch_vs_oroma.md` | `/opt/ai/oroma/v3.7/docs/mensch_vs_oroma.md` → `docs/mensch_vs_oroma.md` | basename_unique
- `docs/mensch_vs_oroma.md` | `upgrade-guide-v2_11-to-3_8.md` → `docs/upgrade-guide-v2_11-to-3_8.md` | basename_unique
- `docs/logikcheck.md` | `/opt/ai/oroma/docs/logikcheck.md` → `docs/logikcheck.md` | normalize_prefix
- `docs/learning_ui.md` | `/opt/ai/oroma/docs/learning_ui.md` → `docs/learning_ui.md` | normalize_prefix
- `docs/konzeption_architektur_v3_9.md` | `/docs/konzeption_architektur_v3_9.md` → `docs/konzeption_architektur_v3_9.md` | normalize_prefix
- `docs/konzeption_architektur_v3_8.md` | `/opt/ai/oroma/v3.8/docs/konzeption_architektur_v3_8.md` → `docs/konzeption_architektur_v3_8.md` | basename_unique
- `docs/konzeption_architektur_v3_8.md` | `/opt/ai/oroma/v3.8/docs/konzeption_architektur_v3_8.md` → `docs/konzeption_architektur_v3_8.md` | basename_unique
- `docs/konzeption_architektur_v3_7.md` | `/opt/ai/oroma/docs/konzeption_architektur_v3_7.md` → `docs/konzeption_architektur_v3_7.md` | normalize_prefix
- `docs/konzeption_architektur_v3_7.md` | `/opt/ai/oroma/docs/konzeption_architektur_v3_7.md` → `docs/konzeption_architektur_v3_7.md` | normalize_prefix
- `docs/konzeption_architektur_v3_7.md` | `konzeption_architektur_v3_7.md` → `docs/konzeption_architektur_v3_7.md` | basename_unique
- `docs/konzeption_architektur_v3_7_3.md` | `/opt/ai/oroma/docs/konzeption_architektur_v3_7_3.md` → `docs/konzeption_architektur_v3_7_3.md` | normalize_prefix
- `docs/konzeption_architektur_v3_7_3.md` | `snaptoken.md` → `docs/module_snaptoken.md` | basename_unique
- `docs/konzeption_architektur_v3_7_2.md` | `/opt/ai/oroma/docs/konzeption_architektur_v3_7_2.md` → `docs/konzeption_architektur_v3_7_2.md` | normalize_prefix
- `docs/konzeption_architektur_v3_7_1.md` | `/opt/ai/oroma/docs/konzeption_architektur_v3_7_1.md` → `docs/konzeption_architektur_v3_7_1.md` | normalize_prefix
- `docs/konzeption_architektur_v3_7_1.md` | `/opt/ai/oroma/docs/konzeption_architektur_v3_7_1.md` → `docs/konzeption_architektur_v3_7_1.md` | normalize_prefix
- `docs/konzeption_architektur_v3_7_1.md` | `konzeption_architektur_v3_7_1.md` → `docs/konzeption_architektur_v3_7_1.md` | basename_unique
- `docs/konzeption_architektur_v3_6.md` | `/opt/ai/oroma/v3.6/docs/konzeption_architektur_v3_6.md` → `docs/konzeption_architektur_v3_6.md` | basename_unique
- `docs/konzeption_architektur_v3_6.md` | `konzeption_architektur_v3_6.md` → `docs/konzeption_architektur_v3_6.md` | basename_unique
- `docs/konzeption_architektur_v3_6.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/konzeption_architektur_v3_5_patch2.md` | `/docs/Konzeption_Architektur_v3.5_PatchLevel2.md` → `docs/konzeption_architektur_v3_5_patch2.md` | fuzzy_auto(0.95)
- `docs/konzeption_architektur_v3_5_patch2_1.md` | `Konzeption_Architektur_v3.5_PatchLevel2.1.md` → `docs/konzeption_architektur_v3_5_patch2_1.md` | fuzzy_auto(0.95)
- `docs/konzeption_architektur_kurz.md` | `/opt/ai/oroma/docs/konzeption_architektur_kurz.md` → `docs/konzeption_architektur_kurz.md` | normalize_prefix
- `docs/konzeption_architektur_kurz.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/konzeption_architektur_kurz.md` | `snap.md` → `docs/module_snap.md` | basename_unique
- `docs/konzeption_architektur_kurz.md` | `konzeption_architektur.md` → `docs/konzeption_architektur.md` | basename_unique
- `docs/konzeption_2_5d_3d_snapspace.md` | `/opt/ai/oroma/docs/konzeption_2_5d_3d_snapspace.md` → `docs/konzeption_2_5d_3d_snapspace.md` | normalize_prefix
- `docs/ki_readme.md` | `ki_readme.md` → `docs/ki_readme.md` | basename_unique
- `docs/ki_readme.md` | `roadmap_2026.md` → `docs/roadmap_2026.md` | basename_unique
- `docs/ki_readme.md` | `changelog_full.md` → `docs/changelog_full.md` | basename_unique
- `docs/integrationstest.md` | `/opt/ai/oroma/docs/integrationstest.md` → `docs/integrationstest.md` | normalize_prefix
- `docs/install_quickstart.md` | `install_quickstart.md` → `docs/install_quickstart.md` | basename_unique
- `docs/install.md` | `install.md` → `docs/install.md` | basename_unique
- `docs/gesamtanalyse_projektstand_2025-12-03.md` | `gesamtanalyse_3_7.md` → `docs/gesamtanalyse_3_7.md` | basename_unique
- `docs/episoden_sql_cheatsheet.md` | `/opt/ai/oroma/docs/episoden_sql_cheatsheet.md` → `docs/episoden_sql_cheatsheet.md` | normalize_prefix
- `docs/datenbankverwaltung.md` | `/opt/ai/oroma/docs/datenbankverwaltung.md` → `docs/datenbankverwaltung.md` | normalize_prefix
- `docs/dream_cycle.md` | `/opt/ai/oroma/docs/dream_cycle.md` → `docs/dream_cycle.md` | normalize_prefix
- `docs/doc_link_audit.md` | `docs/DREAM_WORKER.md` → `docs/dream_worker.md` | fuzzy_auto(1.01)
- `docs/doc_link_audit.md` | `SceneGraph_Builder.md` → `docs/scenegraph_builder.md` | fuzzy_auto(1.01)
- `docs/doc_link_audit.md` | `docs/DREAM_WORKER.md` → `docs/dream_worker.md` | fuzzy_auto(1.01)
- `docs/doc_link_audit.md` | `docs/Entwicklungsleiter.md` → `docs/history_entwicklungsleiter.md` | basename_unique
- `docs/doc_link_audit.md` | `referenz_handbuch.md` → `docs/referenz_handbuch.md` | basename_unique
- `docs/doc_link_audit.md` | `administrator-handbuch.md` → `docs/administrator-handbuch.md` | basename_unique
- `docs/doc_link_audit.md` | `projektstruktur.md` → `docs/projektstruktur.md` | fuzzy_auto(1.01)
- `docs/doc_link_audit.md` | `README.md` → `docs/README.md` | basename_unique
- `docs/doc_link_audit.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/doc_link_audit.md` | `referenz_handbuch.md` → `docs/referenz_handbuch.md` | basename_unique
- `docs/doc_link_audit.md` | `administrator-handbuch.md` → `docs/administrator-handbuch.md` | basename_unique
- `docs/doc_link_audit.md` | `konzeption_architektur.md` → `docs/konzeption_architektur.md` | basename_unique
- `docs/doc_link_audit.md` | `projektstruktur.md` → `docs/projektstruktur.md` | basename_unique
- `docs/doc_link_audit.md` | `integrationstest.md` → `docs/integrationstest.md` | basename_unique
- `docs/doc_link_audit.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/doc_link_audit.md` | `readme_addons.md` → `docs/readme_addons.md` | basename_unique
- `docs/doc_link_audit.md` | `projektstruktur.md` → `docs/projektstruktur.md` | basename_unique
- `docs/doc_link_audit.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/doc_link_audit.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/doc_link_audit.md` | `TODO_Snaps.md` → `docs/todo_snaps_v3_8.md` | fuzzy_auto(0.85)
- `docs/doc_link_audit.md` | `docs/TODO_Snaps.md` → `docs/todo_snaps_v3_8.md` | fuzzy_auto(0.85)
- `docs/doc_link_audit.md` | `docs/Konzeption_Architektur_v3.5_PatchLevel2.md` → `docs/konzeption_architektur_v3_5_patch2.md` | fuzzy_auto(0.95)
- `docs/doc_link_audit.md` | `docs/Datenbankverwaltung_v3.5.md` → `docs/datenbankverwaltung.md` | fuzzy_auto(0.90)
- `docs/doc_link_audit.md` | `docs/ROADMAP_v3.5patch2.1.md` → `docs/roadmap_v3_5_patch_2_1.md` | fuzzy_auto(0.97)
- `docs/doc_link_audit.md` | `v3.5_patch_2.0.md` → `docs/roadmap_v3_5_patch_2_0.md` | fuzzy_auto(0.81)
- `docs/doc_link_audit.md` | `docs/ROADMAP_v3.5patch2.md` → `docs/roadmap_v3_5_patch_2_1.md` | fuzzy_auto(0.92)
- `docs/doc_link_audit.md` | `MA_v3.5_Master-Zusammenfassung.md` → `docs/history/ORO╠üMA_v3.5_Master-Zusammenfassung.md` | fuzzy_auto(0.93)
- `docs/doc_link_audit.md` | `5_Master-Zusammenfassung.md` → `docs/history/ORO╠üMA_v3.5_Master-Zusammenfassung.md` | fuzzy_auto(0.83)
- `docs/doc_link_audit.md` | `docs/OROMA_v3.5_Master-Zusammenfassung.md` → `docs/history_oroma_v3_0_master-zusammenfassung.md` | fuzzy_auto(0.98)
- `docs/doc_link_audit.md` | `MA_v3.5_Master-Zusammenfassung.md` → `docs/history/ORO╠üMA_v3.5_Master-Zusammenfassung.md` | fuzzy_auto(0.93)
- `docs/doc_link_audit.md` | `5_Master-Zusammenfassung.md` → `docs/history/ORO╠üMA_v3.5_Master-Zusammenfassung.md` | fuzzy_auto(0.83)
- `docs/doc_link_audit.md` | `docs/OROMA_v3.5_Master-Zusammenfassung.md` → `docs/history_oroma_v3_0_master-zusammenfassung.md` | fuzzy_auto(0.98)
- `docs/curriculum_math_tasks.md` | `fusion.md` → `docs/module_fusion.md` | basename_unique
- `docs/curriculum_math_tasks.md` | `roadmap.md` → `docs/roadmap.md` | basename_unique
- `docs/curriculum_math_calculator.md` | `exports.md` → `docs/module_exports.md` | basename_unique
- `docs/curriculum_calculator_v2.md` | `exports.md` → `docs/module_exports.md` | basename_unique
- `docs/core_roterfaden.md` | `/opt/ai/oroma/docs/core_roterfaden.md` → `docs/core_roterfaden.md` | normalize_prefix
- `docs/current.md` | `/opt/ai/oroma/docs/current.md` → `docs/current.md` | normalize_prefix
- `docs/changelog_v3_5patch1.md` | `changelog_v3_5patch1.md` → `docs/changelog_v3_5patch1.md` | basename_unique
- `docs/changelog_v3_5patch1.md` | `changelog_v3_5patch2.md` → `docs/changelog_v3_5patch2.md` | basename_unique
- `docs/changelog_v3_5patch1.md` | `/docs/changelog_v3_5patch1.md` → `docs/changelog_v3_5patch1.md` | normalize_prefix
- `docs/changelog_v3_5patch1.md` | `/docs/changelog_v3_5patch2.md` → `docs/changelog_v3_5patch2.md` | normalize_prefix
- `docs/changelog_full.md` | `changelog_full.md` → `docs/changelog_full.md` | basename_unique
- `docs/changelog_full.md` | `doc_link_audit.md` → `docs/doc_link_audit.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `snap.md` → `docs/module_snap.md` | basename_unique
- `docs/changelog_full.md` | `quick_check_3_6.md` → `docs/quick_check_3_6.md` | basename_unique
- `docs/changelog_full.md` | `curriculum_math_tasks.md` → `docs/curriculum_math_tasks.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `quick_check_3_6.md` → `docs/quick_check_3_6.md` | basename_unique
- `docs/changelog_full.md` | `curriculum_math_tasks.md` → `docs/curriculum_math_tasks.md` | basename_unique
- `docs/changelog_full.md` | `Besonderheit_SnapPattern.md` → `docs/module_besonderheit_snappattern.md` | basename_unique
- `docs/changelog_full.md` | `rag_bridge.md` → `docs/module_rag_bridge.md` | basename_unique
- `docs/changelog_full.md` | `bersicht_blueprints.md` → `docs/ubersicht_blueprints.md` | fuzzy_auto(0.94)
- `docs/changelog_full.md` | `bersicht_blueprints.md` → `docs/ubersicht_blueprints.md` | fuzzy_auto(0.94)
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `fusion.md` → `docs/module_fusion.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `soloprojekt.md` → `docs/soloprojekt.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/changelog_full.md` | `fazit_3_8.md` → `docs/fazit_3_8.md` | basename_unique
- `docs/changelog.md` | `/opt/ai/oroma/docs/changelog.md` → `docs/changelog.md` | normalize_prefix
- `docs/audit_v3_7_1_deltareview.md` | `/opt/ai/oroma/docs/audit_v3_7_1_deltareview.md` → `docs/audit_v3_7_1_deltareview.md` | normalize_prefix
- `docs/abhaengigkeiten.md` | `/opt/ai/oroma/docs/abhaengigkeiten.md` → `docs/abhaengigkeiten.md` | normalize_prefix
- `docs/abhaengigkeiten.md` | `vector_migration.md` → `docs/module_vector_migration.md` | basename_unique
- `docs/ai_assisted_development.md` | `ki_readme.md` → `docs/ki_readme.md` | basename_unique
- `docs/ai_assisted_development.md` | `projektbeschreibung.md` → `docs/projektbeschreibung.md` | basename_unique
- `docs/ai_assisted_development.md` | `roadmap_2026.md` → `docs/roadmap_2026.md` | basename_unique
- `docs/ai_assisted_development.md` | `changelog_full.md` → `docs/changelog_full.md` | basename_unique
- `docs/ai_assisted_development.md` | `konzept_2d_3d_space.md` → `docs/konzept_2d_3d_space.md` | basename_unique
- `docs/ai_assisted_development.md` | `core_roterfaden.md` → `docs/core_roterfaden.md` | basename_unique
- `docs/ai_assisted_development.md` | `dream_cycle.md` → `docs/dream_cycle.md` | basename_unique
- `docs/module_ui.md` | `cores_konzept.md` → `docs/cores_konzept.md` | basename_unique
- `docs/module_ui.md` | `exports.md` → `docs/module_exports.md` | basename_unique
- `docs/module_ui.md` | `dienste.md` → `systemd/dienste.md` | basename_unique
- `docs/module_rag_bridge.md` | `rag_bridge.md` → `docs/module_rag_bridge.md` | basename_unique
- `docs/module_export_manager.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/module_book_import.md` | `book_import.md` → `docs/module_book_import.md` | basename_unique
- `docs/module_besonderheit_snappattern.md` | `/opt/ai/oroma/v2.20/docs/Besonderheit_SnapPattern.md` → `docs/module_besonderheit_snappattern.md` | basename_unique
- `docs/module_besonderheit_overlay.md` | `Besonderheit_Overlay.md` → `docs/module_besonderheit_overlay.md` | basename_unique
- `docs/module_besonderheit_miniprogramme.md` | `snap.md` → `docs/module_snap.md` | basename_unique
- `docs/module_besonderheit_miniprogramme.md` | `snap.md` → `docs/module_snap.md` | basename_unique
- `docs/module_besonderheit_miniprogramme.md` | `snap.md` → `docs/module_snap.md` | basename_unique
- `docs/module_besonderheit_miniprogramme.md` | `docs/Besonderheit_MiniProgramme.md` → `docs/module_besonderheit_miniprogramme.md` | basename_unique
- `docs/module_besonderheit_miniprogramme.md` | `docs/Besonderheit_MiniProgramme.md` → `docs/module_besonderheit_miniprogramme.md` | basename_unique
- `docs/module_besonderheit_miniprogramme.md` | `/mnt/data/Besonderheit_MiniProgramme.md` → `docs/module_besonderheit_miniprogramme.md` | basename_unique
- `docs/history_readme_final_v3_00.md` | `docs/OROMA_v2.30_Doku.md` → `docs/history_oroma_v2_30_doku.md` | basename_unique
- `docs/history_readme_final_v2_30.md` | `ui.md` → `docs/module_ui.md` | basename_unique
- `docs/history_fahrplan_3_5.md` | `ROADMAP_3.5.md` → `docs/roadmap_v3_6.md` | fuzzy_auto(0.90)
- `docs/history_fahrplan_3_5.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/history_fahrplan_3_5.md` | `ROADMAP_3.5.md` → `docs/roadmap_v3_6.md` | fuzzy_auto(0.90)
- `docs/history_fahrplan_3_5.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/history_upgrade_plan.md` | `docs/OROMA_v2.30_Doku.md` → `docs/history_oroma_v2_30_doku.md` | basename_unique
- `docs/history_projektstruktur_patch_roadmap.md` | `README.md` → `docs/README.md` | basename_unique
- `docs/history_projektstruktur_patch_roadmap.md` | `changelog.md` → `docs/changelog.md` | basename_unique
- `docs/history_projektstruktur_patch_roadmap.md` | `/opt/ai/oroma/v2.30/docs/projektstruktur.md` → `docs/projektstruktur.md` | basename_unique
- `docs/history/ORO╠üMA_v3.5_Master-Zusammenfassung.md` | `/opt/ai/oroma/v3.5/docs/OROMA_v3.5_Master-Zusammenfassung.md` → `docs/history_oroma_v3_0_master-zusammenfassung.md` | fuzzy_auto(0.98)
- `docs/history/OROªⁿMA_v3.5_Master-Zusammenfassung.md` | `/opt/ai/oroma/v3.5/docs/OROMA_v3.5_Master-Zusammenfassung.md` → `docs/history_oroma_v3_0_master-zusammenfassung.md` | fuzzy_auto(0.98)
- `docs/history_oroma_v3_0_master-zusammenfassung.md` | `/opt/ai/oroma/v3.0/docs/OROMA_v3.0_Master-Zusammenfassung.md` → `docs/history_oroma_v3_0_master-zusammenfassung.md` | basename_unique
- `docs/history_oroma_v2_30_doku.md` | `referenz_handbuch.md` → `docs/referenz_handbuch.md` | basename_unique
- `docs/history_oroma_v2_30_doku.md` | `administrator-handbuch.md` → `docs/administrator-handbuch.md` | basename_unique
- `docs/history_oroma_v2_30_doku.md` | `projektstruktur.md` → `docs/projektstruktur.md` | fuzzy_auto(1.01)
- `docs/history_entwicklungsleiter.md` | `/opt/ai/oroma/docs/Entwicklungsleiter.md` → `docs/history_entwicklungsleiter.md` | basename_unique
- `docs/history_changelog_final_v3_0.md` | `CHANGELOG_FINAL_V3.0.md` → `docs/history_changelog_final_v3_0.md` | basename_unique
- `docs/history_architektur_final_v3_5.md` | `ARCHITEKTUR_FINAL_V3.5.md` → `docs/history_architektur_final_v3_5.md` | basename_unique
- `cron/README-cron.md` | `/opt/ai/oroma/v2.30/cron/README-cron.md` → `cron/README-cron.md` | basename_unique
- `core/snaps_todo.md` | `/opt/ai/oroma/v3.7/docs/TODO_Snaps.md` → `docs/todo_snaps_v3_8.md` | fuzzy_auto(0.85)
- `core/snaps_todo.md` | `/opt/ai/oroma/v3.7/docs/TODO_Snaps.md` → `docs/todo_snaps_v3_8.md` | fuzzy_auto(0.85)

## Unaufgelöste Referenzen (gekürzt)
Diese Tokens konnten nicht eindeutig auf ein vorhandenes `.md` gemappt werden.

- `docs/fazit_3_8.md` | `docs/DB_Analyse_OROMA_v3.8.md`
- `docs/analysevonzweikis.md` | `docs/Meta_Review_Discussion.md`
- `docs/analysevonzweikis.md` | `docs/Meta_Review_Discussion.md`
- `docs/analysevonzweikis.md` | `docs/Meta_Review_Discussion.md`
- `docs/analysevonzweikis.md` | `docs/NMR_Design_Decisions.md`
- `docs/analysevonzweikis.md` | `docs/Limitations.md`
- `docs/technische_aenderungen.md` | `NDERUNGEN.md`
- `docs/technische_aenderungen.md` | `NDERUNGEN.md`
- `docs/readme_models.md` | `/opt/ai/oroma/docs/Modellverwaltung.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Paper_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Mathematik_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Conference_Slides_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Cognitive_Space_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Cognitive_Comparison_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Research_Bundle_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Paper_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Mathematik_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Conference_Slides_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Cognitive_Space_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Cognitive_Comparison_v3.8.md`
- `docs/oroma_wissenschaftliche_tiefenanalyse.md` | `OROMA_Research_Bundle_v3.8.md`
- `docs/konzeption_architektur_v3_7_2.md` | `/opt/ai/oroma/docs/benchmarks/ttt_v3.7.2.md`
- `docs/konzeption_architektur_v3_7_2.md` | `/opt/ai/oroma/docs/benchmarks/ttt_v3.7.2.md`
- `docs/konzeption_architektur_v3_5_patch2_1.md` | `PatchLevel2.md`
- `docs/gesamtanalyse_3_7.md` | `/opt/ai/oroma/docs/OROMA_Analyse_v3_7_RoterFaden_Update.md`
- `docs/doc_link_audit.md` | `TODO.md`
- `docs/doc_link_audit.md` | `Shared_KI_Memory.md`
- `docs/doc_link_audit.md` | `api_spec.md`
- `docs/doc_link_audit.md` | `docs/Shared_KI_Memory.md`
- `docs/doc_link_audit.md` | `docs/machine/api_spec.md`
- `docs/doc_link_audit.md` | `DB_Auslesen_OROMA.md`
- `docs/doc_link_audit.md` | `docs/DB_Auslesen_OROMA.md`
- `docs/doc_link_audit.md` | `1_DeltaReview.md`
- `docs/doc_link_audit.md` | `5patch1.md`
- `docs/doc_link_audit.md` | `5patch2.md`
- `docs/doc_link_audit.md` | `7.md`
- `docs/doc_link_audit.md` | `1.md`
- `docs/doc_link_audit.md` | `PatchLevel2.md`
- `docs/doc_link_audit.md` | `5_PatchLevel2.md`
- `docs/doc_link_audit.md` | `6.md`
- `docs/doc_link_audit.md` | `6_Patch2_Mengenlehre.md`
- `docs/doc_link_audit.md` | `1.md`
- `docs/doc_link_audit.md` | `2.md`
- `docs/doc_link_audit.md` | `docs/benchmarks/ttt_v3.7.2.md`
- `docs/doc_link_audit.md` | `3.md`
- `docs/doc_link_audit.md` | `7.md`
- `docs/doc_link_audit.md` | `8.md`
- `docs/doc_link_audit.md` | `9.md`
- `docs/doc_link_audit.md` | `Tiefenanalyse.md`
- `docs/doc_link_audit.md` | `8.md`
- `docs/doc_link_audit.md` | `5.md`
- `docs/doc_link_audit.md` | `1.md`
- `docs/doc_link_audit.md` | `5patch2.md`
- `docs/doc_link_audit.md` | `NDERUNGEN.md`
- `docs/doc_link_audit.md` | `8.md`
- `docs/doc_link_audit.md` | `8.md`
- `docs/doc_link_audit.md` | `8.md`
- `docs/doc_link_audit.md` | `Meta_Review_Discussion.md`
- `docs/doc_link_audit.md` | `docs/Meta_Review_Discussion.md`
- `docs/doc_link_audit.md` | `8.md`
- `docs/doc_link_audit.md` | `docs/DB_Analyse_OROMA_v3.8.md`
- `docs/doc_link_audit.md` | `5.md`
- `docs/doc_link_audit.md` | `0.md`
- `docs/doc_link_audit.md` | `5.md`
- `docs/doc_link_audit.md` | `0.md`
- `docs/ankeitung_auslesen_sql_komsole.md` | `docs/DB_Auslesen_OROMA.md`
- `docs/ai_assisted_development.md` | `docs/machine/api_spec.md`
- `docs/ai_assisted_development.md` | `docs/Shared_KI_Memory.md`
- `core/snaps_todo.md` | `TODO.md`

<a id="docs_docs_konsolidierung_md"></a>

## Quelle: `docs/docs_konsolidierung.md`

**Originaltitel:** ORÓMA Docs – Konsolidierung

Stand: 2025-12-25

Dieses Verzeichnis wurde bereinigt, um Linux-/Git-freundliche Dateinamen zu haben und Wildwuchs zu reduzieren:

- **Keine Unterordner mehr**: Inhalte aus `docs/modules/` und `docs/history/` wurden in `docs/` integriert (mit Prefix `module_` bzw. `history_`).
- **ASCII-Dateinamen**: Umlaute/Sonderzeichen entfernt (z.B. `Ü`→`ue`, `ß`→`ss`).
- **Deduplizierung**: Text-identische Duplikate (meist Encoding-Varianten) wurden entfernt.
- **Projektstart korrigiert**: In `changelog.md` und `changelog_full.md` ist der Start nun **Juli 2025** (nicht 2023).

## Wichtige Dateien

- `index.md` – Einstiegspunkt / Inhaltsverzeichnis
- `docs_rename_map.json` – Mapping alter Pfade → neuer Dateiname (für Rückverfolgung / Link-Fixes)

## Entfernte Datei

- `README.md-1766574791` wurde entfernt (älterer Snapshot, Inhalt in `README.md` enthalten).

<a id="docs_module_vector_migration_md"></a>

## Quelle: `docs/module_vector_migration.md`

**Originaltitel:** ORÓMA Core – vector_migration

## Zweck
- Aktiviert einen Vektor-Index (FAISS oder Annoy), sobald die Anzahl gespeicherter SnapChains einen definierten Schwellwert überschreitet.
- Ermöglicht schnelle Ähnlichkeitssuche (Nearest-Neighbor) für SnapFeatures und SnapPatterns.
- Hält SQL-Tabellen (`snapchains`, `patterns`) synchron mit Vektor-Index.

---

## Hauptfunktionen

### Schwellenwert
- Standard: **100.000 SnapChains**
- Konfigurierbar über Umgebungsvariable `OROMA_VECTORDb_THRESHOLD`

### Ablauf
1. Periodischer Check (`vector_index_sync()` aus `sql_manager`).
2. Falls Anzahl SnapChains > Threshold:
   - Initialisiere Vektor-Index (FAISS/Annoy).
   - Migriere vorhandene SnapFeatures in Index.
3. Neue Einträge werden automatisch in den Index geschrieben.

---

## Methoden

- **`check_threshold(count: int) -> bool`**  
  Prüft, ob die aktuelle Anzahl SnapChains den Schwellenwert überschreitet.

- **`migrate_existing(conn) -> dict`**  
  Lädt alle SnapChains, extrahiert Features, fügt sie in den Index ein.  
  Rückgabe: Statistik (`count_added`, `dim`, `backend`).

- **`add_to_index(item_id: int, features: List[float])`**  
  Fügt neuen SnapChain-Featurevektor in den Index.

- **`query_index(features: List[float], topk=5) -> List[Tuple[int, float]]`**  
  Sucht ähnliche SnapChains, Rückgabe: Liste von `(id, score)`.

---

## Besonderheiten
- Backend-agnostisch: nutzt FAISS, Annoy oder einfache NumPy-Suche (Fallback).
- Index-Dateien werden in `database/vector/` persistiert.
- Unterstützt verschiedene Distanzmetriken (Cosine, L2).
- Voll kompatibel mit `langzeitgedaechtnis.py`.

---


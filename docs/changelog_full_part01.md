<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__changelog_full.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

# ORÓMA – Changelog (konsolidiert)

> Hinweis: Projektstart war **Juli 2025** (nicht 2023).


Stand: 2025-12-25


Dieses Dokument enthält das konsolidierte Änderungsprotokoll.
**Wichtig:** Projektstart ist **Juli 2025** (nicht 2023).

> **Hinweis zur Datierung:** In älteren Entwürfen tauchten vereinzelt Jahres-/Monatsangaben auf,
> die dem Projektstart (**Juli 2025**) widersprechen (z. B. 2024 oder 2025-06).
> Diese Stellen wurden im Text **als Sommer 2025** bzw. auf **2025** korrigiert, der technische Inhalt blieb unverändert.


## Quellen (konsolidiert)

- `docs/changelog_full.md`

- `docs/changelog.md`

- `docs/changelog_v3_5patch1.md`

- `docs/changelog_v3_5patch2.md`

- `docs/history_changelog_final_v2_30.md`

- `docs/history_changelog_final_v3_0.md`

- `docs/history_oroma_changelog.md`

---

<a id="docs_changelog_full_md"></a>

## Quelle: `docs/changelog_full.md`

**Originaltitel:** oder zyklisch via oroma-dream.timer

📘 docs/changelog_full.md

## 2025-12-25 – Ops: Orchestrator Mode B (Stale-Locks + Work-Gating) + Cutover Script
- `tools/oroma_orchestrator.py`: Stale-Lock Guard für `data/state/dream.lock` (OROMA_ORCH_STALE_LOCK_SEC, Default 600s).
- `tools/oroma_orchestrator.py`: Work-Gating – Skip Snake/TicTacToe Policy-Jobs wenn keine aktiven SnapChains vorhanden sind.
- `mini_programs/__init__.py`: Quiet-Mode via `OROMA_MINIPROGRAMS_QUIET=1` (Registrierungs-Logs auf DEBUG, Import-Warnungen reduziert).
- Neu: `tools/oroma_orchestrator_cutover_modeB.sh` (stop/disable Legacy Timer/Services; fix drop-in Exec-Bits; daemon-reload).
- `docs/ops_sqlite_locks_and_timers.md`: Cutover-Abschnitt ergänzt.

## 2025-12-24 – Docs: Vollständiger MD-Referenz-Audit + Auto-Fix
- Automatisches Normalisieren von historischen/absoluten Pfaden (z.B. `/opt/ai/oroma/...`) auf Repo-relative Pfade.
- Auto-Mapping von `.md`-Referenzen auf existierende Dokumente via Basename/Fuzzy-Match (ohne Code-Fences anzutasten).
- Neuer Report: `docs/doc_ref_audit.md` (Summary + Änderungslog + Restliste ungefixter Tokens).

ORÓMA – Vollständiges Änderungsprotokoll aller Versionen
Stand: 24.12.2025
Quelle: ZIP → oroma_20251224_081440_with_db.zip + Docs: docs/doc_link_audit.md (Auto-Fix Runde 3, eindeutig)

## Patch-Stand 24.12.2025 – Doku-Konsistenz (Doc→Doc Links)
- Neu: `docs/doc_link_audit.md` (Scan + Report + sichere Auto-Fixes)
- Auto-Fix: Eindeutige Doc→Doc Referenzen wurden auf existierende Ziele umgebogen (ohne Raten).
- Nicht-eindeutige Referenzen bleiben bewusst unverändert (siehe Audit-Liste).

DreamWorker 3.3 / SceneGraph 2.5D / ObjectGraph 1.5 / Object-DB + /objects-UI v0.8 (Ego-Net + Health-Badge)

⸻

Executive Summary (Ende 2025, Kurzfassung)
	•	ORÓMA ist von einem „Bastel-Skript“ zu einem kohärenten KI-System gewachsen:
	•	AgentLoop + Day/Night-Cycle + DreamWorker 3.x
	•	Snap/SnapChain/MetaSnaps + Langzeitgedächtnis + Replay
	•	SceneGraph-Store (2.5D) + ObjectGraph 1.5 inkl. eigener Object-DB
	•	RAG-Bridge + Ask-UI, Coverage/Empathy/Selftest, Games, Video, Episoden, Sensoren.
	•	DreamWorker 3.3 ist der zentrale Kopf der Nachtphase:
	•	Replay, Vergessen, Auto-Tuning, Research, Missions, Curriculum
	•	Vision→SceneGraph→ObjectGraph voll integriert
	•	läuft bei dir als oneshot via oroma-dream.service (systemd) und/oder CLI.
	•	ObjectGraph 1.5:
	•	Aggregat-Ebene in scenegraphs (namespace object:auto:vision)
	•	persistente Objektwelt in object_nodes / object_relations
	•	eigene Health-Tools (objectgraph_*) + /objects-UI v0.8 mit Ego-Net und Health-Badge.
	•	DB-Schema & Tools sind an deinem realen Schema ausgerichtet:
	•	scenegraphs(id, ts, namespace, source, quality, graph_json, notes)
	•	object_nodes(id, kind, label, meta_json, created_ts, …)
	•	object_relations(id, a_id, relation, b_id, confidence, source_scene_id, ts, notes).
	•	Wir haben im Dezember 2025 explizit verifiziert:
	•	DreamWorker-Builds von SceneGraph + ObjectGraph
	•	Object-DB gefüllt und dedupliziert
	•	Audit-Tools liefern konsistente 1:1-Beziehungen
	•	/objects-UI zeigt konsistente Stats, Top-Hubs und Ego-Netze der Knoten.

⸻

Inhaltsverzeichnis
	1.	Version 1.6 – Proof of Concept (Juli 2025)
	2.	Version 1.62 – UI-Skelett
	3.	Version 1.98 – Konsolidierung
	4.	Version 2.00 – Struktur + Deployment
	5.	Version 2.11 – Final Release („Kind“)
	6.	Version 2.20 – Diagnostics & Auto-Tuner
	7.	Version 2.30 – Agentisches Lernen
	8.	Version 3.0 – LLM-Fusion & Replay 3.0
	9.	Version 3.5 – „Forscher/Meister“
	10.	Patch 1 (v3.5patch1) – Transfer & Calculator
	11.	Patch 2 (v3.5patch2) – Empathy/Coverage/Selftest
	12.	Version 3.7 – „Roter Faden + Mutations-Drift“
	13.	Version 3.7.x – DreamWorker 3.1 & SceneGraph (vision/token)
	14.	Version 3.7.3 – DreamWorker 3.3 & ObjectGraph 1.5 (Scene→Object + Object-DB + Audit/Dedupe + /objects-UI v0.8)
	15.	Version 3.8 – Regelarchiv & Pruning
	16.	Version 3.8-r1/r2 – Snap v1.1, SnapIndex & RAG-Benchmark
	17.	Version 3.8-r3 – DeviceHub-Sensoren & Vision-Episoden
	18.	Upgrade-Hinweise (3.0→3.5→…→3.8→3.7.3-ObjectGraph)
	19.	Bekannte Grenzen

⸻

🟦 v1.6 – Proof of Concept (07/2025)

Neu
	•	Einführung der Core-Mechanik:
	•	core/snap.py, core/snaptoken.py, core/snappattern.py
	•	numerische + symbolische Repräsentation
	•	Erste Mini-Games (CLI):
	•	TicTacToe, Connect4
	•	SQLite-Persistenz (rudimentär, noch ohne Langzeit-Gedächtnis-Architektur)
	•	Basic Replay (ohne explizite Dream-Phase)

Bedeutung
	•	Fundament für späteres episodisches und sensorisches Gedächtnis.
	•	Zeigt: „Snap“-Prinzip funktioniert, Sequenzen sind lernbar.

⸻

🟦 v1.62 – UI-Skelett (08/2025)

Neu
	•	Erstes Flask-UI:
	•	Routen: /, /games
	•	Wrapper:
	•	Vision (OpenCV) – einfache Frame-Verarbeitung
	•	Audio (Vosk) – Basic-ASR
	•	Erste Export-Versuche (tar-basiert)

Bedeutung
	•	Übergang von reiner CLI zu interaktiver Weboberfläche.
	•	Legt Grundstruktur der heutigen UI-Blueprints.

⸻

🟦 v1.98 – Konsolidierung (08/2025)

Neu
	•	Neue Mini-Games: Snake + Pong
	•	Replay-System verbessert:
	•	Pause/Resume
	•	einfachere Navigation
	•	Export/Import stabilisiert:
	•	robustere Fehlerbehandlung
	•	kompaktes Paketformat
	•	Circadian Controller v1.0:
	•	einfache Tag/Nacht-Umschaltung

Fixes
	•	Camera-Fallbacks
	•	diverse DB-Fixes

⸻

🟦 v2.00 – Struktur/Deployment (08/2025)

Neu
	•	Einheitlicher Projektbaum unter /opt/ai/oroma/
	•	run_oroma.py als zentraler Entrypoint
	•	systemd-Units:
	•	oroma.service
	•	erste Health/Replay/Dream-Units
	•	picar_safety.py (Safety-Mechanik für PiCar)

Bedeutung
	•	Einstieg in produktive Deployments (Dienste, Autostart).
	•	Grundstein für deine heutige Headless-Installation.

⸻

🟦 v2.11 – Final Release („Kind“) (2025-09-09)

Features
	•	Mini-Games: TicTacToe, Connect4, Snake, Pong, Flappy, Maze, CTF …
	•	Circadian Controller:
	•	Day/Dream-Phasen
	•	SnapChain-Persistenz komplett:
	•	Replayfähig
	•	Exportierbar
	•	Export/Import:
	•	robust + deduplizierend

Bedeutung
	•	Erste „runde“ Version:
	•	kann lernen, schlafen, erinnern
	•	in sich stimmige Architektur.

⸻

🟦 v2.20 – Diagnostics & Auto-Tuner

Neu
	•	Gap-Diagnostics:
	•	Novelty, Coverage, Confidence
	•	Auto-Tuner (ε-Regler):
	•	passt Lernparameter sanft an
	•	Gap-Badge im UI:
	•	Sichtbarkeit von „Wissenslücken“

⸻

🟦 v2.30 – Agentisches Lernen

Neu
	•	Reward-System + Curiosity
	•	Predictor
	•	Episodisches Gedächtnis v2.0
	•	Explainability 1.0:
	•	why_decision, erste „Warum?“-Pfad-Ansichten
	•	Synapses-UI:
	•	Visualisierung von Verbindungen / Snaps

Bedeutung
	•	Erster Schritt zu verstehbaren Entscheidungen:
	•	nicht nur Performance, sondern auch Erklärung.

⸻

🟦 v3.0 – LLM-Fusion + Replay 3.0 (2025-09-19)

Neu
	•	Snap+Token-Fusion:
	•	core/fusion.py
	•	RAG-Bridge:
	•	core/rag_bridge.py
	•	Bücher/Wissen → Vektorraumsuche → Antwort
	•	Book-Import (Knowledge-Pipeline)
	•	DreamWorker v2.0 (erste reguläre Nachtphase)
	•	ExportGate:
	•	heuristische Auswahl „exportwürdiger“ Chains
	•	PiCar-Safety:
	•	sicheres Fahren, Not-Stopp

UI
	•	Replay, Dream, Knowledge, Ask, Models, ASR2, Video
	•	Erste Version des heutigen Dashboards.

⸻

🟧 v3.5 – „Forscher/Meister“ (2025-09-21)

Großes Architektur-Release.

Neu
	•	AgentLoop mit Hook-System:
	•	modulare Lern-Hooks
	•	Langzeitgedächtnis 2.0
	•	Mutation + Variation:
	•	sanfte Veränderung von SnapChains
	•	DreamWorker 3.0 (komplexer)
	•	MetaSnaps:
	•	Zentroid-Ketten zur Verdichtung
	•	Model Registry:
	•	modelle.json/SQL-basierter Katalog
	•	Quality-History:
	•	Zeitverlauf pro Modell/Policy
	•	Replay 3.0:
	•	token-frei
	•	UI vollständig modularisiert:
	•	klar getrennte Blueprints

Fixes
	•	Meta-Zentroid-NaNs entfernt
	•	Episoden-Recall stabilisiert
	•	SQL-Schema konsolidiert

Wirkung
	•	System verhält sich wie ein lernender Student:
	•	Forschung (neue Chains) vs. Meisterschaft (Verfeinerung).

⸻

🟨 v3.5 Patch 1 – Transfer + Self-Assessment + Calculator

Dateien aus ZIP
	•	core/hooks_patch1.py
	•	core/calculator_engine.py
	•	core/transfer_engine.py
	•	ui/calculator_ui.py

Neu
	•	TransferSnaps:
	•	Lerneinheiten über mehrere Chains
	•	Self-Assessment Hook:
	•	automatische Fehlerbewertung / Korrektur
	•	Calculator-Training (add/sub/mult)
	•	drill-basierte Stärkung der Qualität

DB
	•	Tabellen:
	•	transfer_snaps
	•	calculator_tasks
	•	calculator_results

Wirkung
	•	schnelleres Lernen, stabilere Quality-Kurven, weniger Gaps.

⸻

🟩 v3.5 Patch 2 – Empathy + Coverage + Selftest (2025-09-24)

Dateien aus ZIP
	•	core/hooks_patch2.py
	•	ui/empathy_ui.py
	•	ui/coverage_ui.py
	•	ui/selftest_ui.py
	•	templates/empathy.html
	•	templates/coverage.html
	•	templates/selftest.html
	•	core/sql_manager.py (Erweiterungen)

Neu
	•	Empathy Hook:
	•	Mood/Confidence fließt in Replay/Mutation ein.
	•	Coverage Hook:
	•	misst aktive vs. inaktive SnapChains.
	•	Selftest-UI:
	•	Valence/Arousal/Confidence UI
	•	Coverage-Charts
	•	Navigationseinträge

DB
	•	Tabellen:
	•	empathy_snaps
	•	coverage_log (coverage, active, total)

Wirkung
	•	stabilisiert das Lernszenario,
	•	verhindert Overtraining,
	•	menschlicheres Lernprofil.

⸻

🟦 v3.7 – Roter Faden + Mutations-Drift (2025-10-05)

(In der ZIP nicht immer als explizite v3.7-Version gelabelt, aber Mechanik im Code vorhanden.)

Neu (per Codeanalyse bestätigt)
	•	Intent-Layer („Roter Faden“):
	•	steuert, welche Tasks / Ziele verfolgt werden.
	•	Drift-Mechanik:
	•	adaptive Mutationsrate
	•	Self-Healer-Loop:
	•	korrigiert degenerierte Policies
	•	Nudge-Engine:
	•	verhindert „Leerlauflernen“
	•	Stabilisierung über Confidence-Trends
	•	SnapToken v3.7 (core/snaptoken.py):
	•	stabile, modellagnostische Tokenisierung
	•	LLM-Tokenizer bevorzugt (falls vorhanden)
	•	Fallback: Hash-basiert
	•	deterministischer Fingerprint
	•	L2-normalisierbare Embeddings

Wirkung
	•	Lernprofil ähnelt einem jugendlichen, organisch lernenden Geist.
	•	7–360-Tage-Simulationen (konzeptionell):
	•	beste Lernkurven bisher.

⸻

🟦 v3.7.x – DreamWorker 3.1 & SceneGraph (vision/token) (2025-11/12)

Datei
	•	core/dream_worker.py
(Vorläufer zu v3.7.3-r1, hier ungefähr „DreamWorker 3.1“)

Neu
	•	Run-Lock per fcntl:
	•	_RunLock mit exklusivem Lock auf OROMA_DREAM_LOCK
(Default: /opt/ai/oroma/data/state/dream.lock)
	•	verhindert doppelte DreamWorker-Läufe (Timer + manuell).
	•	Quellen-Multiplexer für Replay:
	•	_iter_recent_chains() liefert SnapChains aus:
	•	Model Registry
	•	LangzeitGedächtnis
	•	FS-Fallback (/opt/ai/oroma/data/snapchains/*.json)
	•	robust gegen fehlende Module/Funktionen.
	•	FS-Fallback:
	•	_coerce_json_to_snapchain(...) konvertiert typische JSON-Exports zu echten SnapChains (Vector-First)
	•	unterstützt Top-Level-Keys wie events, patterns, data, metadata, board.
	•	Gewichtetes Vergessen + Kompression (_forgetting()):
	•	weight ← weight * fade_rate
	•	ENV OROMA_FORGET_DECAY_RATE, Default 0.95
	•	wenn weight < compress_threshold
	•	ENV OROMA_FORGET_THRESHOLD, Default 0.20:
	•	Insert in meta_snaps:
	•	label="compressed_<id>"  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	score=new_weight
	•	sources=["chain:<id>"]  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	Original-SnapChain: status='compressed'
	•	Logik: pro Snap-ID nur einmal pro Lauf loggen → kein Log-Spam.
	•	Self-Healing-Adapter:
	•	RewardEngine, EpisodicMemory, ExplainEngine-Adapter:
	•	nutzen APIs aus core.reward, core.episodic, core.explain, fangen Fehler ab.
	•	Auto-Tuning (_auto_tune()):
	•	optional _auto_tuner.auto_tune(...)
	•	Parameterbereiche:
	•	fade_rate ∈ [0.80, 0.999]
	•	compress_threshold ∈ [0.05, 0.50]

Wirkung
	•	DreamWorker-Läufe sind robuster, idempotent und kollidieren nicht mit parallelen Starts.
	•	SnapChains werden sanft „aufgeräumt“: schwache Chains wandern in meta_snaps, gehen aber nicht verloren.
	•	Replay bleibt auch bei Teilfehlern (ASR, Episoden, Explain) stabil.

⸻

🟦 SceneGraph-Store & Vision-SceneGraphs (vision/token)

Dateien
	•	core/scenegraph_store.py
	•	core/scenegraph_builder.py (v3.8-r3)
	•	tools/scenegraph_selfcheck.py

UI
	•	eigener SceneGraph-Viewer:
	•	Liste von Nodes/Edges
	•	Auto-Build aus MetaSnaps / Vision-Tokens

SceneGraph-Store (scenegraph_store.py)

Zentrale Verwaltung von SceneGraphs in Tabelle scenegraphs:

scenegraphs:
  id INTEGER PRIMARY KEY
  ts INTEGER            -- Unix-Zeit (Sekunden)
  namespace TEXT        -- z. B. 'scene:auto_meta:vision_token' oder 'object:auto:vision'
  source TEXT           -- z. B. 'builder:vision_tokens', 'auto:object:scene:auto_meta:'
  quality REAL          -- optional, kann NULL sein
  graph_json TEXT       -- JSON-String mit Knoten/Kanten/Meta
  notes TEXT            -- freier Kommentar

Hilfsfunktionen zum Bauen von Graphen aus:
	•	MetaSnaps (auto_scenegraph_from_meta(...))
	•	Vision-Tokens (build_scenegraph_from_vision_tokens(...), auto_scenegraph_from_vision(...))

Typische Knoten-Typen:
	•	meta:<id> – MetaSnaps  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	chain:<id> – SnapChains  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	origin:<name> – Herkunft/Quelle  <!-- TODO linkfix: name -> docs/module_snap.md -->
	•	optional: scene:<bucket_ts> – Zeit-Buckets  <!-- TODO linkfix: bucket_ts -> docs/quick_check_3_6.md, docs/curriculum_math_tasks.md -->

SceneGraph-Builder (scenegraph_builder.py)

Pipeline für Vision-Tokens:
	•	wählt SnapChains aus snapchains mit:
	•	origin = 'vision/token' (Default; ENV OROMA_SCENEGRAPH_ORIGIN)
	•	status = 'active'
	•	optional: ts >= since_ts, quality >= min_quality
	•	sortiert nach ID, gruppiert in Blöcke (group_size, Default 32):
	•	pro Gruppe:
	•	berechnet avg_quality
	•	Label via _label_for_group(origin, avg_quality), z. B.
	•	scenegraph:vision_token:hoch
	•	scenegraph:vision_token:niedrig
	•	schreibt MetaSnaps in meta_snaps:
	•	label = 'scenegraph:vision_token:...'
	•	score = avg_quality
	•	sources = ["chain:15160", "chain:15161", ...]
	•	baut optional einen SceneGraph in scenegraphs:
	•	Namespace z. B. scene:auto_meta:vision_token
	•	Node-Anzahl ca. 200–250
	•	Edge-Anzahl ca. 300–600
	•	source = "builder:vision_tokens"

CLI-Beispiel (manuell):

PYTHONPATH=/opt/ai/oroma python3 -m core.scenegraph_builder \
  --origin vision/token \
  --max-chains 256 \
  --group-size 32 \
  --min-quality 0.03 \
  --build-graph \
  --max-meta 64 \
  --max-chains-per-meta 16 \
  --namespace scene:auto_meta:vision_token \
  --verbose

Vision-SceneGraph (direkt auf Tokens)
	•	build_scenegraph_from_vision_tokens(...) / auto_scenegraph_from_vision(...):
	•	arbeitet direkt auf origin='vision/token'-Chains, ohne Meta-Zwischenschritt.

Typische Knoten:
	•	token:<id> – einzelnes Vision-Token  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	scene:<bucket_ts> – Zeitfenster  <!-- TODO linkfix: bucket_ts -> docs/quick_check_3_6.md, docs/curriculum_math_tasks.md -->
	•	origin:vision – Quelle

Kanten:
	•	scene → token (contains)
	•	token → token (next, zeitliche Nachbarschaft)
	•	token → origin (origin)

SceneGraph-Selfcheck (tools/scenegraph_selfcheck.py)
	•	prüft MetaSnaps mit Label-Prefix, z. B. scenegraph:vision_token:.
	•	prüft SceneGraphs im Namespace, z. B. scene:auto_meta:vision_token.

Ausgabe:
	•	Anzahl MetaSnaps
	•	min/max/avg Score pro Label
	•	SceneGraph-Statistik (Nodes/Edges, Quelle, quality)
	•	JSON-Output für Automation.

Wirkung
	•	Vision-Token-Ströme (origin='vision/token') werden zu kompakten Szenen verdichtet.
	•	SceneGraphs ermöglichen:
	•	visuelle Inspektion von Bewegungsmustern
	•	zeitlichen Szenen-Strukturen
	•	Beziehungen zwischen MetaSnaps und Chains.

⸻

🟦 v3.7.3 – DreamWorker 3.3 & ObjectGraph 1.5

(Scene→Object + Object-DB + Audit/Dedupe + /objects-UI v0.8, inkl. Ego-Net & Health-Badge)
(2025-12-09–13)

Dateien (im Live-System bestätigt)
	•	core/dream_worker.py       (v3.7.3-r1 – „DreamWorker 3.3“)
	•	core/objectgraph_builder.py
	•	core/object_extractor.py
	•	core/sql_manager.py        (v3.8-r2 + ObjectGraph-Schema & Helper)
	•	tools/objectgraph_selfcheck.py
	•	tools/objectgraph_audit.py
	•	tools/objectgraph_dedupe.py
	•	tools/objectgraph_top_objects.py
	•	tools/objectgraph_fix_compressed_links.py
	•	ui/objects_ui.py           (v0.8 – mit Ego-Net & Health-Integration)
	•	templates/objects.html     (v0.8)
	•	data/oroma.db              (Tabellen scenegraphs, object_nodes, object_relations real gefüllt)

Ziel von v3.7.3
	•	Erweiterung des bestehenden 2.5D-SceneGraph-Stacks um eine persistente Objekt-Schicht:
	•	Scene-Ebene: scene:auto_meta:vision_token (SceneGraphs aus Vision-Tokens)
	•	Object-Ebene: object:auto:vision (ObjectGraph-Aggregate in scenegraphs)
	•	Object-DB:    object_nodes + object_relations (stabile Knoten & Kanten)
	•	Erste, nutzbare Analyse-UI /objects plus Tools für Health, Audit, Dedupe, Top-Hubs und jetzt Ego-Netz-Fokus + Health-Badge.

⸻

Core – DreamWorker 3.3 (SceneGraph + ObjectGraph im Dream-Zyklus)

ENV-Schalter
	•	OROMA_DREAM_SCENEGRAPH – steuert Vision→SceneGraph-Schritt.
	•	OROMA_DREAM_OBJECTGRAPH – steuert Scene→ObjectGraph-Schritt.

Werte:
	•	0 / false / off → Feature deaktiviert
	•	1 / true / on   → Feature aktiv (Standard in deinem Setup)

Reihenfolge im DreamWorker.run() (vereinfacht)

while not stop:
    self._safe_replay()
    self._forgetting()
    self._research_loop()
    self._missions_update()
    self._curriculum_check()
    self._auto_tune()
    self._scenegraph_from_vision()       # 2.5D: Vision → SceneGraph
    self._objectgraph_from_scenegraph()  # 3D-ish: SceneGraph → ObjectGraph-Aggregat
    time.sleep(interval)

Bei --interval=0 (wie in oroma-dream.service):

self._safe_replay()
self._forgetting()
self._research_loop()
self._missions_update()
self._curriculum_check()
self._auto_tune()
self._scenegraph_from_vision()
self._objectgraph_from_scenegraph()
LOG.info("DreamWorker Single-Run beendet")

Systemd-Integration (wichtig!)

/etc/systemd/system/oroma-dream.service:

[Service]
Type=oneshot
WorkingDirectory=/opt/ai/oroma
SyslogIdentifier=oroma-dream

User=oroma
Group=oroma
UMask=002

EnvironmentFile=-/opt/ai/oroma/.env.systemd
EnvironmentFile=-/opt/ai/oroma/.env

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=UTF-8
Environment=TZ=Europe/Berlin
Environment=PYTHONPATH=/opt/ai/oroma
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Environment=OROMA_LOG_DIR=/opt/ai/oroma/logs

ExecStartPre=/usr/bin/env bash -lc 'mkdir -p /opt/ai/oroma/logs'
ExecStart=/usr/bin/python3 -m core.dream_worker --interval=0

Damit ist klar: SceneGraph- und ObjectGraph-Build laufen zentral über den DreamWorker, getriggert über:

systemctl start oroma-dream.service
# oder zyklisch via oroma-dream.timer

⸻

Core – ObjectGraph-Builder (core/objectgraph_builder.py)

Funktion
	•	auto_objectgraph_from_scenegraphs(...):
	•	lädt SceneGraphs aus scenegraphs mit
	•	namespace LIKE '<source_namespace_prefix>%'  <!-- TODO linkfix: source_namespace_prefix -> docs/module_besonderheit_snappattern.md -->
	•	typisch: scene:auto_meta:vision_token oder allgemein scene:auto_meta:.
	•	verdichtet zu einem Objekt-Aggregat:
	•	wiederkehrende Muster/Token-Gruppen → Objektkandidaten
	•	Co-Occurrence & Topologie → Objekt-Kanten (object_edges).
	•	schreibt neuen Eintrag in scenegraphs:
	•	namespace = target_namespace (z. B. "object:auto:vision")
	•	source   = "auto:object:<src_prefix>" (z. B. auto:object:scene:auto_meta:)  <!-- TODO linkfix: src_prefix -> docs/module_rag_bridge.md, docs/ubersicht_blueprints.md (Hinweis: Link-Token war im Ursprung beschädigt) -->
	•	graph_json enthält JSON mit Knoten/Kanten + meta.stats.

Rückgabe

{
  "ok": true,
  "meta": {
    "saved_id": 395,
    "stats": {
      "graphs_used": 32,
      "nodes_seen": 7916,
      "edges_seen": 11672,
      "objects": 4547,
      "object_edges": 8334,
      "source_namespace_prefix": "scene:auto_meta:"
    }
  }
}

(Werte exemplarisch, wachsen mit weiteren Läufen.)

Beispiel aus deinem System (nach einem Dream-Run)

Log (dream.out.log):

[2025-12-10 21:32:19,077] [INFO] Dream-SceneGraph (origin=vision/token): ok=True graph_id=392 nodes=249 edges=368
[2025-12-10 21:32:19,343] [INFO] Dream-ObjectGraph (src_ns=scene:auto_meta:): ok=True graph_id=393 objects=4603 edges=8390 graphs_used=32
[2025-12-10 21:32:19,354] [INFO] DreamWorker Single-Run beendet

DB-Check:

SELECT id, ts, namespace, source, quality, graph_json, notes
  FROM scenegraphs
 WHERE namespace LIKE 'object:auto:%'
 ORDER BY id DESC
 LIMIT 1;

Python-Ausgabe (vereinfacht):

Letzter ObjectGraph:
  id:        395
  ts:        1765400566
  namespace: object:auto:vision
  source:    auto:object:scene:auto_meta:
  quality:   None
  notes:     DreamWorker objectgraph (src=scene:auto_meta:)
  stats:
    graphs_used:          32
    nodes_seen:         7916
    edges_seen:        11672
    objects:            4547
    object_edges:       8334
    source_namespace_prefix: scene:auto_meta:

Wirkung
	•	Auf scenegraphs-Ebene entsteht eine kompakte Zusammenfassung der Objektlandschaft über viele Vision-Szenen.

⸻

Core – ObjectGraph-Schema & SqlManager (Object-DB)

Neue Tabellen in core/sql_manager.py (v3.8-r2 + ObjectGraph-Erweiterung):

object_nodes:
  id INTEGER PRIMARY KEY AUTOINCREMENT
  kind TEXT NOT NULL              -- "object", "snapchain", "meta", "origin"
  label TEXT NOT NULL             -- z. B. "Chain 44919", "compressed_44919", "vision/token"
  meta_json TEXT                  -- optionales JSON (Stats, SceneRefs, Rohdaten)
  created_ts INTEGER NOT NULL     -- Unix-Zeit

object_relations:
  id INTEGER PRIMARY KEY AUTOINCREMENT
  a_id INTEGER NOT NULL           -- Quelle (object_nodes.id)
  relation TEXT NOT NULL          -- "meta_to_chain", "chain_to_origin", "origin", "describes", ...
  b_id INTEGER NOT NULL           -- Ziel (object_nodes.id)
  confidence REAL NOT NULL DEFAULT 1.0
  source_scene_id INTEGER         -- referenzierte scenegraphs.id (optional)
  ts INTEGER NOT NULL             -- Unix-Zeit
  notes TEXT                      -- freier Text/JSON

Indizes:
	•	idx_object_nodes_kind (kind)
	•	idx_object_nodes_label (label)
	•	idx_object_relations_a (a_id)
	•	idx_object_relations_b (b_id)
	•	idx_object_relations_rel (relation)
	•	idx_object_relations_ts (ts)

Helper-Funktionen
	•	ensure_object_node(kind, label, meta=None, db_path=None) -> int
	•	Dedupe auf (kind, label):
	•	existiert → ID zurückgeben, meta_json ggf. mergen/ergänzen.
	•	existiert nicht → neuer Eintrag.
	•	insert_object_relation(a_id, relation, b_id, confidence=1.0, source_scene_id=None, ts=None, notes=None, db_path=None) -> int
	•	fügt Kante zwischen zwei Objektknoten ein.
	•	notes kann Dict (→ JSON) oder Text sein.
	•	fetch_object_nodes(kind=None, limit=200, db_path=None) -> List[Dict]
	•	fetch_object_relations_for_node(node_id, limit=200, db_path=None)
	•	fetch_object_relations(limit=200, db_path=None)

Live-Status (Beispiel-Snapshot, Dez 2025; Werte wachsen)
	•	SELECT COUNT(*) FROM object_nodes; → z. B. 8000+
	•	SELECT COUNT(*) FROM object_relations; → ca. 16000

⸻

Core – Object-Extractor (core/object_extractor.py)

Zweck
	•	Brücke zwischen scenegraphs-Einträgen und der Object-DB (object_nodes / object_relations).

Arbeitsweise (vereinfacht)
	•	lädt SceneGraphs aus scenegraphs, typischerweise:
	•	namespace='scene:auto_meta:vision_token' (Szenen)
	•	namespace='object:auto:vision' (ObjectGraph-Aggregate)
	•	zerlegt Graphen in:
	•	Objektkandidaten → ensure_object_node(...) → object_nodes
	•	Kanten/Relationen → insert_object_relation(...) → object_relations

CLI-Beispiele

# Trockenlauf
PYTHONPATH=/opt/ai/oroma python3 -m core.object_extractor \
  --max-graphs 5 --dry-run --verbose

# Real-Lauf
PYTHONPATH=/opt/ai/oroma python3 -m core.object_extractor \
  --max-graphs 64 --min-quality 0.0 --verbose

Typische Logmeldungen:

object_extractor: scenegraph id=304 -> 4363 nodes, 6931 relations (namespace=object:auto:vision)
object_extractor: scenegraph id=303 ->  248 nodes,  366 relations (namespace=scene:auto_meta:vision_token)
...
object_extractor: fertig – total_nodes=1243, total_relations=1836 (dry_run=True)

Wirkung
	•	SceneGraphs liefern Roh-Strukturen (Szenen & Objekt-Aggregate).
	•	object_extractor erzeugt daraus eine normal querybare Objekt-Welt in object_nodes / object_relations.

⸻

Tools – ObjectGraph-Selfcheck, Audit, Dedupe, Fix & Top-Objekte

tools/objectgraph_selfcheck.py
Ziel
	•	CLI-Selfcheck für die ObjectGraph-Schicht:
	•	lädt object_nodes, object_relations
	•	prüft:
	•	Gesamtzahl der Nodes und Verteilung nach kind
	•	Gesamtzahl der Relationen
	•	Confidence-Min/Max/Avg
	•	Referenzintegrität (missing_a, missing_b)
	•	SceneGraphs mit Namespace-Prefix object:auto::
	•	objects, object_edges, graphs_used
	•	JSON-Output via --json-only (Health-Monitoring/Automation).

Beispielausgabe (Dez 2025; Werte wachsen weiter):

{
  "namespace_prefix": "object:auto:",
  "object_nodes": {
    "total": 8148,
    "kinds": [
      {"kind": "object",    "count": 2841},
      {"kind": "snapchain", "count": 793},
      {"kind": "meta",      "count": 83},
      {"kind": "origin",    "count": 1}
    ]
  },
  "object_relations": {
    "total": 15996,
    "confidence": {"min": 1.0, "max": 1.0, "avg": 1.0},
    "integrity": {"missing_a": 0, "missing_b": 0}
  },
  "scenegraphs": {
    "total": 54,
    "namespace_prefix": "object:auto:",
    "stats": {
      "objects": {
        "min": 3044.0,
        "max": 4715.0,
        "avg": 4032.57
      },
      "object_edges": {
        "min": 5617.0,
        "max": 8502.0,
        "avg": 7477.91
      },
      "graphs_used": {
        "min": 32.0,
        "max": 32.0,
        "avg": 32.0
      }
    }
  }
}

(Zahlen sind Momentaufnahme; Logik & Struktur bleiben konstant.)

⸻

tools/objectgraph_audit.py
Ziel
	•	Auditiert gezielt die Beziehung zwischen:
	•	Meta-Knoten label LIKE 'compressed_%' in object_nodes (kind='meta')
	•	dazugehörigen komprimierten SnapChains in object_nodes
(kind='snapchain', label='Chain <id>')  <!-- TODO linkfix: id -> docs/module_ui.md -->

Erwartung
	•	pro Meta-Knoten genau eine meta_to_chain-Relation
	•	pro komprimierter SnapChain genau eine meta_to_chain + eine chain_to_origin.

Aktueller Status (Dez 2025, nach Fix & Dedupe)

[INFO] [ObjectGraphAudit] ObjectNodes geladen: total=8148
[INFO] [ObjectGraphAudit] ObjectRelations geladen: total=15996
[INFO] [ObjectGraphAudit] Meta-Knoten (compressed_*) = 135, komprimierte SnapChains = 111
[INFO] [ObjectGraphAudit] Audit fertig: meta_ok=135/135, compressed_snap_ok=111/111
{
  "summary": {
    "nodes_total": 8148,
    "relations_total": 15996,
    "compressed_meta_nodes": 135,
    "compressed_snapchains": 111,
    "meta_to_chain": {"ok": 135, "failed": 0},
    "compressed_snapchain_links": {"ok": 111, "failed": 0}
  },
  "details": {
    "meta_errors": [],
    "compressed_snapchain_errors": []
  }
}

⸻

tools/objectgraph_dedupe.py
Ziel
	•	Duplikate in object_relations entfernen.
	•	Duplikat = identische Kombination aus (a_id, relation, b_id).

Modi
	•	--dry-run → zählt nur Duplikate, löscht nichts.
	•	ohne Flag → löscht Duplikate tatsächlich.

Typischer Durchlauf (vorher):

Geladene object_relations: 43600
Duplikate gesamt: 37075
  Relation 'meta_to_chain': 24836 Duplikate
  Relation 'chain_to_origin': 12239 Duplikate

Nach Real-Run:
	•	37075 Duplikate entfernt
	•	object_relations sinkt erst deutlich, wächst später wieder durch neue Läufe auf ca. 15996
	•	objectgraph_audit.py bestätigt 1:1-Beziehungen.

⸻

tools/objectgraph_fix_compressed_links.py
Ziel
	•	Sicherstellen, dass alle compressed_*-Meta-Knoten ordentlich an SnapChains & Origin hängen:
	•	meta (compressed_<id>) → chain (Chain <id>) via meta_to_chain  <!-- TODO linkfix: id -> docs/module_ui.md | id -> docs/module_ui.md -->
	•	chain (Chain <id>) → origin:vision/token via chain_to_origin.  <!-- TODO linkfix: id -> docs/module_ui.md -->

Dry-Run-Beispiel (Auszug):

[INFO] [FixCompressed] Verwende origin-Knoten id=3 label='vision/token'
[INFO] [FixCompressed] Gefundene compressed-Meta-Knoten: 135
[INFO] Ergänze meta_to_chain: meta=6665 (compressed_50043) -> chain=6666 (Chain 50043)
[INFO] Ergänze chain_to_origin: chain=6666 (Chain 50043) -> origin=3
...
[INFO] Ergänze meta_to_chain: meta=6751 (compressed_49989) -> chain=28 (Chain 49989)
[INFO] Ergänze meta_to_chain: meta=6752 (compressed_49988) -> chain=27 (Chain 49988)
[INFO] Fertig. meta_to_chain ergänzt: 56, chain_to_origin ergänzt: 32, ohne Chain: 0, ohne Origin: 0

	•	Im Real-Run (ohne --dry-run) werden diese Relationen tatsächlich eingefügt.
	•	Nachlauf von objectgraph_audit.py bestätigt konsistente Links (0 Fehler).

⸻

tools/objectgraph_top_objects.py
Funktion
	•	Berechnet für alle object_nodes:
	•	Degree = Anzahl eingehender + ausgehender Kanten
	•	Anzahl unterschiedlicher Relationstypen (RelTypes)

Optionen
	•	--min-degree N (Default 3)
	•	--limit N (Default 20)
	•	--hide-global-hubs → blendet globale Hubs aus (vision/token, scenegraph:vision_token:hoch/niedrig).

Typische Ausgabe (ohne Hub-Filter):

ObjectNodes (gesamt): 3718 (davon kind='object': 2841)
ObjectRelations:       6525
Filter: min_degree=3 → Top 20

    ID  Degree  RelTypes  Label
-------------------------------
   346    2418         1  vision/token
   410    1991         1  scenegraph:vision_token:hoch
   344     112         1  scenegraph:vision_token:niedrig
   491       3         2  Chain 44979
   493       3         2  Chain 44978
   ...

Mit --hide-global-hubs:
	•	globale Ursprungs-/Meta-Knoten werden ausgeblendet,
	•	lokale Objekte und Chains treten hervor.

⸻

UI – ObjectGraph-Viewer /objects (v0.8 – Ego-Net + Health-Badge)

Dateien
	•	ui/objects_ui.py (v0.8, 13.12.2025)
	•	templates/objects.html (v0.8, 13.12.2025)
	•	Link in base.html (Navbar: „Objects“ / „ObjectGraph“)

Blueprint & Route
	•	bp = Blueprint("objects", __name__, template_folder="templates")
	•	Routen:
	•	GET /objects
	•	GET /objects/
	•	optionale Query-Parameter:
	•	?focus_id=<id> → Fokus-Knoten + Ego-Netz  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	?kind=<kind>   → object, snapchain, meta, origin oder leer für „alle“.  <!-- TODO linkfix: kind -> docs/module_fusion.md -->

⸻

Backend-Logik (objects_index, v0.8)
1. Parameter einlesen
	•	filter_kind: Optional[str] = request.args.get("kind") or None
	•	focus_id: Optional[int] aus focus_id-Query-Parameter (robust gegen Non-Int).

2. Stichprobengröße
	•	SAMPLE_LIMIT = 500 für:
	•	object_nodes (Nodes-View)
	•	object_relations (Relations-View)

3. Nodes laden (DB-Ebene, inkl. kind-Filter)
	•	Grundstichprobe (für Mapping & Stats):

SELECT id, kind, label, meta_json
  FROM object_nodes
 ORDER BY id DESC
 LIMIT 500;

	•	Gefilterte View:
	•	wenn filter_kind gesetzt:

SELECT id, kind, label, meta_json
  FROM object_nodes
 WHERE kind = ?
 ORDER BY id DESC
 LIMIT 500;

	•	sonst: nodes_view = nodes_sample_all.

	•	total_nodes:
	•	mit Filter: SELECT COUNT(*) FROM object_nodes WHERE kind = ?
	•	ohne Filter: SELECT COUNT(*) FROM object_nodes.

4. Relations-Stichprobe (global)

SELECT id, a_id, b_id, relation, confidence
  FROM object_relations
 ORDER BY id DESC
 LIMIT 500;

	•	total_relations = COUNT(*) FROM object_relations.

5. Mapping id → Node (inkl. Nachladen für Relationen)
	•	Initial:

nodes_by_id = {row["id"]: row for row in nodes_sample_all}

	•	fehlende IDs aus relations sammeln (a_id, b_id)
	•	chunked IN-Query (wegen SQLite-Parameterlimit, z. B. CHUNK_SIZE = 800):

SELECT id, kind, label, meta_json
  FROM object_nodes
 WHERE id IN (?, ?, ...);

	•	Ergebnis in nodes_by_id mergen.

6. Statistiken
	•	kinds_summary: Counter über kind im aktuellen nodes-View.
	•	top_object_labels:
	•	nur Nodes mit kind="object" im aktuellen View.
	•	Label normalisiert (strip()), leere Labels entfernt.
	•	Top 20 nach Häufigkeit.
	•	relations_summary:
	•	Counter über relation in relations.

7. Degree-Statistik & Top-Hubs
	•	degree_counter: zählt Vorkommen von a_id und b_id.
	•	reltypes_per_node: Menge unterschiedlicher Relationstypen pro Node.
	•	top_objects_degree:
	•	nur kind="object".
	•	enthält:
	•	id, kind, label
	•	degree (Gesamt)
	•	rel_types (Anzahl Relationstypen)
	•	sortiert nach degree (absteigend) und Label.
	•	gefiltert via min_degree_for_top (Default: 2)
	•	auf 20 Einträge begrenzt.

8. Fokus-Ego-Netz (focus_degree_info)
	•	Wenn focus_id gesetzt und in nodes_by_id vorhanden:
	•	degree = degree_counter[focus_id] (oder 0)
	•	relation_types = len(reltypes_per_node.get(focus_id, set()))
	•	neighbors: alle direkten Nachbarn aus relations:
	•	wenn relation.a_id == focus_id → Nachbar b_id
	•	wenn relation.b_id == focus_id → Nachbar a_id
	•	für jeden Nachbarn:
	•	Node aus nodes_by_id holen
	•	Struktur: {id, kind, label}
	•	Ergebnis:

focus_degree_info = {
    "node_id": focus_id,
    "degree": degree,
    "relation_types": relation_types,
    "neighbors": [...],
}

	•	focus_relations (optional, für Erweiterungen): Subset der relations, in denen focus_id vorkommt.

9. Health-Integration (objectgraph_selfcheck)
	•	_get_objectgraph_health() ruft tools/objectgraph_selfcheck.py als Subprozess:

python3 tools/objectgraph_selfcheck.py \
  --db-path /opt/ai/oroma/data/oroma.db \
  --namespace-prefix object:auto: \
  --json-only

	•	Output wird geparst, über _normalize_health(...) in einheitliches Format gebracht:

{
  "overall_status": "ok" | "warning" | "error" | "unknown",
  "warnings": [...],
  "errors": [...]
}

	•	Kurzzeit-Cache (_OBJ_HEALTH_CACHE) mit TTL = 60 s, um nicht bei jedem Request den Selfcheck neu zu starten.
	•	Template-Variablen:
	•	health_status
	•	health_warnings_count
	•	health_errors_count.

⸻

Template objects.html (v0.8)
Hauptbereiche:
	1.	Header + Health-Badge
	•	Titel: „ObjectGraph – Objekte & Relationen“
	•	Kurzbeschreibung
	•	Rechts oben: Health-Badge basierend auf health_status:
	•	ok → grün: ObjectGraph OK
	•	warning → gelb: ObjectGraph Warnung
	•	error → rot: ObjectGraph Fehler
	•	sonst → grau: ObjectGraph Status unbekannt
	•	Darunter: Warnings: N | Errors: M.
	2.	High-Level Overview (Nodes / Relations / Selfcheck-Hinweis)
	•	Karte „Überblick – Nodes“:
	•	total_nodes passend zum Filter
	•	Hinweis auf Stichprobe (max. nodes|length)
	•	Liste kinds_summary mit Badge je kind.
	•	Karte „Überblick – Relationen“:
	•	total_relations gesamt
	•	Stichprobengröße relations|length
	•	Liste relations_summary.
	•	Karte „Hinweis – CLI-Selfcheck“:
	•	zeigt die CLI-Aufrufe:

PYTHONPATH=/opt/ai/oroma \
  python3 tools/objectgraph_selfcheck.py \
    --db-path /opt/ai/oroma/data/oroma.db \
    --namespace-prefix object:auto: \
    --json-only > /tmp/objectgraph_report.json

	3.	Filter & Fokus (Formular)
	•	kind-Select:
	•	(alle), object, snapchain, meta, origin
	•	Beschreibung: Filtert auf DB-Ebene nach kind.
	•	focus_id-Input (number):
	•	Hinweis: „Wenn gesetzt, werden Relationen um diesen Knoten fokussiert (Ego-Netz)“.
	•	Buttons:
	•	„Anwenden“
	•	„Reset“ (Link auf url_for('objects.objects_index')).
	4.	Top-Statistiken
	•	Karte „Top Objekt-Labels (kind = ‘object’)“:
	•	Tabelle label / Anzahl
	•	basiert auf aktuellem Nodes-View (nach Kind-Filter).
	•	Karte „Top vernetzte Knoten“:
	•	Optionaler Hinweis (Degree ≥ min_degree_for_top)
	•	Tabelle mit:
	•	ID (als Link, setzt focus_id)
	•	Kind
	•	Label
	•	Degree
	•	Zeilen mit aktuellem Fokus-Knoten (focus_id) werden farblich hervorgehoben (table-warning).
	5.	Fokus-Knoten & Ego-Netz
	•	Nur, wenn focus_id gesetzt und Knoten existiert.
	•	Link in Header: Fokus-Knoten: ID <id> (klickbar, setzt focus_id erneut).  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	Link zwischen focus_id und Filter (kind bleibt erhalten).
	•	Linker Bereich:
	•	Label des Fokus-Knotens
	•	meta_json (falls vorhanden) in <pre> mit Scroll.  <!-- TODO linkfix: pre -> docs/soloprojekt.md -->
	•	Rechter Bereich:
	•	Wenn focus_degree_info vorhanden:
	•	Degree und Relationstypen
	•	Tabelle „Nachbarn im aktuellen Sample“:
	•	ID (klickbarer Link → neuer Fokus)
	•	Kind
	•	Label
	•	sonst:
	•	Hinweis, dass keine Degree-Infos verfügbar sind (außerhalb des Samples oder ohne Relationen).
	6.	Tabellen: ObjectNodes & ObjectRelations
	•	ObjectNodes:
	•	Tabelle:
	•	ID (Link, setzt focus_id)
	•	Kind
	•	Label
	•	Fokus-Knotenzeile farblich markiert.
	•	ObjectRelations:
	•	Tabelle:
	•	ID
	•	Relation
	•	A (Node)
	•	B (Node)
	•	Für A/B:
	•	versucht Lookup via nodes_by_id
	•	falls bekannt:
	•	zeigt [<id>] als Link (setzt focus_id), plus kind – label.  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	falls unbekannt:
	•	[<id>] (unbekannt).  <!-- TODO linkfix: id -> docs/module_ui.md -->
	•	Relationen, in denen focus_id vorkommt, sind farblich markiert (table-warning).

Wirkung (ObjectGraph 1.5)
	•	/objects macht die sonst „versteckte“ Objekt-Schicht sichtbar und navigierbar:
	•	globale Statistiken (Nodes, Relationen, Typverteilung)
	•	Top-Labels und Top-Hubs
	•	Ego-Netz-View für beliebige Knoten via focus_id.
	•	Die Health-Integration via objectgraph_selfcheck.py liefert:
	•	sofort sichtbaren Zustand des ObjectGraph (OK / Warnung / Fehler).
	•	Durch nodes_by_id-Nachladen werden in der UI deutlich weniger (unbekannt)-Nodes angezeigt, Degree-Statistik und Ego-Netz sind realistischer.
	•	Zusammen mit objectgraph_audit, objectgraph_dedupe, objectgraph_fix_compressed_links und objectgraph_top_objects bildet das Ganze den ObjectGraph 1.5:
	•	strukturell konsistent,
	•	dedupliziert,
	•	interaktiv explorierbar,
	•	vorbereitend für NMR 3.75, semantische Objekt-Typen und spätere Audio↔Vision-Brücken.

⸻

🟦 v3.8 – Regelarchiv & Pruning (2025-10-17)

Dateien
	•	core/regelarchiv.py
	•	Export/Import angepasst

Neu
	•	Regelarchiv:
	•	aktive/inaktive Regeln mit weight
	•	Pruning schwacher Regeln
	•	Exportfähigkeit für RuleSets
	•	Early-Stopping erosiver Rules

Wirkung
	•	vorsichtigeres Lernen
	•	weniger Variation
	•	stabil, aber etwas konservativer als 3.7.

⸻

🟦 v3.8-r1/r2 – Snap v1.1, SnapIndex, SnapPattern L2 & RAG-Benchmark (2025-11-23)

Core – Snap/SnapPattern/SnapToken
	•	Snap v1.1 (core/snap.py):
	•	L2-Norm-Cache im Snap (Fingerprint-stabil, Norm reproduzierbar)
	•	Selftest schreibt Snap + SnapIndex-Eintrag (Norm, Feature-Dim, Fingerprint)
	•	Debug-Modus via ENV (OROMA_SNAP_LOG, OROMA_SNAP_LOGLEVEL).
	•	SnapPattern v3.8-r1 (core/snappattern.py):
	•	Centroid-L2-Norm im centroid-Blob ("l2_norm")
	•	Gap-Detection:
	•	optionale Vector-DB (vector_migration.query) ab bestimmtem Chain-Count
	•	Fallback: Cosine-Ähnlichkeit über SnapPattern-Centroids
	•	SnapToken v3.7 (core/snaptoken.py):
	•	bleibt Kompatibilitätsanker zwischen Snap-Ebene und LLM/RAG.

DB – SqlManager v3.8-r2 (core/sql_manager.py)
	•	SnapIndex (snap_index):

id, ts, source, privacy_tier, feature_dim,
l2_norm, fingerprint (UNIQUE), payload (BLOB)

	•	Helper:
	•	insert_snap_index(..., dedup=True) → Upsert auf Basis fingerprint
	•	fetch_snap_index_by_fingerprint()
	•	Indizes:
	•	idx_snap_ts (ts)
	•	idx_snap_src (source)
	•	Allgemeine Robustheit:
	•	busy_timeout=5000 ms für alle Connections
	•	optional WAL-Modus via OROMA_DB_WAL=1.
	•	Schema-Ensure:
	•	ergänzt u. a.:
	•	curriculum_state, empathy_snaps, coverage_log, setcalc_log
	•	Calculator-JSON-Spalten (truth_json, got_json)
	•	episodische Tabellen (episodes, episode_events, episodic_metrics)
	•	scenegraphs, object_nodes, object_relations
	•	Helper-Erweiterungen:
	•	insert_cam_token(): Kamera-Tokens als JSON-Blob in snapchains
	•	kind="cam_token", v=[…], motion/edges/color
	•	fetch_cam_tokens_window() mit Filter nach Zeitfenster, Qualität, origin LIKE 'vision/%'.

RAG – Bridge, Import & Benchmark
	•	RAG-Bridge v3.8-r1 (core/rag_bridge.py):
	•	RAGStore (FTS5 + BM25)
	•	MATCH-Normalisierung der Frage
	•	optionales Reranking via core.fusion.FusionEngine.
	•	Ask-UI v3.7 (ui/ask_ui.py + templates/ask.html):
	•	Route /ask (HTML, bewusst ohne Token)
	•	Route /ask/api (JSON, tokenpflichtig via require_ui_token).
	•	Benchmark & Sample:
	•	tools/bench_rag.py (hit@k, nDCG@10, Latenz)
	•	tools/rag_import_sample.py
	•	tests/rag_qa_sample.json (Demo-QA)

Beobachtete Wirkung (Demo-Daten)
	•	ohne Rerank:
	•	hit@10 ≈ 1.0
	•	nDCG@10 ≈ 1.63
	•	mit Rerank:
	•	gleiche Treffergüte (Demo-Set zu klein, aber Pipeline verifiziert).

⸻

🟦 v3.8-r3 – DeviceHub-Sensoren & Vision-Episoden (2025-12-07)

Dateien
	•	core/device_hub.py
	•	core/sensor_channel.py
	•	wrappers/sensor_ir_front.py
	•	core/episodic_writer.py
	•	core/sql_manager.py (Episoden-Hook in insert_cam_token)
	•	System-Root-Skript:
	•	backup_oroma_with_db.sh (Backup mit schlanker DB)

Ziel von v3.8-r3
	•	DeviceHub als generische Sensorzentrale, nicht nur Kamera/Audio.
	•	Vision-Tokens (cam_token) bekommen episodisches Gedächtnis.
	•	Backups erzeugen eine schlanke Analyse-DB (max. 1000 Zeilen pro Tabelle).

Core – SensorChannel & DeviceHub-Sensor-Loop
	•	core/sensor_channel.py:
	•	Basisklasse BaseSensorChannel (name, kind, origin, namespace, interval_sec)
	•	Methoden:
	•	due(now)
	•	read_raw()
	•	build_snapchain_data(raw, ts)
	•	mark_polled(now)
	•	core/device_hub.py:
	•	Felder: _sensor_channels, _sensor_thread, _sensor_run
	•	ENV:
	•	OROMA_SENSORS_ENABLED
	•	OROMA_SENSORS_SLEEP_BASE
	•	API:
	•	register_sensor_channel(...)
	•	list_sensor_channels()
	•	start_sensors(), stop_sensors()
	•	get_sensor_health()
	•	Poll-Loop _sensor_loop():
	•	iteriert Channels, ruft read_raw()/build_snapchain_data, schreibt snapchains, erzeugt Audit-Events.

Beispiel: Front-IR-Sensor (wrappers/sensor_ir_front.py)
	•	FrontIrChannel(BaseSensorChannel):
	•	origin="sensor/ir/front"
	•	kind="ir_distance"
	•	namespace="sensor"
	•	interval_sec≈0.5s
	•	read_raw():
	•	aktuell simulierte Werte (sanft driftender Abstand)
	•	build_snapchain_data(...):
	•	SnapChain mit origin="sensor/ir/front", kind="ir_distance", distance_cm=<float>.  <!-- TODO linkfix: float -> docs/fazit_3_8.md -->

Core – Episodisches Gedächtnis für Vision (cam_token)
	•	core/episodic_writer.py (v3.7.3-r1):
	•	EpisodeWriter für Audio und Vision:
	•	Vision:
	•	kind="vision"
	•	source="vision/token"
	•	label="Vision-Session"
	•	Rotationslogik:
	•	max_duration_sec=3600
	•	max_idle_sec=300
	•	log_vision_cam_token_global(...):
	•	legt Episode an / erweitert sie
	•	schreibt episode_events mit event_type="cam_token" und ref_table="snapchains".

Wirkung
	•	Sensoren fließen in SnapChains ein und erscheinen später in MetaSnaps, SceneGraphs, ObjectGraphs, Episoden und Replay.
	•	Vision-Tokens werden episodisch organisiert (Tage/Sessions).

Infra – Backup mit schlanker DB
	•	backup_oroma_with_db.sh:
	•	erstellt ZIP mit Projektbaum /opt/ai/oroma
	•	erzeugt eine gesampelte data/oroma.db (max. 1000 Zeilen pro Tabelle)
	•	interne SQLite-Tabellen werden korrekt behandelt
	•	knowledge.db bleibt optional draußen → kleinere Backups.

⸻

🧪 Verifikation & Health-Checks (Dezember 2025)

Wichtige Tests im Live-System (Auszug):
	1.	DreamWorker-Run (SceneGraph + ObjectGraph) über systemd

systemctl start oroma-dream.service

Log-Auszug:

[INFO] Snap 49967–50043 komprimiert → MetaSnap
[INFO] Dream-SceneGraph (origin=vision/token): ok=True graph_id=392 nodes=249 edges=368
[INFO] Dream-ObjectGraph (src_ns=scene:auto_meta:): ok=True graph_id=393 objects=4603 edges=8390 graphs_used=32
[INFO] DreamWorker Single-Run beendet

→ bestätigt: DreamWorker führt Replay, Forgetting, dann SceneGraph- und ObjectGraph-Build aus.

	2.	Schema-Check scenegraphs (kein Fantasie-Schema)

PRAGMA table_info(scenegraphs);

Ergebnis:

['id', 'ts', 'namespace', 'source', 'quality', 'graph_json', 'notes']

→ Doku angepasst: wir sprechen von ts + graph_json, nicht mehr von created_at/graph.

	3.	Letzten ObjectGraph inspizieren

PYTHONPATH=/opt/ai/oroma python3 - << 'PY'
import json
from core import sql_manager

with sql_manager.get_conn() as conn:
    row = conn.execute("""
        SELECT id, ts, namespace, source, quality, graph_json, notes
          FROM scenegraphs
         WHERE namespace LIKE 'object:auto:%'
      ORDER BY id DESC
         LIMIT 1
    """).fetchone()

    raw = row["graph_json"]
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")

    data  = json.loads(raw)
    meta  = data.get("meta")  or {}
    stats = meta.get("stats") or {}

    print("Letzter ObjectGraph:")
    print(f"  id:        {row['id']}")
    print(f"  ts:        {row['ts']}")
    print(f"  namespace: {row['namespace']}")
    print(f"  source:    {row['source']}")
    print(f"  quality:   {row['quality']}")
    print(f"  notes:     {row['notes']}")
    print("  stats:")
    for k in ("graphs_used", "nodes_seen", "edges_seen",
              "objects", "object_edges", "source_namespace_prefix"):
        print(f"    {k}: {stats.get(k)}")
PY

→ Ausgabe wie oben dokumentiert (graphs_used=32, objects≈4547, object_edges≈8334).

	4.	ObjectGraph-Audit & Dedupe
	•	objectgraph_dedupe.py --dry-run:
	•	zeigte 37k+ Duplikate.
	•	objectgraph_dedupe.py (Real-Run):
	•	entfernte Duplikate.
	•	objectgraph_audit.py:
	•	bestätigte:
	•	compressed_meta_nodes=135, compressed_snapchains=111
	•	meta_to_chain.ok=135, compressed_snapchain_links.ok=111
	•	0 Fehler.
	5.	Fix für compressed_*-Meta-Knoten
	•	objectgraph_fix_compressed_links.py --dry-run:
	•	zeigte, welche meta_to_chain/chain_to_origin-Kanten fehlen würden.
	•	Real-Run:
	•	trug diese Kanten nach.
	•	erneuter objectgraph_audit.py:
	•	bestätigte 1:1-Bindung von compressed_*-Meta-Knoten zu Chains + Origin.
	6.	Selfcheck für ObjectGraph + UI-Integration
	•	objectgraph_selfcheck.py:
	•	bestätigte:
	•	object_nodes.total ≈ 8k+
	•	object_relations.total ≈ 16k
	•	SceneGraphs mit namespace_prefix='object:auto:', graphs_used=32, konsistente Min/Max/Avg.
	•	/objects:
	•	Health-Badge zeigt Status (OK / Warnung / Fehler)
	•	Zahlen in der UI (z. B. „5659 ObjectNodes passend zum aktuellen Filter, max. 500 angezeigt“) korrelieren mit DB-Zustand.
	
### v3.7.3 – 2025-12-14 (Math-Transfer + Crossmodal-Linking)

- Calculator-Curriculum erweitert: neue Task-Typen (u.a. arith/seq/fill/cmp/fractions/quadratic)
- `calc/result` wird als SnapChain persistiert (inkl. Vektor; Default 84D)
- MetaSnaps `calc:*` werden automatisch erzeugt/aktualisiert (z.B. `calc:fill:basic_arith`)
- Crossmodal-Linker `link/calc_vision` integriert (Zeitfenster + Cosine-Score; ENV-gesteuert)
- core/__init__.py exportiert `calc_solver` und `calc_vision_linker` als public Modules (robuster Import)

## Verifikation (SQL)

```sql
-- Calculator SnapChains
SELECT COUNT(*) FROM snapchains WHERE origin='calc/result';
SELECT id, ts, quality, substr(CAST(blob AS TEXT),1,160)
  FROM snapchains
 WHERE origin='calc/result'
 ORDER BY id DESC
 LIMIT 5;

-- Calculator MetaSnaps
SELECT id, label, score, substr(sources,1,160)
  FROM meta_snaps
 WHERE label LIKE 'calc:%'
 ORDER BY id DESC
 LIMIT 10;

-- Crossmodal Links (Calc ↔ Vision)
SELECT COUNT(*) FROM snapchains WHERE origin='link/calc_vision';
SELECT id, ts, quality, source_id, substr(CAST(blob AS TEXT),1,200)
  FROM snapchains
 WHERE origin='link/calc_vision'
 ORDER BY id DESC
 LIMIT 10;

⸻

🔧 Upgrade-Hinweise

(3.0 → 3.5 → Patch 1 → Patch 2 → 3.7 → 3.7.x → 3.8 → 3.8-r1/r2 → 3.8-r3 → 3.7.3-ObjectGraph-DB / ObjectGraph 1.5)

Allgemein
	•	Alle Blueprints in run_oroma.py registrieren:
	•	/scenegraph, /objects, /episodic, /health, /ask, /why,
/games, /models, /learning, /video, /replay, /dream, /memory, /knowledge, …
	•	Wichtige ENV:
	•	OROMA_AGENT_*
	•	OROMA_DREAM_*, OROMA_DREAM_LOCK
	•	OROMA_DREAM_SCENEGRAPH, OROMA_DREAM_OBJECTGRAPH
	•	OROMA_UI_TOKEN, OROMA_REQUIRE_TOKEN
	•	OROMA_BASE, OROMA_BASE_DIR
	•	OROMA_SENSORS_ENABLED, OROMA_SENSORS_SLEEP_BASE
	•	OROMA_HUB_AUDIT_*
	•	OROMA_DB_WAL
	•	OROMA_ENABLE_METASNAP
	•	OROMA_SCENEGRAPH_ORIGIN, OROMA_SCENEGRAPH_MAX_CHAINS, OROMA_SCENEGRAPH_GROUP_SIZE
	•	OROMA_FORGET_DECAY_RATE, OROMA_FORGET_THRESHOLD.

DB-Schema
	•	Schema-Ensure:

PYTHONPATH=/opt/ai/oroma python3 -m core.sql_manager --ensure

	•	ergänzt automatisch:
	•	SnapIndex
	•	Calculator-Spalten
	•	Empathie-/Coverage-Tabellen
	•	Episoden-Tabellen
	•	scenegraphs
	•	object_nodes
	•	object_relations
	•	optional WAL:

export OROMA_DB_WAL=1


ObjectGraph
	•	SceneGraphs erzeugen (falls DreamWorker nicht genutzt wird):

PYTHONPATH=/opt/ai/oroma python3 -m core.scenegraph_builder \
  --origin vision/token --max-chains 256 --group-size 32 \
  --min-quality 0.03 --build-graph \
  --max-meta 64 --max-chains-per-meta 16 \
  --namespace scene:auto_meta:vision_token --verbose

	•	ObjectGraphs aggregieren:

PYTHONPATH=/opt/ai/oroma python3 -m core.objectgraph_builder ...

	•	Object-DB füllen:

PYTHONPATH=/opt/ai/oroma python3 -m core.object_extractor --max-graphs 64 --verbose

	•	Health/Audit:

PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_selfcheck.py
PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_audit.py
PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_dedupe.py --dry-run
PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_dedupe.py       # Real-Run
PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_fix_compressed_links.py
PYTHONPATH=/opt/ai/oroma python3 tools/objectgraph_top_objects.py  # Hubs

	•	UI prüfen:

/objects

	•	Health-Badge + Top-Statistiken
	•	Fokus-Knoten & Ego-Netz (per focus_id).

SceneGraph & RAG
	•	siehe Abschnitte v3.7.x und v3.8-r1/r2.

⸻

⚠️ Bekannte Grenzen
	•	keine echte Multi-Intent-Planung (Thema v4.0).
	•	Replay nicht voll nach „Habits“ priorisiert, nur Heuristiken.
	•	MetaSnaps:
	•	experimentell (OROMA_ENABLE_METASNAP).
	•	SceneGraph-Builder:
	•	aktuell Fokus origin='vision/token'.
	•	ASR2:
	•	abhängig von Gerät / Audio-Stack, Latenz variiert.
	•	RAG:
	•	Demo-Daten minimal – echte Wissensbasis muss kuratiert werden.
	•	Vector-DB (Annoy/FAISS):
	•	wird nur genutzt, wenn externe DB korrekt initialisiert ist.
	•	SceneGraphs / ObjectGraphs:
	•	strukturell sehr mächtig,
	•	ontologischer Layer (Semantik, Typ-Hierarchien, Slots/Frames) noch im Aufbau (Roadmap 2026 / v3.9 / v4.0).

⸻

✅ Fazit

Dieses CHANGELOG deckt alle Versionen, Patches und realen Dateien deiner aktuellen ZIP
oroma_20251213_114645_with_db.zip plus den Live-Stand (Dezember 2025) ab – inklusive:
	•	Roter Faden + Mutations-Drift (v3.7)
	•	DreamWorker 3.1/3.3 mit Run-Lock, Self-Healing-Replay, sanftem Vergessen
	•	SceneGraph-Store/Builder + Selfcheck für Vision-Tokens
	•	ObjectGraph 1.5 mit eigener DB-Schicht (object_nodes/object_relations),
	•	Konsistenz-Audit,
	•	Dedupe + Fix für compressed_*,
	•	Top-Hub-Analyse,
	•	und /objects-Viewer v0.8 mit Ego-Netz & Health-Badge
	•	Snap/SnapPattern/SnapIndex-Optimierungen
	•	RAG-Benchmark-Pipeline (Ask-UI + bench_rag.py)
	•	DeviceHub-Sensoren & Vision-Episoden (cam_token)
	•	Backup-Skript mit schlanker Analyse-DB

Damit ist der aktuelle Stand von ORÓMA (Ende 2025) realitätsnah dokumentiert und als Grundlage für die Roadmap 2026
(Episoden 1.5, NMR 3.75, Audio-Teacher/Student, Cortex-Modes) sauber referenzierbar.

<a id="docs_changelog_md"></a>

## Quelle: `docs/changelog.md`

ORÓMA – CHANGELOG (v1.6 → v3.7)

Stand: 2025-09-29
Pfad: docs/changelog.md

Dies ist das vollständige, konsolidierte Changelog von v1.6 bis v3.7 – inkl. Datenbank-Schema, neuen Dateien, ENV, Routen, systemd-Units, Upgrade-Checklisten.

⸻

Inhaltsverzeichnis
	•	v3.7 – Empathie & Self-Listening (2025-09-29)
	•	v3.6 – Wissenschaftler (2025-09-28)
	•	v3.5patch2.2 – Forgetting & Kompression (2025-09-26)
	•	v3.5patch2.1 – Health Monitoring (2025-09-25)
	•	v3.5patch2 – Empathy & Coverage (2025-09-24)
	•	v3.5 – Forscher/Meister (2025-09-21)
	•	v3.0 – LLM-Fusion („Student/Gelehrter“) (2025-09-19)
	•	v2.30 – Agentisches Lernen & Explainability (2025)
	•	v2.20 – Spatio-Temporal + Diagnostics (2025 Roadmap)
	•	v2.11 – Final Release „Kind“ (2025-09-09)
	•	v2.00 – Struktur/Deployment (2025)
	•	v1.98 – Konsolidierung (2025)
	•	v1.62 – UI-Skelett (2025)
	•	v1.6 – Proof of Concept (Juli 2025)
	•	Upgrade-Hinweise
	•	Sicherheit & Betrieb
	•	Bekannte Grenzen / Ausblick
	•	Schnell-Checklisten

⸻

v3.7 – Empathie & Self-Listening (2025-09-29, Final)

Überblick

v3.7 erweitert die „Wissenschaftler“-Basis (v3.6) um soziale Resonanz + Intent-Schicht:
	•	Empathie-Layer: laufende Stimmungserfassung (empathy_snaps)
	•	Self-Listening: ASR hört sich selbst, erzeugt Empathie-Snaps & kleine Speech-Rewards
	•	Mangel-Ansprache: empathie-sensitiv, spricht bei Gaps gezielt
	•	Reward-Brücke: positive Stimmungswechsel → Rewards → Learning-Dashboard
	•	Curriculum empathie-gewichtet: schlechtere Stimmung → mehr Repeats
	•	Roter Faden: Thread/Intent-Layer mit Nudges bei Idle-Phasen
	•	Optional: Chess-UI (volle Regeln, 3 Partien/Tag Limit)

⸻

Neue / geänderte Dateien

Core (neu)
	•	core/asr_reflex.py – Self-Listening: Text → Empathie + Intents
	•	core/mangel_speak_hook.py – Mangel-Ansprache mit Empathie-Policy
	•	core/roter_faden.py – Intent-/Thread-Layer (start, advance, pause, nudge)
	•	tools/social_resonance_tick.py – Timer-Job: Empathie-Reward bei positivem Shift

Option (Chess)
	•	mini_programs/chess/board.py, chess_rules.py, chess_game.py, chess_ai.py
	•	ui/chess_ui.py, templates/chess.html

Core (erweitert)
	•	curriculum_hook.py – empathie-gewichtete Repeats
	•	snap.py, snaptoken.py, meta_snap.py, snappattern.py, snapchain.py – robustere Normierung, Fingerprints, Gap-Flags, Knowledge-Helper
	•	regelarchiv.py, mutation.py – Upserts, Mutations-Audit

UI (neu/erweitert)
	•	ui/asr_ui.py – Reflex-Trigger bei Textänderung
	•	ui/learning.py – berücksichtigt speech/empathy in Charts
	•	ui/chat_ui.py, templates/chat.html – Status-UX, Error-Toasts, Chat-Bubbles
	•	ui/static/style.css – neue Badge-Logik (Curriculum, Missions, Empathie, Health)

Systemd (neu)
	•	v3.7/systemd/oroma-social.service
	•	v3.7/systemd/oroma-social.timer

⸻

Datenbank
	•	Unverändert: nutzt rewards_log, empathy_snaps, coverage_log, setcalc_log, scicalc_results, curriculum_state, calculator_*
	•	Neu genutzt: Rewards mit Quellen "speech", "empathy", "curriculum"
	•	Roter Faden: curriculum_state.window.current_thread enthält Thread-JSON
	•	Chess: optional SnapChain-Log oder Rewards, kein neues Schema

⸻

ENV

ASR / Reflex

OROMA_ASR_MODEL=small
OROMA_ASR_REFLEX_ENABLED=true
OROMA_ASR_MIN_DELTA_MS=250
OROMA_ASR_EMPATHY_LOG_ENABLED=true
OROMA_ASR_SPEECH_REWARD=0.01

MangelSpeak / Empathie

OROMA_MANGEL_INTERVAL=120
OROMA_MANGEL_EMPATHY_ENABLE=true
OROMA_MANGEL_EMPATHY_THRESH=0.40
OROMA_MANGEL_EMPATHY_WINDOW_SEC=600

Timer / Social Resonance

OROMA_EMPATHY_WINDOW_SEC=600
OROMA_EMPATHY_MIN_DELTA=0.2
OROMA_EMPATHY_REWARD=0.02

Chess (optional)

OROMA_CHESS_DAILY=3
OROMA_CHESS_DEPTH_DEFAULT=2

⸻

API / Routen

ASR
	•	GET /asr – Seite
	•	POST /asr/api/start|stop
	•	GET /asr/api/status

Learning
	•	GET /learning – Dashboard inkl. Empathie-Kurve
	•	GET /learning/api/data – speech_mean, empathy_mean, letzter Snap
	•	GET /learning/api/history – Rewards + Empathie

Hooks
	•	core/curriculum_hook.curriculum_hook – alle 15 Ticks
	•	core/mangel_speak_hook.mangel_speak_hook – alle 60 Ticks

Chess (optional)
	•	GET /chess – Board
	•	POST /chess/api/new – neue Partie (Rate-Limit)
	•	POST /chess/api/move – Spielerzug (UCI)
	•	POST /chess/api/ai – KI-Zug
	•	POST /chess/api/resign – Aufgabe

⸻

Upgrade-Hinweise (v3.6 → v3.7)
	1.	Core/UI deployen (neue Dateien + Refactors).
	2.	DB-Schema prüfen:

python -m core.sql_manager --ensure

	3.	Timer aktivieren:

sudo systemctl enable --now oroma-social.timer

	4.	Hooks in agent_loop.py registrieren:

from core import curriculum_hook, mangel_speak_hook
register_hook(curriculum_hook.curriculum_hook)
register_hook(mangel_speak_hook.mangel_speak_hook)

	5.	ENV setzen (ASR, Empathie, Chess).
	6.	Smoke-Tests: /asr, /learning, /chess (falls aktiv).

⸻

Schnell-Checkliste v3.7
	•	ASR Reflex aktiv, Empathie-Snaps entstehen
	•	MangelSpeak triggert empathie-sensitiv
	•	Roter Faden → curriculum_state.window sichtbar
	•	Social Resonance Timer loggt Rewards
	•	Learning-UI zeigt speech/empathy-Kurve
	•	Chess: max. 3 Partien/Tag enforced

<a id="docs_changelog_v3_5patch1_md"></a>

## Quelle: `docs/changelog_v3_5patch1.md`

Perfekt 👍
Dann mache ich dir beide Changelogs nebeneinander, so dass du den Fortschritt klar siehst.

⸻

📑 ORÓMA – Changelog v3.5 Patch 1 & Patch 2

⸻

🧩 v3.5 Patch 1 (2025-09-23)

Fokus: Curriculum-Learning (Calculator), Transfer Engine, Self-Assessment

🚀 Neue Features
	•	Calculator Modul (Curriculum-Taschenrechner):
	•	SQL-Tabellen calculator_tasks & calculator_results.
	•	Insert-Funktionen in sql_manager.py.
	•	Core-Klasse Calculator (Aufgaben generieren + lösen).
	•	UI: calculator_ui.py + calculator.html.
	•	Automatische Rewards: richtig = +1.0, falsch = -0.5.
	•	Transfer Engine:
	•	transfer_snaps Tabelle + Insert-Funktion.
	•	Grundbaustein für Wissensübertragungen.
	•	Self-Assessment Engine:
	•	Selbsttest-Hooks in agent_loop.py.
	•	Erste Meta-Bewertungen von Episoden + Snaps.

🔧 Fixes / Änderungen
	•	sql_manager.py aufgeräumt (Schema erweitert + Insert-Funktionen).
	•	run_oroma.py integriert den neuen Calculator-Blueprint.
	•	UI erweitert um Selftest-Seite.
	•	VectorDB-Check stabilisiert (Fehlerbehandlung beim Sync).

📊 Ergebnis
	•	System kann jetzt aktive Lernaufgaben generieren.
	•	Rewards & Fehler werden messbar in DB gespeichert.
	•	Grundlage geschaffen für Curriculum-basierte Lernkurve.

⸻

🧩 v3.5 Patch 2 (in Arbeit, 2025-09-xx)

Fokus: Stabilisierung, Rewards-Integration, Monitoring

🚀 Neue Features
	•	Calculator ↔ Rewards-Log Integration:
	•	Ergebnisse aus calculator_results werden automatisch auch in rewards_log gespiegelt.
	•	Rewards erscheinen im Learning-Dashboard (Qualitätstrend).
	•	Selftest Dashboard Upgrade:
	•	Erweiterte Ansicht: Letzte Selftests + Scores direkt im UI.
	•	Exportierbar als CSV.
	•	AgentLoop Stabilisierung:
	•	Hook-Fehler werden abgefangen (kein Abbruch mehr).
	•	Logging erweitert (mit Quelle: Snap, Calculator, Transfer).

🔧 Fixes / Änderungen
	•	sql_manager.py doppelte Blöcke entfernt → sauberes, einheitliches Schema.
	•	ensure_schema() prüft jetzt auch auf alle Patch-Tabellen.
	•	calculator_ui.py erweitert → Benutzer kann eigene Eingaben machen (interaktive Tasks).
	•	oroma_graph.js verbessert (Tooltips + Score-Anzeige).

📊 Erwartetes Ergebnis
	•	Rewards fließen jetzt einheitlich ins Lernsystem (SnapChains + Calculator + Transfer).
	•	Lernkurven werden repräsentativer, da Selftests mitgezählt werden.
	•	UI wird für den Benutzer interaktiver und robuster.

⸻

👉 Damit hast du jetzt:
	•	docs/changelog_v3_5patch1.md (fertig, Stand gestern)
	•	docs/changelog_v3_5patch2.md (Startentwurf, in Arbeit)

⸻

Willst du, dass ich dir die beiden Dateien direkt als Markdown-Dateien generiere (docs/changelog_v3_5patch1.md und docs/changelog_v3_5patch2.md), so dass du sie gleich ins Projekt legen kannst?

<a id="docs_changelog_v3_5patch2_md"></a>


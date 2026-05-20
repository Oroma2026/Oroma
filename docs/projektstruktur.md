<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__projektstruktur.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

# ORÓMA – Projektstruktur (konsolidiert)

Stand: 2025-12-25


Autoritative Projektstruktur / Datei-Übersichten (zusammengeführt).

## Quellen (konsolidiert)

- `docs/projektstruktur.md`

- `docs/history_projektstruktur_alte.md`

---

<a id="docs_projektstruktur_md"></a>

## Quelle: `docs/projektstruktur.md`

**Originaltitel:** 📂 ORÓMA v3.7.x + v3.8-r2 – Projektstruktur

Pfadbasis Projekt: `/opt/ai/oroma/`  
Pfad dieses Dokuments: `docs/projektstruktur.md`  
Stand: 2025-12-07  
Gesamt: ~2900 Einträge (inkl. `__pycache__/` & Logs, davon ca. 300 Kern-Dateien)

Diese Struktur beschreibt den aktuellen Stand deiner ZIP (v3.7.x + v3.8-r2):

- **Verhaltens-Stand**: v3.7.x  
  – Roter Faden, Empathie, Mutations-Drift, Curriculum V2, Episoden, Self-Listening.
- **Speicher-/Infra-Stand**: v3.8-r2  
  – Snap v1.1, SnapIndex, RAG-Stack, WAL, SceneGraphs, DeviceHub + Sensor-Hub.

---

## 1) Basis / Wurzel

Im Projekt-Root (`/opt/ai/oroma/`):

- `run_oroma.py` ✏️  
  → Startskript für Flask-UI + AgentLoop (Day-Mode), registriert Blueprints, initialisiert Core (sql_manager.ensure_schema(), DeviceHub-Lazy-Init etc.).
- `.env` / `.env.systemd` ✏️  
  → zentrale ENV-Konfiguration (DB-Pfade, Ports, Tokens, Flags).
- `requirements.txt` ✏️  
  → Haupt-Abhängigkeiten (Flask, SQLite, NumPy, sounddevice, picamera2/OpenCV, usw.).
- `requirements-headless.txt` 🆕  
  → Variante für Headless-Install (kein Qt/Wayland/X11, optimiert für Pi).
- `requirements-optional.txt`  
  → optionale Extras (z. B. Hailo/DeGirum, Zusatz-Backends).
- `requirements-dev.txt`  
  → Test- & Dev-Abhängigkeiten (pytest, coverage, etc.).
- `pytest.ini` ✏️  
  → pytest-Konfiguration (Marker, Pfade, Standard-Options).

Deployment / System:

- `setup_os.sh` ✏️  
  → OS-Setup (Pakete installieren, User/Groups, Basisverzeichnisse).
- `setup_systemwide_oroma.sh` ✏️  
  → Systemweite Installation als Dienst (systemd-Units installieren/aktivieren).
- `deploy_all.sh`, `deploy_from_zip.sh`, `rollback_deploy.sh` ✏️  
  → Deployment-Flow aus ZIP, inkl. Rollback.
- `clean_cache_python.sh`  
  → Bereinigt `__pycache__` und `*.pyc`.

Hilfs-Skripte außerhalb der Baumstruktur (aber relevant):

- `backup_oroma.sh` / `backup_oroma_with_db.sh` (typisch unter `/opt/ai/`)  
  → Backups mit Sampling/Truncation-Regel (max. 1000 Einträge pro großer Tabelle/Log).

---

## 2) Core – Kognition, Gedächtnis & Lernen

### 2.1 Grundbausteine (Snaps, Chains, Patterns, Tokens)

- `core/snap.py` ✏️  
  → **Snap v1.1**: numerische Features, Fingerprint, L2-Norm-Cache, Selftests.
- `core/snapchain.py` ✏️  
  → Sequenzen von Snaps (Spiele, Vision, Audio, Sensoren, Dialoge, Episoden).
- `core/snappattern.py` ✏️  
  → Pattern/Cluster von Snaps (Centroids, Gap-Detection, Ähnlichkeit, L2-Norm-Metadaten).
- `core/snaptoken.py` ✏️  
  → SnapToken v3.7: stabile, modellagnostische Tokenisierung (Text/Meta, deterministische Fingerprints).

### Calculator / Transfer / Crossmodal (v3.7.3+)

- `core/calc_solver.py`  
  Mini-Solver für Math-Tasks (arith/seq/fill/cmp/fractions/quadratic …).  
  Ziel: ORÓMA löst Aufgaben selbst (nicht teacher forced), steuerbar via ENV.

- `core/calc_to_snapchain.py`  
  Persistiert Calculator-Ergebnisse als SnapChains (`origin='calc/result'`) inkl. Vektor (Default 84D)  
  und erzeugt/aktualisiert MetaSnaps (`meta_snaps.label LIKE 'calc:%'`).

- `core/calc_vision_linker.py`  
  Crossmodal-Linker (Calculator ↔ Vision) auf Basis Zeitfenster + Cosine-Score.  
  Persistiert Links als SnapChains (`origin='link/calc_vision'`) mit IDs + Score + dt.

### 2.2 SQL / Speicher / Indizes

- `core/sql_manager.py` ✏️ (v3.8-r2)  
  → Single Source of Truth für SQLite:
  - Tabellen u. a.:  
    `snapchains`, `meta_snaps`, `scenegraphs`, `snap_index`,  
    `metrics`, `coverage_log`, `empathy_snaps`,  
    `calculator_tasks`, `calculator_results`, `setcalc_log`,  
    `kpi_snapshots`,  
    `episodes`, `episode_events`, `episodic_metrics`,  
    `curriculum_state`, `model_registry`, `rag_*`, …
  - `busy_timeout`, optionaler WAL-Modus (ENV `OROMA_DB_WAL=1`).
  - Helper: Insert/Fetch für SnapChains, SnapIndex, Metrics, CamTokens, SceneGraphs, Episoden, RAG, etc.

- `core/vector_migration.py`  
  → Anbindung/Bridge zu externer Vector-DB (Annoy/FAISS, je nach Setup).

- `core/langzeitgedaechtnis.py` ✏️  
  → Langzeit-Gedächtnis 2.0 (Recall/Promotion, optional vektorbasierte Suche).

### 2.3 Lernen / Belohnung / Neugier / Vorhersage

- `core/reward.py`  
  → Reward-Signale (Spiele, Curriculum, Missions), Logging in `metrics`.
- `core/curiosity.py`  
  → Curiosity-Logik, exploration vs. exploitation.
- `core/predictor.py`  
  → einfache Vorhersagemodelle (z. B. Zustand → Reward/Gewinn).
- `core/auto_tuner.py` ✏️  
  → Auto-Tuning von Hyperparametern (ε, Fade-Rates, Thresholds) basierend auf Metriken.
- `core/quota.py`  
  → Quoten-/Budget-Management (z. B. Request-Limits, Tokenbudgets).
- `core/options.py`  
  → zentrale Options-/Feature-Flags (Feature-Toggles für Module/Experimente).

### 2.4 Day-/Dream-Zyklus & Replay

- `core/agent_loop.py` 🧠  
  → Haupt-AgentLoop im Day-Mode (Spiele, Hooks, Empathie, Curriculum, ASR-Reflex, Episoden).
- `core/dream_worker.py` ✏️  
  → **DreamWorker 3.1**:
  - Run-Lock (Datei-Lock unter `data/state/`),  
  - Replay + Mutation,  
  - Gewichtetes Vergessen + Kompression → `meta_snaps`,  
  - ExportGate & Missions/Research-Hooks,  
  - Auto-Tuning von Fade/Thresholds.
- `core/replay_system.py` ✏️  
  → Replay 3.0 (lesen, filtern, abspielen, Token-freies Re-Rendering).
- `core/replay_manager.py` ✏️  
  → Orchestrierung verschiedener Replay-Pfade (Games, Vision, Audio, Episoden, Missions).
- `core/circadian_controller.py`  
  → Automaton Day↔Dream (Lichtsensor, Zeitfenster, Policy).

### 2.5 Meta-Ebene / Regeln / Drift / Intent

- `core/meta_snap.py` 🆕  
  → MetaSnaps: verdichtete Repräsentationen (z. B. komprimierte SnapChains, Sammlungen).
- `core/regelarchiv.py` ✏️  
  → Regelarchiv (RuleSets, Aktiv/Passiv, Pruning schwacher Regeln, Export).
- `core/mutation.py`  
  → Mutationen, Variation, Mutations-Drift (sanfte Veränderung statt Hard-Resets).
- `core/roter_faden.py` 🧠  
  → Intent-/Thread-Layer („Roter Faden“):  
    Threads mit Titel/Ziel/Schritten, Nudges, Auto-Gaps, Logging-Kontext.  
    (Siehe `docs/core_roterfaden.md`.)
- `core/policy_engine.py`, `core/universal_policy.py`  
  → Policy-Logik (Policy-Rules, universelle Politik über Spiele/Tasks hinweg).
- `core/hypotheses.py`  
  → Hypothesen-DB (Forschung, Experimente, Research-UI).
- `core/transfer_engine.py`  
  → TransferSnaps / Transfer-Lernen zwischen Tasks/Origins.

### 2.6 Empathie, Episoden & Explainability

- `core/episodic.py`  
  → episodisches Gedächtnis (API zum Lesen/Abfragen von Episoden).
- `core/episodic_writer.py` 🆕  
  → EpisodeWriter für Audio & Vision:
  - schreibt `episodes` + `episode_events` (Audio-Sequenzen, `cam_token`-Events etc.).
- `core/explain.py` ✏️  
  → Explainability 2.0 (`why_decision`, Pfad-Rekonstruktion, UI-/API-Integration).
- `core/asr_reflex.py`  
  → ASR-Reflex / Self-Listening (Sprache → Intents, Mangel-Speak, Roter Faden).
- `core/hooks_patch2.py` ✏️  
  → Empathy- & Coverage-Hooks:  
    `empathy_snaps`, `coverage_log`, Integration in Learning-Dashboards.
- `core/gaps.py`  
  → Gaps-Analyse (Novelty/Uncertainty/Fehler); Wird u. a. vom Roten Faden (`note_gap`/Auto-Gaps) genutzt.

### 2.7 Vision / SceneGraphs / Sensorik

- `core/device_hub.py` 🆕  
  → Zentrale Geräte-Verwaltung:
  - Kamera-Backend (PiCamera2/OpenCV/Dummy) inkl. Health-Status,  
  - Light-Level (0..100, Hysterese, Audit-Log),  
  - Audio (Mic, Ringbuffer, WAV-Record/Playback),  
  - generische `SensorChannel`-Polling-Loop,  
  - Sessions + Audit-Logging.
- `core/sensor_channel.py` 🆕  
  → Basisklasse für SensorChannels:
  - `read_raw()`, `build_snapchain_data()`,  
  - Felder: `interval_sec`, `origin`, `namespace` etc.  
  - Wird von IR-Sensor-Wrappern verwendet.

- `core/scenegraph_store.py` 🧩  
  → SceneGraph-Store (Tabelle `scenegraphs`, Build/Fetch/Query).

- `core/scenegraph_builder.py` ✏️  
  → Builder für:
  - MetaSnaps aus Vision-Token-Chains (`origin='vision/token'`),  
  - SceneGraphs direkt aus Vision-Tokens.

- `core/vision_arbiter.py`, `core/vision_scene_infer_hook.py`  
  → Routing/Heuristik für Vision-Pfade, Integration in Hooks/AgentLoop.

- `core/cam_token_train.py`  
  → Training/Tests für Vision-Tokens (CamToken-Stream, Experiment-Setups).

- `core/camera_hub.py`  
  → ältere Kamera-Abstraktion (historisch), heute weitgehend von `device_hub` ergänzt/abgelöst.

### 2.8 RAG / LLM / Text

- `core/rag_bridge.py` ✏️  
  → RAG-Stack (SQLite + FTS5, BM25-Suche, optional Fusion-Rerank über `core.fusion`).
- `core/book_import.py`  
  → Buch/Text → `knowledge.db` (Chunking, FTS5-Index).
- `core/llm_runtime.py`  
  → Anbindung an lokale LLMs (z. B. llama.cpp) bzw. optionale externe APIs.
- `core/fusion.py`  
  → Embedding-/Fusion-Engine (Encode, Similarity, Rerank).

### 2.9 Spiele / Adapter / Policies / SSL-Experimente

- `core/ttt_adapter.py`, `core/chess_adapter.py`, `core/calculator_adapter.py`, `core/setcalc_adapter.py`, …  
  → Adapter-Schicht für Games & Tasks (Board/State ↔ SnapChain/Policy).
- `core/pong_arena.py`, `core/snake_trainer.py`, `core/oroma_vs_oroma_ttt.py`  
  → Trainings-/Simulationslogik (Policies für einzelne Spiele).
- `core/ssl_contrastive.py`  
  → experimentelle Self-Supervised-Module (Contrastive Learning).

---

## 3) Wrappers – Hardware / Modelle / Sensoren

Unter `wrappers/`:

- `wrappers/__init__.py`
- `wrappers/audio_wrapper.py`  
  → Audio-Frontend (Mic, Streams) oberhalb von `device_hub`.
- `wrappers/vision_wrapper.py`  
  → Video-/MJPEG-Wrapper (Flask-UI, `/video/stream`, `/video/healthz`, Snapshots).
- `wrappers/picar_wrapper.py`  
  → Ansteuerung des PiCar (Motoren, Safety, Offsets).
- `wrappers/oroma_wrapper.py`  
  → zentrale HL-API für externe Aufrufer (CLI/Tools).
- `wrappers/text_wrapper.py`  
  → Text-IO, Vor-/Nachbereitung für LLM/RAG.
- `wrappers/tts_wrapper.py`  
  → Text-to-Speech (je nach lokalem Setup).
- `wrappers/hailo_wrapper.py`, `wrappers/degirum_wrapper.py`  
  → NPU-Anbindung (Hailo, DeGirum).
- `wrappers/dynamic_wrapper.py`  
  → generischer Wrapper, der zur Laufzeit das passende Backend wählt.
- `wrappers/sensor_ir_front.py` 🆕  
  → Beispiel-Wrapper für einen Front-IR-Abstandssensor:  
    registriert `sensor/ir/front` als `SensorChannel` im DeviceHub (SnapChains mit `origin='sensor/ir/front'`).

---

## 4) Exports

Unter `exports/`:

- `exports/__init__.py`
- `exports/model_export.py`
- `exports/model_import.py`
- `exports/hailo_export.py`
- `exports/degirum_export.py`

→ Export/Import von Modellen und Snap-Daten, inkl. Hailo-/DeGirum-spezifischen Formaten.

---

## 5) UI (Flask)

### 5.1 Python-Module (Blueprints)

In `ui/` (Auszug):

- `ui/__init__.py`
- `ui/flask_ui.py`  
  → Registriert Blueprints, zentrale App-Erstellung.

**Kern-Seiten:**

- `ui/replay_ui.py` ✏️  
  → Replay-Ansicht (Chains, Filter, Export).
- `ui/learning.py` ✏️  
  → Learning Dashboard (Rewards, Coverage, Curiosity, KPIs, Kpi-Snapshots).
- `ui/episodic_ui.py` ✏️  
  → Episoden-Browser (`episodes`, `episode_events`).
- `ui/ask_ui.py` ✏️  
  → Ask-/RAG-UI (Fragen an `knowledge.db`).
- `ui/asr_ui.py`, `ui/asr2_ui.py`  
  → ASR-/Live-Sprach-UI, Self-Listening-Ansichten.
- `ui/empathy_ui.py` 🧠  
  → Empathie-Snaps, Stimmung, Mangel-Speak-Events.
- `ui/coverage_ui.py`  
  → Coverage/Gaps-Ansicht (Coverage-Log).
- `ui/selftest_ui.py`  
  → Selbsttests (Lern-Selbstdiagnose, Mini-Checks).
- `ui/models_ui.py`  
  → Model Registry (Model-Liste, Aktivierung).
- `ui/video_ui.py`  
  → Videostream, Healthz für Kamera/Backend.
- `ui/why_ui.py` ✏️  
  → Explainability 2.0 (Decision-Paths, „Warum habe ich X getan?“).

**Weitere UIs (Auszug):**

- `ui/games_ui.py`, `ui/tictactoe_ui.py`, `ui/snake_ui.py`, `ui/pong_ui.py`,  
  `ui/memorymaze_ui.py`, `ui/hideseek_ui.py`, `ui/ctf_ui.py`, `ui/flappy_ui.py`,  
  `ui/classic_memory_game_ui.py`, `ui/chess_ui.py`  
  → Spiele-Oberflächen.
- `ui/calculator_ui.py`, `ui/setcalc_ui.py`  
  → Mathe-/SetCalc-Trainingsoberflächen.
- `ui/scenegraph_ui.py`  
  → SceneGraph-Ansicht (Nodes/Edges, MetaSnaps).
- `ui/health_ui.py`  
  → System-Health (Logs, Services, DB-Checks).
- `ui/import_ui.py`, `ui/export_ui.py`  
  → Import/Export-Steuerung.
- `ui/stats_ui.py`, `ui/bundle_ui.py`, `ui/selfrec_ui.py`, `ui/nmr_ui.py`, …  
  → Statistik-, Bundle-, Self-Recording- und NMR-bezogene Ansichten (je nach Reifestand).

### 5.2 Templates & Static

- `ui/templates/base.html`  
  → Grundlayout.
- `ui/templates/*.html`  
  → Seiten für Replay, Learning, Ask, Empathy, Coverage, Selftest, Spiele, Video, SceneGraphs, usw.
- `ui/static/style.css`
- `ui/static/scripts.js`
- `ui/static/chart.min.js` (+ `.md`)
- Spiel-spezifische JS: `ctf.js`, `flappy.js`, `oroma_graph.js`, etc.

---

## 6) Mini-Programme (Games)

Unter `mini_programs/`:

- `mini_programs/__init__.py`
- `mini_programs/tictactoe.py`
- `mini_programs/connect4.py`
- `mini_programs/snake.py`
- `mini_programs/pong.py`
- `mini_programs/flappybird.py`
- `mini_programs/memory_maze2033.py`
- `mini_programs/capture_the_flag.py`
- `mini_programs/hide_seek.py`
- `mini_programs/oroma_vs_oroma.py` ✏️  
  → Selbstspiel-Modus (Policy-Tests, Exploration vs. Policy).

Alle Mini-Programme schreiben SnapChains in die DB (z. B. `origin='game:snake'`) und dienen als kontrollierte Lernumgebung.

---

## 7) Daten & Modelle

Unter `data/`:

- `data/oroma.db`  
  → Haupt-DB (SnapChains, MetaSnaps, SceneGraphs, Metrics, Coverage/Empathie, Curriculum, Episodes, usw.).
- `data/knowledge.db`  
  → Wissensbasis für RAG (FTS5).
- `data/state/`  
  → Locks, Laufzeitstatus, interne Zustände (z. B. Dream-Lock, Checkpoints).
- `data/snapchains/` (falls vorhanden)  
  → JSON-Exports / Legacy-Files.
- `data/backups/` (optional)  
  → DB-Backups, Snap-Exports.

Unter `models/`:

- `models/llm/` (GGUF o. ä.)  
- `models/audio/` (ASR-Modelle wie Whisper)  
- `models/vision/` (CNNs, Hailo/DeGirum-Modelle, Experimente)

**Wichtig:**  
Deine Backups folgen inzwischen der Regel:  
große Tabellen/Logs werden bei Exports/Backups auf **max. 1000 Einträge** begrenzt (Sampling/Truncation-Regel), siehe `backup_oroma_with_db.sh`.

---

## 8) Deployment (systemd & Cron)

### 8.1 systemd

Unter `systemd/`:

- `oroma.service`  
  → ORÓMA-Hauptdienst (Flask-UI + AgentLoop).
- `oroma-dream.service` + `oroma-dream.timer`  
  → DreamWorker (Night-Run).
- `oroma-archive.service` + `.timer`  
  → monatliche Archive/Exports.
- `oroma-exportgate.service` + `.timer`  
  → ExportGate-Läufe.
- `oroma-replay.service` + `.timer`  
  → automatisierte Replays.
- `oroma-selftest.service` + `.timer`  
  → Selbsttests in Intervallen.
- `oroma-health.service` + `.timer`  
  → Health-/Watchdog-Checks.
- ggf. weitere timer/Services für Social-Resonance-Ticks, Learning-Kurven etc.
  (z. B. `oroma-social.timer` je nach Setup).

### 8.2 Cron

- `cron/oroma.cron`  
  → Alternative/Ergänzung zu systemd-Timern (Legacy/Kompatibilität).

---

## 9) Tests

Unter `tests/` (Auszug):

- `tests/conftest.py`
- `tests/test_meta_snap.py` 🆕
- `tests/test_auto_tuner.py` ✏️
- `tests/test_research_ui.py` 🆕
- `tests/test_explain_v2.py` 🆕
- `tests/test_oroma_wrapper.py`
- `tests/test_flappy_ui.py`
- `tests/test_ctf_ui.py`
- `tests/test_scenegraph_selfcheck.py` (falls vorhanden)
- `tests/test_sim_learning.py`
- weitere Game-/UI-/Core-Tests.

Shell-Tests / Simulationsskripte:

- `tests/sim_learntest.sh`
- diverse Mini-Benchmarks/Regressionstests.

---

## 10) Tools

Wichtige Tools unter `tools/` (Auszug):

- `tools/fulltest.py`  
  → großer Gesamt-Selbsttest.
- `tools/ui_selftest.py`, `tools/selftest_ui.py`  
  → UI-Funktionstests.
- `tools/sim_learn.py` + `tools/sim_learn_test.sh` ✏️  
  → Lernsimulationen (Langzeit-Kurven, Benchmarks).
- `tools/monthly_archive.sh` ✏️  
  → Monatsarchive (Exports, Log-Rotation).
- `tools/scenegraph_selfcheck.py` 🆕  
  → Health-Check für SceneGraphs/MetaSnaps (Vision).
- `tools/bench_rag.py` 🆕  
  → RAG-Benchmark (hit@k, nDCG, Latenz).
- `tools/rag_import_sample.py` 🆕  
  → Demo-Wissensbasis in `knowledge.db` importieren.

Weitere nützliche Helfer:

- `tools/devicehub_selfcheck.py`  
  → Checks für DeviceHub (Kamera/Audio/Sensoren).
- `tools/db_learning_curve.py`  
  → extrahiert Lernkurven aus DB (KPIs vs. Zeit).
- `tools/kpi_harness.py`  
  → KPI-Erhebung/Szenario-Runner.
- `tools/mark_compressed.py`  
  → Markiert/prüft komprimierte Chains/MetaSnaps.
- `tools/migrate_oroma_db.py`  
  → DB-Migrationen (Schema-Anpassungen).
- `tools/oroma-db-check.py`  
  → DB-Konsistenz-Check.
- `tools/replay_auto.py`  
  → automatisierte Replay-Läufe.
- `tools/social_resonance_tick.py`  
  → Social-Resonance / soziale Ticks.
- `tools/ttt_eval.py`  
  → TicTacToe-Policy-Benchmarks.

---

## 11) Docs

Zentrale Dokumentation unter `docs/`:

- `docs/projektstruktur.md` ✏️  
  → dieses Dokument – Projektstruktur, Dateien & Rollen.
- `docs/manifest_oroma.md` ✏️  
  → strukturierte Auflistung wichtiger Module & Versionen.
- `docs/changelog_full.md` ✏️  
  → vollständiges Änderungsprotokoll (1.6 → 3.8-r2).
- `docs/changelog.md`  
  → Kurzfassung der letzten Änderungen.
- `docs/roadmap.md`  
  → Roadmap (3.7.x → 3.75/4.0).
- `docs/dream_cycle.md`  
  → detaillierte Beschreibung des Day/Dream-Zyklus.
- `docs/core_roterfaden.md`  
  → Architektur des „Roten Fadens“ (Intent-Layer, v3.7.2-r1).
- `docs/abhaengigkeiten.md`  
  → System-/Python-Abhängigkeiten, Install-Hinweise.
- `docs/administrator-handbuch.md`  
  → Betrieb, Logs, Backups, systemd-Steuerung.
- `docs/konzeption_architektur.md` ✏️  
  → aktuelle Architektur-Zusammenfassung (v3.7.x + v3.8-r2) – dein „Finales README“.
- diverse `docs/Konzeption_Architektur_v3.5*.md`, `v3.6`, `v3.7.*`, `v3.8`, `v3.9`  
  → historische/versionsspezifische Konzeptstände.
- `docs/oroma_reifestufen.md`, `docs/Vergleich_Markt-KI*.md`  
  → Einordnung gegenüber Markt-KIs, Reifestufen.

Ältere Projektstruktur-/Roadmap-Varianten sind z. T. unter `docs/` (Prefix `history_`) abgelegt.

---

## 12) Logs & Uploads

- `log/` oder `logs/`  
  → diverse Logfiles (DreamWorker, Services, Coverage, DeviceHub-Audit, KPIs).
- `uploads/`  
  → Upload-Verzeichnis für UI (z. B. Bücher, Exports, Dateien).

---

✅ **Zusammenfassung (aktualisierte Projektstruktur)**

- Die alte v3.5-Projektstruktur ist jetzt auf deinen realen Stand **v3.7.x + v3.8-r2** gehoben.
- Explizit berücksichtigt sind u. a.:
  - `core/device_hub.py` + `core/sensor_channel.py` + `wrappers/sensor_ir_front.py` (generische Sensor-Integration, IR-Sensor),
  - `core/episodic_writer.py` + Vision-/Audio-Episoden (`episodes`, `episode_events`),
  - SceneGraph-Store/Builder + `tools/scenegraph_selfcheck.py`,
  - SnapIndex & RAG-Stack (inkl. `tools/bench_rag.py`, `tools/rag_import_sample.py`),
  - der Intent-/Thread-Layer `core/roter_faden.py` mit eigener Doku `docs/core_roterfaden.md`.

- Dokumente sind so ausgerichtet, dass:
  - `docs/konzeption_architektur.md` als **Finales README/Architekturdoc** dient,
  - `docs/projektstruktur.md` (dieses Dokument) die technische **Datei- und Modulübersicht** bildet.

Damit ist die Projektstruktur von ORÓMA Ende 2025 sauber dokumentiert und direkt mit deinem aktuellen Code/DB-Stand konsistent.

<a id="docs_history_projektstruktur_alte_md"></a>

## Quelle: `docs/history_projektstruktur_alte.md`

**Originaltitel:** -*- coding: utf-8 -*-

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/device_hub.py
# Projekt:   ORÓMA
# Modul:     DeviceHub (Kamera + Light + Audio + Sessions) inkl. Audit-Logging
# Version:   v3.7 (Audit & Sessions)
# Stand:     2025-10-03
#
# Zweck / Rolle
# ─────────────
#  Zentrale, threadsichere Geräteverwaltung:
#    • Kamera (PiCamera2 oder OpenCV; exakt **eine** Instanz systemweit)
#    • Light aus Kamerabild (Skala 0..100)
#    • Audio (Input/Output) mit Ringpuffer, RMS-Level, WAV-Export/Playback
#    • Client-Sessions (wer nutzt was?) → Start/Stop wird protokolliert
#    • MJPEG-Generator & JPEG-Snapshot
#
# Highlights (dieser Build)
# ─────────────────────────
#  • Audit-Logger (JSON Lines, rotierend):
#       - Pfad: OROMA_HUB_AUDIT_PATH       (Default: /opt/ai/oroma/log/devicehub_audit.log)
#       - Max:  OROMA_HUB_AUDIT_MAX_BYTES  (Default: 1_048_576 = 1 MiB)
#       - Backups: OROMA_HUB_AUDIT_BACKUPS (Default: 5)
#       - Felder je Event: ts, kind, action, backend/device, details...
#  • Geringe Spam-Gefahr durch Drosselung (Snapshots/MJPEG, Light optional)
#  • Sessions-API: open_session(client, kind) → session_id, close_session(id)
#  • Ausführliche Header-Kommentare & ENV-Doku
#
# Relevante ENV-Variablen
# ───────────────────────
#  Kamera/Light:
#    OROMA_VISION_BACKEND=picamera2|opencv|dummy      (Default: picamera2)
#    OROMA_VISION_DEVICE=0
#    OROMA_VISION_W=640
#    OROMA_VISION_H=360
#    OROMA_VISION_FPS=30
#    OROMA_VISION_ROTATE=0|90|180|270              (Default: 0; 180 = Kopfstand-Fix)
#    OROMA_LIGHT_SOURCE=camera|dummy|off              (Default: camera)
#    OROMA_LIGHT_CAMERA_INTERVAL=300                  (Sek., Mess-Cache)
#    OROMA_LIGHT_MIN=0 / OROMA_LIGHT_MAX=100
#    OROMA_LIGHT_AUDIT_MODE=changes|all|off           (Default: changes)
#
#  Audio:
#    OROMA_AUDIO_ENABLE=true|false                    (Default: true)
#    OROMA_AUDIO_INPUT_NAME=Jabra Evolve 75           (Substring-Match)
#    OROMA_AUDIO_OUTPUT_NAME=Jabra Evolve 75
#    OROMA_AUDIO_SR=16000
#    OROMA_AUDIO_CH=1
#    OROMA_AUDIO_BLOCK_MS=20
#    OROMA_AUDIO_RING_SEC=10
#    OROMA_AUDIO_LEVEL_INTERVAL=0.15
#
#  Audit-Logging:
#    OROMA_HUB_AUDIT_PATH=/var/log/oroma/devicehub_audit.log
#    OROMA_HUB_AUDIT_MAX_BYTES=1048576
#    OROMA_HUB_AUDIT_BACKUPS=5
#    OROMA_HUB_AUDIT_ENABLE=true|false                (Default: true)
#    OROMA_HUB_AUDIT_SNAPSHOT_THROTTLE=3.0            (Sek., Snapshot/MJPEG)
#
# Kompatibilität
# ──────────────
#  • Ersetzt ältere device_hub/camera_hub Varianten; bestehende Aufrufer
#    (z. B. Video-UI, VisionWrapper) können mjpeg_generator()/get_latest_jpeg()
#    weiterverwenden.
#  • Audio bleibt optional (sounddevice nicht zwingend).
#
# Sicherheit / Stabilität
# ───────────────────────
#  • Singleton, idempotentes Start/Stop
#  • Graceful Shutdown, robuste Fallbacks (DummyCam, Audio passiv)
#  • Logging + Audit ohne Absturzrisiko (best-effort)
#
# Lizenz: MIT (ORÓMA-Projekt)
# =============================================================================

from __future__ import annotations

import os
import io
import json
import time
import uuid
import wave
import errno
import threading
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
from typing import Generator, List, Optional, Tuple, Dict, Any

# --- Optionales Audio-Backend (PortAudio via sounddevice) --------------------
try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover
    sd = None  # type: ignore

# --- Kamera-Dependencies -----------------------------------------------------
try:
    import cv2  # optional, nur für OpenCV-Backend / JPEG-Encode
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    from picamera2 import Picamera2  # type: ignore
except Exception:  # pragma: no cover
    Picamera2 = None  # type: ignore

import numpy as np  # type: ignore

# =============================================================================
# Logging (Console) + Audit (JSON Lines)
# =============================================================================

LOG = logging.getLogger("oroma.device_hub")
if not LOG.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [DeviceHub] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.INFO)

_AUDIT_ENABLE = os.environ.get("OROMA_HUB_AUDIT_ENABLE", "true").strip().lower() in ("1", "true", "yes", "on")
_AUDIT_PATH = os.environ.get("OROMA_HUB_AUDIT_PATH", "/opt/ai/oroma/log/devicehub_audit.log")
_AUDIT_MAX = int(os.environ.get("OROMA_HUB_AUDIT_MAX_BYTES", "1048576"))
_AUDIT_BK = int(os.environ.get("OROMA_HUB_AUDIT_BACKUPS", "5"))
_AUDIT_SNAP_THR = float(os.environ.get("OROMA_HUB_AUDIT_SNAPSHOT_THROTTLE", "3.0"))  # Sek.

def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        return
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def _setup_audit_logger() -> logging.Logger:
    lg = logging.getLogger("oroma.device_hub.audit")
    if lg.handlers:
        return lg
    if not _AUDIT_ENABLE:
        # Dummy-Logger (kein Handler)
        lg.propagate = False
        lg.disabled = True
        return lg
    try:
        _ensure_parent_dir(_AUDIT_PATH)
        rh = RotatingFileHandler(_AUDIT_PATH, maxBytes=_AUDIT_MAX, backupCount=_AUDIT_BK, encoding="utf-8")
        rh.setLevel(logging.INFO)
        # rohe JSON-Zeilen (kein Formatter → nur msg)
        fmt = logging.Formatter("%(message)s")
        rh.setFormatter(fmt)
        lg.addHandler(rh)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        LOG.info("Audit-Logging aktiv: %s (max=%d, backups=%d)", _AUDIT_PATH, _AUDIT_MAX, _AUDIT_BK)
    except Exception as e:
        LOG.warning("Audit-Logger konnte nicht eingerichtet werden: %s", e)
        lg.disabled = True
    return lg

_AUDIT = _setup_audit_logger()

# einfache Drosselung für High-Frequency-Events
_audit_last: Dict[str, float] = {}
_audit_lock = threading.Lock()

def _audit(kind: str, action: str, **fields: Any) -> None:
    if not _AUDIT_ENABLE or _AUDIT.disabled:
        return
    try:
        evt = {"ts": time.time(), "kind": kind, "action": action}
        evt.update(fields)
        _AUDIT.info(json.dumps(evt, ensure_ascii=False))
    except Exception:
        # Audit darf nie hart fehlschlagen
        pass

def _audit_throttled(key: str, min_interval: float, kind: str, action: str, **fields: Any) -> None:
    if not _AUDIT_ENABLE or _AUDIT.disabled:
        return
    now = time.time()
    with _audit_lock:
        last = _audit_last.get(key, 0.0)
        if now - last < min_interval:
            return
        _audit_last[key] = now
    _audit(kind, action, **fields)

# =============================================================================
# Kamera-Backends
# =============================================================================
class _BaseCam:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read(self) -> Optional[np.ndarray]: ...
    def running(self) -> bool: return False
    def id_string(self) -> str: return "unknown"

class _DummyCam(_BaseCam):
    def __init__(self, w: int, h: int) -> None:
        self._w, self._h = w, h
        self._run = False

    def start(self) -> None:
        self._run = True
        LOG.warning("DummyCam aktiv – es werden Platzhalter-Frames geliefert.")
        _audit("camera", "start", backend="dummy", device=None, size=[self._w, self._h])

    def stop(self) -> None:
        self._run = False
        _audit("camera", "stop", backend="dummy")

    def read(self) -> Optional[np.ndarray]:
        if not self._run:
            return None
        img = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        t = int(time.time() % 255)
        img[:] = (t, t, t)
        return img

    def running(self) -> bool:
        return self._run

    def id_string(self) -> str:
        return f"dummy({self._w}x{self._h})"

class _PiCamera2Cam(_BaseCam):
    def __init__(self, w: int, h: int, fps: int) -> None:
        self._w, self._h, self._fps = w, h, fps
        self._run = False
        self._cam = None

    def start(self) -> None:
        if Picamera2 is None:
            raise RuntimeError("PiCamera2-Modul nicht verfügbar")
        if self._run:
            return
        self._cam = Picamera2()
        cfg = self._cam.create_preview_configuration(main={"size": (self._w, self._h), "format": "BGR888"})
        self._cam.configure(cfg)
        self._cam.start()
        self._run = True
        LOG.info("PiCamera2 gestartet (%dx%d @ ~%dfps)", self._w, self._h, self._fps)
        _audit("camera", "start", backend="picamera2", device="picamera2", size=[self._w, self._h], fps=self._fps)

    def stop(self) -> None:
        if self._cam:
            try:
                self._cam.stop()
            except Exception:
                pass
        self._cam = None
        self._run = False
        _audit("camera", "stop", backend="picamera2")

    def read(self) -> Optional[np.ndarray]:
        if not self._run or not self._cam:
            return None
        try:
            return self._cam.capture_array()  # type: ignore
        except Exception as e:
            LOG.error("PiCamera2 read()-Fehler: %s", e)
            _audit("camera", "error", backend="picamera2", error=str(e))
            return None

    def running(self) -> bool:
        return self._run

    def id_string(self) -> str:
        return f"picamera2({self._w}x{self._h}@{self._fps})"

class _OpenCVCam(_BaseCam):
    def __init__(self, dev: int, w: int, h: int, fps: int) -> None:
        self._dev, self._w, self._h, self._fps = dev, w, h, fps
        self._cap = None
        self._run = False

    def start(self) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV nicht verfügbar")
        if self._run:
            return
        cap = cv2.VideoCapture(self._dev)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._h)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        if not cap.isOpened():
            _audit("camera", "error", backend="opencv", device=self._dev, error="open_failed")
            raise RuntimeError(f"OpenCV-Kamera {self._dev} konnte nicht geöffnet werden")
        self._cap = cap
        self._run = True
        LOG.info("OpenCV-Kamera gestartet (dev=%d, %dx%d@~%dfps)", self._dev, self._w, self._h, self._fps)
        _audit("camera", "start", backend="opencv", device=self._dev, size=[self._w, self._h], fps=self._fps)

    def stop(self) -> None:
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None
        self._run = False
        _audit("camera", "stop", backend="opencv", device=self._dev)

    def read(self) -> Optional[np.ndarray]:
        if not self._run or not self._cap:
            return None
        ok, frame = self._cap.read()
        if not ok:
            _audit_throttled("opencv_read_fail", 5.0, "camera", "read_fail", backend="opencv", device=self._dev)
            return None
        return frame

    def running(self) -> bool:
        return self._run

    def id_string(self) -> str:
        return f"opencv(dev={self._dev},{self._w}x{self._h}@{self._fps})"

# =============================================================================
# DeviceHub (Singleton) – Kamera + Light + Audio (+ Sessions)
# =============================================================================
class DeviceHub:
    """Zentrale thread-sichere Geräteverwaltung (Kamera, Light, Audio) mit Audit-Logging und Sessions."""

    _inst: Optional["DeviceHub"] = None
    _inst_lock = threading.Lock()

    # ----- Singleton Zugriff -----
    @classmethod
    def instance(cls) -> "DeviceHub":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = DeviceHub()
            return cls._inst

    # ----- Init -----
    def __init__(self) -> None:
        # Kamera/Light-Konfiguration
        self.backend = os.environ.get("OROMA_VISION_BACKEND", "picamera2").lower()
        self.dev_id = int(os.environ.get("OROMA_VISION_DEVICE", "0"))
        self.w = int(os.environ.get("OROMA_VISION_W", "640"))
        self.h = int(os.environ.get("OROMA_VISION_H", "360"))
        self.fps = int(os.environ.get("OROMA_VISION_FPS", "30"))

        self.light_source = os.environ.get("OROMA_LIGHT_SOURCE", "camera").lower()
        self.light_interval = int(os.environ.get("OROMA_LIGHT_CAMERA_INTERVAL", "300"))
        self.light_min = float(os.environ.get("OROMA_LIGHT_MIN", "0"))
        self.light_max = float(os.environ.get("OROMA_LIGHT_MAX", "100"))
        self.light_audit_mode = os.environ.get("OROMA_LIGHT_AUDIT_MODE", "changes").strip().lower()

        # Audio-Konfiguration
        self.audio_enable = os.environ.get("OROMA_AUDIO_ENABLE", "true").lower() in ("1", "true", "yes", "on")
        self.audio_in_name = os.environ.get("OROMA_AUDIO_INPUT_NAME", "").strip()
        self.audio_out_name = os.environ.get("OROMA_AUDIO_OUTPUT_NAME", "").strip()
        self.audio_sr = int(os.environ.get("OROMA_AUDIO_SR", "16000"))
        self.audio_ch = int(os.environ.get("OROMA_AUDIO_CH", "1"))
        self.audio_block_ms = int(os.environ.get("OROMA_AUDIO_BLOCK_MS", "20"))
        self.audio_ring_sec = int(os.environ.get("OROMA_AUDIO_RING_SEC", "10"))
        self.audio_lvl_iv = float(os.environ.get("OROMA_AUDIO_LEVEL_INTERVAL", "0.15"))

        # --- Kamera State ---
        self._cam: _BaseCam = self._build_cam()
        self._cap_thread: Optional[threading.Thread] = None
        self._cap_run = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_ts: float = 0.0

        # --- Light Cache ---
        self._light_lock = threading.Lock()
        self._light_val: Optional[float] = None
        self._light_ts: float = 0.0
        self._light_state: Optional[str] = None  # "DARK"|"BRIGHT"|None
        # Hysterese-Grenzen (konservativ)
        self._dark_thr = 30.0
        self._bright_thr = 40.0

        # --- Audio State ---
        self._audio_lock = threading.Lock()
        self._mic_stream = None
        self._out_stream = None
        self._ring = deque(maxlen=max(1, self.audio_ring_sec * max(1, self.audio_sr) // max(1, int(self.audio_sr * self.audio_block_ms / 1000))))
        self._ring_np_cache: Optional[np.ndarray] = None  # lazy concat cache
        self._lvl_ts = 0.0
        self._lvl_val = 0.0  # 0..1 RMS
        self._in_dev_idx: Optional[int] = None
        self._out_dev_idx: Optional[int] = None

        # --- Sessions ---
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._sess_lock = threading.Lock()

        LOG.info("DeviceHub init: backend=%s, dev=%s, %dx%d@%dfps, light=%s/%ss, audio=%s (sr=%d, ch=%d, block=%sms, ring=%ss)",
                 self.backend, self.dev_id, self.w, self.h, self.fps,
                 self.light_source, self.light_interval,
                 "on" if (self.audio_enable and sd is not None) else "off",
                 self.audio_sr, self.audio_ch, self.audio_block_ms, self.audio_ring_sec)
        _audit("hub", "init", vision_backend=self.backend, dev=self.dev_id,
               size=[self.w, self.h], fps=self.fps,
               light=self.light_source, audio_enabled=bool(self.audio_enable and sd is not None))

    # -------------------------------------------------------------------------
    # Sessions-API (optional, für sauberes Tracking von Nutzern)
    # -------------------------------------------------------------------------
    def open_session(self, client: str, kind: str) -> str:
        """
        Öffnet eine Session (z. B. client='video_ui', kind='camera|audio|light|generic').
        Liefert session_id (UUID). Muss mit close_session() geschlossen werden.
        """
        sid = str(uuid.uuid4())
        now = time.time()
        with self._sess_lock:
            self._sessions[sid] = {"client": client, "kind": kind, "start": now}
        _audit("session", "open", session_id=sid, client=client, kind=kind)
        return sid

    def close_session(self, session_id: str) -> None:
        now = time.time()
        with self._sess_lock:
            sess = self._sessions.pop(session_id, None)
        if sess:
            dur = now - sess.get("start", now)
            _audit("session", "close", session_id=session_id, client=sess.get("client"), kind=sess.get("kind"), duration=dur)

    # -------------------------------------------------------------------------
    # Kamera
    # -------------------------------------------------------------------------
    def _build_cam(self) -> _BaseCam:
        if self.backend == "picamera2" and Picamera2 is not None:
            return _PiCamera2Cam(self.w, self.h, self.fps)
        if self.backend == "opencv" and cv2 is not None:
            return _OpenCVCam(self.dev_id, self.w, self.h, self.fps)
        LOG.warning("Kein passendes Kamera-Backend → DummyCam.")
        return _DummyCam(self.w, self.h)

    def _loop(self) -> None:
        period = 1.0 / max(self.fps, 1)
        while self._cap_run:
            t0 = time.time()
            frame = None
            try:
                frame = self._cam.read()
            except Exception as e:
                LOG.error("Kamera read()-Fehler: %s", e)
                _audit_throttled("camera_read_exc", 5.0, "camera", "read_error", backend=self.backend, error=str(e))
            if frame is not None:
                with self._frame_lock:
                    self._latest_frame = frame
                    self._latest_ts = time.time()
            dt = time.time() - t0
            time.sleep(max(0.0, period - dt))

    def start(self) -> None:
        """Startet (falls nicht bereits gestartet) Kamera + Capture-Thread."""
        if self._cap_run:
            return
        try:
            self._cam.start()
        except Exception as e:
            LOG.error("Kamera konnte nicht gestartet werden: %s", e)
            _audit("camera", "start_fail", backend=self.backend, device=self.dev_id, error=str(e))
        self._cap_run = True
        self._cap_thread = threading.Thread(target=self._loop, daemon=True)
        self._cap_thread.start()
        LOG.info("DeviceHub Capture-Thread läuft.")
        _audit("camera", "capture_loop_start", backend=self.backend, device=self._cam.id_string())

    def stop(self) -> None:
        """Stoppt Capture-Thread und schließt Kamera."""
        self._cap_run = False
        if self._cap_thread and self._cap_thread.is_alive():
            try:
                self._cap_thread.join(timeout=1.5)
            except Exception:
                pass
        self._cap_thread = None
        try:
            self._cam.stop()
        except Exception:
            pass
        LOG.info("DeviceHub (Kamera) gestoppt.")
        _audit("camera", "capture_loop_stop", backend=self.backend)

    def get_latest_frame(self, ensure_start: bool = True) -> Tuple[Optional[np.ndarray], float]:
        """Gibt (Frame, Timestamp) zurück. Frame ist BGR ndarray oder None."""
        if ensure_start and not self._cap_run:
            self.start()
            time.sleep(0.05)  # kleines Aufwärmen
        with self._frame_lock:
            return (None if self._latest_frame is None else self._latest_frame.copy(), self._latest_ts)

    def get_latest_jpeg(self, quality: int = 85, client: Optional[str] = None) -> Optional[bytes]:
        """Gibt aktuelles Frame als JPEG-Bytes zurück (oder None)."""
        frame, ts = self.get_latest_frame()
        if frame is None:
            _audit_throttled("jpeg_none", 3.0, "camera", "snapshot_none", backend=self.backend, client=client)
            return None
        if cv2 is None:
            try:
                import imageio  # type: ignore
                jb = imageio.v3.imencode(".jpg", frame, quality=quality).tobytes()
            except Exception as e:
                _audit_throttled("jpeg_enc_fail", 5.0, "camera", "snapshot_encode_fail", backend=self.backend, error=str(e))
                return None
        else:
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            if not ok:
                _audit_throttled("jpeg_enc_fail_cv2", 5.0, "camera", "snapshot_encode_fail", backend=self.backend, client=client)
                return None
            jb = buf.tobytes()
        _audit_throttled("snapshot_ok", _AUDIT_SNAP_THR, "camera", "snapshot", backend=self.backend, bytes=len(jb), ts_frame=ts, client=client)
        return jb

    def mjpeg_generator(self, boundary: bytes = b"frame", fps_cap: Optional[int] = None, client: Optional[str] = None) -> Generator[bytes, None, None]:
        """Generator für Flask-Response mit 'multipart/x-mixed-replace'."""
        min_period = 1.0 / float(fps_cap or self.fps or 10)
        sid = None
        try:
            if client:
                sid = self.open_session(client, kind="camera")
            while True:
                t0 = time.time()
                jpg = self.get_latest_jpeg(client=client)
                if jpg is not None:
                    _audit_throttled(f"mjpeg_{client or 'anon'}", _AUDIT_SNAP_THR, "camera", "mjpeg_push", client=client, bytes=len(jpg))
                    yield (b"--" + boundary + b"\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" +
                           jpg + b"\r\n")
                dt = time.time() - t0
                time.sleep(max(0.0, min_period - dt))
        finally:
            if sid:
                self.close_session(sid)

    # -------------------------------------------------------------------------
    # Light (0..100)
    # -------------------------------------------------------------------------
    def _calc_luma(self, frame: np.ndarray) -> float:
        r = frame[:, :, 2].astype(np.float32)
        g = frame[:, :, 1].astype(np.float32)
        b = frame[:, :, 0].astype(np.float32)
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return float(y.mean())

    def _scale_0_100(self, luma_0_255: float) -> float:
        v = (luma_0_255 / 255.0) * 100.0
        v = max(self.light_min, min(self.light_max, v))
        return round(v, 2)

    def get_light_level(self) -> Optional[float]:
        """Gibt gemessene Helligkeit 0..100 zurück (oder None bei off)."""
        if self.light_source == "off":
            return None
        now = time.time()
        with self._light_lock:
            if self._light_val is not None and (now - self._light_ts) < max(1, self.light_interval):
                return self._light_val

        if self.light_source == "dummy":
            val = 80.0  # „hell“
            with self._light_lock:
                self._light_val, self._light_ts = val, now
            if self.light_audit_mode in ("all",):
                _audit("light", "sample", mode="dummy", value=val)
            return val

        frame, _ = self.get_latest_frame(ensure_start=True)
        if frame is None:
            LOG.debug("Light: kein Frame verfügbar.")
            _audit_throttled("light_none", 10.0, "light", "no_frame", mode=self.light_source)
            return None
        luma = self._calc_luma(frame)
        val = self._scale_0_100(luma)

        new_state = "DARK" if val <= (self._dark_thr * (100.0/255.0)) else ("BRIGHT" if val >= (self._bright_thr * (100.0/255.0)) else self._light_state)
        with self._light_lock:
            prev_state = self._light_state
            self._light_val, self._light_ts = val, now
            self._light_state = new_state

        if self.light_audit_mode == "all":
            _audit("light", "sample", value=val, state=self._light_state)
        elif self.light_audit_mode == "changes" and new_state != prev_state and new_state is not None:
            _audit("light", "change", value=val, state=self._light_state, prev=prev_state)
        return val

    # -------------------------------------------------------------------------
    # Audio – Geräte, Capture, Playback
    # -------------------------------------------------------------------------
    def _require_audio(self) -> bool:
        if not self.audio_enable:
            return False
        if sd is None:
            LOG.warning("Audio deaktiviert: 'sounddevice' nicht verfügbar.")
            return False
        return True

    def list_audio_devices(self) -> Dict[str, List[Dict[str, Any]]]:
        """Liefert {'input': [...], 'output': [...]} mit device info (falls verfügbar)."""
        out: Dict[str, List[Dict[str, Any]]] = {"input": [], "output": []}
        if sd is None:
            return out
        try:
            devs = sd.query_devices()
            for i, d in enumerate(devs):
                info = {"index": i, "name": d["name"], "max_input_channels": d["max_input_channels"], "max_output_channels": d["max_output_channels"]}
                if d.get("max_input_channels", 0) > 0:
                    out["input"].append(info)
                if d.get("max_output_channels", 0) > 0:
                    out["output"].append(info)
        except Exception as e:
            LOG.warning("list_audio_devices() Fehler: %s", e)
        return out

    def _pick_device_index(self, want_name: str, want_input: bool) -> Optional[int]:
        if sd is None:
            return None
        try:
            devs = sd.query_devices()
            want = (want_name or "").lower().strip()
            best_idx = None
            for i, d in enumerate(devs):
                name = str(d.get("name", "")).lower()
                ok_ch = (d.get("max_input_channels", 0) > 0) if want_input else (d.get("max_output_channels", 0) > 0)
                if ok_ch and (not want or want in name):
                    best_idx = i
                    if want and want in name:
                        break
            return best_idx
        except Exception:
            return None

    def _audio_callback(self, indata, frames, time_info, status):  # sd.InputStream callback
        # indata: float32 [-1,1], shape (frames, channels)
        if status:
            LOG.debug("Audio status: %s", status)
        if indata is None:
            return
        try:
            if indata.ndim == 2 and indata.shape[1] > 1:
                buf = np.mean(indata, axis=1, dtype=np.float32)
            else:
                buf = indata.reshape(-1).astype(np.float32)
        except Exception:
            try:
                buf = np.array(indata, dtype=np.float32).reshape(-1)
            except Exception:
                return
        with self._audio_lock:
            self._ring.append(buf)
            self._ring_np_cache = None
            t = time.time()
            if t - self._lvl_ts >= self.audio_lvl_iv:
                s = float(np.sqrt(np.mean(np.square(buf), dtype=np.float32))) if buf.size else 0.0
                self._lvl_val = max(0.0, min(1.0, s))
                self._lvl_ts = t

    def start_mic(self, client: Optional[str] = None) -> bool:
        """Startet den Mikrofonstream (lazy)."""
        if not self._require_audio():
            return False
        if self._mic_stream is not None:
            return True
        try:
            self._in_dev_idx = self._pick_device_index(self.audio_in_name, want_input=True)
            if self._in_dev_idx is None:
                LOG.info("Audio-In: kein passendes Gerät gefunden – nutze Default.")
            blocksize = int(self.audio_sr * self.audio_block_ms / 1000)
            stream = sd.InputStream(
                samplerate=self.audio_sr,
                channels=max(1, self.audio_ch),
                dtype="float32",
                callback=self._audio_callback,
                blocksize=max(16, blocksize),
                device=self._in_dev_idx if self._in_dev_idx is not None else None,
            )
            stream.start()
            self._mic_stream = stream
            LOG.info("Mic gestartet (sr=%d ch=%d dev=%s)", self.audio_sr, self.audio_ch, str(self._in_dev_idx))
            _audit("audio", "mic_start", sr=self.audio_sr, ch=self.audio_ch, dev_index=self._in_dev_idx, dev_name=self.audio_in_name or None, client=client)
            return True
        except Exception as e:
            LOG.error("Mic konnte nicht gestartet werden: %s", e)
            _audit("audio", "mic_start_fail", error=str(e))
            self._mic_stream = None
            return False

    def stop_mic(self, client: Optional[str] = None) -> None:
        """Stoppt den Mikrofonstream."""
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None
            LOG.info("Mic gestoppt.")
            _audit("audio", "mic_stop", client=client)

    def get_audio_level(self) -> float:
        """RMS-Level des letzten Blocks (0..1)."""
        return float(self._lvl_val)

    def _concat_ring(self) -> np.ndarray:
        with self._audio_lock:
            if self._ring_np_cache is not None:
                return self._ring_np_cache
            if not self._ring:
                self._ring_np_cache = np.zeros((0,), dtype=np.float32)
            else:
                self._ring_np_cache = np.concatenate(list(self._ring), dtype=np.float32) if len(self._ring) > 1 else self._ring[0].copy()
            return self._ring_np_cache

    def read_audio(self, seconds: float, client: Optional[str] = None) -> np.ndarray:
        """
        Liefert bis zu 'seconds' Sekunden Mono-PCM (float32, [-1,1]) aus dem Ringbuffer.
        Wenn weniger vorhanden, wird nur das geliefert, was vorliegt.
        """
        if not self._require_audio():
            return np.zeros((0,), dtype=np.float32)
        if self._mic_stream is None:
            self.start_mic(client=client)
            time.sleep(max(0.0, self.audio_block_ms / 1000.0))
        buf = self._concat_ring()
        need = int(max(0, seconds) * self.audio_sr)
        if need <= 0 or buf.size == 0:
            return np.zeros((0,), dtype=np.float32)
        out = buf[-need:] if buf.size >= need else buf.copy()
        _audit_throttled("read_audio", 2.0, "audio", "read", seconds=seconds, samples=int(out.size))
        return out

    def record_wav(self, seconds: float, sr: Optional[int] = None, client: Optional[str] = None) -> bytes:
        """
        Nimmt bis zu 'seconds' Sekunden auf (aus Ringbuffer) und liefert WAV-Bytes (PCM16 mono).
        Hinweis: nutzt den aktuellen Ring – für exakte Aufnahme vorher kurze Wartezeit.
        """
        sr = int(sr or self.audio_sr)
        pcm = self.read_audio(seconds, client=client)
        if pcm.size == 0:
            _audit("audio", "record_empty", seconds=seconds)
            return b""
        x = np.clip(pcm, -1.0, 1.0)
        i16 = (x * 32767.0).astype(np.int16)
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(i16.tobytes())
        data = bio.getvalue()
        _audit("audio", "record_wav", seconds=seconds, bytes=len(data), sr=sr)
        return data

    def play_pcm(self, pcm: np.ndarray, sr: Optional[int] = None, client: Optional[str] = None) -> bool:
        """Spielt Mono-PCM float32 [-1,1] ab (falls Audio aktiv)."""
        if not self._require_audio():
            return False
        try:
            sr = int(sr or self.audio_sr)
            if pcm.ndim != 1:
                pcm = pcm.reshape(-1)
            self._out_dev_idx = self._out_dev_idx if self._out_dev_idx is not None else self._pick_device_index(self.audio_out_name, want_input=False)
            sd.play(pcm.astype(np.float32), samplerate=sr, device=self._out_dev_idx if self._out_dev_idx is not None else None, blocking=True)
            _audit("audio", "play_pcm", samples=int(pcm.size), sr=sr, dev_index=self._out_dev_idx, dev_name=self.audio_out_name or None, client=client)
            return True
        except Exception as e:
            LOG.error("Playback-Fehler: %s", e)
            _audit("audio", "play_fail", error=str(e))
            return False

    def play_wav(self, wav_bytes: bytes, client: Optional[str] = None) -> bool:
        """Spielt WAV-Bytes (PCM16 mono) ab."""
        if not self._require_audio():
            return False
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sampw = wf.getsampwidth()
                data = wf.readframes(wf.getnframes())
            if sampw != 2:
                LOG.warning("WAV nicht PCM16 – konvertiere grob.")
            if ch > 1:
                arr = np.frombuffer(data, dtype=np.int16).reshape(-1, ch)
                mono = arr.mean(axis=1).astype(np.int16)
            else:
                mono = np.frombuffer(data, dtype=np.int16)
            pcm = (mono.astype(np.float32) / 32767.0)
            ok = self.play_pcm(pcm, sr=sr, client=client)
            return ok
        except Exception as e:
            LOG.error("play_wav Fehler: %s", e)
            _audit("audio", "play_wav_fail", error=str(e))
            return False

    # -------------------------------------------------------------------------
    # Status / Zusammenfassung
    # -------------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """Gibt eine kompakte Übersicht über den aktuellen Gerätezustand zurück."""
        cam_running = self._cam.running()
        last_frame_age = (time.time() - self._latest_ts) if self._latest_ts else None
        st = {
            "camera": {
                "backend": self.backend,
                "id": self._cam.id_string(),
                "running": cam_running,
                "last_frame_age": last_frame_age,
                "size": [self.w, self.h],
                "fps": self.fps,
            },
            "light": {
                "source": self.light_source,
                "value": self._light_val,
                "state": self._light_state,
                "last_ts": self._light_ts,
            },
            "audio": {
                "enabled": bool(self.audio_enable and sd is not None),
                "in_name": self.audio_in_name,
                "out_name": self.audio_out_name,
                "sr": self.audio_sr,
                "ch": self.audio_ch,
                "mic_active": bool(self._mic_stream is not None),
                "level": self._lvl_val,
            },
            "sessions": self._sessions.copy(),
        }
        return st

# -----------------------------------------------------------------------------
# Singleton-Facade
# -----------------------------------------------------------------------------
def get_hub() -> DeviceHub:
    """Bequemer Zugriff auf das Singleton."""
    return DeviceHub.instance()

# -----------------------------------------------------------------------------
# Selbsttest
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Minimaler Selbsttest: Kamera anwerfen, Snapshot generieren, Light lesen, Audio-Geräte auflisten.
    hub = get_hub()
    LOG.info("Status (vor Start): %s", hub.status())
    # Kamera
    jpg = hub.get_latest_jpeg(client="selftest")
    if jpg:
        LOG.info("Snapshot bytes=%d", len(jpg))
    else:
        LOG.info("Kein Snapshot verfügbar (ggf. DummyCam).")
    # Light
    val = hub.get_light_level()
    LOG.info("Light-Level: %s", str(val))
    # Audio
    devs = hub.list_audio_devices()
    LOG.info("Audio-Geräte (kurz): input=%d, output=%d", len(devs.get("input", [])), len(devs.get("output", [])))
    LOG.info("Status (nach Tests): %s", hub.status())


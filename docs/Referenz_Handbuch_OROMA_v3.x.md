# ORÓMA v3.7.x – Referenz-Handbuch

**Stand:** 2026-04-18  
**Quelle:** System-ZIP `oroma_20260418_133357_with_db.zip`  
**Projekt:** ORÓMA (Offline-Realtime-Organic-Memory-AI) – offline-first adaptive edge intelligence architecture  

## Zitierhinweis (Zenodo)
- **Whitepaper (EN, Referenz):** 10.5281/zenodo.19596002
- **Whitepaper (DE, Übersetzung):** 10.5281/zenodo.19629298

---
## Inhaltsverzeichnis
- 1. [Core](#1-core)
- 2. [Wrapper](#2-wrapper)
- 3. [Exports & Bundles](#3-exports--bundles)
- 4. [UI / Dashboard](#4-ui--dashboard)
- 5. [Deployment / Betrieb](#5-deployment--betrieb)
- 6. [Roadmap-Notizen](#6-roadmap-notizen)
- 7. [ENV Quick Ref](#7-env-quick-ref)
- 8. [Kompatibilität & Migration](#8-kompatibilität--migration)

---

## 1) Core

### 1.1 Snap / SnapToken / Fusion / Patterns / Chains

#### core.snap (`core/snap.py`)
- **Rolle:** atomare Momentaufnahme (Feature-Vektor + Metadata + optional Content/Fusion).
- **Wesentliche Felder (Cache):** `feature_dim`, `l2_norm`, `fingerprint` (Dedup/Index).
- **Wichtige Methoden:** `normalize()`, `recompute_stats()`, `similarity(other)`, `to_dict()/from_dict()`, `as_blob()/from_blob()`, `attach_fusion()/get_fusion()`.
- **ENV:** `OROMA_SNAP_SCHEMA`, `OROMA_SNAP_PRIVACY`.

#### core.snaptoken (`core/snaptoken.py`)
- **Rolle:** text-/token-orientiertes Snap-Äquivalent (Tokenisierung/Embedding + Fingerprint + Serialisierung).
- **Wichtige Methoden:** `ensure_tokenized()`, `feature_vector()`, `normalize_embedding_()`, `to_dict()/from_dict()`, `as_blob()/from_blob()`, `sql_row()/from_row()`.

#### core.fusion (`core/fusion.py`)
- **Rolle:** Crossmodal-Fusion als `FusionPack` (Modalitäts-Vektoren + Tokens + Concepts) mit robusten Offline-Fallbacks.
- **Wichtige Komponenten:** `ModalityVec`, `FusionPack`, `FusionEngine`.
- **Funktionen:** `build_fusion()`, `similarity()`, `score()`; deterministische Fallback-Embeddings (SHA1→Vektor).

#### core.snappattern (`core/snappattern.py`)
- **Rolle:** Muster/Cluster aus Vektoren mit `centroid`, optional Gap-Markierung, SQLite-Persistenz (`snap_patterns`).
- **Wichtige Methoden:** `from_snaps()`, `add_snap()/extend_snaps()`, `recompute_centroid()`, `normalize_centroid()`, `cosine_similarity()/l2_distance()`.
- **Persistenz:** `_ensure_snappattern_schema()`, `save_pattern()`, `load_pattern()`, `find_similar()`.

#### core.snapchain (`core/snapchain.py`)
- **Rolle:** episodische Kette aus `SnapPattern` (zeitlich geordnet), optional Timing/Space-Spuren, RAG-light Knowledge-Bridge.
- **Container-API:** `append()/extend()/clear()`; robustes Koerzieren von `Snap`, `SnapPattern`, Vektor, Dict.
- **Kontext:** `append_with_context()` pflegt `metadata['timing']` und optional `metadata['space']` (Waypoints).
- **Knowledge:** `add_text()`, `add_knowledge_snap()`, `ask_knowledge()`, `synthesize_answer()`.

### 1.2 Regeln, Policy, Transfer, Mutation

#### core.regelarchiv (`core/regelarchiv.py`)
- **Rolle:** versioniertes Regelarchiv (`rules`) mit Aktivierung/Deaktivierung, Export-Markierung und Prune.
- **DBWriter-fähig:** nutzt DBWriter-Write-Pfade, wenn `OROMA_DBW_ENABLE=1`.

#### core.universal_policy (`core/universal_policy.py`)
- **Rolle:** domänenagnostische tabellarische Policy (`policy_rules`): `(namespace, state_hash) → action`.
- **Wichtig:** `state_hash` ist String → kann aus Game, Sensorik, Tools stammen (Transfer-fähig).
- **API:** `choose()`, `learn_many()`; optional Auto-Export nach `rules` (Explainability).

#### core.policy_engine (`core/policy_engine.py`)
- **Rolle:** lernt aus SnapChains in `policy_rules`, Adapter-System für Kanonisierung.
- **Quellen:** primär DB `snapchains`, fallback file-based chains über `OROMA_SNAPCHAINS`.

#### core.transfer_engine (`core/transfer_engine.py`)
- **Rolle:** TransferSnaps (sequence/pattern) + Export-Markierung + KPI-Metriken (best-effort).

#### core.mutation (`core/mutation.py`)
- **Rolle:** kontrollierte Mutationen (Jitter/Variation) auf Regel-/Kettenebene; typischerweise Dream/Offline.

#### core.roter_faden (`core/roter_faden.py`)
- **Rolle:** persistenter Thread/Intent-Kontext („roter Faden“) über `curriculum_state.window` inkl. Gap-Integration (optional).

### 1.3 DB Layer (SQLManager + DBWriter)

#### core.sql_manager (`core/sql_manager.py`)
- **Rolle:** autoritative SQLite-Schicht: `get_conn()` (PRAGMAs, dict rows), `ensure_schema()`, writer_lock + retry.
- **Wichtig:** `_ClosingConnection` schließt Connections zuverlässig (Lock-Vermeidung 24/7).
- **DBWriter Strict Mode:** `OROMA_DBW_STRICT_LOCAL_WRITES=1` → managed DBs lokal read-only (kein Bypass).

#### core.db_writer (`core/db_writer.py`) + core.db_writer_client (`core/db_writer_client.py`)
- **Rolle:** Single-Writer-Daemon (UNIX socket) + Client (framed JSON) für serielle Writes, Queue/Top-Tags.
- **Ops:** `ping`, `state`, `exec`, `executemany`, `transaction`.

### 1.4 Episodic / LTM / Reward / Curiosity / Explain

#### core.episodic (`core/episodic.py`)
- **Rolle:** Episoden-Tabellen/Recall; schema-aware; create/add/finalize; recall helper.

#### core.langzeitgedaechtnis (`core/langzeitgedaechtnis.py`)
- **Rolle:** Langzeitgedächtnis-Index (optional Annoy/FAISS) + Similarity/Replay-Unterstützung.

#### core.reward / core.curiosity / core.predictor / core.explain
- **Rolle:** Reward-Logging/Aggregation, Curiosity-Logs, Predictor/Explain Engines (optional imports).

### 1.5 DeviceHub / Circadian / Dream

#### core.device_hub (`core/device_hub.py`) + core.camera_hub (`core/camera_hub.py`)
- **Rolle:** Headless Hardware-Hub (Camera/Audio/Light) + Provider/Frame Injection + MJPEG/Snapshot + Audit/Sessions.
- **External Provider Safety:** External TTL + Freshness Gate verhindern internen Kamera-Start im Provider-Mode.

#### core.circadian_controller (`core/circadian_controller.py`)
- **Rolle:** DAY↔DREAM Umschaltung via Light + Delay + Hysterese; optional Clock-Fallback; Phase-Status.

#### core.dream_worker (`core/dream_worker.py`)
- **Rolle:** Offline-Konsolidierung: Replay/Mutation/LTM/Prune/Research + optionale SceneGraph/ObjectGraph Ableitungen.
- **RunLock:** `OROMA_DREAM_LOCK` verhindert parallele Dream-Läufe (orchestrator-safe).

#### core.nmr_synaptic_plasticity (`core/nmr_synaptic_plasticity.py`)
- **Rolle:** Dream-Job für synaptische Plastizität über `object_relations` relation='synaptic' (confidence + notes JSON).

---

## 2) Wrapper

**Wrapper-Verzeichnis:** `wrappers/` (in ZIP vorhanden).
Enthaltene Wrapper-Module:
- `__init__.py`
- `audio_wrapper.py`
- `degirum_wrapper.py`
- `dynamic_wrapper.py`
- `gstreamer_wrapper.py`
- `hailo_wrapper.py`
- `oroma_wrapper.py`
- `picar_wrapper.py`
- `ptz_controller.py`
- `sensor_ir_front.py`
- `text_wrapper.py`
- `tts_wrapper.py`
- `vision_wrapper.py`

Kurzrollen (orientierend):
- `oroma_wrapper.py`: Meta-Auswahl/Router (CPU/Hailo/DeGirum).
- `vision_wrapper.py`: Kamera/Video-Embedding/Primitives (Backend routing).
- `audio_wrapper.py`: Audio/ASR Pfade (offline).
- `tts_wrapper.py`: Offline-TTS / Bridge zu UI-Audio.
- `gstreamer_wrapper.py`: RTSP/Files.
- `hailo_wrapper.py` / `degirum_wrapper.py`: NPU Backends.
- `picar_wrapper.py`: PiCar Integration (optional).
- `ptz_controller.py`: PTZ Control Bridge.

---

## 3) Exports & Bundles

- Export/Import ist im Projekt über UI + Core-Module abgebildet (u.a. ExportGate/Bundle UI).
- Public Snapshot Policy: DB/log/state werden typischerweise ausgeschlossen (siehe `docs/core/90_publication.md`).

### 3.1 Export-Grenze: ORÓMA-eigene Artefakte vs. externe Runtime-/Vendor-Artefakte

Für die praktische Nutzung auf Edge-Hardware – insbesondere auf Raspberry Pi 5 mit optionalem Hailo/NPU-Pfad – ist eine klare Trennung wichtig:

#### ORÓMA-eigene Exportdomäne
Diese Artefakte sind konzeptionell ORÓMA-intern und können durch ORÓMA gesammelt, verdichtet, gebündelt, exportiert und wieder importiert werden:

- Knowledge-/Memory-Artefakte
  - Snap-/SnapChain-bezogene Inhalte
  - Replay-/Dream-Ergebnisse
  - Meta-/Bundle-Daten
- Policy-/Regel-Artefakte
  - `policy_rules`
  - `rules`
  - daraus abgeleitete Explainability-/Bundle-Pakete
- Runtime-Metadaten
  - Registry-Einträge
  - Aktivierungszustände
  - kompatible Zielpfad-Informationen

Kurz gesagt: **ORÓMA exportiert primär Wissen, Policies, Bundles und Runtime-Metadaten – nicht proprietäre Accelerator-Buildprodukte als eigene ORÓMA-Origin.**

#### Externe Runtime-/Vendor-Artefakte
Davon klar zu trennen sind Artefakte, die aus externen Toolchains, Modellformaten oder Vendor-spezifischen Beschleunigerpfaden stammen, z. B.:

- ONNX-Modelle aus externem Training
- GGUF-Modelle / llama.cpp-kompatible Gewichte
- Hailo-kompilierte Zielartefakte (z. B. HEF-/Compiler-nahe Outputs)
- DeGirum-/NPU-spezifische Runtime-Ziele
- Third-Party-Modelle wie Whisper oder andere fremdlizenzierte Gewichte

Für diese Artefakte gilt im ORÓMA-Kontext:

- ORÓMA kann sie **registrieren, auswählen, aktivieren und nutzen**
- ORÓMA kann **accelerator-aware** und **backend-aware** sein
- ORÓMA ist damit **Hailo-capable / Hailo-aware**, wenn passende externe Artefakte vorhanden sind
- ORÓMA sollte solche Artefakte aber **nicht pauschal als eigene, frei redistributable ORÓMA-Exports behandeln**

#### Praktische Leitlinie

Die saubere Grenze lautet daher:

- **Knowledge Export / Policy Export / Bundle Export** → ORÓMA-eigene Exportlogik
- **Vendor-spezifische Modellkompilierung / Accelerator-Zielformate** → externe Integrations- und Deployment-Ziele

Das ist besonders für Hailo wichtig:

- ORÓMA kann einen Hailo-fähigen Runtime-Pfad vorbereiten und nutzen
- ORÓMA kann kompatible externe Hailo-Artefakte registrieren
- die eigentliche Vendor-Toolchain und die daraus resultierenden Drittartefakte bleiben jedoch konzeptionell außerhalb des ORÓMA-Kernexports

#### Dokumentationsregel

In öffentlicher Doku und bei Zenodo-/Repo-Veröffentlichungen sollte deshalb sprachlich sauber getrennt werden zwischen:

- **ORÓMA exports**
- **externally compiled runtime artifacts**
- **registered accelerator targets**

Diese Trennung verhindert Lizenzmissverständnisse, falsche Ownership-Eindrücke und unnötige Vermischung von ORÓMA-Wissen mit Third-Party-Deployments.

---

## 4) UI / Dashboard

### 4.1 Start & Registrierung (real aus `run_oroma.py`)
`run_oroma.py` importiert UI-Module guarded und registriert Blueprints best-effort. Bei Importfehlern bleibt der Boot stabil.

Registrierte UI-Module (guarded imports in `run_oroma.py`):
- `ui.control_ui`
- `ui.health_ui`
- `ui.gaps_ui`
- `ui.export_ui`
- `ui.bundle_ui`
- `ui.episodic_ui`
- `ui.synapses_ui`
- `ui.memory_ui`
- `ui.why_ui`
- `ui.games_ui`
- `ui.replay_api`
- `ui.stats_ui`
- `ui.pong_panel_ui`
- `ui.forgetting_ui`
- `ui.ask_ui`
- `ui.knowledge_ui`
- `ui.learning`
- `ui.video_ui`
- `ui.replay_ui`
- `ui.models_ui`
- `ui.dream_ui`
- `ui.chat_ui`
- `ui.tetris_ui`
- `ui.picar_ui`
- `ui.asr_ui`
- `ui.asr2_ui`
- `ui.audio_ui`
- `ui.research_ui`
- `ui.meta_ui`
- `ui.calculator_ui`
- `ui.scicalc_ui`
- `ui.setcalc_ui`
- `ui.empathy_ui`
- `ui.coverage_ui`
- `ui.selftest_ui`
- `ui.missions_ui`
- `ui.curriculum_ui`
- `ui.selfrec_ui`
- `ui.scenegraph_ui`
- `ui.objects_ui`
- `ui.admin`

### 4.2 Auth / Token
- UI Token ist optional; wenn `OROMA_UI_TOKEN` leer ist, ist UI offen (Design).

---

## 5) Deployment / Betrieb

### 5.1 systemd Units (in `systemd/` der ZIP)
Wichtige Services/Timer (Auszug, real in ZIP vorhanden):
- `oroma-archive.service`
- `oroma-archive.timer`
- `oroma-cam-train.service`
- `oroma-cam-train.timer`
- `oroma-db-writer-watchdog.service`
- `oroma-db-writer-watchdog.timer`
- `oroma-db-writer.service`
- `oroma-dream.service`
- `oroma-dream.timer`
- `oroma-energy.service`
- `oroma-energy.timer`
- `oroma-exportgate.service`
- `oroma-exportgate.timer`
- `oroma-forgetting.service`
- `oroma-forgetting.timer`
- `oroma-gap-miner.service`
- `oroma-gap-miner.timer`
- `oroma-health.service`
- `oroma-health.timer`
- `oroma-kpi.service`
- `oroma-kpi.timer`
- `oroma-orchestrator.service`
- `oroma-policy.service`
- `oroma-policy.timer`
- `oroma-ramflush.service`
- `oroma-ramflush.timer`
- `oroma-replay.service`
- `oroma-replay.timer`
- `oroma-selftest.service`
- `oroma-selftest.timer`
- `oroma-social.service`
- `oroma-social.timer`
- `oroma-stats-repair.service`
- `oroma-stats-repair.timer`
- `oroma-stats.service`
- `oroma-stats.timer`
- `oroma-train-snake.service`
- `oroma-train-snake.timer`
- `oroma-usb-kernelwatch.service`
- `oroma-usb-kernelwatch.timer`
- `oroma-usb-noautosuspend.service`
- `oroma.service`

### 5.2 Orchestrator-Modus
- `oroma-orchestrator.service` startet `tools/oroma_orchestrator.py` (serielle Jobs).
- Flag-Datei `.use_orchestrator` sorgt dafür, dass einzelne oneshot services via `ConditionPathExists=!...` übersprungen werden.

### 5.3 Engine Service
- `oroma.service` startet `run_oroma.py` (UI + AgentLoop + DeviceHub + optional Circadian/Dream).

---

## 6) Roadmap-Notizen

- v2.20: Spatio-Temporal Spuren (SnapChain metadata timing/space) – im heutigen Code optional vorhanden.
- v3.x: Day/Dream Trennung + Replay/DreamWorker + ExportGate/Bundle.
- v3.5+: MetaSnaps, Mutation, LTM, AutoTuner, AgentLoop Hooks.
- v3.7+: Curriculum + Self-Listening/ASR Reflex + Empathy/Coverage + SciCalc/SetCalc UI/Rewards.

---

## 7) ENV Quick Ref

**Core/UI:** `FLASK_RUN_HOST`, `FLASK_RUN_PORT`, `OROMA_UI_TOKEN`, `OROMA_LOG_LEVEL`

**AgentLoop:** `OROMA_AGENT_ENABLED`, `OROMA_AGENT_DT`

**DB:** `OROMA_DB_PATH`, `OROMA_DB_WAL`, `OROMA_DB_BUSY_TIMEOUT_MS`, `OROMA_DB_LOCK_RETRY_SEC`

**DBWriter:** `OROMA_DBW_ENABLE`, `OROMA_DBW_STRICT_LOCAL_WRITES`, `OROMA_DBW_SOCKET`, `OROMA_DBW_ALLOW_DBS`

**DeviceHub/Light:** `OROMA_LIGHT_SOURCE`, `OROMA_LIGHT_CAMERA_INTERVAL`, `OROMA_LIGHT_MIN`, `OROMA_LIGHT_MAX`

**Circadian:** `OROMA_NIGHTMODE_LIGHT_THRESHOLD`, `OROMA_NIGHTMODE_DELAY_MINUTES`, `OROMA_CIRCADIAN_POLL_SEC`, `OROMA_CIRCADIAN_HYSTERESIS`

**Dream:** `OROMA_DREAM_ENABLED`, `OROMA_DREAM_LOCK`, `OROMA_DREAM_MAX_RUNTIME_S`, `OROMA_DREAM_REPLAY_BATCH_SIZE`

---

## 8) Kompatibilität & Migration

- Migrationen sind idempotent (`ensure_schema()`), additiv, non-destructive.
- DBWriter Strict Mode verhindert lokale Bypass-Writes auf managed DBs.
- UI-Blueprints sind guarded imports → Deploy bleibt stabil, auch wenn Teilmodule fehlen.


---

# Band A – Core Memory Stack (Detail)

> Quelle: System-ZIP `oroma_20260418_133357_with_db.zip` (Stand 2026-04-18)

## Snap – `core/snap.py`

### Rolle

- Atomare Momentaufnahme: Feature-Vektor + Metadata + optional Content + optional Fusion-Anheftung.

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** `Snap`

- **Funktionen:** `_compute_l2_norm`, `_compute_fingerprint`, `__init__`, `_recompute_stats`, `recompute_stats`, `attach_fusion`, `get_fusion`, `normalize`, `similarity`, `with_metadata`, `merge_metadata`, `to_dict`, `from_dict`, `as_blob`, `from_blob`, `__repr__`, `short_info`, `dedup_or_insert_snap`

### ENV (direkt im Modul referenziert)

- `OROMA_SNAPCHAIN_LEVEL`, `OROMA_SNAPCHAIN_LOGLEVEL`, `OROMA_SNAP_PRIVACY`, `OROMA_SNAP_SCHEMA`

### Persistenz / Tabellen (konzeptuell)

- Diese Module sind primär Daten-/Logikobjekte; DB-Persistenz erfolgt üblicherweise über `core.sql_manager` (und ggf. DBWriter im Single-Writer Modus).

### Betriebsinvarianten / Failure Modes

- Caches (`feature_dim`, `l2_norm`, `fingerprint`) müssen nach Feature-Änderungen via `recompute_stats()` konsistent sein.

- Fusion ist optional: fehlendes `core.fusion` darf keinen Crash erzeugen.



## SnapToken – `core/snaptoken.py`

### Rolle

- Token-/Text-orientierte Einheit: Tokenisierung/Embedding + stabiler Fingerprint + DB/Blob-Support.

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** `SnapToken`

- **Funktionen:** `_pack_blob`, `_unpack_blob`, `_llm_tokenize_safe`, `_hash_to_int`, `_normalize_vec`, `_stable_fingerprint`, `__post_init__`, `_tokenize_text_inplace`, `ensure_tokenized`, `feature_vector`, `normalize_embedding_`, `_compute_fingerprint`, `to_dict`, `from_dict`, `as_blob`, `from_blob`, `sql_row`, `from_row`, `is_text`, `short_info`

### ENV (direkt im Modul referenziert)

- `OROMA_LOG_LEVEL`

### Persistenz / Tabellen (konzeptuell)

- Diese Module sind primär Daten-/Logikobjekte; DB-Persistenz erfolgt üblicherweise über `core.sql_manager` (und ggf. DBWriter im Single-Writer Modus).

### Betriebsinvarianten / Failure Modes

- Tokenisierung/Embedding ist tolerant; Serialisierung/Fingerprint bleibt stabil.



## Fusion – `core/fusion.py`

### Rolle

- Crossmodal-Fusion: vereinheitlicht mehrere Modalitätsvektoren (vision/audio/text/other) und liefert stabile Similarity/Score-Pfade inkl. deterministische Fallback-Embeddings.

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** `ModalityVec`, `FusionPack`, `FusionEngine`

- **Funktionen:** `_l2norm`, `_cosine`, `to_dict`, `from_dict`, `to_json`, `from_json`, `__init__`, `text_to_vec`, `tokenize`, `normalize_concepts`, `vision_to_vec`, `build_fusion`, `similarity`, `fuse`, `split`, `score`

### ENV (direkt im Modul referenziert)

- `OROMA_EMBED_DIM`, `OROMA_EMBED_NORM`, `OROMA_FUSION_ENABLE`, `OROMA_FUSION_MODE`, `OROMA_FUSION_NORMALIZE`, `OROMA_FUSION_W_AUDIO`, `OROMA_FUSION_W_TEXT`, `OROMA_FUSION_W_VISION`

### Persistenz / Tabellen (konzeptuell)

- Diese Module sind primär Daten-/Logikobjekte; DB-Persistenz erfolgt üblicherweise über `core.sql_manager` (und ggf. DBWriter im Single-Writer Modus).

### Betriebsinvarianten / Failure Modes

- Fallback-Embeddings sichern Stabilität in headless/offline Setups.



## SnapPattern – `core/snappattern.py`

### Rolle

- Muster/Cluster: Aggregiert Vektoren zu einem `centroid`, unterstützt Similarity (Cos/L2), Knowledge-Gap-Heuristik und SQLite-Persistenz (`snap_patterns`).

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** `Snap`, `SnapToken`, `SnapPattern`

- **Funktionen:** `__init__`, `feature_vector`, `_to_vector_list`, `_centroid`, `_dot`, `_norm`, `cosine_similarity`, `l2_distance`, `_pack_json`, `_unpack_json`, `from_snaps`, `add_snap`, `extend_snaps`, `feature_dim`, `recompute_centroid`, `normalize_centroid`, `to_dict`, `from_dict`, `as_blob`, `from_blob`, `cosine_to`, `l2_to`, `detect_gap`, `_ensure_snappattern_schema`, `save_pattern`, `load_pattern`, `update_metadata`, `set_gap_flag`, `find_similar`, `create_and_save_from_snaps`, `quick_similarity`, `_selftest`, `rand_vec`

### ENV (direkt im Modul referenziert)

- `OROMA_LOG_LEVEL`

### Persistenz / Tabellen (konzeptuell)

- `snap_patterns` (Schema via `_ensure_snappattern_schema()`): speichert Centroid + optional Full-Payload + metadata + gap_flag.

### Betriebsinvarianten / Failure Modes

- Gap-Detection ist best-effort und darf nie hart crashen.

- `full payload` Speicherung ist optional; centroid-only ist der sichere Default.



## SnapChain – `core/snapchain.py`

### Rolle

- Episodische Sequenz: geordnete Kette aus `SnapPattern`, optional Timing/Space-Spuren, plus RAG-light Knowledge-Helpers.

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** `SnapChain`

- **Funktionen:** `synthesize_answer`, `_as_float_list`, `_is_num_seq`, `_maybe_board_to_vec`, `_coerce_features`, `_snap_to_dict_safe`, `_snap_from_dict_safe`, `_pattern_centroid_safe`, `_pattern_to_dict_safe`, `_dict_to_pattern_safe`, `_init_spatio_temporal_state`, `__init__`, `__len__`, `__iter__`, `clear`, `extend`, `append`, `add_snap`, `_update_timing`, `_update_space_from_obj`, `append_with_context`, `_append_any`, `add_text`, `add_knowledge_snap`, `ask_knowledge`, `feature_centroid`, `score_resonance`, `_cos`, `to_dict`, `from_dict`, `as_blob`, `from_blob`, `__repr__`, `short_info`, `save_chain`, `load_chain`

### ENV (direkt im Modul referenziert)

- `OROMA_SNAPCHAINS`, `OROMA_SNAPCHAIN_ATTACH_STDERR`, `OROMA_SNAPCHAIN_LOGLEVEL`, `OROMA_SNAPCHAIN_TRACE_APPEND`, `OROMA_SNAPCHAIN_TRACE_SERIALIZE`

### Persistenz / Tabellen (konzeptuell)

- Diese Module sind primär Daten-/Logikobjekte; DB-Persistenz erfolgt üblicherweise über `core.sql_manager` (und ggf. DBWriter im Single-Writer Modus).

### Betriebsinvarianten / Failure Modes

- `SCHEMA_VERSION` ist Formatmarker (metadata['version']), nicht Projektversion.

- Space/Timing sind optional: fehlendes `spatial_index` degradiert sauber.



## SnapIndexer – `core/snap_indexer.py`

### Rolle

- Flat Index Bridge: MetaSnap → `snap_index` Upsert via deterministischem Fingerprint (`meta:` + sha256(canon_json)).

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** —

- **Funktionen:** `_sha256_hex`, `fingerprint_meta`, `_snap_index_columns`, `_payload_mode`, `_build_meta_payload_minimal`, `_build_meta_payload_full`, `index_meta_snap`

### ENV (direkt im Modul referenziert)

- `OROMA_SNAP_INDEX_PAYLOAD_MODE`

### Persistenz / Tabellen (konzeptuell)

- `snap_index`: Upsert auf `fingerprint` (optional bridge fields `ref_table/ref_id`).

### Betriebsinvarianten / Failure Modes

- Schema-Drift robust: Spalten via `PRAGMA table_info` ermittelt; optionale Felder nur setzen, wenn vorhanden.



## Calc→SnapChain – `core/calc_to_snapchain.py`

### Rolle

- Transfer-Bridge: Calculator Task/Result → SnapChain Row (`origin="calc/result"`) via deterministischer Vektor-Kodierung.

### Klassen / Public API (aus Code extrahiert)

- **Klassen:** —

- **Funktionen:** `_env_bool`, `_env_int`, `_json_loads_safe`, `_json_dumps_compact`, `_infer_type`, `_infer_skill`, `_first_two_ints`, `_norm_tanh`, `_one_hot`, `_build_v`, `_fetch_task`, `_fetch_result`, `_metasnap_upsert`, `record_from_db`

### ENV (direkt im Modul referenziert)

- `OROMA_CALC_METASNAP_AGG`, `OROMA_CALC_SNAPCHAINS`, `OROMA_CALC_SNAP_EVERY`, `OROMA_CALC_SNAP_VDIM`

### Persistenz / Tabellen (konzeptuell)

- `snapchains`: Insert einer Row mit `origin="calc/result"` und Blob `{kind:"calc/result", v:[...], ...}`.

- Optional: `meta_snaps` Aggregation (wenn `OROMA_CALC_METASNAP_AGG=true`).

### Betriebsinvarianten / Failure Modes

- Deterministische Kodierung (kein LLM nötig) – Calculator darf bei Fehlern nicht crashen (best-effort).




---

# Band B – DB & Persistence (Detail)

> Quelle: System-ZIP `oroma_20260418_133357_with_db.zip` (Stand 2026-04-18)

## Überblick

ORÓMA nutzt SQLite als primäre Persistenzschicht (oroma.db; plus optionale Neben-DBs wie stats/knowledge/registry). 
Der DB-Layer ist so gestaltet, dass er **24/7 edge-stabil** bleibt: konsistente PRAGMAs, kurze Transaktionen, lock-retry, 
und optional ein **Single-Writer-Funnel (DBWriter)**, der Burst-Writes seriell abarbeitet.

### Zielprinzipien

- **Additiv & non-destructive:** Schema-Migrationen sind idempotent (`CREATE IF NOT EXISTS`, defensive ALTER).
- **Lock-Disziplin:** Writer-Lock + Retry-Fenster; optional DBWriter für serielle Writes.
- **Keine stillen Writes:** Fehlerpfade sind sichtbar (Logs/State); bei Strict-Mode keine lokalen Bypass-Writes.

## 1) SQLManager – `core/sql_manager.py`

### Rolle

- Autoritative SQLite-Schicht: Connection-Setup, dict-row_factory, schema ensure, Write-Helper (Hotpaths), Lock-Retry.

### Klassen / API (aus Code extrahiert)

- **Klassen:** `_ClosingConnection`, `SqlManager`
- **Funktionen:** `get_base_dir`, `get_db_path`, `_row_factory`, `_env_bool`, `_env_int`, `_dbw_enabled`, `_dbw_timeout_ms`, `__exit__`, `_dbw_enabled`, `_dbw_strict_local_writes`, `_dbw_is_managed_db`, `get_conn`, `conn_cm`, `_is_lock_error`, `_run_with_lock_retry`, `_db_writelock_path`, `writer_lock`, `_holder_preview`, `_schema_should_skip`, `_schema_mark_done`, `__init__`, `_conn`, `execute`, `insert_and_get_id`, `_do`, `fetchone`, `fetchall`, `_ensure_episodic_tables`, `_cols`, `_count_rows`, `_migrate_transfer_snaps`, `_col_exists`, `ensure_calculator_json_columns`, `ensure_schema`, `_exec_schema`, `insert_snapchain`, `_do_once`, `insert_snap_index`, `_do_once`, `fetch_snap_index_by_fingerprint`, `insert_metric`, `_do_once`, `insert_transfer_snap`, `insert_calculator_task`, `insert_calculator_result`, `insert_scicalc_result`, `insert_empathy_snap`, `insert_coverage`, `insert_coverage_30d`, `insert_replay_log`, … (+35)

### Connection-Policy (WAL/busy_timeout) – Kernaussagen

- `get_conn(...)` setzt `PRAGMA busy_timeout` und (default) `journal_mode=WAL`, `synchronous=NORMAL`.
- RowFactory liefert **dict rows** (`{"col": value, ...}`) → robust für UI/Tools.
- `_ClosingConnection` stellt sicher, dass `with get_conn(): ...` die Connection am Ende **wirklich schließt** (Lock-Vermeidung).

### Writer Lock & Retry

- `writer_lock(kind, timeout_sec)` kombiniert In-Process Lock + optionales interprocess `flock` Lockfile.
- Hotpath-Writes nutzen `_run_with_lock_retry(..., retry_sec)` bis `OROMA_DB_LOCK_RETRY_SEC`.

### DBWriter Strict Local Writes (Stage C)

- Wenn `OROMA_DBW_ENABLE=1` und `OROMA_DBW_STRICT_LOCAL_WRITES=1`:
  - lokale Connections zu **managed DBs** werden **read-only** geöffnet (`mode=ro`).
  - Ziel: kein lokales Bypass-Schreiben an DBWriter vorbei.
  - Managed DBs werden per Basename erkannt: `oroma.db`, `stats.db`, `knowledge.db`, `registry.db`.

### Tabellen (direkt aus `ensure_schema()` extrahiert)

- **Tabellen (28):** `audio_student_pairs`, `calculator_results`, `calculator_tasks`, `coverage_log`, `coverage_log_30d`, `curiosity_log`, `curriculum_state`, `dream_state`, `empathy_snaps`, `episode_events`, `episodes`, `episodic_metrics`, `hypotheses`, `meta_snaps`, `metrics`, `models`, `object_nodes`, `object_relations`, `policy_rules`, `quality_history`, `replay_log`, `rewards_log`, `rules`, `scicalc_results`, `setcalc_log`, `snap_index`, `snapchains`, `transfer_snaps`

**Kurzzuordnung (praktisch):**
- `snapchains`, `snap_index`, `meta_snaps`, `transfer_snaps` → Memory/Index/Transfer
- `policy_rules`, `rules` → Policy & Regelarchiv
- `metrics`, `quality_history`, `rewards_log`, `curiosity_log`, `replay_log` → Telemetrie/Learning
- `episodes`, `episode_events`, `episodic_metrics` → Episodic Memory
- `calculator_tasks/results`, `scicalc_results`, `setcalc_log` → Calculator/SciCalc/SetCalc
- `object_nodes`, `object_relations` → ObjectGraph (inkl. synaptic relations)
- `empathy_snaps`, `coverage_log*` → Empathy/Coverage
- `curriculum_state`, `dream_state`, `hypotheses` → Curriculum/Dream/Research

### Indizes (aus `ensure_schema()` extrahiert)

- **Indizes:** `idx_episode_events_episode_ts`, `idx_episodic_metrics_episode_ts`, `idx_metrics_key_ts`, `idx_object_nodes_kind`, `idx_object_nodes_label`, `idx_object_relations_a`, `idx_object_relations_b`, `idx_object_relations_rel`, `idx_object_relations_ts`, `idx_quality_history_snap_ts`, `idx_replay_log_chain_id`, `idx_replay_log_ts_run`, `idx_setcalc_log_ts`, `idx_snap_src`, `idx_snap_ts`, `idx_snapchains_origin`, `idx_snapchains_status`, `ix_snap_index_ref`, `ix_snap_index_ts`

### Wichtige ENV (direkt in `sql_manager.py` referenziert)

- `OROMA_BASE`, `OROMA_BASE_DIR`, `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM`, `OROMA_DBW_CLIENT_TIMEOUT_MS_UI`, `OROMA_DBW_ENABLE`, `OROMA_DBW_STRICT_LOCAL_WRITES`, `OROMA_DBW_TIMEOUT_MS`, `OROMA_DB_BUSY_TIMEOUT_MS`, `OROMA_DB_LOCK_RETRY_SEC`, `OROMA_DB_PATH`, `OROMA_DB_TIMEOUT_SEC`, `OROMA_DB_WAL`, `OROMA_DB_WRITELOCK_TIMEOUT_SEC`, `OROMA_DB_WRITE_FLOCK`, `OROMA_SCHEMA_CACHE`

## 2) DBWriter Daemon – `core/db_writer.py`

### Rolle

- Lokaler Single-Writer (UNIX socket): serialisiert SQL-Writes, reduziert `database is locked`, liefert Ops-Statistiken (Queue/Top-Tags).

### Klassen / API

- **Klassen:** `_Req`, `DBWriterDaemon`
- **Funktionen:** `_env_int`, `_env_str`, `_env_bool`, `_socket_path`, `_socket_perm_spec`, `_apply_socket_perms`, `_base_dir`, `_db_paths_map`, `_normalize_db_name`, `_allowed_dbs`, `_ensure_parent_dir`, `_recv_exact`, `_recv_frame`, `_send_frame`, `_prio`, `_dejsonify_param`, `_dejsonify_params`, `_dejsonify_params_list`, `__init__`, `__lt__`, `__init__`, `_open_db`, `_get_conn`, `_bind_socket`, `serve_forever`, `shutdown`, `_enqueue`, `_state_payload`, `_client_loop`, `_exec_loop`, `main`, `_sig`

### Protokoll (High-Level)

- Framed JSON Requests/Responses.
- Unterstützte Ops (serverseitig): `ping`, `state`, `exec`, `executemany`, `transaction`.
- Queue/Backpressure: Max Queue + optional Drop low-priority.

### Allowlist & DB-Mapping

- `OROMA_DBW_ALLOW_DBS` steuert, welche DB-Namen angenommen werden (typisch: `oroma,stats,knowledge,registry`).
- DB-Pfade werden über `OROMA_DB_PATH` und `OROMA_*_DB_PATH` aufgelöst.

### Wichtige ENV (direkt in `db_writer.py` referenziert)

- `OROMA_BASE`, `OROMA_BASE_DIR`, `OROMA_DBW_ALLOW_DBS`, `OROMA_DBW_DROP_LOWPRIO`, `OROMA_DBW_LOG_SLOW_MS`, `OROMA_DBW_QUEUE_MAX`, `OROMA_DBW_SOCKET`, `OROMA_DBW_SOCKET_GROUP`, `OROMA_DBW_SOCKET_MODE`, `OROMA_DBW_SOCKET_USER`, `OROMA_DB_BUSY_TIMEOUT_MS`, `OROMA_DB_PATH`, `OROMA_KNOWLEDGE_DB_PATH`, `OROMA_REGISTRY_DB_PATH`, `OROMA_STATS_DB_PATH`

## 3) DBWriter Client – `core/db_writer_client.py`

### Rolle

- Client für den DBWriter-Socket (thread-safe I/O, timeouts), genutzt von `sql_manager` und weiteren Core-Modulen.

### Klassen / API

- **Klassen:** `DBWriterClient`
- **Funktionen:** `_jsonify_param`, `_jsonify_params`, `_jsonify_params_list`, `_jsonify_stmts`, `_env_int`, `_env_str`, `_env_bool`, `_sock_path`, `__init__`, `_send_frame`, `_recv_exact`, `_recv_frame`, `_connect`, `_reset_socket`, `request`, `_request_impl`, `enabled`, `_client`, `ping`, `state`, `exec_write`, `exec_lastrowid`, `exec`, `executemany`, `transaction`

### Timeouts (typisch)

- UI (kurz) vs. Dream/Worker (lang) via ENV getrennt.

### Wichtige ENV (direkt in `db_writer_client.py` referenziert)

- `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM`, `OROMA_DBW_CLIENT_TIMEOUT_MS_UI`, `OROMA_DBW_ENABLE`, `OROMA_DBW_SOCKET`

## 4) Betriebs-Check (praktisch)

- **Wenn DBWriter aktiv:** `db_writer_client.ping()` und `db_writer_client.state()` sollten ok sein.
- **Wenn Strict-Mode aktiv:** lokale Writes auf managed DBs schlagen sichtbar fehl (read-only), was gewollt ist.
- **Wenn Locks auftreten:** lock-retry Fenster (`OROMA_DB_LOCK_RETRY_SEC`) und Orchestrator/Single-Writer prüfen.


---

# Band C – Dream & Consolidation (Detail)

> Quelle: System-ZIP `oroma_20260418_133357_with_db.zip` (Stand 2026-04-18)

## Überblick

Der ORÓMA-„Dream“-Strang ist die Offline-Konsolidierung: Replay/Mutation/Pruning/Graph-Ableitungen laufen **nicht** im Live-Pfad, 
sondern in kontrollierten Jobs mit Lock-Guard und Timeouts. Dadurch bleibt das System 24/7 stabil, während sich Gedächtnis und Regeln weiterentwickeln.

### Zielprinzipien

- **RunLock gegen Parallelität:** kein zweiter Dream-Lauf parallel (Timer/Manuell/Orchestrator).
- **Batch + Timeout:** harte Laufzeitbudgets statt Endlosschleifen.
- **Best-effort Module:** optionale Subsysteme dürfen fehlen, ohne Dream zu crashen.
- **Non-destructive:** keine „blind deletes“; pruning/decay ist kontrolliert.

## 1) DreamWorker – `core/dream_worker.py`

### Rolle

- Zentraler Offline-Worker: verarbeitet SnapChains und erzeugt abgeleitete Artefakte/Logs (MetaSnaps, Rewards, Policy/Rules, Object/Scene Graph, Forgetting/Prune).

### Klassen / API (aus Code extrahiert)

- **Klassen:** `RewardEngine`, `EpisodicMemory`, `ExplainEngine`, `_RunLock`, `DreamWorker`
- **Funktionen:** `__init__`, `evaluate`, `__init__`, `store`, `__init__`, `trace`, `_env_bool`, `_env_float`, `_env_int`, `_log_reward_best_effort`, `_is_num_seq`, `_as_float_list`, `_board_to_vec`, `_extract_feats_from_snap_dict`, `_extract_vectors_from_pattern_dict`, `_coerce_json_to_snapchain`, `_list_recent_snap_paths`, `_load_chain_from_path`, `_chain_cursor_key`, `_iter_recent_chains`, `_accept`, `_maybe_yield`, `__init__`, `acquire`, `release`, `__init__`, `stop`, `_dream_state_path`, `_load_dream_state`, `_save_dream_state`, `_dream_state_update`, `_clear_replay_state`, `_phase_deadline_ts`, `_phase_budget_remaining_s`, `_phase_defs`, `run`, `_budget_remaining_s`, `_phase`, `_budget_reached`, `_research_loop`, … (+25)

### Locking / Run-Disziplin

- File-Lock (RunLock) via `OROMA_DREAM_LOCK`: wenn Lock belegt → Run wird übersprungen (kein Blockieren).
- Dream State wird in `dream_state`/`metrics` persistiert (Resume/Progress).

### DB/Tables (aus SQL im Code sichtbar)

- Reads: `snapchains`, `scenegraphs`, `object_nodes`, `object_relations`, `rewards_log`, `dream_state`.
- Writes: `metrics`, `rewards_log`, `policy_rules`, `meta_snaps`, `dream_state`.

### ENV (direkt im Modul referenziert)

- `OROMA_DBW_ENABLE`, `OROMA_DREAM_ATTACH_STDERR`, `OROMA_DREAM_FORGETTING_BATCH_SIZE`, `OROMA_DREAM_FORGETTING_MAX_RUNTIME_S`, `OROMA_DREAM_LOCK`, `OROMA_DREAM_LOG_REWARDS`, `OROMA_DREAM_MAX_RUNTIME_S`, `OROMA_DREAM_OBJECTGRAPH`, `OROMA_DREAM_OBJECT_EXTRACTOR`, `OROMA_DREAM_PRUNE_MAX_RUNTIME_S`, `OROMA_DREAM_REPLAY_BATCH_SIZE`, `OROMA_DREAM_REPLAY_MAX_RUNTIME_S`, `OROMA_DREAM_REPLAY_PROGRESS_EVERY`, `OROMA_DREAM_ROTATE_MAX_HEAVY`, `OROMA_DREAM_RUN_MODE`, `OROMA_DREAM_SCENEGRAPH`, `OROMA_DREAM_STATE_PATH`, `OROMA_DREAM_STDERR_LEVEL`, `OROMA_DREAM_USE_REWARDLOGGER`, `OROMA_ENABLE_METASNAP`, `OROMA_FORGET_DECAY_RATE`, `OROMA_FORGET_FLUSH_ITEMS`, `OROMA_FORGET_FLUSH_SEC`, `OROMA_FORGET_THRESHOLD`, `OROMA_LOG_DIR`, `OROMA_LOG_ROTATE_BACKUPS`, `OROMA_LOG_ROTATE_BYTES`, `OROMA_OBJECTGRAPH_MAX_GRAPHS`, `OROMA_OBJECTGRAPH_MIN_QUALITY`, `OROMA_OBJECTGRAPH_SRC_NS`, `OROMA_OBJECTGRAPH_TARGET_NS`, `OROMA_OBJECT_EXTRACTOR_MAX_GRAPHS`, `OROMA_OBJECT_EXTRACTOR_NAMESPACES`, `OROMA_PTZ_ATT_POLICY_DREAM_ENABLE`, `OROMA_PTZ_ATT_POLICY_MAX_ROWS`, `OROMA_PTZ_ATT_POLICY_NEG_THR`, `OROMA_PTZ_ATT_POLICY_POS_THR`, `OROMA_PTZ_MOTION_POLICY_DREAM_ENABLE`, `OROMA_PTZ_MOTION_POLICY_MAX_ROWS`, `OROMA_PTZ_MOTION_POLICY_NEG_THR`, `OROMA_PTZ_MOTION_POLICY_POS_THR`, `OROMA_PTZ_PROBE_POLICY_DREAM_ENABLE`, `OROMA_PTZ_PROBE_POLICY_MAX_ROWS`, `OROMA_PTZ_PROBE_POLICY_NEG_THR`, `OROMA_PTZ_PROBE_POLICY_POS_THR`, `OROMA_ROOT`, `OROMA_SCENEGRAPH_GROUP_SIZE`, `OROMA_SCENEGRAPH_MAX_CHAINS`, `OROMA_SCENEGRAPH_MIN_QUALITY`, `OROMA_SCENEGRAPH_NAMESPACE`, `OROMA_SCENEGRAPH_ORIGIN`, `OROMA_SNAPCHAINS`, `OROMA_SSL_LIGHT`, `OROMA_STATE_DIR`

### Failure Modes / Guards

- Timeout-Guards für Replay/Prune/Forgetting-Pfade (harter Abbruch statt Hang).
- LogGuard: Fehler sind sichtbar, aber gedrosselt (keine Log-Explosion).
- Orchestrator-Safety: Dream soll nur einmal laufen (Orchestrator vs oneshots).

## 2) Forgetting / Decay – `core/forgetting.py`

### Rolle

- Kleine Utility für gewichtetes Vergessen/Decay; wird typischerweise vom DreamWorker benutzt.

### API (aus Code extrahiert)

- **Funktionen:** `_hash_blob`, `decay_snaps`, `compress_snaps`, `_is_hex16_blob`, `merge_to_meta`, `nightly_forgetting`
### ENV

- `OROMA_FORGET_COMPRESS_MAX`, `OROMA_FORGET_DECAY_RATE`, `OROMA_FORGET_META_BATCH`, `OROMA_FORGET_THRESHOLD`, `OROMA_SNAPCHAIN_DIR`

## 3) Regelarchiv – `core/regelarchiv.py`

### Rolle

- Versioniertes `rules` Archiv: add/update/activate/deactivate, export marking, prune. Grundlage für Explainability und für „Policy → Rules“ Exportpfade.

### API (aus Code extrahiert)

- **Funktionen:** `_dbw_enabled`, `_dbw_timeout_ms`, `_dbw_exec_write`, `_dbw_transaction`, `_clamp01`, `_policy_weight_from_q`, `_ensure_schema`, `add_rule`, `update_rule`, `deactivate_rule`, `activate_rule`, `mark_exported`, `get_rule`, `list_rules`, `list_for_export`, `count`, `reset_export_flags`, `upsert_policy`, `upsert`, `save_rule`, `prune`, `_selftest`
### ENV

- `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM`, `OROMA_DBW_ENABLE`

### DBWriter

- Modul enthält DBWriter-Write-Helper: wenn `OROMA_DBW_ENABLE=1`, werden Writes bevorzugt über den Single-Writer ausgeführt.

## 4) Mutation – `core/mutation.py`

### Rolle

- Kontrollierte Variation (Jitter/Mutation) für Regeln/Ketten – typischer Dream-Input, um Exploration/Robustheit zu erhöhen.

### API (aus Code extrahiert)

- **Funktionen:** `_now_ts`, `_ensure_rules_schema`, `_ensure_audit_schema`, `select_rules_for_mutation`, `_bounded`, `mutate_weight`, `mutate_rule`, `_persist_rule_update_cur`, `_persist_mutation_audit_cur`, `apply_mutations_and_persist`, `mutate_chain`, `_selftest`

## 5) NMR Synaptic Plasticity – `core/nmr_synaptic_plasticity.py`

### Rolle

- Dream-Job: synaptische Relationen als `object_relations` relation='synaptic', Gewicht in `confidence`, Zusatz-JSON in `notes`.

### API / ENV

- **Funktionen:** `_env_int`, `_env_float`, `_env_bool`, `_get_last_checkpoint_ts`, `_set_checkpoint_ts`, `_table_has_column`, `_fetch_episodes`, `_fetch_episode_events`, `_cooc_inc`, `_decay_factor`, `_norm_from_hebb`, `run_plasticity_once`
- **ENV:** `OROMA_NMR_SYN_ENABLE`, `OROMA_NMR_SYN_EVENTS_PER_EP`, `OROMA_NMR_SYN_HALF_LIFE_SEC`, `OROMA_NMR_SYN_LR`, `OROMA_NMR_SYN_MAX_EPISODES_PER_RUN`, `OROMA_NMR_SYN_MIN_EP_TS_GAP_SEC`, `OROMA_NMR_SYN_WINDOW`

## 6) Episodic Memory – `core/episodic.py`

### Rolle

- Episode-Tabellen + Events + Recall-Helper. Nutzt SQLite (schema-aware) und ist ein wiederverwendbarer Memory-Layer.

### API / ENV

- **Funktionen:** `_now`, `_json`, `_from_json`, `_to_vec`, `_l2`, `ensure_schema`, `create_episode`, `save_episode`, `_next_event_index`, `add_event`, `finalize_episode`, `get_episode`, `_load_event_centroids`, `recall_similar`, `_selftest`, `synapse_graph`, `_cos_sim`, `_add_edge`
- **ENV:** `OROMA_BASE`, `OROMA_DB_`, `OROMA_MAX_RECALL_EVENTS`

## 7) Langzeitgedächtnis (LTM) – `core/langzeitgedaechtnis.py`

### Rolle

- Langzeit-Store/Index über SnapChains (optional Annoy/FAISS; fallback Cosine). Für Similarity/Recall im großen Speicher.

### Klassen / API / ENV

- **Klassen:** `LangzeitGedaechtnis`
- **Funktionen:** `_kpi`, `_cosine`, `_round_vec`, `_chain_centroid`, `_normalize_for_hash`, `_hash_chain`, `__init__`, `_init_annoy`, `_init_faiss`, `save_snapchain`, `load_snapchain`, `_add_to_index`, `search_similar`, `list_recent`, `stats`, `init_default_memory`
- **ENV:** `OROMA_ANNOY_REBUILD`, `OROMA_DB_PATH`, `OROMA_MEMORY_DIM`

## 8) MetaSnap – `core/meta_snap.py`

### Rolle

- Abstrakte Meta-Aggregation: label + sources + score, merge/decay, JSON/Blob-Support. Wird im Dream-Kontext häufig als Verdichtungsartefakt genutzt.

### Klassen / API / ENV

- **Klassen:** `MetaSnap`
- **Funktionen:** `_now_i`, `_bounded`, `_stable_fingerprint`, `_pack_json`, `_unpack_json`, `__post_init__`, `_compute_fingerprint`, `add_source`, `add_sources`, `add_tag`, `rescore`, `decay`, `merge_from`, `touch`, `to_dict`, `from_dict`, `to_json`, `from_json`, `as_blob`, `from_blob`, `__repr__`, `short_info`, `_selftest`
- **ENV:** `OROMA_LOG_LEVEL`


---

# Band D – Runtime & Ops (Detail)

> Quelle: System-ZIP `oroma_20260418_133357_with_db.zip` (Stand 2026-04-18)

## Überblick

Band D beschreibt den **Betriebs-Stack**: Entry (`run_oroma.py`), Live-Agent (`core/agent_loop.py`), Circadian DAY/DREAM, 
Orchestrator-Serienjobs (`tools/oroma_orchestrator.py`) und systemd Units. Fokus: **headless Stabilität**, **kein Double-Execution**, 
und klare Failure/Restart Pfade.

## 1) Entry / Engine – `run_oroma.py`

### Rolle

- Produktions-Entry: startet Flask UI, initialisiert DeviceHub (best effort), startet AgentLoop-Thread, optional Circadian/Dream-Bridge.

- Registriert UI-Blueprints guarded (`safe_register`): Importfehler dürfen Boot nicht killen.

### API (aus Code extrahiert)

- **Funktionen:** `log`, `safe_register`, `_imp`, `_start_agent`, `_stop_agent`, `_bool_env`, `_external_camera_provider_active`, `_try_init_devicehub`, `_warmup_status_once`, `_compute_luma_from_frame`, `_start_luma_sampler`, `loop`, `_stop_luma_sampler`, `_build_light_sensor`, `_start_dream`, `_stop_dream`, `_circ_callback`, `_write_phase_file`, `run`, `patched_set_mode`, `instance`, `_sig`

### Wichtige ENV (direkt referenziert)

- `FLASK_ACCESS_LOG_LEVEL`, `FLASK_RUN_HOST`, `FLASK_RUN_PORT`, `FLASK_THREADED`, `OROMA_AGENT_DT`, `OROMA_AGENT_ENABLED`, `OROMA_AUDIO_ALWAYS_ON`, `OROMA_AUDIO_ENABLE`, `OROMA_AUDIO_SNAPS`, `OROMA_BASE`, `OROMA_CIRCADIAN_ENABLED`, `OROMA_DEVICEHUB_AUTOSTART`, `OROMA_DEVICEHUB_STATUS_WARMUP`, `OROMA_DEVICEHUB_STATUS_WARMUP_SEC`, `OROMA_DREAM_ENABLED`, `OROMA_DREAM_INTERVAL`, `OROMA_FLASK_ACCESS_LOG_LEVEL`, `OROMA_FLASK_THREADED`, `OROMA_LIGHT_CAMERA_INTERVAL`, `OROMA_LIGHT_MAX`, `OROMA_LIGHT_MIN`, `OROMA_LIGHT_SOURCE`, `OROMA_LOG_LEVEL`, `OROMA_LOG_ROTATE_BACKUPS`, `OROMA_LOG_ROTATE_BYTES`, `OROMA_PHASE_PATH`, `OROMA_PICAR_CAMERA`, `OROMA_RUN_ATTACH_STDERR`, `OROMA_RUN_STDERR_LEVEL`

### Ops-Invarianten

- UI Token kann optional sein (`OROMA_UI_TOKEN`): wenn leer → UI offen (Designentscheidung).
- AgentLoop und DeviceHub dürfen bei Teilfehlern nicht den Prozess crashen (best effort).

## 2) Live Agent – `core/agent_loop.py`

### Rolle

- Tick-basierter Hook-Runner (Realtime). Hooks liefern episodic/diagnostics/curriculum/self-listening etc.

### API (aus Code extrahiert)

- **Funktionen:** `register_event_listener`, `unregister_event_listener`, `inject_event`, `_replay_logger_enabled`, `_heartbeat`, `_heartbeat_maybe_async`, `_run`, `register_hook`, `unregister_hook`, `get_registered_hooks`, `_nudge_thread_hook`, `_social_resonance_hook`, `audio_snaptoken_hook`, `_persist_event_trace`, `_default_event_listener`, `_replay_logger_listener`, `_loop`, `start`, `stop`, `status`

### Wichtige ENV

- `OROMA_AGENT_DT`, `OROMA_AGENT_ENABLED`, `OROMA_AGENT_HEARTBEAT`, `OROMA_AGENT_HEARTBEAT_ASYNC`, `OROMA_AGENT_LOGLEVEL`, `OROMA_AUDIO_SNAPS`, `OROMA_AV_SNAPS`, `OROMA_CROSSMODAL_LINKS`, `OROMA_ENABLE_COVERAGE`, `OROMA_ENABLE_EMPATHY`, `OROMA_EVENT_TRACE`, `OROMA_EVENT_TRACE_ORIGIN`, `OROMA_EVENT_TRACE_WEIGHT`, `OROMA_REPLAY_LOGGER`, `OROMA_REPLAY_NS`, `OROMA_VISION_INFER`

### Ops-Invarianten

- Hooks müssen fail-safe sein; Fehler werden gesammelt/gedrosselt, ohne den Loop zu stoppen.

## 3) Circadian DAY/DREAM – `core/circadian_controller.py`

### Rolle

- Automatischer Phasenwechsel über Light (0..100) mit Delay + Hysterese; Clock-Fallback optional.

### Klassen / API

- **Klassen:** `CircadianController`
- **Funktionen:** `__init__`, `start`, `stop`, `update_config`, `force_mode`, `_run`, `_read_light`, `_set_mode`, `get_status`, `fake_sensor`, `cb`

### ENV

- `OROMA_CIRCADIAN_HYSTERESIS`, `OROMA_CIRCADIAN_POLL_SEC`, `OROMA_DAY_START_H`, `OROMA_LOG_DIR`, `OROMA_LOG_LEVEL`, `OROMA_NIGHTMODE_DELAY_MINUTES`, `OROMA_NIGHTMODE_LIGHT_THRESHOLD`, `OROMA_NIGHT_START_H`

## 4) Orchestrator – `tools/oroma_orchestrator.py`

### Rolle

- Serielle Job-Ausführung (statt viele parallele oneshots): reduziert SQLite Lock-Kollisionen, standardisiert State/Timeouts.

### Klassen / API

- **Klassen:** `_GlobalLock`
- **Funktionen:** `_env_bool`, `_env_int`, `_env_float`, `_env_hhmm`, `_now_ts`, `_today_ymd`, `_month_ym`, `_ensure_dir`, `_read_phase`, `_phase_allows_dream`, `_load_state`, `_atomic_write_json`, `__init__`, `__enter__`, `__exit__`, `_run_cmd`, `_should_run_interval`, `_mark_ran`, `_dream_state_path`, `_load_dream_state`, `_dream_should_continue`, `_should_run_daily`, `_mark_daily`, `_mark_daily_fail`, `_clear_daily_fail`, `_should_run_monthly`, `_mark_monthly`, `_base_env`, `run_due_jobs`, `_run`, `main`

### ENV (groß; Kern-Subset)

- `OROMA_DREAM_STATE_PATH`, `OROMA_LOG_DIR`, `OROMA_ORCH_C4_DAILY_AT`, `OROMA_ORCH_C4_EXPLORE_GAMES`, `OROMA_ORCH_C4_POLICY_GAMES`, `OROMA_ORCH_CHESS2_CANON_COOP_DAILY_AT`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_DAILY_AT`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_DAILY_AT`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EPS`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EPS_BLACK`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EPS_WHITE`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EXPLORE_GAMES`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EXPLORE_MOVES_BLACK`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_EXPLORE_MOVES_WHITE`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_MAX_PLIES`, `OROMA_ORCH_CHESS2_CANON_COOP_KING_TERRITORY_POLICY_GAMES`, `OROMA_ORCH_CHESS2_CANON_DAILY_AT`, `OROMA_ORCH_CHESS2_CANON_EPS`, `OROMA_ORCH_CHESS2_CANON_EPS_BLACK`, `OROMA_ORCH_CHESS2_CANON_EPS_WHITE`, `OROMA_ORCH_CHESS2_CANON_EXPLORE_GAMES`, `OROMA_ORCH_CHESS2_CANON_EXPLORE_MOVES_BLACK`, `OROMA_ORCH_CHESS2_CANON_EXPLORE_MOVES_WHITE`, `OROMA_ORCH_CHESS2_CANON_MAX_PLIES`, `OROMA_ORCH_CHESS2_CANON_POLICY_GAMES`, `OROMA_ORCH_CHESS2_DAILY_AT`, `OROMA_ORCH_CHESS2_EPS`, `OROMA_ORCH_CHESS2_EPS_BLACK`, `OROMA_ORCH_CHESS2_EPS_WHITE`, `OROMA_ORCH_CHESS2_EXPLORE_GAMES`, `OROMA_ORCH_CHESS2_EXPLORE_MOVES_BLACK`, `OROMA_ORCH_CHESS2_EXPLORE_MOVES_WHITE`, `OROMA_ORCH_CHESS2_MAX_PLIES`, `OROMA_ORCH_CHESS2_POLICY_GAMES`, `OROMA_ORCH_CHESS_DAILY_AT`, `OROMA_ORCH_CHESS_EXPLORE_GAMES`, `OROMA_ORCH_CHESS_POLICY_EXPORT_AT`, `OROMA_ORCH_CHESS_POLICY_GAMES`, `OROMA_ORCH_CHESS_POLICY_TRAIN_AT`, `OROMA_ORCH_CTF_DAILY_AT`, `OROMA_ORCH_CTF_EXPLORE_GAMES`, `OROMA_ORCH_CTF_POLICY_GAMES`, `OROMA_ORCH_DAILY_JITTER_MIN`, `OROMA_ORCH_DAILY_RETRY_MIN`, `OROMA_ORCH_DREAM_CHAIN_ENABLED`, `OROMA_ORCH_DREAM_CHAIN_MAX_RUNS`, `OROMA_ORCH_DREAM_CHAIN_TOTAL_BUDGET_S`, `OROMA_ORCH_DREAM_REQUIRE_PHASE`, `OROMA_ORCH_ENABLE_ARCHIVE`, `OROMA_ORCH_ENABLE_C4_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS2_CANON_COOP_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS2_CANON_COOP_KING_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS2_CANON_COOP_KING_TERRITORY_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS2_CANON_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS2_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS_DAILY_RUN`, `OROMA_ORCH_ENABLE_CHESS_POLICY_EXPORT`, `OROMA_ORCH_ENABLE_CHESS_POLICY_TRAIN`, `OROMA_ORCH_ENABLE_CROSSMODAL_LINKER`, `OROMA_ORCH_ENABLE_CTF_DAILY_RUN`, … (+124)

### Orchestrator Mode Flag

- Flag-Datei: `/opt/ai/oroma/.use_orchestrator`
  - Wenn vorhanden: viele Worker-Services sind via `ConditionPathExists=!…/.use_orchestrator` deaktiviert.
  - Orchestrator ist dann **Single Executor** für Dream/Energy/Policy/ExportGate/etc.

## 5) Logging Guard – `core/log_guard.py`

### Rolle

- Rate-Limited Logging (`log_suppressed`) um 24/7 Logs kontrollierbar zu halten.

### API / ENV

- **Funktionen:** `_env_int`, `_default_interval_s`, `_safe_stderr`, `_coerce_level`, `log_suppressed`, `log_once`, `install_global_excepthooks`, `_sys_hook`, `_thread_hook`
- **ENV:** `OROMA_LOG_GUARD_INTERVAL_S`

## 6) systemd Units (aus ZIP)

### Services/Timer (Liste)

- `oroma-archive.service`
- `oroma-archive.timer`
- `oroma-cam-train.service`
- `oroma-cam-train.timer`
- `oroma-db-writer-watchdog.service`
- `oroma-db-writer-watchdog.timer`
- `oroma-db-writer.service`
- `oroma-dream.service`
- `oroma-dream.timer`
- `oroma-energy.service`
- `oroma-energy.timer`
- `oroma-exportgate.service`
- `oroma-exportgate.timer`
- `oroma-forgetting.service`
- `oroma-forgetting.timer`
- `oroma-gap-miner.service`
- `oroma-gap-miner.timer`
- `oroma-health.service`
- `oroma-health.timer`
- `oroma-kpi.service`
- `oroma-kpi.timer`
- `oroma-orchestrator.service`
- `oroma-policy.service`
- `oroma-policy.timer`
- `oroma-ramflush.service`
- `oroma-ramflush.timer`
- `oroma-replay.service`
- `oroma-replay.timer`
- `oroma-selftest.service`
- `oroma-selftest.timer`
- `oroma-social.service`
- `oroma-social.timer`
- `oroma-stats-repair.service`
- `oroma-stats-repair.timer`
- `oroma-stats.service`
- `oroma-stats.timer`
- `oroma-train-snake.service`
- `oroma-train-snake.timer`
- `oroma-usb-kernelwatch.service`
- `oroma-usb-kernelwatch.timer`
- `oroma-usb-noautosuspend.service`
- `oroma.service`

### Orchestrator-Gating Units

Folgende Units enthalten `ConditionPathExists=!/opt/ai/oroma/.use_orchestrator` (damit Orchestrator Single-Executor bleibt):

- `oroma-archive.service`
- `oroma-dream.service`
- `oroma-energy.service`
- `oroma-exportgate.service`
- `oroma-forgetting.service`
- `oroma-gap-miner.service`
- `oroma-gap-miner.timer`
- `oroma-kpi.service`
- `oroma-orchestrator.service`
- `oroma-policy.service`
- `oroma-ramflush.service`
- `oroma-ramflush.timer`
- `oroma-social.service`
- `oroma-stats-repair.service`
- `oroma-stats.service`
- `oroma-train-snake.service`

### Kernidee

- `oroma.service` = Engine/UI/Live.
- `oroma-orchestrator.service` = serielle Jobs.
- Worker timers laufen, aber oneshots werden im Orchestrator-Modus übersprungen.


---

# Band E – Wrapper & Devices (Detail)

> Quelle: System-ZIP `oroma_20260418_133357_with_db.zip` (Stand 2026-04-18)

## Überblick

Wrapper sind ORÓMAs **Backend-Abstraktion** für Vision/Audio/TTS/NPU/PTZ/PiCar. 
Sie kapseln harte Abhängigkeiten (Hailo/DeGirum/onnxruntime/cv2/sounddevice) und ermöglichen 
Fallbacks, ohne den Core zu fragmentieren. `OromaWrapper` dient als Router/Facade.

## 1) Wrapper-Übersicht (`wrappers/`)

- `__init__.py`
- `audio_wrapper.py`
- `degirum_wrapper.py`
- `dynamic_wrapper.py`
- `gstreamer_wrapper.py`
- `hailo_wrapper.py`
- `oroma_wrapper.py`
- `picar_wrapper.py`
- `ptz_controller.py`
- `sensor_ir_front.py`
- `text_wrapper.py`
- `tts_wrapper.py`
- `vision_wrapper.py`

## 2) OromaWrapper – `wrappers/oroma_wrapper.py`

### Rolle

- Zentrale Facade/Router: wählt Backend (CPU/Hailo/DeGirum), holt Frames aus DeviceHub, liefert `detect()` und `embed()` APIs.

- Optional: Light-Reactivity Worker (DARK/BRIGHT Schwellen) als Runtime-Feature.

### Klasse / API

- **Klassen:** `OromaWrapper`
- **Funktionen:** `_dbw_enabled`, `_ensure_stats_points_schema_best_effort`, `_bool_env`, `_try_import`, `_load_backends`, `get_instance`, `__init__`, `_choose_backend`, `_maybe_log_infer_ms`, `_bootstrap_infer_ms_once`, `_decode_jpeg_frame_best_effort`, `_get_frame_from_hub`, `_get_luma_from_hub`, `detect`, `embed`, `asr_stream`, `tts_say`, `enable_light_reactivity`, `_worker`, `disable_light_reactivity`, `get_light_level`

### ENV (aus Code)

- `OROMA_BACKEND_PREF`, `OROMA_DBW_ENABLE`, `OROMA_EMBED_BACKEND`, `OROMA_FAILOVER`, `OROMA_LIGHT_BRIGHT_THR`, `OROMA_LIGHT_DARK_THR`, `OROMA_LIGHT_HOLD`, `OROMA_LIGHT_INTERVAL_SEC`, `OROMA_VISION_FRAME_MAX_AGE_SEC`, `OROMA_VISION_INFER_BOOTSTRAP`, `OROMA_VISION_INFER_BOOTSTRAP_VALUE`, `OROMA_VISION_INFER_METRIC_EVERY_SEC`, `OROMA_WRAPPER_DUMMY_VERBOSE`

## 3) Vision – `wrappers/vision_wrapper.py`

### Rolle

- Vision Backend: Frame-Quelle (`OROMA_VISION_SOURCE`), Backend (`OROMA_VISION_BACKEND`), Gerät (`OROMA_VISION_DEVICE`), Größe/FPS/FourCC.

- Unterstützt ONNX Model Pfade (Labels/Model). Nutzt OpenCV Parameter (buffersize/fourcc).

### API

- **Klassen:** `VideoSnapFeatures`, `VisionWrapper`
- **Funktionen:** `_env`, `as_vector`, `as_metadata`, `as_dict`, `__init__`, `_init_inference`, `start`, `stop`, `__del__`, `is_alive`, `_open_capture`, `_loop`, `_put_queue`, `_extract_features`, `_infer_tags_best_effort`, `_render_overlay`, `get_overlay_frame`, `get_latest_features`, `make_snap`, `snapshot`, `snapshot_jpeg`, `snapshot_png`, `build_from_env`, `_colorfulness_0_1`, `embed`, `detect`, `_selftest`

### ENV

- `OROMA_OPENCV_FOURCC`, `OROMA_VISION_BACKEND`, `OROMA_VISION_BUFFERSIZE`, `OROMA_VISION_DEVICE`, `OROMA_VISION_FOURCC`, `OROMA_VISION_FPS`, `OROMA_VISION_H`, `OROMA_VISION_ONNX_LABELS`, `OROMA_VISION_ONNX_MODEL`, `OROMA_VISION_SOURCE`, `OROMA_VISION_W`

## 4) Audio/ASR – `wrappers/audio_wrapper.py`

### Rolle

- ASR Routing: Whisper (Python) und Whisper.cpp (Bin) per ENV; optional DeviceHub Integration.

- Feature-Parameter (Win/Hop) und Audio-Ringbuffer Parameter werden aus ENV genommen.

### Klasse / API

- **Klassen:** `AudioWrapper`
- **Funktionen:** `_env_bool`, `_env_int`, `_env_float`, `_env_str`, `_is_executable`, `_parse_whispercpp_stdout`, `_whispercpp_transcribe_wav_bytes`, `_safe_rms`, `_zcr`, `_pitch_librosa`, `_pitch_acf`, `_log_power_spectrum`, `_features_from_signal`, `__init__`, `start`, `stop`, `__del__`, `_pick_device_index`, `_sd_callback`, `_start_fallback_stream`, `_stop_fallback_stream`, `_fallback_concat`, `_loop`, `_put_q`, `get_features`, `get_audio_level`, `read_audio`, `record_wav`, `play_pcm`, `play_wav`, `_ensure_whisper`, `transcribe`, `asr_stream`, `_atexit`

### ENV

- `OROMA_ASR_BACKEND`, `OROMA_AUDIO_BLOCK_MS`, `OROMA_AUDIO_CH`, `OROMA_AUDIO_FEATURE_HOP`, `OROMA_AUDIO_FEATURE_WIN`, `OROMA_AUDIO_INPUT_NAME`, `OROMA_AUDIO_OUTPUT_NAME`, `OROMA_AUDIO_RING_SEC`, `OROMA_AUDIO_SR`, `OROMA_AUDIO_STUDENT_ENABLED`, `OROMA_AUDIO_WRAPPER_USE_HUB`, `OROMA_LOG_LEVEL`, `OROMA_WHISPERCPP_BIN`, `OROMA_WHISPERCPP_ENABLE`, `OROMA_WHISPERCPP_MODEL`, `OROMA_WHISPERCPP_THREADS`, `OROMA_WHISPERCPP_TIMEOUT_SEC`, `OROMA_WHISPER_ENABLE`, `OROMA_WHISPER_LANG`, `OROMA_WHISPER_MODEL`

## 5) TTS – `wrappers/tts_wrapper.py`

### Rolle

- Offline-TTS Backend Selection, robust gegen Missing Backends; optional Ausgabe über DeviceHub.

### API / ENV

- **Funktionen:** `_which`, `_env_int`, `_env_float`, `_env_bool`, `_remember_tts_failure`, `_clear_tts_failure`, `_log_tts_missing_ratelimited`, `_tts_retry_allowed`, `_norm_rate`, `_norm_volume`, `_norm_text`, `_split_sentences`, `_chunk_text`, `_get_hub`, `_hub_play_wav`, `list_voices`, `set_voice`, `set_rate`, `set_volume`, `speak_chunk`, `synth_to_wav_bytes`, `stop`, `__init__`, `list_voices`, `set_voice`, … (+36)
- **ENV:** `OROMA_LOG_LEVEL`, `OROMA_TTS_BACKEND`, `OROMA_TTS_DISABLE_ON_MISSING`, `OROMA_TTS_MAX_CHARS`, `OROMA_TTS_MISSING_LOG_INTERVAL_SEC`, `OROMA_TTS_RATE`, `OROMA_TTS_RETRY_SEC`, `OROMA_TTS_USE_DEVICE_HUB`, `OROMA_TTS_VOICE`, `OROMA_TTS_VOLUME`

## 6) NPU Backends – Hailo/DeGirum

### `wrappers/hailo_wrapper.py`

- Rolle: Hailo NPU Integration (Import-guarded). ENV wird hier nicht direkt referenziert (Konfig meist über Außenlayer/Runtime).

### `wrappers/degirum_wrapper.py`

- Rolle: DeGirum Runtime wrapper.

- API: `__init__`, `connect`, `load_model`, `infer`, `close`

## 7) GStreamer – `wrappers/gstreamer_wrapper.py`

- Rolle: RTSP/File Quellen via GStreamer Pipeline; mehrere Helferklassen.

- **Klassen:** `VideoConfig`, `GStreamerVideo`, `AudioConfig`, `GStreamerAudio`, `AVConfig`, `GStreamerWrapper`

## 8) PTZ – `wrappers/ptz_controller.py` + `core/ptz_attention_loop.py`

### Rolle

- `wrappers/ptz_controller.py`: Abstraktion der PTZ Controls (Pan/Tilt/Zoom + Limits + Cooldowns).

- `core/ptz_attention_loop.py`: Attention/Scan/Fixate Loop (Motion/Sharpness/Audio) mit vielen Tunables.

### ENV (PTZ Controller)

- `OROMA_PTZ_ALLOW_FOCUS`, `OROMA_PTZ_COOLDOWN_MS`, `OROMA_PTZ_LIST_CTRLS_RETRY`, `OROMA_PTZ_LOG_LEVEL`, `OROMA_PTZ_PAN_MAX`, `OROMA_PTZ_PAN_MIN`, `OROMA_PTZ_REFRESH_BACKOFF_SEC`, `OROMA_PTZ_TILT_MAX`, `OROMA_PTZ_TILT_MIN`, `OROMA_PTZ_ZOOM_MAX`, `OROMA_PTZ_ZOOM_MIN`, `OROMA_STATE_DIR`

### ENV (PTZ Attention Loop – sehr umfangreich)

- `OROMA_BASE`, `OROMA_ORCH_ENABLE_PTZ_ATTENTION`, `OROMA_ORCH_INT_PTZ_ATTENTION`, `OROMA_PTZ_ATTENTION_AUDIO_GUARD`, `OROMA_PTZ_ATTENTION_BORED_SEC`, `OROMA_PTZ_ATTENTION_DRY_RUN`, `OROMA_PTZ_ATTENTION_ENSURE_CAM`, `OROMA_PTZ_ATTENTION_FIXATE_SEC_MAX`, `OROMA_PTZ_ATTENTION_FIXATE_SEC_MIN`, `OROMA_PTZ_ATTENTION_LOG_REPEAT_SEC`, `OROMA_PTZ_ATTENTION_MAX_FRAME_AGE`, `OROMA_PTZ_ATTENTION_MOTION_H`, `OROMA_PTZ_ATTENTION_MOTION_HIGH`, `OROMA_PTZ_ATTENTION_MOTION_LOW`, `OROMA_PTZ_ATTENTION_MOTION_W`, `OROMA_PTZ_ATTENTION_NUDGE_PAN`, `OROMA_PTZ_ATTENTION_NUDGE_TILT`, `OROMA_PTZ_ATTENTION_ONCE_ALLOW_ENSURE_CAM`, `OROMA_PTZ_ATTENTION_ONCE_FASTPATH`, `OROMA_PTZ_ATTENTION_ONCE_MAX_FRAME_AGE`, `OROMA_PTZ_ATTENTION_ONCE_SKIP_ATTENTION_GAIN`, `OROMA_PTZ_ATTENTION_ONCE_SNAPSHOT_FIRST`, `OROMA_PTZ_ATTENTION_ONCE_SNAPSHOT_TIMEOUT`, `OROMA_PTZ_ATTENTION_ONCE_SOFT_DEADLINE_SEC`, `OROMA_PTZ_ATTENTION_ORIENT_COOLDOWN`, `OROMA_PTZ_ATTENTION_ORIENT_THR`, `OROMA_PTZ_ATTENTION_SCAN_BINS_X`, `OROMA_PTZ_ATTENTION_SCAN_BINS_Y`, `OROMA_PTZ_ATTENTION_SCAN_STEP`, `OROMA_PTZ_ATTENTION_SETTLE_MS`, `OROMA_PTZ_ATTENTION_SNAPSHOT_FIRST`, `OROMA_PTZ_ATTENTION_SNAPSHOT_TIMEOUT`, `OROMA_PTZ_ATTENTION_SNAPSHOT_URL`, `OROMA_PTZ_ATTENTION_STATE_PATH`, `OROMA_PTZ_ATTENTION_UI_TOKEN`, `OROMA_PTZ_ATTENTION_USE_HUB`, `OROMA_PTZ_ATTENTION_ZOOM_ENABLE`, `OROMA_PTZ_ATTENTION_ZOOM_STEP`, `OROMA_PTZ_ATT_CURIOSITY_CACHE_SEC`, `OROMA_PTZ_ATT_CURIOSITY_ENABLE`, `OROMA_PTZ_ATT_CURIOSITY_FIXATE_SHRINK`, `OROMA_PTZ_ATT_CURIOSITY_ORIENT_COOLDOWN_SEC`, `OROMA_PTZ_ATT_CURIOSITY_ORIENT_ENABLE`, `OROMA_PTZ_ATT_CURIOSITY_ORIENT_P`, `OROMA_PTZ_ATT_CURIOSITY_ORIENT_THR`, `OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_MAX`, `OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_MIN`, `OROMA_PTZ_ATT_CURIOSITY_POLICY_EPS_SCALE`, `OROMA_PTZ_ATT_CURIOSITY_SCAN_STEP_MAX`, `OROMA_PTZ_ATT_CURIOSITY_SCAN_STEP_SCALE`, `OROMA_PTZ_ATT_CURIOSITY_SIGNAL_MAX`, `OROMA_PTZ_ATT_CURIOSITY_WINDOW_SEC`, `OROMA_PTZ_ATT_CURIOSITY_ZOOM_IN_ORIENT_ENABLE`, `OROMA_PTZ_ATT_CURIOSITY_ZOOM_IN_ORIENT_THR`, `OROMA_PTZ_ATT_POLICY_ENABLE`, `OROMA_PTZ_ATT_POLICY_EPS`, `OROMA_PTZ_ATT_POLICY_MIN_N`, `OROMA_PTZ_ATT_SAMPLE_GAP_MS`, `OROMA_PTZ_ATT_SHARP_DIV`, `OROMA_PTZ_ATT_W_MOTION`, … (+56)

## 9) PiCar – `wrappers/picar_wrapper.py`

- Rolle: PiCar Kamera/Control Integration + Safety (Deadman/Safety toggles).

- **ENV:** `OROMA_OPENCV_DEV`, `OROMA_PICAM_FPS`, `OROMA_PICAM_H`, `OROMA_PICAM_W`, `OROMA_PICAR_CAMERA`, `OROMA_PICAR_DEADMAN`, `OROMA_PICAR_DISTANCE`, `OROMA_PICAR_GPIO_SLEEP`, `OROMA_PICAR_GPIO_TIMEOUT`, `OROMA_PICAR_SAFETY`

## 10) Text + IR Front Sensor

### `wrappers/text_wrapper.py`

- Rolle: Text Input Adapter/Bridge.

### `wrappers/sensor_ir_front.py`

- Rolle: IR Front Sensor Interface (GPIO/Distance).

## 11) DeviceHub & CameraHub (Core Schnittstellen)

- `core/device_hub.py` und `core/camera_hub.py` sind die Hardware-Singletons, die Wrapper konsistent nutzen sollten.

- OromaWrapper holt Frames bevorzugt über den Hub (cached) statt direkte Device-Opens.


---

# Band F – UI / Dashboard (Detail)

> Quelle: System-ZIP `oroma_20260418_133357_with_db.zip` (Stand 2026-04-18)

## Überblick

Die ORÓMA-UI ist eine Flask-App mit vielen Blueprints (Games/Tools/Health/Learning/Video/ASR/Replay/etc.). 
Zentraler Einstieg ist `ui/flask_ui.py`; weitere Blueprints werden im Engine-Entry (`run_oroma.py`) guarded registriert.

## 1) Auth / Token Guard (ui/flask_ui.py)

### Prinzip

- Token-Guard ist **optional**: wenn `OROMA_UI_TOKEN` leer ist → token-free Modus.
- Wenn gesetzt → nur `/api/*` ist geschützt.
- Auth kann über Header (`Authorization: Bearer …`), Query (`?token=`) oder Cookie (`OROMA_UI_TOKEN`) erfolgen.

### Kern-Endpunkte

- `/` (Home / Dashboard)
- `/api/auth/status` [GET]
- `/api/auth/ping` [GET,POST]
- `/api/auth/logout` [POST]

## 2) Blueprint-Katalog (Auszug, real aus UI-Modulen)

Die folgenden Blueprints existieren in der ZIP; hier sind die wichtigsten mit Prefix und Beispielrouten:

### health_ui.py → blueprint `health` prefix `/health`

- `/health/api/health`
- `/health/api/health`
- `/health/api/health/logs`
- `/health/api/health/logs`
- `/health/api/history`
- `/health/api/selftest`

### control_ui.py → blueprint `control` prefix `/control`

- `/control/api/logs` [GET]
- `/control/api/logtail` [GET]
- `/control/api/selftest` [POST]
- `/control/api/service/restart` [POST]
- `/control/api/service/start` [POST]
- `/control/api/service/stop` [POST]

### learning.py → blueprint `learning` prefix `/learning`


### video_ui.py → blueprint `video` prefix `/video`

- `/video/api/devices`
- `/video/api/edge_debug_state`
- `/video/api/ptz/command` [GET,POST]
- `/video/api/ptz/coverage`
- `/video/api/ptz/status`
- `/video/api/usb_alert`

### replay_ui.py → blueprint `replay` prefix `/replay`

- `/replay/api/pause` [POST]
- `/replay/api/resume` [POST]
- `/replay/api/start` [POST]
- `/replay/api/status`
- `/replay/api/stop` [POST]
- `/replay/`

### replay_api.py → blueprint `replay_api` prefix `/replay/api`

- `/replay/api/chains` [GET]
- `/replay/api/debug/config` [GET]
- `/replay/api/healthz` [GET]
- `/replay/api/logs` [GET]
- `/replay/api/pause` [POST]
- `/replay/api/resume` [POST]

### models_ui.py → blueprint `models` prefix `/models`


### export_ui.py → blueprint `export` prefix `/export`

- `/export/api/download/<fname>`
- `/export/api/export` [POST]
- `/export/api/list`
- `/export/api/upload` [POST]
- `/export/`

### episodic_ui.py → blueprint `episodic_ui` prefix `/episodic`

- `/episodic/api/create` [POST]
- `/episodic/api/get/<int:eid>` [GET]
- `/episodic/api/list` [GET]
- `/episodic/api/similar` [POST]
- `/episodic/api/vision/events/<int:eid>` [GET]
- `/episodic/api/vision/list` [GET]

### gaps_ui.py → blueprint `gaps` prefix `/gaps`

- `/gaps/api/list` [GET]
- `/gaps/api/summary` [GET]
- `/gaps/` [GET]

### empathy_ui.py → blueprint `empathy_ui` prefix `/empathy`

- `/empathy/`

### coverage_ui.py → blueprint `coverage_ui` prefix `/coverage`

- `/coverage/`

### curriculum_ui.py → blueprint `curriculum_ui` prefix `/curriculum`

- `/curriculum/api/advance` [POST]
- `/curriculum/api/state` [GET]
- `/curriculum/`

### scicalc_ui.py → blueprint `scicalc` prefix `/scicalc`

- `/scicalc/api/bar` [POST]
- `/scicalc/api/eval` [POST]
- `/scicalc/api/limit` [POST]
- `/scicalc/api/pie` [POST]
- `/scicalc/api/plot` [POST]
- `/scicalc/api/roots` [POST]

### setcalc_ui.py → blueprint `setcalc` prefix `/setcalc`

- `/setcalc/`

### calculator_ui.py → blueprint `calculator_ui` prefix `/calculator`

- `/calculator/`

### asr_ui.py → blueprint `asr` prefix `/asr`

- `/asr/api/start` [POST]
- `/asr/api/status`
- `/asr/api/stop` [POST]
- `/asr/`

### asr2_ui.py → blueprint `asr2_ui` prefix `/asr2`

- `/asr2/api/run` [POST]
- `/asr2/api/status` [GET]
- `/asr2/`

### audio_ui.py → blueprint `audio_ui` prefix `/`

- `/`

### knowledge_ui.py → blueprint `knowledge` prefix `/knowledge`

- `/knowledge/` [GET,POST]
- `/knowledge/search` [GET,POST]

### chat_ui.py → blueprint `chat` prefix `/chat`


### dream_ui.py → blueprint `dream` prefix `/dream`

- `/dream/api/start` [POST]
- `/dream/api/status`
- `/dream/api/stop` [POST]
- `/dream/`

### synapses_ui.py → blueprint `synapses_ui` prefix `/`

- `/api/data` [GET]
- `/` [GET]

### scenegraph_ui.py → blueprint `scenegraph` prefix `/`

- `/scenegraph/api/auto`
- `/scenegraph/api/get/<int:graph_id>`
- `/scenegraph/api/list`
- `/scenegraph`

### objects_ui.py → blueprint `objects` prefix `/`

- `/objects`
- `/objects/`

### tictactoe_ui.py → blueprint `tictactoe_ui` prefix `/tictactoe`

- `/tictactoe/api/diag`
- `/tictactoe/api/flush` [POST]
- `/tictactoe/api/mode` [POST]
- `/tictactoe/api/move` [POST]
- `/tictactoe/api/policy` [GET,POST]
- `/tictactoe/api/reset` [POST]

### connect4_ui.py → blueprint `connect4_ui` prefix `/connect4`

- `/connect4/api/drop` [POST]
- `/connect4/api/move` [POST]
- `/connect4/api/reset` [POST]
- `/connect4/api/set_mode` [POST]
- `/connect4/api/set_speed` [POST]
- `/connect4/api/state`

### chess_ui.py → blueprint `chess_ui` prefix `/chess`

- `/chess/api/counters`
- `/chess/api/daily_status`
- `/chess/api/dbdiag` [GET]
- `/chess/api/flush` [POST,GET]
- `/chess/api/mode` [POST]
- `/chess/api/move` [POST]

### chess2_ui.py → blueprint `chess2_ui` prefix `/chess2`

- `/chess2/api/aggroLevel` [POST]
- `/chess2/api/daily_status` [GET]
- `/chess2/api/kiProfile` [POST]
- `/chess2/api/matchup_status` [GET]
- `/chess2/api/mode` [POST]
- `/chess2/api/move` [POST]

### snake_ui.py → blueprint `snake` prefix `/games/snake`


### ptz_arena_ui.py → blueprint `ptz_arena` prefix `/ptz_arena`

- `/ptz_arena/api/mode` [POST]
- `/ptz_arena/api/reset` [POST]
- `/ptz_arena/api/settings` [POST]
- `/ptz_arena/api/state` [GET]
- `/ptz_arena/api/toggle` [POST]
- `/ptz_arena/` [GET]

### picar_ui.py → blueprint `picar` prefix `/picar`

- `/picar/api/cmd` [POST]
- `/picar/api/mode` [POST]
- `/picar/api/speed` [POST]
- `/picar/api/status`
- `/picar/`

## 3) UI-Module (vollständige Liste)

Alle Python-UI-Module in `ui/` (64 Dateien):

`__init__.py`, `admin.py`, `api.py`, `ask_ui.py`, `asr2_ui.py`, `asr_ui.py`, `audio_ui.py`, `bundle_ui.py`, `calculator_ui.py`, `chat_ui.py`, `chess2_ui.py`, `chess_ui.py`, `classic_memory_game_ui.py`, `connect4_ui.py`, `control_ui.py`, `coverage_ui.py`, `ctf_ui.py`, `curriculum_ui.py`, `dream_ui.py`, `empathy_ui.py`, `episodic_ui.py`, `export_manager.py`, `export_ui.py`, `flappy_ui.py`, `flask_ui.py`, `forgetting_ui.py`, `games_ui.py`, `gaps_ui.py`, `health_ui.py`, `hideseek_ui.py`, `import_manager.py`, `import_ui.py`, `knowledge_ui.py`, `learning.py`, `memory_ui.py`, `memorymaze_ui.py`, `meta_ui.py`, `missions_ui.py`, `models_ui.py`, `objects_ui.py`, `picar_ui.py`, `pong_panel_ui.py`, `pong_ui.py`, `ptz_arena_ui.py`, `ptz_coverage_ui.py`, `ptz_target_ui.py`, `replay_api.py`, `replay_ui.py`, `research_ui.py`, `scenegraph_ui.py`, `scicalc_ui.py`, `selfrec_ui.py`, `selftest_ui.py`, `setcalc_ui.py`, `snake_ui.py`, `stats_ui.py`, `sudoku_ui.py`, `synapses_ui.py`, `tetris_ui.py`, `tictactoe_ui.py`, `tts_ui.py`, `video_ui.py`, `vs_ui.py`, `why_ui.py`

## 4) Templates / Static

- Templates: 59 Dateien (z.B. Video/Replay/Chess/Curriculum).

- Static: 12 Dateien.

## 5) Ops-Hinweis

- Blueprints werden im Boot guarded registriert: fehlende optional Module killen den Start nicht.
- Token-Guard schützt nur `/api/*`, nicht die HTML-Seiten (Design: offline LAN).

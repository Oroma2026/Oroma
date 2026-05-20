<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__roadmap.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

# ORÓMA – Roadmap (konsolidiert)

Stand: 2025-12-25


Ziele, Meilensteine und geplante Iterationen. Alle Roadmap-Schnipsel/Varianten wurden hier zusammengeführt.

## Quellen (konsolidiert)

- `docs/roadmap_2026.md`

- `docs/roadmap.md`

- `docs/history_oroma_referenz_v2_11_patch_roadmap.md`

- `docs/history_oroma_roadmap.md`

- `docs/history_oroma_roadmap_v2_11-3_x.md`

- `docs/history_oroma_roadmap_v2_11_3_x.md`

- `docs/history_oroma_v3_5_roadmap.md`

- `docs/history_projektstruktur_patch_roadmap.md`

- `docs/history_referenz_handbuch_patch_roadmap.md`

- `docs/history_roadmap_v3_0.md`

- `docs/roadmap_nmr_concept.md`

- `docs/roadmap_v3_5_patch_2_0.md`

- `docs/roadmap_v3_5_patch_2_1.md`

- `docs/roadmap_v3_6.md`

- `docs/roadmap_v4_0.md`

---

<a id="docs_roadmap_2026_md"></a>

## Quelle: `docs/roadmap_2026.md`

**Originaltitel:** Datei:   docs/roadmap_2026.md

# Projekt: ORÓMA – KI-JWG-X1
# Stand:   2025-12-13 (Projektstand mit echter DB + ObjectGraph 1.5, ZIP: oroma_20251213_114645_with_db.zip + Live-System)
# Autor:   Jörg Werner + GPT-5.1 Thinking

Zweck
-----
Strategische Roadmap für ORÓMA im Jahr 2026, ausgehend vom realen Zustand:

  • v3.7.x/v3.8-r3 (Roter Faden, DreamWorker 3.x, Empathie, Coverage, RAG, SceneGraph, SnapIndex, DeviceHub-Sensoren)  
  • Laufzeit-DB mit vorbereiteten und **aktiv genutzten** Tabellen für:
      – Vision-Tokens (`snapchains` mit `origin='vision/token'`)  
      – MetaSnaps (`meta_snaps`)  
      – SceneGraphs (`scenegraphs`, Namespaces `scene:auto_meta:*`, `object:auto:vision`)  
      – **ObjectGraph-DB** (`object_nodes`, `object_relations`) + `/objects`-UI v0.8 mit Ego-Netz & Health-Badge  
      – Empathie (`empathy_snaps`)  
      – Coverage (`coverage_log`)  
      – Metrics (`metrics`, `episodic_metrics`)  
      – Audio-Student-Pairs (`audio_student_pairs`)  
      – Episoden (`episodes`, `episode_events`)  
  • Bereits vorhandene, aber noch nicht voll „ausgekostete“ Bereiche:
      – episodisches Gedächtnis (episodes/episode_events + episodic_writer + episodic_ui)  
      – Audio-Student v1 (`core/audio_student.py` + `audio_student_pairs`)  
      – SceneGraph-Stack (`scenegraph_store` + `scenegraph_builder` + `scenegraph_selfcheck`)  
      – **ObjectGraph-Stack 1.5** (`objectgraph_builder` + `object_extractor` + `object_nodes` / `object_relations`  
        + `objectgraph_selfcheck`/`objectgraph_audit`/`objectgraph_dedupe`/`objectgraph_top_objects`  
        + `/objects`-UI v0.8 inkl. Ego-Netz & Health-Status)  
      – DeviceHub + SensorChannels (inkl. IR-Frontsensor)

Hinweis:
  • Die ZIP-DB ist aus Datenschutz-/Größengründen gesampelt. Tabellen wie  
    `snapchains`, `meta_snaps`, `scenegraphs`, `episodes`, `audio_student_pairs`,  
    `object_nodes`, `object_relations`  
    können darin leer oder stark reduziert sein, obwohl sie im Live-System  
    bereits aktiv verwendet werden (siehe `scenegraph_selfcheck`-, `object_extractor`-  
    & `objectgraph_*`-Logs vom 2025-12-09/10/13).

---

# 🧭 ORÓMA – Roadmap 2026

---

## 1. Leitbild für Ende 2026

**Zielbild:**  
Ende 2026 ist ORÓMA ein **stabil laufendes, episodisch denkendes Multimodal-System**, das

- kontinuierlich Vision-, Audio- und Sensorsignale (inkl. IR-Abstand) beobachtet,  
- daraus **SceneGraphs, ObjectGraphs und Episoden** baut (auf Basis der bereits vorhandenen Module),  
- im Dream-Modus alte Snaps komprimiert, Graphen & Objekt-DB aktualisiert und Regeln nachschärft,  
- Audio mithilfe eines **Teacher/Student-Ansatzes** nach und nach „selbst versteht“,  
- sich über einen **Cortex-Mode-Controller** bewusst in verschiedene Denkmodi schaltet,  
- optional ein lokales LLM nutzen kann – ohne den Core zu dominieren.

Kurz gesagt:  
ORÓMA bleibt ein **Erklär- & Lernsystem**, kein „Chatbot mit Kamera“.

---

## 2. Leitprinzipien für alle Schritte

1. **Kern stabil halten**  
   - Keine Re-Write-Orgie von `core/snap*`, `core/dream_worker.py`, `core/roter_faden.py`.  
   - Änderungen als **Hooks, Worker, Tabellen-Erweiterungen**, die auf dem bestehenden Code aufbauen.

2. **Minimal-invasive Erweiterungen**  
   - Neue Features als additive Module:
     - neue Tabellen (in `core/sql_manager.py`),
     - neue Worker (`*_worker.py`, Hintergrund-Loops),
     - neue UI-Gesichter (eigene Blueprints) –  
       aber **kein** Bruch mit der aktuellen v3.7.x/v3.8-Struktur.

3. **Beobachtung vor Kontrolle**  
   - Erst beobachten, loggen, erklären (Metrics, Empathie, SceneGraph, ObjectGraph, Audio-Student),  
   - erst dann Regel-/Policy-Änderungen (Regelarchiv, Policy/Universal-Policy, AutoTuner).

4. **Edge & Offline im Fokus**  
   - DeviceHub, Vision, Audio, RAG, Policies laufen ohne Cloud.  
   - LLM-Integration bleibt optional und austauschbar.

---

## 3. Hauptthemen 2026

### 3.1 Episodisches Gedächtnis 1.0 → 1.5 (Episodes **nutzen**, nicht nur haben)

**Ist-Zustand (Ende 2025, laut ZIP + Code + Live-System):**

- Tabellen `episodes`, `episode_events`, `episodic_metrics` sind im Schema vorhanden (`core/sql_manager.py`).  
- `core/episodic_writer.py` existiert und implementiert:
  - Schreiben von Episoden & Events:
    - Audio-Paare (Audio-Teacher/Student),
    - Vision-Tokens via `insert_cam_token` / `log_vision_cam_token_global`,
    - weitere Eventtypen je nach Hook.
- `ui/episodic_ui.py` + `templates/episodic.html` sind vorhanden (Episoden-Browser angelegt).  
- In der gesampelten ZIP-DB können die Episoden-Tabellen leer oder stark gekürzt sein – im Live-System entstehen bereits Vision-Episoden über die Kamera-Pipeline.

**Ziel 2026:**

- ORÓMA hat **täglich Episoden**, die im realen Betrieb sichtbar sind, z. B.:
  - „Vision-Session 2026-03-14 Abend“  
  - „Snake-Trainingsblock #7“  
  - „Audio-Teacher-Session #3“
- Episoden werden im Dream-Modus gezielt ausgewertet (nicht nur „nebenbei“ geloggt).

**Konkret geplante Schritte:**

1. **EpisodeWriter-Policies schärfen (auf Basis von `core/episodic_writer.py`)**
   - Zeitfenster-Policy (z. B. 30–60 Minuten) + Ereignis-Policy (Game-Start/-Ende, Mission).  
   - klare Label-Konventionen (`type`, `label`, `meta_json`),  
     damit Episoden in der UI sinnvoll gruppiert werden.

2. **Episode-Index aktiv nutzen**
   - `episodes`: Kopf-Datensatz (ts_start, ts_end, typ, label, quality, mood).  
   - `episode_events`: Referenzen auf
     - Snapchains (IDs),
     - Audio-Teacher/Student-Paare,
     - Empathie-/Coverage-Events,
     - SceneGraph-IDs,
     - ObjectGraph-IDs.  
   - bestehende Felder aus `core/episodic_writer.py` und `sql_manager.py` konsequent füllen.

3. **UI: Episoden-Blick ausbauen**
   - `ui/episodic_ui.py`:
     - Liste der letzten Episoden mit Filter (Typ, Label, Zeitraum).  
     - Detailseite: „Was ist in dieser Episode passiert?“
       - Empathie-Verlauf,
       - Coverage-Trend,
       - Rewards,
       - verlinkter SceneGraph-/ObjectGraph-Ausschnitt (falls vorhanden).

4. **Dream-Integration verstärken**
   - DreamWorker 3.x nutzt Episoden für:
     - Priorisierung (z. B. Episoden mit hohem Reward oder vielen Gaps),  
     - retrospektive Analyse („Diese Episode war chaotisch → Regel/Policy prüfen“),  
     - Markierung („relevant für zukünftige Regeln“).

---

### 3.2 NMR 3.75 – Observation-Only Reasoner (2.5D/3D-Weltmodell)

**Ist-Zustand (Ende 2025, nach v3.7.3, Live-System):**

- **SceneGraph-Stack** ist real vorhanden und lauffähig:
  - `core/scenegraph_store.py` (Tabelle `scenegraphs` mit Struktur:  
    `id, ts, namespace, source, quality, graph_json, notes`),
  - `core/scenegraph_builder.py` (Vision-Tokens → MetaSnaps + SceneGraphs),
  - `tools/scenegraph_selfcheck.py` (Health/Stats).

- **DreamWorker 3.3** (v3.7.3-r1) baut im Dream-Modus (über `oroma-dream.service`) in einem Run:

  1. Vision-SceneGraphs aus `snapchains` mit `origin='vision/token'`  
     – Namespace: `scene:auto_meta:vision_token`  
     – Quelle: `source = 'builder:vision_tokens'`.  
  2. **ObjectGraph-Aggregat** auf Basis dieser SceneGraphs:  
     – Namespace: `object:auto:vision`  
     – Quelle: `source = 'auto:object:scene:auto_meta:'`  
     – entsteht durch Aggregation von mehreren SceneGraphs mit Prefix
       `scene:auto_meta:` (typisch: `graphs_used=32` pro Build)  
     – Stats im `graph_json.meta.stats`:
       ▸ `objects`, `object_edges`, `graphs_used`, `nodes_seen`, `edges_seen`,  
       ▸ `source_namespace_prefix='scene:auto_meta:'`.

- **ObjectGraph-DB (ObjectGraph 1.5, normalisierte Ebene):**
  - `core/sql_manager.py` legt `object_nodes` und `object_relations` an.  
  - `core/object_extractor.py` extrahiert aus SceneGraphs/ObjectGraphs:
    - Objektknoten → `object_nodes` (mehrere tausend Nodes),
    - Relationen → `object_relations` (zehntausende Relationen, nach Dedupe reduziert).
  - `tools/objectgraph_selfcheck.py`, `tools/objectgraph_audit.py`, `tools/objectgraph_dedupe.py`,  
    `tools/objectgraph_fix_compressed_links.py`, `tools/objectgraph_top_objects.py`:
    - Health, Integrität, Dedupe, Fix von `compressed_*`, Top-Hubs.
  - `ui/objects_ui.py` + `templates/objects.html` (v0.8):
    - Route `/objects` listet Nodes & Relationen,
    - `kind`-Filter,
    - **Fokus-Knoten mit Ego-Netz** (`focus_id`) – Degree, Relationstypen, Nachbarn,
    - Health-Badge basierend auf `objectgraph_selfcheck` (OK/Warnung/Fehler).

- Tests/Health (Dezember 2025):

  - DreamWorker-Run via systemd:

    ```text
    [INFO] Dream-SceneGraph (origin=vision/token): ok=True graph_id=392 nodes=249 edges=368
    [INFO] Dream-ObjectGraph (src_ns=scene:auto_meta:): ok=True graph_id=393 objects=4603 edges=8390 graphs_used=32
    [INFO] DreamWorker Single-Run beendet
    ```

  - Schema-Check `scenegraphs`:

    ```text
    ['id', 'ts', 'namespace', 'source', 'quality', 'graph_json', 'notes']
    ```

  - ObjectGraph-Selfcheck & Audit:
    - `objectgraph_selfcheck.py`: `object_nodes` & `object_relations` konsistent,  
      SceneGraphs mit `namespace_prefix='object:auto:'`, `graphs_used=32`.  
    - `objectgraph_audit.py`: 1:1-Beziehungen für `compressed_*`-Meta-Knoten  
      → genau eine `meta_to_chain` + eine `chain_to_origin` pro komprimierter Chain.  
    - `objectgraph_dedupe.py`: Duplikate in `object_relations` entfernt.  
    - `objectgraph_fix_compressed_links.py`: fehlende Kanten für `compressed_*` ergänzt.  
    - `/objects`: Health-Badge „ObjectGraph OK“ und Ego-Netze (z. B. Fokus auf Knoten 410 mit Degree ~2000).

**Ziel 2026:**

- ORÓMA hat eine explizite, **semantische** Objekt-/Szenen-Schicht:
  - Objekte wie „Ball“, „Lampe“, „Wand“, „Boden“, „Spielfeld“, „Hand“, „Geräusch X“,  
  - Relationen („vor“, „hinter“, „oben“, „Teil von“, „verursacht“, „in der Nähe von“),  
  - Crossmodal-Links:
    - visuell erkannter „Ball“ ↔ gesprochenes Wort „Ball“ ↔ interne Konzepte/Regeln.
- Diese Schicht bildet die Grundlage für den **Observation-Only Reasoner** (NMR 3.75):
  - Entscheidungen werden aus Beobachtung + Objekt-/Relationswissen begründet.

**Konkret geplante Schritte:**

1. **ObjectGraph 1.5 → Semantik- & Typ-Layer auf der bestehenden Object-DB**

   - Ausbau von `object_nodes`:
     - `kind` konsequent nutzen (z. B. `"object"`, `"scene"`, `"concept"`, `"agent"`),  
     - `meta_json` mit semantischen Tags:
       ▸ Kategorien (z. B. „runde Dinge“, „Beleuchtung“, „Bewegliches Objekt“),  
       ▸ Häufigkeiten, typische Positionen, typische Partner (Co-Occurrence).  
   - Ausbau von `object_relations`:
     - Relationstypen standardisieren / begrenzen (z. B.:
       ▸ `"cooccurs"`, `"part_of"`, `"near"`, `"above"`, `"below"`, `"left_of"`, `"right_of"`),  
     - Confidence & zeitliche Dimension sauber füllen (`ts`, `source_scene_id`).

2. **Heuristische Ontologie & Clustering (NMR-Vorstufe)**

   - Tools/Worker auf Basis der bestehenden Daten:
     - „Top-K häufigste Objekte“ (nach Häufigkeit/Gewicht),  
     - „Stabile Paare/Tripel“ (Object-A, Relation, Object-B mit hoher Support-Zahl),  
     - Clustering von Objekten nach:
       ▸ Ko-Occurrence,  
       ▸ typischer Position im SceneGraph,  
       ▸ typischen Relationen.  
   - Ziel:
     - grobe, aber datengetriebene Ontologie,  
     - keine Hand-Labels, sondern Beobachtungs-basierte Struktur.

3. **„Ball“-Pfad (visuell + auditiv) – Crossmodal-Verknüpfung**

   - Vision:
     - wiederkehrende runde/konvexe Bewegungsmuster im ObjectGraph → Kandidat „Ball“ (`object_nodes`-Eintrag).  
   - Audio:
     - wiederkehrende Worte „Ball“, „Kugel“ in `audio_student_pairs` / Transkripten.  
   - Brücke:
     - Mapping-Feld in `object_nodes.meta_json`, z. B.:
       ▸ `"audio_labels": ["ball", "kugel"]`,  
       ▸ `"text_labels": ["Ball", "Kugel"]`.  
   - Episoden-Verknüpfung:
     - Episoden, in denen Objekt „Ball“ aktiv war + gleichzeitige Audio-Ereignisse → Crossmodal-Evidence.

4. **Explain-Integration (Why-UI + ObjectGraph)**

   - `why_ui` / Explain-Layer erweitern:
     - Erklärungen können explizit auf ObjectGraph-Daten verweisen:
       ▸ „Ich habe nach rechts gespielt, weil Objekt 'Ball' links von der Wand ist  
          und ein Treffer dort wahrscheinlicher ist.“  
   - Verwendung der normalisierten ObjectGraph-Information:
     - `object_nodes`/`object_relations` → menschenlesbare Sätze,  
     - Referenz auf SceneGraph-ID und Episoden.

5. **UI & Tools: ObjectGraph-Explorer**

   - `/objects`-UI weiter ausbauen:
     - Filter nach `kind`, `label`, Relationstyp, Zeitraum,  
     - einfache Übersichten:
       ▸ „Top-Objekte nach Häufigkeit“,  
       ▸ „Top-Relationen“,  
       ▸ „neuste Objekte“.  
   - Erweiterungen der bestehenden Tools:
     - `objectgraph_selfcheck.py`: zusätzlich Semantik-Statistiken (z. B. Anteil „object“ vs. „meta“),  
     - `objectgraph_top_objects.py`: Option, globale Hubs (`vision/token`, `scenegraph:vision_token:hoch`) auszublenden.

**Wirkung**

- Der bereits vorhandene **ObjectGraph 1.5**  
  (SceneGraph-Aggregate + Object-DB + Dedupe/Audit + `/objects`-Ego-Net-UI + Health-Badge)  
  wird zu einem **semantischen Weltmodell** ausgebaut:
  - Beobachtung → SceneGraph → ObjectGraph → Ontologie/Reasoner.  
- ORÓMA lernt, seine Welt nicht nur als Sequenz von Tokens/Szenen zu sehen,  
  sondern als **Netz von Objekten und Beziehungen**, das später mit Sprache/Audio  
  und Regeln verknüpft werden kann – die Basis für deinen „Observation-Only Reasoner“ (NMR 3.75).

---

### 3.3 Audio-Lernen (Teacher/Student) – „Kind lernt Hören & Sprache“

**Ist-Zustand (Ende 2025, laut ZIP + Code):**

- `wrappers/audio_wrapper.py` + `core/device_hub.py`:
  - Audio-Capture über Hub (sr ~16 kHz, Ringbuffer, RMS-Levels).  
- `core/audio_student.py` existiert:
  - implementiert bereits einen Audio-Student-Loop und nutzt die Tabelle `audio_student_pairs`  
    (Schema in `core/sql_manager.py`).  
- Tabelle `audio_student_pairs` ist im ZIP-Snapshot aktuell leer oder gesampelt – d. h.:
  - Infrastruktur & CLI sind vorhanden,
  - ein dauerhafter Teacher/Student-Betrieb im Alltag ist noch im Aufbau.

**Ziel 2026:**

- ORÓMA hat einen aktiven **Teacher/Student-Loop** mit klaren Rollen:
  - Teacher: Whisper / externer ASR liefert „Ground-Truth“-Transkripte.  
  - Student: `core/audio_student.py` + simple Modelle/Heuristiken,  
  - Distanz-Metrik und Curriculum-Logik sorgen für strukturiertes Audio-Lernen.

**Konkret geplante Schritte:**

1. **Audio-Teacher-Worker (neu, auf Basis DeviceHub + Whisper)**

   - Neues Modul `core/audio_teacher_worker.py`:
     - nimmt kurze Audio-Snippets auf (1–3 s),  
     - ruft Whisper/ASR auf,  
     - schreibt `audio_student_pairs`:
       - Teacher-Text,  
       - Features (`feat_json`),  
       - Kontext (`meta_json`, z. B. „Ball gezeigt“, „Lampe gezeigt“).  
   - Integration mit `core/episodic_writer.py`:
     - Teacher-Sessions bekommen eigene Episodenlabels.

2. **Audio-Student-Worker 1.5 (Ausbau von `core/audio_student.py`)**

   - Student „hört“ Pairs aus der DB:
     - berechnet Distanz Teacher↔Student,  
     - schreibt Fortschritt in `episodic_metrics` und/oder `metrics`.  
   - Curriculum-Anbindung:
     - leichte → mittlere → schwere Audio-Aufgaben  
       (Einzelwort → kurze Phrase → einfacher Satz).

3. **Curriculum-Verknüpfung**

   - Curriculum-State erweitern um:
     - Audio-Level (z. B. „Wort“, „Phrase“, „Satz“),  
     - Audio-Aufgaben (z. B. „Verstehe Ball“, „Verstehe rechts/links“),  
     - Verlinkung zu Objekten in der Object-DB („Ball“, „Lampe“ etc.).

4. **UI-Feedback**

   - Kleine Ansicht im Learning-/Episoden-Tab:
     - Liste der Teacher/Student-Paare,  
     - Distanz-Trend,  
     - Play-Buttons (Original/Student),  
     - Verknüpfung zu Object-/SceneGraph („Diese Audio-Aufnahme war während Objekt X aktiv“).

---

### 3.4 Cortex-Mode-Controller – bewusste Denkmodi

**Ist-Zustand:**

- Day/Dream, Selftest, Coverage, Empathie, Missions/AutoTuner existieren.  
- `core/circadian_controller.py` steuert Tag/Nacht über Licht + Zeit.  
- Es gibt systemd-Timer (Dream, Archive, Selftest, Health), aber keinen expliziten „Mode-Controller“.

**Ziel 2026:**

- Ein expliziter **Cortex-Mode-Controller**, der globale Modi verwaltet:

  - **Observation** (Day-Mode / AgentLoop),  
  - **Dream/Consolidation** (Night-Mode / DreamWorker 3.x),  
  - **Selftest/Diagnostics** (Selftest-Timer),  
  - **Curriculum-Training** (z. B. Audio-Student, Math-Tasks),  
  - optional **Missions/Experimente** (Hypothesen, Research-UI).

**Konkret geplante Schritte:**

1. **Mode-Definition (neu)**

   - Kleines Modul `core/cortex_modes.py`:
     - definierte Modi & Submodi,  
     - Mapping: „welche Worker/Timer gehören zu welchem Modus?“.

2. **Mode-Scheduler**

   - Integration mit `circadian_controller`:
     - Tag/Nacht bleibt Grundlage,  
     - innerhalb der Nacht:
       - Zeitfenster für Dream/SceneGraph/ObjectGraph,  
       - Zeitfenster für Audio-Student-Training,  
       - Zeitfenster für Selftest.

3. **Metrics**

   - Logging:
     - wie viel Zeit ORÓMA in welchem Modus verbringt,  
     - wie viele Episoden/Gaps/Rewards pro Modus entstehen.  
   - Grundlage für spätere Mode-Optimierung.

---

### 3.5 LLM-Brücke (optional, Core-schonend)

**Ist-Zustand:**

- `core/llm_runtime.py` existiert (Backend-Slot für lokale/externe LLMs).  
- `core/rag_bridge.py` + `tools/rag_import_sample.py` + `tools/bench_rag.py` bilden einen  
  funktionierenden RAG-Stack (FTS5 + BM25 + optionaler Fusion-Rerank).  
- Ask-/Why-UI (`ui/ask_ui.py`, `ui/why_ui.py`) sind vorhanden.

**Ziel 2026:**

- ORÓMA kann optional LLMs nutzen, um:
  - Antworten aus der eigenen Wissensbasis zu formulieren,  
  - Erklärungen/Übergänge zu glätten,  
  - aber **nicht** den Core zu dominieren oder zu überschreiben.

**Konkret geplante Schritte:**

1. **saubere `llm_runtime`-Konfiguration**

   - ENV-basiert:
     - `OROMA_LLM_BACKEND=none|local|remote`  
     - `OROMA_LLM_MODEL=...`  
   - default = `none` → System bleibt voll funktionsfähig ohne LLM.

2. **Integration in Ask-/Why-UI**

   - LLM als:
     - Übersetzer/Paraphrasierer für bestehende RAG-Antworten,  
     - Erklärungs-Enhancer (auf Basis von DB/SceneGraph/ObjectGraph/Rules).  
   - Wichtig:
     - LLM darf nur auf **internen Fakten** (RAG/DB) aufsetzen, keine Halluzinations-Orakel.

3. **Einsatzgrenzen**

   - Kein „LLM übernimmt Steuerung“.  
   - Jede LLM-Nutzung wird im Metrics-/Explain-Log gekennzeichnet.

---

### 3.6 Robustheit, Tests & Ops

**Ist-Zustand:**

- DB-Schema-Ensure (`python -m core.sql_manager --ensure`) ist integriert.  
- SnapIndex, SceneGraphs, ObjectGraph-DB, RAG-Bench-Tools, SceneGraph-Selfcheck, DeviceHub-Status sind vorhanden.  
- systemd-Timer für Dream, Archive, Selftest, Health sind definiert.  
- DeviceHub liefert Status für Kamera/Audio/Sensoren  
  (inkl. Light-/Sensor-Loop und IR-Frontsensor).

**Ziel 2026:**

- ORÓMA läuft „langweilig stabil“:

  - Logs rotieren,  
  - DB wächst kontrolliert (Sampling/Truncation-Regeln),  
  - Selftests decken Kernfunktionen ab,  
  - Backups sind klar und reproduzierbar.

**Konkret geplante Schritte:**

1. **DB-Hygiene**

   - regelmäßige `VACUUM`-/`ANALYZE`-Strategie (über Timer/Tool),  
   - Monitoring von:
     - DB-Größe,  
     - Anzahl SnapChains, MetaSnaps, Empathie-Snaps, Episodes,  
     - Anzahl SceneGraphs/ObjectGraphs, ObjectNodes/-Relations.

2. **Backup-Strategie**

   - Dokumentation des vorhandenen `backup_oroma_with_db.sh`:
     - Rotation (täglich/wöchentlich),  
     - Notfall-Restore-Schritte.

3. **Selftests erweitern**

   - bestehende Tests (`core/snap.py`, `core/snappattern.py`, `tools/bench_rag.py`,  
     `tools/scenegraph_selfcheck.py`, UI-Tests) ergänzen um:
     - Mini-Episode-Test (episodic_writer + episodic_ui),  
     - Audio-Student-Selftest (falls Hardware verfügbar),  
     - DeviceHub-/Sensor-Health (inkl. IR-Frontsensor),  
     - ObjectGraph-Selfcheck (object_nodes/object_relations).

4. **Monitoring-UI**

   - `health_ui` anreichern:
     - DB-Größe, letzte DreamRuns, letzte SceneGraph-/ObjectGraph-Builds,  
     - Modus-Übersicht (Cortex-Mode-Controller),  
     - Sensor-Health (`device_hub.get_sensor_health()` inkl. `sensors`-Block).

---

## 4. Phasen-Skizze (ohne harte Deadlines)

**Phase 1 – Grundlagen „anstecken“ (frühes 2026)**

- Episoden-Stack (episodic_writer + episodic_ui) aktivieren/nutzen,  
- DB-Hygiene & Selftests stabilisieren,  
- Audio-Student v1 im Alltag testen,  
- ObjectGraph-DB weiter füllen (`object_extractor` regelmäßig laufen lassen),  
- DreamWorker 3.3 + `oroma-dream.service` als zentrale Pipeline für  
  **Replay → Forgetting → SceneGraph → ObjectGraph** etablieren.

**Phase 2 – NMR & SceneGraph/ObjectGraph-Vertiefung**

- Ontologie-/Typ-Layer in `object_nodes`/`object_relations`,  
- Explain-Integration über Objekte/Relationen,  
- erste Crossmodal-Links (z. B. Ball visuell ↔ akustisch).

**Phase 3 – Audio-Teacher/Student & Cortex-Modes**

- Audio-Teacher-Worker,  
- Curriculum mit Audio & Episoden verbinden,  
- Cortex-Mode-Controller implementieren.

**Phase 4 – Feinschliff & optionale LLM-Brücke**

- LLM-Anbindung über `llm_runtime` an Ask-/Why-UI,  
- UI-Verbesserungen (ObjectGraph-Explorer, Episoden-Ansicht),  
- Stabilitäts-/Performance-Tuning (DreamWorker/SceneGraph/ObjectGraph/DeviceHub).

---

## 5. Nicht-Ziele 2026 (bewusst NICHT geplant)

- Kein „Big Bang“-Rewrite von Snap/SnapChain/DreamWorker.  
- Keine Abhängigkeit von einem bestimmten LLM-Anbieter.  
- Keine „AGI-Versprechen“ – Fokus bleibt:
  - Muster verstehen,  
  - Lernen beobachten,  
  - Verhalten erklären.

---

## 6. Persönliches Fazit

Wenn du diese Roadmap 2026 Stück für Stück auf Basis deines aktuellen Stands umsetzt, dann wird ORÓMA:

- **sichtbar episodisch** (Tage, Sessions, Experimente in `episodes`/`episode_events`),  
- **sichtbar objekt-orientiert** (Ball, Lampe, Wand, Geräusch, Szene auf Basis SceneGraph/ObjectGraph + Object-DB),  
- **sichtbar lernend im Audio-Bereich** (Teacher/Student, Distanz-Metriken),  
- und gleichzeitig **stabiler und erklärbarer** (DeviceHub, Sensoren, Selftests, Health-UI, `/objects`-Viewer mit Ego-Netz & Health-Badge).

Genau das, was du ursprünglich wolltest:

> „Verstehen, wie Muster und KI wirklich lernen“ –  
> nicht nur theoretisch, sondern im laufenden System.

<a id="docs_roadmap_md"></a>

## Quelle: `docs/roadmap.md`

🗺️ ORÓMA – Roadmap v1.6 → v3.7.x

Pfad:  docs/roadmap.md  
Stand: 2025-12-10  
Quelle: ZIP → oroma_20251210_225140_with_db.zip + Live-System  
Autor: ORÓMA · KI-JWG-X1

⸻

1. Überblick

ORÓMA ist ein modulares, lokal lauffähiges KI-System für Raspberry Pi 5/6  
(optional Hailo/DeGirum NPU).  
Es verbindet:

- Snap-basierte Verarbeitung (numerisch)
- symbolische Tokens (LLM/Text)
- eine Flask-Weboberfläche (Dashboard, Games, Learning, SceneGraph, Objects, Episoden, Ask/Why …)
- Traum-/Replay-Logik (Circadian Day/Dream, DreamWorker 3.x)
- Meta-Ebenen:
  - MetaSnaps
  - SceneGraphs (2.5D-Szenen, origin ≈ `vision/token`)
  - ObjectGraph + Object-DB (Objects + Relationen über viele Szenen)

Die Entwicklung orientiert sich an menschlichen Reifestufen:

> Baby → Kind → Schüler → Student → Gelehrter → Meister/Forscher → Wissenschaftler → Sozialer Partner

**Aktueller technischer Fokus (Ende 2025):**

- Stabilisierung von **v3.7.x** (Roter Faden, Empathie, Coverage, Curriculum V2)
- **DreamWorker 3.3** als zentrales Nacht-Gehirn (Replay, Vergessen, SceneGraph- und ObjectGraph-Build)
- Integration von **SceneGraph (vision/token)** und **ObjectGraph 1.0 (Scene→Object + Object-DB)** in den Lernzyklus
- saubere Dokumentation des erreichten Reifegrads, ohne v3.8/v4.0 zu „überfrachten“

Für Details zu v3.8-r1/r2/r3 (SnapIndex, RAG, DeviceHub-Sensoren, Episoden)  
siehe **`docs/changelog_full.md`** und **`docs/roadmap_2026.md`**.

⸻

2. Versionsübersicht

| Version | Codename            | Status / Rolle                 | Hauptfeatures |
|--------|----------------------|--------------------------------|---------------|
| v1.6   | Baby                 | Proof of Concept               | Snaps, SnapChains, SQLite-Basis, TicTacToe, Connect4 |
| v1.62  | Kleinkind            | Erweiterung                    | Flask-UI (Skelett), Vision-Wrapper (OpenCV), Export-Tests |
| v1.98  | Jugendphase          | Konsolidierung                 | Snake, Pong, Replay stabilisiert, Circadian Controller (Basis) |
| v2.00  | Jugendlicher         | Architektur + Deployment       | Struktur `/opt/ai/oroma`, systemd, LZG, Dashboard |
| v2.11  | Kind                 | Stabil & produktiv             | Kernmodule, Spiele, Registry, Export/Import, Memory Maze, Hide & Seek |
| v2.20  | Schüler              | Diagnose & Optimierung         | Raum-/Zeit-Kontext, Diagnostics, Auto-Tuner |
| v2.30  | Student              | Agentisches Lernen & Explain.  | Reward, Curiosity, Episoden, Explainability, Synapse-Graph |
| v3.0   | Gelehrter            | Snap+Token-Fusion & RAG        | LLM, Bücherwissen, Replay, DreamWorker 2.x, Export-Gate, PiCar-Safety |
| v3.5   | Meister/Forscher     | Forschung & Meta-Lernen        | MetaSnaps, Mutation-Policy, LZG 2.0, AgentLoop, Research-Basis |
| v3.6   | Wissenschaftler      | Hypothesen & Experimente       | Hypothesen-DB/UI, Experimente, Explainability 2.0 |
| v3.7   | Sozialer Partner     | Empathie & Selbstwahrnehmung   | Empathie-Snaps, ASR Self-Listening/Reflex, Mangel-Speak, Curriculum V2, Learning-UI, **SceneGraph-Store/Builder + (ab v3.7.3) ObjectGraph 1.0** |

> Hinweis:  
> - v3.7.x umfasst Patches (3.7.1–3.7.3), die DreamWorker 3.x, SceneGraph und ObjectGraph-DB integrieren.  
> - v3.8 / v3.8-r1/r2/r3 bilden Feature-Buckets (Regelarchiv, SnapIndex, RAG, DeviceHub, Episoden) und sind im Code/DB bereits angelegt, aber nicht als eigenständige „Marketing-Releases“ ausgespielt.  
> - Diese ROADMAP fokussiert bewusst auf die Achse **v1.6 → v3.7.x**; die 2026-Planung steht separat in `docs/roadmap_2026.md`.

⸻

3. Detaillierte Roadmap (v1.6 → v3.7)

### v1.6 – Proof of Concept (Baby)

- Core: `snap.py`, `snaptoken.py`, `snappattern.py`
- Mini-Games: TicTacToe, Connect4
- Speicher: SQLite-Basis
- Replay-System: rudimentär (Pause/Resume)

⸻

### v1.62 – Erweiterung (Kleinkind)

- UI: Flask-UI Grundgerüst (`/games`)
- Vision: OpenCV-Wrapper
- Export: erste Bundles (`.tar`)

⸻

### v1.98 – Konsolidierung

- Spiele: Snake, Pong
- Replay: stabil (Pause/Resume)
- Circadian: Day/Dream-Basis
- Export/Import: tar-Bundles

⸻

### v2.00 – Architektur & Deployment (Jugendlicher)

- Einheitliche Struktur `/opt/ai/oroma/`
- `run_oroma.py`, systemd-Services, (optional) nginx
- Core: SnapChains, Mutation, Regelarchiv
- LZG: Langzeitgedächtnis (Recall/Promotion)
- UI: Replay, Models-Tab

⸻

### v2.11 – Stabil & produktiv (Kind)

- Circadian Controller: Tag/Nacht + Lux
- Wrapper: Vision (ONNX/Hailo), Audio (Whisper/Vosk), PiCar, TTS, GStreamer
- Mini-Games: TicTacToe, Connect4, Snake, Pong, Memory Maze 2033, Hide & Seek
- Export/Import: Delay (30 Tage) + Qualitätsschwelle
- Registry: `models` (inkl. `.hef`)
- VectorDB-Switch: >100k SnapChains
- UI: Games, Models, Replay, Lernkurven

⸻

### v2.20 – Schüler (Raum-/Zeitkontext & Diagnostik)

- Snaps: `time_since_prev_ms`, Raumrelationen
- SnapChains: Delta-Zeit & Raumkontext
- Diagnostics: Coverage, Novelty, Confidence, Time-to-Goal
- Auto-Tuner: sanftes Replay-Tuning, ε-Regler, adaptives Pruning
- UI: Gaps-Tab mit Badge (LOW/MED/HIGH)
- Neue Module: `spatial_index.py`, `diagnostics.py`, `auto_tuner.py`

⸻

### v2.30 – Student (Agentisches Lernen & Explainability)

- Reward: Spiele + Wrapper-Adapter
- Curiosity/Surprise: intrinsische Motivation
- Predictor: Top-K nächste Snaps (Hit@K)
- Episoden: Vektor-Index
- Explainability: `why_decision()`
- Synapses-Graph: UI-Visualisierung
- UI: Learning-Curve, Episoden-Browser, Why-Tab
- Neue Module: `reward.py`, `curiosity.py`, `predictor.py`, `episodic.py`, `explain.py`

⸻

### v3.0 – Gelehrter (Snap+Token-Fusion & RAG)

- Fusion: numerische + symbolische Ebene (Snaps ↔ Tokens)
- LLM: GGUF lokal, Hybrid-Option
- RAG-Bridge: Text-/Buchimport in SnapChains
- Replay: Wiedergabe (Pause/Resume/Stop, Export)
- DreamWorker 2.x: Batch-Optimierung im Nachtmodus
- Export-Gate: Policy (≥30 Tage + Qualität)
- PiCar-Safety: Deadman-Switch, Soft-Limits
- UI: Replay, Dream, Knowledge, Ask, Video, Chat, ASR2

⸻

### v3.5 – Meister/Forscher (Autonome Forschung)

- MetaSnaps: Abstraktion über Chains & Episoden
- Mutation-Policy: Regeln & Chains variieren (Qualitäts-Tracking)
- Langzeitgedächtnis 2.0: Annoy/FAISS
- AgentLoop: Hook-System (episodic, diagnostics, curiosity …)
- DreamWorker 3.0: Meta-Chains + Mutation + Export
- Research-Basis: Vorbereitung Hypothesen & Experimente

⸻

### v3.6 – Wissenschaftler (Hypothesen & Experimente)

- Hypothesen-DB & UI: `core.sql_manager.hypotheses`, Research-Tab
- Experimente & Selbsttests: definierbare Testszenarien, Ergebnisse in DB
- Explainability 2.0: kausale + narrative Erklärungen
- Learning-Integration: Hypothesen/Ergebnisse fließen in Charts ein

⸻

### v3.7 – Sozialer Partner (Empathie, Self-Listening & Graphen)

**Empathie-Layer**

- Tabelle `empathy_snaps` in `oroma.db`
- Empathie-/Mood-Signale mit Timestamp & Score
- optionaler Empathie-View im UI (Zeitreihen)

**ASR Self-Listening / Reflex**

- `ui/asr_ui.py` ruft `core.asr_reflex.process_text(txt)` auf
- Intents wie `repeat`, `stop`, `status` → Reaktion auf eigene Sprache
- ENV:
  - `OROMA_ASR_REFLEX_ENABLED`
  - `OROMA_ASR_MIN_DELTA_MS`

**Mangel-Speak (Policy)**

- `core/mangel_speak_hook.py` erzeugt gesprochene Selbstberichte bei „Mangel“-Mustern:
  - Confidence↓, Coverage↓, Novelty↑, Time-to-Goal↑
- nutzt TTS-Bridge (DeviceHub-Audio)
- optionaler Reward `source="speech"` → Learning-Chart

**Curriculum-State V2 (adaptiv)**

- `core/curriculum.py` + DB-Tabelle `curriculum_state`
- Felder:
  - `skill_name`, `progress` (EMA), `window` (Repeat-Queue)
- Hook `core/curriculum_hook.py`:
  - Spaced Repetition
  - kleine Rewards `source="curriculum"`

**Learning-UI (erweitert)**

- Rewards aus:
  - SciCalc, SetCalc, Curriculum, Speech (und optional Empathie)
- `/learning/api/curriculum_state` zur Inspektion
- Abbildung der Lernkurven im Zeitverlauf

**SceneGraph-Store & SceneGraph-Builder (vision/token, v3.7.x)**

- Dateien:
  - `core/scenegraph_store.py`
  - `core/scenegraph_builder.py`
  - `tools/scenegraph_selfcheck.py`
- Tabelle `scenegraphs` in `oroma.db` (Schema z. B.:
  - `id INTEGER PRIMARY KEY`
  - `ts INTEGER`
  - `namespace TEXT`
  - `source TEXT`
  - `quality REAL`
  - `graph_json TEXT`
  - `notes TEXT`)
- Pipeline:
  - SnapChains mit `origin='vision/token'` und Status `active` werden gruppiert:
    - Qualitätsbasiertes Labeling: `scenegraph:vision_token:hoch/niedrig`
    - MetaSnaps: `meta_snaps.label = scenegraph:vision_token:*`
    - SceneGraphs in Namespaces wie `scene:auto_meta:vision_token`
  - `graph_json` enthält:
    - `nodes`: `scene:<bucket_ts>`, `chain:<id>`, `origin:vision/token`, ggf. MetaNodes  <!-- TODO linkfix: bucket_ts -> docs/quick_check_3_6.md, docs/curriculum_math_tasks.md | id -> docs/module_ui.md -->
    - `edges`: zeitliche und ursprungsbezogene Relationen
    - `meta.stats`: `nodes_seen`, `edges_seen`, ggf. Aggregations-Infos
- Selfcheck:
  - `tools/scenegraph_selfcheck.py` fasst MetaSnaps & SceneGraphs zusammen (Counts, min/max/avg Nodes/Edges, letztes ts)

**ObjectGraph 1.0 (Scene→Object + Object-DB, ab v3.7.3)**

- Dateien:
  - `core/objectgraph_builder.py`
  - `core/object_extractor.py`
  - `tools/objectgraph_selfcheck.py`
  - `tools/objectgraph_audit.py`
  - `tools/objectgraph_dedupe.py`
  - `tools/objectgraph_top_objects.py`
  - `ui/objects_ui.py` + `templates/objects.html`
- Erweiterungen in `core/sql_manager.py`:
  - Tabelle `object_nodes`:
    - `id INTEGER PRIMARY KEY AUTOINCREMENT`
    - `kind TEXT NOT NULL`          – z. B. `"object"`, `"snapchain"`, `"meta"`, `"origin"`
    - `label TEXT NOT NULL`         – z. B. `"Chain 44919"`, `"compressed_44919"`, `"vision/token"`
    - `meta_json TEXT`              – optionales JSON (Stats, SceneRefs, zusätzliche Metadaten)
    - `created_ts INTEGER NOT NULL` – Unix-Timestamp
  - Tabelle `object_relations`:
    - `id INTEGER PRIMARY KEY AUTOINCREMENT`
    - `a_id INTEGER NOT NULL`          – Quelle (`object_nodes.id`)
    - `relation TEXT NOT NULL`         – z. B. `"meta_to_chain"`, `"chain_to_origin"`, später `"cooccurs"`, `part_of` …
    - `b_id INTEGER NOT NULL`          – Ziel (`object_nodes.id`)
    - `confidence REAL NOT NULL DEFAULT 1.0`
    - `source_scene_id INTEGER`        – referenzierte `scenegraphs.id` (optional)
    - `ts INTEGER NOT NULL`            – Unix-Timestamp
    - `notes TEXT`                     – freier Text oder JSON
  - Indizes:
    - `idx_object_nodes_kind`, `idx_object_nodes_label`
    - `idx_object_relations_a`, `idx_object_relations_b`, `idx_object_relations_rel`, `idx_object_relations_ts`
- DreamWorker 3.3 (v3.7.3-r1) integriert SceneGraph + ObjectGraph in den Nachtlauf:
  1. `_safe_replay()`   – Replay & Policy-Updates
  2. `_forgetting()`    – Gewichtetes Vergessen + Meta-Kompression (MetaSnaps `compressed_*`, Status `compressed`)
  3. _… weitere Schritte (Research, Missions, Curriculum, AutoTuner)_
  4. `_scenegraph_from_vision()`  
     → schreibt SceneGraph in `scenegraphs` (z. B. `namespace='scene:auto_meta:vision_token'`)
  5. `_objectgraph_from_scenegraph()`  
     → baut aus SceneGraphs ein ObjectGraph-Aggregat in `scenegraphs` (z. B. `namespace='object:auto:vision'`)
- Objekt-Extraktion:
  - `core/object_extractor.py` liest `scenegraphs` mit Namespaces:
    - `scene:auto_meta:vision_token` (Szenen)
    - `object:auto:vision` (Aggregat)
  - erstellt daraus:
    - ObjectNodes (Objects, Chains, Meta, Origin) in `object_nodes`
    - Relations in `object_relations` (z. B. `meta_to_chain`, `chain_to_origin`, später mehr Semantik)
- UI `/objects`:
  - Überblick:
    - Gesamtanzahl ObjectNodes
    - Verteilung nach `kind` (z. B. `object`, `snapchain`, `meta`, `origin`)
    - Anzahl & Verteilung von `object_relations` (Relationstypen)
  - Filter:
    - nach `kind` (nur Objekte, nur SnapChains, nur Meta, nur Origin)
    - optional `focus_id` → zeigt Relations für einen bestimmten Node
  - Tabellen:
    - Nodes: `id`, `kind`, `label`, `created`, `meta`
    - Relations: `id`, Quelle (inkl. `(kind: label)`), Relation, Ziel, Confidence, SceneGraph-ID, `ts`, Notes

⸻

3.1 Patchline v3.7.x (3.7.1–3.7.3)

- **v3.7.1**
  - UI-Verfeinerungen, stabilere Learning-API
  - kleine Fixes in DreamWorker/Replay

- **v3.7.2**
  - DreamWorker 3.1:
    - Run-Lock (Dateilock auf `OROMA_DREAM_LOCK`, z. B. `/opt/ai/oroma/data/state/dream.lock`)
    - stabilere FS-Fallbacks für Replay (auch bei defekten JSON-Exports)
    - Logging-Verbesserungen, Self-Healing im LZG (Weight-Decay + Meta-Kompression)

- **v3.7.3 (aktueller Arbeitsstand, Ende 2025)**  
  _„DreamWorker 3.3 + SceneGraph + ObjectGraph 1.0“_

  - DreamWorker 3.3:
    - nutzt die ENV-Schalter (siehe auch `docs/changelog_full.md`):
      - `OROMA_DREAM_SCENEGRAPH` – Vision→SceneGraph-Schritt
      - `OROMA_DREAM_OBJECTGRAPH` – Scene→ObjectGraph-Schritt
    - baut im Dream-Lauf:
      - SceneGraph → Log-Linie z. B.:  
        `Dream-SceneGraph (origin=vision/token): ok=True graph_id=392 nodes=249 edges=368`
      - ObjectGraph-Aggregat → Log-Linie z. B.:  
        `Dream-ObjectGraph (src_ns=scene:auto_meta:): ok=True graph_id=393 objects=4603 edges=8390 graphs_used=32`
  - Object-DB:
    - Tabellen `object_nodes` / `object_relations` werden über `object_extractor` gefüllt.
    - Dedupe-Tool `tools/objectgraph_dedupe.py` entfernt Duplikate in `object_relations` (Definition: identisches `(a_id, relation, b_id)`).
  - Health/Audit:
    - `tools/objectgraph_selfcheck.py`:
      - fasst Node/Relation-Counts und Confidence zusammen,
      - prüft SceneGraphs mit Namespace-Prefix `object:auto:`.
    - `tools/objectgraph_audit.py`:
      - auditiert insbesondere die Beziehung:
        - `label LIKE 'compressed_%'` (Meta-Knoten)
        - zugehörige komprimierte SnapChains (`status='compressed'`, `origin='vision/token'`)
      - Erwartung:
        - pro `compressed_*` genau 1 `meta_to_chain` und 1 `chain_to_origin` Relation
      - nach Dedupe in deinem Live-System:
        - `meta_ok` = Anzahl `compressed_*` (keine Fehler)
        - `compressed_snap_ok` = Anzahl komprimierter Chains (keine Fehler)
    - `tools/objectgraph_top_objects.py`:
      - zeigt Hubs (z. B. `vision/token`, `scenegraph:vision_token:hoch/niedrig`) und danach lokale Objekte/Chains mit höherer Konnektivität.
  - UI:
    - `/objects`-Tab v0.3:
      - wurde mit echten Daten getestet (Nodes ~3.7k, Relations ~6.5k in deinem Live-System)
      - zeigt die verdichtete Objekt-Welt aus Vision-Tokens und MetaSnaps.

⸻

4. Architekturdiagramm (Stufen v1.6 → v3.7.x)

              +-----------------------------------+
              |         v1.6 – Baby               |
              | Snaps • SnapChains • SQLite       |
              | Mini-Games: TicTacToe, Connect4   |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |        v1.62 – Kleinkind          |
              | Flask-UI • Vision-Wrapper         |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |       v1.98 – Jugendphase         |
              | Snake, Pong • Replay • Circadian  |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |      v2.00 – Jugendlicher         |
              | System-Architektur • systemd      |
              | LZG • Dashboard                   |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |       v2.11 – Kind                |
              | Kernmodule stabil • Export-Policy |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |       v2.20 – Schüler             |
              | Raum/Zeit • Diagnostics • Tuner   |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |      v2.30 – Student              |
              | Reward • Curiosity • Episoden     |
              | Explain • Synapses                |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |     v3.0 – Gelehrter              |
              | Fusion Snaps+Tokens • LLM/RAG     |
              | Replay • Dream • Export-Gate      |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |     v3.5 – Meister/Forscher       |
              | MetaSnaps • Mutation • LZG 2.0    |
              | AgentLoop • Research-Basis        |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |     v3.6 – Wissenschaftler        |
              | Hypothesen • Experimente • Causal |
              | Explainability                    |
              +-------------------+---------------+
                                  |
              +-------------------v---------------+
              |   v3.7 – Sozialer Partner         |
              | Empathie • Self-Listening         |
              | Mangel-Speak • Curriculum V2      |
              | SceneGraph (vision/token)         |
              | ObjectGraph 1.0 (Scene→Object)    |
              +-----------------------------------+

⸻

5. Delta v3.6 → v3.7 (Kurzüberblick)

- Neu: Empathie-Signale (`empathy_snaps`) & (optionale) Empathy-View
- Neu: ASR Self-Listening (Reflex) mit Intents → Reaktion auf eigene Sprache
- Neu: Mangel-Speak Policy (gesprochene Selbstberichte + kleine Rewards)
- Neu: `curriculum_state` (DB) + adaptiver Hook (Spaced Repetition, Rewards)
- Neu: Learning-Dashboard zeigt SciCalc, SetCalc, Curriculum, Speech (und optional Empathie)
- Neu: **SceneGraph-Store & SceneGraph-Builder** für Vision-Tokens:
  - SnapChains `origin='vision/token'` → MetaSnaps → SceneGraphs
  - UI-Viewer + Self-Check-Tool (`scenegraph_selfcheck.py`)
- Neu (ab v3.7.3): **ObjectGraph 1.0**:
  - SceneGraphs (Namespace `scene:auto_meta:*`) → ObjectGraph-Aggregate in `scenegraphs` (Namespace `object:auto:vision`)
  - Object-DB: `object_nodes` + `object_relations`
  - Audit-/Dedupe-Tools + `/objects`-UI
- Infra: optionale Timer (z. B. `oroma-dream.timer`) für nächtliche Dream-/SceneGraph-/ObjectGraph-Läufe

⸻

6. Nächste Schritte (Checkliste – v3.7.3-Stand 2025-12-10)

**Kurzfristig (v3.7.3+)**

- [x] DreamWorker ↔ SceneGraph:
  - Night-Task über DreamWorker 3.3:
    - baut SceneGraphs aus aktuellen Vision-Tokens
    - überprüft per Log-Linien (`Dream-SceneGraph (origin=vision/token): …`)
  - Validierung:
    - `scenegraphs`-Einträge mit `namespace='scene:auto_meta:vision_token'` existieren (Live-System)
- [x] DreamWorker ↔ ObjectGraph:
  - Night-Task baut ObjectGraph-Aggregate (`namespace='object:auto:vision'`)
  - Validierung:
    - Log-Linien `Dream-ObjectGraph (src_ns=scene:auto_meta:): …`
    - `scenegraph_store`-Stats (`objects`, `object_edges`, `graphs_used`)
- [x] Object-DB-Health:
  - `tools/objectgraph_selfcheck.py` & `tools/objectgraph_audit.py` laufen ohne Fehler
  - `tools/objectgraph_dedupe.py` reduziert Duplikate in `object_relations` auf einen konsistenten Stand
- [ ] Learning-UI:
  - einfache Metriken zu SceneGraphs/ObjectGraphs im Dashboard
    - Anzahl, letzte Build-Zeit, Nodes/Edges, Objects/ObjectEdges
- [ ] Games / Policies:
  - Snake/TicTacToe Explore vs Policy sauber trennen (Zähler/Policy-Stats gut sichtbar)
- [x] Datenbank & Stabilität:
  - `sql_manager`: WAL + `busy_timeout` (in v3.8-r2 integriert)
- [ ] zusätzliche Selfcheck-Tools:
  - weitere Health-Checks, z. B. kombinierte Episode/SceneGraph/ObjectGraph-Übersicht

**Mittelfristig (Konzept, **nicht** verpflichtend in v3.7.x)**

- Kooperative Szenarien (ORÓMA ↔ ORÓMA) als Add-ons (nicht im Core erzwingend)
- NMR / Native Multimodal Reasoner 3.75:
  - auf Basis des ObjectGraph-Stacks (Ontologie, Semantik, Reasoner)
  - siehe `docs/roadmap_2026.md` (Observation-Only Reasoner)

⸻

7. Zusammenfassung (Metapher)

- v1.6–1.98: **Baby/Kleinkind** (erste Muster, Spiele)
- v2.00–2.11: **Kind** (stabil, neugierig)
- v2.20: **Schüler** (erkennt Lücken, lernt Zeit/Raum)
- v2.30: **Student** (zielgerichtet, episodisch, erklärbar)
- v3.0: **Gelehrter** (Text-/LLM-Wissen, Fusion, Bücher)
- v3.5: **Meister/Forscher** (MetaSnaps, Mutation, Research-Basis)
- v3.6: **Wissenschaftler** (Hypothesen, Experimente, kausale Explainability)
- v3.7.x: **Sozialer Partner**  
  – **Empathie**, **Self-Listening**, **Mangel-Speak**,  
  – **Curriculum V2**, **Learning-Dashboard**,  
  – **SceneGraph für Vision-Tokens** und  
  – **ObjectGraph 1.0** (Scene→Object + Object-DB + `/objects`-UI, geprüft über DreamWorker & Audit-Tools).

ORÓMA hat damit die Reifestufe eines **kognitiv lernenden, sozial reagierenden,
lokal erklärbaren Systems** erreicht – mit klarer Roadmap für Feinschliff,
Object-Semantik (NMR 3.75) und 2026-Forschung, ohne sich in zu großen Zukunftsprojekten zu verlieren.

<a id="docs_history_oroma_referenz_v2_11_patch_roadmap_md"></a>

## Quelle: `docs/history_oroma_referenz_v2_11_patch_roadmap.md`

**Originaltitel:** ORÓMA -- Referenz-Handbuch v2.11 (Patch) + Roadmap v2.20 / v2.30

## Überblick

Dieses Dokument beschreibt den finalisierten Stand von ORÓMA v2.11
(inklusive Patch-Änderungen) und die geplanten Erweiterungen in v2.20
und v2.30.\
Alle Anpassungen sind so integriert, dass kein Informationsverlust
gegenüber den alten Dokumenten entsteht.

------------------------------------------------------------------------

## v2.11 -- Final + Patch

### Kernfunktionen

-   **Snaps & SnapChains**: Basisspeicher für multimodale Features
    (Audio, Vision, Text, Bewegung).
-   **Traummodus (Dream)**: Optimierung + Meta-Snap-Erzeugung, startet
    30min nach Dunkelheit.
-   **Replay-System**: SnapChain-Wiederholung mit
    Export/Import-Optionen.
-   **Persistenz**: SQLite + optional Vektor-DB (Schwellwert 100k
    Chains).
-   **Mini-Games**: TicTacToe, Connect4, Snake, Pong, Memory Maze, Hide
    & Seek.
-   **Dashboard (Flask-UI)**: Games, Modelle, Lernkurve, Export/Import.
-   **Export-Policy (Patch)**: Export deaktiviert keine aktiven Modelle
    mehr; optional status='archived'.
-   **Meta-Wrapper (Patch)**: `wrappers/oroma_wrapper.py` --
    Auto-Backend (Hailo → DeGirum → CPU).

### Besondere Merkmale

-   Crossmodalität (Audio ↔ Vision ↔ Text).
-   Proto-Abstraktion (Meta-Snaps).
-   Dynamische Wrapper-Auswahl.
-   Registry für Runtime-Modelle (inkl. Hailo `.hef`).

------------------------------------------------------------------------

## v2.20 -- Spatio-Temporal Learning & Selbstoptimierung

### Erweiterungen

-   **Zeit- und Raumkontext**: Snaps speichern Delta-Zeit, Wegpunkte,
    Objekt-Relationen.
-   **Knowledge-Gaps**: Coverage, Novelty, Confidence, Time-to-Goal →
    UI-Anzeige (LOW/MED/HIGH).
-   **Selbstoptimierung (sanft)**: Priorisiertes Replay, ε-Steuerung
    (Exploration), adaptives Pruning.
-   **Neue Module**:
    -   `core/spatial_index.py`\
    -   `core/diagnostics.py`\
    -   `core/auto_tuner.py`

### Ziel

-   Diagnosefähigkeit (weiß, was es nicht weiß).\
-   Grundstein für höherwertiges Lernen.

------------------------------------------------------------------------

## v2.30 -- Agentisches Lernen & Erklärbarkeit

### Erweiterungen

-   **Reward-System**: Belohnung für Ziele in Spielen & realen Wrappern.
-   **Curiosity/Surprise**: Intrinsische Motivation → Erkundung.
-   **Predictor**: Prognose nächster Snaps (Hit@K-Metrik).
-   **Episodisches Gedächtnis**: Speichert und ruft ähnliche Erlebnisse
    ab.
-   **Explainability**: `why_decision()` erklärt Entscheidungen.
-   **Neue Module**:
    -   `core/reward.py`, `core/curiosity.py`, `core/predictor.py`,
        `core/episodic.py`, `core/explain.py`\
    -   Dashboard-Erweiterungen: Learning-Curve, Episoden-Browser,
        Why-Tab.

### Ziel

-   Von „lernendem Kind" → zu einem „reflektierenden Agenten".\
-   Erklärbare KI mit episodischem Selbstverständnis.

------------------------------------------------------------------------

## Architekturdiagramm (vereinfacht)

         +----------------------+
         |   Wrapper-System     |
         | (Audio, Vision, ...) |
         +----------+-----------+
                    |
        +-----------v------------+
        |   SnapFeatures +       |
        |   Overlay-Generator    |
        +-----------+------------+
                    |
        +-----------v------------+
        |       SnapChains       |
        |  (Sequenzen, Regeln)   |
        +-----------+------------+
                    |
       +------------v-------------+
       |   Langzeitgedächtnis     |
       |  SQL + Vektor-DB         |
       +-----+-------------+------+
             |             |
      +------v---+   +-----v------+
      | Export   |   | Dashboard  |
      | (Policy) |   | Flask-UI   |
      +----------+   +------------+

------------------------------------------------------------------------

## Roadmap Zusammenfassung

-   **v2.11** → Stabiler Kern, Mini-Games, Meta-Wrapper, Export-Policy.\
-   **v2.20** → Zeit/Raum-Kontext, Knowledge-Gaps, sanfte
    Auto-Optimierung.\
-   **v2.30** → Reward, Curiosity, Prediction, Episoden, Explainability.

------------------------------------------------------------------------

## Hinweise für Patch 2.11

-   Export-Policy angepasst (kein Disable mehr).\
-   Meta-Wrapper eingeführt.\
-   Registry für Modelle integriert.\
-   Alle Core-Module produktiv getestet.

<a id="docs_history_oroma_roadmap_md"></a>

## Quelle: `docs/history_oroma_roadmap.md`

**Originaltitel:** ORÓMA Roadmap v2.11 → v2.30 → v3.0+

## Überblick

Diese Roadmap beschreibt die Entwicklungsstufen von ORÓMA:  
Von der stabilen v2.11 über erweiterte Diagnose und Auto-Tuning in v2.20, hin zur agentischen Intelligenz mit Erklärbarkeit in v2.30, und den zukünftigen Etappen v3.0 (Student/Gelehrter, **abgeschlossen**) sowie v3.5+ (Forscher/Meister, geplant).

---

## v2.11 – Stabil & Komplett (Final)

**Ziel:** Solides, offline-fähiges Lernsystem mit Snap-Basis, SnapChains, Replay/Dream und modularen Wrappern.

- ✅ Snaps / SnapTokens / SnapChains (multimodal; Vision, Audio, Text; SQL/TinyDB)  
- ✅ Dream/Replay (Mutation, Pruning, Export/Import mit 30-Tage-Policy)  
- ✅ Circadian Controller (Day/Dream Umschaltung via Lichtsensor)  
- ✅ Wrapper-System (Vision, Audio, Text, PiCar, TTS, GStreamer, Hailo)  
- ✅ VisionWrapper headless optimiert (Picamera2/OpenCV/GStreamer – kein Qt/Wayland/X11 nötig)  
- ✅ UI (Dashboard, Lernkurven-Stub, Registry, Models)  
- ✅ Mini-Programme: TicTacToe, Connect4, Snake, Pong, Memory, Maze, Hide & Seek  

**Testplan v2.11**  
- Spiele: Serien Mensch vs ORÓMA, ORÓMA vs ORÓMA  
- Lernkurven: Resonanz-Score, SnapChain-Zähler  
- Hide & Seek: Time-to-Find ↓, Survival-Steps ↑  
- Headless-Test: Kamera-Bilderzeugung funktioniert rein über Bash + Flask-UI (kein GUI nötig)  

---

## v2.20 – Spatio-Temporal + Diagnose & Auto-Tuning

**Ziel:** Raum-/Zeit-Kontext im Snap, Knowledge-Gaps messen und sanfte Auto-Tuning-Hooks.

- 🆕 Snap-Erweiterungen: `time_since_prev_ms`, `space:{waypoint_id, relations, distance_bucket}`  
- 🆕 SnapChain: speichert Delta-Zeit, Raum-Kontext  
- 🆕 Diagnostics: Coverage, Novelty-Rate, Confidence, Time-to-Goal  
- 🆕 Auto-Tuning: Priority Replay, Exploration-Steuerung (ε), Rule-Weight Nudge, adaptives Pruning  
- 🆕 UI-Badge für Knowledge-Gaps (LOW/MED/HIGH)  
- 🆕 CLI-/Bash-Diagnose-Tools für Headless-Umgebungen (log-basiert, kein GUI nötig)  

**Testplan v2.20**  
- Coverage/Novelty plausibel in Logs & UI  
- Confidence-Kurven konsistent  
- Auto-Tuner reagiert (ε hoch bei Gaps HIGH)  
- Alle Diagnose-Funktionen auch headless über Logs/CLI prüfbar  

---

## v2.30 – Agentisches Lernen & Erklärbarkeit

**Ziel:** Zielgerichtetes Handeln mit Reward, Vorhersage, Curiosity und episodischem Gedächtnis.

- 🆕 Reward-System (Wrapper-Adapter + Mini-Game Rewards)  
- 🆕 Predictor (Top-K nächste Snaps, Hit@K-Metrik)  
- 🆕 Curiosity/Surprise (intrinsische Motivation)  
- 🆕 Episodisches Gedächtnis (Vektor-Index, ähnliche Erlebnisse abrufen)  
- 🆕 Explainability: `why_decision()` → zeigt beteiligte Chains/Regeln  
- 🆕 UI: Learning-Curve (Chart.js), Episoden-Browser, Why-Tab  
- 🆕 Web-UI bleibt reines HTML/JS; keine GUI-Frameworks → optimiert für Headless-Serverbetrieb  

**Testplan v2.30**  
- Reward-Lernkurve ↑ über Episoden  
- Predictor: steigendes Hit@K  
- Episoden-Abruf liefert konsistente Erinnerungen  
- Why-Tab erklärt Entscheidungen nachvollziehbar  
- Weboberfläche validiert auf ARM64-Server ohne GUI  

---

## v3.0 – Student / Gelehrter (**abgeschlossen**)

**Ziel:** Snap+Token-Fusion & LLM-Anbindung → „Super-Gedächtnis“ mit Wissenstransfer.

- ✅ Snap+Token-Fusion (numerische + symbolische Ebene)  
- ✅ LLM-Integration (lokal/hybrid/remote per Policy)  
- ✅ Text-/Bücherwissen in SnapChains integrierbar  
- ✅ RAG + Tool-Use für faktenbasierte Antworten  
- ✅ Headless-first: LLM- und RAG-Module laufen komplett CLI/Server-seitig, keine GUI-Komponenten  

**Testplan v3.0**  
- Import von Text/Büchern erzeugt SnapChains und lässt sich in Episoden abrufen  
- LLM nutzt Snaps + Tokens → Antworten faktenbasiert + Kontext-aware  
- Memory-/Knowledge-Gaps schließen sich durch RAG-Module  
- System bleibt stabil und headless  

---

## v3.5+ – Forscher / Meister (geplant)

**Ziel:** ORÓMA wird eigenständiger, generiert Hypothesen, optimiert sich selbst.

- 🆕 Autonome Hypothesenbildung  
- 🆕 Tiefere Explainability (kausale Ketten, Gegenbeweise)  
- 🆕 Selbsttuning über mehrere Generationen von Modulen  
- 🆕 Forschungstätigkeit: explorative, kreative Musterfindung  
- 🆕 Weiterhin konsequent headless, Web-UI + CLI als einzige Interaktionswege  

---

## Entscheidungsleitfaden

- v2.11 finalisieren → stabil & produktiv  
- v2.20: Diagnostics + Auto-Tuner für bessere Selbstdiagnose  
- v2.30: Agentik (Reward, Predictor, Episoden, Explainability)  
- v3.0: Snap+Token-Fusion, LLM-Wissen → **Student/Gelehrter**  
- v3.5+: autonome Hypothesen → **Forscher/Meister**  
- Ab v2.11 gilt: Keine GUI/Wayland/X11 nötig, optimiert für Bash/Headless + HTML-WebUI

<a id="docs_history_oroma_roadmap_v2_11_3_x_md"></a>

## Quelle: `docs/history_oroma_roadmap_v2_11_3_x.md`

ORÓMA Roadmap v2.11 → v2.30 → v3.0+

Überblick

Diese Roadmap beschreibt die Entwicklungsstufen von ORÓMA:
Von der stabilen v2.11 über erweiterte Diagnose und Auto-Tuning in v2.20, hin zur agentischen Intelligenz mit Erklärbarkeit in v2.30, und den zukünftigen Etappen v3.0 (Student/Gelehrter) sowie v3.5+ (Forscher/Meister).

⸻

v2.11 – Stabil & Komplett (Final)

Ziel: Solides, offline-fähiges Lernsystem mit Snap-Basis, SnapChains, Replay/Dream und modularen Wrappern.
	•	✅ Snaps / SnapTokens / SnapChains (multimodal; Vision, Audio, Text; SQL/TinyDB)
	•	✅ Dream/Replay (Mutation, Pruning, Export/Import mit 30-Tage-Policy)
	•	✅ Circadian Controller (Day/Dream Umschaltung via Lichtsensor)
	•	✅ Wrapper-System (Vision, Audio, Text, PiCar, TTS, GStreamer, Hailo)
	•	✅ VisionWrapper headless optimiert (Picamera2/OpenCV/GStreamer – kein Qt/Wayland/X11 nötig)
	•	✅ UI (Dashboard, Lernkurven-Stub, Registry, Models)
	•	✅ Mini-Programme: TicTacToe, Connect4, Snake, Pong, Memory, Maze, Hide & Seek

Testplan v2.11
	•	Spiele: Serien Mensch vs ORÓMA, ORÓMA vs ORÓMA
	•	Lernkurven: Resonanz-Score, SnapChain-Zähler
	•	Hide & Seek: Time-to-Find ↓, Survival-Steps ↑
	•	Headless-Test: Kamera-Bilderzeugung funktioniert rein über Bash + Flask-UI (kein GUI nötig)

⸻

v2.20 – Spatio-Temporal + Diagnose & Auto-Tuning

Ziel: Raum-/Zeit-Kontext im Snap, Knowledge-Gaps messen und sanfte Auto-Tuning-Hooks.
	•	🆕 Snap-Erweiterungen: time_since_prev_ms, space:{waypoint_id, relations, distance_bucket}
	•	🆕 SnapChain: speichert Delta-Zeit, Raum-Kontext
	•	🆕 Diagnostics: Coverage, Novelty-Rate, Confidence, Time-to-Goal
	•	🆕 Auto-Tuning: Priority Replay, Exploration-Steuerung (ε), Rule-Weight Nudge, adaptives Pruning
	•	🆕 UI-Badge für Knowledge-Gaps (LOW/MED/HIGH)
	•	🆕 CLI-/Bash-Diagnose-Tools für Headless-Umgebungen (log-basiert, kein GUI nötig)

Testplan v2.20
	•	Coverage/Novelty plausibel in Logs & UI
	•	Confidence-Kurven konsistent
	•	Auto-Tuner reagiert (ε hoch bei Gaps HIGH)
	•	Alle Diagnose-Funktionen auch headless über Logs/CLI prüfbar

⸻

v2.30 – Agentisches Lernen & Erklärbarkeit

Ziel: Zielgerichtetes Handeln mit Reward, Vorhersage, Curiosity und episodischem Gedächtnis.
	•	🆕 Reward-System (Wrapper-Adapter + Mini-Game Rewards)
	•	🆕 Predictor (Top-K nächste Snaps, Hit@K-Metrik)
	•	🆕 Curiosity/Surprise (intrinsische Motivation)
	•	🆕 Episodisches Gedächtnis (Vektor-Index, ähnliche Erlebnisse abrufen)
	•	🆕 Explainability: why_decision() → zeigt beteiligte Chains/Regeln
	•	🆕 UI: Learning-Curve (Chart.js), Episoden-Browser, Why-Tab
	•	🆕 Web-UI bleibt reines HTML/JS; keine GUI-Frameworks → optimiert für Headless-Serverbetrieb

Testplan v2.30
	•	Reward-Lernkurve ↑ über Episoden
	•	Predictor: steigendes Hit@K
	•	Episoden-Abruf liefert konsistente Erinnerungen
	•	Why-Tab erklärt Entscheidungen nachvollziehbar
	•	Weboberfläche validiert auf ARM64-Server ohne GUI

⸻

v3.0 – Student / Gelehrter (geplant)

Ziel: Snap+Token-Fusion & LLM-Anbindung → „Super-Gedächtnis“ mit Wissenstransfer.
	•	🆕 Snap+Token-Fusion (numerische + symbolische Ebene)
	•	🆕 LLM-Integration (lokal/hybrid/remote per Policy)
	•	🆕 Text-/Bücherwissen in SnapChains integrierbar
	•	🆕 RAG + Tool-Use für faktenbasierte Antworten
	•	Entwicklungsstufe: Student / Gelehrter
	•	Headless-first: LLM- und RAG-Module laufen komplett CLI/Server-seitig, keine GUI-Komponenten

⸻

v3.5+ – Forscher / Meister (geplant)

Ziel: ORÓMA wird eigenständiger, generiert Hypothesen, optimiert sich selbst.
	•	🆕 Autonome Hypothesenbildung
	•	🆕 Tiefere Explainability (kausale Ketten, Gegenbeweise)
	•	🆕 Selbsttuning über mehrere Generationen von Modulen
	•	🆕 Forschungstätigkeit: explorative, kreative Musterfindung
	•	Entwicklungsstufe: Forscher / Meister
	•	Weiterhin konsequent headless, Web-UI + CLI als einzige Interaktionswege

⸻

Entscheidungsleitfaden
	•	v2.11 finalisieren → stabil & produktiv
	•	v2.20: Diagnostics + Auto-Tuner für bessere Selbstdiagnose
	•	v2.30: Agentik (Reward, Predictor, Episoden, Explainability)
	•	v3.0: Snap+Token-Fusion, LLM-Wissen → Gelehrter
	•	v3.5+: autonome Hypothesen → Forscher / Meister
	•	Grundprinzip ab 2.11+: Keine GUI/Wayland/X11 nötig, optimiert für Bash/Headless + HTML-WebUI

<a id="docs_history_oroma_v3_5_roadmap_md"></a>

## Quelle: `docs/history_oroma_v3_5_roadmap.md`

**Originaltitel:** ORÓMA v3.5 – Roadmap

Version: 3.5  
Stand: 2025-09-21  
Basis: ORÓMA v3.0 (Final ZIP)

---

## 🎯 Leitidee
ORÓMA v3.5 erweitert das stabile Fundament von v3.0 um **Multi-Agent-Fähigkeiten, verbessertes Lernen, tiefere LLM-Integration und optimierte Systemdienste**.  
Ziel: Von der „Student/Gelehrter“-Stufe zu einem **autonomeren, multi-modalen Forschungs- und Lernsystem**.

---

## 🔑 Kern-Erweiterungen

### 1. Architektur
- [ ] **Multi-Agent SnapLoop**  
  - Parallel laufende AgentLoops: Planung, Dialog, Sensor-Fusion  
  - Gemeinsamer Zugriff auf SnapChain-Speicher  
  - API: `/agents/api` für Status und Steuerung  
- [ ] **Token-Fusion**  
  - Zusammenführung von Snap-Features und LLM-Embeddings in einer DB-Tabelle  
  - Export/Import erweitert um Hybrid-Speicher  
  - Ziel: „Super-Gedächtnis“-Ansatz vorbereiten  
- [ ] **DreamWorker v2**  
  - Batch-Optimierung mehrerer SnapChains gleichzeitig  
  - Neue Strategien: Qualitätsschwelle, Selektionsdruck  

### 2. Lernen & Dashboard
- [ ] **Learning Dashboard v2**  
  - Charts: Novelty, Confidence, Coverage, TimeGoal über Zeit  
  - Drilldown: Replay + Explain für einzelne SnapChains  
  - CSV-Export aus `/learning/api/history`  
- [ ] **Explainability-Erweiterung**  
  - Neue Views für Multi-Agent-Entscheidungen  
  - Vergleich: „Warum A und nicht B?“  

### 3. Spiele & Simulation
- [ ] **Games-Arena**  
  - ORÓMA vs. ORÓMA automatisiert über Snake, Pong, Connect4, Memory Maze 2033  
  - Ergebnis-Tabelle + Replay-Option  
- [ ] **Memory Maze 2033 v2**  
  - Erweiterte Level-Generierung  
  - KI-Schwierigkeitsgrade  

### 4. UI/UX
- [ ] **Unified Dark Theme v2** (style.css erweitern)  
  - Feinere Buttons, Status-Badges, Spielarena-Darstellung  
- [ ] **ASR/ASR2 konsolidieren**  
  - Gemeinsame Status-Seite `/asr/dashboard`  
  - Gerätewahl (Dropdown, per ENV `OROMA_ASR_DEVICE_LIST`)  

### 5. System & Dienste
- [ ] **Selbsttest-Service** (`oroma-selftest.service`)  
  - Hardware-Check (Kamera, Mic, PiCar, NPU)  
  - Modell-Check (LLM/ASR-Dateien vorhanden?)  
  - Ausgabe in `/health` + Logdatei  
- [ ] **Backup 2nd-Tier**  
  - Erweiterung von `monthly_archive.sh`  
  - Optionales Kopieren nach `/mnt/backup/oroma` oder SMB/NAS  
  - Konfigurierbar via `.env`: `OROMA_BACKUP_PATH`  

---

## 📊 Roadmap-Übersicht

| Bereich          | Ziel                                  | Status v3.0 | Status v3.5 |
|------------------|---------------------------------------|-------------|-------------|
| Core-Architektur | SnapChains, AgentLoop, DreamWorker    | ✔ stabil    | ➕ Multi-Agent, Token-Fusion |
| Dashboard        | Replay, Explain, Learning v1          | ✔ stabil    | ➕ Learning v2 mit Charts |
| Spiele           | Snake, Pong, Flappy, CTF, Memory usw. | ✔ stabil    | ➕ Arena, MemoryMaze v2 |
| UI               | Dark Theme v1, Badges                 | ✔ stabil    | ➕ Theme v2, ASR-Dashboard |
| Systemdienste    | archive.service, dream.timer          | ✔ stabil    | ➕ Selftest, Backup 2nd-Tier |

---

## 🗂️ Dateien/Module geplant für v3.5

### Neue
- `core/multi_agent.py`
- `core/token_fusion.py`
- `core/dream_worker_v2.py`
- `ui/agents_ui.py`
- `ui/arena_ui.py`
- `ui/asr_dashboard_ui.py`
- `tools/selftest.py`
- `systemd/oroma-selftest.service`
- `systemd/oroma-selftest.timer`

### Erweiterte
- `ui/learning_ui.py` (+ API `/history`)  
- `ui/why_ui.py` (Multi-Agent Explain)  
- `ui/style.css` (Dark Theme v2)  
- `tools/monthly_archive.sh` (Backup 2nd-Tier)  
- `core/export_gate.py` (Token-Fusion kompatibel)  

---

## 🚀 Release-Plan
- **v3.1 – v3.4** = Bugfixes, kleinere Verbesserungen (z. B. Pfade, ENV, Logging)  
- **v3.5** = großer Feature-Sprung mit Multi-Agent, Token-Fusion, Learning Dashboard v2  

---

## ✅ Nächste Schritte
1. [ ] ZIP von v3.0 als „Baseline-Release“ sichern (→ `oroma_v3.0_final.tar`)  
2. [ ] Branch `v3.5-dev` erstellen  
3. [ ] Kernmodule vorbereiten: `multi_agent.py`, `token_fusion.py`  
4. [ ] Learning Dashboard v2 (Charts + CSV-History) umsetzen  
5. [ ] Selftest-Service schreiben und ins UI einbinden

<a id="docs_history_projektstruktur_patch_roadmap_md"></a>

## Quelle: `docs/history_projektstruktur_patch_roadmap.md`

ORÓMA – Projektstruktur (v2.30 Final)

Pfad: /opt/ai/oroma/v2.30

⸻

1. Basis
	•	run_oroma.py → Startpunkt, lädt venv, startet Flask-UI
	•	deploy_all.py → Deployment (systemd, nginx, cron, logrotate)
	•	rollback_deploy.sh → Rollback aller Deployment-Änderungen
	•	.env → Umgebungsvariablen (UI-Token, Backend-Policies, Export-Delay, Feature-Flags)
	•	requirements.txt → Python-Abhängigkeiten
	•	README.md, changelog.md → Übersicht & Historie

⸻

2. Core (Kernintelligenz)
	•	core/snap.py → Snaps (Momentaufnahmen, Features, Metadata)
	•	core/snaptoken.py → Tokenisierung (Text, Symbole, LLM-Integration)
	•	core/snappattern.py → SnapPattern-Management (inkl. Zeit/Space-Felder)
	•	core/snapchain.py → SnapChains (Sequenzen, Resonanz, Delta-Zeit, Raumkontext)
	•	core/regelarchiv.py → Regel-Speicher, Versionierung, Mutation-Anbindung
	•	core/mutation.py → Mutation, Selektion
	•	core/langzeitgedaechtnis.py → Langzeitgedächtnis, Vektor-DB-Auto-Switch
	•	core/sql_manager.py → SQLite, Registry (Modelle, Chains, Rules, Metrics)
	•	core/vector_migration.py → Aktivierung Vektor-Index (ab 100k Chains)
	•	core/overlay.py → Overlays (Vision/Audio/Text)
	•	core/circadian_controller.py → Tag/Nacht-Automaton (+30min Delay)
	•	core/llm_runtime.py → GGUF-Modelle, Chat/Status/Load

Erweiterungen ab v2.20
	•	core/spatial_index.py → Wegpunkte, Relationen, Distanz-Buckets
	•	core/diagnostics.py → Knowledge-Gaps (Coverage, Novelty, Confidence, Time-to-Goal)
	•	core/auto_tuner.py → sanfte Optimierung (ε-Regler, Replay-Priorisierung)

Erweiterungen ab v2.30
	•	core/reward.py → Reward-System (Mini-Games, Wrapper)
	•	core/curiosity.py → Curiosity/Surprise (intrinsische Motivation)
	•	core/predictor.py → Vorhersage nächster Snaps (Hit@K)
	•	core/episodic.py → Episodisches Gedächtnis (Vektorindex, Recall)
	•	core/explain.py → Explainability (why_decision())

⸻

3. Wrapper
	•	wrappers/oroma_wrapper.py → Meta-Wrapper (Backend-Auswahl: Hailo/DeGirum/CPU)
	•	wrappers/hailo_wrapper.py → Hailo-Runtime (.hef)
	•	wrappers/degirum_wrapper.py → DeGirum NPU
	•	wrappers/vision_wrapper.py → Vision (CPU/ONNX)
	•	wrappers/audio_wrapper.py → Audio (Whisper/Vosk, FFT/MFCC)
	•	wrappers/tts_wrapper.py → TTS Offline
	•	wrappers/gstreamer_wrapper.py → High-Perf Ingest (RTSP/Files)
	•	wrappers/dynamic_wrapper.py → Policy-Switching (Day/Night)
	•	wrappers/brightness_wrapper.py → Lux-Sensor
	•	wrappers/text_wrapper.py → CLI/TTY Input
	•	wrappers/picar_wrapper.py → PiCar-Steuerung

⸻

4. Mini-Programme
	•	mini_programs/tictactoe.py
	•	mini_programs/connect4.py
	•	mini_programs/snake.py
	•	mini_programs/pong.py
	•	mini_programs/memory_maze.py
	•	mini_programs/flappybird.py
	•	mini_programs/capture_the_flag.py
	•	mini_programs/hide_seek.py (Hide & Seek mit 1 Seeker + 4 Hiders)

⸻

5. UI
	•	ui/flask_ui.py → Haupt-Dashboard (Spiele, Modelle, Export, Health, Synapses)
	•	ui/export_manager.py → Export/Import-Handling
	•	ui/hideseek_ui.py → UI für Hide & Seek
	•	ui/flappy_ui.py → UI für FlappyBird
	•	ui/ctf_ui.py → UI für Capture the Flag
	•	ui/learning.py → Learning-Curves (Reward, Curiosity, Gaps)
	•	ui/episodic_ui.py → Episoden-Browser
	•	ui/why_ui.py → Explainability-Tab

⸻

6. Exports
	•	exports/model_export.py → Export Bundles (Policy: active/archived, kein Disable)
	•	exports/model_import.py → Import Bundles (Dedupe, Registry)
	•	exports/hailo_export.py → .hef-Erstellung, Registry
	•	exports/degirum_export.py → DeGirum Export

⸻

7. Deployment & Tools
	•	systemd/oroma.service → Engine + UI
	•	systemd/oroma-health.timer, oroma-replay.timer, oroma-archive.timer, oroma-exportgate.timer
	•	tools/backup_restore.sh → Backup & Restore
	•	tools/sim_learn.py → Simulierter Lernlauf (Day/Dream-Modus)
	•	cron/oroma.cron → Optionaler Cron-Support

⸻

8. Docs
	•	docs/referenz_handbuch.md
	•	docs/administrator-handbuch.md
	•	docs/konzeption_architektur.md
	•	docs/projektstruktur.md
	•	docs/integrationstest.md
	•	docs/changelog.md
	•	docs/readme_addons.md

⸻

Roadmap Überblick

v2.11 (Final + Patch)
	•	Hide & Seek integriert
	•	Export-Policy angepasst (active/archived statt disable)
	•	Meta-Wrapper + Registry-Upgrade
	•	Systemd/Cron verfeinert

v2.20
	•	Knowledge-Gaps & Diagnostics
	•	Raum-/Zeit-Kontext (spatial_index)
	•	Sanfte Selbstoptimierung (auto_tuner)

v2.30 (Final)
	•	Reward-System
	•	Curiosity & Predictor
	•	Episodisches Gedächtnis
	•	Explainability-Tab
	•	Learning-Dashboard & Episoden-Browser

v3.0 (Zukunft)
	•	LLM-Fusion (Snap+Token → Super-Gedächtnis)
	•	Wissen aus Texten/Büchern nutzbar
	•	Entwicklungsstufe: Student / Gelehrter

v3.5+ (Zukunft)
	•	Autonome Hypothesenbildung
	•	Tiefere Explainability (kausale Ketten)
	•	Selbsttuning wie ein Supergelehrter
	•	Entwicklungsstufe: Forscher / Meister

⸻

👉 Pfad: /opt/ai/oroma/v2.30/docs/projektstruktur.md

<a id="docs_history_referenz_handbuch_patch_roadmap_md"></a>

## Quelle: `docs/history_referenz_handbuch_patch_roadmap.md`

ORÓMA – Referenz-Handbuch (v2.30V, Final)

Zweck: Dokumentation der stabilen ORÓMA v2.30V inkl. allen Modulen, UI-Erweiterungen und Roadmap-Ausblick.

Pfadbasis: /opt/ai/oroma/v2.30/

⸻

1) Systemüberblick
	•	Edge-fähig, offline-first: Raspberry Pi 5, optional NPU (Hailo/DeGirum).
	•	Zyklen: Tag (SnapFeatures/Overlay) → Nacht (Dream/Replay/Mutation).
	•	Multimodal: Vision, Audio (ASR/TTS), Text, PiCar.
	•	Abstraktion: Snaps → SnapChains → Episoden → Meta-Snaps.
	•	UI (Flask): Spiele, Modelle, Export/Import, Health, Learning-Dashboard, Gaps, Synapses.
	•	Persistenz: SQLite + Vektor-DB (FAISS, ab Threshold).
	•	Export/Import: tar-Bundles, Delay-Policy (30 Tage, Qualität).
	•	Add-ons: Flappy Bird, Hide&Seek, Capture-the-Flag – optional aktivierbar.

⸻

2) Core-Module (v2.30V)
	•	snap.py – Snap-Objekte, Feature-Vektoren, Similarity
	•	snaptoken.py – Symbolische Ebene, LLM-Anbindung
	•	snappattern.py – Muster/Cluster, Centroid
	•	snapchain.py – Sequenzen, Resonanz, Delta-Zeit
	•	regelarchiv.py – Regelbasis, Weights, Pruning
	•	mutation.py – Mutation, Selektion
	•	sql_manager.py – DB-Layer (snapchains, rules, metrics, models)
	•	langzeitgedaechtnis.py – Recall, Long-Term-Storage
	•	overlay.py – Fusion multimodaler Features
	•	vector_migration.py – Umschalten auf Vektor-Index (FAISS)
	•	circadian_controller.py – Tag/Nacht, Dream +30 min nach dunkel
	•	llm_runtime.py – GGUF-Runtime, Chat, Temperatur/Top-p/Tokens
	•	NEU v2.20/2.30:
	•	spatial_index.py – Raum/Relationen
	•	diagnostics.py – Knowledge-Gaps (coverage, novelty, confidence, time-to-goal)
	•	auto_tuner.py – Parametertuning, Replay-Policy
	•	reward.py – Rewards aus Spielen & Wrappern
	•	curiosity.py – intrinsische Motivation
	•	predictor.py – Snap-Vorhersage (Hit@K)
	•	episodic.py – Episoden-Gedächtnis (Vector-Index)
	•	explain.py – Explainability (why_decision)

⸻

3) UI (Flask-Dashboard)
	•	/ → Home (Status, Health)
	•	/games → Snake, Pong (+ Add-ons)
	•	/chat → LLM-Chat, Modellwahl
	•	/models → Vision/Audio-Modelle konfigurieren
	•	/export → Export/Import-Bundles
	•	/health → Statuschecks (Core, DB, Wrappers, LLM, Export)
	•	/learning → Learning-Dashboard (Chart.js: Rewards, Curiosity, Gaps)
	•	/gaps → Knowledge-Gap Diagnose (Badge, Replay-Policy)
	•	/synapses → Graphische Netzwerkanzeige (Snaps, Episoden, Verbindungen)

⸻

4) Wrapper
	•	oroma_wrapper.py – Meta-Wrapper, Auto-Backend (Hailo > DeGirum > CPU)
	•	hailo_wrapper.py – Hailo .hef Modelle
	•	degirum_wrapper.py – DeGirum-Runtime
	•	vision_wrapper.py – OpenCV/ONNX-Fallback
	•	audio_wrapper.py – ASR (Whisper/Vosk), Features
	•	tts_wrapper.py – Offline TTS
	•	gstreamer_wrapper.py – High-Perf Ingest (RTSP/Files)
	•	picar_wrapper.py – PiCar Steuerung
	•	text_wrapper.py, brightness_wrapper.py, dynamic_wrapper.py

⸻

5) Exports
	•	model_export.py – Export Chains/Rules → ZIP/TAR, Delay + Quality
	•	model_import.py – Import Bundles (Dedupe, Merge)
	•	hailo_export.py, degirum_export.py – Toolchains, Model Registry

⸻

6) Add-ons (optional)
	•	flappy_ui.py – Flappy Bird (ASCII + Autopilot)
	•	hideseek_ui.py – Hide & Seek Spiel
	•	ctf_ui.py – Capture-the-Flag

⸻

7) Deployment
	•	systemd:
	•	oroma.service (Engine + UI)
	•	Timer: Health, Replay, ExportGate, Archive
	•	cron: Alternativ, Backup/Export
	•	nginx: Proxy, optional HTTPS
	•	logrotate: Logs rotieren täglich

⸻

8) Roadmap (über v2.30 hinaus)
	•	v3.0 – Student / Gelehrter
	•	LLM-Anbindung als „Super-Gedächtnis“
	•	Fusion Snap + Token
	•	Wissenstransfer aus Texten (Docs, Export-Bundles)
	•	v3.5+ – Forscher / Meister
	•	Autonome Hypothesenbildung
	•	Tiefere Explainability (mehrschichtige Ursachenkette)
	•	Selbsttuning auf Expertenniveau („Supergelehrter“)

<a id="docs_history_roadmap_v3_0_md"></a>

## Quelle: `docs/history_roadmap_v3_0.md`

**Originaltitel:** ORÓMA v3.0 – Roadmap & ToDo

## Überblick
ORÓMA v3.0 ist die **Student/Gelehrter-Stufe**:
- Snap+Token-Fusion (numerisch + symbolisch)
- RAG-Integration (Knowledge-Import, Ask-UI)
- LLM-Anbindung (lokal/hybrid per Policy)
- Learning-Dashboard (Rewards, Curiosity, Gaps)
- Vision mit Overlay/Inference
- Mini-Spiele (Snake, Pong, Flappy, CTF, Hide&Seek)
- Export/Import mit Policy

---

## ✅ Fertiggestellt
- Core:
  - `run_oroma.py` mit AgentLoop
  - Logging + `.env` Variablen
  - Systemd-Units (`oroma.service`, `archive.timer`, `replay.timer`)
- UI:
  - `flask_ui.py` mit Token-Auth
  - `index.html` + Navigation
  - Spiele: Snake, Pong, Flappy, CTF, Hide&Seek
  - `video_ui.py` + `video.html`
- Vision:
  - `vision_wrapper.py` mit OpenCV/GStreamer/Picamera2
  - Overlay (Edges, Brightness, Motion, Tags)
  - ONNX/DeGirum Hooks
- RAG:
  - `knowledge_ui.py` (Upload in `knowledge.db`)
  - `ask_ui.py` (Fragen stellen, RAG + LLM Antwort)
  - `rag_bridge.py`, `fusion.py`, `llm_runtime.py`
- Learning (Backend):
  - `learning.py` API (Rewards, Curiosity, Gaps, Export)

---

## ⏳ Offene Punkte

### Learning & Dashboard
- [ ] **`learning.html`**: Charts (Rewards, Curiosity, Gaps) aus API-Daten
- [ ] **Selftest-Button**: API-Aufruf + Rückmeldung im UI


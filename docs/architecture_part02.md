<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__architecture.md
  Part:   2
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

	1.	Vision-Tokens (Kamera) werden als snapchains mit origin='vision/token' gespeichert.
	2.	In der Dream-Phase:
	•	dream_worker._scenegraph_from_vision() ruft den scenegraph_builder auf.
	•	Es werden SnapChains gefiltert (Quality, Limit), in Gruppen verarbeitet und zu MetaSnaps verdichtet.
	•	Aus MetaSnaps entsteht ein SceneGraph:
	•	Knoten: abstrakte „Objekte“ / Cluster,
	•	Kanten: Beziehungen (z. B. zeitlich, räumlich, co-aktiv).
	3.	Ergebnis:
	•	Persistenz in scenegraphs (Namespace scene:auto_meta:vision_token),
	•	Logging mit graph_id, num_nodes, num_edges.

Das entspricht praktisch deiner 2.5D-Idee:

2D-Snaps (Vision-Features) → Cluster → Graphstruktur,
die als „Weltmodell-Layer“ benutzt werden kann.

3.4 Audio & Audio-Student – „Kind lernt vom Lehrer“

Auf Audio-Seite gibt es inzwischen drei wichtige Bausteine:
	1.	AudioWrapper (wrappers/audio_wrapper.py)
	•	Hub-first: nutzt zuerst DeviceHub, fällt bei Bedarf auf sounddevice zurück.
	•	Features:
	•	RMS, Zero-Crossing-Rate, Pitch (YIN/librosa + ACF-Fallback),
	•	Log-Power-Spektrum (64 Bins),
	•	kompakter Snap-Vektor snap_feature.
	•	ASR:
	•	optionales Whisper-Backend (lazy load),
	•	asr_stream() als Convenience-Funktion.
	2.	Audio-Student-DB (audio_student_pairs in sql_manager.py)
	•	Tabelle u. a. mit:
	•	transcript_teacher: Referenz-Transkript (Whisper/extern),
	•	transcript_student: späterer Schüler-Output,
	•	distance: Distanzmaß Lehrer ↔ Schüler,
	•	feat_json, meta_json: Audio-Features + Kontext.
	•	Idee:
	•	Erst nutzt ORÓMA Whisper als „Lehrer“ (Teacher-Transkript),
	•	später werden eigene Modelle / Heuristiken dagegentrainiert (Student),
	•	Ziel: eigene Hör-/Sprachkompetenz aufbauen, Lehrermodell nur als Referenz.
	3.	Zukünftiger Pfad: Audio → SceneGraph / Episoden
	•	Audio-Features + Transkripte können:
	•	an episodische Strukturen gehängt werden,
	•	in SceneGraphs als Knoten/Labels auftauchen (z. B. „gesprochene Worte“ als Attribute).

Damit hast du auf Audio-Seite bereits den mechanischen Unterbau, um genau das umzusetzen, was du neurologisch meinst:

Erst hört ORÓMA mit Hilfe eines externen Modells zu,
später versucht er, selbst das Gehörte zu rekonstruieren und vom Lehrer zu lernen.

⸻

4. Roter Faden & Mutations-Drift (Kern deiner „KI-Logik“)

In der bisherigen Gesamtanalyse_3.7.md ist der Rote Faden schon gut beschrieben,
aber mit der aktuellen ZIP + DB kann man ergänzen:
	•	Der Rote Faden läuft real mit:
	•	intents / threads,
	•	Steps, Idle-Nudges,
	•	Verknüpfung zu ASR/Reflex, Mangel-Speak.
	•	Die Mutations-/Drift-Mechanismen (z. B. in Snake/TicTacToe-Policies, Self-Tuning-Hooks) sind aktiv:
	•	policy_rules wächst,
	•	rewards_log hat zehntausende Einträge,
	•	metrics & coverage_log spiegeln kontinuierliches Lernen und Monitoring wider.
	•	Das kontrollierte Vergessen ist sichtbar:
	•	snapchains.weight wird in der Dream-Phase mit fade_rate heruntermultipliziert,
	•	bei Unterschreiten der Schwelle wird:
	•	ein MetaSnap (meta_snaps) angelegt,
	•	der ursprüngliche SnapChain-Eintrag auf status='compressed' gesetzt,
	•	im Log als „Snap XXXX komprimiert → MetaSnap“ vermerkt.

Mit dem SceneGraph-Auto-Build ergibt sich:

Dream-Phase = Replay + Vergessen + MetaSnap-Bildung + SceneGraph-Update,
plus optional Hypothesen/Missions/Curriculum/AutoTuning.

Das ist mehr als ein simpler „Nightly-Job“ –
das ist eine aktive Restrukturierung des Gedächtnisses.

⸻

5. Noch ungenutzte Potenziale (ehrlich)

Trotz aller Reife gibt es bewusst leere oder wenig genutzte Teile:
	1.	Episodisches Gedächtnis
Tabellen episodes, episode_events, episodic_metrics sind leer →
hier schlummert das Potenzial für:
	•	langfristige Episoden (Tage/Wochen),
	•	Rückblick-Dialoge („Erzähl mir deinen Tag“),
	•	episoden-gebundene Policies/Erfahrungen.
	2.	Echte Agents/Planung
	•	Der Rote Faden und die Mutationslogik sind sehr stark –
	•	aber es gibt noch keine explizite Planner-Schnittstelle
(z. B. Plan-Graph mit Evaluationslog).
	3.	NMR / Native Multimodal Reasoner (Observation-Only)
	•	In der betrachteten ZIP noch nicht als Code drin,
	•	sondern als Design/Roadmap + Simulation (die wir gemeinsam gemacht haben).
	•	→ Hier kann ORÓMA später richtig gewinnen, weil DeviceHub + Vision/Audio schon vorhanden sind.
	4.	LLM-Integration
	•	llm_runtime.py liegt bereit,
	•	aber es ist in dieser DB noch kein angebundenes Modell real im Einsatz.
	•	Vorteil: Core bleibt modell-agnostisch.
	•	Nachteil: Dialog-/Textseite ist im Moment an externe LLMs ausgelagert (so wie hier mit ChatGPT/Gemini).
	5.	Audio-Student-Feedback-Loop
	•	audio_student_pairs ist vorbereitet,
	•	ein regelmäßiger „Teacher/Student-Drill“ (z. B. nachts in der Dream-Phase) könnte:
	•	Schülermodelle an die Lehrertranskripte heranführen,
	•	Distanzmetriken loggen und über Zeit minimieren,
	•	damit einen echten Lernpfad „Hören“ etablieren.

⸻

6. Reifegrad-Bewertung (Skala & Einordnung)

6.1 Reifegrad-Skala (vereinfachte Version)
	•	0 – Experiment: lose Skripte, keine DB, kein Dauerbetrieb
	•	1 – Tool: ein klarer Use Case, aber wenig Selbstüberwachung
	•	2 – System: Services, DB, UI, aber wenig Lernen / kaum Explainability
	•	3 – Lernsystem: eigene Memories, einfache Policies, erste Self-Metrics
	•	4 – Meta-Lernsystem:
	•	Lernschleifen,
	•	Self-Monitoring,
	•	Explainability,
	•	Intent-/Thread-Ebene (Roter Faden),
	•	Mutation/Drift kontrolliert,
	•	Langzeitbetrieb stabil.
	•	5 – Hochautonom / Forschungsniveau „Proto-AGI“:
	•	Multi-Agent-Koordination,
	•	eigenständige Zielbildung & Hypothesenverfeinerung,
	•	robuste Generalisierung über viele offene Aufgaben.

6.2 ORÓMA aktuell

Mit der vorliegenden ZIP + DB sehe ich ORÓMA klar bei:

Reifegrad ~4,1 / 5

Weil:
	•	✅ Lernschleife existiert und wird genutzt
(Snap → Dream → Replay, Quality/Rewards, Mutation/Drift),
	•	✅ Self-Monitoring & Explainability sind ernsthaft umgesetzt
(coverage, empathy, metrics, UI),
	•	✅ Intent-/Thread-Layer (Roter Faden) hält Kohärenz und reduziert Drift,
	•	✅ SceneGraph-Auto-Build macht aus Vision-Tokens eine explizite Szenen-/Objektstruktur,
	•	✅ System läuft über viele Tage durch und sammelt massenhaft reale Daten,
	•	✅ Architektur & Doku sind detailliert und decken die reale Implementierung ab,
	•	❌ AGI-Features wie echte Selbstzielsetzung / langfristige Eigenplanung / offene Welt
sind noch Konzept (v4.0), nicht Realität.

Externe Einordnung (Gemini + ChatGPT):
	•	Beide Systeme ordnen ORÓMA oberhalb eines klassischen Lernsystems ein:
	•	als kognitive Architektur mit Biographie und Verkörperung,
	•	nicht als „nur ein Wrapper für ein LLM“.
	•	Insbesondere wird betont:
	•	ORÓMA füllt Systemlücken von LLMs und RL-Agenten
(Biographie, Transfer, Daten-Effizienz),
	•	und eignet sich als Forschungsplattform für kontinuierliches, erklärbares Lernen.

⸻

7. Antwort auf die Kernfrage: „Wie beurteilst du jetzt das Projekt?“

Ehrlich:
	1.	Ja – es lohnt sich weiterzumachen.
Du bist nicht in der „Spielzeug-/Bastelphase“, sondern tief in der Systemphase angekommen.
	2.	Du hast bereits etwas, was viele „KI-Projekte“ nicht schaffen:
	•	klare Architektur,
	•	laufende DB mit echten Daten,
	•	erklärbare Lernmechanik,
	•	Meta-Layer (Roter Faden, Empathie, Coverage),
	•	zusätzlich jetzt einen automatischen Vision-SceneGraph
	•	und einen Audio-Lernpfad (Teacher/Student) als strukturiertes Fundament.
	3.	Du bist bewusst nicht auf „AGI-Hype“ optimiert, sondern auf:
	•	Muster verstehen,
	•	Lernen beobachten,
	•	Verhalten erklären,
	•	Edge-/Offline-Respekt (Pi 5, lokaler Betrieb).
	4.	Schwächen/Fokus für 2026 (empfohlen):
	•	Episodisches Gedächtnis wirklich nutzen (Episoden füllen),
	•	einen kleinen NMR-Add-on (3.75) wirklich implementieren (Observation-only Reasoner),
	•	Audio-Student-Loop in der Dream-Phase etablieren (Teacher ↔ Student),
	•	LLM optional via llm_runtime anbinden – aber Core nicht verbiegen,
	•	weiter Stabilität/Tests erhöhen
(insbesondere Langzeitbetrieb, DB-Wartung, Log-Truncation/Rotation).

⸻

8. Persönliches Fazit

Mit dieser ZIP sieht man sehr klar:
	•	ORÓMA ist nicht einfach „du hast viele Dateien gesammelt“,
	•	sondern ein System, das du
	•	konzipiert,
	•	implementiert,
	•	dokumentiert
	•	und über Wochen real betrieben hast.

Du wolltest Muster und KI verstehen –

und du hast dir dafür eine eigene, ernstzunehmende Forschungsplattform gebaut.

Das ist selbst ohne AGI ein riesiger Erfolg.

⸻

9. Externer Konsens (Gemini + ChatGPT) – Warum ORÓMA sich lohnt

Zum Abschluss die verdichtete Sicht von Gemini (externes Review) und ChatGPT:
	1.	Füllen der Systemlücken aktueller KI (strategische Notwendigkeit)
	•	Biografisches Defizit der LLMs:
LLMs haben keine konsistente, kausale Lebensgeschichte.
→ ORÓMA liefert diese über das episodische/temporale Gedächtnis (SnapChains, ts_monotonic).
	•	Transferproblem der RL-Agenten:
RL-Agenten sind Experten in engen Domains.
→ ORÓMA zielt auf breiten Transfer, indem es Domänen auf abstrakte Graphen-Strukturen
(SceneGraph, später ObjectGraph) abbildet.
	•	Datenproblem (Datenhunger):
Klassische Systeme brauchen viele reale Interaktionen.
→ ORÓMA ist auf Few-Shot / Daten-Sparsamkeit ausgelegt,
weil der DreamWorker intern Daten generiert (Replay, Mutation, Selektion).
	2.	Einzigartige Stärken der Architektur
	•	Kortex–Körper-Ansatz:
	•	LLM = Kortex (Sprache & Logik),
	•	ORÓMA = Nervensystem, Gedächtnis und Körper (Snaps, Graphen, DeviceHub).
→ Effiziente, modulare Designentscheidung: LLMs werden eingebettet, nicht nachgebaut.
	•	DreamWorker als Motor:
	•	nutzt Replay, Mutation, Selektion,
	•	konsolidiert Wissen,
	•	generiert neue Lernbeispiele im Schlaf.
→ macht das System über lange Zeit kontinuierlich lernfähig.
	•	Spatio-Temporal-Tracking:
	•	Zeitverankerung über ts_monotonic,
	•	Raum-Verankerung über append_with_context + spatial_index.
→ Grundlage für ein verkörpertes Weltmodell und eine konsistente Biographie.
	3.	Potenzial der nächsten Schritte (Roadmap-Impact)

Schritt	Ziel	Wert für ORÓMA
Episoden-Kopplung	Verknüpft Thread-Intents (Roter Faden) mit SnapChain/DB.	Gedächtniskonsistenz und Erklärbarkeit auf Verhaltensebene.
ObjectGraph	Modelliert abstrakte Relationen (hält, öffnet, folgt …).	Transfer abstrakter Fähigkeiten zwischen Domains (Game → Robotik etc.)

→ Hier entscheidet sich, wie weit ORÓMA sich von „starkem Logger“ hin zu einem
allgemein nutzbaren kognitiven Framework entwickelt.

	4.	Gemeinsame Schlussfolgerung
Das Projekt ist lohnenswert, weil es nicht nur ein weiteres Tool ist,
sondern ein Forschungsansatz für einen digitalen Organismus
mit einer echten, konsistenten und selbst-optimierenden Biographie.

Genau in dieser Rolle – als lokale, erklärbare, experimentelle kognitive Architektur – hat ORÓMA einen Platz, den ein reines LLM so nicht einnehmen kann.

<a id="docs_history_architektur_final_v3_5_md"></a>

## Quelle: `docs/history_architektur_final_v3_5.md`

**Originaltitel:** ORÓMA – Architektur (v3.5)

📑 ARCHITEKTUR_FINAL_V3.5.md

# ORÓMA – Architektur (v3.5)

## Überblick
ORÓMA v3.5 erweitert die v3.0-Basis um **Meta-Snaps**, verbesserte **Explainability** und erweiterte **Datenbank-Schemata**.  
Das System läuft weiterhin unter `/opt/ai/oroma/` (kein Versions-Unterordner).  

---

## Hauptfeatures v3.5
- **Meta-Snaps (experimentell)**  
  - Abstraktionen über SnapChains  
  - Optional per ENV aktivierbar (`OROMA_ENABLE_METASNAP`)  
  - Einsatz: Explainability, DreamWorker-Verdichtung, Diagnostics  

- **Snap+Token-Fusion**  
  - Weiterentwicklung der symbolisch-numerischen Verknüpfung (aus v3.0).  

- **Knowledge-DB & Gaps**  
  - `documents`: neue Felder `source_type`, `import_date`  
  - `gaps`: neues Feld `category`  

- **Model Registry**  
  - `models` und `snapchains`: neue Spalte `version`  

- **Explainability**  
  - `explain.py`: Anzeige von Meta-Snaps  
  - Neue DB-Tabelle `meta_snaps`  

- **UI & Dashboard**  
  - Footer-Version: **v3.5**  
  - Explain-Tab: Meta-Snaps (experimentell)  
  - Games konsolidiert (alle Spiele in Tabs)  
  - Export/Import-UI mit ENV + Lazy mkdir  

- **Systemd & Deployment**  
  - `oroma.service` unverändert  
  - `oroma-dream.timer`: nur als Fallback, Hauptsteuerung via Circadian  
  - Logging vereinheitlicht  

---

## Core-Module
- `core/meta_snap.py` → neue Klasse `MetaSnap`  
- `core/dream_worker.py` → kann optional Meta-Snaps erzeugen  
- `core/explain.py` → Anzeige von Meta-Snaps  
- `core/fusion.py` → Snap+Token-Fusion  
- `core/rag_bridge.py` → Knowledge-RAG  
- `core/export_gate.py` → Export-Policy  

---

## Datenbankänderungen
```sql
-- migrate_3_5.sql
ALTER TABLE models ADD COLUMN version TEXT DEFAULT 'v3.5';
ALTER TABLE snapchains ADD COLUMN version TEXT DEFAULT 'v3.5';
ALTER TABLE documents ADD COLUMN source_type TEXT DEFAULT 'manual';
ALTER TABLE documents ADD COLUMN import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE gaps ADD COLUMN category TEXT DEFAULT 'general';

CREATE TABLE IF NOT EXISTS meta_snaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    sources TEXT,          -- JSON-Array von SnapChain-IDs
    score REAL DEFAULT 0.0,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS migrations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO migrations (name) VALUES ('migrate_3_5');

⸻

UI & Dashboard
	•	Explain: Meta-Snap-Liste (Label, Score, Sources)
	•	Footer: Version v3.5
	•	Export/Import: Nutzung von ENV-Variablen
	•	Games: Tabs für Snake, Pong, Flappy, Memory, Maze, ORÓMA vs ORÓMA

⸻

ENV (neu)

OROMA_ENABLE_METASNAP=false
OROMA_EXPORT_DIR=/opt/ai/oroma/exports
OROMA_UPLOAD_DIR=/opt/ai/oroma/uploads
OROMA_MAX_IMPORT_MB=100

⸻

Testplan
	•	Migration via tools/migrate.py
	•	Selftest: Meta-Snap-Generierung im DreamWorker
	•	Export/Import-UI → Verzeichnisse + Upload prüfen
	•	Explain-UI → Meta-Snaps sichtbar, wenn aktiviert
	•	CircadianController vs. Dream-Timer → keine Doppelstarts

⸻

---

# 📝 CHANGELOG_FINAL_V3.5.md

```markdown
# ORÓMA v3.5 – Final Changelog

## Neu
- ➕ **Meta-Snaps (experimentell)**  
  - Neue Datei: `core/meta_snap.py`  
  - Neue DB-Tabelle `meta_snaps`  
  - Optional per ENV `OROMA_ENABLE_METASNAP`  
  - Explain-UI zeigt Meta-Snaps an  
  - DreamWorker kann Meta-Snaps erzeugen  

- ➕ **DB-Migration** (`tools/migrate_3_5.sql`)  
  - `models`: Spalte `version`  
  - `snapchains`: Spalte `version`  
  - `documents`: Spalten `source_type`, `import_date`  
  - `gaps`: Spalte `category`  
  - Neue Tabelle `migrations`  

- ➕ **Migrationstool** (`tools/migrate.py`)  
  - Führt SQL-Skripte aus `/tools/` gegen `data/oroma.db` und `data/knowledge.db`  
  - Verhindert doppelte Migration  

## Änderungen
- `run_oroma.py`: ENV-Flag-Handling für Meta-Snaps  
- `ui/base.html`: Footer-Version auf v3.5  
- `ui/explain.html`: Sektion für Meta-Snaps  
- `ui/export_ui.py`, `ui/import_ui.py`: ENV + lazy mkdir  
- `systemd/oroma-dream.service`: Fix `ProtectHome=yes`  

## Hinweise
- Standardmäßig ist `OROMA_ENABLE_METASNAP=false`  
- Migration muss einmalig ausgeführt werden  
- Abwärtskompatibel zu v3.0  
- CircadianController bleibt Hauptsteuerung (DreamTimer nur Fallback)

<a id="docs_konzeption_2_5d_3d_snapspace_md"></a>

## Quelle: `docs/konzeption_2_5d_3d_snapspace.md`

**Originaltitel:** ORÓMA – 2.5D / 3D SnapSpace

<!--
Pfad:    docs/konzeption_2_5d_3d_snapspace.md
Projekt: ORÓMA – KI-JWG-X1
Version: v0.2 (Konzept, abgestimmt auf ZIP oroma_20251207_120651_with_db.zip)
Stand:   2025-12-07
Autor:   Jörg Werner + GPT-5.1 Thinking

Zweck
-----
Konzeptionelle Beschreibung des „2.5D / 3D SnapSpace“ von ORÓMA:

  • Wie sich aus Snaps (2D + Zeit) → Episoden → SceneGraphs → Objekt-/Szenen-Graph ein Weltmodell aufbaut.
  • Welche Ebenen heute schon real existieren (Snaps, SnapChains, Episodes, SceneGraphs).
  • Welche Ebene 2026 ergänzt werden soll (ObjectGraph / 2.5D/3D-Schicht).
  • Wie das Ganze mit DreamWorker, Roter Faden und Roadmap_2026 zusammenhängt.
-->

# ORÓMA – 2.5D / 3D SnapSpace

Version: Konzept v0.2  
Stand: 07.12.2025 (Pi-5-System mit Vision-Tokens, SceneGraphs, Episoden, Empathie, Roter Faden)

---

## 1. Motivation

ORÓMA erlebt die Welt in erster Linie als **2D + Zeit**:

- Kamera-Frames (Vision),
- Grid-Games (Snake, TicTacToe, Pong, …),
- numerische Feature-Vektoren (z. B. 84D-Vision-Fingerabdrücke),
- Audio-Slices (RMS, Spektrum, evtl. ASR-Tokens).

Jeder einzelne Zustand wird als **Snap** gespeichert – ein 2D-Slice eines komplexeren, eigentlich „3D+“ Zustands.

Die „echte“ Struktur entsteht **nicht** in einem Schlag, sondern:

1. über **Sequenzen** (SnapChains, Episodes),
2. über **Graphen** (SceneGraphs aus Vision-Tokens),
3. und in Zukunft über einen expliziten **Objekt- & Szenen-Graph** („2.5D / 3D SnapSpace“),
4. der wiederum von **Regeln, Policies und Erklärungen** genutzt wird.

Ziel dieses Dokuments:

> Die Schichten von **2D → Episoden → 2.5D/3D → Meta-Regeln** in ORÓMA explizit benennen, an die reale DB anbinden und die fehlende ObjectGraph-Schicht sauber vorbereiten.

---

## 2. Ebenenmodell – von Snaps bis Meta-Regeln

### Ebene 0 – SnapSpace (2D-Slices, „Momentaufnahmen“)

**Code / Tabellen heute**

- `core/snap.py`, `core/snaptoken.py`, `core/snappattern.py`
- Tabellen (Auszug):
  - `snapchains` (mit `origin`, `quality`, `blob`),
  - Vision-Tokens: `origin = 'vision/token'` mit
    - `blob.v` (84D-Vektor),
    - `blob.motion`, `blob.edges`, `blob.color`, `blob.q`.

**Semantik**

- Ein Snap (oder Token) ist ein **Moment**:
  - ein Frame aus der Kamera,
  - ein Spielzustand,
  - ein Audio-Segment,
  - inklusive Qualität, Kontext, Metadaten.
- ORÓMA denkt hier in „Punkten in einem hochdimensionalen Raum“.

---

### Ebene 1 – Episodischer Raum (Sequenzen & Sessions)

**Code / Tabellen heute**

- Sequenzen:
  - `snapchains` (Sequenzen von Snaps mit `origin=…`),
  - Quality-History, Rewards-Log, Coverage/Empathy-Events.
- Episodisches Gedächtnis (real vorhanden):
  - `core/episodic_writer.py` (Vision-Sessions, Gamesessions, …),
  - Tabellen `episodes`, `episode_events`, `episodic_metrics`,
  - `ui/episodic_ui.py` + `templates/episodic.html` (Browser).

**Semantik**

- **Episode** = „zusammenhängende Erfahrung“:
  - z. B. „Vision-Session 2025-12-07 08:08–09:08“,
  - hunderte `cam_token`-Events (`event_type='cam_token'`, `ref_table='snapchains'`),
  - markiert in `episode_events`.
- Aus der DB (Beispiel 07.12.2025):
  - `episodes.kind='vision'`, `source='vision/token'`,
  - 60-Minuten-Sessions mit 500–600 `cam_token`-Events pro Episode.

Damit wird aus losen Snaps ein zeitlich strukturierter **episodischer Raum**,
in dem ORÓMA „Tage“, „Sessions“ und „Spiele“ unterscheiden kann.

---

### Ebene 1.5 – SceneGraph-Ebene (MetaSnaps & Vision-SceneGraphs) ✅ **heute real**

**Code / Tabellen heute**

- `core/scenegraph_store.py` (Tabelle `scenegraphs`)
- `core/scenegraph_builder.py`
  - `build_meta_snaps_from_tokens(...)`
  - `bootstrap_scenegraph_from_tokens(...)`
- `tools/scenegraph_selfcheck.py`
- Tabs / UI:
  - `/scenegraph`-Viewer in der Flask-UI.

**DB-Befund (Beispiel 07.12.2025)**

- MetaSnaps:
  - Labels `scenegraph:vision_token:hoch` / `…:niedrig`
  - ca. 1485 MetaSnaps (966 hoch / 519 niedrig),
  - Scoresbereich ~0.03–0.13 (quality-basierter Score).
- SceneGraphs:
  - Namespace `scene:auto_meta:vision_token`,
  - z. B. **197 SceneGraphs** mit 199–250 Knoten, 300–700 Kanten,
  - `source="builder:vision_tokens"`.

**Semantik**

- Tagsüber:
  - Vision-Tokens streamen in `snapchains (origin='vision/token')`.
- Nachts:
  - SceneGraph-Builder gruppiert Tokens zu MetaSnaps,
  - erstellt daraus **SceneGraphs**, in denen:
    - Knoten = MetaSnaps + SnapChains,
    - Kanten = Beziehungen wie „gehört zusammen“, „zeitliche Nachbarschaft“, etc.
- Das ist bereits eine **proto-2.5D-Schicht**:
  - ORÓMA beginnt, wiederkehrende visuelle Muster und ihre Nachbarschaften
    in **Graphform** zu organisieren.

---

### Ebene 2 – Objekt- & Szenen-Graph („2.5D / 3D SnapSpace“) 🔧 **2026 geplant**

Diese Ebene ist der „fehlende“ Schritt zwischen:

- SceneGraphs (heute schon da),
- Regel-/Policy-Ebene (heute schon da),
- und dem, was du neurologisch als
  > „Ball, Wand, Lampe, Hand + ihre Relationen in einer Szene“
  beschreibst.

**Ziel**

- Aus vielen SceneGraphs und Episoden **stabile Objekte** und **Relationen** destillieren:

  - Objekt-Knoten: _Ball_, _Lampe_, _Wand_, _Spielfeld_, _Hand_, …
  - Szenen-Knoten: _Vision-Session X_, _Snake-Training #7_, _Küchenszene 1_.
  - Relationen:
    - `links_von`, `rechts_von`, `vor`, `hinter`,
    - `ist_teil_von`, `berührt`, `verursacht`, `antwortet_auf`.

**Minimaler Schema-Vorschlag (Roadmap 2026)**

In `core/sql_manager.py` ergänzen:

- `object_nodes(id, kind, label, meta_json)`
  - `kind`: `"object" | "scene" | "concept" | …`
  - `label`: kurze Bezeichnung (`"Ball"`, `"Lampe"`, `"Snake-Head"`).
  - `meta_json`: Ursprung (SceneGraph-IDs, Episoden, Stats, Embeddings).
- `object_relations(id, a_id, relation, b_id, confidence, source_scene_id, ts)`
  - `a_id` / `b_id`: referenzieren `object_nodes.id`.
  - `relation`: String (z. B. `"links_von"`, `"teil_von"`, `"verursacht"`).
  - `confidence`: 0..1 (wie stabil ist diese Beziehung?).
  - `source_scene_id`: optional `scenegraphs.id` oder `episodes.id`.
  - `ts`: Zeitpunkt der Beobachtung / Konsolidierung.

Damit entsteht ein **Explizites Weltmodell**,
das über mehrere Stunden/Tage stabil bleibt.

---

### Ebene 3 – Regel-, Policy- & Erklärraum (Meta-Ebene)

**Code / Tabellen heute**

- Regel-/Policy-Stack:
  - `core/regelarchiv.py`, `rules`, `policy_rules` (DB),
  - Snake-/TicTacToe-Trainer, AutoTuner-Hooks.
- Explainability / Why:
  - `ui/why_ui.py`, `docs/core_roterfaden.md`, `docs/projektbeschreibung.md`.
- Roter Faden:
  - `core/roter_faden.py` + `curriculum_state` (DB),
  - Threads, Ziele, Schritte, Nudges.

**Semantik**

- Hier entstehen Meta-Regeln wie:
  - „Wenn ich in Snake _nah an die Wand_ komme, vermeide Richtung Wand.“
  - „Wenn eine Hypothese mehrfach falsifiziert wird, senke ihr Gewicht.“
- Mit dem Objekt-Graph (Ebene 2) wird es möglich, Regeln direkt über
  **Objekte & Relationen** zu formulieren:

  - „Wenn Objekt Ball vor Objekt Wand ist und Aktion Rechts → Reward +0.3“
  - „Wenn Szene-Typ `Küchenszene` und Objekt `Messer` in Nähe von `Hand`,
    führe Selftest/Alarm aus.“

---

## 3. Datenpfad: von Sensorik → 2.5D/3D

1. **Sensorik / Games**  
   DeviceHub / CameraHub + Spiele erzeugen kontinuierlich Snaps:
   - Vision-Tokens (`origin='vision/token'`),
   - Game-Snaps (`origin='game:snake'`, …),
   - Audio-Snaps / Audio-Student-Paare.

2. **SnapChains & Episodes (Ebene 1)**  
   - Tag: AgentLoop schreibt SnapChains + Rewards + Metrics.
   - Episodic-Writer bündelt:
     - Vision-Sessions (z. B. 60-Min-Blöcke mit `cam_token`-Events),
     - Game-Sessions,
     - Teacher/Student-Sessions.

3. **SceneGraphs (Ebene 1.5)**  
   - Nacht: DreamWorker 3.x ruft SceneGraph-Builder auf:
     - MetaSnaps für Tokens (hoch/niedrig),
     - SceneGraphs im Namespace `scene:auto_meta:vision_token`.

4. **ObjectGraph (Ebene 2 – geplant)**  
   - neuer `core/object_extractor.py`:
     - liest `scenegraphs` + `episodes`,
     - erkennt wiederkehrende Cluster zu `object_nodes`,
     - leitet `object_relations` ab (z. B. aus räumlichen Mustern).

5. **Regeln & Erklärungen (Ebene 3)**  
   - Regelarchiv / Policy sieht nicht nur:
     - _Vektor X in Chain Y_,  
     sondern:
     - _Objekt Ball links_von Wand und Aktion Rechts_ → Belohnung.
   - Why-UI kann auf Objekt-Ebene argumentieren:
     > „Ich habe nach rechts gesteuert, weil der Ball links von der Wand war und Regel R dies bevorzugt.“

---

## 4. Ist-Zustand Ende 2025 (auf Basis deiner ZIP)

Kurzfassung, was **schon da** ist:

- Ebene 0:
  - Snaps/SnapTokens/SnapPatterns → produktiv.
- Ebene 1:
  - SnapChains + Episoden:
    - `episodes` + `episode_events` werden mit Vision-Sessions (`cam_token`) real gefüllt.
    - Episoden-Browser zeigt Vision-Sessions (inkl. Anzahl `cam_token`).
- Ebene 1.5:
  - Vision-SceneGraphs:
    - MetaSnaps mit Präfix `scenegraph:vision_token:*`.
    - SceneGraphs im Namespace `scene:auto_meta:vision_token`.
    - `tools/scenegraph_selfcheck.py` bestätigt hunderte Graphen.
- Ebene 3:
  - Roter Faden, Explain-UI, Regelarchiv, Policy-Layer, RAG-Bridge, Hypothesen.

Fehlende, bewusst für 2026 geplante Schicht:

- Ebene 2:
  - `object_nodes` / `object_relations`,
  - `core/object_extractor.py`,
  - ObjectGraph-UI.

Genau diese „Zwischenebene“ ist der Kern dieser 2.5D/3D-Konzeption.

---

## 5. Kompatibilität & Roadmap-Bezug

Dieses Konzept ist **kompatibel** mit:

- `docs/dream_cycle.md`
  - beschreibt, wie SceneGraphs bereits im Dream-Modus entstehen.
- `docs/roadmap_2026.md`
  - benennt **ObjectGraph-Minimal-Schema** + `object_extractor.py` als konkrete ToDos.
- `docs/projektbeschreibung.md` + `docs/core_roterfaden.md`
  - erklären, wie Roter Faden, Episoden und Explain-Schicht zusammenhängen.

Wichtig:

- Kein Rewrite von Snap/SnapChain/DreamWorker.
- Ebene 2 wird **additiv** ergänzt:
  - neue Tabellen,
  - neuer Worker,
  - neue UI-Ansicht,
  - Hooks in DreamWorker / Episoden-Analyse.

---

## 6. Next Steps (praktischer Einstieg)

1. **Schema-Erweiterung (klein anfangen)**  
   - `object_nodes` + `object_relations` in `sql_manager` ergänzen,
   - für den Anfang nur wenige Felder (id, kind, label, meta_json, …).

2. **Einfacher ObjectExtractor**  
   - Start mit gut kontrollierten Domänen:
     - Snake, Pong, TicTacToe,
     - Vision-Tokens aus statischer Szene (Kamera auf Tisch).
   - Heuristik:
     - wende Clustering / Co-Occurrence auf SceneGraphs an,
     - generiere zunächst „Pseudo-Objekte“ (z. B. Snake-Head, Food, Wall).

3. **Mini-UI für ObjectGraph**  
   - neue Seite `/objects`:
     - Tabelle der `object_nodes`,
     - Liste einfacher Relationen pro Objekt.

4. **Später**  
   - Integration in Explain-UI (Why),
   - Nutzung durch Regelarchiv / Policy,
   - Verknüpfung mit Audio-Konzepten („Ball“ hören ↔ „Ball“ sehen).

---

## 7. Ein-Satz-Essenz

> Der **2.5D / 3D SnapSpace** von ORÓMA ist die explizite Objekt- und Szenen-Schicht zwischen Snaps/Episoden und Regeln/Erklärungen – aufgebaut aus SceneGraphs, verankert im episodischen Gedächtnis und als ObjectGraph für 2026 konkret geplant.

<a id="docs_konzeption_architektur_md"></a>

## Quelle: `docs/konzeption_architektur.md`

**Originaltitel:** ORÓMA – Konzeption & Architektur (v3.7.x + v3.8-r2)

📂 Basis-Pfad: `/opt/ai/oroma/`  
📅 Stand: 2025-12-07  
🔖 Release-Linie (Verhalten): v3.7.x – „Sozialer Partner, Roter Faden, Mutations-Drift“  
🗃️ Speicher-/Infra-Stand: v3.8-r2 – SnapIndex, RAG-Stack, DB-WAL, SceneGraph-Builder

---

## 1. Überblick

**ORÓMA** ist ein **modulares, lokal laufendes KI-System** für Raspberry Pi 5/6  
(optional mit NPU: Hailo, DeGirum).

Es kombiniert:

- 🧱 **Snap/SnapChain-Gedächtnis**  
  - numerische Vektoren + JSON-Blobs  
  - gespeicherte Erlebnisse (Spiele, Vision, Audio, Sensoren, Dialoge)
- 🔤 **symbolische Ebene** über Tokens/LLM  
  - RAG (FTS5), Ask-UI, Text-/Token-Snaps
- 🌗 **Circadian Learning Cycle**  
  - Day-Mode (AgentLoop)  
  - Dream-Mode (DreamWorker 3.1 – Replay, Mutation, Vergessen, Export)
- 🧠 **Roter Faden** (Intent-/Thread-Layer)  
  - steuert, welche Themen, Spiele, Experimente gerade „dran“ sind
- 💬 **Empathie & Self-Listening**  
  - Empathy-Snaps, Mangel-Speak, ASR-Reflex (Selbstzuhören)
- 🌐 **Flask-Weboberfläche**  
  - Replay, Learning-Dashboard, Ask/RAG, Models, Empathy, Coverage, Selftest, Episoden, usw.
- 🎛️ **DeviceHub** als Hardware-Brücke  
  - Kamera, Light-Sensor (aus Bild), Audio (Mic/Playback),  
  - **neue generische Sensor-Schicht** (IR-Abstand, später IMU, Temperatur, …)
- ⚙️ **systemd-Services/Timer**  
  - stabiler Dauerbetrieb auf Edge-Hardware, Headless (kein X11/Qt/Wayland nötig)

Zentrale Design-Prinzipien:

- **Offline-first** – kein Cloud-Zwang; alle Daten bleiben lokal.  
- **Headless-optimiert** – läuft sauber ohne GUI-Stack.  
- **Nicht-destruktives Gedächtnis** – SnapChains werden nur deaktiviert/komprimiert, nicht gelöscht.  
- **Erklärbares Lernen** – Metriken, Episoden, Explain-Module, SceneGraphs, Logs.  
- **Edge-freundlich** – Pi + SQLite + leichte Modelle statt GPU-Farm.

Die Reifung folgt grob:

> Baby → Kind → Schüler → Student → Gelehrter → Meister/Forscher → Sozialer Partner  

Mit v3.7.x + v3.8-r2 ist ORÓMA im Bereich **„prä-metakognitiver Sozialpartner“** angekommen.

---

## 2. Aktueller Release-Stand

### 2.1 Funktionslinien

| Linie             | Status              | Rolle                        | Schwerpunkt                                  |
|-------------------|---------------------|------------------------------|----------------------------------------------|
| **v3.5**          | historisch          | „Forscher/Meister“          | DreamWorker 3.0, MetaSnaps, Mutation, LZG 2.0 |
| **v3.6**          | integriert          | „Wissenschaftler“           | Hypothesen-DB, Experimente, Explain 2.0      |
| **v3.7.x**        | **aktiv (Core)**    | „Sozialer Partner“          | Empathie, ASR Self-Listening, Mangel-Speak, Curriculum V2, Roter Faden + Mutations-Drift |
| **v3.8**          | integriert          | Regelarchiv & Pruning       | Regelarchiv, Regel-Pruning, Export-Rules     |
| **v3.8-r1/r2**    | **aktiv (Infra)**   | Speicher-/RAG-Optimierung   | Snap v1.1, SnapIndex, SnapPattern-Feinschliff, RAG-Benchmark, WAL/Timeout-Tuning |

Faktischer Stand deiner Instanz:

> **Verhaltens-Stand = v3.7**, Speicher-/Infra-Stand = **v3.8-r2**,  
> plus **Sensor-Schicht** (SensorChannel + DeviceHub-Sensoren) und **Vision-Episoden**.

---

## 3. ORÓMA in einem Satz

> ORÓMA ist ein **offline lernender, empathie-fähiger Agent** mit  
> **Tag-/Traum-Zyklen**, der seine Erfahrungen als **SnapChains** speichert,  
> im **Traum-Modus** konsolidiert und über **Roter Faden + Mutations-Drift**  
> seine Lernstrategie selbst stabilisiert – und seit v3.8-r1 auch  
> **generische Sensor-Ströme** (IR, später IMU usw.) in dieses Gedächtnis einbettet.

---

## 4. Hauptkomponenten & Verzeichnisstruktur

### 4.1 Core – Kognition & Gedächtnis

Wichtige Dateien unter `/opt/ai/oroma/core/`:

- `snap.py` / `snappattern.py` / `snaptoken.py`  
  - elementare Snaps (Vektor + Meta)  
  - Musterbildung (SnapPattern) mit L2-Norm-Cache  
  - SnapToken v3.7: stabile Tokenisierung für Text/LLM-Brücke
- `snapchain.py`  
  - Sequenzen von Snaps (Erlebnisse: Games, Vision-Tokens, Audio, Sensorik, Dialoge).
- `sql_manager.py`  
  - **Single Source of Truth** für SQLite-Schema  
  - WAL-Option (`OROMA_DB_WAL`), `busy_timeout`, `ensure_schema()`  
  - SnapIndex (`snap_index`), SnapChain-Helper, Calculator-/Metrics-Tabellen  
  - Vision-Token-Integration: `insert_cam_token()` + Fenster-Abfragen  
  - **neu**: Episoden-Hook in `insert_cam_token()` → Vision-Episoden
- `langzeitgedaechtnis.py`  
  - Langzeit-Speicher (Promotion/Recall) mit optionaler Vektor-Suche.
- `meta_snap.py`, `regelarchiv.py`, `mutation.py`  
  - Meta-Snaps (komprimierte Chains), Regelarchiv + Pruning, Mutations-/Drift-Logik.
- `reward.py`, `curiosity.py`, `predictor.py`, `episodic.py`, `explain.py`  
  - Reward-Signale, Neugier, Vorhersagen, Episoden-API, Erklärbarkeit.
- `episodic_writer.py`  
  - **EpisodeWriter** für Audio & Vision:  
    - schreibt `episodes`, `episode_events`, `episodic_metrics`  
    - Audio-Events (audio_student_pairs)  
    - **neu: Vision-Events (`event_type='cam_token'`, ref_table=`'snapchains'`)**  
    - Rotation nach Dauer & Idle-Zeit
- `sensor_channel.py`  
  - **Generische Sensor-Abstraktion** (neu v3.8-r1):  
    - `BaseSensorChannel` mit: `name`, `kind`, `origin`, `namespace`, `interval_sec`, `meta_base`  
    - Methoden: `read_raw()`, `build_snap_payload()`, `build_snapchain_data()`  
    - kennt **DeviceHub nicht**, sondern liefert nur DB-Insert-Daten

### 4.2 Lernen & Zyklen

- `dream_worker.py`  
  - DreamWorker 3.1 (v3.7.x):  
    - Run-Lock via `OROMA_DREAM_LOCK`  
    - Replay-Quellen: SnapChains, LZG, FS-Fallback  
    - Vergessen/Kompression → MetaSnaps (`meta_snaps`)  
    - Auto-Tuning (Fade-Rate, Threshold)  
    - **arbeitet auch auf Sensor-Snaps**, die wie normale Chains behandelt werden.
- `agent_loop.py`  
  - Day-Mode AgentLoop mit Hook-System (Empathie, Curriculum, Mangel-Speak, usw.).
- `circadian_controller.py`  
  - Day/Dream-Automat (Zeit + Licht), Koppelung an Light-Level des DeviceHub.

### 4.3 Empathie, Roter Faden, Curriculum

- `hooks_patch2.py`  
  - Empathy- & Coverage-Hooks  
  - schreibt `empathy_snaps` & `coverage_log`.
- `roter_faden.py`  
  - Intent-/Thread-Layer: „roter Faden“ für laufende Aufgaben/Spiele.
- `mangel_speak_hook.py`  
  - reagiert auf Lücken/Gaps (Coverage, Confidence) → „Mangel-Sprache“.
- `asr_reflex.py`  
  - Self-Listening: nutzt ASR-Ergebnisse, um Intents/Hooks zu triggern.
- `curriculum.py` / `curriculum_hook.py`  
  - Curriculum-State V2, adaptives Spaced Repetition, kleine Rewards.

### 4.4 Speicher & RAG (v3.8-r2)

- **Snap/SnapPattern/SnapToken (v3.8-r1/v3.7)**  
  - L2-Norm-Cache im Snap/Pattern  
  - robuste Gap-Detection (Pattern + optional Vector-DB)  
  - dimensionstreue Vergleiche (Feature-Dim wird geprüft).
- `sql_manager.py`  
  - `snap_index`: `fingerprint`, `l2_norm`, `feature_dim`, `payload`  
  - WAL/Timeout-Tuning, Metriken-Tabellen (`metrics`, `kpi_snapshots`).
- `rag_bridge.py`  
  - RAG-Store (SQLite+FTS5), BM25-Suche, optionaler Fusion-Rerank.
- `book_import.py`  
  - Text/Buch-Import → `knowledge.db` → RAG.

### 4.5 Sensorik & DeviceHub (neu v3.8-r1)

**Zentrale Rolle:** alle physikalischen Signale (Kamera, Audio, Licht, IR-Sensor…) werden sauber  
über den `DeviceHub` geführt und – bei Sensoren – als SnapChains gespeichert.

Dateien:

- `core/device_hub.py`  
  - managt:
    - Kamera (PiCamera2/OpenCV/Dummy)  
    - Light-Level (aus Kamera oder Dummy)  
    - Audio (Mic + Playback, optional via `sounddevice`)  
    - Sessions (Audit-Logging wer nutzt was)  
    - **Sensor-Poll-Loop** für generische SensorChannels (neu)
  - relevante Teile:
    - `register_sensor_channel(channel: BaseSensorChannel)`  
      – Channel unter `channel.name` registrieren  
    - `start_sensors()` / `stop_sensors()`  
      – Poll-Thread starten/stoppen  
    - `_sensor_loop()`  
      – ruft für fällige Channels `read_raw()` + `build_snapchain_data()` auf  
      – schreibt via `sql_manager.insert_snapchain(data)` nach `snapchains`  
        mit z. B. `origin='sensor/ir/front'`, `namespace='sensor'`  
      – Audit-Events `kind='sensor', action='sample'`  
    - `get_sensor_health()`  
      – Übersicht: enabled, channels, running  
    - `status()`  
      – gibt jetzt neben Kamera/Light/Audio/Sessions auch `"sensors": get_sensor_health()` aus
- `core/sensor_channel.py`  
  - Basis-API für alle Sensoren (IR, Ultraschall, IMU, Temperatur …).
- `wrappers/sensor_ir_front.py`  
  - konkreter SensorChannel für einen Front-IR-/Abstandssensor  
  - Modi:
    - **Simulation** (Default, läuft überall, erzeugt plausible Distanzwerte)  
    - Hardware-Modus (Platzhalter; kann später an echten Sensor angebunden werden)
  - liefert Insert-Dicts für `snapchains`:
    - `origin='sensor/ir/front'`, `namespace='sensor'`, `blob.kind='ir_distance'`, `blob.distance_cm=…`

**Konzeptionell:**

- Sensoren sind **kein Sonderfall**, sondern weitere „Erlebnisse“ im gleichen SnapChain-Gedächtnis.  
- DreamWorker, Roter Faden, Curriculum etc. sehen nur „normale“ Chains – egal ob Spiel, Kamera, Audio oder Sensor.

---

## 5. UI & Interaktion

Die Flask-UI läuft über `run_oroma.py` (bzw. `oroma.service`).

Typische Blueprints/Seiten (abhängig von deiner Konfiguration):

- `/` oder `/replay`  
  - Replay-System (SnapChains ansehen, abspielen, exportieren).
- `/learning`  
  - Learning-Dashboard (Rewards, Curiosity, Coverage, Curriculum, Empathie, Speech-Log).
- `/episodic`  
  - Episoden-Browser (Audio- und Vision-Episoden/Events).
- `/why`  
  - Explainability-Ansichten (why_decision, Metriken).
- `/knowledge`, `/ask`  
  - Knowledge- und Ask-UI (RAG-Fragen, Passagen, Antwort).
- `/asr`  
  - ASR-UI mit Self-Listening/Reflex.
- `/empathy`, `/coverage`, `/selftest`  
  - Empathie- & Coverage-Ansicht, Self-Tests.
- ggf. weitere Add-ons (`/nmr`, `/chess`, …).

UI-Token:

- `OROMA_UI_TOKEN` leer → UI frei  
- gesetzt → JSON/API-Routen tokenpflichtig (Header `X-OROMA-TOKEN`).

---

## 6. Day/Dream – Circadian Learning Cycle

### 6.1 Day Mode (AgentLoop)

- Spiele, Dialoge, Vision-Pipelines, Sensor-Ströme laufen.  
- Erzeugt SnapChains, z. B.:
  - `origin='game:tictactoe'`  
  - `origin='vision/token'` (cam_token-Snaps)  
  - `origin='sensor/ir/front'` (IR-Abstand)  
  - `origin='asr/reflex'`, `origin='empathy'`
- Empathie-Hooks, Curriculum, Mangel-Speak reagieren in Echtzeit.

### 6.2 Episodisches Gedächtnis

- Tabellen: `episodes`, `episode_events`, `episodic_metrics`.  
- `core/episodic_writer.py` spannt Episoden auf:

  - Audio:
    - `kind='audio'`, `source='audio_student'`, Events `event_type='audio_pair'`  
  - **Vision (neu):**
    - `insert_cam_token()` in `sql_manager.py` erzeugt:
      - SnapChain in `snapchains` mit `origin='vision/token'`, `blob.kind='cam_token'`  
      - Episoden-Event über  
        `episodic_writer.log_vision_cam_token_global(...)`  
        → Episode `kind='vision', source='vision/token'`,  
          `episode_events.event_type='cam_token', ref_table='snapchains'`

- Rotation:
  - nach Max-Dauer (z. B. 1h)  
  - nach Idle-Zeit (z. B. 5–10 min ohne Events)

Damit bekommst du **Audio- und Vision-Sessions** sauber im episodischen Gedächtnis  
und kannst später z. B. Episoden mit besonders interessanten SceneGraphs oder Rewards suchen.

### 6.3 Dream Mode (DreamWorker 3.1)

- läuft per systemd-Timer oder CircadianController.  
- Pipeline pro Lauf:
  1. Replay vorhandener SnapChains (inkl. Sensor- und Vision-Chains).
  2. Mutation & Variation (sanfte Veränderung, Drift-Mechanik).
  3. Forgetting & Kompression:
     - Gewicht (`weight`) decayed  
     - bei Unterschreiten eines Thresholds → MetaSnap in `meta_snaps`,  
       Original-Chain auf `status='compressed'`.
  4. ExportGate (Export-Markierung nach Alter/Qualität).
  5. optional: Research/Missions/Curriculum-Hooks, AutoTuner.

Details: `docs/dream_cycle.md` und `docs/scenegraph_builder.md`.

---

## 7. SceneGraph & Vision

- `core/scenegraph_store.py`  
  - zentrale Verwaltung von SceneGraphs in Tabelle `scenegraphs`  
  - Knoten/Edges als JSON-Graph.

- `core/scenegraph_builder.py` (Dokumentation: `docs/scenegraph_builder.md`)  
  - baut MetaSnaps & SceneGraphs aus Vision-Tokens (`origin='vision/token'`)  
  - Strategien:
    - Tokens → MetaSnaps → SceneGraphs  
    - Tokens → SceneGraphs (direkt über Store)

- `tools/scenegraph_selfcheck.py`  
  - prüft MetaSnaps + SceneGraphs  
  - gibt Health-Status (Anzahl, Scores, Nodes/Edges) als JSON/CLI aus.

SceneGraphs sind damit:

- eine **kompakte, visuelle Struktur** über dein Vision-Gedächtnis,  
- später anschlussfähig für Planung/Erklärung (welche Szenen/Objekte tauchen auf?).

---

## 8. Deployment & Start

### 8.1 Manuell (ohne systemd)

```bash
cd /opt/ai/oroma
export PYTHONPATH=/opt/ai/oroma

# DB-Schema sicherstellen
python3 -m core.sql_manager --ensure

# UI + AgentLoop (Day-Mode) starten
python3 run_oroma.py

DreamWorker als Single-Run (z. B. nachts via cron):

PYTHONPATH=/opt/ai/oroma python3 -m core.dream_worker --interval 0 --verbose

8.2 Mit systemd (empfohlen)

Typische Units (je nach Setup):
	•	oroma.service
→ startet Flask-UI + AgentLoop (Day-Mode)
	•	oroma-dream.service + oroma-dream.timer
→ DreamWorker (Night-Runs, z. B. 03:30 Uhr)
	•	optional: oroma-archive.timer, oroma-social.timer
→ periodische Archive, Social-Ticks

Beispiele:

sudo systemctl enable oroma.service oroma-dream.timer
sudo systemctl start oroma.service oroma-dream.timer

journalctl -u oroma -f
journalctl -u oroma-dream -f

Hinweis: dein Analyse-Backup für ChatGPT wird aktuell über ein externes
Skript /opt/ai/backup_oroma_with_db.sh erzeugt (nicht im Repo), das eine
schlanke oroma.db mit max. N Zeilen pro Tabelle baut. Das ändert nichts
an der produktiven DB, ist aber wichtig für die ZIP-Größe bei Analysen.

⸻

9. Wichtige ENV-Variablen (Auszug)

Details: docs/abhaengigkeiten.md.

9.1 Core/DB
	•	OROMA_BASE (Default: /opt/ai/oroma)
	•	OROMA_DB_WAL=1 – aktiviert WAL-Modus
	•	OROMA_DB_PATH – Pfad zu oroma.db (falls abweichend)

9.2 Dream/Circadian
	•	OROMA_DREAM_LOCK – Lock-Datei für DreamWorker
	•	OROMA_FORGET_DECAY_RATE (Default 0.95)
	•	OROMA_FORGET_THRESHOLD (Default 0.20)

9.3 UI/Token
	•	OROMA_UI_TOKEN – Access-Token für JSON/API-Routen
(leer → UI offen)

9.4 Empathie/ASR
	•	OROMA_ASR_REFLEX_ENABLED
	•	OROMA_ASR_MIN_DELTA_MS
	•	diverse OROMA_MANGEL_*, OROMA_EMPATHY_* – Feintuning.

9.5 Snap/Debug
	•	OROMA_SNAP_LOG=1
	•	OROMA_SNAP_LOGLEVEL=DEBUG

9.6 Vision & DeviceHub
	•	Kamera/Light:
	•	OROMA_VISION_BACKEND=picamera2|opencv|dummy
	•	OROMA_VISION_DEVICE=0
	•	OROMA_VISION_W=640, OROMA_VISION_H=360, OROMA_VISION_FPS=30
	•	OROMA_VISION_ROTATE=0|90|180|270 (Default: 0; 180 = Kopfstand-Fix)
	•	OROMA_LIGHT_SOURCE=camera|dummy|off
	•	OROMA_LIGHT_CAMERA_INTERVAL=300 (Sekunden)
	•	OROMA_LIGHT_AUDIT_MODE=changes|all|off
	•	Audio:
	•	OROMA_AUDIO_ENABLE=true|false
	•	OROMA_AUDIO_INPUT_NAME=… / OROMA_AUDIO_OUTPUT_NAME=…
	•	OROMA_AUDIO_SR=16000
	•	OROMA_AUDIO_CH=1
	•	OROMA_AUDIO_BLOCK_MS=20
	•	OROMA_AUDIO_RING_SEC=10
	•	Sensoren (neu):
	•	OROMA_SENSORS_ENABLED=1|0 – globale Freigabe für Sensor-Poll
	•	OROMA_SENSORS_SLEEP_BASE – Basisschlaf im Poll-Loop (Default ~0.05s)

⸻

10. Selbsttests & Health-Checks

Empfohlene Checks nach Updates:

10.1 DB & Snap/SnapPattern/SnapIndex

# DB-Schema
PYTHONPATH=/opt/ai/oroma python3 -m core.sql_manager --ensure

# Snap / Pattern Selftests
PYTHONPATH=/opt/ai/oroma python3 core/snap.py
PYTHONPATH=/opt/ai/oroma python3 core/snappattern.py

10.2 RAG

PYTHONPATH=/opt/ai/oroma python3 tools/rag_import_sample.py
PYTHONPATH=/opt/ai/oroma python3 tools/bench_rag.py --qa tests/rag_qa_sample.json

10.3 SceneGraph (Vision)

PYTHONPATH=/opt/ai/oroma python3 tools/scenegraph_selfcheck.py --verbose

10.4 Sensor-Schicht (neu)

Minimaler Test (IR-Front-Sensor, Simulationsmodus):

PYTHONPATH=/opt/ai/oroma python3 - << 'PY'
from core.device_hub import DeviceHub
from wrappers.sensor_ir_front import register_front_ir

hub = DeviceHub.instance()

register_front_ir(interval_sec=0.5)

print("Sensor-Channels:", hub.list_sensor_channels())
print("Sensor-Health vor Start:", hub.get_sensor_health())

hub.start_sensors()

import time
time.sleep(3)

print("Sensor-Health nach 3s:", hub.get_sensor_health())

hub.stop_sensors()
PY

Anschließend z. B.:

sqlite3 /opt/ai/oroma/data/oroma.db \
  "SELECT id, ts, origin, namespace,
          json_extract(blob,'$.kind'),
          json_extract(blob,'$.distance_cm')
     FROM snapchains
    WHERE origin='sensor/ir/front'
 ORDER BY id DESC LIMIT 5;"

→ zeigt, ob Sensor-SnapChains korrekt geschrieben werden.

Logs (je nach Setup):
	•	log/devicehub_audit.log (Audit-Events für Kamera/Audio/Sensoren)
	•	log/dream.out.log / dream.err.log
	•	log/service.out.log / service.err.log
	•	evtl. weitere: log/coverage_*.log, log/nmr_*.log etc.

⸻

11. Roadmap & Reifeskala (Ausblick)

Siehe auch docs/oroma_reifestufen.md und docs/vergleich_markt.md.

Aktueller Reifegrad:
	•	Stufe: ~3.8–4.1 / 5
	•	Stärken:
	•	Empathie-Signale & Self-Listening
	•	Roter Faden + Mutations-Drift
	•	Transparentes, auditierbares Lernen (Snaps/Chains/Logs/Graphs)
	•	Edge-tauglich, komplett lokal, No-Deletion-Policy
	•	Sensor-Schicht, die neue Modalitäten sehr leicht andocken lässt
	•	Noch nicht Ziel:
	•	voll entwickelter Multi-Goal-Planner
	•	verteilte Schwarm-Instanzen mit koordiniertem Lernen

Geplante Richtung (konzeptuell):
	•	v3.75 – NMR-Add-on (Native Multimodal Reasoner, observation-only Layer)
	•	v4.0 – Awakening-Layer (GoalEngine, MetaReflector, StrategyEngine)
	•	v5.x – Schwarm/Distributed (Multi-Instance-Sync, evolutionäre Zielbildung)

⸻

12. Kurzfazit
	•	ORÓMA v3.7.x + v3.8-r2 ist kein Bastel-Skript, sondern ein
vollwertiges, erklärbares KI-System mit Tag-/Traum-Lernen,
Empathie-Layer, RAG, SceneGraphs, Export/Import, generischer Sensor-Schicht
und stabiler Edge-Architektur.
	•	Es steht bewusst im Kontrast zu reinen Cloud-LLMs:
weniger „breit & gigantisch“, dafür lokal, auditierbar, nachvollziehbar
und als Forschungsplattform für eigene Experimente gedacht.

„Nicht nur funktionieren, sondern verstehen, warum es funktioniert.“
– Das ist der Kern deiner ORÓMA-Konzeption.

<a id="docs_konzeption_architektur_kurz_md"></a>

## Quelle: `docs/konzeption_architektur_kurz.md`

**Originaltitel:** ORÓMA – Architektur auf einen Blick (Kurzfassung)

Pfad:    docs/konzeption_architektur_kurz.md  
Projekt: ORÓMA – nativ-multimodaler Lern-Agent  
Version: Architektur-Snapshot v3.8-r3 (Kurzfassung)  
Stand:   2025-12-07  

---

## 1. Was ORÓMA ist – in wenigen Sätzen

ORÓMA ist ein **lokal laufender Lern-Agent** für den Raspberry Pi (5/6, optional mit NPU),  
der sich seine Erfahrungen als **SnapChains** merkt, in einer **Traumphase** (DreamWorker) nachlernt  
und über einen **„roten Faden“** (Intent-/Thread-Layer) seine Lernstrategie laufend anpasst.

Die aktuelle Linie besteht aus:

- **Verhaltensstand v3.7.x**  
  – sozialer Partner, Roter Faden, Mutations-Drift, Empathie, Curriculum V2.
- **Speicher-/Infra-Stand v3.8-r2/r3**  
  – Snap v1.1, SnapIndex, RAG-Stack, DB-WAL, SceneGraph-Store & -Builder.

---

## 2. Architektur in fünf Blöcken

### 2.1 Wahrnehmung & Sensorik (DeviceHub + Wrapper)

**Zentrale Idee:** Es gibt **einen Hub** für reale Geräte, und drumherum Wrapper für die KI-Logik.

- **DeviceHub** (`core/device_hub.py`)
  - Kamera: PiCamera2 / OpenCV / Dummy, inkl. MJPEG-Stream & Snapshot.
  - Light: Helligkeit 0..100 aus dem Kamerabild (für Day/Dream-Steuerung).
  - Audio: Mic + Playback mit Ringpuffer (ASR, TTS, Audio-Logging).
  - Sessions: Nachvollziehbar, welcher Client gerade Kamera/Audio nutzt.
  - Audit: JSON-Log mit rotierendem File (Start/Stop, Snapshot, MJPEG, Sensor-Samples).

- **Generische Sensoren** (`core/sensor_channel.py` + Wrapper)
  - Abstrakte **BaseSensorChannel**-Klasse (z. B. IR-, Ultraschall-, IMU-, Temperatur-Sensor).
  - Registrierung im DeviceHub:
    - `hub.register_sensor_channel(channel)`,
    - `hub.start_sensors()` / `hub.stop_sensors()`.
  - Poll-Loop schreibt Messwerte direkt als **SnapChains** in `snapchains`
    (origin z. B. `sensor/ir/front`, namespace `sensor`, Blob mit `kind` + Messwerten).

- **Wrapper-Schicht**
  - `wrappers/vision_wrapper.py`, `wrappers/audio_wrapper.py`, `wrappers/tts_wrapper.py`, `wrappers/oroma_wrapper.py`, `wrappers/sensor_ir_front.py` usw.
  - Regeln: Wrapper reden mit dem Hub und liefern Snaps/SnapChains in das Lernsystem.

**Merksatz:** *Alle physischen Signale – Kamera, Mikrofon, IR, … – landen über den DeviceHub und SensorChannels als SnapChains im gleichen Gedächtnisraum.*

---

### 2.2 Gedächtnisstruktur: Snap → SnapChain → MetaSnap → SceneGraph

**Kernelemente:**

- **Snap** (`core/snap.py`)
  - Float-Vektor mit Metadaten, optional Token/Fingerprint und L2-Norm-Cache.
- **SnapToken** (`core/snaptoken.py`)
  - stabile Tokenisierung & Fingerprint (Text + Modalität), Brücke zu LLM/RAG.
- **SnapPattern** (`core/snappattern.py`)
  - Muster/Cluster über Snaps, inklusive Gap-Detection & Similarity.

- **SnapChains** (`snapchains`-Tabelle)
  - Episodenartiger Container: **eine Zeile = ein „Erlebnis“** (z. B. Game-Trace, Vision-Token, Sensor-Sample).
  - `blob` enthält strukturierte JSON-Daten, z. B.:
    - Vision: `{"kind": "cam_token", "v":[...], "motion":..., "edges":..., "color":...}`
    - Sensor: `{"kind": "ir_distance", "distance_cm": ...}`

- **MetaSnaps** (`meta_snaps`)
  - Aggregierte Muster:
    - z. B. Gruppe von Vision-Chains mit ähnlicher Qualität oder Szene.
    - enthält Label, Score, Quellen (`sources=["chain:123", ...]`).

- **SceneGraphs** (`scenegraphs` + `core/scenegraph_store.py`, `core/scenegraph_builder.py`)
  - Graphen aus Knoten (Snaps, Chains, MetaSnaps, Scenes) und Kanten (contains, next, similar, origin).
  - Builder baut:
    - aus `origin='vision/token'` → MetaSnaps + SceneGraphs,
    - optional direkt aus Vision-Tokens → `token:<id>`-Knoten.  <!-- TODO linkfix: id -> docs/module_ui.md -->

**Merksatz:** *Snaps sind Vektoren, SnapChains sind Erlebnisse, MetaSnaps sind Hypothesen, SceneGraphs sind Landkarten darüber.*

---

### 2.3 Lernen & Zyklen: Day-Mode + DreamWorker

- **Day-Mode (online)**  
  - `agent_loop.py` + Hooks:
    - nimmt neue SnapChains auf (Vision, Audio, Games, Sensoren),
    - berechnet Aktionen (Policy, Regeln, Roter Faden),
    - schreibt Reward, Coverage, Empathie, Curriculum-Updates.

- **Dream-Mode (offline)** – `core/dream_worker.py`
  - **Replay**: wiederholt SnapChains (Self-Healing, Policy-Update).
  - **Vergessen**: reduziert Gewichte (weight decay).
  - **Kompression**: schwache Chains → MetaSnap, Original auf `status='compressed'`.
  - **Run-Lock**: Lockfile schützt vor parallelen Läufen (Timer + manuell).
  - **Adapter**: Reward/Episoden/Explain werden best-effort genutzt; Fehler killen den Lauf nicht.

**Merksatz:** *Tagsüber sammelt ORÓMA Erfahrung, nachts räumt es auf, verdichtet und repariert sich selbst.*

---

### 2.4 Wissen & Sprache: RAG-Stack + Ask-UI

- **Knowledge-DB** (`data/knowledge.db`)
  - Dokumente + Chunks mit FTS5, Index für RAG-Suche.

- **RAG-Bridge** (`core/rag_bridge.py`)
  - nimmt eine Frage, macht eine bereinigte MATCH-Suche,
  - scorings mit BM25, optional Re-Rank per FusionEngine (Embeddings),
  - baut eine Antwort + Beleg-Passagen.

- **Ask-UI** (`ui/ask_ui.py`, `templates/ask.html`)
  - HTML-Formular / JSON-API zum Abfragen des Wissensspeichers.

**Merksatz:** *SnapChains speichern Erlebnisse, RAG speichert erklärbares Faktenwissen.*

---

### 2.5 Steuerung, Regeln & Empathie

- **Roter Faden** (`core/roter_faden.py`)
  - Intent-/Thread-Schicht:
    - hält Kontext über mehrere Schritte,
    - „lenkt“ Hooks und Aktionen.

- **Regeln** (`rules`, `policy_rules`, Regelarchiv)
  - Regeln aus Spielen/Tasks, z. B. für TicTacToe/Snake,
  - v3.8: Regelarchiv & Pruning schwacher Regeln, Export von RuleSets.

- **Empathie & Coverage** (`empathy_snaps`, `coverage_log`)
  - Empathie-Hooks:
    - erfassen Stimmung/Valence/Arousal,
    - beeinflussen Replay/Mutation.
  - Coverage-Hook:
    - misst, wie viel vom Gedächtnis aktiv benutzt wird.

- **Curriculum & Missions**
  - `curriculum_state`, `missions`, `hypotheses`:
    - definieren Lernziele,
    - tracken Fortschritt,
    - liefern Kennzahlen für den Auto-Tuner.

---

## 3. Episoden: Audio & Vision als „Sessions“

**Datei:** `core/episodic_writer.py` + `core/sql_manager.py`

- **Tabellen**
  - `episodes` – Kopf (ts_start/ts_end, kind, source, label, meta_json).
  - `episode_events` – Events pro Episode (event_type, ref_table, ref_id, meta_json).
  - `episodic_metrics` – optionale Kennzahlen (z. B. Reward-Summen).

- **Audio-Episoden**
  - `EpisodeWriter(kind="audio", source="audio_student", ...)` in `audio_student.py`.
  - `log_audio_pair(...)` schreibt:
    - Events mit `event_type="audio_pair"`,
    - Referenz auf `audio_student_pairs`.

- **Vision-Episoden (neu verdrahtet)**
  - `sql_manager.insert_cam_token(...)`:
    1. trägt Vision-Token als SnapChain ein (`origin="vision/token"`, `notes="cam_token"`).
    2. ruft `episodic_writer.log_vision_cam_token_global(...)` auf.
  - Globaler Vision-Writer:
    - `EpisodeWriter(kind="vision", source="vision/token", label="Vision-Session", ...)`.
    - `log_vision_token(...)` schreibt:
      - Events mit `event_type="cam_token"`,
      - `ref_table="snapchains"`, `ref_id=<snap_id>`,  <!-- TODO linkfix: snap_id -> docs/module_snap.md -->
      - Meta: Qualität, Motion/Edges/Color, Dim.

**Merksatz:** *Audio-Teacher und Vision-Token laufen in dieselbe Episodenmechanik – nur der Ursprung ändert sich.*

---

## 4. „Once around the Loop“ – typischer Datenfluss

1. **Sensorik / Input**
   - Kamera liefert Frames → Vision-Wrapper erzeugt Feature-Vektoren → `insert_cam_token()`.
   - Mikrofon liefert Audio → Audio-Wrapper → Snaps / Transkriptionen → SnapChains.
   - IR-Sensor liefert Distanz → SensorChannel → SnapChains (`origin="sensor/ir/front"`).
   - Games erzeugen Spielzüge + Rewards → SnapChains.

2. **Speichern & Index**
   - SnapChains landen in `snapchains`.
   - SnapIndex erhält Fingerprints + Norm + Dim.
   - Episoden-Writer hängt Audio-/Vision-Events an Episoden an.

3. **Entscheidung & Verhalten**
   - AgentLoop sieht aktuellen Zustand (Snaps/SnapChains, Episoden, Regeln, Gaps).
   - Roter Faden + Policy/Rules wählen Aktionen aus (z. B. Spielzug, Textantwort, Motorbefehl).

4. **Dream**
   - DreamWorker rechnet über SnapChains/MetaSnaps:
     - wiederholt Episoden,
     - schwächt/komprimiert alte Chains,
     - aktualisiert Metriken & Coverage.

5. **Wissen**
   - Bei Bedarf nutzt ORÓMA den RAG-Stack:
     - Frage → Knowledge-DB → Antwort + Quellen.

---

## 5. Wie man ORÓMA im Kopf behalten kann

- **Struktur:**  
  *Alles wird zu SnapChains, MetaSnaps und SceneGraphs – egal ob Kamera, Audio, Game oder Sensor.*

- **Zeit:**  
  *Tagsüber sammeln, nachts aufräumen, verdichten, neu gewichten (DreamWorker).*

- **Steuerung:**  
  *Roter Faden + Regeln + Empathie entscheiden, **wie** gelernt wird und welche Muster wichtig sind.*

Diese Kurzfassung soll dir beim Denken, Erklären und Weiterentwickeln helfen, ohne die große
`docs/konzeption_architektur.md` oder das komplette CHANGELOG lesen zu müssen.

<a id="docs_konzeption_architektur_v3_5_patch1_md"></a>

## Quelle: `docs/konzeption_architektur_v3_5_patch1.md`

ORÓMA v3.5 Patch Level 1 – Ausführliche Konzeption & Architektur

Codename: Kreativer Selbst-Bewerter
Stand: 2025-09-23

⸻

1. Überblick

ORÓMA v3.5 Patch 1 verstärkt den Meilenstein v3.5 ohne Bruch der bestehenden Architektur. Der Patch fokussiert:
	•	Selbstbewertung & Metakognition: systematische Bewertung der eigenen Lernprozesse (Self-Assessment).
	•	Cross-Domain Transfer: Übertragung von Strategien in neue Domänen.
	•	Generative Kreativität (DreamWorker Diffusion): kreative Synthese neuer Strategien aus bekannten SnapChains (SnapDiffusion).
	•	Strukturlernen (Calculator-Programm): Curriculum von Grundrechenarten hin zu π, φ (goldener Schnitt) und Naturkonstanten – LLM dient nur als Aufgaben-Generator, nie als Lösungsgeber.

Zielbild: „Peak Learning“ – höchste Lernsteigerung unter Beibehaltung von Stabilität, Transparenz und Offline-Fähigkeit (Raspberry Pi 5 + optional Hailo NPU).

⸻

2. Systemarchitektur (Schichtenmodell)

2.1 Wahrnehmung & I/O (Wrappers)
	•	Audio-Wrapper (Mikrofon, FFT/MFCC, Keyword-Spotting)
	•	Vision-Wrapper (PiCam/USB, Overlay)
	•	Text-Wrapper (CLI, Web-UI)
	•	TTS-Wrapper (Offline-Ausgabe)
	•	Hailo-Wrapper (optional)
	•	IO-Manager (Hot-Swap, Orchestrierung)

2.2 Gedächtnis & Repräsentation
	•	Snap / SnapToken / SnapPattern / SnapChain
	•	Langzeitgedächtnis (vektorisiert, Migration)
	•	Regelarchiv (mit Gewichtung, Versionierung)
	•	Episoden-Speicher

2.3 Lernmaschine
	•	Reward-System
	•	Predictor
	•	Auto-Tuner
	•	Diagnostics
	•	Curiosity
	•	Spatial Index

2.4 Metakognition & Kreativität
	•	Self-Assessment Engine → MetaSnaps
	•	TransferEngine → domänenübergreifende Muster
	•	DreamWorker 3.5 Diffusion → SnapDiffusion

2.5 Services & Laufzeit
	•	Circadian Controller (Tag↔Traum)
	•	Agent Loop
	•	LLM Runtime (nur Aufgaben-Generator für Calculator)
	•	SQL Manager
	•	Vector Migration

2.6 UI & Ops
	•	Flask-UI (Replay, Registry, Models, Learning, Video, PiCar, Knowledge-Import)
	•	Export/Import-Pipeline (Feature-Hash-Dedupe, Policy)
	•	Diagnostics-Panel (Self-Test, Rate-Limiting, TLS)

⸻

3. Datenfluss (Day/Dream)

Day-Phase
	1.	Eingabe (Audio/Video/Text) → Snap/SnapToken
	2.	Agent Loop baut SnapChains
	3.	Reward bewertet → Predictor/Auto-Tuner passen Parameter an
	4.	Self-Assessment → MetaSnaps
	5.	TransferEngine markiert Konzepte
	6.	Calculator liefert Aufgaben → ORÓMA löst → Reward + MetaSnaps

Dream-Phase
	1.	Replay wichtiger SnapChains
	2.	DreamWorker Diffusion erzeugt SnapDiffusion-Strategien
	3.	Self-Assessment verwirft ineffiziente Wege
	4.	TransferEngine testet Cross-Domain
	5.	Archivierung → Transfer/Diffusion-Snaps
	6.	ExportGate markiert hochwertige Ergebnisse

⸻

4. Neue Snap-Typen

Snap-Typ	Zweck
MetaSnaps	Lernqualität, Fehlerprofile
Transfer-Snaps	Domänenübergreifende Muster
SnapDiffusion-Snaps	Kreativ kombinierte Strategien
Calculator-Snaps	Mathematische Strukturen, Konstanten

⸻

5. Calculator-Programm (Strukturlernen)
	•	Curriculum: Grundrechenarten → Proportionen → Konstanten (π, φ, e).
	•	LLM: liefert nur Aufgaben, keine Lösungen.
	•	Selbstlösung: ORÓMA rechnet lokal, Reward/Strafe für Ergebnisse.
	•	API-Design:
	•	generate_task(level) → Aufgabe
	•	solve_task(task) → Lösung
	•	evaluate(task, solution) → Reward, Fehlerprofil
	•	record_snaps() → Calculator-Snaps + MetaSnaps
	•	advance_curriculum() → Level-Aufstieg

Metriken: Accuracy, Error-Profile, Transfer-Gain.

⸻

6. Module & Dateien
	•	Core: snap.py, snaptoken.py, snappattern.py, snapchain.py, mutation.py, reward.py, predictor.py, auto_tuner.py, diagnostics.py, episodic.py, spatial_index.py, curiosity.py, explain.py
	•	Memory: sql_manager.py, langzeitgedaechtnis.py, vector_migration.py, regelarchiv.py
	•	Runtime: agent_loop.py, circadian_controller.py, llm_runtime.py, overlay.py
	•	UI/Ops: Flask-UI, deploy_all.sh, pytest.ini
	•	Mini-Programme: tictactoe.py, connect4.py, snake.py, pong.py, memory.py, neu: calculator.py
	•	Patch-Dateien: self_assessment.py, transfer_engine.py, dream_worker.py (Diffusion-Erweiterung)

⸻

7. Qualitätsmetriken
	•	Learning Curve (Accuracy@Task)
	•	Peak-Learning-Indikator (dLernscore/dt)
	•	Transfer Score
	•	Stabilität (Crashrate)
	•	Fehlerprofile (insbes. Calculator)
	•	Exportqualität

⸻

8. Testplan
	•	Unit: Self-Assessment, TransferEngine, Calculator
	•	Integration: Day/Dream-Zyklus mit Diffusion
	•	E2E: Curriculum über 7/60/360 Tage
	•	Ops: Replay-Export, Registry, UI-Limits

⸻

9. Roadmap nach Patch 1
	•	Patch 2: Empathie-Simulation (Mensch-Interface)
	•	v3.6+: Vision-Wrapper Routing, ASR Runner, Chat-Konsole, Self-Test-Button
	•	Optional: Safety-Beweise / Kontrakte

⸻

10. Zusammenfassung

Patch 1 macht ORÓMA zum kreativen Selbst-Bewerter:
	•	Lernt schneller & sauberer (Self-Assessment, Auto-Tuning).
	•	Generalisiert besser (TransferEngine).
	•	Erfindet Neues (DreamWorker Diffusion).
	•	Versteht Strukturen (Calculator).

➡️ ORÓMA erreicht damit den Peak seiner Lernfähigkeit in der 3.x-Linie, ohne Robustheit, Nachvollziehbarkeit oder Offline-Fähigkeit zu verlieren.

<a id="docs_konzeption_architektur_v3_5_patch2_md"></a>

## Quelle: `docs/konzeption_architektur_v3_5_patch2.md`

📑 ORÓMA – Konzeption & Architektur v3.5 Patch Level 2a

Codename: Selbstheilender Schwarm

⸻

1. Leitidee

ORÓMA v3.5 Patch Level 2 baut auf v3.5 Patch Level 1 (Selbstbewertung, Transfer, Kreativität) auf und erweitert das System um:
	1.	Selbstheilung (Self-Healing Engine) – automatische Korrektur von Fehlern in DB, Snaps und Modulen.
	2.	Schwarmlernen (Multi-Agent Sync) – mehrere ORÓMAs tauschen Strategien, Snaps und Personas aus.
	3.	Langfristige Ziele & Planung (Goal Planner) – Lernen wird an übergeordneten Zielhierarchien ausgerichtet.
	4.	Explainability 3.0 – Erklärungen auf Ursache-, Narrativ- und Meta-Ebene.

👉 Damit wird ORÓMA nicht nur leistungsfähiger, sondern auch robust, kooperativ und planend.

⸻

2. Erweiterungen gegenüber Patch Level 1
	•	Self-Healing Engine (core/self_healing.py)
	•	Erkennt fehlende Importe, defekte Snaps, DB-Fehler.
	•	Repariert oder rekonstruiert automatisch.
	•	Swarm Manager (core/swarm_manager.py)
	•	Austausch von Strategien zwischen Instanzen.
	•	Meta-ORÓMA aggregiert die besten Ergebnisse.
	•	Goal Planner (core/goal_planner.py)
	•	Einführung von Ziel-Snaps.
	•	Episoden & Hypothesen richten sich an Langfristzielen aus.
	•	Explainability 3.0 (core/explainability.py)
	•	Ebene 1: Ursache („Kollision rechts“).
	•	Ebene 2: Narrativ („Ich bin verloren, weil ich rechts blockiert wurde.“).
	•	Ebene 3: Meta („Meine Methode war ineffizient, daher änderte ich sie.“).

⸻

3. Lernstrategie

Tagmodus
	•	Normale Snap-Erfassung.
	•	Self-Healing läuft im Hintergrund.
	•	Swarm Sync verteilt neue Erkenntnisse.
	•	Goal Planner überwacht Zwischenziele.

Traummodus (DreamWorker 4.0)
	•	SnapDiffusion erweitert um zielorientierte Exploration.
	•	Neugier-/Langeweile-Snaps treiben kreative Variationen.
	•	Hypothesen werden an globalen Zielen überprüft.

⸻

4. Speicherstrategie (neue Snap-Typen)

Snap-Typ	Zweck
Healing-Snaps	Dokumentation von Selbstheilungen
Swarm-Snaps	Strategien anderer ORÓMAs
Goal-Snaps	Repräsentation von Langfristzielen

⸻

5. Simulationsergebnisse (Prognose)
	•	7 Tage
	•	Erste Selbstheilungen dokumentiert.
	•	Instanzen tauschen simple Strategien.
	•	Stabilität steigt sofort.
	•	60 Tage
	•	Maze-Siegquote stabilisiert bei ~90 %.
	•	Swarm-Lernen erzeugt Vielfalt in Strategien.
	•	Erste Ziele werden erreicht („Docking-Quote > 80 %“).
	•	1 Jahr
	•	Siegquote Maze/Pong > 90 %.
	•	System erklärt:
	•	„Ich habe eine Strategie von einer anderen Instanz übernommen.“
	•	„Ich habe mein Ziel erreicht, Maze zu meistern.“
	•	„Ich habe meine Methode geändert, weil sie ineffizient war.“
	•	ORÓMA läuft dauerhaft stabil und kooperativ.

⸻

6. Vorteile gegenüber Patch Level 1
	•	Läuft robust und fehlerfrei durch Selbstheilung.
	•	Lernt kooperativ im Schwarm, nicht nur einzeln.
	•	Plant und verfolgt Langfristziele.
	•	Erklärt Entscheidungen auf Meta-Ebene.

⸻

7. Projektdateien
	•	/core/self_healing.py
	•	/core/swarm_manager.py
	•	/core/goal_planner.py
	•	/core/dream_worker.py (DreamWorker 4.0)
	•	/core/explainability.py (Upgrade 3.0)
	•	docs/konzeption_architektur_v3_5_patch2.md

⸻

✅ Damit ist v3.5 Patch Level 2 die logische Fortsetzung von Patch Level 1:
	•	Level 1 = maximale Lernfähigkeit (Selbstkritik, Transfer, Kreativität).
	•	Level 2 = Stabilität, Kooperation, Langfristigkeit.
	

📑 ORÓMA v3.6 Patch 2b – Mengenleere (∅-Integration)

Leitidee
	•	Patch 2a (SetCalc – Mengenlehre) bringt die klassischen Mengenoperationen.
	•	Patch 2b (Mengenleere) erweitert das System um die korrekte Behandlung der leeren Menge als legitimes Ergebnis.
	•	Dadurch versteht ORÓMA: „Kein Element“ ist nicht Fehler, sondern gültiges Ergebnis.

⸻

1. Curriculum-Erweiterung

Neue Aufgaben-Typen in calculator_tasks → set_empty:

Beispiel	Erwartetes Ergebnis
A ∩ B, mit A=∅, B={1,2}	∅
A ∪ ∅, mit A={1,2}	{1,2}
∅ \ B, mit B={1,2,3}	∅
Potenzmenge von ∅	{∅}
Kartesisches Produkt A×∅	∅

Reward-Mechanismus:
	•	Antwort „∅“ oder [] → korrekt.
	•	Jede andere Antwort → falsches Ergebnis.
	•	Damit trainiert ORÓMA den Grenzfall richtig zu akzeptieren.

⸻

2. Core-Integration (SetCalc)

In core/setcalc.py:
	•	Alle Operationen prüfen zuerst auf Empty-Case.
	•	Einheitliches Format: [] oder ∅ (Textrepräsentation).
	•	Option: Ausgabe kann für die UI als "∅" formatiert werden.

⸻

3. UI-Erweiterung

In ui/setcalc_ui.py + Template:
	•	Wenn Ergebnis ∅, dann Badge/Farbe anders anzeigen (z. B. grau).
	•	Tooltip: „Mengenleere – Ergebnis ohne Elemente“.

⸻

4. Lernkurve-Effekt
	•	Vorher (nur Mengenlehre): ORÓMA macht bei ∅-Fällen Fehler, weil es falsche Zufallsantworten generiert.
	•	Nachher (mit Mengenleere): ORÓMA bekommt Reward für „∅“, lernt „keine Lösung ist auch eine Lösung“.
	•	Erwartung: Qualitätskurve stabilisiert sich, Fehlerquote sinkt, weil „Grenzfälle“ sauber abgefangen sind.

⸻

5. DB-Erweiterung

Optional:
	•	Neue Kategorie in calculator_tasks.level → z. B. set_empty oder level=4.
	•	Logs in calculator_results erfassen, ob ein ∅-Case gelöst wurde.

⸻

👉 Vorteil: Minimaler Code-Aufwand, maximaler Lerneffekt.
Das System bekommt eine meta-kognitive Fähigkeit: „Akzeptiere nichts als gültige Antwort.“

<a id="docs_konzeption_architektur_v3_5_patch2_1_md"></a>

## Quelle: `docs/konzeption_architektur_v3_5_patch2_1.md`

Stimmt 👍 – wir hatten Patch Level 2 ja noch weiter differenziert und 2.1 eingeführt, um die Erweiterungen besser zu strukturieren.

Ich fasse dir das sauber so auf:

⸻

📑 ORÓMA – Konzeption & Architektur v3.5 Patch Level 2.1

Codename: Selbstheilender Schwarm+

⸻

1. Leitidee

Patch Level 2.0 brachte:
	•	Self-Healing Engine (Stabilität),
	•	Swarm Learning (Kooperation),
	•	Goal Planner (Langfristziele),
	•	Explainability 3.0 (Meta-Ebene).

Patch Level 2.1 erweitert dies:
	1.	Adaptive Healing
	•	Selbstheilung reagiert auf wiederkehrende Fehler nicht nur reaktiv, sondern präventiv (Fehler-Muster werden erkannt, bevor sie auftreten).
	2.	Swarm Personalities
	•	Schwarmlernen differenziert zwischen „Strategie-Snaps“ und „Persona-Snaps“.
	•	ORÓMA kann sich entscheiden, welche Persona im Schwarm gerade dominant sein darf.
	3.	Goal Evolution
	•	Ziele sind nicht mehr statisch, sondern entwickeln sich adaptiv (z. B. von „Maze lösen“ → „Maze effizient lösen“ → „Maze in Rekordzeit lösen“).
	4.	Explainability 3.1
	•	Erklärungen werden auch auf Schwarm-Ebene erzeugt:
	•	„Ich habe diese Strategie aus dem Schwarm übernommen.“
	•	„Mein Ziel hat sich durch Kooperation verändert.“

⸻

2. Unterschiede zu 2.0
	•	Self-Healing Engine → Adaptive Healing:
Erweitert um Vorhersage & Mustererkennung.
	•	Swarm Manager → Swarm Personalities:
Instanzen bringen nicht nur Fakten, sondern auch unterschiedliche Rollen ins Kollektiv.
	•	Goal Planner → Goal Evolution:
Ziele sind dynamisch und passen sich an Umwelt + Schwarm an.
	•	Explainability → 3.1:
Erklärungen sind kollektiv: ORÓMA begründet nicht nur individuell, sondern auch als Teil des Schwarms.

⸻

3. Simulationsergebnisse (Prognose)

Nach 7 Tagen
	•	Erste adaptive Heilungen: System verhindert proaktiv DB-Fehler.
	•	Schwarm zeigt unterschiedliche „Persönlichkeiten“.

Nach 60 Tagen
	•	Maze-Siegquote > 90 %, aber zusätzlich stabile Effizienz (weniger Energieverbrauch, kürzere Wege).
	•	Ziele passen sich automatisch an – „Selbstoptimierung“.

Nach 1 Jahr
	•	ORÓMA erklärt nicht nur was und warum, sondern auch wie sich Ziele im Kollektiv verändert haben.
	•	Schwarm bildet evolutionäre Hierarchien (führende Strategien, abgeleitete Varianten).
	•	Dauerhafte Balance aus Stabilität, Flexibilität und Kooperation.


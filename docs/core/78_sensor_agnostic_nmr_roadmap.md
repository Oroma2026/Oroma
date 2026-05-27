# ORÓMA – Sensor-unabhängige NMR-Roadmap v2.1

**Datei / Path:** `docs/core/78_sensor_agnostic_nmr_roadmap.md`  
**Projekt / Project:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Kurzbeschreibung / Short description:** An offline-first adaptive edge intelligence architecture  
**Version:** v2.1 – Snap-/SnapChain-kompatible Präzisierung nach Core-Gesamtprüfung  
**Datum / Date:** 2026-05-26  
**Autor / Author:** ORÓMA-Projekt / Jörg Werner, redaktionell ausgearbeitet mit ChatGPT  
**Baseline:** `oroma_20260526_220322_with_db.zip`  
**Status:** Strategische Roadmap mit konkreter Architekturpräzisierung; keine direkte Implementierungsanweisung  
**Bezug:** NMR-Lite Phase A/A.5 ist live validiert; diese Roadmap beschreibt die nächste Architekturstufe ohne Parallelaufbau zu bestehenden Core-Strukturen.

---

## 0. Ergebnis der Core-Gesamtprüfung

Vor dieser Aktualisierung wurde der komplette `core/`-Ordner der Baseline geprüft.

Geprüfter Umfang:

```text
Baseline: /mnt/data/oroma_20260526_220322_with_db.zip
Core-Pfad: core/
Python-Dateien direkt unter core/: 107
```

Wichtiger Befund:

> ORÓMA besitzt die universellen Gedächtnis- und Verdichtungsbausteine bereits. NMR Phase 2 soll diese Bausteine nutzen, nicht neu erfinden.

Bereits vorhandene Kernbausteine:

```text
Snap              → atomare Moment-/Beobachtungseinheit
SnapToken         → leichte Token-/Signalbrücke
SnapPattern       → frühe Verdichtung / Patternlet-nahe Struktur
SnapChain         → episodische Sequenz / Kontextkette
FusionPack        → optionaler Crossmodal-Container
SensorChannel     → generische Sensorabstraktion
SceneGraph        → szenische Struktur
ObjectGraph       → Objekt-/Relationssubstrat
object_relations  → Binding-/Synapsen-Substrat
Replay            → Wiederholung / Priorisierung / Bonuspfad
DreamWorker       → Konsolidierung / Verdichtung / Exportfähigkeit
NMR-Lite          → Live Prediction Error / EMA / Priority / Hint Layer
```

Daraus folgt:

```text
Observation Atom  = NMR-semantische Sicht auf einen Snap oder snap-kompatiblen Input
Patternlet        = frühe/kleine SnapPattern-Instanz
Episode           = SnapChain / episodischer Kontext
Binding Graph     = object_nodes / object_relations
Concept           = MetaSnap / ObjectGraph / SceneGraph / verdichtete SnapPattern-Semantik
Consolidation     = DreamWorker / Replay / NMR-Synapsen
```

Diese Roadmap wurde deshalb in v2.1 bewusst korrigiert: **Phase 2 baut kein neues Observation-Atom-Persistenzsystem. Phase 2 definiert eine Snap-kompatible NMR-Sicht auf bereits vorhandene Datenstrukturen.**

---

## 1. Zweck dieses Dokuments

Dieses Dokument beschreibt die nächste Entwicklungsstufe von NMR in ORÓMA.

Der bisherige Stand **NMR-Lite** ist bewusst leichtgewichtig, Raspberry-Pi-tauglich und produktiv beobachtbar. Er verarbeitet konkrete Signalpfade wie Vision-Fingerprints, Audio-Merkmale, Curriculum-Zustand, Empathie-Zustand und Runtime-Signale. Daraus entstehen Prediction Error, EMA, Priority und frühe Hint-Signale.

Diese Roadmap geht einen Schritt weiter:

> Ziel ist ein sensor-unabhängiges NMR-System, das wiederkehrende Strukturen über beliebige Eingabekanäle erkennt, bindet und hierarchisch verdichtet – aber auf Basis der vorhandenen ORÓMA-Gedächtnisarchitektur.

Der Kern ist nicht mehr nur:

```text
Vision ändert sich → PE steigt
Audio ändert sich  → PE steigt
```

sondern:

```text
Ein Snap-kompatibles Muster entsteht.
Das Muster wiederholt sich.
Das Muster hat Kontext in SnapChains.
Das Muster bindet sich über object_relations an andere Muster.
Das Muster wird über SnapPattern / MetaSnap / Dream verdichtet.
Aus verdichteten Mustern entstehen Erwartungen.
```

---

## 2. Ausgangspunkt: Was NMR-Lite bereits bewiesen hat

NMR-Lite Phase A/A.5 ist im Live-System validiert.

Der praktisch bestätigte Pfad lautet:

```text
Sensor-/Systemsignale
→ NMR-Lite Observation State
→ nmr_pe / nmr_pe_ema / priority / binding_hint_score
→ DBWriter-kompatible Metrikpersistenz
→ metrics-Tabelle
→ /control/api/status
```

Live nachgewiesene Eigenschaften:

- AgentLoop läuft stabil.
- `_nmr_lite_hook` wird registriert und ausgeführt.
- `nmr_lite.tick()` erzeugt Output.
- `nmr_lite.maybe_persist()` schreibt über den produktiven DBWriter-kompatiblen Pfad.
- `/control/api/status` zeigt `nmr_lite` sichtbar an.
- Vision-Fingerprints erreichen NMR-Lite.
- `nmr_pe` und `nmr_pe_ema` bewegen sich bei visueller Veränderung.
- `confidence` und `priority` reagieren auf aktive Modalitäten.

Beispielhaft validierte Live-Werte:

```text
vision_enabled: true
vision_degraded: false
vision_fp12: [12 numerische Werte]
vision_scene_change: 0.0113
snap_counter: 2
nmr_pe_ema: 0.000305
nmr_priority_score: 0.202996
binding_hint_score: 0.203546
```

Damit ist NMR-Lite als **produktiver Mismatch-/Priority-Layer** bewiesen.

---

## 3. Kritische Einordnung: Was NMR-Lite noch nicht ist

NMR-Lite ist noch kein echtes sensor-unabhängiges Vernetzungssystem.

Aktuelle Grenzen:

1. **Modalitätsspezifische Eingänge**  
   Der aktuelle Code kennt konkrete Felder wie `vision_fp12`, `audio_rms`, `audio_pitch`, `curriculum_acc`, `empathy_valence`.

2. **Explizite Feature-Bündelung**  
   Signale werden in einem bekannten, festen Observation State zusammengeführt.

3. **Frühe Hint-Logik statt emergenter Bindung**  
   `binding_hint`, `binding_hint_score` und `crossmodal_hint` sind erste strukturelle Hinweise, aber noch kein allgemeines Binding-System.

4. **Noch keine generische Verdichtung über alle bestehenden Snap-Strukturen**  
   Snap, SnapPattern, SnapChain, ObjectGraph und Dream existieren bereits. Die fehlende Ebene ist die NMR-Orchestrierung darüber.

5. **Kein universelles Modalitätsmodell im NMR-Layer**  
   Ein neuer Sensor muss heute noch explizit an NMR-Lite angeschlossen werden.

Das ist kein Fehler. Es ist ein bewusst sicherer Phase-1-Stand.

NMR-Lite ist daher korrekt einzuordnen als:

```text
Lightweight Mismatch / Surprise / Priority Layer
```

Die Zielarchitektur ist:

```text
Snap-compatible sensor-agnostic hierarchical pattern binding and compression layer
```

---

## 4. Architekturkorrektur v2.1: Nicht doppelt bauen

Die frühere Formulierung „Observation Atoms“ kann missverstanden werden, als solle ORÓMA neben Snap/SnapChain ein neues paralleles Datenmodell erhalten.

Das wäre falsch.

Richtig ist:

```text
Observation Atom = NMR-semantische Lesart eines bestehenden Snaps oder snap-kompatiblen Inputs.
```

NMR Phase 2 soll deshalb keine neue Grundpersistenz einführen, sondern vorhandene Strukturen konsequent verwenden:

```text
Sensor / Hook / Adapter
→ snap-kompatibler Input
→ Snap
→ SnapPattern
→ SnapChain
→ object_nodes / object_relations
→ Dream / Replay / Compression
```

### 4.1 Mapping der Begriffe auf vorhandene ORÓMA-Bausteine

| Roadmap-Begriff | Vorhandener ORÓMA-Baustein | Bedeutung |
|---|---|---|
| Observation Atom | `core.snap.Snap` oder snap-kompatibles Dict | kleinste normalisierte Beobachtung |
| Patternlet | `core.snappattern.SnapPattern` | frühe wiedererkennbare Mikrostruktur |
| Episode | `core.snapchain.SnapChain` | zeitlicher / episodischer Kontext |
| Fusion Input | `core.fusion.FusionPack` / `ModalityVec` | optionale Crossmodal-Bündelung |
| Sensor Adapter | `core.sensor_channel.BaseSensorChannel` | generischer Sensoreingang |
| Binding Graph | `object_nodes` / `object_relations` | Relation, Synapse, Kontextkante |
| Concept | MetaSnap / ObjectGraph / SceneGraph | verdichtete Struktur / Bedeutungseinheit |
| Consolidation | `core.dream_worker`, `core.replay_manager` | Dream-/Replay-Verstärkung |

### 4.2 Was nicht neu gebaut werden soll

Nicht neu bauen, solange bestehende Strukturen reichen:

```text
ObservationAtom-Klasse als neue Persistenzbasis
Patternlet-Tabelle neben SnapPattern
BindingGraph-Tabelle neben object_relations
neues Episodenformat neben SnapChain
neuer Sensor-Adapter-Stack neben SensorChannel
neuer Compression-Store neben MetaSnap/ObjectGraph/SceneGraph
neuer Crossmodal-Link-Store neben vorhandenen Relations-/Linker-Pfaden
```

Stattdessen:

```text
NMR liest Snaps sensor-unabhängig.
NMR erkennt Wiederholung über SnapPattern-nahe Logik.
NMR schlägt Bindungen über object_relations vor.
NMR priorisiert Replay.
Dream verdichtet stabile Strukturen.
```

---

## 5. Leitbild: Was ein echtes NMR leisten soll

Ein echtes NMR in ORÓMA soll beliebige Quellen als Strukturträger verstehen.

Mögliche Quellen:

- Kamera / Vision
- Audio
- PTZ-Bewegung
- IR / Entfernung / Tiefe
- Text / Sprache / ASR
- Curriculum-Aufgaben
- Spielzustände
- interne Zustände
- Fehlerzustände
- Replay-Ergebnisse
- Dream-Konsolidierung
- Motorik / Aktorik
- UI- und Nutzerinteraktionen

Das System soll dabei nicht primär fragen:

```text
Ist das Vision oder Audio?
```

sondern:

```text
Welche Snap-kompatible Struktur liegt vor?
Wie stabil ist sie?
Wie oft wiederholt sie sich?
In welcher SnapChain / welchem Kontext tritt sie auf?
Mit welchen anderen Strukturen tritt sie gemeinsam auf?
Kann sie als SnapPattern verdichtet werden?
Kann sie über object_relations gebunden werden?
Erzeugt sie Erwartung?
```

---

## 6. Grundprinzip: Unendliche Verdichtung als hierarchische Kompression

Der Begriff „unendlich verdichten“ bedeutet in dieser Roadmap nicht grenzenloses Speichern. Gemeint ist:

> ORÓMA soll Strukturen beliebig tief hierarchisch verdichten können, solange aus Wiederholung, Stabilität und Kontext neue sinnvolle Abstraktion entsteht.

Das Rohsignal ist zu komplex und kann hochdimensional sein. Daher muss ORÓMA nicht alles dauerhaft roh behalten, sondern schrittweise komprimierbare Struktur extrahieren.

### 6.1 Beispiel Vision

```text
Rohbild / Pixel
→ Helligkeitsgradient
→ Kante
→ wiederkehrende Kante
→ Kontur
→ Fläche
→ Füllmenge
→ Objektgröße
→ 3D-Lage
→ Objektzustand
→ Szenenstruktur
→ Handlungskontext
→ Erwartung
```

ORÓMA-konformes Mapping:

```text
Vision-Fingerprint / CamToken
→ Snap
→ SnapPattern-Kandidat
→ SnapChain-Kontext
→ object_relation zu Objekt-/Szenenstruktur
→ Dream-Konsolidierung
→ Concept / Erwartung
```

### 6.2 Beispiel Audio

```text
Amplitude / Frequenz
→ Ton
→ zwei Töne
→ Tonabfolge
→ Rhythmus
→ wiederkehrendes Klangmuster
→ Ereignis-Typ
→ Kontextbindung
→ Erwartung
```

ORÓMA-konformes Mapping:

```text
Audio-Merkmal / AudioToken
→ Snap
→ SnapPattern-Kandidat
→ SnapChain-Kontext
→ Relation zu Vision/PTZ/Curriculum-Kontext
→ Dream-Konsolidierung
```

### 6.3 Beispiel PTZ

```text
Pan/Tilt-Wert
→ Bewegung
→ Fixation
→ wiederkehrende Zielzone
→ Coverage-Gap
→ interessanter Raumsektor
→ aktive Aufmerksamkeit
```

ORÓMA-konformes Mapping:

```text
PTZ-State / Fixation
→ Snap
→ SnapChain mit Raum-/Zeitkontext
→ Attention-Anchor Relation
→ Replay-/Dream-Bewertung
```

### 6.4 Beispiel Curriculum

```text
Einzelaufgabe
→ Fehler
→ wiederholter Fehler
→ Fehlertyp
→ Schwierigkeitsmuster
→ Lernlücke
→ Wiederholungsbedarf
→ Trainingsstrategie
```

ORÓMA-konformes Mapping:

```text
Task Result
→ Snap
→ SnapChain Lernfolge
→ SnapPattern Fehlertyp
→ Relation zu Replay-Erfolg
→ Curriculum-Strategie
```

---

## 7. Zielarchitektur: Snap-kompatibler NMR Observation Layer

Die nächste NMR-Stufe sollte nicht ein neues Persistenzformat einführen, sondern alle Eingänge in eine **Snap-kompatible NMR-Sicht** bringen:

```text
Sensor Adapter / Hook
→ Snap-kompatible Observation Payload
→ Snap / SnapChain / SnapPattern
→ NMR-Messung
→ Dream-Konsolidierung
```

### 7.1 Snap-kompatibles minimales Datenmodell

Eine NMR-Observation sollte als Snap oder snap-kompatibles Dict darstellbar sein.

Beispiel:

```json
{
  "features": [0.148, 0.096, 0.090, 0.064],
  "content": {
    "kind": "vision.fp12",
    "source": "vision",
    "strength": 0.82
  },
  "metadata": {
    "nmr_observation": true,
    "nmr_phase": "DAY",
    "source": "vision",
    "kind": "fp12",
    "quality": 0.81,
    "confidence": 1.0,
    "degraded": false,
    "episode_id": 12345,
    "ptz_pan": null,
    "ptz_tilt": null
  }
}
```

Wichtig:

- `features` bleibt numerischer Vergleichsanker.
- `content` trägt semantische Payload.
- `metadata` trägt NMR-Kontext und Governance.
- Persistenz erfolgt über vorhandene Snap-/SnapChain-/DBWriter-Pfade.
- Keine neue Grundtabelle nur für Observation Atoms.

### 7.2 Anforderungen

Snap-kompatible NMR-Observations müssen:

- klein sein
- billig berechenbar sein
- fehlende Felder tolerieren
- Quelle und Art nennen
- numerischen Vergleichsanker haben
- optional Raum, Zeit, Qualität und Kontext enthalten
- DBWriter-kompatibel materialisierbar sein
- im Day-Modus schnell erzeugbar sein
- im Dream-Modus verdichtbar sein
- als Snap serialisierbar sein
- in SnapChains kontextualisierbar sein

### 7.3 Beispiele

Vision:

```json
{
  "features": [0.148, 0.096, 0.090],
  "content": {"source": "vision", "kind": "fp12"},
  "metadata": {"nmr_observation": true, "quality": 0.81, "confidence": 1.0}
}
```

Audio:

```json
{
  "features": [3292.84, 0.000079],
  "content": {"source": "audio", "kind": "tone_candidate"},
  "metadata": {"nmr_observation": true, "quality": 0.22, "confidence": 0.35}
}
```

PTZ:

```json
{
  "features": [0.25, -0.12, 1.0],
  "content": {"source": "ptz", "kind": "fixation"},
  "metadata": {"nmr_observation": true, "quality": 0.90, "confidence": 0.88}
}
```

Curriculum:

```json
{
  "features": [2.0, 0.0, 0.71],
  "content": {"source": "curriculum", "kind": "task_error"},
  "metadata": {"nmr_observation": true, "quality": 1.0, "confidence": 1.0}
}
```

---

## 8. Patternlet = SnapPattern-nahe Mikrostruktur

Ein **Patternlet** ist in ORÓMA v2.1 kein neues Grundobjekt, sondern eine frühe oder kleine SnapPattern-nahe Struktur.

Patternlets sind noch keine vollständigen Konzepte. Sie sind frühe stabile Muster.

### 8.1 Beispiele

Vision:

```text
wiederkehrender fp12-Cluster
wiederkehrende Kante im linken Bildbereich
wiederkehrende Kontur
stabile Objektfläche
Füllstandskante
```

Audio:

```text
gleicher Toncluster
kurze Tonfolge
wiederkehrender Rhythmus
Geräusch vor Bewegung
```

PTZ:

```text
wiederkehrende Fixation auf Pan/Tilt-Zone
wiederkehrender Attention-Shift
```

Curriculum:

```text
wiederholter Fehler bei Division
wiederholte Korrektur nach Replay
```

### 8.2 Patternlet-Kriterien

Ein Patternlet sollte entstehen, wenn mehrere Kriterien erfüllt sind:

- Ähnlichkeit oberhalb Schwelle
- Wiederholung innerhalb Zeitfenster
- ausreichende Qualität
- ausreichende Stabilität
- begrenzte Neuheit
- keine Massenerzeugung durch Rauschen
- als SnapPattern oder SnapPattern-Metadaten ausdrückbar

### 8.3 Patternlet-Metriken

Mögliche Metriken:

```text
nmr.patternlet.candidates
nmr.patternlet.accepted
nmr.patternlet.repeat_ge_2
nmr.patternlet.repeat_ge_3
nmr.patternlet.quality_avg
nmr.patternlet.novelty_avg
nmr.patternlet.source.<source>.count
```

---

## 9. Binding = object_relations / synaptische Relation

Ein **Binding** verbindet Patternlets, Snaps, SnapPatterns oder Concepts über Zeit, Raum, Kontext oder Kausalnähe.

Wichtig: Binding darf nicht primär als harte Regel entstehen.

Nicht ideal:

```text
if vision and audio then crossmodal_hint
```

Besser:

```text
Wenn zwei Snap-/Pattern-Strukturen wiederholt zeitlich oder kontextuell gemeinsam auftreten,
erhöhe ihre Bindungswahrscheinlichkeit.
```

### 9.1 Binding-Arten

Mögliche Binding-Typen:

```text
temporal        Muster A tritt kurz vor Muster B auf
co_occurrence   Muster A und B treten gemeinsam auf
spatial         Muster A und B liegen räumlich nah
causal_hint     A erhöht Wahrscheinlichkeit von B
contrast        A und B schließen sich häufig aus
sequence        A → B → C wiederholt sich
state_change    A markiert Veränderung von Zustand B
attention       A zieht PTZ/Fokus wiederholt an
synaptic        generische NMR-Synapse / Hebb-artige Bindung
```

### 9.2 Binding-Beispiele

```text
Vision-Kante + Objektfläche
Audio-Tonfolge + Vision-Bewegung
PTZ-Fixation + Scene-Change
Curriculum-Fehler + späterer Replay-Erfolg
Füllstandskante + Objektzustand
```

### 9.3 Binding-Gates

Bindings müssen streng gegated werden:

- Mindestwiederholung
- Mindestqualität
- begrenzte Tagesmenge
- Dedupe über `(a_id, relation, b_id)`
- keine Rohsignal-Massenkanten
- DBWriter-only Writes
- Dream-first Materialisierung
- UI-/Status-Sichtbarkeit

### 9.4 Vorhandene Basis

Die vorhandene Basis ist:

```text
object_nodes
object_relations
relation='synaptic'
nmr_synaptic_plasticity.py
objectgraph_builder.py
scenegraph_store.py
```

Phase 2–5 soll diese Basis erweitern, nicht ersetzen.

---

## 10. Compression Ladder auf ORÓMA-Bausteinen

Die **Compression Ladder** ist die zentrale Verdichtungstreppe von NMR.

v2.1 nutzt bewusst die vorhandenen ORÓMA-Bausteine:

```text
Raw / Kurzzeitpuffer
→ Snap-kompatible Observation
→ Snap
→ SnapPattern / Patternlet
→ SnapChain-Kontext
→ object_relation / Binding
→ MetaSnap / ObjectGraph / SceneGraph Concept
→ Expectation
```

### 10.1 Stufe 0 – Raw / Kurzzeitpuffer

Rohsignale bleiben nur kurz verfügbar.

Zweck:

- lokale Analyse
- schnelle PE-Berechnung
- kurze Vergleichsfenster
- keine langfristige DB-Massenlast

### 10.2 Stufe 1 – Snap-kompatible Observation

Kleine normalisierte Beobachtung, als Snap oder snap-kompatibles Dict darstellbar.

Zweck:

- sensor-unabhängiger Vergleich
- Basis für Wiederholung
- Basis für Patternlet-Erkennung
- bestehende Snap-Persistenz nutzbar halten

### 10.3 Stufe 2 – Snap / SnapToken

Persistierbare oder tokenisierte Momentaufnahme.

Zweck:

- Fingerprint / Dedup
- Feature-Vergleich
- Content-/Metadata-Kontext
- Brücke zu SnapChain und Replay

### 10.4 Stufe 3 – SnapPattern / Patternlet

Wiederkehrende Mikrostruktur.

Zweck:

- Rauschen reduzieren
- stabile Anker finden
- Wiederholung messbar machen

### 10.5 Stufe 4 – SnapChain-Kontext

Episodische Reihenfolge und Kontext.

Zweck:

- Sequenzen erkennen
- Replayfähig machen
- Timing/Space/Explain-Spuren tragen

### 10.6 Stufe 5 – Binding / object_relation

Strukturbeziehung zwischen Patternlets/Snaps/Concepts.

Zweck:

- Kontext erzeugen
- Modalitäten verbinden
- Kausalhinweise sammeln
- Dream-Synapsen materialisieren

### 10.7 Stufe 6 – Concept / MetaSnap / ObjectGraph

Verdichtete Bedeutungseinheit.

Zweck:

- wiederverwendbares Objekt / Ereignis / Zustand
- kompakter Langzeitspeicher
- Grundlage für Entscheidungen

### 10.8 Stufe 7 – Expectation

Vorhersage / Erwartung.

Zweck:

- PE sinnvoll interpretieren
- Aufmerksamkeit steuern
- Replay priorisieren
- Handlungen vorbereiten

---

## 11. Day/Dream-Rollen

NMR muss in ORÓMA zur bestehenden Day/Dream-Architektur passen.

### 11.1 Day

Day ist nicht für schwere Verdichtung zuständig.

Day-Aufgaben:

- Signale aufnehmen
- Snap-kompatible Observations erzeugen
- PE/EMA berechnen
- leichte Patternlet-Kandidaten zählen
- aktuelle Priority erzeugen
- Status/API/Metriken schreiben
- keine teure Massengraph-Erzeugung
- höchstens kleine Ringbuffer oder SnapChain-Anker pflegen

### 11.2 Dream

Dream ist die eigentliche Konsolidierungsphase.

Dream-Aufgaben:

- Wiederholungen prüfen
- Kandidaten sortieren
- SnapPatterns / Patternlets materialisieren
- Bindings über object_relations materialisieren
- Concepts / MetaSnaps / ObjectGraph-Strukturen verdichten
- schwache Kandidaten verwerfen
- export-/archivfähige Strukturen markieren
- Replay-Ergebnisse zurückspielen

### 11.3 Warum Dream-first wichtig ist

Eine sensor-unabhängige NMR-Schicht kann sehr schnell zu viele Kandidaten erzeugen. Deshalb darf Materialisierung nicht blind im Live-Tick passieren.

Regel:

```text
Day beobachtet und markiert.
Dream entscheidet und verdichtet.
```

---

## 12. Roadmap-Phasen

## Phase 1 – NMR-Lite Live Mismatch Layer

**Status:** erreicht / live validiert

Ziel:

- Signale in NMR-Lite einspeisen
- PE/EMA berechnen
- Priority erzeugen
- Status-API sichtbar machen
- DBWriter-kompatibel persistieren

Erreicht:

```text
AgentLoop → NMR tick → PE/EMA/Priority → DBWriter metrics → Status API
```

Noch offen innerhalb Phase 1:

- Audio sauber zwischen Nutzsignal, Grundrauschen und fehlendem Device unterscheiden
- Replay-Bonuswirkung praktisch nachweisen
- Hint-Schwellen über längeren Zeitraum beobachten

---

## Phase 2 – Snap-kompatibler NMR Observation Layer

**Status:** nächster Architekturblock

Ziel:

Alle Sensoren liefern eine einheitliche, Snap-kompatible NMR-Sicht.

Nicht-Ziel:

```text
Keine neue ObservationAtom-Persistenzklasse.
Keine neue Grundtabelle.
Keine parallele Gedächtnisstruktur neben Snap/SnapChain.
```

Aufgaben:

1. NMR-Observation-Konvention für Snap-Metadata definieren.
2. Bestehende Snap-/SnapChain-Pfade als Materialisierungsziel nutzen.
3. Adapter für Vision, Audio, PTZ, Curriculum, Runtime als Snap-kompatible Payloads standardisieren.
4. Einheitliche Qualitäts-/Confidence-Felder in `metadata` definieren.
5. Keine neuen Rohdaten-Massentabellen erzeugen.
6. Zuerst nur Status/Metriken und kleine SnapChain-Anker verwenden.
7. Prüfen, wo `SensorChannel.build_snap_payload()` als Standardadapter genutzt werden kann.

Akzeptanzkriterien:

```text
vision → Snap-kompatible NMR Observation ✓
audio → Snap-kompatible NMR Observation ✓
ptz → Snap-kompatible NMR Observation ✓
curriculum → Snap-kompatible NMR Observation ✓
runtime → Snap-kompatible NMR Observation ✓
```

Metriken:

```text
nmr.obs.snap_compatible.total
nmr.obs.source.vision
nmr.obs.source.audio
nmr.obs.source.ptz
nmr.obs.quality_avg
nmr.obs.degraded_count
nmr.obs.snapchain_anchors
```

---

## Phase 3 – Patternlet Discovery über SnapPattern

**Status:** nach Phase 2

Ziel:

Wiederkehrende Mikrostrukturen erkennen, ohne sofort harte Graph-Kanten zu erzeugen.

Aufgaben:

1. Coarse Fingerprints für Snap-kompatible Observations bilden.
2. Wiederholung pro Quelle messen.
3. Wiederholung über Quellen messen.
4. Kandidatenqualität bewerten.
5. Noise-Gates einbauen.
6. Top-K Kandidaten pro Zeitfenster begrenzen.
7. SnapPattern als vorhandenen Patternlet-Träger nutzen.

Beispiele:

```text
Vision: wiederkehrender fp12-Cluster
Audio: wiederkehrender Toncluster
PTZ: wiederkehrende Fixationszone
Curriculum: wiederkehrender Fehlertyp
```

Akzeptanzkriterien:

```text
Patternlet-Kandidaten messbar
Wiederholung r2/r3/r5 messbar
Keine Massenerzeugung
Keine DB-Locks
Keine direkte Policy-Beeinflussung
SnapPattern-Kompatibilität erhalten
```

---

## Phase 4 – Binding Graph Stage A: Messen, nicht materialisieren

**Status:** nach stabiler Patternlet Discovery

Ziel:

Bindings zunächst nur messen, noch nicht aggressiv materialisieren.

Aufgaben:

1. Co-Occurrence-Kandidaten zählen.
2. Zeitliche Nähe messen.
3. Kontextnähe messen.
4. Sequenzen in SnapChains erkennen.
5. Kandidaten im Status/Dream sichtbar machen.
6. Keine breiten Graph-Writes im Day-Modus.

Metriken:

```text
nmr.binding.candidates
nmr.binding.temporal_candidates
nmr.binding.cooccurrence_candidates
nmr.binding.sequence_candidates
nmr.binding.cross_source_candidates
nmr.binding.accepted_stage_a
```

---

## Phase 5 – Binding Graph Stage B: Materialisierung über object_relations

**Status:** erst nach 24–72h stabilen Messdaten

Ziel:

Aus stabilen Binding-Kandidaten echte Relationseinträge erzeugen.

Regeln:

- Dream-only Materialisierung
- DBWriter-only
- Top-K pro Tag
- Dedupe über `(a_id, relation, b_id)`
- Mindestwiederholung
- Mindestqualität
- keine direkten Motorentscheidungen
- UI-/Log-Sichtbarkeit
- bevorzugt vorhandene `object_relations` nutzen

Mögliche Relationstypen:

```text
nmr:co_occurs_with
nmr:precedes
nmr:predicts_weakly
nmr:part_of_pattern
nmr:compresses_to
nmr:context_of
nmr:attention_anchor
synaptic
```

---

## Phase 6 – Compression Ladder Materialisierung

**Status:** nach stabilen Bindings

Ziel:

SnapPatterns, SnapChains und Bindings zu Concepts verdichten.

Beispiele:

```text
mehrere Kanten → Kontur
Kontur + Fläche → Objektkandidat
Objektkandidat + Veränderung → Zustand
Zustand + Kontext → Erwartung
```

Dream-Aufgaben:

1. Wiederholte SnapPatterns zusammenführen.
2. Mehrfach-Bindings zu Konzeptkandidaten bündeln.
3. Konzepte nur bei stabiler Evidenz materialisieren.
4. MetaSnap-/ObjectGraph-/SceneGraph-Integration nutzen.
5. Kompressionsmetriken schreiben.

Metriken:

```text
nmr.compress.candidates
nmr.compress.accepted
nmr.compress.rejected_noise
nmr.compress.concept_created
nmr.compress.metaconcept_created
nmr.compress.bytes_saved_est
nmr.compress.raw_to_concept_ratio
```

---

## Phase 7 – Replay- und Expectation-Integration

**Status:** nach Concepts

Ziel:

Verdichtete NMR-Strukturen sollen Replay priorisieren und Erwartungsmodelle verbessern.

Aufgaben:

1. Replay-Bonus nicht nur aus `nmr_pe`, sondern auch aus SnapPattern-/Binding-Stabilität ableiten.
2. Überraschende Abweichungen von Concepts stärker replayen.
3. Wiederholte, stabile Concepts weniger roh replayen.
4. Erwartungsverletzung als eigene Metrik führen.

Metriken:

```text
nmr.replay.bonus_applied
nmr.replay.selected_by_pe
nmr.replay.selected_by_binding
nmr.replay.selected_by_concept_drift
nmr.expectation.hit
nmr.expectation.miss
nmr.expectation.violation_score
```

---

## Phase 8 – Attention- und Handlungsvorbereitung

**Status:** konservativ, erst nach Evidenz

Ziel:

NMR darf Aufmerksamkeit beeinflussen, aber nicht ungeprüft Aktionen übernehmen.

Mögliche sichere Effekte:

- PTZ-Aufmerksamkeit leicht in Richtung stabiler Attention-Anker schieben
- Replay-Priorität erhöhen
- UI-Hinweis erzeugen
- Dream-Konsolidierung priorisieren
- unsichere Muster als Beobachtungsziel markieren

Nicht sofort erlaubt:

- direkte Motorsteuerung ohne Policy-/Safety-Gate
- aggressive Aktionsbiases
- massenhafte neue Regeln
- unkontrollierte Graph-Ausweitung

---

## 13. Praktische Datenhaltung

Die Roadmap muss DB-schonend umgesetzt werden.

### 13.1 Keine Rohsignal-Massenspeicherung

Rohsignale bleiben in kurzen Puffern oder bestehenden Snap-/SnapChain-Strukturen.

### 13.2 DBWriter-only

Alle produktiven Writes müssen DBWriter-kompatibel sein.

Keine lokalen SQLite-Fallbacks bei aktivem DBWriter.

### 13.3 Tabellenstrategie

Kurzfristig sollen bestehende Strukturen genutzt werden:

- `metrics`
- `stats_points`
- `snapchains`
- `object_nodes`
- `object_relations`
- `meta_snaps`

Neue Tabellen erst dann, wenn bestehende Strukturen nachweislich nicht reichen.

### 13.4 Materialisierung nur mit Gates

Jede dauerhafte Struktur braucht:

- Wiederholung
- Qualität
- Dedupe
- Tagesbudget
- Sichtbarkeit
- Fehlerlogging

---

## 14. Qualitäts- und Sicherheitsprinzipien

Ein sensor-unabhängiges NMR kann sehr mächtig, aber auch gefährlich für DB-Größe und Graph-Rauschen werden.

Daher gelten diese Regeln:

1. **Measure first**  
   Erst messen, dann materialisieren.

2. **Dream before write**  
   Day markiert, Dream entscheidet.

3. **Reuse existing primitives**  
   Snap, SnapPattern, SnapChain, ObjectGraph und Replay werden genutzt, nicht dupliziert.

4. **No silent failure**  
   Fehler müssen sichtbar in Logs/Status/Metriken erscheinen.

5. **No local DB writes under DBWriter**  
   DBWriter bleibt der produktive Schreibpfad.

6. **Top-K budgets**  
   Jede Materialisierung braucht Tages-/Laufbudget.

7. **Dedupe everywhere**  
   Keine doppelten Kanten/Konzepte.

8. **Disableable by ENV**  
   Jede Phase muss abschaltbar sein.

9. **Pi-safe**  
   Keine schweren Dauer-Embeddings im Live-Tick.

10. **Headless-safe**  
    Keine Qt-/Wayland-/X11-Abhängigkeiten.

11. **Replay-first behavioral effect**  
    Verhalten zuerst indirekt über Replay beeinflussen, nicht direkt über Motorik.

---

## 15. ENV-Strategie

Vorgeschlagene ENV-Gates:

```ini
# Phase 2 – Snap-compatible NMR Observation Layer
OROMA_NMR_OBS_ENABLE=0
OROMA_NMR_OBS_VISION=1
OROMA_NMR_OBS_AUDIO=1
OROMA_NMR_OBS_PTZ=1
OROMA_NMR_OBS_CURRICULUM=1
OROMA_NMR_OBS_RUNTIME=1
OROMA_NMR_OBS_SNAP_COMPAT=1
OROMA_NMR_OBS_SNAPCHAIN_ANCHORS=0

# Phase 3 – Patternlet Discovery via SnapPattern
OROMA_NMR_PATTERNLET_ENABLE=0
OROMA_NMR_PATTERNLET_USE_SNAPPATTERN=1
OROMA_NMR_PATTERNLET_MIN_REPEAT=3
OROMA_NMR_PATTERNLET_TOPK_PER_RUN=25
OROMA_NMR_PATTERNLET_TOPK_PER_DAY=200

# Phase 4/5 – Binding via object_relations
OROMA_NMR_BINDING_MEASURE_ENABLE=0
OROMA_NMR_BINDING_MATERIALIZE_ENABLE=0
OROMA_NMR_BINDING_USE_OBJECT_RELATIONS=1
OROMA_NMR_BINDING_MIN_REPEAT=3
OROMA_NMR_BINDING_TOPK_PER_DAY=25

# Phase 6 – Compression
OROMA_NMR_COMPRESSION_ENABLE=0
OROMA_NMR_COMPRESSION_USE_METASNAP=1
OROMA_NMR_COMPRESSION_TOPK_PER_DAY=10

# Phase 7 – Replay Integration
OROMA_NMR_REPLAY_BONUS_ENABLE=0
OROMA_NMR_REPLAY_BONUS_MAX=0.15

# Safety
OROMA_NMR_DREAM_ONLY_MATERIALIZE=1
OROMA_NMR_NO_LOCAL_DB_WRITES=1
```

Default sollte konservativ sein: neue Phasen erst messen, dann einschalten.

---

## 16. Konkrete nächste Schritte

### Schritt 1 – Phase-1-Dokumentation abschließen

Status: praktisch erreicht.

Dokumentieren:

- NMR-Lite Livevalidierung
- Vision-Bridge
- DBWriter-Persistenz
- Status-API
- PE/EMA-Bewegung

### Schritt 2 – Snap-kompatible Observation-Spezifikation schreiben

Neue Datei sinnvoll:

```text
docs/core/79_nmr_snap_observation_layer.md
```

Inhalt:

- Snap-Metadata-Konvention
- Quellen
- Normalisierung
- Qualitätsfelder
- SnapChain-Strategie
- ENV-Gates
- Nicht-Ziele: keine neue Grundpersistenz

### Schritt 3 – Measure-only Observation Probe

Implementierung als Tool oder Hook-Addon:

```text
tools/nmr_observation_probe.py
```

Ziel:

- Snap-kompatible Observations zählen
- Quellen zählen
- Qualität messen
- Degradation messen
- keine neuen Graph-Writes
- keine lokalen DB-Writes

### Schritt 4 – Patternlet Probe über SnapPattern

Neue Probe:

```text
tools/nmr_patternlet_probe.py
```

Ziel:

- Wiederholung erkennen
- r2/r3/r5 messen
- pro Quelle und quer über Quellen auswerten
- SnapPattern-Kompatibilität prüfen
- nur `metrics`/`stats_points` schreiben

### Schritt 5 – Dream-Konsolidierung vorbereiten

Später:

```text
core/nmr_dream_consolidator.py
```

Ziel:

- SnapPattern-Kandidaten auswählen
- Bindings prüfen
- Top-K über object_relations materialisieren
- Compression Ladder mit MetaSnap/ObjectGraph bedienen

---

## 17. Abgrenzung zu klassischer Sensorfusion

Diese Roadmap beschreibt keine klassische Sensorfusion.

Klassische Sensorfusion fragt oft:

```text
Wie kombiniere ich Vision + Audio zu einer besseren Messung?
```

NMR fragt:

```text
Welche wiederkehrenden Snap-/Pattern-Strukturen entstehen über Zeit, Kontext und Quellen hinweg,
und welche davon sind speicherwürdig?
```

Sensorfusion optimiert eine aktuelle Wahrnehmung.

NMR verdichtet Erfahrung zu Gedächtnisstruktur.

---

## 18. Abgrenzung zu LLM/Vektor-Embedding

Sensor-unabhängiges NMR ist kein LLM-Ersatz und kein reiner Vektorindex.

Ein Vektorindex findet Ähnlichkeit.

NMR soll zusätzlich bewerten:

- Wiederholung
- Zeitnähe
- Kontextnähe
- Stabilität
- Überraschung
- Bindung
- Verdichtung
- Erwartungswert
- Replay-Nutzen

NMR ist damit näher an einem Gedächtnis- und Konsolidierungssystem als an einer reinen Retrieval-Schicht.

---

## 19. Langfristiges Zielbild

Das langfristige Ziel ist ein ORÓMA-System, das über beliebige Quellen hinweg eigenständig Strukturen aufbaut:

```text
Ich sehe nicht nur Pixel.
Ich erkenne Kanten.
Ich erkenne wiederkehrende Kanten.
Ich erkenne daraus Formen.
Ich erkenne Zustände.
Ich erkenne Veränderung.
Ich erkenne Zusammenhang.
Ich verdichte Erfahrung.
Ich bilde Erwartung.
```

Und analog:

```text
Ich höre nicht nur Frequenzen.
Ich erkenne Töne.
Ich erkenne Tonfolgen.
Ich erkenne wiederkehrende Ereignisse.
Ich binde sie an Kontext.
Ich bilde Erwartung.
```

Der entscheidende ORÓMA-Satz lautet:

> Nicht Sensoren werden hart verdrahtet, sondern wiederkehrende Strukturen werden über Snap/SnapChain/SnapPattern/ObjectGraph gebunden und verdichtet.

---

## 20. Aktueller Meilenstein und nächste Architekturentscheidung

Der aktuelle Meilenstein ist:

```text
NMR-Lite Phase A/A.5 live validiert.
```

Die nächste Architekturentscheidung ist:

```text
NMR nicht weiter nur als sensorspezifischen PE-Layer ausbauen,
sondern schrittweise als dünne, sensor-agnostische Logikschicht über Snap/SnapChain/SnapPattern/ObjectGraph führen.
```

Empfohlene Reihenfolge:

```text
1. NMR-Lite stabil halten.
2. Snap-kompatible NMR Observation Convention spezifizieren.
3. Measure-only Observation Probe bauen.
4. Patternlet Discovery über SnapPattern messen.
5. Dream-first Binding über object_relations materialisieren.
6. Compression Ladder mit MetaSnap/ObjectGraph/SceneGraph einführen.
7. Replay/Expectation koppeln.
8. Erst spät Attention/Action beeinflussen.
```

---

# English Section – Sensor-Agnostic NMR Roadmap v2.1

## Purpose

This roadmap describes the next architectural stage after the validated NMR-Lite milestone.

NMR-Lite is a productive Phase-1 layer: it computes live mismatch, prediction error, EMA, priority and early binding hints from concrete signals such as vision fingerprints, audio features, curriculum state and runtime state.

The long-term goal is larger:

```text
Snap-compatible sensor-agnostic hierarchical pattern binding and compression
```

## Important correction in v2.1

The system should not introduce a parallel Observation Atom persistence model.

ORÓMA already has the universal memory primitives:

```text
Snap
SnapToken
SnapPattern
SnapChain
FusionPack
SensorChannel
ObjectGraph
object_relations
Replay
DreamWorker
```

Therefore:

```text
Observation Atom = NMR semantic view of an existing Snap or snap-compatible input
Patternlet       = early/small SnapPattern-like structure
Binding Graph    = object_nodes / object_relations
Concept          = MetaSnap / ObjectGraph / SceneGraph structure
```

## Core idea

A real NMR system should not primarily ask:

```text
Is this vision or audio?
```

It should ask:

```text
What Snap-compatible structure is present?
Does it repeat?
Is it stable?
Which SnapChain context does it occur in?
What other structures does it bind to?
Can it be compressed?
Can it form an expectation?
```

## Compression ladder

The intended ORÓMA-compatible ladder is:

```text
Raw signal
→ Snap-compatible observation
→ Snap
→ SnapPattern / Patternlet
→ SnapChain context
→ object_relation / Binding
→ MetaSnap / ObjectGraph / SceneGraph Concept
→ Expectation
```

## Day/Dream principle

Day should observe, compute lightweight PE/priority and mark candidates.

Dream should consolidate, deduplicate, materialize and compress.

```text
Day observes and marks.
Dream decides and compresses.
```

## Roadmap phases

1. **NMR-Lite Live Mismatch Layer** – achieved.
2. **Snap-compatible NMR Observation Layer** – standardize NMR metadata on top of Snap/SnapChain.
3. **Patternlet Discovery via SnapPattern** – detect recurring microstructures.
4. **Binding Graph Stage A** – measure binding candidates.
5. **Binding Graph Stage B** – materialize stable bindings through object_relations during Dream.
6. **Compression Ladder** – compress bindings into concepts using existing MetaSnap/ObjectGraph/SceneGraph paths.
7. **Replay and Expectation Integration** – use NMR structures to guide replay.
8. **Attention / Action Preparation** – only after evidence and strict safety gates.

## Key principle

NMR is not classical sensor fusion.

Sensor fusion improves current perception.

NMR consolidates recurring structure into memory.

The long-term ORÓMA goal is:

```text
not hard-wired sensors,
but recurring structures bound and compressed through Snap/SnapChain/SnapPattern/ObjectGraph.
```

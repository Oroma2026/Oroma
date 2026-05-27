# ORÓMA – NMR Phase 2: Snap-kompatibler Observation-Layer und Dream-Binding-Probe Implementierungsplan v1.3

**Datei / Path:** `docs/core/79_nmr_phase2_observation_atoms_implementation_plan.md`  
**Projekt / Project:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Kurzbeschreibung / Short description:** An offline-first adaptive edge intelligence architecture  
**Version:** v1.3 – praktischer Umsetzungsplan für NMR Phase 2 mit Beobachtungsmodus, History-JSONL und nicht-materialisierender Binding-Probe  
**Datum / Date:** 2026-05-27  
**Autor / Author:** ORÓMA-Projekt / Jörg Werner, redaktionell ausgearbeitet mit ChatGPT  
**Baseline:** `oroma_20260527_205026_with_db.zip`  
**Status:** Implementierungsplanung; v1.3 ergänzt den operativen Beobachtungsmodus für `tools/nmr_binding_probe.py`: regelmäßige Probe-Läufe, History-Verlauf, Wiederkehr-Prüfung und weiterhin keine Materialisierung in `object_relations`.  
**Bezug:** `docs/core/76_nmr_lite_live_validation.md`, `docs/core/78_sensor_agnostic_nmr_roadmap.md`

---
**Änderung v1.3 / Change v1.3:**  
Diese Fassung ergänzt den konservativen Beobachtungsmodus nach dem ersten erfolgreichen Probe-Run: `tools/nmr_binding_probe.py` soll regelmäßig laufen, die Werte über mehrere Läufe beobachten, keine `object_relations` materialisieren und erst nach mehreren Tagen prüfen, ob dieselben Kandidaten wiederkehren. Dafür wird eine optionale `nmr_binding_probe_history.jsonl` genutzt; systemd-Service/Timer bleiben measure-only und schreiben ausschließlich State/History/Metriken.

---
**Änderung v1.2 / Change v1.2:**  
Diese Fassung ergänzt die in der Review als kritisch erkannte Brücke zwischen NMR-Lite und dem Snap-/SnapChain-Substrat: `tools/nmr_binding_probe.py` soll nicht nur globale Co-Occurrences messen, sondern Snap-/SnapChain-Zeitfenster mit lokalen NMR-Metriken (`nmr:binding_hint_score`, `nmr:priority`, `nmr:pe`, `nmr:pe_ema`, `nmr:confidence`) ausrichten. Dadurch werden Binding-Kandidaten nur dann stark gewichtet, wenn Wiederholung, zeitliche Nähe und NMR-Resonanz gemeinsam auftreten.

**Änderung v1.1 / Change v1.1:**  
Diese Fassung schärft Phase 2 anhand der Core-Gesamtprüfung und des Binding-Brainstormings: Binding wird nicht als Live-Trigger oder direkte Wenn-dann-Regel verstanden, sondern als akkumulierender, gedächtnisbestätigter und Dream-konsolidierter Prozess. Der erste praktische Schritt ist daher eine messende `tools/nmr_binding_probe.py`, die vorhandene Snaps, SnapPatterns, SnapChains und NMR-Metriken auswertet, aber noch keine `object_relations` materialisiert.

---

## 1. Zweck dieses Dokuments

Dieses Dokument konkretisiert die nächste praktische Entwicklungsstufe nach der erfolgreichen Live-Validierung von NMR-Lite.

NMR-Lite Phase A/A.5 ist im Live-System validiert:

```text
Sensor-/Systemsignale
→ NMR-Lite Tick
→ Prediction Error / EMA / Priority / BindingScore
→ DBWriter-kompatible Metriken
→ Status-API
```

Der nächste Schritt ist **nicht**, ein neues paralleles Observation-Atom-System zu bauen. ORÓMA besitzt mit `Snap`, `SnapPattern`, `SnapChain`, `object_nodes`, `object_relations`, `DreamWorker` und `Replay` bereits die wesentlichen universellen Gedächtnisbausteine.

Dieses Dokument definiert deshalb Phase 2 als:

```text
Snap-kompatibler NMR Observation Layer
```

Das Ziel ist eine dünne, produktive, Pi-sichere Schicht, die bestehende Snaps und snap-kompatible Inputs so normalisiert, dass NMR sie sensor-unabhängig lesen, vergleichen, priorisieren, binden und später verdichten kann.

---

## 2. Kernentscheidung: Kein neues paralleles Datenmodell

Phase 2 darf keine zweite Welt neben Snap/SnapChain erzeugen.

Nicht bauen:

```text
neue ObservationAtom-Tabelle
neue Patternlet-Tabelle
neuer BindingGraph-Store
neues Episodenformat
neuer Sensor-Stack neben SensorChannel
neuer Compression-Store neben MetaSnap/ObjectGraph/SceneGraph
```

Stattdessen werden vorhandene ORÓMA-Bausteine genutzt:

| Roadmap-Begriff | ORÓMA-Baustein |
|---|---|
| Observation Atom | Snap oder snap-kompatibler Input |
| Patternlet | frühe/kleine SnapPattern-Instanz |
| Episode | SnapChain |
| Binding Graph | `object_nodes` / `object_relations` |
| Concept | MetaSnap / ObjectGraph / SceneGraph |
| Consolidation | DreamWorker / Replay |
| Live PE/Priority | NMR-Lite |

Die wichtigste Formulierung für Phase 2 lautet:

> Ein Observation Atom ist keine neue Persistenzklasse, sondern die NMR-semantische Sicht auf einen Snap oder einen snap-kompatiblen Input.

---

## 3.0 Zentrale Präzisierung v1.1: Binding ist kein Einzelereignis

Die wichtigste Korrektur dieser Fassung ist die Trennung zwischen **Daten-Normalisierung** und **Binding**.

Ein Snap-kompatibler Observation-Layer verbessert die gemeinsame Sprache der Sensoren. Er erzeugt aber nicht automatisch robuste Bindungen. Binding entsteht in ORÓMA nicht dadurch, dass zwei Signale einmal gleichzeitig auftreten. Binding entsteht durch wiederholte, kontextnahe, ausreichend starke und später bestätigte Co-Aktivierung.

Leitregel:

```text
Binding wird nicht ausgelöst.
Binding wird angesammelt.
Binding ist kein Einzelereignis, sondern eine vom Gedächtnis bestätigte Beziehung.
```

Englisch:

```text
Binding is not triggered.
Binding is accumulated.
Binding is not a single event, but a memory-confirmed relation.
```

Das passt zur bestehenden ORÓMA-Architektur:

```text
Day / AgentLoop:
  beobachten, normalisieren, PE/Priority messen, Kandidatenspuren erzeugen

Dream:
  Wiederholung prüfen, Kandidaten bewerten, Rauschen verwerfen, stabile Beziehungen verstärken

ObjectGraph:
  erst nach bestätigter Stabilität object_relations schreiben oder aktualisieren
```

Diese Fassung empfiehlt deshalb ausdrücklich, vor jeder Materialisierung zuerst eine messende Binding-Probe einzuführen.

---

## 3.1 Biologisch plausibles Akkumulationsmodell für ORÓMA

Die Arbeitsannahme ist an Hebb-artiger Co-Aktivierung orientiert, aber bewusst technisch und Pi-sicher formuliert:

```text
1. Temporäre Co-Aktivierung
   Zwei Snaps / SnapPatterns / SnapChain-Segmente treten zeitlich nah auf.

2. Wiederholung
   Die gleiche oder ähnliche Kombination tritt mehrfach auf.

3. Gewichtung
   PE, EMA, Priority, Confidence, Kontextnähe und zeitliche Nähe erhöhen oder senken den Kandidatenwert.

4. Dream-Bewertung
   Der Dream-Prozess entscheidet, welche Kandidaten stabil genug sind.

5. Materialisierung
   Erst bestätigte Kandidaten werden als object_relations / synaptic notes / spätere Konzepte persistiert.
```

Eine einfache Startformel für spätere Probes:

```text
binding_score =
  repeat_score          * 0.40
+ temporal_score        * 0.25
+ context_score         * 0.15
+ nmr_priority_score    * 0.10
+ nmr_pe_ema_score      * 0.10
- noise_penalty
- age_decay
```

Start-Schwellen für reine Messung, nicht für Materialisierung:

```text
repeat_count >= 3      → sichtbarer Kandidat
repeat_count >= 4..6   → wiederkehrender Kandidat
binding_score >= 0.45  → schwacher Binding-Kandidat
binding_score >= 0.65  → starker Binding-Kandidat, aber noch nicht automatisch persistieren
```

Erst wenn solche Kandidaten über mehrere Dream-Läufe stabil bleiben, darf eine spätere Phase sie in `object_relations` materialisieren.

---

## 3. Ziele von Phase 2

Phase 2 soll folgende Ziele erreichen:

1. **Einheitliche NMR-Sicht auf Snaps**  
   Vision, Audio, PTZ, Curriculum und interne Zustände sollen in einem gemeinsamen semantischen Raster beschreibbar sein.

2. **Sensor-unabhängige Vergleichbarkeit**  
   NMR soll nicht nur `vision_fp12` oder `audio_rms` kennen, sondern allgemein mit `source`, `kind`, `features`, `strength`, `confidence`, `context` arbeiten können.

3. **Leichtgewichtige Live-Verarbeitung**  
   Day-Phase bleibt schnell und headless. Keine schweren Ähnlichkeitssuchen im AgentLoop.

4. **Dream-first Verdichtung**  
   Wiederholung, Patternlet-Kandidaten, Binding-Kandidaten und Kompression sollen primär im Dream/Replay-Pfad ausgewertet werden.

5. **Reuse bestehender Persistenz**  
   Persistenz läuft über Snap/SnapChain/ObjectRelations und DBWriter-kompatible Pfade.

6. **Keine stillen Fehler**  
   Jeder relevante Fehler muss geloggt oder in Status/Metriken sichtbar werden.

---

## 4. Nicht-Ziele von Phase 2

Phase 2 soll bewusst begrenzt bleiben.

Nicht-Ziele:

- kein vollständiges emergentes Weltmodell
- keine großen Embedding-Suchen im AgentLoop
- keine neue Vektor-Datenbank-Pflicht
- keine blockierenden `COUNT(*)`-Analysen im Live-Hook
- keine direkten lokalen SQLite-Writes bei aktivem DBWriter
- kein Ersatz für Snap/SnapChain
- kein Ersatz für NMR-Lite
- kein Ersatz für DreamWorker

Phase 2 ist eine Normalisierungs- und Orchestrierungsschicht, kein neues Gehirn neben dem bestehenden System.

---

## 5. Vorgeschlagenes Snap-kompatibles NMR-Observation-Format

Ein NMR-Observation-Atom ist als Snap-kompatibles Dict definiert. Es kann direkt in einen Snap überführt oder als Metadata-Erweiterung an bestehende Snaps angehängt werden.

### 5.1 Minimalformat

```json
{
  "schema": "nmr.observation.v1",
  "source": "vision",
  "kind": "scene_fingerprint",
  "features": [0.1486, 0.0966, 0.0904],
  "strength": 0.20,
  "confidence": 1.0,
  "ts": 1779820000.0,
  "metadata": {
    "nmr_atom": true,
    "phase": "DAY",
    "origin": "hooks_av_snaptoken",
    "snap_compatible": true
  }
}
```

### 5.2 Felddefinitionen

| Feld | Typ | Bedeutung |
|---|---|---|
| `schema` | string | Formatkennung, Start: `nmr.observation.v1` |
| `source` | string | Ursprungskanal: `vision`, `audio`, `ptz`, `curriculum`, `runtime`, `empathy`, `game`, `system` |
| `kind` | string | Strukturtyp innerhalb des Kanals, z. B. `scene_fingerprint`, `tone_cluster`, `task_error` |
| `features` | list[float] | numerischer Vektor, Snap-kompatibel |
| `strength` | float | Signalstärke / Relevanz im Bereich 0..1 |
| `confidence` | float | Vertrauensgrad im Bereich 0..1 |
| `ts` | float | Epoch-Zeitstempel |
| `metadata` | dict | Kontext, Herkunft, Debug, Governance |

### 5.3 Snap-Mapping

Ein NMR-Observation-Atom wird als Snap abgebildet:

```text
features  → Snap.features
metadata  → Snap.metadata
kind      → metadata["kind"]
source    → metadata["source"] oder metadata["modality"]
content   → optionaler Text/JSON-Content
```

Beispiel:

```python
Snap(
    features=atom["features"],
    metadata={
        "nmr_atom": True,
        "source": atom["source"],
        "kind": atom["kind"],
        "strength": atom["strength"],
        "confidence": atom["confidence"],
        "schema": atom["schema"],
    },
    content=None,
    ts=atom["ts"],
)
```

---

## 6. Beispiel-Adapter pro Kanal

### 6.1 Vision

Quelle:

- `core/hooks_av_snaptoken.py`
- vorhandener `OromaWrapper.embed(frame=None)`-Pfad
- vorhandene CamToken-Metriken
- NMR-Lite Vision-Bridge

Beispiel-Atom:

```json
{
  "schema": "nmr.observation.v1",
  "source": "vision",
  "kind": "scene_fingerprint",
  "features": [0.1486, 0.0966, 0.0904, 0.0646, 0.0798, 0.1608, 0.0622, 0.0590, 0.0633, 0.0247, 0.0120, 0.0092],
  "strength": 0.203,
  "confidence": 1.0,
  "metadata": {
    "origin": "hooks_av_snaptoken",
    "vision_scene_change": 0.0113,
    "snap_counter": 2
  }
}
```

Phase-2-Verhalten:

- Vision-Fingerprint bleibt als Snap-kompatibler Feature-Vektor erhalten.
- `vision_scene_change` wird in Metadata geführt, nicht als Sondermodell.
- NMR-Lite darf weiter `update_vision_signal()` nutzen.
- Der Observation Layer erzeugt zusätzlich eine einheitliche Snap-Sicht.

### 6.2 Audio

Quelle:

- `core/hooks_audio_snaptoken.py`
- vorhandener Audio-RMS/Pitch-Pfad
- ASR/Whisper-Pfad später optional

Beispiel-Atom:

```json
{
  "schema": "nmr.observation.v1",
  "source": "audio",
  "kind": "tone_state",
  "features": [0.000079, 3292.84],
  "strength": 0.05,
  "confidence": 0.65,
  "metadata": {
    "origin": "hooks_audio_snaptoken",
    "audio_rms": 0.000079,
    "audio_pitch": 3292.84,
    "degraded_reason": "low_rms_possible_noise"
  }
}
```

Phase-2-Verhalten:

- Sehr niedrige RMS-Werte werden nicht als harter Fehler behandelt.
- Audio kann als schwach/degraded markiert werden, bleibt aber als Beobachtung verwertbar.
- Wiederkehrende Tonzustände können später zu SnapPatterns verdichtet werden.

### 6.3 PTZ

Quelle:

- `core/device_hub.py`
- PTZ-Metriken
- PTZ-Coverage/Attention-State
- PTZ-Motor-Worker-Status

Beispiel-Atom:

```json
{
  "schema": "nmr.observation.v1",
  "source": "ptz",
  "kind": "fixation_state",
  "features": [0.12, -0.08, 0.0, 0.73],
  "strength": 0.73,
  "confidence": 0.9,
  "metadata": {
    "origin": "device_hub.ptz_status",
    "pan_norm": 0.12,
    "tilt_norm": -0.08,
    "zoom_norm": 0.0,
    "coverage_score": 0.73
  }
}
```

Phase-2-Verhalten:

- PTZ ist kein Sonderfall, sondern eine Quelle räumlicher Aufmerksamkeit.
- PTZ-Fixationen können mit Vision-Snaps gebunden werden.
- Blindspots/Coverage-Gaps können als eigene Observation-Kinds erscheinen.

### 6.4 Curriculum

Quelle:

- bestehende Curriculum-Logs/Metriken
- AgentLoop-Curriculum-Hook

Beispiel-Atom:

```json
{
  "schema": "nmr.observation.v1",
  "source": "curriculum",
  "kind": "task_result",
  "features": [1.0, 0.0, 2.0, 0.42],
  "strength": 0.8,
  "confidence": 1.0,
  "metadata": {
    "origin": "curriculum_hook",
    "success": true,
    "level": 2,
    "task_family": "fill",
    "difficulty": 0.42
  }
}
```

Phase-2-Verhalten:

- Fehler/Wiederholungen werden als Musterquelle behandelt.
- Curriculum-Snaps können mit NMR-PE/Replay-Priorität verknüpft werden.
- Wiederkehrende Fehlerfamilien können später SnapPatterns bilden.

### 6.5 Runtime / System

Quelle:

- AgentLoop-Status
- DBWriter-Status
- Service/Worker-Status

Beispiel-Atom:

```json
{
  "schema": "nmr.observation.v1",
  "source": "runtime",
  "kind": "loop_health",
  "features": [0.25, 0.0, 1.0],
  "strength": 0.5,
  "confidence": 1.0,
  "metadata": {
    "origin": "agent_loop.status",
    "dt": 0.25,
    "ticks_ok": 15379,
    "ticks_failed": 0
  }
}
```

Phase-2-Verhalten:

- Runtime ist ebenfalls ein beobachtbarer Kanal.
- Instabilität, Hänger, DBWriter-Stau oder Worker-Fehler können als NMR-relevante Ereignisse verarbeitet werden.

---

## 7. Day-Phase: Ringbuffer-Strategie

Die Day-Phase muss leicht bleiben. Deshalb wird Phase 2 zunächst als In-Memory-Ringbuffer implementiert.

### 7.1 Grundprinzip

```text
Hook erzeugt snap-kompatibles Observation-Atom
→ Observation Layer normalisiert
→ kleiner Ringbuffer im RAM
→ NMR-Lite liest aggregierte Signale
→ optional Metriken
→ persistente Verdichtung erst später/Dream
```

### 7.2 Vorgeschlagene Limits

ENV-Defaults:

```text
OROMA_NMR_OBS_ENABLE=0
OROMA_NMR_OBS_RING_MAX=256
OROMA_NMR_OBS_MAX_FEATURES=64
OROMA_NMR_OBS_MAX_PER_TICK=8
OROMA_NMR_OBS_MIN_CONFIDENCE=0.05
OROMA_NMR_OBS_PERSIST=0
OROMA_NMR_OBS_DEBUG=0
```

Bedeutung:

| ENV | Default | Zweck |
|---|---:|---|
| `OROMA_NMR_OBS_ENABLE` | `0` | Observation Layer aktivieren |
| `OROMA_NMR_OBS_RING_MAX` | `256` | maximale Atome im RAM-Ringbuffer |
| `OROMA_NMR_OBS_MAX_FEATURES` | `64` | Feature-Vektor hart begrenzen |
| `OROMA_NMR_OBS_MAX_PER_TICK` | `8` | Live-Aufwand pro Tick begrenzen |
| `OROMA_NMR_OBS_MIN_CONFIDENCE` | `0.05` | sehr schwache Inputs filtern |
| `OROMA_NMR_OBS_PERSIST` | `0` | persistente Snap-Erzeugung zunächst aus |
| `OROMA_NMR_OBS_DEBUG` | `0` | ausführliche Debug-Metriken aus |

### 7.3 Warum Persistenz zunächst aus bleibt

Die Live-Phase soll nicht sofort jeden Atom-Schnipsel persistieren. Sonst entsteht DB-Wachstum ohne Qualitätsfilter.

Empfohlen:

- Day: Ringbuffer + Aggregatmetriken
- Dream: Kandidaten rekonstruieren, verdichten, persistieren
- Replay: relevante SnapChains priorisieren

---

## 7.5 Vorgeschalteter praktischer Schritt: `tools/nmr_binding_probe.py`

Vor der vollständigen Umsetzung eines neuen Live-Observation-Layers sollte ORÓMA zuerst messen, ob im vorhandenen Snap-/SnapChain-Bestand bereits stabile Binding-Kandidaten existieren.

Der empfohlene erste Code-Schritt ist daher:

```text
tools/nmr_binding_probe.py
```

### 7.5.1 Zweck

Die Probe ist ein Dream-/Analysewerkzeug. Sie liest vorhandene Daten, berechnet Kandidaten und schreibt nur Diagnose-Artefakte.

Sie darf in der ersten Version **keine** `object_relations` schreiben.

Ziele:

```text
- vorhandene Snaps / SnapPatterns / SnapChains auswerten
- zeitnahe Co-Occurrences finden
- Wiederholungen zählen
- einfache Binding-Scores berechnen
- Kandidaten sichtbar machen
- Schwellwerte realistisch kalibrieren
- Rauschen erkennen, bevor etwas materialisiert wird
```

Nicht-Ziele:

```text
- keine direkten object_relations-Writes
- keine Policy-/Replay-Verhaltensänderung
- kein blockierender AgentLoop-Code
- keine neue Persistenzklasse neben Snap/SnapChain
- keine harte Wenn-dann-Bindung
```

### 7.5.2 Eingaben

Die Probe sollte bevorzugt aus bestehenden Tabellen und Statusquellen lesen:

```text
Snap / Snap-ähnliche Payloads
SnapPattern / Pattern-Zusammenfassungen
SnapChain / Episodenfenster
metrics mit nmr:pe, nmr:pe_ema, nmr:priority, nmr:confidence
metrics mit nmr:binding_hint_score, nmr:surprise und optional weiteren nmr:* Signalen
object_nodes / object_relations nur lesend zur Dedupe-/Vorwissen-Prüfung
```

Die genaue DB-Auswahl muss vor dem Codepatch gegen die aktuelle Live-ZIP geprüft werden. Es darf keine generische Schema-Annahme verwendet werden.

### 7.5.3 Kandidatenbildung

Startlogik:

```text
1. Nimm ein Zeitfenster, z. B. letzte 6h oder letzte N SnapChains.
2. Gruppiere Snap-/Pattern-Ereignisse nach kurzem Zeitfenster, z. B. 1..5 Sekunden.
3. Erzeuge Kandidatenpaare nur zwischen unterscheidbaren Quellen/Kinds oder klar unterschiedlichen SnapPattern-Clustern.
4. Zähle Wiederholung über mehrere Fenster.
5. Berechne temporal_score aus Zeitnähe.
6. Berechne context_score aus ähnlicher Phase, Quelle, PTZ-Kontext, Chain-Kontext oder Pattern-Ähnlichkeit.
7. Moduliere mit NMR-Werten, sofern im Zeitfenster vorhanden.
```

Beispiel:

```text
vision/token A + ptz/fixation B tritt 7x in ähnlichem Kontext auf
→ Kandidat mit repeat_count=7 und temporal_score hoch
→ stark sichtbar in State/Metrik
→ noch keine object_relation
```

### 7.5.4 NMR-Lite → Snap/SnapChain Metric Alignment

Die Binding-Probe darf nicht nur globale Co-Occurrences zählen. Sie muss die Brücke zwischen NMR-Lite und dem episodischen Snap-Substrat herstellen.

Kernregel:

```text
Binding-Kandidaten werden nicht nur durch zeitliche Nähe bewertet,
sondern durch zeitliche Nähe zu NMR-Lite-Ausschlägen verstärkt oder abgeschwächt.
```

Ohne diese Brücke wäre die Probe nur eine allgemeine statistische Näheanalyse. Mit dieser Brücke wird sie zu einer NMR-resonanzgewichteten Binding-Probe.

Die Probe sollte deshalb für jede ausgewertete SnapChain oder jedes Snap-/Pattern-Zeitfenster zusätzlich ein lokales NMR-Fenster lesen:

```text
SnapChain / Snap timestamp t
→ NMR-Metriken im Fenster [t - pre_sec, t + post_sec]
→ lokale Aggregate bilden
→ Kandidaten-Score mit NMR-Resonanz gewichten
```

Empfohlene Startwerte:

```text
pre_sec  = 5
post_sec = 10
```

Zu aggregierende NMR-Metriken:

```text
nmr:binding_hint_score   max / avg
nmr:priority             max / avg
nmr:pe                   max / avg
nmr:pe_ema               max / avg
nmr:confidence           avg
nmr:surprise             max
```

Wichtig: `nmr:binding_hint_score` ist ein Verstärker, aber keine alleinige Entscheidung. Ein hoher Score ohne Wiederholung darf keine stabile Bindung erzeugen.

Die Kandidatenbewertung sollte daher in zwei Ebenen erfolgen:

```text
Basis-Ebene:
  repeat_score
  temporal_score
  context_score

NMR-Resonanz-Ebene:
  nmr_binding_hint_score
  nmr_priority_score
  nmr_pe_ema_score
  nmr_confidence_score
```

Beispiel einer Startformel für die Probe:

```text
binding_score =
  repeat_score              * 0.35
+ temporal_score            * 0.20
+ context_score             * 0.15
+ nmr_binding_hint_score    * 0.15
+ nmr_priority_score        * 0.08
+ nmr_pe_ema_score          * 0.05
+ nmr_confidence_score      * 0.02
- noise_penalty
- age_decay
```

Diese Gewichtung ist ausdrücklich ein Startpunkt für Kalibrierung, keine endgültige biologische Wahrheit.

Die Probe sollte in ihrer State-Datei sichtbar unterscheiden:

```text
candidate_count
nmr_aligned_candidate_count
nmr_boosted_candidate_count
weak_candidate_count
strong_candidate_count
```

Definitionen:

```text
candidate_count:
  Alle wiederholten Co-Occurrence-Kandidaten.

nmr_aligned_candidate_count:
  Kandidaten, für die im lokalen Zeitfenster NMR-Metriken gefunden wurden.

nmr_boosted_candidate_count:
  Kandidaten, deren Score durch NMR-Signale messbar erhöht wurde.

weak_candidate_count:
  Kandidaten über schwacher Probe-Schwelle.

strong_candidate_count:
  Kandidaten über starker Probe-Schwelle, weiterhin ohne Materialisierung.
```

Die Top-Kandidaten müssen außerdem die NMR-Aggregate enthalten:

```json
{
  "a_ref": "snap:vision:...",
  "b_ref": "snap:ptz:...",
  "repeat_count": 7,
  "temporal_score": 0.82,
  "context_score": 0.61,
  "nmr_window": {
    "pre_sec": 5,
    "post_sec": 10
  },
  "nmr_binding_hint_score_max": 0.42,
  "nmr_priority_avg": 0.21,
  "nmr_pe_ema_max": 0.0007,
  "nmr_confidence_avg": 0.95,
  "nmr_aligned": true,
  "nmr_boosted": true,
  "binding_score": 0.67,
  "decision": "strong_candidate_probe_only"
}
```

Akzeptanzkriterium für `tools/nmr_binding_probe.py`:

```text
Die Probe muss reporten, welche Kandidaten nur durch Co-Occurrence entstehen
und welche Kandidaten zusätzlich durch NMR-Lite-Metriken verstärkt wurden.
```

Damit bleibt Binding biologisch plausibel: Wiederholung bildet die Basis, NMR-Resonanz wirkt als Aufmerksamkeits- und Konsolidierungs-Verstärker.

### 7.5.5 Ausgabe der ersten Version

Die Probe schreibt nur:

```text
data/state/nmr_binding_probe_state.json
```

und Metriken:

```text
nmr:binding_probe:candidates
nmr:binding_probe:weak_candidates
nmr:binding_probe:strong_candidates
nmr:binding_probe:avg_score
nmr:binding_probe:max_score
nmr:binding_probe:repeat_max
nmr:binding_probe:window_sec
nmr:binding_probe:nmr_aligned_candidates
nmr:binding_probe:nmr_boosted_candidates
nmr:binding_probe:nmr_binding_hint_score_max
nmr:binding_probe:nmr_priority_avg
nmr:binding_probe:nmr_pe_ema_max
nmr:binding_probe:materialized
```

Dabei gilt in Phase 2.0a:

```text
nmr:binding_probe:materialized = 0
```

### 7.5.6 State-Datei

Vorgeschlagene Struktur:

```json
{
  "ok": true,
  "ts": 1779820000.0,
  "mode": "probe_only",
  "window_sec": 21600,
  "source": "tools/nmr_binding_probe.py",
  "candidate_count": 42,
  "nmr_aligned_candidate_count": 31,
  "nmr_boosted_candidate_count": 12,
  "weak_candidate_count": 9,
  "strong_candidate_count": 2,
  "materialized_count": 0,
  "thresholds": {
    "min_repeat": 3,
    "weak_score": 0.45,
    "strong_score": 0.65
  },
  "top_candidates": [
    {
      "a_ref": "snap:vision:...",
      "b_ref": "snap:ptz:...",
      "repeat_count": 7,
      "temporal_score": 0.82,
      "context_score": 0.61,
      "nmr_binding_hint_score_max": 0.42,
      "nmr_priority_avg": 0.20,
      "nmr_pe_ema_max": 0.0007,
      "nmr_aligned": true,
      "nmr_boosted": true,
      "binding_score": 0.67,
      "decision": "strong_candidate_probe_only"
    }
  ]
}
```

### 7.5.7 Dream-Integration, aber zunächst nicht materialisierend

Die Probe sollte später vom DreamWorker oder Orchestrator als Dream-Job aufgerufen werden können, zunächst jedoch auch manuell laufen:

```bash
cd /opt/ai/oroma; sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma OROMA_DBW_ENABLE=1 python3 tools/nmr_binding_probe.py --once --window-sec 21600 --max-candidates 200 --verbose
```

In Phase 2.0a bleibt sie read-mostly und schreibt nur State/Metriken über den DBWriter-kompatiblen Pfad.

### 7.5.8 Akzeptanzkriterien für die Probe

```text
- Script läuft headless und ohne Qt/Wayland/X11.
- Script kompiliert mit python3 -m py_compile.
- Keine lokalen SQLite-Writes bei aktivem DBWriter.
- Keine object_relations-Writes in Probe-only-Modus.
- State-Datei enthält top_candidates.
- Top-Kandidaten enthalten lokale NMR-Aggregate aus dem Snap/SnapChain-Zeitfenster.
- State unterscheidet `candidate_count`, `nmr_aligned_candidate_count` und `nmr_boosted_candidate_count`.
- Metriken erscheinen unter nmr:binding_probe:*.
- Laufzeit bleibt begrenzt und konfigurierbar.
- Bei fehlenden Tabellen oder leeren Daten wird sichtbar geloggt und nicht still versagt.
```

---

## 8. Vorgeschlagene neue Datei: `core/nmr_observation_layer.py`

Phase 2 sollte in einer kleinen neuen Core-Datei beginnen:

```text
core/nmr_observation_layer.py
```

Diese Datei ist keine neue Persistenzwelt. Sie ist ein Adapter/Normalizer über Snap.

### 8.1 Aufgaben

- Observation-Atom-Dicts validieren
- Feature-Vektoren begrenzen und normalisieren
- Metadata vereinheitlichen
- Ringbuffer verwalten
- leichte Aggregatwerte bereitstellen
- optional Snap-Objekte erzeugen
- Status/Debug-Daten liefern

### 8.2 Öffentliche API

```python
submit_observation(atom: dict) -> bool
submit_snap_like(source: str, kind: str, features: list[float], metadata: dict | None = None, **kwargs) -> bool
get_recent(source: str | None = None, kind: str | None = None, limit: int = 32) -> list[dict]
get_summary(window_sec: float = 30.0) -> dict
as_snap(atom: dict) -> Snap
clear_old(max_age_sec: float = 300.0) -> int
status() -> dict
```

### 8.3 Kein DB-Write im ersten Schritt

`nmr_observation_layer.py` soll in v1 zunächst keine dauerhaften DB-Writes machen. Dadurch bleibt das Risiko klein.

Spätere optionale Persistenz darf nur über vorhandene Pfade laufen:

- `Snap`
- `SnapChain`
- `sql_manager` mit DBWriter-kompatiblem Pfad
- `object_relations` über DBWriter-kompatible Helper

---

## 9. Anpassungen an bestehenden Dateien

### 9.1 `core/hooks_av_snaptoken.py`

Aktueller Zweck:

- Vision/CamToken-Pfad
- NMR-Lite Vision-Bridge

Phase-2-Erweiterung:

- nach erfolgreichem Vision-Embedding zusätzlich `submit_snap_like(...)` aufrufen
- nur wenn `OROMA_NMR_OBS_ENABLE=1`
- Fehler sichtbar, aber rate-limited loggen

Beispiel:

```python
if _HAS_NMR_OBS:
    submit_snap_like(
        source="vision",
        kind="scene_fingerprint",
        features=fp12,
        metadata={
            "origin": "hooks_av_snaptoken",
            "vision_scene_change": scene_change,
            "cam_token_quality": q,
        },
        strength=float(binding_hint_score or 0.0),
        confidence=1.0,
    )
```

### 9.2 `core/hooks_audio_snaptoken.py`

Phase-2-Erweiterung:

- RMS/Pitch als `audio/tone_state` oder `audio/energy_state` einreichen
- sehr kleine RMS-Werte als `degraded_reason` markieren
- nicht blockieren, wenn Audio-Device ungültig ist

### 9.3 `core/agent_loop.py`

Phase-2-Erweiterung:

- `nmr_observation_layer.status()` optional in `/control/api/status` sichtbar machen
- keine schweren Berechnungen im Tick
- nur Summary lesen, nicht Ringbuffer vollständig serialisieren

### 9.4 `ui/control_ui.py`

Phase-2-Erweiterung:

- `nmr_observation`-Statusblock ausgeben
- nur kleine Summaries: counts nach source/kind, newest_ts, dropped_count, last_error

### 9.5 `core/dream_worker.py`

Phase-2-Erweiterung später:

- Dream-Phase kann aus Snaps/SnapChains Patternlet-Kandidaten ableiten
- nicht in Phase 2.0 erzwingen
- erst nach stabiler Day-Beobachtung aktivieren

### 9.6 `core/nmr_synaptic_plasticity.py`

Phase-2-Erweiterung später:

- Observation/SnapPattern-Kandidaten als `object_relations` relation=`synaptic` binden
- bestehende Hebb/Cooc-Struktur weiterverwenden
- keine neue Binding-Tabelle

---

## 10. Metriken

Phase 2 braucht einfache, belastbare Metriken.

### 10.1 Live-Metriken

Vorgeschlagene Keys:

```text
nmr:obs:submitted
nmr:obs:accepted
nmr:obs:dropped
nmr:obs:ring_size
nmr:obs:source:vision
nmr:obs:source:audio
nmr:obs:source:ptz
nmr:obs:source:curriculum
nmr:obs:kind:<kind>
nmr:obs:last_strength
nmr:obs:last_confidence
```

Diese Metriken sollten gedrosselt geschrieben werden, z. B. alle 30–60 Sekunden.

### 10.2 Akzeptanzmetriken

Minimum für Phase 2.0:

```text
Observation Layer enabled
accepted > 0
ring_size > 0
source:vision > 0 bei aktiver Kamera
source:audio > 0 bei aktivem Audio-Pfad
Status-API zeigt nmr_observation
AgentLoop bleibt stabil
keine neuen DB-Locks
```

### 10.3 Keine Qualitätslüge

Ein Atom mit sehr schwachem Signal darf angenommen werden, muss aber korrekt markiert werden:

```text
confidence niedrig
strength niedrig
degraded_reason gesetzt
```

Es darf nicht so aussehen, als wäre aus Rauschen ein hochwertiges Muster entstanden.

---

## 11. Akzeptanzkriterien Phase 2.0

Phase 2.0 gilt als erfolgreich, wenn folgende Punkte erfüllt sind:

1. `OROMA_NMR_OBS_ENABLE=1` aktiviert den Observation Layer.
2. `core/nmr_observation_layer.py` kompiliert und läuft headless.
3. Vision erzeugt Observation-Atoms aus bestehenden fp12-Werten.
4. Audio erzeugt Observation-Atoms aus bestehenden RMS/Pitch-Werten, auch wenn sie schwach sind.
5. `/control/api/status` zeigt einen kompakten `nmr_observation`-Block.
6. Ringbuffer zählt angenommene und verworfene Atome.
7. Keine lokalen SQLite-Writes bei aktivem DBWriter.
8. AgentLoop bleibt stabil über mindestens 10 Minuten.
9. NMR-Lite bleibt unverändert funktionsfähig.
10. Keine neue Tabelle ist erforderlich.

Optional für Phase 2.1:

- PTZ-Observation-Atoms
- Curriculum-Observation-Atoms
- einfache Snap-Konvertierung im Dream-Testmodus
- erste SnapPattern-Kandidaten aus wiederkehrenden Atomen

---

## 12. Risiken und Gegenmaßnahmen

### Risiko 1: Zu viele Atome

Problem:

- Jede Sensoränderung könnte ein Atom erzeugen.
- DB und RAM könnten wachsen.

Gegenmaßnahmen:

- Ringbuffer-Limit
- `OROMA_NMR_OBS_MAX_PER_TICK`
- `OROMA_NMR_OBS_MIN_CONFIDENCE`
- keine Day-Persistenz im Default

### Risiko 2: Rauschen wird als Struktur interpretiert

Problem:

- Audio-Grundrauschen oder minimale Bildänderungen könnten falsche Pattern erzeugen.

Gegenmaßnahmen:

- strength/confidence trennen
- degraded_reason setzen
- Dream-Konsolidierung erst bei Wiederholung/Stabilität
- Patternlet erst ab Mindestanzahl

### Risiko 3: Doppelarbeit mit SnapPattern

Problem:

- Neue Patternlet-Logik könnte SnapPattern duplizieren.

Gegenmaßnahmen:

- Patternlet als SnapPattern-Sicht definieren
- keine neue Tabelle
- bestehende Similarity/Centroid-Logik nutzen

### Risiko 4: AgentLoop-Blockade

Problem:

- Ähnlichkeitssuche oder DB-Zugriff im Tick blockiert Livebetrieb.

Gegenmaßnahmen:

- keine DB-Leseorgien im Hook
- keine COUNT(*) im Tick
- keine Clusterbildung in Day
- nur Ringbuffer + Summary

### Risiko 5: DBWriter-Umgehung

Problem:

- Direkte SQLite-Writes erzeugen Locks oder gehen im Strict-Pfad verloren.

Gegenmaßnahmen:

- DBWriter-kompatible Helper verwenden
- keine lokalen Fallbacks bei managed DBs
- Rückgabewerte prüfen
- Fehler sichtbar loggen

---

## 13. Vorgeschlagene Implementierungsreihenfolge

### Schritt 1: Dokumentation und Gates

- diese Datei einspielen
- ENV-Gates in `.env.systemd` dokumentieren, aber noch nicht aktivieren

### Schritt 2: `core/nmr_observation_layer.py`

- reine In-Memory-Implementierung
- Ringbuffer
- Validierung
- Status
- keine DB-Persistenz

### Schritt 3: Status-API

- `nmr_observation` in `/control/api/status`
- keine großen Payloads, nur Summary

### Schritt 4: Vision-Adapter

- `hooks_av_snaptoken.py` meldet fp12 zusätzlich an Observation Layer
- nur bei `OROMA_NMR_OBS_ENABLE=1`

### Schritt 5: Audio-Adapter

- `hooks_audio_snaptoken.py` meldet RMS/Pitch zusätzlich an Observation Layer
- weak/noise markieren

### Schritt 6: 10-Minuten-Stabilitätstest

- AgentLoop-Tick muss steigen
- NMR-Lite muss weiter persistieren
- Ringbuffer muss füllen
- keine DB-Locks

### Schritt 7: Dream-Probe

- separater Tool-/Dream-Test rekonstruiert aus Ringbuffer/Snaps erste SnapPattern-Kandidaten
- nur read-only oder Testmodus

### Schritt 8: Phase 2.1 planen

- PTZ/Curriculum hinzufügen
- SnapPattern-Reuse konkretisieren
- object_relations-Binding-Kandidaten definieren

---

## 14. Testbefehle nach Phase-2-Codepatch

### 14.1 Compile

```bash
cd /opt/ai/oroma; python3 -m py_compile core/nmr_observation_layer.py core/hooks_av_snaptoken.py core/hooks_audio_snaptoken.py core/agent_loop.py ui/control_ui.py core/nmr_lite.py core/sql_manager.py
```

### 14.2 Aktivierter Live-Test

```bash
cd /opt/ai/oroma; sudo systemctl restart oroma.service oroma-orchestrator.service; sleep 120; echo "===== STATUS ====="; curl -sS http://127.0.0.1:8080/control/api/status | python3 -m json.tool | egrep '"nmr_lite"|"nmr_observation"|"running"|"tick"|"last_error"|"ring_size"|"accepted"|"dropped"|"source_counts"' -A20 -B2; echo "===== METRICS ====="; sudo -u oroma sqlite3 /opt/ai/oroma/data/oroma.db "SELECT key, COUNT(*), ROUND(MIN(value),6), ROUND(MAX(value),6), datetime(MAX(ts),'unixepoch','localtime') FROM metrics WHERE ts > strftime('%s','now')-600 AND key LIKE 'nmr:obs:%' GROUP BY key ORDER BY key;"
```

### 14.3 Deaktivierungstest

```bash
cd /opt/ai/oroma; sudo mkdir -p /etc/systemd/system/oroma.service.d; printf '[Service]\nEnvironment=OROMA_NMR_OBS_ENABLE=0\n' | sudo tee /etc/systemd/system/oroma.service.d/49-nmr-observation-disable-test.conf >/dev/null; sudo systemctl daemon-reload; sudo systemctl restart oroma.service; sleep 45; curl -sS http://127.0.0.1:8080/control/api/status | python3 -m json.tool | egrep '"nmr_observation"|"running"|"tick"' -A20 -B2 || true
```

---

## 15. Minimaler Statusblock

`/control/api/status` sollte später ungefähr so aussehen:

```json
{
  "nmr_observation": {
    "enabled": true,
    "ring_size": 42,
    "submitted_total": 128,
    "accepted_total": 117,
    "dropped_total": 11,
    "source_counts": {
      "vision": 70,
      "audio": 35,
      "runtime": 12
    },
    "kind_counts": {
      "scene_fingerprint": 70,
      "tone_state": 35,
      "loop_health": 12
    },
    "last_error": null,
    "last_ts": 1779820000.0
  }
}
```

Wichtig: Keine kompletten großen Featurelisten im normalen Statusblock. Featurelisten nur bei Debug oder gezieltem Detailendpunkt.

---

## 16. Verhältnis zu NMR-Lite

NMR-Lite bleibt bestehen.

Phase 2 ersetzt NMR-Lite nicht, sondern ergänzt es:

```text
NMR-Lite:
  Live PE/EMA/Priority aus kompaktem Observation State

NMR Observation Layer:
  einheitliche Snap-kompatible Sicht auf beliebige Quellen

Dream/Replay:
  spätere Verdichtung und Binding-Konsolidierung
```

NMR-Lite kann später seine Inputs teilweise aus dem Observation Layer ableiten, aber nicht sofort. Der erste Schritt ist parallele, sichere Beobachtung.

---

## 17. Verhältnis zu SnapPattern

SnapPattern ist die natürliche Patternlet-Stufe.

Phase 2 sollte keine neue Patternlet-Persistenz bauen. Stattdessen:

```text
Observation-Atom-Snaps
→ ähnliche Features über Dream/Tool sammeln
→ SnapPattern-Centroid bilden
→ Gap/Similarity nutzen
→ SnapChain-Kontext prüfen
```

Erst wenn eine Struktur wiederholt stabil ist, darf daraus ein SnapPattern- oder Binding-Kandidat werden.

---

## 18. Verhältnis zu object_relations

Bindings sollen langfristig über vorhandene Relationstabellen laufen.

Prinzip:

```text
Snap/SnapPattern A
tritt wiederholt mit
Snap/SnapPattern B
im ähnlichen Kontext auf
→ object_relations relation='synaptic' oder spezifischer Relationstyp
```

Für Phase 2.0 noch nicht zwingend aktiv. Phase 2.0 sammelt nur Atome und Summaries.

---

## 19. Verhältnis zu DreamWorker und Replay

Dream ist der richtige Ort für schwere Arbeit.

Day:

```text
sehen / hören / zählen / PE erzeugen / Ringbuffer füllen
```

Dream:

```text
wiederkehrende Strukturen suchen
SnapPatterns aktualisieren
Bindings verstärken/abschwächen
Compression-Kandidaten bilden
Replay-Priorität anpassen
```

Replay kann später NMR-Metadaten nutzen:

```text
nmr_pe_ema
binding_hint_score
observation_source_counts
pattern_recurrence
relation_confidence
```

---

## 20. Phase-2-Erfolg in einem Satz

Phase 2 ist erfolgreich, wenn ORÓMA beliebige neue Sensor- oder Systemsignale als Snap-kompatible NMR-Beobachtungen aufnehmen kann, ohne neue Persistenzwelten zu bauen, ohne den AgentLoop zu blockieren und ohne die bestehende Snap/SnapChain/Dream-Architektur zu umgehen.

---

# English Section

## 21. Purpose

This document defines the practical Phase 2 implementation plan for NMR in ORÓMA.

The key correction is: Phase 2 must not introduce a parallel ObservationAtom database or a new memory system. ORÓMA already has the necessary universal primitives:

- Snap
- SnapPattern
- SnapChain
- object_nodes / object_relations
- DreamWorker
- Replay

Therefore, an Observation Atom is not a new persistence class. It is the NMR-semantic view of an existing Snap or snap-compatible input.

## 22. Phase 2 Goal

Phase 2 introduces a lightweight, snap-compatible NMR Observation Layer.

Its role is to normalize inputs from vision, audio, PTZ, curriculum, runtime and other sources into a common structure:

```text
source
kind
features
strength
confidence
context
```

This common representation can then be used by NMR-Lite, Dream and Replay without duplicating the existing memory architecture.

## 23. Non-goals

Phase 2 does not implement a full emergent world model. It does not add a new binding graph, a new patternlet table, or a new episode format. It does not run heavy clustering in the AgentLoop.

The Day phase remains lightweight. The Dream phase remains the place for consolidation and compression.

## 24. Recommended Implementation

Create:

```text
core/nmr_observation_layer.py
```

Initial API:

```python
submit_observation(atom: dict) -> bool
submit_snap_like(source: str, kind: str, features: list[float], metadata: dict | None = None, **kwargs) -> bool
get_recent(source: str | None = None, kind: str | None = None, limit: int = 32) -> list[dict]
get_summary(window_sec: float = 30.0) -> dict
as_snap(atom: dict) -> Snap
status() -> dict
```

The first implementation should be in-memory only, using a bounded ringbuffer. Persistent writes should remain disabled by default.

## 24.5 NMR-Lite to Snap/SnapChain Metric Alignment

The binding probe must not only count global co-occurrences. It must align Snap or SnapChain timestamps with local NMR-Lite metrics.

Core rule:

```text
A binding candidate is not only weighted by temporal proximity.
It is boosted when repeated co-occurrence overlaps with local NMR-Lite resonance.
```

For each Snap, SnapPattern or SnapChain window, the probe should read NMR metrics in a small window around the event timestamp:

```text
[t - pre_sec, t + post_sec]
```

Recommended initial values:

```text
pre_sec  = 5
post_sec = 10
```

Relevant metrics:

```text
nmr:binding_hint_score
nmr:priority
nmr:pe
nmr:pe_ema
nmr:confidence
nmr:surprise
```

The binding score should be based on repetition first, then boosted by NMR resonance:

```text
binding_score =
  repeat_score              * 0.35
+ temporal_score            * 0.20
+ context_score             * 0.15
+ nmr_binding_hint_score    * 0.15
+ nmr_priority_score        * 0.08
+ nmr_pe_ema_score          * 0.05
+ nmr_confidence_score      * 0.02
- noise_penalty
- age_decay
```

The probe must report which candidates are plain co-occurrence candidates and which candidates are NMR-aligned or NMR-boosted. This keeps the design biologically plausible: repetition is the base, NMR resonance is an attention and consolidation amplifier.

## 25. Acceptance Criteria

Phase 2.0 is successful when:

- the observation layer is enabled via `OROMA_NMR_OBS_ENABLE=1`
- vision and audio can submit snap-compatible observations
- `/control/api/status` exposes a compact `nmr_observation` block
- the AgentLoop remains stable
- no local SQLite writes bypass DBWriter
- no new table is required
- NMR-Lite continues to work unchanged

## 26. Final Principle

The correct architecture is:

```text
Sensor / Hook / Adapter
→ Snap-compatible NMR observation
→ Snap
→ SnapPattern
→ SnapChain
→ object_relations
→ Dream / Replay / Compression
```

This keeps ORÓMA memory-first, headless, Pi-safe and aligned with its existing architecture.

---

## 13. Operativer Beobachtungsmodus für `tools/nmr_binding_probe.py`

Nach dem ersten erfolgreichen Live-Run der Binding-Probe ist Phase 2.0a nicht mehr nur geplant, sondern messbar erreichbar. Der erste produktive Probe-Lauf zeigte:

```text
candidate_count:              296
nmr_aligned_candidate_count:  250
nmr_boosted_candidate_count:  250
weak_candidate_count:         114
strong_candidate_count:         0
max_score:                  0.637412
avg_score:                  0.42414
materialized_count:            0
```

Interpretation:

```text
ORÓMA findet wiederkehrende Co-Aktivierungs-Kandidaten.
ORÓMA kann diese Kandidaten mit NMR-Lite-Zeitfenstern ausrichten.
NMR verstärkt messbar Kandidaten.
Es gibt weak candidates, aber noch keine stabile strong relation.
Es wurde nichts materialisiert.
```

Damit ist der korrekte nächste Schritt kein aggressiver Materialisierungs-Patch, sondern ein konservativer Beobachtungsmodus.

### 13.1 Verbindliche Betriebsregel

```text
1. Probe täglich oder alle paar Stunden laufen lassen.
2. Werte beobachten.
3. Keine Materialisierung.
4. Nach einigen Tagen prüfen, ob dieselben Kandidaten wiederkommen.
```

Die zentrale Frage ist nicht, ob ein einzelner Lauf einen hohen Score erzeugt. Die zentrale Frage ist:

```text
Tauchen dieselben Kandidaten über mehrere Probe-Läufe wieder auf?
```

Nur wiederkehrende Kandidaten sind später für Dream-only-Materialisierung interessant.

### 13.2 History-Datei

Zusätzlich zur aktuellen State-Datei wird für den Beobachtungsmodus eine kompakte Verlaufsspur verwendet:

```text
Aktueller State:
  data/state/nmr_binding_probe_state.json

Verlauf über Läufe:
  data/state/nmr_binding_probe_history.jsonl
```

`nmr_binding_probe_state.json` enthält den letzten vollständigen Lauf.

`nmr_binding_probe_history.jsonl` enthält pro Lauf eine kompakte Zeile mit:

```text
ts
runtime_sec
candidate_count
nmr_aligned_candidate_count
nmr_boosted_candidate_count
nmr_sparse_window_count
nmr_missing_window_count
weak_candidate_count
strong_candidate_count
avg_score
max_score
top_candidates mit pair_id / a_ref / b_ref / score / repeat_count / decision
```

Diese History ist bewusst klein und dateibasiert. Sie ist keine neue Persistenzarchitektur und keine neue Binding-Tabelle.

### 13.3 Wiederkehr-Kennzahlen

Die Probe soll Top-Kandidaten über Läufe anhand einer stabilen `pair_id` wiedererkennen. Daraus entstehen zusätzliche Messwerte:

```text
history_seen_before_top_count
history_recurring_weak_top_count
```

Bedeutung:

```text
history_seen_before_top_count
  Anzahl der aktuellen Top-Kandidaten, die bereits in früheren Probe-Läufen vorkamen.

history_recurring_weak_top_count
  Anzahl der aktuellen Top-Kandidaten, die bereits früher vorkamen und aktuell wieder mindestens weak sind.
```

Diese Werte sind wichtiger als ein einzelner hoher `max_score`, weil sie den Übergang von zufälliger Nähe zu wiederkehrender Beziehung sichtbar machen.

### 13.4 Systemd-Beobachtungsmodus

Für regelmäßige Läufe wird ein measure-only Timer vorgesehen:

```text
systemd/oroma-nmr-binding-probe.service
systemd/oroma-nmr-binding-probe.timer
```

Der Timer soll alle paar Stunden laufen, z. B. viermal täglich. Die Service-Unit muss weiterhin garantieren:

```text
keine object_nodes-Writes
keine object_relations-Writes
keine Schema-Änderungen
DBWriter-kompatible Diagnose-Metriken
State- und History-Dateien atomisch bzw. robust schreiben
headless, ohne Qt/Wayland/X11
```

### 13.5 Akzeptanzkriterien für den Beobachtungsmodus

Ein erfolgreicher Beobachtungsmodus ist erreicht, wenn nach mehreren Läufen sichtbar ist:

```text
candidate_count > 0
nmr_aligned_candidate_count > 0
nmr_boosted_candidate_count > 0
weak_candidate_count kann > 0 sein
strong_candidate_count darf zunächst 0 bleiben
history_seen_before_top_count steigt über mehrere Läufe
history_recurring_weak_top_count wird sichtbar
materialized_count bleibt 0
```

Eine spätere Materialisierung darf erst diskutiert werden, wenn über mehrere Tage gezeigt wurde:

```text
dieselben Kandidaten kommen wieder
Scores bleiben stabil oder steigen
NMR-Fenster sind nicht sparse/missing
Kandidaten sind nicht nur Rauschen aus einem einzelnen Zeitcluster
```

---

## 14. English update – Operational observation mode

After the first successful live run of `tools/nmr_binding_probe.py`, Phase 2.0a should remain conservative. The next step is not materialization. The next step is repeated measurement.

Operational rule:

```text
1. Run the probe every few hours or daily.
2. Observe the values.
3. Do not materialize object_relations.
4. After several days, check whether the same candidates recur.
```

A single high score is not enough. The important signal is recurrence across probe runs.

The probe therefore maintains:

```text
data/state/nmr_binding_probe_state.json
data/state/nmr_binding_probe_history.jsonl
```

The history file is only a compact observation trace. It is not a new binding store and not a new persistence model. It helps answer one question:

```text
Do the same NMR-boosted candidates appear again over time?
```

Only if that becomes true should ORÓMA move toward Dream-only materialization in `object_relations`.

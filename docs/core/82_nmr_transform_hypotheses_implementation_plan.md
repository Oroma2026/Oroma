# ORÓMA – NMR Transform-Hypothesen Implementierungsplan v1.0

```text
Pfad:              docs/core/82_nmr_transform_hypotheses_implementation_plan.md
Projekt:           ORÓMA – Offline-Realtime-Organic-Memory-AI
Kurzbeschreibung:  An offline-first adaptive edge intelligence architecture
Version:           v1.0-transform-hypotheses-implementation-plan
Datum:             2026-05-28
Baseline-ZIP:      /mnt/data/oroma_20260527_212242_with_db.zip
Status:            Planungs-/Implementierungsdokument, noch kein Code-Release
Zielgruppe:        ORÓMA-Core-Entwicklung, Dream/NMR/Binding/PTZ/Replay
Sprache:           Deutsch als Hauptsprache, englische Kurzfassung am Ende

Autor/Owner:       ORÓMA-Projekt
Review-Kontext:    Aufbauend auf NMR-Lite Livevalidierung, Binding-Probe-Beobachtungsmodus,
                   Core-Reuse-Prüfung und Transform-/Fluchtlinien-Brainstorming.

Wichtige Regel:    Dieses Dokument beschreibt eine Umsetzung über vorhandene ORÓMA-Core-Bausteine.
                   Es fordert ausdrücklich keine neue Hypothesen-Datenbank, kein neues Binding-System,
                   keine parallele Observation-Atom-Persistenz und keine Live-Materialisierung.

Produktionsregel:  Alle späteren DB-Schreibpfade müssen DBWriter-kompatibel bleiben.
                   Keine lokalen SQLite-Fallbacks bei aktivem DBWriter.
```

---

## 1. Zweck dieses Dokuments

Dieses Dokument konkretisiert die nächste NMR-Stufe nach der aktuell laufenden Binding-Probe.

Der aktuelle Stand ist:

```text
NMR-Lite misst Prediction Error, EMA, Priority und Binding-Hint-Scores live.
Vision-Bridge und DBWriter-Persistenz sind validiert.
tools/nmr_binding_probe.py misst NMR-verstärkte Binding-Kandidaten read-only.
Die Probe läuft im Beobachtungsmodus per systemd-Timer und materialisiert nichts.
```

Die nächste konzeptuelle Stufe ist nicht einfach „mehr Co-Occurrence“, sondern:

```text
Snap_A kann durch einen universellen Transformationsoperator zu Snap_B erklärt werden.
```

Beispiele:

```text
translate:      Muster verschiebt sich räumlich.
scale:          Muster wird größer/kleiner.
occlude:        Muster wird teilweise verdeckt.
reappear:       Muster taucht nach kurzer Unterbrechung wieder auf.
deform/fold:    Muster verändert Form, behält aber Identität.
sequence:       Muster A folgt regelmäßig auf Muster B.
cause_effect:   Muster A erklärt Veränderung in Muster B.
```

Der Implementierungsplan führt diese Idee schrittweise ein, ohne das bestehende ORÓMA-Gedächtnismodell zu duplizieren.

---

## 2. Grundsatz: Nicht neu bauen, vorhandene Core-Bausteine verbinden

Die vollständige Core-Prüfung hat gezeigt, dass ORÓMA bereits zentrale Bausteine besitzt.

### 2.1 Pflichtanker

Diese Bausteine bilden den Hauptpfad der Transform-Hypothesen:

```text
core/hypothesis.py
  Hypothesen-Lifecycle: new_hypothesis(), update_result(), accept_hypothesis(), reject_hypothesis()

core/snappattern.py
  Ähnlichkeit/Invarianz: SnapPattern, centroid, cosine_similarity(), l2_distance(), find_similar(), quick_similarity()

core/spatial_index.py
  Räumliche Relationen: add_point(), nearest(), relate(), edges_for()

core/ptz_attention_loop.py
  PTZ-/Motion-Kontext, ptz:pan, ptz:tilt, ptz:zoom, Motion-/Aufmerksamkeitsmetriken

tools/ptz_motor_worker.py
  Motorischer PTZ-Kontext, dx/dy/action als mögliche Erklärung für Bildverschiebung

core/mutation.py
  Non-destruktive Varianten/Mutation, später als Hypothesen-Testinstrument

core/dream_worker.py
  Dream-Konsolidierung, bereits mit mutate_chain() und origin="dream/mut"

core/nmr_synaptic_plasticity.py
  Hebb-/Co-Occurrence-/Synapsenpfad, spätere Materialisierungsebene

object_nodes / object_relations
  bestehendes Relationensubstrat, keine neue Binding-Tabelle

tools/nmr_binding_probe.py
  aktueller read-only Einstieg, Kandidaten + NMR-Zeitfenster + History
```

### 2.2 Optionale Verstärker

Diese Module sind wichtig, werden aber erst später eingebunden:

```text
core/curiosity.py
  Auswahl, welche Hypothesen aktiv getestet werden sollen.

core/reward.py
  Verstärkung, wenn eine Hypothese mit Erfolg/Nutzen korreliert.

core/predictor.py
  Erwartungsprüfung: Wenn A + Operator X, wird B vorhergesagt?

core/episodic.py / core/episodic_writer.py
  Episodische Evidenz über mehrere Ereignisse/Läufe.

core/forgetting.py / core/forgetting_worker.py
  Abschwächung falscher, rauschender oder nie bestätigter Hypothesen.

core/meta_snap.py
  Langfristige Verdichtung bestätigter Transformationsmuster.

core/transfer_engine.py
  Späterer Transfer bestätigter Operatoren auf andere Domänen.

core/vision_arbiter.py / core/vision_scene_infer_hook.py
  Stabilere visuelle Evidenz, Szene-/Objekt-Kontext, Qualitätsfilter.

core/attention.py
  Priorisierung, welche Transformationshypothesen beobachtet oder getestet werden.
```

Regel:

```text
Vorhandene Core-Bausteine intelligent verbinden,
aber nicht alle vorhandenen Bausteine sofort aktivieren.
```

---

## 3. Warum Co-Occurrence nicht ausreicht

Die aktuelle Binding-Probe beantwortet:

```text
Treten A und B wiederholt zeitlich nah zusammen auf?
Wird diese Nähe durch NMR-Signal verstärkt?
```

Das ist wichtig, aber nicht ausreichend für Identität trotz Veränderung.

Beispiel:

```text
Objekt nah     → großer visueller Fingerprint
Objekt weiter  → kleinerer visueller Fingerprint
```

Reine Co-Occurrence kann daraus nur ableiten:

```text
Diese beiden Muster treten gelegentlich zusammen oder nacheinander auf.
```

Transform-Hypothesen sollen später fragen:

```text
Kann Muster A durch scale/translate/PTZ-Kontext zu Muster B erklärt werden?
Ist es möglicherweise dieselbe Entität unter veränderter Perspektive?
```

Das ist der Unterschied zwischen:

```text
Statistischem Binding     = A und B waren oft zusammen.
Transformativem Binding   = A kann durch Operator X zu B erklärt werden.
```

---

## 4. Begriff: Fluchtlinie

Im ORÓMA-Kontext bedeutet „Fluchtlinie“:

```text
Eine wiederkehrende Transformationsrichtung zwischen Beobachtungszuständen.
```

Eine Fluchtlinie ist kein einzelner Zustand. Sie ist ein Verlauf:

```text
Snap_A → Operator → Snap_B
```

Beispiel Vision/PTZ:

```text
Snap_A: Objekt groß, links im Bild
PTZ/Bewegung: Kamera schwenkt / Objekt bewegt sich
Snap_B: Objekt kleiner, weiter rechts
Hypothese: gleiche Entität unter translate/scale-Transformation
```

Der Begriff ist bewusst allgemeiner als klassische Perspektiv-Fluchtlinie. Er umfasst alle wiederkehrenden Transformationsrichtungen:

```text
räumlich, zeitlich, sensorisch, motorisch, auditiv, curriculum-basiert oder intern.
```

---

## 5. Universelle Transformationsoperatoren

Die Operatoren sind keine objekt-spezifischen Regeln. Sie sind universelle Fragen, die ORÓMA stellen kann:

```text
Hat sich dieses Muster nach Operator X verändert und ist es trotzdem dasselbe?
```

### 5.1 Operator-Katalog

Startkatalog:

```text
translate       Position verändert sich.
scale           Größe/Amplitude/Frequenzumfang verändert sich.
rotate          Orientierung verändert sich.
occlude         Muster wird teilweise verdeckt.
reappear        Muster taucht nach kurzer Unterbrechung wieder auf.
sequence        Musterfolge wiederholt sich.
deform          Form verändert sich kontinuierlich.
fold            Fläche/Struktur klappt oder faltet sich.
flow            Masse/Form verändert sich fließend.
state_change    Zustand wechselt, Identität bleibt möglich.
split           ein Muster wird zu mehreren Mustern.
merge           mehrere Muster werden zu einem Muster.
cause_effect    Muster A erklärt spätere Veränderung B.
```

### 5.2 Lernreihenfolge

Nicht alle Operatoren werden gleichzeitig eingeführt.

#### Stufe 1 — sofort messbar, PTZ-direkt

```text
translate
scale
ptz_explained_transform
```

Begründung:

```text
PTZ pan/tilt kann Bildverschiebung erklären.
PTZ zoom bzw. Distanz-/Skalierungskontext kann Größenänderung erklären.
Diese Operatoren sind am besten kausal prüfbar.
```

#### Stufe 2 — Sequenz und Sichtbarkeit

```text
sequence
occlude
reappear
```

Begründung:

```text
SnapChain und History können zeitliche Kontinuität prüfen.
```

#### Stufe 3 — deformierbare Zustände

```text
deform
fold
flow
state_change
```

Begründung:

```text
Benötigt mehr Evidenz, mehr Wiederholung und stabilere SnapPattern-/Objektkontexte.
```

#### Stufe 4 — höchste Abstraktion

```text
split
merge
cause_effect
```

Begründung:

```text
Benötigt längere Episoden, Kontext, Wiederholung, möglicherweise Reward/Predictor und Dream-Konsolidierung.
```

---

## 6. Minimaler Implementierungspfad

Der Implementierungspfad bleibt konservativ.

```text
Phase T0: Binding-Probe weiter beobachten.
Phase T1: Transform-Kandidaten nur messen, keine Hypothesen schreiben.
Phase T2: Hypothesen optional erzeugen, aber nicht akzeptieren/materialisieren.
Phase T3: Dream aktualisiert Hypothesenstatus.
Phase T4: Erst nach mehrfacher Bestätigung mögliche synaptische Relation.
```

---

## 7. Phase T0 — Beobachtung weiterlaufen lassen

Status: bereits aktiv.

Ziel:

```text
Mehrere Timer-Läufe der bestehenden Binding-Probe sammeln.
```

Zu beobachten:

```text
candidate_count
nmr_aligned_candidate_count
nmr_boosted_candidate_count
weak_candidate_count
strong_candidate_count
history_seen_before_top_count
history_recurring_weak_top_count
max_score
avg_score
```

Kein neuer Code zwingend erforderlich.

Akzeptanzkriterien:

```text
Timer läuft stabil.
History wächst.
Keine object_relations-Writes.
Keine Warnung wegen fehlender NMR-Metrikdichte.
Wiederkehrende Top-Kandidaten werden sichtbar.
```

---

## 8. Phase T1 — Transform-Kandidaten nur messen

Ziel:

```text
nmr_binding_probe.py erkennt erste Transform-Hinweise,
ohne core/hypothesis.py zu beschreiben.
```

### 8.1 Neue optionale CLI-Flags

Vorschlag:

```text
--enable-transform-probe
--transform-operators translate,scale
--min-transform-repeat 3
--ptz-window-sec 30
--transform-topk 20
```

Default:

```text
--enable-transform-probe = false
```

### 8.2 Neue State-Felder

In `data/state/nmr_binding_probe_state.json` optional ergänzen:

```json
{
  "transform_probe": {
    "enabled": false,
    "operator_counts": {
      "translate": 0,
      "scale": 0,
      "ptz_explained_transform": 0
    },
    "candidate_count": 0,
    "ptz_aligned_candidate_count": 0,
    "snappattern_similarity_candidate_count": 0,
    "top_transform_candidates": []
  }
}
```

### 8.3 Neue Metriken

Nur Diagnose-Metriken, DBWriter-kompatibel:

```text
nmr:transform_probe:candidates
nmr:transform_probe:translate_candidates
nmr:transform_probe:scale_candidates
nmr:transform_probe:ptz_explained_candidates
nmr:transform_probe:avg_score
nmr:transform_probe:max_score
nmr:transform_probe:materialized
```

`materialized` bleibt in dieser Phase immer `0`.

### 8.4 Datenquellen

Die Probe nutzt vorhandene Daten:

```text
snapchains
metrics: nmr:*, ptz:pan, ptz:tilt, ptz:zoom, cam:token:*
state: optional ptz_motor_state.json, falls vorhanden
SnapPattern / quick_similarity(), falls praktikabel und billig genug
```

Keine neuen Tabellen.

### 8.5 Scoring in T1

Ein Transform-Score darf nicht nur durch NMR hochgehen.

Vorschlag:

```text
transform_score =
  base_pair_score        * 0.30
+ snappattern_similarity * 0.25
+ operator_fit           * 0.25
+ nmr_boost              * 0.10
+ ptz_explanation        * 0.10
- noise_penalty
```

Für Stufe 1 gilt:

```text
translate/scale benötigen operator_fit oder ptz_explanation.
Kein hoher Transform-Score allein durch Co-Occurrence.
```

---

## 9. Phase T2 — Hypothesen optional erzeugen

Ziel:

```text
Wiederkehrende Transform-Kandidaten werden als prüfbare Hypothesen gespeichert.
```

Wichtig:

```text
Default bleibt aus.
```

Neue CLI-Flags:

```text
--emit-hypotheses
--hypothesis-min-score 0.60
--hypothesis-min-history-seen 2
--hypothesis-types same_object_transform,ptz_explained_transform
```

### 9.1 Verwendung von core/hypothesis.py

Vorhandene Funktionen:

```text
new_hypothesis(text, plan=..., meta=...)
update_result(hid, result)
accept_hypothesis(hid)
reject_hypothesis(hid)
list_hypotheses(status=...)
```

Beispiel-Plan:

```json
{
  "operator": "scale",
  "hypothesis_type": "same_object_transform",
  "test": "repeat_transform_candidate_across_probe_history",
  "min_confirmations": 4,
  "min_transform_score": 0.65,
  "materialize": false
}
```

Beispiel-Meta:

```json
{
  "source": "nmr_binding_probe",
  "stage": "transform_candidate",
  "a_ref": "vision/token/...",
  "b_ref": "vision/token/...",
  "a_snapchain_id": 123,
  "b_snapchain_id": 124,
  "operator": "scale",
  "ptz_explained": true,
  "nmr_boost": 0.07,
  "snappattern_similarity": 0.82,
  "history_seen_count": 3
}
```

### 9.2 Hypothesen-Lifecycle

```text
open       erstellt, aber noch nicht geprüft
running    aktiv in Dream/Probe geprüft
accepted   mehrfach bestätigt, noch nicht automatisch materialisiert
rejected   widerlegt oder zu viel Rauschen
```

Wichtig:

```text
accepted bedeutet nicht automatisch object_relation.
Materialisierung bleibt ein separater späterer Schritt.
```

---

## 10. Phase T3 — Dream aktualisiert Hypothesen

Ziel:

```text
DreamWorker prüft offene/running Transform-Hypothesen über mehrere Läufe.
```

Vorschlag:

```text
dream_worker.py bekommt später optionalen Teilabschnitt:
- list_hypotheses(status="open"/"running")
- prüfe aktuelle Probe-History
- update_result()
- bei wiederholter Bestätigung accept_hypothesis()
- bei Rauschen/fehlender Evidenz reject_hypothesis()
```

ENV-Gate:

```text
OROMA_NMR_TRANSFORM_HYPOTHESIS_DREAM_ENABLE=0/1
```

Default:

```text
0
```

---

## 11. Phase T4 — spätere Materialisierung

Ziel:

```text
Nur bestätigte Hypothesen dürfen später in object_relations/synaptic Relation einfließen.
```

Vorschlag:

```text
relation = "transform_binding"
```

oder Reuse des vorhandenen Synapsenpfads:

```text
relation = "synaptic"
notes.source = "nmr_transform_hypothesis"
notes.operator = "scale"
notes.hypothesis_id = ...
notes.stage = "accepted"
```

Noch nicht in Phase T0–T2.

Sicherheitsregel:

```text
Keine Materialisierung durch die Probe allein.
Keine Materialisierung durch Mutation allein.
Keine Materialisierung ohne Dream-Bestätigung.
```

---

## 12. Rolle von Mutation

Mutation ist vorhanden und produktiv, aber sie ist nicht der Binding-Auslöser.

Aktueller Core-Stand:

```text
core/mutation.py
  mutate_chain()
  mutate_rule()
  apply_mutations_and_persist()

core/dream_worker.py
  nutzt mutate_chain()
  speichert mutierte Ableitungen als origin="dream/mut"
```

Künftige Rolle:

```text
Mutation testet Hypothesen.
Mutation erzeugt Varianten, um zu prüfen, ob eine Transform-Hypothese robust ist.
```

Beispiel:

```text
Hypothese: Snap_A kann durch scale zu Snap_B erklärt werden.
Mutation: Erzeuge leichte scale-/feature-Varianten.
Dream: Prüfe, ob SnapPattern-Ähnlichkeit und NMR-Evidenz stabil bleiben.
```

Regel:

```text
Mutation as hypothesis test, not as binding trigger.
```

---

## 13. ENV-Gates

Alle neuen Funktionen müssen abschaltbar bleiben.

Vorschlag:

```text
OROMA_NMR_TRANSFORM_PROBE_ENABLE=0
OROMA_NMR_TRANSFORM_OPERATORS=translate,scale
OROMA_NMR_TRANSFORM_EMIT_HYPOTHESES=0
OROMA_NMR_TRANSFORM_REQUIRE_PTZ=1
OROMA_NMR_TRANSFORM_MIN_SCORE=0.60
OROMA_NMR_TRANSFORM_MIN_HISTORY_SEEN=2
OROMA_NMR_TRANSFORM_HYPOTHESIS_DREAM_ENABLE=0
OROMA_NMR_TRANSFORM_MATERIALIZE=0
```

Produktionsdefault:

```text
Probe/Planung erlaubt, Materialisierung aus.
```

---

## 14. Akzeptanzkriterien je Phase

### T1 Akzeptanz

```text
--enable-transform-probe läuft ohne Schemaänderung.
State enthält transform_probe-Block.
Metriken nmr:transform_probe:* werden geschrieben.
materialized bleibt 0.
Keine object_relations-Writes.
Keine Laufzeitblockade im AgentLoop.
Runtime bleibt kontrolliert.
```

### T2 Akzeptanz

```text
--emit-hypotheses erzeugt nur Hypothesen aus wiederkehrenden Kandidaten.
core/hypothesis.py wird wiederverwendet.
Keine neue Hypothesentabelle.
Hypothesen enthalten plan/meta mit Operator, Evidence und SnapChain-Referenzen.
Default bleibt aus.
```

### T3 Akzeptanz

```text
Dream aktualisiert Hypothesenstatus kontrolliert.
Accepted/Rejected ist nachvollziehbar.
Fehler werden sichtbar geloggt.
Keine Materialisierung.
```

### T4 Akzeptanz

```text
Nur accepted Hypothesen mit mehrfacher Dream-Bestätigung werden materialisiert.
DBWriter-only.
notes JSON enthält hypothesis_id, operator, evidence_count, source.
```

---

## 15. Risiken

### 15.1 Rauschen durch Co-Occurrence

Risiko:

```text
Zufällige Nähe wird als Transformation missverstanden.
```

Gegenmaßnahme:

```text
History-Wiederholung, NMR-Alignment, operator_fit und PTZ-Kontext verlangen.
```

### 15.2 Zu frühe Materialisierung

Risiko:

```text
object_relations werden mit falschen Transform-Bindings gefüllt.
```

Gegenmaßnahme:

```text
Materialisierung bleibt aus, bis Dream mehrfach bestätigt.
```

### 15.3 Zu komplexe Operatoren zu früh

Risiko:

```text
deform/fold/flow/split/merge erzeugen viel Rauschen.
```

Gegenmaßnahme:

```text
Nur translate/scale zuerst aktivieren.
```

### 15.4 Performance

Risiko:

```text
Vergleiche gegen zu viele SnapPatterns werden teuer.
```

Gegenmaßnahme:

```text
Top-Kandidaten begrenzen, quick_similarity bevorzugen, Dream-/Tool-Pfad statt AgentLoop.
```

---

## 16. Nicht-Ziele

Diese Dinge gehören nicht in die erste Implementierung:

```text
keine neue Hypothesen-Datenbank
keine neue Transform-Tabelle
keine Live-Materialisierung
keine AgentLoop-Blockade
kein vollwertiges Objekttracking
kein deform/fold/flow in Phase 1
kein automatisches Reward-/Policy-Coupling
```

---

## 17. Empfohlene nächste praktische Schritte

### Schritt 1 — Daten sammeln

```text
Binding-Probe-Timer einige Tage laufen lassen.
History prüfen.
Wiederkehrende Top-Kandidaten identifizieren.
```

### Schritt 2 — Transform-Probe als read-only Erweiterung

```text
tools/nmr_binding_probe.py um optionalen transform_probe-Block erweitern.
Nur translate/scale.
Keine Hypothesen schreiben.
```

### Schritt 3 — Hypothesen-Emit optional

```text
Nur für wiederkehrende Kandidaten.
Default aus.
core/hypothesis.py wiederverwenden.
```

### Schritt 4 — Dream-Test

```text
DreamWorker prüft Hypothesen über mehrere Läufe.
Accepted/Rejected, aber keine Materialisierung.
```

### Schritt 5 — Materialisierung später

```text
Nur nach stabiler Evidenz.
DBWriter-only.
object_relations oder nmr_synaptic_plasticity-Reuse.
```

---

## 18. Leitformeln

```text
Binding erklärt Zusammenhang.
Transform-Hypothesen erklären Veränderung.
Mutation testet Hypothesen.
Dream entscheidet, ob daraus Gedächtnis wird.
```

```text
Co-Occurrence bindet Ereignisse.
Transform-Hypothesen binden Identität trotz Veränderung.
Universelle Operatoren machen diese Veränderung prüfbar.
```

```text
Nicht neu bauen.
Vorhandenes verbinden.
Erst messen.
Dann Hypothese.
Dann Dream.
Dann erst Relation.
```

---

# English Summary

This document defines the implementation plan for ORÓMA's NMR Transform Hypotheses layer.

The core idea is:

```text
Snap_A can be explained as Snap_B through a universal transformation operator.
```

This is qualitatively different from ordinary co-occurrence binding.

The implementation must reuse existing ORÓMA primitives:

```text
Snap / SnapPattern / SnapChain
core/hypothesis.py
core/spatial_index.py
PTZ metrics and PTZ motor state
core/mutation.py
DreamWorker
nmr_synaptic_plasticity / object_relations
```

No new hypothesis database, no new binding graph and no new observation persistence layer should be introduced.

The first operators should be:

```text
translate
scale
ptz_explained_transform
```

because PTZ pan/tilt/zoom can provide causal evidence for image translation and scale changes.

The implementation sequence is:

```text
T0: keep the existing binding probe running and collect history
T1: add a read-only transform_probe block to nmr_binding_probe.py
T2: optionally emit hypotheses through core/hypothesis.py, default off
T3: let Dream update/accept/reject hypotheses
T4: only later materialize accepted hypotheses into object_relations/synaptic relations
```

Mutation is not a binding trigger. Mutation is a later hypothesis test instrument.

Main invariant:

```text
Do not materialize transform bindings from a single observation or a single high score.
Only memory-confirmed, repeated, Dream-validated hypotheses may become relations.
```


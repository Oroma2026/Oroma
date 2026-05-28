# ORÓMA – NMR-Transformationshypothesen und Invarianz v1.2 / NMR Transform Hypotheses and Invariance v1.2

<!--
Pfad:      /opt/ai/oroma/docs/core/81_nmr_transform_hypotheses_and_invariance.md
Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
Version:   v1.2-transform-hypotheses-core-reuse-map-2026-05-28
Datum:     2026-05-28
Autor:     ORÓMA-Projekt / Jörg Werner, redaktionell ausgearbeitet mit ChatGPT
Baseline:  /mnt/data/oroma_20260527_212242_with_db.zip

Zweck:
  Dieses Dokument beschreibt die nächste konzeptuelle Stufe nach der laufenden
  NMR-Binding-Probe: Transformationshypothesen und Invarianz. Es hält fest,
  warum reine Co-Occurrence für robustes Binding nicht ausreicht und wie ORÓMA
  vorhandene Core-Bausteine nutzt, um später aus wiederkehrenden Veränderungs-
  richtungen prüfbare Hypothesen zu bilden.

Wichtige Leitidee:
  Binding erklärt Zusammenhang.
  Transform-Hypothesen erklären Veränderung.
  Dream entscheidet, ob daraus Gedächtnis wird.

Geltungsbereich:
  - tools/nmr_binding_probe.py
  - core/mutation.py
  - core/hypothesis.py
  - core/snappattern.py
  - core/snap.py / core/snaptoken.py / core/snapchain.py
  - core/spatial_index.py
  - core/ptz_attention_loop.py
  - tools/ptz_motor_worker.py
  - core/nmr_synaptic_plasticity.py
  - core/dream_worker.py
  - core/replay_manager.py
  - core/curiosity.py / core/reward.py / core/predictor.py
  - core/episodic.py / core/episodic_writer.py
  - core/forgetting.py / core/forgetting_worker.py
  - core/meta_snap.py / core/transfer_engine.py
  - core/vision_arbiter.py / core/vision_scene_infer_hook.py
  - core/attention.py / core/ptz_motor_state.py
  - object_nodes / object_relations

Nicht-Ziele:
  - Keine neue Hypothesen-Tabelle.
  - Kein neues Observation-Atom-System.
  - Kein neues Binding-Graph-System.
  - Keine sofortige object_relations-Materialisierung.
  - Keine Live-Regel, die aus einem Einzelereignis eine stabile Bindung erzeugt.
  - Keine Implementierung in diesem Dokument. Diese Datei ist ein präziser Reuse-
    und Architekturplan für spätere, datengetriebene Erweiterungen.

Headless-/Produktionsprinzipien:
  - Pi-sicher und headless: keine Qt/Wayland/X11-Abhängigkeiten.
  - DBWriter-kompatibel: spätere Writes ausschließlich über vorhandene DBWriter-
    kompatible Pfade.
  - Measure-first: erst beobachten und messen, später materialisieren.
  - Non-destructive: keine Löschungen, keine Schema-Überraschungen, keine stillen
    Fehler.
-->

## DE

### 1. Ausgangspunkt

Mit NMR-Lite und der Binding-Probe ist ORÓMA inzwischen in der Lage,
wiederkehrende und NMR-verstärkte Kandidaten zu messen:

```text
Snap/SnapChain-Kandidaten
→ NMR-Zeitfenster-Alignment
→ binding_score
→ weak_candidate / strong_candidate
→ State, History und Diagnose-Metriken
```

Diese Stufe beantwortet die Frage:

```text
Welche Muster treten wiederholt zeitlich oder kontextuell zusammen auf?
```

Das ist notwendig, aber noch nicht ausreichend.

Robuste Wahrnehmung benötigt zusätzlich die Frage:

```text
Kann Muster A durch eine wiederkehrende Transformation zu Muster B erklärt werden?
```

Das ist der Übergang von statistischem Binding zu Transformationsverständnis.

---

### 2. Warum Co-Occurrence nicht reicht

Reine Co-Occurrence erkennt Zusammenhang:

```text
A und B treten oft zusammen auf.
```

Sie erkennt aber nicht zuverlässig Identität trotz Veränderung:

```text
Ein Objekt ist nah.
Dasselbe Objekt ist später weiter weg.
Der Fingerprint ist anders.
Trotzdem ist es dieselbe Entität.
```

Für ein simples Co-Occurrence-System können das zwei verschiedene Ereignisse sein.
Für ein Gedächtnissystem ist es eine Invarianzfrage:

```text
A und B sind nicht identisch,
aber B kann durch eine erklärbare Veränderung aus A entstehen.
```

Daraus entsteht die zentrale ORÓMA-Formel:

```text
Snap_A → Operator → Snap_B
```

Nicht nur:

```text
Snap_A ↔ Snap_B
```

---

### 3. Begriff: Fluchtlinie

Der Begriff „Fluchtlinie“ beschreibt hier keine einzelne Beobachtung, sondern eine
wiederkehrende Transformationsrichtung.

```text
Fluchtlinie = stabile Richtung, in der sich ein Muster verändert,
              während seine Identität oder Struktur teilweise erhalten bleibt.
```

Beispiele:

```text
Objekt nah       → Objekt kleiner / weiter entfernt
Objekt links     → Objekt rechts nach PTZ-Schwenk
Form frontal     → Form gedreht
Tonfolge langsam → Tonfolge schneller
Zustand A        → Zustand B unter wiederkehrendem Kontext
```

Eine Fluchtlinie ist damit stärker als ein einzelnes Binding. Sie fragt nicht nur,
ob zwei Beobachtungen zusammengehören, sondern **wie** eine Beobachtung in die andere
übergeht.

---

### 4. Universelle Transformationsoperatoren

ORÓMA sollte später keine objekt-spezifischen Spezialregeln lernen wie:

```text
Papier kann sich falten.
Wasser kann fließen.
Ein Auto wirkt kleiner, wenn es weiter weg ist.
```

Stattdessen sollte ORÓMA universelle Operatoren nutzen, die auf alle Modalitäten
angewendet werden können:

```text
translate      Position verändert sich
scale          Größe / Amplitude / Frequenzraum verändert sich
rotate         Orientierung verändert sich
sequence       A folgt regelmäßig auf B
occlude        Muster wird teilweise verdeckt
reappear       Muster taucht nach Verdecken / Lücke wieder auf
deform         Form verändert sich, Struktur bleibt teilweise erhalten
fold           Fläche / Struktur klappt oder faltet sich
flow           Kontur / Masse verändert sich kontinuierlich
state_change   Zustand ändert sich, Entität kann erhalten bleiben
split          ein Muster wird zu mehreren Mustern
merge          mehrere Muster werden zu einem Muster
cause_effect   A erklärt / verursacht Veränderung B
```

Diese Operatoren sind keine harten Regeln. Sie sind Fragen, die ORÓMA an wiederkehrende
Daten stellen kann:

```text
Hat sich dieses Muster nach Operator X verändert – und ist es trotzdem verbunden?
```

---

### 5. Lernreihenfolge der Operatoren

Die Operatoren dürfen nicht alle gleichzeitig produktiv geschaltet werden. Das würde
Rauschen erzeugen. Sie sollten nach Lernbarkeit und vorhandener Evidenz eingeführt
werden.

#### Stufe 1 – PTZ-direkt erklärbare Operatoren

```text
translate
scale
```

Warum zuerst?

ORÓMA hat aktive PTZ-Daten. Dadurch kann das System prüfen, ob Bildveränderungen durch
eigene Kamerabewegung erklärbar sind.

Vorhandene Anker:

```text
core/ptz_attention_loop.py
  - schreibt ptz:pan / ptz:tilt / ptz:zoom
  - berechnet Motion-Centroid mit dx / dy / dist / energy

 tools/ptz_motor_worker.py
  - führt PTZ-Motorik aus
  - führt dx / dy / action / mapped_action / raw_action im Motorzustand
  - schreibt/aktualisiert ptz_motor_state.json
```

Bedeutung:

```text
PTZ pan/tilt erklärt erwartbare Bildverschiebung     → translate
PTZ zoom oder Distanzveränderung erklärt Größenänderung → scale
```

Diese Klasse ist kausal stärker als reine Beobachtung, weil ORÓMA unterscheiden kann:

```text
Die Welt hat sich verändert.
```

gegen:

```text
Ich habe die Kamera bewegt, deshalb hat sich das Bild verändert.
```

Das ist der erste echte Einstieg in `ptz_explained_transform`.

#### Stufe 2 – Zeitliche Kontinuität

```text
sequence
occlude
reappear
```

Benötigt:

```text
SnapChain-Zeitverlauf
NMR-PE/EMA
wiederkehrende Kandidaten aus nmr_binding_probe_history.jsonl
```

Ziel:

```text
Ein Muster verschwindet kurz und taucht plausibel wieder auf.
Ein Muster folgt regelmäßig auf ein anderes.
```

#### Stufe 3 – Deformierbare / fluide Transformationen

```text
deform
fold
flow
state_change
```

Benötigt:

```text
mehr SnapPattern-Evidenz
längere History
mehr Kontext
Dream-Bewertung
```

Beispiele:

```text
Papier flach → Papier gefaltet
Tuch glatt → Tuch zerknüllt
Wasser ruhig → Wasser bewegt
Aufgabe einfach → Aufgabe schwerer
```

#### Stufe 4 – Höchste Abstraktion

```text
split
merge
cause_effect
```

Diese Operatoren brauchen sehr viel Evidenz, weil False Positives teuer sind.
Sie gehören erst nach stabilen Stufen 1–3 in Betracht.

---

### 6. Vorhandene ORÓMA-Bausteine

Diese Stufe darf keine neue Parallelarchitektur bauen. ORÓMA hat die zentralen
Bausteine bereits.

| Konzept | Vorhandener ORÓMA-Baustein | Rolle |
|---|---|---|
| Zustand / Beobachtung | `Snap`, `SnapToken`, snap-kompatibler Input | Einzelbeobachtung |
| Muster / Invarianz-Vorstufe | `SnapPattern` | Centroid, Ähnlichkeit, Pattern |
| Verlauf | `SnapChain` | Sequenz und Kontext |
| Co-Occurrence-Probe | `tools/nmr_binding_probe.py` | Kandidatenmessung |
| Mutation / Variation | `core/mutation.py`, `core/dream_worker.py` | späteres Testinstrument für Hypothesen, nicht Binding-Trigger |
| Hypothesen-Lifecycle | `core/hypothesis.py` | open/running/accepted/rejected |
| Explainability-Hypothesen | `core/explain.py` | UI-/Trace-nahe Erklärungsebene |
| Räumliche Relation | `core/spatial_index.py` | nearest/relate/edges_for |
| PTZ-Kausalität | `core/ptz_attention_loop.py`, `tools/ptz_motor_worker.py` | Eigenbewegung erklärt Bildänderung |
| Synaptische Materialisierung | `core/nmr_synaptic_plasticity.py`, `object_relations` | spätere bestätigte Relation |
| Konsolidierung | `core/dream_worker.py`, Replay | Akzeptieren / Verwerfen / Verdichten |

---

### 7. SnapPattern als Invarianz-Baustein

`core/snappattern.py` ist der stärkste vorhandene Baustein für Invarianz.

Relevante Funktionen und Konzepte:

```text
centroid
cosine_similarity()
l2_distance()
quick_similarity()
find_similar()
create_and_save_from_snaps()
```

Bedeutung für Transformationshypothesen:

```text
Snap_A und Snap_B müssen nicht identisch sein.
Sie können unterschiedliche Beobachtungen derselben Struktur sein,
wenn ihre Pattern-/Centroid-Ähnlichkeit trotz Variation stabil bleibt.
```

Für `scale` / `translate` bedeutet das:

```text
Wenn sich Position oder Größe ändert,
aber SnapPattern-Ähnlichkeit erhalten bleibt,
entsteht ein Kandidat für Invarianz.
```

---

### 8. SpatialIndex als räumliche Relationsebene

`core/spatial_index.py` liefert bereits:

```text
add_point()
nearest()
relate()
edges_for()
spatial_points
spatial_edges
```

Für Fluchtlinien ist wichtig:

```text
Ein Transformationskandidat hat nicht nur Feature-Ähnlichkeit,
sondern auch räumliche Beziehung.
```

Beispiele:

```text
A liegt nahe bei B.
A bewegt sich erwartbar Richtung B.
A und B sind über räumliche Kante verbunden.
```

Diese Ebene sollte später für `translate`, `occlude`, `reappear` und PTZ-erklärte
Transformationen genutzt werden.

---

### 9. Hypothesis-Lifecycle über core/hypothesis.py

`core/hypothesis.py` existiert bereits und sollte wiederverwendet werden.

Relevante API:

```text
new_hypothesis(text, plan={...}, meta={...})
update_result(hid, {score, confidence, status})
accept_hypothesis(hid)
reject_hypothesis(hid)
list_hypotheses(status=None)
```

Statusmodell:

```text
open → running → accepted / rejected
```

Für Transformationshypothesen kann `plan` den Operator und Testplan enthalten:

```json
{
  "type": "nmr_transform_hypothesis",
  "operator": "scale",
  "stage": "candidate",
  "test": "repeat_with_ptz_or_pattern_similarity"
}
```

`meta` kann Evidenz enthalten:

```json
{
  "source": "nmr_binding_probe",
  "a_ref": "vision/token/cam_token/...",
  "b_ref": "vision/token/cam_token/...",
  "a_snapchain_id": 2269023,
  "b_snapchain_id": 2269026,
  "repeat_count": 8,
  "nmr_score": 0.63,
  "operator_evidence": {
    "ptz_explained": true,
    "translation_score": 0.71,
    "scale_score": 0.42
  }
}
```

Wichtig: In der ersten Phase werden solche Hypothesen **nicht automatisch materialisiert**.
Sie bleiben prüfbare Research-Artefakte.

---

### 10. Hypothesen-Lifecycle für Transformationskandidaten

Der spätere Lifecycle sollte so aussehen:

```text
1. Binding-Probe findet wiederkehrenden Kandidaten.
2. Kandidat taucht über mehrere History-Läufe wieder auf.
3. SnapPattern-/PTZ-/Spatial-Evidenz deutet auf Operator X.
4. core/hypothesis.py erhält eine Hypothese mit status="open".
5. Weitere Probe-/Dream-Läufe aktualisieren score/confidence.
6. Bei positiver Wiederholung: status="running" mit steigender confidence.
7. DreamWorker kann akzeptieren oder verwerfen.
8. Erst akzeptierte Hypothesen dürfen später materialisiert werden.
```

Kurzform:

```text
Probe → Hypothesis → Dream → Synaptic Relation
```

---

### 11. Keine Live-Materialisierung

Transformationshypothesen dürfen nicht im Live-AgentLoop direkt zu Relations werden.

Nicht erlaubt:

```text
Ein einzelner starker Transform-Kandidat → object_relation
Ein einzelner hoher PE-Wert → accepted hypothesis
Ein einzelner PTZ-Schwenk → same_object Binding
```

Erlaubt:

```text
Kandidat messen
Operator-Evidenz notieren
Hypothese anlegen oder aktualisieren
History erhöhen
Dream später entscheiden lassen
```

---

### 12. Spätere Materialisierung

Wenn eine Transformationshypothese über mehrere Läufe und Dream-Bewertungen stabil
wird, gibt es zwei mögliche Pfade.

#### Pfad A – object_relations

```text
object_relations.relation = "transform_binding"
```

oder spezifischer:

```text
same_object_transform
ptz_explained_transform
scale_invariance
translation_invariance
state_transition
```

#### Pfad B – vorhandene synaptische Relation

Über `core/nmr_synaptic_plasticity.py` kann eine bestätigte Beziehung auch als
`synaptic` Relation mit Notes/Meta-Daten materialisiert werden.

Beispiel-Notes:

```json
{
  "source": "nmr_transform_hypothesis",
  "operator": "translate",
  "hypothesis_id": 42,
  "stage": "accepted",
  "repeat_count": 12,
  "confidence": 0.78
}
```

Welche Relation später gewählt wird, ist eine Implementierungsentscheidung. Wichtig ist:
**Materialisierung kommt nach Dream-Bestätigung, nicht davor.**

---

### 13. Erste Operatoren: translate und scale

Für ORÓMA sind `translate` und `scale` die richtigen ersten Operatoren.

#### translate

Frage:

```text
Hat sich das Muster räumlich verschoben und ist diese Verschiebung durch PTZ oder
Bewegung erklärbar?
```

Mögliche Evidenz:

```text
PTZ pan/tilt Änderung
motion centroid dx/dy
spatial_index Nähe / Kanten
SnapPattern-Ähnlichkeit trotz Positionsänderung
```

#### scale

Frage:

```text
Hat sich die Größe / Magnitude des Musters verändert, während seine Struktur ähnlich
blieb?
```

Mögliche Evidenz:

```text
PTZ zoom Änderung
Distanz-/Größeneindruck
SnapPattern-Ähnlichkeit trotz veränderter Feature-Magnitude
NMR-PE/EMA-Ausschlag im selben Zeitfenster
```

Diese beiden Operatoren sind direkt prüfbar, weil ORÓMA aktive PTZ- und Vision-Daten
hat.

---

### 14. Abgrenzung zu deformierbaren Objekten

Deformierbare oder fluide Dinge sind eine spätere Stufe.

Beispiele:

```text
Papier flach → gefaltet
Tuch glatt → zerknüllt
Wasser ruhig → fließend
Objekt ganz → teilweise verdeckt
```

Diese Fälle sind schwieriger, weil sie nicht nur Ansichtsinvarianz, sondern echte
Zustandsänderung enthalten.

Deshalb gilt:

```text
Zuerst translate / scale.
Dann sequence / occlude / reappear.
Dann deform / fold / flow / state_change.
Erst zuletzt split / merge / cause_effect.
```

---

### 15. Verhältnis zur aktuellen Binding-Probe

`tools/nmr_binding_probe.py` bleibt zunächst im Beobachtungsmodus.

Aktueller Zweck:

```text
Co-Occurrence + NMR-Boost messen
History aufbauen
wiederkehrende weak/strong Kandidaten sichtbar machen
keine Materialisierung
```

Spätere Erweiterung:

```text
wiederkehrende Kandidaten zusätzlich auf Operator-Evidenz prüfen
```

Möglicher zukünftiger Modus:

```text
--emit-transform-hypotheses
```

Dieser Modus sollte standardmäßig aus bleiben und nur dann aktiviert werden, wenn die
History zeigt, dass Kandidaten stabil wiederkehren.

---

### 16. Weitere NMR-relevante Core-Bausteine: Pflichtanker und optionale Verstärker

Der vollständige Core-Check zeigt: ORÓMA besitzt mehr Bausteine für NMR,
Transformationshypothesen und Konsolidierung, als in der ersten Fassung dieser
Datei sichtbar war. Diese Module sollen aber nicht alle gleichzeitig in den
Implementierungspfad gezogen werden. Sonst würde die nächste Stufe zu breit und
zu schwer testbar.

Die Einordnung lautet deshalb:

```text
Pflichtanker = gehören in die spätere Transform-Hypothesen-Kette.
Optionale Verstärker = dürfen später Signale liefern, sind aber kein Muss für v1.
Nicht direkt koppeln = nur beobachten/dokumentieren, nicht in den kritischen Pfad ziehen.
```

#### 16.1 Pflichtanker für die spätere Umsetzung

Diese Module bilden den Kern der späteren Transform-Hypothesen-Kette:

```text
core/hypothesis.py
  Hypothesen-Lifecycle: open/running/accepted/rejected.

core/snappattern.py
  Ähnlichkeit, Centroid, quick_similarity(), find_similar().

core/spatial_index.py
  Räumliche Nähe und Kanten: nearest(), relate(), edges_for().

core/ptz_attention_loop.py
tools/ptz_motor_worker.py
core/ptz_motor_state.py
  PTZ-Kontext für kausal erklärbare translate/scale-Transformationen.

core/mutation.py
core/dream_worker.py
  Spätere hypothesengeleitete Tests und Dream-Konsolidierung.

core/nmr_synaptic_plasticity.py
object_nodes / object_relations
  Spätere Materialisierung bestätigter Beziehungen.
```

Diese Module sind nicht optional, sobald Transform-Hypothesen wirklich umgesetzt
werden. Sie definieren den Reuse-Pfad:

```text
Binding-Probe → Hypothesis → SnapPattern/Spatial/PTZ-Evidenz
              → Mutation-Test → Dream → Synaptic Relation
```

#### 16.2 Optionale Verstärker

Diese Module sind fachlich relevant, sollen aber nur als optionale Signale
eingebunden werden, wenn die Pflichtanker stabil laufen.

```text
core/curiosity.py
  Kann später helfen zu entscheiden, welche Hypothesen aktiv weiter beobachtet
  oder getestet werden sollen. Curiosity ist ein Selektionssignal, kein
  Binding-Auslöser.

core/reward.py
  Kann bestätigte Hypothesen verstärken, wenn sie mit Erfolg, Aufgabe, Policy
  oder Nutzwert korrelieren. Reward darf aber keine einmalige Relation erzwingen.

core/predictor.py
  Transform-Hypothesen sind kleine Vorhersagen: Wenn Snap_A + Operator_X, dann
  erwartetes Snap_B. Predictor kann später Erwartungsfehler liefern.

core/episodic.py / core/episodic_writer.py
  Relevant für wiederholte Episoden und Langzeit-Evidenz über mehrere Läufe.
  Nicht als neues NMR-Format nutzen, sondern als Evidenzquelle.

core/forgetting.py / core/forgetting_worker.py
  Wichtig zum Abschwächen falscher, instabiler oder rauschender Hypothesen.
  Forgetting ist die Gegenkraft zu unkontrollierter Bindungsbildung.

core/meta_snap.py
  Langfristiges Ziel für bestätigte, verdichtete Transformationskonzepte. Erst
  nach mehrfacher Dream-Bestätigung relevant.

core/transfer_engine.py
  Kann später prüfen, ob eine Transformationsregel in andere Domänen übertragbar
  ist. Für die erste Stufe nicht erforderlich.

core/vision_arbiter.py / core/vision_scene_infer_hook.py
  Können stabilere Vision-Signale und Szenenkontext liefern. Für die erste
  Transformationsstufe bleibt aber die NMR-Bridge über CamToken/fp12 ausreichend.

core/attention.py
  Kann später Hypothesen priorisieren. Aufmerksamkeit ist ein Priorisierer, nicht
  die Hypothese selbst.
```

#### 16.3 Nicht alles gleichzeitig koppeln

Die Existenz dieser Module bedeutet nicht, dass alle sofort in Code verbunden
werden sollen. Der erste praktische Pfad bleibt bewusst klein:

```text
1. Binding-Probe weiter laufen lassen.
2. Wiederkehrende Kandidaten über History prüfen.
3. Nur translate/scale als erste Operatoren betrachten.
4. PTZ-/SnapPattern-/Spatial-Evidenz ergänzen.
5. Erst danach core/hypothesis.py anbinden.
6. Mutation/Reward/Predictor/Curiosity nur optional und später einbeziehen.
```

Damit bleibt ORÓMA Pi-sicher, messbar und nicht überladen.

Kernregel:

```text
Vorhandene Core-Bausteine intelligent verbinden,
aber nicht alle vorhandenen Bausteine sofort aktivieren.
```

---

### 17. Mutation als Hypothesen-Test, nicht als Binding-Auslöser

`core/mutation.py` ist bereits vorhanden und wird im Dream-Kontext genutzt. Mutation
muss deshalb in der Transformations-Roadmap berücksichtigt werden, aber nicht als
neues System und nicht als direkter Binding-Mechanismus.

Aktueller vorhandener Stand:

```text
core/mutation.py
  - select_rules_for_mutation()
  - mutate_weight()
  - mutate_rule()
  - apply_mutations_and_persist()
  - mutate_chain()

core/dream_worker.py
  - nutzt mutate_chain()
  - speichert mutierte Ableitungen als origin="dream/mut"
```

Die heutige Mutation ist damit bereits eine produktive, non-destruktive
Variationsschicht. Sie erzeugt Varianten, verändert Originale aber nicht destruktiv.
Für NMR-Transformationshypothesen reicht das allein noch nicht, weil Mutation aktuell
nicht weiß, **welche Hypothese** sie testen soll.

Daher gilt:

```text
Mutation erzeugt keine Bindung allein.
Mutation testet später Hypothesen.
```

Die spätere Rolle ist `hypothesis-guided mutation`:

```text
1. Binding-Probe findet wiederkehrenden Kandidaten.
2. Transform-Hypothese entsteht, z. B. operator="scale" oder operator="translate".
3. Mutation erzeugt kontrollierte Varianten um diese Hypothese herum.
4. Dream prüft, ob die Variante die Hypothese stärkt oder schwächt.
5. Erst bestätigte Hypothesen dürfen später materialisiert werden.
```

Beispiele:

```text
scale-Hypothese:
  Mutation variiert Feature-Magnitude / Pattern-Centroid leicht.
  Frage: bleibt SnapPattern-Ähnlichkeit erhalten?

translate-Hypothese:
  Mutation variiert räumliche Position / PTZ-erklärte Verschiebung.
  Frage: bleibt die Struktur unter Positionsänderung plausibel identisch?

deform-Hypothese:
  Mutation variiert Form-/Featureanteile stärker.
  Frage: bleibt genug Strukturkontinuität erhalten?
```

Mutation ist damit ein späteres Testinstrument zwischen Hypothese und Dream:

```text
Binding-Probe → Hypothesis → Mutation-Test → Dream → Synaptic Relation
```

Nicht:

```text
Mutation → Binding
```

Für den aktuellen operativen Stand bedeutet das:

```text
- Mutation bleibt aktiv im vorhandenen Dream-Pfad.
- Dieses Dokument nimmt Mutation konzeptionell auf.
- Es wird jetzt keine neue Mutation-Logik für NMR implementiert.
- Hypothesen-geführte Mutation kommt erst nach mehreren Beobachtungsläufen der
  Binding-Probe und nur mit klaren Kandidaten.
```

Diese Trennung ist wichtig, damit ORÓMA nicht zufällige Varianten mit echten
Transformationsbeziehungen verwechselt.

---
### 17. Akzeptanzkriterien für eine spätere Implementierung

Eine spätere Transform-Hypothesen-Erweiterung gilt erst dann als erfolgreich, wenn sie
folgende Kriterien erfüllt:

```text
- nutzt core/hypothesis.py, keine neue Tabelle
- nutzt SnapPattern-Ähnlichkeit, keine neue Vektorarchitektur
- nutzt PTZ-/Spatial-Kontext für translate/scale
- schreibt keine object_relations im Probe-Modus
- zählt Kandidaten, bestätigte und widerlegte Hypothesen sichtbar
- führt State/Metriken für Hypothesen-Emission
- kann vollständig deaktiviert werden
- läuft headless und Pi-sicher
```

Mögliche spätere Metriken:

```text
nmr:transform_hypothesis:candidates
nmr:transform_hypothesis:emitted
nmr:transform_hypothesis:updated
nmr:transform_hypothesis:accepted
nmr:transform_hypothesis:rejected
nmr:transform_hypothesis:translate_candidates
nmr:transform_hypothesis:scale_candidates
nmr:transform_hypothesis:ptz_explained
```

---

### 18. Risiken

#### Risiko 1 – Rauschen durch zu frühe Operatoren

Wenn zu viele Operatoren gleichzeitig getestet werden, entstehen Schein-Hypothesen.

Gegenmaßnahme:

```text
Nur translate/scale zuerst.
Andere Operatoren nur dokumentieren, nicht aktivieren.
```

#### Risiko 2 – Co-Occurrence wird fälschlich als Transformation interpretiert

Zwei Muster können gemeinsam auftreten, ohne dass A zu B transformiert.

Gegenmaßnahme:

```text
Transformation braucht zusätzliche Evidenz:
SnapPattern-Ähnlichkeit, PTZ-/Spatial-Kontext oder zeitliche Kontinuität.
```

#### Risiko 3 – Hypothesen-Tabelle wird mit Müll gefüllt

Gegenmaßnahme:

```text
Hypothesen erst nach wiederholter History-Sichtbarkeit erzeugen.
Maximale Anzahl pro Lauf begrenzen.
Keine Hypothese ohne NMR-Boost und Wiederholung.
```

#### Risiko 4 – Materialisierung zu früh

Gegenmaßnahme:

```text
Probe-Modus bleibt ohne object_relations-Writes.
Dream-only Materialisierung erst in späterer Phase.
```

---

### 19. Aktueller operativer Beschluss

Für den aktuellen Stand gilt:

```text
1. Binding-Probe weiter laufen lassen.
2. History-Daten sammeln.
3. Keine Transform-Hypothesen automatisch erzeugen.
4. Keine object_relations materialisieren.
5. Mutation nur als späteres Hypothesen-Testinstrument betrachten, nicht als Binding-Auslöser.
6. Dieses Dokument als konzeptuellen Reuse-Plan speichern.
7. Nach mehreren Beobachtungsläufen prüfen, welche Kandidaten für translate/scale
   geeignet sind.
```

---

### 20. Leitformeln

```text
Co-Occurrence bindet Ereignisse.
Transformationshypothesen binden Identität trotz Veränderung.
Dream entscheidet, ob daraus Gedächtnis wird.
```

```text
Binding wird nicht ausgelöst.
Binding wird angesammelt.
Hypothesen werden nicht geglaubt.
Hypothesen werden getestet.
```

```text
Snap_A ↔ Snap_B
= Zusammenhang.

Snap_A → Operator → Snap_B
= erklärbare Veränderung.

Snap_A →[PTZ / Spatial / Pattern]→ Snap_B
= kausal gestützte Transformationshypothese.
```

---

## EN

### 1. Purpose

This document describes the next conceptual stage after NMR-Lite and the current
NMR Binding Probe: transform hypotheses and invariance.

The current probe can detect repeated, NMR-boosted co-activation candidates. That is
necessary, but it does not yet explain identity under change.

The next question is:

```text
Can Snap_A be explained as transforming into Snap_B through a reusable operator?
```

This is qualitatively different from co-occurrence.

---

### 2. Vanishing lines / transform directions

A “vanishing line” in this document means a recurring transform direction:

```text
A stable direction in which a pattern changes while part of its identity remains.
```

Examples:

```text
near object      → smaller/farther object
left-side object → right-side object after camera pan
frontal shape    → rotated shape
slow sequence    → faster sequence
state A          → state B under recurring context
```

---

### 3. Universal transform operators

The long-term goal is not to learn object-specific rules. ORÓMA should use universal
operators:

```text
translate
scale
rotate
sequence
occlude
reappear
deform
fold
flow
state_change
split
merge
cause_effect
```

These are not hard rules. They are questions:

```text
Did this pattern change according to operator X, and is it still connected?
```

---

### 4. Learning order

Operators must be introduced by learnability, not all at once.

```text
Stage 1: translate, scale
  PTZ pan/tilt/zoom can explain image shift and apparent size change.

Stage 2: sequence, occlude, reappear
  Requires temporal continuity and SnapChain evidence.

Stage 3: deform, fold, flow, state_change
  Requires more repetition and context.

Stage 4: split, merge, cause_effect
  Highest abstraction, later only.
```

`translate` and `scale` are the correct first operators because ORÓMA has active PTZ
signals.

---

### 5. Existing ORÓMA primitives

No new architecture is required.

```text
Snap / SnapToken        observation/state
SnapPattern             similarity, centroid, invariance seed
SnapChain               sequence and context
nmr_binding_probe.py    candidate measurement
core/hypothesis.py      hypothesis lifecycle
core/spatial_index.py   spatial relations
PTZ modules             causal camera-motion context
nmr_synaptic_plasticity object_relations / synaptic materialization later
DreamWorker / Replay    consolidation
```

The later architecture should be:

```text
Binding Probe → Hypothesis → Dream → Synaptic Relation
```

not:

```text
new hypothesis database
new transform table
new binding graph
```

---

### 6. PTZ-explained transform

PTZ is the strongest first causal anchor.

A passive vision system only sees that the image changed. ORÓMA can ask:

```text
Did the image change because the world changed,
or because I moved the camera?
```

This makes `ptz_explained_transform` the first causal transform-hypothesis class.

---

### 7. Operational decision

For now:

```text
1. Keep the binding probe running.
2. Collect history.
3. Do not emit transform hypotheses automatically yet.
4. Do not materialize object_relations.
5. Use this document as a reuse plan.
6. After multiple observation runs, evaluate which recurring candidates are suitable
   for translate/scale hypothesis testing.
```

---

### 8. Additional NMR-relevant Core modules: required anchors and optional amplifiers

The full Core review shows that ORÓMA already contains more NMR-relevant building
blocks than the initial transform-hypothesis sketch exposed. They must not all be
connected at once. Otherwise the next stage would become too broad and too hard to
test.

The classification is:

```text
Required anchors = part of the future transform-hypothesis chain.
Optional amplifiers = may provide later signals, but are not required for v1.
Do not directly couple = document and observe, but keep out of the critical path.
```

Required anchors:

```text
core/hypothesis.py
  Hypothesis lifecycle: open/running/accepted/rejected.

core/snappattern.py
  Similarity, centroid, quick_similarity(), find_similar().

core/spatial_index.py
  Spatial proximity and edges: nearest(), relate(), edges_for().

core/ptz_attention_loop.py
tools/ptz_motor_worker.py
core/ptz_motor_state.py
  PTZ context for causally explainable translate/scale transforms.

core/mutation.py
core/dream_worker.py
  Future hypothesis-guided testing and Dream consolidation.

core/nmr_synaptic_plasticity.py
object_nodes / object_relations
  Future materialization of confirmed relations.
```

Optional amplifiers:

```text
core/curiosity.py
  May later select which hypotheses deserve additional observation or testing.

core/reward.py
  May reinforce hypotheses that correlate with success or utility.

core/predictor.py
  Transform hypotheses are small predictions: Snap_A + Operator_X should lead to
  an expected Snap_B.

core/episodic.py / core/episodic_writer.py
  Useful as long-term episode evidence, not as a new NMR data format.

core/forgetting.py / core/forgetting_worker.py
  Required later to weaken unstable or noisy hypotheses.

core/meta_snap.py
  Long-term target for strongly confirmed transform concepts.

core/transfer_engine.py
  May later check whether a transform rule transfers across domains.

core/vision_arbiter.py / core/vision_scene_infer_hook.py
  May provide more stable visual context.

core/attention.py
  May prioritize hypotheses, but must not create them alone.
```

The initial implementation path remains deliberately small:

```text
1. Keep the binding probe running.
2. Check recurring candidates through history.
3. Start only with translate/scale.
4. Add PTZ/SnapPattern/Spatial evidence.
5. Only then connect core/hypothesis.py.
6. Keep mutation/reward/predictor/curiosity optional and later.
```

Core rule:

```text
Reuse existing Core building blocks intelligently,
but do not activate every existing building block at once.
```

---

### 9. Mutation as hypothesis test, not as binding trigger

`core/mutation.py` already exists and is used by the Dream path. It must therefore be
part of the transform-hypothesis roadmap, but not as a separate system and not as a
direct binding mechanism.

Current existing anchors:

```text
core/mutation.py
  select_rules_for_mutation()
  mutate_weight()
  mutate_rule()
  apply_mutations_and_persist()
  mutate_chain()

core/dream_worker.py
  uses mutate_chain()
  persists mutated derivatives as origin="dream/mut"
```

The current mutation layer is a productive, non-destructive variation mechanism. It
creates variants, but it does not yet know which transform hypothesis it should test.

Core rule:

```text
Mutation does not create binding by itself.
Mutation later tests hypotheses.
```

Future role:

```text
Binding Probe → Hypothesis → Mutation Test → Dream → Synaptic Relation
```

not:

```text
Mutation → Binding
```

For example, a future `scale` hypothesis may use controlled mutation to test whether
SnapPattern similarity remains stable under small magnitude changes. A future
`translate` hypothesis may use controlled mutation to test whether spatial shift remains
consistent with PTZ context.

For now, no new NMR mutation logic should be implemented. Mutation remains documented
as a later hypothesis-test instrument after several observation runs have produced
stable candidates.

---
### 10. Core invariant

```text
Binding explains relation.
Transform hypotheses explain change.
Dream decides whether it becomes memory.
```


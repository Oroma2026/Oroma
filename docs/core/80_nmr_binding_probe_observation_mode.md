# ORÓMA – NMR Binding-Probe Beobachtungs-, History-Review- und Modalitätsklassen-Modus v1.2 / NMR Binding Probe Observation, History Review and Modality-Class Mode v1.2

<!--
Pfad:      /opt/ai/oroma/docs/core/80_nmr_binding_probe_observation_mode.md
Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
Version:   v1.2-history-review-modality-classes-2026-06-07
Datum:     2026-06-03
Autor:     ORÓMA-Projekt / Jörg Werner, redaktionell ausgearbeitet mit ChatGPT
Baseline:  /mnt/data/oroma_20260527_205026_with_db.zip

Zweck:
  Dieses Dokument hält den konservativen Betriebsmodus und die darauf folgende
  History-Review-Zwischenstufe für die NMR-Binding-Probe fest. Der Modus folgt
  direkt auf erfolgreiche Probe-Läufe mit NMR-aligned und NMR-boosted Kandidaten.
  Er beschreibt ausdrücklich keinen Materialisierungsmodus.

Geltungsbereich:
  - tools/nmr_binding_probe.py
  - systemd/oroma-nmr-binding-probe.service
  - systemd/oroma-nmr-binding-probe.timer
  - data/state/nmr_binding_probe_state.json
  - data/state/nmr_binding_probe_history.jsonl
  - nmr:binding_probe:* Metriken
  - data/state/nmr_binding_probe_review_state.json
  - nmr:binding_probe_review:* Metriken

Nicht-Ziele:
  - Keine object_relations-Materialisierung.
  - Keine object_nodes-Materialisierung.
  - Keine neuen Tabellen.
  - Keine Änderung an Snap/SnapChain/SnapPattern-Schema.
-->

## DE

### 1. Ausgangspunkt

Der erste produktive Lauf von `tools/nmr_binding_probe.py` zeigte:

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
ORÓMA kann wiederkehrende Co-Aktivierungs-Kandidaten finden.
ORÓMA kann diese Kandidaten mit lokalen NMR-Lite-Metriken ausrichten.
NMR-Lite verstärkt Kandidaten messbar.
Es gibt schwache Kandidaten.
Es gibt noch keine stabile starke Bindung.
Es wurde nichts materialisiert.
```

Damit ist Phase 2.0a als Mess-Probe erfolgreich gestartet.

---

### 2. Verbindliche Betriebsregel

Für die nächsten Tage gilt:

```text
1. Probe täglich oder alle paar Stunden laufen lassen.
2. Werte beobachten.
3. Keine Materialisierung.
4. Nach einigen Tagen prüfen, ob dieselben Kandidaten wiederkommen.
```

Der wichtige Nachweis ist nicht ein einzelner hoher Score, sondern Wiederkehr.

```text
Ein einzelner hoher Score = Hinweis.
Wiederkehrender hoher Score über mehrere Läufe = Gedächtnis-Kandidat.
```

---

### 3. Warum keine Materialisierung?

Binding soll in ORÓMA nicht als Live-Regel funktionieren.

Leitregel:

```text
Binding wird nicht ausgelöst.
Binding wird angesammelt.
Binding ist kein Einzelereignis, sondern eine vom Gedächtnis bestätigte Beziehung.
```

Daher darf die Probe aktuell nur messen.

Erlaubt:

```text
State schreiben
History schreiben
Diagnose-Metriken schreiben
Top-Kandidaten anzeigen
Wiederkehr zählen
```

Nicht erlaubt:

```text
object_relations schreiben
object_nodes schreiben
Schema ändern
stabile Bindung behaupten
Replay/Policy-Verhalten ändern
```

---

### 4. State und History

Aktueller Lauf:

```text
data/state/nmr_binding_probe_state.json
```

Verlauf:

```text
data/state/nmr_binding_probe_history.jsonl
```

Die History-Datei ist bewusst klein und dateibasiert. Sie ist keine neue Datenbank und kein neues Binding-Substrat.

Sie beantwortet nur:

```text
Sind dieselben NMR-verstärkten Kandidaten in mehreren Läufen wieder sichtbar?
```

---

### 5. Wiederkehr-Kennzahlen

Zusätzlich zu den bisherigen Countern werden Verlaufssignale geführt:

```text
history_seen_before_top_count
history_recurring_weak_top_count
```

Bedeutung:

```text
history_seen_before_top_count
  Aktuelle Top-Kandidaten, die bereits in früheren Probe-Läufen auftauchten.

history_recurring_weak_top_count
  Aktuelle Top-Kandidaten, die bereits früher auftauchten und aktuell wieder
  mindestens weak_candidate sind.
```

Diese Kennzahlen sind wichtig für spätere Dream-Materialisierung.

---

### 6. Systemd-Betriebsmodus

Die regelmäßige Probe läuft über:

```text
systemd/oroma-nmr-binding-probe.service
systemd/oroma-nmr-binding-probe.timer
```

Die Unit startet `tools/nmr_binding_probe.py` als `oroma`-User, nutzt DBWriter und schreibt:

```text
logs/nmr_binding_probe.out.log
logs/nmr_binding_probe.err.log
data/state/nmr_binding_probe_state.json
data/state/nmr_binding_probe_history.jsonl
nmr:binding_probe:* Metriken
```

Die Timer-Konfiguration ist konservativ:

```text
00:15
06:15
12:15
18:15
plus RandomizedDelaySec=20min
```

Damit entstehen vier Messpunkte pro Tag ohne AgentLoop-Blockade und ohne Materialisierung.

---

### 7. Akzeptanzkriterien nach einigen Tagen

Guter Verlauf:

```text
candidate_count bleibt > 0
nmr_aligned_candidate_count bleibt > 0
nmr_boosted_candidate_count bleibt > 0
weak_candidate_count bleibt sichtbar
history_seen_before_top_count steigt
history_recurring_weak_top_count wird sichtbar
strong_candidate_count darf weiterhin 0 sein
materialized_count bleibt 0
```

Noch kein Problem:

```text
strong_candidate_count = 0
```

Das ist am Anfang sogar erwartbar. Starke Bindungen brauchen Wiederholung.

Warnsignal:

```text
nmr_aligned_candidate_count = 0
nmr_missing_window_count sehr hoch
nmr_sparse_window_count sehr hoch
warning != null
```

Dann sind NMR-Metriken im Zeitfenster zu dünn und die Probe darf nicht still auf reine Co-Occurrence degradieren.

---

### 8. Phase 2.0b – History Review statt weiteres blindes Sammeln

Nach mehr als einer Woche Beobachtungsbetrieb ist weiteres blindes Sammeln nicht
mehr der nächste Erkenntnisschritt. Die Probe hat bewiesen:

```text
NMR-verstärkte Kandidaten existieren.
Weak Candidates wiederholen sich.
Das System bleibt konservativ.
```

Der nächste Schritt ist daher **History Review**, nicht Materialisierung.

Neue Review-Funktion:

```bash
cd /opt/ai/oroma
sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma   OROMA_DBW_ENABLE=1   OROMA_DBW_SOCKET=/opt/ai/oroma/data/state/db_writer.sock   python3 tools/nmr_binding_probe.py --review-history --verbose
```

Der Review-Modus liest ausschließlich:

```text
data/state/nmr_binding_probe_history.jsonl
```

und schreibt:

```text
data/state/nmr_binding_probe_review_state.json
nmr:binding_probe_review:* Metriken
```

Er startet **keinen neuen SnapChain-Scan** und erzeugt keine neuen Kandidaten aus
Live-Daten. Er aggregiert nur vorhandene History-Einträge.

---

### 9. Was der History Review beantwortet

Der Review-Modus beantwortet:

```text
Welche pair_id kommt über mehrere Läufe wieder?
Welche Kandidaten bleiben wiederholt weak?
Gibt es Kandidaten, die mehrfach strong waren?
Wie hoch ist der beste Score pro Kandidat?
Wie hoch ist der mittlere Score?
Wie häufig wurde ein Kandidat gesehen?
Wie stabil ist der Kandidat über mehrere Timer-Läufe?
```

Wichtige Felder:

```text
pair_count
stable_candidate_count
recurring_weak_candidate_count
recurring_strong_candidate_count
max_seen_count
max_score
max_stability_score
top_review_candidates[]
```

`stable_candidate_count` bedeutet nicht „Binding“. Es bedeutet nur:

```text
Dieser Kandidat wurde oft genug wieder gesehen, um später als Hypothesen-Kandidat
betrachtet zu werden.
```

`recurring_weak_candidate_count` ist der wichtigste Wert für den nächsten Schritt.

---

### 10. Was der History Review NICHT darf

Auch Phase 2.0b bleibt messend:

```text
Keine object_relations-Writes.
Keine object_nodes-Writes.
Keine core/hypothesis.py-Writes.
Keine Dream-Entscheidung.
Keine Replay-/Policy-Wirkung.
Keine Schwellenabsenkung, nur um strong candidates zu erzeugen.
```

Der Review-Modus erzeugt nur einen geordneten Kandidatenbericht. Erst wenn diese
Kandidaten über mehrere Review-Läufe stabil bleiben, darf Phase 2.1 diskutiert
werden:

```text
Stable weak candidate
→ Hypothesis candidate
→ core/hypothesis.py
→ Dream prüft später
```

---

### 11. Akzeptanzkriterien für Phase 2.0b

Guter Review-Verlauf:

```text
history_lines >= mehrere Timer-Läufe
pair_count > 0
stable_candidate_count > 0
recurring_weak_candidate_count > 0
materialized_count = 0
warning = null
```

Noch kein Problem:

```text
recurring_strong_candidate_count = 0
```

Das ist sogar gesund, solange `recurring_weak_candidate_count` stabil sichtbar
bleibt. ORÓMA darf nicht durch eine einzelne hohe Spitze materialisieren.

Warnsignale:

```text
history_missing_or_empty
too_few_history_runs_for_stability_review
no_stable_candidates_seen_enough_times
stable_candidates_exist_but_not_weak_enough
```

Diese Warnungen bedeuten nicht automatisch Fehler. Sie verhindern nur, dass das
System still behauptet, es gäbe stabile Kandidaten, obwohl die History dafür nicht
ausreicht.

---

### 12. Nächste Entscheidung nach Phase 2.0b

Nach dem History Review werden geprüft:

```text
Welche pair_id kommt wieder?
Welche Kandidaten bleiben weak oder werden stärker?
Gibt es stabile crossmodale Kandidaten?
Sind NMR-Fenster ausreichend dicht?
Sind die Kandidaten über mehrere Zeitcluster verteilt oder nur ein lokaler Burst?
```

Erst danach darf Phase 2.1 diskutiert werden:

```text
Hypothesis Emit Mode
```

Auch diese Phase muss standardmäßig deaktiviert sein und darf nur mit explizitem
ENV-Gate laufen.

---

### 13. Phase 2.0b.1: Review nach Modalitätsklassen

Der erste History-Review zeigte eine starke Dominanz von `audio/token ↔ audio/token`
in den Top-Kandidaten. Da der Audio-Pfad inzwischen real validiert wurde
(EMEET PIXY Capture, UI-Aufnahme/Wiedergabe und messbarer `audio_rms`-Anstieg
bei Sprache), dürfen Audio-Kandidaten nicht verworfen werden. Sie dürfen aber
auch nicht automatisch als hypothesenreif gelten.

Deshalb klassifiziert der Review-Modus Kandidaten zusätzlich nach groben
Modalitätsklassen:

```text
audio_audio
audio_vision
vision_vision
ptz_vision
audio_game
game_vision
game_internal
internal_vision
crossmodal_other
```

Diese Klassifikation ist eine Diagnose-Sicht, keine neue Persistenzstruktur und
kein semantisches Endurteil. Sie dient nur dazu, audio-dominierte Kandidaten
getrennt auszuweisen und potenziell hypothesenfähige Kandidaten sichtbar zu
machen.

Neue Review-Felder:

```text
modality_class_summary
audio_audio_candidate_count
crossmodal_candidate_count
vision_relevant_candidate_count
ptz_vision_candidate_count
game_internal_candidate_count
hypothesis_ready_candidate_count
top_hypothesis_ready_candidates
top_crossmodal_candidates
top_vision_relevant_candidates
top_audio_audio_candidates
```

Neue Metriken:

```text
nmr:binding_probe_review:audio_audio_candidates
nmr:binding_probe_review:crossmodal_candidates
nmr:binding_probe_review:vision_relevant_candidates
nmr:binding_probe_review:ptz_vision_candidates
nmr:binding_probe_review:game_internal_candidates
nmr:binding_probe_review:hypothesis_ready_candidates
```

Die Bewertung bleibt konservativ:

```text
audio_audio
  → realer Sensorpfad, aber review-only; nicht automatisch hypothesis-ready

audio_vision
  → interessant, weil crossmodal; später manuell prüfen

vision_vision
  → interessant für Invarianz / Fluchtlinien

ptz_vision
  → höchste Priorität für spätere translate/scale-Hypothesen

game/internal
  → separat prüfen, nicht mit Objekt-/Sensorbinding vermischen
```

`hypothesis_ready = true` bedeutet ausdrücklich nicht, dass `core/hypothesis.py`
geschrieben werden darf. Es ist nur ein Review-Filter für spätere manuelle oder
gegatete Phase-2.1-Entscheidungen.

Keine Änderung an der Kernregel:

```text
Keine object_relations.
Keine hypothesis-Writes.
Keine Policy-Writes.
Keine Materialisierung.
```


---

## EN

### 1. Starting point

The first productive run of `tools/nmr_binding_probe.py` produced measurable NMR-aligned and NMR-boosted binding candidates. This proves that ORÓMA can detect candidate relations, align them with local NMR-Lite metrics and score them without materializing anything.

### 2. Operational rule

```text
1. Run the probe every few hours or daily.
2. Observe the values.
3. Do not materialize.
4. After several days, check whether the same candidates recur.
```

A single high score is only a hint. Recurrence across runs is the important signal.

### 3. No materialization yet

Binding is not triggered. Binding is accumulated. It is not a single event, but a memory-confirmed relation.

The probe may write state, history and diagnostic metrics. It must not write `object_nodes`, `object_relations`, schema changes or policy/replay effects.

### 4. Files

```text
data/state/nmr_binding_probe_state.json
data/state/nmr_binding_probe_history.jsonl
systemd/oroma-nmr-binding-probe.service
systemd/oroma-nmr-binding-probe.timer
```

The history file is not a new binding store. It is a compact observation trace used to answer one question: do the same NMR-boosted candidates recur over time?

### 5. Acceptance criteria

A healthy observation phase shows candidate counts, NMR-aligned counts, NMR-boosted counts and weak candidates across repeated runs. `strong_candidate_count` may remain zero at first. `materialized_count` must remain zero.

### 6. Phase 2.0b: History Review

After several days of observation, the next step is not immediate materialization. The next step is a read-only history review:

```bash
python3 tools/nmr_binding_probe.py --review-history --verbose
```

The review mode reads `data/state/nmr_binding_probe_history.jsonl`, aggregates recurring top candidates and writes only:

```text
data/state/nmr_binding_probe_review_state.json
nmr:binding_probe_review:* metrics
```

It does not scan new SnapChains, does not create object relations, does not create hypotheses and does not change replay or policy behavior.

The important counters are:

```text
stable_candidate_count
recurring_weak_candidate_count
recurring_strong_candidate_count
max_seen_count
max_stability_score
```

A recurring weak candidate is not a binding yet. It is only a candidate that may later be considered for a gated hypothesis phase.

Only after repeated recurrence should a Hypothesis Emit Mode be considered. Dream-only materialization remains a later phase.


### 7. Phase 2.0b.1: Modality-class review

The first history review showed a strong dominance of `audio/token ↔ audio/token`
among the global top candidates. Since the audio path has been validated as a
real capture path, audio candidates must not be discarded. However, they must
not automatically become hypothesis-ready either.

The review mode therefore classifies candidates by coarse modality classes such
as `audio_audio`, `audio_vision`, `vision_vision`, `ptz_vision`, `audio_game`,
`game_vision`, `game_internal` and `crossmodal_other`.

This classification is diagnostic only. It is not a new persistence model and it
does not create hypotheses or object relations. Its purpose is to make
audio-dominated candidates visible as a separate bucket and to surface
crossmodal, vision-relevant and PTZ↔vision candidates for later manual review.

`hypothesis_ready = true` remains review-only. It does not authorize writes to
`core/hypothesis.py`, `object_relations`, policy or replay structures.

The invariant remains unchanged:

```text
Review classifies.
Dream decides later.
Nothing is materialized in Phase 2.0b.
```

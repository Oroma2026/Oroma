# ORÓMA – NMR Binding-Probe Beobachtungsmodus v1.0 / NMR Binding Probe Observation Mode v1.0

<!--
Pfad:      /opt/ai/oroma/docs/core/80_nmr_binding_probe_observation_mode.md
Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
Version:   v1.0-observation-mode-2026-05-27
Datum:     2026-05-27
Autor:     ORÓMA-Projekt / Jörg Werner, redaktionell ausgearbeitet mit ChatGPT
Baseline:  /mnt/data/oroma_20260527_205026_with_db.zip

Zweck:
  Dieses Dokument hält den konservativen Betriebsmodus für die NMR-Binding-Probe fest.
  Der Modus folgt direkt auf den ersten erfolgreichen Probe-Run mit NMR-aligned und
  NMR-boosted Kandidaten. Er beschreibt ausdrücklich keinen Materialisierungsmodus.

Geltungsbereich:
  - tools/nmr_binding_probe.py
  - systemd/oroma-nmr-binding-probe.service
  - systemd/oroma-nmr-binding-probe.timer
  - data/state/nmr_binding_probe_state.json
  - data/state/nmr_binding_probe_history.jsonl
  - nmr:binding_probe:* Metriken

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

### 8. Nächste Entscheidung nach Beobachtungsphase

Nach mehreren Tagen werden geprüft:

```text
Welche pair_id kommt wieder?
Welche Kandidaten bleiben weak oder werden stärker?
Gibt es stabile crossmodale Kandidaten?
Sind NMR-Fenster ausreichend dicht?
Sind die Kandidaten über mehrere Zeitcluster verteilt oder nur ein lokaler Burst?
```

Erst danach darf Phase 2.0b diskutiert werden:

```text
Dream-only Materialisierungs-Probe
```

Auch diese müsste zunächst sehr streng sein und weiterhin DBWriter-kompatibel arbeiten.

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

Only after repeated recurrence should a Dream-only materialization phase be considered.

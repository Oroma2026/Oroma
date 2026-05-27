# ORÓMA NMR-Lite Live-Validierung – Meilenstein 2026-05-26 / ORÓMA NMR-Lite Live Validation Milestone 2026-05-26

<!--
Pfad:      /opt/ai/oroma/docs/core/76_nmr_lite_live_validation.md
Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
Version:   v1.0-live-validation-2026-05-26
Datum:     2026-05-26
Autor:     ORÓMA-Projekt / Jörg Werner

Zweck:
  Dieses Dokument hält den erreichten Live-Meilenstein der NMR-Lite-Integration fest.
  Es dokumentiert nicht nur die Zielarchitektur, sondern die konkret geprüfte Laufzeitkette
  im echten ORÓMA-Service auf dem Raspberry-Pi/Edge-System.

Baseline:
  Autoritative Projekt-ZIP für diese Dokumentation:
    /mnt/data/oroma_20260526_133957_with_db.zip

Geprüfte Referenzdateien:
  - core/nmr_lite.py
  - core/agent_loop.py
  - core/hooks_av_snaptoken.py
  - core/hooks_audio_snaptoken.py
  - core/sql_manager.py
  - core/db_writer_client.py
  - ui/control_ui.py
  - systemd/oroma.service.d/45-nmr-lite.conf

Geltungsbereich:
  - Dokumentiert den produktiven Live-Nachweis für NMR-Lite Phase A/A.5.
  - Dokumentiert die geschlossene AgentLoop-/Sensor-/DBWriter-/Status-API-Kette.
  - Dokumentiert aktuelle Grenzen: Audio-Nutzsignal nicht bewiesen, harte Binding-/Surprise-Events
    noch nicht ausgelöst, Replay-Bonuswirkung als nächster Validierungsschritt.

Nicht-Ziele:
  - Kein Ersatz für die vollständige NMR-Lite-Spezifikation.
  - Keine Änderung an Schwellenwerten, Replay-Politik oder Binding-Heuristiken.
  - Keine Behauptung, dass alle sensorischen Modalitäten qualitativ fertig kalibriert sind.
-->

## DE

### Kurzfassung

Am **2026-05-26** wurde der NMR-Lite-Kreis in ORÓMA erstmals live und produktiv nachgewiesen.

Die Integration ist nicht mehr nur architektonisch vorhanden, sondern im echten laufenden ORÓMA-Service sichtbar:

```text
AgentLoop → _nmr_lite_hook → nmr_lite.tick()
          → nmr_lite.maybe_persist()
          → DBWriter-kompatibler Metric-Write
          → metrics-Tabelle
          → /control/api/status
```

Zusätzlich wurde der visuelle Sensorpfad angeschlossen:

```text
Kamera/CamToken → hooks_av_snaptoken.py → nmr_lite.update_vision_signal()
                → vision_fp12 / scene_change
                → NMR Prediction Error / EMA / Priority / Binding Score
```

Damit ist **NMR-Lite Phase A/A.5 live validiert**.

---

### Kontext

NMR-Lite ist in ORÓMA der leichte Realtime-Pfad für:

- Prediction Error (`nmr_pe`)
- geglätteten Prediction Error (`nmr_pe_ema`)
- Surprise-Erkennung (`nmr_surprise_event`)
- Priorisierung für Replay/Consolidation (`nmr_priority_score`)
- frühe Binding-Hinweise (`binding_hint`, `binding_hint_score`)
- crossmodale Hinweise (`crossmodal_hint`)

Das Modul ist bewusst klein gehalten. Es ersetzt nicht Replay, DreamWorker, Synapsenbildung oder SnapChain-Logik, sondern liefert einen schnellen, laufzeitnahen Hinweisstrom.

---

### Live-Baseline

Diese Dokumentation basiert auf der Projekt-ZIP:

```text
/mnt/data/oroma_20260526_133957_with_db.zip
```

Relevante Codepfade:

```text
core/nmr_lite.py
core/agent_loop.py
core/hooks_av_snaptoken.py
core/hooks_audio_snaptoken.py
core/sql_manager.py
core/db_writer_client.py
ui/control_ui.py
systemd/oroma.service.d/45-nmr-lite.conf
```

Live-System:

```text
Service:       oroma.service
Prozess:       /usr/bin/python3 -u /opt/ai/oroma/run_oroma.py
Status-API:    http://127.0.0.1:8080/control/api/status
DB:            /opt/ai/oroma/data/oroma.db
Metric-Tabelle: metrics
DBWriter:      aktiv
```

---

### Aktivierungsbedingungen

NMR-Lite wird im AgentLoop nur produktiv ausgeführt, wenn die ENV-Gates aktiv sind:

```text
OROMA_NMR_ENABLE=1
OROMA_NMR_AGENTLOOP=1
OROMA_NMR_PERSIST=1
OROMA_NMR_PERSIST_WINDOW_SEC=30
OROMA_NMR_METRIC_PREFIX=nmr
```

Im Live-System wurde bestätigt, dass diese Werte im Service-Prozess aktiv sind.

---

### Geschlossene technische Kette

#### 1. AgentLoop-Hook

`core/agent_loop.py` registriert `_nmr_lite_hook` im laufenden AgentLoop.

Live-Nachweis:

```text
NMR-Lite-Hook registriert                  ✓
NMR-Lite erster AgentLoop-Tick OK          ✓
NMR-Lite erste AgentLoop-Persistenz OK     ✓
```

#### 2. Tick und Status

`/control/api/status` exportiert den sichtbaren Block `nmr_lite`.

Live-Nachweis:

```text
running: True
tick: 15380
ticks_ok: 15379
persist_true: 253
last_error: None
```

Bewertung:

```text
AgentLoop läuft stabil        ✓
NMR-Lite wird getickt         ✓
Persistenz wird ausgeführt    ✓
keine Runtime-Fehler          ✓
```

#### 3. DBWriter-kompatible Persistenz

NMR-Lite-Metriken werden über den produktiven Schreibpfad in `metrics` persistiert.

Live-Nachweis über `oroma.db`:

```text
nmr:binding_hint        vorhanden
nmr:binding_hint_score  vorhanden
nmr:confidence          vorhanden
nmr:crossmodal_hint     vorhanden
nmr:pe                  vorhanden
nmr:pe_ema              vorhanden
nmr:priority            vorhanden
nmr:surprise            vorhanden
```

Damit ist die Persistenz nicht nur im Direktprozess, sondern im echten Service-Kontext nachgewiesen.

---

### Sensorischer Stand

#### Audio

Das System erkennt einen Audio-Kanal, aber das Nutzsignal ist aktuell nicht bewiesen.

Live-Beobachtung:

```text
audio_enabled: true
audio_degraded: false
audio_rms: ca. 0.00008
audio_pitch: ca. 3290–3350 Hz
```

Interpretation:

- Es existiert ein Capture-Device: `EMEET PIXY USB Audio`.
- Das aktuelle Signal sieht eher nach sehr leiser Eingabe, Grundrauschen oder digitalem Rauschen aus.
- Für die NMR-Lite-Architektur ist das nicht kritisch, da Modalitäten unabhängig gewichtet werden können.

#### Vision

Der visuelle Kanal wurde erfolgreich angeschlossen.

Live-Nachweis:

```text
vision_enabled: True
vision_degraded: False
vision_fp12: [12 numerische Werte]
vision_scene_change: 0.01131212554466231
snap_counter: 2
```

Beispiel für `vision_fp12` aus dem Live-System:

```text
[
  0.1486794650554657,
  0.09668511152267456,
  0.09047851711511612,
  0.06463867425918579,
  0.07985243201255798,
  0.1608344167470932,
  0.06227321922779083,
  0.05909830704331398,
  0.06331054866313934,
  0.02478949725627899,
  0.012050781399011612,
  0.009251302108168602
]
```

Interpretation:

```text
Kamera liefert Fingerprint         ✓
Vision-Fingerprint erreicht NMR    ✓
Vision ist nicht degraded          ✓
NMR kann visuelle Änderung sehen   ✓
```

---

### Prediction-Error-Nachweis

Der wichtigste Live-Nachweis ist, dass `nmr_pe` und `nmr_pe_ema` nicht mehr statisch bei 0 bleiben.

DB-Nachweis über 10 Minuten:

```text
nmr:pe      | count 19 | min 0.0      | max 0.002857 | avg 0.000351
nmr:pe_ema  | count 19 | min 0.000227 | max 0.000727 | avg 0.000323
nmr:priority| count 19 | min 0.202267 | max 0.205325 | avg 0.203282
confidence  | count 19 | min 1.0      | max 1.0      | avg 1.0
```

Bewertung:

```text
nmr_pe bewegt sich          ✓
nmr_pe_ema bewegt sich      ✓
priority reagiert           ✓
confidence steigt auf 1.0   ✓
```

Damit ist bewiesen, dass ORÓMA über NMR-Lite reale sensorische Veränderung in einen Prediction-Error-Strom übersetzt.

---

### Binding- und Surprise-Status

Aktuell noch nicht ausgelöst:

```text
binding_hint: 0
crossmodal_hint: 0
nmr_surprise_event: 0
```

Das ist korrekt und kein Fehler.

Begründung:

- `vision_scene_change` war im beobachteten Fenster klein.
- `nmr_pe` bewegt sich, aber nur im niedrigen Bereich.
- Audio ist vermutlich nur schwaches Rauschen und kein belastbarer zweiter Kanal.
- Harte Binding-/Surprise-Signale sollen nicht durch minimale Sensoränderung feuern.

Bewertung:

```text
Feine PE-Bewegung erkannt             ✓
Keine überempfindliche Surprise        ✓
Kein falsches Binding durch Rauschen   ✓
```

---

### Architektonische Bedeutung

Der erreichte Zustand ist wichtig, weil ORÓMA nun nicht mehr nur gespeicherte Episoden verarbeitet, sondern im laufenden Betrieb eine leichte Vorhersagefehler-Schicht besitzt.

Das entspricht dem Ziel von ORÓMA als:

```text
Offline-Realtime-Organic-Memory-AI
An offline-first adaptive edge intelligence architecture
```

NMR-Lite bildet dabei keine vollständige semantische Weltmodell-Schicht. Es ist ein schneller, robuster Wahrnehmungsindikator:

```text
Sensorik → Abweichung → Priorität → Hint → Replay/Dream-Verwertung
```

Damit kann ORÓMA künftig besser unterscheiden:

- Was ist vertraut?
- Was verändert sich?
- Was ist potentiell neu?
- Welche Episoden verdienen Replay-Priorität?
- Wo könnten frühe Bindungen zwischen Modalitäten entstehen?

---

### Analogie: andere Wahrnehmung statt schlechterer Wahrnehmung

Der Live-Stand bestätigt eine wichtige Architekturentscheidung:

Ein fehlender oder schwacher Kanal bedeutet nicht, dass ORÓMA insgesamt schlechter wahrnimmt. Das System kann aktive Modalitäten stärker gewichten.

Beispiel:

```text
Audio schwach / Rauschen    → geringe semantische Bedeutung
Vision aktiv                → stärkerer Realtime-Kanal
Vision-Fingerprint          → Prediction Error
Scene-Change                → Novelty-Anteil
EMA                         → Stabilisierung über Zeit
```

Damit ist ein Vision-only- oder Vision-dominanter NMR-Betrieb legitim. Das entspricht der Idee, dass ein System ohne belastbares Audio nicht blind für Veränderung ist, sondern anders bindet.

---

### Aktueller Validierungsstand

| Bereich | Status | Bewertung |
|---|---:|---|
| NMR-Lite importierbar | ✓ | Core-Modul verfügbar |
| ENV-Gates aktiv | ✓ | Service aktiviert NMR-Lite produktiv |
| AgentLoop-Hook registriert | ✓ | `_nmr_lite_hook` im Hook-Set |
| AgentLoop tickt NMR | ✓ | `ticks_ok` steigt stabil |
| Persistenz aktiv | ✓ | `persist_true` steigt |
| DBWriter-Pfad | ✓ | Metriken landen in `metrics` |
| Status-API | ✓ | `/control/api/status` zeigt `nmr_lite` |
| Audio-Kanal technisch aktiv | ✓ | Nutzsignal noch nicht bewiesen |
| Vision-Kanal aktiv | ✓ | `vision_fp12` sichtbar |
| Prediction Error bewegt sich | ✓ | `nmr_pe` und `nmr_pe_ema` bewegen sich |
| Binding-Hint hart ausgelöst | offen | aktuell korrekt 0 |
| Crossmodal-Hint hart ausgelöst | offen | Audio/Vision-Kopplung noch nicht validiert |
| Replay-Bonuswirkung | offen | nächster Validierungsschritt |

---

### Bekannte Grenzen

1. **Audio ist technisch aktiv, aber semantisch noch nicht validiert.**
   Die RMS-Werte sind extrem niedrig. Das spricht eher für Rauschen oder sehr schwachen Input.

2. **Vision ist aktiv, aber aktuell mit kleinen PE-Werten.**
   Das ist bei stabiler Szene plausibel. Für stärkere PE-Ausschläge sollte gezielt Bewegung oder Szenenwechsel provoziert werden.

3. **`binding_hint` bleibt 0.**
   Das ist bei niedriger PE und kleinem Scene-Change korrekt. Es zeigt, dass NMR-Lite nicht überempfindlich feuert.

4. **Replay-Konsum der NMR-Werte ist noch separat zu validieren.**
   Die Erzeugung und Persistenz der NMR-Werte ist bewiesen; die konkrete Replay-Bonuswirkung ist der nächste Qualitätsnachweis.

---

### Nächste Validierungsschritte

#### Schritt 1: Stärkere visuelle Änderung provozieren

Ziel:

```text
nmr_pe max steigt sichtbar
nmr_pe_ema zieht nach
binding_hint_score steigt
```

Beispiele:

- Hand vor Kamera bewegen
- Objekt ins Bild bringen und entfernen
- Lichtwechsel erzeugen
- Kamera kurz auf neue Szene richten

#### Schritt 2: Replay-Konsum prüfen

Ziel:

```text
Replay liest NMR-Metadaten
Replay-Bonus nutzt nmr_priority / binding_hint_score
priorisierte Episoden werden nachvollziehbar markiert
```

#### Schritt 3: Audio bewusst klassifizieren

Entscheidung:

```text
Audio als echter Kanal konfigurieren
oder Audio bei nur Rauschen bewusst degraded/low-weight behandeln
```

#### Schritt 4: Observability im UI konsolidieren

Sinnvolle UI-Werte:

```text
NMR enabled
Vision enabled/degraded
Audio enabled/degraded
nmr_pe
nmr_pe_ema
nmr_priority_score
binding_hint_score
binding_hint
crossmodal_hint
last_persist_ts
persist_true
last_error
```

---

### Abnahmekriterium für diesen Meilenstein

Der Meilenstein gilt als erreicht, weil alle folgenden Aussagen live belegt wurden:

```text
1. NMR-Lite läuft im echten oroma.service.
2. Der AgentLoop ruft NMR-Lite regelmäßig auf.
3. NMR-Lite persistiert über den produktiven DBWriter-kompatiblen Pfad.
4. Die Status-API zeigt den NMR-Lite-Zustand.
5. Vision-Fingerprints erreichen NMR-Lite.
6. Prediction Error und EMA bewegen sich messbar.
7. Es gibt keine NMR-Lite Runtime-Fehler im Status.
```

Damit ist **NMR-Lite Phase A/A.5 live validiert**.

---

## EN

### Summary

On **2026-05-26**, the NMR-Lite loop in ORÓMA was validated live in the real service runtime.

The validated runtime chain is:

```text
AgentLoop → _nmr_lite_hook → nmr_lite.tick()
          → nmr_lite.maybe_persist()
          → DBWriter-compatible metric write
          → metrics table
          → /control/api/status
```

The visual bridge was also validated:

```text
Camera/CamToken → hooks_av_snaptoken.py → nmr_lite.update_vision_signal()
                → vision_fp12 / scene_change
                → NMR prediction error / EMA / priority / binding score
```

This validates **NMR-Lite Phase A/A.5** as a live runtime component.

---

### Validated evidence

Live service status showed:

```text
running: True
tick: 15380
ticks_ok: 15379
persist_true: 253
last_error: None
vision_enabled: True
vision_degraded: False
vision_fp12: 12 numeric values
vision_scene_change: 0.01131212554466231
snap_counter: 2
```

Database evidence over a 10-minute window:

```text
nmr:pe      | count 19 | min 0.0      | max 0.002857 | avg 0.000351
nmr:pe_ema  | count 19 | min 0.000227 | max 0.000727 | avg 0.000323
nmr:priority| count 19 | min 0.202267 | max 0.205325 | avg 0.203282
confidence  | count 19 | min 1.0      | max 1.0      | avg 1.0
```

Interpretation:

```text
NMR-Lite ticks in the real service runtime       ✓
NMR-Lite persists through the production DB path ✓
The status API exposes NMR-Lite state            ✓
Vision reaches NMR-Lite as fp12                  ✓
Prediction error moves measurably                ✓
```

---

### Current limitations

- The audio channel is technically present, but semantic audio quality is not yet proven.
- Vision is active and produces measurable PE, but observed values are still small under stable scene conditions.
- `binding_hint`, `crossmodal_hint`, and `surprise` have not fired yet, which is expected under low novelty.
- Replay consumption of NMR values remains the next validation step.

---

### Milestone conclusion

The milestone is reached because NMR-Lite is now proven as a live ORÓMA runtime path:

```text
Sensors → NMR-Lite → Prediction Error / EMA / Priority / Hints
        → DBWriter metrics → Status/API visibility
```

This is the first confirmed productive live loop for lightweight mismatch detection and early binding prioritization in ORÓMA.

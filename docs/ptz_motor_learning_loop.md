# ORÓMA PTZ Motor Learning Loop – Architektur, Datenfluss und Betriebslogik
## Status und Zweck dieses Dokuments
Dieses Dokument beschreibt den vollständigen PTZ-Motor-Lernloop in ORÓMA auf Basis des aktuell geprüften Projektstands. Ziel ist eine saubere technische Beschreibung des mehrstufigen Lernpfads zwischen schneller Reflexmotorik, verzögerter Reward-Erzeugung, Dream-basierter Policy-Verdichtung und dem inzwischen implementierten, vorsichtig begrenzten Policy-Rückfluss in den Worker.

Wichtig:
Seit **PTZ Phase 5b** ist der Loop nicht mehr nur bis zur Offline-Verdichtung vorhanden. `tools/ptz_motor_worker.py` kann die verdichtete `ptz_motor`-Policy jetzt optional als **weichen, ENV-gesteuerten Policy-Bias** aus `policy_rules namespace='ptz_motor'` lesen. Dieser Bias ersetzt die Reflexlogik nicht, überschreibt keine Safety-Gates und wird seit Phase 5b evidenzgewichtet über `SUM(q*n)/SUM(n)` geladen.
---
## Kurzfassung
Der PTZ-Motor-Lernloop besteht aktuell aus vier logisch getrennten Schichten:
1. **Reflex / Online-Motorik**
   - schneller, DB-freier PTZ-Regelpfad
   - verarbeitet Kamera-/Target-Signale
   - schreibt State nach `ptz_motor_state.json`
2. **Reward-Erzeugung / Bewertung**
   - separater langsamer Collector
   - liest PTZ-State über Zeit
   - erzeugt Reward-Ereignisse in `rewards_log`
3. **Dream-Verdichtung / Policy-Lernen**
   - DreamWorker liest `rewards_log`
   - aggregiert Reward-Signale zu `policy_rules`
   - Fortschritt wird in `dream_state` gespeichert
4. **Policy-Rückfluss in den Worker**
   - seit PTZ Phase 5a implementiert, seit Phase 5b evidenzgewichtet
   - optionaler, read-only geladener Policy-Bias aus `policy_rules namespace='ptz_motor'`
   - standardmäßig per ENV abschaltbar und sicherheitsbegrenzt
---
## Grafisches Gesamtbild
```text
Kamera / Target / Face / Eye / Motion
              │
              ▼
   [1] PTZ Motor Worker (schnell, online)
              │
              ▼
      ptz_motor_state.json
              │
              ▼
   [2] PTZ Motor Reward Collector (langsam)
              │
              ▼
           rewards_log
              │
              ▼
   [3] DreamWorker / ptz_policy_motor
              │
              ▼
      policy_rules namespace='ptz_motor'
              │
              ▼
   [4] PTZ Phase 5b gewichteter Policy-Bias
              │
              ▼
      weicher, optionaler Rückfluss in den Worker
```

⸻

Schicht 1 – PTZ Motor Worker (Reflex, online, schnell)

Zuständigkeit

Der PTZ Motor Worker ist der schnelle Online-Pfad für PTZ-Motorik. Er verarbeitet den aktuellen Kamera-/Target-Zustand und trifft unmittelbare Bewegungsentscheidungen.

Eigenschaften

* läuft kontinuierlich als eigener Prozess bzw. eigener systemd-Dienst
* arbeitet auf Reaktionsgeschwindigkeit
* ist bewusst von der langsamen Reward-Logik getrennt
* schreibt den aktuellen Zustand in eine State-Datei
* schreibt nicht selbst die Reward-Lerneinträge in die DB

Typische Aufgaben

* Lesen von Frame-/Target-/Gesichts-/Eye-/Motion-Signalen
* Berechnung von dx, dy, Energie, Zielvertrauen, Achsenlogik
* Entscheidung über PTZ-Befehl
* Senden von PTZ-Kommandos
* Schreiben des aktuellen Motor-/Target-Zustands in:
    * data/state/ptz_motor_state.json

Warum diese Trennung wichtig ist

Der Worker darf nicht durch:

* DB-Zugriffe
* Reward-Analyse
* Dream-Verarbeitung
* History-Scans

gebremst werden. Deshalb ist er absichtlich ein schneller Reflexpfad.

⸻

Schicht 2 – Reward Collector (slow loop, getrennt vom Worker)

Zuständigkeit

Der Reward Collector ist ein separater langsamer Prozess. Er beobachtet Änderungen im ptz_motor_state.json über die Zeit und erzeugt daraus Reward-Signale.

Warum der Collector separat ist

Diese Trennung ist architektonisch sinnvoll, weil Reward-Attribution:

* zeitliche Vergleiche braucht
* nicht im heißen Motorikpfad passieren sollte
* langsamer und robuster laufen darf
* auch bei Worker-Neustarts nachvollziehbar bleiben soll

Datenquelle

* data/state/ptz_motor_state.json

Ziel

Reward-Ereignisse erzeugen und über core.utility.emit() in rewards_log schreiben.

Aktuell genutzte Reward-Typen

Im aktuellen Stand sind insbesondere diese PTZ-Motor-Reward-Quellen relevant:

* ptz_motor/center_gain
* ptz_motor/wasted_motion_penalty
* ptz_motor/target_conf_gain
* ptz_motor/cmd_fail_penalty

Semantische Bedeutung

center_gain

Positiv, wenn die Distanz zum Zielzentrum besser wird.

wasted_motion_penalty

Negativ, wenn Bewegung statt Verbesserung eher Verschlechterung verursacht.

target_conf_gain

Positiv oder negativ je nachdem, ob das Zielvertrauen steigt oder fällt.

cmd_fail_penalty

Negativ, wenn PTZ-Kommandos scheitern oder der Worker Ausführungsmisserfolge erkennt.

Technischer Pfad

Reward Collector:

* liest alten und neuen Zustand
* berechnet differenzielle Verbesserungen / Verschlechterungen
* emittiert Rewards
* DB-Schreibweg erfolgt über die vorhandene Utility-/DBWriter-Kompatibilität

Kritische Betriebserkenntnis

Der Collector ist ein eigener Prozess. Wenn er nicht läuft:

* entstehen keine neuen PTZ-Motor-Rewards
* rewards_log bleibt stehen
* Dream kann nichts Neues verdichten
* policy_rules frieren scheinbar ein

Das war im Live-Betrieb bereits ein realer Ausfallpunkt.

⸻

Schicht 3 – DreamWorker-Verdichtung (ptz_policy_motor)

Zuständigkeit

Die PTZ-Motor-Dream-Phase ist der langsame Offline-Lernpfad. Sie verdichtet Reward-Ereignisse aus rewards_log zu aggregierten Policy-Regeln.

Konkrete Phase

Im DreamWorker ist die relevante Phase:

* ptz_policy_motor

Eingangsdaten

* rewards_log
* Filter:
    * source LIKE 'ptz_motor/%'

Ziel

Schreiben aggregierter Policy-Regeln nach:

* policy_rules
* namespace='ptz_motor'

Was dort gelernt wird

Für PTZ-Motorik werden Reward-Ereignisse nicht einfach roh gespeichert, sondern verdichtet nach:

* state_hash
* action
* Reward-Mittelwerten / Nutzwerten
* Zählungen / Evidenz

Das Ergebnis ist eine komprimierte, online später nutzbare Policy-Ansammlung.

Checkpoint-Mechanismus

Wichtig:
Der Fortschritt wird nicht in einer Tabelle checkpoints gespeichert.

Im aktuellen Stand wird der Fortschritt gespeichert in:

* Tabelle: dream_state
* Key:
    * ptz_motor_policy:last_reward_id

Bedeutung dieses Checkpoints

Der Checkpoint markiert:

* bis zu welcher Reward-ID bereits verdichtet wurde

Das erlaubt inkrementelles Lernen:

* neue Rewards dazu
* alter Stand bleibt
* nur neue IDs werden nachgezogen

Betriebsbeobachtung aus dem Live-System

Im realen Betrieb war mehrfach sichtbar:

* manueller Dream-Lauf verarbeitet alte PTZ-Rewards korrekt
* policy_rules-n steigt
* dream_state_last_reward_id kann bis rewards_log_max_id aufholen

Das bestätigt:
Die Dream-Verdichtung funktioniert grundsätzlich.

⸻

Schicht 4 – Policy-Rückfluss in den Worker (PTZ Phase 5a)

Zielbild

Der Worker kann die verdichteten `policy_rules namespace='ptz_motor'` seit PTZ Phase 5a wieder als weichen Bias nutzen.

Beispielhafte Idee:

* welche Aktion war in ähnlichem Zustand historisch nützlich?
* wie stark darf ein sanfter Bias linke/rechte/obere/untere Bewegung beeinflussen?
* kein harter Ersatz der Reflexlogik
* sondern weiche, empirisch gestützte Tendenz

Aktueller Stand

Der Rückkanal ist seit PTZ Phase 5a im Code vorhanden, aber bewusst konservativ gebaut:

* `policy_rules namespace='ptz_motor'` werden read-only geladen
* der Worker erzeugt daraus einen kleinen Aktions-Bias
* die Aktivierung erfolgt über `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE`
* Default bleibt sicher: Bias ist aus, solange ENV nicht aktiviert wird
* Safety-/Reflexlogik bleibt führend

Konsequenz

Der aktuelle PTZ-Lernloop ist funktional jetzt:

* Reflex
* Reward
* Dream-Verdichtung
* optionaler Online-Policy-Bias

Die treffende Einordnung seit Phase 5a lautet:

Im aktuellen Stand ist der PTZ-Lernloop end-to-end geschlossen, aber nur als **weicher, abschaltbarer Policy-Bias**. Er ist kein harter Policy-Controller.

⸻

Orchestrator- und Dream-Betriebslogik

Grundidee

Dream läuft im aktuellen System nicht über den klassischen systemd-Timer allein, sondern im Orchestrator-Modus primär über den Orchestrator.

Entscheidender Punkt

Wenn .use_orchestrator vorhanden ist:

* klassischer oroma-dream.service ist nicht der normale Hauptpfad
* Dream wird durch den Orchestrator gestartet
* aber nur dann, wenn die Phase auf DREAM steht

Reale Konsequenz

Wenn phase.json auf DAY steht:

* Dream wird übersprungen
* neue PTZ-Rewards werden nicht automatisch verdichtet
* policy_rules wirken eingefroren

Wenn phase.json auf DREAM steht:

* Orchestrator kann core.dream_worker --interval=0 starten
* ptz_policy_motor kann neue Rewards verarbeiten

Wichtig

Damit hängt die automatische Dream-Verdichtung an zwei Dingen:

1. Reward Collector läuft
2. System kommt tatsächlich in die DREAM-Phase

⸻

Reale Live-Erkenntnisse aus dem bisherigen Betrieb

Befund 1 – Worker lief, aber Collector war zeitweise aus

Symptom:

* PTZ-Motorik lief
* keine neuen ptz_motor/...-Rewards
* rewards_log blieb auf altem Stand stehen

Ursache:

* Reward Collector lief nicht

Folge:

* kein neues Lernen trotz laufender PTZ-Motorik

Befund 2 – manueller Dream-Lauf funktionierte

Symptom:

* policy_rules-n stieg nach manuellem Dream-Lauf
* dream_state_last_reward_id holte auf

Bedeutung:

* Dream-Phase war nicht kaputt
* Problem lag nicht im Verdichtungscode selbst

Befund 3 – automatischer Dream-Lauf wurde bei phase=DAY übersprungen

Symptom:

* Orchestrator-Log enthielt:
    * dream skipped: phase=DAY

Bedeutung:

* automatische Verdichtung konnte in dieser Phase gar nicht anlaufen

Befund 4 – bei phase=DREAM startete Dream automatisch

Symptom:

* Orchestrator-Log enthielt:
    * CMD: /usr/bin/python3 -m core.dream_worker --interval=0

Bedeutung:

* Orchestrator-Dream-Pfad funktioniert grundsätzlich
* DAY/NIGHT-Gating ist betrieblich relevant

Befund 5 – nach systemd-Integration des Collectors kamen neue Rewards wieder an

Symptom:

* neue ptz_motor/target_conf_gain-Einträge in rewards_log
* aktuelle Zeitstempel
* Collector-Prozess aktiv
* Collector-Log startet sauber

Bedeutung:

* der zentrale Reward-Erzeugungspfad lebt wieder
* der Lernloop ist bis Schicht 2 wieder offen
* Dream kann darauf wieder aufsetzen

⸻

Betriebsarchitektur im aktuellen Stand

Laufende Komponenten

PTZ Motor Worker

* schneller Dauerprozess
* Online-Motorik
* State-Erzeugung

PTZ Motor Reward Collector

* separater langsamer Prozess
* Reward-Erzeugung aus State-Verläufen

DreamWorker

* offline / Dream-Phase
* Verdichtung von Rewards zu policy_rules

Datenfluss

1. Worker schreibt ptz_motor_state.json
2. Collector liest den State über Zeit
3. Collector schreibt ptz_motor/...-Rewards in rewards_log
4. DreamWorker verarbeitet neue Reward-IDs
5. DreamWorker schreibt policy_rules namespace='ptz_motor'
6. Checkpoint wandert in dream_state
7. Worker kann `policy_rules namespace='ptz_motor'` als optionalen Policy-Bias zurücklesen

⸻

Tabellen und Zustandsobjekte

ptz_motor_state.json

Flüchtige, aber zentrale Online-State-Datei des Workers.

rewards_log

Langsame Reward-History.
Hier landen alle Collector-basierten Reward-Ereignisse.

policy_rules

Verdichtete, aggregierte Lernregeln.
Für PTZ-Motorik relevant:

* namespace='ptz_motor'

dream_state

Inkrementelle Dream-Fortschrittsverwaltung.
Relevanter Key:

* ptz_motor_policy:last_reward_id

⸻

Woran man erkennt, dass der Loop funktioniert

Worker läuft

* systemctl status oroma-ptz-motor-worker.service

Collector läuft

* ps aux | grep ptz_motor_reward_collector
* oder systemd-Status, wenn als Service integriert

Neue Rewards entstehen

SQL:

SELECT id, source, reward, datetime(created_at,'unixepoch','localtime')
FROM rewards_log
WHERE source LIKE 'ptz_motor/%'
ORDER BY id DESC
LIMIT 20;

Dream-Verdichtung arbeitet nach

SQL:

SELECT 'dream_state_last_reward_id', value
FROM dream_state
WHERE key='ptz_motor_policy:last_reward_id'
UNION ALL
SELECT 'rewards_log_max_id', COALESCE(MAX(id),0)
FROM rewards_log
WHERE source LIKE 'ptz_motor/%';

Interpretation:

* wenn rewards_log_max_id > dream_state_last_reward_id, gibt es offenen Lernrückstand
* wenn beide gleich sind, ist PTZ-Motor-Dream aktuell aufgeholt

Policy wächst

SQL:

SELECT
  action,
  SUM(n) AS total_n,
  ROUND(SUM(q*n) / NULLIF(SUM(n),0),6) AS weighted_q,
  ROUND(AVG(q),6) AS plain_avg_q,
  ROUND(MIN(q),6) AS min_q,
  ROUND(MAX(q),6) AS max_q,
  COUNT(*) AS rule_count,
  datetime(MAX(last_ts),'unixepoch','localtime') AS last_seen
FROM policy_rules
WHERE namespace='ptz_motor'
  AND n >= 3
GROUP BY action
HAVING SUM(n) >= 10
ORDER BY weighted_q DESC;

⸻

Was der Lernloop aktuell bewusst begrenzt

Kein harter Online-Policy-Controller

Die verdichtete `ptz_motor`-Policy darf den Worker seit PTZ Phase 5a nur als weicher Bias beeinflussen. Sie ersetzt nicht die Reflexlogik.

End-to-End-Adaptionskreis mit Sicherheitsbegrenzung

Es gibt jetzt:

* Reward-Erzeugung
* Dream-Verdichtung
* optionalen Policy-Bias in der Online-Motorik

aber weiterhin keinen:

* direkten Policy-Zwang
* Überschreiben von Safety-Gates
* DB-Schreibzugriff im Hot-Loop

Reward-Verteilung derzeit noch schmal

Im zuletzt beobachteten Live-Betrieb kamen besonders wieder:

* ptz_motor/target_conf_gain

Während andere Reward-Typen zeitweise seltener oder gar nicht auftauchten:

* center_gain
* wasted_motion_penalty
* cmd_fail_penalty

Das muss nicht falsch sein, ist aber für die spätere Qualität des Lernens relevant.

⸻

Fachliche Einordnung

Der PTZ-Motor-Lernloop ist ein gutes Beispiel für mehrskaliges Lernen:

* schnelle Zeitskala: Reflexmotorik
* mittlere Zeitskala: Reward-Erzeugung aus State-Verläufen
* langsame Zeitskala: Dream-basierte Policy-Konsolidierung

Diese Architektur trennt:

* Reaktion
* Bewertung
* Lernen

bewusst voneinander.

Das ist stabiler als ein monolithischer Online-Loop, in dem alles gleichzeitig passiert.

⸻

Präzise Einordnung des aktuellen Reifegrads

Die treffendste Formulierung für den Stand **2026-06-07** ist:

Der ORÓMA PTZ-Motor-Lernloop ist bis zur Dream-Verdichtung und bis zum optionalen Online-Rückkanal technisch geschlossen. Online-Reflex, separate Reward-Erzeugung, Dream-basierte Policy-Verdichtung und ein read-only geladener, evidenzgewichteter Policy-Bias sind implementiert und im Live-Betrieb nachweisbar.

Wichtig bleibt die Sicherheitsgrenze:

* Der Policy-Bias ist kein harter Controller.
* Der Standarddienst startet weiterhin mit deaktiviertem Bias.
* Safety-/Reflex-Gates bleiben führend.
* Eine dauerhafte Aktivierung erfolgt erst nach längerer Stabilitätsbeobachtung.

Oder kürzer:

Der PTZ-Lernloop ist aktuell ein **4-stufiger, sicherheitsbegrenzter Loop**: Reflex, Reward, Dream-Verdichtung und optionaler gewichteter Policy-Bias sind real; der dauerhafte Produktiv-Bias bleibt bewusst ausgeschaltet.

⸻

Live-validierter Fix-Stand 2026-06-06/2026-06-07

### Patch 1 – DreamWorker-Checkpoint

Problem:

* `sql_manager.get_conn()` liefert Dict-Rows.
* `core/dream_worker.py` las den Checkpoint mit `row[0]`.
* Dadurch konnte `ptz_motor_policy:last_reward_id` still falsch gelesen werden und der DreamWorker wiederholt alte Fenster verarbeiten.

Fix:

* Dict-/Tuple-sicherer Checkpoint-Leser.
* sichtbares Logging mit `start_id`, `fetched`, `updated`, `skipped`, `no_action`, `last_id`.

Live-Nachweis:

```text
checkpoint wanderte von 1224928 bis 1434710
max ptz_motor reward id: 1434710
backlog: 0
policy_rules namespace='ptz_motor': 523+
```

### Patch 2 – Reward Collector `policy_action`-Guard

Problem:

* Beobachtungsrewards wie `ptz_motor/target_conf_gain` konnten eine `policy_action` tragen.
* Dadurch hätten reine Wahrnehmungsänderungen fälschlich zu Motor-Policy-Regeln werden können.

Fix:

* `policy_action` wird nur noch bei motorisch zuordenbaren Rewards behalten:
  * `ptz_motor/center_gain`
  * `ptz_motor/wasted_motion_penalty`
* Beobachtungs- und Diagnose-Rewards verlieren vor dem Emit Aktionsfelder.

Live-Nachweis:

```text
with_policy_action: 19
motor_with_action: 19
unexpected_non_motor_with_action: []
```

### Patch 3 – Worker Weighted Policy Bias

Problem:

* `AVG(q)` bewertet Einzelregeln mit `n=1` genauso stark wie belastbare Regeln mit hohem `n`.

Fix:

* Worker-Aggregation auf `SUM(q*n)/SUM(n)` umgestellt.
* Mindestfilter:
  * `n >= 3`
  * `SUM(n) >= 10`
* Bias bleibt per Default aus.

Live-Nachweis gewichteter Kandidaten:

```text
up     total_n=433  weighted_q=0.061893  → Gate 0.05 bestanden
down   total_n=159  weighted_q=0.054167  → Gate 0.05 bestanden
right  total_n=87   weighted_q=0.007183  → Gate nicht bestanden
left   total_n=79   weighted_q=0.004254  → Gate nicht bestanden
```

Temporärer Bias-Test:

```text
policy_bias_enabled: True
policy_bias_active:  True
policy_bias:         {'down': 0.002167, 'up': 0.002476}
aggregation:         weighted_q=sum(q*n)/sum(n)
```

Der Test bestätigt den Rückkanal, aber die dauerhafte Aktivierung bleibt aus.

⸻

Schlussfazit

Der PTZ-Motor-Lernloop in ORÓMA ist architektonisch sauber in mehrere Zeitskalen getrennt:

* schnelle Motorik
* separate Reward-Attribution
* Dream-basierte Policy-Konsolidierung
* optionaler, gewichteter und abschaltbarer Policy-Bias

Die reale Betriebsanalyse zeigt:

* Worker kann stabil laufen
* Collector erzeugt motorisch verwertbare Rewards
* DreamWorker verarbeitet inkrementell und holt Backlog auf 0
* `policy_rules namespace='ptz_motor'` wachsen
* der gewichtete Rückkanal funktioniert im temporären Test

Damit ist der Loop nicht mehr nur Reflex + Offline-Lernen, sondern ein kontrolliert geschlossener Lernkreis mit bewusst deaktivierter Dauer-Rückkopplung.

---

## PTZ Phase 5a/5b – implementierter und gewichteter Policy-Bias-Rückfluss

**Stand:** 2026-06-07  
**Status:** Code-Pfad, gewichtete Aggregation und temporärer Live-Bias-Test erfolgreich validiert; dauerhafte Aktivierung bleibt aus.

### Zweck

Phase 5a schloss den bisher offenen Rückkanal zwischen Dream-verdichteter PTZ-Motor-Policy und Online-Worker. Phase 5b macht diesen Rückkanal statistisch belastbarer: Der Worker darf die gelernte Policy weiterhin nur als kleinen Bias verwenden, lädt Action-Kandidaten aber evidenzgewichtet über `SUM(q*n)/SUM(n)`.

```text
rewards_log ptz_motor/*
→ DreamWorker
→ policy_rules namespace='ptz_motor'
→ tools/ptz_motor_worker.py
→ optionaler Aktions-Bias
```

### Aktivierungsmodell

Der Bias ist ENV-gesteuert und sicher deaktivierbar:

```text
OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=0|1
OROMA_PTZ_MOTOR_POLICY_NS=ptz_motor
OROMA_PTZ_MOTOR_POLICY_BIAS_WEIGHT=0.08
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_N=10
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_RULE_N=3
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_ABS_Q=0.05
OROMA_PTZ_MOTOR_POLICY_BIAS_REFRESH_SEC=60
OROMA_PTZ_MOTOR_POLICY_BIAS_MAX_ABS=0.20
```

### Sicherheitsregeln

Der Policy-Bias darf nicht überschreiben:

```text
deadzone
energy_low
micro_guard
cooldown
axis_lock
reversal_guard
cmd_fail handling
max-step / PTZ command safety
```

Er darf nur dort helfen, wo der Worker ohnehin eine plausible Bewegung erwägt. Die Reflexlogik bleibt primär.

### Live-Tests

Der erste Starttest nach Phase 5a war erfolgreich:

```text
python3 -m py_compile tools/ptz_motor_worker.py  → OK
oroma-ptz-motor-worker.service                  → active (running)
fail                                            → 0
ok                                              → steigend
```

Im Startlog wird die neue Policy-Konfiguration sichtbar:

```text
policy_bias=0
policy_ns=ptz_motor
policy_w=0.080
policy_min_total_n=10
policy_min_rule_n=3
policy_min_q=0.050
policy_refresh=60.0s
```

Damit war bestätigt: Der Codepfad startet stabil. Der spätere Phase-5b-Test bestätigte zusätzlich die gewichtete Aggregation und das Gate-Verhalten: `up` und `down` wurden als schwache Bias-Kandidaten geladen, `left` und `right` blieben wegen zu kleinem `weighted_q` außen vor. Die Daueraktivierung bleibt weiterhin aus.

### Erfolgskriterium für Aktivierung

Ein aktiver Test mit `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=1` gilt nur dann als erfolgreich, wenn:

```text
Worker bleibt active (running)
fail bleibt 0
moves steigen nicht unkontrolliert
State/Log zeigt policy_bias_* sichtbar
wasted_motion_penalty sinkt oder bleibt stabil
target_stability / center_gain verbessern sich über längere Beobachtung
```


## Stand 2026-06-13 – Positive Position Marker / Stage-A Evidence

- `tools/ptz_motor_worker.py` speichert konservative Positive Position Marker in `data/state/ptz_positive_position_markers.json`.
- v1.6d blockiert `motion_diff_upper` und Motion-only-Kandidaten standardmäßig (`OROMA_PTZ_MOTOR_POS_MARKER_ALLOW_MOTION_ONLY=0`, `OROMA_PTZ_MOTOR_POS_MARKER_ALLOW_UPPER_MOTION=0`).
- Der Marker ist reine Evidenz: keine Identität, keine automatische Steuerung, keine direkte Policy-Materialisierung.
- Ceiling-Recovery schützt gegen langes Decken-/Leerlauf-Schauen, ist aber durch Start-Grace und Cooldown rate-limitiert.
- `tools/ptz_positive_position_probe.py` ist die Stage-A-Messbrücke: Marker zählen, Top-Zellen und Guard-Status sichtbar machen und optional via DBWriter nach `stats.db.stats_points` schreiben.
- Produktiver Bias bleibt aus, bis Learning-Evidence eindeutig positive Wirkung belegt.

### Stage-A Evidence Timer (P1b, Stand 2026-06-13)

Die Positive-Position-Probe kann jetzt regelmäßig als systemd-Timer laufen:

```text
systemd/oroma-ptz-positive-position-probe.service
systemd/oroma-ptz-positive-position-probe.timer
```

Der Timer führt alle 5 Minuten aus:

```text
tools/ptz_positive_position_probe.py --once --write-stats --verbose
```

Architekturregeln:

- reine Stage-A-Messung; keine PTZ-Motorsteuerung
- keine Policy-Aktivierung
- keine Materialisierung in `object_nodes` oder `object_relations`
- Stats-Writes nur über DBWriter (`OROMA_DBW_ENABLE=1`)
- Verlaufsspur in `stats.db.stats_points` über `ptz.marker.*`-Serien

Damit werden wiederkehrende interessante PTZ-Bildpositionen nicht sofort als
Verhalten genutzt, sondern zuerst über Zeit messbar gemacht. Das entspricht der
Core-Regel: messen → Evidenz sammeln → später Dream/Binding entscheiden lassen.


## P2 – Evidence Report für Positive Position Marker (Stand 2026-06-13)

Mit `tools/ptz_positive_position_evidence_report.py` ist der Positive-Position-Marker-Pfad nicht nur messend persistent, sondern auch read-only auswertbar. Das Tool analysiert die vom Probe-Timer erzeugten `ptz.marker.*`-Serien in `stats.db.stats_points`.

Es beantwortet unter anderem:

- wächst `positive_count`?
- entsteht `repeat_ge_5`?
- bleibt `motion_guard_blocked` hoch?
- wird `ceiling_active` seltener?
- bleibt ein `top_key` stabil oder springt die Aufmerksamkeit?

Wichtig: Diese Auswertung steuert nichts. Sie ist Stage-A Evidence, keine Policy und keine Materialisierung.

```bash
cd /opt/ai/oroma; python3 tools/ptz_positive_position_evidence_report.py --text --verbose
```

## P2.5 – PTZ Policy Atomicity / DBWriter Hygiene (Stand 2026-06-14)

Dieser Stand ist ein reiner Stabilitäts- und Wartbarkeits-Patch. Die Lernsemantik wird bewusst nicht vereinheitlicht:

- `universal_policy.learn_many()` nutzt weiterhin eine diskrete Bilanz-Semantik für Spiel-/Universal-Namespace-Regeln: `q=(pos-neg)/n`.
- PTZ-Policy-Namespace(s) behalten die kontinuierliche Reward-Mittelwert-Semantik: `q=((q*n)+r)/(n+1)`.
- Im DBWriter-Betrieb werden die PTZ-Policy-Updates jetzt als atomare DBWriter-Transaction ausgeführt: `INSERT OR IGNORE` und `UPDATE` bleiben logisch identisch, laufen aber nicht mehr als zwei getrennte Queue-Operationen.
- In `core/sql_manager.py` wurde eine tote doppelte `_dbw_enabled()`-Definition entfernt; die aktive ENV-basierte Implementierung bleibt erhalten.
- Keine Policy-Bias-Aktivierung, keine Motorsteuerung, keine Materialisierung und kein lokaler SQLite-Fallback wurden hinzugefügt.

## P2.6 – Idempotente Stage-A Stats-Writes (Stand 2026-06-16)

Live-Befund nach mehrtägigem Timer-Betrieb: Die PTZ Positive Position Evidence
wuchs fachlich korrekt (`positive_count=19`, `repeat_ge_5=17`, stabile Top-Zelle
`g6:x2:y3`), aber einzelne Wiederholungsläufe konnten in `stats_points` einen
`UNIQUE constraint failed: stats_points.src_table, stats_points.src_uid, stats_points.series`
auslösen.

Korrektur: `tools/ptz_positive_position_probe.py` und
`tools/synapses_bridge_probe.py` schreiben ihre Measure-only-Stats weiterhin
ausschließlich via DBWriter, aber nun idempotent per `ON CONFLICT ... DO UPDATE`.
Das ändert keine Lernsemantik, keine Markerlogik, keine Motorsteuerung und keine
Materialisierung. Es verhindert nur, dass doppelte Sekunden-Snapshots oder
Orchestrator-Wiederholungsläufe als harte Fehler im Timer/DBWriter erscheinen.


## P3a – Regional Temporal Motion Signature Evidence (Stand 2026-06-16)

P3a ist keine neue Steuerung, sondern ein zusätzlicher Stage-A-Evidence-Pfad. Der bisherige Motor-Policy-Lernloop bleibt unverändert. Der Positive-Position-Marker-Pfad bleibt Eye/Face-gated. P3a misst ausschließlich, ob andere interessante Bewegungen – insbesondere kleine, gerichtete Bewegungsobjekte wie Menschen/Autos auf der Straße – vom System getrennt von TV-/Display-/Fensterartefakten erkannt werden können.

Getrennte Zeitskalen:

- kurzfristig pro Lauf: mehrere Frames/Samples in kurzer Folge, Default 12 × 0,35 s
- langfristig im State: langsame EMA-Baseline pro Rasterzelle

Getrennte Klassen:

```text
structured_blob_motion
fixed_fast_change_region
fixed_low_change_display_region
dark_static_region
slow_drift_region
```

Wichtige Invariante: P3a schreibt nur `ptz.motion.*`-Stats und bewegt keinen Motor. Erst wenn über mehrere Tageszeiten stabil gezeigt wird, dass `structured_blob_motion` von `fixed_fast_change_region` und `fixed_low_change_display_region` getrennt bleibt, darf später ein Dream-Candidate diskutiert werden.

## P3z0 – Zoom Context vor ViewMap Sweep (Stand 2026-06-16)

Der PTZ-Lernpfad priorisiert nach dem Live-Befund zuerst die billigste Hypothese: zu enger Zoom. Bei Zoom 130 fand der Motor-Worker keinen qualifizierten Zielvektor (`deadzone`, `confidence=0.0`), während bei Zoom 100 Außen-/Straßenkontext sichtbar wurde. P3z0 prüft diesen Effekt wiederholt und measure-only, bevor P3b0/P3b1 mit Pan/Tilt-Sweep oder Anchor-Curiosity gebaut werden.

Entscheidungsregel: Ein Einzellauf reicht nicht. `ptz.zoom_context.wide_helpful_sample`, `ptz.zoom_context.score.delta` und `ptz.zoom_context.structured.delta` müssen über mehrere Fenster (1h/6h/24h) betrachtet werden. Wenn Wide-Zoom häufig hilfreich ist, ist zuerst eine einfache Zoom-Policy sinnvoll: Suche/Orientierung bei Zoom 100, Detailbeobachtung erst nach Treffer. Wenn Wide-Zoom nicht reicht, folgt der begrenzte PTZ-ViewMap-Sweep mit Boundary-Erkennung.

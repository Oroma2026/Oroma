# ORÓMA PTZ Motor Learning Loop – Architektur, Datenfluss und Betriebslogik
## Status und Zweck dieses Dokuments
Dieses Dokument beschreibt den vollständigen PTZ-Motor-Lernloop in ORÓMA auf Basis des aktuell geprüften Projektstands. Ziel ist eine saubere technische Beschreibung des mehrstufigen Lernpfads zwischen schneller Reflexmotorik, verzögerter Reward-Erzeugung, Dream-basierter Policy-Verdichtung und einem späteren geplanten Policy-Rückfluss in den Worker.
Wichtig:
Der Loop ist im aktuellen Stand bis zur Offline-Verdichtung funktional vorhanden. Der Rückfluss der verdichteten `ptz_motor`-Policy in den Online-Worker ist derzeit noch nicht aktiv.
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
   - konzeptionell vorgesehen
   - im aktuellen Stand noch nicht aktiv
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
   [4] geplanter zukünftiger Policy-Bias
              │
              ▼
      weicher Rückfluss in den Worker

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

Schicht 4 – geplanter Policy-Rückfluss in den Worker

Zielbild

Langfristig soll der Worker die verdichteten policy_rules namespace='ptz_motor' wieder als weichen Bias nutzen.

Beispielhafte Idee:

* welche Aktion war in ähnlichem Zustand historisch nützlich?
* wie stark darf ein sanfter Bias linke/rechte/obere/untere Bewegung beeinflussen?
* kein harter Ersatz der Reflexlogik
* sondern weiche, empirisch gestützte Tendenz

Aktueller Stand

Dieser Rückkanal ist im aktuellen Code noch nicht aktiv.

Das bedeutet:

* policy_rules werden gelernt
* aber der Worker liest sie noch nicht aktiv zurück
* der Loop ist deshalb noch nicht vollständig geschlossen

Konsequenz

Der aktuelle PTZ-Lernloop ist funktional eher:

* Reflex
* Reward
* Dream-Verdichtung
* noch kein Online-Policy-Rückfluss

Deshalb ist die treffende Einordnung:

Im aktuellen Stand ist der PTZ-Lernloop bis zur Offline-Verdichtung vorhanden, aber der online wirksame Policy-Rückkanal ist noch offen.

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
  ROUND(AVG(q),6) AS avg_q,
  ROUND(MIN(q),6) AS min_q,
  ROUND(MAX(q),6) AS max_q,
  COUNT(*) AS rule_count,
  datetime(MAX(last_ts),'unixepoch','localtime') AS last_seen
FROM policy_rules
WHERE namespace='ptz_motor'
GROUP BY action
ORDER BY avg_q DESC;

⸻

Was der Lernloop aktuell noch nicht leistet

Kein Online-Policy-Rückfluss

Die verdichtete ptz_motor-Policy beeinflusst den Worker derzeit noch nicht direkt.

Kein vollständig geschlossener End-to-End-Adaptionskreis

Es gibt zwar:

* Reward-Erzeugung
* Dream-Verdichtung

aber noch keinen live wirksamen:

* Policy-Bias in der Online-Motorik

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

Die treffendste Formulierung für den aktuellen Stand ist:

Der ORÓMA PTZ-Motor-Lernloop ist bis zur Offline-Verdichtung real und funktionsfähig.
Online-Reflex, separate Reward-Erzeugung und Dream-basierte Policy-Verdichtung sind implementiert und im Live-Betrieb nachweisbar.
Der Rückfluss der gelernten ptz_motor-Policy in den Online-Worker ist derzeit noch nicht aktiviert.

Oder kürzer:

Der PTZ-Lernloop ist aktuell ein 3,5-stufiger Loop: Reflex, Reward und Dream-Verdichtung sind real; der Policy-Rückkanal ist noch offen.

⸻

Empfohlene nächste Ausbaustufe

Wenn der Loop später weiterentwickelt wird, ist die nächste saubere Stufe:

1. policy_rules namespace='ptz_motor' im Worker lesbar machen
2. pro state_hash einen weichen Aktionsbias ableiten
3. Bias nur ergänzend, nicht dominierend einsetzen
4. Logging sichtbar machen:
    * Policy-Bias aktiv ja/nein
    * Einflussstärke
    * Quelle / state_hash / action

Dann wäre der PTZ-Lernloop erstmals wirklich geschlossen.

⸻

Schlussfazit

Der PTZ-Motor-Lernloop in ORÓMA ist architektonisch sauber in mehrere Zeitskalen getrennt:

* schnelle Motorik
* separate Reward-Attribution
* Dream-basierte Policy-Konsolidierung

Die reale Betriebsanalyse zeigt:

* Worker kann stabil laufen
* Reward-Erzeugung hängt am Collector
* Dream-Verdichtung hängt an DREAM-Phase und Collector-Daten
* die Policy-Verdichtung funktioniert
* der Rückkanal in den Worker fehlt noch

Damit ist der Loop bereits deutlich weiter als ein bloßer Reflexpfad, aber noch nicht vollständig zum selbstverstärkenden Online-Lernkreis geschlossen.
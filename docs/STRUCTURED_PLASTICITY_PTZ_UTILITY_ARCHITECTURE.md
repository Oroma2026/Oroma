# ORÓMA – Structured Plasticity / PTZ Utility Learning Architecture

**Pfad:** `/opt/ai/oroma/docs/STRUCTURED_PLASTICITY_PTZ_UTILITY_ARCHITECTURE.md`  
**Projekt:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Kurzbeschreibung:** An offline-first adaptive edge intelligence architecture  
**Dokumenttyp:** Architektur-, Zielbild- und Implementierungsdokumentation  
**Stand:** 2026-05-29  
**Baseline-ZIP:** `/mnt/data/oroma_20260529_081410_with_db.zip`  
**Status:** Implementierter Meilenstein – PTZ-Motorik besitzt einen geschlossenen Utility-zu-Dream-zu-Policy-Lernpfad plus PTZ Phase 5a Policy-Bias-Rückfluss  
**Autor/Owner:** ORÓMA-Projekt  

---

## 1. Zweck dieses Dokuments

Dieses Dokument hält den Architekturstand nach Einführung der **Structured Plasticity / Utility Signal Layer** für die PTZ-Motorik fest.

Es dokumentiert:

- welches Ziel hinter der neuen Utility-Schicht steht,
- welche Lücke im bisherigen System geschlossen wurde,
- welche Dateien eingeführt oder erweitert wurden,
- welche Regeln für Domänenfreiheit und Schichtentrennung gelten,
- wie der PTZ-Motor-Worker weiterhin als schneller Hot-Loop getrennt bleibt,
- wie Utility-Signale aus dem Motor-State entstehen,
- wie diese Signale im DreamWorker zu `policy_rules` verdichtet werden,
- welche Live-Tests den geschlossenen Lernpfad bestätigt haben,
- was bewusst noch **nicht** umgesetzt wurde.

Dieses Dokument ist kein Ersatz für die Roadmap-Datei:

```text
/opt/ai/oroma/docs/ROADMAP_STRUCTURED_PLASTICITY_UTILITY_UPDATED.md
```

Die Roadmap beschreibt die geplante Einführung. Dieses Dokument beschreibt den erreichten Architektur- und Implementierungsstand.

---

## 2. Ausgangspunkt

ORÓMA besitzt bereits ein Gedächtnis- und Konsolidierungssystem aus:

```text
Snap / SnapChain
Replay / Dream
RewardLogger / rewards_log
policy_rules
Forgetting / Compression / MetaSnaps
DBWriter-kompatibler Schreibdisziplin
```

Die neue PTZ-Verfolgung über den Motor Worker brachte aber eine wichtige Architekturfrage hervor:

```text
Wie lernt ORÓMA nicht nur, dass etwas passiert ist,
sondern ob eine lokale Wahrnehmungs- oder Motorverbindung nützlich war?
```

Vor dieser Erweiterung gab es bereits klassische Rewards und ältere PTZ-Attention-Signale. Der neue PTZ-Motor-Worker war jedoch bewusst als schneller, DB-freier Hot-Loop gebaut. Dadurch war er stabil und schnell, aber seine Erfahrungen wurden noch nicht sauber in die langfristige ORÓMA-Kognition zurückgeführt.

Die neue Architektur schließt genau diese Lücke.

---

## 3. Leitprinzip: Structured Plasticity

Structured Plasticity bedeutet für ORÓMA:

```text
Nicht alles ist frei.
Nicht alles ist fest.
ORÓMA besitzt spezialisierte Grundbahnen,
aber konkrete Verbindungen entstehen durch Erfahrung,
lokalen Nutzen, Dream-Verdichtung und späteres Pruning.
```

Biologische Analogie:

- Das Gehirn startet nicht als beliebiger leerer Graph.
- Evolutionär vorgeprägte Bahnen existieren bereits: Sehen, Hören, Motorik, Aufmerksamkeit, Sprache, Gedächtnis.
- Innerhalb dieser Bahnen entstehen viele Kandidatenverbindungen.
- Wiederholte Aktivität und lokales Nutzenfeedback verstärken sinnvolle Verbindungen.
- Schwache, nutzlose oder nicht wiederverwendete Verbindungen werden später abgeschwächt, komprimiert oder gepruned.

ORÓMA-Ziel:

```text
Gerüst
→ Kandidaten
→ lokales Utility-Signal
→ Dream-Verdichtung
→ Policy/Pruning
→ geordnete adaptive Struktur
```

Für PTZ bedeutet das konkret:

```text
Der Motor bewegt nicht nur.
ORÓMA bewertet nachträglich,
ob eine Bewegung oder Zieländerung lokal nützlich war.
Diese Bewertung wird im Dream verdichtet.
```

---

## 4. Rollentabelle der beteiligten Schichten

| Schicht / Datei | Rolle | Kennt | Kennt nicht |
|---|---|---|---|
| `core/snap.py` | Was wurde erlebt? | Erlebnisstruktur, Features, Zeit, Kontext | Domänenspezifische Utility-Logik |
| `core/reward.py` | Wo wird ein Signal gespeichert? | `rewards_log`, DBWriter, raw JSON | Ob PTZ/Vision/Audio gut war |
| `core/utility.py` | War etwas lokal nützlich? | normierte Utility-Signale, Validierung, Clamping, Weitergabe an `RewardLogger` | PTZ-, Vision-, Audio-, Dream-, Policy- oder Pruning-Logik |
| `tools/ptz_motor_worker.py` | Schnelle PTZ-Motorik | Kamera-State, Bewegungs-/Kandidatenlogik, Servo-Kommando | DB-Schreibpfad, Dream, Policy-Verdichtung |
| `tools/ptz_motor_reward_collector.py` | Berechnet PTZ-Utility aus Worker-State | vorher/nachher-Vergleich, PTZ-Kontext, UtilitySignal | Dream-Aggregation, Motorsteuerung |
| `core/dream_worker.py` | Langfristige Verdichtung | Rewards, raw/context, `policy_rules`, Namespaces | Hot-Loop-Motorik |
| `core/forgetting.py` | Späteres Abschwächen/Komprimieren | Alter, Gewicht, Qualität, spätere Utility-Hinweise | Roh-Motorlogik |

Grundregel:

```text
Jede Schicht kennt nur die direkt darunterliegende oder stabile Core-Schnittstelle.
utility.py kennt reward.py.
reward.py kennt utility.py nicht.
Collector kennen utility.py.
utility.py kennt Collector nicht.
dream_worker.py liest rewards_log und policy_rules.
dream_worker.py steuert den PTZ-Motor nicht.
```

---

## 5. Implementierter Architekturfluss

Der erreichte Stand ist:

```text
PTZ Motor Worker
  ↓
ptz_motor_state.json
  ↓
PTZ Motor Reward Collector
  ↓
core.utility.UtilitySignal
  ↓
core.reward.RewardLogger
  ↓
rewards_log
  ↓
DreamWorker Phase: ptz_policy_motor
  ↓
policy_rules namespace='ptz_motor'
```

Ausformuliert:

1. `tools/ptz_motor_worker.py` läuft als schneller Motor-Hot-Loop.
2. Der Worker schreibt seinen Zustand nach:

```text
/opt/ai/oroma/data/state/ptz_motor_state.json
```

3. `tools/ptz_motor_reward_collector.py` liest diesen State langsam und vergleicht vorher/nachher.
4. Der Collector berechnet lokale Utility-Signale.
5. Diese Signale werden über `core.utility.emit()` normalisiert und an `RewardLogger` weitergegeben.
6. `RewardLogger` schreibt DBWriter-kompatibel in `rewards_log`.
7. `core/dream_worker.py` liest im Dream nur echte motorische PTZ-Utility-Signale.
8. Der DreamWorker verdichtet diese in `policy_rules` unter:

```text
namespace='ptz_motor'
```

---

## 6. Implementierte Dateien und Verantwortlichkeiten

### 6.1 `core/utility.py`

Neue stabile Core-Datei.

Rolle:

```text
Domänenfreie Schnittstelle für lokale Nützlichkeit.
```

Öffentliche API:

```python
UtilitySignal
emit(signal) -> bool
get_counters() -> Dict[str, int]
```

Wichtige Invarianten:

- keine PTZ-/Vision-/Audio-/Dream-/Policy-Imports,
- `RewardLogger` wird lazy importiert,
- RewardLogger wird als prozessweiter Singleton wiederverwendet,
- `value` wird auf `[-1.0, +1.0]` geclamped,
- `confidence` wird auf `[0.0, 1.0]` geclamped,
- `reward = value * confidence`,
- `context` wird JSON-sicher saniert,
- nicht serialisierbarer Kontext crasht nicht,
- Sanitizing/Clamping wird über Counter sichtbar gemacht.

Relevante Counter:

```text
utility_emit_total
utility_emit_ok
utility_emit_failed
utility_invalid_signal
utility_context_sanitized
utility_value_clamped
utility_confidence_clamped
```

`core/utility.py` enthält bewusst keine Domänenlogik.

---

### 6.2 `tools/ptz_motor_reward_collector.py`

Neue Slow-Loop-Datei für PTZ-Utility.

Rolle:

```text
Aus ptz_motor_state.json lokale PTZ-Nützlichkeit ableiten.
```

Der Collector:

- sendet keine PTZ-Kommandos,
- greift nicht auf Kamera-Hardware zu,
- schreibt nicht direkt in SQLite,
- ändert den Motor-Worker nicht,
- läuft langsam und getrennt vom Hot-Loop,
- emittiert ausschließlich über `core.utility.emit()`.

Implementierte Utility-Sources:

```text
ptz_motor/center_gain
ptz_motor/target_conf_gain
ptz_motor/target_stability
ptz_motor/wasted_motion_penalty
ptz_motor/cmd_fail_penalty
ptz_motor/reversal_penalty
```

Aktiv im Live-Test beobachtet:

```text
ptz_motor/target_conf_gain
ptz_motor/wasted_motion_penalty
```

Wichtige Bewertungsregeln:

- `heartbeat_ts` muss aktuell sein,
- stale State erzeugt kein negatives Signal,
- Bewegungs-Rewards nur bei `cmd_ok=True`,
- `deadzone`, `energy_low`, `idle`, `cooldown` und ähnliche Gründe erzeugen keine falschen Bewegungs-Penalties,
- `center_gain` entsteht nur bei echter Distanzverbesserung,
- `wasted_motion_penalty` entsteht nur bei echter Bewegung und Distanzverschlechterung,
- `target_conf_gain` bewertet Ziel-Konfidenzänderung,
- Fehler-/Reversal-Penalties entstehen nur bei gestiegenen Zählern.

---

### 6.3 Kontext-Semantik des Collectors

Patch 3.2 hat die Kontextstruktur für den DreamWorker geschärft.

Wichtige Top-Level-Felder in `raw.context`:

```text
policy_action
executed_action
proposed_action
reason
cmd_ok
before_dist
after_dist
before_target_conf
after_target_conf
candidate_winner
candidate_source
target_mode
target_update
target_hold_active
eye_hold_bias_active
axis_lock_active
debug
```

Semantik:

```text
policy_action
  Nur gesetzt, wenn cmd_ok=True und wirklich eine Motoraktion ausgeführt wurde.
  Nur diese Einträge sind für motorisches Policy-Lernen geeignet.

executed_action
  Was tatsächlich als Worker-Aktion ausgeführt wurde.

proposed_action
  Was der Worker als Richtung/Kandidat berechnet hatte,
  auch wenn nicht gefahren wurde.

debug
  Rohfelder wie raw_action, mapped_action, obs_action, obs_mapped_action.
  Diese dienen Diagnose, nicht primärem Policy-Lernen.
```

Damit kann der DreamWorker unterscheiden:

```text
policy_action != ""
  → motorisches Lernmaterial für namespace='ptz_motor'

policy_action == "" und proposed_action != ""
  → perzeptives/diagnostisches Utility,
    aber kein direkter Motor-Policy-Beweis
```

---

### 6.4 `core/dream_worker.py`

Erweiterte Datei.

Neue Dream-Phase:

```text
ptz_policy_motor
```

Neue Funktionalität:

```text
rewards_log source LIKE 'ptz_motor/%'
→ raw.utility == true
→ raw.context.policy_action != ''
→ Aggregation in policy_rules namespace='ptz_motor'
```

Wichtige Schutzregel:

```text
Einträge ohne policy_action werden nicht als motorische policy_rules gelernt.
```

Dadurch werden Beobachtungs-, Cooldown-, Hold- oder Stability-Wait-Signale nicht fälschlich als ausgeführte Motoraktion trainiert.

Zusätzliche CLI-/Phasen-Steuerung aus Patch 4.1:

```text
--once
--phase ptz_policy_motor
OROMA_DREAM_ONLY_PHASES=ptz_policy_motor
```

Der Grund dafür war, dass ein kompletter Dream-Lauf im Live-Test zuvor durch die `replay`-Phase das Laufzeitbudget verbrauchte, bevor `ptz_policy_motor` erreicht wurde.

---

## 7. State-Hash-Prinzip für PTZ-Motor-Policy

Der PTZ-Motor-State-Hash im DreamWorker ist bewusst:

```text
float-schonend
action-frei
kontextuell
stabil genug für frühes Lernen
```

Die Aktion selbst wird nicht in den State-Hash eingebaut. Sie bleibt die `action`-Spalte in `policy_rules`.

Typische Kontextanteile:

```text
source
reason
candidate_winner
candidate_source
target_mode
target_update
target_hold_active
eye_hold_bias_active
axis_lock_active
before_dist_bucket
before_target_conf_bucket
```

Warum keine Rohfloats als State:

```text
Rohwerte wie 0.381600, 0.280104, 0.110441 würden zu viele einzigartige States erzeugen.
Bucketisierung verhindert Fragmentierung.
```

Warum keine Action im State:

```text
state_hash beschreibt die Situation.
action beschreibt die gewählte Bewegung.
policy_rules lernt state_hash + action → q.
```

---

## 8. Relevante ENV-Parameter

### 8.1 Utility / Collector

```text
OROMA_PTZ_MOTOR_COLLECTOR_ENABLE=1
OROMA_PTZ_MOTOR_COLLECTOR_INTERVAL_SEC=8
OROMA_PTZ_MOTOR_COLLECTOR_MAX_STATE_AGE=15
OROMA_PTZ_MOTOR_COLLECTOR_MIN_DIST_CHANGE=0.02
OROMA_PTZ_MOTOR_COLLECTOR_MIN_CONF_CHANGE=0.02
OROMA_PTZ_MOTOR_COLLECTOR_MIN_STABILITY_DELTA=1
OROMA_PTZ_MOTOR_COLLECTOR_STABILITY_MIN_COUNT=2
OROMA_PTZ_MOTOR_COLLECTOR_CONF_FLOOR=0.05
OROMA_PTZ_MOTOR_COLLECTOR_CONF_CEIL=1.0
```

### 8.2 DreamWorker PTZ-Motor-Policy

```text
OROMA_PTZ_MOTOR_POLICY_DREAM_ENABLE=1
OROMA_PTZ_MOTOR_POLICY_MAX_ROWS=500
OROMA_PTZ_MOTOR_POLICY_POS_THR=0.02
OROMA_PTZ_MOTOR_POLICY_NEG_THR=-0.02
OROMA_PTZ_MOTOR_POLICY_NS=ptz_motor
```

### 8.3 Gezielt nur PTZ-Motor-Dream-Phase laufen lassen

```text
OROMA_DREAM_ONLY_PHASES=ptz_policy_motor
```

oder per CLI:

```bash
python3 core/dream_worker.py --once --phase ptz_policy_motor --verbose
```

---

## 9. Live-Test-Nachweise

### 9.1 Collector-Test mit geschlossenen Utility-Emits

Live-Lauf nach Patch 3.2:

```text
[ptz_motor_reward_collector] emit source=ptz_motor/wasted_motion_penalty value=-1.0000 confidence=0.4406 ok=1
[ptz_motor_reward_collector] emit source=ptz_motor/target_conf_gain value=+1.0000 confidence=0.4406 ok=1
...
[ptz_motor_reward_collector] stop total_emitted=14 total_skipped=31 ...
```

Utility-Counter:

```text
utility_emit_total: 14
utility_emit_ok: 14
utility_emit_failed: 0
utility_invalid_signal: 0
utility_context_sanitized: 0
utility_value_clamped: 0
utility_confidence_clamped: 0
```

Interpretation:

```text
Der Collector erzeugt valide Utility-Signale.
core.utility akzeptiert sie.
RewardLogger schreibt sie erfolgreich.
Keine Sanitizing-/Clamp-Notfälle im Live-Lauf.
```

---

### 9.2 rewards_log-Prüfung

Beispiel aus `rewards_log` nach Patch 3.2:

```text
ptz_motor/target_conf_gain
ptz_motor/wasted_motion_penalty
```

Kontextprüfung zeigte korrekt:

```text
policy_action=down | executed_action=down | proposed_action=down | reason=follow | cmd_ok=1
policy_action=up   | executed_action=up   | proposed_action=up   | reason=follow | cmd_ok=1
policy_action=''   | executed_action=''   | proposed_action=left | reason=move_cooldown | cmd_ok=NULL
```

Interpretation:

```text
Echte Motoraktionen sind eindeutig von perzeptiven/diagnostischen Utility-Signalen getrennt.
```

---

### 9.3 DreamWorker-Verdichtung in policy_rules

Gezielter DreamWorker-Test:

```bash
cd /opt/ai/oroma; python3 -m py_compile core/dream_worker.py; sudo -u oroma env PYTHONPATH=/opt/ai/oroma OROMA_BASE=/opt/ai/oroma OROMA_DBW_ENABLE=1 OROMA_DREAM_MAX_RUNTIME_S=60 OROMA_PTZ_MOTOR_POLICY_DREAM_ENABLE=1 python3 core/dream_worker.py --once --phase ptz_policy_motor --verbose
```

Ergebnis in `policy_rules`:

```text
ptz_motor|down |4|4| 0.055202|-0.440608| 0.53556 |1779045978
ptz_motor|left |2|2| 0.0     |-0.575542| 0.575542|1779045929
ptz_motor|right|2|2| 0.0     |-0.486114| 0.486114|1779045953
ptz_motor|up   |2|2|-0.026591|-0.198917| 0.145734|1779045969
```

Checkpoint:

```text
ptz_motor_policy:last_reward_id|1165267
```

Interpretation:

```text
DreamWorker hat PTZ-Motor-Utility-Rewards gelesen,
verarbeitet,
in policy_rules verdichtet
und den Fortschritt persistent markiert.
```

---

## 10. Aktueller Architekturstatus

Erreicht:

```text
Patch 1   Roadmap Structured Plasticity / Utility Signal Layer
Patch 2   core/utility.py
Patch 2.1 Lazy Singleton RewardLogger in utility.py
Patch 3   tools/ptz_motor_reward_collector.py
Patch 3.1 Skip-Diagnose im Collector
Patch 3.2 Kontext-Schärfung policy_action/executed_action/proposed_action/debug
Patch 4   DreamWorker ptz_policy_motor
Patch 4.1 DreamWorker --once / --phase / OROMA_DREAM_ONLY_PHASES
```

Damit existiert jetzt ein geschlossener Pfad:

```text
Motorischer Zustand
→ lokales Utility-Signal
→ persistenter Reward
→ Dream-Verdichtung
→ policy_rules
```

Das ist der erste praktisch bestätigte Structured-Plasticity-Lernpfad für PTZ-Motorik.

---

## 11. Was bewusst noch nicht umgesetzt wurde

Noch nicht umgesetzt bzw. weiterhin bewusst begrenzt:

```text
UI-Dashboard für ptz_motor-policy_rules
Pruning anhand ptz_motor-Utility
Vision-/Audio-/Crossmodal-Utility-Collector
Harte Policy-Steuerung der PTZ-Motorik
Direktes Überschreiben von Safety-Gates durch Policy
```

Seit PTZ Phase 5a umgesetzt:

```text
Policy-Rückkopplung in tools/ptz_motor_worker.py
Read-only Laden von policy_rules namespace='ptz_motor'
Weicher, ENV-gesteuerter Aktions-Bias
Kein DB-Schreibzugriff im Worker-Hot-Loop
Kein Ersatz der Reflexlogik
```

Warum nur ein weicher Bias zulässig ist:

```text
Der PTZ-Motor-Worker ist der Hot-Loop.
Er muss stabil, schnell und sicher bleiben.
Die Policy enthält echte, aber noch junge Evidenz.
Eine harte Rückkopplung könnte gutes Reflexverhalten verschlechtern.
```

Der sichere Zwischenzustand seit Phase 5a ist daher:

```text
Motor bleibt reflexbasiert.
Utility bewertet lokal.
Dream verdichtet offline.
Policy speichert Erfahrung.
Worker nutzt Policy nur optional als kleinen Bias.
```

---

## 12. Nächste sinnvolle Schritte

### 12.1 Beobachten und Daten sammeln

Zunächst sollten weitere PTZ-Motor-Utility-Daten gesammelt werden.

Sinnvolle Checks:

```bash
cd /opt/ai/oroma; sudo -u oroma sqlite3 /opt/ai/oroma/data/oroma.db "SELECT namespace, action, COUNT(*) AS rows, SUM(n) AS total_n, ROUND(AVG(q),6) AS avg_q, ROUND(MIN(q),6) AS min_q, ROUND(MAX(q),6) AS max_q, MAX(last_ts) AS last_ts FROM policy_rules WHERE namespace='ptz_motor' GROUP BY namespace, action ORDER BY avg_q DESC;"
```

```bash
cd /opt/ai/oroma; sudo -u oroma sqlite3 /opt/ai/oroma/data/oroma.db "SELECT action, state_hash, n, ROUND(q,6), pos, neg, draw, last_ts FROM policy_rules WHERE namespace='ptz_motor' ORDER BY last_ts DESC LIMIT 20;"
```

### 12.2 Optionaler Collector-Betrieb

Der Collector kann später über Orchestrator oder systemd/timer angebunden werden. Dabei gilt:

```text
Kein DB-Schreibpfad im PTZ-Motor-Hot-Loop.
Collector bleibt Slow-Loop.
Collector nutzt core.utility.
DreamWorker verdichtet separat.
```

### 12.3 Policy-Rückkopplung seit PTZ Phase 5a

Die Rückkopplung in den PTZ-Motor-Worker ist seit PTZ Phase 5a als weicher Bias implementiert:

```text
nicht als harte Motorsteuerung,
nicht bei geringer Datenlage,
nur wenn n und q ausreichend belastbar sind,
nur bei gleichwertigen Kandidaten oder weichen Tuning-Entscheidungen.
```

Beispiel für die implementierte Nutzung:

```text
Wenn mehrere plausible Richtungen existieren,
kann policy_rules namespace='ptz_motor'
eine leicht bevorzugte Richtung vorschlagen.
```

Nicht zulässig als erster Schritt:

```text
Policy ersetzt Reflexlogik.
Policy fährt Motor allein.
Policy ignoriert Safety/Deadzone/Stability-Gates.
```

---

## 13. Wissenschaftliche Einordnung

Der erreichte Stand ist konzeptionell relevant, weil ORÓMA damit nicht nur Sensordaten speichert, sondern lokale Nützlichkeit strukturiert bewertet.

Vorher:

```text
PTZ folgt Bewegung.
```

Jetzt:

```text
PTZ folgt Bewegung.
ORÓMA bewertet nachträglich, ob diese Bewegung lokal nützlich war.
Dream verdichtet diese Erfahrung.
policy_rules speichern die gelernte Tendenz.
```

Das ist ein Schritt von:

```text
Reflex
```

zu:

```text
erfahrungsbasierter Blickentwicklung
```

Die biologische Analogie ist nicht, dass ORÓMA ein Gehirn nachbildet. Die Analogie ist:

```text
Spezialisierte Bahn
+ viele Kandidaten
+ lokales Nutzenfeedback
+ Offline-Konsolidierung
= geordnetere adaptive Struktur
```

Das ist die konkrete technische Bedeutung von Structured Plasticity in ORÓMA.

---

## 14. Wartungsregeln für zukünftige Patches

### 14.1 `core/utility.py` möglichst nicht mehr anfassen

`core/utility.py` ist als stabile Core-Datei gedacht.

Änderungen daran nur, wenn:

```text
eine echte Invariante falsch ist,
ein Sicherheitsproblem besteht,
eine API-abwärtskompatible Erweiterung zwingend nötig ist.
```

Nicht in `core/utility.py` einbauen:

```text
PTZ-Sonderfälle
Vision-Sonderfälle
Audio-Sonderfälle
Dream-Logik
Pruning-Logik
Policy-Auswahl
```

### 14.2 Domänenspezifische Logik gehört in Collector

PTZ-spezifische Bewertung gehört nach:

```text
tools/ptz_motor_reward_collector.py
```

Vision später nach einem Vision-Collector.
Audio später nach einem Audio-Collector.
Crossmodal später nach einem Crossmodal-Collector.

### 14.3 DreamWorker verdichtet, steuert aber nicht

`core/dream_worker.py` darf Rewards aggregieren und in `policy_rules` schreiben.

Er darf nicht:

```text
PTZ-Kommandos senden,
den Worker starten/stoppen,
Hot-Loop-Entscheidungen erzwingen.
```

### 14.4 DBWriter-Disziplin bleibt unverändert

Für DB-Schreibpfade gilt weiterhin:

```text
Keine direkten lokalen DB-Fallback-Writes bei aktivem DBWriter.
Keine offenen SQLite-Verbindungen.
Keine DB-Schreibzugriffe im PTZ-Motor-Hot-Loop.
```

---

## 15. Abschlussbewertung

Der aktuelle Meilenstein ist erreicht:

```text
ORÓMA besitzt jetzt einen geschlossenen lokalen Utility-zu-Dream-zu-Policy-Lernpfad für PTZ-Motorik.
```

Das System kann:

```text
1. PTZ-Motorzustände beobachten,
2. lokale Nützlichkeit berechnen,
3. Utility-Signale persistieren,
4. echte motorische Aktionen von bloßen Vorschlägen trennen,
5. Utility-Erfahrung im Dream verdichten,
6. PTZ-Motor-Tendenzen in policy_rules speichern.
```

Damit ist die Grundlage gelegt für spätere strukturierte Plastizität auch in anderen Bahnen:

```text
Vision
Audio
Crossmodal Binding
Language / ASR
Memory / Replay
Energy / Runtime Control
```

Der aktuelle produktive Zustand bleibt bewusst konservativ:

```text
lernen ja,
rücksteuern ja – aber nur als abschaltbarer, weicher Policy-Bias.
```

Das ist für ORÓMA der richtige nächste stabile Zwischenstand: Der Loop ist geschlossen, ohne die Reflex-/Safety-Architektur aufzugeben.


---

## 16. PTZ Phase 5a – Policy-Bias-Rückfluss im Worker

**Stand:** 2026-05-29  
**Implementierte Datei:** `tools/ptz_motor_worker.py`  
**Rolle:** Schließt den Rückkanal von `policy_rules namespace='ptz_motor'` in die Online-Motorik.

### Datenlage vor Aktivierung

Live-SQL zeigte bereits echte PTZ-Motor-Lerndaten:

```text
ptz_motor/target_conf_gain       8776 Rewards
ptz_motor/wasted_motion_penalty   161 Rewards
ptz_motor/center_gain              58 Rewards
ptz_motor/target_stability         28 Rewards
ptz_motor/cmd_fail_penalty          1 Reward

policy_rules namespace='ptz_motor':
rows=14, SUM(n)=314, avg(q)=0.058, max(q)=0.5755, min(q)=-0.5755
```

Damit war der Offline-Lernpfad ausreichend belegt, um einen vorsichtigen Rückfluss zu testen.

### Umsetzungsprinzip

Der Worker liest `policy_rules` read-only, verdichtet Regeln pro Aktion und erzeugt daraus einen kleinen Bias.

```text
SELECT action, SUM(n), AVG(q), MAX(last_ts)
FROM policy_rules
WHERE namespace='ptz_motor'
GROUP BY action
```

Filter und Begrenzungen:

```text
n >= OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_N
abs(q) >= OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_ABS_Q
abs(bias) <= OROMA_PTZ_MOTOR_POLICY_BIAS_MAX_ABS
```

### Starttest

Der erste Live-Test nach Einbau war erfolgreich:

```text
py_compile OK
oroma-ptz-motor-worker.service active (running)
ok stieg von 169 auf 214
fail blieb 0
```

Die Startzeile zeigte die neuen Parameter:

```text
policy_bias=0 policy_ns=ptz_motor policy_w=0.080 policy_min_n=5 policy_min_q=0.050 policy_refresh=60.0s
```

Der Bias war im ersten Test bewusst noch deaktiviert. Damit ist der Codepfad startstabil, aber der aktive Lernrückfluss muss separat mit `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=1` beobachtet werden.

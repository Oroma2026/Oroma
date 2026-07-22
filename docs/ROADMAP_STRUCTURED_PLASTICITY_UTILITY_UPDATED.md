# ORÓMA Roadmap – Structured Plasticity / Utility Signal Layer

**Projekt:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Kurzbeschreibung:** An offline-first adaptive edge intelligence architecture  
**Dokumenttyp:** Roadmap / technische Umsetzungsplanung  
**Stand:** 2026-05-29  
**Baseline-ZIP:** `/mnt/data/oroma_20260529_081410_with_db.zip`  
**Arbeitskopie zur Prüfung:** `/mnt/data/oroma_md_update/`  
**Status:** Roadmap nach PTZ Phase 5a – Policy-Bias-Rückfluss implementiert und initial live getestet  

---

## 1. Ausgangspunkt

ORÓMA besitzt bereits viele Bausteine für eine biologisch inspirierte Lernarchitektur:

- `snap.py` als Erlebnis- und Gedächtnisatom
- `reward.py` als technischer Reward-/Signal-Logger
- `rewards_log` in `oroma.db` als vorhandene Persistenzschicht
- `dream_worker.py` als Verdichtungs- und Konsolidierungsinstanz
- `forgetting.py` als Abschwächungs-/Kompressions-/Pruning-Schicht
- PTZ, Vision, Audio, Crossmodal- und Policy-Pfade als spezialisierte Bahnen
- DBWriter-kompatible Schreibwege als zentrale Produktionsinvariante

Die konzeptionelle Lücke liegt nicht im Fehlen eines Logs oder einer DB-Tabelle, sondern in der semantischen Trennung zwischen:

```text
Was wurde technisch geloggt?
```

und:

```text
War diese lokale Verbindung / Aktion / Wahrnehmung in ihrer Bahn nützlich?
```

Dafür wird eine stabile Core-Datei eingeführt:

```text
core/utility.py
```

Diese Datei soll das Grundelement der lokalen kognitiven Bewertung definieren.

---

## 2. Leitprinzip: Structured Plasticity

Structured Plasticity bedeutet für ORÓMA:

```text
Nicht alles ist frei.
Nicht alles ist fest.
ORÓMA besitzt spezialisierte Grundbahnen,
aber konkrete Verbindungen entstehen durch Erfahrung,
lokalen Nutzen, Dream-Verdichtung und Pruning.
```

Biologische Analogie:

- Das Gehirn startet nicht als leerer, komplett zufälliger Graph.
- Es besitzt evolutionär vorgeformte Bahnen: Sehen, Hören, Motorik, Aufmerksamkeit, Sprache, Gedächtnis.
- Innerhalb dieser Bahnen werden zu viele Kandidatenverbindungen zugelassen.
- Wiederholte Aktivität, Nutzen, Feedback und Konsolidierung verstärken sinnvolle Verbindungen.
- Unnütze, schwache oder nicht wiederverwendete Verbindungen werden abgeschwächt, komprimiert oder gepruned.

ORÓMA-Ziel:

```text
Gerüst → Kandidaten → lokales Utility-Signal → Dream → Policy/Pruning → geordnete adaptive Struktur
```

---

## 3. Rollentabelle der beteiligten Schichten

| Datei / Schicht | Rolle | Darf wissen | Darf nicht wissen |
|---|---|---|---|
| `core/snap.py` | Was wurde erlebt? | Erlebnisstruktur, Features, Zeit, Kontext | Domänenspezifische Utility-Logik |
| `core/reward.py` | Wo wird ein Signal gespeichert? | `rewards_log`, DBWriter, raw JSON, Metriken | Ob PTZ/Vision/Audio gut oder schlecht war |
| `core/utility.py` | War etwas lokal nützlich? | normierte Utility-Signale, Validierung, Clamping, Weitergabe an `RewardLogger` | PTZ-, Vision-, Audio-, Dream-, Pruning- oder Policy-Logik |
| Collector | Wie wird Nutzen berechnet? | Domänenspezifische Metriken und Kontextdaten | Globale Dream-/Pruning-Entscheidung |
| `core/dream_worker.py` | Was bedeutet es langfristig? | Aggregation, Namespaces, `policy_rules`, Verdichtung | Hot-Loop-Motorik |
| `core/forgetting.py` | Was bleibt, was geht? | Alter, Gewicht, Qualität, spätere Utility-Hinweise | Roh-Motorlogik |

Grundregel:

```text
Jede Schicht kennt nur die Schicht direkt darunter.
utility.py kennt reward.py.
reward.py kennt utility.py nicht.
Collector kennen utility.py.
utility.py kennt Collector nicht.
```

---

## 4. Zielbild für `core/utility.py`

`core/utility.py` soll so stabil gebaut werden, dass sie im Idealfall nach Einführung nicht mehr angepasst werden muss.

Sie ist keine PTZ-Datei, keine Vision-Datei und keine Audio-Datei.
Sie ist eine domänenfreie Core-Schnittstelle.

### 4.1 Öffentliche API

Geplante öffentliche API:

```python
@dataclass
class UtilitySignal:
    source: str
    bahn: str
    value: float
    confidence: float = 1.0
    context: Dict[str, Any] = field(default_factory=dict)
    ts: Optional[float] = None
    episode_id: Optional[int] = None
    step: int = 0
    tag: Optional[str] = None


def emit(signal: UtilitySignal) -> bool:
    ...
```

Zusätzlich ist eine kleine Diagnosefunktion vorgesehen:

```python
def get_counters() -> Dict[str, int]:
    ...
```

`get_counters()` gibt eine Kopie interner Utility-Diagnosezähler zurück.
Diese Funktion bleibt domänenfrei und dient nur dazu, sichtbar zu machen, ob die Utility-Schicht selbst sauber arbeitet.

Weitere öffentliche API ist für Stufe 1 nicht vorgesehen.

### 4.2 Nicht-Ziele

`core/utility.py` darf nicht enthalten:

```text
PTZ-Metriken
Vision-Metriken
Audio-Metriken
Crossmodal-Bewertung
Dream-Aggregation
Pruning-Entscheidung
Policy-Auswahl
DB-Schemaänderungen
Hot-Loop-Logik
```

### 4.3 Invarianten

`source`:

- Muss ein nichtleerer String sein.
- Wird nicht hart auf ein Schema geprüft.
- Dokumentierte Konvention: `bereich/signal_name` oder `bahn/bereich/signal_name`.
- Beispiele:

```text
ptz_motor/follow_gain
ptz_motor/center_gain
vision/feature_stability
audio/probe_confirmed
crossmodal/audio_visual_binding
language/semantic_reuse
```

`bahn`:

- Muss ein nichtleerer String sein.
- Keine Whitelist in `utility.py`.
- Dadurch bleibt die Datei offen für spätere Bahnen:

```text
ptz
vision
audio
crossmodal
language
memory
dream
motor
energy
game
```

`value`:

- Wird als float behandelt.
- Wird auf `[-1.0, +1.0]` geclamped.
- Bedeutung:

```text
-1.0 = maximal lokal schädlich / eindeutig unbrauchbar
 0.0 = neutral / unklar / kein Nutzen
+1.0 = maximal lokal nützlich
```

`confidence`:

- Wird als float behandelt.
- Wird auf `[0.0, 1.0]` geclamped.
- Bedeutet Sicherheit des Collectors, nicht Nutzen selbst.

`reward`:

- Wird für `RewardLogger` als gewichteter Wert geschrieben:

```text
reward = value * confidence
```

- Rohwerte bleiben zusätzlich in `raw` erhalten.

`context`:

- Muss dict-kompatibel sein.
- Wird JSON-sicher gemacht.
- Nicht serialisierbare Werte dürfen `emit()` nicht crashen lassen.
- Wenn `context` sanitisiert werden muss, muss ein interner Diagnosezähler erhöht werden.
- Kontext bleibt domänenspezifisch und wird von `utility.py` nicht interpretiert.

`ts`:

- Epoch seconds.
- Falls `None`: `time.time()`.
- Bei Übergabe an `RewardLogger` integer-kompatibel.

`episode_id`:

- `Optional[int]`.
- Muss zur bestehenden DB-Spalte `rewards_log.episode_id INTEGER` passen.
- Keine String-IDs in Stufe 1.

`emit()`:

- Gibt `True` zurück, wenn das Signal angenommen und an `RewardLogger` übergeben wurde.
- Gibt `False` zurück, wenn Validierung oder Speicherung sichtbar fehlschlägt.
- Darf keine stillen Fehler verschlucken.
- Muss Fehler sichtbar loggen/printen, ohne Hot-Loops zu crashen.


`counters`:

- `utility.py` führt kleine interne Diagnosezähler.
- Die Zähler sind domänenfrei und enthalten keine PTZ-, Vision-, Audio- oder Crossmodal-Semantik.
- Sie dienen nur zur Selbstdiagnose der Utility-Schicht.
- Vorgeschlagene Mindestzähler:

```text
utility_emit_total
utility_emit_ok
utility_emit_failed
utility_invalid_signal
utility_context_sanitized
utility_value_clamped
utility_confidence_clamped
```

Wichtige Invariante:

```text
Wenn context sanitisiert werden muss, muss utility_context_sanitized erhöht werden.
```

Damit bleibt ein unsauberer Collector sichtbar, ohne dass ORÓMA durch nicht serialisierbare Kontextdaten abstürzt.

---

## 5. Persistenzmodell

Es wird in Stufe 1 **keine neue DB-Tabelle** eingeführt.

Bestehende Tabelle aus `core/sql_manager.py`:

```sql
CREATE TABLE IF NOT EXISTS rewards_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  source TEXT NOT NULL,
  episode_id INTEGER,
  step INTEGER,
  reward REAL NOT NULL,
  raw TEXT,
  tag TEXT
);
```

`utility.py` schreibt über:

```text
core.reward.RewardLogger.log(...)
```

Dadurch bleiben erhalten:

- DBWriter-Kompatibilität
- kein lokaler SQLite-Fallback bei aktivem DBWriter
- bestehende `rewards_log`-Indizes
- bestehende DreamWorker-Lesemuster
- bestehende Learning-/Reward-Auswertung

### 5.1 Vorgeschlagene `raw`-Struktur

`utility.py` soll in `raw` mindestens folgende Struktur schreiben:

```json
{
  "utility": true,
  "utility_version": 1,
  "bahn": "ptz",
  "source": "ptz_motor/follow_gain",
  "value": 0.34,
  "confidence": 0.82,
  "weighted_value": 0.2788,
  "context": {
    "action": "pan_right",
    "before_dist": 0.42,
    "after_dist": 0.22,
    "cmd_ok": true
  }
}
```

Optional können später zusätzliche Felder ergänzt werden, aber nur abwärtskompatibel.

---

## 6. Phasenplan

## Phase 1 – `core/utility.py` als stabile Core-Schnittstelle

### Ziel

Einführung einer kleinen, domänenfreien Datei:

```text
core/utility.py
```

### Maßnahmen

1. Datei vollständig neu anlegen.
2. Ausführlichen ORÓMA-Header schreiben:
   - Pfad
   - Projekt
   - Version
   - Datum
   - Zweck
   - Architekturrolle
   - Invarianten
   - DBWriter-Hinweis
   - Nicht-Ziele
   - Nutzung
3. `UtilitySignal` als Dataclass definieren.
4. `emit(signal)` implementieren.
5. `get_counters()` implementieren.
6. Interne Utility-Counter implementieren, mindestens für:
   - angenommene Signale
   - fehlgeschlagene Signale
   - invalide Signale
   - sanitisierten Kontext
   - geclampte Werte
   - geclampte Confidence
7. Interne Helfer implementieren:
   - `_clamp_float()`
   - `_safe_str()`
   - `_json_safe()`
   - `_normalize_signal()`
8. `RewardLogger` lazy importieren oder robust importieren.
9. Keine Abhängigkeit nach oben einbauen.
10. Kein DB-Schema ändern.

### Akzeptanzkriterien

- `python3 -m py_compile core/utility.py` erfolgreich.
- Import-Smoke-Test erfolgreich:

```bash
cd /opt/ai/oroma; PYTHONPATH=/opt/ai/oroma python3 - <<'PY'
from core.utility import UtilitySignal, emit
s = UtilitySignal(source="test/utility_smoke", bahn="test", value=0.25, confidence=0.5, context={"ok": True}, tag="utility.smoke")
print(emit(s))
PY
```

- Bei DBWriter aktiv: kein lokaler SQLite-Fallback.
- Bei nicht serialisierbarem Kontext: kein Crash, sondern sichere Darstellung.
- Bei sanitisiertem Kontext erhöht sich `utility_context_sanitized`.
- `get_counters()` liefert eine Kopie der internen Counter.
- `reward` wird als `value * confidence` gespeichert.
- `raw` enthält `value`, `confidence`, `weighted_value`, `bahn`, `source`, `context`.

### Risiken

- Import-Zyklen, wenn `utility.py` zu viel importiert.
- Stille Fehler, wenn `RewardLogger` nicht erreichbar ist.
- Kontext-JSON kann unerwartete Typen enthalten.

### Gegenmaßnahmen

- Nur `core.reward` verwenden.
- Keine Imports aus Collector, DreamWorker, PTZ, Vision oder Audio.
- Fehler sichtbar printen/loggen.
- `_json_safe()` defensiv bauen.
- Sanitization nicht still durchführen, sondern `utility_context_sanitized` erhöhen.

---

## Phase 2 – `tools/ptz_motor_reward_collector.py`

### Ziel

Der aktuelle PTZ-Motor-Worker bleibt DB-frei und schnell.
Ein separater Collector liest langsam den Motor-State und erzeugt Utility-Signale.

### Ausgangspunkt

Aktueller aktiver Worker:

```text
tools/ptz_motor_worker.py
```

Der Worker schreibt State/Logs, aber keine Rewards.
Das ist korrekt für den Hot-Loop.

Der Collector soll lesen:

```text
data/state/ptz_motor_state.json
```

und über `core.utility.emit()` schreiben.

### Maßnahmen

1. Neue Datei anlegen:

```text
tools/ptz_motor_reward_collector.py
```

2. Kein Eingriff in `tools/ptz_motor_worker.py` in Stufe 2.
3. Collector unterstützt:
   - `--once`
   - `--interval-sec`
   - `--state-path`
   - `--verbose`
4. Collector merkt vorherigen State in eigener kleiner State-Datei, z. B.:

```text
data/state/ptz_motor_reward_collector_state.json
```

5. Collector berechnet nur einfache, robuste Utility-Signale.

### Erste PTZ-Utility-Quellen

```text
ptz_motor/follow_gain
ptz_motor/center_gain
ptz_motor/target_stability
ptz_motor/eye_pair_hold_gain
ptz_motor/wasted_motion_penalty
ptz_motor/reversal_penalty
ptz_motor/cmd_fail_penalty
```

### Beispielhafte Metrik-Ideen

`center_gain`:

```text
before_dist - after_dist
```

Normalisierung:

```text
value = clamp((before_dist - after_dist) / max(before_dist, epsilon), -1, +1)
```

`target_stability`:

```text
positive value, wenn target_conf stabil bleibt oder steigt
negative value, wenn target_conf deutlich fällt
```

`cmd_fail_penalty`:

```text
value < 0, wenn action geplant war, aber cmd_ok false ist
```

`wasted_motion_penalty`:

```text
value < 0, wenn viel Bewegung ohne Distanz-, Confidence- oder Stability-Gewinn erzeugt wurde
```

### Akzeptanzkriterien

- Worker bleibt unverändert DB-frei.
- Collector kann mit `--once` laufen.
- Collector schreibt bei valide verändertem State mindestens ein Utility-Signal.
- Collector schreibt nichts, wenn State unverändert oder unbrauchbar ist.
- Fehler im State-JSON werden sichtbar geloggt.
- Keine DB-Locks durch Hot-Loop.
- `python3 -m py_compile tools/ptz_motor_reward_collector.py` erfolgreich.

### Risiken

- JSON-State wird gerade geschrieben, während Collector liest.
- State-Felder können fehlen.
- Zu viele Rewards bei hoher Frequenz.

### Gegenmaßnahmen

- Robust lesen, bei JSON-Decode-Fehler später erneut versuchen.
- Alle Felder optional behandeln.
- Default-Intervall konservativ wählen, z. B. 5 bis 15 Sekunden.
- Nur bei echter Veränderung loggen.

---

## Phase 3 – DreamWorker-Aggregation für `ptz_motor`

### Ziel

DreamWorker soll Utility-Signale aus `ptz_motor/...` nach `policy_rules` verdichten.

### Ausgangspunkt

`core/dream_worker.py` enthält bereits Muster für PTZ-Rewards:

```text
ptz/attention_gain  → namespace='ptz_att'
ptz/motion_focus    → namespace='ptz_motion'
ptz/audio_probe     → namespace='ptz_probe'
```

Diese Muster sollen nicht ersetzt, sondern additiv erweitert werden.

### Maßnahmen

1. Neue DreamWorker-Methode ergänzen, z. B.:

```text
_ptz_policy_from_motor_utility()
```

2. Quellen lesen:

```sql
SELECT id, created_at, source, reward, raw
FROM rewards_log
WHERE raw LIKE '%"utility":true%'
  AND source LIKE 'ptz_motor/%'
  AND id > ?
ORDER BY id ASC
LIMIT ?
```

3. Checkpoint in `dream_state`, z. B.:

```text
ptz_motor_policy:last_reward_id
```

4. Namespace in `policy_rules`:

```text
ptz_motor
```

5. `state_hash` und `action` aus `raw.context` ableiten, falls vorhanden.
6. Wenn `state_hash` fehlt: konservativer Fallback, z. B. `legacy` oder `unknown`.
7. DBWriter-Pfad einhalten.

### Akzeptanzkriterien

- Bestehende PTZ-Dream-Methoden bleiben unverändert nutzbar.
- Neue Methode ist per ENV aktivierbar/deaktivierbar:

```text
OROMA_PTZ_MOTOR_POLICY_DREAM_ENABLE=1
```

- Checkpoint verhindert doppelte Verarbeitung.
- `policy_rules.namespace='ptz_motor'` erhält neue Einträge.
- Keine direkte Motorsteuerung durch diese Policy in Phase 3.

### Risiken

- Zu grobe `state_hash`-Fallbacks erzeugen wenig brauchbare Policy.
- Utility-Signale ohne `action` sind nicht policy-fähig.

### Gegenmaßnahmen

- Collector soll `action`, `reason`, `state_hash` oder ableitbare Kontextfelder liefern.
- DreamWorker soll unbrauchbare Zeilen zählen und als Metrik sichtbar machen.

---

## Phase 4 – Monitoring / UI / Diagnose

### Ziel

Utility-Signale müssen sichtbar werden, bevor sie Verhalten beeinflussen.

### Maßnahmen

Minimal zunächst per CLI/SQL:

```sql
SELECT source, COUNT(*) AS n, ROUND(AVG(reward), 4) AS avg_reward,
       ROUND(MIN(reward), 4) AS min_reward, ROUND(MAX(reward), 4) AS max_reward
FROM rewards_log
WHERE raw LIKE '%"utility":true%'
GROUP BY source
ORDER BY n DESC;
```

Optional später UI:

```text
/learning/ oder eigene Utility-Kachel
```

Anzeigen:

- Utility-Signale 24h
- Top Quellen
- Ø Reward je Quelle
- positive/negative Ratio
- PTZ center_gain 24h
- cmd_fail_penalty 24h
- Collector-Heartbeat

### Akzeptanzkriterien

- Operator kann sehen, ob Utility-Signale entstehen.
- Operator kann sehen, ob mehr positive oder negative Signale erzeugt werden.
- Fehler/Skipped-Zähler sind sichtbar.

---

## Phase 5 – PTZ-Policy als weicher Bias

### Status

PTZ Phase 5a ist umgesetzt: `tools/ptz_motor_worker.py` kann `policy_rules.namespace='ptz_motor'` optional als weichen Bias in PTZ-Entscheidungen einfließen lassen.

### Ziel

Nachdem genügend Daten gesammelt wurden, darf `policy_rules.namespace='ptz_motor'` optional als weicher Bias in PTZ-Entscheidungen einfließen.

### Nicht-Ziel

Die Policy darf nicht sofort die Motorik übernehmen.

### Grundregel

```text
Reflex bleibt primär.
Policy darf nur bei gleichwertigen Kandidaten leicht bevorzugen.
```

Mögliche Bias-Punkte:

- Scan-Richtung
- Probe-Richtung
- Orient-Entscheidung
- Target-Hold-Verlängerung
- Amount-Tuning in sicherem Rahmen

### Mindestbedingungen

Policy darf nur wirken, wenn:

```text
n >= Mindestanzahl
abs(q) >= Mindestschwelle
last_ts nicht zu alt
cmd_fail_penalty nicht auffällig
```

### Akzeptanzkriterien

- PTZ bleibt sicher und ruhig.
- Kein Zucken durch Policy.
- Policy kann komplett per ENV deaktiviert werden.
- UI/Logs/State zeigen, ob Policy-Bias aktiv war.
- Erster Starttest mit Bias deaktiviert: `py_compile` OK, Worker `active (running)`, `fail=0`.

### PTZ Phase 5a Live-Status 2026-05-29

```text
OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=0 im ersten Starttest
policy_ns=ptz_motor
policy_w=0.080
policy_min_n=5
policy_min_q=0.050
policy_refresh=60.0s
```

Der Codepfad ist startstabil. Der aktive Rückfluss wird erst mit `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=1` bewertet.

---

## Phase 6 – Vision / Audio / Crossmodal analog

### Ziel

Nach PTZ wird das Utility-Prinzip auf weitere Bahnen angewandt.

### Vision

Mögliche Quellen:

```text
vision/feature_stability
vision/reuse_gain
vision/noise_reduction
vision/scenegraph_reuse
```

### Audio

Mögliche Quellen:

```text
audio/probe_confirmed
audio/event_repeatability
audio/audio_visual_confirmed
audio/false_probe_penalty
```

### Crossmodal

Mögliche Quellen:

```text
crossmodal/audio_visual_binding
crossmodal/prediction_success
crossmodal/bridge_confirmed
crossmodal/bridge_unused_decay
```

### Sprache später

Mögliche Quellen:

```text
language/semantic_reuse
language/command_success
language/context_match
language/response_success
```

### Akzeptanzkriterien

- `core/utility.py` bleibt unverändert.
- Neue Bahnen nutzen dieselbe API.
- Domänenlogik bleibt in Collectors oder jeweiligen Modulen.

---

## 7. Dokumentationsstrategie

Vorgeschlagene neue Dokumente:

```text
docs/ROADMAP_STRUCTURED_PLASTICITY_UTILITY.md
docs/core/76_utility_signal.md
```

`docs/ROADMAP_STRUCTURED_PLASTICITY_UTILITY.md`:

- Diese Roadmap.
- Phasenplan.
- Architekturentscheidungen.
- Akzeptanzkriterien.

`docs/core/76_utility_signal.md`:

- Später nach Implementierung.
- Endgültige API-Beschreibung.
- Beispiele für Collector.
- Source-Konventionen.
- DBWriter-Hinweise.

---

## 8. Patch-Reihenfolge

Empfohlene Reihenfolge:

```text
Patch 1:
  docs/ROADMAP_STRUCTURED_PLASTICITY_UTILITY.md

Patch 2:
  core/utility.py
  tests oder Smoke-Test-Kommandos

Patch 3:
  tools/ptz_motor_reward_collector.py
  optional systemd unit/timer nur wenn gewünscht

Patch 4:
  core/dream_worker.py Erweiterung für ptz_motor

Patch 5:
  Monitoring/SQL/UI-Diagnose

Patch 6:
  PTZ Phase 5a Policy-Bias in tools/ptz_motor_worker.py – umgesetzt, initial py_compile/start getestet

Patch 7:
  Vision/Audio/Crossmodal-Collector
```

---

## 9. Patch-Gate für spätere Code-Patches

Für jeden Code-Patch gilt:

1. Aktuelle ZIP vollständig entpacken.
2. Ziel-Dateien vollständig lesen.
3. Minimal-invasiv patchen.
4. Keine bestehenden Routinen entfernen.
5. Keine Platzhalter.
6. Keine stillen Fehler.
7. Python-Dateien kompilieren:

```bash
python3 -m py_compile <datei.py>
```

8. Import-/Smoke-Test ausführen.
9. Gepatchte Datei vollständig re-readen.
10. Diff erzeugen und prüfen.
11. Kleines Patch-ZIP bereitstellen.

---

## 10. Erste konkrete Entscheidung

Die erste produktive Änderung sollte sein:

```text
core/utility.py
```

Nicht:

```text
PTZ-Motorik ändern
DreamWorker ändern
Pruning ändern
DB-Schema ändern
```

Begründung:

- `core/utility.py` ist die stabile Grundlage.
- Sie ist additiv.
- Sie berührt keine Hot-Loops.
- Sie nutzt vorhandene Infrastruktur.
- Sie erzwingt keine Verhaltensänderung.
- Sie schafft eine einheitliche Sprache für lokale Nützlichkeit.

---

## 11. Zusammenfassung

Die Roadmap führt ORÓMA von vorhandenen Rewards und Reflexen zu einem expliziten lokalen Utility-System.

Kernidee:

```text
snap.py    → Was wurde erlebt?
reward.py  → Wo wird es gespeichert?
utility.py → War es lokal nützlich?
```

Dadurch entsteht die fehlende Brücke zwischen:

```text
Kandidatenverbindungen
```

und:

```text
zielgerichteter Verstärkung / Abschwächung / Pruning
```

Der Umbau ist realistisch, weil ORÓMA bereits besitzt:

- `rewards_log`
- `RewardLogger`
- DBWriter-kompatible Schreibwege
- DreamWorker-Aggregation
- PTZ-State mit vielen verwertbaren Signalen
- Policy-Regeln
- Forgetting/Kompression/MetaSnaps

Die wichtigste Architekturregel bleibt:

```text
utility.py bleibt domänenfrei.
Alle konkreten Bedeutungen entstehen in Collectors und DreamWorker.
```



---

## 12. Nachtrag 2026-05-29 – PTZ Phase 5a umgesetzt

Die Roadmap-Stufe „PTZ-Policy als weicher Bias“ wurde als konservativer Phase-5a-Patch umgesetzt.

### Ergebnis

```text
Reward Collector erzeugt ptz_motor/* Rewards.
DreamWorker verdichtet diese nach policy_rules namespace='ptz_motor'.
PTZ Motor Worker kann diese Regeln read-only als weichen Bias laden.
Safety-/Reflexlogik bleibt führend.
Bias ist per ENV abschaltbar.
```

### Erster Live-Test

```text
python3 -m py_compile tools/ptz_motor_worker.py  → OK
oroma-ptz-motor-worker.service                  → active (running)
fail                                            → 0
policy_bias                                     → 0 im ersten Test
```

### Nächster Roadmap-Schritt

Nicht sofort weiter eskalieren. Zuerst:

```text
1. Bias aktivieren mit sehr kleinem Gewicht.
2. 24h beobachten.
3. wasted_motion_penalty, center_gain, target_stability und cmd_fail_penalty vergleichen.
4. Erst bei stabilem Verhalten UI-/Dashboard-Sichtbarkeit ergänzen.
```

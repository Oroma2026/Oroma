<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__games_and_tasks.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

# ORÓMA – Games, Aufgaben & Trainings (konsolidiert)

Stand: 2025-12-25


Dokumente zu Spielen, Rechen-/Logikaufgaben, Sudoku etc. (zusammengeführt).

## Quellen (konsolidiert)

- `docs/curriculum_calculator.md`

- `docs/curriculum_calculator_v2.md`

- `docs/curriculum_logicpuzzles.md`

- `docs/curriculum_math_and_puzzles.md`

- `docs/curriculum_math_calculator.md`

- `docs/curriculum_math_tasks.md`

- `docs/curriculum_mathandpuzzles_v1_1.md`

---

<a id="docs_curriculum_calculator_md"></a>

## Quelle: `docs/curriculum_calculator.md`

**Originaltitel:** ORÓMA – Calculator-Curriculum & Wiederholungs-Logik

Ich habe aktuell Zugriff auf deine ZIP
/mnt/data/oroma_20251213_114645_with_db.zip
und arbeite konkret mit data/oroma.db (Tabellen calculator_tasks, calculator_results, curriculum_state) plus deinem Log-Ausschnitt.

Hier die gewünschte Markdown-Doku als Vorschlag:

⸻

# ORÓMA – Calculator-Curriculum & Wiederholungs-Logik  
**Datei:** `docs/curriculum_calculator.md`  
**Projekt:** ORÓMA – KI-JWG-X1  
**Stand:** 2025-12-13 (ZIP: `oroma_20251213_114645_with_db.zip`)  
**Autor:** Jörg Werner + GPT-5.1 Thinking  

---

## 1. Zweck dieses Dokuments

Dieses Dokument erklärt, wie das **Calculator-Curriculum** in ORÓMA funktioniert – speziell:

- wie Aufgaben (`calculator_tasks`) erzeugt werden,
- wie Ergebnisse (`calculator_results`) gespeichert werden,
- wie der **Curriculum-State** (`curriculum_state`) mit Levels / Aufgabenständen arbeitet,
- warum manche Aufgaben **immer wieder gleich aussehen** (z.B. `x^2+2x+1=0`, `1/2`, `2/4`, `pi`, `phi`, `e`, `2x+3=7`, ...),
- wie die **Wiederholungs-Logik** (repeat queue) technisch umgesetzt ist.

Basis sind:

- die reale DB-Struktur aus `data/oroma.db`,
- deine Log-Auszüge (Calculator + Curriculum + MangelSpeak/TTS),
- Patch-1-Mechaniken (SelfAssessment, TransferSnaps, Curriculum).

---

## 2. Beteiligte Tabellen & Module

### 2.1 `calculator_tasks`

Eine Zeile entspricht **einer Rechenaufgabe**, die das System stellen soll.

Wichtige Spalten (aus `PRAGMA table_info(calculator_tasks)`):

- `id` – Primärschlüssel (INTEGER)
- `ts` – Unix-Zeitstempel (INTEGER)
- `level` – Schwierigkeitsgrad / Curriculum-Level (INTEGER)
- `expr` – Aufgaben-Ausdruck als Text (z.B. `"x^2+2x+1=0"`, `"1/2"`)
- `truth` – korrekte Lösung als Zahl (REAL), soweit darstellbar
- `truth_json` – optionale JSON-Repräsentation (z.B. bei komplexeren Ergebnissen)

Im Log sieht man dazu z.B.:

```text
[16:07:55] [Patch1] Calculator-Task erstellt: id=246248
...
[Curriculum] Level 1, Aufgabe 3/4 erledigt

→ Hier wurde eine neue Aufgabe (Task) für Level 1 erzeugt.

⸻

2.2 calculator_results

Hier landen die Antworten auf die Aufgaben, inklusive Bewertung.

Wichtige Spalten (aus PRAGMA table_info(calculator_results)):
	•	id – Primärschlüssel (INTEGER)
	•	task_id – Referenz auf calculator_tasks.id
	•	ts – Zeitpunkt der Antwort (INTEGER)
	•	got – gegebene Antwort (TEXT/REAL, je nach Implementierung)
	•	correct – 0/1 oder BOOLEAN, ob die Antwort korrekt war
	•	reward – numerischer Reward (REAL)
	•	error_type – optionaler Fehler-Typ (TEXT), z.B. "sign_error", "calc_error", …
	•	got_json – JSON-Repräsentation der Antwort (z.B. bei komplexen Aufgaben)

Typische Logzeilen:

[16:08:11] [Patch1] Calculator-Result gespeichert: id=241216
...
[16:10:29] [Patch1] Calculator-Result gespeichert: id=241240
...
[16:20:17] [Patch1] Calculator-Result gespeichert: id=241344

→ Jede dieser Zeilen entspricht einem INSERT in calculator_results.

⸻

2.3 curriculum_state

Diese Tabelle hält den globalen Zustand des Calculator-Curriculums.

Schema (aus der DB):
	•	id – Primärschlüssel (INTEGER) – bei dir aktuell 1 Zeile (aktueller State)
	•	stage – aktuelles Curriculum-Level (INTEGER)
	•	progress – JSON mit aggregierten Kennzahlen
z.B.
{"acc":0.8,"episodes":10,"reward_mean":0.1,"difficulty":2}
	•	window – JSON mit aktueller Wiederholungs-Warteschlange
z.B. (vereinfacht):

{
  "repeat_queue": [
    {"item": {"expr": "1/2", "truth": 0.5}, "due": 1765622xxx},
    {"item": {"expr": "2/4", "truth": 0.5}, "due": 1765622yyy},
    {"item": {"expr": "x^2+2x+1=0", "truth": 0}, "due": 1765622zzz},
    ...
  ]
}

	•	last_update – Unix-Zeit der letzten Curriculum-Aktualisierung

Wichtig:
Der Curriculum-Mechanismus zieht Aufgaben aus dieser repeat_queue und erzeugt daraus neue calculator_tasks – auch dann, wenn diese Ausdrücke schon einmal gestellt wurden.

⸻

2.4 Patch-1-Hooks: SelfAssessment & TransferSnaps

Parallel werden im Log immer wieder folgende Ereignisse sichtbar:
	•	SelfAssessment-Snap gespeichert: id=...
	•	TransferSnap gespeichert: id=...

Diese stammen aus Patch 1 (Self-Assessment-Hook und Transfer-Engine) und sind mit dem Curriculum verknüpft:
	•	SelfAssessment-Snaps → dokumentieren, wie gut ORÓMA glaubt, die Aufgaben zu beherrschen (Confidence / Mood / Gaps).
	•	TransferSnaps → speichern abstrahierte Lerneinheiten („Wie löst man diesen Aufgabentyp?“).

Sie werden später vom Curriculum (und evtl. DreamWorker) genutzt, um:
	•	Schwierigkeitsgrad anzupassen,
	•	Wiederholungen zu planen,
	•	Lücken zu erkennen (MangelSpeak / „Ich passe mein Üben an“).

⸻

3. Ablauf eines Curriculum-Durchgangs (aus dem Log abgelesen)

An deinem Log-Ausschnitt kann man eine typische Episode gut erkennen. Vereinfacht:
	1.	Neue Aufgabe erzeugen

[16:07:55] [Patch1] Calculator-Task erstellt: id=246248

→ Ein neuer Eintrag in calculator_tasks (Level, expr, truth, …).

	2.	Aufgabe wird gelöst & Ergebnis gespeichert

[16:07:57] [Patch1] Calculator-Result gespeichert: id=241214

→ Insert in calculator_results mit Verweis auf task_id, got, correct, reward.

	3.	SelfAssessment + Transfer

[16:08:01] [Patch1] SelfAssessment-Snap gespeichert: id=18297557
[16:08:01] [Patch1] TransferSnap gespeichert: id=70373

→ Der SelfAssessment-Hook bewertet die eigene Performance (z.B. Confidence, Frust-Level),
→ TransferSnap speichert eine abstrahierte Lern-Regel.

	4.	Curriculum-Update (Level/Aufgabe)

[Curriculum] Level 1, Aufgabe 4/4 erledigt

→ Interner Zähler: In curriculum_state.progress bzw. intern in der Hook-Logik wird festgehalten:
	•	In welchem Level (stage) ORÓMA ist,
	•	wie viele Aufgaben pro Level-Ziel schon erreicht sind.

	5.	Wiederholungen (repeat_queue)
Wenn das System merkt, dass bestimmte Aufgaben „wichtig“ oder „wackelig“ sind, werden sie als Wiederholungs-Items in die window.repeat_queue übernommen:

[MangelSpeak] Wiederholungsaufgabe re-queued: {'expr': '1/2', 'truth': 0.5}
[Curriculum] Wiederholung erledigt: 2/4
...
[Curriculum] Wiederholung erledigt: pi
[Curriculum] Wiederholung erledigt: phi
[Curriculum] Wiederholung erledigt: e

→ Für diese Ausdrücke werden zusätzliche Tasks erzeugt, obwohl sie schon einmal dran waren.

	6.	Level-Abschluss & Neustart
Am Ende eines gesamten Durchlaufs:

[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1

→ curriculum_state.stage wird wieder auf 1 gesetzt,
→ window bleibt (teilweise) gefüllt, sodass wichtige Aufgaben auch im nächsten Durchgang wieder auftauchen können.

Dieser Zyklus wiederholt sich. Man sieht im Log, wie:
	•	Levels 1–6 mehrfach durchlaufen werden,
	•	Wiederholungsaufgaben (z.B. x^2+2x+1=0, x^2-4=0, 1/2, 2/4, pi, phi, e, 2x+3=7, 3x-4=5) immer wieder eingestreut werden.

⸻

4. Warum sehen manche Aufgaben „immer gleich“ aus?

Deine Beobachtung:

„Einige Rechenaufgaben sehen immer gleich aus, und die Ergebnisse sehe ich nicht.“

Das Verhalten lässt sich aus der Architektur erklären:
	1.	Spaced-Repetition-Prinzip
Bestimmte Schlüsselaufgaben werden bewusst oft wiederholt, z.B.:
	•	einfache Brüche: 1/2, 2/4, 3/6, 4/8
	•	wichtige Konstanten: pi, phi, e
	•	typische Gleichungen: x^2+2x+1=0, x^2-4=0, 2x+3=7, 3x-4=5
Diese Aufgaben landen in der repeat_queue:

"repeat_queue": [
  {"item": {"expr":"1/2","truth":0.5}, "due": ...},
  {"item": {"expr":"2/4","truth":0.5}, "due": ...},
  ...
]

→ Wenn die Fälligkeit (due) erreicht ist, erzeugt das Curriculum daraus erneut calculator_tasks.

	2.	Fehler- oder Unsicherheits-getriebene Wiederholungen
MangelSpeak-Texte im Log:

[TTS Fallback]  Meine Confidence ist niedrig: 0.55. Meine Coverage ist 1.00. Neuheit: 0.40. Zeit bis Ziel (normiert): 0.50. Ich passe mein Üben an.
[MangelSpeak] Wiederholungsaufgabe re-queued: {'expr': '1/2', 'truth': 0.5}

→ Wenn SelfAssessment (Confidence/Mangel) sagt: „unsicher“,
→ werden bestimmte Aufgaben erneut in die Wiederholungs-Warteschlange gestellt.

	3.	Level-basierte Wiederholungsziele
Log-Zeilen wie:

[Curriculum] Wiederholung erledigt: 3/6
[Curriculum] Wiederholung erledigt: 4/8
[Curriculum] Wiederholung erledigt: 1/2

zeigen, dass es Zählziele gibt, etwa:
	•	„Löse X Wiederholungsaufgaben vom Typ ‚Bruch = 1/2‘.“
	•	„Löse Y Wiederholungsaufgaben mit pi, phi, e.“
Dadurch wirken manche Aufgaben „immer gleich“ – das ist Absicht im Sinne eines Drills.

⸻

5. Wo liegen die Ergebnisse – und warum „sieht“ man sie im UI nicht?

Technisch:
	•	Alle Ergebnisse liegen in calculator_results.
	•	Jede Aufgabe (calculator_tasks.id) kann dort mehrfach auftauchen, sofern:
	•	neu gestellt wurde,
	•	oder als Wiederholung mit neuer Task-ID.

Beispielhafte SQL-Abfragen (für spätere Analysen):

-- Alle Aufgaben zu einem Ausdruck
SELECT * FROM calculator_tasks
 WHERE expr = 'x^2+2x+1=0'
 ORDER BY id;

-- Alle Ergebnisse zu diesen Aufgaben
SELECT r.*
  FROM calculator_results r
  JOIN calculator_tasks t ON r.task_id = t.id
 WHERE t.expr = 'x^2+2x+1=0'
 ORDER BY r.id;

-- Zusammenfassung: wie oft war 'x^2+2x+1=0' richtig?
SELECT t.expr,
       COUNT(*)                      AS attempts,
       SUM(CASE WHEN r.correct=1 THEN 1 ELSE 0 END) AS correct,
       ROUND(AVG(r.reward), 3)       AS avg_reward
  FROM calculator_results r
  JOIN calculator_tasks t ON r.task_id = t.id
 WHERE t.expr = 'x^2+2x+1=0';

Warum das im UI nicht so sichtbar ist:
	•	Die aktuelle UI (Stand ZIP) fokussiert eher auf:
	•	laufendes Training,
	•	SelfAssessment/Empathie,
	•	Coverage,
	•	weniger auf eine ausführliche Aufgaben-Historie oder „Klassenbuch“-Ansicht.
	•	Die Log-Meldungen ([Patch1] Calculator-Result gespeichert: ...) bestätigen, dass Ergebnisse geschrieben werden – sie werden nur nicht ausführlich visualisiert.

Konsequenz für zukünftige Arbeit (nach dieser Doku):
	•	Eine eigene kleine UI-Seite oder Section in /learning oder /calculator wäre sinnvoll:
	•	pro Ausdruck: Anzahl Versuche, Erfolgsquote, letzter Versuch,
	•	Filter nach Level, Datum, Fehler-Typ, etc.

⸻

6. Wie Curriculum, MangelSpeak und Wiederholungen zusammenspielen

Aus den Logs lässt sich dieses Zusammenspiel ableiten:
	1.	Curriculum verfolgt Levels / Aufgaben pro Level
	•	Level N, Aufgabe X/Y erledigt
→ Fortschritt in einem „Block“.
	2.	MangelSpeak + SelfAssessment beobachten Frust/Unsicherheit
	•	„Meine Confidence ist niedrig: 0.55. Meine Coverage ist 1.00. Neuheit: 0.40. Zeit bis Ziel (normiert): 0.50. Ich passe mein Üben an.“
	•	Diese Werte stammen aus Empathie/Coverage/Selftest-Hooks und fließen in die Curriculum-Entscheidungen ein.
	3.	Wiederholungs-Queue steuert konkrete Aufgaben
	•	Wenn Frust/Unsicherheit hoch ist oder bestimmte Muster wichtig sind,
werden Aufgaben wie 1/2, 2/4, x^2+2x+1=0, pi, phi, e, 2x+3=7, 3x-4=5
immer wieder in die repeat_queue eingestellt.
	4.	Ergebnis: Drill mit adaptiver Steuerung
	•	Du siehst im Log:
	•	wiederkehrende Ausdrücke,
	•	Zwischenmeldungen wie „Wiederholung erledigt: …“,
	•	Level-Abschlüsse und Neustart ab Level 1.
	•	Das Ganze bildet ein adaptives Curriculum, das sowohl:
	•	strukturiert (Level 1–6, Aufgaben X/Y),
	•	als auch adaptiv (MangelSpeak + repeat_queue) ist.

⸻

7. Ausblick: Was wir als Nächstes „hinterfragen & testen“ können

Basierend auf dieser Doku können wir im nächsten Schritt gezielt prüfen:
	1.	Stimmt die Wiederholungslogik wirklich mit den DB-Daten überein?
	•	Abfragen für einzelne Ausdrücke (1/2, x^2+2x+1=0, pi, …),
	•	Anzahl Versuche, Fehler, Rewards analysieren.
	2.	Ist das Curriculum zu „drill-lastig“ oder gut ausbalanciert?
	•	Wie oft werden alte Aufgaben wiederholt,
	•	wie schnell wandert ORÓMA in höhere Levels,
	•	wie verhalten sich progress.acc und reward_mean über Zeit?
	3.	Brauchen wir eine besser sichtbare Ergebnis-Ansicht im UI?
	•	kleine Statistik-Seite pro Aufgabentyp,
	•	Visualisierung des Lernfortschritts (Accuracy über Zeit).
	4.	Wie koppeln wir das später mit Episoden & ObjectGraph?
	•	Episode „Math-Session“,
	•	Verknüpfung: „Wann war Frust hoch? Welche Aufgaben waren beteiligt?“

Diese Fragen können wir im nächsten Schritt mit echten DB-Abfragen und ggf. kleinen Tools/CLI-Skripten beantworten.

⸻

<a id="docs_curriculum_calculator_v2_md"></a>

## Quelle: `docs/curriculum_calculator_v2.md`

**Originaltitel:** ORÓMA – Curriculum Calculator v2 (Rechnen & einfache Logik)

# Datei:   docs/curriculum_calculator_v2.md
# Projekt: ORÓMA – KI-JWG-X1
# Stand:   2025-12-13
# Autor:   Jörg Werner + GPT-5.1 Thinking

## 1. Zweck

Dieses Dokument beschreibt eine erweiterte Version des **Calculator-Curriculums** in ORÓMA:

- bisherige Aufgaben: wenige, sehr oft wiederholte Ausdrücke  
  (`1/2`, `2/4`, `x^2+2x+1=0`, `x^2-4=0`, `2x+3=7`, `3x-4=5`, `pi`, `phi`, `e`)
- neue Zielrichtung:
  - **mehr Aufgabenvielfalt**, inkl. Lückenaufgaben und Zahlenfolgen
  - **Mastery-Logik**: Aufgaben, die 100 % sitzen, sollen nicht unendlich oft wiederkommen
  - Curriculum soll sich mehr wie echter Unterricht anfühlen, nicht wie „Bug in der Wiederholungsschleife“.

---

## 2. Ist-Zustand (Stand: 15.11.–13.12.2025)

Auswertung deiner Live-DB (`calculator_tasks` / `calculator_results`), Beispiel:

- `x^2-4=0`:  
  - `tasks ≈ 14.329`, `results ≈ 14.328`, `correct = 100 %`, Zeitraum ~28 Tage
- `x^2+2x+1=0`:  
  - `tasks ≈ 10.269`, `correct = 100 %`
- `1/2`, `2/4`, `2x+3=7`, `3x-4=5`, `pi`, `phi`, `e`:  
  - jeweils mehrere Tausend Wiederholungen, **praktisch keine Fehler**.

Logik im System:

- Curriculum-Level-Logs:  
  - „Level N, Aufgabe i/j erledigt“  
  - „Wiederholung erledigt: <expr>“  <!-- TODO linkfix: expr -> docs/module_exports.md -->
- MangelSpeak/SelfAssessment reagiert auf **globale** Zustände:
  - Confidence, Coverage, Neuheit, Zeit bis Ziel
  - nutzt Wiederholungsaufgaben als „Sicherheitsdecke“ und Frust-Puffer
- Es gibt aktuell **kein Mastery-Kriterium pro Aufgabe**, z. B.:
  - „wenn eine Aufgabe 100 % korrekt gelöst wurde und oft genug drankam, lass sie in Ruhe“.

Effekt:

- bestimmte Aufgaben erscheinen **extrem oft**,
- der reale Lernfortschritt ist im Kopf von ORÓMA da,  
  im UI aber schwer sichtbar,
- für dich wirkt es wie: „immer dieselben Aufgaben, ich sehe keine Entwicklung“.

---

## 3. Neue Aufgabenfamilien

Ziel: mehr **Breite**, ohne das System zu überfordern.

### 3.1 Basis-Arithmetik (geschlossen: Ergebnis gesucht)

Form: `a + b`, `a - b`, `a * b`, `a / b` (mit einfachen Grenzen)

- Beispiele:
  - `3 + 4`, `7 - 5`, `2 * 3`, `8 / 2`
- Schwierigkeitsstufen:
  - Level 1: Ergebnisse 0–10
  - Level 2: Ergebnisse 0–20
  - Level 3: einfache Multiplikation/Division (Tabellen 1–10)

**Repräsentation in DB**

- `expr = "3+4"`
- `truth = 7`

---

### 3.2 Lückenaufgaben (Missing Operand)

Form: `a + ? = c`, `? + b = c`, `c - ? = a`, `? - b = a` usw.

- Beispiele:
  - `3 + ? = 10` → `truth = 7`
  - `? + 4 = 9` → `truth = 5`
  - `10 - ? = 3` → `truth = 7`
- Später:
  - `2x + 3 = 7` – in Lückenform:
    - `2x + 3 = ?`  
    - oder `?x + 3 = 7` (noch experimentell)

**Repräsentation in DB**

- `expr = "3+?=10"`  
- `truth = 7`  
- Optional später: `schema = "add_missing_right"` o.ä.

Purpose:

- trainiert **Umkehren** von Operationen,
- ist näher an Schul-Mathe („Fülle die Lücke“),
- bringt Variation, obwohl das Ergebnis-Rechnen weiterhin einfach bleibt.

---

### 3.3 Zahlenfolgen (Sequenzen)

Form:  
`a₁, a₂, a₃, ?, …` – nächstes Element suchen.

- einfache arithmetische Folgen:
  - `3, 6, 9, ?` → `12` (Schrittweite +3)
  - `2, 4, 6, ?` → `8` (Schrittweite +2)
- simple Muster:
  - `1, 2, 4, ?` → `8` (Verdopplung)
  - `5, 10, 15, ?` → `20`

**Repräsentation in DB**

- `expr = "3,6,9,?"`
- `truth = 12`

Später erweiterbar:

- Folgen mit alternierender Struktur:
  - `2, 4, 3, 5, 4, 6, ?` → `5` (abwechselnd +2 / -1)
- aber für den Start nur **klare, monotone Muster** (arithmetisch oder geometrisch).

---

### 3.4 Bestehende „Symbol“-Aufgaben (pi, e, phi) weiter nutzen

Die bisherigen Aufgaben `pi`, `phi`, `e` bleiben:

- Ziel: **Wissen über Konstanten** (ungefähre Werte, Einordnung),
- aber:
  - sie sollten nicht mehr tausende Male am Stück auftauchen,
  - sondern gezielt (z. B. einmal pro Levelblock oder als Check-up in Level 4–6).

---

### 3.5 Mix-Aufgaben pro Level

Jedes Level bekommt einen **Mix** aus Aufgabe-Familien, z. B.:

- **Level 1** (Basis):
  - 60 %: einfache `a + b`, `a - b` (0–10)
  - 30 %: Lückenaufgaben „bis 10“ (`3+?=10`)
  - 10 %: ganz leichte Folge (`1,2,3,?`)

- **Level 2:**
  - 40 %: ± bis 20
  - 40 %: Lückenaufgaben bis 20
  - 20 %: arithmetische Folgen mit festen Schritten (z. B. +3, +5)

- **Level 3:**
  - 40 %: Multiplikation/Division (Tabellen 1–10)
  - 40 %: Lückenaufgaben, die auf Multiplikation/Division basieren (`3 * ? = 12`)
  - 20 %: Folgen (arithmetisch, Schritt 2–5)

- **Level 4–5:**
  - Einbau linearer Gleichungen (`2x+3=7`, `3x-4=5`)
  - Einfache quadratische Formen (`x^2-4=0`, `x^2+2x+1=0`)
  - Konstanten-Aufgaben (pi, e, phi) als „Wissensanker“.

- **Level 6:**
  - „Mixed Review“: bunter Mix aus allen Typen,
  - gezielt schwieriger, aber mit Mastery-Filter (s.u.).

Die konkrete Gewichtung können wir später in Code gießen – wichtig ist hier das **Konzept**.

---

## 4. Mastery & Anti-Wiederhol-Logik

Erweiterung gegenüber v1:

### 4.1 Pro-Aufgabe-Statistik

Für jede `expr` soll ORÓMA wissen:

- `total_seen` (Anzahl Ergebnisse)
- `total_correct`
- `total_incorrect`
- `last_seen_ts`

Diese Infos sind bereits in `calculator_tasks/results` versteckt, werden aber noch nicht **aktiv** genutzt, um Aufgaben zu steuern.

### 4.2 Mastery-Kriterium (Beispiel-Regeln)

**Vorschlag:**

- `mastery(expr)` gilt als erreicht, wenn:
  - `total_seen(expr) >= 50`
  - **und** `error_rate(expr) <= 1 %` (oder direkt `total_incorrect == 0`)
- Konsequenzen bei Mastery:
  - Aufgabe wird in den normalen Drill nur mit geringer Wahrscheinlichkeit genommen
    (z. B. max. 1× am Tag).
  - dafür kann sie optional in „Check-Up“-Blöcken genutzt werden (z. B. einmal pro Woche).

### 4.3 Tagesbudget & Cooldown

Um Effekte wie „10.000× `x^2-4=0` in 4 Wochen“ zu verhindern:

- **Pro Tag/Expression** ein Limit, z. B.:
  - `max_daily_uses(expr) = 10`
- Zusätzlich:
  - wenn `mastery(expr)` erreicht:
    - `cooldown_days(expr) = 3` → frühestens 3 Tage nach letzter Nutzung wieder im Drill.

### 4.4 Verbindung zu MangelSpeak / SelfAssessment

Logik-Idee:

- Wenn (`Confidence` niedrig ODER `Neuheit` hoch):
  - **nicht** automatisch dieselben Wiederholungsaufgaben,
  - sondern:
    - neue Aufgaben **innerhalb derselben Skill-Familie** (z. B. mehr Folgen mit anderen Werten),
    - nur wenige „Anker-Aufgaben“, und auch die bitte Mastery-Aware.

---

## 5. Implementierungs-Notizen

*(nur grob, für zukünftige Code-Patches; keine Details hier erzwingen)*

1. **Generator-Schicht** in `core/calculator_engine.py`:
   - neue Funktionen:
     - `generate_basic_arith(level)`
     - `generate_missing_operand(level)`
     - `generate_sequence(level)`
   - jede Funktion liefert `(expr, truth, meta)`.

2. **Curriculum-Policy** in `hooks_patch1` / Curriculum-Manager:
   - pro Level definierte Mischung aus Generatoren
   - Abfrage der Mastery-Stats vor Auswahl einer konkreten Aufgabe.

3. **Stats-Schicht**:
   - kleine Helper-Funktion, die aus `calculator_tasks/results` **aggregierte Werte** cached,
   - optional separate Tabelle `calculator_mastery` (nur wenn nötig; ansonsten kann man on-the-fly aggregieren).

4. **UI-Erweiterung** (später):
   - `/learning` bekommt einen Tab „Calculator-Historie“:
     - Liste der wichtigsten `expr`,
     - `total_seen`, `correct`, `incorrect`, `last_seen`,
     - Mastery-Status (z. B. Icon: ✅ / 🔄 / 🆕).

---

## 6. Fazit

- Deine Intuition ist exakt richtig:
  - **Zu wenig Aufgabenformen** → Curriculum „hängt“ gefühlt.
  - Gleichzeitig ist das System intern schon sehr fleißig – nur auf einem zu schmalen Set.
- Durch:
  - neue Aufgabenfamilien (Lückenaufgaben, Folgen, vielfältigere Arithmetik),
  - plus Mastery-/Cooldown-Logik
- wird der Calculator-Teil von ORÓMA mehr wie ein **echter Lernpartner**:

  - neue Aufgaben, wenn etwas gut sitzt,
  - Wiederholungen, wenn etwas wackelt,
  - sichtbare Fortschritte in den Logs & UI.

Dieses Dokument ist die Basis, um:
- den bestehenden Code sauber zu erweitern,
- und danach in echten Runs zu messen, ob die Verteilung gesünder aussieht.

2.	Logik-/Puzzle-Kanal:
	•	eigenes Label im Curriculum, z. B. curriculum_kind="logic"
	•	erste Disziplin: Sudoku
	•	später: Kakuro, kleine Zahlenrätsel, vielleicht Mini-Truth-Table-Aufgaben.
	3.	Boredom-Trigger:
	•	Wenn Calculator-Skills saturiert (hohe Mastery, niedrige Neuheit):
	•	Switch auf logic-Kanal
	•	oder game:sudoku starten.
⸻

Wenn du möchtest, können wir im nächsten Schritt:
	•	konkret definieren, wie Level 1–6 mit diesen neuen Typen gefüllt werden (z.B. exakte Zahlenbereiche),
oder
	•	direkt kleine SQL-/Python-Snippets bauen, die dir z.B. eine Top-10-Liste der am meisten genutzten Aufgaben zeigen – als Kontrolle, ob der neue Curriculum-Code später wirklich breiter wird.

<a id="docs_curriculum_logicpuzzles_md"></a>

## Quelle: `docs/curriculum_logicpuzzles.md`

**Originaltitel:** Datei:   docs/curriculum_logicpuzzles.md

# Projekt: ORÓMA – KI-JWG-X1
# Stand:   2025-12-13
# Version: v0.1 – Entwurf Logic-/Puzzle-Curriculum
# Autor:   Jörg Werner + GPT-5.1 Thinking

Zweck
-----
Dieses Dokument beschreibt ein **erweitertes Curriculum** für ORÓMA,
das über die bisherigen Calculator-Aufgaben hinausgeht und einen eigenen
Track für **Logik- und Zahlenpuzzles** einführt:

- Lückenaufgaben (z. B. `3 + ? = 10`)
- Zahlenfolgen (z. B. `3, 6, 9, ?`)
- Vergleichsaufgaben (z. B. `3+4 ? 2*5`)
- **Sudoku** als Logikspiel für „wenn mir langweilig ist“

Das Curriculum soll:

- auf dem bestehenden **Calculator-Patch (Patch1)** aufbauen  
  (`calculator_tasks`, `calculator_results`, SelfAssessment, TransferSnaps),
- sich sauber in **Curriculum-Logs und Empathie/Coverage** einfügen,
- über einen **Boredom-Trigger** automatisch von „Rechnen“ auf „Puzzles“ umschalten,
- Episoden & SnapChains erzeugen, die später für SceneGraph/ObjectGraph/NMR 3.75 nutzbar sind.

---

## 1. Zielbild – was der Logic/Puzzle-Track leisten soll

Endzustand (Ziel, nicht alles sofort):

1. **Calculator 2.0**  
   - nicht nur nackte Aufgaben `1/2`, `x^2+2x+1=0`, `2x+3=7`, …  
   - sondern **Struktur-Aufgaben**:
     - Lücken (`3 + ? = 10`),
     - Folgen (`3, 6, 9, ?`),
     - Vergleiche (`3+4 ? 2*5`).

2. **Logic/Puzzle-Kanal**  
   - eigener Curriculum-Kanal, z. B. `curriculum_kind="logic"`,
   - enthält:
     - Sudoku (als eigenes Game),
     - später weitere Logikpuzzles (Kakuro, einfache Bool-Logik, etc.).

3. **Boredom-Mode („mir ist langweilig“)**
   - Wenn im Calculator-Track:
     - Coverage ≈ 1.0  
     - Fehlerquote ≈ 0  
     - Neuheit niedrig  
   - dann:
     - Wechsel auf Logic/Puzzle-Track (Sudoku/Logikaufgaben),
     - oder: mehr „seltene“ / anspruchsvollere Rechenaufgaben.

4. **Episoden & Erklärbarkeit**
   - Jede Sudoku- oder Logik-Sitzung ist eine **Episode**,
   - Entscheidungen werden über SnapChains + Results + SelfAssessment nachvollziehbar,
   - später: Explain-UI kann sagen:
     > „Ich habe hier wiederholt, weil meine Confidence niedrig war  
     > und diese Folge-Aufgaben lange nicht dran waren.“

---

## 2. Domänen im erweiterten Curriculum

Wir unterscheiden bewusst zwei Ebenen:

1. **Mathe-/Symbol-Curriculum (Calculator 2.0)**
2. **Logik-/Puzzle-Curriculum (Games & Patterns)**

### 2.1 Mathe-/Symbol-Curriculum (Calculator 2.0)

Bestehende Formen (bereits in deiner DB):

- Brüche + einfache Ausdrücke:
  - `1/2`, `2/4`, …
- quadratische Gleichungen:
  - `x^2+2x+1=0`, `x^2-4=0`, …
- lineare Gleichungen:
  - `2x+3=7`, `3x-4=5`, …
- Konstanten:
  - `pi`, `phi`, `e`, …

**Neue Aufgabentypen (Vorschlag):**

1. **Lückenaufgaben: „Finde das fehlende Element“**

   Beispiele:
   - `3 + ? = 10` → Wahrheit: `7`
   - `? - 5 = 2` → Wahrheit: `7`
   - `2 * ? = 14` → Wahrheit: `7`

   DB-Repräsentation (weiterhin über `calculator_tasks`):

   - `expr`: `"3 + ? = 10"`
   - `truth_json`: `{"x": 7}` oder einfacher: `{"value": 7}`
   - `kind`-Feld im Task oder im `meta_json` des Tasks kennzeichnet Typ `"gap_add"`, `"gap_sub"`, etc.

2. **Zahlenfolgen: „Was kommt als nächstes?“**

   Beispiele einfach:
   - `3, 6, 9, ?` → `12` (arithmetisch +3)
   - `2, 4, 8, ?` → `16` (geometrisch ×2)

   Beispiele etwas schwieriger:
   - `2, 4, 3, 6, 4, 8, ?`  
     (abwechselnd `+2`, `×2`) → hier könnte man entweder `5` oder `8` als Ziel definieren; wichtig ist: in `truth_json` **klar festhalten**.

   Encoding:
   - `expr`: `"3,6,9,?"`
   - `truth_json`: `{"next": 12, "pattern": "arith+3"}`

3. **Vergleichsaufgaben: „<, = oder > ?“**

   Beispiele:
   - `3+4 ? 2*5` → `<`
   - `7*2 ? 10+3` → `=`
   - `9-1 ? 2*3` → `>`  

   Encoding:
   - `expr`: `"3+4 ? 2*5"`
   - `truth_json`: `{"relation": "<", "left": 7, "right": 10}`

4. **Mischaufgaben zur Strukturwahrnehmung**

   - „Was ist die **größte** Zahl?“ aus:
     - `3+4`, `2*5`, `9-1`
   - hier kann der Task als **Mehrfach-Teilaufgabe** modelliert sein:
     - erst Teil-Aufgaben für die drei Ausdrücke,
     - dann ein Meta-Task `expr="max(3+4, 2*5, 9-1)"`.

---

### 2.2 Logic-/Puzzle-Curriculum (Sudoku & Co.)

Hier definieren wir eine **eigene Schicht**, getrennt vom reinen Calculator:

1. **Sudoku-Track (v1.0)**  
   - Board: 9×9  
   - Anfangs: „easy“ Puzzles (viele vorgegebene Zahlen)  
   - später: „medium“/„hard“ mit weniger Vorgaben

2. **Weitere Puzzles (später)**
   - Kakuro-ähnliche Zahlengitter
   - kleine Truth-Table-Aufgaben (Logikgatter)
   - einfache kombinatorische Puzzles (Permutationen, Zuordnungen)

**Wichtig:**  
Sudoku wird NICHT über `calculator_tasks` abgebildet, sondern bekommt eigene Tabellen, z. B.:

- `sudoku_games`
  - `id`, `created_ts`, `difficulty`, `initial_board`, `solution_board`, `meta_json`
- `sudoku_moves`
  - `id`, `game_id`, `move_idx`, `row`, `col`, `value`, `correct`, `ts`

So bleibt der Calculator-Track sauber und die Sudoku-Welt ist als eigenes „Game mit Zuständen“ modelliert.

---

## 3. Curriculum-Struktur – Levels & Aufgaben

Wir bleiben beim bekannten Muster:

- **Levels** mit fester Anzahl Aufgaben
- dazwischen **Wiederholungsaufgaben**
- SelfAssessment + TransferSnaps als „Reflexion“

### 3.1 Vorschlag: Level-Struktur Mathe-/Symbol-Track

Beispiel-Konfiguration (anpassbar):

- **Level 1 (4 Aufgaben)**  
  - Fokus: einfache Arithmetik + Lücken
  - Mix:
    - 1× einfache Summe (klassisch)
    - 1× „3+?=10“-Typ
    - 1× einfache Folge (z. B. `3,6,9,?`)
    - 1× Vergleich `3+4 ? 2*5`

- **Level 2 (4 Aufgaben)**  
  - Fokus: Folgen & Quadrate
  - Mix:
    - 2× Zahlenfolgen (arith+geom)
    - 1× quadratische Gleichung (klassisch)
    - 1× Wiederholung einer früheren „schweren“ Aufgabe

- **Level 3 (4 Aufgaben)**  
  - Fokus: lineare Gleichungen + gemischte Formen
  - Mix:
    - 2× lineare Gleichung (`2x+3=7`, `3x-4=5`)
    - 1× Lückenform in Gleichungsform
    - 1× Vergleich

- **Level 4 (3 Aufgaben)**  
  - Fokus: Konstanten & Mischaufgaben
  - Mix:
    - 1× Konstante (pi/phi/e)
    - 2× Mischaufgaben („max von“, „kombinierte Folge“)

- **Level 5/6 (3 Aufgaben je)**  
  - schwierigere Sequenzen und Gleichungen  
  - **mehr Wiederholungen** von Aufgaben, bei denen SelfAssessment „niedrige Confidence“ signalisiert hat.

Wenn **alle Levels** (1–6) abgeschlossen sind:

- wie bisher: `Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1`
- aber: mit **Wissensstand**:
  - die Auswahl in Level 1 startet jetzt höher (mehr „schwerere“ oder neue Muster).

---

### 3.2 Boredom-Trigger → Wechsel ins Logic/Puzzle-Curriculum

Der Boredom-Trigger kann z. B. so definiert werden:

- über einen bestimmten Zeitraum (z. B. letzte N Ergebnisse):
  - `correct_rate ≥ 0.99`
  - `neuheit` niedrig (immer wieder dieselben Ausdrücke wie `1/2`, `2/4`, etc.)
  - SelfAssessment meldet z. B. „Confidence hoch“, „Frust/Unterforderung“ o. ä.

Wenn Bedingungen erfüllt:

1. Curriculum-Manager loggt Event:
   - `[Curriculum] Boredom-Mode aktiviert (Calculator saturiert)`
2. Nächste Aufgaben werden **nicht** aus `calculator_tasks` gezogen, sondern:
   - Logik-/Puzzle-Kanal:
     - **Sudoku-Spiel starten**, oder
     - schwerere Zahlenfolgen/Logikaufgaben.

Für Sudoku bedeutet das:

- Start eines Eintrags in `sudoku_games` (difficulty „easy“),
- neue Episode:
  - `episode_type="sudoku_session"`,
  - `episode_events`: Moves, Checks, evtl. Kommentare (TTS/MangelSpeak).

---

## 4. DB & Implementierungs-Skizze

### 4.1 Re-Use: Calculator-Tabellen

Wir verwenden weiter:

- `calculator_tasks`
- `calculator_results`
- `curriculum_state` (falls vorhanden / ergänzbar)
- `empathy_snaps`
- `coverage_log`
- `metrics`

**Erweiterungsidee für `calculator_tasks`:**

- zusätzliche Spalte (falls noch nicht vorhanden), z. B.:
  - `task_kind TEXT`  
    - `simple`, `gap`, `sequence`, `compare`, `mixed`
- `meta_json`:
  - kann weitere Infos enthalten:
    - `"pattern": "arith+3"`
    - `"sequence": [3,6,9]`
    - `"choice_set": ["<", "=", ">"]`, etc.

### 4.2 Neu: Sudoku-Tabellen (Vorschlag)

*(noch nicht im Code, aber als Zielbild)*

```text
sudoku_games:
  id INTEGER PRIMARY KEY AUTOINCREMENT
  ts_start INTEGER NOT NULL
  ts_end   INTEGER              -- optional, wenn gelöst / abgebrochen
  difficulty TEXT NOT NULL      -- 'easy', 'medium', 'hard'
  initial_board TEXT NOT NULL   -- z.B. 81-Char-String oder JSON
  solution_board TEXT NOT NULL  -- vollständige Lösung
  status TEXT NOT NULL          -- 'active', 'solved', 'failed', 'abandoned'
  meta_json TEXT                -- z.B. {"generator_seed": 123, "note": "auto"}

sudoku_moves:
  id INTEGER PRIMARY KEY AUTOINCREMENT
  game_id INTEGER NOT NULL      -- FK auf sudoku_games.id
  move_idx INTEGER NOT NULL     -- 0,1,2,... in Reihenfolge
  row INTEGER NOT NULL          -- 0–8
  col INTEGER NOT NULL          -- 0–8
  value INTEGER NOT NULL        -- 1–9
  correct INTEGER NOT NULL      -- 0/1
  ts INTEGER NOT NULL
  meta_json TEXT                -- optional (z.B. "hint": true)

So lässt sich eine Sudoku-Session später ähnlich auswerten wie ein Spiel
(TicTacToe/Snake), nur mit komplett anderer Struktur.

⸻

5. Trainingszyklus (vereinfacht)
	1.	Curriculum-Manager entscheidet Kanal
	•	kind="calculator" oder kind="logic"
	•	Wenn kind="calculator":
	•	wähle Level + Aufgabe basierend auf:
	•	vergangenen Fehlern,
	•	Wiederholungsbedarf,
	•	Neuheit.
	•	Wenn kind="logic":
	•	stelle z. B. eine Sudoku-Aufgabe bereit.
	2.	Task erzeugen
	•	Calculator:
	•	Eintrag in calculator_tasks mit expr, task_kind, truth_json.
	•	Sudoku:
	•	Eintrag in sudoku_games + initialer Episode-Event.
	3.	ORÓMA „arbeitet“
	•	löst Matheaufgabe,
	•	oder macht einen Sudoku-Zug,
	•	ggf. TTS/MangelSpeak-Kommentar.
	4.	Result speichern
	•	Calculator:
	•	calculator_results.correct + SelfAssessment + TransferSnap.
	•	Sudoku:
	•	Eintrag in sudoku_moves, Update von sudoku_games.status,
	•	Reward (z. B. pro korrektem Zug / Lösung).
	5.	Curriculum-Update
	•	Level-Progress:
	•	Level X, Aufgabe Y/Z erledigt
	•	Boredom-Checks:
	•	bei saturierter Performance → Logik-/Sudoku-Track aktivieren.

⸻

6. Beobachtungs- und Testfragen (für „Hinterfragen & Testen“)

Wenn du das später testen willst, sind u. a. spannend:
	1.	Wird die Aufgabenvielfalt sichtbar größer?
	•	Verteilung von task_kind in calculator_tasks/calculator_results.
	•	Wie oft kommen gap / sequence / compare-Aufgaben im Vergleich
zu den klassischen 1/2, x^2-4=0, etc.?
	2.	Greift der Boredom-Mode tatsächlich?
	•	Zeitpunkte, an denen Curriculum] Boredom-Mode aktiviert im Log auftritt.
	•	Korrelation mit:
	•	correct_rate,
	•	SelfAssessment-Confidence,
	•	Coverage/Neuheit.
	3.	Wie „verhält“ sich ORÓMA in Sudoku?
	•	Anzahl Züge pro Lösung,
	•	Fehlerquote,
	•	Episoden-Verläufe:
	•	Wird nach Fehlern anders weitergespielt
(z. B. mehr Vorsicht / Wiederholungen ähnlicher Muster)?
	4.	Übertragbarkeit
	•	Lernen im Calculator-Track (arithmetische/logische Struktur) →
Veränderungen im Sudoku-Track?
	•	z. B.:
	•	wird schneller erkannt, dass eine Konstellation unmöglich ist?

⸻

7. Nächste Schritte
	1.	Mathe-/Symbol-Curriculum im Code erweitern
	•	neue task_kind-Werte implementieren,
	•	Generator-Funktionen für:
	•	Lückenaufgaben,
	•	Zahlenfolgen,
	•	Vergleichsaufgaben.
	2.	Boredom-Logik definieren und einbauen
	•	(z. B. im Curriculum-Manager / Patch1-Hooks),
	•	Metriken: Moving Window über die letzten N Aufgaben.
	3.	Sudoku-Engine & Tabellen implementieren (separates Modul)
	•	core/sudoku_engine.py
	•	neue Tabellen in sql_manager.ensure_schema(),
	•	UI-Blueprint ui/sudoku_ui.py + Template.
	4.	Tests & Doku erweitern
	•	kleine CLI-Tools:
	•	tools/logic_curriculum_selftest.py,
	•	tools/sudoku_selftest.py,
	•	Doku-Update im Haupt-CHANGELOG:
	•	„Curriculum Logic/Puzzles v1.0“.

⸻

Kurz-Fazit
	•	Deine Intuition („wenn langweilig → Sudoku spielen / Logikspiele“) passt perfekt zu ORÓMAs Architektur.
	•	Mit diesem Curriculum-Entwurf hast du:
	•	einen klaren Plan, wie Calculator-Track breiter wird,
	•	einen sauberen Platz für Sudoku & Co. als eigenen Logic/Puzzle-Track,
	•	und einen sinnvollen Boredom-Mechanismus, der nicht nur „Spielerei“, sondern
ein echter Teil des Lernverhaltens ist.

Dieses Dokument ist bewusst als Basis gedacht – im nächsten Schritt
können wir entweder:
	•	konkret die Generator-Funktionen für neue Calculator-Aufgaben bauen, oder
	•	direkt mit core/sudoku_engine.py loslegen.

<a id="docs_curriculum_math_and_puzzles_md"></a>

## Quelle: `docs/curriculum_math_and_puzzles.md`

**Originaltitel:** ORÓMA – Calculator-Curriculum v2 + Puzzles

**Datei:** `docs/curriculum_math_and_puzzles.md`  
**Projekt:** ORÓMA – KI-JWG-X1  
**Version:** v2.0  
**Stand:** 2025-12-13  
**Autor:** Jörg Werner + GPT-5.1 Thinking  

---

## 1. Zweck dieses Dokuments

Dieses Dokument beschreibt den **aktuellen Stand** des Rechen-Curriculums  
(v3.5 Patch 1 / „Calculator-Engine + Curriculum“) und definiert eine **Erweiterung
zu Curriculum v2**, die

- mehr **Aufgabentypen** bietet (nicht nur immer dieselben Ausdrücke),
- **Wiederholungsaufgaben** klarer strukturiert,
- einfache **Logik- & Musteraufgaben** (z. B. „3 + ? = 10“, „3, 6, 9, ?“) integriert,
- optional **Sudoku** und komplexere Zahlenspiele als „Anti-Langeweile“-Modus vorsieht,
- ohne harte Schema-Brüche mit den bestehenden Tabellen auskommt:
  - `calculator_tasks`
  - `calculator_results`
  - `transfer_snaps`
  - `curriculum_state`
  - `empathy_snaps` / „MangelSpeak“ / Coverage-Logik.

---

## 2. Status Quo – Wie das Curriculum heute arbeitet

### 2.1 Beobachtetes Verhalten (Log-Ausschnitt)

Beispielhafte Logs aus dem laufenden System:

```text
[Curriculum] Level 1, Aufgabe 3/4 erledigt
...
[Curriculum] Level 2, Aufgabe 4/4 erledigt
...
[Curriculum] Level 6, Aufgabe 3/3 erledigt
[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1
...
[MangelSpeak] Wiederholungsaufgabe re-queued: {'expr': '1/2', 'truth': 0.5}
...
[Curriculum] Wiederholung erledigt: x^2+2x+1=0
...
[Curriculum] Wiederholung erledigt: 3x-4=5

Typische Muster:
	•	Das Curriculum arbeitet mit Levels (z. B. Level 1–6) und pro Level einer
festen Anzahl an „Aufgaben“ (z. B. 4/4, 3/3).
	•	Dazwischen werden Wiederholungsaufgaben eingeschoben:
	•	Wiederholung erledigt: 1/2
	•	Wiederholung erledigt: x^2+2x+1=0
	•	Wiederholung erledigt: 3x-4=5
	•	Wiederholung erledigt: pi, phi, e, …
	•	Empathie & Self-Assessment greifen ein:
	•	„Meine Confidence ist niedrig: 0.55… Ich passe mein Üben an.“
	•	„Ich höre Frust… Ich wiederhole anspruchsvolle Aufgaben.“

2.2 DB-Zustand (Beispiel: Wiederholungs-Tasks)

Kurze Auswertung direkt auf der DB:

1/2:           tasks=4740,  results=4740,  correct=4740,  incorrect=0
2/4:           tasks=4495,  results=4495,  correct=4495,  incorrect=0
x^2+2x+1=0:   tasks=10269, results=10269, correct=10269, incorrect=0
x^2-4=0:      tasks=14329, results=14328, correct=14328, incorrect=0
2x+3=7:       tasks=6882,  results=6882,  correct=6882,  incorrect=0
3x-4=5:       tasks=9483,  results=9482,  correct=9482,  incorrect=0
pi:           tasks=4267,  results=4267,  correct=4267,  incorrect=0
phi:          tasks=4396,  results=4396,  correct=4396,  incorrect=0
e:            tasks=5263,  results=5263,  correct=5263,  incorrect=0

Interpretation:
	•	Wenig verschiedene „expr“, aber sehr viele Wiederholungen.
	•	Ergebnisseite (calculator_results) ist korrekt gefüllt (correct=alle).
	•	Für dich als Beobachter wirkt es im Log so, als
ob „immer dieselben Aufgaben“ kommen – was auch stimmt:
das Curriculum hat aktuell nur eine kleine Menge an Kern-Formeln
(Brüche, einfache Gleichungen, pi/phi/e).

⸻

3. Ziel für Curriculum v2

Ziel:
Die „Mathe-Ecke“ von ORÓMA soll sich weniger monoton und inhaltlich breiter
anfühlen – ohne das vorhandene System zu zerstören.

Konkret:
	1.	Mehr Aufgabentypen, nicht nur „expr = Zahl“.
	2.	Mehr Variation in den Wiederholungen, nicht nur 9 feste Ausdrücke.
	3.	Kombination aus „Fakten-Drills“ + „Mustererkennung“ + „Logik-Füllen“.
	4.	Ein optionaler Spiel-/Puzzle-Modus (z. B. Sudoku), den das Curriculum
ansteuern kann, wenn:
	•	Empathie Frust oder Langeweile meldet, oder
	•	Coverage sehr hoch, Neuheit sehr niedrig ist.

⸻

4. Neue Aufgabentypen (konzeptionell, ohne Schema-Bruch)

Die bestehenden Tabellen calculator_tasks und calculator_results kennen u. a.:
	•	expr (TEXT) – die Aufgabenbeschreibung (z. B. "1/2", "x^2-4=0", "pi"),
	•	truth / truth_json – erwartete Lösung,
	•	got / got_json – tatsächlich gelöste Antwort,
	•	correct – 0/1.

Wichtige Leitentscheidung für v2:
Wir erweitern die Vielfalt, indem wir expr & truth_json clever nutzen,
nicht indem wir das Schema mit neuen Tabellen sprengen.

4.1 Typ A – Einfache Lückenaufgaben (Fill-in-the-Blank)

Beispiele:
	•	3 + ? = 10 → Lösung: 7
	•	? - 4 = 9  → Lösung: 13
	•	2 * ? = 14 → Lösung: 7

Speicherung:
	•	expr: z. B. "fill: 3 + ? = 10"
	•	truth_json: {"type": "fill", "solution": 7}

Bewertung:
	•	got_json enthält {"answer": 7} → correct = 1
	•	ggf. Debug-Info im Log:
	•	[Curriculum] Wiederholung erledigt: fill: 3 + ? = 10

4.2 Typ B – Zahlfolgen (Sequenzen)

Beispiele:
	•	"3, 6, 9, ?" → Lösung: 12 (arithmetische Folge +3)
	•	"2, 4, 8, 16, ?" → Lösung: 32 (geometrische Folge ×2)
	•	"1, 1, 2, 3, 5, ?" → Lösung: 8 (Fibonacci)

Speicherung:
	•	expr: "seq: 3, 6, 9, ?"
	•	truth_json:
{"type": "sequence", "sequence": [3, 6, 9], "rule": "arith+3", "solution": 12}

Zur Not kann rule auch null sein, wenn der Student die Regel
nur implizit lernt.

4.3 Typ C – Mini-Logik & Vergleich

Beispiele:
	•	„Welche Zahl ist größer?“ – 7 ? 12 → Lösung: "<" oder "12"
	•	„Welches Ergebnis ist richtig?“ – (3+4) ? 10 → Lösung: "<"

Dieser Typ kann später kommen; für v2 reicht es, ihn im Konzept
zu notieren:
	•	expr: "cmp: 7 ? 12"
	•	truth_json: {"type": "compare", "relation": "<", "left": 7, "right": 12}

4.4 Typ D – „Mathe-Fakten“ (wie bisher: pi, phi, e)

Diese existieren bereits:
	•	"pi", "phi", "e".

Sie passen gut in Kategorie:
	•	expr: "fact: pi"
	•	truth_json: {"type": "fact", "symbol": "pi", "approx": 3.14159}

Du kannst sie so lassen wie sie sind – oder in Zukunft gently in das
truth_json-Format überführen.

⸻

5. Sudoku & komplexere Zahlenspiele

5.1 Sudoku als „Langeweile-Brecher“

Gedanke:
	•	Wenn das Curriculum merkt:
	•	Confidence stabil hoch,
	•	Coverage = 1.0,
	•	Neuheit sehr niedrig (immer gleiche Muster),
	•	oder Empathie meldet „Langeweile“/„Frust“,
	•	dann kann statt weiterer 1-Schritt-Rechenaufgaben ein Sudoku-Puzzle
erzeugt werden.

Sudoku kann man in ORÓMA so einhängen:
	•	neues Game-Backend: game:sudoku (analog zu TicTacToe, Snake, …),
	•	SnapChains mit origin='game:sudoku':
	•	jeder Zug / jede Boardsituation = Snap,
	•	komplette Partie = Episode.

5.2 Sudoku-Aufgabe als Calculator-Task (optional)

Um die Calculator-Linie konsistent zu halten, könnte ein Sudoku auch als
Calculator-Task „markiert“ werden:
	•	expr: "sudoku: <seed_id>"
	•	truth_json:

{
  "type": "sudoku",
  "seed": "<random-seed-or-id>",
  "size": 9,
  "solution": [[5,3,4,...],[6,7,2,...],...]
}


Realistisch brauchst du für echte Sudoku-Spiele:
	•	ein eigenes Modul games/sudoku_game.py (Logik: Generator + Prüfer),
	•	einen einfachen UI-View (optional),
	•	aber das Curriculum kann Sudoku trotzdem als „erledigte Lernaufgabe“
zählen, wenn das Game meldet: „Sudoku korrekt gelöst“.

5.3 „Noch kompliziertere Rechenspiele“

Neben Sudoku bieten sich an:
	•	Rechenrätsel mit mehreren Schritten:
	•	z. B. „Erst addieren, dann multiplizieren“:
	•	expr: "puzzle: (3 + 4) * 2 = ?"
	•	Zahlengitter / Kakuro-artige Aufgaben:
	•	Summenbedingungen pro Reihe/Spalte,
	•	Mini-Gleichungssysteme:
	•	x + y = 10, x - y = 2 → Lösung: x=6, y=4

Für v2 reicht, in truth_json.type z. B. "multi_step" oder "system"
zu definieren – die eigentliche Engine kann später wachsen.

⸻

6. Curriculum-Logik v2 – Wie wird entschieden, was als Nächstes kommt?

6.1 Heute (vereinfacht rekonstruiert)
	•	Curriculum hat Levels mit fester Aufgabenanzahl:
	•	Level 1: 4 Aufgaben (Brüche, leichte Gleichungen)
	•	Level 2: 4 Aufgaben (Quadratische Gleichungen, Wiederholungen)
	•	…
	•	Level 6: 3 Aufgaben (Fortgeschrittene Wiederholungen)
	•	Zwischendurch Wiederholungen, getriggert durch:
	•	Gaps / Fehler,
	•	Self-Assessment (Empathie),
	•	interne „Wiederholungslisten“.
	•	MangelSpeak kommentiert Gefühle („Confidence niedrig“)
und stößt Wiederholungsaufgaben an
(Wiederholungsaufgabe re-queued: {...}).

6.2 v2 – Erweiterung mit mehr Skills

Curriculum v2 soll intern ungefähr so denken:
	1.	Skill-Buckets
	•	basic_arith: einfache +,−,×,÷, Fill-Aufgaben (3+?=10, ?*4=12).
	•	fractions: Brüche wie 1/2, 2/4, Vergleich & Vereinfachung.
	•	equations: Gleichungen wie 2x+3=7, 3x-4=5, quadratische wie x^2-4=0.
	•	facts: pi, phi, e, konstante Werte.
	•	sequences: Zahlfolgen wie 3, 6, 9, ?, 1, 1, 2, 3, 5, ?.
	•	puzzles: Sudoku, Multi-Step-Rätsel.
	2.	Jede Aufgabe gehört zu einem Skill-Bucket
	•	in truth_json könnte ein Feld skill stehen:
	•	z. B. {"skill": "fractions", ...}
	3.	Curriculum-Entscheidung (Pseudo-Regeln)
	•	Wenn Confidence niedrig und Coverage < 1.0:
	•	eher basic_arith / fractions mit Fills & Wiederholungen.
	•	Wenn Confidence okay, Coverage ≈ 1.0, Neuheit niedrig:
	•	sequences oder puzzles einstreuen.
	•	Wenn Frust bei schweren Tasks:
	•	komplexe Aufgaben kurz pausieren,
	•	1-2 leichte Fills (3+?=10) oder bekannte Wiederholungen (1/2)
zur Stabilisierung,
	•	dann wieder hoch.
	4.	„Alle Levels abgeschlossen“
	•	statt danach stumpf wieder bei Level 1 genau dieselben 9 Ausdrücke
zu updaten, kann v2:
	•	die Verteilung der Skill-Buckets rotieren, z. B.:
	•	Runde A: Fokus fractions + equations
	•	Runde B: Fokus facts + sequences
	•	Runde C: Fokus puzzles + equations.

⸻

7. Umsetzungsideen (ohne konkreten Code, aber code-nah)

7.1 Erweiterung der Aufgabengenerierung

Bestehendes Prinzip:
	•	calculator_engine bzw. Patch-1-Hooks erzeugen Aufgaben mit expr
und truth.

Erweiterung v2:
	•	Neue Generatortypen (in Python):
	•	generate_fill_task()
	•	generate_sequence_task()
	•	generate_puzzle_task() (z. B. Sudoku-Seed erzeugen)
	•	Alle Generatoren liefern ein einheitliches Dict, das in
calculator_tasks gespeichert werden kann:

{
    "expr": "fill: 3 + ? = 10",
    "truth_json": {
        "type": "fill",
        "skill": "basic_arith",
        "solution": 7
    }
}


7.2 Wiederholungen „intelligenter“ wählen

Statt nur immer die gleichen Ausdrücke („1/2“, „x^2+2x+1=0“, etc.):
	•	Wiederholungs-Kandidaten aus calculator_results wählen, z. B.:
	•	letzte N Aufgaben pro Skill,
	•	Aufgaben mit:
	•	höherer Fehlerquote (falls es welche gibt),
	•	oder sehr alten Timestamps.

Durch truth_json.skill kann das Curriculum z. B. sagen:

„Ich wiederhole jetzt 2 Aufgaben aus fractions und 1 Aufgabe aus
equations, statt 1000× denselben Ausdruck.“

⸻

8. Zusammenfassung
	•	Ist-Zustand
	•	Das Rechen-Curriculum funktioniert, aber arbeitet aktuell mit einer
sehr kleinen Menge von Ausdrücken, die tausendfach wiederholt werden.
	•	Die DB zeigt: alles korrekt, aber inhaltlich eintönig.
	•	Curriculum v2
	•	nutzt weiterhin calculator_tasks / calculator_results,
	•	erweitert die Aufgabentypen:
	•	Füllen von Lücken (3 + ? = 10),
	•	Zahlfolgen (3, 6, 9, ?),
	•	Mini-Logik (später),
	•	Mathe-Fakten (pi, phi, e),
	•	Puzzles (Sudoku, mehrstufige Rätsel).
	•	hängt jedes Task an einen Skill-Bucket (fractions, equations, facts, …),
	•	lässt das Curriculum je nach Confidence, Coverage, Neuheit
und Empathie/„MangelSpeak“ entscheiden:
	•	Welche Aufgabe als Nächstes kommt?
	•	Ob eine Wiederholung sinnvoll ist?
	•	Ob ein Sudoku-/Puzzle-Block als Abwechslung startet.
	•	Sudoku & Co.
	•	dienen als „Langeweile-Brecher“,
	•	können als eigenes Game laufen (origin='game:sudoku'),
	•	optional mit calculator_tasks verknüpft werden, um das Curriculum
konsistent zu halten.

Damit bleibt ORÓMA:
	•	architektonisch sauber (kein Schema-Chaos),
	•	vom Curriculum her vielfältiger,
	•	und näher an dem, wie ein Mensch lernt:
	•	Fakten, Muster, Lückenaufgaben, Spiele – alles gemischt,
	•	gesteuert von Frust, Neugier, Fortschritt und Wiederholungsbedarf.

<a id="docs_curriculum_math_calculator_md"></a>

## Quelle: `docs/curriculum_math_calculator.md`

**Originaltitel:** ORÓMA – Mathe-Curriculum & Calculator-Lernen

**Datei:** `docs/curriculum_math_calculator.md`  
**Projekt:** ORÓMA – KI-JWG-X1  
**Stand:** 2025-12-13  
**Autor:** Jörg Werner + GPT-5.1 Thinking  

---

## 1. Zweck dieses Dokuments

Dieses Dokument beschreibt, wie das **Mathe-Curriculum** in ORÓMA funktioniert – konkret:

- welche Module beteiligt sind,
- wie **Levels** und **Aufgaben** organisiert werden,
- wie **Wiederholungs-Logik** (Spaced Repetition) funktioniert,
- wie **Empathie / SelfAssessment / MangelSpeak** das Üben beeinflussen,
- und wo die **Ergebnisse** technisch landen (DB-Tabellen, Logs).

Ziel:  
Die Mathe-Pipeline soll nachvollziehbar, prüfbar und debugbar sein, bevor wir sie weiter ausbauen.

---

## 2. Beteiligte Komponenten (Code-Level)

### 2.1 Curriculum-Logik

- **`core/curriculum_math.py`**
  - Enthält den konkreten „Lehrplan“ für den Mathe-Trainer:
    - **Levels**: aktuell Level 1–6.
    - pro Level eine feste Anzahl **Basisaufgaben** (z.B. lineare Gleichungen, Quadrate, Brüche, Konstanten).
  - Typische Inhalte (Beispiele, nicht vollständig):
    - Level 1:
      - einfache lineare Aufgaben (`x+7=10`, `2x+3=7`, `3x-4=5`, …)
    - Level 2:
      - einfache quadratische Gleichungen (`x^2+2x+1=0`, `x^2-4=0`, `x^2-5x+6=0`, …)
    - Level 3–5:
      - Mischformen, Brüche (`1/2`, `2/4`, `3/6`, `4/8`), Konstanten (`pi`, `phi`, `e`)
    - Level 6:
      - fortgeschrittene Kombinationen / Wiederholungsblöcke
  - Stellt Funktionen bereit wie:
    - Auswahl der nächsten **Level-Aufgabe**,
    - Verwaltung von **Wiederholungsaufgaben** (Queue für Spaced Repetition).

- **`core/curriculum_hook.py`**
  - Kapselt das eigentliche **Curriculum-State-Management**:
    - `current_level` (1–6),
    - `current_index` innerhalb des Levels (z.B. 1/4, 2/4, …),
    - Fortschrittslogik: „Level X, Aufgabe Y/n erledigt“.
  - Wird vom Calculator-/Patch1-Hook aufgerufen, nachdem eine Aufgabe gelöst wurde.
  - Entscheidet, ob:
    - eine **normale Level-Aufgabe** bearbeitet wurde  
      → Fortschritt: `Level L, Aufgabe i/n erledigt`
    - oder eine **Wiederholungsaufgabe** aus der Queue  
      → Log: `Wiederholung erledigt: <expr>`.  <!-- TODO linkfix: expr -> docs/module_exports.md -->

### 2.2 Calculator-Engine & Ergebnisse

- **`core/calculator_adapter.py`** (Patch1-Stack)
  - Schnittstelle zwischen Curriculum und der eigentlichen Rechen-Engine.
  - Kernaufgaben:
    - **Aufgabe anlegen**:
      - `create_task(...)`  
        → schreibt Datensatz in `calculator_tasks`.
      - Log:  
        `"[Patch1] Calculator-Task erstellt: id=..."`.
    - **Ergebnis speichern**:
      - `save_result(...)`  
        → schreibt Datensatz in `calculator_results`.
      - Log:  
        `"[Patch1] Calculator-Result gespeichert: id=..."`.
  - Die eigentlichen Zahlen (`truth` und `got`) werden in der **DB** gespeichert, nicht im Log ausgespuckt.

### 2.3 Empathie, SelfAssessment & TransferSnaps

- **SelfAssessment / Empathy**
  - erzeugt **SelfAssessment-Snaps**:
    - Log:  
      `"[Patch1] SelfAssessment-Snap gespeichert: id=..."`.
  - enthält Werte wie:
    - **Confidence**, **Coverage**, **Neuheit** („Novelty“),
    - normierte **Zeit bis Ziel** (Progress-Schätzung).
  - Diese Werte werden vom Curriculum genutzt, um zu entscheiden,
    ob **zusätzliche Wiederholungsaufgaben** eingereiht werden.

- **TransferSnaps**
  - transportieren verdichtete Lerninfos in eine Meta-Ebene:
    - Log:  
      `"[Patch1] TransferSnap gespeichert: id=..."`.
  - dienen als „Gedächtnisanker“ über viele Aufgaben hinweg  
    (z.B. „diese Art von Aufgabe war schwierig, später wiederholen“).

---

## 3. Ablauf pro Aufgabe (Curriculum-Tick)

Grob in der Reihenfolge, wie sie im Log sichtbar ist:

1. **Curriculum wählt Aufgabe**
   - aus dem aktuellen **Level L** und **Index i**  
     oder aus der **Wiederholungs-Queue** (falls eine due ist).

2. **Calculator-Task wird erzeugt**
   - `Calculator-Task erstellt: id=246252`
   - in `calculator_tasks` stehen dann u.a.:
     - `expr` (z.B. `"x^2+2x+1=0"`),
     - `truth_json` (Soll-Ergebnis, z.B. `[-1]`),
     - Metadaten (Level, Herkunft, Zeitpunkt).

3. **Task wird gelöst**
   - ORÓMA rechnet (bzw. der Mathe-Adapter):
     - dein Ergebnis → `got_json`
   - `Calculator-Result gespeichert: id=241219`
     - in `calculator_results`:
       - Verweis auf `task_id`,
       - `got_json` (Antwort),
       - `truth_json` (Soll),
       - `reward` (1.0 bei korrekt, <1.0 bei Fehlern),
       - `ts`.

4. **Curriculum-Hook aktualisiert den Fortschritt**
   - Fall A: **normale Level-Aufgabe**
     - Log z.B.:
       - `[Curriculum] Level 2, Aufgabe 3/4 erledigt`
     - `current_index` wird erhöht.
     - wenn `i == n`:
       - Levelwechsel: `current_level += 1`, `current_index = 0`  
       - ggf.:  
         `"[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1"`.

   - Fall B: **Wiederholungsaufgabe**
     - War die Aufgabe aus der Wiederholungs-Queue, kommt:
       - `[Curriculum] Wiederholung erledigt: x^2+2x+1=0`
     - Die zugehörige Wiederholungs-Statistik wird angepasst,  
       der Task aus der Queue entfernt oder neu geplant.

5. **SelfAssessment & Transfer**
   - in regelmäßigen Abständen (z.B. nach bestimmten Aufgabenzahlen) werden:
     - SelfAssessment-Snaps geschrieben:
       - `[Patch1] SelfAssessment-Snap gespeichert: id=...`
     - TransferSnaps:
       - `[Patch1] TransferSnap gespeichert: id=...`
   - Hier fließen deine **aktuellen Lernparameter** ein:
     - Confidence, Coverage, Neuheit, Zeit bis Ziel.
   - Wenn Confidence zu niedrig ist → **Wiederholungen einreihen**, siehe nächstes Kapitel.

---

## 4. Levels & Aufgabenstruktur

### 4.1 Level-Struktur

Dein Log zeigt mehrere vollständige Durchläufe des Curriculums:

- **Level 1** – 4 Aufgaben  
  → Basisarithmetik, einfache lineare Gleichungen

- **Level 2** – 4 Aufgaben  
  → einfache quadratische Gleichungen (Standardformen)

- **Level 3** – 4 Aufgaben  
  → Mischung aus linearen & quadratischen Aufgaben, leichte Steigerung

- **Level 4** – 3 Aufgaben  
  → kompakter Block mit etwas schwierigeren Aufgaben / Kombinationen

- **Level 5** – 3 Aufgaben  
  → Brüche & Konstanten (`1/2`, `2/4`, `3/6`, `4/8`, `pi`, `phi`, `e`, …)

- **Level 6** – 3 Aufgaben  
  → „Master-Level“ mit Misch- und Wiederholungsaufgaben

Nach Level 6:

```text
[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1

→ Das Curriculum beginnt wieder bei Level 1, mit denselben Kernaufgaben.
Das ist gewollt: wie ein Tag/Nacht-Zyklus für Mathe.

4.2 Beispiel aus dem Log

Echte Ausschnitte (verkürzt):

[Curriculum] Level 1, Aufgabe 3/4 erledigt
...
[Curriculum] Level 1, Aufgabe 4/4 erledigt
[Curriculum] Level 2, Aufgabe 1/4 erledigt
...
[Curriculum] Level 2, Aufgabe 4/4 erledigt
...
[Curriculum] Level 6, Aufgabe 3/3 erledigt
[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1

Man sieht:
	•	strukturiertes Durchlaufen,
	•	keine zufällige Reihenfolge,
	•	Wiederholung nach einem vollen Zyklus.

⸻

5. Wiederholungs-Logik & MangelSpeak

5.1 Trigger: Emotion & Lernstatus

Immer wieder tauchen im Log solche Zeilen auf:

[TTS Fallback]  Meine Confidence ist niedrig: 0.55. Meine Coverage ist 1.00. Neuheit: 0.40. Zeit bis Ziel (normiert): 0.50. Ich passe mein Üben an.
[MangelSpeak] Gesagt: Meine Confidence ist niedrig: 0.55. Meine Coverage ist 1.00. Neuheit: 0.40. Zeit bis Ziel (normiert): 0.50. Ich passe mein Üben an.
[MangelSpeak] Wiederholungsaufgabe re-queued: {'expr': '1/2', 'truth': 0.5}

Bedeutung:
	•	Confidence ~ 0.55
→ ORÓMA ist sich noch nicht ganz sicher.
	•	Coverage = 1.00
→ Stoff ist formal abgedeckt (alle Levelaufgaben wurden schon gesehen).
	•	Neuheit = 0.40
→ Aufgaben sind nicht völlig neu, aber auch nicht komplett „langweilig“.
	•	Zeit bis Ziel = 0.50
→ Lernziel ist etwa zur Hälfte erreicht.

Der Curriculum-Hook interpretiert das so:

„Ich kann die Aufgaben, aber vertraue mir noch nicht genug → ich packe bestimmte Kernaufgaben in eine Wiederholungs-Queue.“

5.2 Wiederholungs-Queue
	•	Bei solchen Triggern wird eine Wiederholungsaufgabe eingereiht:

[MangelSpeak] Wiederholungsaufgabe re-queued: {'expr': '1/2', 'truth': 0.5}

	•	Später, wenn wieder Rechenkapazität frei ist oder der Level-Flow das vorsieht,
wird die Aufgabe aus der Queue gezogen und erledigt:

[Curriculum] Wiederholung erledigt: 1/2

	•	Weitere Beispiele aus deinem Log:

[Curriculum] Wiederholung erledigt: x^2+2x+1=0
[Curriculum] Wiederholung erledigt: x^2-5x+6=0
[Curriculum] Wiederholung erledigt: pi
[Curriculum] Wiederholung erledigt: phi
[Curriculum] Wiederholung erledigt: e
[Curriculum] Wiederholung erledigt: 2x+3=7
[Curriculum] Wiederholung erledigt: 3x-4=5
...


→ Das sind genau die „wichtigen Muster“, die der Lehrer (Curriculum)
immer wieder einschiebt, bis die Confidence stabil hoch ist.

⸻

6. Warum „sehen die Aufgaben gleich aus“?

Aus Sicht des Curriculums ist das kein Bug, sondern ein Feature:
	1.	Kleiner, fester Pool an Kernaufgaben pro Level
	•	In curriculum_math.py ist bewusst kein gigantischer Aufgabenpool hinterlegt,
sondern ein intelligenter Kern aus prototypischen Aufgaben.
	•	Diese werden wiederholt, bis sie wirklich sitzen.
	2.	Spaced Repetition (Wiederholungs-Queue)
	•	Wenn Empathy/SelfAssessment sagt:
	•	„Confidence zu niedrig“ oder „Frust hörbar“,
	•	werden gerade diese Kernaufgaben noch öfter geübt.
	•	Ergebnis:
subjektiv wirken die Aufgaben „immer gleich“ –
tatsächlich ist das Curriculum im Drill-Modus, um Lücken zu schließen.

⸻

7. Wo sind die Ergebnisse? (DB-Sicht)

Die Log-Zeilen zeigen nur die „Story“.
Die numerischen Ergebnisse stehen in der SQLite-DB:
	•	calculator_tasks
	•	id – Task-ID (z.B. 246252)
	•	expr – die Aufgabe ("x^2+2x+1=0")
	•	truth_json – Soll-Ergebnis (z.B. [-1])
	•	Metadaten (Level, Quelle, ts)
	•	calculator_results
	•	id – Result-ID (z.B. 241219)
	•	task_id – Verweis auf calculator_tasks.id
	•	truth_json – Soll
	•	got_json – deine Antwort
	•	reward – z.B. 1.0 (korrekt) oder <1.0 (Fehler)
	•	ts – Zeitstempel

Zusätzlich:
	•	empathy_snaps, transfer_snaps, ggf. setcalc_log
– speichern die Meta-Informationen rund ums Lernen (Mood, Confidence, etc.).

Wichtig:
Dass du im Log nicht direkt „got=…, truth=…“ siehst, heißt nicht,
dass diese Informationen fehlen – sie liegen in der DB und werden von der Learning-/Selftest-UI genutzt.

⸻

8. Ideen für Tests & kritische Fragen

Dieser Abschnitt ist bewusst als „To-Do-Liste“ formuliert –
für dein geplantes Hinterfragen & Testen.

Mögliche Tests:
	1.	DB-Inspektions-Script
	•	Kleines Python-Tool, das die letzten 50 calculator_results zeigt:
	•	Level, expr, got, truth, reward.
	•	Ziel: Transparenz, wie gut ORÓMA tatsächlich rechnet.
	2.	Curriculum-Reset testen
	•	Start bei Level 1,
	•	einmal kompletten Durchlauf beobachten,
	•	verifizieren, dass nach Level 6:
	•	Alle Levels abgeschlossen 🎉 – Neustart bei Level 1
	•	und die Zähler zurückgesetzt sind.
	3.	Wiederholungs-Trigger provozieren
	•	absichtlich falsche Antworten geben,
	•	schauen, ob die betroffenen Ausdrücke in „Wiederholung erledigt: …“ wieder auftauchen,
	•	prüfen, wie sich Confidence und Wiederholungsfrequenz verhalten.
	4.	Empathy/MangelSpeak-Grenzwerte
	•	Schwellwerte systematisch variieren:
	•	ab welcher Confidence-Schwelle werden Wiederholungen einsortiert?
	•	lohnt es sich, einen Minimal-Abstand zwischen Wiederholungen einzubauen?
	5.	UI-Erweiterung
	•	in der Learning-/Selftest-UI:
	•	Liste: „Letzte Mathe-Aufgaben“
	•	Spalten: Level, expr, got, truth, reward, Wiederholung ja/nein.

Offene Fragen (für spätere Doku-Erweiterungen):
	•	Sollen „Kernaufgaben“ (z.B. 1/2, x+7=10, pi, e) dauerhaft immer wieder kommen,
oder nur, bis ein bestimmtes Confidence-Niveau erreicht ist?
	•	Sollen Level/Aufgaben dynamisch werden (z.B. Curriculum passt Anzahl Aufgaben pro Level an),
statt fix 4/4, 3/3?
	•	Wie stark sollen Empathy/MangelSpeak das Curriculum steuern?
	•	nur Wiederholungen,
	•	oder auch Level-Sprünge („Level zurück“, „Level überspringen“)?

⸻

9. Kurzfazit
	•	Das Mathe-Curriculum in ORÓMA ist kein Zufallstrainer,
sondern ein gestufter Lehrplan mit:
	•	Levels (1–6),
	•	festen Kernaufgaben,
	•	einer Wiederholungs-Queue,
	•	und einer Kopplung an Empathy/SelfAssessment.
	•	Dass viele Aufgaben „immer gleich aussehen“,
ist eine direkte Folge der gezielten Wiederholung von Basis-Mustern
– so wie ein menschlicher Lehrer Kernaufgaben immer wieder vorkommen lässt.
	•	Die eigentlichen Ergebnisse (got vs. truth) stehen sauber in der DB (calculator_tasks / calculator_results)
und können jederzeit ausgewertet werden.

Diese Doku ist die Basis, um im nächsten Schritt
das Curriculum kritisch zu hinterfragen und gezielt weiterzutesten.

---

Wenn du magst, machen wir als nächstes genau Punkt 8:  
ein kleines Inspect-/Debug-Script, mit dem du dir auf der Konsole die letzten Aufgaben + Ergebnisse ansehen kannst, und dann schauen wir, ob das Curriculum sich so verhält, wie du es willst – oder wo wir es „schlauer“ machen.

<a id="docs_curriculum_math_tasks_md"></a>

## Quelle: `docs/curriculum_math_tasks.md`

**Originaltitel:** ORÓMA – Mathe-Curriculum & Aufgabengeneratoren

1. Doku: docs/curriculum_math_tasks.md

# ORÓMA – Mathe-Curriculum & Aufgabengeneratoren

**Datei:** `docs/curriculum_math_tasks.md`  
**Projekt:** ORÓMA – KI-JWG-X1  
**Stand:** 2025-12-13  
**Autor:** Jörg Werner + GPT-5.1 Thinking  

---

## 1. Zweck dieses Dokuments

Dieses Dokument beschreibt das **Mathe-Curriculum** in ORÓMA, basierend auf:

- den bestehenden Patch-1-Komponenten (`hooks_patch1`, Calculator, SelfAssessment, TransferSnaps),
- den real beobachteten Logs (Curriculum-Level 1–6, Wiederholungsaufgaben),
- einer geplanten Erweiterung des Aufgabenspektrums über reine „Klassiker“ hinaus.

Ziel:

1. Transparenz: Wie funktionieren die Levels, Aufgaben und Wiederholungen heute?
2. Erweiterung: Welche **neuen Aufgabentypen** (Lückentexte, Zahlenfolgen, Sudoku-Hooks, Logikrätsel) planen wir?
3. Integration: Wie können diese Aufgaben kontrolliert in das Curriculum einsickern, ohne alles kaputtzurefaktorisieren?

---

## 2. Status quo (Ende 2025): Mathe-Curriculum v1

### 2.1 Beobachtungen aus Logs & DB

Ausschnitt aus einem realen Lauf (gekürzt):

```text
[Curriculum] Level 1, Aufgabe 3/4 erledigt
...
[Curriculum] Level 6, Aufgabe 3/3 erledigt
[Curriculum] Alle Levels abgeschlossen 🎉 – Neustart bei Level 1
...
[Curriculum] Wiederholung erledigt: x^2+2x+1=0
[Curriculum] Wiederholung erledigt: 2/4
...
[TTS Fallback]  Meine Confidence ist niedrig: 0.55. Meine Coverage ist 1.00. Neuheit: 0.40. Zeit bis Ziel (normiert): 0.50. Ich passe mein Üben an.

DB-Summary einiger typischer Aufgaben:

1/2:             tasks= 4740, results= 4740, correct= 4740, incorrect=0
2/4:             tasks= 4495, results= 4495, correct= 4495, incorrect=0
x^2+2x+1=0:     tasks=10269, results=10269, correct=10269, incorrect=0
x^2-4=0:        tasks=14329, results=14328, correct=14328, incorrect=0
2x+3=7:         tasks= 6882, results= 6882, correct= 6882, incorrect=0
3x-4=5:         tasks= 9483, results= 9482, correct= 9482, incorrect=0
pi:             tasks= 4267, results= 4267, correct= 4267, incorrect=0
phi:            tasks= 4396, results= 4396, correct= 4396, incorrect=0
e:              tasks= 5263, results= 5263, correct= 5263, incorrect=0

Interpretation:
	•	Das Curriculum arbeitet aktuell mit einem kleinen, festen Set an „Kernaufgaben“:
	•	Brüche: 1/2, 2/4
	•	Quadratische Gleichungen: x^2+2x+1=0, x^2-4=0, x^2-5x+6=0, …
	•	Lineare Gleichungen: 2x+3=7, 3x-4=5, x+7=10, …
	•	Spezialwerte: pi, phi, e.
	•	Diese Aufgaben tauchen:
	•	in den normalen Level-Slots auf („Level 3, Aufgabe 2/4 erledigt“),
	•	und als explizite Wiederholungen („Wiederholung erledigt: x^2+2x+1=0“).
	•	SelfAssessment- und TransferSnaps hängen direkt dran:
	•	nach Blöcken oder Wiederholungen werden SelfAssessment-Snaps + TransferSnaps erzeugt,
	•	das Curriculum wertet diese Signale aus („Confidence niedrig → Wiederholung“).

Kurzfassung:

Das aktuelle Mathe-Curriculum ist sehr fokussiert: wenige, immer wiederkehrende Kernaufgaben, dafür gute Integration mit SelfAssessment, Transfer und Curriculum-Logik.

⸻

2.2 Grundprinzip des Curriculums (heute)

Vereinfacht (aus Logs rekonstruiert):
	1.	Es gibt Level (z. B. 1–6).
	2.	Jedes Level hat eine feste Anzahl an „Slots“:
	•	z. B. Level 1: 4 Aufgaben, Level 4: 3 Aufgaben usw.
	3.	Für jeden Slot:
	•	wird eine Calculator-Task erstellt (calculator_tasks),
	•	wird ein Ergebnis gespeichert (calculator_results),
	•	wird die Curriculum-Position fortgeschrieben (Level X, Aufgabe Y/Z erledigt).
	4.	Zusätzlich:
	•	SelfAssessment-Hooks erzeugen Selbst-Einschätzungs-Snaps,
	•	Transfer-Hooks erzeugen TransferSnaps (Zusammenhang zu anderen Aufgaben),
	•	„MangelSpeak“/TTS geben Feedback bei niedriger Confidence und stoßen gezielte Wiederholungen an.

Wichtig:
Die Aufgabenmenge ist sehr klein, aber die Curriculum-Logik (Levels, Wiederholungen, SelfAssessment, Transfer) ist bereits relativ ausgereift.


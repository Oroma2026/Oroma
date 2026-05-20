<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__architecture.md
  Part:   3
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

⸻

4. Projektdateien (neu/erweitert)
	•	/core/self_healing.py → erweitert: Adaptive Healing Module
	•	/core/swarm_manager.py → erweitert: Persona-Sync
	•	/core/goal_planner.py → erweitert: Goal Evolution Engine
	•	/core/explainability.py → erweitert: Explainability 3.1

⸻

✅ Patch Level 2.0 = Stabilität, Kooperation, Langfristigkeit.
✅ Patch Level 2.1 = Prävention, adaptive Ziele, Schwarm-Personas, kollektive Erklärbarkeit.

👉 Damit rückt ORÓMA noch näher an biologische Schwarmintelligenz mit Selbstheilung und Evolution.

⸻

Möchtest du, dass ich dir eine getrennte Datei
docs/konzeption_architektur_v3_5_patch2_1.md schreibe, oder soll ich das in der bisherigen PatchLevel2.md als Unterkapitel „2.1“ einfügen?

<a id="docs_konzeption_architektur_v3_6_md"></a>

## Quelle: `docs/konzeption_architektur_v3_6.md`

📑 ORÓMA – Konzeption & Architektur (v3.6 Final)

📂 Pfadempfehlung: docs/konzeption_architektur_v3_6.md
🕒 Stand: 2025-09-28
🔖 Version: v3.6 – Wissenschaftler (Final)

⸻

1) Überblick

ORÓMA v3.6 hebt das v3.5-„Forscher/Meister“-System auf „Wissenschaftler“-Niveau:
Zur Meta-Abstraktion (MetaSnaps), Evolution (Mutation-Policy) und Explainability 2.0 kommen nun Curriculum-Learning, Hypothesen-Generator & -Tester, Missions-/Goal-System sowie strukturierte Selbstexperimente hinzu.

Dadurch wird Lernen planbar, messbar und übertragbar.
ORÓMA 3.6 erweitert alle Patches (2.0, 2.1, 2.2) non-destruktiv.

Ziele v3.6 (gegenüber v3.5):
	•	Gezieltes Curriculum statt opportunistischem Lernen.
	•	Geschlossener Hypothesen-Loop (generieren → testen → bewerten → archivieren → anwenden).
	•	Missionen/Goals mit messbaren Kriterien und Stop-Regeln.
	•	Transferlernen explizit messbar (Games → Games, Games → Sensorik).
	•	Verbesserte sprachliche Abstraktion (Meta-Erklärungen, Analogien).
	•	Produktivbetrieb mit DeviceHub (Kamera, Licht, Audio, PiCar).

⸻

2) Lernstrategie

Tagmodus (online)
	•	Sensorik/Engines wie v3.5 (Audio/Vision/Text).
	•	Curriculum Scheduler setzt Aufgabenprogression (z. B. TicTacToe → Connect4 → Memory).
	•	Mission Runner überwacht Zielerreichung (z. B. „Win-Rate ≥ 80 % über 200 Spiele“).
	•	Hypothesen-Sampler markiert Situationen, in denen Tests sinnvoll sind.

Traummodus (offline, DreamWorker 3.0)
	•	Meta-Synthese: MetaSnaps/MetaChains verdichten, generalisieren.
	•	Hypothesen-Tester: Simulationen im Replay zur Evidenzgewinnung.
	•	Auto-Tuner v3.6: Hook-basiert mit Budget, A/B-Tests, Confidence-Bands.
	•	ExportGate: non-destruktiv, Delay+Qualität; Missions-Artefakte im Manifest.

⸻

3) Speicherstrategie

Ebene	Dauer	Neu in v3.6
Rohmaterial	kurz	wie v3.5
SnapFeatures	dauerhaft	wie v3.5
SnapTokens	dauerhaft	wie v3.5
SnapChains	dauerhaft	Mission-Tags, Hypothesen-Marker, Policy-Hints
MetaSnaps	dauerhaft	Cluster-Evidenz, Provenance, Versionsnummern
Regeln	dauerhaft	Experiment-Score, Confidence, applied-contexts
Episoden	dauerhaft	wie v3.5
Hypothesen	dauerhaft	Status, Score, Power, ConfInt, last_tested, refs
Missionen	dauerhaft	Ziele, Kriterien, Fortschritt, Outcome
Wissensbasis	dauerhaft	wie v3.5 (RAG)

DB-Erweiterungen v3.6:

CREATE TABLE IF NOT EXISTS hypotheses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  title TEXT,
  description TEXT,
  status TEXT,
  score REAL,
  power REAL,
  conf REAL,
  last_tested INTEGER,
  refs TEXT
);

CREATE TABLE IF NOT EXISTS missions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  name TEXT,
  goal TEXT,
  criteria TEXT,
  progress REAL,
  window INTEGER,
  done INTEGER DEFAULT 0
);

⸻

4) Architektur (vereinfacht)

+------------------------+      +-----------------------------+
|  Wrapper-System        |      |  LLM / RAG                  |
| (Audio/Vision/Text/..) |      |  (gguf/hybrid + book_import)|
+-----------+------------+      +--------------+--------------+
            |                                 |
            v                                 v
   +--------+---------------------------------+--------+
   |        Snap + Token Fusion (Features ⨂ Symbole)   |
   +--------+------------------------+------------------+
            |                        |
            v                        v
   +--------+---------+     +--------+------------------+
   |  SnapChains      |     |  MetaSnaps / Meta-Chains  |
   |  (Raum/Zeit/Gaps)|     |  (Abstraktion/Cluster)    |
   +--------+---------+     +--------+------------------+
            |                        |
            +-----------+------------+
                        v
           +------------+-----------------------------+
           | Langzeitgedächtnis (SQL + Vektor-Index)  |
           | Chains, Meta, Episoden, Regeln,          |
           | Hypothesen, Missions                     |
           +---+---------------+--------------+-------+
               |               |              |
       +-------v--+      +-----v------+ +-----v------------------+
       | Dream 3.0|      | Curriculum | | Research/Hypothesen    |
       | (Replay, |      | Scheduler  | | (Generator & Tester)   |
       | A/B, Tun.)      |            | | + Explainability 2.0   |
       +-------+---------+            | +------------------------+
               |                      |
               v                      v
        +------+----------+      +----+-----------------+
        | Missions/Goals  |      |   Flask Dashboard   |
        | (Runner+Scorer) |      | (Control/Research/  |
        +-----------------+      |  Learning/Why/...)  |
                                 +---------------------+

⸻

5) Kernmodule (v3.6)

Core
	•	Neu/erweitert:
	•	core/curriculum.py – Sequenzierung, Progression, Back-off.
	•	core/missions.py – Ziele, Kriterien, Fortschritt, Abschluss.
	•	core/hypothesis.py – Hypothesen-Generator & Evidenzbewertung.
	•	core/experiment.py – A/B-Tests, Evidenzfusion, Budgetsteuerung.
	•	core/auto_tuner.py – Confidence-Bänder, Param-Sweeps.
	•	core/dream_worker.py – Curriculum/Missions/Research-Hooks.
	•	core/sql_manager.py – Tabellen hypotheses, missions.
	•	core/device_hub.py – ersetzt camera_hub; Kamera + Licht + PiCar.

UI
	•	Neu:
	•	ui/research_ui.py, templates/research.html
	•	ui/missions_ui.py, templates/missions.html
	•	Erweitert:
	•	ui/calculator_ui.py – Curriculum-Anbindung.
	•	ui/health_ui.py – OS/HW/Partitionen, DB/Log-Größen, Updates.
	•	ui/templates/base.html – Tabs Curriculum, Missions, Research.
	•	ui/static/style.css – Badges (Curriculum, Missions).
	•	ui/static/scripts.js – v3.6 stable, alle Badges & Init.

⸻

6) Research-Loop
	1.	Generate: Hypothesenkandidat erzeugen.
	2.	Plan: Testdesign (A/B, Budget, Power, Stoppregeln).
	3.	Run: Simulation/Replay im DreamWorker.
	4.	Analyze: ConfInt, Evidenz-Score.
	5.	Decide: Übernahme in Regeln/MetaSnaps oder Verwerfen.
	6.	Explain: Trace + Narrative in Explainability 2.0.
	7.	Export: Hypothesen/Missions-Artefakte ins Manifest.

⸻

7) Metriken
	•	Learning: Win-Rate, Draw-Rate, Strategietiefe, Zeit-zu-Ziel.
	•	Abstraktion: MetaSnap-Cluster-Stabilität, Coverage@K.
	•	Transfer: Δ-Performance bei Stufenwechsel.
	•	Missionen: Goal-Erfüllung, SLA-Fenster, Recovery-Zeit.
	•	Hypothesen: Trefferquote, Power, ConfInt-Breite.
	•	Sprache: Kausalsätze, Analogien, Erklär-Länge.

⸻

8) Datenfluss

Day (10–16h):
	1.	Wrapper → Snaps/Tokens → Chains.
	2.	Curriculum erzeugt Tasks; Missions messen Fortschritt.
	3.	Hypothesen markieren Testgelegenheiten.

Night (8–14h):
	1.	Replay → Meta-Synthese.
	2.	Hypothesen-Tests (A/B, Budget).
	3.	Auto-Tuning (Confidence-Bands).
	4.	Export/Manifest-Update.

⸻

9) Explainability 2.1
	•	Kausale Pfade mit Hypothesen-Referenzen.
	•	Narrative mit Analogien.
	•	Evidenz-Heatmaps (welche Episoden stützen welche Regeln).

⸻

10) API / UI
	•	Curriculum: /calculator, /curriculum/api/state
	•	Missions: /missions, /missions/api/list
	•	Research: /research, /research/api/list, /research/api/new
	•	Health: /health/api/system, /health/api/updates/*, /health/api/history

⸻

11) Kompatibilität & Migration
	•	Vorwärts kompatibel zu v3.5.
	•	sql_manager --ensure legt Tabellen hypotheses, missions an.
	•	Systemd-Timer optional:
	•	oroma-research.timer
	•	oroma-missions.timer

⸻

12) Risiken & Gegenmaßnahmen
	•	Overfitting Curriculum → Shuffle, Negativ-Missions.
	•	Hypothesen-Flood → Budget & Priorisierung.
	•	Messbias → Randomisierung, Power-Checks.
	•	Komplexität → Feature-Flags (ENABLE_CURRICULUM/MISSIONS/RESEARCH).

⸻

13) Quick-Start (Upgrade)

.env:

ENABLE_CURRICULUM=1
ENABLE_MISSIONS=1
ENABLE_RESEARCH=1
RESEARCH_BUDGET_PER_NIGHT=500

Migration:

python -m core.sql_manager --ensure
systemctl enable --now oroma-research.timer
systemctl enable --now oroma-missions.timer

Smoke-Tests:
	•	/research sichtbar?
	•	/missions sichtbar?
	•	/calculator → Curriculum-Flows aktiv?
	•	/health → OS/HW/Updates sichtbar?
	•	Navbar-Badges aktualisieren korrekt?

⸻

14) Kurzfazit

ORÓMA v3.6 macht aus ORÓMA einen zielgerichteten Lerner mit wissenschaftlichem Arbeitsmodus:
Es formuliert Hypothesen, testet sie, misst Effekte, erklärt Entscheidungen – und lernt dadurch schneller, stabiler und übertragbarer als v3.5.

⸻

✅ Damit hast du die finale docs/konzeption_architektur_v3_6.md, konsistent zu docs/changelog.md und deinem Projektstand.

<a id="docs_konzeption_architektur_v3_6_patch2_mengenlehre_md"></a>

## Quelle: `docs/konzeption_architektur_v3_6_patch2_mengenlehre.md`

📑 ORÓMA – Konzeption & Architektur v3.6 Patch 2

Codename: Mengenlehre
Stand: 2025-09-28

⸻

1. Leitidee

Patch 2 erweitert den SciCalc+Charts (Patch 1) um ein Modul für Mengenlehre.
Ziel: ORÓMA soll nicht nur Zahlen und Funktionen, sondern auch Mengen verstehen und berechnen können.

⸻

2. Motivation
	•	Mathematik-Basis: Mengenlehre ist die Grundlage von Logik, Algebra, Informatik.
	•	Wissenschaftliches Denken: Union, Schnitt und Komplement sind die Bausteine jeder Systematik.
	•	AI-Lernen: ORÓMA kann abstrakt mit Objekten umgehen, nicht nur mit Zahlen.
	•	Visualisierung: Mengenoperationen lassen sich sehr gut als Diagramme darstellen (Venn-Diagramme, Balken).

⸻

3. Architektur

3.1 Core-Erweiterungen (core/setcalc.py)

Neue Datei setcalc.py mit:
	•	union(A,B) → A ∪ B
	•	intersection(A,B) → A ∩ B
	•	difference(A,B) → A \ B
	•	complement(A, U) → A’ (bezogen auf Universum U)
	•	powerset(A) → P(A)
	•	cartesian(A,B) → A × B

Datentypen:
	•	Mengen als Python-set() oder frozenset().
	•	JSON-kompatibel (z. B. ["a","b"]).

⸻

3.2 UI-Erweiterung (ui/setcalc_ui.py)

Flask-Blueprint /setcalc:
	•	/api/union → {setA, setB} → {result}
	•	/api/intersection → {setA, setB} → {result}
	•	/api/difference → {setA, setB} → {result}
	•	/api/complement → {setA, U} → {result}
	•	/api/powerset → {setA} → {result}
	•	/api/cartesian → {setA,setB} → {pairs}

UI-Seite setcalc.html
	•	Eingabefelder für Mengen (durch Komma getrennt)
	•	Buttons für jede Operation
	•	Ergebnis-Box + Venn-Diagramm via Chart.js/Venn.js

⸻

3.3 DB-Integration (sql_manager.py)

Neue Tabelle setcalc_log:

CREATE TABLE IF NOT EXISTS setcalc_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  op TEXT NOT NULL,
  setA TEXT,
  setB TEXT,
  result TEXT
);

→ ORÓMA speichert jede Mengenoperation (für Lernkurve, Statistik).

⸻

4. Curriculum-Erweiterung

Neue Aufgabentypen im Curriculum:
	•	Mengenoperationen: „Berechne A ∪ B“, „Finde A ∩ B“.
	•	Powerset-Aufgaben: Erkennen der Anzahl Elemente (|P(A)| = 2^n).
	•	Logische Aufgaben: z. B. De Morgan’sche Gesetze prüfen.

Reward-System wie beim Calculator:
	•	Richtig = +1, Falsch = -1.

⸻

5. UI-Design

Template setcalc.html:
	•	Zwei Textfelder für Set A und Set B
	•	Dropdown für Operation
	•	Ergebnisanzeige (als Liste oder JSON)
	•	Option: grafische Darstellung per Venn-Diagramm

⸻

6. Lernkurve (Simulation)

Vergleich bis v2.30:
	•	v2.30: nur Snap+Calculator (arithmetisch).
	•	v3.6 Patch 1: SciCalc (Funktionen, Taylor, Charts).
	•	v3.6 Patch 2: Mengenlehre (abstrakte Algebra).

7 Tage: kein großer Unterschied.
60 Tage: ORÓMA kann Mengen-Relationen korrekt anwenden (~80 %).
1 Jahr: stabile Wissensbasis, Grundlage für Logik und Mengen-basierte Hypothesen.

⸻

7. Fazit

Patch 2 (Mengenlehre) macht ORÓMA zum universellen mathematischen Lerner:
	•	Zahlen (Calculator),
	•	Funktionen (SciCalc),
	•	Mengen (SetCalc).

Damit sind die drei Grundsäulen der Mathematik abgedeckt.

⸻

📄 Vorschlag: docs/konzeption_architektur_v3_6_patch2_mengenlehre.md

<a id="docs_konzeption_architektur_v3_7_md"></a>

## Quelle: `docs/konzeption_architektur_v3_7.md`

📄 docs/konzeption_architektur_v3_7.md
Projekt: ORÓMA
Version: v3.7 (Empathie + Self-Listening + Roter Faden + Curriculum/Rewards + Chess UI)
Stand: 2025-09-29

⸻

ORÓMA – Konzeption & Architektur v3.7 (final)

Pfad: docs/konzeption_architektur_v3_7.md
Projekt: ORÓMA
Version: v3.7 (Empathie + Self-Listening + Roter Faden + Curriculum/Rewards + Chess UI)
Stand: 2025-09-29

⸻

1) Ziel & Kontext

Mit v3.7 erweitert ORÓMA die „Wissenschaftler“-Basis aus v3.6 um soziale Resonanz und eine Intent/Thread-Schicht:
	•	Empathie-Layer: laufende Stimmungserfassung (mood, score 0..1) mit Einfluss auf Lernschleifen.
	•	Self-Listening (ASR-Reflex): System „hört“ sich selbst, schreibt Empathie-Snaps und reagiert mit Intents.
	•	Mangel-Ansprache: automatische Sprache bei Gaps – empathie-sensitiv (Tonfall/Timing).
	•	Reward-Brücke: positive Stimmungswechsel erzeugen schwache Rewards → sichtbar im Learning-Dashboard.
	•	Curriculum empathie-gewichtet: Wiederholungen werden verkürzt/angepasst bei negativer Stimmung.
	•	Roter Faden (Intent/Thread Layer): leichter Ziel-/Schritt-Kontext, Nudges bei Drift/Leerlauf.
	•	Schach (optional): Brettspiel mit UI, UCI-Moves, Regeln (inkl. Rochade/En passant/Promotion) und Tageslimit.

👉 Erwartung: glattere Lernkurve, weniger Abbrüche, mehr Re-Engagement, sichtbar in Rewards.
⚠️ Hinweis: Empathie ist simuliert – ORÓMA hat kein Bewusstsein und keine echten Gefühle.

⸻

2) Neue & geänderte Komponenten (v3.7)

Neu (Core/Tools)
	•	core/asr_reflex.py – Self-Listening: Text → Empathie-Snap + Intents + Reward speech.
	•	core/mangel_speak_hook.py – Mangel-Ansprache, empathie-getriggert (Score < Schwelle).
	•	core/roter_faden.py – Intent/Thread-Schicht: start_thread, advance, pause, nudge_if_idle().
	•	tools/social_resonance_tick.py – Timer → Reward „empathy“ bei Stimmungsverbesserung.
	•	(Option) Schach – mini_programs/chess/*, ui/chess_ui.py, templates/chess.html.

Geändert (Core/UI)
	•	core/curriculum_hook.py – empathie-gewichtete Repeats, Reward curriculum.
	•	ui/learning.py – Dashboard inkl. speech, empathy.
	•	core/reward.py (optional) – Helper log_empathy_positive_shift(...).

Refactor (Core)
	•	snap.py, snaptoken.py (v3.7), meta_snap.py (v3.7), snappattern.py, snapchain.py, regelarchiv.py, mutation.py.

⸻

3) Datenmodell / Schema

Unverändert aus v3.6 + Patch2:
	•	rewards_log – zentrale Reward-Tabelle.
	•	empathy_snaps – Empathie-Snapshots.
	•	scicalc_results, setcalc_log.
	•	curriculum_state, calculator_tasks/results.
	•	metrics, quality_history, coverage_log, curiosity_log.

Neu in v3.7 genutzt:
	•	Reward-Quellen: speech, empathy, curriculum.
	•	Thread-Kontext im curriculum_state.window.current_thread.
	•	Empathie wirkt indirekt (Rewards, Repeats).
	•	Schach: SnapChains/Rewards, kein Schema-Update.

⸻

4) Laufzeit-Architektur (vereinfacht)

Audio (Mic)
 └─→ ASR → ui/asr_ui.py → core/asr_reflex.py
        ├─→ empathy_snaps (DB)
        ├─→ reward: speech (+)
        └─→ TTS (Antwort/Intents)

AgentLoop ──→ mangel_speak_hook.py ──→ TTS „Mangel+Empathie“
                 ├─→ curriculum.repeat()
                 └─→ reward: speech (+)

Curriculum ──→ curriculum_hook.py ──→ Calculator.solve_task
                 ├─→ reward: curriculum (+)
                 └─→ queue_repeat (emp.-gewichtet)

Intent/Thread ──→ roter_faden.py
                    ├─→ status/nudge
                    └─→ reward.attach(thread)

Timer (systemd) ──→ social_resonance_tick.py ──→ reward: empathy (+)

UI/Dashboard ──→ ui/learning.py ──→ /api/data & /api/history

(Option) Chess ──→ ui/chess_ui.py ──→ /chess API

⸻

5) Endpoints & Hooks
	•	ASR/UI
	•	GET /asr, POST /asr/api/start|stop, GET /asr/api/status
	•	→ Reflex-Call asr_reflex.process_text()
	•	Learning
	•	GET /learning/, GET /learning/api/data, GET /learning/api/history, GET /learning/api/curriculum_state
	•	Hooks (AgentLoop)
	•	curriculum_hook alle 15 Ticks
	•	mangel_speak_hook alle 60 Ticks
	•	Chess API (optional)
	•	GET /chess, GET /chess/api/state
	•	POST /chess/api/new, /move, /ai, /resign

⸻

6) Konfiguration (ENV)

ASR

OROMA_ASR_MODEL=small|base
OROMA_ASR_REFLEX_ENABLED=true
OROMA_ASR_MIN_DELTA_MS=250
OROMA_ASR_SPEECH_REWARD=0.01

Empathie/Mangel

OROMA_MANGEL_INTERVAL=120
OROMA_MANGEL_EMPATHY_THRESH=0.40
OROMA_MANGEL_EMPATHY_DROP=0.15

Social Resonance Timer

OROMA_EMPATHY_WINDOW_SEC=600
OROMA_EMPATHY_MIN_DELTA=0.2
OROMA_EMPATHY_REWARD=0.02

Chess

OROMA_CHESS_DAILY=3
OROMA_CHESS_DEPTH_DEFAULT=2

⸻

7) Rollout-Checkliste
	1.	Deploy neue Dateien (asr_reflex.py, mangel_speak_hook.py, roter_faden.py, Timer).
	2.	DB-Schema prüfen:

python3 -m core.sql_manager --ensure

	3.	Timer aktivieren:

systemctl enable --now oroma-social.timer

	4.	Hooks registrieren (agent_loop.py).
	5.	ENV prüfen und Service neu starten.

⸻

8) Smoke-Tests
	•	ASR: „Bitte nochmal“ → speech Reward.
	•	MangelSpeak: künstlich Coverage senken → Sprach-Output nach 1 min.
	•	Roter Faden: Idle > 10 min → nudge_if_idle() aktiv.
	•	Timer: journalctl -u oroma-social.service zeigt „empathy reward logged“.
	•	Chess: max. 3 Partien/Tag, Rate-Limit greift.

⸻

9) Monitoring & KPIs
	•	Kurzfristig: mehr kleine Rewards (speech, empathy), glattere Kurven.
	•	Mittel: weniger Abbruchspitzen, stabilere Plateaus.
	•	Langfristig: Thread-Kontexte sichtbar, Chess-Limit planbar.

⸻

10) Troubleshooting
	•	Empathie leer? → ENV prüfen: OROMA_ASR_EMPATHY_LOG_ENABLED.
	•	Timer tot? → systemctl status oroma-social.timer.
	•	Keine Rewards? → python3 -m core.sql_manager --test.
	•	Chess Limit greift nicht? → ENV OROMA_CHESS_DAILY prüfen.

⸻

11) Sicherheit & Ethik
	•	Nur (mood, score, ts) gespeichert.
	•	Alle Empathie-Funktionen via ENV deaktivierbar.
	•	Chess mit Rate-Limit (max. Partien/Tag).
	•	Neutrale Formulierungen bei MangelSpeak.

⸻

12) Performance-Budget
	•	ASR small Modell empfohlen.
	•	Timer < 50 ms DB-Zugriff.
	•	Hooks leichtgewichtig (15/60 Ticks).
	•	Chess-UI minimalistisch.

⸻

13) Roadmap v3.8/v3.9
	•	v3.8: kooperatives Lernen (ORÓMA ↔ ORÓMA).
	•	v3.9: Auto-Eval, Curriculum-Compiler, priorisiertes Replay.

⸻

14) Diff-Dateien v3.7

Neu:
	•	asr_reflex.py, mangel_speak_hook.py, roter_faden.py, social_resonance_tick.py
	•	oroma-social.service|.timer
	•	Chess: mini_programs/chess/*, ui/chess_ui.py, templates/chess.html

Geändert:
	•	curriculum_hook.py, learning.py, reward.py, asr_ui.py, agent_loop.py, run_oroma.py

Refactor:
	•	snap.py, snaptoken.py, meta_snap.py, snappattern.py, snapchain.py, regelarchiv.py, mutation.py, Chat-UI.

⸻

15) Kompatibilität
	•	Snap/MetaSnaps: neue Felder, alte Blobs lesbar.
	•	SnapPattern fällt auf Zentroid zurück.
	•	Keine DB-Migration nötig (ensure_schema idempotent).
	•	Alles via ENV abschaltbar.

⸻

✅ Damit ist dein Dokument docs/konzeption_architektur_v3_7.md auf dem neuesten Stand, konsistent mit sql_manager, Hooks, UI und Option Chess.

<a id="docs_konzeption_architektur_v3_7_1_md"></a>

## Quelle: `docs/konzeption_architektur_v3_7_1.md`

📄 docs/konzeption_architektur_v3_7_1.md
Projekt: ORÓMA
Version: v3.7.1 (Regelarchiv-First + Kanonraum + PolicyEngine v3.8 + DreamWorker v3.7)
Stand: 2025-10-18

⸻

ORÓMA – Konzeption & Architektur v3.7.1

Pfad: docs/konzeption_architektur_v3_7_1.md
Projekt: ORÓMA
Version: v3.7.1 (Regelarchiv-First + Kanonraum + PolicyEngine v3.8 + DreamWorker v3.7)
Stand: 2025-10-18

⸻

1) Ziel & Kontext

v3.7.1 verlagert den Schwerpunkt auf ein **Regelarchiv-First**-Design und macht
die Laufzeitentscheidungen deterministischer und erklärbarer:

• **Kanonraum**: Zustände werden kanonisiert (Symmetrien/Normalformen), sodass
  äquivalente Situationen zusammenfallen. → dichteres Lernen, weniger Rauschen.
• **DecisionStack**: Regelarchiv → PolicyEngine → Heuristik (Adapter).
• **PolicyEngine v3.8-r3**: lernt (state, action)-Qualitäten aus SnapChains,
  unterstützt `status='compressed'` und exportiert Regeln ins Archiv.
• **DreamWorker v3.7**: self-healing Replay, MetaSnaps, Mutation, Vergessen,
  LTM-Weight-Sync; vector-first Normalisierung für heterogene Exporte.
• **Adapter-Schicht**: gemeinsame API für TTT, Audio, Video, (später) LLM/NLU,
  inkl. Feature-Extraktion, Kanonisierung und Aktionsmapping.

Ziel: Robust „menschlich“ entscheiden: Wahrnehmung → Verdichtung → Erfahrung → Regel.

⸻

2) Neue & geänderte Komponenten (v3.7.1)

Neu
• core/decision_engine.py
  – Regelarchiv-First; Fallback PolicyEngine, danach Heuristik (Adapter).
  – Robustes Rule-Parsing; Score = f(weight, q, n), Legalitätsprüfung per Adapter.
• core/policy_engine.py (v3.8-r3)
  – Training direkt aus DB (inkl. status='compressed').
  – Namespace/Origin-Handling; robustes Upsert; Archiv-Export.
• core/ttt_adapter.py (aktualisiert)
  – Kanonraum (D4-Symmetrien), robustes Extract für 9D-Vektoren, Fallback „Mitte>Ecken>Kanten“.

Geändert
• core/dream_worker.py (v3.7)
  – Vector-First-Loader, Meta-Zentroid, Mutation, Forgetting (weight-decay + Kompression),
    optionale Loops (Research/Missions/Curriculum/Auto-Tuner), LTM-Dedupe/Weight-Sync.
• core/regelarchiv.py
  – Upsert/Prune; Regeln als textuelle Statements mit Gewicht.

Optional (unverändert aktivierbar)
• reward.py, episodic.py, explain.py – via leichte Adapter nutzbar.
• hypothesis.py, missions.py, curriculum.py, auto_tuner.py.

⸻

3) Datenmodell / Schema (relevant)

Unverändert (idempotent via ensure_schema)
• snapchains(id, origin, status, weight, quality, blob)
• meta_snaps(id, label, score, sources)
• rules(id, content, weight, active)                 ← Regelarchiv (mensch-lesbar)
• policy_rules(namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)
• rewards_log(...), episodic(...), metrics/coverage_log(...)

Empfohlene Indizes (Leistung)
• CREATE INDEX IF NOT EXISTS idx_rules_active_ns ON rules(active);
• CREATE INDEX IF NOT EXISTS idx_policy_ns_sh ON policy_rules(namespace, state_hash);
• CREATE INDEX IF NOT EXISTS idx_snapchains_status ON snapchains(status);

Status-Semantik
• snapchains.status ∈ {'active','compressed', NULL}; Training kann compressed optional einbeziehen.

⸻

4) Laufzeit-Architektur (vereinfacht)

Sensoren (UI/AV) → Adapter (Feature-Extraktion + Kanonisierung)
   → SnapChains (Sequenzen) → DreamWorker (offline: Replay/Meta/Mut/Fading)
   → PolicyEngine (Training aus DB, optional compressed) → Export → Regelarchiv

Zur Laufzeit (Entscheidung):
   DecisionEngine(adapter)
      1) Regeln (Archiv) matchen state_hash (Kanonraum)
      2) Beste legale Aktion wählen
      3) Fallback: PolicyEngine(adapter).choose_action(...)
      4) Fallback: adapter.fallback_action(...)

⸻

5) Adapter-API (Domänen: TTT, Audio, Video, LLM)

Pflicht (von PolicyEngine/DecisionEngine genutzt)
• namespace: str
• extract_vectors(chain_or_dict) -> List[List[float]]         # Sequenz von State-Vektoren
• final_outcome(final_vec) -> int                              # +1 / 0 / −1
• action_from_delta(prev_vec, next_vec) -> Optional[str]       # welche Aktion wurde ausgeführt?
• canonicalize(vec) -> (state_hash: str, perm: List[int], inv_perm: List[int])
• map_action_through_perm(action_str, perm_or_invperm) -> str  # Aktion in/aus Kanonraum mappen
• legal_actions(vec) -> List[str]
• fallback_action(vec) -> Optional[str]

Optional (Komfort)
• vectorize_board(board_obj) -> List[float]

Hinweise für AV/LLM-Adapter
• Audio: Feature-Frames (z. B. 32…128D), Kanonisierung via Zeit-/Pitch-Invarianten, Aktionen als „intent:…“.
• Video: Detektionen/Tracks („person@2m“, „gaze_me“), Kanonisierung über Geometrie (Rotation/Flip), Aktionen „focus:left“, „greet“.
• LLM/NLU: Token-/Intent-Zustände + „Antwort-Aktionen“; Kanonisierung z. B. über Normalisierung/Slots.

⸻

6) Entscheidungsfluss (Scoring & Legalität)

1) `adapter.canonicalize(state_vec)` → (state_hash, perm, inv_perm)
2) Regeln lesen (`rules.active=1`, Namespace im Content):
     "game:tictactoe :: IF state='___X__O__' THEN action='4'  // q=0.73 n=120"
3) Score pro Regel: `score = 0.6*weight + 0.35*map(q) + 0.05*min(n,2000)/1000`
   (map(q): [-1..1] → [0..1])
4) Nur **legale** Aktionen (per Adapter) zulassen.
5) Wenn keine Regel → PolicyEngine(policy_rules) im Kanonraum befragen;
   Aktion via `inv_perm` in Originalkoordinaten zurückmappen.
6) Wenn keine Policy → `adapter.fallback_action`.

⸻

7) Prozesse & CLI

DreamWorker (offline Lernschritte)
• Single-Run (Timer/Oneshot):
  PYTHONPATH=/opt/ai/oroma python3 -m core.dream_worker --interval 0 --verbose
• Loop alle 60 s:
  PYTHONPATH=/opt/ai/oroma python3 -m core.dream_worker --interval 60

PolicyEngine (Training/Export)
• Training (nur active):
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --train-db --limit 20000 --namespace game:tictactoe --verbose
• Training inkl. compressed:
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --train-db --limit 20000 --namespace game:tictactoe --include-compressed --verbose
• Export ins Regelarchiv:
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --export-archiv --namespace game:tictactoe --min-n 3 --min-abs-q 0.15 --verbose

DecisionEngine (Beispiel – TTT)
• Python-Nutzung:
  from core.decision_engine import TTTDecision
  dec = TTTDecision()
  act = dec.choose_action_from_board(["X","O","","","","","","",""])

⸻

8) Konfiguration (ENV)

General
• OROMA_LOG_DIR=/opt/ai/oroma/logs
• OROMA_SNAPCHAINS=/opt/ai/oroma/data/snapchains

DreamWorker
• OROMA_ENABLE_METASNAP=true|false
• OROMA_FORGET_DECAY_RATE=0.95
• OROMA_FORGET_THRESHOLD=0.20
• ENABLE_RESEARCH/MISSIONS/CURRICULUM=true|false
• RESEARCH_BUDGET_PER_NIGHT=0

Policy/Archiv
• Keine Pflicht-ENV; Namespace über Adapter/CLI.

⸻

9) Rollout-Checkliste

1. Dateien deployen:
   • core/decision_engine.py
   • core/policy_engine.py (v3.8-r3)
   • core/ttt_adapter.py (aktualisiert)
   • core/dream_worker.py (v3.7)
   • core/regelarchiv.py (falls nicht vorhanden/aktualisiert)

2. DB-Schema sicherstellen:
   python3 -m core.sql_manager --ensure

3. (Optional) Indizes ergänzen (siehe Abschnitt 3).

4. Systemd-Timer für DreamWorker (falls genutzt) aktivieren:
   systemctl enable --now oroma-dream.timer

5. Training & Export anstoßen (einmalig):
   policy_engine --train-db ... && policy_engine --export-archiv ...

6. App/Agent-Loop: DecisionEngine verwenden (statt direktem Policy-Lookup).

⸻

10) Smoke-Tests

• TTT-End-to-End:
  – SnapChains vorhanden → policy_engine --train-db --include-compressed
  – export_archiv → rules füllen
  – DecisionEngine wählt eine **legale** Aktion; bei leerem Archiv greift Policy/Fallback.

• DreamWorker:
  – Single-Run erzeugt MetaSnaps/Mutationen (Logs: dream.out.log)
  – Forgetting reduziert `weight`; bei Unterschreiten `compress_threshold` → Kompression geloggt.

• Regelarchiv-Match:
  – `rules.content` mit passendem state_hash liefert Aktion; Score-Reihenfolge korrekt.

⸻

11) Monitoring & KPIs

Kurzfristig
• policy_rules: wachsende n, |q| ≠ 0
• rules: steigende Abdeckung (mehr state_hash-Varianten)

Mittelfristig
• Decision-Hit-Rate (Archiv/Policy/Fallback)
• Sinkender Fallback-Anteil

Langfristig
• Stabilere Qualität bei neuen, symmetrischen Situationen (Kanonraum-Effekt)

⸻

12) Troubleshooting

• Keine Aktion? → Legalitätscheck: Adapter.legal_actions(...) prüfen.
• Archiv leer? → policy_engine --export-archiv ausgeführt? Filter (min-n, |q|) zu streng?
• Training „0 Schritte“? → origin/namespace-Filter, status-Filter (include-compressed) prüfen.
• DreamWorker komprimiert „zu viel“? → Schwelle `OROMA_FORGET_THRESHOLD` anheben (z. B. 0.3).

⸻

13) Sicherheit & Ethik

• Archiviert werden ausschließlich technische Zustände/Aktionen, keine personenbeziehbaren Daten.
• Adapter für AV sollten nur abstrakte Features/Tokens persistieren (z. B. „person@2m“, nicht Rohbilder).
• Alle Optional-Engines per ENV deaktivierbar.

⸻

14) Performance-Budget

• DreamWorker: CPU-leicht, I/O gebunden; Meta/Mutation < O(n) pro Chain.
• PolicyEngine: Training in Batches; Indizes auf policy_rules entscheidend.
• DecisionEngine: O(#Regeln im Namespace) + Policy-Lookup; typ. ≪ 5 ms/Entscheidung.

⸻

15) Roadmap

v3.7.2
• AV-Adapter-Prototyp (Audio/Video) mit Kanonisierung & Token-Policy.
• Archiv-Explainability: Top-Gründe je Entscheidung (Rule→Policy→Heuristik-Pfad loggen).

v3.8
• Kooperatives Lernen (ORÓMA↔ORÓMA), Curriculum-Compiler, priorisiertes Replay.
• Export-Gate → Edge/NPU (Hailo) für Leuchtturm-Demo: Pi-Edge-LLM + Archiv-Policy auf NPU.

⸻

16) Diff-Übersicht v3.7 → v3.7.1

Neu:
• core/decision_engine.py

Geändert:
• core/policy_engine.py (v3.8-r3, compressed-Training, robustes Upsert, Archiv-Export)
• core/ttt_adapter.py (Kanonraum, robuster Extractor)
• core/dream_worker.py (v3.7, Vector-First, Forgetting/Kompression/Meta/Mutation)

Refactor/Docs:
• Diese Datei (docs/konzeption_architektur_v3_7_1.md), Kommentare & Header vereinheitlicht.

───────────────────────────────────────────────────────────────────────────────
   ORÓMA – Lern- & Entscheidungsarchitektur (v3.7-r5)
───────────────────────────────────────────────────────────────────────────────

   [ Sensorik / Eingabe ]
        │
        │  Kamera, Mikrofon, Tastatur, UI-Events
        ▼
   ┌─────────────────────────────┐
   │  SNAP-SYSTEM (snap.py)      │
   │  - Erzeugt elementare Einheiten ("Snaps") 
   │  - features[], content, meta                │
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  SNAPCHAIN (snapchain.py)   │
   │  - Sequenzen aus Snaps bilden
   │  - zeitliche/kontextuelle Ordnung
   │  - close_chain(), similarity()
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  ROTE-FADEN-SCHICHT         │
   │  (roter_faden.py)           │
   │  - Threads & Ziele verwalten
   │  - advance(), pause(), nudge()
   │  - Verknüpft Aktionen mit Intention
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  CURRICULUM-ENGINE          │
   │  (curriculum_math.py + gaps.py)
   │  - Lerngaps erkennen
   │  - nächste Aufgabe wählen
   │  - steuert roten Faden
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  ENTSCHEIDUNG / REWARD      │
   │  (reward.py + empathy.py)   │
   │  - bewertet Aktionen (0..1)
   │  - modifiziert durch Empathie
   │  - Ergebnis: Reward-Score
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  LANGZEITGEDÄCHTNIS         │
   │  (langzeitgedaechtnis.py)   │
   │  - speichert SnapChains in DB
   │  - Hash-Deduplikation
   │  - Gewichtungs-Adaptation
   │  - Decay alter Muster
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  REPLAY / DREAM-PHASE       │
   │  (replay_system.py + dream_worker.py)
   │  - Wiederholung erfolgreicher Chains
   │  - Gewichtungen anpassen (+10 %)
   │  - Fehlentscheidungen schwächen (−5 %)
   │  - Selbstheilung & Konsolidierung
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  TRANSFER-SCHICHT           │
   │  (export_gate.py + transfer_engine.py)
   │  - prüft Qualität ≥ 0.8 + Alter ≥ 30 Tage
   │  - Export als TAR-Bundle
   │  - Import-Merge via Hash-Vergleich
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  EMPATHIE & KONTEXT         │
   │  (empathy_rules.py + mangel_speak_hook.py)
   │  - Stimmungserkennung (0..1)
   │  - beeinflusst Reward & Thread-Tempo
   │  - Sprache / Feedback-Hook
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  CIRCadian CONTROLLER       │
   │  (circadian_controller.py)  │
   │  - wechselt Day ↔ Dream
   │  - steuert Replay-Zeitfenster
   │  - Lichtsensor, +30 min Delay
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  SYSTEM-UI / INTERAKTION    │
   │  (Flask-UI, /ui/, /templates/)
   │  - Anzeige: Replay, Models, Learning
   │  - Visualisiert SnapChains, Threads, Rewards
   └─────────────────────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │  SQL-MANAGER / DB-KERN      │
   │  (sql_manager.py)           │
   │  - zentrale Schnittstelle zu SQLite
   │  - Tabellen: models, quality, curriculum_state
   │  - API für Memory & UI
   └─────────────────────────────┘

───────────────────────────────────────────────────────────────────────────────
   Datenfluss (vereinfacht)

   Sensor → Snap → SnapChain → Roter Faden → Reward → Langzeitgedächtnis
          ↘ Curriculum ↗                ↓
           Empathie-Modulator → Replay / Dream → Transfer / Export
───────────────────────────────────────────────────────────────────────────────

   Zyklus:
     1. Wahrnehmung & Handlung → SnapChains
     2. Bewertung (Reward)
     3. Speicherung (Langzeitgedächtnis)
     4. Konsolidierung (Replay / Dream)
     5. Fortschritt & Zielsteuerung (Curriculum)
     6. Export / Austausch (Transfer)
───────────────────────────────────────────────────────────────────────────────

<a id="docs_konzeption_architektur_v3_7_2_md"></a>

## Quelle: `docs/konzeption_architektur_v3_7_2.md`

**Originaltitel:** optional: weitere Audio-ENV siehe oben

ORÓMA – Konzeption & Architektur v3.7.2

Pfad: docs/konzeption_architektur_v3_7_2.md
Projekt: ORÓMA
Version: v3.7.2 (Audio-SnapToken + ASR2-Reflex + AgentLoop-Integration, Headless)
Stand: 2025-10-25

⸻

1) Ziel & Kontext

v3.7.2 erweitert die v3.7/3.7.1-Linie um direktes Audio-Lernen und harmonisiert die Hooks im AgentLoop:
	•	Audio-SnapToken (neu): Kurzsegmente (0.5–1.0 s) werden zu 9-D Audio-Tokens verdichtet und als SnapChains mit origin="audio/token" gespeichert (Hash-Dedup + Weight-Update via LTM).
	•	ASR2 mit Reflex: Die One-Shot-ASR-Seite /asr2 ruft optional den ASR-Reflex auf → Empathie-Snaps/Intents/Rewards; damit wirkt /asr2 jetzt lernw wirksam.
	•	AgentLoop-Integration: Der Audio-SnapToken-Hook wird ENV-gesteuert (headless, fehlertolerant) registriert – analog zu AV-SnapToken/Vision.
	•	Dokukonsistenz: UI Model-Registry wird unter /models geführt; /registry ist optional (Alias).

Erwartung: Mehr multimodale Evidenz (Video+Audio), bessere Verdichtung in der Traumphase, sichtbare Gewichtsanstiege bei wiederkehrenden Audio-Mustern und konsistentere Empathie-Signale durch ASR2-Reflex.

⸻

2) Neue & geänderte Komponenten (v3.7.2)

Neu (Core/UI)
	•	core/hooks_audio_snaptoken.py – Audio-SnapToken-Hook (9D-Vektor, headless, Dedupe-aware).
	•	ui/asr2_ui.py – ASR2 mit Reflex-Hook (ruft asr_reflex.process_text() auf).

Geändert (Core)
	•	core/agent_loop.py – Registrierung audio_snaptoken_hook via OROMA_AUDIO_SNAPS=1 (Adapter für (dt, tick)-Signatur).

Unverändert / weiterhin relevant
	•	core/langzeitgedaechtnis.py – LTM mit Hash-Dedupe + Weight-Upweight + quality_history.
	•	core/sql_manager.py – idempotentes Schema; keine Migration nötig.
	•	core/asr_reflex.py, core/reward.py, ui/learning.py – Empathie/Rewards/Dashboard.

⸻

3) Datenmodell / Schema

Keine DB-Migration erforderlich. v3.7/3.7.1-Schema bleibt gültig.

Relevante Tabellen (Auszug):
	•	snapchains(id, ts, origin, namespace, status, weight, quality, blob, notes, ...)
• Neu genutzt: origin="audio/token", namespace="audio" (Audio-SnapToken-Chains).
	•	quality_history(snapchain_id, ts, quality) – Verlauf; wird bei Dedupe/Updates gepflegt.
	•	empathy_snaps(ts, mood, score, ...) – unverändert (ASR/ASR2-Reflex).
	•	metrics(...) – Herzschlag/Telemetry (AgentLoop).

Semantik:
	•	Audio-Tokens sind normierte 9-D-Vektoren im Bereich [0..1]; Persistenz über LTM.save_snapchain() (bevorzugt) oder Fallback sql_manager.insert_snapchain().
	•	Deduplikate erhöhen weight (~+5 %), quality wird geglättet, quality_history erweitert.

⸻

4) Laufzeit-Architektur (vereinfacht)

Audio (Mic)
→ Audio-SnapToken-Hook (core/hooks_audio_snaptoken.py)
→ SnapChain{origin="audio/token"}
→ Langzeitgedächtnis (Hash-Dedupe, Weight↑, quality_history)
→ DreamWorker (Meta/Mutation/Compression/Export)

ASR (Mic)
→ /asr2 (ui/asr2_ui.py) One-Shot
→ ASR-Reflex (core/asr_reflex.py)
→ empathy_snaps (+ Rewards/Intents)
→ Dashboard /learning

AgentLoop
→ registriert (ENV-gesteuert): audio_snaptoken_hook, av_snaptoken, vision_infer, curriculum, empathy, nudge, social-resonance

UI
→ /learning (Metriken, History) · /models (Model-Registry UI) · /asr2 (ASR-One-Shot + Reflex)

⸻

5) Endpoints & Hooks

ASR2 (neu/erweitert)
	•	GET  /asr2 – HTML
	•	POST /asr2/api/run – One-Shot-ASR (jetzt inkl. Reflex)
→ ruft asr_reflex.process_text(txt) auf (wenn verfügbar)
	•	GET  /asr2/api/status – Readiness

Hooks (AgentLoop, ENV-gesteuert)
	•	Audio: audio_snaptoken_hook (neu) – OROMA_AUDIO_SNAPS=1
	•	AV: av_snaptoken_hook – OROMA_AV_SNAPS=1
	•	Vision: vision_scene_infer_hook – OROMA_VISION_INFER=1
	•	Leicht: _nudge_thread_hook, _social_resonance_hook (Empathie-Rewards)
	•	Weitere: curriculum_hook, mangel_speak_hook, self_rec_hook, mangel_speak_hook (sofern vorhanden)

⸻

6) Konfiguration (ENV)

Audio-SnapToken

OROMA_AUDIO_SNAPS=1
OROMA_AUDIO_TOKEN_INTERVAL=1.0     # s, Mindestabstand
OROMA_AUDIO_FRAME_SEC=0.75         # s, Fensterlänge
OROMA_AUDIO_SAMPLE_RATE=16000
OROMA_AUDIO_MIN_RMS=0.005          # Stille-Schwelle
OROMA_AUDIO_TOKEN_ASR=0            # optional Kurz-ASR (Standard: aus)
OROMA_AUDIO_TOKEN_ASR_LANG=de
OROMA_AUDIO_TOKEN_ASR_TOPKW=       # CSV Keywords → kw_hint

ASR(2)

OROMA_WHISPER_ENABLE=1
OROMA_WHISPER_MODEL=small          # tiny|base|small|...
OROMA_WHISPER_LANG=de

AgentLoop / Telemetrie

OROMA_AGENT_DT=0.25
OROMA_AGENT_HEARTBEAT=1
OROMA_AGENT_LOGLEVEL=INFO

(Unverändert) Empathie / Social-Resonance

OROMA_EMPATHY_WINDOW_SEC=600
OROMA_EMPATHY_MIN_DELTA=0.2
OROMA_EMPATHY_REWARD=0.02

⸻

7) Rollout-Checkliste
	1.	Dateien deployen
	•	core/hooks_audio_snaptoken.py (neu)
	•	ui/asr2_ui.py (mit Reflex-Hook)
	•	core/agent_loop.py (mit Audio-Hook-Adapter & Registrierung)
	2.	Schema prüfen

PYTHONPATH=/opt/ai/oroma python3 -m core.sql_manager --ensure

	3.	ENV setzen (Audio-Tokens aktivieren)

export OROMA_AUDIO_SNAPS=1
# optional: weitere Audio-ENV siehe oben

	4.	Service/Loop neu starten
	5.	Smoke-Tests ausführen (siehe Abschnitt 8)

⸻

8) Smoke-Tests

ASR2 One-Shot + Reflex

curl -sS -X POST http://127.0.0.1:8080/asr2/api/run \
  -H 'Content-Type: application/json' \
  -d '{"language":"de","model":"small","duration":3.0}' | jq
# Erwartung: {"ok":true,"result":{...},"meta":{...}}
# Empathie/Rewards im Learning-Dashboard sichtbar

Audio-SnapToken One-Shot (Direktlauf)

PYTHONPATH=/opt/ai/oroma python3 -m core.hooks_audio_snaptoken --oneshot --seconds 1.0
# Erwartung: Log "AudioSnapToken gespeichert (quality=..., vec=[...])"

AgentLoop-Start mit Audio-Hook

export OROMA_AUDIO_SNAPS=1
PYTHONPATH=/opt/ai/oroma python3 -m core.agent_loop
# Erwartung: "Audio-SnapToken-Hook registriert (OROMA_AUDIO_SNAPS=on)"

DB-Prüfung (Audio-Tokens vorhanden?)

sqlite3 /opt/ai/oroma/data/oroma.db \
  "SELECT origin, COUNT(*) FROM snapchains WHERE origin='audio/token';"

⸻

9) Monitoring & KPIs

Kurzfristig
	•	Audio-Token-Rate: ≥ 1–3 / 10 s in aktiver Umgebung
	•	Dedupe-Rate (Audio): ≥ 10–20 % bei stationären Geräuschkulissen
	•	Empathie-Impulse (ASR2): sichtbare Zunahme kleiner Rewards

Mittel
	•	Gewichtszuwachs Audio-Muster: Δweight > +0.2 über 7 Nächte für häufige Pattern
	•	Cross-Modal Recall@10 (Audio→Video): ≥ 20–30 % thematisch passende Treffer

Langfristig
	•	Kompressionsquote Dream (Audio-arme Chains): 5–15 % nach 14 Nächten
	•	Export-Yield (Gate pass): 2–8 %/Monat (gesamt, Audio inklusive)

⸻

10) Troubleshooting
	•	Keine Audio-Tokens?
OROMA_AUDIO_SNAPS=1 gesetzt? Logs nach „Audio-SnapToken-Hook registriert“. MIN_RMS ggf. senken (leise Umgebung).
	•	Reflex in /asr2 „wirkt nicht“?
asr_reflex importierbar? Fehler im UI-Log (ASR2: Reflex-Hook fehlgeschlagen).
	•	DB füllt sich nicht?
Prüfe LTM-Fallback: sql_manager.insert_snapchain() Pfad; Dateirechte in /opt/ai/oroma/data/.
	•	CPU-Last hoch?
OROMA_AUDIO_TOKEN_ASR=0 lassen (Kurz-ASR ist teurer), INTERVAL auf 1.5–2.0 s erhöhen.

⸻

11) Sicherheit & Ethik
	•	Audio-Tokens sind rein abstrakte Features (9D), keine Rohdaten; keine personenbezogenen Inhalte in blob.
	•	ASR-Text fließt über Empathie-Reflex ein; sensible Inhalte werden nicht dauerhaft im Klartext gespeichert (nur abgeleitete Scores/Intents).
	•	Alle Funktionen per ENV deaktivierbar.

⸻

12) Performance-Budget
	•	Audio-Token-Hook: << 2 ms/Token (Feature-Extraktion abhängig vom Wrapper)
	•	Persistenz (LTM): O(1) Insert/Upsert, quality_history append-only
	•	AgentLoop: stabil bei dt=0.25; Kurz-ASR optional ausschalten

⸻

13) Roadmap
	•	v3.7.3: Mini-Eval-Harness (Dedupe-Rate, Export-Yield, Cross-Modal Recall) + Charts im /learning.
	•	v3.8: Kontrastives Video/Audio-SSL (offline), hierarchische Policies (Options), World-Model-Mini für Games.

⸻

14) Diff v3.7.1 → v3.7.2

Neu:
	•	core/hooks_audio_snaptoken.py (Audio-SnapToken, 9D, Dedupe-aware)

Geändert:
	•	ui/asr2_ui.py (Reflex-Hook aktiviert)
	•	core/agent_loop.py (Audio-Hook-Adapter, ENV-Registrierung)

Unverändert, aber genutzt:
	•	core/langzeitgedaechtnis.py, core/sql_manager.py, core/asr_reflex.py, ui/learning.py

⸻

15) Kompatibilität
	•	Keine Migration erforderlich; ensure_schema bleibt idempotent.
	•	Origin/Namespace eindeutig: "audio/token" / "audio".
	•	Model-Registry UI: /models (optional Alias /registry).
	•	Headless-fähig, ohne Qt/Wayland/X11.
	
Hier ist die Bench-Notiz zum Einfrieren des aktuellen TTT-Stands. Lege sie unter diesem Pfad ab:

/opt/ai/oroma/docs/benchmarks/ttt_v3.7.2.md

# ORÓMA – Benchmark-Notiz Tic-Tac-Toe (TTT) · v3.7.2
# =============================================================================
# Pfad:    /opt/ai/oroma/docs/benchmarks/ttt_v3.7.2.md
# Projekt: ORÓMA
# Version: v3.7.2 (Audio/ASR2 + AgentLoop-Integration; TTT eval „side-aware“)
# Stand:   2025-11-05
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Reproduzierbarer Messpunkt für Tic-Tac-Toe nach Fix von Replay/Policy-Kette
# und „side-aware“ Self-Play: bestätigt, dass beide Seiten konsistent dieselbe
# Regel/Policy nutzen → Erwartung bei optimalem Spiel: Remis ≈ 100 %.
#
# TL;DR
# ─────
# • Self-Play (side-aware) 500 Spiele → 100.0 % Remis, 0 illegal, 0 Fehler
# • Training: 354 950 Schritte (aus ~131 932 Chains, inkl. compressed)
# • Export:   1 995 Archiv-Regeln laut Policy-Engine-Log
# • DB-Zählung: policy_rules im Namespace "game:tictactoe" vorhanden (siehe SQL unten)
#
# WICHTIG
# ───────
# Dieses Dokument friert den funktionierenden Zustand von v3.7.2 ein
# (Kanonraum + Legalitätscheck + Policy/Archiv Export + Evaluator „side-aware“).
# =============================================================================

## 1) System-/Laufzeitumgebung

- Hardware: Raspberry Pi 5 (16 GB), headless
- OS: Raspberry Pi OS (64-bit), Python 3
- ORÓMA: v3.7.2 Linie (AgentLoop, Replay/Policy gefixt; Evaluator „side-aware“)
- Kamera/DeviceHub aktiv, aber für TTT-Tests nicht relevant
- Token-Schutz: OROMA_UI_TOKEN **leer** (Replay-API offen auf `127.0.0.1`)

Empfohlene ENV (Auszug):
```bash
export OROMA_LOG_LEVEL=INFO
export OROMA_AGENT_ENABLED=true
export OROMA_AGENT_DT=0.25
# für TTT nicht erforderlich: AV/Audio-Hooks können aus bleiben

2) Trainings- und Export-Kommandos (reproduzierbar)

Hinweis: PYTHONPATH=/opt/ai/oroma setzen, wenn nicht global konfiguriert.

Policy-Training aus DB (inkl. compressed):

cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
  --train-db \
  --namespace game:tictactoe \
  --include-compressed \
  --limit 150000 \
  --verbose

Beispiel-Output (relevant):

[sql_manager] ensure_schema() OK
schema() OK
[ERROR] ingest_chain: ... database is locked   # sporadisch → unkritisch, wird übersprungen
...
[INFO] [policy_engine] trainierte Schritte: 354950 (Chains: 131932, Filter: game:tictactoe +compressed)

Export der Policy ins Archiv/Regel-Form:

PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
  --export-archiv \
  --namespace game:tictactoe \
  --min-n 3 \
  --min-abs-q 0.15 \
  --verbose

Beispiel-Output:

[INFO] [policy_engine] exportierte Archiv-Regeln: 1995

DB-Zählungen (Kontrolle):

sqlite3 /opt/ai/oroma/data/oroma.db "
SELECT 'policy_rules_ttt', COUNT(*) FROM policy_rules WHERE namespace='game:tictactoe';
SELECT 'rules_active_ttt', COUNT(*) FROM rules WHERE active=1 AND instr(lower(content),'game:tictactoe')>0;
"

Erwartung:
	•	policy_rules_ttt → > 1 500 (Beispiel: zuvor ~2 255, abhängig von Run)
	•	rules_active_ttt → kann 0 sein, wenn Export nicht in rules.content taggt
(Policy-Engine arbeitet primär über policy_rules; Archiv-Format variabel).

3) Evaluierung – Self-Play „side-aware“

Warum „side-aware“?
Beide Seiten (X und O) nutzen dieselbe Entscheidungslogik (Regel/Policy im Kanonraum),
und die Seitenvorteile werden neutralisiert. Erwartung bei guter Abdeckung: Remis ≥ 90 %, ideal 100 %.

Ergebnis (Messpunkt):

== TTT Self-Play (side-aware) ==
Games: 500
Draws: 500  (100.0%)
X wins: 0   O wins: 0
Illegal: 0

Akzeptanzkriterien für „grün“:
	•	Draw-Rate ≥ 90 %
	•	Illegale Züge = 0
	•	Fehler/Exceptions = 0

4) Reproduktion – minimaler Evaluator (Beispiel)

Falls kein fertiges CLI existiert, kann dieser Snippet ad-hoc laufen.
(setzt core.decision_engine.TTTDecision voraus)

python3 - <<'PY'
import random, sys
sys.path.insert(0, "/opt/ai/oroma")
from core.decision_engine import TTTDecision  # erwartet deine v3.7.1+ DecisionEngine

def play_one():
    decX, decO = TTTDecision(), TTTDecision()
    board = [""]*9
    player = "X"
    for _ in range(9):
        dec = decX if player=="X" else decO
        move = dec.choose_action_from_board(board)  # muss legale Aktion liefern
        if move is None or board[int(move)]:
            return "illegal"
        board[int(move)] = player
        # Gewinnprüfung
        WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
        for a,b,c in WINS:
            if board[a] and board[a]==board[b]==board[c]:
                return player
        player = "O" if player=="X" else "X"
    return "draw"

N=500
d=x=o=ill=0
for _ in range(N):
    r = play_one()
    if   r=="draw": d+=1
    elif r=="X":    x+=1
    elif r=="O":    o+=1
    else:           ill+=1
print(f"Games: {N}")
print(f"Draws: {d} ({d/N*100:.1f}%)")
print(f"X wins: {x}   O wins: {o}")
print(f"Illegal: {ill}")
PY

Erwartung mit heutigem Stand: Draws ≈ 100 %, Illegal=0.

5) Backups & Artefakte

DB-Snapshot einfrieren:

mkdir -p /opt/ai/oroma/exports
sqlite3 /opt/ai/oroma/data/oroma.db ".backup '/opt/ai/oroma/exports/oroma-ttt-$(date +%F).db'"

Replay-Events/Logs ansehen (optional):

curl -s http://127.0.0.1:8080/replay/api/logs | jq .

Policy-Export-Artefakte
Je nach Implementierung liegen Regeln primär in policy_rules. „Archiv-Regeln“ können
zusätzlich generiert werden; die semantische Nutzung in der DecisionEngine bleibt jedoch
policy-getrieben (Kanonraum).

6) Troubleshooting (kurz)
	•	Draws < 90 %
– Erneut --train-db mit höherem --limit laufen lassen.
– --include-compressed aktiv?
– Kanonisierung/Legalitätscheck im Adapter prüfen.
	•	Illegale Züge > 0
– adapter.legal_actions und map_action_through_perm gegenprüfen.
– Regeln/Policy nur legale Aktionen zulassen.
	•	„database is locked“ im Training
– Harmlos, wenn sporadisch (Retry/Skip).
– Andernfalls andere Prozesse kurz stoppen: DreamWorker/AgentLoop, erneut starten.
	•	Archiv-Zählung = 0
– Das ist ok, wenn die Decision über policy_rules läuft. Export-Modus prüfen
oder SQL anpassen (Namespace-Filter in content weicht je nach Exportformat).

7) KPI-Ziele & Regression-Guards
	•	Ziel: Draw-Rate ≥ 95 % (intern ≥ 90 % als „gelb“), Illegal=0
	•	Guard: Nightly Self-Play 200 Spiele, Abbruch wenn Draw-Rate < 90 % oder Illegal > 0
	•	Monitoring: policy_rules-Anzahl ≥ 1 500, Training-Schritte ≥ 300 000

8) Diff/Changelog (TTT-relevant)
	•	Fix: „side-aware“ Evaluator (beide Seiten gleiche Engine/Policy)
	•	Fix: Replay/Policy-Kette wirksam (Train→Export→Decision)
	•	Kanonraum + Legalitätscheck aktiv
	•	Ergebnis: 500/500 Remis, 0 illegal, 0 Fehler

9) Nächste Schritte
	•	Benchmark gegen Heuristik-Gegner (Corner-Bias) und gegen Minimax
	•	Nightly-Job: train-db + export-archiv + Self-Play Report → /logs/bench_ttt.log
	•	Kurzer UI-Tab „TTT Bench“ mit KPI-Badge (Draw-Rate der letzten 200 Spiele)

Wenn du magst, ergänze ich dir noch eine kleine **systemd-Unit + Timer** für den Nightly-Run (Training + Export + Self-Play + Report), dann ist der Zustand dauerhaft regressionssicher.

Hier ist die Bench-Notiz zum Einfrieren des aktuellen TTT-Stands. Lege sie unter diesem Pfad ab:

/opt/ai/oroma/docs/benchmarks/ttt_v3.7.2.md

# ORÓMA – Benchmark-Notiz Tic-Tac-Toe (TTT) · v3.7.2
# =============================================================================
# Pfad:    /opt/ai/oroma/docs/benchmarks/ttt_v3.7.2.md
# Projekt: ORÓMA
# Version: v3.7.2 (Audio/ASR2 + AgentLoop-Integration; TTT eval „side-aware“)
# Stand:   2025-11-05
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
# Reproduzierbarer Messpunkt für Tic-Tac-Toe nach Fix von Replay/Policy-Kette
# und „side-aware“ Self-Play: bestätigt, dass beide Seiten konsistent dieselbe
# Regel/Policy nutzen → Erwartung bei optimalem Spiel: Remis ≈ 100 %.
#
# TL;DR
# ─────
# • Self-Play (side-aware) 500 Spiele → 100.0 % Remis, 0 illegal, 0 Fehler
# • Training: 354 950 Schritte (aus ~131 932 Chains, inkl. compressed)
# • Export:   1 995 Archiv-Regeln laut Policy-Engine-Log
# • DB-Zählung: policy_rules im Namespace "game:tictactoe" vorhanden (siehe SQL unten)
#
# WICHTIG
# ───────
# Dieses Dokument friert den funktionierenden Zustand von v3.7.2 ein
# (Kanonraum + Legalitätscheck + Policy/Archiv Export + Evaluator „side-aware“).
# =============================================================================

## 1) System-/Laufzeitumgebung

- Hardware: Raspberry Pi 5 (16 GB), headless
- OS: Raspberry Pi OS (64-bit), Python 3
- ORÓMA: v3.7.2 Linie (AgentLoop, Replay/Policy gefixt; Evaluator „side-aware“)
- Kamera/DeviceHub aktiv, aber für TTT-Tests nicht relevant
- Token-Schutz: OROMA_UI_TOKEN **leer** (Replay-API offen auf `127.0.0.1`)

Empfohlene ENV (Auszug):
```bash
export OROMA_LOG_LEVEL=INFO
export OROMA_AGENT_ENABLED=true
export OROMA_AGENT_DT=0.25
# für TTT nicht erforderlich: AV/Audio-Hooks können aus bleiben

2) Trainings- und Export-Kommandos (reproduzierbar)

Hinweis: PYTHONPATH=/opt/ai/oroma setzen, wenn nicht global konfiguriert.

Policy-Training aus DB (inkl. compressed):

cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
  --train-db \
  --namespace game:tictactoe \
  --include-compressed \
  --limit 150000 \
  --verbose

Beispiel-Output (relevant):

[sql_manager] ensure_schema() OK
schema() OK
[ERROR] ingest_chain: ... database is locked   # sporadisch → unkritisch, wird übersprungen
...
[INFO] [policy_engine] trainierte Schritte: 354950 (Chains: 131932, Filter: game:tictactoe +compressed)

Export der Policy ins Archiv/Regel-Form:

PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
  --export-archiv \
  --namespace game:tictactoe \
  --min-n 3 \
  --min-abs-q 0.15 \
  --verbose

Beispiel-Output:

[INFO] [policy_engine] exportierte Archiv-Regeln: 1995

DB-Zählungen (Kontrolle):

sqlite3 /opt/ai/oroma/data/oroma.db "
SELECT 'policy_rules_ttt', COUNT(*) FROM policy_rules WHERE namespace='game:tictactoe';
SELECT 'rules_active_ttt', COUNT(*) FROM rules WHERE active=1 AND instr(lower(content),'game:tictactoe')>0;
"

Erwartung:
	•	policy_rules_ttt → > 1 500 (Beispiel: zuvor ~2 255, abhängig von Run)
	•	rules_active_ttt → kann 0 sein, wenn Export nicht in rules.content taggt
(Policy-Engine arbeitet primär über policy_rules; Archiv-Format variabel).

3) Evaluierung – Self-Play „side-aware“

Warum „side-aware“?
Beide Seiten (X und O) nutzen dieselbe Entscheidungslogik (Regel/Policy im Kanonraum),
und die Seitenvorteile werden neutralisiert. Erwartung bei guter Abdeckung: Remis ≥ 90 %, ideal 100 %.

Ergebnis (Messpunkt):

== TTT Self-Play (side-aware) ==
Games: 500
Draws: 500  (100.0%)
X wins: 0   O wins: 0
Illegal: 0

Akzeptanzkriterien für „grün“:
	•	Draw-Rate ≥ 90 %
	•	Illegale Züge = 0
	•	Fehler/Exceptions = 0

4) Reproduktion – minimaler Evaluator (Beispiel)

Falls kein fertiges CLI existiert, kann dieser Snippet ad-hoc laufen.
(setzt core.decision_engine.TTTDecision voraus)

python3 - <<'PY'
import random, sys
sys.path.insert(0, "/opt/ai/oroma")
from core.decision_engine import TTTDecision  # erwartet deine v3.7.1+ DecisionEngine

def play_one():
    decX, decO = TTTDecision(), TTTDecision()
    board = [""]*9
    player = "X"
    for _ in range(9):
        dec = decX if player=="X" else decO
        move = dec.choose_action_from_board(board)  # muss legale Aktion liefern
        if move is None or board[int(move)]:
            return "illegal"
        board[int(move)] = player
        # Gewinnprüfung
        WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
        for a,b,c in WINS:
            if board[a] and board[a]==board[b]==board[c]:
                return player
        player = "O" if player=="X" else "X"
    return "draw"

N=500
d=x=o=ill=0
for _ in range(N):
    r = play_one()
    if   r=="draw": d+=1
    elif r=="X":    x+=1
    elif r=="O":    o+=1
    else:           ill+=1
print(f"Games: {N}")
print(f"Draws: {d} ({d/N*100:.1f}%)")
print(f"X wins: {x}   O wins: {o}")
print(f"Illegal: {ill}")
PY

Erwartung mit heutigem Stand: Draws ≈ 100 %, Illegal=0.

5) Backups & Artefakte

DB-Snapshot einfrieren:

mkdir -p /opt/ai/oroma/exports
sqlite3 /opt/ai/oroma/data/oroma.db ".backup '/opt/ai/oroma/exports/oroma-ttt-$(date +%F).db'"

Replay-Events/Logs ansehen (optional):

curl -s http://127.0.0.1:8080/replay/api/logs | jq .

Policy-Export-Artefakte
Je nach Implementierung liegen Regeln primär in policy_rules. „Archiv-Regeln“ können
zusätzlich generiert werden; die semantische Nutzung in der DecisionEngine bleibt jedoch
policy-getrieben (Kanonraum).

6) Troubleshooting (kurz)
	•	Draws < 90 %
– Erneut --train-db mit höherem --limit laufen lassen.
– --include-compressed aktiv?
– Kanonisierung/Legalitätscheck im Adapter prüfen.
	•	Illegale Züge > 0
– adapter.legal_actions und map_action_through_perm gegenprüfen.
– Regeln/Policy nur legale Aktionen zulassen.
	•	„database is locked“ im Training
– Harmlos, wenn sporadisch (Retry/Skip).
– Andernfalls andere Prozesse kurz stoppen: DreamWorker/AgentLoop, erneut starten.
	•	Archiv-Zählung = 0
– Das ist ok, wenn die Decision über policy_rules läuft. Export-Modus prüfen
oder SQL anpassen (Namespace-Filter in content weicht je nach Exportformat).

7) KPI-Ziele & Regression-Guards
	•	Ziel: Draw-Rate ≥ 95 % (intern ≥ 90 % als „gelb“), Illegal=0
	•	Guard: Nightly Self-Play 200 Spiele, Abbruch wenn Draw-Rate < 90 % oder Illegal > 0
	•	Monitoring: policy_rules-Anzahl ≥ 1 500, Training-Schritte ≥ 300 000

8) Diff/Changelog (TTT-relevant)
	•	Fix: „side-aware“ Evaluator (beide Seiten gleiche Engine/Policy)
	•	Fix: Replay/Policy-Kette wirksam (Train→Export→Decision)
	•	Kanonraum + Legalitätscheck aktiv
	•	Ergebnis: 500/500 Remis, 0 illegal, 0 Fehler

9) Nächste Schritte
	•	Benchmark gegen Heuristik-Gegner (Corner-Bias) und gegen Minimax
	•	Nightly-Job: train-db + export-archiv + Self-Play Report → /logs/bench_ttt.log
	•	Kurzer UI-Tab „TTT Bench“ mit KPI-Badge (Draw-Rate der letzten 200 Spiele)

Wenn du magst, ergänze ich dir noch eine kleine **systemd-Unit + Timer** für den Nightly-Run (Training + Export + Self-Play + Report), dann ist der Zustand dauerhaft regressionssicher.

<a id="docs_konzeption_architektur_v3_7_3_md"></a>

## Quelle: `docs/konzeption_architektur_v3_7_3.md`

ORÓMA – Konzeption & Architektur v3.7.3

Pfad: docs/konzeption_architektur_v3_7_3.md
Projekt: ORÓMA
Version: v3.7.3 (Replay-System + Health-Dashboard + AgentLoop-EventBus + Circadian-Phasefile)
Stand: 2025-11-05

⸻

1) Ziel & Kontext

v3.7.3 festigt die v3.7-Linie (Empathie/Self-Listening/Thread/Curriculum) um robuste Operabilität & Diagnose:
	•	Replay-System (UI+API): SnapChains gezielt abspielen (Start/Pause/Resume/Stop), Live-Status & Logs.
	•	AgentLoop r5 (Event-Bus): inject_event() + Default-Listener → roter_faden.attach() & leichter Event-Trace (DB).
	•	Health-Dashboard: /health mit Live-Charts (CPU/RAM/GPU), Log-Viewer, Update-Check/Run, Selftest.
	•	Circadian-Controller: DeviceHub-Lichtquelle (camera) + Phase-Datei als UI-Fallback; Instance-Bridge fix.

Erwartung: Besseres Operieren/Debuggen, konsistente Telemetrie und reproduzierbare Lernläufe (Replay).

⸻

2) Neue & geänderte Komponenten (v3.7.3)

Neu
	•	ui/replay_api.py – HTTP-API: /replay/api/* (status, start, pause, resume, stop, logs, chains, healthz).
	•	ui/templates/replay.html – minimalistische Replay-Steuerung mit Live-Status (Token optional).
	•	ui/health_ui.py + templates/health.html – neues Health-Dashboard mit Chart.js.

Geändert
	•	core/agent_loop.py (r5) – Event-Bus (inject_event, register_event_listener), Default-Listener → roter_faden + Event-Trace (ENV-gesteuert), optionale Hooks (Audio/AV/Vision) via ENV.
	•	run_oroma.py – safe_register(), Circadian-Phasefile, DeviceHub-Luma-Sampler, Admin-BP Fix, Signale/Shutdown.
	•	ui/flask_ui.py / Blueprints – Registrierung vereinheitlicht (inkl. Kompat-/Health-Routen).

Optional/Bestandsmodule (weiterhin)
	•	core/replay_manager.py/replay_system.py – Producer; speist Events in agent_loop.inject_event.
	•	core/sql_manager.py – idempotentes Schema inkl. replay_log.

⸻

3) Datenmodell / Schema

Unverändert (idempotent via python3 -m core.sql_manager --ensure) + genutzt:
	•	snapchains(id, ts, origin, namespace, status, weight, quality, blob, notes, version, exported)
NEU genutzt: leichter Event-Trace (origin="event/replay", namespace="replay", notes="replay_event", weight≈0.1).
	•	replay_log(id, chain_id, ts_run, steps, speed, status, info) – Logbuch für Replay-Runs (Start/Step/End).
	•	metrics(name, value, ts) – Telemetrie (z. B. agent_heartbeat, agent_event_injected, replay_event).

Semantik
	•	Event-Trace ist leicht (JSON-Blob; keine Rohmedien), standardmäßig aktivierbar per ENV.
	•	replay_log wird genutzt, wenn OROMA_REPLAY_LOGGER=1 gesetzt ist (Duplikatschutz + Start/Step/End).

⸻

4) Laufzeit-Architektur (vereinfacht)

UI (replay.html) ─┐
                  │   /replay/api/start|pause|resume|stop|status|logs|chains
Flask (replay_api)┼──────────────→ core.replay_manager ──► agent_loop.inject_event(ev)
                  │                                           │
                  │                                           ├─ Default-Listener: roter_faden.attach(...)
                  │                                           └─ Event-Trace → snapchains(origin="event/replay")
                  │
Health UI ────────┼──────────────→ /health/api/* (Status, Logs, History, Updates)
                  │
run_oroma ────────┴→ AgentLoop + DeviceHub (Licht) + Circadian + Phase-Datei (UI-Fallback)

⸻

5) Endpoints & Hooks

Replay-API (JSON)
	•	GET  /replay/api/healthz → {ok:true, ts}
	•	GET  /replay/api/status → {ok:true, status:{running, paused, chain_id, step, total_steps, ...}}
	•	POST /replay/api/start  → Body: {chain_id, speed} → {ok:true, started:true, ...}
	•	POST /replay/api/pause|resume|stop
	•	GET  /replay/api/logs?limit=50 → replay_log (neueste Runs)
	•	GET  /replay/api/chains[?q=...] → Übersicht Chains (DB/FS), Token optional (siehe Sicherheit)

Health-API
	•	GET /health/ (HTML)
	•	GET /health/api/health (Status), /health/api/health/logs?n=300 (Tail), /health/api/history
	•	GET /health/api/updates/check, POST /health/api/updates/run
	•	Kompat: /api/health bleibt per bp_compat verfügbar.

AgentLoop-Hooks (ENV-gesteuert)
	•	Audio-SnapToken: OROMA_AUDIO_SNAPS=1
	•	AV-SnapToken / Vision: OROMA_AV_SNAPS=1, OROMA_VISION_INFER=1
	•	Leichtgewichtig immer aktiv: nudge-/social-resonance-Hooks (roter_faden & Rewards).

⸻

6) Konfiguration (ENV – relevante Auszüge)

AgentLoop / Event-Trace

OROMA_AGENT_DT=0.25
OROMA_AGENT_LOGLEVEL=INFO
OROMA_AGENT_HEARTBEAT=1        # metrics(agent_heartbeat)
OROMA_EVENT_TRACE=1            # leichtes Event-Trace in snapchains
OROMA_EVENT_TRACE_ORIGIN=event/replay
OROMA_EVENT_TRACE_WEIGHT=0.1
OROMA_REPLAY_LOGGER=0          # 1=Start/Step/End in replay_log

Replay-UI/API

OROMA_UI_TOKEN=                # leer/undefiniert → kein Token nötig

Circadian / DeviceHub / Phase-Datei

OROMA_LIGHT_SOURCE=camera      # camera|dummy|off
OROMA_LIGHT_CAMERA_INTERVAL=300
OROMA_LIGHT_MIN=0
OROMA_LIGHT_MAX=100
OROMA_PHASE_PATH=/opt/ai/oroma/data/state/phase.json

Optionale Sensor-Hooks

OROMA_AUDIO_SNAPS=0|1
OROMA_AV_SNAPS=0|1
OROMA_VISION_INFER=0|1

⸻

7) Rollout-Checkliste
	1.	Dateien deployen: ui/replay_api.py, templates/replay.html, ui/health_ui.py, templates/health.html, aktualisierte core/agent_loop.py, run_oroma.py.
	2.	Schema sicherstellen

python3 -m core.sql_manager --ensure

	3.	ENV prüfen (Token leer lassen, wenn nicht gewünscht; Event-Trace/Logger je Bedarf).
	4.	Service neu starten

sudo systemctl restart oroma


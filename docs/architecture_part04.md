<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__architecture.md
  Part:   4
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

	5.	Sanity-Checks
	•	curl -s /replay/api/healthz → {ok:true}
	•	curl -s /health/api/health → Status JSON

⸻

8) Smoke-Tests

Replay Kurzlauf (selftest)

curl -s -X POST http://127.0.0.1:8080/replay/api/start \
  -H 'Content-Type: application/json' \
  -d '{"chain_id":"selftest","speed":0.2}' | jq .
watch -n 0.5 "curl -s /replay/api/status | jq .status"
curl -s -X POST /replay/api/pause  | jq .
curl -s -X POST /replay/api/resume | jq .
curl -s -X POST /replay/api/stop   | jq .
curl -s /replay/api/logs | jq .

Health-Dashboard
	•	Browser: /health/ → Live-Charts & Logs sollten sich aktualisieren.
	•	GET /health/api/history?window_sec=600 zeigt frische Punkte.

Circadian / Phase-Datei
	•	Prüfen: cat $OROMA_PHASE_PATH  (z. B. {"phase":"DAY","ts":...,"threshold":...})

⸻

9) Monitoring & KPIs

Kurzfristig
	•	Replay-KPIs: replay_log.status ∈ {run,done,error}, durchschnittliche Dauer, Abbruchrate.
	•	Event-KPIs: metrics(agent_event_injected), metrics(replay_event) Zähler steigen erwartungsgemäß.

Mittel
	•	Heartbeat-Stabilität: konstante Rate agent_heartbeat (≈ 1/dt).
	•	Health-Charts: CPU/RAM ≤ Budget; GPU-Temp stabil (Pi).

Langfristig
	•	Event-Trace-Nutzung: repräsentative Replay-Korridore als leichte SnapChains (kein Rohvideo!).
	•	Fehlertrend: sinkende Replay-Fehler / robustere Reproduzierbarkeit.

⸻

10) Troubleshooting
	•	invalid or missing token
→ OROMA_UI_TOKEN leer in .env.systemd und .env (beide prüfen), dann Service neu starten.
	•	running=false trotz Start
→ selftest hat nur 1 Step → mit speed=0.2 starten oder sofort Pause klicken. Poll-Intervall im UI auf 1000 ms setzen.
	•	keine replay_log-Einträge
→ OROMA_REPLAY_LOGGER=1 setzen; sonst nur Event-Trace (snapchains).
	•	Health zeigt nichts/Fehler
→ psutil installiert? Pfade /opt/ai/oroma/logs/ vorhanden? Rechte prüfen.
	•	Circadian bleibt „unknown“
→ mitgelieferter Fix setzt CircadianController.INSTANCE nur, wenn Thread existiert; Service neu starten. Phase-Datei prüfen.

⸻

11) Sicherheit & Ethik
	•	Token optional: Standard ohne Token. Falls gesetzt, Header Authorization: Bearer <token> oder X-Api-Token.  <!-- TODO linkfix: token -> docs/module_snaptoken.md -->
	•	Datensparsam: Event-Trace speichert nur leichte JSON-Events (keine Rohbilder/-audios).
	•	Updates: /health/api/updates/run führt apt-Upgrade aus—nur mit Admin-Wissen verwenden.

⸻

12) Performance-Budget
	•	AgentLoop-Tick dt≈0.25 s stabil; Hooks best-effort (Fehler blockieren den Loop nicht).
	•	Event-Trace Insert: O(1) + kleiner JSON-Blob, vernachlässigbar.
	•	Health-Charts: Polling 10 s; Logs 15 s; History 10 s → unkritisch auf Pi 5.

⸻

13) Kompatibilität
	•	Schema: idempotent; keine Migration nötig.
	•	Legacy /api/health: via bp_compat weiterhin erreichbar.
	•	Header-Strings: Falls einzelne Dateien noch v3.8 im Kommentar tragen: nur kosmetisch – Funktion ist v3.7.3-konform.

⸻

14) Diff v3.7.2 → v3.7.3

Neu
	•	ui/replay_api.py, templates/replay.html
	•	Health-Dashboard: ui/health_ui.py, templates/health.html

Geändert
	•	core/agent_loop.py (Event-Bus + Default-Listener + ENV/Telemetry)
	•	run_oroma.py (Phase-File, DeviceHub-Luma, safe_register, Admin-BP Fix, Signals, Shutdown)

Unverändert, aber integriert
	•	core/replay_manager.py / replay_system.py
	•	core/sql_manager.py (stellt replay_log & metrics bereit)

⸻

15) Roadmap

v3.7.4
	•	Replay-Priorisierung (Queues) + Batch-Runs + Export von Replay-Statistiken ins Dashboard.
	•	Health-Charts: zusätzliche Metriken (Disk-IO, Agent-Tick-Jitter).

v3.8 (später)
	•	Kooperatives Lernen, Curriculum-Compiler, priorisiertes Replay, Robotik/Energie (dein 3.8-Aufriss bleibt Ausblick).

⸻

Kurzfazit: v3.7.3 macht dein System bedien- & diagnosetauglich: Replays steuerbar, Status sichtbar, Events sauber getraced—ohne schwere DB-Eingriffe. Genau der richtige Schritt, bevor wir in 3.8 die großen Autonomie-Features ziehen.

<a id="docs_konzeption_architektur_v3_8_md"></a>

## Quelle: `docs/konzeption_architektur_v3_8.md`

**Originaltitel:** Drives – Setpoints (0..1) & Gewichte

Hier ist die überarbeitete, produktionsreife v3.8-Markdown — sauber strukturiert, sprachlich konsistent zur v3.7-Doku und mit ein paar sinnvollen Ergänzungen (ENV, Rollout, Smoke-Tests, Safety). Du kannst sie 1:1 ablegen.

⸻

ORÓMA – Konzeption & Architektur v3.8

Pfad: docs/konzeption_architektur_v3_8.md
Projekt: ORÓMA
Version: v3.8 (Codename: Autonomes Leben)
Stand: 2025-09-29

⸻

1) Leitidee

ORÓMA v3.8 erweitert die Empathie- und Lernfähigkeiten (v3.7) um körperliche Autonomie:
	•	Einbindung realer Roboterfahrzeuge & Drohnen, die remote steuerbar sind und selbständig zur Dockingstation zurückkehren.
	•	Umsetzung eines biologischen Prinzips: Lernen & Forschen gekoppelt an Energie-Management (analog zu Hunger/Müdigkeit).

Zielbild: Ein System, das nicht nur kognitiv lernt (v3.7), sondern sein Verhalten auch energetisch organisiert: „Erkunden, lernen, heimkehren, laden, konsolidieren.“

⸻

2) Entwicklungsschritte (Kontext)
	•	v3.0 – Student/Gelehrter: Snap+Token-Fusion, RAG, Replay.
	•	v3.5 – Forscher/Meister: MetaSnaps, Mutation-Policy, Explainability 2.0.
	•	v3.6 – Wissenschaftler: Hypothesen-Generator, Experimente, Vergleichssystematik.
	•	v3.7 – Empathie-Simulation: Emotionale Marker als Lernverstärker (Frust, Motivation, Freude).
	•	v3.8 – Autonomes Leben:
	•	VehicleRemote-Wrapper (Cars, Drohnen, Roboter).
	•	Energie-Management: Akku-Status & Docking in Lernzyklen integriert.
	•	UI-Erweiterung: Robotik-Tab mit Battery-Status, Dock/Undock, Live-Sensorik.

⸻

3) Lernstrategie (v3.8)

Tagmodus
	•	Sensorik: Audio, Vision, Text, Motorik + Fahrzeugsensoren (Distanz, Kamera, Akku).
	•	Snaps: Vehicle-Daten werden wie PiCar-Daten gespeichert (kompatibel zu v3.7).
	•	Fusion: Snap+Token-Fusion auch auf Fahrstrategien anwendbar (z. B. „Korridor folgen“).

Traummodus (DreamWorker 3.0)
	•	Wenn am Dock: volle Optimierung & Cross-Modal-Training (inkl. Fahr-Episoden).
	•	Wenn nicht am Dock: nur leichte Tagverarbeitung (Energie sparen).
	•	Neu: Mutation/Selektion + Empathie-Snaps erweitert um Energie-Snaps (Müdigkeit/Aufladen).

⸻

4) Speicherstrategie (Erweiterung)

Daten	Dauerhaftigkeit	Zweck
SnapFeatures	dauerhaft	Muster aus Fahrzeug-/Sensorikdaten
SnapTokens	dauerhaft	symbolische Ebene
SnapChains	dauerhaft	Sequenzen inkl. Fahrstrategien
MetaSnaps	dauerhaft	abstrakte Fahr-/Dockingkonzepte
Episoden	dauerhaft	Fahrten, Docking-Events
Empathie-Snaps	dauerhaft	Frust/Motivation/Freude
Energie-Snaps	dauerhaft	Akku-Zustände, Dock/Undock

(Technisch: Energie-Snaps als neue Quelle in metrics/rewards_log + lightweight JSON in curriculum_state.window – kein schweres DB-Refactoring nötig.)

⸻

5) Architektur (v3.8, vereinfacht)

 +----------------------------+
 | Wrapper-System             |
 | (Audio, Vision, Vehicle)   |
 +-------------+--------------+
               |
   +-----------v-------------+
   | VehicleRemote API       |
   | (Vector, ESP32, Drone)  |
   +-------------+------------+
               |
   +-----------v-------------+
   | Snaps + Tokens + Chains |
   | Empathie + EnergieSnaps |
   +-------------+------------+
               |
   +-----------v-------------+
   | Langzeitgedächtnis      |
   | SQL + Vektor-DB + Epis. |
   +-------+-----------+------+
           |           |
   +-------v--+  +----v------+
   | Dream/Exp|  | Dashboard |
   +----------+  +-----------+

⸻

6) Simulationsergebnisse (Prognose)

30 Tage
	•	Fahrzeug fährt zuverlässig zur Dockingstation zurück.
	•	Energie-Snaps stabilisieren Lernzyklen (weniger Abbrüche).

90 Tage
	•	ORÓMA koppelt Fahrstrategien an Akkustand (z. B. „Heimweg früher beginnen“).
	•	„Frust-Snaps“ bei leerem Akku → verbesserte Docking-Routinen.

1 Jahr
	•	Kontinuierlicher Betrieb → ORÓMA wirkt wie ein „Haustier“ (Routine/Erkundung/Heimkehr).
	•	Explainability:
„Ich habe gelernt, rechtzeitig zum Dock zu fahren, sonst war es frustrierend, weil ich ausgegangen bin.“
	•	Forschungsautonomie: Experimente laufen ohne manuelles Eingreifen.

⸻

7) Vergleich v3.7 → v3.8

Kriterium	v3.7 (Empathie)	v3.8 (Autonomes Leben)
Empathie-Snaps	Frust, Freude, Motivation	+ Müdigkeit, Erleichterung (Akku)
Fahrzeuge	PiCar/Simuliert	Real-Remote (Vector, Drone, ESP32)
Docking	manuell	autonom (selbständiges Laden)
Explainability	kausal + emotional	kausal + emotional + energetisch
Autonomie	kognitiv	kognitiv + physisch

⸻

8) Verbesserungsansätze für v3.9+
	•	Multi-Vehicle-Koordination: mehrere Fahrzeuge kooperieren.
	•	Drohnen Auto-Landing: analog zum Docking.
	•	Feinere Energie-Simulation: Unterscheidung Langeweile (Leerlauf) vs. Überlastung.
	•	DreamWorker 4.0: generative Exploration neuer Strategien (SnapDiffusion).
	•	Cloud-Relay: mehrere ORÓMAs synchronisieren Erfahrungen (optional).

⸻

9) ENV & Schnittstellen (neu in v3.8)

VehicleRemote

OROMA_VEHICLE_ENABLED=true
OROMA_VEHICLE_KIND=vector|esp32|drone
OROMA_VEHICLE_ADDR=192.168.0.123
OROMA_VEHICLE_AUTH_TOKEN=...           # falls benötigt

Energie-Manager

OROMA_ENERGY_ENABLED=true
OROMA_ENERGY_DOCK_BATTERY_MIN=0.25     # unter 25% → Docking priorisieren
OROMA_ENERGY_RESERVE_FOR_HOME=0.15     # Reserve für Heimweg
OROMA_ENERGY_IDLE_WHEN_LOW=true        # Aktivität drosseln bei Low Battery

Docking-Policy

OROMA_DOCK_AUTORETURN=true
OROMA_DOCK_TIMEOUT_SEC=120

UI / Robotics
	•	Endpoints (Beispiele):
	•	GET /robotics/ – Dashboard
	•	POST /robotics/api/dock|undock|stop|go
	•	GET /robotics/api/status – Battery %, Pose, Dock-State

(Alle ENV sind optional; bei Fehlen greifen sichere Defaults.)

⸻

10) Rollout-Checkliste
	1.	Dateien deployen
	•	/opt/ai/oroma/v3.8/core/wrappers/vehicle_remote.py
	•	/opt/ai/oroma/v3.8/core/wrappers/adapters/vector_adapter.py
	•	/opt/ai/oroma/v3.8/core/energy_manager.py
	•	/opt/ai/oroma/v3.8/ui/routes/robotics_ui.py
	•	docs/konzeption_architektur_v3_8.md
	2.	DB-Schema verifizieren

python3 -m core.sql_manager --ensure

	3.	ENV ergänzen & Service neu starten

sudo systemctl restart oroma

	4.	AgentLoop-Hooks (optional)
	•	energy_manager als Hook registrieren, falls eigene Periodik gewünscht.
	•	VehicleRemote ggf. über Wrapper-Tick einhängen.

⸻

11) Smoke-Tests
	•	Connectivity: Robotics-UI öffnet, GET /robotics/api/status liefert Battery %, Dock-State.
	•	Docking: POST /robotics/api/dock → Fahrzeug fährt Heim; nach Timeout Dock-State = true.
	•	Energie-Policy: Battery simuliert < 25% → Docking-Policy triggert; Aktivität drosselt.
	•	Dream vs. Day: Am Dock → DreamWorker 3.0 läuft; sonst nur leichte Verarbeitung.
	•	Logging: metrics enthält Battery-Zeitreihe; rewards_log zeigt ggf. energy/docking-Events.

⸻

12) Sicherheit & Ethik (Robotik)
	•	Safety First: Not-Stop in UI + HW-Not-Aus, Soft-Limits (Tempo/Radius).
	•	Geofencing: Fahrzeugbereich begrenzen; Drohnen nur in Sichtweite/Legalraum.
	•	Datenschutz: Keine personenbeziehbaren Rohvideos speichern; nur abgeleitete Metriken/Events loggen.
	•	Transparenz: UI zeigt Energie-Zustand und Entscheidungsgründe (Explainability-Text).

⸻

13) Dateien (v3.8)
	•	/opt/ai/oroma/v3.8/core/wrappers/vehicle_remote.py
	•	/opt/ai/oroma/v3.8/core/wrappers/adapters/vector_adapter.py
	•	/opt/ai/oroma/v3.8/core/energy_manager.py
	•	/opt/ai/oroma/v3.8/ui/routes/robotics_ui.py
	•	docs/konzeption_architektur_v3_8.md

Super Hinweis — ich hab’s dir als ergänzenden Abschnitt fertig formuliert, den du direkt ans Ende deiner v3.8-MD hängen kannst. So bleibt deine bestehende Doku unverändert, und der „lebewesenhafte“ Schritt (Drives/Homöostase + Welt-/Vorhersagemodell) ist sauber dokumentiert.

⸻

14) Drives/Homöostase & Welt-/Vorhersagemodell (Add-on v3.8.x – optional, experimentell)

Motivation. Für den nächsten Schritt in Richtung „lebewesenhaft“ braucht ORÓMA zwei Bausteine:
	1.	Drives/Homöostase – interne Sollwerte (Setpoints) und Defizite (drive debts) für Energie, Neugier, soziale Resonanz, Lernfortschritt, Sicherheit.
	2.	Ein Welt-/Vorhersagemodell – das künftige Beobachtungen schätzt; Erwartungsfehler (prediction error) treiben Neugier/Handlungswahl.

Beides lässt sich modular ergänzen – ohne die bestehende Architektur umzubauen.

⸻

14.1 Komponenten

A) core/homeostasis.py
	•	Verwaltet Drives mit Setpoints & Gewichten:
	•	energy (Akku / Docking), curiosity (Neues entdecken),
	•	social (positive Empathie-Resonanz), progress (Curriculum-Fortschritt),
	•	safety (Konflikte/Verstöße minimal halten).
	•	Berechnet pro Tick Defizite: debt_i = max(0, setpoint_i − state_i) und
eine priorisierte Handlungsneigung: priority = Σ w_i * debt_i.
	•	Stellt eine Policy-Schnittstelle bereit: suggest_actions(state) -> List[Intent].

B) core/world_model.py
	•	Leichtgewichtiges Forward Model (z. B. kleiner MLP/Kalman), trainiert auf Zeitreihen aus
metrics, episodic und relevanten Wrapper-States (Vision/Vehicle/ASR).
	•	API:
	•	predict(next_obs | obs_t, action_t)
	•	update(obs_t+1, pred_t+1) → liefert Prediction Error (PE).
	•	Optional: Neugier-Reward proportional zum PE (gedeckelt), um Exploration zu lenken.

C) Integration in bestehende Loops (ohne Umbau)
	•	AgentLoop-Hook homeostasis_hook(dt, tick):
	•	liest aktuelle States (Akku %, empathy_mean, curriculum_progress, safety_flags)
	•	holt PE vom Weltmodell → schreibt metrics/rewards_log(curiosity)
	•	wählt sanfte Intents (z. B. „dock“, „repeat“, „explore_safely“) per suggest_actions()
	•	DreamWorker 3.0: nutzt gespeicherte Episoden zum Offline-Training des Weltmodells.

⸻

14.2 ENV (Vorschlag, sichere Defaults)

# Drives – Setpoints (0..1) & Gewichte
OROMA_DRIVE_ENERGY_SET=0.60
OROMA_DRIVE_ENERGY_W=1.0
OROMA_DRIVE_CURIOSITY_SET=0.30
OROMA_DRIVE_CURIOSITY_W=0.6
OROMA_DRIVE_SOCIAL_SET=0.40
OROMA_DRIVE_SOCIAL_W=0.5
OROMA_DRIVE_PROGRESS_SET=0.50
OROMA_DRIVE_PROGRESS_W=0.7
OROMA_DRIVE_SAFETY_SET=0.95
OROMA_DRIVE_SAFETY_W=1.2

# Welt-/Vorhersagemodell
OROMA_WM_ENABLED=true
OROMA_WM_KIND=mlp        # mlp|kalman|linear
OROMA_WM_UPDATE_RATE=0.5 # wie oft online nachtrainieren (Hz)
OROMA_WM_MAX_PE=0.2      # Cap für curiosity reward

# Policy-Guardrails
OROMA_POLICY_MAX_ACTIONS_PER_MIN=6
OROMA_POLICY_SAFETY_HARDSTOP=true

⸻

14.3 Datenschnittstellen (leichtgewichtig)
	•	Input-States:
	•	energy: Battery %, Dock-State (aus Vehicle/Robotics UI)
	•	social: rolling mean der empathy_snaps.score
	•	progress: EMA von reward("curriculum") / solved tasks
	•	curiosity: EMA reward("curiosity") bzw. PE
	•	safety: invertierte Rate von Fehlern/Violations
	•	Outputs:
	•	intent (symbolisch): dock, explore, repeat, rest, status_speak …
	•	reward logs: curiosity (aus PE gedeckelt), optionale policy-Events (Audit)

⸻

14.4 Beispiel-Heuristik (Pseudocode)

debt[i]   = max(0, set[i] - state[i])
priority  = sum_i (w[i] * debt[i])    # zur Auswahl handlungsrelevanter Intents

if energy_debt hoch → intent = dock
elif safety_debt hoch → intent = rest/slowdown
elif progress_debt hoch → intent = repeat (Curriculum)
elif curiosity_debt moderat & safety ok → intent = explore_safely
elif social_debt hoch → intent = status_speak (MangelSpeak/Empathie)

PE = ||obs_pred - obs_next|| → reward.curiosity = min(PE, WM_MAX_PE)

⸻

14.5 Rollout (optional/experimentell)
	1.	Dateien hinzufügen
	•	/opt/ai/oroma/v3.8/core/homeostasis.py
	•	/opt/ai/oroma/v3.8/core/world_model.py
	•	AgentLoop: register_hook(homeostasis.homeostasis_hook)
	2.	Schema bleibt (nur metrics/rewards_log intensiver genutzt).
	3.	ENV setzen, neu starten:

sudo systemctl restart oroma


⸻

14.6 Smoke-Tests
	•	PE sichtbar: während Exploration steigt curiosity (Reward) moderat an, gedeckelt.
	•	Energie-Drive: Battery < Setpoint → dock-Intents priorisiert.
	•	Safety-Guard: bei Violations werden aktive Intents gedrosselt/abgebrochen.
	•	Progress-Drive: ausbleibende Curriculum-Rewards → repeat steigt.
	•	Keine Spam-Intents: Rate-Limit & Hardstop greifen.

⸻

14.7 KPIs
	•	Unterdrückte Abbrüche: weniger confidence drops in Lernkurven.
	•	Bessere Energie-Nutzung: reduzierte „leer gefahren“-Events.
	•	Stetiger Fortschritt: höhere EMA von reward("curriculum").
	•	Exploration mit Maß: curiosity steigt ohne Safety-Incidents.

⸻

14.8 Sicherheit
	•	Guardrails zuerst: Safety-Drive hat höchste Priorität; Hardstop bleibt aktiv.
	•	Auditierbarkeit: alle Intents werden mit Drive-Kontext in raw/tag geloggt.
	•	Datensparsam: Weltmodell nutzt abgeleitete Metriken, keine Rohvideos.

⸻

Kurzfassung:
Drives/Homöostase geben deinem System interne „Bedürfnisse“.
Das Welt-/Vorhersagemodell liefert Erwartungsfehler als Lern- und Explorationssignal.
Zusammen entsteht ein Verhalten, das zielgerichtet und situationssensibel wirkt – ohne deine v3.7/3.8-Architektur zu verbiegen.

<a id="docs_konzeption_architektur_v3_9_md"></a>

## Quelle: `docs/konzeption_architektur_v3_9.md`

📑 ORÓMA v3.9 – Persona & Rollenflexibilität

Codename: Rollenspieler+

⸻

1. Leitidee

ORÓMA erweitert in v3.9 die Mensch-KI-Interaktion durch Personas – vordefinierte Rollen mit eigenem Stil.
Neu: erweiterte Persona-Funktionen sind bereits eingebaut, können aber per UI oder Konfiguration zugeschaltet werden:
	•	Adaptive Personas (passen sich an den Menschen an).
	•	Multi-Persona-Dialoge (mehrere Rollen gleichzeitig).
	•	Export/Import von Persona-Definitionen.

⸻

2. Persona-Basis (Standard)
	•	Feste Personas auswählbar (z. B. Lehrer, Forscher, Spielkamerad, Analytiker).
	•	Definiert durch JSON/YAML-Profile (/data/personas/).
	•	Aktivierbar über Dashboard-Tab „Personas“.

Beispiel:

{
  "name": "Forscher",
  "explainability_mode": "kausal+narrativ",
  "empathy_weights": {"frust": 0.8, "freude": 0.6},
  "interaction_style": "fragend+explorativ"
}

⸻

3. Erweiterte Persona-Funktionen (optional aktivierbar)

a) Adaptive Personas
	•	ORÓMA analysiert Interaktionsmuster (z. B. Nutzer reagiert genervt auf lange Erklärungen).
	•	Passen Persona-Parameter dynamisch an (mehr Geduld, weniger Rückfragen).
	•	⚙️ In der UI: Schalter „Adaptive Persona ON/OFF“.

b) Multi-Persona-Modus
	•	Mehrere Personas gleichzeitig aktiv (z. B. Lehrer + Forscher im Dialog).
	•	ORÓMA wechselt zwischen ihnen oder simuliert „Diskussion“.
	•	⚙️ UI: Auswahl von 2+ Personas → ORÓMA spielt beide.

c) Persona-Transfer (Export/Import)
	•	Personas als JSON-Dateien exportieren/importieren.
	•	Austauschbar zwischen verschiedenen ORÓMA-Instanzen.

⸻

4. Architektur (v3.9 erweitert)

+-------------------------------+
| Persona Manager               |
| - feste Personas              |
| - adaptive Persona Engine (*) |
| - multi-Persona Orchestrator(*)|
+-------------------------------+
            |
    +-------v-------+
    | SnapChains +  |
    | EmpathieSnaps |
    +---------------+

(*) = optional per Schalter aktivierbar.

⸻

5. Simulation (Prognose v3.9)
	•	30 Tage: Nutzer kann zwischen Personas wechseln, Unterschiede sind klar erkennbar.
	•	90 Tage: Mit adaptivem Modus passen sich Rollen automatisch an Nutzertyp an.
	•	1 Jahr: Multi-Persona-Dialoge ermöglichen komplexere Experimente (z. B. Forscher- und Lehrer-Persona diskutieren, ORÓMA zieht MetaSnaps daraus).

⸻

6. Vorteile
	•	Direkt spannend für Nutzer (kein monotones KI-Gespräch).
	•	Flexibel: von festen Rollen bis zu adaptiven, dynamischen.
	•	Übertragbar: Personas können geteilt oder getauscht werden (wie kleine „Module“).

⸻

7. Projektdateien (v3.9)
	•	/core/persona_manager.py – Basis + optionale Engines.
	•	/ui/routes/persona_ui.py – Auswahl, Schalter für adaptive/multi.
	•	/data/personas/ – JSON/YAML-Profile.
	•	docs/konzeption_architektur_v3_9.md – Dokumentation.

⸻

✅ Damit hast du in v3.9 alles drin, was 3.10 vorgesehen hätte – nur mit dem Unterschied, dass die komplexeren Features optional zuschaltbar sind.

<a id="docs_oroma_v3_5_vergleich_markt_ki_md"></a>

## Quelle: `docs/oroma_v3_5_vergleich_markt_ki.md`

🤖 Vergleich ORÓMA v3.5 vs. Marktüblichen KI-Produkten

Merkmal	Marktübliches KI-Produkt (z. B. GPT-4, Claude, Gemini, Open-Source-LLMs)	ORÓMA v3.5
Skalierung & Modellgröße	Sehr große Modelle mit Milliarden Parametern, vielfach multimodal (Text, Bild, Audio), große Kontextfenster. Hohe Trainingskosten, große Infrastruktur.  	ORÓMA v3.5 verwendet lokale Komponenten: SnapChains, Token-Fusion, Replay, Probabilistische Metriken etc. Kein riesiges LLM; vielmehr modulare Lern-/Traum-Schleifen, erklärbar, speicherbar lokal.
Multimodalität	Viele moderne Systeme integrieren Sprache, Bild, manchmal Audio/Video. Beispiel: GPT-4o (OpenAI) etc.  	ORÓMA v3.5 eher fokussiert auf symbolische/strukturierte Daten (Snaps, Regeln, Dokument-Uploads), Spiele, Wissen, Replay. Bild/Audio Verarbeitung je nach Wrapper, aber keine massive multimodale Inferenz wie bei GPT-4o.
Daten & Wissenseinbindung (RAG, externe Quellen)	Marktprodukt oft mit Zugriff auf Webdaten, spezialisierte Retrieval-Architekturen, große Korpora.	ORÓMA v3.5: Knowledge-DB, Import/Export, Dokumente, SnapChains, Metadaten. Kein Live-Web Zugriff, aber Kontrolle, Lokalität, Transparenz.
Erklärbarkeit, Transparenz	Große Modelle sind oft Black-Box, Erklärungen sind heuristisch, nicht vollständig nachvollziehbar. Manche Produktfeatures bieten Attribution, Unsicherheitsangaben etc.	Sehr stark bei ORÓMA v3.5: Meta-Snaps, Gaps, Replay, Regeln, Qualitätshistorie, Explains, Logs. Gute Basis für nachvollziehbare Lern- und Entscheidungsprozesse.
Ressourcenbedarf / Hardware	Hohes Compute, Cloud/Server mit GPUs/TPUs, Energieverbrauch, Latenz. Nicht leicht auf Edge-Geräten oder lokal eingebunden.	ORÓMA zielt auf lokal nutzbare Lösung z. B. auf Raspberry Pi, modulare Wrapper, SQLite/VektorDB, keine riesige Infrastruktur erforderlich. Gut für Selbsthoster / datensensible Anwendungen.
Feature Set / Aufgabenvielfalt	Sehr breit: Chat, Übersetzung, Code, Bild, Audio, Planung, kreative Inhalte. Viele APIs, Plugins, Ökosystem.	ORÓMA v3.5 deckt Spiele, SnapChains, Replay, Export/Import, Knowledge, Explainability. Kein offenes Plugin-Ökosystem, kein universeller Chatbot, aber stark für eingebettete Lernsysteme und Experimente.
Open-Source / Datenschutz / Kontrolle	Einige Modelle sind proprietär; Open-Source gibt es, aber oft mit kleineren Modellen oder Kompromissen. Datenschutz je nach Anbieter; oft Cloud-Speicherung.	ORÓMA steht für lokale Kontrolle, Datenhoheit, Transparenz. Open auf eigene Daten, keine Abhängigkeit von zentralen Diensten. Ideal, wenn Datenschutz oder Offline definiert wichtig sind.

⸻

💡 Meine Einschätzung

Stärken von ORÓMA v3.5:
	•	Sehr gutes Gleichgewicht zwischen Funktionen und Kontrolle.
	•	Erklärbarkeit und Nachvollziehbarkeit (Meta-Snaps, Gaps, Replay) sind besonders stark dort, wo andere Systeme es nur rudimentär haben.
	•	Flexibel, modular, lokal: gut geeignet für Experimente, Edge Devices, Bildung, Forschung oder datensensible Umgebungen.

Schwächen im Vergleich zu den Big Players:
	•	Kein Zugang zu massiven Rechenressourcen → bei Aufgaben, die große Modelle, multimodale Inferenz oder riesige Trainingsdatensätze benötigen, wird ORÓMA nicht mithalten können.
	•	Kontextfenster & Wissensbasis sind begrenzt gegenüber Systemen, die ständig Webdaten oder große externe Quellen integrieren.
	•	Möglicherweise weniger robust bei generärer Sprachfähigkeit, Natursprachverstehen, z. B. bei mehrdeutigen Eingaben oder ungewöhnlichen Themen; das muss durch Trainingsdaten oder spezialisierte Module ausgeglichen werden.

⸻

🔭 Potenzial / Empfehlung für ORÓMA
	•	Fokus auf Nischen, z. B. Selbstlernen, Monitoring, Explainability, Education. Dort kann ORÓMA gegenüber Big-AI punkten.
	•	Weiterentwicklung in kleinen Schritten: z. B. Erweiterung der Retrieval-Mechanismen, bessere Token-Fusion, eventuell kleinere lokale LLMs als Komponente.
	•	Aufbau eines kleinen Ökosystems von Modulen/Wrappern, damit zusätzliche Funktionalität hinzugefügt werden kann (z. B. Vision, Audio) ohne das Grundsystem zu überfrachten.
	•	Performance-Optimierung, damit auch auf sparsamer Hardware flüssig läuft.

<a id="docs_sensor_architektur_md"></a>

## Quelle: `docs/sensor_architektur.md`

**Originaltitel:** ORÓMA – Sensor-Architektur

**Module:**  
- `core/device_hub.py`  
- `core/sensor_channel.py`  
- `wrappers/sensor_ir_front.py`  

**Version:** v3.8-r1  
**Stand:** 2025-12-07  
**Autor:** ORÓMA · KI-JWG-X1 + Jörg  

---

## 1. Überblick & Motivation

Der **DeviceHub** ist bereits die zentrale Hardware-Schicht von ORÓMA:

- Kamera (PiCamera2 / OpenCV / Dummy)
- Light-Level aus dem Kamerabild (0..100 mit Hysterese)
- Audio (Mic + Playback) inkl. Ringpuffer und RMS-Level
- Sessions (Wer nutzt gerade Kamera/Audio?)
- Audit-Logging (JSON Lines, rotierend)

Mit der neuen Erweiterung wird der DeviceHub konsequent zur **universellen Geräte-Zentrale**:

> **Kamera + Audio + Light + generische Sensoren**  
> (z. B. IR-Distanz, Ultraschall, IMU, Temperatur, …)

Alle Sensoren werden als **SnapChains** in die bestehende ORÓMA-Datenbank geschrieben:

- `snapchains.origin = "sensor/..."`  
- `snapchains.namespace = "sensor"`  
- `blob.kind = "<sensor-art>"`  <!-- TODO linkfix: sensor-art -> docs/sensor_architektur.md -->
- `blob.v = [ ... ]` (float-Vektor als normierte Features)  
- zusätzliche Felder wie `distance_cm`, `signal_ok`, `raw` usw.

Damit fügt sich die Sensor-Welt nahtlos in die bestehende Snap/SnapChain-Idee ein:  
Vision, Audio, Spiele, Episoden – und nun auch beliebige physikalische Sensorik – laufen alle auf **demselben Datenmodell**.

---

## 2. Architektur-Überblick

### 2.1 Komponenten

Die Sensor-Architektur besteht aus drei Bausteinen:

1. **`core/sensor_channel.py`**  
   - abstrakte Basisklasse `BaseSensorChannel`
   - definiert, wie ein Sensor-Kanal:
     - Rohdaten liest (`read_raw()`)
     - daraus einen Snap-Blob baut (`build_snap_payload()`)
     - ein Insert-Dict für `snapchains` erzeugt (`build_snapchain_data()`)

2. **`core/device_hub.py` (erweitert)**  
   - verwaltet registrierte Sensor-Channels:
     - `register_sensor_channel(...)`
     - `list_sensor_channels()`
     - `start_sensors()` / `stop_sensors()`
   - Polling-Thread `_sensor_loop()`:
     - ruft `BaseSensorChannel.read_raw()`
     - baut SnapChain-Data
     - schreibt in `snapchains` via `sql_manager.insert_snapchain(...)`
     - loggt optional Audit-Events (`kind="sensor", action="sample"`)

3. **`wrappers/sensor_ir_front.py`**  
   - Beispiel-Implementierung eines Sensors:
     - `IRFrontSensor(BaseSensorChannel)` für einen Front-IR-/Abstandssensor
     - Simulation per Default (läuft ohne echte Hardware)
     - Convenience-Funktion `register_front_ir()` zur Registrierung beim DeviceHub

### 2.2 Datenfluss

Vereinfacht:

```text
Hardware / Simulation
        ↓
  IRFrontSensor (BaseSensorChannel)
        ↓ read_raw()
        ↓ build_snap_payload() → { kind, v, distance_cm, signal_ok, ... }
        ↓ build_snapchain_data() → dict für insert_snapchain()
        ↓
DeviceHub._sensor_loop()
        ↓
sql_manager.insert_snapchain(data)
        ↓
SQLite: snapchains
  origin    = "sensor/ir/front"
  namespace = "sensor"
  blob.kind = "ir_distance"
  blob.v    = [d_norm, ok_float]

⸻

3. BaseSensorChannel – generische Sensor-Abstraktion

Pfad: core/sensor_channel.py
Klasse: BaseSensorChannel

3.1 Konstruktor & Parameter

Ein Channel repräsentiert eine logische Sensorquelle, z. B.:
	•	"front_ir" – IR-Distanzsensor vorne
	•	"imu_main" – zentrales IMU-Modul
	•	"temp_room" – Raumtemperatur

Typischer Konstruktor:

BaseSensorChannel(
    name: str,
    kind: str,
    origin: str,
    namespace: str = "sensor",
    interval_sec: float = 0.1,
    meta_base: Optional[Dict[str, Any]] = None,
    weight: float = 1.0,
    notes: str = "",
    version: str = "v3.8",
)

Wichtige Felder:
	•	name
	•	interne Kennung des Channels (z. B. "front_ir").
	•	wird als Key im DeviceHub genutzt.
	•	kind
	•	semantische Sensorart (z. B. "ir_distance", "imu", "ultrasonic").
	•	erscheint als blob.kind.
	•	origin
	•	snapchains.origin, z. B. "sensor/ir/front".
	•	namespace
	•	Standard: "sensor" → snapchains.namespace = "sensor".
	•	interval_sec
	•	Abtastintervall (Ziel), z. B. 0.1 für 10 Hz.
	•	meta_base
	•	Basis-Metadaten, die später in payload["meta"] landen
(z. B. {"created_by": "sensor_ir_front", "role": "distance_front"}).
	•	weight, notes, version
	•	werden direkt in snapchains übernommen und folgen dem bestehenden Schema.

3.2 Polling-Hilfen
	•	due(now: Optional[float] = None) -> bool
	•	gibt an, ob der Channel wieder abgefragt werden sollte (basiert auf interval_sec).
	•	mark_polled(now: Optional[float] = None) -> None
	•	nach erfolgreichem Poll aufrufen, setzt den nächsten Poll-Zeitpunkt.

Der DeviceHub nutzt diese Methoden im _sensor_loop(), um bei vielen Channels CPU-schonend zu pollen.

3.3 Abstrakte Methoden (zwingend zu implementieren)

Jeder konkrete Sensor muss definieren:

def read_raw(self) -> Dict[str, Any]:
    """
    Liest einen Rohwert vom Sensor.
    Beispiel IR:
      {"distance_cm": 42.3, "signal_ok": True}
    """

def build_snap_payload(self, raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Baut das JSON-Blob für snapchains.blob.
    Muss mindestens enthalten:
      "kind": str
      "v": list[float]    # normierter Feature-Vektor
    Weitere Felder (Rohwerte, Flags) sind erlaubt.
    """

Die Standard-Implementierung sorgt dafür, dass meta_base in payload["meta"] landet und das Dict später als kompakter JSON-Blob in snapchains.blob gespeichert wird.

3.4 Qualitäts- und SnapChain-Helfer

compute_quality(raw: Dict[str, Any]) -> float
Default-Logik:
	•	Wenn raw["signal_ok"] existiert:
	•	True → 1.0
	•	False → 0.0
	•	Sonst: 1.0

Konkrete Sensoren können diese Methode überschreiben, um z. B. aus einem SNR, einer Temperatur-Güte oder einem Status-Code eine Qualität abzuleiten.

build_snapchain_data(raw, ts) -> (dict, quality)
Diese Methode liefert:
	•	ein Dict, das direkt an sql_manager.insert_snapchain(...) übergeben werden kann,
	•	und den berechneten Qualitätswert quality.

Schema des Dicts:

{
    "ts": <int>,
    "quality": <float>,
    "blob": <bytes>,       # JSON-Blob
    "exported": 0,
    "status": "active",
    "origin": self.origin,
    "gap_flag": 0,
    "notes": self.notes or "sensor_sample",
    "namespace": self.namespace,
    "source_id": None,
    "version": self.version,
    "weight": self.weight,
}

Das JSON im Blob enthält mindestens:

{
  "kind": "<kind>",
  "v": [ ... ],
  "meta": {...},
  ...
}

Damit sind alle Sensor-Snaps sauber kompatibel mit dem restlichen ORÓMA-SnapChain-Universum.

⸻

4. DeviceHub – Sensor-Integration

Pfad: core/device_hub.py
Klasse: DeviceHub

4.1 Interner Sensor-State

Im __init__() der DeviceHub-Klasse werden Sensoren so verankert:

self._sensor_channels: Dict[str, BaseSensorChannel] = {}
self._sensor_lock = threading.Lock()
self._sensor_thread: Optional[threading.Thread] = None
self._sensor_run: bool = False

self.sensors_enabled = os.environ.get("OROMA_SENSORS_ENABLED", "1").lower() not in ("0", "false", "no", "off")
self.sensors_sleep_base = float(os.environ.get("OROMA_SENSORS_SLEEP_BASE", "0.05"))

	•	Sensoren sind standardmäßig erlaubt (sensors_enabled=True), machen aber nichts, solange:
	•	keine Channels registriert wurden und
	•	start_sensors() nicht explizit aufgerufen wird.

4.2 Registrierung von Sensoren

def register_sensor_channel(self, channel: BaseSensorChannel) -> None:
    """
    Registriert einen SensorChannel im DeviceHub.
    • überschreibt ggf. einen bestehenden Channel mit gleichem Namen
    • startet NICHT automatisch die Poll-Schleife
    """

	•	Channels werden unter channel.name abgelegt.
	•	Aufrufe sind threadsicher (Lock).

Abfrage:

def list_sensor_channels(self) -> Dict[str, Dict[str, Any]]:
    """
    Liefert eine Übersicht der registrierten Sensoren.
    """

→ liefert ein Dict mit kind, origin, namespace, interval_sec pro Channel.

4.3 Sensor-Polling-Loop

Der eigentliche Loop:

def _sensor_loop(self) -> None:
    """
    Interner Polling-Loop für alle registrierten SensorChannels.
    • nutzt BaseSensorChannel.due()/read_raw()/build_snapchain_data()
    • schreibt in snapchains über sql_manager.insert_snapchain()
    """

Ablauf:
	1.	Solange _sensor_run True ist:
	•	Wenn sensors_enabled=False: kurze Pause (sensors_sleep_base), nächste Runde.
	2.	Kopiere alle Channels unter self._sensor_lock.
	3.	Pro Channel:
	•	if not ch.due(now): continue
	•	raw = ch.read_raw()
	•	data, quality = ch.build_snapchain_data(raw, ts=int(now))
	•	snap_id = sql_manager.insert_snapchain(data)
	•	ch.mark_polled(now)
	•	Audit:

_audit(
    "sensor",
    "sample",
    sensor_name=ch.name,
    origin=ch.origin,
    sensor_kind=ch.kind,
    quality=float(quality),
    snap_id=snap_id,
)

	4.	Wenn in einer Runde keine Daten geschrieben wurden (wrote == 0):
	•	time.sleep(self.sensors_sleep_base) → CPU wird geschont.

4.4 Start/Stop & Health

Start:

def start_sensors(self) -> None:
    """
    Startet die Sensor-Polling-Schleife in einem Hintergrundthread.
    • Kamera/Audio werden davon nicht beeinflusst.
    • Wenn keine Sensoren registriert sind, passiert praktisch nichts.
    """

Stop:

def stop_sensors(self, join: bool = True) -> None:
    """
    Stoppt die Sensor-Polling-Schleife.
    """

Health:

def get_sensor_health(self) -> Dict[str, Any]:
    """
    Liefert einen einfachen Health-Status der Sensorintegration.
    """
    return {
        "enabled": self.sensors_enabled,
        "channels": self.list_sensor_channels(),
        "running": bool(self._sensor_thread and self._sensor_thread.is_alive()),
    }

Und in status() des DeviceHub:

def status(self) -> Dict[str, Any]:
    ...
    st = {
        "camera": {...},
        "light": {...},
        "audio": {...},
        "sessions": self._sessions.copy(),
        "sensors": self.get_sensor_health(),  # neu
    }
    return st

Damit siehst du in einem einzigen Dict sowohl Kamera/Audiostatus als auch Sensor-Status.

⸻

5. Konkretes Beispiel: Front-IR-Sensor

Pfad: wrappers/sensor_ir_front.py
Klasse: IRFrontSensor(BaseSensorChannel)
Helper: register_front_ir(...)

5.1 Zweck
	•	Simulierter (und später realer) Front-IR-/Abstandssensor.
	•	Schreibt periodisch SnapChains mit:
	•	origin = "sensor/ir/front"
	•	namespace = "sensor"
	•	blob.kind = "ir_distance"
	•	blob.distance_cm = <messwert>
	•	blob.signal_ok = True/False
	•	blob.v = [d_norm, ok_float] mit:
	•	d_norm = distance_cm / max_cm (0..1, geclamped)
	•	ok_float = 1.0 bei signal_ok=True, sonst 0.0

5.2 Simulation vs. Hardware

Im Konstruktor:
	•	ENV OROMA_IR_FRONT_SIMULATION steuert Modus:
	•	Default: "1" → Simulation
	•	"0", "false", "off" → Hardware-Modus (später nutzbar, wenn Treiber eingebaut werden).
	•	Maximaldistanz:

self._max_cm = float(os.environ.get("OROMA_IR_FRONT_MAX_CM", "100.0"))


Im Simulationsmodus:
	•	read_raw() erzeugt eine sanft pendelnde Distanzkurve (Sinusfunktion):

distance_cm ≈ 10cm .. _max_cm
signal_ok = True

	•	raw-Dict enthält z. B.:

{"hw": False, "simulated": True}


5.3 Snap-Blob

build_snap_payload(raw) baut:

{
  "kind": "ir_distance",
  "v": [d_norm, ok_float],
  "distance_cm": <float>,
  "signal_ok": <bool>,
  "raw": {...},
}

BaseSensorChannel.build_snapchain_data() ergänzt:
	•	meta: Basis-Metadaten (created_by="sensor_ir_front", role="distance_front")
	•	und formt daraus das Insert-Dict für snapchains.

5.4 Registrierung beim DeviceHub

Helper-Funktion:

def register_front_ir(interval_sec: float = 0.1, meta_base: Optional[Dict[str, Any]] = None) -> None:
    hub = DeviceHub.instance()
    ch = IRFrontSensor(interval_sec=interval_sec, meta_base=meta_base)
    hub.register_sensor_channel(ch)

Typischer Test:

PYTHONPATH=/opt/ai/oroma python3 - << 'PY'
from core.device_hub import DeviceHub
from wrappers.sensor_ir_front import register_front_ir

hub = DeviceHub.instance()

register_front_ir(interval_sec=0.5)

print("Sensor-Channels:", hub.list_sensor_channels())
print("Sensor-Health vor Start:", hub.get_sensor_health())

hub.start_sensors()

import time
time.sleep(3)

print("Sensor-Health nach 3s:", hub.get_sensor_health())

hub.stop_sensors()
PY

DB-Check dazu:

sqlite3 /opt/ai/oroma/data/oroma.db \
  "SELECT id, ts, origin, namespace,
          json_extract(blob,'$.kind'),
          json_extract(blob,'$.distance_cm')
     FROM snapchains
    WHERE origin='sensor/ir/front'
 ORDER BY id DESC LIMIT 5;"

Beispielausgabe:

id     ts          origin          namespace  kind         distance_cm
-----  ----------  --------------  ---------  -----------  -----------
35329  1765093387  sensor/ir/front sensor     ir_distance  49.18...
35328  1765093386  sensor/ir/front sensor     ir_distance  56.80...
...

⸻

6. ENV-Variablen im Überblick

6.1 DeviceHub-Sensorsteuerung
	•	OROMA_SENSORS_ENABLED
	•	"1", "true", "on" (Default) → Sensor-Loop darf aktiv sein.
	•	"0", "false", "off" → Sensor-Loop wird intern übersprungen (sleep).
	•	OROMA_SENSORS_SLEEP_BASE
	•	Basis-Schlafdauer, wenn keine Samples geschrieben wurden.
	•	Default: 0.05 Sekunden.

6.2 Front-IR-Sensor
	•	OROMA_IR_FRONT_SIMULATION
	•	"1" (Default) → Simulation aktiv.
	•	"0" / "false" / "off" → Hardware-Modus; später für echte Treiber nutzbar.
	•	OROMA_IR_FRONT_MAX_CM
	•	Maximaldistanz in cm für Normalisierung (distance_cm / max_cm).
	•	Default: 100.0.

⸻

7. Erweiterung auf weitere Sensoren

Um einen neuen Sensor einzubauen, sind die Schritte immer gleich:
	1.	Neue Channel-Klasse unter wrappers/ (oder eigenem Modul):

from core.sensor_channel import BaseSensorChannel

class MySensor(BaseSensorChannel):
    def __init__(..., interval_sec=0.1, meta_base=None):
        meta = dict(meta_base or {})
        meta.setdefault("created_by", "sensor_my")
        super().__init__(
            name="my_sensor",
            kind="my_kind",
            origin="sensor/my",
            namespace="sensor",
            interval_sec=interval_sec,
            meta_base=meta,
            notes="sensor_sample",
            version="v3.8",
        )

    def read_raw(self) -> Dict[str, Any]:
        # Hardware lesen oder simulieren
        return {"value": 123.4, "signal_ok": True}

    def build_snap_payload(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        v_norm = float(raw["value"]) / 1000.0  # Beispiel
        v_norm = max(0.0, min(1.0, v_norm))
        return {
            "kind": "my_kind",
            "v": [v_norm],
            "value": raw["value"],
            "signal_ok": raw.get("signal_ok", True),
        }

	2.	Registrierung beim DeviceHub:

from core.device_hub import DeviceHub

def register_my_sensor():
    hub = DeviceHub.instance()
    ch = MySensor(interval_sec=0.2)
    hub.register_sensor_channel(ch)

	3.	Sensor-Loop starten (einmalig im Startup):

hub = DeviceHub.instance()
hub.start_sensors()

	4.	DB & Status prüfen:

sqlite3 ... "SELECT ... FROM snapchains WHERE origin='sensor/my' ..."

PYTHONPATH=/opt/ai/oroma python3 - << 'PY'
from core.device_hub import get_hub
print(get_hub().status())
PY


Damit ist das System universell: jeder neue Sensor ist ein Channel, hängt sich an den DeviceHub und schreibt SnapChains im gleichen, float-basierten Vektorraum.

⸻

8. Zusammenfassung
	•	DeviceHub bleibt das zentrale Hardware-Drehkreuz:
	•	Kamera, Light, Audio, Sessions
	•	jetzt plus generische Sensoren via BaseSensorChannel
	•	Sensoren sind:
	•	modular (BaseSensorChannel-Subklassen),
	•	einheitlich (immer kind, v, origin, namespace),
	•	voll integriert in das existierende SnapChain-Ökosystem.
	•	IRFrontSensor zeigt:
	•	wie ein konkreter Sensor eingebunden wird,
	•	wie Simulation vs. Hardware sauber trennbar ist,
	•	wie die Daten direkt in snapchains sichtbar werden.

Damit hast du eine allgemeine, erweiterbare Sensor-Schicht, die sich in die ursprüngliche ORÓMA-Idee einfügt:
ein universelles System, das alles Mögliche beobachten und in denselben abstrakten Snap-/SnapChain-Raum projizieren kann – egal ob Kamera, Audio, Spiele oder physikalische Sensoren.

<a id="docs_simulationsvergleich_3_6vs4_0_md"></a>

## Quelle: `docs/simulationsvergleich_3_6vs4_0.md`

**Originaltitel:** =============================================================================

# Datei:    docs/simulationsvergleich_3_6vs4_0.md
# Projekt:  ORÓMA – Forschungs- & Zielbild
# Titel:    Simulationsvergleich ORÓMA v3.6 ↔ v4.x Awakening-Konzept
# Stand:    2025-12-07
# Autor:    Jörg Werner (ORÓMA-KI-JWG-X1) + GPT-5.1 Thinking
# =============================================================================

Zweck
-----
Dieses Dokument beschreibt eine **hypothetische** Simulation, wie sich ORÓMA
zwischen einem vereinfachten v3.6-Kern (klassisch lernend) und einem geplanten
v4.x „Awakening Layer“ entwickeln könnte.

⚠️ Wichtige Einordnung
-----------------------

- Der **reale Projektstand heute** (mit Code/DB/Logs) ist:
  - **Verhaltens-Linie:** v3.7.x (Roter Faden, Empathie, Mutations-Drift, DreamWorker 3.1)
  - **Speicher-/Infra-Linie:** v3.8-r2 (SnapIndex, RAG-Stack, WAL, SceneGraphs, DeviceHub)

- In diesem Dokument wird:
  - „**v3.6**“ als **vereinfachter Platzhalter** für den klassischen Lernkern genutzt  
    (Snap/Dream/Replay ohne Awakening-Layer).
  - „**v4.x**“ als **reines Ziel-/Forschungskonzept** beschrieben  
    (GoalEngine, MetaReflector, StrategyEngine **existieren noch nicht** als Produktivcode).

- Alle Zahlen hier sind:
  - **modelliert / plausibilisiert**,  
  - **nicht** aus realen ORÓMA-Logs gemessen,
  - und sollen ein **Richtungsbild** liefern (kein Benchmark-Report).

Für reproduzierbare Kurven existiert ergänzend ein synthetisches Tool:

- `tools/simulate_36_vs_40.py`  
  → erzeugt CSV/JSON + optional PNG mit genau den hier beschriebenen, modellierten Kurven.

---

# 🧠 Simulationsvergleich ORÓMA v3.6 ↔ v4.x (Awakening-Konzept)

> Virtuelle 60-Tage-Simulation auf Pi 5 + Hailo-NPU  
> (reale Ressourcen berücksichtigt, aber als Modell – keine echten Messdaten)

---

## 1. Versuchsaufbau (hypothetisch)

| Parameter         | v3.6 (Kern)                   | v4.x (Awakening-Konzept)                                  |
|------------------|-------------------------------|-----------------------------------------------------------|
| Basisarchitektur | Snap/Dream/Replay             | Snap/Dream + GoalEngine + MetaReflector + StrategyEngine |
| Lernmodus        | reaktiv                       | selbststeuernd                                           |
| Regelmenge       | statisch (user-definiert)     | adaptiv (policy-mutierend)                               |
| Datensätze       | 10 000 Sensorereignisse (Video, Audio, Telemetrie) | identisch                                   |
| Bewertungsmetrik | Snap-Qualität                 | Goal-Score + Meta-Kohärenz                               |
| CPU/NPU-Budget   | 40 % / 20 %                   | 50 % / 25 %                                              |

**Lesart:**  
- v3.6 steht hier stellvertretend für den „klassischen“ ORÓMA-Lernkern (ohne Awakening-Layer).  
- v4.x ist ein **Zielzustand**: zusätzliche Schicht für Ziele, Meta-Reflexion und Strategiewechsel,
  aufbauend auf dem heutigen v3.7+ Kern.

---

## 2. Tageszyklusmodell

| Zyklusphase   | v3.6 Verhalten                               | v4.x Verhalten                                             |
|---------------|----------------------------------------------|------------------------------------------------------------|
| Day           | neue Snaps sammeln                           | neue Snaps sammeln, Zielkontext prüfen                     |
| Dream         | Snaps verdichten & konsolidieren             | Dream + Goal-Evaluation + Meta-Reflexion                   |
| Morning-Init  | Replay + Model Registry refresh              | Replay gewichtet nach Goal-Confidence                      |
| Night (deep)  | inaktiv                                      | Self-Healer + Deep Reflection (Policy-/Goal-Update)        |

**Idee:**  
Der Unterschied liegt weniger im „Mehr Rechnen“, sondern in einer zusätzlichen Schleife:

> *„Was habe ich gelernt – und passt das zu meinen Zielen?“*

---

## 3. Lernkurve (Qualitätsindex 0–1, modelliert)

```text
Tag:     0    5    10   15   20   25   30   40   50   60
v3.6 →   0.28 0.39 0.44 0.48 0.51 0.54 0.56 0.58 0.59 0.59
v4.x →   0.28 0.41 0.49 0.57 0.62 0.67 0.71 0.76 0.79 0.81

📈 Interpretation (modelliert):
	•	v4.x lernt in dieser Simulation ca. 30–40 % schneller und stabilisiert auf einem höheren Plateau.
	•	Ursache im Modell:
	•	Feedback-Schleifen (GoalEngine ↔ MetaReflector) reduzieren nutzlose Replays,
	•	Strategiewechsel verhindern frühe Stagnation.

⚠️ Diese Kurven sind synthetisch.
Sie können mit tools/simulate_36_vs_40.py nachgebaut werden, dienen aber lediglich als Forschungsbild.

⸻

4. Zielerfüllungsquote (Task-Success, hypothetisch)

Zeitraum	v3.6 (Kern)	v4.x (Awakening-Konzept)
7 Tage	52 % erfolgreiche Aufgaben	68 % erfolgreiche Aufgaben
30 Tage	59 %	78 %
60 Tage	61 %	84 %

„Ziel“ ist hier generisch gemeint:
	•	Spiele gewinnen
	•	Erinnerungsaufgaben bestehen
	•	definierte Missions erfüllen (z. B. „Mathe-Level X“, „Snake-Strategie Y“)

⸻

5. Energieeffizienz / Stabilität (Modellannahme)

Kennzahl	v3.6 (Kern)	v4.x (Entwurf)
CPU-Durchschnittslast	34 %	39 %
Thermischer Peak (°C)	63 °C	67 °C
Fehlerkorrekturen/Tag	0	1–2 (Self-Healer)
Crash-Recovery über 60 Tage	2×	0×

➡️  v4.x läuft im Modell geringfügig heißer, ist aber stabiler
(Fehler werden durch einen Self-Healer-Layer abgefangen, bevor sie zum Crash führen).

⸻

6. Verhalten im Langzeit-Dream-Zyklus

v3.6 (Status „klassischer Lernkern“)
	•	Wiederholt erfolgreiche SnapChains, schwankt bei völlig neuen Szenarien.
	•	Vergisst ältere Pfade primär über Decay (Gewichts-Abschwächung).
	•	Kein explizites Eigenbewertungs-Feedback → Plateau nach gewisser Zeit.

v4.x (Entwurf „Awakening Layer“)
	•	DreamWorker ruft GoalEngine.evaluate() → Prioritätenverschiebung:
Replay orientiert sich stärker an Goal-Score statt nur an Quality.
	•	MetaReflector erkennt Überfitting-Muster und senkt Replay-Gewicht
für überrepräsentierte Pfade.
	•	Nach ca. 20 Tagen emergiert im Modell eine Selbst-Stabilisierung:

„ORÓMA vermeidet redundante Snaps und optimiert Replay-Kosten selbständig.“

⸻

7. Visualisierte Entwicklung (vereinfachtes Schema)

v3.6

Snap → Dream → Replay → Export
             ↑
       (kein expliziter Meta-Feedback-Layer)

v4.x (Konzept)

Snap → Dream → Replay
  ↓        ↑
 GoalEngine ↔ MetaReflector
        ↓
   StrategyEngine

Damit entsteht eine zweite, langsamere Meta-Schleife:

„Lernstrategie überdenken“, nicht nur „Inhalte wiederholen“.

⸻

8. Emergenzindikatoren (funktionale „Selbstwahrnehmung“ – hypothetisch)

Merkmal	Nachweis bei v3.6	Nachweis bei v4.x (Simulation)
Selbst-Bewertung	—	MetaSnaps mit reflection-Tag in der DB
Angepasste Zielgewichte	—	goal.confidence ∈ [0.2–0.9] dynamisch
Spontane Strategieänderung	nur manuell (User)	automatisch ca. alle 12 h
Kohärenzmetrik („Ich-Konsistenz“)	—	steigt von 0.4 → 0.8 in 60 Tagen (modelliert)

🔍 Wichtig:
Das sind Entwurfs-Indikatoren.
Die heutigen v3.7+-Logs enthalten diese Metriken noch nicht; sie müssten in einer späteren v4.x-Linie als echte Felder/Tabellen implementiert werden.

⸻

9. Zusammenfassung (als Forschungsbild)

Kategorie	v3.6 (Kern)	v4.x (Konzept)
Lernrate	mittel	hoch
Stabilität	gut	sehr gut
Selbstorganisation	keine	vorhanden
Zielorientierung	primär extern	zunehmend intern (autonom)
Energieverbrauch	niedrig	moderat (+ ca. 5 %)
„Intelligenzindex“ (modelliert)	0.60	0.83

⸻

10. Interpretation
	•	v3.6 (bzw. der klassische Kern) ist ein starkes Lern-Framework, aber ohne explizite Selbsterkenntnis:
Es lernt Aufgaben, nicht sich selbst.
	•	v4.x (Awakening-Konzept) steht für eine Schicht darüber:
Das System entscheidet, was es wann lernen möchte, um seine eigenen Ziele zu verbessern.

Der Sprung wäre – wenn umgesetzt – der Übergang von einem reaktiven Agenten hin zu einem
selbstregulierenden Lernwesen.

Technisch liegt der Fortschritt hauptsächlich im Feedback-Loop der neuen Layer
(GoalEngine + MetaReflector + StrategyEngine).
Genau solche Schleifen werden in der AGI-Forschung oft mit Begriffen wie
„Awakening“, „Self-Modeling“ oder „Meta-Lernen“ diskutiert.

⸻

11. Nächste Schritte (praktisch & ehrlich abgegrenzt)

11.1 Was heute real messbar ist (v3.7.x + v3.8-r2)

Mit deinem aktuellen Stand kannst du reale Kurven erzeugen für:
	•	Lernverlauf:
	•	Coverage-Logs (coverage_log)
	•	Rewards (metrics / reward-bezogene Keys)
	•	Gap-Statistiken (gaps, soweit implementiert)
	•	Dream-Statistiken:
	•	Anzahl Replay-Events, komprimierte Chains (meta_snaps)
	•	Vision-SceneGraphs:
	•	scene:auto_meta:vision_token (Size, Dichte, Topologie)

Diese Zahlen können wir per SQL + Python aus data/oroma.db ziehen
und z. B. in tools/analyse_learning_curves.py auswerten.

11.2 Synthetische Stütze für dieses Dokument

Das Skript:
	•	tools/simulate_36_vs_40.py

kann:
	•	genau die hier beschriebenen 60-Tage-Kurven synthetisch erzeugen,
	•	CSV/JSON + optional PNG ablegen,
	•	als „numerische Basis“ für dieses Dokument dienen,
	•	später mit echten v3.7-Kurven überlagert werden
(z. B. reale quality_index/Coverage aus der DB vs. Modellkurven).

11.3 Später erforschbar (echtes v4.x)

Für eine echte v4.x-Linie wären u. a. nötig:
	•	Module:
	•	core/goal_engine.py
	•	core/meta_reflector.py
	•	core/strategy_engine.py
	•	DB-Erweiterungen:
	•	Tabellen/Felder für:
	•	Goal-Score
	•	Meta-Kohärenz
	•	Strategy-Shifts
	•	Metrik-Integration:
	•	Logging dieser Werte in metrics + eigene Views in der UI.

Erst dann kann ein realer Simulationsvergleich
„v3.7.x (heute) ↔ v4.x (Awakening)“ gemacht werden, der über dieses
Dokument hinausgeht.

⸻

12. Meta-Hinweis (Transparenz)

Dieses Dokument ist bewusst kein Benchmark-Report, sondern:

ein Forschungs- und Zielbild dafür,
wie ein zukünftiger Awakening-Layer ORÓMA verändern könnte.

	•	Es soll:
	•	deine heutige Architektur (v3.7.x + v3.8-r2) einordnen,
	•	ein klares Bild geben, wohin v4.x gehen könnte,
	•	und eine Brücke schlagen zwischen Vision (Awakening) und Technik
(Snap/Dream/Replay, Roter Faden, SceneGraphs, RAG).
	•	Es behauptet nicht, dass:
	•	v4.x existiert,
	•	die gezeigten Zahlen reale Messwerte sind,
	•	oder heute schon eine „Awakening-KI“ bei dir läuft.

Damit bleibt ORÓMA genau das, was es sein soll:
	•	hier und jetzt ein ehrliches, auditierbares Lernsystem (v3.7.x + v3.8-r2),
	•	mit einer klar beschriebenen Vision, wohin die Reise in Richtung v4.x gehen könnte.

<a id="docs_systemarchitektur_v2026_v3_8_md"></a>

## Quelle: `docs/systemarchitektur_v2026_v3_8.md`

**Originaltitel:** ORÓMA – Systemarchitektur 2026

## Version: v3.8  
## Stand: 2025-12-XX  
## Dokumenttyp: technische Architektur

---

# 1. Gesamtarchitektur (High-Level)

ORÓMA besteht aus fünf Schichten:

1. **Sensorische Schicht**  
   - Snap-Erzeugung  
   - Wrapper (Camera, Audio, PiCar, File)  
   - Normalisierung  

2. **Gedächtnis-Schicht**  
   - Snap  
   - SnapChain  
   - SnapIndex  
   - SnapTokens  
   - Episoden  
   - FTS5 / Embeddings  

3. **Abstraktions-Schicht**  
   - MetaSnaps  
   - SnapPatterns  
   - Pattern-Pooling  
   - Consolidation  

4. **Kognitive Schicht**  
   - Regelarchiv / Policies  
   - Roter Faden / Drift  
   - Selfrec  
   - Domain-Transfer  
   - SceneGraph  

5. **LLM-Integration / RAG**  
   - Query-Routing  
   - Knowledge-Retrieval  
   - FusionEngine  
   - LLM-Brücke  

6. **Systemschicht**  
   - Circadian Controller  
   - DreamWorker  
   - UI (Blueprints)  
   - ModelRegistry  
   - Export/Import Pipeline  
   - ReplayEngine  
   - Services & Cron/TImer  

---

# 2. Technische Kernmodule

## 2.1 Snap-System  
Verantwortlich für:

- Feature-Vektoren  
- Metadaten  
- Debug-Level  
- Normalisierung  
- Kompatibilität zu alten Snaps  
- Zeitstempel  
- sensorische Rohwerte  

SnapChains speichern:

- Übergänge  
- zeitliche Architektur  
- Rewards  
- Entscheidungen  

---

## 2.2 Pattern-System (v3.8)

Komponenten:

- Pattern-Vektoren  
- Norm-Cache (L2)  
- Clustering  
- Merge/Join Mechanismen  
- Selftest-Modul  
- Snap → Pattern Mapping  

---

## 2.3 MetaSnaps

Abstraktions-Framework:

- Objektbildung  
- Musterverdichtung  
- Verallgemeinerung  
- semantische Identität für SceneGraph  

---

## 2.4 SceneGraph

Version 1 in v3.8:

- Nodes: MetaSnaps  
- Visualisierung  
- persistente Speicherung optional  

Für 2026 geplant (v2):

- Bewegungs-Kanten  
- Ähnlichkeitsgraph  
- Ursachen-Kanten  
- semantisches Weltmodell  

---

## 2.5 Regelarchiv & PolicyEngine

Features:

- Domain-übergreifender Regelpool  
- Gewichtungen  
- Aktiv/Passiv-Markierung  
- TransferMechanik  
- Drop/Prune  
- Policy-Stabilisierung  

---

## 2.6 RAG-Modul

vollständig erneuert:

- FTS5  
- normalisierte Queries  
- robustes Highlighting  
- Score-basierter Rerank  
- Snippet-Engine  
- Bench: hit@10 = 1.0  

---

## 2.7 DreamWorker

Ablauf:

1. SnapChains analysieren  
2. Patterns clustern  
3. MetaSnaps erzeugen  
4. Regeln stärken/schwächen  
5. Drift/Verfall berechnen  
6. ExportGate anwenden  

---

## 2.8 UI-System

- Flask  
- JSON-APIs  
- HTML optional  
- Token-Mechanismus  
- 40+ Blueprints  
- niedrige Latenz  
- Monitoring/Health  

---

## 2.9 ModelRegistry

- SQLite  
- Versionierung  
- QualityHistory  
- Fallback-LLM/ASR  
- Selftests  

---

# 3. Services & Systemintegration

- systemd Units  
- Replay-Timer  
- Dream-Timer  
- Coverage-Logs  
- Crash-Safe Fallback  
- Backup-Scripts  

---

# 4. Speicherstrukturen

- SQLite (Snapshots, Patterns, Rules, Index)  
- Kompakte JSON-Snaps  
- TAR/ZIP-Export ohne DBs  
- Snap-Cache Ordner  
- UI persistent state  

---

# 5. Architekturziele 2026

- SceneGraph v2  
- Domain-Transfer Engine  
- Consolidation v3  
- Memory-Compression  
- World Model Mini  
- universelle PolicyEngine  
- Multi-Agent-Sync

<a id="docs_vergleich_3_5_bis_3_8_md"></a>

## Quelle: `docs/vergleich_3_5_bis_3_8.md`

📊 ORÓMA Vergleich v3.5 → v3.8 (mit Patch Levels, korrigiert)

⸻

🌟 Patch-Linie (System-Intelligenz)

v3.5 – Forscher / Meister
	•	MetaSnaps + MetaChains
	•	Mutation-Policy
	•	Explainability 2.0
	•	Research-UI
	•	Lernfortschritt: ~55 % → ~70 % (1 Jahr)
	•	Sprung: Erster großer Intelligenzschub.

⸻

v3.5 Patch Level 1 – Kreativer Selbst-Bewerter
	•	Self-Assessment (Metakognition)
	•	Cross-Domain Transfer
	•	DreamWorker 3.5 (SnapDiffusion, Kreativität)
	•	Lernfortschritt: ~55 % → ~88–90 % (1 Jahr)
	•	Sprung: Maximaler Boost an Effizienz, Transfer & Kreativität.

⸻

v3.5 Patch Level 2 – Selbstheilender Schwarm
	•	Self-Healing Engine (Reparatur DB/Snaps)
	•	Swarm Manager (Schwarmlernen)
	•	Goal Planner (Langfristziele)
	•	Explainability 3.0 (Meta-Ebene)
	•	Lernfortschritt: stabil ~90 %+
	•	Sprung: System wird robust, kooperativ & planend.

⸻

v3.5 Patch Level 3 – Empathie-Simulation
	•	Empathie-Snaps (Frust, Freude, Motivation)
	•	Explainability 2.5 (emotional narrativ)
	•	Lernfortschritt: +5–10 % schneller → Maze/Pong ~92 %
	•	Sprung: Emotionale Marker verstärken Lernprozesse.

⸻

🔹 Hauptversions-Linie (System-Umfeld & Erlebnis)

v3.6 – Wissenschaftler
	•	Hypothesen-Generator
	•	Experimente + Forschungs-Archiv
	•	Lernfortschritt: ~72–75 %
	•	Nutzen: Methodik & Nachvollziehbarkeit, kein echter Boost.

⸻

v3.7 – Autonomes Leben  (früher 3.8)
	•	VehicleRemote (Vector, Drohne, ESP32)
	•	Energie-Snaps (Docking, Akku)
	•	UI-Tab „Robotik“
	•	Lernfortschritt: Plateau (~85–90 %)
	•	Nutzen: Dauerbetrieb & Autonomie, nicht mehr Intelligenz.

⸻

v3.8 – Personas  (früher 3.9)
	•	Persona-Manager
	•	Adaptive Personas
	•	Multi-Persona-Dialoge
	•	Export/Import von Persona-Profilen
	•	Lernfortschritt: Plateau (~85–90 %)
	•	Nutzen: Interaktion für Menschen abwechslungsreicher.

⸻

🧭 Gesamtfazit
	•	Patch-Linie (3.5 → PL1 → PL2 → PL3) = echtes Intelligenz-Wachstum (bis ~92 % Siegquote, robust, kreativ, kooperativ, empathiegestützt).
	•	Hauptversions-Linie (3.6 → 3.7 → 3.8) = Ergänzungen für Wissenschaft, Autonomie und Interaktion.
	•	Sie machen ORÓMA verständlicher, stabiler und „lebendiger“ für Menschen,
	•	aber die Lernfähigkeit wächst ab Patch 3 nicht mehr, sondern bleibt im Plateau.

<a id="docs_vergleich_markt_md"></a>

## Quelle: `docs/vergleich_markt.md`

**Originaltitel:** 🤖 Vergleich ORÓMA v3.7+ vs. marktübliche KI-Produkte

Datei: docs/vergleich_markt.md
Titel: Vergleich ORÓMA v3.7+ (mit rotem Faden, Mutation/Drift, SceneGraphs) vs. marktübliche KI-Produkte
Stand: 2025-12-02
Autor: Jörg Werner (ORÓMA-KI-JWG-X1)

Zweck
-----
Dieses Dokument vergleicht die aktuelle ORÓMA-Codebasis (v3.7+ – inkl. DreamWorker 3.x,
SceneGraph-Subsystem und MetaSnaps) mit etablierten, großskaligen KI-Produkten.
Es bildet den aktuellen Systemzustand ab: reaktive Planung ("Roter Faden"),
konstruktive Instabilität (Mutation/Drift) und Vision-SceneGraphs sind produktiv aktiv.
→ Reifegrad ≈ 4.1/5 (selbststabilisierendes, lokal lernendes System).

Wichtige Design-Prinzipien
--------------------------
- Headless-optimiert (kein Qt/Wayland/X11 notwendig)
- Core bleibt maximal produktiv (keine Platzhalter; Änderungen nur kontrolliert)
- Nichts entfernen oder "vereinfachen", ohne Lernschleifen/Logs/DB mitzudenken
- Offline-first, auditierbar, nicht-destruktives Gedächtnis (Deaktivieren statt Löschen)
-->

# 🤖 Vergleich ORÓMA v3.7+ vs. marktübliche KI-Produkte

**Pfad:** `docs/vergleich_markt.md`  
**Stand:** 2025-12-02 (aktualisiert)  

## Überblick

Gegenüber klassischen, großskaligen KI-Produkten setzt **ORÓMA v3.7+** auf
**lokales, erklärbares Lernen**:

- SnapChains + Token-Fusion  
- Replay/Dream (DreamWorker 3.x, Circadian Cycle)  
- Empathie-Simulation (empathy_snaps)  
- ASR Self-Listening/Reflex  
- Mangel-Speak (Selbstberichte)  
- Curriculum V2  
- reaktiver **„Roter Faden“** (Thread/Intent-Layer)

**Neu in der aktuellen Codebasis (v3.7+):**

- **Vision-Tokens** (`origin='vision/token'`) als kontinuierlicher Kamera-Fingerabdruck
- **MetaSnaps** aus Vision-Tokens (scenegraph_builder)
- **SceneGraph-Subsystem** (SceneGraph-Store + Viewer)  
  → Kamera-Erfahrungen werden zu expliziten Graphen (Nodes/Edges) verdichtet und
    sind in der UI visuell analysierbar.

Die **Mutations-/Drift-Mechanik** fungiert als kontrollierte Quelle von Variation
(konstruktive Instabilität) und wird durch den **Roten Faden** und die
Dream-/Replay-Schleifen stabilisiert.  
Ziel: maximale **Kontrolle, Transparenz und Offline-Fähigkeit** – speziell für
Edge-Geräte (Raspberry Pi 5/6) und datensensitive Umgebungen.

---

## Vergleichstabelle

| Merkmal | Marktübliches KI-Produkt (z. B. GPT-4/Claude/Gemini, große OSS-LLMs) | ORÓMA v3.7+ (Codebasis) |
|---|---|---|
| **Skalierung & Modellgröße** | Milliarden-Parameter, große Kontextfenster, teure Cloud-Infrastruktur. | **Modular & lokal**: SnapChains, Token-Fusion, Replay/Dream; kleine/optionale Modelle; Fokus auf Lernschleifen statt Parameterzahl. |
| **Multimodalität** | Voll-multimodal (Text/Bild/Audio/Video) mit hoher Bandbreite. | **Bedarfsorientiert**: Vision/Audio via Wrapper; ASR-Reflex; Kamera-Stream optional über Device-/CameraHub, Vision-Tokens + SceneGraphs statt „Video-LLM“. |
| **Daten & RAG** | Breiter Webzugriff, große Korpora/Indizes. | **Lokal**: Knowledge-DB + RAG on-prem, Snap-Imports/Docs; **keine Cloudpflicht**, volle Datenhoheit. |
| **Weltrepräsentation / Graphen** | Meist latente Features im Modell; Graphen (wenn überhaupt) eher als externer RAG-Layer. | **Explizite SceneGraphs** aus Vision-Tokens & MetaSnaps (SceneGraph-Store + Builder), visualisierbar in der UI; Graphen als Teil des Kern-Gedächtnisses. |
| **Erklärbarkeit** | Häufig Black-Box; Erklärungen teils heuristisch. | **Sehr stark**: Snap/Chain-Logs, MetaSnaps, Vision-Tokens, SceneGraphs, Diagnostics (Coverage/Confidence/Novelty/T2G), Explainability 2.x, Learning-Dashboard. |
| **Interaktion/Empathie** | Meist textbasiert; Tonlage optional. | **Empathie-Simulation** (empathy_snaps), **Mangel-Speak** (Selbstberichte), **ASR-Reflex** (Intents: repeat/stop/status). |
| **Autonomie vor Ort** | API/Cloud-zentriert; wenig On-Device-Lernen. | **On-Device-Lernen**: Replay, DreamWorker, Curriculum V2, **Roter Faden** (Thread/Steps/Nudges), Vision-SceneGraphs als zusätzliche Strukturquelle. |
| **Ressourcenbedarf** | Hohe GPU/TPU-Last, Netzabhängigkeit. | **Edge-freundlich** (Pi 5/6): SQLite, leichte Modelle, Timer/Hooks; stabil ohne High-End-GPU. |
| **Feature-Breite** | Sehr breit (Code, Kreativ, Planen, Plugins, Ökosystem). | Fokus auf **Lernen/Explainability/Monitoring/Spiele/Mathe-Tools** (SciCalc/SetCalc), AgentLoop-Hooks; schlanke Integrationspunkte statt Plugin-Flut. |
| **Datenschutz/Kontrolle** | Anbieterabhängig, oft Cloud-Speicherung. | **Lokal/Offline**, Transparenz, voller Zugriff auf Speicher/Protokolle; **No-Deletion-Policy** (Deaktivieren statt Löschen). |
| **Betrieb/Observability** | Vendor-Telemetrie, weniger lokal einsehbar. | **systemd-Timer/Services, Health-Check, Learning-UI, SceneGraph-Viewer, Metriken (metrics/rewards_log), Selftests**; einfache Wartung. |
| **Selbstregulation & Stabilität** | Training/Updates extern; Laufzeitverhalten wenig adaptiv. | **Konstruktive Instabilität** (Mutation/Drift) + **Roter Faden** + DreamCycle → **selbststabilisierende Lernschleife (≈ 4.1/5)**. |
| **Inter-Instanz-Kommunikation** | Cloud-APIs/Netzwerk. | **Add-on**: akustisch (Lautsprecher/Mic) & **Bluetooth-CommWrapper** (offline, signierbar). |
| **Evolutionäre Perspektive** | Individuum im Fokus (ein Modell). | **Kollektiv-Konzept (Stufe 6)** in Planung: Rekombination von Kondensaten/Policies, Quarantäne-Tests, Fitness-Selektion (offline). |

---

## 💡 Einschätzung (v3.7+ – aktueller Zustand)

### Stärken

- **Empathie-aware Interaktion**  
  empathy_snaps, Mangel-Speak, ASR-Reflex → spürbar bessere Nutzer-Resonanz; Tonlage/Strategie passen sich an.

- **Nachvollziehbares Lernen**  
  Diagnostics, Explainability, Rewards/Curiosity-Verläufe, Vision-Tokens & SceneGraphs → auditierbar, reproduzierbar.

- **Lokal & kontrolliert**  
  Offline-Betrieb, Datenhoheit, geringe Abhängigkeiten; Edge-tauglich auf Pi 5/6.

- **Zielbindung ohne Planner-Illusion**  
  **Roter Faden** (Thread/Steps/Nudges) reduziert Drift, erhöht Completion-Rate, ohne einen „magischen“ Planner zu behaupten.

- **Selbststabilisierung**  
  **Mutations/Drift** liefert kontrollierte Variation; Replay + DreamCycle + Roter Faden stabilisieren → konstruktive Instabilität statt Chaos.

- **Headless-Betrieb**  
  systemd-Services/Timer, Health/Audits, DreamWorker mit Run-Lock, SceneGraph-Self-Checks; kein Desktop nötig.

### Schwächen (relativ zu Big-AI)

- **Kein Gigant-LLM**  
  Kreative Generierung und Weltwissen sind in Nischen gut, aber naturgemäß nicht auf Niveau großer Cloud-LLMs.

- **Begrenzter Kontext & Korpus**  
  Offline/on-prem bedeutet bewusst: kleinerer Wissenshorizont als Web-gestützte Systeme.

- **High-End-Video-Verständnis**  
  Frame-genaue Langzeit-Videoanalyse ist **nicht Ziel**; Fokus liegt auf komprimierten Vision-Tokens + Graph-Struktur, nicht auf Vollbild-LLMs.

- **Ökosystem-Größe**  
  Kleiner und kuratiert; dafür **klare, schlanke Schnittstellen** (Wrapper/Hooks/Add-ons) statt unüberschaubarem Plugin-Zoo.

---


<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__changelog_full.md
  Part:   2
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

## Quelle: `docs/changelog_v3_5patch2.md`

**Originaltitel:** ORÓMA – Changelog v3.5 Patch 2

**Datum:** 2025-09-xx  
**Fokus:** Stabilisierung, Rewards-Integration, Monitoring

## 🚀 Neue Features
- **Calculator ↔ Rewards-Log Integration:**
  - Ergebnisse aus `calculator_results` werden automatisch auch in `rewards_log` gespiegelt.
  - Rewards erscheinen im Learning-Dashboard (Qualitätstrend).

- **Selftest Dashboard Upgrade:**
  - Erweiterte Ansicht: Letzte Selftests + Scores direkt im UI.
  - Exportierbar als CSV.

- **AgentLoop Stabilisierung:**
  - Hook-Fehler werden abgefangen (kein Abbruch mehr).
  - Logging erweitert (mit Quelle: Snap, Calculator, Transfer).

## 🔧 Fixes / Änderungen
- `sql_manager.py` doppelte Blöcke entfernt → sauberes, einheitliches Schema.
- `ensure_schema()` prüft jetzt auch auf alle Patch-Tabellen.
- `calculator_ui.py` erweitert → Benutzer kann eigene Eingaben machen (interaktive Tasks).
- `oroma_graph.js` verbessert (Tooltips + Score-Anzeige).

## 📊 Erwartetes Ergebnis
- Rewards fließen jetzt **einheitlich** ins Lernsystem (SnapChains + Calculator + Transfer).
- Lernkurven werden **repräsentativer**, da Selftests mitgezählt werden.
- UI wird für den Benutzer **interaktiver** und **robuster**.

<a id="docs_history_changelog_final_v2_30_md"></a>

## Quelle: `docs/history_changelog_final_v2_30.md`

**Originaltitel:** ORÓMA v2.30 – Abschlussbericht (Final)

## 🎯 Überblick
ORÓMA v2.30 ist die **finale konsolidierte Version** der 2.x-Reihe.  
Alle Kernmodule, Blueprints, UI-Templates und Spiele sind integriert, stabilisiert und in einer einheitlichen Architektur verfügbar.  
Das System deckt jetzt die Phasen **„Erleben – Erinnern – Erklären – Visualisieren“** vollständig ab.

---

## 🧩 Core
- `snap.py`, `snaptoken.py`, `snappattern.py`, `snapchain.py`
- `regelarchiv.py`, `mutation.py`, `langzeitgedaechtnis.py`
- `circadian_controller.py` (Tag/Nacht-Modi)
- `episodic.py` (episodisches Gedächtnis: CRUD, Recall, Summaries, Pruning)
- `explain.py` (Why-Trace: Evidenzen, Scores, Episoden, Outcomes)
- `overlay.py` (MJPEG-Stream, Sensorfusion, 5 FPS)
- `sql_manager.py` (SQLite, Metriken, Vektorindex)
- `llm_runtime.py` (LLM-Chat mit GGUF-Modellen)
- `vector_migration.py`, `degirum_export.py`, `model_import.py`
- `__init__.py` (saubere Core-Initialisierung)

---

## 🖥️ Flask-UI & Blueprints
- `flask_ui.py` (Zentrale App, Auth, Logging, Overlay, Blueprints)
- Spiele:
  - `snake_ui.py`, `pong_ui.py` (Canvas)
  - `flappy_ui.py`, `ctf_ui.py`, `hideseek_ui.py`, `memory_ui.py` (iFrame)
- `episodic_ui.py` (Episoden anlegen + Ähnlichkeitssuche)
- `why_ui.py` (Explainability – Entscheidungsanalyse)
- `synapses_ui.py` (Synapsen-Graph API für vis-network)

---

## 📑 Templates
- `base.html` (Layout, Navbar, Bootstrap 5, Chart.js)
- `index.html` (Dashboard Startseite mit Health, Overlay, Lernkurve)
- `games.html` (Tabs für Snake, Pong, Flappy, CTF, Hide & Seek, Memory)
- `models.html` (LLM, Vision, Audio Modelle laden)
- `learning.html` (Lernkurve mit Chart.js + Rohdaten)
- `control.html` (Dream-/Night-Mode + Overlay)
- `why.html` (Explainability – Entscheidungshistorie)
- `episodic.html` (Episoden CRUD + Similarity)
- `synapses.html` (vis-network Graph)

---

## 📂 Static
- `style.css` (Unified Dark Theme, responsive)
- `scripts.js` (API-Helper, Token-Handling, Spiele, Charts, Export/Import, Controls)

---

## ⚙️ Deployment & Utilities
- `run_oroma.py` (Startskript für Flask-UI)
- `deploy_all.py`, `rollback_deploy.sh` (Deployment-Management)
- Logging nach `logs/ui.log`

---

## 🎮 Mini-Spiele integriert
- Snake (Canvas, Echtzeitsteuerung per Pfeiltasten)
- Pong (Canvas, Buttonsteuerung)
- Flappy (iFrame)
- CTF (iFrame)
- Hide & Seek (iFrame mit ASCII/Canvas Logik)
- Memory/Maze (iFrame)

---

## 🔍 Features
- **Overlay:** Echtzeit-MJPEG (Vision, Audio, Text)
- **Auth:** Token-basierte Authentifizierung + Cookie Auto-Login
- **Export/Import:** SnapChain/Regelarchiv als ZIP (Policy: Delay + Quality)
- **Learning:** Chart.js Lernkurve + API `/api/learning/curve`
- **Episodic Memory:** Speicherung, Similarity-Recall, Pruning, Summaries
- **Explainability:** Why-Traces + Entscheidungen im UI
- **Synapsen-Graph:** vis-network für Episoden, Events, Snaps

---

## 🏁 Status
- **Version:** v2.30 (final)
- **Datum:** September 2025
- **Stabilität:** produktiv nutzbar, alle Kernfunktionen integriert
- **Nächste Schritte (optional, v3.0):**
  - Hybrid-Snaps (Snap+LLM-Token)
  - Wissensimporte (Text/PDF)
  - Retrieval-Augmented-Antworten (RAG)
  - Deep Tool-Integration

---

✅ Mit ORÓMA v2.30 ist die 2.x-Reihe abgeschlossen.  
Die Basis für die nächste Entwicklungsstufe (v3.0 „Student/Gelehrter“) ist gelegt.

<a id="docs_history_changelog_final_v3_0_md"></a>

## Quelle: `docs/history_changelog_final_v3_0.md`

📑 CHANGELOG_FINAL_V3.0.md

ORÓMA v3.0 – Final

📌 Überblick
	•	Neue Hauptversion, Übergang von v2.30.
	•	Fokus: Circadian Controller, Replay-System, Export/Import-Pipeline, Model Registry, Dashboard-Erweiterungen.
	•	Alle Module jetzt unter /opt/ai/oroma/ (ohne Versions-Unterordner).

⸻

🚀 Neue Features

🌙 Circadian Controller
	•	Automatischer Wechsel Day/Dream-Modus.
	•	Delay-Steuerung: dark → +30min → Dream, light → sofort → Day.
	•	ENV-Variablen:
	•	OROMA_NIGHTMODE_LIGHT_THRESHOLD
	•	OROMA_NIGHTMODE_DELAY_MINUTES

📼 Replay-System
	•	SnapChain-Wiedergabe mit Pause/Resume/Stop.
	•	Export-Funktion (z. B. für Analyse oder Archiv).
	•	CLI-Tool (replay.py) + systemd-Service/Timer für automatisierte Replays.

📤 Export/Import-Pipeline
	•	Neue Komponente export_gate.py.
	•	Export-Policy:
	•	Mindestalter: 30 Tage (konfigurierbar via OROMA_EXPORT_DELAY_DAYS).
	•	Mindestqualität: 0.7 (via OROMA_EXPORT_QUALITY_THRESHOLD).
	•	Nicht-destruktiv: SnapChains/Modelle werden nur deaktiviert, niemals gelöscht.
	•	Export als portable .tar-Bundles (mit Hailo/DeGirum-Awareness).
	•	Import: ZIP → Merge mit Feature-Hash-Dedupe.

📚 Model Registry
	•	SQLite-gestützt (data/oroma.db).
	•	Tabellen models und items.
	•	Speicherung von Modellinformationen + Quality-History.
	•	Einheitliches UI für Auswahl & Aktivierung.

🖥 Dashboard & UI
	•	Neue Seiten:
	•	/replay – SnapChain-Wiedergabe
	•	/registry – Model-Registry
	•	/models – ASR/LLM-Modellauswahl
	•	/import – Upload-Interface für Exporte
	•	Learning-Tab: CSV-Export + Canvas-Chart (Qualitätsentwicklung).
	•	Video-Seite: Backend-Switcher (onnx/hailo/degirum).
	•	PiCar-Seite: Safety-Layer (Deadman, Soft-Limits, Offset).
	•	Authentifizierung per Token (ENV OROMA_UI_TOKEN).
	•	Optional TLS via selbstsigniertem Zertifikat (Helper).
	•	Rate-Limiting integriert.

🎮 Mini-Programme
	•	Integration von Snake, Pong, Flappy, Memory, TicTacToe, Connect4, Hide & Seek, Memory Maze 2033.
	•	➕ „ORÓMA vs ORÓMA“ (Selbstspiel).
	•	SnapChain-Logging: f_game, f_act, f_st, f_ex.
	•	Übersichtstab + Detail-UI je Spiel.

🎤 ASR & 🎙 LLM
	•	ASR via Whisper (tiny/small).
	•	LiveRunner über AudioWrapper.
	•	LLM via llama.cpp (GGUF).
	•	Chat-UI mit Modellwahl.

🛠 Infrastruktur
	•	assemble_project.sh überarbeitet (VENV, Abhängigkeiten).
	•	Systemd-Units:
	•	oroma.service (Hauptdienst)
	•	oroma-replay.service + Timer
	•	oroma-monthly-archive.service + Timer
	•	Logging: konsistent in /opt/ai/oroma/logs/.
	•	Tests: TLS-Boot, Rate-Limit, Model-Switching, Phase E2E.

⸻

🔄 Änderungen
	•	Projektstruktur konsolidiert: nur noch /opt/ai/oroma/.
	•	.env: zentrale Steuerung aller Variablen (UI, Export, AgentLoop, Circadian, Vision, ASR, Logging).
	•	run_oroma.py: Startpunkt für Flask-UI, AgentLoop, DreamWorker.
	•	ui/flask_ui.py: überarbeitet mit zentraler Blueprint-Registrierung.
	•	style.css: Dark-Theme für Bootstrap 5.
	•	mini_programs/__init__.py: Registry-API für Spiele.

⸻

⚠️ Entfernt / Aufgeräumt
	•	Alte Versionsordner (/v2.30, /v2.11, …) → entfällt.
	•	Doppelte oder veraltete UI-Routen.
	•	Harte Pfade → ersetzt durch ENV-konfigurierbare Pfade.

⸻

✅ Status v3.0
	•	Stabil & produktiv.
	•	Alle Core-Features lauffähig.
	•	Export/Import getestet mit Dedupe.
	•	Dashboard voll funktionsfähig.

<a id="docs_history_oroma_changelog_md"></a>

## Quelle: `docs/history_oroma_changelog.md`

**Originaltitel:** ORÓMA – Changelog

## v3.0 – Student / Gelehrter (2025-09-16)

**Hauptziele:**  
Snap+Token-Fusion, LLM-Integration, Headless-Optimierung.  
ORÓMA entwickelt sich von der agentischen Intelligenz (v2.30) hin zum „Studenten/Gelehrten“ mit erweitertem Wissenstransfer.

### 🔑 Neue Funktionen
- **Snap+Token-Fusion**:  
  Vereinigung der numerischen Snap-Vektoren mit symbolischen Tokens → einheitliches Gedächtnisformat.  
- **LLM-Integration (lokal/hybrid/remote):**  
  - Lokale Modelle (llama.cpp GGUF)  
  - Hybrid-Policy: lokal + Remote-Fallback (konfigurierbar)  
  - Remote-Layer optional via OpenAI/Anthropic, über Policy gesteuert  
- **RAG-Bridge + Tool-Use:**  
  - Import von Text- und Buchdateien in die Wissensbasis (`/knowledge` UI-Route)  
  - Speicherung in SQLite + FAISS Vektor-Index  
  - Fakteneinspeisung in SnapChains, nutzbar in Episoden  
- **Headless-first Design:**  
  - VisionWrapper jetzt ohne Qt/X11/Wayland – Picamera2/OpenCV/GStreamer laufen direkt auf Bash-Servern  
  - UI bleibt reines HTML/JS (kein GUI-Framework nötig)  
  - Alle Tools CLI-/systemd-kompatibel  

### 🛠 Verbesserungen
- VisionWrapper erweitert um **Picamera2-Support**, optimiert für Raspberry Pi Camera v2/v3.  
- Konsistente `.env`-Struktur: neue Variablen `OROMA_VISION_*`, Backend `opencv | gstreamer | picamera2`.  
- Erweiterte Logging-Ausgaben (INFO/DEBUG) für Headless-Diagnose.  
- Verbesserte Systemd-Integration (`oroma.service` + Logfiles in `/opt/ai/oroma/v3.0/logs`).  

### 🧪 Testplan
- **Memory-Spiel** erzeugt SnapChains → Lernkurve sichtbar.  
- **Knowledge-Import** (`/knowledge`) + LLM-Abfragen zeigen direkte RAG-Nutzung.  
- **VisionWrapper (Picamera2)** erzeugt Schnappschüsse + Feature-Vektoren → Learning-Curve-Tab aktualisiert sich.  
- **AgentLoop** bleibt stabil mit `dt=0.25s`.  
- Flask-UI über Netzwerk erreichbar (`0.0.0.0:8080`).  

---

## v2.30 – Agentisches Lernen & Erklärbarkeit (2025-08)

### 🔑 Neue Funktionen
- Reward-System mit Wrapper-Adapter + Mini-Game Rewards  
- Predictor (Top-K nächste Snaps, Hit@K-Metrik)  
- Curiosity/Surprise (intrinsische Motivation)  
- Episodisches Gedächtnis (Vektor-Index für ähnliche Erlebnisse)  
- Explainability (`why_decision()`) → zeigt beteiligte Chains/Regeln  
- UI: Learning-Curve (Chart.js), Episoden-Browser, Why-Tab  

### 🛠 Verbesserungen
- Headless-optimierte Weboberfläche (reines HTML/JS, kein GUI-Framework)  
- Konsistentes Logging (Reward, Predictor, Episoden)  

---

## v2.20 – Spatio-Temporal + Diagnose & Auto-Tuning (2025-07)

- Snap-Erweiterungen: Zeit-/Raum-Kontext  
- Diagnostics: Coverage, Novelty-Rate, Confidence  
- Auto-Tuning: Priority Replay, adaptives Pruning  
- UI-Badges für Knowledge-Gaps  
- CLI-/Bash-Diagnose-Tools  

---

## v2.11 – Stabil & Komplett (Sommer 2025; Quelle nannte 2025-06, korrigiert → Projektstart Juli 2025)

- Snaps / SnapTokens / SnapChains (Vision, Audio, Text)  
- Dream/Replay mit 30-Tage-Exportpolicy  
- Circadian Controller (Day/Dream Umschaltung via Lichtsensor)  
- Wrapper-System (Vision, Audio, Text, PiCar, TTS, Hailo)  
- UI (Dashboard, Registry, Models, Lernkurve-Stub)  
- Mini-Programme: TicTacToe, Connect4, Snake, Pong, Memory, Maze, Hide & Seek  
- Headless-optimierte Kamera (Picamera2/OpenCV/GStreamer)

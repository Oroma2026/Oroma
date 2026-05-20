# ORÓMA – Milestone: Stabilität & Autonomie-Härtung (v3.7.3) – 2026-02-08

**Baseline-ZIP:** `oroma_20260208_154755_with_db.zip`  
**Stand (Berlin):** 2026-02-08  
**Scope:** Core-Logik (AgentLoop/Day-Dream), DreamWorker, Curiosity, Curriculum, DB-Schema, systemd/Orchestrator, Logs & Doku-Abgleich.

---
## 1) Snapshot des Repos

- Dateien gesamt: **1564**
- Python-Dateien: **250**
- Markdown-Doku: **225** (`docs/**.md`)
- Datenbanken: `data/oroma.db`, `data/stats.db`, `data/knowledge.db`

---
## 2) “Planned vs Implemented” – Abgleich mit `docs/` und `docs/history/`

**Doku-Quellen (maßgeblich):**
- `docs/current.md` (Quick-Entry, Orchestrator-Standard, Day/Dream, UI)
- `docs/architecture_part01-05.md` (Architektur: Snap/SnapChains, DreamWorker als Motor, Graph-Ableitungen)
- `docs/history/**` (Archiv: ROADMAP, Konzeption v3.7.2/v3.7.3, Changelogs)

**Abgleich – Status:**
- ✅ **Orchestrator-Modus als Standard** (entspricht `docs/current.md`): `.use_orchestrator` vorhanden, systemd Units mit `ConditionPathExists=!.../.use_orchestrator`.
- ✅ **Day/Dream Modell** (Circadian + DreamWorker): Core-Module vorhanden, DreamWorker als Oneshot/Timer vorgesehen (`systemd/oroma-dream.*`).
- ✅ **Persistentes episodisches Gedächtnis / Event-Tabellen** (in `oroma.db` vorhanden: `episodes`, `episode_events`, `episodic_metrics`).
- ✅ **Curriculum-Subsystem**: `core/curriculum.py` + Hook (`core/curriculum_hook.py`) + UI/Logs. In Live-Logs sichtbar (L1 Tasks).
- ⚠️ **Curiosity**: Modul + DB-Log vorhanden (`core/curiosity.py`, Tabelle `curiosity_log`, API Endpoint). **Im Core-Loop (Decision/AgentLoop/Dream) aktuell nicht als Antrieb verdrahtet** (eher “standalone logging”). → entspricht eher “bereitgestellt” als “voll integriert”.

---
## 3) Core-Logik Check (funktional)

### 3.1 AgentLoop (Runtime-Kern)
- ✅ Hook-Pipeline vorhanden (Curriculum-Hook wird automatisch registriert)
- ✅ DeviceHub-Integration & Start erfolgt im Bootpfad (`run_oroma.py`)

### 3.2 DreamWorker (Offline Learning / Konsolidierung)
- ✅ DreamWorker läuft und erzeugt/komprimiert MetaSnaps (sichtbar in `logs/dream.out.log`)
- ✅ arbeitet DB-safe über `core/sql_manager.get_conn()` (Context-Manager → Close)
- ✅ enthält Replay/Forgetting/Research/Missions/PTZ-Policy Ableitungen (entspricht Architektur-Texten: Replay+Selektion/Konsolidierung)

**Aber: derzeitige Abweichung (wichtig):**
- ⚠️ `logs/dream.err.log` zeigt wiederkehrend: `sqlite3.OperationalError: no such column: created_at`  
  Ursache: `core/episodic.py` versucht Index auf `episodic_metrics(key,created_at)`, die Tabelle hat aber `id, episode_id, ts, key, value`.  
  Folge: Episodic ensure_schema wird im DreamWorker per Guard **unterdrückt** (Dream läuft weiter), aber das Episodic-Metrics-Subsystem ist nicht “sauber grün”.

**Empfohlener Fix (minimal-invasiv):**
- entweder: `created_at` **additiv** als INTEGER-Spalte (backfill mit `ts`) + Index anlegen  
- oder: Index auf `(key, ts)` umstellen, wenn `created_at` fehlt (Schema-Detection).

### 3.3 Curiosity
- ✅ `ensure_schema()` für `curiosity_log`
- ✅ `curiosity_score()` liefert Signal + Bands (Entropie/Prediction-Error etc.)
- ✅ UI/API: `POST /api/curiosity/log`
- ⚠️ In DB: `curiosity_log` Count = **0** (Sample: leer) → aktuell keine aktive Nutzung im Autonomie-Kern.

### 3.4 Curriculum
- ✅ `curriculum_state` Tabelle vorhanden (Count: n/a)
- ✅ Curriculum läuft im Runtime-Log (Mathe-Fill Aufgaben; Correct/Incorrect; SelfAssessment/TransferSnap Persistenz)

---
## 4) DB-Schema-Status (oroma.db)

- Tabellen: **34** (u. a. `snapchains`, `meta_snaps`, `policy_rules`, `episodes`, `episode_events`, `episodic_metrics`, `curiosity_log`)
- Counts (Snapshot):
  - `snapchains`: 1000
  - `meta_snaps`: 1000
  - `episode_events`: 1000
  - `episodic_metrics`: 0
  - `policy_rules`: 1000
  - `knowledge_gaps`: 1000
  - `missions`: 0

**Wichtig: Episode-Events Migration ist ok:**
- `episode_events` enthält: `id, episode_id, ts, event_type, ref_table, ref_id, meta_json, idx, state_hash, centroid, reward, payload` (inkl. `state_hash`, `centroid`, `reward`, `payload`) ✅

**Offener Punkt:**
- `episodic_metrics` enthält aktuell: `id, episode_id, ts, key, value` → kein `created_at` ⚠️

---
## 5) Logs – Gesundheitszustand (Snapshot)

- DB-Locks gefunden: **1** Treffer
- Warmup-Log gefunden: **0** Treffer
- Watchdog-Log/Audit gefunden: **0** Treffer
- Episodic created_at Fehler: **20** Treffer

**Top-Issue (aktuell):**
- `episodic_metrics.created_at` Index-Fehler (siehe Abschnitt 3.2)

---
## 6) Milestone-Definition (was gilt als “erreicht”)

✅ **Erreicht:**
- Stabiler Headless Betrieb (systemd + Orchestrator)
- Day/Dream-Lifecycle läuft; DreamWorker produziert MetaSnaps
- DB-Writer-Kollisionen entschärft (WAL + write-serialization, im Snapshot keine Lock-Signaturen)
- DeviceHub: Warmup + Watchdog → Sensorik stabiler und diagnosefähig
- Curriculum läuft kontinuierlich (sichtbar im Runtime-Log)

⚠️ **Noch offen / nächste Mini-Milestone:**
- EpisodicMetrics Schema-Fix (`created_at` vs `ts`) → Dream err.log wieder “grün”
- Curiosity als echtes “Motivationssignal” in AgentLoop/DecisionEngine einspeisen (z. B. Task-Auswahl / Exploration-Trigger)

---
## 7) Konkrete Next Steps (minimal, produktiv)

1. **EpisodicMetrics-Migration** (additiv oder Index-Fallback) → eliminiert wiederkehrende Dream-Guard-Warnungen.
2. **Curiosity Integration “light”**: CuriosityScore aus aktuellen Observations (Curriculum/Perception) erzeugen und als:
   - Metric (`metrics`), oder
   - Reward-Modulation / Task-Selector Weight
   einspeisen (ohne neue Nebenwirkungen).
3. Optional: Audio-hostapi Warnung auf “once / throttled” (Log Hygiene).

---
**Unterschrift:** ORÓMA Milestone Generator (GPT-5.2 Thinking)  

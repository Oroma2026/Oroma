# ORÓMA Projektstruktur

**Projekt:** ORÓMA (Offline‑Realtime‑Organic‑Memory‑AI)  
**Dokument:** Repository‑ / Verzeichnislayout (Snapshot)  
**Zweck:** Bereitstellung einer stabilen, veröffentlichungsfähigen Übersicht über Codebasis und Laufzeit‑Ordner für Maintainer, Reviewer und akademische Leser.

> **Geltungsbereich (Scope)**
> Dieses Dokument beschreibt die Struktur, wie sie im Projekt‑Snapshot (Root‑Ordner) vorgefunden wurde. Laufzeit‑Ordner wie `data/`, `state/` und `logs/` sind instanzspezifisch und werden in öffentlichen Code‑Snapshots typischerweise ausgeschlossen.

---

## 1. Top‑Level‑Layout

- `run_oroma.py` – Haupt‑Entry‑Point; verbindet Flask‑UI, Agent‑Loop, DeviceHub und optionale Background‑Worker.
- `core/` – architektonischer Kern: Memory‑Primitives (Snap/SnapChain), Konsolidierung (Dream/Replay), Policy/Rules, DB‑Layer.
- `ui/` – Flask‑Blueprints und Templates für Dashboards und Control‑Panels.
- `wrappers/` – Backend‑Wrapper/Adapter (Vision/Audio/LLM/PTZ), optimiert für Headless‑Deployment.
- `tools/` – Offline‑Jobs, Maintenance‑Skripte, Diagnostik, Runner.
- `systemd/` – Service/Timer‑Units für Deployment und Orchestrator‑Modus.
- `docs/` – Architektur‑Doku, Spezifikationen und Betriebsnotizen.

Unterstützende / zusätzliche Verzeichnisse:
- `tests/` – automatisierte Tests.
- `mini_programs/` – kleine Standalone‑Utilities/Demos.
- `third_party/` – vendorte Abhängigkeiten (falls vorhanden).

Laufzeit‑ / instanzspezifische Verzeichnisse (normalerweise **nicht** als “Source” veröffentlicht):
- `data/` – lokale Datenbanken, Caches und persistierte Laufzeit‑Artefakte.
- `state/` – Live‑State‑Dateien (JSON, Caches, Orchestrator‑State, etc.).
- `logs/`, `log/` – Logdateien.
- `models/` – optionale lokale Modell‑Artefakte.
- `exports/`, `exports_out/`, `archives/`, `uploads/` – Import/Export und Runtime‑I/O.

---

## 2. Core (`core/`) – Schlüsselmodule

Die folgenden Dateien definieren die SnapChain‑Architektur und das Langzeitverhalten im Edge‑Betrieb:

### 2.1 Memory‑Primitives
- `core/snap.py` – atomare Beobachtungseinheit (“Snap”).
- `core/snapchain.py` – zeitliche Episodenstruktur (“SnapChain”).
- `core/snappattern.py` – Pattern‑/Centroid‑Logik für Ähnlichkeit und Konsolidierung.
- `core/snaptoken.py` – Token/Feature‑Repräsentationen, die über Subsysteme hinweg genutzt werden.
- `core/snap_indexer.py` – Indexierung und Retrieval‑Hilfen.

### 2.2 Konsolidierung / Replay
- `core/dream_worker.py` – Dream‑Phase‑Konsolidierung (Replay, Umstrukturierung, Kompression).
- `core/replay_system.py` – Replay‑Engine (Selektion, Ausführung, Buchführung).
- `core/replay_manager.py` – Replay‑Orchestrierung und exportfreundliche Controls.

### 2.3 Policy, Rules und Transfer
- `core/policy_engine.py` – Policy‑Learning / Anwendungsschicht.
- `core/universal_policy.py` – domänenagnostische Policy‑Schnittstelle (state_hash/action).
- `core/transfer_engine.py` – Transfer‑Mechanismen über Domänen/Subsysteme hinweg.

### 2.4 Laufzeitstabilität (DB + Device‑I/O)
- `core/sql_manager.py` – DB‑Schema/PRAGMA‑Kontrolle, Migrationen und stabile Read‑Paths.
- `core/db_writer.py` / `core/db_writer_client.py` – Single‑Writer‑Queue/IPC für stabile SQLite‑Writes.
- `core/device_hub.py` – zentraler Gerätezugriff (Kamera/Audio/Light) mit Auditierbarkeit.
- `core/camera_hub.py` – Camera‑Provider‑Abstraktion (V4L2/by‑id Stabilität, Injection‑Sources).
- `core/circadian_controller.py` – Day/Dream‑Switching‑Logik (z. B. lichtbasierte Planung).

---

## 3. UI (`ui/`) – Dashboards und Control Planes

`ui/` enthält Flask‑Blueprints für:
- System‑Health/Metriken‑Dashboards
- Learning/Kurven und History‑Views
- Replay‑Controls und Inspektion
- Video/PTZ‑Monitoring und Steuerung
- Games/Curriculum und Policy‑Experimente
- Import/Export und Model‑Selection‑Pages

Templates und Static Assets liegen unter `ui/templates/` und `ui/static/` (falls im Snapshot vorhanden).

---

## 4. Wrappers (`wrappers/`) – Backend‑Routing (Headless)

`wrappers/` enthält Adapter‑Schichten für:
- Vision‑Backends (OpenCV / Hailo / DeGirum / GStreamer‑Routing)
- Audio/ASR/TTS‑Schnittstellen
- PTZ‑Controller und Device‑Control‑Abstraktionen

Diese Wrapper erlauben ORÓMA im Headless‑Betrieb (keine X11/Qt/Wayland‑Abhängigkeiten) bei gleichzeitig austauschbaren Backends.

---

## 5. Tools (`tools/`) – Jobs, Runner, Diagnostik

`tools/` enthält Skripte für Operatoren und den Orchestrator, z. B.:
- tägliche/periodische Runner (Games, Konsolidierung, Metriken)
- Snapshot-/Export‑Hilfen
- DB‑Diagnostik, Audit‑ und Repair‑Tools
- Cache‑Refresher und Reporting

---

## 6. systemd (`systemd/`) – Services und Timer

`systemd/` enthält Unit‑Files für:
- den Haupt‑ORÓMA‑Service
- den Orchestrator‑Service (Job‑Scheduling)
- periodische Timer (Dream, Replay, Stats, Archive, etc.)

Im Orchestrator‑Modus können einzelne One‑Shot‑Units durch Condition‑Flags untergeordnet sein oder übersprungen werden.

---

## 7. Empfohlene Veröffentlichungs‑Trennung (Paper + Code)

Für akademische/öffentliche Distribution ist eine saubere Trennung:

- **Zenodo Preprint(s):** EN‑Whitepaper + DE‑Übersetzung (bereits veröffentlicht).
- **Zenodo Software Snapshot:** Source‑Only‑Archiv (ohne `data/`, `state/`, `logs/`, große `models/`).
- **Codeberg Repo:** “Living source” mit README, das auf die Zenodo‑DOIs verweist.

---

## 8. Quick‑Tree (Top‑Level)

```
run_oroma.py
core/
ui/
wrappers/
tools/
systemd/
docs/

# runtime / instance data (usually excluded from source snapshots)
data/
state/
logs/ , log/
models/
exports/ , exports_out/ , archives/ , uploads/

# support
mini_programs/
tests/
third_party/
```

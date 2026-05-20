# ORÓMA — DB Single Writer Architektur (Stufe C)

**Pfad (Vorschlag):** `docs/DB_SINGLE_WRITER.md`  
**Projekt:** ORÓMA v3.7.x+ (Orchestrator-/Dream-/Service-Stack)  
**Stand:** 2026-03-04  
**Status:** Implementierungsplan (Design-Doc) — *noch keine Code-Änderungen in diesem Dokument*  
**Zielgruppe:** ORÓMA Core-Entwicklung / Betrieb (Raspberry Pi 5/Edge, offline/headless)

---

## 0. Executive Summary

SQLite ist extrem robust, aber in einem System wie ORÓMA (mehrere Prozesse + Threads, Dream-Jobs, ObjectExtractor, UI/Service/Orchestrator) sind sporadische `database is locked`-Fehler bei konkurrierenden Writes oder langen Transaktionen **physikalisch** zu erwarten.

**Stufe C** löst das Problem an der Wurzel:  
➡️ **Nur ein einziger Prozess** (DB Writer Daemon) hat Schreibprivileg auf `oroma.db`.  
➡️ Alle anderen Komponenten routen **nur Schreiboperationen** über IPC (Unix Domain Socket).  
➡️ Reads bleiben lokal und profitieren maximal vom WAL-Modus (Reader blockieren Writer nicht).

Dieses Muster entspricht in der Praxis dem, was “Big Tech” in verschiedenen Formen intern nutzt (Serialisierung/Journal/Commit-Pipelines), nur eben angepasst auf einen Pi/Edge-Betrieb.

---

## 1. Problemdefinition & Zielkriterien

### 1.1 Ausgangslage (symptomatisch)
- ORÓMA nutzt mehrere parallele Einheiten: `oroma.service`, Orchestrator (jobs), DreamWorker (phases), UI-Endpoints, ggf. Tools/CLI.
- SQLite ist im WAL-Modus gut für parallele Reads + einen Writer, aber:
  - konkurrierende Writer-Transaktionen,
  - zu lange offene Write-Transaktionen,
  - oder unvollständig serialisierte Write-Pfade
  führen zu:
  - `sqlite3.OperationalError: database is locked`
  - ORÓMA flock timeouts (writer_lock)
  - sporadisch fehlschlagende Phasen (objectextractor, forgetting, rewardlog, metrics …)

### 1.2 Ziel (Root-Cause Eliminierung)
**Genau ein globaler Writer** für `oroma.db`:
- Kein anderer Prozess darf `INSERT/UPDATE/DELETE/BEGIN IMMEDIATE` gegen `oroma.db` ausführen.
- Alle Writes gehen über den DB Writer Daemon (IPC).
- Reads bleiben lokal (keine Routing-Pflicht für SELECT-only Pfade).

### 1.3 Abnahmekriterien (Definition of Done)
- `database is locked` geht im Normalbetrieb auf ≈0.
- Dream, Orchestrator, UI, Service laufen parallel ohne Write-Konflikte.
- Kein “Silent Failure”: jeder Drop/Reject/Timeout ist sichtbar geloggt.
- Writer ist robust bei Crash/Restart (MVP: Soft; optional: Hard persistente Queue).
- UI bleibt responsiv: high-prio Writes können low-prio Workloads überholen.

---

## 2. Architekturübersicht

### 2.1 Komponenten

1) **DB Writer Daemon (neuer Prozess)**
- Einziger Prozess mit Write-Rechten auf `oroma.db`
- IPC Interface: Unix Domain Socket (UDS)
- Sequenzielles Abarbeiten von Write-Requests (Queue + Prioritäten)

2) **DB Writer Client Library (im ORÓMA Code)**
- API: `exec`, `executemany`, `transaction`, `ping`
- Lazy Connect (öffnet Socket erst bei tatsächlichem Write)
- Timeouts & Retries
- Low-priority Drop-Policy (optional)

3) **Refactoring / Routing der Write-Pfade**
- Zuerst “schwere” Writer-Jobs oder “low impact” Writes (abhängig von Rollout)
- Ziel: Schrittweise Migration zu 100% routed writes

4) **systemd Integration**
- eigener Service `oroma-db-writer.service`
- startet vor anderen ORÓMA Units
- Restart-Policy + optional Watchdog/Health checks

---

## 3. IPC & Protokoll (robust, headless, offline)

### 3.1 IPC: Unix Domain Socket
**Socket Pfad (Vorschlag):** `/opt/ai/oroma/data/state/db_writer.sock`

Vorteile:
- lokal, keine Firewall/TCP-Themen
- geringer Overhead (schneller als TCP localhost)
- Zugriffskontrolle über Dateirechte (chmod/chown)

### 3.2 Protokoll: Length-prefixed JSON (empfohlen)
**Warum nicht JSON lines?**  
Parameter/Notes können Newlines enthalten; length-prefixed ist robust.

**Frame:**
- 4 Byte big-endian length
- danach UTF-8 JSON payload (genau `length` Bytes)

### 3.3 Request/Response Schema

**Request:**
- `id` (uuid)
- `op`: `"exec" | "executemany" | "transaction" | "ping"`
- `sql` (string) oder `stmts` (liste)
- `params` (liste) oder `params_list` (liste von listen)
- `db`: `"oroma.db"` (optional, default)
- `timeout_ms` (client-side)
- `priority`: `"low" | "normal" | "high"`
- `tag`: string (`"dream.objectextractor"`, `"ui.update"`, ...)
- `expect`: `"rowcount" | "lastrowid" | "none"`
- `ts`: unix time

**Response:**
- `id`
- `ok`
- `result` (rowcount/lastrowid/optional)
- `error`: `{code, message}` (traceback nur im server log)
- `server_ts`

### 3.4 Security / Safety-Line (kritisch)
**Keine SQL-Konkatenation. Kein “format” im Server.**  
Der Writer führt ausschließlich aus:
- `cursor.execute(sql, params)`
- `cursor.executemany(sql, params_list)`

**Optionale Schutzregel (MVP+):**
- Server warnt, wenn `sql` keine `?` enthält, aber params übergeben werden.
- Server warnt bei verdächtigen Patterns (`%s`, `{}`, `format(`), nur heuristisch.

> Hinweis: Die Umgebung ist lokal/offline, aber diese Policy verhindert auch unabsichtliche Bugs und inkonsistente SQL-Strings.

---

## 4. DB Writer Daemon — Design

### 4.1 Prozessmodell
Empfehlung (MVP):
- 1 accept-loop (blocking)
- 1 sequenzieller executor (ein Thread reicht, da Writes seriell)
- interne Queue (priority-aware)

Alternative:
- asyncio Server (ok), aber sequenzielle Execution bleibt Pflicht.

### 4.2 SQLite Konfiguration im Writer
- WAL an (wie im ORÓMA Standard)
- `busy_timeout` konsistent (60s)
- `synchronous=NORMAL` (wie euer Standard)
- `temp_store=MEMORY`
- ggf. `foreign_keys=ON` (wenn genutzt)

### 4.3 Batching & Commit Policy
- `exec`: autocommit (oder per connection default)
- `executemany`: commit am Ende
- heavy jobs (objectextractor): Blöcke (1000–5000 rows), commit je Block
- `transaction`: `BEGIN IMMEDIATE` → statements → `COMMIT`

### 4.4 Backpressure / Überlast (Queue)
- Max queue length (z.B. 50k)
- Wenn queue voll:
  - **low prio**: reject oder drop (visible log)
  - **normal/high**: weiter annehmen, ggf. blocken bis capacity

**Ziel:** UI/Status bleibt auch bei heavy Dream-Loads reaktionsfähig.

### 4.5 Crash Resilience
**MVP (Soft):**
- Queue ist in-memory
- Bei Daemon Restart: Requests verloren
- Clients erhalten Fehler und können retryn (Jobs sind meist idempotent)

**Optional (Hard):**
- Persistente Queue (append-only journal file)
- ack erst nach persist+commit
- replay beim Start
- Aufwand höher, aber maximal robust

---

## 5. Ergänzungen aus Praxis (wichtig für Stabilität)

### 5.1 Zombie-Socket Check (muss implementiert werden)
Nach einem Crash kann die Socket-Datei im FS liegen bleiben.

**Beim Start:**
- wenn `db_writer.sock` existiert:
  - `os.remove(sock)` (try/except)
  - dann neu binden

Ohne diesen Schritt kann der Writer nicht sauber starten.

### 5.2 Watchdog / Health Ping (empfohlen)
Der Writer ist ein SPOF (Single Point of Failure). Wenn er hängt, kann ORÓMA nicht schreiben/lernen.

**MVP pragmatisch:**
- `PING` Operation im Protokoll (op="ping")
- Orchestrator oder systemd timer pingt alle 30s
- bei failure: sichtbarer log + optional restart

**systemd Option:**
- `Restart=always`
- optional `WatchdogSec=` + `sd_notify` (nur wenn wir das explizit implementieren)

### 5.3 Lazy Connect im Client (empfohlen)
Client öffnet den Socket **erst beim ersten Write**:
- vermeidet Start-Reihenfolge-Probleme
- spart Ressourcen
- reduziert Fehler beim Boot

---

## 6. Client Library — API & Verhalten

### 6.1 Minimal API
- `dbw_exec(sql, params=None, tag="", priority="normal", expect="rowcount")`
- `dbw_executemany(sql, params_list, tag="", priority="normal")`
- `dbw_transaction(stmts, tag="", priority="normal")`
- `dbw_ping(timeout_ms=500)`

### 6.2 Timeouts & Retries
- Client timeout (UI kleiner, Dream größer)
- Bei `ENOENT` / `ECONNREFUSED`:
  - visible log
  - **low prio:** drop
  - **normal/high:** retry N times + backoff

Optionaler Fallback (nur wenn ENV erlaubt):
- `OROMA_DBW_CLIENT_FALLBACK_LOCAL=0|1`
- Default: **0** (damit der Single Writer invariant bleibt)

### 6.3 Security: Socket permissions
- UDS file mode `660`
- group `oroma` (oder euer Standard)
- nur lokale Nutzer/Services dürfen schreiben

---

## 7. Migration Strategy (minimal risk, maximal Stabilität)

### 7.1 Phase 0 — Infrastruktur
- Writer daemon implementieren
- Client library implementieren
- systemd unit hinzufügen
- writer starten + ping test
- noch keine produktiven writes routen

### 7.2 Phase 1 — Low impact writes (sicherer Einstieg)
- metrics (best effort)
- rewardlog (best effort)
Ziel: IPC Stabilität verifizieren ohne Risiko.

### 7.3 Phase 2 — Heavy writers (maximaler Effekt)
- objectextractor writes
- forgetting compactions
- scenegraph/objectgraph saves
Ziel: die lock-intensiven Pfade eliminieren.

### 7.4 Phase 3 — Vollständige Umschaltung
- alle `INSERT/UPDATE/DELETE` Pfade routen
- optional “guard”: lokale write calls aktiv verbieten (ENV gated), um Regression zu verhindern

### 7.5 Phase 4 — Lock Simplification (nur mit Messdaten)
- flock/RLock bleibt als Safety (empfohlen)
- Reduktion erst nach 1–2 Wochen Datenlage

---

## 8. Konkrete Touchpoints (werden beim Implementieren gegen ZIP validiert)

**Typische Kandidaten:**
- `core/sql_manager.py`
  - Write-Proxies (db_writer client)
  - Optional `conn_cm(write=True)` route statt local transaction
- `core/dream_worker.py`
  - objectextractor writes → db_writer (high impact)
  - metrics/rewardlog → db_writer (low prio)
- `core/scenegraph_store.py` / objectgraph pipeline
- `tools/oroma_orchestrator.py` (jobs, policy writes)
- UI endpoints, die schreiben (falls vorhanden)

> WICHTIG: Keine Spekulation in Code. Beim Umsetzen wird jede Call-Site in der ZIP identifiziert und sauber, minimal-invasiv umgestellt.

---

## 9. Observability & Debug

### 9.1 Logs
- `logs/db_writer.out.log`
- `logs/db_writer.err.log`

### 9.2 Metriken (optional, aber empfohlen)
- `dbw:queue_len`
- `dbw:req_per_min`
- `dbw:avg_latency_ms`
- `dbw:errors_per_hour`
- `dbw:dropped_lowprio`

### 9.3 Debug Commands
- `ls -l /opt/ai/oroma/data/state/db_writer.sock`
- `journalctl -u oroma-db-writer -n 200 --no-pager`
- `grep -R "database is locked" -n /opt/ai/oroma/logs`
- `grep -R "db_writer" -n /opt/ai/oroma/logs | tail`

---

## 10. Failure Modes & Handling

### 10.1 Writer down
- low prio: drop (visible)
- normal/high: retry with backoff
- optional: fail fast to surface systemic issue

### 10.2 Writer backlog
- backpressure: reject low prio
- log queue_len and reject counts

### 10.3 Slow statements
- log slow statements by hash/tag
- mitigation via batching and transaction scoping

---

## 11. systemd Integration

### 11.1 Service
`systemd/oroma-db-writer.service` (Vorschlag)
- `Before=oroma.service oroma-orchestrator.service oroma-dream.service`
- `Restart=always`
- `RestartSec=2`
- `WorkingDirectory=/opt/ai/oroma`

Optional:
- `WatchdogSec=30` (wenn implementiert)

### 11.2 Rechte
- Socket dir: `/opt/ai/oroma/data/state/` muss existieren
- permissions group-writable

---

## 12. ENV-Konfiguration (Vorschlag)

- `OROMA_DBW_ENABLE=1|0`
- `OROMA_DBW_SOCKET=/opt/ai/oroma/data/state/db_writer.sock`
- `OROMA_DBW_QUEUE_MAX=50000`
- `OROMA_DBW_DROP_LOWPRIO=1`
- `OROMA_DBW_LOG_SLOW_MS=250`
- `OROMA_DBW_CLIENT_TIMEOUT_MS_UI=2000`
- `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM=60000`
- `OROMA_DBW_CLIENT_FALLBACK_LOCAL=0|1` (Default 0)

---

## 13. Erwarteter Effekt

- `database is locked` verschwindet im Normalbetrieb nahezu vollständig.
- Heavy Dream-Jobs (Synapsen, ObjectExtractor) können dauerhaft laufen, ohne die UI “wegzudrücken”.
- WAL-Modus wird optimal genutzt: viele Reader, ein Writer.

---

## 14. Rollout Empfehlung (sicher)

1) Writer unit deployen (disabled routing)
2) Writer starten, Ping prüfen, Zombie-socket handling verifizieren
3) Phase 1: metrics/rewardlog routen
4) Beobachten 6–12h
5) Phase 2: objectextractor + heavy writes routen
6) 24h Stabilitätscheck (`database is locked` Count)
7) Phase 3: restliche Writes

---

## 15. Notizen / Naming (optional)
Begriffe, die zum ORÓMA Konzept passen:
- “Synaptic Funnel” (Writer als Trichter)
- “Memory Orchestrator” (Schreibprivileg zentralisiert)

---
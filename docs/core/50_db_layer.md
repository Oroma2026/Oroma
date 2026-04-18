# DB Layer – SQLManager + DBWriter (Single-Writer) / DB Layer – SQLManager + DBWriter (Single Writer)

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt den ORÓMA **DB-Layer** in Phase‑2‑Tiefe: `core/sql_manager.py` als stabilen SQLite‑Zugriff (Connections, PRAGMAs, Lock‑Retry, Schema‑Guards) und den **DBWriter** (`core/db_writer.py` + `core/db_writer_client.py`) als optionalen **Single‑Writer‑Funnel** (“Stufe C”), der parallele Writer-Spitzen entschärft und Ops‑Transparenz (Queue/Top‑Tags) liefert.

### Scope / Nicht-Ziele
- ✅ In scope: Connection‑Policy (WAL/busy_timeout), `writer_lock` (inproc + flock), DBWriter‑Protokoll (UNIX socket, framed JSON), DBWriter‑Allowlist, Strict‑Mode (no local writes), typische Write‑Routen (`insert_snapchain` via DBWriter).
- ❌ Out of scope: vollständiges DB‑Schema aller Tabellen, Performance‑Tuning auf Query‑Ebene, Backup‑Strategien.

---

## DE – Architekturübersicht (kurz)
Zwei Betriebsmodi:

1) **SQLite direkt (Default)**
- Reads/Writes laufen über `sql_manager.get_conn()` + `writer_lock(...)`
- Schutz durch:
  - `PRAGMA busy_timeout`
  - optional `journal_mode=WAL` + `synchronous=NORMAL`
  - kontrolliertes Retry‑Fenster bei Lock‑Fehlern

2) **Stufe C: DBWriter (optional, empfohlen bei Burst‑Writes)**
- Writes werden über `db_writer_client` an einen lokalen Daemon geroutet (UNIX socket)
- Vorteil:
  - nur **ein** Prozess commit’t Writes → weniger “database is locked”
  - zentrale Queue + Counters + Top‑Tags für Ops/Debug

---

## DE – `core/sql_manager.py`

### Connection‑Policy (`get_conn`)
`get_conn(db_path=None)` setzt produktive Defaults:

- `timeout` (connect): `OROMA_DB_TIMEOUT_SEC` (Default 60s)
- `PRAGMA busy_timeout`: `OROMA_DB_BUSY_TIMEOUT_MS` (Default 60000ms)
- WAL (Default **AN**): `OROMA_DB_WAL=1|0`
  - wenn an: `PRAGMA journal_mode=WAL` und `PRAGMA synchronous=NORMAL`

**Wichtig (Strict Local Writes):**
Wenn `OROMA_DBW_ENABLE=1` und `OROMA_DBW_STRICT_LOCAL_WRITES=1` (Default), dann werden lokale Connections für **verwaltete DBs** nur read‑only geöffnet (`mode=ro` via SQLite URI). Verwaltet sind per Basename:
- `oroma.db`, `stats.db`, `knowledge.db`, `registry.db`

Damit sind lokale Writes “hart” verhindert und können die Single‑Writer‑Architektur nicht unterlaufen.

### Writer‑Lock (`writer_lock`)
`writer_lock(kind, timeout_sec)` kombiniert:
1) In‑Process RLock (thread-sicher, re-entrant)
2) optional interprocess `flock` auf Lockfile (Default an), gesteuert über:
   - `OROMA_DB_WRITE_FLOCK=1|0`
   - `OROMA_DB_WRITELOCK_TIMEOUT_SEC` (Default 30s)

Bei längerem Warten wird (rate‑limitiert) geloggt; die Lockdatei enthält Debug‑Info (pid/kind/argv).

### Lock‑Retry Fenster
Viele Inserts (z.B. `insert_snapchain`, `insert_snap_index`, `insert_metric`) verwenden `_run_with_lock_retry(..., retry_sec)` mit:
- `OROMA_DB_LOCK_RETRY_SEC` (Default 60s)

### Schema Cache
Schema‑Ensure ist gegen Hot‑Path‑Spam geschützt:
- `OROMA_SCHEMA_CACHE=1|0` (Default True)

### DBWriter‑Routing in Writes (Beispiel `insert_snapchain`)
`insert_snapchain(data)` macht:
- wenn `OROMA_DBW_ENABLE=1`: nutzt `db_writer_client.exec_lastrowid(...)` mit Tag `sql_manager.insert_snapchain` (db=`oroma`)
- sonst: lokaler Write unter `writer_lock("insert_snapchain")`

**Hinweis:** Nicht alle Writes sind bereits über DBWriter geroutet (z.B. `insert_snap_index` ist lokal implementiert). Im Strict‑Mode darf das nur gegen nicht‑verwaltete DBs schreiben – sonst wird die Connection read‑only und der Write schlägt sichtbar fehl.

### Wichtige ENVs (sql_manager)
- `OROMA_DB_PATH` (falls Tools einen Pfad setzen)
- `OROMA_DB_TIMEOUT_SEC`
- `OROMA_DB_BUSY_TIMEOUT_MS`
- `OROMA_DB_WAL`
- `OROMA_DB_LOCK_RETRY_SEC`
- `OROMA_DB_WRITELOCK_TIMEOUT_SEC`
- `OROMA_DB_WRITE_FLOCK`
- `OROMA_SCHEMA_CACHE`
- `OROMA_DBW_ENABLE`
- `OROMA_DBW_STRICT_LOCAL_WRITES`
- `OROMA_DBW_CLIENT_TIMEOUT_MS_UI`
- `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM`
- `OROMA_DBW_TIMEOUT_MS` (wird in `insert_snapchain` genutzt)

---

## DE – DBWriter (`core/db_writer.py`)

### Rolle
Der DBWriter ist ein lokaler Daemon, der SQL‑Writes seriell ausführt. Er spricht ein simples framed JSON‑Protokoll über einen UNIX‑Domain‑Socket.

### Socket & Permissions
- Socket Path: `OROMA_DBW_SOCKET` (Default in `data/state/…`)
- Der Daemon entfernt “Zombie” sockets beim Start und setzt Permissions (User/Group/Mode) per ENV:
  - `OROMA_DBW_SOCKET_USER`, `OROMA_DBW_SOCKET_GROUP`, `OROMA_DBW_SOCKET_MODE`

### Allowlist & DB Mapping
Der Daemon akzeptiert nur DBs aus:
- `OROMA_DBW_ALLOW_DBS` (CSV)
Standard‑Mapping:
- `oroma` → `OROMA_DB_PATH` (default `<base>/data/oroma.db`)
- `stats` → `OROMA_STATS_DB_PATH`
- `knowledge` → `OROMA_KNOWLEDGE_DB_PATH`
- `registry` → `OROMA_REGISTRY_DB_PATH`

### Queue / Backpressure
- Queue‑Max: `OROMA_DBW_QUEUE_MAX` (Default 50000)
- Drop low priority (wenn Queue voll): `OROMA_DBW_DROP_LOWPRIO=1|0` (Default True)
- Slow log threshold: `OROMA_DBW_LOG_SLOW_MS` (Default 250ms)

Ops‑State (`op=state`) liefert:
- `queue_total`, `queue_by_db`
- `processed_total`, `processed_by_db`
- `processed_top_tags` (Top‑25)
- `dropped_low_total`, `dropped_low_by_db`
- `uptime_s`, `last_error`

### Ops / Protokoll (Server‑Seite)
Supported ops:
- `ping` → ok
- `state` → state payload
- `exec` → `conn.execute(sql, params)`; returns `rowcount`, `lastrowid`
- `executemany` → returns `rowcount`
- `transaction` → `BEGIN IMMEDIATE` … `COMMIT`

DB Connections werden mit WAL + busy_timeout + temp_store=MEMORY geöffnet.

---

## DE – DBWriter Client (`core/db_writer_client.py`)

### Protokoll (Client‑Seite)
- framed JSON: 4‑Byte big‑endian length prefix + UTF‑8 JSON
- per‑request `id` (UUID) → Response ID muss matchen
- **Thread‑Safety:** Client serialisiert socket I/O per Lock, um Frame‑Corruption zu vermeiden

### Timeouts
- Socket timeout: `timeout_ms / 1000`, min 0.2s
- Empfohlene Split‑Timeouts:
  - UI: `OROMA_DBW_CLIENT_TIMEOUT_MS_UI` (Default 2000)
  - Dream/Worker: `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM` (Default 60000)

### Client API (stabil)
- `enabled()`
- `ping(timeout_ms=500)`
- `state(timeout_ms=1000)`
- `exec_write(sql, params, tag, priority, timeout_ms, db="oroma")`
- `exec_lastrowid(...)`
- `executemany(...)`
- `transaction(stmts, ...)`

---

## DE – Failure Modes (sichtbar, keine stillen Writes)
- DBWriter Socket down → Client wirft Fehler / `enabled()` false
- Queue full → low prio kann gedroppt werden (`queue_full`)
- Strict Local Writes aktiv → lokale Writes auf managed DBs schlagen sichtbar fehl (read‑only)
- Lock contention ohne DBWriter → `_run_with_lock_retry` versucht bis `OROMA_DB_LOCK_RETRY_SEC`

---

## DE – Bezug zum Code
- Relevante Dateien:
  - `core/sql_manager.py`
  - `core/db_writer.py`
  - `core/db_writer_client.py`
- Verwandte Core‑Dokus:
  - `docs/core/90_publication.md`
  - `docs/core/24_snap_indexer.md` (Index‑Writes)
  - `docs/core/22_snapchain.md` (SnapChain Inserts)

---

## EN

### Purpose
This document describes ORÓMA’s **DB layer** in phase‑2 depth: `core/sql_manager.py` as the stable SQLite access layer (connections, PRAGMAs, lock retry, schema guards) and the optional **DBWriter** (`core/db_writer.py` + `core/db_writer_client.py`) as a **single‑writer funnel** (“Stage C”) that reduces “database is locked” during burst writes and provides ops visibility (queue/top tags).

### Scope / Non-goals
- ✅ In scope: connection policy (WAL/busy_timeout), `writer_lock` (in‑proc + flock), DBWriter protocol (UNIX socket, framed JSON), DB allowlist, strict mode (no local writes), typical write routing (`insert_snapchain` via DBWriter).
- ❌ Out of scope: full schema of all tables, query-level tuning, backup strategy.

---

## EN – Architecture overview
Two modes:

1) **Direct SQLite (default)**
- reads/writes via `sql_manager.get_conn()` + `writer_lock(...)`
- protected by `busy_timeout`, optional WAL, and lock‑retry windows

2) **Stage C: DBWriter (optional)**
- writes routed through a local daemon over a UNIX socket
- benefits:
  - only **one** committer process → fewer write collisions
  - central queue + counters + top tags for ops/debug

---

## EN – `core/sql_manager.py`

### Connection policy (`get_conn`)
- connect timeout: `OROMA_DB_TIMEOUT_SEC` (default 60s)
- `PRAGMA busy_timeout`: `OROMA_DB_BUSY_TIMEOUT_MS` (default 60000ms)
- WAL (default **ON**): `OROMA_DB_WAL=1|0`
  - if on: `journal_mode=WAL`, `synchronous=NORMAL`

**Strict local writes:**
When `OROMA_DBW_ENABLE=1` and `OROMA_DBW_STRICT_LOCAL_WRITES=1` (default), local connections to **managed DBs** are opened read‑only (`mode=ro` URI). Managed DBs are detected by basename:
- `oroma.db`, `stats.db`, `knowledge.db`, `registry.db`

This prevents local writes from bypassing the single-writer architecture.

### Writer lock (`writer_lock`)
Combines:
1) in‑process RLock
2) optional interprocess `flock` lockfile (default enabled)
Controlled by:
- `OROMA_DB_WRITE_FLOCK`
- `OROMA_DB_WRITELOCK_TIMEOUT_SEC`

### Lock retry window
Hot write paths use `_run_with_lock_retry(..., retry_sec)` with:
- `OROMA_DB_LOCK_RETRY_SEC` (default 60s)

### Schema cache
- `OROMA_SCHEMA_CACHE` (default true)

### DBWriter routing example (`insert_snapchain`)
- if DBWriter enabled: `db_writer_client.exec_lastrowid(..., tag="sql_manager.insert_snapchain", db="oroma")`
- else: local write under `writer_lock("insert_snapchain")`

Not all writes are routed yet (e.g. `insert_snap_index` is local). Under strict mode, local writes to managed DBs will fail visibly as read‑only.

### Key env vars (sql_manager)
`OROMA_DB_TIMEOUT_SEC`, `OROMA_DB_BUSY_TIMEOUT_MS`, `OROMA_DB_WAL`, `OROMA_DB_LOCK_RETRY_SEC`,  
`OROMA_DB_WRITE_FLOCK`, `OROMA_DB_WRITELOCK_TIMEOUT_SEC`, `OROMA_SCHEMA_CACHE`,  
`OROMA_DBW_ENABLE`, `OROMA_DBW_STRICT_LOCAL_WRITES`, `OROMA_DBW_CLIENT_TIMEOUT_MS_UI`, `OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM`, `OROMA_DBW_TIMEOUT_MS`.

---

## EN – DBWriter (`core/db_writer.py`)

### Role
Local daemon executing SQL writes serially over a framed JSON protocol on a UNIX domain socket.

### Socket & permissions
- `OROMA_DBW_SOCKET` (path)
- permission controls: `OROMA_DBW_SOCKET_USER`, `OROMA_DBW_SOCKET_GROUP`, `OROMA_DBW_SOCKET_MODE`

### Allowlist & DB mapping
- `OROMA_DBW_ALLOW_DBS` (CSV)
- standard db paths: `OROMA_DB_PATH`, `OROMA_STATS_DB_PATH`, `OROMA_KNOWLEDGE_DB_PATH`, `OROMA_REGISTRY_DB_PATH`

### Queue / backpressure
- `OROMA_DBW_QUEUE_MAX` (default 50000)
- `OROMA_DBW_DROP_LOWPRIO` (default true)
- `OROMA_DBW_LOG_SLOW_MS` (default 250ms)
- `op=state` exposes queue and throughput counters + top tags.

### Supported ops
- `ping`, `state`, `exec`, `executemany`, `transaction` (BEGIN IMMEDIATE … COMMIT)

---

## EN – Client (`core/db_writer_client.py`)

### Protocol
- 4‑byte big‑endian length prefix + UTF‑8 JSON
- request UUID id → response id must match
- thread‑safe socket I/O via a lock (prevents frame corruption)

### Timeouts
- socket timeout derives from `timeout_ms` (min 0.2s)
- separate UI vs worker timeouts via env.

### Client API
`enabled`, `ping`, `state`, `exec_write`, `exec_lastrowid`, `executemany`, `transaction`.

---

## EN – Failure modes (no silent writes)
- socket down → errors / `enabled()` false
- queue full → low prio may be dropped (`queue_full`)
- strict local writes → local writes to managed DBs fail visibly (read‑only)
- lock contention without DBWriter → `_run_with_lock_retry` up to `OROMA_DB_LOCK_RETRY_SEC`

---

## EN – Code mapping
- `core/sql_manager.py`
- `core/db_writer.py`
- `core/db_writer_client.py`

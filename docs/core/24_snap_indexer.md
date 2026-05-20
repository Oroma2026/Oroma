# SnapIndexer – MetaSnap → `snap_index` Upsert / MetaSnap → `snap_index` Upsert

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt `core/snap_indexer.py`: eine fokussierte Hilfsschicht, die **MetaSnaps** deterministisch in eine flache Index-Tabelle **`snap_index`** schreibt (Upsert via `ON CONFLICT(fingerprint)`). Ziel ist ein **stabiler Fingerprint-Key** für schnelle Lookups/Explainability, ohne große SnapChain-Blobs scannen zu müssen. Das Modul ist bewusst **headless** und **SQLite-first**.

### Scope / Nicht-Ziele
- ✅ In scope: Fingerprint-Bildung (`fingerprint_meta`), Payload-Strategie (`minimal/full`), robuste Spalten-Erkennung (`PRAGMA table_info`), Upsert-Insert (`index_meta_snap`), ENV-Steuerung.
- ❌ Out of scope: Indexieren kompletter SnapChains, Forgetting/Pruning, ML/Vision/Audio-Backends, DBWriter-IPC (hier wird eine offene Connection erwartet).

### Begriffe
- **MetaSnap:** kompakte Aggregationseinheit (siehe `14_meta_snap.md`).
- **snap_index:** flache Index-Tabelle mit `fingerprint` als UNIQUE-Key.
- **Fingerprint:** deterministischer Dedupe-Key (hier: `"meta:" + sha256(canon_json)`).
- **Payload mode:** Strategie, wie viel Meta-Info in `payload` gespeichert wird.

### Architekturrolle
Konzeptueller Fluss:
**Dream/Compression/Transfer → MetaSnap → SnapIndexer (`snap_index`) → UI/Analyse/Explainability**

`SnapIndexer` ist absichtlich klein: Er dient als **Index-Brücke** zwischen “komprimierten/abgeleiteten” Meta-Objekten und schnellen UI/Analyse-Lookups.

### Public API (stabil)
- `fingerprint_meta(label: str, sources: list) -> str`  
  → deterministischer Fingerprint aus kanonischem JSON (kind/label/sources).
- `index_meta_snap(conn, meta_id, label, score, sources, ts=None, source="dream:meta", privacy_tier="local") -> str`  
  → schreibt/upsertet einen Index-Record und gibt den Fingerprint zurück.

### Fingerprint (Dedupe-Key)
`fingerprint_meta(label, sources)`:
- baut kanonisches JSON:
  - `{"kind":"meta_snap","label":..., "sources":[...]}` (sort_keys, compact separators)
- hash: `sha256(json_bytes)`
- prefix: `"meta:"`

**Wichtig:** Dedupe/Idempotenz basiert auf **label+sources**, nicht auf dem Payload. Auch im `minimal`-Modus bleibt der Fingerprint gleich.

### Payload-Strategie (DB-Wachstum kontrollieren)
Steuerung über ENV:
- `OROMA_SNAP_INDEX_PAYLOAD_MODE = "minimal" | "full"` (Default: `minimal`)

**minimal**
- enthält **keine** vollständige `sources`-Liste, nur `sources_n`
- geeignet für leichte UI-Snippets
- Felder im Payload:
  - `label`, `score`, `sources_n`, `privacy_tier`, `ts`

**full**
- enthält `kind`, `meta_id`, `label`, `score`, **`sources`**, `privacy_tier`, `ts`
- nur für Debug/Analyse (größer)

Payload ist jeweils JSON-bytes (`utf-8`).

### Schema-kompatible Spalten-Erkennung (Produktionsfix)
`_snap_index_columns(conn)` liest `PRAGMA table_info(snap_index)` robust aus, unabhängig von `row_factory`:
- dict-row (`row.get("name")`)
- sqlite3.Row (`row["name"]`)
- tuple/list fallback (`row[1]`)

Damit bleibt das Modul kompatibel, auch wenn `sql_manager` eine dict-row_factory nutzt.

### Upsert-Insert in `snap_index`
`index_meta_snap(...)`:
1) bestimmt `ts` (default: `time.time()`)
2) berechnet `fp = fingerprint_meta(label, sources)`
3) baut `payload` abhängig vom `payload_mode`
4) liest existierende Spalten (für optionale Bridge-Felder)
5) führt `INSERT ... ON CONFLICT(fingerprint) DO UPDATE SET ...` aus

**Kernfelder** (immer):
- `ts`, `source`, `privacy_tier`, `feature_dim=NULL`, `l2_norm=NULL`, `fingerprint`, `payload`

**Optional** (nur wenn Spalten existieren):
- `ref_table="meta_snaps"`
- `ref_id=meta_id`
- `ref_key=NULL`

### Wichtige Invarianten
- `fingerprint` ist der eindeutige Schlüssel: niemals Duplikate.
- Upsert hält den **latest** Stand (payload/ts/source/privacy_tier/refs).
- Modul erwartet eine **bereits geöffnete Connection** (Transaktionen/BusyTimeout werden upstream geregelt).

### Fehlerfälle & Robustheit
- Kein Crash bei abweichendem Schema: optionale Spalten werden nur gesetzt, wenn vorhanden.
- Payload-Modus ist hard-sanitized: unbekannte Werte → `minimal`.

### ENV
- `OROMA_SNAP_INDEX_PAYLOAD_MODE` (`minimal`/`full`)

### Bezug zum Code
- Relevante Datei:
  - `core/snap_indexer.py`
- Verwandte Core-Dokus:
  - `docs/core/14_meta_snap.md`
  - `docs/core/22_snapchain.md` (SnapChain-Persistenz/Indexierung getrennt)
  - `docs/core/90_publication.md` (Snapshot-Policy: DB/log/state exclude)

---

## EN

### Purpose
This document describes `core/snap_indexer.py`: a focused helper that deterministically writes **MetaSnaps** into the flat **`snap_index`** table (Upsert via `ON CONFLICT(fingerprint)`). The goal is a **stable fingerprint key** for fast lookups/explainability without scanning large SnapChain blobs. The module is intentionally **headless** and **SQLite-first**.

### Scope / Non-goals
- ✅ In scope: fingerprint construction (`fingerprint_meta`), payload strategy (`minimal/full`), robust column detection (`PRAGMA table_info`), upsert insert (`index_meta_snap`), ENV control.
- ❌ Out of scope: indexing full SnapChains, forgetting/pruning, ML backends, DBWriter IPC (the module expects an open DB connection).

### Terms
- **MetaSnap:** compact aggregation entity (see `14_meta_snap.md`).
- **snap_index:** flat index table with `fingerprint` as UNIQUE key.
- **Fingerprint:** deterministic dedupe key (`"meta:" + sha256(canon_json)`).
- **Payload mode:** strategy for how much data is stored in `payload`.

### Architectural role
Conceptual flow:
**Dream/Compression/Transfer → MetaSnap → SnapIndexer (`snap_index`) → UI/analysis/explainability**

`SnapIndexer` is intentionally small: it serves as an **index bridge** for compressed/derived meta objects.

### Public API (stable)
- `fingerprint_meta(label: str, sources: list) -> str`
- `index_meta_snap(conn, meta_id, label, score, sources, ts=None, source="dream:meta", privacy_tier="local") -> str`

### Fingerprint (dedupe key)
`fingerprint_meta(label, sources)`:
- canonical JSON: `{"kind":"meta_snap","label":..., "sources":[...]}`
- hash: `sha256(json_bytes)`
- prefix: `"meta:"`

Dedup/idempotency is based on **label+sources**, not payload.

### Payload strategy (control DB growth)
ENV:
- `OROMA_SNAP_INDEX_PAYLOAD_MODE = "minimal" | "full"` (default `minimal`)

**minimal**
- does not store `sources` list (only `sources_n`)
- fields: `label`, `score`, `sources_n`, `privacy_tier`, `ts`

**full**
- stores `kind`, `meta_id`, `label`, `score`, `sources`, `privacy_tier`, `ts`

Payload is stored as UTF-8 JSON bytes.

### Schema-compatible column detection (production fix)
`_snap_index_columns(conn)` reads `PRAGMA table_info(snap_index)` robustly across row factories:
- dict rows
- sqlite3.Row mapping rows
- tuple/list fallback

### Upsert into `snap_index`
`index_meta_snap(...)`:
1) default `ts` to `time.time()`
2) compute `fp`
3) build `payload` based on payload mode
4) detect existing columns (optional bridge fields)
5) execute `INSERT ... ON CONFLICT(fingerprint) DO UPDATE SET ...`

Core fields:
- `ts`, `source`, `privacy_tier`, `feature_dim=NULL`, `l2_norm=NULL`, `fingerprint`, `payload`

Optional fields (only if present):
- `ref_table="meta_snaps"`, `ref_id=meta_id`, `ref_key=NULL`

### Key invariants
- `fingerprint` is the unique key → no duplicates.
- upsert keeps the **latest** state.
- requires an **already opened DB connection** (transaction settings handled upstream).

### Failure modes & robustness
- no crash on schema differences: optional fields are written only when columns exist.
- payload mode is sanitized: unknown values fall back to `minimal`.

### ENV
- `OROMA_SNAP_INDEX_PAYLOAD_MODE` (`minimal`/`full`)

### Code mapping
- Relevant file:
  - `core/snap_indexer.py`
- Related core docs:
  - `docs/core/14_meta_snap.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/90_publication.md`

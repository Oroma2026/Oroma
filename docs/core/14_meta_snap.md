# MetaSnap – Leichtgewichtige Abstraktion / Lightweight Abstraction

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt **MetaSnap** als bewusst minimalistische, stabile Abstraktion über mehrere Snaps/SnapChains: **Label**, **Sources**, **Score**, **Tags**, **Notes**, **Extra** sowie **UID/Fingerprint** und eine kompakte Serialisierung (JSON + zlib). Basis ist `core/meta_snap.py`.

### Scope / Nicht-Ziele
- ✅ In scope: Datenmodell, Invarianten, Fingerprint/UID-Logik, Merge/Decay, Serialisierung (dict/json/blob), Logging/ENV.
- ❌ Out of scope: konkrete DB-Tabellen/Writer-Integrationen (MetaSnap ist hier rein in-memory + blob), Fusion-Mechanik (separat), SnapChain/Pattern-Details (eigene Docs).

### Begriffe
- **MetaSnap:** Abstraktionseinheit, die mehrere Quellen (z. B. SnapChains) zusammenfasst.
- **sources:** Liste von IDs/Keys (z. B. `"chain:42"`), unik & non-empty.
- **score:** Wichtigkeit/Resonanz im Bereich **[0..1]**.
- **tags:** freie Schlagworte, normalisiert (lowercase, unique).
- **extra:** beliebige Zusatzdaten (dict), zur Erweiterung ohne Schema-Bruch.
- **uid:** kurze stabile ID (SHA1(Label|created_at) → 16 hex).
- **fingerprint:** stabiler Hash über relevante Teile (SHA1 über Teileliste).

### Architekturrolle
MetaSnap dient als **kompakte “Binding/Index”-Einheit**:  
Statt viele rohe Snaps/Chains überall zu referenzieren, kann ein MetaSnap:
- mehrere Quellen bündeln (`sources`)
- Wichtigkeit/Resonanz tragen (`score`)
- Schlagworte tragen (`tags`)
- Zusatzinfos speichern (`extra`)
- als komprimierter Blob transportiert/gespeichert werden (`as_blob`)

Typischer Fluss (konzeptuell):
**Snap/SnapChain/SnapPattern → MetaSnap (Aggregation) → Indexierung/Telemetry/Forgetting/DreamWorker**

### Datenmodell (aus `core/meta_snap.py`)
Pflicht/Kern:
- `label: str` (max. 120 Zeichen; Default-Fallback `"meta"`)
- `sources: List[str]` (unique, non-empty)
- `score: float` (geclamped auf [0..1])
- `tags: List[str]` (strip + lowercase + unique)
- `created_at: int`, `updated_at: int` (Unix seconds)

Optional:
- `notes: Optional[str]` (kurze Notiz)
- `extra: Dict[str, Any]` (erweiterbar)
- `uid: Optional[str]`
- `fingerprint: Optional[str]`

### Invarianten & Lifecycle
- **Normalisierung in `__post_init__`:**
  - `label` wird getrimmt/gekürzt
  - `sources` & `tags` werden dedupliziert
  - `score` wird geclamped (`_bounded`)
  - `uid` wird erzeugt, falls fehlend (SHA1 über `label|created_at`, 16 hex)
  - `fingerprint` wird berechnet
- **`touch()`** aktualisiert `updated_at` und berechnet den `fingerprint` neu.

### Mutations-Methoden (Semantik)
- `add_source(key)` / `add_sources(keys)`  
  → union-add (keine Duplikate), danach `touch()`
- `add_tag(tag)`  
  → normalized add (lowercase), danach `touch()`
- `rescore(value)`  
  → clamp [0..1], danach `touch()`
- `decay(factor=0.98)`  
  → leichte zeitliche Abkühlung (nützlich für DreamWorker), danach `touch()`
- `merge_from(other)`  
  → **nicht-destruktive Verschmelzung**:
  - Quellen/Tags: Union
  - Score: Max
  - Notes: concat mit `" | "` wenn beide vorhanden
  - Extra: `update()` (other überschreibt keys), danach `touch()`

### Fingerprint (stabil, bewusst simpel)
`fingerprint = sha1(parts)` mit Separator `|`, wobei `parts` u. a. enthalten:
- `label`, `sources`, `tags`, `score` (gerundet)
- `created_at`, `updated_at`
- `extra` als sortiertes JSON
- `notes`, `uid`

**Hinweis:** Weil `updated_at` im Fingerprint steckt, ändert sich der Fingerprint bei jeder Mutation (das ist gewollt: er repräsentiert “aktuellen Zustand”).

### Serialisierung (kompakt)
- `to_dict()` / `from_dict(d)`  
  - `to_dict()` enthält zusätzlich `"version": "3.7.0"`
  - `from_dict()` berechnet Fingerprint neu, wenn inkonsistent
- `to_json()` / `from_json(s)` (UTF‑8 JSON)
- `as_blob()` / `from_blob(blob)`  
  - JSON wird mit zlib komprimiert (level=6)

### Logging / ENV
- Logger: `oroma.meta_snap` (StreamHandler)
- Level über `OROMA_LOG_LEVEL` (Default WARNING)

### Bezug zum Code
- Relevante Datei:
  - `core/meta_snap.py`
- Nahe/anschließende Core-Dokus (geplant):
  - `docs/core/10_snap.md`
  - `docs/core/20_snappattern.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/24_snap_indexer.md`

---

## EN

### Purpose
This document defines **MetaSnap** as a deliberately minimal, stable abstraction over multiple Snaps/SnapChains: **label**, **sources**, **score**, **tags**, **notes**, **extra**, plus **uid/fingerprint** and compact serialization (JSON + zlib). Based on `core/meta_snap.py`.

### Scope / Non-goals
- ✅ In scope: data model, invariants, uid/fingerprint logic, merge/decay behavior, serialization (dict/json/blob), logging/ENV.
- ❌ Out of scope: concrete DB tables/writer integration (MetaSnap here is in-memory + blob), fusion mechanics, SnapChain/Pattern details.

### Terms
- **MetaSnap:** abstraction unit that aggregates multiple sources (e.g., SnapChains).
- **sources:** list of unique, non-empty IDs/keys (e.g., `"chain:42"`).
- **score:** importance/resonance in **[0..1]**.
- **tags:** free keywords, normalized (lowercase, unique).
- **extra:** extensible dict to add fields without breaking a schema.
- **uid:** short stable ID (SHA1(label|created_at) → 16 hex).
- **fingerprint:** stable SHA1 hash over relevant parts.

### Architectural role
MetaSnap is a compact “binding/index” entity:
- aggregates multiple sources (`sources`)
- carries importance/resonance (`score`)
- carries keywords (`tags`)
- stores additional context (`extra`)
- can be transported/stored as a compressed blob (`as_blob`)

Conceptual flow:
**Snap/SnapChain/SnapPattern → MetaSnap (aggregation) → indexing/telemetry/forgetting/dream worker**

### Data model (from `core/meta_snap.py`)
Core fields:
- `label: str` (trimmed, max 120 chars; fallback `"meta"`)
- `sources: List[str]` (unique, non-empty)
- `score: float` (clamped to [0..1])
- `tags: List[str]` (strip + lowercase + unique)
- `created_at: int`, `updated_at: int` (unix seconds)

Optional:
- `notes: Optional[str]`
- `extra: Dict[str, Any]`
- `uid: Optional[str]`
- `fingerprint: Optional[str]`

### Lifecycle invariants
- **Normalization in `__post_init__`:**
  - normalize `label`, `sources`, `tags`, `score`
  - create `uid` if missing (SHA1 over `label|created_at`, 16 hex)
  - compute `fingerprint`
- **`touch()`** updates `updated_at` and recomputes `fingerprint`.

### Mutation semantics
- `add_source` / `add_sources` → union-add + `touch()`
- `add_tag` → normalized add + `touch()`
- `rescore` → clamp + `touch()`
- `decay(factor=0.98)` → time cooling (DreamWorker-friendly) + `touch()`
- `merge_from(other)` (non-destructive merge):
  - sources/tags: union
  - score: max
  - notes: concatenation with `" | "`
  - extra: dict update (other overrides keys)
  - then `touch()`

### Fingerprint (stable and intentionally simple)
Fingerprint is `sha1(parts)` with `|` separator. The `parts` include:
- label, sources, tags, score (rounded)
- created_at, updated_at
- extra as sorted JSON
- notes, uid

**Note:** Since `updated_at` is included, the fingerprint changes on every mutation (intended: it represents the current state).

### Serialization (compact)
- `to_dict()` / `from_dict(d)`
  - `to_dict()` also includes `"version": "3.7.0"`
  - `from_dict()` recomputes fingerprint if inconsistent
- `to_json()` / `from_json(s)`
- `as_blob()` / `from_blob(blob)`  
  - JSON is zlib-compressed (level=6)

### Logging / ENV
- logger: `oroma.meta_snap` (StreamHandler)
- level via `OROMA_LOG_LEVEL` (default WARNING)

### Code mapping
- Relevant file:
  - `core/meta_snap.py`
- Related core docs (planned):
  - `docs/core/10_snap.md`
  - `docs/core/20_snappattern.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/24_snap_indexer.md`

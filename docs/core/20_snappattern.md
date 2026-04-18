# SnapPattern – Muster/Cluster aus Snaps / Patterns & Clusters from Snaps

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt **SnapPattern** als “Mittelstufe” zwischen **Snap** (Atom) und **SnapChain** (Episode). Ein SnapPattern bündelt mehrere Snaps bzw. Feature-Vektoren zu einem Muster mit **Centroid** (repräsentativer Vektor), unterstützt **Ähnlichkeit/Distanz** (Cosine/L2), kann optional als **Knowledge Gap** markiert werden und bietet eine robuste **SQLite-Persistenz** über die Tabelle `snap_patterns`. Basis ist `core/snappattern.py` (v3.7.3).

### Scope / Nicht-Ziele
- ✅ In scope: Datenmodell, Centroid-Bildung, Cosine/L2, (De-)Serialisierung, Gap-Detection, Persistenz (Schema+save/load/find_similar), DBWriter-First Verhalten.
- ❌ Out of scope: SnapChain-Logik (separat `22_snapchain.md`), SnapIndex-Details (separat `24_snap_indexer.md`), konkrete Vector-DB Migration/ANN Details.

### Begriffe
- **SnapPattern:** Muster/Cluster aus Vektoren (`patterns`) plus `metadata` und `centroid`.
- **patterns:** Liste von Vektoren gleicher Dimension (z. B. aus Snaps oder SnapTokens).
- **centroid:** arithmetischer Mittelwert der Muster-Vektoren (repräsentativer Vektor).
- **Gap / Knowledge Gap:** Heuristik-Markierung für “fehlendes Wissen” (z. B. wenig ähnliche Nachbarn).
- **Thin vs. full payload:** Speicherung nur Centroid/Metadata vs. vollständige Muster-Vektoren.

### Architekturrolle
Konzeptueller Fluss:
**Snap → (optional Fusion/SnapToken) → SnapPattern → SnapChain → Replay/Dream/Policy**

SnapPattern ist die erste stabile “Verdichtungs”-Stufe:
- reduziert viele Snaps auf einen Centroid,
- erlaubt schnelle Similarity-Suche,
- und dient als Baustein für episodische Ketten (SnapChains).

### Datenmodell (aus `SnapPattern` dataclass)
- `patterns: List[List[float]]` – Rohmuster-Vektoren (können leer sein)
- `metadata: Dict[str, Any]` – frei erweiterbar (Labels, Tags, Herkunft, etc.)
- `created_at: int` – Unix Timestamp
- `centroid: List[float]` – Centroid-Vektor (leer, wenn nicht berechenbar)

**Wichtige Invariante:** Alle Vektoren in `patterns` müssen die gleiche Dimension haben. Bei Dimension-Mismatch wird der Centroid leer/Null (siehe Selftest).

### Erzeugung & Mutationen
- `from_snaps(snaps, metadata=None)`  
  → akzeptiert heterogene Inputs (Snap, SnapToken, dict, Sequence[float]) und extrahiert jeweils einen Vektor via `_to_vector_list()`.
- `add_snap(snap_or_vec)` / `extend_snaps(snaps)`  
  → fügt Vektoren hinzu, **ignoriert** Dimension-Mismatch (mit Warning), und recomputet `centroid`.
- `recompute_centroid()`  
  → bildet arithmetisches Mittel; bei leerer Liste bleibt centroid leer.
- `normalize_centroid()`  
  → L2-normalisiert Centroid in-place (optional vor Persistenz).

### Vektor-Metriken
- `cosine_similarity(a, b, allow_mismatch=False)`  
  → robust: bei leeren Vektoren 0.0; optional Mismatch tolerant.
- `l2_distance(a, b, allow_mismatch=False)`  
  → robust: bei leeren Vektoren große Distanz; optional Mismatch tolerant.
- Convenience Methoden:
  - `sp.cosine_to(other)` / `sp.l2_to(other)`
  - `quick_similarity(a, b, allow_mismatch=False)` (Patterns/Vektoren/IDs)

### Serialisierung
- `to_dict(include_patterns=True)` / `from_dict(d)`
- `as_blob(include_patterns=True)` / `from_blob(blob)`  
  → kompakt über JSON-Pack (`_pack_json` / `_unpack_json`), tolerant gegenüber RowFactory-Varianten.

### Gap-Detection (Knowledge Gap Heuristik)
`detect_gap(sp, threshold=0.30, max_candidates=500)`:
- wenn `centroid` leer → Gap
- versucht zunächst (falls verfügbar) eine Vector-DB Query (`vector_migration.query`) ab einer Threshold-Anzahl SnapChains
- sonst Fallback: lädt Kandidaten-SnapChains aus DB und vergleicht Cosine, um “kein ähnlicher Nachbar” zu erkennen
- Ergebnis: bool → wird als `gap_flag` gespeichert (optional)

Wichtig: Gap-Detection ist best-effort und darf **nicht** hart crashen.

### Persistenz: `snap_patterns` (SQLite)
#### Schema (idempotent)
`_ensure_snappattern_schema()`:
- erweitert Schema um Tabelle `snap_patterns` und Indizes
- **DBWriter-First**: Wenn DBWriter aktiv ist, werden Schema-Writes via DBWriter ausgeführt; lokaler Fallback ist dann ausdrücklich verboten.

Tabellenfelder (konzeptuell):
- `id` (PK), `created_at`, `feature_dim`, `num_snaps`, `centroid` (BLOB), `payload` (BLOB), `metadata` (TEXT), `gap_flag` (INT)

#### Speichern
`save_pattern(sp, store_full_payload=False, detect_gap_flag=True, normalize=False) -> int`:
- optional centroid normalisieren
- schreibt `centroid` als JSON-BLOB; **v3.8-r1**: zusätzlich optional `l2_norm` im centroid_blob
- `payload` enthält optional die vollen Muster-Vektoren (wenn `store_full_payload=True`)
- setzt `gap_flag` per `detect_gap()` (wenn aktiviert)
- **DBWriter-First**: bei aktivem DBWriter keine lokalen Writes; bei DBWriter-Fehlern wird ohne lokalen Fallback abgebrochen (`-1`)

#### Laden
`load_pattern(id, full=True)`:
- spiegelt `gap_flag` nach `metadata["_gap_flag"]`
- spiegelt optionales centroid `l2_norm` nach `metadata["_centroid_l2_norm"]`
- robustes Metadata-JSON Parsing (ungültiges JSON → Warnung, `{}`)
- `full=True`: versucht `payload` zu laden; bei Fehler fallback auf centroid-only

#### Metadaten Patch / Gap Override
- `update_metadata(id, patch: dict) -> bool`
- `set_gap_flag(id, flag: bool) -> bool`

#### Similarity Search (Fallback ohne ANN)
`find_similar(query, topk=10, max_age_days=None, require_same_dim=True, ...)`:
- lädt bis zu 2000 neueste Pattern-Centroids (optional gefiltert)
- berechnet Cosine-Scores und liefert Top-K

### Logging / ENV
- Logger-Level via `OROMA_LOG_LEVEL` (einzige direkte ENV im Modul).

### Bezug zum Code
- Relevante Datei:
  - `core/snappattern.py`
- Verwandte Core-Dokus:
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/24_snap_indexer.md`

---

## EN

### Purpose
This document defines **SnapPattern** as the “middle layer” between **Snap** (atomic unit) and **SnapChain** (episode). A SnapPattern clusters multiple snaps / feature vectors into a pattern with a **centroid** (representative vector), supports **similarity/distance** (cosine/L2), can be marked as a **knowledge gap**, and provides robust **SQLite persistence** via the `snap_patterns` table. Based on `core/snappattern.py` (v3.7.3).

### Scope / Non-goals
- ✅ In scope: data model, centroid computation, cosine/L2 metrics, (de-)serialization, gap detection, persistence (schema+save/load/find_similar), DBWriter-first behavior.
- ❌ Out of scope: SnapChain logic (`22_snapchain.md`), SnapIndex details (`24_snap_indexer.md`), full vector DB migration/ANN specifics.

### Terms
- **SnapPattern:** pattern/cluster of vectors (`patterns`) with `metadata` and a `centroid`.
- **patterns:** list of same-dimensional vectors (from snaps or tokens).
- **centroid:** arithmetic mean of pattern vectors (representative vector).
- **Gap / knowledge gap:** heuristic flag for “missing knowledge” (e.g., no close neighbors).
- **Thin vs. full payload:** store centroid/metadata only vs. store full pattern vectors.

### Architectural role
Conceptual flow:
**Snap → (optional Fusion/SnapToken) → SnapPattern → SnapChain → Replay/Dream/Policy**

SnapPattern is the first stable “compression” layer:
- collapses many snaps into a centroid,
- enables similarity search,
- acts as a building block for episodic chains (SnapChains).

### Data model (`SnapPattern` dataclass)
- `patterns: List[List[float]]`
- `metadata: Dict[str, Any]`
- `created_at: int`
- `centroid: List[float]`

**Key invariant:** all vectors in `patterns` must share the same dimension. Dimension mismatch results in an empty centroid (see selftest).

### Construction & mutation
- `from_snaps(snaps, metadata=None)` accepts heterogeneous inputs (Snap, SnapToken, dict, vector) via `_to_vector_list()`.
- `add_snap(...)` / `extend_snaps(...)` appends vectors, ignores dimension mismatches (warning), recomputes centroid.
- `recompute_centroid()` computes an arithmetic mean; empty patterns → empty centroid.
- `normalize_centroid()` optionally L2-normalizes the centroid in-place.

### Vector metrics
- `cosine_similarity(a, b, allow_mismatch=False)` – robust, returns 0.0 for empty vectors.
- `l2_distance(a, b, allow_mismatch=False)` – robust, large distance for empty vectors.
- Convenience:
  - `sp.cosine_to(...)` / `sp.l2_to(...)`
  - `quick_similarity(a, b, allow_mismatch=False)` supports patterns/vectors/IDs.

### Serialization
- `to_dict(include_patterns=True)` / `from_dict(d)`
- `as_blob(include_patterns=True)` / `from_blob(blob)` using JSON packing; tolerant to different row factories.

### Gap detection (knowledge gap heuristic)
`detect_gap(sp, threshold=0.30, max_candidates=500)`:
- empty centroid → gap
- prefers vector DB query (`vector_migration.query`) when available after a threshold
- fallback loads candidate snapchains and checks cosine similarity
- best-effort: should not crash the system.

### Persistence: `snap_patterns` (SQLite)
#### Schema (idempotent)
`_ensure_snappattern_schema()` creates `snap_patterns` and indexes.
**DBWriter-first:** when DBWriter is enabled, schema writes are executed via DBWriter; local fallback is forbidden.

#### Save
`save_pattern(sp, store_full_payload=False, detect_gap_flag=True, normalize=False) -> int`:
- optional centroid normalization
- centroid stored as JSON blob; **v3.8-r1** adds optional `l2_norm` into centroid blob
- payload optionally stores full vectors
- gap flag computed via `detect_gap()`
- **DBWriter-first:** if DBWriter fails, the function returns `-1` and does not fall back to local writes.

#### Load
`load_pattern(id, full=True)`:
- mirrors `gap_flag` into `metadata["_gap_flag"]`
- mirrors optional centroid `l2_norm` into `metadata["_centroid_l2_norm"]`
- robust metadata JSON parsing (invalid JSON → warning, `{}`)
- with `full=True` attempts payload load; falls back to centroid-only.

#### Metadata patch / gap override
- `update_metadata(id, patch) -> bool`
- `set_gap_flag(id, flag) -> bool`

#### Similarity search (fallback without ANN)
`find_similar(query, topk=10, ...)`:
- loads up to 2000 latest centroids (filtered)
- computes cosine scores and returns top-k.

### Logging / ENV
- logging level via `OROMA_LOG_LEVEL`

### Code mapping
- Relevant file:
  - `core/snappattern.py`
- Related core docs:
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/24_snap_indexer.md`

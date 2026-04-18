# Snap – Atomare Beobachtungseinheit / Atomic Observation Unit

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt den **Snap** als kleinste stabile Repräsentation einer Momentaufnahme im ORÓMA-System (Snap v1.1) – inklusive Feature-Vektor, Metadaten, Norm-/Fingerprint-Cache (Dedup) und der Schnittstellen zur Serialisierung sowie SnapIndex-Registrierung.

### Scope / Nicht-Ziele
- ✅ In scope: Datenmodell, Invarianten, Normalisierung/Ähnlichkeit, Dedup-Fingerprint, Serialisierung, SnapIndex-Insert (konzeptuell).
- ❌ Out of scope: Vollständige DB-Schema-Details, Fusion-Implementierung (separates Dokument), UI/Orchestrator-Details.

### Begriffe
- **Snap:** Atomare Beobachtungseinheit (Features + Content + Metadata) mit Zeitstempel und stabilen Caches (L2-Norm, Fingerprint).
- **Fingerprint:** Kurzer SHA1-basierter Hash zur Deduplikation (v. a. für `snap_index`).
- **L2-Norm:** Cache der Vektornorm, genutzt für Normalisierung und Similarity.
- **SnapIndex:** Flache Index-Tabelle, in die Snaps als Payload (Blob) plus Fingerprint/Stats geschrieben werden (DB-schonender Lookup).

### Architekturrolle
Typischer Fluss (konzeptuell):
**DeviceHub/Perzeption → (optional Fusion) → Snap → SnapPattern/SnapChain → Replay/Dream/Policy**  
Der Snap ist dabei die kleinste “haltbare” Einheit, die später erneut geladen, verglichen und in Episoden (SnapChains) eingebaut werden kann.

### Kernprinzipien / Invarianten (aus `core/snap.py`)
- **Schema-Kennung:** Snap trägt eine `schema`-ID (Default: `"snap.v1.1"`; via ENV überschreibbar).
- **Governance/Privacy:** Snap hat `privacy_tier` (Default via ENV; z. B. `"internal"`).
- **Caches:** `feature_dim`, `l2_norm` und `fingerprint` werden gecacht und bei Änderungen über `recompute_stats()` aktualisiert.
- **Dedup:** Fingerprint ist kurz und stabil genug, um Duplicate Snaps im Index zu erkennen (optional).
- **Monotone Zeit:** zusätzlich zu `ts` wird `ts_monotonic` gehalten (lokale Reihenfolge/Debug).

### Wichtige ENV-Variablen
- `OROMA_SNAP_SCHEMA="snap.v1.1"` – überschreibt die Standard-Schema-Kennung.
- `OROMA_SNAP_PRIVACY="internal"` – Default Privacy Tier für neue Snaps.
- Logging (Legacy-Konvention):  
  `OROMA_SNAPCHAIN_LOGLEVEL` (präferiert) / `OROMA_SNAPCHAIN_LEVEL` (Fallback).

### API-Oberfläche (stabil)
**Konstruktion**
- `Snap(features, metadata, content=..., ts=..., uid=..., schema=..., privacy_tier=..., snap_id=...)`

**Statistik/Caches**
- `recompute_stats()` – stellt sicher, dass `feature_dim`, `l2_norm`, `fingerprint` konsistent sind.

**Fusion-Hook (ohne Zwang)**
- `attach_fusion(fusion_pack)` / `get_fusion()` – Snap kann Fusion-Informationen tragen, bleibt aber ohne Fusion funktionsfähig.

**Normalisierung & Ähnlichkeit**
- `normalize()` – normalisiert den Feature-Vektor; aktualisiert L2/Fingerprint.
- `similarity(other)` – Ähnlichkeitsmaß (vektorbasiert; nutzt Norm-Caches).

**Metadaten**
- `with_metadata(**kv)` – erzeugt Snap-Variante mit zusätzlichen Metadaten.
- `merge_metadata(dict)` – merge/override in bestehende Metadaten.

**Serialisierung**
- `to_dict()` / `from_dict()` – JSON-fähige Struktur.
- `as_blob()` / `from_blob()` – kompakte Byte-Payload (für Index/DB).

**SnapIndex-Registrierung (Helper)**
- `dedup_or_insert_snap(snap, source=..., privacy_tier=..., payload=..., dedup=True, update_metadata=True)`  
  → schreibt Snap in `snap_index` (Upsert bei dedup) und setzt `snap.id` sowie `metadata["snap_id"]`.

### Fehlerfälle & Robustheit
- Ohne verfügbaren `sql_manager.insert_snap_index` wird `dedup_or_insert_snap()` **best-effort** abgebrochen (kein Crash).
- Metadaten-Updates werden defensiv behandelt (suppress/log) statt harten Exceptions.

### Bezug zum Code
- Relevante Datei:
  - `core/snap.py`
- Nahe/anschließende Core-Dokus:
  - `docs/core/12_snaptoken.md` (SnapToken)
  - `docs/core/15_fusion.md` (Fusion)
  - `docs/core/20_snappattern.md` (SnapPattern)
  - `docs/core/22_snapchain.md` (SnapChain)
  - `docs/core/24_snap_indexer.md` (SnapIndex)

---

## EN

### Purpose
This document defines the ORÓMA **Snap** as the smallest stable “moment” representation (Snap v1.1): feature vector, content, metadata, and cached stats (L2 norm + fingerprint) for normalization, similarity, and deduplication in the SnapIndex.

### Scope / Non-goals
- ✅ In scope: data model, invariants, normalization/similarity, fingerprint-based dedup, serialization, SnapIndex registration (conceptual).
- ❌ Out of scope: full DB schema details, fusion implementation (separate doc), UI/orchestrator details.

### Terms
- **Snap:** Atomic observation unit (features + content + metadata) with timestamps and stable caches (L2 norm, fingerprint).
- **Fingerprint:** Short SHA1-based hash used for deduplication (primarily for `snap_index`).
- **L2 norm:** Cached vector norm used by normalization and similarity computations.
- **SnapIndex:** Flat index table storing snaps as payload blobs plus fingerprint/stats for lightweight lookup.

### Architectural role
Typical conceptual flow:
**DeviceHub/Perception → (optional Fusion) → Snap → SnapPattern/SnapChain → Replay/Dream/Policy**  
A Snap is the smallest “durable” unit that can be reloaded, compared, and assembled into episodes (SnapChains).

### Core principles / invariants (from `core/snap.py`)
- **Schema ID:** Snap carries a `schema` identifier (default `"snap.v1.1"`, overridable via ENV).
- **Privacy tier:** `privacy_tier` supports governance (default via ENV, e.g. `"internal"`).
- **Cached stats:** `feature_dim`, `l2_norm`, `fingerprint` are cached and kept consistent via `recompute_stats()`.
- **Dedup:** fingerprint enables optional dedup/Upsert behavior in the index.
- **Monotonic time:** in addition to `ts`, a `ts_monotonic` value is stored for local ordering/debug.

### Key environment variables
- `OROMA_SNAP_SCHEMA="snap.v1.1"` – override schema identifier.
- `OROMA_SNAP_PRIVACY="internal"` – default privacy tier for newly created snaps.
- Logging (legacy): `OROMA_SNAPCHAIN_LOGLEVEL` (preferred) / `OROMA_SNAPCHAIN_LEVEL` (fallback).

### Stable API surface
**Construction**
- `Snap(features, metadata, content=..., ts=..., uid=..., schema=..., privacy_tier=..., snap_id=...)`

**Stats / caches**
- `recompute_stats()` – ensures `feature_dim`, `l2_norm`, `fingerprint` are consistent.

**Optional fusion attachment**
- `attach_fusion(fusion_pack)` / `get_fusion()` – Snap can carry fusion details but works without them.

**Normalization & similarity**
- `normalize()` – normalizes the feature vector; updates L2/fingerprint caches.
- `similarity(other)` – vector-based similarity using norm caches.

**Metadata**
- `with_metadata(**kv)` – create a variant with additional metadata.
- `merge_metadata(dict)` – merge/override into existing metadata.

**Serialization**
- `to_dict()` / `from_dict()` – JSON-friendly representation.
- `as_blob()` / `from_blob()` – compact byte payload for DB/index storage.

**SnapIndex registration helper**
- `dedup_or_insert_snap(snap, source=..., privacy_tier=..., payload=..., dedup=True, update_metadata=True)`  
  → writes the snap into `snap_index` (Upsert when dedup is enabled) and updates `snap.id` plus `metadata["snap_id"]`.

### Failure modes & robustness
- If `sql_manager.insert_snap_index` is unavailable, `dedup_or_insert_snap()` exits best-effort (no crash).
- Metadata updates are handled defensively (suppressed/logged) rather than raising hard exceptions.

### Code mapping
- Relevant file:
  - `core/snap.py`
- Related core docs (planned):
  - `docs/core/12_snaptoken.md`
  - `docs/core/15_fusion.md`
  - `docs/core/20_snappattern.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/24_snap_indexer.md`

# SnapChain – Episoden als Sequenz von SnapPatterns / Episodes as Sequences of SnapPatterns

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt **SnapChain** als ORÓMAs episodische Gedächtniseinheit: eine **zeitlich geordnete Kette** von **SnapPatterns** (nicht nur rohen Snaps), ergänzt um optionale **Spatio-Temporal-Spuren** (Zeitdelta + Raum-Waypoints) und eine robuste **Knowledge-/RAG-Bridge** (Text/Knowledge-Snaps und einfache Anfragefunktionen). Basis ist `core/snapchain.py` (Projekt v3.7.3; Wire/Formatmarker über `SCHEMA_VERSION`).

### Scope / Nicht-Ziele
- ✅ In scope: Datenmodell, Container-API, Kontext-Append (Zeit/Raum), Feature-Centroid, Resonanz/Ähnlichkeit, Knowledge-Bridge, (De-)Serialisierung (dict/blob), Logging/ENV.
- ❌ Out of scope: Persistenz der gesamten SnapChain in `snapchains`-Tabelle (anderes Modul/Layer), Policy-/Decision-Engine, UI/Replay-Controller-Details.

### Begriffe
- **SnapChain:** Episode als Liste von `SnapPattern`-Objekten (`patterns`).
- **SnapPattern:** Verdichtung/Cluster aus Snaps oder Vektoren mit `centroid` (siehe `20_snappattern.md`).
- **Wire/Schema Marker:** `SCHEMA_VERSION` in `metadata["version"]` beschreibt das Serialisierungsformat, nicht die Projektversion.
- **Spatio-Temporal Spur:** `metadata["timing"]` und `metadata["space"]` für Zeitabstände und Raumgraph-Bezüge.
- **Knowledge Snap:** SnapChain kann Text/“Knowledge”-Snaps als Pattern aufnehmen und später einfache Antworten synthetisieren (RAG-light).

### Architekturrolle
Konzeptueller Fluss:
**Snap → (optional Fusion/SnapToken) → SnapPattern → SnapChain → Replay/Dream/Policy**

SnapChain ist die erste Struktur, die **zeitliche Reihenfolge** als Episode konserviert und dadurch:
- Replay ermöglicht,
- Konsolidierung (Dream) strukturiert,
- und Transfer/Explainability unterstützt.

### Datenmodell (Kern)
- `self.patterns: List[SnapPattern]` – die Kette (Episode)
- `self.metadata: Dict[str, Any]` – Formatmarker + Timing/Space + freie Felder
- `self.resonance_score: float` – Score-Feld (nicht automatisch persistent)
- `self.reward_score: float` – Score-Feld
- `self.episodic_id: Optional[str]` – optional externe ID
- `self.explain_trace: Optional[Dict[str, Any]]` – optional Debug/Explain
- `self.ts_created: float` – Erstellzeit

### Wire/Format vs Projektversion
`metadata["version"] = SCHEMA_VERSION` wird als **Formatmarker** gesetzt.  
Wichtig: Der Header weist ausdrücklich darauf hin, dass `SCHEMA_VERSION` nicht die Projektversion ist. Das Format ist bewusst “Superset-fähig” für Forward-Compatibility.

### Container-API (stabil)
- `__len__`, `__iter__`, `clear()`, `extend(items)`, `append(obj)`
- `add_snap(...)` ist ein Legacy-Alias für `append(...)`.

**Append-Input-Typen (robust):**
- `Snap`
- `SnapPattern`
- `List[float]` (Feature-Vektor)
- `Dict[str, Any]` (snap-ähnliche Struktur)

Intern wird über `_append_any(...)` in einen `SnapPattern` koerziert (z. B. Vektor → Pattern mit einem Vektor).

### Spatio-Temporal Erweiterung (optional, aber wichtig)
SnapChain pflegt zwei optionale Strukturen in `metadata`:

#### Timing
- `metadata["timing"]["ts"]` – Zeitstempel je Schritt
- `metadata["timing"]["delta_time"]` – dt zum vorherigen Schritt

Diese werden **nur** über `append_with_context(...)` aktualisiert:
- `_update_timing(ts)` schreibt `ts` und `dt` (nicht negativ, robust)

#### Space (Raumgraph)
- `metadata["space"]["waypoints"]` – Punkt-IDs aus `core.spatial_index`
- `metadata["space"]["relations"]` – Kanten/Relationen zwischen Waypoints

Aktualisierung erfolgt ebenfalls über `append_with_context(...)` und `_update_space_from_obj(obj)`:
- erwartet `pos` als Dict mit mindestens `x`, `y` (optional `z`, `label`)
- funktioniert für `Snap.metadata["pos"]` oder Dict-Form (`obj["pos"]` / `obj["metadata"]["pos"]`)
- wird nur aktiv, wenn `core.spatial_index` importierbar ist (`_HAS_SPATIAL`)

**Wichtig:** Normales `append()` pflegt keinen Raum/Zeit-Kontext – das ist bewusst getrennt.

### Knowledge-/RAG-Bridge (RAG-light)
SnapChain bietet helper, um Text/Knowledge in die Episode einzubetten:
- `add_text(text, meta=None)` – Text als Pattern in die Chain
- `add_knowledge_snap(text, meta=None)` – Alias/Variante mit Knowledge-Markern
- `ask_knowledge(question, top_k=..., ...)` – sucht relevante Knowledge-Snaps und synthesisiert eine Antwort
- `synthesize_answer(question, facts)` – simple Antwort-Synthese (nicht LLM-abhängig; robust)

Das ist bewusst “lightweight”: keine externe Cloud-Abhängigkeit, best-effort Similarity über Vektor-/Token-Features.

### Aggregationen / Scores
- `feature_centroid()` – Centroid über Pattern-Centroids (tolerant gegenüber leeren Vektoren)
- `score_resonance(other=None)` – Resonanz/Ähnlichkeit (Cosine), optional gegen andere Chain/Vector
- interne Cosine-Helper `_cos` (optional NumPy Beschleunigung)

### Serialisierung
- `to_dict()` / `from_dict(d)` – JSON-freundliche Struktur
  - enthält `patterns` als Pattern-Dicts und `metadata`
  - `from_dict` rekonstruiert Patterns robust (auch bei alten/teilweisen Strukturen)
- `as_blob()` / `from_blob(blob)` – kompakte Byte-Form (zlib/JSON-Pack)
- Debug-Tracing via ENV:
  - `OROMA_SNAPCHAIN_TRACE_APPEND`
  - `OROMA_SNAPCHAIN_TRACE_SERIALIZE`

### Logging / ENV
- `OROMA_SNAPCHAINS` – Rootpfad/Default-Ordner für SnapChain-Artefakte (wenn File-IO genutzt wird)
- `OROMA_SNAPCHAIN_LOGLEVEL` – Logging-Level
- `OROMA_SNAPCHAIN_ATTACH_STDERR` – optionale Log-Spiegelung auf stderr
- `OROMA_SNAPCHAIN_TRACE_APPEND` / `...TRACE_SERIALIZE` – Debug-Traces

### Fehlerfälle & Robustheit
- Viele Koerzionspfade (`_snap_to_dict_safe`, `_pattern_centroid_safe`, etc.) sind best-effort und tolerieren:
  - fehlende Felder
  - leere Vektoren
  - gemischte Inputtypen
- Spatio-Temporal ist optional: wenn `spatial_index` fehlt, wird Space-Update einfach übersprungen.

### Bezug zum Code
- Relevante Datei:
  - `core/snapchain.py`
- Verwandte Core-Dokus:
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/20_snappattern.md`
  - `docs/core/24_snap_indexer.md` (Indexierung/DB-Schonung)

---

## EN

### Purpose
This document defines **SnapChain** as ORÓMA’s episodic memory unit: a **time-ordered chain** of **SnapPatterns** (not just raw snaps), augmented by optional **spatio-temporal traces** (time deltas + spatial waypoints) and a robust **knowledge/RAG bridge** (text/knowledge snaps and lightweight query helpers). Based on `core/snapchain.py` (project v3.7.3; wire/format marker via `SCHEMA_VERSION`).

### Scope / Non-goals
- ✅ In scope: data model, container API, context-aware append (time/space), feature centroid, resonance/similarity, knowledge bridge, (de-)serialization (dict/blob), logging/ENV.
- ❌ Out of scope: full DB persistence of snapchains (handled in other layers), policy/decision engine, UI/replay controller specifics.

### Terms
- **SnapChain:** episode represented as a list of `SnapPattern` objects (`patterns`).
- **SnapPattern:** compression/cluster with a `centroid` (see `20_snappattern.md`).
- **Wire/schema marker:** `SCHEMA_VERSION` stored in `metadata["version"]` indicates serialized format, not project version.
- **Spatio-temporal trace:** `metadata["timing"]` and `metadata["space"]` for time deltas and spatial graph references.
- **Knowledge snap:** SnapChain can embed text/knowledge as patterns and later synthesize simple answers (RAG-light).

### Architectural role
Conceptual flow:
**Snap → (optional Fusion/SnapToken) → SnapPattern → SnapChain → Replay/Dream/Policy**

SnapChain preserves **temporal ordering** as an episode, enabling replay, structured consolidation (dream), and transfer/explainability.

### Core data model
- `patterns: List[SnapPattern]`
- `metadata: Dict[str, Any]` (format marker + timing/space + free fields)
- `resonance_score`, `reward_score`
- `episodic_id`, `explain_trace`
- `ts_created`

### Wire/format vs project version
`metadata["version"] = SCHEMA_VERSION` is a **format marker**. The module header explicitly warns not to confuse this with the project version; the format is designed to be superset/forward compatible.

### Container API (stable)
- `__len__`, `__iter__`, `clear()`, `extend()`, `append()`
- `add_snap(...)` is a legacy alias to `append(...)`.

Accepted append input types (robust):
- `Snap`
- `SnapPattern`
- `List[float]` feature vector
- `Dict[str, Any]` snap-like structure

Internally `_append_any(...)` coerces inputs into `SnapPattern` objects.

### Optional spatio-temporal tracking
SnapChain maintains two optional metadata blocks:

#### Timing
- `metadata["timing"]["ts"]`
- `metadata["timing"]["delta_time"]`

These are updated **only** via `append_with_context(...)` using `_update_timing(ts)`.

#### Space (spatial graph)
- `metadata["space"]["waypoints"]` (point IDs from `core.spatial_index`)
- `metadata["space"]["relations"]`

Updated via `append_with_context(...)` and `_update_space_from_obj(obj)`:
- expects `pos` dict with `x`, `y` (optional `z`, `label`)
- supports Snap metadata or dict forms
- no-op when `spatial_index` is unavailable.

### Knowledge / RAG bridge (RAG-light)
SnapChain provides helpers to embed text/knowledge and query it:
- `add_text(text, meta=None)`
- `add_knowledge_snap(text, meta=None)`
- `ask_knowledge(question, top_k=..., ...)`
- `synthesize_answer(question, facts)` (robust, non-LLM dependent)

### Aggregations / scores
- `feature_centroid()` computes a centroid over pattern centroids (tolerant to empty vectors).
- `score_resonance(other=None)` computes cosine-based resonance, optionally against another chain/vector.

### Serialization
- `to_dict()` / `from_dict(d)` for JSON-friendly structures
- `as_blob()` / `from_blob(blob)` for compact binary blobs
- Debug tracing via env:
  - `OROMA_SNAPCHAIN_TRACE_APPEND`
  - `OROMA_SNAPCHAIN_TRACE_SERIALIZE`

### Logging / ENV
- `OROMA_SNAPCHAINS`
- `OROMA_SNAPCHAIN_LOGLEVEL`
- `OROMA_SNAPCHAIN_ATTACH_STDERR`
- `OROMA_SNAPCHAIN_TRACE_APPEND`, `OROMA_SNAPCHAIN_TRACE_SERIALIZE`

### Failure modes & robustness
- Coercion helpers are best-effort and tolerate missing fields, empty vectors, and mixed input types.
- Spatio-temporal tracking is optional; missing `spatial_index` simply disables space updates.

### Code mapping
- Relevant file:
  - `core/snapchain.py`
- Related core docs:
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/20_snappattern.md`
  - `docs/core/24_snap_indexer.md`

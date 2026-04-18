# Fusion – Crossmodal Feature-Fusion / Crossmodal Feature Fusion

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt das ORÓMA-Modul **`core/fusion.py`**: einen **deterministischen, headless-tauglichen** Mechanismus zur **Crossmodal-Fusion** (Vision ↔ Audio ↔ Text ↔ Meta). Ergebnis ist ein **`FusionPack`**, das mehrere Modalitäten als einzelne Vektoren (Parts) plus symbolische Tokens/Concepts kapselt – für Similarity, Linking, Replay/Dream und Explainability.

### Scope / Nicht-Ziele
- ✅ In scope: Datenstrukturen (`ModalityVec`, `FusionPack`), `FusionEngine`, deterministische Fallbacks, Similarity/Score, Serialisierung.
- ❌ Out of scope: komplette Anwendungspfade in anderen Subsystemen (Linker/UI), konkrete DB-Tabellen, Training/Modelle.

### Begriffe
- **ModalityVec:** Vektor + Meta-Info einer Modalität (`kind`, `vec`, `meta`).
- **FusionPack:** Container aus `modalities`, `tokens`, `concepts` (plus `created_ts`, `version`).
- **FusionEngine:** Brücke zu optionalen Runtimes (Text-Embedding/Tokenizer und Vision-Projection), mit robusten Offline-Fallbacks.

### Architekturrolle
Konzeptueller Fluss:
**DeviceHub/Perzeption → Snap (atomar) → Fusion (optional) → FusionPack → Similarity/Linking/Replay/Dream**

Wichtig: Fusion ist **optional**. Snap/SnapChain funktionieren weiterhin ohne Fusion – Fusion ergänzt nur eine stabile Crossmodal-Repräsentation.

### Öffentliche API (realer Stand in `core/fusion.py`)
#### Datenklassen
- `ModalityVec(kind: str, vec: List[float], meta: Dict[str, Any])`
- `FusionPack(modalities, tokens, concepts, created_ts, version)`
  - `to_dict()` / `from_dict()`
  - `to_json()` / `from_json()`

#### Engine
- `FusionEngine(llm_rt=None, vision_rt=None)`
  - `text_to_vec(text)`: Text → Vektor (LLM-Embedding oder deterministischer SHA1-Fallback)
  - `tokenize(text)`: Text → Tokens (LLM-Tokenizer oder whitespace split)
  - `normalize_concepts(tokens)`: Tokens → Konzepte (LLM oder unique/lowercase)
  - `vision_to_vec(features)`: Vision-Features → Projektionsraum (vision_rt.project oder Identity/Fallback)
  - `build_fusion(text=None, vision_features=None, audio_features=None, extra_modalities=None) -> FusionPack`
  - `similarity(a: FusionPack, b: FusionPack, weights=None) -> float`: gewichtete Cosine-Ähnlichkeit je Modalität
  - `fuse(snaps: List[List[float]], tokens: List[str]) -> FusionPack`: High-Level Sensor-Fusion (kind="sensor")
  - `split(fusion_snap) -> (snap_vecs, tokens)`: inverse zu `fuse()`
  - `score(fusion_snap) -> float`: Heuristik 0..1 (Modalitäten + Tokens/Konzepte)

> Hinweis: Der Header des Moduls erwähnt „concat/weighted“ Strategien. Der **aktuelle Code** modelliert Fusion als **Liste von Modalitäts-Vektoren** plus Similarity/Score. Ein explizites `fused_vector` wird im aktuellen Stand **nicht** als Feld persistiert; bei Bedarf kann ein Consumer die Vektoren deterministisch zusammenführen (z. B. concat) – aber das ist außerhalb dieses Moduls.

### Deterministische Fallbacks (wichtig für Offline/Headless)
- **Text embedding fallback:** SHA1 über Text → deterministischer Pseudo-Vektor mit Dimension `OROMA_EMBED_DIM` (Default 128). Optional L2-normalisiert.
- **Tokenizer fallback:** whitespace split.
- **Concept normalization fallback:** unique + lowercase.

Damit bleibt Fusion auch dann nutzbar, wenn LLM/Vision-Runtimes nicht verfügbar sind.

### Similarity (gewichtete Cosine je Modalität)
`FusionEngine.similarity()`:
- bildet die Menge der `kind`s aus beiden Packs
- vergleicht pro `kind` jeweils den **ersten** passenden Vektor
- berechnet Cosine und bildet ein gewichtetes Mittel (`weights[kind]`, Default 1.0)

### Score (0..1)
`score()` ist eine leichte Heuristik:
- mehr Modalitäten → höher
- Tokens vorhanden → Bonus
- Concepts vorhanden → Bonus
- Sättigung über Exponentialterm (keine lineare Explosion)

### Wichtige ENV-Variablen
- `OROMA_FUSION_ENABLE=1|0` – Schalter (häufig in Laufzeit/Orchestrator genutzt; Modul selbst erzwingt nichts).
- `OROMA_FUSION_MODE=concat|weighted` – im Header dokumentiert; aktuelle Engine nutzt Similarity-Weights.
- `OROMA_FUSION_W_VISION`, `OROMA_FUSION_W_AUDIO`, `OROMA_FUSION_W_TEXT` – Gewichte (typisch für Similarity).
- `OROMA_FUSION_NORMALIZE=1|0` – Normalisierungsschalter (Header).
- `OROMA_EMBED_DIM=128` – Fallback-Embedding-Dimension.
- `OROMA_EMBED_NORM=1|0` – L2-Normalisierung von Vektoren.

### Fehlerfälle & Robustheit
- Alle optionalen Runtime-Calls (`llm_rt.*`, `vision_rt.project`) sind in `try/except` gekapselt und verwenden `log_guard.log_suppressed(...)`.
- Bei Fehlern wird **nicht** abgebrochen, sondern deterministisch/fallback weitergearbeitet.

### Bezug zum Code
- Relevante Datei:
  - `core/fusion.py`
- Verwandte Core-Dokus:
  - `docs/core/10_snap.md` (Snap)
  - `docs/core/12_snaptoken.md` (SnapToken)
  - `docs/core/22_snapchain.md` (SnapChain – nutzt/transportiert Fusion optional)
  - `docs/core/26_snaptoken_hooks.md` (Hooks, die Tokens liefern)

---

## EN

### Purpose
This document describes ORÓMA’s **`core/fusion.py`** module: a **deterministic, headless-friendly** mechanism for **crossmodal fusion** (vision ↔ audio ↔ text ↔ meta). The output is a **`FusionPack`** that stores modality vectors (parts) plus symbolic tokens/concepts for similarity, linking, replay/dream, and explainability.

### Scope / Non-goals
- ✅ In scope: data structures (`ModalityVec`, `FusionPack`), `FusionEngine`, deterministic fallbacks, similarity/score, serialization.
- ❌ Out of scope: full integration paths in other subsystems, concrete DB tables, training/models.

### Terms
- **ModalityVec:** modality vector with metadata (`kind`, `vec`, `meta`).
- **FusionPack:** container of `modalities`, `tokens`, `concepts` (+ `created_ts`, `version`).
- **FusionEngine:** bridge to optional runtimes (text embedding/tokenizer and vision projection) with offline fallbacks.

### Architectural role
Conceptual flow:
**DeviceHub/Perception → Snap (atomic) → Fusion (optional) → FusionPack → similarity/linking/replay/dream**

Fusion is **optional**. Snap/SnapChain continue to work without it; fusion adds a stable crossmodal representation.

### Public API (current implementation in `core/fusion.py`)
#### Data classes
- `ModalityVec(kind: str, vec: List[float], meta: Dict[str, Any])`
- `FusionPack(modalities, tokens, concepts, created_ts, version)`
  - `to_dict()` / `from_dict()`
  - `to_json()` / `from_json()`

#### Engine
- `FusionEngine(llm_rt=None, vision_rt=None)`
  - `text_to_vec(text)` – text → vector (LLM embedding or deterministic SHA1 fallback)
  - `tokenize(text)` – text → tokens (LLM tokenizer or whitespace split)
  - `normalize_concepts(tokens)` – tokens → concepts (LLM or unique/lowercase)
  - `vision_to_vec(features)` – vision features → projected vector (vision_rt.project or fallback)
  - `build_fusion(text=None, vision_features=None, audio_features=None, extra_modalities=None) -> FusionPack`
  - `similarity(a, b, weights=None) -> float` – weighted cosine similarity per modality
  - `fuse(snaps, tokens) -> FusionPack` – high-level sensor fusion (kind="sensor")
  - `split(fusion_snap) -> (snap_vecs, tokens)` – inverse of `fuse()`
  - `score(fusion_snap) -> float` – 0..1 heuristic (modalities + tokens/concepts)

> Note: The module header mentions “concat/weighted” fusion strategies. The **current code** models fusion as a list of modality vectors plus similarity/score. A persistent `fused_vector` field is **not** present in the current implementation; consumers may concatenate deterministically if needed, outside this module.

### Deterministic fallbacks (offline/headless)
- **Text embedding fallback:** SHA1(text) → deterministic pseudo-vector of dimension `OROMA_EMBED_DIM` (default 128), optionally L2-normalized.
- **Tokenizer fallback:** whitespace split.
- **Concept normalization fallback:** unique + lowercase.

### Similarity (weighted cosine per modality)
`FusionEngine.similarity()`:
- collects modality kinds from both packs
- compares the first matching vector per kind
- computes cosine and returns a weighted average (`weights[kind]`, default 1.0)

### Score (0..1)
`score()` is a lightweight heuristic:
- more modalities increases the score (saturating)
- presence of tokens/concepts adds small bonuses

### Key environment variables
- `OROMA_FUSION_ENABLE=1|0`
- `OROMA_FUSION_MODE=concat|weighted` (documented in header; current code uses similarity weights)
- `OROMA_FUSION_W_VISION`, `OROMA_FUSION_W_AUDIO`, `OROMA_FUSION_W_TEXT`
- `OROMA_FUSION_NORMALIZE=1|0` (documented in header)
- `OROMA_EMBED_DIM=128`
- `OROMA_EMBED_NORM=1|0`

### Failure modes & robustness
- Optional runtime calls (`llm_rt.*`, `vision_rt.project`) are wrapped in `try/except` and use `log_guard.log_suppressed(...)`.
- On failures, the engine falls back deterministically and continues (no hard dependency).

### Code mapping
- Relevant file:
  - `core/fusion.py`
- Related core docs:
  - `docs/core/10_snap.md`
  - `docs/core/12_snaptoken.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/26_snaptoken_hooks.md`

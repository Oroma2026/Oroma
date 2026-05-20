# SnapToken – Symbolische Token-Ebene / Symbolic Token Layer

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt **SnapToken** als universelles, leichtgewichtiges Token-Objekt (Text/Vision/Audio/Motion/Meta) zur **symbolischen Repräsentation** und als Brücke zwischen Snap-/Fusion-Ebene und nachgelagerten Subsystemen (z. B. Policy, Replay, Linking). Basis ist `core/snaptoken.py` (v3.7.3).

### Scope / Nicht-Ziele
- ✅ In scope: Datenmodell (Felder), Tokenisierung (LLM oder Fallback), Fingerprint, Embedding/Feature-Vektor, Serialisierung (dict/blob), SQL-Row-Helper.
- ❌ Out of scope: konkrete Hook-Implementierungen (separat in `26_snaptoken_hooks.md`), Fusion-Mechanik (separat `15_fusion.md`), konkrete DB-Tabellen (nur konzeptuell).

### Begriffe
- **SnapToken:** universelles Token-Objekt mit `modality`, optional `text`, `token_ids`, `features`, optional `embedding` und `metadata`.
- **Modality:** Klassifizierung des Tokens: `text`, `vision`, `audio`, `motion`, `meta`.
- **token_ids:** Integer-IDs (entweder aus LLM-Tokenizer oder deterministischer Fallback-Hash).
- **Fingerprint:** stabiler Hash über wesentliche Token-Inhalte (inkl. metadata + Zeit + version), um Wiedererkennung/Dedup zu unterstützen.

### Architekturrolle
Typischer Fluss (konzeptuell):
**Perzeption/DeviceHub → (optional Fusion) → Snap → SnapToken (symbolisch) → Linking/Policy/Replay/Transfer**  
SnapTokens sind bewusst **klein** und robust, damit sie:
- bei fehlender LLM-Tokenisierung trotzdem funktionieren,
- als “symbolische Anker” in der DB/Telemetry genutzt werden können,
- und Modalitäten einheitlich behandeln.

### Datenmodell (aus `core/snaptoken.py`)
Pflicht-/Kernfelder:
- `uid` (hex) – eindeutige Token-ID
- `modality` – `text|vision|audio|motion|meta`
- `created_ts` – Erstellzeit (Unix-Sekunden)
- `fingerprint` – stabiler Hash

Optionale Inhalte:
- `text` – Textinhalt (für `text` und optional `meta`)
- `token_ids` – Liste von int (Tokenisierung)
- `features` – numerische Liste (z. B. Bewegungs-/Scorewerte)
- `embedding` – numerischer Vektor (wenn verfügbar)
- `model_hint` – Hinweis für Tokenizer/Embedding-Quelle
- `metadata` – frei strukturierbar (JSON)

### Invarianten & Robustheit
- **Modality-Sanitizing:** unbekannte `modality` wird defensiv zu `meta` normalisiert.
- **Numerische Felder:** `features/embedding` werden auf float, `token_ids` auf int bereinigt.
- **Best-effort Tokenisierung:** wenn `text` vorhanden und `token_ids` leer sind, wird automatisch tokenisiert.
- **Fingerprint immer konsistent:** wird in `__post_init__` berechnet und bei `from_dict` notfalls neu berechnet.

### Tokenisierung: LLM oder deterministischer Fallback
1) **Primär:** `_llm_tokenize_safe(text, model_hint)` versucht `core.llm_runtime.tokenize_text(...)`.
2) **Fallback:** Whitespace-Split und pro Token ein stabiler 32-bit Hash (BLAKE2b) → `token_ids`.

Das ist entscheidend für Headless-/Offline-Stabilität: SnapToken bleibt nutzbar auch ohne verfügbaren LLM-Tokenizer.

### Vektoren: Embedding vs Features
- `feature_vector(prefer_embedding=True, normalize=False)`:
  - nimmt `embedding`, wenn vorhanden (und bevorzugt), sonst `features`
  - optional L2-normalisiert
- `normalize_embedding_()` normalisiert `embedding` in-place (L2)

### Fingerprint-Definition (vereinfachte Sicht)
Der Fingerprint wird stabil aus folgenden Teilen gebildet:
- `modality`, `uid`
- optional `text`
- gerundete `features`, `token_ids`, gerundete `embedding`
- `metadata` (sortierte Keys)
- `created_ts`, `version`

→ Ergebnis ist ein stabiler Hash (`sha1` über gepackte Teile; intern `_stable_fingerprint`).

### Serialisierung
- `to_dict()` / `from_dict(...)` – JSON-freundlich; `from_dict` validiert/rekonstruiert Fingerprint.
- `as_blob()` / `from_blob(...)` – kompakte Byte-Repräsentation (MessagePack) für DB/IPC.

### SQL-Row Helper
`sql_row()` liefert eine generische Row:
- `(uid, modality, text_or_none, fingerprint, created_ts, blob)`

`from_row(...)` rekonstruiert den Token aus dieser Row.

### Bezug zum Code
- Relevante Datei:
  - `core/snaptoken.py`
- Nahe/anschließende Core-Dokus (geplant):
  - `docs/core/10_snap.md` (Snap)
  - `docs/core/15_fusion.md` (Fusion)
  - `docs/core/26_snaptoken_hooks.md` (Hooks für AV/Audio SnapTokens)

---

## EN

### Purpose
This document defines **SnapToken** as a universal, lightweight token object (text/vision/audio/motion/meta) used as a **symbolic representation layer** and as a bridge between the Snap/Fusion layer and downstream subsystems (policy, replay, linking). Based on `core/snaptoken.py` (v3.7.3).

### Scope / Non-goals
- ✅ In scope: data model (fields), tokenization (LLM or fallback), fingerprinting, embedding/feature vectors, serialization (dict/blob), SQL row helpers.
- ❌ Out of scope: hook implementations (see `26_snaptoken_hooks.md`), fusion mechanics (`15_fusion.md`), concrete DB schemas (conceptual only).

### Terms
- **SnapToken:** universal token object with `modality`, optional `text`, `token_ids`, `features`, optional `embedding`, and `metadata`.
- **Modality:** token class: `text`, `vision`, `audio`, `motion`, `meta`.
- **token_ids:** integer IDs (from LLM tokenizer or deterministic fallback hashing).
- **Fingerprint:** stable hash over token contents (including metadata + time + version) for recognition/dedup.

### Architectural role
Typical conceptual flow:
**Perception/DeviceHub → (optional Fusion) → Snap → SnapToken (symbolic) → Linking/Policy/Replay/Transfer**  
SnapTokens are intentionally **small and robust** so they remain usable even when LLM tokenization is unavailable.

### Data model (from `core/snaptoken.py`)
Core fields:
- `uid` (hex) – unique token id
- `modality` – `text|vision|audio|motion|meta`
- `created_ts` – creation time (unix seconds)
- `fingerprint` – stable hash

Optional content:
- `text`
- `token_ids`
- `features`
- `embedding`
- `model_hint`
- `metadata` (free-form JSON)

### Invariants & robustness
- **Modality sanitizing:** unknown modality is coerced to `meta`.
- **Numeric cleanup:** `features/embedding` → float, `token_ids` → int.
- **Best-effort tokenization:** if `text` exists and `token_ids` is empty, tokenization is attempted automatically.
- **Fingerprint consistency:** computed in `__post_init__`; re-validated in `from_dict`.

### Tokenization: LLM or deterministic fallback
1) Primary: `_llm_tokenize_safe(text, model_hint)` calls `core.llm_runtime.tokenize_text(...)`.
2) Fallback: whitespace split and stable 32-bit hash per token (BLAKE2b) → `token_ids`.

### Vectors: embedding vs features
- `feature_vector(prefer_embedding=True, normalize=False)`:
  - returns `embedding` if available, otherwise `features`
  - optional L2 normalization
- `normalize_embedding_()` normalizes the embedding in-place.

### Fingerprint definition (simplified)
Fingerprint is derived from:
- `modality`, `uid`
- optional `text`
- rounded `features`, `token_ids`, rounded `embedding`
- normalized `metadata` (sorted keys)
- `created_ts`, `version`

### Serialization
- `to_dict()` / `from_dict(...)` – JSON-friendly; validates/recomputes fingerprint as needed.
- `as_blob()` / `from_blob(...)` – compact byte representation (MessagePack) for DB/IPC.

### SQL row helper
`sql_row()` yields:
- `(uid, modality, text_or_none, fingerprint, created_ts, blob)`

`from_row(...)` reconstructs the token from that row.

### Code mapping
- Relevant file:
  - `core/snaptoken.py`
- Related core docs (planned):
  - `docs/core/10_snap.md`
  - `docs/core/15_fusion.md`
  - `docs/core/26_snaptoken_hooks.md`

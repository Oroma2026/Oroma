# Calculator → SnapChain Bridge / Calculator → SnapChain Bridge (Transfer)

## Öffentliche Referenzen / Public references (DOI / Repo)
- **Whitepaper (EN, reference):** https://doi.org/10.5281/zenodo.19596002
- **Whitepaper (DE, translation):** https://doi.org/10.5281/zenodo.19629298
- **Repository (Landing page):** https://codeberg.org/oromamaster/Oroma

> **Zitation / Citation:** Bitte die englische Referenzversion zitieren (EN DOI).  
> The German translation is provided for accessibility.

---

## DE

### Zweck
Dieses Dokument beschreibt `core/calc_to_snapchain.py`: eine **robuste Bridge** vom ORÓMA-Calculator (Tabellen `calculator_tasks` / `calculator_results`) hin zu einer **SnapChain-Logzeile** in `snapchains` mit `origin="calc/result"`. Ziel ist, mathematische Muster als **kanonische, deterministische Vektorrepräsentation** (Transfer-Wissen) früh im System zu verankern – ohne DB-Schema-Änderungen und ohne Crash-Risiko.

### Scope / Nicht-Ziele
- ✅ In scope: Aktivierung/Throttling, DB-Reads, deterministische Vektor-Kodierung (`_build_v`), JSON-Blob Format, SnapChain-Insert, optionale MetaSnap-Aggregation.
- ❌ Out of scope: UI/Orchestrator-Flows, SnapChain-Replay, Policy/Rules-Training, “semantische” Embeddings (hier bewusst nicht).

### Begriffe
- **Calculator Task/Result:** strukturierte Aufgaben und deren Lösungen (`expr`, `truth`, `got`, `correct`, `reward`, JSON-Meta).
- **SnapChain (calc/result):** eine SnapChain-Row, die eine Lösung als Vektor + Meta kapselt.
- **MetaSnap Aggregation:** optionales Zusammenfassen statistischer Signale pro Label (`calc:<type>[:<skill>]`) in `meta_snaps`.

### Architekturrolle
Konzeptuell:
**Calculator → (Task/Result) → deterministischer Vektor → SnapChain (origin="calc/result") → Dream/Linking/Transfer**

Diese Bridge ist bewusst “kanonisch”: Sie erzeugt keine freien Text-Embeddings, sondern einen festen, interpretierbaren Vektorraum, den spätere Subsysteme alignen können.

---

## Aktivierung & ENV
- `OROMA_CALC_SNAPCHAINS` (true|false) – Default: **true**
- `OROMA_CALC_SNAP_EVERY` (int>=1) – Default: **1** (jede Lösung wird geloggt); Throttling über `result_id % N`.
- `OROMA_CALC_SNAP_VDIM` (int>=16) – Default: **84** (Vektorlänge)
- `OROMA_CALC_METASNAP_AGG` (true|false) – Default: **true** (MetaSnap-Stats)

---

## SnapChain-Blob Format (JSON)
Das Modul schreibt `snapchains.blob` als kompaktes JSON (separator `,` `:`) im Format:

- `kind`: `"calc/result"`
- `v`: fester Vektor (Default 84D)
- `task_id`, `result_id`
- `ts`, `level`, `expr`
- `truth`, `got`, `correct`, `reward`
- `error_type` (optional)
- `truth_json` (optional dict)
- `got_json` (optional list/dict)
- `meta` (optional dict)

Zusätzlich werden in der SnapChain-Row gesetzt:
- `origin="calc/result"`
- `notes="Calculator result → SnapChain (transfer bridge)"`
- `version="v3.7.3"`
- `quality` (Heuristik)
- `weight` (Heuristik)

### Quality/Weight Heuristik
- korrekt: `quality=0.65`, `weight=0.35`
- falsch: `quality=0.15`, `weight=0.50`

Diese Werte sind bewusst konservativ: die Calc-Bridge soll Signal liefern, aber nicht andere Modalitäten dominieren.

---

## Deterministische Vektor-Kodierung (`_build_v`)
`_build_v(...)` erzeugt einen festen Vektor der Länge `_VDIM`. Er ist **deterministisch** und bewusst nicht “magisch semantisch”.

Vektor-Segmente (bei Default 84D; Rest bleibt 0):
- `[0..6)` Basisfeatures:
  - bias, correctness, reward,
  - truth (tanh-norm), got (tanh-norm), abs error (tanh-norm)
- `[6..16)` Task-Type OneHot (10):
  - arith, fill, sequence, fraction_add, fraction_sub, cmp, puzzle, const, logic, other
- `[16..26)` Level OneHot (10) (clamp 1..10)
- `[26..36)` Solution digit OneHot (10) (nur wenn truth “ganzzahlig”)
- `[36..46)` und `[46..56)` erste zwei Zahlen aus `expr` (mod 10 OneHot)

Normierung:
- `_norm_tanh(x, scale)` → `tanh(x/scale)` als robuste, saturierende Skalierung.

---

## DB Reads (robust)
- `_fetch_task(task_id)` liest `calculator_tasks` (id, ts, level, expr, truth, truth_json)
- `_fetch_result(result_id)` liest `calculator_results` (id, task_id, ts, got, correct, reward, error_type, got_json)

Beide Reads sind best-effort, nutzen kurze `with sql_manager.get_conn()` Contexts und geben bei Problemen `None` zurück (fail-safe).

---

## Public API
### `record_from_db(task_id, result_id) -> Optional[int]`
Hauptfunktion:
1) prüft Enable + Throttling (`result_id % SNAP_EVERY`)
2) lädt Task + Result
3) baut Vektor `v` + Blob
4) schreibt eine SnapChain-Row (`sql_manager.insert_snapchain(...)`)
5) optional: MetaSnap-Aggregation per Label

Rückgabe:
- `snapchain_id` oder `None`

---

## MetaSnap-Aggregation (optional)
Wenn `OROMA_CALC_METASNAP_AGG=true`:
- Label wird aus Type/Skill abgeleitet:
  - `label = f"calc:{typ}:{skill}"` oder `calc:{typ}`
- Upsert in `meta_snaps`:
  - nutzt `score = reward_avg`
  - `sources` enthält ein JSON-Payload (`kind="calc_metasnap"`, count, correct_count, reward_sum/avg, sample_expr, last_ts, last_task_id)

**Hinweis (ehrlich):** Dieser Teil nutzt direkte SQLite-Updates via `sql_manager.get_conn()` und ist bewusst “best-effort”, um den Calculator niemals zu blockieren.

---

## Fehlerfälle & Robustheit
- Throttling/JSON-Parsing sind komplett `try/except`-geschützt.
- Wenn SnapChain-Insert fehlschlägt, wird dennoch versucht, MetaSnap zu upserten; beides darf scheitern ohne Crash.
- Ziel: **Calculator darf nie crashen**.

---

## Bezug zum Code
- Relevante Datei:
  - `core/calc_to_snapchain.py`
- Verwandte Core-Dokus:
  - `docs/core/10_snap.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/14_meta_snap.md`
  - `docs/core/90_publication.md`

---

## EN

### Purpose
This document describes `core/calc_to_snapchain.py`: a **robust bridge** from ORÓMA’s Calculator tables (`calculator_tasks` / `calculator_results`) to a **snapchains** row with `origin="calc/result"`. The goal is to anchor mathematical patterns as a **canonical, deterministic vector representation** (transfer knowledge) early in the system—without schema changes and without crash risk.

### Scope / Non-goals
- ✅ In scope: enable/throttling, DB reads, deterministic vector encoding (`_build_v`), JSON blob format, snapchain insert, optional MetaSnap aggregation.
- ❌ Out of scope: UI/orchestrator flows, replay, policy/rules training, semantic embeddings (intentionally not used here).

### Terms
- **Calculator task/result:** structured problems and outcomes (`expr`, `truth`, `got`, `correct`, `reward`, JSON meta).
- **SnapChain (calc/result):** a snapchains row that packs a solution as vector + meta.
- **MetaSnap aggregation:** optional stats aggregation per label (`calc:<type>[:<skill>]`) into `meta_snaps`.

### Architectural role
Conceptually:
**Calculator → (task/result) → deterministic vector → SnapChain (origin="calc/result") → dream/linking/transfer**

This bridge is intentionally “canonical”: no free-form text embeddings, but a fixed, interpretable vector space that later subsystems may align with other modalities.

---

## Enable & ENV
- `OROMA_CALC_SNAPCHAINS` (true|false) – default **true**
- `OROMA_CALC_SNAP_EVERY` (int>=1) – default **1** (log every result); throttling via `result_id % N`.
- `OROMA_CALC_SNAP_VDIM` (int>=16) – default **84**
- `OROMA_CALC_METASNAP_AGG` (true|false) – default **true**

---

## SnapChain blob format (JSON)
`snapchains.blob` uses compact JSON:

- `kind`: `"calc/result"`
- `v`: fixed vector (default 84D)
- ids: `task_id`, `result_id`
- core fields: `ts`, `level`, `expr`, `truth`, `got`, `correct`, `reward`
- optional: `error_type`, `truth_json`, `got_json`, `meta`

Row fields:
- `origin="calc/result"`, `notes=...`, `version="v3.7.3"`, plus `quality` and `weight`.

### Quality/weight heuristic
- correct: `quality=0.65`, `weight=0.35`
- incorrect: `quality=0.15`, `weight=0.50`

---

## Deterministic vector encoding (`_build_v`)
Segments (default 84D; remaining dims are 0):
- `[0..6)` base features: bias, correctness, reward, normalized truth/got/abs error
- `[6..16)` task type one-hot (10)
- `[16..26)` level one-hot (10)
- `[26..36)` solution digit one-hot (10) for integer truths
- `[36..46)` & `[46..56)` first two integers extracted from `expr` (mod 10 one-hot)

Normalization uses `tanh(x/scale)`.

---

## DB reads (robust)
- `_fetch_task(task_id)` reads `calculator_tasks`
- `_fetch_result(result_id)` reads `calculator_results`

Both are best-effort and fail-safe.

---

## Public API
### `record_from_db(task_id, result_id) -> Optional[int]`
1) checks enable + throttling
2) loads task + result
3) builds vector + blob
4) inserts a snapchains row (`sql_manager.insert_snapchain`)
5) optional MetaSnap aggregation

Returns a `snapchain_id` or `None`.

---

## MetaSnap aggregation (optional)
When enabled:
- label derived from type/skill (`calc:<typ>[:<skill>]`)
- upsert into `meta_snaps` with:
  - `score = reward_avg`
  - JSON payload in `sources` (`kind="calc_metasnap"`, counters, sample expr, last ts/id)

**Note:** this part uses direct sqlite updates via `sql_manager.get_conn()` and is intentionally best-effort.

---

## Failure modes & robustness
- parsing and throttling are fully `try/except` protected
- snapchain insert failures do not crash the calculator
- overall goal: **never crash the calculator path**

---

## Code mapping
- Relevant file:
  - `core/calc_to_snapchain.py`
- Related core docs:
  - `docs/core/10_snap.md`
  - `docs/core/22_snapchain.md`
  - `docs/core/14_meta_snap.md`
  - `docs/core/90_publication.md`

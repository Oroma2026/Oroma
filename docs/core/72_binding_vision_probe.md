# Binding Stage A – Vision Repetition Probe (ORÓMA)

## DE – Zweck
Diese Stage-A-Probe macht **Wiederholung** im Vision-Stream messbar.  
Wiederholung ist in biologischen Systemen eine **Abkürzung**: häufige Muster können verdichtet/komprimiert und später effizienter gebunden oder genutzt werden.

**Wichtig:** Stage A ist *measure-only*. Es werden **keine** Graph-Edges geschrieben und **keine** neuen Tabellen eingeführt.

## EN – Purpose
This Stage-A probe measures **repetition** in the vision stream.  
In biological cognition, repetition is a **shortcut**: frequent patterns can be compressed and later used as stable anchors for binding and consolidation.

**Important:** Stage A is *measure-only*. It writes **no** graph edges and adds **no** new tables.

---

## Data Source / Datenquelle
- `snapchains` with `origin = "vision/token"`
- The `blob` may be stored as **BLOB(bytes)** or **TEXT(str)** (the tool accepts both).
- Current observed state: `vision/token` has **empty meta** (no labels / no bbox).

---

## Keying Strategy / Schlüsselbildung (v2)
Because `meta` is empty, repetition is measured via **coarse vector fingerprints**:

- `fp_dims` (default 32): number of leading vector dimensions used
- `fp_decimals` (default 2): rounding precision per dimension

This is intentionally coarse to tolerate small noise and reveal repetition density.

ENV / CLI:
- `OROMA_VISION_BIND_FP_DIMS` / `--fp-dims`
- `OROMA_VISION_BIND_FP_DECIMALS` / `--fp-decimals`

---

## Metrics written to stats.db (stats_points)
The probe writes these `series` (24h window):

- `binding.v.events.count_24h`
- `binding.v.keys.unique_24h`
- `binding.v.keys.repeat_ge_2_24h`
- `binding.v.keys.repeat_ge_3_24h`
- `binding.v.keys.repeat_ge_5_24h`
- `binding.v.fp.dims_24h`
- `binding.v.fp.decimals_24h`
- `binding.v.meta.empty_24h`
- `binding.v.meta.has_any_24h`
- `binding.v.meta.has_label_24h`
- `binding.v.meta.has_bbox_24h`

Interpretation:
- Higher `repeat_ge_3` / `repeat_ge_5` indicates stable recurring visual patterns.
- If `meta.empty_24h ≈ events`, the system is operating on pure vectors (expected in current setup).

---

## How to run (manual)
```bash
cd /opt/ai/oroma
PYTHONPATH=/opt/ai/oroma OROMA_DBW_ENABLE=1 python3 tools/vision_binding_probe.py
```

Inspect latest points:
```bash
sqlite3 /opt/ai/oroma/data/stats.db \
"select datetime(ts,'unixepoch','localtime'), series, value
 from stats_points
 where series like 'binding.v.%'
 order by ts desc
 limit 60;"
```

---

## Next Step (Stage B – later)
Stage B should only start after Stage A stabilizes for 24–72h.

A safe rule is to materialize only patterns with:
- `repeat_ge_3` (or stricter `repeat_ge_5`)
- bounded Top-K per day
- dedupe + noise-gates

No Stage B changes are applied in this document.

---

## DE – Interpretation der Kennzahlen (praktisch)
Die Vision-Probe schreibt **nur Messwerte**. Du kannst sie direkt als „Wie viel Wiederholung existiert wirklich?“ lesen.

**Begriffe:**
- **ev** (`binding.v.events.count_24h`)  
  Anzahl der Vision-Token-Events im 24h-Fenster.
- **uq** (`binding.v.keys.unique_24h`)  
  Anzahl **einzigartiger** Fingerprint-Keys im 24h-Fenster.
- **r2 / r3 / r5** (`binding.v.keys.repeat_ge_{2,3,5}_24h`)  
  Wie viele Keys tauchen **mindestens** 2× / 3× / 5× auf.  
  Das sind deine **Wiederholungsanker**.
- **fp=Xd/Ydec** (`binding.v.fp.dims_24h`, `binding.v.fp.decimals_24h`)  
  Fingerprint-Auflösung: erste **X** Vektor-Dimensionen, gerundet auf **Y** Dezimalstellen.
- **meta_empty** (`binding.v.meta.empty_24h`)  
  Wie viele Vision-Token-Events **ohne Meta-Felder** (Labels/BBox/IDs) vorliegen.

**Wie du das liest:**
- Wenn **uq ≈ ev**, ist der Stream sehr variabel (viel Neues).  
- Wenn **r3/r5** steigen, entstehen stabile, wiederkehrende Muster (gut für spätere Kompression/Binding).
- **meta_empty ≈ ev** heißt: aktuell ist der Vision-Stream „vector-only“. Das ist für Stage A okay – Stage B muss dann vorsichtiger sein.

**Wann Stage B (Materialisierung) überhaupt sinnvoll ist:**
- mindestens 24–72h Stage-A-Daten vorhanden
- `r3` stabil > 0 (besser: `r5` > 0)
- Top-K/Tag begrenzen (z.B. max 10 neue Edges/Tag)
- Dedupe + Noise-Gates (keine Massenerzeugung)

**Optionales Tuning (vorsichtig):**
- `fp_decimals`: **2 → 1** erhöht Wiederholung (mehr „Clustering“), kann aber False-Matches erhöhen.
- `fp_dims`: **32 → 24** macht Fingerprints robuster gegen Drift, aber weniger spezifisch.

---

## EN – Interpreting the numbers (practical)
This probe is **measure-only**. You can read it as: “How much real repetition exists?”

**Terms:**
- **ev** (`binding.v.events.count_24h`) – number of vision-token events in the last 24h.
- **uq** (`binding.v.keys.unique_24h`) – number of **unique** fingerprint keys in the last 24h.
- **r2 / r3 / r5** (`binding.v.keys.repeat_ge_{2,3,5}_24h`) – keys that repeat at least 2× / 3× / 5× (your **repetition anchors**).
- **fp=Xd/Ydec** (`binding.v.fp.dims_24h`, `binding.v.fp.decimals_24h`) – fingerprint resolution (first X dims, rounded to Y decimals).
- **meta_empty** (`binding.v.meta.empty_24h`) – events without meta fields (labels/bbox/ids).

**How to read it:**
- If **uq ≈ ev**, the stream is highly variable (lots of novelty).
- If **r3/r5** increase, stable recurring patterns exist (good for later compression/binding).
- **meta_empty ≈ ev** means the stream is currently “vector-only” (OK for Stage A, Stage B must be conservative).

**When Stage B (materialization) is reasonable:**
- at least 24–72h of Stage-A data
- `r3` stably > 0 (better: `r5` > 0)
- strict daily Top-K (e.g. ≤10 new edges/day)
- dedupe + noise gates (avoid mass edge generation)

**Optional tuning (careful):**
- `fp_decimals`: **2 → 1** increases repetition (more clustering) but may increase false matches.
- `fp_dims`: **32 → 24** is more drift-tolerant but less specific.

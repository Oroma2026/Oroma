# ORÓMA – Compression Probe (Stage A) / Kompressions-Probe (Stufe A)

**Purpose / Zweck:**  
Measure-only probe to understand why `compressed_share = 0.00%`.

## DE – Was wird gemessen?
Stage A misst zwei Achsen:

1) **Redundanz in SnapChains** (24h)
- Wiederholte Fingerprints → *gibt es überhaupt komprimierbares Material?*

2) **Materialisierung von `compressed_*`**
- `meta_snaps.label LIKE 'compressed_%'` (DreamForgetting erzeugt diese Marker)
- `object_nodes.label LIKE 'compressed_%'` (**entscheidend** für `compressed_share` in der Learning UI)

## DE – Metriken (stats_points)
- `compress.a.events_24h` – gescannte Chains
- `compress.a.unique_fp_24h` – eindeutige Fingerprints
- `compress.a.repeat_ge_2_24h / _ge_3_24h / _ge_5_24h` – Wiederholungsanker
- `compress.a.metasnap.compressed_24h` – Kompressionsmarker im MetaSnap-Layer
- `compress.a.objnode.compressed_24h` – Kompressionskonzepte im ObjectGraph (UI relevant)
- `compress.a.gate.missing_vec_24h` – Chains ohne Vektor
- `compress.a.origin.<origin>.repeat_ge_3_24h` – Redundanz pro Top-Origin

## DE – Wie interpretiert man das?
- Wenn `repeat_ge_3` ≈ 0 → Input ist zu divers → Kompression kann realistisch 0 bleiben.
- Wenn `repeat_ge_3` hoch, aber `objnode.compressed_24h` = 0 →
  Kompression wird nicht in den ObjectGraph materialisiert (Pipeline/Gate Problem).
- Wenn `objnode.compressed_24h` > 0, aber `compressed_share` bleibt 0 →
  Messpfad/UI/Query prüfen (ObjectGraph-Fenster, created_ts).

## EN – What is measured?
Two axes:
1) SnapChain redundancy (repeated coarse fingerprints)
2) Materialization of `compressed_*` in MetaSnaps and ObjectGraph

## EN – Why this matters
`compressed_share` in Learning UI is derived from ObjectGraph nodes:
`object_nodes.created_ts >= window AND label LIKE 'compressed_%'`.
So we must explicitly track whether such nodes are created.

## Stage B (later)
Stage B would materialize compression carefully (Top-K per night, dedupe, strict gates).
Stage A is strictly measure-only.
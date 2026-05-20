# ORÓMA Patch 2026-05-06 · Context Neighbor Anchors A3 / Bridge Probe A6

Baseline: `/mnt/data/oroma_20260506_071733_with_db.zip`

Changed files:
- `core/nmr_synaptic_plasticity.py`
- `tools/synapses_origin_probe.py`
- `tools/synapses_bridge_probe.py`
- `tools/synapses_bridge_materializer.py`
- `.env.systemd`

Purpose:
- Add medium-granularity `synaptic_context` anchors that sit between generic anchors and singleton refs:
  - `neighbor_time_bucket:*`
  - `episode_sequence_bucket:*`
  - `snapchain_nearby_bucket:*`
  - `origin_time_bucket:*`
- Keep bridge materialization manual-only and restricted to medium candidates.
- Keep generic anchors (`scope:*`, `event_type:*`, `scope_event_type:*`) excluded from bridge writes.

Validation performed:
- `python3 -m py_compile` on all changed Python files
- `--help` smoke test on changed tools
- anchor generation smoke test for NMR helper
- materializer source allow-list smoke test

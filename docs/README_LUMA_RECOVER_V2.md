# ORÓMA – PTZ Luma-Recovery v2 (Guardrail Fix)

**Baseline:** `oroma_20260203_174639_with_db.zip`

## Beobachtung (Live-Test)
Im Live-Test kam es vor, dass der One-Shot-Run zwar `mode=luma_recover` meldet,
aber kein Move ausgefuehrt wird (`action=""`, `moved=false`).

Das ist fuer die Autonomie unguenstig, weil der Guardrail dann nicht sichtbar
"anschiebt" und die Kamera im dunklen Bereich kleben bleiben kann.

## Aenderungen
1) **Stabiler Reason fuer UI/Logs**
   - Statt detailreicher Strings wird nun klar zwischen
     - `luma_dark` und
     - `luma_bright`
     unterschieden.

2) **Produktions-Guard im `luma_recover`-Mode**
   - Wenn `mode == luma_recover`, aber `luma_recover_remaining <= 0` (inkonsistenter State),
     wird ein minimaler 1-Step-Burst erzwungen.
   - Dadurch entsteht immer mindestens ein PTZ-Impuls, bevor der Cooldown/Hold wieder greift.

## Dateien
- `core/ptz_attention_loop.py`
- `ptz_attention_loop_luma_v2.diff`

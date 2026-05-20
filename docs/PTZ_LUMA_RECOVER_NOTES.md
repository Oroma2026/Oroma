# ORÓMA – PTZ Luma-Recovery (Brightness Guardrail)

## Ziel
Wenn die Kamera in eine *dauerhaft* extrem dunkle (oder extrem helle) Szene gerät
(z.B. Boden/unter Tisch / Decke mit LED-Spot), soll ORÓMA autonom wieder eine
nutzbare Sicht herstellen, **ohne** DB-Abhängigkeit und ohne hektisches Verhalten.

## Umsetzung (Layer-Logik)
**Priorität (Mode):**
Threat > Probe > SpeechGuard > **LumaRecover** > Fixate > Scan > Orient

**Trigger:**
- Berechne pro Tick die mittlere Helligkeit (Luma) aus dem bereits vorhandenen
  Downsample (`cur_small`, typ. 64×36).
- Glätte per EMA (`luma_ema`) zur Robustheit.
- Wenn `luma_ema <= LUMA_LOW` *länger als HOLD_SEC* und Cooldown erfüllt:
  → `mode = luma_recover` (ein Tilt-Nudge Richtung Neutralposition).

## Performance
- O(n) nur über ~2304 Bytes pro Tick (sum(bytes)) → sehr schnell.
- Keine OpenCV-Operationen für Luma (nur optional Sharpness wie bisher).

## ENV Tuning
- `OROMA_PTZ_LUMA_ENABLE` (Default: 1)
- `OROMA_PTZ_LUMA_EMA_ALPHA` (Default: 0.15)
- `OROMA_PTZ_LUMA_LOW` (Default: 0.12)
- `OROMA_PTZ_LUMA_HIGH` (Default: 0.18)
- `OROMA_PTZ_LUMA_HOLD_SEC` (Default: 2)
- `OROMA_PTZ_LUMA_RECOVER_COOLDOWN_SEC` (Default: 10)
- `OROMA_PTZ_LUMA_RECOVER_AMOUNT` (Default: 1)

## Telemetrie (metrics)
- `ptz:luma`, `ptz:luma_ema`, `ptz:low_light`

## Datei(e)
- `core/ptz_attention_loop.py` – erweitert um Luma-Recovery Mode & Metrics.

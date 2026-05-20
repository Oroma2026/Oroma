# ORÓMA PTZ – Luma-Recover + Scan-Stability (v3)

## Baseline
- ZIP: `oroma_20260204_210622_with_db.zip`
- Datei: `core/ptz_attention_loop.py`

## Zweck
Stabilisiert das Zusammenspiel von `luma_recover` und `scan`, damit der Scan **nicht** sofort wieder in sehr dunkle Tilt-Zonen (Boden/Untertisch) fährt.

## Änderungen (minimal-invasiv)
1. **Scan Tilt Soft-Band** (per ENV): Scan meidet standardmäßig extreme Tilt-Bereiche.
2. **Dark-Guard**: Wenn `luma` noch "dark-ish" ist, wird ein `scan`-`down` verhindert (sonst Oszillation).
3. **Luma-Recover Fixate**: Nach einem Recover-Move wird `fix_until_ts` auf +2s gesetzt, damit Auto-Exposure/Gain stabilisieren kann.

## Neue ENV (optional)
- `OROMA_PTZ_SCAN_TILT_SOFT_MIN_FRAC=0.20`
- `OROMA_PTZ_SCAN_TILT_SOFT_MAX_FRAC=0.12`
- `OROMA_PTZ_SCAN_TILT_SOFT_DARK_BOOST=0.15`

## Test (1 Terminal)
```bash
sudo systemctl restart oroma.service oroma-orchestrator.service
for i in $(seq 1 8); do
  sudo -u oroma PYTHONPATH=/opt/ai/oroma python3 /opt/ai/oroma/core/ptz_attention_loop.py --once --verbose | tail -n 25
  sleep 1
done
```

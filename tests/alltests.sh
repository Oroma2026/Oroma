#!/usr/bin/env bash
# =============================================================================
# ORÓMA v3.5 – Gesamttest-Suite
# Pfad:    /opt/ai/oroma/tools/alltests.sh
# Zweck:   Führt alle wichtigen Tests nacheinander aus (Simulation, Wrapper, UI)
# Stand:   2025-09-21
# =============================================================================
set -euo pipefail

BASE="/opt/ai/oroma"
LOG_DIR="$BASE/logs"
TS="$(date +%Y-%m-%d_%H-%M-%S)"
OUT="$LOG_DIR/alltests_$TS.log"

mkdir -p "$LOG_DIR"

echo "==============================================" | tee -a "$OUT"
echo "🧪 Starte ORÓMA v3.5 – Gesamttestsuite" | tee -a "$OUT"
echo "Basis: $BASE" | tee -a "$OUT"
echo "Logdatei: $OUT" | tee -a "$OUT"
echo "Zeit: $TS" | tee -a "$OUT"
echo "==============================================" | tee -a "$OUT"

cd "$BASE"

# Virtuelle Umgebung aktivieren, falls vorhanden
if [ -d "venv" ]; then
  source venv/bin/activate
fi

RC_ALL=0

# --- 1. Simulationstest (Learning Cycle) ---
echo "➡️  [1/4] Simulationstest (Learning)" | tee -a "$OUT"
if ./tools/sim_learntest.sh >>"$OUT" 2>&1; then
  echo "   ✅ Simulationstest OK" | tee -a "$OUT"
else
  echo "   ❌ Simulationstest FEHLER" | tee -a "$OUT"
  RC_ALL=1
fi

# --- 2. Wrapper-Test ---
echo "➡️  [2/4] Wrapper-Test (CPU/Hailo/DeGirum)" | tee -a "$OUT"
if ./tools/wrappertest.sh >>"$OUT" 2>&1; then
  echo "   ✅ Wrapper-Test OK" | tee -a "$OUT"
else
  echo "   ❌ Wrapper-Test FEHLER" | tee -a "$OUT"
  RC_ALL=1
fi

# --- 3. UI-Selftest ---
echo "➡️  [3/4] UI-Selftest (HTTP-Routen)" | tee -a "$OUT"
if python3 tools/uiselftest.py >>"$OUT" 2>&1; then
  echo "   ✅ UI-Selftest OK" | tee -a "$OUT"
else
  echo "   ❌ UI-Selftest FEHLER" | tee -a "$OUT"
  RC_ALL=1
fi

# --- 4. Exporttests (optional) ---
if [ -f "./tests/test_hailo_export.py" ]; then
  echo "➡️  [4/4] Export-Tests (Hailo/DeGirum Simulation)" | tee -a "$OUT"
  if pytest -q tests/test_hailo_export.py >>"$OUT" 2>&1; then
    echo "   ✅ Export-Tests OK" | tee -a "$OUT"
  else
    echo "   ❌ Export-Tests FEHLER" | tee -a "$OUT"
    RC_ALL=1
  fi
else
  echo "➡️  [4/4] Export-Tests übersprungen (Datei fehlt)" | tee -a "$OUT"
fi

echo "==============================================" | tee -a "$OUT"
if [ $RC_ALL -eq 0 ]; then
  echo "🎉 Gesamttestsuite erfolgreich bestanden!" | tee -a "$OUT"
else
  echo "⚠️  Gesamttestsuite hatte FEHLER – siehe Logdatei: $OUT" | tee -a "$OUT"
fi
echo "==============================================" | tee -a "$OUT"

exit $RC_ALL
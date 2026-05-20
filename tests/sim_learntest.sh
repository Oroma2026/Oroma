#!/usr/bin/env bash
# =============================================================================
# ORÓMA v3.5 – sim_learntest.sh
# Pfad:    /opt/ai/oroma/tools/sim_learntest.sh
# Zweck:   Führt gezielt den Test test_sim_learning.py mit pytest aus.
# Stand:   2025-09-21
# =============================================================================
set -euo pipefail

BASE="/opt/ai/oroma"

cd "$BASE"

echo "========================================"
echo "🧪 Starte ORÓMA v3.5 – Simulation Learning Test"
echo "----------------------------------------"

# Virtuelle Umgebung aktivieren, falls vorhanden
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "[INFO] Virtuelle Umgebung aktiviert."
fi

# Test ausführen
pytest -q tests/test_sim_learning.py

RC=$?

echo "----------------------------------------"
if [ $RC -eq 0 ]; then
    echo "✅ Simulation Learning Test erfolgreich."
else
    echo "❌ Fehler im Simulation Learning Test. (rc=$RC)"
fi
echo "========================================"

exit $RC
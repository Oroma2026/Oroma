#!/usr/bin/env bash
# =============================================================================
# ORÓMA v3.5 – Wrapper-Testlauf
# Pfad:    /opt/ai/oroma/tools/wrappertest.sh
# Zweck:   Führt gezielt alle Wrapper-Tests aus (Hailo, DeGirum, CPU-Fallback)
# Stand:   2025-09-21
# =============================================================================
set -euo pipefail

BASE="/opt/ai/oroma"

echo "=============================================="
echo "🔍 Starte Wrapper-Tests für ORÓMA v3.5"
echo "Basis: $BASE"
echo "=============================================="

cd "$BASE"

# Virtuelle Umgebung aktivieren, falls vorhanden
if [ -d "venv" ]; then
  source venv/bin/activate
fi

# Pytest mit Marker "wrapper" starten
pytest -q -m wrapper tests/test_oroma_wrapper.py

RC=$?
echo "=============================================="
if [ $RC -eq 0 ]; then
  echo "✅ Wrapper-Tests erfolgreich abgeschlossen."
else
  echo "❌ Wrapper-Tests fehlgeschlagen. Bitte Logs prüfen."
fi
echo "=============================================="

exit $RC
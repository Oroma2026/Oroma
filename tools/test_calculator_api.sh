#!/bin/bash
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/test_calculator_api.sh
# Projekt: ORÓMA
# Version: v3.5patch1
# Stand:   2025-09-23
#
# Zweck:
#   - Testet die Calculator-API-Endpunkte:
#       /calculator/api/new_task
#       /calculator/api/solve
#       /calculator/api/tasks
#       /calculator/api/results
#   - Nutzt curl (JSON POST/GET)
# =============================================================================

BASE_URL="http://127.0.0.1:8080"
TOKEN_HEADER=""
# Falls du Token brauchst:
# TOKEN_HEADER="-H 'X-OROMA-TOKEN: dein_token'"

echo "===> 1. Neue Aufgabe erstellen"
resp=$(curl -s $TOKEN_HEADER -X POST \
  -H "Content-Type: application/json" \
  -d '{"level":1}' \
  "$BASE_URL/calculator/api/new_task")

echo "Antwort: $resp"
task_id=$(echo "$resp" | jq -r '.task.id')
truth=$(echo "$resp" | jq -r '.task.truth')

echo "===> 2. Aufgabe lösen (korrekt)"
resp=$(curl -s $TOKEN_HEADER -X POST \
  -H "Content-Type: application/json" \
  -d "{\"task_id\": $task_id, \"got\": $truth}" \
  "$BASE_URL/calculator/api/solve")
echo "Antwort: $resp"

echo "===> 3. Aufgabenliste abrufen"
curl -s $TOKEN_HEADER "$BASE_URL/calculator/api/tasks?limit=5" | jq .

echo "===> 4. Ergebnisse abrufen"
curl -s $TOKEN_HEADER "$BASE_URL/calculator/api/results?limit=5" | jq .
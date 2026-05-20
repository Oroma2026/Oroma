#!/usr/bin/env bash
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/oroma_orchestrator_cutover_modeB.sh
# Projekt: ORÓMA
# Version: v1.0 – Cutover auf Orchestrator Mode B (Legacy Timer deaktivieren)
# Stand:   2025-12-25
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# -----
#   Stellt das Livesystem sauber auf "Mode B" um:
#   - Orchestrator (serial workers) läuft
#   - Legacy systemd Timer/Services werden gestoppt & deaktiviert
#   - Optional: unübliche Exec-Bits auf systemd Drop-In .conf Dateien entfernen
#
# Warum?
# ------
#   Wenn alte Timer/Services parallel zum Orchestrator laufen, hast du:
#     - unnötige Doppelstarts
#     - mehr SQLite Writer-Kollisionen ("database is locked")
#     - höheres CPU/RAM/IO auf dem Pi
#
# Nutzung
# -------
#   sudo bash /opt/ai/oroma/tools/oroma_orchestrator_cutover_modeB.sh
#
# Hinweis
# -------
#   Dieses Skript löscht keine Daten. Es stoppt & deaktiviert nur Dienste/Timer.
#   Du kannst später jederzeit wieder per `systemctl enable --now ...` reaktivieren.
# =============================================================================

set -euo pipefail

LEGACY_UNITS=(
  oroma-dream.timer oroma-dream.service
  oroma-kpi.timer oroma-kpi.service
  oroma-energy.timer oroma-energy.service
  oroma-stats.timer oroma-stats.service
  oroma-social.timer oroma-social.service
  oroma-policy.timer oroma-policy.service
  oroma-exportgate.timer oroma-exportgate.service
  oroma-archive.timer oroma-archive.service
  oroma-forgetting.timer oroma-forgetting.service
  oroma-train-snake.timer oroma-train-snake.service
)

echo "[cutover] Stop/Disable legacy units (ignore missing units)..."
for u in "${LEGACY_UNITS[@]}"; do
  if systemctl list-unit-files | awk '{print $1}' | grep -qx "$u"; then
    echo "  - disable --now $u"
    systemctl disable --now "$u" || true
    systemctl stop "$u" || true
  else
    echo "  - skip (not installed): $u"
  fi
done

echo "[cutover] Reset failed states..."
systemctl reset-failed || true

# Optional: systemd drop-ins (*.conf) sollten nicht executable sein.
DROPIN_DIR="/etc/systemd/system/oroma.service.d"
if [[ -d "$DROPIN_DIR" ]]; then
  echo "[cutover] Fix permissions: $DROPIN_DIR/*.conf -> 0644"
  chmod 0644 "$DROPIN_DIR"/*.conf 2>/dev/null || true
fi

echo "[cutover] Reload systemd daemon..."
systemctl daemon-reload

echo "[cutover] Orchestrator status:"
systemctl status oroma-orchestrator.service --no-pager || true

echo "[cutover] Done."

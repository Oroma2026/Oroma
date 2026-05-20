#!/usr/bin/env bash
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/usb_noautosuspend_apply.sh
# Projekt: ORÓMA – USB Runtime-PM Stabilizer (No Autosuspend)
# Version: v3.7.3
# Stand:   2026-01-30
# Autor:   Jörg + GPT-5.2 Thinking
# =============================================================================
#
# Zweck
# -----
# Setzt USB Runtime Power Management für ausgewählte Geräte auf "on".
# Dies ist ein defensiver Fallback zusätzlich zur udev-Regel
#   /etc/udev/rules.d/99-oroma-usb-noautosuspend.rules
#
# Zielgeräte
# ----------
# - EMEET PIXY: idVendor=328f, idProduct=00c0
# - Jabra Speak2 55 MS: idVendor=0b0e, idProduct=ae6a
#
# Verhalten
# ---------
# - best-effort: wenn sysfs Pfade fehlen, wird nicht gefailt
# - sichtbar: schreibt klare Logzeilen auf stdout/stderr (systemd journal)
#
# Wichtiger Hinweis
# -----------------
# "over-current" Kernel-Meldungen sind primär Hardware/Power/Hub/Port Themen.
# Dieses Skript kann nur Autosuspend/Runtime-PM entschärfen, nicht Overcurrent.
#
# =============================================================================
set -euo pipefail

apply_one() {
  local vid="$1" pid="$2" label="$3"
  local found=0
  for dev in /sys/bus/usb/devices/*; do
    [[ -f "$dev/idVendor" && -f "$dev/idProduct" ]] || continue
    local v p
    v=$(cat "$dev/idVendor" 2>/dev/null || true)
    p=$(cat "$dev/idProduct" 2>/dev/null || true)
    [[ "$v" == "$vid" && "$p" == "$pid" ]] || continue
    found=1

    if [[ -w "$dev/power/control" ]]; then
      echo "on" > "$dev/power/control" || true
      echo "[usb_noautosuspend] $label: set power/control=on ($dev)"
    else
      echo "[usb_noautosuspend] $label: power/control not writable ($dev)" >&2
    fi

    # Best-effort: disable autosuspend delay if possible
    if [[ -w "$dev/power/autosuspend" ]]; then
      echo "-1" > "$dev/power/autosuspend" 2>/dev/null || true
      echo "[usb_noautosuspend] $label: set power/autosuspend=-1 ($dev)"
    fi
    if [[ -w "$dev/power/autosuspend_delay_ms" ]]; then
      echo "-1" > "$dev/power/autosuspend_delay_ms" 2>/dev/null || true
      echo "[usb_noautosuspend] $label: set power/autosuspend_delay_ms=-1 ($dev)"
    fi
  done

  if [[ $found -eq 0 ]]; then
    echo "[usb_noautosuspend] $label: device not present (vid=$vid pid=$pid)"
  fi
}

apply_one "328f" "00c0" "EMEET PIXY"
apply_one "0b0e" "ae6a" "Jabra Speak2 55 MS"

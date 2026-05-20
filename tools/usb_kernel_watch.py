#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/usb_kernel_watch.py
# Projekt: ORÓMA – Headless Lern-KI (Edge)
# Version: v3.7.3
# Stand:   2026-01-30
# Autor:   Jörg + GPT-5.2 Thinking
# =============================================================================
#
# ZWECK
# -----
# Dieses Tool macht USB/Kernel-Probleme sichtbar, die sich sonst als scheinbar
# "zufällige" ORÓMA-Ausfälle äußern (Video hängt, PTZ reagiert nicht, Audio
# wird instabil). Typische Ursachen werden im Kernel-Log protokolliert:
#   - over-current change
#   - USB disconnect / reset / re-enumeration
#   - uvcvideo: Failed to resubmit video URB (-19)
#   - ähnliche UVC/USB/XHCI Fehlermuster
#
# ORÓMA-Kontext
# ------------
# ORÓMA selbst kann bei solchen Ereignissen nur eingeschränkt reagieren.
# Entscheidend ist: Die Ursache MUSS sichtbar sein (keine stillen Fehler).
# Dieses Tool liest das Kernel-Journal best-effort, filtert relevante Zeilen
# und schreibt sie in:
#   1) Log:   /opt/ai/oroma/logs/usb_kernel_watch.log
#   2) State: /opt/ai/oroma/data/state/usb_kernel_watch.json
#
# Die UI (ui/video_ui.py) kann den State lesen und im Video-Tab einen Banner
# anzeigen. Das ist bewusst *entkoppelt* vom Video/P... wodurch auch bei
# Kamera-Stall klar bleibt, ob USB/KERNEL beteiligt ist.
#
# WICHTIGE EIGENSCHAFTEN
# ----------------------
# - Headless: keinerlei GUI-Abhängigkeiten.
# - Best-effort: darf NIE das System stören; Fehler werden in stderr
#   protokolliert, aber Exit-Codes sind dennoch sinnvoll.
# - Minimal invasiv: liest nur, schreibt nur eigene Log/State-Dateien.
# - Keine Log-Flut: Es werden NUR "Alert"-Zeilen geschrieben (Filter).
# - Reboot-sicher: Boot-ID wird genutzt; bei Boot-Wechsel wird die Cursor-Logik
#   zurückgesetzt.
#
# SYSTEMD
# -------
# Empfohlen ist ein Timer (oneshot) in kurzen Intervallen (z.B. 20-60s).
# Die passenden Units liegen im Projekt unter systemd/.
#
# ENV
# ---
#   OROMA_LOG_DIR        Default: /opt/ai/oroma/logs
#   OROMA_STATE_DIR      Default: /opt/ai/oroma/data/state
#   OROMA_USBKW_MAX_LINES Default: 800   (Schutz gegen zu große Journal-Ausgaben)
#   OROMA_USBKW_SINCE_SEC Default: 600   (Fallback-Fenster, wenn noch kein Cursor)
#
# CLI
# ---
#   python3 /opt/ai/oroma/tools/usb_kernel_watch.py --once
#
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import pwd
import grp
from typing import Any, Dict, List, Optional, Tuple


# --- Defaults / Paths ---------------------------------------------------------
LOG_DIR = os.environ.get("OROMA_LOG_DIR", "/opt/ai/oroma/logs")
STATE_DIR = os.environ.get("OROMA_STATE_DIR", "/opt/ai/oroma/data/state")
LOG_PATH = os.path.join(LOG_DIR, "usb_kernel_watch.log")
STATE_PATH = os.path.join(STATE_DIR, "usb_kernel_watch.json")

try:
    MAX_LINES = int(os.environ.get("OROMA_USBKW_MAX_LINES", "800"))
except Exception:
    MAX_LINES = 800

try:
    FALLBACK_SINCE_SEC = int(os.environ.get("OROMA_USBKW_SINCE_SEC", "600"))
except Exception:
    FALLBACK_SINCE_SEC = 600


# --- Filter Patterns (ALERT only) --------------------------------------------
# Ziel: Nur echte Problem-Signale, keine Boot-Enumerations.
_ALERT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("over-current", re.compile(r"over-current", re.IGNORECASE)),
    ("disconnect", re.compile(r"\bUSB disconnect\b", re.IGNORECASE)),
    ("reset", re.compile(r"\breset\b.*\busb\b|\busb\b.*\breset\b", re.IGNORECASE)),
    ("uvc-urb", re.compile(r"Failed to resubmit.*URB.*\(-19\)", re.IGNORECASE)),
    ("uvc", re.compile(r"\buvcvideo\b.*(fail|error|timeout|cannot|broken)", re.IGNORECASE)),
]

# Zusätzlich: USB/XHCI "error"-Muster, aber nur wenn wirklich error-like.
_GENERIC_ERR = re.compile(r"\b(xhci|usb|uvcvideo)\b.*\b(error|failed|timeout|over-current)\b", re.IGNORECASE)


def _ensure_dirs() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _boot_id() -> str:
    bid = _read_file("/proc/sys/kernel/random/boot_id")
    return bid or "unknown"


def _whoami() -> Dict[str, Any]:
    """Return process identity info for diagnostics.

    Written into the state file so UI/ops can see *why* journal access fails.
    Typical: service runs as non-root without adm/systemd-journal groups.
    """
    try:
        euid = os.geteuid()
        egid = os.getegid()
        user = pwd.getpwuid(euid).pw_name
    except Exception:
        euid, egid, user = -1, -1, "unknown"

    groups = []
    try:
        gids = os.getgroups()
        for g in gids:
            try:
                groups.append(grp.getgrgid(g).gr_name)
            except Exception:
                groups.append(str(g))
    except Exception:
        pass

    return {"euid": int(euid), "egid": int(egid), "user": user, "groups": groups}


def _load_state() -> Dict[str, Any]:
    st: Dict[str, Any] = {}
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                st = json.load(f) or {}
        if not isinstance(st, dict):
            st = {}
    except Exception:
        st = {}
    return st


def _save_state(st: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def _append_log_line(line: str) -> None:
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _run(cmd: List[str], timeout: float = 4.0) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as e:
        return 99, "", str(e)


def _compact_err(s: str, max_len: int = 400) -> str:
    """Normalize stderr for logs/UI (single line, bounded).

    Warum:
      `journalctl` gibt bei fehlenden Rechten oft mehrzeilige Hinweise aus
      ("Hint: ...") die sonst die Logs fluten und die UI unlesbar machen.

    Verhalten:
      - CR/LF -> Space
      - Mehrfach-Spaces kollabieren
      - Ausgabe wird hart auf max_len begrenzt
    """
    if not s:
        return ""
    s = " ".join((s.replace("\r", "\n").split("\n")))
    s = " ".join(s.split())
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _classify(line: str) -> Optional[str]:
    for kind, pat in _ALERT_PATTERNS:
        if pat.search(line):
            return kind
    if _GENERIC_ERR.search(line):
        return "usb-error"
    return None


def _journal_since_args(state: Dict[str, Any], now: int, boot_id: str) -> List[str]:
    """Determine journalctl --since argument.

    Prefer last_scan_ts within same boot.
    Fallback: now - FALLBACK_SINCE_SEC
    """
    st_boot = state.get("boot_id")
    last_scan = state.get("last_scan_ts")
    if st_boot == boot_id and isinstance(last_scan, (int, float)) and int(last_scan) > 0:
        # small overlap to not miss border
        since = max(0, int(last_scan) - 2)
    else:
        since = max(0, int(now) - int(FALLBACK_SINCE_SEC))
    return ["--since", f"@{since}"]


def scan_once() -> Dict[str, Any]:
    """Scan kernel journal and update log/state."""
    _ensure_dirs()
    now = int(time.time())
    boot_id = _boot_id()

    st = _load_state()

    who = _whoami()
    st["who"] = who

    # If boot changed, reset cursor-ish fields but keep last_alert for visibility.
    if st.get("boot_id") != boot_id:
        st["boot_id"] = boot_id
        st["boot_changed_ts"] = now
        # we intentionally do NOT clear last_alert_* (keep last seen for UI).

    # Query kernel journal (short-unix gives unix timestamps at line start)
    # We cap lines to avoid heavy output.
    # -q unterdrückt die berüchtigten Permission-Hints (mehrzeilig),
    # ohne echte Errors zu verstecken.
    cmd = ["journalctl", "-k", "-q", "-o", "short-unix", "--no-pager"]
    cmd += _journal_since_args(st, now, boot_id)
    cmd += ["-n", str(MAX_LINES)]

    rc, out, err = _run(cmd, timeout=6.0)
    errc = _compact_err(err)

    res: Dict[str, Any] = {
        "ok": (rc == 0),
        "rc": rc,
        "err": errc,
        "scanned_at": now,
        "who": who,
        "boot_id": boot_id,
        "alerts": 0,
        "last_alert_ts": int(st.get("last_alert_ts") or 0),
        "last_alert_kind": st.get("last_alert_kind"),
        "last_alert_line": st.get("last_alert_line"),
    }

    if rc != 0:
        # Kein Silent-Fail, aber auch kein Log-Spam: nur alle X Sekunden loggen.
        last_err_log = int(st.get("last_scan_err_log_ts") or 0)
        if now - last_err_log >= 300:
            _append_log_line(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [USB-KERNEL][SCAN-FAIL] journalctl rc={rc} err={res['err']}"
            )
            st["last_scan_err_log_ts"] = now
        st["last_scan_ts"] = now
        st["last_scan_rc"] = rc
        st["last_scan_err"] = res["err"]

        # Diagnose permission issues explicitly (no silent failures).
        if "insufficient permissions" in (res["err"] or "").lower():
            st["last_scan_perm_hint"] = (
                "journalctl -k denied. Fix: run service with SupplementaryGroups=systemd-journal adm or run as root."
            )
        else:
            st["last_scan_perm_hint"] = ""
        _save_state(st)
        return res

    lines = [ln for ln in out.splitlines() if ln.strip()]
    # Parse: short-unix => "<ts> <host> kernel: ..."
    for ln in lines:
        kind = _classify(ln)
        if not kind:
            continue

        # Extract timestamp prefix if possible
        ts = now
        try:
            # first token is unix timestamp with microseconds: 1700000000.123456
            first = ln.split(" ", 1)[0]
            ts = int(float(first))
        except Exception:
            ts = now

        human = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        _append_log_line(f"[{human}] [USB-KERNEL][ALERT:{kind}] {ln}")

        st["last_alert_ts"] = ts
        st["last_alert_kind"] = kind
        st["last_alert_line"] = ln
        st["last_alert_human"] = human
        res["alerts"] += 1

    # Always advance scan cursor
    st["last_scan_ts"] = now
    st["last_scan_rc"] = rc
    st["last_scan_err"] = ""
    st["last_scan_perm_hint"] = ""

    # Mirror alert fields into response
    res["last_alert_ts"] = int(st.get("last_alert_ts") or 0)
    res["last_alert_kind"] = st.get("last_alert_kind")
    res["last_alert_line"] = st.get("last_alert_line")

    _save_state(st)
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA USB/KERNEL watcher (filter + state)")
    ap.add_argument("--once", action="store_true", help="Run one scan and exit")
    args = ap.parse_args(argv)

    if not args.once:
        args.once = True

    res = scan_once()
    # Keep stdout JSON-friendly for manual testing.
    try:
        print(json.dumps(res, ensure_ascii=False))
    except Exception:
        print(res)

    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

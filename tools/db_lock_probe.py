#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/db_lock_probe.py

ORÓMA – DB Lock Probe (headless-safe)

Warum es diese Datei gibt
-------------------------
In produktiven ORÓMA-Setups treten gelegentlich Situationen auf, in denen
Runner/Jobs (z. B. *memory_daily_runner*, *tictactoe_daily_runner*, Orchestrator-
Jobs oder Dream/Transfer Worker) beim Schreiben in die SQLite-DB mit
"database is locked" oder mit dem ORÓMA-internen Writer-Lock (FLOCK) scheitern.

Dieses Tool ist eine bewusst kleine, robuste Diagnosehilfe, die ohne "lsof",
"fuser" oder sonstige externe Utilities auskommt. Es liest ausschließlich aus
/proc (Linux) und nutzt Standardbibliothek + sqlite3.

Es beantwortet in einem Lauf:
  1) Welche Prozesse haben die DB-Datei (oder -wal/-shm) offen?
  2) Welche Prozesse haben die ORÓMA-Writer-Lockdatei offen?
  3) Ist auf der Writer-Lockdatei aktuell ein FLOCK aktiv?
  4) SQLite Status-Snapshot: journal_mode, busy_timeout, wal_checkpoint(PASSIVE)

Wichtig / Sicherheitsprinzip
----------------------------
- Keine Änderungen am Schema.
- Keine WAL-Checkpoint-TRUNCATE/RESTART-Operationen.
- Keine "Fixes" (z. B. Killen von Prozessen) – nur Diagnose.
- Alle geöffneten SQLite-Verbindungen werden garantiert geschlossen.

Usage
-----
  python3 tools/db_lock_probe.py
  python3 tools/db_lock_probe.py --db /opt/ai/oroma/data/oroma.db
  python3 tools/db_lock_probe.py --json

Exit Codes
----------
0  : Probe erfolgreich
2  : DB-Datei nicht gefunden / nicht lesbar
3  : /proc nicht verfügbar (nicht Linux oder eingeschränkte Umgebung)
4  : Unerwarteter Fehler

"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sqlite3
import stat
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class ProcHit:
    pid: int
    uid: Optional[int]
    cmdline: str
    exe: str
    matches: List[str]


def _read_text(path: str, limit: int = 1_000_000) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
        return data.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except PermissionError:
        return ""


def _safe_realpath(p: str) -> str:
    try:
        return os.path.realpath(p)
    except Exception:
        return p


def _proc_available() -> bool:
    return os.path.isdir("/proc") and os.path.isfile("/proc/self/status")


def _get_uid_from_status(pid: int) -> Optional[int]:
    txt = _read_text(f"/proc/{pid}/status", limit=200_000)
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            # Uid: real effective saved fs
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except Exception:
                    return None
    return None


def _get_cmdline(pid: int) -> str:
    raw = ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read(200_000)
    except Exception:
        return ""
    if not raw:
        return ""
    # cmdline is NUL-separated
    parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
    return " ".join(parts)


def _get_exe(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except Exception:
        return ""


def _iter_pids() -> Iterable[int]:
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                yield int(name)
            except Exception:
                continue


def _list_open_paths(pid: int) -> List[str]:
    fd_dir = f"/proc/{pid}/fd"
    try:
        names = os.listdir(fd_dir)
    except Exception:
        return []
    out: List[str] = []
    for fd in names:
        p = os.path.join(fd_dir, fd)
        try:
            target = os.readlink(p)
        except Exception:
            continue
        out.append(target)
    return out


def find_procs_holding_paths(target_paths: List[str]) -> List[ProcHit]:
    """Return processes that have any of the given target_paths open.

    Matching is done against the resolved realpath when possible.
    """
    # Normalize targets
    norm_targets = [
        _safe_realpath(tp) for tp in target_paths if tp
    ]
    # also accept exact (non-realpath) variants (e.g. deleted, symlink)
    norm_targets += [tp for tp in target_paths if tp]

    hits: List[ProcHit] = []

    for pid in _iter_pids():
        # Quick skip for self? Not required.
        open_links = _list_open_paths(pid)
        if not open_links:
            continue

        matches: List[str] = []
        for link in open_links:
            # /proc/PID/fd may show "(deleted)" suffix
            link_clean = link.replace(" (deleted)", "")
            link_real = _safe_realpath(link_clean)

            for tp in norm_targets:
                if link_clean == tp or link_real == tp:
                    matches.append(link)
                    break

        if matches:
            hits.append(
                ProcHit(
                    pid=pid,
                    uid=_get_uid_from_status(pid),
                    cmdline=_get_cmdline(pid),
                    exe=_get_exe(pid),
                    matches=sorted(set(matches)),
                )
            )

    # Sort: most informative first (more matches), then PID
    hits.sort(key=lambda h: (-len(h.matches), h.pid))
    return hits


def try_flock_probe(lock_path: str) -> Dict[str, object]:
    """Attempt a non-blocking exclusive flock on lock_path.

    Returns a dict with:
      - exists
      - acquired (True means we acquired and released, so no other lock holder)
      - error (string)

    Note: This probes *flock* state, not sqlite's internal locks.
    """
    import fcntl

    info: Dict[str, object] = {
        "path": lock_path,
        "exists": os.path.exists(lock_path),
        "acquired": False,
        "error": "",
    }

    if not info["exists"]:
        return info

    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError as e:
        info["error"] = f"open_failed: {e}"
        return info

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info["acquired"] = True
            # release
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                info["acquired"] = False
                info["error"] = "locked_by_other"
            else:
                info["error"] = f"flock_failed: {e}"
    finally:
        try:
            os.close(fd)
        except Exception:
            pass

    return info


def sqlite_snapshot(db_path: str) -> Dict[str, object]:
    """Return a small PRAGMA snapshot.

    This function is read-only. It opens a new connection with a short timeout
    and closes it in all cases.
    """
    snap: Dict[str, object] = {
        "db_path": db_path,
        "ok": False,
        "error": "",
        "journal_mode": None,
        "busy_timeout_ms": None,
        "wal_checkpoint": None,
        "page_count": None,
        "page_size": None,
    }

    if not os.path.exists(db_path):
        snap["error"] = "db_not_found"
        return snap

    conn: Optional[sqlite3.Connection] = None
    try:
        # Small timeout: this is diagnostic; we don't want to hang forever.
        conn = sqlite3.connect(db_path, timeout=2.0)
        cur = conn.cursor()

        # Use separate small calls; keep it readable.
        cur.execute("PRAGMA journal_mode;")
        row = cur.fetchone()
        snap["journal_mode"] = row[0] if row else None

        cur.execute("PRAGMA busy_timeout;")
        row = cur.fetchone()
        snap["busy_timeout_ms"] = int(row[0]) if row and row[0] is not None else None

        # PASSIVE does not truncate; it just reports status and tries a light checkpoint.
        cur.execute("PRAGMA wal_checkpoint(PASSIVE);")
        row = cur.fetchone()
        # row: (checkpointed_frames, log_frames, busy)
        if row and len(row) >= 3:
            snap["wal_checkpoint"] = {
                "checkpointed_frames": int(row[0]),
                "log_frames": int(row[1]),
                "busy": int(row[2]),
            }

        cur.execute("PRAGMA page_count;")
        row = cur.fetchone()
        snap["page_count"] = int(row[0]) if row else None

        cur.execute("PRAGMA page_size;")
        row = cur.fetchone()
        snap["page_size"] = int(row[0]) if row else None

        snap["ok"] = True
    except Exception as e:
        snap["error"] = str(e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return snap


def file_stat(path: str) -> Dict[str, object]:
    st: Dict[str, object] = {"path": path, "exists": False}
    try:
        s = os.stat(path)
        st["exists"] = True
        st["size"] = int(s.st_size)
        st["mtime"] = int(s.st_mtime)
        st["mtime_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.st_mtime))
        st["mode"] = stat.filemode(s.st_mode)
    except FileNotFoundError:
        return st
    except Exception as e:
        st["error"] = str(e)
        return st
    return st


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA DB lock probe (proc-based, headless-safe)")
    ap.add_argument("--db", default=os.environ.get("OROMA_DB_PATH", "/opt/ai/oroma/data/oroma.db"), help="Path to oroma.db")
    ap.add_argument("--lock", default=os.environ.get("OROMA_DB_WRITE_LOCK", ""), help="Path to writer lock file (optional; if empty, auto-detect next to DB)")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    args = ap.parse_args()

    if not _proc_available():
        print("[db_lock_probe] ERROR: /proc not available; this tool requires Linux /proc.", file=sys.stderr)
        return 3

    db_path = args.db
    if not os.path.exists(db_path):
        print(f"[db_lock_probe] ERROR: DB not found: {db_path}", file=sys.stderr)
        return 2

    db_real = _safe_realpath(db_path)
    wal_path = db_path + "-wal"
    shm_path = db_path + "-shm"

    lock_path = args.lock.strip()
    if not lock_path:
        # Default ORÓMA pattern: .oroma_db_write.lock in DB directory
        lock_path = os.path.join(os.path.dirname(db_path), ".oroma_db_write.lock")

    targets = [db_path, db_real, wal_path, shm_path, lock_path]

    holders = find_procs_holding_paths([db_path, db_real, wal_path, shm_path])
    lock_holders = find_procs_holding_paths([lock_path])

    out: Dict[str, object] = {
        "db": {
            "db_path": db_path,
            "db_real": db_real,
            "files": {
                "db": file_stat(db_path),
                "wal": file_stat(wal_path),
                "shm": file_stat(shm_path),
            },
        },
        "writer_lock": {
            "lock_path": lock_path,
            "file": file_stat(lock_path),
            "flock_probe": try_flock_probe(lock_path),
        },
        "open_holders": [
            {
                "pid": h.pid,
                "uid": h.uid,
                "exe": h.exe,
                "cmdline": h.cmdline,
                "matches": h.matches,
            }
            for h in holders
        ],
        "open_lock_holders": [
            {
                "pid": h.pid,
                "uid": h.uid,
                "exe": h.exe,
                "cmdline": h.cmdline,
                "matches": h.matches,
            }
            for h in lock_holders
        ],
        "sqlite": sqlite_snapshot(db_path),
        "ts": int(time.time()),
        "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())),
    }

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=False))
        return 0

    # Human-readable output
    print("\n=== ORÓMA DB Lock Probe ===")
    print(f"DB: {db_path}")
    if db_real != db_path:
        print(f"DB (realpath): {db_real}")
    print(f"WAL: {wal_path}")
    print(f"SHM: {shm_path}")
    print(f"Writer lock: {lock_path}")

    print("\n-- File stats --")
    for k in ("db", "wal", "shm"):
        fs = out["db"]["files"][k]
        if fs.get("exists"):
            print(f"{k:>3}: size={fs.get('size')} mtime={fs.get('mtime_iso')} mode={fs.get('mode')}")
        else:
            print(f"{k:>3}: (missing)")

    lk = out["writer_lock"]["file"]
    if lk.get("exists"):
        print(f"lock: size={lk.get('size')} mtime={lk.get('mtime_iso')} mode={lk.get('mode')}")
    else:
        print("lock: (missing)")

    fp = out["writer_lock"]["flock_probe"]
    if fp.get("exists"):
        if fp.get("acquired") is True:
            print("flock: FREE (probe acquired+released)")
        else:
            err = fp.get("error") or "locked"
            print(f"flock: BUSY ({err})")

    print("\n-- Processes holding DB/WAL/SHM --")
    if not holders:
        print("(none found)")
    else:
        for h in holders:
            cmd = h.cmdline or h.exe or "(unknown)"
            print(f"pid={h.pid} uid={h.uid} open={len(h.matches)}")
            print(f"  cmd: {cmd}")
            for m in h.matches:
                print(f"   - {m}")

    print("\n-- Processes holding writer lock file (open fd) --")
    if not lock_holders:
        print("(none found)")
    else:
        for h in lock_holders:
            cmd = h.cmdline or h.exe or "(unknown)"
            print(f"pid={h.pid} uid={h.uid} open={len(h.matches)}")
            print(f"  cmd: {cmd}")
            for m in h.matches:
                print(f"   - {m}")

    print("\n-- SQLite snapshot --")
    ss = out["sqlite"]
    if ss.get("ok"):
        print(f"journal_mode: {ss.get('journal_mode')}")
        print(f"busy_timeout_ms: {ss.get('busy_timeout_ms')}")
        wcp = ss.get("wal_checkpoint")
        if isinstance(wcp, dict):
            print(f"wal_checkpoint(PASSIVE): checkpointed={wcp.get('checkpointed_frames')} log={wcp.get('log_frames')} busy={wcp.get('busy')}")
        print(f"page_count: {ss.get('page_count')}  page_size: {ss.get('page_size')}")
    else:
        print(f"sqlite: ERROR: {ss.get('error')}")

    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/db_writer.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     DBWriter – globaler Single-Writer Daemon (Stufe C)
# Version:   v3.7.3+
# Stand:     2026-03-04
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK
# ─────────
# Dieser Daemon ist der **einzige** Prozess, der Schreiboperationen (INSERT/UPDATE/DELETE/
# BEGIN IMMEDIATE) gegen ORÓMA SQLite Datenbanken ausführt.
# Andere Prozesse (Dream/Orchestrator/UI/Service) senden Write-Requests per Unix Domain
# Socket (Length-Prefixed JSON) an diesen Daemon.
#
# MULTI-DB (Allowlist)
# ───────────────────
# Der DBWriter kann mehrere ORÓMA-DBs bedienen, aber **nur** über eine explizite Allowlist.
# Dadurch bleibt der Single-Writer-Ansatz konsistent und sicher.
#
# Unterstützte logische DB-Namen:
#   - oroma
#   - stats
#   - knowledge
#   - registry
#
# Der Client darf (legacy) auch "oroma.db"/"stats.db" etc. senden; dies wird
# normalisiert.
#
# ENV
# ───
# - OROMA_DBW_SOCKET=/opt/ai/oroma/data/state/db_writer.sock
# - OROMA_DBW_QUEUE_MAX=50000
# - OROMA_DBW_DROP_LOWPRIO=1
# - OROMA_DBW_LOG_SLOW_MS=250
# - OROMA_DB_BUSY_TIMEOUT_MS=60000
# - OROMA_DBW_ALLOW_DBS=oroma,stats,knowledge,registry
# - OROMA_DB_PATH (optional) → oroma.db Pfad
# - OROMA_STATS_DB_PATH (optional) → stats.db Pfad
# - OROMA_KNOWLEDGE_DB_PATH (optional) → knowledge.db Pfad
# - OROMA_REGISTRY_DB_PATH (optional) → registry.db Pfad
#
# START
# ─────
#   python3 -m core.db_writer
# oder via systemd: oroma-db-writer.service
#

from __future__ import annotations

import base64
import json
import logging
import os
import grp
import pwd
import queue
import signal
import socket
import sqlite3
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


LOG = logging.getLogger("oroma.dbw")

_B64_KEY = "__oroma_bytes_b64__"


def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name, "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip()
    return v if v else default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return bool(default)
    v = str(v).strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _socket_path() -> str:
    return _env_str("OROMA_DBW_SOCKET", "/opt/ai/oroma/data/state/db_writer.sock")


def _socket_perm_spec() -> Tuple[int, str, str]:
    """Return (mode, user, group) to apply to the UDS socket file.

    Motivation:
      - The db-writer daemon is often started as root (systemd default).
      - ORÓMA UI/Service/Orchestrator may run as user 'oroma' (or similar).
      - A root-owned 0600 socket causes PermissionError on connect.

    Defaults:
      - mode: 0660
      - user: '' (keep current owner)
      - group: 'oroma'
    """
    mode_str = _env_str("OROMA_DBW_SOCKET_MODE", "660")
    try:
        mode = int(mode_str, 8)
    except Exception:
        mode = 0o660
    user = _env_str("OROMA_DBW_SOCKET_USER", "")
    group = _env_str("OROMA_DBW_SOCKET_GROUP", "oroma")
    return mode, user, group


def _apply_socket_perms(sock_path: str) -> None:
    """Best-effort apply chmod/chown to the socket file so clients can connect."""
    try:
        mode, user, group = _socket_perm_spec()
        try:
            os.chmod(sock_path, mode)
        except Exception:
            pass

        uid = -1
        gid = -1
        if group:
            try:
                gid = grp.getgrnam(group).gr_gid
            except KeyError:
                gid = -1
        if user:
            try:
                uid = pwd.getpwnam(user).pw_uid
            except KeyError:
                uid = -1

        if uid == -1 and gid == -1:
            return
        os.chown(sock_path, uid if uid != -1 else -1, gid if gid != -1 else -1)
    except Exception:
        # Never crash the daemon because of permission/ownership tweaks.
        return


def _base_dir() -> str:
    return str(os.environ.get("OROMA_BASE") or os.environ.get("OROMA_BASE_DIR") or "/opt/ai/oroma")


def _db_paths_map() -> Dict[str, str]:
    base = _base_dir()
    return {
        "oroma": str(os.environ.get("OROMA_DB_PATH") or os.path.join(base, "data", "oroma.db")),
        "stats": str(os.environ.get("OROMA_STATS_DB_PATH") or os.path.join(base, "data", "stats.db")),
        "knowledge": str(os.environ.get("OROMA_KNOWLEDGE_DB_PATH") or os.path.join(base, "data", "knowledge.db")),
        "registry": str(os.environ.get("OROMA_REGISTRY_DB_PATH") or os.path.join(base, "data", "registry.db")),
    }


def _normalize_db_name(db: Any) -> str:
    s = str(db or "").strip().lower()
    if not s:
        return "oroma"
    # legacy: "oroma.db" → "oroma"
    if s.endswith(".db"):
        s = os.path.basename(s)
        s = s[:-3]
    return s


def _allowed_dbs() -> List[str]:
    raw = _env_str("OROMA_DBW_ALLOW_DBS", "oroma")
    out: List[str] = []
    for part in raw.split(","):
        p = part.strip().lower()
        if p:
            out.append(p)
    if "oroma" not in out:
        out.append("oroma")
    return out


def _ensure_parent_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client closed")
        buf.extend(chunk)
    return bytes(buf)


def _recv_frame(s: socket.socket) -> Dict[str, Any]:
    hdr = _recv_exact(s, 4)
    (ln,) = struct.unpack(">I", hdr)
    if ln <= 0 or ln > 50_000_000:
        raise ValueError(f"invalid frame length: {ln}")
    raw = _recv_exact(s, ln)
    return json.loads(raw.decode("utf-8", errors="replace"))


def _send_frame(s: socket.socket, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdr = struct.pack(">I", len(raw))
    s.sendall(hdr + raw)


def _prio(priority: str) -> int:
    p = str(priority).lower().strip()
    if p == "high":
        return 0
    if p == "low":
        return 2
    return 1


def _dejsonify_param(v: Any) -> Any:
    if isinstance(v, dict) and _B64_KEY in v and isinstance(v.get(_B64_KEY), str):
        try:
            return base64.b64decode(v[_B64_KEY].encode("ascii"))
        except Exception:
            return b""
    if isinstance(v, list):
        return [_dejsonify_param(x) for x in v]
    if isinstance(v, dict):
        return {k: _dejsonify_param(val) for k, val in v.items()}
    return v


def _dejsonify_params(params: Any) -> List[Any]:
    if not params:
        return []
    return [_dejsonify_param(x) for x in list(params)]


def _dejsonify_params_list(params_list: Any) -> List[List[Any]]:
    if not params_list:
        return []
    return [_dejsonify_params(p) for p in list(params_list)]


class _Req:
    __slots__ = ("prio", "seq", "client", "msg")

    def __init__(self, prio: int, seq: int, client: socket.socket, msg: Dict[str, Any]):
        self.prio = prio
        self.seq = seq
        self.client = client
        self.msg = msg

    def __lt__(self, other: "_Req") -> bool:
        if self.prio != other.prio:
            return self.prio < other.prio
        return self.seq < other.seq


class DBWriterDaemon:
    def __init__(self):
        self.sock_path = _socket_path()
        self.queue_max = _env_int("OROMA_DBW_QUEUE_MAX", 50000)
        self.drop_low = _env_bool("OROMA_DBW_DROP_LOWPRIO", True)
        self.slow_ms = _env_int("OROMA_DBW_LOG_SLOW_MS", 250)

        self.allowed_dbs = set(_allowed_dbs())
        self.db_paths = _db_paths_map()
        self._conns: Dict[str, sqlite3.Connection] = {}

        # ------------------------------------------------------------------
        # Queue/Throughput Counters (Ops-Transparenz)
        # ------------------------------------------------------------------
        # Motivation:
        #   In Stufe C ist der DBWriter ein zentraler „Funnel“. Für Debug/Diagnose
        #   (Backpressure, "wer schreibt am meisten?", Fehleranalyse) brauchen wir
        #   eine transparente Sicht auf:
        #     - Queue-Länge total
        #     - Queue-Länge pro DB
        #     - verarbeitete Writes pro DB
        #     - verarbeitete Writes pro Tag (Top-N)
        #
        # Design:
        #   - geringer Overhead
        #   - thread-safe (enqueue in Client-Threads)
        #   - begrenzte Speicherhaltung (Tag-Map wird gekappt)
        #
        self._ctr_lock = threading.Lock()
        self._pending_by_db: Dict[str, int] = {}
        self._processed_total = 0
        self._processed_by_db: Dict[str, int] = {}
        self._processed_by_tag: Dict[str, int] = {}
        self._dropped_low_total = 0
        self._dropped_low_by_db: Dict[str, int] = {}
        self._last_error: Optional[str] = None
        self._start_ts = time.time()

        self._seq = 0
        self._pq: "queue.PriorityQueue[_Req]" = queue.PriorityQueue(maxsize=self.queue_max)
        self._stop = threading.Event()
        self._srv: Optional[socket.socket] = None

    def _open_db(self, db_name: str) -> sqlite3.Connection:
        p = self.db_paths.get(db_name)
        if not p:
            raise ValueError(f"unknown db name: {db_name}")
        _ensure_parent_dir(p)
        conn = sqlite3.connect(p, timeout=60.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            conn.execute(f"PRAGMA busy_timeout={_env_int('OROMA_DB_BUSY_TIMEOUT_MS', 60000)}")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA temp_store=MEMORY")
        except Exception:
            pass
        return conn

    def _get_conn(self, db_name: str) -> sqlite3.Connection:
        c = self._conns.get(db_name)
        if c is not None:
            return c
        c = self._open_db(db_name)
        self._conns[db_name] = c
        return c

    def _bind_socket(self) -> socket.socket:
        _ensure_parent_dir(self.sock_path)
        # Zombie socket removal
        try:
            if os.path.exists(self.sock_path):
                os.remove(self.sock_path)
        except Exception as e:
            LOG.warning("db_writer: could not remove existing socket %s: %s", self.sock_path, e)

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sock_path)
        # Ensure clients (often user 'oroma') can connect even if daemon runs as root.
        _apply_socket_perms(self.sock_path)
        srv.listen(64)
        srv.settimeout(1.0)
        return srv

    def serve_forever(self) -> int:
        logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s')
        LOG.info("db_writer: starting (sock=%s allow_dbs=%s)", self.sock_path, ",".join(sorted(self.allowed_dbs)))
        self._srv = self._bind_socket()

        t_exec = threading.Thread(target=self._exec_loop, name="dbw-exec", daemon=True)
        t_exec.start()

        try:
            while not self._stop.is_set():
                try:
                    c, _ = self._srv.accept()
                    c.settimeout(10.0)
                    t = threading.Thread(target=self._client_loop, args=(c,), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self._stop.is_set():
                        LOG.warning("db_writer: accept failed: %s", e)
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            if self._srv is not None:
                try:
                    self._srv.close()
                except Exception:
                    pass
        finally:
            self._srv = None

        try:
            for _k, _c in list(self._conns.items()):
                try:
                    _c.close()
                except Exception:
                    pass
        finally:
            self._conns = {}

        try:
            if os.path.exists(self.sock_path):
                os.remove(self.sock_path)
        except Exception:
            pass

        LOG.info("db_writer: stopped")

    def _enqueue(self, client: socket.socket, msg: Dict[str, Any]) -> None:
        pr = _prio(msg.get("priority") or "normal")
        self._seq += 1
        r = _Req(prio=pr, seq=self._seq, client=client, msg=msg)

        db_name = _normalize_db_name(msg.get("db") or "oroma")

        if self._pq.full() and pr >= 2 and self.drop_low:
            with self._ctr_lock:
                self._dropped_low_total += 1
                self._dropped_low_by_db[db_name] = int(self._dropped_low_by_db.get(db_name, 0)) + 1
            resp = {"id": msg.get("id"), "ok": False, "error": {"code": "queue_full", "message": "queue full; low prio dropped"}, "server_ts": int(time.time())}
            try:
                _send_frame(client, resp)
            except Exception:
                pass
            return

        try:
            self._pq.put_nowait(r)
            with self._ctr_lock:
                self._pending_by_db[db_name] = int(self._pending_by_db.get(db_name, 0)) + 1
        except queue.Full:
            resp = {"id": msg.get("id"), "ok": False, "error": {"code": "queue_full", "message": "queue full"}, "server_ts": int(time.time())}
            try:
                _send_frame(client, resp)
            except Exception:
                pass

    def _state_payload(self) -> Dict[str, Any]:
        """Return a compact snapshot for ops/debug (used by op=state)."""
        with self._ctr_lock:
            q_by_db = dict(sorted(self._pending_by_db.items(), key=lambda kv: (-int(kv[1]), kv[0])))
            p_by_db = dict(sorted(self._processed_by_db.items(), key=lambda kv: (-int(kv[1]), kv[0])))
            top_tags = sorted(self._processed_by_tag.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:25]
            d_by_db = dict(sorted(self._dropped_low_by_db.items(), key=lambda kv: (-int(kv[1]), kv[0])))
            return {
                "sock": self.sock_path,
                "allow_dbs": sorted(self.allowed_dbs),
                "queue_total": int(self._pq.qsize()),
                "queue_by_db": q_by_db,
                "processed_total": int(self._processed_total),
                "processed_by_db": p_by_db,
                "processed_top_tags": top_tags,
                "dropped_low_total": int(self._dropped_low_total),
                "dropped_low_by_db": d_by_db,
                "uptime_s": int(max(0.0, time.time() - float(self._start_ts))),
                "last_error": self._last_error,
            }

    def _client_loop(self, client: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                try:
                    msg = _recv_frame(client)
                except socket.timeout:
                    # Idle client connection: keep alive.
                    continue
                op = str(msg.get("op") or "")
                if op == "ping":
                    resp = {"id": msg.get("id"), "ok": True, "result": {}, "server_ts": int(time.time())}
                    _send_frame(client, resp)
                    continue
                if op == "state":
                    resp = {"id": msg.get("id"), "ok": True, "result": self._state_payload(), "server_ts": int(time.time())}
                    _send_frame(client, resp)
                    continue
                self._enqueue(client, msg)
        except Exception:
            try:
                client.close()
            except Exception:
                pass

    def _exec_loop(self) -> None:
        last_report = time.time()
        while not self._stop.is_set():
            try:
                r: _Req = self._pq.get(timeout=0.5)
            except queue.Empty:
                if (time.time() - last_report) >= 10.0:
                    last_report = time.time()
                    LOG.info("db_writer: queue=%d", self._pq.qsize())
                continue

            msg = r.msg
            req_id = msg.get("id")
            tag = str(msg.get("tag") or "")
            op = str(msg.get("op") or "")
            db_raw = msg.get("db") or "oroma"
            db_name = _normalize_db_name(db_raw)

            # Decrement pending counter as soon as we have taken the item.
            with self._ctr_lock:
                if db_name in self._pending_by_db:
                    self._pending_by_db[db_name] = max(0, int(self._pending_by_db.get(db_name, 0)) - 1)

            t0 = time.time()
            try:
                if db_name not in self.allowed_dbs:
                    raise ValueError(f"db not allowlisted: {db_raw}")
                conn = self._get_conn(db_name)

                if op == "exec":
                    sql = str(msg.get("sql") or "")
                    params = _dejsonify_params(msg.get("params") or [])
                    cur = conn.execute(sql, params)
                    res = {"rowcount": int(cur.rowcount), "lastrowid": int(cur.lastrowid or 0)}
                elif op == "executemany":
                    sql = str(msg.get("sql") or "")
                    params_list = _dejsonify_params_list(msg.get("params_list") or [])
                    cur = conn.executemany(sql, params_list)
                    res = {"rowcount": int(cur.rowcount)}
                elif op == "transaction":
                    stmts = msg.get("stmts") or []
                    conn.execute("BEGIN IMMEDIATE")
                    for (sql, params) in stmts:
                        conn.execute(str(sql), _dejsonify_params(params or []))
                    conn.execute("COMMIT")
                    res = {}
                else:
                    raise ValueError(f"unsupported op: {op}")

                dt_ms = int((time.time() - t0) * 1000.0)
                if dt_ms >= self.slow_ms:
                    LOG.info("db_writer: slow op=%s %dms tag=%s db=%s", op, dt_ms, tag, db_name)

                resp = {"id": req_id, "ok": True, "result": res, "server_ts": int(time.time())}
                try:
                    _send_frame(r.client, resp)
                except Exception:
                    pass

                with self._ctr_lock:
                    self._processed_total += 1
                    self._processed_by_db[db_name] = int(self._processed_by_db.get(db_name, 0)) + 1
                    if tag:
                        self._processed_by_tag[tag] = int(self._processed_by_tag.get(tag, 0)) + 1
                        # Keep tag map bounded to avoid unbounded growth.
                        if len(self._processed_by_tag) > 5000:
                            top = sorted(self._processed_by_tag.items(), key=lambda kv: -int(kv[1]))[:2000]
                            self._processed_by_tag = {k: int(v) for (k, v) in top}
            except Exception as e:
                try:
                    if "conn" in locals():
                        conn.execute("ROLLBACK")
                except Exception:
                    pass
                LOG.warning("db_writer: op failed op=%s tag=%s db=%s err=%s", op, tag, db_name, e)
                with self._ctr_lock:
                    self._last_error = f"{op} {db_name} {tag}: {e}"
                resp = {"id": req_id, "ok": False, "error": {"code": "exec_failed", "message": str(e)}, "server_ts": int(time.time())}
                try:
                    _send_frame(r.client, resp)
                except Exception:
                    pass


def main() -> int:
    d = DBWriterDaemon()

    def _sig(_signo: int, _frame: Any) -> None:
        d.shutdown()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    return d.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())

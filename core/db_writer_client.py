#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/db_writer_client.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     DBWriterClient – IPC-Client für globalen Single-Writer (Stufe C)
# Version:   v3.7.3+
# Stand:     2026-03-04
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK
# ─────────
# Client für den globalen DBWriter Daemon (Unix Domain Socket, length-prefixed JSON).
# Ziel: SQLite-Writes werden nicht mehr aus mehreren Prozessen/Threads direkt ausgeführt,
# sondern **sequenziell** im Writer-Prozess.
#
# MULTI-DB
# ────────
# Der Client kann ein logisches DB-Ziel angeben (db="oroma"|"stats"|"knowledge"|"registry").
# Legacy ("oroma.db") wird serverseitig normalisiert.
#
# ENV
# ───
# - OROMA_DBW_ENABLE=1|0
# - OROMA_DBW_SOCKET=/opt/ai/oroma/data/state/db_writer.sock
# - OROMA_DBW_CLIENT_TIMEOUT_MS_UI=2000
# - OROMA_DBW_CLIENT_TIMEOUT_MS_DREAM=60000
#

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import uuid
import threading
from typing import Any, Dict, Optional, Sequence, Tuple, List

_B64_KEY = "__oroma_bytes_b64__"


def _jsonify_param(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return {_B64_KEY: base64.b64encode(b).decode("ascii")}
    if isinstance(v, (list, tuple)):
        return [_jsonify_param(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonify_param(val) for k, val in v.items()}
    return str(v)


def _jsonify_params(params: Optional[Sequence[Any]]) -> List[Any]:
    if params is None:
        return []
    return [_jsonify_param(x) for x in list(params)]


def _jsonify_params_list(params_list: Optional[Sequence[Sequence[Any]]]) -> List[List[Any]]:
    if params_list is None:
        return []
    return [_jsonify_params(p) for p in list(params_list)]


def _jsonify_stmts(stmts: Optional[Sequence[Tuple[str, Sequence[Any]]]]) -> List[List[Any]]:
    if stmts is None:
        return []
    out: List[List[Any]] = []
    for sql, params in stmts:
        out.append([str(sql), _jsonify_params(params)])
    return out


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


def _sock_path() -> str:
    return _env_str("OROMA_DBW_SOCKET", "/opt/ai/oroma/data/state/db_writer.sock")


class DBWriterClient:
    def __init__(self):
        self._sock_path = _sock_path()
        self._sock: Optional[socket.socket] = None
        # Thread-Safety:
        # ORÓMA nutzt einen globalen Client (_DEFAULT) in mehreren Threads (UI/Service/Worker).
        # Der DBWriter nutzt ein framed Stream-Protokoll; paralleles send/recv auf demselben
        # Socket zerstört Framing ("invalid frame length") und führt zu "server closed".
        # Deshalb serialisieren wir I/O pro Client.
        self._io_lock = threading.Lock()

    @staticmethod
    def _send_frame(s: socket.socket, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdr = struct.pack(">I", len(raw))
        s.sendall(hdr + raw)

    @staticmethod
    def _recv_exact(s: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("db_writer: server closed")
            buf.extend(chunk)
        return bytes(buf)

    @classmethod
    def _recv_frame(cls, s: socket.socket) -> Dict[str, Any]:
        hdr = cls._recv_exact(s, 4)
        (ln,) = struct.unpack(">I", hdr)
        if ln <= 0 or ln > 50_000_000:
            raise ValueError(f"db_writer invalid frame length: {ln}")
        raw = cls._recv_exact(s, ln)
        return json.loads(raw.decode("utf-8", errors="replace"))

    def _connect(self, timeout_ms: int) -> socket.socket:
        # Lazy connect: open only when needed.
        if self._sock is not None:
            return self._sock
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(max(0.2, float(timeout_ms) / 1000.0))
        s.connect(self._sock_path)
        self._sock = s
        return s

    
    def _reset_socket(self) -> None:
        try:
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    def request(
        self,
        op: str,
        sql: Optional[str] = None,
        params: Optional[Sequence[Any]] = None,
        params_list: Optional[Sequence[Sequence[Any]]] = None,
        stmts: Optional[Sequence[Tuple[str, Sequence[Any]]]] = None,
        tag: str = "",
        priority: str = "normal",
        timeout_ms: int = 60000,
        expect: str = "none",
        db: str = "oroma",
    ) -> Dict[str, Any]:
        """Send a request to the DBWriter daemon (length-prefixed JSON protocol).

        NOTE:
        This method exists because an earlier patch accidentally defined request() at
        module scope (with a 'self' parameter), which breaks callers that expect a
        DBWriterClient.request method. We keep the implementation in a shared helper
        to avoid large re-indentation diffs."""
        return _request_impl(
            self,
            op=op,
            sql=sql,
            params=params,
            params_list=params_list,
            stmts=stmts,
            tag=tag,
            priority=priority,
            timeout_ms=timeout_ms,
            expect=expect,
            db=db,
        )

def _request_impl(
        self,
        op: str,
        sql: Optional[str] = None,
        params: Optional[Sequence[Any]] = None,
        params_list: Optional[Sequence[Sequence[Any]]] = None,
        stmts: Optional[Sequence[Tuple[str, Sequence[Any]]]] = None,
        tag: str = "",
        priority: str = "normal",
        timeout_ms: int = 60000,
        expect: str = "none",
        db: str = "oroma",
    ) -> Dict[str, Any]:
        req_id = str(uuid.uuid4())
        payload: Dict[str, Any] = {
            "id": req_id,
            "op": str(op),
            "db": str(db),
            "timeout_ms": int(timeout_ms),
            "priority": str(priority),
            "tag": str(tag),
            "expect": str(expect),
            "ts": int(time.time()),
        }
        if sql is not None:
            payload["sql"] = str(sql)
        if params is not None:
            payload["params"] = _jsonify_params(params)
        if params_list is not None:
            payload["params_list"] = _jsonify_params_list(params_list)
        if stmts is not None:
            payload["stmts"] = _jsonify_stmts(stmts)

        # Send/Recv with one retry on transient disconnect (server closes idle sockets).
        for attempt in (1, 2):
            s = self._connect(timeout_ms=timeout_ms)
            try:
                # Serialize socket I/O to avoid frame corruption across threads.
                with self._io_lock:
                    self._send_frame(s, payload)
                    resp = self._recv_frame(s)
                break
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, ConnectionError, OSError) as e:
                # Retry once with a fresh socket. Keep errors visible to caller on final failure.
                self._reset_socket()
                if attempt == 2:
                    raise
                continue
        if resp.get("id") != req_id:
            raise RuntimeError("db_writer protocol error: id mismatch")
        return resp


_DEFAULT: Optional[DBWriterClient] = None


def enabled() -> bool:
    return _env_bool("OROMA_DBW_ENABLE", False)


def _client() -> DBWriterClient:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DBWriterClient()
    return _DEFAULT


def ping(timeout_ms: int = 500) -> bool:
    if not enabled():
        return False
    try:
        resp = _client().request(op="ping", timeout_ms=int(timeout_ms), expect="none", tag="client.ping")
        return bool(resp.get("ok"))
    except Exception:
        return False


def state(timeout_ms: int = 1000) -> Dict[str, Any]:
    """Return a DBWriter state snapshot (queue/counters).

    This is intended for ops/debug visibility (e.g. Health UI), not for
    high-frequency data collection.
    """
    if not enabled():
        return {"ok": False, "error": "db_writer disabled"}
    resp = _client().request(op="state", timeout_ms=int(timeout_ms), expect="none", tag="client.state")
    if not resp.get("ok"):
        err = resp.get("error") or {}
        return {"ok": False, "error": f"{err.get('code')} {err.get('message')}"}
    return dict(resp.get("result") or {})


def exec_write(sql: str, params: Sequence[Any], tag: str, priority: str, timeout_ms: int, db: str = "oroma") -> int:
    resp = _client().request(op="exec", sql=sql, params=params, tag=tag, priority=priority, timeout_ms=timeout_ms, expect="rowcount", db=db)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        raise RuntimeError(f"db_writer exec failed: {err.get('code')} {err.get('message')}")
    return int((resp.get("result") or {}).get("rowcount") or 0)


def exec_lastrowid(sql: str, params: Sequence[Any], tag: str, priority: str, timeout_ms: int, db: str = "oroma") -> int:
    resp = _client().request(op="exec", sql=sql, params=params, tag=tag, priority=priority, timeout_ms=timeout_ms, expect="lastrowid", db=db)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        raise RuntimeError(f"db_writer exec failed: {err.get('code')} {err.get('message')}")
    return int((resp.get("result") or {}).get("lastrowid") or 0)


def exec(
    sql: str,
    params: Sequence[Any],
    tag: str,
    priority: str,
    timeout_ms: int,
    expect: str = "rowcount",
    db: str = "oroma",
) -> int:
    """Compatibility shim.

    Some older ORÓMA modules call db_writer_client.exec(..., expect='rowcount'|'lastrowid').
    Keep this wrapper so those call sites remain stable.
    """
    if str(expect).lower() == "lastrowid":
        return exec_lastrowid(sql, params=params, tag=tag, priority=priority, timeout_ms=timeout_ms, db=db)
    return exec_write(sql, params=params, tag=tag, priority=priority, timeout_ms=timeout_ms, db=db)


def executemany(sql: str, params_list: Sequence[Sequence[Any]], tag: str, priority: str, timeout_ms: int, db: str = "oroma") -> int:
    resp = _client().request(op="executemany", sql=sql, params_list=params_list, tag=tag, priority=priority, timeout_ms=timeout_ms, expect="rowcount", db=db)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        raise RuntimeError(f"db_writer executemany failed: {err.get('code')} {err.get('message')}")
    return int((resp.get("result") or {}).get("rowcount") or 0)


def transaction(stmts: Sequence[Tuple[str, Sequence[Any]]], tag: str, priority: str, timeout_ms: int, db: str = "oroma") -> Dict[str, Any]:
    resp = _client().request(op="transaction", stmts=stmts, tag=tag, priority=priority, timeout_ms=timeout_ms, expect="none", db=db)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        raise RuntimeError(f"db_writer transaction failed: {err.get('code')} {err.get('message')}")
    return resp.get("result") or {}

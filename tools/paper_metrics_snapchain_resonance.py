#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/paper_metrics_snapchain_resonance.py
# Projekt: ORÓMA
# Version: v1.1 (Paper-Metriken – Section 4.3 "SnapChain Resonance")
# Stand:   2025-12-17
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.2 Thinking
# =============================================================================
#
# Änderungsnotiz v1.1 (gegenüber v1.0)
# ------------------------------------
# 1) Audit-Fix:
#    - meta_snaps.sources wird robust geparst:
#      * CSV ("1,2,3")
#      * JSON-List ("[1,2,3]" / ["1","2"])
#      * Klammern/Quotes/Whitespace-Varianten
#      * Fallback: Regex-Extraktion aller Integer-IDs
#    - "bad_format" wird nun als "unparseable_entries" ausgewiesen
#      und "orphans" basiert auf tatsächlich extrahierten IDs.
#
# 2) Resonanz-Fix:
#    - Blob-Vektor-Extraktion unterstützt zusätzlich:
#      * "v": Tokenliste (z.B. vision/token) -> Hash-Bag-of-Tokens Embedding
#      * optionale numerische Features: motion/edges/color/... (wenn vorhanden)
#    - Ergebnis: resonance_sample_n_used wird > 0, sofern Blobs JSON enthalten.
#
# Sicherheit / Nicht-destruktiv
# -----------------------------
# Dieses Tool führt KEINE Updates in der DB aus.
# Es liest nur und schreibt CSV/JSON außerhalb der DB.
#
# =============================================================================

from __future__ import annotations

import os
import csv
import json
import math
import re
import time
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import logging
from core.log_guard import log_suppressed

# ----------------------------- Config ----------------------------------------

SAMPLE_N = int(os.environ.get("OROMA_PAPER_SAMPLE_N", "1000"))
DAYS = int(os.environ.get("OROMA_PAPER_DAYS", "7"))
COS_THRESHOLD = float(os.environ.get("OROMA_PAPER_COS_THRESHOLD", "0.30"))

# Token-hash embedding size (power of two is convenient)
TOK_EMB_DIM = int(os.environ.get("OROMA_PAPER_TOK_EMB_DIM", "128"))

# Output CSV
BASE_DIR = Path(os.environ.get("OROMA_BASE", "/opt/ai/oroma"))
OUT_DIR = BASE_DIR / "exports_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = OUT_DIR / "paper_metrics_resonance.csv"

# ----------------------------- Helpers ---------------------------------------

def _now_i() -> int:
    return int(time.time())

def _detect_db_path() -> str:
    try:
        from core import sql_manager  # type: ignore
        if hasattr(sql_manager, "get_db_path"):
            p = str(sql_manager.get_db_path())  # type: ignore
            if p and Path(p).exists():
                return p
    except Exception as e:
        log_suppressed('tools/paper_metrics_snapchain_resonance.py:74', exc=e, level=logging.WARNING)
        pass

    for k in ("OROMA_DB", "OROMA_DB_PATH", "OROMA_DB_FILE"):
        v = (os.environ.get(k, "") or "").strip()
        if v and Path(v).exists():
            return v

    candidates = [
        "/opt/ai/oroma/data/oroma.db",
        "/opt/ai/oroma/database/oroma.db",
        "/opt/ai/oroma/v2.30/database/oroma.db",
        "/opt/ai/oroma/v2.11/database/oroma.db",
    ]
    for p in candidates:
        if Path(p).exists():
            return p

    raise FileNotFoundError("Konnte oroma.db nicht finden. Setze ENV OROMA_DB auf den korrekten Pfad.")

def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(len(a)):
        x = float(a[i]); y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    den = math.sqrt(na) * math.sqrt(nb)
    if den <= 1e-12:
        return 0.0
    return float(dot / den)

def _l2_norm(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) + 1e-12
    return [x / n for x in v]

def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _get_time_min(days: int) -> int:
    return _now_i() - int(days) * 86400

# ----------------------------- Sources parsing (Audit) -----------------------

_RE_INT = re.compile(r"\b\d+\b")

def _parse_sources_any(s: Any) -> Tuple[List[int], int]:
    """
    Returns (ids, unparseable_entries_count)
    - Robust against CSV, JSON-list, quotes/brackets, and mixed text.
    """
    if s is None:
        return [], 0
    if isinstance(s, (bytes, bytearray, memoryview)):
        try:
            s = bytes(s).decode("utf-8", "ignore")
        except Exception:
            s = str(s)

    if isinstance(s, list):
        ids = []
        bad = 0
        for x in s:
            if isinstance(x, int):
                ids.append(int(x))
            elif isinstance(x, str) and x.strip().isdigit():
                ids.append(int(x.strip()))
            else:
                bad += 1
        return ids, bad

    txt = str(s).strip()
    if not txt:
        return [], 0

    # Try JSON first (covers "[1,2]" or {"sources":[...]}).
    if txt[:1] in ("[", "{"):
        try:
            j = json.loads(txt)
            if isinstance(j, list):
                return _parse_sources_any(j)
            if isinstance(j, dict):
                if "sources" in j:
                    return _parse_sources_any(j.get("sources"))
        except Exception as e:
            log_suppressed('tools/paper_metrics_snapchain_resonance.py:177', exc=e, level=logging.WARNING)
            pass

    # Normalize common wrappers
    norm = txt.strip()
    norm = norm.strip("()[]{}")
    norm = norm.replace('"', "").replace("'", "")

    # Split on common separators; if still messy, regex fallback
    parts = re.split(r"[,\s;|]+", norm)
    ids: List[int] = []
    bad = 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p.isdigit():
            ids.append(int(p))
        else:
            bad += 1

    if ids:
        return ids, bad

    # Regex fallback: extract all integers from string
    hits = _RE_INT.findall(txt)
    if hits:
        return [int(h) for h in hits], 0

    return [], 1  # fully unparseable entry

# ----------------------------- Vector extraction (Resonance) -----------------

def _hash_bucket(token: int, dim: int) -> int:
    # Fast stable bucket (no Python hash randomization issues)
    # multiplicative hashing
    x = (token * 2654435761) & 0xFFFFFFFF
    return int(x & (dim - 1)) if (dim & (dim - 1)) == 0 else int(x % dim)

def _vector_from_json_obj(j: Dict[str, Any]) -> Optional[List[float]]:
    """
    Supports:
      - patterns/pattern: list[float] or list[list[float]] -> mean -> normalize
      - v: list[int] (tokens) -> hashed BoW vector (+ optional numeric features)
    """
    # 1) patterns/pattern (legacy / embeddings)
    patterns = j.get("patterns") or j.get("pattern")
    if isinstance(patterns, list) and patterns:
        if isinstance(patterns[0], (int, float)):
            vec = [float(x) for x in patterns]
            return _l2_norm(vec)
        if isinstance(patterns[0], list):
            # mean vector
            acc = None
            n = 0
            for p in patterns:
                if not isinstance(p, list):
                    continue
                v = [float(x) for x in p]
                if acc is None:
                    acc = [0.0] * len(v)
                if len(v) != len(acc):
                    continue
                for i in range(len(v)):
                    acc[i] += v[i]
                n += 1
            if acc and n > 0:
                vec = [x / float(n) for x in acc]
                return _l2_norm(vec)

    # 2) v tokens (vision/token style)
    vtok = j.get("v")
    if isinstance(vtok, list) and vtok:
        dim = max(16, int(TOK_EMB_DIM))
        bow = [0.0] * dim
        # count tokens
        for t in vtok:
            try:
                ti = int(t)
            except Exception:
                continue
            bow[_hash_bucket(ti, dim)] += 1.0

        # Optional numeric features appended (kept small, edge-tauglich)
        feats: List[float] = []
        for k in ("motion", "edges", "color", "brightness", "sharpness", "entropy"):
            if k in j and isinstance(j[k], (int, float)):
                feats.append(float(j[k]))

        # Combine bow + feats (feats are scaled to not dominate)
        if feats:
            # light scaling: divide by (1 + abs) to keep ~[-1,1]
            feats = [x / (1.0 + abs(x)) for x in feats]
            vec = bow + feats
        else:
            vec = bow

        return _l2_norm(vec)

    return None

def _decode_blob_vector(blob: Any) -> Optional[List[float]]:
    if blob is None:
        return None
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, (bytes, bytearray)):
        txt = blob.decode("utf-8", "ignore").strip()
    else:
        txt = str(blob).strip()

    if not txt:
        return None

    # quick reject: looks like short hex hash
    if len(txt) <= 64 and all(c in "0123456789abcdef" for c in txt.lower()):
        return None

    try:
        j = json.loads(txt)
    except Exception:
        return None

    if isinstance(j, dict):
        return _vector_from_json_obj(j)
    return None

# ----------------------------- Core Metrics ----------------------------------

def _load_forgetting_defaults() -> Tuple[float, int]:
    try:
        from core import forgetting  # type: ignore
        thr = float(getattr(forgetting, "THRESHOLD", float(os.environ.get("OROMA_FORGET_THRESHOLD", "0.2"))))
        mb = int(getattr(forgetting, "META_BATCH", int(os.environ.get("OROMA_FORGET_META_BATCH", "50"))))
        return thr, mb
    except Exception:
        thr = float(os.environ.get("OROMA_FORGET_THRESHOLD", "0.2"))
        mb = int(os.environ.get("OROMA_FORGET_META_BATCH", "50"))
        return thr, mb

def compute_metrics(con: sqlite3.Connection) -> Dict[str, Any]:
    thr, meta_batch = _load_forgetting_defaults()
    tmin = _get_time_min(DAYS)

    total = con.execute("SELECT COUNT(*) AS c FROM snapchains WHERE ts >= ?", (tmin,)).fetchone()["c"]
    weak = con.execute("SELECT COUNT(*) AS c FROM snapchains WHERE ts >= ? AND weight < ?", (tmin, thr)).fetchone()["c"]
    compressed = con.execute(
        "SELECT COUNT(*) AS c FROM snapchains WHERE ts >= ? AND notes = 'compressed'",
        (tmin,)
    ).fetchone()["c"]

    metas_total = con.execute("SELECT COUNT(*) AS c FROM meta_snaps").fetchone()["c"]

    # try to detect a meta time column
    metas_window = None
    meta_cols = [r["name"] for r in con.execute("PRAGMA table_info(meta_snaps)").fetchall()]
    for tc in ("created_at", "ts", "t", "created_ts", "time"):
        if tc in meta_cols:
            metas_window = con.execute(f"SELECT COUNT(*) AS c FROM meta_snaps WHERE {tc} >= ?", (tmin,)).fetchone()["c"]
            break

    weak_frac = (float(weak) / float(total)) if total else 0.0

    projected_inputs = 1000
    projected_weak = weak_frac * projected_inputs
    projected_meta = int(projected_weak // meta_batch)
    if projected_meta <= 0 and projected_weak >= meta_batch:
        projected_meta = 1

    # Storage dryrun: avg blob size for weak & not-yet-compressed candidates
    cand_rows = con.execute(
        "SELECT blob FROM snapchains WHERE ts >= ? AND weight < ? AND (notes IS NULL OR notes != 'compressed') LIMIT 5000",
        (tmin, thr)
    ).fetchall()
    sizes = []
    for r in cand_rows:
        b = r["blob"]
        if isinstance(b, memoryview):
            b = b.tobytes()
        if isinstance(b, (bytes, bytearray)):
            sizes.append(len(b))
        else:
            sizes.append(len(str(b).encode("utf-8", "ignore")))
    avg_blob = float(sum(sizes) / len(sizes)) if sizes else 0.0
    avg_saved = max(0.0, avg_blob - 16.0)
    savings_pct = (avg_saved / avg_blob) if avg_blob > 0 else 0.0

    # Resonance sample
    sample_rows = con.execute(
        "SELECT id, blob FROM snapchains WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
        (tmin, SAMPLE_N * 5)
    ).fetchall()

    vectors: List[Tuple[int, List[float]]] = []
    for r in sample_rows:
        sid = _safe_int(r["id"])
        v = _decode_blob_vector(r["blob"])
        if v is None:
            continue
        vectors.append((sid, v))
        if len(vectors) >= SAMPLE_N:
            break

    resonance_total = len(vectors)
    resonance_assignable = 0

    # Use vector_migration.query if available, else intra-sample
    use_vectordb = False
    try:
        from core import vector_migration  # type: ignore
        if hasattr(vector_migration, "query"):
            use_vectordb = True
    except Exception:
        use_vectordb = False

    if resonance_total == 0:
        resonance_p = 0.0
    else:
        if use_vectordb:
            try:
                from core import vector_migration  # type: ignore
                # If numpy exists, convert lists to np arrays to match faiss expectations
                try:
                    import numpy as np  # type: ignore
                    to_vec = lambda x: np.asarray(x, dtype=np.float32)
                except Exception:
                    to_vec = lambda x: x

                for sid, vec in vectors:
                    best = -1.0
                    res = vector_migration.query(to_vec(vec), k=10)  # type: ignore
                    for rid, score in (res or []):
                        rid_i = _safe_int(rid, -1)
                        sc = _safe_float(score, -1.0)
                        if rid_i == sid:
                            continue
                        if sc > best:
                            best = sc
                    if best >= COS_THRESHOLD:
                        resonance_assignable += 1

                resonance_p = float(resonance_assignable) / float(resonance_total)
            except Exception:
                use_vectordb = False

        if not use_vectordb:
            for i in range(resonance_total):
                _, vi = vectors[i]
                best = -1.0
                for j in range(resonance_total):
                    if i == j:
                        continue
                    _, vj = vectors[j]
                    sc = _cosine(vi, vj)
                    if sc > best:
                        best = sc
                if best >= COS_THRESHOLD:
                    resonance_assignable += 1
            resonance_p = float(resonance_assignable) / float(resonance_total)

    # Audit pragmas + integrity
    pragmas = {}
    for key in ("journal_mode", "synchronous", "foreign_keys", "wal_autocheckpoint"):
        try:
            pragmas[key] = con.execute(f"PRAGMA {key}").fetchone()[0]
        except Exception:
            pragmas[key] = None

    integrity = None
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    except Exception:
        integrity = None

    # Robust orphan check meta_snaps.sources -> snapchains.id
    total_sources = 0
    orphan_sources = 0
    unparseable_entries = 0
    parsed_entries = 0
    try:
        ms_rows = con.execute("SELECT sources FROM meta_snaps").fetchall()
        for r in ms_rows:
            ids, bad = _parse_sources_any(r["sources"])
            if bad:
                unparseable_entries += bad
            if not ids:
                continue
            parsed_entries += 1
            total_sources += len(ids)
            # Check existence
            for sid in ids:
                ex = con.execute("SELECT 1 FROM snapchains WHERE id=?", (sid,)).fetchone()
                if not ex:
                    orphan_sources += 1
    except Exception as e:
        log_suppressed('tools/paper_metrics_snapchain_resonance.py:472', exc=e, level=logging.WARNING)
        pass

    out = {
        "ts_run": _now_i(),
        "window_days": DAYS,
        "forget_threshold": float(thr),
        "forget_meta_batch": int(meta_batch),
        "snapchains_total_window": int(total),
        "snapchains_weak_window": int(weak),
        "snapchains_weak_fraction": float(weak_frac),
        "snapchains_compressed_window": int(compressed),
        "metasnaps_total": int(metas_total),
        "metasnaps_window": None if metas_window is None else int(metas_window),
        "projection_inputs": projected_inputs,
        "projection_weak": float(projected_weak),
        "projection_metasnaps": int(projected_meta),
        "projection_ratio_inputs_per_meta": (float(projected_inputs) / float(projected_meta)) if projected_meta else None,
        "storage_dryrun_avg_blob_bytes": float(avg_blob),
        "storage_dryrun_avg_saved_bytes": float(avg_saved),
        "storage_dryrun_saved_fraction": float(savings_pct),
        "resonance_threshold_cos": float(COS_THRESHOLD),
        "resonance_sample_n_used": int(resonance_total),
        "resonance_used_vectordb": bool(use_vectordb),
        "resonance_assignable_fraction": float(resonance_p),
        "audit_pragmas": pragmas,
        "audit_integrity_check": integrity,
        "audit_meta_sources_total": int(total_sources),
        "audit_meta_sources_orphan": int(orphan_sources),
        "audit_meta_sources_parsed_entries": int(parsed_entries),
        "audit_meta_sources_unparseable_entries": int(unparseable_entries),
    }
    return out

def append_csv(row: Dict[str, Any]) -> None:
    flat = dict(row)
    pr = flat.pop("audit_pragmas", {}) or {}
    for k, v in pr.items():
        flat[f"pragma_{k}"] = v

    write_header = not CSV_PATH.exists()
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(flat.keys()))
        if write_header:
            w.writeheader()
        w.writerow(flat)

def main() -> int:
    db_path = _detect_db_path()
    con = _connect(db_path)
    try:
        metrics = compute_metrics(con)
    finally:
        con.close()

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    try:
        append_csv(metrics)
    except Exception as e:
        print(f"[WARN] CSV konnte nicht geschrieben werden: {e}")

    print(f"[OK] CSV: {CSV_PATH}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
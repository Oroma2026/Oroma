#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/vector_migration.py
# Projekt: ORÓMA
# Version: v3.0
# Stand:   2025-09-18
#
# Zweck:
#   - Aktiviert & verwaltet eine Vektor-Datenbank ab Schwellwert (default: 100.000)
#   - Unterstützt Backends:
#       • FAISS (falls installiert)
#       • Annoy (falls installiert)
#       • BruteForce (NumPy) als Fallback
#   - Migriert SnapChain-Embeddings aus SQLite in ANN-Indizes
#   - Stellt k-NN Query-API bereit (inkl. inkrementellem Update)
#
# Konfiguration (.env / ENV):
#   - OROMA_VECTORDb_THRESHOLD → int, default 100000
#   - OROMA_VECTOR_BACKEND     → 'faiss' | 'annoy' | 'bruteforce' | 'auto'
#   - OROMA_BASE_DIR           → Projektwurzel, default: /opt/ai/oroma
#
# Persistenz:
#   - index_dir   → <BASE>/vector_index/
#   - meta.json   → Backend, Dimension, Anzahl, Zeitstempel, Version
#   - index files → index.faiss | index.ann | brute.npy + ids.npy
#
# API:
#   - vector_db_threshold() -> int
#   - is_enabled() -> bool
#   - set_enabled(flag: bool) -> None
#   - available_backends() -> list[str]
#   - current_backend() -> str
#   - set_backend(name: str) -> None
#   - migrate_if_needed(batch_size=2000) -> dict
#   - rebuild_index(batch_size=2000) -> dict
#   - add_or_update(chain_id:int, vec:np.ndarray) -> None
#   - query(vec:np.ndarray, k=10) -> list[(chain_id, score)]
#   - query_by_chain_id(chain_id:int, k=10) -> list[(neighbor_id, score)]
#
# CLI:
#   python -m core.vector_migration status
#   python -m core.vector_migration build
#   python -m core.vector_migration search --id 123 --k 5
#   python -m core.vector_migration search --vec 0.1,0.2,0.3 --k 5
#
# Hinweise:
#   - Embeddings: erwartet BLOB/JSON aus SnapChain.as_blob()
#   - Standard: Mittelwert aller Pattern-Vektoren als Repräsentant
#   - Vektoren werden l2-normalisiert (Score ~ Cosine Similarity)
#   - BruteForce-Backend: effizient bis mittlere Größenordnung (Pi-tauglich)
# =============================================================================

from __future__ import annotations
import os, json, time, sqlite3, math, errno, argparse
from typing import List, Tuple, Optional, Dict, Any
from core.log_guard import log_suppressed
import logging

# Pflicht
import numpy as np

# Optionale Backends
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

try:
    from annoy import AnnoyIndex  # type: ignore
    _HAS_ANNOY = True
except Exception:
    _HAS_ANNOY = False

# Projektbasis ermitteln
BASE = os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma/v2.11")
DB_PATH_DEFAULT = os.path.join(BASE, "database", "oroma.db")
INDEX_DIR = os.path.join(BASE, "vector_index")
META_PATH = os.path.join(INDEX_DIR, "meta.json")

# Backend-Dateinamen
FAISS_PATH = os.path.join(INDEX_DIR, "index.faiss")
ANNOY_PATH = os.path.join(INDEX_DIR, "index.ann")
BRUTE_VECS_PATH = os.path.join(INDEX_DIR, "brute.npy")
BRUTE_IDS_PATH = os.path.join(INDEX_DIR, "ids.npy")

# -----------------------------------------------------------------------------#
# Interop zu core.sql_manager (falls vorhanden), sonst direkte SQLite-Nutzung
# -----------------------------------------------------------------------------#

def _get_db_path() -> str:
    """Versucht core.sql_manager.get_db_path(); sonst Fallback auf DEFAULT."""
    try:
        from core.sql_manager import get_db_path  # type: ignore
        return get_db_path()
    except Exception:
        return DB_PATH_DEFAULT

def _get_conn() -> sqlite3.Connection:
    """Hole eine SQLite-Verbindung (row_factory=Row)."""
    dbp = _get_db_path()
    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    return conn

def _count_snapchains_sql() -> int:
    """Anzahl SnapChains in der DB (Fallback, wenn sql_manager.count_snapchains nicht nutzbar)."""
    try:
        from core.sql_manager import count_snapchains  # type: ignore
        return int(count_snapchains())
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.vector_migration.pass.1",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )
    with _get_conn() as c:
        cur = c.execute("SELECT COUNT(1) AS n FROM snapchains")
        return int(cur.fetchone()["n"])

def _iter_snapchains_sql(batch_size: int = 2000):
    """
    Iterator über (id, blob) aus der DB, seitenweise. Erwartet Tabelle:
      snapchains(id INTEGER PRIMARY KEY, blob BLOB, quality REAL, created_at INTEGER, ...)
    """
    with _get_conn() as c:
        cur = c.execute("SELECT id, blob FROM snapchains ORDER BY id ASC")
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            for r in rows:
                yield int(r["id"]), r["blob"]

# -----------------------------------------------------------------------------#
# Meta- und Konfig-Verwaltung (Schwellwert, Backend, Aktiv-Flag)
# -----------------------------------------------------------------------------#

def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def vector_db_threshold() -> int:
    """Schwellwert für Aktivierung – per ENV konfigurierbar."""
    v = os.environ.get("OROMA_VECTORDb_THRESHOLD", "100000")
    try:
        return max(1, int(v))
    except Exception:
        return 100000

def _load_meta() -> Dict[str, Any]:
    if not os.path.isfile(META_PATH):
        return {
            "enabled": True,              # Vektor-DB grundsätzlich erlaubt
            "backend": "auto",            # 'auto' | 'faiss' | 'annoy' | 'bruteforce'
            "dim": None,                  # wird bei erstem Build gesetzt
            "count": 0,
            "built_at": None,
            "version": "2.11",
        }
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_meta(meta: Dict[str, Any]) -> None:
    _ensure_dir(INDEX_DIR)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def is_enabled() -> bool:
    """Globale Aktivierung (nicht: ob Index aktiv gebaut wurde)."""
    meta = _load_meta()
    return bool(meta.get("enabled", True))

def set_enabled(flag: bool) -> None:
    meta = _load_meta()
    meta["enabled"] = bool(flag)
    _save_meta(meta)

def available_backends() -> List[str]:
    """Liste möglicher Backends auf diesem System."""
    out = []
    if _HAS_FAISS:
        out.append("faiss")
    if _HAS_ANNOY:
        out.append("annoy")
    out.append("bruteforce")
    return out

def _resolve_backend(pref: Optional[str] = None) -> str:
    """
    Backend-Auflösung:
      - ENV OROMA_VECTOR_BACKEND oder meta["backend"] (falls nicht 'auto')
      - sonst Präferenz pref
      - sonst: faiss > annoy > bruteforce
    """
    env = os.environ.get("OROMA_VECTOR_BACKEND", "").strip().lower()
    meta = _load_meta()
    meta_choice = (meta.get("backend") or "auto").strip().lower()
    wished = pref or (env or (meta_choice if meta_choice != "auto" else ""))

    if wished in ("faiss", "annoy", "bruteforce"):
        # prüfen ob verfügbar
        if wished == "faiss" and _HAS_FAISS:
            return "faiss"
        if wished == "annoy" and _HAS_ANNOY:
            return "annoy"
        if wished == "bruteforce":
            return "bruteforce"

    # auto
    if _HAS_FAISS:
        return "faiss"
    if _HAS_ANNOY:
        return "annoy"
    return "bruteforce"

def current_backend() -> str:
    meta = _load_meta()
    return _resolve_backend(meta.get("backend"))

def set_backend(name: str) -> None:
    name = (name or "").strip().lower()
    if name not in ("auto", "faiss", "annoy", "bruteforce"):
        raise ValueError("Unbekanntes Backend: " + name)
    meta = _load_meta()
    meta["backend"] = name
    _save_meta(meta)

# -----------------------------------------------------------------------------#
# Feature-Extraktion aus SnapChain-BLOB
# -----------------------------------------------------------------------------#

def _decode_blob_get_vector(blob: bytes) -> Optional[np.ndarray]:
    """
    Erwartet JSON im Blob:
      {"patterns":[[...], [...], ...], "metadata": {...}, ...}
    Rückgabe: np.ndarray shape (D,) – standardmäßig Mittelwert aller pattern-Vektoren.
    """
    try:
        s = blob.decode("utf-8", "ignore")
        j = json.loads(s)
        patterns = j.get("patterns") or j.get("pattern") or []
        if not patterns:
            return None
        # Muster: Liste von Vektoren (Listen). Mittelwert über alle Pattern.
        m = np.array(patterns, dtype=np.float32)
        if m.ndim == 1:
            vec = m.astype(np.float32)
        else:
            vec = m.mean(axis=0).astype(np.float32)
        # l2-Normalisierung (für cos-sim / dot-product)
        n = np.linalg.norm(vec) + 1e-12
        vec = (vec / n).astype(np.float32)
        return vec
    except Exception:
        return None

# -----------------------------------------------------------------------------#
# Backend-spezifische Loader/Saver/Query
# -----------------------------------------------------------------------------#

class _IndexBase:
    def __init__(self, dim: int):
        self.dim = int(dim)

    def save(self) -> None:
        raise NotImplementedError

    def add_batch(self, ids: np.ndarray, vecs: np.ndarray) -> None:
        raise NotImplementedError

    def query(self, vec: np.ndarray, k: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        """Rückgabe: (ids, scores). Scores ~ Cosinus-Ähnlichkeit."""
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

class _FaissIndex(_IndexBase):
    def __init__(self, dim: int):
        super().__init__(dim)
        # IndexFlatIP = inner product (bei normierten Vektoren = cos-sim)
        self.index = faiss.IndexFlatIP(dim)
        self._ids = None  # Wir nutzen separaten ID-Wrapper
        # Für stabile IDs: faiss.index_id_map2
        self.index = faiss.IndexIDMap2(self.index)

    def save(self) -> None:
        faiss.write_index(self.index, FAISS_PATH)

    @classmethod
    def load(cls, dim: int):
        if not os.path.isfile(FAISS_PATH):
            return None
        idx = faiss.read_index(FAISS_PATH)
        obj = cls(dim)
        obj.index = idx
        return obj

    def add_batch(self, ids: np.ndarray, vecs: np.ndarray) -> None:
        self.index.add_with_ids(vecs.astype(np.float32), ids.astype(np.int64))

    def query(self, vec: np.ndarray, k: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        vec = vec.reshape(1, -1).astype(np.float32)
        sims, ids = self.index.search(vec, k)
        return ids[0], sims[0]

    def count(self) -> int:
        return int(self.index.ntotal)

class _AnnoyIndex(_IndexBase):
    def __init__(self, dim: int, n_trees: int = 20):
        super().__init__(dim)
        self.ann = AnnoyIndex(dim, metric='angular')  # angular ~ cos
        self._ids: List[int] = []
        self._vecs_added = 0
        self._built = False
        self.n_trees = int(n_trees)

    def save(self) -> None:
        # Für Annoy müssen wir build() durchführen, bevor wir speichern
        if not self._built:
            self.ann.build(self.n_trees)
            self._built = True
        self.ann.save(ANNOY_PATH)
        # IDs-Liste persistieren (Annoy verwaltet keine eigenen IDs)
        np.save(BRUTE_IDS_PATH, np.array(self._ids, dtype=np.int64))

    @classmethod
    def load(cls, dim: int):
        if not os.path.isfile(ANNOY_PATH) or not os.path.isfile(BRUTE_IDS_PATH):
            return None
        obj = cls(dim)
        obj.ann.load(ANNOY_PATH)
        obj._ids = np.load(BRUTE_IDS_PATH).astype(np.int64).tolist()
        obj._built = True
        return obj

    def add_batch(self, ids: np.ndarray, vecs: np.ndarray) -> None:
        # Annoy braucht sequentielle Indizes 0..N-1; wir mappen eigene IDs
        for i in range(vecs.shape[0]):
            self.ann.add_item(self._vecs_added, vecs[i, :].tolist())
            self._ids.append(int(ids[i]))
            self._vecs_added += 1
        self._built = False  # muss später build() vor save/query

    def _ensure_built(self):
        if not self._built:
            self.ann.build(self.n_trees)
            self._built = True

    def query(self, vec: np.ndarray, k: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        self._ensure_built()
        idxs, dists = self.ann.get_nns_by_vector(vec.tolist(), k, include_distances=True)
        # angular distance -> cos sim approx.: cos = 1 - (d^2)/2  (Annäherung)
        idxs = np.array(idxs, dtype=np.int64)
        dists = np.array(dists, dtype=np.float32)
        sims = 1.0 - (dists**2) / 2.0
        # mapping zurück auf chain_ids
        chain_ids = np.array([self._ids[i] for i in idxs], dtype=np.int64)
        return chain_ids, sims

    def count(self) -> int:
        return len(self._ids)

class _BruteForceIndex(_IndexBase):
    def __init__(self, dim: int):
        super().__init__(dim)
        self.vecs = None  # np.ndarray (N, D)
        self.ids = None   # np.ndarray (N,)

    def save(self) -> None:
        np.save(BRUTE_VECS_PATH, self.vecs if self.vecs is not None else np.zeros((0, self.dim), dtype=np.float32))
        np.save(BRUTE_IDS_PATH, self.ids if self.ids is not None else np.zeros((0,), dtype=np.int64))

    @classmethod
    def load(cls, dim: int):
        if not os.path.isfile(BRUTE_VECS_PATH) or not os.path.isfile(BRUTE_IDS_PATH):
            return None
        obj = cls(dim)
        obj.vecs = np.load(BRUTE_VECS_PATH).astype(np.float32)
        obj.ids = np.load(BRUTE_IDS_PATH).astype(np.int64)
        return obj

    def add_batch(self, ids: np.ndarray, vecs: np.ndarray) -> None:
        if self.vecs is None:
            self.vecs = vecs.astype(np.float32)
            self.ids = ids.astype(np.int64)
        else:
            self.vecs = np.vstack((self.vecs, vecs.astype(np.float32)))
            self.ids = np.concatenate((self.ids, ids.astype(np.int64)))

    def query(self, vec: np.ndarray, k: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        if self.vecs is None or self.vecs.shape[0] == 0:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
        # cos-sim via dot (Vektoren sind bereits normalisiert)
        sims = self.vecs @ vec.astype(np.float32)
        idx = np.argpartition(-sims, kth=min(k, sims.shape[0]-1))[:k]
        # sortiert
        idx = idx[np.argsort(-sims[idx])]
        return self.ids[idx], sims[idx]

    def count(self) -> int:
        return 0 if self.ids is None else int(self.ids.shape[0])

# -----------------------------------------------------------------------------#
# High-Level: Index-Lifecycle
# -----------------------------------------------------------------------------#

class VectorIndex:
    """Vereinheitlichte Hülle um FAISS/ANNOY/BRUTEFORCE mit Meta-Verwaltung."""
    def __init__(self, backend: str, dim: int):
        self.backend = backend
        self.dim = int(dim)
        if backend == "faiss":
            self.impl = _FaissIndex(dim)
        elif backend == "annoy":
            self.impl = _AnnoyIndex(dim)
        else:
            self.impl = _BruteForceIndex(dim)

    @classmethod
    def load_or_create(cls, backend: str, dim_hint: Optional[int] = None):
        _ensure_dir(INDEX_DIR)
        # Laden
        if backend == "faiss" and _HAS_FAISS:
            obj = _FaissIndex.load(dim_hint or 1)
            if obj:
                vi = cls("faiss", obj.index.d)  # type: ignore
                vi.impl = obj
                return vi
        if backend == "annoy" and _HAS_ANNOY:
            obj = _AnnoyIndex.load(dim_hint or 1)
            if obj:
                vi = cls("annoy", dim_hint or obj.dim)
                vi.impl = obj
                return vi
        if backend == "bruteforce":
            obj = _BruteForceIndex.load(dim_hint or 1)
            if obj:
                vi = cls("bruteforce", obj.vecs.shape[1] if obj.vecs is not None else (dim_hint or 1))
                vi.impl = obj
                return vi
        # Neu
        if backend in ("faiss", "annoy") and dim_hint is None:
            # Wenn keine Dim bekannt ist, wird beim ersten add_batch gesetzt
            dim_hint = 0
        return cls(backend, dim_hint or 0)

    def save(self):
        self.impl.save()
        meta = _load_meta()
        meta["backend"] = self.backend
        meta["dim"] = self.dim
        meta["count"] = self.impl.count()
        meta["built_at"] = int(time.time())
        _save_meta(meta)

    def add_batch(self, ids: np.ndarray, vecs: np.ndarray):
        if self.dim == 0:
            self.dim = int(vecs.shape[1])
        self.impl.add_batch(ids, vecs)

    def query(self, vec: np.ndarray, k: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        return self.impl.query(vec, k)

    def count(self) -> int:
        return self.impl.count()

# -----------------------------------------------------------------------------#
# Migration / Rebuild
# -----------------------------------------------------------------------------#

def _collect_vectors(batch_size: int = 2000) -> Tuple[np.ndarray, np.ndarray, int]:
    """
    Lädt alle SnapChain-BLOBs, extrahiert Vektoren, liefert (ids, vecs, dim).
    Ignoriert Einträge ohne brauchbare Vektoren (z. B. leere patterns).
    """
    ids: List[int] = []
    vecs: List[np.ndarray] = []
    dim = None
    n_all = 0
    for chain_id, blob in _iter_snapchains_sql(batch_size=batch_size):
        n_all += 1
        v = _decode_blob_get_vector(blob)
        if v is None or v.size == 0:
            continue
        if dim is None:
            dim = int(v.size)
        elif v.size != dim:
            # Pad/Trim inkonsistenter Dimensionen robust auf dim
            if v.size > dim:
                v = v[:dim]
            else:
                pad = np.zeros((dim - v.size,), dtype=np.float32)
                v = np.concatenate([v, pad], axis=0)
        ids.append(chain_id)
        vecs.append(v)
    if not ids:
        return np.zeros((0,), dtype=np.int64), np.zeros((0, 1), dtype=np.float32), 0
    V = np.vstack(vecs).astype(np.float32)
    I = np.array(ids, dtype=np.int64)
    return I, V, int(dim or V.shape[1])

def rebuild_index(batch_size: int = 2000, prefer_backend: Optional[str] = None) -> Dict[str, Any]:
    """
    Baut den Index von Grund auf neu (alle Einträge).
    """
    if not is_enabled():
        return {"ok": False, "msg": "VectorDB deaktiviert (enabled=False)."}

    # Daten sammeln
    I, V, dim = _collect_vectors(batch_size=batch_size)
    if I.shape[0] == 0:
        return {"ok": False, "msg": "Keine SnapChain-Vektoren in DB gefunden."}

    backend = _resolve_backend(prefer_backend)
    vi = VectorIndex.load_or_create(backend, dim_hint=dim)
    # frisch beginnen -> Indexdateien löschen
    try:
        if os.path.isfile(FAISS_PATH): os.remove(FAISS_PATH)
        if os.path.isfile(ANNOY_PATH): os.remove(ANNOY_PATH)
        if os.path.isfile(BRUTE_VECS_PATH): os.remove(BRUTE_VECS_PATH)
        if os.path.isfile(BRUTE_IDS_PATH): os.remove(BRUTE_IDS_PATH)
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.vector_migration.pass.2",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

    # In sinnvollen Batches hinzufügen (RAM-freundlich)
    bs = max(10000, min(200000, batch_size * 10))
    n = I.shape[0]
    for start in range(0, n, bs):
        end = min(n, start + bs)
        vi.add_batch(I[start:end], V[start:end])

    vi.save()
    return {
        "ok": True,
        "msg": "Index neu aufgebaut",
        "backend": backend,
        "dim": vi.dim,
        "count": vi.count(),
        "threshold": vector_db_threshold(),
    }

def migrate_if_needed(batch_size: int = 2000, prefer_backend: Optional[str] = None) -> Dict[str, Any]:
    """
    Prüft Schwellwert & Enabled und baut bei Bedarf den Index.
    """
    if not is_enabled():
        return {"ok": False, "msg": "VectorDB deaktiviert (enabled=False).", "active": False}

    count = _count_snapchains_sql()
    th = vector_db_threshold()
    if count < th:
        return {"ok": True, "msg": f"VectorDB noch nicht aktiviert (count={count} < threshold={th}).", "active": False}

    # Index laden oder neu bauen, wenn leer
    backend = _resolve_backend(prefer_backend)
    vi = VectorIndex.load_or_create(backend)
    if vi.count() > 0 and vi.dim > 0:
        # Bereits vorhanden: aktiv
        meta = _load_meta()
        meta["backend"] = backend
        meta["dim"] = vi.dim
        meta["count"] = vi.count()
        meta["built_at"] = meta.get("built_at") or int(time.time())
        _save_meta(meta)
        return {"ok": True, "msg": "VectorDB aktiv (bereits vorhanden).", "backend": backend, "active": True, "count": vi.count(), "dim": vi.dim}

    # Sonst: neu aufbauen
    res = rebuild_index(batch_size=batch_size, prefer_backend=backend)
    res["active"] = bool(res.get("ok"))
    return res

# -----------------------------------------------------------------------------#
# Inkrementelle Updates & Abfragen
# -----------------------------------------------------------------------------#

def _load_index_or_raise() -> VectorIndex:
    meta = _load_meta()
    backend = _resolve_backend(meta.get("backend"))
    vi = VectorIndex.load_or_create(backend)
    if vi.count() == 0 or vi.dim == 0:
        raise RuntimeError("Vector-Index ist leer – bitte 'build' ausführen.")
    return vi

def add_or_update(chain_id: int, vec: np.ndarray) -> None:
    """
    Fügt einen Vektor hinzu (oder aktualisiert – bei bruteforce erfolgt Append).
    Für FAISS/Annoy: re-save/merge – je nach Größe ggf. später: entfernte IDs neu bauen.
    """
    meta = _load_meta()
    backend = _resolve_backend(meta.get("backend"))
    vi = VectorIndex.load_or_create(backend, dim_hint=vec.size)
    ids = np.array([int(chain_id)], dtype=np.int64)
    vec = vec.reshape(1, -1).astype(np.float32)
    # normalize
    n = np.linalg.norm(vec, axis=1, keepdims=True) + 1e-12
    vec = vec / n
    vi.add_batch(ids, vec)
    vi.save()

def _get_vector_by_chain_id(chain_id: int) -> Optional[np.ndarray]:
    """Lädt einen einzelnen Vektor für eine chain_id aus der DB."""
    with _get_conn() as c:
        cur = c.execute("SELECT blob FROM snapchains WHERE id=?", (int(chain_id),))
        r = cur.fetchone()
        if not r:
            return None
        return _decode_blob_get_vector(r["blob"])

def query(vec: np.ndarray, k: int = 10) -> List[Tuple[int, float]]:
    """k-NN Suche: Rückgabe Liste (chain_id, score)."""
    vi = _load_index_or_raise()
    # normalize
    vec = vec.astype(np.float32)
    n = np.linalg.norm(vec) + 1e-12
    vec = vec / n
    ids, sims = vi.query(vec, k=k)
    out = []
    for i in range(len(ids)):
        if int(ids[i]) < 0:
            continue
        out.append((int(ids[i]), float(sims[i])))
    return out

def query_by_chain_id(chain_id: int, k: int = 10) -> List[Tuple[int, float]]:
    vec = _get_vector_by_chain_id(chain_id)
    if vec is None:
        return []
    return query(vec, k=k)

# -----------------------------------------------------------------------------#
# CLI-Interface (für Admin-Tasks)
# -----------------------------------------------------------------------------#

def _cmd_status(args):
    meta = _load_meta()
    print(json.dumps({
        "enabled": meta.get("enabled", True),
        "backend": current_backend(),
        "available_backends": available_backends(),
        "dim": meta.get("dim"),
        "count": meta.get("count"),
        "built_at": meta.get("built_at"),
        "threshold": vector_db_threshold(),
        "index_dir": INDEX_DIR,
    }, indent=2, ensure_ascii=False))

def _cmd_enable(args):
    set_enabled(True)
    print("VectorDB enabled=True")

def _cmd_disable(args):
    set_enabled(False)
    print("VectorDB enabled=False")

def _cmd_backend(args):
    if args.set:
        set_backend(args.set)
        print("Backend gesetzt auf:", args.set)
    print("Current backend:", current_backend())
    print("Available:", ", ".join(available_backends()))

def _cmd_build(args):
    res = rebuild_index(batch_size=args.batch, prefer_backend=args.backend)
    print(json.dumps(res, indent=2, ensure_ascii=False))

def _cmd_migrate_if_needed(args):
    res = migrate_if_needed(batch_size=args.batch, prefer_backend=args.backend)
    print(json.dumps(res, indent=2, ensure_ascii=False))

def _cmd_search(args):
    k = int(args.k)
    if args.id is not None:
        out = query_by_chain_id(int(args.id), k=k)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    if args.vec is not None:
        parts = [p for p in args.vec.split(",") if p.strip() != ""]
        arr = np.array([float(x) for x in parts], dtype=np.float32)
        out = query(arr, k=k)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    print("Bitte --id oder --vec angeben.")

def main_cli():
    ap = argparse.ArgumentParser(prog="oroma-vector", description="ORÓMA v2.11 – Vector DB Management")
    sub = ap.add_subparsers()

    sp = sub.add_parser("status", help="Zeigt Status/Meta/Backends")
    sp.set_defaults(func=_cmd_status)

    sp = sub.add_parser("enable", help="VectorDB aktivieren")
    sp.set_defaults(func=_cmd_enable)

    sp = sub.add_parser("disable", help="VectorDB deaktivieren")
    sp.set_defaults(func=_cmd_disable)

    sp = sub.add_parser("backend", help="Backend anzeigen/setzen")
    sp.add_argument("--set", choices=["auto","faiss","annoy","bruteforce"], help="Backend setzen")
    sp.set_defaults(func=_cmd_backend)

    sp = sub.add_parser("build", help="Index vollständig neu aufbauen")
    sp.add_argument("--batch", type=int, default=2000, help="DB-Batch-Size")
    sp.add_argument("--backend", choices=["auto","faiss","annoy","bruteforce"], default=None)
    sp.set_defaults(func=_cmd_build)

    sp = sub.add_parser("migrate", help="Nur migrieren, wenn Schwellwert erreicht")
    sp.add_argument("--batch", type=int, default=2000)
    sp.add_argument("--backend", choices=["auto","faiss","annoy","bruteforce"], default=None)
    sp.set_defaults(func=_cmd_migrate_if_needed)

    sp = sub.add_parser("search", help="Suche Nachbarn (per --id oder --vec)")
    sp.add_argument("--id", type=int, default=None, help="chain_id als Ursprung")
    sp.add_argument("--vec", type=str, default=None, help="Kommagetrennter Vektor, z.B. '0.1,0.2,0.3'")
    sp.add_argument("--k", type=int, default=10)
    sp.set_defaults(func=_cmd_search)

    args = ap.parse_args()
    if not hasattr(args, "func"):
        ap.print_help()
        return
    # Indexverzeichnis sicherstellen
    _ensure_dir(INDEX_DIR)
    args.func(args)

if __name__ == "__main__":
    main_cli()
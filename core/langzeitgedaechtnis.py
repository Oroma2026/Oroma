#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/langzeitgedaechtnis.py
# Projekt:   ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:     LangzeitGedaechtnis – LTM Persistenz für SnapChains + Dedupe (Hash in notes) + Similarity Search (Annoy/FAISS optional)
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert das „Langzeitgedächtnis“ (LTM) für ORÓMA – im Sinne von:
#   - persistenter Speicherung und Wiederauffindbarkeit von SnapChains
#   - Hash-basierter Deduplikation (identische Episoden werden nicht neu gespeichert,
#     sondern „ge-upweighted“ und in der Qualität geglättet)
#   - optionaler Ähnlichkeitssuche über einen Vektorindex (Annoy/FAISS)
#   - stets headless (keine GUI/Qt/Wayland/X11 Anforderungen)
#
# WICHTIG: In der aktuellen v3.7.3-Baseline nutzt dieses Modul NICHT eine separate
# "ltm_chains" Tabelle, sondern direkt die bestehende Haupttabelle:
#
#   oroma.db → Tabelle `snapchains`
#
# Das ist absichtlich kompatibel (kein neues Schema notwendig) und erlaubt, LTM
# als „Overlay“ über das bestehende episodische Gedächtnis zu betreiben.
#
# KERNIDEE: DEDUPE ÜBER HASH IN `snapchains.notes`
# ────────────────────────────────────────────────
# Beim Speichern wird eine stabile SHA1-Signatur der Chain erzeugt:
#   - _normalize_for_hash(chain) extrahiert nur nicht-volatile Merkmale
#     (z. B. meta.game/origin, Pattern-Centroids/Pattern-Vektoren, Länge)
#   - daraus wird ein JSON erzeugt (sort_keys) und SHA1 gebildet
#
# Diese Signatur wird in `snapchains.notes` abgelegt und als Dedupe-Key benutzt:
#   SELECT id, weight, quality FROM snapchains WHERE notes=? LIMIT 1
#
# Treffer (Duplikat):
#   - es wird KEIN neuer Datensatz angelegt
#   - weight wird moderat erhöht (bis Cap; z. B. 10.0)
#   - quality wird geglättet (simple mean: (old_q + new_q)/2)
#   - ts wird aktualisiert (Aktivitätsmarker)
#
# Neuer Eintrag:
#   - INSERT in snapchains (ts, quality, blob, exported, status, origin, notes, weight)
#
# WARUM `notes`?
# ──────────────
# `notes` ist im ORÓMA-Schema ein flexibles Feld und eignet sich in der Praxis
# gut als „Hash-Carrier“, ohne dass eine Schema-Migration nötig ist.
#
# VEKTOREXTRAKTION (CENTROID) FÜR SEARCH
# ─────────────────────────────────────
# Für Similarity Search wird aus einer SnapChain ein kompakter Vektor erzeugt:
#   - _chain_centroid(chain, target_dim)
#     • iteriert über chain.patterns
#     • nutzt p.centroid() oder p.centroid Feld (tolerant)
#     • mittelt über Patterns, kürzt/padded auf vector_dim
#     • rundet Werte (stabiler, weniger Noise)
#
# VECTOR INDEX (OPTIONAL)
# ──────────────────────
# Index ist optional, das Modul funktioniert auch ohne externe Libs:
#
#   - Annoy (annoy.AnnoyIndex)  → angular distance (cosine-ähnlich)
#   - FAISS (faiss.IndexFlatL2) → L2 Suche (benötigt numpy)
#   - Fallback: Cosine-Scan über die letzten N Chains aus DB (z. B. 500)
#
# Der Index wird „best effort“ gepflegt:
#   - bei save_snapchain() wird NACH dem Commit ein Centroid gerechnet
#   - dann _add_to_index(chain_id, vec)
#   - bei Annoy wird periodisch rebuilded (alle N adds), um Suche aktuell zu halten
#
# DREAMWORKER-KOMPAT (WICHTIG)
# ───────────────────────────
# Dieses Modul bietet bewusst eine kleine, stabile API, die DreamWorker/Tools nutzen können:
#   - list_recent(limit=20, status="active") → liefert IDs der jüngsten Chains
#   - load_snapchain(chain_id)               → lädt SnapChain aus snapchains.blob
#   - search_similar(chain, top_k=5)         → IDs ähnlicher Chains (Index oder Fallback)
#   - stats()                                → einfache Aggregationen (counts + avg quality/weight)
#
# SERIALISIERUNG
# ──────────────
# SnapChains werden bevorzugt als Bytes gespeichert:
#   - chain.as_blob() (JSON bytes)
# Fallback:
#   - json.dumps(chain.to_dict()).encode("utf-8")
# Beim Laden:
#   - SnapChain.from_blob(blob)
# Fallback:
#   - json.loads(...) + SnapChain.from_dict(...)
#
# DB / LOCK-ROBUSTHEIT (ORCHESTRATOR-BETRIEB)
# ──────────────────────────────────────────
# - DB-Zugriffe laufen über core.sql_manager.get_conn()
# - ensure_schema() wird im __init__ einmalig aufgerufen
# - pro Operation wird eine frische Connection genutzt (kurze Transaktionen)
# - Fehler werden defensiv behandelt; Search fällt im Zweifel auf Fallback-Scan zurück
#
# KPI / METRICS (BEST EFFORT)
# ──────────────────────────
# Dieses Modul schreibt optionale KPIs über core.sql_manager.insert_metric():
#   - kpi:dedupe_event     (Duplikat erkannt)
#   - kpi:new_chain_event  (neue Chain gespeichert)
# Wenn metrics insert wegen Locks/Import nicht möglich ist, wird es suppressed geloggt.
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
#   OROMA_MEMORY_DIM=<int>            # Ziel-Dimension für Centroid/Vektorindex (Default in Code: 9)
#   OROMA_ANNOY_REBUILD=<int>         # nach wie vielen Adds Annoy rebuild (Default in Code: 256, min 16)
#
# Optional (Projektumfeld):
#   OROMA_DB_PATH=<path>              # wird i. d. R. über sql_manager gesteuert
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class LangzeitGedaechtnis:
#   - save_snapchain(chain: SnapChain, quality: float=0.0) -> int
#   - load_snapchain(chain_id: int) -> Optional[SnapChain]
#   - search_similar(chain: SnapChain, top_k: int=5) -> List[int]
#   - list_recent(limit: int=20, status: str|None="active") -> List[{"id":int}]
#   - stats() -> dict
#
# Factory:
#   - init_default_memory() -> LangzeitGedaechtnis
#
# INVARIANTEN (BITTE NICHT „WEGOPTIMIEREN“)
# ─────────────────────────────────────────
# - Muss headless & import-leicht bleiben (stdlib first; Annoy/FAISS optional).
# - Dedupe über notes-hash ist bewusst kompatibel (keine Schema-Migration erzwingen).
# - Fallback-Search muss funktionieren, auch wenn Index-Libs fehlen.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import json, math, logging, sqlite3, time, hashlib, os
from typing import List, Dict, Any, Optional, Iterable, Tuple

import logging
from core import log_guard
logger = logging.getLogger(__name__)
# Projekt-Imports (DB)
try:
    from core.sql_manager import get_conn, ensure_schema  # type: ignore
except Exception as e:
    raise RuntimeError(f"[langzeitgedaechtnis] sql_manager fehlt: {e}")

# SnapChain
try:
    from core.snapchain import SnapChain
except Exception as e:
    raise RuntimeError(f"[langzeitgedaechtnis] SnapChain fehlt: {e}")

# Optional: NumPy
try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

# Optional: Annoy
try:
    from annoy import AnnoyIndex  # type: ignore
    _HAS_ANNOY = True
except Exception:
    _HAS_ANNOY = False

# Optional: FAISS
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

# Logging
LOG = logging.getLogger("oroma.langzeitgedaechtnis")
if not LOG.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.INFO)

# --- KPI: Helper (safe, ohne harte Abhängigkeit) -----------------------------
def _kpi(name: str, v: float = 1.0) -> None:
    try:
        from core import sql_manager  # lazy import
        sql_manager.insert_metric(name, float(v))
    except Exception as e:
        log_guard.log_suppressed(logger, key="langzeitgedaechtnis.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

# =============================================================================
# Utils
# =============================================================================

def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    if _HAS_NUMPY:
        va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
        denom = (float(np.linalg.norm(va)) * float(np.linalg.norm(vb))) or 1e-12
        return float(float(va.dot(vb)) / denom)
    num = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) ** 2 for x in a)) or 1e-12
    nb = math.sqrt(sum(float(y) ** 2 for y in b)) or 1e-12
    return num / (na * nb)

def _round_vec(v: Iterable[float], nd: int = 5) -> List[float]:
    return [round(float(x), nd) for x in list(v or [])]

def _chain_centroid(chain: SnapChain, target_dim: Optional[int] = None) -> List[float]:
    try:
        pats = getattr(chain, "patterns", []) or []
    except Exception:
        pats = []
    rows: List[List[float]] = []
    for p in pats:
        c = []
        try:
            c = p.centroid() if callable(getattr(p, "centroid", None)) else getattr(p, "centroid", [])  # type: ignore
        except Exception:
            c = getattr(p, "centroid", []) or []
        if isinstance(c, (list, tuple)) and c:
            rows.append([float(x) for x in c])
    if not rows:
        return []
    dim = min(len(r) for r in rows)
    acc = [0.0] * dim
    for r in rows:
        for i in range(dim):
            acc[i] += float(r[i])
    cen = [x / len(rows) for x in acc]
    if target_dim is None:
        return _round_vec(cen)
    if len(cen) >= target_dim:
        return _round_vec(cen[:target_dim])
    return _round_vec(cen + [0.0] * (target_dim - len(cen)))

def _normalize_for_hash(chain: SnapChain) -> Dict[str, Any]:
    meta_src = getattr(chain, "metadata", {}) or {}
    meta = {k: meta_src.get(k) for k in ("game", "origin") if k in meta_src}
    vecs: List[List[float]] = []
    try:
        for p in getattr(chain, "patterns", []) or []:
            arr = getattr(p, "patterns", None)
            if isinstance(arr, list) and arr and isinstance(arr[0], (list, tuple)):
                for v in arr:
                    if isinstance(v, (list, tuple)):
                        vecs.append(_round_vec(v))
                continue
            c = getattr(p, "centroid", []) or []
            if isinstance(c, (list, tuple)) and c:
                vecs.append(_round_vec(c))
    except Exception as e:
        log_guard.log_suppressed(logger, key="langzeitgedaechtnis.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return {"meta": meta, "vecs": vecs, "len": len(vecs)}

def _hash_chain(chain: SnapChain) -> str:
    try:
        base = _normalize_for_hash(chain)
        raw = json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()
    except Exception:
        return ""

# =============================================================================
# Kernklasse
# =============================================================================

class LangzeitGedaechtnis:
    """Zentrale Persistenz- und Index-Schicht des ORÓMA-Langzeitgedächtnisses."""

    def __init__(self, *, vector_dim: Optional[int] = None, use_annoy: Optional[bool] = None):
        ensure_schema()
        self.vector_dim = int(vector_dim or int(os.environ.get("OROMA_MEMORY_DIM", "9")))
        self._index = None
        self._ids: List[int] = []
        self._annoy_rebuild_every = max(16, int(os.environ.get("OROMA_ANNOY_REBUILD", "256")))
        self._annoy_since_build = 0

        if (use_annoy is True) or (use_annoy is None and _HAS_ANNOY):
            self._init_annoy()
        elif _HAS_FAISS:
            self._init_faiss()
        else:
            LOG.info("Kein Annoy/FAISS verfügbar – Cosine-Scan Fallback aktiv.")

    # -------------------- Index Init --------------------

    def _init_annoy(self) -> None:
        self._index = AnnoyIndex(self.vector_dim, "angular")
        self._ids.clear()
        LOG.info("Annoy Index init (dim=%d)", self.vector_dim)

    def _init_faiss(self) -> None:
        self._index = faiss.IndexFlatL2(self.vector_dim)  # type: ignore
        self._ids.clear()
        LOG.info("FAISS Index init (dim=%d)", self.vector_dim)

    # -------------------- Persistenz --------------------

    def save_snapchain(self, chain: SnapChain, quality: float = 0.0) -> int:
        """
        Speichert eine SnapChain.
        • Duplikat (gleicher Hash in 'notes') → weight ↑, quality glätten.
        • Neu → Insert + optional Index-Update.
        Rückgabe: ID des gespeicherten (oder aktualisierten) Datensatzes.
        """
        h = _hash_chain(chain)
        now = int(time.time())
        origin = (getattr(chain, "metadata", {}) or {}).get("origin", "memory")
        try:
            blob = chain.as_blob()
        except Exception:
            try:
                blob = json.dumps(chain.to_dict(), ensure_ascii=False).encode("utf-8")
            except Exception as e:
                raise RuntimeError(f"Chain-Serialisierung fehlgeschlagen: {e}")

        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, weight, quality FROM snapchains WHERE notes=? LIMIT 1", (h,))
            row = cur.fetchone()

            if row:
                # Duplikat → upweight + quality smooth
                rid = int(row["id"] if hasattr(row, "keys") else row[0])
                old_w = float(row["weight"] if hasattr(row, "keys") else row[1])
                old_q = float(row["quality"] if hasattr(row, "keys") else row[2])
                new_w = min(10.0, max(0.1, old_w * 1.05))
                new_q = (old_q + float(quality)) / 2.0
                cur.execute(
                    "UPDATE snapchains SET weight=?, quality=?, ts=? WHERE id=?",
                    (new_w, new_q, now, rid),
                )
                _kpi("kpi:dedupe_event", 1.0)  # KPI: Duplikat-Event
                LOG.debug("♻️ Dedupe id=%s → weight=%.3f, quality=%.3f", rid, new_w, new_q)
                return rid

            # Neu
            cur.execute(
                """
                INSERT INTO snapchains (ts, quality, blob, exported, status, origin, notes, weight)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (now, float(quality), sqlite3.Binary(blob), 0, "active", str(origin), h, 1.0),
            )
            rid = int(cur.lastrowid)
            _kpi("kpi:new_chain_event", 1.0)  # KPI: Neuer SnapChain-Event

        # Index erst NACH Commit anfassen
        vec = _chain_centroid(chain, self.vector_dim)
        if vec:
            self._add_to_index(rid, vec)
        LOG.debug("💾 SnapChain neu gespeichert id=%s origin=%s", rid, origin)
        return rid

    def load_snapchain(self, chain_id: int) -> Optional[SnapChain]:
        with get_conn() as conn:
            row = conn.execute("SELECT blob FROM snapchains WHERE id=?", (int(chain_id),)).fetchone()
            if not row:
                return None
            blob = row["blob"] if hasattr(row, "keys") else row[0]
        try:
            return SnapChain.from_blob(bytes(blob))
        except Exception:
            try:
                d = json.loads(blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob)
                return SnapChain.from_dict(d)  # type: ignore
            except Exception as e:
                LOG.warning("load_snapchain id=%s parse-fehler: %s", chain_id, e)
                return None

    # -------------------- Indexpflege & Suche --------------------

    def _add_to_index(self, chain_id: int, vec: List[float]) -> None:
        if self._index is None:
            return
        if _HAS_ANNOY and isinstance(self._index, AnnoyIndex):
            self._index.add_item(int(chain_id), vec)
            self._ids.append(int(chain_id))
            self._annoy_since_build += 1
            if self._annoy_since_build >= self._annoy_rebuild_every:
                self._index.build(10)
                self._annoy_since_build = 0
        elif _HAS_FAISS:
            if not _HAS_NUMPY:
                return
            xb = np.asarray([vec], dtype=np.float32)
            ids = np.asarray([int(chain_id)], dtype=np.int64)
            self._index.add_with_ids(xb, ids)  # type: ignore
            self._ids.append(int(chain_id))

    def search_similar(self, chain: SnapChain, top_k: int = 5) -> List[int]:
        vec = _chain_centroid(chain, self.vector_dim)
        if not vec:
            return []
        if _HAS_ANNOY and isinstance(self._index, AnnoyIndex) and len(self._ids) >= top_k:
            try:
                return list(self._index.get_nns_by_vector(vec, int(top_k), include_distances=False))
            except Exception as e:
                log_guard.log_suppressed(logger, key="langzeitgedaechtnis.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        if _HAS_FAISS and self._ids:
            try:
                if not _HAS_NUMPY:
                    return []
                xb = np.asarray([vec], dtype=np.float32)
                _, I = self._index.search(xb, int(top_k))  # type: ignore
                return [int(i) for i in list(I[0]) if int(i) >= 0]
            except Exception as e:
                log_guard.log_suppressed(logger, key="langzeitgedaechtnis.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        # Fallback: Cosine über letzte N
        with get_conn() as conn:
            rows = conn.execute("SELECT id, blob FROM snapchains ORDER BY id DESC LIMIT 500").fetchall() or []
        sims: List[Tuple[int, float]] = []
        for r in rows:
            rid = int(r["id"] if hasattr(r, "keys") else r[0])
            blob = r["blob"] if hasattr(r, "keys") else r[1]
            try:
                other = SnapChain.from_blob(bytes(blob))
            except Exception:
                try:
                    d = json.loads(blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob)
                    other = SnapChain.from_dict(d)  # type: ignore
                except Exception:
                    continue
            cen = _chain_centroid(other, len(vec))
            if cen:
                sims.append((rid, _cosine(vec, cen)))
        sims.sort(key=lambda t: t[1], reverse=True)
        return [sid for sid, _ in sims[: int(top_k)]]

    # -------------------- DreamWorker-Kompat --------------------

    def list_recent(self, *, limit: int = 20, status: Optional[str] = "active") -> List[Dict[str, Any]]:
        sql = "SELECT id FROM snapchains"
        args: List[Any] = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(max(0, limit)))

        with get_conn() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall() or []
        return [{"id": int(r["id"] if hasattr(r, "keys") else r[0])} for r in rows]

    def stats(self) -> Dict[str, Any]:
        out = {"chains": 0, "active": 0, "compressed": 0, "avg_quality": 0.0, "avg_weight": 0.0}
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS n FROM snapchains")
            row = cur.fetchone(); out["chains"] = int(row["n"] if hasattr(row, "keys") else row[0] or 0)
            cur.execute("SELECT COUNT(*) AS n FROM snapchains WHERE status='active'")
            row = cur.fetchone(); out["active"] = int(row["n"] if hasattr(row, "keys") else row[0] or 0)
            cur.execute("SELECT COUNT(*) AS n FROM snapchains WHERE status='compressed'")
            row = cur.fetchone(); out["compressed"] = int(row["n"] if hasattr(row, "keys") else row[0] or 0)
            cur.execute("SELECT AVG(quality) AS q, AVG(weight) AS w FROM snapchains WHERE status='active'")
            row = cur.fetchone()
            if row:
                out["avg_quality"] = float((row["q"] if hasattr(row, "keys") else row[0]) or 0.0)
                out["avg_weight"]  = float((row["w"] if hasattr(row, "keys") else row[1]) or 0.0)
        return out

# =============================================================================
# Factory
# =============================================================================

def init_default_memory() -> LangzeitGedaechtnis:
    """Convenience-Factory für Standard-Initialisierung."""
    return LangzeitGedaechtnis()
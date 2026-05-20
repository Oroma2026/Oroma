#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/rag_bridge.py
# Projekt: ORÓMA
# Modul:   RAG-Bridge (lokal, FTS5) + optionaler Pattern-Rerank
# Version: v3.8-r2
# Stand:   2025-11-24
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   RAG-Bridge (lokal, FTS5) für semantisch-nahe Volltextsuche.
#   - Dokumentverwaltung (documents, chunks)
#   - Suche mit bm25-Ranking und Snippets (FTS5 highlight/snippet)
#   - Drop-in kompatibel zu v3.0, aber mit besseren Ergebnissen
#
# Neu in v3.8-r2
# ──────────────
#   • MATCH-Query wird aus der Frage abgeleitet:
#       - Satzzeichen werden entfernt (Fragezeichen etc. → kein fts5-Fehler mehr)
#       - Deutsche Frage-/Füllwörter (Stopwords) werden herausgefiltert
#       - Begriffe, die im Index überhaupt nicht vorkommen, werden verworfen
#         (pro Token kurzer SELECT 1 FROM chunks WHERE chunks MATCH 'token')
#       - Übrig bleibt ein kompakter MATCH-Ausdruck wie
#             "Hauptstadt Frankreich"
#         statt der kompletten Frage.
#   • MATCH-Ausdruck wird als Literal in das SQL eingebaut, top_k bleibt
#     weiterhin per Placeholder parametrisierbar (nur FTS-MATCH mag kein '?').
#
# Neu in v3.8-r1
# ──────────────
#   • Optionaler Cosine-Rerank der FTS-Treffer über Vektoren:
#       - Funktion rerank_by_pattern(question, hits, fusion=None, top_k=None)
#       - nutzt, falls vorhanden, core.fusion.FusionEngine (encode_text)
#       - robust: wenn kein FusionEngine verfügbar ist oder ein Fehler
#         auftritt, bleiben die Original-Hits unverändert.
#
# Integration / Beispiel
# ──────────────────────
#   store = RAGStore(db_path)
#   hits  = store.search("Was ist ORÓMA?", top_k=20)
#
#   # UI-/API-Handler (z. B. /ask?rerank=1):
#   hits  = store.rerank_by_pattern("Was ist ORÓMA?", hits, top_k=5)
#
#   Die Funktion hängt jedem Treffer optional das Feld "sim" (Cosine-Score)
#   an und sortiert nach sim DESC.
# =============================================================================

from __future__ import annotations

import sqlite3
from core import db_writer_client as _dbw
import os
import time
import logging
import re
from typing import List, Tuple, Optional, Dict, Any, Sequence
from core.log_guard import log_suppressed
import logging

logger = logging.getLogger("oroma.rag")
if not logger.handlers:
    h = logging.StreamHandler()
    f = logging.Formatter("[rag] %(levelname)s: %(message)s")
    h.setFormatter(f)
    logger.addHandler(h)
logger.setLevel(logging.INFO)

# Optional: FusionEngine für Vektor-Rerank
try:
    from core.fusion import FusionEngine  # type: ignore
except Exception:
    FusionEngine = None  # type: ignore[assignment]

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  title TEXT,
  created_ts REAL NOT NULL,
  meta_json TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks
USING fts5(content, doc_id UNINDEXED, source UNINDEXED, tokenize = "porter");
"""


# =============================================================================
# Hilfsfunktionen für FTS-MATCH-Query
# =============================================================================

# kleine Stopword-Liste (deutsche Frage-/Füllwörter)
_STOPWORDS = {
    "der", "die", "das", "und", "oder", "mit", "im", "in", "von", "zu",
    "den", "dem", "ein", "eine", "einer", "eines", "einen",
    "ist", "war", "sind", "sein", "wird",
    "wer", "wie", "was", "wo", "wann",
    "welcher", "welche", "welches",
    "für", "auf", "an", "am", "aus", "um",
}


def _tokenize_question(text: str) -> List[str]:
    """
    Wandelt eine Nutzerfrage in eine Liste von FTS-geeigneten Tokens um:

      • entfernt Sonderzeichen (Fragezeichen, Kommas, Klammern, …)
      • reduziert Mehrfach-Leerzeichen
      • trennt an Leerzeichen
      • filtert sehr kurze Tokens (≤1 Zeichen)
      • behält nur Tokens mit mind. einem ASCII-Buchstaben (A–Z, a–z)
    """
    text = text or ""
    # Alles was kein Wortzeichen/Whitespace ist → Leerzeichen
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    raw_tokens = text.split(" ")
    out: List[str] = []
    for t in raw_tokens:
        t = t.strip()
        if len(t) <= 1:
            continue
        # mindestens ein ASCII-Buchstabe: schützt vor reinem Zahlen-/Sondermüll
        if not any(ch.isascii() and ch.isalpha() for ch in t):
            continue
        out.append(t)
    return out


def _build_match_expr(con: sqlite3.Connection, question: str) -> Tuple[str, str]:
    """
    Erzeugt aus der Originalfrage einen FTS5-MATCH-Ausdruck.

    Pipeline:
      1. tokenize → Roh-Tokens
      2. Stopwords filtern (deutsche Frage-/Füllwörter)
      3. pro Token prüfen, ob es im Index überhaupt vorkommt
         (SELECT 1 FROM chunks WHERE chunks MATCH 'token' LIMIT 1)
      4. falls danach noch Tokens übrig sind → "t1 t2 t3"
         (FTS5-Standard: Dokument muss alle Tokens enthalten)
      5. falls nichts übrig bleibt → Fallback: erstes sinnvolles Token

    Rückgabe:
      (match_expr, original_question)
    """
    orig = question or ""
    tokens = _tokenize_question(orig)
    if not tokens:
        return "", orig

    # Stopwords rauswerfen
    candidates: List[str] = [
        t for t in tokens
        if t.lower() not in _STOPWORDS
    ]
    if not candidates:
        candidates = tokens

    # Gegen den Index testen, ob Token irgendwo vorkommt
    cur = con.cursor()
    valid: List[str] = []
    for t in candidates:
        lit = t.replace("'", "''")
        sql = f"SELECT 1 FROM chunks WHERE chunks MATCH '{lit}' LIMIT 1"
        try:
            cur.execute(sql)
            if cur.fetchone():
                valid.append(t)
        except sqlite3.OperationalError:
            # Token ist aus Sicht von FTS ungültig → überspringen
            continue

    if not valid:
        # Fallback: wenigstens EIN Token behalten, damit FTS arbeiten kann
        valid = candidates[:1]

    match_expr = " ".join(valid)
    return match_expr, orig


# =============================================================================
# RAGStore
# =============================================================================



def _dbw_enabled() -> bool:
    try:
        return bool(int(os.getenv("OROMA_DBW_ENABLE", "0")))
    except Exception:
        return False

class RAGStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        con = self._connect()
        try:
            con.executescript(SCHEMA)
            con.commit()
        finally:
            con.close()

    # ---------------- Chunker ----------------
    def split_into_chunks(self, text: str, max_chars: int = 800) -> List[str]:
        """Zerteilt Text in überlappungsfreie Chunks (Default: 800 Zeichen)."""
        text = re.sub(r"\s+", " ", text or "").strip()
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]

    # ---------------- Dokumente ----------------
    def add_document(
        self,
        source: str,
        title: str,
        chunks: List[str],
        meta_json: Optional[str] = "{}"
    ) -> int:
        con = self._connect()
        try:
            cur = con.cursor()
            if _dbw_enabled() and "_dbw" in globals() and _dbw is not None and str(self.db_path).endswith("knowledge.db"):
                doc_sql = "INSERT INTO documents(source, title, created_ts, meta_json) VALUES(?,?,?,?)"
                doc_id = int(_dbw.exec_lastrowid(
                    doc_sql,
                    params=[source, title, float(time.time()), meta_json],
                    tag="rag.add_document",
                    priority="normal",
                    timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_KNOWLEDGE", "60000")),
                    db="knowledge",
                ))
                ch_sql = "INSERT INTO chunks(rowid, content, doc_id, source) VALUES(NULL, ?, ?, ?)"
                plist = [[ch, doc_id, source] for ch in chunks]
                if plist:
                    _dbw.executemany(
                        ch_sql,
                        plist,
                        tag="rag.add_document.chunks",
                        priority="normal",
                        timeout_ms=int(os.getenv("OROMA_DBW_TIMEOUT_MS_KNOWLEDGE", "60000")),
                        db="knowledge",
                    )
                return int(doc_id)
            cur.execute(
                "INSERT INTO documents(source, title, created_ts, meta_json) "
                "VALUES(?,?,?,?)",
                (source, title, time.time(), meta_json),
            )
            doc_id = cur.lastrowid
            for ch in chunks:
                cur.execute(
                    "INSERT INTO chunks(rowid, content, doc_id, source) "
                    "VALUES(NULL, ?, ?, ?)",
                    (ch, doc_id, source),
                )
            con.commit()
            logger.info(
                "[RAG] Dokument hinzugefügt: id=%s, source=%s, title=%s, chunks=%s",
                doc_id, source, title, len(chunks),
            )
            return int(doc_id)
        finally:
            con.close()

    # ---------------- Suche ----------------
    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        FTS5-Suche mit bm25-Ranking + Snippet.

        Rückgabe: Liste von Dicts:
          [{content, source, score (kleiner=besser), snippet}]
        """
        query = (query or "").strip()
        if not query:
            return []

        con = self._connect()
        try:
            cur = con.cursor()

            match_expr, orig = _build_match_expr(con, query)
            if not match_expr:
                logger.info(
                    "[RAG] Suche: MATCH='<leer>' (orig='%s') → 0 Treffer",
                    orig,
                )
                return []

            # SQL-Literal für MATCH (kein Placeholder, FTS5 mag kein '?')
            match_sql = match_expr.replace("'", "''")

            sql = f"""
                SELECT
                  rowid,
                  source,
                  content,
                  bm25(chunks) AS score,
                  snippet(chunks, -1, '<<', '>>', ' … ', 8) AS snip
                FROM chunks
                WHERE chunks MATCH '{match_sql}'
                ORDER BY score ASC
                LIMIT ?
            """

            try:
                cur.execute(sql, (int(top_k),))
            except sqlite3.OperationalError as e:
                logger.warning(
                    "[RAG] FTS-Fehler bei MATCH='%s' (orig='%s'): %s",
                    match_expr, orig, e,
                )
                return []

            rows = cur.fetchall()
            logger.info(
                "[RAG] Suche: MATCH='%s' (orig='%s') → %d Treffer",
                match_expr, orig, len(rows),
            )

            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "content": r["content"],
                        "source": r["source"],
                        "score": float(r["score"])
                        if r["score"] is not None
                        else 0.0,
                        "snippet": r["snip"] or "",
                    }
                )
            return out
        finally:
            con.close()

    def list_docs(self) -> List[dict]:
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT id, source, title, created_ts "
                "FROM documents ORDER BY created_ts DESC"
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "source": r["source"],
                    "title": r["title"],
                    "ts": int(r["created_ts"]),
                }
                for r in rows
            ]
        finally:
            con.close()

    # Proxy-Methode für bench_rag.py (ruft die Modul-Funktion auf)
    def rerank_by_pattern(
        self,
        question: str,
        hits: List[Dict[str, Any]],
        *,
        fusion: Optional[Any] = None,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return rerank_by_pattern(
            question,
            hits,
            fusion=fusion,
            top_k=top_k,
        )


# -----------------------------------------------------------------------------
# Einfache Antwort-Synthese (unchanged, aber typisiert)
# -----------------------------------------------------------------------------
def synthesize_answer(question: str, passages: List[str]) -> str:
    if not passages:
        return f"Keine relevanten Stellen gefunden für: {question}"
    out = ["Relevante Stellen:"]
    for i, p in enumerate(passages, 1):
        out.append(f"[{i}] {p}")
    return "\n".join(out)


# -----------------------------------------------------------------------------
# Optionaler Cosine-Rerank via FusionEngine
# -----------------------------------------------------------------------------

def _as_float_list(v: Any) -> Optional[List[float]]:
    try:
        if isinstance(v, (list, tuple)):
            return [float(x) for x in v]
    except Exception:
        return None
    return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Einfache Cosine-Ähnlichkeit (ohne NumPy)."""
    if not a or not b:
        return 0.0
    d = min(len(a), len(b))
    if d <= 0:
        return 0.0
    num = 0.0
    na = 0.0
    nb = 0.0
    for i in range(d):
        fa = float(a[i])
        fb = float(b[i])
        num += fa * fb
        na += fa * fa
        nb += fb * fb
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(num / ((na ** 0.5) * (nb ** 0.5)))


def rerank_by_pattern(
    question: str,
    hits: List[Dict[str, Any]],
    *,
    fusion: Optional[Any] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Rerankt FTS-Treffer per Cosine-Similarität auf Basis von Text-Embeddings.

    Design
    ------
    • Verwendet, falls verfügbar, core.fusion.FusionEngine:
          - entweder über den Parameter 'fusion'
          - oder durch lazy Instanziierung FusionEngine()

    • Erwartet, dass fusion.encode_text(text: str) -> Sequence[float]
      zurückgibt. Falls das nicht der Fall ist oder ein Fehler auftritt,
      bleiben die Treffer unverändert.

    Parameter
    ---------
    question : str
        Benutzerfrage / Query-Text.
    hits : List[dict]
        Trefferliste aus RAGStore.search().
    fusion : FusionEngine|Any|None
        Optional existierende FusionEngine-Instanz; wenn None, wird
        versucht, FusionEngine() zu instanziieren.
    top_k : int|None
        Optional Begrenzung nach Rerank (Standard: keine weitere Kürzung).

    Rückgabe
    --------
    List[dict] : Trefferliste, absteigend nach 'sim' sortiert.
                 Jeder Eintrag erhält zusätzlich:
                    - "sim": Cosine-Score (float, 0..1)
    """
    question = (question or "").strip()
    if not question or not hits:
        return hits

    # FusionEngine bereitstellen (optional / best effort)
    eng = fusion
    if eng is None and FusionEngine is not None:
        try:
            eng = FusionEngine()  # type: ignore[call-arg]
        except Exception as e:
            logger.info(
                "[RAG] FusionEngine konnte nicht instanziiert werden: %s", e
            )
            eng = None

    if eng is None or not hasattr(eng, "encode_text"):
        logger.debug("[RAG] Kein FusionEngine-encode_text verfügbar → kein Rerank")
        return hits

    try:
        q_vec_raw = eng.encode_text(question)  # type: ignore[attr-defined]
    except Exception as e:
        logger.warning("[RAG] encode_text(question) fehlgeschlagen: %s", e)
        return hits

    q_vec = _as_float_list(q_vec_raw)
    if not q_vec:
        logger.debug("[RAG] Query-Embedding leer → kein Rerank")
        return hits

    re_ranked: List[Dict[str, Any]] = []
    for h in hits:
        text = h.get("content") or h.get("snippet") or ""
        text = str(text).strip()
        if not text:
            h2 = dict(h)
            h2["sim"] = 0.0
            re_ranked.append(h2)
            continue

        try:
            v_raw = eng.encode_text(text)  # type: ignore[attr-defined]
            v = _as_float_list(v_raw)
            sim = _cosine(q_vec, v) if v else 0.0
        except Exception as e:
            logger.debug("[RAG] encode_text(chunk) fehlgeschlagen: %s", e)
            sim = 0.0

        h2 = dict(h)
        h2["sim"] = float(sim)
        re_ranked.append(h2)

    # Sortierung: sim DESC; falls sim fehlt, 0.0
    re_ranked.sort(key=lambda d: float(d.get("sim", 0.0)), reverse=True)

    if top_k is not None:
        try:
            k = int(top_k)
            if k > 0 and len(re_ranked) > k:
                re_ranked = re_ranked[:k]
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.rag_bridge.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )

    logger.info(
        "[RAG] Rerank via Pattern/FusionEngine: question='%s', hits=%d",
        question,
        len(re_ranked),
    )
    return re_ranked
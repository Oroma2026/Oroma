#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/book_import.py
# Projekt:   ORÓMA – Knowledge Ingest (RAGStore/FTS5)
# Version:   v3.8-r2 (Dedupe + Normalisierung + PDF/EPUB optional + CLI)
# Stand:     2025-11-02
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
#  • Dokumente (TXT/MD/*optional*: PDF/EPUB/DOCX) in eine lokale FTS5-DB
#    importieren und in überlappungsfreie Text-Chunks segmentieren.
#  • Deduplizierung über SHA-1 je Chunk (global, corpus-weit).
#  • Normalisierung (Whitespace, Steuerzeichen, Unicode NFKC).
#  • Suche mit Snippets/Highlights (fts5 snippet()).
#
# MERKMALE
# ────────
#  • DB: SQLite/FTS5 (content, doc_id, source), plus chunk_meta(content_hash,…).
#  • Optionaler Parser-Support:
#       - PDF:    PyPDF2 oder pdfminer.six (wenn vorhanden)
#       - EPUB:   ebooklib + bs4 (wenn vorhanden)
#       - DOCX:   python-docx (wenn vorhanden)
#  • Fallback: falls Parser fehlen, wird Datei übersprungen (saubere Logs).
#  • CLI:  book_import.py import <datei|verzeichnis>
#          book_import.py search "<query>" [--k 5]
#          book_import.py list
#
# UMGEBUNGSVARIABLEN
# ──────────────────
#  • OROMA_BASE_DIR         (Default: /opt/ai/oroma)
#  • OROMA_KNOWLEDGE_DB     (Default: $BASE/data/knowledge.db)
#  • OROMA_CHUNK_MAX_CHARS  (Default: 800)
#  • OROMA_MIN_CHARS        (Default: 50)    – kürzere Chunks werden verworfen
#  • OROMA_OVERLAP          (Default: 0)     – Zeichen-Overlap zw. Chunks
#
# INTEGRATION
# ───────────
#  • Wird von ui/knowledge_ui.py genutzt (list_docs/search/import_file).
#  • Bricht API nicht: RAGStore.search() bleibt kompatibel (Tuple-Liste).
#
# ABHÄNGIGKEITEN (optional)
# ─────────────────────────
#  pip install PyPDF2 pdfminer.six ebooklib beautifulsoup4 lxml python-docx
#  → Modul nutzt sie nur, wenn vorhanden; sonst Fallback (Überspringen).
#
# BENCHMARK-HINWEIS
# ─────────────────
#  • FTS5 benötigt SQLite mit FTS5-Extension (Debian/RPi: standardmäßig aktiv).
#  • Große PDFs → pdfminer.six = genauer, aber langsamer als PyPDF2.
# =============================================================================

from __future__ import annotations

import os
import re
import io
import sys
import json
import time
import math
import hashlib
import logging
import sqlite3
from core import db_writer_client as _dbw
import unicodedata
from typing import List, Tuple, Optional, Iterable, Dict
from core.log_guard import log_suppressed

logger = logging.getLogger("oroma.rag")
if not logger.handlers:
    h = logging.StreamHandler()
    f = logging.Formatter("[rag] %(levelname)s: %(message)s")
    h.setFormatter(f)
    logger.addHandler(h)
logger.setLevel(logging.INFO)

BASE = os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma")
DEFAULT_DB = os.environ.get("OROMA_KNOWLEDGE_DB", os.path.join(BASE, "data", "knowledge.db"))

DEFAULT_CHUNK = int(os.environ.get("OROMA_CHUNK_MAX_CHARS", "800"))
DEFAULT_MIN_CHARS = int(os.environ.get("OROMA_MIN_CHARS", "50"))
DEFAULT_OVERLAP = int(os.environ.get("OROMA_OVERLAP", "0"))

SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  title  TEXT,
  created_ts REAL NOT NULL,
  meta_json TEXT
);

-- FTS5: content + Metadaten-Spalten (UNINDEXED)
CREATE VIRTUAL TABLE IF NOT EXISTS chunks
USING fts5(
  content,
  doc_id UNINDEXED,
  source UNINDEXED,
  tokenize = "porter"
);

-- Dedupe: globaler Chunk-Hash (sha1) → UNIQUE
CREATE TABLE IF NOT EXISTS chunk_meta(
  rowid_ref INTEGER,           -- Zeiger auf chunks.rowid (kann NULL sein, wenn noch nicht gesetzt)
  doc_id    INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  PRIMARY KEY(content_hash)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_chunk_meta_doc ON chunk_meta(doc_id);

-- Für schnelle Dokumentliste
CREATE INDEX IF NOT EXISTS idx_documents_ts ON documents(created_ts);
"""

# -------------------------- optionale Parser-Imports --------------------------

try:
    import PyPDF2  # type: ignore
except Exception:
    PyPDF2 = None  # type: ignore

try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text  # type: ignore
except Exception:
    pdfminer_extract_text = None  # type: ignore

try:
    from ebooklib import epub  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    epub = None  # type: ignore
    BeautifulSoup = None  # type: ignore

try:
    import docx  # type: ignore
except Exception:
    docx = None  # type: ignore


# =============================================================================
#                                RAGStore
# =============================================================================



def _dbw_enabled() -> bool:
    try:
        return bool(int(os.getenv("OROMA_DBW_ENABLE", "0")))
    except Exception:
        return False

class RAGStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init()

    # ------------------------------- DB --------------------------------------

    class _ClosingConnection(sqlite3.Connection):
        """sqlite3.Connection, die beim Verlassen eines `with`-Blocks garantiert schließt.

        WICHTIG:
          sqlite3.Connection als Context-Manager committet/rollbackt zwar,
          schließt aber NICHT automatisch. In Langläufern führt das zu vielen
          offenen FD/Connections → später `database is locked`.
        """

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    self.commit()
                else:
                    self.rollback()
            finally:
                try:
                    self.close()
                except Exception:
                    pass
            return False

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, factory=self._ClosingConnection)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self._connect() as con:
            con.executescript(SCHEMA)
            con.commit()

    # --------------------------- Normalisierung ------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        # Unicode-NFKC, Steuerzeichen raus, Whitespace kondensieren
        t = unicodedata.normalize("NFKC", text)
        t = t.replace("\x00", " ")
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\s*\n\s*", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    @staticmethod
    def _strip_markdown(text: str) -> str:
        # sehr simple Heuristik; keine externe Lib nötig
        t = re.sub(r"`{1,3}.*?`{1,3}", " ", text, flags=re.S)        # Codeblöcke/Inline
        t = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", t)                 # Bilder
        t = re.sub(r"\[[^\]]*\]\([^\)]*\)", " ", t)                  # Links
        t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)                 # Überschriften
        t = re.sub(r"[*_~]{1,3}", "", t)                             # Emphasis
        t = re.sub(r">+\s?", "", t, flags=re.M)                      # Zitate
        t = re.sub(r"\|.*\|\n", "", t)                               # Tabellen-Zeilen grob
        return t

    # ----------------------------- Chunker -----------------------------------

    def split_into_chunks(self, text: str, max_chars: int = DEFAULT_CHUNK,
                          min_chars: int = DEFAULT_MIN_CHARS,
                          overlap: int = DEFAULT_OVERLAP) -> List[str]:
        """
        Zerteilt Text in (ggf. überlappende) Chunks.
        - min_chars: zu kurze Chunks werden verworfen
        - overlap:   fester Zeichen-Overlap (0 = aus)
        """
        text = self._normalize(text)
        if not text:
            return []

        out: List[str] = []
        if overlap <= 0:
            for i in range(0, len(text), max_chars):
                chunk = text[i:i + max_chars]
                if len(chunk) >= min_chars:
                    out.append(chunk)
            return out

        # mit Overlap
        step = max(1, max_chars - overlap)
        i = 0
        while i < len(text):
            chunk = text[i:i + max_chars]
            if len(chunk) >= min_chars:
                out.append(chunk)
            i += step
        return out

    # -------------------------- Dedupe / Insert ------------------------------

    @staticmethod
    def _sha1(s: str) -> str:
        return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()

    def _have_hash(self, cur: sqlite3.Cursor, content_hash: str) -> bool:
        cur.execute("SELECT 1 FROM chunk_meta WHERE content_hash=? LIMIT 1", (content_hash,))
        return cur.fetchone() is not None

    def add_document(self, source: str, title: str, chunks: List[str],
                     meta_json: Optional[str] = "{}") -> int:
        """
        Fügt Dokument + (deduplizierte) Chunks ein.
        Gibt doc_id zurück.
        """
        if not isinstance(meta_json, str):
            meta_json = json.dumps(meta_json or {}, ensure_ascii=False)

        with self._connect() as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO documents(source, title, created_ts, meta_json) VALUES(?,?,?,?)",
                (source, title, time.time(), meta_json),
            )
            doc_id = cur.lastrowid

            added = 0
            for ch in chunks:
                ch_norm = self._normalize(ch)
                if len(ch_norm) < DEFAULT_MIN_CHARS:
                    continue
                h = self._sha1(ch_norm)
                if self._have_hash(cur, h):
                    continue  # globales Dedupe
                # zuerst in FTS einfügen …
                cur.execute(
                    "INSERT INTO chunks(rowid, content, doc_id, source) VALUES(NULL, ?, ?, ?)",
                    (ch_norm, doc_id, source),
                )
                rowid_ref = cur.lastrowid
                # … dann Hash registrieren (UNIQUE)
                cur.execute(
                    "INSERT OR IGNORE INTO chunk_meta(rowid_ref, doc_id, content_hash) VALUES(?,?,?)",
                    (rowid_ref, doc_id, h),
                )
                added += 1

            con.commit()

        logger.info(f"[RAG] Dokument hinzugefügt: id={doc_id}, source={source}, title={title}, chunks_ins={added}")
        return doc_id

    # ------------------------------- Query -----------------------------------

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, str, str]]:
        """
        Kompatibler Rückgabetyp (Tuple) für bestehende Aufrufer:
          [(rowid, source, content), ...]
        """
        with self._connect() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT rowid, source, content FROM chunks WHERE chunks MATCH ? LIMIT ?",
                (query, int(top_k)),
            )
            rows = cur.fetchall()
            logger.info(f"[RAG] Suche: '{query}' → {len(rows)} Treffer")
            return [(int(r["rowid"]), r["source"], r["content"]) for r in rows]

    def search_dict(self, query: str, top_k: int = 5) -> List[Dict[str, object]]:
        """
        Komfort-Rückgabe mit Snippet.
        """
        with self._connect() as con:
            con.create_function("hl", 1, lambda s: s)  # placeholder falls benötigt
            cur = con.cursor()
            # snippet(chunks, column, start_mark, end_mark, ellipsis, num_tokens)
            cur.execute(
                "SELECT rowid, doc_id, source, "
                "snippet(chunks, 0, '[', ']', ' … ', 12) AS snip "
                "FROM chunks WHERE chunks MATCH ? LIMIT ?",
                (query, int(top_k)),
            )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                out.append({
                    "rowid": int(r["rowid"]),
                    "doc_id": int(r["doc_id"]),
                    "source": r["source"],
                    "snippet": r["snip"],
                })
            return out

    def list_docs(self) -> List[dict]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT id, source, title, created_ts FROM documents ORDER BY created_ts DESC"
            )
            rows = cur.fetchall()
            return [
                {"id": int(r["id"]), "source": r["source"], "title": r["title"], "ts": int(r["created_ts"])}
                for r in rows
            ]

    # ------------------------------ Helpers ----------------------------------

    @staticmethod
    def _read_text_txt_md(path: str) -> str:
        encs = ("utf-8", "utf-16", "latin-1")
        for enc in encs:
            try:
                with open(path, "r", encoding=enc, errors="ignore") as f:
                    txt = f.read()
                # MD → grob säubern
                if path.lower().endswith(".md"):
                    txt = RAGStore._strip_markdown(txt)
                return txt
            except Exception:
                continue
        raise RuntimeError("Konnte Textdatei nicht lesen (Encoding)")

    @staticmethod
    def _read_text_pdf(path: str) -> str:
        if PyPDF2:
            try:
                with open(path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    buf = []
                    for page in reader.pages:
                        buf.append(page.extract_text() or "")
                    return "\n".join(buf)
            except Exception as e:
                logger.warning(f"[RAG] PyPDF2 fehlgeschlagen ({path}): {e}")
        if pdfminer_extract_text:
            try:
                return pdfminer_extract_text(path) or ""
            except Exception as e:
                logger.warning(f"[RAG] pdfminer fehlgeschlagen ({path}): {e}")
        raise RuntimeError("Kein PDF-Parser verfügbar (PyPDF2/pdfminer fehlen)")

    @staticmethod
    def _read_text_epub(path: str) -> str:
        if not (epub and BeautifulSoup):
            raise RuntimeError("Kein EPUB-Parser verfügbar (ebooklib/bs4 fehlen)")
        try:
            book = epub.read_epub(path)
            parts: List[str] = []
            for item in book.get_items():
                if item.get_type() == 9:  # DOCUMENT
                    soup = BeautifulSoup(item.get_body_content(), "lxml")
                    parts.append(soup.get_text(" ", strip=True))
            return "\n".join(parts)
        except Exception as e:
            raise RuntimeError(f"EPUB-Parsing fehlgeschlagen: {e}")

    @staticmethod
    def _read_text_docx(path: str) -> str:
        if not docx:
            raise RuntimeError("Kein DOCX-Parser verfügbar (python-docx fehlt)")
        try:
            d = docx.Document(path)
            return "\n".join([p.text for p in d.paragraphs])
        except Exception as e:
            raise RuntimeError(f"DOCX-Parsing fehlgeschlagen: {e}")

    # ------------------------------- High-Level -------------------------------

    def import_file(self, file_path: str) -> int:
        """
        Importiert eine Datei (txt/md/pdf/epub/docx) in die Wissensbasis.
        Gibt doc_id zurück.
        """
        path = os.path.abspath(file_path)
        ext = os.path.splitext(path)[1].lower()
        title = os.path.basename(path)

        try:
            if ext in (".txt", ".md"):
                raw = self._read_text_txt_md(path)
            elif ext == ".pdf":
                raw = self._read_text_pdf(path)
            elif ext == ".epub":
                raw = self._read_text_epub(path)
            elif ext == ".docx":
                raw = self._read_text_docx(path)
            else:
                raise RuntimeError(f"Nicht unterstützte Dateiendung: {ext}")
        except Exception as e:
            logger.error(f"[RAG] Import übersprungen ({title}): {e}")
            raise

        chunks = self.split_into_chunks(raw, max_chars=DEFAULT_CHUNK,
                                        min_chars=DEFAULT_MIN_CHARS,
                                        overlap=DEFAULT_OVERLAP)
        meta = {
            "ext": ext,
            "size": os.path.getsize(path) if os.path.exists(path) else None,
            "mtime": os.path.getmtime(path) if os.path.exists(path) else None,
            "chunk_max": DEFAULT_CHUNK,
            "overlap": DEFAULT_OVERLAP,
            "min_chars": DEFAULT_MIN_CHARS,
        }
        return self.add_document(source=path, title=title, chunks=chunks, meta_json=json.dumps(meta, ensure_ascii=False))

    def import_dir(self, directory: str, patterns: Iterable[str] = (".txt", ".md", ".pdf", ".epub", ".docx")) -> List[int]:
        """
        Durchläuft ein Verzeichnis rekursiv und importiert unterstützte Dateien.
        Gibt Liste der doc_ids zurück (nur erfolgreiche Importe).
        """
        directory = os.path.abspath(directory)
        doc_ids: List[int] = []
        for root, _dirs, files in os.walk(directory):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext in patterns:
                    path = os.path.join(root, fn)
                    try:
                        doc_ids.append(self.import_file(path))
                    except Exception as e:
                        # Fehler wurden geloggt; weitermachen
                        log_suppressed(
                            logging.getLogger(__name__),
                            key="core.book_import.pass.1",
                            exc=e,
                            msg="Suppressed exception (was: pass)",
                        )
        return doc_ids


# -----------------------------------------------------------------------------
# Kompatible High-Level Funktion (API-Stabilität)
# -----------------------------------------------------------------------------

def import_file(db_path: str, file_path: str) -> int:
    """
    *Kompatibel zur alten Signatur.*
    Importiert eine Datei (txt/md/*optional*: pdf/epub/docx) in die Wissensbasis.
    """
    rag = RAGStore(db_path)
    return rag.import_file(file_path)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="ORÓMA Knowledge Import/Search (FTS5)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import", help="Datei oder Verzeichnis importieren")
    p_imp.add_argument("path", help="Datei oder Verzeichnis")
    p_imp.add_argument("--db", default=DEFAULT_DB, help=f"Pfad zur DB (Default: {DEFAULT_DB})")

    p_ls = sub.add_parser("list", help="Dokumente auflisten")
    p_ls.add_argument("--db", default=DEFAULT_DB)

    p_q = sub.add_parser("search", help="Volltextsuche")
    p_q.add_argument("query", help='FTS5-Query, z.B. "neuron* AND biolog*"')
    p_q.add_argument("--k", type=int, default=5, help="Top-K Treffer")
    p_q.add_argument("--db", default=DEFAULT_DB)

    args = p.parse_args()

    rag = RAGStore(args.db)

    if args.cmd == "import":
        path = os.path.abspath(args.path)
        if os.path.isdir(path):
            ids = rag.import_dir(path)
            print(json.dumps({"ok": True, "imported": ids}, ensure_ascii=False))
        else:
            try:
                doc_id = rag.import_file(path)
                print(json.dumps({"ok": True, "doc_id": doc_id}, ensure_ascii=False))
            except Exception as e:
                print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
                sys.exit(1)

    elif args.cmd == "list":
        print(json.dumps({"ok": True, "docs": rag.list_docs()}, ensure_ascii=False))

    elif args.cmd == "search":
        hits = rag.search_dict(args.query, top_k=args.k)
        print(json.dumps({"ok": True, "hits": hits}, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/bench_rag.py
# Projekt: ORÓMA
# Modul:   RAG-Benchmark (hit@k, nDCG@10, serve_ms)
# Version: v3.8-r1-snap-bench
# Stand:   2025-11-23
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Bewertet die Qualität und Latenz der lokalen RAG-Suche (core.rag_bridge.RAGStore)
#   mit optionalem SnapPattern-Re-Ranking.
#
#   Metriken:
#     • hit@k      – Anteil der Queries mit mind. einem relevanten Treffer in Top k
#     • nDCG@10    – Normalized Discounted Cumulative Gain für Top 10 (binäre Relevanz)
#     • serve_ms   – mittlere Antwortzeit des RAG-Stacks in Millisekunden
#
#   Ergebnisse:
#     • werden auf stdout ausgegeben
#     • werden zusätzlich in die metrics-Tabelle von core.sql_manager geschrieben:
#         - "rag.hit_at_k"   → hit@k (0..1)
#         - "rag.ndcg_10"    → nDCG@10 (0..1)
#         - "rag.serve_ms"   → mittlere Antwortzeit in ms
#         - "rag.samples"    → Anzahl der ausgewerteten Queries
#         - "rag.rerank"     → 0 oder 1 (ob Rerank aktiviert war)
#
# Eingabeformat (QA-Datei, JSON)
# ──────────────────────────────
#   Erwartet wird eine JSON-Datei mit einer Liste von Objekten:
#
#     [
#       {
#         "question": "Wie heißt die Hauptstadt von Frankreich?",
#         "answers":  ["Paris"]
#       },
#       {
#         "q": "Wer schrieb den Faust?",
#         "a": ["Goethe", "Johann Wolfgang von Goethe"]
#       }
#     ]
#
#   Unterstützte Schlüssel:
#     - "question" oder "q"    → Query-Text (String, Pflicht)
#     - "answers" oder "a"     → Erwartete Antwort(en) als
#                                String oder Liste von Strings
#
#   Relevanzprüfung:
#     • Eine Passage ist relevant, wenn eine erwartete Antwort (lowercased)
#       als Substring im Passage-"content" vorkommt.
#
# Aufrufbeispiele
# ───────────────
#   # Ohne Rerank:
#   python -m core.tools.bench_rag --qa /opt/ai/oroma/data/qa.json
#
#   # Mit Rerank (falls rag.rerank_by_pattern verfügbar ist):
#   python -m core.tools.bench_rag --qa /opt/ai/oroma/data/qa.json --rerank --k 10
#
# Konfiguration
# ─────────────
#   • OROMA_KNOWLEDGE_DB: Pfad zur RAG-DB (Default: /opt/ai/oroma/data/knowledge.db)
#   • OROMA_BASE/OROMA_BASE_DIR: Basisverzeichnis für core.sql_manager
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

import os
import json
import time
import math
import argparse
from typing import Any, Dict, List, Tuple

from core.rag_bridge import RAGStore
from core import sql_manager
from core.sql_manager import ensure_schema, insert_metric
import logging
from core.log_guard import log_suppressed

# =============================================================================
# Hilfsfunktionen: QA-Handling
# =============================================================================

def _load_qa(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("QA-Datei muss eine Liste von Objekten enthalten.")
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        q = item.get("question") or item.get("q")
        a = item.get("answers", item.get("a"))
        if not q:
            continue
        # Antworten in Liste überführen
        if isinstance(a, str):
            answers = [a]
        elif isinstance(a, list):
            answers = [str(x) for x in a if isinstance(x, (str, int, float))]
        elif a is None:
            answers = []
        else:
            answers = [str(a)]
        out.append({"question": str(q), "answers": answers})
    return out


def _is_relevant(passage: str, answers: List[str]) -> bool:
    """Binary Relevanz: irgendeine erwartete Antwort kommt als Substring vor."""
    if not answers:
        return False
    txt = (passage or "").lower()
    for a in answers:
        if not a:
            continue
        if str(a).lower() in txt:
            return True
    return False


def _dcg_at_k(rels: List[int], k: int = 10) -> float:
    """Discounted Cumulative Gain für binäre Relevanzen."""
    dcg = 0.0
    for i, rel in enumerate(rels[:k], start=1):
        if rel <= 0:
            continue
        dcg += float(rel) / math.log2(i + 1.0)
    return dcg


def _ndcg_at_k(rels: List[int], k: int = 10) -> float:
    """
    Normalized DCG (nDCG) für binäre Relevanz.

    WICHTIG (Fix):
      Die frühere Implementierung setzte IDCG pauschal auf 1.0, sobald überhaupt
      irgendein relevanter Treffer existierte. Das ist nur dann korrekt, wenn
      exakt EIN relevanter Treffer im Top-k vorkommt.

      Bei mehreren relevanten Treffern kann DCG > 1.0 werden – und damit wäre
      „nDCG“ > 1.0 möglich, was technisch falsch ist.

    Korrekt:
      IDCG ist die DCG der ideal sortierten Relevanzliste (rels absteigend).
    """
    if not rels:
        return 0.0
    dcg = _dcg_at_k(rels, k)
    ideal = sorted(rels, reverse=True)[:k]
    idcg = _dcg_at_k(ideal, k)
    if idcg <= 0.0:
        return 0.0
    return dcg / idcg

# =============================================================================
# Benchmark-Logik
# =============================================================================

def run_benchmark(
    qa_path: str,
    db_path: str,
    top_k: int = 10,
    use_rerank: bool = False,
) -> Tuple[float, float, float, int]:
    """
    Führt den RAG-Benchmark aus und gibt zurück:
        (hit_at_k, ndcg_10, avg_ms, n_samples)
    """
    qa = _load_qa(qa_path)
    if not qa:
        raise ValueError("Keine gültigen QA-Einträge gefunden.")

    store = RAGStore(db_path)

    has_rerank = use_rerank and hasattr(store, "rerank_by_pattern")

    hit_values: List[float] = []
    ndcg_values: List[float] = []
    latencies_ms: List[float] = []

    for item in qa:
        q = item["question"]
        answers = item["answers"]

        t0 = time.time()
        hits = store.search(q, top_k=top_k)
        if has_rerank:
            try:
                hits = store.rerank_by_pattern(q, hits)  # type: ignore[attr-defined]
            except Exception as e:
                # Rerank ist optional – Fehler sollen den Benchmark nicht abbrechen.
                log_suppressed('tools/bench_rag.py:198', exc=e, level=logging.WARNING)
                pass
        t1 = time.time()
        latencies_ms.append((t1 - t0) * 1000.0)

        # Relevanzliste für nDCG@10
        rels: List[int] = []
        found_relevant = False
        for h in hits[:top_k]:
            content = h.get("content") or ""
            is_rel = _is_relevant(content, answers)
            rels.append(1 if is_rel else 0)
            if is_rel:
                found_relevant = True

        hit_values.append(1.0 if found_relevant else 0.0)
        ndcg_values.append(_ndcg_at_k(rels, k=10))

    # Mittelwerte
    n = len(hit_values)
    hit_at_k = sum(hit_values) / float(n) if n else 0.0
    ndcg_10 = sum(ndcg_values) / float(n) if n else 0.0
    avg_ms = sum(latencies_ms) / float(n) if n else 0.0

    return hit_at_k, ndcg_10, avg_ms, n

# =============================================================================
# Metrics-Logging
# =============================================================================

def log_metrics(hit_at_k: float, ndcg_10: float, avg_ms: float, n_samples: int, use_rerank: bool) -> None:
    """
    Schreibt Benchmark-Metriken in die metrics-Tabelle.
    """
    ts = int(time.time())
    insert_metric("rag.hit_at_k", float(hit_at_k), ts)
    insert_metric("rag.ndcg_10", float(ndcg_10), ts)
    insert_metric("rag.serve_ms", float(avg_ms), ts)
    insert_metric("rag.samples", float(n_samples), ts)
    insert_metric("rag.rerank", 1.0 if use_rerank else 0.0, ts)

# =============================================================================
# CLI
# =============================================================================

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RAG-Benchmark für ORÓMA (hit@k, nDCG@10, serve_ms).",
    )
    parser.add_argument(
        "--qa",
        required=True,
        help="Pfad zur QA-JSON-Datei (Liste von {question/q, answers/a}).",
    )
    parser.add_argument(
        "--db",
        help="Pfad zur RAG-DB (Default: OROMA_KNOWLEDGE_DB oder /opt/ai/oroma/data/knowledge.db).",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=10,
        help="Top-k für hit@k und nDCG@10 (Default: 10, Range: 1..50).",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Re-Ranking via SnapPattern aktivieren (falls RAGStore.rerank_by_pattern vorhanden).",
    )

    args = parser.parse_args(argv)

    qa_path = os.path.abspath(args.qa)
    if not os.path.isfile(qa_path):
        raise SystemExit(f"QA-Datei nicht gefunden: {qa_path}")

    db_path = args.db or os.environ.get("OROMA_KNOWLEDGE_DB", "/opt/ai/oroma/data/knowledge.db")
    db_path = os.path.abspath(db_path)

    top_k = max(1, min(50, int(args.k)))
    use_rerank = bool(args.rerank)

    # Schema sicherstellen (metrics-Tabelle etc.)
    ensure_schema()

    print("[bench_rag] Starte Benchmark …")
    print(f"  QA-Datei : {qa_path}")
    print(f"  DB-Pfad  : {db_path}")
    print(f"  top_k    : {top_k}")
    print(f"  rerank   : {use_rerank}")

    hit_at_k, ndcg_10, avg_ms, n_samples = run_benchmark(
        qa_path=qa_path,
        db_path=db_path,
        top_k=top_k,
        use_rerank=use_rerank,
    )

    print("\n[bench_rag] Ergebnisse")
    print(f"  SAMPLES   : {n_samples}")
    print(f"  hit@{top_k:<2} : {hit_at_k:.4f}")
    print(f"  nDCG@10   : {ndcg_10:.4f}")
    print(f"  serve_ms  : {avg_ms:.2f} ms")

    log_metrics(hit_at_k, ndcg_10, avg_ms, n_samples, use_rerank)
    print("\n[bench_rag] Metriken in core.sql_manager.metrics geloggt ✅")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
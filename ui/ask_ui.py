#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/ask_ui.py
# Projekt: ORÓMA
# Version: v3.8-snap-rerank
# Stand:   2025-11-23
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   UI + API für RAG-Anfragen (lokale Wissensbasis).
#
#   Routen:
#     - /ask         → HTML-Formular (ask.html) – bewusst ohne Token (UI bleibt nutzbar)
#     - /ask/api     → JSON-API (token-geschützt via require_ui_token)
#
#   Funktionen:
#     • RAG-Suche via core.rag_bridge.RAGStore (FTS5)
#     • Antwort-Synthese via synthesize_answer()
#     • OPTIONAL: Re-Ranking der Treffer via SnapPattern/Snaps
#         - Flag: "rerank" (bool) in HTML-Form / JSON-API
#         - Wenn RAGStore.rerank_by_pattern vorhanden ist, wird es genutzt.
#
# Sicherheit
# ──────────
#   - /ask/api erfordert gültigen UI-Token (Header X-OROMA-TOKEN, Cookie etc.).
#   - Frage wird begrenzt (max 512 Zeichen), top_k validiert (1..20).
#
# Abhängigkeiten
# ──────────────
#   - core.rag_bridge: RAGStore (FTS5) + synthesize_answer (+ optional rerank_by_pattern)
#   - ui.require_ui_token: UI-Token-Decorator (No-Op-Fallback, falls nicht vorhanden)
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

import os
import logging
from flask import Blueprint, render_template, request, jsonify

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("oroma.ui.ask")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[ask_ui] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# Token-Decorator (best effort)
try:
    from ui import require_ui_token  # aus ui/__init__.py
except Exception:  # Fallback: No-Op
    def require_ui_token(fn):  # type: ignore
        return fn

from core.rag_bridge import RAGStore, synthesize_answer

bp = Blueprint("ask", __name__, url_prefix="/ask")

# Knowledge-DB
DB_PATH = os.environ.get("OROMA_KNOWLEDGE_DB", "/opt/ai/oroma/data/knowledge.db")
rag = RAGStore(DB_PATH)

# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _parse_top_k(v) -> int:
    try:
        k = int(str(v or "5"))
    except Exception:
        k = 5
    return max(1, min(20, k))


def _sanitize_question(q: str) -> str:
    q = (q or "").strip()
    return q[:512]


def _parse_rerank_flag(v) -> bool:
    """
    Interpretiert UI/JSON-Flag für Rerank:
      - True für: True, "1", "true", "True", 1
      - Sonst False.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def _apply_rerank_if_available(question: str, hits, rerank_flag: bool):
    """
    Wendet optional rag.rerank_by_pattern(...) an, falls:
      - rerank_flag True ist und
      - RAGStore-Instanz rag dieses Attribut besitzt.

    Fehler führen nur zu einem Warning-Log, die Original-Trefferliste bleibt erhalten.
    """
    if not rerank_flag:
        return hits
    if not hasattr(rag, "rerank_by_pattern"):
        logger.info("rerank_flag=True, aber RAGStore hat kein rerank_by_pattern() – übersprungen.")
        return hits
    try:
        # Erwartete Signatur in rag_bridge v3.2+:
        #   rerank_by_pattern(question: str, hits: List[dict]) -> List[dict]
        new_hits = rag.rerank_by_pattern(question, hits)  # type: ignore[attr-defined]
        logger.info("Rerank via SnapPattern angewendet (Treffer: %d)", len(new_hits or []))
        return new_hits or hits
    except Exception as ex:  # safety: nie hart crashen
        logger.warning("Rerank fehlgeschlagen: %s", ex)
        return hits

# =============================================================================
# UI
# =============================================================================

@bp.route("/", methods=["GET", "POST"])
def page():
    """HTML-Seite: Frage stellen (kein Token zwingend erforderlich)."""
    question = ""
    answer = None
    passages = []
    top_k = 5
    rerank_flag = False

    if request.method == "POST":
        question = (request.form.get("question") or "").strip()
        top_k = _parse_top_k(request.form.get("top_k"))
        rerank_flag = _parse_rerank_flag(request.form.get("rerank"))

        if question:
            hits = rag.search(question, top_k=top_k)
            hits = _apply_rerank_if_available(question, hits, rerank_flag)
            passages = hits
            answer = synthesize_answer(question, [h["content"] for h in hits])

    return render_template(
        "ask.html",
        question=question,
        answer=answer,
        passages=passages,
        top_k=top_k,
        rerank=rerank_flag,
    )

# =============================================================================
# API
# =============================================================================

@bp.route("/api", methods=["POST"])
@require_ui_token
def api():
    """JSON-API: {question, top_k, rerank?} → {ok, answer, passages[], rerank}"""
    try:
        data = request.get_json(force=True) or {}
        question = _sanitize_question(data.get("question", ""))
        if not question:
            return jsonify({"ok": False, "error": "Keine Frage übergeben"}), 400

        top_k = _parse_top_k(data.get("top_k"))
        rerank_flag = _parse_rerank_flag(data.get("rerank"))

        hits = rag.search(question, top_k=top_k)
        hits = _apply_rerank_if_available(question, hits, rerank_flag)

        passages = [
            {
                "content": h["content"],
                "source": h["source"],
                "score": float(h.get("score", 0.0)),
                "snippet": h.get("snippet", ""),
            }
            for h in hits
        ]
        answer = synthesize_answer(question, [h["content"] for h in hits])

        return jsonify({
            "ok": True,
            "question": question,
            "answer": answer,
            "passages": passages,
            "rerank": bool(rerank_flag),
        })
    except Exception as e:
        logger.exception("Fehler in /ask/api: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
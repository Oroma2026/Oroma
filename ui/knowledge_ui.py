#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/knowledge_ui.py
# Projekt: ORÓMA
# Version: v3.5
# Stand:   2025-09-21
#
# Zweck:
#   UI-Route zum Importieren und Durchsuchen von Text-/Buchdateien
#   in die lokale RAG-Wissensbasis:
#     - /knowledge        → Upload-Formular + Liste importierter Dokumente
#     - /knowledge/search → Suchfunktion über RAGStore
#
# Hinweise:
#   - Nutzt core.book_import (RAGStore + import_file)
#   - Datenbankpfade und Upload-Verzeichnis über ENV konfigurierbar
#   - Integriert ins Dashboard-Layout (base.html)
# =============================================================================

import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from core.book_import import import_file, RAGStore

bp = Blueprint("knowledge", __name__, url_prefix="/knowledge")

# Basis aus ENV laden
BASE = os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma")
DB_PATH = os.environ.get("OROMA_KNOWLEDGE_DB", os.path.join(BASE, "data", "knowledge.db"))
UPLOAD_DIR = os.environ.get("OROMA_UPLOAD_DIR", os.path.join(BASE, "uploads"))

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _list_docs():
    """Hole alle importierten Dokumente aus der RAG-Datenbank"""
    try:
        rag = RAGStore(DB_PATH)
        return rag.list_docs()
    except Exception as e:
        print(f"[knowledge_ui] Fehler bei list_docs: {e}")
        return []


@bp.route("/", methods=["GET", "POST"])
def knowledge():
    if request.method == "POST":
        if "file" not in request.files:
            flash("Keine Datei hochgeladen!", "error")
            return redirect(url_for("knowledge.knowledge"))

        f = request.files["file"]
        if f.filename == "":
            flash("Dateiname fehlt!", "error")
            return redirect(url_for("knowledge.knowledge"))

        save_path = os.path.join(UPLOAD_DIR, f.filename)
        f.save(save_path)

        try:
            import_file(DB_PATH, save_path)
            flash(f"Datei {f.filename} erfolgreich importiert.", "success")
        except Exception as e:
            flash(f"Fehler beim Import: {e}", "error")

        return redirect(url_for("knowledge.knowledge"))

    docs = _list_docs()
    return render_template("knowledge.html", docs=docs)


@bp.route("/search", methods=["GET", "POST"])
def search():
    """Einfache Suchroute über die RAGStore-Datenbank"""
    results = []
    query = ""
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            rag = RAGStore(DB_PATH)
            rows = rag.search(query, top_k=10)
            results = [{"id": r[0], "source": r[1], "content": r[2]} for r in rows]
    return render_template("knowledge_search.html", query=query, results=results)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from core.rag_bridge import RAGStore

DB_PATH = os.environ.get("OROMA_KNOWLEDGE_DB", "/opt/ai/oroma/data/knowledge.db")

def main():
    store = RAGStore(DB_PATH)

    docs = [
        (
            "wiki:frankreich",
            "Frankreich – Hauptstadt",
            """
            Frankreich ist ein Land in Europa. Die Hauptstadt von Frankreich heißt Paris.
            Paris ist zugleich die größte Stadt des Landes.
            """
        ),
        (
            "wiki:faust",
            "Faust – Goethe",
            """
            Faust ist ein berühmtes Drama von Johann Wolfgang von Goethe.
            Goethe gilt als einer der wichtigsten deutschsprachigen Dichter.
            """
        ),
        (
            "wiki:eiffelturm",
            "Eiffelturm",
            """
            Der Eiffelturm ist ein bekannter Turm in Paris.
            Er steht im 7. Arrondissement der Stadt Paris in Frankreich.
            """
        ),
    ]

    for src, title, text in docs:
        chunks = store.split_into_chunks(text, max_chars=800)
        doc_id = store.add_document(src, title, chunks)
        print(f"Dokument importiert: id={doc_id}, source={src}, title={title}, chunks={len(chunks)}")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/mark_compressed.py
# Projekt: ORÓMA
# Version: v3.5patch2.2
# Stand:   2025-09-26
#
# Zweck:
#   - Hilfsskript zur Simulation von Forgetting/Kompression
#   - Markiert SnapChains mit niedrigem Gewicht als "compressed"
#   - Unterstützt Parameter:
#       • --db <pfad>         → Pfad zur SQLite-DB (Default: data/oroma.db)
#       • --threshold <wert>  → Gewicht-Schwelle (Default: 0.35)
#       • --limit <anzahl>    → Maximal zu markierende Einträge (Default: 100)
#
# Nutzung:
#   python3 tools/mark_compressed.py --threshold 0.4 --limit 50
#
# Ergebnis:
#   - Aktualisiert status von "active" → "compressed"
#   - Ausgabe zeigt Anzahl betroffener Snaps
# =============================================================================

import sqlite3
import argparse
import os

def mark_compressed(db_path: str, threshold: float, limit: int):
    if not os.path.exists(db_path):
        print(f"[FEHLER] DB nicht gefunden: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Selektiere Kandidaten
    cur.execute("""
        SELECT id, weight
          FROM snapchains
         WHERE status='active' AND weight < ?
         ORDER BY weight ASC
         LIMIT ?;
    """, (threshold, limit))
    rows = cur.fetchall()

    if not rows:
        print(f"[INFO] Keine Snaps unterhalb Threshold={threshold}.")
        conn.close()
        return

    ids = [r["id"] for r in rows]
    cur.execute(f"""
        UPDATE snapchains
           SET status='compressed'
         WHERE id IN ({','.join('?' for _ in ids)});
    """, ids)

    conn.commit()
    print(f"[OK] {len(ids)} Snaps auf 'compressed' gesetzt (Threshold={threshold}).")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Markiere schwache Snaps als compressed.")
    parser.add_argument("--db", default="data/oroma.db", help="Pfad zur SQLite-DB")
    parser.add_argument("--threshold", type=float, default=0.35, help="Gewicht-Schwelle")
    parser.add_argument("--limit", type=int, default=100, help="Max. Anzahl zu markierende Snaps")
    args = parser.parse_args()

    mark_compressed(args.db, args.threshold, args.limit)
#cd /opt/ai/oroma/
#python3 tools/mark_compressed.py --threshold 0.4 --limit 50
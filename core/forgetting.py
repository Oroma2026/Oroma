#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/forgetting.py
# Projekt: ORÓMA
# Version: v3.5patch2.2
# Stand:   2025-09-26
#
# Zweck:
#   - Biologisch inspiriertes Vergessen:
#       1. Verblassen (weight ↓ mit Zeit)
#       2. Komprimieren (Details → Hash/Feature)
#       3. MetaSnap-Bildung (viele ähnliche Snaps bündeln)
#
# Aufruf:
#   - Wird vom DreamWorker jede Nacht getriggert
#   - API: decay_snaps(), compress_snaps(), merge_to_meta()
#
# ENV-Parameter:
#   OROMA_FORGET_DECAY_RATE   (default=0.99)
#   OROMA_FORGET_THRESHOLD    (default=0.2)
#   OROMA_FORGET_META_BATCH   (default=50)
# =============================================================================

from __future__ import annotations
import os
import time
import hashlib
from typing import List, Dict, Any, Optional

from core import sql_manager

# ----------------------------- ENV Defaults ----------------------------------

DECAY_RATE = float(os.environ.get("OROMA_FORGET_DECAY_RATE", "0.99"))
THRESHOLD = float(os.environ.get("OROMA_FORGET_THRESHOLD", "0.2"))
META_BATCH = int(os.environ.get("OROMA_FORGET_META_BATCH", "50"))

# ----------------------------- Helpers ---------------------------------------

def _hash_blob(blob: bytes) -> str:
    return hashlib.sha1(blob).hexdigest()[:16]

# ----------------------------- Verblassen ------------------------------------

def decay_snaps() -> int:
    """
    Senkt das Gewicht (weight) aller SnapChains leicht ab.
    Returns: Anzahl betroffener Zeilen.
    """
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, weight FROM snapchains")
    rows = cur.fetchall() or []
    count = 0
    for r in rows:
        new_w = float(r["weight"]) * DECAY_RATE
        conn.execute("UPDATE snapchains SET weight=? WHERE id=?", (new_w, r["id"]))
        count += 1
    conn.commit()
    return count

# ----------------------------- Komprimieren ----------------------------------

def compress_snaps() -> int:
    """
    Komprimiert SnapChains mit sehr geringem Gewicht (Vergessen/Decay), ohne Datenverlust:

    Problem (alt):
        Die alte Variante hat den BLOB direkt durch einen 16-Byte Hash ersetzt.
        Dadurch gehen Sequenz-/Feature-Informationen verloren (z. B. Games → Policy-Training = 0 Schritte).

    Lösung (neu):
        1) Der Original-Blob wird zuerst als JSON-Datei in das SnapChain-Export-Verzeichnis ausgelagert.
           - Dateiname: <source_id>.json
           - Falls source_id fehlt, wird automatisch `db_<id>` vergeben.
        2) Im DB-BLOB wird nur noch der 16-Byte Hash gespeichert.
        3) status wird auf 'compressed' gesetzt und notes werden ergänzt.

    Steuerung per ENV:
        - OROMA_FORGET_COMPRESS_MAX   (Default: 500)   Max. SnapChains pro Run
        - OROMA_SNAPCHAIN_DIR         (Fallback, falls core.snapchain.get_snapchain_dir() nicht verfügbar ist)

    Rückgabe:
        Anzahl erfolgreich komprimierter SnapChains.
    """
    try:
        max_rows = int(os.environ.get("OROMA_FORGET_COMPRESS_MAX", "500") or "500")
    except Exception:
        max_rows = 500

    conn = sql_manager.get_conn()
    cur = conn.cursor()

    # Nur aktive Chains komprimieren (sonst wird bereits komprimiertes Material unnötig angefasst).
    # Wir holen origin/source_id mit, um später zuverlässig auf File-Storage zurückfallen zu können.
    cur.execute(
        """
        SELECT id, origin, source_id, blob, notes, status, weight
        FROM snapchains
        WHERE status='active'
          AND weight < ?
        LIMIT ?
        """,
        (THRESHOLD, max_rows),
    )
    rows = cur.fetchall() or []
    if not rows:
        return 0

    # Zielverzeichnis (gleiches Verzeichnis, das auch save_chain() nutzt)
    try:
        from core.snapchain import get_snapchain_dir  # type: ignore
        out_dir = get_snapchain_dir()
    except Exception:
        out_dir = os.environ.get("OROMA_SNAPCHAIN_DIR", "/opt/ai/oroma/data/snapchains")

    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        # Wenn das Verzeichnis nicht angelegt werden kann, lieber NICHT komprimieren → sonst Datenverlust.
        return 0

    def _is_hex16_blob(b: bytes) -> bool:
        if len(b) != 16:
            return False
        try:
            s = b.decode("ascii", errors="strict").lower()
        except Exception:
            return False
        return all(ch in "0123456789abcdef" for ch in s)

    n = 0
    now_ts = int(time.time())

    for r in rows:
        # sqlite rows können dict-ähnlich sein
        rid = r["id"]
        blob = r["blob"] or b""

        # Bereits kompakt? Dann überspringen.
        if isinstance(blob, str):
            blob_b = blob.encode("utf-8", errors="ignore")
        else:
            blob_b = bytes(blob)

        if _is_hex16_blob(blob_b):
            continue

        # Hash wird immer über den Original-Blob gebildet
        h = _hash_blob(blob_b).encode("utf-8")

        source_id = r["source_id"] if ("source_id" in getattr(r, "keys", lambda: [])()) else None
        if not source_id:
            source_id = f"db_{rid}"

        # Original-Blob als Datei sichern (wenn nicht schon vorhanden)
        path = os.path.join(out_dir, f"{source_id}.json")
        if not os.path.exists(path):
            try:
                # normal: JSON im Blob
                s = blob_b.decode("utf-8")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(s)
            except Exception:
                # Fallback: Bytes als Base64-Wrap sichern (immer noch reversibel)
                import base64
                wrap = {"encoding": "base64", "data": base64.b64encode(blob_b).decode("ascii")}
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(wrap, f, ensure_ascii=False)

        notes = r["notes"] if ("notes" in getattr(r, "keys", lambda: [])()) else ""
        notes = notes or ""
        if "compressed@" not in notes:
            notes = (notes + " " if notes else "") + f"compressed@{now_ts}"

        conn.execute(
            """
            UPDATE snapchains
            SET blob=?, status='compressed', source_id=?, notes=?
            WHERE id=?
            """,
            (h, source_id, notes, rid),
        )
        n += 1

    conn.commit()
    return n
def merge_to_meta() -> int:
    """
    Bündelt schwache Snaps in einen MetaSnap.
    - Sammelt META_BATCH Snaps mit weight < THRESHOLD
    - Erzeugt MetaSnap mit sources=IDs
    Returns: Anzahl gebildeter MetaSnaps.
    """
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, blob, weight FROM snapchains WHERE weight < ? LIMIT ?", (THRESHOLD, META_BATCH))
    rows = cur.fetchall() or []
    if len(rows) < META_BATCH:
        return 0

    ids = [str(r["id"]) for r in rows]
    hashes = [_hash_blob(r["blob"] if isinstance(r["blob"], (bytes, bytearray)) else str(r["blob"]).encode()) for r in rows]
    label = f"meta_{int(time.time())}"
    avg_w = sum(r["weight"] for r in rows) / len(rows)

    conn.execute(
        "INSERT INTO meta_snaps (label, score, sources) VALUES (?, ?, ?)",
        (label, avg_w, ",".join(ids)),
    )

    # Optional: SnapChains deaktivieren
    for r in rows:
        conn.execute("UPDATE snapchains SET status='archived' WHERE id=?", (r["id"],))

    conn.commit()
    return 1

# ----------------------------- Orchestrator ----------------------------------

def nightly_forgetting() -> Dict[str, Any]:
    """
    Führt alle Forgetting-Mechanismen aus.
    Rückgabe: Statistik als Dict.
    """
    stats = {}
    stats["decayed"] = decay_snaps()
    stats["compressed"] = compress_snaps()
    stats["metasnaps"] = merge_to_meta()
    return stats

# ----------------------------- CLI Test --------------------------------------

if __name__ == "__main__":
    print("[forgetting] Starte Selftest (Patch 2.2)…")
    sql_manager.ensure_schema()
    result = nightly_forgetting()
    print("[forgetting] Ergebnis:", result)

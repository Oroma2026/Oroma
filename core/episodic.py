#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/episodic.py
# Projekt:   ORÓMA
# Version:   v3.8 (Episodic Memory – RAM→DB, Replay-ready)
# Stand:     2025-10-11
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK (Was macht dieses Modul?)
# -----------------------------------------------------------------------------
#  Episodisches Gedächtnis für ORÓMA. Dieses Modul speichert zeitlich
#  begrenzte Erfahrungen (“Episoden”) in einer leichtgewichtigen,
#  SD-schonenden Form in SQLite und macht sie für Lernen, Replay,
#  Ähnlichkeitssuche (Recall) und Auswertung zugänglich.
#
#  Eine "Episode" ist eine geordnete Sequenz von Ereignissen (Events),
#  die jeweils mit:
#    • ts (Zeitstempel),
#    • optionalem Zustands-Hash (state_hash),
#    • einem repräsentativen Vektor (centroid) und
#    • einem Reward-Wert
#  abgelegt wird. Der Vektor kann z. B. der Zentroid einer SnapChain,
#  eines Spielzustands (z. B. TicTacToe) oder eines abgeleiteten
#  Feature-Arrays sein.
#
# ROLLE IM LERNKREISLAUF (Wie trägt es zum Lernen bei?)
# -----------------------------------------------------------------------------
#  ORÓMAs Lernkreislauf (vereinfacht):
#
#     Interaktion/Spiel → Snap → SnapChain → (optional) Episode(Eventfolge)
#          │                 │
#          │                 └─► DB (snapchains.blob) + quality/history
#          └─► Reward/Logs
#
#     Nacht/Dream: DreamWorker
#          ├─► Replay/Mutation   (nutzt SnapChains/Episoden)
#          ├─► Forgetting        (Gewichte verblassen, MetaSnaps)
#          └─► Research/Mission  (optional)
#
#  Dieses Modul bildet die Brücke zwischen “lokaler Erfahrung” und
#  “übertragbarem Gedächtnis”. Episoden/Events lassen sich:
#    • ähnlichkeitsbasiert wiederfinden (recall_similar),
#    • statistisch auswerten (z. B. Reward-Verläufe),
#    • im Dashboard visualisieren (z. B. Synapsen-/Episoden-Ansichten),
#    • für Replay/Transfer benutzen (z. B. Episoden gezielt neu abspielen).
#
#  Praktisch bedeutet das:
#   - Während des Tages (Spielen/Interaktion) können SnapChains in Episoden
#     “abgebildet” werden (z. B. ein Spielverlauf = 1 Episode mit mehreren Events).
#   - Der DreamWorker kann Episoden in der Nacht nutzen, um Varianten zu
#     erzeugen, Muster zu verdichten und Hypothesen zu testen.
#   - Über recall_similar(…) kann ORÓMA „verwandte Situationen“ schnell
#     wiederfinden und daraus lernen (z. B. beim nächsten Spiel).
#
# DATENFLUSS & PERSISTENZ (ohne JSON-Dateien, SD-schonend)
# -----------------------------------------------------------------------------
#  • Die API-Funktionen dieses Moduls schreiben ausschließlich in SQLite
#    (siehe core/sql_manager.get_conn()). Externe JSON-Exports sind nicht nötig.
#  • Tabellen:
#       episodes           – Kopf einer Episode (Titel, Meta, Summary)
#       episode_events     – zeitlich geordnete Events mit Vektor, Reward, Payload
#       episodic_metrics   – optionale Kennzahlen (Key/Value-Zeitreihe)
#  • Alle Writes sind kurz, atomar und robust; große Vektordaten liegen kompakt
#    als JSON-Text vor (centroid), optionale Binärdaten in payload (BLOB).
#
#  Vorteil: Die SD-Karte wird geschont (keine großen, häufigen JSON-Dateien);
#  der DreamWorker bekommt die Daten direkt aus der DB.
#
# SCHNITTSTELLEN (öffentliche Funktionen – Kern)
# -----------------------------------------------------------------------------
#  ensure_schema()                  → Tabellen sicherstellen (idempotent)
#  create_episode(title, meta)      → Episode anlegen, id zurückgeben
#  save_episode(title, chain_ids, centroid, meta)
#                                   → Episode anlegen + optional Anfangsevent
#  add_event(episode_id, centroid, state_hash, reward, payload, ts)
#                                   → Ereignis anhängen (Index wird auto-erhöht)
#  finalize_episode(episode_id, summary)
#                                   → Episode zusammenfassen/abschließen
#  get_episode(episode_id, with_events=True)
#                                   → Episode (inkl. Events) laden
#  recall_similar(vec, topk, max_age_days=None)
#                                   → Ähnlichkeitssuche über Event-Vektoren
#  find_similar(...)                → Alias für recall_similar
#
#  • recall_similar nutzt FAISS (falls installiert) oder Annoy; ansonsten eine
#    effiziente Python-Fallback-Distanzfunktion (L2).
#
# INTEGRATION & BEZUG ZU ANDEREN MODULEN
# -----------------------------------------------------------------------------
#  • core/sql_manager     – liefert DB-Connection, kümmert sich um Migrations-Sanftheit
#  • core/dream_worker    – liest Episoden/SnapChains in der Nacht, erzeugt Mutationen,
#                           vergisst schwache Muster (Patch 2.2), bildet MetaSnaps
#  • ui/episodic_ui.py    – UI-Blueprint: Episoden anlegen, laden, ähnlich suchen, listen
#  • ui/synapses_ui.py    – grafische Netz-Darstellung (Episoden/Events) im Browser
#  • core/reward          – Rewards fließen als Event-Attribut ein
#  • core/roter_faden     – Thread-/Intent-Kontext kann in meta/summary dokumentiert werden
#
# PERFORMANCE & SPEICHER
# -----------------------------------------------------------------------------
#  • MAX_RECALL_EVENTS begrenzt den Umfang für Similarity-Suche (Default 50k).
#    Für Pi-Systeme ausreichend, schützt RAM/CPU.
#  • FAISS/Annoy werden opportunistisch genutzt – wenn nicht vorhanden,
#    bleibt die Suche funktional (Fallback), nur etwas langsamer.
#
# SICHERHEIT & BETRIEB
# -----------------------------------------------------------------------------
#  • ensure_schema() ist idempotent – gefahrlos beim Start aufrufen.
#  • Episoden lassen sich versioniert halten: meta/summary sind freie Felder für
#    Provenienz, Experiment-Tags (z. B. Dream-Cycle “2025-10-11/1”) oder Thread-IDs.
#  • Keine Netzwerkzugriffe – alles lokal, auditierbar in SQLite.
#
# ENV-HINWEISE
# -----------------------------------------------------------------------------
#  OROMA_BASE                  – Projektbasis (Default /opt/ai/oroma/)
#  OROMA_MAX_RECALL_EVENTS     – Obergrenze für Recall-Kandidaten (Default 50000)
#
# SELFTEST
# -----------------------------------------------------------------------------
#  Direkt ausführbar:
#       python3 -m core.episodic
#  Legt eine Demo-Episode an, hängt Events an und testet recall_similar.
#  Ergebnis: “OK ✅” bei Erfolg.
#
# =============================================================================

from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

# ---------------------------------------------------------------------
# Projektbasis
# ---------------------------------------------------------------------
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma/")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

MAX_RECALL_EVENTS = int(os.environ.get("OROMA_MAX_RECALL_EVENTS", "50000"))

# ---------------------------------------------------------------------
# Optional: NumPy / FAISS / Annoy
# ---------------------------------------------------------------------
_HAS_NP = False
try:
    import numpy as _np
    _HAS_NP = True
except Exception:
    _HAS_NP = False

_HAS_FAISS = False
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

_HAS_ANNOY = False
try:
    from annoy import AnnoyIndex  # type: ignore
    _HAS_ANNOY = True
except Exception:
    _HAS_ANNOY = False

# ---------------------------------------------------------------------
# SQL-Manager
# ---------------------------------------------------------------------
_SQL_OK = True
try:
    from core import sql_manager  # type: ignore
    sql_manager.ensure_schema()
except Exception:
    _SQL_OK = False

# ---------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------
def _now() -> int:
    return int(time.time())

def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

def _from_json(txt: Optional[str]) -> Any:
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return txt

def _to_vec(x: Optional[Sequence[float]]) -> List[float]:
    if not x:
        return []
    return [float(v) for v in x]

def _l2(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return float("inf")
    if _HAS_NP:
        va, vb = _np.asarray(a, dtype=_np.float32), _np.asarray(b, dtype=_np.float32)
        return float(_np.linalg.norm(va - vb))
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))

# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------
def ensure_schema() -> None:
    """
    =============================================================================
    Pfad:    /opt/ai/oroma/core/episodic.py
    Projekt: ORÓMA – Episodic Memory (kompatibel zu sql_manager v3.7.3+)
    Stand:   2026-02-08
    =============================================================================

    Zweck
    -----
    Dieses Modul wird vom DreamWorker als "EpisodicMemory"-Adapter genutzt.

    WICHTIGER HINTERGRUND (Bugfix / Kompatibilität)
    ----------------------------------------------
    In frühen v3.7.x Varianten existierten *Legacy*-Episoden-Tabellen mit:
      - episodes(created_at, title, meta, summary)
      - episodic_metrics(created_at, key, value)

    Im aktuellen Kern (sql_manager) ist das Ziel-Schema jedoch:
      - episodes(ts_start, ts_end, kind, source, label, meta_json)
      - episode_events(episode_id, ts, event_type, ref_table, ref_id, meta_json, ...)
      - episodic_metrics(episode_id, ts, key, value)

    Der bisherige ensure_schema()-Block in dieser Datei versuchte u.a.
    Indizes auf "created_at" anzulegen. Wenn die DB bereits das *neue* Schema
    hatte, führte das zu wiederkehrenden Dream-Fehlern:

        sqlite3.OperationalError: no such column: created_at

    Diese Version delegiert zunächst an core/sql_manager.ensure_schema()
    (autoritativer Schema-Owner) und ergänzt danach *nur* optionale Spalten/
    Indizes für episode_events (idx/state_hash/centroid/reward/payload),
    die in ORÓMA als zusätzliche Diagnose-/Semantikfelder genutzt werden.

    Design-Prinzipien
    -----------------
      - Headless, minimal-invasiv, keine destruktiven Drops hier.
      - Keine stillen Fehler: relevante Probleme werden über log_suppressed()
        sichtbar, aber rate-limited (keine Log-Flut).
      - DB-Verbindungen immer sauber schließen (Connection-Factory übernimmt Close).

    ENV
    ---
      - (indirekt) alle OROMA_DB_* Variablen über sql_manager
    """
    if not _SQL_OK:
        return

    # 1) Basis-Schema: sql_manager ist der autoritative Owner.
    try:
        if hasattr(sql_manager, "ensure_schema"):
            sql_manager.ensure_schema()
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.episodic.schema.1",
            exc=e,
            msg="Suppressed exception (sql_manager.ensure_schema failed)",
            level=logging.WARNING,
            interval_s=600,
        )
        # Ohne Basis-Schema macht der Rest keinen Sinn.
        return

    # 2) Optionale Spalten/Indizes auf episode_events ergänzen (best effort).
    conn = None
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()

        # episode_events: optionale Spalten – nur additiv
        try:
            cur.execute("PRAGMA table_info(episode_events)")
            cols = [str(r[1]) for r in (cur.fetchall() or [])]
        except Exception:
            cols = []

        # idx/state_hash/centroid/reward/payload sind optional – existieren aber oft.
        optional_cols = [
            ("idx", "INTEGER"),
            ("state_hash", "TEXT"),
            ("centroid", "TEXT"),
            ("reward", "REAL"),
            ("payload", "BLOB"),
        ]
        for name, typ in optional_cols:
            if name not in cols:
                try:
                    cur.execute(f"ALTER TABLE episode_events ADD COLUMN {name} {typ}")
                    cols.append(name)
                except Exception as e:
                    log_suppressed(
                        logging.getLogger(__name__),
                        key="core.episodic.schema.2",
                        exc=e,
                        msg=f"Suppressed exception (ALTER TABLE episode_events ADD COLUMN {name} failed)",
                        level=logging.DEBUG,
                        interval_s=600,
                    )

        # Backfill idx (sofern vorhanden)
        if "idx" in cols:
            try:
                cur.execute("UPDATE episode_events SET idx = id WHERE idx IS NULL")
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.episodic.schema.3",
                    exc=e,
                    msg="Suppressed exception (backfill episode_events.idx failed)",
                    level=logging.DEBUG,
                    interval_s=600,
                )

        # Indizes (best effort) – keine created_at Abhängigkeit mehr.
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_epev_ep_idx ON episode_events(episode_id, idx)")
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.episodic.schema.4",
                exc=e,
                msg="Suppressed exception (create index idx_epev_ep_idx failed)",
                level=logging.DEBUG,
                interval_s=600,
            )
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_epev_ts ON episode_events(ts)")
        except Exception:
            pass
        # state_hash index nur, wenn die Spalte existiert
        try:
            cur.execute("PRAGMA table_info(episode_events)")
            cols2 = [str(r[1]) for r in (cur.fetchall() or [])]
            if "state_hash" in cols2:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_epev_state ON episode_events(state_hash)")
        except Exception:
            pass

        conn.commit()
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

def create_episode(title: str, meta: Optional[Dict[str, Any]] = None) -> int:
    """
    Erstellt eine Episode im **aktuellen** ORÓMA-Schema (sql_manager).

    Mapping (legacy → neu):
      - title          → episodes.label
      - meta(dict)     → episodes.meta_json
      - created_at     → episodes.ts_start
      - kind/source    → gesetzt auf 'dream' / 'core.episodic'

    Diese Funktion ist absichtlich klein, DB-safe und schließt Verbindungen
    zuverlässig (Lock-Vermeidung).
    """
    if not _SQL_OK:
        raise RuntimeError("sql_manager nicht verfügbar")

    ensure_schema()

    conn = None
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO episodes(ts_start, ts_end, kind, source, label, meta_json) VALUES(?,?,?,?,?,?)",
            (_now(), None, "dream", "core.episodic", str(title or ""), _json(meta or {})),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

def save_episode(title: str,
                 chain_ids: Optional[Sequence[int]] = None,
                 centroid: Optional[Sequence[float]] = None,
                 meta: Optional[Dict[str, Any]] = None) -> int:
    m = dict(meta or {})
    if chain_ids is not None:
        m["chain_ids"] = [int(x) for x in chain_ids]
    if centroid is not None:
        m["centroid_hint"] = [float(x) for x in centroid]

    eid = create_episode(title, meta=m)
    if centroid:
        try:
            add_event(eid, centroid=centroid, reward=0.0)
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.episodic.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
    return eid

def _next_event_index(conn, episode_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT MAX(idx) AS m FROM episode_events WHERE episode_id=?", (int(episode_id),))
    r = cur.fetchone()
    try:
        return (int(r["m"]) + 1) if r and r["m"] is not None else 0
    except Exception:
        return 0

def add_event(episode_id: int,
              *,
              centroid: Optional[Sequence[float]] = None,
              state_hash: Optional[str] = None,
              reward: float = 0.0,
              payload: Optional[bytes] = None,
              ts: Optional[int] = None,
              event_type: str = "dream/event") -> int:
    """
    Fügt ein Ereignis zur Episode hinzu (aktuelles ORÓMA-Schema).

    Hinweise:
      - episode_events.event_type ist im sql_manager-Schema NOT NULL.
      - meta_json existiert ebenfalls und wird minimal mit '{}' befüllt.
      - optionale Felder (idx/state_hash/centroid/reward/payload) werden genutzt,
        sofern ensure_schema() sie additiv ergänzt hat.

    DB-Sicherheit:
      - kurze Transaktion
      - Verbindung wird immer geschlossen
    """
    if not _SQL_OK:
        raise RuntimeError("sql_manager nicht verfügbar")

    ensure_schema()

    conn = None
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()
        idx = _next_event_index(conn, int(episode_id))
        cur.execute(
            """
            INSERT INTO episode_events(
                episode_id, idx, ts,
                event_type, ref_table, ref_id, meta_json,
                state_hash, centroid, reward, payload
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(episode_id),
                int(idx),
                int(ts or _now()),
                str(event_type or "dream/event"),
                None,  # ref_table
                None,  # ref_id
                "{}",  # meta_json (minimal)
                str(state_hash) if state_hash else None,
                _json(_to_vec(centroid)) if centroid is not None else None,
                float(reward or 0.0),
                payload if payload is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
def finalize_episode(episode_id: int, summary: Optional[Any] = None) -> None:
    """
    Finalisiert eine Episode im neuen Schema.

    Aktion:
      - setzt episodes.ts_end = now()
      - schreibt optional eine Summary in episodes.meta_json unter 'summary'
        (statt einer nicht mehr existierenden episodes.summary Spalte)

    DB-Sicherheit:
      - Verbindung wird geschlossen.
    """
    if not _SQL_OK:
        return
    ensure_schema()

    conn = None
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()

        # meta_json lesen
        cur.execute("SELECT meta_json FROM episodes WHERE id=?", (int(episode_id),))
        row = cur.fetchone()
        meta = {}
        try:
            if row and row["meta_json"]:
                meta = _from_json(row["meta_json"]) or {}
        except Exception:
            meta = {}

        if summary is not None:
            meta["summary"] = summary

        cur.execute(
            "UPDATE episodes SET ts_end=?, meta_json=? WHERE id=?",
            (int(_now()), _json(meta), int(episode_id)),
        )
        conn.commit()
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
def get_episode(episode_id: int, with_events: bool = True) -> Optional[Dict[str, Any]]:
    """
    Lädt eine Episode in einem kompatiblen Dict-Format.

    Kompatibilität:
      - Legacy-Felder (created_at/title/meta/summary) werden weiterhin ausgegeben,
        aber aus dem *neuen* Schema gemappt:
          created_at := ts_start
          title      := label
          meta       := meta_json
          summary    := None (sofern nicht separat gepflegt)

    DB-Sicherheit:
      - Verbindung wird geschlossen.
    """
    if not _SQL_OK:
        return None
    ensure_schema()

    conn = None
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, ts_start, ts_end, kind, source, label, meta_json FROM episodes WHERE id=?",
            (int(episode_id),),
        )
        row = cur.fetchone()
        if not row:
            return None

        out: Dict[str, Any] = {
            "id": int(row["id"]),
            "created_at": int(row["ts_start"]),   # legacy alias
            "title": row["label"],                # legacy alias
            "meta": _from_json(row["meta_json"]),
            "summary": None,
            # neue Felder zusätzlich:
            "ts_start": int(row["ts_start"]),
            "ts_end": int(row["ts_end"]) if row["ts_end"] is not None else None,
            "kind": row["kind"],
            "source": row["source"],
            "label": row["label"],
        }

        if with_events:
            cur.execute(
                """
                SELECT id, idx, ts, event_type, state_hash, centroid, reward
                FROM episode_events
                WHERE episode_id=?
                ORDER BY COALESCE(idx, id) ASC
                """,
                (int(episode_id),),
            )
            events = []
            for r in cur.fetchall() or []:
                events.append(
                    {
                        "id": int(r["id"]),
                        "idx": int(r["idx"]) if r["idx"] is not None else int(r["id"]),
                        "ts": int(r["ts"]),
                        "event_type": r["event_type"],
                        "state_hash": r["state_hash"] if "state_hash" in r.keys() else None,
                        "centroid": _from_json(r["centroid"]) or [],
                        "reward": float(r["reward"] or 0.0),
                    }
                )
            out["events"] = events

        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

# Alias
load_episode = get_episode

# ---------------------------------------------------------------------
# Recall (FAISS / Annoy / Fallback)
# ---------------------------------------------------------------------
def _load_event_centroids(conn, max_age_days: Optional[int] = None) -> List[Tuple[int, int, int, List[float]]]:
    cur = conn.cursor()
    where, params = [], []
    if max_age_days and max_age_days > 0:
        cutoff = _now() - int(max_age_days * 86400)
        where.append("ts >= ?")
        params.append(cutoff)
    sql = "SELECT id, episode_id, idx, centroid FROM episode_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY id DESC LIMIT {int(MAX_RECALL_EVENTS)}"
    cur.execute(sql, tuple(params))
    rows = cur.fetchall() or []
    out: List[Tuple[int, int, int, List[float]]] = []
    for r in rows:
        c = _from_json(r["centroid"]) or []
        if isinstance(c, list) and c:
            out.append((int(r["id"]), int(r["episode_id"]), int(r["idx"]), _to_vec(c)))
    return out

def recall_similar(vec: Sequence[float], topk: int = 10, max_age_days: Optional[int] = None) -> List[Dict[str, Any]]:
    if not _SQL_OK:
        return []
    ensure_schema()
    conn = sql_manager.get_conn()
    candidates = _load_event_centroids(conn, max_age_days=max_age_days)
    if not candidates:
        return []
    q = _to_vec(vec)
    if not q:
        return []
    dim = len(candidates[0][3])
    if len(q) != dim:
        q = (q + [0.0] * dim)[:dim]

    # ---- FAISS ----
    if _HAS_FAISS and _HAS_NP:
        index = faiss.IndexFlatL2(dim)  # type: ignore
        all_vecs = _np.asarray([c[3] for c in candidates], dtype=_np.float32)
        all_ids = _np.asarray([c[0] for c in candidates], dtype=_np.int64)
        index.add_with_ids(all_vecs, all_ids)  # type: ignore
        qv = _np.asarray([q], dtype=_np.float32)
        D, I = index.search(qv, int(topk))
        id2row = {c[0]: c for c in candidates}
        out = []
        for ev_id, dist in zip(I[0], D[0]):
            if int(ev_id) in id2row:
                _, ep_id, idx, _ = id2row[int(ev_id)]
                out.append({"event_id": int(ev_id), "episode_id": int(ep_id), "idx": int(idx), "dist": float(dist)})
        return out

    # ---- Annoy ----
    if _HAS_ANNOY:
        ann = AnnoyIndex(dim, "euclidean")
        id2row: Dict[int, Tuple[int, int, int, List[float]]] = {}
        for i, (ev_id, ep_id, idx, v) in enumerate(candidates):
            ann.add_item(i, v)
            id2row[i] = (ev_id, ep_id, idx, v)
        ann.build(10)
        ids = ann.get_nns_by_vector(q, topk, include_distances=True)
        out = []
        for i, dist in zip(ids[0], ids[1]):
            ev_id, ep_id, idx, _ = id2row[i]
            out.append({"event_id": ev_id, "episode_id": ep_id, "idx": idx, "dist": float(dist)})
        return out

    # ---- Fallback ----
    scored = []
    for ev_id, ep_id, idx, v in candidates:
        d = _l2(q, v)
        scored.append((d, (ev_id, ep_id, idx)))
    scored.sort(key=lambda x: x[0])
    return [{"event_id": ev_id, "episode_id": ep_id, "idx": idx, "dist": d}
            for d, (ev_id, ep_id, idx) in scored[:topk]]

# Alias
find_similar = recall_similar

# ---------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------
def _selftest() -> None:
    print("[episodic] selftest…")
    ensure_schema()
    eid = save_episode("demo", chain_ids=[1, 2], centroid=[0.1, -0.2, 0.05])
    print("episode id:", eid)
    import random
    for i in range(12):
        add_event(eid, centroid=[random.uniform(-1,1) for _ in range(8)], reward=0.1*i)
    ep = get_episode(eid, with_events=True)
    print("loaded episode:", ep["id"], "events:", len(ep.get("events", [])))
    rec = recall_similar([0.1, -0.2, 0.05, 0,0,0,0,0], topk=5)
    print("recall_similar:", rec[:3])
    print("[episodic] OK ✅")


# -----------------------------------------------------------------------------
# Synapsen-Graph (UI / Diagnose)
# -----------------------------------------------------------------------------
def synapse_graph(
    n: int = 25,
    events_per_episode: int = 2,
    min_sim: float = 0.35,
    within_episode_window: int = 3,
    max_pair_edges: int = 4000,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    =============================================================================
    Pfad:      /opt/ai/oroma/core/episodic.py
    Projekt:   ORÓMA – Synapsen-Graph (Episoden/Events → Netzwerk)
    Version:   v3.8+
    Stand:     2026-03-02
    Autor:     ORÓMA · KI-JWG-X1
    =============================================================================

    Zweck
    -----
    Liefert ein vis-network kompatibles Graph-Modell (nodes/edges) für die UI
    unter /synapses/.

    WICHTIG (Semantik / Modelltreue)
    -------------------------------
    Der Synapsen-Graph soll **keine** triviale Zeit-Kette ("Reihe") sein,
    sondern ein Netz aus:
      1) **Ko-Okkurrenz** (Events treten in derselben Episode gemeinsam auf)
      2) **Ähnlichkeit** (Cosine-Similarity zwischen Event-Centroids)

    Dadurch entstehen automatisch **Kreuzverbindungen** zwischen Episoden und
    nicht nur lineare Sequenzen. Die Edge-Weights sind normiert auf [0..1].

    Datenquellen / Felder
    ---------------------
    - episodes:        ts_start, kind, source, label, meta_json
    - episode_events:  ts, event_type, ref_table, ref_id, meta_json,
                       optional: idx, centroid, reward (via ensure_schema)

    Parameter
    ---------
    n:
      Anzahl Episoden (neueste nach ts_start DESC), die als Subgraph betrachtet werden.
    events_per_episode:
      Anzahl Events pro Episode (0 = alle, aber hart gecappt). Wird bevorzugt über idx
      chronologisch gewählt (falls idx vorhanden), sonst über ts.
    min_sim:
      Mindest-Cosine-Similarity für inter-episodische Kanten (0..1).
    within_episode_window:
      Fenster für Ko-Okkurrenz-Kanten innerhalb einer Episode. Beispiel:
      window=3 verbindet Event i zusätzlich mit i+1..i+3 (und symmetrisch).
    max_pair_edges:
      Schutz gegen O(n^2) Explosion bei vielen Knoten.

    Return
    ------
    (nodes, edges)
      nodes: [{id,label,type,weight,ts,...}, ...]
      edges: [{source,target,weight,kind,...}, ...]
    """

    # ---- Guardrails / Parameter normalisieren
    try:
        n = int(n)
    except Exception:
        n = 25
    n = max(1, min(400, n))

    try:
        events_per_episode = int(events_per_episode)
    except Exception:
        events_per_episode = 2
    events_per_episode = max(0, min(50, events_per_episode))

    try:
        min_sim = float(min_sim)
    except Exception:
        min_sim = 0.35
    min_sim = max(0.0, min(1.0, min_sim))

    try:
        within_episode_window = int(within_episode_window)
    except Exception:
        within_episode_window = 3
    within_episode_window = max(1, min(12, within_episode_window))

    try:
        max_pair_edges = int(max_pair_edges)
    except Exception:
        max_pair_edges = 4000
    max_pair_edges = max(200, min(20000, max_pair_edges))

    if not _SQL_OK:
        return ([], [])

    # ---- lokale Helper: Cosine Similarity (0..1)
    def _cos_sim(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        # NumPy optional – aber wir bleiben hier bei python, um headless/robust zu sein
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            fx = float(x); fy = float(y)
            dot += fx * fy
            na += fx * fx
            nb += fy * fy
        if na <= 0.0 or nb <= 0.0:
            return 0.0
        # Clamp gegen numerische Ausreißer
        c = dot / (math.sqrt(na) * math.sqrt(nb))
        if c < -1.0: c = -1.0
        if c > 1.0:  c = 1.0
        # wir wollen 0..1 für UI-Weight
        return (c + 1.0) * 0.5

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    # Dedup / Edge-Accu: mehrere Kanten gleichen Paars zusammenführen (max weight)
    edge_best: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _add_edge(a_id: str, b_id: str, w: float, kind: str) -> None:
        if not a_id or not b_id or a_id == b_id:
            return
        w = float(w or 0.0)
        if w <= 0.0:
            return
        if w > 1.0:
            w = 1.0
        if w < 0.0:
            w = 0.0

        # undirected: stabil sortieren
        k = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        prev = edge_best.get(k)
        if prev is None or float(prev.get("weight", 0.0) or 0.0) < w:
            edge_best[k] = {"source": k[0], "target": k[1], "weight": round(w, 4), "kind": kind}

    conn = None
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()

        # 1) Episoden auswählen
        cur.execute(
            "SELECT id, ts_start, kind, source, label, meta_json "
            "FROM episodes ORDER BY ts_start DESC LIMIT ?",
            (n,),
        )
        eps = cur.fetchall() or []

        # 2) Für jede Episode Events laden (optional idx/centroid/reward)
        event_nodes: List[Dict[str, Any]] = []
        event_vecs: List[List[float]] = []
        event_ids: List[str] = []
        event_meta: List[Dict[str, Any]] = []

        for eprow in eps:
            ep_id = int(eprow["id"])
            ep_ts = int(eprow.get("ts_start") or 0)
            ep_kind = str(eprow.get("kind") or "")
            ep_src = str(eprow.get("source") or "")
            ep_label = str(eprow.get("label") or "") or ep_kind or f"Episode {ep_id}"

            ep_node_id = f"ep_{ep_id}"
            nodes.append({
                "id": ep_node_id,
                "label": f"Episode #{ep_id} · {ep_label}",
                "type": "episode",
                "weight": 0.5,
                "ts": ep_ts,
                "kind": ep_kind,
                "source": ep_src,
            })

            # Events: bevorzugt idx ASC (falls vorhanden), sonst ts ASC
            # NOTE: episode_events.idx kann optional sein – ORDER BY COALESCE(idx, ts)
            # (idx ist oft eine monotone Reihenfolge, ts kann identisch sein)
            cur.execute(
                "SELECT id, episode_id, ts, event_type, ref_table, ref_id, meta_json, "
                "       idx, state_hash, centroid, reward "
                "FROM episode_events "
                "WHERE episode_id=? "
                "ORDER BY COALESCE(idx, ts) ASC",
                (ep_id,),
            )
            evrows = cur.fetchall() or []
            if not evrows:
                continue

            # events_per_episode=0 => alle, aber cap (Schutz)
            if events_per_episode <= 0:
                evsel = evrows[-50:]  # cap: 50 pro Episode
            else:
                evsel = evrows[-events_per_episode:]

            # Ko-Okkurrenz innerhalb Episode: später nach event id list
            local_ev_ids: List[str] = []

            for r in evsel:
                ev_id = int(r["id"])
                ev_ts = int(r.get("ts") or 0)
                ev_type = str(r.get("event_type") or "event")
                ref_tbl = str(r.get("ref_table") or "")
                ref_id = r.get("ref_id")
                reward = float(r.get("reward") or 0.0)
                centroid = _from_json(r.get("centroid"))
                vec = _to_vec(centroid if isinstance(centroid, (list, tuple)) else None)

                node_id = f"ev_{ep_id}_{ev_id}"
                # Label: knapp, aber aussagekräftig
                ref_part = ""
                if ref_tbl and ref_id is not None:
                    ref_part = f" · {ref_tbl}:{int(ref_id)}"
                nodes.append({
                    "id": node_id,
                    "label": f"{ev_type}{ref_part}\nR={reward:.3f}",
                    "type": "event",
                    # Node-Weight ist NICHT Synapsenstärke – hier nur Diagnose (Reward-Magnitude)
                    "weight": min(1.0, abs(reward)),
                    "ts": ev_ts,
                    "episode_id": ep_id,
                    "event_type": ev_type,
                })

                # Episode → Event (leichte Kante, rein strukturell)
                _add_edge(ep_node_id, node_id, 0.18, "belongs")

                local_ev_ids.append(node_id)

                # Similarity candidate pool
                if vec:
                    event_nodes.append({"id": node_id})
                    event_vecs.append(vec)
                    event_ids.append(node_id)
                    event_meta.append({"episode_id": ep_id})

            # Ko-Okkurrenz-Kanten: innerhalb Episode, im Fenster
            if within_episode_window > 0 and len(local_ev_ids) >= 2:
                for i in range(len(local_ev_ids)):
                    for j in range(i + 1, min(len(local_ev_ids), i + 1 + within_episode_window)):
                        # je näher, desto stärker
                        delta = j - i
                        w = 1.0 / (1.0 + float(delta))
                        _add_edge(local_ev_ids[i], local_ev_ids[j], w, "cooc")

        # 3) Ähnlichkeits-Kanten zwischen Event-Vektoren (cross connections)
        # Schutz: Wir rechnen paarweise nur bis max_pair_edges.
        # Heuristik: wenn sehr viele Event-Knoten, sampeln wir die Kandidaten.
        m = len(event_vecs)
        if m >= 2 and min_sim > 0.0:
            # optional: L2-normalisieren für stabile Cosine
            # (Wir nutzen Cosine-Formel ohnehin, Norm wird intern gerechnet.)
            pairs = 0
            for i in range(m):
                vi = event_vecs[i]
                a_id = event_ids[i]
                for j in range(i + 1, m):
                    if pairs >= max_pair_edges:
                        break
                    vj = event_vecs[j]
                    b_id = event_ids[j]
                    s = _cos_sim(vi, vj)
                    if s >= min_sim:
                        _add_edge(a_id, b_id, s, "sim")
                    pairs += 1
                if pairs >= max_pair_edges:
                    break

        # finalize edges list
        edges = list(edge_best.values())
        return (nodes, edges)

    except Exception as e:
        # keine stillen Fehler – sichtbar loggen, aber nicht crashen
        try:
            logging.getLogger(__name__).warning("synapse_graph failed: %s", e)
        except Exception:
            pass
        return (nodes, list(edge_best.values()) if edge_best else [])
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    _selftest()
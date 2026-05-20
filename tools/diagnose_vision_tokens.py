#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/diagnose_vision_tokens.py
# Projekt:   ORÓMA
# Modul:     Diagnose-Tool für Vision-Tokens (vision/token)
# Version:   v3.7.3
# Stand:     2025-12-01
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#   Liest die letzten vision/token-SnapChains aus SQLite (snapchains-Tabelle)
#   und zeigt eine kompakte Übersicht:
#
#     • id, lokale Zeit, quality
#     • motion, edges, color
#     • Länge des Feature-Vektors v
#
#   Damit kannst du schnell prüfen:
#     – wie viele Tokens in letzter Zeit geschrieben wurden
#     – ob sich die Werte plausibel mit Tag/Nacht/Bewegung ändern
#     – ob die Thresholds (OROMA_AV_MIN_QUALITY, OROMA_AV_MIN_MOTION, ...)
#       sinnvoll greifen.
#
# Nutzung
# ───────
#   cd /opt/ai/oroma
#   PYTHONPATH=/opt/ai/oroma \
#     python3 tools/diagnose_vision_tokens.py --limit 20
#
#   Optionale Argumente:
#     --limit N         → Anzahl zu zeigender Tokens (Default: 20)
#     --origin ORIGIN   → SnapChain-Origin (Default: vision/token)
#
# Abhängigkeiten
# ──────────────
#   • core.sql_manager  – für DB-Verbindung
#   • Python-Standardbibliothek (json, argparse, datetime)
#
# Hinweis
# ───────
#   Die Tokens werden aktuell als JSON im Feld `blob` gespeichert (cam_token).
#   Dieses Tool decodiert die JSON-Struktur best effort; unbekannte Felder
#   werden ignoriert.
# =============================================================================

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any, Dict, List

from core import sql_manager


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose-Tool für vision/token SnapChains."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Anzahl der Tokens, die angezeigt werden sollen (Default: 20).",
    )
    parser.add_argument(
        "--origin",
        type=str,
        default="vision/token",
        help='Origin-Filter (Default: "vision/token").',
    )
    return parser.parse_args()


def _fetch_tokens(origin: str, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with sql_manager.get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id,
                   ts,
                   origin,
                   quality,
                   blob
              FROM snapchains
             WHERE origin = ?
          ORDER BY id DESC
             LIMIT ?
            """,
            (origin, int(limit)),
        )
        for r in cur.fetchall():
            rows.append(dict(r))
    return rows


def _decode_blob(blob: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(blob.decode("utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _fmt_ts(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def main() -> None:
    args = _parse_args()
    origin = args.origin
    limit = max(1, int(args.limit))

    tokens = _fetch_tokens(origin, limit)
    if not tokens:
        print(f"Keine SnapChains mit origin='{origin}' gefunden.")
        return

    # Überschrift
    print(f"Letzte {len(tokens)} Tokens für origin='{origin}':\n")
    print(
        "{:>6}  {:19}  {:7}  {:7}  {:7}  {:7}  {:6}".format(
            "id", "ts_local", "q", "motion", "edges", "color", "len(v)"
        )
    )
    print("-" * 66)

    # Stats sammeln
    q_vals: List[float] = []
    m_vals: List[float] = []
    e_vals: List[float] = []
    c_vals: List[float] = []

    for row in tokens:
        blob = row.get("blob") or b""
        data = _decode_blob(blob)

        vec = data.get("v", []) or []
        if not isinstance(vec, list):
            vec = []

        motion = data.get("motion")
        edges = data.get("edges")
        color = data.get("color")

        q = float(row.get("quality", 0.0) or 0.0)
        ts = _fmt_ts(row.get("ts", 0))

        # Stats
        q_vals.append(q)
        if isinstance(motion, (int, float)):
            m_vals.append(float(motion))
        if isinstance(edges, (int, float)):
            e_vals.append(float(edges))
        if isinstance(color, (int, float)):
            c_vals.append(float(color))

        print(
            "{:6d}  {:19}  {:7.3f}  {:7.3f}  {:7.3f}  {:7.3f}  {:6d}".format(
                int(row["id"]),
                ts,
                q,
                float(motion or 0.0),
                float(edges or 0.0),
                float(color or 0.0),
                len(vec),
            )
        )

    def _agg(vals: List[float]) -> str:
        if not vals:
            return "min=nan max=nan avg=nan"
        vmin = min(vals)
        vmax = max(vals)
        vavg = sum(vals) / len(vals)
        return f"min={vmin:.3f} max={vmax:.3f} avg={vavg:.3f}"

    print("\nStatistik:")
    print(f"  quality: {_agg(q_vals)}")
    print(f"  motion : {_agg(m_vals)}")
    print(f"  edges  : {_agg(e_vals)}")
    print(f"  color  : {_agg(c_vals)}")


if __name__ == "__main__":
    main()
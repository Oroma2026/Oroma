#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Datei:      /opt/ai/oroma/ui/synapses_ui.py
# Projekt:    🧠 ORÓMA v3.8 – Synapsen-Graph (Episoden / SnapChains)
# Stand:      2025-10-12
# Autor:      ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# -----
#   Visualisierung der neuronalen Verknüpfungen innerhalb des ORÓMA-Lernsystems:
#     • Verbindet episodisches Gedächtnis (core/episodic.py) mit der
#       SnapChain-Datenbank (core/sql_manager.py)
#     • Stellt eine interaktive Graph-Darstellung bereit (vis-network im Frontend)
#     • Ermöglicht Analyse von Lernverläufen, Ähnlichkeiten und Netzwerk-Dichte
#
# Lernbezug
# ---------
#   Der Synapsen-Graph ist eine **Meta-Visualisierung** des aktuellen
#   Gedächtnisnetzes. Jede Linie entspricht einer funktionalen oder
#   semantischen Verbindung zwischen Lernobjekten:
#
#     📘 Episode → Folge von Events (aus DreamWorker oder ImportGate)
#     🔗 SnapChain → Abfolge von Snaps (z. B. aus Spielen oder Sensoren)
#
#   Der Graph dient der:
#     • Diagnose von Lernmustern (Clusterbildung, Wiederholung, Vergessen)
#     • Anzeige der „mentalen Nähe“ zwischen Ereignissen (Ähnlichkeitsgewicht)
#     • Vorbereitung für Swarm-Learning-Module (Peer-Sync)
#
# -----------------------------------------------------------------------------
# Architektur
# -----------------------------------------------------------------------------
#   /synapses/            → HTML-Seite (templates/synapses.html)
#   /synapses/api/data    → liefert { nodes, edges } im vis-network-Format
#
# Datenquellen
# ------------
#   1️⃣ core.episodic.synapse_graph()
#       - nutzt Episoden + Events aus dem episodischen Gedächtnis
#       - berechnet Ähnlichkeiten, Gewichte und Cluster
#
#   2️⃣ core.sql_manager.snapchains (Fallback)
#       - falls episodic fehlt, werden SnapChains aus der DB gelesen
#       - Kanten werden sequentiell über Zeit/ID gebildet
#
#   3️⃣ Minimal-Demo (letzter Fallback)
#       - generiert synthetische Nodes/Edges, falls keine Daten vorhanden
#
# -----------------------------------------------------------------------------
# Sicherheit & Token
# -----------------------------------------------------------------------------
#   - Token-Authentifizierung optional (OROMA_UI_TOKEN)
#   - Wenn kein Token gesetzt → Zugriff automatisch erlaubt
#   - Bei gesetztem Token: Header "X-OROMA-TOKEN" ODER ?token=...
#
# -----------------------------------------------------------------------------
# Besonderheiten v3.8
# -----------------------------------------------------------------------------
#   ✅ Keine Dummy-Werte – arbeitet direkt mit DB- oder Episoden-Daten
#   ✅ Robuste Fehlerbehandlung + Logging
#   ✅ UTF-8-sichere JSON-Ausgabe (vis-network-kompatibel)
#   ✅ Getrennte Layer: Core (Episoden) / Fallback (DB) / Demo (synthetisch)
# =============================================================================

from __future__ import annotations
import os, random, logging
from typing import Dict, Any, List
from flask import Blueprint, render_template, request, jsonify, make_response

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.synapses_ui")
if not LOG.handlers:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [SynapsesUI] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOG.addHandler(sh)
LOG.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Core-Imports (optional)
# -----------------------------------------------------------------------------
try:
    from core import episodic
    LOG.info("✅ core.episodic geladen.")
except Exception as e:
    episodic = None
    LOG.warning("⚠️ episodic-Modul nicht verfügbar: %s", e)

try:
    from core import sql_manager
    LOG.info("✅ core.sql_manager geladen.")
except Exception as e:
    sql_manager = None
    LOG.warning("⚠️ sql_manager nicht verfügbar: %s", e)

# -----------------------------------------------------------------------------
# Blueprint
# -----------------------------------------------------------------------------
synapses_bp = Blueprint(
    "synapses_ui", __name__,
    template_folder="templates", static_folder="static",
    url_prefix="/synapses"
)

# -----------------------------------------------------------------------------
# Authentifizierung
# -----------------------------------------------------------------------------
def _check_auth() -> bool:
    """Erlaubt Zugriff je nach Token-Konfiguration oder LAN-Client."""
    tok_cfg = os.environ.get("OROMA_UI_TOKEN", "").strip()
    if not tok_cfg:
        return True  # kein Token gesetzt → frei zugänglich (LAN)
    tok = request.headers.get("X-OROMA-TOKEN") or request.args.get("token") or ""
    if tok == tok_cfg:
        return True
    # kleine LAN-Whitelist (192.168.x, 10.x, 172.16–31, localhost)
    ra = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").strip()
    local_prefixes = (
        "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
        "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
        "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
        "127.", "0.0.0.0"
    )
    if any(ra.startswith(p) for p in local_prefixes):
        return True
    LOG.warning("❌ Unauthorized access from %s", ra)
    return False

@synapses_bp.before_request
def _mw():
    """Middleware prüft Token (wenn gesetzt)."""
    if _check_auth():
        return
    return make_response("Unauthorized", 401)

# -----------------------------------------------------------------------------
# Seite
# -----------------------------------------------------------------------------
@synapses_bp.route("/", methods=["GET"])
def page():
    """Render-Seite (HTML-Template)."""
    return render_template("synapses.html")

# -----------------------------------------------------------------------------
# API – Data
# -----------------------------------------------------------------------------
@synapses_bp.route("/api/data", methods=["GET"])
def api_data():
    """
    Liefert alle aktuell verfügbaren Episoden- oder SnapChain-Verbindungen
    als JSON für das vis-network-Frontend.
    """
    try:
        n   = int(request.args.get("n", 25))
        ev  = int(request.args.get("ev", 2))
        sim = float(request.args.get("sim", 0.35))
    except Exception:
        return jsonify({"ok": False, "error": "invalid parameters"}), 400

    LOG.info("📡 Synapse-Request n=%d ev=%d sim=%.2f", n, ev, sim)


    # 0️⃣ NMR/ObjectGraph Quelle – persistente Synapsen als Relationstyp 'synaptic'
    #    (core/nmr_synaptic_plasticity.py schreibt object_relations(relation='synaptic')).
    #
    #    Vorteil:
    #      - echte Persistenz (Langzeit-Plastizität) statt reiner UI-Projektion
    #      - NMR kann diese Kanten als Soft-Evidence nutzen
    try:
        with sql_manager.get_conn() as conn:
            # prüfe ob es überhaupt synaptic edges gibt
            cur = conn.execute(
                """
                SELECT COUNT(*) AS n
                  FROM object_relations
                 WHERE relation = 'synaptic'
                """
            )
            row = cur.fetchone()
            have_syn = int(row["n"] or 0) if row else 0
            if have_syn > 0:
                # Edge-Cap defensiv: vis-network bleibt sonst unbenutzbar auf iPhone/Safari.
                edge_cap = max(50, min(4000, n * max(5, ev) * 2))
                cur = conn.execute(
                    """
                    SELECT r.a_id, r.b_id, r.confidence, r.ts, r.notes,
                           na.kind AS a_kind, na.label AS a_label, na.meta_json AS a_meta,
                           nb.kind AS b_kind, nb.label AS b_label, nb.meta_json AS b_meta
                      FROM object_relations r
                      JOIN object_nodes na ON na.id = r.a_id
                      JOIN object_nodes nb ON nb.id = r.b_id
                     WHERE r.relation = 'synaptic'
                     ORDER BY r.ts DESC
                     LIMIT ?
                    """,
                    (int(edge_cap),),
                )
                rows = cur.fetchall() or []
                node_map = {}
                nodes = []
                edges = []
                for r in rows:
                    for side in ("a", "b"):
                        nid = int(r[f"{side}_id"])
                        if nid in node_map:
                            continue
                        kind = str(r[f"{side}_kind"] or "node")
                        label = str(r[f"{side}_label"] or f"node:{nid}")
                        meta_raw = r.get(f"{side}_meta")
                        # UI-Label: compact, meta optional
                        ui_label = label
                        if kind == "event" and label.startswith("event:"):
                            ui_label = f"Event {label.split(':',1)[1]}"
                        nodes.append({
                            "id": f"n_{nid}",
                            "label": ui_label,
                            "type": kind,
                            "weight": 0.0,  # Node weight optional, edges tragen Semantik
                        })
                        node_map[nid] = f"n_{nid}"

                    w = float(r["confidence"] or 0.0)
                    if w < 0.0:
                        w = 0.0
                    if w > 1.0:
                        w = 1.0
                    edges.append({
                        "source": node_map[int(r["a_id"])],
                        "target": node_map[int(r["b_id"])],
                        "weight": round(w, 4),
                        "kind": "synaptic",
                    })

                LOG.info("📊 NMR synaptic Graph: nodes=%d edges=%d (cap=%d)", len(nodes), len(edges), edge_cap)
                return jsonify({"ok": True, "nodes": nodes, "edges": edges})
    except Exception as e:
        LOG.warning("Synapses: NMR synaptic fetch failed, fallback to episodic (%s)", e)


    # 1️⃣ Primäre Quelle – episodic.synapse_graph()
    if episodic and hasattr(episodic, "synapse_graph"):
        try:
            nodes, edges = episodic.synapse_graph(n=n, events_per_episode=ev, min_sim=sim)
            if nodes:
                LOG.info("🧠 Episodic Graph mit %d Knoten, %d Kanten", len(nodes), len(edges))
                return jsonify({"ok": True, "nodes": nodes, "edges": edges})
        except Exception as e:
            LOG.warning("Fehler in episodic.synapse_graph(): %s", e)

    # 2️⃣ Fallback – SnapChains aus DB
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    if sql_manager:
        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, origin, quality, status, ts "
                    "FROM snapchains ORDER BY ts DESC LIMIT ?",
                    (n,)
                )
                rows = cur.fetchall() or []
                last_id = None
                # Wir sammeln zuerst alle Nodes (mit ts/origin/quality),
                # bauen danach **Netz-Kanten** (Kreuzverbindungen) – nicht nur eine Kette.
                tmp = []  # [{id, ts, origin, q}]
                for r in rows:
                    nid = f"sc_{r['id']}"
                    ts = int(r.get("ts") or 0)
                    q = float(r.get("quality", 0.0) or 0.0)
                    origin = (r.get("origin") or "unknown").split(":")[-1]
                    nodes.append({
                        "id": nid,
                        "label": f"SnapChain #{r['id']} ({origin})\nq={q:.3f}",
                        "type": "chain",
                        "weight": q,
                        "ts": ts,
                        "origin": origin,
                    })
                    tmp.append({"id": nid, "ts": ts, "origin": origin, "q": q})

                # --- Kanten: Zeit-Nachbarschaft + Origin-Kohorte
                # Ziel: "Netz" statt "Reihe" → pro Node mehrere Nachbarn.
                # Weight ist 0..1 (vis-network width mapping).
                # Schutz: Begrenze Gesamtkanten.
                max_edges = max(50, min(4000, n * 12))
                tau = 3600.0  # 1h Zeitkonstante für Nähe-Gewichtung

                def _w_time(a_ts: int, b_ts: int) -> float:
                    dt = abs(float(a_ts) - float(b_ts))
                    # exp(-dt/tau) → 1 bei dt=0, schnell fallend
                    try:
                        w = pow(2.718281828, -dt / tau)
                    except Exception:
                        w = 0.0
                    if w < 0.0: w = 0.0
                    if w > 1.0: w = 1.0
                    return w

                # sort nach ts DESC (wie rows)
                tmp_sorted = sorted(tmp, key=lambda x: int(x.get("ts") or 0), reverse=True)

                # 1) Zeit-Nachbarn: je Node bis zu k Nachbarn
                k_time = 3
                edge_best = {}
                def _add(a: str, b: str, w: float, kind: str):
                    if not a or not b or a == b:
                        return
                    w = float(w or 0.0)
                    if w <= 0.0:
                        return
                    if w > 1.0: w = 1.0
                    if w < 0.0: w = 0.0
                    k = (a, b) if a < b else (b, a)
                    prev = edge_best.get(k)
                    if prev is None or float(prev.get("weight", 0.0) or 0.0) < w:
                        edge_best[k] = {"source": k[0], "target": k[1], "weight": round(w, 4), "kind": kind}

                for i, a in enumerate(tmp_sorted):
                    a_id = a["id"]; a_ts = int(a.get("ts") or 0)
                    for j in range(i + 1, min(len(tmp_sorted), i + 1 + k_time)):
                        b = tmp_sorted[j]
                        w = _w_time(a_ts, int(b.get("ts") or 0))
                        _add(a_id, b["id"], w, "time")

                # 2) Origin-Kohorten: innerhalb gleicher origin zusätzliche Querverbindungen
                by_origin = {}
                for x in tmp_sorted:
                    by_origin.setdefault(x.get("origin") or "unknown", []).append(x)
                for org, items in by_origin.items():
                    if len(items) < 3:
                        continue
                    # Verbindung: jedes Element zu den nächsten 2 innerhalb der Kohorte
                    for i in range(len(items)):
                        a = items[i]
                        for j in range(i + 1, min(len(items), i + 3)):
                            b = items[j]
                            # origin-bonus + zeitfaktor
                            w = min(1.0, 0.25 + 0.75 * _w_time(int(a.get("ts") or 0), int(b.get("ts") or 0)))
                            _add(a["id"], b["id"], w, "origin")

                edges = list(edge_best.values())[:max_edges]

                LOG.info("📊 DB-Fallback genutzt (%d SnapChains).", len(nodes))
                return jsonify({"ok": True, "nodes": nodes, "edges": edges})("📊 DB-Fallback genutzt (%d SnapChains).", len(nodes))
                return jsonify({"ok": True, "nodes": nodes, "edges": edges})
        except Exception as e:
            LOG.error("sql_manager error: %s", e)
            return jsonify({"ok": False, "error": f"sql_manager error: {e}"}), 500

    # 3️⃣ Minimal-Fallback (Demo-Daten)
    for i in range(n):
        nodes.append({"id": f"demo{i}", "label": f"Node {i}", "type": "demo"})
        if i > 0:
            edges.append({
                "source": f"demo{i-1}",
                "target": f"demo{i}",
                "weight": random.random()
            })
    LOG.info("🧩 Demo-Daten generiert (%d Nodes).", len(nodes))
    return jsonify({"ok": True, "nodes": nodes, "edges": edges})
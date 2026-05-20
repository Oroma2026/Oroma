// =============================================================================
// Pfad:      /opt/ai/oroma/ui/static/oroma_graph.js
// Projekt:   ORÓMA
// Modul:     Synapsen-Graph Utilities (vis-network)
// Version:   v3.5
// Stand:     2025-09-27
//
// Zweck / Rolle
// ─────────────
//  - JavaScript-Hilfsmodul für das ORÓMA-Dashboard.
//  - Baut interaktive Graphen aus SnapChains, Episoden, MetaSnaps, Hypothesen
//    und Kausalitäten auf Basis von vis-network auf.
//  - Visualisierung dient Explainability 2.0 (transparente Einsicht in
//    Snap-Verknüpfungen und Hypothesenbildung).
//
// Hauptfunktionen
// ───────────────
//  • buildVisGraph(container, nodes, edges)
//    - Initialisiert oder ersetzt eine vis-network Instanz im angegebenen
//      Container-Element.
//    - Nutzt die übergebenen Knoten (nodes) und Kanten (edges).
//
// Node-Typen (Farben / Shapes)
// ────────────────────────────
//  - episode     → blau (#60a5fa), dot, groß (size 18)
//  - event       → amber (#f59e0b), dot
//  - snap        → lila (#a78bfa), dot
//  - metasnap    → grün (#10b981), dot (+Score-Anzeige im Tooltip)
//  - hypothesis  → rot/rosa (#f43f5e), box (+Text im Tooltip)
//  - causal      → teal (#14b8a6), dot
//  - default     → grau (#9aa4b2)
//
// Edge-Darstellung
// ────────────────
//  - Gewicht (0..1) → Kantenbreite (1..8 px).
//  - Directed = Pfeilrichtung „to“.
//  - Glättung: dynamic (geschwungene Linien).
//
// Tooltip-System
// ──────────────
//  - Node Hover: zeigt ID, Label, Typ, ggf. Score oder Hypothese-Text.
//  - Edge Hover: zeigt Quelle, Ziel, Gewicht.
//  - Tooltips werden bei Pan/Zoom/Drag ausgeblendet.
//  - HTML wird escaped, um XSS zu vermeiden.
//
// Optionen / Physics
// ──────────────────
//  - Solver: forceAtlas2Based (mit Dämpfung, Federkonstanten etc.)
//  - Stabilisierung: 200 Iterationen.
//  - Interaktion: Hover, Zoom, DragView aktiviert.
//
// Abhängigkeiten
// ──────────────
//  - vis-network (CDN oder lokal eingebunden).
//  - Läuft headless im Browser – keine nativen UI-Stacks erforderlich.
//  - Eingebettet in Flask/Jinja Templates (`health.html`, `registry.html` etc.).
//
// Rückgabe
// ─────────
//  - Gibt die erzeugte `vis.Network`-Instanz zurück.
//  - Speichert diese zusätzlich als `container._network` (für Zerstören/Reinit).
//
// Sicherheit
// ──────────
//  - Eingaben (Label, Text) werden per `escapeHtml` vor HTML-Injection geschützt.
//  - Tooltip läuft rein clientseitig, keine Server-Calls.
//
// Kompatibilität
// ──────────────
//  - Getestet mit vis-network v9.x, Chrome/Firefox/Edge.
//  - ORÓMA Dashboard v3.5 (Explainability 2.0).
//
// Hinweise
// ────────
//  - Bei großen Graphen (>10k Knoten/Kanten) Performance-Test empfohlen.
//  - Erweiterbar um Custom-Events (z. B. Node-Click → Detail-Panel).
// =============================================================================

export function buildVisGraph(container, nodes, edges) {
  // Farbwahl nach Typ
  function colorForType(type) {
    switch(type) {
      case "episode":    return "#60a5fa"; // blau
      case "event":      return "#f59e0b"; // amber
      case "snap":       return "#a78bfa"; // lila
      case "metasnap":   return "#10b981"; // grün
      case "hypothesis": return "#f43f5e"; // rot/rosa
      case "causal":     return "#14b8a6"; // teal
      default:           return "#9aa4b2"; // grau
    }
  }

  // Kantengewicht auf Breite mappen
  function widthForWeight(w) {
    const x = Math.max(0, Math.min(1, Number(w) || 0));
    return 1 + x * 7; // 1..8 px
  }

  // vis-network Nodes
  const visNodes = nodes.map(n => ({
    id: n.id,
    label: n.label || String(n.id),
    color: {
      background: colorForType(n.type),
      border: "#0f172a",
      highlight: { background: colorForType(n.type), border: "#334155" }
    },
    shape: n.type === "hypothesis" ? "box" : "dot",
    size: n.type === "episode" ? 18 : (n.type === "hypothesis" ? 14 : 10),
    font: { color: "#e6e6e6", size: 12 }
  }));

  // vis-network Edges
  const visEdges = edges.map(e => ({
    from: e.source,
    to: e.target,
    value: Math.max(0, Math.min(1, Number(e.weight) || 0)),
    color: { color: "#94a3b8" },
    width: widthForWeight(e.weight),
    smooth: { enabled: true, type: "dynamic" },
    arrows: e.directed ? "to" : undefined
  }));

  const options = {
    physics: {
      enabled: true,
      solver: "forceAtlas2Based",
      forceAtlas2Based: {
        gravitationalConstant: -50,
        centralGravity: 0.005,
        springLength: 110,
        springConstant: 0.09,
        damping: 0.4
      },
      stabilization: { iterations: 200, updateInterval: 20 }
    },
    interaction: {
      hover: true,
      tooltipDelay: 60,
      zoomView: true,
      dragView: true
    },
    edges: { selectionWidth: 2 },
    nodes: { font: { color: "#e6e6e6", size: 12 } }
  };

  // Bestehendes Netzwerk ersetzen
  if (container._network) {
    container._network.destroy();
  }

  const network = new vis.Network(
    container,
    { nodes: new vis.DataSet(visNodes), edges: new vis.DataSet(visEdges) },
    options
  );

  container._network = network;

  // Tooltip
  const tip = document.createElement("div");
  tip.style.position = "fixed";
  tip.style.pointerEvents = "none";
  tip.style.background = "rgba(15,17,21,0.95)";
  tip.style.color = "#e6e6e6";
  tip.style.border = "1px solid #273043";
  tip.style.borderRadius = "8px";
  tip.style.padding = "8px 10px";
  tip.style.fontSize = "12px";
  tip.style.zIndex = "9999";
  tip.style.display = "none";
  tip.style.maxWidth = "380px";
  tip.style.boxShadow = "0 6px 24px rgba(0,0,0,.3)";
  document.body.appendChild(tip);

  function showTip(x, y, html) {
    tip.innerHTML = html;
    tip.style.left = (x + 14) + "px";
    tip.style.top = (y + 12) + "px";
    tip.style.display = "block";
  }
  function hideTip() { tip.style.display = "none"; }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
    }[c]));
  }

  // Node Hover
  network.on("hoverNode", params => {
    const n = nodes.find(x => x.id === params.node);
    if (!n) return;
    let extra = "";
    if (n.type === "metasnap" && n.score != null) {
      extra = `<br>Score: ${(n.score).toFixed(3)}`;
    }
    if (n.type === "hypothesis" && n.text) {
      extra = `<br>Hypothese: ${escapeHtml(n.text)}`;
    }
    showTip(params.pointer.DOM.x, params.pointer.DOM.y,
      `<b>Knoten</b><br>ID: ${escapeHtml(n.id)}<br>Label: ${escapeHtml(n.label||"")}<br>Typ: ${escapeHtml(n.type||"")}${extra}`);
  });
  network.on("blurNode", hideTip);

  // Edge Hover
  network.on("hoverEdge", params => {
    const e = network.body.data.edges.get(params.edge);
    const raw = edges.find(x => x.source === e.from && x.target === e.to) || {};
    showTip(params.pointer.DOM.x, params.pointer.DOM.y,
      `<b>Kante</b><br>Quelle: ${escapeHtml(String(e.from))}<br>Ziel: ${escapeHtml(String(e.to))}<br>Gewicht: ${(Number(raw.weight)||0).toFixed(3)}`);
  });
  network.on("blurEdge", hideTip);

  // Tooltip bei Pan/Zoom verbergen
  ["dragStart","dragEnd","zoom"].forEach(ev => network.on(ev, hideTip));

  return network;
}
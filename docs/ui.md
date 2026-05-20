<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__ui.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

# ORÓMA – UI & Dashboard (konsolidiert)

Stand: 2025-12-25


UI-/Dashboard-bezogene Doku (zusammengeführt).

## Quellen (konsolidiert)

- `docs/ui_objectgraph.md`

---

<a id="docs_ui_objectgraph_md"></a>

## Quelle: `docs/ui_objectgraph.md`

ORÓMA – ObjectGraph UI

Datei: docs/ui_objectgraph.md
Modul: ui/objects_ui.py + ui/templates/objects.html
Version: v0.8
Stand: 2025-12-13
Autor: ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking

⸻

1. Überblick

Der ObjectGraph ist eine aggregierte Objekt-Ebene, die aus den SceneGraphs
aufgebaut wird. Er fasst Informationen aus vielen Episoden / SnapChains / MetaSnaps
zusammen und speichert sie in zwei Tabellen:
	•	object_nodes
	•	object_relations

Die ObjectGraph UI (Route: /objects) ist ein reiner Explorer für diese
Tabellen – ähnlich wie die Seiten Episodic, MetaSnaps oder SceneGraphs:
	•	Sie zeigt eine Stichprobe der letzten N Einträge (konfiguriert im Code).
	•	Sie bietet einfache Filter (z. B. kind=object).
	•	Sie reichert die Ansicht um Statistiken an (Top-Labels, Degree-Topliste).
	•	Sie kann einen Fokus-Knoten (focus_id) genauer anzeigen:
	•	Label, Meta-Daten
	•	kleines Ego-Netz (Nachbarn im aktuellen Sample)
	•	Degree und Relationstypen

Der Anspruch dieser UI ist nicht, den gesamten Graphen vollständig zu visualisieren,
sondern einen leichten Einstieg in die Datenstruktur zu geben:
	•	„Was hat die SceneGraph-Pipeline eigentlich alles schon für Objekte erzeugt?“
	•	„Wie viele Relationen gibt es, und welche Typen kommen vor?“
	•	„Welche Knoten sind besonders stark vernetzt?“

⸻

2. Datenbasis: Tabellen object_nodes und object_relations

Die UI greift auf zwei Tabellen in der oroma.db zu:

2.1 object_nodes
	•	Enthält alle Objekt-Knoten, die aus SceneGraphs / MetaSnaps abgeleitet wurden.
	•	Wichtige Spalten:
	•	id (INTEGER PRIMARY KEY)
	•	kind (TEXT)
	•	z. B. "object", "snapchain", "meta", "origin"
	•	label (TEXT)
	•	freier Text, z. B. "Chain 45915", "compressed_45915",
"scenegraph:vision_token:hoch", "vision/token", …
	•	meta_json (BLOB/TEXT)
	•	optionale zusätzliche Informationen als JSON (z. B. Mapping auf SceneGraph-ID).

Die genaue Semantik der Kinds:
	•	object – „eigentliche“ Objekte (z. B. abstrahierte Entities aus SceneGraphs)
	•	snapchain – SnapChain-Knoten, die von MetaSnaps / Origins referenziert werden
	•	meta – Meta-Knoten, z. B. beschreibende Knoten aus MetaSnaps
	•	origin – Wurzel / Ursprung, z. B. "vision/token", "scenegraph:..." etc.

Hinweis: Der ObjectGraph kann sich über viele Import-/Export-Zyklen aufbauen und
enthält daher sowohl ältere als auch frisch erzeugte Knoten.

2.2 object_relations
	•	Enthält gerichtete Relationen zwischen Knoten in object_nodes.
	•	Wichtige Spalten:
	•	id (INTEGER PRIMARY KEY)
	•	a_id (INTEGER)
	•	b_id (INTEGER)
	•	relation (TEXT)
	•	z. B. "origin", "describes", "meta_to_chain", "chain_to_origin", …
	•	confidence (REAL)
	•	optionaler Score (0–1), falls die Pipeline so etwas befüllt

Typische Relationen, die in den aktuellen Daten (Stand 2025-12-13) vorkommen:
	•	origin
	•	verbindet z. B. eine SnapChain mit ihrem Ursprung (origin-Knoten oder
globalem Szenen-Ursprung).
	•	describes
	•	verknüpft Meta-Knoten bzw. Objekt-Knoten, die eine SnapChain oder einen
originären Knoten beschreiben.
	•	meta_to_chain
	•	Kante von Meta-Knoten zu SnapChain-Knoten.
	•	chain_to_origin
	•	Kante von SnapChain-Knoten zum originären Knoten.

⸻

3. Route & Blueprint

Die UI wird über ein Flask-Blueprint eingebunden:
	•	Blueprint: ui/objects_ui.py
	•	Name: bp = Blueprint("objects", ...)
	•	Registriert in run_oroma.py etwa so:

from ui import objects_ui
app.register_blueprint(objects_ui.bp, url_prefix="/objects")

Wichtige Routen:
	•	GET /objects
	•	GET /objects/

Beide Routen werden vom Handler objects_index() bedient.

⸻

4. Kernlogik von objects_index()

Die Route objects_index() implementiert die gesamte Logik für:
	•	Filter (kind, focus_id)
	•	Stichprobenauswahl
	•	Statistiken
	•	Health-Status
	•	Fokus-Ego-Netz

4.1 Eingangsparameter (GET-Parameter)
	•	kind (optional)
	•	Wenn gesetzt, werden nur Nodes mit diesem Kind in die View-Stichprobe
aufgenommen (und gezählt).
	•	Mögliche Werte:
	•	"object", "snapchain", "meta", "origin".
	•	focus_id (optional, integer)
	•	Wenn gesetzt, wird ein bestimmter Knoten in den Fokus genommen:
	•	Fokus-Details (Label, meta_json)
	•	kleines Ego-Netz (Nachbarn im Sample)
	•	Hervorhebung in den Tabellen

Beispiel-URLs:
	•	/objects
	•	Alle Knoten, keine Filterung.
	•	/objects?kind=object
	•	Nur Knoten mit kind="object".
	•	/objects?kind=object&focus_id=410
	•	Fokus auf Objektknoten mit ID 410.

4.2 Stichproben-Limits

Der Code arbeitet mit einem Stichprobenlimit:

SAMPLE_LIMIT = 500

Das bedeutet:
	•	Maximal 500 object_nodes werden in der aktuellen View angezeigt.
	•	Maximal 500 object_relations werden aus der Tabelle geladen.

Die gesamt-Counts (total_nodes, total_relations) werden aber immer über
COUNT(*) bestimmt (d. h. sie beziehen sich auf die gesamte Tabelle bzw. den
Filter, nicht nur auf die Stichprobe).

4.3 DB-Queries für Nodes

Es gibt zwei Ebenen:
	1.	Ungefilterte Nodes-Stichprobe (für Mapping & Nachladen):

SELECT id, kind, label, meta_json
  FROM object_nodes
 ORDER BY id DESC
 LIMIT ?

	•	Wird immer geladen, unabhängig vom kind-Filter.
	•	Dient als Basis-Mapping nodes_by_id für die Relationen-Auflösung.

	2.	Gefilterte Nodes-View (für die eigentliche Ansicht + Top-Labels):
	•	Wenn kind gesetzt:

SELECT id, kind, label, meta_json
  FROM object_nodes
 WHERE kind = ?
 ORDER BY id DESC
 LIMIT ?

plus:

SELECT COUNT(*) AS c FROM object_nodes WHERE kind = ?

	•	Wenn kind nicht gesetzt:
	•	nodes_view = nodes_sample_all (ungefilterte Stichprobe).
	•	SELECT COUNT(*) AS c FROM object_nodes.

4.4 DB-Queries für Relationen

Die Relationen werden aktuell immer global (nicht nach kind) als Stichprobe
geladen:

SELECT id, a_id, b_id, relation, confidence
  FROM object_relations
 ORDER BY id DESC
 LIMIT ?

Zusätzlich wird die Gesamtanzahl abgefragt:

SELECT COUNT(*) AS c FROM object_relations

⸻

5. Mapping nodes_by_id und Nachladen fehlender Knoten

Damit die Relationen im UI sinnvoll angezeigt werden, braucht es ein Mapping:

nodes_by_id = {int(n["id"]): n for n in nodes_sample_all if "id" in n}

Danach werden aus der Relationen-Stichprobe alle IDs eingesammelt, die noch
nicht im Mapping enthalten sind:

missing_ids = set()
for r in relations:
    for key in ("a_id", "b_id"):
        node_id = r.get(key)
        if isinstance(node_id, int) and node_id not in nodes_by_id:
            missing_ids.add(node_id)

Diese fehlenden IDs werden anschließend in Chunks nachgeladen:

CHUNK_SIZE = 800
for i in range(0, len(missing_ids_list), CHUNK_SIZE):
    chunk = missing_ids_list[i : i + CHUNK_SIZE]
    placeholders = ",".join("?" for _ in chunk)
    sql = (
        "SELECT id, kind, label, meta_json "
        "FROM object_nodes WHERE id IN (" + placeholders + ")"
    )
    cur = conn.execute(sql, chunk)
    ...

Dadurch werden im UI:
	•	deutlich weniger "(unbekannt)"-Knoten angezeigt,
	•	die Degree-Berechnung realistischer, weil die Objekt-Knoten bekannt sind.

⸻

6. Statistiken

6.1 Verteilung nach kind

Basierend auf der aktuellen View (nodes) wird gezählt:

kinds_counter = Counter(n.get("kind") for n in nodes)

Im UI erscheint dies im Block:
	•	„Verteilung nach kind (im aktuellen View)“

Beispiel:
	•	object: 500
	•	snapchain: 0
	•	meta: 0
	•	origin: 0

(Je nachdem, welcher Filter aktiv ist.)

6.2 Top-Objekt-Labels

Die Top-Labels werden ab v0.7 aus der aktuellen View (nodes) berechnet,
nicht mehr aus der ungefilterten Stichprobe:

label_counter = Counter(
    (n.get("label") or "").strip()
    for n in nodes
    if n.get("kind") == "object"
)

	•	Leere Labels werden entfernt.
	•	Anschließend werden die Top 20 angezeigt.

Im UI:
	•	„Top Objekt-Labels (kind = “object”)“
	•	Tabelle mit Label und Anzahl.

6.3 Relationstypen

Die Relationstypen werden aus der Relationen-Stichprobe ermittelt:

rel_counter = Counter(r.get("relation") for r in relations)

Im UI:
	•	„Top Relationstypen (Stichprobe)“
	•	Liste von Relationstypen (origin, describes, meta_to_chain, …) mit Häufigkeit.

6.4 Degree-Statistik (Top vernetzte Knoten)

Für die Relationen-Stichprobe wird ein Degree pro Knoten berechnet:
	•	Jeder Auftritt als a_id oder b_id erhöht den Degree um 1.
	•	Zusätzlich wird gezählt, wie viele verschiedene Relationstypen pro Node existieren.

degree_counter = Counter()
reltypes_per_node = {}

for r in relations:
    rel_name = r.get("relation")
    a_id = r.get("a_id")
    b_id = r.get("b_id")

    for node_id in (a_id, b_id):
        if not isinstance(node_id, int):
            continue
        degree_counter[node_id] += 1
        if node_id not in reltypes_per_node:
            reltypes_per_node[node_id] = set()
        if rel_name:
            reltypes_per_node[node_id].add(rel_name)

Anschließend werden nur Nodes mit kind="object" berücksichtigt:

for node_id, deg in degree_counter.items():
    node = nodes_by_id.get(node_id)
    if not node:
        continue
    if node.get("kind") != "object":
        continue
    ...

Filter:
	•	Standard-Grenze: min_degree_for_top = 2
	•	d. h. Nodes mit degree < 2 werden nicht angezeigt.
	•	Sortierung:
	•	Absteigend nach Degree
	•	Bei Gleichstand: alphabetisch nach Label

Im UI:
	•	„Top vernetzte Knoten (Degree ≥ 2)“
	•	Tabelle mit:
	•	ID (verlinkt, setzt focus_id)
	•	Kind
	•	Label
	•	Degree

Beispiel (aus deinem aktuellen Snapshot):
	•	Node 410 (object, Label scenegraph:vision_token:hoch) mit Degree = 78
	•	Node 346 (object, Label vision/token) mit Degree = 78
	•	Viele weitere Chain XXXXX-Nodes mit Degree = 2

⸻

7. Fokus-Knoten & Ego-Netz

Wenn focus_id gesetzt ist und der Node in nodes_by_id gefunden wird,
zeigt die UI im unteren Bereich einen Fokus-Block:

7.1 Focus-Node Detail
	•	ID + Kind (mit Link auf sich selbst):
	•	Fokus-Knoten: ID 410 (object)
	•	Label:
	•	z. B. scenegraph:vision_token:hoch
	•	Optional: meta_json als Roh-JSON (scrollbar).

7.2 Ego-Netz (aktuelle Stichprobe)

In objects_ui.py wird zusätzlich eine Struktur focus_degree_info aufgebaut:
	•	node_id – ID des Fokus-Knotens
	•	degree – Degree des Nodes (auf Basis der aktuellen Relationen-Stichprobe)
	•	relation_types – Anzahl unterschiedlicher Relationstypen
	•	neighbors – Liste der Nachbarn (Knoten, die direkt mit dem Fokus verbunden sind)

Die Nachbarn werden so bestimmt:
	•	Alle Relationen aus der Stichprobe, in denen a_id == focus_id oder
b_id == focus_id vorkommt.
	•	Die jeweils „andere“ Seite (b_id oder a_id) wird als Nachbar aufgenommen.
	•	Die Nachbarn werden dedupliziert und mit ihren Labels (nodes_by_id) versehen.

Im Template wird das Ego-Netz so dargestellt:
	•	Degree + Relationstypen
	•	Tabelle „Nachbarn im aktuellen Sample“:
	•	ID (Link, setzt focus_id auf den Nachbarn)
	•	Kind
	•	Label

Beispiel (verkürzt):
	•	Fokus-Knoten: ID 410 (object), Label scenegraph:vision_token:hoch
	•	Degree: 78
	•	Relationstypen: 1 (describes)
	•	Nachbarn:
	•	869 – Chain 48961
	•	870 – Chain 48962
	•	871 – Chain 48963
	•	… (viele weitere Chains)

⸻

8. Health-Status (Selfcheck-Integration)

Die UI bindet den ObjectGraph-Selfcheck ein, um einen Health-Badge
anzuzeigen:

8.1 Selfcheck-Aufruf

Es wird ein CLI-Skript aufgerufen:

PYTHONPATH=/opt/ai/oroma \
  python3 tools/objectgraph_selfcheck.py \
    --db-path /opt/ai/oroma/data/oroma.db \
    --namespace-prefix object:auto: \
    --json-only

	•	Rückgabe: JSON mit Health-Informationen.
	•	Der Code führt das Skript in einem Subprozess aus und cached das Ergebnis
für einige Sekunden (_OBJ_HEALTH_TTL_SECONDS).

8.2 Normalisierung der Health-Daten

Die Funktion _normalize_health() sorgt dafür, dass auch ältere Versionen
des Selfchecks (ohne expliziten health-Block) ein standardisiertes Format
bekommen:
	•	overall_status – "ok", "warning", "error" oder "unknown"
	•	warnings – Liste von Warnhinweisen
	•	errors – Liste von Fehlern

Fehlt overall_status, wird ersatzweise die Integrität der Relationen geprüft:
	•	missing_a, missing_b – Anzahl fehlender FK-Referenzen
	•	Wenn > 0 → overall_status = "warning"

8.3 Anzeige im UI

Im Headline-Bereich der Seite wird ein Badge angezeigt:
	•	„ObjectGraph OK“ (grün), wenn overall_status == "ok"
	•	„ObjectGraph Warnung“ (gelb), wenn overall_status == "warning"
	•	„ObjectGraph Fehler“ (rot), wenn overall_status == "error"
	•	„ObjectGraph Status unbekannt“ (grau), wenn nichts bekannt ist.

Darunter eine kleine Zusammenfassung:
	•	Warnings: X | Errors: Y

So siehst du im Dashboard auf einen Blick, ob der Selfcheck irgendwelche
Probleme gemeldet hat (z. B. fehlende Knoten, inkonsistente Relationen).

⸻

9. Legende & Interpretation

9.1 Was bedeuten die wichtigsten Felder?
	•	Kind (kind)
	•	object – eigentliche Objekte / Entities.
	•	snapchain – SnapChain-Knoten (komplette Episode / Kette).
	•	meta – Meta-Objekte (z. B. beschreibende Knoten aus MetaSnaps).
	•	origin – Ursprung / Quelle (z. B. "vision/token", "scenegraph:...").
	•	Label (label)
	•	Menschlich lesbarer Name des Objekts.
	•	Typische Muster:
	•	Chain 45915, Chain 47057, …
	•	compressed_45915, compressed_47057, …
	•	scenegraph:vision_token:hoch
	•	vision/token
	•	Das Label alleine ist nicht garantiert einzigartig – die ID ist der
eindeutige Schlüssel.
	•	Relation (relation)
	•	Typ der Kante zwischen a_id und b_id.
	•	Beispiele (aktuelle Daten):
	•	origin – „Kette X hat Ursprung Y“
	•	describes – „Knoten X beschreibt Knoten Y“
	•	meta_to_chain – „Meta-Knoten X gehört zur SnapChain Y“
	•	chain_to_origin – „SnapChain X gehört zu Origin Y“
	•	Degree
	•	Anzahl der Relationen, in denen ein Knoten vorkommt (als a_id oder b_id).
	•	Hoher Degree → stark vernetzt, wichtiger Knoten.
	•	Relationstypen (im Fokus-Ego-Netz)
	•	Anzahl der verschiedenen relation-Werte, die beim Fokus-Knoten auftreten.
	•	Z. B.:
	•	1 → nur describes
	•	2 → describes + origin

9.2 Typische Beobachtungen
	•	Knoten wie scenegraph:vision_token:hoch (ID 410) haben einen sehr hohen
Degree (z. B. 78) – sie sind zentrale Hubs, die viele SnapChains oder
Objektknoten verbinden.
	•	origin-Knoten wie vision/token (ID 346) sind ebenfalls stark vernetzt:
	•	Sie sind Startpunkte für viele SnapChains.
	•	Die vielen Chain XXXXX-Knoten haben oft Degree 2:
	•	Eine origin-Kante (origin)
	•	Eine beschreibende Kante (describes) zu einem Meta-/Objektknoten.

⸻

10. Nutzung im Alltag

Wie kannst du die ObjectGraph UI praktisch verwenden?
	•	1. Sanity-Check nach größeren Import-/Exportläufen
	•	origin & describes sollten in plausibler Anzahl vorhanden sein.
	•	Degree-Tabelle checken: Gibt es wie erwartet zentrale Hubs?
	•	2. Debugging von SceneGraph- oder MetaSnap-Problemen
	•	Wenn in Meta-/SceneGraph-UI etwas „komisch“ aussieht, kannst du im
ObjectGraph querprüfen:
	•	Gibt es die entsprechenden ObjectNodes?
	•	Wie sind sie verknüpft?
	•	3. Erkundung neuer Features
	•	Wenn du neue Relationstypen einführst (z. B. near, contains, part_of),
werden sie hier sichtbar:
	•	In Top Relationstypen
	•	In den Degree-Statistiken (z. B. mehr Relationen pro Objekt)
	•	4. Selektive Inspektion per Fokus-ID
	•	Du kannst gezielt einzelne Knoten ansteuern:
	•	/objects?focus_id=410
	•	/objects?kind=object&focus_id=346
	•	Dann siehst du Label + Meta und das Ego-Netz dieses Knotens.

⸻

11. Erweiterungsideen (Future Work)

Die aktuelle ObjectGraph UI ist bewusst leichtgewichtig gehalten. Mögliche
Erweiterungen:
	1.	Zeitliche Filter
	•	Relationen oder Nodes, die in bestimmten Zeitfenstern entstanden sind
(created_ts / updated_ts, falls vorhanden).
	2.	Relationen nach Typ filtern
	•	Zusätzlicher Filterparameter relation, um nur bestimmte Kanten
anzuzeigen (z. B. nur origin).
	3.	Paginierung / „Mehr laden“
	•	Schrittweise Erweiterung der Stichprobe über das UI
(z. B. Buttons: „+500 Nodes laden“).
	4.	Graph-Visualisierung
	•	Kleine, interaktive Graph-Ansicht (z. B. mit D3.js) für Fokus-Ego-Netz.
	5.	Export-Funktion
	•	Export des aktuellen Samples (Nodes + Relations) als JSON oder CSV
für externe Analysen.

⸻

12. Troubleshooting

12.1 Viele „(unbekannt)“-Einträge bei Relationen

Ursache:
	•	nodes_by_id kennt einen Knoten nicht (z. B. weil er außerhalb des
Stichprobenfensters liegt und auch im Nachlade-Schritt nicht gefunden wurde).

Maßnahmen:
	•	Prüfen, ob die DB-Tabellen konsistent sind:
	•	Selfcheck ausführen:

PYTHONPATH=/opt/ai/oroma \
  python3 tools/objectgraph_selfcheck.py \
    --db-path /opt/ai/oroma/data/oroma.db \
    --namespace-prefix object:auto: \
    --json-only > /tmp/objectgraph_report.json

	•	Falls im Selfcheck viele missing_a / missing_b auftauchen:
	•	Ursache in der SceneGraph-/ObjectGraph-Pipeline suchen.

12.2 „ObjectGraph Fehler“ im Health-Badge

Mögliche Ursachen:
	•	Selfcheck-Skript hat Fehler geworfen (Exit-Code != 0).
	•	Selfcheck-JSON konnte nicht geparst werden.
	•	Selfcheck meldet selbst overall_status = "error".

Vorgehen:
	1.	Selfcheck manuell aufrufen (s. o.).
	2.	Output-JSON in Ruhe anschauen:
	•	Welche Checks schlagen fehl?
	•	Welche Tabellen / Relationen sind betroffen?

12.3 Keine Daten / leere Tabellen

Wenn keine Nodes oder Relationen angezeigt werden:
	•	Prüfen, ob die ObjectGraph-Pipeline bereits gelaufen ist:
	•	Werden SceneGraphs verarbeitet und in ObjectNodes/-Relations überführt?
	•	In der DB direkt nachsehen:

SELECT COUNT(*) FROM object_nodes;
SELECT COUNT(*) FROM object_relations;


⸻

13. Zusammenfassung

Die ObjectGraph UI ist ein diagnostisches Werkzeug, das dir einen
strukturierten Blick auf die aggregierten Objekt- und Relationsdaten von ORÓMA
gibt:
	•	Nodes: Was für Objekte (inkl. SnapChains, Meta, Origin) existieren?
	•	Relations: Wie hängen diese Objekte zusammen?
	•	Statistik: Welche Objekte sind besonders zentral?
	•	Fokus: Was ist das lokale Ego-Netz eines bestimmten Knotens?
	•	Health: Ist der Graph intern konsistent (Selfcheck)?

Sie ergänzt damit die anderen UIs (Episodic, MetaSnaps, SceneGraphs) und
hilft dir, die „Meta-Struktur“ der Welt, die ORÓMA gesehen hat, besser zu
verstehen – ohne dass du direkt in SQL oder Roh-JSON einsteigen musst.

Wenn du später weitere Relationstypen oder Objektkategorien einführst, solltest
du diese Doku bei Bedarf um eine kurze Beschreibung ergänzen – dann bleibt die
ObjectGraph-Welt für dich (und zukünftige Leser) gut interpretierbar.

⸻


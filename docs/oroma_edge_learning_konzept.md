# ORÓMA – Konzept: Vervollständigung der Lernkette für Kantenwahrnehmung und Visualisierung

**Projekt:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Kontext:** Vision / Video / Learning / SceneGraph / Dream  
**Stand der Konzeptbasis:** abgeleitet aus aktueller Projekt-ZIP `oroma_20260405_062620_with_db.zip`  
**Ziel dieses Dokuments:** Festhalten, **was als Nächstes produktiv umgesetzt werden soll**, um die ursprünglich vorgesehene visuelle Lernkette für Kanten wieder vollständig und sichtbar zu machen.

---

## 1. Ausgangslage

Im aktuellen ORÓMA-Stand ist das Konzept für primitive visuelle Merkmale bereits in mehreren Kernmodulen angelegt:

- `motion`
- `edges`
- `color`
- kompakter `cam_token` / Vision-Vektor (aktuell 84D)

Die vorhandenen Core-Dateien und Dokumente zeigen klar, dass ORÓMA **nicht nur mit abstrakten Vision-Vektoren**, sondern ursprünglich mit **primitiven Wahrnehmungsmerkmalen** arbeiten sollte.

### Bereits vorgesehen in der Architektur

- `hooks_av_snaptoken.py` erwartet explizit `motion`, `edges`, `color`
- `vision_arbiter.py` nutzt `edges` direkt in der Featurebewertung / Heuristik
- `sql_manager.py` kann `motion`, `edges`, `color` bereits im Cam-/Vision-Token persistieren
- `episodic_writer.py` ist ebenfalls auf diese Felder vorbereitet
- `overlay.py` berechnet bereits ein strukturelles Kantenmaß (`edge_mean`)

### Problem im Ist-Zustand

Obwohl die Architektur darauf vorbereitet ist, zeigen die bisherigen Datenstände:

- Vision-/Cam-Tokens werden erzeugt
- der 84D-Vektor wird gespeichert
- **explizite Edge-Werte sind in realen DB-Daten bisher nicht zuverlässig befüllt**
- im Video-Tab fehlt eine verlässliche, sichtbare Debug-Visualisierung der Kanten

Damit ist die Lernkette aktuell **nur teilweise geschlossen**.

---

## 2. Fachliches Ziel

Das Ziel ist **nicht nur ein Video-Overlay**, sondern die **Vervollständigung der visuellen Lernkette**.

ORÓMA soll Kanten **ähnlich einer frühen menschlichen Wahrnehmungsschicht** behandeln:

1. **primitive Wahrnehmung** von Struktur, Kontrast, Orientierung, Bewegung
2. **Verdichtung** in Tokens / Embeddings
3. **Persistenz** in Gedächtnis und Historie
4. **Visualisierung** zur Kontrolle und Debugbarkeit
5. **Weiterverarbeitung** in SceneGraph, Objektbildung und Dream-/Replay-Mechanismen

Kurz gesagt:

> Kanten sollen nicht nur sichtbar sein, sondern als expliziter Bestandteil der ORÓMA-Lernkette wieder ernsthaft genutzt werden.

---

## 3. Zielbild der Lernkette

Geplante Zielkette:

```text
Kameraframe
  -> primitive visuelle Merkmale (motion / edges / color / Struktur)
  -> CamToken / VisionToken (84D-Vektor + primitive Features)
  -> Persistenz in DB / SnapChain / Episodic / Metrics
  -> Visualisierung im Video-Tab und Debug-UI
  -> spätere Verdichtung in SceneGraph / Objektbildung / Meta-Snaps
  -> Reuse / Replay / Dream / Transferwissen
```

---

## 4. Konzept: Rollenverteilung der Daten

### 4.1 Primitive visuelle Merkmale

Diese Ebene soll **explizit und interpretierbar** sein.

Vorgesehene Werte:

- `motion`
- `edges`
- `color`
- optionale abgeleitete Edge-Metriken:
  - `edge_density`
  - `edge_mean`
  - `edge_center_density`
  - `edge_h_strength`
  - `edge_v_strength`
  - optional kleines `edge_grid`

**Rolle:**
- direkt verständlich
- gut für UI / Debug / Historie
- bildet die primitive Wahrnehmungsschicht ab

### 4.2 84D-Vektor

Der bestehende 84D-Vektor soll **nicht ersetzt**, sondern weiter genutzt werden.

**Rolle des Vektors:**
- kompakte Szenensignatur
- Ähnlichkeit / Clustering
- Wiedererkennung ähnlicher Situationen
- Transferwissen
- abstrakter als rohe Edge-Werte

### 4.3 Zusammenspiel

Die beiden Ebenen sollen gemeinsam genutzt werden:

- **Primitive Features** = explizite Wahrnehmung
- **84D-Vektor** = verdichtete Repräsentation

Das bedeutet:

- Kanten werden **neu explizit berechnet und gespeichert**
- der Vektor bleibt als **kompakte Signatur** erhalten
- spätere Entscheidungen und Lernschritte können auf **beidem** aufbauen

---

## 5. Ziel für die UI

### 5.1 Video-Tab: Edge-Debug sichtbar schaltbar

Wenn der Modus freigeschaltet ist, soll ORÓMA im Video-Tab direkt zeigen:

- Edge-Overlay oder Edge-Maske
- klaren Status, dass der Edge-Debug aktiv ist
- primitive Live-Werte, z. B.:
  - `edges`
  - `motion`
  - `color`
  - `q`
  - optional horizontale/vertikale Dominanz

**Ziel:**
- direkte Kontrolle, ob ORÓMA Kanten in der aktuellen Szene wahrnimmt
- Debugbarkeit des Vision-Pfads
- transparente Sicht auf die frühe Wahrnehmungsschicht

### 5.2 Learning-/Vision-Debug

Nicht nur Livebild, sondern auch zeitlicher Verlauf:

- Verlauf von `edges`
- Kantenarm / kantenreich im Zeitfenster
- Zusammenhang zu:
  - `accepted`
  - `candidate`
  - `q`
  - `vision/token`
  - SceneGraph-Bildung

**Ziel:**
- Kanten nicht nur sehen, sondern ihr Lernen und ihre Nutzung nachvollziehen

---

## 6. Ziel für die Persistenz / DB

### 6.1 Was gespeichert werden soll

Minimal und produktiv:

- bestehender Vision-/Cam-Token-Vektor (84D)
- primitive Felder:
  - `motion`
  - `edges`
  - `color`

Optional, wenn sinnvoll und leichtgewichtig:

- `edge_density`
- `edge_center_density`
- `edge_h_strength`
- `edge_v_strength`

### 6.2 Was bewusst **nicht** gespeichert werden soll

Nicht standardmäßig in SQLite:

- komplette Edge-Bilder
- große Debug-Frames
- schwere Rohmasken pro Frame

**Begründung:**
- unnötige DB-Aufblähung
- hoher IO-/Speicherbedarf
- für das Lernen oft nicht nötig

Wenn später überhaupt nötig, dann eher:

- kleines komprimiertes Edge-Grid
- oder Snapshot-Debug nur optional / on-demand

---

## 7. Fachliche Lernidee: „wie beim Menschen“

Die Grundidee ist biologisch inspiriert:

### Frühe visuelle Stufe

ORÓMA soll einfache Strukturreize erfassen:

- Kontrast
- Kanten
- Orientierungen
- Bewegung
- Farbunterschiede

### Spätere Stufe

Darauf aufbauend lernt ORÓMA wiederkehrende Muster wie:

- glatte vs. strukturreiche Szenen
- vertikale/waagerechte Muster
- statische Struktur vs. bewegte Kanten
- Szenenähnlichkeiten über Token-Vektoren
- spätere Objekt-/SceneGraph-Bildung

Die Kantenwerte sind also **nicht das Endziel**, sondern eine **primitive Wahrnehmungsbasis**, auf der höhere Repräsentationen entstehen.

---

## 8. Konkrete Umsetzungsphasen

### Phase 1 – Producer-Pfad vervollständigen

Ziel:
- sicherstellen, dass der echte Vision-/Embed-/Wrapper-Pfad `motion`, `edges`, `color` im Live-Betrieb wirklich liefert

Erwartetes Ergebnis:
- die bereits vorhandenen Core-Module bekommen endlich reale primitive Vision-Features

### Phase 2 – Persistenz schließen

Ziel:
- primitive Features landen zuverlässig in DB / SnapChain / Episodic / Logging

Erwartetes Ergebnis:
- spätere historische Analyse und Lernkurven möglich

### Phase 3 – Video-UI visualisieren

Ziel:
- Edge-Debug im Video-Tab sauber sichtbar machen
- klarer Ein/Aus-Zustand
- sichtbarer Diagnosepfad statt stiller Fallbacks

Erwartetes Ergebnis:
- direkte visuelle Kontrolle der Wahrnehmungsschicht

### Phase 4 – Learning-/Debug-Auswertung

Ziel:
- zeitliche und fachliche Auswertung der Kantenwerte
- Zusammenhang zu Token-Qualität und Vision-Entscheidungen sichtbar machen

Erwartetes Ergebnis:
- ORÓMA lernt nicht nur implizit, sondern nachvollziehbar

### Phase 5 – Verdichtung / Dream / SceneGraph

Ziel:
- primitive Features und Vektorsignatur gemeinsam in höhere Gedächtnisbildung überführen

Erwartetes Ergebnis:
- wiederkehrende Strukturmuster werden nicht nur gesehen, sondern in der ORÓMA-Gedächtniskette nutzbar

---

## 9. Entscheidung zur Rolle des 84D-Vektors

Wichtige Festlegung:

> Der 84D-Vektor bleibt erhalten und wird **nicht** durch explizite Edge-Werte ersetzt.

Begründung:

- Der Vektor ist die kompakte, abstrakte Signatur
- explizite Edge-Werte sind die interpretierbare primitive Wahrnehmung
- beide zusammen sind stärker als nur eine der beiden Ebenen

Praktische Rolle des Vektors im neuen Konzept:

- Wiedererkennung ähnlicher Szenen
- Clustering / Ähnlichkeit
- Übergang zu Gedächtnis, Replay, Dream, Transfer
- Ergänzung zu expliziten Kantenwerten

---

## 10. Leitplanken für die Umsetzung

Bei der Umsetzung gelten folgende Leitlinien:

- minimal-invasiv auf bestehender ORÓMA-Architektur aufbauen
- keine unnötige DB-Aufblähung
- keine stillen Fehler
- Edge-Debug in der UI muss klar sichtbar sein
- primitive Features und Vektorsignatur werden gemeinsam genutzt
- Ziel ist nicht nur Debug, sondern **Vervollständigung der Lernkette**

---

## 11. Konkretes Vorhaben ab jetzt

Ab jetzt ist das Vorhaben:

1. den ursprünglichen Edge-/Motion-/Color-Pfad in der realen Producer-Kette wieder vollständig aktivieren
2. die primitive visuelle Wahrnehmung explizit und zuverlässig persistieren
3. die Kanten im Video-Tab sichtbar machen, wenn freigeschaltet
4. die historische und fachliche Auswertung im Learning-/Vision-Bereich ergänzen
5. den 84D-Vektor als verdichtete Signatur parallel weiter nutzen
6. so die ORÓMA-Lernkette von **Wahrnehmung → Token → Gedächtnis → Visualisierung → Verdichtung** schließen

---

## 12. Kurzfassung

ORÓMA soll Kanten nicht nur anzeigen, sondern **als primitive visuelle Wahrnehmung lernen und weiterverarbeiten**.

Dafür werden:

- primitive Features (`motion`, `edges`, `color`) explizit vervollständigt,
- im bestehenden Token-/DB-/Learning-Pfad genutzt,
- im Video-Tab sichtbar gemacht,
- und zusammen mit dem bestehenden 84D-Vektor in die höhere Gedächtnis- und Lernarchitektur eingebunden.

Damit wird die ursprünglich angelegte visuelle Lernkette fachlich sauber vervollständigt.

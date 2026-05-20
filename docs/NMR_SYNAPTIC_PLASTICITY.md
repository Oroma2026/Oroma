# ORÓMA – NMR Synaptische Plastizität (Relationstyp: synaptic)

**Projekt:** ORÓMA v3.7.3+ (Edge / Raspberry Pi)  
**Stand:** 2026-03-04  
**Gültig für ZIP:** oroma_20260304_164958_with_db.zip (und kompatible Folgestände)

---

## Zweck

ORÓMA erzeugt bereits implizite Verknüpfungen (Episoden/Events, SnapChains, SceneGraph/ObjectGraph).  
Die Synapses-UI zeigte diese Verknüpfungen anfangs **nur als Laufzeit-Graph** (on-the-fly).

Mit der NMR-Synaptik wird daraus ein **persistentes Assoziationssubstrat**:

- Synapsen werden als **Relationstyp `synaptic`** in der bestehenden Graph-DB gespeichert.
- Die Kanten werden **im Dream** (Konsolidierung) verstärkt/aktualisiert (Hebb + Decay-Logik in notes).
- UI und spätere Reasoner-Komponenten können diese Kanten **direkt** nutzen (Soft-Evidence / Retrieval).

Wichtig: Das ist **kein Performance-Tuning**. Es ist eine strukturelle Gedächtnisschicht.

---

## Speicherort / Datenmodell

### Tabellen

Synapsen werden **nicht** in einer separaten `synaptic_links` Tabelle gespeichert, sondern im bestehenden ObjectGraph-Backbone:

- `object_nodes`
- `object_relations`

### Relationstyp

- `object_relations.relation = 'synaptic'`
- `object_relations.confidence = w` (sichtbarer Weight, normiert **0..1**)

**Eindeutigkeit der Kante:**

Damit synaptische Updates nicht zu Duplikaten führen, ist ein Unique-Index erforderlich:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_object_relations_unique_triplet
ON object_relations(a_id, relation, b_id);
```

> Ohne diesen Index können bei Parallelität (Threads/Prozesse) Duplikate entstehen, auch wenn Helper vor dem INSERT einen SELECT machen.

### Metadaten in `notes` (JSON)

Synapsen-spezifische Langzeit-/Diagnosewerte liegen in `object_relations.notes` als JSON (Beispiele):

- `hebb`: Langzeitpotenzial (unbounded, ≥0)
- `cooc`: akkumulierte Ko-Okkurrenz
- `sim`: reserved (derzeit 0.0 im MVP)
- `react_count`: wie oft die Kante reaktiviert/aktualisiert wurde
- `first_ts`, `last_ts`: unix timestamps
- `half_life_sec`: Halbwertszeit für Decay (derzeit dokumentiert, Decay kann später aktiviert werden)
- `scope`: optionaler Namespace/Scope zur Domain-Trennung (z.B. episode.source / episode.kind)

---

## Dream-Integration (Variante A)

Synapsen werden **nur im Dream** geschrieben (kein Live-Pfad).

### Dream-Phase

`DreamPhase: nmr_synapses`

Diese Phase läuft nach `forgetting` und vor (z.B.) `scenegraph`/`objectgraph`/`objectextractor` (je nach Dream-Plan).

### Umgebungsvariablen (ENV)

Die Dream-Phase kann über folgende ENV-Flags parametrisiert werden:

| ENV | Default | Bedeutung |
|---|---:|---|
| `OROMA_NMR_SYN_ENABLE` | `1` | Aktiviert/Deaktiviert die Phase |
| `OROMA_NMR_SYN_MAX_EPISODES_PER_RUN` | `500` | Max. Episoden pro Dream-Run |
| `OROMA_NMR_SYN_EVENTS_PER_EP` | `25` | Max. Events pro Episode (Cap) |
| `OROMA_NMR_SYN_WINDOW` | `3` | Ko-Okkurrenz-Fenster (i→i+1..i+window) |
| `OROMA_NMR_SYN_LR` | `0.05` | Lernrate für Hebb-Inkrement |
| `OROMA_NMR_SYN_HALF_LIFE_SEC` | `2592000` | Halbwertszeit (30 Tage) |
| `OROMA_NMR_SYN_MIN_EP_TS_GAP_SEC` | `0` | Optionaler Gap-Filter für Episoden |

### MVP-Semantik (heute)

Im MVP werden synaptische Kanten aus **Ko-Okkurrenz** erzeugt:

- pro Episode werden Events (gecappt) chronologisch sortiert
- pro Event werden Kanten innerhalb eines Fensters verbunden
- dadurch entstehen **Kreuzverbindungen** (kein reines “Ketten”-Replay)

Similarity Top-K (Cosine) ist als späterer Ausbau vorgesehen.

---

## UI / Synapses-Ansicht

Die Seite `/synapses/` sollte bevorzugt die **persistenten** Synapsen anzeigen.

**SQL-Prüfung (persistente Kanten):**

```sql
SELECT COUNT(*) FROM object_relations WHERE relation='synaptic';
```

Wenn der Count 0 ist, ist entweder:
- Dream-Phase deaktiviert (`OROMA_NMR_SYN_ENABLE=0`)
- Dream-Phase nicht gelaufen
- Modul/Import fehlgeschlagen (Log: „Modul nicht verfügbar“)
- Unique-Index/DB-Locks verhindern Inserts (siehe Troubleshooting)

---

## Monitoring / Sanity-Checks

### 1) Kantenanzahl

```sql
SELECT COUNT(*) AS syn_edges
FROM object_relations
WHERE relation='synaptic';
```

### 2) Gewicht-Verteilung (confidence)

```sql
SELECT
  printf('%.3f', MIN(confidence)) AS min_w,
  printf('%.3f', AVG(confidence)) AS avg_w,
  printf('%.3f', MAX(confidence)) AS max_w
FROM object_relations
WHERE relation='synaptic';
```

Interpretation:
- Niedrige Werte sind in frühen Runs normal (erst wenige Hebb-Updates).
- Sichtbare Cluster entstehen über Tage/Wochen oder durch höhere `LR`/größeres Fenster/Similarity.

### 3) Top-Hubs (out-degree)

```sql
SELECT a_id, COUNT(*) AS deg
FROM object_relations
WHERE relation='synaptic'
GROUP BY a_id
ORDER BY deg DESC
LIMIT 20;
```

Wenn `deg` ungefähr dem Fenster entspricht (z.B. 3), ist das ein Indikator, dass aktuell primär window-basierte Ko-Okkurrenz aktiv ist.

### 4) Unique-Index vorhanden?

```sql
PRAGMA index_list('object_relations');
```

Erwartung:
- `idx_object_relations_unique_triplet` mit `unique=1`

---

## Troubleshooting

### A) `database is locked`
Symptom (typisch in Dream-Phasen wie ObjectExtractor):
- `sqlite3.OperationalError: database is locked`

Ursachen:
- parallele Writer in verschiedenen Prozessen (Orchestrator, Service, Dream)
- lange Transactions
- zu aggressives paralleles Schreiben

Gegenmaßnahmen:
- Busy-timeout hoch (Default 60s empfohlen)
- möglichst kurze Write-Transaktionen (Rechnung außerhalb, DB-Write nur zum Persistieren)
- Writer-Lock konsequent nutzen (prozessübergreifend + thread-sicher)

### B) Unique-Index lässt sich nicht anlegen (Duplikate)
Wenn beim Start/Migration geloggt wird:
- `sql_manager.object_rel.unique.fail`

Dann existieren bereits Duplikate in `object_relations` für `(a_id,relation,b_id)` und der Unique-Index kann nicht erstellt werden.

Lösung:
- Dedupe durchführen (gezielt, mit Backup), z.B. „nur neueste ts behalten“.

### C) `nmr_synapses` wird übersprungen
Symptom:
- `NMR synapses: Modul nicht verfügbar – Phase übersprungen.`

Ursachen:
- ImportError / Modul nicht im Build
- falsche Imports/Paths

Lösung:
- Logs prüfen, Importpfade korrigieren
- Dream-Start als Modul `python3 -m core.dream_worker ...` bevorzugen oder Root ins sys.path aufnehmen (falls Direktaufruf).

---

## Roadmap (optional)

1) Similarity Top-K (Cosine) zwischen Events (keine O(n²) Explosion)
2) Reactivation-Boost aus Replay/Dream (gezielt)
3) Decay-Sweep aktivieren (Hebb/Weight über Zeit abklingen lassen)
4) Regel-Kandidaten aus stabilen Synapsen (gated, outcome-validiert, scope-aware)


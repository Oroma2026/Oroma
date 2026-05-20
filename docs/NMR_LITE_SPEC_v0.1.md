# ORÓMA – NMR-Lite Spec v0.1 (Predictive Coding + Fixed-Latent + Co-Occurrence Binding)
**Pfad (Vorschlag):** `/opt/ai/oroma/docs/NMR_LITE_SPEC_v0.1.md`  
**Projekt:** ORÓMA – Headless Lern-KI (Edge)  
**Stand:** 2026-01-30  
**Autor:** Jörg + GPT-5.2 Thinking  
**Baseline-ZIP:** `oroma_20260126_204948_with_db_milestone.zip` (Arbeitsbasis)  

---

## 1. Zielbild (warum NMR-Lite)
NMR-Lite liefert den **Kernnutzen von NMR** (multimodaler Zustandsraum, Prediction Error als Lern-/Neugier-Signal, Binding über Zeitkoinzidenz, Retrieval über Ähnlichkeit), **ohne**:
- kontrastives Training (InfoNCE),
- große Latent-Modelle,
- hohe GPU/CPU-Last.

Die Hardware-Randbedingungen (Pi 5, headless, 24/7-Stabilität) stehen im Vordergrund: deterministisch, billig, robust, DB-safe.

---

## 2. Prinzip: Fixed-Latent statt trainiertem Latent
### 2.1 Observation Vector (O)
ORÓMA besitzt bereits nutzbare Roh-/Hand-Features aus Vision/Audio/Sensorik. NMR-Lite bündelt diese zu einem Observation-Vektor `O(t)`.

**Quelle (bestehende Orte im Repo):**
- Vision: Feature-Aggregationen / Motion etc. (z. B. `core/vision_arbiter.py`, DeviceHub/Vision-Pfad)
- Audio: RMS/Proxy-Frequenz/FFT-Bündel (z. B. `core/overlay.py`, Audio-Hook/ASR-Pfad)
- Sensorik: Light/IR/Phase-Infos (z. B. Circadian/DeviceHub)

> Wichtig: NMR-Lite fordert **keine neuen Sensoren**. Wenn Light-Delta fehlt, läuft es nur mit Vision+Audio.

### 2.2 Random Projection / Hash-Embedding
Anstatt ein Embedding zu trainieren, wird `O(t)` über eine **fixe** Projektion in `Z(t)` überführt:

- `Z(t) = normalize( RP * O(t) )`
- `RP` ist eine deterministische Random-Projection-Matrix (seeded).
- `d_latent` klein: **32 / 48 / 64** (Pi-freundlich).

Optional:
- **int8-Quantisierung** für Logging/DB (Speicher/IO-Vorteil).
- **Binary Code** `B(t) = sign(Z(t))` (SimHash-ähnlich) für extrem schnelles Retrieval/Binding.

**Warum das reicht:**  
Ähnlichkeit (cosine / Hamming) funktioniert auch ohne Training – “good enough” für Clustering, Wiedererkennung, Habituation, PE-Trigger.

---

## 3. Prediction Error (PE) als Neugier-Signal ohne World-Model
NMR-Lite nutzt **Predictive Coding** über simple Statistik statt Modelltraining:

### 3.1 EMA-State pro Latent-Dimension
Für jedes `Z_i(t)` wird gehalten:
- `μ_i` (EMA-Mittelwert),
- `σ_i` (EMA-Streuung oder Varianz),
- optional `v_i` (Trend/Velocity, EMA der Differenz).

**Vorhersage:**
- `Ẑ_i(t) = μ_i` (oder `μ_i + v_i`)

**Prediction Error:**
- `PE(t) = mean_i( |Z_i(t) - Ẑ_i(t)| / (σ_i + ε) )`

### 3.2 Trigger-Logik (praktisch)
- `PE(t)` hoch → “Überraschung” / Neuheit / mögliche Handlung.
- `PE(t)` niedrig → Habituation (nichts tun, normal loggen).

**Wichtig:**  
PE ist hier **nicht** “Loss”, sondern ein *operatives Signal* für Neugier/Exploration/Repriorisierung.

---

## 4. Binding (Cross-Modal) ohne Contrastive Training
Binding entsteht über **Zeitkoinzidenz** und Wiederholung, nicht über gelernte Alignment-Modelle.

### 4.1 Co-Occurrence Window
Für ein Zeitfenster `W` (z. B. 2–5 Sekunden):
- wenn Vision-Code `B_v(t)` und Audio-Code `B_a(t)` im selben Fenster auftreten,
  → Edge-Weight zwischen Codes/Nodes erhöhen.

### 4.2 Repräsentation als Graph-Edges
Binding wird als Kanten im bestehenden Graph/Relation-Konzept abgebildet:
- `code:V:<hash>` —[co_occurs]→ `code:A:<hash>`
- Gewicht wächst mit Häufigkeit, fällt per Decay.

**Ergebnis:**  
“Multimodale Assoziationen” entstehen label-free und edge-tauglich.

---

## 5. Dream-Phase: Konsolidierung statt Training
Dream ist der Ort für billige Verdichtung, nicht für Modelltraining:

### 5.1 Konsolidierungsjobs
- Clustering/Online-Centroids für `Z(t)` oder `B(t)` (geringe K, inkrementell).
- Pruning: seltene Codes/Edges abwerten, häufige stabilisieren.
- Meta-States: starke Muster als “Meta” verdichten (analog zu MetaSnaps).

### 5.2 Zielmetriken
- mehr Wiedererkennung (Ähnlichkeit),
- weniger “Rauschen” in Codes,
- besserer PE-Threshold durch stabilere `σ`.

---

## 6. Datenhaltung / Telemetrie (minimal, produktiv)
NMR-Lite soll in ORÓMA sichtbar werden, ohne neue Komplexität.

### 6.1 Logging-Strategie
**Primär:** als `metrics`-Serie (für Learning-Sampler/Stats-DB)  
Vorschlag:
- `metric:nmr:pe` (float)
- `metric:nmr:novelty` (float, optional; aus “seen_count”/Code-Frequenz)
- `metric:nmr:code_hamming` oder `metric:nmr:sim` (optional)
- `metric:nmr:bind_hits` (optional)

**Sekundär:** eigene kleine Log-Tabelle (nur wenn nötig), aber bevorzugt im bestehenden Pattern.

### 6.2 DB-Safety
Grundregel:
- Jede DB-Connection wird **immer** geschlossen (Kontextmanager/try-finally).
- Keine langen Transaktionen, keine WAL-Blocker.

---

## 7. Integration Points im bestehenden ORÓMA-Pattern (ohne neue Architektur)
NMR-Lite soll *wie Coverage/Empathy* laufen: Hook → DB → UI.

### 7.1 Bestehende Blueprint-Mechanik
- `core/hooks_patch2.py` enthält produktive Hooks (coverage, empathy).
- `core/agent_loop.py` registriert diese Hooks.
- `core/curiosity.py` existiert als Signal-Modul (PE/novelty/entropy).

### 7.2 Minimaler Einhängepfad
1) **Feed sammeln** im Agent-Tick (wo Vision/Audio-Features ohnehin verfügbar sind).  
2) **Z(t) / B(t)** berechnen (deterministisch, billig).  
3) **PE(t)** berechnen (EMA-State).  
4) **Loggen**: `metrics` (und/oder existing curiosity log)  
5) Optional: Aktion (PTZ/Replay-Priorität) nur **bei** PE-Trigger und Cooldown.

### 7.3 Aktoren (optional, strikt defensiv)
- PTZ nur, wenn verfügbar und in Safe-Limits (bestehender PTZ-Controller).
- Repriorisierung: Replay/Dream/Queue-Gewichtung statt “harte” Aktionen.

---

## 8. Parameter Defaults (Pi-freundlich, konservativ)
Empfohlene Startwerte:
- `d_latent = 48` (oder 32 bei CPU-Engpass)
- EMA `alpha_mu = 0.02` (50 Ticks Halbwert)
- EMA `alpha_sigma = 0.02`
- `epsilon = 1e-3`
- Co-occurrence window `W = 3.0s`
- PE-threshold initial `T = 1.2` (z-normiert) + Auto-Tuning in Dream
- Cooldown für Aktionen (PTZ/Exploration) `>= 10s`

---

## 9. Erfolgskriterien (nach 7–14 Tagen sichtbar)
- `metric:nmr:pe` zeigt Peaks bei echten Umgebungswechseln.
- Coverage/Novelty steigt messbar (nicht zwingend linear).
- Binding-Hits > 0 (statt 0.00%).
- Keine DB-Locks, keine Log-Spam, keine UI-Errors.

---

## 10. Abgrenzung: Was NMR-Lite NICHT ist
- Kein trainiertes Shared-Embedding (kein InfoNCE).
- Kein echtes World-Model.
- Keine “3D-Rekonstruktion” (EG3D/Magic3D etc. sind Inspirationsklasse, nicht Edge-Plan).

NMR-Lite ist der **robuste Zwischenlayer**, der ORÓMA lebendiger macht, ohne das System in ein Trainingsprojekt zu verwandeln.

---
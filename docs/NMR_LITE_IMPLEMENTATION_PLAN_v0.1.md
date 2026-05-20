# ORÓMA – NMR-Lite Implementationsplan v0.1 (minimal-invasiv, headless, DB-safe)
**Pfad (Vorschlag):** `/opt/ai/oroma/docs/NMR_LITE_IMPLEMENTATION_PLAN_v0.1.md`  
**Projekt:** ORÓMA – Headless Lern-KI (Edge)  
**Stand:** 2026-01-30  
**Autor:** Jörg + GPT-5.2 Thinking  
**Baseline-ZIP:** `oroma_20260126_204948_with_db_milestone.zip`  

---

## 0) Leitprinzipien (verbindlich)
1) **Minimal-invasive** Änderungen: vorhandene Patterns verwenden (insb. Hook-Pattern wie Coverage/Empathy).
2) **Headless-only:** keine Qt/Wayland/X11/GUI-Abhängigkeiten.
3) **DB-safe:** jede SQLite-Connection muss **immer** geschlossen werden (with/try-finally).
4) **Keine stillen Fehler:** jede relevante Fehlersituation muss sichtbar geloggt werden.
5) **Aktionen (PTZ etc.) strikt defensiv:** nur bei klaren Triggern, mit Cooldown, mit Safe-Limits, niemals hard-fail.
6) **Telemetrie zuerst:** erst messbar machen (metrics/log), dann Aktionen daran koppeln.

---

## 1) Scope & Definition of Done
### 1.1 Scope (v0.1)
- Aus Live-Features wird pro Tick/Episode ein Observation-Vektor `O(t)` gebildet.
- Daraus wird deterministisch ein Fixed-Latent `Z(t)` (Random Projection oder SimHash-like) erzeugt.
- Prediction Error `PE(t)` wird über EMA-State berechnet (Predictive Coding).
- `PE(t)` wird als **Metrics-Serie** geloggt und ist im Learning-UI sichtbar (über existing Stats-Sampler).
- Optional (feature-flag): einfacher “Binding hit” Counter via Zeitfenster-Co-Occurrence.

### 1.2 Done (messbar)
- Neue Metrics erscheinen zuverlässig in `oroma.db metrics` (und über Sampler in `stats.db stats_points`).
- Keine DB-Locks, kein Log-Spam, keine UI-500 Fehler.
- `PE(t)` reagiert plausibel (Peaks bei deutlichen Änderungen).
- CPU-Mehrlast gering (Ziel: wenige ms pro Tick; keine Dauerlast).

---

## 2) Ziel-Datenfluss (kompakt)
1) AgentLoop Tick:
   - sammelt Features → `O(t)`
2) NMR-Lite State:
   - `Z(t)` berechnen
   - EMA aktualisieren
   - `PE(t)` berechnen
3) Telemetrie:
   - `metric:nmr:pe` schreiben
   - optional `metric:nmr:bind_hits`
4) Optional (später):
   - bei `PE(t)` hoch → “nudge”-Aktion (PTZ / Replay-Priorität) mit Cooldown

---

## 3) Invasions-Strategie: bestehende Stellen nutzen
### 3.1 Bestehende Patterns (soll genutzt werden)
- Hook-Pattern wie in `core/hooks_patch2.py` (coverage/empathy)
- Agent-Registrierung in `core/agent_loop.py`
- Signal-/Score-Modul `core/curiosity.py` (optional als Reuse; v0.1 darf auch separat sein)

### 3.2 Minimal neue Surface Area
**Ziel:** 1 neues Modul + kleine Einbindung in AgentLoop/Hooks.  
Vorschlag:
- `core/nmr_lite.py` (neu): stateful PE/EMA + fixed projection + safe helpers
- `core/hooks_patch2.py` (kleine Ergänzung): `nmr_lite_hook()` analog coverage/empathy
- `core/agent_loop.py` (kleine Ergänzung): Hook registrieren + Feature-Input liefern

> Wichtig: Kein “großer Umbau” in Vision/Audio-Pipeline. V0.1 nimmt das, was bereits im Tick verfügbar ist.

---

## 4) Konkrete TODO-Liste pro Datei (v0.1)
> Hinweis: Pfade beziehen sich auf Baseline-Projektstruktur aus ZIP und Zielpfad in Live-System `/opt/ai/oroma/...`.

### 4.1 (NEU) `/opt/ai/oroma/core/nmr_lite.py`
**Verantwortung:**
- deterministische Projektion `O(t) → Z(t)`
- EMA-State (`mu`, `sigma`, optional `vel`) persistent im RAM (pro Prozess)
- `pe = mean(|z - zhat|/(sigma+eps))`
- optional binary code `B(t)` und sehr leichter Co-Occurrence Counter

**Muss enthalten:**
- Ausführlicher Header (Pfad, Zweck, ENV, Safety)
- Keine externen schweren Dependencies
- Strict defensive coding:
  - wenn `O(t)` leer/None → log warning + return None/NaN
  - clamps und eps schützen vor div0

**ENV-Schalter (Vorschlag):**
- `OROMA_NMR_LITE=1` (enable)
- `OROMA_NMR_LATENT_D=48`
- `OROMA_NMR_ALPHA=0.02`
- `OROMA_NMR_PE_THRESH=1.2` (optional; nur für spätere Aktionen)
- `OROMA_NMR_BIND=0/1`
- `OROMA_NMR_BIND_WINDOW_SEC=3.0`

**Output API (minimal):**
- `update(obs: dict|list|np.array, ts: int) -> dict`
  - returns `{ "pe": float, "z_norm": float, "bind_hits": int, "code": str }` (teile optional)

> v0.1: `code` darf noch intern bleiben; wichtig ist `pe` + optional bind_hits.

---

### 4.2 (PATCH) `/opt/ai/oroma/core/hooks_patch2.py`
**Ziel:**
- Neue Hook-Funktion `nmr_lite_hook(context)` (oder ähnlich), die:
  - Features aus `context` liest
  - NMR-Lite updatet
  - `metrics` schreibt:
    - `metric:nmr:pe`
    - optional `metric:nmr:bind_hits`
- DB-safe: `with get_conn() as conn: ...`

**Logging-Regeln:**
- Falls NMR disabled → keine Logs, kein Spam
- Falls Feature-Input unvollständig → debug/warn *rate-limited* (z. B. alle 60s einmal)
- Bei DB error → error log + klare Message (kein silent)

---

### 4.3 (PATCH) `/opt/ai/oroma/core/agent_loop.py`
**Ziel:**
- Hook registrieren (analog empathy/coverage)
- Sicherstellen, dass der Hook Zugriff auf ein kleines Feature-Set bekommt.

**Welche Features v0.1 reicht (konservativ):**
- Vision: `motion` oder “frame_diff” (falls im Tick bereits vorhanden)
- Audio: `rms` (oder “energy”)
- Light: optional, falls im Tick vorhanden

**Wichtig:**
- Keine neuen DeviceHub Calls einführen, die Latenz/Locks riskieren.
- Nur bereits vorhandene Messwerte weiterreichen.

---

### 4.4 Optional (später, NICHT v0.1): `/opt/ai/oroma/wrappers/ptz_controller.py`
**Nur wenn v0.1 stabil läuft:**
- bei `PE(t) > threshold` und cooldown ok → kleiner PTZ-step
- aber nur, wenn device verfügbar + safe range
- Fehler dürfen niemals den Loop crashen (try/except + sichtbare logs)

---

## 5) Telemetrie & UI Sichtbarkeit
### 5.1 Metrics Keys (final für v0.1)
- `metric:nmr:pe`
- optional `metric:nmr:bind_hits`

### 5.2 Erwarteter Datenpfad
- Hook schreibt in `oroma.db` Tabelle `metrics`
- Existing Sampler übernimmt nach `stats.db stats_points`
- Learning UI zeigt `metric:*` Serien (wenn vorhanden)

> Keine UI-Änderungen in v0.1 zwingend nötig, sofern `ui/learning.py` bereits `metric:*` Serien anzeigt.
> Falls nicht, dann minimal: Learning-UI ergänzt um Anzeige von `metric:nmr:pe` (v0.2).

---

## 6) Acceptance Checks (bash-ready)
> Alles kopier/paste-fähig, headless.

### 6.1 Runtime: sehen ob Metrics geschrieben werden
```bash
sqlite3 /opt/ai/oroma/data/oroma.db "
SELECT datetime(ts,'unixepoch','localtime') AS t, key, value
FROM metrics
WHERE key IN ('metric:nmr:pe','metric:nmr:bind_hits')
ORDER BY ts DESC
LIMIT 20;"

6.2 Stats-Sampler: prüfen ob Serie im stats.db landet

sqlite3 /opt/ai/oroma/data/stats.db "
SELECT datetime(ts,'unixepoch','localtime') AS t, series, value
FROM stats_points
WHERE series IN ('metric:nmr:pe','metric:nmr:bind_hits')
ORDER BY ts DESC
LIMIT 20;"

6.3 Log-Sichtbarkeit (keine stillen Fehler)

journalctl -u oroma.service -u oroma-orchestrator.service -n 200 --no-pager | egrep -i "nmr|pe|curiosity|hook|sqlite|error|warn" || true


⸻

7) Risiken & Schutzmaßnahmen

7.1 DB-Locks
	•	Jede Hook-DB-Operation: kurz, mit close, keine Batch-Transaktionen.
	•	Kein “while” in Hook, keine long running loops.

7.2 CPU / Tick-Latenz
	•	d_latent klein halten (32–64).
	•	Keine numpy-Pflicht (wenn numpy vorhanden ok, aber muss ohne laufen können).
	•	Optional: Bind/Code-Berechnung per ENV abschaltbar.

7.3 “Daten fehlen”
	•	Hook muss robust sein, wenn z. B. Audio gerade deaktiviert ist:
	•	PE kann auf Vision-only laufen (degradiert), statt zu crashen.

⸻

8) Iterationsplan (v0.1 → v0.2)

v0.1 (jetzt)
	•	PE (metrics) + optional bind_hits (metrics)

v0.2
	•	“Novelty” aus Seen-Count / Code-Frequenz (ohne DB-heavy)
	•	UI: Learning zeigt eine kleine Box “NMR-Lite” (pe last/avg/max, bind_hits)
	•	Optional: sehr konservative Aktion (Replay-Priorität, nicht PTZ)

v0.3
	•	PTZ nudge (strict cooldown, strict safelimits)
	•	Dream: Konsolidierung/centroids (nur offline)


# ORÓMA PTZ Motor Worker

**Pfad:** `/opt/ai/oroma/docs/PTZ_MOTOR_WORKER.md`  
**Projekt:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Version:** v3.8.0+ptz-motor-worker-v1.6b-weighted-policy-bias  
**Stand:** 2026-06-07  
**Status:** Phase 5b – manueller systemd-Worker, Video-UI read-only, Target-Hold + Candidate/Axis-Lock + Eye-Pair-Salience + Face-assisted Motion-Radius + optionaler gewichteter `ptz_motor` Policy-Bias

---

## Zweck

Der PTZ Motor Worker trennt die schnelle Motorik der PTZ-Kamera vom schweren seriellen ORÓMA-Orchestrator.

Der bisherige PTZ-Pfad lief über:

```bash
python3 -m core.ptz_attention_loop --once
```

Dieser One-Shot-Pfad ist für Diagnose, Scan/Orient/Fixate und langsame Aufmerksamkeit geeignet, aber nicht für sichtbares Personen- oder Bewegungs-Following. Jeder Tick startet einen neuen Python-Prozess, lädt Module, initialisiert Statuspfade und kann durch DB-/Frame-Wartezeiten verzögert werden.

Der Motor Worker läuft dagegen dauerhaft:

```bash
python3 tools/ptz_motor_worker.py --verbose
```

Er initialisiert PTZ einmal, liest Frames aus dem schnellen Cache und sendet kurze Pan/Tilt-Korrekturen mit niedriger Latenz.

---

## Architektur

```text
ORÓMA Kognition / Langsampfad
├── tools/oroma_orchestrator.py
├── core/ptz_attention_loop.py
├── oroma.db / stats.db / knowledge.db
└── data/state/ptz_attention_state.json

ORÓMA Motorik / Schnellpfad
├── tools/ptz_motor_worker.py
├── wrappers/ptz_controller.py
├── core/camera_hub.py fast frame cache
└── data/state/ptz_motor_state.json
```

Der Motorikpfad soll keine Haupt-DB-Schreibzugriffe im Hot-Path machen. Die große ORÓMA-DB bekommt später nur verdichtete Ereignisse oder Summary-Metriken.

---

## Aktueller Zielzustand nach den Live-Tests

Der Stand aus der gemeinsamen PTZ-/Claude-Kalibrierung ist:

- `oroma-ptz-motor-worker.service` existiert als eigene systemd-Unit.
- Die Unit ist bewusst `disabled` und bleibt bis zur finalen Freigabe ohne Autostart.
- Runtime-Steuerung erfolgt ausschließlich per root/sudoer-Konsole auf dem Pi.
- Die `/video/` UI zeigt Status, Heartbeat, PID, Tuning, State-Pfad und Logs read-only.
- Flask/UI bekommt keine systemd-Schreibrechte und führt kein `start`, `stop`, `restart`, `enable` oder `disable` aus.
- Wenn der Worker gestoppt ist, wird ein alter State als stale/inaktiv markiert, damit alte `dx/dy/reason/action`-Werte nicht wie aktive Motorik wirken.

### Manuelle systemd-Steuerung

```bash
cd /opt/ai/oroma
sudo systemctl start oroma-ptz-motor-worker.service
sudo systemctl status oroma-ptz-motor-worker.service --no-pager -l
sudo systemctl stop oroma-ptz-motor-worker.service
systemctl is-enabled oroma-ptz-motor-worker.service
```

Erwartet in der Kalibrierphase:

```text
disabled
```

---

## Bestätigte Live-Test-Ergebnisse

Die Live-Tests zeigten:

- PTZ-Hardware: OK
- V4L2-Control-Device: OK
- Pan links/rechts: OK
- Tilt hoch/runter: OK
- Motion `dx/dy`: sichtbar
- Mapping X invertiert: notwendig und bestätigt
- Home-on-start: aktiv, Kamera geht beim Worker-Start auf Ursprung `pan=0`, `tilt=0`
- Coverage/Arena/Target-Autonomie: deaktiviert, damit kein zweiter PTZ-Pfad gleichzeitig fährt

---

## v1.1 Kalibrierung aus der gemeinsamen Arbeit

Aktiver v1.1-Kern:

- numpy-vektorisierte `_motion_centroid()` mit Python-Fallback
- Upper-Body-Bias statt reinem Motion-Centroid
- gemeinsame History-Struktur für Stability und Down-Hold
- signalbasierter Down-Hold: `down` erst bei mehrheitlich positivem `dy` in der History
- kein zusätzlicher `DOWN_GAIN`, damit Down nicht doppelt gedämpft wird
- `dy_raw`, `dy_biased`, `upper_bias`, `down_confirm`, `stable`, `bypass` im Verbose-Log
- `home_on_start` für reproduzierbaren Ursprung nach Start/Restart

Bewährte Testwerte aus dem letzten Live-Test:

```bash
OROMA_PTZ_MOTOR_ENERGY_MIN=0.004
OROMA_PTZ_MOTOR_STRONG_SIGNAL_BYPASS=0.400
OROMA_PTZ_MOTOR_STABILITY_MIN=3
OROMA_PTZ_MOTOR_DOWN_CONFIRM_MIN=3
OROMA_PTZ_MOTOR_UPPER_GAIN=1.35
OROMA_PTZ_MOTOR_LOWER_DAMPING=0.70
OROMA_PTZ_FOLLOW_INVERT_X=1
OROMA_PTZ_FOLLOW_INVERT_Y=0
```

Diese Werte wirkten ruhiger und nahmen Kopfbewegungen besser mit als die vorherigen Varianten.

## v1.2a/v1.2b Stabilisierung

### v1.2a – Target-Hold und geglättetes Ziel

Um den Übergang von reinem Reflex zu gerichteter Aufmerksamkeit vorzubereiten, hält der Worker seit v1.2a ein kurzlebiges Ziel im Speicher:

```text
target_dx / target_dy
target_conf
target_age_ticks
target_hold_active
target_update
```

Wichtig: Hold wird nur aus einem echten erfolgreichen `reason=follow` qualifiziert. Schwache `energy_low`-Frames oder geblockte `stability_wait`/`down_hold`-Entscheidungen erzeugen keinen qualifizierten Hold. Dadurch stabilisiert der Worker keine schlechten Ziele.

Aktiver Test-Default nach dem Tageslichttest vom 2026-05-11:

```bash
OROMA_PTZ_MOTOR_TARGET_ENABLE=1
OROMA_PTZ_MOTOR_TARGET_DECAY=0.85
OROMA_PTZ_MOTOR_TARGET_ALPHA=0.45
OROMA_PTZ_MOTOR_TARGET_CONF_MIN=0.020
OROMA_PTZ_MOTOR_TARGET_OVERRIDE_RATIO=1.80
OROMA_PTZ_MOTOR_TARGET_HOLD_TICKS=6
OROMA_PTZ_MOTOR_TARGET_HOLD_COMMAND=0
```

`TARGET_CONF_MIN=0.020` ist bewusst als neuer Default gesetzt, weil der Live-Test zeigte, dass `0.040` den Hold bei feinen Kopf-/Oberkörperbewegungen zu schnell auslaufen lässt.

### v1.2b – Candidate-Struktur und Axis-Lock

v1.2b führt intern eine explizite Candidate-Struktur ein, ohne bereits einen vollwertigen Face-/Head-Tracker zu erzwingen:

```text
Motion Field → Candidate Extraction → Candidate Scoring
→ Target Hold / Axis Lock → Servo Decision → PTZ Command
```

Der Motion-Candidate ist weiterhin der stabile Fallback aus dem vorhandenen Frame-Diff. Die explizite Struktur ist die Nahtstelle für v2, in der mehrere Kandidaten, Eye-Pair-/Face-like-Salience und ein echter Target Tracker sauber ergänzt werden können.

Axis-Lock ist bewusst kurz und konservativ:

```bash
OROMA_PTZ_MOTOR_AXIS_LOCK_ENABLE=1
OROMA_PTZ_MOTOR_AXIS_LOCK_TICKS=4
OROMA_PTZ_MOTOR_AXIS_LOCK_OVERRIDE_RATIO=1.65
```

Der Lock wird nur nach einem echten erfolgreichen PTZ-Follow gesetzt. Eine deutlich stärkere orthogonale Bewegung darf den Lock sofort brechen. Ziel ist weniger x/y-Flackern, nicht starres Festhalten an einer falschen Achse.


## v1.4a/v1.4b/v1.4c – Eye-Pair / Face-like Salience Candidate

Der nächste Schritt ist bewusst **keine klassische Face Detection** und keine Personenerkennung. Der Worker sucht optional nur nach zwei plausiblen dunklen Blobs im oberen/mittleren Bildbereich, die als weiches Augenpaar-Signal dienen können. Dieses Signal ist ein zusätzlicher Candidate mit Confidence-Score, kein hartes Ja/Nein.

Pipeline-Ziel:

```text
Motion Field
→ Motion Candidate
→ Eye-Pair / Face-like Salience Candidate
→ Candidate Scoring
→ Target Hold / Axis Lock
→ Servo Decision
→ PTZ Command
```

Konservative Defaults:

```bash
OROMA_PTZ_MOTOR_EYE_PAIR_ENABLE=1
OROMA_PTZ_MOTOR_EYE_PAIR_REQUIRE_MOTION=1
OROMA_PTZ_MOTOR_EYE_PAIR_MIN_CONF=0.18
OROMA_PTZ_MOTOR_EYE_PAIR_SCORE_GAIN=1.20
OROMA_PTZ_MOTOR_EYE_PAIR_MAX_ANGLE_DEG=45
OROMA_PTZ_MOTOR_EYE_PAIR_MIN_SEP=0.07
OROMA_PTZ_MOTOR_EYE_PAIR_MAX_SEP=0.46

# v1.4b False-Positive-Gates:
OROMA_PTZ_MOTOR_EYE_PAIR_MOTION_RADIUS=0.35
OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST=1.55
OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST_MIN=0.40
OROMA_PTZ_MOTOR_EYE_PAIR_MIN_FRAMES_STABLE=2
OROMA_PTZ_MOTOR_EYE_PAIR_STABLE_RADIUS=0.12
```

Wichtig für das Konzept:

- Motion bleibt Fallback und Basis-Signal.
- Eye-Pair darf das Ziel nur als Candidate stabilisieren.
- Standardmäßig braucht Eye-Pair weiterhin Motion-Bestätigung (`EYE_PAIR_REQUIRE_MOTION=1`).
- Keine Identifikation, keine personenbezogene Erkennung, kein Modellzwang.
- Random-Dunkelstellen werden durch Abstand, Winkel, Größe, Dunkelheits-Score und obere Bildregion nur weich bewertet.
- v1.4b lässt geometrisch plausible Eye-Pair-Rohkandidaten erst dann wirken, wenn sie lokal zur Motion passen und über mehrere Ticks stabil bleiben.
- v1.5c kann den lokalen Motion-Radius nur dann weich erweitern, wenn die erwartete Face-Region bereits plausibel ist. Dadurch werden Kopf-/Augen-Kandidaten toleranter gegen Motion-Centroids auf Brille, Schulter oder Hand, ohne den Basisradius pauschal zu öffnen.
- Die Video-UI bleibt read-only; diese Stufe ändert keine systemd-Steuerung.

Neue State-/Log-Felder:

```text
candidates[]
candidate.kind = motion_centroid | eye_pair_salience
candidate.source = motion_diff_upper | eye_pair_heuristic
eye_pair_enabled
eye_pair_require_motion
eye_pair_min_conf
counters.eye_pair_raw
counters.eye_pair_geom_ok
counters.eye_pair_motion_gated
counters.eye_pair_temporal_gated
counters.eye_pair_candidates
counters.eye_pair_selected
counters.eye_pair_rejected_motion
counters.eye_pair_rejected_temporal
counters.eye_pair_rejected_geometry
attention.eye_pair
```

---

## v1.4c – Eye/Head-Hold-Bias

Der Test mit Person zeigte: Wenn die Person kurz stillhält, kann ein anderes Motion-/Edge-Signal das zuletzt plausible Eye-/Head-Ziel verdrängen. v1.4c verändert deshalb nicht die Erkennung, sondern den Attention-Hold: Ein zuletzt per `eye_pair_salience` qualifiziertes Follow-Ziel darf für wenige Ticks bevorzugt und optional aktiv gehalten werden.

Konservative Defaults:

```bash
OROMA_PTZ_MOTOR_EYE_HOLD_BIAS_ENABLE=1
OROMA_PTZ_MOTOR_EYE_HOLD_TICKS=8
OROMA_PTZ_MOTOR_EYE_HOLD_CONF_MIN=0.060
OROMA_PTZ_MOTOR_EYE_HOLD_OVERRIDE_RATIO=1.60
OROMA_PTZ_MOTOR_EYE_HOLD_COMMAND=1
```

Bedeutung:

- Nur ein vorheriges echtes `follow` mit Candidate-Kind `eye_pair_salience` qualifiziert den Eye/Head-Hold.
- Schwächere neue Motion-/Edge-Signale ziehen das Ziel nicht sofort weg.
- Deutlich stärkere neue Kandidaten dürfen weiterhin überschreiben.
- Der Hold läuft über Confidence-Decay und Tick-Limit aus; es gibt kein endloses Festkleben.
- Diese Stufe ist weiterhin keine Personenerkennung, sondern eine trägere Aufmerksamkeit für ein zuvor plausibles Kopf-/Augenziel.

Neue State-/Log-Felder:

```text
target_last_qualified_kind
target_last_qualified_source
eye_hold_bias_enabled
eye_hold_bias_active
eye_hold_command_active
eye_hold_ticks
eye_hold_conf_min
eye_hold_override_ratio
eye_hold_command
counters.eye_hold_bias
counters.eye_hold_commands
```


## Wichtige State-Datei

Der Worker schreibt den Motorikzustand nach:

```text
/opt/ai/oroma/data/state/ptz_motor_state.json
```

Prüfen:

```bash
cat /opt/ai/oroma/data/state/ptz_motor_state.json | python3 -m json.tool
```

Wichtige Felder:

```text
heartbeat_ts
state_age_sec
state_stale
loop_hz_target
frame_source
frame_age_sec
reason
action
raw_action
mapped_action
axis
dx
dy
dy_raw
dy_biased
dist
energy
energy_weighted
upper_bias_delta
target_dx / target_dy / target_conf / target_age_ticks
target_hold_active / target_update
candidate / candidates / candidate_source
eye_pair_enabled / eye_pair_require_motion / eye_pair_min_conf
eye_pair_motion_radius / eye_pair_min_frames_stable / eye_pair_stable_radius
eye_pair_stable_count / eye_pair_last
axis_lock_enabled / axis_lock_active / axis_lock_axis / axis_lock_reason
eye_hold_bias_active / eye_hold_command_active / target_last_qualified_kind
amount
cmd_ok
counters
```

---

## Erwartete Logzeilen

Bei `--verbose`:

```text
[ptz_motor_worker] start ok=1 device=... hz=3.00 ... upper_bias=1 ... stability=3 bypass=0.400
[ptz_motor_worker] tick=1 reason=deadzone ...
[ptz_motor_worker] tick=2 reason=follow ... dx=-0.21 dy=0.04 dy_raw=... dy_biased=...
[ptz_motor_worker] summary ticks=60 frames=58 moves=12 ok=12 fail=0 ...
```

Bedeutung:

```text
reason=follow          PTZ-Korrektur wurde ausgeführt
reason=deadzone        Bewegung zu nah an Bildmitte
reason=energy_low      Bewegung unter Mindestenergie
reason=no_frame        kein Cache-Frame verfügbar
reason=stale_frame     Cache-Frame zu alt
reason=reversal_guard  Gegenrichtung kurz geblockt
reason=down_hold       Down wurde signalbasiert noch nicht bestätigt
reason=stability_wait  Richtung wurde noch nicht stabil genug bestätigt
reason=cmd_fail        PTZController.nudge() fehlgeschlagen
reason=target_hold     kurzlebiges Ziel ist noch bekannt; default ohne eigenes Kommando
reason=eye_hold        zuletzt plausibles Eye-/Head-Ziel wird kurz aktiv gehalten
```

---

## Troubleshooting

### Keine Bewegung

```bash
cat /opt/ai/oroma/data/state/ptz_motor_state.json | python3 -m json.tool
systemctl status oroma-ptz-motor-worker.service --no-pager -l
tail -n 80 /opt/ai/oroma/logs/ptz_motor_worker.out.log
tail -n 80 /opt/ai/oroma/logs/ptz_motor_worker.err.log
```

Achte auf:

```text
cmd_ok
cmd_error
frame_source
frame_age_sec
reason
energy
state_stale
```

### Nur `no_frame`

Dann liefert der Kamera-/Visionpfad keinen Cache-Frame. Prüfen:

```bash
ls -lah /opt/ai/oroma/data/state/latest_frame_cache.*
```

### Nur `stale_frame`

Dann ist der Cache vorhanden, aber zu alt. Prüfen, ob `oroma.service` und der Kamera-Hub laufen.

### Pendeln oder nervöses Verhalten

Nicht zuerst die Frequenz erhöhen. Erst konservativer machen:

```bash
OROMA_PTZ_MOTOR_AMOUNT_MAX=3
OROMA_PTZ_MOTOR_REVERSAL_GUARD_SEC=1.2
OROMA_PTZ_MOTOR_STABILITY_MIN=3
OROMA_PTZ_MOTOR_STRONG_SIGNAL_BYPASS=0.400
```

---

### v1.4d Servo-Damping / Calm-Follow

v1.4d reduziert das hektische Nachzucken des Motor-Workers, ohne die
Eye-Pair-/Head-Salience oder Motion-Fallback-Architektur zu entfernen. Der Patch
ist bewusst eine Servo-/Motorik-Schicht, keine neue Erkennung.

Neue bzw. geänderte Parameter:

```bash
OROMA_PTZ_MOTOR_AMOUNT_MAX=3
OROMA_PTZ_MOTOR_MOVE_COOLDOWN_TICKS=3
OROMA_PTZ_MOTOR_MICRO_GUARD_ENABLE=1
OROMA_PTZ_MOTOR_MICRO_GUARD_DIST_FACTOR=1.50
OROMA_PTZ_MOTOR_MICRO_GUARD_CONF_MAX=0.120
```

Regeln:

- Nach erfolgreichem PTZ-Kommando wird ein tick-basierter Cooldown gesetzt.
- Der Loop schläft nicht zusätzlich; der Cooldown blockiert nur neue schwache
  Servo-Kommandos.
- Starke Signale (`obs_conf > STRONG_SIGNAL_BYPASS`) und
  `eye_pair_salience` dürfen den Cooldown überschreiben.
- Der Micro-Guard läuft vor Axis-Lock. Dadurch kann Axis-Lock keinen zuvor
  geblockten Mikro-Move wieder in einen Achsenbefehl verwandeln.
- `AMOUNT_MAX` ist konservativ auf 3 gesetzt, damit normales Following weniger
  mechanisch springt. Seit v1.4d1 ist dieser Wert nicht nur Python-Default,
  sondern auch explizit in den systemd-Referenzdateien auf `3` gepinnt. Der
  Worker-Startlog muss damit `amount=2-3` zeigen, sofern keine lokale `.env`
  oder externe Unit-Override-Datei den Wert erneut überschreibt.

Neue Diagnosefelder/Counters im State/API:

```text
move_cooldown_remaining
move_cooldown_active
move_cooldown_bypass
micro_guard_active
counters.move_cooldown_blocks
counters.move_cooldown_bypass
counters.micro_guard_blocks
attention.servo.*
```

Zielwerte nach v1.4d bei Personentest über 120 Sekunden:

```text
moves eher 25–45 statt ca. 100
cmd_fail=0
Kamera folgt weiter, wirkt aber ruhiger
Eye/Head-Hold bekommt mehr Zeitfenster, weil Motion nicht dauernd dazwischen feuert
```

---

## Offene Checkliste

1. **Read-only Video-UI beibehalten**  
   `/video/` darf Status/Logs anzeigen, aber keine systemd-Schreibaktionen ausführen.

2. **Kein sudoers-Hack**  
   Steuerung ausschließlich per `sudo systemctl` als root/sudoer auf dem Pi. Flask/UI bekommt zu keinem Zeitpunkt Schreibrechte auf systemd.

3. **Stale-State-Markierung prüfen**  
   UI muss alte State-Werte sichtbar als inaktiv/stale markieren, wenn `time.time() - heartbeat_ts` den Grenzwert überschreitet.

4. **v1.2a als bestätigt markieren**  
   Target-Hold, exponentieller Decay, Override-Logik und Verbose-Felder sind produktiv getestet. `TARGET_CONF_MIN=0.020` ist der aktuelle Default.

5. **v1.2b gezielt testen**  
   Candidate-Struktur und Axis-Lock beobachten: Ziel ist weniger x/y-Flackern. Prüfen, ob starke neue Querbewegungen den Lock sauber brechen.

6. **Bypass X/Y getrennt erst später**  
   Niedrige Priorität. Erst beobachten, ob starke seitliche Bewegungen wirklich Probleme erzeugen.

7. **v1.4b Eye-Pair False-Positive-Gate testen**  
   Ohne Person sollen `eye_pair_raw`/`eye_pair_geom_ok` zwar Rohfunde zeigen dürfen, aber `eye_pair_temporal_gated` und `eye_pair_selected` sollen niedrig bleiben. Mit Person sollen beide Werte sichtbar steigen.

8. **v1.4c Eye/Head-Hold-Bias testen**  
   Mit Person: bewegen, dann kurz stillhalten. Erwartung: Bei zuvor qualifiziertem `eye_pair_salience` soll die Kamera den Kopf-/Augenbereich kurz halten, statt sofort auf schwache Raumkanten/Motion-Signale wegzudrehen. Ohne Person darf `eye_hold` praktisch nicht aktiv werden.

9. **v1.4d Servo-Damping testen**  
   Mit Person: `moves`, `cooldown`, `cooldown_bypass` und `micro` beobachten. Ziel ist ruhigeres Following mit deutlich weniger Motorbefehlen, ohne starke Bewegungen oder Eye-Pair-Salience zu blockieren.

10. **v2 Face-like Salience später erweitern**  
   Gesichtsähnliche Salienz mit Augenpaar, Symmetrie, Temporal-Stability und optional breitem Hautfarb-Bonus erst nach stabilem v1.4b-Gating erweitern.

11. **Kinder/untere Bildbereiche beachten**  
   Upper-Body-Bias darf Kinder oder sitzende Personen nicht vollständig wegdämpfen. Das bleibt ein Kalibrierpunkt.

12. **Head-/Face-Tracker nach Initialerkennung**  
   Klassischer Tracker nach Initialerkennung ist sinnvoll, aber erst nach stabiler Motion-/Upper-Body-/Candidate-Basis.

---

## Nächster sauberer Test

```bash
cd /opt/ai/oroma
python3 -m py_compile tools/ptz_motor_worker.py ui/video_ui.py
sudo systemctl start oroma-ptz-motor-worker.service
systemctl status oroma-ptz-motor-worker.service --no-pager -l
tail -n 80 /opt/ai/oroma/logs/ptz_motor_worker.out.log
curl -sS http://127.0.0.1:8080/video/api/ptz/motor/status | python3 -m json.tool
sudo systemctl stop oroma-ptz-motor-worker.service
systemctl is-enabled oroma-ptz-motor-worker.service
```

---

## v1.5b Expected-Face-Region / Head-Context

v1.5b ergänzt die Eye-Pair-Salience um einen leichten Head-Context-Bonus. Das ist weiterhin keine Face Detection, keine Personenerkennung und kein Modellpfad.

Aus dem Augenpaar wird eine erwartete Kopf-/Gesichtsregion abgeleitet:

```text
Augenabstand sep_px
→ erwartete Kopfbreite ca. 1.55 × sep_px
→ erwartete Kopfhöhe ca. 2.80 × sep_px
→ ROI-Mittelpunkt leicht unterhalb des Augenmittelpunkts
```

In dieser ROI werden nur einfache Bildstatistiken geprüft:

- lokaler Kontrast / Standardabweichung
- horizontale Kanten-Dominanz, um Möbel-/Regalkanten abzuwerten
- Textur-/Gradientenstärke, damit homogene Wand-/Deckenbereiche nicht belohnt werden

Der Face-Region-Score ist ein Bonus auf `eye_pair_salience`. Motion bleibt Fallback und die bestehenden Local-Motion-/Temporal-Gates bleiben aktiv.

Neue ENV-Werte:

```bash
OROMA_PTZ_MOTOR_FACE_REGION_ENABLE=1
OROMA_PTZ_MOTOR_FACE_REGION_BONUS=0.28
OROMA_PTZ_MOTOR_FACE_REGION_MIN_SCORE=0.18
OROMA_PTZ_MOTOR_FACE_REGION_MIN_STD=4.0
OROMA_PTZ_MOTOR_FACE_REGION_HORIZ_MAX=0.82
```

Neue Counter/Statusfelder:

```text
face_region_checked
face_region_ok
face_region_bonus
attention.face_region
candidate.face_region
```

Ziel: Eye-Pair-Kandidaten, die wirklich in einer plausiblen Kopf-/Gesichtsregion liegen, sollen gegenüber zufälligen Möbelkanten, Schattenpaaren und Brillen-/Reflexmustern etwas stärker gewichtet werden. Der Patch soll das bestehende ruhige Servo-Verhalten aus v1.4d/v1.4d1 nicht wieder hektischer machen.


## v1.5b – Face-Region Gradient Score + Eye/Face Soft-Ranking

- `OROMA_PTZ_MOTOR_FACE_REGION_GRAD_MIN` (Default `0.80`) bewertet die vertikale Kontraststaffelung in der erwarteten Kopf-/Gesichts-ROI.
- `OROMA_PTZ_MOTOR_EYE_FACE_RANK_THRESHOLD` (Default `0.85`) erlaubt Eye/Face-Salience als Soft-Ranking-Gewinner, ohne Motion als Fallback zu entfernen.
- Die Berechnung bleibt Raspberry-Pi-5-tauglich: Sie nutzt nur das bereits downsampled small-gray Bild und 1-D NumPy-Mittelwerte/Varianzen, keine Modelle, keine Cascade und keinen Full-Resolution-Scan.
- Diagnosefelder: `face_region.ok`, `face_region.score`, `face_region.reason`, `candidate_winner`.

## v1.5c – Face-assisted Motion-Radius

v1.5c ändert nicht die Servo-Motorik und macht keine neue Erkennungsschicht auf. Der Patch adressiert gezielt den beobachteten Bottleneck `gate_reason=motion_too_far`: Eye-Pair-Kandidaten mit plausibler Face-Region wurden verworfen, wenn der aktuelle Motion-Centroid leicht neben dem Augenmittelpunkt lag.

Neue ENV-Werte:

```bash
OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST=1.55
OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST_MIN=0.40
```

Verhalten:

- Basisradius bleibt `OROMA_PTZ_MOTOR_EYE_PAIR_MOTION_RADIUS=0.35`.
- Nur wenn `candidate.face_region.ok=true` und `face_region.score_norm >= EYE_FACE_RADIUS_BOOST_MIN`, wird der effektive Radius auf `base_radius * EYE_FACE_RADIUS_BOOST` erweitert.
- Ohne plausible Face-Region bleibt das Gate unverändert konservativ.
- Motion bleibt Fallback; Servo-Damping, Cooldown, Micro-Guard und Manual-only-systemd bleiben unverändert.

Neue/erweiterte Diagnosefelder am Eye-Pair-Kandidaten:

```text
motion_radius
motion_radius_effective
face_radius_boost_active
face_radius_boost_factor
face_radius_boost_min
```

Ziel: `motion_too_far` soll bei plausiblen Kopf-/Gesichts-Kandidaten seltener werden; `eye_pair_selected` und gelegentlich `candidate_winner=eye_face_salience` sollen steigen, ohne `moves` wieder in den hektischen Bereich zu treiben.



## v1.6 / PTZ Phase 5a – optionaler `ptz_motor` Policy-Bias

Seit 2026-05-29 kann der Worker die im Dream verdichteten Regeln aus `policy_rules namespace='ptz_motor'` optional read-only als weichen Aktions-Bias laden.

### Ziel

Der bisherige Lernpfad wird geschlossen:

```text
ptz_motor_state.json
→ ptz_motor_reward_collector.py
→ rewards_log source LIKE 'ptz_motor/%'
→ dream_worker.py / ptz_policy_motor
→ policy_rules namespace='ptz_motor'
→ ptz_motor_worker.py Policy-Bias
```

### Sicherheitsmodell

Der Bias ist kein Controller und ersetzt keine Reflexlogik. Er darf nur leichte Tendenzen geben und keine Safety-Gates überschreiben.

Nicht überschreibbar bleiben:

```text
deadzone
energy_low
micro_guard
cooldown
axis_lock
reversal_guard
cmd_fail handling
PTZ command safety
```

### ENV-Parameter

```bash
OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=0
OROMA_PTZ_MOTOR_POLICY_NS=ptz_motor
OROMA_PTZ_MOTOR_POLICY_BIAS_WEIGHT=0.08
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_N=10
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_RULE_N=3
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_ABS_Q=0.05
OROMA_PTZ_MOTOR_POLICY_BIAS_REFRESH_SEC=60
OROMA_PTZ_MOTOR_POLICY_BIAS_MAX_ABS=0.20
```

### Erster Live-Test

Der erste Starttest nach Einbau war erfolgreich:

```text
python3 -m py_compile tools/ptz_motor_worker.py  → OK
systemd service                                  → active (running)
ok                                               → steigend
fail                                             → 0
```

Die Startzeile zeigte:

```text
policy_bias=0 policy_ns=ptz_motor policy_w=0.080 policy_min_total_n=10 policy_min_rule_n=3 policy_min_q=0.050 policy_refresh=60.0s
```

Damit ist der Codepfad stabil eingebaut. Ein echter aktiver Policy-Bias-Test beginnt erst mit `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=1`.

## v1.6b / PTZ Phase 5b – gewichtete Policy-Aggregation und validierter Kurztest

**Stand:** 2026-06-07  
**Status:** Live validiert, aber dauerhaftes systemd-Gate weiterhin nicht aktiviert.

### Anlass

Nach dem ersten Policy-Bias-Pfad zeigte die Live-Analyse, dass ein einfacher Durchschnitt `AVG(q)` statistisch zu grob ist. Eine Regel mit `n=1` darf nicht genauso stark wirken wie eine wiederholt bestätigte Regel mit `n=38` oder höher. Deshalb wurde der Ladepfad des Workers auf eine evidenzgewichtete Aggregation umgestellt.

### Neue Aggregationslogik

Der Worker bewertet Action-Kandidaten aus `policy_rules namespace='ptz_motor'` mit:

```sql
SUM(q * n) / SUM(n)
```

Zusätzlich werden Einzelbeobachtungen gefiltert:

```sql
WHERE namespace = 'ptz_motor'
  AND n >= OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_RULE_N
GROUP BY action
HAVING SUM(n) >= OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_N
```

Produktive, konservative Defaults:

```bash
OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=0
OROMA_PTZ_MOTOR_POLICY_NS=ptz_motor
OROMA_PTZ_MOTOR_POLICY_BIAS_WEIGHT=0.08
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_N=10
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_RULE_N=3
OROMA_PTZ_MOTOR_POLICY_BIAS_MIN_ABS_Q=0.05
OROMA_PTZ_MOTOR_POLICY_BIAS_REFRESH_SEC=60
OROMA_PTZ_MOTOR_POLICY_BIAS_MAX_ABS=0.20
```

Wichtig: `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE` bleibt standardmäßig aus. Phase 5b aktiviert keine dauerhafte Autonomie-Erweiterung, sondern macht die spätere Bias-Entscheidung belastbarer und sichtbarer.

### Live-Nachweis vom 2026-06-07

Nach Backlog-Abbau und Dream-Verdichtung lag der PTZ-Motor-Lernstand bei:

```text
policy_rules namespace='ptz_motor': 524
sum(n): 1270
backlog: 0
```

Gewichtete Kandidaten:

```text
up     total_n=433  weighted_q=0.061893  → Gate 0.05 bestanden
down   total_n=159  weighted_q=0.054167  → Gate 0.05 bestanden
right  total_n=87   weighted_q=0.007183  → Gate nicht bestanden
left   total_n=79   weighted_q=0.004254  → Gate nicht bestanden
```

Ein temporärer `systemd-run`-Bias-Test mit `OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=1` und `OROMA_PTZ_MOTOR_POLICY_BIAS_WEIGHT=0.04` bestätigte:

```text
policy_bias_enabled: true
policy_bias_active:  true
policy_bias:         {'down': 0.002167, 'up': 0.002476}
raw_rule_count:      523
eligible_rule_count: 59
used_action_count:   2
aggregation:         weighted_q=sum(q*n)/sum(n)
```

Die Werte sind korrekt skaliert:

```text
up    0.061893 * 0.04 = 0.002476
down  0.054167 * 0.04 = 0.002167
```

### Betriebsentscheidung

Der Test bestätigt den technischen Rückkanal, aber die dauerhafte Aktivierung bleibt bewusst aus. Die Policy ist noch jung; `up` und `down` liegen nur knapp über dem Gate. Empfohlen ist weiterer Normalbetrieb mit:

```text
Collector an
Dream-Verarbeitung regelmäßig
Policy-Bias im Standarddienst aus
```

Erst wenn `weighted_q`, `total_n` und die Reward-Bilanz über mehrere Tage stabil bleiben, sollte eine dauerhafte, schwach gewichtete Aktivierung über systemd geprüft werden.



## Stand 2026-06-13 – Positive Position Marker / Stage-A Evidence

- `tools/ptz_motor_worker.py` speichert konservative Positive Position Marker in `data/state/ptz_positive_position_markers.json`.
- v1.6d blockiert `motion_diff_upper` und Motion-only-Kandidaten standardmäßig (`OROMA_PTZ_MOTOR_POS_MARKER_ALLOW_MOTION_ONLY=0`, `OROMA_PTZ_MOTOR_POS_MARKER_ALLOW_UPPER_MOTION=0`).
- Der Marker ist reine Evidenz: keine Identität, keine automatische Steuerung, keine direkte Policy-Materialisierung.
- Ceiling-Recovery schützt gegen langes Decken-/Leerlauf-Schauen, ist aber durch Start-Grace und Cooldown rate-limitiert.
- `tools/ptz_positive_position_probe.py` ist die Stage-A-Messbrücke: Marker zählen, Top-Zellen und Guard-Status sichtbar machen und optional via DBWriter nach `stats.db.stats_points` schreiben.
- Produktiver Bias bleibt aus, bis Learning-Evidence eindeutig positive Wirkung belegt.

### Stage-A Evidence Timer (P1b, Stand 2026-06-13)

Die Positive-Position-Probe kann jetzt regelmäßig als systemd-Timer laufen:

```text
systemd/oroma-ptz-positive-position-probe.service
systemd/oroma-ptz-positive-position-probe.timer
```

Der Timer führt alle 5 Minuten aus:

```text
tools/ptz_positive_position_probe.py --once --write-stats --verbose
```

Architekturregeln:

- reine Stage-A-Messung; keine PTZ-Motorsteuerung
- keine Policy-Aktivierung
- keine Materialisierung in `object_nodes` oder `object_relations`
- Stats-Writes nur über DBWriter (`OROMA_DBW_ENABLE=1`)
- Verlaufsspur in `stats.db.stats_points` über `ptz.marker.*`-Serien

Damit werden wiederkehrende interessante PTZ-Bildpositionen nicht sofort als
Verhalten genutzt, sondern zuerst über Zeit messbar gemacht. Das entspricht der
Core-Regel: messen → Evidenz sammeln → später Dream/Binding entscheiden lassen.


## P2 – PTZ Positive Position Evidence Report (Stand 2026-06-13)

`tools/ptz_positive_position_evidence_report.py` ergänzt die Stage-A-Probe um eine reine Read-only-Trendanalyse. Während der Timer `oroma-ptz-positive-position-probe.timer` alle fünf Minuten Messpunkte schreibt, wertet der Report diese Punkte über Zeitfenster aus.

Eigenschaften:

- liest `data/stats.db.stats_points` mit `series LIKE 'ptz.marker.%'`
- liest optional den aktuellen Marker-/Worker-State
- schreibt nichts
- bewegt keinen Motor
- aktiviert keine Policy
- materialisiert keine `object_nodes` oder `object_relations`
- gibt JSON für UI/Audit oder Text für die Konsole aus

Standardfenster:

- 60 Minuten
- 360 Minuten
- 1440 Minuten

Beispiel:

```bash
cd /opt/ai/oroma; python3 tools/ptz_positive_position_evidence_report.py --json --verbose
```

Wichtige Kennzahlen:

- `positive_count_last`
- `repeat_ge_3_last`
- `repeat_ge_5_last`
- `motion_guard_rate`
- `ceiling_active_rate`
- `ceiling_marker_stale_rate`
- `top_key_stability`

Der Report ist bewusst kein Entscheidungsmodul. Er liefert nur Evidence, damit Dream/Binding später auf stabilere Fakten zugreifen kann.

## P2.5 – PTZ Policy Atomicity / DBWriter Hygiene (Stand 2026-06-14)

Dieser Stand ist ein reiner Stabilitäts- und Wartbarkeits-Patch. Die Lernsemantik wird bewusst nicht vereinheitlicht:

- `universal_policy.learn_many()` nutzt weiterhin eine diskrete Bilanz-Semantik für Spiel-/Universal-Namespace-Regeln: `q=(pos-neg)/n`.
- PTZ-Policy-Namespace(s) behalten die kontinuierliche Reward-Mittelwert-Semantik: `q=((q*n)+r)/(n+1)`.
- Im DBWriter-Betrieb werden die PTZ-Policy-Updates jetzt als atomare DBWriter-Transaction ausgeführt: `INSERT OR IGNORE` und `UPDATE` bleiben logisch identisch, laufen aber nicht mehr als zwei getrennte Queue-Operationen.
- In `core/sql_manager.py` wurde eine tote doppelte `_dbw_enabled()`-Definition entfernt; die aktive ENV-basierte Implementierung bleibt erhalten.
- Keine Policy-Bias-Aktivierung, keine Motorsteuerung, keine Materialisierung und kein lokaler SQLite-Fallback wurden hinzugefügt.

## P2.6 – Idempotente Stage-A Stats-Writes (Stand 2026-06-16)

Live-Befund nach mehrtägigem Timer-Betrieb: Die PTZ Positive Position Evidence
wuchs fachlich korrekt (`positive_count=19`, `repeat_ge_5=17`, stabile Top-Zelle
`g6:x2:y3`), aber einzelne Wiederholungsläufe konnten in `stats_points` einen
`UNIQUE constraint failed: stats_points.src_table, stats_points.src_uid, stats_points.series`
auslösen.

Korrektur: `tools/ptz_positive_position_probe.py` und
`tools/synapses_bridge_probe.py` schreiben ihre Measure-only-Stats weiterhin
ausschließlich via DBWriter, aber nun idempotent per `ON CONFLICT ... DO UPDATE`.
Das ändert keine Lernsemantik, keine Markerlogik, keine Motorsteuerung und keine
Materialisierung. Es verhindert nur, dass doppelte Sekunden-Snapshots oder
Orchestrator-Wiederholungsläufe als harte Fehler im Timer/DBWriter erscheinen.


## P3a – Regional Temporal Motion Signature Evidence (Stand 2026-06-16)

Der bestehende PTZ Positive-Position-Pfad ist konservativ Eye/Face-gated. Das ist richtig, weil Motion-only zuvor Decke, Lichtwechsel oder Grundrauschen hätte positiv markieren können. In der Live-Situation kann die Kamera jedoch auch kleine Menschen/Autos auf der Straße sehen, die keine erkennbaren Eye/Face-Signale erzeugen. Dafür ergänzt P3a einen separaten, strikt messenden Pfad.

Neue Komponenten:

```text
tools/ptz_structured_motion_probe.py
tools/ptz_structured_motion_evidence_report.py
systemd/oroma-ptz-structured-motion-probe.service
systemd/oroma-ptz-structured-motion-probe.timer
```

Architekturregeln:

- eigener Namespace: `ptz.motion.*`
- eigener State: `data/state/ptz_structured_motion_state.json`
- keine Änderung am Eye/Face-/`ptz.marker.*`-Pfad
- keine PTZ-Motorbefehle
- keine Policy-Aktivierung
- keine object_nodes/object_relations-Materialisierung
- Stats-Writes nur via DBWriter nach `stats.db.stats_points`
- echte historische `top_key`-Werte stehen in `stats_points.meta`, numerische Scores in `value`

P3a klassifiziert pro Rasterzelle regionale Zeitsignaturen:

- `structured_blob_motion`: kleine wandernde Blobs, Straße/Menschen/Autos-artig
- `fixed_fast_change_region`: schnelle feste Flächenänderung, TV-/Stream-artig
- `fixed_low_change_display_region`: ruhiges festes Display, z. B. Alexa Show mit Uhrzeit/Hintergrund
- `dark_static_region`: dunkle statische Fläche, z. B. ausgeschalteter TV
- `slow_drift_region`: langsame Helligkeitsdrift, z. B. Fenster/Tag-Nacht-Baseline

Eine Alexa-Show-Zelle darf bei seltenem Streaming zeitweise von `fixed_low_change_display_region` zu `fixed_fast_change_region` wechseln und später zurück. Das ist korrektes Verhalten und soll im Report sichtbar bleiben.

## P3a.1 / P3z0/P3z0.1 – Low-Change-Korrektur und Zoom-Kontext (Stand 2026-06-21)

P3a.1 korrigiert die erste P3a-Klassifikation: `low_change_region` wird von `fixed_low_change_display_candidate` getrennt. Ein kurzer ruhiger Messlauf darf nicht alle 36 Rasterzellen als Display-artig zählen. Display-Kandidaten werden erst sichtbar, wenn eine ruhige Region über mehrere Läufe stabil bleibt. Die bisherigen `ptz.motion.low_change_display.*`-Serien bleiben kompatibel, tragen aber nun die Kandidatenanzahl und nicht mehr alle ruhigen Zellen.

P3z0 beantwortet die Zoom-Frage vor jedem größeren Pan/Tilt-Sweep. Die EMEET PIXY meldete live Zoom 130/150; bei Zoom 100 war Außen-/Straßenbereich sichtbar. `tools/ptz_zoom_context_probe.py` vergleicht daher aktuellen Zoom und Wide-Zoom (Default 100), nutzt P3a.1-Klassen-Signaturen, schreibt `ptz.zoom_context.*` über DBWriter und stellt den alten Zoom zuverlässig zurück. P3z0.1 ergänzt nicht-semantische Wide-FOV-/Edge-/Bottom-Right-Kontextmetriken, damit zusätzlicher Rand-/Außenkontext nicht nur bei aktueller Bewegung sichtbar wird. Der stündliche Timer `oroma-ptz-zoom-context-probe.timer` sammelt wiederholte Evidenz über Tageszeiten. Erst wenn Wide-Zoom wiederholt hilfreich ist, sollte eine Wide-Observe-Policy diskutiert werden; Pan/Tilt-ViewMap-Sweep bleibt nachgelagert.


## P3z1b – Wide-Observe-Zoom-Policy Preview / Dry-Run (Stand 2026-06-25)

P3z1b führt noch keine produktive Zoom-Steuerung ein. Das neue Tool
`tools/ptz_zoom_policy_preview.py` berechnet nur, ob ORÓMA aktuell `zoom=100`
als Such-/Orientierungszoom empfehlen würde. Es liest read-only den
PTZ-Motor-State über `core.ptz_motor_state` und verwendet die realen Worker-
Felder `reason`, `action`, `raw_action`, `target_conf`, `obs_conf`,
`candidate.confidence` und `state_stale`.

Wichtig: `confidence=0.0` allein ist kein Trigger. Eligible sind nur explizite
Suchgründe wie `deadzone`, `stale_frame`, `no_frame` oder `energy_low`, und nur
wenn P3z0.1 über das beste Evidenzfenster Wide-Zoom als hilfreich bewertet.

Die Preview schreibt eigene DBWriter-Stats unter `ptz.zoom_policy.preview.*` und
legt `data/state/ptz_zoom_policy_preview_state.json` ab. Diese History ist der
Gate für eine spätere P3z1c-Auto-Zoom-Policy. Bis dahin bleiben `/video/` und
der PTZ-Worker read-only bzw. manuell kontrolliert.

## P3z1b Live-History und P3z1c Gate (Stand 2026-06-26)

Nach rund einem Tag Preview-History ist P3z1b fachlich bestätigt. Der Live-Report
zeigte 109 Samples mit `recommend_wide_count=20`. Die Empfehlungen traten nur bei
Such-/Orientierungszuständen auf: `deadzone=19` und `stale_frame=1`. Gleichzeitig
wurde bei aktiven oder potenziell aktiven Zielzuständen korrekt blockiert,
insbesondere bei `move_cooldown`, `target_hold`, `down_hold`, `stability_wait`,
`follow`, `micro_guard` und `energy_low` mit vorhandener `obs_conf`/
`candidate_conf`.

Damit ist die Preview nicht zu locker: Wide-Zoom-Evidence wird genutzt, aber
laufendes Tracking wird nicht gestört. P3z1c ergänzt deshalb eine erste streng
gegatete Auto-Zoom-Stufe: `tools/ptz_zoom_policy_apply.py`. Das Tool darf
ausschließlich `zoom_absolute` setzen, niemals Pan/Tilt/Focus, und nur wenn
P3z1b aktuell `recommend_wide_observe_zoom=true` liefert. Zusätzlich sind zwei
Freigaben nötig: CLI-Gate `--apply` und `OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1`.
Ohne beide Gates läuft P3z1c fail-closed und schreibt nur Apply-/No-op-Stats
unter `ptz.zoom_policy.apply.*`.

P3z1c bleibt dadurch reversibel und beobachtbar: State-Datei
`data/state/ptz_zoom_policy_apply_state.json`, DBWriter-Stats, Rate-Limit und
sichtbare Logs. Eine echte Auto-Zoom-Aktivierung darf erst nach manueller
Freigabe erfolgen. P3b0 ViewMap Sweep bleibt weiterhin nachgelagert.


## P3z1c Live-Apply und Zoom-100-Nachlauf (Stand 2026-06-29)

Der erste echte P3z1c-Hardware-Apply wurde auf `oromaki` kontrolliert validiert.
Ausgangspunkt war `zoom_absolute=130`; Ziel des Wide-Observe-Pfads war
`target_zoom=100`. Der Apply wurde nicht dauerhaft aktiviert, sondern als
begrenztes manuelles Doppel-Gate-Fenster ausgeführt:

```text
CLI-Gate: --apply
ENV-Gate: OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1
maximal 20 Versuche, 1 Versuch pro Minute
Stop bei applied=True
```

Live-Befund:

```text
TRY 1: preview_recommend=False, apply_allowed=False, applied=False, current_zoom=130
TRY 2: preview_recommend=True, apply_allowed=True, applied=True, current_zoom=100, target_zoom=100
final_decision=applied_wide_observe_zoom
reason=preview_recommend_wide:true;zoom_set_ok
APPLIED_OK_STOP
```

Die Hardware wurde anschließend über `/video/api/ptz/status` bestätigt:

```text
zoom_absolute.value = 100
```

Der Nachlauf bei Zoom 100 bestätigte die Schutzlogik. P3z1c erkannte den
bereits erreichten Zielzustand und wiederholte keine Zoom-Kommandos:

```text
already_at_target=True
final_decision=already_at_wide_observe_zoom
reason=preview_recommend_wide:true;env_apply_gate_closed;already_at_target_zoom
```

Gleichzeitig blieben unsichere/aktive Zustände gebremst:

```text
motor_reason=energy_low
preview_recommend=False
final_decision=hold_current_zoom
```

Die Observe-API zeigte später weiterhin `state=fail_closed`,
`apply_allowed_count=0`, `applied_count=0`, aber `current_zoom=100`. Das ist
konsistent mit dem manuellen Test: Der reale Apply wurde durch Tool-Log und
PTZ-Status bewiesen; der Apply-Stats-Write im manuellen Run meldete
`stats_write_error=timed out`. Dieser Timeout ist eine Observability-Lücke,
kein Freibrief für lokale SQLite-Fallbacks.

Bewertung:

- P3z1c kann `zoom_absolute` real setzen.
- P3z1c setzt nicht blind, sondern nur bei aktueller Preview-Freigabe.
- Der systemd-Timer bleibt fail-closed, solange das ENV-Gate geschlossen ist.
- Dauerhafte Auto-Zoom-Aktivierung ist weiterhin nicht freigegeben.
- P3b0 ViewMap Sweep bleibt nachgelagert, solange der Zoom-Pfad ausreichend
  Evidence liefert.


# ORÓMA PTZ Motor Worker

**Pfad:** `/opt/ai/oroma/docs/PTZ_MOTOR_WORKER.md`  
**Projekt:** ORÓMA – Offline-Realtime-Organic-Memory-AI  
**Version:** v3.8.0+ptz-motor-worker-v1.5c  
**Stand:** 2026-05-16  
**Status:** Phase 3a/3b – manueller systemd-Worker, Video-UI read-only, Target-Hold + Candidate/Axis-Lock + Eye-Pair-Salience mit False-Positive-Gates + Eye/Head-Hold-Bias + Expected-Face-Region/Head-Context + Face-assisted Motion-Radius

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


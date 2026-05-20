# ORÓMA – Roadmap: Stable Audio (Jabra Speak2 55 MS) – Headless, zuverlässig, token-sicher
Stand: 2026-01-09 (Europe/Berlin)
Baseline: ORÓMA v3.7.x/v3.8-ish – Jabra Speak2 55 MS (USB), Raspberry Pi 5, systemd/orchestrator

## 0) Ausgangslage / Status (IST)
- Hardware ist grundsätzlich OK:
  - `arecord -l` zeigt Capture (Card 0: Jabra … device 0)
  - `/proc/asound/pcm` zeigt `capture 1`
  - `snd-usb-audio` bindet korrekt (dmesg)
- ORÓMA Audio-Tokens funktionieren (Beweis aus DB):
  - `metrics`: `audio:token:candidate` + `audio:token:accepted`
  - `snapchains`: `origin='audio/token'` wird geschrieben
- Problemklasse:
  - sporadisch: „nur Rauschen“ (obwohl mic_active=true)
  - sporadisch: Audio-Devices leer / PortAudio listet 0 Devices (meist nach Service-Restart ohne Reboot)
  - Nebenwirkungen bei parallelen Tests: Device busy (normal, ORÓMA hält Stream offen)

## 1) Zielbild (SOLL)
Audio gilt als „stable“, wenn:
1) **Device-Enumeration** im Service-Kontext dauerhaft funktioniert (keine 0 Devices).
2) **Mic-Start** ist deterministisch (MIC: ON < 2s, kein Rauschen).
3) **WAV aus UI/API** enthält Sprache/Umgebung, nicht nur Noise.
4) **Tokens** laufen weiter (candidate/accepted) und sind im UI sichtbar.
5) **Recovery ohne Reboot** möglich (Hotplug/Restart = ok).

## 2) Messkriterien & Checks (Definition “stable”)
### 2.1 Live-Checks (schnell)
- API:
  - `/audio/api/status` liefert JSON (kein 404/HTML)
  - `devices.input/output` nicht leer
  - `mic_active=true` nach Start
- DB:
  - `audio:token:candidate` steigt in 2 Minuten
  - `audio:token:accepted` steigt (oder nachvollziehbare skip_* Gründe)
  - `snapchains origin='audio/token'` wächst

### 2.2 Minimal-Kommandos (copy/paste)
- Devices:
  - `arecord -l`
  - `aplay -l`
  - `cat /proc/asound/pcm`
- ORÓMA:
  - `curl -s http://127.0.0.1:8080/audio/api/status | python3 -m json.tool`
  - `sqlite3 ... "SELECT key,COUNT(*) FROM metrics WHERE key LIKE 'audio:token:%' AND ts>... GROUP BY key;"`

## 3) Stabilitäts-Hypothesen (warum es “kippelt”)
### H1) Service-Kontext / HostAPI / Timing
PortAudio kann im systemd-Kontext zeitweise keine Devices sehen (Start-Reihenfolge, HostAPI-Wahl, Default device invalid).
Symptom: devices = [] / mic_active=false / last_error “kein Input-Device gefunden”.

### H2) Stream-Format / Samplerate / dtype
USB-Audio kann “formal geöffnet” sein (z. B. 16k float) aber in der Praxis Müll liefern.
Symptom: mic_active=true + Pegel ändert sich + Aufnahme klingt wie Rauschen.

### H3) USB/Runtime-PM
In deinem aktuellen Dump: `power/control=on`, `runtime_status=active`.
=> Zurzeit nicht der Hauptschuldige, aber als “Hardening” trotzdem aufnehmen.

## 4) Hardening Roadmap (ohne sofortige Änderungen – in Phasen)
> Grundregel: Änderungen nur, wenn wir ein klares Symptom reproduzieren können.
> Jede Phase hat klare “Rollback”-Kante: eine Datei/Einheit, leicht rückgängig.

### Phase A – Observability & UI-Sicherheit (low risk)
Ziel: Ohne SSH sofort sehen, was wirklich ausgewählt/ geöffnet ist.
Deliverables:
- Audio-UI zeigt stabil:
  - `MIC: ON/OFF` (mic_active + fallback mic)
  - `in_dev_name / in_dev_index`
  - `mic_open_sr / target_sr / ch`
  - `last_error` aus DeviceHub
- `/audio` und `/audio/` beide erreichbar (redirect/alias)

Akzeptanz:
- Kein Einfluss auf Pipeline (nur Anzeige/Status).
- “OFF” Anzeige darf nie falsch sein, wenn mic_active=true.

### Phase B – Service Start/Restart Robustness (medium risk, aber effektiv)
Ziel: Kein “0 Devices” nach Restart.
Optionen:
1) systemd: `After=sound.target` / `Wants=sound.target`
2) user permissions: `SupplementaryGroups=audio` für den Service-User (falls nicht schon)
3) Audio-Init delay: kurze, definierte Probe-Schleife (z. B. 2s, 5 Versuche) bevor “no devices” gesetzt wird
4) HostAPI: fallback-Strategie (ALSA bevorzugt, falls sinnvoll)

Akzeptanz:
- Nach `systemctl restart` ist `/audio/api/devices` innerhalb 10s wieder nicht-leer.
- Kein Reboot erforderlich.

Rollback:
- Nur systemd unit revert / nur eine DeviceHub-Funktion revert.

### Phase C – Noise/Format Fix ohne “Zerlegen” (medium risk)
Ziel: “Rauschen” robust eliminieren.
Leitlinie: Eingangsstream konservativ (int16), dann intern float32.
Änderungen (konzeptionell):
- PortAudio InputStream: `dtype='int16'`
- Konvertierung: `pcm_f = pcm_i16.astype(np.float32) / 32768.0`
- Samplerate: bevorzugt device-native (typ. 48k) + resample auf target 16k

Akzeptanz:
- WAV aus `/audio/api/wav` klingt wie echte Aufnahme (Voice/Room), nicht Noise.
- Token-Qualität bleibt stabil (accepted nicht dramatisch sinkend).

Rollback:
- Ein einzelner Toggle (ENV) z. B. `OROMA_AUDIO_INPUT_DTYPE=int16|float32`
- Ein einzelner Toggle (ENV) `OROMA_AUDIO_OPEN_SR=48000|16000|auto`

### Phase D – Recovery/Healing (optional, high value)
Ziel: Autorecover ohne Reboot.
Mechaniken:
- Wenn `devices==[]` oder Stream liefert unplausible Daten -> “soft reset”:
  - Stream schließen
  - Devices neu query
  - neu öffnen (max 3 Versuche)
- Wenn über X Sekunden nur Noise erkannt:
  - heuristischer Check: RMS konstant, Spektrum “white-ish”, etc.
  - dann einmal neu initialisieren

Akzeptanz:
- Ein “kaputter Zustand” wird in <30s automatisch geheilt.
- Keine Endlosschleifen (Backoff + Cooldown).

### Phase E – Regression Tests / Selftest-Button (low/medium risk)
Ziel: Jede Änderung beweisbar, kein “Zufallsgefühl”.
Deliverables:
- `/audio/api/selftest` oder UI Button:
  1) open mic
  2) record 2s
  3) compute quick stats (rms/max/zero-crossing)
  4) return JSON + optional WAV sample
- DB selftest entry in metrics: `audio:selftest:ok|fail`

Akzeptanz:
- Bei ok: devices != [] + mic_active true + rms>min
- Bei fail: klare Fehlermeldung (kein Device / busy / invalid format)

## 5) Operational Best Practices (damit es 24/7 bleibt)
- Jabra möglichst an einem stabilen USB-Port/Hub (kein wackeliges Kabel).
- Wenn “Device busy” bei Tests: ORÓMA vorher stoppen oder `dsnoop`/virtuelle Capture-Layer nutzen.
- Keine parallelen Recorder im gleichen Device (sonst race/busy).
- Wenn nach Restart Devices leer: lieber einmal “re-enumerate” (UI refresh) + soft-reset (Phase D), nicht reboot.

## 6) Konkrete ToDo-Liste (für die nächste Session)
1) Snapshot: Logs + status + devices (wenn wieder “Rauschen” oder “0 Devices” auftaucht)
2) Phase B entscheiden: systemd Start/Permissions vs. DeviceHub Probe-Schleife
3) Phase C design: int16 input + 48k open + resample
4) Phase D: Soft-Reset Trigger definieren (heuristics + cooldown)

## 7) Notizen / Lessons Learned
- `power/control=on` & `runtime_status=active` => Autosuspend aktuell nicht verantwortlich.
- Der Unterschied zwischen PortAudio “(hw:0,0)” und ALSA `hw:CARD=MS,DEV=0` ist wichtig.
- Reboot kann USB-Audio sauber reinitialisieren, aber Ziel ist: **kein Reboot nötig**.
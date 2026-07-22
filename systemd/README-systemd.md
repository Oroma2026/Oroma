<!-- /opt/ai/oroma/systemd/README-systemd.md -->
# ORÓMA – systemd Architektur und Betrieb

## Zweck dieses Dokuments

Dieses Dokument beschreibt die Rolle der systemd-Units im aktuellen ORÓMA-
Projektstand. Es ersetzt keine einzelne Unit-Datei und nimmt keine Änderung an
Services oder Timern vor. Ziel ist die klare Einordnung der heutigen
Laufzeitarchitektur, insbesondere im Orchestrator-Modus.

## Architekturstatus 2026-06-26

ORÓMA wird im aktuellen Projektstand standardmäßig im **Orchestrator-Modus**
betrieben. Die Datei `/opt/ai/oroma/.use_orchestrator` ist dabei das zentrale
Systemsignal für die Laufzeitsteuerung.

Im Orchestrator-Modus gilt:

- `oroma-orchestrator.service` ist die autoritative Ablaufsteuerung für die
  periodischen ORÓMA-Jobs.
- `oroma.service` bleibt die zentrale UI-/Core-Basis und wird über Drop-ins für
  den Orchestrator-Betrieb konfiguriert.
- `oroma-db-writer.service` bleibt die autoritative Single-Writer-Komponente für
  produktive SQLite-Schreibpfade.
- Viele klassische OneShot-Services und Timer bleiben im Projekt erhalten,
  werden im Orchestrator-Modus aber bewusst über
  `ConditionPathExists=!/opt/ai/oroma/.use_orchestrator` übersprungen.
- Spezielle Runtime-/Evidence-Services, die bewusst neben dem Orchestrator
  laufen sollen, verwenden dagegen `ConditionPathExists=/opt/ai/oroma/.use_orchestrator`.

Dadurch existiert keine konkurrierende zweite Laufzeitsteuerung. Die älteren
Timer-/OneShot-Units bleiben als Legacy-, Debug-, Einzeltest- und Fallback-
Infrastruktur erhalten, ohne im Orchestrator-Modus doppelt gegen den
Orchestrator zu arbeiten.

## Heutige Rollenverteilung

```text
systemd/
├─ oroma.service
│  └─ Flask UI / Core-Basis / Drop-in-Konfiguration
│
├─ oroma-orchestrator.service
│  └─ autoritative periodische Ablaufsteuerung im Orchestrator-Modus
│
├─ oroma-db-writer.service
│  └─ globaler Single-Writer für SQLite-Schreibpfade
│
├─ klassische Timer + OneShot-Services
│  └─ Legacy/Fallback/Einzeltest; im Orchestrator-Modus überwiegend deaktiviert
│
└─ spezielle Runtime-/Evidence-Units
   └─ PTZ Worker, Reward Collector, PTZ Probes, NMR Binding Probe usw.
```

### Autoritative Services

- `oroma.service`  
  Startet die zentrale ORÓMA-UI/Core-Laufzeit und bindet die Drop-in-
  Konfigurationen unter `oroma.service.d/` ein.

- `oroma-orchestrator.service`  
  Ist im Orchestrator-Modus die autoritative periodische Steuerung für Jobs,
  die früher teilweise über einzelne Timer liefen.

- `oroma-db-writer.service`  
  Bleibt die autoritative Single-Writer-Komponente für produktive SQLite-
  Schreibpfade.

### Bewusst erhaltene Legacy-/Fallback-Units

Folgende Unit-Gruppen bleiben im Projekt, obwohl sie im Orchestrator-Modus nicht
als primäre Laufzeitsteuerung dienen:

- Dream / Policy / KPI / Stats / Gap Miner / ExportGate / Forgetting
- Replay / Archive / Health / Selftest
- RAMFlush / Repair / Trainings- und Spezialtimer

Zweck:

- Rückwärtskompatibilität mit Installationen ohne Orchestrator-Modus
- manuelle Einzeltests
- Debugging
- dokumentierte Historie der früheren systemd-Steuerung
- kontrollierter Fallback bei Bedarf

### PTZ- und Evidence-Units

PTZ-nahe Units sind bewusst gesondert zu betrachten. Sie sind keine doppelte
Implementierung der allgemeinen Orchestrator-Logik, sondern erfüllen konkrete
Runtime- oder Evidence-Aufgaben:

- `oroma-ptz-motor-worker.service`
- `oroma-ptz-motor-reward-collector.service`
- `oroma-ptz-positive-position-probe.service` / `.timer`
- `oroma-ptz-structured-motion-probe.service` / `.timer`
- `oroma-ptz-zoom-context-probe.service` / `.timer`
- `oroma-ptz-zoom-policy-preview.service` / `.timer`

Diese Units sind daher nicht als Replay-ähnliche Doppelstruktur zu bewerten.

## Installation / Betrieb

```bash
# Service- und Timer-Dateien nach /etc/systemd/system kopieren
sudo cp /opt/ai/oroma/systemd/*.service /etc/systemd/system/
sudo cp /opt/ai/oroma/systemd/*.timer   /etc/systemd/system/

# Drop-ins bei Bedarf ebenfalls kopieren
sudo mkdir -p /etc/systemd/system/oroma.service.d
sudo cp /opt/ai/oroma/systemd/oroma.service.d/*.conf /etc/systemd/system/oroma.service.d/

sudo mkdir -p /etc/systemd/system/oroma-orchestrator.service.d
sudo cp /opt/ai/oroma/systemd/oroma-orchestrator.service.d/*.conf /etc/systemd/system/oroma-orchestrator.service.d/

# systemd neu einlesen
sudo systemctl daemon-reload

# Hauptdienste aktivieren und starten
sudo systemctl enable --now oroma-db-writer.service
sudo systemctl enable --now oroma.service
sudo systemctl enable --now oroma-orchestrator.service
```

Nützliche Diagnosebefehle:

```bash
systemctl status oroma.service oroma-orchestrator.service oroma-db-writer.service --no-pager -l
journalctl -u oroma.service -n 120 --no-pager
journalctl -u oroma-orchestrator.service -n 120 --no-pager
systemctl list-timers 'oroma-*' --no-pager
```

## Historische Entwicklung

Ältere ORÓMA-Versionen nutzten stärker klassische systemd-Timer für einzelne
periodische Aufgaben. Diese Historie bleibt relevant, weil viele Units weiterhin
als Fallback-, Debug- oder Einzeltest-Infrastruktur im Projekt gepflegt werden.

Historische Beispiele:

- `oroma-replay.service` + `oroma-replay.timer` für stündliches Replay
- `oroma-health.service` + `oroma-health.timer` für Healthchecks
- `oroma-archive.service` + `oroma-archive.timer` für Archiv-/Backup-Läufe
- `oroma-selftest.service` + `oroma-selftest.timer` für tägliche Selbsttests
- frühere Export-Bezeichnungen wie `oroma-export.service`; im aktuellen Projekt
  ist `oroma-exportgate.service` maßgeblich

Diese historischen Rollen erklären, warum die Units nicht gelöscht werden. Im
aktuellen Orchestrator-Betrieb sind sie jedoch nicht die primäre Ablaufsteuerung,
sofern sie durch `ConditionPathExists=!/opt/ai/oroma/.use_orchestrator`
abgegrenzt sind.

## PTZ Positive Position Probe Timer (Stage-A Evidence)

- `oroma-ptz-positive-position-probe.service` führt
  `tools/ptz_positive_position_probe.py --once --write-stats --verbose` als
  OneShot aus.
- `oroma-ptz-positive-position-probe.timer` startet diese Probe alle 5 Minuten.
- Zweck: fortlaufende `ptz.marker.*`-Evidence in `stats.db.stats_points`; keine
  PTZ-Steuerung, keine Policy, keine Materialisierung.
- Aktivierung nach Kopieren der Units:
  `sudo systemctl daemon-reload; sudo systemctl enable --now oroma-ptz-positive-position-probe.timer`.

## PTZ Structured Motion Probe Timer (P3a Regional Temporal Signature Evidence)

- `oroma-ptz-structured-motion-probe.service` führt
  `tools/ptz_structured_motion_probe.py --once --write-stats --verbose` als
  OneShot aus.
- `oroma-ptz-structured-motion-probe.timer` startet diese Probe alle 5 Minuten.
- Zweck: fortlaufende `ptz.motion.*`-Evidence in `stats.db.stats_points`; keine
  PTZ-Steuerung, keine Policy, keine Materialisierung.
- Die Probe nimmt innerhalb jedes Laufs mehrere Samples in kurzer Folge auf und
  trennt regionale Zeitsignaturen wie `structured_blob_motion`,
  `fixed_fast_change_region`, `fixed_low_change_display_region`,
  `dark_static_region` und `slow_drift_region`.
- Aktivierung nach Kopieren der Units:
  `sudo systemctl daemon-reload; sudo systemctl enable --now oroma-ptz-structured-motion-probe.timer`.

## PTZ Zoom Context Probe (P3z0/P3z0.1)

`oroma-ptz-zoom-context-probe.service` / `.timer` führt einen kontrollierten
Wide-/Detail-Zoomvergleich aus. Der Lauf setzt den Zoom kurz auf den
konfigurierten Wide-Zoom (Default 100), misst P3a.1-Klassen-Signaturen plus
nicht-semantischen Wide-FOV-/Edge-/Bottom-Right-Kontextgewinn, stellt den
ursprünglichen Zoom wieder her und schreibt nur `ptz.zoom_context.*`-Stats via
DBWriter.

Der Timer läuft standardmäßig stündlich. Er ist kein Pan/Tilt-Sweep, kein
Tracking und keine Policy-Aktivierung. Zweck ist die wiederholte Evidenzfrage,
ob Wide-Zoom für Orientierung/Straßenkontext hilfreicher ist als der aktuelle
Detail-Zoom.

## PTZ Zoom Policy Preview (P3z1b)

Dateien:

```text
systemd/oroma-ptz-zoom-policy-preview.service
systemd/oroma-ptz-zoom-policy-preview.timer
```

Diese Units starten nur die Wide-Observe-Zoom-Policy-Preview. Das Tool steuert
keine Kamera, sondern liest P3z0.1-Evidence und den PTZ-Motor-State, schreibt
`ptz.zoom_policy.preview.*` via DBWriter und protokolliert eine Dry-Run-
Empfehlung für spätere Auswertung.

Aktivierung:

```bash
sudo cp systemd/oroma-ptz-zoom-policy-preview.service /etc/systemd/system/
sudo cp systemd/oroma-ptz-zoom-policy-preview.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oroma-ptz-zoom-policy-preview.timer
```

## Architekturreview 2026-06-26

Status: 🟢 Architektur bestätigt

Ergebnis:

- Keine Replay-ähnliche Doppelstruktur in `systemd/` festgestellt.
- Der Orchestrator-Modus ist die autoritative Laufzeitsteuerung für periodische
  ORÓMA-Jobs.
- Klassische Timer-/OneShot-Units bleiben bewusst erhalten, werden aber im
  Orchestrator-Modus über `ConditionPathExists` von der Primärsteuerung
  abgegrenzt.
- PTZ-/Evidence-Units sind gesonderte Runtime-/Messpfade und keine Duplikate der
  allgemeinen Orchestrator-Logik.
- Keine Änderung an `.service`- oder `.timer`-Dateien erforderlich.

Offene Hygiene-Beobachtung:

- Einige Unit-/Drop-in-Dateien können ausführbare Dateirechte besitzen. Das ist
  für systemd normalerweise nicht kritisch, erzeugt aber Warnungen bei
  `systemd-analyze verify`. Eine reine Dateirechte-Bereinigung kann später
  separat erfolgen, ohne die Unit-Inhalte zu ändern.

## PTZ Zoom Policy Apply (P3z1c gated auto-zoom)

Dateien:

```text
systemd/oroma-ptz-zoom-policy-apply.service
systemd/oroma-ptz-zoom-policy-apply.timer
```

Diese Units starten die streng gegatete Wide-Observe-Auto-Zoom-Stufe. Das Tool
setzt ausschließlich `zoom_absolute` und nur wenn P3z1b aktuell
`recommend_wide_observe_zoom=true` liefert. Zusätzlich sind zwei Gates nötig:

```text
ExecStart enthält --apply
OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=1
```

Die mitgelieferte Unit bleibt absichtlich fail-closed:

```text
Environment=OROMA_PTZ_ZOOM_AUTO_APPLY_ENABLE=0
```

Dadurch können State, Logs und `ptz.zoom_policy.apply.*`-Stats gesammelt werden,
ohne die Kamera zu verändern. Für echte Tests muss das ENV-Gate explizit
überschrieben werden. Pan/Tilt/Focus, Policy-Aktivierung und Materialisierung
sind ausgeschlossen.

Aktivierung der fail-closed Unit:

```bash
sudo cp systemd/oroma-ptz-zoom-policy-apply.service /etc/systemd/system/
sudo cp systemd/oroma-ptz-zoom-policy-apply.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oroma-ptz-zoom-policy-apply.timer
```


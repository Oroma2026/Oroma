# ORÓMA Deployment QuickStart / Schnellstart Deployment

## EN

### Purpose

This document describes a practical deployment-oriented quick start for a public ORÓMA repository clone.

It is intentionally focused on:

- Linux deployment
- environment files
- systemd-based startup
- safe public-repo usage

It is **not** a full live-system replication guide.

---

### 1. Clone the repository

```bash
git clone <CODEBERG_OR_GITHUB_URL>
cd oroma
```

---

### 2. Create local environment files

Copy the public templates:

```bash
cp .env.systemd.example .env.systemd
cp .env.example .env
```

Adjust them for the local host.

Typical local changes:

- UI token
- camera device path
- PTZ device path
- model directory
- optional audio configuration

Important:
Do not commit real secrets, real tokens, or private device paths back into the public repository.

---

### 3. Review systemd drop-ins

Important files usually include:

- `systemd/oroma.service.d/10-env.conf`
- `systemd/oroma.service.d/20-vision.conf`
- `systemd/oroma.service.d/30-ptz.conf`
- `systemd/oroma.service.d/35-audio.conf`
- `systemd/oroma.service.d/40-camera.conf`
- `systemd/oroma.service.d/90-orchestrator-master.conf`

Check whether your local machine really has:

- the expected camera
- the expected PTZ device
- the expected audio stack
- the intended orchestrator mode

---

### 4. Install service files

Example:

```bash
sudo cp systemd/oroma.service /etc/systemd/system/oroma.service
sudo mkdir -p /etc/systemd/system/oroma.service.d
sudo cp systemd/oroma.service.d/*.conf /etc/systemd/system/oroma.service.d/
sudo systemctl daemon-reload
```

If you also use the orchestrator:

```bash
sudo cp systemd/oroma-orchestrator.service /etc/systemd/system/oroma-orchestrator.service
sudo mkdir -p /etc/systemd/system/oroma-orchestrator.service.d
sudo cp systemd/oroma-orchestrator.service.d/*.conf /etc/systemd/system/oroma-orchestrator.service.d/
sudo systemctl daemon-reload
```

---

### 5. Start ORÓMA

Direct service start example:

```bash
sudo systemctl restart oroma.service
sudo systemctl status oroma.service --no-pager -l
```

If orchestrator mode is used:

```bash
sudo systemctl restart oroma-orchestrator.service
sudo systemctl status oroma-orchestrator.service --no-pager -l
```

---

### 6. Validate the environment

Check at least:

- service status
- logs
- UI accessibility
- phase/state files
- camera functionality
- PTZ functionality, if enabled

Useful examples:

```bash
sudo systemctl status oroma.service --no-pager -l
sudo systemctl status oroma-orchestrator.service --no-pager -l
tail -n 100 /opt/ai/oroma/logs/service.err.log
tail -n 100 /opt/ai/oroma/logs/orchestrator.err.log
```

---

### 7. Public repository rule

The public repository should remain clean.

Do not add:

- logs
- databases
- runtime state
- `.env`
- `.env.systemd`
- local credentials
- private host details

Keep those only on the deployed machine.

---

### 8. Recommended workflow

- public code/docs in Codeberg
- optional GitHub mirror
- public archive snapshots on Zenodo
- live instance remains separate and host-specific

---

## DE

### Zweck

Dieses Dokument beschreibt einen praktischen, deploymentsorientierten Schnellstart für einen öffentlichen ORÓMA-Repository-Clone.

Der Fokus liegt bewusst auf:

- Linux-Deployment
- Environment-Dateien
- systemd-basiertem Start
- sicherem Umgang mit dem öffentlichen Repo

Es ist **keine** vollständige Anleitung zur 1:1-Replikation eines Live-Systems.

---

### 1. Repository klonen

```bash
git clone <CODEBERG_OR_GITHUB_URL>
cd oroma
```

---

### 2. Lokale Environment-Dateien anlegen

Die öffentlichen Templates kopieren:

```bash
cp .env.systemd.example .env.systemd
cp .env.example .env
```

Danach lokal anpassen.

Typische lokale Änderungen:

- UI-Token
- Kamera-Gerätepfad
- PTZ-Gerätepfad
- Modellverzeichnis
- optionale Audio-Konfiguration

Wichtig:
Keine echten Secrets, Tokens oder privaten Gerätepfade zurück ins öffentliche Repository committen.

---

### 3. systemd-Drop-ins prüfen

Wichtige Dateien sind typischerweise:

- `systemd/oroma.service.d/10-env.conf`
- `systemd/oroma.service.d/20-vision.conf`
- `systemd/oroma.service.d/30-ptz.conf`
- `systemd/oroma.service.d/35-audio.conf`
- `systemd/oroma.service.d/40-camera.conf`
- `systemd/oroma.service.d/90-orchestrator-master.conf`

Prüfe, ob dein lokales System wirklich hat:

- die erwartete Kamera
- das erwartete PTZ-Gerät
- den erwarteten Audio-Stack
- den gewünschten Orchestrator-Modus

---

### 4. Service-Dateien installieren

Beispiel:

```bash
sudo cp systemd/oroma.service /etc/systemd/system/oroma.service
sudo mkdir -p /etc/systemd/system/oroma.service.d
sudo cp systemd/oroma.service.d/*.conf /etc/systemd/system/oroma.service.d/
sudo systemctl daemon-reload
```

Falls auch der Orchestrator genutzt wird:

```bash
sudo cp systemd/oroma-orchestrator.service /etc/systemd/system/oroma-orchestrator.service
sudo mkdir -p /etc/systemd/system/oroma-orchestrator.service.d
sudo cp systemd/oroma-orchestrator.service.d/*.conf /etc/systemd/system/oroma-orchestrator.service.d/
sudo systemctl daemon-reload
```

---

### 5. ORÓMA starten

Beispiel für den direkten Service-Start:

```bash
sudo systemctl restart oroma.service
sudo systemctl status oroma.service --no-pager -l
```

Falls Orchestrator-Modus verwendet wird:

```bash
sudo systemctl restart oroma-orchestrator.service
sudo systemctl status oroma-orchestrator.service --no-pager -l
```

---

### 6. Umgebung validieren

Mindestens prüfen:

- Service-Status
- Logs
- Erreichbarkeit der UI
- Phase-/State-Dateien
- Kamerafunktion
- PTZ-Funktion, falls aktiviert

Nützliche Beispiele:

```bash
sudo systemctl status oroma.service --no-pager -l
sudo systemctl status oroma-orchestrator.service --no-pager -l
tail -n 100 /opt/ai/oroma/logs/service.err.log
tail -n 100 /opt/ai/oroma/logs/orchestrator.err.log
```

---

### 7. Regel für das öffentliche Repository

Das öffentliche Repository sollte sauber bleiben.

Nicht hinzufügen:

- Logs
- Datenbanken
- Runtime-State
- `.env`
- `.env.systemd`
- lokale Zugangsdaten
- private Host-Details

Diese Dinge gehören nur auf die deployte Maschine.

---

### 8. Empfohlener Workflow

- öffentlicher Code / Doku auf Codeberg
- optionaler GitHub-Spiegel
- öffentliche Archiv-Snapshots auf Zenodo
- Live-Instanz bleibt getrennt und host-spezifisch

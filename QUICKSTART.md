# ORÓMA QuickStart / Schnellstart

## EN – What this file is for

This document provides a practical public-repository quick start for ORÓMA.

It is intended for readers who:
- cloned the public repository from Codeberg or GitHub
- want to understand the basic repository roles
- need safe starter templates for `.env` and `.env.systemd`
- want a minimal first local run without copying a private live system

---

## DE – Zweck dieser Datei

Dieses Dokument bietet einen praktischen Schnellstart für das öffentliche ORÓMA-Repository.

Es richtet sich an Leser, die:
- das öffentliche Repository von Codeberg oder GitHub geklont haben
- die Rollen der öffentlichen Repositories verstehen möchten
- sichere Startvorlagen für `.env` und `.env.systemd` brauchen
- einen ersten lokalen Start vorbereiten möchten, ohne ein privates Livesystem zu kopieren

---

## EN – Public references

- Whitepaper (EN, reference DOI): `10.5281/zenodo.19596002`
- Whitepaper (DE translation DOI): `10.5281/zenodo.19629298`
- Public software snapshot DOI: `10.5281/zenodo.20262590`

## DE – Öffentliche Referenzen

- Whitepaper (EN, Referenz-DOI): `10.5281/zenodo.19596002`
- Whitepaper (DE, Übersetzungs-DOI): `10.5281/zenodo.19629298`
- Öffentlicher Software-Snapshot DOI: `10.5281/zenodo.20262590`

---

## EN – Repository roles

- **Codeberg**: primary public repository
- **GitHub**: public mirror
- **Zenodo**: archival and citation reference

## DE – Rollen der Repositories

- **Codeberg**: primäres öffentliches Repository
- **GitHub**: öffentlicher Spiegel
- **Zenodo**: Archiv- und Zitationsreferenz

---

## EN – What the public repository should contain

Included:
- source code
- documentation
- public systemd files
- public templates
- release metadata

Excluded:
- logs
- databases
- live runtime state
- secrets
- real tokens
- private device paths unless intentionally published

## DE – Was das öffentliche Repository enthalten sollte

Enthalten:
- Quellcode
- Dokumentation
- öffentliche systemd-Dateien
- öffentliche Templates
- Release-Metadaten

Nicht enthalten:
- Logs
- Datenbanken
- Live-Runtime-State
- Geheimnisse / Secrets
- echte Tokens
- private Gerätepfade, sofern sie nicht bewusst veröffentlicht werden

---

## EN – Minimal checkout

```bash
git clone <CODEBERG_OR_GITHUB_URL>
cd oroma
```

## DE – Minimaler Checkout

```bash
git clone <CODEBERG_OR_GITHUB_URL>
cd oroma
```

---

## EN – Environment files

ORÓMA uses two layers of local configuration:

1. `.env.systemd`
2. `.env`

Typical loading order:
- `.env.systemd` first
- `.env` afterwards

This means:
- `.env.systemd` holds service-level defaults
- `.env` can override instance-specific values

## DE – Umgebungsdateien

ORÓMA nutzt zwei Ebenen lokaler Konfiguration:

1. `.env.systemd`
2. `.env`

Typische Lade-Reihenfolge:
- zuerst `.env.systemd`
- danach `.env`

Das bedeutet:
- `.env.systemd` enthält dienstnahe Basiswerte
- `.env` kann instanzspezifische Werte überschreiben

---

## EN – Create local files from templates

```bash
cp .env.systemd.example .env.systemd
cp .env.example .env
```

Then adjust:
- tokens
- local paths
- camera/PTZ/audio options
- runtime flags

## DE – Lokale Dateien aus Templates erzeugen

```bash
cp .env.systemd.example .env.systemd
cp .env.example .env
```

Danach anpassen:
- Tokens
- lokale Pfade
- Kamera-/PTZ-/Audio-Optionen
- Runtime-Flags

---

## EN – Minimal direct start

```bash
python3 run_oroma.py
```

## DE – Minimaler Direktstart

```bash
python3 run_oroma.py
```

---

## EN – systemd-oriented deployment note

If the host uses systemd deployment, install the service/drop-in files under `/etc/systemd/system/` and reload systemd afterwards.

Example:

```bash
sudo systemctl daemon-reload
sudo systemctl restart oroma.service
sudo systemctl status oroma.service --no-pager -l
```

If your host uses orchestrator mode, also review the related orchestrator service and drop-in files.

## DE – Hinweis für systemd-basierte Deployments

Wenn der Host systemd-Deployment nutzt, müssen die Service-/Drop-in-Dateien unter `/etc/systemd/system/` installiert und danach systemd neu geladen werden.

Beispiel:

```bash
sudo systemctl daemon-reload
sudo systemctl restart oroma.service
sudo systemctl status oroma.service --no-pager -l
```

Wenn dein Host den Orchestrator-Modus nutzt, prüfe zusätzlich die zugehörigen Orchestrator-Service- und Drop-in-Dateien.

---

## EN – Suggested reading order

1. `README.md`
2. `QUICKSTART.md`
3. `PROJECT_STRUCTURE.md`
4. `docs/architecture_audit.md`
5. Zenodo whitepaper

## DE – Empfohlene Reihenfolge zum Einlesen

1. `README.md`
2. `QUICKSTART.md`
3. `PROJECT_STRUCTURE.md`
4. `docs/architecture_audit.md`
5. Zenodo-Whitepaper

---

## EN – Public snapshot principle

The public repository is a safe, documented, reproducible public snapshot.

It should not be treated as a direct dump of a private live system.

## DE – Prinzip des öffentlichen Snapshots

Das öffentliche Repository ist ein sicherer, dokumentierter und reproduzierbarer öffentlicher Snapshot.

Es sollte nicht als direkter Dump eines privaten Livesystems behandelt werden.

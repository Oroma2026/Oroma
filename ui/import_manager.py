#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/import_manager.py
# Projekt:   ORÓMA (Offline-First · Headless · Import/Merge Pipeline)
# Modul:     ImportManager CLI – Import von Export-Bundles (tar/tar.gz) über core.import_gate (Merge/Dedupe, non-destructive)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# WARUM LIEGT DAS UNTER ui/ ?
# ───────────────────────────
# Historisch ist diese Datei als Tool/CLI entstanden (ähnlich „tools/import_manager.py“),
# liegt in diesem Repo-Stand aber unter ui/. Funktional ist es **kein Flask Blueprint**,
# sondern ein **headless CLI-Tool**.
#
# Bitte NICHT „umziehen“, ohne alle Aufrufer zu prüfen (systemd/orchestrator/scripts),
# sondern nur Header/Logik konsistent halten.
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Tool importiert ORÓMA Export-Bundles (z. B. aus /opt/ai/oroma/uploads)
# und delegiert die eigentliche Merge/Dedupe-Logik an:
#   core.import_gate.import_bundle(path)
#
# Typische Verwendung:
# - Upload eines Bundles (z. B. via /export UI Upload oder manuell scp)
# - Anschließend Import per CLI (dieses Tool) oder orchestrator/systemd
#
# Designziele:
# - Headless (keine UI nötig)
# - Robust (fehlende Dateien/Fehler werden geloggt, Prozess bleibt stabil)
# - Non-destructive: Import bedeutet „zusammenführen“, nicht löschen
#
# ABHÄNGIGKEITEN (EXAKT IM CODE)
# ──────────────────────────────
# - stdlib: os, sys, glob, logging, pathlib.Path
# - core.import_gate (optional)
#
# Wenn core.import_gate nicht importierbar ist:
# - import_gate = None
# - import_file() loggt einen Fehler und gibt False zurück (kein Crash)
#
# PFADE / VERZEICHNISSE (ENV + DEFAULTS)
# ─────────────────────────────────────
# BASE      = ENV["OROMA_BASE_DIR"]   oder "/opt/ai/oroma"
# UPLOAD_DIR= ENV["OROMA_UPLOAD_DIR"] oder f"{BASE}/uploads"
# LOG_DIR   = ENV["OROMA_LOG_DIR"]    oder f"{BASE}/logs"
#
# Beim Start:
# - LOG_DIR wird mit parents=True angelegt (mkdir exist_ok=True)
# - logging.basicConfig schreibt in:
#     {LOG_DIR}/import_manager.log
#
# LOGGING (PRODUKTIONSWICHTIG)
# ────────────────────────────
# Logger Name: "oroma.import_manager"
# - Level: INFO
# - Format: "%(asctime)s [%(levelname)s] %(message)s"
# - Datei: logs/import_manager.log
#
# Damit ist das Tool unabhängig von systemd-journal, aber kompatibel zu Orchestrator
# (Orchestrator kann Logfiles zusätzlich tailen).
#
# FUNKTIONEN (ÖFFENTLICH / STABIL)
# ───────────────────────────────
# import_file(path: Path) -> bool
#   - prüft:
#       • import_gate vorhanden?
#       • Datei existiert?
#   - ruft dann:
#       stats = import_gate.import_bundle(str(path))
#   - loggt:
#       "Import erfolgreich: <path> → <stats>"
#   - Fehler → loggt "Fehler beim Import ..." und gibt False zurück
#
# import_all(pattern: str="oroma_export_*.tar.gz") -> int
#   - sucht im UPLOAD_DIR nach Bundles (glob)
#   - sortiert stabil (sorted)
#   - importiert nacheinander via import_file()
#   - Rückgabe: Anzahl erfolgreich importierter Bundles
#
# CLI / MAIN (AUFRUFVERHALTEN)
# ────────────────────────────
# main():
#   - Wenn Argumente übergeben wurden:
#       jedes Argument wird als glob Pattern interpretiert
#       (z. B. "/opt/ai/oroma/uploads/oroma_export_*.tar.gz")
#   - Wenn keine Argumente:
#       import_all() im UPLOAD_DIR
#       stdout: "[import_manager] <n> Bundles importiert"
#
# BEISPIELE (COPY/PASTE)
# ──────────────────────
# 1) Import eines einzelnen Bundles:
#   python3 /opt/ai/oroma/ui/import_manager.py /opt/ai/oroma/uploads/oroma_export_20260101_*.tar.gz
#
# 2) Import aller Bundles im Upload-Ordner (Default):
#   python3 /opt/ai/oroma/ui/import_manager.py
#
# 3) Mehrere Patterns:
#   python3 /opt/ai/oroma/ui/import_manager.py "/opt/ai/oroma/uploads/oroma_export_*.tar.gz" "/tmp/oroma_export_*.tar.gz"
#
# BUNDLE-ERWARTUNGEN (FORMAT)
# ───────────────────────────
# Dieses Tool validiert das Bundle NICHT selbst; es reicht den Pfad an import_gate weiter.
# import_gate ist verantwortlich für:
#   - Lesen meta.json
#   - Einlesen snapchains/<id>.json oder andere Inhalte
#   - Dedupe (feature-hash / origin_instance_id / policy)
#   - Schreiben in oroma.db (und ggf. stats.db)
#
# SICHERHEIT / GOVERNANCE
# ───────────────────────
# - Dieses Tool löscht keine Dateien.
# - Es verschiebt Bundles nicht automatisch.
# - Optionales „Post-Import“-Archivieren wäre ein separates, bewusstes Tool (nicht hier).
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - import_gate ist optional; Fehlen darf Boot/Run nicht crashen.
# - Logging in eigene Datei muss bleiben (leichter Remote-Debug).
# - Default-Import (UPLOAD_DIR) muss bleiben (Orchestrator/Jobs verlassen sich darauf).
# - Keine destruktiven Aktionen (kein Delete/Move) ohne explizite neue Policy.
#
# =============================================================================
# END HEADER
# =============================================================================

import os
import sys
import glob
import logging
from pathlib import Path

try:
    from core import import_gate
except ImportError:
    import_gate = None

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------
BASE = Path(os.environ.get("OROMA_BASE_DIR", "/opt/ai/oroma"))
UPLOAD_DIR = Path(os.environ.get("OROMA_UPLOAD_DIR", BASE / "uploads"))
LOG_DIR = Path(os.environ.get("OROMA_LOG_DIR", BASE / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "import_manager.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
LOG = logging.getLogger("oroma.import_manager")

# -----------------------------------------------------------------------------
# Funktionen
# -----------------------------------------------------------------------------
def import_file(path: Path) -> bool:
    if not import_gate:
        LOG.error("import_gate Modul fehlt")
        return False
    if not path.exists():
        LOG.error("Datei nicht gefunden: %s", path)
        return False
    try:
        stats = import_gate.import_bundle(str(path))
        LOG.info("Import erfolgreich: %s → %s", path, stats)
        return True
    except Exception as e:
        LOG.error("Fehler beim Import %s: %s", path, e)
        return False


def import_all(pattern: str = "oroma_export_*.tar.gz") -> int:
    files = sorted(UPLOAD_DIR.glob(pattern))
    if not files:
        LOG.info("Keine Importdateien gefunden im %s", UPLOAD_DIR)
        return 0
    count = 0
    for f in files:
        if import_file(f):
            count += 1
    return count

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main():
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            for path in glob.glob(arg):
                import_file(Path(path))
    else:
        # Default: alle im Upload-Verzeichnis
        n = import_all()
        print(f"[import_manager] {n} Bundles importiert")


if __name__ == "__main__":
    main()
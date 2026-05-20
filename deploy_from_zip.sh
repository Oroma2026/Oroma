sudo tee /opt/ai/oroma/v2.11/deploy_from_zip.sh >/dev/null <<'SH'
# (füge hier den gesamten Skriptinhalt ein)
#!/usr/bin/env bash
# deploy_from_zip.sh — One-shot Installer für ORÓMA v2.11 aus einer ZIP
# Usage:
#   sudo bash deploy_from_zip.sh --zip /pfad/Archiv080925.zip [--base /opt/ai/oroma/v2.11] [--with-dev] [--nginx]
#
# Idempotent: wiederholbares Ausführen ok (überschreibt Dateien, behält DB/Backups).
set -euo pipefail

# ----------------------------- Konfig/Defaults ------------------------------
BASE_DIR="/opt/ai/oroma/v2.11"
ZIP_PATH=""
WITH_DEV="false"
SETUP_NGINX="false"
PY_BIN="${PY_BIN:-python3}"      # überschreibbar per Env
PIP_OPTS="${PIP_OPTS:-}"         # z.B. "--index-url ..."

# ----------------------------- Helpers --------------------------------------
log() { printf "\033[1;32m[+] %s\033[0m\n" "$*"; }
warn(){ printf "\033[1;33m[!] %s\033[0m\n" "$*"; }
die() { printf "\033[1;31m[✗] %s\033[0m\n" "$*" ; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || die "fehlendes Kommando: $1"; }

usage(){
  cat <<EOF
Usage: sudo bash $0 --zip /pfad/ArchivXXXX.zip [--base /opt/ai/oroma/v2.11] [--with-dev] [--nginx]

Optionen:
  --zip PATH         Pfad zur ORÓMA-ZIP (erforderlich)
  --base DIR         Zielverzeichnis (Default: ${BASE_DIR})
  --with-dev         installiert zusätzlich requirements-dev.txt
  --nginx            richtet einfachen Reverse-Proxy auf :8080 ein
  --help             diese Hilfe
Env-Variablen:
  PY_BIN             Python-Binary (Default: python3)
  PIP_OPTS           zusätzliche pip-Optionen (Proxy, Index, ...)

Beispiel:
  sudo bash $0 --zip /tmp/Archiv080925.zip --with-dev --nginx
EOF
}

# ----------------------------- Args -----------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --zip) ZIP_PATH="$2"; shift 2;;
    --base) BASE_DIR="$2"; shift 2;;
    --with-dev) WITH_DEV="true"; shift 1;;
    --nginx) SETUP_NGINX="true"; shift 1;;
    --help|-h) usage; exit 0;;
    *) die "Unbekannte Option: $1 (nutze --help)";;
  esac
done

[[ -n "$ZIP_PATH" ]] || { usage; die "--zip ist erforderlich"; }
[[ -f "$ZIP_PATH" ]] || die "ZIP nicht gefunden: $ZIP_PATH"
[[ $EUID -eq 0 ]] || die "Bitte als root/sudo ausführen"

# ----------------------------- Checks ---------------------------------------
need unzip
need $PY_BIN
need systemctl
need ffmpeg || warn "ffmpeg fehlt (Whisper/Audio optional)"
if [[ "$SETUP_NGINX" == "true" ]]; then need nginx; fi

log "Zielverzeichnis: $BASE_DIR"
mkdir -p "$BASE_DIR"

# ----------------------------- Entpacken ------------------------------------
log "Entpacke ZIP → $BASE_DIR"
unzip -oq "$ZIP_PATH" -d "$BASE_DIR"

# Sanity: zentrale Dateien?
for f in run_oroma.py requirements.txt ui/flask_ui.py core/sql_manager.py; do
  [[ -f "$BASE_DIR/$f" ]] || die "Erwartete Datei fehlt nach dem Entpacken: $f"
done

# ----------------------------- Verzeichnisse --------------------------------
log "Erzeuge Basisverzeichnisse"
mkdir -p "$BASE_DIR/database" "$BASE_DIR/logs" "$BASE_DIR/models" "$BASE_DIR/exports" "$BASE_DIR/systemd" "$BASE_DIR/cron"

# ----------------------------- venv + pip -----------------------------------
log "Richte venv ein"
if [[ ! -d "$BASE_DIR/venv" ]]; then
  $PY_BIN -m venv "$BASE_DIR/venv"
fi
# shellcheck disable=SC1091
source "$BASE_DIR/venv/bin/activate"
python -m pip install -U pip wheel setuptools
log "Installiere requirements.txt"
pip install $PIP_OPTS -r "$BASE_DIR/requirements.txt"
if [[ "$WITH_DEV" == "true" && -f "$BASE_DIR/requirements-dev.txt" ]]; then
  log "Installiere requirements-dev.txt"
  pip install $PIP_OPTS -r "$BASE_DIR/requirements-dev.txt"
fi

# ----------------------------- .env -----------------------------------------
ENV_FILE="$BASE_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  log "Erzeuge .env (Standardwerte)"
  cat > "$ENV_FILE" <<'EOF'
# ORÓMA v2.11 – .env
OROMA_BASE_DIR=/opt/ai/oroma/v2.11
OROMA_UI_TOKEN=oroma_ui_demo_token
OROMA_DB=/opt/ai/oroma/v2.11/database/oroma.db
OROMA_MODELS=/opt/ai/oroma/v2.11/models
OROMA_BACKEND_PREF=auto
OROMA_FAILOVER=true
FLASK_RUN_HOST=127.0.0.1
FLASK_RUN_PORT=8080
EOF
else
  log ".env existiert – unverändert"
fi

# ----------------------------- DB Schema ------------------------------------
log "Initialisiere Datenbank-Schema"
python - <<'PY'
import os, sys
base = os.environ.get("OROMA_BASE_DIR","/opt/ai/oroma/v2.11")
sys.path.insert(0, base)
from core.sql_manager import ensure_schema, get_db_path
ensure_schema()
print("DB:", get_db_path(), "bereit")
PY

# ----------------------------- systemd ---------------------------------------
SYS_DIR="/etc/systemd/system"
log "Installiere systemd Units/Timer"
install_service(){
  local unit="$1"
  if [[ -f "$BASE_DIR/systemd/$unit" ]]; then
    install -m 0644 "$BASE_DIR/systemd/$unit" "$SYS_DIR/$unit"
  else
    warn "Unit fehlt in Projekt: $unit"
  fi
}
install_service "oroma.service"
install_service "oroma-exportgate.service"
install_service "oroma-exportgate.timer"
install_service "oroma-health.service"
install_service "oroma-health.timer"
install_service "oroma-replay.service"
install_service "oroma-replay.timer"
install_service "oroma-archive.service"
install_service "oroma-archive.timer"

log "systemd reload/enable/start"
systemctl daemon-reload
systemctl enable oroma.service || true
for t in oroma-exportgate.timer oroma-health.timer oroma-replay.timer oroma-archive.timer; do
  systemctl enable "$t" || true
done
systemctl restart oroma.service
for t in oroma-exportgate.timer oroma-health.timer oroma-replay.timer oroma-archive.timer; do
  systemctl restart "$t" || true
done

# ----------------------------- NGINX (optional) ------------------------------
if [[ "$SETUP_NGINX" == "true" ]]; then
  log "Richte einfachen NGINX-Proxy auf :8080 ein"
  cat > /etc/nginx/sites-available/oroma <<'NGX'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGX
  ln -sf /etc/nginx/sites-available/oroma /etc/nginx/sites-enabled/oroma
  nginx -t && systemctl reload nginx
fi

# ----------------------------- Summary --------------------------------------
log "Deployment abgeschlossen!"
echo "Service-Status:  sudo systemctl status oroma.service"
echo "Logs:            tail -f $BASE_DIR/logs/service.out.log"
echo "UI:              http://<HOST>/"
echo "Health:          curl -s http://127.0.0.1:8080/health || true"
SH
sudo chmod +x /opt/ai/oroma/v2.11/deploy_from_zip.sh
#!/usr/bin/env bash
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/backup_oroma_with_db.sh
# Projekt: ORÓMA – Backup-Skript (komprimiertes Projekt-Backup mit selektivem Staging, Slim-DBs und Log-Truncation)
# Version: v2.7
# Stand:   2026-04-08
# Autor:   Jörg + GPT-5.4 Thinking
# =============================================================================
#
# Zweck
# -----
# Erstellt ein komprimiertes ZIP-Archiv des produktiven ORÓMA-Verzeichnisses
# inklusive *reduzierter* SQLite-Datenbanken und gestutzter Log-Dateien.
# Das Skript ist für produktive Live-Systeme gedacht, bei denen die großen
# Datenbanken im Backup nicht vollständig, sondern kontrolliert und reproduzierbar
# als kompakte Slim-Kopie mitgeführt werden sollen.
#
#   Quelle: /opt/ai/oroma
#   Ziel:   /opt/ai/oroma_backups/oroma_YYYYMMDD_HHMMSS_with_db.zip
#
# Hinweis zur Dateiablage
# ----------------------
# Diese Datei liegt produktiv *immer* im ORÓMA-Repo unter:
#   /opt/ai/oroma/tools/backup_oroma_with_db.sh
# Zusätzlich existiert bei dir häufig eine Kopie unter:
#   /opt/ai/backup_oroma_with_db.sh
# Die Repo-Version ist die Referenz und Patch-Basis.
#
# Architektur / Arbeitsweise
# --------------------------
# - Slim ist IMMER aktiv – für ALLE DBs in /opt/ai/oroma/data:
#     *.db, *.sqlite
#   inkl. knowledge.db, stats.db, oroma.db usw.
#
# - Die produktiven Live-DBs werden NICHT vollständig kopiert, sondern direkt
#   gelesen. Für das Backup wird pro DB eine kleine Ziel-DB erzeugt, die nur
#   die letzten N Zeilen je Tabelle enthält.
#
# - Ab v1.9 wird bewusst KEIN COUNT(*) pro Tabelle mehr ausgeführt.
#   Hintergrund: Gerade auf großen SQLite-Dateien (z.B. oroma.db / stats.db)
#   kostet COUNT(*) über viele Tabellen spürbar Zeit. Stattdessen werden die
#   letzten N Zeilen direkt über ORDER BY ... DESC LIMIT N gelesen.
#   Das reduziert die Backup-Laufzeit deutlich, ohne das Slim-Prinzip zu ändern.
#
# - Ab v2.0 wird das Repo nicht mehr als nahezu vollständige Live-Kopie ins
#   Staging gespiegelt. Große Live-Bereiche werden gezielt ausgeschlossen:
#     * archives/
#     * data/**   (die Slim-DBs werden danach gezielt neu erzeugt)
#     * logs/ und log/ (werden danach trunkiert neu erzeugt)
#     * state/ und data/state/ Runtime-Artefakte
#     * Root-Runtime-Dateien wie .lgd-*
#   Das vermeidet doppelte I/O auf sehr großen Live-Datenbeständen.
#
# - Die Slim-Ziel-DB wird direkt im Staging erzeugt. Ein zusätzlicher temporärer
#   Zwischenkopier-Schritt für die erzeugte Mini-DB entfällt damit bewusst.
#
# - FTS/Virtual-Table Fix:
#   Bei FTS5 erzeugt "CREATE VIRTUAL TABLE ..." automatisch Shadow-Tabellen
#   (z.B. chunks_content, chunks_data, chunks_docsize, chunks_config, chunks_idx).
#   Wenn man diese Shadow-Tabellen danach erneut "CREATE TABLE ..."t, kommen
#   Warnungen "table already exists".
#   → Das Skript erkennt FTS-Virtual-Tables und überspringt ihre Shadow-Tabellen.
#
# - Logs werden nicht 1:1 ins Staging kopiert. Stattdessen werden die Logs aus
#   dem Live-Verzeichnis direkt auf die letzten N Zeilen reduziert.
#   Das verhindert "No space left on device" bei großen Logs, insbesondere wenn
#   /tmp als tmpfs gemountet ist.
#
# - Ab v2.1 protokolliert das Skript pro Hauptschritt Start-/Endzeit und die
#   gemessene Dauer in Sekunden. Damit lässt sich auf dem Livesystem sofort
#   erkennen, ob Staging, DB-Slim, Log-Truncation oder ZIP der Engpass ist.
#
# - Ab v2.2 protokolliert die Slim-Phase zusätzlich Laufzeiten pro Datenbank
#   sowie ein Langsamkeitslog pro Tabelle. Damit kann auf dem Livesystem direkt
#   erkannt werden, welche konkrete DB bzw. welche konkrete Tabelle die Slim-Zeit
#   verursacht. Das Tabellen-Log ist bewusst schwellenbasiert, damit die Ausgabe
#   produktiv lesbar bleibt und nicht jede kleine Tabelle den Backup-Lauf flutet.
#
# - Ab v2.4 wird die Quell-DB fuer die Slim-Phase explizit read-only angehaengt
#   und die Ziel-DB-PRAGMAs werden ausschliesslich auf `main` gesetzt. Hintergrund:
#   auf stark aktiven Live-DBs wie oroma.db kann ein unqualifiziertes
#   `PRAGMA journal_mode=OFF` nach `ATTACH DATABASE` zu Lock-Konflikten fuehren.
#   Zusaetzlich wird das ATTACH bei transienten SQLite-Locks kurz retryt, damit
#   ein laufender Writer das Backup nicht sofort scheitern laesst.
#
# - Ab v2.6 wird am Ende zusaetzlich die Gesamtlaufzeit sowie die resultierende
#   ZIP-Groesse ausgegeben. Damit sind direkte Lauf- und Groessenvergleiche
#   zwischen verschiedenen Limits oder Patch-Staenden ohne Zusatzbefehle moeglich.

# - Ab v2.7 wird die hostseitige Diagnose-Datei unter ${BACKUP_BASE}/_staging
#   nach erfolgreichem Backup wieder entfernt, damit dort keine losen Altdateien
#   liegen bleiben. Die Diagnose verbleibt weiterhin im ZIP unter
#   logs/backup_backup_diag.log.
#
# - Ebenfalls ab v2.7 werden nach erfolgreicher ZIP-Erzeugung die Live-Logdateien
#   unter /opt/ai/oroma per `find ... -name "*.log" -exec truncate -s 0 {} \;`
#   auf 0 gesetzt. Hintergrund: Die relevanten letzten Logzeilen sind bereits im
#   Backup enthalten; das Live-System wird danach direkt wieder mit leeren Logs
#   weiterbetrieben.
#
# Per-Table Slim Limits
# ---------------------
# Manche Tabellen können trotz globalem DB_LIMIT sehr groß werden, z.B. durch
# große JSON/Text-Felder wie scenegraphs.graph_json. Deshalb erlaubt
# DB_TABLE_LIMITS gezielte Limits pro Tabelle, ohne den globalen Wert für alle
# anderen Tabellen abzusenken. Zusätzlich kann DB_EXCLUDE_TABLES komplette
# Tabelleninhalte überspringen (0 Zeilen), während das Schema erhalten bleibt.
#
# Default-Verhalten dieser Version
# --------------------------------
# - Globales Default-Limit: 30 Zeilen pro Tabelle
# - Logs: standardmäßig 1000 Zeilen
# - scenegraphs: Default ebenfalls 30 Zeilen
#
# Ziel dieser Defaults ist ein deutlich kleineres und schnelleres komprimiertes
# Backup. Bei Bedarf können die Limits weiterhin per Environment bewusst höher
# gesetzt werden.
#
# Nutzung
# -------
#   chmod +x /opt/ai/oroma/tools/backup_oroma_with_db.sh
#   sudo /opt/ai/oroma/tools/backup_oroma_with_db.sh
#
#   # (Optional) Repo-Version als globale Kopie ablegen:
#   sudo cp -f /opt/ai/oroma/tools/backup_oroma_with_db.sh /opt/ai/backup_oroma_with_db.sh
#
# Konfiguration
# -------------
#   DB_LIMIT=30            (Default max Zeilen je Tabelle)
#   DB_TABLE_LIMITS=...     (Per-Tabelle Override, z.B. "scenegraphs=30,snapchains=200")
#   DB_EXCLUDE_TABLES=...   (Daten-Copy überspringen, z.B. "scenegraphs,objectgraph_edges")
#   LOG_LIMIT=1000         (Default max Zeilen je *.log; unabhängig vom DB-Limit)
#   SLOW_TABLE_SEC=1       (Default: ab dieser Dauer wird eine Tabelle als langsam geloggt)
#   DB_ORDER_OVERRIDES=... (Per-Tabelle Order-Override, z.B. "metrics=rowid")
#
# Abhängigkeiten
# --------------
#   - zip
#   - python3
#   - optional rsync (schneller)
# =============================================================================

set -euo pipefail

SCRIPT_T0="$(date +%s)"

# --------------------------------------
# Konfiguration
# --------------------------------------
OROMA_DIR="/opt/ai/oroma"
BACKUP_BASE="/opt/ai/oroma_backups"

DB_LIMIT="${DB_LIMIT:-30}"
LOG_LIMIT="${LOG_LIMIT:-1000}"
SLOW_TABLE_SEC="${SLOW_TABLE_SEC:-1}"
DB_ORDER_OVERRIDES="${DB_ORDER_OVERRIDES:-metrics=rowid}"

# Per-Table Overrides (v1.9)
# --------------------------
# Problem: einzelne Tabellen können trotz DB_LIMIT extrem groß werden, wenn
#         große JSON/Text-BLOBs gespeichert werden (z.B. scenegraphs.graph_json).
# Lösung:  DB_TABLE_LIMITS erlaubt Limits pro Tabelle, ohne den globalen
#         DB_LIMIT für alle anderen Tabellen zu ändern.
#
# Default (bewusst klein): scenegraphs=30
#   -> reduziert Backup-Größe drastisch, ohne Schema zu verlieren.
#
# Format:
#   DB_TABLE_LIMITS="tableA=1000,tableB=50"
#   DB_EXCLUDE_TABLES="tableC,tableD"   # kopiert 0 Zeilen, Schema bleibt
#
# Hinweis: Leerer String deaktiviert die jeweilige Funktion.
DB_TABLE_LIMITS="${DB_TABLE_LIMITS:-scenegraphs=30}"
DB_EXCLUDE_TABLES="${DB_EXCLUDE_TABLES:-}"

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_BASE}/oroma_${TS}_with_db.zip"

DATA_DIR="${OROMA_DIR}/data"

# -----------------------------------------------------------------------------
# STAGING-Strategie (v1.7)
# -----------------------------------------------------------------------------
# Hintergrund:
#   Viele Systeme mounten /tmp als tmpfs (RAM). Wenn einzelne Log-Dateien sehr
#   groß werden (mehrere GB), kann ein rsync/cp in /tmp scheitern:
#     "No space left on device"
#   obwohl genügend Platz auf der Root-Partition frei wäre.
#
# Lösung:
#   - Standardmäßig stage'n wir auf die Root-Partition unter BACKUP_BASE.
#   - /tmp kann weiterhin genutzt werden, wenn explizit gewünscht:
#       STAGING_BASE=/tmp
#
# Umgebungsvariablen:
#   STAGING_BASE   Basis-Verzeichnis für Staging und Slim-DB-Tmp
#                 Default: ${BACKUP_BASE}/_staging
# -----------------------------------------------------------------------------
STAGING_BASE="${STAGING_BASE:-${BACKUP_BASE}/_staging}"
STAGING_DIR="${STAGING_BASE}/oroma_backup_stage_${TS}"
DIAG_LOG_HOST="${BACKUP_BASE}/_staging/oroma_backup_diag_${TS}.log"
DIAG_LOG_STAGE_REL="logs/backup_backup_diag.log"

mkdir -p "${BACKUP_BASE}"
mkdir -p "${STAGING_BASE}"

# Alle Konsolenausgaben dieses Laufs parallel in eine separate Diagnose-Datei
# schreiben. Die Datei wird spaeter vor dem ZIP bewusst ins Staging uebernommen,
# damit die Laufanalyse direkt im Backup-Archiv enthalten ist.
exec > >(tee -a "${DIAG_LOG_HOST}") 2>&1

echo "[backup_oroma_with_db] Starte Backup von ${OROMA_DIR}"
echo "[backup_oroma_with_db] Ziel: ${BACKUP_FILE}"
echo "[backup_oroma_with_db] DB_LIMIT=${DB_LIMIT} | LOG_LIMIT=${LOG_LIMIT}"
echo "[backup_oroma_with_db] SLOW_TABLE_SEC=${SLOW_TABLE_SEC}"
echo "[backup_oroma_with_db] DB_TABLE_LIMITS=${DB_TABLE_LIMITS:-""}"
echo "[backup_oroma_with_db] DB_EXCLUDE_TABLES=${DB_EXCLUDE_TABLES:-""}"
echo "[backup_oroma_with_db] DB_ORDER_OVERRIDES=${DB_ORDER_OVERRIDES:-""}"

format_bytes() {
  local bytes="${1:-0}"
  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec-i --suffix=B "${bytes}"
  else
    python3 - <<'PY' "${bytes}"
import sys
size=int(sys.argv[1]) if len(sys.argv) > 1 else 0
units=["B","KiB","MiB","GiB","TiB"]
i=0
val=float(size)
while val >= 1024 and i < len(units)-1:
    val/=1024.0
    i+=1
print(f"{val:.1f}{units[i]}" if i else f"{int(val)}{units[i]}")
PY
  fi
}

now_human() {
  date '+%F %T'
}

step_begin() {
  STEP_NAME="$1"
  STEP_T0="$(date +%s)"
  echo "[backup_oroma_with_db] [$(now_human)] START ${STEP_NAME}"
}

step_end() {
  local rc=$?
  local t1 dur
  t1="$(date +%s)"
  dur=$(( t1 - STEP_T0 ))
  if [ $rc -eq 0 ]; then
    echo "[backup_oroma_with_db] [$(now_human)] ENDE  ${STEP_NAME} | Dauer: ${dur}s"
  else
    echo "[backup_oroma_with_db] [$(now_human)] FEHLER ${STEP_NAME} | Dauer bis Fehler: ${dur}s | rc=${rc}" >&2
  fi
  return $rc
}


# --------------------------------------
# Checks
# --------------------------------------
if ! command -v zip >/dev/null 2>&1; then
  echo "[backup_oroma_with_db] Fehler: 'zip' ist nicht installiert. Bitte 'apt install zip' ausführen." >&2
  exit 1
fi

if [ ! -d "${OROMA_DIR}" ]; then
  echo "[backup_oroma_with_db] Fehler: Verzeichnis ${OROMA_DIR} existiert nicht." >&2
  exit 1
fi

mkdir -p "${STAGING_DIR}"

cleanup() {
  if [ -d "${STAGING_DIR}" ]; then rm -rf "${STAGING_DIR}"; fi
}
trap cleanup EXIT

# --------------------------------------
# Schritt 1: ORÓMA-Verzeichnis selektiv ins Staging kopieren
#           (ohne Live-Daten, Archive, Runtime-Dateien und ohne Logs)
# --------------------------------------
step_begin "Staging-Kopie"
echo "[backup_oroma_with_db] Kopiere ORÓMA-Verzeichnis selektiv ins Staging: ${STAGING_DIR}"

if command -v rsync >/dev/null 2>&1; then
  # Hinweis: KEINE Kommentarzeilen innerhalb eines "\"-fortgesetzten rsync-Kommandos,
  # sonst wird die "#" Zeile als Argument interpretiert und rsync bricht mit Usage ab.
  rsync -a \
    --exclude 'archives/**' \
    --exclude 'data/**' \
    --exclude 'data' \
    --exclude 'third_party/whisper.cpp/**' \
    --exclude '/.venv/**' \
    --exclude '/log/**' \
    --exclude '/logs/**' \
    --exclude '/state/**' \
    --exclude '/__pycache__/**' \
    --exclude '**/__pycache__/**' \
    --exclude '.lgd-*' \
    --exclude 'gstshark_*' \
    --exclude '*.gz' \
    --exclude '*.bak' \
    "${OROMA_DIR}/" "${STAGING_DIR}/"
else
  echo "[backup_oroma_with_db] Hinweis: 'rsync' nicht gefunden, verwende 'cp -a' + Bereinigung."
  cp -a "${OROMA_DIR}/." "${STAGING_DIR}/"

  # Große Live-Bereiche / Runtime-Artefakte entfernen – die relevanten
  # Inhalte (Slim-DBs, truncierte Logs) werden unten gezielt neu erzeugt.
  rm -rf \
    "${STAGING_DIR}/archives" \
    "${STAGING_DIR}/data" \
    "${STAGING_DIR}/log" \
    "${STAGING_DIR}/logs" \
    "${STAGING_DIR}/state" 2>/dev/null || true

  find "${STAGING_DIR}" -mindepth 1 \
    \( -type s -o -type p -o -type b -o -type c \) -delete 2>/dev/null || true
  find "${STAGING_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
  find "${STAGING_DIR}" -maxdepth 1 -type s -name ".lgd-*" -delete 2>/dev/null || true
  find "${STAGING_DIR}" -maxdepth 1 -type f -name "gstshark_*" -delete 2>/dev/null || true
  find "${STAGING_DIR}" -type f \( -name "*.gz" -o -name "*.bak" \) -delete
fi

# Zusätzliche Sicherheitsbereinigung nach beiden Pfaden:
# Falls Runtime-Artefakte trotz Copy-Phase auftauchen, vor dem Slimmen entfernen.
find "${STAGING_DIR}" -mindepth 1 \
  \( -type s -o -type p -o -type b -o -type c \) -delete 2>/dev/null || true
find "${STAGING_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -maxdepth 1 -type s -name ".lgd-*" -delete 2>/dev/null || true
find "${STAGING_DIR}" -maxdepth 1 -type f -name "gstshark_*" -delete 2>/dev/null || true

mkdir -p "${STAGING_DIR}/data"
step_end

# Hinweis:
#   Das Live-Verzeichnis data/ wurde oben bewusst nicht 1:1 übernommen.
#   Für das Backup existiert data/ im Staging daher nur als Zielstruktur für
#   die gezielt erzeugten Slim-DBs. Große Runtime-/Cache-/State-Inhalte bleiben
#   damit bewusst draußen.
mkdir -p "${STAGING_DIR}/data"
# --------------------------------------
# Schritt 2: Alle DBs slimmen und ins Staging zurücklegen
# --------------------------------------
step_begin "Slim-DB-Erzeugung"
echo "[backup_oroma_with_db] Slimme alle DBs in ${DATA_DIR} ..."

shopt -s nullglob
DB_FILES=( "${DATA_DIR}"/*.db "${DATA_DIR}"/*.sqlite )
shopt -u nullglob

if [ "${#DB_FILES[@]}" -eq 0 ]; then
  echo "[backup_oroma_with_db] Hinweis: Keine DB-Dateien in ${DATA_DIR} gefunden."
else
  for src_db in "${DB_FILES[@]}"; do
    base="$(basename "${src_db}")"
    dst_db="${STAGING_DIR}/data/${base}"

    db_t0="$(date +%s)"
    echo "[backup_oroma_with_db]   → Slim: ${base} | START $(now_human)"

    SRC_DB="${src_db}" DST_DB="${dst_db}" LIMIT="${DB_LIMIT}" TABLE_LIMITS="${DB_TABLE_LIMITS}" EXCLUDE_TABLES="${DB_EXCLUDE_TABLES}" SLOW_TABLE_SEC="${SLOW_TABLE_SEC}" DB_ORDER_OVERRIDES="${DB_ORDER_OVERRIDES}" python3 - <<'PY'
import os, sqlite3, sys, re, time, urllib.parse

src = os.environ["SRC_DB"]
dst = os.environ["DST_DB"]
limit = int(os.environ.get("LIMIT","1000"))
table_limits_raw = os.environ.get("TABLE_LIMITS","").strip()
exclude_tables_raw = os.environ.get("EXCLUDE_TABLES","").strip()

def _parse_kv_limits(s: str) -> dict:
    out = {}
    if not s:
        return out
    # allow separators: comma/semicolon/whitespace
    parts = re.split(r"[;,\s]+", s)
    for p in parts:
        if not p or "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        try:
            out[k] = int(v)
        except Exception:
            continue
    return out

def _parse_list(s: str) -> set:
    if not s:
        return set()
    parts = re.split(r"[;,\s]+", s)
    return {p.strip() for p in parts if p and p.strip()}

def _parse_kv_text(s: str) -> dict:
    out = {}
    if not s:
        return out
    parts = re.split(r"[;,\s]+", s)
    for p in parts:
        if not p or "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k or not v:
            continue
        out[k] = v
    return out

table_limits = _parse_kv_limits(table_limits_raw)
exclude_tables = _parse_list(exclude_tables_raw)
order_overrides = _parse_kv_text(os.environ.get("DB_ORDER_OVERRIDES", ""))
slow_table_sec = max(0.0, float(os.environ.get("SLOW_TABLE_SEC", "1") or "1"))

if not os.path.exists(src):
    print(f"[backup_oroma_with_db][PY] WARN: missing src {src}")
    sys.exit(0)

# (re)create dst
if os.path.exists(dst):
    os.remove(dst)

def is_fts_virtual(create_sql: str) -> bool:
    s = (create_sql or "").lower()
    return ("create virtual table" in s) and ("fts" in s)

# FTS5 shadow tables
FTS_SHADOW_SUFFIXES = ("_content","_data","_docsize","_config","_idx")

dst_conn = sqlite3.connect(dst, timeout=60.0, uri=True)
dst_conn.row_factory = sqlite3.Row
dst_conn.execute("PRAGMA busy_timeout=60000")
dst_conn.execute("PRAGMA foreign_keys=OFF")
# Backup-Ziel aggressiv auf schnelle, lokale Schreibparameter setzen.
# Wichtig: nur `main` adressieren, damit keine PRAGMA-Aktion versehentlich die
# read-only angehaengte Live-DB beeinflusst oder dafuer Lock-Arbeit ausloest.
dst_conn.execute("PRAGMA main.journal_mode=MEMORY")
dst_conn.execute("PRAGMA main.synchronous=OFF")
dst_conn.execute("PRAGMA main.temp_store=MEMORY")

src_uri = "file:" + urllib.parse.quote(os.path.abspath(src), safe="/") + "?mode=ro"
attach_sql = f"ATTACH DATABASE '{src_uri}' AS src"
last_attach_err = None
for attempt in range(1, 6):
    try:
        dst_conn.execute(attach_sql)
        last_attach_err = None
        break
    except sqlite3.OperationalError as e:
        last_attach_err = e
        if "locked" not in str(e).lower():
            raise
        wait_s = min(2 * attempt, 8)
        print(f"[backup_oroma_with_db][PY] WARN: ATTACH locked db={os.path.basename(src)} attempt={attempt}/5 wait={wait_s}s err={e}", flush=True)
        time.sleep(wait_s)
if last_attach_err is not None:
    raise last_attach_err

def _log(msg: str) -> None:
    print(msg, flush=True)


def _indexed_leading_columns(conn, table_name: str) -> set[str]:
    cols = set()
    try:
        idxs = conn.execute(f"PRAGMA src.index_list({table_name})").fetchall()
    except Exception:
        return cols
    for idx in idxs:
        try:
            idx_name = idx["name"] if isinstance(idx, sqlite3.Row) else idx[1]
            xinfo = conn.execute(f"PRAGMA src.index_xinfo('{idx_name}')").fetchall()
        except Exception:
            continue
        first_user_col = None
        first_seqno = None
        for r in xinfo:
            seqno = r[0] if not isinstance(r, sqlite3.Row) else r["seqno"]
            cid = r[1] if not isinstance(r, sqlite3.Row) else r["cid"]
            name = r[2] if not isinstance(r, sqlite3.Row) else r["name"]
            key = r[5] if len(r) > 5 else (r["key"] if isinstance(r, sqlite3.Row) and "key" in r.keys() else 1)
            if cid is None or cid < 0 or not key:
                continue
            if first_seqno is None or seqno < first_seqno:
                first_seqno = seqno
                first_user_col = name
        if first_user_col:
            cols.add(str(first_user_col))
    return cols

# Load all table create sql
rows = dst_conn.execute(
    "SELECT name, sql FROM src.sqlite_master "
    "WHERE type='table' AND name NOT LIKE 'sqlite_stat%' AND name != 'sqlite_sequence' "
    "ORDER BY name"
).fetchall()

table_sql = { r["name"]: (r["sql"] or "") for r in rows }

# Detect FTS virtual tables and mark their shadow tables to skip
skip_tables = set()
for name, create_sql in table_sql.items():
    if is_fts_virtual(create_sql):
        # skip the auto-created shadows later
        for suf in FTS_SHADOW_SUFFIXES:
            skip_tables.add(name + suf)

# Create tables first
for name in sorted(table_sql.keys()):
    if name in skip_tables:
        continue
    create_sql = table_sql[name].strip()
    if not create_sql:
        continue
    try:
        dst_conn.execute(create_sql)
    except Exception as e:
        # Should be rare; keep going
        print(f"[backup_oroma_with_db][PY] WARN: CREATE fail {name}: {e}")

# Copy data (limited)
for name in sorted(table_sql.keys()):
    if name in skip_tables:
        continue

    table_t0 = time.monotonic()

    # Per-table limit / exclude (v1.8)
    # - exclude_tables -> kopiert 0 Zeilen (Schema bleibt)
    # - table_limits   -> überschreibt globales 'limit' nur für diese Tabelle
    eff_limit = table_limits.get(name, limit)
    if name in exclude_tables:
        eff_limit = 0

    # columns
    try:
        cols = [c["name"] for c in dst_conn.execute(f"PRAGMA src.table_info({name})")]
    except Exception:
        cols = []

    # Determine best order column
    # Strategy:
    # 1) explicit per-table override via ORDER_OVERRIDES (e.g. metrics=rowid)
    # 2) prefer id when present
    # 3) prefer ts / created_at only when they are the *leading* index column
    # 4) otherwise fall back to rowid to avoid expensive full sorts on huge tables
    order_col = None
    override = order_overrides.get(name)
    if override:
        if override in ("rowid", "none"):
            order_col = None
        elif override in cols:
            order_col = override
    if order_col is None:
        if "id" in cols:
            order_col = "id"
        else:
            leading_idx_cols = _indexed_leading_columns(dst_conn, name)
            for cand in ("ts", "created_at"):
                if cand in cols and cand in leading_idx_cols:
                    order_col = cand
                    break

    # Insert
    if eff_limit <= 0:
        # bewusst 0 Zeilen (nur Schema) – z.B. für extrem große Tabellen
        continue

    try:
        if order_col:
            # Direkt die letzten N Zeilen lesen – bewusst ohne COUNT(*)/Cutoff-Suche.
            dst_conn.execute(
                f"INSERT INTO {name} SELECT * FROM (SELECT * FROM src.{name} ORDER BY {order_col} DESC LIMIT ?) ORDER BY {order_col} ASC",
                (eff_limit,)
            )
        else:
            try:
                # Fallback für normale rowid-Tabellen
                dst_conn.execute(
                    f"INSERT INTO {name} SELECT * FROM (SELECT * FROM src.{name} ORDER BY rowid DESC LIMIT ?) ORDER BY rowid ASC",
                    (eff_limit,)
                )
            except Exception:
                # WITHOUT ROWID oder exotische Tabellen → best effort ohne garantierte 'letzte' Reihenfolge
                dst_conn.execute(
                    f"INSERT INTO {name} SELECT * FROM src.{name} LIMIT ?",
                    (eff_limit,)
                )
    except Exception as e:
        # Some virtual tables can be picky; ignore but continue
        print(f"[backup_oroma_with_db][PY] WARN: INSERT fail {name}: {e}")
    finally:
        table_dur = time.monotonic() - table_t0
        if table_dur >= slow_table_sec:
            chosen_order = override if override else (order_col or 'rowid/limit')
            print(f"[backup_oroma_with_db][PY] SLOW TABLE db={os.path.basename(src)} table={name} dur={table_dur:.3f}s limit={eff_limit} order={chosen_order}", flush=True)

# Create indexes/views/triggers AFTER data (faster + no trigger firing during copy)
objs = dst_conn.execute(
    "SELECT type, name, sql FROM src.sqlite_master "
    "WHERE type IN ('index','view','trigger') AND sql IS NOT NULL"
).fetchall()

for o in objs:
    sql = (o["sql"] or "").strip()
    if not sql:
        continue

    # Make it idempotent (avoid 'already exists' noise)
    s = sql
    s = re.sub(r"^CREATE\s+UNIQUE\s+INDEX\s+", "CREATE UNIQUE INDEX IF NOT EXISTS ", s, flags=re.I)
    s = re.sub(r"^CREATE\s+INDEX\s+", "CREATE INDEX IF NOT EXISTS ", s, flags=re.I)
    s = re.sub(r"^CREATE\s+VIEW\s+", "CREATE VIEW IF NOT EXISTS ", s, flags=re.I)
    s = re.sub(r"^CREATE\s+TRIGGER\s+", "CREATE TRIGGER IF NOT EXISTS ", s, flags=re.I)

    try:
        dst_conn.execute(s)
    except Exception:
        # ignore (some SQLite builds don't support IF NOT EXISTS for trigger/view)
        try:
            dst_conn.execute(sql)
        except Exception:
            pass

dst_conn.commit()
dst_conn.execute("DETACH DATABASE src")
dst_conn.close()
PY

    db_t1="$(date +%s)"
    db_dur=$(( db_t1 - db_t0 ))
    if [ ! -f "${dst_db}" ]; then
      echo "[backup_oroma_with_db] WARN: Slim-DB fehlt für ${base} – überspringe."
    else
      echo "[backup_oroma_with_db]   → Slim: ${base} | ENDE $(now_human) | Dauer: ${db_dur}s"
    fi
  done
fi
step_end

# --------------------------------------
# Schritt 3: Logs gezielt aus Live kopieren – bereits trunkiert
# --------------------------------------
# Wichtig:
#   Wir kopieren die Logs NICHT 1:1 ins Staging, weil das bei sehr großen
#   Logdateien / tmpfs (/tmp) sofort scheitern kann. Stattdessen:
#     - wir erzeugen die Logverzeichnisse im Staging neu
#     - wir schreiben pro Datei nur die letzten LOG_LIMIT Zeilen

copy_truncated_logs() {
  local src_dir="$1"  # /opt/ai/oroma/logs oder /opt/ai/oroma/log
  local dst_dir="$2"  # ${STAGING_DIR}/logs oder ${STAGING_DIR}/log
  local limit="$3"

  [ -d "${src_dir}" ] || return 0

  mkdir -p "${dst_dir}"

  # Typische Textlogs (ORÓMA)
  # - *.log, *.err.log, *.out.log, *.jsonl
  # - ohne *.gz / *.bak
  local f
  shopt -s nullglob
  for f in "${src_dir}"/*; do
    [ -f "${f}" ] || continue
    case "${f}" in
      *.gz|*.bak) continue ;;
    esac

    local base
    base="$(basename "${f}")"
    local dst
    dst="${dst_dir}/${base}"

    # Bei allem, was wie Textlog aussieht: tail
    case "${base}" in
      *.log|*.log.*|*.jsonl|*.txt)
        # tail ist i.d.R. O(1) bzgl. Datei-Groesse (liest vom Ende)
        if ! tail -n "${limit}" "${f}" > "${dst}" 2>/dev/null; then
          # Fallback: wenn tail fehlschlägt, kopiere die Datei (best effort)
          cp -a "${f}" "${dst}" 2>/dev/null || true
        fi
        ;;
      *)
        # Unbekannte Dateien (z.B. *.pid): normal kopieren
        cp -a "${f}" "${dst}" 2>/dev/null || true
        ;;
    esac
  done
  shopt -u nullglob
}

step_begin "Log-Truncation"
echo "[backup_oroma_with_db] Schreibe Logs trunkiert ins Staging (letzte ${LOG_LIMIT} Zeilen) ..."
copy_truncated_logs "${OROMA_DIR}/logs" "${STAGING_DIR}/logs" "${LOG_LIMIT}"
copy_truncated_logs "${OROMA_DIR}/log"  "${STAGING_DIR}/log"  "${LOG_LIMIT}"
if [ -f "${DIAG_LOG_HOST}" ]; then
  mkdir -p "${STAGING_DIR}/logs"
  cp -f "${DIAG_LOG_HOST}" "${STAGING_DIR}/${DIAG_LOG_STAGE_REL}" || true
  echo "[backup_oroma_with_db] Diagnose-Log ins Staging übernommen: ${STAGING_DIR}/${DIAG_LOG_STAGE_REL}"
fi
step_end

# --------------------------------------
# Schritt 4: ZIP-Archiv aus dem Staging bauen
# --------------------------------------
step_begin "ZIP-Erzeugung"
echo "[backup_oroma_with_db] Erzeuge ZIP-Archiv ..."

# Zusätzliche Sicherheitsbereinigung direkt vor ZIP:
# Spezialdateien oder spät entstandene Runtime-Artefakte sollen nie im Archiv landen.
find "${STAGING_DIR}" -mindepth 1 \
  \( -type s -o -type p -o -type b -o -type c \) -delete 2>/dev/null || true
find "${STAGING_DIR}" -maxdepth 1 -type s -name ".lgd-*" -delete 2>/dev/null || true

remaining_specials="$(find "${STAGING_DIR}" -mindepth 1   \( -type s -o -type p -o -type b -o -type c \) -print 2>/dev/null || true)"
if [ -n "${remaining_specials}" ]; then
  echo "[backup_oroma_with_db] WARN: Vor ZIP verbleibende Spezialdateien erkannt:" >&2
  printf '%s
' "${remaining_specials}" >&2
fi

cd "${STAGING_DIR}"
zip -r -q "${BACKUP_FILE}" . -x '*.sock' '.lgd-*' '*/.lgd-*'
step_end

# Hostseitige Diagnose-Arbeitsdatei bereinigen: Die Diagnose befindet sich bereits
# im Staging/ZIP unter logs/backup_backup_diag.log und soll nicht dauerhaft lose
# unter ${BACKUP_BASE}/_staging liegen bleiben.
if [ -f "${DIAG_LOG_HOST}" ]; then
  rm -f "${DIAG_LOG_HOST}" || true
fi

# Nach erfolgreichem Backup die Live-Logs im ORÓMA-Baum auf 0 setzen. Die letzten
# relevanten Zeilen wurden zuvor bereits trunkiert ins Backup übernommen.
step_begin "Live-Log-Reset"
echo "[backup_oroma_with_db] Setze Live-Logs unter ${OROMA_DIR} auf 0 ..."
find "${OROMA_DIR}" -type f -name "*.log" -exec truncate -s 0 {} \;
step_end

echo "[backup_oroma_with_db] Backup fertig."
echo "[backup_oroma_with_db] Datei: ${BACKUP_FILE}"

SCRIPT_T1="$(date +%s)"
SCRIPT_DUR=$(( SCRIPT_T1 - SCRIPT_T0 ))
ZIP_BYTES=0
if [ -f "${BACKUP_FILE}" ]; then
  ZIP_BYTES="$(wc -c < "${BACKUP_FILE}" | tr -d " ")"
fi
ZIP_HUMAN="$(format_bytes "${ZIP_BYTES}")"
echo "[backup_oroma_with_db] Gesamtdauer: ${SCRIPT_DUR}s"
echo "[backup_oroma_with_db] ZIP-Größe: ${ZIP_BYTES} Bytes (${ZIP_HUMAN})"

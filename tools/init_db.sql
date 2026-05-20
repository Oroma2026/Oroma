-- =============================================================================
-- ORÓMA v3.5 Patch Level 1 – Komplettes Datenbankschema
-- =============================================================================
-- Enthält:
--   • Original v3.5 Tabellen
--   • Erweiterungen für Patch 1 (transfer_snaps, calculator_tasks, calculator_results)
-- =============================================================================

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;

-- SnapChains
CREATE TABLE IF NOT EXISTS snapchains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    quality REAL NOT NULL DEFAULT 0.0,
    blob BLOB NOT NULL,
    exported INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    origin TEXT DEFAULT NULL,
    gap_flag INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT NULL,
    namespace TEXT DEFAULT NULL,
    source_id TEXT DEFAULT NULL,
    version TEXT DEFAULT 'v3.5'
);

-- Rules
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    weight REAL NOT NULL DEFAULT 0.0,
    blob BLOB NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    namespace TEXT DEFAULT NULL,
    source_id TEXT DEFAULT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

-- Metrics
CREATE TABLE IF NOT EXISTS metrics (
    key TEXT NOT NULL,
    ts INTEGER NOT NULL,
    value REAL NOT NULL
);

-- Models (Registry)
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    family TEXT DEFAULT NULL,
    version TEXT DEFAULT 'v3.5',
    input_size TEXT DEFAULT NULL,
    preproc_json TEXT DEFAULT NULL,
    postproc_json TEXT DEFAULT NULL,
    labels_txt TEXT DEFAULT NULL,
    hef_path TEXT DEFAULT NULL,
    source_hash TEXT DEFAULT NULL,
    calib_hash TEXT DEFAULT NULL,
    created_at INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
);

-- Quality History
CREATE TABLE IF NOT EXISTS quality_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapchain_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    quality REAL NOT NULL,
    FOREIGN KEY(snapchain_id) REFERENCES snapchains(id)
);

-- Rewards Log
CREATE TABLE IF NOT EXISTS rewards_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    reward REAL NOT NULL
);

-- Curiosity Log
CREATE TABLE IF NOT EXISTS curiosity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    signal REAL NOT NULL
);

-- Gaps (Diagnostics)
CREATE TABLE IF NOT EXISTS gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    description TEXT NOT NULL,
    category TEXT DEFAULT 'general'
);

-- Knowledge-DB: Documents
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT DEFAULT 'manual',
    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    embedding BLOB
);

-- Meta-Snaps (experimentell, für SelfAssessment nutzbar)
CREATE TABLE IF NOT EXISTS meta_snaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    sources TEXT CHECK(json_valid(sources)),
    score REAL DEFAULT 0.0,
    created_at INTEGER
);

-- Transfer-Snaps (Patch 1)
CREATE TABLE IF NOT EXISTS transfer_snaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    sequence TEXT NOT NULL,
    pattern TEXT NOT NULL
);

-- Calculator-Tasks (Patch 1)
CREATE TABLE IF NOT EXISTS calculator_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    level INTEGER NOT NULL,
    expr TEXT NOT NULL,
    truth REAL
);

-- Calculator-Results (Patch 1)
CREATE TABLE IF NOT EXISTS calculator_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    got REAL,
    correct INTEGER,
    reward REAL,
    error_type TEXT,
    FOREIGN KEY(task_id) REFERENCES calculator_tasks(id)
);

-- Migrations-Tracking
CREATE TABLE IF NOT EXISTS migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    schema_version TEXT DEFAULT 'v3.5',
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Migrationseinträge
INSERT INTO migrations (name) VALUES ('init_db_v3_5');
INSERT INTO migrations (name) VALUES ('patch1_db_extension');
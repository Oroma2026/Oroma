-- =============================================================================
-- Pfad:    /opt/ai/oroma/tools/migrate_patch2_2.sql
-- Projekt: ORÓMA
-- Version: v3.5patch2.2
-- Stand:   2025-09-26
--
-- Zweck:
--   - Migration für Forgetting/Kompression-Dashboard
--   - Stellt sicher:
--       • Feld 'status' existiert in snapchains
--       • Werte 'active' und 'compressed' nutzbar
--       • Indexe auf status für schnelle Abfragen
-- =============================================================================

BEGIN TRANSACTION;

-- 1. Status-Feld hinzufügen (falls nicht vorhanden)
ALTER TABLE snapchains
  ADD COLUMN status TEXT DEFAULT 'active';

-- 2. Standardwerte für existierende Reihen
UPDATE snapchains
   SET status = 'active'
 WHERE status IS NULL;

-- 3. Index auf status für Performance
CREATE INDEX IF NOT EXISTS idx_snapchains_status
    ON snapchains(status);

-- 4. MetaSnaps-Tabelle absichern (falls nicht existiert)
CREATE TABLE IF NOT EXISTS meta_snaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts INTEGER NOT NULL,
    data TEXT,
    note TEXT
);

COMMIT;

#cd /opt/ai/oroma/
#sqlite3 data/oroma.db < tools/migrate_patch2_2.sql
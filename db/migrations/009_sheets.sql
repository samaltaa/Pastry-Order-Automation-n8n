-- Spreadsheet snapshots sent by Make (one table per sheet, recreated when its headers change),
-- and a count of products set to Draft because they left the source.
CREATE SCHEMA IF NOT EXISTS sheets;
ALTER TABLE sync.runs ADD COLUMN IF NOT EXISTS removed INT NOT NULL DEFAULT 0;

-- Stock updates and stock conflicts are reported separately from product updates.
ALTER TABLE sync.runs ADD COLUMN IF NOT EXISTS stock_updated INT NOT NULL DEFAULT 0;
ALTER TABLE sync.runs ADD COLUMN IF NOT EXISTS conflicts INT NOT NULL DEFAULT 0;

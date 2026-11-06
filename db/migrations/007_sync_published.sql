-- Products created through the API aren't on any sales channel until they're published.
ALTER TABLE sync.items ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT FALSE;

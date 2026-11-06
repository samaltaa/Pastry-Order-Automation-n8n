-- The price last sent to Shopify, so a sudden jump can be spotted, and the changes held for the
-- owner because they looked like typos. One row per product and field; released lets it through once.
ALTER TABLE sync.items ADD COLUMN IF NOT EXISTS last_price TEXT;
CREATE TABLE IF NOT EXISTS sync.held_changes (
  sku TEXT NOT NULL,
  field TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  reason TEXT NOT NULL,
  released BOOLEAN NOT NULL DEFAULT FALSE,
  held_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (sku, field)
);

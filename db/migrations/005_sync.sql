-- Tables for the POS -> Shopify sync service (sync-api). Kept in their own schema.
CREATE SCHEMA IF NOT EXISTS sync;

-- Column mappings proposed by Claude. Only an approved mapping can be used to sync.
CREATE TABLE IF NOT EXISTS sync.mappings (
  id SERIAL PRIMARY KEY,
  source_table VARCHAR(120) NOT NULL,
  mapping JSONB NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
  model VARCHAR(80),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  reviewed_at TIMESTAMPTZ
);

-- One row per sync run, with counts for the report and alerts.
CREATE TABLE IF NOT EXISTS sync.runs (
  id SERIAL PRIMARY KEY,
  mapping_id INT REFERENCES sync.mappings(id),
  trigger VARCHAR(20) NOT NULL,
  dry_run BOOLEAN NOT NULL DEFAULT FALSE,
  status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
  created INT NOT NULL DEFAULT 0,
  updated INT NOT NULL DEFAULT 0,
  skipped INT NOT NULL DEFAULT 0,
  failed INT NOT NULL DEFAULT 0,
  error TEXT,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ
);

-- Last known state of each synced product, so unchanged rows are skipped
-- and Shopify IDs don't have to be looked up again.
CREATE TABLE IF NOT EXISTS sync.items (
  sku VARCHAR(60) PRIMARY KEY,
  source_key VARCHAR(60) NOT NULL,
  data_hash VARCHAR(64) NOT NULL,
  shopify_product_id VARCHAR(80),
  shopify_variant_id VARCHAR(80),
  shopify_inventory_item_id VARCHAR(80),
  last_quantity INT,
  last_error TEXT,
  synced_at TIMESTAMPTZ DEFAULT NOW()
);

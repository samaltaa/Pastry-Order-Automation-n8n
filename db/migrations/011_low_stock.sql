-- Products the owner has been told are running low. A product alerts once when it drops to its
-- reorder level, and its row is removed when it's restocked, so the next drop alerts again.
CREATE TABLE IF NOT EXISTS sync.low_stock (
  sku TEXT PRIMARY KEY,
  alerted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

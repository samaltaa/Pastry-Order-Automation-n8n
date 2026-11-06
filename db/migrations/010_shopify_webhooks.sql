-- Shopify webhook deliveries already handled. Shopify retries a delivery it isn't sure arrived,
-- so each X-Shopify-Webhook-Id is processed once.
CREATE TABLE IF NOT EXISTS sync.webhook_deliveries (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

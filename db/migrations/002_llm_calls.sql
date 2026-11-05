CREATE TABLE IF NOT EXISTS llm_calls (
  id SERIAL PRIMARY KEY,
  route VARCHAR(60) NOT NULL,
  ref VARCHAR(120),
  model VARCHAR(80) NOT NULL,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  latency_ms INT NOT NULL DEFAULT 0,
  outcome VARCHAR(20) NOT NULL,
  error TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS llm_calls_created_at_idx ON llm_calls (created_at);
CREATE INDEX IF NOT EXISTS orders_delivery_time_idx ON orders (delivery_time);

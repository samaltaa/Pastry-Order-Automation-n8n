ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS chat_id VARCHAR(80);
CREATE INDEX IF NOT EXISTS llm_calls_chat_id_created_at_idx ON llm_calls (chat_id, created_at);

CREATE TABLE IF NOT EXISTS events (
  id SERIAL PRIMARY KEY,
  kind VARCHAR(60) NOT NULL,
  source VARCHAR(120),
  detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS events_created_at_idx ON events (created_at);

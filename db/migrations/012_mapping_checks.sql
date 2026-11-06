-- Who approved a mapping ("auto" when every deterministic check passed, "owner" otherwise),
-- and the checks it was judged on.
ALTER TABLE sync.mappings ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE sync.mappings ADD COLUMN IF NOT EXISTS checks JSONB;

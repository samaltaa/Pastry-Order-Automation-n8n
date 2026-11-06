-- Claude reviews price and stock changes too. applied marks a change that already happened (an
-- online sale): declining it puts the old value back instead of keeping it out of the store.
ALTER TABLE sync.held_changes ADD COLUMN IF NOT EXISTS flagged_by TEXT NOT NULL DEFAULT 'rule';
ALTER TABLE sync.held_changes ADD COLUMN IF NOT EXISTS applied BOOLEAN NOT NULL DEFAULT FALSE;

-- The last image URL sent per product. Shopify re-uploads an image every time it's sent,
-- so the sync only sends it when it changes.
ALTER TABLE sync.items ADD COLUMN IF NOT EXISTS last_image TEXT;

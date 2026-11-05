DELETE FROM conversations a USING conversations b WHERE a.chat_id = b.chat_id AND a.id < b.id;
CREATE UNIQUE INDEX IF NOT EXISTS conversations_chat_id_key ON conversations (chat_id);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_address TEXT;

CREATE TABLE IF NOT EXISTS menu_items (
  id SERIAL PRIMARY KEY,
  item_name VARCHAR(120) NOT NULL,
  category VARCHAR(50) NOT NULL,
  price NUMERIC(10,2) NOT NULL,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customers (
  id SERIAL PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  phone VARCHAR(40) NOT NULL UNIQUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
  id SERIAL PRIMARY KEY,
  customer_id INT REFERENCES customers(id),
  delivery_address TEXT NOT NULL,
  delivery_time VARCHAR(80) NOT NULL,
  total NUMERIC(10,2) NOT NULL DEFAULT 0,
  status VARCHAR(30) DEFAULT 'pending',
  notes TEXT,
  idempotency_key VARCHAR(120) UNIQUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS order_items (
  id SERIAL PRIMARY KEY,
  order_id INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  item_id INT NOT NULL REFERENCES menu_items(id),
  quantity INT NOT NULL CHECK (quantity > 0),
  unit_price NUMERIC(10,2) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
  id SERIAL PRIMARY KEY,
  chat_id VARCHAR(80) NOT NULL,
  state JSONB NOT NULL DEFAULT '{}',
  last_message TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS processed_updates (
  update_id VARCHAR(120) PRIMARY KEY,
  source VARCHAR(30) NOT NULL,
  processed_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO menu_items (item_name, category, price)
VALUES
  ('Tres Leches', 'cake', 650.00),
  ('Empanadas de carne', 'savory', 120.00),
  ('Empanadas de queso', 'savory', 110.00),
  ('Croissant de mantequilla', 'bread', 180.00),
  ('Pan de queso', 'bread', 140.00),
  ('Tarta de coco', 'cake', 420.00)
ON CONFLICT DO NOTHING;

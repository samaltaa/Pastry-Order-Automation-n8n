INSERT INTO menu_items (item_name, category, price)
SELECT * FROM (VALUES
  ('Tres Leches', 'cake', 650.00),
  ('Empanadas de carne', 'savory', 120.00),
  ('Empanadas de queso', 'savory', 110.00),
  ('Croissant de mantequilla', 'bread', 180.00),
  ('Pan de queso', 'bread', 140.00),
  ('Tarta de coco', 'cake', 420.00)
) AS seed (item_name, category, price)
WHERE NOT EXISTS (SELECT 1 FROM menu_items);

-- Mock point-of-sale (POS) database for the Shopify sync demo: a Dominican bakery serving North American
-- customers. The POS software is in Spanish (column names, staff notes); products are named in English.
-- Built to be messy like real POS data: prices stored as text in mixed formats, S/N flags, a padded
-- lowercase code, empty barcodes and a system row that isn't a product.
-- Safe to run again: it refreshes names, categories and photos but never prices or stock.
-- Photos are set by product_images.sql (credited in IMAGE_CREDITS.md).

SET client_min_messages = warning;

CREATE SCHEMA IF NOT EXISTS pos;

CREATE TABLE IF NOT EXISTS pos.articulos (
  codigo VARCHAR(20) PRIMARY KEY,
  descripcion VARCHAR(120) NOT NULL,
  categoria VARCHAR(30),
  unidad VARCHAR(10),
  precio_venta VARCHAR(20) NOT NULL,
  costo NUMERIC(10,2),
  existencia NUMERIC(10,2) NOT NULL DEFAULT 0,
  itbis CHAR(1) NOT NULL DEFAULT 'S',
  activo CHAR(1) NOT NULL DEFAULT 'S',
  cod_barra VARCHAR(20),
  nota_interna TEXT,
  fecha_mod TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE pos.articulos ADD COLUMN IF NOT EXISTS imagen_url TEXT;

-- Like most POS systems, any edit bumps fecha_mod. The sync uses it to find changed rows.
CREATE OR REPLACE FUNCTION pos.touch_fecha_mod() RETURNS trigger AS $$
BEGIN
  NEW.fecha_mod := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS articulos_touch ON pos.articulos;
CREATE TRIGGER articulos_touch BEFORE UPDATE ON pos.articulos
  FOR EACH ROW EXECUTE FUNCTION pos.touch_fecha_mod();

INSERT INTO pos.articulos
  (codigo, descripcion, categoria, unidad, precio_venta, costo, existencia, itbis, activo, cod_barra, nota_interna)
VALUES
  ('REP-001', 'DOMINICAN VANILLA CAKE 1LB', 'CAKES', 'UND', '$24.00', 9.50, 6, 'S', 'S', '746000100011', NULL),
  ('REP-002', 'DOMINICAN CHOCOLATE CAKE 1LB', 'CAKES', 'UND', '$26.00', 10.20, 4, 'S', 'S', '746000100028', NULL),
  ('REP-003', 'DOM. VANILLA CAKE 1/2 LB', 'CAKES', 'UND', '13.5', 5.10, 9, 'S', 'S', '746000100035', 'pedir con 24h'),
  ('REP-004', 'RED VELVET CAKE 1LB', 'CAKES', 'UND', 'US$ 28.00', 11.00, 3, 'S', 'S', '746000100042', NULL),
  ('REP-005', 'CARROT CAKE W/ CREAM CHEESE', 'CAKES', 'UND', '$27.00', 10.80, 2, 'S', 'S', NULL, NULL),
  ('rep-006 ', 'Orange pound cake', 'CAKES', 'UND', '22', 8.40, 5, 'S', 'S', '', 'codigo viejo, no cambiar'),
  ('REP-010', 'TRES LECHES SLICE', 'DESSERTS', 'PORC', '$4.50', 1.60, 18, 'S', 'S', '746000100103', NULL),
  ('REP-011', 'TRES LECHES WHOLE 10"', 'DESSERTS', 'UND', '$32.00', 12.50, 3, 'S', 'S', '746000100110', NULL),
  ('REP-012', 'MILK FLAN SLICE', 'DESSERTS', 'PORC', '3.75', 1.10, 20, 'S', 'S', '746000100127', NULL),
  ('REP-013', 'COCONUT FLAN WHOLE', 'DESSERTS', 'UND', '$19.00', 6.80, 2, 'S', 'S', NULL, NULL),
  ('REP-014', 'PASSION FRUIT CHEESECAKE SLICE', 'DESSERTS', 'PORC', '$5.25', 2.00, 12, 'S', 'S', '746000100141', 'chinola = maracuya'),
  ('REP-015', 'STRAWBERRY CHEESECAKE WHOLE', 'DESSERTS', 'UND', '$36.00', 14.00, 1, 'S', 'S', '746000100158', NULL),
  ('REP-016', 'COCONUT TART', 'DESSERTS', 'UND', '$21.00', 7.90, 4, 'S', 'S', '746000100165', NULL),
  ('REP-017', 'MAJARETE CORN PUDDING CUP', 'DESSERTS', 'UND', '2.50', 0.80, 15, 'S', 'S', NULL, NULL),
  ('REP-018', 'SWEET CREAM OF BEANS 16OZ', 'DESSERTS', 'UND', '$6.00', 2.10, 0, 'S', 'N', '746000100189', 'solo cuaresma'),
  ('REP-020', 'DOMINICAN BREAD ROLL', 'BREAD', 'UND', '0.35', 0.09, 120, 'N', 'S', NULL, NULL),
  ('REP-021', 'DOMINICAN BREAD ROLLS DOZEN', 'BREAD', 'DOC', '$3.90', 1.05, 15, 'N', 'S', '746000100219', NULL),
  ('REP-022', 'SOFT SANDWICH ROLL', 'BREAD', 'UND', '0.50', 0.14, 80, 'N', 'S', NULL, NULL),
  ('REP-023', 'BUTTER CROISSANT', 'BREAD', 'UND', '$2.25', 0.70, 30, 'S', 'S', '746000100233', NULL),
  ('REP-024', 'CHEESE BREAD', 'BREAD', 'UND', '1.75', 0.55, 25, 'S', 'S', '746000100240', NULL),
  ('REP-025', 'WHOLE WHEAT LOAF', 'BREAD', 'UND', '$4.25', 1.40, 10, 'N', 'S', '746000100257', NULL),
  ('REP-026', 'GLAZED DONUT', 'BREAD', 'UND', '1.25', 0.35, 0, 'S', 'S', NULL, 'agotado hoy'),
  ('REP-030', 'CHICKEN EMPANADA', 'SAVORY', 'UND', '$1.80', 0.60, 40, 'S', 'S', '746000100301', NULL),
  ('REP-031', 'CHEESE EMPANADA', 'SAVORY', 'UND', '$1.60', 0.50, 35, 'S', 'S', '746000100318', NULL),
  ('REP-032', 'BEEF EMPANADA', 'SAVORY', 'UND', '1.80', 0.62, 38, 'S', 'S', '746000100325', NULL),
  ('REP-033', 'CHICKEN PASTELITO', 'SAVORY', 'UND', '1.50', 0.45, 24, 'S', 'S', NULL, NULL),
  ('REP-034', 'KIBBEH (QUIPE)', 'SAVORY', 'UND', '$1.50', 0.48, 30, 'S', 'S', '746000100349', NULL),
  ('REP-035', 'PARTY PLATTER 50 PCS', 'SAVORY', 'UND', '$45.00', 18.00, 2, 'S', 'S', '746000100356', 'para fiestas'),
  ('REP-040', 'BUTTER COOKIES 12 CT', 'COOKIES', 'UND', '$7.50', 2.40, 14, 'S', 'S', '746000100400', NULL),
  ('REP-041', 'CHOCOLATE CHIP COOKIES', 'COOKIES', 'LB', '$9.00', 3.10, 8, 'S', 'S', '746000100417', NULL),
  ('REP-042', 'POLVORONES SHORTBREAD', 'COOKIES', 'DOC', '6', 1.80, 10, 'S', 'S', NULL, NULL),
  ('REP-043', 'DULCE DE LECHE ALFAJORES', 'COOKIES', 'DOC', '$8.50', 2.90, 6, 'S', 'S', '746000100431', NULL),
  ('REP-050', 'AMERICANO COFFEE', 'DRINKS', 'UND', '$1.75', 0.30, 999, 'S', 'S', NULL, 'no lleva inventario'),
  ('REP-051', 'HOT CHOCOLATE', 'DRINKS', 'UND', '$2.50', 0.55, 999, 'S', 'S', NULL, 'no lleva inventario'),
  ('REP-052', 'PASSION FRUIT JUICE 16OZ', 'DRINKS', 'UND', '$3.00', 0.90, 20, 'S', 'S', '746000100523', NULL),
  ('REP-060', 'NUMBER BIRTHDAY CANDLE', 'ACCESSORIES', 'UND', '$1.00', 0.25, 50, 'S', 'S', '746000100608', NULL),
  ('REP-061', 'CAKE BOX 10"', 'ACCESSORIES', 'UND', '0.75', 0.30, 100, 'S', 'N', NULL, 'uso interno, no vender'),
  ('REP-099', 'AJUSTE INVENTARIO', NULL, NULL, '0', NULL, 0, 'N', 'N', NULL, 'articulo del sistema, ignorar')
ON CONFLICT (codigo) DO UPDATE SET
  descripcion = EXCLUDED.descripcion,
  categoria = EXCLUDED.categoria;

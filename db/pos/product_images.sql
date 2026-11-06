-- Product photos for the mock POS, one per product: the single place to change a photo.
-- Each link is a Wikimedia Commons file used under its free license (credits in IMAGE_CREDITS.md).
-- Run after mock_pos.sql (scripts/seed-pos.sh does both). Editing a link here makes the next sync
-- upload the new photo to Shopify.

UPDATE pos.articulos AS a SET imagen_url = photo.url
FROM (VALUES
  ('REP-001', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/8/80/Victoria_Sponges.jpg/960px-Victoria_Sponges.jpg'),
  ('REP-002', 'https://upload.wikimedia.org/wikipedia/commons/5/55/Chocolate_fudge_cake.jpg'),
  ('REP-003', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/3/3f/Bundt_cake_%2812546036764%29.jpg/960px-Bundt_cake_%2812546036764%29.jpg'),
  ('REP-004', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b2/Red_Velvet_Cake_Waldorf_Astoria.jpg/960px-Red_Velvet_Cake_Waldorf_Astoria.jpg'),
  ('REP-005', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fe/Carrot_cake_at_America_Graffiti_%28cropped%29.jpg/960px-Carrot_cake_at_America_Graffiti_%28cropped%29.jpg'),
  ('rep-006 ', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/9a/Pound_cake_2.jpg/960px-Pound_cake_2.jpg'),
  ('REP-010', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/c/ca/Pastel_de_Tres_Leches.jpg/960px-Pastel_de_Tres_Leches.jpg'),
  ('REP-011', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/d/db/Torta_de_tres_leches_2.jpg/960px-Torta_de_tres_leches_2.jpg'),
  ('REP-012', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/5/58/Cremecaramel.jpg/960px-Cremecaramel.jpg'),
  ('REP-013', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/04/Coconut_Flan.jpg/960px-Coconut_Flan.jpg'),
  ('REP-014', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a9/Passion_fruit_%26_raspberry_cheesecake_-_Jacob%27s_Bakery_%2B_Cafe_2025-10-08.jpg/960px-Passion_fruit_%26_raspberry_cheesecake_-_Jacob%27s_Bakery_%2B_Cafe_2025-10-08.jpg'),
  ('REP-015', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/99/Strawberry_cheesecake_round.jpg/960px-Strawberry_cheesecake_round.jpg'),
  ('REP-016', 'https://upload.wikimedia.org/wikipedia/commons/8/8e/Coconut_cake_garnished_with_Peeps_candy.jpg'),
  ('REP-017', 'https://upload.wikimedia.org/wikipedia/commons/e/e3/Pudding_Corn.jpg'),
  ('REP-018', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/f2/Habichuelas_con_dulce_dominicana.jpg/960px-Habichuelas_con_dulce_dominicana.jpg'),
  ('REP-020', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b8/Bread_rolls_at_a_bakery_%28cropped%29.jpg/960px-Bread_rolls_at_a_bakery_%28cropped%29.jpg'),
  ('REP-021', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fd/Bread_rolls_at_Black_Horse_Inn%2C_Nuthurst_West_Sussex_England.jpg/960px-Bread_rolls_at_Black_Horse_Inn%2C_Nuthurst_West_Sussex_England.jpg'),
  ('REP-022', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/6/63/Kaiser_roll.JPG/960px-Kaiser_roll.JPG'),
  ('REP-023', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/2/2a/Croissant-Petr_Kratochvil.jpg/960px-Croissant-Petr_Kratochvil.jpg'),
  ('REP-024', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/2/20/Cheesebread.jpg/960px-Cheesebread.jpg'),
  ('REP-025', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/4/46/Vegan_no-knead_whole_wheat_bread_loaf%2C_September_2010.jpg/960px-Vegan_no-knead_whole_wheat_bread_loaf%2C_September_2010.jpg'),
  ('REP-026', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a5/Glazed-Donut.jpg/960px-Glazed-Donut.jpg'),
  ('REP-030', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/7/78/Tapa_de_empanadillitas.JPG/960px-Tapa_de_empanadillitas.JPG'),
  ('REP-031', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/4/4f/Empanadas%2C_chicken_and_cheese_-_Massachusetts.jpg/960px-Empanadas%2C_chicken_and_cheese_-_Massachusetts.jpg'),
  ('REP-032', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/95/Beef_Empanada_-_Malo_Argentinian_Empanadas%2C_Brighton_2023-11-25.jpg/960px-Beef_Empanada_-_Malo_Argentinian_Empanadas%2C_Brighton_2023-11-25.jpg'),
  ('REP-033', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fa/Traditional_fried_beef_empanadas_from_Argentina.jpg/960px-Traditional_fried_beef_empanadas_from_Argentina.jpg'),
  ('REP-034', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/8/88/Kibbeh3.jpg/960px-Kibbeh3.jpg'),
  ('REP-035', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/8/8b/Appetizer_Platter.jpg/960px-Appetizer_Platter.jpg'),
  ('REP-040', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/e/e5/2019-11-29_14_52_43_The_interior_of_a_tin_of_McKenzie_%26_Lloyds_Danish_Style_Butter_Cookies_in_the_Dulles_section_of_Sterling%2C_Loudoun_County%2C_Virginia.jpg/960px-2019-11-29_14_52_43_The_interior_of_a_tin_of_McKenzie_%26_Lloyds_Danish_Style_Butter_Cookies_in_the_Dulles_section_of_Sterling%2C_Loudoun_County%2C_Virginia.jpg'),
  ('REP-041', 'https://upload.wikimedia.org/wikipedia/commons/b/b4/Choco_chip_cookie.png'),
  ('REP-042', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/d/d4/MedinaSidonia-P1060062_%28cropped%29.JPG/960px-MedinaSidonia-P1060062_%28cropped%29.JPG'),
  ('REP-043', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0c/AlfajoresP1115215_02.jpg/960px-AlfajoresP1115215_02.jpg'),
  ('REP-050', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/4/41/Espresso_Americano.jpeg/960px-Espresso_Americano.jpeg'),
  ('REP-051', 'https://upload.wikimedia.org/wikipedia/commons/1/14/Hot_chocolate_mug_with_whipped_cream.jpg'),
  ('REP-052', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/1/14/Cocktail_of_passion_fruit_juice_and_watermelon_juice_in_a_glass.jpg/960px-Cocktail_of_passion_fruit_juice_and_watermelon_juice_in_a_glass.jpg'),
  ('REP-060', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/6/65/Birthday_Candle_7.JPG/960px-Birthday_Candle_7.JPG'),
  ('REP-061', 'https://thumb.wikimedia.org/wikipedia/commons/thumb/6/6b/Box_of_%22punyetes%22.jpg/960px-Box_of_%22punyetes%22.jpg')
) AS photo(codigo, url)
WHERE a.codigo = photo.codigo AND a.imagen_url IS DISTINCT FROM photo.url;

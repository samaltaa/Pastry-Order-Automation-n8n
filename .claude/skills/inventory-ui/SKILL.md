---
name: inventory-ui
description: Design and build the operator-facing web pages in this repo (above all sync-api/app/demo.html, the reviewer demo) in an inventory / back-office style - dense, data-first, calm, trustworthy - instead of a generic "AI landing page" look. Use this skill whenever the task touches demo.html or any HTML/CSS/JS page served by sync-api or ops-api, including requests like "make the demo nicer", "more user friendly", "looks vibe coded", "add a section/panel/table", "show X on the page", restyling, layout, colors, mobile view, or accessibility fixes, even if the user doesn't say "design".
---

# Inventory UI

The pages in this repo are tools for people who run a shop: an owner checking stock between customers, a reviewer at an agency judging whether this would hold up for a client. They should feel like a good inventory system (Shopify admin, Square for Retail, Linear's density), not like a marketing page. Trust comes from precision and restraint, not decoration.

## What "vibe coded" looks like, and what to do instead

Reviewers spot these instantly; each one signals "generated, not designed".

| Avoid | Prefer | Why |
|---|---|---|
| Big hero headline + paragraph of sales copy | A compact page header: title, one line of context, primary actions on the right | Operators came to work, not to be sold to |
| Every item as a large photo card | A table: one row per product, 40px thumbnail, columns that line up | Scanning 40 SKUs needs alignment; cards hide comparisons |
| Rounded-everything, soft shadows, gradients, emoji, arrow glyphs (↗ ✨) in labels | 1px borders, 6-8px radius, flat surfaces, plain words | Decoration competes with data |
| Several accent colors | One neutral palette + one brand accent + status colors with fixed meanings | Color must carry meaning, so it can't also be decoration |
| Numbers in proportional font, centered | `font-variant-numeric: tabular-nums`, right-aligned, units in the header | Columns of figures must line up digit by digit |
| Vague states ("Updated!") | Specific, countable feedback ("2 products synced · 1 written to the sheet · 14:02") | Operators reconcile; they need to know exactly what happened |
| Explaining the feature in prose | Labels, column headers and a short hint where an action isn't obvious | Interfaces teach by structure |

## Visual system

Define everything as CSS custom properties on `:root` and reuse them; never hard-code a color in a rule.

- **Neutrals:** a near-white page (`#f6f7f9`), white surfaces, borders around `#e3e6ea`, text `#1d2128`, secondary text `#5c6470`. Dark mode mirrors these under `@media (prefers-color-scheme: dark)`.
- **Accent:** one color for primary actions and links (a deep teal or ink blue reads as "operations"). Primary buttons are solid accent; everything else is a quiet bordered button.
- **Status colors, fixed meanings, used only for status:** green = in stock / synced, amber = low stock / pending edit, red = out of stock / failed, grey = hidden / skipped. Pair every color with a word, never color alone.
- **Type:** the system font stack at 14px for UI and tables (13px for secondary), 600 weight for headers. SKUs, IDs and codes in a monospace stack (`ui-monospace, SFMono-Regular, Menlo, monospace`) at 12-13px.
- **Spacing:** a 4px grid (4, 8, 12, 16, 24, 32). Table rows about 52px tall with a thumbnail, 40px without.
- **Elevation:** borders, not shadows. A shadow only on things that float (sticky action bar, popovers).

## Layout patterns that fit this domain

- **Header bar:** page title and source ("Inventory · Google Sheet"), then actions on the right (Open store, Open sheet).
- **Summary strip:** 3-5 KPI tiles in one row: products, units on hand, low stock, last sync. Big tabular number, small label. A KPI that needs attention (low stock > 0) uses its status color and is clickable to filter.
- **Attention list before the table:** items needing action (low stock) as a compact list, not a banner of cards.
- **Toolbar:** search on the left, filter as a segmented control or select (All · Low · Out · Hidden, then categories), counts in each filter.
- **The table:** columns Product (thumb + name + SKU under it), Category, Status, Price, On hand, Reorder at, In Shopify. Editable cells look like inputs only on hover/focus or when edited, so the table stays quiet. A row with unsaved edits gets an amber left edge.
- **Pending changes bar:** sticky at the bottom, "3 unsaved changes · Discard · Sync to Shopify". The primary button says what it will do.
- **Results:** a dismissible panel listing each change as a row (product, what changed, where it went), not a paragraph.
- **Secondary material** (mapping details, run history, CSV try-out) below the table or in `<details>`, styled as plain tables.
- **Phone:** the table collapses to stacked rows (thumbnail, name and status on the first line; price and stock inputs on the second). No horizontal page scroll; a 16px side gutter. Give the input columns fixed widths in the stacked grid; `auto` columns size per row, so fields drift out of line from one product to the next.
- **One definition per number.** If a KPI tile, a filter count and an attention list all count "low stock", they must use the same predicate. Mismatched counts on one screen destroy trust faster than any styling issue.
- **Tiles that are sometimes buttons** inherit button styles (gap, line-height, alignment). Reset them so clickable and static tiles line up, and check by measuring element positions, not by eye.

## Engineering rules in this repo

- One self-contained HTML file; no build step, no framework, no CDN.
- Build the DOM with the page's `el()` helper and `textContent`. Never `innerHTML` with data: product names, CSV cells and sheet values come from users and reviewers.
- Keep the existing API contract (`/demo/api/state`, `/demo/api/sync`, `/demo/api/csv`) and every behavior the tests and README rely on. Restyle around the data; don't rename fields.
- Accessibility: real `<table>` with `<th scope>`, labels on every input (`aria-label` is fine in a table), visible focus rings (`:focus-visible` with the accent), 4.5:1 text contrast, `aria-live="polite"` on result and status regions, buttons are `<button>`.
- Numbers: format money with two decimals and a `$` in the cell, counts as integers; "Sold out" instead of 0 where a customer-facing meaning is clearer.

## Verify like a reviewer

A page isn't done when it renders; it's done when someone can use it.

1. Rebuild and open it: `docker compose up -d --build sync-api`, then http://localhost:3003/demo.
2. Screenshot desktop (1280×900) and phone (390×900) with Playwright and look at both:
   `~/.local/bin/uv run --no-project --with playwright==1.55.0 python <script>` (Chromium is already installed).
3. Click through the main path in the script: edit a price, step stock up, see the pending bar, sync, read the result panel. Assert there are no console errors.
4. Check the phone screenshot for horizontal scroll and cramped inputs.
5. Run `uv run pytest` in `sync-api/`; `tests/test_demo.py` checks the page and its API.

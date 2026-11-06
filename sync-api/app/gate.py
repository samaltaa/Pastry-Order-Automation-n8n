"""Decides whether a proposed mapping is safe to apply without a person.

Claude proposes; these checks dispose. They run on the real rows and on what is already in Shopify,
not on Claude's opinion of itself. If every check passes, the mapping is approved automatically; if
any fails, it waits for the owner, and the failed checks say why.
"""

from app.mapping import REQUIRED_TARGETS, TRANSFORMS, Mapping, apply, is_excluded, problems
from app.sync import product_hash

MIN_CONFIDENCE = 0.8
MAX_CHANGED_SHARE = 0.10  # of the products already in Shopify


def check(
    mapping: Mapping, columns: list[str], rows: list[dict], previous: Mapping | None, known: dict[str, str]
) -> list[dict]:
    """Every check as {"name", "passed", "detail"}. known maps each synced SKU to its last data hash."""
    products = [(row, apply(mapping, row)) for row in rows if not is_excluded(mapping, row)]
    return [
        _structure(mapping, columns),
        _confidence(mapping),
        _readable(mapping, products),
        _unique_skus(products),
        _same_meaning(mapping, previous, columns),
        _store_impact(products, known),
    ]


def passed(checks: list[dict]) -> bool:
    return all(item["passed"] for item in checks)


def _result(name: str, failures: list[str], ok: str) -> dict:
    return {"name": name, "passed": not failures, "detail": "; ".join(failures[:3]) if failures else ok}


def _structure(mapping: Mapping, columns: list[str]) -> dict:
    return _result(
        "Structure", problems(mapping, columns), "every column mapped once; required fields present"
    )


def _confidence(mapping: Mapping) -> dict:
    unsure = [
        f"{field.source_column} -> {field.target} ({field.confidence:.0%})"
        for field in mapping.fields
        if field.target != "ignore" and field.confidence < MIN_CONFIDENCE
    ]
    return _result("Confidence", unsure, f"every field used is at least {MIN_CONFIDENCE:.0%} sure")


def _readable(mapping: Mapping, products: list[tuple[dict, dict]]) -> dict:
    """A value that's present but turns into nothing (a price of "call us", stock of "lots") is a
    sign the column means something else."""
    failures = []
    for row, product in products:
        key = row.get(mapping.key_column) or "(no key)"
        missing = sorted(target for target in REQUIRED_TARGETS if not product.get(target))
        if missing:
            failures.append(f"{key}: no {', '.join(missing)}")
        for field in mapping.fields:
            raw = row.get(field.source_column)
            if (
                field.target != "ignore"
                and raw not in (None, "")
                and TRANSFORMS[field.transform](str(raw)) is None
            ):
                failures.append(f"{key}: {field.source_column} '{raw}' isn't a valid {field.target}")
    return _result("Every row reads cleanly", failures, f"{len(products)} products, no unreadable values")


def _unique_skus(products: list[tuple[dict, dict]]) -> dict:
    seen, repeated = set(), set()
    for _, product in products:
        sku = product.get("sku")
        if sku in seen:
            repeated.add(sku)
        seen.add(sku)
    return _result(
        "SKUs are unique", [f"{sku} appears more than once" for sku in sorted(repeated)], "no repeats"
    )


def _same_meaning(mapping: Mapping, previous: Mapping | None, columns: list[str]) -> dict:
    """A column that keeps its name keeps its meaning: price can't quietly become cost."""
    if previous is None:
        return _result("Same meaning as before", [], "no earlier mapping to compare")
    before = {field.source_column: field.target for field in previous.fields}
    failures = [
        f"{field.source_column} was {before[field.source_column]}, now {field.target}"
        for field in mapping.fields
        if field.source_column in before and before[field.source_column] != field.target
    ]
    if previous.key_column in columns and previous.key_column != mapping.key_column:
        failures.append(f"key column changed from {previous.key_column} to {mapping.key_column}")
    return _result("Same meaning as before", failures, "columns that kept their name kept their meaning")


def _store_impact(products: list[tuple[dict, dict]], known: dict[str, str]) -> dict:
    """What applying the mapping would change in Shopify, stock aside. A renamed column with the same
    meaning changes nothing; swapped price and cost columns change everything."""
    compared = [
        (product["sku"], product_hash(product)) for _, product in products if product.get("sku") in known
    ]
    if not compared:
        return _result("Small effect on the store", ["nothing in Shopify to compare with yet"], "")
    changed = [sku for sku, data_hash in compared if known[sku] != data_hash]
    share = len(changed) / len(compared)
    failures = (
        [
            f"would change {len(changed)} of {len(compared)} products ({share:.0%}), "
            f"e.g. {', '.join(changed[:3])}"
        ]
        if share > MAX_CHANGED_SHARE
        else []
    )
    return _result(
        "Small effect on the store",
        failures,
        f"would change {len(changed)} of {len(compared)} products (limit {MAX_CHANGED_SHARE:.0%})",
    )

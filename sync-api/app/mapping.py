"""The column mapping: what Claude proposes, the checks it must pass, and the transforms that apply it.

Claude only chooses from the targets and transforms listed here. The transforms themselves are
plain Python, so once a mapping is approved every sync is deterministic.
"""

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

Target = Literal[
    "title", "sku", "price", "cost", "quantity", "product_type", "barcode", "taxable", "status", "ignore"
]
Transform = Literal[
    "none",
    "trim",
    "trim_upper",
    "title_case",
    "money",
    "integer",
    "yes_no",
    "active_status",
    "inactive_status",
]

REQUIRED_TARGETS = {"title", "sku", "price"}
REVIEW_BELOW_CONFIDENCE = 0.8


class FieldMapping(BaseModel):
    source_column: str
    target: Target
    transform: Transform
    confidence: float = Field(description="0 to 1: how sure you are this mapping is right")
    reason: str = Field(description="One short sentence a store owner can understand")


class ExcludedRow(BaseModel):
    key: str = Field(description="Value of the key column, exactly as in the data")
    reason: str


class Mapping(BaseModel):
    key_column: str = Field(description="Column that uniquely identifies each product")
    fields: list[FieldMapping]
    excluded_rows: list[ExcludedRow] = Field(description="Sample rows that are not real products")


def problems(mapping: Mapping, columns: list[str]) -> list[str]:
    """Reasons this mapping can't be approved. Empty means it's structurally sound."""
    found: list[str] = []
    mapped = [field.source_column for field in mapping.fields]
    for column in columns:
        if mapped.count(column) != 1:
            found.append(f"Column '{column}' must be mapped exactly once (found {mapped.count(column)})")
    for column in set(mapped) - set(columns):
        found.append(f"Column '{column}' does not exist in the table")
    if mapping.key_column not in columns:
        found.append(f"Key column '{mapping.key_column}' does not exist in the table")

    targets = [field.target for field in mapping.fields if field.target != "ignore"]
    for target in sorted(set(targets)):
        if targets.count(target) > 1:
            found.append(f"Shopify field '{target}' is mapped from more than one column")
    for target in sorted(REQUIRED_TARGETS - set(targets)):
        found.append(f"Shopify field '{target}' is required but not mapped")
    return found


def needs_review(mapping: Mapping) -> list[FieldMapping]:
    return [field for field in mapping.fields if field.confidence < REVIEW_BELOW_CONFIDENCE]


TRUE_WORDS = {"s", "si", "sí", "y", "yes", "true", "t", "1", "a", "activo", "active"}
FALSE_WORDS = {"n", "no", "false", "f", "0", "i", "inactivo", "inactive"}
LOWERCASE_WORDS = {"de", "del", "la", "el", "los", "las", "y", "con", "c/", "en", "and", "of", "with"}


def _flag(value: str) -> bool | None:
    word = value.strip().lower()
    return True if word in TRUE_WORDS else False if word in FALSE_WORDS else None


def _money(value: str) -> str | None:
    cleaned = "".join(char for char in value if char.isdigit() or char in ".-")
    try:
        return str(Decimal(cleaned).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _integer(value: str) -> int | None:
    try:
        return int(Decimal(value.strip().replace(",", "")))
    except InvalidOperation:
        return None


def _title_case_word(index: int, word: str) -> str:
    if index and word.lower() in LOWERCASE_WORDS:
        return word.lower()
    if word.replace(".", "").isalpha():
        return word.capitalize()
    return word  # sizes and codes such as 1LB or 16OZ stay as written


def _title_case(value: str) -> str:
    return " ".join(_title_case_word(index, word) for index, word in enumerate(value.strip().split()))


TRANSFORMS = {
    "none": lambda value: value,
    "trim": lambda value: value.strip() or None,
    "trim_upper": lambda value: value.strip().upper() or None,
    "title_case": lambda value: _title_case(value) or None,
    "money": _money,
    "integer": _integer,
    "yes_no": _flag,
    "active_status": lambda value: {True: "ACTIVE", False: "DRAFT"}.get(_flag(value)),
    "inactive_status": lambda value: {True: "DRAFT", False: "ACTIVE"}.get(_flag(value)),
}


def apply(mapping: Mapping, row: dict) -> dict:
    """Turns one source row into Shopify fields. Empty or unreadable values become None."""
    product = {}
    for field in mapping.fields:
        if field.target == "ignore":
            continue
        value = row.get(field.source_column)
        product[field.target] = None if value is None else TRANSFORMS[field.transform](str(value))
    return product


def is_excluded(mapping: Mapping, row: dict) -> bool:
    key = str(row.get(mapping.key_column, "")).strip().upper()
    return key in {excluded.key.strip().upper() for excluded in mapping.excluded_rows}

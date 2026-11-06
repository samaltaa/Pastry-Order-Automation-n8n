import pytest

from app.mapping import TRANSFORMS, Mapping, apply, is_excluded, needs_review, problems


def field(column, target, transform="none", confidence=0.9):
    return {
        "source_column": column,
        "target": target,
        "transform": transform,
        "confidence": confidence,
        "reason": "-",
    }


def make_mapping(*fields, key="codigo", excluded=()):
    return Mapping.model_validate(
        {
            "key_column": key,
            "fields": list(fields),
            "excluded_rows": [{"key": k, "reason": "-"} for k in excluded],
        }
    )


BASIC = [
    field("codigo", "sku", "trim_upper"),
    field("descripcion", "title"),
    field("precio", "price", "money"),
]
COLUMNS = ["codigo", "descripcion", "precio"]


@pytest.mark.parametrize(
    ("transform", "value", "expected"),
    [
        ("money", "$24.00", "24.00"),
        ("money", "US$ 28.00", "28.00"),
        ("money", "13.5", "13.50"),
        ("money", "RD$1,250.00", "1250.00"),
        ("money", "n/a", None),
        ("money", "RD$ 20,00", "20.00"),
        ("money", "1.250,50", "1250.50"),
        ("money", "1,250", "1250.00"),
        ("money", "2,5", "2.50"),
        ("integer", "12.00", 12),
        ("integer", "1,200", 1200),
        ("integer", "", None),
        ("trim_upper", "rep-006 ", "REP-006"),
        ("trim", "   ", None),
        ("title_case", "BIZCOCHO DE NARANJA 1LB", "Bizcocho de Naranja 1LB"),
        ("title_case", "HABICHUELAS C/ DULCE 16OZ", "Habichuelas c/ Dulce 16OZ"),
        ("title_case", "BIZC. DOM. VAINILLA 1/2 LB", "Bizc. Dom. Vainilla 1/2 Lb"),
        ("yes_no", "S", True),
        ("yes_no", "n", False),
        ("yes_no", "maybe", None),
        ("active_status", "S", "ACTIVE"),
        ("active_status", "I", "DRAFT"),
        ("inactive_status", "Y", "DRAFT"),
        ("inactive_status", "N", "ACTIVE"),
    ],
)
def test_transforms(transform, value, expected):
    assert TRANSFORMS[transform](value) == expected


def test_a_complete_mapping_has_no_problems():
    assert problems(make_mapping(*BASIC), COLUMNS) == []


def test_every_column_must_be_mapped_once_and_exist():
    mapping = make_mapping(*BASIC[:2], field("precio_x", "price"))
    assert problems(mapping, COLUMNS) == [
        "Column 'precio' must be mapped exactly once (found 0)",
        "Column 'precio_x' does not exist in the table",
    ]


def test_required_and_duplicate_targets():
    mapping = make_mapping(field("codigo", "sku"), field("descripcion", "sku"), field("precio", "ignore"))
    assert problems(mapping, COLUMNS) == [
        "Shopify field 'sku' is mapped from more than one column",
        "Shopify field 'price' is required but not mapped",
        "Shopify field 'title' is required but not mapped",
    ]


def test_low_confidence_fields_need_review():
    mapping = make_mapping(*BASIC[:2], field("precio", "price", "money", confidence=0.5))
    assert [f.source_column for f in needs_review(mapping)] == ["precio"]


def test_apply_skips_ignored_columns_and_keeps_missing_values_empty():
    mapping = make_mapping(*BASIC, field("nota", "ignore"))
    row = {"codigo": " rep-001", "descripcion": "Flan", "precio": None, "nota": "secret"}
    assert apply(mapping, row) == {"sku": "REP-001", "title": "Flan", "price": None}


def test_excluded_rows_match_despite_spacing_and_case():
    mapping = make_mapping(*BASIC, excluded=["REP-099"])
    assert is_excluded(mapping, {"codigo": "rep-099 "})
    assert not is_excluded(mapping, {"codigo": "REP-001"})

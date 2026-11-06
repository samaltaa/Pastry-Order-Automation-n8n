"""Reads and stores the data the sync starts from. A source is a table in one of two schemas:

- pos.<table>: the point-of-sale database
- sheets.<name>: the latest snapshot of a spreadsheet, sent by Make

Nothing outside these schemas can be read, so the API can never send other data (customers,
orders) to Claude. A bare name such as "articulos" means pos.articulos.
"""

from psycopg import Connection, sql

SOURCE_SCHEMAS = ("pos", "sheets")
SAMPLE_ROWS = 50
MAX_SHEET_ROWS = 2000
MAX_COLUMNS = 40
MAX_CELL_CHARS = 2000


class UnknownTable(ValueError):
    pass


class BadSheet(ValueError):
    pass


def qualify(source: str) -> tuple[str, str]:
    schema, _, table = source.rpartition(".")
    schema = schema or "pos"
    if schema not in SOURCE_SCHEMAS or not table:
        raise UnknownTable(f"No table '{source}'")
    return schema, table


def full_name(source: str) -> str:
    return ".".join(qualify(source))


def read_rows(conn: Connection, source: str, limit: int | None = None) -> list[dict]:
    """Rows as dicts with every value as text, the same shape Claude saw when it proposed the mapping."""
    query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(*qualify(source)))
    if limit:
        query += sql.SQL(" LIMIT {}").format(sql.Literal(limit))
    cursor = conn.execute(query)
    names = [column.name for column in cursor.description]
    return [
        {name: None if value is None else str(value) for name, value in zip(names, row, strict=True)}
        for row in cursor.fetchall()
    ]


def column_names(conn: Connection, source: str) -> list[str]:
    return [name for name, _ in _columns(conn, source)]


def _columns(conn: Connection, source: str) -> list[tuple[str, str]]:
    return conn.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        qualify(source),
    ).fetchall()


def describe_table(conn: Connection, source: str) -> dict:
    """Column names and types, plus sample rows."""
    columns = _columns(conn, source)
    if not columns:
        raise UnknownTable(f"No table '{full_name(source)}'")
    return {
        "table": full_name(source),
        "columns": [{"name": name, "type": data_type} for name, data_type in columns],
        "rows": read_rows(conn, source, SAMPLE_ROWS),
    }


def store_sheet(conn: Connection, name: str, values: list[list]) -> list[str]:
    """Replaces sheets.<name> with a snapshot: the header row, then data rows, as Google returns them.

    The table is recreated when the headers change, so its columns always match the sheet's.
    Blank rows are dropped; short rows (Google leaves out trailing empty cells) are padded.
    """
    if len(values) < 2:
        raise BadSheet("The sheet needs a header row and at least one row of data")
    if len(values) > MAX_SHEET_ROWS + 1:
        raise BadSheet(f"Up to {MAX_SHEET_ROWS} rows")
    header = [_cell(value) or "" for value in values[0]]
    while header and not header[-1]:
        header.pop()
    if not header or not all(header) or len(set(header)) != len(header):
        raise BadSheet("Every column needs a unique, non-empty name in the header row")
    if len(header) > MAX_COLUMNS or any(len(name) > 63 for name in header):
        raise BadSheet(f"Up to {MAX_COLUMNS} columns, with names up to 63 characters")

    rows = []
    for raw in values[1:]:
        row = [_cell(value) for value in raw[: len(header)]]
        if any(row):
            rows.append(row + [None] * (len(header) - len(row)))

    table = sql.Identifier("sheets", name)
    if column_names(conn, f"sheets.{name}") != header:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                table, sql.SQL(", ").join(sql.SQL("{} TEXT").format(sql.Identifier(c)) for c in header)
            )
        )
    else:
        conn.execute(sql.SQL("DELETE FROM {}").format(table))
    with conn.cursor() as cursor:
        cursor.executemany(
            sql.SQL("INSERT INTO {} VALUES ({})").format(
                table, sql.SQL(", ").join(sql.Placeholder() * len(header))
            ),
            rows,
        )
    return header


def _cell(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > MAX_CELL_CHARS:
        raise BadSheet(f"Cells can hold up to {MAX_CELL_CHARS} characters")
    return text or None

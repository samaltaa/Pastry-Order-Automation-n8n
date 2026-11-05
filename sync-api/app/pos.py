"""Reads the point-of-sale tables. Only tables in the `pos` schema can be read, so the API
can never send other data (customers, orders) to Claude."""

from psycopg import Connection, sql

POS_SCHEMA = "pos"
SAMPLE_ROWS = 50


class UnknownTable(ValueError):
    pass


def read_rows(conn: Connection, table: str, limit: int | None = None) -> list[dict]:
    """Rows as dicts with every value as text, the same shape Claude saw when it proposed the mapping."""
    query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(POS_SCHEMA, table))
    if limit:
        query += sql.SQL(" LIMIT {}").format(sql.Literal(limit))
    cursor = conn.execute(query)
    names = [column.name for column in cursor.description]
    return [
        {name: None if value is None else str(value) for name, value in zip(names, row, strict=True)}
        for row in cursor.fetchall()
    ]


def describe_table(conn: Connection, table: str) -> dict:
    """Column names and types, plus sample rows."""
    columns = conn.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (POS_SCHEMA, table),
    ).fetchall()
    if not columns:
        raise UnknownTable(f"No table '{POS_SCHEMA}.{table}'")
    return {
        "table": f"{POS_SCHEMA}.{table}",
        "columns": [{"name": name, "type": data_type} for name, data_type in columns],
        "rows": read_rows(conn, table, SAMPLE_ROWS),
    }

"""Reads the point-of-sale tables. Only tables in the `pos` schema can be read, so the API
can never send other data (customers, orders) to Claude."""

from psycopg import Connection, sql

POS_SCHEMA = "pos"
SAMPLE_ROWS = 50


class UnknownTable(ValueError):
    pass


def describe_table(conn: Connection, table: str) -> dict:
    """Column names and types, plus sample rows with every value as text."""
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

    cursor = conn.execute(
        sql.SQL("SELECT * FROM {} LIMIT {}").format(
            sql.Identifier(POS_SCHEMA, table), sql.Literal(SAMPLE_ROWS)
        )
    )
    names = [column.name for column in cursor.description]
    rows = [
        {name: None if value is None else str(value) for name, value in zip(names, row, strict=True)}
        for row in cursor.fetchall()
    ]
    return {
        "table": f"{POS_SCHEMA}.{table}",
        "columns": [{"name": name, "type": data_type} for name, data_type in columns],
        "rows": rows,
    }

import hmac
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Literal

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from app import mapping as mappings
from app.config import Settings
from app.llm import MappingFailed, log_call, propose_mapping, spent_today
from app.pos import UnknownTable, describe_table
from app.shopify import ShopifyClient
from app.sync import NoApprovedMapping, SyncAlreadyRunning, run_sync

SHOP_QUERY = """
{
  shop { name currencyCode }
  locations(first: 10) { nodes { id name } }
}
"""
PREVIEW_ROWS = 5

Mapper = Callable[[dict], tuple[mappings.Mapping, dict]]


class ProposeRequest(BaseModel):
    table: str


class SyncRequest(BaseModel):
    table: str = "articulos"
    dry_run: bool = False
    trigger: Literal["manual", "schedule"] = "manual"


def create_app(
    settings: Settings | None = None,
    shopify: ShopifyClient | None = None,
    mapper: Mapper | None = None,
) -> FastAPI:
    """Builds the app. Tests pass their own settings, a fake Shopify client and a fake mapper."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = ConnectionPool(settings.database_url, min_size=1, max_size=5, timeout=5, open=True)
        app.state.shopify = shopify or ShopifyClient(
            settings.shopify_store,
            settings.shopify_client_id,
            settings.shopify_client_secret,
            settings.shopify_api_version,
        )
        claude = None if mapper else anthropic.Anthropic(timeout=120, max_retries=2)
        app.state.mapper = mapper or (
            lambda table: propose_mapping(claude, settings.anthropic_model_strong, table)
        )
        yield
        if claude:
            claude.close()
        app.state.shopify.close()
        app.state.pool.close()

    app = FastAPI(title="Pastry Ops sync", lifespan=lifespan)

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_api_key.encode(), settings.ops_api_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid API key")

    def read_table(pool: ConnectionPool, name: str) -> dict:
        with pool.connection() as conn:
            try:
                return describe_table(conn, name)
            except UnknownTable as error:
                raise HTTPException(status_code=404, detail=str(error)) from error

    def report(mapping_id: int, status: str, mapping: mappings.Mapping, table: dict) -> dict:
        """What a reviewer needs to decide: the mapping, what's wrong, what's unsure, and sample output."""
        columns = [column["name"] for column in table["columns"]]
        return {
            "id": mapping_id,
            "status": status,
            "source_table": table["table"],
            "mapping": mapping.model_dump(),
            "problems": mappings.problems(mapping, columns),
            "needs_review": [field.source_column for field in mappings.needs_review(mapping)],
            "preview": [
                {
                    "key": row.get(mapping.key_column),
                    "excluded": mappings.is_excluded(mapping, row),
                    "shopify": mappings.apply(mapping, row),
                }
                for row in table["rows"][:PREVIEW_ROWS]
            ],
        }

    def load(pool: ConnectionPool, mapping_id: int) -> tuple[str, str, mappings.Mapping]:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT source_table, status, mapping FROM sync.mappings WHERE id = %s", (mapping_id,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No mapping {mapping_id}")
        return row[0], row[1], mappings.Mapping.model_validate(row[2])

    @app.get("/health")
    def health(request: Request):
        with request.app.state.pool.connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
        return {"ok": True, "db": row == (1,)}

    @app.get("/shopify/shop", dependencies=[Depends(require_api_key)])
    def shopify_shop(request: Request):
        return request.app.state.shopify.graphql(SHOP_QUERY)

    @app.post("/mappings", dependencies=[Depends(require_api_key)])
    def propose(body: ProposeRequest, request: Request):
        pool = request.app.state.pool
        table = read_table(pool, body.table)
        with pool.connection() as conn:
            if spent_today(conn) >= settings.daily_llm_budget_usd:
                raise HTTPException(status_code=429, detail="Daily AI budget reached")

        try:
            mapping, call = request.app.state.mapper(table)
        except Exception as error:
            failed = getattr(error, "call", {}) or {"model": settings.anthropic_model_strong}
            with pool.connection() as conn:
                log_call(conn, table["table"], {**failed, "outcome": "error", "error": str(error)[:500]})
            status = 502 if isinstance(error, (MappingFailed, anthropic.APIError)) else 500
            raise HTTPException(status_code=status, detail="Claude could not propose a mapping") from error

        with pool.connection() as conn:
            log_call(conn, table["table"], {**call, "outcome": "ok"})
            mapping_id = conn.execute(
                "INSERT INTO sync.mappings (source_table, mapping, model) VALUES (%s, %s, %s) RETURNING id",
                (table["table"], Jsonb(mapping.model_dump()), call["model"]),
            ).fetchone()[0]
        return report(mapping_id, "proposed", mapping, table)

    @app.get("/mappings/{mapping_id}", dependencies=[Depends(require_api_key)])
    def show(mapping_id: int, request: Request):
        source_table, status, mapping = load(request.app.state.pool, mapping_id)
        table = read_table(request.app.state.pool, source_table.split(".", 1)[1])
        return report(mapping_id, status, mapping, table)

    @app.post("/mappings/{mapping_id}/{decision}", dependencies=[Depends(require_api_key)])
    def review(mapping_id: int, decision: str, request: Request):
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=404, detail="Use approve or reject")
        pool = request.app.state.pool
        source_table, status, mapping = load(pool, mapping_id)
        if status != "proposed":
            raise HTTPException(status_code=409, detail=f"Mapping {mapping_id} is already {status}")
        if decision == "approve":
            table = read_table(pool, source_table.split(".", 1)[1])
            found = mappings.problems(mapping, [column["name"] for column in table["columns"]])
            if found:
                raise HTTPException(status_code=422, detail=found)

        new_status = "approved" if decision == "approve" else "rejected"
        with pool.connection() as conn:
            conn.execute(
                "UPDATE sync.mappings SET status = %s, reviewed_at = NOW() WHERE id = %s",
                (new_status, mapping_id),
            )
        return {"id": mapping_id, "status": new_status}

    @app.post("/sync", dependencies=[Depends(require_api_key)])
    def sync(body: SyncRequest, request: Request):
        with request.app.state.pool.connection() as conn:
            try:
                return run_sync(
                    conn,
                    request.app.state.shopify,
                    body.table,
                    body.dry_run,
                    body.trigger,
                    settings.shopify_location_id,
                )
            except (NoApprovedMapping, SyncAlreadyRunning) as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/sync/runs", dependencies=[Depends(require_api_key)])
    def sync_runs(request: Request, limit: int = 10):
        with request.app.state.pool.connection() as conn:
            return (
                conn.cursor(row_factory=dict_row)
                .execute("SELECT * FROM sync.runs ORDER BY id DESC LIMIT %s", (min(limit, 100),))
                .fetchall()
            )

    return app

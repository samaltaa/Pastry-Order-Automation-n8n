import hmac
from contextlib import asynccontextmanager
from typing import Literal

import anthropic
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse, RedirectResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from app import commands
from app.config import Settings
from app.demo import demo_router
from app.llm import propose_mapping
from app.service import Mapper, Post, Problem, Service
from app.shopify import ShopifyClient
from app.sources import MAX_SHEET_ROWS

SHOP_QUERY = """
{
  shop { name currencyCode }
  locations(first: 10) { nodes { id name } }
}
"""


class ProposeRequest(BaseModel):
    table: str


class SyncRequest(BaseModel):
    table: str | None = None  # defaults to the active source (SYNC_SOURCE)
    dry_run: bool = False
    trigger: Literal["manual", "schedule"] = "manual"


class CommandRequest(BaseModel):
    text: str


class SheetSnapshot(BaseModel):
    """A whole sheet as Google's values API returns it: the header row first. Other fields are ignored."""

    model_config = ConfigDict(extra="ignore")
    values: list[list[str | int | float | bool | None]] = Field(max_length=MAX_SHEET_ROWS + 1)


def key_matches(given: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def create_app(
    settings: Settings | None = None,
    shopify: ShopifyClient | None = None,
    mapper: Mapper | None = None,
    post: Post | None = None,
) -> FastAPI:
    """Builds the app. Tests pass their own settings, a fake Shopify client, a fake mapper and a
    recorder in place of outgoing HTTP calls."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = ConnectionPool(settings.database_url, min_size=1, max_size=5, timeout=5, open=True)
        shopify_client = shopify or ShopifyClient(
            settings.shopify_store,
            settings.shopify_client_id,
            settings.shopify_client_secret,
            settings.shopify_api_version,
        )
        claude = None if mapper else anthropic.Anthropic(timeout=120, max_retries=2)
        http = httpx.Client(timeout=15) if post is None else None

        def post_json(url: str, body: dict) -> None:
            http.post(url, json=body).raise_for_status()

        app.state.service = Service(
            settings,
            pool,
            shopify_client,
            mapper or (lambda table: propose_mapping(claude, settings.anthropic_model_strong, table)),
            post_json if post is None else post,
        )
        yield
        if claude:
            claude.close()
        if http:
            http.close()
        shopify_client.close()
        pool.close()

    app = FastAPI(title="Pastry Ops sync", lifespan=lifespan)

    @app.exception_handler(Problem)
    def problem_response(request: Request, problem: Problem):
        return JSONResponse(status_code=problem.status, content={"detail": problem.detail})

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        if not key_matches(x_api_key, settings.ops_api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    def require_sheet_key(x_api_key: str = Header(default="")) -> None:
        """Make gets its own key, good for this one endpoint, not the key to the whole API."""
        if not key_matches(x_api_key, settings.sheet_ingest_key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    def service(request: Request) -> Service:
        return request.app.state.service

    @app.get("/health")
    def health(request: Request):
        with service(request).pool.connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
        return {"ok": True, "db": row == (1,)}

    @app.get("/shopify/shop", dependencies=[Depends(require_api_key)])
    def shopify_shop(request: Request):
        return service(request).shopify.graphql(SHOP_QUERY)

    @app.post("/mappings", dependencies=[Depends(require_api_key)])
    def propose(body: ProposeRequest, request: Request):
        return service(request).propose(body.table)

    @app.get("/mappings/{mapping_id}", dependencies=[Depends(require_api_key)])
    def show(mapping_id: int, request: Request):
        return service(request).show(mapping_id)

    @app.post("/mappings/{mapping_id}/{decision}", dependencies=[Depends(require_api_key)])
    def review(mapping_id: int, decision: str, request: Request):
        return service(request).review(mapping_id, decision)

    @app.post("/sync", dependencies=[Depends(require_api_key)])
    def sync(body: SyncRequest, request: Request):
        return service(request).sync(body.table, body.dry_run, body.trigger)

    @app.get("/sync/runs", dependencies=[Depends(require_api_key)])
    def sync_runs(request: Request, limit: int = 10):
        return service(request).runs(limit)

    @app.post("/commands", dependencies=[Depends(require_api_key)])
    def command(body: CommandRequest, request: Request):
        """Owner commands from Telegram, forwarded by n8n."""
        return {"reply": commands.handle(service(request), body.text)}

    if settings.sheet_ingest_key:

        @app.post("/sources/sheets/{name}", dependencies=[Depends(require_sheet_key)])
        def ingest_sheet(
            body: SheetSnapshot, request: Request, name: str = Path(pattern=r"^[a-z0-9_]{1,40}$")
        ):
            """Make sends the whole sheet each time it changes. Unchanged rows cost nothing to sync."""
            result = service(request).ingest_sheet(name, body.values)
            if proposal := result.pop("new_proposal", None):
                why = (
                    f"The {name} sheet needs a mapping before it can sync."
                    if proposal["first"]
                    else f"The {name} sheet's columns changed, so its sync is paused until you approve "
                    "a new mapping."
                )
                service(request).notify_owner(f"{why}\n\n{commands.proposal_reply(proposal)}")
            return result

    if settings.demo_enabled:
        app.include_router(demo_router(settings))

        @app.get("/", include_in_schema=False)
        def home():
            return RedirectResponse("/demo")

    return app

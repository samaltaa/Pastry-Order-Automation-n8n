import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from psycopg_pool import ConnectionPool

from app.config import Settings
from app.shopify import ShopifyClient

SHOP_QUERY = """
{
  shop { name currencyCode }
  locations(first: 10) { nodes { id name } }
}
"""


def create_app(settings: Settings | None = None, shopify: ShopifyClient | None = None) -> FastAPI:
    """Builds the app. Tests pass their own settings and a fake Shopify client."""
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
        yield
        app.state.shopify.close()
        app.state.pool.close()

    app = FastAPI(title="Pastry Ops sync", lifespan=lifespan)

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_api_key.encode(), settings.ops_api_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/health")
    def health(request: Request):
        with request.app.state.pool.connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
        return {"ok": True, "db": row == (1,)}

    @app.get("/shopify/shop", dependencies=[Depends(require_api_key)])
    def shopify_shop(request: Request):
        return request.app.state.shopify.graphql(SHOP_QUERY)

    return app

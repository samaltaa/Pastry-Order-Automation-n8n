from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from environment variables (case-insensitive). A missing required one stops startup."""

    database_url: str
    ops_api_key: str
    shopify_store: str
    shopify_client_id: str
    shopify_client_secret: str
    shopify_api_version: str = "2026-10"
    shopify_location_id: str | None = None
    anthropic_model_strong: str = "claude-sonnet-5-5"
    daily_llm_budget_usd: float = 2.0

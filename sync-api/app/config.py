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
    # Where the inventory lives: a POS table ("articulos") or a spreadsheet ("sheets.inventory").
    # The n8n schedule, the Telegram commands and the demo page all use it. Only one source is synced
    # at a time, so two systems never overwrite each other's stock.
    sync_source: str = "articulos"
    demo_enabled: bool = False
    storefront_url: str | None = None
    storefront_password: str | None = None
    # Spreadsheet pipeline (Make). Without SHEET_INGEST_KEY the ingest endpoint is off.
    sheet_ingest_key: str | None = None
    make_sheet_webhook_url: str | None = None
    sheet_url: str | None = None
    # Where Shopify sends stock changes (online sales, cancellations, admin edits), e.g.
    # https://sync.<domain>/webhooks/shopify. Registered with Shopify at startup.
    shopify_webhook_url: str | None = None
    # Approve a sheet's new mapping without the owner when every deterministic check passes (app/gate.py).
    # A mapping that fails any check still waits for /approve.
    auto_approve_mappings: bool = True
    # Owner alerts when a sheet's columns change and a new mapping needs approval
    telegram_bot_token: str | None = None
    owner_telegram_chat_id: str | None = None

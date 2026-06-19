from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_ids: List[int] = Field(default_factory=list, alias="ADMIN_IDS")
    database_url: str = Field(..., alias="DATABASE_URL")

    app_host: str = Field("0.0.0.0", alias="APP_HOST")
    app_port: int = Field(8000, alias="APP_PORT")

    payment_webhook_secret: str = Field("", alias="PAYMENT_WEBHOOK_SECRET")

    # Optional payment provider keys. Provider auto-registers if its key(s) set.
    payment_provider_default: str = Field("manual", alias="PAYMENT_PROVIDER_DEFAULT")
    payment_return_url: str = Field("", alias="PAYMENT_RETURN_URL")
    payment_webhook_base_url: str = Field("", alias="PAYMENT_WEBHOOK_BASE_URL")

    # Platform domain used to detect client subdomains (e.g. slug.shopplatform.app).
    # Override via PLATFORM_DOMAIN env var.
    platform_domain: str = Field("shopplatform.app", alias="PLATFORM_DOMAIN")

    mono_token: str = Field("", alias="MONO_TOKEN")

    liqpay_public_key: str = Field("", alias="LIQPAY_PUBLIC_KEY")
    liqpay_private_key: str = Field("", alias="LIQPAY_PRIVATE_KEY")

    # Optional Cloudinary (for bot photo uploads). All three must be set to enable.
    cloudinary_cloud_name: str = Field("", alias="CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key: str = Field("", alias="CLOUDINARY_API_KEY")
    cloudinary_api_secret: str = Field("", alias="CLOUDINARY_API_SECRET")

    # Base URL for personal client bot webhooks.
    # Example: https://shopplatform.app
    # If empty, personal client bots will not be started automatically.
    client_bot_webhook_base: str = Field("", alias="CLIENT_BOT_WEBHOOK_BASE")

    # Railway deploy settings for personal bot mode (technomarket_premium).
    # Set TECHNOMARKET_CLIENT_REPO to the GitHub repo slug used as deploy template.
    technomarket_client_repo: str = Field(
        "prostoArtemG/technomarket_client_template",
        alias="TECHNOMARKET_CLIENT_REPO",
    )
    client_deploy_enabled: bool = Field(False, alias="CLIENT_DEPLOY_ENABLED")
    railway_api_token: str = Field("", alias="RAILWAY_API_TOKEN")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        return v


settings = Settings()

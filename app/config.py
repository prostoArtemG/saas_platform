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

    mono_token: str = Field("", alias="MONO_TOKEN")

    liqpay_public_key: str = Field("", alias="LIQPAY_PUBLIC_KEY")
    liqpay_private_key: str = Field("", alias="LIQPAY_PRIVATE_KEY")

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

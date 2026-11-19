from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    apify_api_token: str | None = Field(default=None, validation_alias="APIFY_API_TOKEN")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")

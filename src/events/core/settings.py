from typing import Any

from pydantic import SecretStr
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mongodb_uri: SecretStr | None = None
    mongodb_database: str = "eventsurfer"

    @field_validator("mongodb_uri", mode="before")
    @classmethod
    def empty_mongodb_uri_is_unconfigured(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from events.models import SourceConfig

UserId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
    ),
]


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Rome"
    sources: list[SourceConfig] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def source_identities_are_unique(self) -> "UserSettings":
        identities = [source.identity for source in self.sources]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Source name and platform pairs must be unique (case-insensitive)."
            )
        return self

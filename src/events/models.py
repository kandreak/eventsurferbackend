from __future__ import annotations

from datetime import date as DateValue
from datetime import datetime
from datetime import time as TimeValue
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

Platform = Literal["instagram", "facebook"]
NonEmptyString = Annotated[str, Field(min_length=1)]


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyString | None = None
    platform: Platform
    url: HttpUrl
    lookback_days: int = Field(gt=0)

    @property
    def display_name(self) -> str:
        return self.name or str(self.url)

    @property
    def identity(self) -> tuple[str, Platform]:
        return self.display_name.casefold(), self.platform


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    sources: list[SourceConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def source_identities_are_unique(self) -> SourcesConfig:
        identities = [source.identity for source in self.sources]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Source name and platform pairs must be unique "
                "(case-insensitive)."
            )
        return self


class SocialPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: NonEmptyString
    platform: Platform
    external_id: NonEmptyString
    post_url: HttpUrl
    caption: str | None = None
    published_at: datetime | None = None
    media_urls: list[HttpUrl] = Field(default_factory=list)
    location: str | None = None
    author: str | None = None
    raw_provider_data: dict[str, Any] = Field(
        default_factory=dict,
        exclude=True,
        repr=False,
    )

    @field_validator("published_at")
    @classmethod
    def publication_date_must_be_timezone_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("published_at must include a timezone.")
        return value


class ExtractedEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: NonEmptyString
    description: str | None = None
    date: DateValue | None = None
    start_time: TimeValue | None = None
    end_time: TimeValue | None = None
    venue: str | None = None
    address: str | None = None
    city: str | None = None
    participants: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool
    date_is_explicit: bool
    time_is_explicit: bool

    @model_validator(mode="after")
    def missing_date_requires_review(self) -> ExtractedEvent:
        if self.date is None:
            self.needs_review = True
        return self


class Event(ExtractedEvent):
    model_config = ConfigDict(extra="forbid")

    source_post_url: HttpUrl
    source_name: NonEmptyString


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[ExtractedEvent] = Field(default_factory=list)


class GroupedExtractedEvent(ExtractedEvent):
    post_key: NonEmptyString


class GroupedExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[GroupedExtractedEvent] = Field(default_factory=list)

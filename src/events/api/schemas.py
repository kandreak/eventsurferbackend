from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from events.models import Event, EventStatus, SourceConfig

UserId = Annotated[
    str,
    Field(min_length=1, max_length=128),
]
RunStage = Literal["collect", "analyze", "publish"]
RunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "stopped",
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


class APIValidationIssue(BaseModel):
    location: list[str | int]
    message: str
    type: str


class APIError(BaseModel):
    code: str
    message: str
    details: list[APIValidationIssue] | None = None


class APIErrorResponse(BaseModel):
    error: APIError


class StoredEvent(Event):
    event_id: str = Field(min_length=1)
    status: EventStatus = "active"
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)
    message: str | None = None


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: RunStage
    message: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class StoredRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    user_id: UserId
    status: RunStatus
    cancellation_requested: bool = False
    current_stage: RunStage
    progress: RunProgress
    report: dict[str, Any] | None = None
    checkpoints: dict[RunStage, datetime] = Field(default_factory=dict)
    settings_snapshot: UserSettings
    attempt: int = Field(ge=0)
    error: RunError | None = None
    errors: list[RunError] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator(
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    )
    @classmethod
    def timestamps_are_utc(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("checkpoints")
    @classmethod
    def checkpoint_timestamps_are_utc(
        cls, value: dict[RunStage, datetime]
    ) -> dict[RunStage, datetime]:
        return {
            stage: (
                timestamp.replace(tzinfo=UTC)
                if timestamp.utcoffset() is None
                else timestamp.astimezone(UTC)
            )
            for stage, timestamp in value.items()
        }


class StopUserRunsResponse(BaseModel):
    stopped: int = Field(ge=0)
    cancellation_requested: int = Field(ge=0)

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from events.models import Platform


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Rome"

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value


class ActorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    input_template: dict[str, Any] = Field(default_factory=dict)
    field_map: dict[str, str] = Field(default_factory=dict)


class CollectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_posts_per_source: int = Field(default=50, gt=0)
    max_images_per_post: int = Field(default=5, ge=0)
    max_total_charge_usd: float = Field(default=1.0, gt=0)
    actors: dict[Platform, ActorSettings]

    @model_validator(mode="after")
    def actors_have_input_templates(self) -> CollectionSettings:
        for platform, actor in self.actors.items():
            if actor.id and not actor.input_template:
                raise ValueError(
                    f"collection.actors.{platform}.input_template is required "
                    "when an Actor ID is configured."
                )
        return self


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["gemini"] = "gemini"
    model: str = Field(min_length=1)
    max_concurrent_requests: int = Field(default=3, gt=0)
    max_retries: int = Field(default=2, ge=0)


class RunExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrent_runs: int = Field(gt=0)
    max_concurrent_runs_per_user: int = Field(gt=0)
    max_outstanding_runs_per_user: int = Field(gt=0)
    lease_duration_seconds: int = Field(gt=0)
    dispatch_poll_interval_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def limits_are_consistent(self) -> RunExecutionSettings:
        if self.max_concurrent_runs_per_user > self.max_concurrent_runs:
            raise ValueError(
                "run_execution.max_concurrent_runs_per_user cannot exceed "
                "max_concurrent_runs."
            )
        if (
            self.max_outstanding_runs_per_user
            < self.max_concurrent_runs_per_user
        ):
            raise ValueError(
                "run_execution.max_outstanding_runs_per_user cannot be less "
                "than max_concurrent_runs_per_user."
            )
        return self


class SettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application: ApplicationSettings = Field(default_factory=ApplicationSettings)
    collection: CollectionSettings
    ai: AISettings
    run_execution: RunExecutionSettings


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except OSError as exc:
        raise ValueError(f"Could not read configuration file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must contain a JSON object.")
    return data


def load_settings(settings_path: Path) -> SettingsConfig:
    return SettingsConfig.model_validate(_read_json(settings_path))

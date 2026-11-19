from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from events.models import Platform, SourcesConfig


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Rome"
    output_directory: Path = Path("output")

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


class CalendarSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(default="events.ics", min_length=1)

    @field_validator("filename")
    @classmethod
    def filename_must_be_a_file_name(cls, value: str) -> str:
        if Path(value).name != value:
            raise ValueError("calendar.filename must be a file name, not a path.")
        return value


class ReportingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    json_enabled: bool = True
    text_report_enabled: bool = True


class SettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application: ApplicationSettings = Field(default_factory=ApplicationSettings)
    collection: CollectionSettings
    ai: AISettings
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
    reporting: ReportingSettings = Field(default_factory=ReportingSettings)


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


def load_config(
    sources_path: Path, settings_path: Path
) -> tuple[SourcesConfig, SettingsConfig]:
    sources = SourcesConfig.model_validate(_read_json(sources_path))
    settings = SettingsConfig.model_validate(_read_json(settings_path))
    missing_actors = {
        source.platform
        for source in sources.sources
        if source.platform not in settings.collection.actors
    }
    if missing_actors:
        platforms = ", ".join(sorted(missing_actors))
        raise ValueError(
            "Missing collection.actors settings for source platform(s): "
            f"{platforms}."
        )
    return sources, settings

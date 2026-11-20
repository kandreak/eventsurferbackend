import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from events.api.schemas import UserId, UserSettings
from events.config import load_settings
from events.models import Event, SocialPost, SourceConfig


ROOT = Path(__file__).parents[1]


def test_sample_application_settings_load():
    settings = load_settings(ROOT / "config" / "settings.json")
    assert settings.application.timezone == "Europe/Rome"
    assert settings.collection.max_posts_per_source == 30
    assert settings.collection.max_images_per_post == 10
    assert settings.collection.actors["instagram"].id == "apify/instagram-scraper"
    assert settings.collection.actors["instagram"].field_map == {
        "external_id": "id",
        "post_url": "url",
        "caption": "caption",
        "published_at": "timestamp",
        "media_urls": "images",
        "author": "ownerUsername",
    }
    assert settings.ai.provider == "gemini"
    assert settings.run_execution.max_concurrent_runs == 5
    assert settings.run_execution.max_concurrent_runs_per_user == 1
    assert settings.run_execution.max_outstanding_runs_per_user == 1
    assert settings.run_execution.dispatch_poll_interval_seconds == 1
    assert settings.collection.actors["facebook"].field_map["published_at"] == "createdAt"


def test_run_execution_limits_are_validated(tmp_path):
    settings_path = ROOT / "config" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    data["run_execution"]["max_concurrent_runs_per_user"] = 6
    invalid_path = tmp_path / "settings.json"
    invalid_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError, match="cannot exceed"):
        load_settings(invalid_path)


def test_run_execution_supports_a_larger_per_user_queue(tmp_path):
    settings_path = ROOT / "config" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    data["run_execution"]["max_outstanding_runs_per_user"] = 3
    configured_path = tmp_path / "settings.json"
    configured_path.write_text(json.dumps(data), encoding="utf-8")

    settings = load_settings(configured_path)

    assert settings.run_execution.max_outstanding_runs_per_user == 3
    assert settings.run_execution.max_concurrent_runs_per_user == 1


def test_invalid_json_configuration_is_reported(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("settings: invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        load_settings(settings_path)


def test_user_id_cannot_escape_its_api_scope():
    with pytest.raises(ValidationError, match="pattern"):
        TypeAdapter(UserId).validate_python("../another-user")


def test_source_name_is_optional_and_falls_back_to_the_full_url():
    source = SourceConfig(
        platform="instagram",
        url="https://www.instagram.com/example_venue/",
        lookback_days=30,
    )

    assert source.display_name == str(source.url)


def test_source_identity_is_name_and_platform_or_url_and_platform():
    def source(name, platform, url):
        return SourceConfig(
            name=name,
            platform=platform,
            url=url,
            lookback_days=30,
        )

    UserSettings(
        sources=[
            source("Example", "instagram", "https://instagram.com/example/"),
            source("Example", "facebook", "https://facebook.com/example/"),
            source(None, "instagram", "https://instagram.com/other/"),
            source(None, "instagram", "https://instagram.com/yet-another/"),
        ],
    )
    with pytest.raises(ValidationError, match="name and platform"):
        UserSettings(
            sources=[
                source("Example", "instagram", "https://instagram.com/example/"),
                source("Example", "instagram", "https://instagram.com/another/"),
            ],
        )
    with pytest.raises(ValidationError, match="name and platform"):
        UserSettings(
            sources=[
                source(None, "instagram", "https://instagram.com/example/"),
                source(None, "instagram", "https://instagram.com/example/"),
            ],
        )


def test_social_post_rejects_naive_publication_timestamps():
    with pytest.raises(ValidationError, match="timezone"):
        SocialPost(
            source_name="Example",
            platform="instagram",
            external_id="1",
            post_url="https://www.instagram.com/p/example/",
            published_at="2026-11-18T10:00:00",
        )


def test_event_without_date_is_marked_for_review():
    event = Event(
        title="Unscheduled announcement",
        confidence=0.7,
        needs_review=False,
        date_is_explicit=False,
        time_is_explicit=False,
        source_post_url="https://www.facebook.com/example/posts/1",
        source_name="Example",
    )

    assert event.needs_review

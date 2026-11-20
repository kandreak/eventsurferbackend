from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from pydantic import HttpUrl, TypeAdapter

from events import main
from events.core.settings import BackendSettings
from events.models import SocialPost


@pytest.fixture(autouse=True)
def disable_database_connection(monkeypatch):
    monkeypatch.setattr(
        main,
        "BackendSettings",
        lambda: BackendSettings(_env_file=None),
    )


def _http_url(value: str) -> HttpUrl:
    return TypeAdapter(HttpUrl).validate_python(value)


def _write_config(tmp_path):
    user_settings_path = tmp_path / "user_settings.json"
    user_settings_path.write_text(
        json.dumps(
            {
                "user_id": "test-user",
                "sources": [
                    {
                        "name": "Example Venue",
                        "platform": "instagram",
                        "url": "https://www.instagram.com/example_venue/",
                        "lookback_days": 30,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "application": {
                    "timezone": "Europe/Rome",
                    "output_directory": str(tmp_path / "output"),
                },
                "collection": {
                    "max_posts_per_source": 50,
                    "max_images_per_post": 5,
                    "max_total_charge_usd": 1.0,
                    "actors": {
                        "instagram": {
                            "id": "",
                            "input_template": {},
                        }
                    },
                },
                "ai": {
                    "provider": "gemini",
                    "model": "test-model",
                    "max_concurrent_requests": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    return user_settings_path, settings_path


@pytest.mark.asyncio
async def test_cli_writes_reports_without_calling_apis_when_credentials_are_missing(
    tmp_path, monkeypatch
):
    user_settings_path, settings_path = _write_config(tmp_path)
    monkeypatch.setattr(
        main,
        "Secrets",
        lambda: SimpleNamespace(apify_api_token=None, gemini_api_key=None),
    )

    exit_code = await main._run(
        "all",
        user_settings_path=user_settings_path,
        settings_path=settings_path,
    )

    output = tmp_path / "output" / "test-user"
    assert exit_code == 1
    assert (output / "events.ics").is_file()
    assert (output / "events.json").is_file()
    report = (output / "report.txt").read_text(encoding="utf-8")
    assert "APIFY_API_TOKEN is not set" in report


@pytest.mark.asyncio
async def test_cli_batches_gemini_requests_for_posts_from_a_source(
    tmp_path, monkeypatch
):
    user_settings_path, settings_path = _write_config(tmp_path)
    posts = [
        SocialPost(
            source_name="Example Venue",
            platform="instagram",
            external_id=f"post-{index}",
            post_url=_http_url(f"https://www.instagram.com/p/post-{index}/"),
            caption=f"Caption {index}",
            published_at=datetime.now(timezone.utc),
        )
        for index in range(9)
    ]
    extraction_calls = []

    class FakeExtractor:
        def __init__(self, api_key, model, *, max_retries):
            self.options = (api_key, model, max_retries)

        async def extract_events_for_source(
            self, source_posts, *, timezone, max_images
        ):
            extraction_calls.append((source_posts, timezone, max_images))
            return [[] for _ in source_posts]

        async def close(self):
            pass

    async def collect_source(*args, **kwargs):
        assert len(args) == 2
        assert "actor_settings" in kwargs
        return posts, [], False

    monkeypatch.setattr(
        main,
        "Secrets",
        lambda: SimpleNamespace(apify_api_token="test-token", gemini_api_key="test-key"),
    )
    monkeypatch.setattr(main, "ApifyClientAsync", lambda **_: object())
    monkeypatch.setattr(main, "_collect_source", collect_source)
    monkeypatch.setattr(main, "GeminiExtractor", FakeExtractor)

    exit_code = await main._run(
        "all",
        user_settings_path=user_settings_path,
        settings_path=settings_path,
    )

    assert exit_code == 0
    assert [len(call[0]) for call in extraction_calls] == [8, 1]
    assert [post for call, _, _ in extraction_calls for post in call] == posts
    assert all(
        (timezone, max_images) == ("Europe/Rome", 5)
        for _, timezone, max_images in extraction_calls
    )


@pytest.mark.asyncio
async def test_stages_resume_from_saved_posts_and_reuse_analysis_cache(
    tmp_path, monkeypatch
):
    user_settings_path, settings_path = _write_config(tmp_path)
    post = SocialPost(
        source_name="Example Venue",
        platform="instagram",
        external_id="cached-post",
        post_url=_http_url("https://www.instagram.com/p/cached-post/"),
        caption="An event announcement",
        published_at=datetime.now(timezone.utc),
    )
    extraction_calls = []

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            pass

        async def extract_events_for_source(self, posts, **kwargs):
            extraction_calls.extend(posts)
            return [[] for _ in posts]

        async def close(self):
            pass

    async def collect_source(*args, **kwargs):
        return [post], [], False

    monkeypatch.setattr(
        main,
        "Secrets",
        lambda: SimpleNamespace(
            apify_api_token="test-token", gemini_api_key="test-key"
        ),
    )
    monkeypatch.setattr(main, "ApifyClientAsync", lambda **_: object())
    monkeypatch.setattr(main, "_collect_source", collect_source)
    monkeypatch.setattr(main, "GeminiExtractor", FakeExtractor)

    paths = {
        "user_settings_path": user_settings_path,
        "settings_path": settings_path,
    }
    assert await main._run("collect", **paths) == 0
    assert (tmp_path / "output" / "test-user" / "social_posts.json").is_file()
    assert await main._run("analyze", **paths) == 0
    assert await main._run("analyze", **paths) == 0
    assert extraction_calls == [post]

    original_post = post
    post = post.model_copy(update={"caption": "Updated announcement"})
    assert await main._run("collect", **paths) == 0
    assert await main._run("analyze", **paths) == 0
    assert extraction_calls == [original_post, post]
    analysis = json.loads(
        (tmp_path / "output" / "test-user" / "analysis.json").read_text(
            encoding="utf-8"
        )
    )
    assert analysis["report"]["posts_cached"] == 0
    assert await main._run("publish", **paths) == 0

import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from events.config import load_settings
from events.models import SocialPost
from events.pipeline import _analyze_posts, _report_template
from events.persistence.posts_repository import PostCacheResult


ROOT = Path(__file__).parents[1]


@pytest.mark.asyncio
async def test_analysis_batches_posts_per_source_without_provider_calls(
    monkeypatch,
):
    settings = load_settings(ROOT / "config" / "settings.json")
    posts = [
        SocialPost(
            source_name="Example Venue",
            platform="instagram",
            external_id=f"post-{index}",
            post_url=f"https://www.instagram.com/p/post-{index}/",
            caption=f"Caption {index}",
            published_at=datetime.now(timezone.utc),
        )
        for index in range(9)
    ]
    extraction_calls = []

    class FakePostsRepository:
        async def upsert_post(self, user_id, post, *, content_hash):
            return None

        async def upsert_post_with_status(
            self,
            user_id,
            post,
            *,
            content_hash,
        ):
            return PostCacheResult(disposition="new", events=None)

        async def mark_analyzed(
            self,
            user_id,
            post,
            *,
            content_hash,
            events,
        ):
            pass

        async def mark_analysis_failed(
            self,
            user_id,
            post,
            *,
            content_hash,
            error,
        ):
            raise AssertionError(error)

        async def mark_skipped(
            self,
            user_id,
            post,
            *,
            content_hash,
            reason,
        ):
            pass

    class FakeExtractor:
        def __init__(self, api_key, model, *, max_retries):
            self.options = (api_key, model, max_retries)

        async def extract_events_for_source(
            self,
            source_posts,
            *,
            timezone,
            max_images,
        ):
            extraction_calls.append((source_posts, timezone, max_images))
            return [[] for _ in source_posts]

        async def close(self):
            pass

    monkeypatch.setattr("events.pipeline.GeminiExtractor", FakeExtractor)
    result, report = await _analyze_posts(
        posts,
        _report_template(1),
        settings,
        SimpleNamespace(gemini_api_key="test-key"),
        posts_repository=FakePostsRepository(),
        user_id="test-user",
    )

    assert result == []
    assert report["posts_analyzed"] == 9
    assert [len(call[0]) for call in extraction_calls] == [8, 1]
    assert [post for call, _, _ in extraction_calls for post in call] == posts
    assert all(
        (timezone, max_images)
        == (settings.application.timezone, settings.collection.max_images_per_post)
        for _, timezone, max_images in extraction_calls
    )


@pytest.mark.asyncio
async def test_analysis_logs_cache_origin_for_provider_failures(
    monkeypatch,
    caplog,
):
    settings = load_settings(ROOT / "config" / "settings.json")
    post = SocialPost(
        source_name="Example Venue",
        platform="instagram",
        external_id="post-1",
        post_url="https://www.instagram.com/p/post-1/",
        caption="Caption",
    )

    class FakePostsRepository:
        async def upsert_post_with_status(
            self,
            user_id,
            post,
            *,
            content_hash,
        ):
            return PostCacheResult(disposition="pending", events=None)

        async def mark_analysis_failed(
            self,
            user_id,
            post,
            *,
            content_hash,
            error,
        ):
            pass

    class FakeExtractor:
        def __init__(self, api_key, model, *, max_retries):
            pass

        async def extract_events_for_source(
            self,
            source_posts,
            *,
            timezone,
            max_images,
        ):
            return [ValueError("400 INVALID_ARGUMENT")] * len(source_posts)

        async def close(self):
            pass

    monkeypatch.setattr("events.pipeline.GeminiExtractor", FakeExtractor)
    report = _report_template(1)
    report["post_cache_dispositions"] = {"instagram:post-1": "new"}

    with caplog.at_level(logging.INFO, logger="events"):
        _, report = await _analyze_posts(
            [post],
            report,
            settings,
            SimpleNamespace(gemini_api_key="test-key"),
            posts_repository=FakePostsRepository(),
            user_id="test-user",
            run_id="run-1",
        )

    assert "Run run-1 analysis cache history: new=1" in caplog.text
    assert "cache_disposition=new" in caplog.text
    assert "400 INVALID_ARGUMENT" in report["post_errors"][0]

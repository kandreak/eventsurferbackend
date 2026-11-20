from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from events.config import load_settings
from events.models import SocialPost
from events.pipeline import _analyze_posts, _report_template


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

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from events.collectors import InstagramCollector
from events.collectors.apify import _normalize_post, _render_template
from events.config import ActorSettings, load_config
from events.models import SourceConfig
from pathlib import Path


@pytest.mark.asyncio
async def test_apify_collector_renders_input_and_normalizes_actor_results():
    source = SourceConfig(
        name="Example Venue",
        platform="instagram",
        url="https://www.instagram.com/example_venue/",
        lookback_days=30,
    )
    actor = ActorSettings(
        id="owner~instagram-collector",
        input_template={
            "directUrls": ["{url}"],
            "resultsLimit": "{max_posts}",
        },
        field_map={
            "post_url": "permalink",
            "published_at": "created.timestamp",
            "media_urls": "media.images",
        },
    )
    run_call = AsyncMock(return_value={"defaultDatasetId": "dataset-1"})
    list_items = AsyncMock(
        return_value=SimpleNamespace(
            items=[
                {
                    "id": 123,
                    "permalink": "https://www.instagram.com/p/abc/",
                    "caption": "An announcement",
                    "created": {"timestamp": "2026-11-18T10:00:00Z"},
                    "media": {"images": ["https://cdn.example/image.jpg"]},
                }
            ]
        )
    )
    client = Mock()
    client.actor.return_value = SimpleNamespace(call=run_call)
    client.dataset.return_value = SimpleNamespace(list_items=list_items)

    result = await InstagramCollector().fetch_posts(
        client,
        source,
        actor,
        max_posts=25,
        budget_usd=0.5,
    )

    run_call.assert_awaited_once_with(
        run_input={
            "directUrls": ["https://www.instagram.com/example_venue/"],
            "resultsLimit": 25,
        },
        max_total_charge_usd=0.5,
    )
    list_items.assert_awaited_once_with(limit=25)
    assert len(result.posts) == 1
    assert result.posts[0].external_id == "123"
    assert result.posts[0].published_at == datetime(
        2026, 11, 18, 10, 0, tzinfo=timezone.utc
    )
    assert [str(url) for url in result.posts[0].media_urls] == [
        "https://cdn.example/image.jpg"
    ]
    assert not result.truncated


def test_configured_actor_templates_render_their_published_input_shapes():
    root = Path(__file__).parents[1]
    sources, settings = load_config(
        root / "config" / "user_settings.json",
        root / "config" / "settings.json",
    )
    source_by_platform = {
        source.platform: source for source in sources.sources
    }

    instagram = _render_template(
        settings.collection.actors["instagram"].input_template,
        url=str(source_by_platform["instagram"].url),
        max_posts=30,
        lookback_days=15,
        since_date="2026-10-19",
    )
    facebook = _render_template(
        settings.collection.actors["facebook"].input_template,
        url=str(source_by_platform["facebook"].url),
        max_posts=30,
        lookback_days=15,
        since_date="2026-11-04",
    )

    assert instagram == {
        "directUrls": [str(source_by_platform["instagram"].url)],
        "resultsType": "posts",
        "resultsLimit": 30,
        "onlyPostsNewerThan": "15 days",
        "skipPinnedPosts": False,
    }
    assert facebook == {
        "pageUrls": [{"url": str(source_by_platform["facebook"].url)}],
        "maxPosts": 30,
        "postsSince": "2026-11-04",
    }


def test_facebook_actor_fields_normalize_to_social_post():
    source = SourceConfig(
        name="Example Page",
        platform="facebook",
        url="https://www.facebook.com/example/",
        lookback_days=60,
    )
    post = _normalize_post(
        {
            "postId": "12345",
            "url": "https://www.facebook.com/example/posts/12345",
            "text": "An event announcement",
            "createdAt": "2026-11-18T10:00:00Z",
            "imageUrls": ["https://cdn.example/flyer.jpg"],
            "pageName": "Example Page",
        },
        source,
        {
            "external_id": "postId",
            "post_url": "url",
            "caption": "text",
            "published_at": "createdAt",
            "media_urls": "imageUrls",
            "author": "pageName",
        },
    )

    assert post.external_id == "12345"
    assert post.caption == "An event announcement"
    assert post.published_at == datetime(
        2026, 11, 18, 10, 0, tzinfo=timezone.utc
    )
    assert [str(url) for url in post.media_urls] == [
        "https://cdn.example/flyer.jpg"
    ]


def test_empty_mapped_instagram_fields_fall_back_to_caption_and_thumbnail():
    source = SourceConfig(
        name="Example Venue",
        platform="instagram",
        url="https://www.instagram.com/example_venue/",
        lookback_days=15,
    )
    post = _normalize_post(
        {
            "id": "123",
            "url": "https://www.instagram.com/p/example-post/",
            "caption": " ",
            "captionText": "An announcement",
            "images": [],
            "displayUrl": "https://cdn.example/post-thumbnail.jpg",
        },
        source,
        {"caption": "caption", "media_urls": "images"},
    )

    assert post.caption == "An announcement"
    assert [str(url) for url in post.media_urls] == [
        "https://cdn.example/post-thumbnail.jpg"
    ]


def test_empty_mapped_facebook_images_fall_back_to_attachment_image():
    source = SourceConfig(
        name="Example Page",
        platform="facebook",
        url="https://www.facebook.com/example/",
        lookback_days=15,
    )
    post = _normalize_post(
        {
            "postId": "12345",
            "url": "https://www.facebook.com/example/reel/12345",
            "text": "An announcement",
            "imageUrls": [],
            "attachments": [
                {"type": "video", "imageUrl": "https://cdn.example/reel.jpg"}
            ],
        },
        source,
        {"media_urls": "imageUrls"},
    )

    assert [str(url) for url in post.media_urls] == [
        "https://cdn.example/reel.jpg"
    ]


def test_post_source_name_falls_back_to_full_source_url():
    source = SourceConfig(
        platform="instagram",
        url="https://www.instagram.com/example_venue/",
        lookback_days=30,
    )
    post = _normalize_post(
        {"url": "https://www.instagram.com/p/example-post/"},
        source,
        {},
    )

    assert post.source_name == str(source.url)

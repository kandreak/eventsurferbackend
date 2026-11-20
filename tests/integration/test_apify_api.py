import os
from pathlib import Path

import pytest
from apify_client import ApifyClientAsync

from events.collectors import InstagramCollector
from events.config import load_settings
from events.models import SourceConfig
from events.secrets import Secrets

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_APIFY_INTEGRATION_TESTS") != "1",
    reason="Set RUN_APIFY_INTEGRATION_TESTS=1 to call the Apify API.",
)

_SCRAPE_OPT_IN = os.getenv("RUN_APIFY_SCRAPE_INTEGRATION_TESTS") == "1"
_SCRAPE_BUDGET_USD = 0.05


def _settings():
    path = Path(__file__).resolve().parents[2] / "config" / "settings.json"
    return load_settings(path)


def _api_token() -> str:
    token = Secrets().apify_api_token
    if not token:
        pytest.fail(
            "APIFY_API_TOKEN must be configured to run this integration test."
        )
    return token


@pytest.mark.asyncio
async def test_apify_can_read_configured_actor_metadata_without_running_it():
    settings = _settings()
    actor_id = settings.collection.actors["instagram"].id
    if not actor_id:
        pytest.fail("The Instagram Apify actor is not configured.")

    client = ApifyClientAsync(token=_api_token())
    async with client.http_client.impit_async_client:
        actor = await client.actor(actor_id).get()

    assert actor is not None
    assert actor.get("id") or actor.get("_id")
    assert actor.get("name")


@pytest.mark.skipif(
    not _SCRAPE_OPT_IN,
    reason=(
        "Set RUN_APIFY_SCRAPE_INTEGRATION_TESTS=1 to run one capped scrape."
    ),
)
@pytest.mark.asyncio
async def test_apify_scraper_collects_at_most_one_post_with_a_charge_cap():
    source_url = os.getenv("APIFY_INTEGRATION_SOURCE_URL", "").strip()
    if not source_url:
        pytest.fail(
            "Set APIFY_INTEGRATION_SOURCE_URL to a public Instagram profile "
            "before opting into the scraper test."
        )

    settings = _settings()
    source = SourceConfig(
        name="Apify integration test source",
        platform="instagram",
        url=source_url,
        lookback_days=30,
    )
    actor_settings = settings.collection.actors["instagram"]
    client = ApifyClientAsync(token=_api_token())
    async with client.http_client.impit_async_client:
        result = await InstagramCollector().fetch_posts(
            client,
            source,
            actor_settings,
            max_posts=1,
            budget_usd=_SCRAPE_BUDGET_USD,
        )

    assert len(result.posts) <= 1
    assert all(post.platform == "instagram" for post in result.posts)
    assert all(post.source_name == source.display_name for post in result.posts)

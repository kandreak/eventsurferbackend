import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from google import genai

from events.ai import GeminiExtractor
from events.config import load_settings
from events.models import SocialPost
from events.secrets import Secrets

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_GEMINI_INTEGRATION_TESTS") != "1",
    reason="Set RUN_GEMINI_INTEGRATION_TESTS=1 to call the Gemini API.",
)
_GENERATION_OPT_IN = (
    os.getenv("RUN_GEMINI_GENERATION_INTEGRATION_TESTS") == "1"
)


def _settings():
    settings_path = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "settings.json"
    )
    return load_settings(settings_path)


def _api_key() -> str:
    api_key = Secrets().gemini_api_key
    if not api_key:
        pytest.fail(
            "GEMINI_API_KEY must be configured to run this integration test."
        )
    return api_key


@pytest.mark.asyncio
async def test_gemini_api_key_can_access_the_configured_model():
    settings = _settings()
    client = genai.Client(api_key=_api_key())
    try:
        model = await client.aio.models.get(model=settings.ai.model)
    finally:
        await client.aio.aclose()

    assert model.name
    assert model.name.endswith(settings.ai.model)


@pytest.mark.skipif(
    not _GENERATION_OPT_IN,
    reason=(
        "Set RUN_GEMINI_GENERATION_INTEGRATION_TESTS=1 to make one "
        "billable Gemini generation request."
    ),
)
@pytest.mark.asyncio
async def test_gemini_extracts_a_synthetic_event_from_one_text_post():
    settings = _settings()
    event_date = date.today() + timedelta(days=30)
    post = SocialPost(
        source_name="Integration Test Venue",
        platform="instagram",
        external_id="gemini-integration-smoke-test",
        post_url="https://example.com/integration-test-post",
        caption=(
            "Event announcement: Example Band will perform a concert on "
            f"{event_date.isoformat()} at 19:00 at Sample Hall."
        ),
    )
    extractor = GeminiExtractor(
        _api_key(),
        settings.ai.model,
        max_retries=0,
    )
    try:
        results = await extractor.extract_events_for_source(
            [post],
            timezone=settings.application.timezone,
            max_images=0,
        )
    finally:
        await extractor.close()

    assert len(results) == 1
    result = results[0]
    if isinstance(result, Exception):
        pytest.fail(
            f"Gemini request or response validation failed: "
            f"{type(result).__name__}"
        )
    assert result
    assert any("example band" in event.title.casefold() for event in result)
    assert result[0].date == event_date
    assert result[0].source_name == post.source_name
    assert result[0].source_post_url == post.post_url

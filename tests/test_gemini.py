from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import HttpUrl, TypeAdapter

from events.ai import gemini
from events.models import (
    GroupedExtractedEvent,
    GroupedExtractionResult,
    SocialPost,
)


def _http_url(value: str) -> HttpUrl:
    return TypeAdapter(HttpUrl).validate_python(value)


def _client(create_interaction):
    return SimpleNamespace(
        aio=SimpleNamespace(
            interactions=SimpleNamespace(create=create_interaction),
            aclose=AsyncMock(),
        )
    )


def _post(external_id: str, caption: str, *, media_urls=()) -> SocialPost:
    return SocialPost(
        source_name="Example Venue",
        platform="instagram",
        external_id=external_id,
        post_url=_http_url(f"https://www.instagram.com/p/{external_id}/"),
        caption=caption,
        media_urls=[_http_url(url) for url in media_urls],
    )


def _extracted_event(post_key: str, title: str) -> GroupedExtractedEvent:
    return GroupedExtractedEvent(
        post_key=post_key,
        title=title,
        date=date(2026, 12, 12),
        confidence=0.95,
        needs_review=False,
        date_is_explicit=True,
        time_is_explicit=False,
    )


def test_grouped_response_schema_omits_unsupported_additional_properties():
    schema = GroupedExtractionResult.model_json_schema()

    assert "additionalProperties" not in schema
    event_items = schema["properties"]["events"]["items"]
    assert "additionalProperties" not in event_items


@pytest.mark.asyncio
async def test_grouped_extraction_preserves_post_keys_media_and_provenance(
    monkeypatch,
):
    response_text = GroupedExtractionResult(
        events=[
            _extracted_event("post-2", "Second event"),
            _extracted_event("post-1", "First event"),
        ]
    ).model_dump_json()
    create_interaction = AsyncMock(
        return_value=SimpleNamespace(output_text=response_text)
    )
    client = _client(create_interaction)
    monkeypatch.setattr(gemini.genai, "Client", lambda **_: client)
    posts = [
        _post(
            "first",
            "First caption",
            media_urls=[
                "https://cdn.example/flyer.jpg",
                "https://cdn.example/flyer.jpg",
            ],
        ),
        _post("second", "Second caption"),
    ]
    extractor = gemini.GeminiExtractor("test-key", "test-model", max_retries=0)

    results = await extractor.extract_events_for_source(
        posts,
        timezone="Europe/Rome",
        max_images=1,
    )

    request_call = create_interaction.await_args
    if request_call is None:
        pytest.fail("Gemini request was not made")
    request = request_call.kwargs
    assert request["model"] == "test-model"
    assert request["store"] is False
    assert request["stream"] is False
    assert request["response_format"]["mime_type"] == "application/json"
    input_items = request["input"]
    assert len(input_items) == 4
    assert "Post key: post-1" in input_items[1]["text"]
    assert "First caption" in input_items[1]["text"]
    assert input_items[2] == {
        "type": "image",
        "uri": "https://cdn.example/flyer.jpg",
        "mime_type": "image/jpeg",
    }
    assert "Post key: post-2" in input_items[3]["text"]
    assert len(results) == 2
    first_result, second_result = results
    if isinstance(first_result, Exception) or isinstance(
        second_result, Exception
    ):
        pytest.fail("Expected successful event extraction for both posts")
    assert [event.title for event in first_result] == ["First event"]
    assert [event.title for event in second_result] == ["Second event"]
    assert first_result[0].source_name == "Example Venue"
    assert str(first_result[0].source_post_url) == str(posts[0].post_url)
    client.aio.aclose.assert_not_awaited()

    await extractor.close()
    client.aio.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_grouped_extraction_retries_transient_api_errors(monkeypatch):
    create_interaction = AsyncMock(
        side_effect=[
            APIError(503, {"error": {"status": "UNAVAILABLE"}}),
            SimpleNamespace(
                output_text=GroupedExtractionResult().model_dump_json()
            ),
        ]
    )
    client = _client(create_interaction)
    monkeypatch.setattr(gemini.genai, "Client", lambda **_: client)
    monkeypatch.setattr(gemini.asyncio, "sleep", AsyncMock())
    extractor = gemini.GeminiExtractor("test-key", "test-model", max_retries=1)

    results = await extractor.extract_events_for_source(
        [_post("post-1", "No event announced")],
        timezone="Europe/Rome",
        max_images=0,
    )

    assert results == [[]]
    assert create_interaction.await_count == 2
    assert gemini.asyncio.sleep.await_count == 1

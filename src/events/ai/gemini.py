from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import cast
from urllib.parse import urlsplit

from google import genai
from google.genai.errors import APIError
from google.genai.interactions import Interaction
from pydantic import ValidationError

from events.models import Event, GroupedExtractionResult, SocialPost

_SYSTEM_PROMPT = """\
You identify real-world events announced by social media posts. Events can be
of any kind: concerts, festivals, exhibitions, conferences, markets, parties,
workshops, and more. Return only events explicitly announced by a post.
Never invent or guess missing details. Use null for unknown date, time, venue,
address, or city. Resolve relative dates only when the post publication date
makes the reference unambiguous; set date_is_explicit=false for such dates.
Set time_is_explicit=false unless the time is explicitly stated. Set
needs_review=true if important event details are missing or ambiguous.
Each post has a unique post_key. Return that exact post_key for each event and
never combine details from different posts. Analyze only the caption and
supplied image URLs. An empty events list is valid.
"""

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
logger = logging.getLogger("events.ai.gemini")


def _image_mime_type(url: str) -> str | None:
    guessed, _ = mimetypes.guess_type(urlsplit(url).path)
    if guessed and guessed.startswith("image/"):
        return guessed
    return None


def _unique_media_urls(urls: list[str], maximum: int) -> list[str]:
    return list(dict.fromkeys(urls))[:maximum]


def _post_text(post: SocialPost, *, post_key: str, timezone: str) -> str:
    return (
        f"Post key: {post_key}\n"
        f"Post ID: {post.external_id}\n"
        f"Configured timezone: {timezone}\n"
        f"Post published at: {post.published_at or 'unknown'}\n"
        f"Source page: {post.source_name}\n"
        f"Author: {post.author or 'unknown'}\n"
        f"Post location: {post.location or 'unknown'}\n"
        f"Original post URL (context only): {post.post_url}\n"
        f"Caption:\n{post.caption or ''}\n"
        "For every event, include title, description, date, start_time, "
        "end_time, venue, address, city, participants, confidence, "
        "needs_review, date_is_explicit, and time_is_explicit."
    )


def _source_input(
    posts: list[SocialPost],
    *,
    timezone: str,
    max_images: int,
) -> list[dict[str, str]]:
    contents: list[dict[str, str]] = [
        {
            "type": "text",
            "text": (
                f"Analyze these {len(posts)} independent posts. "
                "Only associate an event with the post whose key is attached "
                "to its caption and images."
            ),
        }
    ]
    for index, post in enumerate(posts, start=1):
        contents.append(
            {
                "type": "text",
                "text": _post_text(
                    post,
                    post_key=f"post-{index}",
                    timezone=timezone,
                ),
            }
        )
        media_urls = _unique_media_urls(
            [str(url) for url in post.media_urls], max_images
        )
        for url in media_urls:
            image_input = {"type": "image", "uri": url}
            mime_type = _image_mime_type(url)
            if mime_type:
                image_input["mime_type"] = mime_type
            contents.append(image_input)
    return contents


def _events_by_post(
    result: GroupedExtractionResult,
    posts: list[SocialPost],
) -> list[list[Event]]:
    posts_by_key = {
        f"post-{index}": post for index, post in enumerate(posts, start=1)
    }
    events_by_key = {f"post-{index}": [] for index in range(1, len(posts) + 1)}
    for extracted_event in result.events:
        post = posts_by_key.get(extracted_event.post_key)
        if post is None:
            raise ValueError(
                f"Gemini returned an unknown post key: "
                f"{extracted_event.post_key}"
            )
        events_by_key[extracted_event.post_key].append(
            Event(
                **extracted_event.model_dump(exclude={"post_key"}),
                source_post_url=post.post_url,
                source_name=post.source_name,
            )
        )
    return [
        events_by_key[f"post-{index}"]
        for index in range(1, len(posts) + 1)
    ]


class GeminiExtractor:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_retries: int,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_retries = max_retries

    async def close(self) -> None:
        await self._client.aio.aclose()

    async def extract_events_for_source(
        self,
        posts: list[SocialPost],
        *,
        timezone: str,
        max_images: int,
    ) -> list[list[Event] | Exception]:
        if not posts:
            return []

        contents = _source_input(
            posts,
            timezone=timezone,
            max_images=max_images,
        )
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.aio.interactions.create(
                    model=self._model,
                    input=contents,
                    system_instruction=_SYSTEM_PROMPT,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": GroupedExtractionResult.model_json_schema(),
                    },
                    store=False,
                    stream=False,
                )
                response_text = cast(Interaction, response).output_text
                if response_text is None:
                    raise ValueError(
                        "Gemini returned no structured event response."
                    )
                parsed = GroupedExtractionResult.model_validate_json(
                    response_text
                )
                mapped_events = _events_by_post(parsed, posts)
                results: list[list[Event] | Exception] = []
                results.extend(mapped_events)
                return results
            except APIError as exc:
                status_code = exc.code
                retry = (
                    status_code in _RETRYABLE_STATUS_CODES
                    and attempt < self._max_retries
                )
                logger.log(
                    logging.WARNING if retry else logging.ERROR,
                    "Gemini request failed: model=%s source=%s/%s "
                    "post_ids=%s batch_size=%d attempt=%d/%d "
                    "http_status=%s provider_status=%s details=%s",
                    self._model,
                    posts[0].source_name,
                    posts[0].platform,
                    [post.external_id for post in posts],
                    len(posts),
                    attempt + 1,
                    self._max_retries + 1,
                    status_code,
                    exc.status,
                    str(exc),
                )
                if not retry:
                    return [exc] * len(posts)
                await asyncio.sleep(min(2**attempt, 8))
            except (ValidationError, ValueError) as exc:
                return [exc] * len(posts)

        return [
            RuntimeError("Gemini did not return a result for this source.")
            for _ in posts
        ]

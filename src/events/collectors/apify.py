from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from apify_client import ApifyClientAsync
from pydantic import ValidationError

from events.collectors.base import BaseCollector, CollectionResult
from events.config import ActorSettings
from events.models import SocialPost, SourceConfig

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": ("id", "postId", "shortCode", "legacyId"),
    "post_url": ("url", "postUrl", "permalink", "link"),
    "caption": ("caption", "captionText", "text", "description", "message"),
    "published_at": ("timestamp", "takenAt", "publishedAt", "date", "createdTime"),
    "media_urls": (
        "images",
        "imageUrl",
        "displayUrl",
        "mediaUrls",
        "photos",
        "attachments",
    ),
    "location": ("locationName", "location", "place"),
    "author": ("ownerUsername", "username", "author", "pageName"),
}
_VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".webm"}


def _read_path(item: dict[str, Any], path: str) -> Any:
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _field(
    item: dict[str, Any], name: str, field_map: dict[str, str]
) -> Any:
    def is_missing(value: Any) -> bool:
        if value is None:
            return True
        if name == "caption" and isinstance(value, str):
            return not value.strip()
        return name == "media_urls" and isinstance(value, list) and not value

    configured_path = field_map.get(name)
    if configured_path:
        value = _read_path(item, configured_path)
        if not is_missing(value):
            return value
    for alias in _FIELD_ALIASES[name]:
        value = _read_path(item, alias)
        if not is_missing(value):
            return value
    return None


def _media_urls(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    urls: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            url = (
                item.get("url")
                or item.get("imageUrl")
                or item.get("displayUrl")
                or item.get("thumbnailUrl")
            )
        else:
            url = item
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        suffix = urlsplit(url).path.lower()
        if any(suffix.endswith(extension) for extension in _VIDEO_SUFFIXES):
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _render_template(
    value: Any,
    *,
    url: str,
    max_posts: int,
    lookback_days: int,
    since_date: str,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _render_template(
                child,
                url=url,
                max_posts=max_posts,
                lookback_days=lookback_days,
                since_date=since_date,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _render_template(
                child,
                url=url,
                max_posts=max_posts,
                lookback_days=lookback_days,
                since_date=since_date,
            )
            for child in value
        ]
    if isinstance(value, str):
        replacements: dict[str, str | int] = {
            "{url}": url,
            "{max_posts}": max_posts,
            "{lookback_days}": lookback_days,
            "{since_date}": since_date,
        }
        if value in replacements:
            return replacements[value]
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, str(replacement))
    return value


def _normalize_post(
    item: dict[str, Any],
    source: SourceConfig,
    field_map: dict[str, str],
) -> SocialPost:
    post_url = _field(item, "post_url", field_map)
    if not isinstance(post_url, str):
        raise ValueError("Actor result is missing a post URL.")
    external_id = _field(item, "external_id", field_map)
    published_at = _field(item, "published_at", field_map)
    if isinstance(published_at, str):
        published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    if external_id is None:
        external_id = post_url
    caption = _field(item, "caption", field_map)
    location = _field(item, "location", field_map)
    author = _field(item, "author", field_map)
    return SocialPost(
        source_name=source.display_name,
        platform=source.platform,
        external_id=str(external_id),
        post_url=post_url,
        caption=caption if isinstance(caption, str) else None,
        published_at=published_at,
        media_urls=_media_urls(_field(item, "media_urls", field_map)),
        location=location if isinstance(location, str) else None,
        author=author if isinstance(author, str) else None,
    )


class ApifyCollector(BaseCollector):
    async def fetch_posts(
        self,
        client: ApifyClientAsync,
        source: SourceConfig,
        actor: ActorSettings,
        *,
        max_posts: int,
        budget_usd: float,
    ) -> CollectionResult:
        if not actor.id:
            raise ValueError(
                f"No Apify Actor ID configured for {source.platform}; "
                f"set collection.actors.{source.platform}.id in settings.json."
            )
        if not actor.input_template:
            raise ValueError(
                f"No Apify input template configured for {source.platform}."
            )
        run_input = _render_template(
            actor.input_template,
            url=str(source.url),
            max_posts=max_posts,
            lookback_days=source.lookback_days,
            since_date=(
                datetime.now(timezone.utc)
                - timedelta(days=source.lookback_days)
            ).date().isoformat(),
        )
        run = await client.actor(actor.id).call(
            run_input=run_input,
            max_total_charge_usd=budget_usd,
        )
        dataset_id = run.get("defaultDatasetId") if run else None
        if not dataset_id:
            raise RuntimeError(
                f"Apify Actor {actor.id} completed without a default dataset."
            )
        page = await client.dataset(dataset_id).list_items(limit=max_posts)
        items = page.items
        result = CollectionResult(truncated=len(items) >= max_posts)
        if result.truncated:
            result.warnings.append(
                f"Actor reached the configured limit of {max_posts} posts; "
                "additional posts may have been omitted."
            )
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                result.warnings.append(
                    f"Skipped Actor result {index}: expected a JSON object."
                )
                continue
            try:
                result.posts.append(_normalize_post(item, source, actor.field_map))
            except (TypeError, ValueError, ValidationError) as exc:
                result.warnings.append(
                    f"Skipped Actor result {index}: {exc}"
                )
        return result


class InstagramCollector(ApifyCollector):
    platform = "instagram"


class FacebookCollector(ApifyCollector):
    platform = "facebook"

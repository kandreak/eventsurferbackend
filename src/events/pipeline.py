from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify_client import ApifyClientAsync

from events.ai import GeminiExtractor
from events.collectors import FacebookCollector, InstagramCollector
from events.config import ActorSettings, SettingsConfig
from events.models import Event, SocialPost, SourceConfig
from events.persistence.posts_repository import (
    PostsRepository,
    analysis_content_hash,
)
from events.processing import deduplicate_events, execution_now, filter_recent_posts
from events.secrets import Secrets

logger = logging.getLogger("events")
_ANALYSIS_BATCH_SIZE = 8

_COLLECTORS = {
    "instagram": InstagramCollector,
    "facebook": FacebookCollector,
}


async def _collect_source(
    client: ApifyClientAsync,
    source: SourceConfig,
    *,
    actor_settings: ActorSettings,
    max_posts: int,
    budget_usd: float,
) -> tuple[list[SocialPost], list[str], bool]:
    collector_type = _COLLECTORS[source.platform]
    collector = collector_type()
    result = await collector.fetch_posts(
        client,
        source,
        actor_settings,
        max_posts=max_posts,
        budget_usd=budget_usd,
    )
    return result.posts, result.warnings, result.truncated


async def _extract_source_posts(
    extractor: GeminiExtractor,
    semaphore: asyncio.Semaphore,
    posts: list[SocialPost],
    *,
    timezone: str,
    max_images: int,
) -> list[list[Event] | Exception]:
    async with semaphore:
        return await extractor.extract_events_for_source(
            posts,
            timezone=timezone,
            max_images=max_images,
        )


def _report_template(source_count: int) -> dict[str, Any]:
    return {
        "sources_configured": source_count,
        "sources_succeeded": 0,
        "posts_retrieved": 0,
        "posts_in_lookback": 0,
        "posts_analyzed": 0,
        "posts_cached": 0,
        "posts_skipped": 0,
        "events_extracted": 0,
        "duplicates_removed": 0,
        "events_requiring_review": 0,
        "events_without_dates": [],
        "source_errors": [],
        "post_errors": [],
        "warnings": [],
    }


async def _collect_posts(
    sources: list[SourceConfig],
    settings: SettingsConfig,
    secrets: Secrets,
    *,
    posts_repository: PostsRepository,
    user_id: str,
) -> tuple[list[SocialPost], dict[str, Any]]:
    collection = settings.collection
    report = _report_template(len(sources))
    posts: list[SocialPost] = []
    now = execution_now(settings.application.timezone)

    logger.info("Collecting posts from %d configured sources.", len(sources))
    if secrets.apify_api_token:
        apify_client = ApifyClientAsync(token=secrets.apify_api_token)
        per_source_budget = collection.max_total_charge_usd / len(sources)
        for index, source in enumerate(sources, start=1):
            logger.info(
                f"[{index}/{len(sources)}] {source.platform.title()}: "
                f"{source.display_name}"
            )
            try:
                source_posts, source_warnings, truncated = await _collect_source(
                    apify_client,
                    source,
                    actor_settings=collection.actors[source.platform],
                    max_posts=collection.max_posts_per_source,
                    budget_usd=per_source_budget,
                )
            except Exception as exc:
                message = f"{source.display_name} ({source.platform}): {exc}"
                logger.exception("Collection failed for %s", source.display_name)
                report["source_errors"].append(message)
                continue
            report["sources_succeeded"] += 1
            report["posts_retrieved"] += len(source_posts)
            report["warnings"].extend(source_warnings)
            recent, filter_warnings = filter_recent_posts(
                source_posts, source, now=now
            )
            report["warnings"].extend(filter_warnings)
            if truncated:
                logger.warning(
                    "Collection may be truncated for %s", source.display_name
                )
            logger.info(
                "Retrieved %d posts; %d are within the last %d days for %s.",
                len(source_posts),
                len(recent),
                source.lookback_days,
                source.display_name,
            )
            posts.extend(recent)
    else:
        report["source_errors"].append(
            "APIFY_API_TOKEN is not set; no sources could be collected."
        )
    report["posts_in_lookback"] = len(posts)
    for post in posts:
        await posts_repository.upsert_post(
            user_id,
            post,
            content_hash=_post_cache_key(
                post,
                model=settings.ai.model,
                timezone=settings.application.timezone,
                max_images=collection.max_images_per_post,
            ),
        )
    return posts, report


def _post_cache_key(
    post: SocialPost,
    *,
    model: str,
    timezone: str,
    max_images: int,
) -> str:
    return analysis_content_hash(
        post,
        model=model,
        timezone=timezone,
        max_images=max_images,
    )


async def _analyze_posts(
    posts: list[SocialPost],
    report: dict[str, Any],
    settings: SettingsConfig,
    secrets: Secrets,
    *,
    posts_repository: PostsRepository,
    user_id: str,
) -> tuple[list[Event], dict[str, Any]]:
    ai_settings = settings.ai
    collection = settings.collection
    events_by_post: dict[str, list[Event]] = {}
    analyzable: list[SocialPost] = []
    for post in posts:
        key = _post_cache_key(
            post,
            model=ai_settings.model,
            timezone=settings.application.timezone,
            max_images=collection.max_images_per_post,
        )
        if not (post.caption or post.media_urls):
            report["posts_skipped"] += 1
            report["warnings"].append(
                f"Skipped post {post.external_id} from {post.source_name}: "
                "no caption or image."
            )
            await posts_repository.upsert_post(
                user_id,
                post,
                content_hash=key,
            )
            await posts_repository.mark_skipped(
                user_id,
                post,
                content_hash=key,
                reason="Post has no caption or image.",
            )
            continue
        cached_events = await posts_repository.upsert_post(
            user_id,
            post,
            content_hash=key,
        )
        if cached_events is not None:
            events_by_post[key] = cached_events
            report["posts_cached"] += 1
            continue
        analyzable.append(post)

    report["posts_analyzed"] = len(analyzable)
    if analyzable and not secrets.gemini_api_key:
        error = "GEMINI_API_KEY is not set; retrieved posts were not analyzed."
        report["post_errors"].append(error)
        report["posts_skipped"] += len(analyzable)
        for post in analyzable:
            await posts_repository.mark_analysis_failed(
                user_id,
                post,
                content_hash=_post_cache_key(
                    post,
                    model=ai_settings.model,
                    timezone=settings.application.timezone,
                    max_images=collection.max_images_per_post,
                ),
                error=error,
            )
    elif analyzable and secrets.gemini_api_key:
        extractor = GeminiExtractor(
            secrets.gemini_api_key,
            ai_settings.model,
            max_retries=ai_settings.max_retries,
        )
        posts_by_source: dict[tuple[str, str], list[SocialPost]] = {}
        for post in analyzable:
            posts_by_source.setdefault(
                (post.source_name, post.platform), []
            ).append(post)
        post_groups = [
            source_posts[start : start + _ANALYSIS_BATCH_SIZE]
            for source_posts in posts_by_source.values()
            for start in range(0, len(source_posts), _ANALYSIS_BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(ai_settings.max_concurrent_requests)
        try:
            results = await asyncio.gather(
                *(
                    _extract_source_posts(
                        extractor,
                        semaphore,
                        group,
                        timezone=settings.application.timezone,
                        max_images=collection.max_images_per_post,
                    )
                    for group in post_groups
                ),
                return_exceptions=True,
            )
            for group, result_group in zip(post_groups, results, strict=True):
                if isinstance(result_group, BaseException):
                    if isinstance(result_group, asyncio.CancelledError):
                        raise result_group
                    for post in group:
                        message = (
                            f"{post.source_name} / {post.external_id}: "
                            f"{result_group}"
                        )
                        logger.error("Event extraction failed for %s", message)
                        report["post_errors"].append(message)
                        await posts_repository.mark_analysis_failed(
                            user_id,
                            post,
                            content_hash=_post_cache_key(
                                post,
                                model=ai_settings.model,
                                timezone=settings.application.timezone,
                                max_images=collection.max_images_per_post,
                            ),
                            error=str(result_group),
                        )
                    continue
                for post, result in zip(group, result_group, strict=True):
                    if isinstance(result, Exception):
                        message = f"{post.source_name} / {post.external_id}: {result}"
                        logger.error("Event extraction failed for %s", message)
                        report["post_errors"].append(message)
                        await posts_repository.mark_analysis_failed(
                            user_id,
                            post,
                            content_hash=_post_cache_key(
                                post,
                                model=ai_settings.model,
                                timezone=settings.application.timezone,
                                max_images=collection.max_images_per_post,
                            ),
                            error=str(result),
                        )
                        continue
                    key = _post_cache_key(
                        post,
                        model=ai_settings.model,
                        timezone=settings.application.timezone,
                        max_images=collection.max_images_per_post,
                    )
                    events_by_post[key] = result
                    await posts_repository.mark_analyzed(
                        user_id,
                        post,
                        content_hash=key,
                        events=result,
                    )
        finally:
            await extractor.close()

    events = [
        event
        for post in posts
        for event in events_by_post.get(
            _post_cache_key(
                post,
                model=ai_settings.model,
                timezone=settings.application.timezone,
                max_images=collection.max_images_per_post,
            ),
            [],
        )
    ]
    report["events_extracted"] = len(events)
    events, report["duplicates_removed"] = deduplicate_events(events)
    report["events_requiring_review"] = sum(
        event.needs_review for event in events
    )
    report["events_without_dates"] = [
        event.title for event in events if event.date is None
    ]
    return events, report



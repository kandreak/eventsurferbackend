from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal, Sequence

from apify_client import ApifyClientAsync
from pydantic import ValidationError
from pymongo import AsyncMongoClient

from events.ai import GeminiExtractor
from events.calendar_export import serialize_calendar
from events.collectors import FacebookCollector, InstagramCollector
from events.config import ActorSettings, SettingsConfig, load_config
from events.core.settings import BackendSettings
from events.models import Event, SocialPost, SourceConfig
from events.persistence.mongodb import MongoDatabase
from events.persistence.events_repository import EventsRepository
from events.persistence.posts_repository import (
    PostsRepository,
    analysis_content_hash,
)
from events.processing import deduplicate_events, execution_now, filter_recent_posts
from events.reporting import write_outputs
from events.secrets import Secrets

logger = logging.getLogger("events")
Stage = Literal["all", "collect", "analyze", "publish"]
_CACHE_VERSION = 1
_ANALYSIS_BATCH_SIZE = 8

_COLLECTORS = {
    "instagram": InstagramCollector,
    "facebook": FacebookCollector,
}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="events",
        description="Find events in public social posts and export a calendar.",
    )
    parser.add_argument(
        "--user-settings",
        "--sources",
        dest="user_settings",
        type=Path,
        default=Path("config/user_settings.json"),
        help="Path to the user-settings JSON file.",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path("config/settings.json"),
        help="Path to the application-settings JSON file.",
    )
    parser.add_argument(
        "--stage",
        choices=("all", "collect", "analyze", "publish"),
        default="all",
        help="Run the full pipeline or resume from one saved stage.",
    )
    return parser.parse_args(argv)


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


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
    posts_repository: PostsRepository | None = None,
    user_id: str | None = None,
) -> tuple[list[SocialPost], dict[str, Any]]:
    collection = settings.collection
    report = _report_template(len(sources))
    posts: list[SocialPost] = []
    now = execution_now(settings.application.timezone)

    print("EVENTS AGGREGATOR")
    print("=================")
    print(f"Configured sources: {len(sources)}")
    if secrets.apify_api_token:
        apify_client = ApifyClientAsync(token=secrets.apify_api_token)
        per_source_budget = collection.max_total_charge_usd / len(sources)
        for index, source in enumerate(sources, start=1):
            print(
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
                print(f"  Collection failed: {exc}")
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
            print(
                f"  Retrieved {len(source_posts)} posts; "
                f"{len(recent)} within the last {source.lookback_days} days."
            )
            posts.extend(recent)
    else:
        report["source_errors"].append(
            "APIFY_API_TOKEN is not set; no sources could be collected."
        )
    report["posts_in_lookback"] = len(posts)
    if posts_repository is not None:
        if user_id is None:
            raise ValueError("A user_id is required when persisting posts.")
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
    cache_path: Path,
    *,
    posts_repository: PostsRepository | None = None,
    user_id: str | None = None,
) -> tuple[list[Event], dict[str, Any]]:
    ai_settings = settings.ai
    collection = settings.collection
    if posts_repository is not None and user_id is None:
        raise ValueError("A user_id is required when persisting posts.")
    cache = {"version": _CACHE_VERSION, "entries": {}}
    if posts_repository is None:
        cache = (
            _read_json(cache_path)
            if cache_path.exists()
            else {"version": _CACHE_VERSION, "entries": {}}
        )
        if cache.get("version") != _CACHE_VERSION or not isinstance(
            cache.get("entries"), dict
        ):
            raise ValueError(f"Unsupported or invalid analysis cache: {cache_path}")
    entries: dict[str, Any] = cache["entries"]
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
            if posts_repository is not None:
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
        if posts_repository is not None:
            cached_events = await posts_repository.upsert_post(
                user_id,
                post,
                content_hash=key,
            )
            if cached_events is not None:
                events_by_post[key] = cached_events
                report["posts_cached"] += 1
                continue
        elif key in entries:
            events_by_post[key] = [
                Event.model_validate(item) for item in entries[key]
            ]
            report["posts_cached"] += 1
        else:
            analyzable.append(post)

    report["posts_analyzed"] = len(analyzable)
    if analyzable and not secrets.gemini_api_key:
        error = "GEMINI_API_KEY is not set; retrieved posts were not analyzed."
        report["post_errors"].append(error)
        report["posts_skipped"] += len(analyzable)
        if posts_repository is not None:
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
                        if posts_repository is not None:
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
                        if posts_repository is not None:
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
                    if posts_repository is not None:
                        await posts_repository.mark_analyzed(
                            user_id,
                            post,
                            content_hash=key,
                            events=result,
                        )
                    else:
                        entries[key] = [
                            event.model_dump(mode="json") for event in result
                        ]
        finally:
            await extractor.close()

    if posts_repository is None:
        _write_json(cache_path, cache)
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


async def _run(
    stage: Stage = "all",
    *,
    user_settings_path: Path = Path("config/user_settings.json"),
    settings_path: Path = Path("config/settings.json"),
) -> int:
    if stage not in ("all", "collect", "analyze", "publish"):
        raise ValueError(f"Unknown pipeline stage: {stage}")
    try:
        sources_config, settings = load_config(user_settings_path, settings_path)
        secrets = Secrets()
    except (ValueError, ValidationError) as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    sources = sources_config.sources
    output_directory = settings.application.output_directory / sources_config.user_id
    posts_path = output_directory / "social_posts.json"
    analysis_path = output_directory / "analysis.json"
    cache_path = output_directory / "analysis_cache.json"

    backend_settings = BackendSettings()
    mongo: MongoDatabase | None = None
    posts_repository: PostsRepository | None = None
    events_repository: EventsRepository | None = None
    if (
        stage in ("all", "collect", "analyze")
        and backend_settings.mongodb_uri is not None
    ):
        client = AsyncMongoClient(
            backend_settings.mongodb_uri.get_secret_value()
        )
        mongo = MongoDatabase(client, backend_settings.mongodb_database)
        try:
            await mongo.initialize()
        except Exception:
            await mongo.close()
            raise
        posts_repository = PostsRepository(mongo.database["posts"])
        events_repository = EventsRepository(mongo.database["events"])

    try:
        if stage in ("all", "collect"):
            posts, report = await _collect_posts(
                sources,
                settings,
                secrets,
                posts_repository=posts_repository,
                user_id=sources_config.user_id,
            )
            _write_json(
                posts_path,
                {
                    "posts": [post.model_dump(mode="json") for post in posts],
                    "report": report,
                },
            )
        if stage in ("all", "analyze"):
            scrape = _read_json(posts_path)
            posts = [SocialPost.model_validate(post) for post in scrape["posts"]]
            events, report = await _analyze_posts(
                posts,
                scrape["report"],
                settings,
                secrets,
                cache_path,
                posts_repository=posts_repository,
                user_id=sources_config.user_id,
            )
            if events_repository is not None:
                await events_repository.save_events(
                    sources_config.user_id,
                    events,
                )
            _write_json(
                analysis_path,
                {
                    "events": [event.model_dump(mode="json") for event in events],
                    "report": report,
                },
            )
        if stage in ("all", "publish"):
            analysis = _read_json(analysis_path)
            events = [Event.model_validate(event) for event in analysis["events"]]
            report = analysis["report"]
            written = write_outputs(
                output_directory=output_directory,
                calendar_filename=settings.calendar.filename,
                calendar_data=serialize_calendar(
                    events, settings.application.timezone
                ),
                events=events,
                report=report,
                json_enabled=settings.reporting.json_enabled,
                text_report_enabled=settings.reporting.text_report_enabled,
            )
            print(
                f"Results: {report['posts_in_lookback']} posts in lookback, "
                f"{len(events)} events, {report['duplicates_removed']} "
                "duplicates removed."
            )
            for path in written:
                print(f"Generated: {path}")
        if report["source_errors"] or report["post_errors"]:
            return 1
        return 0
    finally:
        if mongo is not None:
            await mongo.close()


def main(
    stage: Stage = "all",
    *,
    user_settings_path: Path = Path("config/user_settings.json"),
    settings_path: Path = Path("config/settings.json"),
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    try:
        return asyncio.run(
            _run(
                stage,
                user_settings_path=user_settings_path,
                settings_path=settings_path,
            )
        )
    except (ValueError, OSError) as exc:
        logger.error("Run failed: %s", exc)
        return 1


if __name__ == "__main__":
    arguments = _parse_args()
    raise SystemExit(
        main(
            arguments.stage,
            user_settings_path=arguments.user_settings,
            settings_path=arguments.settings,
        )
    )

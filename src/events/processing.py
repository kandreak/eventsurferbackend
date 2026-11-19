from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from events.models import Event, SocialPost, SourceConfig


def filter_recent_posts(
    posts: list[SocialPost],
    source: SourceConfig,
    *,
    now: datetime,
) -> tuple[list[SocialPost], list[str]]:
    cutoff = now - timedelta(days=source.lookback_days)
    recent: list[SocialPost] = []
    warnings: list[str] = []
    for post in posts:
        if post.published_at is None:
            warnings.append(
                f"Skipped post {post.external_id} from {source.display_name}: "
                "publication date is missing."
            )
            continue
        if post.published_at < cutoff:
            continue
        recent.append(post)
    return recent, warnings


def _normalized(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _same_event(left: Event, right: Event) -> bool:
    if left.date is None or left.date != right.date:
        return False
    if SequenceMatcher(
        None, _normalized(left.title), _normalized(right.title)
    ).ratio() < 0.88:
        return False
    if left.source_post_url == right.source_post_url:
        return True
    left_venue = _normalized(left.venue)
    right_venue = _normalized(right.venue)
    return bool(left_venue and left_venue == right_venue)


def deduplicate_events(events: list[Event]) -> tuple[list[Event], int]:
    unique: list[Event] = []
    removed = 0
    for event in events:
        if any(_same_event(event, existing) for existing in unique):
            removed += 1
            continue
        unique.append(event)
    return unique, removed


def execution_now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))

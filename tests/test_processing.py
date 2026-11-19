from datetime import date, datetime, timedelta, timezone

from events.models import Event, SocialPost, SourceConfig
from events.processing import deduplicate_events, filter_recent_posts


def _source() -> SourceConfig:
    return SourceConfig(
        name="Example Venue",
        platform="instagram",
        url="https://www.instagram.com/example_venue/",
        lookback_days=30,
    )


def _post(external_id: str, published_at: datetime | None) -> SocialPost:
    return SocialPost(
        source_name="Example Venue",
        platform="instagram",
        external_id=external_id,
        post_url=f"https://www.instagram.com/p/{external_id}/",
        published_at=published_at,
    )


def _event(title: str, venue: str, post_id: str) -> Event:
    return Event(
        title=title,
        date=date(2026, 12, 12),
        confidence=0.9,
        needs_review=False,
        date_is_explicit=True,
        time_is_explicit=False,
        venue=venue,
        source_post_url=f"https://www.instagram.com/p/{post_id}/",
        source_name="Example Venue",
    )


def test_lookback_excludes_old_posts_and_reports_undated_posts():
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    recent, warnings = filter_recent_posts(
        [
            _post("recent", now - timedelta(days=10)),
            _post("old", now - timedelta(days=31)),
            _post("unknown", None),
        ],
        _source(),
        now=now,
    )

    assert [post.external_id for post in recent] == ["recent"]
    assert len(warnings) == 1
    assert "publication date is missing" in warnings[0]


def test_deduplicates_same_day_similar_events_at_same_venue():
    unique, removed = deduplicate_events(
        [
            _event("Community workshop", "Town Hall", "post-a"),
            _event("Community workshops", "Town Hall", "post-b"),
            _event("Community workshop", "Library", "post-c"),
        ]
    )

    assert len(unique) == 2
    assert removed == 1

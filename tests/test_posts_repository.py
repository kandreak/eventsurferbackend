from datetime import date
from typing import Any

import pytest

from events.models import Event, SocialPost
from events.persistence.posts_repository import (
    PostsRepository,
    analysis_content_hash,
)


class InMemoryCollection:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(query: dict[str, Any]) -> tuple[str, str, str]:
        return query["user_id"], query["platform"], query["external_id"]

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        document = self.documents.get(self._key(query))
        return document.copy() if document is not None else None

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        identity = self._key(query)
        document = self.documents.get(identity)
        if document is not None and any(
            document.get(key) != value
            for key, value in query.items()
            if key not in ("user_id", "platform", "external_id")
        ):
            return type("UpdateResult", (), {"matched_count": 0})()
        if document is None:
            if not upsert:
                return type("UpdateResult", (), {"matched_count": 0})()
            document = {
                "user_id": identity[0],
                "platform": identity[1],
                "external_id": identity[2],
            }
            self.documents[identity] = document
        document.update(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))
        return type("UpdateResult", (), {"matched_count": 1})()


def _post(caption: str = "Event announcement") -> SocialPost:
    return SocialPost(
        source_name="Example Venue",
        platform="instagram",
        external_id="post-1",
        post_url="https://www.instagram.com/p/post-1/",
        caption=caption,
        raw_provider_data={"caption": caption, "provider_id": "raw-1"},
    )


def _content_hash(post: SocialPost) -> str:
    return analysis_content_hash(
        post,
        model="test-model",
        timezone="Europe/Rome",
        max_images=5,
    )


def _event() -> Event:
    return Event(
        title="Local concert",
        date=date(2026, 12, 12),
        confidence=0.9,
        needs_review=False,
        date_is_explicit=True,
        time_is_explicit=False,
        source_post_url="https://www.instagram.com/p/post-1/",
        source_name="Example Venue",
    )


@pytest.mark.asyncio
async def test_repository_reuses_analysis_and_scopes_posts_by_user():
    collection = InMemoryCollection()
    repository = PostsRepository(collection)
    post = _post()
    content_hash = _content_hash(post)

    assert await repository.upsert_post(
        "alice", post, content_hash=content_hash
    ) is None
    await repository.mark_analyzed(
        "alice",
        post,
        content_hash=content_hash,
        events=[_event()],
    )

    reloaded_post = SocialPost.model_validate(post.model_dump(mode="json"))
    cached = await repository.upsert_post(
        "alice", reloaded_post, content_hash=content_hash
    )
    other_user_cache = await repository.upsert_post(
        "bob", post, content_hash=content_hash
    )

    assert cached == [_event()]
    assert other_user_cache is None
    alice = collection.documents[("alice", "instagram", "post-1")]
    bob = collection.documents[("bob", "instagram", "post-1")]
    assert alice["analysis_status"] == "analyzed"
    assert alice["raw_provider_data"] == {
        "caption": "Event announcement",
        "provider_id": "raw-1",
    }
    assert bob["analysis_status"] == "pending"
    assert bob["extracted_events"] == []


@pytest.mark.asyncio
async def test_changed_content_invalidates_analysis_and_events():
    collection = InMemoryCollection()
    repository = PostsRepository(collection)
    original = _post()
    original_hash = _content_hash(original)
    await repository.upsert_post(
        "alice", original, content_hash=original_hash
    )
    await repository.mark_analyzed(
        "alice",
        original,
        content_hash=original_hash,
        events=[_event()],
    )

    changed = _post("Updated event announcement")
    changed_hash = _content_hash(changed)
    assert changed_hash != original_hash
    cached = await repository.upsert_post(
        "alice", changed, content_hash=changed_hash
    )

    saved = collection.documents[("alice", "instagram", "post-1")]
    assert cached is None
    assert saved["analysis_status"] == "pending"
    assert saved["extracted_events"] == []
    assert saved["post"]["caption"] == "Updated event announcement"


@pytest.mark.asyncio
async def test_upsert_reports_new_pending_failed_cached_and_changed_posts():
    repository = PostsRepository(InMemoryCollection())
    post = _post()
    content_hash = _content_hash(post)

    new = await repository.upsert_post_with_status(
        "alice",
        post,
        content_hash=content_hash,
    )
    pending = await repository.upsert_post_with_status(
        "alice",
        post,
        content_hash=content_hash,
    )
    await repository.mark_analysis_failed(
        "alice",
        post,
        content_hash=content_hash,
        error="HTTP 400",
    )
    failed = await repository.upsert_post_with_status(
        "alice",
        post,
        content_hash=content_hash,
    )
    await repository.mark_analyzed(
        "alice",
        post,
        content_hash=content_hash,
        events=[_event()],
    )
    analyzed = await repository.upsert_post_with_status(
        "alice",
        post,
        content_hash=content_hash,
    )
    changed_post = _post("Updated event announcement")
    changed = await repository.upsert_post_with_status(
        "alice",
        changed_post,
        content_hash=_content_hash(changed_post),
    )

    assert new.disposition == "new"
    assert pending.disposition == "pending"
    assert failed.disposition == "failed"
    assert analyzed.disposition == "analyzed"
    assert analyzed.events == [_event()]
    assert changed.disposition == "changed"


def test_hash_includes_analysis_context():
    post = _post()
    assert _content_hash(post) != analysis_content_hash(
        post,
        model="another-model",
        timezone="Europe/Rome",
        max_images=5,
    )

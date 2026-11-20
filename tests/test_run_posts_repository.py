from datetime import date
from typing import Any

import pytest

from events.models import Event, Platform, SocialPost
from events.persistence.run_posts_repository import RunPostsRepository


class InMemoryCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def sort(self, field: str, direction: int) -> InMemoryCursor:
        self.documents.sort(
            key=lambda document: document.get(field),
            reverse=direction < 0,
        )
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.documents if length is None else self.documents[:length]

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class InMemoryCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    @staticmethod
    def _matches(
        document: dict[str, Any],
        query: dict[str, Any],
    ) -> bool:
        return all(document.get(key) == value for key, value in query.items())

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.documents = [
            document
            for document in self.documents
            if not self._matches(document, query)
        ]

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> None:
        document = next(
            (
                item
                for item in self.documents
                if self._matches(item, query)
            ),
            None,
        )
        if document is None:
            if not upsert:
                return
            document = query.copy()
            self.documents.append(document)
        document.update(update.get("$set", {}))

    async def update_many(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> None:
        for document in self.documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, int],
    ) -> InMemoryCursor:
        documents = []
        included_fields = {
            key for key, included in projection.items() if included == 1
        }
        for document in self.documents:
            if not self._matches(document, query):
                continue
            if included_fields:
                documents.append(
                    {
                        key: document[key]
                        for key in included_fields
                        if key in document
                    }
                )
            else:
                documents.append(
                    {
                        key: value
                        for key, value in document.items()
                        if projection.get(key) != 0
                    }
                )
        return InMemoryCursor(documents)


def _post(external_id: str, platform: Platform) -> SocialPost:
    domain = "instagram.com" if platform == "instagram" else "facebook.com"
    return SocialPost(
        source_name="Example Venue",
        platform=platform,
        external_id=external_id,
        post_url=f"https://www.{domain}/{external_id}/",
    )


def _event(post: SocialPost, title: str) -> Event:
    return Event(
        title=title,
        date=date(2026, 12, 12),
        confidence=0.9,
        needs_review=False,
        date_is_explicit=True,
        time_is_explicit=False,
        source_post_url=post.post_url,
        source_name=post.source_name,
    )


@pytest.mark.asyncio
async def test_run_posts_and_events_are_scoped_by_run_and_user():
    repository = RunPostsRepository(InMemoryCollection())
    alice_post = _post("alice-post", "instagram")
    bob_post = _post("bob-post", "facebook")
    alice_event = _event(alice_post, "Alice event")
    bob_event = _event(bob_post, "Bob event")

    await repository.replace_posts("run-1", "alice", [alice_post])
    await repository.replace_posts("run-1", "bob", [bob_post])
    await repository.replace_events("run-1", "alice", [alice_event])
    await repository.replace_events("run-1", "bob", [bob_event])

    assert await repository.list_posts("run-1", "alice") == [alice_post]
    assert await repository.list_posts("run-1", "bob") == [bob_post]
    assert await repository.list_posts("run-1", "carol") == []
    assert await repository.list_events("run-1", "alice") == [alice_event]
    assert await repository.list_events("run-1", "bob") == [bob_event]
    assert await repository.list_events("run-1", "carol") == []

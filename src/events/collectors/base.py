from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from apify_client import ApifyClientAsync

from events.config import ActorSettings
from events.models import SocialPost, SourceConfig


@dataclass
class CollectionResult:
    posts: list[SocialPost] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False


class BaseCollector(ABC):
    platform: str

    @abstractmethod
    async def fetch_posts(
        self,
        client: ApifyClientAsync,
        source: SourceConfig,
        actor: ActorSettings,
        *,
        max_posts: int,
        budget_usd: float,
    ) -> CollectionResult:
        raise NotImplementedError

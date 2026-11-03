"""Shared types for data source connectors."""

from dataclasses import dataclass
from typing import Protocol


class Http(Protocol):
    """Minimal JSON HTTP client: GoogleHttp in production, FakeHttp in tests."""

    def get(self, url: str) -> dict: ...

    def post(self, url: str, body: dict) -> dict: ...


class SourceError(Exception):
    """A data source could not be read."""


class GoogleAuthError(SourceError):
    """Google sign-in is missing, revoked or expired."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str


@dataclass(frozen=True)
class GA4Month:
    period: str
    sessions: int
    prev_sessions: int
    key_events: int
    prev_key_events: int
    channels: list[tuple[str, int]]  # this month, biggest first
    trend: list[tuple[str, int]]  # (YYYY-MM, sessions), 13 months, oldest first


@dataclass(frozen=True)
class SearchRow:
    key: str
    clicks: int
    impressions: int
    ctr: float
    position: float


@dataclass(frozen=True)
class GSCMonth:
    period: str
    clicks: int
    prev_clicks: int
    impressions: int
    prev_impressions: int
    trend: list[tuple[str, int]]  # (YYYY-MM, clicks), 13 months, oldest first
    top_queries: list[SearchRow]
    top_pages: list[SearchRow]

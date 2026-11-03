"""Fake Google HTTP client and mailer for tests. No network."""

import json
from email.message import EmailMessage
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

RUN_REPORT = {
    (): "ga4_totals.json",
    ("sessionDefaultChannelGroup",): "ga4_channels.json",
    ("yearMonth",): "ga4_trend.json",
}
SEARCH = {
    ("date",): "gsc_trend.json",
    ("query",): "gsc_queries.json",
    ("page",): "gsc_pages.json",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeHttp:
    """Answers GA4 and Search Console calls from fixtures.

    fail: raise this error. fail_on: only for URLs containing this text.
    empty: answer every POST with {} (an account with no data).
    """

    def __init__(self, fail: Exception | None = None, fail_on: str | None = None, empty: bool = False):
        self.fail = fail
        self.fail_on = fail_on
        self.empty = empty
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

    def _maybe_fail(self, url: str) -> None:
        if self.fail and (self.fail_on is None or self.fail_on in url):
            raise self.fail

    def get(self, url: str) -> dict:
        self.gets.append(url)
        self._maybe_fail(url)
        if "accountSummaries" in url:
            return load("ga4_account_summaries.json")
        if url.endswith("/sites"):
            return load("gsc_sites.json")
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, body: dict) -> dict:
        self.posts.append((url, body))
        self._maybe_fail(url)
        if self.empty:
            return {}
        dims = tuple(d["name"] if isinstance(d, dict) else d for d in body.get("dimensions", []))
        if ":runReport" in url:
            return load(RUN_REPORT[dims])
        if "searchAnalytics" in url:
            if not dims:
                return load("gsc_totals.json")[body["startDate"]]
            return load(SEARCH[dims])
        raise AssertionError(f"unexpected POST {url}")


class FakeMailer:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.sent: list[EmailMessage] = []

    def __call__(self, message: EmailMessage) -> None:
        if self.fail:
            raise self.fail
        self.sent.append(message)

"""Google Search Console: site list and one month of data."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from urllib.parse import quote

from clientreport.connectors.base import GSCMonth, Http, SearchRow, Source
from clientreport.months import last_n, month_bounds, shift

API = "https://www.googleapis.com/webmasters/v3"


def list_sites(http: Http) -> list[Source]:
    data = http.get(f"{API}/sites")
    sites = [
        entry["siteUrl"]
        for entry in data.get("siteEntry", [])
        if entry.get("permissionLevel") != "siteUnverifiedUser"
    ]
    return [Source(site, site) for site in sorted(sites)]


def _query(http: Http, site_url: str, start: date, end: date,
           dimensions: Sequence[str] = (), row_limit: int | None = None) -> list[dict]:
    body: dict = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    if dimensions:
        body["dimensions"] = list(dimensions)
    if row_limit:
        body["rowLimit"] = row_limit
    url = f"{API}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    return http.post(url, body).get("rows", [])


def _month_totals(http: Http, site_url: str, period: str) -> tuple[int, int]:
    rows = _query(http, site_url, *month_bounds(period))
    if not rows:
        return 0, 0
    return int(rows[0]["clicks"]), int(rows[0]["impressions"])


def _row(raw: dict) -> SearchRow:
    return SearchRow(
        key=raw["keys"][0],
        clicks=int(raw["clicks"]),
        impressions=int(raw["impressions"]),
        ctr=float(raw["ctr"]),
        position=float(raw["position"]),
    )


def fetch_month(http: Http, site_url: str, period: str) -> GSCMonth:
    clicks, impressions = _month_totals(http, site_url, period)
    prev_clicks, prev_impressions = _month_totals(http, site_url, shift(period, -1))

    months = last_n(period, 13)
    daily = _query(http, site_url, month_bounds(months[0])[0], month_bounds(period)[1], ["date"], 25000)
    per_month: dict[str, int] = defaultdict(int)
    for raw in daily:
        per_month[raw["keys"][0][:7]] += int(raw["clicks"])

    start, end = month_bounds(period)
    return GSCMonth(
        period=period,
        clicks=clicks,
        prev_clicks=prev_clicks,
        impressions=impressions,
        prev_impressions=prev_impressions,
        trend=[(m, per_month[m]) for m in months],
        top_queries=[_row(r) for r in _query(http, site_url, start, end, ["query"], 10)],
        top_pages=[_row(r) for r in _query(http, site_url, start, end, ["page"], 10)],
    )
